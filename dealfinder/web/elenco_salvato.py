"""Copia nel database dell'elenco del sito e delle statistiche della scheda (12/11, avvio senza buchi).

Prima l'elenco viveva solo in memoria: a ogni riavvio (deploy o fuori memoria) il sito restava senza auto da 40 s a
3,5 minuti, e i dati di vendita della scheda (trattativa.STATS) mancavano per circa 8 minuti. Ora:
  - a fine calcolo l'elenco si salva in sito_cache['elenco'] (JSON compresso; al massimo ogni ELENCO_SALVA_MIN_S
    secondi, e solo se è cambiato o la copia ha più di 10 minuti);
  - all'avvio il sito lo rilegge e serve subito l'ultimo elenco buono (se ha meno di ELENCO_SALVATO_MAX_ORE, 12)
    mentre ricalcola in sottofondo;
  - le statistiche della scheda si salvano dopo ogni calcolo (ogni 12 ore) in sito_cache['scheda_stats'] e si
    rileggono all'avvio: se hanno meno di 12 ore non si ricalcolano nemmeno.
Interruttore: ELENCO_SALVATO=0 spegne salvataggio e lettura. Se la tabella non c'è il sito funziona come prima.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import zlib
from datetime import date, datetime
from decimal import Decimal

log = logging.getLogger("scovo")

TABELLA_SQL = """CREATE TABLE IF NOT EXISTS sito_cache (chiave TEXT PRIMARY KEY, dati BYTEA NOT NULL, n INT, firma TEXT,
    salvato_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
_ultimo: dict = {}                     # chiave -> (firma, monotonic) dell'ultimo salvataggio
_lock = threading.Lock()
_in_corso: set = set()                 # chiavi che un thread sta scrivendo (un salvataggio alla volta per chiave)


def acceso() -> bool:
    return os.environ.get("ELENCO_SALVATO", "1") == "1"


# ------------------------------------------------------------------------------------------------ codifica
def _enc(o):
    if isinstance(o, datetime):
        return {"$dt": o.isoformat()}
    if isinstance(o, date):
        return {"$d": o.isoformat()}
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    if isinstance(o, (bytes, bytearray, memoryview)):
        return None
    return str(o)


def _dec(d: dict):
    if len(d) == 1:
        if "$dt" in d:
            return datetime.fromisoformat(d["$dt"])
        if "$d" in d:
            return date.fromisoformat(d["$d"])
    return d


def comprimi(obj) -> bytes:
    return zlib.compress(json.dumps(obj, default=_enc, ensure_ascii=False, separators=(",", ":")).encode(), 6)


def espandi(blob) -> object:
    return json.loads(zlib.decompress(bytes(blob)).decode(), object_hook=_dec)


# ------------------------------------------------------------------------------------------------ database
def _connetti(connetti=None):
    if connetti is not None:
        return connetti()
    from ..db import connect
    return connect()


def salva(chiave: str, obj, n: int | None = None, connetti=None, min_s: float = 0, max_s: float = 600) -> bool:
    """Scrive obj in sito_cache[chiave]. Salta se è passato meno di min_s dall'ultimo salvataggio, o se è uguale
    all'ultimo e sono passati meno di max_s. Mai un errore: ritorna False se non ha scritto."""
    if not acceso():
        return False
    try:
        blob = comprimi(obj)
        firma = hashlib.sha1(blob).hexdigest()
        with _lock:                       # solo il controllo: la scrittura nel database avviene fuori dal lucchetto
            prima = _ultimo.get(chiave)
            ora = time.monotonic()
            if chiave in _in_corso or (prima and (ora - prima[1] < min_s or (prima[0] == firma and ora - prima[1] < max_s))):
                return False
            _in_corso.add(chiave)
        try:
            with _connetti(connetti) as conn:
                conn.execute("SET LOCAL statement_timeout = '20s'")
                if not _ultimo.get("_tabella"):
                    conn.execute(TABELLA_SQL)          # se la migrazione 047 non è ancora passata
                    _ultimo["_tabella"] = (True, 0)
                conn.execute("INSERT INTO sito_cache (chiave, dati, n, firma, salvato_at) VALUES (%s,%s,%s,%s, now()) "
                             "ON CONFLICT (chiave) DO UPDATE SET dati=EXCLUDED.dati, n=EXCLUDED.n, firma=EXCLUDED.firma, "
                             "salvato_at=now()", (chiave, blob, n, firma))
                conn.commit()
            with _lock:
                _ultimo[chiave] = (firma, ora)
        finally:
            with _lock:
                _in_corso.discard(chiave)
        return True
    except Exception as e:
        log.warning("SITO_CACHE %s non salvato: %s", chiave, str(e)[:200])
        return False


def carica(chiave: str, max_ore: float | None = None, connetti=None) -> tuple[object, float] | None:
    """(oggetto, età in secondi) da sito_cache, None se manca, è troppo vecchio o non si legge."""
    if not acceso():
        return None
    try:
        with _connetti(connetti) as conn:
            conn.execute("SET LOCAL statement_timeout = '15s'")
            if not conn.execute("SELECT to_regclass('sito_cache') AS t").fetchone()["t"]:
                return None
            r = conn.execute("SELECT dati, extract(epoch FROM now() - salvato_at) AS eta FROM sito_cache WHERE chiave=%s",
                             (chiave,)).fetchone()
        if not r:
            return None
        eta = float(r["eta"] or 0)
        if max_ore is not None and eta > max_ore * 3600:
            log.warning("SITO_CACHE %s troppo vecchio (%.1f ore): non usato", chiave, eta / 3600)
            return None
        return espandi(r["dati"]), eta
    except Exception as e:
        log.warning("SITO_CACHE %s non letto: %s", chiave, str(e)[:200])
        return None


# ------------------------------------------------------------------------------------------------ elenco del sito
def salva_elenco(items: list, opens: dict, connetti=None) -> bool:
    if not items:                       # un elenco vuoto (database in difficoltà) non sostituisce quello buono
        return False
    return salva("elenco", {"v": 1, "items": items, "opens": {str(k): v for k, v in (opens or {}).items()}},
                 n=len(items), connetti=connetti, min_s=float(os.environ.get("ELENCO_SALVA_MIN_S", "60")))


def carica_elenco(connetti=None) -> tuple[list, dict, float] | None:
    """(auto, aperture, età in secondi) dell'ultimo elenco salvato, se ha meno di ELENCO_SALVATO_MAX_ORE ore."""
    r = carica("elenco", float(os.environ.get("ELENCO_SALVATO_MAX_ORE", "12")), connetti)
    if not r:
        return None
    d, eta = r
    if not isinstance(d, dict) or not d.get("items"):
        return None
    return list(d["items"]), {int(k): int(v) for k, v in (d.get("opens") or {}).items()}, eta


# ------------------------------------------------------------------------------------------------ statistiche della scheda
_STATS_CHIAVI = ("gruppi", "modelli", "attivi", "esempi")


def stats_in_json(stats: dict) -> dict:
    """trattativa.STATS ha chiavi tupla: si salvano come liste [chiave, valore]."""
    out = {"t": stats.get("t"), "n": stats.get("n")}
    for k in _STATS_CHIAVI:
        out[k] = [[list(kk), v] for kk, v in (stats.get(k) or {}).items()]
    return out


def stats_da_json(d: dict) -> dict:
    out = {"n": int(d.get("n") or 0)}
    for k in _STATS_CHIAVI:
        out[k] = {tuple(kk): (tuple(v) if k in ("modelli", "attivi") else
                             [tuple(x) for x in v] if k == "esempi" else v) for kk, v in (d.get(k) or [])}
    return out


def salva_stats(stats: dict, connetti=None) -> bool:
    if not stats.get("gruppi") and not stats.get("modelli"):
        return False
    return salva("scheda_stats", stats_in_json(stats), n=len(stats.get("modelli") or {}), connetti=connetti)


def carica_stats(stats: dict, connetti=None) -> float | None:
    """Rimette in stats (trattativa.STATS) l'ultima copia salvata; ritorna la sua età in ore (None se non c'è)."""
    r = carica("scheda_stats", float(os.environ.get("SCHEDA_STATS_MAX_ORE", "72")), connetti)
    if not r:
        return None
    d, eta = r
    try:
        nuovo = stats_da_json(d)
    except Exception as e:
        log.warning("SCHEDA_STATS copia non valida: %s", str(e)[:200])
        return None
    stats.update(nuovo, t=time.time() - eta)
    log.warning("SCHEDA_STATS lette dal database (%.1f ore fa): %d gruppi, %d modelli", eta / 3600,
                len(nuovo["gruppi"]), len(nuovo["modelli"]))
    return eta / 3600
