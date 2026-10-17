"""Ricalcola con la policy pv2 tutte le auto che il sito potrebbe mostrare e salva il risultato
in `valutazioni_policy`. Scrive nei log il confronto prima/dopo (policy precedente → pv2).

    python -m dealfinder.pricing.ricalcolo          (oppure in sottofondo dal sito, ogni POLICY_ORE ore)
"""
from __future__ import annotations

import json
import re
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


# Ripiego (richiesta del 17/10): se nel mercato di casa ci sono meno di 5 auto equivalenti, si cercano i confronti
# in tutto il Nord Italia della nostra base prezzi. L'auto proposta resta entro 150 km; la valutazione lo dichiara.
NORD = ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV", "CR", "MN", "SO", "NO", "VC", "BI", "VB", "AL", "AT", "TO",
        "CN", "AO", "GE", "SV", "IM", "SP", "PC", "PR", "RE", "MO", "BO", "FE", "RA", "FC", "RN", "VR", "VI", "PD", "VE",
        "TV", "BL", "RO", "TN", "BZ", "UD", "PN", "GO", "TS"]
NORD_SQL = "province IN (" + ",".join(f"'{p}'" for p in NORD) + ")"


def valuta_con_ripiego(conn, t: dict, mercato: list, soglia: int, cfg, cache: dict) -> dict:
    v = P.valuta(t, mercato, soglia, cfg)
    if os.environ.get("POLICY_RIPIEGO_NORD", "1") != "1" or (v.get("n") or 0) >= cfg.n_preferito \
            or not (t.get("make") and t.get("model") and t.get("fuel")):
        return v
    key = (t["make"], t["model"], t["fuel"])
    if key not in cache:
        cache.clear()                                    # un gruppo alla volta: poca memoria
        cache[key] = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                      for m in conn.execute(MARKET_SQL.format(home=NORD_SQL), key).fetchall()]
    v2 = P.valuta(t, cache[key], soglia, cfg)
    if (v2.get("n") or 0) > (v.get("n") or 0):
        v2["fuori_zona"] = True
        v2.setdefault("ipotesi", []).append("pochi confronti entro 150 km: usati anche annunci uguali nel resto del "
                                            "Nord Italia (prezzi richiesti, possibili piccole differenze di zona)")
        return v2
    return v


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
    cache_nord: dict = {}
    for r in targets:
        key = (r["make"], r["model"], r["fuel"])
        if key != gruppo:
            gruppo = key
            cache_nord.clear()                       # memoria: il ripiego Nord serve solo al gruppo corrente
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
            v = valuta_con_ripiego(conn, t, [dict(m, vehicle_identity=_j(m.get("vehicle_identity"))) for m in mercato],
                                   soglia, cfg, cache_nord)
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
            "AND l.last_seen_at > now() - make_interval(days => " + os.environ.get("TUTTE_GIORNI", "14") + ") AND l.source IN ('subito','autoscout24','facebook') "
            "AND COALESCE(l.damage_class, 'sconosciuto') IN ('nessuno','sconosciuto') AND NOT COALESCE(l.problem_search, false) "
            "AND COALESCE(l.damage_declared, false) = false "
            "AND COALESCE(l.stage_reason, '') NOT IN ('doppione','truffa','prezzo_finto','dati_incoerenti') "
            "AND COALESCE(l.stage_reason, '') NOT LIKE 'analisi_manuale%%'")      # scartate a mano: mai ripubblicate


_CIL = re.compile(r"\b(\d\.\d)\b")


