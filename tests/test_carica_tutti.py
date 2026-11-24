"""24/11 (cliente: "carica tutti tutti tutti quelli che abbiamo in database tranne quelli esclusi"): lavoro una
tantum carica_tutti:<etichetta>. Brevo e database finti: esclusioni, niente email nei log, a blocchi, una volta."""
import contextlib
import json
import logging

import pytest

httpx = pytest.importorskip("httpx")
from dealfinder.marketing import brevo_liste as bl
from dealfinder.marketing.invii import impronta

ESCLUSE = ["info@loconte.it", "vendite@officinemulino.it", "a@cosvalauto.it", "b@autoeautovarese.it", "c@srauto.it",
           "info@gtautomobili.it", "info@gt-automobili.com", "milano@privacar.it", "ciao@vroom.it"]


def test_scegli_tutti_esclusioni_e_nomi():
    voci = [(e, None) for e in ESCLUSE] + [
        ("Info@Salone.it", None), ("info@salone.it", "Salone  Rossi"), ("x@pec.it", None), ("y@legalmail.it", None),
        ("via@d.it", "D"), ("rimbalzo@e.it", None), ("logo@2x.png", None), ("nonemail", None), ("", None),
        ("buono@f.com", None), ("perbene@g.it", "Privacar Monza"), ("altro@h.it", "Autosalone Loconte")]
    out, n = bl.scegli_tutti(voci, {impronta("via@d.it"), impronta("rimbalzo@e.it")})
    assert out == [{"email": "buono@f.com", "nome": ""}, {"email": "info@salone.it", "nome": "Salone Rossi"}]
    assert n["non_valide"] == 2 and n["escluse_pec"] == 2 and n["escluse_disiscritte_o_rimbalzate"] == 2
    assert n["escluse_loconte"] == 2 and n["escluse_altri_esclusi"] == len(ESCLUSE) - 1 + 1
    assert n["da_caricare"] == 2 and n["con_nome"] == 1
    assert n["raccolti"] == 2 + 2 + 2 + 2 + len(ESCLUSE) + 1 - 1


def test_esclusi_ci_sono_tutti():
    for e in ESCLUSE[1:]:
        assert bl._esclusi(e), e.split("@")[1]
    assert not bl._esclusi("info@salone.it")


def test_righe_di_privati_mai():
    assert bl.voci_da_riga('{"nome": "Mario", "azienda": false, "email": "m@x.it"}') is None
    assert bl.voci_da_riga('{"nome": "Salone", "azienda": true, "email": ["s@x.it", "t@x.it"]}') == [
        ("s@x.it", "Salone"), ("t@x.it", "Salone")]
    assert bl.voci_da_riga('{"riga": "tel; z@x.it"}') == [("z@x.it", None)]


def test_limite_piano():
    assert bl._limite_piano({"plan": [{"type": "subscription"}],
                             "planVerticals": [{"planCategory": "Marketing", "planType": "paid", "name": "Starter",
                                                "status": "active"}]}) == 500_000
    assert bl._limite_piano({"plan": [{"type": "free"}]}) == 100_000
    assert bl._limite_piano({"plan": [{"type": "subscription", "contactsLimit": 2000}]}) == 2000


class _Log(logging.Handler):
    """Raccoglie tutto quello che va nei log (run_tests.py non ha la fixture caplog)."""
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.righe = []

    def emit(self, r):
        self.righe.append(r.getMessage())

    @property
    def text(self):
        return "\n".join(self.righe)

    def __enter__(self):
        self.prima = logging.getLogger().level
        logging.getLogger().addHandler(self)
        logging.getLogger().setLevel(logging.DEBUG)
        return self

    def __exit__(self, *a):
        logging.getLogger().removeHandler(self)
        logging.getLogger().setLevel(self.prima)


class _R:
    def __init__(self, dati=None, status=200):
        self._d, self.status_code = dati or {}, status

    def json(self):
        return self._d

    def raise_for_status(self):
        assert self.status_code < 300


class _Brevo:
    def __init__(self, contatti, conto=None):
        self.contatti, self.post_, self.get_ = contatti, [], []
        self.conto = conto or {"plan": [{"type": "subscription"}]}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self.get_.append(url)
        u = url.split("/v3")[1]
        if u == "/account":
            return _R(self.conto)
        if u == "/contacts":
            return _R({"contacts": self.contatti})
        if u == "/contacts/lists":
            return _R({"lists": [{"name": "Skovo altra", "id": 3}]})
        if u == "/contacts/folders":
            return _R({"folders": [{"name": "Skovo", "id": 7}]})
        if u.startswith("/processes/"):
            return _R({"status": "completed"})
        if u.startswith("/contacts/lists/"):
            return _R({"uniqueSubscribers": 1203})
        raise AssertionError(u)

    def post(self, url, json=None):
        u = url.split("/v3")[1]
        self.post_.append((u, json))
        if u == "/contacts/lists":
            return _R({"id": 99}, 201)
        if u == "/contacts/import":
            return _R({"processId": 1000 + len(self.post_)}, 202)
        if u.startswith("/contacts/attributes/"):
            return _R({}, 201)
        raise AssertionError("chiamata non prevista: " + u)       # mai campagne, mai invii


class _Conn:
    def __init__(self, tabelle):
        self.t = tabelle

    def execute(self, sql, par=None):
        for nome, righe in self.t.items():
            if f"FROM {nome}" in sql:
                return type("C", (), {"fetchall": lambda s: righe, "fetchone": lambda s: None})()
        if sql.startswith("INSERT INTO disiscritti"):
            return type("C", (), {"fetchall": lambda s: [], "fetchone": lambda s: None})()
        raise RuntimeError("tabella che manca")

    def commit(self):
        pass

    def rollback(self):
        pass


