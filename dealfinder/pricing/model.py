"""Modello dei prezzi addestrato sulla base di mercato.

Si addestra sul database (annunci sani raccolti) e si salva come file/record JSON.
Durante la raccolta valuta ogni annuncio ALL'ISTANTE, senza interrogare il catalogo:
così già allo scraping si separano le auto "apparentemente interessanti" da tutte le altre.

Come funziona (semplice, spiegabile, robusto con pochi dati):
  log(prezzo) = a + b·età + c·km/10.000 + d·log(kW) + e·commerciante
stimato per gruppi sempre più ampi, e si usa il gruppo più specifico con dati sufficienti:
  1. marca + modello + carburante   (almeno 40 annunci)
  2. marca + modello                (almeno 40)
  3. marca                          (almeno 80)
  4. tutto il mercato
Per ogni gruppo si salvano anche i residui (quanto i prezzi reali si discostano), da cui
si ricava il prezzo prudente (25° percentile) e l'incertezza.

Il modello si riaddestra da solo ogni settimana e viene attivato solo se l'errore misurato
su annunci tenuti da parte non peggiora rispetto al modello precedente.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import date

import numpy as np

from ..core.models import Listing

MODEL_VERSION = "modello-prezzi-1"
MIN_GROUP = {"mmf": 40, "mm": 40, "m": 80}
RIDGE = 1.0


def _features(l: Listing, ref_year: int) -> list[float] | None:
    if not (l.year and l.mileage_km is not None):
        return None
    age = max(0, ref_year - l.year)
    kw = l.power_kw if l.power_kw and 30 <= l.power_kw <= 400 else None
    return [1.0, age, min(l.mileage_km, 400_000) / 10_000,
            math.log(kw) if kw else 0.0, 1.0 if kw else 0.0,
            1.0 if l.seller_type == "commerciante" else 0.0]


def _usable(l: Listing) -> bool:
    return (l.price_eur and 500 <= l.price_eur <= 80_000 and l.make and l.model and l.year
            and l.mileage_km is not None and not l.damage_declared
            and l.damage_class in ("nessuno", "sconosciuto")
            and not ({"leasing_o_rata", "prezzo_civetta", "importazione"} & set(l.price_flags)))


def _fit(X: np.ndarray, y: np.ndarray) -> tuple[list[float], list[float]]:
    A = X.T @ X + RIDGE * np.eye(X.shape[1])
    A[0, 0] -= RIDGE                       # nessuna penalità sull'intercetta
    beta = np.linalg.solve(A, X.T @ y)
    resid = y - X @ beta
    q = np.quantile(resid, [0.25, 0.5, 0.75]).tolist()
    return beta.tolist(), q


def _keys(l: Listing) -> list[tuple[str, str]]:
    return [("mmf", f"{l.make}|{l.model}|{l.fuel}"), ("mm", f"{l.make}|{l.model}"), ("m", f"{l.make}"), ("all", "*")]


def train(listings: list[Listing], ref_year: int | None = None, holdout: float = 0.2, seed: int = 7) -> dict:
    ref_year = ref_year or date.today().year
    data = [(l, _features(l, ref_year)) for l in listings if _usable(l)]
    data = [(l, f) for l, f in data if f is not None]
    rnd = random.Random(seed)
    rnd.shuffle(data)
    n_test = int(len(data) * holdout)
    test, tr = data[:n_test], data[n_test:]

    groups: dict[tuple[str, str], list] = {}
    for l, f in tr:
        for k in _keys(l):
            groups.setdefault(k, []).append((f, math.log(l.price_eur)))
    model = {"version": MODEL_VERSION, "ref_year": ref_year, "groups": {}, "n_train": len(tr)}
    for (lvl, key), rows in groups.items():
        if lvl != "all" and len(rows) < MIN_GROUP[lvl]:
            continue
        X = np.array([r[0] for r in rows])
        y = np.array([r[1] for r in rows])
        beta, q = _fit(X, y)
        model["groups"][f"{lvl}:{key}"] = {"beta": beta, "q": q, "n": len(rows)}
    model["metrics"] = evaluate(model, [l for l, _ in test])
    return model


def predict(model: dict, l: Listing) -> dict | None:
    """Prezzo di mercato stimato per un'auto SANA con queste caratteristiche."""
    f = _features(l, model["ref_year"])
    if f is None or not l.make:
        return None
    for lvl, key in _keys(l):
        g = model["groups"].get(f"{lvl}:{key}")
        if g is None:
            continue
        base = float(np.dot(g["beta"], f))
        q25, q50, q75 = g["q"]
        return {"p50": round(math.exp(base + q50)), "p25": round(math.exp(base + q25)),
                "p75": round(math.exp(base + q75)), "level": lvl, "n": g["n"],
                "spread": round(math.exp(q75 - q25) - 1, 3)}
    return None


def evaluate(model: dict, test: list[Listing]) -> dict:
    errs, cover, by_level = [], [], {}
    for l in test:
        p = predict(model, l)
        if not p:
            continue
        e = abs(p["p50"] - l.price_eur) / l.price_eur
        errs.append(e)
        cover.append(p["p25"] <= l.price_eur)
        by_level.setdefault(p["level"], []).append(e)
    if not errs:
        return {"n_test": 0}
    return {"n_test": len(errs), "median_abs_pct_error": round(float(np.median(errs)), 4),
            "p25_coverage": round(sum(cover) / len(cover), 3),
            "by_level": {k: {"n": len(v), "err": round(float(np.median(v)), 4)} for k, v in by_level.items()}}


@dataclass
class PrescreenRule:
    """Quando un annuncio è "apparentemente interessante" già allo scraping."""
    private_resale_factor: float = 0.94      # da prezzo di mercato a incassato (trattativa)
    base_costs: int = 1100                   # trasporto, pratiche, preparazione
    healthy_ratio: float = 0.8               # potenziale ≥ 80% della soglia: vale l'approfondimento
    damaged_ratio: float = 1.3               # con problemi: deve restare spazio per i ricambi
    max_spread: float = 0.6                  # gruppo troppo disperso: modello poco affidabile


def prescreen(model: dict, l: Listing, rule: PrescreenRule | None = None,
              threshold_low: int = 2000, threshold_high: int = 3000, split: int = 5000,
              threshold_cheap: int = 1000, cheap_max: int = 2000) -> dict:
    rule = rule or PrescreenRule()
    if not l.price_eur:
        return {"esito": "dati_insufficienti"}
    p = predict(model, l)
    if not p:
        return {"esito": "nessun_modello"}
    threshold = threshold_cheap if l.price_eur <= cheap_max else (threshold_low if l.price_eur < split else threshold_high)
    potential = p["p50"] * rule.private_resale_factor - l.price_eur - rule.base_costs
    damaged = bool(l.damage_declared or l.problem_search)
    need = threshold * (rule.damaged_ratio if damaged else rule.healthy_ratio)
    out = {"model_p50": p["p50"], "model_p25": p["p25"], "level": p["level"], "potenziale": round(potential),
           "sconto": round(1 - l.price_eur / p["p50"], 3), "con_problemi": damaged}
    if p["spread"] > rule.max_spread:
        out["esito"] = "modello_incerto"
    else:
        out["esito"] = "interessante" if potential >= need else "non_interessante"
    return out


def dumps(model: dict) -> str:
    return json.dumps(model)
