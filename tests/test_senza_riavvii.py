"""12/11 (cliente: "odio che per ogni azione il sito si debba riavviare"): impostazioni del sito nel database con
cache, elenco e statistiche della scheda salvati e riletti all'avvio, /health vero, meno memoria nel sito.

I test con "pg" usano un Postgres VERO temporaneo (tests/pg_vero.py); se initdb non c'è non fanno nulla."""
import json
import os
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

from dealfinder.web import elenco_salvato as ES
from dealfinder.web import impostazioni as IMP
from dealfinder.web import scovo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pg_vero  # noqa: E402

QUI = Path(__file__).resolve().parents[1]
PRIMA = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)      # ribasso del 10/11 acceso
DOPO = datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc)       # spento da solo (fino 16:55 ora italiana)


class _Lettore:
    """Al posto del database: conta le letture."""
    def __init__(self, dati):
        self.dati, self.letture = dati, 0

    def __call__(self):
        self.letture += 1
        if isinstance(self.dati, Exception):
            raise self.dati
        return json.loads(json.dumps(self.dati))


def _con(dati):
    let = _Lettore(dati)
    IMP.usa_lettore(let)
    return let


def _fine():
    IMP.usa_lettore(None)


# ------------------------------------------------------------------------------------------------ ribassi
def test_ribasso_dal_database_come_dal_codice():
    """Stesse fasce e stessi prezzi di oggi (variabili SITO_RIBASSO/SITO_RIBASSO_FINO), ma dal database."""
    vecchie = {k: os.environ.get(k) for k in ("SITO_RIBASSO", "SITO_RIBASSO_FINO")}
    try:
        os.environ["SITO_RIBASSO"] = scovo.RIBASSO_SCHEMA
        os.environ["SITO_RIBASSO_FINO"] = "2026-11-10T16:55:00+01:00"
        prezzi = [1500, 1999, 2000, 5999, 6000, 9999, 10000, 15000, 20000, 29999, 30000, 80000]
        auto = [{"id": i, "prezzo": p, "pv": {"margine": 2000, "M": p + 3000}} for i, p in enumerate(prezzi)]
        dal_codice = {ora: [scovo.prezzo_sito(dict(a), ora) for a in auto] for ora in (PRIMA, DOPO)}
        _con({"ribasso": {"attivo": True, "schema": scovo.RIBASSO_SCHEMA, "fino": "2026-11-10T16:55:00+01:00"}})
        os.environ["SITO_RIBASSO"] = "0:9999"               # il database vince sulle variabili
        for ora in (PRIMA, DOPO):
            assert [scovo.prezzo_sito(dict(a), ora) for a in auto] == dal_codice[ora]
        assert scovo.prezzo_sito(dict(auto[6]), PRIMA)["prezzo"] == 8500
        assert scovo.prezzo_sito(dict(auto[6]), DOPO)["prezzo"] == 10000
    finally:
        _fine()
        for k, v in vecchie.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_ribasso_spento_e_senza_scadenza():
    try:
        _con({"ribasso": {"attivo": False, "schema": scovo.RIBASSO_SCHEMA, "fino": None}})
        assert scovo.ribasso_per(12000, PRIMA) == 0
        _con({"ribasso": {"attivo": True, "schema": "0:500", "fino": None}})
        assert scovo.ribasso_per(12000, DOPO) == 500
        _con({"ribasso": {"attivo": True, "schema": "0:500", "fino": "non è una data"}})
        assert scovo.ribasso_per(12000, PRIMA) == 0                 # data sbagliata: spento, mai per sempre
        _con({"ribasso": {"attivo": True, "schema": "0:9000", "fino": None}})
        assert scovo.ribasso_per(12000, PRIMA) == 3000              # al massimo 3.000 € come prima
    finally:
        _fine()


def test_valida_ribasso():
    v = IMP.valida_ribasso({"attivo": True, "schema": " 6000:1000, 2000:700 ", "fino": "2026-11-12T10:00:00+00:00"})
    assert v == {"attivo": True, "schema": "2000:700,6000:1000", "fino": "2026-11-12T10:00:00+00:00"}
    for male in ({"attivo": True, "schema": ""}, {"schema": "2000-700"}, {"schema": "2000:700", "fino": "domani"}):
        try:
            IMP.valida_ribasso(male)
            assert False, male
        except ValueError:
            pass


# ------------------------------------------------------------------------------------------------ in cima
FIAT = {"id": 77, "marca": "Fiat", "modello": "500", "nome": "Fiat 500", "km": 68000, "prezzo": 7900,
        "pv": {"margine": 2100}, "valutazione": {"margine": 2000}}


def test_in_cima_dal_database_come_dal_codice():
    altra = dict(FIAT, id=78, km=68001)
    # 13/11 10:52: IN_CIMA del codice ha di nuovo la Fiat 500 rossa a 6.900 €
    voce = {"marca": "fiat", "km": 68000, "prezzo": 7900, "mostra": 6900}
    assert scovo.IN_CIMA == [voce]
    dal_codice = [scovo.in_cima(dict(FIAT)), scovo.in_cima(dict(altra))]
    assert dal_codice[0]["prezzo"] == 6900 and dal_codice[0]["vetrina"] == -100 and dal_codice[0]["pv"]["margine"] == 3100
    seme = [dict(voce, nota="Fiat 500 rossa")]                                 # come la migrazione 048
    try:
        _con({"in_cima": seme})
        assert [scovo.in_cima(dict(FIAT)), scovo.in_cima(dict(altra))] == dal_codice
        _con({"in_cima": []})                                                   # tolta dal pannello
        assert scovo.in_cima(dict(FIAT)) == FIAT
    finally:
        _fine()


def test_in_cima_per_id_e_mai_piu_cara():
    try:
        _con({"in_cima": [{"id": 5}, {"id": 77, "mostra": 9000}, {"marca": "fiat", "modello": "panda", "km": 68000, "prezzo": 7900, "mostra": 7000}]})
        o = scovo.in_cima(dict(FIAT))
        assert o["vetrina"] == -99 and o["prezzo"] == 7900 and "prezzo_annuncio" not in o      # 9.000 > 7.900: solo in cima
        assert scovo.in_cima(dict(FIAT, id=5)) == dict(FIAT, id=5, vetrina=-100)
        assert scovo.in_cima(dict(FIAT, id=6)) == dict(FIAT, id=6)                            # modello diverso (panda)
    finally:
        _fine()


