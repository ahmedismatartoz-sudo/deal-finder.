"""22/11 (decisione del proprietario, fase B2): la stima di ripiego (pricing/valore_base.stima_ripiego) si ACCENDE per
le auto "dati insufficienti", ma SOLO nelle coppie (livello, fascia del valore grezzo) di regole.StimaRipiego.fasce:
unione 10-15k / 15-20k / >20k e largo 15-20k / >20k, sconto extra 0 ("non gonfiare i prezzi": la stima resta già
sotto il prezzo vero). Mai sotto 10.000 €, mai "largo" 10-15k, mai "largo4". Sempre col controllo AI, al massimo
"candidato"; margine oltre il 40% del valore = resta dati_insufficienti. Interruttori automatismi "stima_ripiego" e
"stima_ripiego_largo": spenti = come prima, senza deploy. Le auto col valore prudente non cambiano di un bit."""
from __future__ import annotations

import copy
import json
import os
import random
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder import automatismi                       # noqa: E402
from dealfinder import regole as RG                     # noqa: E402
from dealfinder.pricing import base_vendute as BV        # noqa: E402
from dealfinder.pricing import policy_v2 as P            # noqa: E402
from dealfinder.pricing import valore_base as VB         # noqa: E402
from dealfinder.web import scovo                         # noqa: E402
from dealfinder.web import sito_regole as SR             # noqa: E402

FASCE = (("unione", "10-15k", 0.0), ("unione", "15-20k", 0.0), ("unione", ">20k", 0.0),
         ("largo", "15-20k", 0.0), ("largo", ">20k", 0.0))
CAMPI_RIPIEGO = ("stima_ripiego", "ripiego_scartato")


def _c(i, prezzo, status="attivo", sel="commerciante", year=2018, km=60000, **kw):
    r = dict(id=i, source="subito", url=f"https://x/{i}", make="Fiat", model="Panda", fuel="benzina",
             gearbox="manuale", power_kw=Decimal("51"), year=year, mileage_km=km, price_eur=Decimal(str(int(prezzo))),
             price_flags=None, seller_type=sel, status=status, province="MI", danno="nessuno", problema=False,
             dichiarato=False, stage="", giorni_visto=Decimal("2.5"), version_raw=None, first_seen_at=None,
             disappeared_at=None, giorni_sparita=Decimal("10") if status == "scomparso" else None,
             giorni_online=Decimal("20"))
    r.update(kw)
    return VB.norm(r)


def _t(prezzo, **kw):
    t = dict(id=999, make="Fiat", model="Panda", fuel="benzina", gearbox="manuale", power_kw=Decimal("51"),
             year=2018, mileage_km=60000, price_eur=Decimal(str(int(prezzo))), price_flags=[], seller_type="privato",
             damage_class="nessuno", problem_search=False, stage_reason=None, targa_e_telefono=True, dichiarato=False)
    t.update(kw)
    return t


def _unione(centro, year=2018):
    """3 commercianti e 3 vendute: nessuna base arriva a 5 da sola, insieme sì (pochi_confronti:solo_unendo)."""
    D = [_c(i, centro * f, year=year) for i, f in ((1, 1.00), (2, 1.04), (3, 1.08))]
    V = [_c(10 + i, centro * f, status="scomparso", sel="privato", year=year) for i, f in ((1, 0.94), (2, 0.98), (3, 1.02))]
    return {"D": D, "V": V, "Dv": []}


def _largo(centro):
    """Confronti a 3 anni di distanza: fuori dai livelli di oggi (±1, ±2), dentro "largo" (±3, km ±60%)."""
    return _unione(centro, year=2015)


def _j(x):
    return json.dumps(x, sort_keys=True, default=str)


def _valuta(t, base):
    return VB.valuta(dict(t), copy.deepcopy(base), scovo.soglia(int(t["price_eur"])))


def _buona(base, quota=0.70):
    """Auto col prezzo al 70% del valore di ripiego della base (margine 30%: sopra la soglia, sotto il tetto del 40%)."""
    R = _valuta(_t(10 ** 6), base)["R"]
    return _t(int(R * quota))


