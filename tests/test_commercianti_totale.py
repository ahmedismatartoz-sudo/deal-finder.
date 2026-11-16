"""16/11 (cliente: "sul sito non più di 60 auto di commercianti IN TUTTO"): tetto totale dei commercianti nelle regole
del sito (lontani, fascia richiesta, diesel in più), privati invariati. Niente rete né database."""
import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.web import sito_regole as SR   # noqa: E402
from test_diesel_extra import G, auto, elenco  # noqa: E402

SENZA = dataclasses.replace(G, commercianti_totale=0)


def _mondo():
    lontani = [auto(1000 + k, carb="benzina", prezzo=12000, M=16000, margine=5000 + k, prov="MB",
                    _r={"valutazione_nuova": True}) for k in range(45)]                  # 40 entrano (tetto dei 40)
    fascia = [auto(2000 + k, carb="benzina", anno=2014, margine=2500 + 10 * k) for k in range(30)]
    diesel = [auto(3000 + k, margine=3000 + 10 * k) for k in range(10)]                # diesel in più
    privati = [auto(4000 + k, carb="benzina", seller="privato", margine=1900 + k) for k in range(25)]
    return lontani + fascia + diesel + privati


def _tipi(out):
    from collections import Counter
    return Counter(it.get("_tipo") for it in out)


def test_default_60_e_interruttore():
    assert SR.Regole().commercianti_totale == 60 and SR.Regole.da_env().commercianti_totale == 60
    vecchio = os.environ.get("SITO_COMMERCIANTI_TOTALE")
    try:
        os.environ["SITO_COMMERCIANTI_TOTALE"] = "0"
        assert SR.Regole.da_env().commercianti_totale == 0
        os.environ["SITO_COMMERCIANTI_TOTALE"] = "25"
        assert SR.Regole.da_env().commercianti_totale == 25
    finally:
        if vecchio is None:
            os.environ.pop("SITO_COMMERCIANTI_TOTALE", None)
        else:
            os.environ["SITO_COMMERCIANTI_TOTALE"] = vecchio


def test_al_massimo_60_commercianti_privati_invariati():
    prima, _ = elenco(_mondo(), SENZA)
    t0 = _tipi(prima)
    assert t0["comm_lontano"] == 40 and t0["comm_fascia"] == 30 and t0["comm_diesel"] == 10 and t0["privato"] == 25
    out, fun = elenco(_mondo())
    t = _tipi(out)
    assert sum(n for k, n in t.items() if k.startswith("comm_")) == 60
    assert t["privato"] == 25                                         # i privati non si toccano
    assert t["comm_lontano"] == 40                                    # i 40 lontani dentro il totale
    # i migliori per margine (stesso criterio del tetto dei 40): 40 lontani, poi diesel (3.000-3.090) e fascia migliori
    tenuti_comm = sorted((it for it in out if SR.e_commerciante(it)), key=lambda it: -SR.margine(it))
    tolti = [it for it in prima if SR.e_commerciante(it) and it["id"] not in {x["id"] for x in out}]
    assert min(SR.margine(it) for it in tenuti_comm) >= max(SR.margine(it) for it in tolti)
    p = next(x for x in fun.passi if x["passo"] == "tetto_commercianti_totale")
    assert p["motivi"] == {"oltre_60_commercianti": 20} and p["uscite"] == 20
    assert sorted(fun.tolti["tetto_commercianti_totale"]) == sorted(it["id"] for it in tolti)
    # diesel in più coerenti: "entrate" conta solo quelli rimasti, nessuno contato due volte
    assert fun.diesel_extra["entrate"] == t["comm_diesel"] == sum(1 for it in out if it.get("diesel_extra"))
    assert not set(fun.tolti["tetto_commercianti_totale"]) & set(fun.tolti.get("diesel_extra", []))


def test_sotto_il_tetto_nessun_cambiamento():
    items = [it for it in _mondo() if not (2000 <= it["id"] < 2030)]   # 40 + 10 = 50 commercianti
    a = sorted(it["id"] for it in elenco(items)[0])
    b = sorted(it["id"] for it in elenco(items, SENZA)[0])
    assert a == b
    _, fun = elenco(items)
    p = next(x for x in fun.passi if x["passo"] == "tetto_commercianti_totale")
    assert p["uscite"] == 0 and "motivi" not in p


def test_spento_come_prima():
    out, fun = elenco(_mondo(), SENZA)
    assert sum(1 for it in out if SR.e_commerciante(it)) == 80
    assert not any(x["passo"] == "tetto_commercianti_totale" for x in fun.passi)


def test_sentinella_usa_il_totale():
    from dealfinder import sentinella as S
    base = {"prezzo": 8000, "margine": 3000, "soglia": 1800, "commerciante": True, "sezione": "affari"}
    sito = ([dict(base, listing_id=i) for i in range(40)]
            + [dict(base, listing_id=100 + i, sezione="fascia_richiesta") for i in range(15)]
            + [dict(base, listing_id=200 + i, sezione="diesel_extra") for i in range(5)]
            + [dict(base, listing_id=300 + i, commerciante=False) for i in range(385)])
    r = S.c_tetto_commercianti(None, {"istantanea": sito, "istantanea_ore": 0.5})
    assert r["ok"] and r["numeri"]["commercianti_totale"] == 60 and r["numeri"]["tetto_totale"] == 60
    r = S.c_tetto_commercianti(None, {"istantanea": sito + [dict(base, listing_id=999, sezione="fascia_richiesta")],
                                      "istantanea_ore": 0.5})
    assert {a["chiave"] for a in r["allarmi"]} == {"tetto_commercianti:totale"}
