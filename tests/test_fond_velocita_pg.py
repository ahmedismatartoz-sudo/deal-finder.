"""16/11 revisione (fondamenta della velocità): la coda tr_coda e la scrittura degli invariati su Postgres VERO
(tests/pg_scheda.py, con tutte le migrazioni compresa la 058). Se Postgres non c'è, i test non fanno nulla."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder import db as DBM                          # noqa: E402
from dealfinder.core.models import Listing                # noqa: E402
from dealfinder.pipeline import tempo_reale as T          # noqa: E402

_C: dict = {}


def _pg():
    """Un database con tutte le migrazioni; la connessione restituisce i tempi come datetime (come psycopg)."""
    if "c" in _C:
        return _C["c"]
    try:
        import pg_scheda as P
    except Exception:
        _C["c"] = None
        return None
    base = P.nuovo_db("fond_velocita")
    if base is None:
        _C["c"] = None
        return None
    P.migra_tutte(base)

    class Conn(P.Conn):
        savepoint = 0

        @contextmanager
        def transaction(self):
            # ogni execute di pg_scheda è già una transazione a sé: un errore annulla solo quell'istruzione, come il
            # punto di ripristino di psycopg (conn.transaction() dentro una transazione aperta)
            Conn.savepoint += 1
            yield

        def execute(self, sql, params=None):
            r = super().execute(sql, params)
            for riga in r._r:
                for k, v in list(riga.items()):
                    if k.endswith("_at") and isinstance(v, str):
                        riga[k] = datetime.fromisoformat(v)
            return r
    c = Conn(base.d, base.db)
    _C["c"] = c
    _C["P"] = P
    return c


def _annuncio(c, sid, status="attivo", tel=None) -> int:
    return c.execute("INSERT INTO listings (source, source_id, url, make, model, year, mileage_km, price_eur, province, "
                     "seller_type, status, telefono_ok, last_seen_at) VALUES ('subito', %s, %s, 'fiat', 'panda', 2015, "
                     "90000, 5000, 'MI', 'privato', %s, %s, now() - interval '3 days') RETURNING id",
                     (sid, "https://x/" + sid, status, tel)).fetchone()["id"]


def _riga(c, lid):
    return c.execute("SELECT * FROM tr_coda WHERE listing_id = %s", (lid,)).fetchone()


def test_coda_ciclo_completo_su_postgres_vero(monkeypatch):
    c = _pg()
    if c is None:
        return
    monkeypatch.setenv("TR_CODA_RIPRESA_MIN", "0")
    assert T.coda_attiva(c) is True
    ids = [_annuncio(c, f"coda{i}") for i in range(4)]
    st = Counter()
    l = Listing(source="subito", source_id="coda0", url="u", raw={"dates": {"display": "2026-11-16 10:30:00"}})
    for lid in ids:
        T._coda_metti(c, lid, l, "nuovo", st)
    assert st["coda:messi"] == 4 and Conn_savepoint(c) >= 4
    r0 = _riga(c, ids[0])
    assert r0["tentativi"] == 1 and r0["preso_at"] is not None and r0["valutato_at"] is None
    assert r0["annuncio_at"] == datetime(2026, 11, 16, 9, 30, tzinfo=timezone.utc)
    T._coda_metti(c, ids[0], l, "prezzo", st)                    # ancora in attesa: resta com'è
    assert _riga(c, ids[0])["evento"] == "nuovo"

    # errore nella coda (id non valido): l'annuncio salvato resta, si conta l'errore
    T._coda_metti(c, "non-un-numero", l, "nuovo", st)
    assert st["coda:errore"] == 1 and c.execute("SELECT count(*) AS n FROM listings WHERE source_id LIKE 'coda%%'"
                                                ).fetchone()["n"] == 4

    # giro caduto: nessuno valutato. Il giro dopo li riprende (escluso quello già nel suo lotto)
    st2 = Counter()
    presi = T._coda_prendi(c, [ids[3]], st2)
    assert sorted(presi) == sorted(ids[:3]) and st2["coda:ripresi"] == 3
    assert _riga(c, ids[0])["tentativi"] == 2

    # SKIP LOCKED: una riga bloccata da un'altra sessione non si aspetta e non si prende
    c.execute("UPDATE tr_coda SET preso_at = NULL WHERE listing_id = ANY(%s::bigint[])", (ids,))
    blocco = subprocess.Popen(
        ["psql", "-h", c.d, "-p", "54347", "-U", "postgres", "-d", c.db, "-X", "-q", "-c",
         f"BEGIN; SELECT 1 FROM tr_coda WHERE listing_id = {ids[1]} FOR UPDATE; SELECT pg_sleep(4); COMMIT;"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            n = c.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE query LIKE '%%pg_sleep(4)%%' "
                          "AND pid <> pg_backend_pid()").fetchone()["n"]
            if n:
                break
            time.sleep(.1)
        time.sleep(.3)
        t0 = time.time()
        presi = T._coda_prendi(c, [], Counter())
        assert time.time() - t0 < 3                               # non ha aspettato il blocco
        assert ids[1] not in presi and set(presi) == {ids[0], ids[2], ids[3]}
    finally:
        blocco.wait(10)

    # valutati: righe chiuse, esito e tempi
    st3 = Counter()
    T._coda_fatti(c, ids, [ids[0]], st3)
    assert st3["coda:valutati"] == 4
    r0, r2 = _riga(c, ids[0]), _riga(c, ids[2])
    assert r0["esito"] == "pubblicata" and r0["pubblicato_at"] is not None and r0["valutato_at"] is not None
    assert r2["esito"] == "valutata" and r2["pubblicato_at"] is None
    assert T._coda_prendi(c, [], Counter()) == []                 # chiusi: niente da riprendere
    T._coda_metti(c, ids[2], l, "prezzo", st)                     # ribasso dopo la valutazione: torna in coda
    assert _riga(c, ids[2])["valutato_at"] is None and _riga(c, ids[2])["evento"] == "prezzo"

    # comparse sull'elenco del sito
    ris = T.segna_sul_sito(c, [ids[0], ids[1], 999999])
    assert ris["auto"] == 2 and "annuncio_sito_p95_min" in ris and _riga(c, ids[0])["sito_at"] is not None
    assert T.segna_sul_sito(c, [ids[0]]) == {}                    # una volta sola

    # arresto: rilascio; pulizia: abbandonate dopo troppi tentativi
    c.execute("UPDATE tr_coda SET preso_at = now() WHERE listing_id = %s", (ids[2],))
    T._coda_rilascia(c, [ids[2]])
    assert _riga(c, ids[2])["preso_at"] is None
    c.execute("UPDATE tr_coda SET tentativi = 9, preso_at = now() - interval '1 hour' WHERE listing_id = %s", (ids[2],))
    T._coda_pulita["t"] = 0.0
    T._coda_pulisci(c)
    assert _riga(c, ids[2])["esito"] == "abbandonata"


def Conn_savepoint(c) -> int:
    return type(c).savepoint


def test_segna_invariati_su_postgres_vero():
    c = _pg()
    if c is None:
        return
    a, b, sp = _annuncio(c, "inv1"), _annuncio(c, "inv2", tel=True), _annuncio(c, "inv3", status="scomparso")
    assert DBM.segna_invariati(c, [a, b, sp, a], [a, sp]) == 2      # solo gli attivi
    righe = {r["id"]: r for r in c.execute(
        "SELECT id, last_seen_at > now() - interval '1 minute' AS visto, last_checked_at IS NOT NULL AS controllato, "
        "telefono_ok, status FROM listings WHERE id = ANY(%s::bigint[])", ([a, b, sp],)).fetchall()}
    assert righe[a]["visto"] and righe[a]["controllato"] and righe[a]["telefono_ok"] is True
    assert righe[b]["visto"] and righe[b]["telefono_ok"] is True
    assert not righe[sp]["visto"] and righe[sp]["status"] == "scomparso"
    assert DBM.segna_invariati(c, []) == 0
