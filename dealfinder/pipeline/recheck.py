"""Verifica quotidiana degli annunci pubblicati o candidati (Subito e Facebook).

    python -m dealfinder.pipeline.recheck

Un'opportunità non verificata nelle ultime 24 ore non viene mostrata ai commercianti.
"""
from __future__ import annotations

import logging

from ..db import connect, log_job
from .verify import verify_rows

log = logging.getLogger("recheck")


def run() -> dict:
    with connect() as conn:
        finish = log_job(conn, "recheck")
        rows = conn.execute(
            """SELECT id, source, url, price_eur FROM listings
               WHERE status='attivo' AND stage IN ('candidato','approfondito')
               ORDER BY last_checked_at NULLS FIRST LIMIT 500""").fetchall()
        stats = dict(verify_rows(conn, rows))
        finish(True, stats)
    log.info("recheck: %s", stats)
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