def test_valida_voce():
    assert IMP.valida_voce({"id": "123", "mostra": "6.900"}, mostra=True) == {"id": 123, "mostra": 6900}
    assert IMP.valida_voce({"marca": "Fiat", "modello": "500", "km": "68.000", "prezzo": 7900, "anno": ""}) == \
        {"marca": "fiat", "modello": "500", "km": 68000, "prezzo": 7900}
    for male in ({"marca": "fiat"}, {"marca": "fiat", "km": 1, "prezzo": 100, "mostra": 200}, {"id": "abc"}):
        try:
            IMP.valida_voce(male, mostra=True)
            assert False, male
        except ValueError:
            pass


# ------------------------------------------------------------------------------------------------ auto tolte
def _scarti_file():
    return json.loads((QUI / "data" / "scarti_sito.json").read_text())


def test_scarti_dal_database_come_dal_file():
    from dealfinder.web import app as A
    dal_file = A._scarti_cliente_ids()                     # nessun database: il file come prima
    assert dal_file == {int(e["id"]) for e in _scarti_file() if e.get("id")} and len(dal_file) == 93   # 95 voci, 2 doppie
    righe = [{"sid": k, "listing_id": e.get("id"), "marca": e.get("marca"), "modello": e.get("modello"), "anno": e.get("anno"),
              "km": e.get("km"), "prezzo": e.get("prezzo"), "motivo": e.get("motivo"), "attivo": not e.get("rimetti")}
             for k, e in enumerate(_scarti_file())]
    try:
        _con({"scarti": [r for r in righe if r["attivo"]]})
        assert A._scarti_cliente_ids() == dal_file
        _con({"scarti": [r for r in righe if r["attivo"] and r["listing_id"] != 60545]})     # rimessa dal pannello
        assert 60545 not in A._scarti_cliente_ids()
        _con({"scarti": []})                                    # tutte rimesse: nessuna fuori (non torna al file)
        assert A._scarti_cliente_ids() == set()
    finally:
        _fine()


def test_togli_scartate_per_id_e_per_dati():
    items = [dict(FIAT, id=i) for i in (1, 2, 3)] + [{"id": 4, "marca": "Mini", "anno": 2016, "km": 83000, "prezzo": 15500},
                                                     {"id": 5, "marca": "Mini", "anno": 2017, "km": 83000, "prezzo": 15500}]
    try:
        _con({"scarti": [{"sid": 1, "listing_id": 2, "attivo": True},
                         {"sid": 2, "listing_id": None, "marca": "mini", "anno": 2016, "km": 83000, "prezzo": 15500, "attivo": True}]})
        assert [it["id"] for it in IMP.togli_scartate(items)] == [1, 3, 5]
        _con({})
        assert IMP.togli_scartate(items) is items
    finally:
        _fine()


# ------------------------------------------------------------------------------------------------ cache
def test_cache_breve_e_ultima_lettura_buona():
    try:
        let = _con({"ribasso": {"attivo": False, "schema": "0:100", "fino": None}})
        v0 = IMP.versione()
        for _ in range(50):
            IMP.ribasso()
            scovo.ribasso_per(5000)
        assert let.letture == 1                                  # nessuna lettura in più entro la cache
        IMP._stato["t"] -= IMP.CACHE_S + 1                       # passati 45 secondi, nulla cambiato
        IMP.dati()
        assert let.letture == 2 and IMP.versione() == v0
        let.dati = {"ribasso": {"attivo": True, "schema": "0:100", "fino": None}}
        assert IMP.ribasso()["attivo"] is False                  # dentro la cache: ancora la lettura di prima
        IMP._stato["t"] -= IMP.CACHE_S + 1
        assert IMP.ribasso()["attivo"] is True and IMP.versione() == v0 + 1
        let.dati = RuntimeError("database giù")
        IMP._stato["t"] -= IMP.CACHE_S + 1
        assert IMP.ribasso()["attivo"] is True                   # database giù: resta l'ultima lettura buona
        IMP.invalida()
        assert IMP.ribasso()["attivo"] is True
    finally:
        _fine()


def test_senza_database_niente_letture():
    vecchia = os.environ.pop("DATABASE_URL", None)
    try:
        IMP.usa_lettore(None)
        assert IMP.dati() == {} and IMP.ribasso() is None and IMP.in_cima() is None and IMP.scarti() is None
    finally:
        if vecchia is not None:
            os.environ["DATABASE_URL"] = vecchia


# ------------------------------------------------------------------------------------------------ elenco salvato
class _ConnFinta:
    """sito_cache in un dizionario (stesse istruzioni SQL del modulo)."""
    def __init__(self, tab):
        self.tab = tab

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def execute(self, sql, p=None):
        r = types.SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [], rowcount=0)
        if sql.startswith("INSERT INTO sito_cache"):
            self.tab[p[0]] = {"dati": p[1], "t": time.time() - self.tab.get("_vecchio", 0)}
        elif "to_regclass" in sql:
            r.fetchone = lambda: {"t": "sito_cache"}
        elif sql.startswith("SELECT dati"):
            x = self.tab.get(p[0])
            r.fetchone = lambda: {"dati": x["dati"], "eta": time.time() - x["t"]} if x else None
        return r


def _items():
    return [dict(FIAT, id=i, nuova=datetime(2026, 11, 12, 9, i, tzinfo=timezone.utc), geo=[45.4, 9.1],
                 pv={"categoria": "opportunita", "margine": 2100 + i, "M": 9800}) for i in range(1, 40)]


def test_elenco_salvato_andata_e_ritorno():
    tab = {}
    con = lambda: _ConnFinta(tab)                      # noqa: E731
    ES._ultimo.clear()
    items, opens = _items(), {i: i % 3 for i in range(1, 40)}
    assert ES.salva_elenco(items, opens, connetti=con)
    assert not ES.salva_elenco(items, opens, connetti=con)       # uguale e appena salvato: niente scrittura
    assert not ES.salva_elenco([], {}, connetti=con)             # un elenco vuoto non sostituisce quello buono
    it2, op2, eta = ES.carica_elenco(connetti=con)
    assert it2 == items and op2 == opens and eta < 5            # date, numeri e liste tornano uguali
    assert isinstance(it2[0]["nuova"], datetime)
    tab["elenco"]["t"] -= 13 * 3600                             # più vecchio di 12 ore: non si usa
    assert ES.carica_elenco(connetti=con) is None


