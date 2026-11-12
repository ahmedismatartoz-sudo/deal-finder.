"""12/11: pagamenti e sicurezza (audit 5). Un test (o più) per ogni punto; tutti fallivano prima delle correzioni."""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from starlette.requests import Request

from dealfinder.web import abbonamento as ab
from dealfinder.web import app as A
from tests.test_abbonamento import FintoStripeDB, _R


# ---------------------------------------------------------------------------------------------- aiuti
def _req(path, body=None, method="POST", headers=None, cookies=None, path_params=None):
    h = [(b"authorization", b"Bearer x")] + [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        h.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    scope = {"type": "http", "method": method, "path": path, "query_string": b"", "headers": h,
             "client": ("1.2.3.4", 1), "path_params": path_params or {}}
    raw = json.dumps(body or {}).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request(scope, receive)


def _esegui(fn, req):
    try:
        r = fn(req)
        if asyncio.iscoroutine(r):
            r = asyncio.run(r)
    except A.HTTPError as e:
        return {"error": e.message}, e.status, None
    try:
        return json.loads(r.body), r.status_code, r
    except Exception:
        return None, r.status_code, r


class _Sostituisci:
    """Sostituisce attributi di moduli e li rimette alla fine (anche se il test fallisce)."""

    def __init__(self, *coppie):
        self.coppie, self.vecchi = coppie, []

    def __enter__(self):
        for obj, nome, val in self.coppie:
            self.vecchi.append((obj, nome, getattr(obj, nome)))
            setattr(obj, nome, val)
        return self

    def __exit__(self, *a):
        for obj, nome, val in reversed(self.vecchi):
            setattr(obj, nome, val)
        return False


class _Log(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.righe = []

    def emit(self, r):
        self.righe.append((r.levelno, r.getMessage()))


def _cattura(nome):
    h = _Log()
    lg = logging.getLogger(nome)
    lg.addHandler(h)
    return h, lambda: lg.removeHandler(h)


class DBStripe(FintoStripeDB):
    """FintoStripeDB + colonne della migrazione 049 (sessione di checkout aperta)."""

    def __init__(self):
        super().__init__()
        self.dealers[7].update(email="cliente@esempio.it", role="commerciante", checkout_session=None,
                               richiede_abbonamento=True, password_hash=None, paga_carta=False)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, p=()):
        s = " ".join(sql.split())
        d = self.dealers[7]
        if s.startswith("SELECT 1 FROM information_schema.columns"):
            return _R([{"x": 1}])
        if s.startswith("SELECT pg_advisory_xact_lock"):
            return _R([{"x": 1}])
        if s.startswith("SELECT id, email, stripe_customer, abbonamento_stato FROM dealers"):
            return _R([{k: d[k] for k in ("id", "email", "stripe_customer", "abbonamento_stato")}])
        if s.startswith("SELECT checkout_session FROM dealers"):
            return _R([{"checkout_session": d["checkout_session"]}] if d["checkout_session"] else [])
        if s.startswith("UPDATE dealers SET checkout_session"):
            d["checkout_session"] = p[0]
            return _R()
        if s.startswith("SELECT richiede_abbonamento, abbonamento_stato, abbonamento_tolleranza"):
            return _R([{"richiede_abbonamento": True, "abbonamento_stato": d["abbonamento_stato"],
                        "abbonamento_tolleranza": d["abbonamento_tolleranza"], "con_password": False,
                        "role": "commerciante", "paga_carta": False}])
        if s.startswith("SELECT abbonamento_stato FROM dealers"):
            return _R([{"abbonamento_stato": d["abbonamento_stato"]}])
        if s.startswith("SELECT 1 FROM abbonamenti WHERE dealer_id=%s AND stato='incomplete'"):
            return _R([{"x": 1}] if any(a["stato"] == "incomplete" for a in self.abb.values()) else [])
        return super().execute(sql, p)


SID = "cs_test_a1B2c3D4e5F6g7H8"


def _sessione(stato="complete", pagato="paid", sub_stato="active", dealer=7, piano="base"):
    lk = "scovo_pro_200_mese" if piano == "pro" else "scovo_base_100_mese"
    return {"id": SID, "status": stato, "payment_status": pagato, "client_reference_id": str(dealer),
            "customer": "cus_1", "created": 1000, "url": "https://checkout.stripe.com/c/pay/" + SID,
            "metadata": {"piano": piano},
            "subscription": {"id": "sub_1", "status": sub_stato, "customer": "cus_1",
                             "metadata": {"dealer_id": str(dealer)},
                             "items": {"data": [{"price": {"id": "p", "lookup_key": lk},
                                                 "current_period_end": 1900000000}]}}}


class StripeFinto:
    def __init__(self, sessione=None):
        self.sessione, self.post = sessione, []

    def leggi(self, path, params):
        if path.startswith("/checkout/sessions/"):
            return self.sessione
        return {"data": []}

    def crea(self, dealer, base, http=None, piano="base"):
        self.post.append(("crea", piano))
        return {"id": "cs_test_nuovaSessione123", "url": "https://checkout.stripe.com/nuova"}

    def scadi(self, sid, http=None):
        self.post.append(("scadi", sid))


def _con_stripe(db, st, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_finto")
    get_vero = ab._get
    return _Sostituisci((A, "connect", lambda: db),
                        (A, "current_user", lambda r, admin=False: {"id": 7, "role": "commerciante", "sid": "s"}),
                        (ab, "_get", lambda path, params: st.leggi(path, params) if st else get_vero(path, params)),
                        (ab, "crea_checkout", st.crea), (ab, "scadi_checkout", st.scadi),
                        (ab, "portale", lambda cu, base, http=None: "https://billing.stripe.com/portale"))


# ------------------------------------------------- 1. ALTA: verifica al ritorno da Stripe, niente doppio checkout
def test_ritorno_da_stripe_con_session_id(monkeypatch):
    inviati = []

    class H:
        @staticmethod
        def post(url, data=None, **k):
            inviati.append(data)

            class R:
                status_code = 200

                def json(self):
                    return {"id": "cs_test_x", "url": "https://checkout"}
            return R()
    ab._CACHE.update(base="price_b", coupon="SCOVO_PRIMO_MESE_50")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    s = ab.crea_checkout({"id": 3, "email": "a@b.it"}, "https://x", H, "base")
    assert s["id"] == "cs_test_x"
    assert inviati[-1]["success_url"] == "https://x/?session_id={CHECKOUT_SESSION_ID}#/abbonamento/ok"


def test_verifica_pagamento_senza_webhook(monkeypatch):
    db, st = DBStripe(), StripeFinto(_sessione())
    with _con_stripe(db, st, monkeypatch):
        out, code, _ = _esegui(A.abbonamento_verifica, _req("/api/abbonamento/verifica", {"session_id": SID}))
        assert code == 200 and out["accesso"] is True and out["stato"] == "active"
        assert db.dealers[7]["stripe_customer"] == "cus_1"
        # di nuovo (il telefono riprova; poi arriva il webhook): idempotente, nessun cambiamento
        out, code, _ = _esegui(A.abbonamento_verifica, _req("/api/abbonamento/verifica", {"session_id": SID}))
        assert code == 200 and out["accesso"] is True and out["esito"] == "doppione"
        ev = {"id": "evt_vero", "type": "checkout.session.completed", "created": 1003,
              "data": {"object": dict(_sessione(), subscription="sub_1")}}
        assert ab.applica(db, ev) == "pagato:7" and db.dealers[7]["abbonamento_stato"] == "active"


def test_verifica_sessione_di_un_altro_account(monkeypatch):
    db, st = DBStripe(), StripeFinto(_sessione(dealer=99))
    with _con_stripe(db, st, monkeypatch):
        out, code, _ = _esegui(A.abbonamento_verifica, _req("/api/abbonamento/verifica", {"session_id": SID}))
    assert code == 404 and db.dealers[7]["abbonamento_stato"] is None


def test_verifica_pagamento_non_ancora_concluso(monkeypatch):
    db, st = DBStripe(), StripeFinto(_sessione(stato="open", pagato="unpaid"))
    with _con_stripe(db, st, monkeypatch):
        out, code, _ = _esegui(A.abbonamento_verifica, _req("/api/abbonamento/verifica", {"session_id": SID}))
    assert code == 200 and out["accesso"] is False and out["in_corso"] is True


def test_secondo_checkout_riusa_la_sessione_aperta(monkeypatch):
    db, st = DBStripe(), StripeFinto(_sessione(stato="open", pagato="unpaid"))
    db.dealers[7]["checkout_session"] = SID
    with _con_stripe(db, st, monkeypatch):
        out, code, _ = _esegui(A.abbonamento_checkout, _req("/api/abbonamento/checkout", {"piano": "base"}))
    assert code == 200 and out["url"].endswith(SID) and out["ripreso"] and st.post == []      # nessuna sessione nuova


def test_secondo_checkout_dopo_pagamento_senza_webhook_va_al_portale(monkeypatch):
    # il cliente ha pagato, il webhook non è arrivato, stripe_customer è ancora vuoto: prima si apriva un 2° checkout
    db, st = DBStripe(), StripeFinto(_sessione())
    db.dealers[7]["checkout_session"] = SID
    with _con_stripe(db, st, monkeypatch):
        out, code, _ = _esegui(A.abbonamento_checkout, _req("/api/abbonamento/checkout", {"piano": "base"}))
    assert out.get("gia_abbonato") and out.get("portale") and st.post == []
    assert db.dealers[7]["abbonamento_stato"] == "active"


def test_cambio_piano_chiude_la_sessione_vecchia(monkeypatch):
    db, st = DBStripe(), StripeFinto(_sessione(stato="open", pagato="unpaid", piano="base"))
    db.dealers[7]["checkout_session"] = SID
    with _con_stripe(db, st, monkeypatch):
        out, code, _ = _esegui(A.abbonamento_checkout, _req("/api/abbonamento/checkout", {"piano": "max"}))
    assert code == 200 and st.post == [("scadi", SID), ("crea", "pro")]
    assert db.dealers[7]["checkout_session"] == "cs_test_nuovaSessione123"


def test_sessione_sconosciuta_a_stripe_non_blocca_il_checkout(monkeypatch):
    db, st = DBStripe(), StripeFinto()
    db.dealers[7]["checkout_session"] = SID

    def leggi(path, params):
        raise RuntimeError("stripe /checkout/sessions/x -> 404 resource_missing")
    st.leggi = leggi
    with _con_stripe(db, st, monkeypatch):
        out, code, _ = _esegui(A.abbonamento_checkout, _req("/api/abbonamento/checkout", {"piano": "base"}))
    assert code == 200 and st.post == [("crea", "base")]


def test_abbonamento_incompleto_blocca_il_checkout():
    db = FintoStripeDB()
    leggi = lambda path, params: {"data": [{"id": "sub_9", "status": "incomplete"}]}    # noqa: E731
    assert ab.abbonamento_in_corso(db, {"id": 7, "abbonamento_stato": None, "stripe_customer": "cus_1"}, leggi)


def test_frontend_verifica_e_niente_paywall_durante_la_verifica():
    js = open(os.path.join(os.path.dirname(A.__file__), "static", "scovo", "app.js"), encoding="utf-8").read()
    assert '"/api/abbonamento/verifica"' in js and 'get("session_id")' in js
    assert 'res.status === 402 && !(S.pagaStato && S.pagaStato.fase === "verifica")' in js
    # il ritorno da Stripe non dice più "Abbonamento attivo" senza aver verificato
    i = js.index('if (r.route === "abbonamento")')
    assert "verificaPagamento(sid)" in js[i:i + 900]


# ------------------------------------------------------------------- 2. ALTA: tolleranza anche su /vai
def test_vai_annuncio_in_tolleranza():
    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, p=()):
            s = " ".join(sql.split())
            if s.startswith("SELECT permessi FROM dealers"):
                return _R([{"permessi": ["auto"]}])
            if s.startswith("SELECT subscription_id, stato") or s.startswith("SELECT 1 FROM abbonamenti"):
                return _R([])
            if s.startswith("SELECT richiede_abbonamento"):
                riga = {"richiede_abbonamento": True, "abbonamento_stato": "past_due", "con_password": False,
                        "role": "commerciante", "paga_carta": False}
                if "abbonamento_tolleranza" in s:            # come il database: solo le colonne chieste
                    riga["abbonamento_tolleranza"] = datetime.now(timezone.utc) + timedelta(days=2)
                return _R([riga])
            raise AssertionError(s)
    with _Sostituisci((A, "connect", lambda: DB()), (A, "read_token", lambda t: {"id": 7, "sid": "s"}),
                      (A, "_utente_del_token", lambda d, admin=False: {"id": 7, "sid": "s", "role": "commerciante"}),
                      (A, "_apri_contatto", lambda req, user: {"url": "https://www.subito.it/annuncio"})):
        _, code, r = _esegui(A.vai_annuncio, _req("/api/affari/5/vai", method="GET", cookies={"scovo_t": "tok"},
                                                  path_params={"id": 5}))
    assert code == 302 and r.headers["location"] == "https://www.subito.it/annuncio"


# --------------------------------------------------------- 3. ALTA: esiti di webhook ed email a WARNING
def test_esito_webhook_a_warning_senza_email():
    db = DBStripe()
    ev = {"id": "evt_log", "type": "checkout.session.completed", "created": 100,
          "data": {"object": {"client_reference_id": "7", "customer": "cus_1", "subscription": "sub_1",
                              "payment_status": "paid", "customer_details": {"email": "cliente@esempio.it"}}}}
    raw = json.dumps(ev).encode()
    scope = {"type": "http", "method": "POST", "path": "/api/stripe/webhook", "query_string": b"", "headers": []}

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    h, togli = _cattura("web")
    try:
        with _Sostituisci((A, "connect", lambda: db), (ab, "firma_valida", lambda p, s: True)):
            _, code, _ = _esegui(A.stripe_webhook, Request(scope, receive))
    finally:
        togli()
    assert code == 200
    righe = [m for lv, m in h.righe if lv >= logging.WARNING and "STRIPE_WEBHOOK" in m]
    assert righe and "evt_log" in righe[0] and "checkout.session.completed" in righe[0] and "pagato:7" in righe[0]
    assert not any("@" in m for _, m in h.righe)


def test_esito_email_appuntamento_a_warning_senza_indirizzi():
    import sys
    import types
    vero = sys.modules.get("httpx")
    gia_bl = "dealfinder.marketing.brevo_liste" in sys.modules
    httpx = types.ModuleType("httpx")
    httpx.Client = object
    sys.modules["httpx"] = httpx
    from dealfinder.marketing import brevo_liste

    class Risp:
        status_code, text = 201, "{}"
    httpx.post = lambda *a, **k: Risp()
    h, togli = _cattura("scovo")
    try:
        with _Sostituisci((brevo_liste, "_chiave", lambda: "finta")):
            assert A._avvisa_appuntamento("testo", "oggetto", None) is True
            Risp.status_code, Risp.text = 400, '{"message":"invalid email mario.rossi@gmail.com"}'
            assert A._avvisa_appuntamento("testo", "oggetto", None) is False
    finally:
        togli()
        if not gia_bl:
            sys.modules.pop("dealfinder.marketing.brevo_liste", None)
        if vero is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = vero
    ok = [(lv, m) for lv, m in h.righe if "email esito=ok" in m]
    assert ok and ok[0][0] == logging.WARNING
    assert not any("@" in m for _, m in h.righe)


# ------------------------------------------------- 4. ALTA: appuntamenti limitati e senza consumare posti
class DBApp:
    def __init__(self, accesso=True):
        self.app, self.q, self.accesso = [], [], accesso

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def execute(self, sql, p=()):
        s = " ".join(sql.split())
        self.q.append(s)
        if s.startswith("SELECT permessi FROM dealers"):
            return _R([{"permessi": ["auto"]}])
        if s.startswith("SELECT subscription_id, stato"):
            return _R([])
        if s.startswith("SELECT richiede_abbonamento"):
            return _R([{"richiede_abbonamento": True, "abbonamento_stato": "active" if self.accesso else None,
                        "abbonamento_tolleranza": None, "con_password": False, "role": "commerciante",
                        "paga_carta": False}])
        if s.startswith("SELECT dati FROM aziende_abbonati"):
            return _R([{"dati": {"nome_azienda": "Auto Srl", "indirizzo": "Via 1", "comune": "Milano",
                                 "provincia": "MI", "referente": "Mario", "cellulare": "3330000000"}}])
        if s.startswith("SELECT id, url, title"):
            return _R([{"id": p[0], "url": "https://x", "title": "Panda", "description": "", "price_eur": 5000}])
        if s.startswith("SELECT id FROM appuntamenti WHERE dealer_id"):
            return _R([])
        if s.startswith("SELECT count(*) AS n FROM appuntamenti"):
            return _R([{"n": len(self.app)}])
        if s.startswith("INSERT INTO appuntamenti"):
            self.app.append(p[1])
            return _R([{"id": len(self.app)}])
        return _R()


def _appuntamento(db, lid):
    import datetime as d
    from zoneinfo import ZoneInfo
    roma = ZoneInfo("Europe/Rome")

    class T:
        def __init__(self, target, args, daemon):
            pass

        def start(self):
            pass
    body = {"proposte": [{"giorno": "2026-11-05", "ora": "10:00"}]}
    with _Sostituisci((A, "connect", lambda: db),
                      (A, "current_user", lambda r, admin=False: {"id": 7, "role": "commerciante", "sid": "s"}),
                      (A.threading, "Thread", T), (A, "_ora_roma", lambda: d.datetime(2026, 11, 4, 10, 0, tzinfo=roma))):
        return _esegui(A.appuntamento, _req(f"/api/affari/{lid}/appuntamento", body, path_params={"id": lid}))


def test_appuntamento_senza_accesso_alle_auto():
    db = DBApp(accesso=False)
    out, code, _ = _appuntamento(db, 11)
    assert code == 402 and db.app == []


def test_appuntamenti_massimo_5_al_giorno_e_nessun_posto_consumato():
    db = DBApp()
    for lid in range(1, 6):
        out, code, _ = _appuntamento(db, lid)
        assert code == 200, out
    out, code, _ = _appuntamento(db, 6)
    assert code == 429 and len(db.app) == 5
    assert not any(q.startswith("INSERT INTO listing_opens") for q in db.q)      # la richiesta non occupa posti


def test_richiesta_in_corso_resta_visibile_a_chi_la_fa():
    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, p=()):
            s = " ".join(sql.split())
            if s.startswith("SELECT DISTINCT listing_id FROM listing_opens"):
                return _R([])
            if s.startswith("SELECT DISTINCT listing_id FROM appuntamenti"):
                return _R([{"listing_id": 2}])
            raise AssertionError(s)
    from dealfinder.config import settings
    el = {"t": time.time(), "items": [{"id": 1}, {"id": 2}], "opens": {1: 0, 2: settings.max_dealer_opens}, "busy": False}
    with _Sostituisci((A, "connect", lambda: DB()), (A, "_elenco", el)):
        ids = [it["id"] for it in A._elenco_per(7)]
    assert ids == [1, 2]


