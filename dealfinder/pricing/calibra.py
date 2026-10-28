"""Misura della precisione della rivendita sulle auto vendute davvero (28/10, cliente: "margini non precisi").

Idea: un'auto sparita entro CALIBRA_GIORNI_VENDITA giorni dalla prima volta che l'abbiamo vista è stata venduta
(o quasi) al prezzo dell'annuncio: quel prezzo è un prezzo a cui il mercato compra davvero.
Per ognuna si rifà la valutazione con il metodo del sito (mercato M, rivendita prudente R, escludendo l'auto stessa)
e si misura, per fascia di prezzo:
  - rapporto prezzo / M  (mediana): se è 0,92 il mercato vero è l'8% sotto la mediana degli annunci;
  - copertura: quota di auto vendute con prezzo >= R (R prudente "giusto" se vicino all'80%);
  - errore di R in %: (R - prezzo) / prezzo, mediana.
Non cambia niente nel database: scrive solo CALIBRA_* nei log.
"""
from __future__ import annotations

import gc
import json
import logging
import os
from collections import defaultdict
from statistics import median

log = logging.getLogger("calibra")

FASCE = [(5000, "<5k"), (10000, "5-10k"), (20000, "10-20k"), (35000, "20-35k"), (60000, "35-60k"), (10**9, ">60k")]


def _fascia(p: float) -> str:
    return next(n for lim, n in FASCE if p < lim)


VENDUTE_SQL = """
SELECT {cols}, l.title, l.description, l.telefono_ok
FROM listings l
WHERE l.status = 'scomparso'
  AND l.disappeared_at > now() - make_interval(days => %(dal)s)
  AND l.disappeared_at - l.first_seen_at <= make_interval(days => %(gg)s)
  AND l.price_eur BETWEEN 1000 AND 400000
  AND coalesce(l.mileage_km, 0) <= 200000
  AND coalesce(l.damage_class, '') NOT IN ('grave', 'alto_rischio', 'medio')
  AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL
  AND {zona}
ORDER BY l.make, l.model, l.fuel
LIMIT %(lim)s"""


def misura(conn) -> dict:
    from . import policy_v2 as P
    from .ricalcolo import MARKET_SQL, NORD_SQL, TARGET_COLS, _j, _rischio, valuta_con_ripiego
    from ..store import HOME_REGION_SQL
    from ..web import scovo

    cols = TARGET_COLS
    zona = HOME_REGION_SQL
    rows = conn.execute(VENDUTE_SQL.format(cols=cols, zona=zona),
                        {"dal": int(os.environ.get("CALIBRA_DAL_GIORNI", "60")),
                         "gg": int(os.environ.get("CALIBRA_GIORNI_VENDITA", "7")),
                         "lim": int(os.environ.get("CALIBRA_MAX", "3000"))}).fetchall()
    cfg = P.PolicyConfig.from_env()
    per = defaultdict(lambda: {"n": 0, "rapp_M": [], "err_R": [], "coperte": 0})
    mercato, chiave, cache = [], None, {}
    tot = 0
    for r in rows:
        key = (r["make"], r["model"], r["fuel"])
        if key != chiave:
            mercato = None
            gc.collect()
            mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                       for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
            chiave = key
        prezzo = float(r["price_eur"])
        t = dict(r, vehicle_identity=_j(r.get("vehicle_identity")), parts_cost_low=None, parts_cost_high=None,
                 rischio_strutturale=_rischio(r), ai=None, targa_e_telefono=True)
        confronti = [m for m in mercato if m.get("id") != r["id"]]
        try:
            v = valuta_con_ripiego(conn, t, confronti, scovo.soglia(int(prezzo)), cfg, cache)
        except Exception as e:
            log.warning("CALIBRA errore %s: %s", r["id"], str(e)[:120])
            continue
        M, R = v.get("M"), v.get("R_finale") or v.get("R")
        if not M or not R or (v.get("n") or 0) < cfg.n_minimo:
            continue
        f = _fascia(prezzo)
        d = per[f]
        d["n"] += 1
        d["rapp_M"].append(prezzo / M)
        d["err_R"].append((R - prezzo) / prezzo * 100)
        d["coperte"] += 1 if prezzo >= R else 0
        tot += 1
    out = {"vendute_lette": len(rows), "valutate": tot, "fasce": {}}
    for lim, f in FASCE:
        d = per.get(f)
        if not d or not d["n"]:
            continue
        out["fasce"][f] = {"n": d["n"],
                           "prezzo_su_M": round(median(d["rapp_M"]), 3),
                           "errore_R_pct": round(median(d["err_R"]), 1),
                           "copertura_R": round(d["coperte"] / d["n"], 2)}
    log.warning("CALIBRA_RISULTATO %s", json.dumps(out, ensure_ascii=False))
    return out
