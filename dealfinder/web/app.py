"""API di Deal Finder + sito web (stessa API usata in futuro dall'app mobile).

Avvio:  uvicorn dealfinder.web.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
import os
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
from ..db import connect
from . import cards, foto, scovo, sessions, social
from .auth import hash_password, make_token, read_token, verify_password

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


def site_open() -> bool:
    """SITO_APERTO=1: chiunque vede l'elenco e le schede (anteprima). Contatti, Vendi e Ricambi restano solo con accesso."""
    return os.environ.get("SITO_APERTO") == "1"


def viewer(request: Request) -> dict:
    """Utente con accesso, oppure ospite se il sito è aperto e non c'è un accesso."""
    if not request.headers.get("authorization") and site_open():
        return {"id": 0, "role": "ospite", "sid": None}
    return current_user(request)


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
            u = conn.execute("SELECT * FROM dealers WHERE email=%s AND active", (email,)).fetchone()
        return u, bool(u) and verify_password(str(data.get("password", ""))[:200], u["password_hash"])
    u, ok = await run_in_threadpool(check)
    if not ok:
        _attempts[email].append(now)
        _attempts["ip:" + ip].append(now)
        err(401, "Email o password non corretti")
    return jsonify(await run_in_threadpool(start_session, request, u, "password"))


def me(request: Request):
    user = current_user(request)
    with connect() as conn:
        u = conn.execute("SELECT id, name, email, role, company, provinces, max_purchase, accept_damage, "
                         "preferred_parts, resale_as FROM dealers WHERE id=%s", (user["id"],)).fetchone()
        c = conn.execute("SELECT * FROM dealer_costs WHERE dealer_id=%s", (user["id"],)).fetchone()
    if not u:
        err(401, "Account non trovato")
    return jsonify({**u, "costs": cards.costs_dict(cards.dealer_costs(c))})


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
                            "AND stage_reason IN ('opportunita','da_verificare') "
                            "AND last_checked_at > now() - interval '48 hours'").fetchone()["n"]
    if affari_n < 10:
        out.append(f"Solo {affari_n} auto visibili ai commercianti: controlla verifica e analisi")
    return out


def admin_dealers(request: Request):
    current_user(request, admin=True)
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.id, d.name, d.email, d.company, d.phone, d.role, d.active, d.provinces, d.created_at,
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
    pwd = str(data.get("password", ""))
    if "@" not in email or len(pwd) < 8:
        err(400, "Email valida e password di almeno 8 caratteri")
    role = "admin" if data.get("role") == "admin" else "commerciante"
    with connect() as conn:
        if conn.execute("SELECT 1 FROM dealers WHERE email=%s", (email,)).fetchone():
            err(409, "Email già registrata")
        row = conn.execute(
            "INSERT INTO dealers (name, email, company, phone, password_hash, role, provinces) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (str(data.get("name") or email)[:120], email, data.get("company"), data.get("phone"),
             hash_password(pwd), role, data.get("provinces") or ["MI", "MB", "BG", "BS"])).fetchone()
        conn.execute("INSERT INTO dealer_costs (dealer_id) VALUES (%s)", (row["id"],))
        conn.commit()
    return jsonify({"id": row["id"]}, 201)


async def admin_update_dealer(request: Request):
    current_user(request, admin=True)
    did = int(request.path_params["id"])
    data = await body(request)
    with connect() as conn:
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
    return jsonify({**social.enabled(), "aperto": site_open()})


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
    d = social.redeem(str(data.get("code", "")))
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


