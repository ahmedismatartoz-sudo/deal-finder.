"""Importa l'analisi fatta a parte (file data/analisi_manuale.json nel repository).

Formato: lista di oggetti
  {"id": 123, "esito": "opportunita|da_verificare|incidentata|scartata", "danno": "nessuno|leggero|medio|alto_rischio|grave",
   "danni": [{"part": "paraurti_anteriore", "side": null, "action": "sostituire", "severity": "medio"}],
   "ricambi_min": 250, "ricambi_max": 420, "ricambi": [{"label": "...", "low": 120, "high": 180, "offer": "url"}],
   "nota": "testo per la scheda", "controlli": ["..."], "rivendita_prudente": 3000 (facoltativo, solo al ribasso)}

L'esito viene ricalcolato con i numeri (confronti e margine): l'analisi manuale fornisce stato
e ricambi, non il margine.
"""
from __future__ import annotations

import json
import os
import logging
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from ..ai.damage import sanitize_items
from ..core.models import DamageItem
from ..db import connect, log_job
from ..pricing import report
from ..web import scovo
from ..pricing.engine import value_listing
from ..pricing.fallback import apply_model, estimate_missing_km, weak
from ..pricing.train import load_active
from ..pricing.margin import DealerCosts, compute_margin
from ..store import load_market, row_to_listing, save_valuation, set_stage, update_listing_fields

log = logging.getLogger("manuale")
FILE = Path(__file__).resolve().parents[2] / "data" / "analisi_manuale.json"


def applica_scarti_cliente(conn) -> int:
    """Auto che il cliente ha chiesto di togliere dal sito (data/scarti_sito.json: marca, anno, km, prezzo).
    Si riconoscono da marca + anno + km + prezzo; non tornano più sul sito."""
    f = FILE.parent / "scarti_sito.json"
    if not f.exists():
        return 0
    n = 0
    for e in json.loads(f.read_text()):
        n += conn.execute("UPDATE listings SET stage='scartato', stage_reason='analisi_manuale: tolta dal cliente' "
                          "WHERE make ILIKE %s AND year=%s AND mileage_km=%s AND price_eur=%s "
                          "AND stage IS DISTINCT FROM 'scartato'",
                          (e["marca"] + "%", e["anno"], e["km"], e["prezzo"])).rowcount
    conn.commit()
    log.warning("SCARTI_CLIENTE %d auto tolte dal sito", n)
    return n


def applica_scarti(conn) -> int:
    """Toglie subito dal sito le auto scartate a mano (foto: incidenti gravi, airbag, ecc.).
    Leggero: solo un aggiornamento dello stato, si fa a ogni avvio del sito senza aspettare il ciclo."""
    try:
        applica_scarti_cliente(conn)
    except Exception as ex:
        conn.rollback()
        log.warning("SCARTI_CLIENTE non riuscito: %s", str(ex)[:200])
    if not FILE.exists():
        return 0
    ids = [e["id"] for e in json.loads(FILE.read_text())
           if e.get("esito") == "scartata" or (e.get("danno") == "grave" and e.get("esito") != "incidentata")]
    if not ids:
        return 0
    n = conn.execute("UPDATE listings SET stage='scartato', stage_reason='analisi_manuale: controllo foto' "
                     "WHERE id = ANY(%s) AND stage <> 'scartato'", (ids,)).rowcount
    conn.commit()
    log.info("MANUALE scarti applicati subito: %d auto tolte dal sito", n)
    return n


def segna_viste_di_recente(conn) -> dict:
    """Auto approvate e viste dalla raccolta nelle ultime 48 ore: valgono come verificate (sono ancora online).
    Serve a mostrarle subito, senza aspettare il controllo una per una del ciclo."""
    conn.execute("SET LOCAL lock_timeout = '10s'")
    n = conn.execute("""UPDATE listings SET last_checked_at=now()
                        WHERE status='attivo' AND stage='approfondito' AND stage_reason IN ('opportunita','da_verificare','incidentata')
                          AND last_seen_at > now() - make_interval(hours => %s)
                          AND (last_checked_at IS NULL OR last_checked_at < now() - interval '12 hours')""",
                     (int(os.environ.get("FRESCHEZZA_ORE", "48")),)).rowcount
    rows = conn.execute("""SELECT source, count(*) AS n FROM listings
                           WHERE status='attivo' AND stage='approfondito' AND stage_reason IN ('opportunita','da_verificare','incidentata')
                             AND last_checked_at > now() - interval '48 hours' GROUP BY source""").fetchall()
    conn.commit()
    out = {"aggiornate": n, **{r["source"]: r["n"] for r in rows}}
    log.info("MANUALE auto approvate visibili: %s", out)
    return out


