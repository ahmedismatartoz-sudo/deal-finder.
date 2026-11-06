"""Casi limite della policy di valutazione pv2."""
from datetime import datetime, timedelta, timezone

import os as _os
# 4/11: niente impostazioni d'ambiente: i test girano con le regole di produzione (dealfinder/regole.py)
from dealfinder.pricing.policy_v2 import (PolicyConfig, percentile, valuta, seleziona)

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
CFG = PolicyConfig()
CFG_DANNI = PolicyConfig(solo_sane=False, perdita_storico_danno=.08)


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


def test_base_preferita_opportunita():
    v = valuta(TARGET, market(BASE), soglia=1500, cfg=CFG, now=NOW)
    assert v["n"] == 12 and v["campione"] == "base preferita"
    assert 9700 <= v["M"] <= 9950 and 9300 <= v["L"] <= 9600   # corretti per km nei due sensi
    assert v["riduzione"] == 0 and v["R"] == v["M"]              # 4/11: R = M, nessuna riduzione
    assert v["categoria"] == "opportunita" and v["M_fonte"] == "annunci"
    assert v["margine"] == v["R_finale"] - 6000 - v["totale_costi"]


def _ripristina(nome, prima):
    """3/11: rimette il valore di prima (prima si scriveva "0", e i test dopo giravano con le regole vecchie)."""
    if prima is None:
        _os.environ.pop(nome, None)
    else:
        _os.environ[nome] = prima


def test_rivendita_vera_uguale_al_mercato():
    prima = _os.environ.get("RIVENDITA_VERA")
    _os.environ["RIVENDITA_VERA"] = "0"                  # variabile tolta: non cambia più niente
    try:
        v = valuta(TARGET, market(BASE), soglia=1500, cfg=CFG, now=NOW)
    finally:
        _ripristina("RIVENDITA_VERA", prima)
    assert v["riduzione"] == 0 and v["R"] == round(v["M"]) and v["R_da"] == "mercato vero"
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


def _ampliamento_esplicito():
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
    # 4/11: sopra 50.000 € non più "dati insufficienti" perché finiva la tabella delle riduzioni
    m = market([60000 + n * 500 for n in range(12)])
    v = valuta(car(1, 40000), m, 2000, CFG, NOW)
    assert v.get("R") and v["R"] == v["M"] > 50000 and v["categoria"] == "opportunita"


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


def test_nessun_costo_margine_uguale_rivendita_meno_prezzo():
    v = valuta(car(1, 6000), market(BASE), 1500, CFG, NOW)
    assert v["totale_costi"] == 0 and v["riserva"]["euro"] == 0
    assert v["margine"] == v["R_finale"] - 6000


def test_solo_auto_sane():
    # 4/11 (Incidentate): danno dichiarato con costo stimato -> si valuta col costo tolto (candidato, da verificare)
    v = valuta(car(1, 6000, damage_class="leggero", parts_cost_high=300), market(BASE), 1500, CFG, NOW)
    assert v["categoria"] == "candidato" and v["totale_costi"] == 300 and v["margine"] == v["R"] - 6000 - 300
    # danno senza stima, grave/strutturale o dichiarato e non letto: fuori
    for t in (car(1, 6000, damage_class="leggero", parts_cost_high=None), car(1, 6000, damage_class="grave", parts_cost_high=300),
              car(1, 6000, rischio_strutturale=True), car(1, 6000, damage_declared=True)):
        v = valuta(t, market(BASE), 1500, CFG, NOW)
        assert v["categoria"] == "dati_insufficienti" and "sane" in v["motivi"][-1]
    # difetti letti dall'AI (ai_difetti, non gravi): costo tolto una volta, segnato per il sito
    v = valuta(car(1, 6000, costo_difetti=400, difetti_motivo="frizione"), market(BASE), 1500, CFG, NOW)
    assert v["costi"]["difetti"]["euro"] == 400 and v["costo_difetti_incluso"] and v["categoria"] == "candidato"


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


