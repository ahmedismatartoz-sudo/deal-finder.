"""Margine per singolo commerciante, soglie, classifica, regola delle aperture.

La manodopera NON è inclusa: Deal Finder stima solo i ricambi; ogni
commerciante valuta il lavoro con la propria officina.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import Listing
from .engine import Valuation

MAX_DEALER_OPENS = 7
VAT_RATE = 0.22


@dataclass
class DealerCosts:
    transport_eur: int = 150
    paperwork_eur: int = 450
    preparation_eur: int = 300
    contingency_pct: float = 0.05
    contingency_damaged_pct: float = 0.15        # carrozzeria
    contingency_fault_pct: float = 0.25          # guasti meccanici noti
    contingency_high_risk_pct: float = 0.35      # motore/cambio/airbag, non parte, guasto ignoto
    warranty_reserve_eur: int = 0       # tra privati non c'è garanzia legale del venditore
    vat_margin_scheme: bool = False     # default: compravendita tra privati, nessuna IVA
    threshold_low_eur: int = 2000
    threshold_high_eur: int = 3000
    threshold_split_eur: int = 5000     # sul prezzo di acquisto
    threshold_cheap_eur: int = 1000     # auto economiche: margine minimo per comparire
    threshold_cheap_max_eur: int = 2000 # fino a questo prezzo di acquisto vale la soglia "economica"


@dataclass
class MarginResult:
    purchase: int
    resale_prudent: int
    parts_cost: int
    fixed_costs: int
    contingency: int
    vat_on_margin: int
    net_margin: int
    threshold: int
    status: str                         # opportunita | da_verificare | scartata
    score: float = 0.0
    breakdown: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def compute_margin(listing: Listing, val: Valuation, costs: DealerCosts,
                   parts_cost_high: int | None = None) -> MarginResult | None:
    if val.resale_prudent is None or listing.price_eur is None:
        return None

    from ..ai.damage import FAULTS
    damaged = listing.damage_class in ("leggero", "medio", "alto_rischio") or bool(listing.damage_items)
    has_fault = any(d.part in FAULTS for d in listing.damage_items)
    high_risk = listing.damage_class == "alto_rischio"
    notes = ["manodopera_esclusa"] if damaged else []
    if damaged and parts_cost_high is None:
        notes.append("ricambi_non_stimati")
    if high_risk:
        notes.append("alto_rischio_meccanico")

    purchase = listing.price_eur
    parts = parts_cost_high or 0
    fixed = costs.transport_eur + costs.paperwork_eur + costs.preparation_eur + costs.warranty_reserve_eur
    if high_risk:
        pct = costs.contingency_high_risk_pct
    elif has_fault:
        pct = costs.contingency_fault_pct
    elif damaged:
        pct = costs.contingency_damaged_pct
    else:
        pct = costs.contingency_pct
    contingency = round(pct * (purchase + parts))

    gross = val.resale_prudent - purchase - parts - fixed - contingency
    # IVA solo per chi vende con il regime del margine (opzione per commerciante).
    # Default: acquisto da privato e vendita a privato, nessuna IVA.
    vat = 0
    if costs.vat_margin_scheme:
        vat = round(max(0, val.resale_prudent - purchase) * VAT_RATE / (1 + VAT_RATE))
    net = gross - vat

    if purchase <= costs.threshold_cheap_max_eur:
        threshold = costs.threshold_cheap_eur
    elif purchase < costs.threshold_split_eur:
        threshold = costs.threshold_low_eur
    else:
        threshold = costs.threshold_high_eur

    if net < threshold:
        status = "scartata"
    elif val.confidence != "affidabile" or "ricambi_non_stimati" in notes or high_risk:
        status = "da_verificare"
    else:
        status = "opportunita"

    res = MarginResult(purchase=purchase, resale_prudent=val.resale_prudent, parts_cost=parts,
                       fixed_costs=fixed, contingency=contingency, vat_on_margin=vat,
                       net_margin=net, threshold=threshold, status=status, notes=notes,
                       breakdown={"trasporto": costs.transport_eur, "pratiche": costs.paperwork_eur,
                                  "preparazione": costs.preparation_eur,
                                  "riserva_garanzia": costs.warranty_reserve_eur})
    res.score = rank_score(res, val, listing)
    return res


def liquidity_factor(days: float | None) -> float:
    if days is None:
        return 0.85
    if days <= 30:
        return 1.0
    if days <= 60:
        return 0.9
    if days <= 90:
        return 0.8
    return 0.65


def risk_penalty(val: Valuation, listing: Listing) -> float:
    """0 = nessun rischio, 1 = massimo. Pesi iniziali, da tarare."""
    r = 0.0
    if listing.damage_class in ("leggero", "medio"):
        r += 0.15
    if listing.damage_class == "alto_rischio":
        r += 0.35
    if listing.damage_class == "sconosciuto":
        r += 0.10
    if val.fraud_flags:
        r += 0.30
    if val.dispersion and val.dispersion > 0.15:
        r += 0.10
    if len(listing.missing_fields) > 2:
        r += 0.10
    return min(r, 0.8)


def rank_score(m: MarginResult, val: Valuation, listing: Listing) -> float:
    conf = 1.0 if val.confidence == "affidabile" else 0.7
    roi = max(0.0, m.net_margin) / max(m.purchase, 500)
    roi_boost = 1 + 0.3 * min(roi, 3.0)       # 1.000 € di acquisto e 2.000 € di margine: +60%
    return round(m.net_margin * conf * roi_boost * liquidity_factor(val.liquidity_days)
                 * (1 - risk_penalty(val, listing)), 1)


def visible_for_dealers(distinct_dealers_opened: int) -> bool:
    """L'annuncio sparisce dopo 7 commercianti diversi che hanno aperto il link."""
    return distinct_dealers_opened < MAX_DEALER_OPENS
