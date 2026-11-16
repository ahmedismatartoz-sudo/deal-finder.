"""16/11 (fondamenta): test del comportamento ATTUALE di pricing/valore_reale.py e pricing/ai_prezzi.py (senza
database: dati sintetici e connessioni finte). Non cambiano nulla."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
# sklearn al primo import scrive KMP_* nell'ambiente: si importa qui (all'import del modulo di test) così run_tests.py
# non lo conta come "stato lasciato" da un test
import sklearn.ensemble  # noqa: F401

from dealfinder.pricing import ai_prezzi as AI
from dealfinder.pricing import valore_reale as VR

ADESSO = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


# ------------------------------------------------------------------ valore_reale
def test_pulisci():
    assert VR.pulisci({"damage_class": "nessuno"})
    assert not VR.pulisci({"damage_class": "medio"})
    assert not VR.pulisci({"problem_search": True})
    assert not VR.pulisci({"price_flags": ["leasing_o_rata"]})
    assert not VR.pulisci({"stage_reason": "truffa"})
    assert not VR.pulisci({"year": 2015, "mileage_km": 2015})          # l'anno scritto al posto dei km


def test_etichetta():
    d = lambda g: ADESSO - timedelta(days=g)
    assert VR.etichetta({"status": "scomparso", "first_seen_at": d(30), "disappeared_at": d(20)}, ADESSO) == ("venduta", 3.0)
    assert VR.etichetta({"status": "scomparso", "first_seen_at": d(50), "disappeared_at": d(10)}, ADESSO) == ("venduta", 2.0)
    assert VR.etichetta({"status": "scomparso", "first_seen_at": d(90), "disappeared_at": d(10)}, ADESSO) == ("ferma", .5)
    # sparita ma riapparsa: non è una vendita, si giudica come un annuncio attivo (età dalla prima comparsa)
    assert VR.etichetta({"status": "scomparso", "riapparso": True, "first_seen_at": d(5), "disappeared_at": d(1)},
                        ADESSO) == ("attiva", 1.0)
    assert VR.etichetta({"status": "attivo", "first_seen_at": d(10)}, ADESSO) == ("attiva", 1.0)
    assert VR.etichetta({"status": "attivo", "first_seen_at": d(50)}, ADESSO) == ("ferma", .5)
    assert VR.etichetta({"status": "attivo", "first_seen_at": d(1), "ribassi": 3}, ADESSO) == ("ferma", .5)


def test_caratteristiche():
    r = {"year": 2020, "mileage_km": 50000, "power_kw": 100, "gearbox": "automatico",
         "version_raw": "2.0 TDI S line Avant", "title": "", "seller_type": "commerciante"}
    x = VR.caratteristiche(r, 80.0)
    assert len(x) == len(VR.NOMI) - 1
    assert x[0] == 1.0 and math.isclose(x[3], math.log1p(50)) and math.isclose(x[4], math.log(100))
    assert x[5:] == [1.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0]          # automatico, sportivo, sw, -, -, commerciante, kW ok
    senza_kw = VR.caratteristiche(dict(r, power_kw=None, version_raw="Coupé", gearbox="manuale",
                                       seller_type="privato"), 80.0)
    assert math.isclose(senza_kw[4], math.log(80.0)) and senza_kw[5:] == [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0]


def test_adatta_ritrova_i_coefficienti():
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(300), rng.uniform(0, 10, 300)])
    y = 9.0 - 0.1 * X[:, 1] + rng.normal(0, .01, 300)
    beta, res = VR.adatta(X, y, np.ones(300), ridge=0.0)
    assert abs(beta[0] - 9.0) < .02 and abs(beta[1] + 0.1) < .005 and len(res) == 300


def _gruppo(n=80, vendute=10):
    rng = np.random.default_rng(3)
    righe = []
    for i in range(n):
        anno = int(rng.integers(2012, 2024))
        km = int(rng.integers(10000, 180000))
        prezzo = int(math.exp(9.9 - 0.09 * (2026 - anno) - 0.05 * math.log1p(km / 1000) + rng.normal(0, .05)))
        venduta = i < vendute
        righe.append({"id": i + 1, "price_eur": prezzo, "year": anno, "mileage_km": km, "power_kw": 70,
                      "gearbox": "manuale", "version_raw": "", "title": "", "seller_type": "privato",
                      "status": "scomparso" if venduta else "attivo",
                      "first_seen_at": ADESSO - timedelta(days=20), "disappeared_at": ADESSO - timedelta(days=5) if venduta else None,
                      "damage_class": "nessuno"})
    return righe


def test_costruisci_e_stima(monkeypatch):
    monkeypatch.delenv("VR_MIN_GRUPPO", raising=False)
    assert VR.costruisci(_gruppo(30), ("fiat", "panda", "benzina"), now=ADESSO) is None      # sotto 40 annunci
    righe = _gruppo()
    m = VR.costruisci(righe, ("fiat", "panda", "benzina"), now=ADESSO)
    assert m is not None and m.n == 80 and m.n_vendute == 10 and .05 <= m.sigma <= .6
    s = m.stima({"year": 2020, "mileage_km": 60000, "power_kw": 70})
    assert set(s) == {"V", "P25", "sigma", "n", "n_vendute", "premio_venduta"}
    assert s["P25"] < s["V"] and 5000 < s["V"] < 20000
    # un annuncio dell'addestramento si stima SENZA di lui (formula esatta): stima diversa da quella "con lui"
    righe[20]["price_eur"] *= 3                            # prezzo fuori scala: "tira" il modello verso l'alto
    m = VR.costruisci(righe, ("fiat", "panda", "benzina"), now=ADESSO)
    con = m.stima(dict(righe[20], id=None))
    senza = m.stima(righe[20])
    assert senza["V"] < con["V"]
    meno = VR.costruisci(righe, ("fiat", "panda", "benzina"), escludi={1, 2, 3, 4, 5, 6, 7, 8}, now=ADESSO)
    assert meno.n == 72 and meno.n_vendute == 2 and meno.premio_venduta == 0.0 and meno.beta[-1] == 0.0


# ------------------------------------------------------------------ ai_prezzi
def test_norm_e_sana():
    assert AI._norm("  Alfa-Romeo  Giulia ") == "alfa romeo giulia"
    assert AI._norm(None) == ""
    r = {"damage_class": "nessuno", "price_flags": [], "mileage_km": 80000, "year": 2015}
    assert AI._sana(r)
    assert not AI._sana(dict(r, damage_class="leggero"))
    assert not AI._sana(dict(r, price_flags=["prezzo_civetta"]))
    assert not AI._sana(dict(r, mileage_km=2016))             # anno al posto dei km (±3)
    assert AI._sana(dict(r, mileage_km=2025))                  # 10 di differenza: valido


def _righe_ai(n=400):
    rng = np.random.default_rng(5)
    modelli = [("Fiat", "Panda", 9.0), ("Volkswagen", "Golf", 9.8), ("BMW", "X1", 10.2)]
    out = []
    for i in range(n):
        mk, md, base = modelli[i % 3]
        anno = int(rng.integers(2010, 2025))
        km = int(rng.integers(5000, 200000))
        out.append({"id": i + 1, "make": mk, "model": md, "fuel": "benzina", "gearbox": "manuale", "power_kw": 70,
                    "year": anno, "mileage_km": km, "seller_type": "privato", "source": "subito",
                    "price_eur": int(math.exp(base - 0.08 * (2026 - anno) - 0.02 * km / 10000 + rng.normal(0, .05))),
                    "status": "attivo"})
    return out


def test_dati_e_codifica():
    righe = _righe_ai(30)
    d = AI.Dati(righe, 2026)
    assert d.mm[0] == "fiat|panda" and d.mmf[0] == "fiat|panda|benzina" and d.num.shape == (30, 5)
    assert d.cat_raw["seller"][0] == "privato" and d.y is not None
    idx = np.arange(30)
    cod = AI.Codifica(d, idx, np.zeros(30))
    X = cod.righe(d, idx)
    assert X.shape == (30, 4 + len(AI.CAT)) and np.allclose(X[:, :3], 0.0)
    assert AI._come_privato(AI.Dati([dict(righe[0], seller_type="commerciante")], 2026)).cat_raw["seller"] == ["privato"]


def test_fit_e_pred_su_dati_sintetici(monkeypatch):
    monkeypatch.setenv("AI_ALBERI", "60")
    righe = _righe_ai(400)
    d = AI.Dati(righe, 2026)
    tr = np.arange(400)
    pred = AI._pred(AI._fit(d, tr), d, tr)
    err = np.median(np.abs(np.exp(pred) - np.exp(d.y)) / np.exp(d.y))
    assert err < .15


def test_allena_e_stima_con_pochi_annunci_non_addestra(monkeypatch):
    class Conn:
        def execute(self, sql, p=()):
            return type("R", (), {"fetchall": lambda s: _righe_ai(50)})()
    monkeypatch.setattr(AI, "_righe", lambda conn: _righe_ai(50))
    assert AI.allena_e_stima(Conn()) == {"annunci": 50, "saltato": True}