def _prepara(monkeypatch, contatti, tabelle, conto=None):
    import dealfinder.db as db
    brevo = _Brevo(contatti, conto)
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-finta")
    monkeypatch.setattr(bl.httpx, "Client", lambda **k: brevo)
    monkeypatch.setattr(db, "connect", lambda: contextlib.nullcontext(_Conn(tabelle)))
    monkeypatch.setattr(bl, "_bloccati_0111", lambda: {f"h{i}" for i in range(bl.BLOCCATI_MINIMI)})
    return brevo


def _tabelle():
    buone = [f"salone{i}@dominio{i}.it" for i in range(1200)]
    return buone, {
        "lead_consegne": [{"d": json.dumps({"nome": f"Salone {i}", "email": e})} for i, e in enumerate(buone[:700])]
        + [{"d": json.dumps({"nome": "Loconte", "email": ESCLUSE[0]})}, {"d": json.dumps({"riga": " ".join(ESCLUSE)})},
           {"d": json.dumps({"nome": "Privato", "azienda": False, "email": "privato@casa.it"})},
           {"d": json.dumps({"email": "studio@pec.it"})}],
        "lead_email e": [{"email": [e, "via@disiscritto.it"], "nome": None} for e in buone[600:1100]],
        "disiscritti": [{"email_hash": impronta("via@disiscritto.it")}],
        "brevo_inviati": [{"email": e} for e in buone[1100:1195]],
    }


def test_carica_tutti_a_blocchi_senza_esclusi_e_senza_email_nei_log(monkeypatch):
    buone, tabelle = _tabelle()
    contatti = [{"email": e} for e in buone[1195:]] + [{"email": "Bloccato@Brevo.it", "emailBlacklisted": True},
                                                       {"email": "x@legalmail.it"}, {"email": buone[0]}]
    brevo = _prepara(monkeypatch, contatti, tabelle)
    with _Log() as caplog:
        esito = bl.carica_tutti(attesa_s=1)
        logging.getLogger("t").warning("CARICA_TUTTI %s", json.dumps(esito))
    imp = [j for u, j in brevo.post_ if u == "/contacts/import"]
    assert [len(j["jsonBody"]) for j in imp] == [500, 500, 200]                     # a blocchi, niente tetto a 1.495
    assert all(j["listIds"] == [99] and j["updateExistingContacts"] is True and j["emptyContactsAttributes"] is False
               for j in imp)
    caricate = [x["email"] for j in imp for x in j["jsonBody"]]
    assert sorted(caricate) == sorted(buone)
    assert ("/contacts/lists", {"name": "Skovo tutti 24-11", "folderId": 7}) in brevo.post_
    assert not any("ampaign" in u or "smtp" in u for u, _ in brevo.post_)            # nessun invio
    con_nome = [x for j in imp for x in j["jsonBody"] if "attributes" in x]
    assert len(con_nome) == 700 and con_nome[0]["attributes"]["AZIENDA"].startswith("Salone ")
    assert esito["caricati"] == 1200 and esito["lista_id"] == 99 and esito["blocchi"] == 3
    assert esito["escluse_loconte"] == 1 and esito["escluse_altri_esclusi"] == len(ESCLUSE) - 1
    assert esito["escluse_pec"] == 2 and esito["escluse_disiscritte_o_rimbalzate"] == 2
    assert esito["fonti"]["righe_non_attivita"] == 1 and len(esito["process_id"]) == 3
    assert esito["processi"] == {"completed": 3} and esito["nella_lista"] == 1203 and esito["limite_piano"] == 500_000
    assert "@" not in caplog.text and "@" not in json.dumps(esito)                   # nei log solo conteggi


def test_carica_tutti_fermo_se_il_piano_non_basta(monkeypatch):
    _, tabelle = _tabelle()
    brevo = _prepara(monkeypatch, [], tabelle, {"plan": [{"type": "subscription", "contactsLimit": 1000}]})
    esito = bl.carica_tutti(attesa_s=0)
    assert esito["fermo"] == "piano_insufficiente" and esito["caricati"] == 0 and not brevo.post_


def test_carica_tutti_fermo_senza_i_disiscritti(monkeypatch):
    _, tabelle = _tabelle()
    brevo = _prepara(monkeypatch, [], tabelle)
    monkeypatch.setattr(bl, "_bloccati_0111", lambda: set())
    assert bl.carica_tutti(attesa_s=0)["fermo"] == "disiscritti_incompleti" and not brevo.post_


def test_coda_una_volta_per_etichetta(monkeypatch):
    from dealfinder.pipeline import ciclo, coda_lavori as cl
    assert cl.SPOSTABILI["carica_tutti:"] == (False, cl.BREVO)
    assert cl.mancano("carica_tutti:2411", {}) == ["BREVO_API_KEY"]
    fatti, giri = set(), []
    monkeypatch.setattr(ciclo, "_una_volta", lambda job: not (job in fatti or fatti.add(job)))
    monkeypatch.setattr(bl, "carica_tutti", lambda: giri.append(1) or {"caricati": 3, "lista_id": 99})
    with _Log() as caplog:
        cl.esegui("carica_tutti:2411")
        cl.esegui("carica_tutti:2411")
        assert len(giri) == 1 and fatti == {"carica_tutti:2411"}
        cl.esegui("carica_tutti:2411bis")
    assert len(giri) == 2
    assert 'CARICA_TUTTI {"caricati": 3, "lista_id": 99}' in caplog.text and "già fatto" in caplog.text
