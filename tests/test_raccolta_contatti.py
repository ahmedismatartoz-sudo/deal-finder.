"""raccolta_contatti (dealfinder/pipeline/raccolta_contatti.py): email e cellulari dai siti dei saloni. Pagine finte in
tests/dati/raccolta_contatti; rete e archivio finti (niente internet); l'SQL vero su Postgres con tests/pg_scheda.py."""
import json
import logging
import os
import sys
import time

from dealfinder import automatismi
from dealfinder.pipeline import coda_lavori as CL
from dealfinder.pipeline import raccolta_contatti as RC

DATI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dati", "raccolta_contatti")
DOM = "brianzamotors-esempio.it"
SITO = f"https://www.{DOM}/"


def _leggi(nome):
    with open(os.path.join(DATI, nome), encoding="utf-8") as f:
        return f.read()


class FintaRete:
    ua = RC.UA

    def __init__(self, pagine, robots=None):
        self.pagine, self.robots, self.chieste, self.tempi = pagine, robots or {}, [], {}

    def get(self, url):
        self.chieste.append(url)
        self.tempi.setdefault(RC.dominio(url), []).append(time.time())
        if url.endswith("/robots.txt"):
            r = self.robots.get(RC.dominio(url))
            return (404, "", None) if r is None else r if isinstance(r, tuple) else (200, r, None)
        r = self.pagine.get(url)
        if r is None:
            return 404, "", None
        if isinstance(r, tuple):
            return r
        return 200, r, None


class FintoArchivio:
    def __init__(self, righe, email_note=(), cell_noti=(), disiscritti=()):
        self.righe = {r["dominio"]: {"tentato": None, "consegnato": False, **r} for r in righe}
        self.note, self.noti, self.dis = set(email_note), set(cell_noti), set(disiscritti)
        self.consegne, self.in_le = [], []

    def prepara(self):
        pass

    def semina(self):
        return {"finta": len(self.righe)}

    def prendi(self, quanti, rifai):
        out = [r for r in self.righe.values() if r["tentato"] is None][:quanti]
        for r in out:
            r["tentato"] = 1
        return [dict(r) for r in out]

    def da_leggere(self):
        return sum(1 for r in self.righe.values() if r["tentato"] is None)

    def non_contattabili(self, emails):
        return set(emails) & self.dis

    def email_note(self, emails, dom):
        altri = {e for d, r in self.righe.items() if d != dom for e in r.get("email") or []}
        return set(emails) & (self.note | altri)

    def cell_noti(self, cell, dom):
        altri = {c for d, r in self.righe.items() if d != dom for c in r.get("cell") or []}
        return set(cell) & (self.noti | altri)

    def salva(self, dom, d):
        self.righe[dom].update(d)

    def in_lead_email(self, rif, emails, sito):
        self.in_le.append(rif)
        return True

    def da_consegnare(self, quanti):
        return [dict(r) for r in self.righe.values()
                if not r["consegnato"] and (r.get("email_nuove") or r.get("cell_nuovi"))][:quanti]

    def cell_su_tanti_siti(self, cell, soglia):
        return {c for c in cell if sum(1 for r in self.righe.values() if c in (r.get("cell") or [])) >= soglia}

    def consegna(self, dom, righe):
        self.consegne += righe
        self.righe[dom]["consegnato"] = True


def _riga(dom=DOM, nome="Brianza Motors Esempio", **k):
    return {"dominio": dom, "sito": f"https://www.{dom}/", "nome": nome, "comune": "Monza", "prov": "MB",
            "origine": "maps", "rif": None, **k}


def _pagine():
    return {SITO: _leggi("home.html"), SITO + "contatti/": _leggi("contatti.html"),
            SITO + "chi-siamo": "<html><body>La nostra storia</body></html>"}


def _giro(monkeypatch, arch, rete, **k):
    monkeypatch.setenv("RACC_PAUSA", "0.2")
    monkeypatch.setenv("RACC_PARALLELI", "2")
    return RC.lavoro(k.pop("minuti", 1), k.pop("max_siti", 100), archivio=arch, rete=rete, **k)


