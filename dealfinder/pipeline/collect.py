"""Raccolta: esegue i collettori, pulisce, salva, registra lo storico.

Uso:
    python -m dealfinder.pipeline.collect opportunita   # zona Milano, ≤ 20.000 €
    python -m dealfinder.pipeline.collect mercato       # base di mercato, zona ampia
    python -m dealfinder.pipeline.collect facebook      # Facebook Marketplace via Bright Data
"""
from __future__ import annotations

import logging
import os
import re
import sys
from collections import Counter

from ..collectors.brightdata import BrightDataFacebookCollector
from ..collectors.subito import SubitoCollector
from ..config import settings
from ..core.normalize import normalize_fields
from ..db import connect, log_job, mark_disappeared, upsert_listing

log = logging.getLogger("collect")


def _recent_facebook() -> bool:
    hours = max(1, int(os.environ.get("FACEBOOK_ORE", "8")) - 1)
    with connect() as conn:
        row = conn.execute("SELECT max(started_at) > now() - make_interval(hours => %s) AS recent FROM job_runs "
                           "WHERE (job='collect:facebook' OR job LIKE 'collect:fb_backfill%%') AND ok", (hours,)).fetchone()
    return bool(row and row["recent"])


def _backfill_done() -> bool:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM job_runs WHERE job='collect:fb_backfill:v2' AND ((ok AND started_at > now() - "
                           "interval '30 days') OR (finished_at IS NULL AND started_at > now() - interval '6 hours')) LIMIT 1").fetchone()
    return bool(row)


def _backfill_running() -> bool:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM job_runs WHERE job LIKE 'collect:fb_backfill%%' AND finished_at IS NULL "
                           "AND started_at > now() - interval '6 hours' LIMIT 1").fetchone()
    return bool(row)


class _ReadyImporter(BrightDataFacebookCollector):
    """Importa i lotti Facebook già pronti su Bright Data (avviati da un altro giro): nessuna nuova spesa."""

    def search(self, query: dict):
        if not self.configured():
            return
        sids = self.ready_snapshots(hours=int(os.environ.get("FB_IMPORTA_ORE", "48")))
        with connect() as conn:
            done = {r["sid"] for r in conn.execute("SELECT sid FROM fb_snapshots WHERE sid = ANY(%s)",
                                                   (sids,)).fetchall()} if sids else set()
        sids = [s for s in sids if s not in done]
        log.info("Facebook: %d lotti pronti da importare (%d già importati)", len(sids), len(done))
        yield from self.collect_snapshots(sids, query.get("max_price", 20_000), max_minutes=5)


ZONA = {"MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV", "CR", "NO"}


