"""17/11 (notte, "M più preciso"): scelta delle gemelle per versione, potenza, cambio e carrozzeria anche quando le
colonne power_kw/gearbox mancano, con la scheda letta dal testo già salvato (pricing/versione_tecnica.py).

Tre famiglie di controlli:
  - NON REGRESSIONE contro il codice VERO di prima (c9f2436, copia in tests/dati/policy_v2_c9f2436.py.txt): con
    power_kw e gearbox nelle colonne di tutte le auto la scelta delle gemelle e M sono identici, anche con sigle
    sportive, cilindrate, carrozzerie nuove (Sportsvan, allroad: spente di default) o "clima automatico" nel testo;
  - PRECISIONE: un'auto base non prende più come gemelle le versioni top senza kW (GTI, S3, A 45 AMG…) e viceversa,
    il DSG scritto nel testo non si mescola col manuale; allestimenti/accessori ("RS3 look", "R-Line", "St. Wagon",
    "clima automatico") NON sono una versione diversa;
  - PRUDENZA su un campione realistico (sintetico, con la versione vera nota): quante auto vedono M salire rispetto a
    c9f2436 e perché; ogni aumento deve venire dal togliere gemelle davvero diverse (sportiva vera, cambio vero…).
Le versioni usate sono testi VERI delle righe DUMP_CANDIDATI del 16-17/11 (nessun dato del venditore)."""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone

from dealfinder.pricing import policy_v2 as P
from dealfinder.pricing import versione_tecnica as VT

NOW = datetime(2026, 11, 17, 3, tzinfo=timezone.utc)
CFG = P.PolicyConfig()
QUI = os.path.dirname(os.path.abspath(__file__))


def car(i, prezzo, versione="", kw=None, cambio="manuale", anno=2018, km=90000, make="volkswagen", model="golf",
        fuel="benzina", **x):
    d = {"id": i, "url": f"https://x/{i}", "source": "subito" if i % 2 else "autoscout24", "make": make, "model": model,
         "version_raw": versione, "year": anno, "mileage_km": km, "fuel": fuel, "gearbox": cambio, "power_kw": kw,
         "price_eur": prezzo, "price_flags": [], "status": "attivo", "seller_type": "privato",
         "damage_class": "sconosciuto", "last_checked_at": NOW - timedelta(days=1),
         "first_seen_at": NOW - timedelta(days=30), "vehicle_id": 5000 + i}
    d.update(x)
    return d


