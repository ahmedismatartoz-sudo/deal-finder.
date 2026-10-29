"""API di Deal Finder + sito web (stessa API usata in futuro dall'app mobile).

Avvio:  uvicorn dealfinder.web.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
from html import escape as html_esc
import logging
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..config import settings
from ..db import connect, usa_pool
from . import abbonamento, cards, foto, scovo, sessions, social
from .auth import check_link_code, hash_password, make_link_code, make_token, parse_link_code, read_token, verify_password

log = logging.getLogger("web")
STATIC = Path(__file__).parent / "static"
FEEDBACK_STATUSES = {"scartata", "contattato", "trattativa", "comprata", "venduta"}


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def err(status: int, message: str):
    raise HTTPError(status, message)


def current_user(request: Request, admin: bool = False) -> dict:
    auth = request.headers.get("authorization", "")
    data = read_token(auth[7:] if auth.lower().startswith("bearer ") else None)
    if not data:
        err(401, "Accesso richiesto")
    ok, why = sessions.check(data.get("sid"), data["id"])
    if not ok:
        err(401, sessions.REVOKED_MSG.get(why or "", "Accesso scaduto: entra di nuovo"))
    if admin and data.get("role") != "admin":
        err(403, "Solo amministratori")
    return data


PERMESSI = ("auto", "incentivi")


def _province_zona(data: dict) -> list[str]:
    """Zona dell'account: "milano", "ravenna" o "tutte" (oppure province date una per una)."""
    from ..zone import ZONE, tutte
    if isinstance(data.get("provinces"), list) and data["provinces"]:
        return [str(p).upper()[:2] for p in data["provinces"]][:30]
    z = data.get("zona") or "milano"
    return tutte() if z == "tutte" else list(ZONE.get(z, ZONE["milano"])["province"])


def permessi_di(conn, user: dict) -> list[str]:
    if user.get("role") == "admin":
        return list(PERMESSI)
    row = conn.execute("SELECT permessi FROM dealers WHERE id=%s", (user["id"],)).fetchone()
    return list((row or {}).get("permessi") or [])


def richiede(request: Request, permesso: str) -> dict:
    """Accesso con credenziali e con il permesso giusto (gli amministratori hanno tutto)."""
    user = current_user(request)
    if user.get("role") != "admin":
        with connect() as conn:
            if permesso not in permessi_di(conn, user):
                err(403, "Il tuo account non ha accesso a questa sezione")
            if permesso == "auto":
                row = conn.execute("SELECT richiede_abbonamento, abbonamento_stato, (password_hash IS NOT NULL) AS con_password, "
                                   "role FROM dealers WHERE id=%s",
                                   (user["id"],)).fetchone()
                if not abbonamento.puo_vedere(row):
                    err(402, "Per vedere le auto serve l'abbonamento")
    return user


def site_open() -> bool:
    """SITO_APERTO=1: chiunque vede l'elenco e le schede (anteprima). Contatti, Vendi e Ricambi restano solo con accesso."""
    return os.environ.get("SITO_APERTO") == "1"


def viewer(request: Request, permesso: str | None = None) -> dict:
    """Utente con accesso, oppure ospite se il sito è aperto e non c'è un accesso."""
    if not request.headers.get("authorization") and site_open():
        return {"id": 0, "role": "ospite", "sid": None}
    return richiede(request, permesso) if permesso else current_user(request)


def start_session(request: Request, dealer: dict, method: str) -> dict:
    """Login riuscito: apre la sessione personale e restituisce token e dati."""
    with connect() as conn:
        sid = sessions.open_session(conn, dealer["id"], method, request.headers.get("user-agent"))
    return {"token": make_token(dealer["id"], dealer["role"], sid),
            "user": {"id": dealer["id"], "name": dealer["name"], "email": dealer["email"], "role": dealer["role"]}}


async def body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        err(400, "Richiesta non valida")


def jsonify(data, status=200):
    return JSONResponse(json.loads(json.dumps(data, default=str)), status_code=status)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")) or "?"


# ---------------------------------------------------------------------------
# Accesso
# ---------------------------------------------------------------------------
_attempts: dict[str, list[float]] = defaultdict(list)


def _diagnosi_password(email: str, pw: str, u: dict | None) -> str:
    """Perché la password non va, SENZA scriverla: è quella dell'amministratore (riempita dal telefono)?
    Dove differisce da quella preparata su Render? La password salvata corrisponde a quella preparata?"""
    out = []
    adm = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    if adm:
        out.append(f"e_la_password_admin={pw.strip() == adm}")
    try:
        for c in json.loads(os.environ.get("ACCOUNT_CLIENTI") or "[]"):
            if str(c.get("email", "")).strip().lower() == email:
                giusta = str(c.get("password", "")).strip()
                diff = next((i for i, (a, b) in enumerate(zip(pw, giusta)) if a != b), None)
                out.append(f"uguale_a_quella_preparata={pw == giusta} lunghezza_giusta={len(giusta)} "
                           f"prima_differenza_carattere={None if diff is None else diff + 1} "
                           f"tipo_carattere_diverso={'' if diff is None else ('cifra' if pw[diff].isdigit() else 'lettera' if pw[diff].isalpha() else 'simbolo')}")
                if u:
                    out.append(f"salvata_corrisponde_a_preparata={verify_password(giusta, u['password_hash'])}")
    except Exception as e:
        out.append(f"diagnosi_errore={type(e).__name__}")
    return " ".join(out)


async def login(request: Request):
    data = await body(request)
    email = str(data.get("email", "")).strip().lower()[:200]
    ip = client_ip(request)
    now = time.time()
    for k in (email, "ip:" + ip):
        _attempts[k] = [t for t in _attempts[k] if now - t < 900]
    if len(_attempts[email]) >= 8 or len(_attempts["ip:" + ip]) >= 30:
        err(429, "Troppi tentativi, riprova tra 15 minuti")
    def check():
        with connect() as conn:
            u = conn.execute("SELECT * FROM dealers WHERE email=%s AND active AND (accesso_fino IS NULL OR accesso_fino > now())", (email,)).fetchone()
        pw = str(data.get("password", ""))[:200]
        # spazi in più copiati dal messaggio con la password: si prova anche senza
        return u, bool(u) and (verify_password(pw, u["password_hash"]) or
                               (pw != pw.strip() and verify_password(pw.strip(), u["password_hash"])))
    u, ok = await run_in_threadpool(check)
    if not ok:
        pw = str(data.get("password", ""))
        log.warning("ACCESSO_FALLITO email=%s utente=%s lunghezza_password=%d maiuscole=%s spazi=%s %s",
                 email, "trovato" if u else "NON trovato o disattivato", len(pw), any(c.isupper() for c in pw),
                 pw != pw.strip(), _diagnosi_password(email, pw, u))
        _attempts[email].append(now)
        _attempts["ip:" + ip].append(now)
        err(401, "Email o password non corretti")
    return jsonify(await run_in_threadpool(start_session, request, u, "password"))


def me(request: Request):
    user = current_user(request)
    with connect() as conn:
        u = conn.execute("SELECT id, name, email, role, company, provinces, max_purchase, accept_damage, "
                         "preferred_parts, resale_as, lat, lon, (richiede_abbonamento AND password_hash IS NULL AND role <> 'admin') AS richiede_abbonamento, abbonamento_stato, "
                         "abbonamento_fino, (stripe_customer IS NOT NULL) AS ha_pagamento "
                         "FROM dealers WHERE id=%s", (user["id"],)).fetchone()
        c = conn.execute("SELECT * FROM dealer_costs WHERE dealer_id=%s", (user["id"],)).fetchone()
    if not u:
        err(401, "Account non trovato")
    with connect() as conn:
        perm = permessi_di(conn, {"id": user["id"], "role": u.get("role")})
    return jsonify({**u, "permessi": perm, "costs": cards.costs_dict(cards.dealer_costs(c))})


async def update_me(request: Request):
    user = current_user(request)
    data = await body(request)
    with connect() as conn:
        prefs = {}
        if "provinces" in data:
            prefs["provinces"] = [str(p).upper()[:2] for p in data["provinces"]][:20]
        if "max_purchase" in data:
            prefs["max_purchase"] = max(500, min(int(data["max_purchase"]), 200_000))
        if "accept_damage" in data:
            prefs["accept_damage"] = bool(data["accept_damage"])
        if data.get("resale_as") in ("privato", "commerciante"):
            prefs["resale_as"] = data["resale_as"]
        if data.get("preferred_parts") in ("originale", "aftermarket", "usato"):
            prefs["preferred_parts"] = data["preferred_parts"]
        for k, v in prefs.items():
            conn.execute(f"UPDATE dealers SET {k}=%s WHERE id=%s", (v, user["id"]))
        pos = data.get("posizione")
        if isinstance(pos, dict):
            try:
                lat, lon = float(pos["lat"]), float(pos["lon"])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    conn.execute("UPDATE dealers SET lat=%s, lon=%s, pos_at=now() WHERE id=%s",
                                 (round(lat, 3), round(lon, 3), user["id"]))
            except (KeyError, TypeError, ValueError):
                pass
        costs = data.get("costs") or {}
        clean = {}
        for k in cards.COST_FIELDS:
            if k in costs:
                v = costs[k]
                if k == "vat_margin_scheme":
                    clean[k] = bool(v)
                elif k.endswith("_pct"):
                    clean[k] = max(0.0, min(float(v), 0.5))
                else:
                    clean[k] = max(0, min(int(v), 100_000))
        if clean:
            conn.execute("INSERT INTO dealer_costs (dealer_id) VALUES (%s) ON CONFLICT DO NOTHING", (user["id"],))
            for k, v in clean.items():
                conn.execute(f"UPDATE dealer_costs SET {k}=%s WHERE dealer_id=%s", (v, user["id"]))
        conn.commit()
    return me(request)


# ---------------------------------------------------------------------------
# Opportunità
# ---------------------------------------------------------------------------
LIST_SQL = """
SELECT l.*, v.id AS valuation_id, v.private_median, v.dealer_median, v.resale_prudent, v.resale_median,
       v.comparables_used, v.comparable_level, v.n_comparables, v.dispersion, v.liquidity_days,
       v.confidence, v.confidence_reasons, v.parts_cost_low, v.parts_cost_high, v.parts_detail,
       v.discount_vs_private, v.fraud_flags, v.motivation, v.checks, v.engine_version,
       v.created_at AS valued_at, v.resale_prudent_private, v.resale_median_private,
       v.resale_prudent_dealer, v.resale_median_dealer, v.asis_median, v.asis_n,
       (SELECT count(*) FROM listing_opens o WHERE o.listing_id=l.id) AS opens,
       EXISTS (SELECT 1 FROM listing_opens o WHERE o.listing_id=l.id AND o.dealer_id=%(me)s) AS opened_by_me,
       (SELECT array_agg(source_url ORDER BY position) FROM listing_photos p WHERE p.listing_id=l.id) AS photo_urls
FROM listings l
JOIN LATERAL (SELECT * FROM valuations WHERE listing_id=l.id ORDER BY created_at DESC LIMIT 1) v ON true
WHERE l.stage='approfondito' AND l.status='attivo'
  AND l.last_checked_at > now() - interval '24 hours'
"""


def _dealer_context(conn, dealer_id: int):
    d = conn.execute("SELECT * FROM dealers WHERE id=%s", (dealer_id,)).fetchone()
    c = conn.execute("SELECT * FROM dealer_costs WHERE dealer_id=%s", (dealer_id,)).fetchone()
    return d, c


def _build(row, costs, dealer, full=False):
    val_row = {k: row[k] for k in row.keys()}
    val_row["created_at"] = row.get("valued_at")
    return cards.build(row, val_row, list(row.get("photo_urls") or []), costs,
                       dealer.get("preferred_parts") or "aftermarket",
                       opens=row["opens"], opened_by_me=row["opened_by_me"], full=full,
                       resale_as=dealer.get("resale_as") or "privato")


def _visible(row) -> bool:
    return row["opened_by_me"] or row["opens"] < settings.max_dealer_opens


def opportunities(request: Request):
    user = current_user(request)
    q = request.query_params
    want = q.get("status", "opportunita")            # opportunita | da_verificare | tutte
    with connect() as conn:
        dealer, costs = _dealer_context(conn, user["id"])
        provinces = [p for p in (q.get("province") or "").upper().split(",") if p] or list(dealer["provinces"])
        max_price = int(q.get("max_price") or dealer["max_purchase"])
        sql = LIST_SQL + " AND l.price_eur <= %(max_price)s AND (l.province = ANY(%(prov)s) OR l.source='facebook')"
        if not (dealer["accept_damage"] and q.get("damaged", "1") != "0"):
            sql += " AND l.damage_class IN ('nessuno','sconosciuto')"
        if q.get("source"):
            sql += " AND l.source = %(source)s"
        rows = conn.execute(sql + " LIMIT 2000", {"me": user["id"], "max_price": max_price,
                                                 "prov": provinces, "source": q.get("source")}).fetchall()
    items = []
    for r in rows:
        if not _visible(r):
            continue
        card = _build(r, costs, dealer)
        if not card or card["status"] == "scartata":
            continue
        if want != "tutte" and card["status"] != want:
            continue
        items.append(card)
    sort = q.get("sort", "score")
    keyf = {"score": lambda c: -c["score"], "margin": lambda c: -c["net_margin"],
            "recent": lambda c: str(c["first_seen_at"]), "price": lambda c: c["price"]}.get(sort)
    items.sort(key=keyf, reverse=(sort == "recent"))
    return jsonify({"count": len(items), "items": items[:300]})


def opportunity_detail(request: Request):
    user = current_user(request)
    lid = int(request.path_params["id"])
    with connect() as conn:
        dealer, costs = _dealer_context(conn, user["id"])
        row = conn.execute(LIST_SQL + " AND l.id=%(id)s", {"me": user["id"], "id": lid}).fetchone()
        fb = conn.execute("SELECT status, reason, bought_eur, sold_eur, at FROM dealer_feedback "
                          "WHERE listing_id=%s AND dealer_id=%s ORDER BY at DESC LIMIT 1",
                          (lid, user["id"])).fetchone()
    if not row or not _visible(row):
        err(404, "Annuncio non disponibile")
    card = _build(row, costs, dealer, full=True)
    if not card:
        err(404, "Annuncio non valutabile")
    card["my_feedback"] = fb
    return jsonify(card)


def open_listing(request: Request):
    """Registra l'apertura del link e restituisce l'URL originale dell'annuncio."""
    user = current_user(request)
    lid = int(request.path_params["id"])
    with connect() as conn:
        row = conn.execute(
            "SELECT url, (SELECT count(*) FROM listing_opens WHERE listing_id=%s) AS opens, "
            "EXISTS (SELECT 1 FROM listing_opens WHERE listing_id=%s AND dealer_id=%s) AS mine "
            "FROM listings WHERE id=%s AND status='attivo'", (lid, lid, user["id"], lid)).fetchone()
        if not row or (not row["mine"] and row["opens"] >= settings.max_dealer_opens):
            err(404, "Annuncio non più disponibile")
        conn.execute("INSERT INTO listing_opens (listing_id, dealer_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                     (lid, user["id"]))
        conn.commit()
    return jsonify({"url": row["url"]})


async def feedback(request: Request):
    user = current_user(request)
    lid = int(request.path_params["id"])
    data = await body(request)
    if data.get("status") not in FEEDBACK_STATUSES:
        err(400, "Stato non valido")

    def num(k):
        return int(data[k]) if data.get(k) not in (None, "") else None
    with connect() as conn:
        conn.execute("INSERT INTO dealer_feedback (listing_id, dealer_id, status, reason, bought_eur, sold_eur, "
                     "days_to_sell) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (lid, user["id"], data["status"], str(data.get("reason") or "")[:500],
                      num("bought_eur"), num("sold_eur"), num("days_to_sell")))
        conn.commit()
    return jsonify({"ok": True})


