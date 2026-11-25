"""25/11: i cellulari di raccolta_contatti (RACC_CELL) in una lista Brevo (marketing/esporta_raccolta.py).
Brevo e database finti: nessuna chiamata vera."""
import json
import re
from urllib.parse import unquote

from dealfinder.marketing import esporta_raccolta as ER
from dealfinder.pipeline import coda_lavori as CL

API = ER.API
ZONA = {"monza": ("Monza", "MB", 15.2), "bergamo": ("Bergamo", "BG", 44.9), "sesto-san-giovanni": ("Sesto San Giovanni", "MI", 8.0)}


def _riga(n, comune="Monza", prov="MB", nome=None, **k):
    return {"nome": nome or f"Autosalone {n}", "comune": comune, "prov": prov, "sito": f"https://www.salone{n}.it",
            "origine": "maps", "cellulare": f"+39333000{n:04d}", "altri_cellulari": "", **k}


# ---------- la voce ----------
def test_etichetta():
    assert ER.etichetta("esporta_raccolta:2611") == "2611" and ER.etichetta("esporta_raccolta: sera_26-11 ") == "sera_26-11"
    assert ER.LISTA.format("2611") == "Skovo raccolta cellulari 2611"
    for v in ("esporta_raccolta:", "esporta_raccolta", "esporta_raccolta:26 11", "esporta_raccolta:a:b", "altro:2611",
              "esporta_raccolta:" + "x" * 21, "esporta_raccolta:26/11", None):
        try:
            ER.etichetta(v)
            assert False, v
        except ValueError:
            pass


# ---------- la scelta ----------
def test_scelta_solo_cellulari_italiani_entro_50_km_senza_esclusi_stop_e_doppioni():
    righe = [
        _riga(1),                                                       # buono
        json.dumps(_riga(2, comune="20099 Sesto San Giovanni (MI)", prov="MI")),   # riga come testo JSON, comune "sporco"
        _riga(3, cellulare="+39 02 1234567"),                           # fisso
        _riga(4, cellulare="+4915112345678"),                           # estero
        _riga(5, cellulare=""),                                         # senza numero
        _riga(6, comune="Torino", prov="TO"),                           # fuori zona
        _riga(7, comune="Brescia", prov="BS"),                          # provincia vicina ma comune oltre i 50 km
        _riga(8, nome="Officine Mulino Srl"),                           # escluso per nome
        _riga(9, sito="https://www.privacar.com/agenzia-monza"),        # escluso per sito
        _riga(10),                                                      # ha detto basta
        _riga(1, nome="Autosalone 1 bis"),                              # doppione del primo
        _riga(11, comune="Paese Sconosciuto", prov="MB"),               # MI/MB senza comune riconosciuto: dentro, km ignoto
        _riga(12, comune="Bergamo", prov="BG", altri_cellulari="+393339990001 +393339990002"),
        "non è json", None,
    ]
    contatti, conti = ER.scegli(righe, stop={"333 000 0010"}, zona=ZONA)
    assert [c["tel"] for c in contatti] == ["+393330000001", "+393330000002", "+393330000011", "+393330000012"]
    assert contatti[0] == {"tel": "+393330000001", "nome": "Autosalone 1", "comune": "Monza", "prov": "MB", "km": 15.2}
    assert contatti[1]["comune"] == "Sesto San Giovanni" and contatti[1]["prov"] == "MI" and contatti[1]["km"] == 8.0
    assert contatti[2]["km"] is None and contatti[2]["prov"] == "MB"
    assert conti == {"righe": 15, "non_cellulare": 5, "fuori_zona": 2, "esclusi": 2, "stop": 1, "doppioni": 1,
                     "altri_cellulari_lasciati": 2}
    assert len(contatti) + sum(v for k, v in conti.items() if k not in ("righe", "altri_cellulari_lasciati")) == conti["righe"]


def test_scelta_con_i_comuni_veri_del_raggio():
    contatti, conti = ER.scegli([_riga(1, comune="Monza", prov="MB"), _riga(2, comune="Roma", prov="RM"),
                                 _riga(3, comune="Napoli", prov="NA")])
    assert [c["tel"] for c in contatti] == ["+393330000001"] and conti["fuori_zona"] == 2
    assert contatti[0]["km"] is None or 0 < contatti[0]["km"] <= 50


