"""Password e token di accesso (solo libreria standard)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

TOKEN_DAYS = int(os.environ.get("TOKEN_GIORNI", "3650"))   # resta collegato: si esce solo con 'Esci' o se l'account viene chiuso


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


# ---------- link di accesso (senza scrivere la password) ----------
LINK_GIORNI = int(os.environ.get("LINK_GIORNI", "7"))


def _link_sig(dealer_id: int, exp: int, pw_hash: str | None) -> str:
    msg = f"link:{dealer_id}:{exp}:{(pw_hash or '')[-24:]}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:40]


def make_link_code(dealer_id: int, pw_hash: str | None) -> str:
    """Codice per entrare con un link: vale LINK_GIORNI giorni e smette di valere se cambia la password."""
    exp = int(time.time()) + LINK_GIORNI * 86400
    return f"L{dealer_id}x{exp}x{_link_sig(dealer_id, exp, pw_hash)}"


def parse_link_code(code: str) -> tuple[int, int, str] | None:
    try:
        a, b, sig = code[1:].split("x")
        return int(a), int(b), sig
    except (ValueError, IndexError):
        return None


def check_link_code(code: str, pw_hash: str | None) -> bool:
    p = parse_link_code(code)
    if not p or p[1] < time.time():
        return False
    return hmac.compare_digest(p[2], _link_sig(p[0], p[1], pw_hash))
