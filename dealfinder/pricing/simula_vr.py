"""3/11 (cliente: "più auto, ma margini veri"): simulazione, SENZA scrivere niente, di quante auto di privati della zona
passerebbero le regole del sito con tre varianti della rivendita quando il mercato è il valore reale (prezzo di vendita):

A  regole attuali: rivendita = min(mercato −5%, P25 del valore reale, vendita veloce)
B  senza il tetto P25 del valore reale (la P25 era pensata per i prezzi CHIESTI: sui prezzi di vendita si somma due volte)
C  come B e senza il "prezzo per vendere in fretta" quando il mercato è il valore reale

Il sito poi applica le sue correzioni (calibrazione dei prezzi chiesti, minimo confronti): qui sono rifatte allo stesso
modo per avere numeri confrontabili. Solo conteggi ed esempi nei log (SIMVR)."""
from __future__ import annotations

import dataclasses
import json
import logging
import os
from collections import Counter

log = logging.getLogger("scovo")


def _passa_sito(v: dict, t: dict, fattori: list, soglia_fn) -> tuple[bool, int | None]:
    """Margine come lo vede il sito (app._mercato_calibrato) e se l'auto entra."""
    from ..web import scovo
    if v.get("categoria") not in scovo.CATEGORIE_SITO or v.get("margine") is None or not v.get("M"):
        return False, v.get("margine")
    prezzo = int(t["price_eur"])
    n = v.get("n") or 0
    if prezzo < 10000 and n < 8 and not v.get("prudente_pochi") and not v.get("vr_poche_gemelle"):
        return False, v.get("margine")
    M = float(v["M"])
    f = next(x for lim, _, x in fattori if M < lim)
    if str(v.get("metodo") or "").startswith("valore_reale"):
        f = 1.0
    if f < 1 and "benzin" in str(t.get("fuel") or "").lower() and (t.get("mileage_km") or 0) <= 100000:
        f = 1 - (1 - f) * 0.5
    R = float(v.get("R_finale") or v.get("R") or 0)
    R2 = min(R, M * f * 0.95) if f < 1 else R
    m = int(v["margine"] - (R - R2))
    return m >= max(soglia_fn(prezzo), 1500 if prezzo > 4000 else 800), m


def simula(conn, fattori: list, limite: int = 5000) -> dict:
    from ..web import scovo
    from ..zone import tutte
    from . import policy_v2 as P
    from .ricalcolo import (MARKET_SQL, TARGET_COLS, _j, _rischio, _valuta_base, ai_di, carica_taratura, vr_di)
    from ..store import HOME_REGION_SQL
    rows = conn.execute(f"""SELECT {TARGET_COLS}, l.title, l.description, l.telefono_ok, l.damage_declared
        FROM listings l JOIN valore_reale vr ON vr.listing_id = l.id
        JOIN LATERAL (SELECT margine FROM valutazioni_policy u WHERE u.listing_id = l.id ORDER BY computed_at DESC LIMIT 1) u ON true
        WHERE l.status='attivo' AND l.seller_type='privato' AND upper(l.province) = ANY(%s)
          AND l.price_eur BETWEEN 1000 AND 20000 AND COALESCE(l.mileage_km,0) <= 150000
          AND vr.computed_at > now() - interval '2 days' AND COALESCE(u.margine, -99999) >= -1500
          AND COALESCE(l.damage_declared, false) = false
        LIMIT %s""", (list(tutte()), limite)).fetchall()
    rows = sorted(rows, key=lambda r: (r["make"] or "", r["model"] or "", r["fuel"] or ""))
    carica_taratura(conn)
    aimap = ai_di(conn, [r["id"] for r in rows])
    vrmap = vr_di(conn, [r["id"] for r in rows])
    cfg = P.PolicyConfig.from_env()
    cfg_c = dataclasses.replace(cfg, vr_veloce=False)
    log.warning("SIMVR regole in uso: %s", json.dumps({k: getattr(cfg, k) for k in (
        "usa_L", "fasce", "adattiva", "rid_buona", "rid_precisa", "vendita_veloce", "q_veloce", "veloce_max",
        "veloce_min", "veloce_max_incerto", "vr_adattiva", "vr_veloce")}, default=str))
    st, esempi = Counter(), []
    gruppo, mercato, cache = None, [], {}
    for r in rows:
        if not (r["make"] and r["model"] and r["fuel"]) or r["id"] not in vrmap:
            continue
        key = (r["make"], r["model"], r["fuel"])
        if key != gruppo:
            gruppo, cache = key, {}
            mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                       for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
        base = dict(r, vehicle_identity=_j(r.get("vehicle_identity")), parts_cost_low=None, parts_cost_high=None,
                    rischio_strutturale=_rischio(r), ai=aimap.get(r["id"]), targa_e_telefono=bool(r.get("telefono_ok")))
        soglia = scovo.soglia(int(r["price_eur"]))
        vr = vrmap[r["id"]]
        try:
            # senza il giudice AI (nessun costo): stesse valutazioni del calcolo, confronti del Nord compresi
            va = _valuta_base(conn, dict(base, vr=vr), mercato, soglia, cfg, cache)[0]
            vb = _valuta_base(conn, dict(base, vr=dict(vr, p25=10**9)), mercato, soglia, cfg, cache)[0]
            # C: come B e senza vendita veloce quando il mercato è il valore reale (cfg.vr_veloce=False)
            vc = _valuta_base(conn, dict(base, vr=dict(vr, p25=10**9)), mercato, soglia, cfg_c, cache)[0]
        except Exception as e:
            st["errore"] += 1
            if st["errore"] <= 3:
                log.warning("SIMVR %s: %s", r["id"], str(e)[:150])
            continue
        st["auto"] += 1
        fascia = "<5k" if r["price_eur"] < 5000 else "5-10k" if r["price_eur"] < 10000 else "10-20k"
        b100 = "benzin" in str(r["fuel"] or "").lower() and (r["mileage_km"] or 0) <= 100000
        esiti = {}
        for nome, v in (("A", va), ("B", vb), ("C", vc)):
            ok, m = _passa_sito(v, r, fattori, scovo.soglia)
            esiti[nome] = (ok, m, v)
            if ok:
                st[f"{nome}_passano"] += 1
                st[f"{nome}_{fascia}"] += 1
                st[f"{nome}_benzina_100k"] += b100
        if esiti["C"][0] and not esiti["A"][0] and len(esempi) < 12:
            esempi.append({"id": r["id"], "auto": f"{r['make']} {r['model']} {r.get('year')} {r.get('mileage_km')}km",
                           "prezzo": r["price_eur"], "V": vr.get("v"), "P25": vr.get("p25"),
                           "M_gemelle": va.get("M_gemelle"), "n": va.get("n"),
                           "R": {k: e[2].get("R_finale") for k, e in esiti.items()},
                           "margine_sito": {k: e[1] for k, e in esiti.items()}})
        if st["auto"] % 500 == 0:
            log.warning("SIMVR avanzamento %d/%d: %s", st["auto"], len(rows), json.dumps(dict(st)))
    out = {"lette": len(rows), **dict(st)}
    log.warning("SIMVR FINE %s", json.dumps(out, ensure_ascii=False))
    for e in esempi:
        log.warning("SIMVR ESEMPIO %s", json.dumps(e, ensure_ascii=False, default=str))
    return out
