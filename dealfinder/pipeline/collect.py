"""Raccolta: esegue i collettori, pulisce, salva, registra lo storico.

Uso:
    python -m dealfinder.pipeline.collect opportunita   # zona Milano, ≤ 20.000 €
    python -m dealfinder.pipeline.collect mercato       # base di mercato, zona ampia
    python -m dealfinder.pipeline.collect facebook      # Facebook Marketplace via Bright Data
"""
from __future__ import annotations

import logging
import os
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
                           "WHERE job IN ('collect:facebook','collect:fb_backfill') AND ok", (hours,)).fetchone()
    return bool(row and row["recent"])


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
    elif mode in ("facebook", "fb_backfill"):
        if mode == "facebook" and _recent_facebook():
            log.info("Facebook raccolto da poco (altro lavoro): salto per non pagare due volte gli stessi annunci")
            return stats
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
        finish = log_job(conn, f"collect:{mode}")
        from ..pricing.model import prescreen
        from ..pricing.train import load_active
        model = load_active(conn)          # caricato UNA volta: nessuna ricerca nel catalogo per annuncio
        if model is None:
            log.info("nessun modello dei prezzi attivo: filtro rapido con i confronti")
        for collector, query in sources:
            try:
                for listing in collector.search(query):
                    normalize_fields(listing)
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
