"""Importa l'analisi fatta a parte (file data/analisi_manuale.json nel repository).

Formato: lista di oggetti
  {"id": 123, "esito": "opportunita|da_verificare|scartata", "danno": "nessuno|leggero|medio|alto_rischio|grave",
   "danni": [{"part": "paraurti_anteriore", "side": null, "action": "sostituire", "severity": "medio"}],
   "ricambi_min": 250, "ricambi_max": 420, "ricambi": [{"label": "...", "low": 120, "high": 180, "offer": "url"}],
   "nota": "testo per la scheda", "controlli": ["..."], "rivendita_prudente": 3000 (facoltativo, solo al ribasso)}

L'esito viene ricalcolato con i numeri (confronti e margine): l'analisi manuale fornisce stato
e ricambi, non il margine.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from ..ai.damage import sanitize_items
from ..core.models import DamageItem
from ..db import connect, log_job
from ..pricing import report
from ..pricing.engine import value_listing
from ..pricing.fallback import apply_model, estimate_missing_km, weak
from ..pricing.train import load_active
from ..pricing.margin import DealerCosts, compute_margin
from ..store import load_market, row_to_listing, save_valuation, set_stage, update_listing_fields

log = logging.getLogger("manuale")
FILE = Path(__file__).resolve().parents[2] / "data" / "analisi_manuale.json"


def run() -> dict:
    if not FILE.exists():
        return {"file": "assente"}
    entries = json.loads(FILE.read_text())
    stats: Counter = Counter()
    with connect() as conn:
        finish = log_job(conn, "manuale")
        model = load_active(conn)
        done = {r["listing_id"] for r in conn.execute(
            "SELECT DISTINCT listing_id FROM valuations WHERE engine_version LIKE '%%+manuale-v2'").fetchall()}
        for e in entries:
            lid = e["id"]
            if lid in done and not e.get("aggiorna"):
                stats["gia_importati"] += 1
                continue
            row = conn.execute("SELECT * FROM listings WHERE id=%s", (lid,)).fetchone()
            if not row:
                stats["non_trovato"] += 1
                continue
            l = row_to_listing(row)
            if e.get("esito") == "scartata" or e.get("danno") == "grave":
                set_stage(conn, lid, "scartato", "analisi_manuale: " + str(e.get("nota", ""))[:150])
                stats["scartate"] += 1
                conn.commit()
                continue
            items = sanitize_items(e.get("danni") or [])
            l.damage_class = e.get("danno") or ("nessuno" if not items else "medio")
            l.damage_items = [DamageItem(**{k: v for k, v in it.items() if k in DamageItem.__dataclass_fields__})
                              for it in items]
            km_est = estimate_missing_km(l)
            v = value_listing(l, load_market(conn, l.make, l.model, l.fuel))
            if weak(v):
                apply_model(v, l, model)
            v.engine_version += "+manuale-v2"
            # rivendita corretta a mano (es. allestimento povero, limitazioni di circolazione)
            if e.get("rivendita_prudente") and v.resale_prudent:
                v.resale_prudent = min(v.resale_prudent, int(e["rivendita_prudente"]))
            parts = None
            if e.get("ricambi_max") is not None:
                lines = [{"part": p.get("label"), "label": p.get("label"), "low": p.get("low"), "high": p.get("high"),
                          "probable_hidden": False, "offers": [{"url": p["offer"], "price_eur": p.get("high"),
                          "type": "aftermarket", "seller": ""}] if p.get("offer") else []}
                         for p in e.get("ricambi") or []]
                parts = {"parts_cost_low": e.get("ricambi_min"), "parts_cost_high": e["ricambi_max"],
                         "lines": lines, "complete": True, "vehicle": f"{l.make} {l.model} {l.year}",
                         "by_type": {"aftermarket": {"high": e["ricambi_max"], "complete": True}}}
            m = compute_margin(l, v, DealerCosts(), parts["parts_cost_high"] if parts else None)
            if m and e.get("esito") == "da_verificare" and m.status == "opportunita":
                m.status = "da_verificare"         # l'analisi chiede controlli prima di proporla
            mot = report.motivation(l, v, parts)
            if e.get("nota"):
                mot.append("Analisi: " + e["nota"])
            chk = report.checks(l, v, parts) + list(e.get("controlli") or [])
            if km_est:
                l.mileage_km = None                 # la stima serve solo al calcolo, non si salva
                chk.append("Km non dichiarati: stimati in modo prudente, da chiedere al venditore")
            update_listing_fields(conn, lid, l)
            save_valuation(conn, lid, v, parts, mot, chk, asdict(m) if m else None)
            set_stage(conn, lid, "approfondito", m.status if m else "non_valutabile")
            conn.execute("UPDATE listings SET deep_at=now() WHERE id=%s", (lid,))
            stats[m.status if m else "non_valutabile"] += 1
            conn.commit()
        finish(True, dict(stats))
    log.info("MANUALE %s", dict(stats))
    return dict(stats)