def test_email_solo_aziendali():
    em, n = RC.email_aziendali(_leggi("home.html"), DOM)
    assert em == ["info@brianzamotors-esempio.it"]
    assert n["pec"] == 1 and n["automatiche"] >= 2 and n["di_terzi"] >= 1      # PEC, privacy@/noreply@, agenzia web
    em, _ = RC.email_aziendali(_leggi("contatti.html"), DOM)
    assert em == ["vendite@brianzamotors-esempio.it", "brianzamotors.esempio@gmail.com"]   # prima il dominio del sito
    for brutta in ("posta@pec.salone.it", "salone@legalmail.it", "dpo@salone.it", "webmaster@salone.it",
                   "pec@salone.it", "no-reply@salone.it", "fatture@salone.it"):
        assert RC.email_aziendali(f"<p>{brutta}</p>", "salone.it")[0] == [], brutta
    assert RC.email_aziendali("<p>info [at] salone.it</p>", "salone.it")[0] == ["info@salone.it"]


def test_esclusi_sempre():
    for nome in ("Loconte Auto", "Officine Mulino", "Cosval Auto", "Auto & Auto Varese", "SR Auto", "GT Automobili",
                 "Privacar Monza", "Vroom"):
        assert RC.escluso(nome), nome
    for d in ("cosvalauto.it", "autoeautovarese.it", "srauto.it", "officinemulino.it", "gtautomobili.com"):
        assert RC.escluso(d), d
    assert not RC.escluso("Brianza Motors", DOM)
    assert RC.email_aziendali("<p>info@cosvalauto.it</p>", "cosvalauto.it")[0] == []


def test_cellulari_solo_del_salone():
    c, _ = RC.cellulari_salone(_leggi("contatti.html"))
    assert c == ["3471234567", "3487654321", "3331112223"]            # WhatsApp, tel:, dati strutturati
    c, _ = RC.cellulari_salone(_leggi("home.html"))
    assert c == []                                                    # fisso, P.IVA e numeri negli script: niente
    c, n = RC.cellulari_salone(_leggi("elenco.html"))
    assert c == ["3409998877"] and n["pagine_elenco"] == 1            # pagina-elenco: solo il numero del link


def test_siti_ammessi_e_pagine_utili():
    assert RC.dominio("https://www.Salone.it/contatti?x=1") == "salone.it"
    for u in ("ftp://salone.it", "http://127.0.0.1/", "http://localhost/", "http://salone.it:8080/", "javascript:1", None):
        assert RC.dominio(u) == "", u
    assert not RC.sito_ammesso("facebook.com") and not RC.sito_ammesso("subito.it") and RC.sito_ammesso("salone.it")
    p = RC.pagine_utili(_leggi("home.html"), SITO, DOM, 3)
    assert p == [SITO + "contatti/", SITO + "chi-siamo"]              # niente Facebook, niente pdf, niente /usato
    assert RC.pagine_utili("<html></html>", SITO, DOM, 3) == [SITO + "contatti", SITO + "chi-siamo"]


def test_robots():
    r = RC.Robots(200, "User-agent: *\nDisallow: /contatti\nCrawl-delay: 5\n", RC.UA)
    assert r.permesso(SITO) and not r.permesso(SITO + "contatti/") and r.attesa() == 5.0
    assert not RC.Robots(200, "User-agent: ScovoContatti\nDisallow: /\n", RC.UA).permesso(SITO)
    assert RC.Robots(404, "", RC.UA).permesso(SITO)
    assert not RC.Robots(503, "", RC.UA).permesso(SITO) and not RC.Robots(0, "", RC.UA).permesso(SITO)


