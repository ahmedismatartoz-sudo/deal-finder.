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

Impara da solo:
- si riaddestra ogni giorno su tutti gli annunci raccolti;
- a ogni addestramento prova da solo 18 combinazioni di impostazioni e tiene la migliore;
- gli annunci spariti in fretta (probabilmente venduti) contano di più, quelli fermi da mesi
  (prezzo troppo alto) contano di meno: così impara il prezzo a cui le auto si vendono davvero;
- il nuovo modello sostituisce il vecchio solo se sbaglia meno sugli stessi annunci di prova.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import date

import numpy as np

from ..core.models import Listing

MODEL_VERSION = "modello-prezzi-3"
MIN_GROUP = {"mmfg": 40, "mmf": 30, "mm": 30, "m": 60}
OUTLIER_MAD = 3.0          # annunci con prezzo assurdo per il loro gruppo: esclusi e si ricalcola
RIDGE = 1.0
# Ricerca automatica delle impostazioni migliori a ogni addestramento (si tiene la combinazione
# con l'errore più basso su annunci tenuti da parte)
GRID = [{"ridge": r, "outlier": o, "min_scale": m} for r in (0.3, 1.0, 3.0) for o in (2.5, 3.0) for m in (0.75, 1.0, 1.5)]


def _features(l: Listing, ref_year: int) -> list[float] | None:
    if not (l.year and l.mileage_km is not None):
        return None
    age = max(0, ref_year - l.year)
    km = min(l.mileage_km, 400_000) / 10_000
    kw = l.power_kw if l.power_kw and 30 <= l.power_kw <= 400 else None
    # la svalutazione non è lineare: anni e km anche al quadrato e insieme
    return [1.0, age, age * age / 10, km, km * km / 10, age * km / 10,
            math.log(kw) if kw else 0.0, 1.0 if kw else 0.0,
            1.0 if l.seller_type == "commerciante" else 0.0,
            1.0 if (l.gearbox or "").startswith("auto") else 0.0]


def _usable(l: Listing) -> bool:
    from ..core.consistency import check
    if check(l, fix=True) or "dati_incoerenti" in (l.price_flags or []):
        return False
    return (l.price_eur and 500 <= l.price_eur <= 80_000 and l.make and l.model and l.year
            and l.mileage_km is not None and not l.damage_declared
            and l.damage_class in ("nessuno", "sconosciuto")
            and not ({"leasing_o_rata", "prezzo_civetta", "importazione"} & set(l.price_flags)))


def _solve(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None, ridge: float = RIDGE) -> np.ndarray:
    Xw = X * w[:, None] if w is not None else X
    A = Xw.T @ X + ridge * np.eye(X.shape[1])
    A[0, 0] -= ridge                       # nessuna penalità sull'intercetta
    return np.linalg.solve(A, Xw.T @ y)


def _wquantiles(v: np.ndarray, w: np.ndarray, qs=(0.25, 0.5, 0.75)) -> list[float]:
    o = np.argsort(v)
    v, w = v[o], w[o]
    c = np.cumsum(w) / w.sum()
    return [float(v[min(np.searchsorted(c, q), len(v) - 1)]) for q in qs]


def _fit(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None, ridge: float = RIDGE,
         outlier: float = OUTLIER_MAD) -> tuple[list[float], list[float]]:
    w = np.ones(len(y)) if w is None else w
    beta = _solve(X, y, w, ridge)
    resid = y - X @ beta
    # robusto: si tolgono gli annunci molto lontani dal gruppo (prezzi civetta, errori) e si ricalcola
    mad = float(np.median(np.abs(resid - np.median(resid)))) * 1.4826
    if mad > 0:
        keep = np.abs(resid - np.median(resid)) <= outlier * mad
        if keep.sum() >= max(10, X.shape[1] + 2) and keep.sum() < len(y):
            beta = _solve(X[keep], y[keep], w[keep], ridge)
            resid, w = y[keep] - X[keep] @ beta, w[keep]
    return beta.tolist(), _wquantiles(resid, w)


