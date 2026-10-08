"""Test di analisi danni, agente ricambi (con ricerca simulata), schede, accesso, backtest."""
from datetime import datetime

from dealfinder.ai import analyze, parts_agent
from dealfinder.ai.damage import classify, hidden_parts, sanitize_items
from dealfinder.ai.client import extract_json
from dealfinder.core.models import DamageItem, Listing
from dealfinder.pricing import report
from dealfinder.pricing.backtest import evaluate, suggested_discount
from dealfinder.pricing.engine import value_listing
from dealfinder.pricing.margin import DealerCosts, compute_margin
from dealfinder.web import auth, cards
from tests.test_core import market, mk


def test_extract_json_variants():
    assert extract_json('Ecco:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('testo {"b": [1,2]} fine') == {"b": [1, 2]}


def test_damage_classification():
    assert classify(["telaio_longheroni"], []) == "grave"
    assert classify(["airbag_esplosi"], []) == "alto_rischio"
    assert classify(["non_parte"], []) == "alto_rischio"
    assert classify([], []) == "nessuno"
    assert classify([], [{"part": "paraurti_anteriore", "severity": "leggero"}]) == "leggero"
    assert classify([], [{"part": "cofano", "severity": "medio"}]) == "medio"
    assert classify([], [{"part": "frizione", "severity": "medio"}]) == "medio"
    assert classify([], [{"part": "motore_sostituzione"}]) == "alto_rischio"


def test_sanitize_drops_unknown_parts():
    out = sanitize_items([{"part": "motore", "severity": "grave"},
                          {"part": "faro_anteriore", "side": "sx", "severity": "boh", "action": "x"}])
    assert len(out) == 1 and out[0]["severity"] == "medio" and out[0]["action"] == "sostituire"


def test_hidden_parts_and_adas():
    items = [{"part": "paraurti_anteriore", "action": "sostituire", "severity": "medio"}]
    assert "calibrazione_radar_adas" in hidden_parts(items, 2020)
    assert "calibrazione_radar_adas" not in hidden_parts(items, 2015)
    assert "traversa_anteriore" in hidden_parts(items, 2015)


def test_merge_damage_unknown_when_photos_insufficient():
    cls, items, severe = analyze.merge_damage({}, {"damage_visible": "incerto", "exterior_fully_visible": False})
    assert cls == "sconosciuto" and not items
    cls, _, _ = analyze.merge_damage({}, {"damage_visible": "no", "exterior_fully_visible": True})
    assert cls == "nessuno"


def test_apply_extract_fills_only_missing_and_validates():
    l = Listing(source="t", source_id="1", url="u", make="fiat", model=None, year=2015, price_eur=4000)
    origin = analyze.apply_extract(l, {"make": "Audi", "model": "Panda", "year": 2019, "mileage_km": 9_999_999,
                                       "power_cv": 69, "price_notes": ["rata"]})
    assert l.make == "fiat" and l.model == "panda" and l.year == 2015
    assert l.mileage_km is None and l.power_kw == 51
    assert "leasing_o_rata" in l.price_flags and origin["model"] == "dedotto_ai"


def test_parts_price_range():
    res = {"offers": [
        {"type": "aftermarket", "price_eur": 80, "fits_exact_vehicle": True},
        {"type": "aftermarket", "price_eur": 100, "fits_exact_vehicle": True},
        {"type": "aftermarket", "price_eur": 140, "fits_exact_vehicle": True},
        {"type": "originale", "price_eur": 320, "fits_exact_vehicle": True},
        {"type": "aftermarket", "price_eur": 20, "fits_exact_vehicle": False},
    ]}
    r = parts_agent.price_range(res, "aftermarket")
    assert r["low"] == 80 and r["high"] == 120 and r["status"] == "ok"
    r = parts_agent.price_range(res, "originale")   # una sola offerta originale: ripiega su aftermarket (≥2)
    assert r["type"] == "aftermarket"
    assert parts_agent.price_range({"offers": []})["status"] == "non_trovato"


def test_estimate_parts_with_fake_search_and_cache(monkeypatch=None):
    calls = []

    def fake_search(vehicle, part, side, usage_sink=None, listing_id=None):
        calls.append(part)
        base = 200 if "paraurti" in part else 60
        return {"part": part, "side": side, "label": part, "oem_codes": [],
                "offers": [{"type": "aftermarket", "price_eur": base, "fits_exact_vehicle": True, "url": "u", "seller": "s"},
                           {"type": "aftermarket", "price_eur": base * 1.5, "fits_exact_vehicle": True, "url": "u", "seller": "s"}]}

    cache = {}
    orig = parts_agent.search_part
    parts_agent.search_part = fake_search
    try:
        items = [{"part": "paraurti_anteriore", "side": None, "action": "sostituire", "severity": "medio"},
                 {"part": "cofano", "side": None, "action": "riparare", "severity": "leggero"}]
        vehicle = {"make": "vw", "model": "golf", "search_name": "VW Golf VII"}
        get = lambda k, p: cache.get((k, p))
        put = lambda k, p, r: cache.__setitem__((k, p), r)
        r1 = parts_agent.estimate_parts(vehicle, items, 2019, "aftermarket", get, put)
        n = len(calls)
        r2 = parts_agent.estimate_parts(vehicle, items, 2019, "aftermarket", get, put)
    finally:
        parts_agent.search_part = orig
    assert len(calls) == n            # seconda volta tutto dalla cache
    assert r1 == r2
    assert r1["parts_cost_low"] == 200                       # solo il paraurti visibile, al minimo
    assert r1["parts_cost_high"] > r1["parts_cost_low"]      # include traversa, staffe, sensori, ADAS
    assert "cofano" in r1["repair_only_no_parts"] and r1["complete"]


def test_report_texts():
    mkt = market()
    t = mk(999, 9000, damage="leggero")
    t.damage_items = [DamageItem(part="paraurti_anteriore", side=None, action="sostituire", severity="medio")]
    v = value_listing(t, mkt)
    mot = report.motivation(t, v, {"parts_cost_low": 200, "parts_cost_high": 450, "lines": [{"part": "x"}]})
    assert any("sotto la mediana" in m for m in mot) and any("Ricambi" in m for m in mot)
    chk = report.checks(t, v, None)
    assert any("Spessimetro" in c for c in chk) and any("traversa" in c for c in chk)
    assert report.reason_text("pochi_confronti:3").startswith("Solo 3")


def test_auth_password_and_token():
    h = auth.hash_password("segreta123")
    assert auth.verify_password("segreta123", h) and not auth.verify_password("altra", h)
    t = auth.make_token(5, "admin")
    assert auth.read_token(t)["id"] == 5
    assert auth.read_token(t[:-2] + "00") is None
    assert auth.read_token("garbage") is None


def _rows_for(listing, v, parts=None):
    row = {k: getattr(listing, k) for k in ("source", "source_id", "url", "title", "description", "make", "model",
                                             "version_raw", "year", "mileage_km", "fuel", "gearbox", "power_kw",
                                             "price_raw", "price_eur", "seller_type", "city", "province", "region",
                                             "damage_declared", "damage_class")}
    row.update(id=1, price_flags=listing.price_flags, missing_fields=listing.missing_fields,
               damage_items=[d.__dict__ for d in listing.damage_items], first_seen_at=datetime(2026, 10, 1))
    val = {k: getattr(v, k) for k in ("engine_version", "private_median", "dealer_median", "resale_prudent",
                                       "resale_median", "comparable_level", "n_comparables", "dispersion",
                                       "liquidity_days", "discount_vs_private", "confidence",
                                       "confidence_reasons", "fraud_flags", "comparables_used")}
    val.update(parts_detail=parts, motivation=["m"], checks=["c"], created_at=None)
    return row, val


def test_card_uses_dealer_costs_and_parts_preference():
    t = mk(999, 9000)
    v = value_listing(t, market())
    row, val = _rows_for(t, v)
    c1 = cards.build(row, val, ["p1"], None, full=True)
    cheap = {"transport_eur": 0, "paperwork_eur": 0, "preparation_eur": 0, "warranty_reserve_eur": 0}
    c2 = cards.build(row, val, ["p1"], cheap)
    assert c2["net_margin"] > c1["net_margin"]
    assert c1["costs"]["acquisto"] == 9000 and c1["photos"] == ["p1"]

    t2 = mk(998, 7000, damage="leggero")
    t2.damage_items = [DamageItem(part="faro_anteriore", side="sx")]
    v2 = value_listing(t2, market())
    parts = {"parts_cost_low": 100, "parts_cost_high": 150,
             "by_type": {"aftermarket": {"high": 150, "complete": True},
                         "originale": {"high": 600, "complete": True},
                         "usato": {"high": 90, "complete": False}}}
    row2, val2 = _rows_for(t2, v2, parts)
    a = cards.build(row2, val2, [], None, preferred="aftermarket")
    o = cards.build(row2, val2, [], None, preferred="originale")
    u = cards.build(row2, val2, [], None, preferred="usato")
    assert a["parts_cost"] == 150 and o["parts_cost"] == 600 and a["net_margin"] > o["net_margin"]
    assert u["status"] in ("da_verificare", "scartata")


def test_backtest_and_discount():
    mkt = market(60, seed=3)
    res = evaluate(mkt[:20], lambda l: mkt)
    assert res["n_cases"] > 0 and res["median_abs_pct_error"] < 0.15
    assert suggested_discount([(10000, 9300)] * 12) == 0.07
    assert suggested_discount([(10000, 9300)] * 3) is None
