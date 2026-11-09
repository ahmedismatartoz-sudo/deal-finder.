"""4/11 sera (cliente): sezione "Da trattare": auto della fascia richiesta col margine sotto il minimo che ci
arrivano con uno sconto da chiedere di al massimo 500 € (fino a 7.000 €) o 700 € (sopra)."""
from dealfinder.web import sito_regole as SR
from dealfinder.web import scovo


from datetime import datetime, timedelta, timezone

OGGI = datetime.now(timezone.utc)


def _auto(prezzo, margine, **k):
    return dict({"id": 1, "prezzo": prezzo, "visto_dal": (OGGI - timedelta(days=2)).isoformat(), "km": 80000, "carb": "Benzina", "anno": 2016,
                 "pv": {"M": prezzo + 3000, "margine": margine}}, **k)


def test_sconto_dentro_il_tetto():
    g = SR.Regole.da_env()
    sog = scovo.soglia(6000)
    it = _auto(6000, sog - 500)
    assert SR.sconto_trattativa(it, g) == 500
    assert SR.r_margine_minimo(it, g) is None
    assert it["trattare"] == {"sconto": 500, "prezzo_obiettivo": 5500, "margine": sog}
    assert SR.sconto_trattativa(_auto(6000, sog - 550), g) is None          # oltre 500 € fino a 7.000 €


def test_sopra_7000_fino_a_700():
    g = SR.Regole.da_env()
    sog = scovo.soglia(8500)
    assert SR.sconto_trattativa(_auto(8500, sog - 700), g) == 700
    assert SR.sconto_trattativa(_auto(8500, sog - 750), g) is None


def test_solo_fascia_richiesta():
    g = SR.Regole.da_env()
    sog = scovo.soglia(6000)
    assert SR.sconto_trattativa(_auto(6000, sog - 300, km=120000), g) is None
    assert SR.sconto_trattativa(_auto(6000, sog - 300, carb="Diesel", anno=2012), g) is None
    assert SR.sconto_trattativa(_auto(6000, sog - 300, supercar=True), g) is None
    assert SR.sconto_trattativa(_auto(6000, 0), g) is None
    it = _auto(6000, sog + 100)                                              # già sopra il minimo: sezione normale
    assert SR.r_margine_minimo(it, g) is None and "trattare" not in it


def test_solo_annunci_degli_ultimi_10_giorni():
    g = SR.Regole.da_env()
    sog = scovo.soglia(6000)
    assert SR.sconto_trattativa(_auto(6000, sog - 300, visto_dal=(OGGI - timedelta(days=9)).isoformat()), g) == 300
    assert SR.sconto_trattativa(_auto(6000, sog - 300, visto_dal=(OGGI - timedelta(days=11)).isoformat()), g) is None
    assert SR.sconto_trattativa(_auto(6000, sog - 300, visto_dal=None), g) is None


def test_limiti_da_render(monkeypatch):
    monkeypatch.setenv("TRATTA_GIORNI", "20")
    monkeypatch.setenv("TRATTA_MAX", "1000000000:1000")
    g = SR.Regole.da_env()
    assert g.tratta_giorni == 20 and g.tratta_max == ((10**9, 1000),)
    sog = scovo.soglia(6000)
    assert SR.sconto_trattativa(_auto(6000, sog - 900, visto_dal=(OGGI - timedelta(days=15)).isoformat()), g) == 900


def test_diagnosi():
    g = SR.Regole.da_env()
    sog = scovo.soglia(6000)
    d = SR.diagnosi_trattabili([_auto(6000, sog - 600), _auto(6000, sog + 10)], g)
    assert d == {"fino_700|3g": 1}


def test_fascia_fuori_zona_le_100_piu_vicine():
    g = SR.Regole.da_env()
    def a(i, prov, **k):
        return dict({"id": i, "prezzo": 6000, "km": 80000, "carb": "Benzina", "anno": 2016,
                     "pv": {"M": 9000, "margine": 3000}, "_r": {"provincia": prov}}, **k)
    to, rm, mi = a(1, "GE"), a(2, "RM"), a(3, "MI")          # 9/11: Torino ora è zona del sito, si prova con Genova
    assert SR.r_zona(to, g) is None and to["fuori_zona_km"] > 100
    assert SR.r_zona(rm, g) == "provincia_RM"
    assert SR.r_zona(mi, g) is None and "fuori_zona_km" not in mi
    fuori_fascia = a(4, "GE", km=130000)
    assert SR.r_zona(fuori_fascia, g) == "provincia_GE"
    import dataclasses
    g2 = dataclasses.replace(g, fascia_fuori_max=1)
    vi = a(5, "VI"); SR.r_zona(vi, g2)
    tenute, via = SR.tetto_fuori_zona([to, vi, mi], g2)
    assert via == {5} and [x["id"] for x in tenute] == [1, 3]
