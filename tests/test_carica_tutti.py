"""24/11 (cliente: "carica tutti tutti tutti quelli che abbiamo in database tranne quelli esclusi"): lavoro una
tantum carica_tutti:<etichetta>. Brevo e database finti: esclusioni, niente email nei log, a blocchi, una volta."""
import contextlib
import json
import logging
import types

import pytest

httpx = pytest.importorskip("httpx")
from dealfinder.marketing import brevo_liste as bl
from dealfinder.marketing.invii import impronta

ESCLUSE = ["info@loconte.it", "vendite@officinemulino.it", "a@cosvalauto.it", "b@autoeautovarese.it", "c@srauto.it",
           "info@gtautomobili.it", "info@gt-automobili.com", "milano@privacar.it", "ciao@vroom.it",
           "info@officine-mulino.it", "x@sr-auto.it", "y@auto-e-auto-varese.it", "z@cosval.it"]
SPAZZATURA = ["abc@sentry.io", "x@sentry.wixpress.com", "news@mailchimp.com", "a@sendgrid.net", "b@brevo.com",
              "nome@example.com", "c@gestionaleauto.com", "d@carmove.it", "e@motork.io", "info@dominio.it",
              "info@tuosito.it", "f@test.it", "info@skovo.app", "noreply@salone.it", "no-reply@salone.it",
              "mailer-daemon@salone.it", "postmaster@salone.it", "privacy@salone.it", "dpo@salone.it",
              "webmaster@salone.it", "unsubscribe@salone.it", "support@autoscout24.it",
              "0123456789abcdef0123456789abcdef@salone.it", "u003einfo@salone.it", "20info@salone.it",
              "3evendite@salone.it", "a" * 55 + "@salone.it", "g@subito.it"]


def test_scegli_tutti_esclusioni_e_nomi():
    voci = [(e, None) for e in ESCLUSE] + [
        ("Info@Salone.it", None), ("info@salone.it", "Salone  Rossi"), ("x@pec.it", None), ("y@legalmail.it", None),
        ("via@d.it", "D"), ("rimbalzo@e.it", None), ("logo@2x.png", None), ("nonemail", None), ("", None),
        ("buono@f.com", None), ("perbene@g.it", "Privacar Monza"), ("altro@h.it", "Autosalone Loconte"),
        # un nome buono visto prima non salva l'indirizzo: basta UNO dei nomi escluso
        ("doppio@i.it", "Autosalone Bianchi"), ("doppio@i.it", "GT Automobili srl"),
        ("doppio@l.it", "Auto Verdi"), ("doppio@l.it", "Loconte Auto"), ("tolto@m.it", "M")]
    out, n = bl.scegli_tutti(voci, {impronta("via@d.it"), impronta("rimbalzo@e.it")}, {"tolto@m.it"})
    assert out == [{"email": "buono@f.com", "nome": ""}, {"email": "info@salone.it", "nome": "Salone Rossi"},
                   {"email": "tolto@m.it", "nome": "M"}]
    assert n["non_valide"] == 2 and n["escluse_pec"] == 2 and n["escluse_disiscritte_o_rimbalzate"] == 2
    assert n["escluse_loconte"] == 3 and n["escluse_altri_esclusi"] == len(ESCLUSE) - 1 + 2
    assert n["rimessi_cancellati"] == 1 and "escluse_spazzatura" not in n          # rientra: "tutti tutti tutti"
    assert n["da_caricare"] == 3 and n["con_nome"] == 2


def test_spazzatura_fuori_e_commercianti_dentro():
    buone = ["info@salone.it", "support@salone.it", "assistenza@autorossi.it", "vendite@gmail.com", "3emme@libero.it",
             "2000auto@tiscali.it", "privacyauto@salone.it", "salone@googlemail.com", "rossi@aruba.it",
             "info@autorossi.altervista.org", "20auto@libero.it", "22auto@gmail.com", "27auto@tiscali.it"]
    out, n = bl.scegli_tutti([(e, None) for e in SPAZZATURA + buone], set())
    assert [x["email"] for x in out] == sorted(buone)
    assert n["escluse_spazzatura"] == len(SPAZZATURA)


def test_esclusi_ci_sono_tutti():
    for e in ESCLUSE[1:]:
        assert bl._esclusi(e), e.split("@")[1]
    assert not bl._esclusi("info@salone.it")


def test_righe_di_privati_mai():
    for falso in ("false", '"false"', "0", '"False"'):
        assert bl.voci_da_riga('{"nome": "Mario", "azienda": %s, "email": "m@x.it"}' % falso) is None
    assert bl.voci_da_riga('{"nome": "Salone", "azienda": true, "email": ["s@x.it", "t@x.it"]}') == [
        ("s@x.it", "Salone"), ("t@x.it", "Salone")]
    assert bl.voci_da_riga('{"riga": "tel; z@x.it"}') == [("z@x.it", None)]
    assert bl.voci_da_riga('{"companyName": "Rossi Auto", "contatti": {"email": "r@x.it"}}') == [("r@x.it", "Rossi Auto")]


