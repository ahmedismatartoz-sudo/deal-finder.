"""Memoria del processo web (12/11, cliente: "il sito va fuori memoria"): 512 MB con ~90 thread.

Due aiuti sicuri, senza cambiare piano su Render:
  - limita_arene(): glibc crea un'"arena" di memoria per ogni thread che alloca (fino a 8 × CPU): con decine di thread
    la memoria liberata resta sparsa in tante arene e il processo non la restituisce mai. Con MEMORIA_ARENE (2) le
    arene sono poche e la memoria si riusa. Va chiamata PRIMA di avviare i thread (all'import del sito).
  - rilascia(): dopo un lavoro grande (giro Jev, lettura completa di Subito) restituisce al sistema la memoria libera
    (gc + malloc_trim). Al massimo ogni MEMORIA_RILASCIA_MIN_S secondi (600): gc.collect con 90 thread non è gratis.
Interruttori nel codice: MEMORIA_ARENE=0 spegne il limite, MEMORIA_RILASCIA=0 spegne il rilascio.
Fuori da Linux/glibc non fanno niente.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import gc
import logging
import os
import time

log = logging.getLogger("memoria")
M_ARENA_MAX = -8           # costante di mallopt (malloc.h)
_libc: dict = {}
_ultimo_rilascio = [float("-inf")]


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


# 19/11 (cliente: "non voglio sistemare la memoria a mano quando passo a 2 GB"): il tetto di memoria dell'elenco del
# sito si calcola da solo dal limite VERO del container (cgroup v2 memory.max, poi cgroup v1 memory.limit_in_bytes,
# altrimenti i 512 MB del piano starter), alla quota TETTO_QUOTA (85%). Stessa unità di rss_mb (byte / 1.000.000):
# 512 MiB = 536,9 MB → tetto 456 MB (prima 450 fissi). Con 2 GB il tetto sale da solo a ~1.825 MB. Le variabili
# d'ambiente, se presenti, restano un override (ELENCO_MEMORIA_MB per il tetto, MEMORIA_CONTAINER_MB per il limite).
CGROUP_V2 = "/sys/fs/cgroup/memory.max"
CGROUP_V1 = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
PREDEFINITO_MB = 512 * 1024 * 1024 / 1e6      # 536,9 MB: piano starter di Render
TETTO_QUOTA = 0.85
_SENZA_LIMITE = 1 << 60                       # cgroup v1 senza limite scrive un numero enorme (9223372036854771712)


def _leggi_limite(percorso: str) -> float | None:
    """Limite in MB (byte / 1e6) da un file del cgroup; None se manca, illeggibile o "max" (nessun limite)."""
    try:
        with open(percorso) as f:
            v = f.read().strip()
    except Exception:
        return None
    if not v or v == "max":
        return None
    try:
        b = int(v)
    except ValueError:
        return None
    if b <= 0 or b >= _SENZA_LIMITE:
        return None
    return b / 1e6


def limite_container(v2: str = CGROUP_V2, v1: str = CGROUP_V1) -> tuple[float, str]:
    """(limite di memoria del container in MB, da dove viene): env MEMORIA_CONTAINER_MB, cgroup v2, cgroup v1,
    altrimenti PREDEFINITO_MB (512 MiB)."""
    env = os.environ.get("MEMORIA_CONTAINER_MB")
    if env:
        try:
            if float(env) > 0:
                return float(env), "env"
        except ValueError:
            pass
    for percorso, nome in ((v2, "cgroup_v2"), (v1, "cgroup_v1")):
        mb = _leggi_limite(percorso)
        if mb:
            return mb, nome
    return PREDEFINITO_MB, "predefinito"


def tetto_elenco(v2: str = CGROUP_V2, v1: str = CGROUP_V1) -> dict:
    """Tetto di memoria per la lettura dell'elenco del sito: {"tetto_mb", "limite_mb", "fonte", "quota"}.
    ELENCO_MEMORIA_MB (se presente) vince; ELENCO_MEMORIA_QUOTA cambia la quota (default 0,85)."""
    limite, fonte = limite_container(v2, v1)
    try:
        quota = float(os.environ.get("ELENCO_MEMORIA_QUOTA") or TETTO_QUOTA)
    except ValueError:
        quota = TETTO_QUOTA
    quota = min(0.95, max(0.3, quota))
    env = os.environ.get("ELENCO_MEMORIA_MB")
    if env not in (None, ""):
        try:
            return {"tetto_mb": float(env), "limite_mb": round(limite, 1), "fonte": "env:ELENCO_MEMORIA_MB",
                    "quota": None}
        except ValueError:
            pass
    return {"tetto_mb": round(limite * quota, 1), "limite_mb": round(limite, 1), "fonte": fonte, "quota": quota}


def scala(valore_512: float, limite_mb: float | None = None) -> float:
    """Un valore pensato per il container da 512 MB riportato al limite vero (mai sotto quello di partenza)."""
    lim = limite_mb if limite_mb is not None else limite_container()[0]
    return valore_512 * max(1.0, lim / PREDEFINITO_MB)


def limita_arene() -> bool:
    n = int(os.environ.get("MEMORIA_ARENE", "2") or 0)
    c = _c()
    if n <= 0 or c is None or not hasattr(c, "mallopt"):
        return False
    try:
        return bool(c.mallopt(M_ARENA_MAX, n))
    except Exception:
        return False


def rilascia(motivo: str = "", forza: bool = False) -> float:
    """gc + malloc_trim(0): ritorna i MB restituiti al sistema (0 se spento, non disponibile o fatto da poco).
    forza=True salta l'attesa dei 10 minuti (dopo un lavoro raro e grande)."""
    if os.environ.get("MEMORIA_RILASCIA", "1") != "1":
        return 0.0
    ora = time.monotonic()
    if not forza and ora - _ultimo_rilascio[0] < float(os.environ.get("MEMORIA_RILASCIA_MIN_S", "600")):
        return 0.0
    _ultimo_rilascio[0] = ora
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
