"""Misura della precisione della rivendita sulle auto vendute davvero (28/10, cliente: "margini non precisi").

Idea: un'auto sparita entro CALIBRA_GIORNI_VENDITA giorni dalla prima volta che l'abbiamo vista è stata venduta
(o quasi) al prezzo dell'annuncio: quel prezzo è un prezzo a cui il mercato compra davvero.
Per ognuna si rifà la valutazione con il metodo del sito (mercato M, rivendita prudente R, escludendo l'auto stessa)
e si misura, per fascia di prezzo:
  - rapporto prezzo / M  (mediana): se è 0,92 il mercato vero è l'8% sotto la mediana degli annunci;
  - copertura: quota di auto vendute con prezzo >= R (R prudente "giusto" se vicino all'80%);
  - errore di R in %: (R - prezzo) / prezzo, mediana.
Non cambia niente nel database: scrive solo CALIBRA_* nei log.

17/11 notte (cliente: "margini molto precisi"): campione casuale della popolazione del sito (non più le prime vendute in
ordine alfabetico di marca), M misurato solo quando viene dai prezzi chiesti, misura del valore reale su prezzo / V
salvato (fasce_vr, prima quasi vuota) e diagnosi per età, carburante e fonte. Vedi VENDUTE_SQL e VENDUTE_VR_SQL.
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


# 17/11 notte (cliente: "margini molto precisi"): il campione era "ORDER BY marca LIMIT CALIBRA_MAX" (in produzione
# 600): le PRIME 600 vendute in ordine alfabetico di marca (abarth, alfa, audi, bmw, ... mai fiat, volkswagen, renault,
# toyota), ogni volta le stesse, e con auto che il sito non valuta mai (prima del 2007, oltre 150.000 km, vendute da
# commercianti, che chiedono di più dei privati e alzano il rapporto prezzo/M). Ora: campione CASUALE (md5 dell'id,
# stabile tra un giro e l'altro) della popolazione del sito (anno >= ammissione.anno_min, km <= ammissione.km_max,
# privati); l'ordine per marca/modello resta solo per rileggere il mercato una volta per gruppo.
VENDUTE_SQL = """
WITH c AS (
  SELECT l.id FROM listings l
  WHERE l.status = 'scomparso'
    AND l.disappeared_at > now() - make_interval(days => %(dal)s)
    AND l.disappeared_at - l.first_seen_at <= make_interval(days => %(gg)s)
    AND l.price_eur BETWEEN 1000 AND 400000
    AND l.mileage_km BETWEEN 0 AND %(km_max)s
    AND l.year >= %(anno_min)s
    AND l.seller_type = 'privato'
    AND coalesce(l.damage_class, '') NOT IN ('grave', 'alto_rischio', 'medio')
    AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL
    AND {zona}
  ORDER BY md5(l.id::text)
  LIMIT %(lim)s)
SELECT {cols}, l.title, l.description, l.telefono_ok, l.first_seen_at, l.disappeared_at
FROM c JOIN listings l ON l.id = c.id
ORDER BY l.make, l.model, l.fuel"""

# 17/11 notte: misura chiesto → venduto del VALORE REALE (voci "vr:" di regole.fattori_calibrazione). Prima si contava
# solo un'auto venduta che, rivalutata, avesse metodo "valore_reale" puro (in produzione 9 auto in tutto, nessuna fascia
# con le 20 necessarie): il valore reale, metodo principale del sito (2/3 delle auto pubblicate), non veniva MAI
# corretto, mentre la prova del valore reale (VALORE_REALE PROVA del 16/11, 2.338 auto vendute di privati) lo misura
# in media il 7% SOPRA il prezzo finale. Ora: prezzo finale / V salvato quando l'auto era ancora online (tabella
# valore_reale, scritta solo per annunci attivi: nessun dato dal futuro), stesse vendute e stessa popolazione della
# misura sui prezzi chiesti, per fascia di V. Il fattore si applica a V sia con l'accordo sia nel combinato
# (policy_v2, regole.calibra_vendita) ed è sempre tra calibrazione.minimo e 1,0: può solo abbassare.
VENDUTE_VR_SQL = """
SELECT l.price_eur, v.v, v.p25, l.year, l.fuel, l.source
FROM listings l JOIN valore_reale v ON v.listing_id = l.id
WHERE l.status = 'scomparso'
  AND l.disappeared_at > now() - make_interval(days => %(dal)s)
  AND l.disappeared_at - l.first_seen_at <= make_interval(days => %(gg)s)
  AND v.computed_at <= l.disappeared_at
  AND l.price_eur BETWEEN 1000 AND 400000
  AND l.mileage_km BETWEEN 0 AND %(km_max)s
  AND l.year >= %(anno_min)s
  AND l.seller_type = 'privato'
  AND coalesce(l.damage_class, '') NOT IN ('grave', 'alto_rischio', 'medio')
  AND v.v > 0
  AND {zona}
