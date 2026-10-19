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


QUALSIASI = re.compile(r"incidentat\w*|motore fus\w*|\bfus[ei]\b|qualsiasi (condizion\w*|stato|tipo)|non marciant\w*|"
                       r"guast\w*|rott[ae]\b|export|km alti|alto chilometraggio|senza revisione|fermo amministrativo|"
                       r"grandinat\w*|sinistrat\w*", re.I)
TEL = re.compile(r"(?:\+39[\s.]?)?(?:3\d{2}|0\d{1,3})[\s./-]?\d{3,4}[\s./-]?\d{3,4}")


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
        q = QUALSIASI.search(testo)
        n_si += bool(m)
        frase = ""
        for x in (q, m):
            if x:
                a, b = max(0, x.start() - 80), x.end() + 80
                frase = " ".join(testo[a:b].split())[:220]
                break
        log.info("LEADP %s", json.dumps({"fonte": "autoscout24", "nome": d["nome"], "citta": d["citta"],
                                         "url": d["url"], "compra": bool(m), "qualsiasi": bool(q), "frase": frase,
                                         "tel": d.get("tel") or [], "annunci": d.get("annunci")},
                                        ensure_ascii=False))
        time.sleep(0.8)
    log.info("LEADP_AS %d su %d autosaloni", n_si, len(lista))
    sub = SubitoCollector()
    visti = set()
    for q in ("compro auto", "acquisto auto", "ritiro auto", "compriamo auto", "acquistiamo auto",
              "compro auto incidentate", "compro auto fuse", "acquisto auto qualsiasi condizione",
              "compro auto export", "ritiro auto incidentate", "compro auto non marcianti", "compro furgoni"):
        for page in range(5):
            try:
                r = sub.client.get(API_SEARCH, headers=API_HEADERS,
                                   params={"c": 2, "r": 4, "ci": 8, "t": "s", "q": q, "lim": 100, "start": page * 100})
                ads = (r.json() or {}).get("ads") or [] if r.status_code == 200 else []
            except Exception:
                ads = []
            for it in ads:
                adv = it.get("advertiser") or {}
                testo = f"{it.get('subject') or ''} {it.get('body') or ''}"
                # chi pubblica "compro auto" lo fa per lavoro anche se il profilo è da privato
                if not (adv.get("company") is True or str(adv.get("type")) == "1" or COMPRA.search(testo)):
                    continue
                k = adv.get("user_id") or adv.get("shop_id") or adv.get("name")
                if k in visti:
                    continue
                visti.add(k)
                geo = it.get("geo") or {}
                citta = ((geo.get("town") or {}).get("value") if isinstance(geo.get("town"), dict) else None)
                log.info("LEADP %s", json.dumps({"fonte": "subito", "nome": adv.get("shop_name") or adv.get("name"),
                                                 "citta": citta, "url": (it.get("urls") or {}).get("default"),
                                                 "compra": True, "qualsiasi": bool(QUALSIASI.search(testo)),
                                                 "azienda": adv.get("company") is True,
                                                 "frase": (it.get("subject") or "")[:200],
                                                 "tel": sorted(set(TEL.findall(testo)))[:2]}, ensure_ascii=False))
            time.sleep(1.0)
            if len(ads) < 100:
                break
    log.info("LEADP_FINE autoscout %d, subito %d", n_si, len(visti))
    return n_si + len(visti)


