"""12/11 sera (ramo margini-fase2): calibrazione del combinato, fattore oltre 1 (spento), pooling dei tassi anno/km,
trazione a livelli e optional, forbice della rivendita, guardia delle 36 ore e giudice, statistiche della scheda,
prova alla cieca per fascia e prima/dopo."""
import math
import os

from dealfinder import regole as RG
from dealfinder.ai import prova_cieca as PC
from dealfinder.pricing import calibra as CB
from dealfinder.pricing import dotazioni as DT
from dealfinder.pricing import forbice as FB
from dealfinder.pricing import policy_v2 as P
from dealfinder.pricing import valutazione as VZ
from dealfinder.web import sito_regole as SR
from dealfinder.web import trattativa as TR
from tests.test_policy import CFG, NOW, car

QUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg(**campi):
    return P.PolicyConfig(RG.con_campi(RG.regole(), campi))


# ------------------------------------------------------------------ interruttori

def test_interruttori_e_default():
    r = RG.regole()
    assert r.correzione.pooling and r.calibrazione.vr_nel_combinato and r.trazione.attiva and r.forbice.attiva
    assert r.scrittura.giudice_vince
    # le novità che possono ALZARE un prezzo restano spente
    assert not r.correzione.due_sensi_alto and not r.calibrazione.alza and not r.dotazioni.aggiusta
    for nome in ("TASSI_POOLING", "CORREZIONE_DUE_SENSI_ALTO", "CALIBRA_VR_COMBINATO", "CALIBRA_ALZA",
                 "TRAZIONE_LIVELLI", "DOTAZIONI_PREZZO", "RIVENDITA_FORBICE", "GIUDICE_VINCE"):
        assert nome in RG.INTERRUTTORI
    r0 = RG.regole({"TASSI_POOLING": "0", "TRAZIONE_LIVELLI": "0"})
    assert not r0.correzione.pooling and not r0.trazione.attiva
    prima = RG.con_campi(r, RG.NOVITA_FASE2)
    assert not prima.correzione.pooling and not prima.calibrazione.vr_nel_combinato and not prima.trazione.attiva
    assert RG.impronta(prima) != RG.impronta(r)


# ------------------------------------------------------------------ calibrazione

def _fasce(x, n=100, q=None):
    return {k: {"n": n, "prezzo_su_M": x, **({"q": q} if q else {})}
            for k in ("<5k", "5-10k", "10-20k", "20-35k", "35-60k", ">60k")}


def test_fattore_oltre_uno_solo_se_acceso_e_sopra_20000():
    mis = _fasce(1.10)
    f = RG.fattori_calibrazione(mis, per_M=True)
    assert all(x <= 1.0 for _, n, x in f if not str(n).startswith("_"))           # spento: mai sopra 1
    r = RG.con_campi(RG.regole(), {"calibrazione.alza": True})
    f = dict((n, x) for _, n, x in RG.fattori_calibrazione(mis, r, per_M=True))
    assert f["<5k"] == 1.0 and f["5-10k"] == 1.0 and f["10-20k"] == 1.0          # sotto 20.000: mai
    assert f["20-35k"] == r.calibrazione.alza_max and f[">60k"] == r.calibrazione.alza_max
    # misura vecchia (per prezzo) o poche auto: niente
    assert all(x <= 1 for _, n, x in RG.fattori_calibrazione(mis, r, per_M=False) if not str(n).startswith("_"))
    assert all(x <= 1 for _, n, x in RG.fattori_calibrazione(_fasce(1.1, n=30), r, per_M=True)
               if not str(n).startswith("_"))


def test_quantili_nei_fattori_ignorati_dal_fattore():
    q = [.7, .8, .9, 1.0, 1.1]
    f = RG.fattori_calibrazione(_fasce(.9, q=q), per_M=True)
    assert any(n == "_q:<5k" and x == tuple(q) for _, n, x in f)
    f0 = RG.fattori_calibrazione(_fasce(.9), per_M=True)
    assert RG.fattore_per(8000, f) == RG.fattore_per(8000, f0)
    assert RG.quantili_di({"n": 100, "q": [1, .9, 1, 1, 1]}, 20) is None           # non crescente
    assert RG.quantili_di({"n": 5, "q": q}, 20) is None


def _gemelle(prezzi, **kw):
    return [car(100 + i, p, km=88000 + i * 500, **kw) for i, p in enumerate(prezzi)]


