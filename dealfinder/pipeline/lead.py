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
                      (SELECT array_agg(k) FROM jsonb_object_keys(raw) k) AS chiavi,
                      seller_type
               FROM listings
               WHERE source IN ('autoscout24','subito') AND province = ANY(%s)
                 AND last_seen_at > now() - interval '45 days'
                 AND seller_type = 'commerciante'""",
            (list(MILANO),))
        n = 0
        visti_debug: dict = {}
        for r in cur:
            n += 1
            if visti_debug.get(r["source"], 0) < 3:
                visti_debug[r["source"]] = visti_debug.get(r["source"], 0) + 1
                log.info("LEAD_DEBUG %s chiavi=%s venditore=%s", r["source"], r["chiavi"],
                         json.dumps(r["s"], ensure_ascii=False, default=str)[:700])
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


TIENI = re.compile(r"company|name|phone|callto|number|street|zip|city|url|link|web|id$|type", re.I)
NO_PERSONA = re.compile(r"contact|person|first|last|surname|nome_|owner", re.I)


def dal_sito() -> int:
    """Gli annunci salvati non hanno più i dati del venditore: si leggono direttamente da AutoScout24
    (solo concessionari/autosaloni entro LEAD_RAGGIO km da Milano). Dati PUBBLICI dell'attività: ragione
    sociale, telefono dell'attività, indirizzo, sito; mai il nome di una persona."""
    import os
    from ..collectors.autoscout import AutoScoutCollector
    os.environ["AUTOSCOUT_CAP"], os.environ["AUTOSCOUT_RAGGIO"] = "20121", os.environ.get("LEAD_RAGGIO", "60")
    os.environ["AUTOSCOUT_DANNEGGIATE"] = "include"
    os.environ.pop("AUTOSCOUT_KM_MAX", None)
    os.environ.pop("PASSI_JOB", None)
    asc = AutoScoutCollector()
    asc.min_delay_s, asc.max_delay_s = 0.6, 1.4
    massimo = int(os.environ.get("LEAD_MAX", "900"))
    g: dict = {}
    n = 0
    stampati = 0
    for l in asc.search({"massiva": True, "min_price": 500, "max_price": 60000, "step": 2500,
                         "venditori": [("D", "commerciante")]}):
        n += 1
        s = (l.raw or {}).get("seller") or {}
        if n <= 3:
            log.info("LEAD_DEBUG venditore=%s", json.dumps(s, ensure_ascii=False, default=str)[:900])
        k = _chiave("as", s) if isinstance(s, dict) else None
        if not k:
            continue
        if k not in g:
            dati = {kk: vv for kk, vv in _piatto(s).items() if TIENI.search(kk) and not NO_PERSONA.search(kk)}
            g[k] = {"chiave": k, "dati": dati, "citta": l.city, "provincia": l.province, "annunci": 0,
                    "prezzi": [], "ritiro": 0, "esempio": l.url}
        x = g[k]
        x["annunci"] += 1
        if l.price_eur:
            x["prezzi"].append(l.price_eur)
        if RITIRO.search(f"{l.title or ''} {l.description or ''}"):
            x["ritiro"] += 1
        if len(g) >= massimo and n % 200 == 0:
            asc.stop = True
            break
    log.info("LEAD_LETTI %d annunci, %d autosaloni", n, len(g))
    righe = sorted(g.values(), key=lambda x: -x["annunci"])
    for x in righe:
        p = sorted(x.pop("prezzi"))
        x.update(prezzo_min=p[0] if p else None, prezzo_med=p[len(p) // 2] if p else None,
                 prezzo_max=p[-1] if p else None)
        log.info("LEAD %s", json.dumps(x, ensure_ascii=False, default=str))
        stampati += 1
    log.info("LEAD_FINE %d autosaloni", stampati)
    return stampati


COMPRA = re.compile(r"compriamo|acquistiamo|ritiriamo|compro auto|acquisto auto|ritiro auto|acquisto (del )?(vostr|tu)\w* (auto|veicol)|"
                    r"compr\w* la tua auto|vendi la tua auto|valutazion\w* (gratuit|immediat|in giornata)|"
                    r"pagamento (immediato|in contanti|contanti)|ritiro (del(l')? )?usato|auto in conto vendita|conto vendita", re.I)


def compra_da_privati() -> int:
    """Autosaloni della provincia di Milano (data/lead_mi.json) che scrivono sulla loro pagina AutoScout24 che
    comprano/ritirano auto dai privati; poi annunci "compro auto" di attività su Subito, provincia di Milano.
    Nei log: LEADP {json}."""
    import time
    from pathlib import Path
    from html import unescape
    from ..collectors.autoscout import AutoScoutCollector
    from ..collectors.subito import SubitoCollector, API_SEARCH, API_HEADERS
    lista = json.loads((Path(__file__).resolve().parents[2] / "data" / "lead_mi.json").read_text())
    asc = AutoScoutCollector()
    n_si = 0
    for d in lista:
        try:
            r = asc.client.get(d["url"])
            testo = unescape(re.sub(r"<[^>]+>", " ", r.text)) if r.status_code == 200 else ""
        except Exception:
            testo = ""
        m = COMPRA.search(testo)
        tel = re.findall(r"\+39[\s\d\-]{8,16}", testo)
        if m:
            n_si += 1
            a, b = max(0, m.start() - 80), m.end() + 80
            log.info("LEADP %s", json.dumps({"fonte": "autoscout24", "nome": d["nome"], "citta": d["citta"],
                                             "url": d["url"], "frase": " ".join(testo[a:b].split())[:220],
                                             "tel": sorted(set(t.strip() for t in tel))[:3]}, ensure_ascii=False))
        time.sleep(0.8)
    log.info("LEADP_AS %d su %d autosaloni", n_si, len(lista))
    sub = SubitoCollector()
    visti = set()
    for q in ("compro auto", "acquisto auto", "ritiro auto", "compriamo auto", "acquistiamo auto"):
        for page in range(5):
            try:
                r = sub.client.get(API_SEARCH, headers=API_HEADERS,
                                   params={"c": 2, "r": 4, "ci": 8, "t": "s", "q": q, "lim": 100, "start": page * 100})
                ads = (r.json() or {}).get("ads") or [] if r.status_code == 200 else []
            except Exception:
                ads = []
            for it in ads:
                adv = it.get("advertiser") or {}
                if not (adv.get("company") is True or str(adv.get("type")) == "1"):
                    continue
                k = adv.get("user_id") or adv.get("shop_id") or adv.get("name")
                if k in visti:
                    continue
                visti.add(k)
                geo = it.get("geo") or {}
                citta = ((geo.get("town") or {}).get("value") if isinstance(geo.get("town"), dict) else None)
                log.info("LEADP %s", json.dumps({"fonte": "subito", "nome": adv.get("shop_name") or adv.get("name"),
                                                 "citta": citta, "url": (it.get("urls") or {}).get("default"),
                                                 "frase": (it.get("subject") or "")[:200]}, ensure_ascii=False))
            time.sleep(1.0)
            if len(ads) < 100:
                break
    log.info("LEADP_FINE autoscout %d, subito %d", n_si, len(visti))
    return n_si + len(visti)
