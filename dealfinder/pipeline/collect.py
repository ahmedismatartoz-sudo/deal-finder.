"""Raccolta: esegue i collettori, pulisce, salva, registra lo storico.

Uso:
    python -m dealfinder.pipeline.collect opportunita   # zona Milano, ≤ 20.000 €
    python -m dealfinder.pipeline.collect mercato       # base di mercato, zona ampia
    python -m dealfinder.pipeline.collect facebook      # Facebook Marketplace via Bright Data
"""
from __future__ import annotations

import logging
import sys
from collections import Counter

from ..collectors.brightdata import BrightDataFacebookCollector
from ..collectors.subito import SubitoCollector
from ..config import settings
from ..core.normalize import normalize_fields
from ..db import connect, log_job, mark_disappeared, upsert_listing

log = logging.getLogger("collect")


def run(mode: str) -> Counter:
    stats: Counter = Counter()
    if mode == "opportunita":
        provinces, max_price = settings.opportunity_provinces, settings.max_purchase_eur
    else:
        provinces, max_price = settings.market_provinces, settings.market_max_price_eur

    # Subito ogni 3 ore (opportunità) e ogni notte (mercato). Facebook ha un lavoro a parte,
    # meno frequente, perché ogni annuncio scaricato da Bright Data ha un costo.
    if mode == "facebook":
        sources = [(BrightDataFacebookCollector(), {"max_price": settings.max_purchase_eur})]
    else:
        subito = SubitoCollector(proxy=settings.scraper_proxy)
        sources = [(subito, {"region": settings.region, "provinces": provinces,
                             "max_price": max_price, "max_pages": settings.max_pages_per_province})]

    with connect() as conn:
        finish = log_job(conn, f"collect:{mode}")
        for collector, query in sources:
            try:
                for listing in collector.search(query):
                    normalize_fields(listing)
                    lid, event = upsert_listing(conn, listing)
                    if event in ("prezzo", "riapparso"):
                        # prezzo cambiato: l'annuncio va rivalutato
                        conn.execute("UPDATE listings SET stage='nuovo' WHERE id=%s AND stage<>'nuovo'", (lid,))
                    stats[f"{collector.source}:{event}"] += 1
                    conn.commit()
            except Exception:
                log.exception("collettore %s fallito", collector.source)
                stats[f"{collector.source}:errore"] += 1
        # Si segnano gli scomparsi solo se la raccolta è andata a buon fine,
        # altrimenti un blocco del sito farebbe "sparire" tutto.
        seen = sum(v for k, v in stats.items() if k.startswith("subito:") and k != "subito:errore")
        if mode == "mercato" and not stats["subito:errore"] and seen > 200:
            stats["subito:scomparsi"] = mark_disappeared(conn, "subito", not_seen_hours=36)
        conn.commit()
        # Riepilogo della qualità dei dati raccolti nell'ultima ora (visibile nei log)
        for q in conn.execute(
                """SELECT source, count(*) AS n,
                          round(100.0*avg((make IS NOT NULL)::int)) AS marca,
                          round(100.0*avg((model IS NOT NULL)::int)) AS modello,
                          round(100.0*avg((year IS NOT NULL)::int)) AS anno,
                          round(100.0*avg((mileage_km IS NOT NULL)::int)) AS km,
                          round(100.0*avg((price_eur IS NOT NULL)::int)) AS prezzo,
                          round(100.0*avg((province IS NOT NULL OR city IS NOT NULL)::int)) AS zona,
                          round(100.0*avg((seller_type='privato')::int)) AS privati,
                          round(100.0*avg((EXISTS (SELECT 1 FROM listing_photos p WHERE p.listing_id=listings.id))::int)) AS foto
                   FROM listings WHERE last_seen_at > now() - interval '1 hour' GROUP BY source""").fetchall():
            log.info("QUALITA %s: %s annunci | %% presenti: marca %s, modello %s, anno %s, km %s, prezzo %s, "
                     "zona %s, foto %s | privati %s%%", q["source"], q["n"], q["marca"], q["modello"], q["anno"],
                     q["km"], q["prezzo"], q["zona"], q["foto"], q["privati"])
        finish(not any(k.endswith(":errore") for k in stats), dict(stats))
    log.info("raccolta %s: %s", mode, dict(stats))
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mode = sys.argv[1] if len(sys.argv) > 1 else "opportunita"
    import os
    if os.environ.get("SUBITO_PROBE") == "1":
        # Diagnostica di accesso: nessuna raccolta finché la variabile è attiva
        from ..collectors.probe import run as probe_run
        probe_run()
        sys.exit(0)
    run(mode)
    if mode in ("opportunita", "facebook") and "--senza-analisi" not in sys.argv:
        from .process import run as process_run
        process_run("tutto")
