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
    sospette = [0]
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
        if (v["categoria"] in scovo.CATEGORIE_SITO and v.get("M") and r["price_eur"]
                and v["M"] > 1.5 * r["price_eur"] and sospette[0] < int(os.environ.get("POLICY_LOG_SOSPETTE", "12"))):
            sospette[0] += 1         # controllo a vista: mercato molto sopra il prezzo (solo dati dell'auto, niente venditori)
            log.warning("POLICY_SOSPETTA %s", json.dumps({
                "id": r["id"], "auto": f"{r['make']} {r['model']} {r['version_raw'] or ''} {r['year']} {r['mileage_km']}km "
                                      f"{r['fuel']} {r['gearbox']} {r['power_kw']}kW", "prezzo": r["price_eur"],
                "M": v["M"], "L": v["L"], "R": v.get("R_finale"), "margine": v.get("margine"), "cat": v["categoria"],
                "confronti": [[c["prezzo"], c.get("prezzo_corretto"), c["anno"], c["km"], c["kw"], (c.get("versione") or "")[:20]]
                              for c in (v.get("confronti") or [])[:12]]}, ensure_ascii=False, default=str))
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


# ------------------------------------------------------------------ auto da 1.000 a 7.000 € (utilitarie)
UTILITARIE = {"panda", "punto", "grande-punto", "punto-evo", "500", "ypsilon", "fiesta", "ka", "ka+", "fusion", "polo", "up",
              "fox", "lupo", "yaris", "aygo", "iq", "clio", "twingo", "modus", "zoe", "208", "207", "206", "107", "108",
              "c1", "c2", "c3", "c3-picasso", "ds3", "corsa", "agila", "adam", "karl", "micra", "note", "i10", "i20",
              "getz", "atos", "picanto", "rio", "venga", "ibiza", "mii", "arona", "fabia", "citigo", "roomster", "swift",
              "splash", "alto", "celerio", "ignis", "jazz", "sandero", "logan", "spark", "matiz", "colt", "space-star",
              "mito", "a1", "fortwo", "forfour", "one", "cooper", "1-series", "serie-1", "mazda2", "2", "colt", "500l",
              "500x", "qubo", "fiorino", "idea", "musa", "delta", "stonic", "kalos", "aveo", "duster", "captur", "2008",
              "juke", "golf", "a-class", "classe-a", "a-klasse", "b-klasse", "classe-b", "auris", "corolla", "megane", "astra",
              "focus", "bravo", "308", "c4", "leon", "ceed", "i30", "civic"}

UTIL_SQL = ("SELECT {cols}, l.title, l.description, l.telefono_ok, l.damage_declared, l.problem_search FROM listings l "
            "WHERE l.status='attivo' AND l.seller_type='privato' AND l.price_eur BETWEEN %(lo)s AND %(hi)s "
            "AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s AND l.province = ANY(%(prov)s) "
            "AND l.last_seen_at > now() - interval '4 days' AND l.source IN ('subito','autoscout24','facebook') "
            "AND COALESCE(l.damage_class, 'sconosciuto') IN ('nessuno','sconosciuto') AND NOT COALESCE(l.problem_search, false) "
            "AND COALESCE(l.damage_declared, false) = false "
            "AND COALESCE(l.stage_reason, '') NOT IN ('doppione','truffa','prezzo_finto','dati_incoerenti')")


