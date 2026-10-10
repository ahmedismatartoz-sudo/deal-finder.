"""Agente ricambi: cerca sul web i prezzi dei pezzi per il veicolo esatto e
calcola SOLO il costo dei ricambi (niente manodopera, niente verniciatura).

Per ogni pezzo: ricerca web (negozi ricambi italiani/europei, usato),
raccolta di offerte con prezzo e link, poi il calcolo lo fa il codice:
- costo basso  = offerta più economica del tipo preferito dal commerciante
- costo alto   = 75° percentile delle offerte del tipo preferito
I pezzi "probabili nascosti" (traverse, sensori, calibrazione ADAS) entrano
solo nel costo alto. Con meno di 2 offerte il pezzo è "da verificare".
"""
from __future__ import annotations

import json
import os
import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from . import client as ai
from .damage import FAULTS, MECH_PART_LABELS, PARTS, hidden_parts, part_key

log = logging.getLogger(__name__)

CACHE_DAYS = 14
PART_TYPES = ("originale", "aftermarket", "usato")

HIDDEN_LABELS = {
    "traversa_anteriore": "Traversa paraurti anteriore (rinforzo)",
    "traversa_posteriore": "Traversa paraurti posteriore (rinforzo)",
    "staffe_paraurti_anteriore": "Staffe/supporti paraurti anteriore",
    "staffe_paraurti_posteriore": "Staffe/supporti paraurti posteriore",
    "sensori_parcheggio_anteriori": "Sensori di parcheggio anteriori",
    "sensori_parcheggio_posteriori": "Sensori di parcheggio posteriori",
    "staffa_faro": "Staffa di fissaggio faro",
    "passaruota_anteriore": "Passaruota anteriore",
    "calibrazione_radar_adas": "Calibrazione radar ADAS (servizio)",
    "calibrazione_telecamera_adas": "Calibrazione telecamera ADAS (servizio)",
}

SYSTEM = """Sei un ricambista esperto. Devi trovare prezzi REALI e ATTUALI di un ricambio per un veicolo
preciso, cercando sul web (negozi di ricambi online in Italia o UE, eBay, autodemolizioni).
Rispondi SOLO JSON:
{"oem_codes": [str], "offers": [{"type": "originale|aftermarket|usato", "price_eur": number,
  "shipping_eur": number|null, "seller": str, "url": str, "fits_exact_vehicle": bool, "note": str}],
 "notes": str}
Regole: riporta solo offerte trovate davvero, con il link. Prezzi IVA inclusa in euro.
Non inventare prezzi. Se non trovi nulla, offers = []. Massimo 8 offerte."""


def _side_label(side: str | None) -> str:
    return {"sx": " lato sinistro (guida)", "dx": " lato destro (passeggero)"}.get(side or "", "")


def search_part(vehicle: dict, part: str, side: str | None, usage_sink=None, listing_id=None) -> dict:
    label = PARTS.get(part) or MECH_PART_LABELS.get(part) or HIDDEN_LABELS.get(part, part)
    query = (f"Veicolo: {vehicle.get('search_name')}. Pezzo: {label}{_side_label(side)}. "
             f"Dettagli veicolo: {json.dumps({k: vehicle.get(k) for k in ('generation', 'facelift', 'body_type', 'engine', 'year_from', 'year_to')}, ensure_ascii=False)}. "
             "Trova prezzi per ricambio originale, aftermarket e usato.")
    # modello economico con poche ricerche: i prezzi li legge dalle pagine trovate, i conti li fa il codice
    r = ai.ask_json("ricambi", os.environ.get("AI_MODEL_RICAMBI", ai.MODEL_FAST), SYSTEM, [{"type": "text", "text": query}],
                    max_tokens=2000, usage_sink=usage_sink, listing_id=listing_id,
                    web_search={"max_uses": int(os.environ.get("AI_RICERCHE_PER_PEZZO", "2")),
                                "user_location": {"type": "approximate", "country": "IT", "city": "Milano"}})
    data = r.data if isinstance(r.data, dict) else {}
    cited = {s["url"] for s in r.sources}
    offers = []
    for o in data.get("offers") or []:
        try:
            price = float(o.get("price_eur"))
        except (TypeError, ValueError):
            continue
        if not (1 <= price <= 20_000) or o.get("type") not in PART_TYPES or not o.get("url"):
            continue
        offers.append({"type": o["type"], "price_eur": round(price + float(o.get("shipping_eur") or 0), 2),
                       "seller": str(o.get("seller", ""))[:80], "url": o["url"],
                       "fits_exact_vehicle": bool(o.get("fits_exact_vehicle")),
                       "cited": o["url"] in cited})
    return {"part": part, "side": side, "label": label + _side_label(side),
            "oem_codes": data.get("oem_codes") or [], "offers": offers,
            "searched_at": datetime.now(timezone.utc).isoformat()}


