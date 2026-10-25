"""Esame del software (25/10, cliente: "migliorare con i numeri, non a sensazione").

data/esame_auto.json: lista di auto valutate a mano, [{"id": 634003, "rivendita": 18000, "nota": "..."}]
("rivendita" = prezzo a cui la rivenderebbe velocemente un commerciante, deciso a mano confrontando 5–10 annunci
uguali). A ogni avvio del sito e ogni ESAME_ORE ore si confronta con la rivendita calcolata dal software per le
stesse auto e si scrive nei log:
  ESAME {"auto": 50, "valutate": 47, "errore_medio_pct": 6.1, "errore_mediano_pct": 4.8, "sovrastima_media_pct": 2.3,
         "entro_5pct": 30, "entro_10pct": 41, "peggiori": [...]}
Così ogni cambiamento al calcolo si misura: se l'errore scende, il cambiamento è buono.
"""
from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path

log = logging.getLogger("esame")
FILE = Path(__file__).resolve().parents[2] / "data" / "esame_auto.json"


def carica() -> list[dict]:
    try:
        return [x for x in json.loads(FILE.read_text()) if x.get("id") and x.get("rivendita")]
    except Exception:
        return []


def esegui(conn) -> dict:
    auto = carica()
    if not auto:
        return {"auto": 0, "nota": "data/esame_auto.json vuoto: servono le valutazioni a mano"}
    ids = [int(x["id"]) for x in auto]
    rows = {r["listing_id"]: r for r in conn.execute(
        "SELECT DISTINCT ON (listing_id) listing_id, dati, computed_at FROM valutazioni_policy "
        "WHERE listing_id = ANY(%s) ORDER BY listing_id, computed_at DESC", (ids,)).fetchall()}
    corr = {r["listing_id"]: r["correzione_pct"] for r in conn.execute(
        "SELECT listing_id, correzione_pct FROM decisione_ai WHERE listing_id = ANY(%s)", (ids,)).fetchall()}
    errori, dettagli, mancanti = [], [], 0
    for x in auto:
        r = rows.get(int(x["id"]))
        d = (r or {}).get("dati") or {}
        if isinstance(d, str):
            d = json.loads(d)
        R = d.get("R_finale") or d.get("R")
        if not R:
            mancanti += 1
            continue
        import os
        c = min(0.0, float(corr.get(int(x["id"])) or 0)) if os.environ.get("JEV_CORREGGI", "0") == "1" else 0.0
        R = R * (1 + c / 100)
        rif = float(x["rivendita"])
        e = (R - rif) / rif * 100
        errori.append(e)
        dettagli.append({"id": x["id"], "software": int(round(R)), "a_mano": int(rif), "errore_pct": round(e, 1),
                         "confronti": d.get("n")})
    if not errori:
        res = {"auto": len(auto), "valutate": 0, "senza_calcolo": mancanti}
    else:
        ass = [abs(e) for e in errori]
        res = {"auto": len(auto), "valutate": len(errori), "senza_calcolo": mancanti,
               "errore_medio_pct": round(statistics.mean(ass), 1), "errore_mediano_pct": round(statistics.median(ass), 1),
               "sovrastima_media_pct": round(statistics.mean(errori), 1),
               "entro_5pct": sum(1 for e in ass if e <= 5), "entro_10pct": sum(1 for e in ass if e <= 10),
               "peggiori": sorted(dettagli, key=lambda z: -abs(z["errore_pct"]))[:8]}
    log.warning("ESAME %s", json.dumps(res, ensure_ascii=False))
    return res


VARIANTI = {
    "attuale": {},
    "senza_riduzione_q27": {"fasce": [(50000, 0.0)], "usa_L": False, "q_veloce": .27, "adattiva": False},
    "senza_riduzione_q30": {"fasce": [(50000, 0.0)], "usa_L": False, "q_veloce": .30, "adattiva": False},
    "senza_riduzione_q35": {"fasce": [(50000, 0.0)], "usa_L": False, "q_veloce": .35, "adattiva": False},
    "senza_riduzione_q40": {"fasce": [(50000, 0.0)], "usa_L": False, "q_veloce": .40, "adattiva": False},
    "senza_riduzione_q45": {"fasce": [(50000, 0.0)], "usa_L": False, "q_veloce": .45, "adattiva": False},
    "commercianti_5": {"fasce": [(50000, 0.0)], "usa_L": False, "q_veloce": .35, "adattiva": False,
                       "rivendita_commercianti": True, "commercianti_sconto": .05},
    "commercianti_8": {"fasce": [(50000, 0.0)], "usa_L": False, "q_veloce": .35, "adattiva": False,
                       "rivendita_commercianti": True, "commercianti_sconto": .08},
    "commercianti_12": {"fasce": [(50000, 0.0)], "usa_L": False, "q_veloce": .35, "adattiva": False,
                        "rivendita_commercianti": True, "commercianti_sconto": .12},
    "mediana": {"fasce": [(50000, 0.0)], "usa_L": False, "q_veloce": .50, "adattiva": False, "veloce_max": 1.0},
}


