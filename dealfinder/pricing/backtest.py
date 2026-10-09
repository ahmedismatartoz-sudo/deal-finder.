"""Misura settimanale della qualità delle stime.

    python -m dealfinder.pricing.backtest

1. Annunci sani spariti negli ultimi 60 giorni: si stimano con il mercato
   (escludendo l'annuncio stesso) e si confronta la stima con l'ultimo prezzo.
   - errore mediano %: precisione della stima di mercato
   - copertura prudente: quota di casi in cui il prudente "lordo" (senza
     sconto trattativa) è sotto l'ultimo prezzo richiesto. Obiettivo ~75%.
2. Vendite reali dei commercianti (dealer_feedback 'venduta'): suggeriscono
   lo sconto di trattativa effettivo.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from statistics import median

from ..db import connect, log_job
from ..store import LIGHT_COLS, MARKET_COLS, load_market, row_to_listing
from .engine import ENGINE_VERSION, PricingConfig, value_listing

log = logging.getLogger("backtest")


def evaluate(cases, market_loader, cfg: PricingConfig | None = None) -> dict:
    cfg = cfg or PricingConfig()
    errors, covered, by_seg = [], [], defaultdict(list)
    for l in cases:
        market = market_loader(l)
        v = value_listing(l, market, cfg)
        if v.private_median is None or v.n_comparables < cfg.min_comparables:
            continue
        ref = v.private_median if l.seller_type == "privato" else v.dealer_median
        err = (ref - l.price_eur) / l.price_eur
        errors.append(abs(err))
        if l.seller_type == "commerciante" and v.resale_prudent_dealer:
            covered.append(v.resale_prudent_dealer / (1 - cfg.negotiation_discount) <= l.price_eur)
        elif l.seller_type == "privato" and v.resale_prudent_private:
            covered.append(v.resale_prudent_private / (1 - cfg.private_negotiation_discount) <= l.price_eur)
        by_seg[f"{l.make} {l.model}"].append(abs(err))
    return {
        "n_cases": len(errors),
        "median_abs_pct_error": round(median(errors), 4) if errors else None,
        "prudent_coverage": round(sum(covered) / len(covered), 3) if covered else None,
        "by_segment": {k: {"n": len(v), "err": round(median(v), 3)} for k, v in by_seg.items() if len(v) >= 5},
    }


def suggested_discount(sales: list[tuple[int, int]]) -> float | None:
    """sales: (rivendita mediana stimata, prezzo di vendita reale). Ritorna lo sconto mediano."""
    ratios = [1 - sold / est for est, sold in sales if est and sold]
    return round(median(ratios), 3) if len(ratios) >= 10 else None


def run() -> dict:
    with connect() as conn:
        finish = log_job(conn, "backtest")
        rows = conn.execute(
            f"""SELECT {MARKET_COLS} FROM listings WHERE status='scomparso' AND disappeared_at > now() - interval '60 days'
               AND (province IN ('MI','MB','BG','BS','CO','VA','LC','LO','PV','CR','MN','SO') OR (province IS NULL AND (region IS NULL OR lower(region) = 'lombardia')))
               AND damage_class IN ('nessuno','sconosciuto') AND NOT COALESCE(damage_declared,false)
               AND price_eur IS NOT NULL AND make IS NOT NULL AND model IS NOT NULL
               ORDER BY random() LIMIT 2000""").fetchall()
        cases = [row_to_listing(r) for r in rows]
        for c in cases:
            c.damage_class = "nessuno"
        cache: dict = {}

        def loader(l):
            k = (l.make, l.model, l.fuel)
            if k not in cache:
                cache[k] = load_market(conn, *k)
            return cache[k]

        res = evaluate(cases, loader)
        sales = conn.execute(
            """SELECT v.resale_median AS est, f.sold_eur AS sold FROM dealer_feedback f
               JOIN LATERAL (SELECT resale_median FROM valuations WHERE listing_id=f.listing_id
                             ORDER BY created_at DESC LIMIT 1) v ON true
               WHERE f.status='venduta' AND f.sold_eur IS NOT NULL""").fetchall()
        res["suggested_negotiation_discount"] = suggested_discount([(s["est"], s["sold"]) for s in sales])
        conn.execute("""INSERT INTO model_runs (engine_version, n_cases, median_abs_pct_error, prudent_coverage,
                        suggested_negotiation_discount, by_segment) VALUES (%s,%s,%s,%s,%s,%s)""",
                     (ENGINE_VERSION, res["n_cases"], res["median_abs_pct_error"], res["prudent_coverage"],
                      res["suggested_negotiation_discount"], json.dumps(res["by_segment"])))
        conn.commit()
        finish(True, res)
    log.info("backtest: %s", res)
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
