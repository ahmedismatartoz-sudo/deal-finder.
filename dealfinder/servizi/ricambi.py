"""Servizio "Ricambi": il commerciante mette la targa e i pezzi che gli servono;
Scovo cerca sul web le offerte più economiche per quel veicolo esatto.

- targa -> veicolo esatto (ai/plate.py)
- per ogni pezzo: ricerca web con l'agente ricambi (negozi online, eBay, usato),
  solo offerte trovate davvero e con il link
- offerte ordinate per prezzo totale (pezzo + spedizione), con il risparmio
  rispetto all'offerta più cara trovata
Risultati in cache per 14 giorni per veicolo e pezzo (meno costi, risposte immediate).
"""
from __future__ import annotations

import json
import logging
import re

from ..ai import client as ai
from ..ai import plate as plates
from ..ai.parts_agent import PART_TYPES, SYSTEM

log = logging.getLogger("servizi.ricambi")
MAX_PEZZI = 8
TYPE_LABEL = {"originale": "Originale", "aftermarket": "Compatibile", "usato": "Usato"}


def vehicle_name(v: dict) -> str:
    bits = [v.get("make"), v.get("model"), v.get("version"),
            f"{v['engine_cc']} cc" if v.get("engine_cc") else None,
            f"{v['power_kw']} kW" if v.get("power_kw") else None, v.get("fuel"), str(v.get("year") or "")]
    return re.sub(r"\s+", " ", " ".join(str(b) for b in bits if b)).strip()


def clean_offers(data: dict, cited: set[str]) -> list[dict]:
    out = []
    for o in (data or {}).get("offers") or []:
        try:
            price = float(o.get("price_eur"))
            ship = float(o.get("shipping_eur") or 0)
        except (TypeError, ValueError):
            continue
        url = str(o.get("url") or "")
        if not (1 <= price <= 20_000) or o.get("type") not in PART_TYPES or not url.startswith("http"):
            continue
        out.append({"tipo": TYPE_LABEL[o["type"]], "prezzo": round(price, 2), "spedizione": round(ship, 2),
                    "totale": round(price + ship, 2), "venditore": str(o.get("seller", ""))[:80], "link": url,
                    "compatibile_sicuro": bool(o.get("fits_exact_vehicle")), "fonte_verificata": url in cited})
    out.sort(key=lambda o: (not o["compatibile_sicuro"], o["totale"]))
    return out


def search(vehicle: dict, pezzo: str) -> dict:
    query = (f"Veicolo: {vehicle_name(vehicle)}. Pezzo richiesto dal commerciante: {pezzo}. "
             "Trova le offerte più economiche (originale, compatibile/aftermarket e usato) per questo veicolo esatto.")
    r = ai.ask_json("ricambi_servizio", ai.MODEL_DEEP, SYSTEM, [{"type": "text", "text": query}], max_tokens=2000,
                    web_search={"max_uses": 5, "user_location": {"type": "approximate", "country": "IT", "city": "Milano"}})
    data = r.data if isinstance(r.data, dict) else {}
    return {"codici_oem": data.get("oem_codes") or [], "offerte": clean_offers(data, {s["url"] for s in r.sources})}


def summarize(pezzo: str, res: dict) -> dict:
    offers = sorted(res.get("offerte") or [], key=lambda o: o["totale"])
    best = offers[0] if offers else None
    return {"pezzo": pezzo, "codici_oem": res.get("codici_oem") or [],
            "migliore": best, "risparmio": round(offers[-1]["totale"] - best["totale"], 2) if len(offers) > 1 else 0,
            "offerte": res.get("offerte", [])[:8], "trovato": bool(offers)}


def run(conn, targa: str, pezzi: list[str], lookup=plates.lookup, searcher=search) -> dict:
    if not plates.valid_plate(targa):
        return {"ok": False, "errore": "Targa non valida: scrivila come AB123CD"}
    pezzi = [p.strip()[:80] for p in (pezzi or []) if p and p.strip()][:MAX_PEZZI]
    if not pezzi:
        return {"ok": False, "errore": "Scrivi almeno un pezzo (es. faro anteriore sinistro)"}
    vehicle = plates.cached_lookup(conn, targa, lookup)
    if not vehicle:
        return {"ok": False, "errore": "Non troviamo questa targa. Controlla di averla scritta giusta."}
    if searcher is search and not ai.available():
        return {"ok": False, "errore": "Ricerca ricambi non attiva: manca la chiave del servizio AI"}
    vkey = "servizio|" + vehicle_name(vehicle).lower()
    out = []
    for pezzo in pezzi:
        key = "libero:" + pezzo.lower()
        res = None
        if conn is not None:
            row = conn.execute("SELECT result FROM parts_cache WHERE vehicle_key=%s AND part=%s "
                               "AND searched_at > now() - interval '14 days'", (vkey, key)).fetchone()
            res = row["result"] if row else None
            if isinstance(res, str):
                res = json.loads(res)
        if res is None:
            try:
                res = searcher(vehicle, pezzo)
            except Exception as e:
                log.warning("ricerca ricambio fallita %s: %s", pezzo, str(e)[:200])
                res = {"offerte": [], "errore": "ricerca non riuscita"}
            else:
                if conn is not None:
                    conn.execute("INSERT INTO parts_cache (vehicle_key, part, result) VALUES (%s,%s,%s) "
                                 "ON CONFLICT (vehicle_key, part) DO UPDATE SET result=EXCLUDED.result, searched_at=now()",
                                 (vkey, key, json.dumps(res)))
                    conn.commit()
        out.append(summarize(pezzo, res))
    totale = round(sum(p["migliore"]["totale"] for p in out if p["migliore"]), 2)
    risparmio = round(sum(p["risparmio"] for p in out), 2)
    return {"ok": True, "auto": vehicle_name(vehicle), "pezzi": out, "totale_migliori": totale,
            "risparmio_totale": risparmio}
