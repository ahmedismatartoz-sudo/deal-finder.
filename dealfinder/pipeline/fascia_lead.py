"""20/10 (cliente): lead per la fascia MEDIO-BASSA. Per ogni lead di data/lead_fascia.json guarda le auto che
l'attività ha in vendita (pagina AutoScout24 del concessionario o pagina del negozio Subito) e calcola prezzo medio
e quota di auto di lusso. Nei log: FASCIA {json} per ogni lead e FASCIA_FINE.
Niente dati personali: solo nome dell'attività, prezzi e marche delle auto in vendita."""
from __future__ import annotations

import json
import logging
import re
import statistics
from collections import Counter
from html import unescape
from pathlib import Path

log = logging.getLogger("fascia_lead")
LUSSO = {"porsche", "ferrari", "lamborghini", "mclaren", "bentley", "rolls-royce", "rolls royce", "aston martin",
         "maserati", "lotus", "maybach", "bugatti", "pagani", "alpina"}
MARCHE = ["fiat", "lancia", "alfa romeo", "volkswagen", "opel", "ford", "renault", "peugeot", "citroen", "toyota",
          "nissan", "kia", "hyundai", "dacia", "suzuki", "skoda", "seat", "smart", "mini", "audi", "bmw", "mercedes",
          "volvo", "jeep", "mazda", "honda", "mitsubishi", "dr", "mg", "ds", "cupra", "tesla", "land rover", "jaguar",
          "lexus", "subaru", "chevrolet", "porsche", "ferrari", "lamborghini", "maserati", "bentley", "mclaren",
          "aston martin", "rolls-royce", "lotus"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _annunci_json(d, out, prof=0):
    """Annunci dentro __NEXT_DATA__: dizionari con prezzo e veicolo."""
    if prof > 12 or len(out) > 200:
        return
    if isinstance(d, dict):
        if "price" in d and ("vehicle" in d or "make" in d):
            v = d.get("vehicle") if isinstance(d.get("vehicle"), dict) else d
            p = d.get("price")
            if isinstance(p, dict):
                p = p.get("priceFormatted") or p.get("value") or p.get("amount")
            trk = d.get("tracking") if isinstance(d.get("tracking"), dict) else {}
            p = trk.get("price") or p
            n = re.sub(r"[^\d]", "", str(p or ""))
            if n and 300 <= int(n) <= 2_000_000:
                out.append((str(v.get("make") or "").lower(), int(n)))
                return
        for x in d.values():
            _annunci_json(x, out, prof + 1)
    elif isinstance(d, list):
        for x in d:
            _annunci_json(x, out, prof + 1)


def _annunci_testo(html: str):
    """Ripiego: prezzi e marche letti dal testo della pagina."""
    t = unescape(re.sub(r"<[^>]+>", " ", html))
    prezzi = [int(re.sub(r"\D", "", m)) for m in re.findall(r"€\s?(\d{1,3}(?:[.  ]\d{3})+)", t)]
    prezzi += [int(re.sub(r"\D", "", m)) for m in re.findall(r"(\d{1,3}(?:\.\d{3})+)\s?€", t)]
    prezzi = [p for p in prezzi if 500 <= p <= 2_000_000]
    low = t.lower()
    marche = Counter({m: len(re.findall(r"\b" + re.escape(m) + r"\b", low)) for m in MARCHE})
    return prezzi, marche


def _stat(cl, url: str) -> dict:
    from ..collectors.base import next_data
    try:
        r = cl.get(url, timeout=20)
    except Exception as e:
        return {"errore": str(e)[:80]}
    if r.status_code != 200:
        return {"errore": f"HTTP {r.status_code}"}
    auto: list = []
    _annunci_json(next_data(r.text) or {}, auto)
    html = r.text
    if len(auto) < 3 and "autoscout24" in url:              # pagina del concessionario senza annunci: link al suo stock
        for h in re.findall(r'href="([^"]*/lst[^"]*)"', html)[:5]:
            u2 = unescape(h if h.startswith("http") else "https://www.autoscout24.it" + h)
            try:
                r2 = cl.get(u2, timeout=20)
                if r2.status_code == 200:
                    _annunci_json(next_data(r2.text) or {}, auto)
                    if len(auto) >= 3:
                        break
            except Exception:
                pass
    if len(auto) >= 3:
        prezzi = [p for _, p in auto]
        lusso = sum(1 for m, p in auto if m in LUSSO or p >= 45000)
        marche = Counter(m for m, _ in auto if m)
        metodo = "annunci"
    else:
        prezzi, mc = _annunci_testo(html)
        lusso = sum(1 for p in prezzi if p >= 45000)
        marche = mc
        metodo = "testo"
    if len(prezzi) < 3:
        return {"metodo": metodo, "n": len(prezzi), "lusso_marche": sum(marche[m] for m in LUSSO if m in marche)}
    return {"metodo": metodo, "n": len(prezzi), "mediana": int(statistics.median(prezzi)),
            "quota_lusso": round(lusso / len(prezzi), 2), "lusso_marche": sum(marche[m] for m in LUSSO if m in marche),
            "marche": [m for m, _ in marche.most_common(5) if _]}


def giro() -> dict:
    import httpx
    from concurrent.futures import ThreadPoolExecutor
    voci = json.loads((Path(__file__).resolve().parents[2] / "data" / "lead_fascia.json").read_text())

    import time, random

    def uno(v):
        with httpx.Client(headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"}, follow_redirects=True) as cl:
            for t in range(4):
                time.sleep(random.uniform(1.5, 3.0))
                s = _stat(cl, v["url"])
                if "429" not in str(s.get("errore", "")):
                    return v, s
                time.sleep(40 * (t + 1))
            return v, s

    st: Counter = Counter()
    riga = []
    with ThreadPoolExecutor(2) as ex:
        for v, s in ex.map(uno, voci):
            st[s.get("metodo", "errore")] += 1
            riga.append([v["i"], s.get("mediana"), s.get("quota_lusso"), s.get("lusso_marche"), s.get("n"),
                         ",".join(s.get("marche") or []), s.get("errore")])
            if len(riga) >= 20:
                log.warning("FASCIA2 %s", json.dumps(riga, ensure_ascii=False)); riga = []
    if riga:
        log.warning("FASCIA2 %s", json.dumps(riga, ensure_ascii=False))
    log.warning("FASCIA_FINE %s", json.dumps(dict(st)))
    return dict(st)