def test_conferma_appuntamento_conta_come_apertura():
    q = []

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def commit(self):
            pass

        def execute(self, sql, p=()):
            s = " ".join(sql.split())
            q.append(s)
            if s.startswith("UPDATE appuntamenti SET stato"):
                return _R([{"dealer_id": 7, "listing_id": 11}])
            return _R()
    with _Sostituisci((A, "connect", lambda: DB()), (A, "current_user", lambda r, admin=False: {"id": 1, "role": "admin"})):
        _, code, _ = _esegui(A.admin_appuntamento_stato, _req("/api/admin/appuntamenti/3", {"stato": "annullato"},
                                                               path_params={"id": 3}))
        assert code == 200 and not any(x.startswith("INSERT INTO listing_opens") for x in q)
        _, code, _ = _esegui(A.admin_appuntamento_stato, _req("/api/admin/appuntamenti/3", {"stato": "confermato"},
                                                               path_params={"id": 3}))
        assert code == 200 and any(x.startswith("INSERT INTO listing_opens") for x in q)
        _, code, _ = _esegui(A.admin_appuntamento_stato, _req("/api/admin/appuntamenti/3", {"stato": "boh"},
                                                               path_params={"id": 3}))
        assert code == 400


# --------------------------------------------------------- 5. MEDIA: niente telefoni nelle risposte API
def test_vecchie_api_senza_telefono():
    from types import SimpleNamespace as NS
    from dealfinder.web import cards
    l = NS(title="Panda chiamare 333 123 4567", description="Ottima. Tel 333-1234567 oppure mario@x.it", source="subito",
           make="fiat", model="panda", version_raw=None, year=2015, mileage_km=90000, fuel="benzina", gearbox=None,
           power_kw=None, city="Milano", province="MI", url="https://x", price_eur=5000, damage_class="nessuno",
           missing_fields=[], seller_type="privato", price_flags=[])
    m = NS(status="opportunita", score=1, net_margin=2000, parts_cost=0, threshold=1500, purchase=5000, breakdown={},
           contingency=0, vat_on_margin=0, notes=[])
    with _Sostituisci((cards, "row_to_listing", lambda r, ph: l), (cards, "compute_margin", lambda *a: m),
                      (cards, "_risk_label", lambda *a: "basso"), (cards.report, "damage_labels", lambda l: [])):
        c = cards.build({"id": 1}, {}, [], None, full=True)
    assert "333" not in c["title"] and "333" not in c["description"] and "@" not in c["description"]
    assert "[numero col bottone]" in c["description"]


