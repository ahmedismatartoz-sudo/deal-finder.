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


def test_sigle_motore_1_11():
    from dealfinder.core.dal_testo import alimentazione, completa
    assert alimentazione("BMW 118d Advantage") == "diesel"
    assert alimentazione("Mercedes A 180 d Sport") == "diesel"
    assert alimentazione("Seat Ibiza 1.0 EcoTSI 115 CV") == "benzina"
    assert alimentazione("BMW 320i Touring") == "benzina"
    assert alimentazione("Peugeot 208 d'occasione tenuta bene") is None
    assert completa({"version_raw": "X1 sDrive18d"}, "BMW X1", None) == {"fuel": "diesel"}


def test_cambio_e_potenza_1_11():
    from dealfinder.core.dal_testo import cambio, potenza_kw, completa
    assert cambio("A3 1.4 TFSI 125 CV S tronic") == "automatico"
    assert cambio("cambio manuale 6 marce") == "manuale"
    assert cambio("cambio manuale e cambio automatico") is None
    assert cambio("clima automatico, libretto e manuale") is None
    assert potenza_kw("1.4 TFSI 125 CV") == 92
    assert potenza_kw("92 kW (125 CV)") == 92
    assert potenza_kw("110 CV e 150 CV") is None
    assert completa({"gearbox": None, "power_kw": None, "fuel": "benzina", "year": 2015, "mileage_km": 1},
                    "Golf 1.4 TSI 125 CV DSG", None) == {"gearbox": "automatico", "power_kw": 92}
    assert completa({"fuel": "benzina", "year": 2015, "mileage_km": 1}, "Golf 1.4 TSI 125 CV DSG", None) == {}