def _pulisci(monkeypatch, **interruttori):
    monkeypatch.setattr(automatismi, "valori", lambda: {})
    for k in ("AUTOMATISMO_STIMA_RIPIEGO", "AUTOMATISMO_STIMA_RIPIEGO_LARGO"):
        monkeypatch.delenv(k, raising=False)
    for k, v in interruttori.items():
        monkeypatch.setenv("AUTOMATISMO_" + k.upper(), v)


# ------------------------------------------------------------------ regole

def test_regole_del_22_11_e_nessun_interruttore_d_ambiente():
    rp = RG.regole().stima_ripiego
    assert isinstance(rp, RG.StimaRipiego) and rp.fasce == FASCE
    assert (rp.percentile, rp.n_min_unione, rp.n_min_largo, rp.largo_anni, rp.largo_km, rp.margine_max) == \
           (0.40, 5, 6, 3, 0.60, 0.40)
    # mai sotto 10.000 €, mai largo 10-15k, mai largo4, sconto extra sempre 0 (la stima non si corregge)
    assert not [f for f in rp.fasce if f[1] in ("<5k", "5-10k") or f[0] not in VB.RIPIEGO_ATTIVABILI or f[2] != 0.0]
    assert ("largo", "10-15k") not in {(f[0], f[1]) for f in rp.fasce}
    # le fasce sono nell'impronta delle regole; gli interruttori no (né variabili d'ambiente né automatismi)
    assert RG.come_dict()["stima_ripiego"]["fasce"] == [list(f) for f in FASCE]
    assert not [k for k, v in RG.INTERRUTTORI.items() if "ripiego" in k.lower() and k != "POLICY_RIPIEGO_NORD"]
    assert not [v for v in RG.INTERRUTTORI.values() if v.startswith("stima_ripiego")]
    imp = RG.impronta(environ={})
    for env in ({"AUTOMATISMO_STIMA_RIPIEGO": "0"}, {"AUTOMATISMO_STIMA_RIPIEGO_LARGO": "0"}, {"STIMA_RIPIEGO": "0"}):
        assert RG.impronta(environ=env) == imp and RG.regole(env).stima_ripiego.fasce == FASCE


def test_regole_non_leggono_gli_automatismi(monkeypatch):
    def vietato(*a, **k):
        raise AssertionError("le regole non devono leggere gli automatismi")
    monkeypatch.setattr(automatismi, "attivo", vietato)
    monkeypatch.setattr(automatismi, "valori", vietato)
    monkeypatch.setattr(automatismi, "numero", vietato)
    RG._CACHE.clear()
    try:
        assert RG.regole().stima_ripiego.fasce == FASCE and len(RG.impronta()) == 12
    finally:
        RG._CACHE.clear()


def test_livelli_accesi_dalle_regole_e_dagli_interruttori(monkeypatch):
    _pulisci(monkeypatch)
    assert VB.livelli_accesi() == ("unione", "largo")
    monkeypatch.setenv("AUTOMATISMO_STIMA_RIPIEGO_LARGO", "0")
    assert VB.livelli_accesi() == ("unione",)
    monkeypatch.setenv("AUTOMATISMO_STIMA_RIPIEGO", "0")
    assert VB.livelli_accesi() == ()
    _pulisci(monkeypatch)
    monkeypatch.setattr(automatismi, "valori", lambda: {"stima_ripiego": False})      # rollback senza deploy
    assert VB.livelli_accesi() == ()
    monkeypatch.setattr(automatismi, "valori", lambda: {"stima_ripiego_largo": False})
    assert VB.livelli_accesi() == ("unione",)


# ------------------------------------------------------------------ (a) fasce accese

