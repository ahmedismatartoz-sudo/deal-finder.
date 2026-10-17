from dealfinder.core.dal_testo import alimentazione, anno, chilometri, completa


def test_alimentazione():
    assert alimentazione("Fiat Panda 1.3 Multijet ottime condizioni") == "diesel"
    assert alimentazione("Golf 1.4 TSI tagliandata") == "benzina"
    assert alimentazione("Punto benzina/gpl di serie") == "gpl"
    assert alimentazione("Yaris hybrid") == "ibrida"
    assert alimentazione("bella auto, ottimo stato") is None
    assert alimentazione("motore diesel, prima era a benzina") is None      # conflitto: resta vuoto


def test_anno_e_km():
    assert anno("immatricolata 03/2014, unico proprietario") == 2014
    assert anno("anno 2012 e del 2015") is None
    assert chilometri("ha 128.000 km certificati") == 128000
    assert chilometri("150 mila km") == 150000
    assert chilometri("km: 98000") == 98000
    assert chilometri("a 2 km dalla stazione") is None


def test_completa_solo_campi_vuoti():
    out = completa({"fuel": "benzina", "year": None, "mileage_km": None}, "Panda", "diesel anno 2011 140.000 km")
    assert out == {"year": 2011, "mileage_km": 140000}
