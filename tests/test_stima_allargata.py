"""25/11 (ramo confronti-ombra): stima allargata per le auto dati_insufficienti:pochi_confronti
(pricing/stima_allargata.py), misura CONFRONTI_OMBRA, ricerche mirate in tutta Italia (confronti_mirati.cerca_italia).
Niente database: basi finte, elenco del sito finto."""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
from collections import Counter
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder import regole as RG                         # noqa: E402
from dealfinder.pricing import base_vendute as BV           # noqa: E402
from dealfinder.pricing import confronti_mirati as CM       # noqa: E402
from dealfinder.pricing import policy_v2 as P               # noqa: E402
from dealfinder.pricing import stima_allargata as SA        # noqa: E402
from dealfinder.pricing import valore_base as VB            # noqa: E402
from dealfinder.web import scovo                            # noqa: E402
from dealfinder.web import sito_regole as SR                # noqa: E402


def _r(i, prezzo, status="attivo", sel="commerciante", year=2018, km=60000, **kw):
    r = dict(id=i, make="Fiat", model="Panda", fuel="benzina", gearbox="manuale", power_kw=51.0, year=year,
             mileage_km=km, price_eur=float(prezzo), price_flags=None, seller_type=sel, status=status, province="MI",
             telefono_ok=True, danno="nessuno", problema=False, dichiarato=False, stage="", giorni_visto=2.0,
             giorni_sparita=10.0 if status == "scomparso" else None, giorni_online=20.0)
    r.update(kw)
    return r


def _t(prezzo, **kw):
    t = _r(999, prezzo, sel="privato", damage_class="nessuno", stage_reason="", telefono="3", **kw)
    return t


def _vb():
    return RG.regole().valore_base


def _basi(n=4, d0=9000, v0=8400):
    """n commercianti e n vendute simili: nessuna base arriva a 5 da sola (pochi_confronti:solo_unendo, sotto 10.000 €
    la stima di ripiego è spenta)."""
    D = [_r(i, d0 + 200 * i) for i in range(1, n + 1)]
    V = [_r(10 + i, v0 + 200 * i, status="scomparso", sel="privato") for i in range(1, n + 1)]
    return D, V


def test_interruttori_di_default_e_impronta():
    assert SA.OMBRA is True and SA.MODO_REALE is True           # 25/11: modo reale acceso su decisione del cliente
    assert SA.ETICHETTA == "stima allargata" and SA.AVVISO == "prezzo stimato con meno confronti"
    assert "allargata" not in json.dumps(RG.come_dict())        # nessun parametro nelle regole (impronta invariata)
    assert [p.nome for p in SA.PROVE] == ["zona", "zona_larga", "italia"]
    assert SA.KM_CONFRONTI_MAX == CM.ITALIA_KM_MAX == 400000 and RG.regole().ammissione.km_max == 150000


def test_zona_sotto_10k_prudente_mai_sopra_mediana_ne_ripiego():
    D, V = _basi()
    t = _r(999, 6000, sel="privato")
    assert VB.stima_prudente(D, V, t, _vb())["valore"] is None
    rr = SA.stima(D, V, t, _vb())
    assert rr["prova"] == "zona" and rr["n"] == 8 and rr["fascia"] == "5-10k"
    rip = VB.stima_ripiego(D, V, t, _vb())                       # stessa base, 40° percentile, senza prudenza in più
    assert rr["valore"] < rip["valore"] <= rip["mediana"] and rr["grezzo"] <= rr["mediana"]
    assert rr["valore"] <= rr["grezzo"] * (1 - rr["sconto_base"]) * (1 - 0.03) + 1e-6
    assert rr["riferimento"] > rr["valore"]


def test_pesi_distanza_e_km():
    t = _r(999, 6000, sel="privato")
    assert abs(SA.peso(_r(1, 9000), t) - 1.0) < 1e-9
    lontano = SA.peso(_r(1, 9000, province="NA"), t)
    vicino = SA.peso(_r(1, 9000, province="BG"), t)
    assert lontano < 0.2 < vicino < 1.0
    assert abs(SA.peso(_r(1, 9000, km=110000), t) - 0.5) < 1e-9          # 50.000 km di differenza: metà peso
    assert SA.peso(_r(1, 9000, province=None), t) == 0.25
    # percentile pesato: i confronti con più peso tirano il valore dalla loro parte
    assert SA.percentile_pesato([(100, 1), (200, 1), (300, 1)], 0.5) == 200
    assert SA.percentile_pesato([(100, 5), (200, 1), (300, 1)], 0.5) < 150
    assert SA.percentile_pesato([(100, 1), (200, 1), (300, 5)], 0.5) > 250


