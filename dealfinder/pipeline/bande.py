"""Rapporto di prova: le 3 migliori opportunità VERIFICATE per fascia di prezzo richiesto.

    python -m dealfinder.pipeline.bande

Non forza nulla: se in una fascia non ci sono 3 auto che superano le soglie con stime
prudenti, mostra quelle che ci sono e lo dice. Costi standard (privato → privato, senza IVA).
"""
from __future__ import annotations

import json
import logging

from ..db import connect
from ..web import cards
from ..web.app import LIST_SQL

log = logging.getLogger("bande")
BANDS = [(1000, 2000), (2000, 5000), (5000, 8000), (8000, 11000), (11000, 14000), (14000, 17000), (17000, 20000)]
PER_BAND = {(1000, 2000): 10}


def run(per_band: int = 3) -> dict:
    out = {}
    with connect() as conn:
        rows = conn.execute(LIST_SQL + " LIMIT 5000", {"me": 0}).fetchall()
    built = []
    for r in rows:
        val = dict(r)
        val["created_at"] = r.get("valued_at")
        c = cards.build(r, val, list(r.get("photo_urls") or []), None)
        if c and c["status"] == "opportunita":
            built.append(c)
    for lo, hi in BANDS:
        sel = sorted([c for c in built if lo <= c["price"] < hi], key=lambda c: -c["score"])
        key = f"{lo}-{hi}"
        out[key] = {"trovate": len(sel), "migliori": [
            {k: c[k] for k in ("id", "title", "source", "price", "resale_prudent", "parts_cost", "net_margin",
                               "risk", "damage_class", "city", "km", "year")} for c in sel[:PER_BAND.get((lo, hi), per_band)]]}
        log.info("FASCIA %s: %s", key, json.dumps(out[key], ensure_ascii=False, default=str))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
