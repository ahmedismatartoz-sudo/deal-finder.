"""12/11 (ramo margini-precisi): correzioni del rapporto 2 §4, allestimento, rivendita dai commercianti, netto,
simulazione. Titoli veri presi dai log DUMP_CANDIDATI del 12/11."""
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta

from dealfinder import regole as RG
from dealfinder.pricing import allestimento as AL
from dealfinder.pricing import netto as NT
from dealfinder.pricing import policy_v2 as P
from dealfinder.pricing import rivendita as RV
from tests.test_policy import CFG, NOW, car

QUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _liv(nome):
    marca = " ".join(nome.split()[:2]) if nome.startswith(("Land Rover", "Alfa Romeo")) else nome.split()[0]
    r = AL.livello({"nome": nome, "marca": marca})
    return r["livello"] if r else None


# ------------------------------------------------------------------ allestimento

TITOLI = [   # (titolo vero dai log, livello atteso)
    ("Fiat 500 1.2 Pop", 0), ("Fiat 500 1.2 Lounge", 2), ("Fiat 500 1.0 Hybrid Dolcevita", 2),
    ("Fiat 500 1.2 Sport 69cv", 3), ("Fiat Panda 1.3 MJT 16V DPF Active", 0), ("Fiat Qubo 1.4 8v Easy 77cv", 1),
    ("Fiat Panda Cross 0.9 TwinAir Turbo S&S", 3), ("Fiat 500X 1.3 MultiJet 95 CV S-Design", 3),
    ("Fiat Tipo 5p 1.6 mjt Business s&s", 1), ("Fiat Bravo 1.6 MJT 120 CV DPF Dynamic", 1),
    ("Volkswagen Polo 1.4 TDI 5p. Comfortline", 1), ("Volkswagen Polo 5p 1.6 tdi Highline 90cv dsg", 2),
    ("Volkswagen Polo 1.0 TSI R-Line", 3), ("Volkswagen Golf 1.0 etsi evo Life 110cv dsg", 1),
    ("Volkswagen T ROC I 2017 1.6 tdi Style", 2), ("Volkswagen Tiguan 2.0 tdi R-Line BM 4motion", 3),
    ("Audi A1 1.6 TDI Attraction", 0), ("Audi Q2 1.6 tdi Business s-tronic", 1),
    ("Audi Q3 Sportback 35 2.0 tdi S line", 3), ("Audi A1 SPB 30 TFSI S tronic S line", 3),
    ("BMW Serie 1 116d Advantage 5p auto", 1), ("BMW Serie 1 118d 5p Sport", 2), ("BMW X1 sdrive18i Futura", 2),
    ("BMW Serie 3 330i Msport auto", 3), ("BMW Serie 4 428i Cabrio M Sport Luxury", 3), ("BMW X5 xDrive45e xLine", 2),
    ("Mercedes-Benz Classe A A 180 Automatic Executive", 1), ("Mercedes-Benz GLA 200 d Sport 4matic auto", 2),
    ("Mercedes-Benz GLB AMG Line Premium auto", 3), ("Mercedes-Benz CLA d Premium 4matic auto FL", 3),
    ("Toyota Yaris 1.5 Hybrid 5 porte Trend", 1), ("Toyota Yaris 1.5 Hybrid 5 porte Lounge", 2),
    ("Toyota Yaris 1.5 130 Hybrid 5 porte GR", 3), ("Toyota Aygo II 2014 5p 1.0 x-play", 0),
    ("Renault Captur I 2013 0.9 tce Life (wave)", 0), ("Renault Twingo SCe Zen", 1),
    ("Renault Captur 1.0 tce Intens Gpl 100cv", 2), ("Renault Captur 1.5 dci Initiale Paris 110cv", 3),
    ("Peugeot 107 1.0 68CV 3p. Access", 0), ("Peugeot 2008 BlueHDi 100 S&S Active", 1),
    ("Peugeot 2008 1.6 bluehdi 120cv Allure", 2), ("Peugeot 5008 1.5 bluehdi GT Line s&s", 3),
    ("Opel Corsa 1.4 5 porte Advance", 1), ("Opel Crossland 1.2 12V Start&Stop Elegance", 2),
    ("Opel Grandland X 1.2 Ultimate s&s 130cv", 3), ("Ford Fiesta 1.2 1.25 Trend", 1),
    ("Ford Ecosport 1.0 ecoboost Titanium 100cv", 2), ("Ford Puma 1.0 ecoboost h ST-Line s&s", 3),
    ("Jeep Wrangler 2.8 CRD DPF Sport Auto", 0), ("Jeep Compass 1.3 Turbo T4 2WD Longitude", 1),
    ("Jeep Renegade 1.6 Mjt 120 CV Limited", 2), ("Jeep Compass 1.3 turbo t4 phev Trailhawk", 3),
    ("MINI Mini 1.5 Classic auto", 0), ("MINI Mini 2.0 Hype 3p", 2),
    ("Alfa Romeo Giulietta 1.6 jtdm-2 Progression", 0), ("Alfa Romeo Giulietta 2.0 jtdm(2) Distinctive", 1),
    ("Land Rover Range Rover Sport 3.0 SDV6 249 CV HSE", 2),
    # 12/11, controllo del giudice (prova3): falsi positivi corretti
    ("Fiat 500L 1.3 Mjt 95 CV Pop Star", 1), ("Fiat Panda 1.2 City Cross", 2), ("Fiat Panda 1.2 Easypower", None),
    ("Fiat Punto 1.4 8V 5 porte Easypower Lounge", 2), ("Land Rover Discovery 3.0 SDV6 tetto e pelle", None),
    ("Volvo XC60 B4 Geartronic Momentum Pro", 1), ("Kia Picanto 1.0 12V 5 porte Cool Plus", 0),
    ("Hyundai Tucson 1.6 CRDi XPrime", 2),
]


