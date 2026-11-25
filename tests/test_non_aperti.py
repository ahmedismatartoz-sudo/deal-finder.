"""25/11: lista Brevo di chi non ha aperto le campagne (marketing/non_aperti.py). httpx finto: nessuna chiamata vera."""
import gzip
import io
import json
import re
import zipfile

from dealfinder.marketing import invii
from dealfinder.marketing import non_aperti as NA
from dealfinder.pipeline import coda_lavori as CL

API = NA.API


# ---------- la voce ----------
def test_voce_campagne_e_nome_della_lista():
    assert NA.analizza("non_aperti:101,102:Skovo non aperti 26-11") == ([101, 102], "Skovo non aperti 26-11")
    assert NA.analizza("non_aperti: 7 ,7,, 9 :Lista: con due punti ") == ([7, 9], "Lista: con due punti")
    for v in ("non_aperti:101", "non_aperti::Nome", "non_aperti:101,abc:Nome", "non_aperti:101:  ", "non_aperti:0:Nome",
              "non_aperti:-3:Nome", "altro:101:Nome", "", None):
        try:
            NA.analizza(v)
            assert False, v
        except ValueError:
            pass


# ---------- il CSV ----------
def test_csv_colonna_email_dal_titolo_con_ogni_separatore():
    csv_ = "ID;Email;NOME;Data\n1;Mario@Salone.it;Salone Mario;2026-11-24\n2;info@auto-due.com;Auto Due;2026-11-24\n"
    assert NA.emails_da_csv(csv_.encode()) == ["mario@salone.it", "info@auto-due.com"]
    assert NA.emails_da_csv(csv_.replace(";", ",").encode()) == ["mario@salone.it", "info@auto-due.com"]
    assert NA.emails_da_csv(csv_.replace(";", "\t").encode()) == ["mario@salone.it", "info@auto-due.com"]
    # titoli scritti in altri modi, con BOM e virgolette
    for titolo in ("EMAIL", "email", "E-mail", "Email_ID", "Email Address", "e_mail"):
        dati = ("﻿" + f'"{titolo}","NOME"\n"a@x.it","A"\n').encode()
        assert NA.emails_da_csv(dati) == ["a@x.it"], titolo


def test_csv_solo_la_colonna_delle_email_senza_doppioni_ne_righe_rotte():
    csv_ = ("EMAIL,NOME,REFERENTE,MI_LINK\n"
            "a@x.it,Salone A,collega@altro.it,scovo.onrender.com/mi/abc23456\n"      # l'email in un'altra colonna non conta
            "A@X.IT,Salone A bis,,\n"                                                # doppione
            "non-una-email,Salone B,,\n"
            ",Salone C,,\n"
            '"b@y.it","Salone, con virgola",,\n'
            "c@z.it\n")                                                              # riga corta
    assert NA.emails_da_csv(csv_.encode()) == ["a@x.it", "b@y.it", "c@z.it"]


def test_csv_senza_titolo_riconosciuto_prende_la_colonna_con_piu_email():
    assert NA.emails_da_csv(b"a@x.it\nb@y.it\n") == ["a@x.it", "b@y.it"]                 # nessuna riga di titoli
    csv_ = "Contatto;Indirizzo;Altro\nSalone A;a@x.it;z@z.it\nSalone B;b@y.it;\nSalone C;c@z.it;\n"
    assert NA.emails_da_csv(csv_.encode()) == ["a@x.it", "b@y.it", "c@z.it"]