def test_stats_scheda_andata_e_ritorno():
    from dealfinder.web import trattativa as T
    stats = {"t": time.time(), "n": 900, "in_corso": False,
             "gruppi": {("privato", 0, 0): {"n": 300, "resto_med": -.03, "resto_p25": -.08, "quota_scende": .4, "giorni_med": 20.0}},
             "modelli": {("fiat", "500", "benzina", 2016): (40, 12, 21.0, 9.0, 7600.0, None)},
             "attivi": {("fiat", "500", "benzina", 2016): (55, 7900.0, 20)},
             "esempi": {("fiat", "500", "benzina", 2016): [(7500, 70000, "Milano", 12, "2026-11-01", 2016)]}}
    tab = {}
    con = lambda: _ConnFinta(tab)                      # noqa: E731
    ES._ultimo.clear()
    assert ES.salva_stats(stats, connetti=con)
    nuovo = {"t": 0.0, "gruppi": {}, "n": 0, "in_corso": False, "modelli": {}, "attivi": {}, "esempi": {}}
    ore = ES.carica_stats(nuovo, connetti=con)
    assert ore is not None and ore < .01
    for k in ("gruppi", "modelli", "attivi", "esempi", "n"):
        assert nuovo[k] == stats[k], k
    assert T.gruppo("privato", 3, 0, nuovo["gruppi"])["n"] == 300
    assert T.rivendita("fiat", "500", "benzina", 2016, modelli=nuovo["modelli"], attivi=nuovo["attivi"],
                       esempi=nuovo["esempi"]) == T.rivendita("fiat", "500", "benzina", 2016, modelli=stats["modelli"],
                                                              attivi=stats["attivi"], esempi=stats["esempi"])


# ------------------------------------------------------------------------------------------------ avvio e /health
def _salva_elenco_app():
    from dealfinder.web import app as A
    return A, dict(A._elenco)


def _rimetti_elenco_app(A, prima):
    A._elenco.clear()
    A._elenco.update(prima)


def test_avvio_legge_elenco_dal_database_e_health():
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient
    A, prima = _salva_elenco_app()
    vecchia, orig = os.environ.get("DATABASE_URL"), ES.carica_elenco
    c = TestClient(Starlette(routes=[Route("/health", A.health)]))
    try:
        os.environ["DATABASE_URL"] = "postgresql://finto/finto"
        _con({"scarti": [{"sid": 1, "listing_id": 3, "attivo": True}]})
        A._elenco.update(t=0.0, items=[], opens={}, busy=False, pronto=False, fonte=None, imp_v=None)
        r = c.get("/health")
        assert r.status_code == 503 and r.json()["ok"] is False and r.json()["auto"] == 0
        ES.carica_elenco = lambda connetti=None: (_items(), {1: 2}, 120.0)
        assert A._carica_elenco_salvato()
        assert A._elenco["pronto"] and A._elenco["fonte"] == "database" and A._elenco["opens"] == {1: 2}
        assert [it["id"] for it in A._elenco["items"]][:3] == [1, 2, 4]           # tolte dal pannello: subito fuori
        r = c.get("/health")
        assert r.status_code == 200 and r.json() == {"ok": True, "auto": 38, "elenco": "database"}
        assert not A._carica_elenco_salvato()                                      # c'è già un elenco: non si rilegge
        A._elenco.update(busy=True)                                                # ricalcolo in corso: sempre 200
        assert c.get("/health").status_code == 200
        A._elenco.update(items=[], pronto=False, busy=False)
        A._AVVIO_PRIMA = A._AVVIO
        A._AVVIO = time.monotonic() - 60                                          # 60 s senza elenco: ancora 503
        assert c.get("/health").status_code == 503
        A._AVVIO = time.monotonic() - 91                                          # oltre 90 s: 200 (niente riavvii a catena)
        r = c.get("/health")
        assert r.status_code == 200 and "avviso" in r.json()
    finally:
        A._AVVIO = getattr(A, "_AVVIO_PRIMA", A._AVVIO)
        if hasattr(A, "_AVVIO_PRIMA"):
            del A._AVVIO_PRIMA
        ES.carica_elenco = orig
        _fine()
        _rimetti_elenco_app(A, prima)
        os.environ.pop("DATABASE_URL", None) if vecchia is None else os.environ.__setitem__("DATABASE_URL", vecchia)


def test_health_senza_database_subito_pronto():
    from dealfinder.web import app as A
    vecchia = os.environ.pop("DATABASE_URL", None)
    try:
        r = A.health(None)
        assert r.status_code == 200
    finally:
        if vecchia is not None:
            os.environ["DATABASE_URL"] = vecchia


def test_pannello_toglie_subito_dall_elenco_calcolato():
    A, prima = _salva_elenco_app()
    try:
        let = _con({})
        A._elenco.update(items=_items(), imp_v=None)
        A._filtra_impostazioni()
        assert len(A._elenco["items"]) == 39
        let.dati = {"scarti": [{"sid": 9, "listing_id": 7, "attivo": True}]}
        IMP.invalida()                                          # come dopo "Togli" dal pannello
        A._filtra_impostazioni()
        assert 7 not in {it["id"] for it in A._elenco["items"]} and len(A._elenco["items"]) == 38
    finally:
        _fine()
        _rimetti_elenco_app(A, prima)


# ------------------------------------------------------------------------------------------------ pannello: protezione
def test_endpoint_sito_solo_admin_e_validazione():
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from dealfinder.web import app as A, admin_sito as S
    app = Starlette(routes=S.ROTTE, exception_handlers={A.HTTPError: A.http_error})
    c = TestClient(app)
    for metodo, url in (("get", "/api/admin/sito"), ("get", "/api/admin/sito/cerca?q=fiat"), ("put", "/api/admin/sito/ribasso"),
                        ("put", "/api/admin/sito/in-cima"), ("post", "/api/admin/sito/scarti"), ("post", "/api/admin/sito/scarti/1/rimetti")):
        assert getattr(c, metodo)(url, **({"json": {}} if metodo != "get" else {})).status_code == 401, url
    orig_cu, orig_conn = A.current_user, A.connect
    scritte = []

    class _C(_ConnFinta):
        def execute(self, sql, p=None):
            scritte.append((sql, p))
            return types.SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [], rowcount=0)
    try:
        A.current_user = lambda request, admin=False: {"id": 1, "role": "admin"} if admin else None
        A.connect = lambda: _C({})
        _con({})
        assert c.put("/api/admin/sito/ribasso", json={"attivo": True, "schema": "2000-700"}).status_code == 400
        r = c.put("/api/admin/sito/ribasso", json={"attivo": True, "schema": "2000:700", "fino": None})
        assert r.status_code == 200 and r.json()["acceso_ora"] is True
        assert any("INSERT INTO sito_impostazioni" in s and p[0] == "ribasso" for s, p in scritte)
        assert c.put("/api/admin/sito/in-cima", json={"voci": [{"marca": "fiat", "km": 1, "prezzo": 100, "mostra": 900}]}).status_code == 400
        assert c.put("/api/admin/sito/in-cima", json={"voci": [{"id": 77, "mostra": 6900, "sul_sito": {"x": 1}}]}).status_code == 200
        assert json.loads([p for s, p in scritte if "sito_impostazioni" in s][-1][1]) == [{"id": 77, "mostra": 6900}]
    finally:
        A.current_user, A.connect = orig_cu, orig_conn
        _fine()


