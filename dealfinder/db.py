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

# 3/11 (studio 4): colonne nuove (migrazione 033). Le raccolte del cron possono partire prima che il sito abbia
# applicato la migrazione: si scrive una colonna nuova solo se c'è davvero (controllo una volta per processo,
# ripetuto ogni 10 minuti finché manca). Senza colonna tutto funziona come prima.
_SCHEMA: dict = {"colonne": None, "indici": None, "at": 0.0}


def _schema(conn) -> tuple[set, set]:
    import time
    if _SCHEMA["colonne"] is not None:
        completo = len(_SCHEMA["colonne"]) == 2 and bool(_SCHEMA["indici"])
        if time.time() - _SCHEMA["at"] < (86400 if completo else 600):
            return _SCHEMA["colonne"], _SCHEMA["indici"]
    try:
        col = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='listings' "
            "AND column_name IN ('disappear_reason','fuel_tipo')").fetchall()}
        idx = {r["indexname"] for r in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename='listings' AND indexname='listings_vehicle_idx'").fetchall()}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        col, idx = set(), set()
    _SCHEMA.update(colonne=col, indici=idx, at=time.time())
    return col, idx


def ha_colonna(conn, nome: str) -> bool:
    return nome in _schema(conn)[0]


# Stati di un annuncio non più online. Solo 'scomparso' vale come "venduta" per valore reale, vendute e calibra:
# 'scaduto' (annuncio scaduto sul portale) e 'ripubblicato' (stesso veicolo rimesso in vendita) NON sono vendite.
STATI_SPARITI = ("scomparso", "scaduto", "ripubblicato")
RIPUBBLICATO_GIORNI = 30


def ripubblicazione(conn, lid: int, vehicle_id: int | None, listing: Listing) -> int:
    """Annuncio NUOVO: se lo stesso veicolo (vehicle_id + stesso comune + km ±1.500) era sparito negli ultimi 30
    giorni, quello vecchio diventa 'ripubblicato' (non venduto). Solo con l'indice su vehicle_id (niente scansioni
    della tabella a ogni annuncio). Ritorna quanti annunci vecchi sono stati cambiati."""
    if not vehicle_id or "listings_vehicle_idx" not in _schema(conn)[1]:
        return 0
    try:
        with conn.transaction():          # punto di ripristino: un errore qui non perde l'annuncio appena salvato
            return _ripubblica(conn, lid, vehicle_id, listing)
    except Exception:
        return 0


def _ripubblica(conn, lid: int, vehicle_id: int, listing: Listing) -> int:
    rows = conn.execute(
        """SELECT id, city, mileage_km FROM listings
           WHERE vehicle_id=%s AND id<>%s AND status IN ('scomparso','scaduto')
             AND COALESCE(disappeared_at, last_checked_at) > now() - make_interval(days => %s)""",
        (vehicle_id, lid, RIPUBBLICATO_GIORNI)).fetchall()
    n = 0
    for r in rows:
        if listing.city and r.get("city") and _citta(r["city"]) != _citta(listing.city):
            continue
        if listing.mileage_km is not None and r.get("mileage_km") is not None \
                and abs(int(r["mileage_km"]) - int(listing.mileage_km)) > 1500:
            continue
        motivo = ", disappear_reason='ripubblicato'" if ha_colonna(conn, "disappear_reason") else ""
        conn.execute(f"UPDATE listings SET status='ripubblicato'{motivo} WHERE id=%s", (r["id"],))
        conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'ripubblicato',%s)",
                     (r["id"], listing.price_eur))
        n += 1
    return n


