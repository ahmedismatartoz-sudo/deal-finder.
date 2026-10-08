"""Raccolta: esegue i collettori, pulisce, salva, registra lo storico.

Uso:
    python -m dealfinder.pipeline.collect opportunita   # zona Milano, ≤ 20.000 €
    python -m dealfinder.pipeline.collect mercato       # base di mercato, zona ampia
"""
from __future__ import annotations

import logging
import sys
from collections import Counter

from ..collectors.meta import MetaProviderCollector
from ..collectors.subito import SubitoCollector
from ..config import settings
from ..core.normalize import normalize_fields
from ..db import connect, mark_disappeared, upsert_listing

log = logging.getLogger("collect")


def run(mode: str) -> Counter:
    stats: Counter = Counter()
    if mode == "opportunita":
        provinces, max_price = settings.opportunity_provinces, settings.max_purchase_eur
    else:
        provinces, max_price = settings.market_provinces, settings.market_max_price_eur

    subito = SubitoCollector(proxy=settings.scraper_proxy)
    meta = MetaProviderCollector()
    sources = [(subito, {"region": settings.region, "provinces": provinces,
                         "max_price": max_price, "max_pages": settings.max_pages_per_province})]
    if meta.configured() and mode == "opportunita":
        sources.append((meta, {"provider_input": {}}))   # input definito col fornitore scelto

    with connect() as conn:
        for collector, query in sources:
            try:
                for listing in collector.search(query):
                    normalize_fields(listing)
                    _, event = upsert_listing(conn, listing)
                    stats[f"{collector.source}:{event}"] += 1
                    conn.commit()
            except Exception:
                log.exception("collettore %s fallito", collector.source)
                stats[f"{collector.source}:errore"] += 1
        if mode == "mercato":
            stats["subito:scomparsi"] = mark_disappeared(conn, "subito", not_seen_hours=36)
        conn.commit()
    log.info("raccolta %s: %s", mode, dict(stats))
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run(sys.argv[1] if len(sys.argv) > 1 else "opportunita")