def _norm_city(c: str | None) -> str:
    import unicodedata
    c = unicodedata.normalize("NFKD", str(c or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", c.split(",")[0])


def collector_is_fb(sources) -> bool:
    return any(getattr(c, "source", "") == "facebook" for c, _ in sources)


def city_provinces(conn) -> dict:
    """Comune -> provincia, imparato dagli annunci di Subito (che hanno sempre la provincia)."""
    rows = conn.execute("SELECT city, province, count(*) AS n FROM listings WHERE source='subito' "
                        "AND city IS NOT NULL AND province IS NOT NULL GROUP BY 1,2").fetchall()
    best: dict = {}
    for r in rows:
        k = _norm_city(r["city"])
        if k and r["n"] > best.get(k, ("", 0))[1]:
            best[k] = (r["province"], r["n"])
    out = {k: v[0] for k, v in best.items()}
    out.update({"milan": "MI", "milano": "MI", "monza": "MB", "bergamo": "BG", "brescia": "BS", "como": "CO",
                "varese": "VA", "pavia": "PV", "lecco": "LC", "lodi": "LO", "cremona": "CR", "novara": "NO"})
    return out


def run(mode: str) -> Counter:
    stats: Counter = Counter()
    if mode == "opportunita":
        provinces, max_price = settings.opportunity_provinces, settings.max_purchase_eur
    else:
        provinces, max_price = settings.market_provinces, settings.market_max_price_eur
        settings.max_pages_per_province = settings.market_max_pages

    # Subito ogni 3 ore (opportunità) e ogni notte (mercato). Facebook ha un lavoro a parte,
    # meno frequente, perché ogni annuncio scaricato da Bright Data ha un costo.
    if mode == "profondo":
        bands = [(p, p + 500) for p in range(500, 5000, 500)] + [(p, p + 1000) for p in range(5000, 20000, 1000)]
        sources = [(SubitoCollector(proxy=settings.scraper_proxy),
                    {"region": settings.region, "provinces": list(settings.opportunity_provinces),
                     "max_price": settings.max_purchase_eur, "max_pages": 0, "price_bands": bands,
                     "band_pages": int(os.environ.get("BAND_PAGES", "30"))})]
    elif mode == "fb_importa":
        sources = [(_ReadyImporter(), {"max_price": settings.max_purchase_eur})]
    elif mode in ("facebook", "fb_backfill", "fb_importa"):
        if mode == "facebook" and _backfill_running():
            # una raccolta grande è ancora in corso in un altro giro: si importano subito i lotti già pronti
            mode = "fb_importa"
            log.info("Facebook: raccolta di partenza in corso altrove, importo i lotti già pronti")
        elif mode == "facebook" and os.environ.get("FB_BACKFILL") == "1" and not _backfill_done():
            mode = "fb_backfill"          # "Trigger Run" sul lavoro Facebook avvia subito la raccolta di partenza
            log.info("Facebook: raccolta di partenza (ultimi 30 giorni)")
        if mode == "facebook" and _recent_facebook():
            log.info("Facebook raccolto da poco (altro lavoro): salto per non pagare due volte gli stessi annunci")
            return stats
        if mode == "fb_importa":
            sources = [(_ReadyImporter(), {"max_price": settings.max_purchase_eur})]
        else:
            # dopo una pausa (più di 3 giorni senza annunci Facebook) si recupera l'ultima settimana, poi solo il giorno
            from .ciclo import facebook_due
            if mode == "facebook" and "FB_GIORNI" not in os.environ and facebook_due(72):
                os.environ["FB_GIORNI"] = "7"
                log.info("Facebook: ripresa dopo una pausa, recupero gli annunci dell'ultima settimana")
            sources = [(BrightDataFacebookCollector(backfill=(mode == "fb_backfill")),
                        {"max_price": settings.max_purchase_eur})]
    else:
        subito = SubitoCollector(proxy=settings.scraper_proxy)
        q = {"region": settings.region, "provinces": provinces,
             "max_price": max_price, "max_pages": settings.max_pages_per_province}
        if mode == "opportunita":
            q.update(problem_keywords=list(settings.problem_keywords), problem_pages=settings.problem_pages,
                     cheap_pages=int(os.environ.get("CHEAP_PAGES", "5")))
        sources = [(subito, q)]

    with connect() as conn:
        finish = log_job(conn, "collect:fb_backfill:v2" if mode == "fb_backfill" else f"collect:{mode}")
        from ..pricing.model import prescreen
        from ..pricing.train import load_active
        model = load_active(conn)          # caricato UNA volta: nessuna ricerca nel catalogo per annuncio
        if model is None:
            log.info("nessun modello dei prezzi attivo: filtro rapido con i confronti")
        prov_of = city_provinces(conn) if collector_is_fb(sources) else {}
        if prov_of:
            # provincia anche per gli annunci Facebook già salvati
            n = 0
            for r in conn.execute("SELECT id, city FROM listings WHERE source='facebook' AND province IS NULL "
                                  "AND city IS NOT NULL").fetchall():
                pv = prov_of.get(_norm_city(r["city"]))
                if pv:
                    conn.execute("UPDATE listings SET province=%s WHERE id=%s", (pv, r["id"]))
                    n += 1
            conn.commit()
            stats["facebook:provincia_aggiunta"] += n
        for collector, query in sources:
            try:
                for listing in collector.search(query):
                    normalize_fields(listing)
                    if listing.source == "facebook":
                        # posizione giusta: provincia dal comune; fuori da Milano e dintorni non si salva
                        pv = prov_of.get(_norm_city(listing.city))
                        if pv and not listing.province:
                            listing.province = pv
                        if pv and pv not in ZONA:
                            stats["facebook:fuori_zona"] += 1
                            continue
                    try:
                        lid, event = upsert_listing(conn, listing)
                    except Exception as e:          # un annuncio con dati assurdi non ferma la raccolta
                        conn.rollback()
                        stats[f"{collector.source}:scartato_dati"] += 1
                        log.warning("annuncio %s non salvato: %s", listing.url, str(e)[:120])
                        continue
                    if model is not None and event in ("nuovo", "prezzo", "riapparso"):
                        ps = prescreen(model, listing)
                        conn.execute("UPDATE listings SET model_p50=%s, model_p25=%s, prescreen=%s, "
                                     "prescreen_potential=%s WHERE id=%s",
                                     (ps.get("model_p50"), ps.get("model_p25"), ps["esito"],
                                      ps.get("potenziale"), lid))
                        stats[f"prescreen:{ps['esito']}"] += 1
                        if ps["esito"] == "non_interessante":
                            conn.execute("UPDATE listings SET stage='mercato', stage_reason='modello_prezzi' "
                                         "WHERE id=%s AND stage='nuovo'", (lid,))
                    if event in ("prezzo", "riapparso"):
                        # prezzo cambiato: l'annuncio va rivalutato
                        conn.execute("UPDATE listings SET stage='nuovo' WHERE id=%s AND stage<>'nuovo'", (lid,))
                    stats[f"{collector.source}:{event}"] += 1
                    conn.commit()
            except Exception:
                log.exception("collettore %s fallito", collector.source)
                stats[f"{collector.source}:errore"] += 1
            for sid, n in getattr(collector, "done_sids", []):   # lotti Facebook importati: non si riscaricano
                conn.execute("INSERT INTO fb_snapshots (sid, rows) VALUES (%s,%s) ON CONFLICT (sid) DO NOTHING", (sid, n))
            conn.commit()
        # Si segnano gli scomparsi solo se la raccolta è andata a buon fine,
        # altrimenti un blocco del sito farebbe "sparire" tutto.
        seen = sum(v for k, v in stats.items() if k.startswith("subito:") and k != "subito:errore")
        if mode == "mercato" and not stats["subito:errore"] and seen > 200:
            stats["subito:scomparsi"] = mark_disappeared(conn, "subito", not_seen_hours=36)
        conn.commit()
        # Riepilogo della qualità dei dati raccolti nell'ultima ora (visibile nei log)
        for q in conn.execute(
                """SELECT source, count(*) AS n,
                          round(100.0*avg((make IS NOT NULL)::int)) AS marca,
                          round(100.0*avg((model IS NOT NULL)::int)) AS modello,
                          round(100.0*avg((year IS NOT NULL)::int)) AS anno,
                          round(100.0*avg((mileage_km IS NOT NULL)::int)) AS km,
                          round(100.0*avg((price_eur IS NOT NULL)::int)) AS prezzo,
                          round(100.0*avg((province IS NOT NULL OR city IS NOT NULL)::int)) AS zona,
                          round(100.0*avg((seller_type='privato')::int)) AS privati,
                          round(100.0*avg((EXISTS (SELECT 1 FROM listing_photos p WHERE p.listing_id=listings.id))::int)) AS foto
                   FROM listings WHERE last_seen_at > now() - interval '1 hour' GROUP BY source""").fetchall():
            log.info("QUALITA %s: %s annunci | %% presenti: marca %s, modello %s, anno %s, km %s, prezzo %s, "
                     "zona %s, foto %s | privati %s%%", q["source"], q["n"], q["marca"], q["modello"], q["anno"],
                     q["km"], q["prezzo"], q["zona"], q["foto"], q["privati"])
        finish(not any(k.endswith(":errore") for k in stats), dict(stats))
    log.info("raccolta %s: %s", mode, dict(stats))
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mode = sys.argv[1] if len(sys.argv) > 1 else "opportunita"
    import os
    if not os.environ.get("DATABASE_URL"):
        log.info("DATABASE_URL non impostata: servizio non configurato, nulla da fare")
        sys.exit(0)
    if os.environ.get("SUBITO_PROBE") == "1":
        # Diagnostica di accesso: nessuna raccolta finché la variabile è attiva
        from ..collectors.probe import run as probe_run
        probe_run()
        sys.exit(0)
    if mode == "facebook":
        # una raccolta Facebook al giorno basta (ogni riga si paga, anche i doppioni): se il ciclo
        # o un altro lavoro l'ha già fatta nelle ultime ore, questo giro non riscarica
        from .ciclo import facebook_due
        if not facebook_due(int(os.environ.get("FACEBOOK_ORE", "23"))):
            log.info("Facebook già raccolto nelle ultime %s ore: niente da fare", os.environ.get("FACEBOOK_ORE", "23"))
            sys.exit(0)
    if os.environ.get("CICLO_COMPLETO") == "1" and mode == "opportunita":
        from .ciclo import run as ciclo_run
        ciclo_run()
        sys.exit(0)
    run(mode)
    if mode == "mercato":
        # dopo la raccolta notturna: riaddestra il modello se ha più di 7 giorni
        from ..pricing.train import run as train_run
        train_run()
    # L'analisi parte se non è esclusa dal comando, oppure se ANALISI_ATTIVA=1 (impostabile da Render)
    analisi = "--senza-analisi" not in sys.argv or os.environ.get("ANALISI_ATTIVA") == "1"
    if mode in ("opportunita", "facebook") and analisi:
        from .process import run as process_run
        process_run("tutto")