def test_attivita_senza_telefono_nel_titolo():
    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, p=()):
            return _R([{"id": 1, "title": "Golf tel. 347 1234567", "at": "2026-11-12", "status": "contattato"}])
    with _Sostituisci((A, "connect", lambda: DB()), (A, "richiede", lambda r, p: {"id": 7, "role": "commerciante"})):
        out, code, _ = _esegui(A.my_activity, _req("/api/activity", method="GET"))
    assert code == 200 and "347" not in out["items"][0]["title"]


def test_nome_di_ripiego_senza_telefono():
    from dealfinder.web import scovo
    assert "333" not in scovo.nome({"title": "Vendo auto 3331234567"})


# ----------------------------------------------------------------- 6. MEDIA: #/paga
def test_paga_dopo_si_cancella_e_non_si_usa_se_gia_abbonati():
    js = open(os.path.join(os.path.dirname(A.__file__), "static", "scovo", "app.js"), encoding="utf-8").read()
    i = js.index('if (r.route === "paga")')
    blocco = js[i:i + 900]
    # già dentro: niente paga_dopo in memoria, voce sostituita, pagina disegnata prima di Stripe
    dentro = blocco[blocco.index("if (S.token)"):blocco.index('store.set("paga_dopo"')]
    assert "store.set" not in dentro and "replaceState" in dentro and "render(" in dentro and "pagaSeServe" in dentro
    assert "pagaDopoLeggi()" in js and 'store.del("paga_dopo")' in js and "ABB_IN_CORSO.includes" in js
    # checkout fallito: messaggio che resta (vista dedicata), non solo un avviso che sparisce
    assert "function viewPagamento" in js and "S.pagaStato = e.status === 409" in js


