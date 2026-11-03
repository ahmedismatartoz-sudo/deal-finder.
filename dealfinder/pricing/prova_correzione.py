"""Prova della correzione anno/km (regole.Correzione.due_sensi) sulle auto VENDUTE: regola di prima (solo verso il
basso, pv6) contro regola nuova (nei due sensi, tetto ±25%). Solo lettura del database (scrive una riga di risultati
in prove_prezzi, migrazione 036).

    python -m dealfinder.pricing.prova_correzione            (database: DATABASE_URL)
    python -m dealfinder.pricing.prova_correzione --sintetica (dati sintetici, senza database)

Auto vendute (studio 1 §2.1): annunci di PRIVATI della zona di casa, spariti (status 'scomparso', mai 'riapparso')
entro 60 giorni dalla comparsa, negli ultimi 60 giorni, sani e senza segnalazioni sul prezzo. Bersaglio: l'ultimo
prezzo chiesto (nel database non c'è il prezzo di vendita concluso).

Per ogni auto si calcola M come mediana delle gemelle corrette (prezzi chiesti, senza valore reale, senza AI e senza
il modello prezzi del gruppo, che corregge già nei due sensi: così si misura solo la correzione) con le due regole, sugli annunci attivi (l'auto stessa e i suoi doppioni esclusi da pulisci).
Metriche, per fascia di prezzo e in totale, per ciascuna regola:
- distorsione = mediana di log(M / prezzo venduto) (negativa = M sotto il vero);
- MdAPE = errore assoluto mediano |M/prezzo − 1|;
- MdAPE_tarato = MdAPE dopo aver tolto la distorsione mediana della fascia: è l'errore che resta dopo la correzione
  chiesto → venduto del sito (regole.calibra_vendita), quindi il confronto giusto tra le due regole;
- entro_10 = quota entro ±10%.
Decisione (scritta nel risultato): la regola nuova è "peggiore" se il suo MdAPE_tarato totale supera quello della
regola di prima di oltre 0,5 punti: in quel caso il default di regole.Correzione.due_sensi va rimesso a False.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import random
import statistics as stx
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import regole as RG
from . import policy_v2 as P

log = logging.getLogger("prova_correzione")

VENDUTE_SQL = """
SELECT {cols}, l.title, l.status, l.first_seen_at, l.disappeared_at FROM listings l
WHERE l.status = 'scomparso' AND COALESCE(l.seller_type, 'privato') <> 'commerciante'
  AND l.disappeared_at > now() - interval '60 days'
  AND l.disappeared_at - l.first_seen_at <= interval '60 days'
  AND NOT EXISTS (SELECT 1 FROM price_events e WHERE e.listing_id = l.id AND e.event = 'riapparso')
  AND l.price_eur BETWEEN 1000 AND 100000 AND l.year IS NOT NULL AND l.mileage_km IS NOT NULL
  AND COALESCE(l.damage_class, 'sconosciuto') IN ('nessuno', 'sconosciuto') AND NOT COALESCE(l.problem_search, false)
  AND NOT COALESCE(l.damage_declared, false) AND COALESCE(l.price_flags, '{{}}'::text[]) = '{{}}'::text[]
  AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL AND {home}
