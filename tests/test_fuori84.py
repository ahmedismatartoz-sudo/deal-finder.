"""16/11: 1.495 nuovi, mai chi era nella lista 84 (ricostruita per esclusione)."""
from dealfinder.marketing import su_misura as SM
from dealfinder.marketing.invii import impronta, norm_email


def p(e):
    return impronta(norm_email(e))[:16]


def test_fuori84_prende_solo_chi_aveva_la_75_o_oltre_il_confine():
    righe = ([{"chiave": f"m{i}", "nome": f"S{i}", "email": [f"info@mi{i}.it"], "prov": "MI"} for i in range(5)]
             + [{"chiave": "a75", "nome": "A", "email": ["info@a75.it"], "prov": "MI"},
                {"chiave": "mix", "nome": "M", "email": ["x@mix.it", "y@mix.it"], "prov": "MI"},
                {"chiave": "lon", "nome": "L", "email": ["info@lontano.it"], "prov": "BS"},
                {"chiave": "esc", "nome": "Loconte auto", "email": ["info@lc.it"], "prov": "BS"}])
    oggi = {p("info@a75.it"), p("x@mix.it")}
    scelti, info = SM.scegli_fuori84(righe, set(), oggi, n84=3, margine=1, totale=100)
    em = {s["email"] for s in scelti}
    # tutti i MI senza la 75 erano candidati alla lista 84 → fuori; mix ha un indirizzo non della 75 → fuori
    assert em == {"info@a75.it", "info@lontano.it"}


def test_fuori84_senza_abbastanza_per_rifare_la_lista_non_prende_nessuno_dal_resto():
    righe = [{"chiave": "lon", "nome": "L", "email": ["info@lontano.it"], "prov": "BS"},
             {"chiave": "a75", "nome": "A", "email": ["info@a75.it"], "prov": "MI"}]
    scelti, info = SM.scegli_fuori84(righe, set(), {p("info@a75.it")}, n84=5, margine=0)
    assert [s["email"] for s in scelti] == ["info@a75.it"]


def test_fuori84_bloccati_e_totale():
    righe = [{"chiave": str(i), "nome": "x", "email": [f"info@s{i}.it"], "prov": "MI"} for i in range(10)]
    oggi = {p(f"info@s{i}.it") for i in range(10)}
    scelti, _ = SM.scegli_fuori84(righe, {impronta("info@s0.it")}, oggi, n84=1, margine=0, totale=4)
    assert len(scelti) == 4 and "info@s0.it" not in {s["email"] for s in scelti}
