"""Impostazioni del sito nel database (12/11, cliente: "odio che per ogni azione il sito si debba riavviare").

Prima ribassi, auto in cima e auto tolte stavano nel codice (scovo.RIBASSO_SCHEMA, scovo.IN_CIMA,
data/scarti_sito.json) o in variabili di Render: ogni cambio voleva un deploy e il sito restava vuoto per minuti.
Ora stanno in due tabelle (migrazioni 047 e 048) e si cambiano dal pannello /gestione → Sito:

  sito_impostazioni (chiave, valore jsonb)
      'ribasso'  {"attivo": bool, "schema": "da:euro,...", "fino": "ISO con fuso" | null}
      'in_cima'  [{"id": 123} | {"marca": "fiat", "modello": "500", "km": 68000, "prezzo": 7900}, + "mostra": 6900, "nota"]
  sito_scarti (auto tolte dal cliente: per id annuncio oppure marca/modello/anno/km/prezzo; attivo=false = rimessa)

Il sito le rilegge al massimo ogni SITO_IMPOSTAZIONI_CACHE_S secondi (45): una modifica dal pannello si vede entro un
minuto, senza riavvio (nel processo che la riceve subito). Se le tabelle non ci sono o sono vuote vale quello che c'era
prima (costanti del codice, variabili d'ambiente, data/scarti_sito.json): il sito funziona uguale.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("impostazioni")

CACHE_S = float(os.environ.get("SITO_IMPOSTAZIONI_CACHE_S", "45"))
RIBASSO_MAX = 3000                       # come prima: nessuna fascia toglie più di 3.000 €
_stato: dict = {"t": float("-inf"), "dati": {}, "firma": None, "versione": 0}
_lock = threading.Lock()
_lettore: dict = {"f": None}             # per i test: funzione che ritorna i dati al posto del database


# ------------------------------------------------------------------------------------------------ lettura con cache
def usa_lettore(f) -> None:
    """Test: f() -> {"ribasso":..., "in_cima":..., "scarti": [...]} al posto del database (None = database)."""
    _lettore["f"] = f
    invalida()


def invalida() -> None:
    """La prossima lettura va al database (dopo una modifica dal pannello)."""
    _stato["t"] = float("-inf")


def leggi_db(conn) -> dict:
    """Le impostazioni salvate. Chiavi presenti solo se nel database c'è qualcosa (altrimenti valgono i default)."""
    out: dict = {}
    r = conn.execute("SELECT to_regclass('sito_impostazioni') AS a, to_regclass('sito_scarti') AS b").fetchone()
    if r and r["a"]:
        for x in conn.execute("SELECT chiave, valore FROM sito_impostazioni").fetchall():
            v = x["valore"]
            out[x["chiave"]] = json.loads(v) if isinstance(v, str) else v
    if r and r["b"]:
        righe = [dict(x) for x in conn.execute(
            "SELECT id AS sid, listing_id, marca, modello, anno, km, prezzo, motivo, attivo, creato_at::text AS creato "
            "FROM sito_scarti ORDER BY id").fetchall()]
        if righe:                                   # tabella vuota = mai usata: vale ancora data/scarti_sito.json
            out["scarti"] = [x for x in righe if x.get("attivo")]
    return out


def _da_db() -> dict:
    if _lettore["f"] is not None:
        return _lettore["f"]() or {}
    if not os.environ.get("DATABASE_URL"):
        return {}
    from ..db import connect
    with connect() as conn:
        conn.execute("SET LOCAL statement_timeout = '3s'")
        return leggi_db(conn)


def dati() -> dict:
    """Impostazioni correnti (copia in memoria, rinfrescata al massimo ogni CACHE_S secondi). Mai un errore: se il
    database non risponde resta l'ultima lettura buona."""
    if time.monotonic() - _stato["t"] < CACHE_S:
        return _stato["dati"]
    # un altro thread sta già leggendo: si usa la lettura di prima; alla PRIMA lettura (niente di prima) si aspetta
    # quella dell'altro thread, al massimo 3 secondi, invece di partire con i valori del codice
    if not (_lock.acquire(timeout=3) if _stato["firma"] is None else _lock.acquire(blocking=False)):
        return _stato["dati"]
    try:
        if time.monotonic() - _stato["t"] < CACHE_S:  # l'ha appena letta l'altro thread
            return _stato["dati"]
        try:
            d = _da_db()
        except Exception as e:
            log.warning("IMPOSTAZIONI non lette (resta l'ultima lettura): %s", str(e)[:160])
            _stato["t"] = time.monotonic() - CACHE_S + 10        # si riprova tra 10 secondi
            return _stato["dati"]
        firma = json.dumps(d, sort_keys=True, default=str)
        if firma != _stato["firma"]:
            _stato.update(dati=d, firma=firma, versione=_stato["versione"] + 1)
        _stato["t"] = time.monotonic()
        return _stato["dati"]
    finally:
        _lock.release()


def versione() -> int:
    """Cambia ogni volta che le impostazioni lette sono diverse dalle precedenti (per ri-filtrare l'elenco)."""
    dati()
    return _stato["versione"]