def test_combinato_porta_V_al_venduto_solo_con_misura():
    t = dict(car(1, 4000, year=2012, km=120000), vr={"v": 6000, "sigma": .12, "n": 200, "n_vendute": 60})
    t["fattori_calibra"] = RG.fattori_calibrazione({}, per_M=True)
    merc = [car(100 + i, p, year=2012, km=118000 + i * 300) for i, p in enumerate([7600, 7800, 8000, 8100, 8300, 8500,
                                                                                   8700, 8900])]
    v0 = P.valuta(t, merc, 1500, CFG, NOW)
    assert v0["metodo"] == "valore_reale_combinato" and "fattore_V" not in v0["valore_reale"]
    t2 = dict(t, fattori_calibra=RG.fattori_calibrazione({}, misurate_vr={"<5k": {"n": 50, "prezzo_su_M": .85},
                                                                             "5-10k": {"n": 50, "prezzo_su_M": .9}},
                                                         per_M=True))
    v1 = P.valuta(t2, merc, 1500, CFG, NOW)
    assert v1["valore_reale"]["fattore_V"] < 1 and v1["M"] < v0["M"]
    v2 = P.valuta(t2, merc, 1500, _cfg(**{"calibrazione.vr_nel_combinato": False}), NOW)
    assert v2["M"] == v0["M"]


# ------------------------------------------------------------------ tassi (pooling)

def test_priore_tassi_marca_tutti_riserva():
    c_ = RG.regole().correzione
    pr = {"marche": {"fiat": {"anno": .07, "km_10000": .02, "n": 900}, "seat": {"anno": .07, "km_10000": .02, "n": 50},
                     "bmw": {"anno": .40, "km_10000": .02, "n": 900}},
          "tutti": {"anno": .09, "km_10000": .018, "n": 50000}}
    assert P.priore_tassi(pr, "Fiat", c_)[:2] == (.07, .02)
    assert P.priore_tassi(pr, "seat", c_)[:2] == (.09, .018)              # marca con pochi annunci: tutti
    assert P.priore_tassi(pr, "bmw", c_)[:2] == (.09, .018)               # marca fuori limite: tutti
    assert P.priore_tassi(None, "fiat", c_)[:2] == (c_.svalutazione_anno, c_.svalutazione_10km)


def test_pooling_restringe_e_resta_nei_limiti():
    g = [car(100 + i, 0, year=2014 + i % 6, km=30000 + i * 9000) for i in range(12)]
    for c in g:                                         # vero: 10% per anno, 3% ogni 10.000 km
        c["price_eur"] = int(10000 * 1.1 ** (c["year"] - 2014) * .97 ** (c["mileage_km"] / 10000))
    ya, ka, origine = P.tassi(g, CFG)
    w = 12 / (12 + RG.regole().correzione.pooling_k)
    assert "ristretta" in origine and abs(ka - (w * .03 + (1 - w) * .012)) < .002
    assert abs(ya - (w * (1 - 1 / 1.1) + (1 - w) * .08)) < .003
    pr = {"marche": {"volkswagen": {"anno": .1, "km_10000": .03, "n": 1000}}}
    ya2, ka2, o2 = P.tassi(g, CFG, pr, "volkswagen")
    assert "marca" in o2 and abs(ka2 - .03) < .002
    ya3, ka3, o3 = P.tassi(g[:3], CFG, pr, "volkswagen")                 # troppo pochi: solo il priore
    assert (round(ya3, 4), round(ka3, 4)) == (.1, .03) and "a priori" in o3
    # regola di prima: 12 annunci (< 15) → riserva
    assert P.tassi(g, _cfg(**{"correzione.pooling": False}))[2] == "valori configurati"


def test_tassi_da_somme_della_misura():
    import random
    rnd = random.Random(3)
    righe = []
    for i in range(400):
        y, k = rnd.uniform(-4, 4), rnd.uniform(-5, 5)
        righe.append((y, k, -math.log(1 - .08) * y + math.log(1 - .02) * k + rnd.gauss(0, .01)))
    s = dict(n=len(righe), syy=sum(y * y for y, _, _ in righe), skk=sum(k * k for _, k, _ in righe),
             syk=sum(y * k for y, k, _ in righe), syl=sum(y * l for y, _, l in righe), skl=sum(k * l for _, k, l in righe))
    t = CB.tassi_da_somme(**s)
    assert abs(t["anno"] - .08) < .005 and abs(t["km_10000"] - .02) < .003
    assert CB.tassi_da_somme(0, 0, 0, 0, 0, 0) is None
    assert CB.quantili([1, 2, 3, 4, 5]) == [1.4, 2.0, 3.0, 4.0, 4.6] and CB.quantili([1, 2]) is None