# ---------------------------------------------------------------------------
# Sito Scovo: affari, scheda, contatto, foto
# ---------------------------------------------------------------------------
def affari(request: Request):
    user = viewer(request)
    with connect() as conn:
        dealer = conn.execute("SELECT provinces FROM dealers WHERE id=%s", (user["id"],)).fetchone()
        prov = list((dealer or {}).get("provinces") or [])
        sql = scovo.LIST_SQL
        if prov:
            sql += " AND (l.province = ANY(%(prov)s) OR l.source='facebook' OR l.province IS NULL)"
        rows = conn.execute(sql + " LIMIT 3000", {"me": user["id"], "prov": prov}).fetchall()
    items = [it for it in (scovo.item(r) for r in rows if scovo.visible(r, settings.max_dealer_opens)) if it]
    return jsonify({"items": items, "costi_fissi": scovo.COSTI_FISSI, "sconto": scovo.SCONTO_DEFAULT,
                    "aggiornato": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def affare(request: Request):
    user = viewer(request)
    lid = int(request.path_params["id"])
    with connect() as conn:
        row = conn.execute(scovo.LIST_SQL + " AND l.id=%(id)s", {"me": user["id"], "id": lid}).fetchone()
        extra = conn.execute(scovo.DETAIL_EXTRA_SQL, (lid,)).fetchone() if row else None
    if not row or not scovo.visible(row, settings.max_dealer_opens):
        err(404, "Questa auto non è più disponibile")
    base = scovo.item(row)
    if not base:
        err(404, "Questa auto non è più disponibile")
    return jsonify(scovo.detail(base, extra or {}))


def contatto(request: Request):
    """Parla col venditore: conta l'apertura e restituisce il numero (se scritto nell'annuncio) e il link."""
    user = current_user(request)
    lid = int(request.path_params["id"])
    with connect() as conn:
        row = conn.execute(
            "SELECT url, description, (SELECT count(*) FROM listing_opens WHERE listing_id=%s) AS opens, "
            "EXISTS (SELECT 1 FROM listing_opens WHERE listing_id=%s AND dealer_id=%s) AS mine "
            "FROM listings WHERE id=%s AND status='attivo'", (lid, lid, user["id"], lid)).fetchone()
        if not row or (not row["mine"] and row["opens"] >= settings.max_dealer_opens):
            err(404, "Questa auto non è più disponibile")
        conn.execute("INSERT INTO listing_opens (listing_id, dealer_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                     (lid, user["id"]))
        conn.commit()
    return jsonify({"url": row["url"], "tel": scovo.find_phone(row["description"])})


def foto_auto(request: Request):
    lid, pos = int(request.path_params["id"]), int(request.path_params["pos"])
    if pos >= foto.MAX_PER_AUTO or not foto.check(lid, pos, request.query_params.get("s")):
        return Response(status_code=404)
    with connect() as conn:
        content = foto.fetch_one(conn, lid, pos)
    if not content:
        return Response(status_code=404, headers={"Cache-Control": "public, max-age=3600"})
    return Response(content, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=2592000, immutable"})


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


_index_cache: dict = {}


def index(request: Request):
    """Pagina del sito, con la versione dei file nel link (il telefono non tiene file vecchi)."""
    files = [SITE / "index.html", SITE / "app.js", SITE / "app.css"]
    key = tuple(f.stat().st_mtime_ns for f in files)
    if _index_cache.get("key") != key:
        import hashlib
        ver = hashlib.sha256(b"".join(f.read_bytes() for f in files[1:])).hexdigest()[:10]
        _index_cache.update(key=key, html=files[0].read_text().replace("__V__", ver))
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


def robots(request: Request):
    host = request.headers.get("host", "")
    return Response("User-agent: *\nAllow: /$\nAllow: /chi-siamo\nAllow: /privacy\nAllow: /condizioni\n"
                    "Disallow: /api/\nDisallow: /gestione\n"
                    f"Sitemap: https://{host}/sitemap.xml\n", media_type="text/plain")


def sitemap(request: Request):
    host = request.headers.get("host", "")
    urls = "".join(f"<url><loc>https://{host}{p}</loc></url>" for p in ("/", "/chi-siamo", "/privacy", "/condizioni"))
    return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    + urls + "</urlset>", media_type="application/xml")


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
       "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; manifest-src 'self'; worker-src 'self'")
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"x-frame-options", b"DENY"),
    (b"permissions-policy", b"camera=(self), geolocation=(), microphone=()"),
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
                h += SECURITY_HEADERS
                if https:
                    h.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                if scope["path"].startswith("/api/") and not scope["path"].startswith("/api/foto/"):
                    h.append((b"cache-control", b"no-store"))
                message["headers"] = h
            await send(message)
        await self.app(scope, receive, send_wrapper)


routes = [
    Route("/health", health),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/metodi", auth_metodi),
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
    Route("/api/foto/{id:int}/{pos:int}", foto_auto),
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
    Route("/api/admin/richieste", admin_requests),
    Route("/api/admin/richieste/{id:int}", admin_request_done, methods=["POST"]),
    Route("/api/servizi/vendi", servizio_vendi, methods=["POST"]),
    Route("/api/servizi/ricambi", servizio_ricambi, methods=["POST"]),
    Route("/sw.js", service_worker),
    Route("/manifest.webmanifest", manifest),
    Route("/robots.txt", robots),
    Route("/sitemap.xml", sitemap),
    Route("/chi-siamo", pagina("chi-siamo")),
    Route("/privacy", pagina("privacy")),
    Route("/condizioni", pagina("condizioni")),
    Route("/gestione", gestione),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
    Route("/", index),
]

app = Starlette(routes=routes,
                middleware=[Middleware(SecurityMiddleware), Middleware(GZipMiddleware, minimum_size=800)],
                exception_handlers={HTTPError: http_error, 500: server_error})
