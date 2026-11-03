"""3/11 (ricostruzione, fase 4): raccolta e qualità dei dati (studio 4 §5), senza rete e con dati finti."""
from dealfinder.core.models import Listing


def _l(**kw):
    base = dict(source="subito", source_id="x1", url="https://x/1")
    base.update(kw)
    return Listing(**base)


# ------------------------------------------------------------------ 8. DS e Abarth coerenti

def test_ds_e_abarth_coerenti():
    from dealfinder.core.consistency import check
    from dealfinder.core.normalize import canon_make, slug
    ds = _l(make=canon_make(slug("DS")), model="ds-3-crossback", title="DS 3 Crossback 1.2 PureTech")
    assert ds.make == "ds-automobiles" and check(ds, fix=False) == []
    ab = _l(make="abarth", model="595", title="Fiat 500 Abarth 595 Turismo")
    assert check(ab, fix=False) == []
    ab2 = _l(make="fiat", model="500", title="Abarth 595 competizione")
    assert check(ab2, fix=False) == []
    # una marca davvero diversa resta incoerente
    assert check(_l(make="ford", model="transit", title="Opel Corsa 2007"), fix=False) == ["marca_diversa_dal_testo"]


# ------------------------------------------------------------------ 7. normalizzazione

def _norm(make, model, fuel=None, gearbox=None, title=None):
    from dealfinder.core.normalize import normalize_fields
    l = _l(make=make, model=model, fuel=fuel, gearbox=gearbox, title=title)
    normalize_fields(l)
    return l


def test_normalizzazione_coppie_uguali_e_diverse():
    uguali = [("fiat", "500X", "500 X"), ("fiat", "500-X", "500X"), ("mercedes", "CLA 200", "Classe CLA"),
              ("bmw", "220 Active Tourer", "Serie 2 Active Tourer"), ("audi", "A3 Sportback", "A3"),
              ("volkswagen", "Golf Variant", "Golf"), ("mazda", "CX-5", "CX5"), ("bmw", "320", "Serie 3"),
              ("mercedes-benz", "A 180", "Classe A"), ("mercedes-benz", "GLA 200", "Classe GLA")]
    for mk, a, b in uguali:
        assert _norm(mk, a).model == _norm(mk, b).model, (mk, a, b)
    diverse = [("fiat", "500", "500X"), ("volkswagen", "Golf", "Golf Plus"), ("toyota", "Yaris", "Yaris Cross"),
               ("mercedes-benz", "Classe GLC", "Classe GLC Coupe")]
    for mk, a, b in diverse:
        assert _norm(mk, a).model != _norm(mk, b).model, (mk, a, b)


def test_normalizzazione_cambio_e_alimentazione():
    for g in ("Cambio manuale", "Manuale", "manuale 6 marce", "Manual"):
        assert _norm("fiat", "panda", gearbox=g).gearbox == "manuale", g
    for g in ("Automatico", "Automatico (DSG)", "Semiautomatico", "Sequenziale", "CVT"):
        assert _norm("fiat", "panda", gearbox=g).gearbox == "automatico", g
    assert _norm("fiat", "panda", fuel="Elettrica/Benzina").fuel == _norm("fiat", "panda", fuel="Ibrida").fuel == "ibrida"
    # stessa auto mild hybrid: "Benzina" su una fonte, "Ibrida" sull'altra -> stesso gruppo, sottotipo a parte
    a = _norm("fiat", "panda", fuel="Benzina", title="Fiat Panda 1.0 Hybrid mild")
    b = _norm("fiat", "panda", fuel="mild-hybrid-benzina")
    assert a.fuel == b.fuel == "ibrida" and a.fuel_tipo == b.fuel_tipo == "mild"
    assert _norm("toyota", "rav4", fuel="plug-in-hybrid-benzina").fuel_tipo == "plug-in"
    assert _norm("fiat", "panda", fuel="Benzina/GPL").fuel_tipo == "bifuel"
    pura = _norm("fiat", "panda", fuel="Benzina", title="Fiat Panda 1.2 Easy")
    assert pura.fuel == "benzina" and pura.fuel_tipo is None
