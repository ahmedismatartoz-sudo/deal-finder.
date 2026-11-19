"""19/11 (cliente: "c'è ancora un gravissimo errore di gonfiamento prezzi nel sito: tante auto hanno un valore di mercato
sovrastimato"; regola storica "non gonfiare i prezzi"). Valore PRUDENTE di pricing/valore_base.stima_prudente con i
parametri di regole.ValoreBase. 19/11 pomeriggio, regola mediana-1911b (la prudente-1911 correggeva troppo: sito da ~620
a 187 auto, −6,7% sulle vendute): mediana, sconto di vetrina per fascia del valore interpolato (scostamento atteso
≈ −1% per fascia sulla misura BASE_VENDUTE del 19/11 10:47), minimo con le vendute solo sotto 5.000 €, livello 2 con
5 confronti, economiche/vecchie con 8, mai sopra il calcolo del 18/11. Soglie SOGLIE_PV invariate.
19/11 sera, regola mediana-1911c: stessi passi, sconti ricalibrati sulla misura BASE_VENDUTE 1911b per fascia della STIMA
senza sconto ("prima|fascia_stima"): 5% fino a 4.500 €, 4,5% a 7.500, 4,4% a 12.500, 3,4% a 17.500, 3,2% da 22.500."""
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

# misura BASE_VENDUTE 1911b (19/11, vendute degli ultimi 30 giorni), calcolo del 18/11 SENZA sconto per fascia della
# STIMA ("prima|fascia_stima"): scostamento medio (stima − ultimo prezzo delle vendute), al centro di ogni fascia
MISURA_1911B = {7500: .041, 12500: .037, 17500: .024, 22500: .023, 40000: .023}
MISURA_1911B_SOTTO_5000 = .100


def test_parametri_e_impronta_delle_regole():
    vb = _vb()
    assert (vb.percentile, vb.n_min, vb.n_min_livello2, vb.n_min_fragile, vb.n_tetto_V) == (0.50, 5, 5, 8, 3)
    assert vb.sconti == ((4500, .05), (7500, .045), (12500, .044), (17500, .034), (22500, .032))
    assert (vb.extra_vecchie, vb.sconto_min, vb.sconto_max, vb.tetto_V_sotto) == (0.0, 0.0, .25, 5000)
    assert (vb.fragile_valore, vb.fragile_anni) == (5000, 10)
    assert vb.n_min == VB.N_MIN_SITO and VB.REGOLA == "mediana-1911c"
    # dentro l'impronta delle regole: cambiarli fa riscrivere tutte le valutazioni (guardia "regole_cambiate")
    assert "valore_base" in RG.come_dict()
    diverse = RG.con_campi(RG.regole(), {"valore_base.percentile": 0.4})
    assert RG.impronta(diverse) != RG.impronta()
    # le soglie del sito non cambiano
    assert scovo.soglia(4000) == 1500 and scovo.soglia(7400) == 1800
    assert scovo.SOGLIE_PV == [(7000, 1500), (10000, 1800), (15000, 2500), (20000, 3200), (30000, 4000),
                               (10**9, 5000)]


def test_percentile_e_mediana_di_prima():
    assert BV.percentile([1, 2, 3, 4, 5], .5) == 3 and BV.percentile([1, 2, 3, 4], .5) == 2.5
    assert BV.percentile([10, 20, 30, 40, 50, 60], .4) == 30
    base = [_c(i, p) for i, p in enumerate((9000, 9100, 9200, 9300, 9400, 9500), 1)]
    t = VB.norm(_t(6000))
    assert BV.stima_dettaglio(base, t, n_min=5)["valore"] == 9250            # la misura di prima non cambia
    assert BV.stima_dettaglio(base, t, n_min=5, q=.4)["valore"] == 9200