def test_giro_completo_solo_conteggi_nei_log(monkeypatch):
    log = logging.getLogger("lead")
    visti = []
    h = logging.Handler()
    h.emit = lambda rec: visti.append(rec.getMessage())
    log.addHandler(h)
    arch = FintoArchivio([_riga(rif="https://www.autoscout24.it/concessionari/brianza"),
                          _riga("cosvalauto.it", "Cosval Auto"), _riga("officina-esempio.it", "Autofficina Rossi"),
                          _riga("facebook.com", "Salone su Facebook"), _riga("chiuso-esempio.it", "Auto Chiuso"),
                          _riga("giacontattato-esempio.it", "Auto Gia Srl"),
                          _riga("lontano-esempio.it", "Auto Lontano", comune="Brescia", prov="BS"),
                          _riga("senzacomune-esempio.it", "Auto Senza Comune", comune=None, prov="BG")],
                         email_note={"info@giacontattato-esempio.it"}, cell_noti={"3331112223"},
                         disiscritti={"brianzamotors.esempio@gmail.com"})
    pagine = _pagine()
    pagine["https://www.giacontattato-esempio.it/"] = "<p>info@giacontattato-esempio.it</p><a href='tel:3381234567'>x</a>"
    rete = FintaRete(pagine, robots={"chiuso-esempio.it": "User-agent: *\nDisallow: /\n"})
    try:
        esito = _giro(monkeypatch, arch, rete)
    finally:
        log.removeHandler(h)
    r = arch.righe[DOM]
    assert r["esito"] == "ok" and r["pagine"] == 2                    # home + contatti: trovati tutti e due, basta
    assert r["email"] == ["info@brianzamotors-esempio.it", "vendite@brianzamotors-esempio.it"]   # il disiscritto no
    assert r["cell"] == ["3471234567", "3487654321", "3331112223"] and r["cell_nuovi"] == ["3471234567", "3487654321"]
    assert arch.righe["cosvalauto.it"]["esito"] == "escluso" and arch.righe["officina-esempio.it"]["esito"] == "non_auto"
    assert arch.righe["facebook.com"]["esito"] == "non_sito" and arch.righe["chiuso-esempio.it"]["esito"] == "robots"
    # oltre 50 km da Milano o senza comune riconoscibile: scartati senza nemmeno una richiesta
    assert arch.righe["lontano-esempio.it"]["esito"] == "fuori_zona"
    assert arch.righe["senzacomune-esempio.it"]["esito"] == "fuori_zona"
    assert not [u for u in rete.chieste if "lontano" in u or "senzacomune" in u]
    g = arch.righe["giacontattato-esempio.it"]
    assert g["email"] and g["email_nuove"] == [] and g["cell_nuovi"] == ["3381234567"]       # email già nostra
    # mai una richiesta ai siti esclusi, a Facebook, né alle pagine di un sito chiuso da robots.txt
    assert not [u for u in rete.chieste if "cosval" in u or "facebook" in u or "officina" in u]
    assert [u for u in rete.chieste if "chiuso-esempio" in u] == ["https://www.chiuso-esempio.it/robots.txt"]
    tipi = [t for t, _ in arch.consegne]
    assert tipi.count(RC.TIPO_EMAIL) == 1 and tipi.count(RC.TIPO_CELL) == 2
    cell = [d for t, d in arch.consegne if t == RC.TIPO_CELL and d["nome"] == "Brianza Motors Esempio"][0]
    assert cell["cellulare"] == "+393471234567" and cell["altri_cellulari"] == "+393487654321"
    assert esito["siti"] == 8 and esito["email_nuove"] == 2 and esito["cell_nuovi"] == 3 and not esito["da_riprendere"]
    assert arch.in_le == []                                            # interruttore spento: lead_email non si tocca
    # nei log le due righe di conteggi e mai un indirizzo o un numero
    testo = "\n".join(visti)
    assert "RACCOLTA_EMAIL {" in testo and "RACCOLTA_CELL {" in testo
    assert "@" not in testo and "347123" not in testo and "348765" not in testo and "338123" not in testo
    assert json.loads(testo.split("RACCOLTA_EMAIL ")[1].split("\n")[0])["nuove"] == 2
    # secondo giro: niente da leggere, niente consegne doppie
    n = len(arch.consegne)
    assert _giro(monkeypatch, arch, FintaRete(pagine))["siti"] == 0 and len(arch.consegne) == n