# ---------------------------------------------------- 7. MEDIA: permesso appuntamenti coerente
def test_permesso_appuntamenti_max_past_due_oltre_tolleranza():
    db = DBStripe()
    db.dealers[7]["permessi"] = ["auto"]
    ab.applica(db, {"id": "e1", "type": "customer.subscription.updated", "created": 100,
                    "data": {"object": {"id": "sub_1", "customer": "cus_1", "status": "past_due",
                                        "metadata": {"dealer_id": "7"},
                                        "items": {"data": [{"price": {"lookup_key": "scovo_pro_200_mese"}}]}}}})
    _orig = db.execute

    def ex(sql, p=()):
        if " ".join(sql.split()).startswith("SELECT permessi FROM dealers"):
            return _R([{"permessi": list(db.dealers[7]["permessi"])}])
        return _orig(sql, p)
    db.execute = ex
    assert "appuntamenti" in A.permessi_di(db, {"id": 7, "role": "commerciante"})         # in tolleranza
    db.abb["sub_1"]["past_due_dal"] = db.now - timedelta(days=30)
    assert "appuntamenti" not in A.permessi_di(db, {"id": 7, "role": "commerciante"})     # da settimane: no
    db.dealers[7]["permessi"] = ["auto", "appuntamenti"]                                   # dato a mano: resta
    assert "appuntamenti" in A.permessi_di(db, {"id": 7, "role": "commerciante"})