def test_riga_che_nomina_un_escluso():
    # il nome è in una chiave qualsiasi (o solo nel sito): l'indirizzo esce segnato ed è escluso dovunque compaia
    righe = ['{"companyName": "Privacar Milano", "email": "a@buono1.it"}', '{"ragione": "LOCONTE SRL", "email": "b@buono2.it"}',
             '{"sito": "https://www.gt-automobili.it", "email": "c@buono3.it"}', '{"nome": "Vroom", "email": "d@buono4.it"}',
             '{"email": "a@buono1.it"}', '{"nome": "Salone", "email": "e@buono5.it"}',
             # scritti in altri modi, o solo nel sito / in un campo qualsiasi
             '{"nome": "Auto&Auto - Varese", "email": "f@buono6.it"}', '{"nome": "G.T. Automobili", "email": "g@buono7.it"}',
             '{"nome": "S R Automobili", "email": "h@buono8.it"}', '{"x": "Lo-Conte auto", "email": "i@buono9.it"}',
             '{"sito": "www.auto-e-auto-varese.it", "email": "l@buono10.it"}', '{"x": "officine_mulino", "email": "m@buono11.it"}',
             '{"email": "s.r.auto@gmail.com"}',
             # non sono loro: Carlo Conte, "lo contesto", una srl qualsiasi
             '{"nome": "Carlo Conte Auto s.r.l. automobili", "note": "lo contesto", "email": "n@buono12.it"}']
    voci = [v for r in righe for v in bl.voci_da_riga(r)]
    out, n = bl.scegli_tutti(voci, set())
    assert [x["email"] for x in out] == ["e@buono5.it", "n@buono12.it"]
    assert n["escluse_loconte"] == 2 and n["escluse_altri_esclusi"] == 9


def test_stesso_dominio_di_un_escluso():
    voci = [("info@salonex.it", "Privacar Bergamo"), ("vendite@salonex.it", None), ("altro@salonex.it", "Altro nome"),
            ("mario@gmail.com", "GT Automobili"), ("luigi@gmail.com", None), ("a@libero.it", "Loconte"), ("b@libero.it", None),
            ("capo@autoy.it", "Loconte srl"), ("ufficio@autoy.it", None), ("ok@salonez.it", "Salone Z")]
    out, n = bl.scegli_tutti(voci, set())
    assert [x["email"] for x in out] == ["b@libero.it", "luigi@gmail.com", "ok@salonez.it"]   # gmail e libero sono di tutti
    assert n["escluse_stesso_dominio_di_un_escluso"] == 3 and n["escluse_loconte"] == 2 and n["escluse_altri_esclusi"] == 2


def test_email_senza_resti_di_a_capo():
    # nel JSON scritto l'a capo è \\n: "tel\\ninfo@x.it" non deve dare "ninfo@x.it" né "ttel@"
    riga = json.dumps({"nome": "S", "note": "chiama\ninfo@x.it\ttel@y.it", "altri": [{"e": "\r\nvendite@z.it"}]})
    assert "\\ninfo@" in riga
    assert sorted(e for e, _ in bl.voci_da_riga(riga)) == ["info@x.it", "tel@y.it", "vendite@z.it"]
    assert sorted(bl.email_nel_testo(riga)) == ["info@x.it", "tel@y.it", "vendite@z.it"]
    assert bl.email_nel_testo("testo \\ninfo@x.it") == ["info@x.it"]          # anche in un testo che non è JSON
    r = bl.conta_email([riga], set(), set())
    assert r["totale"] == 3 and r["usabili"] == 3


def test_limite_piano_niente_stime(monkeypatch):
    monkeypatch.delenv("BREVO_LIMITE_PIANO", raising=False)
    assert bl._limite_piano() == (500_000, "decisione_cliente")
    monkeypatch.setenv("BREVO_LIMITE_PIANO", "2000")
    assert bl._limite_piano() == (2000, "BREVO_LIMITE_PIANO")
    monkeypatch.setenv("BREVO_LIMITE_PIANO", "boh")
    assert bl._limite_piano() == (500_000, "decisione_cliente")


def test_pec_per_gestore_non_per_tre_lettere():
    for e in ("a@pec.it", "a@legalmail.it", "a@cert.legalmail.it", "a@postecert.it", "a@sicurezzapostale.it",
              "a@actaliscertymail.it", "a@ticertifica.it", "a@arubapec.it", "a@pec.salone.it", "a@salone.pec.it",
              "a@pec.libero.it", "a@x.legalmail.it", "a@ascompec.it", "a@cnapec.it", "a@keypec.it", "a@pecveneto.it",
              "a@dadapec.com", "a@cert.salone.it"):
        assert bl.pec(e), e.split("@")[1]
    for e in ("info@specialcar.it", "info@specauto.com", "a@autopecchia.it", "a@gmail.com"):
        assert not bl.pec(e), e.split("@")[1]
    out, n = bl.scegli_tutti([("info@specialcar.it", None), ("a@arubapec.it", None)], set())
    assert [x["email"] for x in out] == ["info@specialcar.it"] and n["escluse_pec"] == 1
    assert bl.gruppo_dominio("info@specialcar.it") == "pec"       # gli altri lavori restano come prima