def test_pausa_per_dominio_e_rinvii(monkeypatch):
    pagine = _pagine()
    pagine["https://www.vecchio-esempio.it/"] = (301, "", SITO)       # rinvio a un altro dominio: robots anche lì
    arch = FintoArchivio([_riga("vecchio-esempio.it", "Auto Vecchio")])
    rete = FintaRete(pagine)
    monkeypatch.setenv("RACC_PAUSA", "0.3")
    RC.lavoro(1, 10, archivio=arch, rete=rete)
    assert arch.righe["vecchio-esempio.it"]["email"] == ["info@brianzamotors-esempio.it", "vendite@brianzamotors-esempio.it",
                                                         "brianzamotors.esempio@gmail.com"]
    assert SITO + "robots.txt" in rete.chieste
    for dom, tt in rete.tempi.items():                                # mai due richieste ravvicinate allo stesso dominio
        assert all(b - a >= 0.28 for a, b in zip(tt, tt[1:])), dom


def test_numero_su_tanti_siti_non_si_consegna(monkeypatch):
    righe = [_riga(f"salone{i}-esempio.it", f"Auto Salone {i}") for i in range(4)]
    pagine = {f"https://www.salone{i}-esempio.it/": "<a href='tel:3400000001'>assistenza</a>" for i in range(4)}
    arch = FintoArchivio(righe)
    _giro(monkeypatch, arch, FintaRete(pagine))
    assert [t for t, _ in arch.consegne] == []                        # numero del fornitore del sito: di nessuno


def test_tetto_di_tempo_e_ripresa(monkeypatch):
    arch = FintoArchivio([_riga(f"s{i}-esempio.it", f"Auto S {i}") for i in range(6)])
    e = _giro(monkeypatch, arch, FintaRete({}), max_siti=2)
    assert e["siti"] == 2 and e["da_leggere"] == 4 and e["da_riprendere"]
    e = _giro(monkeypatch, arch, FintaRete({}))
    assert e["siti"] == 4 and not e["da_riprendere"]


def test_interruttore_lead_email(monkeypatch):
    monkeypatch.setenv("AUTOMATISMO_RACCOLTA_CONTATTI_IN_LEAD_EMAIL", "1")
    arch = FintoArchivio([_riga(rif="chiave-as")])
    _giro(monkeypatch, arch, FintaRete(_pagine()))
    assert arch.in_le == ["chiave-as"]


def test_voce_della_coda_e_rimando(monkeypatch):
    assert CL.lungo("raccolta_contatti:2511") and CL.mancano("raccolta_contatti:2511", {}) == []
    assert CL.durata_prevista("raccolta_contatti:2511", {"RACC_ORE": "1"}) == 75
    monkeypatch.setenv("LAVORI_NEL_CRON", "lunghi")
    assert CL.spostabile("raccolta_contatti:2511:pg200")
    from dealfinder.pipeline import ciclo as C
    stato, chiamate = [], []

    class Lav:
        def fatto(self, s=None):
            stato.append("fatto")

        def rimanda(self, s=None):
            stato.append("rimandato")

        def fallito(self, e):
            stato.append("fallito")
    monkeypatch.setattr(C, "inizia", lambda job, tentativi_max=3: (chiamate.append(job), Lav())[1])
    monkeypatch.setattr(RC, "lavoro", lambda minuti, siti, pg_max=0: (chiamate.append(pg_max),
                                                                   {"siti": 5, "da_leggere": 9, "da_riprendere": True})[1])
    assert CL.esegui("raccolta_contatti:2511:pg200") is False and stato == ["rimandato"]
    assert chiamate == ["raccolta_contatti:2511", 200]
    monkeypatch.setattr(RC, "lavoro", lambda minuti, siti, pg_max=0: {"siti": 5, "da_leggere": 0})
    assert CL.esegui("raccolta_contatti:2511") is True and stato[-1] == "fatto"
    monkeypatch.setattr(C, "inizia", lambda job, tentativi_max=3: None)
    assert CL.esegui("raccolta_contatti:2511") is None


