"""Raccolta: esegue i collettori, pulisce, salva, registra lo storico.

Uso:
    python -m dealfinder.pipeline.collect opportunita   # zona Milano, ≤ 20.000 €
    python -m dealfinder.pipeline.collect mercato       # base di mercato, zona ampia
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter

from ..collectors.meta import MetaProviderCollector
from ..collectors.subito import SubitoCollector
from ..config import settings
from ..core.normalize import normalize_fields
from ..db import connect, log_job, mark_disappeared, upsert_listing

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
        sources.append((meta, {"provider_input": meta_input()}))

    with connect() as conn:
        finish = log_job(conn, f"collect:{mode}")
        for collector, query in sources:
            try:
                for listing in collector.search(query):
                    normalize_fields(listing)
                    lid, event = upsert_listing(conn, listing)
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
        finish(not any(k.endswith(":errore") for k in stats), dict(stats))
    log.info("raccolta %s: %s", mode, dict(stats))
    return stats


def meta_input() -> dict:
    """Parametri per il fornitore Meta: JSON in META_PROVIDER_INPUT (dipende dallo scraper scelto)."""
    raw = os.environ.get("META_PROVIDER_INPUT")
    return json.loads(raw) if raw else {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mode = sys.argv[1] if len(sys.argv) > 1 else "opportunita"
    run(mode)
    if mode == "opportunita" and "--senza-analisi" not in sys.argv:
        from .process import run as process_run
        process_run("tutto")
