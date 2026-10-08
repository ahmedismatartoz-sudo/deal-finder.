import random
from datetime import datetime, timedelta

from dealfinder.collectors import meta, subito
from dealfinder.core.models import Listing
from dealfinder.core.normalize import fingerprint, normalize_fields
from dealfinder.pricing.engine import value_listing
from dealfinder.pricing.margin import DealerCosts, compute_margin, visible_for_dealers


def mk(i, price, year=2017, km=120_000, seller="privato", damage="nessuno", **kw):
    now = datetime(2026, 10, 1)
    l = Listing(source="test", source_id=str(i), url=f"https://x/{i}", make="Volkswagen", model="Golf",
                year=year, mileage_km=km, fuel="diesel", gearbox="manuale", power_kw=85,
                price_eur=price, seller_type=seller, province="MI", photos=["p"],
                first_seen_at=now - timedelta(days=40), **kw)
    l.damage_class = damage
    normalize_fields(l)
    return l


def market(n=40, seed=1):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        year = rnd.choice([2016, 2017, 2018])
        km = rnd.randint(90_000, 150_000)
        base = 13_000 * (0.92 ** (2017 - year) if year <= 2017 else 1.08 ** (year - 2017))
        base *= 1 - 0.012 * (km - 120_000) / 10_000
        seller = "commerciante" if i % 2 else "privato"
        price = int(base * (1.12 if seller == "commerciante" else 1.0) * rnd.uniform(0.95, 1.05))
        l = mk(i, price, year, km, seller)
        if i % 3 == 0:
            l.disappeared_at = l.first_seen_at + timedelta(days=25)
        out.append(l)
    return out


def test_price_flags():
    l = mk(1000, None, price_raw="10.000 € + IVA")
    assert l.price_eur == 12200 and "plus_iva" in l.price_flags
    l = mk(1001, 199, description="rata da 199 al mese")
    assert "leasing_o_rata" in l.price_flags and "prezzo_civetta" in l.price_flags


def test_missing_fields_reported():
    l = Listing(source="t", source_id="1", url="u", make="Fiat", price_eur=3000)
    normalize_fields(l)
    assert {"model", "year", "mileage_km", "fuel", "photos"} <= set(l.missing_fields)


def test_fingerprint_tolerates_small_km_diff():
    a, b = mk(1, 9000, km=120_100), mk(2, 9500, km=121_900)
    assert fingerprint(a) == fingerprint(b)


def test_valuation_reliable_on_good_market():
    mkt = market()
    target = mk(999, 9000)
    v = value_listing(target, mkt)
    assert v.n_comparables >= 5
    assert v.private_median and 11_500 < v.private_median < 14_500
    assert v.resale_prudent < v.dealer_median
    assert v.confidence == "affidabile", v.confidence_reasons


def test_unknown_condition_needs_check():
    v = value_listing(mk(999, 9000, damage="sconosciuto"), market())
    assert v.confidence == "da_verificare" and "stato_non_verificato" in v.confidence_reasons


def test_no_comparables():
    t = mk(999, 9000)
    t.model = "polo"
    v = value_listing(t, market())
    assert v.confidence == "da_verificare" and v.resale_prudent is None


def test_too_cheap_is_flagged():
    v = value_listing(mk(999, 4000), market())
    assert "prezzo_troppo_basso" in v.fraud_flags and v.confidence == "da_verificare"


def test_margin_and_thresholds():
    mkt = market()
    t = mk(999, 9000)
    v = value_listing(t, mkt)
    m = compute_margin(t, v, DealerCosts())
    expected_vat = 0   # default: tra privati, nessuna IVA
    assert m.vat_on_margin == expected_vat
    assert m.threshold == 3000
    assert m.status in ("opportunita", "scartata")
    assert m.net_margin == v.resale_prudent - 9000 - m.fixed_costs - m.contingency - m.vat_on_margin


def test_damaged_without_parts_is_to_verify():
    mkt = market()
    t = mk(999, 7000, damage="leggero")
    v = value_listing(t, mkt)
    m = compute_margin(t, v, DealerCosts())
    assert "manodopera_esclusa" in m.notes
    assert m.status in ("da_verificare", "scartata")


def test_seven_dealers_rule():
    assert visible_for_dealers(6) and not visible_for_dealers(7)


def test_subito_parse():
    item = {
        "urn": "id:ad:123:list:456", "subject": "Golf 1.6 TDI", "body": "ottime condizioni",
        "urls": {"default": "https://www.subito.it/auto/golf-milano-456.htm"},
        "geo": {"city": {"shortName": "MI"}, "town": {"value": "Milano"}, "region": {"value": "Lombardia"}},
        "advertiser": {"type": 0},
        "images": [{"cdnBaseUrl": "https://img/1"}],
        "features": {
            "/price": {"label": "Prezzo", "values": [{"key": "9500", "value": "9.500 €"}]},
            "/year": {"label": "Immatricolazione", "values": [{"value": "03/2017"}]},
            "/mileage": {"label": "Km", "values": [{"value": "120.000"}]},
            "/fuel": {"label": "Carburante", "values": [{"value": "Diesel"}]},
            "/gear": {"label": "Cambio", "values": [{"value": "Manuale"}]},
            "/power": {"label": "Potenza", "values": [{"value": "85 kW (115 CV)"}]},
            "/brand": {"label": "Marca", "values": [{"value": "Volkswagen"}]},
            "/model": {"label": "Modello", "values": [{"value": "Golf"}]},
        },
    }
    l = subito.parse_item(item)
    normalize_fields(l)
    assert (l.make, l.model, l.year, l.mileage_km, l.price_eur, l.power_kw) == ("volkswagen", "golf", 2017, 120000, 9500, 85)
    assert l.seller_type == "privato" and l.fuel == "diesel" and l.province == "MI"
    assert l.missing_fields == ["version_raw"]


def test_meta_parse():
    l = meta.parse_item({"id": "77", "marketplace_listing_title": "2015 Fiat Panda",
                         "listing_price": {"amount": "4500"}, "location": {"city": "Monza"}})
    assert l.year == 2015 and l.price_eur == 4500 and l.city == "Monza"
    assert l.url.endswith("/item/77/")


def test_meta_parse_crawloop_style_and_mileage():
    l = meta.parse_item({"id": "88", "listingTitle": "2016 Volkswagen Golf", "listingPrice": {"amount": "7900"},
                         "listingPhotos": ["https://f/1.jpg"], "vehicle": {"make": "Volkswagen", "model": "Golf",
                         "odometer": "120K km"}})
    assert (l.make, l.model, l.year, l.mileage_km, l.price_eur) == ("Volkswagen", "Golf", 2016, 120000, 7900)
    assert meta.parse_mileage("85.000 km") == 85000
    assert meta.parse_mileage("120 mila") == 120000
    assert meta.parse_mileage("50K miles") == 80450
    assert meta.parse_mileage({"value": 99000, "unit": "KILOMETERS"}) == 99000