def test_csv_zip_gzip_vuoto_e_file_che_non_e_un_elenco():
    csv_ = b"Email\na@x.it\nb@y.it\n"
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("export.csv", csv_)
    assert NA.emails_da_csv(z.getvalue()) == ["a@x.it", "b@y.it"]
    assert NA.emails_da_csv(gzip.compress(csv_)) == ["a@x.it", "b@y.it"]
    assert NA.emails_da_csv(b"") == [] and NA.emails_da_csv(b"\n \n") == []
    assert NA.emails_da_csv(b"Email;NOME\n") == [] and NA.emails_da_csv(b"EMAIL\n") == []    # solo i titoli
    for rotto in (b"<html><body>Access Denied</body></html>", b"<?xml version='1.0'?>\n<Error><Code>AccessDenied</Code></Error>",
                  b"NOME;COMUNE\nSalone A;Monza\n"):
        try:
            NA.emails_da_csv(rotto)
            assert False, rotto
        except ValueError:
            pass


# ---------- i filtri ----------
def test_filtri_ogni_motivo_contato_una_volta():
    contatti = {e: {} for e in ("buono@salone.it", "aperto@salone.it", "info@loconte.it", "vendite@officinemulino.com",
                                "no@risposto.it", "x@dominio-no.it", "info@skovo.app", "ahmedisma545@gmail.com",
                                "rimbalzo@smtp.it", "altro@salone.it")}
    contatti["nero@salone.it"] = {"emailBlacklisted": True}
    contatti["disiscritto@salone.it"] = {"unsubscribed": True}
    contatti["duro@salone.it"] = {"hardBounce": True}
    contatti["locale@salone.it"] = {}
    non_aperti = ["Buono@Salone.it", "buono@salone.it", "aperto@salone.it", "info@loconte.it", "vendite@officinemulino.com",
                  "no@risposto.it", "x@dominio-no.it", "info@skovo.app", "ahmedisma545@gmail.com", "nero@salone.it",
                  "disiscritto@salone.it", "locale@salone.it", "duro@salone.it", "rimbalzo@smtp.it", "cancellato@salone.it",
                  "altro@salone.it", "", None]
    buoni, tolti = NA.filtra(non_aperti, aperti=["APERTO@salone.it"], contatti=contatti, rimbalzati={"Rimbalzo@smtp.it"},
                             disiscritti={invii.impronta("locale@salone.it")},
                             esclusi_env=[" No@Risposto.it ", "dominio-no.it", ""])
    assert buoni == ["buono@salone.it", "altro@salone.it"]
    assert tolti == {"aperto_una_campagna": 1, "nostri": 2, "rinvio_esclusi": 2, "commercianti_esclusi": 2,
                     "disiscritti": 3, "rimbalzi_definitivi": 2, "non_in_brevo": 1}
    assert len(buoni) + sum(tolti.values()) == 15                             # ogni indirizzo in un solo conteggio


def test_filtri_senza_niente_da_togliere_e_senza_contatti():
    assert NA.filtra(["a@x.it"], contatti={"a@x.it": {}})[0] == ["a@x.it"]
    buoni, tolti = NA.filtra(["a@x.it"])                                      # nessun contatto in Brevo: non si aggiunge
    assert buoni == [] and tolti["non_in_brevo"] == 1


# ---------- Brevo finto ----------
class _R:
    def __init__(self, stato=200, dati=None, testo=""):
        self.status_code, self._d, self.text, self.headers = stato, dati, testo, {}

    def raise_for_status(self):
        assert self.status_code < 300, self.status_code

    def json(self):
        if self._d is None:
            raise ValueError("niente JSON")
        return self._d


