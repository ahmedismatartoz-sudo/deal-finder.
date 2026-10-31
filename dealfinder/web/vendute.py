"""31/10 sera (cliente): sezione "Già vendute" — le auto passate sul sito (o che avevano il margine per entrarci)
e sparite dagli annunci negli ultimi 7 giorni. Serve a far vedere al commerciante gli affari già andati.

Onestà: un annuncio sparito vuol dire quasi sempre venduto, ma può anche essere ritirato, e non sappiamo chi l'ha
comprato. Per questo la sezione dice "sparite dagli annunci", non "comprate dai nostri clienti".
Niente contatti: niente link all'annuncio, niente telefono, niente nome del venditore; della zona solo la provincia.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from ..db import connect
from . import foto, scovo

log = logging.getLogger("scovo")

_TABELLA = """CREATE TABLE IF NOT EXISTS sito_storico (
    listing_id BIGINT PRIMARY KEY,
    dati JSONB NOT NULL,
    prima TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultima TIMESTAMPTZ NOT NULL DEFAULT now())"""

_ultimo_salvataggio = {"t": 0.0}
_cache = {"t": 0.0, "dati": None}
_lock = threading.Lock()

FASCE = [(0, 3000), (3000, 6000), (6000, 10000), (10000, 15000), (15000, 25000), (25000, 50000), (50000, 10**9)]


def _provincia(zona) -> str | None:
    m = re.search(r"\((\w\w)\)\s*$", str(zona or ""))
    return m.group(1).upper() if m else None


def salva_sito(items: list[dict]) -> None:
    """Dopo ogni calcolo dell'elenco: ricorda le auto mostrate sul sito (al massimo ogni 10 minuti)."""
    if time.time() - _ultimo_salvataggio["t"] < 600:
        return
    _ultimo_salvataggio["t"] = time.time()
    righe = []
    for it in items:
        pv = it.get("pv") or {}
        marg = pv.get("margine")
        if marg is None or not it.get("prezzo"):
            continue
        righe.append((int(it["id"]), json.dumps({
            "nome": it.get("nome"), "marca": it.get("marca"), "anno": it.get("anno"), "km": it.get("km"),
            "carb": it.get("carb"), "prov": _provincia(it.get("zona")), "prezzo": int(it["prezzo"]),
            "mercato": pv.get("M") or it.get("mercato"), "margine": int(marg), "supercar": bool(it.get("supercar")),
        }, ensure_ascii=False)))
    if not righe:
        return
    try:
        with connect() as conn:
            conn.execute(_TABELLA)
            for k in range(0, len(righe), 500):
                blocco = righe[k:k + 500]
                conn.execute(
                    "INSERT INTO sito_storico (listing_id, dati) SELECT * FROM unnest(%s::bigint[], %s::jsonb[]) "
                    "ON CONFLICT (listing_id) DO UPDATE SET dati = EXCLUDED.dati, ultima = now()",
                    ([r[0] for r in blocco], [r[1] for r in blocco]))
        log.warning("VENDUTE storico sito salvato: %d auto", len(righe))
    except Exception as e:
        log.warning("VENDUTE storico non salvato: %s", str(e)[:200])


# auto sparite negli ultimi 7 giorni, già passate sul sito
_SQL_SITO = """
SELECT s.listing_id AS id, s.dati, l.first_seen_at, l.disappeared_at,
       (SELECT count(*) FROM listing_photos p WHERE p.listing_id = l.id) AS n_foto
FROM sito_storico s JOIN listings l ON l.id = s.listing_id
WHERE l.status = 'scomparso' AND l.disappeared_at > now() - make_interval(days => %(giorni)s)
"""

# 31/10: prima della sezione non tenevamo lo storico del sito. Per i giorni passati si prendono le auto di privati
# della zona che avevano il margine minimo del sito con la stessa regola (rivendita prudente − prezzo − costi fissi)
_SQL_RICOSTRUITE = """
SELECT l.id, l.make, l.model, l.version_raw, l.title, l.year, l.mileage_km, l.fuel, l.province, l.price_eur,
       l.first_seen_at, l.disappeared_at, v.resale_prudent, v.private_median, v.n_comparables,
       (SELECT count(*) FROM listing_photos p WHERE p.listing_id = l.id) AS n_foto
FROM listings l
JOIN LATERAL (SELECT resale_prudent, private_median, n_comparables, fraud_flags FROM valuations
              WHERE listing_id = l.id ORDER BY created_at DESC LIMIT 1) v ON true
WHERE l.status = 'scomparso' AND l.disappeared_at > now() - make_interval(days => %(giorni)s)
  AND l.seller_type = 'privato' AND upper(l.province) = ANY(%(prov)s)
  AND l.price_eur BETWEEN 1000 AND 300000 AND coalesce(l.mileage_km, 0) <= 150000
  AND coalesce(l.damage_class, 'nessuno') IN ('nessuno', 'leggero')
  AND NOT ('leasing' = ANY(l.price_flags))
  AND v.n_comparables >= 5 AND v.resale_prudent IS NOT NULL
  AND coalesce(array_length(v.fraud_flags, 1), 0) = 0
  AND l.price_eur >= 0.5 * coalesce(v.private_median, 0)     -- metà prezzo o meno: spesso truffa o prezzo sbagliato
  AND v.resale_prudent - l.price_eur - %(costi)s >= CASE WHEN l.price_eur <= 4000 THEN 800 ELSE 1500 END
"""


