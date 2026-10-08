"""Prova: quante persone CERCANO un'auto su Facebook (Marketplace e gruppi pubblici)?

    python -m dealfinder.pipeline.cerco_probe

Gira una volta (dal ciclo automatico, poi non più per 30 giorni) e scrive nei log:
  CERCO_PROBE {...}   conteggi per fonte e per giorno, con alcuni esempi di testo
Non salva nomi, profili o numeri di telefono: solo conteggi e brevi estratti del testo.

Marketplace: ricerche "cerco auto" e simili con il dataset già in uso.
Gruppi: solo gruppi PUBBLICI indicati in FB_CERCO_GROUPS (link separati da virgola),
dataset Bright Data "Facebook posts by group URL".
Spesa massima: FB_CERCO_LIMIT record per ricerca o gruppo (default 100).
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter

from ..collectors.brightdata import API, BrightDataFacebookCollector, search_url
from ..db import connect, log_job

log = logging.getLogger("cerco")
GROUPS_DATASET = os.environ.get("BRIGHTDATA_GROUPS_DATASET", "gd_lz11l67o2cb3r0lkj3")
LIMIT = int(os.environ.get("FB_CERCO_LIMIT", "100"))
MARKET_QUERIES = ["cerco auto", "cerco macchina", "compro auto"]

RE_REQUEST = re.compile(r"\b(cerc[oa]|cerchiamo|cercasi|sto cercando|compro|acquisto|acquisterei|"
                        r"valuto acquisto|qualcuno vende)\b", re.I)
RE_CAR = re.compile(r"\b(auto|macchina|vettura|utilitaria|suv|station|monovolume|neopatentat\w*|"
                    r"panda|punto|yaris|polo|golf|clio|corsa|fiesta|c3|208|500|ypsilon|i10|picanto|"
                    r"aygo|micra|sandero|duster|qashqai|tiguan|audi|bmw|mercedes|fiat|toyota|"
                    r"volkswagen|renault|peugeot|citroen|opel|ford|dacia|kia|hyundai|nissan|seat|skoda)\b",
                    re.I)
RE_SELLER = re.compile(r"\b(vendo|vendesi|in vendita|cedo|prezzo trattabile)\b", re.I)
# chi "compra qualsiasi auto" è quasi sempre un commerciante, non un cliente finale
RE_DEALER_BUYER = re.compile(r"\b(compr\w* (?:auto|macchine|veicoli)\b(?! per)|ritiro auto|ritiriamo|pagamento (?:in )?contanti|"
                             r"anche incidentat\w*|qualsiasi (?:auto|condizione|marca)|ogni condizione|valutazione gratuita)",
                             re.I)
RE_PHONE = re.compile(r"\+?\d[\d .]{7,}\d")
RE_BUDGET = re.compile(r"(?:max|massimo|fino a|budget|entro)\s*(?:di\s*)?(?:€\s*)?(\d{1,3}(?:[.\s]?\d{3})?)\s*(?:€|euro|k)?",
                       re.I)


def is_request(text: str) -> bool:
    """Una persona che cerca un'auto da comprare (non un venditore)."""
    head = (text or "")[:400]
    return bool(RE_REQUEST.search(head) and RE_CAR.search(head) and not RE_SELLER.search(head)
                and not RE_DEALER_BUYER.search(head))


def snippet(text: str) -> str:
    return RE_PHONE.sub("[tel]", " ".join((text or "").split()))[:160]


def _trigger_groups(c: BrightDataFacebookCollector, groups: list[str]) -> str:
    bodies = [{"input": [{"url": g, "num_of_posts": LIMIT} for g in groups]},
              {"input": [{"url": g} for g in groups]}]          # se i campi extra non sono accettati
    last = None
    for body in bodies:
        r = c.client.post(f"{API}/trigger", params={"dataset_id": GROUPS_DATASET, "include_errors": "true",
                                                     "limit_multiple_results": LIMIT * len(groups)}, json=body)
        if r.status_code < 400:
            return r.json()["snapshot_id"]
        last = f"HTTP {r.status_code}: {r.text[:300]}"
        log.warning("gruppi: trigger rifiutato (%s), riprovo", last)
    raise RuntimeError("Bright Data gruppi: " + str(last))


def _summarize(rows: list[dict], text_keys: tuple, date_key: str | None) -> dict:
    total, req, dealers, per_day, budgets, examples = 0, 0, 0, Counter(), [], []
    for row in rows:
        if row.get("error"):
            continue
        total += 1
        text = " ".join(str(row.get(k) or "") for k in text_keys)
        if RE_DEALER_BUYER.search(text[:400]):
            dealers += 1
            continue
        if not is_request(text):
            continue
        req += 1
        day = str(row.get(date_key) or "")[:10] if date_key else ""
        if day:
            per_day[day] += 1
        m = RE_BUDGET.search(text)
        if m:
            budgets.append(int(re.sub(r"\D", "", m.group(1))))
        if len(examples) < 12:
            examples.append(snippet(text))
    return {"letti": total, "richieste": req, "commercianti_che_comprano": dealers, "per_giorno": dict(sorted(per_day.items())),
            "con_budget": len(budgets), "budget_mediano": sorted(budgets)[len(budgets) // 2] if budgets else None,
            "esempi": examples}


def job_name() -> str:
    return "cerco_probe+gruppi" if os.environ.get("FB_CERCO_GROUPS") else "cerco_probe"


def run() -> dict:
    c = BrightDataFacebookCollector()
    if not c.configured():
        return {"saltato": "BRIGHTDATA_API_KEY assente"}
    c.limit = LIMIT
    out: dict = {}
    with connect() as conn:
        finish = log_job(conn, job_name())
        try:
            urls = [search_url("milan", 100, 0, 1_000_000, days=7, query=q) for q in MARKET_QUERIES]
            sid = c.trigger(urls)
            c.wait(sid, max_minutes=30)
            out["marketplace"] = _summarize(c.download(sid), ("title", "description"), None)
            log.info("CERCO_PROBE marketplace %s", json.dumps(out["marketplace"], ensure_ascii=False))
        except Exception as e:
            log.exception("prova Marketplace fallita")
            out["marketplace"] = {"errore": str(e)[:200]}
        groups = [g.strip() for g in os.environ.get("FB_CERCO_GROUPS", "").split(",") if g.strip()]
        if groups:
            try:
                sid = _trigger_groups(c, groups)
                c.wait(sid, max_minutes=40)
                rows = c.download(sid)
                out["gruppi"] = _summarize(rows, ("content",), "date_posted")
                out["gruppi"]["per_gruppo"] = dict(Counter(str(r.get("group_name") or r.get("group_url"))
                                                           for r in rows if not r.get("error")
                                                           and is_request(r.get("content") or "")))
                log.info("CERCO_PROBE gruppi %s", json.dumps(out["gruppi"], ensure_ascii=False))
            except Exception as e:
                log.exception("prova gruppi fallita")
                out["gruppi"] = {"errore": str(e)[:200]}
        else:
            out["gruppi"] = {"saltato": "nessun gruppo in FB_CERCO_GROUPS"}
        finish(True, {k: {kk: vv for kk, vv in v.items() if kk != "esempi"} for k, v in out.items()})
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(json.dumps(run(), ensure_ascii=False, indent=1))