class Brevo:
    """Risponde alle sole chiamate di non_aperti. export: {(campagna, tipo): csv | None (rifiutata) | "mai" | "fallita"}."""

    def __init__(self, export, contatti=None, bloccati=(), liste=None, cartelle=None, aggiunta=None):
        self.export, self.contatti, self.bloccati = export, contatti or [], list(bloccati)
        self.liste = dict(liste or {})
        self.cartelle = [{"id": 5, "name": "Skovo"}] if cartelle is None else cartelle
        self.aggiunta = aggiunta or (lambda emails: _R(201, {"contacts": {"success": emails, "failure": []}}))
        self.chiamate, self.processi, self.file, self.aggiunti, self.create = [], {}, {}, [], []

    def close(self):
        pass

    def get(self, url, params=None, **k):
        self.chiamate.append(("GET", url.replace(API, ""), params))
        params = params or {}
        m = re.fullmatch(API + r"/processes/(\d+)", url)
        if m:
            cid, tipo = self.processi[int(m.group(1))]
            cosa = self.export[(cid, tipo)]
            if cosa == "mai":
                return _R(200, {"status": "in_process"})
            if cosa == "fallita":
                return _R(200, {"status": "failed"})
            indirizzo = f"https://file.example/export-{cid}-{tipo}.csv?firma=SEGRETA"
            self.file[indirizzo] = cosa
            return _R(200, {"status": "completed", "export_url": indirizzo})
        if url == API + "/contacts":
            off = params.get("offset", 0)
            return _R(200, {"contacts": self.contatti[off:off + params["limit"]]})
        if url == API + "/smtp/blockedContacts":
            off = params.get("offset", 0)
            return _R(200, {"contacts": [{"email": e} for e in self.bloccati[off:off + params["limit"]]]})
        if url == API + "/contacts/lists":
            voci = [{"id": i, "name": n} for n, i in self.liste.items()]
            return _R(200, {"lists": voci[params.get("offset", 0):params.get("offset", 0) + params["limit"]]})
        if url == API + "/contacts/folders":
            return _R(200, {"folders": self.cartelle})
        raise AssertionError("GET non previsto: " + url)

    def post(self, url, json=None, **k):
        self.chiamate.append(("POST", url.replace(API, ""), json))
        m = re.fullmatch(API + r"/emailCampaigns/(\d+)/exportRecipients", url)
        if m:
            cid, tipo = int(m.group(1)), json["recipientsType"]
            if self.export.get((cid, tipo)) is None:
                return _R(404, {"code": "document_not_found"})
            pid = 900 + len(self.processi)
            self.processi[pid] = (cid, tipo)
            return _R(202, {"processId": pid})
        if url == API + "/contacts/lists":
            assert json["folderId"] == 5
            lid = 300 + len(self.create)
            self.create.append(json["name"])
            self.liste[json["name"]] = lid
            return _R(201, {"id": lid})
        m = re.fullmatch(API + r"/contacts/lists/(\d+)/contacts/add", url)
        if m:
            assert 0 < len(json["emails"]) <= 150 and set(json) == {"emails"}
            r = self.aggiunta(json["emails"])
            if r.status_code < 300:
                self.aggiunti.append((int(m.group(1)), list(json["emails"])))
            return r
        raise AssertionError("POST non previsto: " + url)

    def scarica(self, url):
        return self.file[url].encode()


class _Log:
    def __init__(self):
        self.righe = []

    def warning(self, msg, *a):
        self.righe.append(msg % a if a else msg)


class _Db:
    def __init__(self, disiscritti=()):
        self.disiscritti = set(disiscritti)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, q, p=()):
        assert q == "SELECT email_hash FROM disiscritti"
        return type("C", (), {"fetchall": lambda s: [{"email_hash": h} for h in self.disiscritti]})()


def _csv(emails):
    return "Email;NOME\n" + "".join(f"{e};Salone\n" for e in emails)


def _giro(brevo, v="non_aperti:101,102:Skovo non aperti 26-11", db=None, monkeypatch=None, env=None):
    log, pause = _Log(), []
    monkeypatch.setattr(NA, "lg", log)
    monkeypatch.setattr(NA.os, "environ", env or {})
    try:
        esito = NA.lavoro(v, client=brevo, connetti=lambda: db or _Db(), dormi=pause.append, scarica=brevo.scarica)
    except Exception as e:
        esito = e
    return esito, log, pause