# ---------- Brevo e database finti ----------
class _R:
    def __init__(self, stato=200, dati=None):
        self.status_code, self._d, self.headers, self.text = stato, dati, {}, ""

    def raise_for_status(self):
        assert self.status_code < 300, self.status_code

    def json(self):
        if self._d is None:
            raise ValueError("niente JSON")
        return self._d


class Brevo:
    def __init__(self, attributi=("SMS", "NOME"), liste=None, crea=None, per_numero=None):
        self.attributi, self.liste = list(attributi), dict(liste or {})
        self.crea = crea or (lambda corpo: _R(201, {"id": 1}))
        self.per_numero = per_numero or {}
        self.chiamate, self.contatti, self.create, self.attr_creati, self.aggiunti = [], [], [], [], []

    def close(self):
        pass

    def get(self, url, params=None, **k):
        self.chiamate.append(("GET", url.replace(API, ""), params))
        params = params or {}
        if url == API + "/contacts/attributes":
            return _R(200, {"attributes": [{"name": a, "category": "normal"} for a in self.attributi]})
        if url == API + "/contacts/lists":
            voci = [{"id": i, "name": n} for n, i in self.liste.items()]
            return _R(200, {"lists": voci[params.get("offset", 0):params.get("offset", 0) + params["limit"]]})
        if url == API + "/contacts/folders":
            return _R(200, {"folders": [{"id": 5, "name": "Skovo"}]})
        m = re.fullmatch(API + r"/contacts/(%2B39\d+)", url)
        if m:
            assert params == {"identifierType": "phone_id"}
            cid = self.per_numero.get(unquote(m.group(1)))
            return _R(200, {"id": cid}) if cid else _R(404, {})
        raise AssertionError("GET non previsto: " + url)

    def post(self, url, json=None, **k):
        self.chiamate.append(("POST", url.replace(API, ""), json))
        m = re.fullmatch(API + r"/contacts/attributes/normal/([A-Z]+)", url)
        if m:
            self.attr_creati.append((m.group(1), json["type"]))
            self.attributi.append(m.group(1))
            return _R(201, None)
        if url == API + "/contacts/lists":
            assert json["folderId"] == 5
            self.create.append(json["name"])
            self.liste[json["name"]] = 700
            return _R(201, {"id": 700})
        if url == API + "/contacts":
            assert "email" not in json and json["updateEnabled"] is True and re.fullmatch(r"\+393\d{9}", json["attributes"]["SMS"])
            r = self.crea(json)
            if r.status_code < 300:
                self.contatti.append(json)
            return r
        m = re.fullmatch(API + r"/contacts/lists/(\d+)/contacts/add", url)
        if m:
            self.aggiunti.append((int(m.group(1)), json))
            return _R(201, {"contacts": {"success": json["ids"], "failure": []}})
        raise AssertionError("POST non previsto: " + url)


class _Db:
    def __init__(self, righe, stop=()):
        self.righe, self.stop, self.frasi = righe, list(stop), []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def execute(self, q, p=()):
        q = " ".join(q.split())
        self.frasi.append((q, p))
        if q.startswith(("CREATE", "ALTER TABLE sms_si")):
            dati = []
        elif q == "SELECT dati FROM lead_consegne WHERE tipo = %s ORDER BY id":
            assert p == ("RACC_CELL",)
            dati = [{"dati": r} for r in self.righe]
        elif q == "SELECT telefono FROM sms_stop":
            dati = [{"telefono": t} for t in self.stop]
        else:
            raise AssertionError("frase non prevista: " + q[:80])
        return type("C", (), {"fetchall": lambda s: dati})()


class _Log:
    def __init__(self):
        self.righe = []

    def warning(self, msg, *a):
        self.righe.append(msg % a if a else msg)


def _giro(brevo, db, monkeypatch, v="esporta_raccolta:2611"):
    log, pause = _Log(), []
    monkeypatch.setattr(ER, "lg", log)
    try:
        esito = ER.lavoro(v, client=brevo, connetti=lambda: db, dormi=pause.append, zona=ZONA)
    except Exception as e:
        esito = e
    return esito, log, pause


