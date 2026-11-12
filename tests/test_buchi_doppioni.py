"""12/11 buchi della raccolta (§3.7): doppioni tra Subito e AutoScout24 con criterio stretto (Postgres vero)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.core.models import Listing          # noqa: E402
from dealfinder.pipeline.collect import doppione_altro_sito     # noqa: E402


def _as(**k):
    d = dict(source="autoscout24", source_id="x", url="u", make="fiat", model="panda", year=2016, mileage_km=80000,
             price_eur=6000, fuel="benzina", gearbox="manuale", province="MI")
    d.update(k)
    return Listing(**d)


def test_doppioni_stretti_su_postgres_vero():
    try:
        import pg_scheda as P
    except Exception:
        return
    c = P.nuovo_db("buchi_dopp")
    if c is None:
        return
    P.migra_tutte(c)
    c.execute("INSERT INTO listings (source, source_id, url, make, model, year, mileage_km, price_eur, fuel, gearbox, province) "
              "VALUES ('subito', 's1', 'u1', 'fiat', 'panda', 2016, 80200, 6100, 'benzina', 'manuale', 'MI')")
    lid = c.execute("INSERT INTO listings (source, source_id, url) VALUES ('autoscout24', 'a1', 'ua') RETURNING id").fetchone()["id"]
    vecchio = os.environ.pop("DOPPIONE_STRETTO", None)
    try:
        assert doppione_altro_sito(c, _as(), lid)                                  # stessa auto: doppione
        assert doppione_altro_sito(c, _as(province="MB"), lid)                    # stessa regione: doppione
        assert not doppione_altro_sito(c, _as(province="NO"), lid)                # altra regione (Piemonte)
        assert doppione_altro_sito(c, _as(fuel="Benzina", gearbox="Manuale"), lid)   # valori grezzi normalizzati
        assert not doppione_altro_sito(c, _as(fuel="diesel"), lid)                # altro carburante
        assert not doppione_altro_sito(c, _as(mileage_km=81000), lid)             # km a 800 di distanza
        assert doppione_altro_sito(c, _as(fuel=None, province=None), lid)         # dato mancante: non esclude
        os.environ["DOPPIONE_STRETTO"] = "0"
        assert doppione_altro_sito(c, _as(province="NO", mileage_km=81000), lid)  # criterio di prima
    finally:
        os.environ.pop("DOPPIONE_STRETTO", None)
        if vecchio is not None:
            os.environ["DOPPIONE_STRETTO"] = vecchio


def test_cap_di_biella():
    from dealfinder.collectors.autoscout import province_from_cap
    assert province_from_cap("13900") == "BI" and province_from_cap("13811") == "BI"
    assert province_from_cap("13100") == "VC" and province_from_cap("13019") == "VC"