def test_i_pesi_possono_solo_abbassare():
    # i confronti VICINI sono i più cari: il percentile pesato salirebbe, la stima resta su quello non pesato
    D = [_r(i, 12000 + 100 * i) for i in range(1, 5)]
    V = [_r(10 + i, 9000 + 100 * i, status="scomparso", sel="privato", province="NA") for i in range(1, 5)]
    t = _r(999, 6000, sel="privato")
    rr = SA.stima(D, V, t, _vb(), solo="zona")
    prezzi = [float(c["price_eur"]) for c in D + V]
    assert rr["grezzo"] <= BV.percentile(prezzi, 0.35) + 1e-6
    # confronti tutti lontanissimi: peso totale sotto PESO_MIN, nessuna stima
    Dl = [dict(c, province="PA") for c in D]
    Vl = [dict(c, province="PA") for c in V]
    assert SA.stima(Dl, Vl, t, _vb(), solo="zona")["valore"] is None


def test_italia_km_fino_a_400k_normalizzati_con_tetto():
    t = _r(999, 6000, sel="privato", km=100000)
    it = {"D": [_r(i, 8000 + 100 * i, province=p, km=k) for i, (p, k) in enumerate(
              [("TO", 120000), ("VR", 180000), ("BO", 250000), ("FI", 90000), ("GE", 140000)], start=1)],
          "V": [_r(20 + i, 7800 + 100 * i, status="scomparso", sel="privato", province=p, km=k) for i, (p, k) in enumerate(
              [("PD", 110000), ("BS", 95000), ("MI", 130000), ("BG", 105000)], start=1)]}
    assert SA.stima([], [], t, _vb())["valore"] is None                   # senza base d'Italia: niente
    chiamate = []
    rr = SA.stima([], [], t, _vb(), italia=lambda: chiamate.append(1) or it)
    assert rr["prova"] == "italia" and rr["n"] == 9 and rr["km"] is None and chiamate == [1]
    assert rr["valore"] <= rr["grezzo"] <= rr["mediana"]
    # correzione dei km: la stessa di BV._adatta, ma verso l'alto mai oltre +25%
    c = _r(1, 8000, km=120000)
    assert abs(SA._adatta_italia(c, t) - BV._adatta(c, t)) < 1e-9
    c = _r(1, 8000, km=400000)
    assert BV._adatta(c, t) > 8000 * 1.25 and abs(SA._adatta_italia(c, t) - 8000 * 1.25) < 1e-6
    assert not SA._simile_italia(_r(1, 8000, km=400001), t, 2) and SA._simile_italia(c, t, 2)
    assert not SA._simile_italia(_r(1, 8000, year=2015), t, 2)
    prov = SA.province_italia()
    assert "NA" in prov and "MI" in prov and not (set(prov) & set(SR.ROMA))


def test_tetto_di_dispersione_e_auto_fragili():
    D = [_r(i, p) for i, p in enumerate((7000, 7200, 9000, 12000), start=1)]
    V = [_r(10 + i, p, status="scomparso", sel="privato") for i, p in enumerate((6900, 7100, 11800, 12200), start=1)]
    t = _r(999, 5000, sel="privato")
    rr = SA.stima(D, V, t, _vb())
    assert rr["valore"] is None and rr["motivo"] == "dispersione"
    # auto economica (valore sotto 5.000 €): servono 8 confronti, con 6 niente
    D, V = _basi(3, d0=4000, v0=3800)
    assert SA.stima(D, V, _r(999, 2500, sel="privato"), _vb())["valore"] is None
    D, V = _basi(4, d0=4000, v0=3800)
    assert SA.stima(D, V, _r(999, 2500, sel="privato"), _vb())["n"] == 8


