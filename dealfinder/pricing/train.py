"""Addestramento del modello dei prezzi (automatico, dopo la raccolta notturna del mercato).

    python -m dealfinder.pricing.train            # addestra se il modello attivo ha più di 7 giorni
    python -m dealfinder.pricing.train --forza    # addestra comunque

Il nuovo modello diventa attivo solo se l'errore misurato non peggiora più del 5%.
"""
from __future__ import annotations

import json
import logging
import sys

from ..db import connect, log_job
from ..store import row_to_listing
from .model import MODEL_VERSION, train

log = logging.getLogger("train")


def load_active(conn) -> dict | None:
    row = conn.execute("SELECT model FROM price_models WHERE active ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    m = row["model"]
    return json.loads(m) if isinstance(m, str) else m


def run(force: bool = False) -> dict:
    with connect() as conn:
        cur = conn.execute("SELECT created_at, metrics FROM price_models WHERE active "
                           "ORDER BY created_at DESC LIMIT 1").fetchone()
        if cur and not force:
            age = conn.execute("SELECT now() - %s > interval '7 days' AS old", (cur["created_at"],)).fetchone()
            if not age["old"]:
                log.info("modello attivo recente, nessun addestramento")
                return {"saltato": True}
        finish = log_job(conn, "train")
        rows = conn.execute(
            """SELECT * FROM listings WHERE price_eur IS NOT NULL AND make IS NOT NULL AND model IS NOT NULL
               AND (status='attivo' OR disappeared_at > now() - interval '180 days')""").fetchall()
        model = train([row_to_listing(r) for r in rows])
        m = model["metrics"]
        prev = (cur or {}).get("metrics") or {}
        if isinstance(prev, str):
            prev = json.loads(prev)
        ok = m.get("n_test", 0) >= 50 and (
            not prev.get("median_abs_pct_error")
            or m["median_abs_pct_error"] <= prev["median_abs_pct_error"] * 1.05)
        conn.execute("INSERT INTO price_models (version, model, metrics, active) VALUES (%s,%s,%s,%s)",
                     (MODEL_VERSION, json.dumps(model), json.dumps(m), ok))
        if ok:
            conn.execute("UPDATE price_models SET active = (id = (SELECT max(id) FROM price_models))")
        conn.commit()
        res = {"annunci": len(rows), "gruppi": len(model["groups"]), "metriche": m, "attivato": ok}
        finish(True, res)
    log.info("MODELLO %s", json.dumps(res, default=str))
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run("--forza" in sys.argv)
