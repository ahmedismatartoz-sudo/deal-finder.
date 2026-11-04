"""5/11: fascia di prezzo stretta ancora oltre 400 risultati: si divide per anni fino all'anno singolo."""
import sys
import types

sys.modules.setdefault("httpx", types.SimpleNamespace(Client=object, get=None))


def test_divide_per_anni(monkeypatch):
    from dealfinder.collectors import autoscout as A
    c = A.AutoScoutCollector.__new__(A.AutoScoutCollector)
    c.blocked = c.stop = False
    chiamate = []

    def pagina(url):
        chiamate.append(url)
        anni = url.split("fregfrom=")[1]
        singolo = "fregto=" in url and anni.split("&")[0] == url.split("fregto=")[1].split("&")[0]
        return [], (100 if singolo or "fregto=2030" not in url and "fregfrom=2020" not in url else 900), True
    c._page = pagina
    c.search_url = lambda lo, hi, page, ctype, years: f"x?fregfrom={years[0] if years else 2007}" + (f"&fregto={years[1]}" if years else "")
    c._emit = lambda items, ct, s: iter(())
    list(c._band(15875, 15937, "C", "commerciante", (2020, 2030)))
    assert any("fregfrom=2020&fregto=2023" in u for u in chiamate)
    assert len(chiamate) > 3