def varianti(conn) -> dict:
    """Ricalcola le auto dell'esame con diverse impostazioni del calcolo e misura l'errore di ciascuna.
    Non cambia niente nel database: serve a scegliere la taratura con i numeri."""
    import copy
    import os
    import statistics as stx
    from . import policy_v2 as P
    from .ricalcolo import MARKET_SQL, TARGET_COLS, _j, _rischio, valuta_con_ripiego
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    auto = carica()
    if not auto:
        return {}
    rif = {int(x["id"]): float(x["rivendita"]) for x in auto}
    rows = conn.execute(f"SELECT {TARGET_COLS}, l.title, l.description, l.telefono_ok FROM listings l "
                        f"WHERE l.id = ANY(%s)", (list(rif),)).fetchall()
    base = P.PolicyConfig.from_env()
    mercati = {}
    out = {}
    for nome, over in VARIANTI.items():
        cfg = copy.deepcopy(base)
        for k, v in over.items():
            setattr(cfg, k, v)
        errs, dett = [], []
        for r in rows:
            key = (r["make"], r["model"], r["fuel"])
            if not all(key):
                continue
            if key not in mercati:
                mercati[key] = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                                for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
            t = dict(r, vehicle_identity=_j(r.get("vehicle_identity")), parts_cost_low=None, parts_cost_high=None,
                     rischio_strutturale=_rischio(r), ai=None, targa_e_telefono=True)
            try:
                v = valuta_con_ripiego(conn, t, mercati[key], scovo.soglia(int(r["price_eur"] or 0)), cfg, {})
            except Exception as e:
                log.warning("ESAME_VARIANTI %s %s: %s", nome, r["id"], str(e)[:120])
                continue
            R = v.get("R_finale") or v.get("R")
            if not R:
                continue
            e = (R - rif[r["id"]]) / rif[r["id"]] * 100
            errs.append(e)
            dett.append((r["id"], int(R), round(e)))
        if errs:
            a = [abs(e) for e in errs]
            out[nome] = {"n": len(errs), "medio": round(stx.mean(a), 1), "mediano": round(stx.median(a), 1),
                         "con_segno": round(stx.mean(errs), 1), "entro10": sum(1 for x in a if x <= 10)}
            if os.environ.get("ESAME_DETTAGLI") == "1":
                out[nome]["dettagli"] = dett
    log.warning("ESAME_VARIANTI %s", json.dumps(out, ensure_ascii=False))
    return out


VENDUTI_SQL = """
SELECT {cols}, l.disappeared_at, l.first_seen_at FROM listings l
WHERE l.status = 'scomparso' AND l.seller_type = 'commerciante'
  AND l.disappeared_at > now() - interval '60 days'
  AND l.disappeared_at - l.first_seen_at BETWEEN interval '1 day' AND make_interval(days => %(giorni)s)
  AND l.price_eur BETWEEN 1500 AND 60000 AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL
  AND l.damage_class IN ('nessuno', 'sconosciuto') AND NOT COALESCE(l.damage_declared, false)
  AND l.mileage_km IS NOT NULL AND l.year IS NOT NULL AND {home}
ORDER BY md5(l.id::text) LIMIT %(lim)s"""

VARIANTI_VENDUTI = ("attuale", "senza_riduzione_q40", "commercianti_5", "commercianti_8", "commercianti_12")


def venduti(conn) -> dict:
    """25/10 (cliente: "come risolviamo questi errori"): esame grande e automatico, senza valutazioni a mano.
    Auto di concessionari della zona sparite in poco tempo (venduta in fretta, ESAME_VENDUTI_GIORNI, default 30):
    il loro ultimo prezzo è il prezzo vero a cui un commerciante vende quella macchina. Il software le valuta
    con il mercato di oggi (loro non ci sono più, quindi non si confrontano con se stesse) e si misura l'errore,
    per ogni variante e per ogni modello. Solo lettura: non cambia niente nel database."""
    import copy
    import os
    import statistics as stx
    from collections import defaultdict
    from . import policy_v2 as P
    from .ricalcolo import MARKET_SQL, TARGET_COLS, _j, _rischio, valuta_con_ripiego
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    rows = conn.execute(VENDUTI_SQL.format(cols=TARGET_COLS, home=HOME_REGION_SQL),
                        {"giorni": int(os.environ.get("ESAME_VENDUTI_GIORNI", "30")),
                         "lim": int(os.environ.get("ESAME_VENDUTI_MAX", "400"))}).fetchall()
    rows = sorted(rows, key=lambda r: (r["make"], r["model"], r["fuel"]))
    base = P.PolicyConfig.from_env()
    cfgs = {}
    for nome in VARIANTI_VENDUTI:
        c = copy.deepcopy(base)
        for k, v in VARIANTI[nome].items():
            setattr(c, k, v)
        c.correzione_modello = False          # l'esame misura il calcolo senza correzione (niente circolo)
        cfgs[nome] = c
    errs = defaultdict(list)
    per_modello = defaultdict(list)
    chiave, mercato, cache = None, [], {}
    for r in rows:
        key = (r["make"], r["model"], r["fuel"])
        if key != chiave:
            chiave = key
            mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                       for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
            cache = {}
        t = dict(r, vehicle_identity=_j(r.get("vehicle_identity")), parts_cost_low=None, parts_cost_high=None,
                 rischio_strutturale=_rischio(r), ai=None, targa_e_telefono=True, seller_type="privato")
        vero = float(r["price_eur"])
        for nome, cfg in cfgs.items():
            try:
                v = valuta_con_ripiego(conn, t, mercato, scovo.soglia(int(vero)), cfg, cache)
            except Exception:
                continue
            R = v.get("R_finale") or v.get("R")
            if not R or (v.get("n") or 0) < cfg.n_minimo:
                continue
            e = (R - vero) / vero * 100
            errs[nome].append(e)
            if nome == "attuale":
                per_modello[(r["make"], r["model"])].append((int(r["id"]), R, vero))
    out = {"casi": len(rows)}
    for nome, es in errs.items():
        a = [abs(e) for e in es]
        out[nome] = {"n": len(es), "medio": round(stx.mean(a), 1), "mediano": round(stx.median(a), 1),
                     "con_segno": round(stx.mean(es), 1), "entro10": round(100 * sum(1 for x in a if x <= 10) / len(a)),
                     "sopra10": round(100 * sum(1 for x in es if x > 10) / len(es))}
    mod = {f"{k[0]} {k[1]}": (len(v), round(stx.mean((R - x) / x * 100 for _, R, x in v), 1))
           for k, v in per_modello.items() if len(v) >= 4}
    out["modelli_peggiori"] = sorted(mod.items(), key=lambda kv: -abs(kv[1][1]))[:10]
    try:
        out["correzione"] = correzioni(conn, per_modello)
    except Exception as e:
        conn.rollback()
        out["correzione"] = {"errore": str(e)[:200]}
    log.warning("ESAME_VENDUTI %s", json.dumps(out, ensure_ascii=False))
    return out