def test_migrazione_049_appuntamenti_automatici():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "migrations",
                     "049_pagamenti_sicurezza.sql")
    sql = open(p, encoding="utf-8").read()
    assert "WHERE appuntamenti_da_stripe" in sql and "a.piano IN ('pro', 'max')" in sql
    assert "checkout_session" in sql and "link_accesso_usati" in sql


# ---------------------------------------------------- 9. MEDIA: sessione 90 giorni, link monouso di 1 ora
def test_token_90_giorni_rinnovato_con_l_uso():
    from dealfinder.web import auth
    ora = time.time()
    t = auth.make_token(7, "commerciante", "s1", ora)
    d = auth.read_token(t)
    assert 89 * 86400 < d["exp"] - ora <= 90 * 86400
    assert auth.rinnovo_token(d, ora + 3600) is None                       # appena fatto: niente
    n = auth.rinnovo_token(d, ora + 2 * 86400)                             # dopo due giorni: nuovo, stessa sessione
    assert n and auth.read_token(n)["sid"] == "s1" and auth.read_token(n)["exp"] > d["exp"]
    vecchio = dict(d, exp=int(ora) + 3650 * 86400)                         # token di prima (10 anni)
    assert auth.read_token(auth.rinnovo_token(vecchio, ora))["exp"] <= ora + 90 * 86400 + 1