def test_valuta_candidato_sotto_soglia_e_antitruffa():
    D, V = _basi()
    base = {"D": D, "V": V}
    R = int(round(SA.stima(D, V, _r(999, 1, sel="privato"), _vb())["valore"]))
    sg = scovo.soglia(R - 2500)
    v, c = SA.valuta(_t(R - 2500), base, sg)
    assert c["esito"] == "entra" and v["categoria"] == "candidato" and v["serve_controllo_ai"] is True
    assert v["M_fonte"] == "allargata" and v["metodo"] == "base_vendute" and v["margine"] == 2500 >= sg
    assert v["etichetta_stima"] == "stima allargata" and "da_trattare" not in v and "stima_ripiego" not in v
    assert c["R"] == R and c["n"] == 8 and c["prova"] == "zona" and c["da"] == "pochi_confronti:solo_unendo"
    v, c = SA.valuta(_t(R - 300), base, scovo.soglia(R - 300))
    assert c["esito"] == "sotto_soglia" and v["categoria"] == "non_conveniente"
    # prezzo sotto il 60% del valore di riferimento: resta fuori (l'anti-truffa non si alleggerisce)
    v, c = SA.valuta(_t(int(R * 0.5)), base, scovo.soglia(int(R * 0.5)))
    assert v is None and c["esito"] == "margine_oltre_max"
    v, c = SA.valuta(_t(R - 2500), {"D": D[:1], "V": V[:1]}, sg)
    assert v is None and c["esito"] == "nessuna_stima:pochi_confronti"


def test_ombra_la_valutazione_resta_identica_e_porta_i_numeri(monkeypatch):
    monkeypatch.setattr(SA, "MODO_REALE", False)
    D, V = _basi()
    base = {"D": D, "V": V}
    t = _t(6000)
    sg = scovo.soglia(6000)
    v0 = VB.valuta(dict(t), base, sg)
    assert v0["categoria"] == "dati_insufficienti" and v0["motivo_dati"] == "pochi_confronti:solo_unendo"
    st = Counter()
    v1 = SA.dopo_valutazione(copy.deepcopy(v0), dict(t), base, sg, stats=st)
    sa = v1.pop("stima_allargata")
    assert v1 == v0 and sa["ombra"] is True and sa["esito"] == "entra" and sa["margine"] == sa["R"] - 6000
    assert st["allargata:entra"] == 1 and "stima_allargata" in P.riassunto(dict(v1, stima_allargata=sa))
    # le protezioni non si toccano: anti-truffa, ripiego scartato, auto non pulita, altri motivi
    for extra in ({"motivo_dati": "antitruffa:margine_oltre_55"}, {"ripiego_scartato": {"motivo": "x"}},
                  {"motivo_dati": "auto_non_pulita"}, {"motivo_dati": "dati_auto_incompleti"}):
        vx = dict(copy.deepcopy(v0), **extra)
        assert SA.dopo_valutazione(copy.deepcopy(vx), dict(t), base, sg) == vx


def test_auto_con_abbastanza_confronti_valutate_come_oggi(monkeypatch):
    """Cliente 25/11: il confronto allargato vale SOLO per le auto altrimenti dati_insufficienti. Un'auto col valore
    normale non cambia in nessun campo (ombra e modo reale) e non fa leggere la base d'Italia."""
    D = [_r(i, 9000 + 100 * i) for i in range(1, 9)]
    base = {"D": D, "V": []}
    lontani = {"D": [_r(100 + i, 5000, province="NA", km=300000) for i in range(20)], "V": []}
    lette = []
    for reale in (False, True):
        monkeypatch.setattr(SA, "MODO_REALE", reale)
        for lid, prezzo in ((999, 6000), (1000, 6000), (1001, 9300)):      # 1000: nel campione della precisione
            t = dict(_t(prezzo), id=lid)
            sg = scovo.soglia(prezzo)
            v0 = VB.valuta(dict(t), base, sg)
            assert v0["categoria"] in ("opportunita", "non_conveniente") and v0["M_fonte"] == "commercianti"
            v1 = SA.dopo_valutazione(copy.deepcopy(v0), dict(t), base, sg,
                                     italia=lambda: lette.append(1) or lontani, italia_pronta=lambda: lontani)
            prova = v1.pop("allargata_prova", None)
            assert v1 == v0 and "stima_allargata" not in v1
            assert (prova is not None) == (lid == 1000)
            if prova:
                assert set(prova) <= {"zona", "zona_larga", "italia"} and all(x <= 1.0 for x in prova.values())
    assert lette == []
    # VB.valuta non conosce la stima allargata: senza `stato` niente cambia
    Dp, Vp = _basi()
    assert VB.valuta(_t(6000), {"D": Dp, "V": Vp}, 1500)["categoria"] == "dati_insufficienti"


