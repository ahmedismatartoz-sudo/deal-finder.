"""16/11 pomeriggio (cliente): "raccogliamo TUTTI i commercianti nei 60 km di Milano, da AutoScout24 e da Subito,
con email e numeri di telefono, da capo, massimo tre ore".

Lavoro una tantum (BREVO_IMPORT_LISTE=raccolta60:<etichetta>, web/app.py). Tre fili in parallelo, fino a una
scadenza (R60_ORE, 2,6 ore), poi l'unione e la consegna:
  A) AutoScout24: l'elenco "concessionari a <comune>" di OGNI comune entro 60 km (tutti quelli di MI e MB, gli altri
     da data/comuni_60km.json), poi per ogni salone la home e "chi siamo": nome, cellulari, fissi, email, sito.
  B) Subito: tutti gli annunci auto di attività (advertiser.company) delle province della zona, per fasce di prezzo:
     nome del negozio, comune, numeri scritti nel testo dell'annuncio.
  C) email: per chi ha un sito ma nessuna email, le pagine del sito (home, contatti, chi siamo, privacy).
Unione: un salone per pagina AutoScout24 o per negozio Subito; doppioni uniti per nome (normalizzato) e per
cellulare. Si aggiungono anche i commercianti già nel database (lead_tutti_dealer + lead_email) della zona, così
non si perde nessuno di quelli trovati nei giri vecchi.
Consegna: righe "R60" in lead_consegne (/api/admin/lead.csv?tipo=R60) e, per poterle leggere senza l'area
amministratore, copie in modelli Brevo NON attivi ("R60 dati k/n", testo tra <pre>), che non mandano email.
Solo attività, mai privati. Nei log solo conteggi.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
import unicodedata
from html import unescape
from pathlib import Path

log = logging.getLogger("lead")

BASE_AS = "https://www.autoscout24.it"
NON_SALONE = {"regioni", "cerca", "search", "lst", "informazioni", "recensioni", "chi-siamo"}
REGIONE = {"NO": "piemonte", "VC": "piemonte", "AL": "piemonte", "PC": "emilia-romagna"}
PAGINE_SITO = ["", "contatti", "contatti/", "contact", "chi-siamo", "contattaci", "dove-siamo", "privacy", "privacy-policy"]
BANDE = [(0, 3000), (3000, 5000), (5000, 7000), (7000, 9000), (9000, 11000), (11000, 13000), (13000, 15000),
         (15000, 18000), (18000, 22000), (22000, 27000), (27000, 35000), (35000, 50000), (50000, 400000)]


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def nn(n: str) -> str:
    n = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower()
    n = re.sub(r"\b(s\.?r\.?l\.?s?|s\.?n\.?c\.?|s\.?a\.?s\.?|s\.?p\.?a\.?|di|e|&|auto|automobili|cars?)\b", " ", n)
    return re.sub(r"[^a-z0-9]", "", n)


def testo(html: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")))


def comuni_zona() -> dict:
    """{slug: (nome, prov, km)}: tutti i comuni di MI e MB (km 0 se non noti) e quelli entro 60 km dell'elenco."""
    base = Path(__file__).resolve().parents[2] / "data"
    out: dict = {}
    for k, v in json.loads((base / "comuni_60km.json").read_text()).items():
        if float(v.get("km") or 99) <= 60:
            out[slug(v["comune"])] = (v["comune"], v["pv"], float(v["km"]))
    for nome, pv in json.loads((base / "comuni_zona_lead.json").read_text()):
        if pv in ("MI", "MB") and slug(nome) not in out:
            out[slug(nome)] = (nome, pv, None)
    for nome, pv in json.loads((base / "comuni_sesto.json").read_text()):
        if pv in ("MI", "MB") and slug(nome) not in out:
            out[slug(nome)] = (nome, pv, None)
    return out