def test_correzione_nei_due_sensi_con_tetto():
    # 4/11: un confronto più vecchio o con più km SALE (prima solo verso il basso), al massimo +25%
    vecchie = [car(500 + n, 10000 + n * 100, year=2015, km=160000 + n * 500) for n in range(6)]
    v = valuta(car(1, 9000), vecchie, 1500, PolicyConfig(due_sensi=True), NOW)   # 4/11: spenta di base finché non è provata
    assert all(c["prezzo"] < c["prezzo_corretto"] <= c["prezzo"] * 1.25 + 1 for c in v["confronti"])
    assert v["M"] > 10300
    v0 = valuta(car(1, 9000), vecchie, 1500, PolicyConfig(due_sensi=False), NOW)       # regola di prima
    assert all(c["prezzo_corretto"] <= c["prezzo"] for c in v0["confronti"]) and v0["M"] <= 10300


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
    assert v2.get("M")              # 6/11: il modello vale solo se concorda (±10%) con le gemelle, sennò la mediana


def test_modello_scartato_se_lontano_dalle_gemelle():
    """6/11 (BMW 430i 2017): il modello del gruppo (generazioni miste) dà +40%: vale la mediana delle gemelle."""
    m = [car(800 + n, 30000 + n * 50, year=2017 + n % 2, km=80000 + n * 1000) for n in range(12)]
    gem = [car(900 + n, 21000 + n * 100, year=2017, km=85000 + n * 2000) for n in range(3)]
    t = car(1, 18000, year=2017, km=86000)
    from dealfinder.pricing import policy_v2 as P
    orig = P.regressione
    P.regressione = lambda *a, **k: {"prezzo": 31000.0, "n": 15, "errore_tipico": .05, "anno_pct": .08,
                                     "km10k_pct": .01, "potenza": False}
    try:
        v = valuta(t, gem, 1500, CFG, NOW)
    finally:
        P.regressione = orig
    assert v.get("modello_scartato") and v["metodo"] != "modello" and v["M"] < 25000
    assert v["n"] == v["n_gemelle"] == 3


def test_telefono_subito_dietro_il_bottone():
    from dealfinder.pipeline.tempo_reale import telefono_subito
    assert telefono_subito({"advertiser": {"phone": "+39 333 1234567"}})
    # 25/10 21:40: Subito mette advertiser.phone = "0" proprio quando il venditore HA il numero (bottone "Mostra numero")
    assert telefono_subito({"advertiser": {"phone": "1"}}) and telefono_subito({"advertiser": {"phone": "0"}})
    assert not telefono_subito({"advertiser": {"phone": ""}})
    assert not telefono_subito({"advertiser": {}})
    assert not telefono_subito(None)


def test_telefono_da_pagina_subito():
    from dealfinder.pipeline.tempo_reale import telefono_da_pagina
    assert telefono_da_pagina(None, "") is None                                  # pagina bloccata: non si sa
    assert telefono_da_pagina({"props": {"ad": {"advertiser": {"hasPhone": True}}}}, "") is True
    assert telefono_da_pagina({"props": {"ad": {"advertiser": {"showPhone": False}}}}, "") is False
    assert telefono_da_pagina({"props": {}}, "<button>mostra numero</button>") is True


def test_showphone_nella_pagina_subito():
    from dealfinder.pipeline.tempo_reale import telefono_da_pagina
    assert telefono_da_pagina(None, "", 'x\\"username\\":\\"A\\",\\"showPhone\\":true,\\"y') is True
    assert telefono_da_pagina(None, "", 'x\\"showPhone\\":false,\\"isDefaultName') is None    # false non basta per dire "no"
    assert telefono_da_pagina(None, "", "pagina senza dati") is None


def test_misure_funzioni_presenti():
    from dealfinder.pipeline import tempo_reale as tr
    assert callable(tr.registra_sparita) and callable(tr.riepilogo_misure)


