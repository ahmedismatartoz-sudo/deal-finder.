"""Stima di riserva quando i confronti diretti sono pochi o troppo dispersi.

Molti annunci (modelli meno diffusi, allestimenti rari) hanno meno di 4 auto simili nella
base di mercato. Invece di scartarli, si usa il modello addestrato (regressione per marca e
modello), ma solo se il gruppo è specifico (marca+modello) e poco disperso. La rivendita
prudente è più bassa di quella da confronti diretti e la valutazione resta "da verificare".

Su Facebook i km spesso mancano: si stimano in modo prudente (molti km = valore più basso)
e la scheda lo segnala.
"""
from __future__ import annotations

from datetime import date

from ..core.models import Listing
from .model import predict

KM_PER_YEAR_PRUDENT = 18_000
MODEL_RESALE_FACTOR = 0.95      # trattativa con il privato acquirente
MAX_SPREAD = 0.5


def weak(v) -> bool:
    return v.resale_prudent is None or v.n_comparables < 3 or (v.dispersion or 0) > 0.40


def estimate_missing_km(l: Listing) -> bool:
    """Km mancanti (tipico su Facebook): stima prudente dall'età. True se stimati."""
    if l.mileage_km is not None or not l.year:
        return False
    age = max(1, date.today().year - l.year)
    l.mileage_km = min(300_000, age * KM_PER_YEAR_PRUDENT)
    return True


def apply_model(v, l: Listing, model: dict | None) -> bool:
    """Sostituisce una stima debole con quella del modello addestrato. True se applicata."""
    if not model:
        return False
    # il modello è affidabile solo su auto normali e recenti, con km plausibili
    age = date.today().year - (l.year or 0)
    if not l.year or l.year < 2008 or l.fuel == "altro" or l.mileage_km is None \
            or (age >= 2 and l.mileage_km < 5000):
        return False
    if l.fuel is None:
        # carburante non scritto (frequente su Facebook): si prende la stima più bassa
        # tra benzina e diesel, così il valore non viene mai gonfiato
        preds = []
        for f in ("benzina", "diesel"):
            l.fuel = f
            q = predict(model, l)
            if q and q["level"] in ("mmfg", "mmf", "mm"):
                preds.append(q)
        l.fuel = None
        p = min(preds, key=lambda q: q["p50"]) if preds else None
        if p and "carburante_non_indicato" not in v.confidence_reasons:
            v.confidence_reasons.append("carburante_non_indicato")
    else:
        p = predict(model, l)
    if not p or p["level"] not in ("mmfg", "mmf", "mm") or p["spread"] > MAX_SPREAD:
        return False
    v.private_median = p["p50"]
    v.resale_median = round(p["p50"] * MODEL_RESALE_FACTOR)
    v.resale_prudent = round(p["p50"] * MODEL_RESALE_FACTOR)
    v.dispersion = p["spread"]
    v.confidence = "da_verificare"
    if "stima_da_modello" not in v.confidence_reasons:
        v.confidence_reasons.append("stima_da_modello")
    return True