def market_weight(l: Listing, today: date | None = None) -> float:
    """Quanto conta un annuncio per imparare il prezzo VERO di mercato.
    Un annuncio sparito in fretta è stato probabilmente venduto: il suo prezzo era giusto (conta di più).
    Un annuncio fermo da mesi chiede troppo (conta di meno)."""
    if getattr(l, "peso", None):
        return l.peso                      # prezzo di vendita vero riportato da un commerciante
    today = today or date.today()
    first = l.first_seen_at.date() if l.first_seen_at else None
    if not first:
        return 1.0
    if l.disappeared_at:
        days = (l.disappeared_at.date() - first).days
        return 1.5 if days <= 30 else (1.2 if days <= 60 else 0.8)
    days = (today - first).days
    return 1.0 if days <= 45 else (0.7 if days <= 90 else 0.5)


def _keys(l: Listing) -> list[tuple[str, str]]:
    gear = "auto" if (l.gearbox or "").startswith("auto") else "man"
    return [("mmfg", f"{l.make}|{l.model}|{l.fuel}|{gear}"), ("mmf", f"{l.make}|{l.model}|{l.fuel}"),
            ("mm", f"{l.make}|{l.model}"), ("m", f"{l.make}"), ("all", "*")]


def _build(tr: list, params: dict, ref_year: int) -> dict:
    groups: dict[tuple[str, str], list] = {}
    for l, f, w in tr:
        for k in _keys(l):
            groups.setdefault(k, []).append((f, math.log(l.price_eur), w))
    model = {"version": MODEL_VERSION, "ref_year": ref_year, "groups": {}, "n_train": len(tr), "params": params}
    for (lvl, key), rows in groups.items():
        if lvl != "all" and len(rows) < MIN_GROUP[lvl] * params.get("min_scale", 1.0):
            continue
        X = np.array([r[0] for r in rows])
        y = np.array([r[1] for r in rows])
        w = np.array([r[2] for r in rows])
        beta, q = _fit(X, y, w, params.get("ridge", RIDGE), params.get("outlier", OUTLIER_MAD))
        model["groups"][f"{lvl}:{key}"] = {"beta": beta, "q": q, "n": len(rows)}
    return model


def train(listings: list[Listing], ref_year: int | None = None, holdout: float = 0.2, seed: int = 7,
          grid: list[dict] | None = None) -> dict:
    """Addestra cercando da solo le impostazioni migliori.
    I dati si dividono in: addestramento, scelta delle impostazioni (validazione) e prova finale (test)."""
    ref_year = ref_year or date.today().year
    today = date.today()
    data = [(l, _features(l, ref_year)) for l in listings if _usable(l)]
    data = [(l, f, market_weight(l, today)) for l, f in data if f is not None]
    rnd = random.Random(seed)
    rnd.shuffle(data)
    n_test = int(len(data) * holdout)
    test, tr = data[:n_test], data[n_test:]
    grid = grid if grid is not None else GRID
    best, tried = None, []
    if len(grid) > 1 and len(tr) >= 500:
        n_val = int(len(tr) * 0.2)
        val, fit_part = tr[:n_val], tr[n_val:]
        for params in grid:
            m = _build(fit_part, params, ref_year)
            e = evaluate(m, [l for l, _, _ in val]).get("median_abs_pct_error")
            tried.append({**params, "err": e})
            if e is not None and (best is None or e < best[1]):
                best = (params, e)
    params = best[0] if best else (grid[0] if grid else {"ridge": RIDGE, "outlier": OUTLIER_MAD, "min_scale": 1.0})
    model = _build(tr, params, ref_year)
    model["metrics"] = evaluate(model, [l for l, _, _ in test])
    model["metrics"]["impostazioni"] = params
    model["metrics"]["prove"] = len(tried)
    model["_test"] = [l for l, _, _ in test]      # per il confronto con il modello attivo (non si salva)
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
        if len(g["beta"]) != len(f):
            return None                    # modello di una versione precedente: si aspetta il riaddestramento
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
    base_costs: int = 140                    # passaggio (90 €) e pulizia (50 €)
    healthy_ratio: float = 0.6               # potenziale ≥ 80% della soglia: vale l'approfondimento
    damaged_ratio: float = 1.1               # con problemi: deve restare spazio per i ricambi
    max_spread: float = 0.6                  # gruppo troppo disperso: modello poco affidabile


def prescreen(model: dict, l: Listing, rule: PrescreenRule | None = None,
              threshold_low: int = 1500, threshold_high: int = 2000, split: int = 5000,
              threshold_cheap: int = 700, cheap_max: int = 2000) -> dict:
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