def test_modo_reale_candidato_al_posto_del_dati_insufficienti(monkeypatch):
    D, V = _basi()
    base = {"D": D, "V": V}
    t = _t(6000)
    sg = scovo.soglia(6000)
    v0 = VB.valuta(dict(t), base, sg)
    monkeypatch.setattr(SA, "MODO_REALE", True)
    v = SA.dopo_valutazione(copy.deepcopy(v0), dict(t), base, sg)
    assert v["categoria"] == "candidato" and v["stima_allargata"]["ombra"] is False and v["serve_controllo_ai"]
    pv = P.riassunto(v)
    assert pv["etichetta_stima"] == "stima allargata" and pv["avviso_stima"] == "prezzo stimato con meno confronti"
    it = {"id": 1, "pv": pv, "prezzo": 6000, "_r": {}}
    assert SR.r_valutata(it, SR.Regole()) is None and it["stima_allargata"] is True and it["stima_ripiego"] is True
    # fascia non ammessa o interruttore spento dopo: la valutazione già scritta non vale più
    monkeypatch.setattr(SA, "FASCE_REALI", (">20k",))
    assert SR.r_valutata({"id": 1, "pv": pv, "prezzo": 6000, "_r": {}}, SR.Regole()) == "dati_insufficienti"
    assert SA.dopo_valutazione(copy.deepcopy(v0), dict(t), base, sg)["categoria"] == "dati_insufficienti"
    monkeypatch.setattr(SA, "FASCE_REALI", None)
    monkeypatch.setattr(SA, "MODO_REALE", False)
    assert SR.r_valutata({"id": 1, "pv": pv, "prezzo": 6000, "_r": {}}, SR.Regole()) == "dati_insufficienti"
    # un errore nel calcolo non tocca mai la valutazione
    monkeypatch.setattr(SA, "valuta", lambda *a, **k: 1 / 0)
    assert SA.dopo_valutazione(copy.deepcopy(v0), dict(t), base, sg) == v0


def _auto_sito(i, prezzo=5000, margine=3000, prov="MI", tel=True, seller="privato", jev=None, **kw):
    r = {"tel": tel, "seller_type": seller, "provincia": prov, "jev": jev}
    it = {"id": i, "prezzo": prezzo, "anno": 2015, "km": 90000, "carb": "Benzina", "fonte": "Subito",
          "pv": {"categoria": "opportunita", "M": prezzo + margine, "R": prezzo + margine,
                 "R_finale": prezzo + margine, "margine": margine, "totale_costi": 0, "n": 10, "calibrato": 1.0,
                 "metodo": "base_vendute"}, "_r": r}
    it.update(kw)
    return it


def _insuff(i, prezzo=6000, R=9000, esito="entra", **kw):
    it = _auto_sito(i, prezzo=prezzo, **kw)
    sa = {"regola": SA.REGOLA, "ombra": True, "esito": esito}
    if R:
        sa.update(prova="zona", n=7, fascia="5-10k", R=R, R_finale=R, margine=R - prezzo, soglia=scovo.soglia(prezzo))
    it["pv"] = {"categoria": "dati_insufficienti", "metodo": "base_vendute",
                "motivo_dati": "pochi_confronti:solo_unendo", "stima_allargata": sa, "calibrato": 1.0}
    return it


class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.righe = []

    def emit(self, record):
        self.righe.append(record.getMessage())


