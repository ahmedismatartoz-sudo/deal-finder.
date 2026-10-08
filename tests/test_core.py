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


def test_private_resale_and_weights():
    from datetime import timedelta
    from dealfinder.pricing.engine import PricingConfig, comparable_weight
    v = value_listing(mk(999, 9000), market())
    assert v.resale_prudent == v.resale_prudent_private            # default: vende a privati
    assert v.resale_prudent_private < v.resale_prudent_dealer
    cfg = PricingConfig()
    fresh_sold = mk(1, 9000); fresh_sold.disappeared_at = fresh_sold.first_seen_at + timedelta(days=10)
    stale = mk(2, 9000); stale.first_seen_at = stale.first_seen_at - timedelta(days=200)
    from datetime import datetime, timezone
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert comparable_weight(fresh_sold, cfg, now) > comparable_weight(stale, cfg, now)


def test_brightdata_parse():
    from dealfinder.collectors.brightdata import parse_row
    row = {"product_id": 5, "url": "https://www.facebook.com/marketplace/item/5/?x=1", "title": "2015 Fiat Panda",
           "description": "Panda 1.2, 98.000 km", "final_price": 4500, "currency": "EUR", "country_code": "IT",
           "images": ["https://x/1.jpg"], "car_miles": 60000, "is_sold": False}
    l = parse_row(row)
    assert (l.year, l.mileage_km, l.price_eur, l.url) == (2015, 98000, 4500, "https://www.facebook.com/marketplace/item/5/")
    assert parse_row({**row, "is_sold": True}) is None
    assert parse_row({**row, "currency": "USD"}) is None
    assert parse_row({**row, "final_price": 25000}) is None


def test_make_model_from_title():
    from dealfinder.core.vehicles import make_model_from_title as mm
    assert mm("2016 Volkswagen Golf 1.6 TDI") == ("volkswagen", "golf")
    assert mm("Fiat Panda 1.2 2015") == ("fiat", "panda")
    assert mm("2019 Mercedes-Benz Classe A 180d") == ("mercedes-benz", "classe-a")
    assert mm("BMW Serie 1 118d") == ("bmw", "serie-1")
    assert mm("Alfa Romeo Giulietta") == ("alfa-romeo", "giulietta")
    assert mm("Vendo auto ottimo stato") == (None, None)


def test_asis_market_and_problem_hint():
    from dealfinder.pricing.engine import asis_market
    mkt = market()
    damaged = []
    for i, p in enumerate((5200, 5600, 6000, 6400)):
        d = mk(500 + i, p, description="incidentata frontale")
        assert d.damage_declared is True          # riconosciuta dalle parole chiave
        damaged.append(d)
    target = mk(999, 4000, damage="medio")
    med, n = asis_market(target, mkt + damaged)
    assert n == 4 and 5600 <= med <= 6000
    v = value_listing(mk(998, 9000), mkt + damaged)
    assert all(c["price"] > 7000 for c in v.comparables_used)   # le danneggiate non entrano nei confronti sani


def test_price_model_train_predict_prescreen():
    import random as _r
    from dealfinder.pricing.model import predict, prescreen, train
    rnd = _r.Random(3)
    data = []
    for i in range(400):
        year = rnd.choice(range(2012, 2022))
        km = rnd.randint(20_000, 220_000)
        price = int(20000 * (0.88 ** (2026 - year)) * (1 - 0.01 * km / 10_000) * rnd.uniform(0.92, 1.08))
        l = mk(10_000 + i, price, year=year, km=km, seller="privato" if i % 2 else "commerciante")
        data.append(l)
    model = train(data, ref_year=2026)
    assert model["metrics"]["median_abs_pct_error"] < 0.08
    t = mk(1, 1000, year=2018, km=100_000)
    p = predict(model, t)
    truth = 20000 * 0.88 ** 8 * 0.9
    assert abs(p["p50"] - truth) / truth < 0.12 and p["level"] in ("mmf", "mm")
    cheap = mk(2, int(truth * 0.45), year=2018, km=100_000)   # margine potenziale > 80% della soglia
    fair = mk(3, int(truth), year=2018, km=100_000)
    assert prescreen(model, cheap)["esito"] == "interessante"
    assert prescreen(model, fair)["esito"] == "non_interessante"
    damaged = mk(4, int(truth * 0.75), year=2018, km=100_000, description="incidentata")
    assert prescreen(model, damaged)["con_problemi"] is True