def test_privati_anche_annidati():
    for riga in ({"saloni": [{"nome": "A", "email": "a@x.it"}, {"azienda": False, "email": "p@x.it"}]},
                 {"dati": {"venditore": "Privato", "email": "p@x.it"}}, {"x": [[{"seller_type": "privato"}]], "e": "p@x.it"},
                 {"a": {"b": {"azienda": "false"}}, "email": "p@x.it"}, {"a": [{"azienda": 0}], "email": "p@x.it"}):
        assert bl.privato(riga) and bl.voci_da_riga(json.dumps(riga)) is None    # in dubbio, tutta la riga fuori
    assert not bl.privato({"azienda": True, "venditore": "commerciante", "note": "compra da privato"})
    assert not bl.privato({"azienda": "Salone Rossi srl", "x": [{"nome": "y"}]})


def test_contatti_di_skovo_tutti_mai_cancellati():
    class C:
        def __init__(self, pagine):
            self.pagine, self.off = pagine, []

        def get(self, url, params=None):
            self.off.append(params["offset"])
            return _R({"lists": self.pagine[params["offset"] // 50]})
    altre = [{"name": f"Skovo saloni {i}", "id": i} for i in range(50)]
    c = C([altre, [{"name": "Skovo tutti 24-11", "id": 99}, {"name": "Skovo tutti 30-11", "id": 120},
                   {"name": "Altra Skovo tutti", "id": 5}]])
    ids = bl.liste_tutti(c)
    assert ids == {99, 120} and c.off == [0, 50]
    assert bl.protetto({"email": "x", "listIds": [3, 99]}, ids) and not bl.protetto({"email": "x", "listIds": [3]}, ids)
    assert not bl.protetto({"email": "x"}, ids) and not bl.protetto({"email": "x", "listIds": [99]}, set())
    # i lavori che cancellano contatti da Brevo "per fare posto" saltano i protetti
    import inspect
    from dealfinder.marketing import auto_invio, su_misura
    testo = inspect.getsource(su_misura)
    assert testo.count("c.delete(") == 3 and testo.count("and not _protetto(x, protette):") == 3
    assert testo.count("protette = _liste_tutti(c)") == 3
    assert "not protetto(x, protette)" in inspect.getsource(bl.mix_e_invia)
    assert "if e in protetti:" in inspect.getsource(auto_invio.nuovi_3_11)


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


class _Rete(Exception):
    pass


class _R:
    def __init__(self, dati=None, status=200, headers=None):
        self._d, self.status_code, self.headers = dati or {}, status, headers or {}

    def json(self):
        return self._d

    def raise_for_status(self):
        assert self.status_code < 300


class _Brevo:
    def __init__(self, contatti, conto=None, risposte_import=None, attributi=("AZIENDA",)):
        self.contatti, self.post_, self.get_ = contatti, [], []
        self.conto = conto or {"plan": [{"type": "subscription"}], "planVerticals": [
            {"planCategory": "Marketing", "planType": "paid", "name": "Starter", "status": "active"}]}
        self.risposte_import = list(risposte_import or [])       # codici (o eccezioni) prima delle risposte buone
        self.attributi = list(attributi)

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
        if u == "/contacts/attributes":
            return _R({"attributes": [{"name": a} for a in self.attributi]})
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
            if self.risposte_import:
                x = self.risposte_import.pop(0)
                if isinstance(x, Exception):
                    raise x
                return _R({}, x, {"Retry-After": "2"} if x == 429 else {})
            return _R({"processId": 1000 + len(self.post_)}, 202)
        if u.startswith("/contacts/attributes/"):
            return _R({}, 400)                                    # la creazione non riesce: i nomi non si mandano
        raise AssertionError("chiamata non prevista: " + u)       # mai campagne, mai invii


class _Cur:
    def __init__(self, righe):
        self.righe = list(righe)

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return None

    def fetchmany(self, n):
        out, self.righe = self.righe[:n], self.righe[n:]
        return out


class _Conn:
    def __init__(self, tabelle):
        self.t, self.sql, self.cursori, self.segnati = tabelle, [], 0, set()

    def _righe(self, sql):
        self.sql.append(sql)
        for nome, righe in self.t.items():
            if f"FROM {nome}" in sql:
                return righe
        if sql.startswith(("INSERT INTO disiscritti", "SET LOCAL", "CREATE TABLE IF NOT EXISTS brevo_tutti_caricati")):
            return []
        raise RuntimeError("tabella che manca")

    def execute(self, sql, par=None):
        if sql.startswith("INSERT INTO brevo_tutti_caricati"):
            self.segnati |= set(par[0])
            return _Cur([])
        return _Cur(self._righe(sql))

    def cursor(self, name=None):
        assert name                                               # cursore sul server: lettura a blocchi
        conn = self
        conn.cursori += 1

        class C(_Cur):
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def execute(s, sql):
                s.righe = list(conn._righe(sql))
        return C([])

    def commit(self):
        pass

    def rollback(self):
        pass


def _prepara(monkeypatch, contatti, tabelle, **k):
    import dealfinder.db as db
    brevo = _Brevo(contatti, **k)
    conn = _Conn(tabelle)
    brevo.conn = conn
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-finta")
    monkeypatch.delenv("BREVO_LIMITE_PIANO", raising=False)
    # httpx finto per intero: altri test lo sostituiscono nel modulo, qui servono solo Client e HTTPError
    monkeypatch.setattr(bl, "httpx", types.SimpleNamespace(Client=lambda **kk: brevo, HTTPError=_Rete))
    monkeypatch.setattr(db, "connect", lambda: contextlib.nullcontext(conn))
    monkeypatch.setattr(bl, "_bloccati_0111", lambda: {f"h{i}" for i in range(bl.BLOCCATI_MINIMI)})
    return brevo


def _tabelle():
    buone = [f"salone{i}@concessionaria{i}.it" for i in range(1200)]
    vecchi = [f"vecchio{i}@cancellato{i}.it" for i in range(30)]          # caricati in Brevo e poi cancellati
    return buone, {
        "lead_consegne": [{"d": json.dumps({"nome": f"Salone {i}", "email": e})} for i, e in enumerate(buone[:700])]
        + [{"d": json.dumps({"nome": "Loconte", "email": ESCLUSE[0]})}, {"d": json.dumps({"riga": " ".join(ESCLUSE[:9])})},
           {"d": json.dumps({"nome": "Privato", "azienda": False, "email": "privato@casa.it"})},
           {"d": json.dumps({"companyName": "Privacar Lecco", "email": "sede@lecco-auto.it"})},
           {"d": json.dumps({"note": "scrivi a\nnuovo@inpiu.it"})}, {"d": json.dumps({"email": "abc@sentry.io"})},
           {"d": json.dumps({"email": "studio@pec.it"})}],
        "lead_email e": [{"email": [e, "via@disiscritto.it"], "nome": None} for e in buone[600:1195]]
        + [{"email": vecchi, "nome": "Vecchi"}],
        "disiscritti": [{"email_hash": impronta("via@disiscritto.it")}],
        "brevo_inviati": [{"email": e} for e in vecchi + [buone[0].upper()]],
    }


def test_carica_tutti_a_blocchi_senza_esclusi_e_senza_email_nei_log(monkeypatch):
    buone, tabelle = _tabelle()
    contatti = [{"email": e} for e in buone[1195:]] + [
        {"email": "Bloccato@Brevo.it", "emailBlacklisted": True}, {"email": "x@legalmail.it"}, {"email": "info@skovo.app"},
        {"email": buone[0], "attributes": {"AZIENDA": "Nome già in Brevo"}},
        {"email": "inbrevo@autox.it", "attributes": {"AZIENDA": "Officine Mulino"}}]
    brevo = _prepara(monkeypatch, contatti, tabelle)
    with _Log() as caplog:
        esito = bl.carica_tutti(attesa_s=1, dormi=lambda s: None)
        logging.getLogger("t").warning("CARICA_TUTTI %s", json.dumps(esito))
    imp = [j for u, j in brevo.post_ if u == "/contacts/import"]
    assert [len(j["jsonBody"]) for j in imp] == [500, 500, 231]                     # a blocchi, niente tetto a 1.495
    assert all(j["listIds"] == [99] and j["updateExistingContacts"] is True and j["emptyContactsAttributes"] is False
               for j in imp)
    caricate = [x["email"] for j in imp for x in j["jsonBody"]]
    vecchi = [x["email"] for x in tabelle["brevo_inviati"]][:30]
    assert sorted(caricate) == sorted(buone + ["nuovo@inpiu.it"] + vecchi)                   # mai "nnuovo@" dall'a capo
    assert ("/contacts/lists", {"name": "Skovo tutti 24-11", "folderId": 7}) in brevo.post_
    assert not any("ampaign" in u or "smtp" in u for u, _ in brevo.post_)            # nessun invio
    con_nome = [x for j in imp for x in j["jsonBody"] if "attributes" in x]
    assert len(con_nome) == 729 and all(x["attributes"]["AZIENDA"].startswith(("Salone ", "Vecchi")) for x in con_nome)
    assert buone[0] not in [x["email"] for x in con_nome]          # l'AZIENDA di chi è già in Brevo non si tocca
    assert esito["caricati"] == 1231 and esito["lista_id"] == 99 and esito["blocchi"] == 3
    assert esito["nomi_mandati"] == 729 and esito["import_codici"] == {"202": 3} and esito["blocchi_falliti"] == 0
    assert esito["escluse_loconte"] == 9 and esito["escluse_altri_esclusi"] == 2
    assert esito["escluse_pec"] == 2 and esito["escluse_disiscritte_o_rimbalzate"] == 2
    assert esito["escluse_spazzatura"] == 2 and esito["rimessi_cancellati"] == 30       # tolti per il vecchio tetto: rientrano
    assert esito["privati_esclusi"] == 1 and len(esito["process_id"]) == 3
    assert esito["processi"] == {"completed": 3} and esito["nella_lista"] == 1203 and esito["limite_piano"] == 500_000
    assert esito["limite_fonte"] == "decisione_cliente" and esito["processi_finiti"] is True and esito["piano"] == "Starter"
    assert "@" not in caplog.text and "@" not in json.dumps(esito)                   # nei log solo conteggi
    assert brevo.conn.segnati == set(caricate) and bl.incompleto(esito) is None      # marcatore per auto_invio
    # memoria: solo le righe con una @, cursore a blocchi, tempo massimo
    assert any("FROM lead_consegne WHERE strpos(dati::text, '@') > 0" in q for q in brevo.conn.sql)
    assert brevo.conn.cursori == 3 and any("statement_timeout = '120s'" in q for q in brevo.conn.sql)


def test_import_riprova_su_429_e_5xx_e_conta_i_codici(monkeypatch):
    _, tabelle = _tabelle()
    attese = []
    brevo = _prepara(monkeypatch, [], tabelle, risposte_import=[429, 503, _Rete("giù"), 500, 500, 500, 400],
                     attributi=())
    esito = bl.carica_tutti(attesa_s=0, dormi=attese.append)
    # blocco 1: 429, 503, poi errore di rete -> perso; blocco 2: 500 x3 -> perso; blocco 3: 400 -> perso senza riprovare
    assert esito["blocchi"] == 3 and esito["blocchi_falliti"] == 3 and esito["caricati"] == 0
    assert esito["import_codici"] == {"429": 1, "503": 1, "rete__Rete": 1, "500": 3, "400": 1}
    assert attese[0] == 2.0 and len(attese) == 4                                    # Retry-After rispettato
    assert esito["nomi_mandati"] == 0                                               # attributo AZIENDA non verificato
    brevo = _prepara(monkeypatch, [], tabelle, risposte_import=[429, 502], attributi=())
    esito = bl.carica_tutti(attesa_s=0, dormi=attese.append)
    assert esito["blocchi_falliti"] == 0 and esito["caricati"] == 1226 and esito["import_codici"]["202"] == 3
    assert not any("attributes" in x for u, j in brevo.post_ if u == "/contacts/import" for x in j["jsonBody"])


def test_carica_tutti_fermo_se_il_piano_non_basta(monkeypatch):
    _, tabelle = _tabelle()
    brevo = _prepara(monkeypatch, [], tabelle)
    monkeypatch.setenv("BREVO_LIMITE_PIANO", "1000")
    esito = bl.carica_tutti(attesa_s=0)
    assert esito["fermo"] == "piano_insufficiente" and esito["limite_fonte"] == "BREVO_LIMITE_PIANO" and esito["caricati"] == 0 and not brevo.post_


def test_carica_tutti_fermo_senza_i_disiscritti(monkeypatch):
    _, tabelle = _tabelle()
    brevo = _prepara(monkeypatch, [], tabelle)
    monkeypatch.setattr(bl, "_bloccati_0111", lambda: set())
    assert bl.carica_tutti(attesa_s=0)["fermo"] == "disiscritti_incompleti" and not brevo.post_
    tabelle["disiscritti"] = []                                    # tabella dei disiscritti vuota: fermo comunque
    brevo = _prepara(monkeypatch, [], tabelle)
    assert bl.carica_tutti(attesa_s=0)["fermo"] == "disiscritti_incompleti" and not brevo.post_


def test_carica_tutti_senza_brevo_inviati_va_avanti(monkeypatch):
    _, tabelle = _tabelle()
    del tabelle["brevo_inviati"]
    _prepara(monkeypatch, [], tabelle)
    esito = bl.carica_tutti(attesa_s=0, dormi=lambda s: None)
    assert "fermo" not in esito and esito["fonti"]["brevo_inviati"].startswith("non letta")
    assert esito["caricati"] == 1226 and esito["rimessi_cancellati"] == 0


def test_carica_tutti_fermo_se_una_fonte_non_si_legge(monkeypatch):
    for fonte in ("lead_consegne", "lead_email e"):
        _, tabelle = _tabelle()
        del tabelle[fonte]
        brevo = _prepara(monkeypatch, [], tabelle)
        esito = bl.carica_tutti(attesa_s=0, dormi=lambda s: None)
        assert esito["fermo"] == "fonte_non_letta" and not brevo.post_ and bl.incompleto(esito) == "fonte_non_letta"


def test_incompleto_il_lavoro_non_e_fatto():
    ok = {"fonti": {"lead_consegne": 10, "lead_email": 5}, "caricati": 15, "blocchi_falliti": 0,
          "processi": {"completed": 1}, "processi_finiti": True}
    assert bl.incompleto(ok) is None and bl.incompleto({**ok, "processi": {}}) is None
    assert bl.incompleto({**ok, "fermo": "piano_insufficiente"}) == "piano_insufficiente"
    assert bl.incompleto({"errore": "chiave"}) == "chiave"
    assert bl.incompleto({**ok, "fonti": {"lead_consegne": "non letta (X)", "lead_email": 5}}) == "lead_consegne_non_letta"
    assert bl.incompleto({**ok, "fonti": {"lead_consegne": 3}}) == "lead_email_non_letta"
    assert bl.incompleto({**ok, "blocchi_falliti": 1}) == "blocchi_falliti"
    assert bl.incompleto({**ok, "processi": {"completed": 2, "failed": 1}}) == "import_fallito"
    assert bl.incompleto({**ok, "processi": {"completed": 2, "in_queue": 1}}) == "import_non_finito"
    assert bl.incompleto({**ok, "processi": {"in_attesa": 1}}) == "import_non_finito"


class _Lav:
    def __init__(self, esiti):
        self.esiti = esiti

    def fatto(self):
        self.esiti.append("fatto")

    def fallito(self, e):
        self.esiti.append("fallito")


def test_coda_fatta_solo_a_lavoro_finito(monkeypatch):
    from dealfinder.pipeline import ciclo, coda_lavori as cl
    assert cl.SPOSTABILI["carica_tutti:"] == (False, cl.BREVO)
    assert cl.mancano("carica_tutti:2411", {}) == ["BREVO_API_KEY"]
    fatti, esiti, giri = set(), [], []
    fonti = {"lead_consegne": 2, "lead_email": 1}
    risultati = [{"caricati": 3, "blocchi_falliti": 1, "lista_id": 99, "fonti": fonti},
                 {"caricati": 3, "lista_id": 99, "fonti": fonti}, {"errore": "chiave rifiutata"},
                 {"fermo": "disiscritti_incompleti", "fonti": fonti},
                 {"caricati": 3, "fonti": fonti, "processi": {"failed": 1}},
                 {"caricati": 3, "fonti": fonti, "processi": {"in_attesa": 1}}]
    monkeypatch.setattr(ciclo, "inizia", lambda job, **k: None if job in fatti else (giri.append(job) or _Lav(esiti)))
    monkeypatch.setattr(bl, "carica_tutti", lambda: risultati.pop(0))
    with _Log() as caplog:
        assert cl.esegui("carica_tutti:2411") is False            # un blocco fallito: non fatta, si riprova
        assert cl.esegui("carica_tutti:2411") is True
        fatti.add("carica_tutti:2411")
        assert cl.esegui("carica_tutti:2411") is None             # una volta per etichetta
        assert cl.esegui("carica_tutti:2411bis") is False         # chiave rifiutata: non fatta
        assert cl.esegui("carica_tutti:c") is False               # fermo: non fatta
        assert cl.esegui("carica_tutti:d") is False               # un import fallito: non fatta
        assert cl.esegui("carica_tutti:e") is False               # import non finito in tempo: non fatta
    assert esiti == ["fallito", "fatto", "fallito", "fallito", "fallito", "fallito"]
    assert giri[:3] == ["carica_tutti:2411", "carica_tutti:2411", "carica_tutti:2411bis"]
    assert '"lista_id": 99' in caplog.text and "già fatto" in caplog.text and "import_non_finito" in caplog.text


def test_tetto_dei_contatti_e_limiti_di_invio_separati():
    # 24/11 (cliente): 500.000 sono i contatti che si possono TENERE; le email per giro restano quelle di prima
    import inspect
    from dealfinder.marketing import auto_invio, giro_1711, giro_lotto, su_misura
    assert bl.TETTO_BREVO == 500_000 and bl.LIMITE_PIANO == bl.TETTO_BREVO
    assert (bl.TETTO_INVIO, bl.TETTO_INVIO_GIRO, bl.TETTO_INVIO_AUTO) == (1495, 1400, 1450)
    assert su_misura.TETTO_INVIO == 1495 and giro_1711.TETTO_INVIO == 1495
    for modulo in (su_misura, giro_1711):
        assert "TETTO_BREVO" not in inspect.getsource(modulo)
    for fn in (bl.rimetti_e_invia, bl.carica_e_programma, bl.mix_e_invia, bl.lista_pulita):
        t = inspect.getsource(fn)
        assert "TETTO_INVIO - " in t and "TETTO_BREVO" not in t, fn.__name__
    assert "min(TETTO_BREVO - int(n_brevo), TETTO_INVIO_GIRO)" in inspect.getsource(giro_lotto)
    assert auto_invio._tetto() == 500_000
    t = inspect.getsource(auto_invio)
    assert 'tetto = _tetto_invio("AUTO_TETTO_CONTATTI")' in inspect.getsource(auto_invio.ciclo)
    assert 'tetto = _tetto_invio("BREVO_TETTO_2_11")' in inspect.getsource(auto_invio.invia_2_11)
    assert 'tetto = _tetto_invio("BREVO_TETTO", 1498)' in inspect.getsource(auto_invio.nuovi_3_11)
    assert "or _tetto())" not in t


def test_limiti_di_invio_di_auto_invio(monkeypatch):
    from dealfinder.marketing import auto_invio
    for v in ("AUTO_TETTO_CONTATTI", "BREVO_TETTO_2_11", "BREVO_TETTO"):
        monkeypatch.delenv(v, raising=False)
    assert auto_invio._tetto_invio("AUTO_TETTO_CONTATTI") == 1450 and auto_invio._tetto_invio("BREVO_TETTO_2_11") == 1450
    assert auto_invio._tetto_invio("BREVO_TETTO", 1498) == 1498
    monkeypatch.setenv("AUTO_TETTO_CONTATTI", "900")              # la variabile, se c'è, vale più del predefinito
    assert auto_invio._tetto_invio("AUTO_TETTO_CONTATTI") == 900


class _ConnAuto:
    """Database finto per auto_invio.ciclo: archivio dei commercianti, brevo_inviati e il marcatore di carica_tutti."""
    def __init__(self, emails, inviati, marcati):
        self.emails, self.inviati, self.marcati = emails, set(inviati), set(marcati)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, par=None):
        if "FROM lead_email e" in sql:
            return _Cur([{"chiave": f"k{i}", "email": [e], "sito": None} for i, e in enumerate(self.emails)])
        if "FROM brevo_tutti_caricati" in sql:
            return _Cur([{"email": e} for e in self.marcati])
        if "FROM brevo_inviati" in sql:
            return _Cur([{"email": e} for e in self.inviati])
        if sql.startswith("INSERT INTO brevo_inviati"):
            self.inviati.add(par[0])
        return _Cur([])

    def commit(self):
        pass


def _gira_ciclo(monkeypatch, conn, in_brevo, contatti):
    import dealfinder.db as db
    from dealfinder.marketing import auto_invio
    from dealfinder.pipeline import lead_email
    mandate = []

    class C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            assert url.endswith("/contacts/lists"), url
            return _R({"lists": [{"name": "Skovo tutti 24-11", "id": 99}, {"name": "Skovo altra", "id": 3}]})

    class Fine(Exception):
        pass

    def basta(s):
        raise Fine()
    for v in ("AUTO_TETTO_CONTATTI", "AUTO_PROV"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-finta")
    monkeypatch.setenv("AUTO_MIN_N", "1")
    monkeypatch.setenv("AUTO_TESTO_RESTANTI", "100000")           # tutte con il testo corto: non servono le auto del sito
    monkeypatch.setattr(db, "connect", lambda: conn)
    monkeypatch.setattr(auto_invio, "httpx", types.SimpleNamespace(Client=lambda **k: C()))
    monkeypatch.setattr(auto_invio, "_semina", lambda c, conn: None)
    monkeypatch.setattr(lead_email, "giro", lambda: None)
    monkeypatch.setattr(auto_invio, "_conta_brevo", lambda c: in_brevo)
    monkeypatch.setattr(auto_invio, "_contatti_brevo", lambda c: contatti)
    monkeypatch.setattr(auto_invio, "_manda", lambda c, nome, oggetto, html, emails, lavoro=None: mandate.append(list(emails)) or True)
    monkeypatch.setattr(auto_invio, "time", types.SimpleNamespace(sleep=basta, strftime=lambda f: "24-11 1200"))
    try:
        auto_invio.ciclo(lambda: [])
    except Fine:
        pass
    return mandate


def test_auto_invio_non_scrive_a_chi_e_entrato_con_carica_tutti(monkeypatch):
    emails = ["a@salone1.it", "b@salone2.it", "c@salone3.it", "d@salone4.it"]
    # b: segnato da carica_tutti; c: in Brevo nella lista "Skovo tutti" (senza marcatore); d: già caricato prima
    conn = _ConnAuto(emails, inviati={"d@salone4.it"}, marcati={"b@salone2.it"})
    contatti = [{"email": "C@salone3.it", "listIds": [99]}, {"email": "x@altro.it", "listIds": [3]}]
    assert _gira_ciclo(monkeypatch, conn, 100, contatti) == [["a@salone1.it"]]
    assert conn.inviati == {"d@salone4.it", "a@salone1.it"}       # solo a chi ha ricevuto
    # tutti entrati con carica_tutti: nessuna campagna
    conn = _ConnAuto(emails, inviati=set(), marcati=set(emails))
    assert _gira_ciclo(monkeypatch, conn, 100, []) == []
    # dopo carica_tutti Brevo ha migliaia di contatti: il limite di invio è quello di prima (1.450), il giro resta fermo
    conn = _ConnAuto(emails, inviati=set(), marcati=set())
    assert _gira_ciclo(monkeypatch, conn, 4000, []) == [] and not conn.inviati
    # il limite per giro: con 1.447 contatti in Brevo partono al massimo 3 email, non 4
    assert [len(x) for x in _gira_ciclo(monkeypatch, conn, 1447, [])] == [3]


def test_nuovi_3_11_limite_di_invio_e_nessuna_cancellazione_per_fare_posto():
    import inspect
    from dealfinder.marketing import auto_invio
    t = inspect.getsource(auto_invio.nuovi_3_11)
    # i "mai aperto" (e le letture campaignStats) si fermano al posto rimasto sotto il limite di invio; chi sta in
    # "Skovo tutti" non riceve; si cancella solo oltre i contatti che si possono tenere (500.000)
    assert "if ESCLUSI_3_11.search(e) or e in protetti or len(mai_aperte) >= posto:" in t
    assert t.index("len(mai_aperte) >= posto") < t.index("/campaignStats")
    assert "cand = cand[:tetto]" in t and "da_togliere = max(0, len(attuali) + len(cand) - _tetto())" in t
    assert "scelte = scelte[:tetto]" in inspect.getsource(auto_invio.invia_2_11)


def test_nomi_corti_solo_come_parola_intera():
    # _esclusi() serve anche agli altri lavori: non deve portarsi via saloni che hanno solo un nome simile
    for buono in ("Toyota Yaris 1.5 SR automatica", "SR Autonoleggio", "S.R. Autofficina", "Cosvaldo Motors",
                  "Autovroom", "info@autovroom.it", "cosvaldo@gmail.com", "info@sr-autonoleggio.it",
                  "Auto e moto Varese", "Carlo Conte Auto"):
        assert not bl._esclusi(buono) and not bl._nome_escluso(buono), buono
    for escluso in ("SR Auto", "S.R. Auto srl", "S R Automobili", "sr-auto", "c@srauto.it", "x@sr-auto.it", "s.r.auto@gmail.com",
                    "Cosval", "Cosval Auto", "z@cosval.it", "a@cosvalauto.it", "Vroom", "Vroom Italia", "ciao@vroom.it",
                    "Auto & Auto Varese", "Auto&Auto - Varese", "Auto &amp; Auto (Varese)", "Auto + Auto, Varese",
                    "Auto e Auto S.r.l. Varese", "autoeauto@gmail.com", "info@autoeauto.it", "b@autoeautovarese.it"):
        assert bl._nome_escluso(escluso), escluso
    # "auto & auto" in un campo e Varese in un altro della stessa riga
    riga = '{"nome": "Auto & Auto", "comune": "Varese", "email": "info@salonevarese.it"}'
    out, n = bl.scegli_tutti(bl.voci_da_riga(riga), set())
    assert out == [] and n["escluse_altri_esclusi"] == 1
    riga = '{"nome": "Auto & Auto", "comune": "Lecce", "email": "info@salonelecce.it"}'             # un altro Auto & Auto
    assert len(bl.scegli_tutti(bl.voci_da_riga(riga), set())[0]) == 1


def test_pec_elenco_esplicito_e_domini_pubblici():
    for e in ("a@autospec.it", "a@carspec.it", "a@aspec.it", "a@specialcar.it"):
        assert not bl.pec(e), e.split("@")[1]
    for e in ("a@legal.email.it", "a@postemailcertificata.it", "a@mailcert.it", "a@pecservizi.it", "a@peclegale.it",
              "a@pecposta.it", "a@ascompec.it", "a@cnapec.it", "a@keypec.it", "a@dadapec.com", "a@pecveneto.it",
              "a@arubapec.it", "a@x.pecposta.it"):
        assert bl.pec(e), e.split("@")[1]
    for d in ("windowslive.com", "tre.it", "wind.it", "infinito.it", "jumpy.it", "lycos.it", "tele2.it", "fastwebmail.it",
              "pm.me", "tutanota.com", "zoho.com", "web.de", "free.fr", "orange.fr", "gmail.com", "libero.it"):
        assert bl._dominio_proprio("x@" + d) == "", d
    assert bl._dominio_proprio("x@salone.it") == "salone.it"