def my_activity(request: Request):
    user = current_user(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT ON (f.listing_id) f.listing_id AS id, f.status, f.bought_eur, f.sold_eur, f.at,
                      l.title, l.price_eur, l.url, l.status AS listing_status
               FROM dealer_feedback f JOIN listings l ON l.id=f.listing_id
               WHERE f.dealer_id=%s ORDER BY f.listing_id, f.at DESC""", (user["id"],)).fetchall()
    rows.sort(key=lambda r: str(r["at"]), reverse=True)
    return jsonify({"items": rows})


def notifications(request: Request):
    """Nuove opportunità dall'ultima volta che il commerciante ha guardato."""
    user = current_user(request)
    with connect() as conn:
        dealer, costs = _dealer_context(conn, user["id"])
        rows = conn.execute(LIST_SQL + " AND l.deep_at > %(since)s AND l.price_eur <= %(max)s "
                            "AND (l.province = ANY(%(prov)s) OR l.source='facebook') LIMIT 500",
                            {"me": user["id"], "since": dealer["last_seen_opps_at"],
                             "max": dealer["max_purchase"], "prov": list(dealer["provinces"])}).fetchall()
    items = [c for c in (_build(r, costs, dealer) for r in rows if _visible(r))
             if c and c["status"] == "opportunita"]
    return jsonify({"new_count": len(items), "items": items[:20]})


def notifications_seen(request: Request):
    user = current_user(request)
    with connect() as conn:
        conn.execute("UPDATE dealers SET last_seen_opps_at=now() WHERE id=%s", (user["id"],))
        conn.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Amministrazione
# ---------------------------------------------------------------------------
def admin_overview(request: Request):
    current_user(request, admin=True)
    with connect() as conn:
        conn.execute("SET LOCAL statement_timeout = '20s'")        # con tanti annunci: meglio un errore che una pagina ferma
        out = {
            "listings_by_source": conn.execute(
                "SELECT source, status, count(*) AS n FROM listings GROUP BY source, status ORDER BY source").fetchall(),
            "new_last_24h": conn.execute(
                "SELECT source, count(*) AS n FROM listings WHERE first_seen_at > now() - interval '24 hours' "
                "GROUP BY source").fetchall(),
            "stages": conn.execute(
                "SELECT stage, stage_reason, count(*) AS n FROM listings WHERE first_seen_at > now() - interval '7 days' "
                "GROUP BY stage, stage_reason ORDER BY n DESC").fetchall(),
            "jobs": conn.execute(
                "SELECT DISTINCT ON (job) job, started_at, finished_at, ok, stats FROM job_runs "
                "ORDER BY job, started_at DESC").fetchall(),
            "ai_usage_today": conn.execute(
                "SELECT task, model, count(*) AS calls, sum(input_tokens) AS input_tokens, "
                "sum(output_tokens) AS output_tokens, sum(web_searches) AS web_searches FROM ai_usage "
                "WHERE at > date_trunc('day', now()) GROUP BY task, model").fetchall(),
            "ai_usage_30d": conn.execute(
                "SELECT model, sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens, "
                "sum(web_searches) AS web_searches FROM ai_usage WHERE at > now() - interval '30 days' "
                "GROUP BY model").fetchall(),
            "quality": conn.execute("SELECT * FROM model_runs ORDER BY at DESC LIMIT 8").fetchall(),
            "feedback": conn.execute(
                "SELECT status, count(*) AS n FROM dealer_feedback GROUP BY status").fetchall(),
            "servizi_oggi": conn.execute(
                "SELECT service, count(*) AS n, count(*) FILTER (WHERE ok) AS ok FROM service_usage "
                "WHERE at > date_trunc('day', now()) GROUP BY service").fetchall(),
            "richieste_accesso": conn.execute(
                "SELECT count(*) AS n FROM access_requests WHERE NOT handled").fetchone()["n"],
        }
        out["allarmi"] = alarms(conn)
    return jsonify(out)


DB_DISK_GB = float(os.environ.get("DB_DISK_GB", "5"))


def alarms(conn) -> list[str]:
    """Cose che non vanno: cicli fermi, Facebook che non raccoglie, disco quasi pieno, nessun affare."""
    out = []
    size = conn.execute("SELECT pg_database_size(current_database()) AS b").fetchone()["b"]
    used = size / (DB_DISK_GB * 1024 ** 3)
    if used > 0.8:
        out.append(f"Database pieno al {used:.0%}: aumenta il disco su Render o pulisci i dati vecchi")
    last = conn.execute("SELECT max(started_at) AS t FROM job_runs WHERE job LIKE 'ciclo%%' OR job LIKE 'collect:%%'"
                        ).fetchone()["t"]
    if not last or conn.execute("SELECT %s < now() - interval '7 hours' AS old", (last,)).fetchone()["old"]:
        out.append("Nessun ciclo di raccolta nelle ultime 7 ore: controlla i cron su Render")
    fb = conn.execute("SELECT count(*) AS n FROM listings WHERE source='facebook' "
                      "AND first_seen_at > now() - interval '36 hours'").fetchone()["n"]
    if fb == 0:
        out.append("Facebook: nessun annuncio nuovo nelle ultime 36 ore (Bright Data, credito o chiave)")
    sb = conn.execute("SELECT count(*) AS n FROM listings WHERE source='subito' "
                      "AND first_seen_at > now() - interval '12 hours'").fetchone()["n"]
    if sb == 0:
        out.append("Subito: nessun annuncio nuovo nelle ultime 12 ore")
    fails = conn.execute("SELECT job, count(*) AS n FROM job_runs WHERE ok = false AND started_at > now() - interval '24 hours' "
                         "GROUP BY job").fetchall()
    for f in fails:
        out.append(f"Lavoro '{f['job']}' fallito {f['n']} volte nelle ultime 24 ore")
    affari_n = conn.execute("SELECT count(*) AS n FROM listings WHERE stage='approfondito' AND status='attivo' "
                            "AND stage_reason IN ('opportunita','da_verificare','incidentata') "
                            "AND last_checked_at > now() - interval '48 hours'").fetchone()["n"]
    if affari_n < 10:
        out.append(f"Solo {affari_n} auto visibili ai commercianti: controlla verifica e analisi")
    return out


def admin_dealers(request: Request):
    current_user(request, admin=True)
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.id, d.name, d.email, d.company, d.phone, d.role, d.active, d.provinces, d.created_at, d.permessi,
                      (SELECT count(*) FROM listing_opens o WHERE o.dealer_id=d.id) AS opens,
                      (SELECT count(*) FROM dealer_feedback f WHERE f.dealer_id=d.id AND f.status='comprata') AS bought,
                      (SELECT count(*) FROM dealer_sessions s WHERE s.dealer_id=d.id AND s.revoked_at IS NULL) AS sessions,
                      (SELECT max(last_seen_at) FROM dealer_sessions s WHERE s.dealer_id=d.id) AS last_seen,
                      concat_ws(' ', CASE WHEN d.google_sub IS NOT NULL THEN 'Google' END,
                                CASE WHEN d.apple_sub IS NOT NULL THEN 'Apple' END,
                                CASE WHEN d.password_hash IS NOT NULL THEN 'Email' END) AS methods
               FROM dealers d ORDER BY d.created_at DESC""").fetchall()
    return jsonify({"items": rows})


async def admin_create_dealer(request: Request):
    current_user(request, admin=True)
    data = await body(request)
    email = str(data.get("email", "")).strip().lower()
    pwd = str(data.get("password", "")).strip()
    if "@" not in email or len(pwd) < 8:
        err(400, "Email valida e password di almeno 8 caratteri")
    role = "admin" if data.get("role") == "admin" else "commerciante"
    with connect() as conn:
        if conn.execute("SELECT 1 FROM dealers WHERE email=%s", (email,)).fetchone():
            err(409, "Email già registrata")
        row = conn.execute(
            "INSERT INTO dealers (name, email, company, phone, password_hash, role, provinces, permessi) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (str(data.get("name") or email)[:120], email, data.get("company"), data.get("phone"),
             hash_password(pwd), role, _province_zona(data),
             [p for p in (data.get("permessi") or ["auto"]) if p in PERMESSI])).fetchone()
        conn.execute("INSERT INTO dealer_costs (dealer_id) VALUES (%s)", (row["id"],))
        conn.commit()
    return jsonify({"id": row["id"]}, 201)


def admin_link(request: Request):
    """Link per entrare senza password (da mandare al cliente su WhatsApp)."""
    current_user(request, admin=True)
    did = int(request.path_params["id"])
    with connect() as conn:
        u = conn.execute("SELECT id, password_hash, active FROM dealers WHERE id=%s", (did,)).fetchone()
    if not u:
        err(404, "Account non trovato")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "scovo.onrender.com"
    from .auth import LINK_GIORNI
    return jsonify({"url": f"https://{host}/#/accesso/{make_link_code(did, u['password_hash'])}", "giorni": LINK_GIORNI,
                    "attivo": u["active"]})


async def admin_update_dealer(request: Request):
    current_user(request, admin=True)
    did = int(request.path_params["id"])
    data = await body(request)
    with connect() as conn:
        if data.get("zona") or isinstance(data.get("provinces"), list):
            conn.execute("UPDATE dealers SET provinces=%s WHERE id=%s", (_province_zona(data), did))
        if isinstance(data.get("permessi"), list):
            conn.execute("UPDATE dealers SET permessi=%s WHERE id=%s",
                         ([p for p in data["permessi"] if p in PERMESSI], did))
        if "active" in data:
            conn.execute("UPDATE dealers SET active=%s WHERE id=%s", (bool(data["active"]), did))
            if not data["active"]:
                sessions.revoke_all(conn, did, "account_disattivato")
        if data.get("password"):
            if len(data["password"]) < 8:
                err(400, "Password troppo corta")
            conn.execute("UPDATE dealers SET password_hash=%s WHERE id=%s", (hash_password(data["password"]), did))
            sessions.revoke_all(conn, did, "password_cambiata")
        conn.commit()
    return jsonify({"ok": True})


def health(request: Request):
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Servizi: Vendi (targa+foto -> prezzo e annuncio) e Ricambi (targa+pezzi -> offerte)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Entra con Google / Apple
# ---------------------------------------------------------------------------
def auth_metodi(request: Request):
    from . import voce
    return jsonify({**social.enabled(), "aperto": site_open(), "voce": voce.info(), "abbonamento": abbonamento.info()})


async def abbonamento_checkout(request: Request):
    user = current_user(request)
    if not abbonamento.attivo():
        err(503, "Pagamenti non ancora attivi")
    with connect() as conn:
        d = conn.execute("SELECT id, email, stripe_customer FROM dealers WHERE id=%s", (user["id"],)).fetchone()
    try:
        url = await run_in_threadpool(abbonamento.checkout, d, social.base_url(request))
    except RuntimeError as e:
        err(502, str(e))
    return jsonify({"url": url})


async def abbonamento_portale(request: Request):
    user = current_user(request)
    with connect() as conn:
        d = conn.execute("SELECT stripe_customer FROM dealers WHERE id=%s", (user["id"],)).fetchone()
    if not d or not d["stripe_customer"]:
        err(404, "Nessun abbonamento da gestire")
    try:
        url = await run_in_threadpool(abbonamento.portale, d["stripe_customer"], social.base_url(request))
    except RuntimeError as e:
        err(502, str(e))
    return jsonify({"url": url})


async def stripe_webhook(request: Request):
    payload = await request.body()
    if not abbonamento.firma_valida(payload, request.headers.get("stripe-signature")):
        err(400, "firma non valida")
    evento = json.loads(payload)

    def go():
        with connect() as conn:
            return abbonamento.applica(conn, evento)
    esito = await run_in_threadpool(go)
    log.info("STRIPE %s -> %s", abbonamento.dump(evento), esito)
    return jsonify({"ok": True})


def auth_start(request: Request):
    provider = request.path_params["provider"]
    if provider not in ("google", "apple") or not social.enabled().get(provider):
        return RedirectResponse("/#/entra/non-attivo", status_code=302)
    state = social.make_state(provider)
    resp = RedirectResponse(social.auth_url(request, provider, state), status_code=302)
    # Apple rimanda con un POST da un altro sito: il cookie deve essere SameSite=None
    resp.set_cookie(social.STATE_COOKIE, state, max_age=600, httponly=True, secure=True,
                    samesite="none" if provider == "apple" else "lax", path="/auth/")
    return resp


async def auth_callback(request: Request):
    provider = request.path_params["provider"]
    if provider not in ("google", "apple") or not social.enabled().get(provider):
        return RedirectResponse("/#/entra/non-attivo", status_code=302)
    if request.method == "POST":
        from urllib.parse import parse_qs
        raw = (await request.body())[:20000].decode(errors="ignore")
        params = {k: v[0] for k, v in parse_qs(raw).items()}
    else:
        params = dict(request.query_params)
    if params.get("error"):
        return RedirectResponse("/#/entra/annullato", status_code=302)
    if not social.check_state(params.get("state"), request.cookies.get(social.STATE_COOKIE), provider):
        return RedirectResponse("/#/entra/scaduto", status_code=302)
    name_hint = None
    if params.get("user"):                       # Apple manda il nome solo la prima volta
        try:
            n = json.loads(params["user"]).get("name") or {}
            name_hint = " ".join(filter(None, [n.get("firstName"), n.get("lastName")])) or None
        except ValueError:
            pass

    def work():
        info = social.exchange(request, provider, params.get("code", ""))
        with connect() as conn:
            return social.find_or_create(conn, provider, info, name_hint)
    try:
        dealer, esito = await run_in_threadpool(work)
    except Exception as e:
        log.warning("accesso %s non riuscito: %s", provider, str(e)[:200])
        return RedirectResponse("/#/entra/errore", status_code=302)
    if esito != "ok":
        resp = RedirectResponse(f"/#/entra/{esito.replace('_', '-')}", status_code=302)
    else:
        resp = RedirectResponse(f"/#/accesso/{social.one_time_code(dealer, provider)}", status_code=302)
    resp.delete_cookie(social.STATE_COOKIE, path="/auth/")
    return resp


async def auth_scambio(request: Request):
    data = await body(request)
    code = str(data.get("code", ""))[:200]
    if code.startswith("L"):
        # link di accesso preparato dall'amministratore: si entra senza scrivere la password
        def by_link():
            p = parse_link_code(code)
            if not p:
                return None
            with connect() as conn:
                u = conn.execute("SELECT * FROM dealers WHERE id=%s AND active", (p[0],)).fetchone()
            return u if u and check_link_code(code, u["password_hash"]) else None
        u = await run_in_threadpool(by_link)
        if not u:
            err(401, "Link scaduto o non valido: chiedine uno nuovo")
        return jsonify(await run_in_threadpool(start_session, request, u, "link"))
    d = social.redeem(code)
    if not d:
        err(401, "Accesso scaduto: riprova")
    return jsonify(await run_in_threadpool(start_session, request, d, d.get("method", "google")))


async def esci(request: Request):
    data = current_user(request)
    with connect() as conn:
        sessions.revoke(conn, data["sid"], "uscita")
    return jsonify({"ok": True})


SERVIZI_LIMITE_GIORNO = int(os.environ.get("SERVIZI_LIMITE_GIORNO", "15"))     # per commerciante, ogni servizio
SERVIZI_LIMITE_TOTALE = int(os.environ.get("SERVIZI_LIMITE_TOTALE", "150"))    # tutto il sito, al giorno


def _service_gate(conn, user: dict, service: str) -> None:
    """Tetti giornalieri: ogni ricerca targa e ogni ricerca ricambi ha un costo."""
    tot = conn.execute("SELECT count(*) AS n FROM service_usage WHERE at > date_trunc('day', now())").fetchone()["n"]
    if tot >= SERVIZI_LIMITE_TOTALE:
        err(503, "Servizio molto richiesto oggi: riprova domani")
    if user.get("role") != "admin":
        mine = conn.execute("SELECT count(*) AS n FROM service_usage WHERE dealer_id=%s AND service=%s "
                            "AND at > date_trunc('day', now())", (user["id"], service)).fetchone()["n"]
        if mine >= SERVIZI_LIMITE_GIORNO:
            err(429, f"Hai usato {service.capitalize()} {mine} volte oggi: il limite è {SERVIZI_LIMITE_GIORNO}. Riprova domani")


def _service_log(conn, user: dict, service: str, ok: bool) -> None:
    conn.execute("INSERT INTO service_usage (dealer_id, service, ok) VALUES (%s,%s,%s)", (user["id"], service, ok))
    conn.commit()


async def servizio_vendi(request: Request):
    user = current_user(request)
    data = await body(request)
    from ..servizi import vendi
    try:
        km = int(str(data.get("km") or "").replace(".", "")) if data.get("km") else None
    except ValueError:
        err(400, "Km non validi")
    if km is not None and not (0 <= km <= 1_000_000):
        err(400, "Km non validi")
    foto_in = [f for f in (data.get("foto") or []) if isinstance(f, str)][:6]
    cambio = data.get("cambio") if data.get("cambio") in ("manuale", "automatico") else None

    def work():
        from ..ai import plate as plates
        if not plates.valid_plate(str(data.get("targa", ""))):
            return {"ok": False, "errore": "Targa non valida: scrivila come AB123CD"}
        with connect() as conn:
            _service_gate(conn, user, "vendi")
            out = vendi.run(conn, str(data.get("targa", ""))[:12], km, foto_in,
                            str(data.get("note") or "")[:500] or None, cambio)
            _service_log(conn, user, "vendi", bool(out.get("ok")))
            return out
    out = await run_in_threadpool(work)
    return jsonify(out, 200 if out.get("ok") else 422)


async def servizio_ricambi(request: Request):
    user = current_user(request)
    data = await body(request)
    from ..servizi import ricambi
    pezzi = data.get("pezzi") or []
    if isinstance(pezzi, str):
        pezzi = [p for p in pezzi.replace(";", ",").split(",")]

    def work():
        from ..ai import plate as plates
        if not plates.valid_plate(str(data.get("targa", ""))):
            return {"ok": False, "errore": "Targa non valida: scrivila come AB123CD"}
        with connect() as conn:
            _service_gate(conn, user, "ricambi")
            out = ricambi.run(conn, str(data.get("targa", ""))[:12], [str(p)[:80] for p in pezzi][:8])
            _service_log(conn, user, "ricambi", bool(out.get("ok")))
            return out
    out = await run_in_threadpool(work)
    return jsonify(out, 200 if out.get("ok") else 422)


ASSISTENTE_LIMITE_GIORNO = int(os.environ.get("ASSISTENTE_LIMITE_GIORNO", "80"))
_guest_questions: dict[str, list[float]] = defaultdict(list)


async def assistente(request: Request):
    """Assistente a voce: domanda in linguaggio naturale, risposta + auto trovate."""
    user = viewer(request)
    data = await body(request)
    msgs = [m for m in (data.get("messages") or []) if isinstance(m, dict)][-10:]
    if not msgs or not str(msgs[-1].get("content", "")).strip():
        err(400, "Fai una domanda")
    if user.get("role") == "ospite":
        ip, now = client_ip(request), time.time()
        _guest_questions[ip] = [t for t in _guest_questions[ip] if now - t < 86400]
        if len(_guest_questions[ip]) >= 15:
            err(429, "Hai fatto molte domande oggi: entra con il tuo account per continuare")
        _guest_questions[ip].append(now)
    from . import assistente as bot

    def work():
        with connect() as conn:
            if user.get("role") != "ospite":
                n = conn.execute("SELECT count(*) AS n FROM service_usage WHERE dealer_id=%s AND service='assistente' "
                                 "AND at > date_trunc('day', now())", (user["id"],)).fetchone()["n"]
                if n >= ASSISTENTE_LIMITE_GIORNO:
                    err(429, "Hai raggiunto le domande di oggi: riprova domani")
            out = bot.answer(conn, user, msgs, _service_gate, _service_log)
            if user.get("role") != "ospite":
                _service_log(conn, user, "assistente", True)
            return out
    return jsonify(await run_in_threadpool(work))


# Voce naturale: le chiamate costano, quindi un tetto al giorno per persona (o per indirizzo, per gli ospiti)
VOCE_LIMITE = {"tts": int(os.environ.get("VOCE_LIMITE_GIORNO", "300")), "stt": int(os.environ.get("TRASCRIZIONI_LIMITE_GIORNO", "150"))}
_voce_uso: dict[str, list[float]] = defaultdict(list)


def _voce_gate(request: Request, user: dict, kind: str) -> None:
    ospite = user.get("role") == "ospite"
    k = f"{kind}:{'ip:' + client_ip(request) if ospite else user['id']}"
    now = time.time()
    _voce_uso[k] = [t for t in _voce_uso[k] if now - t < 86400]
    lim = VOCE_LIMITE[kind] // (5 if ospite else 1)
    if len(_voce_uso[k]) >= lim:
        err(429, "Hai usato molto la voce oggi: continuo a risponderti per iscritto")
    _voce_uso[k].append(now)


async def voce_tts(request: Request):
    """Testo -> audio MP3 con voce naturale."""
    from . import voce
    user = viewer(request)
    if not voce.provider():
        err(404, "Voce naturale non attiva")
    data = await body(request)
    testo = str(data.get("testo") or "").strip()
    if not testo:
        err(400, "Niente da leggere")
    _voce_gate(request, user, "tts")
    try:
        audio = await run_in_threadpool(voce.tts, testo)
    except Exception as e:
        log.warning("voce non riuscita: %s", str(e)[:200])
        err(502, "La voce non risponde adesso")
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})


async def voce_stt(request: Request):
    """Registrazione dal microfono -> testo (funziona anche dove il telefono non riconosce la voce)."""
    from . import voce
    user = viewer(request)
    if not voce.provider("stt"):
        err(404, "Trascrizione non attiva")
    audio = await request.body()
    if not audio or len(audio) < 800:
        return jsonify({"testo": ""})
    if len(audio) > voce.MAX_AUDIO:
        err(413, "Registrazione troppo lunga")
    _voce_gate(request, user, "stt")
    try:
        testo = await run_in_threadpool(voce.stt, audio, request.headers.get("content-type", ""))
    except Exception as e:
        log.warning("trascrizione non riuscita: %s", str(e)[:200])
        err(502, "Non riesco a capire la registrazione adesso: riprova o scrivi")
    return jsonify({"testo": testo})


# ---------------------------------------------------------------------------
# Sito Scovo: affari, scheda, contatto, foto
# ---------------------------------------------------------------------------
def _vetrina() -> dict:
    try:
        return json.loads((Path(__file__).resolve().parents[2] / "data" / "vetrina.json").read_text())
    except Exception:
        return {}


_tel_cache: dict[int, bool] = {}


def _meno_subito_senza_telefono(items: list[dict]) -> list[dict]:
    """Annunci Subito senza numero di telefono nel testo: si nasconde la metà peggiore (meno guadagno).
    TOGLI_SENZA_TELEFONO = percentuale da togliere (predefinita 50)."""
    quota = int(os.environ.get("TOGLI_SENZA_TELEFONO", "50"))
    if quota <= 0:
        return items
    sub_ids = [it["id"] for it in items if it.get("fonte") == "Subito"]
    mancano = [i for i in sub_ids if i not in _tel_cache]
    if mancano:
        with connect() as conn:
            for r in conn.execute("SELECT id, description FROM listings WHERE id = ANY(%s)", (mancano,)).fetchall():
                t = r.get("description") or ""
                _tel_cache[r["id"]] = bool(scovo.RE_MOBILE.search(t) or scovo.RE_LANDLINE.search(t))
    senza = [it for it in items if it.get("fonte") == "Subito" and not _tel_cache.get(it["id"], False)]
    senza.sort(key=lambda it: scovo.guadagno_minimo(it))
    via = {it["id"] for it in senza[: len(senza) * quota // 100]}
    return [it for it in items if it["id"] not in via]


_tel_da_controllare: set = set()     # 25/10: auto tolte solo per il numero mai controllato (vedi telefoni_candidate)


def _solo_con_telefono(items: list[dict]) -> list[dict]:
    """TELEFONO_OBBLIGATORIO=1: sul sito solo auto che si possono chiamare. AutoScout24 mostra sempre il numero
    nell'annuncio; Subito e Facebook solo se il numero è scritto nel testo dell'annuncio."""
    if os.environ.get("TELEFONO_OBBLIGATORIO", "1") != "1":
        return items
    ids = [it["id"] for it in items if it.get("fonte") != "AutoScout24"]
    mancano = [i for i in ids if i not in _tel_cache]
    for k in range(0, len(mancano), 1000):
        with connect() as conn:
            for r in conn.execute("SELECT id, title, description, telefono_ok FROM listings WHERE id = ANY(%s)",
                                  (mancano[k:k + 1000],)).fetchall():
                t = f"{r.get('title') or ''} {r.get('description') or ''}"
                if r.get("telefono_ok") or scovo.RE_MOBILE.search(t) or scovo.RE_LANDLINE.search(t):
                    _tel_cache[r["id"]] = True     # solo i sì restano in memoria: un "no" si ricontrolla (bottone Chiama)
    # Subito senza numero (si contattano dalla chat dell'annuncio): si tiene la metà con più guadagno
    # (META_SENZA_NUMERO, 0.5), separatamente per ogni sezione (affari, incidentate, economiche...)
    # dal 16/10: Subito e AutoScout24 SOLO con il numero (META_SENZA_NUMERO=0); senza numero solo Facebook
    quota = float(os.environ.get("META_SENZA_NUMERO", "0"))
    meta: set = set()
    gruppi: dict = defaultdict(list)
    for it in items:
        if it.get("fonte") == "Subito" and not _tel_cache.get(it["id"], False):
            sez = "eco" if it.get("economica") else "inc" if it.get("incidentata") else "affari"
            gruppi[(sez, it.get("area") or "milano")].append(it)
    for g in gruppi.values():
        g.sort(key=lambda it: -scovo.guadagno_minimo(it))
        meta.update(it["id"] for it in g[:round(len(g) * quota)])
    # Facebook: si tengono tutte (si contatta il venditore in chat su Marketplace), FB_SENZA_NUMERO=1
    fb_tutte = os.environ.get("FB_SENZA_NUMERO", "1") == "1"
    # sezione economiche (non controllate a mano): solo con il numero, ECO_TELEFONO=1
    eco_tel = os.environ.get("ECO_TELEFONO", "1") == "1"
    # AutoScout24: il numero si legge dalla pagina dell'annuncio (agente di verifica, ogni 10 minuti): senza, fuori
    as_ids = [it["id"] for it in items if it.get("fonte") == "AutoScout24"]
    as_tel: dict = {}
    for k in range(0, len(as_ids), 1000):
        with connect() as conn:
            for r in conn.execute("SELECT id, telefono_ok FROM listings WHERE id = ANY(%s)", (as_ids[k:k + 1000],)).fetchall():
                as_tel[r["id"]] = r["telefono_ok"]
    as_rigido = os.environ.get("AS_SOLO_CON_NUMERO", "1") == "1"
    tenute = [it for it in items if (it.get("fonte") == "AutoScout24" and (as_tel.get(it["id"]) is True or not as_rigido))
              or (it.get("fonte") != "AutoScout24" and _tel_cache.get(it["id"], False))
              or (it.get("fonte") != "AutoScout24" and not (eco_tel and it.get("economica")) and (
                  it["id"] in meta or (fb_tutte and it.get("fonte") == "Facebook Marketplace")))]
    tenute = _solo_facebook_recenti(tenute)
    # 17/10 17:08: gli annunci Facebook non si possono verificare senza account e molti risultano "non esiste più"
    # (prova del cliente): fuori dal sito finché non c'è un controllo affidabile (FB_SITO=1 per rimetterli)
    if os.environ.get("FB_SITO", "0") != "1":
        tenute = [it for it in tenute if it.get("fonte") != "Facebook Marketplace"]
    # le senza numero si contano DOPO aver tolto Facebook: al massimo il 20% del sito vero
    tenute += _senza_numero_super(items, tenute)
    tenute += _utilitarie_subito_senza_numero(items, tenute)
    tenute += _senza_numero_margini_enormi(items, tenute)
    tenute_ids = {it["id"] for it in tenute}
    _tel_da_controllare.update(it["id"] for it in items if it["id"] not in tenute_ids
                               and it.get("fonte") in ("AutoScout24", "Subito"))
    logging.getLogger("scovo").warning("TELEFONO: %d auto senza numero tolte, %d restano (%d Subito senza numero tenute)",
                                       len(items) - len(tenute), len(tenute), len(meta))
    return tenute


def _solo_facebook_recenti(items: list[dict]) -> list[dict]:
    """Facebook non si può riaprire annuncio per annuncio (niente account): sul sito solo gli annunci rivisti dalla
    raccolta di Facebook nelle ultime FB_VISTO_ORE ore (36: la raccolta gira 4 volte al giorno). Se un annuncio non
    compare più nella raccolta, probabilmente è stato venduto o tolto."""
    ore = int(os.environ.get("FB_VISTO_ORE", "0"))   # 0 = spento: la raccolta FB non aggiorna last_seen (tolte tutte il 17/10)
    fb = [it["id"] for it in items if it.get("fonte") == "Facebook Marketplace"]
    if not fb or ore <= 0:
        return items
    try:
        with connect() as conn:
            ok = {r["id"] for r in conn.execute(
                "SELECT id FROM listings WHERE id = ANY(%s) AND status='attivo' AND last_seen_at > now() - make_interval(hours => %s)",
                (fb, ore)).fetchall()}
    except Exception as e:
        logging.getLogger("scovo").warning("FB recenti non controllati: %s", str(e)[:200])
        return items
    via = set(fb) - ok
    if via:
        logging.getLogger("scovo").warning("FACEBOOK: %d annunci non rivisti nelle ultime %d ore tolti, %d restano", len(via), ore, len(ok))
    return [it for it in items if it["id"] not in via]


def _utilitarie_subito_senza_numero(items: list[dict], tenute: list[dict]) -> list[dict]:
    """17/10 15:07, cliente: utilitarie di Subito fino a 7.000 € anche senza numero (si contattano dalla chat di Subito),
    ma SOLO LE MIGLIORI: margine ≥ soglia piena della fascia, almeno 5 confronti, al massimo UTIL_SENZA_NUMERO_QUOTA
    (20%) delle auto sul sito, dalla più conveniente."""
    quota = float(os.environ.get("UTIL_SENZA_NUMERO_QUOTA", "0.2"))   # 17/10 18:22: massimo 20% del sito senza numero   # 17/10 17:09: almeno 100 utilitarie in più
    if quota <= 0:
        return []
    pmax = int(os.environ.get("UTIL_SENZA_NUMERO_MAX", "10000"))   # 17/10 17:40: fino a 10.000 €
    min_conf = int(os.environ.get("UTIL_SENZA_NUMERO_CONFRONTI", "5"))
    m_euro = int(os.environ.get("UTIL_SENZA_NUMERO_MARGINE", "1000"))   # 17/10 17:40: 1.000 € fino a 7.000 €, 1.500 € sopra
    dentro = {it["id"] for it in tenute}
    cand = []
    for it in items:
        pv = it.get("pv") or {}
        if it["id"] in dentro or it.get("economica") or it.get("fonte") not in ("Subito", "AutoScout24") or it["prezzo"] > pmax:
            continue
        if pv.get("margine") is None or (pv.get("n") or 0) < min_conf or pv["margine"] < (max(m_euro, .15 * it["prezzo"]) if it["prezzo"] <= 7000 else 1500):
            continue
        # che non sembri una truffa: prezzo non sotto il 55% del mercato, km e anno sensati, almeno 3 foto
        if pv.get("M") and it["prezzo"] < .5 * pv["M"]:
            continue
        if not it.get("km") or it["km"] < 30000 or not it.get("anno"):
            continue
        if int(it.get("n_foto") or 0) < 3:
            continue
        cand.append(it)
    cand.sort(key=lambda it: -it["pv"]["margine"])
    massimo = min(int(len(tenute) * quota / (1 - quota)), int(os.environ.get("UTIL_SENZA_NUMERO_N", "200")))   # le migliori 200
    ok = cand[:massimo]
    for it in ok:
        it["senza_numero"] = True
    logging.getLogger("scovo").warning("UTIL_SENZA_NUMERO: candidate=%d, ammesse=%d (massimo %d)", len(cand), len(ok), massimo)
    return ok


def _senza_numero_margini_enormi(items: list[dict], tenute: list[dict]) -> list[dict]:
    """23/10 10:04, cliente: oltre al 20%, al massimo SENZA_NUMERO_EXTRA (50) auto senza numero in più, SOLO quelle
    con i margini più alti (margine ≥ SENZA_NUMERO_EXTRA_MARGINE e ≥ 2 × soglia della fascia, almeno 8 confronti).
    Restano i controlli anti-truffa (prezzo non sotto il 55% del mercato, km e anno sensati, almeno 3 foto) e Jev dopo."""
    extra = int(os.environ.get("SENZA_NUMERO_EXTRA", "0"))
    if extra <= 0:
        return []
    m_min = int(os.environ.get("SENZA_NUMERO_EXTRA_MARGINE", "2500"))
    fattore = float(os.environ.get("SENZA_NUMERO_EXTRA_FATTORE", "2"))
    min_conf = int(os.environ.get("SENZA_NUMERO_EXTRA_CONFRONTI", "8"))
    dentro = {it["id"] for it in tenute}
    cand = []
    for it in items:
        pv = it.get("pv") or {}
        if it["id"] in dentro or it.get("fonte") not in ("Subito", "AutoScout24") or pv.get("margine") is None:
            continue
        if pv.get("categoria") not in scovo.CATEGORIE_SITO:
            continue
        if pv["margine"] < max(m_min, fattore * scovo.soglia(it["prezzo"])) or (pv.get("n") or 0) < min_conf:
            continue
        if pv.get("M") and it["prezzo"] < .55 * pv["M"]:
            continue
        if not it.get("km") or it["km"] < 20000 or not it.get("anno") or int(it.get("n_foto") or 0) < 3:
            continue
        cand.append(it)
    cand.sort(key=lambda it: -it["pv"]["margine"])
    ok = cand[:extra]
    for it in ok:
        it["senza_numero"] = True
    logging.getLogger("scovo").warning("SENZA_NUMERO_EXTRA: candidate=%d, ammesse=%d (massimo %d, margine minimo %d)",
                                       len(cand), len(ok), extra, m_min)
    return ok


# Auto SENZA numero ammesse (richiesta del 17/10): al massimo SENZA_NUMERO_QUOTA (20%) delle auto sul sito, solo se
# "super valide" (sopra la soglia piena, margine ≥ SENZA_NUMERO_FATTORE × soglia, almeno SENZA_NUMERO_CONFRONTI
# confronti) e con la TARGA VISIBILE nelle foto: targa coperta + niente numero = possibile truffa, fuori.
_targhe_da_controllare: list = []


def _senza_numero_super(items: list[dict], tenute: list[dict]) -> list[dict]:
    quota = float(os.environ.get("SENZA_NUMERO_QUOTA", "0.2"))
    if quota <= 0:
        return []
    fattore = float(os.environ.get("SENZA_NUMERO_FATTORE", "1.5"))
    min_conf = int(os.environ.get("SENZA_NUMERO_CONFRONTI", "5"))
    dentro = {it["id"] for it in tenute}
    cand = []
    for it in items:
        pv = it.get("pv") or {}
        if it["id"] in dentro or it.get("economica") or it.get("fonte") not in ("Subito", "AutoScout24"):
            continue
        if pv.get("categoria") not in scovo.CATEGORIE_SITO or pv.get("margine") is None:
            continue
        if pv["margine"] < fattore * scovo.soglia(it["prezzo"]) or (pv.get("n") or 0) < min_conf:
            continue
        cand.append(it)
    cand.sort(key=lambda it: -(it["pv"]["margine"]))
    _targhe_da_controllare[:] = [it["id"] for it in cand]
    if not cand:
        return []
    from ..pipeline import tempo_reale as tr
    try:
        with connect() as conn:
            esiti = tr.esiti_targa(conn, [it["id"] for it in cand])
    except Exception as e:
        logging.getLogger("scovo").warning("controllo targa non letto: %s", str(e)[:200])
        return []
    massimo = int(len(tenute) * quota / (1 - quota))          # così sono al massimo il 20% del totale
    ok = [it for it in cand if esiti.get(it["id"]) == "visibile"][:massimo]
    for it in ok:
        it["senza_numero"] = True
    from collections import Counter as _C
    logging.getLogger("scovo").warning("SENZA_NUMERO: candidate super valide=%d, ammesse=%d (massimo %d), targhe: %s",
                                       len(cand), len(ok), massimo, dict(_C(esiti.get(it["id"], "da_controllare") for it in cand)))
    return ok


def _regole_finali(items: list[dict]) -> list[dict]:
    """Regole fisse del sito: mai prima del 2007 (ANNO_MINIMO), mai oltre 180.000 km, e in ogni sezione e zona
    al massimo DIESEL_MAX (30%) di diesel: si tengono i diesel con più guadagno, il resto benzina/GPL/metano/ibride."""
    anno_min = int(os.environ.get("ANNO_MINIMO_SITO", "2007"))
    items = [it for it in items if not (it.get("anno") and it["anno"] < anno_min) and not scovo.troppi_km(it.get("km"))]
    # 24/10 sera (cliente): niente auto oltre 150.000 km; sopra i 2.000 € almeno 1.500 € di margine
    km_max = int(os.environ.get("KM_MASSIMO_SITO", "150000"))
    p_lim, m_min = int(os.environ.get("SITO_PREZZO_LIMITE", "2000")), int(os.environ.get("SITO_MARGINE_MIN_SOPRA", "1500"))

    def _marg(it):
        m = (it.get("pv") or {}).get("margine")
        if m is None:
            try:
                m = scovo.guadagno_minimo(it)
            except Exception:
                m = None
        return m

    # 26/10 (cliente: "margini molto gonfiati"): la rivendita sul sito non supera mai il mercato dei privati M meno
    # SITO_TETTO_PCT (10%). Vale subito anche per le auto già valutate (il ricalcolo del cron poi fa lo stesso).
    tetto_pct = float(os.environ.get("SITO_TETTO_PCT", "10")) / 100
    abbassate = tolte_tetto = 0
    tenute_t = []
    for it in items:
        pv = it.get("pv") if isinstance(it.get("pv"), dict) else None
        if pv and pv.get("M") and pv.get("margine") is not None:
            R = pv.get("R_finale") or pv.get("R")
            cap = int(round(float(pv["M"]) * (1 - tetto_pct)))
            if R and R > cap:
                m2 = int(pv["margine"]) - (int(R) - cap)
                abbassate += 1
                prezzo = it.get("prezzo") or 0
                if m2 < scovo.soglia(int(prezzo)) or (prezzo > p_lim and m2 < m_min):
                    tolte_tetto += 1
                    continue
                it["pv"] = dict(pv, margine=m2, R=cap, R_finale=cap, margine_prima_tetto=int(pv["margine"]))
        tenute_t.append(it)
    items = tenute_t
    # 26/10 (cliente: "togli quelle gonfiate"): margine oltre il 100% del prezzo E oltre 3.000 € = quasi sempre
    # truffa o confronto sbagliato (es. Maggiolino Cabrio a 4.999 € con 13.000 € di margine). Le auto economiche
    # molto sotto il mercato (margine fino a 3.000 €) restano, come da regola del cliente.
    g_pct = float(os.environ.get("SITO_GONFIATE_PCT", "100")) / 100
    g_eur = int(os.environ.get("SITO_GONFIATE_EURO", "3000"))
    prima_g = len(items)
    items = [it for it in items if not (
        (it.get("pv") or {}).get("margine") is not None and it.get("prezzo")
        and int(it["pv"]["margine"]) > g_eur and int(it["pv"]["margine"]) > g_pct * int(it["prezzo"]))]
    logging.getLogger("scovo").warning("GONFIATE tolte %d (margine oltre %d%% del prezzo e oltre %d €)",
                                       prima_g - len(items), int(g_pct * 100), g_eur)
    logging.getLogger("scovo").warning("TETTO_RIVENDITA %d abbassate a M−%d%%, %d tolte perché il margine non basta più",
                                       abbassate, int(tetto_pct * 100), tolte_tetto)
    prima = len(items)
    items = [it for it in items if not (it.get("km") and int(it["km"]) > km_max)]
    dopo_km = len(items)
    items = [it for it in items if not ((it.get("prezzo") or 0) > p_lim and (_marg(it) is None or _marg(it) < m_min))]
    # 26/10 (cliente): sotto il limite (4.000 €) basta un margine minimo più basso (SITO_MARGINE_MIN_SOTTO, 800 €)
    m_sotto = int(os.environ.get("SITO_MARGINE_MIN_SOTTO", "0"))
    if m_sotto:
        items = [it for it in items if (it.get("prezzo") or 0) > p_lim or (_marg(it) is not None and _marg(it) >= m_sotto)]
    logging.getLogger("scovo").warning("REGOLE_KM_MARGINE tolte: %d oltre %d km, %d sopra %d € con margine < %d €",
                                       prima - dopo_km, km_max, dopo_km - len(items), p_lim, m_min)
    # 25/10 (cliente: "prezzi e margini sballati"): il margine vale solo se viene da abbastanza auto uguali,
    # e i dati dell'annuncio devono essere coerenti (modello indicato, anno possibile per quel modello)
    n_min = int(os.environ.get("SITO_CONFRONTI_MIN", "5"))

    from datetime import date as _date
    km_anno_min = int(os.environ.get("SITO_KM_ANNO_MIN", "1500"))

    def _dati_ok(it):
        nome = (it.get("nome") or "").lower()
        # km non credibili: un'auto di 6+ anni con meno di 1.500 km l'anno è quasi sempre un errore dell'annuncio
        # (esame 25/10: Panda 2012 con 16.200 km, 500 del 2008 con 19.999 km)
        anni = _date.today().year - int(it.get("anno") or _date.today().year)
        km = it.get("km")
        if km is not None and anni >= 6 and int(km) < km_anno_min * anni:
            return False
        if "altro modello" in nome:
            return False
        anno = it.get("anno") or 0
        for mod, dal in (("2008", 2013), ("3008", 2009), ("5008", 2009), ("308", 2007), ("208", 2012), ("108", 2014)):
            if "peugeot" in nome and f" {mod}" in f" {nome}" and anno and anno < dal:
                return False
        return True

    prima = len(items)
    # 25/10: sotto i 3.000 € le auto uguali in vendita sono poche e la stima è debole (esame: Spark +25%, Musa +16%):
    # lì servono almeno 8 confronti
    n_min_eco, p_eco = int(os.environ.get("SITO_CONFRONTI_MIN_ECO", "8")), int(os.environ.get("SITO_ECO_DA", "3000"))

    def _n_serve(it):
        return n_min_eco if (it.get("prezzo") or 0) < p_eco else n_min

    pochi = [it for it in items if it.get("pv") and (it["pv"].get("n") or 0) < _n_serve(it)]
    items = [it for it in items if not (it.get("pv") and (it["pv"].get("n") or 0) < _n_serve(it))]
    dopo_n = len(items)
    items = [it for it in items if _dati_ok(it)]
    logging.getLogger("scovo").warning("REGOLE_AFFIDABILITA tolte: %d con meno di %d auto uguali, %d con dati incoerenti",
                                       len(pochi), n_min, dopo_n - len(items))
    quota = float(os.environ.get("DIESEL_MAX", "0.3"))    # massimo 30% di diesel (confermato dal cliente il 17/10)
    if quota >= 1:
        return items
    gruppi: dict = {}
    for it in items:
        sez = "eco" if it.get("economica") else "inc" if it.get("incidentata") else "affari"
        gruppi.setdefault((sez, it.get("area") or "milano"), []).append(it)
    via = set()
    for g in gruppi.values():
        # 23/10 (cliente): i diesel Euro 6 (immatricolati dal 2016) vanno benissimo, fuori dal tetto
        euro6 = int(os.environ.get("DIESEL_EURO6_DAL", "2016"))
        diesel = [it for it in g if (it.get("carb") or "").lower() == "diesel" and (it.get("anno") or 0) < euro6]
        altri = len(g) - len(diesel)
        tetto = int(altri * quota / (1 - quota))
        if len(diesel) > tetto:
            diesel.sort(key=lambda it: -scovo.guadagno_minimo(it))
            via.update(it["id"] for it in diesel[tetto:])
    if via:
        logging.getLogger("scovo").warning("DIESEL: %d diesel tolti per restare al %d%%", len(via), int(quota * 100))
    return [it for it in items if it["id"] not in via]


def _prezzi_cliente() -> dict:
    """data/prezzi_sito.json {"id auto": prezzo}: prezzo già trattato col venditore (richiesta del cliente)."""
    from pathlib import Path
    try:
        return {str(k): int(v) for k, v in json.loads((Path(__file__).resolve().parents[2] / "data" / "prezzi_sito.json").read_text()).items()}
    except Exception:
        return {}


def _ribasso_fascia(prezzo: int) -> int:
    """27/10 (cliente): prezzo mostrato = annuncio meno il ribasso della sua fascia, mai sotto il 60% dell'annuncio."""
    r = 1500 if prezzo < 8000 else 3000 if prezzo < 15000 else 4000 if prezzo < 50000 else 5000
    return int(round(max(prezzo - r, prezzo * 0.6) / 100) * 100)


def _prezzi_trattati(items: list[dict]) -> None:
    prezzi = _prezzi_cliente()
    tutte = os.environ.get("RIBASSO_FASCE") == "1"   # 27/10: ribasso per fascia su tutto il sito (temporaneo)
    for it in items:
        nuovo = _ribasso_fascia(it["prezzo"]) if tutte and it.get("prezzo") else prezzi.get(str(it.get("id")))
        # 28/10 13:50 (cliente, scelta esplicita "dal prezzo originale"): sopra RIBASSO_SOPRA € si mostra il prezzo
        # dell'annuncio meno RIBASSO_EURO (es. 20.000 / 3.000)
        sopra, rib = int(os.environ.get("RIBASSO_SOPRA", "0") or 0), int(os.environ.get("RIBASSO_EURO", "0") or 0)
        if not nuovo and sopra and rib and (it.get("prezzo") or 0) > sopra:
            nuovo = it["prezzo"] - rib
        if not nuovo or not it.get("prezzo") or nuovo == it["prezzo"]:
            continue
        diff = it["prezzo"] - nuovo
        it["prezzo"] = nuovo
        pv = it.get("pv")
        if isinstance(pv, dict) and pv.get("margine") is not None:
            it["pv"] = dict(pv, margine=pv["margine"] + diff)


from .scovo import FIN_SOLO_RE  # noqa: E402  (21/10: auto solo leasing/finanziamento)


def _solo_privati(items: list[dict]) -> list[dict]:
    """21/10 sera (cliente): sul sito SOLO auto messe in vendita da privati (venditore segnato come privato)."""
    if os.environ.get("SITO_SOLO_PRIVATI", "1") != "1" or not items:
        return items
    ids = [it["id"] for it in items if isinstance(it.get("id"), int)]
    try:
        with connect() as conn:
            # anche i commercianti che si segnano come privati: nome o segni da rivenditore nel testo dell'annuncio
            nomi = os.environ.get("SITO_COMMERCIANTI_NOMI", "loconte")
            marchi = r"(\ms\.?\s?r\.?\s?l\.?s?\M|\ms\.?p\.?a\.?\M|concessionari|autosalone|rivenditor|garanzia\s+(di\s+)?12\s+mesi|iva\s+esposta|esposta\s+iva|iva\s+deducibile|finanziamenti\s+in\s+sede|nostro\s+(parco|salone)|" \
                     + "|".join(re.escape(n.strip()) for n in nomi.split(",") if n.strip()) + ")"
            ok = {r["id"] for r in conn.execute(
                "SELECT id FROM listings WHERE id = ANY(%s) AND seller_type='privato' "
                "AND NOT ((coalesce(title,'') || ' ' || coalesce(description,'')) ~* %s)", (ids, marchi)).fetchall()}
    except Exception as e:
        logging.getLogger("scovo").warning("SOLO_PRIVATI non controllato: %s", str(e)[:200])
        return items
    tenute = [it for it in items if it.get("id") in ok]
    logging.getLogger("scovo").warning("SOLO_PRIVATI %d auto di commercianti tolte, %d restano", len(items) - len(tenute), len(tenute))
    return tenute


def _decisioni_jev(items: list[dict]) -> list[dict]:
    """21/10 sera (cliente): decide Jev (AI decisionale TypeSafe, JEV_MODO=attiva). Valgono solo le decisioni prese sul
    prezzo attuale. Fuori dal sito: probabile truffa, scarta con confidenza alta, danni evidenti nelle foto, e le auto
    che con la correzione di Jev alla rivendita (solo al ribasso) non hanno più margine. Le altre restano come sono."""
    if os.environ.get("JEV_MODO", "prova") != "attiva" or not items:
        return items
    ids = [it["id"] for it in items if isinstance(it.get("id"), int)]
    try:
        with connect() as conn:
            dec = {r["listing_id"]: r for r in conn.execute(
                "SELECT d.listing_id, d.decisione, d.confidenza, d.truffa, d.correzione_pct, d.versione_ok, "
                "(d.risposta->'guasto'->>'noul')::float AS guasto, (d.risposta->'prezzo_rata'->>'noul')::float AS rata, "
                "(d.risposta->'km_credibili'->>'noul')::float AS km_ok, "
                "(d.risposta->'answers'->'danni_foto'->>'noul')::float AS danni "
                "FROM decisione_ai d JOIN listings l ON l.id = d.listing_id "
                "WHERE d.listing_id = ANY(%s) AND d.prezzo = l.price_eur", (ids,)).fetchall()}
    except Exception as e:
        logging.getLogger("scovo").warning("JEV_SITO non applicato: %s", str(e)[:200])
        return items
    conf = float(os.environ.get("JEV_CONF_SCARTA", "0.6"))
    tr = float(os.environ.get("JEV_TRUFFA", "0.7"))
    m_min = int(os.environ.get("JEV_MARGINE_MIN", "300"))
    da, alta_min = int(os.environ.get("ALTA_DA", "50000")), int(os.environ.get("ALTA_MARGINE_MIN", "0"))
    from collections import Counter
    c, tenute = Counter(), []
    for it in items:
        d = dec.get(it.get("id"))
        if not d:
            c["non_decise"] += 1
            tenute.append(it)
            continue
        if str((it.get("pv") or {}).get("etichetta") or "").startswith("Selezione") and not (
                float(d.get("guasto") or 0) >= 0.7):
            c["selezione_a_mano"] += 1          # 26/10: scelte a mano, Jev toglie solo i guasti dichiarati
            tenute.append(it)
            continue
        # 24/10 sera (cliente): se c'è il numero del venditore l'auto resta anche se il prezzo sembra troppo basso;
        # il sospetto truffa toglie solo le auto senza numero.
        if (d["truffa"] or 0) >= tr:
            if it.get("senza_numero") or os.environ.get("JEV_TRUFFA_SOLO_SENZA_NUMERO", "1") != "1":
                c["truffa"] += 1
                continue
            c["truffa_tenuta_col_numero"] += 1
        if d["decisione"] == "scarta" and (d["confidenza"] or 0) >= conf:
            c["scarta"] += 1
            continue
        if (d["danni"] or 0) >= .7:
            c["danni_foto"] += 1
            continue
        # 25/10 (cliente): Jev come lettore di annunci. Fuori le auto non marcianti o con guasti dichiarati, quelle
        # con un prezzo che non è il prezzo intero (rata, anticipo) e quelle con i km non credibili: affari finti.
        if (d.get("guasto") or 0) >= .7:
            c["guasto_dichiarato"] += 1
            continue
        if (d.get("rata") or 0) >= .7:
            c["prezzo_non_vero"] += 1
            continue
        if d.get("km_ok") is not None and d["km_ok"] <= .3:
            c["km_non_credibili"] += 1
            continue
        # 25/10 (cliente): agente controllore. Se l'AI dice che le auto di confronto non sono la stessa auto
        # (motore, versione, allestimento diversi) il margine non è affidabile: l'auto resta fuori.
        if d.get("versione_ok") is not None and d["versione_ok"] < float(os.environ.get("JEV_VERSIONE_MIN", "0.4")):
            c["confronti_non_uguali"] += 1
            continue
        # 25/10: l'esame ha mostrato che il calcolo è già prudente (−4%) e la correzione dell'AI lo portava a −8%:
        # di base Jev non abbassa più la rivendita (JEV_CORREGGI=1 per riaccenderla)
        corr = min(0.0, float(d["correzione_pct"] or 0)) if os.environ.get("JEV_CORREGGI", "0") == "1" else 0.0
        pv = it.get("pv") or {}
        m = pv.get("margine") if pv else None
        if m is None:
            try:
                m = scovo.guadagno_minimo(it)
            except Exception:
                m = None
        if m is not None and corr < 0:
            prezzo = it.get("prezzo") or 0
            m2 = m + (prezzo + m) * corr / 100
            soglia = alta_min if prezzo >= da and alta_min else m_min
            if m2 < soglia:
                c["margine_dopo_correzione"] += 1
                continue
            # 25/10: vale la stima più prudente. Il margine mostrato è quello corretto dall'AI e deve rispettare
            # la regola del sito (sopra 2.000 € almeno 1.500 € di margine).
            if prezzo > int(os.environ.get("SITO_PREZZO_LIMITE", "2000")) and m2 < int(os.environ.get("SITO_MARGINE_MIN_SOPRA", "1500")):
                c["margine_dopo_correzione"] += 1
                continue
            if isinstance(it.get("pv"), dict) and it["pv"].get("margine") is not None:
                it["pv"] = dict(it["pv"], margine=int(round(m2)), margine_calcoli=int(m), correzione_ai_pct=corr)
                for k in ("R", "R_finale"):
                    if it["pv"].get(k) is not None:
                        it["pv"][k] = int(round(it["pv"][k] + (m2 - m)))
        c[f"tenuta_{d['decisione']}"] += 1
        tenute.append(it)
    logging.getLogger("scovo").warning("JEV_SITO %d tolte, %d restano %s", len(items) - len(tenute), len(tenute),
                                       json.dumps(dict(c)))
    return tenute


def _margini_gonfiati(items: list[dict]) -> list[dict]:
    """21/10 sera (cliente): ricontrollo di tutte le auto del sito con il modello AI dei prezzi (tabella stima_ai,
    addestrato su tutta la base, prima le utilitarie). Dove il modello è affidabile per quel modello d'auto, la
    rivendita non può superare il prezzo AI meno la sua incertezza (almeno −8%); se con quella rivendita il margine
    non regge più, l'auto ha un margine gonfiato ed esce dal sito."""
    if os.environ.get("SITO_CONTROLLO_AI", "1") != "1" or not items:
        return items
    ids = [it["id"] for it in items if isinstance(it.get("id"), int)]
    try:
        with connect() as conn:
            ai = {r["listing_id"]: r for r in conn.execute(
                "SELECT listing_id, prezzo, errore, n_gruppo FROM stima_ai WHERE listing_id = ANY(%s) "
                "AND computed_at > now() - interval '2 days'", (ids,)).fetchall()}
    except Exception as e:
        logging.getLogger("scovo").warning("CONTROLLO_AI non applicato: %s", str(e)[:200])
        return items
    err_max = float(os.environ.get("AI_ERRORE_MAX_SITO", "0.18"))
    n_min = int(os.environ.get("AI_N_MIN_SITO", "8"))
    da, alta_min = int(os.environ.get("ALTA_DA", "50000")), int(os.environ.get("ALTA_MARGINE_MIN", "0"))
    from collections import Counter
    c, tenute, esempi = Counter(), [], []
    for it in items:
        a = ai.get(it.get("id"))
        pv = it.get("pv") or {}
        prezzo = it.get("prezzo") or pv.get("acquisto") or 0
        if not a or not pv or pv.get("margine") is None or not prezzo:
            c["senza_stima"] += 1
            tenute.append(it)
            continue
        if a["errore"] is None or a["errore"] > err_max or (a["n_gruppo"] or 0) < n_min:
            c["stima_poco_sicura"] += 1
            tenute.append(it)
            continue
        R = pv.get("R_finale") or pv.get("R")
        if not R:
            c["senza_rivendita"] += 1
            tenute.append(it)
            continue
        r_ai = a["prezzo"] * (1 - max(0.08, float(a["errore"])))
        m_ai = pv["margine"] - max(0, R - r_ai)
        minimo = alta_min if prezzo >= da and alta_min else max(int(os.environ.get("AI_MARGINE_MIN", "1000")), int(prezzo * .1))
        if R > r_ai and m_ai < minimo:            # solo se l'AI dice davvero che la rivendita è troppo alta
            c["margine_gonfiato"] += 1
            if len(esempi) < 15:
                esempi.append({"id": it["id"], "auto": str(it.get("nome") or "")[:50], "prezzo": prezzo, "R": int(R),
                               "ai": int(a["prezzo"]), "err": round(float(a["errore"]), 2),
                               "margine": pv["margine"], "margine_ai": int(m_ai)})
            continue
        # 21/10 sera (cliente: "le auto con margini bassi che dovrebbero essere alzate, alzale"): rialzo controllato
        # solo dove il modello AI è molto preciso per quel modello d'auto E i confronti sono tanti; la rivendita resta
        # sotto il prezzo AI meno l'incertezza e sotto il 95% del mercato, e sale al massimo di AI_RIALZO_MAX euro
        M = pv.get("M") or 0
        if (float(a["errore"]) <= float(os.environ.get("AI_RIALZO_ERR", "0.10")) and (a["n_gruppo"] or 0) >= 30
                and (pv.get("n") or 0) >= 10 and M):
            tetto = min(r_ai, M * .95)
            su = int(min(tetto - R, int(os.environ.get("AI_RIALZO_MAX", "1500"))))
            if su >= 100:
                it["pv"] = dict(pv, R_finale=int(R + su), margine=int(pv["margine"] + su), rialzo_ai=su)
                c["rialzate"] += 1
        c["confermata"] += 1
        tenute.append(it)
    logging.getLogger("scovo").warning("CONTROLLO_AI %d tolte, %d restano %s %s", len(items) - len(tenute), len(tenute),
                                       json.dumps(dict(c)), json.dumps(esempi, ensure_ascii=False))
    return tenute


def _senza_solo_finanziamento(items: list[dict]) -> list[dict]:
    if os.environ.get("SITO_NO_SOLO_FIN", "1") != "1" or not items:
        return items
    ids = [it["id"] for it in items if isinstance(it.get("id"), int)]
    try:
        with connect() as conn:
            via = {r["id"] for r in conn.execute(
                "SELECT id FROM listings WHERE id = ANY(%s) AND (coalesce(description,'') || ' ' || coalesce(title,'')) ~* %s",
                (ids, FIN_SOLO_RE)).fetchall()}
    except Exception as e:
        logging.getLogger("scovo").warning("SOLO_FIN non controllato: %s", str(e)[:200])
        return items
    logging.getLogger("scovo").warning("SOLO_FIN %d auto tolte (solo leasing/finanziamento)", len(via))
    return [it for it in items if it.get("id") not in via]


def _margine_alta(items: list[dict]) -> list[dict]:
    """21/10 (cliente): le auto da ALTA_DA (50.000 €) in su restano sul sito solo con almeno ALTA_MARGINE_MIN € di
    margine (lo stesso margine mostrato sul sito)."""
    da = int(os.environ.get("ALTA_DA", "50000"))
    minimo = int(os.environ.get("ALTA_MARGINE_MIN", "0"))
    if minimo <= 0:
        return items

    def margine(it):
        pv = it.get("pv") or {}
        if pv:
            return pv.get("margine")
        try:
            return scovo.guadagno_minimo(it)
        except Exception:
            return None

    # 21/10 (cliente): sopra ALTA_DA solo auto messe in vendita da PRIVATI
    privati = None
    if os.environ.get("ALTA_SOLO_PRIVATI", "1") == "1":
        ids = [it["id"] for it in items if (it.get("prezzo") or 0) >= da and isinstance(it.get("id"), int)]
        try:
            with connect() as conn:
                privati = {r["id"] for r in conn.execute("SELECT id FROM listings WHERE id = ANY(%s) AND seller_type='privato'",
                                                         (ids,)).fetchall()}
        except Exception as e:
            logging.getLogger("scovo").warning("ALTA_SOLO_PRIVATI non controllato: %s", str(e)[:200])
    # 21/10 (cliente): i commercianti restano solo con deal molto grossi (ALTA_MARGINE_COMM, es. 15.000 €);
    # auto tenute a mano per marca/modello/prezzo (data/tieni_sito.json)
    comm_min = int(os.environ.get("ALTA_MARGINE_COMM", "0"))
    try:
        from pathlib import Path
        tieni = json.loads((Path(__file__).resolve().parents[2] / "data" / "tieni_sito.json").read_text())
    except Exception:
        tieni = []

    def tenuta(it):
        testo = f"{it.get('marca') or ''} {it.get('modello') or ''} {it.get('nome') or ''}".lower()
        return any(all(w.lower() in testo for w in t.get("parole", [])) and
                   t.get("prezzo_min", 0) <= (it.get("prezzo") or 0) <= t.get("prezzo_max", 10**9) for t in tieni)

    tenute, tolte = [], 0
    for it in items:
        if (it.get("prezzo") or 0) >= da:
            if tenuta(it):
                tenute.append(it)
                continue
            m = margine(it)
            if privati is not None and it.get("id") not in privati:
                if not comm_min or m is None or m < comm_min:
                    tolte += 1
                    continue
            if m is None or m < minimo:
                tolte += 1
                continue
        tenute.append(it)
    alte = sum(1 for it in tenute if (it.get("prezzo") or 0) >= da)
    logging.getLogger("scovo").warning("MARGINE_ALTA da %d € con almeno %d €, solo privati: %d tolte, %d restano (%d da %d € in su)",
                                       da, minimo, tolte, len(tenute), alte, da)
    return tenute


def _margine_recenti(items: list[dict]) -> list[dict]:
    """20/10 (cliente): le auto dal 2017 in poi restano sul sito solo con almeno 3.500 € di margine
    (lo stesso margine mostrato sul sito). Le auto più vecchie non cambiano."""
    dal = int(os.environ.get("MARGINE_RECENTI_DAL", "2017"))
    minimo = int(os.environ.get("MARGINE_RECENTI_MIN", "3500"))
    if minimo <= 0:
        return items

    def margine(it):
        pv = it.get("pv") or {}
        if pv:
            return pv.get("margine")
        try:
            return scovo.guadagno_minimo(it)
        except Exception:
            return None

    tenute, tolte = [], 0
    for it in items:
        m = margine(it)
        if it.get("anno") and it["anno"] >= dal and (m is None or m < minimo):
            tolte += 1
            continue
        tenute.append(it)
    logging.getLogger("scovo").warning("MARGINE_RECENTI dal %d con almeno %d €: %d tolte, %d restano",
                                       dal, minimo, tolte, len(tenute))
    return tenute


# Elenco auto condiviso: la query pesante gira al massimo una volta ogni ELENCO_SECONDI (90) per tutti,
# in sottofondo; ogni richiesta aggiunge solo le auto già aperte da quel commerciante (query leggera).
_elenco: dict = {"t": 0.0, "items": [], "opens": {}, "busy": False}
_elenco_lock = threading.Lock()
ELENCO_SECONDI = int(os.environ.get("ELENCO_SECONDI", "90"))


def _rivendita_prudente(items: list) -> list:
    """27/10 sera (cliente): rivendita abbassata del RIVENDITA_SCONTO (5%) su tutte le auto, per stare sicuri;
    poi restano solo quelle con il margine delle regole (sopra 4.000 € almeno 1.500 €, fino a 4.000 € almeno 800 €)."""
    sc = float(os.environ.get("RIVENDITA_SCONTO", "0") or 0)
    # 28/10 13:55 (cliente): da RIVENDITA_SOPRA € (20.000) in su rivendita −RIVENDITA_SOPRA_PCT (10%)
    r_sopra = int(os.environ.get("RIVENDITA_SOPRA", "0") or 0)
    r_pct = float(os.environ.get("RIVENDITA_SOPRA_PCT", "0") or 0)
    # 28/10 22:06 (cliente, dalla misura su 327 auto vendute): sotto RIVENDITA_SOTTO € (10.000) rivendita −RIVENDITA_SOTTO_PCT (5%)
    r_sotto = int(os.environ.get("RIVENDITA_SOTTO", "0") or 0)
    r_sotto_pct = float(os.environ.get("RIVENDITA_SOTTO_PCT", "0") or 0)
    if sc <= 0 and not (r_sopra and r_pct) and not (r_sotto and r_sotto_pct):
        return items
    out = []
    for it in items:
        pv = it.get("pv")
        R = (pv or {}).get("R_finale") or (pv or {}).get("R") if isinstance(pv, dict) else None
        if not R or pv.get("margine") is None:
            out.append(it)            # sezioni senza calcolo (economiche): invariate
            continue
        extra = 0.0
        nome = str(it.get("nome") or "").lower()
        for voce in (os.environ.get("RIVENDITA_MARCA") or "").split(","):    # 27/10 sera: es. "fiat:0.2,lancia:0.15"
            if ":" in voce:
                marca, pct = voce.split(":", 1)
                if marca.strip() and nome.startswith(marca.strip().lower()):
                    extra += float(pct)
        anni_v = int(os.environ.get("RIVENDITA_VECCHIE_ANNI", "10"))
        if it.get("anno") and (time.localtime().tm_year - int(it["anno"])) >= anni_v:
            extra += float(os.environ.get("RIVENDITA_VECCHIE", "0") or 0)   # auto vecchie: si rivendono peggio
        if r_sopra and r_pct and (it.get("prezzo") or 0) >= r_sopra:
            extra += r_pct
        if r_sotto and r_sotto_pct and 0 < (it.get("prezzo") or 0) < r_sotto:
            extra += r_sotto_pct
        if sc + extra <= 0:
            out.append(it)
            continue
        giu = int(round(R * min(sc + extra, 0.6)))
        it = dict(it, pv=dict(pv, R=int(R - giu), R_finale=int(R - giu), margine=int(pv["margine"] - giu)))
        soglia = 1500 if (it.get("prezzo") or 0) > 4000 else 800
        # 28/10 13:58 (cliente: "rimettile"): le auto da RIVENDITA_SOPRA in su restano anche sotto soglia (margine > 0)
        if r_sopra and os.environ.get("RIVENDITA_SOPRA_TIENI") == "1" and (it.get("prezzo") or 0) >= r_sopra:
            soglia = 1 - int(os.environ.get("RIBASSO_EURO", "0") or 0)   # col ribasso del prezzo il margine torna positivo
        if it["pv"]["margine"] >= soglia:
            out.append(it)
    logging.getLogger("scovo").warning("RIVENDITA_PRUDENTE -%d%%: %d auto prima, %d restano", round(sc * 100), len(items), len(out))
    return out


_calib_cache = {"t": 0.0, "f": None}
_FASCE_CALIB = [(5000, "<5k"), (10000, "5-10k"), (20000, "10-20k"), (35000, "20-35k"), (60000, "35-60k"), (10**9, ">60k")]


def _fattori_calibra() -> list:
    """28/10 sera (cliente: "non voglio più margini gonfiati"): per fascia, prezzo di vendita vero / mediana degli annunci,
    misurato sulle auto vendute (tabella calibrazione, aggiornata dalla misura CALIBRA). Mai sopra 1, mai sotto 0,8;
    fasce con meno di CALIBRA_N_MIN auto misurate: si usa il valore di riserva CALIBRA_FATTORI."""
    if time.time() - _calib_cache["t"] < 1800 and _calib_cache["f"] is not None:
        return _calib_cache["f"]
    riserva = {}
    for voce in (os.environ.get("CALIBRA_FATTORI") or "<5k:0.87,5-10k:0.915,10-20k:0.97").split(","):
        if ":" in voce:
            k, v = voce.split(":", 1)
            riserva[k.strip()] = float(v)
    fasce = {}
    try:
        with connect() as conn:
            if conn.execute("SELECT to_regclass('calibrazione') AS t").fetchone()["t"]:
                r = conn.execute("SELECT dati FROM calibrazione ORDER BY at DESC LIMIT 1").fetchone()
                d = r["dati"] if r else None
                fasce = (json.loads(d) if isinstance(d, str) else d or {}).get("fasce") or {}
    except Exception as e:
        logging.getLogger("scovo").warning("CALIBRA fattori non letti: %s", str(e)[:120])
    nmin = int(os.environ.get("CALIBRA_N_MIN", "20"))
    out = []
    for lim, nome in _FASCE_CALIB:
        f = fasce.get(nome) or {}
        x = f.get("prezzo_su_M") if (f.get("n") or 0) >= nmin else None
        x = riserva.get(nome, 1.0) if x is None else x
        out.append((lim, nome, max(0.8, min(1.0, float(x)))))
    _calib_cache.update(t=time.time(), f=out)
    logging.getLogger("scovo").warning("CALIBRA_FATTORI %s", {n: x for _, n, x in out})
    return out


def _mercato_calibrato(items: list) -> list:
    """28/10 sera (cliente): 1) il mercato è la mediana dei prezzi CHIESTI: si porta al prezzo di vendita vero col
    fattore misurato della sua fascia, e la rivendita diventa min(rivendita, mercato corretto × 0,95);
    2) sotto CONFRONTI_MIN_SOTTO € (10.000) servono almeno CONFRONTI_MIN_ECO (8) auto uguali;
    3) difetti scritti nell'annuncio (lettura AI, tabella ai_difetti): costo tolto dal margine, gravi fuori dal sito.
    Poi il margine minimo delle regole (sopra 4.000 € almeno 1.500 €, fino a 4.000 € almeno 800 €)."""
    if os.environ.get("MERCATO_CALIBRATO", "1") != "1" or not items:
        return items
    fatt = _fattori_calibra()
    lim_eco, n_eco = int(os.environ.get("CONFRONTI_MIN_SOTTO", "10000")), int(os.environ.get("CONFRONTI_MIN_ECO", "8"))
    difetti = _difetti_letti([it["id"] for it in items if isinstance(it.get("id"), int)])
    out, st = [], {"corretti": 0, "pochi_confronti": 0, "difetto_grave": 0, "sotto_soglia": 0, "difetto_costo": 0}
    for it in items:
        pv = it.get("pv")
        if not isinstance(pv, dict) or pv.get("margine") is None or not pv.get("M") or it.get("supercar"):
            out.append(it)
            continue
        prezzo = it.get("prezzo") or 0
        if prezzo and prezzo < lim_eco and (pv.get("n") or 0) and pv["n"] < n_eco and pv.get("metodo") != "confronti Europa":
            st["pochi_confronti"] += 1
            continue
        M = float(pv["M"])
        f = next(x for lim, _, x in fatt if M < lim)
        R = float(pv.get("R_finale") or pv.get("R") or 0)
        R2 = min(R, M * f * 0.95) if f < 1 else R
        giu = int(round(R - R2))
        d = difetti.get(it["id"])
        if d and d.get("grave"):
            st["difetto_grave"] += 1
            continue
        costo = int(d.get("costo") or 0) if d else 0
        if giu > 0 or costo:
            st["corretti"] += 1
            st["difetto_costo"] += bool(costo)
            pv = dict(pv, M=int(round(M * f)), M_annunci=int(M), R=int(R2), R_finale=int(R2),
                      margine=int(pv["margine"] - giu - costo), calibrato=round(f, 3),
                      difetto=(d or {}).get("motivo") if costo else None)
            it = dict(it, pv=pv, mercato=int(round(M * f)))
        soglia = 1500 if prezzo > 4000 else 800
        if it["pv"]["margine"] >= soglia or (it.get("prezzo") or 0) >= int(os.environ.get("RIVENDITA_SOPRA", "0") or 10**9) \
                and os.environ.get("RIVENDITA_SOPRA_TIENI") == "1" and it["pv"]["margine"] > 0:
            out.append(it)
        else:
            st["sotto_soglia"] += 1
    logging.getLogger("scovo").warning("MERCATO_CALIBRATO %d auto prima, %d restano %s", len(items), len(out), st)
    return out


def _difetti_letti(ids: list) -> dict:
    try:
        with connect() as conn:
            if not conn.execute("SELECT to_regclass('ai_difetti') AS t").fetchone()["t"]:
                return {}
            return {r["listing_id"]: dict(r) for r in conn.execute(
                "SELECT listing_id, costo, grave, motivo FROM ai_difetti WHERE listing_id = ANY(%s)", (ids,)).fetchall()}
    except Exception:
        return {}


def _zona_sito(items: list) -> list:
    """27/10 sera (cliente): sul sito solo le province accettate (Lombardia + Novara, Pavia, Piacenza)."""
    ok = {p.strip().upper() for p in (os.environ.get("ZONA_SITO") or "").split(",") if p.strip()}
    if not ok:
        return items
    out = []
    for it in items:
        m = re.search(r"\((\w\w)\)\s*$", str(it.get("zona") or ""))
        if not m or m.group(1).upper() in ok:
            out.append(it)
    logging.getLogger("scovo").warning("ZONA_SITO %d auto prima, %d restano", len(items), len(out))
    return out


def _margine_minimo_sito(items: list) -> list:
    """23/10 10:59, cliente: sul sito solo auto con margine (quello mostrato, dopo prezzi trattati e controlli)
    di almeno MARGINE_MIN_SITO €."""
    m = int(os.environ.get("MARGINE_MIN_SITO", "0"))
    if m <= 0:
        return items
    out = [it for it in items if ((it.get("pv") or {}).get("margine") or 0) >= m]
    if len(out) != len(items):
        logging.getLogger("scovo").warning("MARGINE_MIN_SITO %d auto tolte (margine sotto %d €), %d restano", len(items) - len(out), m, len(out))
    return out


def _senza_scarti_id(items: list) -> list:
    """23/10: le auto tolte a mano per id (data/scarti_sito.json) escono dal sito da qualunque strada arrivino
    (anche le utilitarie economiche, che non guardano lo stato 'scartato')."""
    from pathlib import Path
    try:
        ids = {int(e["id"]) for e in json.loads((Path(__file__).resolve().parents[2] / "data" / "scarti_sito.json").read_text())
               if e.get("id")}
    except Exception:
        return items
    out = [it for it in items if it.get("id") not in ids]
    if len(out) != len(items):
        logging.getLogger("scovo").warning("SCARTI_ID %d auto tolte dal sito", len(items) - len(out))
    return out


def _supercar_europa(gia: set, opens: dict) -> list:
    """28/10 sera: auto di privati da 70.000 € in su in 8 paesi europei con margine ≥ 5.000 € (tabella supercar_eu).
    Il prezzo di rivendita e il margine sono quelli calcolati sui confronti europei, con i costi di import se all'estero."""
    if os.environ.get("SUPERCAR_SITO", "1") != "1":
        return []
    from collections import Counter
    from ..pipeline.supercar_eu import SITO_SQL
    out, scarti = [], Counter()
    try:
        with connect() as conn:
            if not conn.execute("SELECT to_regclass('supercar_eu') AS t").fetchone()["t"]:
                return []
            val = {r["listing_id"]: r for r in conn.execute(SITO_SQL, {"lim": int(os.environ.get("SUPERCAR_QUANTE", "300"))}).fetchall()}
            if not val:
                return []
            rows = conn.execute(scovo.ECO_SQL.split("WHERE l.status='attivo'")[0] + " WHERE l.id = ANY(%(ids)s) AND l.status='attivo'",
                                {"me": 0, "ids": list(val)}).fetchall()
            # 29/10 notte (cliente: "solo quelle davvero in vendita, margini giusti, niente leasing/finanziamenti, niente foto da internet")
            from ..ai import supercar_vero as SV
            desc = {x["id"]: x["description"] for x in conn.execute("SELECT id, description FROM listings WHERE id = ANY(%s)", (list(val),)).fetchall()}
            controllo = SV.letti(conn, list(val))
        for r in rows:
            if r["id"] in gia:
                continue
            v = dict(val[r["id"]])
            motivo, corr = SV.filtra(dict(r, description=desc.get(r["id"])), v, int(r.get("n_photos") or 0))
            if motivo:
                scarti[motivo] += 1
                continue
            c = controllo.get(r["id"])
            if not c or not c["ok"]:
                scarti["non_controllata_ai" if not c else "scartata_ai"] += 1
                continue
            v.update(r=corr["R"], margine=corr["margine"])
            it = scovo.item_eco(r)
            if not it:
                continue
            it.update(economica=False, verificare=True, mercato=int(v["m"]), top=True, supercar=True,
                      zona=" · ".join(x for x in (r.get("city"), v["paese"]) if x),
                      pv={"categoria": "opportunita", "etichetta": "Selezione Supercar Europa", "M": int(v["m"]),
                          "R": int(v["r"]), "R_finale": int(v["r"]), "margine": int(v["margine"]), "n": int(v["n"]),
                          "totale_costi": int(v["costi"]), "metodo": "confronti Europa"})
            out.append(it)
            opens[it["id"]] = int(r["opens"] or 0)
    except Exception as ex:
        logging.getLogger("scovo").warning("SUPERCAR_SITO non caricate: %s", str(ex)[:200])
    logging.getLogger("scovo").warning("SUPERCAR_SITO %d auto, tolte %s", len(out), dict(scarti))
    return out


def _forzate_sito(gia: set, opens: dict) -> list:
    """19/10 (cliente: "mettimeli tutti sul sito"): auto scelte a mano (data/forzate_sito.json, per link) sempre sul
    sito finché l'annuncio è attivo, con il mercato e la rivendita veloce calcolati sulle auto uguali."""
    from pathlib import Path
    f = Path(__file__).resolve().parents[2] / "data" / "forzate_sito.json"
    try:
        tutte = json.loads(f.read_text())
        voci = {e["url"]: e for e in tutte if e.get("url")}
        per_id = {int(e["id"]): e for e in tutte if e.get("id") and not e.get("url")}   # 26/10: scelte per id
    except Exception:
        return []
    if not voci and not per_id:
        return []
    out = []
    try:
        with connect() as conn:
            rows = conn.execute(scovo.ECO_SQL.split("WHERE l.status='attivo'")[0]
                                + " WHERE (l.url = ANY(%(urls)s) OR l.id = ANY(%(ids)s)) AND l.status='attivo'",
                                {"me": 0, "urls": list(voci), "ids": list(per_id)}).fetchall()
        costi = int(os.environ.get("POLICY_COSTO_FISSO", "200"))      # 26/10: tagliando 150 € + pulizia 50 €
        for r in rows:
            if r["id"] in gia:
                continue
            e = voci.get(r["url"]) or per_id.get(r["id"])
            if not e:
                continue
            it = scovo.item_eco(r)
            if not it:
                continue
            prezzo = int(r["price_eur"]) - int(e.get("sconto") or 0)    # prezzo già trattato col venditore
            it["prezzo"] = prezzo
            marg = int(e["R"]) - prezzo - costi
            it.update(economica=False, verificare=True, mercato=int(e["M"]), top=True,
                      pv={"categoria": "candidato", "etichetta": "Selezione · da verificare", "M": int(e["M"]),
                          "R": int(e["R"]), "R_finale": int(e["R"]), "margine": marg, "n": int(e.get("n") or 0),
                          "totale_costi": costi, "metodo": "confronti"})
            out.append(it)
            opens[it["id"]] = int(r["opens"] or 0)
    except Exception as ex:
        logging.getLogger("scovo").warning("forzate sito non caricate: %s", str(ex)[:200])
    logging.getLogger("scovo").warning("FORZATE_SITO %d su %d", len(out), len(voci) + len(per_id))
    return out


_dump_fatto = {"t": 0.0}


def _dump_candidati(pool: list[dict], sul_sito: set | None = None) -> None:
    """26/10 (cliente: "analizza e sceglimi tu 100 auto dei candidati"): una volta, nei log, tutte le auto di privati
    con il numero e fino a 150.000 km, con i numeri del calcolo e il link dell'annuncio. Niente dati del venditore."""
    lg = logging.getLogger("scovo")
    righe = []
    for it in pool:
        pv = it.get("pv") or {}
        if (it.get("km") or 0) > 150000 or not pv.get("M"):
            continue
        righe.append({"id": it["id"], "auto": it.get("nome"), "anno": it.get("anno"),
                      "km": it.get("km"), "carb": it.get("carb"), "fonte": it.get("fonte"), "prezzo": it.get("prezzo"),
                      "M": pv.get("M"), "R": pv.get("R_finale") or pv.get("R"), "margine": pv.get("margine"),
                      "n": pv.get("n"), "cat": pv.get("categoria"), "zona": it.get("zona"),
                      "sito": (it["id"] in sul_sito) if sul_sito is not None else None,
                      "metodo": pv.get("metodo"), "livello": pv.get("livello")})
    for k in range(0, len(righe), 40):
        lg.warning("DUMP_CANDIDATI %d/%d %s", k, len(righe), json.dumps(righe[k:k + 40], ensure_ascii=False))
    lg.warning("DUMP_CANDIDATI_FINE %d", len(righe))


def _calcola_elenco() -> None:
    t0 = time.time()
    with connect() as conn:
        # 28/10: senza ORDER BY il LIMIT prendeva ogni volta 3000 righe a caso: il sito oscillava tra 70 e 170 auto
        rows = conn.execute(scovo.LIST_SQL + " ORDER BY l.id DESC LIMIT %(lim)s",
                            {"me": 0, "lim": int(os.environ.get("ELENCO_LIMITE", "8000"))}).fetchall()
    # nuova policy attiva appena il ricalcolo ha valutato le auto (prima del primo giro vale la regola precedente)
    scovo.POLICY_PRONTA["ok"] = sum(1 for r in rows if r.get("pv")) >= max(1, len(rows) // 2)
    items, opens, riserva = [], {}, []
    for r in rows:
        it = scovo.item(r)
        if scovo.abbastanza(it):
            items.append(it)
            opens[it["id"]] = int(r["opens"] or 0)
        elif scovo.di_riserva(it):
            riserva.append(it)
            opens[it["id"]] = int(r["opens"] or 0)
    items += scovo.copri_fasce(items, riserva)
    items = _meno_subito_senza_telefono(items)
    # sezione "Sotto 1.000 €": tutte le auto di privati dal 2010, anche senza margine calcolato
    if os.environ.get("ECONOMICHE_SITO", "1") == "1":
        gia = {it["id"] for it in items}
        try:
            with connect() as conn:
                conn.execute("SET LOCAL statement_timeout = '%ds'" % int(os.environ.get("ECO_TIMEOUT_S", "120")))
                eco = conn.execute(scovo.ECO_SQL + " ORDER BY l.first_seen_at DESC LIMIT %(lim)s",
                                   dict(scovo.eco_params(0), lim=int(os.environ.get("ECONOMICHE_QUANTE", "2500")))).fetchall()
            for r in eco:
                it = scovo.item_eco(r)
                # sopra ECONOMICHE_LIBERO (1.000 €) si mostrano solo se il modello prezzi vede un guadagno
                sopra = it and it["prezzo"] > int(os.environ.get("ECONOMICHE_LIBERO", "1000"))
                if sopra and it.get("pv") is not None:
                    if it["pv"].get("categoria") not in scovo.CATEGORIE_SITO:     # policy prudente: niente eccezioni
                        continue
                elif it and (it.get("pv") or {}).get("categoria") not in scovo.CATEGORIE_SITO:
                    continue                      # 17/10: anche sotto 1.000 € solo auto con il margine minimo (1.500 €)
                elif sopra and (not it.get("mercato") or scovo.guadagno_minimo(it) < int(os.environ.get("ECONOMICHE_GUADAGNO", "400"))):
                    continue
                if it and it["id"] not in gia and not scovo.troppi_km(it.get("km")):
                    items.append(it)
                    opens[it["id"]] = int(r["opens"] or 0)
        except Exception as e:
            logging.getLogger("scovo").warning("sotto 1.000 non caricate: %s", str(e)[:200])
    _pool_revisione = None
    if os.environ.get("DUMP_CANDIDATI") == "1" and time.time() - _dump_fatto.get("t", 0) > float(os.environ.get("DUMP_ORE", "3")) * 3600:
        _dump_fatto["t"] = time.time()
        try:
            _pool_revisione = _solo_privati(_solo_con_telefono([dict(it) for it in items]))
        except Exception as e:
            logging.getLogger("scovo").warning("DUMP_CANDIDATI non riuscito: %s", str(e)[:200])
    items = _regole_finali(_solo_con_telefono(items))
    # 26/10: le auto scelte a mano sostituiscono la stessa auto arrivata dal calcolo (così Jev non le toglie per
    # "confronti non uguali": restano col margine prudente scelto a mano, min(calcolo, mercato −5%))
    _forz = _forzate_sito(set(), opens)
    _ids_f = {f["id"] for f in _forz}
    items = [it for it in items if it["id"] not in _ids_f] + _forz
    items = _zona_sito(_rivendita_prudente(_mercato_calibrato(_margine_minimo_sito(_senza_scarti_id(_margini_gonfiati(_decisioni_jev(_solo_privati(_senza_solo_finanziamento(_margine_alta(_margine_recenti(items)))))))))))
    # 28/10 sera (cliente): supercar da 70.000 € in tutta Europa (valutate sui confronti europei, vedi supercar_eu.py)
    items += _senza_scarti_id(_supercar_europa({it["id"] for it in items}, opens))
    # 26/10 pomeriggio: il prezzo trattato (data/prezzi_sito.json) si applica DOPO i controlli, che restano sul prezzo vero
    _prezzi_trattati(items)
    _elenco.update(t=time.time(), items=items, opens=opens)
    if _pool_revisione is not None:
        try:
            _dump_candidati(_pool_revisione, {it["id"] for it in items})
        except Exception as e:
            logging.getLogger("scovo").warning("DUMP_CANDIDATI non riuscito: %s", str(e)[:200])
    _log_controllo_finale(items)
    from collections import Counter
    c = Counter()
    for it in items:
        sez = "eco" if it.get("economica") else "inc" if it.get("incidentata") else "affari"
        c[f"{sez}:{it.get('area') or 'milano'}"] += 1
        if sez == "affari" and it["prezzo"] < 2000:
            c[f"affari_sotto_2000:{it.get('area') or 'milano'}"] += 1
    logging.getLogger("scovo").warning("ELENCO %d auto in %.1fs %s", len(items), time.time() - t0, dict(sorted(c.items())))
    try:      # 19/10: auto sul sito a metà prezzo (o meno) rispetto al mercato, e quante entrate nelle ultime 14 ore
        from datetime import datetime, timedelta, timezone
        dal = datetime.now(timezone.utc) - timedelta(hours=14)
        sotto = []
        for it in items:
            m = (it.get("pv") or {}).get("M") or it.get("mercato")
            if m and it["prezzo"] <= .5 * m:
                nv = it.get("nuova")
                nv = datetime.fromisoformat(nv) if isinstance(nv, str) else nv
                sotto.append({"id": it["id"], "auto": f"{it.get('nome')} {it.get('anno')} {it.get('km')}km", "prezzo": it["prezzo"],
                              "M": int(m), "pct": round(it["prezzo"] / m * 100), "n": (it.get("pv") or {}).get("n"),
                              "cat": (it.get("pv") or {}).get("categoria"), "fonte": it.get("fonte"),
                              "notte": bool(nv and nv.tzinfo and nv >= dal)})
        logging.getLogger("scovo").warning("ELENCO_SOTTO50 totale=%d notte=%d %s", len(sotto),
                                           sum(x["notte"] for x in sotto), json.dumps(sotto[:60], ensure_ascii=False, default=str))
    except Exception as e:
        logging.getLogger("scovo").warning("ELENCO_SOTTO50 errore: %s", str(e)[:200])


_ultimo_controllo = {"t": 0.0}


def _log_controllo_finale(items: list[dict]) -> None:
    """Ogni CONTROLLO_MIN minuti: elenco compatto delle auto sul sito per il controllo finale a mano (solo dati
    dell'auto, niente venditori): così si rivedono una per una mercato, rivendita e margine."""
    if time.time() - _ultimo_controllo["t"] < float(os.environ.get("CONTROLLO_MIN", "30")) * 60:
        return
    _ultimo_controllo["t"] = time.time()
    lg = logging.getLogger("controllo")
    for it in sorted(items, key=lambda x: -((x.get("pv") or {}).get("margine") or 0))[:int(os.environ.get("CONTROLLO_MAX", "400"))]:
        pv = it.get("pv") or {}
        lg.warning("CONTROLLO_FINALE %s", json.dumps({
            "id": it["id"], "auto": it.get("nome"), "anno": it.get("anno"), "km": it.get("km"), "carb": it.get("carb"),
            "fonte": it.get("fonte"), "prezzo": it.get("prezzo"), "M": pv.get("M"), "n": pv.get("n"), "metodo": pv.get("metodo"),
            "rid": pv.get("riduzione"), "R": pv.get("R_finale") or pv.get("R"), "margine": pv.get("margine"),
            "cat": pv.get("categoria"), "top": bool(pv.get("top"))}, ensure_ascii=False))


def _aggiorna_elenco(attendi: bool) -> None:
    with _elenco_lock:
        if _elenco["busy"]:
            partito = False
        else:
            _elenco["busy"] = partito = True
    if not partito:
        if attendi:                       # primo avvio: aspetta chi sta già calcolando
            while _elenco["busy"] and not _elenco["items"]:
                time.sleep(.2)
        return

    def go():
        try:
            _calcola_elenco()
        except Exception as e:
            logging.getLogger("scovo").warning("elenco non aggiornato: %s", str(e)[:200])
        finally:
            _elenco["busy"] = False
    if attendi:
        go()
    else:
        threading.Thread(target=go, daemon=True, name="elenco").start()


def _elenco_per(dealer_id: int) -> list[dict]:
    eta = time.time() - _elenco["t"]
    if not _elenco["items"]:
        _aggiorna_elenco(attendi=True)
    elif eta > ELENCO_SECONDI:
        _aggiorna_elenco(attendi=False)   # si serve subito l'elenco di prima, il nuovo arriva in sottofondo
    with connect() as conn:
        mie = {r["listing_id"] for r in conn.execute(
            "SELECT DISTINCT listing_id FROM listing_opens WHERE dealer_id=%s", (dealer_id,)).fetchall()}
    out, mx = [], settings.max_dealer_opens
    for it in _elenco["items"]:
        aperta = it["id"] in mie
        if aperta or _elenco["opens"].get(it["id"], 0) < mx:
            out.append(dict(it, aperta=True) if aperta else it)
    return out


def affari(request: Request):
    user = viewer(request, "auto")
    items = [dict(it) for it in _elenco_per(user["id"])]
    # vetrina: le auto scelte a mano (economiche, con tanto margine, quasi tutte sane), in testa all'elenco
    vet = _vetrina()
    per_id = {it["id"]: it for it in items}
    mid = [per_id[i] for i in (vet.get("ids") or []) if i in per_id][: int(vet.get("quante") or 30)]
    eco = [per_id[i] for i in (vet.get("ids_eco") or []) if i in per_id and per_id[i] not in mid][: int(vet.get("quante_eco") or 0)]
    # una sotto i 3.000 € ogni due della fascia media
    scelte, a, b = [], list(mid), list(eco)
    while a or b:
        scelte += a[:2]; a = a[2:]
        if b: scelte.append(b.pop(0))
    for n, it in enumerate(scelte):
        it["vetrina"] = n
    n = len(scelte)
    return jsonify({"items": items, "vetrina_titolo": vet.get("titolo") if n else None,
                    "policy": scovo.POLICY_VERSION if scovo.POLICY_SITO == "pv2" and scovo.POLICY_PRONTA["ok"] else None, "costi_fissi": scovo.COSTI_FISSI, "sconto": scovo.SCONTO_DEFAULT,
                    "margine_minimo": scovo.MARGINE_MINIMO, "soglie": [[l if l < 10**9 else None, m] for l, m in scovo.SOGLIE],
                    "aggiornato": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def affare(request: Request):
    user = viewer(request, "auto")
    lid = int(request.path_params["id"])
    with connect() as conn:
        row = conn.execute(scovo.LIST_SQL + " AND l.id=%(id)s", {"me": user["id"], "id": lid}).fetchone()
        eco = None
        if not row:
            eco = row = conn.execute(scovo.ECO_SQL + " AND l.id=%(id)s", dict(scovo.eco_params(user["id"]), id=lid)).fetchone()
        extra = conn.execute(scovo.DETAIL_EXTRA_SQL, (lid,)).fetchone() if row else None
        if str(lid) in _prezzi_cliente():
            # prezzo trattato: la scheda usa i dati dell'elenco, già con il prezzo nuovo e il margine giusto
            tr = next((it for it in (_elenco.get("items") or []) if it.get("id") == lid), None)
            if tr:
                extra = conn.execute(scovo.DETAIL_EXTRA_SQL, (lid,)).fetchone()
                return jsonify(scovo.detail(dict(tr), dict(extra or {}, pv_dati=None)))
        if not row:
            # auto scelte a mano (data/forzate_sito.json): la scheda viene dall'elenco del sito
            forz = next((it for it in (_elenco.get("items") or []) if it.get("id") == lid
                         and "Selezione" in str((it.get("pv") or {}).get("etichetta") or "")), None)
            if forz:
                extra = conn.execute(scovo.DETAIL_EXTRA_SQL, (lid,)).fetchone()
                d = scovo.detail(dict(forz), dict(extra or {}, pv_dati=None))
                return jsonify(d)
    if not row or not scovo.visible(row, settings.max_dealer_opens):
        err(404, "Questa auto non è più disponibile")
    base = scovo.item_eco(row) if eco else scovo.item(row)
    if not base:
        err(404, "Questa auto non è più disponibile")
    return jsonify(scovo.detail(base, extra or {}))


_contatti_ospiti: dict[str, list[float]] = defaultdict(list)
OSPITI_EMAIL = "ospiti@scovo.invalid"


def _ospiti_id(conn) -> int:
    """Un unico profilo tecnico per i visitatori senza account (conta come UN commerciante per auto)."""
    row = conn.execute("SELECT id FROM dealers WHERE email=%s", (OSPITI_EMAIL,)).fetchone()
    if row:
        return row["id"]
    row = conn.execute("INSERT INTO dealers (name, email, role, active) VALUES ('Visitatori senza account', %s, "
                       "'commerciante', false) ON CONFLICT (email) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                       (OSPITI_EMAIL,)).fetchone()
    return row["id"]


def _apri_contatto(request: Request, user: dict | None) -> dict:
    """Conta l'apertura (ospite se user è None) e restituisce url e numero dell'annuncio."""
    ospite = user is None
    if ospite:
        ip, now = client_ip(request), time.time()
        _contatti_ospiti[ip] = [t for t in _contatti_ospiti[ip] if now - t < 3600]
        if len(_contatti_ospiti[ip]) >= int(os.environ.get("CONTATTI_OSPITE_ORA", "30")):
            err(429, "Hai aperto molti contatti: riprova tra un po' oppure crea un account")
        _contatti_ospiti[ip].append(now)
    lid = int(request.path_params["id"])
    with connect() as conn:
        uid = _ospiti_id(conn) if ospite else user["id"]
        row = conn.execute(
            "SELECT url, description, (SELECT count(*) FROM listing_opens WHERE listing_id=%s) AS opens, "
            "EXISTS (SELECT 1 FROM listing_opens WHERE listing_id=%s AND dealer_id=%s) AS mine "
            "FROM listings WHERE id=%s AND status='attivo'", (lid, lid, uid, lid)).fetchone()
        if not row or (not row["mine"] and row["opens"] >= settings.max_dealer_opens):
            err(404, "Questa auto non è più disponibile")
        conn.execute("INSERT INTO listing_opens (listing_id, dealer_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                     (lid, uid))
        conn.commit()
    return {"url": row["url"], "tel": scovo.find_phone(row["description"])}


def contatto(request: Request):
    """Parla col venditore: conta l'apertura e restituisce il numero (se scritto nell'annuncio) e il link.
    Con il sito aperto anche chi non ha un account può contattare (limite per indirizzo, contro gli abusi)."""
    ospite = not request.headers.get("authorization") and site_open()
    return jsonify(_apri_contatto(request, None if ospite else richiede(request, "auto")))


def _cookie_token(request: Request) -> str | None:
    """Il sito scrive il cookie con encodeURIComponent: "=" diventa %3D e va decodificato."""
    from urllib.parse import unquote
    t = request.cookies.get(COOKIE_TOKEN)
    return unquote(t) if t else None


def vai_annuncio(request: Request):
    """Parla col venditore come link normale (nuova scheda): il sito resta aperto dov'era.
    Il token arriva dal cookie; conta l'apertura e rimanda all'annuncio originale."""
    lid = int(request.path_params["id"])
    tok = _cookie_token(request)
    if tok and not read_token(tok) and site_open():
        tok = None           # accesso vecchio nel cookie ma sito aperto: si contatta come ospite
    try:
        if tok:
            data = read_token(tok)
            if not data:
                err(401, "Accesso scaduto")
            ok, _ = sessions.check(data.get("sid"), data["id"])
            if not ok:
                err(401, "Accesso scaduto")
            if data.get("role") != "admin":
                with connect() as conn:
                    if "auto" not in permessi_di(conn, data):
                        err(403, "Nessun accesso")
                    row = conn.execute("SELECT richiede_abbonamento, abbonamento_stato, (password_hash IS NOT NULL) "
                                       "AS con_password, role FROM dealers WHERE id=%s", (data["id"],)).fetchone()
                    if not abbonamento.puo_vedere(row):
                        err(402, "Serve l'abbonamento")
            r = _apri_contatto(request, data)
        elif site_open():
            r = _apri_contatto(request, None)
        else:
            err(401, "Accesso richiesto")
    except HTTPError as e:
        msg = html_esc(e.message or "")
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>Scovo</title><body style='font:17px system-ui;padding:24px'>"
            f"<p>{msg or 'Annuncio non disponibile'}</p><p><a href='/#/auto/{lid}'>Torna a Scovo</a></p>",
            status_code=e.status)
    return RedirectResponse(r["url"], status_code=302)


# foto già lette dal database tenute in memoria (fino a FOTO_MEMORIA_MB, 48 MB): la stessa foto vista da più
# commercianti non passa più dal database
from collections import OrderedDict
_foto_mem: OrderedDict = OrderedDict()
_foto_mem_tot = [0]
FOTO_MEMORIA = int(os.environ.get("FOTO_MEMORIA_MB", "48")) * 1024 * 1024


def foto_auto(request: Request):
    lid, pos = int(request.path_params["id"]), int(request.path_params["pos"])
    if pos >= foto.MAX_PER_AUTO or not foto.check(lid, pos, request.query_params.get("s")):
        return Response(status_code=404)
    content = _foto_mem.get((lid, pos))
    if content is None:
        with connect() as conn:
            content = foto.fetch_one(conn, lid, pos)
        if content:
            _foto_mem[(lid, pos)] = content
            _foto_mem_tot[0] += len(content)
            try:
                while _foto_mem_tot[0] > FOTO_MEMORIA and _foto_mem:
                    _, vecchia = _foto_mem.popitem(last=False)
                    _foto_mem_tot[0] -= len(vecchia)
            except KeyError:
                pass
    if not content:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return Response(content, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=2592000, immutable"})


def commercianti_tel(request: Request):
    """22/10 (cliente): CSV dei piccoli commercianti di utilitarie con cellulare aziendale da AutoScout24.
    Si scarica solo con la chiave COMMERCIANTI_KEY (link dato al cliente). Nei log solo il conteggio."""
    import hmac
    chiave = os.environ.get("COMMERCIANTI_KEY") or ""
    if not chiave or not hmac.compare_digest(chiave, str(request.query_params.get("k") or "")):
        return Response(status_code=404)
    from ..marketing import commercianti_tel as ct
    with connect() as conn:
        rows = ct.righe(conn, int(os.environ.get("COMMERCIANTI_MAX_AUTO", "40")))
    logging.getLogger("dealfinder.marketing").warning("COMMERCIANTI_TEL scaricati %d", len(rows))
    return Response(ct.csv_bytes(rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=commercianti_cellulari.csv", "Cache-Control": "no-store"})


def foto_email(request: Request):
    """22/10 (cliente): foto delle auto del sito per le email ai commercianti (indirizzo fisso, senza firma),
    solo per le auto che sono sul sito in questo momento e solo le prime 4 foto."""
    lid, pos = int(request.path_params["id"]), int(request.path_params["pos"])
    if pos >= 4 or lid not in {it.get("id") for it in (_elenco.get("items") or [])}:
        return Response(status_code=404)
    request.path_params["id"], request.path_params["pos"] = lid, pos
    content = _foto_mem.get((lid, pos))
    if content is None:
        with connect() as conn:
            content = foto.fetch_one(conn, lid, pos)
    if not content:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


async def cambia_password(request: Request):
    user = current_user(request)
    data = await body(request)
    old, new = str(data.get("vecchia", ""))[:200], str(data.get("nuova", ""))[:200]
    if len(new) < 8:
        err(400, "La nuova password deve avere almeno 8 caratteri")

    def work():
        with connect() as conn:
            u = conn.execute("SELECT password_hash FROM dealers WHERE id=%s", (user["id"],)).fetchone()
            if not u or not verify_password(old, u["password_hash"]):
                return False
            conn.execute("UPDATE dealers SET password_hash=%s WHERE id=%s", (hash_password(new), user["id"]))
            conn.commit()
            sessions.revoke_all(conn, user["id"], "password_cambiata", keep=user.get("sid"))
            return True
    if not await run_in_threadpool(work):
        err(400, "La password attuale non è giusta")
    return jsonify({"ok": True})


_requests_ip: dict[str, list[float]] = defaultdict(list)


async def richiesta_accesso(request: Request):
    """Modulo pubblico "Prova Scovo": salva la richiesta, la vede l'amministratore."""
    data = await body(request)
    ip, now = client_ip(request), time.time()
    _requests_ip[ip] = [t for t in _requests_ip[ip] if now - t < 3600]
    if len(_requests_ip[ip]) >= 5:
        err(429, "Troppe richieste, riprova più tardi")
    email = str(data.get("email", "")).strip().lower()[:200]
    if "@" not in email or "." not in email.split("@")[-1]:
        err(400, "Scrivi un'email valida")
    if data.get("sito"):                      # campo nascosto: lo compilano solo i robot
        return jsonify({"ok": True})
    _requests_ip[ip].append(now)

    def work():
        with connect() as conn:
            conn.execute("INSERT INTO access_requests (name, company, email, phone, note) VALUES (%s,%s,%s,%s,%s)",
                         (str(data.get("nome") or "")[:120], str(data.get("azienda") or "")[:120], email,
                          str(data.get("telefono") or "")[:40], str(data.get("note") or "")[:500]))
            conn.commit()
    await run_in_threadpool(work)
    return jsonify({"ok": True}, 201)


def admin_requests(request: Request):
    current_user(request, admin=True)
    with connect() as conn:
        rows = conn.execute(
            "SELECT r.*, d.id AS dealer_id, d.active AS dealer_active FROM access_requests r "
            "LEFT JOIN dealers d ON d.email = r.email WHERE NOT r.handled ORDER BY r.at DESC LIMIT 200").fetchall()
    return jsonify({"items": rows})


async def admin_request_done(request: Request):
    """Richiesta gestita: se il commerciante si è iscritto con Google/Apple, lo attiva."""
    current_user(request, admin=True)
    rid = int(request.path_params["id"])
    with connect() as conn:
        r = conn.execute("UPDATE access_requests SET handled=true WHERE id=%s RETURNING email", (rid,)).fetchone()
        if r:
            conn.execute("UPDATE dealers SET active=true WHERE email=%s", (r["email"],))
        conn.commit()
    return jsonify({"ok": True})


SITE = STATIC / "scovo"
NO_CACHE = {"Cache-Control": "no-cache"}


usa_pool()                        # il sito riusa le connessioni al database

_index_cache: dict = {}


def index(request: Request):
    """Pagina del sito, con la versione dei file nel link (il telefono non tiene file vecchi)."""
    files = [SITE / "index.html", SITE / "app.js", SITE / "app.css", SITE / "home.css"]
    key = tuple(f.stat().st_mtime_ns for f in files)
    if _index_cache.get("key") != key:
        import hashlib
        ver = hashlib.sha256(b"".join(f.read_bytes() for f in files[1:])).hexdigest()[:10]
        from . import seo
        verif = os.environ.get("GOOGLE_SITE_VERIFICATION")
        testa = (f'<link rel="canonical" href="{seo.BASE}/">'
                 + (f'<meta name="google-site-verification" content="{verif}">' if verif else "") + seo.jsonld_home())
        _index_cache.update(key=key, html=files[0].read_text().replace("__V__", ver).replace("<!--SEO-->", testa))
    return HTMLResponse(_index_cache["html"], headers=NO_CACHE)


def service_worker(request: Request):
    return FileResponse(SITE / "sw.js", media_type="text/javascript",
                        headers={**NO_CACHE, "Service-Worker-Allowed": "/"})


def manifest(request: Request):
    return FileResponse(SITE / "manifest.webmanifest", media_type="application/manifest+json")


def pagina(name: str):
    def handler(request: Request):
        return FileResponse(SITE / f"{name}.html", headers=NO_CACHE)
    return handler


def gestione(request: Request):
    return FileResponse(STATIC / "gestione" / "index.html", headers=NO_CACHE)


def vetrina(request: Request):
    """23/10: siti vetrina dei commercianti (data/vetrine/<nome>.html)."""
    nome = request.path_params["nome"]
    f = Path(__file__).resolve().parents[2] / "data" / "vetrine" / f"{nome}.html"
    if not re.fullmatch(r"[a-z0-9-]{2,40}", nome) or not f.exists():
        return Response("Pagina non trovata", status_code=404)
    return FileResponse(f, media_type="text/html; charset=utf-8", headers={"cache-control": "public, max-age=600"})


def vetrina_pagina(request: Request):
    """23/10: pagine SEO dei siti vetrina (data/vetrine/<nome>/<pagina>.html e sitemap.xml)."""
    nome, pagina = request.path_params["nome"], request.path_params["pagina"]
    if not re.fullmatch(r"[a-z0-9-]{2,40}", nome) or not re.fullmatch(r"[a-z0-9-]{2,80}(\.xml|\.webp|\.jpg|\.png)?", pagina):
        return Response("Pagina non trovata", status_code=404)
    d = Path(__file__).resolve().parents[2] / "data" / "vetrine" / nome
    if pagina.endswith((".webp", ".jpg", ".png")):  # 27/10: foto reali del cliente
        f = d / "img" / pagina
        tipo = {"webp": "image/webp", "jpg": "image/jpeg", "png": "image/png"}[pagina.rsplit(".", 1)[1]]
        return (FileResponse(f, media_type=tipo, headers={"cache-control": "public, max-age=86400"})
                if f.exists() else Response("Pagina non trovata", status_code=404))
    if pagina.endswith(".xml"):
        f = d / pagina
        return FileResponse(f, media_type="application/xml") if f.exists() else Response("Pagina non trovata", status_code=404)
    f = d / f"{pagina}.html"
    if not f.exists():
        return Response("Pagina non trovata", status_code=404)
    return FileResponse(f, media_type="text/html; charset=utf-8", headers={"cache-control": "public, max-age=600"})


def robots(request: Request):
    from . import seo
    return Response("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /gestione\nDisallow: /auth/\n"
                    "Disallow: /incentivi/\n"
                    f"Sitemap: {seo.BASE}/sitemap.xml\nSitemap: {seo.BASE}/vetrina/marconi1/sitemap.xml\n", media_type="text/plain")


def sitemap(request: Request):
    from . import seo
    urls = "".join(f"<url><loc>{seo.BASE}{p}</loc><lastmod>{seo.OGGI}</lastmod>"
                   f"<changefreq>{'daily' if p.startswith('/auto') or p == '/' else 'monthly'}</changefreq>"
                   f"<priority>{'1.0' if p == '/' else '0.8' if p.startswith('/auto') else '0.5'}</priority></url>"
                   for p in seo.tutti_gli_indirizzi())
    return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    + urls + "</urlset>", media_type="application/xml")


def seo_citta(request: Request):
    from . import seo
    h = seo.pagina_citta(request.path_params["citta"])
    return HTMLResponse(h, headers={"Cache-Control": "public, max-age=3600"}) if h else HTMLResponse("Pagina non trovata", 404)


def seo_argomento(slug: str):
    def handler(request: Request):
        from . import seo
        return HTMLResponse(seo.pagina_argomento(slug), headers={"Cache-Control": "public, max-age=3600"})
    return handler


def seo_guida(request: Request):
    from . import seo
    h = seo.pagina_guida(request.path_params["guida"])
    return HTMLResponse(h, headers={"Cache-Control": "public, max-age=86400"}) if h else HTMLResponse("Pagina non trovata", 404)


async def http_error(request: Request, exc: HTTPError):
    return JSONResponse({"error": exc.message}, status_code=exc.status)


async def server_error(request: Request, exc: Exception):
    log.exception("errore %s %s", request.method, request.url.path)
    return JSONResponse({"error": "Qualcosa è andato storto, riprova tra poco"}, status_code=500)


# ---------------------------------------------------------------------------
# Sicurezza: HTTPS obbligatorio, intestazioni, grandezza massima delle richieste
# ---------------------------------------------------------------------------
MAX_BODY = 12 * 1024 * 1024        # Vendi: fino a 6 foto già ridotte dal telefono
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; media-src 'self' blob:; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; manifest-src 'self'; worker-src 'self'")
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"x-frame-options", b"DENY"),
    (b"permissions-policy", b"camera=(self), geolocation=(), microphone=(self)"),
    (b"content-security-policy", CSP.encode()),
]


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app
        self.force_https = bool(os.environ.get("RENDER")) or os.environ.get("FORCE_HTTPS") == "1"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        proto = headers.get(b"x-forwarded-proto", b"").decode()
        if self.force_https and proto == "http":
            host = headers.get(b"host", b"").decode()
            qs = scope.get("query_string", b"").decode()
            url = f"https://{host}{scope['path']}" + (f"?{qs}" if qs else "")
            return await RedirectResponse(url, status_code=301)(scope, receive, send)
        try:
            if int(headers.get(b"content-length", b"0") or 0) > MAX_BODY:
                return await JSONResponse({"error": "Richiesta troppo grande: usa meno foto"}, status_code=413)(
                    scope, receive, send)
        except ValueError:
            pass
        https = self.force_https

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                h = list(message.get("headers") or [])
                if scope["path"].startswith("/vetrina/"):   # 23/10: siti vetrina dei commercianti clienti
                    h += [x for x in SECURITY_HEADERS if x[0] != b"content-security-policy"] + [(b"content-security-policy",
                          b"default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                          b"font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; frame-src https://maps.google.com https://www.google.com")]
                else:
                    h += SECURITY_HEADERS
                if https:
                    h.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                if scope["path"].startswith("/api/") and not scope["path"].startswith("/api/foto/"):
                    h.append((b"cache-control", b"no-store"))
                message["headers"] = h
            await send(message)
        await self.app(scope, receive, send_wrapper)


def _incentivi_app():
    """Modulo Incentivi (prototipo), applicazione a sé montata su /incentivi."""
    import incentivi.web as incw
    incw.controllo_accesso = _accesso_incentivi
    return incw.app


COOKIE_TOKEN = "scovo_t"


def _accesso_incentivi(request: Request) -> tuple[bool, int, str]:
    """Il browser apre /incentivi come pagina normale: il token arriva dal cookie che il sito imposta all'accesso."""
    auth = request.headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else _cookie_token(request)
    data = read_token(tok)
    if not data:
        return False, 401, "Accesso richiesto"
    ok, why = sessions.check(data.get("sid"), data["id"])
    if not ok:
        return False, 401, sessions.REVOKED_MSG.get(why or "", "Accesso scaduto: entra di nuovo")
    if data.get("role") == "admin":
        return True, 200, ""
    with connect() as conn:
        if "incentivi" in permessi_di(conn, data):
            return True, 200, ""
    return False, 403, "Il tuo account non comprende gli Incentivi. Chiedi all'amministratore di attivarli"


routes = [
    Route("/health", health),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/metodi", auth_metodi),
    Route("/api/abbonamento/checkout", abbonamento_checkout, methods=["POST"]),
    Route("/api/abbonamento/portale", abbonamento_portale, methods=["POST"]),
    Route("/api/stripe/webhook", stripe_webhook, methods=["POST"]),
    Route("/api/auth/scambio", auth_scambio, methods=["POST"]),
    Route("/api/auth/esci", esci, methods=["POST"]),
    Route("/auth/{provider}", auth_start),
    Route("/auth/{provider}/callback", auth_callback, methods=["GET", "POST"]),
    Route("/api/me", me, methods=["GET"]),
    Route("/api/me", update_me, methods=["PUT"]),
    Route("/api/me/password", cambia_password, methods=["PUT"]),
    Route("/api/affari", affari),
    Route("/api/affari/{id:int}", affare),
    Route("/api/affari/{id:int}/contatto", contatto, methods=["POST"]),
    Route("/api/affari/{id:int}/vai", vai_annuncio),
    Route("/api/foto/{id:int}/{pos:int}", foto_auto),
    Route("/email-foto/{id:int}/{pos:int}.jpg", foto_email),
    Route("/admin/commercianti-tel.csv", commercianti_tel),
    Route("/api/richiesta-accesso", richiesta_accesso, methods=["POST"]),
    Route("/api/opportunities", opportunities),
    Route("/api/opportunities/{id:int}", opportunity_detail),
    Route("/api/opportunities/{id:int}/open", open_listing, methods=["POST"]),
    Route("/api/opportunities/{id:int}/feedback", feedback, methods=["POST"]),
    Route("/api/affari/{id:int}/esito", feedback, methods=["POST"]),
    Route("/api/activity", my_activity),
    Route("/api/notifications", notifications),
    Route("/api/notifications/seen", notifications_seen, methods=["POST"]),
    Route("/api/admin/overview", admin_overview),
    Route("/api/admin/dealers", admin_dealers, methods=["GET"]),
    Route("/api/admin/dealers", admin_create_dealer, methods=["POST"]),
    Route("/api/admin/dealers/{id:int}", admin_update_dealer, methods=["PATCH"]),
    Route("/api/admin/dealers/{id:int}/link", admin_link, methods=["POST"]),
    Route("/api/admin/richieste", admin_requests),
    Route("/api/admin/richieste/{id:int}", admin_request_done, methods=["POST"]),
    Route("/api/assistente", assistente, methods=["POST"]),
    Route("/api/voce", voce_tts, methods=["POST"]),
    Route("/api/trascrivi", voce_stt, methods=["POST"]),
    Route("/api/servizi/vendi", servizio_vendi, methods=["POST"]),
    Route("/sw.js", service_worker),
    Route("/manifest.webmanifest", manifest),
    Route("/robots.txt", robots),
    Route("/vetrina/{nome}", vetrina),
    Route("/vetrina/{nome}/{pagina}", vetrina_pagina),
    Route("/auto-usate/{citta}", seo_citta),
    Route("/guide/{guida}", seo_guida),
    Route("/auto-sotto-prezzo-milano", seo_argomento("auto-sotto-prezzo-milano")),
    Route("/auto-incidentate-milano", seo_argomento("auto-incidentate-milano")),
    Route("/auto-economiche-sotto-1000-euro-milano", seo_argomento("auto-economiche-sotto-1000-euro-milano")),
    Route("/comprare-auto-da-privati-per-rivendere", seo_argomento("comprare-auto-da-privati-per-rivendere")),
    Route("/valutazione-auto-usata-gratis", seo_argomento("valutazione-auto-usata-gratis")),

    Route("/sitemap.xml", sitemap),
    Route("/chi-siamo", pagina("chi-siamo")),
    Route("/privacy", pagina("privacy")),
    Route("/condizioni", pagina("condizioni")),
    Route("/gestione", gestione),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
    Route("/incentivi", lambda r: RedirectResponse("/incentivi/", status_code=308)),
    Mount("/incentivi", app=_incentivi_app()),
    Route("/", index),
]

def _pulizia_disco():
    """Libera spazio nel database (disco pieno il 12/10): toglie il testo originale della fonte (raw) agli annunci
    non Facebook e le foto in cache delle auto non più sul sito, a blocchi piccoli, poi VACUUM. Una volta sola."""
    import threading

    def go():
        lg = logging.getLogger("pulizia")
        try:
            import psycopg
            from ..config import settings
            from ..db import log_job
            with connect() as conn:
                if conn.execute("SELECT 1 FROM job_runs WHERE job='pulizia:disco:v1' AND ok").fetchone():
                    return
                finish = log_job(conn, "pulizia:disco:v1")
                tot = 0
                while True:
                    n = conn.execute("""UPDATE listings SET raw = NULL WHERE id IN (SELECT id FROM listings
                                        WHERE raw IS NOT NULL AND source <> 'facebook' LIMIT 5000)""").rowcount
                    conn.commit()
                    tot += n
                    if n == 0:
                        break
                lg.warning("PULIZIA raw tolti: %d", tot)
                foto = conn.execute("""DELETE FROM photo_cache WHERE listing_id NOT IN (SELECT id FROM listings
                                       WHERE status='attivo' AND (stage='approfondito' OR source='facebook'))""").rowcount
                conn.commit()
                lg.warning("PULIZIA foto in cache tolte: %d", foto)
                finish(True, {"raw": tot, "foto": foto})
            with psycopg.connect(settings.database_url, autocommit=True) as c:
                for t in ("listings", "photo_cache", "price_events", "valuations"):
                    c.execute(f"VACUUM {t}")
                    lg.warning("PULIZIA vacuum %s fatto", t)
        except Exception as e:
            lg.warning("pulizia non riuscita: %s", str(e)[:300])
    threading.Thread(target=go, daemon=True, name="pulizia").start()


def _aggiorna_visibili():
    """In sottofondo (ogni 30 minuti): le auto approvate e viste dalla raccolta nelle ultime 48 ore restano visibili,
    anche quando il ciclo notturno è in ritardo. Non blocca mai l'avvio del sito."""
    import threading

    def loop():
        from ..pipeline import aggiunte, manuale
        try:
            # prima di tutto (è veloce): le auto approvate viste di recente tornano visibili
            with connect() as conn:
                logging.getLogger("visibili").warning("VISIBILI subito: %s", manuale.segna_viste_di_recente(conn))
        except Exception as e:
            logging.getLogger("visibili").warning("auto visibili non aggiornate: %s", str(e)[:200])
        try:
            logging.getLogger("visibili").warning("AGGIUNTE: %s", aggiunte.run())
        except Exception as e:
            logging.getLogger("visibili").warning("aggiunte non importate: %s", str(e)[:300])
        try:
            # sezione Incidentate: le auto scelte a mano entrano subito, senza aspettare il ciclo
            # auto controllate a mano e non ancora importate (nuove selezioni, Incidentate): entrano subito
            logging.getLogger("visibili").warning("NUOVE importate: %s", manuale.run(solo_nuove=True))
        except Exception as e:
            logging.getLogger("visibili").warning("incidentate non importate: %s", str(e)[:200])
        while True:
            try:
                with connect() as conn:
                    manuale.segna_viste_di_recente(conn)
                    aggiunte.tieni_visibili(conn)
            except Exception as e:
                logging.getLogger("visibili").warning("aggiornamento auto visibili non riuscito: %s", str(e)[:200])
            time.sleep(1800)
    threading.Thread(target=loop, daemon=True, name="visibili").start()


def _azzera_no_subito():
    """Una volta: i "senza numero" di Subito decisi solo dal testo tornano "da controllare" (il bottone Chiama si vede
    solo nella pagina)."""
    try:
        with connect() as conn:
            if not conn.execute("SELECT 1 FROM job_runs WHERE job='subito:telefono:azzera:v2' AND ok").fetchone():
                from ..db import log_job
                fine = log_job(conn, "subito:telefono:azzera:v2")
                n = conn.execute("UPDATE listings SET telefono_ok=NULL WHERE source='subito' AND telefono_ok=false").rowcount
                conn.commit()
                fine(True, {"azzerati": n})
                logging.getLogger("tempo_reale").warning("TR subito: %d 'senza numero' tornano da controllare", n)
    except Exception as e:
        logging.getLogger("tempo_reale").warning("azzeramento telefoni subito: %s", str(e)[:200])


_misure = {"t": 0.0}


def _telefoni_scelte():
    """Ultimo passo dell'imbuto: numero di telefono controllato UNA volta, solo sulle auto AutoScout24 appena scelte."""
    from ..pipeline import tempo_reale as tr
    lg = logging.getLogger("tempo_reale")
    try:
        t0 = time.time()
        st = tr.con_lucchetto("telefoni", tr.controlla_telefoni)
        if st:
            lg.warning("TR TELEFONI SCELTE in %.0fs: %s", time.time() - t0, dict(st))
    except Exception as e:
        lg.warning("TR telefoni scelte non riuscito: %s", str(e)[:300])


def _tempo_reale():
    """Due agenti continui (pipeline/tempo_reale.py): VERIFICA delle auto sul sito e NUOVI annunci, ogni 10 minuti."""
    from ..pipeline import tempo_reale as tr
    lg = logging.getLogger("tempo_reale")

    def verifica():
        time.sleep(int(os.environ.get("TR_RITARDO_S", "90")))
        while True:
            t0 = time.time()
            try:
                ids = [it["id"] for it in _elenco["items"] if it.get("fonte") in ("Subito", "AutoScout24", "Facebook Marketplace")]
                st = tr.con_lucchetto("verifica", tr.giro_verifica, ids)
                if st is not None:
                    spariti = set(st.pop("_spariti", []) or [])
                    if spariti:                    # tolte subito dall'elenco, senza aspettare il ricalcolo
                        _elenco["items"] = [it for it in _elenco["items"] if it["id"] not in spariti]
                    _elenco["t"] = 0.0
                    _aggiorna_elenco(attendi=False)
                    lg.warning("TR VERIFICA %d auto in %.0fs, tolte %d: %s", len(ids), time.time() - t0, len(spariti), dict(st))
                    if time.time() - _misure["t"] > 6 * 3600:          # ogni 6 ore: come stanno andando le stime
                        _misure["t"] = time.time()
                        with connect() as conn:
                            lg.warning("MISURA_STIME ultimi %s giorni: %s", os.environ.get("MISURA_GIORNI", "3"), tr.riepilogo_misure(conn))
            except Exception as e:
                lg.warning("TR verifica non riuscita: %s", str(e)[:300])
            time.sleep(max(30, float(os.environ.get("TR_VERIFICA_MIN", "10")) * 60 - (time.time() - t0)))

    def nuovi():
        time.sleep(int(os.environ.get("TR_RITARDO_S", "90")) + 30)
        ultimo_completo = 0.0
        while True:
            t0 = time.time()
            try:
                # ogni TR_COMPLETO_MIN minuti (e al primo giro) si rilegge TUTTO il nuovo delle ultime 24 ore
                completo = (t0 - ultimo_completo >= float(os.environ.get("TR_COMPLETO_MIN", "60")) * 60
                            and not _calcolo["in_corso"] and os.environ.get("TR_COMPLETO", "1") == "1")
                _calcolo["completo"] = completo
                try:
                    st = tr.con_lucchetto("nuovi", tr.giro_nuovi, completo)
                finally:
                    _calcolo["completo"] = False
                if st is not None and completo:
                    ultimo_completo = t0
                if st is not None:
                    if st.get("pubblicate"):
                        _telefoni_scelte()
                        _elenco["t"] = 0.0
                        _aggiorna_elenco(attendi=False)
                    lg.warning("TR NUOVI in %.0fs: %s", time.time() - t0, dict(st))
            except Exception as e:
                lg.warning("TR nuovi non riuscito: %s", str(e)[:300])
            time.sleep(max(30, float(os.environ.get("TR_NUOVI_MIN", "5")) * 60 - (time.time() - t0)))

    def telefoni_subito():
        time.sleep(int(os.environ.get("TR_TEL_RITARDO_S", "60")))
        while True:
            try:
                from ..db import connect as _c
                giorno = time.strftime("%Y-%m-%d")
                with _c() as conn:
                    fatto = conn.execute("SELECT 1 FROM job_runs WHERE job=%s AND ok", ("telefoni_subito:v3:" + giorno,)).fetchone()
                if not fatto:
                    t0 = time.time()
                    with _c() as conn:
                        from ..db import log_job
                        fine = log_job(conn, "telefoni_subito:v3:" + giorno)
                        st = tr.giro_telefoni_subito(conn)
                        fine(True, st)
                    lg.warning("TR TELEFONI SUBITO in %.0fs: %s", time.time() - t0, st)
                    _tel_cache.clear()
                    _elenco["t"] = 0.0
                    _aggiorna_elenco(attendi=False)
                    if os.environ.get("UTIL_SIM", "1") == "1":     # subito il calcolo completo con i numeri nuovi
                        from ..pricing import ricalcolo
                        with _c() as conn:
                            ricalcolo.utilitarie(conn, pubblica=os.environ.get("UTIL_PUBBLICA", "1") == "1")
                        _telefoni_scelte()
                        _elenco["t"] = 0.0
                        _aggiorna_elenco(attendi=False)
            except Exception as e:
                lg.warning("TR telefoni subito non riuscito: %s", str(e)[:300])
            time.sleep(3600)

    def targhe():
        time.sleep(int(os.environ.get("TR_RITARDO_S", "90")) + 60)
        while True:
            try:
                ids = list(_targhe_da_controllare)
                st = tr.con_lucchetto("targhe", tr.giro_targhe, ids) if ids else None
                if st:
                    lg.warning("TR TARGHE: %s", dict(st))
                    if any(k in st for k in ("visibile", "coperta", "non_inquadrata", "incerta", "foto_internet")):
                        _elenco["t"] = 0.0
                        _aggiorna_elenco(attendi=False)
            except Exception as e:
                lg.warning("TR targhe non riuscito: %s", str(e)[:300])
            time.sleep(float(os.environ.get("TR_TARGHE_MIN", "5")) * 60)

    _azzera_no_subito()
    if os.environ.get("TR_TARGHE", "0") == "1":     # 25/10 (cliente): le targhe non servono, conta solo il numero
        threading.Thread(target=targhe, daemon=True, name="tr-targhe").start()
    threading.Thread(target=verifica, daemon=True, name="tr-verifica").start()
    threading.Thread(target=telefoni_subito, daemon=True, name="tr-telefoni").start()
    threading.Thread(target=nuovi, daemon=True, name="tr-nuovi").start()

    def ai_decisionale():
        """AI decisionale (TypeSafe Jev, pricing/jev.py): ogni JEV_MIN minuti sulle auto migliori non ancora decise.
        Parte solo con la chiave TYPESAFE_API_KEY; non gira mentre c'è il calcolo grande (memoria)."""
        from ..pricing import jev
        time.sleep(int(os.environ.get("JEV_RITARDO_S", "240")))
        saltati = int(os.environ.get("JEV_SALTATI_INIZIO", "8"))     # al riavvio parte subito (lotto piccolo)
        while True:
            attesa = float(os.environ.get("JEV_MIN", "60")) * 60
            try:
                # 19/10: con la base più grande il calcolo dura ~1 ora e riparte subito, e Jev non girava mai.
                # Dopo 40 minuti di attesa gira lo stesso, con un lotto piccolo (poca memoria: solo richieste e 4 foto).
                # 19/10 sera: con 38.000 auto il calcolo va in OOM (512 MB) se Jev gira insieme: di base Jev aspetta
                piccolo = _calcolo["in_corso"] and saltati >= 8 and os.environ.get("JEV_DURANTE_CALCOLO", "0") == "1"
                if jev.attiva() and _calcolo["in_corso"] and not piccolo:
                    attesa = 300                       # calcolo grande in corso: si riprova tra 5 minuti
                    saltati += 1
                elif jev.attiva():
                    saltati = 0
                    with connect() as conn:
                        if conn.execute("SELECT pg_try_advisory_lock(734105) AS ok").fetchone()["ok"]:
                            try:
                                res = jev.giro(conn, int(os.environ.get("JEV_LOTTO_PICCOLO", "60")) if piccolo else None)
                                lg.warning("JEV_RIEPILOGO 3 giorni: %s", jev.riepilogo(conn))
                                if res.get("pubblicate"):
                                    _telefoni_scelte()
                                    _elenco["t"] = 0.0
                                    _aggiorna_elenco(attendi=False)
                                if res.get("candidate", 0) >= int(os.environ.get("JEV_PER_GIRO", "300")):
                                    attesa = 120               # ce ne sono ancora: giro successivo subito
                            finally:
                                conn.rollback()
                                conn.execute("SELECT pg_advisory_unlock(734105)")
                                conn.commit()
            except Exception as e:
                lg.warning("JEV non riuscito: %s", str(e)[:300])
            time.sleep(attesa)
    threading.Thread(target=ai_decisionale, daemon=True, name="ai-decisionale").start()

    def lettore_annunci():
        """25/10 (cliente): agente lettore (ai/lettore.py) sulle auto candidate, ogni LETTORE_MIN minuti."""
        from ..ai import lettore
        time.sleep(int(os.environ.get("LETTORE_RITARDO_S", "180")))
        while True:
            try:
                with connect() as conn:
                    if conn.execute("SELECT pg_try_advisory_lock(734106) AS ok").fetchone()["ok"]:
                        try:
                            res = lettore.giro(conn)
                            if res.get("pubblicate"):
                                _elenco["t"] = 0.0
                                _aggiorna_elenco(attendi=False)
                        finally:
                            conn.execute("SELECT pg_advisory_unlock(734106)")
            except Exception as e:
                lg.warning("LETTORE non riuscito: %s", str(e)[:300])
            time.sleep(float(os.environ.get("LETTORE_MIN", "5")) * 60)

    if os.environ.get("LETTORE", "1") == "1":
        threading.Thread(target=lettore_annunci, daemon=True, name="lettore").start()

    def telefoni_candidate():
        """25/10 (cliente: "più auto sul sito"): controlla il numero delle auto tolte dal sito solo perché il numero
        non era mai stato letto; quelle con il numero tornano sul sito al giro dopo."""
        from ..pipeline import tempo_reale as tr
        time.sleep(int(os.environ.get("TEL_CAND_RITARDO_S", "240")))
        while True:
            try:
                ids = list(_tel_da_controllare)
                if ids:
                    with connect() as conn:
                        st = tr.telefoni_candidate(conn, ids)
                    lg.warning("TEL_CANDIDATE %s", dict(st))
                    if any(k.endswith(":si") for k in st):
                        _tel_cache.clear()
                        _elenco["t"] = 0.0
                        _aggiorna_elenco(attendi=False)
            except Exception as e:
                lg.warning("TEL_CANDIDATE non riuscito: %s", str(e)[:300])
            time.sleep(float(os.environ.get("TEL_CAND_MIN", "10")) * 60)

    def link_annunci():
        """25/10: link originali (pagina pubblica dell'annuncio) delle auto in LOG_URL_IDS, per il cliente."""
        ids = [int(x) for x in os.environ.get("LOG_URL_IDS", "").split(",") if x.strip().isdigit()]
        if not ids:
            return
        time.sleep(30)
        try:
            with connect() as conn:
                for r in conn.execute("SELECT id, source, status, price_eur, url FROM listings WHERE id = ANY(%s)",
                                      (ids,)).fetchall():
                    lg.warning("URL_ANNUNCIO %s %s %s %s € %s", r["id"], r["source"], r["status"], r["price_eur"], r["url"])
        except Exception as e:
            lg.warning("URL_ANNUNCIO non riuscito: %s", str(e)[:200])

    threading.Thread(target=link_annunci, daemon=True, name="link_annunci").start()

    def diag_motivi():
        """26/10: perché tante auto restano "dati insufficienti" (63% delle valutate): conteggio dei motivi."""
        if os.environ.get("DIAG_MOTIVI", "0") != "1":
            return
        time.sleep(45)
        try:
            with connect() as conn:
                rows = conn.execute(r"""
                    SELECT regexp_replace(COALESCE(u.dati->'motivi'->>0, '?'), '\d+', 'N', 'g') AS m,
                           COALESCE(l.seller_type, 'privato') AS st, count(*) AS n
                    FROM (SELECT DISTINCT ON (listing_id) listing_id, categoria, dati FROM valutazioni_policy
                          WHERE computed_at > now() - interval '30 hours' ORDER BY listing_id, computed_at DESC) u
                    JOIN listings l ON l.id = u.listing_id AND l.status = 'attivo'
                    WHERE u.categoria = 'dati_insufficienti' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30""").fetchall()
            lg.warning("DIAG_MOTIVI %s", json.dumps([[r["m"][:110], r["st"], r["n"]] for r in rows], ensure_ascii=False))
        except Exception as e:
            lg.warning("DIAG_MOTIVI non riuscito: %s", str(e)[:200])

    threading.Thread(target=diag_motivi, daemon=True, name="diag_motivi").start()

    def rivaluta_pochi_confronti():
        """26/10 (cliente: "servono molte più auto"): una volta, subito, rivaluta con la ricerca allargata
        (anno ±2, km ±50.000) le auto di privati rimaste "dati insufficienti" per pochi confronti, a blocchi."""
        if os.environ.get("RIVALUTA_POCHI", "0") != "1":
            return
        time.sleep(90)
        try:
            from collections import Counter
            from ..pipeline.tempo_reale import valuta_e_pubblica
            with connect() as conn:
                ids = [r["listing_id"] for r in conn.execute("""
                    SELECT u.listing_id FROM (SELECT DISTINCT ON (listing_id) listing_id, categoria, dati
                          FROM valutazioni_policy WHERE computed_at > now() - interval '40 hours'
                          ORDER BY listing_id, computed_at DESC) u
                    JOIN listings l ON l.id = u.listing_id AND l.status = 'attivo'
                     AND COALESCE(l.seller_type, 'privato') <> 'commerciante'
                    WHERE u.categoria = 'dati_insufficienti' AND u.dati::text LIKE '%%confronti validi%%'""").fetchall()]
            tot = Counter()
            for k in range(0, len(ids), 150):
                with connect() as conn:
                    st = Counter()
                    valuta_e_pubblica(conn, ids[k:k + 150], st)
                    conn.commit()
                    tot.update(st)
                time.sleep(2)
            lg.warning("RIVALUTA_POCHI %d auto rivalutate: %s", len(ids), dict(tot))
            _elenco["t"] = 0.0
            _aggiorna_elenco(attendi=False)
        except Exception as e:
            lg.warning("RIVALUTA_POCHI non riuscito: %s", str(e)[:300])

    threading.Thread(target=rivaluta_pochi_confronti, daemon=True, name="rivaluta_pochi").start()

    def rivaluta_sito_vecchie():
        """26/10 (cliente: "c'erano tantissime auto valide sul sito, perché le hai tolte? ricontrollale"): una volta,
        le auto di privati che il 24 e il 25/10 erano opportunità/candidate si rivalutano col metodo di oggi; nei log
        quante tornano e, per le altre, il motivo."""
        if os.environ.get("RIVALUTA_VECCHIE", "0") != "1":
            return
        time.sleep(60)
        try:
            from collections import Counter
            from ..pipeline.tempo_reale import valuta_e_pubblica
            with connect() as conn:
                ids = [r["listing_id"] for r in conn.execute("""
                    SELECT DISTINCT v.listing_id FROM valutazioni_policy v
                    JOIN listings l ON l.id = v.listing_id AND l.status = 'attivo'
                     AND COALESCE(l.seller_type, 'privato') <> 'commerciante'
                    WHERE v.computed_at >= '2026-10-24' AND v.computed_at < '2026-10-26 06:00'
                      AND v.categoria IN ('opportunita', 'candidato')""").fetchall()]
            tot = Counter()
            for k in range(0, len(ids), 150):
                with connect() as conn:
                    st = Counter()
                    valuta_e_pubblica(conn, ids[k:k + 150], st)
                    conn.commit()
                    tot.update(st)
                time.sleep(2)
            with connect() as conn:
                mot = conn.execute(r"""
                    SELECT u.categoria, regexp_replace(COALESCE(u.dati->'motivi'->>0, ''), '\d+', 'N', 'g') AS m, count(*) AS n
                    FROM (SELECT DISTINCT ON (listing_id) listing_id, categoria, dati FROM valutazioni_policy
                          WHERE listing_id = ANY(%s) ORDER BY listing_id, computed_at DESC) u
                    GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20""", (ids,)).fetchall()
            lg.warning("RIVALUTA_VECCHIE %d auto ricontrollate: %s motivi=%s", len(ids), dict(tot),
                       json.dumps([[r["categoria"], r["m"][:90], r["n"]] for r in mot], ensure_ascii=False))
            _elenco["t"] = 0.0
            _aggiorna_elenco(attendi=False)
        except Exception as e:
            lg.warning("RIVALUTA_VECCHIE non riuscito: %s", str(e)[:300])

    threading.Thread(target=rivaluta_sito_vecchie, daemon=True, name="rivaluta_vecchie").start()

    def diag_campi():
        """26/10 (cliente: "lo scraper legge male? AutoScout dà tutte le informazioni"): quanti annunci attivi hanno
        i dati che servono al confronto (kW, cambio, versione/cilindrata, km, anno), per sito."""
        if os.environ.get("DIAG_CAMPI", "0") != "1":
            return
        time.sleep(40)
        try:
            with connect() as conn:
                rows = conn.execute(r"""
                    SELECT source, COALESCE(seller_type,'?') AS st, count(*) AS n,
                      round(100.0*avg((power_kw IS NOT NULL)::int)) AS kw,
                      round(100.0*avg((gearbox IS NOT NULL)::int)) AS cambio,
                      round(100.0*avg((version_raw IS NOT NULL AND version_raw <> '')::int)) AS versione,
                      round(100.0*avg((COALESCE(version_raw,'') || ' ' || COALESCE(title,'') ~ '\m\d\.\d\M')::int)) AS cilindrata,
                      round(100.0*avg((power_kw IS NOT NULL OR (COALESCE(version_raw,'') || ' ' || COALESCE(title,'') ~ '\m\d\.\d\M'))::int)) AS motore,
                      round(100.0*avg((mileage_km IS NOT NULL)::int)) AS km, round(100.0*avg((year IS NOT NULL)::int)) AS anno,
                      round(100.0*avg((fuel IS NOT NULL)::int)) AS carb
                    FROM listings WHERE status='attivo' AND last_seen_at > now() - interval '14 days'
                    GROUP BY 1, 2 ORDER BY 3 DESC""").fetchall()
            lg.warning("DIAG_CAMPI %s", json.dumps([dict(r) for r in rows], default=str, ensure_ascii=False))
        except Exception as e:
            lg.warning("DIAG_CAMPI non riuscito: %s", str(e)[:300])

    threading.Thread(target=diag_campi, daemon=True, name="diag_campi").start()

    def icona_telefono():
        if os.environ.get("DIAG_ICONA_TEL", "0") != "1":
            return
        time.sleep(20)
        try:
            from ..pipeline import tempo_reale as tr
            tr.diag_indicatore_subito(int(os.environ.get("DIAG_ICONA_N", "4")))
        except Exception as e:
            lg.warning("ICONA_TEL non riuscito: %s", str(e)[:200])

    threading.Thread(target=icona_telefono, daemon=True, name="icona_tel").start()

    def subito_completo():
        """26/10 (cliente: "in modo permanente"): lettura completa di Subito entro 100 km ogni notte
        (SUBITO_COMPLETO=1, dopo le SUBITO_COMPLETO_ORA UTC, default 1), una volta al giorno, prima del ricalcolo delle 6."""
        if os.environ.get("SUBITO_COMPLETO", "0") != "1":
            return
        time.sleep(int(os.environ.get("SUBITO_COMPLETO_RITARDO_S", "120")))
        while True:
            try:
                from ..db import connect as _c, log_job
                from ..pipeline import tempo_reale as tr
                giorno = time.strftime("%Y-%m-%d", time.gmtime())
                if time.gmtime().tm_hour >= int(os.environ.get("SUBITO_COMPLETO_ORA", "1")):
                    with _c() as conn:
                        fatto = conn.execute("SELECT 1 FROM job_runs WHERE job=%s AND ok", ("subito_completo:" + giorno,)).fetchone()
                    if not fatto:
                        with _c() as conn:
                            fine = log_job(conn, "subito_completo:" + giorno)
                            st = tr.subito_completo(conn)
                            fine(True, st)
                        _tel_cache.clear()
                        _elenco["t"] = 0.0
                        _aggiorna_elenco(attendi=False)
            except Exception as e:
                lg.warning("SUBITO_COMPLETO non riuscito: %s", str(e)[:300])
            time.sleep(1800)

    threading.Thread(target=subito_completo, daemon=True, name="subito_completo").start()

    if os.environ.get("TEL_CANDIDATE", "1") == "1":
        threading.Thread(target=telefoni_candidate, daemon=True, name="tel_candidate").start()

    def esame_software():
        """25/10: esame del calcolo sulle auto valutate a mano (pricing/esame.py), all'avvio e ogni ESAME_ORE ore."""
        from ..pricing import esame
        time.sleep(int(os.environ.get("ESAME_RITARDO_S", "300")))
        while True:
            try:
                with connect() as conn:
                    esame.esegui(conn)
                with connect() as conn:
                    esame.varianti(conn)
                if os.environ.get("ESAME_VENDUTI", "1") == "1":
                    with connect() as conn:
                        esame.venduti(conn)
            except Exception as e:
                lg.warning("ESAME non riuscito: %s", str(e)[:300])
            time.sleep(float(os.environ.get("ESAME_ORE", "3")) * 3600)

    threading.Thread(target=esame_software, daemon=True, name="esame").start()

    def brevo_liste():
        """22/10 (cliente): carica una volta in Brevo le 6 liste dei commercianti (data/marketing_gruppi.json)."""
        if os.environ.get("BREVO_IMPORT_LISTE") != "1":
            return
        time.sleep(20)
        try:
            from ..marketing import brevo_liste as bl
            bl.importa()
            if os.environ.get("BREVO_CAMPAGNE") == "1":
                time.sleep(120)  # Brevo finisce di importare i contatti
                for _ in range(60):  # aspetta che l'elenco del sito sia pronto (le foto valgono solo per le auto pubblicate)
                    if _elenco.get("items"):
                        break
                    time.sleep(60)
                def piatte():
                    out = []
                    for it in list(_elenco.get("items") or []):
                        pv = it.get("pv") or {}
                        out.append({"id": it.get("id"), "auto": it.get("nome"), "anno": it.get("anno"), "km": it.get("km"),
                                    "carb": it.get("carb"), "prezzo": it.get("prezzo"), "n": pv.get("n"),
                                    "R": pv.get("R_finale") or pv.get("R"), "margine": pv.get("margine")})
                    return out
                bl.campagne(piatte)
        except Exception as e:
            lg.warning("BREVO_LISTE non riuscito: %s", str(e)[:300])
    threading.Thread(target=brevo_liste, daemon=True, name="brevo-liste").start()

    def lead_email_subito():
        """27/10 sera (cliente: "ci servono piu' in fretta"): cerca le email dei commercianti anche qui, subito,
        senza aspettare il cron. Solo richieste web leggere; nei log solo conteggi + righe LEADE a fine giro."""
        if os.environ.get("LEADE_SUBITO") != "1":
            return
        time.sleep(90)
        try:
            from ..pipeline import lead_email as LE
            LE.giro()
            LE.esporta()
        except Exception as e:
            lg.warning("LEADE_SUBITO non riuscito: %s", str(e)[:300])
    threading.Thread(target=lead_email_subito, daemon=True, name="lead-email").start()

    def auto_invio():
        """28/10 notte (cliente): raccolta email aggressiva + invio subito appena arrivano (marketing/auto_invio.py)."""
        if os.environ.get("AUTO_INVIO") != "1":
            return
        time.sleep(120)
        for _ in range(60):
            if _elenco.get("items"):
                break
            time.sleep(60)

        def piatte():
            out = []
            for it in list(_elenco.get("items") or []):
                pv = it.get("pv") or {}
                out.append({"id": it.get("id"), "auto": it.get("nome"), "anno": it.get("anno"), "km": it.get("km"),
                            "carb": it.get("carb"), "prezzo": it.get("prezzo"), "n": pv.get("n"), "zona": it.get("zona"),
                            "R": pv.get("R_finale") or pv.get("R"), "margine": pv.get("margine")})
            return out
        try:
            from ..marketing import auto_invio as AI
            AI.ciclo(piatte)
        except Exception as e:
            lg.warning("AUTO_INVIO non riuscito: %s", str(e)[:300])
    threading.Thread(target=auto_invio, daemon=True, name="auto-invio").start()

    def raccolta_zona():
        """28/10 (cliente: "auto sul sito tra un'ora intorno a Grumello del Monte, 50 km, da 1.000 € a infinito"):
        un giro di raccolta+valutazione+pubblicazione qui sul web, con le impostazioni RZ_* (non tocca il cron)."""
        if os.environ.get("RACCOLTA_ZONA") != "1":
            return
        time.sleep(60)
        for k, v in {"BENZ_CAP": os.environ.get("RZ_CAP", "24064"), "BENZ_RAGGIO": os.environ.get("RZ_RAGGIO", "50"),
                     "BENZ_MIN": os.environ.get("RZ_MIN", "1000"), "BENZ_PMAX": os.environ.get("RZ_MAX", "300000"),
                     "BENZ_PROVINCE": os.environ.get("RZ_PROVINCE", "BG,BS,CR,LC,MI,MB"), "BENZ_PASSO": os.environ.get("RZ_PASSO", "1000"),
                     "BENZ_SOLO_PRIVATI": "1", "BENZ_RACCOLTA_TUTTI": "0", "BENZ_SUB_PROFONDA": "0", "BENZ_TUTTI_CARB": "1",
                     "BENZ_DAL": os.environ.get("RZ_DAL", "2026-09-01 00:00"), "BENZ_SUB_PAGINE": os.environ.get("RZ_SUB_PAGINE", "200"),
                     "BENZ_MAX": os.environ.get("RZ_CANDIDATE", "1500"), "BENZ_SOGLIA": os.environ.get("RZ_SOGLIA", "0.9"),
                     "BENZ_PARALLELI": os.environ.get("RZ_PARALLELI", "4"),
                     "BENZ_SOLO_VALUTA": os.environ.get("RZ_SOLO_VALUTA", "0"), "BENZ_VAL_PEZZO": os.environ.get("RZ_VAL_PEZZO", "80"),
                     "BENZ_TEL_PARALLELI": os.environ.get("RZ_TEL_PARALLELI", "3"),
                     "BENZ_G_MAX": os.environ.get("RZ_G_MAX", "300000")}.items():
            os.environ[k] = v
        try:
            from ..pipeline import benzina_mi as B
            esito = B.giro()
            lg.warning("RACCOLTA_ZONA fatta: %s", {k: v for k, v in esito.items() if isinstance(v, int)})
            _aggiorna_elenco(False)
        except Exception as e:
            lg.warning("RACCOLTA_ZONA non riuscita: %s", str(e)[:300])
    threading.Thread(target=raccolta_zona, daemon=True, name="raccolta-zona").start()

    def casi_studio():
        """28/10 (cliente): casi studio per la presentazione: auto di fascia alta passate sul sito e vendute presto."""
        if os.environ.get("CASI_STUDIO") != "1":
            return
        time.sleep(20)
        try:
            from . import foto as FO
            with connect() as conn:
                rows = conn.execute(
                    """SELECT m.listing_id AS id, m.make, m.model, m.anno, m.km, m.prezzo_iniziale, m.prezzo_finale, m.ribassi,
                              m.mercato_m, m.rivendita_r, m.margine, m.n_confronti, m.metodo,
                              round(m.giorni_online, 1) AS giorni_online, round(m.giorni_sul_sito, 1) AS giorni_sito,
                              m.sparita_at::date AS sparita, l.title, l.province, l.city, l.fuel, l.seller_type, l.source,
                              (SELECT count(*) FROM listing_photos p WHERE p.listing_id = m.listing_id) AS n_foto
                       FROM misure_vendite m JOIN listings l ON l.id = m.listing_id
                       WHERE m.prezzo_finale >= %s AND m.margine > 0
                       ORDER BY m.margine DESC LIMIT 40""", (int(os.environ.get("CASI_DA", "20000")),)).fetchall()
            for r in rows:
                d = {k: (v if isinstance(v, (int, float, str, type(None))) else str(v)) for k, v in dict(r).items()}
                d["foto"] = [FO.url_for(r["id"], i) for i in range(min(int(r["n_foto"] or 0), 3))]
                lg.warning("CASO_STUDIO %s", json.dumps(d, default=str, ensure_ascii=False))
            lg.warning("CASI_STUDIO fatti: %d", len(rows))
            # foto per la presentazione: piccole (900 px), in base64 a pezzi nei log
            import base64, io
            from PIL import Image
            for voce in [x for x in os.environ.get("CASI_FOTO", "").split(",") if ":" in x]:
                lid, pos = (int(x) for x in voce.split(":"))
                with connect() as conn:
                    raw = FO.fetch_one(conn, lid, pos)
                if not raw:
                    lg.warning("CASO_FOTO %d:%d assente", lid, pos)
                    continue
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                im.thumbnail((900, 900))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=72)
                b = base64.b64encode(buf.getvalue()).decode()
                pezzi = [b[i:i + 6000] for i in range(0, len(b), 6000)]
                for i, pz in enumerate(pezzi):
                    lg.warning("CASO_FOTO %d:%d %d/%d %s", lid, pos, i + 1, len(pezzi), pz)
        except Exception as e:
            lg.warning("CASI_STUDIO non riusciti: %s", str(e)[:300])
    threading.Thread(target=casi_studio, daemon=True, name="casi-studio").start()

    def calibra():
        """28/10: misura una tantum della precisione della rivendita sulle auto vendute (CALIBRA=1)."""
        if os.environ.get("CALIBRA") != "1":
            return
        time.sleep(int(os.environ.get("CALIBRA_ATTESA", "120")))
        try:
            from ..pricing import calibra as CB
            with connect() as conn:
                CB.misura(conn)
        except Exception as e:
            lg.warning("CALIBRA non riuscita: %s", str(e)[:300])
    threading.Thread(target=calibra, daemon=True, name="calibra").start()

    def normalizza_db():
        """28/10 (controllo scraper): pulizia una tantum dei dati già salvati (NORMALIZZA_DB=1) e elenco dei nomi
        di modello per fonte (per unire "320"/"serie-3", "classe-a"/"a-class")."""
        if os.environ.get("NORMALIZZA_DB") != "1":
            return
        time.sleep(30)
        try:
            with connect() as conn:
                conn.execute("SET statement_timeout = '900s'")
                n1 = conn.execute("""UPDATE listings SET fuel='ibrida' WHERE fuel IS NOT NULL AND fuel <> 'ibrida' AND
                    (fuel ~* '(hybrid|ibrid)' OR (fuel ~* 'elettr' AND fuel ~* '(benz|dies|gasol)'))""").rowcount
                n2 = conn.execute("""UPDATE listings SET fuel='elettrica' WHERE fuel IS NOT NULL AND fuel <> 'elettrica'
                    AND fuel ~* '(elettr|electric|bev)' AND fuel !~* '(benz|dies|gasol|hybrid|ibrid)'""").rowcount
                n3 = conn.execute("""UPDATE listings SET fuel=lower(fuel) WHERE fuel <> lower(fuel)""").rowcount
                n4 = conn.execute("""UPDATE listings SET make=lower(replace(make,' ','-')) WHERE make <> lower(replace(make,' ','-'))""").rowcount
                n5 = conn.execute("""UPDATE listings SET model=lower(replace(model,' ','-')) WHERE model <> lower(replace(model,' ','-'))""").rowcount
                n6 = conn.execute("""UPDATE listings SET price_flags=array_remove(price_flags,'dati_incoerenti')
                    WHERE make IN ('mercedes-benz','mercedes') AND 'dati_incoerenti' = ANY(price_flags)
                    AND title ~* 'mercedes' AND title !~* '(bmw|audi|volkswagen|fiat|ford|opel|renault|peugeot|toyota)'""").rowcount
                conn.commit()
                lg.warning("NORMALIZZA_DB alimentazione ibrida %d, elettrica %d, minuscole %d, marche %d, modelli %d, "
                           "mercedes sbloccate %d", n1, n2, n3, n4, n5, n6)
                for mk in os.environ.get("NORMALIZZA_MARCHE", "bmw,mercedes-benz,mercedes,audi,volkswagen,land-rover,mini,"
                                         "fiat,alfa-romeo,porsche,volvo,peugeot,citroen,toyota,ford,opel,renault").split(","):
                    rows = conn.execute("""SELECT source, model, count(*) AS n FROM listings WHERE make=%s
                        AND first_seen_at > now() - interval '120 days' GROUP BY 1,2 HAVING count(*) >= 15
                        ORDER BY 3 DESC LIMIT 80""", (mk,)).fetchall()
                    lg.warning("MODELLI_FONTE %s %s", mk, json.dumps([[r["source"][:2], r["model"], r["n"]] for r in rows]))
                rows = conn.execute("""SELECT make, count(*) AS n FROM listings WHERE first_seen_at > now() - interval '120 days'
                    GROUP BY 1 ORDER BY 2 DESC LIMIT 80""").fetchall()
                lg.warning("MARCHE_FONTE %s", json.dumps([[r["make"], r["n"]] for r in rows]))
                st = conn.execute("""SELECT count(*) AS tot, count(*) FILTER (WHERE status='attivo') AS attivi,
                    count(*) FILTER (WHERE status='attivo' AND seller_type='privato') AS privati,
                    count(*) FILTER (WHERE status='attivo' AND upper(province) IN ('MI','MB','BG','BS','CO','CR','LC','LO','MN','PV','SO','VA','NO','PC')) AS zona,
                    count(*) FILTER (WHERE status='scomparso' AND disappeared_at > now() - interval '60 days') AS vendute60
                    FROM listings""").fetchone()
                lg.warning("CONTEGGI_DB %s", json.dumps(dict(st)))
        except Exception as e:
            lg.warning("NORMALIZZA_DB non riuscita: %s", str(e)[:300])
    threading.Thread(target=normalizza_db, daemon=True, name="normalizza-db").start()

    def normalizza_modelli():
        """28/10: stessa marca/modello tra AutoScout e Subito anche per gli annunci già salvati (NORMALIZZA_MODELLI=1)."""
        if os.environ.get("NORMALIZZA_MODELLI") != "1":
            return
        time.sleep(20)
        try:
            from ..core.normalize import canon_make, canon_model
            with connect() as conn:
                conn.execute("SET statement_timeout = '900s'")
                n_marche = 0
                for vecchia, nuova in (("mercedes", "mercedes-benz"), ("ds", "ds-automobiles"), ("dr", "dr-automobiles")):
                    n_marche += conn.execute("UPDATE listings SET make=%s WHERE make=%s", (nuova, vecchia)).rowcount
                conn.commit()
                coppie = conn.execute("""SELECT DISTINCT make, model FROM listings WHERE make IN
                    ('bmw','mercedes-benz','porsche','mini','volkswagen') AND model IS NOT NULL""").fetchall()
                n_mod, cambi = 0, {}
                for r in coppie:
                    nuovo = canon_model(r["make"], r["model"])
                    if nuovo and nuovo != r["model"]:
                        n_mod += conn.execute("UPDATE listings SET model=%s WHERE make=%s AND model=%s",
                                              (nuovo, r["make"], r["model"])).rowcount
                        cambi[f"{r['make']}:{r['model']}"] = nuovo
                        conn.commit()
                lg.warning("NORMALIZZA_MODELLI marche %d righe, modelli %d righe, %d nomi unificati: %s",
                           n_marche, n_mod, len(cambi), json.dumps(dict(list(cambi.items())[:60])))
        except Exception as e:
            lg.warning("NORMALIZZA_MODELLI non riuscita: %s", str(e)[:300])
    threading.Thread(target=normalizza_modelli, daemon=True, name="normalizza-modelli").start()

    def indice_confronti():
        """28/10 sera: i confronti ora usano anche le auto vendute e la base Italia: serve un indice su
        marca/modello/alimentazione valido per tutti gli stati (quello vecchio copre solo gli annunci attivi)."""
        if os.environ.get("INDICE_CONFRONTI", "1") != "1":
            return
        time.sleep(5)
        try:
            import psycopg
            from ..config import settings
            with psycopg.connect(settings.database_url, autocommit=True) as c:
                r = c.execute("""SELECT i.indisvalid FROM pg_class x JOIN pg_index i ON i.indexrelid = x.oid
                                 WHERE x.relname = 'listings_mmf_tutti_idx'""").fetchone()
                if r is not None and r[0]:
                    lg.warning("INDICE_CONFRONTI già pronto")
                    return
                if r is not None:                       # rimasto a metà da un riavvio: si rifà
                    c.execute("DROP INDEX CONCURRENTLY IF EXISTS listings_mmf_tutti_idx")
                    lg.warning("INDICE_CONFRONTI non valido, lo rifaccio")
                t0 = time.time()
                c.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS listings_mmf_tutti_idx ON listings (make, model, fuel)")
            lg.warning("INDICE_CONFRONTI pronto in %.0f s", time.time() - t0)
        except Exception as e:
            lg.warning("INDICE_CONFRONTI non riuscito: %s", str(e)[:300])
    threading.Thread(target=indice_confronti, daemon=True, name="indice-confronti").start()

    def commercianti_brevo():
        """22/10 (cliente): una volta all'avvio, i piccoli commercianti con cellulare nella lista Brevo (nessun invio)."""
        if os.environ.get("COMMERCIANTI_BREVO") != "1":
            return
        time.sleep(30)
        try:
            from ..marketing import commercianti_tel as ct
            with connect() as conn:
                rows = ct.righe(conn, int(os.environ.get("COMMERCIANTI_MAX_AUTO", "40")))
                if not rows:
                    lg.warning("COMMERCIANTI_TEL diagnosi %s", json.dumps(ct.diagnosi(conn), default=str)[:3000])
            ct.a_brevo(rows)
        except Exception as e:
            lg.warning("COMMERCIANTI_TEL non riuscito: %s", str(e)[:300])
    threading.Thread(target=commercianti_brevo, daemon=True, name="commercianti-brevo").start()

    def scan_49k():
        """19/10 (cliente): auto da 49.000 € in su (Porsche prima) molto sotto il prezzo delle auto uguali. Solo log."""
        if not os.environ.get("SCAN_49K"):
            return
        time.sleep(60)
        sql = """
        SELECT t.id, t.source, t.url, t.make, t.model, t.version_raw, t.year, t.mileage_km, t.fuel, t.power_kw,
               t.price_eur, t.province, t.seller_type, t.damage_class, t.price_flags,
               c.n, c.med, c.p25
        FROM listings t
        JOIN LATERAL (
          SELECT count(*) AS n, percentile_cont(.5) WITHIN GROUP (ORDER BY o.price_eur) AS med,
                 percentile_cont(.25) WITHIN GROUP (ORDER BY o.price_eur) AS p25
          FROM listings o
          WHERE o.status='attivo' AND o.id<>t.id AND o.make=t.make AND o.model=t.model
            AND o.fuel IS NOT DISTINCT FROM t.fuel AND o.year BETWEEN t.year-1 AND t.year+1
            AND o.price_eur > 5000
            AND (t.mileage_km IS NULL OR o.mileage_km BETWEEN t.mileage_km*0.6-5000 AND t.mileage_km*1.4+10000)
            AND (t.power_kw IS NULL OR o.power_kw IS NULL OR o.power_kw BETWEEN t.power_kw*0.92 AND t.power_kw*1.08)
        ) c ON true
        WHERE t.status='attivo' AND t.price_eur >= %(min)s AND t.year IS NOT NULL
          AND coalesce(t.damage_class,'') NOT IN ('grave','alto_rischio','medio')
          AND c.n >= 3 AND t.price_eur < c.med * 0.92
        ORDER BY (t.make ILIKE 'porsche') DESC, t.price_eur / c.med ASC
        LIMIT 60"""
        try:
            with connect() as conn:
                conn.execute("SET LOCAL statement_timeout = '300s'")
                tot = conn.execute("SELECT count(*) AS n, count(*) FILTER (WHERE make ILIKE 'porsche') AS p FROM listings "
                                   "WHERE status='attivo' AND price_eur >= %(min)s", {"min": int(os.environ.get("SCAN_49K_MIN", "49000"))}).fetchone()
                lg.warning("SCAN49K_BASE %s", json.dumps(dict(tot)))
                for r in conn.execute(sql, {"min": int(os.environ.get("SCAN_49K_MIN", "49000"))}).fetchall():
                    d = dict(r)
                    d["sotto_pct"] = round((1 - float(d["price_eur"]) / float(d["med"])) * 100, 1)
                    d["med"], d["p25"] = int(d["med"]), int(d["p25"])
                    lg.warning("SCAN49K %s", json.dumps(d, default=str, ensure_ascii=False))
        except Exception as e:
            lg.warning("SCAN49K errore: %s", str(e)[:300])
    threading.Thread(target=scan_49k, daemon=True, name="scan49k").start()

    def lead_dintorni():
        """19/10: lista lead una tantum (LEAD_GIRO_WEB=n): commercianti di Milano e dintorni che comprano dai privati."""
        giro = os.environ.get("LEAD_GIRO_WEB")
        if not giro:
            return
        time.sleep(180)
        from ..pipeline import lead
        from ..pipeline.ciclo import _due
        from ..db import log_job
        job = f"lead:dintorni:{giro}"
        try:
            if _due(job, 10000):
                with connect() as conn:
                    fin = log_job(conn, job)
                    fin(True, {"trovati": lead.compra_dintorni()})
        except Exception as e:
            lg.warning("LEADN non riuscito: %s", str(e)[:300])
    threading.Thread(target=lead_dintorni, daemon=True, name="lead").start()

    def lusso():
        """20/10 (cliente), una tantum (LUSSO_GIRO=n): lead di commercianti di supercar che comprano dai privati (LEADS)
        e raccolta di tutte le auto da 60.000 € in su a Milano e dintorni (LUSSO)."""
        giro = os.environ.get("LUSSO_GIRO")
        if not giro:
            return
        time.sleep(120)
        from ..pipeline import lusso as L
        from ..pipeline.ciclo import _due
        from ..db import log_job
        for nome, fn in (("lead", L.lead_supercar), ("raccolta", L.raccolta_60k)):
            job = f"lusso:{nome}:{giro}"
            try:
                if _due(job, 10000):
                    with connect() as conn:
                        fin = log_job(conn, job)
                        fin(True, {"trovati": fn()})
            except Exception as e:
                lg.warning("LUSSO %s non riuscito: %s", nome, str(e)[:300])
    threading.Thread(target=lusso, daemon=True, name="lusso").start()

    def supercar_eu():
        """28/10 sera (cliente): supercar e auto di lusso da 70.000 € in su in tutta Europa (AutoScout24, 8 paesi),
        valutate sui confronti europei col metodo del cliente; sul sito quelle di privati con margine ≥ 5.000 €.
        SUPERCAR_EU=1 avvia; si ripete ogni SUPERCAR_EU_ORE ore. SUPERCAR_SOLO_VALUTA=1: solo il calcolo."""
        if os.environ.get("SUPERCAR_EU") != "1":
            return
        time.sleep(int(os.environ.get("SUPERCAR_ATTESA", "60")))
        from ..pipeline import supercar_eu as S
        while True:
            try:
                if os.environ.get("SUPERCAR_SOLO_VALUTA") != "1":
                    S.raccolta()
                S.valuta()
                _elenco["t"] = 0                       # il sito si ricalcola subito con le nuove auto
            except Exception as e:
                lg.warning("SUPERCAR non riuscito: %s", str(e)[:300])
            time.sleep(float(os.environ.get("SUPERCAR_EU_ORE", "6")) * 3600)
    threading.Thread(target=supercar_eu, daemon=True, name="supercar_eu").start()

    def difetti_annunci():
        """28/10 sera (cliente): l'AI legge i difetti dichiarati negli annunci delle auto sul sito (DIFETTI=1, predefinito)."""
        if os.environ.get("DIFETTI", "1") != "1":
            return
        time.sleep(int(os.environ.get("DIFETTI_ATTESA", "180")))
        from ..ai import difetti as D
        while True:
            try:
                ids = [it["id"] for it in list(_elenco.get("items") or []) if isinstance(it.get("id"), int) and not it.get("supercar")]
                st = D.giro(ids)
                if st.get("difetti") or st.get("gravi"):
                    _elenco["t"] = 0                       # il sito si ricalcola togliendo il costo dei difetti
            except Exception as e:
                lg.warning("DIFETTI non riuscito: %s", str(e)[:300])
            time.sleep(int(os.environ.get("DIFETTI_MIN", "15")) * 60)
    threading.Thread(target=difetti_annunci, daemon=True, name="difetti").start()

    def danni_in_attesa():
        """29/10 (cliente: "servono molte più auto medie e basse"): gli annunci con danni scritti, fermati dalla
        valutazione nel cron (lì non c'è la chiave AI), li legge l'AI qui; quelli non gravi si valutano subito con la
        policy di sempre e il costo della riparazione si toglie dal margine sul sito (DANNI_AI=1, predefinito)."""
        if os.environ.get("DANNI_AI", "1") != "1":
            return
        time.sleep(int(os.environ.get("DANNI_ATTESA", "240")))
        from collections import Counter as _Cn
        from ..ai import difetti as D
        from ..pipeline import tempo_reale as tr
        from ..pipeline.ciclo import _due
        while True:
            try:
                with connect() as conn:
                    conn.execute("CREATE TABLE IF NOT EXISTS analisi_danni (listing_id BIGINT PRIMARY KEY, at TIMESTAMPTZ DEFAULT now())")
                    conn.commit()
                if _due("danni_ai_recupero", 24):
                    # una volta al giorno: gli annunci di privati della zona con danni scritti, mai letti dall'AI
                    from ..zone import tutte
                    from ..db import log_job
                    with connect() as conn:
                        D._tabella(conn)
                        conn.commit()
                        fin = log_job(conn, "danni_ai_recupero")
                        conn.execute("SET LOCAL statement_timeout = '300s'")
                        n = conn.execute("""INSERT INTO analisi_danni (listing_id)
                            SELECT l.id FROM listings l
                            WHERE l.status='attivo' AND l.seller_type='privato' AND l.last_seen_at > now() - interval '5 days'
                              AND upper(l.province) = ANY(%s) AND l.price_eur BETWEEN 1000 AND 50000 AND l.year >= 2007
                              AND COALESCE(l.mileage_km, 0) <= 150000
                              AND (COALESCE(l.damage_declared, false) OR (COALESCE(l.title,'') || ' ' || COALESCE(l.description,'')) ~*
                                   '(incident|sinistrat|danneggiat|grandinat|da sistemare|da riparare|da rivedere|guast|spia|frizione|distribuzione da|perde olio|cos[iì] com)')
                              AND NOT EXISTS (SELECT 1 FROM ai_difetti d WHERE d.listing_id=l.id)
                            ORDER BY l.first_seen_at DESC LIMIT %s
                            ON CONFLICT DO NOTHING""", (list(tutte()), int(os.environ.get("DANNI_RECUPERO", "800")))).rowcount
                        conn.commit()
                        fin(True, {"inseriti": n})
                    lg.warning("DANNI_AI recupero: %s annunci con danni scritti da leggere", n)
                    ids = [x["listing_id"] for x in conn.execute(
                        """SELECT a.listing_id FROM analisi_danni a JOIN listings l ON l.id=a.listing_id
                           WHERE l.status='attivo' ORDER BY a.at DESC LIMIT %s""", (int(os.environ.get("DANNI_PEZZO", "60")),)).fetchall()]
                if ids:
                    st_d = D.giro(ids)
                    st = _Cn()
                    with connect() as conn:
                        letti = [x["listing_id"] for x in conn.execute(
                            "SELECT listing_id FROM ai_difetti WHERE listing_id = ANY(%s)", (ids,)).fetchall()]
                        if letti:
                            tr.valuta_e_pubblica(conn, letti, st)
                            conn.execute("DELETE FROM analisi_danni WHERE listing_id = ANY(%s)", (letti,))
                        conn.execute("DELETE FROM analisi_danni WHERE listing_id IN (SELECT a.listing_id FROM analisi_danni a "
                                     "JOIN listings l ON l.id=a.listing_id WHERE l.status <> 'attivo')")
                        conn.commit()
                    lg.warning("DANNI_AI %d letti %s, valutazione %s", len(ids), st_d, dict(st))
                    if st.get("pubblicate"):
                        _elenco["t"] = 0
                    if letti and len(ids) >= int(os.environ.get("DANNI_PEZZO", "60")):
                        time.sleep(30)
                        continue
            except Exception as e:
                lg.warning("DANNI_AI non riuscito: %s", str(e)[:300])
            time.sleep(int(os.environ.get("DANNI_MIN", "15")) * 60)
    threading.Thread(target=danni_in_attesa, daemon=True, name="danni_ai").start()

    def rinvio_lista():
        """29/10 (cliente): lista Brevo per rimandare l'email a tutti i commercianti (RINVIO_LISTA=<nome>), una volta."""
        nome = os.environ.get("RINVIO_LISTA")
        if not nome:
            return
        time.sleep(60)
        from ..db import log_job
        from ..pipeline.ciclo import _due
        from ..marketing import rinvio
        try:
            if _due(f"rinvio:{nome}", 100000):
                with connect() as conn:
                    fin = log_job(conn, f"rinvio:{nome}")
                    fin(True, {"lista": rinvio.lista(nome)})
        except Exception as e:
            lg.warning("RINVIO_LISTA non riuscita: %s", str(e)[:300])
    threading.Thread(target=rinvio_lista, daemon=True, name="rinvio").start()

    def supercar_vere():
        """29/10 notte (cliente): l'AI controlla foto e annuncio delle supercar; sul sito solo quelle controllate (SC_CONTROLLO=1)."""
        if os.environ.get("SC_CONTROLLO", "1") != "1":
            return
        time.sleep(int(os.environ.get("SC_ATTESA", "60")))
        from ..ai import supercar_vero as SV
        while True:
            try:
                st = SV.giro(int(os.environ.get("SUPERCAR_QUANTE", "300")) + 200)
                if st.get("vere") or st.get("scartate_ai"):
                    _elenco["t"] = 0
            except Exception as e:
                lg.warning("SUPERCAR_VERO non riuscito: %s", str(e)[:300])
            time.sleep(int(os.environ.get("SC_MIN", "20")) * 60)
    threading.Thread(target=supercar_vere, daemon=True, name="supercar_vere").start()

    def benzina_mi():
        """20/10 (cliente), una tantum (BENZ_GIRO=n): auto a benzina 3.000-15.000 € in provincia di Milano (BENZ)."""
        giro = os.environ.get("BENZ_GIRO")
        if not giro:
            return
        time.sleep(90)
        for _ in range(240):                                   # mai insieme ad altri giri pesanti (memoria)
            if not any(t.name in ("leadsp", "privati60", "lusso") and t.is_alive() for t in threading.enumerate()):
                break
            time.sleep(15)
        from ..pipeline import benzina_mi as B
        from ..pipeline.ciclo import _due
        from ..db import log_job
        job = f"benzina_mi:{giro}"
        try:
            if _due(job, 10000):
                with connect() as conn:
                    fin = log_job(conn, job)
                    fin(True, B.giro())
                _telefoni_scelte()
                _elenco["t"] = 0.0
                _aggiorna_elenco(attendi=True)
                sito = [it for it in (_elenco.get("items") or []) if (it.get("carb") or "").lower().startswith("benzina")
                        and 3000 <= (it.get("prezzo") or 0) <= 15000]
                lg.warning("BENZ_SITO %d auto a benzina 3.000-15.000 € sul sito", len(sito))
        except Exception as e:
            lg.warning("BENZ non riuscito: %s", str(e)[:300])
    threading.Thread(target=benzina_mi, daemon=True, name="benzina").start()

    def privati_60k():
        """20/10 (cliente), una tantum (PRIV60_GIRO=n): solo privati, tutta la Lombardia, da 60.000 € in su (PRIV60)."""
        giro = os.environ.get("PRIV60_GIRO")
        if not giro:
            return
        time.sleep(150)
        from ..pipeline import lusso as L
        from ..pipeline.ciclo import _due
        from ..db import log_job
        job = f"privati60:{giro}"
        try:
            if _due(job, 10000):
                with connect() as conn:
                    fin = log_job(conn, job)
                    fin(True, {"tenute": L.privati_lombardia()})
        except Exception as e:
            lg.warning("PRIV60 non riuscito: %s", str(e)[:300])
    threading.Thread(target=privati_60k, daemon=True, name="privati60").start()

    def lead_sport():
        """20/10 (cliente), una tantum (LEADSP_GIRO=n): lead di commercianti di auto sportive che comprano dai privati."""
        giro = os.environ.get("LEADSP_GIRO")
        if not giro:
            return
        time.sleep(200)
        from ..pipeline import lusso as L
        from ..pipeline.ciclo import _due
        from ..db import log_job
        job = f"leadsp:{giro}"
        try:
            if _due(job, 10000):
                with connect() as conn:
                    fin = log_job(conn, job)
                    fin(True, {"trovati": L.lead_sportive()})
        except Exception as e:
            lg.warning("LEADSP non riuscito: %s", str(e)[:300])
    threading.Thread(target=lead_sport, daemon=True, name="leadsp").start()

    def fascia_lead():
        """20/10 (cliente), una tantum (FASCIA_LEAD=n): quali lead vendono auto di fascia medio-bassa (FASCIA)."""
        giro = os.environ.get("FASCIA_LEAD")
        if not giro:
            return
        time.sleep(30)
        from ..pipeline import fascia_lead as F
        from ..pipeline.ciclo import _due
        from ..db import log_job
        job = f"fascia_lead:{giro}"
        try:
            if _due(job, 10000):
                esito = F.giro()
                with connect() as conn:
                    log_job(conn, job)(True, esito)
        except Exception as e:
            lg.warning("FASCIA non riuscito: %s", str(e)[:300])
    threading.Thread(target=fascia_lead, daemon=True, name="fascia").start()

    def lead_utilitarie():
        """22/10 (cliente), una tantum (LEAD_UTILITARIE=n): piccoli commercianti di utilitarie con cellulare (LEADWA)."""
        giro = os.environ.get("LEAD_UTILITARIE")
        if not giro:
            return
        time.sleep(30)
        from ..pipeline import lead_utilitarie as LU
        from ..pipeline.ciclo import _due
        from ..db import log_job
        job = f"lead_utilitarie:{giro}"
        try:
            if _due(job, 10000):
                esito = LU.giro()
                with connect() as conn:
                    log_job(conn, job)(True, esito)
        except Exception as e:
            lg.warning("LEADWA non riuscito: %s", str(e)[:300])
    threading.Thread(target=lead_utilitarie, daemon=True, name="leadwa").start()

    def lead_tutti():
        """22/10 (cliente): tutti i commercianti con cellulare entro 50 km da Sesto (LEAD_TUTTI=n, una tantum)
        e consegna a blocchi (LEADT_ESPORTA=blocco:quanti)."""
        from ..pipeline import lead_tutti as LT
        from ..pipeline.ciclo import _due
        from ..db import log_job
        time.sleep(40)
        esp = os.environ.get("LEADT_ESPORTA") or ""
        if ":" in esp:
            job = f"lead_tutti_esporta:{esp}"
            try:
                if _due(job, 100000):
                    k = LT.esporta(int(esp.split(":")[1]))
                    with connect() as conn:
                        log_job(conn, job)(True, {"esportati": k})
            except Exception as e:
                lg.warning("LEADT esportazione non riuscita: %s", str(e)[:300])
        tag = os.environ.get("LEADA") or ""
        if tag and _due(f"lead_tutti_tutti:{tag}", 100000):
            try:
                with connect() as conn:
                    log_job(conn, f"lead_tutti_tutti:{tag}")(True, LT.esporta_tutti())
            except Exception as e:
                lg.warning("LEADA non riuscito: %s", str(e)[:300])
        giro = os.environ.get("LEAD_TUTTI")
        if not giro:
            return
        job = f"lead_tutti:{giro}"
        try:
            if _due(job, 100000):
                esito = LT.giro()
                with connect() as conn:
                    log_job(conn, job)(True, esito)
        except Exception as e:
            lg.warning("LEADT non riuscito: %s", str(e)[:300])
    threading.Thread(target=lead_tutti, daemon=True, name="leadt").start()

    def foto_dealer():
        """23/10 (cliente): foto delle auto di un commerciante per il suo sito (data/foto_dealer.json, una volta)."""
        f = Path(__file__).resolve().parents[2] / "data" / "foto_dealer.json"
        if not f.exists():
            return
        time.sleep(20)
        try:
            from ..marketing import foto_dealer as FD
            cfg = json.loads(f.read_text())
            FD.giro(cfg["pagina"], int(cfg.get("max", 10)))
        except Exception as e:
            lg.warning("FOTODEALER non riuscito: %s", str(e)[:300])
    threading.Thread(target=foto_dealer, daemon=True, name="fotodealer").start()

    def lead_comuni():
        """23/10 (cliente): saloni dei comuni del suo elenco, da AutoScout24 (LEADC=etichetta, una volta)."""
        tag = os.environ.get("LEADC")
        if not tag:
            return
        time.sleep(30)
        try:
            from ..pipeline import lead_comuni as LC
            from ..pipeline.ciclo import _due
            from ..db import log_job
            job = f"lead_comuni:{tag}"
            if _due(job, 100000):
                esito = LC.giro()
                with connect() as conn:
                    log_job(conn, job)(True, esito)
        except Exception as e:
            lg.warning("LEADC non riuscito: %s", str(e)[:300])
    threading.Thread(target=lead_comuni, daemon=True, name="leadc").start()

    def diag_plus():
        """21/10: perché i privati 60-200k risultano "dati insufficienti" (solo lettura, DIAG_PLUS=1)."""
        if os.environ.get("DIAG_PLUS") != "1":
            return
        time.sleep(40)
        try:
            with connect() as conn:
                rows = conn.execute("""
                    SELECT l.id, l.source, l.make, l.model, l.year, l.mileage_km, l.fuel, l.gearbox, l.power_kw, l.price_eur,
                           v.categoria, v.dati->'mancanti' AS mancanti, v.dati->'motivi' AS motivi, v.dati->>'n' AS n,
                           v.dati->>'n_esclusi' AS n_esclusi, v.dati->'non_equivalenti' AS non_eq, v.dati->>'livello' AS livello,
                           v.dati->'esclusi' AS esclusi
                    FROM valutazioni_policy v JOIN listings l ON l.id=v.listing_id
                    WHERE v.computed_at > now() - interval '8 hours' AND l.seller_type='privato'
                      AND l.price_eur BETWEEN 60000 AND 200000 AND v.categoria='dati_insufficienti'
                    ORDER BY v.computed_at DESC LIMIT 25""").fetchall()
            for r in rows:
                d = dict(r)
                d["esclusi"] = (d.get("esclusi") or [])[:4]
                lg.warning("DIAG_PLUS %s", json.dumps(d, default=str, ensure_ascii=False)[:1500])
            lg.warning("DIAG_PLUS_FINE %d", len(rows))
        except Exception as e:
            lg.warning("DIAG_PLUS non riuscito: %s", str(e)[:300])
    threading.Thread(target=diag_plus, daemon=True, name="diagplus").start()


_calcolo = {"in_corso": False, "completo": False}   # mentre gira il calcolo grande la lettura completa aspetta (memoria: 512 MB)


def _ricalcolo_policy():
    """Ricalcolo con la policy prudente (pricing/ricalcolo.py): subito dopo l'avvio e poi ogni POLICY_ORE ore;
    finito il giro, l'elenco del sito si ricalcola con i nuovi numeri."""
    def loop():
        time.sleep(int(os.environ.get("POLICY_RITARDO_S", "20")))
        while True:
            try:
                from ..pricing import ricalcolo
                for _ in range(120):                 # se sta girando la lettura completa, si aspetta che finisca
                    if not _calcolo["completo"]:
                        break
                    time.sleep(15)
                _calcolo["in_corso"] = True
                with connect() as conn:
                    ricalcolo.esegui(conn)
                if os.environ.get("UTIL_SIM", "1") == "1":
                    with connect() as conn:
                        ricalcolo.utilitarie(conn, pubblica=os.environ.get("UTIL_PUBBLICA", "1") == "1")
                    _telefoni_scelte()
                _calcolo["in_corso"] = False
                _elenco["t"] = 0.0
                _aggiorna_elenco(attendi=False)
            except Exception as e:
                _calcolo["in_corso"] = False
                logging.getLogger("policy").warning("ricalcolo policy non riuscito: %s", str(e)[:300])
            time.sleep(int(float(os.environ.get("POLICY_ORE", "3")) * 3600))
    threading.Thread(target=loop, daemon=True, name="policy").start()


from contextlib import asynccontextmanager


def _lavoro_subito():
    """LAVORO_SUBITO=ravenna (da Render): fa partire subito quel lavoro di raccolta in un processo a parte,
    senza aspettare il cron (un solo giro: il lavoro stesso salta se è già stato fatto)."""
    import subprocess
    import sys
    # più lavori in parallelo separati da ";" (es. "economiche,lombardia;economiche_as")
    for modo in [m.strip() for m in os.environ.get("LAVORO_SUBITO", "").split(";") if m.strip()]:
        env = dict(os.environ, COLLECT_MODO=modo, RAVENNA_SOLO_SUBITO="1", POI_RAVENNA="0",
                   RAVENNA_VERSIONE=os.environ.get("RAVENNA_VERSIONE", "ra-v2"))
        subprocess.Popen([sys.executable, "-m", "dealfinder.pipeline.collect", modo], env=env)
        logging.getLogger("lavoro").warning("LAVORO_SUBITO avviato: %s", modo)


@asynccontextmanager
async def _lifespan(app):
    if os.environ.get("DATABASE_URL"):
        _pulizia_disco()
        _aggiorna_elenco(attendi=False)       # elenco auto pronto prima del primo visitatore
    if os.environ.get("DATABASE_URL") and os.environ.get("VISIBILI_SOTTOFONDO", "1") == "1":
        _aggiorna_visibili()
    if os.environ.get("DATABASE_URL") and os.environ.get("POLICY_RICALCOLO", "1") == "1":
        _ricalcolo_policy()
    if os.environ.get("DATABASE_URL") and os.environ.get("TEMPO_REALE", "1") == "1":
        _tempo_reale()
    if os.environ.get("DATABASE_URL"):
        try:
            _lavoro_subito()
        except Exception as e:
            logging.getLogger("lavoro").warning("lavoro non avviato: %s", str(e)[:200])
    yield


app = Starlette(routes=routes, lifespan=_lifespan,
                middleware=[Middleware(SecurityMiddleware), Middleware(GZipMiddleware, minimum_size=800)],
                exception_handlers={HTTPError: http_error, 500: server_error})