def test_a_unione_nelle_fasce_accese_candidato_con_controllo_ai(monkeypatch):
    _pulisci(monkeypatch)
    for centro, fascia in ((12500, "10-15k"), (17500, "15-20k"), (26000, ">20k")):
        base = _unione(centro)
        t = _buona(base)
        assert VB.stima_prudente(base["D"], base["V"], VB.norm(t), RG.regole().valore_base)["valore"] is None
        v = _valuta(t, base)
        rr = VB.stima_ripiego(base["D"], base["V"], VB.norm(t), RG.regole().valore_base, rp=RG.regole().stima_ripiego)
        assert rr["livello"] == "unione" and rr["fascia"] == fascia and rr["sconto_extra"] == 0.0
        assert v["categoria"] == "candidato" and v["serve_controllo_ai"] is True       # mai "opportunita"
        assert v["metodo"] == v["metodo_base"] == "base_vendute" and v["M_fonte"] == "ripiego"
        assert v["fonte_base"] == "ripiego:unione" and v["campione"] == "ripiego (confronti allargati)"
        assert v["stima_ripiego"] == {"livello": "unione", "n": 6, "fascia": fascia, "sconto_extra": 0.0,
                                      "da": "pochi_confronti:solo_unendo", "regola": VB.REGOLA_RIPIEGO}
        assert v["M"] == v["R"] == v["R_finale"] == int(round(rr["valore"])) and v["n"] == v["n_gate"] == 6
        assert v["margine"] == v["R"] - int(t["price_eur"]) >= v["soglia"] == scovo.soglia(int(t["price_eur"]))
        assert v["livello"] == 1 and len(v["confronti"]) == 6 and "motivo_dati" not in v
        assert sum(1 for c in v["confronti"] if c.get("venduta")) == 3
        assert any("stima di ripiego" in x for x in v["ipotesi"]) and any("ripiego" in x for x in v["motivi"])
        # il riassunto che legge il sito porta i campi che servono a sito_regole.r_valutata e al controllo AI
        ri = P.riassunto(v)
        assert ri["stima_ripiego"]["livello"] == "unione" and ri["serve_controllo_ai"] and ri["M_fonte"] == "ripiego"
        assert ri["categoria"] == "candidato" and ri["metodo"] == "base_vendute"


def test_a_largo_nelle_fasce_accese(monkeypatch):
    _pulisci(monkeypatch)
    for centro, fascia in ((13000, "15-20k"), (26000, ">20k")):
        base = _largo(centro)
        t = _buona(base)
        v = _valuta(t, base)
        assert v["categoria"] == "candidato" and v["serve_controllo_ai"] is True and v["M_fonte"] == "ripiego"
        assert v["fonte_base"] == "ripiego:largo" and v["livello"] == 3
        assert v["stima_ripiego"]["livello"] == "largo" and v["stima_ripiego"]["fascia"] == fascia
        assert v["stima_ripiego"]["n"] == 6 and v["stima_ripiego"]["da"] == "pochi_confronti:nessuno_simile"
        assert any("anno ±3 e km ±60%" in x for x in v["ipotesi"])


def test_a_margine_sotto_la_soglia_non_conveniente_mai_opportunita(monkeypatch):
    """SOGLIE_PV invariate: col ripiego il margine deve arrivare alla stessa soglia; e mai "opportunita"."""
    _pulisci(monkeypatch)
    base = _unione(17500)
    R = _valuta(_t(14000), base)["R"]
    v = _valuta(_t(R - 100, targa_e_telefono=False), base)
    assert v["categoria"] in ("non_conveniente", "candidato") and v["margine"] == 100 < v["soglia"]
    assert v["categoria"] != "candidato" or v.get("da_trattare")
    assert v["serve_controllo_ai"] is True and v["stima_ripiego"]["livello"] == "unione"
    rnd = random.Random(22)
    for _ in range(200):
        centro = rnd.choice((12000, 17000, 24000, 40000))
        v = _valuta(_t(centro * rnd.uniform(0.55, 1.05)), rnd.choice((_unione, _largo))(centro))
        assert v["categoria"] != "opportunita"
        if "stima_ripiego" in v:
            assert v["categoria"] in ("candidato", "non_conveniente") and v["serve_controllo_ai"] is True
            assert v["categoria"] == "non_conveniente" or v["margine"] >= v["soglia"] or v.get("da_trattare")


# ------------------------------------------------------------------ (b) fasce spente