def test_giro_intero_contatti_per_numero_attributi_e_solo_conteggi_nel_log(monkeypatch):
    db = _Db([_riga(1), _riga(2, comune="Bergamo", prov="BG"), _riga(3, comune="Torino", prov="TO"), _riga(4, nome="Privacar Monza"),
              _riga(5), _riga(6, comune="Boh", prov="MI")], stop=["+393330000005"])
    brevo = Brevo(attributi=("SMS", "NOME", "MI_LINK"))
    esito, log, pause = _giro(brevo, db, monkeypatch)
    assert not isinstance(esito, Exception), esito
    assert esito["righe"] == 6 and esito["da_caricare"] == 3 and esito["creati"] == 3 and esito["errori"] == 0
    assert esito["fuori_zona"] == 1 and esito["esclusi"] == 1 and esito["stop"] == 1
    assert esito["lista"] == 700 and esito["lista_nuova"] and brevo.create == ["Skovo raccolta cellulari 2611"]
    assert sorted(brevo.attr_creati) == [("COMUNE", "text"), ("KM", "float"), ("PROV", "text")]     # NOME c'era già
    assert esito["attributi"] == ["SMS", "COMUNE", "KM", "NOME", "PROV"]
    assert brevo.contatti[0] == {"attributes": {"SMS": "+393330000001", "NOME": "Autosalone 1", "COMUNE": "Monza", "PROV": "MB",
                                                "KM": 15.2}, "listIds": [700], "updateEnabled": True}
    assert brevo.contatti[1]["attributes"]["KM"] == 44.9 and brevo.contatti[1]["attributes"]["COMUNE"] == "Bergamo"
    assert "KM" not in brevo.contatti[2]["attributes"] and brevo.contatti[2]["attributes"]["PROV"] == "MI"   # km ignoto: non si manda
    assert not any("WHATSAPP" in x["attributes"] for x in brevo.contatti)
    assert pause.count(ER.PAUSA_S) == 3
    # nel log solo conteggi: mai un numero, nemmeno a pezzi lunghi
    assert len(log.righe) == 1 and log.righe[0].startswith("ESPORTA_RACCOLTA {")
    assert json.loads(log.righe[0][len("ESPORTA_RACCOLTA "):]) == esito
    assert not re.search(r"\d{6,}", log.righe[0]) and "+39" not in log.righe[0] and "Autosalone" not in log.righe[0]
    # le email non si toccano: nessuna lettura di RACC_EMAIL, nessun contatto con email
    assert not any("RACC_EMAIL" in str(f) for f in db.frasi)


def test_whatsapp_se_l_account_lo_usa_e_ripiego_solo_sms(monkeypatch):
    brevo = Brevo(attributi=("SMS", "WHATSAPP", "NOME", "COMUNE", "PROV", "KM"))
    esito, log, _ = _giro(brevo, _Db([_riga(1)]), monkeypatch)
    assert brevo.contatti[0]["attributes"]["WHATSAPP"] == "+393330000001" and not brevo.attr_creati
    assert esito["attributi"][:2] == ["SMS", "WHATSAPP"] and esito["creati"] == 1
    # Brevo rifiuta WHATSAPP: si riprova con il solo SMS
    brevo = Brevo(attributi=("SMS", "WHATSAPP"), crea=lambda corpo: _R(400, {"code": "invalid_parameter"})
                  if "WHATSAPP" in corpo["attributes"] else _R(201, {"id": 9}))
    esito, log, _ = _giro(brevo, _Db([_riga(1)]), monkeypatch)
    assert esito["creati"] == 1 and "WHATSAPP" not in brevo.contatti[0]["attributes"]


