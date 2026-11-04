"""Regole del sito (sito_regole): funzioni pure su auto finte, niente database."""
from dealfinder.web import scovo
from dealfinder.web import sito_regole as SR

G = SR.Regole()


def auto(i, prezzo=5000, margine=3000, M=8000, prov="MI", tel=True, seller="privato", anno=2015, km=90000,
         carb="Diesel", fonte="Subito", jev=None, **kw):
    r = {"tel": tel, "seller_type": seller, "provincia": prov, "jev": jev}
    r.update(kw.pop("_r", {}))
    it = {"id": i, "prezzo": prezzo, "anno": anno, "km": km, "carb": carb, "fonte": fonte,
          "pv": {"categoria": "opportunita", "M": M, "R": prezzo + margine + 200, "R_finale": prezzo + margine + 200,
                 "margine": margine, "totale_costi": 200, "n": 10},
          "_r": r}
    it.update(kw)
    return it


def ids(items):
    out, _ = SR.elenco_sito([dict(it, _r=dict(it["_r"])) for it in items], G, scrivi_log=False)
    return [it["id"] for it in out]


def test_nessuna_auto_sotto_il_margine_minimo():
    s = scovo.soglia(5000)
    items = [auto(1, margine=s), auto(2, margine=s - 1), auto(3, margine=-100), auto(4, margine=0)]
    assert ids(items) == [1]
    # il costo del difetto letto dall'AI (unica modifica al margine) viene PRIMA del controllo finale
    it = auto(5, margine=s + 100, _r={"difetto": {"costo": 300, "motivo": "frizione"}})
    out, _ = SR.elenco_sito([it], G, scrivi_log=False)
    assert out == []
    it = auto(6, margine=s + 500, _r={"difetto": {"costo": 300, "motivo": "frizione"}})
    out, _ = SR.elenco_sito([it], G, scrivi_log=False)
    assert out[0]["pv"]["margine"] == s + 200 and out[0]["pv"]["difetto_costo"] == 300
    for it in SR.elenco_sito([auto(k, prezzo=p, margine=m) for k, (p, m) in enumerate(
            [(900, 1600), (3000, 1400), (8000, 1700), (12000, 2600), (25000, 3900), (40000, 7000)])], G,
            scrivi_log=False)[0]:
        assert it["pv"]["margine"] >= scovo.soglia(it["prezzo"])


def test_senza_margine_fuori_anche_economiche():
    it = auto(1, prezzo=800, economica=True)
    it["pv"]["margine"] = None
    assert ids([it]) == []
    assert ids([auto(2, prezzo=800, M=2200, economica=True, margine=scovo.soglia(800))]) == [2]


def test_mai_roma():
    assert ids([auto(1, prov="RM"), auto(2, prov="LT"), auto(3, prov="MI")]) == [3]
    assert ids([auto(4, prov="RM", supercar=True, prezzo=100000, margine=30000, M=140000, _r={"sc_ok": True})]) == []
    assert SR.provincia_di(None, "Roma") == "RM" and SR.provincia_di("", "Milano") == "MI"


def test_zona_100km_ravenna_e_supercar():
    assert ids([auto(1, prov="TO"), auto(2, prov="BG"), auto(3, prov="RA"), auto(4, prov=None)]) == [2, 3]
    sc = auto(5, prov=None, supercar=True, prezzo=100000, margine=20000, M=130000, _r={"sc_ok": True})
    assert ids([sc]) == [5]


def test_loconte_sempre_fuori():
    assert ids([auto(1, _r={"loconte": True}), auto(2)]) == [2]
    assert ids([auto(3, seller="commerciante", prov="BG", _r={"loconte": True, "valutazione_nuova": True})]) == []


def test_tetto_40_commercianti_dopo_zona_e_margine():
    s = scovo.soglia(5000)
    lontani = [auto(100 + k, seller="commerciante", prov="BG", margine=s + k, M=6000, _r={"valutazione_nuova": True})
               for k in range(45)]
    roma = [auto(200 + k, seller="commerciante", prov="RM", margine=s + 1000, M=6000, _r={"valutazione_nuova": True})
            for k in range(10)]
    sotto = [auto(300 + k, seller="commerciante", prov="BG", margine=s - 1, M=6000, _r={"valutazione_nuova": True})
             for k in range(10)]
    out = ids(lontani + roma + sotto)
    assert len(out) == 40 and set(out) == {100 + k for k in range(5, 45)}       # i 40 migliori, nessuno di Roma
    # commerciante di Milano non della fascia richiesta: fuori; con promo: fuori
    assert ids([auto(1, seller="commerciante", prov="MI", _r={"valutazione_nuova": True})]) == []
    assert ids([auto(2, seller="commerciante", prov="BG", _r={"valutazione_nuova": True, "promo": True})]) == []
    # prezzo esca (sotto il 75% del mercato)
    assert ids([auto(3, seller="commerciante", prov="BG", M=8000, _r={"valutazione_nuova": True})]) == []


