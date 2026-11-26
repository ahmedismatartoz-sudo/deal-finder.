"""26/11: diagnosi per provincia (SITO_PROVINCE) e conteggio del mercato esteso a NO/AL/VB/PC. Solo conteggi."""
from dealfinder.pipeline import conta_mercato as CM
from dealfinder.web import sito_regole as SR


def test_province_conteggio():
    assert "NO" in CM.PROVINCE_ALTRE[2] and "PC" in CM.PROVINCE_ALTRE[8]
    assert "LC" in SR.PROVINCE_DIAGNOSI and "AL" in SR.PROVINCE_DIAGNOSI


def test_funnel_province():
    a = {"id": 1, "_r": {"provincia": "LC"}}
    b = {"id": 2, "_r": {"provincia": "LC"}}
    c = {"id": 3, "_r": {"provincia": "PV"}}
    fun = SR.Funnel()
    fun.tolti = {"truffa": [2], "zona": []}
    out = SR.funnel_province([a, b, c], [a, c], fun)
    assert out["LC"]["entrate"] == 2 and out["LC"]["sito"] == 1 and out["LC"]["uscite"] == {"truffa": 1}
    assert out["PV"]["sito"] == 1 and out["MI"]["entrate"] == 0
