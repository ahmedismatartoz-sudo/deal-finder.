"""Passi già fatti di una raccolta lunga (fascia di prezzo, provincia, centro): dopo un riavvio del sito la
raccolta riparte da dove era arrivata invece che da capo. Attivo solo se c'è PASSI_JOB nell'ambiente.

12/11: dentro il sito più raccolte girano in thread dello stesso processo, e PASSI_JOB (ambiente) e la cache sono di
tutto il processo. Con `with locale(job):` un thread usa un job e una cache SOLO SUOI (gli altri thread non li vedono
e lui non vede PASSI_JOB); `dopo_segna` si chiama a ogni passo segnato (fascia chiusa)."""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager

from ..db import connect

log = logging.getLogger("passi")
_cache: set | None = None
_loc = threading.local()


def job() -> str | None:
    return getattr(_loc, "job", None) or os.environ.get("PASSI_JOB") or None


@contextmanager
def locale(j: str, dopo_segna=None):
    """Job dei passi solo per questo thread (con la sua cache)."""
    prima = (getattr(_loc, "job", None), getattr(_loc, "cache", None), getattr(_loc, "dopo", None))
    _loc.job, _loc.cache, _loc.dopo = j, None, dopo_segna
    try:
        yield
    finally:
        _loc.job, _loc.cache, _loc.dopo = prima


def _leggi_cache() -> set | None:
    return _loc.cache if getattr(_loc, "job", None) else _cache


def _scrivi_cache(c: set) -> None:
    global _cache
    if getattr(_loc, "job", None):
        _loc.cache = c
    else:
        _cache = c


def fatto(chiave: str) -> bool:
    j = job()
    if not j:
        return False
    c = _leggi_cache()
    if c is None:
        try:
            with connect() as conn:
                c = {r["chiave"] for r in conn.execute("SELECT chiave FROM raccolta_passi WHERE job=%s",
                                                       (j,)).fetchall()}
            if c:
                log.info("raccolta %s: riprendo, %d passi già fatti", j, len(c))
        except Exception:
            c = set()
        _scrivi_cache(c)
    return chiave in c


def segna(chiave: str) -> None:
    j = job()
    if not j:
        return
    try:
        with connect() as conn:
            conn.execute("INSERT INTO raccolta_passi (job, chiave) VALUES (%s,%s) ON CONFLICT DO NOTHING", (j, chiave))
            conn.commit()
        c = _leggi_cache()
        if c is not None:
            c.add(chiave)
    except Exception as e:
        log.warning("passo non salvato: %s", str(e)[:120])
        return
    dopo = getattr(_loc, "dopo", None) if getattr(_loc, "job", None) else None
    if dopo is not None:
        dopo(chiave)


def finito(j: str) -> None:
    """Raccolta completata: i passi non servono più (la prossima volta si rifà tutto)."""
    with connect() as conn:
        conn.execute("DELETE FROM raccolta_passi WHERE job=%s", (j,))
        conn.commit()