def test_due_sensi_solo_per_le_auto_care_se_acceso():
    t = car(1, 25000, year=2019, km=60000)
    merc = [car(100 + i, 30000, year=2017, km=60000) for i in range(8)]    # confronti più vecchi di 2 anni
    v0 = P.valuta(t, merc, 4000, CFG, NOW)
    v1 = P.valuta(t, merc, 4000, _cfg(**{"correzione.due_sensi_alto": True}), NOW)
    assert v1["M"] > v0["M"] and v1["correzione"]["due_sensi"] and not v0["correzione"]["due_sensi"]
    t2 = car(2, 9000, year=2019, km=60000)                                   # sotto 20.000: come prima
    assert P.valuta(t2, merc, 1800, _cfg(**{"correzione.due_sensi_alto": True}), NOW)["M"] == \
        P.valuta(t2, merc, 1800, CFG, NOW)["M"]


# ------------------------------------------------------------------ trazione e optional

def test_trazione_di():
    assert DT.trazione_di({"version_raw": "2.0 TDI 4MOTION Highline"}) == "4x4"
    assert DT.trazione_di({"title": "BMW X1 sDrive18d"}) == "2wd"
    assert DT.trazione_di({"trazione_dati": "Integrale", "version_raw": "2.0"}) == "4x4"
    assert DT.trazione_di({"trazione_dati": "Anteriore", "version_raw": "4x4"}) == "2wd"   # la scheda vince
    assert DT.trazione_di({"version_raw": "1.6 TDI"}) is None


def test_trazione_a_livelli():
    r = RG.regole()
    q = [dict(car(100 + i, 20000), version_raw="2.0 TDI 4motion") for i in range(3)]
    d = [dict(car(200 + i, 17000), version_raw="2.0 TDI") for i in range(6)]
    t4 = dict(car(1, 15000), version_raw="2.0 TDI 4Motion")
    tenuti, via, nota = DT.per_trazione(t4, q + d, 2, r)
    assert tenuti == q and len(via) == 6 and nota is None
    tenuti, via, nota = DT.per_trazione(t4, q[:1] + d, 2, r)               # un solo 4x4: tutti, con avviso
    assert len(tenuti) == 7 and "4x4" in nota
    t2 = dict(car(2, 12000), version_raw="2.0 TDI")
    tenuti, via, _ = DT.per_trazione(t2, q + d, 2, r)
    assert tenuti == d and len(via) == 3
    tenuti, _, _ = DT.per_trazione(t2, q + d[:2], 2, r)                    # 4x4 in maggioranza e auto muta: tutti
    assert len(tenuti) == 5
    off = RG.con_campi(r, {"trazione.attiva": False})
    assert DT.per_trazione(t4, q + d, 2, off)[0] == q + d


def test_trazione_nella_valutazione():
    q = [dict(car(100 + i, 21000 + i * 100, km=90000 + i * 300), version_raw="2.0 TDI 4motion") for i in range(6)]
    d = [dict(car(200 + i, 17000 + i * 100, km=90000 + i * 300), version_raw="2.0 TDI") for i in range(10)]
    t4 = dict(car(1, 16000), version_raw="2.0 TDI 4motion")
    v = P.valuta(t4, q + d, 3200, CFG, NOW)
    v0 = P.valuta(t4, q + d, 3200, _cfg(**{"trazione.attiva": False}), NOW)
    assert v["n_gemelle"] == 6 and v0["n_gemelle"] == 16 and v["M"] > v0["M"]
    assert v["non_equivalenti"].get("trazione diversa (non 4x4)") == 10