ORDER BY md5(l.id::text)
LIMIT %(lim)s"""


def parametri_campione(rg=None) -> dict:
    """Parametri della popolazione misurata: quella che il sito valuta (regole.ammissione, NON cambiate qui)."""
    from .. import regole as RG
    a = (rg or RG.regole()).ammissione
    return {"dal": int(os.environ.get("CALIBRA_DAL_GIORNI", "60")),
            "gg": int(os.environ.get("CALIBRA_GIORNI_VENDITA", "7")),
            "lim": int(os.environ.get("CALIBRA_MAX", "3000")),
            "km_max": int(a.km_max), "anno_min": int(a.anno_min)}


def _vuota():
    return {"n": 0, "rapp_M": [], "err_R": [], "coperte": 0}


def aggiungi(d: dict, prezzo: float, M: float, R: float) -> None:
    d["n"] += 1
    d["rapp_M"].append(prezzo / M)
    d["err_R"].append((R - prezzo) / prezzo * 100)
    d["coperte"] += 1 if prezzo >= R else 0


def riassumi(dati: dict, ordine: list) -> dict:
    """{fascia: {n, prezzo_su_M (mediana), errore_R_pct, copertura_R, q}} nell'ordine dato."""
    out = {}
    for f in ordine:
        d = dati.get(f)
        if not d or not d["n"]:
            continue
        out[f] = {"n": d["n"], "prezzo_su_M": round(median(d["rapp_M"]), 3),
                  "errore_R_pct": round(median(d["err_R"]), 1),
                  "copertura_R": round(d["coperte"] / d["n"], 2),
                  "q": quantili(d["rapp_M"])}
    return out


def fascia_eta(anno, ora: int | None = None) -> str:
    from datetime import datetime
    e = (ora or datetime.now().year) - int(anno or 0)
    return "0-3" if e <= 3 else "4-8" if e <= 8 else "9-12" if e <= 12 else "13+"


def misura_vr(conn, par: dict, zona: str) -> tuple[dict, dict]:
    """(fasce_vr, tutte): prezzo finale / V (valore reale salvato quando l'auto era online), per fascia di V, sulle
    vendute della popolazione del sito. Lim proprio (CALIBRA_MAX_VR, la lettura è leggera: niente rivalutazione).
    In fasce_vr (quelle che il sito applica) vanno SOLO le fasce con uno scostamento dimostrato verso il basso
    (precisione.fattore_gruppo: almeno calibrazione.n_min auto, test dei segni p < 0,05, V sopra il prezzo finale):
    senza prova il valore reale, che è già un prezzo di vendita, non si tocca. `tutte` = la misura completa (diagnosi)."""
    from .. import regole as RG
    from .precisione import fattore_gruppo
    cal = RG.regole().calibrazione
    righe = conn.execute(VENDUTE_VR_SQL.format(zona=zona),
                         dict(par, lim=int(os.environ.get("CALIBRA_MAX_VR", "5000")))).fetchall()
    per = defaultdict(_vuota)
    coppie = defaultdict(list)
    for r in righe:
        V, prezzo = float(r["v"] or 0), float(r["price_eur"] or 0)
        if V <= 0 or prezzo <= 0:
            continue
        aggiungi(per[_fascia(V)], prezzo, V, V)        # rivendita del sito con il valore reale = V (prima dei costi)
        coppie[_fascia(V)].append((V, prezzo))
    tutte = riassumi(per, [n for _, n in FASCE])
    applicate = {}
    for f, d in tutte.items():
        fg = fattore_gruppo(coppie[f], n_min=cal.n_min, minimo=cal.minimo)
        d["prova"] = {k: fg.get(k) for k in ("p", "stime_sopra", "stime_sotto", "applica", "motivo")}
        if fg["applica"]:
            applicate[f] = d
    return applicate, tutte


def quantili(valori: list) -> list | None:
    """Quantili 10/25/50/75/90 (tipo 7) di prezzo/M, arrotondati a 3 decimali."""
    from .policy_v2 import percentile
    if len(valori) < 5:
        return None
    return [round(percentile(valori, q), 3) for q in (.10, .25, .50, .75, .90)]


