"""Confronto tra regioni: comprare in Lombardia e rivendere dove la stessa auto vale di più.

Esempio tipico: diesel colpiti dai divieti di circolazione in Lombardia, che i proprietari
svendono, mentre in regioni senza limiti si vendono a prezzo pieno.

Per ogni auto si calcola la rivendita prudente in ogni regione raccolta (stessi confronti e
stesse regole del mercato di casa), si tolgono trasporto e costi di vendita a distanza e si
tiene la regione migliore, solo se i confronti sono solidi e il guadagno extra è reale.

I costi di trasporto sono stime indicative (bisarca, partenza Lombardia) e si possono
cambiare con la variabile TRASPORTO_REGIONI (JSON {"campania": 550, ...}).
"""
from __future__ import annotations

import json
import os
from statistics import median

from ..core.models import Listing
from ..store import load_market
from .engine import value_listing
from .model import predict

DEFAULT_REGIONS = "piemonte,veneto,emilia-romagna,toscana,lazio,campania,puglia,sicilia"
TRANSPORT = {"piemonte": 250, "liguria": 300, "veneto": 250, "emilia-romagna": 250, "trentino-alto-adige": 300,
             "friuli-venezia-giulia": 300, "toscana": 350, "umbria": 400, "marche": 400, "lazio": 450,
             "abruzzo": 500, "molise": 550, "campania": 550, "puglia": 600, "basilicata": 650,
             "calabria": 700, "sicilia": 750, "sardegna": 800}
REMOTE_SALE_COST = 150          # viaggio/consegna, tempo in più per vendere lontano
MIN_EXTRA = 400                 # sotto questo guadagno in più non vale la complicazione
MIN_COMPARABLES = 5
MAX_DISPERSION = 0.30


def regions() -> list[str]:
    return [r.strip() for r in os.environ.get("ARBITRAGGIO_REGIONI", DEFAULT_REGIONS).split(",") if r.strip()]


def transport(region: str) -> int:
    custom = json.loads(os.environ.get("TRASPORTO_REGIONI", "{}") or "{}")
    return int(custom.get(region, TRANSPORT.get(region, 600)))


def best_region(conn, l: Listing, home_resale: int | None, cache: dict) -> dict | None:
    """Regione dove rivendere conviene di più rispetto alla Lombardia (o None)."""
    if not (l.make and l.model and home_resale):
        return None
    best = None
    for reg in regions():
        key = (reg, l.make, l.model, l.fuel)
        if key not in cache:
            cache[key] = load_market(conn, l.make, l.model, l.fuel, region=reg)
        market = cache[key]
        if len(market) < MIN_COMPARABLES:
            continue
        saved = l.damage_class
        l.damage_class = "nessuno"
        v = value_listing(l, market)
        l.damage_class = saved
        if v.resale_prudent is None or v.n_comparables < MIN_COMPARABLES or (v.dispersion or 1) > MAX_DISPERSION:
            continue
        cost = transport(reg) + REMOTE_SALE_COST
        extra = v.resale_prudent - home_resale - cost
        if extra >= MIN_EXTRA and (best is None or extra > best["guadagno_extra"]):
            best = {"regione": reg, "rivendita_prudente": v.resale_prudent, "mediana_privati": v.private_median,
                    "confronti": v.n_comparables, "trasporto": transport(reg), "costi_vendita_distanza": REMOTE_SALE_COST,
                    "guadagno_extra": extra}
    return best


def price_index(conn, model: dict | None) -> dict:
    """Quanto costano le auto in ogni regione rispetto alla Lombardia, a parità di caratteristiche.

    Il modello è addestrato sul mercato lombardo: per ogni annuncio di un'altra regione si
    calcola prezzo / prezzo stimato in Lombardia. Sopra 1 = la regione paga di più.
    Diviso anche per diesel vecchi (fino al 2010, Euro 4 o precedenti), i più colpiti dai divieti."""
    if not model:
        return {"errore": "nessun modello"}
    from ..store import row_to_listing
    out = {}
    for reg in regions():
        rows = conn.execute(
            "SELECT * FROM listings WHERE lower(region)=lower(%s) AND last_seen_at > now() - interval '14 days' "
            "AND price_eur BETWEEN 500 AND 40000 AND make IS NOT NULL AND model IS NOT NULL "
            "AND year IS NOT NULL AND mileage_km IS NOT NULL AND NOT COALESCE(damage_declared,false)",
            (reg,)).fetchall()
        ratios: dict[str, list[float]] = {"tutte": [], "diesel_fino_2010": [], "diesel_2011_2015": [],
                                          "benzina": [], "privati": []}
        for r in rows:
            l = row_to_listing(r)
            p = predict(model, l)
            if not p or p["level"] not in ("mmf", "mm"):
                continue
            ratio = l.price_eur / p["p50"]
            if not 0.3 < ratio < 3:
                continue
            ratios["tutte"].append(ratio)
            if l.fuel == "diesel" and l.year <= 2010:
                ratios["diesel_fino_2010"].append(ratio)
            elif l.fuel == "diesel" and l.year <= 2015:
                ratios["diesel_2011_2015"].append(ratio)
            elif l.fuel == "benzina":
                ratios["benzina"].append(ratio)
            if l.seller_type == "privato":
                ratios["privati"].append(ratio)
        out[reg] = {k: {"n": len(v), "rapporto": round(median(v), 3) if len(v) >= 20 else None}
                    for k, v in ratios.items()}
    return out
