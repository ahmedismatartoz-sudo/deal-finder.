"""Rilegge gli annunci Facebook già salvati con il lettore migliorato (marca, modello, anno,
km, carburante e cambio presi anche dalla descrizione) e li rimette in valutazione.

Gira una volta dal ciclo automatico (job "fb_rilettura:v1"); a mano:
    python -m dealfinder.pipeline.fb_rilettura
Non scarica nulla da Bright Data: usa il testo originale già salvato (colonna raw).
"""
from __future__ import annotations

import json
import logging
from collections import Counter

from ..collectors.brightdata import parse_row
from ..core.normalize import normalize_fields
from ..db import connect, log_job
from ..store import update_listing_fields

log = logging.getLogger("fb_rilettura")
JOB = "fb_rilettura:v1"


def run() -> dict:
    stats: Counter = Counter()
    with connect() as conn:
        finish = log_job(conn, JOB)
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM listings WHERE source='facebook' AND raw IS NOT NULL ORDER BY id").fetchall()]
        stats["annunci"] = len(ids)
        for lid in ids:
            row = conn.execute("SELECT raw, stage, stage_reason FROM listings WHERE id=%s", (lid,)).fetchone()
            raw = row["raw"]
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    stats["raw_illeggibile"] += 1
                    continue
            l = parse_row(raw or {})
            if l is None:
                stats["non_auto_o_venduta"] += 1
                continue
            normalize_fields(l)
            try:
                update_listing_fields(conn, lid, l)
                manual = str(row["stage_reason"] or "").startswith("analisi_manuale")
                if row["stage"] != "approfondito" and not manual:
                    conn.execute("UPDATE listings SET stage='nuovo', stage_reason='fb_rilettura' WHERE id=%s", (lid,))
                conn.commit()
            except Exception as e:
                conn.rollback()
                stats["errore"] += 1
                log.warning("annuncio %s non aggiornato: %s", lid, str(e)[:120])
                continue
            stats["aggiornati"] += 1
            for f in ("make", "model", "year", "mileage_km", "fuel"):
                if getattr(l, f):
                    stats[f"con_{f}"] += 1
            if l.make and l.model and l.year and l.fuel:
                stats["completi_senza_km"] += 1
        finish(True, dict(stats))
    log.info("FB_RILETTURA %s", dict(stats))
    return dict(stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(run())