def test_sconto_di_vetrina_per_fascia_continuo():
    vb = _vb()
    assert [VB.sconto_vetrina(x, 5, vb) for x in (1500, 4500, 7500, 12500, 17500, 22500, 60000)] == \
        [.05, .05, .045, .044, .034, .032, .032]
    assert VB.sconto_vetrina(10000, 5, vb) == .0445 and VB.sconto_vetrina(20000, 5, vb) == .033
    assert VB.sconto_vetrina(6000, 5, vb) == .0475
    assert VB.sconto_vetrina(6000, 13, vb) == .0475                         # nessun extra oltre 10 anni (vedi sotto)
    # niente scalini: 100 € in più cambiano lo sconto di meno di mezzo punto, e lo sconto non sale mai col valore
    xs = list(range(500, 40000, 100))
    ss = [VB.sconto_vetrina(x, 5, vb) for x in xs]
    assert all(0 <= a - b < .005 for a, b in zip(ss, ss[1:]))
    # nemmeno il valore netto fa scalini all'indietro: chi vale di più resta sopra
    nn = [x * (1 - s) for x, s in zip(xs, ss)]
    assert all(b > a for a, b in zip(nn, nn[1:]))


def test_scostamento_atteso_circa_meno_uno_per_cento_in_ogni_fascia():
    """Con lo scostamento della fascia (stima del 18/11 senza sconto, misura 1911b), il valore scontato sta tra −2% e 0
    (mai positivo = gonfiato). Sotto 5.000 € il valore è min(V, D scontata) con D/V = 1,078: lo sconto da solo darebbe
    +4,5%, il minimo con le vendute (dispersione V/D 10-15%) lo porta tra −0,7% e −2,4%."""
    import math
    vb = _vb()
    for x, b in MISURA_1911B.items():
        atteso = (1 + b) * (1 - VB.sconto_vetrina(x, 5, vb)) - 1
        assert -0.02 <= atteso <= 0, (x, atteso)
    nd = statistics.NormalDist()
    q = [nd.inv_cdf((i + .5) / 200) for i in range(200)]
    c = 1 - VB.sconto_vetrina(4000, 5, vb)
    for sig, lo, hi in ((.10, -.01, 0), (.15, -.03, -.02)):
        m = sum(min(math.exp(sig * z) / VB.interpola(4000, vb.rapporto_dv), c) for z in q) / len(q)
        assert lo <= (1 + MISURA_1911B_SOTTO_5000) * m - 1 <= hi, sig
    # la regola mediana-1911b (23% / 8,5% / 4,5% / 2% / 0,5%) misurava −16,3%, −7,6%, −1,1%, +0,2%, +1,7%
    assert (1 + MISURA_1911B[22500]) * (1 - .005) - 1 > 0


def test_sconto_sulle_sole_vendute_non_sconta_due_volte():
    vb = _vb()
    for x in (3000, 6000, 9000, 15000):
        sd, sv = VB.sconto_vetrina(x, 5, vb), VB.sconto_vendute(x, 5, vb)
        assert 0 <= sv < sd
        r = VB.interpola(x, vb.rapporto_dv)
        assert sv == round(max(0, 1 - (1 - VB.sconto_vetrina(x * r, 5, vb)) * r), 4)
    assert VB.sconto_vendute(30000, 5, vb) == .032                          # D/V = 1: lo stesso sconto
    assert VB.sconto_vendute(3000, 5, vb) == 0                              # D/V 1,078 > 1/(1 − 5%): mai negativo


# ------------------------------------------------------------------ regola

