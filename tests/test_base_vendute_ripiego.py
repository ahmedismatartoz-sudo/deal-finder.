"""20/11 (proprietario: recuperare i dati insufficienti "in qualche modo", MA "non gonfiare i prezzi"). B1 = SOLA MISURA
della stima di ripiego: pricing/valore_base.stima_ripiego (commercianti e vendute insieme, 40° percentile, sconto base,
tetto delle stime deboli) e le chiavi "ripiego|..." / "ripiego_stimate:" / "ripiego_buone:" di pricing/base_vendute.
Le valutazioni del sito NON cambiano: valuta() non usa il ripiego e livelli_accesi() torna () senza fasce ammesse."""
from __future__ import annotations

import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder import automatismi                       # noqa: E402
from dealfinder import regole as RG                     # noqa: E402
from dealfinder.pricing import base_vendute as BV        # noqa: E402
from dealfinder.pricing import valore_base as VB         # noqa: E402
from dealfinder.web import scovo                         # noqa: E402


def _r(i, prezzo, status="attivo", sel="commerciante", year=2018, km=60000, **kw):
    r = dict(id=i, make="Fiat", model="Panda", fuel="benzina", gearbox="manuale", power_kw=51.0, year=year,
             mileage_km=km, price_eur=float(prezzo), price_flags=None, seller_type=sel, status=status, province="MI",
             telefono_ok=True, danno="nessuno", problema=False, dichiarato=False, stage="", giorni_visto=2.0,
             giorni_sparita=10.0 if status == "scomparso" else None, giorni_online=20.0)
    r.update(kw)
    return r


def _vb():
    return RG.regole().valore_base


def _basi():
    """3 commercianti e 3 vendute simili: nessuna base arriva a 5 da sola, insieme sì (pochi_confronti:solo_unendo)."""
    D = [_r(i, p) for i, p in ((1, 9000), (2, 9400), (3, 9800))]
    V = [_r(10 + i, p, status="scomparso", sel="privato") for i, p in ((1, 8400), (2, 8800), (3, 9200))]
    return D, V


def test_costanti_e_prove():
    assert VB.REGOLA_RIPIEGO == "ripiego-2011a"
    assert VB.RIPIEGO_PROVE == {"unione": BV.LIVELLI, "largo": ((3, 0.60),), "largo4": ((4, 0.80),)}
    assert VB.RIPIEGO_ATTIVABILI == ("unione", "largo") and VB.RIPIEGO_MARGINE_MAX == 0.40
    # B1 non tocca le regole: nessun campo nuovo nell'impronta
    assert "stima_ripiego" not in RG.come_dict()


def test_unione_dove_il_prudente_non_stima():
    D, V = _basi()
    t = _r(999, 7000, sel="privato")
    assert VB.stima_prudente(D, V, t, _vb())["valore"] is None
    rr = VB.stima_ripiego(D, V, t, _vb())
    assert rr["livello"] == "unione" and rr["n"] == 6 and rr["quota_D"] == 0.5 and rr["sconto_extra"] == 0.0
    prezzi = sorted(float(c["price_eur"]) for c in D + V)
    assert rr["mediana"] == statistics.median(prezzi)
    assert rr["grezzo"] == BV.percentile(prezzi, 0.40) < rr["mediana"]
    assert rr["fascia"] == BV.fascia(rr["grezzo"]) == "5-10k"
    eta = VB.eta_auto(2018)
    atteso_sb = 0.5 * VB.sconto_vetrina(rr["grezzo"], eta, _vb()) + 0.5 * VB.sconto_vendute(rr["grezzo"], eta, _vb())
    assert rr["sconto_base"] == round(atteso_sb, 4)
    # tetti: le vendute da sole (3 confronti, mediana 8.800 già scontata) stanno sotto il 40° percentile scontato
    v_debole = 8800 * (1 - VB.sconto_vendute(8800, eta, _vb()))
    assert v_debole < rr["grezzo"] * (1 - atteso_sb) and rr["tetto"] == int(round(v_debole))
    assert abs(rr["valore"] - min(rr["grezzo"] * (1 - atteso_sb), v_debole)) < 1e-6
    assert rr["valore"] <= rr["grezzo"] <= rr["mediana"]


