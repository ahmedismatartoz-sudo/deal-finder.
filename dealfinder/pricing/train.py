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


def prescreen_backlog(conn, model: dict) -> dict:
    """Applica il modello agli annunci ancora da valutare (stage nuovo/attesa_mercato)."""
    from collections import Counter

    from .model import prescreen
    stats: Counter = Counter()
    rows = conn.execute("SELECT * FROM listings WHERE stage IN ('nuovo','attesa_mercato') AND status='attivo' "
                        "AND price_eur IS NOT NULL").fetchall()
    for r in rows:
        ps = prescreen(model, row_to_listing(r))
        stats[ps["esito"]] += 1
        conn.execute("UPDATE listings SET model_p50=%s, model_p25=%s, prescreen=%s, prescreen_potential=%s, "
                     "stage = CASE WHEN %s='non_interessante' THEN 'mercato' ELSE stage END WHERE id=%s",
                     (ps.get("model_p50"), ps.get("model_p25"), ps["esito"], ps.get("potenziale"),
                      ps["esito"], r["id"]))
    conn.commit()
    return dict(stats)


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
               AND (province IN ('MI','MB','BG','BS','CO','VA','LC','LO','PV','CR','MN','SO') OR (province IS NULL AND (region IS NULL OR lower(region) = 'lombardia')))
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
        if ok:
            res["prevalutati"] = prescreen_backlog(conn, model)
        finish(True, res)
    log.info("MODELLO %s", json.dumps(res, default=str))
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run("--forza" in sys.argv)