# ------------------------------------------------------------------------------------------------ commercianti (2d)
def test_paga_carta_e_permessi_dal_pannello_senza_riavvio():
    """La casella "Deve pagare con carta" e i permessi scrivono nel database; ogni richiesta li rilegge (nessuna cache)."""
    import asyncio
    from dealfinder.web import app as A, sessions
    riga = {"permessi": ["auto"], "paga_carta": False, "richiede_abbonamento": False, "abbonamento_stato": None,
            "abbonamento_tolleranza": None, "con_password": True, "role": "commerciante"}
    scritte = []

    class _C(_ConnFinta):
        def execute(self, sql, p=None):
            scritte.append(sql)
            if sql.startswith("UPDATE dealers SET paga_carta"):
                riga["paga_carta"] = p[0]
            if sql.startswith("UPDATE dealers SET permessi"):
                riga["permessi"] = p[0]
            out = dict(riga) if "FROM dealers" in sql else None
            return types.SimpleNamespace(fetchone=lambda: out, fetchall=lambda: [], rowcount=1)
    orig = (A.current_user, A.connect, sessions.dimentica_account)
    dimenticati = []
    try:
        A.connect = lambda: _C({})
        sessions.dimentica_account = dimenticati.append
        A.current_user = lambda request, admin=False: {"id": 9, "role": "commerciante"}
        assert A.richiede(None, "auto")["id"] == 9                         # vede le auto
        A.current_user = lambda request, admin=False: {"id": 1, "role": "admin"}

        class _R:
            path_params = {"id": 9}

            async def json(self):
                return self.dati
        r = _R()
        r.dati = {"paga_carta": True, "permessi": ["auto", "appuntamenti"]}
        assert asyncio.run(A.admin_update_dealer(r)).status_code == 200
        assert dimenticati == [9] and riga["paga_carta"] is True and riga["permessi"] == ["auto", "appuntamenti"]
        A.current_user = lambda request, admin=False: {"id": 9, "role": "commerciante"}
        try:
            A.richiede(None, "auto")
            assert False, "doveva chiedere il pagamento"
        except A.HTTPError as e:
            assert e.status == 402                                         # subito, senza riavvio
    finally:
        A.current_user, A.connect, sessions.dimentica_account = orig


# ------------------------------------------------------------------------------------------------ memoria
def test_cache_testo_in_bit():
    from dealfinder.web import app as A
    d = {"tel_testo": True, "tel_estero": False, "testo_truffa": True, "segni_commerciante": False, "promo_testo": False,
         "solo_fin": True, "loconte_testo": False}
    assert A._testo_da_bit(A._testo_in_bit(d)) == d and A._testo_da_bit(None) == {}
    assert A._testo_chiave(123, 4500) != A._testo_chiave(123, 4600) != A._testo_chiave(124, 4500)
    assert A._testo_chiave(123, None) == A._testo_chiave(123, 0)


def test_jev_foto_a_blocchi():
    """Mai più di JEV_FOTO_BLOCCO auto con le foto in memoria (prima tutte le 300 insieme, ~230 MB)."""
    from dealfinder.pricing import jev
    vive = {"ora": 0, "max": 0}

    class _Foto(dict):
        def __init__(self):
            super().__init__(foto1={"data_base64": "x" * 1000})
            vive["ora"] += 1
            vive["max"] = max(vive["max"], vive["ora"])

        def __del__(self):
            vive["ora"] -= 1

    righe = [{"id": i, "cat": "opportunita", "margine": 1000, "price_eur": 5000, "telefono_ok": True} for i in range(1, 41)]

    class _C:
        def execute(self, sql, p=None):
            n = {"n": 0}
            return types.SimpleNamespace(fetchone=lambda: n, fetchall=lambda: [dict(r) for r in righe], rowcount=0)

        def commit(self):
            pass

    fatte = []
    finto_httpx = types.SimpleNamespace(Client=lambda **k: types.SimpleNamespace(close=lambda: None))
    orig = (jev.attiva, jev.foto_base64, jev.chiedi, jev.leggi, jev.stato_auto, sys.modules.get("httpx"))
    vecchie = {k: os.environ.get(k) for k in ("TYPESAFE_API_KEY", "JEV_FOTO_BLOCCO", "JEV_SOSTITUTO", "RIPIEGO_PRUDENTE")}
    try:
        # 13/11: il giro di Jev resta dietro JEV_SOSTITUTO=jev (di default ora fa il lavoro Claude, ai/controllo_auto)
        os.environ.update(TYPESAFE_API_KEY="x", JEV_FOTO_BLOCCO="8", JEV_SOSTITUTO="jev", RIPIEGO_PRUDENTE="0")
        sys.modules["httpx"] = finto_httpx
        jev.attiva = lambda: True
        jev.foto_base64 = lambda conn, lid, client: _Foto()
        jev.stato_auto = lambda r: {"id": r["id"]}
        jev.chiedi = lambda client, stato, foto: (fatte.append((stato["id"], bool(foto))), {"answers": {}})[1]
        jev.leggi = lambda risp: {"decisione": "compra", "confidenza": .9, "p_compra": .8, "truffa": 0, "versione_ok": 1,
                                  "dati_coerenti": 1, "stato": "ok", "correzione_pct": 0}
        jev._PAUSA.update(fino=0, prova=False)
        res = jev.giro(_C())
        assert res["fatte"] == 40 and len(fatte) == 40 and all(f for _, f in fatte)   # ogni auto con le sue foto
        assert vive["max"] <= 8, vive
    finally:
        jev.attiva, jev.foto_base64, jev.chiedi, jev.leggi, jev.stato_auto, h = orig
        if h is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = h
        for k, v in vecchie.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_subito_completo_freno_non_perde_annunci():
    """Quando il freno dice basta, il pacchetto in corso si salva prima di fermarsi: nessun annuncio letto va perso."""
    from dealfinder.pipeline import tempo_reale as tr
    salvati = []

    class _Col:
        def _deep(self, q, voluti, code, lim):
            for i in range(10_000):
                yield types.SimpleNamespace(raw=None, n=i)

    orig = tr._salva
    try:
        tr._salva = lambda conn, pacco, st: salvati.append(len(pacco))
        conn = types.SimpleNamespace(commit=lambda: None, rollback=lambda: None)
        mem = {"n": 0}

        def basta():
            mem["n"] += 1
            return mem["n"] >= 400
        st = tr.subito_completo(conn, col=_Col(), basta=basta)
        assert st["fermato_memoria"] == 1 and sum(salvati) == 400 and max(salvati) == 150   # salvato fino all'ultimo letto
        salvati.clear()
        st = tr.subito_completo(conn, col=_Col())                          # senza freno né tempo: legge tutto
        assert not st.get("fermato_memoria") and sum(salvati) >= 10_000 and sum(salvati) % 10_000 == 0   # tutte le regioni
    finally:
        tr._salva = orig


