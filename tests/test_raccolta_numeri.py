"""raccolta_numeri (dealfinder/pipeline/raccolta_numeri.py): parser delle vetrine Subito, dei risultati di ricerca
Subito e di Pagine Gialle con pagine finte salvate in tests/dati/raccolta_numeri; filtri (solo chi vende auto, esclusi
sempre), unione, voce della coda, ripresa dopo il tetto di tempo, Pagine Gialle bloccata, niente numeri nei log."""
import json
import logging
import os
import time

from dealfinder.pipeline import coda_lavori as CL
from dealfinder.pipeline import raccolta_numeri as RN

DATI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dati", "raccolta_numeri")
ZONA = {"monza": ("Monza", "MB", 15.0), "brugherio": ("Brugherio", "MB", 12.7), "seregno": ("Seregno", "MB", 20.0),
        "cesano-maderno": ("Cesano Maderno", "MB", 19.0), "meda": ("Meda", "MB", 22.0),
        "gallarate": ("Gallarate", "VA", 38.0), "lodi": ("Lodi", "LO", 31.0)}


def _leggi(nome):
    with open(os.path.join(DATI, nome), encoding="utf-8") as f:
        return f.read()


def test_vetrina_con_dati_strutturati():
    d = RN.dati_vetrina(_leggi("vetrina_subito.html"))
    assert d["cell"] == ["3471234567"] and d["fissi"] == ["0392345678"]
    assert d["comune"] == "Monza" and d["prov"] == "MB" and d["indirizzo"] == "Via Lissone 12"
    assert d["sito"].startswith("https://www.brianzamotors-esempio.it")
    assert "Brianza Motors" in d["nome"]
    # la partita IVA (11 cifre, comincia con 0) non diventa un fisso
    assert "01234567890" not in d["fissi"]


def test_vetrina_solo_testo():
    d = RN.dati_vetrina(_leggi("vetrina_solo_testo.html"))
    assert d["cell"] == ["3337654321"] and d["fissi"] == ["0371556677"]
    assert d["nome"] == "Garage Lodigiano" and d["sito"] == "http://www.garagelodigiano-esempio.com"
    assert "3999999999" not in d["cell"]                     # numeri negli script non contano
    assert "09876543210" not in d["fissi"]


def test_negozi_da_ricerca_subito_mai_privati():
    ads = json.loads(_leggi("ricerca_subito.json"))["ads"]
    n = RN.negozi_da_annunci(ads, ZONA, "MB")
    sid = [x["sid"] for x in n]
    assert sid.count("98765") == 1                          # un negozio una volta
    assert "11111" in sid and n[sid.index("11111")]["km"] == 12.7
    assert all(x["sid"] not in ("privato1", "u3") for x in n)   # privato e attività senza vetrina: fuori
    # fuori dai 60 km (provincia non MI/MB e comune non nell'elenco): fuori
    assert "55555" not in [x["sid"] for x in RN.negozi_da_annunci(ads, ZONA, "CO")]


def test_pagine_gialle_blocchi_e_filtri():
    voci = RN.voci_paginegialle(_leggi("paginegialle.html"))
    per_nome = {v["nome"]: v for v in voci}
    assert len(voci) == 5
    r = per_nome["Rossi Automobili Srl"]
    assert r["comune"] == "Cesano Maderno" and r["prov"] == "MB" and r["cell"] == ["3381112233"]
    assert r["fissi"] == ["0362123456"] and r["sito"] == "https://www.rossiautomobili-esempio.it/"
    v = per_nome["Verdi Car Center"]
    assert v["cell"] == ["3489876543"] and v["fissi"] == ["0362445566"] and v["comune"] == "Meda"
    tenuti = sorted(x["nome"] for x in voci if RN.vende_auto(x["nome"], x["categorie"]))
    assert tenuti == ["Rossi Automobili Srl", "Verdi Car Center"]       # fuori officina, Loconte, noleggio


def test_pagine_gialle_schema_org_e_blocco():
    voci = RN.voci_paginegialle(_leggi("paginegialle_ld.html"))
    assert [v["nome"] for v in voci] == ["Gallarate Auto Usate", "Camper Point Varese"]
    assert voci[0]["fissi"] == ["0331789012"] and voci[0]["comune"] == "Gallarate" and voci[0]["prov"] == "VA"
    assert not RN.vende_auto(voci[1]["nome"])
    html = _leggi("pg_captcha.html")
    assert RN.voci_paginegialle(html) == [] and RN.pg_bloccata(200, html) and RN.pg_bloccata(403, "")
    assert not RN.pg_bloccata(200, _leggi("paginegialle.html"))