def compro_auto() -> int:
    """Chi compra auto di ogni tipo in provincia di Milano e Monza-Brianza:
    1) annunci Subito "cerco" (t=k) nella categoria auto, province MI e MB: chi scrive "compro auto ...";
    2) annunci di vendita Subito MI/MB con parole "compro/acquisto auto ... incidentate/fuse/qualsiasi";
    3) pagine AutoScout24 degli autosaloni di Monza e Brianza (data/lead_mb.json).
    Nei log: LEADC {json}."""
    import time
    from pathlib import Path
    from html import unescape
    from ..collectors.autoscout import AutoScoutCollector
    from ..collectors.subito import SubitoCollector, API_SEARCH, API_HEADERS
    sub = SubitoCollector()
    visti = set()
    n = 0

    def scrivi(it, origine):
        nonlocal n
        adv = it.get("advertiser") or {}
        testo = f"{it.get('subject') or ''}\n{it.get('body') or ''}"
        k = it.get("urn") or (it.get("urls") or {}).get("default")
        if k in visti:
            return
        visti.add(k)
        geo = it.get("geo") or {}
        town = geo.get("town") if isinstance(geo.get("town"), dict) else {}
        city = geo.get("city") if isinstance(geo.get("city"), dict) else {}
        n += 1
        log.info("LEADC %s", json.dumps({
            "origine": origine, "nome": adv.get("shop_name") or adv.get("name"), "azienda": adv.get("company") is True,
            "utente": adv.get("user_id"), "comune": town.get("value"), "provincia": city.get("short_name") or city.get("value"),
            "titolo": (it.get("subject") or "")[:150], "testo": " ".join((it.get("body") or "").split())[:500],
            "tel": sorted(set(TEL.findall(testo)))[:3], "url": (it.get("urls") or {}).get("default"),
            "qualsiasi": bool(QUALSIASI.search(testo)), "compra": bool(COMPRA.search(testo) or re.search(r"\bcompro\b", testo, re.I)),
            "data": it.get("dates", {}).get("display") if isinstance(it.get("dates"), dict) else None},
            ensure_ascii=False))

    for ci in (8, 9):                                   # 8 = Milano, 9 = Monza e Brianza
        for tipo, q in (("k", None), ("k", "compro"), ("s", "compro auto"), ("s", "acquisto auto"),
                        ("s", "ritiro auto"), ("s", "auto incidentate compro"), ("s", "compro auto fuse")):
            for page in range(10):
                params = {"c": 2, "r": 4, "ci": ci, "t": tipo, "lim": 100, "start": page * 100, "sort": "datedesc"}
                if q:
                    params["q"] = q
                try:
                    r = sub.client.get(API_SEARCH, headers=API_HEADERS, params=params)
                    ads = ((r.json() or {}).get("ads") or []) if r.status_code == 200 else []
                except Exception:
                    ads = []
                log.info("LEADC_PAGINA ci=%s t=%s q=%s pagina=%s annunci=%d", ci, tipo, q, page, len(ads))
                for it in ads:
                    testo = f"{it.get('subject') or ''} {it.get('body') or ''}"
                    if tipo == "k" or re.search(r"\b(compro|compriamo|acquisto|acquistiamo|ritiro|ritiriamo)\b", it.get("subject") or "", re.I):
                        scrivi(it, f"subito_{'cerco' if tipo == 'k' else 'vendo'}")
                time.sleep(1.0)
                if len(ads) < 100:
                    break
    lista = json.loads((Path(__file__).resolve().parents[2] / "data" / "lead_mb.json").read_text())
    asc = AutoScoutCollector()
    for d in lista:
        try:
            r = asc.client.get(d["url"])
            testo = unescape(re.sub(r"<[^>]+>", " ", r.text)) if r.status_code == 200 else ""
        except Exception:
            testo = ""
        m, qq = COMPRA.search(testo), QUALSIASI.search(testo)
        frase = ""
        for x in (qq, m):
            if x:
                a, b = max(0, x.start() - 80), x.end() + 80
                frase = " ".join(testo[a:b].split())[:220]
                break
        log.info("LEADC %s", json.dumps({"origine": "autoscout24", "nome": d["nome"], "comune": d["citta"], "provincia": "MB",
                                         "url": d["url"], "compra": bool(m), "qualsiasi": bool(qq), "testo": frase,
                                         "tel": d.get("tel") or [], "annunci": d.get("annunci")}, ensure_ascii=False))
        time.sleep(0.8)
    log.info("LEADC_FINE subito %d, autoscout MB %d", n, len(lista))
    return n


# ---------------------------------------------------------------- 19/10: nuovi lead "compriamo la tua auto"
PROV_DINTORNI = ["MI", "MB", "VA", "CO", "LC", "BG", "LO", "PV", "CR", "NO"]
CI_SUBITO = {"BG": 1, "CO": 3, "CR": 4, "LC": 5, "LO": 6, "MI": 8, "MB": 9, "PV": 10, "VA": 12}


def _h(s: str) -> str:
    import hashlib
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _nu(u):
    u = (u or "").strip().lower().split("?")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", u)


