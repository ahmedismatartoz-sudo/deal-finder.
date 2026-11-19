"""19/11 (cliente: "c'è ancora un gravissimo errore di gonfiamento prezzi nel sito: tante auto hanno un valore di mercato
sovrastimato"; regola storica "non gonfiare i prezzi"). Valore PRUDENTE di pricing/valore_base.stima_prudente con i
parametri di regole.ValoreBase: 40° percentile, sconto di vetrina sui commercianti, vendute come tetto, livello 2 e auto
economiche/vecchie solo con più confronti, mai sopra il calcolo del 18/11. Soglie SOGLIE_PV invariate."""
from __future__ import annotations

import datetime
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder import regole as RG                     # noqa: E402
from dealfinder.pricing import base_vendute as BV        # noqa: E402
from dealfinder.pricing import valore_base as VB         # noqa: E402
from dealfinder.web import scovo                         # noqa: E402

ANNO = datetime.date.today().year


def _c(i, prezzo, status="attivo", sel="commerciante", year=2018, km=60000, **kw):
    r = dict(id=i, source="subito", url=f"https://x/{i}", make="Fiat", model="Panda", fuel="benzina",
             gearbox="manuale", power_kw=Decimal("51"), year=year, mileage_km=km, price_eur=Decimal(str(prezzo)),
             price_flags=None, seller_type=sel, status=status, province="MI", danno="nessuno", problema=False,
             dichiarato=False, stage="", giorni_visto=Decimal("2"),
             giorni_sparita=Decimal("10") if status == "scomparso" else None, giorni_online=Decimal("20"))
    r.update(kw)
    return VB.norm(r)


def _t(prezzo, year=2018, km=60000, **kw):
    t = dict(id=999, make="Fiat", model="Panda", fuel="benzina", gearbox="manuale", power_kw=Decimal("51"),
             year=year, mileage_km=km, price_eur=Decimal(str(prezzo)), price_flags=[], seller_type="privato",
             damage_class="nessuno", problem_search=False, stage_reason=None, targa_e_telefono=True, dichiarato=False)
    t.update(kw)
    return t


def _vb():
    return RG.regole().valore_base


# ------------------------------------------------------------------ pezzi

def test_parametri_e_impronta_delle_regole():
    vb = _vb()
    assert (vb.percentile, vb.n_min, vb.n_min_livello2, vb.n_min_fragile, vb.n_tetto_V) == (0.40, 5, 8, 8, 3)
    assert (vb.trattativa, vb.costi_vetrina, vb.extra_vecchie, vb.sconto_min, vb.sconto_max) == (.03, 300, .03, .05, .20)
    assert vb.n_min == VB.N_MIN_SITO
    # dentro l'impronta delle regole: cambiarli fa riscrivere tutte le valutazioni (guardia "regole_cambiate")
    assert "valore_base" in RG.come_dict()
    diverse = RG.con_campi(RG.regole(), {"valore_base.percentile": 0.5})
    assert RG.impronta(diverse) != RG.impronta()
    # le soglie del sito non cambiano
    assert scovo.soglia(4000) == 1500 and scovo.soglia(7400) == 1800


def test_percentile_e_mediana_di_prima():
    assert BV.percentile([1, 2, 3, 4, 5], .5) == 3 and BV.percentile([1, 2, 3, 4], .5) == 2.5
    assert BV.percentile([10, 20, 30, 40, 50, 60], .4) == 30
    base = [_c(i, p) for i, p in enumerate((9000, 9100, 9200, 9300, 9400, 9500), 1)]
    t = VB.norm(_t(6000))
    assert BV.stima_dettaglio(base, t, n_min=5)["valore"] == 9250            # la misura di prima non cambia
    assert BV.stima_dettaglio(base, t, n_min=5, q=.4)["valore"] == 9200


def test_sconto_di_vetrina_piu_alto_sulle_auto_economiche_e_vecchie():
    vb = _vb()
    assert VB.sconto_vetrina(20000, 5, vb) == 0.05                          # 3% + 1,5% → minimo 5%
    assert VB.sconto_vetrina(10000, 5, vb) == 0.06                          # 3% + 3%
    assert VB.sconto_vetrina(5000, 5, vb) == 0.09                           # 3% + 6%
    assert VB.sconto_vetrina(5000, 13, vb) == 0.12                          # + 3% oltre 10 anni
    assert VB.sconto_vetrina(3000, 13, vb) == 0.16
    assert VB.sconto_vetrina(1200, 15, vb) == 0.20                          # massimo


# ------------------------------------------------------------------ regola

