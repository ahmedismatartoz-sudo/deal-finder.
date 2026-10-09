"""Password e token di accesso (solo libreria standard)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

TOKEN_DAYS = 30


def _secret() -> bytes:
    s = os.environ.get("SECRET_KEY")
    if not s:
        if os.environ.get("RENDER"):
            raise RuntimeError("SECRET_KEY mancante")
        s = "solo-sviluppo-locale"
    return s.encode()


def hash_password(pwd: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pwd.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_password(pwd: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, h = stored.split("$")
    calc = hashlib.scrypt(pwd.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1)
    return hmac.compare_digest(calc, base64.b64decode(h))


def make_token(dealer_id: int, role: str, sid: str | None = None) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"id": dealer_id, "role": role, "sid": sid, "exp": int(time.time()) + TOKEN_DAYS * 86400}).encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    return data if data.get("exp", 0) > time.time() else None