def test_giro_intero_due_campagne_blocchi_da_150_e_solo_conteggi_nel_log(monkeypatch):
    tanti = [f"s{i}@salone{i}.it" for i in range(320)]
    brevo = Brevo(
        export={(101, "nonOpeners"): _csv(tanti[:200] + ["aperto102@salone.it", "nero@salone.it", "info@loconte.it"]),
                (101, "openers"): _csv(["chi@apre.it"]),
                (102, "nonOpeners"): _csv(tanti[150:] + ["via@brevo.it", "no@risposto.it", "locale@salone.it", "duro@smtp.it"]),
                (102, "openers"): _csv(["aperto102@salone.it"])},
        contatti=[{"email": e} for e in tanti] + [{"email": "Aperto102@salone.it"}, {"email": "nero@salone.it", "emailBlacklisted": True},
                                                  {"email": "info@loconte.it"}, {"email": "no@risposto.it"},
                                                  {"email": "locale@salone.it"}, {"email": "duro@smtp.it"}],
        bloccati=["duro@smtp.it"], liste={"Un'altra lista": 12})
    esito, log, pause = _giro(brevo, db=_Db({invii.impronta("locale@salone.it")}), monkeypatch=monkeypatch,
                              env={"RINVIO_ESCLUSI": "no@risposto.it"})
    assert not isinstance(esito, Exception), esito
    assert esito["campagne"] == {"101": {"non_aperti": 203, "aperti": 1}, "102": {"non_aperti": 174, "aperti": 1}}
    assert esito["totale"] == 327 and esito["da_aggiungere"] == 320 and esito["aggiunti"] == 320
    assert esito["tolti"] == {"aperto_una_campagna": 1, "nostri": 0, "rinvio_esclusi": 1, "commercianti_esclusi": 1,
                              "disiscritti": 2, "rimbalzi_definitivi": 1, "non_in_brevo": 1}
    assert esito["lista"] == 300 and esito["lista_nuova"] and brevo.create == ["Skovo non aperti 26-11"]
    assert esito["blocchi"] == 3 and esito["blocchi_falliti"] == 0 and esito["rifiutati"] == 0
    assert [len(e) for _, e in brevo.aggiunti] == [150, 150, 20] and {l for l, _ in brevo.aggiunti} == {300}
    assert sorted(e for _, b in brevo.aggiunti for e in b) == sorted(tanti)
    # le quattro esportazioni, con il tipo giusto, prima di ogni altra cosa
    richieste = [(u, j["recipientsType"]) for m, u, j in brevo.chiamate if u.endswith("/exportRecipients")]
    assert richieste == [("/emailCampaigns/101/exportRecipients", "nonOpeners"), ("/emailCampaigns/101/exportRecipients", "openers"),
                         ("/emailCampaigns/102/exportRecipients", "nonOpeners"), ("/emailCampaigns/102/exportRecipients", "openers")]
    ordine = [u for _, u, _ in brevo.chiamate]
    assert max(i for i, u in enumerate(ordine) if "exportRecipients" in u or "/processes/" in u) < ordine.index("/contacts/lists")
    # nel log: una riga NON_APERTI con soli conteggi, mai un indirizzo né l'indirizzo firmato del file
    assert len(log.righe) == 1 and log.righe[0].startswith("NON_APERTI {")
    assert json.loads(log.righe[0][len("NON_APERTI "):]) == esito
    assert "@" not in log.righe[0] and "SEGRETA" not in log.righe[0] and "file.example" not in log.righe[0]
    assert pause.count(NA.PASSO_S) == 4