def dati_pagina(html: str) -> dict:
    """Nome, cellulari, fissi, email e sito da una pagina di un salone (AutoScout24 o sito proprio)."""
    from .lead import TEL
    from .lead_comuni import cell_validi, fissi
    from .lead_email import _email, _sito
    t = testo(html)
    return {"cell": cell_validi(TEL.findall(t)), "fissi": fissi(t)[:3], "email": _email(html or ""),
            "sito": _sito(html or "", "")}


def unisci(saloni: list[dict]) -> list[dict]:
    """Doppioni uniti: stesso cellulare o stesso nome normalizzato nello stesso comune/provincia. Funzione pura."""
    per_cell: dict = {}
    per_nome: dict = {}
    out: list[dict] = []
    for s in saloni:
        k_nome = (nn(s.get("nome")), (s.get("prov") or "").upper()) if nn(s.get("nome")) else None
        g = None
        for c in s.get("cell") or []:
            if c in per_cell:
                g = per_cell[c]
                break
        if g is None and k_nome and k_nome in per_nome:
            g = per_nome[k_nome]
        if g is None:
            g = {"nome": s.get("nome"), "comune": s.get("comune"), "prov": s.get("prov"), "km": s.get("km"),
                 "cell": [], "fissi": [], "email": [], "sito": None, "fonti": set(), "link_autoscout": None,
                 "link_subito": None}
            out.append(g)
        for f in ("cell", "fissi", "email"):
            g[f] += [x for x in (s.get(f) or []) if x and x not in g[f]]
        g["sito"] = g["sito"] or s.get("sito")
        g["fonti"] |= set(s.get("fonti") or [])
        for f in ("link_autoscout", "link_subito", "comune", "km"):
            g[f] = g[f] or s.get(f)
        for c in g["cell"]:
            per_cell[c] = g
        if k_nome:
            per_nome[k_nome] = g
    return out


