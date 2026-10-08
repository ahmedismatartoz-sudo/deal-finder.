"""Verifica che le opportunità siano ancora disponibili.

    python -m dealfinder.pipeline.recheck

Subito: apre l'annuncio. Facebook: un annuncio non più restituito dal
fornitore per 48 ore viene considerato scomparso.
"""
from __future__ import annotations

import logging
from collections import Counter

from ..collectors.subito import SubitoCollector
from ..config import settings
from ..db import connect, log_job, mark_disappeared

log = logging.getLogger("recheck")


def run() -> dict:
    stats: Counter = Counter()
    subito = SubitoCollector(proxy=settings.scraper_proxy)
    with connect() as conn:
        finish = log_job(conn, "recheck")
        rows = conn.execute(
            """SELECT id, url, price_eur FROM listings
               WHERE source='subito' AND status='attivo' AND stage IN ('candidato','approfondito')
               ORDER BY last_checked_at NULLS FIRST LIMIT 500""").fetchall()
        for r in rows:
            try:
                current = subito.fetch(r["url"])
            except Exception as e:
                log.warning("verifica fallita %s: %s", r["url"], e)
                stats["errore"] += 1
                continue
            if current is None:
                conn.execute("UPDATE listings SET status='scomparso', disappeared_at=now(), "
                             "last_checked_at=now() WHERE id=%s", (r["id"],))
                conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'scomparso',%s)",
                             (r["id"], r["price_eur"]))
                stats["scomparso"] += 1
            else:
                conn.execute("UPDATE listings SET last_checked_at=now(), last_seen_at=now() WHERE id=%s", (r["id"],))
                if current.price_eur and current.price_eur != r["price_eur"]:
                    conn.execute("UPDATE listings SET price_eur=%s, stage='candidato' WHERE id=%s",
                                 (current.price_eur, r["id"]))
                    conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'prezzo',%s)",
                                 (r["id"], current.price_eur))
                    stats["prezzo_cambiato"] += 1   # torna candidato: si rivaluta
                else:
                    stats["disponibile"] += 1
            conn.commit()
        stats["facebook_scomparsi"] = mark_disappeared(conn, "facebook", not_seen_hours=48)
        conn.commit()
        finish(True, dict(stats))
    log.info("recheck: %s", dict(stats))
    return dict(stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
