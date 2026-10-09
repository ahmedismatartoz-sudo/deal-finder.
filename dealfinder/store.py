"""Letture e scritture di uso comune per pipeline e API."""
from __future__ import annotations

import json
from dataclasses import asdict

from .core.models import DamageItem, Listing

LISTING_FIELDS = ("source", "source_id", "url", "title", "description", "make", "model", "version_raw",
                  "year", "mileage_km", "fuel", "gearbox", "power_kw", "price_raw", "price_eur",
                  "seller_type", "city", "province", "region", "damage_declared", "damage_class",
                  "first_seen_at", "last_seen_at", "disappeared_at")


# Colonne utili ai calcoli: si esclude "raw" (il testo originale completo della fonte), che con
# decine di migliaia di annunci occupa troppa memoria (il lavoro su Render ha 512 MB).
LIGHT_COLS = ", ".join(LISTING_FIELDS + ("id", "status", "stage", "prescreen", "problem_search", "price_flags",
                                          "missing_fields", "damage_items", "last_checked_at"))

# Solo i dati che servono ai confronti e al modello dei prezzi (niente testi)
MARKET_COLS = ("id, source, source_id, url, make, model, version_raw, year, mileage_km, fuel, gearbox, power_kw, "
               "price_eur, price_flags, seller_type, province, region, damage_declared, damage_class, status, "
               "first_seen_at, last_seen_at, disappeared_at")


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


HOME_REGION_SQL = "(province IN ('MI','MB','BG','BS','CO','VA','LC','LO','PV','CR','MN','SO') OR (province IS NULL AND (region IS NULL OR lower(region) = 'lombardia')))"


def load_market(conn, make: str, model: str, fuel: str | None, days: int = 120,
                region: str | None = None) -> list[Listing]:
    """Confronti possibili: stessa marca/modello/carburante, attivi o spariti di recente.
    Senza `region`: solo il mercato di casa (Lombardia). Con `region`: un'altra regione
    (raccolta per il confronto tra regioni; si usano gli annunci visti negli ultimi 14 giorni)."""
    if region:
        where = "lower(region) = lower(%s) AND last_seen_at > now() - interval '14 days'"
        params = (make, model, fuel, fuel, region)
    else:
        where = HOME_REGION_SQL + " AND (status='attivo' OR disappeared_at > now() - make_interval(days => %s))"
        params = (make, model, fuel, fuel, days)
    rows = conn.execute(
        f"""SELECT {MARKET_COLS} FROM listings
           WHERE make=%s AND model=%s AND (%s::text IS NULL OR fuel=%s)
             AND price_eur IS NOT NULL AND {where}""", params).fetchall()
    from .core.consistency import check
    out = []
    for r in rows:
        l = row_to_listing(r)
        if not check(l, fix=True) and "dati_incoerenti" not in l.price_flags:
            out.append(l)
    return out


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
