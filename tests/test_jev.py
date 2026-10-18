"""AI decisionale (Jev): stato mandato e lettura delle risposte, senza rete."""
from dealfinder.pricing import jev


def test_stato_senza_numero_e_con_calcoli():
    r = {"id": 1, "title": "Fiat Panda 1.2", "description": "ottima, chiamare 333 1234567", "make": "fiat", "model": "panda",
         "year": 2015, "mileage_km": 90000, "fuel": "benzina", "price_eur": 4000, "source": "subito", "n_foto": 8,
         "cat": "opportunita", "dati": {"M": 6000, "L": 5500, "n": 12, "R_finale": 5800, "margine": 1800, "soglia": 1395,
                                       "confronti": [{"prezzo": 6100, "anno": 2015, "km": 85000}]}}
    s = jev.stato_auto(r)
    assert "333" not in s["annuncio"]["descrizione"]           # il numero del venditore non va a Jev
    assert s["annuncio"]["numero_di_telefono_presente"] is True
    assert s["calcoli_scovo"]["rivendita_stimata"] == 5800 and len(s["confronti"]) == 1


def test_leggi_risposta():
    risp = {"answers": {"decisione": {"type": "choice", "choice": "compra", "confidence": .8,
                                      "probabilities": {"compra": .85, "tratta": .1, "scarta": .05}},
                        "truffa": {"type": "noul", "noul": .05}, "versione_ok": {"type": "noul", "noul": .9},
                        "dati_coerenti": {"type": "noul", "noul": .95}, "stato": {"type": "score", "score": 3.1},
                        "correzione": {"type": "score", "score": 3.5}}, "usage": {"input_tokens": 1500}}
    x = jev.leggi(risp)
    assert x["decisione"] == "compra" and x["p_compra"] == .85 and x["token"] == 1500
    assert x["correzione_pct"] == -2.5                          # tra −5% e 0%