def test_rinnovo_nell_intestazione():
    from dealfinder.web import auth, sicurezza_http
    t = auth.make_token(7, "commerciante", "s1", time.time() - 5 * 86400)
    scope = {"path": "/api/me", "headers": [(b"authorization", b"Bearer " + t.encode())]}
    assert sicurezza_http.token_rinnovato(scope)
    assert sicurezza_http.token_rinnovato(dict(scope, path="/static/x.js")) is None


def test_sessione_chiusa_dopo_90_giorni_senza_uso():
    from dealfinder.web import sessions
    eseguite = []

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def commit(self):
            pass

        def execute(self, sql, p=()):
            s = " ".join(sql.split())
            eseguite.append((s, p))
            if s.startswith("UPDATE dealer_sessions s SET last_seen_at"):
                assert "p.last_seen_at < now() - make_interval(days => %s)" in s and p[1] == 90
                return _R([{"dealer_id": 7, "revoked_why": None, "open": True, "active": True, "role": "commerciante",
                            "scaduto": False, "inattiva": True}])
            return _R()
    sessions._cache.pop("s_vecchia", None)
    with _Sostituisci((sessions, "connect", lambda: DB())):
        assert sessions.check("s_vecchia", 7) == (False, "inattiva")
    sessions._cache.pop("s_vecchia", None)
    assert any(s.startswith("UPDATE dealer_sessions SET revoked_at=now(), revoked_why='inattiva'") for s, _ in eseguite)


