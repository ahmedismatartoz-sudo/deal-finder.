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


def test_km_dei_lavori_non_sono_i_km_dell_auto_17_11():
    # km dei lavori, degli intervalli e delle gomme: non sono la percorrenza
    assert chilometri("tagliando fatto a 90.000 km") is None
    assert chilometri("distribuzione cambiata ai 120 mila km") is None
    assert chilometri("tagliandi ogni 15.000 km") is None
    assert chilometri("percorrenza annua 10.000 km") is None
    assert chilometri("gomme nuove con 5.000 km") is None
    assert chilometri("ultimo tagliando 8.000 km fa") is None
    # con i km veri accanto ora si legge il valore giusto (prima: due valori, campo vuoto)
    assert chilometri("128.000 km reali, distribuzione fatta a 90.000 km") == 128000
    assert chilometri("Golf km 145.000, tagliando a km 140.000") == 145000
    # restano i km dell'auto anche vicino alle parole dei lavori
    assert chilometri("tagliandata 128.000 km") == 128000
    assert chilometri("auto tagliandata con 98.000 km certificati") == 98000
    assert chilometri("ha 128.000 km certificati") == 128000
    assert chilometri("Gomme nuove, 75.000 km") == 75000


class _ConnFinta:
    def __init__(self, righe):
        self.righe, self.scritti, self.letto = righe, [], False

    def execute(self, sql, args=None):
        if sql.lstrip().startswith("SELECT"):
            out = [] if self.letto else self.righe
            self.letto = True
            return type("R", (), {"fetchall": lambda s: out})()
        self.scritti.append((sql, args))

    def commit(self):
        pass


def test_rileggi_km_gia_presi_dal_testo():
    import json
    from dealfinder.core.dal_testo import rileggi_km_db
    righe = [
        {"id": 1, "title": "Golf", "description": "tagliando fatto a 90.000 km", "version_raw": None, "mileage_km": 90000,
         "field_origin": {"mileage_km": "dal_testo"}},
        {"id": 2, "title": "Panda", "description": "120.000 km, distribuzione a 90.000 km", "version_raw": None,
         "mileage_km": 90000, "field_origin": json.dumps({"mileage_km": "dal_testo"})},
        {"id": 3, "title": "Polo", "description": "ha 60.000 km", "version_raw": None, "mileage_km": 60000,
         "field_origin": {"mileage_km": "dal_testo"}},
    ]
    c = _ConnFinta(righe)
    st = rileggi_km_db(c)
    assert st == {"km_riletti": 3, "km_corretti": 1, "km_tolti": 1}
    per_id = {a[-1]: a for _, a in c.scritti}
    assert per_id[1][0] is None and "mileage_km" not in json.loads(per_id[1][1])      # km incerto: campo vuoto
    assert per_id[2][0] == 120000 and json.loads(per_id[2][1])["km_riletto_v3"] == 1
    assert len(per_id[3]) == 2 and json.loads(per_id[3][0])["km_riletto_v3"] == 1     # uguale: solo il segno
