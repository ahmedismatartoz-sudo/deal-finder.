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
