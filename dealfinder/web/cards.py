"""Costruzione delle schede per il commerciante: margine ricalcolato con i
SUOI costi e il SUO tipo di ricambio preferito."""
from __future__ import annotations

import json
from dataclasses import asdict, fields

from ..pricing import report
from ..pricing.engine import Valuation
from ..pricing.margin import DealerCosts, compute_margin
from ..store import row_to_listing

COST_FIELDS = [f.name for f in fields(DealerCosts)]
SOURCE_LABELS = {"subito": "Subito", "facebook": "Facebook Marketplace"}


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def valuation_from_row(v: dict) -> Valuation:
    val = Valuation()
    for k in ("engine_version", "private_median", "dealer_median", "resale_prudent", "resale_median",
              "comparable_level", "n_comparables", "dispersion", "liquidity_days", "discount_vs_private",
              "confidence"):
        if v.get(k) is not None:
            setattr(val, k, v[k])
    val.confidence_reasons = list(v.get("confidence_reasons") or [])
    val.fraud_flags = list(v.get("fraud_flags") or [])
    val.comparables_used = _j(v.get("comparables_used")) or []
    return val


def dealer_costs(row: dict | None) -> DealerCosts:
    if not row:
        return DealerCosts()
    return DealerCosts(**{k: row[k] for k in COST_FIELDS if k in row and row[k] is not None})


def parts_for(parts: dict | None, preferred: str) -> tuple[int | None, bool]:
    if not parts:
        return None, True
    by_type = parts.get("by_type") or {}
    sel = by_type.get(preferred) or {"high": parts.get("parts_cost_high"), "complete": parts.get("complete", True)}
    return sel.get("high"), bool(sel.get("complete", True))


def build(listing_row: dict, val_row: dict, photos: list[str], costs_row: dict | None,
          preferred: str = "aftermarket", opens: int = 0, opened_by_me: bool = False,
          full: bool = False) -> dict | None:
    l = row_to_listing(listing_row, photos)
    v = valuation_from_row(val_row)
    parts = _j(val_row.get("parts_detail"))
    parts_high, parts_complete = parts_for(parts, preferred)
    costs = dealer_costs(costs_row)
    m = compute_margin(l, v, costs, parts_high)
    if m is None:
        return None
    if not parts_complete and m.status == "opportunita":
        m.status = "da_verificare"
        m.notes.append("ricambi_non_tutti_prezzati")

    card = {
        "id": listing_row["id"],
        "status": m.status,
        "score": m.score,
        "title": l.title,
        "source": l.source, "source_label": SOURCE_LABELS.get(l.source, l.source),
        "photo": photos[0] if photos else None,
        "make": l.make, "model": l.model, "version": l.version_raw, "year": l.year, "km": l.mileage_km,
        "fuel": l.fuel, "gearbox": l.gearbox, "power_kw": l.power_kw,
        "city": l.city, "province": l.province,
        "price": l.price_eur,
        "resale_prudent": v.resale_prudent,
        "parts_cost": m.parts_cost,
        "net_margin": m.net_margin,
        "threshold": m.threshold,
        "damage_class": l.damage_class,
        "confidence": v.confidence,
        "liquidity_days": v.liquidity_days,
        "risk": _risk_label(l, v),
        "first_seen_at": listing_row.get("first_seen_at"),
        "opens": opens, "opened_by_me": opened_by_me,
        "missing_fields": l.missing_fields,
    }
    if full:
        card.update({
            "description": l.description,
            "photos": photos,
            "seller_type": l.seller_type,
            "price_flags": l.price_flags,
            "costs": {"acquisto": m.purchase, "ricambi": m.parts_cost, **m.breakdown,
                      "riserva_imprevisti": m.contingency, "iva_sul_margine": m.vat_on_margin},
            "notes": m.notes,
            "valuation": {
                "private_median": v.private_median, "dealer_median": v.dealer_median,
                "resale_prudent": v.resale_prudent, "resale_median": v.resale_median,
                "n_comparables": v.n_comparables, "comparable_level": v.comparable_level,
                "dispersion": v.dispersion, "discount_vs_private": v.discount_vs_private,
                "confidence_reasons": [report.reason_text(r) for r in v.confidence_reasons],
                "fraud_flags": [report.FRAUD_LABELS.get(f, f) for f in v.fraud_flags],
            },
            "comparables": v.comparables_used[:15],
            "damage": report.damage_labels(l),
            "parts": parts,
            "parts_preferred": preferred,
            "motivation": list(val_row.get("motivation") or []),
            "checks": list(val_row.get("checks") or []),
            "valued_at": val_row.get("created_at"),
        })
    return card


def _risk_label(l, v) -> str:
    from ..pricing.margin import risk_penalty
    r = risk_penalty(v, l)
    return "basso" if r < 0.1 else ("medio" if r < 0.3 else "alto")


def costs_dict(c: DealerCosts) -> dict:
    return asdict(c)
