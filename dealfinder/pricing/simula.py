"""Simulazione una tantum sul server (BREVO_IMPORT_LISTE=simula_margini): calcolo VECCHIO (l'ultima valutazione
salvata, fatta con il codice di prima) contro NUOVO (rifatta ora con il codice nuovo, con i confronti e gli annunci
dei commercianti del database), SENZA scrivere niente nelle valutazioni: solo righe SIMULA_MARGINI nei log.

Per ogni auto: M, R, margine, categoria e netto vecchi e nuovi, origine della rivendita (commercianti venduti / attivi
/ come prima), allestimento. Il giudice AI non fa chiamate nuove (solo giudizi già in archivio, come le misure).
Riepilogo per fascia di prezzo: auto sopra la soglia prima e dopo, margine mediano, netto mediano.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from collections import defaultdict
from statistics import median

from .. import regole as RG

log = logging.getLogger("simula")

FASCE = ((5000, "<5k"), (7000, "5-7k"), (10000, "7-10k"), (15000, "10-15k"), (20000, "15-20k"), (30000, "20-30k"),
         (10**9, ">30k"))

SQL_SALVATE = """SELECT DISTINCT ON (listing_id) listing_id, riassunto FROM valutazioni_policy
   WHERE listing_id = ANY(%s) ORDER BY listing_id, computed_at DESC"""
SQL_SCELTA = """SELECT listing_id FROM valutazioni_policy
   WHERE computed_at > now() - interval '3 days' AND categoria IN ('opportunita','candidato','non_conveniente')
     AND margine > 0 ORDER BY computed_at DESC LIMIT %s"""


def _fascia(p) -> str:
    return next(n for lim, n in FASCE if int(p or 0) < lim)


def _j(v):
    return json.loads(v) if isinstance(v, str) else (v or {})


def esegui(conn, ids_sito: list | None = None, quante: int = 1500) -> dict:
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    from . import netto as NT
    from . import policy_v2 as P
    from . import rivendita as RV
    from .ricalcolo import MARKET_SQL, valuta_con_ripiego
    from .valutazione import COLONNE, ingressi, prepara
    t0 = time.time()
    conn.execute("SET statement_timeout = '600s'")
    ids = list(dict.fromkeys(list(ids_sito or []) + [r["listing_id"] for r in conn.execute(SQL_SCELTA, (quante,)).fetchall()]))
    sito = set(ids_sito or [])
    vecchie = {r["listing_id"]: _j(r["riassunto"]) for r in conn.execute(SQL_SALVATE, (ids,)).fetchall()}
    rg = RG.regole()
    cfg = P.PolicyConfig(rg)
    cfg_senza_comm = P.PolicyConfig(dataclasses.replace(rg, rivendita=dataclasses.replace(rg.rivendita, attiva=False)))
    righe = conn.execute(f"SELECT {COLONNE} FROM listings l WHERE l.id = ANY(%s) ORDER BY l.make, l.model, l.fuel",
                         (ids,)).fetchall()
    ing = ingressi(conn, ids)
    fat = ing["fattori_calibra"]
    gruppo, mercato, comm, cache = None, [], None, {}
    out, per = [], defaultdict(lambda: defaultdict(list))
    for r in righe:
        r = dict(r)
        try:
            t, motivo = prepara(r, ing, rg)
            if motivo:
                continue
            key = (r["make"], r["model"], r["fuel"])
            if key != gruppo:
                gruppo, cache = key, {}
                mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                           for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
                comm = RV.carica(conn, *key, HOME_REGION_SQL, rg)
            t["commercianti"] = comm
            s = scovo.soglia(int(r["price_eur"]))
            nuovo = RG.calibra_vendita(valuta_con_ripiego(conn, t, mercato, s, cfg, cache, giudice="esame"), fat)
            senza = RG.calibra_vendita(valuta_con_ripiego(conn, t, mercato, s, cfg_senza_comm, cache, giudice="esame"), fat)
            vecchio = RG.calibra_vendita(vecchie.get(r["id"]) or {}, fat)
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            log.warning("SIMULA_MARGINI %s errore: %s", r.get("id"), str(e)[:120])
            continue
        p = int(r["price_eur"])
        rc = nuovo.get("rivendita_commercianti") or {}
        nv = NT.calcola(vecchio.get("R_finale") or vecchio.get("R"), p, vecchio.get("totale_costi") or 0)
        x = {"id": r["id"], "p": p, "sito": r["id"] in sito,
             "v": [vecchio.get("M"), vecchio.get("R_finale") or vecchio.get("R"), vecchio.get("margine"), vecchio.get("categoria")],
             "n": [nuovo.get("M"), nuovo.get("R_finale") or nuovo.get("R"), nuovo.get("margine"), nuovo.get("categoria")],
             "n_senza_comm": [senza.get("R_finale") or senza.get("R"), senza.get("margine")],
             "netto": [nv["netto"] if nv else None, nuovo.get("netto")],
             "riv": [rc.get("fonte"), rc.get("n_venduti"), rc.get("n_attivi"), rc.get("sconto"), rc.get("giorni_vendita")],
             "all": (nuovo.get("allestimento") or {}).get("livello"), "disp": nuovo.get("dispersione")}
        out.append(x)
        f = per[_fascia(p)]
        for nome, v in (("vecchio", vecchio), ("nuovo", nuovo)):
            ok = v.get("categoria") in scovo.CATEGORIE_SITO and v.get("margine") is not None and v["margine"] >= s
            if ok:
                f["sopra_" + nome].append(v["margine"])
                f["netto_" + nome].append((nv or {}).get("netto") if nome == "vecchio" else v.get("netto"))
        f["riv_comm"].append(1 if rc.get("R") else 0)
    for k in range(0, len(out), 40):
        log.warning("SIMULA_MARGINI %d/%d %s", k, len(out), json.dumps(out[k:k + 40], ensure_ascii=False, default=str))
    riep = {}
    for _, nome in FASCE:
        f = per.get(nome)
        if not f:
            continue
        riep[nome] = {
            "auto": len(f["riv_comm"]), "con_rivendita_commercianti": sum(f["riv_comm"]),
            "sopra_soglia_prima": len(f["sopra_vecchio"]), "sopra_soglia_dopo": len(f["sopra_nuovo"]),
            "margine_mediano_prima": int(median(f["sopra_vecchio"])) if f["sopra_vecchio"] else None,
            "margine_mediano_dopo": int(median(f["sopra_nuovo"])) if f["sopra_nuovo"] else None,
            "netto_mediano_prima": int(median([x for x in f["netto_vecchio"] if x is not None]))
            if any(x is not None for x in f["netto_vecchio"]) else None,
            "netto_mediano_dopo": int(median([x for x in f["netto_nuovo"] if x is not None]))
            if any(x is not None for x in f["netto_nuovo"]) else None}
    stats = {"auto": len(out), "sul_sito": len(sito), "secondi": round(time.time() - t0), "fasce": riep}
    log.warning("SIMULA_MARGINI_RIEPILOGO %s", json.dumps(stats, ensure_ascii=False))
    return stats