def _nn(n):
    return re.sub(r"[^a-z0-9]", "", (n or "").lower())


def _tel9(t):
    d = re.sub(r"\D", "", str(t or ""))
    if d.startswith("39") and len(d) > 10:
        d = d[2:]
    return d[-9:] if len(d) >= 8 else None


def _cellulari(tels) -> list:
    """Solo numeri di cellulare italiani (3xx), mai fissi (19/10, cliente)."""
    out = []
    for t in tels or []:
        d = re.sub(r"\D", "", str(t))
        if d.startswith("39") and len(d) > 10:
            d = d[2:]
        if d.startswith("3") and 9 <= len(d) <= 10 and d not in out:
            out.append(d)
    return out


def compra_dintorni() -> int:
    """Commercianti di Milano e dintorni che DICHIARANO di comprare auto dai privati (pagina AutoScout24 o annunci
    Subito "compro auto"), esclusi quelli già consegnati (data/lead_esclusi.json, solo hash). Nei log: LEADN {json}.
    Solo attività: nome dell'attività, comune, telefoni pubblicati dall'attività, link, la frase in cui lo dichiarano."""
    import time
    from pathlib import Path
    from html import unescape
    from ..collectors.autoscout import AutoScoutCollector
    from ..collectors.subito import SubitoCollector, API_SEARCH, API_HEADERS
    es = json.loads((Path(__file__).resolve().parents[2] / "data" / "lead_esclusi.json").read_text())
    EU, ET, EN = set(es["url"]), set(es["tel"]), set(es["nome"])

    def gia(nome, url, tels):
        return (_h(_nn(nome)) in EN if nome else False) or (url and _h(_nu(url)) in EU) or \
            any(t and _h(t) in ET for t in (_tel9(x) for x in tels))

    visti_n, visti_t = set(), set()

    def nuovo(nome, tels):
        k = _nn(nome)
        ts = {t for t in (_tel9(x) for x in tels) if t}
        if (k and k in visti_n) or (ts & visti_t):
            return False
        visti_n.add(k)
        visti_t.update(ts)
        return True

    # autosaloni dalle ricerche AutoScout24 (concessionari entro LEAD_RAGGIO km da Milano): il DB non tiene i dati del venditore
    import os
    os.environ["AUTOSCOUT_CAP"], os.environ["AUTOSCOUT_RAGGIO"] = "20121", os.environ.get("LEAD_RAGGIO", "70")
    os.environ["AUTOSCOUT_DANNEGGIATE"] = "include"
    os.environ.pop("AUTOSCOUT_KM_MAX", None)
    os.environ.pop("PASSI_JOB", None)
    ricerca = AutoScoutCollector()
    ricerca.min_delay_s, ricerca.max_delay_s = 0.6, 1.4
    massimo = int(os.environ.get("LEAD_MAX_AUTOSALONI", "3500"))
    dealers: dict = {}
    n_ann = 0
    for l in ricerca.search({"massiva": True, "min_price": 300, "max_price": 80000, "step": 2500,
                             "venditori": [("D", "commerciante")]}):
        n_ann += 1
        s = (l.raw or {}).get("seller") or {}
        if not isinstance(s, dict) or (l.province and l.province.upper() not in PROV_DINTORNI):
            continue
        links = s.get("links") if isinstance(s.get("links"), dict) else {}
        url = (links.get("infoPage") or "").split("?")[0]
        nome = s.get("companyName")
        if not url or not nome:
            continue
        d = dealers.setdefault(url, {"nome": nome, "citta": l.city, "prov": l.province, "n": 0,
                                     "tel": [p.get("formattedNumber") for p in (s.get("phones") or [])
                                             if isinstance(p, dict) and p.get("formattedNumber")]})
        d["n"] += 1
        if n_ann % 2000 == 0:
            log.warning("LEADN_RICERCA avanzamento: %d annunci, %d autosaloni in zona", n_ann, len(dealers))
        if len(dealers) >= massimo:
            ricerca.stop = True
            break
    log.warning("LEADN_RICERCA %d annunci letti", n_ann)
    log.warning("LEADN_AS %d autosaloni in zona da controllare", len(dealers))
    asc = AutoScoutCollector()
    trovati, controllati, esclusi = 0, 0, 0
    for url, d in sorted(dealers.items(), key=lambda kv: -kv[1]["n"]):
        if gia(d["nome"], url, d["tel"]):
            esclusi += 1
            continue
        try:
            r = asc.client.get(url)
            testo = unescape(re.sub(r"<[^>]+>", " ", r.text)) if r.status_code == 200 else ""
        except Exception:
            testo = ""
        controllati += 1
        if controllati % 100 == 0:
            log.warning("LEADN_AS avanzamento: %d pagine controllate, %d trovati", controllati, trovati)
        m = COMPRA.search(testo)
        cell = _cellulari(d["tel"])
        if cell and nuovo(d["nome"], d["tel"]):
            q = QUALSIASI.search(testo)
            frase = ""
            if m:
                a, b = max(0, m.start() - 90), m.end() + 90
                frase = " ".join(testo[a:b].split())[:240]
            trovati += 1
            log.warning("LEADN %s", json.dumps({"fonte": "autoscout24", "nome": d["nome"], "comune": d["citta"], "prov": d["prov"],
                                             "cell": cell[:2], "url": url, "annunci": d["n"], "dichiara": bool(m),
                                             "qualsiasi": bool(q), "frase": frase}, ensure_ascii=False))
        time.sleep(0.9)
    log.warning("LEADN_AS_FINE controllati %d, già consegnati %d, trovati %d", controllati, esclusi, trovati)
    sub = SubitoCollector()
    n_sub = 0
    for prov, ci in CI_SUBITO.items():
        for tipo, q in (("k", None), ("k", "compro"), ("s", "compro auto"), ("s", "acquisto auto"), ("s", "ritiro auto"),
                        ("s", "compriamo auto"), ("s", "acquistiamo auto"), ("s", "compro auto incidentate"),
                        ("s", "compro auto fuse"), ("s", "compro auto qualsiasi condizione"), ("s", "valutazione gratuita auto")):
            for page in range(6):
                params = {"c": 2, "r": 4, "ci": ci, "t": tipo, "lim": 100, "start": page * 100, "sort": "datedesc"}
                if q:
                    params["q"] = q
                try:
                    r = sub.client.get(API_SEARCH, headers=API_HEADERS, params=params)
                    ads = ((r.json() or {}).get("ads") or []) if r.status_code == 200 else []
                except Exception:
                    ads = []
                for it in ads:
                    adv = it.get("advertiser") or {}
                    sogg = it.get("subject") or ""
                    testo = f"{sogg}\n{it.get('body') or ''}"
                    # deve dire nel TITOLO che compra auto (non "cerco Ferrari 458"), e il testo deve essere da commerciante
                    if not re.search(r"\b(compro|compriamo|acquisto|acquistiamo|ritiro|ritiriamo)\b[^.\n]{0,25}\b(auto|veicol|macchin|vettur|usat)", sogg, re.I):
                        continue
                    if not (adv.get("company") is True or COMPRA.search(testo) or
                            re.search(r"pagamento (immediato|in contanti)|contanti|ritiro a domicilio|passaggio (di proprietà )?(a nostro carico|gratuito|incluso)|qualsiasi (marca|condizione|stato)", testo, re.I)):
                        continue
                    nome = adv.get("shop_name") or adv.get("name") or ""
                    tels = sorted(set(TEL.findall(testo)))[:3]
                    if not _cellulari(tels):
                        continue
                    link = (it.get("urls") or {}).get("default")
                    if gia(nome, link, tels) or not nuovo(nome or link, tels):
                        continue
                    geo = it.get("geo") or {}
                    town = geo.get("town") if isinstance(geo.get("town"), dict) else {}
                    n_sub += 1
                    log.warning("LEADN %s", json.dumps({"fonte": "subito", "nome": nome, "azienda": adv.get("company") is True,
                                                     "comune": town.get("value"), "prov": prov, "cell": _cellulari(tels)[:2], "url": link, "dichiara": True,
                                                     "qualsiasi": bool(QUALSIASI.search(testo)), "frase": sogg[:200]},
                                                    ensure_ascii=False))
                time.sleep(1.0)
                if len(ads) < 100:
                    break
    log.warning("LEADN_FINE autoscout %d, subito %d", trovati, n_sub)
    return trovati + n_sub