def test_lista_gia_esistente_si_riusa_e_i_gia_dentro_non_sono_un_errore(monkeypatch):
    brevo = Brevo(export={(7, "nonOpeners"): _csv(["a@x.it", "b@y.it"]), (7, "openers"): "Email\n"},
                  contatti=[{"email": "a@x.it"}, {"email": "b@y.it"}], liste={"Lista": 44},
                  aggiunta=lambda emails: _R(201, {"contacts": {"success": ["a@x.it"], "failure": ["b@y.it"]}}))
    esito, log, _ = _giro(brevo, "non_aperti:7:Lista", monkeypatch=monkeypatch)
    assert esito["lista"] == 44 and esito["lista_nuova"] is False and not brevo.create
    assert esito["aggiunti"] == 1 and esito["rifiutati"] == 1 and esito["blocchi_falliti"] == 0
    # un secondo giro: Brevo risponde 400 (tutti già nella lista): finito lo stesso, niente da riprovare
    brevo2 = Brevo(export=brevo.export, contatti=brevo.contatti, liste={"Lista": 44},
                   aggiunta=lambda emails: _R(400, {"code": "invalid_parameter"}))
    esito, log, _ = _giro(brevo2, "non_aperti:7:Lista", monkeypatch=monkeypatch)
    assert not isinstance(esito, Exception) and esito["aggiunti"] == 0 and esito["rifiutati"] == 2