# Tassi anno/km per marca (punto 3): regressione del log-prezzo su anno e km CENTRATI dentro ogni gruppo marca/modello/
# carburante (così conta solo come cambia il prezzo dentro lo stesso modello), con le somme calcolate nel database.
TASSI_SQL = """
WITH a AS (
  SELECT make, model, fuel, ln(price_eur::float) AS lp, year::float AS y, mileage_km / 10000.0 AS k
  FROM listings
  WHERE status = 'attivo' AND price_eur BETWEEN 1000 AND 200000 AND year >= 2005
    AND mileage_km BETWEEN 1000 AND 300000 AND make IS NOT NULL AND model IS NOT NULL AND fuel IS NOT NULL
    AND {zona}),
c AS (
  SELECT make, lp - avg(lp) OVER w AS lp, y - avg(y) OVER w AS y, k - avg(k) OVER w AS k, count(*) OVER w AS n
  FROM a WINDOW w AS (PARTITION BY make, model, fuel))
SELECT make, count(*) AS n, sum(y * y) AS syy, sum(k * k) AS skk, sum(y * k) AS syk, sum(y * lp) AS syl,
       sum(k * lp) AS skl
FROM c WHERE n >= 5 GROUP BY make"""


def tassi_da_somme(n, syy, skk, syk, syl, skl) -> dict | None:
    """Risolve la regressione a due variabili dalle somme: {"anno", "km_10000", "n"} oppure None."""
    det = float(syy) * float(skk) - float(syk) ** 2
    if not n or det <= 0:
        return None
    b1 = (float(syl) * float(skk) - float(skl) * float(syk)) / det
    b2 = (float(skl) * float(syy) - float(syl) * float(syk)) / det
    import math
    return {"anno": round(1 - math.exp(-b1), 4), "km_10000": round(1 - math.exp(b2), 4), "n": int(n)}


def misura_tassi(conn) -> dict:
    """{"marche": {marca: {"anno", "km_10000", "n"}}, "tutti": {...}} sulla zona di casa."""
    from ..store import HOME_REGION_SQL
    righe = conn.execute(TASSI_SQL.format(zona=HOME_REGION_SQL)).fetchall()
    marche, tot = {}, [0, 0.0, 0.0, 0.0, 0.0, 0.0]
    for r in righe:
        v = [r["n"], r["syy"], r["skk"], r["syk"], r["syl"], r["skl"]]
        t = tassi_da_somme(*v)
        if t and r["make"]:
            marche[str(r["make"]).lower()] = t
        tot = [a + float(b or 0) for a, b in zip(tot, v)]
    return {"marche": marche, "tutti": tassi_da_somme(*tot)}


