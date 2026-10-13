"""Lista commercianti di Milano e province (per il commerciale di Scovo): autosaloni che pubblicano su
AutoScout24 e Subito, raggruppati per venditore, con i dati PUBBLICI dell'attività presi dai loro annunci
(nome, comune, telefono/sito se presenti), quante auto hanno in vendita e se scrivono che ritirano o
comprano auto da privati. Solo attività (venditori professionali), mai privati. Risultato nei log: LEAD {json}.

    COLLECT_MODO=lead_commercianti
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict

from ..db import connect
from ..zone import MILANO

log = logging.getLogger("lead")

RITIRO = re.compile(r"ritir\w*|compr(o|iamo)\b|acquist(o|iamo)\b|permut\w*|valutazion\w*|pagamento immediato|"
                    r"valutiamo|supervalutaz\w*", re.I)
COMPRO = re.compile(r"^\s*(compro|acquisto|cerco)\b|compro auto|acquisto auto|ritiro auto|ritiriamo", re.I)
CAMPI = re.compile(r"name|company|phone|tel|mobile|url|link|web|address|street|zip|city|shop|id$|type", re.I)


def _piatto(d, pref="", out=None, prof=0):
    """Solo i campi utili del venditore (nome, telefono, indirizzo, sito, id), appiattiti."""
    out = {} if out is None else out
    if prof > 3:
        return out
    if isinstance(d, dict):
        for k, v in d.items():
            _piatto(v, f"{pref}{k}.", out, prof + 1)
    elif isinstance(d, list):
        for i, v in enumerate(d[:3]):
            _piatto(v, f"{pref}{i}.", out, prof + 1)
    elif d not in (None, "", True, False) and pref and CAMPI.search(pref.rstrip(".")):
        out[pref.rstrip(".")] = str(d)[:120]
    return out


def _chiave(source, s: dict) -> str | None:
    for k in ("id", "customerId", "sellerId", "user_id", "userId", "shop_id", "shopId"):
        if s.get(k):
            return f"{source}:{s[k]}"
    for k in ("companyName", "name", "shop_name", "shopName"):
        if s.get(k):
            return f"{source}:n:{str(s[k]).strip().lower()}"
    return None


def run() -> int:
    gruppi: dict = defaultdict(lambda: {"annunci": 0, "ritiro": 0, "prezzi": [], "citta": defaultdict(int),
                                        "provincia": None, "fonte": None, "dati": {}, "esempio": None,
                                        "compro": 0})
    with connect() as conn:
        cur = conn.execute(
            """SELECT source, city, province, price_eur, url, title, left(description, 1500) AS descr,
                      CASE WHEN source='autoscout24' THEN raw->'seller' ELSE raw->'advertiser' END AS s,
                      seller_type
               FROM listings
               WHERE source IN ('autoscout24','subito') AND province = ANY(%s)
                 AND last_seen_at > now() - interval '45 days'
                 AND seller_type = 'commerciante'""",
            (list(MILANO),))
        n = 0
        for r in cur:
            n += 1
            s = r["s"] if isinstance(r["s"], dict) else {}
            k = _chiave(r["source"], s)
            if not k:
                continue
            g = gruppi[k]
            g["annunci"] += 1
            g["fonte"] = r["source"]
            g["provincia"] = g["provincia"] or r["province"]
            if r["city"]:
                g["citta"][r["city"]] += 1
            if r["price_eur"]:
                g["prezzi"].append(r["price_eur"])
            testo = f"{r['title'] or ''} {r['descr'] or ''}"
            if RITIRO.search(testo):
                g["ritiro"] += 1
            if COMPRO.search(r["title"] or ""):
                g["compro"] += 1
            if not g["dati"]:
                g["dati"] = _piatto(s)
            if not g["esempio"]:
                g["esempio"] = r["url"]
    log.info("LEAD_LETTI %d annunci, %d venditori", n, len(gruppi))
    righe = []
    for k, g in gruppi.items():
        p = sorted(g["prezzi"])
        righe.append({"chiave": k, "fonte": g["fonte"], "provincia": g["provincia"],
                      "citta": max(g["citta"], key=g["citta"].get) if g["citta"] else None,
                      "annunci": g["annunci"], "con_ritiro_permuta": g["ritiro"], "annunci_compro": g["compro"],
                      "prezzo_min": p[0] if p else None, "prezzo_med": p[len(p) // 2] if p else None,
                      "prezzo_max": p[-1] if p else None, "dati": g["dati"], "esempio": g["esempio"]})
    # prima chi compra auto dai privati, poi chi ha più auto (più movimento = più acquisti)
    righe.sort(key=lambda x: (-(x["annunci_compro"] > 0), -(x["con_ritiro_permuta"] > 0), -x["annunci"]))
    for x in righe[:1500]:
        log.info("LEAD %s", json.dumps(x, ensure_ascii=False, default=str))
    log.info("LEAD_FINE %d venditori", len(righe))
    return len(righe)
