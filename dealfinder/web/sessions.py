"""Sessioni personali: ogni accesso (email, Google, Apple) apre una sessione legata al commerciante.

- al massimo MAX_DISPOSITIVI sessioni attive per account (predefinito 2: telefono e computer);
  entrando da un terzo dispositivo si chiude quella usata meno di recente
- "Esci" chiude la sessione sul server, non solo sul telefono
- account disattivato o password cambiata: le sessioni si chiudono subito
Il controllo a ogni richiesta usa una piccola memoria di 60 secondi per non pesare sul database.
"""
from __future__ import annotations

import os
import secrets
import time

from ..db import connect

MAX_DISPOSITIVI = int(os.environ.get("MAX_DISPOSITIVI", "2"))
CACHE_SECONDS = 60
_cache: dict[str, tuple[float, dict | None]] = {}
REVOKED_MSG = {
    "altro_dispositivo": "Sei entrato da un altro dispositivo: entra di nuovo per continuare qui",
    "uscita": "Sei uscito: entra di nuovo",
    "account_disattivato": "Il tuo account non è attivo",
    "password_cambiata": "La password è cambiata: entra di nuovo",
    "scaduto": "Il periodo di accesso è finito: contattaci per continuare",
    "inattiva": "Non entravi da molto tempo: entra di nuovo",
}
# 12/11: una sessione non usata per SESSIONE_GIORNI giorni si chiude (vale anche per i token di 10 anni di prima)
SESSIONE_GIORNI = max(1, min(int(os.environ.get("SESSIONE_GIORNI", "90") or 90), 90))


def open_session(conn, dealer_id: int, method: str, user_agent: str | None) -> str:
    sid = secrets.token_urlsafe(18)
    conn.execute("INSERT INTO dealer_sessions (id, dealer_id, method, user_agent) VALUES (%s,%s,%s,%s)",
                 (sid, dealer_id, method, (user_agent or "")[:200]))
    old = conn.execute("SELECT id FROM dealer_sessions WHERE dealer_id=%s AND revoked_at IS NULL "
                       "ORDER BY last_seen_at DESC OFFSET %s", (dealer_id, MAX_DISPOSITIVI)).fetchall()
    for r in old:
        revoke(conn, r["id"], "altro_dispositivo", commit=False)
    conn.commit()
    return sid


def revoke(conn, sid: str, why: str, commit: bool = True) -> None:
    conn.execute("UPDATE dealer_sessions SET revoked_at=now(), revoked_why=%s WHERE id=%s AND revoked_at IS NULL",
                 (why, sid))
    _cache.pop(sid, None)
    if commit:
        conn.commit()


def revoke_all(conn, dealer_id: int, why: str, keep: str | None = None) -> None:
    rows = conn.execute("UPDATE dealer_sessions SET revoked_at=now(), revoked_why=%s WHERE dealer_id=%s "
                        "AND revoked_at IS NULL AND id <> %s RETURNING id", (why, dealer_id, keep or "")).fetchall()
    for r in rows:
        _cache.pop(r["id"], None)
    conn.commit()


def ruolo(sid: str | None) -> str | None:
    """Ruolo letto dal database con l'ultimo controllo della sessione (non quello scritto nel token)."""
    hit = _cache.get(sid or "")
    return (hit[1] or {}).get("role") if hit else None


def ruolo_fresco(dealer_id: int) -> str | None:
    """Ruolo riletto adesso dal database, senza la memoria di 60 secondi (per le pagine di amministrazione)."""
    with connect() as conn:
        row = conn.execute("SELECT role, active FROM dealers WHERE id=%s", (dealer_id,)).fetchone()
    return row["role"] if row and row["active"] else None


def dimentica_account(dealer_id: int) -> None:
    """Dopo un cambio fatto dal pannello (ruolo, attivo): la memoria delle sessioni di quell'account si svuota."""
    for k in [k for k, v in list(_cache.items()) if (v[1] or {}).get("dealer_id") == dealer_id]:
        _cache.pop(k, None)


def check(sid: str | None, dealer_id: int) -> tuple[bool, str | None]:
    """(valida, motivo se chiusa)."""
    if not sid:
        return False, None
    now = time.time()
    hit = _cache.get(sid)
    if hit and now - hit[0] < CACHE_SECONDS:
        row = hit[1]
    else:
        with connect() as conn:
            # p = la stessa riga PRIMA dell'aggiornamento: serve l'ultimo uso precedente per la scadenza di 90 giorni
            row = conn.execute(
                "UPDATE dealer_sessions s SET last_seen_at=now() FROM dealers d, dealer_sessions p "
                "WHERE s.id=%s AND d.id=s.dealer_id AND p.id=s.id "
                "RETURNING s.dealer_id, s.revoked_why, (s.revoked_at IS NULL) AS open, d.active, d.role, "
                "(d.accesso_fino IS NOT NULL AND d.accesso_fino < now()) AS scaduto, "
                "(p.last_seen_at < now() - make_interval(days => %s)) AS inattiva",
                (sid, SESSIONE_GIORNI)).fetchone()
            if row and row.get("inattiva") and row["open"]:
                conn.execute("UPDATE dealer_sessions SET revoked_at=now(), revoked_why='inattiva' WHERE id=%s "
                             "AND revoked_at IS NULL", (sid,))
                row = dict(row, open=False, revoked_why="inattiva")
            conn.commit()
        _cache[sid] = (now, row)
        if len(_cache) > 5000:
            for k in [k for k, v in _cache.items() if now - v[0] > CACHE_SECONDS]:
                _cache.pop(k, None)
    if not row or row["dealer_id"] != dealer_id:
        return False, None
    if not row["active"]:
        return False, "account_disattivato"
    if row.get("scaduto"):
        return False, "scaduto"
    if not row["open"]:
        return False, row["revoked_why"]
    return True, None