def test_fascia_richiesta_commercianti_locali_senza_tetto():
    import os
    prima = os.environ.get("BENZ_RICHIESTE")
    os.environ["BENZ_RICHIESTE"] = "1"
    try:
        s = scovo.soglia(6000)
        items = [auto(k, prezzo=6000, M=7000, margine=s, carb="Benzina", km=80000, seller="commerciante", prov="MI")
                 for k in range(50)]
        assert len(ids(items)) == 50
        promo = auto(99, prezzo=6000, M=7000, margine=s, carb="Benzina", km=80000, seller="commerciante", prov="MI",
                     _r={"promo": True})
        assert ids([promo]) == []
    finally:
        if prima is None:
            os.environ.pop("BENZ_RICHIESTE", None)
        else:
            os.environ["BENZ_RICHIESTE"] = prima


def test_supercar_soglie():
    base = dict(supercar=True, prov=None, _r={"sc_ok": True})
    assert ids([auto(1, prezzo=100000, margine=15000, M=130000, **base)]) == [1]          # 15% di 100.000
    assert ids([auto(2, prezzo=100000, margine=14999, M=130000, **base)]) == []
    assert ids([auto(3, prezzo=50000, margine=9999, M=70000, **base)]) == []               # sotto 10.000 €
    assert ids([auto(4, prezzo=50000, margine=10000, M=70000, **base)]) == [4]
    assert ids([auto(5, prezzo=100000, margine=20000, M=130000, supercar=True, prov=None, _r={"sc_ok": None})]) == []
    assert ids([auto(6, prezzo=100000, margine=20000, M=130000, supercar=True, prov=None, _r={"sc_ok": False})]) == []
    sc = auto(7, prezzo=100000, margine=20000, M=130000, **base)
    sc["pv"]["margine"] = None
    assert ids([sc]) == []                                                                 # mai senza margine


def test_tabella_truffa():
    s = scovo.soglia(5000)
    j = lambda p, **k: dict({"truffa": p}, **k)
    out, _ = SR.elenco_sito([auto(1, jev=j(0.3)), auto(2, jev=j(0.6)), auto(3, jev=j(0.9)), auto(4)], G,
                            scrivi_log=False)
    assert [(it["id"], it["avviso_verifica"]) for it in out] == [(1, False), (2, True), (4, False)]
    # segnali duri: fuori anche con probabilità bassa
    assert ids([auto(5, jev=j(0.1, foto_internet=0.8))]) == []
    assert ids([auto(6, prezzo=3000, M=7000, margine=s, jev=j(0.1))]) == []                # 43% del mercato
    assert ids([auto(7, prezzo=2000, M=5000, margine=scovo.soglia(2000), jev=j(0.1))]) == [7]  # 40%: sotto 3.000 € ok
    assert ids([auto(8, prezzo=3000, M=7000, margine=s, incidentata=True, jev=j(0.1))]) == [8]  # danno dichiarato
    assert ids([auto(9, _r={"testo_truffa": True})]) == []
    assert ids([auto(10, _r={"tel_estero": True})]) == []
    assert SR.testo_truffa("chiedo caparra con bonifico prima di vedere l'auto")
    assert SR.testo_truffa("l'auto si trova in Germania, spedizione con corriere")
    assert not SR.testo_truffa("tagliandata, gomme nuove, visibile a Monza")
    assert SR.tel_estero("chiamare +44 7700 900123") and not SR.tel_estero("tel +39 333 1234567")
    # margine molto alto (>100% del prezzo e >3.000 €): soglie −0,1 e solo dopo Jev
    alto = dict(prezzo=3500, M=7500, margine=4500)
    assert ids([auto(11, **alto)]) == []                                                   # Jev non ha deciso
    out, _ = SR.elenco_sito([auto(12, jev=j(0.45), **alto), auto(13, jev=j(0.76), **alto)], G, scrivi_log=False)
    assert [it["id"] for it in out] == []                                                  # 47% del mercato: troppo bella
    # 4/11 sconto massimo 40%; oltre il limite in euro della fascia serve l'AI pulita (truffa < 0,30)
    assert ids([auto(17, prezzo=13000, M=21900, margine=8900, anno=2024, jev=j(0.1))]) == []      # la Yaris GR: 41%
    assert ids([auto(18, prezzo=4000, M=6500, margine=2500, jev=j(0.4))]) == [18]                # piccola: 38%, sotto 5.000 €
    assert ids([auto(19, prezzo=12000, M=17500, margine=5500, jev=j(0.1))]) == [19]              # 5.500 € > 5.000 ma AI pulita
    assert ids([auto(20, prezzo=12000, M=17500, margine=5500, jev=j(0.4))]) == []                # stessa auto, AI dubbia
    assert ids([auto(22, prezzo=12000, M=17500, margine=5500)]) == []                            # stessa auto, AI non ha deciso
    assert ids([auto(23, prezzo=12000, M=15500, margine=3500, jev=j(0.6))]) == [23]              # sconto normale: col cartello
    # decisioni Jev non lette: le auto da verificare e quelle con margine molto alto restano fuori
    assert ids([auto(14, _r={"jev_errore": True, "da_verificare": True}), auto(15, _r={"jev_errore": True}, **alto),
                auto(16, _r={"jev_errore": True})]) == [16]


