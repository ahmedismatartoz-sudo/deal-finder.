"""Casi limite della policy di valutazione pv2."""
from datetime import datetime, timedelta, timezone

from dealfinder.pricing.policy_v2 import (PolicyConfig, percentile, riduzione, rivendita, valuta, seleziona)

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
CFG = PolicyConfig()


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
    assert riduzione(4999, CFG)[0] == .30
    assert riduzione(5000, CFG)[0] == .25
    assert riduzione(9999, CFG)[0] == .25 and riduzione(10000, CFG)[0] == .20
    assert riduzione(30000, CFG)[0] == .10 and riduzione(50000, CFG)[0] == .10
    assert riduzione(50001, CFG)[0] is None


def test_esempio_documento():
    assert rivendita(12000, 10500, CFG)["R"] == 9600
    assert rivendita(12000, 9000, CFG)["R"] == 9000


def test_transizione_mai_sopra_la_regola_piu_severa():
    cfg = PolicyConfig(transizione=True, transizione_w=1000)
    prev = 0
    for m in range(4000, 52000, 50):
        r = rivendita(m, 10**9, cfg)
        if r["R"] is None:
            break
        base = rivendita(m, 10**9, CFG)["R"]
        assert r["R"] <= base + 1                         # mai sopra la tabella base
        assert r["R"] >= prev - 1                         # continua e crescente
        prev = r["R"]
    assert rivendita(10000, 10**9, cfg)["R"] == 7500       # al confine vale la fascia più severa


def test_base_preferita_opportunita():
    v = valuta(TARGET, market(BASE), soglia=1500, cfg=CFG, now=NOW)
    assert v["n"] == 12 and v["campione"] == "base preferita"
    assert v["M"] == 9900 and v["L"] == 9475
    assert v["riduzione"] == .25 and v["R"] == 7425        # M ridotto 7.425 < L
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
    v = valuta(TARGET, market(BASE[:4]), 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti" and "R" not in v
    v = valuta(car(1, 4000), market(BASE[:7]), 1500, CFG, NOW)
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
    lontani = [car(300 + n, 9500, year=2021, km=140000 + n) for n in range(12)]
    v = valuta(TARGET, lontani, 1500, CFG, NOW)
    assert v["n"] == 0 and v["categoria"] == "dati_insufficienti"
    medi = [car(300 + n, 9500 + n * 50, year=2020, km=110000 + n * 100) for n in range(12)]
    v = valuta(TARGET, medi, 1000, CFG, NOW)
    assert v["livello"] == 2 and any("ampliati" in i for i in v["ipotesi"])
    assert v["categoria"] != "opportunita"


def test_costo_mancante_blocca():
    t = car(1, 3000, damage_class="medio", parts_cost_high=None)
    v = valuta(t, market(BASE), 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti"
    assert any("ricambi" in m for m in v["motivi"])


def test_incidentata_non_vale_come_sana():
    sana = valuta(car(1, 3000), market(BASE), 1500, CFG, NOW)
    inc = valuta(car(1, 3000, damage_class="leggero", parts_cost_low=300, parts_cost_high=500), market(BASE), 1500, CFG, NOW)
    assert inc["R_finale"] < sana["R_finale"]
    assert inc["costi"]["manodopera"]["euro"] > 0
    assert inc["categoria"] in ("candidato", "non_conveniente")
    # gli annunci di auto incidentate non entrano nei confronti
    m = market(BASE) + [car(700 + n, 2000, damage_class="grave") for n in range(5)]
    assert all(c["prezzo"] != 2000 for c in valuta(car(1, 3000), m, 1500, CFG, NOW)["confronti"])


def test_alto_rischio_sempre_da_approfondire():
    t = car(1, 1500, damage_class="alto_rischio", parts_cost_low=100, parts_cost_high=200)
    v = valuta(t, market(BASE), 500, CFG, NOW)
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
