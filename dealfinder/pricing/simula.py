"""Simulazione una tantum nel CRON (voce COLLECT_PRIMA "simulamargini", spenta finché non la si aggiunge; mai nel
servizio web: 512 MB): calcolo VECCHIO contro NUOVO sui dati completi del database, SENZA scrivere niente (solo righe
SIMULA_MARGINI nei log; ogni gruppo si chiude con rollback).

Stessa condizione per i due calcoli (controllo del giudice A2): entrambi rifatti ADESSO, sugli stessi annunci, senza
giudice AI.
- vecchio = regole_vecchie(): niente allestimento a livelli, niente rivendita dai commercianti, tasso km anche 0%,
  niente controllo della dispersione, niente ampiezza con 2–4 gemelle, calibrazione a scalini e mai sul valore reale;
- nuovo = regole in vigore (con gli annunci di commercianti del gruppo);
- nuovo senza commercianti = regole in vigore con RIVENDITA_COMMERCIANTI spento: le auto sopra soglia SOLO grazie ai
  commercianti devono essere 0 (R = min(M, R_commercianti)).
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


def regole_vecchie(rg: RG.Regole) -> RG.Regole:
    """Le regole di prima (commit 9bcae2e) per quanto riproducibile con gli interruttori."""
    return dataclasses.replace(
        rg, allestimento=dataclasses.replace(rg.allestimento, attivo=False),
        rivendita=dataclasses.replace(rg.rivendita, attiva=False),
        correzione=dataclasses.replace(rg.correzione, km_min=0.0),
        confronti=dataclasses.replace(rg.confronti, dispersione_max=1e9, ampiezza_poche=False),
        calibrazione=dataclasses.replace(rg.calibrazione, interpola=False))


def _ids_sito(conn) -> list:
    from ..web import scovo
    try:
        return [r["id"] for r in conn.execute("SELECT q.id FROM (" + scovo.LIST_SQL + ") q", {"me": 0}).fetchall()]
    except Exception as e:
        conn.rollback()
        log.warning("SIMULA_MARGINI auto del sito non lette: %s", str(e)[:120])
        return []


def esegui(conn, ids_sito: list | None = None, quante: int = 1500) -> dict:
    import gc
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    from . import policy_v2 as P
    from . import rivendita as RV
    from .ricalcolo import MARKET_SQL, valuta_con_ripiego
    from .valutazione import COLONNE, ingressi, prepara
    t0 = time.time()
    conn.execute("SET statement_timeout = '600s'")
    if ids_sito is None:
        ids_sito = _ids_sito(conn)
    ids = list(dict.fromkeys(list(ids_sito) + [r["listing_id"] for r in conn.execute(SQL_SCELTA, (quante,)).fetchall()]))
    sito = set(ids_sito)
    rg = RG.regole()
    rv_ = regole_vecchie(rg)
    cfg = P.PolicyConfig(rg)
    cfg_v = P.PolicyConfig(rv_)
    cfg_sc = P.PolicyConfig(dataclasses.replace(rg, rivendita=dataclasses.replace(rg.rivendita, attiva=False)))
    righe = conn.execute(f"SELECT {COLONNE} FROM listings l WHERE l.id = ANY(%s) ORDER BY l.make, l.model, l.fuel",
                         (ids,)).fetchall()
    ing = ingressi(conn, ids)
    fat = ing["fattori_calibra"]
    fat_v = [x for x in fat if not str(x[1]).startswith(("vr:", "_"))]   # prima: niente fattori sul valore reale
    conn.rollback()
    gruppo, mercato, comm, cache = None, [], None, {}
    out, per = [], defaultdict(lambda: defaultdict(list))
    solo_comm = 0
    for r in righe:
        r = dict(r)
        try:
            t, motivo = prepara(r, ing, rg)
            if motivo:
                continue
            key = (r["make"], r["model"], r["fuel"])
            if key != gruppo:
                conn.rollback()                             # niente scritture: si chiude il gruppo
                gruppo, cache, mercato, comm = key, {}, None, None
                gc.collect()
                mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                           for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
                comm = RV.carica(conn, *key, HOME_REGION_SQL, rg)
            s = scovo.soglia(int(r["price_eur"]))
            tv = dict(t, fattori_calibra=fat_v)
            vecchio = RG.calibra_vendita(valuta_con_ripiego(conn, tv, mercato, s, cfg_v, cache, giudice=False), fat_v, rv_)
            nuovo = RG.calibra_vendita(valuta_con_ripiego(conn, dict(t, commercianti=comm), mercato, s, cfg, cache,
                                                          giudice=False), fat, rg)
            senza = RG.calibra_vendita(valuta_con_ripiego(conn, dict(t), mercato, s, cfg_sc, cache, giudice=False), fat, rg)
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            log.warning("SIMULA_MARGINI %s errore: %s", r.get("id"), str(e)[:120])
            continue
        p = int(r["price_eur"])
        sopra = {nome: (v.get("categoria") in scovo.CATEGORIE_SITO and v.get("margine") is not None
                        and v["margine"] >= s) for nome, v in (("vecchio", vecchio), ("nuovo", nuovo), ("senza", senza))}
        if sopra["nuovo"] and not sopra["senza"]:
            solo_comm += 1
        rc = nuovo.get("rivendita_commercianti") or {}
        x = {"id": r["id"], "p": p, "sito": r["id"] in sito,
             "v": [vecchio.get("M"), vecchio.get("R_finale") or vecchio.get("R"), vecchio.get("margine"), vecchio.get("categoria")],
             "n": [nuovo.get("M"), nuovo.get("R_finale") or nuovo.get("R"), nuovo.get("margine"), nuovo.get("categoria")],
             "n_senza_comm": [senza.get("R_finale") or senza.get("R"), senza.get("margine")],
             "netto": [vecchio.get("netto"), nuovo.get("netto")],
             "riv": [rc.get("fonte"), rc.get("R"), rc.get("n_venduti"), rc.get("n_attivi"), rc.get("giorni_vendita")],
             "all": (nuovo.get("allestimento") or {}).get("livello"), "disp": nuovo.get("dispersione")}
        out.append(x)
        f = per[_fascia(p)]
        for nome, v in (("vecchio", vecchio), ("nuovo", nuovo)):
            if sopra[nome]:
                f["sopra_" + nome].append(v["margine"])
                f["netto_" + nome].append(v.get("netto"))
        f["riv_comm"].append(1 if rc.get("R") else 0)
        f["riv_abbassata"].append(1 if nuovo.get("R_da") == "commercianti" else 0)
    conn.rollback()
    for k in range(0, len(out), 40):
        log.warning("SIMULA_MARGINI %d/%d %s", k, len(out), json.dumps(out[k:k + 40], ensure_ascii=False, default=str))
    riep = {}
    for _, nome in FASCE:
        f = per.get(nome)
        if not f:
            continue
        nv = [x for x in f["netto_vecchio"] if x is not None]
        nn = [x for x in f["netto_nuovo"] if x is not None]
        riep[nome] = {
            "auto": len(f["riv_comm"]), "con_rivendita_commercianti": sum(f["riv_comm"]),
            "R_abbassata_dai_commercianti": sum(f["riv_abbassata"]),
            "sopra_soglia_prima": len(f["sopra_vecchio"]), "sopra_soglia_dopo": len(f["sopra_nuovo"]),
            "margine_mediano_prima": int(median(f["sopra_vecchio"])) if f["sopra_vecchio"] else None,
            "margine_mediano_dopo": int(median(f["sopra_nuovo"])) if f["sopra_nuovo"] else None,
            "netto_mediano_prima": int(median(nv)) if nv else None,
            "netto_mediano_dopo": int(median(nn)) if nn else None}
    stats = {"auto": len(out), "sul_sito": len(sito), "sopra_soglia_solo_grazie_ai_commercianti": solo_comm,
             "secondi": round(time.time() - t0), "fasce": riep}
    log.warning("SIMULA_MARGINI_RIEPILOGO %s", json.dumps(stats, ensure_ascii=False))
    return stats