def test_km_uguale_anno_non_valutata():
    from dealfinder.pricing import policy_v2 as P
    assert P.km_come_anno({"year": 2017, "mileage_km": 2017})
    assert P.km_come_anno({"year": 2023, "mileage_km": 2025})
    assert not P.km_come_anno({"year": 2017, "mileage_km": 120000})
    assert not P.km_come_anno({"year": 2017, "mileage_km": 1000})
    v = P.valuta({"id": 1, "year": 2017, "mileage_km": 2017, "fuel": "diesel", "price_eur": 9000, "make": "vw", "model": "golf"},
                 [], 2200, P.PolicyConfig())
    assert v["categoria"] == "dati_insufficienti"


def test_utilitaria_economica_molto_sotto_mercato():
    prezzi = [4000, 4100, 4200, 4300, 4400, 4500, 4600, 4700]
    v = valuta(car(1, 2100), market(prezzi), soglia=1500, cfg=CFG, now=NOW)       # ~50% di M, 8 confronti
    assert v["categoria"] != "dati_insufficienti", v["motivi"]
    v = valuta(car(1, 1700), market(prezzi), soglia=1500, cfg=CFG, now=NOW)       # sotto il 45%: bloccata
    assert v["categoria"] == "dati_insufficienti"
    v = valuta(car(1, 2100), market(prezzi[:3]), soglia=1500, cfg=CFG, now=NOW)   # pochi confronti: regola normale
    assert v["categoria"] == "dati_insufficienti"
    caro = [p * 3 for p in prezzi]
    v = valuta(car(1, 6300), market(caro), soglia=3200, cfg=CFG, now=NOW)         # sopra 3.000 €: regola normale
    assert v["categoria"] == "dati_insufficienti"


# ---------------------------------------------------------------- pv15: stima del modello AI dei prezzi

CFG_AI = PolicyConfig(ai=True)          # 6/11: stima AI spenta di default (come in produzione)


def test_ai_senza_confronti_valuta_ma_resta_candidato():
    t = dict(TARGET, ai={"prezzo": 10000, "errore": .10, "n": 30})
    v = valuta(t, [], 1500, CFG_AI, NOW)
    assert v["metodo"] == "ai" and v["M"] == 10000 and v.get("solo_ai")
    assert v["categoria"] == "candidato"                      # mai "opportunità" piena con la sola AI
    v0 = valuta(TARGET, [], 1500, CFG_AI, NOW)
    assert v0["categoria"] == "dati_insufficienti"            # senza AI: come prima


def test_ai_poco_precisa_non_usata():
    for ai in ({"prezzo": 10000, "errore": .25, "n": 30}, {"prezzo": 10000, "errore": .10, "n": 3}):
        v = valuta(dict(TARGET, ai=ai), [], 1500, CFG, NOW)
        assert v["categoria"] == "dati_insufficienti" and "ai" not in v


def test_ai_con_confronti_solo_segnale():
    m = market(BASE)
    v0 = valuta(TARGET, m, 1500, CFG_AI, NOW)
    v = valuta(dict(TARGET, ai={"prezzo": v0["M"], "errore": .08, "n": 40}), m, 1500, CFG_AI, NOW)
    assert v["ai"]["concorda"] and v["M"] == v0["M"]
    # 4/11: con le gemelle l'AI (prezzi chiesti) è solo un segnale: non entra in M né sopra né sotto
    for k in (1.5, 0.6):
        x = valuta(dict(TARGET, ai={"prezzo": int(v0["M"] * k), "errore": .08, "n": 40}), m, 1500, CFG_AI, NOW)
        assert x["ai"]["disaccordo"] and x["M"] == v0["M"] and x["categoria"] in ("candidato", "dati_insufficienti")


