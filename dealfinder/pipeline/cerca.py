"""Ricerca su richiesta (CERCA_JSON) tra tutti gli annunci attivi: le auto più sotto il prezzo di mercato.

CERCA_JSON = {"marca": "toyota", "modello": "yaris", "km_max": 80000, "zona": "milano", "quante": 15}
Scrive nei log, per le migliori:
  CERCA {json}                         dati, mercato, sconto, guadagno
  CERCAFOTO <id> <k>/<n> <base64>      foglio con 4 foto per controllare
"""
from __future__ import annotations

import base64
import json
import logging
import os

from ..db import connect
from ..pricing.engine import value_listing
from ..pricing.fallback import apply_model, weak
from ..pricing.train import load_active
from ..store import LIGHT_COLS, load_market, row_to_listing
from ..web import foto, scovo
from ..zone import ZONE
from .mille import PEZZO, foglio
from .verify import verify_rows

log = logging.getLogger("cerca")


def sito(area: str = "milano") -> list[dict]:
    """Le auto che sono adesso sul sito (stesse regole dell'elenco Affari), una riga SITO per auto."""
    with connect() as conn:
        rows = conn.execute(scovo.LIST_SQL + " LIMIT 4000", {"me": 0}).fetchall()
    items = [it for it in (scovo.item(r) for r in rows) if scovo.abbastanza(it)]
    items = [it for it in items if it.get("area") == area and not it.get("incidentata")]
    for it in items:
        g = scovo.guadagno_minimo(it)
        log.info("SITO %s", json.dumps({"id": it["id"], "nome": it["nome"], "anno": it["anno"], "km": it["km"],
                                        "prezzo": it["prezzo"], "mercato": it["mercato"], "rip": it["rip_hi"],
                                        "guadagno": g, "zona": it["zona"], "fonte": it["fonte"],
                                        "verificare": it["verificare"], "foto": it["n_foto"]}, ensure_ascii=False))
    log.info("SITO_FINE %d", len(items))
    return items


def audit(min_guadagno: int = 2500, rapporto: float = 1.6) -> int:
    """Controllo delle auto sul sito con guadagno alto o mercato molto sopra il prezzo: per ognuna
    AUDIT {json} (dati, descrizione, mercato grezzo e realistico) e AUDITFOTO (foglio 4 foto)."""
    with connect() as conn:
        rows = conn.execute(scovo.LIST_SQL + " LIMIT 5000", {"me": 0}).fetchall()
        n = 0
        for r in rows:
            it = scovo.item(r)
            if not it or not scovo.abbastanza(it):
                continue
            g = scovo.guadagno_minimo(it)
            if g < min_guadagno and it["mercato_grezzo"] < it["prezzo"] * rapporto:
                continue
            d = conn.execute("SELECT url, description, version_raw FROM listings WHERE id=%s", (r["id"],)).fetchone()
            rec = {"id": it["id"], "nome": it["nome"], "versione": d["version_raw"], "anno": it["anno"], "km": it["km"],
                   "carburante": it["carb"], "cambio": it["cambio"], "zona": it["zona"], "fonte": it["fonte"],
                   "url": d["url"], "descrizione": (d["description"] or "")[:800], "prezzo": it["prezzo"],
                   "mercato_grezzo": it["mercato_grezzo"], "mercato": it["mercato"], "confronti": it["confronti"],
                   "ricambi_attuali": [it["rip_lo"], it["rip_hi"]], "guadagno_sito": g,
                   "incidentata": it["incidentata"], "da_verificare": it["verificare"]}
            log.info("AUDIT %s", json.dumps(rec, ensure_ascii=False, default=str))
            imgs = [c for c in (foto.fetch_one(conn, it["id"], p) for p in range(min(it["n_foto"], 4))) if c]
            sheet = foglio(imgs, f"{it['id']} {it['nome'][:40]} {it['anno']} {it['prezzo']}e")
            if sheet:
                b64 = base64.b64encode(sheet).decode()
                parts = [b64[i:i + PEZZO] for i in range(0, len(b64), PEZZO)]
                for k, p in enumerate(parts, 1):
                    log.info("AUDITFOTO %s %d/%d %s", it["id"], k, len(parts), p)
            n += 1
    log.info("AUDIT_FINE %d", n)
    return n