def test_allestimento_titoli_veri():
    sbagliati = [(t, atteso, _liv(t)) for t, atteso in TITOLI if _liv(t) != atteso]
    assert not sbagliati, sbagliati


def test_allestimento_non_confonde_cambio_carrozzeria_e_modello():
    # S tronic (cambio), Sportback (carrozzeria), T-Cross / Yaris Cross (modello), Cooper S (motore), s&s
    for t in ("Audi A3 SPB 35 TDI S tronic", "Audi A3 Sportback 2.0 tdi", "MINI Mini 2.0 Cooper S 5 porte",
              "Opel Astra 1.5 CDTI 105 CV S&S Sports Tourer", "BMW Serie 2 218d Active Tourer"):
        assert _liv(t) is None, t
    assert AL.livello({"make": "volkswagen", "model": "t-cross", "title": "Volkswagen T-Cross 1.0 TSI"}) is None
    assert AL.livello({"make": "toyota", "model": "yaris-cross", "title": "Toyota Yaris Cross 1.5 Hybrid"}) is None
    assert _liv("Opel Mokka X 1.6 cdti Advance s") == 1          # la "s" rimasta da "s&s" non è un allestimento


def test_allestimento_scheda_e_descrizione():
    a = AL.allestimento({"make": "fiat", "model": "panda", "title": "Fiat Panda 1.2", "version_raw": None,
                         "description": "Vendo panda, allestimento Lounge, tagliandata"})
    assert a == {"classe": "lounge", "nome": "Lounge", "livello": 2, "origine": "descrizione", "descrizione": "alto"}
    assert AL.allestimento({"make": "fiat", "title": "Fiat Panda 1.2"}) is None


def test_allestimento_su_tutti_i_titoli_dei_log_senza_errori():
    p = "/tmp/claude-0/-home-claude/2b3941a8-afca-589e-82ac-250cc5efc30e/scratchpad/simula/titoli.txt"
    if not os.path.exists(p):
        return
    titoli = open(p, encoding="utf-8").read().splitlines()
    riconosciuti = sum(1 for t in titoli if _liv(t) is not None)
    assert len(titoli) > 1500 and riconosciuti / len(titoli) > .35


def _trim(i, prezzo, versione, **kw):
    return car(i, prezzo, version_raw=versione, year=2019, km=60000 + i * 100, fuel="benzina", make="fiat",
               model="panda", **kw)


def test_confronti_stesso_allestimento_prima():
    t = _trim(1, 5000, "Panda 1.2 Pop")
    mk = [_trim(200 + i, p, "Panda 1.2 Pop") for i, p in enumerate([8000, 8100, 8200, 8300, 8400, 8500, 8600, 8700])]
    mk += [_trim(300 + i, p, "Panda 1.2 Cross") for i, p in enumerate([11000, 11500, 12000, 12500])]
    v = P.valuta(t, mk, 1500, CFG, NOW)
    assert v["n_gemelle"] == 8 and v["M"] <= 8700          # le Cross (livello 3) non entrano