class _Notte:
    """Banco di prova della lettura notturna: 5 fasce da 200 annunci, passi e job_runs in memoria."""
    FASCE = 5

    def __init__(self):
        from dealfinder.web import app as A
        from dealfinder.pipeline import ciclo, passi, tempo_reale as tr
        self.A, self.ciclo, self.passi, self.tr = A, ciclo, passi, tr
        self.passi_db, self.rimandi, self.salvati, self.esiti, self.job_visti = {}, 0, [], [], []
        self.orig = (ciclo.inizia, passi.connect, tr._salva, A._rss_mb, A._aspetta_memoria, A._memoria.rilascia,
                     os.environ.get("SUBITO_COMPLETO_MAX_MB"), os.environ.get("PASSI_JOB"))
        banco = self

        class _Lav:
            def fatto(self, st=None):
                banco.esiti.append("fatto")

            def rimanda(self, st=None):
                banco.esiti.append("rimandato")

            def fallito(self, e=None):
                banco.esiti.append("fallito")

        class _C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def commit(self):
                pass

            def rollback(self):
                pass

            def execute(self, q, p=None):
                r = types.SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [], rowcount=0)
                if "FROM job_runs" in q:
                    r.fetchone = lambda: {"n": banco.rimandi}
                elif q.startswith("INSERT INTO job_runs"):
                    banco.rimandi += 1
                elif q.startswith("SELECT chiave FROM raccolta_passi"):
                    righe = [{"chiave": k} for k in banco.passi_db.get(p[0], set())]
                    r.fetchall = lambda: righe
                elif q.startswith("INSERT INTO raccolta_passi"):
                    banco.passi_db.setdefault(p[0], set()).add(p[1])
                elif q.startswith("DELETE FROM raccolta_passi WHERE job=%s"):
                    banco.passi_db.pop(p[0], None)
                return r
        self.C = _C
        ciclo.inizia = lambda job, tentativi_max=3, connetti=None: _Lav()
        passi.connect = _C
        tr._salva = lambda conn, pacco, st: self.salvati.extend(x.n for x in pacco)
        A._aspetta_memoria = lambda L, nome, limite=420.0: None
        A._memoria.rilascia = lambda motivo="", forza=False: 0.0
        os.environ.pop("PASSI_JOB", None)

    def col(self):
        from dealfinder.pipeline import passi
        banco = self

        class _Col:
            def _deep(self, q, voluti, code, lim):
                for f in range(banco.FASCE):
                    chiave = f"sub:{code}:MI:{f}"
                    if passi.fatto(chiave):
                        continue
                    banco.job_visti.append((passi.job(), os.environ.get("PASSI_JOB")))
                    for i in range(200):
                        yield types.SimpleNamespace(raw=None, n=f * 1000 + i)
                    passi.segna(chiave)
        return _Col()

    def giro(self):
        orig = self.tr.subito_completo
        col = self.col()
        self.tr.subito_completo = lambda conn, minuti=None, basta=None: orig(conn, col=col, basta=basta)
        try:
            return self.A._subito_completo_giro("2026-11-12", connetti=self.C)
        finally:
            self.tr.subito_completo = orig

    def fine(self):
        (self.ciclo.inizia, self.passi.connect, self.tr._salva, self.A._rss_mb, self.A._aspetta_memoria,
         self.A._memoria.rilascia, mb, pj) = self.orig
        os.environ.pop("SUBITO_COMPLETO_MAX_MB", None) if mb is None else os.environ.__setitem__("SUBITO_COMPLETO_MAX_MB", mb)
        os.environ.pop("PASSI_JOB", None) if pj is None else os.environ.__setitem__("PASSI_JOB", pj)


def test_subito_completo_freno_spento_arriva_in_fondo():
    """Memoria sempre sopra ogni tetto, freno a 0 (default, come in main): la lettura arriva in fondo e si segna fatta."""
    b = _Notte()
    try:
        os.environ.pop("SUBITO_COMPLETO_MAX_MB", None)
        b.A._rss_mb = lambda: 999.0
        assert b.giro() == "fatto" and b.esiti == ["fatto"]
        assert len(set(b.salvati)) == _Notte.FASCE * 200
        assert b.passi_db == {}                                             # finita: passi cancellati
        assert b.job_visti and all(j == "subito_completo:2026-11-12" and env is None for j, env in b.job_visti)
    finally:
        b.fine()


def test_subito_completo_freno_a_fascia_chiusa_e_al_massimo_3_rimandi():
    b = _Notte()
    try:
        os.environ["SUBITO_COMPLETO_MAX_MB"] = "470"
        b.A._rss_mb = lambda: 520.0                                         # resta alta anche dopo rilascio e attesa
        for _ in range(3):
            assert b.giro() == "rimandato"
        assert b.esiti == ["rimandato"] * 3 and b.rimandi == 3
        assert len(b.passi_db["subito_completo:2026-11-12"]) == 3          # una fascia intera segnata per giro
        assert b.giro() == "fatto"                                          # quarto giro: freno spento, arriva in fondo
        assert len(set(b.salvati)) == _Notte.FASCE * 200 and b.passi_db == {}
    finally:
        b.fine()


def test_subito_completo_freno_memoria_scesa_dopo_rilascio():
    b = _Notte()
    try:
        os.environ["SUBITO_COMPLETO_MAX_MB"] = "470"
        mem = {"mb": 520.0}
        b.A._rss_mb = lambda: mem["mb"]
        b.A._aspetta_memoria = lambda L, nome, limite=420.0: mem.update(mb=400.0)   # dopo l'attesa scende
        assert b.giro() == "fatto" and b.rimandi == 0
    finally:
        b.fine()


def test_subito_completo_non_parte_con_un_giro_pesante():
    import threading
    b = _Notte()
    fermo = threading.Event()
    t = threading.Thread(target=fermo.wait, name="lusso", daemon=True)
    t.start()
    try:
        assert b.giro() == "aspetta" and b.esiti == []
    finally:
        fermo.set()
        t.join()
        b.fine()