def run(esiti: tuple[str, ...] | None = None, solo_nuove: bool = False, voci: list | None = None) -> dict:
    """esiti: importa solo le voci con questi esiti (es. ("incidentata",) all'avvio del sito, veloce).
    voci: analisi da importare al posto del file (annunci aggiunti a mano)."""
    if voci is None and not FILE.exists():
        return {"file": "assente"}
    entries = voci if voci is not None else json.loads(FILE.read_text())
    if esiti:
        entries = [e for e in entries if e.get("esito") in esiti]
    stats: Counter = Counter()
    with connect() as conn:
        finish = log_job(conn, "manuale")
        model = load_active(conn)
        # una revisione nuova (es. "audit": "a1") si importa anche all'avvio: ha un'etichetta sua
        done = {(r["listing_id"], r["engine_version"].split("+manuale-v5", 1)[1]) for r in conn.execute(
            "SELECT DISTINCT listing_id, engine_version FROM valuations WHERE engine_version LIKE '%%+manuale-v5%%'"
        ).fetchall()}
        # scarti già applicati: si saltano senza toccare il database (prima si riscrivevano tutti a ogni avvio)
        da_scartare = [e["id"] for e in entries
                       if e.get("esito") == "scartata" or (e.get("danno") == "grave" and e.get("esito") != "incidentata")]
        gia_scartati = {r["id"] for r in conn.execute(
            "SELECT id FROM listings WHERE id = ANY(%s) AND stage='scartato'", (da_scartare,)).fetchall()} if da_scartare else set()
        for e in entries:
            lid = e["id"]
            tag = ("-" + e["audit"]) if e.get("audit") else ""
            if (lid, tag) in done and (solo_nuove or not e.get("aggiorna")):
                stats["gia_importati"] += 1
                continue
            if lid in gia_scartati and (e.get("esito") == "scartata" or (e.get("danno") == "grave" and e.get("esito") != "incidentata")):
                stats["gia_scartate"] += 1
                continue
            row = conn.execute("SELECT * FROM listings WHERE id=%s", (lid,)).fetchone()
            if not row:
                stats["non_trovato"] += 1
                continue
            l = row_to_listing(row)
            if e.get("esito") == "scartata" or (e.get("danno") == "grave" and e.get("esito") != "incidentata"):
                set_stage(conn, lid, "scartato", "analisi_manuale: " + str(e.get("nota", ""))[:150])
                stats["scartate"] += 1
                conn.commit()
                continue
            items = sanitize_items(e.get("danni") or [])
            l.damage_class = e.get("danno") or ("nessuno" if not items else "medio")
            l.damage_items = [DamageItem(**{k: v for k, v in it.items() if k in DamageItem.__dataclass_fields__})
                              for it in items]
            km_est = estimate_missing_km(l)
            v = value_listing(l, load_market(conn, l.make, l.model, l.fuel))
            if weak(v):
                apply_model(v, l, model)
            v.engine_version += "+manuale-v5" + tag
            # rivendita corretta a mano (es. allestimento povero, limitazioni di circolazione)
            if e.get("rivendita_prudente") and not v.resale_prudent:
                # nessun confronto né modello (es. Facebook senza modello): vale la rivendita stimata a mano
                rp = int(e["rivendita_prudente"])
                v.resale_prudent = rp
                for f in ("private_median", "resale_median_private", "resale_median"):
                    if hasattr(v, f) and not getattr(v, f, None):
                        setattr(v, f, round(rp / (1 - scovo.SCONTO_DEFAULT / 100)))
            if e.get("rivendita_prudente") and v.resale_prudent:
                v.resale_prudent = min(v.resale_prudent, int(e["rivendita_prudente"]))
            if e.get("mercato_da_rivendita") and e.get("rivendita_prudente"):
                # anche il mercato mostrato sul sito scende: rivendita prudente = mercato −10%
                cap = round(int(e["rivendita_prudente"]) / (1 - scovo.SCONTO_DEFAULT / 100))
                for f in ("private_median", "resale_median_private", "resale_median"):
                    if getattr(v, f, None) and getattr(v, f) > cap:
                        setattr(v, f, cap)
            parts = None
            if e.get("ricambi_max") is not None:
                lines = [{"part": p.get("label"), "label": p.get("label"), "low": p.get("low"), "high": p.get("high"),
                          "probable_hidden": False, "offers": [{"url": p["offer"], "price_eur": p.get("high"),
                          "type": "aftermarket", "seller": ""}] if p.get("offer") else []}
                         for p in e.get("ricambi") or []]
                parts = {"parts_cost_low": e.get("ricambi_min"), "parts_cost_high": e["ricambi_max"],
                         "lines": lines, "complete": True, "vehicle": f"{l.make} {l.model} {l.year}",
                         "by_type": {"aftermarket": {"high": e["ricambi_max"], "complete": True}}}
            m = compute_margin(l, v, DealerCosts(), parts["parts_cost_high"] if parts else None)
            # regola del sito (mercato −10%, ricambi al massimo, soglia per fascia di prezzo): se la supera,
            # l'auto si propone anche quando il calcolo prudente del motore la scarterebbe
            if m and m.status == "scartata" and e.get("esito") in ("opportunita", "da_verificare"):
                mercato = v.private_median or v.resale_median_private or v.resale_median
                if mercato and l.price_eur:
                    sito = (round(mercato * (1 - scovo.SCONTO_DEFAULT / 100)) - l.price_eur - sum(scovo.COSTI_FISSI.values())
                            - int((parts or {}).get("parts_cost_high") or 0))
                    if sito >= scovo.soglia(l.price_eur):
                        m.status = "da_verificare"
            if m and e.get("audit") == "a2" and m.status == "scartata":
                # riammessa dopo il controllo: resta sul sito con il margine abbassato (almeno il minimo mostrato)
                mercato = v.private_median or v.resale_median_private or v.resale_median
                if mercato and l.price_eur:
                    sito = (round(mercato * (1 - scovo.SCONTO_DEFAULT / 100)) - l.price_eur - sum(scovo.COSTI_FISSI.values())
                            - int((parts or {}).get("parts_cost_high") or 0))
                    if sito >= scovo.MIN_MOSTRATO:
                        m.status = "da_verificare"
            if m and e.get("esito") == "opportunita" and m.net_margin >= m.threshold:
                m.status = "opportunita"           # confermata dall'analisi (stima e testo controllati)
            if m and e.get("esito") == "da_verificare" and m.status == "opportunita":
                m.status = "da_verificare"         # l'analisi chiede controlli prima di proporla
            if e.get("esito") == "incidentata" and m:
                # sezione Incidentate (per commercianti e carrozzieri): conta solo la regola del sito con i pezzi
                mercato = v.private_median or v.resale_median_private or v.resale_median
                sito = None
                if mercato and l.price_eur:
                    riv = round(mercato * (1 - scovo.SCONTO_DEFAULT / 100))
                    if e.get("rivendita_prudente"):
                        riv = min(riv, int(e["rivendita_prudente"]))
                    sito = riv - l.price_eur - sum(scovo.COSTI_FISSI.values()) - int((parts or {}).get("parts_cost_high") or 0)
                m.status = "incidentata" if sito is not None and sito >= scovo.soglia(l.price_eur) else "scartata"
            mot = report.motivation(l, v, parts)
            if e.get("nota"):
                mot.append("Analisi: " + e["nota"])
            chk = report.checks(l, v, parts) + list(e.get("controlli") or [])
            if km_est:
                l.mileage_km = None                 # la stima serve solo al calcolo, non si salva
                chk.append("Km non dichiarati: stimati in modo prudente, da chiedere al venditore")
            update_listing_fields(conn, lid, l)
            save_valuation(conn, lid, v, parts, mot, chk, asdict(m) if m else None)
            set_stage(conn, lid, "approfondito", m.status if m else "non_valutabile")
            conn.execute("UPDATE listings SET deep_at=now() WHERE id=%s", (lid,))
            stats[m.status if m else "non_valutabile"] += 1
            conn.commit()
        finish(True, dict(stats))
    log.info("MANUALE %s", dict(stats))
    return dict(stats)