def gemelle(t: dict, mercato: list[dict], now=None) -> list[dict]:
    """Auto IDENTICHE in vendita (idea del cliente, 17/10): stessa marca/modello (gruppo), alimentazione, cambio,
    carrozzeria, cilindrata scritta e potenza quasi uguale (±5%), anno ±1, km ±20.000; sane, attive, viste di recente."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    if not (t.get("year") and t.get("mileage_km") is not None):
        return []
    tg, bt, tp = P._norm_gear(t.get("gearbox")), P.carrozzeria(t), t.get("power_kw")
    tc = set(_CIL.findall(t.get("version_raw") or ""))
    out = []
    for c in mercato:
        if c.get("id") == t.get("id") or c.get("status") != "attivo" or not c.get("price_eur") or c["price_eur"] < 500:
            continue
        if c.get("fuel") != t.get("fuel") or not c.get("year") or c.get("mileage_km") is None:
            continue
        if abs(int(c["year"]) - int(t["year"])) > 1 or abs(int(c["mileage_km"]) - int(t["mileage_km"])) > 20000:
            continue
        cg = P._norm_gear(c.get("gearbox"))
        if (tg or cg) and tg != cg:
            continue
        if P.carrozzeria(c) != bt:
            continue
        if tp and c.get("power_kw") and abs(c["power_kw"] - tp) > tp * .05:
            continue
        if bool(tp) != bool(c.get("power_kw")) and not tc:
            continue                                          # senza potenza né cilindrata non si può dire "identica"
        cc = set(_CIL.findall(c.get("version_raw") or ""))
        if tc and cc and not (tc & cc):
            continue
        if (c.get("damage_class") or "sconosciuto") in P.DANNEGGIATE or c.get("problem_search") or c.get("finanziamento"):
            continue
        if c.get("stage_reason") in ("doppione", "truffa", "prezzo_finto") or set(c.get("price_flags") or []) & set(P.FLAG_ESCLUSI):
            continue
        visto = max((d for d in (c.get("last_checked_at"), c.get("last_seen_at")) if d), default=None)
        if not visto or (now - visto).days > 7:
            continue
        out.append(c)
    return sorted(out, key=lambda c: c["price_eur"])


def gemella_rivendita(t: dict, twins: list[dict], M: float | None, sconto: int = 1000) -> dict | None:
    """Rivendita = gemella più economica AFFIDABILE − sconto. Protezione contro l'annuncio "strano": la gemella
    scelta non può stare sotto l'85% della mediana delle gemelle (se sono ≥ 3) né sotto il 70% del mercato M."""
    if not twins:
        return None
    prezzi = [c["price_eur"] for c in twins]
    med = P.percentile(prezzi, .5)
    for c in twins:
        if len(twins) >= 3 and c["price_eur"] < .85 * med:
            continue
        if M and c["price_eur"] < .7 * M:
            continue
        return {"gemella": c, "rivendita": int(c["price_eur"]) - sconto, "n_gemelle": len(twins)}
    return None


_UTIL_LOCK = __import__("threading").Lock()


def utilitarie(conn, pubblica: bool = False) -> dict:
    """Un solo calcolo completo alla volta nello stesso processo (memoria: 512 MB su Render)."""
    if not _UTIL_LOCK.acquire(blocking=False):
        log.warning("UTILITARIE già in corso: salto")
        return {}
    try:
        return _utilitarie(conn, pubblica)
    finally:
        _UTIL_LOCK.release()


