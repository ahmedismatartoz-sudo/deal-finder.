from dealfinder.web import scovo
from dealfinder.zone import ZONE, tutte


def test_area_torino_e_zona():
    assert scovo.area({"province": "TO"}) == "torino" and scovo.area({"province": "CN"}) == "torino"
    assert scovo.area({"province": "MI"}) == "milano" and scovo.area({"province": "RA"}) == "ravenna"
    assert ZONE["torino"]["subito_regione"] == "piemonte" and "CN" in tutte()
