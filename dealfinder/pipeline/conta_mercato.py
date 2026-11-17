"""Conteggio del mercato (18/11 notte, cliente: "nel raggio di 150 km da Milano ci sono oltre 60.000 annunci, non li
leggiamo tutti"). SOLA LETTURA, nessuna AI, nessuna scrittura nel database.

Chiede ai due siti QUANTI annunci ci sono (una pagina per ricerca, si legge solo il totale) e li mette accanto a quanti
ne abbiamo noi attivi nel database, così si vede dove la raccolta ne perde:
- AutoScout24: CAP 20121 (Milano), raggio 150 km, auto usate, privati (P) e commercianti (D); con i filtri delle
  regole (immatricolate dal 2007, fino a 150.000 km) e senza filtri;
- Subito: Lombardia per provincia (i codici provincia del collettore), auto in vendita, senza filtri (l'API non
  filtra anno e km): il totale lo restituisce l'API ("count_all" o simili), se non c'è si scrivono le chiavi;
- noi: annunci attivi visti nelle ultime 48 ore per fonte e tipo di venditore, con e senza i filtri di anno/km, nelle stesse province.
Una riga di log "CONTA_MERCATO {json}". Lanciato con BREVO_IMPORT_LISTE=conta_mercato:ora (gira subito nel sito)."""
from __future__ import annotations

import logging
import os

log = logging.getLogger("dealfinder.pipeline")
PROVINCE = ("MI", "MB", "BG", "BS", "CO", "CR", "LC", "LO", "MN", "PV", "SO", "VA")


def _totale_subito(j: dict):
    for k in ("count_all", "total", "count", "total_count", "totalCount", "numberOfResults"):
        v = j.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return None


def autoscout() -> dict:
    from ..collectors.autoscout import AutoScoutCollector
    salva = {k: os.environ.get(k) for k in ("AUTOSCOUT_CAP", "AUTOSCOUT_RAGGIO", "AUTOSCOUT_KM_MAX", "AUTOSCOUT_CARB",
                                            "AUTOSCOUT_ADAGE")}
    out: dict = {}
    try:
        os.environ.update({"AUTOSCOUT_CAP": "20121", "AUTOSCOUT_RAGGIO": "150"})
        for k in ("AUTOSCOUT_CARB", "AUTOSCOUT_ADAGE"):
            os.environ.pop(k, None)
        c = AutoScoutCollector()
        for filtri, anni, km in (("regole", (2007, None), "150000"), ("tutti", (1900, None), None)):
            if km:
                os.environ["AUTOSCOUT_KM_MAX"] = km
            else:
                os.environ.pop("AUTOSCOUT_KM_MAX", None)
            for tipo in ("P", "D"):
                _, tot, ok = c._page(c.search_url(1, 1000000, 1, custtype=tipo, years=anni))
                out[f"{filtri}:{'privati' if tipo == 'P' else 'commercianti'}"] = tot if ok else "errore"
    finally:
        for k, v in salva.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return out


def subito() -> dict:
    from ..collectors.subito import SubitoCollector
    c = SubitoCollector()
    out: dict = {}
    keys = c._province_keys(4, set(PROVINCE), 100)
    chiavi_viste = None
    for sn in PROVINCE:
        key = keys.get(sn)
        if not key:
            out[sn] = "codice_mancante"
            continue
        r = c._api({"c": 2, "r": 4, "ci": key, "t": "s", "lim": 1, "start": 0})
        if r.status_code != 200:
            out[sn] = f"HTTP {r.status_code}"
            continue
        j = r.json() or {}
        tot = _totale_subito(j)
        if tot is None and chiavi_viste is None:
            chiavi_viste = sorted(j)[:30]
        out[sn] = tot
    if chiavi_viste is not None:
        out["_chiavi_risposta"] = chiavi_viste
    return out


NOSTRI_SQL = """
SELECT l.source AS fonte, coalesce(l.seller_type, 'privato') AS venditore, upper(l.province) AS prov,
       count(*) AS tutti,
       count(*) FILTER (WHERE l.year >= 2007 AND l.mileage_km BETWEEN 0 AND 150000) AS regole
FROM listings l
WHERE l.status = 'attivo' AND l.last_seen_at > now() - interval '48 hours'
  AND upper(l.province) = ANY(%(prov)s)
GROUP BY 1, 2, 3"""


def nostri() -> dict:
    import psycopg
    from psycopg.rows import dict_row

    from ..config import settings
    out: dict = {}
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET statement_timeout = '300s'")
        for r in conn.execute(NOSTRI_SQL, {"prov": list(PROVINCE)}).fetchall():
            d = out.setdefault(f'{r["fonte"]}:{r["venditore"]}', {"tutti": 0, "regole": 0, "per_provincia": {}})
            d["tutti"] += r["tutti"]
            d["regole"] += r["regole"]
            d["per_provincia"][r["prov"]] = d["per_provincia"].get(r["prov"], 0) + r["regole"]
    return out


def lavoro(spec: str = "") -> dict:
    esito: dict = {"province": list(PROVINCE)}
    for nome, fn in (("autoscout24_sito", autoscout), ("subito_sito", subito), ("noi", nostri)):
        try:
            esito[nome] = fn()
        except Exception as e:                       # un pezzo che fallisce non ferma gli altri
            esito[nome] = f"errore {type(e).__name__}: {str(e)[:200]}"
    return esito