def test_ai_non_salva_le_truffe():
    t = dict(TARGET, price_eur=3000, ai={"prezzo": 10000, "errore": .10, "n": 30})
    v = valuta(t, [], 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti"             # prezzo sotto il 55% del mercato: resta fuori


def test_prezzo_molto_basso_ammesso_con_targa_e_telefono():
    m = market(BASE)
    t = dict(TARGET, price_eur=3000)
    assert valuta(t, m, 1500, CFG, NOW)["categoria"] == "dati_insufficienti"      # senza targa+numero: fuori
    v = valuta(dict(t, targa_e_telefono=True), m, 1500, CFG, NOW)
    assert v["categoria"] == "candidato" and v.get("prezzo_molto_basso_ammesso")


def test_prezzo_assurdo_mai_sul_sito_anche_con_targa():
    m = market(BASE)
    t = dict(TARGET, price_eur=500, targa_e_telefono=True)
    v = valuta(t, m, 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti"


def test_allestimento_1_11():
    from dealfinder.pricing.policy_v2 import allestimento
    assert allestimento({"version_raw": "118d M Sport"}) == "sportivo"
    assert allestimento({"title": "Golf 2.0 TDI R-Line"}) == "sportivo"
    assert allestimento({"version_raw": "Golf 1.6 TDI Business"}) == "normale"


def test_valore_reale_concorda_e_poche_gemelle():
    v0 = valuta(TARGET, market(BASE), 1500, CFG, NOW)
    assert v0.get("M")
    f = .915                                                  # riserva chiesto -> venduto della fascia 5-10k
    # gemelle portate al prezzo di vendita entro ±8% dal valore reale: M = V (prezzo di vendita, niente calibrazione)
    for k in (1.02, 0.97):
        t = dict(TARGET, vr={"v": int(v0["M"] * f * k), "p25": 1, "sigma": 0.1, "n": 500})
        v = valuta(t, market(BASE), 1500, CFG, NOW)
        assert v["M"] == int(v0["M"] * f * k) and v["metodo"] == "valore_reale" and v["vr_accordo"] is True
        assert v["M_fonte"] == "vendite" and v["M_gemelle"] == v0["M"] and v["metodo_base"] == v0["metodo"]
    # molto diversi (sopra o sotto): media pesata con gli errori, mai il più basso o il più alto dei due
    for k in (0.7, 1.4):
        V = int(v0["M"] * f * k)
        v2 = valuta(dict(TARGET, vr={"v": V, "p25": 1, "sigma": 0.1, "n": 500}), market(BASE), 1500, CFG, NOW)
        G = v2["valore_reale"]["gemelle_vendita"]
        assert min(V, G) < v2["M"] < max(V, G) and v2["vr_accordo"] is False
        assert v2["metodo"] == "valore_reale_combinato" and abs(sum(v2["valore_reale"]["pesi"].values()) - 1) < .01


def test_valore_reale_poche_gemelle_non_diventa_affidabile():
    # 3/11 (controllo indipendente): 2 gemelle + valore reale preciso -> entra, ma resta "candidato" (campione limitato)
    # e il numero salvato resta quello vero
    m = market([9500, 9700])
    v0 = valuta(TARGET, m, 1500, CFG, NOW)
    if not v0.get("M"):
        return
    t = dict(TARGET, vr={"v": int(v0["M"] * 0.98), "p25": int(v0["M"] * 0.9), "sigma": 0.1, "n": 500})
    v = valuta(t, m, 1500, CFG, NOW)
    assert v.get("vr_poche_gemelle") and v.get("n_gemelle") == 2
    assert v["categoria"] != "opportunita"
    assert (v.get("n") or 0) <= 2 or v.get("metodo_base") in ("modello", "ai")


def test_valore_reale_una_gemella_con_ai_non_basta():
    # 1 sola gemella: anche se il modello AI alza il conteggio, il valore reale non "garantisce" il minimo
    m = market([9500])
    t = dict(TARGET, ai={"prezzo": 9400, "errore": .08, "n": 300, "livello": 1},
             vr={"v": 9300, "p25": 8500, "sigma": 0.1, "n": 500})
    v = valuta(t, m, 1500, CFG, NOW)
    assert not v.get("vr_poche_gemelle")


def test_allestimento_soglia_n_minimo():
    # 3/11: 3 "Business" e 4 "R-Line": una Business si confronta solo con le Business (prima: tutte e 7, +21%)
    t = car(1, 21000, version_raw="Golf 1.6 TDI Business", year=2020, km=60000)
    mk = [car(200 + i, p, version_raw="Golf 1.6 TDI Business", year=2020, km=58000 + i * 1000) for i, p in
          enumerate([22000, 22200, 22400])]
    mk += [car(300 + i, p, version_raw="Golf 1.6 TDI R-Line", year=2020, km=59000 + i * 1000) for i, p in
           enumerate([27000, 27500, 28000, 28500])]
    v = valuta(t, mk, 4000, CFG, NOW)
    if v.get("M") and v.get("metodo") == "mediana":
        assert v["M"] <= 22500

def test_fascia_richiesta():
    from dealfinder.pricing.policy_v2 import benzina_richiesta
    prima = _os.environ.get("BENZ_RICHIESTE")
    _os.environ["BENZ_RICHIESTE"] = "1"
    try:
        assert benzina_richiesta({"fuel": "Benzina", "price_eur": 6000, "mileage_km": 80000, "year": 2012})
        assert benzina_richiesta({"fuel": "Ibrida", "price_eur": 9000, "mileage_km": 90000, "year": 2015})
        assert benzina_richiesta({"fuel": "GPL", "price_eur": 5000, "mileage_km": 50000, "year": 2011})
        assert benzina_richiesta({"fuel": "Diesel", "price_eur": 9500, "mileage_km": 70000, "year": 2018})
        assert not benzina_richiesta({"fuel": "Diesel", "price_eur": 6000, "mileage_km": 70000, "year": 2014})
        assert not benzina_richiesta({"fuel": "Benzina", "price_eur": 12000, "mileage_km": 50000, "year": 2018})
        assert not benzina_richiesta({"fuel": "Benzina", "price_eur": 6000, "mileage_km": 120000, "year": 2012})
    finally:
        _ripristina("BENZ_RICHIESTE", prima)


def test_ibride_mild_diesel_non_confrontate_con_plugin():
    """6/11: 318d mild hybrid e 330e plug-in erano 'la stessa auto' (stesso fuel 'ibrida', kW vicini)."""
    from dealfinder.pricing.policy_v2 import _equivalente, ibrida_tipo
    a = car(1, 20900, fuel="ibrida", version_raw="318d Touring mhev 48V Business", kw=110)
    b = car(2, 30000, fuel="ibrida", version_raw="330e Touring Plug-in Msport", kw=110)
    c = car(3, 21500, fuel="ibrida", version_raw="318d Touring mild hybrid 48V", kw=110)
    d = car(4, 26000, fuel="ibrida", version_raw="A6 Avant 50 TFSI e quattro S tronic", kw=220)
    assert ibrida_tipo(a) == ("mild", "diesel") and ibrida_tipo(b)[0] == "plug-in" and ibrida_tipo(d)[0] == "plug-in"
    assert _equivalente(a, b, CFG, 1)
    assert _equivalente(a, c, CFG, 1) is None
    e = car(5, 9000, fuel="ibrida", version_raw="Yaris 1.5 Hybrid Active", kw=85)
    f = car(6, 9000, fuel="ibrida", version_raw="Yaris 1.5 Hybrid Trend", kw=85)
    assert _equivalente(e, f, CFG, 1) is None


def test_vicina_alla_soglia_va_in_da_trattare():
    """6/11: un'auto della fascia richiesta a 300 € dal margine minimo diventa candidato 'da trattare' (prima
    restava non_conveniente e non arrivava mai al sito)."""
    from dealfinder.web import scovo
    m = [car(100 + n, 7800 + n * 10, fuel="benzina", km=60000 + n * 500) for n in range(9)]
    t = car(1, 6500, fuel="benzina", km=61000)
    v = valuta(t, m, scovo.soglia(6500), CFG, NOW)
    assert v["margine"] < scovo.soglia(6500)
    assert v["categoria"] == "candidato" and v["da_trattare"]["sconto"] <= 500
    lontana = valuta(car(2, 7500, fuel="benzina", km=61000), m, scovo.soglia(7500), CFG, NOW)
    assert lontana["categoria"] == "non_conveniente"