def test_sopra_5000_commercianti_scontati_senza_minimo_con_le_vendute():
    vb = _vb()
    D = [_c(i, p) for i, p in enumerate((10000, 10200, 10400, 10600, 10800, 11000), 1)]
    t = VB.norm(_t(6000))
    V_alte = [_c(100 + i, p, status="scomparso", sel="privato") for i, p in enumerate((11000, 11200, 11400, 11600, 11800))]
    sp = VB.stima_prudente(D, V_alte, t, vb)
    # mediana 10.500 − 4,44% = 10.033,8
    assert sp["fonte"] == "commercianti" and round(sp["valore"], 1) == 10033.8 and sp["prudenza"]["sconto_vetrina"] == .0444
    V_basse = [_c(100 + i, p, status="scomparso", sel="privato") for i, p in enumerate((8800, 8900, 9000, 9100, 9200))]
    sp2 = VB.stima_prudente(D, V_basse, t, vb)
    # il minimo di due stime rumorose sposterebbe in basso tutta la fascia: sopra 5.000 € valgono i commercianti
    assert sp2["valore"] == sp["valore"] and "tetto_V" not in sp2["prudenza"]
    assert sp2["prudenza"]["V"] == 9000 and sp2["prudenza"]["V_netta"] < 9000
    # sole vendute: mediana meno lo sconto della fascia tolta la differenza commercianti/vendute
    sp3 = VB.stima_prudente([], V_basse, t, vb)
    assert sp3["fonte"] == "vendute" and sp3["valore"] == 9000 * (1 - VB.sconto_vendute(9000, 8, vb))


def test_sotto_5000_minimo_tra_vendute_e_commercianti_scontati():
    vb = _vb()
    D = [_c(i, p) for i, p in enumerate((3800, 3900, 4000, 4100, 4200, 4300, 4400, 4500, 4600, 4700), 1)]
    t = VB.norm(_t(2000))
    V = [_c(100 + i, p, status="scomparso", sel="privato")
         for i, p in enumerate((2900, 2950, 3000, 3050, 3100, 3150, 3200, 3250))]
    sp = VB.stima_prudente(D, V, t, vb)
    assert sp["prudenza"]["D_netta"] == round(4250 * .95) and sp["valore"] == 3075 and sp["fonte"] == "vendute"
    # poche vendute (3-4): solo tetto, mai al rialzo; la stima mostrata resta quella dei commercianti
    sp = VB.stima_prudente(D, V[:3], t, vb)
    assert sp["fonte"] == "commercianti" and sp["valore"] == 2950 and sp["prudenza"]["tetto_V"] == 2950
    alte = [_c(100 + i, p, status="scomparso", sel="privato") for i, p in enumerate((4500, 4600, 4700))]
    sp = VB.stima_prudente(D, alte, t, vb)
    assert sp["valore"] == 4250 * .95 and "tetto_V" not in sp["prudenza"]
    assert VB.stima_prudente([], V[:3], t, vb)["valore"] is None             # un tetto da solo non è una stima


def test_livello_2_con_5_confronti_come_il_18_11():
    vb = _vb()
    t = VB.norm(_t(6000))
    lontani = [_c(i, 9000 + 50 * i, year=2016) for i in range(1, 6)]       # 5 auto di 2 anni prima: solo livello 2
    sp = VB.stima_prudente(lontani, [], t, vb)
    assert sp["scelta"]["livello"] == 2 and sp["scelta"]["n"] == 5 and sp["fonte"] == "commercianti"
    v = VB.valuta(_t(6000), {"D": lontani[:4], "V": []}, scovo.soglia(6000))
    assert v["categoria"] == "dati_insufficienti" and v["motivo_dati"].startswith("pochi_confronti")