def test_link_di_accesso_monouso_e_di_un_ora():
    from dealfinder.web import auth
    c = auth.make_link_code(7, "scrypt$a$b")
    assert auth.parse_link_code(c)[1] - time.time() <= 3600 + 1
    usati = set()

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def commit(self):
            pass

        def execute(self, sql, p=()):
            s = " ".join(sql.split())
            if s.startswith("SELECT * FROM dealers"):
                return _R([{"id": 7, "password_hash": "scrypt$a$b", "role": "commerciante", "name": "M", "email": "m@x.it"}])
            if s.startswith("SELECT 1 FROM information_schema.columns"):
                return _R([{"x": 1}])
            if s.startswith("INSERT INTO link_accesso_usati"):
                if p[0] in usati:
                    return _R()
                usati.add(p[0])
                return _R([{"impronta": p[0]}])
            raise AssertionError(s)
    with _Sostituisci((A, "connect", lambda: DB()), (A, "start_session", lambda r, u, m: {"token": "t", "user": {}})):
        out, code, _ = _esegui(A.auth_scambio, _req("/api/auth/scambio", {"code": c}))
        assert code == 200
        out, code, _ = _esegui(A.auth_scambio, _req("/api/auth/scambio", {"code": c}))
        assert code == 401 and "già stato usato" in out["error"]
    assert all(c not in u for u in usati)                                  # nel database solo l'impronta


# ------------------------------------------------------- 10. MEDIA: limite di 12 MB anche senza Content-Length
def _asgi(app, method, chunks, content_length=None):
    from dealfinder.web import sicurezza_http
    msgs = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]
    letti, inviati = [0], []

    async def receive():
        letti[0] += 1
        return msgs.pop(0) if msgs else {"type": "http.disconnect"}

    async def send(m):
        inviati.append(m)
    h = [] if content_length is None else [(b"content-length", str(content_length).encode())]
    scope = {"type": "http", "method": method, "path": "/api/trascrivi", "headers": h}

    async def go():
        rec = await sicurezza_http.limita_corpo(scope, receive, send, 100)
        if rec is not None:
            await app(scope, rec, send)
    asyncio.run(go())
    return inviati, letti[0]