# ------------------------------------------------------------------------------------------------ ribassi
def fasce_da_schema(schema: str) -> list[tuple[int, int]]:
    """"2000:700,6000:1000" → [(2000, 700), (6000, 1000)]; euro tra 0 e 3.000. Solleva ValueError se scritto male."""
    out = []
    for x in str(schema or "").split(","):
        if not x.strip():
            continue
        a, b = x.split(":")
        out.append((int(a), max(0, min(RIBASSO_MAX, int(b)))))
    return sorted(out)


def _ora_iso(s) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def ribasso() -> dict | None:
    """Impostazione del ribasso dal database, None se non c'è (allora valgono codice e variabili come prima)."""
    r = dati().get("ribasso")
    return r if isinstance(r, dict) else None


def ribasso_acceso(r: dict, ora=None) -> bool:
    """Regola del pannello: comanda l'interruttore. Acceso con "fino a" vuoto = acceso finché non lo spegni; acceso con
    una data = si spegne da solo a quell'ora. (Senza impostazioni nel database vale scovo: variabile vuota = spento.)"""
    if not r or not r.get("attivo"):
        return False
    try:
        fino = _ora_iso(r.get("fino"))
    except ValueError:
        return False                                  # data scritta male: spento (mai ribassi per sempre)
    return fino is None or (ora or datetime.now(timezone.utc)) < fino


def fasce_ribasso(r: dict, ora=None) -> list[tuple[int, int]]:
    if not ribasso_acceso(r, ora):
        return []
    try:
        return fasce_da_schema(r.get("schema"))
    except Exception:
        return []


# ------------------------------------------------------------------------------------------------ auto in cima
def in_cima() -> list | None:
    v = dati().get("in_cima")
    return v if isinstance(v, list) else None


def _testo(x) -> str:
    return str(x or "").strip().lower().replace("-", " ")


def corrisponde(e: dict, it: dict) -> bool:
    """Una voce (in cima o tolta) riconosce l'auto: per id annuncio, oppure marca (+ modello, anno, km, prezzo vero)."""
    if e.get("id") or e.get("listing_id"):
        return it.get("id") == int(e.get("id") or e.get("listing_id"))
    if not e.get("marca"):
        return False
    if not _testo(it.get("marca")).startswith(_testo(e["marca"])):
        return False
    if e.get("modello") and _testo(e["modello"]) not in _testo(it.get("modello") or it.get("nome")):
        return False
    vero = int(it.get("prezzo_annuncio") or it.get("prezzo") or 0)
    for k, v in (("anno", it.get("anno")), ("km", it.get("km")), ("prezzo", vero)):
        if e.get(k) is not None and int(v or 0) != int(e[k]):
            return False
    return True


# ------------------------------------------------------------------------------------------------ auto tolte
def scarti() -> list | None:
    """Auto tolte dal cliente (solo quelle ancora fuori), None se la tabella non è usata."""
    v = dati().get("scarti")
    return v if isinstance(v, list) else None


def scarti_ids() -> set | None:
    s = scarti()
    return None if s is None else {int(x["listing_id"]) for x in s if x.get("listing_id")}


def togli_scartate(items: list[dict]) -> list[dict]:
    """L'elenco già calcolato senza le auto tolte dal pannello (nessun ricalcolo)."""
    s = scarti()
    if not s:
        return items
    ids = {int(x["listing_id"]) for x in s if x.get("listing_id")}
    altre = [x for x in s if not x.get("listing_id")]
    return [it for it in items if it.get("id") not in ids and not any(corrisponde(e, it) for e in altre)]


# ------------------------------------------------------------------------------------------------ scrittura (pannello)
CHIAVI = ("ribasso", "in_cima")


def valida_ribasso(d: dict) -> dict:
    schema = str(d.get("schema") or "").replace(" ", "")
    fasce = fasce_da_schema(schema)                     # ValueError se scritto male
    if d.get("attivo") and not fasce:
        raise ValueError("Scrivi almeno una fascia di ribasso")
    fino = d.get("fino") or None
    if fino:
        _ora_iso(fino)                                  # ValueError se la data non è valida
    return {"attivo": bool(d.get("attivo")), "schema": ",".join(f"{a}:{b}" for a, b in fasce), "fino": fino}


def _like(s: str) -> str:
    """Testo letterale dentro ILIKE: % e _ scritti nel pannello non fanno da jolly."""
    return str(s).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtri_listing(e: dict) -> tuple[list[str], list]:
    filtri, par = ["make ILIKE %s", "mileage_km=%s", "price_eur=%s"], [_like(e["marca"]) + "%", e["km"], e["prezzo"]]
    if e.get("modello"):
        filtri.append("model ILIKE %s")
        par.append("%" + _like(e["modello"]) + "%")
    if e.get("anno"):
        filtri.append("year=%s")
        par.append(e["anno"])
    return filtri, par


def _intero(v, nome: str, lo: int = 0, hi: int = 10_000_000) -> int | None:
    if v in (None, ""):
        return None
    try:
        n = int(round(float(str(v).replace(".", "").replace(",", ".")) if isinstance(v, str) else float(v)))
    except (TypeError, ValueError):
        raise ValueError(f"{nome}: numero non valido")
    if not lo <= n <= hi:
        raise ValueError(f"{nome}: numero fuori misura")
    return n


