"""Verifica che gli annunci esistano ancora PRIMA di analizzarli e PRIMA di mostrarli.

Un'opportunità è visibile ai commercianti solo se verificata nelle ultime 24 ore.
Subito: apertura della pagina. Facebook: controllo per URL con Bright Data (a lotti).
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

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
        # Visti da Subito nelle ultime ore durante la raccolta: sono attivi, non serve riaprirli
        fresh = {x["id"] for x in conn.execute(
            "SELECT id FROM listings WHERE id = ANY(%s) AND last_seen_at > now() - make_interval(hours => %s)",
            ([r["id"] for r in sb], int(os.environ.get("VERIFICA_ORE_FRESCHI", "6")))).fetchall()}
        for r in sb:
            if r["id"] in fresh:
                mark(conn, r["id"], "attivo", r.get("price_eur"))
                stats["subito:attivo_recente"] += 1
        todo = [r for r in sb if r["id"] not in fresh]

        def check(r):
            col = SubitoCollector(proxy=settings.scraper_proxy)
            try:
                return r, ("attivo" if col.fetch(r["url"]) is not None else "scomparso")
            except Exception as e:
                log.warning("verifica Subito fallita %s: %s", r["url"], e)
                return r, "errore"

        # pochi controlli in parallelo: più veloce, ma sempre con un ritmo rispettoso per il sito
        with ThreadPoolExecutor(max_workers=int(os.environ.get("VERIFICA_PARALLELI", "3"))) as ex:
            for r, st in ex.map(check, todo):
                mark(conn, r["id"], st, r.get("price_eur"))
                stats[f"subito:{st}"] += 1
    conn.commit()
    return stats