def test_confronti_ombra_conta_senza_cambiare_il_sito(monkeypatch):
    monkeypatch.setattr(SA, "MODO_REALE", False)
    g = SR.Regole()
    pulito = {"fonte": "claude", "decisione": "tieni", "confidenza": 0.9, "truffa": 0.0}
    items = [_auto_sito(1), _auto_sito(2, _prova=1),
             _insuff(10, jev=pulito),                              # entrerebbe
             _insuff(11),                                          # aspetta il controllo AI
             _insuff(12, jev=pulito, tel=False),                   # senza numero: corsia chat, fuori (ha l'avviso)
             _insuff(13, jev=pulito, prov="RM"),                   # Roma: mai
             _insuff(14, jev=pulito, km=200000),                   # oltre 150.000 km: fuori come oggi
             _insuff(15, R=6300, esito="sotto_soglia"),            # margine sotto SOGLIE_PV
             _insuff(16, R=None, esito="nessuna_stima:pochi_confronti"),
             _insuff(17, jev=pulito, seller="commerciante", prov="MI")]
    items[1]["pv"]["allargata_prova"] = {"zona": 0.93, "italia": 0.88}
    items[9]["pv"].pop("stima_allargata")                          # non ancora rivalutata
    items[9]["pv"]["motivo_dati"] = "antitruffa:margine_oltre_55"
    assert SA.copie_per_ombra(items, forza=True) is not None and SA.copie_per_ombra(items) is None    # ogni 10 minuti
    copie = SA.copie_per_ombra(items, forza=True)
    prima = copy.deepcopy(items)
    out, _fun = SR.elenco_sito(items, g, scrivi_log=False)
    assert [it["id"] for it in out] == [1, 2]                       # il sito vero: nessuna auto in più
    h = _Log()
    logging.getLogger("scovo").addHandler(h)
    try:
        riga = SA.ombra_sito(copie, [it["id"] for it in out], g)
    finally:
        logging.getLogger("scovo").removeHandler(h)
    assert [it["pv"] for it in prima[2:]] == [it["pv"] for it in items[2:]]
    testo = [r for r in h.righe if r.startswith("CONFRONTI_OMBRA ")]
    assert len(testo) == 1 and json.loads(testo[0].split(" ", 1)[1])["totale"] == 8
    assert riga["modo"] == "ombra" and riga["totale"] == 8 and riga["con_prezzo"] == 6 and riga["sopra_soglia"] == 5
    assert riga["entrerebbero"] == 1 and riga["aspettano_controllo_ai"] == 1 and riga["uscirebbero_dal_sito"] == 0
    assert riga["esiti"] == {"entra": 5, "sotto_soglia": 1, "nessuna_stima": 1, "non_ammessa": 1}
    assert riga["fuori_per_regola"] == {"aspetta_controllo_ai": 1, "chat_subito": 1, "no_roma": 1, "km": 1}
    assert riga["per_fascia"]["entrerebbero"] == {"5-10k": 1} and riga["per_venditore"]["entrerebbero"] == {"privato": 1}
    assert riga["motivi_oggi"] == {"pochi_confronti:solo_unendo": 7, "antitruffa:margine_oltre_55": 1}
    assert riga["precisione"]["zona"]["errore_mediano_pct"] == 7.0 and riga["precisione"]["italia"]["n"] == 1
    es = riga["esempi"]
    assert es == [{"id": 10, "prezzo_stimato": 9000, "prezzo_annuncio": 6000, "margine": 3000, "confronti": 7,
                   "prova": "zona", "venditore": "privato", "etichetta": "stima allargata"}]
    assert "tel" not in testo[0] and "telefono\"" not in testo[0].replace('"telefono": 1', "")


def test_ricerche_italia_gruppi_cache_24_ore_e_tetto():
    righe = [dict(id=1, make="Fiat", model="Panda", fuel="benzina", year=2018),
             dict(id=2, make="fiat", model="panda", fuel="benzina", year=2014),
             dict(id=3, make="Fiat", model="Tipo", fuel="diesel", year=2019),
             dict(id=4, make="Fiat", model="500", fuel="benzina", year=2017)]
    g = CM.gruppi_italia(righe)
    assert [(x["model"], x["ids"], x["anno_da"], x["anno_a"], x["zona"]) for x in g] == \
           [("Panda", [1, 2], 2012, 2020, "italia"), ("Tipo", [3], 2017, 2021, "italia"),
            ("500", [4], 2015, 2019, "italia")]
    cercati = {("fiat", "panda", "benzina", "italia"): (3.0, 5), ("fiat", "tipo", "diesel", "italia"): (25.0, 0),
               ("fiat", "500", "benzina", "milano"): (1.0, 5)}       # la ricerca della zona non conta
    assert [x["model"] for x in CM.gruppi_italia(righe, cercati)] == ["Tipo", "500"]
    assert len(CM.gruppi_italia(righe, massimo=2)) == 2 and CM.ITALIA_RIPOSO_ORE == 24
    # tutta Italia: nessuna regione su Subito, nessun raggio su AutoScout24; la zona resta com'era
    gi, gz = g[0], dict(g[0], zona="milano")
    assert "r" not in CM.parametri_subito(gi, None, 0) and CM.parametri_subito(gz, 4, 0)["r"] == 4
    assert "zip" not in CM.url_autoscout("B", "fiat/panda", gi, 1) and "custtype=D" in CM.url_autoscout("B", "x", gi, 1)
    assert "zip=20121&zipr=100" in CM.url_autoscout("B", "fiat/panda", gz, 1)