def run(q: dict | None = None) -> list[dict]:
    q = q or json.loads(os.environ.get("CERCA_JSON") or "{}")
    if q.get("audit"):
        return audit(int(q.get("min_guadagno") or 2500), float(q.get("rapporto") or 1.6))
    if q.get("sito"):
        return sito(q.get("zona") or "milano")
    marca, modello = (q.get("marca") or "").lower(), (q.get("modello") or "").lower()
    prov = list(ZONE.get(q.get("zona") or "milano", ZONE["milano"])["province"])
    out = []
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT {LIGHT_COLS}, stage_reason FROM listings
                WHERE status='attivo' AND make=%s AND model=%s AND province = ANY(%s)
                  AND COALESCE(seller_type,'privato') <> 'commerciante' AND price_eur >= 1000
                  AND mileage_km IS NOT NULL AND mileage_km <= %s
                  AND (year IS NULL OR year >= %s)
                  AND last_seen_at > now() - interval '10 days'""",
            (marca, modello, prov, int(q.get("km_max") or 10**7), int(q.get("anno_min") or 2007))).fetchall()
        log.info("CERCA_TROVATI %d annunci %s %s", len(rows), marca, modello)
        model = load_active(conn)
        markets = {}
        for r in rows:
            l = row_to_listing(r)
            key = (l.make, l.model, l.fuel)
            if key not in markets:
                markets[key] = load_market(conn, *key)
            saved = l.damage_class
            l.damage_class = "nessuno"
            v = value_listing(l, markets[key])
            l.damage_class = saved
            if weak(v) and not apply_model(v, l, model):
                continue
            mercato = v.private_median or v.resale_median_private or v.resale_median
            if not mercato:
                continue
            guad = round(mercato * (1 - scovo.SCONTO_DEFAULT / 100)) - l.price_eur - sum(scovo.COSTI_FISSI.values())
            out.append({"id": r["id"], "fonte": r["source"], "url": r["url"], "titolo": l.title,
                        "descrizione": (l.description or "")[:700], "versione": l.version_raw, "anno": l.year,
                        "km": l.mileage_km, "carburante": l.fuel, "cambio": l.gearbox, "citta": l.city,
                        "provincia": l.province, "prezzo": l.price_eur, "mercato": mercato,
                        "sconto_pct": round(100 * (1 - l.price_eur / mercato), 1), "guadagno_prima_ricambi": guad,
                        "confronti": v.n_comparables, "danno": r.get("damage_class"), "esito": r.get("stage_reason"),
                        "segnali": list(v.fraud_flags or [])})
        out.sort(key=lambda x: -x["guadagno_prima_ricambi"])
        top = out[: int(q.get("quante") or 15) * 2]
        full = {r["id"]: r for r in rows}
        verify_rows(conn, [full[x["id"]] for x in top if x["fonte"] == "subito"])
        alive = {x["id"] for x in conn.execute("SELECT id FROM listings WHERE id = ANY(%s) AND status='attivo'",
                                                ([x["id"] for x in top],)).fetchall()}
        top = [x for x in top if x["id"] in alive][: int(q.get("quante") or 15)]
        for x in top:
            log.info("CERCA %s", json.dumps(x, ensure_ascii=False, default=str))
            n = conn.execute("SELECT count(*) AS n FROM listing_photos WHERE listing_id=%s", (x["id"],)).fetchone()["n"]
            imgs = [c for c in (foto.fetch_one(conn, x["id"], p) for p in range(min(n, 4))) if c]
            sheet = foglio(imgs, f"{x['id']} {marca} {modello} {x['anno']} {x['prezzo']}e")
            if sheet:
                b = base64.b64encode(sheet).decode()
                parts = [b[i:i + PEZZO] for i in range(0, len(b), PEZZO)]
                for k, p in enumerate(parts, 1):
                    log.info("CERCAFOTO %s %d/%d %s", x["id"], k, len(parts), p)
    log.info("CERCA_FINE %d", len(top))
    return top
