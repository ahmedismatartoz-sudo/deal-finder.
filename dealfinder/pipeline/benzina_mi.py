"""20/10 (cliente): più auto a BENZINA da 3.000 a 15.000 € in provincia di MILANO sul sito, con buon margine.

Giro una tantum (BENZ_GIRO=n):
1. raccoglie tutto il nuovo di Subito (provincia di Milano) e di AutoScout24 (entro 30 km da Milano, privati e
   commercianti) da 3.000 a 15.000 €;
2. sceglie le auto a benzina della provincia di Milano molto sotto il prezzo delle auto uguali;
3. per quelle AutoScout24 controlla se l'annuncio mostra il numero del venditore;
4. le valuta con la policy prudente di sempre (nessuna regola allentata): quelle buone vanno sul sito.
Nei log: BENZ {...} e BENZ_FINE.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

log = logging.getLogger("benzina_mi")
# entro circa 100 km da Milano (cliente 20/10)
PROVINCE_100 = [p.strip() for p in os.environ.get("BENZ_PROVINCE", "MI,MB,VA,CO,LC,BG,LO,PV,CR,NO,VB,PC,AL,VC,BS").split(",")]

CANDIDATI_SQL = """
SELECT t.id, t.source, t.url, t.price_eur, t.telefono_ok, c.n, c.med
FROM listings t
JOIN LATERAL (
  SELECT count(*) AS n, percentile_cont(.5) WITHIN GROUP (ORDER BY o.price_eur) AS med
  FROM listings o
  WHERE o.id<>t.id AND o.make=t.make AND o.model=t.model AND o.fuel IS NOT DISTINCT FROM t.fuel
    AND o.year BETWEEN t.year-1 AND t.year+1 AND o.price_eur BETWEEN 800 AND 60000
    AND (t.mileage_km IS NULL OR o.mileage_km BETWEEN t.mileage_km*0.6-5000 AND t.mileage_km*1.4+10000)
) c ON true
WHERE t.status='attivo' AND upper(t.province) = ANY(%(prov)s) AND t.fuel ILIKE 'benzina%%'
  AND t.price_eur BETWEEN 3000 AND 15000 AND t.year >= 2007 AND coalesce(t.mileage_km, 0) <= 180000
  AND coalesce(t.damage_class,'') NOT IN ('grave','alto_rischio','medio')
  AND t.stage IS DISTINCT FROM 'approfondito'
  AND c.n >= 5 AND t.price_eur < c.med * %(soglia)s
ORDER BY t.price_eur / c.med ASC
LIMIT %(lim)s"""


def giro() -> dict:
    from ..db import connect
    from . import tempo_reale as tr
    from ..collectors.subito import SubitoCollector
    from ..collectors.autoscout import AutoScoutCollector
    st: Counter = Counter()
    letti = []
    # 1a. Subito: tutta la provincia di Milano, dal più recente
    sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    sub.min_delay_s, sub.max_delay_s = 0.4, 0.9
    try:
        for l in sub.search_recenti("lombardia", PROVINCE_100, os.environ.get("BENZ_DAL", "2026-08-01 00:00"),
                                    max_pages=int(os.environ.get("BENZ_SUB_PAGINE", "300"))):
            if l.price_eur and 3000 <= l.price_eur <= 15000:
                letti.append(l)
    except Exception as e:
        log.warning("BENZ subito: %s", str(e)[:200])
    st["letti_subito"] = len(letti)
    log.warning("BENZ subito letti %d (3.000-15.000 €, entro 100 km da Milano)", len(letti))
    # 1b. AutoScout24: entro 30 km da Milano, a fasce di 500 €, molte fasce insieme
    from .lusso import bande_parallele
    os.environ["AUTOSCOUT_CAP"], os.environ["AUTOSCOUT_RAGGIO"] = "20121", os.environ.get("BENZ_RAGGIO", "100")
    os.environ.pop("PASSI_JOB", None)
    n0 = len(letti)
    try:
        for l in bande_parallele(3000, 15000, 500, [("P", "privato"), ("D", "commerciante")]):
            letti.append(l)
    except Exception as e:
        log.warning("BENZ autoscout: %s", str(e)[:200])
    st["letti_autoscout"] = len(letti) - n0
    log.warning("BENZ autoscout letti %d", len(letti) - n0)
    with connect() as conn:
        tr._salva(conn, letti, st)
        conn.commit()
        del letti
        # 2. candidate: benzina, provincia di Milano, molto sotto il prezzo delle auto uguali
        conn.execute("SET statement_timeout = '300s'")
        cand = conn.execute(CANDIDATI_SQL, {"prov": PROVINCE_100, "soglia": float(os.environ.get("BENZ_SOGLIA", "0.85")),
                                            "lim": int(os.environ.get("BENZ_MAX", "500"))}).fetchall()
        conn.execute("SET statement_timeout = 0")
        st["candidate"] = len(cand)
        log.warning("BENZ candidate %d", len(cand))
        # 3. numero del venditore sugli annunci AutoScout24 non ancora controllati
        from concurrent.futures import ThreadPoolExecutor
        da_fare = [r for r in cand if r["source"] == "autoscout24" and r["telefono_ok"] is None]

        def numero(r):
            cl = tr._client_as()
            stato, det = tr._as_dettaglio(cl, r["url"])
            return r["id"], stato, (tr.telefono_autoscout(det) if det is not None else None)

        with ThreadPoolExecutor(6) as ex:
            for lid, stato, tel in ex.map(numero, da_fare):
                if stato == "scomparso":
                    st["autoscout_sparite"] += 1
                elif tel is not None or stato == "attivo":
                    conn.execute("UPDATE listings SET telefono_ok=%s, telefono_at=now() WHERE id=%s", (tel, lid))
                    st[f"autoscout_telefono_{tel}"] += 1
        conn.commit()
        # 4. valutazione prudente e pubblicazione
        ids = [r["id"] for r in cand]
        tr.valuta_e_pubblica(conn, ids, st)
        conn.commit()
    log.warning("BENZ_FINE %s", json.dumps(dict(st)))
    return dict(st)