def test_auto_economiche_o_vecchie_solo_con_8_confronti():
    vb = _vb()
    anno = ANNO - 13                                                        # oltre 10 anni
    sei = [_c(i, p, year=anno) for i, p in enumerate((5000, 5100, 5200, 5300, 5400, 5500), 1)]
    v = VB.valuta(_t(2400, year=anno), {"D": sei, "V": []}, scovo.soglia(2400))
    assert v["categoria"] == "dati_insufficienti" and v["motivo_dati"] == "pochi_confronti:fragile"
    assert "pochi_confronti:fragile" in VB.MOTIVI_DATI
    nove = sei + [_c(i, p, year=anno) for i, p in ((7, 5000), (8, 5300), (9, 5600))]
    sp = VB.stima_prudente(nove, [], VB.norm(_t(2400, year=anno)), vb)
    assert sp["prudenza"]["D"] == 5300 and sp["prudenza"]["fragile"]        # mediana dei 9
    assert sp["prudenza"]["sconto_vetrina"] == VB.sconto_vetrina(5300, 13, vb)
    assert round(sp["valore"]) == round(5300 * (1 - sp["prudenza"]["sconto_vetrina"]))
    # recente ma economica (sotto 5.000 € dopo lo sconto): anche lei 8 confronti, a ogni livello
    lontani = [_c(i, 4000 + 50 * i, year=2016) for i in range(1, 8)]       # 7 al livello 2
    assert VB.stima_prudente(lontani, [], VB.norm(_t(1500)), vb)["fragile_poche"]
    sp = VB.stima_prudente(lontani + [_c(8, 4100, year=2016)], [], VB.norm(_t(1500)), vb)
    assert sp["scelta"]["n"] == 8 and sp["scelta"]["livello"] == 2


def test_caso_c3_vecchia_economica_non_piu_gonfiata():
    """Come la Citroën C3 2013 109.000 km a 2.400 € del dump del 19/11 (mercato 5.194 col calcolo del 18/11)."""
    anno = ANNO - 13
    D = [_c(i, p, year=anno, km=109000) for i, p in
         enumerate((4600, 4800, 4900, 5000, 5150, 5200, 5300, 5500, 5700, 5900), 1)]
    prima = BV.stima_dettaglio(D, VB.norm(_t(2400, year=anno, km=109000)), n_min=5)["valore"]
    v = VB.valuta(_t(2400, year=anno, km=109000), {"D": D, "V": []}, scovo.soglia(2400))
    # 1911b: mediana 5.175 − 19,74% = 4.153 (−20%, troppo: la fascia misurava −16%); 1911c: − 4,89% (tra 5% a 4.500 €
    # e 4,5% a 7.500 €) = 4.922, sotto 5.000 € quindi fragile (10 confronti: ok)
    assert prima == 5175 and v["R"] == 4922 and v["R"] < prima and v["prudenza"]["fragile"]
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


def test_mai_sopra_anche_economiche_e_vecchie():
    """Proprietà (giudice 19/11): come sopra su auto economiche/vecchie (fragili) e di ogni fascia: mai sopra il 18/11,
    e la stima mostrata ha sempre ≥ 5 confronti e ≥ 8 se l'auto è fragile (sotto 5.000 € o oltre 10 anni)."""
    vb = _vb()
    rnd = random.Random(19112)
    visti = Counter()
    for prova in range(800):
        base, yr = rnd.choice([1500, 3000, 4500, 5500, 6000, 9000, 20000]), rnd.choice([2008, 2012, 2015, 2019, 2022])
        t = VB.norm(_t(1000, year=yr, km=60000))
        def riga(i, venduta):
            return _c(i, max(200, int(rnd.gauss(base * (.95 if venduta else 1), base * rnd.uniform(.05, .3)))),
                      year=yr + rnd.randint(-2, 2), km=int(60000 * rnd.uniform(.5, 1.5)),
                      status="scomparso" if venduta else "attivo", sel="privato" if venduta else "commerciante")
        D = [riga(i, False) for i in range(rnd.randint(0, 15))]
        V = [riga(100 + i, True) for i in range(rnd.randint(0, 15))]
        prima = (BV.stima_dettaglio(D, t, n_min=5) or BV.stima_dettaglio(V, t, n_min=5) or {}).get("valore")
        sp = VB.stima_prudente(D, V, t, vb)
        if sp["valore"] is None:
            continue
        assert prima is not None and sp["valore"] <= prima + 1e-6, (prova, sp["valore"], prima)
        assert sp["scelta"]["n"] >= 5
        fragile = sp["valore"] < vb.fragile_valore or ANNO - yr > vb.fragile_anni
        assert sp["prudenza"]["fragile"] == fragile
        if fragile:
            assert sp["scelta"]["n"] >= 8, (prova, sp["prudenza"])
        visti["fragile" if fragile else "normale"] += 1
        visti["livello2"] += sp["scelta"]["livello"] == 2
    assert visti["fragile"] > 50 and visti["normale"] > 50 and visti["livello2"] > 5, visti


