"""4/11: appuntamenti in ora italiana, niente orari passati, niente richieste doppie, esito dell'email controllato,
niente DDL nella richiesta."""
import asyncio
import datetime as d
import json
from zoneinfo import ZoneInfo

from starlette.requests import Request

from dealfinder.web import app as A

ROMA = ZoneInfo("Europe/Rome")


def _req(lid=11, body=None):
    scope = {"type": "http", "method": "POST", "path": f"/api/affari/{lid}/appuntamento", "query_string": b"",
             "headers": [(b"authorization", b"Bearer x")], "client": ("1.2.3.4", 1), "path_params": {"id": lid}}
    raw = json.dumps(body or {}).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request(scope, receive)


def test_proposte_ora_italiana_e_orari_passati():
    # 4/11 00:30 a Roma (23:30 UTC del 3/11): "oggi" è il 4, e oggi+14 (18/11) è ancora valido
    adesso = d.datetime(2026, 11, 4, 0, 30, tzinfo=ROMA)
    p, sc = A._proposte_valide([{"giorno": "2026-11-18", "ora": "10:00"}], adesso)
    assert p == [(d.date(2026, 11, 18), "10:00")] and sc == []
    # oggi alle 15:00: le 9:00 e le 15:30 (meno di un'ora) sono scartate e segnalate, le 17:00 vanno bene
    adesso = d.datetime(2026, 11, 4, 15, 0, tzinfo=ROMA)
    p, sc = A._proposte_valide([{"giorno": "2026-11-04", "ora": "9:00"}, {"giorno": "2026-11-04", "ora": "15:30"},
                                {"giorno": "2026-11-04", "ora": "17:00"}, {"giorno": "2026-11-04", "ora": "17:00"},
                                {"giorno": "2026-11-03", "ora": "10:00"}, {"giorno": "boh", "ora": "10:00"},
                                {"giorno": "2026-11-05", "ora": "3:00"}], adesso)
    assert p == [(d.date(2026, 11, 4), "17:00")]
    motivi = [x["motivo"] for x in sc]
    assert motivi.count("orario già passato o troppo vicino") == 2
    assert "giorno fuori dalle prossime 2 settimane" in motivi and "giorno non valido" in motivi
    assert "orario non valido" in motivi


class _R:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FintoDB:
    def __init__(self):
        self.app, self.q = [], []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def execute(self, sql, p=()):
        s = " ".join(sql.split())
        self.q.append(s)
        assert not s.upper().startswith(("CREATE", "ALTER")), "niente DDL nella richiesta"
        if s.startswith("SELECT permessi FROM dealers"):
            return _R([{"permessi": ["auto", "appuntamenti"]}])
        if s.startswith("SELECT dati FROM aziende_abbonati"):
            return _R([{"dati": {"nome_azienda": "Auto Srl", "indirizzo": "Via 1", "comune": "Milano",
                                 "provincia": "MI", "referente": "Mario", "cellulare": "3330000000"}}])
        if s.startswith("SELECT id, url, title"):
            return _R([{"id": p[0], "url": "https://x", "title": "Panda", "description": "", "price_eur": 5000}])
        if s.startswith("SELECT id FROM appuntamenti WHERE dealer_id"):
            m = [a for a in self.app if a["dealer_id"] == p[0] and a["listing_id"] == p[1] and a["impronta"] == p[2]]
            return _R(m[-1:])
        if s.startswith("SELECT count(*) AS n FROM appuntamenti"):
            return _R([{"n": len([a for a in self.app if a["dealer_id"] == p[0]])}])
        if s.startswith("INSERT INTO appuntamenti"):
            self.app.append({"id": len(self.app) + 1, "dealer_id": p[0], "listing_id": p[1], "impronta": p[7]})
            return _R([{"id": len(self.app)}])
        return _R()


def _con(db, fn, *a):
    """Esegue l'endpoint con database finto, utente commerciante 7 e senza thread per l'email."""
    vecchi = (A.connect, A.current_user, A.threading.Thread, A._ora_roma)
    mandate = []

    class T:
        def __init__(self, target, args, daemon):
            mandate.append(args)

        def start(self):
            pass
    A.connect = lambda: db
    A.current_user = lambda r, admin=False: {"id": 7, "role": "commerciante", "sid": "s"}
    A.threading.Thread = T
    A._ora_roma = lambda: d.datetime(2026, 11, 4, 10, 0, tzinfo=ROMA)
    try:
        r = asyncio.run(fn(*a))
        return json.loads(r.body), r.status_code, mandate
    finally:
        A.connect, A.current_user, A.threading.Thread, A._ora_roma = vecchi


def test_richiesta_doppia_una_sola_email():
    db = FintoDB()
    body = {"proposte": [{"giorno": "2026-11-05", "ora": "10:00"}], "nota": "dopo pranzo"}
    out, st, mail = _con(db, A.appuntamento, _req(11, body))
    assert st == 200 and out["id"] == 1 and len(mail) == 1
    out, st, mail = _con(db, A.appuntamento, _req(11, body))           # stesso clic due volte
    assert st == 200 and out["id"] == 1 and out.get("gia_inviata") and mail == []
    assert len(db.app) == 1
    assert any("pg_advisory_xact_lock" in q for q in db.q)
    out, st, mail = _con(db, A.appuntamento, _req(12, body))           # altra auto: nuova richiesta
    assert out["id"] == 2 and len(mail) == 1


def test_tutte_le_date_passate_400_con_motivo():
    db = FintoDB()
    out, st, _ = _con(db, A.appuntamento, _req(11, {"proposte": [{"giorno": "2026-11-04", "ora": "9:00"}]}))
    assert st == 400 and out["scartate"][0]["motivo"].startswith("orario già passato")
    assert db.app == []


def test_esito_email_brevo_controllato():
    import sys
    import types
    db = FintoDB()
    vero = sys.modules.get("httpx")
    gia_bl = "dealfinder.marketing.brevo_liste" in sys.modules
    httpx = types.ModuleType("httpx")                   # httpx finto: nessuna chiamata vera a Brevo
    httpx.Client = object
    sys.modules["httpx"] = httpx
    from dealfinder.marketing import brevo_liste
    vecchi = (A.connect, brevo_liste._chiave)

    class Risp:
        status_code, text = 401, '{"message":"Key not found"}'
    httpx.post = lambda *a, **k: Risp()
    A.connect = lambda: db
    brevo_liste._chiave = lambda: "finta"
    try:
        assert A._avvisa_appuntamento("testo", "oggetto", 5) is False
        upd = [q for q in db.q if q.startswith("UPDATE appuntamenti SET email_ok")]
        assert upd
        Risp.status_code = 201
        assert A._avvisa_appuntamento("testo", "oggetto", 5) is True
    finally:
        A.connect, brevo_liste._chiave = vecchi
        if not gia_bl:                                  # non lasciare il modulo legato all'httpx finto
            sys.modules.pop("dealfinder.marketing.brevo_liste", None)
        if vero is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = vero
