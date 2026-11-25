"""25/11: lettura completa della zona (pipeline/lettura_completa.py) e riga COPERTURA_LETTURA
(pipeline/copertura_lettura.py). Portali finti: paginazione fino in fondo, divisione delle fasce, arresto al blocco."""
import json
import logging
import re

from dealfinder.pipeline import copertura_lettura as CL
from dealfinder.pipeline import lettura_completa as LC


class _Resp:
    def __init__(self, dati=None, code=200, text=None):
        self._dati, self.status_code, self.text = dati, code, text if text is not None else ""

    def json(self):
        if self._dati is None:
            raise ValueError("non è JSON")
        return self._dati


def _annuncio_subito(i: int, prezzo: int, prov: str = "MI", privato: bool = True) -> dict:
    return {"urn": f"id:ad:{i}", "subject": "Fiat Panda", "urls": {"default": f"https://www.subito.it/auto/x-{i}.htm"},
            "features": [{"label": "Prezzo", "values": [{"value": str(prezzo)}]}],
            "advertiser": {"type": 0 if privato else 1},
            "geo": {"city": {"short_name": prov}, "town": {"value": "Milano"}}}


class _Subito:
    """Finta interfaccia di ricerca: annunci con un prezzo, filtro ps/pe (estremi compresi), 100 a pagina."""

    def __init__(self, prezzi, con_totale=True, blocco_alla=None, codice=429):
        self.ads = [_annuncio_subito(i, p) for i, p in enumerate(prezzi)]
        self.con_totale, self.blocco_alla, self.codice = con_totale, blocco_alla, codice
        self.chiamate, self.start_max = 0, 0

    def get(self, url, headers=None, params=None):
        self.chiamate += 1
        if self.blocco_alla is not None and self.chiamate >= self.blocco_alla:
            return _Resp(None, self.codice)
        sel = [a for a in self.ads if params["ps"] <= int(a["features"][0]["values"][0]["value"]) <= params["pe"]]
        self.start_max = max(self.start_max, params["start"])
        out = {"ads": sel[params["start"]: params["start"] + params["lim"]]}
        if self.con_totale:
            out["count_all"] = len(sel)
        return _Resp(out)


class _Col:
    def __init__(self, client):
        self.client = client


def _lettore(salvati: list, **kw) -> LC.Lettore:
    fatti: set = set()

    def salva(listings):
        salvati.extend(listings)
        return len(listings)
    L = LC.Lettore(salva, dormi=lambda s: None, fatto=fatti.__contains__, segna=fatti.add, **kw)
    L.fatti = fatti
    return L


def _piccoli_tetti(monkeypatch, pagine=3):
    monkeypatch.setattr(LC, "SUB_PAGINE_MAX", pagine)


def test_subito_fino_in_fondo_senza_tetto_di_pagine(monkeypatch):
    _piccoli_tetti(monkeypatch, 30)
    cl = _Subito([1000 + (i % 400) for i in range(1234)])
    salvati: list = []
    m = CL.apri("prova")
    LC.subito_fetta(_lettore(salvati), _Col(cl), 4, "8", "MI", 0, 1500, set())
    r = CL.chiudi(m)["fonti"]["subito"]
    assert len({l.source_id for l in salvati}) == 1234
    assert cl.chiamate == 13 and r["pagine"] == 13 and r["letti"] == 1234        # 12 pagine piene + l'ultima
    assert r["totale_dichiarato"] == 1234 and r["nuovi"] == 1234 and r["motivi"] == {"fine_risultati": 1}


def test_subito_divide_la_fascia_troppo_piena_con_il_totale(monkeypatch):
    _piccoli_tetti(monkeypatch, 3)                    # tetto: 300 annunci per ricerca
    cl = _Subito([i for i in range(0, 1500)])         # 1.500 annunci, un prezzo diverso ciascuno
    salvati: list = []
    m = CL.apri("prova")
    L = _lettore(salvati)
    LC.subito_fetta(L, _Col(cl), 4, "8", "MI", 0, 1500, set())
    r = CL.chiudi(m)["fonti"]["subito"]
    assert len({l.source_id for l in salvati}) == 1500            # tutti, nessuno perso al tetto
    assert cl.start_max <= 200                                    # mai oltre la 3ª pagina di una ricerca
    assert r["motivi"].get("divisa", 0) >= 3 and "tetto_pagine" not in r["motivi"]
    assert "sub:4:MI:0-1500:div" in L.fatti and "sub:4:MI:0-1500" not in L.fatti


