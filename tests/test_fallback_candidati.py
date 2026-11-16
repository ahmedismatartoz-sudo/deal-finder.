"""16/11 (fondamenta): test del comportamento ATTUALE di pricing/fallback.py e pipeline/candidati.py, che non ne
avevano. Non cambiano nulla: se uno fallisce, il comportamento è cambiato (voluto o no)."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from dealfinder.core.models import Listing
from dealfinder.pipeline import candidati as C
from dealfinder.pricing import fallback as F
from dealfinder.web import scovo


def _l(**kw) -> Listing:
    base = dict(source="subito", source_id="1", url="https://example.invalid/1", make="fiat", model="panda",
                year=date.today().year - 5, mileage_km=60000, fuel="benzina", price_eur=6000)
    base.update(kw)
    return Listing(**base)


def _v(**kw):
    base = dict(resale_prudent=5000, n_comparables=5, dispersion=.2, private_median=None, resale_median=None,
                confidence="alta", confidence_reasons=[])
    base.update(kw)
    return SimpleNamespace(**base)


# ------------------------------------------------------------------ fallback
def test_weak():
    assert not F.weak(_v())
    assert F.weak(_v(resale_prudent=None))
    assert F.weak(_v(n_comparables=2))
    assert F.weak(_v(dispersion=.41))
    assert not F.weak(_v(dispersion=None))


def test_km_mancanti_stimati_dall_eta():
    l = _l(mileage_km=None, year=date.today().year - 4)
    assert F.estimate_missing_km(l) and l.mileage_km == 4 * F.KM_PER_YEAR_PRUDENT
    vecchia = _l(mileage_km=None, year=1990)
    assert F.estimate_missing_km(vecchia) and vecchia.mileage_km == 300_000       # tetto
    nuova = _l(mileage_km=None, year=date.today().year)
    assert F.estimate_missing_km(nuova) and nuova.mileage_km == F.KM_PER_YEAR_PRUDENT   # almeno un anno
    assert not F.estimate_missing_km(_l())                                          # km già scritti
    assert not F.estimate_missing_km(_l(mileage_km=None, year=None))


def test_apply_model_condizioni(monkeypatch):
    stima = {"p50": 10000, "p25": 9000, "p75": 11000, "level": "mm", "n": 50, "spread": .2}
    monkeypatch.setattr(F, "predict", lambda m, l: dict(stima))
    assert not F.apply_model(_v(), _l(), None)                       # niente modello
    assert not F.apply_model(_v(), _l(year=2007), {})               # troppo vecchia (nemmeno {} conta)
    assert not F.apply_model(_v(), _l(year=2007), {"x": 1})
    assert not F.apply_model(_v(), _l(fuel="altro"), {"x": 1})
    assert not F.apply_model(_v(), _l(mileage_km=None), {"x": 1})
    assert not F.apply_model(_v(), _l(mileage_km=3000), {"x": 1})   # km non plausibili per l'età
    v = _v()
    assert F.apply_model(v, _l(), {"x": 1})
    assert v.private_median == 10000 and v.resale_median == v.resale_prudent == 9500   # × 0,95
    assert v.dispersion == .2 and v.confidence == "da_verificare" and v.confidence_reasons == ["stima_da_modello"]
    F.apply_model(v, _l(), {"x": 1})
    assert v.confidence_reasons == ["stima_da_modello"]              # mai due volte


def test_apply_model_livello_o_dispersione_non_buoni(monkeypatch):
    monkeypatch.setattr(F, "predict", lambda m, l: {"p50": 1, "level": "make", "spread": .1})
    assert not F.apply_model(_v(), _l(), {"x": 1})
    monkeypatch.setattr(F, "predict", lambda m, l: {"p50": 1, "level": "mm", "spread": F.MAX_SPREAD + .01})
    assert not F.apply_model(_v(), _l(), {"x": 1})


def test_apply_model_senza_carburante_prende_la_stima_piu_bassa(monkeypatch):
    prezzi = {"benzina": 9000, "diesel": 11000}
    monkeypatch.setattr(F, "predict", lambda m, l: {"p50": prezzi[l.fuel], "level": "mmf", "spread": .1})
    v, l = _v(), _l(fuel=None)
    assert F.apply_model(v, l, {"x": 1})
    assert v.private_median == 9000 and l.fuel is None
    assert v.confidence_reasons == ["carburante_non_indicato", "stima_da_modello"]


# ------------------------------------------------------------------ candidati
def test_guadagno_sito():
    v = SimpleNamespace(private_median=10000, resale_median_private=None, resale_median=None)
    atteso = round(10000 * (1 - scovo.SCONTO_DEFAULT / 100)) - 7000 - sum(scovo.COSTI_FISSI.values())
    assert C.guadagno_sito(v, 7000) == atteso
    assert C.guadagno_sito(SimpleNamespace(private_median=None, resale_median_private=None, resale_median=8000), 7000) \
        == round(8000 * (1 - scovo.SCONTO_DEFAULT / 100)) - 7000 - sum(scovo.COSTI_FISSI.values())
    assert C.guadagno_sito(v, None) is None
    assert C.guadagno_sito(SimpleNamespace(private_median=None, resale_median_private=None, resale_median=None), 1) is None


def test_hidden_reasons():
    l = SimpleNamespace(title="Vendo auto", make="fiat", model="panda", description="corta", photos=["a"],
                        price_flags=["prezzo_da_descrizione"])
    assert C.hidden_reasons(l) == ["titolo_senza_marca_modello", "descrizione_quasi_vuota", "poche_foto",
                                   "prezzo_sbagliato_nel_campo"]
    ok = SimpleNamespace(title="Fiat Panda 1.2", make="fiat", model="panda", description="x" * 50,
                         photos=["a", "b", "c"], price_flags=[])
    assert C.hidden_reasons(ok) == []


def test_balanced_quote_per_fascia_e_stato():
    def it(m, prezzo, danno=False):
        return (m, None, SimpleNamespace(price_eur=prezzo), None, None, None, danno)
    items = [it(100 - i, 1000) for i in range(20)] + [it(50, 6000), it(40, 15000, True)]
    out = C.balanced(items, 10)              # quota = max(1, 10 // 10) = 1 per gruppo, poi i migliori rimasti
    assert len(out) == 10
    assert any(x[2].price_eur == 6000 for x in out) and any(x[2].price_eur == 15000 for x in out)
    assert out[0][0] == 100
    # prezzo oltre l'ultima fascia: va nell'ultima
    assert len(C.balanced([it(1, 999999)], 5)) == 1
