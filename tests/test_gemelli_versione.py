"""17/11 (notte, "M più preciso"): scelta delle gemelle per versione, potenza, cambio e carrozzeria anche quando le
colonne power_kw/gearbox mancano, con la scheda letta dal testo già salvato (pricing/versione_tecnica.py).

Due famiglie di controlli:
  - NON REGRESSIONE: con power_kw e gearbox scritti nelle colonne di tutte le auto la scelta delle gemelle e M sono
    identici a prima (GEMELLI_SCHEDA=False = codice di prima), anche con sigle sportive o cilindrate nel testo;
  - PRECISIONE: un'auto base non prende più come gemelle le versioni top senza kW (GTI, S3, A 45 AMG…) e viceversa,
    il DSG scritto nel testo non si mescola col manuale, Sportsvan e allroad non sono berline.
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


def _con_e_senza(t, mercato):
    """(prima, adesso): valutazione col codice di prima (GEMELLI_SCHEDA=False) e con quello nuovo."""
    prima = P.GEMELLI_SCHEDA
    try:
        P.GEMELLI_SCHEDA = False
        P._scheda_testo.cache_clear()
        v0 = P.valuta(dict(t), [dict(c) for c in mercato], soglia=2000, cfg=CFG, now=NOW)
        P.GEMELLI_SCHEDA = True
        v1 = P.valuta(dict(t), [dict(c) for c in mercato], soglia=2000, cfg=CFG, now=NOW)
    finally:
        P.GEMELLI_SCHEDA = prima
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


def test_cambio_dal_testo():
    for t in ("Golf 2.0 TSI DSG GTI Black", "A3 S3 2.0 TFSI quattro S tronic", "Giulia 2.2 JTDM 180CV Super AT8",
              "Clio 5p 1.6 RS Trophy 220cv edc", "Yaris 5p 1.3 dual S m-mt", "Panda 1.2 Dualogic", "Classe A 180 automatica"):
        assert VT.cambio_da_testo(t) == "automatico", t
    assert VT.cambio_da_testo("Panda 1.2 cambio manuale") == "manuale"
    for t in ("Golf 1.6 TDI Comfortline", "Mini Countryman 1.5 Northwood", "Fiat 500 1.2 Pop", ""):
        assert VT.cambio_da_testo(t) is None, t


def test_carrozzerie_nuove():
    assert P.carrozzeria({"version_raw": "Golf VII 2016 Sportsvan 1.6 tdi"}) == "monovolume"
    assert P.carrozzeria({"version_raw": "A4 allroad 2.0 TDI quattro"}) == "rialzata"
    assert P.carrozzeria({"version_raw": "Classe E 220 d All-Terrain"}) == "rialzata"
    assert P.carrozzeria({"version_raw": "A4 Avant 2.0 TDI"}) == "sw"
    assert P.carrozzeria({"version_raw": "Golf 1.6 TDI"}) is None


# ------------------------------------------------------------------ non regressione: colonne complete = come prima

def test_colonne_complete_risultato_identico():
    """Mercati casuali con kW e cambio nelle colonne di TUTTE le auto (anche con GTI, 2.0/1.6 e DSG scritti nel testo):
    stesse gemelle, stessa M, stessa categoria del codice di prima."""
    rnd = random.Random(17)
    versioni = ["1.6 TDI Comfortline", "2.0 TDI DSG GTD", "GTI Performance 2.0 245 CV", "1.0 TSI Life", "2.0 TDI Highline",
                "1.5 TSI ACT Style DSG", "R 2.0 TSI 4motion", "1.4 TSI Sport", "Sportsvan 1.6 TDI", ""]
    kws = [85, 110, 135, 180, 81, 96, 221]
    for prova in range(40):
        kw_t = rnd.choice(kws)
        t = car(1, rnd.randint(8000, 20000), rnd.choice(versioni), kw=kw_t, cambio=rnd.choice(["manuale", "automatico"]),
                anno=rnd.randint(2014, 2021), km=rnd.randint(40000, 140000), fuel="diesel")
        mercato = [car(100 + j, rnd.randint(7000, 26000), rnd.choice(versioni),
                       kw=rnd.choice([kw_t, kw_t, kw_t + 3, rnd.choice(kws)]), cambio=rnd.choice(["manuale", "automatico"]),
                       anno=t["year"] + rnd.randint(-4, 4), km=max(5000, t["mileage_km"] + rnd.randint(-80000, 80000)),
                       fuel="diesel")
                   for j in range(rnd.randint(3, 30))]
        v0, v1 = _con_e_senza(t, mercato)
        assert (v0.get("M"), v0.get("categoria"), v0.get("n")) == (v1.get("M"), v1.get("categoria"), v1.get("n")), prova
        assert _ids(v0) == _ids(v1), prova


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


def test_sportsvan_non_e_una_golf():
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
