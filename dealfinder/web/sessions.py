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
}


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
            row = conn.execute(
                "UPDATE dealer_sessions s SET last_seen_at=now() FROM dealers d WHERE s.id=%s AND d.id=s.dealer_id "
                "RETURNING s.dealer_id, s.revoked_why, (s.revoked_at IS NULL) AS open, d.active",
                (sid,)).fetchone()
            conn.commit()
        _cache[sid] = (now, row)
        if len(_cache) > 5000:
            for k in [k for k, v in _cache.items() if now - v[0] > CACHE_SECONDS]:
                _cache.pop(k, None)
    if not row or row["dealer_id"] != dealer_id:
        return False, None
    if not row["active"]:
        return False, "account_disattivato"
    if not row["open"]:
        return False, row["revoked_why"]
    return True, None
