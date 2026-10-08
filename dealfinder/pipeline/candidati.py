"""Valutazione SENZA AI di tutti gli annunci di privati, ed esportazione dei candidati nei log.

    python -m dealfinder.pipeline.candidati

Serve quando l'analisi AI automatica non è attiva: i candidati vengono esaminati a parte
(analisi manuale/assistita) e i risultati reimportati con pipeline.manuale.

Per ogni annuncio di privato in zona: confronti con auto simili, rivendita prudente a privati,
margine con i costi standard PRIMA dei ricambi. Esce se il margine supera la soglia (le auto con
problemi dichiarati devono avere 800 € in più di spazio per i ricambi). Prima di esportare si
verifica che l'annuncio esista ancora.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter

from ..config import settings
from ..core.normalize import problem_hint
from ..db import connect, log_job
from ..pricing.engine import value_listing
from ..pricing.margin import DealerCosts, compute_margin
from ..store import load_market, photos_of, row_to_listing
from .verify import verify_rows

log = logging.getLogger("candidati")
PROVINCES = ["MI", "MB", "BG", "BS"]
MAX_EXPORT = int(os.environ.get("MAX_CANDIDATI", "400"))
DAMAGE_ROOM = 800


def run() -> dict:
    stats: Counter = Counter()
    out = []
    with connect() as conn:
        finish = log_job(conn, "candidati")
        rows = conn.execute(
            """SELECT * FROM listings WHERE status='attivo' AND price_eur BETWEEN 500 AND %s
                 AND seller_type <> 'commerciante'
                 AND (province = ANY(%s) OR source='facebook')
                 AND COALESCE(prescreen,'') <> 'non_interessante'
                 AND stage NOT IN ('scartato','approfondito')""",
            (settings.max_purchase_eur, PROVINCES)).fetchall()
        stats["esaminati"] = len(rows)
        cache: dict = {}
        costs = DealerCosts()
        for r in rows:
            l = row_to_listing(r)
            if not (l.make and l.model and l.year and l.mileage_km is not None):
                stats["dati_mancanti"] += 1
                continue
            key = (l.make, l.model, l.fuel)
            if key not in cache:
                cache[key] = load_market(conn, *key)
            hints = problem_hint(l)
            damaged = bool(hints or l.damage_declared or r.get("problem_search"))
            l.damage_class = "nessuno"                 # rivendita calcolata da auto sistemata
            v = value_listing(l, cache[key])
            if v.resale_prudent is None:
                stats["nessun_confronto"] += 1
                continue
            m = compute_margin(l, v, costs)
            need = m.threshold + (DAMAGE_ROOM if damaged else 0)
            if m.net_margin < need:
                stats["margine_insufficiente"] += 1
                continue
            if "prezzo_troppo_basso" in v.fraud_flags and not damaged:
                stats["sospetto"] += 1
            out.append((m.score, r, l, v, m, hints, damaged))
        out.sort(key=lambda x: -x[0])
        out = out[:MAX_EXPORT]
        stats.update(verify_rows(conn, [x[1] for x in out]))
        alive = {x["id"] for x in conn.execute(
            "SELECT id FROM listings WHERE id = ANY(%s) AND status='attivo'", ([x[1]["id"] for x in out],)).fetchall()}
        n = 0
        for score, r, l, v, m, hints, damaged in out:
            if r["id"] not in alive:
                continue
            n += 1
            rec = {"id": r["id"], "fonte": r["source"], "url": r["url"], "titolo": l.title,
                   "descrizione": (l.description or "")[:900], "marca": l.make, "modello": l.model,
                   "versione": l.version_raw, "anno": l.year, "km": l.mileage_km, "carburante": l.fuel,
                   "cambio": l.gearbox, "kw": l.power_kw, "citta": l.city, "provincia": l.province,
                   "prezzo": l.price_eur, "mediana_privati": v.private_median, "rivendita_prudente": v.resale_prudent,
                   "confronti": v.n_comparables, "livello_confronti": v.comparable_level, "dispersione": v.dispersion,
                   "margine_prima_ricambi": m.net_margin, "soglia": m.threshold, "con_problemi": damaged,
                   "parole_problema": hints, "mediana_da_sistemare": v.asis_median, "segnali": v.fraud_flags,
                   "giorni_vendita_simili": v.liquidity_days, "foto": photos_of(conn, r["id"])[:3]}
            log.info("CANDIDATO %s", json.dumps(rec, ensure_ascii=False, default=str))
        stats["esportati"] = n
        finish(True, dict(stats))
    log.info("CANDIDATI_RIEPILOGO %s", json.dumps(dict(stats)))
    return dict(stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