def test_b_fasce_spente_restano_dati_insufficienti(monkeypatch):
    _pulisci(monkeypatch)
    vb = RG.regole().valore_base
    casi = ((_unione(3800), "unione", "<5k"), (_unione(8500), "unione", "5-10k"),
            (_largo(7000), "largo", "5-10k"), (_largo(9500), "largo", "10-15k"))
    for base, livello, fascia in casi:
        t = _t(float(base["D"][0]["price_eur"]) * 0.70)
        rr = VB.stima_ripiego(base["D"], base["V"], VB.norm(t), vb, rp=RG.regole().stima_ripiego)
        assert (rr["livello"], rr["fascia"]) == (livello, fascia) and rr["valore"] > 0      # la stima ci sarebbe
        v = _valuta(t, base)
        assert v["categoria"] == "dati_insufficienti" and v["motivo_dati"].startswith("pochi_confronti:")
        assert v.get("M") is None and "margine" not in v and not [k for k in CAMPI_RIPIEGO if k in v]
        monkeypatch.setenv("AUTOMATISMO_STIMA_RIPIEGO", "0")
        assert _j(_valuta(t, base)) == _j(v)                                   # identica a prima
        monkeypatch.delenv("AUTOMATISMO_STIMA_RIPIEGO")


def test_b_largo4_mai(monkeypatch):
    _pulisci(monkeypatch)
    base = _unione(30000, year=2014)                       # anno a 4 anni: solo "largo4" (misurato e basta)
    t = _t(20000)
    assert VB.stima_ripiego(base["D"], base["V"], VB.norm(t), RG.regole().valore_base, solo="largo4")["n"] == 6
    v = _valuta(t, base)
    assert v["categoria"] == "dati_insufficienti" and not [k for k in CAMPI_RIPIEGO if k in v]
    # nemmeno se qualcuno lo scrivesse nelle fasce: livelli_accesi e ripiego_ammesso lo ignorano
    rp = RG.con_campi(RG.regole(), {"stima_ripiego.fasce": FASCE + (("largo4", ">20k", 0.0),)}).stima_ripiego
    assert VB.livelli_accesi(rp) == ("unione", "largo")
    assert VB.ripiego_ammesso(base["D"], base["V"], VB.norm(t), RG.regole().valore_base, rp) is None


def test_b_la_prima_prova_con_abbastanza_confronti_decide(monkeypatch):
    """Unione sotto 10.000 € (spenta): non si passa al "largo" per cercare una coppia accesa."""
    _pulisci(monkeypatch)
    base = _unione(9000)
    base["V"] += [_c(50 + i, 30000, status="scomparso", sel="privato", year=2015) for i in range(8)]
    t = _t(6500)
    assert VB.ripiego_ammesso(base["D"], base["V"], VB.norm(t), RG.regole().valore_base,
                              RG.regole().stima_ripiego) is None
    assert _valuta(t, base)["categoria"] == "dati_insufficienti"


# ------------------------------------------------------------------ (c) interruttori

def test_c_interruttore_spento_identico_a_prima(monkeypatch):
    """stima_ripiego spento = le stesse valutazioni di regole senza fasce (com'era prima del 22/11)."""
    _pulisci(monkeypatch)
    senza_fasce = RG.con_campi(RG.regole(), {"stima_ripiego.fasce": ()})
    basi = [_unione(12500), _unione(17500), _unione(26000), _largo(9500), _largo(13000), _largo(26000)]
    casi = [(_buona(b) if i != 3 else _t(8000), b) for i, b in enumerate(basi)]
    accese = [_valuta(t, b) for t, b in casi]
    assert sum(1 for v in accese if "stima_ripiego" in v) == 5                # largo 10-15k no
    for acceso, valore in (("0", None), (None, {"stima_ripiego": False})):
        _pulisci(monkeypatch)
        if acceso is not None:
            monkeypatch.setenv("AUTOMATISMO_STIMA_RIPIEGO", acceso)
        else:
            monkeypatch.setattr(automatismi, "valori", lambda valore=valore: valore)
        for t, b in casi:
            v = _valuta(t, b)
            prima = VB.valuta(dict(t), copy.deepcopy(b), scovo.soglia(int(t["price_eur"])), rg=senza_fasce)
            assert _j(v) == _j(prima)
            assert v["categoria"] == "dati_insufficienti" and v["motivo_dati"].startswith("pochi_confronti:")
            assert not [k for k in CAMPI_RIPIEGO if k in v] and "serve_controllo_ai" not in v


