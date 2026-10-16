"""Ricalcola con la policy pv2 tutte le auto che il sito potrebbe mostrare e salva il risultato
in `valutazioni_policy`. Scrive nei log il confronto prima/dopo (policy precedente → pv2).

    python -m dealfinder.pricing.ricalcolo          (oppure in sottofondo dal sito, ogni POLICY_ORE ore)
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

from . import policy_v2 as P

log = logging.getLogger("policy")

TARGET_COLS = ("l.id, l.source, l.url, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox, "
               "l.power_kw, l.price_eur, l.price_flags, l.seller_type, l.damage_class, l.damage_items, l.vehicle_id, "
               "l.vehicle_identity, l.stage_reason")
FIN_SQL = (r"COALESCE(description ~* '(solo|vincolat\w*|valid\w*|previo|tramite|con)\s+(a\s+|al\s+|il\s+|un\s+)?"
           r"finanziament', false) AS finanziamento")
MARKET_SQL = ("SELECT id, source, url, vehicle_id, vehicle_identity, make, model, version_raw, year, mileage_km, fuel, "
              "gearbox, power_kw, price_eur, price_flags, seller_type, damage_class, status, stage_reason, problem_search, "
              "first_seen_at, last_seen_at, last_checked_at, " + FIN_SQL + " FROM listings "
              "WHERE status='attivo' AND make=%s AND model=%s AND fuel=%s AND price_eur IS NOT NULL "   # usa listings_cmp_idx
              "AND {home}")
RISCHIO = {"telaio_longheroni", "tetto_montanti", "airbag_esplosi", "frontale_profondo", "airbag",
           "modulo_airbag_volante", "modulo_airbag_passeggero", "centralina_airbag", "motore_sostituzione",
           "cambio_sostituzione", "alluvionata", "incendio"}


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _rischio(r) -> bool:
    parts = {str((d or {}).get("part")) for d in (_j(r.get("damage_items")) or [])}
    return r.get("damage_class") == "alto_rischio" or bool(parts & RISCHIO)


def _prima(it: dict | None, abbastanza) -> dict | None:
    """Numeri della policy precedente (mercato −10% − 140 € − ricambi) per il confronto."""
    if not it or not it.get("mercato"):
        return None
    from ..web import scovo
    return {"mercato": it["mercato"], "rivendita": round(it["mercato"] * (1 - scovo.SCONTO_DEFAULT / 100)),
            "guadagno": scovo.guadagno_minimo(it), "mostrata": bool(abbastanza)}


def esegui(conn, limite: int | None = None) -> dict:
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    t0 = time.time()
    conn.execute("SET statement_timeout = '60s'")
    cfg = P.PolicyConfig.from_env()
    # 1) le auto candidate: quelle approfondite (sezioni affari e incidentate) + la sezione economica
    rows = conn.execute(scovo.LIST_SQL + " LIMIT 4000", {"me": 0}).fetchall()
    eco = conn.execute(scovo.ECO_SQL + " ORDER BY l.first_seen_at DESC LIMIT %(lim)s",
                       dict(scovo.eco_params(0), lim=int(os.environ.get("ECONOMICHE_QUANTE", "2500")))).fetchall()
    prima, info = {}, {}
    for r in rows:
        it = scovo.item(r)
        prima[r["id"]] = _prima(it, scovo.abbastanza(it, policy=False))
        info[r["id"]] = {"parts_cost_low": r.get("parts_cost_low"), "parts_cost_high": r.get("parts_cost_high"),
                         "eco": False, "audit": "+manuale-v5-" in (r.get("engine_version") or ""),
                         "mercato_audit": it["mercato"] if it else None}
    for r in eco:
        if r["id"] in info:
            continue
        it = scovo.item_eco(r)
        prima[r["id"]] = _prima(it, it and (it["prezzo"] <= int(os.environ.get("ECONOMICHE_LIBERO", "1000"))
                                           or scovo.guadagno_minimo(it) >= int(os.environ.get("ECONOMICHE_GUADAGNO", "400"))))
        info[r["id"]] = {"parts_cost_low": r.get("parts_cost_low"), "parts_cost_high": r.get("parts_cost_high"), "eco": True}
    ids = list(info)[:limite] if limite else list(info)
    targets = conn.execute(f"SELECT {TARGET_COLS} FROM listings l WHERE l.id = ANY(%s)", (ids,)).fetchall()
    targets.sort(key=lambda r: (r["make"] or "", r["model"] or "", r["fuel"] or ""))

    cat, trans, motivi, ns = Counter(), Counter(), Counter(), Counter()
    fasce = {i: Counter() for i in range(len(scovo.COPERTURA_FASCE))}
    campioni, gruppo, mercato, gruppi = [], None, [], 0
    for r in targets:
        key = (r["make"], r["model"], r["fuel"])
        if key != gruppo:
            gruppo = key
            mercato = (conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()
                       if r["make"] and r["model"] and r["fuel"] else [])
            gruppi += 1
            if gruppi % 50 == 0:
                log.warning("POLICY avanzamento: %d gruppi, %d auto, %.0fs", gruppi, sum(cat.values()), time.time() - t0)
        t = dict(r, **{k: v for k, v in info[r["id"]].items() if k.startswith("parts_")})
        t["vehicle_identity"] = _j(t.get("vehicle_identity"))
        t["rischio_strutturale"] = _rischio(r)
        if info[r["id"]].get("audit") and info[r["id"]].get("mercato_audit"):
            t["tetto_rivendita"] = info[r["id"]]["mercato_audit"]
        soglia = scovo.soglia(int(r["price_eur"] or 0))
        try:
            v = P.valuta(t, [dict(m, vehicle_identity=_j(m.get("vehicle_identity"))) for m in mercato], soglia, cfg)
        except Exception as e:                       # una valutazione sbagliata non ferma le altre
            log.warning("policy: auto %s non valutata: %s", r["id"], str(e)[:200])
            continue
        v["sezione"] = "economiche" if info[r["id"]]["eco"] else "affari"
        pr = prima.get(r["id"])
        conn.execute(
            """INSERT INTO valutazioni_policy (listing_id, policy_version, categoria, margine, riassunto, dati, prima)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (listing_id, policy_version) DO UPDATE SET computed_at=now(), categoria=EXCLUDED.categoria,
                 margine=EXCLUDED.margine, riassunto=EXCLUDED.riassunto, dati=EXCLUDED.dati, prima=EXCLUDED.prima""",
            (r["id"], P.POLICY_VERSION, v["categoria"], v.get("margine"), json.dumps(P.riassunto(v), default=str),
             json.dumps(v, default=str), json.dumps(pr) if pr else None))
        cat[(v["sezione"], v["categoria"])] += 1
        if v["sezione"] == "affari" and (r["year"] or 0) >= scovo.ANNO_MINIMO and not scovo.troppi_km(r["mileage_km"]):
            f = scovo._fascia(int(r["price_eur"] or 0))
            if v["categoria"] in scovo.CATEGORIE_SITO:
                fasce[f]["ok"] += 1
            elif (v["categoria"] == "non_conveniente" and v.get("margine") is not None
                  and v["margine"] >= max(scovo.COPERTURA_MARGINE_MIN, scovo.COPERTURA_QUOTA * soglia)):
                fasce[f]["riserva"] += 1
        ns["≥10" if v.get("n", 0) >= 10 else "5-9" if v.get("n", 0) >= 5 else "<5"] += 1
        if pr and pr["mostrata"]:
            trans[(v["sezione"], v["categoria"])] += 1
            motivi[(v["motivi"] or ["—"])[0].split(" (")[0].split(":")[0][:60]] += 1
            if len(campioni) < int(os.environ.get("POLICY_CAMPIONE", "25")) and v["sezione"] == "affari":
                campioni.append({"id": r["id"], "auto": f"{r['make']} {r['model']} {r['year']} {r['mileage_km']}km {r['fuel']}",
                                 "prezzo": r["price_eur"], "prima_mercato": pr["mercato"], "prima_guadagno": pr["guadagno"],
                                 "M": v.get("M"), "L": v.get("L"), "n": v.get("n"), "liv": v.get("livello"),
                                 "rid": v.get("riduzione"), "R": v.get("R_finale"), "costi": v.get("totale_costi"),
                                 "riserva": (v.get("riserva") or {}).get("euro"), "margine": v.get("margine"),
                                 "soglia": soglia, "cat": v["categoria"], "perche": (v["motivi"] or [""])[:2]})
        conn.commit()
    stats = {"valutate": len(targets), "secondi": round(time.time() - t0, 1),
             "prima_mostrate": sum(1 for p in prima.values() if p and p["mostrata"]),
             "categorie": {f"{a}:{b}": n for (a, b), n in sorted(cat.items())},
             "mostrate_prima_ora": {f"{a}:{b}": n for (a, b), n in sorted(trans.items())},
             "confronti": dict(ns),
             "fasce_affari": {f"<{lim}": dict(fasce[i]) for i, lim in enumerate(scovo.COPERTURA_FASCE)}, "motivi_principali": dict(motivi.most_common(12))}
    log.warning("POLICY_RIEPILOGO %s", json.dumps(stats, ensure_ascii=False))
    for c in campioni:
        log.warning("POLICY_PRIMA_DOPO %s", json.dumps(c, ensure_ascii=False, default=str))
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from ..db import connect
    with connect() as conn:
        print(esegui(conn))