def _fattori(casi: dict) -> dict:
    """casi: {(make, model): [(id, R, prezzo_venduto)]} → {(make, model): {n, errore_pct, fattore}}.
    Obiettivo: R = prezzo di vendita del concessionario meno CORR_PRUDENZA (8%), così la correzione non toglie
    la prudenza ("prezzi bassi per non gonfiare i margini"). Con pochi casi la correzione si riduce
    (n/(n+8)); al massimo +10% in su e −20% in giù; sotto il 2% non si corregge."""
    import os
    import statistics as stx
    prud = float(os.environ.get("CORR_PRUDENZA", "0.08"))
    nmin = int(os.environ.get("CORR_N_MIN", "5"))
    su, giu = float(os.environ.get("CORR_MAX_SU", "0.10")), float(os.environ.get("CORR_MAX_GIU", "0.20"))
    out = {}
    for k, v in casi.items():
        if len(v) < nmin:
            continue
        errs = [(R - x * (1 - prud)) / (x * (1 - prud)) for _, R, x in v]
        e = stx.median(errs)
        f = -e * len(v) / (len(v) + 8)
        f = max(-giu, min(su, f))
        if abs(f) < .02:
            continue
        out[k] = {"n": len(v), "errore_pct": round(e * 100, 1), "fattore": round(f, 3)}
    return out


def correzioni(conn, casi: dict) -> dict:
    """Calcola le correzioni per modello, misura onestamente se servono (si calcolano su metà delle auto vendute
    e si provano sull'altra metà) e le scrive nella tabella correzioni_modello."""
    import statistics as stx
    a = {k: [c for c in v if c[0] % 2 == 0] for k, v in casi.items()}
    b = {k: [c for c in v if c[0] % 2 == 1] for k, v in casi.items()}
    import os
    fa = _fattori(a)
    q = 1 - float(os.environ.get("CORR_PRUDENZA", "0.08"))      # obiettivo: prezzo venduto meno la prudenza
    prima, dopo = [], []
    for k, v in b.items():
        f = fa.get(k, {}).get("fattore", 0.0)
        for _, R, x in v:
            prima.append(abs(R - x * q) / (x * q) * 100)
            dopo.append(abs(R * (1 + f) - x * q) / (x * q) * 100)
    prova = {"auto_prova": len(prima), "errore_prima": round(stx.mean(prima), 1) if prima else None,
             "errore_dopo": round(stx.mean(dopo), 1) if dopo else None}
    tutte = _fattori(casi)
    conn.execute("""CREATE TABLE IF NOT EXISTS correzioni_modello (make TEXT, model TEXT, n INT, errore_pct REAL,
                    fattore REAL, computed_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (make, model))""")
    conn.execute("DELETE FROM correzioni_modello")
    for (mk, md), d in tutte.items():
        conn.execute("INSERT INTO correzioni_modello (make, model, n, errore_pct, fattore) VALUES (%s,%s,%s,%s,%s)",
                     (mk, md, d["n"], d["errore_pct"], d["fattore"]))
    conn.commit()
    return {"modelli_corretti": len(tutte), "prova_su_meta": prova,
            "esempi": sorted(((f"{k[0]} {k[1]}", d["fattore"]) for k, d in tutte.items()), key=lambda z: -abs(z[1]))[:8]}
