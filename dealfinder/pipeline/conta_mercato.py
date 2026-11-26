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
# 26/11 (cliente: "forse non leggiamo bene quelle città"): anche le province vicine fuori dalla Lombardia, per regione
# di Subito (2 Piemonte, 8 Emilia-Romagna). Solo conteggio: nessuna regola cambia.
PROVINCE_ALTRE = {2: ("NO", "AL", "VB"), 8: ("PC",)}


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
    chiavi_viste = None
    gruppi = [(4, PROVINCE)] + list(PROVINCE_ALTRE.items())
    for reg, prov in gruppi:
        keys = c._province_keys(reg, set(prov), 100)
        for sn in prov:
            key = keys.get(sn)
            if not key:
                out[sn] = "codice_mancante"
                continue
            r = c._api({"c": 2, "r": reg, "ci": key, "t": "s", "lim": 1, "start": 0})
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
        for r in conn.execute(NOSTRI_SQL, {"prov": list(PROVINCE) + [x for v in PROVINCE_ALTRE.values() for x in v]}).fetchall():
            d = out.setdefault(f'{r["fonte"]}:{r["venditore"]}', {"tutti": 0, "regole": 0, "per_provincia": {}})
            d["tutti"] += r["tutti"]
            d["regole"] += r["regole"]
            d["per_provincia"][r["prov"]] = d["per_provincia"].get(r["prov"], 0) + r["regole"]
            t = d.setdefault("per_provincia_tutti", {})       # 26/11: anche senza i filtri anno/km (confronto col portale)
            t[r["prov"]] = t.get(r["prov"], 0) + r["tutti"]
    return out


# 26/11 (cliente: "troppe poche auto per le città, fai un controllo mirato"): per provincia e per alcune città, dove si
# fermano gli annunci letti prima del sito: fase (nuovo/mercato/approfondito/scartato), motivo dello scarto, auto
# approfondite e controllate nelle ultime 24 ore (solo queste vanno al sito), categoria della valutazione.
# Solo conteggi in lettura: nessun dato del venditore, nessuna regola cambia.
CITTA_DIAGNOSI = ("crema", "cremona", "novara", "pavia", "vigevano", "voghera", "lecco", "lodi", "piacenza", "milano")

IMBUTO_SQL = """
WITH a AS (
  SELECT l.id, upper(l.province) AS prov, lower(coalesce(l.city, '')) AS citta, coalesce(l.seller_type, '?') AS vend,
         l.stage, coalesce(l.stage_reason, '') AS motivo,
         (l.year >= 2007 AND l.mileage_km BETWEEN 0 AND 150000) AS regole,
         (l.last_checked_at > now() - interval '24 hours') AS controllata_24h
  FROM listings l
  WHERE l.status = 'attivo' AND l.last_seen_at > now() - interval '48 hours'
    AND (upper(l.province) = ANY(%(prov)s) OR lower(coalesce(l.city, '')) = ANY(%(citta)s))
)
SELECT prov, citta, vend, stage, motivo, regole, controllata_24h, count(*) AS n FROM a GROUP BY 1, 2, 3, 4, 5, 6, 7"""


def imbuto_province() -> dict:
    import psycopg
    from psycopg.rows import dict_row

    from ..config import settings
    prov = list(PROVINCE) + [x for v in PROVINCE_ALTRE.values() for x in v]
    per_prov: dict = {}
    per_citta: dict = {}

    def somma(d, r):
        d["letti"] = d.get("letti", 0) + r["n"]
        if r["regole"]:
            d["regole"] = d.get("regole", 0) + r["n"]
            if r["vend"] == "privato":
                d["regole_privati"] = d.get("regole_privati", 0) + r["n"]
            f = d.setdefault("fase_regole", {})
            f[r["stage"]] = f.get(r["stage"], 0) + r["n"]
            if r["stage"] == "approfondito" and r["controllata_24h"]:
                d["al_sito_24h"] = d.get("al_sito_24h", 0) + r["n"]
            if r["stage"] == "approfondito" and not r["controllata_24h"]:
                d["approfondite_non_ricontrollate"] = d.get("approfondite_non_ricontrollate", 0) + r["n"]
            if r["stage"] != "approfondito" and r["motivo"]:
                m = d.setdefault("_motivi", {})
                m[r["motivo"][:40]] = m.get(r["motivo"][:40], 0) + r["n"]

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET statement_timeout = '300s'")
        for r in conn.execute(IMBUTO_SQL, {"prov": prov, "citta": list(CITTA_DIAGNOSI)}).fetchall():
            if r["prov"] in prov:
                somma(per_prov.setdefault(r["prov"], {}), r)
            if r["citta"] in CITTA_DIAGNOSI:
                somma(per_citta.setdefault(r["citta"], {}), r)
    for d in list(per_prov.values()) + list(per_citta.values()):
        m = d.pop("_motivi", {})
        d["motivi_top"] = dict(sorted(m.items(), key=lambda kv: -kv[1])[:6])
    return {"province": per_prov, "citta": per_citta}


def lavoro(spec: str = "") -> dict:
    esito: dict = {"province": list(PROVINCE) + [x for v in PROVINCE_ALTRE.values() for x in v]}
    for nome, fn in (("autoscout24_sito", autoscout), ("subito_sito", subito), ("noi", nostri), ("imbuto", imbuto_province)):
        try:
            esito[nome] = fn()
        except Exception as e:                       # un pezzo che fallisce non ferma gli altri
            esito[nome] = f"errore {type(e).__name__}: {str(e)[:200]}"
    return esito
