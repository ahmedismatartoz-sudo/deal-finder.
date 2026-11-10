from datetime import datetime, timezone

import os

from dealfinder.web import scovo

os.environ.setdefault("SITO_RIBASSO", scovo.RIBASSO_SCHEMA)

PRIMA = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)      # 16:00 a Roma: ribasso acceso
DOPO = datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc)       # 17:00 a Roma: spento da solo


def test_schema():
    attesi = {1999: 0, 2000: 700, 5999: 700, 6000: 1000, 9999: 1000, 10000: 1500, 15000: 2000, 20000: 2500,
              29999: 2500, 30000: 3000, 80000: 3000}
    for p, e in attesi.items():
        assert scovo.ribasso_per(p, PRIMA) == e, p


def test_ribasso_auto():
    it = {"id": 1, "prezzo": 12000, "pv": {"margine": 3000, "M": 17000}}
    o = scovo.prezzo_sito(it, PRIMA)
    assert o["prezzo"] == 10500 and o["prezzo_annuncio"] == 12000 and o["pv"]["margine"] == 4500
    assert it["prezzo"] == 12000 and it["pv"]["margine"] == 3000
    assert scovo.prezzo_sito(o, PRIMA) is o
    for x in ({"prezzo": 5000, "supercar": True}, {"prezzo": 5000, "trattare": {"sconto": 300}}, {"prezzo": 1500}):
        assert scovo.prezzo_sito(x, PRIMA) is x


def test_si_spegne_da_solo():
    it = {"id": 1, "prezzo": 12000, "pv": {"margine": 3000}}
    assert scovo.prezzo_sito(it, DOPO) is it


def test_spento_di_base():
    prima = os.environ.pop("SITO_RIBASSO", None)
    try:
        it = {"id": 1, "prezzo": 12000, "pv": {"margine": 3000}}
        assert scovo.prezzo_sito(it, PRIMA) is it
    finally:
        if prima is not None:
            os.environ["SITO_RIBASSO"] = prima
