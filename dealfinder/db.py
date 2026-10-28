"""Accesso al database (PostgreSQL, psycopg 3)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from .config import settings
from .core.models import Listing
from .core.normalize import fingerprint, find_plate, plate_hash

MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"


# Connessioni riusate (solo nel sito, attivate con usa_pool()): aprire una connessione nuova costa una stretta
# di mano TLS e un login, lenti quando il database è sotto carico. Le raccolte (processi a parte) non cambiano.
_pool: list = []
_pool_on = False
POOL_MAX = 10
POOL_ETA = 300                    # secondi: connessioni ferme da più tempo si chiudono


def usa_pool(on: bool = True) -> None:
    global _pool_on
    _pool_on = on


def _prendi():
    import time
    while _pool:
        try:
            conn, t = _pool.pop()
        except IndexError:
            break
        if not conn.closed and time.time() - t < POOL_ETA:
            return conn
        try:
            conn.close()
        except Exception:
            pass
    return None


@contextmanager
def connect():
    import psycopg
    from psycopg.rows import dict_row
    if not _pool_on:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            yield conn
        return
    import time
    conn = _prendi() or psycopg.connect(settings.database_url, row_factory=dict_row)
    ok = False
    try:
        yield conn
        ok = True
    finally:
        try:
            if ok:
                conn.commit()
            else:
                conn.rollback()
            riusa = (ok and not conn.closed and not conn.autocommit
                     and conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE and len(_pool) < POOL_MAX)
        except Exception:
            riusa = False
        if riusa:
            _pool.append((conn, time.time()))
        else:
            try:
                conn.close()
            except Exception:
                pass


def migrate(conn) -> list[str]:
    """Applica in ordine le migrazioni non ancora eseguite."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, "
                 "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    applied = []
    for f in sorted(MIGRATIONS.glob("*.sql")):
        if f.name in done:
            continue
        conn.execute(f.read_text())
        conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
        conn.commit()
        applied.append(f.name)
    return applied


def log_job(conn, job: str):
    """Registra l'inizio di un lavoro; ritorna una funzione per chiuderlo."""
    jid = conn.execute("INSERT INTO job_runs (job) VALUES (%s) RETURNING id", (job,)).fetchone()["id"]
    conn.commit()

    def finish(ok: bool, stats: dict) -> None:
        conn.execute("UPDATE job_runs SET finished_at=now(), ok=%s, stats=%s WHERE id=%s",
                     (ok, json.dumps(stats, default=str), jid))
        conn.commit()
    return finish


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


_LETTURA_OK = False


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
        missing_fields=listing.missing_fields,
        # il testo originale della fonte serve solo per Facebook (rilettura): per gli altri occupava GB di disco
        raw=json.dumps(listing.raw, default=str) if listing.source == "facebook" and listing.raw else None,
        problem_search=listing.problem_search,
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
    # 28/10 (controllo scraper): le correzioni del lettore AI (kW, cambio, versione, rata, danni) non vanno perse
    # alla rilettura successiva dell'annuncio: si tengono quelle salvate in lettura_ai.cambiati
    global _LETTURA_OK
    if not _LETTURA_OK:
        conn.execute("""CREATE TABLE IF NOT EXISTS lettura_ai (
            listing_id BIGINT PRIMARY KEY, prezzo INTEGER, dati JSONB, cambiati JSONB, computed_at TIMESTAMPTZ DEFAULT now())""")
        _LETTURA_OK = True
    _ai = "(SELECT a.cambiati FROM lettura_ai a WHERE a.listing_id = listings.id)"
    _tenere = {
        "power_kw": f"COALESCE(({_ai}->>'power_kw')::int, %(power_kw)s)",
        "gearbox": f"COALESCE({_ai}->>'gearbox', %(gearbox)s)",
        "version_raw": f"COALESCE({_ai}->>'version_raw', %(version_raw)s)",
        "damage_declared": f"COALESCE(({_ai}->>'damage_declared')::boolean, %(damage_declared)s)",
        "price_flags": f"(CASE WHEN {_ai} ? 'price_flags' THEN ARRAY(SELECT DISTINCT unnest(%(price_flags)s::text[] || ARRAY['leasing_o_rata'])) ELSE %(price_flags)s END)",
    }
    sets = ", ".join(f"{k} = {_tenere[k]}" if k in _tenere else f"{k} = %({k})s"
                     for k in params if k not in ("source", "source_id"))
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