def utilitarie(conn, pubblica: bool = False) -> dict:
    """Tutte le auto sane di privati da UTIL_MIN a UTIL_MAX € (con il numero): valutazione con la policy e
    simulazione di quante superano UTIL_MARGINE € di margine con riduzioni diverse sotto i 10.000 €.
    Con pubblica=True quelle che passano con la tabella in vigore vanno sul sito (stage 'approfondito')."""
    from ..core.models import Listing
    from ..core.normalize import problem_hint
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    from ..zone import MILANO
    from ..pipeline.tempo_reale import ha_numero_testo
    t0 = time.time()
    lo, hi = int(os.environ.get("UTIL_MIN", "1000")), int(os.environ.get("UTIL_MAX", "7000"))
    margine_min = int(os.environ.get("UTIL_MARGINE", "1000"))
    cfg = P.PolicyConfig.from_env()
    rows = conn.execute(UTIL_SQL.format(cols=TARGET_COLS), {"lo": lo, "hi": hi, "anno": scovo.ANNO_MINIMO,
                                                             "km": scovo.KM_MASSIMO, "prov": list(MILANO)}).fetchall()
    rows.sort(key=lambda r: (r["make"] or "", r["model"] or "", r["fuel"] or ""))
    st, vals, gruppo, mercato = Counter(), [], None, []
    for r in rows:
        if r["source"] == "autoscout24":
            tel = r["telefono_ok"]                       # None = non ancora controllato (lo fa l'agente di verifica)
        elif r["source"] == "subito":
            tel = ha_numero_testo(r["title"], r["description"])
        else:
            tel = True
        if tel is False:
            st["senza_numero"] += 1
            continue
        if problem_hint(Listing(source=r["source"], source_id="", url="", title=r["title"], description=r["description"])):
            st["danni_nel_testo"] += 1
            continue
        if not (r["make"] and r["model"] and r["fuel"]):
            st["dati_incompleti"] += 1
            continue
        key = (r["make"], r["model"], r["fuel"])
        if key != gruppo:
            gruppo = key
            mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                       for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
        t = dict(r, vehicle_identity=_j(r.get("vehicle_identity")), parts_cost_low=None, parts_cost_high=None,
                 rischio_strutturale=_rischio(r))
        try:
            v = P.valuta(t, mercato, margine_min, cfg)
        except Exception as e:
            st["errore"] += 1
            log.warning("utilitarie: %s non valutata: %s", r["id"], str(e)[:120])
            continue
        st[f"cat:{v['categoria']}"] += 1
        if v.get("M"):
            vals.append({"id": r["id"], "src": r["source"], "p": int(r["price_eur"]), "M": v["M"], "L": v["L"], "n": v["n"],
                         "tel": tel, "util": (r["model"] or "") in UTILITARIE, "v": v})
    # simulazione: riduzione unica sotto i 10.000 € (sopra resta la tabella), margine = R − prezzo
    sim = {}
    for rid in (.30, .25, .22, .20, .18, .15):
        ok = [x for x in vals if x["n"] >= cfg.n_minimo and x["p"] >= x["M"] * cfg.sospetto_sotto_m
              and min(x["M"] * (1 - rid), x["L"]) - x["p"] >= margine_min]
        sim[f"{int(rid*100)}%"] = {"tutte": len(ok), "n>=5": sum(1 for x in ok if x["n"] >= 5),
                                   "utilitarie": sum(1 for x in ok if x["util"]),
                                   "con_numero_certo": sum(1 for x in ok if x["tel"] is True),
                                   "per_fascia": dict(Counter(f"{(x['p'] // 1000) * 1000}" for x in ok))}
    out = {"lette": len(rows), "valutate": len(vals), "stats": dict(st), "simulazione": sim, "secondi": round(time.time() - t0)}
    log.warning("UTILITARIE_SIM %s", json.dumps(out, ensure_ascii=False))
    # esempi (solo dati dell'auto) con la riduzione in vigore
    pass_ = [x for x in vals if x["v"]["categoria"] in scovo.CATEGORIE_SITO and (x["v"].get("margine") or 0) >= margine_min]
    for x in sorted(pass_, key=lambda x: -x["v"]["margine"])[:8]:
        v = x["v"]
        log.warning("UTILITARIE_ESEMPIO %s", json.dumps({"id": x["id"], "prezzo": x["p"], "M": x["M"], "L": x["L"], "n": x["n"],
                    "R": v.get("R_finale"), "rid": v.get("riduzione"), "margine": v.get("margine"), "cat": v["categoria"],
                    "confronti": [[c["prezzo"], c.get("prezzo_corretto"), c["anno"], c["km"], c["kw"]] for c in (v.get("confronti") or [])[:8]]},
                    default=str))
    if pubblica:
        n = 0
        for x in pass_:
            v = x["v"]
            conn.execute(
                """INSERT INTO valutazioni_policy (listing_id, policy_version, categoria, margine, riassunto, dati)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (listing_id, policy_version) DO UPDATE SET computed_at=now(),
                   categoria=EXCLUDED.categoria, margine=EXCLUDED.margine, riassunto=EXCLUDED.riassunto, dati=EXCLUDED.dati""",
                (x["id"], P.POLICY_VERSION, v["categoria"], v.get("margine"), json.dumps(P.riassunto(v), default=str),
                 json.dumps(v, default=str)))
            if not conn.execute("SELECT 1 FROM valuations WHERE listing_id=%s LIMIT 1", (x["id"],)).fetchone():
                conn.execute(
                    """INSERT INTO valuations (listing_id, engine_version, private_median, resale_median, resale_prudent,
                         n_comparables, comparables_used, confidence, parts_cost_low, parts_cost_high)
                       VALUES (%s,%s,%s,%s,%s,%s,'[]',%s,0,0)""",
                    (x["id"], P.POLICY_VERSION + "+utilitarie", v.get("M"), v.get("M"), v.get("R_finale"), v.get("n"),
                     "affidabile" if v["categoria"] == "opportunita" else "da_verificare"))
            conn.execute("UPDATE listings SET stage='approfondito', stage_reason=%s, deep_at=COALESCE(deep_at, now()), "
                         "last_checked_at=now() WHERE id=%s AND stage <> 'approfondito'",
                         ("opportunita" if v["categoria"] == "opportunita" else "da_verificare", x["id"]))
            n += 1
        conn.commit()
        log.warning("UTILITARIE_PUBBLICATE %d", n)
    return out
