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
