"""3/11 fase 1: il ricalcolo pesante non gira più nel sito (solo nel cron), salvo POLICY_RICALCOLO_SITO=1."""
import os


def test_ricalcolo_sito_spento_di_default():
    from dealfinder.web import app
    prima = os.environ.pop("POLICY_RICALCOLO_SITO", None)
    try:
        assert app._ricalcolo_sito() is False
        os.environ["POLICY_RICALCOLO_SITO"] = "1"
        assert app._ricalcolo_sito() is True
    finally:
        os.environ.pop("POLICY_RICALCOLO_SITO", None)
        if prima is not None:
            os.environ["POLICY_RICALCOLO_SITO"] = prima


def test_cron_ricalcolo_invariato():
    src = open(os.path.join(os.path.dirname(__file__), "..", "dealfinder", "pipeline", "collect.py"),
               encoding="utf-8").read()
    i = src.index('elif voce.startswith("ricalcolo")')
    blocco = src[i:i + 900]
    assert "ricalcolo.esegui(conn)" in blocco and "ricalcolo.utilitarie(conn" in blocco
    assert "RICALCOLO_SITO" not in src
