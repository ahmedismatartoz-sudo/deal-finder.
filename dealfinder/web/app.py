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

from .. import regole as _RG
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


def _utente_del_token(data: dict | None, admin: bool = False) -> dict:
    """Token valido + sessione aperta. Il ruolo NON si prende dal token (dura anni): si rilegge dal database a ogni
    richiesta (con la memoria di 60 s della sessione; per le pagine admin senza memoria). Un admin declassato o
    spento perde subito l'accesso alle pagine di amministrazione."""
    if not data:
        err(401, "Accesso richiesto")
    ok, why = sessions.check(data.get("sid"), data["id"])
    if not ok:
        err(401, sessions.REVOKED_MSG.get(why or "", "Accesso scaduto: entra di nuovo"))
    data = dict(data)
    ruolo = sessions.ruolo_fresco(data["id"]) if admin else sessions.ruolo(data.get("sid"))
    data["role"] = ruolo or "commerciante"
    if admin and data["role"] != "admin":
        err(403, "Solo amministratori")
    return data


def current_user(request: Request, admin: bool = False) -> dict:
    auth = request.headers.get("authorization", "")
    return _utente_del_token(read_token(auth[7:] if auth.lower().startswith("bearer ") else None), admin)


PERMESSI = ("auto", "incentivi", "appuntamenti")   # appuntamenti: abbonati da 200 €


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
    perm = list((row or {}).get("permessi") or [])
    if "appuntamenti" not in perm:
        # 11/11 (cliente): chi paga il Max ha SEMPRE gli appuntamenti, anche se il permesso è stato tolto dal pannello
        try:
            mx = conn.execute("SELECT 1 FROM abbonamenti WHERE dealer_id=%s AND piano IN ('pro','max') "
                              "AND stato IN ('active','trialing','past_due') LIMIT 1", (user["id"],)).fetchone()
            if mx:
                perm.append("appuntamenti")
        except Exception:
            log.exception("PERMESSI_MAX")
    return perm