def test_limite_corpo_a_pezzi():
    ricevuto = []

    async def app(scope, receive, send):
        b = b""
        while True:
            m = await receive()
            b += m.get("body", b"")
            if not m.get("more_body"):
                break
        ricevuto.append(b)
        await send({"type": "http.response.start", "status": 200, "headers": []})
    # senza Content-Length, oltre il limite: 413 e lettura fermata (non si leggono tutti i 50 pezzi)
    inviati, letti = _asgi(app, "POST", [b"x" * 30] * 50)
    assert inviati[0]["status"] == 413 and letti < 10 and ricevuto == []
    # sotto il limite: il corpo arriva intero all'app
    inviati, _ = _asgi(app, "POST", [b"ab", b"cd", b"ef"])
    assert inviati[0]["status"] == 200 and ricevuto == [b"abcdef"]
    # Content-Length dichiarato oltre il limite: 413 subito
    inviati, _ = _asgi(app, "POST", [b"x"], content_length=1000)
    assert inviati[0]["status"] == 413


def test_limite_corpo_middleware_intero():
    from starlette.testclient import TestClient

    def gen():
        for _ in range(13):
            yield b"x" * (1024 * 1024)
    c = TestClient(A.app, raise_server_exceptions=False)          # senza "with": l'avvio del sito non parte
    r = c.post("/api/trascrivi", content=gen(), headers={"authorization": "Bearer x"})
    assert r.status_code == 413


# ------------------------------------------------------------- 11. BASSA: monkeypatch in run_tests.py
def test_run_tests_fornisce_monkeypatch():
    import run_tests
    visto = {}

    def prova(monkeypatch):
        monkeypatch.setenv("PROVA_MP_12_11", "1")
        monkeypatch.setattr(run_tests, "QUI", "/altrove")
        visto["env"], visto["qui"] = os.environ.get("PROVA_MP_12_11"), run_tests.QUI
    prima = run_tests.QUI
    run_tests._chiama(prova)
    assert visto == {"env": "1", "qui": "/altrove"}
    assert "PROVA_MP_12_11" not in os.environ and run_tests.QUI == prima


# ------------------------------------------------------- 12. BASSA: ribasso a tempo con la data vuota
def test_ribasso_spento_se_la_data_e_vuota(monkeypatch):
    from dealfinder.web import scovo
    monkeypatch.setenv("SITO_RIBASSO_FINO", "")
    assert scovo._ribasso_fasce(datetime(2026, 11, 12, tzinfo=timezone.utc)) == []
    assert scovo.ribasso_per(8000) == 0
    monkeypatch.setenv("SITO_RIBASSO_FINO", "2099-01-01T00:00:00+01:00")
    assert scovo.ribasso_per(8000, datetime(2026, 11, 12, tzinfo=timezone.utc)) > 0      # con la data: funziona


# ----------------------------------------------- 13. dal rapporto 1: candidate fino a 150.000 km
def test_scelta_candidate_150000_km(monkeypatch):
    import sys
    if "dealfinder.pipeline.benzina_mi" not in sys.modules:
        # caricato solo per questo test: poi si toglie, così i test dopo trovano lo stesso stato di prima (run_tests
        # controlla benzina_mi.PROVINCE_100 solo se il modulo è già caricato)
        monkeypatch.setitem(sys.modules, "dealfinder.pipeline.benzina_mi", None)
        del sys.modules["dealfinder.pipeline.benzina_mi"]
    from dealfinder.pipeline import benzina_mi
    monkeypatch.delenv("BENZ_KM_MAX", raising=False)
    assert benzina_mi.km_max_scelta() == 150000
    monkeypatch.setenv("BENZ_KM_MAX", "180000")                     # una variabile vecchia su Render non supera la regola
    assert benzina_mi.km_max_scelta() == 150000
    monkeypatch.setenv("BENZ_KM_MAX", "120000")
    assert benzina_mi.km_max_scelta() == 120000
    src = open(benzina_mi.__file__, encoding="utf-8").read()
    assert '"BENZ_KM_MAX", "180000"' not in src
