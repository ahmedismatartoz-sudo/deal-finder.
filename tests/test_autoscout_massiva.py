import json
import re

from dealfinder.collectors.autoscout import AutoScoutCollector, province_from_cap


class _Resp:
    def __init__(self, text, code=200):
        self.text, self.status_code = text, code


class _FakeClient:
    """Finto AutoScout24: 1.000 annunci da 500 a 10.499 €, al massimo 20 pagine da 20 per ricerca."""
    def __init__(self):
        self.cars = [{"id": f"a{i}", "url": f"/annunci/a{i}", "price": {"priceFormatted": f"€ {500 + i * 10}"},
                      "tracking": {"price": str(500 + i * 10), "mileage": "100000", "firstRegistration": "05-2012"},
                      "vehicle": {"make": "Fiat", "model": "Panda"}, "location": {"zip": "20100", "city": "Milano"}}
                     for i in range(1000)]
        self.calls = 0

    def get(self, url):
        self.calls += 1
        q = dict(re.findall(r"[?&](\w+)=([^&]*)", url))
        lo, hi, page = int(q["pricefrom"]), int(q["priceto"]), int(q["page"])
        sel = [c for c in self.cars if lo <= int(c["tracking"]["price"]) <= hi]
        items = sel[(page - 1) * 20: page * 20] if page <= 20 else []
        data = {"props": {"pageProps": {"listings": items, "numberOfResults": len(sel)}}}
        return _Resp(f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>')


def test_massiva_legge_tutto_senza_doppioni():
    c = AutoScoutCollector(client=_FakeClient())
    c.pause = lambda: None
    got = list(c.search({"massiva": True, "min_price": 500, "max_price": 12000, "step": 12000,
                         "venditori": [("P", "privato")]}))
    ids = [l.source_id for l in got]
    assert len(ids) == len(set(ids)) == 1000          # tutti, nessun doppione, nonostante il tetto di 400
    assert all(l.seller_type == "privato" and l.province == "MI" for l in got)


def test_obiettivo_ferma_la_raccolta():
    c = AutoScoutCollector(client=_FakeClient())
    c.pause = lambda: None
    n = 0
    for _ in c.search({"massiva": True, "min_price": 500, "max_price": 12000, "venditori": [("P", "privato")]}):
        n += 1
        if n >= 50:
            c.stop = True
    assert n < 100


def test_cap_fuori_lombardia():
    assert province_from_cap("28100") == "NO" and province_from_cap("29121") == "PC"
    assert province_from_cap("23900") == "LC" and province_from_cap("20900") == "MB"