class Raccolta:
    def __init__(self, scadenza: float):
        self.scadenza = scadenza
        self.lock = threading.Lock()
        self.saloni: dict = {}            # chiave -> dict
        self.siti: queue.Queue = queue.Queue()
        self.st = {"comuni": 0, "pagine_elenco": 0, "saloni_autoscout": 0, "pagine_saloni": 0,
                   "annunci_autoscout": 0, "pagine_annunci_as": 0, "annunci_subito": 0, "negozi_subito": 0, "siti_letti": 0, "email_da_siti": 0, "errori": 0,
                   "http_autoscout": {}, "http_subito": {}}
        self.zona = comuni_zona()

    def tempo(self) -> bool:
        return time.time() < self.scadenza

    def metti(self, chiave: str, d: dict) -> None:
        with self.lock:
            g = self.saloni.setdefault(chiave, {"cell": [], "fissi": [], "email": [], "fonti": set()})
            for f in ("cell", "fissi", "email"):
                g[f] += [x for x in (d.get(f) or []) if x and x not in g[f]]
            g["fonti"] |= set(d.get("fonti") or [])
            for f, v in d.items():
                if f not in ("cell", "fissi", "email", "fonti") and v and not g.get(f):
                    g[f] = v

    # ---- A) AutoScout24 ----
    def autoscout(self) -> None:
        from collections import Counter
        from ..collectors.autoscout import AutoScoutCollector
        asc = AutoScoutCollector()
        asc.min_delay_s = float(os.environ.get("R60_PAUSA_MIN", "0.45"))
        asc.max_delay_s = float(os.environ.get("R60_PAUSA_MAX", "0.9"))
        get = asc.client.get
        http = Counter()
        slugs: dict = {}
        for i, (sl_c, (nome, pv, km)) in enumerate(sorted(self.zona.items(), key=lambda x: (x[1][2] or 0))):
            if not self.tempo():
                break
            self.st["comuni"] += 1
            for pag in range(1, 40):
                url = f"{BASE_AS}/concessionari/regioni/{REGIONE.get(pv, 'lombardia')}/{sl_c}/" + (f"?page={pag}" if pag > 1 else "")
                try:
                    r = get(url)
                except Exception:
                    self.st["errori"] += 1
                    break
                asc.pause()
                http[r.status_code] += 1
                if r.status_code == 429 or r.status_code == 403:
                    time.sleep(60)
                    break
                if r.status_code != 200:
                    break
                self.st["pagine_elenco"] += 1
                nuovi = 0
                for s in dict.fromkeys(re.findall(r'href="(?:https://www\.autoscout24\.it)?/concessionari/([a-z0-9][a-z0-9-]+)/?["?#]', r.text)):
                    if s in NON_SALONE or s in slugs:
                        continue
                    slugs[s] = (nome, pv, km)
                    nuovi += 1
                if not nuovi:
                    break
            if (i + 1) % 50 == 0:
                log.warning("R60 AutoScout24 elenchi: %d/%d comuni, %d saloni, risposte %s", i + 1, len(self.zona),
                            len(slugs), dict(http))
        self.st["saloni_autoscout"] = len(slugs)
        for j, (s, (comune, pv, km)) in enumerate(slugs.items()):
            if not self.tempo():
                break
            d = {"cell": [], "fissi": [], "email": [], "sito": None}
            nome = s
            for u in (f"{BASE_AS}/concessionari/{s}", f"{BASE_AS}/concessionari/{s}/chi-siamo"):
                try:
                    r = get(u)
                    asc.pause()
                except Exception:
                    self.st["errori"] += 1
                    continue
                http[r.status_code] += 1
                if r.status_code != 200:
                    continue
                self.st["pagine_saloni"] += 1
                if u.endswith(s):
                    m = re.search(r"<title>(.*?)</title>", r.text or "", re.S)
                    if m:
                        nome = re.split(r"\s+[-|–]\s+", unescape(m.group(1)).strip())[0]
                p = dati_pagina(r.text)
                for f in ("cell", "fissi", "email"):
                    d[f] += [x for x in p[f] if x not in d[f]]
                d["sito"] = d["sito"] or p["sito"]
            self.metti(f"{BASE_AS}/concessionari/{s}", {**d, "nome": nome[:150], "comune": comune, "prov": pv, "km": km,
                                                        "fonti": {"autoscout24"}, "link_autoscout": f"{BASE_AS}/concessionari/{s}"})
            if d["sito"] and not d["email"]:
                self.siti.put(f"{BASE_AS}/concessionari/{s}")
            if (j + 1) % 200 == 0:
                log.warning("R60 AutoScout24 saloni: %d/%d, risposte %s", j + 1, len(slugs), dict(http))
        self.st["http_autoscout"] = dict(http)

    # ---- A2) AutoScout24 dagli ANNUNCI (16/11 sera) ----
    # Le pagine "concessionari a <comune>" mostrano solo ~10 saloni "ottimi": a Milano ce ne sono centinaia. Gli
    # annunci dei concessionari entro 60 km (custtype=D, zipr=60), per fasce di prezzo/anno sotto il tetto di 400,
    # danno OGNI salone che vende su AutoScout24, con ragione sociale, comune e telefoni del salone.
    def _as_url(self, lo: int, hi: int, page: int, years) -> str:
        anni = (f"&fregfrom={years[0]}" + (f"&fregto={years[1]}" if years[1] else "")) if years else ""
        return (f"{BASE_AS}/lst?atype=C&cy=I&ustate=N%2CU&damaged_listing=include&custtype=D"
                f"&zip={os.environ.get('R60_CAP', '20121')}&zipr={os.environ.get('R60_RAGGIO', '60')}"
                f"&pricefrom={lo}&priceto={hi}{anni}&sort=age&desc=1&page={page}")

    def _as_items(self, items) -> None:
        from ..collectors.autoscout import province_from_cap
        from .lead import TEL
        from .lead_comuni import cell_validi, fissi
        for it in items or []:
            self.st["annunci_autoscout"] += 1
            s = (it or {}).get("seller") or {}
            if not isinstance(s, dict) or str(s.get("type") or "").lower().startswith("priv") or not s.get("id"):
                continue
            d = s.get("dealer") if isinstance(s.get("dealer"), dict) else {}
            nome = (d.get("companyName") or s.get("companyName") or "").strip()
            tel = " ; ".join(str(v) for p in (s.get("phones") or []) if isinstance(p, dict) for v in p.values()
                             if isinstance(v, str))
            loc = it.get("location") or {}
            citta = str(loc.get("city") or "")
            z = self.zona.get(slug(citta))
            pv = z[1] if z else province_from_cap(loc.get("zip"))
            url = it.get("url")
            self.metti(f"as:{s['id']}", {"nome": nome[:150] or None, "comune": (z[0] if z else citta) or None,
                                         "prov": pv, "km": z[2] if z else None, "cell": cell_validi(TEL.findall(tel)),
                                         "fissi": fissi(tel)[:3], "fonti": {"autoscout24"},
                                         "link_autoscout": (BASE_AS + url) if isinstance(url, str) and url.startswith("/") else None})

    def _as_fascia(self, asc, lo: int, hi: int, years=None) -> None:
        if not self.tempo() or asc.blocked:
            return
        items, total, ok = asc._page(self._as_url(lo, hi, 1, years))
        self.st["pagine_annunci_as"] += 1
        if not ok:
            return
        if total is not None and total > 400:
            pezzi = asc._dividi(lo, hi, years)
            if pezzi:
                for a, b, ys in pezzi:
                    self._as_fascia(asc, a, b, ys)
                return
        self._as_items(items)
        page, last = 1, items
        while len(last) >= 20 and page < 20 and self.tempo() and not asc.blocked:
            page += 1
            last, _, ok = asc._page(self._as_url(lo, hi, page, years))
            self.st["pagine_annunci_as"] += 1
            if not ok:
                return
            self._as_items(last)

    def autoscout_annunci(self) -> None:
        from ..collectors.autoscout import AutoScoutCollector
        asc = AutoScoutCollector()
        asc.min_delay_s = float(os.environ.get("R60_PAUSA_MIN", "0.45"))
        asc.max_delay_s = float(os.environ.get("R60_PAUSA_MAX", "0.9"))
        fasce = [(a, a + 999) for a in range(0, 40000, 1000)] + [(40000, 59999), (60000, 99999), (100000, 2000000)]
        for n, (lo, hi) in enumerate(fasce, 1):
            if not self.tempo() or asc.blocked:
                break
            self._as_fascia(asc, lo, hi)
            if n % 5 == 0:
                log.warning("R60 AutoScout24 annunci: fascia %d/%d, %d annunci, %d saloni", n, len(fasce),
                            self.st["annunci_autoscout"], sum(1 for k in self.saloni if k.startswith("as:")))
        self.st["saloni_da_annunci_as"] = sum(1 for k in self.saloni if k.startswith("as:"))

    # ---- B) Subito ----
    def subito(self) -> None:
        from collections import Counter
        from ..collectors.subito import SubitoCollector, API_SEARCH, API_HEADERS
        from .lead import CI_SUBITO, TEL
        from .lead_comuni import cell_validi
        sub = SubitoCollector()
        sub.min_delay_s = float(os.environ.get("R60_SUB_PAUSA_MIN", "1.2"))
        sub.max_delay_s = float(os.environ.get("R60_SUB_PAUSA_MAX", "2.4"))
        http = Counter()
        prov_zona = sorted({pv for _, pv, _ in self.zona.values()} & set(CI_SUBITO))
        for prov in prov_zona:
            for ps, pe in BANDE:
                for page in range(int(os.environ.get("R60_SUBITO_PAGINE", "40"))):
                    if not self.tempo():
                        return
                    try:
                        r = sub.client.get(API_SEARCH, headers=API_HEADERS,
                                           params={"c": 2, "r": 4, "ci": CI_SUBITO[prov], "t": "s", "ps": ps, "pe": pe,
                                                   "lim": 100, "start": page * 100, "sort": "datedesc"})
                        http[r.status_code] += 1
                        ads = ((r.json() or {}).get("ads") or []) if r.status_code == 200 else []
                    except Exception:
                        self.st["errori"] += 1
                        ads = []
                    sub.pause()
                    for it in ads:
                        self.st["annunci_subito"] += 1
                        adv = it.get("advertiser") or {}
                        if not (adv.get("company") is True or str(adv.get("type")) == "1"):
                            continue
                        geo = it.get("geo") or {}
                        citta = ((geo.get("town") or {}).get("value") or "") if isinstance(geo.get("town"), dict) else ""
                        z = self.zona.get(slug(citta))
                        if not z and prov not in ("MI", "MB"):
                            continue                                   # fuori dai 60 km
                        nome = (adv.get("shop_name") or adv.get("name") or "").strip()
                        sid = adv.get("shop_id") or adv.get("user_id") or adv.get("userId") or nn(nome)
                        if not sid:
                            continue
                        cell = cell_validi(TEL.findall(f"{it.get('subject') or ''}\n{it.get('body') or ''}"))
                        link = adv.get("shop_url") or adv.get("url") or (it.get("urls") or {}).get("default")
                        self.metti(f"subito:{sid}", {"nome": nome[:150], "comune": (z[0] if z else citta) or None,
                                                     "prov": z[1] if z else prov, "km": z[2] if z else None,
                                                     "cell": cell, "fonti": {"subito"},
                                                     "link_subito": link if isinstance(link, str) else None})
                    if len(ads) < 100:
                        break
            log.warning("R60 Subito provincia %s fatta: %d annunci letti, risposte %s", prov,
                        self.st["annunci_subito"], dict(http))
        self.st["negozi_subito"] = sum(1 for k in self.saloni if k.startswith("subito:"))
        self.st["http_subito"] = dict(http)

    # ---- C) email dai siti ----
    def siti_email(self, fine: threading.Event) -> None:
        import httpx
        from .lead_email import _email
        with httpx.Client(timeout=12, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36"}) as c:
            while self.tempo():
                try:
                    chiave = self.siti.get(timeout=5)
                except queue.Empty:
                    if fine.is_set():
                        return
                    continue
                sito = (self.saloni.get(chiave) or {}).get("sito")
                if not sito:
                    continue
                trovate = []
                for p in PAGINE_SITO:
                    try:
                        r = c.get(sito.rstrip("/") + "/" + p)
                        self.st["siti_letti"] += 1
                        if r.status_code == 200:
                            trovate = _email(r.text)
                    except Exception:
                        pass
                    if trovate:
                        break
                if trovate:
                    self.st["email_da_siti"] += 1
                    self.metti(chiave, {"email": trovate})

    def dal_database(self) -> list[dict]:
        """I commercianti già raccolti nei giri vecchi (lead_tutti_dealer + lead_email), se nella zona."""
        from ..db import connect
        out = []
        per_comune = {slug(n): (n, pv, km) for n, pv, km in self.zona.values()}
        with connect() as conn:
            rows = conn.execute("SELECT d.chiave, d.nome, d.comune, d.prov, d.cell, d.fonte, e.email, e.sito "
                                "FROM lead_tutti_dealer d LEFT JOIN lead_email e ON e.chiave = d.chiave").fetchall()
        for r in rows:
            z = per_comune.get(slug(r["comune"] or ""))
            pv = (r["prov"] or "").upper()
            if not z and pv not in ("MI", "MB"):
                continue
            ch = r["chiave"] or ""
            out.append({"nome": r["nome"], "comune": z[0] if z else r["comune"], "prov": z[1] if z else pv,
                        "km": z[2] if z else None, "cell": list(r["cell"] or []), "email": list(r["email"] or []),
                        "sito": r["sito"], "fonti": {r["fonte"] or "database"},
                        "link_autoscout": ch if "/concessionari/" in ch else None})
        return out


def _brevo_copie(righe: list[dict], etichetta: str) -> int:
    """Copie leggibili senza area amministratore: modelli Brevo NON attivi, 400 righe ciascuno (non mandano email)."""
    import httpx
    from ..marketing.brevo_liste import API, _chiave
    key = _chiave()
    if not key:
        return 0
    n = 0
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        blocchi = [righe[i:i + 400] for i in range(0, len(righe), 400)]
        for k, b in enumerate(blocchi, 1):
            corpo = "\n".join(json.dumps(x, ensure_ascii=False, default=list) for x in b)
            html = "<html><body><pre>" + corpo.replace("&", "&amp;").replace("<", "&lt;") + "</pre></body></html>"
            r = c.post(f"{API}/smtp/templates", json={
                "templateName": f"R60 {etichetta} dati {k}/{len(blocchi)}", "subject": "dati interni, non inviare",
                "sender": {"email": "info@skovo.app"}, "htmlContent": html, "isActive": False})
            n += r.status_code < 300
    return n


def _pulisci(d: dict) -> dict:
    d["email"] = [e for e in d.get("email") or [] if "subito" not in e and "adevinta" not in e]
    if d.get("sito") and any(x in d["sito"] for x in ("subito.it", "adevinta", "google.", "facebook.", "apple.com")):
        d["sito"] = None
    return d


def _negozio_subito(c, url: str) -> dict:
    """Pagina annuncio o negozio Subito (o sito del salone): telefoni, email, sito. Dalla pagina annuncio si segue
    anche il link alla pagina negozio (impresapiu.subito.it/shops/...), dove spesso ci sono sito e telefono."""
    vuoto = {"cell": [], "fissi": [], "email": [], "sito": None}
    try:
        r = c.get(url)
        if r.status_code != 200:
            return vuoto
        d = _pulisci(dati_pagina(r.text))
        m = re.search(r'https://impresapiu\.subito\.it/shops/[A-Za-z0-9_-]+', r.text)
        if m and "impresapiu" not in url:
            r2 = c.get(m.group(0))
            if r2.status_code == 200:
                d2 = _pulisci(dati_pagina(r2.text))
                for f in ("cell", "fissi", "email"):
                    d[f] += [x for x in d2[f] if x not in d[f]]
                d["sito"] = d["sito"] or d2["sito"]
        return d
    except Exception:
        return vuoto


def lavoro_telefoni(etichetta: str) -> dict:
    """16/11 sera (cliente: "cerca il telefono di ognuno"): i negozi Subito della zona senza numero nel testo degli
    annunci. Si rilegge Subito (pausa più corta), poi per ogni negozio senza contatti la pagina negozio e il sito
    (home, contatti...): cellulari, fissi, email. Consegna: TUTTI i negozi Subito (con fonte e link), il confronto
    con il file già consegnato si fa fuori."""
    import httpx
    from ..marketing.consegne import consegna
    ore = float(os.environ.get("R60_ORE_TEL", "1.4"))
    R = Raccolta(time.time() + ore * 3600)
    log.warning("R60T parte: Subito + pagine negozio + siti, fino a %.1f ore", ore)
    R.subito()
    senza = [k for k, g in R.saloni.items() if not (g.get("cell") or g.get("fissi") or g.get("email"))]
    log.warning("R60T Subito fatto: %d negozi, %d senza contatti", len(R.saloni), len(senza))
    cerca_sito: list = []
    with httpx.Client(timeout=12, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36"}) as c:
        for i, k in enumerate(senza):
            g = R.saloni[k]
            if g.get("link_subito"):
                d = _negozio_subito(c, g["link_subito"])
                R.metti(k, {"cell": d["cell"], "fissi": d["fissi"], "email": d["email"], "sito": d["sito"]})
                time.sleep(float(os.environ.get("R60_PAUSA_NEG", "0.8")))
            if R.saloni[k].get("sito") and not (R.saloni[k].get("cell") or R.saloni[k].get("fissi")):
                cerca_sito.append(k)
            if (i + 1) % 200 == 0:
                log.warning("R60T pagine negozio: %d/%d", i + 1, len(senza))
        for k in cerca_sito:
            sito = R.saloni[k].get("sito") or ""
            if not sito.startswith("http") or "subito.it" in sito:
                continue
            for p in ("", "contatti", "contatti/", "contattaci", "chi-siamo", "dove-siamo"):
                d = _negozio_subito(c, sito.rstrip("/") + "/" + p)
                R.metti(k, {"cell": d["cell"], "fissi": d["fissi"], "email": d["email"]})
                if d["cell"] or d["fissi"]:
                    break
    righe = []
    for k, g in R.saloni.items():
        righe.append({"nome": g.get("nome"), "comune": g.get("comune"), "prov": g.get("prov"), "km": g.get("km"),
                      "cellulari": " ".join((g.get("cell") or [])[:4]), "fissi": " ".join((g.get("fissi") or [])[:3]),
                      "email": " ".join((g.get("email") or [])[:3]), "sito": g.get("sito"), "fonti": "subito",
                      "link_autoscout": None, "link_subito": g.get("link_subito"),
                      "era_senza": k in senza})
    for r in righe:
        consegna("R60T", r)
    copie = _brevo_copie(righe, etichetta)
    trovati = sum(1 for r in righe if r["era_senza"] and (r["cellulari"] or r["fissi"] or r["email"]))
    esito = {"negozi": len(righe), "senza_prima": len(senza), "trovati_dopo": trovati,
             "con_cell_dopo": sum(1 for r in righe if r["era_senza"] and r["cellulari"]), "siti_provati": len(cerca_sito),
             "modelli_brevo": copie, "http_subito": R.st.get("http_subito")}
    log.warning("R60T_FINE %s", json.dumps(esito, ensure_ascii=False))
    return esito


def lavoro(etichetta: str) -> dict:
    if etichetta.startswith("tel"):
        return lavoro_telefoni(etichetta)
    from ..marketing.consegne import consegna
    ore = float(os.environ.get("R60_ORE", "2.6"))
    R = Raccolta(time.time() + ore * 3600)
    fine = threading.Event()
    log.warning("R60 parte: %d comuni entro 60 km, fino a %.1f ore", len(R.zona), ore)
    fili = [threading.Thread(target=R.autoscout_annunci, daemon=True, name="r60-as-ann"),
            threading.Thread(target=R.autoscout, daemon=True, name="r60-as"),
            threading.Thread(target=R.subito, daemon=True, name="r60-sub")]
    email = [threading.Thread(target=R.siti_email, args=(fine,), daemon=True, name=f"r60-email{i}") for i in range(3)]
    for t in fili + email:
        t.start()
    for t in fili:
        t.join()
    fine.set()
    for t in email:
        t.join(timeout=600)
    vecchi = R.dal_database()
    tutti = unisci(list(R.saloni.values()) + vecchi)
    righe = []
    for g in tutti:
        if not (g["cell"] or g["fissi"] or g["email"]):
            continue
        righe.append({"nome": g["nome"], "comune": g["comune"], "prov": g["prov"], "km": g["km"],
                      "cellulari": " ".join(g["cell"][:4]), "fissi": " ".join(g["fissi"][:3]),
                      "email": " ".join(g["email"][:3]), "sito": g["sito"], "fonti": ",".join(sorted(f for f in g["fonti"] if f)),
                      "link_autoscout": g["link_autoscout"], "link_subito": g["link_subito"]})
    righe.sort(key=lambda x: (x["km"] if x["km"] is not None else (5 if x["prov"] == "MI" else 20), x["prov"] or "", x["comune"] or ""))
    for r in righe:
        consegna("R60", r)
    copie = _brevo_copie(righe, etichetta) if os.environ.get("R60_BREVO", "1") == "1" else 0
    esito = {**{k: v for k, v in R.st.items()}, "saloni_nuovo_giro": len(R.saloni), "dal_database": len(vecchi),
             "dopo_doppioni": len(tutti), "con_contatto": len(righe),
             "con_cellulare": sum(1 for r in righe if r["cellulari"]), "con_email": sum(1 for r in righe if r["email"]),
             "modelli_brevo": copie}
    log.warning("R60_FINE %s", json.dumps(esito, ensure_ascii=False))
    return esito
