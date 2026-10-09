"""Test dei servizi Vendi e Ricambi (senza rete: targa e ricerca simulate)."""
from dealfinder.ai import plate
from dealfinder.core.models import Listing


def test_plate_parse_openapi_shape():
    v = plate.parse_vehicle({"data": {"CarMake": "VOLKSWAGEN", "CarModel": "GOLF", "Version": "1.6 TDI Highline",
                                      "FuelType": "Diesel", "PowerCV": "115", "EngineSize": "1598",
                                      "RegistrationYear": "2017"}})
    assert v["make"] == "VOLKSWAGEN" and v["model"] == "GOLF" and v["year"] == 2017
    assert v["power_kw"] == 85 and v["engine_cc"] == 1598
    assert plate.parse_vehicle({"CarMake": "FIAT"}) is None


def test_plate_format():
    assert plate.valid_plate("ab 123 cd") and plate.normalize_plate("ab-123 cd") == "AB123CD"
    assert not plate.valid_plate("123ABC") and not plate.valid_plate("")


def test_vendi_listing_and_prices():
    from dealfinder.servizi import vendi
    l = vendi.to_listing({"make": "MERCEDES-BENZ", "model": "CLASSE A", "year": 2018, "fuel": "Diesel"}, 90000)
    assert (l.make, l.model, l.fuel, l.mileage_km) == ("mercedes", "classe-a", "diesel", 90000)
    assert vendi.round_price(7963) == 7950 and vendi.round_price(1234) == 1240
    market = [Listing(source="subito", source_id=str(i), url="", make="mercedes", model="classe-a", year=2018,
                      mileage_km=80000 + i * 2000, fuel="diesel", price_eur=17000 + i * 150, seller_type="privato",
                      region="Lombardia") for i in range(12)]
    pr = vendi.prices(l, market, None)
    assert pr["ok"] and pr["veloce"] < pr["giusto"] < pr["alto"]
    assert vendi.prices(l, [], None)["ok"] is False


def test_vendi_run_without_ai():
    from dealfinder.servizi import vendi
    assert vendi.run(None, "XX", 1000)["ok"] is False
    assert vendi.run(None, "AB123CD", 1000, lookup=lambda t: None)["ok"] is False
    t = vendi.fallback_text({"make": "FIAT", "model": "PANDA"}, Listing(source="s", source_id="1", url="", year=2015,
                                                                       mileage_km=98000, fuel="benzina"), None)
    assert "Fiat Panda" in t["titolo"] and "98.000 km" in t["descrizione"]


def test_ricambi_run_and_sorting():
    from dealfinder.servizi import ricambi
    data = {"offers": [{"type": "aftermarket", "price_eur": 90, "shipping_eur": 10, "seller": "A", "url": "https://a",
                        "fits_exact_vehicle": True},
                       {"type": "originale", "price_eur": 240, "shipping_eur": 0, "seller": "B", "url": "https://b",
                        "fits_exact_vehicle": True},
                       {"type": "usato", "price_eur": 40, "shipping_eur": 15, "seller": "C", "url": "https://c",
                        "fits_exact_vehicle": True},
                       {"type": "boh", "price_eur": 1, "url": "https://x"}]}
    offers = ricambi.clean_offers(data, set())
    assert [o["totale"] for o in offers] == [55, 100, 240]
    out = ricambi.run(None, "AB123CD", ["faro anteriore sx", ""],
                      lookup=lambda t: {"make": "FIAT", "model": "PANDA", "year": 2015},
                      searcher=lambda v, p: {"offerte": offers, "codici_oem": ["51787"]})
    assert out["ok"] and len(out["pezzi"]) == 1
    assert out["pezzi"][0]["migliore"]["totale"] == 55 and out["risparmio_totale"] == 185
    assert ricambi.run(None, "AB123CD", [], lookup=lambda t: {})["ok"] is False


def test_plate_parse_regcheck_xml():
    xml = ('<?xml version="1.0"?><Vehicle><vehicleJson>{"Description":"Alfa Romeo 147","RegistrationYear":"2005",'
           '"CarMake":{"CurrentTextValue":"ALFA ROMEO"},"CarModel":{"CurrentTextValue":"147"},'
           '"EngineSize":{"CurrentTextValue":"1598"},"FuelType":{"CurrentTextValue":"Benzina"},'
           '"Version":"147 1.6 16V TS (105 CV) 5p","PowerCV":"105","PowerKW":"77"}</vehicleJson></Vehicle>')
    v = plate.parse_regcheck(xml)
    assert v["make"] == "ALFA ROMEO" and v["model"] == "147" and v["year"] == 2005
    assert v["fuel"] == "Benzina" and v["power_kw"] == 77 and v["engine_cc"] == 1598
    assert plate.parse_regcheck("<x/>") is None