def test_solo_chi_vende_auto_ed_esclusi_sempre():
    for n in ("Loconte Auto", "Officine Mulino srl", "Cosvalauto", "Cosval Auto", "Auto e Auto Varese",
              "Auto & Auto Varese", "SR Auto", "S.R. Auto", "GT Automobili", "Privacar", "Vroom Italia",
              "Carrozzeria Rossi", "Autofficina Bianchi", "Noleggio Auto Milano", "Ricambi Auto Brianza",
              "Moto Shop Monza", "Camper Center", "Autodemolizioni Verdi", "Autoscuola Centrale", "Elettrauto Neri"):
        assert not RN.vende_auto(n), n
    for n in ("Brianza Motors", "Autosalone Rossi", "Verdi Car Center", "Automobili Gialli srl"):
        assert RN.vende_auto(n), n
    assert not RN.vende_auto("Centro Rossi", "Autofficine; Carrozzerie")
    assert RN.vende_auto("Centro Rossi", "Autoveicoli usati; Autofficine")


def test_unione_e_nuovi():
    grezze = [
        {"fonte": "subito", "nome": "Brianza Motors", "comune": "Monza", "prov": "MB", "km": 15.0,
         "cell": ["3471234567"], "fissi": [], "sito": None, "indirizzo": "Via Lissone 12", "link": "https://x/1"},
        {"fonte": "paginegialle", "nome": "Autosalone Brianza Motors srl", "comune": "Monza", "prov": "MB", "km": 15.0,
         "cell": ["3471234567"], "fissi": ["0392345678"], "sito": "https://b.it", "indirizzo": None, "link": None},
        {"fonte": "paginegialle", "nome": "Vecchio Salone", "comune": "Meda", "prov": "MB", "km": 22.0,
         "cell": [], "fissi": ["0362445566"], "sito": None, "indirizzo": None, "link": None},
        {"fonte": "paginegialle", "nome": "Loconte", "comune": "Meda", "prov": "MB", "km": 22.0,
         "cell": ["3331112222"], "fissi": [], "sito": None, "indirizzo": None, "link": None},
    ]
    righe = RN.unisci_righe(grezze, noti={"362445566"})
    assert len(righe) == 2
    a, b = righe
    assert a["nome"] == "Brianza Motors" and a["cellulare"] == "3471234567" and a["fisso"] == "0392345678"
    assert a["fonte"] == "paginegialle+subito" and a["nuovo"] is True and a["sito"] == "https://b.it"
    assert b["nome"] == "Vecchio Salone" and b["nuovo"] is False          # numero già raccolto
    assert set(a) >= {"nome", "comune", "km", "cellulare", "fisso", "fonte"}


def test_voce_nella_coda_lunga():
    v = "raccolta_numeri:1811"
    assert CL._prefisso(v) == "raccolta_numeri:" and CL.lungo(v) and CL.mancano(v, {}) == []
    assert CL.durata_prevista(v, {}) == 3 * 60 + 15 and CL.durata_prevista(v, {"RNUM_ORE": "2"}) == 135


def test_spostabile_al_cron(monkeypatch):
    monkeypatch.setenv("LAVORI_NEL_CRON", "lunghi")
    assert CL.spostabile("raccolta_numeri:1811")


class _Archivio:
    def __init__(self):
        self.p, self.righe, self.fin = {}, [], []

    def passi(self):
        return dict(self.p)

    def segna(self, passo, dati=None):
        self.p[passo] = dati

    def salva(self, r):
        self.righe.append(r)

    def grezze(self):
        return list(self.righe)

    def finale(self, r):
        self.fin = r

    def noti(self):
        return set()