ORDER BY l.make, l.model, l.fuel
LIMIT %(lim)s"""

FASCE = ((5000, "<5k"), (10000, "5-10k"), (20000, "10-20k"), (10**9, ">20k"))


def _fascia(p: float) -> str:
    return next(n for lim, n in FASCE if p < lim)


def _cfg(due_sensi: bool) -> P.PolicyConfig:
    r = RG.regole()
    r = dataclasses.replace(r, correzione=dataclasses.replace(r.correzione, due_sensi=due_sensi),
                            valore_reale=dataclasses.replace(r.valore_reale, attivo=False),
                            ai=dataclasses.replace(r.ai, attiva=False),
                            modello_locale=dataclasses.replace(r.modello_locale, attivo=False))
    return P.PolicyConfig(r)


def confronta(casi: list[tuple[dict, list[dict]]], now=None) -> dict:
    """casi: [(auto venduta, mercato)] → metriche per le due regole. Pura (niente database)."""
    cfgs = {"prima_solo_giu": _cfg(False), "nuova_due_sensi": _cfg(True)}
    res = {k: defaultdict(list) for k in cfgs}
    usate = 0
    for t, mercato in casi:
        vero = float(t["price_eur"])
        stime = {}
        for nome, cfg in cfgs.items():
            v = P.valuta(dict(t, vr=None, ai=None), mercato, 10**9, cfg, now)
            if not v.get("M") or (v.get("n_gemelle") or 0) < cfg.n_minimo:
                break                                   # stesse auto per le due regole
            stime[nome] = math.log(v["M"] / vero)
        if len(stime) != len(cfgs):
            continue
        usate += 1
        for nome, x in stime.items():
            res[nome][_fascia(vero)].append(x)
            res[nome]["tutte"].append(x)
    out = {"auto": len(casi), "usate": usate, "regole": {}}
    for nome, per in res.items():
        m = {}
        for f, xs in per.items():
            if not xs:
                continue
            d = stx.median(xs)
            m[f] = {"n": len(xs), "distorsione_pct": round((math.exp(d) - 1) * 100, 2),
                    "mdape_pct": round(stx.median(abs(math.exp(x) - 1) for x in xs) * 100, 2),
                    "mdape_tarato_pct": round(stx.median(abs(math.exp(x - d) - 1) for x in xs) * 100, 2),
                    "entro_10_pct": round(100 * sum(1 for x in xs if abs(math.exp(x) - 1) <= .10) / len(xs), 1)}
        # MdAPE "tarato" totale: ogni auto corretta con la distorsione della SUA fascia (come fa il sito)
        if per.get("tutte"):
            tarati = [abs(math.exp(x - stx.median(per[f])) - 1) for f, xs in per.items() if f != "tutte" for x in xs]
            m["tutte"]["mdape_tarato_fasce_pct"] = round(stx.median(tarati) * 100, 2)
        out["regole"][nome] = m
    a = out["regole"].get("prima_solo_giu", {}).get("tutte")
    b = out["regole"].get("nuova_due_sensi", {}).get("tutte")
    if a and b:
        peggiore = b["mdape_tarato_fasce_pct"] > a["mdape_tarato_fasce_pct"] + 0.5
        out["decisione"] = ("nuova PEGGIORE: rimettere regole.Correzione.due_sensi=False" if peggiore else
                            "nuova non peggiore: due_sensi=True resta")
        out["nuova_peggiore"] = peggiore
    return out


def esegui(conn, limite: int = 3000) -> dict:
    from ..store import HOME_REGION_SQL
    from .ricalcolo import MARKET_SQL, TARGET_COLS, _j
    rows = conn.execute(VENDUTE_SQL.format(cols=TARGET_COLS, home=HOME_REGION_SQL), {"lim": limite}).fetchall()
    casi, chiave, mercato = [], None, []
    for r in rows:
        key = (r["make"], r["model"], r["fuel"])
        if key != chiave:
            chiave = key
            mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                       for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
        casi.append((dict(r, vehicle_identity=_j(r.get("vehicle_identity"))), mercato))
    out = confronta(casi)
    out["impronta_regole"] = RG.impronta()
    log.warning("PROVA_CORREZIONE %s", json.dumps(out, ensure_ascii=False))
    try:
        conn.execute("INSERT INTO prove_prezzi (prova, impronta, metriche) VALUES (%s,%s,%s)",
                     ("correzione_anno_km", out["impronta_regole"], json.dumps(out)))
        conn.commit()
    except Exception as e:                      # tabella non ancora creata (migrazione 036): solo log
        conn.rollback()
        log.warning("PROVA_CORREZIONE non salvata: %s", str(e)[:120])
    return out


def sintetica(n_gruppi: int = 30, seme: int = 7) -> dict:
    """Dati sintetici (studio 1 §5, G): log p = 9,6 − 0,09·età − 0,10·log(km/1000) + ε (σ 0,05) per gruppo, annunci
    attivi tra 2010 e 2023; le auto vendute costano il 7% in meno a parità di anno e km. Serve a controllare il banco
    (non decide il default: quello lo decide la prova sui dati veri)."""
    rnd = random.Random(seme)
    now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)

    def riga(i, g, anno, km, prezzo, status="attivo"):
        return {"id": i, "url": f"x/{i}", "source": "subito", "make": f"marca{g}", "model": "m", "year": anno,
                "mileage_km": km, "fuel": "benzina", "gearbox": "manuale", "power_kw": 70, "price_eur": int(prezzo),
                "price_flags": [], "status": status, "seller_type": "privato", "damage_class": "sconosciuto",
                "last_checked_at": now - timedelta(days=1), "first_seen_at": now - timedelta(days=20),
                "vehicle_id": i}

    casi, i = [], 0
    for g in range(n_gruppi):
        base = 9.6 + rnd.uniform(-.4, .4)
        prezzo = lambda a, k: math.exp(base - .09 * (2026 - a) - .10 * math.log(k / 1000) + rnd.gauss(0, .05))
        mercato = []
        for _ in range(60):
            a = rnd.randint(2010, 2023)
            k = max(5000, int(rnd.gauss((2026 - a) * 13000, 25000)))
            i += 1
            mercato.append(riga(i, g, a, k, prezzo(a, k)))
        for _ in range(15):
            a = rnd.randint(2011, 2022)
            k = max(5000, int(rnd.gauss((2026 - a) * 13000, 25000)))
            i += 1
            casi.append((riga(i, g, a, k, prezzo(a, k) * .93, status="scomparso"), mercato))
    return confronta(casi, now)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if "--sintetica" in sys.argv:
        print(json.dumps(sintetica(), indent=1, ensure_ascii=False))
    else:
        from ..db import connect
        with connect() as conn:
            print(json.dumps(esegui(conn), indent=1, ensure_ascii=False, default=str))
