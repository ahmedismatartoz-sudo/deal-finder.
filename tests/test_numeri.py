from dealfinder.core.normalize import parse_int
from dealfinder.collectors.autoscout import _num


def test_numeri_all_italiana():
    assert parse_int("12.500,00") == 12500
    assert parse_int("12.500,00 €") == 12500
    assert parse_int("12500.00") == 12500
    assert parse_int("€ 8.900") == 8900
    assert parse_int("12,500") == 12500
    assert parse_int("150.000 km") == 150000
    assert parse_int("1.500 € trattabili, 2 proprietari") == 1500
    assert parse_int(None) is None and parse_int("abc") is None
    assert _num("12.500,00") == 12500 and _num("12500.00") == 12500