def test_subito_senza_totale_divide_dopo_le_pagine_piene(monkeypatch):
    _piccoli_tetti(monkeypatch, 3)
    cl = _Subito([i for i in range(0, 1000)], con_totale=False)
    salvati: list = []
    m = CL.apri("prova")
    LC.subito_fetta(_lettore(salvati), _Col(cl), 4, "8", "MI", 0, 1500, set())
    r = CL.chiudi(m)["fonti"]["subito"]
    assert len({l.source_id for l in salvati}) == 1000 and len(salvati) == 1000      # i doppioni si saltano
    assert r["totale_dichiarato"] is None and r["motivi"].get("divisa", 0) >= 1


def test_subito_fascia_stretta_piena_si_dice(monkeypatch):
    _piccoli_tetti(monkeypatch, 2)
    cl = _Subito([5000] * 500)                         # 500 annunci allo stesso prezzo: non si può dividere
    monkeypatch.setattr(LC, "SUB_FASCIA_MIN", 100)
    salvati: list = []
    m = CL.apri("prova")
    LC.subito_fetta(_lettore(salvati), _Col(cl), 4, "8", "MI", 4950, 5100, set())
    r = CL.chiudi(m)["fonti"]["subito"]
    assert r["motivi"] == {"tetto_pagine": 1} and r["totale_dichiarato"] == 500 and r["letti"] == 200


def test_subito_blocco_ferma_la_fonte_senza_riprove():
    for codice in (429, 403):
        cl = _Subito([1000] * 900, blocco_alla=3, codice=codice)
        salvati: list = []
        m = CL.apri("prova")
        L = _lettore(salvati)
        try:
            LC.leggi_subito(L, _Col(cl), {"MI", "MB"})
            assert False, "doveva fermarsi"
        except LC.Blocco as b:
            assert str(codice) in str(b)
        r = CL.chiudi(m)["fonti"]["subito"]
        assert cl.chiamate == 3                        # nessuna richiesta dopo il blocco, nessuna riprova
        assert r["motivi"] == {"blocco": 1} and len(salvati) == 200
        assert not any(k.startswith("sub:") and not k.endswith(":div") for k in L.fatti)   # la fascia resta da leggere


def test_subito_pagina_di_blocco_e_errori_di_fila():
    class _Html:
        chiamate = 0

        def get(self, url, headers=None, params=None):
            self.chiamate += 1
            return _Resp(None, 200, "<html>captcha</html>")
    cl = _Html()
    try:
        LC.subito_fetta(_lettore([]), _Col(cl), 4, "8", "MI", 0, 1500, set())
        assert False
    except LC.Blocco:
        assert cl.chiamate == 1

    class _Giu:
        chiamate = 0

        def get(self, url, headers=None, params=None):
            self.chiamate += 1
            return _Resp(None, 503)
    cl = _Giu()
    L = _lettore([])
    try:
        LC.leggi_subito(L, _Col(cl), {"MI"})
        assert False
    except LC.Blocco:
        assert cl.chiamate == LC.ERRORI_MAX and L.incompleta          # tre errori di fila: la fonte si ferma


def test_subito_scarta_altre_province_e_tempo():
    cl = _Subito([1000] * 150)
    cl.ads[0]["geo"]["city"]["short_name"] = "RM"
    salvati: list = []
    LC.subito_fetta(_lettore(salvati), _Col(cl), 4, "8", "MI", 0, 1500, set())
    assert len(salvati) == 149
    ora = [0.0]
    L = _lettore([], scadenza=10.0, adesso=lambda: ora[0])
    ora[0] = 11.0
    try:
        LC.subito_fetta(L, _Col(_Subito([1000] * 150)), 4, "8", "MI", 0, 1500, set())
        assert False
    except LC.Tempo:
        pass


def test_zona_e_fasce_subito():
    pv = LC.province_subito({"MI", "TO", "PC", "GE", "RM"})
    assert {(r, s) for r, s, _ in pv} == {(4, "MI"), (2, "TO"), (8, "PC"), (3, "GE")}
    f = LC.fasce_subito()
    assert f[0][0] == 0 and f[-1][1] == LC.PREZZO_MAX and all(a < b for a, b in f)
    assert all(f[i][1] == f[i + 1][0] for i in range(len(f) - 1))


# ------------------------------------------------------------------ AutoScout24

