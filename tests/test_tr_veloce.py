"""16/11 sera: giro veloce AutoScout24 — il fuori zona non apre il dettaglio e non fa rileggere tutte le pagine."""
from types import SimpleNamespace
from dealfinder.pipeline import tempo_reale as T


class Conn:
    def execute(self, *a, **k):
        return SimpleNamespace(fetchall=lambda: [])


def test_fuori_zona_senza_dettaglio_e_stop_al_giro_dopo(monkeypatch):
    pagine = {1: [{"id": "a1", "pv": "MI"}, {"id": "a2", "pv": "RM"}], 2: [{"id": "a3", "pv": "FI"}]}
    lette = []

    def get(client, url, nome):
        p = int(url.rsplit("page=", 1)[1])
        lette.append(p)
        return SimpleNamespace(status_code=200, text=str(p))

    from dealfinder.pipeline import as_riprova
    from dealfinder.collectors import autoscout, base
    monkeypatch.setattr(as_riprova, "get", get)
    monkeypatch.setattr(base, "next_data", lambda t: {"props": {"pageProps": {"listings": pagine.get(int(t), [])}}})
    monkeypatch.setattr(autoscout, "parse_item", lambda it: SimpleNamespace(source_id=it["id"], province=it["pv"], seller_type=None))
    monkeypatch.setattr(T, "_client_as", lambda: None)
    monkeypatch.setattr(T, "_as_completa", lambda c, out: out)
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    T._AS_VISTI.clear()
    out = T._autoscout_nuovi(Conn())
    assert [l.source_id for l in out] == ["a1"]          # RM e FI fuori zona: niente dettaglio
    lette.clear()
    assert T._autoscout_nuovi(Conn()) == []              # giro dopo: tutto già visto
    assert lette == [1]                                   # si ferma alla prima pagina