def test_c_largo_spento_resta_solo_unione(monkeypatch):
    _pulisci(monkeypatch, stima_ripiego_largo="0")
    for centro in (17500, 30000):
        _pulisci(monkeypatch)
        t = _buona(_largo(centro))
        assert _valuta(t, _largo(centro))["stima_ripiego"]["livello"] == "largo"
        _pulisci(monkeypatch, stima_ripiego_largo="0")
        v = _valuta(t, _largo(centro))
        assert v["categoria"] == "dati_insufficienti" and not [k for k in CAMPI_RIPIEGO if k in v]
        u = _valuta(_buona(_unione(centro)), _unione(centro))
        assert u["categoria"] == "candidato" and u["stima_ripiego"]["livello"] == "unione"


def test_c_sul_sito_resta_solo_col_livello_acceso():
    """Rollback senza deploy: una valutazione di ripiego già salvata esce dal sito appena il suo livello è spento."""
    g = SR.Regole()
    pv = {"categoria": "candidato", "metodo": "base_vendute", "M": 15000, "R": 15000, "R_finale": 15000,
          "margine": 2500, "stima_ripiego": {"livello": "largo"}, "serve_controllo_ai": True}
    for livelli, atteso in ((("unione", "largo"), None), (("unione",), "dati_insufficienti"), ((), "dati_insufficienti")):
        it = {"id": 1, "prezzo": 12500, "pv": dict(pv), "_r": {"ripiego_livelli": livelli}}
        assert SR.r_valutata(it, g) == atteso
        assert bool(it.get("stima_ripiego")) == (atteso is None)


# ------------------------------------------------------------------ (d) mai gonfiare

def test_d_valore_mai_sopra_le_stime_deboli_ne_la_mediana_grezza(monkeypatch):
    import statistics
    _pulisci(monkeypatch)
    rnd = random.Random(2211)
    vb = RG.regole().valore_base
    viste = 0
    for _ in range(1500):
        centro = rnd.choice((9000, 12000, 14000, 17000, 19000, 24000, 30000, 45000))
        anno = rnd.choice((2014, 2016, 2019, 2022))
        D = [_c(i, centro * rnd.uniform(0.8, 1.3), year=anno + rnd.randint(-3, 3), km=rnd.randint(20000, 140000))
             for i in range(rnd.randint(0, 7))]
        V = [_c(100 + i, centro * rnd.uniform(0.7, 1.2), status="scomparso", sel=rnd.choice(("privato", "commerciante")),
                year=anno + rnd.randint(-3, 3), km=rnd.randint(20000, 140000)) for i in range(rnd.randint(0, 7))]
        t = _t(centro * rnd.uniform(0.6, 1.0), year=anno, mileage_km=rnd.randint(30000, 120000))
        v = _valuta(t, {"D": D, "V": V, "Dv": []})
        if "stima_ripiego" not in v:
            continue
        viste += 1
        tt = VB.norm(t)
        sr = v["stima_ripiego"]
        assert (sr["livello"], sr["fascia"], sr["sconto_extra"]) in FASCE and sr["sconto_extra"] == 0.0
        rr = VB.stima_ripiego(D, V, tt, vb, solo=sr["livello"])
        assert BV.fascia(rr["grezzo"]) == sr["fascia"] and rr["grezzo"] >= 10000
        mediana = statistics.median(p for _c2, p in rr["tenuti"])
        assert v["R"] <= rr["grezzo"] + 0.5 <= mediana + 0.5 and v["R"] == v["M"] == int(round(rr["valore"]))
        eta = VB.eta_auto(tt["year"])
        dd, vd = BV.stima_dettaglio(D, tt, n_min=3), BV.stima_dettaglio(V, tt, n_min=3)
        if dd:
            assert v["R"] <= dd["valore"] * (1 - VB.sconto_vetrina(dd["valore"], eta, vb)) + 0.5
        if vd:
            assert v["R"] <= vd["valore"] * (1 - VB.sconto_vendute(vd["valore"], eta, vb)) + 0.5
        assert v["categoria"] in ("candidato", "non_conveniente") and v["margine"] <= 0.40 * v["R"]
    assert viste > 150


# ------------------------------------------------------------------ (e) margine oltre il 40%