def test_passi_locali_al_thread():
    import threading
    from dealfinder.pipeline import passi
    vecchia = os.environ.get("PASSI_JOB")
    visti = {}
    try:
        os.environ["PASSI_JOB"] = "benzina:altro"
        with passi.locale("subito_completo:2026-11-12"):
            visti["qui"] = passi.job()
            t = threading.Thread(target=lambda: visti.update(altro=passi.job()))
            t.start()
            t.join()
        visti["dopo"] = passi.job()
        assert visti == {"qui": "subito_completo:2026-11-12", "altro": "benzina:altro", "dopo": "benzina:altro"}
    finally:
        os.environ.pop("PASSI_JOB", None) if vecchia is None else os.environ.__setitem__("PASSI_JOB", vecchia)


def test_rimanda_toglie_la_riga_senza_contare_un_tentativo():
    from dealfinder.pipeline import ciclo
    sql, rilasciato = [], []

    class _C:
        def execute(self, q, p=None):
            sql.append(q)

        def commit(self):
            pass
    lav = ciclo.LavoroUnaVolta("subito_completo:2026-11-12", _C(), lambda *a: rilasciato.append(1))
    lav.id = 7
    lav.rimanda({"fermato_memoria": 1})
    assert sql[0].startswith("DELETE FROM job_runs") and rilasciato == [1]
    lav.fatto()                                                              # già chiuso: niente altro
    assert not any("UPDATE job_runs" in q for q in sql)


def test_memoria_rilascia_non_rompe():
    from dealfinder import memoria
    assert memoria.rss_mb() > 0
    memoria._ultimo_rilascio[0] = float("-inf")
    chiamate = []
    orig_gc = memoria.gc.collect
    vecchia = os.environ.get("MEMORIA_RILASCIA")
    try:
        memoria.gc.collect = lambda: chiamate.append(1)
        assert memoria.rilascia("prova") >= 0 and memoria.rilascia("prova") == 0      # al massimo ogni 10 minuti
        assert chiamate == [1]
        memoria.rilascia("prova", forza=True)                                          # dopo Subito completo: sempre
        assert chiamate == [1, 1]
        memoria.gc.collect = orig_gc
        os.environ["MEMORIA_RILASCIA"] = "0"
        assert memoria.rilascia("prova") == 0
    finally:
        memoria.gc.collect = orig_gc
        os.environ.pop("MEMORIA_RILASCIA", None) if vecchia is None else os.environ.__setitem__("MEMORIA_RILASCIA", vecchia)


# ------------------------------------------------------------------------------------------------ Postgres VERO
def test_pg_migrazioni_e_valori_di_oggi():
    """047 e 048 sul Postgres vero: le impostazioni lette dal database danno lo stesso risultato di oggi."""
    c = pg_vero.nuovo_db("senza_riavvii_1")
    if c is None:
        return
    pg_vero.migra(c, "047_impostazioni_sito.sql", "048_impostazioni_iniziali.sql", "050_togli_fiat.sql",
                  "053_rimetti_fiat_ribassi.sql")
    pg_vero.migra(c, "048_impostazioni_iniziali.sql", "053_rimetti_fiat_ribassi.sql")   # due volte: niente doppioni
    d = IMP.leggi_db(c)
    assert d["ribasso"] == {"attivo": True, "schema": scovo.RIBASSO_SCHEMA, "fino": None}   # 13/11: ribassi accesi
    assert [{k: v for k, v in e.items() if k != "nota"} for e in d["in_cima"]] == scovo.IN_CIMA
    file = _scarti_file()
    assert len(d["scarti"]) == len([e for e in file if not e.get("rimetti")])   # 13/11: la Fiat 500 è di nuovo sul sito
    assert {x["listing_id"] for x in d["scarti"] if x["listing_id"]} == {e["id"] for e in file if e.get("id")}
    assert sorted((x["marca"], x["anno"], x["km"], x["prezzo"]) for x in d["scarti"] if not x["listing_id"]) == \
        sorted((e["marca"], e.get("anno"), e["km"], e["prezzo"]) for e in file if not e.get("id") and not e.get("rimetti"))
    try:
        IMP.usa_lettore(lambda: IMP.leggi_db(c))
        assert scovo.in_cima(dict(FIAT))["prezzo"] == 6900                     # 13/11: di nuovo in cima a 6.900 €
        assert scovo.ribasso_per(12000, PRIMA) > 0                             # 13/11: ribassi accesi
    finally:
        _fine()
    from dealfinder.pipeline import manuale
    voci = manuale._scarti_db(c)                                           # stesse voci del file per il lavoro di avvio
    assert sorted(json.dumps(v, sort_keys=True) for v in voci) == sorted(
        json.dumps(dict({k: v for k, v in e.items() if k != "rimetti"}, motivo=e.get("motivo") or "tolta dal cliente"), sort_keys=True)
        for e in file if not e.get("rimetti"))                             # (il motivo serve solo al pannello)


def test_pg_togli_rimetti_e_impostazioni():
    c = pg_vero.nuovo_db("senza_riavvii_2")
    if c is None:
        return
    pg_vero.migra(c, "047_impostazioni_sito.sql")
    c.execute("CREATE TABLE listings (id BIGINT PRIMARY KEY, make TEXT, model TEXT, year INT, mileage_km INT, price_eur INT, "
              "status TEXT DEFAULT 'attivo', stage TEXT, stage_reason TEXT, last_checked_at TIMESTAMPTZ)")
    c.execute("INSERT INTO listings (id, make, model, year, mileage_km, price_eur, stage, stage_reason) VALUES "
              "(1, 'fiat', '500', 2016, 68000, 7900, 'approfondito', 'opportunita'), "
              "(2, 'mini', 'cooper', 2016, 83000, 15500, 'approfondito', 'opportunita')")
    assert IMP.leggi_db(c) == {}                                           # tabelle vuote: valgono i default
    IMP.scrivi(c, "ribasso", IMP.valida_ribasso({"attivo": True, "schema": "0:500", "fino": None}), "1")
    IMP.scrivi(c, "in_cima", [IMP.valida_voce({"id": 1, "mostra": 6900}, mostra=True)], "1")
    IMP.scrivi(c, "ribasso", IMP.valida_ribasso({"attivo": False, "schema": "0:400", "fino": None}), "1")
    d = IMP.leggi_db(c)
    assert d["ribasso"] == {"attivo": False, "schema": "0:400", "fino": None} and d["in_cima"] == [{"id": 1, "mostra": 6900}]
    s1 = IMP.aggiungi_scarto(c, IMP.valida_voce({"id": 1}), "non la vuole", "1")
    s2 = IMP.aggiungi_scarto(c, IMP.valida_voce({"marca": "Mini", "anno": 2016, "km": 83000, "prezzo": 15500}), "", "1")
    stati = {r["id"]: (r["stage"], r["stage_reason"]) for r in c.execute("SELECT id, stage, stage_reason FROM listings").fetchall()}
    assert stati[1] == ("scartato", "analisi_manuale: tolta dal cliente: non la vuole")
    assert stati[2] == ("scartato", "analisi_manuale: tolta dal cliente")
    assert IMP.leggi_db(c)["scarti"][0]["listing_id"] == 1
    try:
        IMP.usa_lettore(lambda: IMP.leggi_db(c))
        assert [it["id"] for it in IMP.togli_scartate([{"id": 1}, {"id": 2, "marca": "MINI", "anno": 2016, "km": 83000, "prezzo": 15500},
                                                       {"id": 3}])] == [3]
    finally:
        _fine()
    assert IMP.rimetti_scarto(c, s1, "1")["listing_id"] == 1
    assert IMP.rimetti_scarto(c, s1, "1") is None                          # già rimessa
    IMP.rimetti_scarto(c, s2, "1")
    stati = {r["id"]: r["stage_reason"] for r in c.execute("SELECT id, stage_reason FROM listings").fetchall()}
    assert stati == {1: "da_verificare", 2: "da_verificare"}
    d = IMP.leggi_db(c)
    assert d["scarti"] == []                                               # tutte rimesse: niente torna dal file
    from dealfinder.pipeline import manuale
    assert manuale._scarti_db(c) == []