def _vecchio():
    """policy_v2.py com'era a c9f2436 (prima delle gemelle per versione), caricato come modulo del pacchetto pricing:
    gli altri moduli non sono cambiati, quindi è esattamente il codice di prima."""
    import importlib.machinery
    import importlib.util
    import sys
    nome = "dealfinder.pricing._policy_v2_c9f2436"
    if nome not in sys.modules:
        percorso = os.path.join(QUI, "dati", "policy_v2_c9f2436.py.txt")
        loader = importlib.machinery.SourceFileLoader(nome, percorso)
        spec = importlib.util.spec_from_loader(nome, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[nome] = mod
        loader.exec_module(mod)
    return sys.modules[nome]


def _con_e_senza(t, mercato):
    """(prima, adesso): valutazione col codice VERO di c9f2436 e con quello nuovo (bandiere ai default)."""
    V = _vecchio()
    v0 = V.valuta(dict(t), [dict(c) for c in mercato], soglia=2000, cfg=V.PolicyConfig(), now=NOW)
    v1 = P.valuta(dict(t), [dict(c) for c in mercato], soglia=2000, cfg=CFG, now=NOW)
    return v0, v1


def _ids(v):
    return sorted(c["id"] for c in (v.get("confronti") or []) if isinstance(c, dict))


# ------------------------------------------------------------------ funzioni pure della scheda

def test_variante_sportiva_dai_testi_veri():
    casi = {
        ("Volkswagen", "Golf GTI Performance 2.0 245 CV"): "gti", ("Volkswagen", "Golf 2.0 TDI DSG GTD"): "gtd",
        ("Volkswagen", "Golf 5p 2.0 tsi R 4motion"): "r", ("Volkswagen", "T ROC R 2.0 TSI DSG 4MOTION"): "r",
        ("Audi", "A3 S3 SPB 2.0 TFSI quattro S"): "s", ("Audi", "RS Q3 Sportback 2.5 quattro"): "rs",
        ("Audi", "TT TTS Coupé 2.0 TFSI 310 CV"): "s", ("BMW", "M4 Coupe 3.0 dkg"): "m",
        ("BMW", "Serie 3 G21 2022 Touring M340d"): "m_performance", ("Mercedes-Benz", "Classe A 2.0 A 45S AMG 4Matic+"): "amg45",
        ("Mercedes-Benz", "GLA 35 4Matic AMG Advanced Plus"): "amg35", ("Ford", "Fiesta 1.6 182 CV 5 porte ST"): "st",
        ("Renault", "Clio 5p 1.6 RS Trophy 220cv edc"): "rs", ("Hyundai", "I30 2.0 t-gdi N Performance"): "n",
        ("Porsche", "911 4.0 GT3"): "gt", ("Porsche", "911 GT3 RS"): "gt_rs", ("Toyota", "Yaris GR 1.6 Circuit"): "gr",
        ("MINI", "Mini 2.0 John Cooper Works"): "jcw", ("Fiat", "500 Abarth 1.4 Turismo"): "abarth",
    }
    for (m, t), atteso in casi.items():
        assert VT.variante_sportiva(m, "", t) == atteso, (m, t, VT.variante_sportiva(m, "", t))
    # allestimenti sportivi (solo estetica): versione NORMALE
    normali = [("Volkswagen", "Golf 1.6 TDI R-Line"), ("Ford", "Fiesta 1.0 ST-Line"), ("Renault", "Clio 1.0 RS Line"),
               ("Audi", "A3 SPB 1.5 TFSI S tronic S line"), ("Hyundai", "i20 1.0 N Line"), ("Toyota", "Yaris 1.5 GR Sport"),
               ("BMW", "320d M Sport"), ("BMW", "X3 M X3 G01 2021 xdrive20i mhev"), ("Mercedes-Benz", "A 180 d AMG Line"),
               ("Mercedes-Benz", "A 200 Premium AMG"), ("MINI", "Mini 3p 1.5 C JCW auto"), ("Volkswagen", "Golf 1.0 TSI Life")]
    for m, t in normali:
        assert VT.variante_sportiva(m, "", t) == "normale", (m, t, VT.variante_sportiva(m, "", t))
    assert VT.variante_sportiva("Dacia", "", "Sandero 1.0 Streetway") is None     # marca senza elenco: non si dice


def test_sigle_di_estetica_non_sono_sportive():
    """17/11 (giudice): accessori, allestimenti e parole che contengono la sigla NON sono la variante sportiva."""
    normali = [
        ("Volkswagen", "Golf 2.0 TDI R.Line"), ("Volkswagen", "Golf 2.0 TDI R/Line"), ("Volkswagen", "Golf 1.5 TSI R-Line"),
        ("Volkswagen", "Golf 1.6 TDI cerchi R 17"), ("Volkswagen", "Golf 1.6 TDI R17"), ("Volkswagen", "Golf 2.0 TDI Highline - R"),
        ("Volkswagen", 'Polo 1.0 TSI Style "R" pack'), ("Audi", "A3 SPB 1.6 TDI RS3 look"), ("Audi", "A5 2.0 TDI S5 look"),
        ("Audi", "A4 2.0 TDI s4 cerchi"), ("Audi", "A3 1.6 TDI S-line edition RS"), ("Ford", "Focus 2.0 TDCi St. Wagon"),
        ("Ford", "Focus 1.6 TDCi St.Wagon"), ("Ford", "Fiesta 1.0 ST.Line"), ("Ford", "Fiesta 1.0 ST-Line"),
        ("Renault", "Clio 1.2 Trophy"), ("Hyundai", "i30 1.6 CRDi n°1 proprietario"), ("Hyundai", "i20 1.2 n. 2 proprietari"),
        ("Hyundai", "i20 1.2 n 2 proprietari"), ("Mercedes-Benz", "C 220 d AMG 35 anni"),
        ("Mercedes-Benz", "A 180 AMG tagliando 63"), ("Mercedes-Benz", "A 200 kit AMG 45"), ("Fiat", "500 1.2 kit Abarth"),
        ("Seat", "Leon 1.5 TSI Cupra look"), ("Subaru", "Impreza 2.0 STI look"), ("Skoda", "Octavia 2.0 TDI RS look"),
        ("Skoda", "Octavia 2.0 TDI RS232"), ("Ford", "Focus 1.0 RS232"), ("BMW", "320d M3 look"),
    ]
    for m, t in normali:
        assert VT.variante_sportiva(m, "", t) == "normale", (m, t, VT.variante_sportiva(m, "", t))
    vere = {("Volkswagen", "Golf R 2.0 TSI 4motion"): "r", ("Volkswagen", "Golf 5p 2.0 tsi R 4motion"): "r",
            ("Audi", "TT RS Coupé"): "rs", ("Audi", "A3 RS3 Sportback 2.5"): "rs", ("Ford", "Focus ST 2.0 EcoBoost"): "st",
            ("Ford", "Focus ST Wagon 2.0"): "st", ("Renault", "Clio R.S. Trophy 1.6"): "rs",
            ("Hyundai", "i30 N 2.0 T-GDI"): "n", ("Mercedes-Benz", "C 63 AMG S"): "amg63",
            ("Mercedes-Benz", "AMG GT 63 S 4Matic+"): "amg63", ("Mercedes-Benz", "CLA 45 AMG"): "amg45",
            ("Skoda", "Octavia RS 2.0 TDI"): "rs", ("Seat", "Leon Cupra 290"): "cupra"}
    for (m, t), atteso in vere.items():
        assert VT.variante_sportiva(m, "", t) == atteso, (m, t, VT.variante_sportiva(m, "", t))


def test_cambio_dal_testo():
    for t in ("Golf 2.0 TSI DSG GTI Black", "A3 S3 2.0 TFSI quattro S tronic", "Giulia 2.2 JTDM 180CV Super AT8",
              "Clio 5p 1.6 RS Trophy 220cv edc", "Yaris 5p 1.3 dual S m-mt", "Panda 1.2 Dualogic",
              "Classe A 180 cambio automatico", "C3 1.2 cambio aut.", "C4 1.5 BlueHDi EAT8", "Focus 2.0 Powershift",
              "Serie 1 118d Steptronic", "Juke 1.6 Xtronic", "Kuga 2.0 TDCi DCT"):
        assert VT.cambio_da_testo(t) == "automatico", t
    assert VT.cambio_da_testo("Panda 1.2 cambio manuale") == "manuale"
    assert VT.cambio_da_testo("Golf 1.6 TDI 6 marce") == "manuale"
    for t in ("Golf 1.6 TDI Comfortline", "Mini Countryman 1.5 Northwood", "Fiat 500 1.2 Pop", ""):
        assert VT.cambio_da_testo(t) is None, t
    # 17/11 (giudice): "automatico" generico e "at" isolato NON dicono il cambio
    for t in ("Golf 1.6 TDI clima automatico", "Golf 1.6 TDI climatizzatore automatico",
              "Passat 2.0 TDI freno di stazionamento automatico", "Polo 1.0 fari automatici", "Polo 1.0 luce automatica",
              "Clio 1.5 dCi start&stop automatico", "Classe A 180 automatica", "Fiesta 1.2 at home", "Punto 1.3 tagliando at 100000",
              "Panda 1.2 manuale d'uso e libretto"):
        assert VT.cambio_da_testo(t) is None, t


def test_carrozzerie_nuove(monkeypatch):
    # di default SPENTE: come c9f2436 ("Golf Sports Van" resta commerciale come prima, allroad resta sw/berlina)
    V = _vecchio()
    for v in ("Golf VII 2016 Sportsvan 1.6 tdi", "Golf Sports Van 1.6 TDI", "A4 allroad 2.0 TDI quattro",
              "A6 Avant allroad 3.0 TDI", "Classe E 220 d All-Terrain", "V60 Cross Country D4", "Golf Plus 1.4"):
        assert P.carrozzeria({"version_raw": v}) == V.carrozzeria({"version_raw": v}), v
    monkeypatch.setattr(P, "CARROZZERIE_NUOVE", True)
    assert P.carrozzeria({"version_raw": "Golf VII 2016 Sportsvan 1.6 tdi"}) == "monovolume"
    assert P.carrozzeria({"version_raw": "Golf Sports Van 1.6 TDI"}) == "monovolume"       # non commerciale (\bvan\b)
    assert P.carrozzeria({"version_raw": "Golf Plus 1.4 TSI"}) == "monovolume"
    assert P.carrozzeria({"version_raw": "A4 allroad 2.0 TDI quattro"}) == "rialzata"
    assert P.carrozzeria({"version_raw": "A6 Avant allroad 3.0 TDI"}) == "rialzata"        # rialzata prima di sw
    assert P.carrozzeria({"version_raw": "Classe E 220 d All-Terrain"}) == "rialzata"
    assert P.carrozzeria({"version_raw": "A4 Avant 2.0 TDI"}) == "sw"
    assert P.carrozzeria({"version_raw": "Panda Van 1.3 Multijet"}) == "commerciale"
    assert P.carrozzeria({"version_raw": "Golf 1.6 TDI"}) is None
    monkeypatch.setattr(P, "GEMELLI_SCHEDA", False)                                         # bandiera generale spenta
    assert P.carrozzeria({"version_raw": "A4 allroad 2.0 TDI quattro"}) == V.carrozzeria({"version_raw": "A4 allroad 2.0 TDI quattro"})


# ------------------------------------------------------------------ non regressione: colonne complete = come prima

def test_colonne_complete_risultato_identico():
    """Mercati casuali con kW e cambio nelle colonne di TUTTE le auto (anche con GTI, 2.0/1.6, DSG, Sportsvan, allroad,
    "clima automatico" scritti nel testo): stesse gemelle, stessa M, stessa categoria del codice VERO di c9f2436."""
    rnd = random.Random(17)
    versioni = ["1.6 TDI Comfortline", "2.0 TDI DSG GTD", "GTI Performance 2.0 245 CV", "1.0 TSI Life", "2.0 TDI Highline",
                "1.5 TSI ACT Style DSG", "R 2.0 TSI 4motion", "1.4 TSI Sport", "Sportsvan 1.6 TDI", "Sports Van 1.6 TDI",
                "Golf Plus 1.6", "Variant 2.0 TDI", "Alltrack allroad 2.0 TDI", "1.6 TDI clima automatico",
                "1.6 TDI R-Line cerchi R 17", "2.0 TDI 150 CV", ""]
    kws = [85, 110, 135, 180, 81, 96, 221]
    diversi = []
    for prova in range(300):
        kw_t = rnd.choice(kws)
        t = car(1, rnd.randint(8000, 20000), rnd.choice(versioni), kw=kw_t, cambio=rnd.choice(["manuale", "automatico"]),
                anno=rnd.randint(2014, 2021), km=rnd.randint(40000, 140000), fuel="diesel")
        mercato = [car(100 + j, rnd.randint(7000, 26000), rnd.choice(versioni),
                       kw=rnd.choice([kw_t, kw_t, kw_t + 3, rnd.choice(kws)]), cambio=rnd.choice(["manuale", "automatico"]),
                       anno=t["year"] + rnd.randint(-4, 4), km=max(5000, t["mileage_km"] + rnd.randint(-80000, 80000)),
                       fuel="diesel")
                   for j in range(rnd.randint(3, 30))]
        v0, v1 = _con_e_senza(t, mercato)
        if (v0.get("M"), v0.get("categoria"), v0.get("n"), _ids(v0)) != (v1.get("M"), v1.get("categoria"), v1.get("n"), _ids(v1)):
            diversi.append((prova, t["version_raw"], v0.get("M"), v1.get("M")))
    assert not diversi, diversi[:10]


def test_colonne_complete_selezione_identica_sigle_nel_testo():
    """Stessa potenza nelle colonne: la sigla nel testo non conta (decide la potenza, come prima)."""
    t = car(1, 15000, "Golf 2.0 TSI 180 kW", kw=180, cambio="automatico")
    mercato = [car(10 + j, 21000 + 300 * j, "GTI Performance 2.0" if j % 2 else "2.0 TSI DSG", kw=180,
                   cambio="automatico", km=80000 + 2000 * j) for j in range(8)]
    v0, v1 = _con_e_senza(t, mercato)
    assert v0["M"] == v1["M"] and v0["n"] == v1["n"] == 8


# ------------------------------------------------------------------ precisione: casi che prima davano una M sbagliata

def test_auto_base_non_confrontata_con_gti_senza_kw():
    """Golf 1.6 TDI 85 kW (colonna, allestimento non scritto): prima le GTD di Subito senza kW e senza cambio passavano
    dal livello 2 e alzavano M (con l'allestimento scritto le toglieva già pricing/allestimento.py)."""
    t = car(1, 9000, "1.6 TDI 5p", kw=85, fuel="diesel", anno=2016, km=120000)
    normali = [car(10 + j, 10000 + 150 * j, "1.6 TDI 5 porte", kw=85, fuel="diesel", anno=2016, km=110000 + 3000 * j)
               for j in range(4)]
    top = [car(30 + j, 17500 + 300 * j, "2.0 TDI DSG GTD" if j % 2 else "Golf GTD 2.0 tdi", kw=None, cambio=None,
               fuel="diesel", anno=2016, km=115000 + 2000 * j) for j in range(5)]
    v0, v1 = _con_e_senza(t, normali + top)
    assert set(_ids(v1)) <= {10, 11, 12, 13}, _ids(v1)
    assert v1["M"] < v0["M"]                              # M più bassa: niente margine gonfiato dalle GTD
    # anche al livello 2 (cambio e potenza "non indicati" ammessi): la GTD resta fuori per la sigla o per il DSG scritto
    assert P._equivalente(t, top[0], CFG, 2).startswith("versione sportiva diversa (GTD")
    assert P._equivalente(t, top[1], CFG, 2) == "cambio diverso"


def test_versione_top_non_confrontata_con_le_base():
    """Audi S3 senza kW nella colonna: le A3 1.6 TDI/1.5 TFSI non sono gemelle (prima M troppo bassa)."""
    t = car(1, 23500, "S3 Sportback 2.0 tfsi quattro", kw=None, cambio="automatico", make="audi", model="a3",
            anno=2017, km=100000)
    base = [car(10 + j, 15500 + 200 * j, "Sportback 1.6 TDI Business", kw=85, cambio="automatico", make="audi",
                model="a3", anno=2017, km=95000 + 3000 * j, version_raw2=None) for j in range(6)]
    s3 = [car(30 + j, 27000 + 400 * j, "S3 SPB 2.0 TFSI quattro S tronic", kw=228, cambio="automatico", make="audi",
              model="a3", anno=2017, km=90000 + 4000 * j) for j in range(5)]
    sel = P.seleziona(t, base + s3, CFG, NOW)
    assert {c["id"] for c in sel["validi"]} == {30, 31, 32, 33, 34}


def test_kw_scritti_nel_testo_usati_per_la_potenza():
    """Auto senza kW nella colonna ma con "150 CV" nella versione: le gemelle si scelgono per potenza."""
    t = car(1, 12000, "Tiguan 2.0 TDI 150 CV Business", kw=None, model="tiguan", fuel="diesel", cambio="manuale")
    giuste = [car(10 + j, 15000 + 200 * j, "2.0 TDI 150cv Sport", kw=110, model="tiguan", fuel="diesel",
                  km=85000 + 2000 * j) for j in range(5)]
    potenti = [car(30 + j, 22000 + 200 * j, "2.0 TDI 240 CV 4motion", kw=176, model="tiguan", fuel="diesel",
                   km=85000 + 2000 * j) for j in range(4)]
    v0, v1 = _con_e_senza(t, giuste + potenti)
    assert set(_ids(v1)) <= {10, 11, 12, 13, 14} and v1["n"] == 5
    assert v1["M"] <= v0["M"]


def test_dsg_nel_testo_non_mescolato_col_manuale():
    t = car(1, 11000, "1.6 TDI Highline", kw=85, cambio="manuale", fuel="diesel")
    manuali = [car(10 + j, 12000 + 150 * j, "1.6 TDI Comfortline", kw=85, cambio="manuale", fuel="diesel",
                   km=85000 + 2000 * j) for j in range(5)]
    dsg = [car(30 + j, 14500 + 150 * j, "1.6 TDI DSG Highline", kw=85, cambio=None, fuel="diesel",
               km=85000 + 2000 * j) for j in range(4)]
    sel = P.seleziona(t, manuali + dsg, CFG, NOW)
    assert {c["id"] for c in sel["validi"]} == {10, 11, 12, 13, 14}
    assert sel["motivi_non_equivalenti"].get("cambio diverso") == 4


def test_cilindrata_diversa_senza_kw():
    t = car(1, 9000, "1.6 TDI Comfortline", kw=85, fuel="diesel")
    giuste = [car(10 + j, 10000 + 150 * j, "1.6 TDI Trendline", kw=85, fuel="diesel", km=85000 + 2000 * j)
              for j in range(5)]
    duelitri = [car(30 + j, 13500 + 150 * j, "2.0 TDI Highline", kw=None, fuel="diesel", km=85000 + 2000 * j)
                for j in range(4)]
    sel = P.seleziona(t, giuste + duelitri, CFG, NOW)
    assert {c["id"] for c in sel["validi"]} == {10, 11, 12, 13, 14}


def test_sportsvan_non_e_una_golf(monkeypatch):
    monkeypatch.setattr(P, "CARROZZERIE_NUOVE", True)                # di default spenta: qui si prova la classe
    t = car(1, 7000, "Golf VII 2016 Sportsvan 1.6 tdi", kw=81, fuel="diesel", anno=2016, km=140000)
    golf = [car(10 + j, 10500 + 150 * j, "1.6 TDI Business", kw=81, fuel="diesel", anno=2016, km=130000 + 2000 * j)
            for j in range(6)]
    sv = [car(30 + j, 8800 + 150 * j, "Sportsvan 1.6 TDI Comfortline", kw=81, fuel="diesel", anno=2016,
              km=130000 + 2000 * j) for j in range(5)]
    sel = P.seleziona(t, golf + sv, CFG, NOW)
    assert {c["id"] for c in sel["validi"]} == {30, 31, 32, 33, 34}


def test_testi_veri_del_dump_nessuna_sigla_sugli_allestimenti():
    """Sulle 804 auto vere del 16/11: nessun allestimento estetico (R-Line, S line, ST-Line, AMG Line, M Sport, N Line,
    GR Sport, RS Line) viene preso per una versione sportiva, e la funzione non fallisce mai."""
    with open(os.path.join(QUI, "dati", "dump_candidati_16_11.json"), encoding="utf-8") as f:
        casi = json.load(f)["casi"]
    estetici = ("r-line", "r line", "s line", "s-line", "st-line", "st line", "amg line", "m sport", "msport", "n line",
                "n-line", "gr sport", "rs line", "gt line")
    sportive = 0
    for c in casi:
        testo = c["auto"]
        marca = testo.split()[0]
        v = VT.variante_sportiva(marca, "", testo)
        VT.cambio_da_testo(testo)
        if v not in (None, "normale"):
            sportive += 1
            assert not any(e in testo.lower() for e in estetici) or any(
                s in testo.lower() for s in ("gti", "gtd", " rs", " s3", "amg 4", " 45", " 63")), testo
    assert sportive < 0.1 * len(casi)


# ------------------------------------------------------------------ casi del giudice: M di auto NORMALI non sale

def test_golf_clima_automatico_non_e_automatica():
    """17/11 (giudice): Golf 1.6 TDI "clima automatico" (cambio non in colonna) era letta automatica e tolta dalle
    gemelle delle manuali (M 12.975 → 14.875). Ora il testo non dice il cambio: stesse gemelle e stessa M di prima."""
    t = car(1, 11000, "Golf 1.6 TDI Business clima automatico", kw=85, cambio="manuale", fuel="diesel", anno=2017,
            km=110000)
    mercato = [car(10 + j, 11500 + 350 * j, "1.6 TDI clima automatico" if j % 2 else "1.6 TDI Comfortline", kw=85,
                   cambio=None if j % 2 else "manuale", fuel="diesel", anno=2017, km=100000 + 4000 * j)
               for j in range(10)]
    v0, v1 = _con_e_senza(t, mercato)
    assert v1["M"] == v0["M"] and _ids(v1) == _ids(v0)
    assert P._cambio({"gearbox": None, "version_raw": "1.6 TDI clima automatico"}) is None


def test_focus_station_wagon_non_e_una_st():
    """17/11 (giudice): "Focus 2.0 TDCi St. Wagon" era presa per una Focus ST (M 13.854 → 21.750)."""
    t = car(1, 9000, "Focus 2.0 TDCi St. Wagon Titanium", kw=None, cambio="manuale", make="ford", model="focus",
            fuel="diesel", anno=2016, km=120000)
    assert VT.variante_sportiva("Ford", "focus", t["version_raw"]) == "normale"
    sw = [car(10 + j, 9500 + 300 * j, "Focus 2.0 TDCi SW Business" if j % 2 else "Focus St.Wagon 2.0 TDCi", kw=110,
              cambio="manuale", make="ford", model="focus", fuel="diesel", anno=2016, km=110000 + 4000 * j)
          for j in range(8)]
    v0, v1 = _con_e_senza(t, sw)
    assert v1["M"] <= v0["M"] and v1.get("n") == v0.get("n")


# ------------------------------------------------------------------ prudenza: campione realistico contro c9f2436

# (marca, modello, alimentazione, [(versione scritta, variante vera, litri veri, kW veri, prezzo base 2018/80.000 km)])
_CATALOGO = [
    ("volkswagen", "golf", "diesel", [("1.6 TDI {all}", "normale", 1.6, 85, 13500), ("2.0 TDI {all}", "normale", 2.0, 110, 15500),
                                      ("2.0 TDI GTD", "gtd", 2.0, 135, 21000), ("R-Line 1.6 TDI {all}", "normale", 1.6, 85, 14000),
                                      ("1.6 TDI cerchi R 17 {all}", "normale", 1.6, 85, 13600)]),
    ("volkswagen", "golf", "benzina", [("1.0 TSI {all}", "normale", 1.0, 81, 12500), ("1.5 TSI {all}", "normale", 1.5, 110, 15000),
                                       ("GTI Performance 2.0", "gti", 2.0, 180, 25000), ("R 2.0 TSI 4motion", "r", 2.0, 221, 30000),
                                       ('1.0 TSI Style "R" pack', "normale", 1.0, 81, 12800)]),
    ("audi", "a3", "diesel", [("SPB 1.6 TDI {all}", "normale", 1.6, 85, 16000), ("SPB 2.0 TDI {all}", "normale", 2.0, 110, 18500),
                              ("SPB 1.6 TDI RS3 look", "normale", 1.6, 85, 16300), ("1.6 TDI S-line edition RS", "normale", 1.6, 85, 16500)]),
    ("audi", "a3", "benzina", [("SPB 1.5 TFSI {all}", "normale", 1.5, 110, 18000), ("S3 SPB 2.0 TFSI quattro", "s", 2.0, 228, 29000),
                               ("RS3 Sportback 2.5 TFSI", "rs", 2.5, 294, 42000)]),
    ("ford", "focus", "diesel", [("1.5 TDCi {all}", "normale", 1.5, 88, 10500), ("2.0 TDCi St. Wagon {all}", "normale", 2.0, 110, 11500),
                                 ("St.Wagon 1.5 TDCi {all}", "normale", 1.5, 88, 10800), ("ST-Line 1.5 TDCi", "normale", 1.5, 88, 11800),
                                 ("ST 2.0 TDCi", "st", 2.0, 140, 17000)]),
    ("mercedes-benz", "classe a", "diesel", [("A 180 d {all}", "normale", 1.5, 80, 17000), ("A 180 d AMG Line", "normale", 1.5, 80, 18500),
                                             ("A 200 d AMG 35 anni", "normale", 2.1, 100, 19000)]),
    ("mercedes-benz", "classe a", "benzina", [("A 200 {all}", "normale", 1.3, 120, 19500), ("A 45 AMG 4Matic", "amg45", 2.0, 280, 38000),
                                              ("A 180 AMG tagliando 63", "normale", 1.3, 100, 17500)]),
    ("renault", "clio", "benzina", [("1.2 Trophy", "normale", 1.2, 55, 8000), ("0.9 TCe {all}", "normale", 0.9, 66, 8500),
                                    ("R.S. 1.6 Trophy 220", "rs", 1.6, 162, 17000)]),
    ("hyundai", "i30", "benzina", [("1.4 T-GDI {all} n°1 proprietario", "normale", 1.4, 103, 13000),
                                   ("1.0 T-GDI n. 2 proprietari", "normale", 1.0, 88, 11500), ("N 2.0 T-GDI Performance", "n", 2.0, 202, 25000)]),
    ("fiat", "500", "benzina", [("1.2 Lounge", "normale", 1.2, 51, 7500), ("1.2 kit Abarth", "normale", 1.2, 51, 7800),
                                ("Abarth 1.4 T-Jet 595", "abarth", 1.4, 107, 14000)]),
]
_ALL = ["Business", "Comfortline", "Highline", "Executive", "Titanium", "Sport", "", "clima automatico",
        "climatizzatore automatico", "fari automatici", "start&stop automatico", "freno di stazionamento automatico",
        "tagliando at 100000"]


def _campione(rnd, n_mercati=60):
    """Mercati sintetici ma realistici: testi come quelli di Subito/AutoScout, colonne kW/cambio a volte vuote (Subito),
    sigla del cambio scritta solo a volte. La versione VERA (variante, litri, kW, cambio) resta nota per il controllo."""
    mercati = []
    for k in range(n_mercati):
        marca, modello, fuel, versioni = _CATALOGO[k % len(_CATALOGO)]
        auto = []
        for j in range(rnd.randint(12, 30)):
            testo, var, litri, kw, base = rnd.choice(versioni)
            testo = testo.replace("{all}", rnd.choice(_ALL)).strip()
            aut = rnd.random() < (0.7 if var != "normale" else 0.3)
            if aut and rnd.random() < 0.6:
                testo += " " + rnd.choice(["DSG", "S tronic", "cambio automatico", "Powershift", "edc", "7G-DCT"])
            anno = rnd.randint(2015, 2021)
            km = rnd.randint(20000, 150000)
            prezzo = base * (1.08 if aut else 1.0) * (1 + 0.07 * (anno - 2018)) * (1 - 0.0012 * (km - 80000) / 1000)
            prezzo = int(round(prezzo * rnd.uniform(0.9, 1.1), -1))
            subito = rnd.random() < 0.5
            c = car(1000 * k + j, max(1500, prezzo), testo, kw=None if subito and rnd.random() < 0.7 else kw,
                    cambio=None if subito and rnd.random() < 0.6 else ("automatico" if aut else "manuale"),
                    anno=anno, km=km, make=marca, model=modello, fuel=fuel)
            c["_vero"] = (var, litri, kw, "automatico" if aut else "manuale")
            auto.append(c)
        mercati.append(auto)
    return mercati


def _davvero_diversa(t, c):
    vt, vc = t["_vero"], c["_vero"]
    return vt[0] != vc[0] or abs(vt[1] - vc[1]) > 0.15 + 1e-9 or abs(vt[2] - vc[2]) > 0.10 * vt[2] or vt[3] != vc[3]


def _scheda_reg(mod, t, c, cfg):
    """Motivo per cui il modello di prezzo del gruppo (regressione) NON usa il confronto per cambio/potenza/versione,
    con le funzioni del modulo dato (c9f2436 non ha _cambio/_kw/versioni_diverse: colonne sole), oppure None."""
    cambio = getattr(mod, "_cambio", lambda x: mod._norm_gear(x.get("gearbox")))
    kw = getattr(mod, "_kw", lambda x: x.get("power_kw"))
    tg, cg = cambio(t), cambio(c)
    if tg and cg and tg != cg:
        return "cambio diverso"
    tp, cp = kw(t), kw(c)
    if tp and cp and abs(cp - tp) > tp * cfg.reg_potenza:
        return "potenza"
    vd = getattr(mod, "versioni_diverse", lambda *a: None)(t, c, cfg.reg_potenza)
    return vd


def _cambi_diretti(V, t, altri):
    """Gemelle su cui il codice nuovo decide DIVERSAMENTE da c9f2436 a parità di livello (1, 2, 3) o nel modello di
    prezzo del gruppo: [(confronto, motivo nuovo o None se ora ammesso, davvero diversa)]."""
    cfg0, out = V.PolicyConfig(), []
    for c in altri:
        for liv in (1, 2, 3):
            w0, w1 = V._equivalente(t, c, cfg0, liv), P._equivalente(t, c, CFG, liv)
            if bool(w0) != bool(w1):
                out.append((c, w1, _davvero_diversa(t, c)))
                break
        else:
            w0, w1 = _scheda_reg(V, t, c, cfg0), _scheda_reg(P, t, c, CFG)
            if bool(w0) != bool(w1):
                out.append((c, w1 and "modello: " + w1, _davvero_diversa(t, c)))
    return out


def test_campione_realistico_m_sale_solo_per_gemelle_davvero_diverse():
    """Su 540 auto (60 mercati sintetici realistici, colonne kW/cambio spesso vuote come su Subito): quante vedono M
    salire/scendere rispetto a c9f2436 e perché.
      - ogni gemella che il codice nuovo TOGLIE (stesso livello) dev'essere davvero diversa (variante, cilindrata,
        potenza o cambio veri) e ogni gemella che AMMETTE in più davvero uguale: nessuna decisione sbagliata;
      - ogni auto con M più alta ha almeno una gemella davvero diversa tolta (il resto sono effetti a catena di quella
        scelta: livello, mediana, allestimento); lo stesso per un'auto che prima non aveva M e ora sì."""
    V = _vecchio()
    rnd = random.Random(1711)
    su, giu, uguali, nuove, sbagliate, motivi_su = [], [], 0, 0, [], {}
    for mercato in _campione(rnd):
        for t in mercato[:9]:
            altri = [c for c in mercato if c["id"] != t["id"]]
            v0, v1 = _con_e_senza(t, altri)
            diretti = _cambi_diretti(V, t, altri)
            sbagliate += [(t["version_raw"], t["gearbox"], c["version_raw"], c["gearbox"], w) for c, w, diversa in diretti
                          if bool(w) != diversa]
            m0, m1 = v0.get("M"), v1.get("M")
            if m1 is None or m0 == m1:
                uguali += 1                                  # uguale, oppure senza M (prudente: l'auto non esce)
                continue
            if m0 is None or m1 > m0:                        # M più alta o M nuova: va spiegata
                nuove += m0 is None
                tolte_vere = [w for c, w, diversa in diretti if w and diversa]
                assert tolte_vere, ("M sale senza gemelle davvero diverse tolte", t["version_raw"], m0, m1)
                for w in tolte_vere:
                    k = w.split(" (")[0]
                    motivi_su[k] = motivi_su.get(k, 0) + 1
                su.append((t["version_raw"], m0, m1))
            else:
                giu.append((t["version_raw"], m0, m1))
    print(f"CAMPIONE 540 auto contro c9f2436: M su {len(su)} (di cui {nuove} prima senza M), giù {len(giu)}, "
          f"uguale/senza M {uguali}; "
          f"decisioni sbagliate {len(sbagliate)}; motivi delle gemelle tolte quando M sale: {motivi_su}")
    assert not sbagliate, sbagliate[:10]
    assert su and giu                                # il campione contiene davvero gemelle sbagliate in entrambi i sensi