def test_numero_gia_di_un_altro_contatto_va_nella_lista_e_i_rifiutati_si_contano(monkeypatch):
    def crea(corpo):
        tel = corpo["attributes"]["SMS"]
        return {"+393330000001": _R(204, None), "+393330000002": _R(400, {"code": "duplicate_parameter"}),
                "+393330000003": _R(400, {"code": "invalid_parameter"})}.get(tel, _R(201, {"id": 1}))
    brevo = Brevo(crea=crea, per_numero={"+393330000002": 4321}, liste={"Skovo raccolta cellulari 2611": 88})
    esito, log, _ = _giro(brevo, _Db([_riga(n) for n in (1, 2, 3, 4)]), monkeypatch)
    assert not isinstance(esito, Exception), esito
    assert esito["lista"] == 88 and esito["lista_nuova"] is False and not brevo.create          # lista già fatta: riusata
    assert (esito["creati"], esito["aggiornati"], esito["gia_in_brevo_aggiunti"], esito["rifiutati"], esito["errori"]) == (1, 1, 1, 1, 0)
    assert brevo.aggiunti == [(88, {"ids": [4321]})]
    assert not re.search(r"\d{6,}", " ".join(log.righe))


def test_errori_di_brevo_il_lavoro_resta_da_fare_e_niente_numeri_nel_log(monkeypatch):
    brevo = Brevo(crea=lambda corpo: _R(500, {}) if corpo["attributes"]["SMS"].endswith("2") else _R(201, {"id": 1}))
    esito, log, _ = _giro(brevo, _Db([_riga(1), _riga(2)]), monkeypatch)
    assert isinstance(esito, RuntimeError) and "1 cellulari non caricati" in str(esito)
    assert json.loads(log.righe[-1][len("ESPORTA_RACCOLTA "):])["errori"] == 1 and not re.search(r"\d{6,}", " ".join(log.righe))
    # database fermo: errore senza toccare Brevo

    class Fermo:
        def __enter__(self):
            raise ConnectionError("db giù +393330000001")

        def __exit__(self, *a):
            return False
    brevo = Brevo()
    esito, log, _ = _giro(brevo, Fermo(), monkeypatch)
    assert isinstance(esito, ConnectionError) and log.righe == ["ESPORTA_RACCOLTA ERRORE: ConnectionError"] and not brevo.chiamate


def test_niente_da_caricare_non_crea_la_lista(monkeypatch):
    brevo = Brevo()
    esito, log, _ = _giro(brevo, _Db([_riga(1, comune="Torino", prov="TO")]), monkeypatch)
    assert esito["lista"] is None and esito["da_caricare"] == 0 and not brevo.chiamate and log.righe[0].startswith("ESPORTA_RACCOLTA {")


def test_senza_chiave_di_brevo_si_ferma(monkeypatch):
    monkeypatch.setattr(ER, "_chiave", lambda: "")
    monkeypatch.setattr(ER, "lg", _Log())
    try:
        ER.lavoro("esporta_raccolta:2611")
        assert False
    except RuntimeError as e:
        assert "BREVO_API_KEY" in str(e)


# ---------- la voce nella coda dei lavori ----------
def test_voce_solo_nel_sito_subito_anche_insieme_a_non_aperti(monkeypatch):
    v = "esporta_raccolta:2611"
    assert CL.SPOSTABILI["esporta_raccolta:"] == (False, CL.BREVO) and CL._prefisso(v) == "esporta_raccolta:"
    for modo in ("lunghi", "tutti", "0"):                       # mai nella coda del cron: la fa il sito appena parte
        monkeypatch.setenv("LAVORI_NEL_CRON", modo)
        assert not CL.spostabile(v)
    viste, fatti = [], []

    def lungo(chiave, nome_log, fn):
        viste.append((chiave, nome_log))
        fn()
        return True
    monkeypatch.setattr(CL, "_lungo", lungo)
    monkeypatch.setattr(ER, "lavoro", lambda voce: fatti.append(voce))
    from dealfinder.marketing import non_aperti as NA
    monkeypatch.setattr(NA, "lavoro", lambda voce: fatti.append(voce))
    # il valore con le due voci separate da "+": il sito le divide e le fa una dopo l'altra
    tutto = "esporta_raccolta:2611+non_aperti:101,102:Skovo non aperti 26-11"
    parti = [x.strip() for x in tutto.split("+") if x.strip()]
    assert not tutto.startswith(("mix:", "rimetti:", "giro_lotto:")) and len(parti) == 2
    for parte in parti:
        assert parte.startswith(CL.SOLO_SITO) and CL.esegui(parte) is True
    assert viste == [("esporta_raccolta:2611", "ESPORTA_RACCOLTA"), ("non_aperti:101,102:Skovo non aperti 26-11", "NON_APERTI")]
    assert fatti == parti
