"""Passi già fatti di una raccolta lunga (fascia di prezzo, provincia, centro): dopo un riavvio del sito la
raccolta riparte da dove era arrivata invece che da capo. Attivo solo se c'è PASSI_JOB nell'ambiente."""
from __future__ import annotations

import logging
import os

from ..db import connect

log = logging.getLogger("passi")
_cache: set | None = None


def job() -> str | None:
    return os.environ.get("PASSI_JOB") or None


def fatto(chiave: str) -> bool:
    global _cache
    j = job()
    if not j:
        return False
    if _cache is None:
        try:
            with connect() as conn:
                _cache = {r["chiave"] for r in conn.execute("SELECT chiave FROM raccolta_passi WHERE job=%s",
                                                             (j,)).fetchall()}
            if _cache:
                log.info("raccolta %s: riprendo, %d passi già fatti", j, len(_cache))
        except Exception:
            _cache = set()
    return chiave in _cache


def segna(chiave: str) -> None:
    j = job()
    if not j:
        return
    try:
        with connect() as conn:
            conn.execute("INSERT INTO raccolta_passi (job, chiave) VALUES (%s,%s) ON CONFLICT DO NOTHING", (j, chiave))
            conn.commit()
        if _cache is not None:
            _cache.add(chiave)
    except Exception as e:
        log.warning("passo non salvato: %s", str(e)[:120])


def finito(j: str) -> None:
    """Raccolta completata: i passi non servono più (la prossima volta si rifà tutto)."""
    with connect() as conn:
        conn.execute("DELETE FROM raccolta_passi WHERE job=%s", (j,))
        conn.commit()
