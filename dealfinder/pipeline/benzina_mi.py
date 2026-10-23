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
PMIN, PMAX, SOLO_P = 3000, 15000, False
PROVINCE_100 = [p.strip() for p in os.environ.get("BENZ_PROVINCE", "MI,MB,VA,CO,LC,BG,LO,PV,CR,NO,VB,PC,AL,VC,BS").split(",")]

CANDIDATI_SQL = """
WITH g AS (                     -- prezzo mediano per marca, modello, alimentazione e anno: una sola passata
  SELECT make, model, fuel, year, count(*) AS n, percentile_cont(.5) WITHIN GROUP (ORDER BY price_eur) AS med
  FROM listings
  WHERE price_eur BETWEEN 800 AND 60000 AND year >= 2005 AND make IS NOT NULL AND model IS NOT NULL
  GROUP BY make, model, fuel, year
)
SELECT t.id, t.source, t.url, t.price_eur, t.telefono_ok, g.n, g.med
FROM listings t
JOIN g ON g.make=t.make AND g.model=t.model AND g.fuel IS NOT DISTINCT FROM t.fuel AND g.year=t.year
WHERE t.status='attivo' AND upper(t.province) = ANY(%(prov)s)
  AND (%(tutti_carb)s OR t.fuel ILIKE 'benzina%%')   -- 23/10: con BENZ_TUTTI_CARB=1 tutte le alimentazioni (tetto diesel 30% sul sito)
  AND t.price_eur BETWEEN %(pmin)s AND %(pmax)s AND t.year >= 2007
  AND (%(solo_privati)s = false OR t.seller_type = 'privato') AND coalesce(t.mileage_km, 0) <= 180000
  AND coalesce(t.damage_class,'') NOT IN ('grave','alto_rischio','medio')
  AND t.stage IS DISTINCT FROM 'approfondito'
  AND g.n >= 5 AND t.price_eur < g.med * %(soglia)s
ORDER BY (t.price_eur > %(util)s) ASC, t.price_eur / g.med ASC   -- 22/10: utilitarie per prime (BENZ_UTIL_PRIMA)
LIMIT %(lim)s"""


def giro() -> dict:
    from ..db import connect
    from . import tempo_reale as tr
    from ..collectors.subito import SubitoCollector
    from ..collectors.autoscout import AutoScoutCollector
    st: Counter = Counter()
    global PMIN, PMAX, SOLO_P
    PMIN, PMAX = int(os.environ.get("BENZ_MIN", "3000")), int(os.environ.get("BENZ_PMAX", "15000"))
    SOLO_P = os.environ.get("BENZ_SOLO_PRIVATI") == "1"
    # 21/10 sera (cliente): si raccolgono TUTTI gli annunci (anche concessionari) per la base prezzi del modello;
    # sul sito vanno comunque solo i privati (le candidate restano filtrate con SOLO_P)
    racc_p = SOLO_P and os.environ.get("BENZ_RACCOLTA_TUTTI") != "1"
    # salvataggio a pacchetti: in memoria non resta mai più di un pacchetto di annunci (512 MB di memoria)
    with connect() as conn:
        pacco: list = []

        def salva(forza=False):
            if pacco and (forza or len(pacco) >= 150):
                tr._salva(conn, pacco, st)
                conn.commit()
                pacco.clear()

        solo_valuta = os.environ.get("BENZ_SOLO_VALUTA") == "1"   # annunci già raccolti: si passa subito alla scelta
        # 1a. Subito: Lombardia entro 100 km, dal più recente
        sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
        sub.min_delay_s, sub.max_delay_s = 0.4, 0.9
        n = 0
        try:
            for l in ([] if solo_valuta else sub.search_recenti("lombardia", PROVINCE_100, os.environ.get("BENZ_DAL", "2026-08-01 00:00"),
                                        max_pages=int(os.environ.get("BENZ_SUB_PAGINE", "300")))):
                if l.price_eur and PMIN <= l.price_eur <= PMAX and (not racc_p or l.seller_type == "privato"):
                    pacco.append(l); n += 1
                    salva()
        except Exception as e:
            log.warning("BENZ subito: %s", str(e)[:200])
        salva(True)
        # 1a-bis (21/10 sera, cliente: "tutti gli annunci del mercato, solo privati, a benzina"): raccolta profonda
        # di Subito provincia per provincia e fascia di prezzo per fascia (la ricerca per data si ferma prima),
        # in Lombardia, Piemonte ed Emilia entro il raggio
        if not solo_valuta and os.environ.get("BENZ_SUB_PROFONDA") == "1":
            passo = int(os.environ.get("BENZ_SUB_PASSO", "500"))
            fasce = [(a, min(a + passo, PMAX)) for a in range(PMIN, PMAX, passo)]
            regioni = [(4, ["MI", "MB", "VA", "CO", "LC", "BG", "LO", "PV", "CR", "BS", "SO", "MN"]),
                       (2, ["NO", "VB", "VC", "BI", "AL", "AT", "TO"]), (8, ["PC", "PR", "RE"])]
            for reg, provs in regioni:
                provs = [p for p in provs if p in PROVINCE_100]
                if not provs:
                    continue
                try:
                    for l in sub._deep({"price_bands": fasce, "band_pages": int(os.environ.get("BENZ_SUB_PAG_FASCIA", "30"))},
                                       set(provs), reg, 100):
                        if (l.price_eur and PMIN <= l.price_eur <= PMAX and (not racc_p or l.seller_type == "privato")
                                and "benzina" in (l.fuel or "").lower()):
                            pacco.append(l); n += 1
                            salva()
                except Exception as e:
                    log.warning("BENZ subito profonda regione %s: %s", reg, str(e)[:200])
                salva(True)
                log.warning("BENZ subito profonda regione %s: letti finora %d", reg, n)
        st["letti_subito"] = n
        log.warning("BENZ subito letti %d (%d-%d €)", n, PMIN, PMAX)
        # 1b. AutoScout24: entro 100 km da Milano, a fasce di 500 €, più fasce insieme
        from .lusso import bande_parallele
        os.environ["AUTOSCOUT_CAP"], os.environ["AUTOSCOUT_RAGGIO"] = "20121", os.environ.get("BENZ_RAGGIO", "100")
        os.environ.pop("PASSI_JOB", None)
        n = 0
        try:
            for l in ([] if solo_valuta else bande_parallele(PMIN, PMAX, int(os.environ.get("BENZ_PASSO", "500")), [("P", "privato")] if racc_p else [("P", "privato"), ("D", "commerciante")], workers=int(os.environ.get("BENZ_PARALLELI", "4")))):
                pacco.append(l); n += 1
                salva()
        except Exception as e:
            log.warning("BENZ autoscout: %s", str(e)[:200])
        salva(True)
        st["letti_autoscout"] = n
        log.warning("BENZ autoscout letti %d", n)
        # 2. candidate: benzina, provincia di Milano, molto sotto il prezzo delle auto uguali
        conn.execute("SET statement_timeout = '900s'")
        cand = conn.execute(CANDIDATI_SQL, {"prov": PROVINCE_100, "soglia": float(os.environ.get("BENZ_SOGLIA", "0.85")),
                                            "lim": int(os.environ.get("BENZ_MAX", "500")), "pmin": PMIN, "pmax": PMAX, "solo_privati": SOLO_P,
                                            "util": int(os.environ.get("BENZ_UTIL_PRIMA", "1000000")),
                                            "tutti_carb": os.environ.get("BENZ_TUTTI_CARB", "1") == "1"}).fetchall()
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