def test_minimo_tra_vendute_e_commercianti_scontati():
    vb = _vb()
    D = [_c(i, p) for i, p in enumerate((10000, 10200, 10400, 10600, 10800, 11000), 1)]
    V_alte = [_c(100 + i, p, status="scomparso", sel="privato") for i, p in enumerate((11000, 11200, 11400, 11600, 11800))]
    t = VB.norm(_t(6000))
    sp = VB.stima_prudente(D, V_alte, t, vb)
    # commercianti: 40° percentile 10.400 − (3% + 300/10.400) = 9.788; vendute (11.320) più alte: valgono i commercianti
    assert sp["fonte"] == "commercianti" and round(sp["valore"]) == 9788 and sp["prudenza"]["sconto_vetrina"] == .0588
    V_basse = [_c(100 + i, p, status="scomparso", sel="privato") for i, p in enumerate((8800, 8900, 9000, 9100, 9200))]
    sp = VB.stima_prudente(D, V_basse, t, vb)
    assert sp["fonte"] == "vendute" and round(sp["valore"]) == 8960 and sp["prudenza"]["tetto_V"] == 8960


def test_poche_vendute_solo_tetto_mai_al_rialzo():
    vb = _vb()
    D = [_c(i, p) for i, p in enumerate((10000, 10200, 10400, 10600, 10800, 11000), 1)]
    t = VB.norm(_t(6000))
    tre_basse = [_c(100 + i, p, status="scomparso", sel="privato") for i, p in enumerate((8000, 8100, 8200))]
    sp = VB.stima_prudente(D, tre_basse, t, vb)
    assert sp["fonte"] == "commercianti" and round(sp["valore"]) == 8080 and sp["prudenza"]["tetto_V"] == 8080
    tre_alte = [_c(100 + i, p, status="scomparso", sel="privato") for i, p in enumerate((14000, 14100, 14200))]
    sp = VB.stima_prudente(D, tre_alte, t, vb)
    assert round(sp["valore"]) == 9788 and "tetto_V" not in sp["prudenza"]
    assert VB.stima_prudente([], tre_basse, t, vb)["valore"] is None        # un tetto da solo non è una stima


def test_livello_2_solo_con_almeno_8_confronti():
    vb = _vb()
    t = VB.norm(_t(6000))
    lontani = [_c(i, 9000 + 50 * i, year=2016) for i in range(1, 8)]       # 7 auto di 2 anni prima: solo livello 2
    v = VB.valuta(_t(6000), {"D": lontani, "V": []}, scovo.soglia(6000))
    assert v["categoria"] == "dati_insufficienti" and v["motivo_dati"].startswith("pochi_confronti")
    otto = lontani + [_c(8, 9400, year=2016)]
    sp = VB.stima_prudente(otto, [], t, vb)
    assert sp["scelta"]["livello"] == 2 and sp["scelta"]["n"] == 8 and sp["fonte"] == "commercianti"
    # commercianti del livello 2 sotto il minimo: solo tetto di una stima più alta (mai sopra il calcolo del 18/11)
    V = [_c(100 + i, p, status="scomparso", sel="privato") for i, p in enumerate((15000, 15100, 15200, 15300, 15400))]
    sp = VB.stima_prudente(lontani, V, t, vb)
    assert sp["fonte"] == "vendute" and sp["prudenza"]["tetto_D"] == round(sp["valore"]) < 15000


def test_auto_economiche_o_vecchie_solo_con_8_confronti():
    vb = _vb()
    anno = ANNO - 13                                                        # oltre 10 anni
    sei = [_c(i, p, year=anno) for i, p in enumerate((5000, 5100, 5200, 5300, 5400, 5500), 1)]
    v = VB.valuta(_t(2400, year=anno), {"D": sei, "V": []}, scovo.soglia(2400))
    assert v["categoria"] == "dati_insufficienti" and v["motivo_dati"] == "pochi_confronti:fragile"
    assert "pochi_confronti:fragile" in VB.MOTIVI_DATI
    nove = sei + [_c(i, p, year=anno) for i, p in ((7, 5000), (8, 5300), (9, 5600))]
    sp = VB.stima_prudente(nove, [], VB.norm(_t(2400, year=anno)), vb)
    assert sp["prudenza"]["D"] == 5220                                      # 40° percentile dei 9
    assert sp["prudenza"]["fragile"] and sp["prudenza"]["sconto_vetrina"] == round(.03 + 300 / 5220 + .03, 4)
    assert round(sp["valore"]) == round(5220 * (1 - sp["prudenza"]["sconto_vetrina"]))


