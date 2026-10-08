"""Letture e scritture di uso comune per pipeline e API."""
from __future__ import annotations

import json
from dataclasses import asdict

from .core.models import DamageItem, Listing

LISTING_FIELDS = ("source", "source_id", "url", "title", "description", "make", "model", "version_raw",
                  "year", "mileage_km", "fuel", "gearbox", "power_kw", "price_raw", "price_eur",
                  "seller_type", "city", "province", "region", "damage_declared", "damage_class",
                  "first_seen_at", "last_seen_at", "disappeared_at")


def row_to_listing(row: dict, photos: list[str] | None = None) -> Listing:
    l = Listing(**{k: row.get(k) for k in LISTING_FIELDS})
    l.price_flags = list(row.get("price_flags") or [])
    l.missing_fields = list(row.get("missing_fields") or [])
    l.photos = photos or []
    items = row.get("damage_items") or []
    if isinstance(items, str):
        items = json.loads(items)
    l.damage_items = [DamageItem(**{k: v for k, v in it.items() if k in DamageItem.__dataclass_fields__})
                      for it in items]
    return l


def photos_of(conn, listing_id: int) -> list[str]:
    return [r["source_url"] for r in conn.execute(
        "SELECT source_url FROM listing_photos WHERE listing_id=%s ORDER BY position", (listing_id,)).fetchall()]


def load_market(conn, make: str, model: str, fuel: str | None, days: int = 120) -> list[Listing]:
    """Confronti possibili: stessa marca/modello/carburante, attivi o spariti di recente."""
    rows = conn.execute(
        """SELECT * FROM listings
           WHERE make=%s AND model=%s AND (%s::text IS NULL OR fuel=%s)
             AND price_eur IS NOT NULL
             AND (status='attivo' OR disappeared_at > now() - make_interval(days => %s))""",
        (make, model, fuel, fuel, days)).fetchall()
    return [row_to_listing(r) for r in rows]


def set_stage(conn, listing_id: int, stage: str, reason: str | None = None, **cols) -> None:
    sets = ["stage=%(stage)s", "stage_reason=%(reason)s"]
    for k in cols:
        sets.append(f"{k}=%({k})s")
    params = {"stage": stage, "reason": reason, "id": listing_id,
              **{k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) and k not in ("price_flags", "missing_fields") else v)
                 for k, v in cols.items()}}
    conn.execute(f"UPDATE listings SET {', '.join(sets)} WHERE id=%(id)s", params)


def update_listing_fields(conn, listing_id: int, l: Listing) -> None:
    conn.execute(
        """UPDATE listings SET make=%s, model=%s, version_raw=%s, year=%s, mileage_km=%s, fuel=%s,
           gearbox=%s, power_kw=%s, price_eur=%s, price_flags=%s, seller_type=%s, damage_declared=%s,
           damage_class=%s, damage_items=%s, missing_fields=%s WHERE id=%s""",
        (l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox, l.power_kw, l.price_eur,
         l.price_flags, l.seller_type, l.damage_declared, l.damage_class,
         json.dumps([asdict(d) for d in l.damage_items]), l.missing_fields, listing_id))


def save_valuation(conn, listing_id: int, val, parts: dict | None, motivation: list[str],
                   checks: list[str], default_margin: dict | None) -> int:
    row = conn.execute(
        """INSERT INTO valuations (listing_id, engine_version, private_median, dealer_median, resale_prudent,
             resale_median, comparables_used, comparable_level, n_comparables, dispersion, liquidity_days,
             confidence, confidence_reasons, parts_cost_low, parts_cost_high, parts_detail,
             discount_vs_private, fraud_flags, damage_items, motivation, checks, default_margin,
             resale_prudent_private, resale_median_private, resale_prudent_dealer, resale_median_dealer,
             asis_median, asis_n)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (listing_id, val.engine_version, val.private_median, val.dealer_median, val.resale_prudent,
         val.resale_median, json.dumps(val.comparables_used), val.comparable_level, val.n_comparables,
         val.dispersion, val.liquidity_days, val.confidence, val.confidence_reasons,
         (parts or {}).get("parts_cost_low"), (parts or {}).get("parts_cost_high"),
         json.dumps(parts) if parts else None, val.discount_vs_private, val.fraud_flags,
         None, motivation, checks, json.dumps(default_margin) if default_margin else None,
         val.resale_prudent_private, val.resale_median_private, val.resale_prudent_dealer,
         val.resale_median_dealer, val.asis_median, val.asis_n)).fetchone()
    return row["id"]
