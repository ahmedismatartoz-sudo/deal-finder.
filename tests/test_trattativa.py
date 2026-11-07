"""7/11: scheda trattativa (storico, voto, previsione, prezzi, rivendita)."""
from datetime import datetime, timedelta, timezone

from dealfinder.web import trattativa as T
from dealfinder.web.scovo import soglia

ORA = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def ev(giorni_fa, prezzo, evento="prezzo", lid=1, fonte="subito"):
    return {"id": lid, "source": fonte, "status": "attivo", "event": evento, "price_eur": prezzo,
            "at": ORA - timedelta(days=giorni_fa)}


GRUPPI = {("privato", 30, 2): {"n": 120, "resto_med": -.06, "resto_p25": -.11, "quota_scende": .62, "giorni_med": 12},
          ("privato", 0, 0): {"n": 900, "resto_med": 0.0, "resto_p25": -.05, "quota_scende": .3, "giorni_med": 20}}


def test_venditore_morbido_voto_alto_e_previsione():
    st = T.storico([ev(40, 8500, "apparso"), ev(20, 8000), ev(5, 7500)], 1)
    assert st["ribassi"] == 2 and st["prezzo_iniziale"] == 8500
    b = {"first_seen_at": ORA - timedelta(days=40), "price_eur": 7500, "seller_type": "privato",
         "description": "Vendo per trasferimento all'estero, prezzo trattabile", "title": "Panda", "price_flags": [],
         "year": 2018, "mileage_km": 95000, "make": "fiat", "model": "panda", "fuel": "benzina"}
    d = {"prezzo": 7500, "nome": "Fiat Panda", "fonte": "Subito",
         "pv": {"M": 9200, "margine": 1700, "soglia": 1800},
         "valutazione": {"confronti": [{"prezzo": 7100, "url": "https://x/1"}, {"prezzo": 9000}] * 5}}
    s = T.calcola(b, st, [], d, soglia, ora=ORA, gruppi=GRUPPI, modelli={}, attivi={})
    assert s["voto"] >= 8 and s["giorni_in_vendita"] == 40 and s["sceso_euro"] == 1000
    assert s["previsione"]["prezzo_annuncio_previsto"] == 7050 and s["previsione"]["probabilita_ribasso"] == .62
    p = s["prezzi"]
    assert p["apertura"] < p["chiusura_probabile"] <= 7500
    assert p["massimo"] is not None and p["massimo"] < 7500         # margine 1700 < soglia 1800: deve scendere
    assert "7.100" in s["argomenti"][1]["testo"] or any("7.100" in a["testo"] for a in s["argomenti"])
    assert "trasferimento" in " ".join(s["motivi"]) or any("fretta" in m for m in s["motivi"])
    assert str(p["apertura"]).replace("000", "") and f"{p['apertura']:,}".replace(",", ".") in s["messaggio"]


def test_non_trattabile_e_appena_uscita():
    st = T.storico([ev(1, 6000, "apparso")], 1)
    b = {"first_seen_at": ORA - timedelta(days=1), "price_eur": 6000, "seller_type": "privato",
         "description": "Prezzo non trattabile", "title": "", "price_flags": [], "year": 2019, "mileage_km": 40000}
    d = {"prezzo": 6000, "pv": {"M": 9000, "margine": 3000, "soglia": 1500}, "valutazione": {}}
    s = T.calcola(b, st, [], d, soglia, ora=ORA, gruppi=GRUPPI, modelli={}, attivi={})
    assert s["voto"] <= 3 and s["quando"]["azione"] == "chiama_subito"


def test_massimo_per_margine():
    assert T.massimo_per_margine(8000, 2500, soglia) == 8700        # 8700 + 1800 = 8000 + 2500
    assert T.massimo_per_margine(8000, None, soglia) is None


def test_rivendita_veloce_poca_concorrenza():
    mod = {("fiat", "panda", "benzina", 2018): (60, 25, 11.0, 5.0, 7600.0, 7400.0),
           ("fiat", "panda", "benzina", 2019): (30, 12, 13.0, 6.0, 8200.0, 8100.0)}
    att = {("fiat", "panda", "benzina", 2018): (9, 7900.0, 20), ("fiat", "panda", "benzina", 2019): (6, 8400.0, 14)}
    r = T.rivendita("Fiat", "Panda", "benzina", 2018, modelli=mod, attivi=att)
    assert r["velocita"] == "molto_veloce" and r["concorrenza"] == "poca" and r["vendute_90g"] == 90
    assert r["giorni_vendita"] == 12 and r["voto"] >= 8 and r["frasi"]
    assert T.rivendita("Fiat", "Multipla", "benzina", 2018, modelli=mod, attivi=att) is None


def test_gruppo_allarga_se_pochi():
    g = T.gruppo("commerciante", 33, 2, GRUPPI)      # commerciante con pochi dati: usa i privati con stesso stato
    assert g and g["n"] == 120
    assert T.gruppo("privato", 33, 2, {}) is None