def test_confronti_livello_diverso_portati_al_livello_mai_oltre():
    t = _trim(1, 5000, "Panda 1.2 Lounge")                 # livello 2: le Cross (3) scendono del 4%, le Pop (0) salgono
    mk = [_trim(200 + i, 10000, "Panda 1.2 Cross") for i in range(3)]
    sel = P.seleziona(t, mk, CFG, NOW)
    assert all(abs(c["prezzo"] / 10000 - .96) < .01 for c in sel["validi"])
    mk = [_trim(300 + i, 10000, "Panda 1.2 Pop") for i in range(3)]
    sel = P.seleziona(t, mk, CFG, NOW)
    f = (1 - RG.regole().allestimento.fattore) ** -1
    assert all(abs(c["prezzo"] / 10000 - f) < .01 for c in sel["validi"])      # Pop: al massimo UN livello in su
    mk = [_trim(400 + i, 10000, "Panda 1.2") for i in range(3)]                 # senza allestimento: nessuna correzione
    assert all(c["prezzo"] == 10000 for c in P.seleziona(t, mk, CFG, NOW)["validi"])


def test_interruttore_allestimento():
    r = RG.regole({"ALLESTIMENTO_CONFRONTI": "0", "RIVENDITA_COMMERCIANTI": "0"})
    assert r.allestimento.attivo is False and r.rivendita.attiva is False
    r = RG.regole({})
    assert r.allestimento.attivo is True and r.rivendita.attiva is True


# ------------------------------------------------------------------ rapporto 2 §4

def test_giudice_vede_campione_per_somiglianza_non_i_piu_economici():
    t = car(1, 8000, year=2018, km=90000)
    mk = [car(100 + i, 9000 + i * 60, year=2018 - (i % 3), km=60000 + (i * 997) % 60000) for i in range(80)]
    v = P.valuta(t, mk, 1500, CFG, NOW)
    corr = [c["prezzo_corretto"] for c in v["confronti"]]
    assert len(corr) == 40 and corr == sorted(corr)           # campione di 40, mostrato in ordine di prezzo
    tutti = sorted(int(round(c["prezzo"])) for c in P.seleziona(t, mk, CFG, NOW)["validi"])
    assert len(tutti) > 40 and corr != tutti[:40] and max(corr) > tutti[39]   # non sono i 40 più economici
    vicini = sorted(P.seleziona(t, mk, CFG, NOW)["validi"], key=lambda c: P.somiglianza(t, c))[:40]
    assert {c["id"] for c in v["confronti"]} == {c["id"] for c in vicini}


def test_calibrazione_misura_vecchia_non_alza_il_fattore():
    # misura per fascia di prezzo (senza "_per_M"): l'interpolazione può solo abbassare il fattore della fascia
    fat = RG.fattori_calibrazione({"<5k": {"n": 100, "prezzo_su_M": .8}, "5-10k": {"n": 100, "prezzo_su_M": .98}})
    assert RG.fattore_per(4900, fat) == .8 and RG.fattore_per(3600, fat) == .8
    assert .8 < RG.fattore_per(5400, fat) < .98
    assert RG.calibrazione_vecchia({"fasce": {"<5k": {}}, "correzione_due_sensi": False})
    assert not RG.calibrazione_vecchia({"fasce": {"<5k": {}}, "fasce_su": "M", "correzione_due_sensi": False})


def test_calibrazione_senza_scalino_a_5000():
    fat = RG.fattori_calibrazione({"<5k": {"n": 100, "prezzo_su_M": .8}, "5-10k": {"n": 100, "prezzo_su_M": .98},
                                   "10-20k": {"n": 100, "prezzo_su_M": .99}}, per_M=True)
    a, b = RG.fattore_per(4990, fat), RG.fattore_per(5010, fat)
    assert abs(a - b) < .002                                 # prima 0,80 contro 0,98
    assert RG.fattore_per(3000, fat) == .8 and abs(RG.fattore_per(7071, fat) - .98) < 1e-9
    xs = [RG.fattore_per(m, fat) for m in range(3500, 14200, 250)]
    assert xs == sorted(xs)                                  # monotono tra i centri
    pv = {"M": 4900, "R": 4900, "R_finale": 4900, "margine": 1600, "M_fonte": "annunci"}
    pv2 = RG.calibra_vendita(pv, fat)
    assert 4900 * .8 < pv2["R"] < 4900 * .98 and pv2["margine"] == 1600 - (4900 - pv2["R"])


