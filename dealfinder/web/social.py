"""Entra con Google e con Apple (OpenID Connect, flusso con codice lato server).

Niente script di Google o Apple nel sito: il bottone porta a /auth/google o /auth/apple,
il fornitore rimanda a /auth/<fornitore>/callback con un codice, il server lo scambia
direttamente con il fornitore (HTTPS) e legge email e identificativo dal token ricevuto.
Poi il sito riceve un codice monouso (60 secondi) e lo cambia nel solito token di Scovo.

Variabili d'ambiente:
  PUBLIC_URL                    es. https://scovo.onrender.com (per gli indirizzi di ritorno)
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
  APPLE_CLIENT_ID (Services ID), APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY (file .p8)
  ISCRIZIONE = approvazione (predefinito: i nuovi account aspettano l'ok dell'amministratore)
             | aperta (i nuovi commercianti entrano subito)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

from .auth import _secret

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
APPLE_AUTH = "https://appleid.apple.com/auth/authorize"
APPLE_TOKEN = "https://appleid.apple.com/auth/token"
STATE_COOKIE = "scovo_stato"

_codes: dict[str, tuple[float, dict]] = {}     # codice monouso -> (scadenza, dati per il token)


def enabled() -> dict:
    return {"google": bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")),
            "apple": bool(os.environ.get("APPLE_CLIENT_ID") and os.environ.get("APPLE_TEAM_ID")
                          and os.environ.get("APPLE_KEY_ID") and os.environ.get("APPLE_PRIVATE_KEY"))}


def signup_open() -> bool:
    return os.environ.get("ISCRIZIONE", "approvazione") == "aperta"


def base_url(request) -> str:
    url = os.environ.get("PUBLIC_URL")
    if url:
        return url.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{request.headers.get('host')}"


def redirect_uri(request, provider: str) -> str:
    return f"{base_url(request)}/auth/{provider}/callback"


# --- stato firmato (contro le richieste false) -------------------------------------------
def make_state(provider: str) -> str:
    nonce = secrets.token_urlsafe(16)
    raw = f"{provider}.{int(time.time())}.{nonce}"
    sig = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{raw}.{sig}"


def check_state(state: str | None, cookie: str | None, provider: str) -> bool:
    if not state or not cookie or not hmac.compare_digest(state, cookie):
        return False
    try:
        prov, ts, nonce, sig = state.split(".")
    except ValueError:
        return False
    good = hmac.new(_secret(), f"{prov}.{ts}.{nonce}".encode(), hashlib.sha256).hexdigest()[:24]
    return prov == provider and hmac.compare_digest(sig, good) and time.time() - int(ts) < 600


# --- indirizzi dei fornitori --------------------------------------------------------------
def auth_url(request, provider: str, state: str) -> str:
    if provider == "google":
        return GOOGLE_AUTH + "?" + urlencode({
            "client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": redirect_uri(request, "google"),
            "response_type": "code", "scope": "openid email profile", "state": state,
            "prompt": "select_account"})
    return APPLE_AUTH + "?" + urlencode({
        "client_id": os.environ["APPLE_CLIENT_ID"], "redirect_uri": redirect_uri(request, "apple"),
        "response_type": "code", "scope": "name email", "response_mode": "form_post", "state": state})


def _claims(id_token: str) -> dict:
    """Legge il token ricevuto direttamente dal fornitore sul canale HTTPS (OIDC 3.1.3.7)."""
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def apple_client_secret() -> str:
    import jwt
    now = int(time.time())
    key = os.environ["APPLE_PRIVATE_KEY"].replace("\\n", "\n")
    return jwt.encode({"iss": os.environ["APPLE_TEAM_ID"], "iat": now, "exp": now + 300,
                       "aud": "https://appleid.apple.com", "sub": os.environ["APPLE_CLIENT_ID"]},
                      key, algorithm="ES256", headers={"kid": os.environ["APPLE_KEY_ID"]})


def exchange(request, provider: str, code: str, http=None) -> dict:
    """Codice -> {sub, email, email_verified, name}. Errore se qualcosa non torna."""
    import httpx
    http = http or httpx.Client(timeout=15)
    if provider == "google":
        data = {"client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri(request, "google")}
        url, aud, issuers = GOOGLE_TOKEN, os.environ["GOOGLE_CLIENT_ID"], {"https://accounts.google.com", "accounts.google.com"}
    else:
        data = {"client_id": os.environ["APPLE_CLIENT_ID"], "client_secret": apple_client_secret(),
                "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri(request, "apple")}
        url, aud, issuers = APPLE_TOKEN, os.environ["APPLE_CLIENT_ID"], {"https://appleid.apple.com"}
    r = http.post(url, data=data, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise ValueError(f"scambio codice rifiutato ({r.status_code})")
    c = _claims(r.json()["id_token"])
    if c.get("iss") not in issuers or c.get("aud") != aud or int(c.get("exp", 0)) < time.time():
        raise ValueError("token non valido")
    verified = c.get("email_verified") in (True, "true")
    if not c.get("email") or not verified:
        raise ValueError("email non verificata")
    return {"sub": str(c["sub"]), "email": str(c["email"]).lower(), "name": c.get("name")}


# --- account ------------------------------------------------------------------------------
def find_or_create(conn, provider: str, info: dict, name_hint: str | None = None) -> tuple[dict | None, str]:
    """Ritorna (commerciante, esito): esito = ok | in_attesa | disattivato."""
    col = "google_sub" if provider == "google" else "apple_sub"
    d = conn.execute(f"SELECT * FROM dealers WHERE {col}=%s", (info["sub"],)).fetchone()
    if not d:
        d = conn.execute("SELECT * FROM dealers WHERE email=%s", (info["email"],)).fetchone()
        if d:
            conn.execute(f"UPDATE dealers SET {col}=%s WHERE id=%s", (info["sub"], d["id"]))
            conn.commit()
    if d:
        if d["active"]:
            return d, "ok"
        pending = conn.execute("SELECT 1 FROM access_requests WHERE email=%s AND NOT handled",
                               (d["email"],)).fetchone()
        return d, ("in_attesa" if pending else "disattivato")
    name = (info.get("name") or name_hint or info["email"].split("@")[0])[:120]
    active = signup_open()
    d = conn.execute(
        f"INSERT INTO dealers (name, email, role, active, {col}) VALUES (%s,%s,'commerciante',%s,%s) RETURNING *",
        (name, info["email"], active, info["sub"])).fetchone()
    conn.execute("INSERT INTO dealer_costs (dealer_id) VALUES (%s) ON CONFLICT DO NOTHING", (d["id"],))
    if not active:
        conn.execute("INSERT INTO access_requests (name, email, note, source) VALUES (%s,%s,%s,%s)",
                     (name, info["email"], f"Iscritto con {provider.capitalize()}", provider))
    conn.commit()
    return d, ("ok" if active else "in_attesa")


def one_time_code(dealer: dict) -> str:
    now = time.time()
    for k in [k for k, (exp, _) in _codes.items() if exp < now]:
        _codes.pop(k, None)
    code = secrets.token_urlsafe(24)
    _codes[code] = (now + 60, {"id": dealer["id"], "role": dealer["role"], "name": dealer["name"],
                               "email": dealer["email"]})
    return code


def redeem(code: str) -> dict | None:
    item = _codes.pop(code or "", None)
    if not item or item[0] < time.time():
        return None
    return item[1]