class _AS:
    """Finto AutoScout24: al massimo 20 pagine da 20 per ricerca, totale dichiarato."""

    def __init__(self, n=1000, passo=10, anni=None, blocco_alla=None, codice=429, vuota=False):
        self.cars = [{"id": f"a{i}", "url": f"/annunci/a{i}", "tracking": {
            "price": str(500 + i * passo), "mileage": "100000",
            "firstRegistration": f"05-{(anni or [2012])[i % len(anni or [2012])]}"},
            "vehicle": {"make": "Fiat", "model": "Panda"}, "location": {"zip": "20100", "city": "Milano"},
            "seller": {"type": "Private", "phones": ["x"]}} for i in range(n)]
        self.chiamate, self.pagina_max, self.blocco_alla, self.codice, self.vuota = 0, 0, blocco_alla, codice, vuota
        self.url: list = []

    def get(self, url):
        self.chiamate += 1
        self.url.append(url)
        if self.blocco_alla is not None and self.chiamate >= self.blocco_alla:
            return _Resp(None, self.codice) if not self.vuota else _Resp(None, 200, "<html>verifica di essere umano</html>")
        q = dict(re.findall(r"[?&](\w+)=([^&]*)", url))
        lo, hi, page = int(q["pricefrom"]), int(q["priceto"]), int(q["page"])
        y0, y1 = int(q["fregfrom"]), int(q.get("fregto", 9999))
        sel = [c for c in self.cars if lo <= int(c["tracking"]["price"]) <= hi
               and y0 <= int(c["tracking"]["firstRegistration"][-4:]) <= y1]
        self.pagina_max = max(self.pagina_max, page)
        items = sel[(page - 1) * 20: page * 20] if page <= 20 else []
        data = {"props": {"pageProps": {"listings": items, "numberOfResults": len(sel)}}}
        return _Resp(None, 200, f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>')


def test_autoscout_divide_per_prezzo_e_legge_tutto():
    cl = _AS(1000)
    salvati: list = []
    m = CL.apri("prova")
    LC.leggi_autoscout(_lettore(salvati), cl, fasce=[(500, 12000)], centri=[("20121", "150")], anno_min=2007)
    r = CL.chiudi(m)["fonti"]["autoscout24"]
    assert len({l.source_id for l in salvati}) == len(salvati) == 1000      # oltre il tetto dei 400, senza doppioni
    assert cl.pagina_max <= 20 and all(l.seller_type == "privato" for l in salvati)
    assert all("custtype=P" in u and "zip=20121&zipr=150" in u and "adage" not in u for u in cl.url)
    assert r["motivi"].get("divisa", 0) >= 2 and "tetto_portale" not in r["motivi"]
    assert r["totale_dichiarato"] == 1000                                  # i pezzi, non la ricerca divisa
    assert all("phones" not in json.dumps(l.raw) for l in salvati)          # mai numeri di telefono in memoria


def test_autoscout_stesso_prezzo_divide_per_anni():
    cl = _AS(900, passo=0, anni=list(range(2007, 2025)))                    # 900 annunci tutti a 500 €
    salvati: list = []
    m = CL.apri("prova")
    LC.leggi_autoscout(_lettore(salvati), cl, fasce=[(500, 560)], centri=[("20121", "150")], anno_min=2007)
    r = CL.chiudi(m)["fonti"]["autoscout24"]
    assert len(salvati) == 900 and any("fregto=" in u for u in cl.url)
    assert "tetto_portale" not in r["motivi"]


def test_autoscout_blocco_403_429_e_pagina_di_blocco():
    for kw in ({"codice": 429}, {"codice": 403}, {"vuota": True}):
        cl = _AS(300, blocco_alla=4, **kw)
        salvati: list = []
        m = CL.apri("prova")
        try:
            LC.leggi_autoscout(_lettore(salvati), cl, fasce=[(500, 12000), (12001, 20000)],
                               centri=[("20121", "150"), ("25121", "90")], anno_min=2007)
            assert False, "doveva fermarsi"
        except LC.Blocco:
            pass
        r = CL.chiudi(m)["fonti"]["autoscout24"]
        assert cl.chiamate == 4 and len(salvati) == 60                      # fermo subito: niente riprove, niente altre fasce
        assert r["motivi"] == {"blocco": 1}


def test_autoscout_in_pausa_dal_tempo_reale_non_chiede_nulla(monkeypatch):
    from dealfinder.pipeline import as_riprova
    monkeypatch.setitem(as_riprova._stato, "fermo_fino", __import__("time").time() + 600)
    cl = _AS(100)
    try:
        LC.leggi_autoscout(_lettore([]), cl, fasce=[(500, 12000)], centri=[("20121", "150")], anno_min=2007)
        assert False
    except LC.Blocco:
        assert cl.chiamate == 0


def test_ripresa_dai_passi_fatti():
    cl = _AS(1000)
    salvati: list = []
    L = _lettore(salvati)
    LC.leggi_autoscout(L, cl, fasce=[(500, 12000)], centri=[("20121", "150")], anno_min=2007)
    prima = cl.chiamate
    L2 = _lettore([])
    L2.fatto, L2.segna = L.fatti.__contains__, L.fatti.add
    LC.leggi_autoscout(L2, cl, fasce=[(500, 12000)], centri=[("20121", "150")], anno_min=2007)
    assert cl.chiamate == prima                                             # tutto già fatto: nessuna richiesta


# ------------------------------------------------------------------ giro e ciclo

class _Conn:
    def __init__(self):
        self.stato = None

    def execute(self, sql, par=()):
        if sql.startswith("INSERT INTO tempo_reale"):
            self.stato = par[1]
        return self

    def fetchone(self):
        return {"stats": self.stato} if self.stato else None

    def commit(self):
        pass

    def rollback(self):
        pass


class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.righe: list = []

    def emit(self, record):
        self.righe.append(record.getMessage())


def _con_log(nome):
    h = _Log()
    lg = logging.getLogger(nome)
    lg.addHandler(h)
    return h, lambda: lg.removeHandler(h)


def test_giro_blocco_di_una_fonte_e_ciclo(monkeypatch):
    monkeypatch.setenv("AUTOMATISMO_LETTURA_COMPLETA", "1")
    conn = _Conn()
    ora = [1000.0]
    chiamate = []

    def sub_bloccato(L):
        chiamate.append("subito")
        CL.nota("subito", "MI:0-1500", gruppo="MI", motivo="blocco")
        raise LC.Blocco("HTTP 429")

    def as_ok(L):
        chiamate.append("autoscout24")
        CL.nota("autoscout24", "20121:150:300-2000", gruppo="20121:150", totale=40, pagine=2, letti=40, nuovi=4,
                motivo="fine_risultati")

    h, togli = _con_log("lettura_completa")
    hc, togli_c = _con_log("copertura_lettura")
    try:
        st = LC.giro(conn, lettori={"subito": sub_bloccato, "autoscout24": as_ok}, adesso=lambda: ora[0])
        assert st["bloccate"] == ["subito"] and st["finito"] is False and chiamate == ["subito", "autoscout24"]
        blocchi = [r for r in h.righe if r.startswith("LETTURA_COMPLETA_BLOCCO")]
        assert len(blocchi) == 1 and json.loads(blocchi[0].split(" ", 1)[1])["fonte"] == "subito"
        riga = json.loads([r for r in hc.righe if r.startswith("COPERTURA_LETTURA")][-1].split(" ", 1)[1])
        assert riga["giro"] == "lettura_completa" and riga["bloccate"] == ["subito"]
        assert riga["fonti"]["subito"]["motivi"] == {"blocco": 1}
        assert riga["fonti"]["autoscout24"]["totale_dichiarato"] == 40
        # giro dopo, mezz'ora più tardi: Subito riposa (3 ore), AutoScout24 è già finito: nessuna richiesta
        ora[0] += 1800
        chiamate.clear()
        st = LC.giro(conn, lettori={"subito": sub_bloccato, "autoscout24": as_ok}, adesso=lambda: ora[0])
        assert chiamate == [] and st["finito"] is False
        # passate le ore di riposo Subito riparte e finisce: ciclo chiuso, riga del ciclo con la copertura
        ora[0] += LC.FERMO_BLOCCO_ORE * 3600

        def sub_ok(L):
            CL.nota("subito", "MI:0-1500", gruppo="MI", totale=250, pagine=3, letti=250, nuovi=50, motivo="fine_risultati")
            L.salva([])
        st = LC.giro(conn, lettori={"subito": sub_ok, "autoscout24": as_ok}, adesso=lambda: ora[0])
        assert st["finito"] is True
        ciclo = json.loads([r for r in h.righe if '"lettura_completa:ciclo"' in r][-1].split(" ", 1)[1])
        assert ciclo["fonti"]["subito"]["totale_dichiarato"] == 250 and ciclo["fonti"]["subito"]["copertura_pct"] == 100.0
        assert ciclo["blocchi"] == {"subito": 1}
        # ciclo finito da poco: niente da fare fino a CICLO_ORE; con forza (avvio a mano) parte il ciclo 2
        assert LC.giro(conn, lettori={"subito": sub_ok, "autoscout24": as_ok}, adesso=lambda: ora[0] + 3600) is None
        st = LC.giro(conn, forza=True, lettori={"subito": sub_ok, "autoscout24": as_ok}, adesso=lambda: ora[0] + 3600)
        assert st["ciclo"] == 2
    finally:
        togli()
        togli_c()


def test_giro_tempo_e_interruttore(monkeypatch):
    conn = _Conn()

    def lento(L):
        raise LC.Tempo()
    monkeypatch.setenv("AUTOMATISMO_LETTURA_COMPLETA", "1")
    st = LC.giro(conn, lettori={"subito": lento, "autoscout24": lento})
    assert st["tempo"] is True and st["finito"] is False and json.loads(conn.stato)["finite"] == {}
    monkeypatch.setenv("AUTOMATISMO_LETTURA_COMPLETA", "0")
    assert LC.giro(conn, lettori={"subito": lento, "autoscout24": lento}) is None


def test_ciclo_da_fare():
    assert LC.ciclo_da_fare({}, 100.0)["ciclo"] == 1
    in_corso = {"ciclo": 3, "iniziato": 100.0, "finito": None, "finite": {"subito": True}}
    assert LC.ciclo_da_fare(in_corso, 200.0) is in_corso
    assert LC.ciclo_da_fare(in_corso, 100.0 + (LC.CICLO_MAX_ORE + 1) * 3600)["ciclo"] == 4       # non finisce: si riparte
    finito = {"ciclo": 3, "iniziato": 100.0, "finito": 1000.0}
    assert LC.ciclo_da_fare(finito, 1000.0 + 3600) is None
    assert LC.ciclo_da_fare(finito, 1000.0 + LC.CICLO_ORE * 3600 + 1)["ciclo"] == 4


# ------------------------------------------------------------------ misura e avvio a mano

def test_copertura_riepilogo_gruppi_e_tagliate():
    m = CL.Misura("prova")
    for i in range(40):
        m.nota("subito", f"MI:{i}", gruppo="MI", totale=100, pagine=1, letti=100, nuovi=1, motivo="fine_risultati")
    m.nota("subito", "MI:x", gruppo="MI", totale=9000, pagine=30, letti=3000, motivo="tetto_pagine")
    m.nota("subito", "MI:y", gruppo="MI", totale=50000, pagine=1, motivo="divisa")
    r = m.riepilogo(nuovi={"subito": 7})["fonti"]["subito"]
    assert r["ricerche"] == 42 and r["totale_dichiarato"] == 4000 + 9000 and r["letti"] == 7000 and r["nuovi"] == 7
    assert r["motivi"] == {"fine_risultati": 40, "tetto_pagine": 1, "divisa": 1}
    assert list(r["tagliate"]) == ["MI:x"] and r["gruppi"]["MI"]["ricerche"] == 42
    assert CL.corrente() is None or CL.corrente() is not m
    CL.nota("subito", "senza giro aperto", pagine=1)              # nessun giro aperto: non fa nulla e non solleva


def test_collettori_esistenti_scrivono_la_misura():
    from dealfinder.collectors.autoscout import AutoScoutCollector
    from tests.test_autoscout_massiva import _FakeClient
    c = AutoScoutCollector(client=_FakeClient())
    c.pause = lambda: None
    m = CL.apri("prova")
    n = len(list(c.search({"massiva": True, "min_price": 500, "max_price": 12000, "step": 12000,
                           "venditori": [("P", "privato")]})))
    r = CL.chiudi(m, nuovi={"autoscout24": 5})["fonti"]["autoscout24"]
    assert n == 1000 and r["totale_dichiarato"] == 1000 and r["letti"] >= 1000 and r["nuovi"] == 5
    assert r["motivi"].get("divisa", 0) >= 1 and "tetto_portale" not in r["motivi"]


def test_avvio_a_mano_solo_nel_sito_e_interruttore():
    from dealfinder import automatismi
    from dealfinder.pipeline import coda_lavori as CODA
    from dealfinder.pipeline import tempo_reale as tr
    assert "lettura_completa:ora".startswith(CODA.SOLO_SITO) and not CODA.spostabile("lettura_completa:ora")
    assert "lettura_completa" in automatismi.NOMI and "lettura" in tr.LOCK
    assert len(set(tr.LOCK.values())) == len(tr.LOCK)


def test_nessuna_variabile_nuova():
    import inspect
    for mod in (LC, CL):
        src = inspect.getsource(mod)
        assert set(re.findall(r'environ\.get\("([A-Z_0-9]+)"', src)) <= {"ANNO_MINIMO"}