def test_calibrazione_valore_reale_solo_se_misurata():
    pv = {"M": 6000, "R": 6000, "R_finale": 6000, "margine": 2000, "M_fonte": "vendite", "metodo": "valore_reale"}
    fat = RG.fattori_calibrazione({"<5k": {"n": 100, "prezzo_su_M": .8}})
    assert RG.calibra_vendita(pv, fat) == pv                 # nessuna misura sul valore reale: non si tocca
    fat_vr = RG.fattori_calibrazione({}, misurate_vr={"5-10k": {"n": 50, "prezzo_su_M": .95},
                                                      "<5k": {"n": 50, "prezzo_su_M": .95}})
    pv2 = RG.calibra_vendita(pv, fat_vr)
    assert abs(pv2["R"] - 6000 * .95) <= 1 and pv2["calibrato"] == .95
    fat_poche = RG.fattori_calibrazione({}, misurate_vr={"5-10k": {"n": 5, "prezzo_su_M": .8}})
    assert RG.calibra_vendita(pv, fat_poche) == pv           # meno di n_min auto misurate: niente
    # misurata solo <5k: un M da 6.000 (fascia 5-10k, non misurata) resta com'è, niente interpolazione verso <5k
    fat_solo = RG.fattori_calibrazione({}, misurate_vr={"<5k": {"n": 50, "prezzo_su_M": .85}})
    assert RG.fattore_per(7071, fat_solo, vr=True) == 1.0 and RG.fattore_per(3500, fat_solo, vr=True) == .85
    assert .85 < RG.fattore_per(6000, fat_solo, vr=True) < 1.0       # verso 1,0 fino al centro della fascia vicina
    assert RG.calibra_vendita(dict(pv, metodo="valore_reale_combinato"), fat_vr) == dict(pv, metodo="valore_reale_combinato")


def test_calibrazione_con_rivendita_commercianti_prende_il_minimo():
    fat = RG.fattori_calibrazione({"5-10k": {"n": 100, "prezzo_su_M": .9}, "10-20k": {"n": 100, "prezzo_su_M": .9}},
                                  per_M=True)
    pv = {"M": 9000, "R": 8500, "R_finale": 8500, "margine": 1500, "M_fonte": "annunci", "R_commercianti": 8500,
          "R_da": "commercianti"}
    pv2 = RG.calibra_vendita(pv, fat)                       # M × 0,9 = 8.100 < 8.500
    assert pv2["R"] == 8100 and pv2["margine"] == 1100
    pv = dict(pv, R=9000, R_finale=9000, margine=2000, R_commercianti=7800, R_da="mercato vero")
    pv2 = RG.calibra_vendita(pv, fat)                       # R_commercianti sotto M × f: vale quella
    assert pv2["R"] == 7800 and pv2["margine"] == 800


def _vr(v, sigma=.1, n_vendute=30):
    return {"v": v, "p25": int(v * .9), "sigma": sigma, "n": 200, "n_vendute": n_vendute}