def price_range(result: dict, preferred: str = "aftermarket") -> dict:
    """Calcolo deterministico del costo di un pezzo dalle offerte trovate."""
    offers = [o for o in result["offers"] if o["fits_exact_vehicle"]] or result["offers"]
    by_type = {t: sorted(o["price_eur"] for o in offers if o["type"] == t) for t in PART_TYPES}
    order = [preferred] + [t for t in ("aftermarket", "originale", "usato") if t != preferred]
    chosen = next((t for t in order if len(by_type[t]) >= 2), None) \
        or next((t for t in order if by_type[t]), None)
    if not chosen:
        return {"type": None, "low": None, "high": None, "n_offers": 0, "status": "non_trovato"}
    prices = by_type[chosen]
    return {"type": chosen, "low": round(prices[0]), "high": round(float(np.quantile(prices, 0.75))),
            "n_offers": len(prices), "status": "ok" if len(prices) >= 2 else "una_sola_offerta"}


def vehicle_key(vehicle: dict) -> str:
    return "|".join(str(vehicle.get(k) or "") for k in ("make", "model", "generation", "engine", "body_type")).lower()


def estimate_parts(vehicle: dict, damage_items: list[dict], year: int | None, preferred: str = "aftermarket",
                   cache_get=None, cache_put=None, usage_sink=None, listing_id=None) -> dict:
    """Stima il costo totale dei ricambi. cache_get/put: funzioni (vkey, part) per la cache."""
    vkey = vehicle_key(vehicle)
    lines, missing = [], []
    body = [it for it in damage_items if it["part"] not in FAULTS]
    faults = [it for it in damage_items if it["part"] in FAULTS]
    wanted = [(it["part"], it.get("side"), False) for it in body if it.get("action") == "sostituire"]
    # Guasti: ogni guasto diventa i suoi ricambi. Il volano bimassa non c'è su tutte le auto:
    # entra solo nel costo alto.
    for it in faults:
        for mp in FAULTS[it["part"]][1]:
            wanted.append((mp, None, mp == "volano_bimassa"))
    wanted += [(p, None, True) for p in hidden_parts(body, year)]

    for part, side, probable in wanted:
        key = part_key(part, side)
        res = cache_get(vkey, key) if cache_get else None
        if res is None:
            try:
                res = search_part(vehicle, part, side, usage_sink, listing_id)
            except Exception as e:
                log.warning("ricerca ricambio fallita %s: %s", key, e)
                res = {"part": part, "side": side, "label": PARTS.get(part, part), "offers": [], "error": str(e)}
            else:
                if cache_put:
                    cache_put(vkey, key, res)
        rng = price_range(res, preferred)
        label = (PARTS.get(part) or MECH_PART_LABELS.get(part) or HIDDEN_LABELS.get(part)
                 or res.get("label") or part) + _side_label(side)
        line = {"part": key, "label": label, "probable_hidden": probable, **rng,
                "oem_codes": res.get("oem_codes", []),
                "offers": sorted(res.get("offers", []), key=lambda o: o["price_eur"])[:5]}
        lines.append(line)
        if rng["status"] != "ok" and not probable:
            missing.append(key)

    visible = [l for l in lines if not l["probable_hidden"] and l["low"] is not None]
    hidden = [l for l in lines if l["probable_hidden"] and l["high"] is not None]
    low = sum(l["low"] for l in visible)
    high = sum(l["high"] for l in visible) + sum(l["high"] for l in hidden)
    # Riparazioni senza sostituzione: nessun ricambio, lo segnaliamo
    repair_only = [part_key(it["part"], it.get("side")) for it in body if it.get("action") == "riparare"]
    return {"vehicle": vehicle.get("search_name"), "preferred_type": preferred,
            "parts_cost_low": low if lines else 0, "parts_cost_high": high if lines else 0,
            "lines": lines, "parts_not_priced": missing, "repair_only_no_parts": repair_only,
            "complete": not missing}


def db_cache(conn):
    def get(vkey, part):
        row = conn.execute("SELECT result, searched_at FROM parts_cache WHERE vehicle_key=%s AND part=%s",
                           (vkey, part)).fetchone()
        if row and row["searched_at"] > datetime.now(timezone.utc) - timedelta(days=CACHE_DAYS):
            return row["result"]
        return None

    def put(vkey, part, result):
        conn.execute("INSERT INTO parts_cache (vehicle_key, part, result) VALUES (%s,%s,%s) "
                     "ON CONFLICT (vehicle_key, part) DO UPDATE SET result=EXCLUDED.result, searched_at=now()",
                     (vkey, part, json.dumps(result)))
    return get, put