def test_largo_solo_con_sei_confronti_e_ordine_delle_prove():
    # anno a 3 anni di distanza: fuori dai livelli di oggi (±1, ±2), dentro "largo" (±3, km ±60%)
    base = [_r(i, 9000 + 100 * i, year=2015) for i in range(1, 4)] + \
           [_r(10 + i, 8600 + 100 * i, status="scomparso", sel="privato", year=2015) for i in range(1, 4)]
    D, V = base[:3], base[3:]
    t = _r(999, 7000, sel="privato")
    assert VB.stima_ripiego(D, V, t, _vb(), solo="unione")["valore"] is None
    rr = VB.stima_ripiego(D, V, t, _vb())
    assert rr["livello"] == "largo" and rr["n"] == 6 and (rr["anni"], rr["km"]) == (3, 0.60)
    assert VB.stima_ripiego(D[:2], V, t, _vb())["valore"] is None            # 5 confronti: al largo ne servono 6
    assert VB.stima_ripiego(D, V, t, _vb(), solo=("largo4",))["livello"] == "largo4"
    # anno ±4: solo "largo4" (misurato e basta), mai nelle prove di default
    D4 = [dict(c, year=2014) for c in D]
    V4 = [dict(c, year=2014) for c in V]
    assert VB.stima_ripiego(D4, V4, t, _vb())["valore"] is None
    assert VB.stima_ripiego(D4, V4, t, _vb(), solo="largo4")["n"] == 6
    # nessuna base o nessun simile: fuori (modello_assente / nessuno_simile restano dati insufficienti)
    assert VB.stima_ripiego([], [], t, _vb())["valore"] is None
    lontane = [dict(c, year=2005) for c in D + V]
    assert VB.stima_ripiego(lontane[:3], lontane[3:], t, _vb(), solo=tuple(VB.RIPIEGO_PROVE))["valore"] is None


def test_esclude_l_auto_stessa_e_cambio_potenza():
    D, V = _basi()
    v = V[0]
    rr = VB.stima_ripiego(D, V, v, _vb(), escludi=v)
    assert rr["n"] == 5 and all(c is not v for c, _p in rr["tenuti"])
    t = _r(999, 7000, sel="privato", gearbox="automatico")
    assert VB.stima_ripiego(D, V, t, _vb(), solo=tuple(VB.RIPIEGO_PROVE))["valore"] is None
    t = _r(999, 7000, sel="privato", power_kw=70.0)
    assert VB.stima_ripiego(D, V, t, _vb(), solo=tuple(VB.RIPIEGO_PROVE))["valore"] is None


def test_mai_sopra_le_stime_deboli_ne_la_mediana_grezza():
    rnd = random.Random(2011)
    vb = _vb()
    provate = 0
    for giro in range(300):
        centro = rnd.choice((3500, 6000, 9000, 14000, 19000, 30000))
        anno = rnd.choice((2012, 2016, 2019, 2022))
        D = [_r(i, centro * rnd.uniform(0.8, 1.3), year=anno + rnd.randint(-3, 3), km=rnd.randint(20000, 140000))
             for i in range(rnd.randint(0, 5))]
        V = [_r(100 + i, centro * rnd.uniform(0.7, 1.2), status="scomparso", sel=rnd.choice(("privato", "commerciante")),
                year=anno + rnd.randint(-3, 3), km=rnd.randint(20000, 140000)) for i in range(rnd.randint(0, 5))]
        t = _r(999, centro * 0.8, sel="privato", year=anno, km=rnd.randint(30000, 120000))
        eta = VB.eta_auto(anno)
        for prova in VB.RIPIEGO_PROVE:
            rr = VB.stima_ripiego(D, V, t, vb, solo=prova)
            if rr["valore"] is None:
                continue
            provate += 1
            assert rr["n"] >= VB.RIPIEGO_N_MIN[prova]
            assert rr["valore"] <= rr["grezzo"] + 1e-9 <= rr["mediana"] + 2e-9
            assert rr["mediana"] == statistics.median(p for _c, p in rr["tenuti"])
            dd = BV.stima_dettaglio(D, t, n_min=3)
            vd = BV.stima_dettaglio(V, t, n_min=3)
            if dd:
                assert rr["valore"] <= dd["valore"] * (1 - VB.sconto_vetrina(dd["valore"], eta, vb)) + 1e-6
            if vd:
                assert rr["valore"] <= vd["valore"] * (1 - VB.sconto_vendute(vd["valore"], eta, vb)) + 1e-6
    assert provate > 100


def test_sconto_extra_della_coppia_per_ultimo():
    D, V = _basi()
    t = _r(999, 7000, sel="privato")
    senza = VB.stima_ripiego(D, V, t, _vb())
    rp = SimpleNamespace(percentile=0.40, n_min_unione=5, n_min_largo=6, largo_anni=3, largo_km=0.60,
                         fasce=(("unione", "5-10k", 0.05), ("largo", "10-15k", 0.03)))
    con = VB.stima_ripiego(D, V, t, _vb(), rp=rp)
    assert con["sconto_extra"] == 0.05 and abs(con["valore"] - senza["valore"] * 0.95) < 1e-6
    assert VB.sconto_extra_ripiego("unione", "10-15k", rp) == 0.0
    assert VB.sconto_extra_ripiego("unione", "5-10k", None) == 0.0
    # oltre il 15% la coppia andrebbe scartata: qui comunque mai più del tetto, mai negativo
    assert VB.sconto_extra_ripiego("x", "y", SimpleNamespace(fasce=(("x", "y", 0.5),))) == VB.RIPIEGO_EXTRA_MAX
    assert VB.sconto_extra_ripiego("x", "y", SimpleNamespace(fasce=(("x", "y", -0.2),))) == 0.0


