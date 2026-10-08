"""Accesso al database (PostgreSQL, psycopg 3)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .config import settings
from .core.models import Listing
from .core.normalize import fingerprint, find_plate, plate_hash


@contextmanager
def connect():
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def init_schema(conn) -> None:
    sql = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()
    conn.execute(sql)


def upsert_vehicle(conn, listing: Listing) -> int:
    fp = fingerprint(listing)
    plate = find_plate(" ".join(filter(None, [listing.title, listing.description])))
    ph = plate_hash(plate, settings.plate_salt) if plate else None
    if ph:
        row = conn.execute("SELECT id FROM vehicles WHERE plate_hash = %s", (ph,)).fetchone()
        if row:
            return row["id"]
    row = conn.execute(
        """INSERT INTO vehicles (fingerprint, plate_hash) VALUES (%s, %s)
           ON CONFLICT (fingerprint) DO UPDATE SET plate_hash = COALESCE(vehicles.plate_hash, EXCLUDED.plate_hash)
           RETURNING id""", (fp, ph)).fetchone()
    return row["id"]


def upsert_listing(conn, listing: Listing) -> tuple[int, str]:
    """Salva o aggiorna un annuncio. Ritorna (id, evento): nuovo | prezzo | invariato | riapparso."""
    prev = conn.execute("SELECT id, price_eur, status FROM listings WHERE source=%s AND source_id=%s",
                        (listing.source, listing.source_id)).fetchone()
    vehicle_id = upsert_vehicle(conn, listing)
    damage = [d.__dict__ for d in listing.damage_items]
    params = dict(
        source=listing.source, source_id=listing.source_id, url=listing.url, vehicle_id=vehicle_id,
        title=listing.title, description=listing.description, make=listing.make, model=listing.model,
        version_raw=listing.version_raw, year=listing.year, mileage_km=listing.mileage_km,
        fuel=listing.fuel, gearbox=listing.gearbox, power_kw=listing.power_kw,
        price_raw=listing.price_raw, price_eur=listing.price_eur, price_flags=listing.price_flags,
        seller_type=listing.seller_type, city=listing.city, province=listing.province,
        region=listing.region, damage_declared=listing.damage_declared,
        damage_class=listing.damage_class, damage_items=json.dumps(damage),
        missing_fields=listing.missing_fields, raw=json.dumps(listing.raw, default=str),
    )
    if prev is None:
        cols = ", ".join(params)
        vals = ", ".join(f"%({k})s" for k in params)
        lid = conn.execute(f"INSERT INTO listings ({cols}) VALUES ({vals}) RETURNING id", params).fetchone()["id"]
        conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'apparso',%s)",
                     (lid, listing.price_eur))
        _save_photos(conn, lid, listing.photos)
        return lid, "nuovo"

    lid = prev["id"]
    sets = ", ".join(f"{k} = %({k})s" for k in params if k not in ("source", "source_id"))
    conn.execute(f"UPDATE listings SET {sets}, last_seen_at = now(), status='attivo', disappeared_at=NULL "
                 f"WHERE id = %(id)s", {**params, "id": lid})
    event = "invariato"
    if prev["status"] == "scomparso":
        event = "riapparso"
        conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'riapparso',%s)",
                     (lid, listing.price_eur))
    if prev["price_eur"] != listing.price_eur:
        event = "prezzo"
        conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'prezzo',%s)",
                     (lid, listing.price_eur))
    return lid, event


def _save_photos(conn, listing_id: int, urls: list[str]) -> None:
    for i, u in enumerate(urls[:30]):
        conn.execute("INSERT INTO listing_photos (listing_id, position, source_url) VALUES (%s,%s,%s) "
                     "ON CONFLICT DO NOTHING", (listing_id, i, u))


def mark_disappeared(conn, source: str, not_seen_hours: int = 36) -> int:
    """Annunci non più visti nelle ricerche: diventano 'scomparso' (dato di mercato, non cancellato)."""
    rows = conn.execute(
        """UPDATE listings SET status='scomparso', disappeared_at=now()
           WHERE source=%s AND status='attivo' AND last_seen_at < now() - make_interval(hours => %s)
           RETURNING id, price_eur""", (source, not_seen_hours)).fetchall()
    for r in rows:
        conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'scomparso',%s)",
                     (r["id"], r["price_eur"]))
    return len(rows)