def _giorni(a, b) -> int | None:
    try:
        return max(1, round((b - a).total_seconds() / 86400))
    except Exception:
        return None


def _calcola() -> dict:
    giorni = int(os.environ.get("VENDUTE_GIORNI", "7"))
    prov = [p.strip().upper() for p in (os.environ.get("ZONA_SITO") or "").split(",") if p.strip()]
    costi = sum(scovo.COSTI_FISSI.values())
    auto = {}
    with connect() as conn:
        conn.execute(_TABELLA)
        conn.execute("SET LOCAL statement_timeout = '%ds'" % int(os.environ.get("VENDUTE_TIMEOUT_S", "90")))
        for r in conn.execute(_SQL_SITO, {"giorni": giorni}).fetchall():
            d = r["dati"] if isinstance(r["dati"], dict) else json.loads(r["dati"])
            if prov and d.get("prov") and d["prov"] not in prov and not d.get("supercar"):
                continue
            auto[r["id"]] = dict(d, id=r["id"], giorni=_giorni(r["first_seen_at"], r["disappeared_at"]),
                                 sparita=r["disappeared_at"].isoformat() if r["disappeared_at"] else None,
                                 foto=foto.url_for(r["id"], 0) if r["n_foto"] else None, origine="sito")
        if prov:
            for r in conn.execute(_SQL_RICOSTRUITE, {"giorni": giorni, "prov": prov, "costi": costi}).fetchall():
                if r["id"] in auto:
                    continue
                nome = " ".join(x for x in [r["make"], r["model"], r["version_raw"]] if x) or r["title"]
                auto[r["id"]] = {
                    "id": r["id"], "nome": nome, "marca": r["make"], "anno": r["year"], "km": r["mileage_km"],
                    "carb": r["fuel"], "prov": (r["province"] or "").upper() or None, "prezzo": int(r["price_eur"]),
                    "mercato": r["private_median"], "margine": int(r["resale_prudent"] - r["price_eur"] - costi),
                    "giorni": _giorni(r["first_seen_at"], r["disappeared_at"]),
                    "sparita": r["disappeared_at"].isoformat() if r["disappeared_at"] else None,
                    "foto": foto.url_for(r["id"], 0) if r["n_foto"] else None, "origine": "calcolo"}
    # le migliori N coprendo tutte le fasce di prezzo: a turno, da ogni fascia la prossima col margine più alto
    quante = int(os.environ.get("VENDUTE_QUANTE", "100"))
    per_fascia = [sorted([a for a in auto.values() if lo <= a["prezzo"] < hi], key=lambda a: -a["margine"])
                  for lo, hi in FASCE]
    scelte = []
    while len(scelte) < quante and any(per_fascia):
        for f in per_fascia:
            if f and len(scelte) < quante:
                scelte.append(f.pop(0))
    scelte.sort(key=lambda a: -a["margine"])
    for a in scelte:
        a.pop("id", None)          # niente id: dalla sezione non si apre l'annuncio né il contatto
    tot = sum(a["margine"] for a in scelte)
    log.warning("VENDUTE %d auto (su %d sparite col margine), margine totale %d €, dal sito %d", len(scelte),
                len(auto), tot, sum(1 for a in scelte if a["origine"] == "sito"))
    return {"items": scelte, "giorni": giorni, "margine_totale": tot, "totale_sparite": len(auto),
            "aggiornato": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def dati() -> dict:
    with _lock:
        if _cache["dati"] is None or time.time() - _cache["t"] > int(os.environ.get("VENDUTE_CACHE_S", "1800")):
            _cache["dati"] = _calcola()
            _cache["t"] = time.time()
        return _cache["dati"]