def test_meno_severa_della_regola_del_mattino_ma_mai_sopra_il_18_11():
    """Sulle auto sopra 10.000 € la regola mediana-1911c (come la 1911b) sta tra la prudente-1911 (40° percentile − 3% − 300 €/D, almeno
    5%) e il calcolo del 18/11: il valore torna su dove la misura diceva che era troppo basso."""
    vb = _vb()
    D = [_c(i, p, year=2021, km=40000) for i, p in enumerate(range(19000, 21400, 200), 1)]
    t = VB.norm(_t(15000, year=2021, km=40000))
    prima = BV.stima_dettaglio(D, t, n_min=5)["valore"]
    p40 = BV.stima_dettaglio(D, t, n_min=5, q=.4)["valore"]
    mattino = p40 * (1 - max(.05, .03 + 300 / p40))
    sp = VB.stima_prudente(D, [], t, vb)
    assert mattino < sp["valore"] <= prima and sp["valore"] == prima * (1 - VB.sconto_vetrina(prima, 5, vb))


# ------------------------------------------------------------------ rivalutazione e misura

def test_rivalutazione_rifa_le_auto_valutate_con_le_regole_di_prima():
    assert "vp.impronta_regole = %(imp)s" in VB.IDS_SQL
    from dealfinder.pipeline import coda_lavori as CL
    for et in ("1911", "1911b", "1911c"):
        assert CL._prefisso(f"rivaluta_base:{et}") == "rivaluta_base:"
        assert f"rivaluta_base:{et}".startswith(CL.SOLO_SITO) and not CL.spostabile(f"rivaluta_base:{et}")
        assert CL._prefisso(f"base_vendute:{et}") == "base_vendute:"


def test_misura_sulle_vendute_prima_e_dopo():
    """base_vendute._gruppo: errore del calcolo del 18/11 e del valore prudente sulle stesse vendute, per fascia (del
    prezzo vero e della stima) ed età, e rapporto commercianti/vendute. Dati finti: commercianti il 12% sopra le
    vendute."""
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
    assert prima["scostamento_medio_pct"] > 8 and dopo["scostamento_medio_pct"] < prima["scostamento_medio_pct"] - 5
    assert dopo["errore_mediano_pct"] < prima["errore_mediano_pct"]
    for k in ("fascia:", "eta:", "fascia_stima:", "eta_fascia:"):
        assert any(x.startswith("prudente|" + k) for x in prec), k
    r = BV._rapporti(rapp)
    assert 1.08 < r["D/V|fascia:5-10k"]["mediana"] < 1.16 and any(k.startswith("p40/mediana|") for k in r)
    assert any(k.startswith("D/V|fascia_stima:") for k in r)
    assert statistics.median(rapp["p40/mediana|fascia:5-10k"]) < 1


def test_economiche_mostrano_il_mercato_della_valutazione():
    """Sezione "Sotto 1.000 €" (scovo.item_eco): il Mercato mostrato è M della valutazione (valore prudente), non la
    mediana vecchia della tabella valuations; il tetto prezzo × 1,4 + 500 resta."""
    r = dict(id=1, source="subito", make="fiat", model="panda", year=2012, mileage_km=150000, fuel="benzina",
             price_eur=900, private_median=1700, parts_cost_low=0, parts_cost_high=0, n_photos=0,
             pv='{"categoria": "opportunita", "M": 1400, "R": 1400, "margine": 500, "n": 9}')
    if scovo.POLICY_SITO == "pv2":
        assert scovo.item_eco(r)["mercato"] == 1400
        assert scovo.item_eco(dict(r, pv='{"categoria": "opportunita", "M": 3000}'))["mercato"] == 1760
    assert scovo.item_eco(dict(r, pv=None))["mercato"] == 1700