def test_premio_dotazioni_e_aggiustamento_spento():
    p = DT.premio([(.05, 10, 10), (.03, 20, 20), (.04, 5, 30)])
    assert p["celle"] == 3 and 0.03 < p["premio"] < 0.05 and p["n_con"] == 35 and p["se"] > 0
    r = RG.regole()
    mis = {"pelle": {"premio": .05, "se": .005, "n_con": 300}, "navigatore": {"premio": .01, "se": .02, "n_con": 300}}
    assert DT.misurato(mis["pelle"], r) == r.dotazioni.tetto_voce                # tetto per voce
    assert DT.misurato(mis["navigatore"], r) is None                             # non significativo
    t = {"opt": {"pelle": True, "navigatore": False, "tetto": False}}
    c = {"opt": {"pelle": False, "navigatore": False, "tetto": False}}
    assert DT.fattore(t, c, mis, r) == 1.0                                       # spento di default
    on = RG.con_campi(r, {"dotazioni.aggiusta": True})
    assert abs(DT.fattore(t, c, mis, on) - math.exp(.04)) < 1e-9
    assert DT.fattore(t, {"opt": None}, mis, on) == 1.0                          # optional del confronto non noti
    assert DT.arricchisci([{"id": 1}, {"id": 2}], {1: {"trazione_dati": "Integrale", "opt": None}})[0]["trazione_dati"]


def test_dotazioni_senza_colonna_nessuna_query():
    class Conn:
        sql = []

        def execute(self, s, a=()):
            self.sql.append(s)
            raise AssertionError("nessuna query senza colonna")
    import dealfinder.db as DB
    orig = DB.ha_colonna
    DB.ha_colonna = lambda conn, nome: False
    try:
        assert DT.carica_gruppo(Conn(), "fiat", "panda", "benzina") == {} and DT.misura(Conn()) == {}
    finally:
        DB.ha_colonna = orig


# ------------------------------------------------------------------ forbice della rivendita

Q = [.70, .80, .90, 1.0, 1.08]


def _fatt():
    return RG.fattori_calibrazione(_fasce(.9, q=Q), misurate_vr={}, per_M=True)


def test_forbice_da_annunci_e_probabilita():
    pv = {"M": 10000, "R": 9000, "R_finale": 9000, "M_fonte": "annunci", "metodo": "mediana", "margine": 2000}
    f = FB.calcola(pv, _fatt())
    assert (f["p25"], f["p50"], f["p75"]) == (8000, 9000, 10000) and f["base"] == "annunci"
    assert f["prob_R"] == .5
    assert FB.probabilita(8000, [7000, 8000, 9000, 10000, 10800]) == .75
    assert FB.probabilita(20000, [7000, 8000, 9000, 10000, 10800]) == .05      # limiti 5–95%
    assert FB.probabilita(1000, [7000, 8000, 9000, 10000, 10800]) == .95
    # valore reale senza la sua misura: solo la larghezza, centrata su M
    g = FB.calcola(dict(pv, M_fonte="vendite", metodo="valore_reale_combinato"), _fatt())
    assert g["base"] == "larghezza" and g["p50"] == 10000 and g["p25"] == 8900
    assert FB.calcola(pv, RG.fattori_calibrazione({}, per_M=True)) is None       # senza quantili: niente
    off = RG.con_campi(RG.regole(), {"forbice.attiva": False})
    assert FB.calcola(pv, _fatt(), off) is None


def test_forbice_sul_sito_non_cambia_il_margine():
    g = SR.Regole(fattori_calibra=tuple(_fatt()))
    it = {"id": 1, "prezzo": 7000, "pv": {"M": 10000, "R": 10000, "R_finale": 10000, "M_fonte": "annunci",
                                         "metodo": "mediana", "margine": 3000, "categoria": "opportunita"}}
    SR.t_margine_sito(it, g)
    pv = it["pv"]
    assert pv["forbice_R"]["p25"] == 8000 and pv["R"] == 9000 and pv["margine"] == 2000   # solo la calibrazione
    assert pv["forbice_R"]["prob_R"] == .5
    js = open(os.path.join(QUI, "dealfinder", "web", "static", "scovo", "app.js"), encoding="utf-8").read()
    assert "function boxForbice(" in js and "${boxForbice(p)}" in js and "function probVendita(" in js


# ------------------------------------------------------------------ guardia delle 36 ore

def test_giudicata_si_scrive_sempre():
    from datetime import datetime, timezone
    ora = datetime.now(timezone.utc)
    es = {"n": 20, "impronta_auto": "a", "impronta_regole": "r", "computed_at": ora}
    assert VZ.deve_scrivere(es, 12, "a", "r", ora) == (False, "meno_confronti")
    assert VZ.deve_scrivere(es, 12, "a", "r", ora, giudicata_nuova=True) == (True, "giudicata")
    assert VZ.giudicata({"ai_confronti": {"tolti": 3}}) and not VZ.giudicata({"ai_confronti": {"tolti": 0}})
    assert "OR %s" in VZ.UPSERT_SQL and VZ.UPSERT_SQL.count("%s") == 14