def test_esportazione_non_riuscita_errore_chiaro_e_nessuna_lista(monkeypatch):
    casi = {None: "Brevo non l'ha accettata (HTTP 404)", "fallita": "finito in 'failed'", "mai": "non pronta dopo 10 minuti",
            "<html>Access Denied</html>\n<p>no</p>": "file non leggibile"}
    for cosa, frase in casi.items():
        brevo = Brevo(export={(101, "nonOpeners"): _csv(["a@x.it"]), (101, "openers"): "Email\n", (102, "nonOpeners"): cosa,
                              (102, "openers"): "Email\n"}, contatti=[{"email": "a@x.it"}])
        esito, log, pause = _giro(brevo, monkeypatch=monkeypatch)
        assert isinstance(esito, NA.Fermo) and "nonOpeners della campagna 102" in str(esito) and frase in str(esito), (cosa, esito)
        assert not brevo.create and not brevo.aggiunti                              # niente liste a metà
        assert not any(u.startswith("/contacts") for _, u, _ in brevo.chiamate)
        assert log.righe[-1].startswith("NON_APERTI ERRORE: esportazione nonOpeners della campagna 102")
        assert "Nessun contatto aggiunto" in log.righe[-1] and "SEGRETA" not in " ".join(log.righe)
        # richiesta due volte prima di arrendersi
        assert sum(1 for _, u, j in brevo.chiamate if u == "/emailCampaigns/102/exportRecipients") == NA.TENTATIVI_EXPORT
        if cosa == "mai":
            assert pause.count(NA.PASSO_S) == 2 + 2 * (NA.ATTESA_S // NA.PASSO_S)   # al massimo 10 minuti per tentativo


def test_anche_l_esportazione_di_chi_ha_aperto_deve_riuscire(monkeypatch):
    brevo = Brevo(export={(101, "nonOpeners"): _csv(["a@x.it"]), (101, "openers"): None}, contatti=[{"email": "a@x.it"}])
    esito, log, _ = _giro(brevo, "non_aperti:101:Lista", monkeypatch=monkeypatch)
    assert isinstance(esito, NA.Fermo) and "openers della campagna 101" in str(esito) and not brevo.create


def test_scarico_non_riuscito_senza_l_indirizzo_firmato_nel_log(monkeypatch):
    brevo = Brevo(export={(101, "nonOpeners"): _csv(["a@x.it"]), (101, "openers"): "Email\n"}, contatti=[{"email": "a@x.it"}])

    def get_finto(url, **k):
        raise ConnectionError("non raggiungo " + url)
    monkeypatch.setattr(NA.httpx, "get", get_finto)
    log = _Log()
    monkeypatch.setattr(NA, "lg", log)
    try:
        NA.lavoro("non_aperti:101:Lista", client=brevo, connetti=lambda: _Db(), dormi=lambda s: None)   # scarico vero (finto httpx)
        assert False
    except NA.Fermo as e:
        assert "file non scaricato (ConnectionError)" in str(e)
    assert not brevo.create and "SEGRETA" not in " ".join(log.righe) and "file.example" not in " ".join(log.righe)
    # e con una risposta che non è 200
    monkeypatch.setattr(NA.httpx, "get", lambda url, **k: _R(403))
    try:
        NA.lavoro("non_aperti:101:Lista", client=brevo, connetti=lambda: _Db(), dormi=lambda s: None)
        assert False
    except NA.Fermo as e:
        assert "HTTP 403 allo scarico" in str(e)
    # lo scarico buono passa da httpx.get senza la chiave di Brevo
    visto = {}

    def get_buono(url, **k):
        visto.update(url=url, k=k)
        return type("R", (), {"status_code": 200, "content": b"Email\na@x.it\n" if "nonOpeners" in url else b"Email\n"})()
    monkeypatch.setattr(NA.httpx, "get", get_buono)
    esito = NA.lavoro("non_aperti:101:Lista", client=brevo, connetti=lambda: _Db(), dormi=lambda s: None)
    assert esito["aggiunti"] == 1 and visto["url"].startswith("https://file.example/") and "headers" not in visto["k"]


def test_nessuno_da_aggiungere_non_crea_la_lista(monkeypatch):
    brevo = Brevo(export={(101, "nonOpeners"): _csv(["a@x.it"]), (101, "openers"): _csv(["a@x.it"])}, contatti=[{"email": "a@x.it"}])
    esito, log, _ = _giro(brevo, "non_aperti:101:Lista", monkeypatch=monkeypatch)
    assert esito["lista"] is None and esito["aggiunti"] == 0 and not brevo.create and log.righe[0].startswith("NON_APERTI {")


def test_blocco_non_aggiunto_il_lavoro_resta_da_fare(monkeypatch):
    brevo = Brevo(export={(101, "nonOpeners"): _csv(["a@x.it"]), (101, "openers"): "Email\n"}, contatti=[{"email": "a@x.it"}],
                  aggiunta=lambda emails: _R(500, {}))
    esito, log, _ = _giro(brevo, "non_aperti:101:Lista", monkeypatch=monkeypatch)
    assert isinstance(esito, RuntimeError) and "1 blocchi non aggiunti" in str(esito)
    assert json.loads(log.righe[-1][len("NON_APERTI "):])["blocchi_falliti"] == 1


def test_senza_chiave_di_brevo_si_ferma(monkeypatch):
    monkeypatch.setattr(NA, "_chiave", lambda: "")
    monkeypatch.setattr(NA, "lg", _Log())
    try:
        NA.lavoro("non_aperti:101:Lista")
        assert False
    except NA.Fermo as e:
        assert "BREVO_API_KEY" in str(e)


# ---------- la voce nella coda dei lavori ----------
def test_voce_solo_nel_sito_subito_e_una_volta_per_etichetta(monkeypatch):
    v = "non_aperti:101,102:Skovo non aperti 26-11"
    assert CL.SPOSTABILI["non_aperti:"] == (False, CL.BREVO) and CL._prefisso(v) == "non_aperti:"
    for modo in ("lunghi", "tutti", "0"):                       # mai nella coda del cron: la fa il sito appena parte
        monkeypatch.setenv("LAVORI_NEL_CRON", modo)
        assert not CL.spostabile(v)
    assert v.startswith(CL.SOLO_SITO) and "+" not in v
    viste, fatti = [], []

    def lungo(chiave, nome_log, fn):
        viste.append((chiave, nome_log))
        fn()
        return True
    monkeypatch.setattr(CL, "_lungo", lungo)
    monkeypatch.setattr(NA, "lavoro", lambda voce: fatti.append(voce))
    assert CL.esegui(v) is True
    assert viste == [("non_aperti:101,102:Skovo non aperti 26-11", "NON_APERTI")] and fatti == [v]
