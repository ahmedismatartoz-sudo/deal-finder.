"""Casi limite della policy di valutazione pv2."""
from datetime import datetime, timedelta, timezone

from dealfinder.pricing.policy_v2 import (PolicyConfig, percentile, riduzione, rivendita, valuta, seleziona)

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
CFG = PolicyConfig()
CFG_DANNI = PolicyConfig(solo_sane=False)


def car(i, price, year=2018, km=90000, fuel="diesel", gearbox="manuale", kw=81, **kw2):
    d = {"id": i, "url": f"https://x/{i}", "source": "subito", "make": "volkswagen", "model": "golf",
         "year": year, "mileage_km": km, "fuel": fuel, "gearbox": gearbox, "power_kw": kw, "price_eur": price,
         "price_flags": [], "status": "attivo", "seller_type": "privato", "damage_class": "sconosciuto",
         "last_checked_at": NOW - timedelta(days=1), "first_seen_at": NOW - timedelta(days=40), "vehicle_id": 1000 + i}
    d.update(kw2)
    return d


def market(prices, **kw):
    return [car(100 + n, p, km=85000 + n * 900, **kw) for n, p in enumerate(prices)]


TARGET = car(1, 6000)
BASE = [9000, 9200, 9400, 9500, 9600, 9800, 10000, 10100, 10300, 10500, 10800, 11000]


def test_percentile_tipo7():
    assert percentile([1, 2, 3, 4], .5) == 2.5
    assert percentile([1, 2, 3, 4], .25) == 1.75          # (n-1)*q = 0.75 -> 1 + .75*(2-1)
    assert percentile([10], .25) == 10


def test_fasce_e_confini():
    assert riduzione(4999, CFG)[0] == .12 and riduzione(9999, CFG)[0] == .12 and riduzione(50000, CFG)[0] == .12
    assert riduzione(50001, CFG)[0] is None

def test_esempio_documento():
    assert rivendita(12000, 10500, CFG)["R"] == 10560          # 12.000 × 0,88 (L non taglia più)
    cfg = PolicyConfig(usa_L=True)
    assert rivendita(12000, 9000, cfg)["R"] == 9000             # regola del documento, se riattivata

def test_transizione_mai_sopra_la_regola_piu_severa():
    cfg = PolicyConfig(transizione=True, transizione_w=1000, usa_L=True)
    cfg.fasce = [(5000, .20), (10000, .17), (50000, .15)]
    base_cfg = PolicyConfig(usa_L=True)
    base_cfg.fasce = cfg.fasce
    prev = 0
    for m in range(4000, 50000, 50):
        r = rivendita(m, 10**9, cfg)["R"]
        assert r <= rivendita(m, 10**9, base_cfg)["R"] + 1 and r >= prev - 1
        prev = r

def test_base_preferita_opportunita():
    v = valuta(TARGET, market(BASE), soglia=1500, cfg=CFG, now=NOW)
    assert v["n"] == 12 and v["campione"] == "base preferita"
    assert 9700 <= v["M"] <= 9900 and 9300 <= v["L"] <= 9475   # corretti per km (confronti con meno km)
    assert v["riduzione"] == .12 and v["R"] == round(v["M"] * .88)
    assert v["categoria"] in ("opportunita", "non_conveniente")
    assert v["margine"] == v["R_finale"] - 6000 - v["totale_costi"] - v["riserva"]["euro"]


def test_confronti_molto_cari_non_alzano_M():
    v1 = valuta(TARGET, market(BASE), 1500, CFG, NOW)
    v2 = valuta(TARGET, market(BASE + [19000, 19500, 21000]), 1500, CFG, NOW)
    assert v2["M"] - v1["M"] < 700                          # mediana, non media
    assert v2["L"] - v1["L"] < 400


def test_singolo_prezzo_basso_resta_ma_non_domina():
    v = valuta(TARGET, market(BASE + [7000]), 1500, CFG, NOW)
    assert any(c["prezzo"] == 7000 for c in v["confronti"])  # plausibile: non si elimina
    v2 = valuta(TARGET, market(BASE + [300]), 1500, CFG, NOW)
    assert all(c["prezzo"] != 300 for c in v2["confronti"])
    assert any("simbolico" in e["motivo"] for e in v2["esclusi"])


def test_doppioni_contati_una_volta():
    m = market(BASE[:6])
    dup = dict(m[0], id=999, source="autoscout24", vehicle_id=None, price_eur=m[0]["price_eur"] + 100)
    dup_sito = dict(m[1], id=998, stage_reason="doppione", vehicle_id=None)
    sel = seleziona(TARGET, m + [dup, dup_sito], CFG, NOW)
    assert len(sel["validi"]) == 6
    assert sum("doppione" in e["motivo"] for e in sel["esclusi"]) == 2


