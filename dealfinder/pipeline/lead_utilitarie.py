"""22/10 (cliente): piccoli commercianti di utilitarie (3.000-25.000 €) in Lombardia e dintorni, con il
cellulare che l'attività pubblica sui suoi annunci AutoScout24, profilati per fascia, per la prova WhatsApp
(il cliente chiede il permesso a ognuno prima di scrivere). Stesso metodo delle liste lead del 19-21/10:
solo attività (venditori professionali), mai privati. Risultato nei log: LEADWA {json}.
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
from collections import Counter

from .lead import _cellulari

log = logging.getLogger("lead")

PROV = {"MI", "MB", "BG", "BS", "CO", "CR", "LC", "LO", "PV", "VA", "NO", "PC"}
GRANDI = re.compile(r"\b(s\.?p\.?a|spa|group|gruppo|holding|motorvillage|stellantis|spoticar|autotorino|"
                    r"concessionari[ao]\s+(ufficiale|fiat|opel|peugeot|renault|ford|toyota|volkswagen))\b", re.I)


def giro() -> dict:
    from ..collectors.autoscout import AutoScoutCollector

    os.environ["AUTOSCOUT_CAP"], os.environ["AUTOSCOUT_RAGGIO"] = "20121", os.environ.get("LEADWA_RAGGIO", "90")
    os.environ.pop("AUTOSCOUT_KM_MAX", None)
    os.environ.pop("PASSI_JOB", None)
    ric = AutoScoutCollector()
    ric.min_delay_s, ric.max_delay_s = 0.6, 1.4
    massimo = int(os.environ.get("LEADWA_MAX_ANNUNCI", "40000"))
    dealers: dict = {}
    n = 0
    for l in ric.search({"massiva": True, "min_price": 2500, "max_price": 25000, "step": 1000,
                         "venditori": [("D", "commerciante")]}):
        n += 1
        s = (l.raw or {}).get("seller") or {}
        if not isinstance(s, dict) or str(s.get("type") or "").lower().startswith("priv"):
            continue
        if not l.province or l.province.upper() not in PROV:
            continue
        nome = s.get("companyName") or (s.get("dealer") or {}).get("companyName")
        cell = _cellulari([p.get("callTo") or p.get("formattedNumber") for p in (s.get("phones") or []) if isinstance(p, dict)])
        if not nome or not cell:
            continue
        links = s.get("links") if isinstance(s.get("links"), dict) else {}
        k = str(s.get("id") or links.get("infoPage") or nome)
        d = dealers.setdefault(k, {"nome": nome, "comune": l.city, "prov": l.province.upper(), "cell": cell,
                                   "url": "https://www.autoscout24.it" + (links.get("infoPage") or ""),
                                   "prezzi": [], "marche": Counter(), "benz": 0})
        if l.price_eur:
            d["prezzi"].append(int(l.price_eur))
        if l.make:
            d["marche"][l.make] += 1
        if "benz" in str(l.fuel or "").lower():
            d["benz"] += 1
        if n % 5000 == 0:
            log.warning("LEADWA_RICERCA avanzamento: %d annunci, %d commercianti con cellulare", n, len(dealers))
        if n >= massimo:
            ric.stop = True
            break
    max_auto = int(os.environ.get("LEADWA_MAX_AUTO", "40"))
    tenuti = 0
    fasce = Counter()
    for d in sorted(dealers.values(), key=lambda x: len(x["prezzi"])):
        k = len(d["prezzi"])
        if not k or k > max_auto or GRANDI.search(d["nome"] or ""):
            continue
        med = statistics.median(d["prezzi"])
        fascia = "3-8k" if med < 8000 else ("8-15k" if med < 15000 else "15-25k")
        fasce[fascia] += 1
        tenuti += 1
        log.warning("LEADWA %s", json.dumps({
            "nome": d["nome"], "comune": d["comune"], "prov": d["prov"], "cell": d["cell"], "fascia": fascia,
            "prezzo_min": min(d["prezzi"]), "prezzo_mediano": int(med), "prezzo_max": max(d["prezzi"]),
            "auto": k, "quota_benzina": round(d["benz"] / k, 2),
            "marche": [m for m, _ in d["marche"].most_common(6)], "fonte": d["url"]}, ensure_ascii=False))
    esito = {"annunci": n, "commercianti_con_cell": len(dealers), "piccoli": tenuti, "fasce": dict(fasce)}
    log.warning("LEADWA_FINE %s", json.dumps(esito))
    return esito