class _Rete:
    def __init__(self, pg="ok", lento=0.0):
        self.pg, self.lento, self.chiamate = pg, lento, []

    def sblocco(self):
        return True

    def api(self, params):
        time.sleep(self.lento)
        if params["ci"] == "9" and params["ps"] == 0 and params["start"] == 0:
            return json.loads(_leggi("ricerca_subito.json"))["ads"]
        return []

    def pagina(self, url, sblocco):
        self.chiamate.append(url)
        time.sleep(self.lento)
        if "impresapiu" in url:
            if url.endswith("98765"):
                return 200, _leggi("vetrina_subito.html")
            return 200, _leggi("vetrina_solo_testo.html") if url.endswith("11111") else "<html>senza numeri</html>"
        if self.pg == "bloccata":
            return 200, _leggi("pg_captcha.html")
        return 200, _leggi("paginegialle.html") if "Cesano" in url else "<html></html>"


def test_giro_completo_pg_bloccata_e_niente_numeri_nei_log(monkeypatch):
    monkeypatch.setattr(RN, "PROVINCE_SUBITO", (("MB", 4),))
    monkeypatch.setenv("RNUM_PAUSA_BLOCCO", "0")
    monkeypatch.setenv("RNUM_PG_BLOCCHI_MAX", "2")
    righe_log = []

    class H(logging.Handler):
        def emit(self, rec):
            righe_log.append(rec.getMessage())
    h = H()
    RN.log.addHandler(h)
    try:
        a = _Archivio()
        esito = RN.lavoro("t1", archivio=a, rete=_Rete(pg="bloccata"), zona=ZONA, ore=0.05)
    finally:
        RN.log.removeHandler(h)
    assert esito["pagine_gialle_bloccata"] is True and not esito.get("da_riprendere")
    assert esito["subito_salvati"] == 2 and esito["saloni"] == 2                  # fonte 1 continua
    assert {r["nome"] for r in a.fin} == {"Autosalone Brianza Motors", "Brugherio Car"}
    assert all("passo" not in r for r in a.righe) and all(r["etichetta"] == "t1" for r in a.righe)
    testo = "\n".join(righe_log)
    assert "BLOCCATA" in testo and "RNUM_FINE" in testo
    for numero in ("3471234567", "347 123", "0392345678", "3337654321", "Brianza", "Brugherio Car"):
        assert numero not in testo                                              # solo conteggi nei log


def test_tetto_di_tempo_e_ripresa(monkeypatch):
    monkeypatch.setattr(RN, "PROVINCE_SUBITO", (("MB", 4),))
    a = _Archivio()
    rete = _Rete(lento=0.02)
    e1 = RN.lavoro("t2", archivio=a, rete=rete, zona=ZONA, ore=0.3 / 3600)      # 0,3 secondi: non finisce
    assert e1["da_riprendere"] is True and a.fin == []
    fatti = set(a.p)
    e2 = RN.lavoro("t2", archivio=a, rete=rete, zona=ZONA, ore=0.05)            # il giro dopo riprende e finisce
    assert not e2.get("da_riprendere") and e2["giro"] == 2
    gia = [u for u in rete.chiamate if u.endswith("/shops/98765")]
    assert ("vetrina:98765" in fatti) == (len(gia) == 1)                       # vetrina letta una volta sola
    nomi = {r["nome"] for r in a.fin}
    assert {"Autosalone Brianza Motors", "Brugherio Car", "Rossi Automobili Srl", "Verdi Car Center"} <= nomi
    assert not nomi & {"Autofficina Bianchi", "Autosalone Loconte", "Noleggio Furgoni Brianza"}
    v = next(r for r in a.fin if r["nome"] == "Verdi Car Center")
    assert v["km"] == 22.0 and v["fonte"] == "paginegialle" and v["cellulare"] == "3489876543"


def test_voce_rimandata_a_tempo_finito(monkeypatch):
    """A tempo finito il lavoro si rimanda (non è fatto, non conta come tentativo) e la voce esce con 1 (si riprende)."""
    stati = []

    class Lav:
        def fatto(self, st=None):
            stati.append("fatto")

        def rimanda(self, st=None):
            stati.append("rimanda")

        def fallito(self, e=None):
            stati.append("fallito")
    import dealfinder.pipeline.ciclo as C
    monkeypatch.setattr(C, "inizia", lambda job, tentativi_max=3: Lav())
    monkeypatch.setattr(RN, "lavoro", lambda et: {"da_riprendere": True, "giro": 1})
    assert CL.esegui("raccolta_numeri:1811") is False and stati == ["rimanda"]
    monkeypatch.setattr(RN, "lavoro", lambda et: {"saloni": 3})
    assert CL.esegui("raccolta_numeri:1811") is True and stati[-1] == "fatto"
