"""Motore prezzi: confronti a livelli, due mercati, prudenza, confidenza.

Principi:
- Il prezzo degli annunci è un prezzo RICHIESTO: la rivendita si stima dal
  mercato dei commercianti, al 25° percentile, meno uno sconto di trattativa.
- Mercato privato e mercato commercianti si stimano separatamente.
- Se i dati non bastano la stima è "da_verificare", mai inventata.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median

import numpy as np

from ..core.models import Listing

ENGINE_VERSION = "prezzi-0.1"

EXCLUDE_FLAGS = {"leasing_o_rata", "prezzo_civetta", "importazione"}


@dataclass
class PricingConfig:
    min_comparables: int = 5
    max_dispersion: float = 0.20          # (P75-P25)/mediana
    negotiation_discount: float = 0.08    # da prezzo richiesto a incassato (da calibrare)
    dealer_private_ratio: float = 1.12    # usato solo se manca un mercato
    default_year_depr: float = 0.08       # svalutazione annua se non stimabile
    default_km_depr: float = 0.012        # per 10.000 km se non stimabile
    fraud_discount: float = 0.45          # oltre questo sconto: sospetto
    recent_days: int = 120                # peso agli annunci recenti


@dataclass
class Comparable:
    listing: Listing
    level: int
    adjusted_price: float


@dataclass
class Valuation:
    engine_version: str = ENGINE_VERSION
    private_median: int | None = None
    dealer_median: int | None = None
    resale_prudent: int | None = None
    resale_median: int | None = None
    comparable_level: int | None = None
    n_comparables: int = 0
    dispersion: float | None = None
    liquidity_days: float | None = None
    discount_vs_private: float | None = None
    confidence: str = "da_verificare"
    confidence_reasons: list[str] = field(default_factory=list)
    fraud_flags: list[str] = field(default_factory=list)
    comparables_used: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Selezione dei confronti
# ---------------------------------------------------------------------------
LEVELS = {
    # livello: (anni ±, km ±%, potenza ±%, cambio uguale)
    1: (1, 0.20, 0.05, True),
    2: (2, 0.30, 0.15, True),
    3: (3, 0.40, 0.25, False),
}


def _usable(c: Listing) -> bool:
    return (c.price_eur is not None and c.year is not None and c.mileage_km is not None
            and c.damage_class in ("nessuno", "sconosciuto") and not c.damage_declared
            and not (set(c.price_flags) & EXCLUDE_FLAGS))


def _matches(t: Listing, c: Listing, level: int) -> bool:
    dy, dkm, dpw, same_gear = LEVELS[level]
    if (c.make, c.model, c.fuel) != (t.make, t.model, t.fuel):
        return False
    if abs(c.year - t.year) > dy:
        return False
    ref_km = max(t.mileage_km, 10_000)
    if abs(c.mileage_km - t.mileage_km) > dkm * ref_km:
        return False
    if t.power_kw and c.power_kw and abs(c.power_kw - t.power_kw) > dpw * t.power_kw:
        return False
    if same_gear and t.gearbox and c.gearbox and c.gearbox != t.gearbox:
        return False
    return True


def select_comparables(target: Listing, market: list[Listing], level: int) -> list[Listing]:
    return [c for c in market
            if c is not target and (c.source, c.source_id) != (target.source, target.source_id)
            and _usable(c) and _matches(target, c, level)]


# ---------------------------------------------------------------------------
# Correzione per anno e chilometri
# ---------------------------------------------------------------------------
def depreciation_rates(group: list[Listing], cfg: PricingConfig) -> tuple[float, float]:
    """Stima svalutazione per anno e per 10.000 km sul gruppo (log-prezzo).

    Con pochi dati o coefficienti senza senso usa i valori di default.
    """
    if len(group) >= 15:
        y = np.log([c.price_eur for c in group])
        X = np.column_stack([np.ones(len(group)),
                             [c.year for c in group],
                             [c.mileage_km / 10_000 for c in group]])
        try:
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            year_rate = 1 - math.exp(-beta[1])
            km_rate = 1 - math.exp(beta[2])
            if 0.02 <= year_rate <= 0.25 and 0.0 <= km_rate <= 0.05:
                return year_rate, km_rate
        except np.linalg.LinAlgError:
            pass
    return cfg.default_year_depr, cfg.default_km_depr


def adjust_price(c: Listing, t: Listing, year_rate: float, km_rate: float) -> float:
    """Porta il prezzo del confronto all'anno e ai km dell'auto valutata."""
    p = float(c.price_eur)
    p *= (1 - year_rate) ** (c.year - t.year)           # confronto più nuovo → vale di più → scendo
    p *= (1 - km_rate) ** ((t.mileage_km - c.mileage_km) / 10_000)
    return p


def _q(values: list[float], q: float) -> float:
    return float(np.quantile(values, q))


# ---------------------------------------------------------------------------
# Valutazione
# ---------------------------------------------------------------------------
def value_listing(target: Listing, market: list[Listing],
                  cfg: PricingConfig | None = None,
                  catalog_confidence: float | None = None) -> Valuation:
    cfg = cfg or PricingConfig()
    v = Valuation()

    missing_core = [f for f in ("make", "model", "year", "mileage_km", "fuel", "price_eur")
                    if getattr(target, f) in (None, "")]
    if missing_core:
        v.confidence_reasons.append("dati_mancanti:" + ",".join(missing_core))
        return v

    # Livello più stretto che dà abbastanza confronti
    comps: list[Listing] = []
    for level in (1, 2, 3):
        comps = select_comparables(target, market, level)
        v.comparable_level = level
        if len(comps) >= cfg.min_comparables:
            break

    v.n_comparables = len(comps)
    if not comps:
        v.confidence_reasons.append("nessun_confronto")
        return v

    year_rate, km_rate = depreciation_rates(comps, cfg)
    adjusted = [Comparable(c, v.comparable_level, adjust_price(c, target, year_rate, km_rate))
                for c in comps]

    # Rimozione valori anomali (fuori 2.5 MAD dalla mediana)
    prices = [a.adjusted_price for a in adjusted]
    med = median(prices)
    mad = median([abs(p - med) for p in prices]) or med * 0.05
    adjusted = [a for a in adjusted if abs(a.adjusted_price - med) <= 2.5 * 1.4826 * mad]
    v.n_comparables = len(adjusted)

    private = [a.adjusted_price for a in adjusted if a.listing.seller_type == "privato"]
    dealer = [a.adjusted_price for a in adjusted if a.listing.seller_type == "commerciante"]
    allp = [a.adjusted_price for a in adjusted]

    if private:
        v.private_median = round(median(private))
    if dealer:
        v.dealer_median = round(median(dealer))
    if v.private_median is None and v.dealer_median:
        v.private_median = round(v.dealer_median / cfg.dealer_private_ratio)
        v.confidence_reasons.append("mercato_privato_stimato_da_commercianti")
    if v.dealer_median is None and v.private_median:
        v.dealer_median = round(v.private_median * cfg.dealer_private_ratio)
        v.confidence_reasons.append("mercato_commercianti_stimato_da_privati")

    # Rivendita: dal mercato commercianti se ci sono abbastanza dati, altrimenti da tutti
    base = dealer if len(dealer) >= 3 else [p * (cfg.dealer_private_ratio if a.listing.seller_type == "privato" else 1)
                                             for a, p in zip(adjusted, allp)]
    v.resale_prudent = round(_q(base, 0.25) * (1 - cfg.negotiation_discount))
    v.resale_median = round(_q(base, 0.50) * (1 - cfg.negotiation_discount))
    v.dispersion = round((_q(allp, 0.75) - _q(allp, 0.25)) / median(allp), 3)

    # Liquidità: giorni online dei confronti già spariti
    days = [(a.listing.disappeared_at - a.listing.first_seen_at).days
            for a in adjusted if a.listing.disappeared_at and a.listing.first_seen_at]
    v.liquidity_days = float(median(days)) if len(days) >= 3 else None

    v.discount_vs_private = round(1 - target.price_eur / v.private_median, 3)
    v.comparables_used = [{
        "source": a.listing.source, "source_id": a.listing.source_id, "url": a.listing.url,
        "year": a.listing.year, "km": a.listing.mileage_km, "seller": a.listing.seller_type,
        "price": a.listing.price_eur, "adjusted_price": round(a.adjusted_price),
    } for a in sorted(adjusted, key=lambda a: a.adjusted_price)]

    # Segnali di truffa o errore
    if v.discount_vs_private > cfg.fraud_discount:
        v.fraud_flags.append("prezzo_troppo_basso")
    v.fraud_flags += [f for f in target.price_flags if f in EXCLUDE_FLAGS | {"plus_iva"}]

    # Confidenza
    reasons = v.confidence_reasons
    if v.n_comparables < cfg.min_comparables:
        reasons.append(f"pochi_confronti:{v.n_comparables}")
    if v.dispersion > cfg.max_dispersion:
        reasons.append(f"confronti_dispersi:{v.dispersion}")
    if v.comparable_level == 3:
        reasons.append("confronti_solo_livello_largo")
    if catalog_confidence is not None and catalog_confidence < 0.8:
        reasons.append("versione_incerta")
    if target.damage_class not in ("nessuno",):
        reasons.append("danni_da_verificare" if target.damage_class != "sconosciuto" else "stato_non_verificato")
    if v.fraud_flags:
        reasons.append("segnali_anomali")
    blocking = [r for r in reasons if not r.startswith("mercato_")]
    v.confidence = "affidabile" if not blocking else "da_verificare"
    return v
