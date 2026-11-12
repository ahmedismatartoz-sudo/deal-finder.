"""Password e token di accesso (solo libreria standard)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

# 12/11: token di 90 giorni (prima 10 anni), rinnovato con l'uso: il server manda un token nuovo nell'intestazione
# X-Scovo-Token (rinnovo_token) e il sito lo salva. Chi entra almeno una volta ogni 90 giorni resta collegato.
# I token di prima (10 anni) restano validi, ma la sessione si chiude dopo 90 giorni senza uso (sessions.check) e al
# primo uso vengono sostituiti da un token di 90 giorni. TOKEN_GIORNI su Render vale al massimo 90.
TOKEN_DAYS = max(1, min(int(os.environ.get("TOKEN_GIORNI", "90") or 90), 90))
RINNOVO_DOPO_S = 86400           # un token nuovo al massimo una volta al giorno per sessione


def _secret() -> bytes:
    s = os.environ.get("SECRET_KEY")
    if not s:
        # 25/10: il cron (ricalcolo prezzi) firma i link foto solo per confronti interni, mai salvati né mostrati:
        # lì basta una chiave locale. Il sito web resta obbligato ad avere SECRET_KEY.
        if os.environ.get("RENDER_SERVICE_TYPE") == "cron":
            return b"solo-cron-confronti-interni"
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


def make_token(dealer_id: int, role: str, sid: str | None = None, ora: float | None = None) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"id": dealer_id, "role": role, "sid": sid,
         "exp": int(ora or time.time()) + TOKEN_DAYS * 86400}).encode()).decode()
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


def rinnovo_token(data: dict | None, ora: float | None = None) -> str | None:
    """Token nuovo (stessa sessione, 90 giorni da adesso) se quello usato ha più di un giorno o dura più di 90 giorni
    (token di prima). None se non serve. La validità della sessione la controlla sempre sessions.check."""
    if not data or not data.get("sid"):
        return None
    ora = ora or time.time()
    resta = int(data.get("exp") or 0) - ora
    if resta <= 0:
        return None
    if resta > TOKEN_DAYS * 86400 or resta < TOKEN_DAYS * 86400 - RINNOVO_DOPO_S:
        return make_token(int(data["id"]), str(data.get("role") or "commerciante"), data["sid"], ora)
    return None


# ---------- link di accesso (senza scrivere la password) ----------
# 12/11: il link vale 1 ora (prima 7 giorni) e una volta sola (app.auth_scambio, tabella link_accesso_usati).
LINK_ORE = max(1, min(int(os.environ.get("LINK_ORE", "1") or 1), 24))
LINK_GIORNI = LINK_ORE / 24          # compatibilità: chi legge ancora LINK_GIORNI


def _link_sig(dealer_id: int, exp: int, pw_hash: str | None) -> str:
    msg = f"link:{dealer_id}:{exp}:{(pw_hash or '')[-24:]}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:40]


def make_link_code(dealer_id: int, pw_hash: str | None) -> str:
    """Codice per entrare con un link: vale LINK_ORE ore, una volta sola, e smette di valere se cambia la password."""
    exp = int(time.time()) + LINK_ORE * 3600
    return f"L{dealer_id}x{exp}x{_link_sig(dealer_id, exp, pw_hash)}"


def parse_link_code(code: str) -> tuple[int, int, str] | None:
    try:
        a, b, sig = code[1:].split("x")
        return int(a), int(b), sig
    except (ValueError, IndexError):
        return None


def impronta_link(code: str) -> str:
    """Impronta del link (mai il link in chiaro nel database) per ricordare che è già stato usato."""
    return hashlib.sha256(code.encode()).hexdigest()


def check_link_code(code: str, pw_hash: str | None) -> bool:
    p = parse_link_code(code)
    if not p or p[1] < time.time():
        return False
    return hmac.compare_digest(p[2], _link_sig(p[0], p[1], pw_hash))