def _utilitarie(conn, pubblica: bool = False) -> dict:
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
    # dal 17/10: TUTTI gli annunci raccolti entro 150 km (auto sane di privati), da 500 a 50.000 €
    lo, hi = int(os.environ.get("TUTTE_MIN", "500")), int(os.environ.get("TUTTE_MAX", "50000"))
    cfg = P.PolicyConfig.from_env()
    from ..core.dal_testo import completa_db
    log.warning("DAL_TESTO %s", json.dumps(completa_db(conn)))          # anno, km, alimentazione mancanti: dal testo
    cache_nord: dict = {}
    rows = conn.execute(UTIL_SQL.format(cols=TARGET_COLS), {"lo": lo, "hi": hi, "anno": scovo.ANNO_MINIMO,
                                                             "km": scovo.KM_MASSIMO, "prov": list(MILANO)}).fetchall()
    rows.sort(key=lambda r: (r["make"] or "", r["model"] or "", r["fuel"] or ""))
    import gc
    pavimento = min(int(os.environ.get("AS_TOP_MIN", "600")), int(os.environ.get("TOP_MIN_EURO", "800")))
    st, vals, gruppo, mercato = Counter(), [], None, []
    gsim, gesempi = Counter(), []
    for i_r in range(len(rows)):
        r, rows[i_r] = rows[i_r], None                 # memoria: ogni riga si libera dopo l'uso
        if i_r % 2000 == 0:
            gc.collect()
            log.warning("UTILITARIE avanzamento: %d/%d auto, %.0fs", i_r, len(rows), time.time() - t0)
        if r["source"] == "autoscout24":
            tel = r["telefono_ok"]                       # None = non ancora controllato (lo fa l'agente di verifica)
        elif r["source"] == "subito":
            # numero dietro il bottone "Chiama" (telefono_ok) oppure scritto nel testo
            # IMBUTO: su Subito il numero NON filtra all'inizio. Con il numero nel testo = sì; altrimenti "da controllare"
            # (None): se l'auto viene selezionata, l'agente apre la sua pagina e controlla il bottone Chiama.
            tel = True if (r["telefono_ok"] or ha_numero_testo(r["title"], r["description"])) else (
                False if r["telefono_ok"] is False else None)
        else:
            tel = True
        if tel is False:
            st["senza_numero"] += 1               # valutata solo per la simulazione, mai pubblicata
        if problem_hint(Listing(source=r["source"], source_id="", url="", title=r["title"], description=r["description"])):
            st["danni_nel_testo"] += 1
            continue
        if not (r["make"] and r["model"] and r["fuel"]):
            st["dati_incompleti"] += 1
            continue
        key = (r["make"], r["model"], r["fuel"])
        if key != gruppo:
            gruppo = key
            cache_nord.clear()                       # memoria (512 MB su Render): solo il gruppo corrente
            mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                       for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
        t = dict(r, vehicle_identity=_j(r.get("vehicle_identity")), parts_cost_low=None, parts_cost_high=None,
                 rischio_strutturale=_rischio(r))
        try:
            v = valuta_con_ripiego(conn, t, mercato, scovo.soglia(int(r["price_eur"])), cfg, cache_nord)
        except Exception as e:
            st["errore"] += 1
            log.warning("utilitarie: %s non valutata: %s", r["id"], str(e)[:120])
            continue
        st[f"cat:{v['categoria']}"] += 1
        p_ = int(r["price_eur"])
        if 1000 <= p_ <= 5000:                         # simulazione "gemella più economica − 1.000 €"
            g = gemella_rivendita(t, gemelle(t, mercato), v.get("M"))
            ok_now = v["categoria"] in scovo.CATEGORIE_SITO and (v.get("margine") or 0) >= scovo.soglia(p_)
            ok_g = bool(g) and g["rivendita"] - p_ >= scovo.soglia(p_)
            gsim["auto_1000_5000"] += 1
            gsim["con_gemella"] += bool(g)
            gsim["passano_regola_attuale"] += ok_now
            gsim["passano_gemella"] += ok_g
            gsim["passano_entrambe"] += ok_now and ok_g
            gsim["solo_gemella"] += ok_g and not ok_now
            gsim["solo_attuale"] += ok_now and not ok_g
            if g and len(gesempi) < 15 and (ok_g or ok_now):
                c = g["gemella"]
                gesempi.append({"id": r["id"], "auto": f"{r['make']} {r['model']} {r['version_raw'] or ''} {r['year']} {r['mileage_km']}km",
                                "prezzo": p_, "gemella": [c["price_eur"], c["year"], c["mileage_km"], c.get("url")],
                                "n_gemelle": g["n_gemelle"], "riv_gemella": g["rivendita"], "margine_gemella": g["rivendita"] - p_,
                                "M": v.get("M"), "riv_attuale": v.get("R_finale"), "margine_attuale": v.get("margine")})
        if v.get("M"):
            # memoria: la valutazione completa (con i confronti) si tiene solo per le auto che possono andare sul sito
            utile = v.get("margine") is not None and v["margine"] >= pavimento
            vals.append({"id": r["id"], "src": r["source"], "p": int(r["price_eur"]), "M": v["M"], "L": v["L"], "n": v["n"],
                         "tel": tel, "util": (r["model"] or "") in UTILITARIE, "fuel": r["fuel"],
                         "v": v if utile else {"categoria": v["categoria"], "margine": v.get("margine")}})
    sim = {"pubblicabili_per_fascia": dict(Counter(
        next(f"<{lim}" for lim in (5000, 10000, 15000, 20000, 30000, 10**9) if x["p"] < lim)
        for x in vals if x["v"]["categoria"] in scovo.CATEGORIE_SITO))}
    out = {"lette": len(rows), "valutate": len(vals), "stats": dict(st), "simulazione": sim, "secondi": round(time.time() - t0)}
    log.warning("GEMELLA_SIM %s", json.dumps(dict(gsim)))
    for e in gesempi:
        log.warning("GEMELLA_ESEMPIO %s", json.dumps(e, default=str, ensure_ascii=False))
    log.warning("UTILITARIE_SIM %s", json.dumps(out, ensure_ascii=False))
    # esempi (solo dati dell'auto) con la riduzione in vigore
    # simulazione: quante auto con soglie più basse (stessa rivendita, niente prezzi gonfiati) e anche senza numero
    soglie_sim = {}
    for f in (1.0, .85, .7, .6):
        for senza in (False, True):
            ok = [x for x in vals if x["v"].get("margine") is not None and (senza or x["tel"] is not False)
                  and x["v"]["categoria"] in ("opportunita", "candidato", "non_conveniente")
                  and x["v"]["margine"] >= scovo.soglia(x["p"]) * f]
            soglie_sim[f"soglie_x{f}{'_anche_senza_numero' if senza else ''}"] = {
                "tutte": len(ok), "diesel": sum(1 for x in ok if x["fuel"] == "diesel"),
                "per_fascia": dict(Counter(next(f"<{lim}" for lim in (5000, 10000, 15000, 20000, 10**9) if x["p"] < lim) for x in ok))}
    log.warning("SOGLIE_SIM %s", json.dumps(soglie_sim, ensure_ascii=False))
    # sopra la soglia piena anche SENZA numero (17/10): sul sito ne entra al massimo il 20%, solo le super valide e con
    # la targa visibile nelle foto (app._senza_numero_super); le altre restano fuori
    pass_ = [x for x in vals if x["v"]["categoria"] in scovo.CATEGORIE_SITO
             and (x["v"].get("margine") or 0) >= scovo.soglia(x["p"])]
    # Le migliori TOP_N (richiesta del 17/10: "le 250 più convenienti"): oltre a quelle sopra soglia, le migliori per
    # margine rispetto alla soglia della fascia, purché il margine sia almeno TOP_MIN_EURO e TOP_QUOTA della soglia.
    top_n = int(os.environ.get("TOP_N", "550"))      # più largo: una parte cade al controllo del numero
    top_min, top_quota = int(os.environ.get("TOP_MIN_EURO", "800")), float(os.environ.get("TOP_QUOTA", "0.6"))
    gia = {x["id"] for x in pass_}
    extra = [x for x in vals if x["id"] not in gia and x["tel"] is not False and x["v"]["categoria"] == "non_conveniente"
             and x["v"].get("margine") is not None
             and x["v"]["margine"] >= max(top_min, top_quota * scovo.soglia(x["p"]))]
    extra.sort(key=lambda x: -x["v"]["margine"] / scovo.soglia(x["p"]))
    extra = extra[:max(0, top_n - len(pass_))]
    # AutoScout24 (richiesta del 17/10): le migliori AS_TOP_N auto AutoScout analizzate, con margine un po' più flessibile
    # (almeno AS_TOP_MIN € e AS_TOP_QUOTA della soglia). Quelle col numero non ancora controllato le verifica l'agente
    # entro pochi giri; senza numero non vanno sul sito.
    as_n = int(os.environ.get("AS_TOP_N", "160"))
    as_min, as_quota = int(os.environ.get("AS_TOP_MIN", "600")), float(os.environ.get("AS_TOP_QUOTA", "0.4"))
    scelti = {x["id"] for x in pass_ + extra}
    as_gia = sum(1 for x in pass_ + extra if x["src"] == "autoscout24")
    as_extra = [x for x in vals if x["src"] == "autoscout24" and x["id"] not in scelti and x["tel"] is not False
                and x["v"]["categoria"] == "non_conveniente" and x["v"].get("margine") is not None
                and x["v"]["margine"] >= max(as_min, as_quota * scovo.soglia(x["p"]))]
    as_extra.sort(key=lambda x: -x["v"]["margine"] / scovo.soglia(x["p"]))
    as_extra = as_extra[:max(0, as_n - as_gia)]
    extra += as_extra
    # UTILITARIE ECONOMICHE (richiesta del 17/10: "almeno 50 auto da 1.000 a 3.000 €, il più discreto possibile"):
    # quota riservata alla fascia, con le stesse stime (nessun prezzo gonfiato) e un margine minimo proporzionato al
    # prezzo: almeno BASSE_EURO € e BASSE_QUOTA del prezzo d'acquisto, con almeno BASSE_N_CONFRONTI confronti.
    b_lo, b_hi = int(os.environ.get("BASSE_MIN", "500")), int(os.environ.get("BASSE_MAX", "7000"))   # 17/10 15:05: utilitarie 0-7k
    b_n = int(os.environ.get("BASSE_N", "300"))
    b_euro, b_quota = int(os.environ.get("BASSE_EURO", "1200")), float(os.environ.get("BASSE_QUOTA", "0.2"))   # 1.200 €: deciso dal cliente
    b_conf = int(os.environ.get("BASSE_N_CONFRONTI", "3"))
    scelti = {x["id"] for x in pass_ + extra}
    b_gia = sum(1 for x in pass_ + extra if b_lo <= x["p"] <= b_hi)

    def _basse(euro, quota):
        return [x for x in vals if b_lo <= x["p"] <= b_hi and x["id"] not in scelti and x["tel"] is not False
                and x["v"]["categoria"] in ("non_conveniente", "candidato", "opportunita")
                and x["v"].get("margine") is not None and (x.get("n") or 0) >= b_conf
                and x["v"]["margine"] >= max(euro, quota * x["p"])]
    b_extra = sorted(_basse(b_euro, b_quota), key=lambda x: -x["v"]["margine"])[:b_n]
    extra += b_extra
    log.warning("BASSE_0_7K già scelte=%d, aggiunte=%d (margine ≥ %d € e ≥ %.0f%% del prezzo); simulazione: %s", b_gia,
                len(b_extra), b_euro, b_quota * 100,
                {f"{e}€/{int(q * 100)}%": len(_basse(e, q)) for e, q in ((1000, .2), (1200, .2), (1500, .2), (2000, .25))})
    log.warning("TOP_AUTOSCOUT già scelte=%d, aggiunte=%d (con numero già confermato %d)", as_gia, len(as_extra),
                sum(1 for x in as_extra if x["tel"] is True))
    log.warning("TOP_AUTO sopra soglia=%d, aggiunte tra le migliori=%d (margine minimo %s)", len(pass_), len(extra),
                dict(Counter(next(f"<{lim}" for lim in (5000, 10000, 15000, 20000, 10**9) if x["p"] < lim) for x in extra)))
    for x in sorted(pass_, key=lambda x: -x["v"]["margine"])[:8]:
        v = x["v"]
        log.warning("UTILITARIE_ESEMPIO %s", json.dumps({"id": x["id"], "prezzo": x["p"], "M": x["M"], "L": x["L"], "n": x["n"],
                    "R": v.get("R_finale"), "rid": v.get("riduzione"), "margine": v.get("margine"), "cat": v["categoria"],
                    "confronti": [[c["prezzo"], c.get("prezzo_corretto"), c["anno"], c["km"], c["kw"]] for c in (v.get("confronti") or [])[:8]]},
                    default=str))
    if pubblica:
        n = 0
        conn.execute("DELETE FROM top_auto")
        for pos, x in enumerate(sorted(extra, key=lambda x: -x["v"]["margine"] / scovo.soglia(x["p"]))):
            conn.execute("INSERT INTO top_auto (listing_id, posizione, margine) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                         (x["id"], pos, x["v"]["margine"]))
        for x in pass_ + extra:
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