def test_pochi_confronti_dispersi_dati_insufficienti():
    t = car(1, 9000, year=2016, km=80000)
    mk = [car(100, 10000, year=2016, km=80000), car(101, 14000, year=2016, km=81000), car(102, 19000, year=2016, km=79000)]
    v = P.valuta(t, mk, 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti" and v["dispersione"] == 1.9
    # con un valore reale affidabile si usa quello (mai le 3 auto disperse), al massimo candidato
    v = P.valuta(dict(t, vr=_vr(13000)), mk, 1500, CFG, NOW)
    assert v["M"] == 13000 and v["metodo"] == "valore_reale_modello" and v["categoria"] != "opportunita"
    # 3 auto concordi: si usano
    mk2 = [car(100, 12000, year=2016, km=80000), car(101, 12500, year=2016, km=81000), car(102, 13000, year=2016, km=79000)]
    assert P.valuta(t, mk2, 1500, CFG, NOW).get("dispersione") is None


def test_errore_gemelle_con_2_confronti_lontani():
    s_vicini = P._errore_gemelle([10000, 10200], None, "mediana", .10, .05)
    s_lontani = P._errore_gemelle([18000, 39900], None, "mediana", .10, .05)
    assert s_lontani > 4 * s_vicini                          # prima uguali (si usava sigma_V)
    M, pesi = RG.media_pesata_log([(17000, .10), (26800, s_lontani)], .05)
    assert pesi[0] > .9 and M < 18000                        # caso 470446: prima M = 20.056


def test_tasso_km_minimo():
    assert RG.regole().correzione.km_min == .008
    g = [car(100 + i, 10000 + (i % 3) * 50, year=2015 + i % 5, km=20000 + i * 7000) for i in range(30)]
    for c in g:                                              # prezzo dipende dall'anno, non dai km: tasso km 0%
        c["price_eur"] = int(8000 * 1.08 ** (c["year"] - 2015))
    ya, ka, origine = P.tassi(g, CFG)
    assert origine == "valori configurati" and ka == RG.regole().correzione.svalutazione_10km


def test_esame_non_chiama_il_giudice(monkeypatch=None):
    """Le misure (esame) non pagano chiamate AI nuove e non scrivono GIUDICE_AI."""
    from dealfinder.ai import giudice
    chiamate = []
    orig_chiedi, orig_av = giudice._chiedi, giudice.AI.available
    giudice._chiedi = lambda *a, **k: chiamate.append(1) or "NOTA ok"
    giudice.AI.available = lambda: True

    class Conn:
        def execute(self, *a, **k):
            class R:
                def fetchone(self_):
                    return None

                def fetchall(self_):
                    return []
            return R()

        def rollback(self):
            pass
    try:
        assert giudice.giudica(Conn(), {"id": 1, "price_eur": 1}, [{"id": 5}], solo_archivio=True) is None
        assert not chiamate
    finally:
        giudice._chiedi, giudice.AI.available = orig_chiedi, orig_av


# ------------------------------------------------------------------ rivendita commercianti

def _comm(i, prezzo, venduta_giorni=None, prezzo_iniziale=None, **kw):
    d = car(i, prezzo, seller_type="commerciante", km=90000 + i * 10, **kw)
    if venduta_giorni is not None:
        d.update(status="scomparso", first_seen_at=NOW - timedelta(days=40),
                 disappeared_at=NOW - timedelta(days=40 - venduta_giorni), riapparso=False)
    d["prezzo_iniziale"] = prezzo_iniziale or prezzo
    return d


def test_rivendita_dai_commercianti_venduti():
    t = car(1, 7000)
    comm = [_comm(400 + i, p, venduta_giorni=g, prezzo_iniziale=p + 400)
            for i, (p, g) in enumerate([(10500, 20), (10800, 30), (11000, 15), (11200, 25), (11500, 35)])]
    comm.append(_comm(450, 30000, venduta_giorni=10, fuel="benzina"))        # carburante diverso: fuori
    comm.append(_comm(451, 11000, venduta_giorni=80))                        # sparita dopo 80 giorni: non venduta
    rc = RV.calcola(t, comm, CFG, NOW)
    assert rc["fonte"] == "venduti" and rc["n_venduti"] == 5 and rc["giorni_vendita"] == 25
    assert 10700 <= rc["R"] <= 11200
    assert rc["sconto"] is not None and .025 < rc["sconto"] < .035   # media del gruppo: 5 × 400 € su ~11.000 e una a 0
    # mercato più basso dei commercianti (~9.800): R resta M, R_commercianti a parte (cliente: "non gonfiare")
    mk = [car(100 + n, p, km=85000 + n * 900) for n, p in enumerate([9000, 9200, 9400, 9500, 9600, 9800, 10000, 10100])]
    v0 = P.valuta(dict(t), mk, 1500, CFG, NOW)
    v = P.valuta(dict(t, commercianti=comm), mk, 1500, CFG, NOW)
    assert v["R"] == v["M"] == v0["R"] and v["margine"] == v0["margine"] and v["R_commercianti"] == rc["R"]
    assert v["R_da"].startswith("mercato vero") and v["categoria"] == v0["categoria"]
    assert v["giorni_vendita_commercianti"] == 25 and v["n_dati_rivendita"] == 5
    assert v["netto"] < v["margine"] and v["costi_netto"]["sconto_da"] == "valore standard"
    # mercato più alto dei commercianti: R scende al prezzo dei commercianti
    mk_alto = [car(100 + n, p, km=85000 + n * 900) for n, p in enumerate([12500, 12600, 12800, 13000, 13100, 13300, 13500, 13600])]
    v = P.valuta(dict(t, commercianti=comm), mk_alto, 1500, CFG, NOW)
    assert v["R_da"] == "commercianti" and v["R"] == rc["R"] < v["M"]
    assert v["margine"] == rc["R"] - 7000 - v["totale_costi"]


def test_prova1_commercianti_sopra_m_non_cambiano_niente():
    """Controllo del giudice, prova1: auto a 8.000 €, M 9.529 €, commercianti venduti a ~11.400 €: prima R 11.400 e
    margine 3.400 (opportunità); ora R e margine invariati, anche dopo la calibrazione del sito."""
    t = car(1, 8000)
    mk = [car(100 + n, p, km=85000 + n * 900) for n, p in enumerate([9000, 9200, 9400, 9500, 9600, 9800, 10000, 10100])]
    comm = [_comm(400 + i, p, venduta_giorni=g, prezzo_iniziale=p + 300)
            for i, (p, g) in enumerate([(10900, 20), (11200, 30), (11400, 15), (11600, 25), (11900, 35)])]
    v0 = P.valuta(dict(t), mk, 1800, CFG, NOW)
    v1 = P.valuta(dict(t, commercianti=comm), mk, 1800, CFG, NOW)
    assert v0["M"] == v1["M"] == 9529
    for k in ("R", "R_finale", "margine", "categoria", "netto"):
        assert v0[k] == v1[k], k
    fat = RG.fattori_calibrazione({"5-10k": {"n": 99, "prezzo_su_M": .95}, "<5k": {"n": 99, "prezzo_su_M": .8},
                                   "10-20k": {"n": 99, "prezzo_su_M": .97}})
    c0, c1 = RG.calibra_vendita(P.riassunto(v0), fat), RG.calibra_vendita(P.riassunto(v1), fat)
    assert (c0["R"], c0["margine"]) == (c1["R"], c1["margine"])


def test_gruppo_commercianti_senza_cache_per_id():
    """prova1: la cache per id(lista) dava lo sconto di un altro gruppo; ora i dati stanno sul Gruppo."""
    t = car(1, 8000)
    g1 = RV.Gruppo(_comm(600 + i, 10000, venduta_giorni=20, prezzo_iniziale=12000) for i in range(5))
    assert RV.calcola(t, g1, CFG, NOW)["sconto"] > .16
    del g1
    g2 = RV.Gruppo(_comm(700 + i, 10000, venduta_giorni=20, prezzo_iniziale=10000) for i in range(5))
    assert RV.calcola(t, g2, CFG, NOW)["sconto"] == 0
    import threading
    g3 = RV.Gruppo(_comm(800 + i, 10000, venduta_giorni=20, prezzo_iniziale=10500) for i in range(5))
    ris = []
    th = [threading.Thread(target=lambda: ris.append(RV.calcola(t, g3, CFG, NOW)["sconto"])) for _ in range(8)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert len(set(ris)) == 1 and len(g3._dati) == 1


def test_commercianti_ripubblicate_e_stage_reason():
    t = car(1, 7000)
    vend = [_comm(400 + i, 10500 + i * 100, venduta_giorni=20) for i in range(5)]
    assert RV.calcola(t, vend, CFG, NOW)["n_venduti"] == 5
    # un annuncio uguale ricompare 3 giorni dopo sullo stesso portale, stesso anno, km vicini: non è venduta
    nuova = _comm(900, 10900)
    nuova["mileage_km"] = vend[0]["mileage_km"] + 500
    nuova["first_seen_at"] = vend[0]["disappeared_at"] + timedelta(days=3)
    rip = RV.ripubblicate(vend + [nuova], RG.regole().rivendita)
    assert rip == {vend[4]["id"]}                            # una sola sparizione spiegata, la più vicina di km
    assert RV.calcola(t, vend + [nuova], CFG, NOW)["n_venduti"] == 4
    vend[1]["stage_reason"] = "doppione_autoscout"
    vend[2]["stage_reason"] = "Truffa probabile"
    assert RV.calcola(t, vend, CFG, NOW)["n_venduti"] == 3


def test_rivendita_dai_commercianti_attivi_con_sconto_osservato():
    t = car(1, 7000)
    vendute = [_comm(400 + i, 10000, venduta_giorni=20, prezzo_iniziale=10500) for i in range(5)]
    for c in vendute:                                       # vendute ma lontane (km): contano solo per lo sconto
        c["mileage_km"] = 10000
    attive = [_comm(500 + i, 11000 + i * 100) for i in range(8)]
    rc = RV.calcola(t, vendute + attive, CFG, NOW)
    assert rc["n_venduti"] == 0 and rc["n_attivi"] == 8 and rc["fonte"] == "attivi_scontati"
    assert abs(rc["sconto"] - 500 / 10500) < 1e-3
    assert rc["R"] == int(round(11350 * (1 - 500 / 10500)))


def test_rivendita_pochi_dati_resta_come_prima():
    t = car(1, 7000)
    rc = RV.calcola(t, [_comm(400, 10000, venduta_giorni=20), _comm(401, 11000)], CFG, NOW)
    assert rc["R"] is None and rc["n_venduti"] == 1 and rc["n_attivi"] == 1
    mk = [car(100 + n, p, km=85000 + n * 900) for n, p in enumerate([9000, 9200, 9400, 9500, 9600, 9800, 10000, 10100])]
    v = P.valuta(dict(t, commercianti=[]), mk, 1500, CFG, NOW)
    assert v["R"] == v["M"] and v["R_da"] == "mercato vero"
    assert RV.calcola(t, None, CFG, NOW) is None


# ------------------------------------------------------------------ netto

def test_netto_voci():
    r = NT.calcola(10000, 7000, 0)
    # regime del margine: IVA su (vendita 10.000 − 300 di trattativa) − acquisto 7.000 = 2.700 → 487
    assert r["voci"] == {"trattativa": 300, "passaggio": 180, "preparazione": 250, "garanzia": 150,
                         "ricambi_difetti": 0, "iva_margine": 487}
    assert r["netto"] == 10000 - 7000 - 880 - 487
    r = NT.calcola(30000, 20000, 500, sconto_osservato=.05)
    assert r["voci"]["trattativa"] == 1500 and r["voci"]["preparazione"] == 600 and r["voci"]["garanzia"] == 450
    assert NT.calcola(8000, 9000, 0)["voci"]["iva_margine"] == 0          # margine negativo: niente IVA
    assert NT.calcola(None, 9000) is None


def test_netto_segue_il_prezzo_mostrato():
    from dealfinder.web import scovo
    it = {"id": 1, "prezzo": 8000, "pv": {"R": 11000, "R_finale": 11000, "margine": 3000, "totale_costi": 0}}
    a = scovo.con_netto(it)
    b = scovo.con_netto(dict(it, prezzo=7000, prezzo_annuncio=8000))
    assert b["netto"] > a["netto"] and "costi_netto" in a
    assert scovo.con_netto({"id": 2, "prezzo": 5000}) == {"id": 2, "prezzo": 5000}
    tr = scovo.con_netto(dict(it, trattare={"prezzo_obiettivo": 7500, "sconto": 500, "margine": 3500}))
    assert tr["netto"] == NT.calcola(11000, 7500, 0)["netto"]


# ------------------------------------------------------------------ simulazione dai log

def test_simulazione_dai_log():
    righe = [{"id": 1, "auto": "Fiat Panda 1.2 Pop", "prezzo": 4000, "M": 5300, "R": 5300, "margine": 1300,
              "metodo": "mediana", "sito": False, "cat": "candidato", "_tipo": "DUMP_CANDIDATI"},
             {"id": 2, "auto": "Fiat 500 1.2 Lounge", "prezzo": 6000, "M": 8000, "R": 8000, "margine": 2000,
              "metodo": "valore_reale", "sito": True, "cat": "opportunita", "_tipo": "DUMP_CANDIDATI"},
             {"id": 3, "auto": "x", "prezzo": 6000, "M": 8000, "R": 8000, "margine": 2000, "_tipo": "CONTROLLO_FINALE"}]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "dump.jsonl")
        open(p, "w").write("\n".join(json.dumps(r) for r in righe))
        out = os.path.join(d, "r.md")
        r = subprocess.run([sys.executable, os.path.join(QUI, "scripts", "simula_margini.py"), p, out],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        testo = open(out).read()
    assert "| 5–7.000 € | 1 | 1 | 1 |" in testo and '"auto": 2' in testo


def test_giudice_riusa_i_giudizi_gia_dati():
    """Controllo del giudice M1: il campione cambia, ma i confronti già giudicati non si ripagano."""
    from dealfinder.ai import giudice
    chieste = []
    orig = giudice._chiedi, giudice.AI.available, giudice.AI.budget_finito
    giudice._chiedi = lambda conn, t, conf: chieste.append([c["id"] for c in conf]) or "VIA 3 versione diversa\nNOTA ok"
    giudice.AI.available = lambda: True
    giudice.AI.budget_finito = lambda conn: False

    class Conn:
        def __init__(self):
            self.righe = []

        def execute(self, sql, args=()):
            conn = self

            class R:
                def fetchone(self_):
                    return None

                def fetchall(self_):
                    return [dict(chiave=c, via=v, nota=n) for (lid, c, v, n) in conn.righe if lid == args[0]]
            if sql.startswith("INSERT INTO ai_giudizi"):
                self.righe.append(args)
            return R()

        def rollback(self):
            pass
    try:
        c = Conn()
        t = {"id": 1, "price_eur": 5000}
        g = giudice.giudica(c, t, [{"id": i} for i in (1, 2, 3)])
        assert g["via"] == {3: "versione diversa"} and chieste == [[1, 2, 3]]
        g = giudice.giudica(c, t, [{"id": i} for i in (2, 3)])          # campione diverso, tutti già giudicati
        assert g["via"] == {3: "versione diversa"} and g.get("da_archivio") and len(chieste) == 1
        g = giudice.giudica(c, t, [{"id": i} for i in (2, 3, 4)])       # solo il 4 è nuovo: l'AI vede solo lui
        assert chieste[-1] == [4] and g["via"] == {3: "versione diversa"}
        assert giudice.giudica(c, dict(t, price_eur=4500), [{"id": 2}], solo_archivio=True) is None   # prezzo cambiato
    finally:
        giudice._chiedi, giudice.AI.available, giudice.AI.budget_finito = orig


def test_simula_regole_vecchie_e_server_senza_dati():
    from dealfinder.pricing import simula
    v = simula.regole_vecchie(RG.regole())
    assert not v.allestimento.attivo and not v.rivendita.attiva and v.correzione.km_min == 0
    assert not v.calibrazione.interpola and not v.confronti.ampiezza_poche and v.confronti.dispersione_max > 100
    fat = RG.fattori_calibrazione({"<5k": {"n": 99, "prezzo_su_M": .8}, "5-10k": {"n": 99, "prezzo_su_M": .98}}, per_M=True)
    assert RG.fattore_per(5100, fat, v) == .98 and RG.fattore_per(4900, fat, v) == .8     # scalini come prima

    class R:
        def __init__(s, x):
            s.x = x

        def fetchall(s):
            return s.x

    class C:
        def execute(s, sql, args=None):
            return R([])

        def rollback(s):
            pass
    out = simula.esegui(C(), [1, 2])
    assert out["auto"] == 0 and out["sopra_soglia_solo_grazie_ai_commercianti"] == 0


def test_calibrazione_valore_reale_senza_salti():
    """Ricontrollo M-a: prima M 4.900 → 0,85 e M 5.100 → 1,0."""
    fat = RG.fattori_calibrazione({}, misurate_vr={"<5k": {"n": 50, "prezzo_su_M": .85},
                                                   "10-20k": {"n": 50, "prezzo_su_M": .95}})
    xs = [RG.fattore_per(m, fat, vr=True) for m in range(3000, 40000, 50)]
    assert max(abs(a - b) for a, b in zip(xs, xs[1:])) < .005
    assert abs(RG.fattore_per(4900, fat, vr=True) - RG.fattore_per(5100, fat, vr=True)) < .01
    assert RG.fattore_per(7071, fat, vr=True) == 1.0 and RG.fattore_per(26458, fat, vr=True) == 1.0


def test_calibrazione_r_da_chi_fa_il_minimo():
    fat = RG.fattori_calibrazione({"5-10k": {"n": 99, "prezzo_su_M": .9}, "10-20k": {"n": 99, "prezzo_su_M": .9}},
                                  per_M=True)
    base = {"M": 9000, "R": 8800, "R_finale": 8800, "margine": 1800, "M_fonte": "annunci", "R_commercianti": 8800,
            "R_da": "commercianti"}
    out = RG.calibra_vendita(base, fat)                       # M × 0,9 = 8.100 < 8.800: vale il mercato
    assert out["R"] == 8100 and out["R_da"].startswith("mercato vero")
    out = RG.calibra_vendita(dict(base, R=9000, R_finale=9000, R_commercianti=7900, R_da="mercato vero"), fat)
    assert out["R"] == 7900 and out["R_da"] == "commercianti"