def valida_voce(d: dict, mostra: bool = False) -> dict:
    """Voce per id annuncio oppure per marca + km + prezzo (modello e anno facoltativi), come in data/scarti_sito.json."""
    out = {}
    lid = _intero(d.get("id") or d.get("listing_id"), "Id annuncio", 1, 10**12)
    if lid:
        out["id"] = lid
    else:
        marca = str(d.get("marca") or "").strip().lower()[:40]
        km, prezzo = _intero(d.get("km"), "Km"), _intero(d.get("prezzo"), "Prezzo", 1)
        if not marca or km is None or prezzo is None:
            raise ValueError("Serve l'id dell'annuncio, oppure marca, km e prezzo dell'annuncio")
        out.update(marca=marca, km=km, prezzo=prezzo)
        if str(d.get("modello") or "").strip():
            out["modello"] = str(d["modello"]).strip().lower()[:40]
        anno = _intero(d.get("anno"), "Anno", 1950, 2100)
        if anno:
            out["anno"] = anno
    if mostra:
        m = _intero(d.get("mostra"), "Prezzo da mostrare", 1)
        if m is not None:
            if out.get("prezzo") and m > out["prezzo"]:
                raise ValueError("Il prezzo mostrato non può essere più alto di quello dell'annuncio")
            out["mostra"] = m
    if str(d.get("nota") or "").strip():
        out["nota"] = str(d["nota"]).strip()[:120]
    return out


def scrivi(conn, chiave: str, valore, da: str | None = None) -> None:
    if chiave not in CHIAVI:
        raise ValueError("Impostazione sconosciuta")
    conn.execute("INSERT INTO sito_impostazioni (chiave, valore, aggiornato, da) VALUES (%s, %s::jsonb, now(), %s) "
                 "ON CONFLICT (chiave) DO UPDATE SET valore = EXCLUDED.valore, aggiornato = now(), da = EXCLUDED.da",
                 (chiave, json.dumps(valore), da))
    conn.commit()
    invalida()


def aggiungi_scarto(conn, voce: dict, motivo: str, da: str | None = None) -> int:
    r = conn.execute("INSERT INTO sito_scarti (listing_id, marca, modello, anno, km, prezzo, motivo, da) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                     (voce.get("id"), voce.get("marca"), voce.get("modello"), voce.get("anno"), voce.get("km"),
                      voce.get("prezzo"), (motivo or "tolta dal cliente")[:200], da)).fetchone()
    applica_scarto_listing(conn, dict(voce, listing_id=voce.get("id"), motivo=motivo))
    conn.commit()
    invalida()
    return int(r["id"])


def applica_scarto_listing(conn, e: dict) -> int:
    """Come pipeline/manuale.applica_scarti_cliente: l'annuncio resta fuori anche dalle raccolte e dai lavori."""
    if e.get("listing_id"):
        return conn.execute("UPDATE listings SET stage='scartato', stage_reason=%s WHERE id=%s "
                            "AND stage IS DISTINCT FROM 'scartato'",
                            ("analisi_manuale: tolta dal cliente: " + str(e.get("motivo") or "")[:120], e["listing_id"])).rowcount
    if not e.get("marca") or e.get("km") is None or e.get("prezzo") is None:
        return 0
    filtri, par = _filtri_listing(e)
    return conn.execute("UPDATE listings SET stage='scartato', stage_reason='analisi_manuale: tolta dal cliente' WHERE "
                        + " AND ".join(filtri) + " AND stage IS DISTINCT FROM 'scartato'", tuple(par)).rowcount


def rimetti_scarto(conn, sid: int, da: str | None = None) -> dict | None:
    """L'auto torna sul sito: la voce si spegne e l'annuncio torna "da verificare" (come il "rimetti" di prima).
    Ritorna la voce (None se non c'era)."""
    r = conn.execute("SELECT id, listing_id, marca, modello, anno, km, prezzo, motivo FROM sito_scarti "
                     "WHERE id=%s AND attivo", (sid,)).fetchone()
    if not r:
        return None
    e = dict(r)
    conn.execute("UPDATE sito_scarti SET attivo=false, rimesso_at=now(), rimesso_da=%s WHERE id=%s", (da, sid))
    if e.get("listing_id"):
        conn.execute("UPDATE listings SET stage='approfondito', stage_reason='da_verificare', last_checked_at=now() "
                     "WHERE id=%s AND stage='scartato' AND stage_reason LIKE 'analisi_manuale:%%'", (e["listing_id"],))
    elif e.get("marca") and e.get("km") is not None and e.get("prezzo") is not None:
        filtri, par = _filtri_listing(e)
        conn.execute("UPDATE listings SET stage='approfondito', stage_reason='da_verificare', last_checked_at=now() WHERE "
                     + " AND ".join(filtri) + " AND stage='scartato' AND stage_reason ILIKE '%%tolta dal cliente%%'", tuple(par))
    conn.commit()
    invalida()
    return e
