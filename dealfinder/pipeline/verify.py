"""Verifica che gli annunci esistano ancora PRIMA di analizzarli e PRIMA di mostrarli.

Un'opportunità è visibile ai commercianti solo se verificata nelle ultime 24 ore.
Subito: apertura della pagina. Facebook: controllo per URL con Bright Data (a lotti).
"""
from __future__ import annotations

import logging
from collections import Counter

from ..collectors.brightdata import BrightDataFacebookCollector
from ..collectors.subito import SubitoCollector
from ..config import settings

log = logging.getLogger("verify")
VISIBLE_HOURS = 24


def mark(conn, lid: int, status: str, price=None) -> None:
    if status == "attivo":
        conn.execute("UPDATE listings SET last_checked_at=now(), last_seen_at=now() WHERE id=%s", (lid,))
    elif status in ("scomparso", "venduto"):
        conn.execute("UPDATE listings SET status='scomparso', disappeared_at=now(), last_checked_at=now(), "
                     "stage_reason=COALESCE(stage_reason,'') || ' | ' || %s WHERE id=%s", (status, lid))
        conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'scomparso',%s)", (lid, price))


def verify_rows(conn, rows: list[dict]) -> Counter:
    """Verifica un gruppo di annunci; ritorna i conteggi per esito."""
    stats: Counter = Counter()
    fb = [r for r in rows if r["source"] == "facebook"]
    sb = [r for r in rows if r["source"] == "subito"]
    if fb:
        bd = BrightDataFacebookCollector()
        if bd.configured():
            try:
                res = bd.check_urls([r["url"] for r in fb])
                for r in fb:
                    st = res.get(r["url"], "errore")
                    mark(conn, r["id"], st, r.get("price_eur"))
                    stats[f"facebook:{st}"] += 1
            except Exception as e:
                log.warning("verifica Facebook fallita: %s", e)
                stats["facebook:errore"] += len(fb)
    if sb:
        col = SubitoCollector(proxy=settings.scraper_proxy)
        for r in sb:
            try:
                cur = col.fetch(r["url"])
                st = "attivo" if cur is not None else "scomparso"
            except Exception as e:
                log.warning("verifica Subito fallita %s: %s", r["url"], e)
                st = "errore"
            mark(conn, r["id"], st, r.get("price_eur"))
            stats[f"subito:{st}"] += 1
    conn.commit()
    return stats
