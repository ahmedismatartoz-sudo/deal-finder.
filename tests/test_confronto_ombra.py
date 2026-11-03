"""Confronto in ombra di due regole di prezzo (dealfinder/pricing/confronto_ombra.py), senza database."""
import os

from dealfinder.pricing import confronto_ombra as O
from dealfinder.pricing import policy_v2 as P


def _auto(i, prezzo=8000):
    return {"id": i, "make": "Fiat", "model": "Panda", "year": 2015, "price_eur": prezzo}


def test_entrano_escono_mediana_e_piu_grandi():
    campione = [(_auto(i), [], 1500) for i in range(6)]
    a = {0: ("opportunita", 2000), 1: ("non_conveniente", 900), 2: ("candidato", 1600), 3: ("opportunita", 3000),
         4: ("dati_insufficienti", None), 5: ("non_conveniente", 100)}
    b = {0: ("opportunita", 2500), 1: ("candidato", 1700), 2: ("non_conveniente", 1200), 3: ("opportunita", 3000),
         4: ("dati_insufficienti", None), 5: ("non_conveniente", -2900)}
    fa = lambda t, m, s: {"categoria": a[t["id"]][0], "margine": a[t["id"]][1]}
    fb = lambda t, m, s: {"categoria": b[t["id"]][0], "margine": b[t["id"]][1]}
    r = O.confronta(campione, fa, fb, categorie_sito={"opportunita", "candidato"})
    assert (r["valutate"], r["entrano"], r["escono"]) == (6, 1, 1)
    assert r["entrano_id"] == [1] and r["escono_id"] == [2]
    assert (r["sito_a"], r["sito_b"]) == (3, 3)
    assert r["margine_delta_mediana"] == 0 and r["margine_delta_media"] == -420.0 and r["margine_cambiati"] == 4 and r["con_margine"] == 5
    assert [x["id"] for x in r["piu_grandi"]] == [5, 1, 0, 2, 3]
    assert r["passaggi"]["non_conveniente -> candidato"] == 1
    assert "entrano 1" in O.testo(dict(r, policy="x"))


def test_errore_di_una_valutazione_non_ferma_le_altre():
    def rotta(t, m, s):
        if t["id"] == 1:
            raise ValueError("dati strani")
        return {"categoria": "opportunita", "margine": 2000}
    r = O.confronta([(_auto(i), [], 1500) for i in range(3)], rotta, rotta, categorie_sito={"opportunita"})
    assert r["valutate"] == 2 and r["n_errori"] == 1 and r["errori"][0]["id"] == 1
    assert r["entrano"] == r["escono"] == 0 and r["margine_delta_mediana"] == 0


def test_config_da_dict_campi_e_ambiente():
    prima = os.environ.get("POLICY_COSTO_FISSO")
    cfg, env = O.config_da_dict({"costo_fisso": 600, "POLICY_KM_TOL": "30000", "SOGLIE_PV": "7000:1500"})
    assert cfg.costo_fisso == 600 and env.get("POLICY_KM_TOL") == "30000"   # 4/11: le regole non si leggono più dall'ambiente
    assert env == {"POLICY_KM_TOL": "30000", "SOGLIE_PV": "7000:1500"}
    assert os.environ.get("POLICY_KM_TOL") is None and os.environ.get("POLICY_COSTO_FISSO") == prima
    try:
        O.config_da_dict({"campo_che_non_esiste": 1})
        raise AssertionError("doveva rifiutare")
    except ValueError as e:
        assert "campo_che_non_esiste" in str(e)


def test_valutatore_da_dict_usa_la_config_e_rimette_l_ambiente():
    visti = []
    vera = P.valuta
    P.valuta = lambda t, m, s, cfg: visti.append((cfg.costo_fisso, os.environ.get("SOGLIE_PV"))) or {"categoria": "x"}
    try:
        f = O.valutatore({"costo_fisso": 777, "SOGLIE_PV": "1:2"})
        assert f(_auto(1), [], 1500) == {"categoria": "x"}
    finally:
        P.valuta = vera
    assert visti == [(777, "1:2")] and os.environ.get("SOGLIE_PV") is None
    g = lambda t, m, s: {"categoria": "y"}
    assert O.valutatore(g) is g


def test_valuta_vera_dati_insufficienti_uguale_con_a_e_b():
    t = {"id": 9, "make": "Fiat", "model": "Panda", "year": None, "mileage_km": None, "fuel": "benzina",
         "price_eur": 5000}
    r = O.confronta([(t, [], 1500)], O.valutatore(None), O.valutatore({"costo_fisso": 600}),
                    categorie_sito={"opportunita"})
    assert r["passaggi"] == {"dati_insufficienti -> dati_insufficienti": 1} and r["entrano"] == 0


def test_cli_senza_database():
    prima = os.environ.pop("DATABASE_URL", None)
    try:
        assert O.main(["--n", "5"]) == 2
    finally:
        if prima is not None:
            os.environ["DATABASE_URL"] = prima