def test_giro_periodico_spento_di_default(monkeypatch):
    monkeypatch.delenv("AUTOMATISMO_RACCOLTA_CONTATTI", raising=False)
    monkeypatch.setattr(automatismi, "valori", lambda: {})
    monkeypatch.setattr(RC, "lavoro", lambda *a, **k: (_ for _ in ()).throw(AssertionError("non deve partire")))
    assert RC.giro_periodico() == {"spento": True}
    assert "raccolta_contatti" in automatismi.NOMI_SPENTI and "raccolta_contatti" not in automatismi.NOMI
    from dealfinder.pipeline import ciclo as C
    fatti = set()
    monkeypatch.setattr(C, "_una_volta", lambda job: not (job in fatti or fatti.add(job)))
    monkeypatch.setattr(automatismi, "valori", lambda: {"raccolta_contatti": True, "raccolta_contatti_siti": 30})
    visti = []
    monkeypatch.setattr(RC, "lavoro", lambda minuti, siti: (visti.append((minuti, siti)), {"siti": 3})[1])
    assert RC.giro_periodico("20261125") == {"siti": 3} and visti == [(15.0, 30)]
    assert RC.giro_periodico("20261125") == {"gia_fatto": True}      # una volta al giorno


def test_sql_vero_semina_prendi_confronti_consegna():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pg_scheda as P
    conn = P.nuovo_db("racc_contatti")
    if conn is None:
        return
    P.migra(conn, "030_commercianti_as24.sql", "032_lead_consegne.sql")
    conn.execute("CREATE TABLE disiscritti (email_hash TEXT PRIMARY KEY, motivo TEXT)")
    conn.execute("CREATE TABLE brevo_inviati (email TEXT PRIMARY KEY)")
    from dealfinder.marketing.invii import impronta
    from dealfinder.pipeline import lead_email as LE, lead_maps as LM, lead_tutti as LT
    for s in (LE.SQL, LM.SQL, LT.SQL_TAB):
        for q in s.split(";"):
            if q.strip():
                conn.execute(q)
    conn.execute("INSERT INTO lead_maps (place_id, nome, comune, prov, tel, sito) VALUES "
                 "('p1','Auto Uno','Monza','MB','+39 347 000 0001','https://WWW.autouno-esempio.it/home?x=1'),"
                 "('p2','Auto Uno bis','Monza','MB',NULL,'http://autouno-esempio.it'),"
                 "('p3','Auto Roma','Roma','RM',NULL,'https://autoroma-esempio.it/'),"
                 "('p4','Senza sito','Lodi','LO',NULL,NULL)")
    conn.execute("INSERT INTO lead_tutti_dealer (chiave, nome, comune, prov, cell) VALUES "
                 "('https://www.autoscout24.it/concessionari/due','Auto Due','Lecco','LC','{3470000002}'),"
                 "('https://www.autoscout24.it/concessionari/tre','Auto Tre','Pavia','PV','{}')")
    conn.execute("INSERT INTO lead_email (chiave, email, sito) VALUES "
                 "('https://www.autoscout24.it/concessionari/due','{}','https://www.autodue-esempio.it/'),"
                 "('https://www.autoscout24.it/concessionari/tre','{vecchia@autotre-esempio.it}',NULL)")
    conn.execute("INSERT INTO lead_consegne (tipo, dati) VALUES ('RNUM', %s), ('R60', %s), ('LEADE', %s)",
                 (json.dumps({"nome": "Auto Quattro", "comune": "Meda", "prov": "MB", "sito": "https://autoquattro-esempio.it",
                              "cellulare": "347 0000 004"}),
                  json.dumps({"nome": "Auto Cinque", "sito": "", "email": "Info@AutoCinque-esempio.it"}),
                  json.dumps({"nome": "Altro", "sito": "https://nonpreso-esempio.it"})))
    conn.execute("INSERT INTO brevo_inviati (email) VALUES ('gia@brevo-esempio.it')")
    conn.execute("INSERT INTO disiscritti (email_hash, motivo) VALUES (%s, 'prova')", (impronta("basta@autouno-esempio.it"),))
    conn.execute("INSERT INTO commercianti_as24 (seller_id, nome, cellulari) VALUES ('s1','X','{+393470000005}')")

    class A(RC.Archivio):
        def _c(self):
            return conn
    a = A()
    a.prepara()
    n = a.semina()
    assert n == {"maps": 1, "commercianti": 1, "elenchi": 1}, n       # un dominio una volta, Roma e LEADE fuori
    assert a.semina() == {"maps": 0, "commercianti": 0, "elenchi": 0}
    assert a.da_leggere() == 3
    presi = a.prendi(2, 7)
    assert [r["dominio"] for r in presi] == ["autodue-esempio.it", "autoquattro-esempio.it"]
    assert presi[0]["rif"].endswith("/due") and presi[0]["origine"] == "autoscout"
    assert [r["dominio"] for r in a.prendi(5, 7)] == ["autouno-esempio.it"] and a.prendi(5, 7) == []
    assert a.non_contattabili(["basta@autouno-esempio.it", "info@autouno-esempio.it"]) == {"basta@autouno-esempio.it"}
    assert a.email_note(["gia@brevo-esempio.it", "vecchia@autotre-esempio.it", "info@autocinque-esempio.it",
                         "nuova@autouno-esempio.it"], "autouno-esempio.it") == {
        "gia@brevo-esempio.it", "vecchia@autotre-esempio.it", "info@autocinque-esempio.it"}
    assert a.cell_noti(["3470000001", "3470000002", "3470000004", "3470000005", "3470000009"], "autouno-esempio.it") == {
        "3470000001", "3470000002", "3470000004", "3470000005"}
    a.salva("autouno-esempio.it", {"email": ["nuova@autouno-esempio.it"], "cell": ["3470000009"],
                                   "email_nuove": ["nuova@autouno-esempio.it"], "cell_nuovi": ["3470000009"],
                                   "esito": "ok", "pagine": 2})
    a.salva("autodue-esempio.it", {"email": ["info@autodue-esempio.it"], "cell": ["3470000009"], "esito": "ok", "pagine": 1})
    assert a.email_note(["nuova@autouno-esempio.it"], "autodue-esempio.it") == {"nuova@autouno-esempio.it"}
    assert a.cell_su_tanti_siti(["3470000009"], 2) == {"3470000009"} and a.cell_su_tanti_siti(["3470000009"], 4) == set()
    d = a.da_consegnare(10)
    assert len(d) == 1 and d[0]["email_nuove"] == ["nuova@autouno-esempio.it"]
    a.consegna(d[0]["dominio"], [(RC.TIPO_EMAIL, {"email": "nuova@autouno-esempio.it"})])
    assert a.da_consegnare(10) == []
    assert conn.execute("SELECT count(*) AS n FROM lead_consegne WHERE tipo = %s", (RC.TIPO_EMAIL,)).fetchone()["n"] == 1
    assert a.in_lead_email("https://www.autoscout24.it/concessionari/due", ["info@autodue-esempio.it"], None) is True
    assert a.in_lead_email("https://www.autoscout24.it/concessionari/tre", ["x@autotre-esempio.it"], None) is False
    # Pagine Gialle per nome: solo chi non ha né sito né cellulare, una volta sola
    s = a.senza_sito(10)
    assert [x["nome"] for x in s] == ["Auto Tre"]
    assert a.aggiungi("pg:abc", {"nome": "Auto Tre", "rif": s[0]["chiave"], "esito": "pg_niente", "tentato": "now"})
    assert a.senza_sito(10) == [] and a.da_leggere() == 0


def test_rete_vera_su_server_locale_taglio_e_rinvio(monkeypatch):
    """La classe Rete (httpx) contro un server locale: user agent dichiarato, pagina tagliata, rinvio non seguito."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import httpx
    if not hasattr(httpx, "Timeout"):          # httpx finto di un altro test: qui serve quello vero
        return
    visti = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            visti.append(self.headers.get("User-Agent"))
            if self.path == "/vai":
                self.send_response(301)
                self.send_header("Location", "/grande")
                self.end_headers()
                return
            corpo = b"x" * 200_000 if self.path == "/grande" else b"%PDF"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if self.path == "/grande" else "application/pdf")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("RACC_BYTE", "50000")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    r = RC.Rete()
    try:
        code, testo, rinvio = r.get(base + "/grande")
        assert code == 200 and len(testo) == 50000 and rinvio is None        # tagliata: la memoria non cresce
        assert r.get(base + "/vai") == (301, "", "/grande")                  # il rinvio lo decide chi chiama (robots)
        assert r.get(base + "/doc.pdf") == (200, "", None)                   # non è una pagina: niente testo
        assert visti and all(v == RC.UA and "ScovoContatti" in v for v in visti)
    finally:
        r.chiudi()
        srv.shutdown()