def test_caso_c3_vecchia_economica_non_piu_gonfiata():
    """Come la Citroën C3 2013 109.000 km a 2.400 € del dump del 19/11 (mercato 5.194 col calcolo del 18/11)."""
    anno = ANNO - 13
    D = [_c(i, p, year=anno, km=109000) for i, p in
         enumerate((4600, 4800, 4900, 5000, 5150, 5200, 5300, 5500, 5700, 5900), 1)]
    prima = BV.stima_dettaglio(D, VB.norm(_t(2400, year=anno, km=109000)), n_min=5)["valore"]
    v = VB.valuta(_t(2400, year=anno, km=109000), {"D": D, "V": []}, scovo.soglia(2400))
    # 40° percentile 5.090 − (3% + 300/5.090 + 3% oltre 10 anni = 11,89%) = 4.485: −13% sul valore di prima
    assert prima == 5175 and v["R"] == 4485 and v["R"] <= 0.87 * prima
    assert v["margine"] == v["R"] - 2400


def test_mai_sopra_il_calcolo_del_18_11():
    """Proprietà: su basi a caso il valore prudente non supera MAI la stima del 18/11 (mediana dei commercianti con
    almeno 5 confronti, altrimenti mediana delle vendute)."""
    vb = _vb()
    rnd = random.Random(1911)
    for prova in range(300):
        t = VB.norm(_t(5000, year=2018, km=60000))
        def riga(i, venduta):
            return _c(i, int(rnd.gauss(9000 if not venduta else 8500, 1500)), year=2018 + rnd.randint(-2, 2),
                      km=int(60000 * rnd.uniform(.6, 1.4)), status="scomparso" if venduta else "attivo",
                      sel="privato" if venduta else "commerciante")
        D = [riga(i, False) for i in range(rnd.randint(0, 12))]
        V = [riga(100 + i, True) for i in range(rnd.randint(0, 12))]
        d = BV.stima_dettaglio(D, t, n_min=5)
        v = BV.stima_dettaglio(V, t, n_min=5)
        prima = (d or v or {}).get("valore")
        sp = VB.stima_prudente(D, V, t, vb)
        if sp["valore"] is not None:
            assert prima is not None and sp["valore"] <= prima + 1e-6, (prova, sp["valore"], prima)


# ------------------------------------------------------------------ rivalutazione e misura

def test_rivalutazione_rifa_le_auto_valutate_con_le_regole_di_prima():
    assert "vp.impronta_regole = %(imp)s" in VB.IDS_SQL
    from dealfinder.pipeline import coda_lavori as CL
    assert CL._prefisso("rivaluta_base:1911") == "rivaluta_base:"
    assert "rivaluta_base:1911".startswith(CL.SOLO_SITO) and not CL.spostabile("rivaluta_base:1911")
    assert CL._prefisso("base_vendute:1911") == "base_vendute:"


def test_misura_sulle_vendute_prima_e_dopo():
    """base_vendute._gruppo: errore del calcolo del 18/11 e del valore prudente sulle stesse vendute, per fascia ed
    età, e rapporto commercianti/vendute. Dati finti: commercianti il 12% sopra le vendute."""
    rnd = random.Random(7)
    righe = []
    for i in range(40):
        anno = 2016 + i % 5
        base = 9000 * (1 - 0.085 * (2020 - anno))
        righe.append(dict(make="fiat", model="panda", fuel="benzina", gearbox="manuale", power_kw=51, year=anno,
                          mileage_km=60000, price_eur=round(base * 1.12 * rnd.uniform(.95, 1.05)), price_flags=[],
                          seller_type="commerciante", status="attivo", province="MI", telefono_ok=None,
                          danno="nessuno", problema=False, dichiarato=False, stage="", giorni_visto=2,
                          giorni_sparita=None, giorni_online=20))
        righe.append(dict(righe[-1], seller_type="privato", status="scomparso", giorni_sparita=5,
                          price_eur=round(base * rnd.uniform(.95, 1.05))))
    st, prec, rapp = Counter(), defaultdict(list), defaultdict(list)
    BV._gruppo(righe, st, prec, [], rapp)
    prima, dopo = BV._err(prec["prima_su_stesse"]), BV._err(prec["prudente"])
    assert prima["n"] == dopo["n"] > 20
    assert prima["scostamento_medio_pct"] > 8 and dopo["scostamento_medio_pct"] < prima["scostamento_medio_pct"] - 8
    assert dopo["errore_mediano_pct"] < prima["errore_mediano_pct"]
    assert any(k.startswith("prudente|fascia:") for k in prec) and any(k.startswith("prudente|eta:") for k in prec)
    r = BV._rapporti(rapp)
    assert 1.08 < r["D/V|fascia:5-10k"]["mediana"] < 1.16 and any(k.startswith("p40/mediana|") for k in r)
    assert statistics.median(rapp["p40/mediana|fascia:5-10k"]) < 1