def test_e_margine_oltre_il_40_per_cento_resta_dati_insufficienti(monkeypatch):
    _pulisci(monkeypatch)
    base = _unione(17500)
    R = _valuta(_t(14000), base)["R"]
    dentro = _valuta(_t(int(R * 0.61)), base)
    assert dentro["categoria"] == "candidato" and dentro["margine"] <= 0.40 * R
    for prezzo in (int(R * 0.59), int(R * 0.50), int(R * 0.30)):
        v = _valuta(_t(prezzo), base)
        assert v["categoria"] == "dati_insufficienti" and v["motivo_dati"] == "pochi_confronti:solo_unendo"
        assert v.get("M") is None and "margine" not in v and "stima_ripiego" not in v
        assert "serve_controllo_ai" not in v and "sospetto_truffa" not in v
        assert v["ripiego_scartato"] == {"livello": "unione", "fascia": "15-20k",
                                         "motivo": "ripiego:margine_oltre_max"}
        # a parte la nota (senza valori) è la valutazione di prima
        monkeypatch.setenv("AUTOMATISMO_STIMA_RIPIEGO", "0")
        prima = _valuta(_t(prezzo), base)
        monkeypatch.delenv("AUTOMATISMO_STIMA_RIPIEGO")
        assert _j({k: x for k, x in v.items() if k != "ripiego_scartato"}) == _j(prima)


# ------------------------------------------------------------------ (f) le altre auto non cambiano

def test_f_auto_con_valore_prudente_identiche_bit_per_bit(monkeypatch):
    """Con la stima prudente normale il ripiego non si guarda nemmeno: stessa valutazione con le fasce accese, con
    l'interruttore spento e con regole senza fasce (com'era prima del 22/11)."""
    senza_fasce = RG.con_campi(RG.regole(), {"stima_ripiego.fasce": ()})
    rnd = random.Random(1911)
    categorie = set()
    prudenti = 0
    for _ in range(2500):
        centro = rnd.choice((3500, 6000, 9000, 12000, 17000, 24000, 30000))
        anno = rnd.choice((2012, 2016, 2019, 2022))
        D = [_c(i, centro * rnd.uniform(0.8, 1.3), year=anno + rnd.randint(-3, 3), km=rnd.randint(20000, 140000))
             for i in range(rnd.randint(0, 14))]
        V = [_c(100 + i, centro * rnd.uniform(0.7, 1.2), status="scomparso", sel=rnd.choice(("privato", "commerciante")),
                year=anno + rnd.randint(-3, 3), km=rnd.randint(20000, 140000)) for i in range(rnd.randint(0, 14))]
        t = _t(centro * rnd.uniform(0.4, 1.05), year=anno, mileage_km=rnd.randint(30000, 120000),
               targa_e_telefono=rnd.random() < 0.5, damage_class=rnd.choice(("nessuno", "nessuno", "grave")))
        base = {"D": D, "V": V, "Dv": []}
        sg = scovo.soglia(int(t["price_eur"]))
        _pulisci(monkeypatch)
        acceso = _valuta(t, base)
        prima = VB.valuta(dict(t), copy.deepcopy(base), sg, rg=senza_fasce)
        _pulisci(monkeypatch, stima_ripiego="0")
        spento = _valuta(t, base)
        assert _j(spento) == _j(prima)
        if [k for k in CAMPI_RIPIEGO if k in acceso]:
            assert prima["categoria"] == "dati_insufficienti" and prima["motivo_dati"].startswith("pochi_confronti:")
            continue
        assert _j(acceso) == _j(prima)
        if prima.get("fonte_base") in ("commercianti", "vendute"):
            prudenti += 1
            categorie.add(prima["categoria"])
            assert "serve_controllo_ai" not in acceso and acceso["M_fonte"] in ("commercianti", "vendute")
    assert prudenti > 200 and {"opportunita", "non_conveniente", "dati_insufficienti"} <= categorie


def test_f_auto_non_pulite_e_incomplete_mai_col_ripiego(monkeypatch):
    _pulisci(monkeypatch)
    base = _unione(17500)
    for t in (_t(14000, damage_class="grave"), _t(14000, year=None), _t(14000, price_flags=["leasing_o_rata"])):
        v = _valuta(t, base)
        assert v["categoria"] == "dati_insufficienti" and not [k for k in CAMPI_RIPIEGO if k in v]
        assert v["motivo_dati"] in ("auto_non_pulita", "dati_auto_incompleti")
