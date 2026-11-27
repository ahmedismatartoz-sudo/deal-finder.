"""27/11: lettura mirata di Ferrara (lettura_zona:) e valutazione dei privati di Ravenna e dintorni. Nessuna regola."""
import inspect


def test_lettura_zona_lavoro_registrato():
    from dealfinder.pipeline import coda_lavori as CL, lettura_completa as LC
    assert "lettura_zona:" in CL.SOLO_SITO
    assert "FE" in LC.ZONA_MIRATA and ("44121", "35") in LC.ZONA_MIRATA_AS
    assert callable(LC.lavoro_zona)


def test_utilitarie_anche_ravenna():
    from dealfinder.pricing import ricalcolo as R
    src = inspect.getsource(R._utilitarie)
    assert "RAVENNA" in src and '"prov": zona_val' in src


def test_lavoro_zona_lucchetto_occupato(monkeypatch):
    from dealfinder.pipeline import lettura_completa as LC, tempo_reale as tr
    monkeypatch.setattr(tr, "con_lucchetto", lambda nome, fn, *a: None)
    es = LC.lavoro_zona("lettura_zona:prova:FE")
    assert es["province"] == ["FE"] and "saltato" in es
