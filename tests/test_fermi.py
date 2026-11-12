"""12/11 sera: contatti fermi nel database, mai quelli che hanno ricevuto oggi, mai disiscritti/esclusi."""
from dealfinder.marketing import su_misura as SM
from dealfinder.marketing.invii import impronta


def test_scegli_fermi_salta_oggi_disiscritti_ed_esclusi():
    righe = [
        {"chiave": "a", "email": ["oggi@salone1.it"], "prov": "MI"},
        {"chiave": "b", "email": ["via@salone2.it"], "prov": "MI"},
        {"chiave": "c", "email": ["info@salone3.it", "altro@salone3.it"], "prov": "MB"},
        {"chiave": "d", "email": ["info@loconte.it"], "prov": "MI"},
        {"chiave": "e", "email": ["info@officinemulino.it"], "prov": "MI"},
    ]
    out = SM.scegli_fermi(righe, {impronta("via@salone2.it")}, {"oggi@salone1.it"}, 10)
    assert [r["email"] for r in out] == ["info@salone3.it"]


def test_scegli_fermi_rispetta_il_totale():
    righe = [{"chiave": str(i), "email": [f"info@s{i}.it"], "prov": "MI"} for i in range(20)]
    assert len(SM.scegli_fermi(righe, set(), set(), 7)) == 7