def test_livelli_accesi_vuoto_senza_fasce(monkeypatch):
    for k in ("AUTOMATISMO_STIMA_RIPIEGO", "AUTOMATISMO_STIMA_RIPIEGO_LARGO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(automatismi, "valori", lambda: {})
    assert VB.livelli_accesi() == ()                       # B1: le regole non hanno fasce → sempre ()
    monkeypatch.setenv("AUTOMATISMO_STIMA_RIPIEGO", "1")
    monkeypatch.setenv("AUTOMATISMO_STIMA_RIPIEGO_LARGO", "1")
    assert VB.livelli_accesi() == ()
    assert VB.livelli_accesi(SimpleNamespace(fasce=())) == ()
    # con le fasce vuote gli interruttori non si leggono nemmeno (nessuna lettura del database)
    monkeypatch.setattr(automatismi, "attivo", lambda *a, **k: (_ for _ in ()).throw(AssertionError("letto")))
    assert VB.livelli_accesi() == ()


def test_livelli_accesi_con_fasce_e_interruttori(monkeypatch):
    monkeypatch.setattr(automatismi, "valori", lambda: {})
    for k in ("AUTOMATISMO_STIMA_RIPIEGO", "AUTOMATISMO_STIMA_RIPIEGO_LARGO"):
        monkeypatch.delenv(k, raising=False)
    rp = SimpleNamespace(fasce=(("largo", "10-15k", 0.02), ("unione", "5-10k", 0.0), ("largo4", ">20k", 0.0)))
    assert VB.livelli_accesi(rp) == ("unione", "largo")    # largo4 mai: solo misurato
    assert VB.livelli_accesi(SimpleNamespace(fasce=(("largo", "10-15k", 0.02),))) == ("largo",)
    monkeypatch.setenv("AUTOMATISMO_STIMA_RIPIEGO_LARGO", "0")
    assert VB.livelli_accesi(rp) == ("unione",)
    monkeypatch.setenv("AUTOMATISMO_STIMA_RIPIEGO", "0")
    assert VB.livelli_accesi(rp) == ()
    monkeypatch.delenv("AUTOMATISMO_STIMA_RIPIEGO")
    monkeypatch.setattr(automatismi, "valori", lambda: {"stima_ripiego": False})
    assert VB.livelli_accesi(rp) == ()
    assert {"stima_ripiego", "stima_ripiego_largo"} <= set(automatismi.NOMI)


def test_valuta_non_usa_il_ripiego():
    """B1: la valutazione del sito resta dati_insufficienti (solo_unendo), senza alcun campo del ripiego."""
    D, V = _basi()
    for c in D + V:
        c.update(source="subito", url="https://x/%d" % c["id"], version_raw=None, first_seen_at=None,
                 disappeared_at=None)
    t = dict(id=999, make="Fiat", model="Panda", fuel="benzina", gearbox="manuale", power_kw=51, year=2018,
             mileage_km=60000, price_eur=7000, price_flags=[], seller_type="privato", damage_class="nessuno",
             problem_search=False, stage_reason=None, targa_e_telefono=True)
    pv = VB.valuta(t, {"D": D, "V": V, "Dv": []}, scovo.soglia(7000))
    assert pv["categoria"] == "dati_insufficienti" and pv["motivo_dati"] == "pochi_confronti:solo_unendo"
    assert "stima_ripiego" not in pv and pv.get("M") is None and "margine" not in pv


def test_finestra_dall_etichetta():
    assert BV.finestra("base_vendute:rip30") == 30 == BV.PRECISIONE_GIORNI
    assert BV.finestra("base_vendute:rip60g60") == 60
    assert BV.finestra("base_vendute:rip_g45") == 45
    assert BV.finestra("base_vendute:rip9g90") == BV.VENDUTE_GIORNI          # mai oltre le vendute della base
    assert BV.finestra("base_vendute:ora") == 30 and BV.finestra("base_vendute:1911b") == 30
    assert BV.finestra("base_vendute:prog60") == 30 and BV.finestra("") == 30


def _misura(righe, giorni=None):
    st, prec, esempi = Counter(), defaultdict(list), []
    if giorni is None:
        BV._gruppo(righe, st, prec, esempi)
    else:
        BV._gruppo(righe, st, prec, esempi, None, giorni)
    return st, prec


def test_chiavi_della_misura_sulle_vendute_e_finestra():
    D, V = _basi()
    V[2]["giorni_sparita"] = 45.0                          # dentro i 60 giorni, fuori dai 30
    st, prec = _misura(D + V)
    assert st["ripiego_vendute_provate"] == 2 and st["vendute_senza_stima_prudente"] == 0
    # l'auto stessa è esclusa: restano 5 confronti (bastano all'unione, non alle prove larghe che ne chiedono 6)
    assert len(prec["ripiego|unione"]) == 2 and "ripiego|largo" not in prec and "ripiego|largo4" not in prec
    v = V[0]
    atteso = VB.stima_ripiego(D, V, v, _vb(), escludi=v)
    assert (atteso["valore"], float(v["price_eur"])) in prec["ripiego|unione"] and atteso["n"] == 5
    eta = BV.fascia_eta(2018)
    assert len(prec["ripiego|unione|eta:" + eta]) == 2
    fasce = [k for k in prec if k.startswith("ripiego|unione|fascia_stima:")]
    assert fasce and sum(len(prec[k]) for k in fasce) == 2
    assert set(k.rsplit(":", 1)[1] for k in fasce) <= {"<5k", "5-10k", "10-15k", "15-20k", ">20k"}
    e = BV._err(prec["ripiego|unione"])
    assert e["n"] == 2 and "scostamento_medio_pct" in e and "errore_mediano_pct" in e and "entro_10pct_quota" in e
    st60, prec60 = _misura(D + V, 60)
    assert st60["ripiego_vendute_provate"] == 3 and len(prec60["ripiego|unione"]) == 3
    # le chiavi di prima restano uguali (stessa misura con e senza ripiego)
    assert BV._err(prec["sito_n5_solo_unendo"])["n"] == 2 and BV._err(prec60["sito_n5_solo_unendo"])["n"] == 3


def test_vendute_con_valore_prudente_non_misurano_il_ripiego():
    D = [_r(i, 10000 + 100 * i) for i in range(1, 9)]
    V = [_r(20 + i, 9500 + 100 * i, status="scomparso", sel="privato") for i in range(1, 9)]
    st, prec = _misura(D + V)
    assert len(prec["prudente"]) == 8 and st["ripiego_vendute_provate"] == 0
    assert not [k for k in prec if k.startswith("ripiego|")]


def test_conteggi_affari_tra_i_privati_senza_stima():
    D, V = _basi()
    buona = _r(900, 6500, sel="privato")                   # ben sotto il valore di ripiego: entrerebbe
    cara = _r(901, 8600, sel="privato")                    # stimata, margine sotto la soglia
    regalo = _r(902, 3000, sel="privato")                  # margine oltre il 40% del valore: non conta come buona
    st, _prec = _misura(D + V + [buona, cara, regalo])
    rr = VB.stima_ripiego(D, V, buona, _vb())
    assert rr["valore"] - 6500 >= scovo.soglia(6500) > rr["valore"] - 8600 and rr["valore"] - 3000 > 0.40 * rr["valore"]
    k = "unione:" + rr["fascia"]
    assert st["privati"] == 3 and st["privati_senza_stima_prudente"] == 3
    assert st["ripiego_stimate:" + k] == 3 and st["ripiego_buone:" + k] == 1
    assert st["ripiego_stimate:largo:" + rr["fascia"]] == 3 and st["ripiego_stimate:largo4:" + rr["fascia"]] == 3
    # i conteggi di prima non cambiano: le tre auto hanno la stima di prova con 3 confronti, nessuna quella prudente
    assert st["senza_stima"] == 0 and st["con_stima_D"] == 3 and st["buone_prudente"] == 0


def test_conteggi_di_prima_identici_con_e_senza_ripiego(monkeypatch):
    rnd = random.Random(7)
    righe = []
    for i in range(120):
        venduta = rnd.random() < 0.4
        righe.append(_r(i, rnd.uniform(4000, 16000), status="scomparso" if venduta else "attivo",
                        sel=rnd.choice(("privato", "commerciante")), year=rnd.randint(2010, 2022),
                        km=rnd.randint(20000, 140000), giorni_sparita=rnd.uniform(1, 55) if venduta else None))
    st, prec = _misura(righe)
    vuoto = dict(VB._ripiego_vuoto())
    monkeypatch.setattr(VB, "stima_ripiego", lambda *a, **k: dict(vuoto))
    st0, prec0 = _misura(righe)
    nuove = ("ripiego_", "privati_senza_stima_prudente")
    assert {k: v for k, v in st.items() if not k.startswith(nuove)} == \
           {k: v for k, v in st0.items() if not k.startswith(nuove)}
    assert {k: v for k, v in prec.items() if not k.startswith("ripiego|")} == dict(prec0)
    assert any(k.startswith("ripiego|") for k in prec)