def test_pg_elenco_e_stats_salvati():
    c = pg_vero.nuovo_db("senza_riavvii_3")
    if c is None:
        return
    con = lambda: c                                    # noqa: E731
    ES._ultimo.clear()
    assert ES.carica_elenco(connetti=con) is None                          # tabella assente: nessun errore
    items, opens = _items(), {i: 1 for i in range(1, 40)}
    assert ES.salva_elenco(items, opens, connetti=con)                     # crea la tabella se manca (047 non ancora passata)
    it2, op2, eta = ES.carica_elenco(connetti=con)
    assert it2 == items and op2 == opens and 0 <= eta < 60
    ES._ultimo.clear()
    altri = items[:5]
    assert ES.salva_elenco(altri, {}, connetti=con)
    assert ES.carica_elenco(connetti=con)[0] == altri                      # sostituito, una riga sola
    assert c.execute("SELECT count(*) AS n, max(n) AS auto FROM sito_cache").fetchone() == {"n": 1, "auto": 5}
    c.execute("UPDATE sito_cache SET salvato_at = now() - interval '13 hours'")
    assert ES.carica_elenco(connetti=con) is None


# ------------------------------------------------------------------------------------------------ revisione
def test_filtro_non_rimette_un_elenco_vecchio():
    """Corsa tra thread: se mentre si filtra arriva il calcolo nuovo, il filtro non lo sovrascrive."""
    A, prima = _salva_elenco_app()
    orig = IMP.togli_scartate
    try:
        _con({"scarti": [{"sid": 1, "listing_id": 2, "attivo": True}]})
        vecchio = _items()
        nuovo_calcolo = [dict(FIAT, id=500)]
        A._elenco.update(items=vecchio, imp_v=None, fonte="calcolato", copia_t=None)

        def lento(items):
            A._elenco["items"] = nuovo_calcolo          # il calcolo finisce proprio adesso
            return orig(items)
        IMP.togli_scartate = lento
        A._filtra_impostazioni()
        assert A._elenco["items"] is nuovo_calcolo
        IMP.togli_scartate = orig
        A._filtra_impostazioni()                          # giro dopo: il filtro si applica al calcolo nuovo
        assert [it["id"] for it in A._elenco["items"]] == [500] and A._elenco["imp_v"] == IMP.versione()
    finally:
        IMP.togli_scartate = orig
        _fine()
        _rimetti_elenco_app(A, prima)


def test_copia_del_database_salvata_ora_e_calcolo_vince():
    A, prima = _salva_elenco_app()
    orig = ES.carica_elenco
    try:
        _con({})
        A._elenco.update(items=[], imp_v=None, fonte=None, copia_t=None, pronto=False)
        calcolato = [dict(FIAT, id=900)]

        def carica(connetti=None):
            A._elenco["items"] = calcolato                # il calcolo arriva mentre si legge la copia
            return _items(), {}, 60.0
        ES.carica_elenco = carica
        assert not A._carica_elenco_salvato() and A._elenco["items"] is calcolato
    finally:
        ES.carica_elenco = orig
        _fine()
        _rimetti_elenco_app(A, prima)


def test_copia_del_database_scade_dopo_12_ore():
    A, prima = _salva_elenco_app()
    orig = ES.carica_elenco
    try:
        _con({})
        A._elenco.update(items=[], imp_v=None, fonte=None, copia_t=None)
        ES.carica_elenco = lambda connetti=None: (_items(), {}, 11.5 * 3600)
        assert A._carica_elenco_salvato() and A._elenco["fonte"] == "database"
        assert abs(time.time() - A._elenco["copia_t"] - 11.5 * 3600) < 5      # età della copia salvata in _elenco
        assert not A._scadenza_copia() and len(A._elenco["items"]) == 39
        A._elenco["copia_t"] -= 3600                                          # ora ha 12,5 ore
        A._filtra_impostazioni()
        assert A._elenco["items"] == [] and A._elenco["fonte"] == "copia scaduta"
        A._elenco.update(items=_items(), fonte="calcolato", copia_t=None)    # un elenco calcolato non scade
        assert not A._scadenza_copia() and len(A._elenco["items"]) == 39
    finally:
        ES.carica_elenco = orig
        _fine()
        _rimetti_elenco_app(A, prima)


def test_prima_lettura_aspetta_quella_in_corso():
    import threading
    try:
        partito = threading.Event()

        def lento():
            partito.set()
            time.sleep(.5)
            return {"ribasso": {"attivo": True, "schema": "0:100", "fino": None}}
        IMP.usa_lettore(lento)
        IMP._stato.update(firma=None, dati={})
        t = threading.Thread(target=IMP.dati)
        t.start()
        partito.wait(2)
        assert IMP.ribasso() == {"attivo": True, "schema": "0:100", "fino": None}   # non i valori del codice
        t.join()
    finally:
        _fine()


