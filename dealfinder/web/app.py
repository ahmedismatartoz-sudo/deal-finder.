"""API di Deal Finder + sito web (stessa API usata in futuro dall'app mobile).

Avvio:  uvicorn dealfinder.web.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..config import settings
from ..db import connect
from . import cards
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
    if admin and data.get("role") != "admin":
        err(403, "Solo amministratori")
    return data


async def body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        err(400, "Richiesta non valida")


def jsonify(data, status=200):
    return JSONResponse(json.loads(json.dumps(data, default=str)), status_code=status)


# ---------------------------------------------------------------------------
# Accesso
# ---------------------------------------------------------------------------
_attempts: dict[str, list[float]] = defaultdict(list)


async def login(request: Request):
    data = await body(request)
    email = str(data.get("email", "")).strip().lower()
    now = time.time()
    _attempts[email] = [t for t in _attempts[email] if now - t < 900]
    if len(_attempts[email]) >= 8:
        err(429, "Troppi tentativi, riprova tra 15 minuti")
    with connect() as conn:
        u = conn.execute("SELECT * FROM dealers WHERE email=%s AND active", (email,)).fetchone()
    if not u or not verify_password(str(data.get("password", "")), u["password_hash"]):
        _attempts[email].append(now)
        err(401, "Email o password non corretti")
    return jsonify({"token": make_token(u["id"], u["role"]),
                    "user": {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"]}})


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
        }
    return jsonify(out)


def admin_dealers(request: Request):
    current_user(request, admin=True)
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.id, d.name, d.email, d.company, d.phone, d.role, d.active, d.provinces, d.created_at,
                      (SELECT count(*) FROM listing_opens o WHERE o.dealer_id=d.id) AS opens,
                      (SELECT count(*) FROM dealer_feedback f WHERE f.dealer_id=d.id AND f.status='comprata') AS bought
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
        if data.get("password"):
            if len(data["password"]) < 8:
                err(400, "Password troppo corta")
            conn.execute("UPDATE dealers SET password_hash=%s WHERE id=%s", (hash_password(data["password"]), did))
        conn.commit()
    return jsonify({"ok": True})


def health(request: Request):
    return JSONResponse({"ok": True})


def index(request: Request):
    return FileResponse(STATIC / "index.html")


async def http_error(request: Request, exc: HTTPError):
    return JSONResponse({"error": exc.message}, status_code=exc.status)


def _wrap(fn):
    """Le funzioni sincrone vengono eseguite in un thread da Starlette."""
    return fn


routes = [
    Route("/health", health),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/me", me, methods=["GET"]),
    Route("/api/me", update_me, methods=["PUT"]),
    Route("/api/opportunities", opportunities),
    Route("/api/opportunities/{id:int}", opportunity_detail),
    Route("/api/opportunities/{id:int}/open", open_listing, methods=["POST"]),
    Route("/api/opportunities/{id:int}/feedback", feedback, methods=["POST"]),
    Route("/api/activity", my_activity),
    Route("/api/notifications", notifications),
    Route("/api/notifications/seen", notifications_seen, methods=["POST"]),
    Route("/api/admin/overview", admin_overview),
    Route("/api/admin/dealers", admin_dealers, methods=["GET"]),
    Route("/api/admin/dealers", admin_create_dealer, methods=["POST"]),
    Route("/api/admin/dealers/{id:int}", admin_update_dealer, methods=["PATCH"]),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
    Route("/", index),
]

app = Starlette(routes=routes, middleware=[Middleware(GZipMiddleware, minimum_size=1000)],
                exception_handlers={HTTPError: http_error})
