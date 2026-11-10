from dealfinder.web import scovo


def test_ribasso_fascia():
    it = {"id": 1, "prezzo": 5000, "pv": {"margine": 1800, "M": 8000}}
    o = scovo.prezzo_sito(it)
    assert o["prezzo"] == 4300 and o["prezzo_annuncio"] == 5000 and o["pv"]["margine"] == 2500
    assert it["prezzo"] == 5000 and it["pv"]["margine"] == 1800      # l'originale non cambia
    assert scovo.prezzo_sito(o) is o                                    # mai due volte


def test_ribasso_fuori_fascia():
    for p in (1999, 6000, 12000):
        it = {"id": 2, "prezzo": p, "pv": {"margine": 1500}}
        assert scovo.prezzo_sito(it) is it
    assert scovo.prezzo_sito({"prezzo": 2000})["prezzo"] == 1300
    sc = {"prezzo": 5000, "supercar": True}
    assert scovo.prezzo_sito(sc) is sc
    tr = {"prezzo": 5000, "trattare": {"sconto": 300}}
    assert scovo.prezzo_sito(tr) is tr
