"""Archivio del modulo Incentivi: PostgreSQL + documenti cifrati.

I file (visure, preventivi, DURC…) sono cifrati con Fernet (AES-128-CBC + HMAC) prima di
finire nel database. La chiave deriva da INCENTIVI_CHIAVE oppure, se manca, da SECRET_KEY
(HKDF-SHA256 con un'etichetta dedicata): nessuna chiave nel codice.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from functools import lru_cache


@lru_cache(maxsize=1)
def _fernet():
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    seed = os.environ.get("INCENTIVI_CHIAVE") or os.environ.get("SECRET_KEY")
    if not seed:
        if os.environ.get("RENDER"):
            raise RuntimeError("SECRET_KEY o INCENTIVI_CHIAVE mancante")
        seed = "solo-sviluppo-locale"
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"incentivi-documenti",
               info=b"fernet-v1").derive(seed.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def cifra(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decifra(data: bytes) -> bytes:
    return _fernet().decrypt(bytes(data))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nuovo_token(n: int = 24) -> str:
    return secrets.token_urlsafe(n)


def ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(("inc|" + ip).encode()).hexdigest()[:16]


def J(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def ensure(conn) -> None:
    """Tabelle del modulo (stessa migrazione del repository, idempotente)."""
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "db" / "migrations" / "016_incentivi.sql").read_text()
    conn.execute(sql)
    conn.commit()


def log(conn, livello: str, messaggio: str, dettagli: dict | None = None) -> None:
    conn.execute("INSERT INTO inc_job_log (livello, messaggio, dettagli) VALUES (%s,%s,%s)",
                 (livello, messaggio, J(dettagli) if dettagli else None))