def richiede(request: Request, permesso: str) -> dict:
    """Accesso con credenziali e con il permesso giusto (gli amministratori hanno tutto)."""
    user = current_user(request)
    if user.get("role") != "admin":
        with connect() as conn:
            if permesso not in permessi_di(conn, user):
                err(403, "Il tuo account non ha accesso a questa sezione")
            if permesso == "auto":
                row = conn.execute("SELECT richiede_abbonamento, abbonamento_stato, abbonamento_tolleranza, "
                                   "(password_hash IS NOT NULL) AS con_password, role, paga_carta FROM dealers WHERE id=%s",
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


def _ip_pubblico(v: str) -> str | None:
    """L'indirizzo, se è un IP valido e pubblico (non privato, loopback, link-local, riservato: quelli dei proxy)."""
    import ipaddress
    v = (v or "").strip().strip('"')
    if v.startswith("[") and "]" in v:                 # [2001:db8::1]:443
        v = v[1:v.index("]")]
    elif v.count(":") == 1:                            # 1.2.3.4:5678
        v = v.split(":")[0]
    try:
        ip = ipaddress.ip_address(v)
    except ValueError:
        return None
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return None
    return str(ip)


def client_ip(request: Request) -> str:
    """IP vero del visitatore, per i limiti di frequenza (accessi, contatti, domande, voce degli ospiti).
    4/11: prima le intestazioni scritte dal proxy davanti al sito (CF-Connecting-IP, True-Client-IP, X-Real-IP), poi
    il primo indirizzo di X-Forwarded-For che non è privato o di un proxy, infine l'indirizzo della connessione."""
    for h in ("cf-connecting-ip", "true-client-ip", "x-real-ip"):
        ip = _ip_pubblico(request.headers.get(h) or "")
        if ip:
            return ip
    for parte in (request.headers.get("x-forwarded-for") or "").split(","):
        ip = _ip_pubblico(parte)
        if ip:
            return ip
    return (request.client.host if request.client else "") or "?"


# ---------------------------------------------------------------------------
# Accesso
# ---------------------------------------------------------------------------
_attempts: dict[str, list[float]] = defaultdict(list)


def _maschera_email(email: str) -> str:
    """m***@dominio.it: abbastanza per riconoscere il caso nei log, non per raccogliere gli indirizzi."""
    nome, _, dom = (email or "").partition("@")
    return (nome[:1] + "***@" + dom) if dom else "***"


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
        # 4/11: nel log nessuna informazione sulla password (né lunghezza né confronti) e l'email mascherata
        log.warning("ACCESSO_FALLITO email=%s utente=%s", _maschera_email(email),
                    "trovato" if u else "NON trovato o disattivato")
        _attempts[email].append(now)
        _attempts["ip:" + ip].append(now)
        err(401, "Email o password non corretti")
    return jsonify(await run_in_threadpool(start_session, request, u, "password"))


def me(request: Request):
    user = current_user(request)
    with connect() as conn:
        u = conn.execute("SELECT id, name, email, role, company, provinces, max_purchase, accept_damage, "
                         "preferred_parts, resale_as, lat, lon, ((richiede_abbonamento AND password_hash IS NULL OR paga_carta) AND role <> 'admin') AS richiede_abbonamento, abbonamento_stato, "
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
    user = richiede(request, "auto")   # 3/11 23:45: servono accesso E abbonamento (prima bastava l\'accesso)
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
    user = richiede(request, "auto")   # 3/11 23:45: servono accesso E abbonamento (prima bastava l\'accesso)
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
    user = richiede(request, "auto")   # 3/11 23:45: servono accesso E abbonamento (prima bastava l\'accesso)
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
    user = richiede(request, "auto")   # 3/11 23:45: servono accesso E abbonamento (prima bastava l\'accesso)
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
    user = richiede(request, "auto")   # 3/11 23:45: servono accesso E abbonamento (prima bastava l\'accesso)
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
            """SELECT d.id, d.name, d.email, d.company, d.phone, d.role, d.active, d.provinces, d.created_at, d.permessi, d.paga_carta, d.abbonamento_stato,
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
        if "paga_carta" in data:               # 10/11: deve pagare con carta prima di vedere le auto
            conn.execute("UPDATE dealers SET paga_carta=%s WHERE id=%s", (bool(data["paga_carta"]), did))
        sessions.dimentica_account(did)          # ruolo e stato riletti subito dal database
        if "active" in data:
            # disattivato_da_admin: né il pagamento Stripe né l'avvio lo riaccendono
            conn.execute("UPDATE dealers SET active=%s, disattivato_da_admin=%s WHERE id=%s",
                         (bool(data["active"]), not bool(data["active"]), did))
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
    piano = str((await body(request)).get("piano") or "base")

    def go():
        # 3/11 notte: niente secondo abbonamento se ce n'è già uno attivo, in prova o in tolleranza (database o
        # Stripe): si manda al portale di Stripe, dove si cambia piano (Base <-> Max) o carta
        with connect() as conn:
            d = conn.execute("SELECT id, email, stripe_customer, abbonamento_stato FROM dealers WHERE id=%s",
                             (user["id"],)).fetchone()
            gia = abbonamento.abbonamento_in_corso(conn, d)
        if gia:
            if not d.get("stripe_customer"):
                return {"gia_abbonato": True, "errore": "Hai già un abbonamento attivo"}
            return {"url": abbonamento.portale(d["stripe_customer"], social.base_url(request)), "portale": True,
                    "gia_abbonato": True}
        return {"url": abbonamento.checkout(d, social.base_url(request), None, piano)}
    try:
        out = await run_in_threadpool(go)
    except RuntimeError as e:
        err(502, str(e))
    if out.get("gia_abbonato"):
        log.warning("STRIPE checkout rifiutato: commerciante %s ha già un abbonamento (portale)", user["id"])
    return jsonify(out, 200 if out.get("url") else 409)


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
    user = richiede(request, "auto")          # 4/11: accesso + abbonamento attivo (prima bastava l'accesso)
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
    user = richiede(request, "auto")          # 4/11: accesso + abbonamento attivo (prima bastava l'accesso)
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
    """Assistente a voce: domanda in linguaggio naturale, risposta + auto trovate.
    4/11: restituisce auto, quindi serve accesso + abbonamento attivo (permesso "auto"); senza account solo con il
    sito aperto (SITO_APERTO=1), con il limite di domande degli ospiti."""
    user = viewer(request, "auto")
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
    bot.ELENCO = _elenco_per          # 4/11: l'assistente propone solo le auto del sito, con gli stessi numeri

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


VOCE_OSPITE_MINUTO = int(os.environ.get("VOCE_OSPITE_MINUTO", "6"))           # per indirizzo, al minuto
VOCE_OSPITI_GIORNO = int(os.environ.get("VOCE_OSPITI_GIORNO", "300"))          # tutti gli ospiti insieme, al giorno
VOCE_OSPITE_CARATTERI = int(os.environ.get("VOCE_OSPITE_CARATTERI", "300"))


def _voce_ospiti_oggi(kind: str, conta: bool) -> int:
    """Usi della voce di TUTTI gli ospiti oggi, salvati nel database (un riavvio non azzera il tetto)."""
    with connect() as conn:
        uid = _ospiti_id(conn)
        n = conn.execute("SELECT count(*) AS n FROM service_usage WHERE dealer_id=%s AND service=%s "
                         "AND at > date_trunc('day', now())", (uid, "voce_ospite_" + kind)).fetchone()["n"]
        if conta:
            conn.execute("INSERT INTO service_usage (dealer_id, service, ok) VALUES (%s,%s,true)",
                         (uid, "voce_ospite_" + kind))
        conn.commit()
    return n


def _voce_gate(request: Request, user: dict, kind: str, testo: str = "") -> None:
    """Tetti della voce (ogni chiamata costa). Ospiti (sito aperto, senza account): per indirizzo al minuto e al
    giorno, testi corti, e un tetto giornaliero per tutti gli ospiti insieme salvato nel database."""
    ospite = user.get("role") == "ospite"
    k = f"{kind}:{'ip:' + client_ip(request) if ospite else user['id']}"
    now = time.time()
    _voce_uso[k] = [t for t in _voce_uso[k] if now - t < 86400]
    lim = VOCE_LIMITE[kind] // (5 if ospite else 1)
    if len(_voce_uso[k]) >= lim:
        err(429, "Hai usato molto la voce oggi: continuo a risponderti per iscritto")
    if ospite:
        if len(testo) > VOCE_OSPITE_CARATTERI:
            err(413, "Testo troppo lungo da leggere: entra con il tuo account")
        if sum(1 for t in _voce_uso[k] if now - t < 60) >= VOCE_OSPITE_MINUTO:
            err(429, "Troppe richieste di voce: riprova tra un minuto")
        if _voce_ospiti_oggi(kind, conta=False) >= VOCE_OSPITI_GIORNO:
            err(429, "La voce per chi non ha un account è finita per oggi: entra con il tuo account")
        _voce_ospiti_oggi(kind, conta=True)
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
    _voce_gate(request, user, "tts", testo)
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


_tel_da_controllare: set = set()     # 25/10: auto tolte solo per il numero mai controllato (vedi telefoni_candidate)
_targhe_da_controllare: list = []    # 4/11: niente più auto senza numero sul sito: resta vuota (lavoro targhe fermo)


# 3/11 10:29 (cliente: "metti commercianti ma lontani, senza prezzi promo e senza leasing, fuori da Milano, non Loconte
# e quelli là, assicurati che il prezzo non sia fatto apposta per tirare su il cliente; dei commercianti solo i 40
# migliori"). Valutati col metodo nuovo (valore reale) dal lavoro commercianti_top, poi sul sito i 40 con più margine.
COMMERCIANTI_TOP = 40
COMMERCIANTI_PROVINCE_ESCLUSE = ("MI",)
COMMERCIANTI_PREZZO_MIN_SU_M = 0.75        # sotto il 75% del mercato da un commerciante: prezzo "esca", fuori
# 6/11 (verifica imbuto): tolte le parole sciolte "leasing", "noleggio", "anticipo", "con finanziamento", "incentivi":
# scattavano sulla frase di rito "possibilità di finanziamento/leasing" (184 auto fuori). Promo = prezzo legato al finanziamento.
RE_PROMO_COMMERCIANTE = (r"(promo|promozion|prezzo\s+(valido|riservat\w*|special\w*|bloccat\w*)|a\s+partire\s+da|"
                         r"(solo|vincolat\w*|valid\w*|previo|in\s+caso\s+di)\s+(a\s+|al\s+|il\s+|un\s+)?finanziament|"
                         r"\mrat[ae]\s+da\s*\d|anticipo\s+(di\s+)?(€\s*)?\d|(prezzo|offerta)\s+(in\s+|con\s+)?(leasing|noleggio)|"
                         r"prezzo\s+(con|in)\s+permuta|valid\w*\s+con\s+permuta|solo\s+con\s+permuta|permuta\s+obbligatoria|"
                         r"iva\s+esclus|\+\s*iva|"
                         r"oltre\s+iva|esclus\w*\s+(il\s+)?passaggio|passaggio\s+esclus|\+\s*passaggio|"
                         r"messa\s+su\s+strada|con\s+(gli\s+)?incentiv|rottamazion|\mbonus\M|offerta\s+valida|esauriment|"
                         r"prezzo\s+con\s|in\s+caso\s+di\s+finanz|salvo\s+approvazione|tan\s+\d|taeg)")
_FLAG_PROMO = ["leasing_o_rata", "prezzo_civetta", "importazione", "dati_incoerenti"]


def _rss_mb() -> float:
    """Memoria usata da questo processo (MB). 3/11: il sito ha 512 MB e i lavori lunghi lo mandavano fuori memoria."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except Exception:
        return 0.0


def _aspetta_memoria(L, nome: str, limite: float = 420.0) -> None:
    """Prima di ogni blocco di un lavoro lungo: se la memoria è alta si libera e si aspetta (il sito viene prima)."""
    import gc
    for tent in range(10):
        if _rss_mb() < limite:
            return
        gc.collect()
        if tent == 0 or tent % 5 == 4:
            L.warning("%s: memoria %.0f MB, aspetto prima di continuare", nome, _rss_mb())
        time.sleep(30)


def _commercianti_nomi_re() -> str:
    return "|".join(re.escape(n.strip()) for n in os.environ.get("SITO_COMMERCIANTI_NOMI", "loconte").split(",") if n.strip())


_elenco: dict = {"t": 0.0, "items": [], "opens": {}, "busy": False}
_elenco_lock = threading.Lock()
ELENCO_SECONDI = int(os.environ.get("ELENCO_SECONDI", "90"))


_calib_cache = {"t": 0.0, "f": None}


def _fattori_calibra() -> list:
    """Fattori prezzo CHIESTO → prezzo di VENDITA per fascia di M (regole.fattori_calibrazione, dall'ultima misura
    della tabella calibrazione; riserva e limiti in dealfinder/regole.py, niente variabili d'ambiente)."""
    if time.time() - _calib_cache["t"] < 1800 and _calib_cache["f"] is not None:
        return _calib_cache["f"]
    fasce = {}
    try:
        with connect() as conn:
            if conn.execute("SELECT to_regclass('calibrazione') AS t").fetchone()["t"]:
                r = conn.execute("SELECT dati FROM calibrazione ORDER BY at DESC LIMIT 1").fetchone()
                d = r["dati"] if r else None
                dati = (json.loads(d) if isinstance(d, str) else d or {})
                fasce = dati.get("fasce") or {}
                if _RG.calibrazione_vecchia(dati):
                    logging.getLogger("scovo").warning("CALIBRA_VECCHIA misura fatta con un'altra correzione anno/km: "
                                                       "va rifatta (CALIBRA=1)")
    except Exception as e:
        logging.getLogger("scovo").warning("CALIBRA fattori non letti: %s", str(e)[:120])
    out = _RG.fattori_calibrazione(fasce)
    _calib_cache.update(t=time.time(), f=out)
    logging.getLogger("scovo").warning("CALIBRA_FATTORI %s", {n: x for _, n, x in out})
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


from .sito_regole import PROVINCE_100KM  # noqa: E402  (12 province entro 100 km da Milano, una sola definizione)


def _scarti_cliente_ids() -> set:
    """23/10: auto tolte a mano per id (data/scarti_sito.json): fuori dal sito da qualunque strada arrivino."""
    from pathlib import Path
    try:
        return {int(e["id"]) for e in json.loads((Path(__file__).resolve().parents[2] / "data" / "scarti_sito.json").read_text())
                if e.get("id")}
    except Exception:
        return set()


def _supercar_righe(conn, val: dict) -> tuple[list, dict, dict]:
    rows = conn.execute(scovo.ECO_SQL.split("WHERE l.status='attivo'")[0] + " WHERE l.id = ANY(%(ids)s) AND l.status='attivo'",
                        {"me": 0, "ids": list(val)}).fetchall()
    from ..ai import supercar_vero as SV
    desc = {x["id"]: x["description"] for x in conn.execute("SELECT id, description FROM listings WHERE id = ANY(%s)", (list(val),)).fetchall()}
    return rows, desc, SV.letti(conn, list(val))


def _supercar_item(r, v: dict, corr: dict, controllo: dict, lusso: bool) -> dict | None:
    it = scovo.item_eco(r)
    if not it:
        return None
    c = controllo.get(r["id"])
    it.update(economica=False, verificare=True, mercato=int(v["m"]), top=True, supercar=True, lusso=lusso,
              zona=" · ".join(x for x in (r.get("city"), v["paese"]) if x),
              pv={"categoria": "opportunita", "etichetta": "Opportunità Supercar Europa", "M": int(v["m"]),
                  "R": int(corr["R"]), "R_finale": int(corr["R"]), "margine": int(corr["margine"]), "n": int(v["n"]),
                  "totale_costi": int(v["costi"] or 0), "metodo": "confronti Europa"})
    # controllo AI (foto vere, davvero in vendita, niente leasing in altre lingue): la regola 'jev' vuole ok=True
    it["_r"] = {"sc_ok": (bool(c["ok"]) if c else None)}
    return it


def _supercar_europa(gia: set, opens: dict, stats=None) -> list:
    """28/10 sera: auto di privati da 70.000 € in su in 8 paesi europei (tabella supercar_eu), margine sui confronti
    europei con i costi di import. 4/11: il margine minimo (10.000 € e 15% del prezzo) e il controllo AI obbligatorio
    li decide sito_regole, come per tutte le altre auto."""
    if os.environ.get("SUPERCAR_SITO", "1") != "1":
        return []
    from collections import Counter
    from ..pipeline.supercar_eu import SITO_SQL
    from ..ai import supercar_vero as SV
    out, scarti = [], Counter()
    try:
        with connect() as conn:
            if not conn.execute("SELECT to_regclass('supercar_eu') AS t").fetchone()["t"]:
                return []
            val = {r["listing_id"]: r for r in conn.execute(SITO_SQL, {"lim": int(os.environ.get("SUPERCAR_QUANTE", "300"))}).fetchall()}
            if not val:
                return []
            rows, desc, controllo = _supercar_righe(conn, val)
        for r in rows:
            if r["id"] in gia:
                continue
            v = dict(val[r["id"]])
            motivo, corr = SV.filtra(dict(r, description=desc.get(r["id"])), v, int(r.get("n_photos") or 0))
            if motivo:
                scarti[motivo] += 1
                continue
            it = _supercar_item(r, v, corr, controllo, lusso=False)
            if it:
                out.append(it)
                gia.add(it["id"])
                opens[it["id"]] = int(r["opens"] or 0)
    except Exception as ex:
        logging.getLogger("scovo").warning("SUPERCAR_SITO non caricate: %s", str(ex)[:200])
    if stats is not None:
        stats.update({"supercar:" + k: v for k, v in scarti.items()})
        stats["supercar_caricate"] += len(out)
    return out


def _supercar_lusso(gia: set, opens: dict, stats=None) -> list:
    """30/10: auto di PRIVATI da SC_LUSSO_MIN (150.000 €) in su negli 8 paesi AutoScout24 (tabella supercar_eu), con le
    regole fisse delle supercar (niente leasing/rate, almeno 5 foto, non troppo belle). 4/11 (cliente: "mai senza margine"):
    tolte le auto "sotto la media" senza margine e quelle da 1 €; margine minimo e controllo AI in sito_regole."""
    if os.environ.get("SC_LUSSO", "1") != "1":
        return []
    from collections import Counter
    from ..ai import supercar_vero as SV
    out, scarti = [], Counter()
    try:
        with connect() as conn:
            if not conn.execute("SELECT to_regclass('supercar_eu') AS t").fetchone()["t"]:
                return []
            val = {r["listing_id"]: r for r in conn.execute("""
                SELECT s.listing_id, s.m, s.r, s.n, s.margine, s.costi, s.paese
                FROM supercar_eu s JOIN listings l ON l.id = s.listing_id
                WHERE s.computed_at > now() - interval '4 days' AND l.status = 'attivo' AND l.seller_type = 'privato'
                  AND l.price_eur >= %(min)s AND s.m IS NOT NULL AND s.m > l.price_eur
                  AND s.r IS NOT NULL AND s.margine IS NOT NULL
                ORDER BY (s.m - l.price_eur)::float / l.price_eur DESC LIMIT %(lim)s""",
                {"min": int(os.environ.get("SC_LUSSO_MIN", "150000")), "lim": int(os.environ.get("SC_LUSSO_QUANTE", "600"))}).fetchall()}
            if not val:
                return []
            rows, desc, controllo = _supercar_righe(conn, val)
        for r in rows:
            if r["id"] in gia:
                continue
            v = dict(val[r["id"]])
            motivo, corr = SV.filtra(dict(r, description=desc.get(r["id"])), v, int(r.get("n_photos") or 0),
                                     minimo=1, nmin=int(os.environ.get("SC_LUSSO_NMIN", "5")))
            if motivo:
                scarti[motivo] += 1
                continue
            it = _supercar_item(r, v, corr, controllo, lusso=True)
            if it:
                out.append(it)
                gia.add(it["id"])
                opens[it["id"]] = int(r["opens"] or 0)
    except Exception as ex:
        logging.getLogger("scovo").warning("SUPERCAR_LUSSO non caricate: %s", str(ex)[:200])
    if stats is not None:
        stats.update({"lusso:" + k: v for k, v in scarti.items()})
        stats["lusso_caricate"] += len(out)
    return out


def _supercar_super(gia: set, opens: dict, stats=None) -> list:
    """4/11 pomeriggio (cliente): "almeno 100 super da 100k in su in tutta Europa". Auto di privati da SC_SUPER_MIN
    (100.000 €) negli 8 paesi AutoScout24, prima gli affari col prezzo migliore rispetto al mercato. Da
    SC_SENZA_MARGINE_DA (300.000 €) in su basta il prezzo sotto il mercato (margine non richiesto, mostrato lo sconto
    sulla media); sotto quella cifra il margine minimo e il controllo AI li decide sito_regole come sempre."""
    if os.environ.get("SC_SUPER", "1") != "1":
        return []
    from collections import Counter
    from ..ai import supercar_vero as SV
    from ..pipeline.supercar_eu import SUPER_SQL, super_params, senza_margine_da
    out, scarti = [], Counter()
    try:
        with connect() as conn:
            if not conn.execute("SELECT to_regclass('supercar_eu') AS t").fetchone()["t"]:
                return []
            val = {r["listing_id"]: r for r in conn.execute(SUPER_SQL, super_params()).fetchall()}
            if not val:
                return []
            rows, desc, controllo = _supercar_righe(conn, val)
        senza = senza_margine_da()
        for r in rows:
            if r["id"] in gia:
                continue
            v = dict(val[r["id"]])
            prezzo = int(r.get("price_eur") or 0)
            motivo, corr = SV.filtra(dict(r, description=desc.get(r["id"])), v, int(r.get("n_photos") or 0),
                                     **SV.soglie_super(prezzo))
            if motivo:
                scarti[motivo] += 1
                continue
            it = _supercar_item(r, v, corr, controllo, lusso=prezzo >= 150000)
            if not it:
                continue
            M = int(v["m"])
            if prezzo >= senza:
                pv = dict(it["pv"], sconto_pct=max(0, round((M - prezzo) / M * 100)), senza_margine=True,
                          etichetta="Supercar Europa · prezzo sotto il mercato")
                if int(corr.get("margine") or 0) <= 0:
                    pv["margine"] = None                   # nessun margine promesso: si mostra lo sconto sulla media
                it["pv"] = pv
            out.append(it)
            gia.add(it["id"])
            opens[it["id"]] = int(r["opens"] or 0)
    except Exception as ex:
        logging.getLogger("scovo").warning("SUPERCAR_SUPER non caricate: %s", str(ex)[:200])
    if stats is not None:
        stats.update({"super:" + k: v for k, v in scarti.items()})
        stats["super_caricate"] += len(out)
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


# Segni da rivenditore nel testo di un annuncio "privato" (stessa regola di prima, ora in Python sul testo letto una volta)
_SEGNI_COMMERCIANTE = re.compile(
    r"(?i)(\bs\.?\s?r\.?\s?l\.?s?\b|\bs\.?p\.?a\.?\b|concessionaria\s+(ufficiale|autorizzata)|la\s+nostra\s+concessionaria|"
    r"autosalone|siamo\s+(un\s+)?rivenditor|rivenditore\s+autorizzato|garanzia\s+(di\s+)?12\s+mesi|iva\s+esposta|"
    r"esposta\s+iva|iva\s+deducibile|finanziamenti\s+in\s+sede|nostro\s+(parco|salone))")
# il testo di RE_PROMO_COMMERCIANTE è scritto per Postgres (\m \M = inizio/fine parola)
_PROMO_RE = re.compile(RE_PROMO_COMMERCIANTE.replace(r"\m", r"\b").replace(r"\M", r"\b"), re.I)
_SOLO_FIN_RE = re.compile(scovo.FIN_SOLO_RE, re.I)
# commercianti lontani: solo se valutati col metodo nuovo (data della valutazione, non più un confronto di testo
# sulla versione); prima i commercianti sparivano a ogni cambio del calcolo
COMMERCIANTI_VALUTATI_DAL = os.environ.get("COMMERCIANTI_VALUTATI_DAL", "2026-11-03T22:55:00+01:00")


def _dopo(ts, dal: str) -> bool:
    from datetime import datetime
    if not ts:
        return False
    try:
        ts = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
        limite = datetime.fromisoformat(dal)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=limite.tzinfo)
        return ts >= limite
    except Exception:
        return False


# 4/11: quello che si ricava dal TESTO dell'annuncio (titolo e descrizione) si calcola una volta per annuncio e prezzo
# e si tiene in memoria (solo sì/no, mai numeri di telefono): l'elenco si ricalcola ogni 90 secondi e rileggere 8.000
# descrizioni ogni volta pesa sul database. Si svuota ogni TESTO_CACHE_ORE ore (6).
_testo_cache: dict = {}
_testo_cache_t = {"t": time.time()}


def _dal_testo(t: str, nomi) -> dict:
    from . import sito_regole as SR
    return {"tel_testo": bool(scovo.RE_MOBILE.search(t) or scovo.RE_LANDLINE.search(t)),
            "tel_estero": SR.tel_estero(t), "testo_truffa": SR.testo_truffa(t),
            "segni_commerciante": bool(_SEGNI_COMMERCIANTE.search(t)), "promo_testo": bool(_PROMO_RE.search(t)),
            "solo_fin": bool(_SOLO_FIN_RE.search(t)), "loconte_testo": bool(nomi and nomi.search(t))}


def _arricchisci(items: list[dict], pv_at: dict, stats) -> None:
    """Dati per le regole del sito, letti UNA volta per giro (nessun numero di telefono salvato: solo sì/no)."""
    from . import sito_regole as SR
    ids = [it["id"] for it in items if isinstance(it.get("id"), int)]
    prezzi = {it["id"]: it.get("prezzo") for it in items}
    scarti = _scarti_cliente_ids()
    nomi_re = _commercianti_nomi_re()
    nomi = re.compile(nomi_re, re.I) if nomi_re else None
    if time.time() - _testo_cache_t["t"] > float(os.environ.get("TESTO_CACHE_ORE", "6")) * 3600 or len(_testo_cache) > 60000:
        _testo_cache.clear()
        _testo_cache_t["t"] = time.time()
    dati: dict = {}
    loconte: set = set()
    jev: dict = {}
    jev_errore = False
    with connect() as conn:
        senza_testo = [i for i in ids if (i, prezzi.get(i)) not in _testo_cache]
        for k in range(0, len(senza_testo), 2000):
            for r in conn.execute("SELECT id, title, description FROM listings WHERE id = ANY(%s)",
                                  (senza_testo[k:k + 2000],)).fetchall():
                _testo_cache[(r["id"], prezzi.get(r["id"]))] = _dal_testo(
                    f"{r.get('title') or ''} {r.get('description') or ''}", nomi)
        stats["testi_letti"] = len(senza_testo)
        for k in range(0, len(ids), 2000):
            for r in conn.execute(
                    "SELECT id, seller_type, telefono_ok, price_flags, province, city, stage, stage_reason, damage_class "
                    "FROM listings WHERE id = ANY(%s)", (ids[k:k + 2000],)).fetchall():
                tx = _testo_cache.get((r["id"], prezzi.get(r["id"]))) or {}
                flags = set(r.get("price_flags") or [])
                dati[r["id"]] = {
                    "tel": bool(r.get("telefono_ok") or tx.get("tel_testo")),
                    "tel_estero": bool(tx.get("tel_estero")), "testo_truffa": bool(tx.get("testo_truffa")),
                    "seller_type": r.get("seller_type"), "segni_commerciante": bool(tx.get("segni_commerciante")),
                    "promo": bool(tx.get("promo_testo") or flags & set(_FLAG_PROMO)), "solo_fin": bool(tx.get("solo_fin")),
                    "provincia": SR.provincia_di(r.get("province"), r.get("city")),
                    "scarto_cliente": r["id"] in scarti or r.get("stage") == "scartato",
                    "da_verificare": r.get("stage_reason") == "da_verificare",
                    "danno_dichiarato": (r.get("damage_class") or "") in SR.DANNI_DICHIARATI,
                }
                if tx.get("loconte_testo"):
                    loconte.add(r["id"])
        # commercianti: data dell'ultima valutazione (metodo nuovo dal COMMERCIANTI_VALUTATI_DAL), non più un confronto
        # di testo sulla versione del calcolo
        comm = [i for i, d in dati.items() if d.get("seller_type") == "commerciante" and not pv_at.get(i)]
        if comm:
            try:
                for r in conn.execute("SELECT DISTINCT ON (listing_id) listing_id, computed_at FROM valutazioni_policy "
                                      "WHERE listing_id = ANY(%s) ORDER BY listing_id, computed_at DESC", (comm,)).fetchall():
                    pv_at[r["listing_id"]] = r["computed_at"]
            except Exception as e:
                conn.rollback()
                logging.getLogger("scovo").warning("COMMERCIANTI data valutazione non letta: %s", str(e)[:200])
        if nomi_re:
            for q in ("SELECT l.id FROM listings l JOIN commercianti_as24_annunci a ON a.listing_id = l.source_id "
                      "JOIN commercianti_as24 c ON c.seller_id = a.seller_id WHERE l.id = ANY(%(ids)s) AND c.nome ~* %(n)s",
                      "SELECT l.id FROM listings l JOIN lead_tutti_ann t ON t.annuncio = l.source_id "
                      "JOIN lead_tutti_dealer d ON d.chiave = t.chiave WHERE l.id = ANY(%(ids)s) AND d.nome ~* %(n)s"):
                try:
                    loconte |= {r["id"] for r in conn.execute(q, {"ids": ids, "n": nomi_re}).fetchall()}
                except Exception as e:
                    conn.rollback()
                    logging.getLogger("scovo").warning("COMMERCIANTI_NOMI non controllati: %s", str(e)[:200])
        try:
            jev = {r["listing_id"]: dict(r) for r in conn.execute(
                "SELECT d.listing_id, d.decisione, d.confidenza, d.truffa, d.versione_ok, "
                "(d.risposta->'guasto'->>'noul')::float AS guasto, (d.risposta->'prezzo_rata'->>'noul')::float AS rata, "
                "(d.risposta->'km_credibili'->>'noul')::float AS km_ok, "
                "(COALESCE(d.risposta->'danni_foto', d.risposta->'answers'->'danni_foto')->>'noul')::float AS danni, "
                "(COALESCE(d.risposta->'foto_internet', d.risposta->'answers'->'foto_internet')->>'noul')::float AS foto_internet "
                "FROM decisione_ai d JOIN listings l ON l.id = d.listing_id "
                "WHERE d.listing_id = ANY(%s) AND d.prezzo = l.price_eur", (ids,)).fetchall()}
        except Exception as e:
            conn.rollback()
            jev_errore = True          # fail closed: le auto da controllare restano fuori (regola truffa)
            logging.getLogger("scovo").warning("JEV decisioni non lette: %s", str(e)[:200])
    difetti = _difetti_letti(ids)
    stats["jev_decise"] = len(jev)
    stats["jev_errore"] = int(jev_errore)
    for it in items:
        r = dict(dati.get(it["id"]) or {}, **(it.get("_r") or {}))
        r.update(loconte=it["id"] in loconte, jev=jev.get(it["id"]), jev_errore=jev_errore,
                 difetto=difetti.get(it["id"]), valutazione_nuova=_dopo(pv_at.get(it["id"]), COMMERCIANTI_VALUTATI_DAL))
        if it.get("supercar"):
            r["seller_type"] = "privato"           # SITO_SQL e la query lusso prendono solo privati
        it["_r"] = r


def _carica_candidati() -> tuple[list[dict], dict, dict]:
    """Caricamento (l'unico punto col database): elenco, sezione economiche, supercar, poi i dati per le regole."""
    from collections import Counter
    stats: Counter = Counter()
    with connect() as conn:
        # 28/10: senza ORDER BY il LIMIT prendeva ogni volta 3000 righe a caso: il sito oscillava tra 70 e 170 auto
        rows = conn.execute(scovo.LIST_SQL + " ORDER BY l.id DESC LIMIT %(lim)s",
                            {"me": 0, "lim": int(os.environ.get("ELENCO_LIMITE", "8000"))}).fetchall()
    scovo.POLICY_PRONTA["ok"] = sum(1 for r in rows if r.get("pv")) >= max(1, len(rows) // 2)
    items, opens, pv_at = [], {}, {}
    stats["lista"] = len(rows)
    for r in rows:
        it = scovo.item(r)
        if not it:
            stats["lista_senza_scheda"] += 1
            continue
        items.append(it)
        opens[it["id"]] = int(r["opens"] or 0)
        pv_at[it["id"]] = r.get("pv_at")
    gia = {it["id"] for it in items}
    # sezione economiche (regola di sempre: auto di privati dal 2010 fino a ECONOMICHE_MAX, valutate dalla policy con
    # categoria del sito); il margine minimo poi lo controlla sito_regole come per tutte
    if os.environ.get("ECONOMICHE_SITO", "1") == "1":
        try:
            with connect() as conn:
                conn.execute("SET LOCAL statement_timeout = '%ds'" % int(os.environ.get("ECO_TIMEOUT_S", "120")))
                eco = conn.execute(scovo.ECO_SQL + " ORDER BY l.first_seen_at DESC LIMIT %(lim)s",
                                   dict(scovo.eco_params(0), lim=int(os.environ.get("ECONOMICHE_QUANTE", "2500")))).fetchall()
            for r in eco:
                it = scovo.item_eco(r)
                if not it or it["id"] in gia:
                    continue
                if (it.get("pv") or {}).get("categoria") not in scovo.CATEGORIE_SITO:
                    stats["eco_categoria_fuori"] += 1
                    continue
                items.append(it)
                gia.add(it["id"])
                opens[it["id"]] = int(r["opens"] or 0)
                pv_at[it["id"]] = r.get("pv_at")
                stats["eco_caricate"] += 1
        except Exception as e:
            logging.getLogger("scovo").warning("economiche non caricate: %s", str(e)[:200])
    # 28/10 sera (cliente): supercar da 70.000 € in tutta Europa (valutate sui confronti europei, vedi supercar_eu.py)
    # 4/11 pomeriggio: prima le super da 100.000 € in su (da 300.000 € senza obbligo di margine), poi le altre
    items += _supercar_super(gia, opens, stats)
    items += _supercar_europa(gia, opens, stats)
    if os.environ.get("SC_SUPER", "1") != "1":
        items += _supercar_lusso(gia, opens, stats)
    _arricchisci(items, pv_at, stats)
    return items, opens, dict(stats)


_FUNNEL_SALVATO = {"t": 0.0}


def _tabelle_sito() -> None:
    """All'avvio (mai dentro una richiesta): tabella dei contatori giornalieri dei filtri del sito."""
    try:
        with connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS sito_funnel_giorno (
                giorno DATE NOT NULL, passo TEXT NOT NULL, entrate INT NOT NULL, uscite INT NOT NULL,
                aggiornato TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (giorno, passo))""")
            conn.commit()
    except Exception as e:
        logging.getLogger("scovo").warning("sito_funnel_giorno non creata: %s", str(e)[:200])


def _salva_funnel_giorno(fun) -> None:
    """SITO_FUNNEL del giorno (ora italiana) in sito_funnel_giorno. Vale l'ULTIMO elenco calcolato nel giorno: le righe
    del giorno si sostituiscono (non si sommano: lo stesso annuncio passa da ogni elenco ogni 90 secondi).
    Al massimo ogni SITO_FUNNEL_SALVA_MIN minuti (10)."""
    if time.time() - _FUNNEL_SALVATO["t"] < float(os.environ.get("SITO_FUNNEL_SALVA_MIN", "10")) * 60:
        return
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from . import sito_regole as SR
    righe = SR.righe_giorno(fun)
    giorno = datetime.now(ZoneInfo("Europe/Rome")).date()
    try:
        with connect() as conn:
            conn.execute("DELETE FROM sito_funnel_giorno WHERE giorno = %s", (giorno,))
            for passo, entrate, uscite in righe:
                conn.execute("INSERT INTO sito_funnel_giorno (giorno, passo, entrate, uscite) VALUES (%s,%s,%s,%s) "
                             "ON CONFLICT (giorno, passo) DO UPDATE SET entrate=EXCLUDED.entrate, uscite=EXCLUDED.uscite, "
                             "aggiornato=now()", (giorno, passo, entrate, uscite))
            conn.commit()
        _FUNNEL_SALVATO["t"] = time.time()
    except Exception as e:
        logging.getLogger("scovo").warning("SITO_FUNNEL non salvato: %s", str(e)[:200])


def _sentinella(items: list[dict]) -> None:
    """Fotografia dell'elenco per dealfinder.sentinella (altro gruppo): se il modulo non c'è, non succede niente."""
    try:
        import importlib
        f = getattr(importlib.import_module("dealfinder.sentinella"), "salva_istantanea", None)
    except ImportError:
        return
    except Exception as e:
        logging.getLogger("scovo").warning("SENTINELLA non caricata: %s", str(e)[:200])
        return
    if f is None:
        return
    try:
        f([dict(it) for it in items])
    except Exception as e:
        logging.getLogger("scovo").warning("SENTINELLA non riuscita: %s", str(e)[:200])


def _calcola_elenco() -> None:
    from . import sito_regole as SR
    t0 = time.time()
    items, opens, stats = _carica_candidati()
    _pool_revisione = None
    if os.environ.get("DUMP_CANDIDATI") == "1" and time.time() - _dump_fatto.get("t", 0) > float(os.environ.get("DUMP_ORE", "3")) * 3600:
        _dump_fatto["t"] = time.time()
        _pool_revisione = [dict(it) for it in items if (it.get("_r") or {}).get("tel")
                           and (it.get("_r") or {}).get("seller_type") == "privato"]
    fonte = {it["id"]: it.get("fonte") for it in items}
    import dataclasses as _dc
    try:
        _g = _dc.replace(SR.Regole.da_env(), fattori_calibra=tuple(_fattori_calibra()))
    except Exception as e:
        logging.getLogger("scovo").warning("CALIBRA non applicata: %s", str(e)[:200])
        _g = SR.Regole.da_env()
    out, fun = SR.elenco_sito(items, g=_g, caricamento=stats)
    # auto tolte solo perché il numero non è mai stato letto: le ricontrolla il lavoro telefoni_candidate
    _tel_da_controllare.update(i for i in fun.tolti.get("telefono", []) if fonte.get(i) in ("AutoScout24", "Subito"))
    for it in out:
        it.pop("_r", None)
        it.pop("_tipo", None)
    _elenco.update(t=time.time(), items=out, opens=opens)
    try:      # 31/10: storico delle auto mostrate, per la sezione "Già vendute" (prezzo vero dell'annuncio)
        from . import vendute
        vendute.salva_sito(out)
        threading.Thread(target=vendute.dati, daemon=True).start()    # la sezione è pronta al primo clic
    except Exception as e:
        logging.getLogger("scovo").warning("VENDUTE storico: %s", str(e)[:200])
    _sentinella(out)
    _salva_funnel_giorno(fun)
    if _pool_revisione is not None:
        try:
            _dump_candidati(_pool_revisione, {it["id"] for it in out})
        except Exception as e:
            logging.getLogger("scovo").warning("DUMP_CANDIDATI non riuscito: %s", str(e)[:200])
    _log_controllo_finale(out)
    from collections import Counter
    c = Counter()
    for it in out:
        sez = "super" if it.get("supercar") else "eco" if it.get("economica") else "inc" if it.get("incidentata") else "affari"
        c[f"{sez}:{it.get('area') or 'milano'}"] += 1
        if SR.fascia(it):
            c["fascia_richiesta" + (":commercianti" if it.get("commerciante") else ":privati")] += 1
    logging.getLogger("scovo").warning("ELENCO %d auto in %.1fs %s", len(out), time.time() - t0, dict(sorted(c.items())))
    try:   # 11/11: quante auto per provincia (es. Novara)
        import collections as _co, re as _re
        _pp = _co.Counter((_re.search(r"\(([A-Z]{2})\)\s*$", str(it.get("zona") or "")) or [None, "?"])[1] for it in out)
        logging.getLogger("scovo").warning("ELENCO_PROV %s", dict(_pp.most_common(25)))
    except Exception:
        pass
    try:      # 19/10: auto sul sito a metà prezzo (o meno) rispetto al mercato, e quante entrate nelle ultime 14 ore
        from datetime import datetime, timedelta, timezone
        dal = datetime.now(timezone.utc) - timedelta(hours=14)
        sotto = []
        for it in out:
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


def api_vendute(request: Request):
    """31/10: auto passate sul sito e sparite dagli annunci negli ultimi 7 giorni (senza contatti)."""
    viewer(request, "auto")
    from . import vendute
    try:
        return jsonify(vendute.dati())
    except Exception as e:
        logging.getLogger("scovo").warning("VENDUTE errore: %s", str(e)[:300])
        return jsonify({"items": [], "errore": "non disponibile"})


def affari(request: Request):
    user = viewer(request, "auto")
    items = [dict(scovo.in_cima(scovo.prezzo_sito(it))) for it in _elenco_per(user["id"])]
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
                    "policy": scovo.POLICY_VERSION if scovo.POLICY_SITO == "pv2" and scovo.POLICY_PRONTA["ok"] else None,
                    # 4/11: costi del commerciante al massimo 200 € fissi (tagliando e pulizia) più i pezzi
                    "costi_fissi": {"tagliando_pulizia": int(os.environ.get("COSTI_FISSI_MAX", "200"))},
                    "margine_minimo": scovo.SOGLIE_PV[0][1],
                    "soglie": [[l if l < 10**9 else None, m] for l, m in scovo.SOGLIE_PV],
                    "aggiornato": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def affare(request: Request):
    """Scheda di un'auto: la STESSA auto dell'elenco del sito (stessi R, costi e margine). 4/11: un'auto che non è sul
    sito dà 404 (solo l'amministratore vede la scheda dal database, per controllo)."""
    user = viewer(request, "auto")
    lid = int(request.path_params["id"])
    mie = _elenco_per(user["id"])
    it = next((x for x in mie if x.get("id") == lid), None)
    admin = user.get("role") == "admin"
    with connect() as conn:
        if it is not None:
            extra = dict(conn.execute(scovo.DETAIL_EXTRA_SQL, (lid,)).fetchone() or {})
            if it.get("supercar"):
                extra["pv_dati"] = None          # le supercar hanno la valutazione dei confronti europei, non la policy
            d = scovo.in_cima(scovo.prezzo_sito(scovo.detail(dict(it), extra)))   # 10/11: anche dentro la scheda vale il prezzo ribassato
            _con_trattativa(conn, lid, d)
            return jsonify(d)
        if not admin:
            err(404, "Questa auto non è più disponibile")
        row = conn.execute(scovo.LIST_SQL + " AND l.id=%(id)s", {"me": user["id"], "id": lid}).fetchone()
        eco = None
        if not row:
            eco = row = conn.execute(scovo.ECO_SQL + " AND l.id=%(id)s", dict(scovo.eco_params(user["id"]), id=lid)).fetchone()
        extra = conn.execute(scovo.DETAIL_EXTRA_SQL, (lid,)).fetchone() if row else None
    base = (scovo.item_eco(row) if eco else scovo.item(row)) if row else None
    if not base:
        err(404, "Questa auto non è più disponibile")
    d = scovo.detail(base, extra or {})
    d["non_sul_sito"] = True                    # solo amministratore: auto valutata ma tolta dalle regole del sito
    with connect() as conn:
        _con_trattativa(conn, lid, d)
    return jsonify(d)


def _con_trattativa(conn, lid: int, d: dict) -> None:
    """7/11: scheda trattativa (storico prezzi, venditore, previsioni, tempo di rivendita). Mai blocca la scheda."""
    if os.environ.get("SCHEDA_TRATTATIVA", "1") != "1":
        return
    try:
        from . import trattativa as _tr
        d["trattativa"] = _tr.scheda(conn, lid, d, scovo.soglia)
    except Exception as e:
        logging.getLogger("trattativa").warning("SCHEDA errore %s: %s", lid, str(e)[:200])
        try:
            conn.rollback()
        except Exception:
            pass


async def esito_affare(request: Request):
    """Com'è andata (contattato, trattativa, comprata, rivenduta...) su un'auto del sito. 4/11: "Rivenduta a … €" salva
    il prezzo di rivendita vero in dealer_feedback.sold_eur (serve a misurare le stime). Solo abbonati."""
    user = richiede(request, "auto")
    lid = int(request.path_params["id"])
    data = await body(request)
    if data.get("status") not in FEEDBACK_STATUSES:
        err(400, "Stato non valido")

    def num(k, lo=0, hi=5_000_000):
        v = data.get(k)
        if v in (None, ""):
            return None
        try:
            v = int(round(float(str(v).replace(".", "").replace(",", ".") if isinstance(v, str) else v)))
        except (TypeError, ValueError):
            err(400, "Numero non valido")
        if not lo <= v <= hi:
            err(400, "Numero fuori misura")
        return v
    sold = num("sold_eur", 100)
    if data["status"] == "venduta" and sold is None:
        err(400, "Scrivi il prezzo a cui l'hai rivenduta")
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM listings WHERE id=%s", (lid,)).fetchone():
            err(404, "Auto non trovata")
        conn.execute("INSERT INTO dealer_feedback (listing_id, dealer_id, status, reason, bought_eur, sold_eur, "
                     "days_to_sell) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (lid, user["id"], data["status"], str(data.get("reason") or "")[:500],
                      num("bought_eur", 100), sold, num("days_to_sell", 0, 3650)))
        conn.commit()
    return jsonify({"ok": True})


def admin_sito_funnel(request: Request):
    """Pannello: auto entrate e uscite da ogni regola del sito, giorno per giorno (ultimo elenco del giorno)."""
    current_user(request, admin=True)
    giorni = max(1, min(int(request.query_params.get("giorni") or 7), 60))
    try:
        with connect() as conn:
            rows = conn.execute("SELECT giorno, passo, entrate, uscite, aggiornato FROM sito_funnel_giorno "
                                "WHERE giorno > current_date - %s ORDER BY giorno DESC, passo", (giorni,)).fetchall()
    except Exception as e:
        return jsonify({"giorni": [], "errore": "contatori non disponibili: " + str(e)[:120]})
    per: dict = {}
    for r in rows:
        g = per.setdefault(str(r["giorno"]), {"giorno": str(r["giorno"]), "aggiornato": r["aggiornato"], "passi": []})
        g["passi"].append({"passo": r["passo"], "entrate": r["entrate"], "uscite": r["uscite"]})
    from . import sito_regole as SR
    ordine = {n: i for i, (n, _) in enumerate(SR.REGOLE)} | {"margine_sito": 50, "truffa": 51, "margine_minimo": 52,
                                                               "tetto_commercianti": 53}
    for g in per.values():
        g["passi"].sort(key=lambda x: (ordine.get(x["passo"].split(".")[0], 99), "." in x["passo"], x["passo"]))
    return jsonify({"giorni": list(per.values())})


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


# ---------------------------------------------------------------------------
# 3/11 sera (cliente): abbonati da 200 €. Prima i dati dell'azienda (chi sono e dove), poi il tasto "Fissa
# appuntamento" su ogni auto: il commerciante sceglie giorno e fascia oraria, noi fissiamo l'appuntamento col
# privato. Ogni richiesta va in tabella appuntamenti e arriva per email a info@skovo.app.
# ---------------------------------------------------------------------------
# 3/11 (cliente): niente ragione sociale e P.IVA; nome dell'azienda, dove si trova e chi è il referente
AZIENDA_CAMPI = [("nome_azienda", "Nome dell'azienda", True), ("indirizzo", "Indirizzo", True), ("comune", "Comune", True),
                 ("provincia", "Provincia", True), ("referente", "Nome e cognome", True),
                 ("cellulare", "Cellulare o WhatsApp", True), ("email", "Email", False)]
APP_FASCE = {"mattina": "Mattina (9–12)", "pranzo": "Pranzo (12–14)", "pomeriggio": "Pomeriggio (14–18)", "sera": "Sera (18–20)"}
APP_ORE = [f"{h}:{m}" for h in range(9, 20) for m in ("00", "30")][:-1]       # 9:00 … 19:00
APP_MAX = 8
APP_GIORNO = int(os.environ.get("APPUNTAMENTI_GIORNO", "20"))    # richieste al giorno per commerciante
APP_DOPPIA_ORE = 24                                                 # stessa richiesta per la stessa auto: non si rimanda
APP_PREAVVISO_MIN = int(os.environ.get("APPUNTAMENTI_PREAVVISO_MIN", "60"))
# Le tabelle aziende_abbonati e appuntamenti nascono con la migrazione 031 (prima alla prima richiesta del sito).


def _ora_roma():
    """Adesso in Italia: il server di Render è in UTC, il commerciante sceglie giorni e ore italiani."""
    import datetime as _d
    from zoneinfo import ZoneInfo
    return _d.datetime.now(ZoneInfo("Europe/Rome"))


def _proposte_valide(grezze: list, adesso) -> tuple[list, list]:
    """(proposte buone ordinate [(giorno, ora)], scartate [{giorno, ora, motivo}]). Giorni da oggi a oggi+14 in
    Europe/Rome; oggi solo gli orari almeno APP_PREAVVISO_MIN minuti dopo adesso; doppioni tolti."""
    import datetime as _d
    oggi = adesso.date()
    proposte, scartate, visti = [], [], set()
    for x in (grezze or [])[:APP_MAX]:
        x = x if isinstance(x, dict) else {}
        g_txt, o = str(x.get("giorno") or ""), str(x.get("ora") or "")
        try:
            g = _d.date.fromisoformat(g_txt)
        except ValueError:
            scartate.append({"giorno": g_txt, "ora": o, "motivo": "giorno non valido"})
            continue
        if o not in APP_ORE:
            scartate.append({"giorno": g_txt, "ora": o, "motivo": "orario non valido"})
        elif not (oggi <= g <= oggi + _d.timedelta(days=14)):
            scartate.append({"giorno": g_txt, "ora": o, "motivo": "giorno fuori dalle prossime 2 settimane"})
        elif g == oggi and _d.datetime.combine(g, _d.time(*map(int, o.split(":"))), adesso.tzinfo) < \
                adesso + _d.timedelta(minutes=APP_PREAVVISO_MIN):
            scartate.append({"giorno": g_txt, "ora": o, "motivo": "orario già passato o troppo vicino"})
        elif (g, o) not in visti:
            visti.add((g, o))
            proposte.append((g, o))
    proposte.sort()
    return proposte, scartate


def _impronta_appuntamento(dealer_id: int, lid: int, proposte: list, nota: str | None) -> str:
    import hashlib
    chiave = json.dumps([dealer_id, lid, [[str(g), o] for g, o in proposte], (nota or "").strip().lower()])
    return hashlib.sha256(chiave.encode()).hexdigest()


def _azienda_norm(d: dict | None) -> dict:
    d = dict(d or {})
    if not d.get("nome_azienda") and d.get("ragione_sociale"):     # dati salvati col modulo di prima
        d["nome_azienda"] = d["ragione_sociale"]
    return d


def _fascia_di(ora: str) -> str:
    h = int(ora.split(":")[0])
    return "mattina" if h < 12 else "pranzo" if h < 14 else "pomeriggio" if h < 18 else "sera"


def _azienda_ok(d: dict | None) -> bool:
    d = _azienda_norm(d)
    return bool(d) and all(str(d.get(k) or "").strip() for k, _, obbl in AZIENDA_CAMPI if obbl)


async def azienda_get(request: Request):
    user = current_user(request)
    with connect() as conn:
        r = conn.execute("SELECT dati FROM aziende_abbonati WHERE dealer_id=%s", (user["id"],)).fetchone()
        perm = permessi_di(conn, user)
    d = _azienda_norm((r or {}).get("dati"))
    return jsonify({"dati": d, "completa": _azienda_ok(d), "ore": APP_ORE, "max": APP_MAX, "campi": [{"k": k, "l": l, "obbligatorio": o} for k, l, o in AZIENDA_CAMPI],
                    "appuntamenti": True})   # 11/11: aperto a tutti


async def azienda_put(request: Request):
    user = current_user(request)
    data = await body(request)
    d = {k: str(data.get(k) or "").strip()[:200] for k, _, _ in AZIENDA_CAMPI}
    d["provincia"] = d["provincia"].upper()[:2]
    if not _azienda_ok(d):
        err(400, "Compila tutti i campi obbligatori")
    with connect() as conn:
        conn.execute("INSERT INTO aziende_abbonati (dealer_id, dati) VALUES (%s,%s) ON CONFLICT (dealer_id) DO UPDATE "
                     "SET dati=EXCLUDED.dati, updated_at=now()", (user["id"], json.dumps(d)))
        conn.commit()
    return jsonify({"ok": True, "dati": d, "completa": True})


def _avvisa_appuntamento(testo: str, oggetto: str, aid: int | None = None) -> bool:
    """Email a info@skovo.app con la richiesta (Brevo transazionale, chiave su Render). 4/11: si guarda la risposta
    di Brevo; l'esito va nel log (APPUNTAMENTO email esito=...) e in appuntamenti.email_ok/email_esito, così una
    richiesta senza email si vede nel pannello."""
    lg = logging.getLogger("scovo")
    try:
        import httpx
        from ..marketing.brevo_liste import API, MITTENTE, _chiave
        r = httpx.post(f"{API}/smtp/email", headers={"api-key": _chiave(), "accept": "application/json"}, timeout=20,
                       json={"sender": MITTENTE, "to": [{"email": os.environ.get("APPUNTAMENTI_EMAIL", "info@skovo.app")}],
                             "subject": oggetto, "textContent": testo})
        ok = 200 <= r.status_code < 300
        esito = f"http {r.status_code}" + ("" if ok else " " + (r.text or "")[:200])
    except Exception as e:
        ok, esito = False, f"{type(e).__name__}: {str(e)[:200]}"
    (lg.info if ok else lg.error)("APPUNTAMENTO %s email esito=%s", aid, "ok" if ok else esito)
    if aid:
        try:
            with connect() as conn:
                conn.execute("UPDATE appuntamenti SET email_ok=%s, email_esito=%s WHERE id=%s", (ok, esito[:300], aid))
                conn.commit()
        except Exception as e:
            lg.warning("APPUNTAMENTO %s esito email non salvato: %s", aid, type(e).__name__)
    return ok


# ---------- 9/11 (cliente): "Cercamela" — il commerciante dice a voce l'auto che vuole, noi entro 30 minuti gli
# troviamo la migliore dei privati in zona. Solo piano Max (permesso "appuntamenti"). La richiesta va in tabella
# richieste_auto e arriva per email a info@skovo.app (nome, telefono, testo e le auto del sito che già combaciano).
RICHIESTE_GIORNO = int(os.environ.get("RICHIESTE_AUTO_GIORNO", "10"))
_RICHIESTE_SQL = """CREATE TABLE IF NOT EXISTS richieste_auto (
    id BIGSERIAL PRIMARY KEY, dealer_id BIGINT NOT NULL, testo TEXT NOT NULL, zona TEXT,
    stato TEXT NOT NULL DEFAULT 'in_cerca', risposta TEXT, email_ok BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""


_RICHIESTE_PRONTA = {"ok": False}


def _richieste_tabella(conn) -> None:
    if _RICHIESTE_PRONTA["ok"]:                 # 9/11 revisione: la tabella si crea una volta sola per processo
        return
    conn.execute(_RICHIESTE_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS richieste_auto_dealer ON richieste_auto (dealer_id, created_at DESC)")
    _RICHIESTE_PRONTA["ok"] = True


def _richiesta_righe(conn, did: int) -> list[dict]:
    return [{"id": r["id"], "testo": r["testo"], "stato": r["stato"], "risposta": r["risposta"],
             "quando": r["created_at"].isoformat()}
            for r in conn.execute("SELECT id, testo, stato, risposta, created_at FROM richieste_auto WHERE dealer_id=%s "
                                  "ORDER BY id DESC LIMIT 10", (did,)).fetchall()]


async def richieste_get(request: Request):
    user = current_user(request)

    def work():
        with connect() as conn:
            _richieste_tabella(conn)
            perm = permessi_di(conn, user)
            righe = _richiesta_righe(conn, user["id"])
            conn.commit()
        return {"max": user.get("role") == "admin" or "appuntamenti" in perm, "richieste": righe}
    return jsonify(await run_in_threadpool(work))


async def richieste_post(request: Request):
    user = current_user(request)
    data = await body(request)
    testo = re.sub(r"\s+", " ", str(data.get("testo") or "")).strip()[:1500]
    if len(testo) < 6:
        err(400, "Dicci che auto cerchi: marca, modello, anno, km e quanto vuoi spendere")
    zona = str(data.get("zona") or "")[:20] or None

    def work():
        with connect() as conn:
            _richieste_tabella(conn)
            if not (user.get("role") == "admin" or "appuntamenti" in permessi_di(conn, user)):
                err(402, "La ricerca su misura è inclusa nel piano Max")
            if user.get("role") != "admin":
                n = conn.execute("SELECT count(*) AS n FROM richieste_auto WHERE dealer_id=%s "
                                 "AND created_at > now() - interval '24 hours'", (user["id"],)).fetchone()["n"]
                if n >= RICHIESTE_GIORNO:
                    err(429, f"Hai già mandato {n} richieste oggi: il limite è {RICHIESTE_GIORNO}")
            gia = conn.execute("SELECT id FROM richieste_auto WHERE dealer_id=%s AND testo=%s "
                               "AND created_at > now() - interval '10 minutes'", (user["id"], testo)).fetchone()
            rid = gia["id"] if gia else conn.execute(
                "INSERT INTO richieste_auto (dealer_id, testo, zona) VALUES (%s,%s,%s) RETURNING id",
                (user["id"], testo, zona)).fetchone()["id"]
            d = conn.execute("SELECT name, email, company, phone FROM dealers WHERE id=%s", (user["id"],)).fetchone() or {}
            conn.commit()
            # le auto del sito che già combaciano (marca/modello/prezzo/anno/km detti a voce): aiutano chi cerca
            intanto = []
            try:
                from . import assistente as bot
                bot.ELENCO = bot.ELENCO or _elenco_per
                q = bot.parse(testo)
                if q.get("marca") or q.get("modello"):
                    a = {k: q.get(k) for k in ("marca", "modello", "prezzo_max", "stato")}
                    if q.get("anno"):
                        a["anno_min"] = q["anno"] - 1
                    if q.get("km"):
                        a["km_max"] = q["km"]
                    a["limite"] = 3
                    a["limite"] = 10
                    intanto = bot.cerca_affari(conn, user, a).get("auto") or []
                    if zona and zona != "tutte":           # 9/11 revisione: solo auto della zona scelta sul sito
                        aree = {it["id"]: it.get("area") for it in (_elenco_per(user["id"]) or [])}
                        intanto = [c for c in intanto if aree.get(c["id"], "milano") == zona]
                    intanto = intanto[:3]
            except Exception as e:
                logging.getLogger("scovo").warning("RICHIESTA_AUTO %s ricerca nel sito non riuscita: %s", rid, type(e).__name__)
            righe = _richiesta_righe(conn, user["id"])
        if gia:
            return {"ok": True, "id": rid, "gia_inviata": True, "richieste": righe, "intanto": intanto}
        base = os.environ.get("SITO_URL", "https://scovo.onrender.com")
        testo_email = (f"Richiesta auto su misura n. {rid} (piano Max): trovare la migliore entro 30 minuti\n\n"
                       f"Cosa cerca: {testo}\n\n"
                       f"Commerciante: {d.get('company') or d.get('name') or '-'}\n"
                       f"Nome: {d.get('name') or '-'}\nTelefono: {d.get('phone') or '-'}\nEmail: {d.get('email') or '-'}\n"
                       f"Zona scelta sul sito: {zona or 'tutte'}\n")
        if intanto:
            testo_email += "\nGià sul sito e simili:\n" + "\n".join(
                f"- {c['nome']} {c.get('anno') or ''} · {c.get('zona') or ''} · {c['prezzo']} € · guadagno "
                f"{c['guadagno_da']}–{c['guadagno_a']} € · {base}/#/auto/{c['id']}" for c in intanto)
        ok = _avvisa_appuntamento(testo_email, f"Skovo - CERCAMELA n. {rid}: {testo[:60]}")
        try:
            with connect() as conn:
                conn.execute("UPDATE richieste_auto SET email_ok=%s WHERE id=%s", (ok, rid))
                conn.commit()
        except Exception:
            pass
        (logging.getLogger("scovo").warning if ok else logging.getLogger("scovo").error)(
            "RICHIESTA_AUTO %s dealer=%s email=%s simili=%d", rid, user["id"], "ok" if ok else "NON_PARTITA", len(intanto))
        return {"ok": True, "id": rid, "richieste": righe, "intanto": intanto, "avviso_ok": ok}
    return jsonify(await run_in_threadpool(work))


async def admin_richieste_auto(request: Request):
    current_user(request, admin=True)
    with connect() as conn:
        _richieste_tabella(conn)
        rows = conn.execute("SELECT r.id, r.testo, r.stato, r.risposta, r.zona, r.created_at, r.email_ok, d.name, d.company, "
                            "d.phone, d.email FROM richieste_auto r LEFT JOIN dealers d ON d.id = r.dealer_id "
                            "ORDER BY r.id DESC LIMIT 100").fetchall()
        conn.commit()
    return jsonify([{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows])


async def admin_richiesta_auto_aggiorna(request: Request):
    current_user(request, admin=True)
    data = await body(request)
    stato = str(data.get("stato") or "trovata")
    if stato not in ("in_cerca", "trovata", "non_trovata"):
        err(400, "Stato non valido")
    with connect() as conn:
        _richieste_tabella(conn)
        n = conn.execute("UPDATE richieste_auto SET stato=%s, risposta=COALESCE(%s, risposta), updated_at=now() WHERE id=%s",
                         (stato, (str(data.get("risposta") or "").strip()[:1000] or None), int(request.path_params["id"]))).rowcount
        conn.commit()
    if not n:
        err(404, "Richiesta non trovata")
    return jsonify({"ok": True})


async def appuntamento(request: Request):
    user = current_user(request)
    lid = int(request.path_params["id"])
    data = await body(request)
    grezze = data.get("proposte")
    if not isinstance(grezze, list):          # versione di prima: un giorno e una fascia
        grezze = [{"giorno": data.get("giorno"), "ora": data.get("ora") or {"mattina": "10:00", "pranzo": "12:30",
                   "pomeriggio": "15:00", "sera": "18:30"}.get(str(data.get("fascia") or ""), "")}]
    proposte, scartate = _proposte_valide(grezze, _ora_roma())
    if not proposte:
        return jsonify({"errore": "Scegli almeno un giorno e un orario ancora libero (da adesso alle prossime "
                                  "2 settimane)", "scartate": scartate}, 400)
    giorno, ora = proposte[0]
    fascia = _fascia_di(ora)
    nota = str(data.get("nota") or "").strip()[:500] or None
    impronta = _impronta_appuntamento(user["id"], lid, proposte, nota)
    with connect() as conn:
        # 11/11 (cliente): "Fissa l'appuntamento" aperto a tutti gli account, non solo al Max
        az = _azienda_norm((conn.execute("SELECT dati FROM aziende_abbonati WHERE dealer_id=%s", (user["id"],)).fetchone() or {}).get("dati"))
        if not _azienda_ok(az):
            return jsonify({"serve_azienda": True, "errore": "Prima inserisci i dati della tua azienda"}, 409)
        car = conn.execute("SELECT id, url, title, make, model, year, mileage_km, price_eur, city, province, description "
                           "FROM listings WHERE id=%s AND status='attivo'", (lid,)).fetchone()
        if not car:
            err(404, "Questa auto non è più disponibile")
        # due clic (o due schede) con la stessa richiesta: una sola riga e una sola email
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"appuntamento:{user['id']}:{lid}",))
        gia = conn.execute("SELECT id FROM appuntamenti WHERE dealer_id=%s AND listing_id=%s AND impronta=%s "
                           "AND created_at > now() - make_interval(hours => %s) ORDER BY id DESC LIMIT 1",
                           (user["id"], lid, impronta, APP_DOPPIA_ORE)).fetchone()
        if gia:
            conn.commit()
            return jsonify({"ok": True, "id": gia["id"], "gia_inviata": True, "scartate": scartate})
        if user.get("role") != "admin":
            n = conn.execute("SELECT count(*) AS n FROM appuntamenti WHERE dealer_id=%s "
                             "AND created_at > now() - interval '24 hours'", (user["id"],)).fetchone()["n"]
            if n >= APP_GIORNO:
                err(429, f"Hai chiesto {n} appuntamenti nelle ultime 24 ore: il limite è {APP_GIORNO}")
        aid = conn.execute("INSERT INTO appuntamenti (dealer_id, listing_id, giorno, fascia, ora, nota, proposte, impronta) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                           (user["id"], lid, giorno, fascia, ora, nota,
                            json.dumps([{"giorno": str(g), "ora": o} for g, o in proposte]), impronta)).fetchone()["id"]
        conn.execute("INSERT INTO listing_opens (listing_id, dealer_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (lid, user["id"]))
        conn.commit()
    tel = scovo.find_phone(car.get("description"))
    testo = (f"Richiesta appuntamento n. {aid}\n\n"
             f"Commerciante: {az.get('nome_azienda')}\n"
             f"Sede: {az.get('indirizzo')}, {az.get('comune')} ({az.get('provincia')})\n"
             f"Referente: {az.get('referente')} - {az.get('cellulare')}" + (f" - {az.get('email')}" if az.get('email') else "") + "\n\n"
             f"Auto: {car.get('title') or ''} - {car.get('year') or ''} - {car.get('mileage_km') or ''} km - {car.get('price_eur')} EUR\n"
             f"Zona: {car.get('city') or ''} ({car.get('province') or ''})\nAnnuncio: {car.get('url')}\n"
             + (f"Numero del venditore nell'annuncio: {tel}\n" if tel else "") +
             "\nDisponibilità del commerciante (ora italiana, una qualsiasi va bene):\n"
             + "".join(f"  - {['lun','mar','mer','gio','ven','sab','dom'][g.weekday()]} {g.strftime('%d/%m')} ore {o}\n" for g, o in proposte)
             + (f"Nota: {nota}\n" if nota else ""))
    threading.Thread(target=_avvisa_appuntamento, args=(testo, f"Appuntamento ({len(proposte)} date) - {az.get('nome_azienda')} - {car.get('title') or ''}"[:150], aid),
                     daemon=True).start()
    logging.getLogger("scovo").warning("APPUNTAMENTO %s richiesto: commerciante %s, auto %s, %s %s", aid, user["id"], lid, giorno, fascia)
    return jsonify({"ok": True, "id": aid, "scartate": scartate})


async def admin_appuntamenti(request: Request):
    current_user(request, admin=True)
    with connect() as conn:
        # LEFT JOIN: le richieste restano anche quando l'annuncio viene cancellato dalla pulizia
        rows = conn.execute("""SELECT a.id, a.giorno, a.fascia, a.ora, a.nota, a.stato, a.created_at, a.listing_id,
                 a.proposte, a.email_ok, a.email_esito, l.title, l.url, l.price_eur, z.dati AS azienda
                 FROM appuntamenti a LEFT JOIN listings l ON l.id = a.listing_id
                 LEFT JOIN aziende_abbonati z ON z.dealer_id = a.dealer_id ORDER BY a.created_at DESC LIMIT 200""").fetchall()
    return jsonify({"appuntamenti": [dict(r, giorno=str(r["giorno"]), created_at=str(r["created_at"])) for r in rows]})


def _valutata_per_sito(lid: int) -> bool:
    """Riserva quando l'elenco non è ancora pronto: l'auto ha una valutazione da sito (ultima valutazione della policy
    in una categoria del sito con margine almeno la soglia, oppure supercar valutata come opportunità)."""
    try:
        with connect() as conn:
            r = conn.execute(
                "SELECT l.price_eur, (SELECT riassunto FROM valutazioni_policy vp WHERE vp.listing_id=l.id "
                "ORDER BY vp.computed_at DESC LIMIT 1) AS pv FROM listings l WHERE l.id=%s AND l.status='attivo'",
                (lid,)).fetchone()
            if not r:
                return False
            pv = r["pv"] if isinstance(r["pv"], dict) else (json.loads(r["pv"]) if r["pv"] else {})
            if pv.get("categoria") in scovo.CATEGORIE_SITO and pv.get("margine") is not None \
                    and int(pv["margine"]) >= scovo.soglia(int(r["price_eur"] or 0)):
                return True
            if conn.execute("SELECT to_regclass('supercar_eu') AS t").fetchone()["t"]:
                return bool(conn.execute("SELECT 1 FROM supercar_eu WHERE listing_id=%s AND categoria='opportunita'",
                                         (lid,)).fetchone())
    except Exception as e:
        logging.getLogger("scovo").warning("contatto: controllo di riserva non riuscito: %s", str(e)[:150])
    return False


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
    # 3/11 23:45: contatto (url e numero) solo per le auto mostrate sul sito, non per qualsiasi annuncio del database
    if not (user and user.get("role") == "admin"):
        sul_sito = {it.get("id") for it in (_elenco.get("items") or [])}
        if sul_sito and lid not in sul_sito:
            err(404, "Questa auto non è più disponibile")
        if not sul_sito and not _valutata_per_sito(lid):
            # 4/11: subito dopo un riavvio l'elenco è vuoto: non si salta il controllo, si guarda il database
            err(404, "Questa auto non è più disponibile")
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
            data = _utente_del_token(data)          # ruolo dal database, non dal token
            if data.get("role") != "admin":
                with connect() as conn:
                    if "auto" not in permessi_di(conn, data):
                        err(403, "Nessun accesso")
                    row = conn.execute("SELECT richiede_abbonamento, abbonamento_stato, (password_hash IS NOT NULL) "
                                       "AS con_password, role, paga_carta FROM dealers WHERE id=%s", (data["id"],)).fetchone()
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


def _chiave_export_ok(request: Request) -> bool:
    """4/11: la chiave non va più nell'indirizzo (?k=..., finiva nei log di accesso e nella cronologia): arriva
    nell'intestazione X-Chiave (curl -H "X-Chiave: ..."), oppure si scarica da amministratore con il token."""
    import hmac
    chiave = os.environ.get("COMMERCIANTI_KEY") or ""
    data = str(request.headers.get("x-chiave") or "")
    if chiave and data and hmac.compare_digest(chiave.encode(), data.encode()):
        return True
    if request.headers.get("authorization"):
        try:
            current_user(request, admin=True)
            return True
        except HTTPError:
            return False
    return False


def commercianti_tel(request: Request):
    """22/10 (cliente): CSV dei piccoli commercianti di utilitarie con cellulare aziendale da AutoScout24.
    Si scarica solo con la chiave COMMERCIANTI_KEY nell'intestazione X-Chiave o da amministratore. Nei log solo il conteggio."""
    if not _chiave_export_ok(request):
        return Response(status_code=404)
    from ..marketing import commercianti_tel as ct
    with connect() as conn:
        rows = ct.righe(conn, int(os.environ.get("COMMERCIANTI_MAX_AUTO", "40")))
    logging.getLogger("dealfinder.marketing").warning("COMMERCIANTI_TEL scaricati %d", len(rows))
    return Response(ct.csv_bytes(rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=commercianti_cellulari.csv", "Cache-Control": "no-store"})


async def admin_dati_riservati(request: Request):
    """4/11 sera: l'amministratore carica una lista con dati personali (es. email dei saloni trovate a mano) nella
    tabella dati_riservati, fuori dal repo e dai log. Corpo JSON {"nome": "lead_...", "dati": [...]}."""
    current_user(request, admin=True)
    from ..marketing import dati_riservati as DR
    body = await request.json()
    nome = str(body.get("nome") or "")
    if not re.fullmatch(r"lead_[a-z0-9_]{1,40}", nome) or not isinstance(body.get("dati"), (list, dict)):
        err(400, "nome lead_... e dati (lista) richiesti")
    with connect() as conn:
        DR.carica(conn, nome, body["dati"])
    return jsonify({"ok": True, "nome": nome, "voci": len(body["dati"])})


def admin_lead_csv(request: Request):
    """4/11: liste di commercianti (prima righe LEAD..., LEADE, LEADT, ESPORTA_* nei log di Render) da scaricare in
    CSV dall'amministratore: /api/admin/lead.csv?tipo=LEADE. Senza tipo: i tipi disponibili con i conteggi."""
    current_user(request, admin=True)
    from ..marketing import consegne
    tipo = str(request.query_params.get("tipo") or "")[:40]
    with connect() as conn:
        if not tipo:
            rows = conn.execute("SELECT tipo, count(*) AS n, max(at) AS ultimo FROM lead_consegne GROUP BY tipo "
                                "ORDER BY tipo").fetchall()
            return jsonify({"tipi": rows})
        rows = consegne.righe(conn, tipo)
    logging.getLogger("dealfinder.marketing").warning("LEAD_CSV %s scaricati %d", tipo, len(rows))
    return Response(consegne.csv_bytes(rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=lead_{re.sub(r'[^A-Za-z0-9_]', '', tipo)}.csv",
                             "Cache-Control": "no-store"})


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
            # un account spento dall'admin si riaccende solo dal pannello (Attiva), non chiudendo una richiesta
            conn.execute("UPDATE dealers SET active=true WHERE email=%s AND NOT disattivato_da_admin", (r["email"],))
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


def vetrina_js(request: Request):
    """5/11: three.js servito dal nostro dominio per i siti vetrina 3D (il CDN su iPhone era lento)."""
    nome, p = request.path_params["nome"], request.path_params["p"]
    if not re.fullmatch(r"[a-z0-9-]{2,40}", nome) or ".." in p or not re.fullmatch(r"[A-Za-z0-9_./-]{2,120}\.(js|json)", p):
        return Response("non trovato", status_code=404)
    if p.startswith("/") or "//" in p or "\\" in p:
        return Response("non trovato", status_code=404)
    base = (Path(__file__).resolve().parents[2] / "data" / "vetrine" / nome / "js").resolve()
    f = (base / p).resolve()
    if not f.is_relative_to(base) or not f.is_file():
        return Response("non trovato", status_code=404)
    tipo = "text/javascript; charset=utf-8" if p.endswith(".js") else "application/json"
    return FileResponse(f, media_type=tipo, headers={"cache-control": "public, max-age=604800", "Access-Control-Allow-Origin": "*"})


def robots(request: Request):
    from . import seo
    return Response("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /gestione\nDisallow: /auth/\n"
                    "Disallow: /incentivi/\n"
                    f"Sitemap: {seo.BASE}/sitemap.xml\nSitemap: {seo.BASE}/vetrina/marconi1/sitemap.xml\nSitemap: {seo.BASE}/vetrina/astro/sitemap.xml\n", media_type="text/plain")


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
    data = {**data, "role": sessions.ruolo(data.get("sid")) or "commerciante"}      # ruolo dal database
    if data.get("role") == "admin":
        return True, 200, ""
    with connect() as conn:
        if "incentivi" in permessi_di(conn, data):
            return True, 200, ""
    return False, 403, "Il tuo account non comprende gli Incentivi. Chiedi all'amministratore di attivarli"


from .vetrina import api_vetrina, foto_vetrina

routes = [
    Route("/api/vetrina/{slug}", api_vetrina),
    Route("/api/vetrina-foto", foto_vetrina),
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
    Route("/api/vendute", api_vendute),
    Route("/api/affari", affari),
    Route("/api/affari/{id:int}", affare),
    Route("/api/affari/{id:int}/contatto", contatto, methods=["POST"]),
    Route("/api/affari/{id:int}/vai", vai_annuncio),
    Route("/api/affari/{id:int}/appuntamento", appuntamento, methods=["POST"]),
    Route("/api/me/azienda", azienda_get, methods=["GET"]),
    Route("/api/me/azienda", azienda_put, methods=["PUT"]),
    Route("/api/admin/appuntamenti", admin_appuntamenti),
    Route("/api/richieste-auto", richieste_get, methods=["GET"]),
    Route("/api/richieste-auto", richieste_post, methods=["POST"]),
    Route("/api/admin/richieste-auto", admin_richieste_auto),
    Route("/api/admin/richieste-auto/{id:int}", admin_richiesta_auto_aggiorna, methods=["POST"]),
    Route("/api/foto/{id:int}/{pos:int}", foto_auto),
    Route("/email-foto/{id:int}/{pos:int}.jpg", foto_email),
    Route("/admin/commercianti-tel.csv", commercianti_tel),
    Route("/api/admin/lead.csv", admin_lead_csv),
    Route("/api/admin/dati-riservati", admin_dati_riservati, methods=["POST"]),
    Route("/api/richiesta-accesso", richiesta_accesso, methods=["POST"]),
    Route("/api/opportunities", opportunities),
    Route("/api/opportunities/{id:int}", opportunity_detail),
    Route("/api/opportunities/{id:int}/open", open_listing, methods=["POST"]),
    Route("/api/opportunities/{id:int}/feedback", feedback, methods=["POST"]),
    Route("/api/affari/{id:int}/esito", esito_affare, methods=["POST"]),
    Route("/api/admin/sito-funnel", admin_sito_funnel),
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
    Route("/vetrina/{nome}/js/{p:path}", vetrina_js),
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


NON_COMMERCIANTI = re.compile(r"(?i)uffic|amministrativ|bosch|car service|detailing|riparaz|assistenz|\bassist|immobiliar|agenzia|"
                              r"marketing|incidentat|grandinat|compro auto|rottam|carrozz|wrapping|lavaggi|ricambi|pneumatic|"
                              r"veicoli commerciali|furgon|camper|moto\b|scooter|autoscuol|assicuraz|leasing|finanziam|truck|camion|"
                              # chi ha già detto di no (email del 2/11)
                              r"auto\s*(&|e)\s*auto varese|cosval|officine mulino|loconte")


def _sms_strutturati(no, L):
    """2/11 sera: commercianti 'ben strutturati' entro ~45 km da Sesto San Giovanni, un cellulare per azienda.
    Fonti: AutoScout24 (pagina commerciante con almeno 8 auto in vendita) e Google Maps (categoria concessionaria/
    auto usate, sito web, almeno 20 recensioni, voto >= 3.5). Tolti: officine & co., esclusi/già consegnati, già
    chiamati, numeri condivisi da più aziende. Ritorna ([(priorità, tel)], info) ordinati dal più strutturato."""
    import hashlib
    from collections import Counter
    from ..pipeline.lead import _h, _nn, _nu, _tel9
    from ..pipeline.lead_maps import CATEGORIE_OK, CATEGORIE_NO
    base = Path(__file__).resolve().parents[2] / "data"
    km60 = json.loads((base / "comuni_60km.json").read_text())

    def km(comune):
        k = (comune or "").split(" - ")[0].split(",")[0].strip().lower()
        for a, b in (("à", "a"), ("è", "e"), ("é", "e"), ("ù", "u"), ("ò", "o"), ("ì", "i")):
            k = k.replace(a, b)
        k = re.sub(r"[^a-z]", "", k)
        if k == "milano":
            return 9.0
        v = km60.get(k)
        return float(v["km"]) if v else None
    es = json.loads((base / "lead_esclusi.json").read_text())
    ET, EN, EU = set(es["tel"]), set(es["nome"]), set(es["url"])
    chiamati = set(json.loads((base / "lead_chiamati.json").read_text())["tel"])
    righe = []
    st = Counter()
    with connect() as conn:
        for r in conn.execute("SELECT tel, nome, comune, prov, link, auto FROM lead_tutti "
                              "WHERE link LIKE 'http%%' AND auto >= 3").fetchall():
            righe.append({"tel": r["tel"], "nome": r["nome"] or "", "comune": r["comune"] or "", "prov": r["prov"], "link": r["link"],
                          "peso": f'{r["auto"]} auto', "punti": min(r["auto"], 200) * 3, "fonte": "autoscout"})
        for r in conn.execute("SELECT nome, comune, prov, tel, categoria, voto, recensioni FROM lead_maps WHERE cell "
                              "AND sito IS NOT NULL AND sito <> '' AND coalesce(recensioni,0) >= 5 "
                              "AND coalesce(voto,0) >= 3.5").fetchall():
            if not CATEGORIE_OK.search(r["categoria"] or "") or CATEGORIE_NO.search(r["categoria"] or ""):
                st["maps_categoria"] += 1
                continue
            righe.append({"tel": r["tel"], "nome": r["nome"] or "", "comune": r["comune"] or "", "prov": r["prov"], "link": None,
                          "peso": f'{r["recensioni"]} recensioni', "punti": min(r["recensioni"], 600), "fonte": "maps"})
    st["grezzi"] = len(righe)
    for x in righe:
        d = re.sub(r"\D", "", str(x["tel"] or ""))
        x["tel"] = d[2:] if d.startswith("39") and len(d) > 10 else d
    nomi_tel = {}
    for x in righe:
        nomi_tel.setdefault(x["tel"], set()).add(_nn(x["nome"]) or x["nome"])
    buoni, visti_nomi = [], set()
    for x in sorted(righe, key=lambda x: -x["punti"]):
        x["km"] = km(x["comune"])
        nn = _nn(x["nome"])
        if not x["nome"].strip() or no.search(x["nome"]) or NON_COMMERCIANTI.search(x["nome"]):
            st["officine_ecc"] += 1
            continue
        if x["km"] is None or x["km"] > 50:
            st["lontani_o_comune_ignoto"] += 1
            continue
        if len(nomi_tel.get(x["tel"], ())) > 1:
            st["numero_di_piu_aziende"] += 1
            continue
        # i lead "già consegnati" in lista al cliente NON sono esclusi (consegnati non vuol dire contattati): si contano soltanto
        t9 = _tel9(x["tel"])
        if (t9 and _h(t9) in ET) or (nn and _h(nn) in EN) or (x["link"] and _h(_nu(x["link"])) in EU):
            st["(info) gia_consegnati_in_lista"] += 1
        if hashlib.sha1(x["tel"][-9:].encode()).hexdigest()[:16] in chiamati:
            st["gia_chiamati"] += 1
            continue
        if nn and nn in visti_nomi:
            st["stessa_azienda"] += 1
            continue
        visti_nomi.add(nn)
        buoni.append(x)
    L.warning("SMS_STRUTT filtri: %s, candidati %d", json.dumps(st), len(buoni))
    return [(0, x["tel"]) for x in buoni], buoni


def _migrazioni_in_sottofondo():
    """4/11: se all'avvio il lucchetto delle migrazioni era occupato (init_db le ha rimandate), il sito le riprova
    qui ogni 30 secondi finché non sono tutte fatte. Quando non manca niente, il primo giro finisce subito."""
    from ..db import migrate, MigrazioniRimandate
    lg = logging.getLogger("migrazioni")

    def giro():
        for i in range(240):
            try:
                with connect() as conn:
                    fatte = migrate(conn, attesa_max=20)
                if fatte:
                    lg.warning("MIGRAZIONI applicate in sottofondo: %s", fatte)
                return
            except MigrazioniRimandate:
                pass
            except Exception as e:
                lg.warning("MIGRAZIONI in sottofondo non riuscite: %s", str(e)[:300])
            time.sleep(30)

    threading.Thread(target=giro, daemon=True, name="migrazioni").start()


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
                    from ..pipeline.ciclo import una_volta_sicura      # 6/11: riga chiusa anche se il sito si riavvia a metà
                    st = None
                    with una_volta_sicura("telefoni_subito:v3:" + giorno, tentativi_max=6) as fare:
                        if fare:
                            with _c() as conn:
                                st = tr.giro_telefoni_subito(conn)
                    if st is None:
                        time.sleep(3600)
                        continue
                    lg.warning("TR TELEFONI SUBITO in %.0fs: %s", time.time() - t0, st)
                    _elenco["t"] = 0.0
                    _aggiorna_elenco(attendi=False)
                    if os.environ.get("UTIL_SIM", "1") == "1" and not _ricalcolo_sito():
                        # 3/11 fase 1: il calcolo completo lo fa il cron (collect "ricalcolo"), non il sito (512 MB)
                        lg.warning("RICALCOLO_SITO spento: utilitarie dopo i telefoni Subito lasciate al cron "
                                   "(POLICY_RICALCOLO_SITO=1 per rifarle qui)")
                    elif os.environ.get("UTIL_SIM", "1") == "1":     # subito il calcolo completo con i numeri nuovi
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
                        from ..pipeline.ciclo import una_volta_sicura   # 6/11: riga chiusa anche dopo un riavvio
                        with una_volta_sicura("subito_completo:" + giorno, tentativi_max=6) as fare:
                            if fare:
                                with _c() as conn:
                                    tr.subito_completo(conn)
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

    def brevo_liste(v: str | None = None):
        """22/10 (cliente): carica una volta in Brevo le 6 liste dei commercianti (data/marketing_gruppi.json).
        8/11: più lavori una tantum insieme separati da '+' (es. 'zona:0811+utenti:0811'), uno dopo l'altro."""
        if v is None:
            v = os.environ.get("BREVO_IMPORT_LISTE") or ""
            if "+" in v and not v.startswith(("mix:", "rimetti:")):
                for parte in [x.strip() for x in v.split("+") if x.strip()]:
                    try:
                        brevo_liste(parte)
                    except Exception as e:
                        lg.warning("LAVORO %s non riuscito: %s", parte.split(":")[0], str(e)[:200])
                return
        if v.startswith("mix:"):              # 5/11 (cliente): nuove + richiamo del 3/11 fino a 1.200, invio subito
            time.sleep(30)
            try:
                from ..marketing import brevo_liste as bl
                from ..pipeline.ciclo import _una_volta
                if _una_volta(f"brevo_mix:{v}"):
                    lg.warning("BREVO_MIX %s", json.dumps(bl.mix_e_invia(int(v.split(":")[1]))))
                else:
                    lg.warning("BREVO_MIX %s già fatto", v)
            except Exception as e:
                lg.warning("BREVO_MIX non riuscito: %s", str(e)[:300])
            return
        if v.startswith("su_misura2:"):       # 8/11 pomeriggio (cliente): chi oggi non ha ricevuto, campagna in bozza + esempi
            time.sleep(30)
            try:
                from ..marketing import su_misura as SM
                from ..pipeline.ciclo import _una_volta
                if not _una_volta(f"su_misura2:{v}"):
                    lg.warning("SU_MISURA2 %s già fatto", v)
                    return
                for _ in range(30):
                    if _elenco.get("items"):
                        break
                    time.sleep(60)
                lg.warning("SU_MISURA2 %s", json.dumps(SM.prepara_secondo(list(_elenco.get("items") or []),
                                                                          totale=int(v.split(":")[1] or 1100))))
            except Exception as e:
                lg.warning("SU_MISURA2 non riuscito: %s", str(e)[:300])
            return
        if v.startswith("togli_lista:"):      # 10/11 sera (cliente): dalla lista di domani via chi ha ricevuto le campagne indicate
            time.sleep(20)
            try:
                import httpx as _hx
                from ..marketing.brevo_liste import _esporta, API as _API
                from ..marketing.su_misura import _chiave
                from ..pipeline.ciclo import _una_volta
                if not _una_volta(f"togli_lista:{v}"):
                    lg.warning("TOGLI_LISTA %s già fatto", v)
                    return
                lista, camp = v.split(":", 1)[1].split("@", 1)
                with _hx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=60) as c:
                    via = set()
                    for cid in camp.split(","):
                        if cid.startswith("L"):          # L82 = i contatti della lista 82 (chi ha ricevuto quella campagna)
                            off = 0
                            while True:
                                r = c.get(f"{_API}/contacts/lists/{int(cid[1:])}/contacts", params={"limit": 500, "offset": off})
                                cs = (r.json() or {}).get("contacts") or [] if r.status_code < 300 else []
                                via |= {x["email"].lower() for x in cs if x.get("email")}
                                if len(cs) < 500:
                                    break
                                off += 500
                        else:
                            via |= set(_esporta(c, int(cid), "all"))
                    via = sorted(via)
                    tolti = 0
                    for k in range(0, len(via), 150):
                        r = c.post(f"{_API}/contacts/lists/{int(lista)}/contacts/remove", json={"emails": via[k:k + 150]})
                        if r.status_code < 300:
                            tolti += len((r.json().get("contacts") or {}).get("success") or [])
                    rl = c.get(f"{_API}/contacts/lists/{int(lista)}").json()
                lg.warning("TOGLI_LISTA %s", json.dumps({"da_togliere": len(via), "tolti": tolti,
                                                           "rimasti": rl.get("uniqueSubscribers")}))
            except Exception as e:
                lg.warning("TOGLI_LISTA non riuscito: %s", str(e)[:300])
            return
        if v.startswith(("fermi:", "fermi2:")):  # 12/11 sera (cliente): i contatti fermi nel database, non quelli di oggi
            time.sleep(20)
            try:
                from ..marketing import su_misura as SM
                from ..pipeline.ciclo import _una_volta
                if not _una_volta(f"fermi:{v[:80]}"):
                    lg.warning("FERMI %s già fatto", v)
                    return
                corpo, _, pref = v.split(":", 1)[1].partition("#")      # dopo '#': inizio impronte di chi ha ricevuto oggi
                n, quando = corpo.split("@", 1)
                pref = {x for x in pref.split(",") if len(x) == 16}
                lg.warning("FERMI %s", json.dumps(SM.ripesca_fermi(totale=int(n or 1400), quando=quando,
                                                                   oggi_pref=pref)))
            except Exception as e:
                lg.warning("FERMI non riuscito: %s", str(e)[:300])
            return
        if v.startswith("nuovi_sm:"):         # 10/11 sera (cliente): 1.450 dal nostro archivio con esempi nuovi, programmata
            time.sleep(30)
            try:
                from ..marketing import su_misura as SM
                from ..pipeline.ciclo import _una_volta
                if not _una_volta(f"nuovi_sm:{v}"):
                    lg.warning("NUOVI_SM %s già fatto", v)
                    return
                for _ in range(30):
                    if _elenco.get("items"):
                        break
                    time.sleep(60)
                n, quando = v.split(":", 1)[1].split("@", 1)
                lg.warning("NUOVI_SM %s", json.dumps(SM.prepara_rinvio(
                    list(_elenco.get("items") or []), campagne=(70, 72, 73), quando=quando, totale=int(n or 1450),
                    solo_non_aperti=False, nome="Skovo esempi nuovi " + quando[8:10] + "-" + quando[5:7],
                    oggetto="Due nuove auto da privati sotto mercato")))
            except Exception as e:
                lg.warning("NUOVI_SM non riuscito: %s", str(e)[:300])
            return
        if v.startswith("rinvio_sm:"):        # 9/11 sera (cliente): rinvio su misura a chi non ha aperto 70/72, programmato
            time.sleep(30)
            try:
                from ..marketing import su_misura as SM
                from ..pipeline.ciclo import _una_volta
                if not _una_volta(f"rinvio_sm:{v}"):
                    lg.warning("RINVIO_SM %s già fatto", v)
                    return
                for _ in range(30):
                    if _elenco.get("items"):
                        break
                    time.sleep(60)
                camp, quando = v.split(":", 1)[1].split("@", 1)
                lg.warning("RINVIO_SM %s", json.dumps(SM.prepara_rinvio(list(_elenco.get("items") or []),
                                                                     campagne=tuple(int(x) for x in camp.split(",")),
                                                                     quando=quando)))
            except Exception as e:
                lg.warning("RINVIO_SM non riuscito: %s", str(e)[:300])
            return
        if v.startswith("su_misura:"):        # 8/11 (cliente): 1.450 email su misura (2 auto vicine al salone), a scaglioni
            time.sleep(30)
            try:
                from ..marketing import su_misura as SM
                from ..pipeline.ciclo import _una_volta
                if not _una_volta(f"su_misura:{v}"):
                    lg.warning("SU_MISURA %s già fatto", v)
                    return
                for _ in range(30):
                    if _elenco.get("items"):
                        break
                    time.sleep(60)
                n_tot = v.split(":")[1]
                lg.warning("SU_MISURA %s", json.dumps(SM.prepara(list(_elenco.get("items") or []),
                                                                 totale=int(n_tot.rstrip("b") or 1450),
                                                                 crea_campagne=not n_tot.endswith("b"))))
            except Exception as e:
                lg.warning("SU_MISURA non riuscito: %s", str(e)[:300])
            return
        if v.startswith("conta_email:"):      # 8/11 (cliente): quante email abbiamo e quante si possono usare (solo numeri)
            try:
                from ..marketing import brevo_liste as bl
                lg.warning("CONTA_EMAIL %s", json.dumps(bl.conteggio_email()))
            except Exception as e:
                lg.warning("CONTA_EMAIL non riuscito: %s", str(e)[:300])
            return
        if v.startswith("pulita:"):           # 8/11 (cliente): lista di chi apre, pulita, per il nuovo mittente (non invia)
            time.sleep(30)
            try:
                from ..marketing import brevo_liste as bl
                from ..pipeline.ciclo import _una_volta
                if _una_volta(f"brevo_pulita:{v}"):
                    lg.warning("BREVO_PULITA %s", json.dumps(bl.lista_pulita(totale=int(v.split(":")[1] or 400))))
                else:
                    lg.warning("BREVO_PULITA %s già fatto", v)
            except Exception as e:
                lg.warning("BREVO_PULITA non riuscito: %s", str(e)[:300])
            return
        if v.startswith("cliccati:"):         # 9/11 (cliente): chi ha cliccato le email, elenco per email a info@
            time.sleep(30)
            try:
                from ..marketing import brevo_liste as bl
                from ..pipeline.ciclo import _una_volta
                if _una_volta(f"brevo_cliccati:{v}"):
                    ids = [int(x) for x in v.split(":")[1].split(",") if x.strip()]
                    lg.warning("BREVO_CLICCATI %s", json.dumps(bl.cliccati(ids)))
                else:
                    lg.warning("BREVO_CLICCATI %s già fatto", v)
            except Exception as e:
                lg.warning("BREVO_CLICCATI non riuscito: %s", str(e)[:300])
            return
        if v.startswith("aperture:"):         # 4/11 sera (cliente): chi ha aperto la campagna, da chiamare
            time.sleep(30)
            try:
                from ..marketing import brevo_liste as bl
                from ..pipeline.ciclo import _una_volta
                if _una_volta(f"brevo_aperture:{v}"):
                    r = bl.aperture(int(v.split(":")[1]))
                    hs = r.pop("h", [])
                    lg.warning("BREVO_APERTURE %s", json.dumps(r))
                    for i in range(0, len(hs), 100):
                        lg.warning("BREVO_APERTURE_H %d %s", i, " ".join(hs[i:i + 100]))
                else:
                    lg.warning("BREVO_APERTURE %s già fatto", v)
            except Exception as e:
                lg.warning("BREVO_APERTURE non riuscito: %s", str(e)[:300])
            return
        if v.startswith("utenti:"):           # 6/11 sera (cliente): quanto usano il sito i commercianti registrati (niente email nei log)
            time.sleep(20)
            try:
                from ..pipeline.ciclo import _una_volta
                if not _una_volta("utenti:" + v.split(":", 1)[1]):
                    lg.warning("UTENTI già fatto")
                    return
                with connect() as conn:
                    righe = conn.execute("""
                        SELECT d.id, d.name, d.created_at::date AS dal, d.active, d.disattivato_da_admin AS spento,
                               d.abbonamento_stato AS stato, d.abbonamento_fino::date AS fino, d.accesso_fino::date AS accesso,
                               d.richiede_abbonamento AS richiede, d.stripe_customer IS NOT NULL AS stripe,
                               (SELECT a.piano FROM abbonamenti a WHERE a.dealer_id=d.id ORDER BY a.aggiornato_at DESC LIMIT 1) AS piano,
                               (SELECT a.stato FROM abbonamenti a WHERE a.dealer_id=d.id ORDER BY a.aggiornato_at DESC LIMIT 1) AS stato_stripe,
                               (SELECT count(*) FROM dealer_sessions s WHERE s.dealer_id=d.id) AS sessioni,
                               (SELECT max(s.last_seen_at)::timestamp(0) FROM dealer_sessions s WHERE s.dealer_id=d.id) AS ultimo,
                               (SELECT count(DISTINCT s.last_seen_at::date) FROM dealer_sessions s WHERE s.dealer_id=d.id
                                  AND s.last_seen_at > now()-interval '30 days') AS giorni30,
                               (SELECT count(*) FROM listing_opens o WHERE o.dealer_id=d.id) AS aperture,
                               (SELECT count(*) FROM listing_opens o WHERE o.dealer_id=d.id AND o.first_opened_at > now()-interval '7 days') AS aperture7,
                               (SELECT count(*) FROM listing_opens o WHERE o.dealer_id=d.id AND o.first_opened_at > now()-interval '30 days') AS aperture30,
                               (SELECT max(o.first_opened_at)::timestamp(0) FROM listing_opens o WHERE o.dealer_id=d.id) AS ultima_apertura,
                               (SELECT count(*) FROM appuntamenti p WHERE p.dealer_id=d.id) AS appuntamenti,
                               (SELECT json_object_agg(status, n) FROM (SELECT status, count(*) n FROM dealer_feedback f
                                  WHERE f.dealer_id=d.id GROUP BY status) x) AS esiti
                        FROM dealers d ORDER BY d.id""").fetchall()
                for i in range(0, len(righe), 10):
                    lg.warning("UTENTI_RIGHE %d %s", i, json.dumps([dict(r) for r in righe[i:i + 10]], ensure_ascii=False, default=str))
                lg.warning("UTENTI_FINE %d", len(righe))
            except Exception as e:
                lg.warning("UTENTI non riuscito: %s", str(e)[:300])
            return
        if v.startswith("zona:"):             # 7/11 (cliente): quante auto per provincia (sito e annunci attivi), nessun dato personale
            time.sleep(30)
            try:
                from ..pipeline.ciclo import _una_volta
                from . import sito_regole as _SR
                if not _una_volta("zona:" + v.split(":", 1)[1][:40]):
                    lg.warning("ZONA_CONTA già fatto")
                    return
                for _ in range(30):
                    if _elenco.get("items"):
                        break
                    time.sleep(60)
                sito, fascia_p = {}, {}
                for it in list(_elenco.get("items") or []):
                    import re as _re
                    m = _re.search(r"\(([A-Z]{2})\)", str(it.get("zona") or ""))
                    pr = (m.group(1) if m else None) or _SR._r(it).get("provincia") or it.get("provincia") or "?"
                    sito[pr] = sito.get(pr, 0) + 1
                    if _SR.fascia(it):
                        fascia_p[pr] = fascia_p.get(pr, 0) + 1
                with connect() as conn:
                    righe = conn.execute(
                        "SELECT province, count(*) AS n, count(*) FILTER (WHERE seller_type = 'privato') AS privati, "
                        "count(*) FILTER (WHERE first_seen_at > now() - interval '7 days') AS nuove_7g, "
                        "count(*) FILTER (WHERE price_eur <= 10000) AS fino_10k "
                        "FROM listings WHERE status = 'attivo' AND source IN ('subito', 'autoscout24') GROUP BY 1 ORDER BY 2 DESC LIMIT 40").fetchall()
                lg.warning("ZONA_SITO %s", json.dumps({"sito": dict(sorted(sito.items(), key=lambda x: -x[1])),
                                                        "fascia": dict(sorted(fascia_p.items(), key=lambda x: -x[1]))}))
                lg.warning("ZONA_ATTIVI %s", json.dumps([dict(r) for r in righe], default=str))
            except Exception as e:
                lg.warning("ZONA_CONTA non riuscito: %s", str(e)[:300])
            return
        if v.startswith("wa30:"):             # 6/11 notte (cliente): profili di 30 commercianti + auto del sito per WhatsApp su misura
            time.sleep(30)
            try:
                import hashlib
                from ..pipeline.ciclo import _una_volta
                if not _una_volta(f"wa30:{hashlib.sha256(v.encode()).hexdigest()[:10]}"):
                    lg.warning("WA30 già fatto")
                    return
                cerca = set(v.split(":", 1)[1].split(","))

                def h(t):
                    d = re.sub(r"\D", "", str(t or ""))
                    d = d[2:] if d.startswith("39") and len(d) > 10 else d
                    return hashlib.sha256(d.encode()).hexdigest()[:12]
                with connect() as conn:
                    righe = conn.execute("SELECT tel, altri_tel, nome, comune, prov, auto, prezzo_min, prezzo_mediano, prezzo_max, "
                                         "fascia, quota_benzina, marche, link FROM lead_tutti").fetchall()
                trovati = 0
                for r in righe:
                    hs = [h(r["tel"])] + [h(t) for t in str(r["altri_tel"] or "").split() if t]
                    hit = next((x for x in hs if x in cerca), None)
                    if hit:
                        trovati += 1
                        lg.warning("WA30_PROFILO %s", json.dumps({"h": hit, "nome": r["nome"], "comune": r["comune"], "prov": r["prov"],
                                   "auto": r["auto"], "pmin": r["prezzo_min"], "pmed": r["prezzo_mediano"], "pmax": r["prezzo_max"],
                                   "fascia": r["fascia"], "benz": r["quota_benzina"], "marche": r["marche"], "link": r["link"]},
                                   ensure_ascii=False, default=str))
                lg.warning("WA30_PROFILI trovati %d su %d cercati", trovati, len(cerca))
                for _ in range(30):
                    if _elenco.get("items"):
                        break
                    time.sleep(60)
                el = []
                for it in list(_elenco.get("items") or []):
                    pv = it.get("pv") or {}
                    el.append([it.get("id"), it.get("marca"), it.get("modello"), it.get("nome"), it.get("anno"), it.get("km"),
                               it.get("carb"), it.get("cambio"), it.get("prezzo"), pv.get("R_finale") or pv.get("R"), pv.get("margine"),
                               pv.get("n"), it.get("area"), it.get("zona"), it.get("fonte"), str(it.get("nuova") or "")[:16], it.get("n_foto")])
                for i in range(0, len(el), 20):
                    lg.warning("WA30_AUTO %d %s", i, json.dumps(el[i:i + 20], ensure_ascii=False, default=str))
                lg.warning("WA30_FINE auto=%d", len(el))
            except Exception as e:
                lg.warning("WA30 non riuscito: %s", str(e)[:300])
            return
        if v.startswith("rimetti:"):          # 4/11 notte (cliente): "rimetti:<id campagna>" una volta sola
            time.sleep(30)
            try:
                from ..marketing import brevo_liste as bl
                from ..pipeline.ciclo import _una_volta
                if _una_volta(f"brevo_rimetti:{v}"):
                    lg.warning("BREVO_RIMETTI %s", json.dumps(bl.rimetti_e_invia(int(v.split(":")[1]))))
                else:
                    lg.warning("BREVO_RIMETTI %s già fatto", v)
            except Exception as e:
                lg.warning("BREVO_RIMETTI non riuscito: %s", str(e)[:300])
            return
        if v != "1":
            return
        time.sleep(20)
        try:
            from ..marketing import brevo_liste as bl
            from ..pipeline.ciclo import _una_volta
            # 4/11: una volta per gruppo di liste, non a ogni avvio (prima ogni riavvio reimportava tutto)
            if not _una_volta(f"brevo_liste:{bl.NOME_GRUPPI}"):
                lg.warning("BREVO_LISTE %s già importate: niente (togli BREVO_IMPORT_LISTE da Render)", bl.NOME_GRUPPI)
                return
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
                     "BENZ_PROVINCE": ",".join(sorted({x.strip().upper() for x in (os.environ.get("RZ_PROVINCE", "BG,BS,CR,LC,MI,MB") + "," + (os.environ.get("ZONA_SITO") or "")).split(",") if x.strip()})), "BENZ_PASSO": os.environ.get("RZ_PASSO", "1000"),
                     "BENZ_SOLO_PRIVATI": "1", "BENZ_RACCOLTA_TUTTI": "0", "BENZ_SUB_PROFONDA": "0", "BENZ_TUTTI_CARB": os.environ.get("RZ_TUTTI_CARB", "1"),
                     "BENZ_KM_MAX": os.environ.get("RZ_KM_MAX", "180000"), "BENZ_SOLO_NUOVE": os.environ.get("RZ_SOLO_NUOVE", "0"), "BENZ_SOLO_INSUFF": os.environ.get("RZ_SOLO_INSUFF", "0"),
                     "BENZ_FOCUS": os.environ.get("RZ_FOCUS", "0"),
                     "BENZ_DAL": os.environ.get("RZ_DAL", "2026-09-01 00:00"), "BENZ_SUB_PAGINE": os.environ.get("RZ_SUB_PAGINE", "200"),
                     "BENZ_MAX": os.environ.get("RZ_CANDIDATE", "1500"), "BENZ_SOGLIA": os.environ.get("RZ_SOGLIA", "1.1"),
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
            # 2/11: dopo la sola valutazione, subito la ricerca mirata (raccolta + valutazione delle nuove), in sequenza
            if os.environ.get("BENZ_SOLO_VALUTA") == "1" and os.environ.get("BENZ_FOCUS") == "1":
                os.environ["BENZ_SOLO_VALUTA"] = "0"
                os.environ["BENZ_SOLO_NUOVE"] = "1"
                esito = B.giro()
                lg.warning("RACCOLTA_ZONA mirata fatta: %s", {k: v for k, v in esito.items() if isinstance(v, int)})
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
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
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
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
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
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
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
                with connect() as conn:
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
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
        from ..marketing import rinvio
        try:
            if _una_volta(f"rinvio:{nome}"):        # 4/11: _una_volta (segnato prima di partire, atomico): un riavvio a metà invio non rimanda
                with connect() as conn:
                    fin = log_job(conn, f"rinvio:{nome}")
                    fin(True, {"lista": rinvio.lista(nome)})
        except Exception as e:
            lg.warning("RINVIO_LISTA non riuscita: %s", str(e)[:300])
    threading.Thread(target=rinvio_lista, daemon=True, name="rinvio").start()

    def rinvio_invia():
        """29/10 (cliente: "rimandala a tutti"): manda una volta la campagna preparata (RINVIO_INVIA=<id campagna>)."""
        cid = os.environ.get("RINVIO_INVIA")
        if not cid or not cid.isdigit():
            return
        time.sleep(45)
        from ..db import log_job
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
        from ..marketing import rinvio
        try:
            if _una_volta(f"rinvio_invia:{cid}"):   # 4/11: _una_volta (segnato prima di partire, atomico): un riavvio a metà invio non rimanda
                with connect() as conn:
                    fin = log_job(conn, f"rinvio_invia:{cid}")
                    esito = rinvio.invia(int(cid))
                    fin(esito < 300, {"esito": esito})
        except Exception as e:
            lg.warning("RINVIO_INVIA non riuscito: %s", str(e)[:300])
    threading.Thread(target=rinvio_invia, daemon=True, name="rinvio_invia").start()

    def rinvio_non_aperti():
        """30/10 (cliente): rimanda alle 8 la campagna a chi non l'ha aperta (RINVIO_NONAPERTI=<id>, RINVIO_ORA=<ISO>), una volta."""
        cid, quando = os.environ.get("RINVIO_NONAPERTI"), os.environ.get("RINVIO_ORA")
        if not cid or not quando:
            return
        time.sleep(50)
        from ..db import log_job
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
        from ..marketing import rinvio
        try:
            if _una_volta(f"rinvio_nonaperti:{cid}"):   # 4/11: _una_volta (segnato prima di partire, atomico): un riavvio a metà invio non rimanda
                with connect() as conn:
                    fin = log_job(conn, f"rinvio_nonaperti:{cid}")
                    fin(True, rinvio.non_aperti(int(cid), quando))
        except Exception as e:
            lg.warning("RINVIO_NONAPERTI non riuscito: %s", str(e)[:300])
    threading.Thread(target=rinvio_non_aperti, daemon=True, name="rinvio_nonaperti").start()

    def rinvio_escludi():
        """30/10: toglie dal rinvio programmato chi ha aperto la campagna (RINVIO_ESCLUDI=<orig>:<nuova>), una volta."""
        v = os.environ.get("RINVIO_ESCLUDI")
        if not v or ":" not in v:
            return
        time.sleep(40)
        from ..db import log_job
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
        from ..marketing import rinvio
        try:
            if _una_volta(f"rinvio_escludi:{v}"):
                with connect() as conn:
                    fin = log_job(conn, f"rinvio_escludi:{v}")
                    a, b = v.split(":")[:2]
                    fin(True, rinvio.escludi_aperti(int(a), int(b)))
        except Exception as e:
            lg.warning("RINVIO_ESCLUDI non riuscito: %s", str(e)[:300])
    threading.Thread(target=rinvio_escludi, daemon=True, name="rinvio_escludi").start()

    def cerca_numero():
        """29/10 (cliente): a chi corrisponde un numero tra i COMMERCIANTI già nelle nostre liste (CERCA_NUMERO).
        Solo tabelle dei commercianti (mai annunci di privati); nel log nome, comune, provincia e fonte."""
        n = re.sub(r"\D", "", os.environ.get("CERCA_NUMERO") or "")[-10:]
        if len(n) < 9:
            return
        time.sleep(30)
        out = []
        q = [("lead_tutti", "SELECT nome, comune, prov, fonti AS fonte, link FROM lead_tutti WHERE tel LIKE %(p)s OR altri_tel LIKE %(p)s"),
             ("lead_tutti_dealer", "SELECT nome, comune, prov, fonte, chiave AS link FROM lead_tutti_dealer WHERE array_to_string(cell, ',') LIKE %(p)s"),
             ("lead_maps", "SELECT nome, comune, prov, categoria AS fonte, sito AS link FROM lead_maps WHERE regexp_replace(COALESCE(tel,''), '\\D', '', 'g') LIKE %(p)s"),
             ("commercianti_as24", "SELECT nome, NULL AS comune, NULL AS prov, 'autoscout24' AS fonte, seller_id AS link FROM commercianti_as24 WHERE array_to_string(cellulari, ',') LIKE %(p)s")]
        for tab, sql in q:
            try:
                with connect() as conn:
                    for r in conn.execute(sql, {"p": f"%{n}%"}).fetchall():
                        out.append(dict(r, tabella=tab))
            except Exception as e:
                lg.warning("CERCA_NUMERO %s: %s", tab, str(e)[:120])
        lg.warning("CERCA_NUMERO %d risultati: %s", len(out), json.dumps(out, ensure_ascii=False, default=str)[:3000])
    threading.Thread(target=cerca_numero, daemon=True, name="cerca_numero").start()

    def diagnosi_sito():
        """31/10 notte (cliente: "perché le auto normali non aumentano mai? guarda il software"): una volta (DIAGNOSI_SITO=<tag>),
        conta quante auto di privati della zona si perdono a ogni passo, dal database al sito. Solo conteggi nel log."""
        tag = os.environ.get("DIAGNOSI_SITO")
        if not tag:
            return
        time.sleep(20 if tag.startswith(("b10", "tel:")) else 150)
        from collections import Counter
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
        L = logging.getLogger("scovo")
        try:
            if not _una_volta(f"diagnosi_sito:{tag}"):
                return
            prov = [p.strip().upper() for p in (os.environ.get("ZONA_SITO") or "").split(",") if p.strip()]
            if tag.startswith("tel:"):
                # 2/11 (cliente: "chi è questo numero tra i nostri lead"): cerca numeri (ultime 9 cifre) ovunque
                with connect() as conn:
                    conn.execute("SET statement_timeout = '300s'")
                    for n in [x.strip()[-9:] for x in tag[4:].split(",") if x.strip()]:
                        like = "%" + n + "%"
                        out = {}
                        for nome, q in (
                            ("annunci", "SELECT id, source, url, title, make, model, year, price_eur, seller_type, city, province, status, "
                                        "first_seen_at::date AS dal FROM listings WHERE regexp_replace(coalesce(description,''),'[^0-9]','','g') LIKE %(l)s LIMIT 10"),
                            ("maps", "SELECT nome, comune, prov, tel, cell, sito, categoria FROM lead_maps WHERE "
                                     "regexp_replace(coalesce(tel,''),'[^0-9]','','g') LIKE %(l)s LIMIT 5"),
                            ("dealer", "SELECT nome, comune, prov, cell, fonte FROM lead_tutti_dealer WHERE "
                                       "regexp_replace(coalesce(cell::text,''),'[^0-9]','','g') LIKE %(l)s LIMIT 5"),
                            ("as24", "SELECT nome, cellulari::text AS cell FROM commercianti_as24 WHERE "
                                     "regexp_replace(coalesce(cellulari::text,''),'[^0-9]','','g') LIKE %(l)s LIMIT 5")):
                            try:
                                out[nome] = [dict(r) for r in conn.execute(q, {"l": like}).fetchall()]
                            except Exception as e:
                                conn.rollback(); out[nome] = "err " + str(e)[:120]
                        L.warning("CERCA_TEL %s %s", n, json.dumps(out, default=str, ensure_ascii=False)[:3000])
                return
            if tag.startswith("b10"):
                # 2/11: imbuto solo per benzina <=10.000 EUR, <=100.000 km, privati, zona sito
                with connect() as conn:
                    conn.execute("SET statement_timeout = '300s'")
                    F = """FROM listings l LEFT JOIN LATERAL (SELECT categoria, margine, riassunto FROM valutazioni_policy v
                             WHERE v.listing_id=l.id ORDER BY computed_at DESC LIMIT 1) u ON true
                           WHERE l.status='attivo' AND COALESCE(l.seller_type,'privato') <> 'commerciante'
                             AND upper(l.province) = ANY(%(p)s) AND l.fuel ILIKE '%%benzin%%'
                             AND l.price_eur BETWEEN 500 AND 10000 AND COALESCE(l.mileage_km,0) <= 100000"""
                    a = conn.execute("SELECT l.source, COALESCE(u.categoria, u.riassunto->>'categoria','(non valutata)') AS cat, count(*) AS n "
                                     + F + " GROUP BY 1,2 ORDER BY n DESC", {"p": prov}).fetchall()
                    L.warning("DIAGNOSI_B10 categorie %s", json.dumps([dict(r) for r in a], default=str))
                    b = conn.execute("""SELECT CASE WHEN u.margine IS NULL THEN 'null' WHEN u.margine < 0 THEN '<0'
                                        WHEN u.margine < 400 THEN '0-400' WHEN u.margine < 800 THEN '400-800'
                                        WHEN u.margine < 1500 THEN '800-1500' ELSE '>=1500' END AS fascia,
                                        (l.price_eur <= 4000) AS sotto4k, count(*) AS n """ + F +
                                     " AND COALESCE(u.categoria, u.riassunto->>'categoria') = 'non_conveniente' GROUP BY 1,2 ORDER BY 1,2",
                                     {"p": prov}).fetchall()
                    L.warning("DIAGNOSI_B10 margini_non_conv %s", json.dumps([dict(r) for r in b], default=str))
                    t = conn.execute("SELECT count(*) AS n, count(*) FILTER (WHERE l.last_seen_at > now() - interval '48 hours') AS viste48 "
                                     "FROM listings l WHERE l.status='attivo' AND COALESCE(l.seller_type,'privato') <> 'commerciante' "
                                     "AND upper(l.province) = ANY(%(p)s) AND l.fuel ILIKE '%%benzin%%' AND l.price_eur BETWEEN 500 AND 10000 "
                                     "AND COALESCE(l.mileage_km,0) <= 100000", {"p": prov}).fetchone()
                    L.warning("DIAGNOSI_B10 totale %s", dict(t))
                    if tag.startswith("b10d"):
                        x = conn.execute("""SELECT l.id, l.source, l.make, l.model, l.year, l.mileage_km, l.price_eur, u.margine,
                                  u.riassunto->>'soglia' AS soglia, u.riassunto->>'n' AS n, u.riassunto->>'livello' AS liv,
                                  u.riassunto->>'R_finale' AS rf, u.riassunto->>'M' AS m
                                  """ + F + """ AND COALESCE(u.categoria, u.riassunto->>'categoria') = 'non_conveniente'
                                  AND u.margine >= 1500 LIMIT 40""", {"p": prov}).fetchall()
                        L.warning("DIAGNOSI_B10D nonconv_margine_alto %s", json.dumps([dict(r) for r in x], default=str))
                        y = conn.execute("""SELECT left(regexp_replace(COALESCE(v.dati->'motivi'->>0, ''), '[0-9][0-9.]*', 'N', 'g'), 80) AS motivo,
                                  u.riassunto->>'n' AS n, count(*) AS c FROM listings l
                                  JOIN LATERAL (SELECT categoria, margine, riassunto, dati FROM valutazioni_policy v
                                     WHERE v.listing_id=l.id ORDER BY computed_at DESC LIMIT 1) v ON true
                                  CROSS JOIN LATERAL (SELECT v.riassunto) u
                                  WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%(p)s)
                                  AND l.fuel ILIKE '%%benzin%%' AND l.price_eur BETWEEN 500 AND 10000 AND COALESCE(l.mileage_km,0) <= 100000
                                  AND v.categoria = 'dati_insufficienti' GROUP BY 1,2 ORDER BY c DESC LIMIT 25""", {"p": prov}).fetchall()
                        L.warning("DIAGNOSI_B10D insufficienti_privati %s", json.dumps([dict(r) for r in y], default=str))
                        z = conn.execute("""SELECT CASE WHEN u.margine < 0 THEN '<0' WHEN u.margine < 800 THEN '0-800'
                                  WHEN u.margine < 1500 THEN '800-1500' ELSE '>=1500' END AS f, count(*) AS c
                                  """ + F + """ AND l.seller_type='privato' AND COALESCE(u.categoria, u.riassunto->>'categoria') = 'non_conveniente'
                                  GROUP BY 1""", {"p": prov}).fetchall()
                        L.warning("DIAGNOSI_B10D nonconv_privati %s", json.dumps([dict(r) for r in z], default=str))
                if not tag.startswith("b10s") and not tag.startswith("b10d"):
                    return
            with connect() as conn:
                conn.execute("SET LOCAL statement_timeout = '300s'")
                q = conn.execute("""
                    WITH ult AS (SELECT DISTINCT ON (listing_id) listing_id, riassunto->>'categoria' AS cat, computed_at
                                 FROM valutazioni_policy ORDER BY listing_id, computed_at DESC)
                    SELECT count(*) AS tot,
                      count(*) FILTER (WHERE u.cat IN ('opportunita','candidato')) AS buone,
                      count(*) FILTER (WHERE u.cat IN ('opportunita','candidato') AND l.stage='approfondito') AS buone_approf,
                      count(*) FILTER (WHERE u.cat IN ('opportunita','candidato') AND l.stage_reason IN ('opportunita','da_verificare','incidentata')) AS buone_reason,
                      count(*) FILTER (WHERE u.cat IN ('opportunita','candidato') AND l.last_checked_at > now() - interval '48 hours') AS buone_fresche,
                      count(*) FILTER (WHERE u.cat IN ('opportunita','candidato') AND l.last_seen_at > now() - interval '48 hours') AS buone_viste48,
                      count(*) FILTER (WHERE u.cat IN ('opportunita','candidato') AND l.stage='approfondito'
                                         AND l.stage_reason IN ('opportunita','da_verificare','incidentata')
                                         AND l.last_checked_at > now() - interval '48 hours') AS buone_in_lista
                    FROM listings l JOIN ult u ON u.listing_id = l.id
                    WHERE l.status='attivo' AND COALESCE(l.seller_type,'privato') <> 'commerciante' AND l.province = ANY(%(p)s)""",
                    {"p": prov}).fetchone()
                L.warning("DIAGNOSI_SITO db zona privati attivi: %s", dict(q))
                q2 = conn.execute("""
                    WITH ult AS (SELECT DISTINCT ON (listing_id) listing_id, riassunto->>'categoria' AS cat
                                 FROM valutazioni_policy ORDER BY listing_id, computed_at DESC)
                    SELECT l.stage, l.stage_reason, (l.last_checked_at > now() - interval '48 hours') AS fresca, count(*) AS n
                    FROM listings l JOIN ult u ON u.listing_id = l.id
                    WHERE l.status='attivo' AND COALESCE(l.seller_type,'privato') <> 'commerciante' AND l.province = ANY(%(p)s)
                      AND u.cat IN ('opportunita','candidato')
                    GROUP BY 1,2,3 ORDER BY n DESC LIMIT 15""", {"p": prov}).fetchall()
                L.warning("DIAGNOSI_SITO buone per stato: %s", [dict(r) for r in q2])
            # 4/11: l'imbuto del sito ora è uno solo (sito_regole), con i contatori di ogni regola
            from . import sito_regole as SR
            cand, _o, st = _carica_candidati()
            _out, fun = SR.elenco_sito(cand, scrivi_log=False, caricamento=st)
            L.warning("DIAGNOSI_SITO imbuto %s", json.dumps({"caricamento": st, "passi": fun.passi}, ensure_ascii=False))
        except Exception as e:
            L.warning("DIAGNOSI_SITO non riuscita: %s", str(e)[:300])
    threading.Thread(target=diagnosi_sito, daemon=True, name="diagnosi_sito").start()

    def diagnosi_scarti_foto():
        """31/10 (cliente: "ricontrolla le auto scartate dal controllo foto"): una volta (DIAGNOSI_SCARTI=<tag>) elenca nel
        log le auto di privati della zona con margine buono tolte dall'analisi manuale, con il motivo scritto allora."""
        tag = os.environ.get("DIAGNOSI_SCARTI")
        if not tag:
            return
        time.sleep(200)
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
        from ..pipeline.manuale import FILE as _F
        L = logging.getLogger("scovo")
        try:
            if not _una_volta(f"diagnosi_scarti:{tag}"):
                return
            prov = [p.strip().upper() for p in (os.environ.get("ZONA_SITO") or "").split(",") if p.strip()]
            note = {e["id"]: e for e in json.loads(_F.read_text())}
            with connect() as conn:
                rows = conn.execute("""
                    WITH ult AS (SELECT DISTINCT ON (listing_id) listing_id, riassunto->>'categoria' AS cat,
                                        (riassunto->>'margine')::float AS marg FROM valutazioni_policy ORDER BY listing_id, computed_at DESC)
                    SELECT l.id, l.price_eur, l.year, l.mileage_km, l.make, l.model, u.marg
                    FROM listings l JOIN ult u ON u.listing_id=l.id
                    WHERE l.status='attivo' AND COALESCE(l.seller_type,'privato') <> 'commerciante' AND l.province = ANY(%s)
                      AND u.cat IN ('opportunita','candidato') AND l.stage='scartato'
                      AND l.stage_reason='analisi_manuale: controllo foto'""", (prov,)).fetchall()
            out = []
            for r in rows:
                e = note.get(r["id"]) or {}
                out.append([r["id"], r["make"], r["model"], r["year"], r["mileage_km"], r["price_eur"], int(r["marg"] or 0),
                            e.get("esito"), e.get("danno"), (e.get("nota") or "")[:140]])
            for i in range(0, len(out), 25):
                L.warning("DIAGNOSI_SCARTI %d/%d %s", i, len(out), json.dumps(out[i:i + 25], ensure_ascii=False, default=str))
        except Exception as e:
            L.warning("DIAGNOSI_SCARTI non riuscita: %s", str(e)[:300])
    threading.Thread(target=diagnosi_scarti_foto, daemon=True, name="diagnosi_scarti").start()

    def lead_maps_web():
        """1/11 (cliente): commercianti di Milano e dintorni da Google Maps via Apify (APIFY_TOKEN sul servizio web),
        poi le email lette dai loro siti. Una volta per etichetta (LEADMAPS_WEB=<tag>). Nei log solo conteggi."""
        tag = os.environ.get("LEADMAPS_WEB")
        L = logging.getLogger("scovo")
        if not tag:
            return
        if not (os.environ.get("OUTSCRAPER_KEY") or os.environ.get("APIFY_TOKENS") or os.environ.get("APIFY_TOKEN")):
            L.warning("LEADMAPS_WEB fermo: APIFY_TOKEN non c'è sul servizio web")
            return
        time.sleep(120)
        L.warning("LEADMAPS_WEB parte (%s, province %s)", tag, os.environ.get("MAPS_SOLO_PROV", "MI"))
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            from ..db import log_job
            if not _due(f"leadmaps_web:{tag}", 100000):
                return
            os.environ.setdefault("MAPS_SOLO_PROV", "MI")
            from ..pipeline import lead_maps as LM, lead_email as LE
            with connect() as conn:
                fine = log_job(conn, f"leadmaps_web:{tag}")
                esito = LM.giro()
                if os.environ.get("MAPS_COMPLETA_SITI") == "1":
                    try:
                        esito["siti"] = LM.completa_siti(int(os.environ.get("MAPS_SITI_MAX", "300")))
                    except Exception as e:
                        L.warning("LEADMAPS siti non completati: %s", str(e)[:200])
                try:
                    esito["email"] = LE.giro()
                except Exception as e:
                    L.warning("LEADMAPS_WEB email non riuscite: %s", str(e)[:200])
                fine(True, esito)
            with connect() as conn:
                r = conn.execute("""SELECT count(*) AS posti, count(*) FILTER (WHERE m.sito IS NOT NULL) AS con_sito,
                                           count(*) FILTER (WHERE cardinality(e.email) > 0) AS con_email
                                    FROM lead_maps m LEFT JOIN lead_email e ON e.chiave = 'maps:' || m.place_id
                                    WHERE m.prov = 'MI'""").fetchone()
            L.warning("LEADMAPS_WEB fatto: %s · Milano e provincia: %s", esito, dict(r))
        except Exception as e:
            L.warning("LEADMAPS_WEB non riuscito: %s", str(e)[:300])
    threading.Thread(target=lead_maps_web, daemon=True, name="lead_maps_web").start()

    def lead_nomi_web():
        """2/11 notte (cliente: "1.000-1.500 email senza doppioni"): i commercianti dei nostri annunci (Subito, AutoScout24)
        della zona senza email: nome + comune su Google Maps (Outscraper), sito, email. Una volta per etichetta
        (LEADE_NOMI=<tag>), in parallelo al giro Maps. Niente Brevo. Nei log solo conteggi."""
        tag = os.environ.get("LEADE_NOMI")
        if not tag:
            return
        time.sleep(60)
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            from ..db import log_job
            if not _due(f"lead_nomi:{tag}", 100000):
                return
            from ..pipeline import lead_email as LE
            with connect() as conn:
                fine = log_job(conn, f"lead_nomi:{tag}")
                esito = LE.giro(solo_nomi=True)
                fine(True, esito)
        except Exception as e:
            L.warning("LEADE_NOMI non riuscito: %s", str(e)[:300])
    threading.Thread(target=lead_nomi_web, daemon=True, name="lead_nomi_web").start()

    def brevo_2_11():
        """2/11 notte (cliente): carica in Brevo le email della zona mai contattate e manda il testo corto (BREVO_2_11=<tag>)."""
        tag = os.environ.get("BREVO_2_11")
        if not tag:
            return
        time.sleep(60)
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            from ..db import log_job
            if not _una_volta(f"brevo_2_11:{tag}"):   # 4/11: _una_volta (segnato prima di partire, atomico): un riavvio a metà invio non rimanda
                return
            from ..marketing import auto_invio as AI
            with connect() as conn:
                fine = log_job(conn, f"brevo_2_11:{tag}")
                fine(True, AI.invia_2_11(tag))
        except Exception as e:
            logging.getLogger("scovo").warning("BREVO_2_11 non riuscito: %s", str(e)[:300])
    threading.Thread(target=brevo_2_11, daemon=True, name="brevo_2_11").start()

    def campagna_1_11():
        """1/11 (cliente): email a tutti i contatti Brevo con 3 testi e 2 auto per fascia (marketing/campagna_1_11.py).
        CAMPAGNA_1_11=prova solo log delle auto scelte; =invia manda una volta sola."""
        modo = os.environ.get("CAMPAGNA_1_11")
        if not modo:
            return
        L = logging.getLogger("scovo")
        time.sleep(150)
        for _ in range(30):
            if _elenco.get("items"):
                break
            time.sleep(20)
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            from ..db import log_job
            from ..marketing import campagna_1_11 as C
            if modo == "invia":
                if not _una_volta("campagna_1_11:invio"):   # 4/11: _una_volta (segnato prima di partire, atomico): un riavvio a metà invio non rimanda
                    L.warning("CAMPAGNA_1_11 già inviata: niente")
                    return
                with connect() as conn:
                    fine = log_job(conn, "campagna_1_11:invio")
                    fine(True, C.esegui(lambda: list(_elenco["items"]), "invia"))
            else:
                C.esegui(lambda: list(_elenco["items"]), "prova")
        except Exception as e:
            L.warning("CAMPAGNA_1_11 non riuscita: %s", str(e)[:300])
    threading.Thread(target=campagna_1_11, daemon=True, name="campagna_1_11").start()

    def diagnosi_insufficienti():
        """1/11 (cliente: "il software può fare di più"): perché le auto di privati della zona restano 'dati insufficienti'.
        Una volta per etichetta (DIAGNOSI_INSUFF=<tag>). Solo conteggi e 30 esempi anonimi (marca/modello/anno) nel log."""
        tag = os.environ.get("DIAGNOSI_INSUFF")
        if not tag:
            return
        time.sleep(180)
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _due(f"diag_insuff:{tag}", 100000):
                return
            from ..db import log_job
            prov = [p.strip().upper() for p in (os.environ.get("ZONA_SITO") or "").split(",") if p.strip()]
            with connect() as conn:
                fine = log_job(conn, f"diag_insuff:{tag}")
                conn.execute("SET statement_timeout = '300s'")
                rows = conn.execute("""
                    WITH ult AS (SELECT DISTINCT ON (listing_id) listing_id, dati, riassunto FROM valutazioni_policy
                                 ORDER BY listing_id, computed_at DESC)
                    SELECT l.id, l.make, l.model, l.fuel, l.year, l.mileage_km, l.gearbox, l.power_kw, l.version_raw, l.price_eur,
                           u.dati->'motivi' AS motivi, (u.riassunto->>'n')::int AS n
                    FROM listings l JOIN ult u ON u.listing_id = l.id
                    WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%s)
                      AND u.riassunto->>'categoria' = 'dati_insufficienti'""", (prov,)).fetchall()
                from collections import Counter as _C
                motivi, mancano, nconf = _C(), _C(), _C()
                pochi = []
                for r in rows:
                    m = (r["motivi"] or [""])
                    m0 = m[0] if isinstance(m, list) and m else str(m)
                    chiave = re.sub(r"\d[\d.]*", "N", str(m0))[:90]
                    motivi[chiave] += 1
                    if "confronti validi" in str(m0):
                        pochi.append(r)
                        nconf[min(int(r["n"] or 0), 5)] += 1
                        if not r["power_kw"]:
                            mancano["potenza"] += 1
                        if not r["gearbox"]:
                            mancano["cambio"] += 1
                        if not r["version_raw"]:
                            mancano["versione"] += 1
                # per le auto con pochi confronti: quante auto stessa marca/modello/alimentazione, anno ±1, km ±20.000
                # (senza guardare motore e cambio) ci sono in tutto il Nord tra online e sparite negli ultimi 60 giorni
                larghi = _C()
                esempi = []
                for r in pochi[:400]:
                    if not (r["year"] and r["mileage_km"] is not None):
                        larghi["senza_anno_o_km"] += 1
                        continue
                    k = conn.execute("""SELECT count(*) AS n FROM listings WHERE make=%s AND model=%s AND fuel IS NOT DISTINCT FROM %s
                        AND year BETWEEN %s AND %s AND mileage_km BETWEEN %s AND %s AND price_eur IS NOT NULL AND id <> %s
                        AND (status='attivo' OR (status='scomparso' AND disappeared_at > now() - interval '60 days'))""",
                        (r["make"], r["model"], r["fuel"], r["year"] - 1, r["year"] + 1, max(0, r["mileage_km"] - 20000),
                         r["mileage_km"] + 20000, r["id"])).fetchone()["n"]
                    larghi["0-4" if k < 5 else "5-9" if k < 10 else "10-29" if k < 30 else "30+"] += 1
                    if k >= 10 and len(esempi) < 30:
                        esempi.append([r["make"], r["model"], r["fuel"], r["year"], r["mileage_km"], r["power_kw"], r["gearbox"],
                                       (r["version_raw"] or "")[:40], int(r["n"] or 0), k])
                esito = {"auto": len(rows), "motivi": dict(motivi.most_common(15)), "pochi_confronti": len(pochi),
                         "n_validi": dict(nconf), "mancano_tra_pochi": dict(mancano), "confronti_larghi": dict(larghi)}
                L.warning("DIAGNOSI_INSUFF %s", json.dumps(esito, ensure_ascii=False))
                for i in range(0, len(esempi), 15):
                    L.warning("DIAGNOSI_INSUFF esempi %s", json.dumps(esempi[i:i + 15], ensure_ascii=False))
                fine(True, esito)
        except Exception as e:
            L.warning("DIAGNOSI_INSUFF non riuscita: %s", str(e)[:300])
    threading.Thread(target=diagnosi_insufficienti, daemon=True, name="diag_insuff").start()

    def diagnosi_as24():
        """1/11 (cliente): il giudizio di prezzo di AutoScout24 ("super prezzo", "ottimo prezzo"…) contro il nostro calcolo.
        Una volta per etichetta (DIAGNOSI_AS24=<tag>): conteggi per giudizio, e per i migliori giudizi la nostra categoria,
        il primo motivo di scarto e se sono sul sito. Solo conteggi + 40 esempi anonimi nel log."""
        tag = os.environ.get("DIAGNOSI_AS24")
        if not tag:
            return
        time.sleep(200)
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _due(f"diag_as24:{tag}", 100000):
                return
            from ..db import log_job
            from collections import Counter as _C
            prov = [p.strip().upper() for p in (os.environ.get("ZONA_SITO") or "").split(",") if p.strip()]
            sul_sito = {it.get("id") for it in (_elenco.get("items") or [])}
            with connect() as conn:
                fine = log_job(conn, f"diag_as24:{tag}")
                conn.execute("SET statement_timeout = '300s'")
                rows = conn.execute("""
                    WITH ult AS (SELECT DISTINCT ON (listing_id) listing_id, riassunto, dati->'motivi' AS motivi
                                 FROM valutazioni_policy ORDER BY listing_id, computed_at DESC)
                    SELECT l.id, l.make, l.model, l.fuel, l.year, l.mileage_km, l.price_eur, l.seller_type, l.telefono_ok,
                           (SELECT f FROM unnest(l.price_flags) f WHERE f LIKE 'as24_prezzo:%%' LIMIT 1) AS giudizio,
                           u.riassunto->>'categoria' AS cat, (u.riassunto->>'margine')::numeric AS margine,
                           (u.riassunto->>'M')::numeric AS m, u.motivi
                    FROM listings l LEFT JOIN ult u ON u.listing_id = l.id
                    WHERE l.source='autoscout24' AND l.status='attivo' AND upper(l.province) = ANY(%s)
                      AND EXISTS (SELECT 1 FROM unnest(l.price_flags) f WHERE f LIKE 'as24_prezzo:%%')""", (prov,)).fetchall()
                per_giudizio = _C(r["giudizio"] for r in rows)
                per_giudizio_priv = _C(r["giudizio"] for r in rows if r["seller_type"] == "privato")
                migliori = sorted(per_giudizio, key=lambda g: -per_giudizio[g])
                det = {}
                esempi = []
                for g in migliori:
                    sub = [r for r in rows if r["giudizio"] == g and r["seller_type"] == "privato"]
                    c = _C()
                    for r in sub:
                        c["sul_sito" if r["id"] in sul_sito else f"cat:{r['cat'] or 'non_valutata'}"] += 1
                        if r["cat"] == "dati_insufficienti" and r["motivi"]:
                            m0 = r["motivi"][0] if isinstance(r["motivi"], list) and r["motivi"] else str(r["motivi"])
                            c["insuff:" + re.sub(r"\d[\d.]*", "N", str(m0))[:50]] += 1
                        if r["m"] and r["price_eur"]:
                            c["sotto_M_20+"] += int(r["price_eur"] < float(r["m"]) * .8)
                    det[g] = dict(c.most_common(14))
                    for r in sub[:400]:
                        if len(esempi) < 40 and r["id"] not in sul_sito and r["cat"] in ("non_conveniente", "dati_insufficienti") \
                                and ("top" in str(g) or "super" in str(g) or "very" in str(g)):
                            esempi.append([g, r["make"], r["model"], r["fuel"], r["year"], r["mileage_km"], r["price_eur"],
                                           int(r["m"] or 0), int(r["margine"] or 0), r["cat"], r["telefono_ok"]])
                esito = {"annunci_con_giudizio": len(rows), "per_giudizio": dict(per_giudizio),
                         "per_giudizio_privati": dict(per_giudizio_priv), "dettaglio_privati": det}
                L.warning("DIAGNOSI_AS24 %s", json.dumps(esito, ensure_ascii=False, default=str))
                for i in range(0, len(esempi), 20):
                    L.warning("DIAGNOSI_AS24 esempi %s", json.dumps(esempi[i:i + 20], ensure_ascii=False, default=str))
                fine(True, {"annunci": len(rows)})
        except Exception as e:
            L.warning("DIAGNOSI_AS24 non riuscita: %s", str(e)[:300])
    threading.Thread(target=diagnosi_as24, daemon=True, name="diag_as24").start()

    def esporta_email():
        """1/11 sera (cliente: "mettili in un file a parte, poi tolgo io quelli già su Brevo"): tutte le email aziendali
        dei commercianti che abbiamo, nel database (lead_consegne tipo ESPORTA_EMAIL, da /api/admin/lead.csv; 4/11: non più nei log). Solo dati aziendali pubblici
        (nome dell'attività, comune, provincia, email, sito), con la data in cui è stata trovata e se è già in Brevo.
        Si ripete a ogni avvio con ESPORTA_EMAIL=<etichetta> nuova."""
        tag = os.environ.get("ESPORTA_EMAIL")
        if not tag:
            return
        time.sleep(int(os.environ.get("ESPORTA_EMAIL_RITARDO_S", "120")))
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _due(f"esporta_email:{tag}", 100000):
                return
            from ..db import log_job
            with connect() as conn:
                fine = log_job(conn, f"esporta_email:{tag}")
                try:
                    in_brevo = {r["email"] for r in conn.execute("SELECT email FROM brevo_inviati").fetchall()}
                except Exception:
                    conn.rollback()
                    in_brevo = set()
                rows = conn.execute(
                    "SELECT e.email, e.sito, e.tentato, d.nome, d.comune, d.prov, CASE WHEN d.chiave LIKE 'subito%%' THEN 'subito' ELSE 'autoscout' END AS fonte FROM lead_email e "
                    "JOIN lead_tutti_dealer d ON d.chiave = e.chiave WHERE cardinality(e.email) > 0 "
                    "UNION ALL "
                    "SELECT e.email, e.sito, e.tentato, m.nome, m.comune, m.prov, 'google_maps' AS fonte FROM lead_email e "
                    "JOIN lead_maps m ON 'maps:' || m.place_id = e.chiave WHERE cardinality(e.email) > 0").fetchall()
                righe, visti = [], set()
                for r in rows:
                    for em in r["email"] or []:
                        em = em.strip().lower()
                        if em in visti:
                            continue
                        visti.add(em)
                        righe.append([em, r["nome"], r["comune"], r["prov"], r["sito"], r["fonte"],
                                      r["tentato"].strftime("%Y-%m-%d") if r["tentato"] else "", "si" if em in in_brevo else "no"])
                from ..marketing.consegne import consegna      # 4/11: nel database (pannello admin), non nei log
                for x in righe:
                    consegna("ESPORTA_EMAIL", dict(zip(("email", "nome", "comune", "prov", "sito", "fonte", "tentato",
                                                        "in_brevo"), x)))
                L.warning("ESPORTA_EMAIL_FINE %d email (%d non in Brevo)", len(righe), sum(1 for x in righe if x[-1] == "no"))
                fine(True, {"email": len(righe)})
        except Exception as e:
            L.warning("ESPORTA_EMAIL non riuscito: %s", str(e)[:300])
    threading.Thread(target=esporta_email, daemon=True, name="esporta_email").start()

    def esporta_senza_email():
        """4/11 pom. (cliente: "voglio gente che c'è già su AutoScout, prendere le loro email"): i saloni di AutoScout24
        della zona (MI, MB, VA, CO, LC, BG, LO, PV) di cui non abbiamo l'email, con la loro pagina AutoScout24,
        per cercare a mano/con l'agente il loro sito. Una volta per etichetta (SENZA_EMAIL, di serie "4-11").
        Righe in lead_consegne tipo SENZA_EMAIL (da /api/admin/lead.csv?tipo=SENZA_EMAIL), nei log solo il conteggio."""
        tag = os.environ.get("SENZA_EMAIL", "4-11")
        if not tag or tag == "0":
            return
        time.sleep(int(os.environ.get("SENZA_EMAIL_RITARDO_S", "90")))
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _due(f"senza_email:{tag}", 100000):
                return
            from ..db import log_job
            from ..marketing.consegne import consegna
            from ..pipeline.lead_email import _fascia
            from ..pipeline.lead_comuni import prov_zona
            with connect() as conn:
                fine = log_job(conn, f"senza_email:{tag}")
                rows = conn.execute(
                    "SELECT d.chiave, d.nome, d.comune, d.prov FROM lead_tutti_dealer d "
                    "LEFT JOIN lead_email e ON e.chiave = d.chiave "
                    "WHERE d.chiave NOT LIKE 'subito%%' AND upper(d.prov) = ANY(%s) AND coalesce(d.nome, '') <> '' "
                    "AND (e.chiave IS NULL OR cardinality(e.email) = 0) ORDER BY d.prov, d.comune, d.nome",
                    (sorted(prov_zona()),)).fetchall()
                ann: dict = {}
                for r in conn.execute("SELECT chiave, prezzo FROM lead_tutti_ann WHERE chiave = ANY(%s)",
                                      ([r["chiave"] for r in rows],)).fetchall():
                    ann.setdefault(r["chiave"], []).append(r["prezzo"])
                for r in rows:
                    fascia, med = _fascia(ann.get(r["chiave"], []))
                    consegna("SENZA_EMAIL", {"nome": r["nome"], "comune": r["comune"], "prov": r["prov"],
                                             "pagina_autoscout": r["chiave"], "auto": len(ann.get(r["chiave"], [])),
                                             "fascia": fascia, "prezzo_mediano": med})
                L.warning("SENZA_EMAIL_FINE %d saloni AutoScout24 della zona senza email", len(rows))
                fine(True, {"saloni": len(rows)})
        except Exception as e:
            L.warning("SENZA_EMAIL non riuscito: %s", str(e)[:300])
    threading.Thread(target=esporta_senza_email, daemon=True, name="senza_email").start()

    def senza_email_nei_log():
        """4/11 sera (cliente: "fallo tu"): una volta (SENZA_EMAIL_LOG, di serie "4-11"), le righe SENZA_EMAIL già salvate
        scritte anche nei log in forma corta, SOLO dati pubblici dell'attività (nome, comune, provincia, pagina
        AutoScout24, numero di auto; mai telefoni né email), a blocchi di 40, per farle leggere all'agente che cerca i siti."""
        tag = os.environ.get("SENZA_EMAIL_LOG", "4-11")
        if not tag or tag == "0":
            return
        time.sleep(int(os.environ.get("SENZA_EMAIL_LOG_RITARDO_S", "70")))
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _due(f"senza_email_log:{tag}", 100000):
                return
            from ..db import log_job
            from ..marketing import consegne
            with connect() as conn:
                fine = log_job(conn, f"senza_email_log:{tag}")
                righe = consegne.righe(conn, "SENZA_EMAIL")
            visti, corte = set(), []
            for r in righe:
                pag = str(r.get("pagina_autoscout") or "")
                if pag in visti:
                    continue
                visti.add(pag)
                corte.append([r.get("nome"), r.get("comune"), r.get("prov"),
                              pag.rstrip("/").split("/concessionari/")[-1], r.get("auto")])
            corte.sort(key=lambda x: -(x[4] or 0))
            for i in range(0, len(corte), 40):
                L.warning("SENZA_EMAIL_RIGHE %d %s", i // 40, json.dumps(corte[i:i + 40], ensure_ascii=False))
            L.warning("SENZA_EMAIL_RIGHE_FINE %d saloni", len(corte))
            fine(True, {"saloni": len(corte)})
        except Exception as e:
            L.warning("SENZA_EMAIL_LOG non riuscito: %s", str(e)[:300])
    threading.Thread(target=senza_email_nei_log, daemon=True, name="senza_email_log").start()

    def zona_mai_usate():
        """4/11 sera (cliente: "ci servono più email"): una volta (ZONA_MAI_USATE, di serie "4-11"), quante email dei saloni
        AutoScout24 delle province della zona abbiamo già ma non sono MAI state caricate in Brevo (brevo_inviati) e
        non sono disiscritte. Nei log solo i conteggi per provincia; le righe in lead_consegne (tipo ZONA_MAI_USATE)."""
        tag = os.environ.get("ZONA_MAI_USATE", "4-11")
        if not tag or tag == "0":
            return
        time.sleep(int(os.environ.get("ZONA_MAI_USATE_RITARDO_S", "80")))
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _due(f"zona_mai_usate:{tag}", 100000):
                return
            from collections import Counter
            from ..db import log_job
            from ..marketing import invii as INV
            from ..marketing.consegne import consegna
            from ..pipeline.lead_comuni import prov_zona
            with connect() as conn:
                fine = log_job(conn, f"zona_mai_usate:{tag}")
                try:
                    in_brevo = {INV.norm_email(r["email"]) for r in conn.execute("SELECT email FROM brevo_inviati").fetchall()}
                except Exception:
                    conn.rollback()
                    in_brevo = set()
                try:
                    via = INV.disiscritti(conn)
                except Exception:
                    conn.rollback()
                    via = set()
                rows = conn.execute(
                    "SELECT e.email, e.sito, d.nome, d.comune, d.prov, d.chiave FROM lead_email e "
                    "JOIN lead_tutti_dealer d ON d.chiave = e.chiave WHERE cardinality(e.email) > 0 "
                    "AND d.chiave NOT LIKE 'subito%%' AND upper(d.prov) = ANY(%s)", (sorted(prov_zona()),)).fetchall()
                tot, mai, visti = Counter(), Counter(), set()
                for r in rows:
                    for em in r["email"] or []:
                        em = INV.norm_email(em)
                        if not em or em in visti:
                            continue
                        visti.add(em)
                        tot[r["prov"]] += 1
                        if em in in_brevo or INV.impronta(em) in via:
                            continue
                        mai[r["prov"]] += 1
                        consegna("ZONA_MAI_USATE", {"email": em, "nome": r["nome"], "comune": r["comune"], "prov": r["prov"],
                                                    "sito": r["sito"], "pagina_autoscout": r["chiave"]})
                L.warning("ZONA_MAI_USATE_FINE email in zona %d, mai caricate in Brevo e non disiscritte %d; per provincia "
                          "tutte %s mai %s", sum(tot.values()), sum(mai.values()), dict(tot), dict(mai))
                fine(True, {"zona": sum(tot.values()), "mai_usate": sum(mai.values())})
        except Exception as e:
            L.warning("ZONA_MAI_USATE non riuscito: %s", str(e)[:300])
    threading.Thread(target=zona_mai_usate, daemon=True, name="zona_mai_usate").start()

    def tutte_mai_usate():
        """5/11 (cliente: "a me servono le email nuove"): come ZONA_MAI_USATE ma su TUTTE le email raccolte (tutte le
        fonti e tutte le province, non solo gli 8 capoluoghi): quante non sono MAI state caricate in Brevo e non sono
        disiscritte, niente PEC né Loconte. Nei log solo conteggi; righe in lead_consegne (tipo TUTTE_MAI_USATE)."""
        tag = "5-11"
        time.sleep(100)
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due
            if not _due(f"tutte_mai_usate:{tag}", 100000):
                return
            from collections import Counter
            from ..db import log_job
            from ..marketing import invii as INV
            from ..marketing.consegne import consegna
            from ..pipeline.lead_comuni import prov_zona
            zona = prov_zona()
            with connect() as conn:
                fine = log_job(conn, f"tutte_mai_usate:{tag}")
                try:
                    in_brevo = {INV.norm_email(r["email"]) for r in conn.execute("SELECT email FROM brevo_inviati").fetchall()}
                except Exception:
                    conn.rollback()
                    in_brevo = set()
                try:
                    via = INV.disiscritti(conn)
                except Exception:
                    conn.rollback()
                    via = set()
                rows = conn.execute(
                    "SELECT e.email, e.sito, e.chiave, d.nome, d.comune, d.prov FROM lead_email e "
                    "LEFT JOIN lead_tutti_dealer d ON d.chiave = e.chiave WHERE cardinality(e.email) > 0").fetchall()
                tot, mai, visti = Counter(), Counter(), set()
                for r in rows:
                    pv = (r.get("prov") or "?").upper()[:2]
                    for em in r["email"] or []:
                        em = INV.norm_email(em)
                        if not em or em in visti or "loconte" in em or "pec" in em.split("@")[-1] or "legalmail" in em:
                            continue
                        visti.add(em)
                        tot[pv] += 1
                        if em in in_brevo or INV.impronta(em) in via:
                            continue
                        mai[pv] += 1
                        consegna("TUTTE_MAI_USATE", {"email": em, "nome": r.get("nome"), "comune": r.get("comune"),
                                                     "prov": pv, "zona": pv in zona, "sito": r.get("sito"),
                                                     "chiave": r.get("chiave")})
                L.warning("TUTTE_MAI_USATE_FINE email %d, mai caricate in Brevo e non disiscritte %d (in zona %d); "
                          "per provincia mai %s", sum(tot.values()), sum(mai.values()),
                          sum(v for k, v in mai.items() if k in zona), dict(mai.most_common(30)))
                fine(True, {"tutte": sum(tot.values()), "mai_usate": sum(mai.values())})
        except Exception as e:
            L.warning("TUTTE_MAI_USATE non riuscito: %s", str(e)[:300])
    threading.Thread(target=tutte_mai_usate, daemon=True, name="tutte_mai_usate").start()

    def nuovi_zona_invio():
        """4/11 sera (cliente: "carica e manda i nuovi"): una volta (NUOVI_ZONA_INVIO, di serie "5-11") carica in Brevo
        le email dei saloni della zona mai contattati (lista caricata dall'admin in dati_riservati lead_zona_nuove_0511
        + righe ZONA_MAI_USATE) e crea la campagna copia della 68, programmata (NUOVI_ZONA_QUANDO). Parte solo quando
        la lista c'è; nei log solo conteggi."""
        tag = os.environ.get("NUOVI_ZONA_INVIO", "5-11")
        if not tag or tag == "0":
            return
        L = logging.getLogger("scovo")
        from ..marketing import brevo_liste as BL
        from ..marketing import consegne as CO
        from ..marketing.dati_riservati import leggi
        for _ in range(240):                                   # aspetta fino a 4 ore che l'admin carichi la lista
            time.sleep(60 if _ else 5)
            try:
                with connect() as conn:
                    nuove = leggi(conn, "lead_zona_nuove_0511")
                if nuove:
                    L.warning("NUOVI_ZONA lista trovata: %d email (si caricano da %s)", len(nuove),
                              os.environ.get("NUOVI_ZONA_CARICA_DA", "2026-12-31T08:30:00+01:00"))
                    break
            except Exception:
                pass
        else:
            L.warning("NUOVI_ZONA lista non caricata: niente")
            return
        from datetime import datetime as _dt
        da = _dt.fromisoformat(os.environ.get("NUOVI_ZONA_CARICA_DA", "2026-12-31T08:30:00+01:00"))
        # 5/11: la lista di ieri non è arrivata in tempo: niente caricamento né invio finché il cliente non dice sì
        # (si cambia questa data). Nel log solo quante email ha la lista.
        while _dt.now(da.tzinfo) < da:                         # 4/11: il cliente libera posto in Brevo stanotte: si
            time.sleep(60)                                     # carica la mattina, con il posto che c'è a quell'ora
        try:
            from ..pipeline.ciclo import _una_volta
            if not _una_volta(f"nuovi_zona_invio:{tag}"):
                return
            with connect() as conn:
                db = [r.get("email") for r in CO.righe(conn, "ZONA_MAI_USATE")]
            emails = [str(e) for e in list(nuove) + db if e]
            esito = BL.carica_e_programma(emails, int(os.environ.get("NUOVI_ZONA_MODELLO", "68")),
                                          os.environ.get("NUOVI_ZONA_QUANDO", "2026-11-05T09:10:00+01:00"))
            L.warning("NUOVI_ZONA_FINE %s", json.dumps(esito))
        except Exception as e:
            L.warning("NUOVI_ZONA non riuscito: %s", str(e)[:300])
    threading.Thread(target=nuovi_zona_invio, daemon=True, name="nuovi_zona_invio").start()

    def esporta_sportive():
        """4/11 sera (cliente: "prova con gente che tratta solo macchine sportive, che compra qua in Italia, non troppo
        grossa"): saloni AutoScout24 delle province della zona con la maggior parte delle auto in vendita di marche
        sportive e un parco piccolo-medio (SPORT_AUTO_MIN-SPORT_AUTO_MAX auto), con telefono ed email aziendali se li
        abbiamo. Una volta per etichetta (SPORTIVE, di serie "4-11"). Righe in lead_consegne tipo SPORTIVE
        (/api/admin/lead.csv?tipo=SPORTIVE). Niente invii."""
        tag = os.environ.get("SPORTIVE", "4-11")
        if not tag or tag == "0":
            return
        time.sleep(int(os.environ.get("SPORTIVE_RITARDO_S", "100")))
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _due(f"sportive:{tag}", 100000):
                return
            from ..db import log_job
            from ..marketing.consegne import consegna
            from ..pipeline.lead_comuni import prov_zona
            marche = ("porsche", "ferrari", "lamborghini", "maserati", "aston", "mclaren", "bentley", "rolls",
                      "lotus", "alpine", "jaguar", "abarth", "alfa romeo")
            amin, amax = int(os.environ.get("SPORT_AUTO_MIN", "4")), int(os.environ.get("SPORT_AUTO_MAX", "40"))
            quota = float(os.environ.get("SPORT_QUOTA", "0.5"))
            with connect() as conn:
                fine = log_job(conn, f"sportive:{tag}")
                rows = conn.execute(
                    "SELECT d.chiave, d.nome, d.comune, d.prov, d.cell, e.email, e.sito, "
                    "count(a.*) AS auto, "
                    "count(*) FILTER (WHERE lower(coalesce(a.marca, '')) LIKE ANY(%s)) AS sportive, "
                    "percentile_cont(0.5) WITHIN GROUP (ORDER BY a.prezzo) AS mediana, "
                    "string_agg(DISTINCT lower(a.marca), ', ') AS marche "
                    "FROM lead_tutti_dealer d JOIN lead_tutti_ann a ON a.chiave = d.chiave "
                    "LEFT JOIN lead_email e ON e.chiave = d.chiave "
                    "WHERE d.chiave NOT LIKE 'subito%%' AND upper(d.prov) = ANY(%s) "
                    "GROUP BY d.chiave, d.nome, d.comune, d.prov, d.cell, e.email, e.sito",
                    ([m + "%" for m in marche], sorted(prov_zona()))).fetchall()
                tenuti = 0
                for r in rows:
                    n, sp = int(r["auto"] or 0), int(r["sportive"] or 0)
                    if not (amin <= n <= amax) or sp < quota * n:
                        continue
                    tenuti += 1
                    consegna("SPORTIVE", {"nome": r["nome"], "comune": r["comune"], "prov": r["prov"],
                                          "auto": n, "sportive": sp, "quota_sportive": round(sp / n, 2),
                                          "prezzo_mediano": int(r["mediana"]) if r["mediana"] else None,
                                          "marche": r["marche"], "telefoni": list(r["cell"] or []),
                                          "email": list(r["email"] or []), "sito": r["sito"],
                                          "pagina_autoscout": r["chiave"]})
                L.warning("SPORTIVE_FINE %d saloni di sportive piccoli-medi su %d", tenuti, len(rows))
                fine(True, {"saloni": tenuti})
        except Exception as e:
            L.warning("SPORTIVE non riuscito: %s", str(e)[:300])
    threading.Thread(target=esporta_sportive, daemon=True, name="sportive").start()

    def lead_zona_100km():
        """4/11 pom. (cliente: "raccogliere altro qua a Milano e recuperare le loro email, Milano Bergamo Varese ecc.,
        persone nuove, non doppioni"): una volta (LEAD_ZONA, di serie "4-11"): i saloni NUOVI dell'elenco AutoScout24
        comune per comune in tutti i comuni di MI, MB, VA, CO, LC, BG, LO, PV (lead_comuni.nuovi_zona),
        poi le loro email (pagina AutoScout24 e sito), poi i saloni della zona ancora senza email in lead_consegne
        (tipo SENZA_EMAIL, etichetta "<tag>-dopo"). Niente invii. Nei log solo conteggi."""
        tag = os.environ.get("LEAD_ZONA", "0")   # 4/11 sera: spento, 40 comuni letti = 2 saloni nuovi soli (zona già coperta)
        if not tag or tag == "0":
            return
        time.sleep(int(os.environ.get("LEAD_ZONA_RITARDO_S", "180")))
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            from ..db import log_job
            from ..pipeline import lead_comuni as LC, lead_email as LE
            if _due(f"lead_zona:{tag}", 100000):
                esito = LC.nuovi_zona()       # elenco saloni di AutoScout24 comune per comune: solo i nuovi
                with connect() as conn:
                    log_job(conn, f"lead_zona:{tag}")(True, esito)
                L.warning("LEAD_ZONA_RACCOLTA_FINE %s", json.dumps(esito, ensure_ascii=False, default=str)[:600])
            if _due(f"lead_zona_email:{tag}", 100000):
                st = LE.giro()
                with connect() as conn:
                    log_job(conn, f"lead_zona_email:{tag}")(True, st)
            os.environ["SENZA_EMAIL"] = f"{tag}-dopo"
            os.environ["SENZA_EMAIL_RITARDO_S"] = "1"
            esporta_senza_email()
        except Exception as e:
            L.warning("LEAD_ZONA non riuscito: %s", str(e)[:300])
    threading.Thread(target=lead_zona_100km, daemon=True, name="lead_zona").start()

    def esporta_tel():
        """2/11 (cliente: "dammi la mega lista dei numeri + i nuovi + quelli di Google Maps di ieri"): numeri AZIENDALI
        dei commercianti (Google Maps lead_maps, AutoScout24/Subito lead_tutti_dealer, commercianti_as24), una volta
        per etichetta (ESPORTA_TEL=<tag>). Righe in lead_consegne (tipo ESPORTA_TEL, da /api/admin/lead.csv; 4/11: non più nei log)."""
        tag = os.environ.get("ESPORTA_TEL")
        if not tag:
            return
        time.sleep(int(os.environ.get("ESPORTA_TEL_RITARDO_S", "90")))
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _una_volta(f"esporta_tel:{tag}"):
                return
            righe = []
            with connect() as conn:
                for r in conn.execute("SELECT nome, comune, prov, tel, cell, sito, categoria, aggiornato FROM lead_maps "
                                      "WHERE coalesce(tel,'') <> ''").fetchall():
                    righe.append([r["tel"], bool(r["cell"]), r["nome"], r["comune"], r["prov"], r["sito"], r["categoria"],
                                  "google_maps", r["aggiornato"].strftime("%Y-%m-%d") if r["aggiornato"] else ""])
                for r in conn.execute("SELECT nome, comune, prov, cell, fonte, chiave FROM lead_tutti_dealer "
                                      "WHERE cardinality(cell) > 0").fetchall():
                    for c in r["cell"]:
                        righe.append([c, True, r["nome"], r["comune"], r["prov"],
                                      r["chiave"] if str(r["chiave"]).startswith("http") else None, None, r["fonte"] or "annunci", ""])
                try:
                    for r in conn.execute("SELECT c.nome, c.cellulari, c.aggiornato, min(a.comune) AS comune, min(a.prov) AS prov "
                                          "FROM commercianti_as24 c LEFT JOIN commercianti_as24_annunci a USING (seller_id) "
                                          "WHERE cardinality(c.cellulari) > 0 GROUP BY c.seller_id, c.nome, c.cellulari, c.aggiornato").fetchall():
                        for c in r["cellulari"]:
                            righe.append([c, True, r["nome"], r["comune"], r["prov"], None, None, "autoscout24",
                                          r["aggiornato"].strftime("%Y-%m-%d") if r["aggiornato"] else ""])
                except Exception:
                    conn.rollback()
            from ..marketing.consegne import consegna          # 4/11: nel database (pannello admin), non nei log
            for x in righe:
                consegna("ESPORTA_TEL", {"riga": x})
            L.warning("ESPORTA_TEL_FINE %d righe", len(righe))
        except Exception as e:
            L.warning("ESPORTA_TEL non riuscito: %s", str(e)[:300])
    threading.Thread(target=esporta_tel, daemon=True, name="esporta_tel").start()

    def rimetti_brevo():
        """2/11: RINVIO_INVIA=rimetti<n> ricarica in Brevo i contatti vecchi fino a 1.498 (nessun invio). Una volta per etichetta."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("rimetti"):
            return
        time.sleep(30)
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _una_volta(f"rimetti:{tag}"):
                return
            from ..marketing import auto_invio as AI
            AI.rimetti_contatti(tag)
        except Exception as e:
            logging.getLogger("scovo").warning("RIMETTI non riuscito: %s", str(e)[:300])
    threading.Thread(target=rimetti_brevo, daemon=True, name="rimetti-brevo").start()

    def esempi_rimessi():
        """2/11: RINVIO_INVIA=esempi<n> manda l'email con 3 esempi ai contatti vecchi rimessi. Una volta per etichetta."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("esempi"):
            return
        time.sleep(120)
        for _ in range(30):
            if _elenco.get("items"):
                break
            time.sleep(60)
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _una_volta(f"esempi_rimessi:{tag}"):
                return

            def piatte():
                out = []
                for it in list(_elenco.get("items") or []):
                    pv = it.get("pv") or {}
                    out.append({"id": it.get("id"), "auto": it.get("nome"), "anno": it.get("anno"), "km": it.get("km"),
                                "carb": it.get("carb"), "prezzo": it.get("prezzo"), "n": pv.get("n"), "zona": it.get("zona"),
                                "R": pv.get("R_finale") or pv.get("R"), "margine": pv.get("margine")})
                return out
            from ..marketing import auto_invio as AI
            AI.invia_rimessi(tag, piatte)
        except Exception as e:
            logging.getLogger("scovo").warning("RIMESSI_ESEMPI non riuscito: %s", str(e)[:300])
    threading.Thread(target=esempi_rimessi, daemon=True, name="esempi-rimessi").start()

    def fasce_brevo():
        """2/11: RINVIO_INVIA=fasce<n>: contatti Brevo divisi per fascia + 3 esempi per fascia in bozza (nessun invio)."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("fasce"):
            return
        time.sleep(120)
        for _ in range(30):
            if _elenco.get("items"):
                break
            time.sleep(60)
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _una_volta(f"fasce_brevo:{tag}"):
                return

            def piatte():
                out = []
                for it in list(_elenco.get("items") or []):
                    pv = it.get("pv") or {}
                    out.append({"id": it.get("id"), "auto": it.get("nome"), "anno": it.get("anno"), "km": it.get("km"),
                                "carb": it.get("carb"), "prezzo": it.get("prezzo"), "n": pv.get("n"), "zona": it.get("zona"),
                                "R": pv.get("R_finale") or pv.get("R"), "margine": pv.get("margine")})
                return out
            from ..marketing import auto_invio as AI
            AI.fasce_e_esempi(tag, piatte)
        except Exception as e:
            logging.getLogger("scovo").warning("FASCE non riuscito: %s", str(e)[:300])
    threading.Thread(target=fasce_brevo, daemon=True, name="fasce-brevo").start()

    def nuovi_brevo():
        """3/11 (cliente: "manda a tutti i 1500, gente nuova mai contattata, togli chi si è lamentato"): DIAGNOSI_DATI=nuovi<n>
        (RINVIO_INVIA resta alla rivalutazione). Una volta per etichetta."""
        tag = os.environ.get("DIAGNOSI_DATI") or ""
        if not tag.startswith("nuovi"):
            return
        time.sleep(120)
        for _ in range(30):
            if _elenco.get("items"):
                break
            time.sleep(60)
        try:
            from ..pipeline.ciclo import _una_volta
            if not _una_volta(f"nuovi_brevo:{tag}"):
                return

            def piatte():
                out = []
                for it in list(_elenco.get("items") or []):
                    if it.get("commerciante"):
                        continue
                    pv = it.get("pv") or {}
                    out.append({"id": it.get("id"), "auto": it.get("nome"), "anno": it.get("anno"), "km": it.get("km"),
                                "carb": it.get("carb"), "prezzo": it.get("prezzo"), "n": pv.get("n"), "zona": it.get("zona"),
                                "R": pv.get("R_finale") or pv.get("R"), "margine": pv.get("margine"),
                                "rev": pv.get("policy_rev"), "regole": pv.get("regole")})
                return out
            from ..marketing import auto_invio as AI
            AI.nuovi_3_11(tag, piatte)
        except Exception as e:
            logging.getLogger("scovo").warning("NUOVI_3_11 non riuscito: %s", str(e)[:300])
    threading.Thread(target=nuovi_brevo, daemon=True, name="nuovi-brevo").start()

    def voce_cartesia():
        """2/11 (video Skovo): RINVIO_INVIA=voce<n> genera la voce del Reel con Cartesia (chiave CARTESIA_API_KEY su Render):
        elenca le voci italiane, genera i 7 paragrafi del discorso uno per uno (MP3) e li scrive nei log in base64
        (VOCE_B64 <paragrafo> <pezzo>/<pezzi> <dati>). Una volta per etichetta."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("voce"):
            return
        time.sleep(20)
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _una_volta(f"voce:{tag}"):
                return
            import base64
            import httpx
            key = os.environ.get("CARTESIA_API_KEY")
            H = {"Authorization": f"Bearer {key}", "Cartesia-Version": "2026-03-01", "Content-Type": "application/json"}
            # frasi del discorso, una per file: i tempi di ogni frase guidano il montaggio del Reel
            testi = [
                "Ti è mai capitato di vedere un'auto a un prezzo ottimo…", "e scoprire che era già venduta?",
                "Succede ogni giorno:", "le occasioni vere durano poche ore.",
                "Il problema è che gli annunci sono migliaia,", "e in mezzo ci sono truffe, annunci falsi e auto incidentate.",
                "Per trovarne una buona perdi ore.", "Per questo abbiamo creato Skovo.",
                "Il nostro software legge tutti gli annunci di privati nella tua zona,", "li confronta con auto uguali",
                "e ti trova quelle davvero sotto mercato.", "Scarta le truffe,", "ti avvisa appena esce l'occasione,",
                "trattiamo noi con il venditore, arriviamo al prezzo più basso e fissiamo anche l'appuntamento.", "Tu devi solo comprare e rivendere.",
                "Skovo: le occasioni, prima degli altri.", "Chiamaci al 348 242 5633, ti spieghiamo tutto in due minuti.",
            ]
            with httpx.Client(timeout=120, headers=H) as c:
                r = c.get("https://api.cartesia.ai/voices", params={"language": "it", "limit": 100})
                js = r.json() if r.status_code < 300 else {}
                voci = js.get("data", js) if isinstance(js, dict) else js
                voci = [v for v in (voci or []) if isinstance(v, dict) and str(v.get("language", "")).startswith("it")]
                L.warning("VOCE voci italiane (%s): %s", r.status_code, json.dumps([[v.get("id"), v.get("name"), v.get("gender"), (v.get("description") or "")[:80]] for v in voci][:40], ensure_ascii=False))
                scelta = os.environ.get("VOCE_ID") or ""
                if not scelta and voci:
                    pref = [v for v in voci if str(v.get("gender", "")).lower() in ("masculine", "male", "m")
                            or re.search(r"\b(man|male|uomo|maschil)", (str(v.get("name")) + " " + str(v.get("description"))).lower())]
                    scelta = (pref or voci)[0]["id"]
                if tag.startswith("voceintro"):     # 4/11: inizio del video per i commercianti, stessa voce del Reel
                    scelta = os.environ.get("VOCE_ID") or "79693aee-1207-4771-a01e-20c393c89e6f"
                    testi = ["Sei un commerciante d'auto? Skovo è solo per commercianti."]
                L.warning("VOCE scelta %s", scelta)
                # 2/11 sera: "voceintera" = tutto il discorso in una sola generazione (pause e intonazione naturali);
                # i tagli del video si ricavano poi dalle pause dell'audio
                if tag.startswith("voceintera"):
                    paragrafi = [" ".join(testi[0:4]), " ".join(testi[4:7]), testi[7], " ".join(testi[8:11]), " ".join(testi[11:14]), testi[14], " ".join(testi[15:17])]
                    testi = ["\n\n".join(paragrafi)]
                # 2/11 sera: "vocets" = discorso intero via SSE con i tempi di ogni parola (allineamento esatto del video).
                # Audio PCM 16 bit 24 kHz mono nei log (VOCE_PCM), parole nei log (VOCE_WORDS).
                if tag.startswith("vocets"):
                    body = {"model_id": "sonic-3.5", "transcript": "\n\n".join(paragrafi) if False else " ".join(testi), "voice": {"mode": "id", "id": scelta},
                            "language": "it", "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 24000},
                            "add_timestamps": True, "generation_config": {"speed": float(os.environ.get("VOCE_VEL", "1.0"))}}
                    pcm, words, starts, ends = bytearray(), [], [], []
                    with c.stream("POST", "https://api.cartesia.ai/tts/sse", json=body, timeout=180) as rr:
                        if rr.status_code >= 300:
                            L.warning("VOCE sse errore %s %s", rr.status_code, rr.read()[:300]); return
                        for line in rr.iter_lines():
                            if not line.startswith("data:"):
                                continue
                            try:
                                ev = json.loads(line[5:].strip())
                            except Exception:
                                continue
                            if ev.get("data") and ev.get("type", "chunk") == "chunk":
                                pcm += base64.b64decode(ev["data"])
                            wt = ev.get("word_timestamps")
                            if wt:
                                words += wt.get("words", []); starts += wt.get("start", []); ends += wt.get("end", [])
                            if ev.get("type") == "error":
                                L.warning("VOCE sse evento errore %s", str(ev)[:300])
                    L.warning("VOCE_WORDS %s", json.dumps({"w": words, "s": starts, "e": ends}, ensure_ascii=False))
                    b = base64.b64encode(bytes(pcm)).decode()
                    pz = [b[k:k + 3500] for k in range(0, len(b), 3500)]
                    for k, x in enumerate(pz):
                        L.warning("VOCE_PCM %d/%d %s", k + 1, len(pz), x)
                    L.warning("VOCE fatto sse: %d byte, %d parole", len(pcm), len(words))
                    return
                for i, t in enumerate(testi):
                    body = {"model_id": "sonic-3.5", "transcript": t, "voice": {"mode": "id", "id": scelta}, "language": "it",
                            "output_format": {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000},
                            "generation_config": {"speed": float(os.environ.get("VOCE_VEL", "1.0"))}}
                    rr = c.post("https://api.cartesia.ai/tts/bytes", json=body)
                    if rr.status_code >= 300:
                        body["model_id"] = "sonic-3"; body.pop("generation_config", None)
                        rr = c.post("https://api.cartesia.ai/tts/bytes", json=body)
                    if rr.status_code >= 300:
                        L.warning("VOCE paragrafo %d errore %s %s", i + 1, rr.status_code, rr.text[:300]); continue
                    b = base64.b64encode(rr.content).decode()
                    pz = [b[k:k + 3500] for k in range(0, len(b), 3500)]
                    for k, x in enumerate(pz):
                        L.warning("VOCE_B64 %d %d/%d %s", i + 1, k + 1, len(pz), x)
                    L.warning("VOCE paragrafo %d ok: %d byte", i + 1, len(rr.content))
            L.warning("VOCE fatto")
        except Exception as e:
            L.warning("VOCE non riuscita: %s", str(e)[:300])
    threading.Thread(target=voce_cartesia, daemon=True, name="voce").start()

    def musica_video():
        """2/11 (video Skovo): RINVIO_INVIA=musica<n> scarica l'inizio di registrazioni orchestrali di pubblico dominio
        (Musopen su Wikimedia Commons) e le scrive nei log in base64 (MUSICA_B64 <brano> <pezzo>/<pezzi> <dati>).
        Solo i primi MUSICA_BYTE byte (Range), che bastano per ~1-2 minuti di musica. Una volta per etichetta."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("musica"):
            return
        time.sleep(20)
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _una_volta(f"musica:{tag}"):
                return
            import base64
            import httpx
            urls = [u for u in (os.environ.get("MUSICA_URL") or "").split("|") if u] or [
                "https://upload.wikimedia.org/wikipedia/commons/1/17/Antonin_Dvorak_-_symphony_no._9_in_e_minor_%27from_the_new_world%27%2C_op._95_-_iv._allegro_con_fuoco.ogg",
                "https://commons.wikimedia.org/wiki/Special:FilePath/Antonin_Dvorak_-_symphony_no._9_in_e_minor_%27from_the_new_world%27%2C_op._95_-_iv._allegro_con_fuoco.ogg"]
            nb = int(os.environ.get("MUSICA_BYTE", "2000000"))
            with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": "SkovoVideoBot/1.0 (https://scovo.onrender.com) httpx", "Range": f"bytes=0-{nb - 1}", "Accept": "*/*"}) as c:
                for i, u in enumerate(urls):
                    r = c.get(u)
                    L.warning("MUSICA brano %d: %s %s %d byte", i + 1, r.status_code, r.headers.get("content-type"), len(r.content))
                    if r.status_code >= 300:
                        L.warning("MUSICA risposta: %s", r.text[:300]); continue
                    b = base64.b64encode(r.content[:nb]).decode()
                    pz = [b[k:k + 3500] for k in range(0, len(b), 3500)]
                    for k, x in enumerate(pz):
                        L.warning("MUSICA_B64 %d %d/%d %s", i + 1, k + 1, len(pz), x)
                    break
            L.warning("MUSICA fatto")
        except Exception as e:
            L.warning("MUSICA non riuscita: %s", str(e)[:300])
    threading.Thread(target=musica_video, daemon=True, name="musica").start()

    def sms_invia():
        """2/11 (cliente: "manda l'SMS ai commercianti di Milano e provincia, subito, più numeri possibile"): SMS da Brevo
        (mittente "Skovo") ai cellulari AZIENDALI dei commercianti di Milano e provincia (Google Maps e annunci), tolti
        officine/carrozzerie ecc., Loconte e i numeri già chiamati (data/lead_chiamati.json). Il primo è il cliente (prova).
        Si attiva con RINVIO_INVIA=sms<n> (Render è al limite di 300 variabili). Una volta per etichetta. Nei log solo conteggi."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("sms"):
            return
        time.sleep(30)
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _una_volta(f"sms_invia:{tag}"):
                return
            import hashlib
            import httpx
            from pathlib import Path as _P
            from ..marketing.brevo_liste import _chiave
            testo = ("Buongiorno, siamo Skovo: cerchiamo per voi auto di privati in vendita sotto mercato da comprare e "
                     "rivendere, e vi fissiamo l'appuntamento. Info 3482425633")
            # 2/11 sera (cliente: "manda a 200 commercianti reali ben strutturati, numeri validi", testo scelto "Corto"):
            # RINVIO_INVIA=sms<n>strutt -> testo nuovo (1 SMS) e selezione dei commercianti strutturati; con "prova" nel
            # tag NON invia nulla e scrive solo conteggi e nomi (niente numeri).
            strutt = "strutt" in tag
            prova = "prova" in tag
            if strutt:
                testo = ("Siamo Skovo: troviamo auto di privati sotto mercato nella vostra zona, trattiamo il prezzo e vi "
                         "fissiamo l'appuntamento. Scriveteci: wa.me/393482425633 STOP=no")
            massimo = int(os.environ.get("SMS_MAX", "229"))
            chiamati = set(json.loads((_P(__file__).resolve().parents[2] / "data" / "lead_chiamati.json").read_text())["tel"])   # solo impronte
            no = re.compile(r"(?i)officin|carrozz|gomm|pneumat|demoliz|rottam|soccorso|noleggio|ricambi|elettrauto|lavaggio|"
                            r"autoscuola|revision|moto\b|loconte|\baci\b")
            cand = []
            with connect() as conn:
                for r in conn.execute("SELECT nome, categoria, tel FROM lead_maps WHERE upper(prov) = 'MI' AND cell").fetchall():
                    if not no.search(f"{r['nome']} {r['categoria']}"):
                        cand.append((1, r["tel"]))
                for r in conn.execute("SELECT nome, chiave, cell, fonte FROM lead_tutti_dealer WHERE upper(prov) = 'MI' "
                                      "AND cardinality(cell) > 0").fetchall():
                    if no.search(r["nome"] or ""):
                        continue
                    pri = 0 if str(r["chiave"]).startswith("http") else (2 if r["nome"] else 3)
                    cand += [(pri, c) for c in r["cell"]]
            cand.sort(key=lambda x: x[0])
            vecchi = set()
            if strutt:
                # giro di stamattina (sms<n> senza strutt): stessi candidati, primi 229 cellulari validi -> mai più
                for _, t in cand:
                    d = re.sub(r"\D", "", str(t or ""))
                    if d.startswith("39") and len(d) > 10:
                        d = d[2:]
                    if re.fullmatch(r"3[1-9]\d{8}", d) and d not in vecchi and \
                            hashlib.sha1(d[-9:].encode()).hexdigest()[:16] not in chiamati:
                        vecchi.add(d)
                    if len(vecchi) >= 260:
                        break
                L.warning("SMS_STRUTT numeri del giro di stamattina tolti (stima larga): %d", len(vecchi))
                cand, info = _sms_strutturati(no, L)
                massimo = int(os.environ.get("SMS_MAX", "200"))
            # 2/11: chi ha già ricevuto un SMS da un giro precedente (tag skovo_sms*) non lo riceve di nuovo
            gia = set()
            try:
                with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=60) as c0:
                    off = 0
                    # 2/11: Brevo accetta al massimo 100 eventi per pagina (con 2500 rispondeva 400 e la lista restava vuota)
                    while off < 20000:
                        rr = c0.get("https://api.brevo.com/v3/transactionalSMS/statistics/events",
                                    params={"limit": 100, "offset": off, "days": 30})
                        if rr.status_code >= 300:
                            raise RuntimeError(f"eventi {rr.status_code} {rr.text[:100]}")
                        ev = (rr.json() or {}).get("events") or []
                        if off == 0:
                            L.warning("SMS_INVIA eventi Brevo letti: %d, esempio campi %s, tag %s", len(ev), sorted((ev[0] if ev else {}).keys()),
                                      sorted({str(e.get("tag")) for e in ev})[:5])
                        for e in ev:
                            if str(e.get("tag") or "").startswith("skovo_sms") or not e.get("tag"):
                                gia.add(re.sub(r"\D", "", str(e.get("phoneNumber") or ""))[-10:])
                        if len(ev) < 100:
                            break
                        off += 100
            except Exception as e:
                # senza l'elenco dei già contattati NON si invia (si rischierebbero doppioni)
                L.warning("SMS_INVIA fermato: già inviati non letti: %s", str(e)[:150])
                return
            L.warning("SMS_INVIA numeri che hanno già ricevuto un SMS: %d", len(gia))
            numeri, visti = ([] if prova else ["3482425633"]), {"3482425633"} | gia | vecchi
            for _, t in cand:
                d = re.sub(r"\D", "", str(t or ""))
                if d.startswith("39") and len(d) > 10:
                    d = d[2:]
                if not re.fullmatch(r"3[1-9]\d{8}", d) or d in visti:
                    continue
                # prefissi di cellulari italiani realmente in uso (TIM, Vodafone, WindTre, Iliad, Fastweb, PosteMobile, Coop...)
                if strutt and not re.match(r"3(2\d|3\d|4\d|5[0-35]|6[0-8]|7[0-9]|8[0-9]|9[0-37])", d):
                    continue
                if hashlib.sha1(d[-9:].encode()).hexdigest()[:16] in chiamati:
                    continue
                visti.add(d)
                numeri.append(d)
                if len(numeri) >= massimo + (0 if prova else 1):
                    break
            # 4/11: SMS promozionali solo a chi ha dato il consenso esplicito (tabella consensi) e una volta per numero
            # e per giro (tabella invii: un riavvio non rimanda). Il numero del cliente (prova) resta.
            from ..marketing import invii as _inv
            with connect() as conn:
                prova_cliente = [n for n in numeri if n == "3482425633"]
                altri = [n for n in numeri if n != "3482425633"]
                con_ok = _inv.con_consenso(conn, altri, "sms")
                L.warning("SMS_INVIA senza consenso esplicito, esclusi: %d", len(altri) - len(con_ok))
                numeri = prova_cliente + (con_ok if prova else _inv.prenota(conn, f"sms_invia:{tag}", con_ok, "sms"))
            L.warning("SMS_INVIA %d numeri (il primo è il cliente)", len(numeri))
            if strutt:
                from collections import Counter as _C
                scelti = set(numeri)
                righe = [x for x in info if x["tel"] in scelti]
                L.warning("SMS_STRUTT selezionati %d: fonti %s, km %s", len(righe), json.dumps(dict(_C(x["fonte"] for x in righe))),
                          json.dumps(dict(_C("<=15" if x["km"] <= 15 else "<=30" if x["km"] <= 30 else "<=45" for x in righe))))
                # 4/11: i nomi dei commercianti scelti non vanno più nei log (solo i conteggi qui sopra)
            if prova:
                L.warning("SMS_INVIA prova a secco: nessun SMS inviato")
                return
            ok = ko = 0
            errori = {}
            with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=30) as c:
                for i, n in enumerate(numeri):
                    r = c.post("https://api.brevo.com/v3/transactionalSMS/sms",
                               json={"sender": "Skovo", "recipient": "39" + n, "content": testo, "type": "marketing",
                                     "tag": f"skovo_{tag}"})
                    if r.status_code >= 500:
                        time.sleep(2)
                        r = c.post("https://api.brevo.com/v3/transactionalSMS/sms",
                                   json={"sender": "Skovo", "recipient": "39" + n, "content": testo, "type": "marketing",
                                         "tag": f"skovo_{tag}"})
                    if r.status_code == 402:
                        L.warning("SMS_INVIA fermato: credito finito dopo %d inviati", ok)
                        break
                    if r.status_code < 300:
                        ok += 1
                        with connect() as conn:
                            _inv.segna_inviati(conn, f"sms_invia:{tag}", [n], "sms")
                    else:
                        ko += 1
                        k = f"{r.status_code}:{r.text[:150]}"
                        errori[k] = errori.get(k, 0) + 1
                        if ko >= 5 and ok == 0:
                            L.warning("SMS_INVIA fermato: i primi %d invii sono falliti %s", ko, errori)
                            return
                    if i == 0:
                        L.warning("SMS_INVIA prova al cliente: %s", "ok" if ok else errori)
                    time.sleep(0.6)
            L.warning("SMS_INVIA fatto: %d inviati, %d non inviati %s", ok, ko, errori)
        except Exception as e:
            L.warning("SMS_INVIA non riuscito: %s", str(e)[:300])
    threading.Thread(target=sms_invia, daemon=True, name="sms_invia").start()

    def sms_statistiche():
        """2/11 (cliente: "gli SMS sono andati malissimo, vedi se c'è qualche problema"): RINVIO_INVIA=statsms<n> legge da
        Brevo gli eventi degli SMS Skovo (tag skovo_sms*) degli ultimi 5 giorni e scrive nei log SOLO conteggi: esito per
        evento, quanti numeri hanno ricevuto 2+ SMS, risposte, motivi di errore. NON invia nulla. Una volta per etichetta."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("statsms"):
            return
        time.sleep(20)
        L = logging.getLogger("scovo")
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            if not _una_volta(f"statsms:{tag}"):
                return
            import httpx
            from collections import Counter
            from ..marketing.brevo_liste import _chiave
            ev_all = []
            with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=60) as c:
                off = 0
                while off < 20000:
                    rr = c.get("https://api.brevo.com/v3/transactionalSMS/statistics/events", params={"limit": 2500, "offset": off, "days": 5})
                    if rr.status_code >= 300:
                        L.warning("STATSMS eventi errore %s %s", rr.status_code, rr.text[:200]); break
                    ev = (rr.json() or {}).get("events") or []
                    ev_all += ev
                    if len(ev) < 2500:
                        break
                    off += 2500
                agg = c.get("https://api.brevo.com/v3/transactionalSMS/statistics/aggregatedReport", params={"days": 5})
                L.warning("STATSMS riepilogo Brevo 5 giorni: %s", agg.text[:600])
                acc = c.get("https://api.brevo.com/v3/account")
                try:
                    L.warning("STATSMS crediti: %s", json.dumps([p for p in (acc.json().get("plan") or []) if "sms" in str(p).lower()])[:400])
                except Exception:
                    pass
            sk = [e for e in ev_all if str(e.get("tag") or "").startswith("skovo_sms")]
            L.warning("STATSMS eventi totali %d, Skovo %d", len(ev_all), len(sk))
            L.warning("STATSMS per tag ed evento: %s", json.dumps(Counter(f"{e.get('tag')}|{e.get('event')}" for e in sk), ensure_ascii=False))
            L.warning("STATSMS motivi: %s", json.dumps(Counter(f"{e.get('event')}|{e.get('reason')}" for e in sk if e.get("reason")), ensure_ascii=False)[:1500])
            inv = Counter(re.sub(r"\D", "", str(e.get("phoneNumber") or ""))[-10:] for e in sk if e.get("event") in ("sent", "accepted"))
            L.warning("STATSMS numeri diversi %d, numeri con 2+ SMS %d", len(inv), sum(1 for v in inv.values() if v > 1))
            cons = {re.sub(r"\D", "", str(e.get("phoneNumber") or ""))[-10:] for e in sk if e.get("event") == "delivered"}
            L.warning("STATSMS numeri con SMS consegnato %d", len(cons))
            L.warning("STATSMS risposte %d", sum(1 for e in sk if e.get("event") in ("replied", "reply")))
            L.warning("STATSMS esempio evento (senza numero): %s", json.dumps({k: v for k, v in (sk[0] if sk else {}).items() if k not in ("phoneNumber",)}, ensure_ascii=False)[:400])
        except Exception as e:
            L.warning("STATSMS non riuscito: %s", str(e)[:300])
    threading.Thread(target=sms_statistiche, daemon=True, name="statsms").start()

    def diagnosi_dati():
        """29/10 (cliente: "far lavorare meglio gli scraper"): perché le auto della zona finiscono in dati_insufficienti
        (DIAGNOSI_DATI=<etichetta>, una volta). Solo conteggi nel log."""
        tag = os.environ.get("DIAGNOSI_DATI")
        if not tag:
            return
        if tag.startswith("simvr"):
            # 3/11 (cliente): quante auto entrerebbero con la rivendita senza la prudenza doppia (solo conteggi)
            time.sleep(40)
            L = logging.getLogger("scovo")
            try:
                from ..pipeline.ciclo import _una_volta
                if not _una_volta(f"simvr:{tag}"):
                    return
                # 4/11: simula_vr tolta (simulava varianti della vendita veloce e della P25, regole non più in uso)
                L.warning("SIMVR tolta il 4/11: le regole prudenti che simulava non esistono più")
            except Exception as e:
                L.warning("SIMVR non riuscita: %s", str(e)[:300])
            return
        if tag.startswith("valreale"):
            # 2/11 notte (cliente: ok a sviluppare e usare il metodo "valore reale"): prova sulle vendute, calcolo per le
            # auto della zona, conteggio di quante entrerebbero con le stesse regole; in parallelo il lettore AI completa
            # i dati mancanti (kW, versione, cambio) delle auto di privati della zona, entro il budget AI del giorno.
            time.sleep(40)
            L = logging.getLogger("scovo")
            from ..pipeline.ciclo import _una_volta
            if not _una_volta(f"valreale:{tag}"):
                return

            def _lettore_zona():
                try:
                    from ..ai import lettore
                    from ..zone import tutte
                    with connect() as c2:
                        ids = [r["id"] for r in c2.execute("""SELECT l.id FROM listings l
                            LEFT JOIN lettura_ai a ON a.listing_id = l.id
                            WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%s)
                              AND l.price_eur BETWEEN 1000 AND 30000 AND a.listing_id IS NULL
                              AND (l.power_kw IS NULL OR l.version_raw IS NULL OR l.gearbox IS NULL OR l.year IS NULL)
                            ORDER BY (l.fuel ILIKE '%%benzin%%') DESC, l.last_seen_at DESC LIMIT %s""",
                                                            (list(tutte()), int(os.environ.get("VR_AI_MAX", "2000")))).fetchall()]
                        L.warning("VALORE_REALE lettore AI: %d auto della zona con dati mancanti", len(ids))
                        for i in range(0, len(ids), 100):
                            res = lettore.giro(c2, ids=ids[i:i + 100], limite=100, rivaluta=True)
                            if res.get("saltato") or res.get("spento"):
                                L.warning("VALORE_REALE lettore AI fermo: %s", res)
                                break
                except Exception as e:
                    L.warning("VALORE_REALE lettore AI non riuscito: %s", str(e)[:300])
            threading.Thread(target=_lettore_zona, daemon=True, name="vr-lettore").start()
            try:
                from ..pricing import valore_reale as VR
                from ..zone import tutte
                with connect() as conn:
                    conn.execute("SET statement_timeout = '900s'")
                    pr = VR.prova(conn, limite_gruppi=int(os.environ.get("VR_GRUPPI_PROVA", "400")))
                    L.warning("VALORE_REALE PROVA %s", json.dumps(pr, ensure_ascii=False, default=str))
                    st = VR.calcola_e_salva(conn, limite_gruppi=int(os.environ.get("VR_GRUPPI", "600")), prov_zona=list(tutte()))
                    L.warning("VALORE_REALE CALCOLO %s", json.dumps(st, default=str))
                    rows = conn.execute("""SELECT l.id, l.price_eur, l.fuel, l.mileage_km, v.v, v.p25, v.sigma FROM valore_reale v
                        JOIN listings l ON l.id = v.listing_id WHERE l.status='attivo' AND l.seller_type='privato'
                          AND COALESCE(l.mileage_km,0) <= 150000""").fetchall()
                from collections import Counter as _Cn
                c = _Cn()
                for r in rows:
                    R = min(r["v"] * 0.95, r["p25"])
                    m = R - r["price_eur"]
                    ok = m >= _sc.soglia(int(r["price_eur"]))
                    c["stimate"] += 1
                    if ok:
                        c["sopra_soglia"] += 1
                        c["sopra_soglia_sigma<=0.12"] += r["sigma"] <= 0.12
                        if "benzin" in str(r["fuel"] or "") and (r["mileage_km"] or 0) <= 100000:
                            c["sopra_soglia_benzina_100k"] += 1
                L.warning("VALORE_REALE OMBRA (stesse regole: R=min(V*0,95,P25), soglie, privati, <=150k km): %s", dict(c))
            except Exception as e:
                L.warning("VALORE_REALE non riuscito: %s", str(e)[:300])
            return
        if tag.startswith("conf"):
            # 2/11 notte (cliente: "abbiamo un grave problema nella struttura dei confronti, trova cosa blocca tutto"):
            # per le auto di privati della zona scartate per pochi confronti: quanti annunci dello stesso modello c'erano
            # in partenza e quale regola li ha esclusi (somma dei motivi salvati in ogni valutazione). Solo conteggi.
            time.sleep(40)
            L = logging.getLogger("scovo")
            try:
                from ..pipeline.ciclo import _una_volta
                if not _una_volta(f"diagconf:{tag}"):
                    return
                from collections import Counter as _C
                from ..zone import tutte
                with connect() as conn:
                    conn.execute("SET statement_timeout = '600s'")
                    if tag.startswith("conf2"):
                        # motivi delle esclusioni PRIMA del confronto (campione di 40 per auto salvato nella valutazione)
                        ce = _C()
                        for r in conn.execute("""SELECT u.dati->'esclusi' AS e, (u.dati->>'n_esclusi')::int AS ex
                            FROM listings l JOIN LATERAL (SELECT categoria, dati FROM valutazioni_policy v WHERE v.listing_id=l.id
                                      ORDER BY computed_at DESC LIMIT 1) u ON true
                            WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%s)
                              AND l.price_eur BETWEEN 1000 AND 30000 AND u.categoria='dati_insufficienti'
                              AND u.dati->'motivi'->>0 LIKE 'solo %%confronti%%' LIMIT 3000""", (list(tutte()),)).fetchall():
                            ee = r["e"] if isinstance(r["e"], list) else json.loads(r["e"] or "[]")
                            tot = len(ee) or 1
                            for x in ee:
                                ce[re.sub(r"\d[\d.]*", "N", str(x.get("motivo")))[:70]] += (r["ex"] or 0) / tot
                        L.warning("DIAGCONF2 esclusioni prima del confronto (stima sul totale): %s",
                                  json.dumps({k: int(v) for k, v in ce.most_common(15)}, ensure_ascii=False))
                        from ..pricing.ricalcolo import _STATO_SQL as _ss
                        L.warning("DIAGCONF2 filtro stato della ricerca confronti: %s", _ss[:300])
                        return
                    rows = conn.execute("""SELECT l.make, l.model, l.fuel, l.power_kw IS NULL AS senza_kw, l.gearbox IS NULL AS senza_cambio,
                            (l.fuel ILIKE '%%benzin%%' AND COALESCE(l.mileage_km,0) <= 100000 AND l.price_eur <= 10000) AS b10,
                            u.dati->'non_equivalenti' AS ne, (u.dati->>'n_esclusi')::int AS ex, (u.dati->>'n')::int AS n
                        FROM listings l JOIN LATERAL (SELECT categoria, dati FROM valutazioni_policy v WHERE v.listing_id=l.id
                                  ORDER BY computed_at DESC LIMIT 1) u ON true
                        WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%s)
                          AND l.price_eur BETWEEN 1000 AND 30000 AND u.categoria='dati_insufficienti'
                          AND u.dati->'motivi'->>0 LIKE 'solo %%confronti%%'""", (list(tutte()),)).fetchall()
                    pool = conn.execute("""SELECT source, count(*) AS n FROM listings WHERE status='attivo'
                        AND last_seen_at > now() - interval '10 days' GROUP BY 1""").fetchall()
                mot, mot_b, base, campi, modelli = _C(), _C(), _C(), _C(), _C()
                for r in rows:
                    ne = r["ne"] if isinstance(r["ne"], dict) else json.loads(r["ne"] or "{}")
                    tot = (r["n"] or 0) + (r["ex"] or 0) + sum(int(v) for v in ne.values())
                    base["partenza 0" if tot == 0 else "1-9" if tot < 10 else "10-49" if tot < 50 else "50+"] += 1
                    for k, v in ne.items():
                        k2 = re.sub(r"\d[\d.]*", "N", k)[:60]
                        mot[k2] += int(v)
                        if r["b10"]:
                            mot_b[k2] += int(v)
                    mot["(esclusi: venduti/vecchi/danneggiati/prezzo)"] += r["ex"] or 0
                    campi["senza_kw"] += bool(r["senza_kw"]); campi["senza_cambio"] += bool(r["senza_cambio"])
                    if tot == 0:
                        modelli[f"{r['make']} {r['model']} {r['fuel']}"] += 1
                L.warning("DIAGCONF auto %d; annunci stesso modello in partenza: %s; campi: %s; base confronti attiva 10 gg: %s",
                          len(rows), json.dumps(dict(base)), json.dumps(dict(campi)), json.dumps([dict(r) for r in pool]))
                L.warning("DIAGCONF motivi di esclusione dei confronti (tutte): %s", json.dumps(dict(mot.most_common(15)), ensure_ascii=False))
                L.warning("DIAGCONF motivi (benzina <=10k <=100k km): %s", json.dumps(dict(mot_b.most_common(12)), ensure_ascii=False))
                L.warning("DIAGCONF modelli con ZERO annunci di partenza: %s", json.dumps(modelli.most_common(25), ensure_ascii=False))
            except Exception as e:
                L.warning("DIAGCONF non riuscita: %s", str(e)[:300])
            return
        time.sleep(90)
        try:
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
            from ..db import log_job
            from ..zone import tutte
            if not _due(f"diagnosi:{tag}", 100000):
                return
            with connect() as conn:
                fin = log_job(conn, f"diagnosi:{tag}")
                conn.execute("SET LOCAL statement_timeout = '600s'")
                rows = conn.execute("""
                    SELECT DISTINCT ON (vp.listing_id) vp.listing_id, vp.categoria, vp.dati->'motivi'->>0 AS motivo,
                           l.source, l.power_kw IS NULL AS senza_kw, l.gearbox IS NULL AS senza_cambio,
                           l.version_raw IS NULL AS senza_versione, l.mileage_km IS NULL AS senza_km
                    FROM valutazioni_policy vp JOIN listings l ON l.id = vp.listing_id
                    WHERE vp.computed_at > now() - interval '3 days' AND l.status='attivo' AND l.seller_type='privato'
                      AND upper(l.province) = ANY(%s) AND l.price_eur BETWEEN 1000 AND 50000
                    ORDER BY vp.listing_id, vp.computed_at DESC""", (list(tutte()),)).fetchall()
            from collections import Counter as _C
            cat, mot, fonte, campi = _C(), _C(), _C(), _C()
            for r in rows:
                cat[r["categoria"]] += 1
                if r["categoria"] != "dati_insufficienti":
                    continue
                m = re.sub(r"\d+", "N", (r["motivo"] or "nessun motivo"))[:70]
                mot[m] += 1; fonte[r["source"]] += 1
                for k in ("senza_kw", "senza_cambio", "senza_versione", "senza_km"):
                    campi[k] += bool(r[k])
            out = {"auto": len(rows), "categorie": dict(cat), "motivi": dict(mot.most_common(15)), "fonti": dict(fonte), "campi_mancanti": dict(campi)}
            lg.warning("DIAGNOSI_DATI %s", json.dumps(out, ensure_ascii=False))
            with connect() as conn:
                fin2 = log_job(conn, f"diagnosi:{tag}")
                fin2(True, {"auto": len(rows)})
        except Exception as e:
            lg.warning("DIAGNOSI_DATI non riuscita: %s", str(e)[:300])
    threading.Thread(target=diagnosi_dati, daemon=True, name="diagnosi").start()

    def valore_reale_notte():
        """2/11 notte: il valore reale si ricalcola ogni notte (verso le 3 ora italiana) con gli annunci e le vendite del
        giorno, e si rifà la prova sulle vendute (nei log VALORE_REALE PROVA, per controllare che l'errore non salga).
        VR_NOTTE=0 lo spegne."""
        if os.environ.get("VR_NOTTE", "1") != "1":
            return
        L = logging.getLogger("scovo")
        time.sleep(600)
        while True:
            try:
                from datetime import datetime as _dt, timezone as _tz
                if _dt.now(_tz.utc).hour == int(os.environ.get("VR_ORA_UTC", "2")):
                    with connect() as conn:
                        ult = conn.execute("SELECT max(computed_at) AS t FROM valore_reale").fetchone()["t"] \
                            if conn.execute("SELECT to_regclass('valore_reale') AS t").fetchone()["t"] else None
                    if ult is None or (_dt.now(_tz.utc) - ult).total_seconds() > 20 * 3600:
                        from ..pipeline.ciclo import inizia   # 3/11: fatto solo se il calcolo finisce
                        lav = inizia("valore_reale_notte:" + _dt.now(_tz.utc).strftime("%Y%m%d"))
                        if lav:
                            from ..pricing import valore_reale as VR
                            from ..zone import tutte
                            # 3/11 (controllo indipendente): prima il calcolo (serve al sito), poi la prova in un try a
                            # parte: se la prova fallisce il valore reale resta aggiornato
                            try:
                                with connect() as conn:
                                    conn.execute("SET statement_timeout = '900s'")
                                    L.warning("VALORE_REALE CALCOLO %s", json.dumps(
                                        VR.calcola_e_salva(conn, prov_zona=list(tutte()),
                                                           commercianti_fuori=list(COMMERCIANTI_PROVINCE_ESCLUSE)), default=str))
                            except Exception as e:
                                lav.fallito(e)                  # il giro dopo (fra 30 minuti) lo rifà
                                raise
                            lav.fatto()
                            try:
                                with connect() as conn:
                                    conn.execute("SET statement_timeout = '900s'")
                                    L.warning("VALORE_REALE PROVA %s", json.dumps(VR.prova(conn), ensure_ascii=False, default=str))
                            except Exception as e:
                                L.warning("VALORE_REALE prova notturna non riuscita: %s", str(e)[:300])
            except Exception as e:
                L.warning("VALORE_REALE notte non riuscito: %s", str(e)[:300])
            time.sleep(1800)
    threading.Thread(target=valore_reale_notte, daemon=True, name="vr-notte").start()

    def soglia_7k():
        """2/11 sera (cliente: "abbassiamo fino ai 7k, va bene 1.500 € di margine ... trova un modo per far entrare più
        auto"): RINVIO_INVIA=soglia7k<n>. 1) rivaluta le auto di privati della zona da 5.000 a 6.999 € scartate come
        non convenienti con margine tra 1.500 e 2.199 € (con la soglia nuova possono entrare); 2) diagnosi completa dei
        motivi di scarto delle auto di privati della zona (solo conteggi). Una volta per etichetta."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("soglia7k"):
            return
        time.sleep(30)
        L = logging.getLogger("scovo")
        lav = None
        try:
            from ..pipeline.ciclo import inizia      # 3/11: segnato fatto solo a lavoro finito (prima: prima di farlo)
            lav = inizia(f"soglia7k:{tag}")
            if not lav:
                return
            from collections import Counter as _C
            from ..pipeline import tempo_reale as tr
            from ..zone import tutte
            L.warning("SOGLIA7K soglie in uso: SOGLIE_PV env=%r tabella=%s policy=%s esempi 5500->%s 6900->%s",
                      os.environ.get("SOGLIE_PV"), _sc.SOGLIE_PV[:3], _sc.POLICY_SITO, _sc.soglia(5500), _sc.soglia(6900))
            prov = list(tutte())
            sql_ultima = """
                SELECT DISTINCT ON (vp.listing_id) vp.listing_id, vp.categoria, vp.dati, l.price_eur, l.source
                FROM valutazioni_policy vp JOIN listings l ON l.id = vp.listing_id
                WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%s)
                  AND l.price_eur BETWEEN %s AND %s AND vp.computed_at > now() - interval '5 days'
                ORDER BY vp.listing_id, vp.computed_at DESC"""
            with connect() as conn:
                conn.execute("SET statement_timeout = '600s'")
                rows = conn.execute(sql_ultima, (prov, 5000, 6999)).fetchall()
                ids = [r["listing_id"] for r in rows if r["categoria"] == "non_conveniente"
                       and 1500 <= int((r["dati"] or {}).get("margine") or -99999) < 2200]
                L.warning("SOGLIA7K da rivalutare: %d (su %d auto 5.000-6.999 €)", len(ids), len(rows))
                st = _C()
                for i in range(0, len(ids), 25):
                    tr.valuta_e_pubblica(conn, ids[i:i + 25], st)
                    conn.commit()
                L.warning("SOGLIA7K rivalutate: %s", json.dumps(dict(st)))
                L.warning("SOGLIA7K diagnosi in corso")
                rows = conn.execute(sql_ultima, (prov, 1000, 30000)).fetchall()
            cat, mot, marg, fonte = _C(), _C(), _C(), _C()
            for r in rows:
                cat[r["categoria"]] += 1
                d = r["dati"] or {}
                if r["categoria"] == "dati_insufficienti":
                    m = (d.get("motivi") or ["nessun motivo"])[0]
                    mot[re.sub(r"\d[\d.]*", "N", m)[:80]] += 1
                    fonte[r["source"]] += 1
                if r["categoria"] == "non_conveniente" and d.get("margine") is not None and d.get("soglia"):
                    manca = int(d["soglia"]) - int(d["margine"])
                    marg["mancano <300" if manca < 300 else "300-700" if manca < 700 else "700-1500" if manca < 1500 else ">1500"] += 1
            L.warning("SOGLIA7K diagnosi privati zona 1-30k: auto %d, categorie %s", len(rows), json.dumps(dict(cat)))
            L.warning("SOGLIA7K dati insufficienti per motivo: %s", json.dumps(dict(mot.most_common(20)), ensure_ascii=False))
            L.warning("SOGLIA7K dati insufficienti per fonte: %s; non convenienti per quanto manca alla soglia: %s",
                      json.dumps(dict(fonte)), json.dumps(dict(marg)))
            lav.fatto()
        except Exception as e:
            L.warning("SOGLIA7K non riuscita: %s", str(e)[:300])
            if lav:
                lav.fallito(e)
    threading.Thread(target=soglia_7k, daemon=True, name="soglia7k").start()

    def ripara_danni():
        """2/11 sera (cliente: "trova un qualsiasi modo per far entrare più auto, con le nostre regole"): le parole di
        danno scattavano anche su frasi normali ("motore da 1.4", "cambio da 6 marce", "frizione da poco sostituita") e
        l'auto veniva trattata come incidentata. RINVIO_INVIA=riparadanni<n>: per le auto di privati della zona segnate
        "con danni" SOLO per quelle frasi (la regola nuova non trova più nulla, nessuna lettura AI che dica danno) si
        toglie il segno e si rivalutano. Le auto davvero danneggiate restano fuori. Solo conteggi nei log."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("riparadanni"):
            return
        time.sleep(30)
        L = logging.getLogger("scovo")
        lav = None
        try:
            from ..pipeline.ciclo import inizia      # 3/11: segnato fatto solo a lavoro finito (prima: prima di farlo)
            lav = inizia(f"riparadanni:{tag}")
            if not lav:
                return
            from collections import Counter as _C
            from ..core.models import Listing
            from ..core.normalize import problem_hint
            from ..pipeline import tempo_reale as tr
            from ..zone import tutte
            VECCHIA = re.compile(r"\b(frizione (?:da|che) |distribuzione da|turbina da|motore da|cambio da)", re.I)
            with connect() as conn:
                conn.execute("SET statement_timeout = '600s'")
                rows = conn.execute(
                    "SELECT l.id, l.title, l.description FROM listings l WHERE l.status='attivo' AND l.seller_type='privato' "
                    "AND l.damage_declared AND upper(l.province) = ANY(%s) AND l.price_eur BETWEEN 1000 AND 30000 "
                    "AND NOT EXISTS (SELECT 1 FROM lettura_ai a WHERE a.listing_id=l.id AND a.cambiati ? 'damage_declared') "
                    "LIMIT 20000", (list(tutte()),)).fetchall()
                ids = []
                for r in rows:
                    testo = " ".join(filter(None, [r["title"], r["description"]]))
                    if not VECCHIA.search(testo):
                        continue
                    if problem_hint(Listing(source="x", source_id="x", url="", title=r["title"], description=r["description"])):
                        continue
                    ids.append(r["id"])
                L.warning("RIPARADANNI auto segnate con danni: %d, solo per frasi normali: %d", len(rows), len(ids))
                for i in range(0, len(ids), 500):
                    conn.execute("UPDATE listings SET damage_declared = NULL WHERE id = ANY(%s)", (ids[i:i + 500],))
                conn.commit()
                st = _C()
                for i in range(0, len(ids), 25):
                    tr.valuta_e_pubblica(conn, ids[i:i + 25], st)
                    conn.commit()
                L.warning("RIPARADANNI rivalutate: %s", json.dumps(dict(st)))
            lav.fatto()
        except Exception as e:
            L.warning("RIPARADANNI non riuscita: %s", str(e)[:300])
            if lav:
                lav.fallito(e)
    threading.Thread(target=ripara_danni, daemon=True, name="riparadanni").start()

    def rivaluta_costi():
        """2/11 sera (cliente: "togli i 600 euro di costi"): POLICY_COSTO_FISSO=0. RINVIO_INVIA=rivcosti<n> rivaluta
        subito le auto di privati della zona scartate come non convenienti a cui mancavano al massimo 600 € per la
        soglia (con il costo tolto possono entrare). Una volta per etichetta; nei log solo conteggi."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("rivcosti"):
            return
        time.sleep(30)
        L = logging.getLogger("scovo")
        lav = None
        try:
            from ..pipeline.ciclo import inizia      # 3/11: segnato fatto solo a lavoro finito (prima: prima di farlo)
            lav = inizia(f"rivcosti:{tag}")
            if not lav:
                return
            from collections import Counter as _C
            from ..pipeline import tempo_reale as tr
            from ..pricing import policy_v2 as P
            from ..zone import tutte
            L.warning("RIVCOSTI costo fisso in uso: %s €", P.PolicyConfig.from_env().costo_fisso)
            with connect() as conn:
                conn.execute("SET statement_timeout = '600s'")
                rows = conn.execute("""SELECT l.id, l.price_eur, u.margine FROM listings l
                    JOIN LATERAL (SELECT categoria, margine FROM valutazioni_policy v WHERE v.listing_id=l.id
                                  ORDER BY computed_at DESC LIMIT 1) u ON true
                    WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%s)
                      AND l.price_eur BETWEEN 1000 AND 20000 AND COALESCE(l.mileage_km,0) <= 150000
                      AND u.categoria = 'non_conveniente' AND u.margine IS NOT NULL""", (list(tutte()),)).fetchall()
                ids = [r["id"] for r in rows if int(r["margine"]) >= _sc.soglia(int(r["price_eur"])) - 600]
                L.warning("RIVCOSTI non convenienti %d, a cui mancavano <= 600 €: %d", len(rows), len(ids))
                st = _C()
                for i in range(0, len(ids), 25):
                    tr.valuta_e_pubblica(conn, ids[i:i + 25], st)
                    conn.commit()
                L.warning("RIVCOSTI rivalutate: %s", json.dumps(dict(st)))
            lav.fatto()
        except Exception as e:
            L.warning("RIVCOSTI non riuscita: %s", str(e)[:300])
            if lav:
                lav.fallito(e)
    threading.Thread(target=rivaluta_costi, daemon=True, name="rivcosti").start()

    def valuta_mai_valutate():
        """2/11 notte (cliente: "me ne servono di più, subito"): RINVIO_INVIA=valnuove<n> valuta subito le auto di
        privati della zona mai valutate (fino a 30.000 €, fino a 150.000 km), prima le benzina. Solo conteggi nei log."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("valnuove"):
            return
        time.sleep(30)
        L = logging.getLogger("scovo")
        lav = None
        try:
            from ..pipeline.ciclo import inizia      # 3/11: segnato fatto solo a lavoro finito (prima: prima di farlo)
            lav = inizia(f"valnuove:{tag}")
            if not lav:
                return
            from collections import Counter as _C
            from ..pipeline import tempo_reale as tr
            from ..zone import tutte
            with connect() as conn:
                conn.execute("SET statement_timeout = '600s'")
                # 2/11 notte (cliente: "dove sono finiti gli annunci raccolti per fare più confronti?"): base dei confronti
                for r in conn.execute("""SELECT source, status, count(*) AS n,
                        count(*) FILTER (WHERE last_seen_at > now() - interval '10 days') AS visti10,
                        count(*) FILTER (WHERE last_seen_at > now() - interval '30 days') AS visti30,
                        count(*) FILTER (WHERE first_seen_at > now() - interval '15 days') AS nuovi15
                        FROM listings GROUP BY 1,2 ORDER BY n DESC LIMIT 12""").fetchall():
                    L.warning("VALNUOVE base_confronti %s", json.dumps(dict(r), default=str))
                ids = [] if "conf" in tag else [r["id"] for r in conn.execute("""SELECT l.id FROM listings l
                    WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%s)
                      AND l.price_eur BETWEEN 1000 AND 30000 AND COALESCE(l.mileage_km,0) <= 150000
                      AND NOT EXISTS (SELECT 1 FROM valutazioni_policy v WHERE v.listing_id=l.id)
                    ORDER BY (l.fuel ILIKE '%%benzin%%') DESC, l.last_seen_at DESC LIMIT 3000""", (list(tutte()),)).fetchall()]
                L.warning("VALNUOVE mai valutate: %d", len(ids))
                st = _C()
                for i in range(0, len(ids), 25):
                    tr.valuta_e_pubblica(conn, ids[i:i + 25], st)
                    conn.commit()
                    if i % 500 == 0:
                        L.warning("VALNUOVE avanzamento %d/%d: %s", i, len(ids), json.dumps(dict(st)))
                L.warning("VALNUOVE fatto: %s", json.dumps(dict(st)))
                # poi le auto scartate per pochi confronti: con il livello 2 corretto oggi (si allarga fino al minimo del
                # sito) molte trovano abbastanza auto uguali. Prima le benzina fino a 100.000 km, poi il resto.
                ids = [r["id"] for r in conn.execute("""SELECT l.id FROM listings l
                    JOIN LATERAL (SELECT categoria, dati FROM valutazioni_policy v WHERE v.listing_id=l.id
                                  ORDER BY computed_at DESC LIMIT 1) u ON true
                    WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%s)
                      AND l.price_eur BETWEEN 1000 AND 30000 AND COALESCE(l.mileage_km,0) <= 150000
                      AND u.categoria = 'dati_insufficienti' AND u.dati->'motivi'->>0 LIKE 'solo %%confronti validi%%'
                    ORDER BY (l.fuel ILIKE '%%benzin%%' AND COALESCE(l.mileage_km,0) <= 100000) DESC,
                             (l.price_eur <= 10000) DESC, l.last_seen_at DESC LIMIT 4000""", (list(tutte()),)).fetchall()]
                L.warning("VALNUOVE pochi confronti da rivalutare: %d", len(ids))
                st = _C()
                for i in range(0, len(ids), 25):
                    tr.valuta_e_pubblica(conn, ids[i:i + 25], st)
                    conn.commit()
                    if i % 500 == 0:
                        L.warning("VALNUOVE confronti avanzamento %d/%d: %s", i, len(ids), json.dumps(dict(st)))
                L.warning("VALNUOVE confronti fatto: %s", json.dumps(dict(st)))
            lav.fatto()
        except Exception as e:
            L.warning("VALNUOVE non riuscita: %s", str(e)[:300])
            if lav:
                lav.fallito(e)
    threading.Thread(target=valuta_mai_valutate, daemon=True, name="valnuove").start()

    def rivaluta_tutte():
        """3/11 notte (cliente: "mettilo come metodo principale e rivaluta tutte le auto"): RINVIO_INVIA=rivtutte<n>
        rivaluta con il valore reale tutte le auto di privati della zona fino a 20.000 € e 150.000 km che hanno il
        valore calcolato: prima le benzina fino a 100.000 km, poi dalla più economica. Solo conteggi nei log."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("rivtutte"):
            return
        time.sleep(45)
        L = logging.getLogger("scovo")
        try:
            # 3/11: lucchetto per tutta la durata (non "una volta sola"): se il sito si riavvia il giro riprende da
            # dove era (le auto già fatte col codice attuale si saltano)
            _cm_lk = connect()               # il riferimento resta per tutta la funzione (sennò la connessione si chiude)
            _lk = _cm_lk.__enter__()
            _ok_lk = _lk.execute("SELECT pg_try_advisory_lock(778821) AS ok").fetchone()["ok"]
            _lk.commit()                     # il lucchetto di sessione resta, la transazione no
            if not _ok_lk:
                return
            from collections import Counter as _C
            from ..pipeline import tempo_reale as tr
            from ..pricing import policy_v2 as _P
            from ..zone import tutte
            # 3/11: dalla 2a volta (rivtutte2...) prima si ricalcola il valore reale (stima di ogni auto SENZA lei stessa
            # nel modello), poi si rivalutano solo le auto non ancora fatte con il codice attuale (POLICY_REV): se il
            # sito si riavvia, il giro riprende da dove era. Per velocità le auto vanno a gruppi dello stesso modello.
            if tag != "rivtutte1" and os.environ.get("RIVTUTTE_VR", "1") == "1":
                try:
                    from ..pricing import valore_reale as VR
                    with connect() as conn:
                        fresco = conn.execute("SELECT max(computed_at) > now() - interval '6 hours' AS ok "
                                              "FROM valore_reale").fetchone()["ok"]
                    if fresco:
                        raise StopIteration          # già ricalcolato nelle ultime 6 ore (es. riavvio): si riprende
                    with connect() as conn:
                        conn.execute("SET statement_timeout = '900s'")
                        L.warning("RIVTUTTE valore reale ricalcolato: %s",
                                  json.dumps(VR.calcola_e_salva(conn, prov_zona=list(tutte())), default=str))
                except StopIteration:
                    L.warning("RIVTUTTE valore reale già fresco: non si ricalcola")
                except Exception as e:
                    L.warning("RIVTUTTE ricalcolo valore reale non riuscito (si usa quello di stanotte): %s", str(e)[:300])
            with connect() as conn:
                conn.execute("SET statement_timeout = '600s'")
                rows = conn.execute("""SELECT l.id, l.make, l.model, l.fuel, l.price_eur, COALESCE(u.margine, -99999) AS margine,
                        (l.stage = 'approfondito' AND COALESCE(u.margine, -1) >= 500) AS sul_sito,
                        (l.fuel ILIKE '%%benzin%%' AND COALESCE(l.mileage_km,0) <= 100000) AS benz100
                    FROM listings l JOIN valore_reale v ON v.listing_id = l.id
                    LEFT JOIN valutazioni_policy u ON u.listing_id = l.id AND u.policy_version = %s
                    WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%s)
                      AND l.price_eur BETWEEN 1000 AND 20000 AND COALESCE(l.mileage_km,0) <= 150000
                      AND v.computed_at > now() - interval '2 days'
                      AND NOT (u.impronta_regole IS NOT DISTINCT FROM %s AND u.computed_at > v.computed_at)
                    LIMIT %s""", (_P.POLICY_VERSION, list(tutte()), _RG.impronta(),
                                  int(os.environ.get("RIVTUTTE_MAX", "40000")))).fetchall()
                # prima le auto che possono essere sul sito, poi le benzina fino a 100.000 km, poi le altre; dentro
                # ogni parte per modello (un solo caricamento dei confronti per gruppo)
                # 3/11 10:23 (cliente: "mi servono molte più auto sul sito, ho una presentazione"): prima le auto più
                # vicine alla soglia (margine salvato da −800 € in su: con la regola nuova possono entrare), poi quelle
                # del sito, poi le benzina fino a 100.000 km, poi le altre
                # 3/11 10:26 (cliente: "tantissime auto a benzina sotto 10k e sotto i 100.000 km"): per prime le benzina
                # fino a 10.000 € e 100.000 km vicine alla soglia, poi tutte le benzina fino a 10.000 € e 100.000 km
                b10 = lambda r: bool(r["benz100"]) and (r["price_eur"] or 0) <= 10000
                rows = sorted(rows, key=lambda r: (not (b10(r) and r["margine"] >= -800), not b10(r),
                                                   not (r["margine"] >= -800), not r["sul_sito"], not r["benz100"],
                                                   r["make"] or "", r["model"] or "", r["fuel"] or ""))
                ids = [r["id"] for r in rows]
                del rows
                L.warning("RIVTUTTE da rivalutare con il valore reale: %d", len(ids))
                st = _C()
                for i in range(0, len(ids), 30):
                    _aspetta_memoria(L, "RIVTUTTE")
                    try:
                        tr.valuta_e_pubblica(conn, ids[i:i + 30], st)
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        st["blocchi_non_riusciti"] += 1
                        L.warning("RIVTUTTE blocco %d non riuscito: %s", i, str(e)[:200])
                    if i % 600 == 0:
                        import gc as _gc
                        _gc.collect()
                        L.warning("RIVTUTTE avanzamento %d/%d (memoria %.0f MB): %s", i, len(ids), _rss_mb(), json.dumps(dict(st)))
                L.warning("RIVTUTTE fatto: %s", json.dumps(dict(st)))
        except Exception as e:
            L.warning("RIVTUTTE non riuscita: %s", str(e)[:300])
    threading.Thread(target=rivaluta_tutte, daemon=True, name="rivtutte").start()

    def riallinea_sito():
        """3/11 (cliente: "tutte le auto valutate col nuovo metodo"): ogni RIALLINEA_MIN minuti le auto che possono
        stare sul sito (privati della zona, margine salvato ≥ 500 €) ma valutate con un codice diverso da quello attuale
        (POLICY_REV: per esempio dal ricalcolo del cron non ancora aggiornato) si rivalutano qui, poche alla volta.
        RIALLINEA=0 lo spegne. Ogni auto si riprova al massimo ogni 6 ore (es. Subito senza numero, che si salta)."""
        # 3/11 23:00: spento di base per lo stesso motivo (calcolo del sito con meno confronti del cron)
        if os.environ.get("RIALLINEA", "0") != "1":
            return
        L = logging.getLogger("scovo")
        time.sleep(300)
        provate: dict = {}
        while True:
            try:
                from collections import Counter as _C
                from ..pipeline import tempo_reale as tr
                from ..pricing import policy_v2 as _P
                from ..zone import tutte
                ora = time.time()
                for k in [k for k, t0 in provate.items() if ora - t0 > 6 * 3600]:
                    provate.pop(k, None)
                with connect() as conn:
                    conn.execute("SET statement_timeout = '300s'")
                    rows = conn.execute("""SELECT l.id, l.make, l.model, l.fuel FROM listings l
                        JOIN valutazioni_policy u ON u.listing_id = l.id AND u.policy_version = %s
                        WHERE l.status='attivo' AND l.seller_type='privato' AND l.stage='approfondito'
                          AND upper(l.province) = ANY(%s) AND COALESCE(u.margine, -1) >= 500
                          AND u.impronta_regole IS DISTINCT FROM %s
                        LIMIT 3000""", (_P.POLICY_VERSION, list(tutte()), _RG.impronta())).fetchall()
                    rows = sorted([r for r in rows if r["id"] not in provate],
                                  key=lambda r: (r["make"] or "", r["model"] or "", r["fuel"] or ""))
                    rows = rows[:int(os.environ.get("RIALLINEA_MAX", "600"))]
                    st = _C()
                    for i in range(0, len(rows), 60):
                        pezzo = [r["id"] for r in rows[i:i + 60]]
                        for k in pezzo:
                            provate[k] = ora
                        _aspetta_memoria(L, "RIALLINEA")
                        try:
                            tr.valuta_e_pubblica(conn, pezzo, st)
                            conn.commit()
                        except Exception as e:
                            conn.rollback()
                            L.warning("RIALLINEA blocco non riuscito: %s", str(e)[:200])
                    if rows:
                        L.warning("RIALLINEA %d auto del sito rivalutate col codice attuale (%s): %s", len(rows),
                                  _P.POLICY_REV, json.dumps(dict(st)))
            except Exception as e:
                L.warning("RIALLINEA non riuscito: %s", str(e)[:300])
            time.sleep(float(os.environ.get("RIALLINEA_MIN", "20")) * 60)
    threading.Thread(target=riallinea_sito, daemon=True, name="riallinea").start()

    def diag_benzina():
        """3/11 22:05: quante benzina <10k <100k km dei privati in zona ci sono nel database e a che punto della
        valutazione si fermano (solo conteggi nei log, BENZ_DB)."""
        time.sleep(400)
        try:
            from ..pricing import policy_v2 as _P
            from ..zone import tutte
            with connect() as conn:
                conn.execute("SET statement_timeout = '240s'")
                base = """FROM listings l WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%(pr)s)
                          AND l.fuel ILIKE 'benzin%%' AND l.price_eur BETWEEN 1000 AND 9999 AND l.mileage_km < 100000"""
                pr = [p for p in tutte() if p in PROVINCE_100KM] or list(tutte())
                tot = conn.execute("SELECT count(*) AS n, count(*) FILTER (WHERE l.stage='approfondito') AS appr " + base,
                                   {"pr": pr}).fetchone()
                cat = conn.execute("""SELECT COALESCE(u.categoria,'senza_valutazione') AS c, count(*) AS n,
                        count(*) FILTER (WHERE u.margine >= 1500) AS m1500, count(*) FILTER (WHERE u.margine BETWEEN 800 AND 1499) AS m800,
                        count(*) FILTER (WHERE u.margine BETWEEN 0 AND 799) AS m0,
                        count(*) FILTER (WHERE u.impronta_regole IS NOT DISTINCT FROM %(rev)s) AS rev_nuova
                        """ + base.replace("FROM listings l", "FROM listings l LEFT JOIN LATERAL (SELECT categoria, margine, impronta_regole "
                        "FROM valutazioni_policy v WHERE v.listing_id = l.id ORDER BY computed_at DESC LIMIT 1) u ON true") +
                        " GROUP BY 1 ORDER BY 2 DESC", {"pr": pr, "rev": _RG.impronta()}).fetchall()
            logging.getLogger("scovo").warning("BENZ_DB totale=%s approfondite=%s per categoria: %s", tot["n"], tot["appr"],
                                               json.dumps([dict(r) for r in cat], default=str))
        except Exception as e:
            logging.getLogger("scovo").warning("BENZ_DB non riuscito: %s", str(e)[:300])
    threading.Thread(target=diag_benzina, daemon=True, name="diag_benz").start()

    def benzina_subito():
        """3/11 22:15 (cliente: "lo voglio adesso, non stanotte"): ricalcolo SUBITO, col codice attuale, delle benzina
        dei privati in zona sotto 10.000 € e 100.000 km (prima quelle col margine salvato più alto). Blocchi da 40 col
        controllo della memoria; salta quelle già fatte col codice attuale (riparte giusto dopo un riavvio)."""
        # 3/11 23:00: SPENTO di base. Il calcolo nel sito trova meno auto uguali del cron (es. 19 contro 13, 193 contro 22)
        # e sovrascriveva valutazioni migliori: le auto buone uscivano dal sito. Il ricalcolo si fa nel cron.
        if os.environ.get("BENZINA_SUBITO", "0") != "1":
            return
        L = logging.getLogger("scovo")
        time.sleep(90)
        try:
            from collections import Counter as _C
            from ..pipeline import tempo_reale as tr
            from ..pricing import policy_v2 as _P
            from ..zone import tutte
            pr = [p for p in tutte() if p in PROVINCE_100KM] or list(tutte())
            with connect() as conn:
                conn.execute("SET statement_timeout = '300s'")
                rows = conn.execute("""SELECT l.id, l.make, l.model, l.fuel FROM listings l
                    LEFT JOIN LATERAL (SELECT margine, impronta_regole FROM valutazioni_policy v WHERE v.listing_id = l.id
                                       ORDER BY computed_at DESC LIMIT 1) u ON true
                    WHERE l.status='attivo' AND l.seller_type IN ('privato', 'commerciante')
                      AND upper(l.province) = ANY(%s) AND l.price_eur BETWEEN 1000 AND 9999
                      AND (l.fuel ~* '(benzin|ibrid|hybrid|gpl|lpg|metano)' OR (l.fuel ~* 'diesel' AND l.year >= 2017))
                      AND COALESCE(l.mileage_km, 0) BETWEEN 1 AND 99999 AND COALESCE(l.damage_declared, false) = false
                      AND u.impronta_regole IS DISTINCT FROM %s AND COALESCE(u.margine, 0) >= -2500
                    ORDER BY COALESCE(u.margine, 0) DESC LIMIT 5000""", (pr, _RG.impronta())).fetchall()
                L.warning("BENZINA_SUBITO da ricalcolare: %d", len(rows))
                rows = sorted(rows, key=lambda r: (r["make"] or "", r["model"] or "", r["fuel"] or ""))
                st = _C()
                perse = [0]
                for i in range(0, len(rows), 40):
                    _aspetta_memoria(L, "BENZINA_SUBITO")
                    pezzo = [r["id"] for r in rows[i:i + 40]]
                    _q = """SELECT DISTINCT ON (listing_id) listing_id, categoria, margine, riassunto FROM valutazioni_policy
                            WHERE listing_id = ANY(%s) ORDER BY listing_id, computed_at DESC"""
                    prima = {r["listing_id"]: r for r in conn.execute(_q, (pezzo,)).fetchall()}
                    try:
                        tr.valuta_e_pubblica(conn, pezzo, st)
                        conn.commit()
                        if perse[0] < 25:     # 3/11 22:50: auto che erano buone e col ricalcolo non lo sono più: perché
                            dopo = {r["listing_id"]: r for r in conn.execute(_q, (pezzo,)).fetchall()}
                            for k, a in prima.items():
                                b = dopo.get(k)
                                if b and a["categoria"] in _sc.CATEGORIE_SITO and b["categoria"] not in _sc.CATEGORIE_SITO and perse[0] < 25:
                                    perse[0] += 1
                                    ra, rb = a["riassunto"] or {}, b["riassunto"] or {}
                                    L.warning("BENZINA_PERSA %s", json.dumps({"id": k, "prima": [a["categoria"], a["margine"], ra.get("M"),
                                              ra.get("R_finale") or ra.get("R"), ra.get("metodo"), ra.get("n"), ra.get("policy_rev")],
                                              "dopo": [b["categoria"], b["margine"], rb.get("M"), rb.get("R_finale") or rb.get("R"),
                                              rb.get("metodo"), rb.get("n"), rb.get("soglia")]}, default=str))
                    except Exception as e:
                        conn.rollback()
                        L.warning("BENZINA_SUBITO blocco non riuscito: %s", str(e)[:200])
                    if (i // 40) % 10 == 9:
                        L.warning("BENZINA_SUBITO avanzamento %d/%d: %s", i + 40, len(rows), json.dumps(dict(st)))
                        _elenco["t"] = 0          # l'elenco del sito si rifà col prossimo caricamento
                _elenco["t"] = 0
                L.warning("BENZINA_SUBITO FINE %d auto: %s", len(rows), json.dumps(dict(st)))
        except Exception as e:
            L.warning("BENZINA_SUBITO non riuscito: %s", str(e)[:300])
    threading.Thread(target=benzina_subito, daemon=True, name="benzina_subito").start()

    _AVVIO_COMM = time.strftime("%Y%m%d%H%M%S")

    def commercianti_top_lavoro():
        """3/11 10:29 (cliente): commercianti LONTANI (fuori provincia di Milano), senza promo/leasing/prezzi esca, valutati
        col metodo nuovo (valore reale). Una volta al giorno il valore reale anche per loro, poi ogni 20 minuti le auto
        con un margine possibile (prezzo ≤ V −5% −1.200 €, mai sotto il 75% di V). Sul sito vanno solo i 40 migliori
        (_solo_privati / _tetto_commercianti). COMMERCIANTI_LAVORO=0 lo spegne."""
        if os.environ.get("COMMERCIANTI_LAVORO", "1") != "1":
            return
        L = logging.getLogger("scovo")
        time.sleep(150)
        while True:
            try:
                from collections import Counter as _C
                from datetime import datetime as _dt, timezone as _tz
                from ..core.models import Listing as _Lst
                from ..core.normalize import problem_hint as _ph
                from ..pricing import policy_v2 as _P
                from ..pricing import valore_reale as VR
                from ..pricing.valutazione import valuta_e_scrivi
                from ..zone import tutte
                from ..pipeline.ciclo import _una_volta
                escl = list(COMMERCIANTI_PROVINCE_ESCLUSE)
                with connect() as conn:
                    conn.execute("SET statement_timeout = '600s'")
                    n_vr = conn.execute("""SELECT count(*) AS n FROM valore_reale v JOIN listings l ON l.id = v.listing_id
                        WHERE l.seller_type = 'commerciante' AND v.computed_at > now() - interval '20 hours'""").fetchone()["n"]
                # una volta per avvio del sito (un riavvio a metà calcolo lo rifà subito, non dopo un'ora)
                # 3/11 11:50: il calcolo completo di giorno mandava il sito fuori memoria (512 MB): solo di notte (2-5 UTC)
                if n_vr < 200 and 2 <= _dt.now(_tz.utc).hour < 5 and _una_volta("vr_commercianti:" + _AVVIO_COMM):
                    with connect() as conn:
                        conn.execute("SET statement_timeout = '900s'")
                        L.warning("COMMERCIANTI_TOP valore reale (privati + commercianti lontani): %s", json.dumps(
                            VR.calcola_e_salva(conn, prov_zona=list(tutte()), commercianti_fuori=escl), default=str))
                with connect() as conn:
                    conn.execute("SET statement_timeout = '600s'")
                    ids = [r["id"] for r in conn.execute("""SELECT l.id FROM listings l JOIN valore_reale v ON v.listing_id = l.id
                        LEFT JOIN valutazioni_policy u ON u.listing_id = l.id AND u.policy_version = %(pv)s
                        WHERE l.status='attivo' AND l.seller_type='commerciante' AND upper(l.province) = ANY(%(zona)s)
                          AND NOT (upper(l.province) = ANY(%(escl)s))
                          AND l.price_eur BETWEEN 1000 AND 20000 AND COALESCE(l.mileage_km,0) <= 150000
                          AND l.last_seen_at > now() - interval '4 days' AND v.computed_at > now() - interval '2 days'
                          AND l.price_eur <= v.v * 0.95 - 1200 AND l.price_eur >= v.v * %(minimo)s
                          AND NOT (coalesce(l.price_flags, '{}'::text[]) && %(flag)s::text[])
                          AND NOT ((coalesce(l.title,'') || ' ' || coalesce(l.description,'')) ~* %(promo)s)
                          AND NOT ((coalesce(l.title,'') || ' ' || coalesce(l.description,'')) ~* %(nomi)s)
                          AND COALESCE(l.damage_declared, false) = false
                          AND NOT (u.impronta_regole IS NOT DISTINCT FROM %(rev)s AND u.computed_at > v.computed_at)
                        ORDER BY (v.v * 0.95 - l.price_eur) DESC LIMIT 400""",
                        {"pv": _P.POLICY_VERSION, "zona": list(tutte()), "escl": escl, "minimo": COMMERCIANTI_PREZZO_MIN_SU_M,
                         "flag": _FLAG_PROMO, "promo": RE_PROMO_COMMERCIANTE, "nomi": _commercianti_nomi_re() or "$^",
                         "rev": _RG.impronta()}).fetchall()]
                    st = _C()
                    # 4/11: stessa valutazione di cron e sito (pricing/valutazione.valuta_e_scrivi): per un commerciante
                    # un prezzo molto sotto il mercato non è mai ammesso (niente prezzi esca, regole.antitruffa)
                    for k in range(0, len(ids), 30):
                        _aspetta_memoria(L, "COMMERCIANTI_TOP")
                        es = valuta_e_scrivi(conn, ids[k:k + 30], "commercianti", extra={i: {"sezione": "affari"} for i in ids[k:k + 30]})
                        for chiave, n in es.stats.items():
                            st[chiave] += n
                        for lid, cat in es.salvata.items():
                            v = es.valutazioni.get(lid) or {}
                            if cat not in scovo.CATEGORIE_SITO or v.get("categoria") != cat:
                                continue
                            try:
                                conf = "affidabile" if cat == "opportunita" else "da_verificare"
                                conn.execute(
                                    """INSERT INTO valuations (listing_id, engine_version, private_median, resale_median, resale_prudent,
                                         n_comparables, comparables_used, confidence, confidence_reasons, parts_cost_low, parts_cost_high)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0)""",
                                    (lid, _P.POLICY_VERSION + "+commercianti-top", v.get("M"), v.get("M"), v.get("R_finale"),
                                     v.get("n"), json.dumps(v.get("confronti") or [])[:200000], conf, (v.get("motivi") or [])[:5]))
                                conn.execute("UPDATE listings SET stage='approfondito', stage_reason=%s, deep_at=now(), "
                                             "last_checked_at=now() WHERE id=%s", (conf, lid))
                                conn.commit()
                                st["pubblicate"] += 1
                            except Exception as e:
                                conn.rollback()
                                st["errore"] += 1
                                if st["errore"] <= 3:
                                    L.warning("COMMERCIANTI_TOP %s: %s", lid, str(e)[:150])
                    L.warning("COMMERCIANTI_TOP giro: %d candidati lontani senza promo, %s", len(ids), json.dumps(dict(st)))
            except Exception as e:
                L.warning("COMMERCIANTI_TOP non riuscito: %s", str(e)[:300])
            time.sleep(20 * 60)
    threading.Thread(target=commercianti_top_lavoro, daemon=True, name="commercianti-top").start()

    def diagnosi_incompleti():
        """2/11 notte: RINVIO_INVIA=incompleti<n>. Le auto di privati della zona senza anno o km (non valutabili):
        da quale sito, quale dato manca, se il testo lo contiene. Solo conteggi + 15 esempi senza dati personali."""
        tag = os.environ.get("RINVIO_INVIA") or ""
        if not tag.startswith("incompleti"):
            return
        time.sleep(30)
        L = logging.getLogger("scovo")
        lav = None
        try:
            from ..pipeline.ciclo import inizia      # 3/11: segnato fatto solo a lavoro finito (prima: prima di farlo)
            lav = inizia(f"incompleti:{tag}")
            if not lav:
                return
            from collections import Counter as _C
            from ..core.dal_testo import anno, chilometri
            from ..zone import tutte
            with connect() as conn:
                conn.execute("SET statement_timeout = '600s'")
                rows = conn.execute("""SELECT id, source, year, mileage_km, fuel, title, left(description, 600) AS d,
                        version_raw, field_origin FROM listings
                    WHERE status='attivo' AND seller_type='privato' AND upper(province) = ANY(%s)
                      AND price_eur BETWEEN 1000 AND 30000 AND (year IS NULL OR mileage_km IS NULL OR fuel IS NULL)""",
                                    (list(tutte()),)).fetchall()
            c = _C()
            es = []
            for r in rows:
                manca = "+".join(k for k in ("year", "mileage_km", "fuel") if r[k] is None)
                testo = f"{r['version_raw'] or ''} {r['title'] or ''} {r['d'] or ''}"
                c[f"{r['source']}|{manca}"] += 1
                c[f"testo_ha_anno={bool(anno(testo))}"] += r["year"] is None
                c[f"testo_ha_km={bool(chilometri(testo))}"] += r["mileage_km"] is None
                c[f"descrizione_vuota={not (r['d'] or '').strip()}"] += 1
                fo = r["field_origin"] if isinstance(r["field_origin"], dict) else json.loads(r["field_origin"] or "{}")
                c[f"testo_letto_v2={'testo_letto_v2' in fo}"] += 1
                if len(es) < 15 and r["source"] == "subito":
                    es.append([r["id"], manca, (r["title"] or "")[:60], len(r["d"] or "")])
            L.warning("INCOMPLETI auto %d: %s", len(rows), json.dumps(dict(c.most_common(30))))
            L.warning("INCOMPLETI esempi subito: %s", json.dumps(es, ensure_ascii=False))
            lav.fatto()
        except Exception as e:
            L.warning("INCOMPLETI non riuscita: %s", str(e)[:300])
            if lav:
                lav.fallito(e)
    threading.Thread(target=diagnosi_incompleti, daemon=True, name="incompleti").start()

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
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
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
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
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
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
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
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
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
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
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
        from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
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
            from ..pipeline.ciclo import _due, _una_volta  # noqa: F401
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


def _ricalcolo_sito() -> bool:
    """3/11 fase 1: il ricalcolo pesante (ricalcolo.esegui + utilitarie) gira nel cron (collect "ricalcolo"), non nel
    sito (512 MB: andava fuori memoria, il sito ripartiva e rifaceva tutto). POLICY_RICALCOLO_SITO=1 lo riaccende qui.
    Cambia solo dove gira il lavoro, non i risultati."""
    return os.environ.get("POLICY_RICALCOLO_SITO", "0") == "1"


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
    try:
        from .. import regole_log as _rl             # 3/11 fase 1: impronta delle regole usate dal sito
        _rl.registra("web")
    except Exception:
        pass
    if os.environ.get("DATABASE_URL"):
        try:                                         # 3/11 sentinella: impronta delle regole + un thread leggero
            from .. import sentinella as _sent       # (fotografia del sito, controlli ogni ora, email degli allarmi)
            _sent.salva_impronta("web", in_sottofondo=True)
            _sent.avvia_thread_web()
        except Exception:
            pass
    if os.environ.get("DATABASE_URL") and os.environ.get("SCHEDA_TRATTATIVA", "1") == "1":
        try:                                  # 7/11: statistiche di ribassi e tempi di vendita per la scheda trattativa
            from . import trattativa as _tr
            _tr.avvia_statistiche(connect)
        except Exception:
            pass
    if os.environ.get("DATABASE_URL"):
        _migrazioni_in_sottofondo()           # 4/11: quelle rimandate da init_db (lucchetto occupato)
        _pulizia_disco()
        _tabelle_sito()                       # 4/11: contatori giornalieri dei filtri del sito (mai nelle richieste)
        _aggiorna_elenco(attendi=False)       # elenco auto pronto prima del primo visitatore
    if os.environ.get("DATABASE_URL") and os.environ.get("VISIBILI_SOTTOFONDO", "1") == "1":
        _aggiorna_visibili()
    if os.environ.get("DATABASE_URL") and os.environ.get("POLICY_RICALCOLO", "1") == "1":
        if _ricalcolo_sito():
            _ricalcolo_policy()
        else:
            logging.getLogger("policy").warning("RICALCOLO_SITO spento: il ricalcolo all'avvio lo fa il cron "
                                                "(POLICY_RICALCOLO_SITO=1 per rifarlo nel sito)")
    if os.environ.get("DATABASE_URL") and os.environ.get("TEMPO_REALE", "1") == "1":
        _tempo_reale()
    if os.environ.get("DATABASE_URL"):
        try:
            _lavoro_subito()
        except Exception as e:
            logging.getLogger("lavoro").warning("lavoro non avviato: %s", str(e)[:200])
    yield


# 4/11: tolta l'anteprima "prezzi −1.000 €" (numeri finti a chi aveva il link).


app = Starlette(routes=routes, lifespan=_lifespan,
                middleware=[Middleware(SecurityMiddleware), Middleware(GZipMiddleware, minimum_size=800)],
                exception_handlers={HTTPError: http_error, 500: server_error})