def test_ribasso_variabile_data_vuota_spento():
    """Senza impostazioni nel database: SITO_RIBASSO_FINO vuota = spento (come nel ramo pagamenti-sicurezza)."""
    vecchie = {k: os.environ.get(k) for k in ("SITO_RIBASSO", "SITO_RIBASSO_FINO")}
    try:
        _con({})
        os.environ.update(SITO_RIBASSO=scovo.RIBASSO_SCHEMA, SITO_RIBASSO_FINO="")
        assert scovo.ribasso_per(12000, PRIMA) == 0
        os.environ["SITO_RIBASSO_FINO"] = "2026-11-10T16:55:00+01:00"
        assert scovo.ribasso_per(12000, PRIMA) == 1500
        _con({"ribasso": {"attivo": True, "schema": "0:500", "fino": ""}})      # pannello: acceso e data vuota = acceso
        assert scovo.ribasso_per(12000, DOPO) == 500
    finally:
        _fine()
        for k, v in vecchie.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_pg_jolly_ilike_non_passano():
    c = pg_vero.nuovo_db("senza_riavvii_4")
    if c is None:
        return
    pg_vero.migra(c, "047_impostazioni_sito.sql")
    c.execute("CREATE TABLE listings (id BIGINT PRIMARY KEY, make TEXT, model TEXT, year INT, mileage_km INT, price_eur INT, "
              "status TEXT DEFAULT 'attivo', stage TEXT, stage_reason TEXT, last_checked_at TIMESTAMPTZ)")
    c.execute("INSERT INTO listings (id, make, model, mileage_km, price_eur, stage) VALUES "
              "(1, 'fiat', '500', 68000, 7900, 'approfondito'), (2, 'f_at', '5%0', 68000, 7900, 'approfondito')")
    IMP.aggiungi_scarto(c, IMP.valida_voce({"marca": "f_at", "modello": "5%0", "km": 68000, "prezzo": 7900}), "", "1")
    stati = {r["id"]: r["stage"] for r in c.execute("SELECT id, stage FROM listings").fetchall()}
    assert stati == {1: "approfondito", 2: "scartato"}                    # "_" e "%" scritti nel pannello non sono jolly


def test_errore_alla_prima_lettura_non_fa_aspettare_le_altre():
    try:
        IMP.usa_lettore(_Lettore(RuntimeError("database giù")))
        IMP._stato.update(firma=None, dati={})
        assert IMP.dati() == {} and IMP._stato["firma"] == ""
    finally:
        _fine()


def test_ribasso_variabile_con_spazi():
    vecchie = {k: os.environ.get(k) for k in ("SITO_RIBASSO", "SITO_RIBASSO_FINO")}
    try:
        _con({})
        os.environ.update(SITO_RIBASSO=scovo.RIBASSO_SCHEMA, SITO_RIBASSO_FINO="   ")
        assert scovo.ribasso_per(12000, PRIMA) == 0
        os.environ["SITO_RIBASSO_FINO"] = " 2026-11-10T16:55:00+01:00 "
        assert scovo.ribasso_per(12000, PRIMA) == 1500
    finally:
        _fine()
        for k, v in vecchie.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


# ------------------------------------------------------------------------------------------------ 12/11 19:13 Fiat tolta
def test_fiat_rimessa_in_cima_e_ribassi_accesi():
    """Cliente 13/11 10:52: la Fiat 500 rossa (fiat, 68.000 km, 7.900 €) di nuovo sul sito, in cima a 6.900 €, e
    ribassi accesi. Codice + file senza database, e 047+048+050+053 sul Postgres vero (due volte)."""
    assert scovo.in_cima(dict(FIAT))["prezzo"] == 6900
    voce = [e for e in _scarti_file() if e.get("marca") == "fiat" and e.get("km") == 68000 and e.get("prezzo") == 7900]
    assert len(voce) == 1 and voce[0].get("rimetti")
    c = pg_vero.nuovo_db("senza_riavvii_fiat")
    if c is None:
        return
    pg_vero.migra(c, "047_impostazioni_sito.sql", "048_impostazioni_iniziali.sql", "050_togli_fiat.sql",
                  "053_rimetti_fiat_ribassi.sql")
    pg_vero.migra(c, "053_rimetti_fiat_ribassi.sql")                       # due volte: nessun doppione
    n = c.execute("SELECT count(*) AS n, bool_or(attivo) AS a FROM sito_scarti WHERE marca='fiat' AND km=68000 "
                  "AND prezzo=7900").fetchone()
    assert (n["n"], n["a"]) == (1, False)
    d = IMP.leggi_db(c)
    assert len(d["in_cima"]) == 1 and d["in_cima"][0]["mostra"] == 6900
    assert d["ribasso"]["attivo"] is True and d["ribasso"]["fino"] is None
    try:
        IMP.usa_lettore(lambda: IMP.leggi_db(c))
        assert scovo.in_cima(dict(FIAT))["prezzo"] == 6900
        assert IMP.togli_scartate([dict(FIAT)]) == [dict(FIAT)]
    finally:
        _fine()
    # con altre auto in cima messe dal pannello: la 053 aggiunge solo la Fiat
    c2 = pg_vero.nuovo_db("senza_riavvii_fiat_2")
    pg_vero.migra(c2, "047_impostazioni_sito.sql", "048_impostazioni_iniziali.sql", "050_togli_fiat.sql")
    c2.execute("UPDATE sito_impostazioni SET valore = valore || '[{\"id\": 5, \"mostra\": 9000}]'::jsonb WHERE chiave='in_cima'")
    pg_vero.migra(c2, "053_rimetti_fiat_ribassi.sql")
    pg_vero.migra(c2, "053_rimetti_fiat_ribassi.sql")
    assert [e.get("id") or e.get("marca") for e in IMP.leggi_db(c2)["in_cima"]] == [5, "fiat"]



def test_054_rimette_in_elenco_la_fiat():
    c = pg_vero.nuovo_db("senza_riavvii_054")
    if c is None:
        return
    c.execute("CREATE TABLE listings (id BIGINT PRIMARY KEY, make TEXT, model TEXT, year INT, mileage_km INT, price_eur INT, "
              "status TEXT DEFAULT 'attivo', stage TEXT, stage_reason TEXT, last_checked_at TIMESTAMPTZ)")
    c.execute("INSERT INTO listings (id, make, model, year, mileage_km, price_eur, stage, stage_reason) VALUES "
              "(77, 'Fiat', '500', 2016, 68000, 7900, 'scartato', 'analisi_manuale: tolta dal cliente'),"
              "(78, 'Fiat', '500', 2016, 68000, 7900, 'scartato', 'danno_grave')")
    pg_vero.migra(c, "054_fiat_stage.sql")
    pg_vero.migra(c, "054_fiat_stage.sql")
    st = {r["id"]: r["stage"] for r in c.execute("SELECT id, stage FROM listings").fetchall()}
    assert st == {77: "approfondito", 78: "scartato"}
