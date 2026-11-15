"""15/11 (ramo piu-auto, studio dei buchi della zona): commercianti della fascia richiesta nel giro della zona,
province del sito sempre nel giro, confronti extra senza letture doppie, utilitarie senza rivalutazioni identiche,
cambi di prezzo con la finestra giusta, Torino senza seconda lettura."""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.pipeline import benzina_mi as B          # noqa: E402
from dealfinder.pipeline import scelta_candidate as SC   # noqa: E402


def _pg(nome):
    try:
        import pg_scheda as P
    except Exception:
        return None, None
    c = P.nuovo_db(nome)
    if c is None:
        return None, None
    P.migra_tutte(c)
    return P, c


# ------------------------------------------------------------------ punto 1: commercianti della fascia richiesta

def test_comm_fascia_parametri_dalle_regole_del_cliente():
    from dealfinder.regole import regole
    a = regole().ammissione
    p = B.comm_fascia_parametri(["MI", "TO", "NO", "PC", "MN", "SO", "CN"], 1000, 0.92, 400000, 300)
    assert p["prov"] == ["MI", "NO", "PC"]                       # solo PROVINCE_100KM: niente Torino, Mantova, Sondrio
    assert p["fascia_prezzo"] == a.richiesta_prezzo_max == 10000 and p["fascia_km"] == a.richiesta_km_max == 100000
    assert p["diesel_dal"] == a.richiesta_diesel_dal == 2017 and p["soglia"] == 0.92
    import re
    for f in ("benzina", "ibrida benzina", "hybrid", "gpl", "metano", "benzina/gpl"):
        assert re.search(p["carb_re"], f)
    assert not re.search(p["carb_re"], "diesel") and not re.search(p["carb_re"], "elettrica")
    assert re.search(p["nomi"], "Autosalone Loconte srl", re.I) and re.search(p["nomi"], "OFFICINE  MULINO", re.I)


def test_comm_fascia_spenta_o_fuori_zona():
    class Mai:
        def execute(self, *a, **k):
            raise AssertionError("nessuna query")
    vecchio = os.environ.get("BENZ_COMM_FASCIA")
    try:
        os.environ["BENZ_COMM_FASCIA"] = "0"
        assert B.candidate_comm_fascia(Mai(), ["MI"], 1000, 0.92, 400000, Counter()) == []
        os.environ["BENZ_COMM_FASCIA"] = "1"
        assert B.candidate_comm_fascia(Mai(), ["TO", "CN"], 1000, 0.92, 400000, Counter()) == []   # nessuna provincia del sito
    finally:
        if vecchio is None:
            os.environ.pop("BENZ_COMM_FASCIA", None)
        else:
            os.environ["BENZ_COMM_FASCIA"] = vecchio


def test_comm_fascia_sql_su_postgres_vero():
    """Solo commercianti in fascia richiesta, province del sito, sotto la soglia della mediana, niente esca/promo/
    Loconte, auto invariate valutate da poco fuori (salta se Postgres non c'è)."""
    P, c = _pg("piu_auto_comm")
    if c is None:
        return
    SC._COL.clear()
    ins = ("INSERT INTO listings (source, source_id, url, make, model, fuel, year, mileage_km, price_eur, province, "
           "seller_type, title, description, telefono_ok, price_flags) VALUES ('autoscout24', %s, %s, 'fiat', 'panda', "
           "%s, %s, %s, %s, %s, %s, %s, %s, true, %s) RETURNING id")

    def nuova(k, prezzo, venditore="commerciante", prov="MI", km=60000, fuel="benzina", anno=2018, titolo="Panda",
              testo="ok", flag=("x",)):
        return c.execute(ins, (k, "u" + k, fuel, anno, km, prezzo, prov, venditore, titolo, testo, list(flag))).fetchone()["id"]
    for i in range(8):                                           # gruppo di confronto: mediana 8.000
        nuova(f"g{i}", 8000, venditore="privato")
        nuova(f"d{i}", 8000, venditore="privato", fuel="diesel")
        nuova(f"v{i}", 8000, venditore="privato", fuel="diesel", anno=2015)
    ids = {
        "ok": nuova("ok", 6500),                                 # entra
        "pc": nuova("pc", 6600, prov="PC"),                      # Piacenza: entra
        "privato": nuova("privato", 6500, venditore="privato"),  # privato: c'è già la scelta dei privati
        "to": nuova("to", 6500, prov="TO"),                      # Torino: non è zona del sito
        "caro": nuova("caro", 7900),                             # sopra il 92% della mediana
        "esca": nuova("esca", 5000),                             # sotto il 70% della mediana: prezzo esca
        "km": nuova("km", 6500, km=120000),                      # fuori fascia (km)
        "promo": nuova("promo", 6500, flag=("leasing_o_rata",)),
        "loconte": nuova("loconte", 6500, titolo="Panda Loconte auto"),
        "diesel17": c.execute(ins, ("d17", "ud17", "diesel", 2018, 60000, 6500, "MI", "commerciante", "Panda", "ok",
                                    ["x"])).fetchone()["id"],
        "diesel15": c.execute(ins, ("d15", "ud15", "diesel", 2015, 60000, 6500, "MI", "commerciante", "Panda", "ok",
                                    ["x"])).fetchone()["id"],
        "invariata": nuova("inv", 6400),
    }
    imp = SC.parametri_invariate()["inv_regole"]
    c.execute("INSERT INTO valutazioni_policy (listing_id, policy_version, categoria, margine, riassunto, dati, "
              "impronta_regole, computed_at) VALUES (%s, 'v', 'non_conveniente', -2000, '{}', '{}', %s, "
              "now() - interval '1 hour')", (ids["invariata"], imp))
    par = dict(B.comm_fascia_parametri(["MI", "PC", "TO", "NO"], 1000, 0.92, 400000, 100), **SC.parametri_invariate())
    righe = c.execute(B.COMM_FASCIA_SQL.format(invariate=SC.condizione_invariate(c)), par).fetchall()
    presi = {r["id"] for r in righe}
    assert presi == {ids["ok"], ids["pc"], ids["diesel17"]}, {k for k, v in ids.items() if v in presi}