def test_auto_stessa_esclusa():
    m = market(BASE)
    sel = seleziona(TARGET, m + [TARGET], CFG, NOW)
    assert all(c["id"] != 1 for c in sel["validi"])


def test_rate_e_finanziamento():
    m = market(BASE[:8]) + [car(500, 2500, price_flags=["leasing_o_rata"]),
                            car(501, 7900, finanziamento=True), car(502, 8000, price_flags=["importazione"])]
    sel = seleziona(TARGET, m, CFG, NOW)
    motivi = " ".join(e["motivo"] for e in sel["esclusi"])
    assert "rata" in motivi and "finanziamento" in motivi and "importare" in motivi
    assert len(sel["validi"]) == 8


def test_pochi_confronti():
    v = valuta(TARGET, market(BASE[:1]), 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti" and "R" not in v
    v = valuta(car(1, 5600), market(BASE[:3]), 1500, CFG, NOW)
    assert v["campione"].startswith("provvisoria")
    assert v["categoria"] in ("candidato", "non_conveniente")


def test_motori_cambi_generazioni_diversi():
    m = (market(BASE[:6]) + market([15000] * 4, kw=110) + market([12000] * 4, gearbox="automatico")
         + market([20000] * 4, fuel="benzina"))
    for c in m[6:]:
        c["id"] += 50
        c["vehicle_id"] += 50
    sel = seleziona(TARGET, m, CFG, NOW)
    assert len(sel["validi"]) == 6
    t = dict(TARGET, vehicle_identity={"generation": "mk7"})
    m2 = [dict(c, vehicle_identity={"generation": "mk8"}) for c in market(BASE)]
    assert seleziona(t, m2, CFG, NOW)["validi"] == []


def test_anno_e_km_stretti_poi_ampliamento_esplicito():
    lontani = [car(300 + n, 9500, year=2023, km=160000 + n) for n in range(12)]
    v = valuta(TARGET, lontani, 1500, CFG, NOW)
    assert v["n"] == 0 and v["categoria"] == "dati_insufficienti"
    medi = [car(300 + n, 9500 + n * 50, year=2020, km=110000 + n * 100, gearbox=None) for n in range(12)]
    v = valuta(TARGET, medi, 1000, CFG, NOW)
    assert v["livello"] == 2 and any("ampliati" in i for i in v["ipotesi"])
    assert v["categoria"] != "opportunita"


def test_costo_mancante_blocca():
    t = car(1, 3000, damage_class="medio", parts_cost_high=None)
    v = valuta(t, market(BASE), 1500, CFG_DANNI, NOW)
    assert v["categoria"] == "dati_insufficienti"
    assert any("ricambi" in m for m in v["motivi"])


def test_incidentata_non_vale_come_sana():
    sana = valuta(car(1, 5600), market(BASE), 1500, CFG_DANNI, NOW)
    inc = valuta(car(1, 5600, damage_class="leggero", parts_cost_low=300, parts_cost_high=500), market(BASE), 1500, CFG_DANNI, NOW)
    assert inc["R_finale"] < sana["R_finale"]
    assert inc["costi"]["ricambi"]["euro"] == 500 and "manodopera" not in inc["costi"]
    assert inc["categoria"] in ("candidato", "non_conveniente")
    # gli annunci di auto incidentate non entrano nei confronti
    m = market(BASE) + [car(700 + n, 2000, damage_class="grave") for n in range(5)]
    assert all(c["prezzo"] != 2000 for c in valuta(car(1, 3000), m, 1500, CFG_DANNI, NOW)["confronti"])


def test_alto_rischio_sempre_da_approfondire():
    t = car(1, 5600, damage_class="alto_rischio", parts_cost_low=100, parts_cost_high=200)
    v = valuta(t, market(BASE), 500, CFG_DANNI, NOW)
    assert v["categoria"] in ("candidato", "non_conveniente")


def test_oltre_50mila():
    m = market([60000 + n * 500 for n in range(12)])
    v = valuta(car(1, 40000), m, 2000, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti" and v.get("R") is None


def test_annunci_vecchi_o_spariti():
    m = market(BASE[:6]) + [car(800, 9000, status="scomparso"),
                            car(801, 9000, last_checked_at=NOW - timedelta(days=40))]
    sel = seleziona(TARGET, m, CFG, NOW)
    assert len(sel["validi"]) == 6
    # annuncio pubblicato da tanto ma ricontrollato ieri: resta valido
    vecchio = car(802, 9700, first_seen_at=NOW - timedelta(days=200), km=99000)
    assert any(c["id"] == 802 for c in seleziona(TARGET, m + [vecchio], CFG, NOW)["validi"])


def test_soglia_non_abbassata():
    v = valuta(TARGET, market(BASE), soglia=5000, cfg=CFG, now=NOW)
    assert v["soglia"] == 5000 and v["categoria"] == "non_conveniente"


def test_troppo_bello_mai_sul_sito():
    v = valuta(car(1, 3000), market(BASE), 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti" and any("truffa" in m for m in v["motivi"])


def test_fascia_piu_severa_tra_prezzo_e_mercato():
    cfg = PolicyConfig()
    cfg.fasce = [(5000, .20), (10000, .17), (50000, .15)]
    v = valuta(car(1, 8700), market([12000 + n * 100 for n in range(12)]), 2200, cfg, NOW)
    assert v["riduzione"] == .17

def test_nessun_costo_margine_uguale_rivendita_meno_prezzo():
    v = valuta(car(1, 6000), market(BASE), 1500, CFG, NOW)
    assert v["totale_costi"] == 0 and v["riserva"]["euro"] == 0
    assert v["margine"] == v["R_finale"] - 6000


def test_solo_auto_sane():
    v = valuta(car(1, 6000, damage_class="leggero", parts_cost_high=300), market(BASE), 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti" and "sane" in v["motivi"][-1]


def test_correzione_anno_km():
    nuove = [car(400 + n, 12000 + n * 100, year=2021, km=40000 + n * 500) for n in range(6)]
    v = valuta(car(1, 6000), nuove, 1500, CFG, NOW)
    assert v["M"] < 12000 * 0.85                       # 3 anni e 50.000 km in più: il prezzo scende
    assert all(c["prezzo_corretto"] < c["prezzo"] for c in v["confronti"])
    assert any("corretti" in i for i in v["ipotesi"])


def test_confronto_senza_anno_o_km():
    m = market(BASE) + [car(900, 9000, year=None), car(901, 9000, km=None)]
    v = valuta(TARGET, m, 1500, CFG, NOW)
    assert v["n"] == 12


def test_correzione_non_gonfia_i_confronti_peggiori():
    vecchie = [car(500 + n, 10000 + n * 100, year=2015, km=160000 + n * 500) for n in range(6)]
    v = valuta(car(1, 9000), vecchie, 1500, CFG, NOW)
    assert all(c["prezzo_corretto"] <= c["prezzo"] for c in v["confronti"])
    assert v["M"] <= 10300


def test_carrozzerie_diverse():
    t = car(1, 9000, version_raw="218i Active Tourer Advantage")
    m = [car(600 + n, 15000, version_raw="218i Coupe M Sport") for n in range(6)] + \
        [car(700 + n, 12000, version_raw="218i Active Tourer") for n in range(3)]
    sel = seleziona(t, m, CFG, NOW)
    assert len(sel["validi"]) == 3 and sel["motivi_non_equivalenti"].get("carrozzeria diversa") == 6


def test_modello_prezzi_preciso_e_piu_auto():
    import random
    rnd = random.Random(3)
    vero = lambda y, km: 15000 * 0.9 ** (2022 - y) * 0.985 ** (km / 10000)
    m = []
    for n in range(40):
        y, km = rnd.randint(2013, 2021), rnd.randint(40, 200) * 1000
        m.append(car(800 + n, int(vero(y, km) * rnd.uniform(.93, 1.07)), year=y, km=km))
    t = car(1, 5000, year=2016, km=125000)
    v = valuta(t, m, 1500, CFG, NOW)
    assert v["metodo"] == "modello"
    assert abs(v["M"] - vero(2016, 125000)) / vero(2016, 125000) < .08
    # auto senza confronti diretti vicini (anno ±3 e km ±50.000): con il modello si valuta lo stesso
    t2 = car(2, 5000, year=2013, km=60000)
    m2 = [c for c in m if abs(c["year"] - 2013) > 3 or abs(c["mileage_km"] - 60000) > 50000] + \
         [car(900 + k, int(vero(2014 + k % 6, 90000 + k * 3000)), year=2014 + k % 6, km=90000 + k * 3000) for k in range(12)]
    v2 = valuta(t2, m2, 1500, CFG, NOW)
    assert v2.get("M") and v2["metodo"] == "modello"
