import os

from dealfinder.web import abbonamento as A


def test_paga_carta():
    os.environ.pop("ABBONAMENTO_OBBLIGATORIO", None)
    base = {"richiede_abbonamento": False, "con_password": True, "role": "commerciante"}
    assert A.puo_vedere(base)                                      # creato da noi: accesso completo
    assert not A.puo_vedere(dict(base, paga_carta=True))           # deve pagare: bloccato
    assert A.puo_vedere(dict(base, paga_carta=True, abbonamento_stato="active"))
    assert A.puo_vedere(dict(base, paga_carta=True, role="admin"))