def test_numero_obbligatorio():
    assert ids([auto(1, tel=False), auto(2, tel=True)]) == [2]
    fb = auto(3, tel=False, fonte="Facebook Marketplace")
    assert ids([fb]) == []                                                                 # FB_SITO=0 (default)
    out, _ = SR.elenco_sito([dict(fb, _r=dict(fb["_r"]))], SR.Regole(fb_sito=True), scrivi_log=False)
    assert [it["id"] for it in out] == [3]


def test_km_e_anno():
    assert ids([auto(1, km=None), auto(2, km=150000), auto(3, km=150001), auto(4, anno=2006), auto(5, anno=2007)]) == [2, 5]


def test_incidentate_restano():
    s = scovo.soglia(4000)
    inc = auto(1, prezzo=4000, M=9000, margine=s, incidentata=True, _r={"danno_dichiarato": True},
               jev={"truffa": 0.1, "danni": 0.9})
    assert ids([inc]) == [1]
    sana = auto(2, prezzo=4000, M=9000, margine=s, jev={"truffa": 0.1, "danni": 0.9})
    assert ids([sana]) == []                                                               # danni nelle foto non dichiarati


def test_jev_toglie_solo_i_casi_chiari():
    assert ids([auto(1, jev={"guasto": 0.8}), auto(2, jev={"km_ok": 0.2}), auto(3, jev={"rata": 0.9}),
                auto(4, jev={"decisione": "scarta", "confidenza": 0.7}), auto(5, jev={"versione_ok": 0.1}),
                auto(6, jev={"versione_ok": 0.35}), auto(7, jev={"decisione": "scarta", "confidenza": 0.3})]) == [6, 7]
    # nessuna correzione al ribasso per "confronti non uguali": il margine resta quello calcolato
    out, _ = SR.elenco_sito([auto(8, margine=3000, jev={"versione_ok": 0.35})], G, scrivi_log=False)
    assert out[0]["pv"]["margine"] == 3000


def test_funnel_conta_e_righe_giorno():
    out, fun = SR.elenco_sito([auto(1), auto(2, prov="RM"), auto(3, tel=False)], G, scrivi_log=False)
    passi = {p["passo"]: p for p in fun.passi}
    assert passi["telefono"]["uscite"] == 1 and passi["no_roma"]["uscite"] == 1 and len(out) == 1
    righe = SR.righe_giorno(fun)
    assert ("no_roma.roma", 2, 1) in righe and all(len(r) == 3 for r in righe)


def test_stessi_numeri_elenco_e_scheda():
    vecchio = scovo.POLICY_SITO
    scovo.POLICY_SITO = "pv2"
    try:
        it = auto(1, margine=3000, _r={"difetto": {"costo": 400, "motivo": "frizione"}})
        out, _ = SR.elenco_sito([it], G, scrivi_log=False)
        el = out[0]
        el.pop("_r", None)
        el.update(n_foto=0, foto=None)
        dati = {"M": 8000, "R": 9999, "R_finale": 9999, "margine": 3000, "totale_costi": 999, "categoria": "opportunita"}
        d = scovo.detail(dict(el), {"description": "ok", "pv_dati": dati})
        for k in ("R", "R_finale", "margine", "totale_costi", "M"):
            assert d["valutazione"][k] == el["pv"][k], k
        assert d["valutazione"]["margine"] == 2600 and d["pv"]["margine"] == 2600
    finally:
        scovo.POLICY_SITO = vecchio


def test_commercianti_valutati_per_data_non_per_testo():
    from datetime import datetime, timezone
    from dealfinder.web import app
    assert app._dopo(datetime(2026, 11, 4, tzinfo=timezone.utc), "2026-11-03T22:55:00+01:00")
    assert not app._dopo(datetime(2026, 11, 3, 12, tzinfo=timezone.utc), "2026-11-03T22:55:00+01:00")
    assert not app._dopo(None, "2026-11-03T22:55:00+01:00")
    tx = app._dal_testo("Golf tagliandata, chiamare 333 1234567. Bonifico prima di vedere l'auto.", None)
    assert tx["tel_testo"] and tx["testo_truffa"] and not tx["loconte_testo"]


def test_correzione_chiesto_venduto_una_volta():
    import dataclasses
    from dealfinder.web import sito_regole as SR
    g = dataclasses.replace(SR.Regole(), fattori_calibra=((5000, "<5k", 0.87), (10000, "5-10k", 0.915), (10**9, "oltre", 1.0)))
    it = {"id": 1, "prezzo": 5000, "pv": {"M": 8000, "R": 8000, "R_finale": 8000, "margine": 3000, "metodo": "mediana"}}
    SR.t_margine_sito(it, g)
    assert it["pv"]["R_finale"] == 7320 and it["pv"]["margine"] == 2320
    vr = {"id": 2, "prezzo": 5000, "pv": {"M": 8000, "R": 8000, "R_finale": 8000, "margine": 3000, "metodo": "valore_reale"}}
    SR.t_margine_sito(vr, g)
    assert vr["pv"]["R_finale"] == 8000 and vr["pv"]["margine"] == 3000      # il valore reale è già un prezzo di vendita
