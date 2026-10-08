"""Esportazione delle opportunità nei log (una riga JSON per auto), per rapporti e presentazioni.

    python -m dealfinder.pipeline.esporta

Costi standard (vendita a privati, nessuna IVA). Solo annunci verificati nelle ultime 24 ore.
"""
from __future__ import annotations

import json
import logging
from collections import Counter

from ..db import connect
from ..web import cards
from ..web.app import LIST_SQL

log = logging.getLogger("esporta")


def run() -> dict:
    with connect() as conn:
        rows = conn.execute(LIST_SQL + " LIMIT 5000", {"me": 0}).fetchall()
    comp: Counter = Counter()
    n = 0
    for r in rows:
        val = dict(r)
        val["created_at"] = r.get("valued_at")
        c = cards.build(r, val, list(r.get("photo_urls") or []), None, full=True)
        if not c or c["status"] == "scartata":
            continue
        n += 1
        tipo = "sana" if c["damage_class"] == "nessuno" else ("da_verificare_stato" if c["damage_class"] == "sconosciuto" else "con_problemi")
        comp[f"{c['status']}:{tipo}"] += 1
        rec = {k: c.get(k) for k in ("id", "status", "source", "title", "make", "model", "version", "year", "km", "fuel",
                                     "gearbox", "city", "province", "price", "resale_prudent", "parts_cost", "net_margin",
                                     "threshold", "damage_class", "risk", "liquidity_days")}
        rec.update(url=r["url"], tipo=tipo, photos=(c.get("photos") or [])[:4], damage=c.get("damage"),
                   motivation=c.get("motivation"), checks=c.get("checks"), costs=c.get("costs"),
                   private_median=c["valuation"]["private_median"], asis_median=c["valuation"].get("asis_median"),
                   n_comparables=c["valuation"]["n_comparables"], reasons=c["valuation"]["confidence_reasons"],
                   parts=[{"label": l.get("label"), "low": l.get("low"), "high": l.get("high"),
                           "offer": (l.get("offers") or [{}])[0].get("url")} for l in ((c.get("parts") or {}).get("lines") or [])])
        log.info("EXPORT %s", json.dumps(rec, ensure_ascii=False, default=str))
    out = {"totale": n, "composizione": dict(comp)}
    log.info("EXPORT_RIEPILOGO %s", json.dumps(out))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