def _citta(c: str) -> str:
    import re
    import unicodedata
    c = unicodedata.normalize("NFKD", str(c or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", c.split(",")[0])


_DB_GB: dict = {"gb": None, "at": 0.0}


def db_gb(conn) -> float | None:
    """Dimensione del database in GB (letta al massimo ogni 10 minuti)."""
    import time
    if _DB_GB["gb"] is not None and time.time() - _DB_GB["at"] < 600:
        return _DB_GB["gb"]
    try:
        gb = float(conn.execute("SELECT pg_database_size(current_database()) / 1e9 AS gb").fetchone()["gb"])
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    _DB_GB.update(gb=gb, at=time.time())
    return gb


def db_pieno(conn) -> bool:
    """3/11: oltre CE_DB_MAX_GB (15) si smette di salvare gli annunci che servono SOLO come confronti (commercianti
    del giro della zona, auto fuori regole, rilettura settimanale, confronti fuori zona). Il resto continua."""
    import os
    gb = db_gb(conn)
    return gb is not None and gb > float(os.environ.get("CE_DB_MAX_GB", "15"))


def assicura_indice_veicolo() -> str:
    """Indice su vehicle_id (riconoscere le ripubblicazioni) creato CONCURRENTLY, solo nel cron: nessun blocco
    della tabella. Se resta a metà (riavvio) si rifà al giro dopo."""
    import psycopg
    try:
        with psycopg.connect(settings.database_url, autocommit=True) as c:
            r = c.execute("""SELECT i.indisvalid FROM pg_class x JOIN pg_index i ON i.indexrelid = x.oid
                             WHERE x.relname = 'listings_vehicle_idx'""").fetchone()
            if r is not None and r[0]:
                return "pronto"
            if r is not None:
                c.execute("DROP INDEX CONCURRENTLY IF EXISTS listings_vehicle_idx")
            c.execute("SET statement_timeout = 0")
            c.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS listings_vehicle_idx ON listings (vehicle_id)")
            _SCHEMA["colonne"] = None
            return "creato"
    except Exception as e:
        return "errore: " + str(e)[:120]


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
    if getattr(listing, "fuel_tipo", None) is not None and ha_colonna(conn, "fuel_tipo"):
        params["fuel_tipo"] = listing.fuel_tipo
    if prev is None:
        cols = ", ".join(params)
        vals = ", ".join(f"%({k})s" for k in params)
        lid = conn.execute(f"INSERT INTO listings ({cols}) VALUES ({vals}) RETURNING id", params).fetchone()["id"]
        conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'apparso',%s)",
                     (lid, listing.price_eur))
        _save_photos(conn, lid, listing.photos)
        ripubblicazione(conn, lid, vehicle_id, listing)
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
        # 2/11 sera: un nuovo passaggio dello scraper senza il dato NON cancella quello già ricavato dal testo
        "power_kw": f"COALESCE(({_ai}->>'power_kw')::int, %(power_kw)s, listings.power_kw)",
        "gearbox": f"COALESCE({_ai}->>'gearbox', %(gearbox)s, listings.gearbox)",
        "version_raw": f"COALESCE({_ai}->>'version_raw', %(version_raw)s, listings.version_raw)",
        "fuel": "COALESCE(%(fuel)s, listings.fuel)",
        "year": "COALESCE(%(year)s, listings.year)",
        "mileage_km": "COALESCE(%(mileage_km)s, listings.mileage_km)",
        "damage_declared": f"COALESCE(({_ai}->>'damage_declared')::boolean, %(damage_declared)s)",
        "price_flags": f"(CASE WHEN {_ai} ? 'price_flags' THEN ARRAY(SELECT DISTINCT unnest(%(price_flags)s::text[] || ARRAY['leasing_o_rata'])) ELSE %(price_flags)s END)",
    }
    sets = ", ".join(f"{k} = {_tenere[k]}" if k in _tenere else f"{k} = %({k})s"
                     for k in params if k not in ("source", "source_id"))
    sql_prima = (f"UPDATE listings SET {sets}, last_seen_at = now(), status='attivo', disappeared_at=NULL "
                 f"WHERE id = %(id)s")
    if listing.source in ("subito", "autoscout24"):
        _aggiorna_senza_perdere(conn, listing, lid, params, _tenere, sql_prima)
    else:
        conn.execute(sql_prima, {**params, "id": lid})
    event = "invariato"
    if prev["status"] in STATI_SPARITI:
        event = "riapparso"
        conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'riapparso',%s)",
                     (lid, listing.price_eur))
        if ha_colonna(conn, "disappear_reason"):
            conn.execute("UPDATE listings SET disappear_reason=NULL WHERE id=%s", (lid,))
    if prev["price_eur"] != listing.price_eur:
        event = "prezzo"
        conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'prezzo',%s)",
                     (lid, listing.price_eur))
    return lid, event


# 3/11 (Subito e AutoScout24): una nuova lettura SENZA un dato non cancella più quello già salvato. Es. le pagine di
# ricerca di AutoScout24 non hanno la descrizione (la pagina dell'annuncio sì): a ogni giro della zona la
# descrizione tornava NULL (numero di telefono nel testo, danni scritti, tutto perso). Venditore 'sconosciuto' non
# sostituisce privato/commerciante. Le foto si aggiungono se la nuova lettura ne ha più di quelle salvate
# (prima si salvavano solo alla prima lettura).
_TENERE_SCRAPER = {
    "title": "COALESCE(NULLIF(%(title)s, ''), listings.title)",
    "description": "COALESCE(NULLIF(%(description)s, ''), listings.description)",
    "make": "COALESCE(%(make)s, listings.make)",
    "model": "COALESCE(%(model)s, listings.model)",
    "city": "COALESCE(%(city)s, listings.city)",
    "province": "COALESCE(%(province)s, listings.province)",
    "region": "COALESCE(%(region)s, listings.region)",
    "seller_type": "COALESCE(NULLIF(%(seller_type)s, 'sconosciuto'), listings.seller_type, %(seller_type)s)",
}
_avvisi_upsert = {"n": 0}


def _aggiorna_senza_perdere(conn, listing: Listing, lid: int, params: dict, tenere: dict, sql_prima: str) -> None:
    """UPDATE che tiene i dati già salvati, dentro un punto di ripristino: se qualcosa va storto si torna
    all'UPDATE di prima (stesso risultato di sempre), mai un annuncio perso."""
    regole = {**tenere, **_TENERE_SCRAPER}
    sets = ", ".join(f"{k} = {regole[k]}" if k in regole else f"{k} = %({k})s"
                     for k in params if k not in ("source", "source_id"))
    sql = (f"UPDATE listings SET {sets}, last_seen_at = now(), status='attivo', disappeared_at=NULL "
           f"WHERE id = %(id)s RETURNING (SELECT count(*) FROM listing_photos p WHERE p.listing_id = listings.id) AS n_foto")
    try:
        with conn.transaction():
            row = conn.execute(sql, {**params, "id": lid}).fetchone()
    except Exception as e:
        if _avvisi_upsert["n"] < 5:
            _avvisi_upsert["n"] += 1
            import logging
            logging.getLogger(__name__).warning("upsert senza perdite non riuscito (uso quello di prima): %s", str(e)[:200])
        conn.execute(sql_prima, {**params, "id": lid})
        return
    try:
        n_foto = row["n_foto"] if row else None
        if listing.photos and n_foto is not None and int(n_foto) < min(len(listing.photos), 30):
            with conn.transaction():
                _save_photos(conn, lid, listing.photos)
    except Exception as e:
        if _avvisi_upsert["n"] < 5:
            _avvisi_upsert["n"] += 1
            import logging
            logging.getLogger(__name__).warning("foto in più non salvate: %s", str(e)[:200])


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
