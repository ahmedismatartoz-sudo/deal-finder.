"""Pulizia del database: tutte le energie su Milano e dintorni (province in zone.MILANO).

    COLLECT_MODO=spazio   (SPAZIO_SOLO_REPORT=1 per vedere solo i numeri)

1. fuori zona: annunci (e foto, stime, prezzi collegati) di province fuori da Milano e dintorni;
2. foto salvate di auto che non sono sul sito né candidate (si riscaricano se servono);
3. annunci spariti da più di SPAZIO_GIORNI_SPARITI giorni (60);
4. registri vecchi (lavori, consumi);
5. VACUUM ANALYZE delle tabelle grandi.
Nei log: SPAZIO ..."""
from __future__ import annotations

import logging
import os

import psycopg

from ..db import connect
from ..zone import MILANO

log = logging.getLogger("spazio")


def report(tag: str) -> None:
    with connect() as conn:
        tot = conn.execute("SELECT pg_size_pretty(pg_database_size(current_database())) AS s").fetchone()["s"]
        rows = conn.execute("""SELECT relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS s,
                                      pg_total_relation_size(c.oid) AS b
                               FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                               WHERE n.nspname='public' AND c.relkind='r' ORDER BY b DESC LIMIT 12""").fetchall()
        zone = conn.execute("""SELECT source, (province = ANY(%s)) AS in_zona, status, count(*) AS n
                               FROM listings GROUP BY 1,2,3 ORDER BY 4 DESC""", (list(MILANO),)).fetchall()
    log.info("SPAZIO %s database %s | %s", tag, tot, ", ".join(f"{r['relname']} {r['s']}" for r in rows))
    log.info("SPAZIO %s annunci %s", tag, [(r["source"], r["in_zona"], r["status"], r["n"]) for r in zone])


def _cancella(sql_ids: str, params: tuple, nome: str) -> int:
    """Cancella a blocchi gli annunci scelti (tranne quelli con un riscontro dei commercianti)."""
    tot = 0
    while True:
        with connect() as conn:
            conn.execute("SET LOCAL lock_timeout = '20s'")
            ids = [r["id"] for r in conn.execute(sql_ids + " AND NOT EXISTS (SELECT 1 FROM dealer_feedback f "
                                                 "WHERE f.listing_id=listings.id) LIMIT 2000", params).fetchall()]
            if not ids:
                break
            conn.execute("DELETE FROM listings WHERE id = ANY(%s)", (ids,))
            conn.commit()
        tot += len(ids)
        log.info("SPAZIO %s: %d annunci cancellati finora", nome, tot)
    return tot


def run() -> dict:
    report("PRIMA")
    if os.environ.get("SPAZIO_SOLO_REPORT") == "1":
        return {}
    out = {}
    zona = list(MILANO)
    # 1. fuori zona (Facebook senza provincia: si tiene, la raccolta è già solo Milano)
    out["fuori_zona"] = _cancella(
        "SELECT id FROM listings WHERE province IS NOT NULL AND NOT (province = ANY(%s))", (zona,), "fuori zona")
    # 3. spariti da tempo
    giorni = int(os.environ.get("SPAZIO_GIORNI_SPARITI", "60"))
    out["spariti"] = _cancella(
        "SELECT id FROM listings WHERE status <> 'attivo' AND COALESCE(disappeared_at, last_seen_at) "
        "< now() - make_interval(days => %s)", (giorni,), "spariti")
    with connect() as conn:
        conn.execute("SET LOCAL lock_timeout = '20s'")
        # 2. foto salvate di auto non sul sito / non candidate (tranne le ultime 3 giornate)
        out["foto"] = conn.execute(
            """DELETE FROM photo_cache p USING listings l
               WHERE p.listing_id=l.id AND p.saved_at < now() - interval '3 days'
                 AND NOT (l.status='attivo' AND l.stage IN ('approfondito','candidato'))""").rowcount
        conn.commit()
        # 4. registri vecchi
        for tab, col, gg in (("ai_usage", "at", 30), ("raccolta_passi", "fatto_at", 7)):
            try:
                out[tab] = conn.execute(f"DELETE FROM {tab} WHERE {col} < now() - make_interval(days => %s)",
                                        (gg,)).rowcount
                conn.commit()
            except Exception as e:
                conn.rollback()
                log.info("SPAZIO %s saltata: %s", tab, str(e)[:120])
    log.info("SPAZIO cancellati %s", out)
    # 5. VACUUM: rende lo spazio riutilizzabile (fuori da una transazione)
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            for tab in ("photo_cache", "listings", "valuations", "listing_photos", "price_events"):
                try:
                    c.execute(f"VACUUM (ANALYZE) {tab}")
                    log.info("SPAZIO vacuum %s fatto", tab)
                except Exception as e:
                    log.info("SPAZIO vacuum %s saltato: %s", tab, str(e)[:120])
    except Exception as e:
        log.info("SPAZIO vacuum non riuscito: %s", str(e)[:150])
    report("DOPO")
    return out