def misura(conn) -> dict:
    from . import policy_v2 as P
    from .ricalcolo import MARKET_SQL, TARGET_COLS, _j, _rischio, _valuta_base
    from ..store import HOME_REGION_SQL
    from ..web import scovo

    cols = TARGET_COLS
    zona = HOME_REGION_SQL
    par = parametri_campione()
    rows = conn.execute(VENDUTE_SQL.format(cols=cols, zona=zona), par).fetchall()
    cfg = P.PolicyConfig.from_env()
    # 12/11 (rapporto 2 E2/E4): fasce di M (come nell'applicazione, regole.fattore_per) e non di prezzo: scegliere la
    # fascia sul prezzo (la variabile misurata) esagera la sovrastima.
    per = defaultdict(_vuota)
    per_prezzo = defaultdict(_vuota)
    # 17/11 notte: le stesse misure per età, carburante e fonte (solo da leggere, non usate dai fattori): dicono dove M
    # sbaglia ancora prima di decidere una correzione
    per_eta, per_carb, per_fonte = defaultdict(_vuota), defaultdict(_vuota), defaultdict(_vuota)
    try:
        from .valutazione import ingressi
        _ing = ingressi(conn, [], solo=("calibra",))
        fattori = _ing.get("fattori_calibra")
        priori = _ing.get("tassi_misurati")            # 12/11 sera: la misura parte con il pooling se i tassi ci sono
    except Exception:
        fattori, priori = None, None
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
                 rischio_strutturale=_rischio(r), ai=None, targa_e_telefono=True, fattori_calibra=fattori,
                 tassi_priori=priori)
        confronti = [m for m in mercato if m.get("id") != r["id"]]
        try:
            v, _ = _valuta_base(conn, t, confronti, scovo.soglia(int(prezzo)), cfg, cache)
        except Exception as e:
            log.warning("CALIBRA errore %s: %s", r["id"], str(e)[:120])
            continue
        M, R = v.get("M"), v.get("R_finale") or v.get("R")
        # solo M dai prezzi CHIESTI: è l'unico a cui si applicano questi fattori (regole.da_calibrare)
        if not M or not R or (v.get("n") or 0) < cfg.n_minimo or v.get("M_fonte") not in (None, "annunci"):
            continue
        for d in (per[_fascia(M)], per_prezzo[_fascia(prezzo)], per_eta[fascia_eta(r.get("year"))],
                  per_carb[str(r.get("fuel") or "?")], per_fonte[str(r.get("source") or "?")]):
            aggiungi(d, prezzo, M, R)
        tot += 1
    try:
        fasce_vr, fasce_vr_tutte = misura_vr(conn, par, zona)
        # 17/11 notte: la misura del valore reale si APPLICA (voci "vr:" del sito) solo con CALIBRA_VR_APPLICA=1.
        # Motivo: le "vendute" qui sono le sparite entro 7 giorni, cioè soprattutto le auto messe a prezzo basso, e il
        # rapporto prezzo/V esce più basso del vero (la prova alla cieca del 15/11, sparite entro 30 giorni a caso, non
        # trova scostamenti dimostrati tra 5.000 e 35.000 €); intanto V riguarda 2/3 delle auto del sito e con margini
        # vicini alla soglia un -3% su V ne toglierebbe ~56 su 196 (DUMP_CANDIDATI del 17/11). Senza l'interruttore
        # la misura resta in diagnosi.valore_reale e il sito resta com'era (nessuna fascia vr misurata su 20 auto).
        if os.environ.get("CALIBRA_VR_APPLICA") != "1":
            fasce_vr = {}
    except Exception as e:
        conn.rollback()
        fasce_vr, fasce_vr_tutte = {}, {}
        log.warning("CALIBRA valore reale non misurato: %s", str(e)[:150])
    # 4/11: con quale correzione anno/km è stata misurata (il fattore cambia se cambia la regola: va rimisurato)
    rg = cfg.regole
    ordine = [n for _, n in FASCE]
    out = {"vendute_lette": len(rows), "valutate": tot, "fasce": riassumi(per, ordine), "fasce_su": "M",
           "fasce_vr": fasce_vr, "fasce_prezzo": riassumi(per_prezzo, ordine),
           "diagnosi": {"valore_reale": fasce_vr_tutte, "eta": riassumi(per_eta, ["0-3", "4-8", "9-12", "13+"]),
                        "carburante": riassumi(per_carb, sorted(per_carb)),
                        "fonte": riassumi(per_fonte, sorted(per_fonte))},
           "campione": {"tipo": "casuale_popolazione_sito", "anno_min": par["anno_min"], "km_max": par["km_max"],
                        "venditore": "privato", "giorni_vendita": par["gg"], "dal_giorni": par["dal"]},
           "correzione_due_sensi": bool(cfg.due_sensi),
           # 12/11 sera (controllo del giudice M2): con quali regole è stata misurata (regole.calibrazione_vecchia)
           "tassi_pooling": bool(rg.correzione.pooling and priori),
           "correzione_due_sensi_alto": bool(rg.correzione.due_sensi_alto)}
    # 12/11 sera (punti 3 e 4): tassi anno/km per marca e di tutti (pooling), premi di 4x4 e optional. Calcolati nel
    # database (somme per gruppo), mai le righe in memoria; un errore non ferma la misura principale.
    try:
        out["tassi"] = misura_tassi(conn)
    except Exception as e:
        conn.rollback()
        log.warning("CALIBRA tassi non misurati: %s", str(e)[:150])
    try:
        from . import dotazioni as DT
        d = DT.misura(conn)
        if d:
            out["dotazioni"] = d
    except Exception as e:
        conn.rollback()
        log.warning("CALIBRA dotazioni non misurate: %s", str(e)[:150])
    log.warning("CALIBRA_RISULTATO %s", json.dumps(out, ensure_ascii=False))
    try:      # 28/10 sera: il sito corregge il mercato con questi valori (vedi app._mercato_calibrato)
        conn.execute("CREATE TABLE IF NOT EXISTS calibrazione (at TIMESTAMPTZ PRIMARY KEY DEFAULT now(), dati JSONB)")
        conn.execute("INSERT INTO calibrazione (dati) VALUES (%s)", (json.dumps(out),))
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.warning("CALIBRA non salvata: %s", str(e)[:120])
    return out