def test_cerca_italia_tetti_blocchi_e_rivalutazione(monkeypatch):
    righe = [dict(id=i, make="Fiat", model=f"M{i}", fuel="benzina", year=2018) for i in range(1, 6)]
    segnati, chiamate = [], []
    monkeypatch.setattr(CM, "gruppi_cercati", lambda conn: {})
    monkeypatch.setattr(CM, "segna_gruppo", lambda conn, g, t, s: segnati.append((g["model"], g["zona"], t, s)))
    monkeypatch.setattr(CM, "ITALIA_GRUPPI", 4)

    def sub(g, n, st):
        chiamate.append(("subito", g["model"], n))
        st["pagine"] += 1
        if g["model"] == "M3":
            st["subito:bloccato"] = 1                 # 403/429: la fonte non si chiama più
            return []
        return ["a", "b"] if g["model"] == "M1" else []

    def asc(g, n, st):
        chiamate.append(("autoscout24", g["model"], n))
        st["pagine"] += 1
        return []

    st = Counter()
    ids = CM.cerca_italia(None, righe, (("subito", sub), ("autoscout24", asc)), st,
                          salva_fn=lambda conn, trovati, g, st: set(trovati))
    assert ids == {1}                                  # si rivalutano solo le auto dei modelli con confronti salvati
    assert st["italia_gruppi"] == 4 and st["italia_gruppi_cercati"] == 4 and st["italia_pagine"] == 7
    assert [c for c in chiamate if c[0] == "subito"] == [("subito", "M1", 1), ("subito", "M2", 1), ("subito", "M3", 1)]
    assert segnati[0] == ("M1", "italia", 2, 2) and all(z == "italia" for _m, z, _t, _s in segnati)
    # tempo finito: nessuna ricerca
    st = Counter()
    assert CM.cerca_italia(None, righe, (("subito", sub),), st, fermo=lambda: True) == set() and st["italia_fermata"]


def test_salva_italia_solo_confronto_fuori_zona(monkeypatch):
    import dealfinder.db as DB
    import dealfinder.core.dal_testo as DT
    import dealfinder.core.normalize as NZ
    monkeypatch.setattr(DT, "completa", lambda d, titolo, testo: {})
    monkeypatch.setattr(NZ, "normalize_fields", lambda l: None)
    salvati = []

    def upsert(conn, l):
        salvati.append(l.province)
        return len(salvati), "nuovo"

    monkeypatch.setattr(DB, "upsert_listing", upsert)

    class Conn:
        sql = []

        def execute(self, q, par=None):
            self.sql.append(q)

        def commit(self):
            pass

        def rollback(self):
            pass

    def ann(prov, km=100000, year=2018, model="Panda", fuel="benzina", city=None):
        return SimpleNamespace(make="Fiat", model=model, fuel=fuel, year=year, mileage_km=km, gearbox=None,
                               power_kw=None, version_raw=None, title="", description="", province=prov, city=city,
                               region=None, price_eur=8000, source="subito", seller_type="privato")

    g = {"make": "Fiat", "model": "Panda", "fuel": "benzina", "zona": "italia", "anno_da": 2016, "anno_a": 2020}
    st = Counter()
    conn = Conn()
    ids = CM.salva_italia(conn, [ann("NA", km=390000), ann("RM"), ann("BA", km=400001), ann("FI", model="Tipo"),
                                 ann("TO", year=2010), ann(None), ann("PA", fuel="diesel")], g, st)
    assert salvati == ["NA"] and ids == {1} and st["italia_confronti_salvati"] == 1
    assert st["italia_scarto:provincia"] == 2 and st["italia_scarto:anno_km_prezzo"] == 2
    assert st["italia_scarto:altro_modello"] == 2
    assert any("stage='mercato', stage_reason='mercato_nord'" in q for q in conn.sql)       # mai proposta sul sito


def test_simulazione_sintetica_mai_sopra_il_valore_normale():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    import simula_allargata as SIM
    ris = SIM.simula(seme=1, modelli=40)
    assert set(ris) == {"zona", "zona_larga", "italia"}
    for prova, m in ris.items():
        assert m["n"] > 50 and m["scostamento_mediano_pct"] < 0, prova      # sempre dalla parte prudente
        assert m["oltre_5pct_sopra_pct"] <= 1.0 and m["sopra_il_normale_pct"] <= 5.0, prova
