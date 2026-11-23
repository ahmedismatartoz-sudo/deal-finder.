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
            # 23/11 (sicurezza): un account nato dalla registrazione con password NON ha l'email verificata: chiunque
            # può aver registrato questo indirizzo con la propria password. Google/Apple invece la verificano: al primo
            # collegamento vince chi dimostra di avere l'email -> password azzerata e sessioni aperte chiuse.
            if d.get("password_hash") and _da_registrazione_non_verificata(d):
                conn.execute(f"UPDATE dealers SET {col}=%s, password_hash=NULL WHERE id=%s", (info["sub"], d["id"]))
                from . import abbonamento, sessions
                if abbonamento.ha_colonna(conn, "dealers", "email_verificata"):
                    conn.execute("UPDATE dealers SET email_verificata=true WHERE id=%s", (d["id"],))
                sessions.revoke_all(conn, d["id"], "collegato_" + provider)      # fa anche il commit
                import logging
                logging.getLogger("dealfinder.web").warning("ACCESSO_SOCIAL password azzerata al collegamento id=%s", d["id"])
                d = conn.execute("SELECT * FROM dealers WHERE id=%s", (d["id"],)).fetchone()
            else:
                conn.execute(f"UPDATE dealers SET {col}=%s WHERE id=%s", (info["sub"], d["id"]))
                conn.commit()
    if d:
        if d["active"]:
            return d, "ok"
        pending = conn.execute("SELECT 1 FROM access_requests WHERE email=%s AND NOT handled",
                               (d["email"],)).fetchone()
        return d, ("in_attesa" if pending else "disattivato")
    name = (info.get("name") or name_hint or info["email"].split("@")[0])[:120]
    return crea_account(conn, name, info["email"], {col: info["sub"]}, provider,
                        f"Iscritto con {provider.capitalize()}")


def _da_registrazione_non_verificata(d: dict) -> bool:
    """Account creato da /api/auth/registrati e mai entrato con Google/Apple. Con la migrazione 065: colonna
    email_verificata = false. Senza (migrazione rimandata): i segni che scrive solo crea_account con la password."""
    if d.get("google_sub") or d.get("apple_sub") or d.get("role") == "admin":
        return False
    if "email_verificata" in d:
        return d["email_verificata"] is False
    return bool(d.get("richiede_abbonamento") and d.get("paga_carta"))


def registrazione_attiva() -> bool:
    """23/11: registrazione con email e password dal sito. Accesa di default (REGISTRAZIONE=0 la spegne), ma SOLO con
    ISCRIZIONE=pagamento: chi si registra da solo entra pagando, mai con l'iscrizione aperta o in approvazione."""
    return os.environ.get("REGISTRAZIONE", "1").strip() != "0" and os.environ.get("ISCRIZIONE") == "pagamento"


def crea_account(conn, name: str, email: str, extra: dict, fonte: str, nota: str,
                 richiesta: dict | None = None) -> tuple[dict, str]:
    """Account nuovo di chi si iscrive da solo: UNA sola regola per Google/Apple e per la registrazione con password
    (23/11). `extra` = colonne in più di dealers scritte dal codice (google_sub, password_hash, company, phone...),
    mai nomi presi dalla richiesta. Ritorna (commerciante, esito): esito = ok | in_attesa.

    ISCRIZIONE=pagamento: account attivo, richiede_abbonamento, permesso "auto", tutte le zone; le auto si vedono
    dopo il pagamento. Altrimenti: attivo solo con ISCRIZIONE=aperta, se no resta in attesa con una riga in
    access_requests (la vede l'amministratore)."""
    cols = {"name": name[:120], "email": email, "role": "commerciante", **extra}
    if os.environ.get("ISCRIZIONE") == "pagamento":
        # iscrizione libera con abbonamento: l'account è attivo, le auto si vedono dopo il pagamento
        from ..zone import tutte
        cols.update(active=True, richiede_abbonamento=True, permessi=["auto"], provinces=tutte())
        esito = "ok"
    else:
        cols["active"] = signup_open()
        esito = "ok" if cols["active"] else "in_attesa"
    if extra.get("password_hash"):
        # abbonamento.puo_vedere lascia entrare senza abbonamento gli account con password (creati da noi dal
        # gestionale): chi si registra da solo con la password deve SEMPRE pagare con carta, in qualunque modalità
        cols["paga_carta"] = True
        from . import abbonamento
        if abbonamento.ha_colonna(conn, "dealers", "email_verificata"):
            cols["email_verificata"] = False      # nessuno ha ancora dimostrato di avere questo indirizzo
    nomi = list(cols)
    d = conn.execute(
        f"INSERT INTO dealers ({', '.join(nomi)}) VALUES ({','.join(['%s'] * len(nomi))}) RETURNING *",
        tuple(cols[k] for k in nomi)).fetchone()
    conn.execute("INSERT INTO dealer_costs (dealer_id) VALUES (%s) ON CONFLICT DO NOTHING", (d["id"],))
    if esito == "in_attesa":
        r = richiesta or {}
        conn.execute("INSERT INTO access_requests (name, company, email, phone, note, source) VALUES (%s,%s,%s,%s,%s,%s)",
                     (name[:120], r.get("company"), email, r.get("phone"), nota, fonte))
    conn.commit()
    return d, esito


def one_time_code(dealer: dict, method: str = "google") -> str:
    now = time.time()
    for k in [k for k, (exp, _) in _codes.items() if exp < now]:
        _codes.pop(k, None)
    code = secrets.token_urlsafe(24)
    _codes[code] = (now + 60, {"id": dealer["id"], "role": dealer["role"], "name": dealer["name"],
                               "email": dealer["email"], "method": method})
    return code


def redeem(code: str) -> dict | None:
    item = _codes.pop(code or "", None)
    if not item or item[0] < time.time():
        return None
    return item[1]
