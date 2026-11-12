"""Memoria del processo web (12/11, cliente: "il sito va fuori memoria"): 512 MB con ~90 thread.

Due aiuti sicuri, senza cambiare piano su Render:
  - limita_arene(): glibc crea un'"arena" di memoria per ogni thread che alloca (fino a 8 × CPU): con decine di thread
    la memoria liberata resta sparsa in tante arene e il processo non la restituisce mai. Con MEMORIA_ARENE (2) le
    arene sono poche e la memoria si riusa. Va chiamata PRIMA di avviare i thread (all'import del sito).
  - rilascia(): dopo un lavoro grande (elenco, giro Jev) restituisce al sistema la memoria libera (malloc_trim).
Interruttori nel codice: MEMORIA_ARENE=0 spegne il limite, MEMORIA_RILASCIA=0 spegne il rilascio.
Fuori da Linux/glibc non fanno niente.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import gc
import logging
import os

log = logging.getLogger("memoria")
M_ARENA_MAX = -8           # costante di mallopt (malloc.h)
_libc: dict = {}


def _c():
    if "c" not in _libc:
        try:
            _libc["c"] = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        except Exception:
            _libc["c"] = None
    return _libc["c"]


def rss_mb() -> float:
    """Memoria residente del processo in MB (0 se non leggibile)."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except Exception:
        return 0.0


def limita_arene() -> bool:
    n = int(os.environ.get("MEMORIA_ARENE", "2") or 0)
    c = _c()
    if n <= 0 or c is None or not hasattr(c, "mallopt"):
        return False
    try:
        return bool(c.mallopt(M_ARENA_MAX, n))
    except Exception:
        return False


def rilascia(motivo: str = "") -> float:
    """gc + malloc_trim(0): ritorna i MB restituiti al sistema (0 se spento o non disponibile)."""
    if os.environ.get("MEMORIA_RILASCIA", "1") != "1":
        return 0.0
    prima = rss_mb()
    gc.collect()
    c = _c()
    if c is not None and hasattr(c, "malloc_trim"):
        try:
            c.malloc_trim(0)
        except Exception:
            pass
    resi = max(0.0, prima - rss_mb())
    if resi >= 20:
        log.warning("MEMORIA %s: restituiti %.0f MB (ora %.0f MB)", motivo, resi, rss_mb())
    return resi