# ------------------------------------------------------------------ statistiche della scheda

def test_statistiche_scheda_campione_uniforme():
    q = TR.stats_sql(True)[1]
    assert "md5(l.id::text)" in q and f"LIMIT {TR.STATS_TETTO}" in q and "disappeared_at DESC" not in q
    q0 = TR.stats_sql(False)[1]
    assert "ORDER BY l.disappeared_at DESC LIMIT 40000" in q0
    assert all("{" not in x for x in TR.stats_sql(True))


# ------------------------------------------------------------------ prova alla cieca

def test_prova_cieca_per_fascia_e_prima_dopo():
    righe = [{"vero": 4000, "m_calcolo": 5200, "m_prima": 5200, "m_dopo": 4600, "m_tutte": 4600},
             {"vero": 4500, "m_calcolo": 5400, "m_prima": 5400, "m_dopo": 4900, "m_tutte": 4900},
             {"vero": 30000, "m_calcolo": 26000, "m_prima": 26000, "m_dopo": 26500, "m_tutte": 29000},
             {"vero": 12000, "m_calcolo": 12500, "m_prima": None, "m_dopo": 12400, "m_tutte": 12400}]
    res = PC.riepilogo(righe)
    pd = res["prima_dopo_per_fascia_M"]
    assert pd["5-10k"]["m_prima"]["n"] == 2 and pd["5-10k"]["m_dopo"]["scostamento_medio_pct"] < \
        pd["5-10k"]["m_prima"]["scostamento_medio_pct"]
    assert pd["20-35k"]["m_tutte"]["errore_medio_eur"] == 1000
    assert res["prima_dopo"]["m_prima"]["n"] == 3                        # solo le auto con tutte le stime
    assert res["per_fascia_prezzo"]["<5k"]["m_calcolo"]["n"] == 2
    assert set(res["per_sconto_apparente"]) <= {n for _, n in PC.SCONTO}
    v = PC.regole_varianti()
    assert not v["prima"].correzione.pooling and v["dopo"].correzione.pooling and v["tutte"].dotazioni.aggiusta


def test_calibra_vendita_oltre_uno_solo_con_alza_e_mai_sopra_i_commercianti():
    mis = _fasce(1.10)
    pv = {"M": 30000, "R": 30000, "R_finale": 30000, "M_fonte": "annunci", "metodo": "modello", "margine": 5000}
    f0 = RG.fattori_calibrazione(mis, per_M=True)
    assert RG.calibra_vendita(pv, f0)["R"] == 30000                                  # spento: niente
    r = RG.con_campi(RG.regole(), {"calibrazione.alza": True})
    f1 = RG.fattori_calibrazione(mis, r, per_M=True)
    out = RG.calibra_vendita(pv, f1, r)
    assert out["R"] == 31500 and out["margine"] == 6500
    assert RG.calibra_vendita(dict(pv, R_commercianti=30500), f1, r)["R"] == 30500
    assert RG.calibra_vendita(dict(pv, R=29000, R_finale=29000), f1, r)["R"] == 29000   # R già sotto M: resta


def test_simula_fase2_gira_sul_dump_di_prova():
    import json
    import subprocess
    import sys
    import tempfile
    righe = [{"_tipo": "DUMP_CANDIDATI", "id": 1, "auto": "Fiat Panda 1.2 Easy", "prezzo": 4300, "M": 6000, "R": 6000,
              "margine": 1700, "cat": "opportunita", "sito": True, "metodo": "valore_reale_combinato"},
             {"_tipo": "DUMP_CANDIDATI", "id": 2, "auto": "BMW X3 xDrive20d", "prezzo": 21000, "M": 26000, "R": 26000,
              "margine": 5000, "cat": "opportunita", "sito": True, "metodo": "modello"}]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "dump.jsonl")
        open(p, "w").write("\n".join(json.dumps(x) for x in righe))
        out = subprocess.run([sys.executable, os.path.join(QUI, "scripts", "simula_fase2.py"), p, "--vr", "<5k:0.8,5-10k:0.9"],
                             capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "**Totale** | 2 | 1 |" in out.stdout                    # la Panda combinata esce (V al venduto)
