"""22/10 (cliente): TUTTI i commercianti (solo attività, mai privati) entro LEADT_RAGGIO km da Sesto San Giovanni
che pubblicano su AutoScout24 o Subito, con un cellulare aziendale (3xx), senza doppioni e senza quelli gia'
consegnati (data/lead_esclusi.json, solo hash). Tutto resta nel database (tabella lead_tutti); nei log il conteggio
(LEADT_FINE) e, a richiesta, le righe da consegnare a blocchi (LEADT_ESPORTA="da-a" -> righe LEADT {json}).

    LEAD_TUTTI=n           giro una tantum (raccolta)
    LEADT_ESPORTA=n:200    scrive nei log le prossime 200 righe non ancora consegnate e le segna consegnate
                           (n = numero del blocco, cosi' ogni blocco si esporta una volta sola)
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
from collections import Counter
from pathlib import Path

from .lead import CI_SUBITO, TEL, _cellulari, _h, _nn, _nu, _tel9

log = logging.getLogger("lead")

PROV_SUBITO = ["MI", "MB", "LO", "CO", "LC", "BG", "PV", "VA"]     # province (anche in parte) entro ~50 km da Sesto
PROV_AS = {"MI", "MB", "LO", "CO", "LC", "BG", "PV", "VA", "NO", "CR"}

SQL_TAB = """
CREATE TABLE IF NOT EXISTS lead_tutti (
    tel        TEXT PRIMARY KEY,
    nome       TEXT, comune TEXT, prov TEXT, altri_tel TEXT,
    fonti      TEXT, link TEXT,
    auto       INTEGER, prezzo_min INTEGER, prezzo_mediano INTEGER, prezzo_max INTEGER,
    fascia     TEXT, quota_benzina REAL, marche TEXT,
    consegnato BOOLEAN NOT NULL DEFAULT false,
    aggiornato TIMESTAMPTZ NOT NULL DEFAULT now()
)"""


def _esclusi():
    es = json.loads((Path(__file__).resolve().parents[2] / "data" / "lead_esclusi.json").read_text())
    return set(es["tel"]), set(es["nome"]), set(es["url"])


def _nuovo_dealer(nome, comune, prov, cell, link, fonte):
    return {"nome": nome, "comune": comune, "prov": prov, "cell": list(cell), "link": link, "fonti": {fonte},
            "prezzi": [], "marche": Counter(), "benz": 0}


def _aggiungi(d, prezzo, marca, carb):
    if prezzo:
        d["prezzi"].append(int(prezzo))
    if marca:
        d["marche"][marca] += 1
    if "benz" in str(carb or "").lower():
        d["benz"] += 1


SENZA_CELL: dict = {}


def _chi_siamo(dealers: dict) -> int:
    """22/10 (cliente): per i commercianti senza cellulare negli annunci, il cellulare pubblicato nella loro
    pagina AutoScout24 ("chi siamo" o pagina del concessionario). Solo numeri dell'attività."""
    from html import unescape
    from ..collectors.autoscout import AutoScoutCollector
    asc = AutoScoutCollector()
    asc.min_delay_s, asc.max_delay_s = 0.8, 1.6
    trovati = 0
    for i, (url, d) in enumerate(sorted(SENZA_CELL.items(), key=lambda kv: -len(kv[1]["prezzi"]))):
        if "/concessionari/" not in url:
            continue
        cell = []
        for u in (url.rstrip("/") + "/chi-siamo", url):
            try:
                r = asc.client.get(u)
                testo = unescape(re.sub(r"<[^>]+>", " ", r.text)) if r.status_code == 200 else ""
            except Exception:
                testo = ""
            cell = _cellulari(TEL.findall(testo))
            asc.pause()
            if cell:
                break
        if cell:
            d["cell"] = cell
            k = cell[0]
            if k in dealers:
                dealers[k]["prezzi"] += d["prezzi"]
                dealers[k]["marche"].update(d["marche"])
                dealers[k]["benz"] += d["benz"]
            else:
                dealers[k] = d
            trovati += 1
        if (i + 1) % 200 == 0:
            log.warning("LEADT avanzamento chi siamo: %d pagine, %d cellulari trovati", i + 1, trovati)
    log.warning("LEADT chi siamo: %d commercianti senza cellulare negli annunci, %d trovati nella pagina", len(SENZA_CELL), trovati)
    return trovati


def _autoscout(dealers: dict, massimo: int) -> int:
    from ..collectors.autoscout import AutoScoutCollector
    os.environ["AUTOSCOUT_CAP"] = "20099"                       # Sesto San Giovanni
    os.environ["AUTOSCOUT_RAGGIO"] = os.environ.get("LEADT_RAGGIO", "50")
    os.environ["AUTOSCOUT_DANNEGGIATE"] = "include"
    os.environ.pop("AUTOSCOUT_KM_MAX", None)
    os.environ.pop("PASSI_JOB", None)
    os.environ["ANNO_MINIMO"] = "1990"
    ric = AutoScoutCollector()
    ric.min_delay_s, ric.max_delay_s = 0.6, 1.4
    n = 0
    for l in ric.search({"massiva": True, "min_price": 300, "max_price": 200000, "step": 2000,
                         "venditori": [("D", "commerciante")]}):
        n += 1
        s = (l.raw or {}).get("seller") or {}
        if not isinstance(s, dict) or str(s.get("type") or "").lower().startswith("priv"):
            continue
        if l.province and l.province.upper() not in PROV_AS:
            continue
        cell = _cellulari([p.get("callTo") or p.get("formattedNumber") for p in (s.get("phones") or []) if isinstance(p, dict)])
        nome = s.get("companyName") or (s.get("dealer") or {}).get("companyName")
        if not nome:
            continue
        links = s.get("links") if isinstance(s.get("links"), dict) else {}
        info = links.get("infoPage") or ""
        link = info if info.startswith("http") else "https://www.autoscout24.it" + info
        if not cell:
            # nessun cellulare negli annunci: si cerca dopo nella sua pagina "chi siamo"
            senza = SENZA_CELL.setdefault(link.split("?")[0], _nuovo_dealer(nome, l.city, (l.province or "").upper(), [],
                                                                             link.split("?")[0], "autoscout24"))
            _aggiungi(senza, l.price_eur, l.make, l.fuel)
            continue
        k = cell[0]
        d = dealers.get(k) or dealers.setdefault(k, _nuovo_dealer(nome, l.city, (l.province or "").upper(), cell, link.split("?")[0], "autoscout24"))
        d["fonti"].add("autoscout24")
        _aggiungi(d, l.price_eur, l.make, l.fuel)
        if n % 10000 == 0:
            log.warning("LEADT avanzamento AutoScout24: %d annunci, %d commercianti con cellulare", n, len(dealers))
        if n >= massimo:
            ric.stop = True
            break
    return n


def _subito(dealers: dict, max_pagine: int) -> int:
    from ..collectors.subito import SubitoCollector, API_SEARCH, API_HEADERS, parse_item
    sub = SubitoCollector()
    n = 0
    bande = [(0, 3000), (3000, 5000), (5000, 7000), (7000, 9000), (9000, 11000), (11000, 13000), (13000, 15000),
             (15000, 18000), (18000, 22000), (22000, 27000), (27000, 35000), (35000, 50000), (50000, 300000)]
    for prov in PROV_SUBITO:
        ci = CI_SUBITO.get(prov)
        if not ci:
            continue
        for ps, pe in bande:
            for page in range(max_pagine):
                try:
                    r = sub.client.get(API_SEARCH, headers=API_HEADERS,
                                       params={"c": 2, "r": 4, "ci": ci, "t": "s", "ps": ps, "pe": pe,
                                               "lim": 100, "start": page * 100, "sort": "datedesc"})
                    ads = ((r.json() or {}).get("ads") or []) if r.status_code == 200 else []
                except Exception:
                    ads = []
                sub.pause()
                for it in ads:
                    n += 1
                    adv = it.get("advertiser") or {}
                    if not (adv.get("company") is True or str(adv.get("type")) == "1"):
                        continue                                       # solo attività
                    testo = f"{it.get('subject') or ''}\n{it.get('body') or ''}"
                    cell = _cellulari(TEL.findall(testo))
                    if not cell:
                        continue
                    l = parse_item(it)
                    nome = adv.get("shop_name") or adv.get("name") or ""
                    k = cell[0]
                    d = dealers.get(k)
                    if d is None:
                        d = dealers.setdefault(k, _nuovo_dealer(nome, l.city if l else None, prov, cell,
                                                                (it.get("urls") or {}).get("default"), "subito"))
                    d["fonti"].add("subito")
                    if not d["nome"]:
                        d["nome"] = nome
                    if l:
                        _aggiungi(d, l.price_eur, l.make, l.fuel)
                if len(ads) < 100:
                    break
        log.warning("LEADT avanzamento Subito: provincia %s fatta, %d annunci letti, %d commercianti", prov, n, len(dealers))
    return n


def giro() -> dict:
    from ..db import connect
    dealers: dict = {}
    n_as = _autoscout(dealers, int(os.environ.get("LEADT_MAX_ANNUNCI", "250000")))
    n_cs = _chi_siamo(dealers) if os.environ.get("LEADT_CHI_SIAMO", "1") == "1" else 0
    n_sub = _subito(dealers, int(os.environ.get("LEADT_SUBITO_PAGINE", "30")))
    ET, EN, EU = _esclusi()
    # doppioni: stesso numero (chiave) oppure stesso nome normalizzato
    per_nome: dict = {}
    righe, gia = [], 0
    for k, d in dealers.items():
        nn = _nn(d["nome"])
        if any(_h(t) in ET for t in (_tel9(x) for x in d["cell"]) if t) or (nn and _h(nn) in EN) or \
                (d["link"] and _h(_nu(d["link"])) in EU):
            gia += 1
            continue
        if nn and nn in per_nome:
            altro = per_nome[nn]
            altro["cell"] += [c for c in d["cell"] if c not in altro["cell"]]
            altro["prezzi"] += d["prezzi"]
            altro["marche"].update(d["marche"])
            altro["benz"] += d["benz"]
            altro["fonti"] |= d["fonti"]
            continue
        per_nome[nn] = d
        righe.append(d)
    with connect() as conn:
        conn.execute(SQL_TAB)
        for d in righe:
            p = d["prezzi"]
            med = int(statistics.median(p)) if p else None
            fascia = None if med is None else ("sotto 3k" if med < 3000 else "3-8k" if med < 8000 else "8-15k" if med < 15000
                                                else "15-25k" if med < 25000 else "25-50k" if med < 50000 else "oltre 50k")
            conn.execute(
                "INSERT INTO lead_tutti (tel, nome, comune, prov, altri_tel, fonti, link, auto, prezzo_min, prezzo_mediano, "
                "prezzo_max, fascia, quota_benzina, marche) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tel) DO UPDATE SET nome=EXCLUDED.nome, comune=EXCLUDED.comune, prov=EXCLUDED.prov, "
                "altri_tel=EXCLUDED.altri_tel, fonti=EXCLUDED.fonti, link=EXCLUDED.link, auto=EXCLUDED.auto, "
                "prezzo_min=EXCLUDED.prezzo_min, prezzo_mediano=EXCLUDED.prezzo_mediano, prezzo_max=EXCLUDED.prezzo_max, "
                "fascia=EXCLUDED.fascia, quota_benzina=EXCLUDED.quota_benzina, marche=EXCLUDED.marche, aggiornato=now()",
                (d["cell"][0], (d["nome"] or "")[:150], d["comune"], d["prov"], " ".join(d["cell"][1:]),
                 ",".join(sorted(d["fonti"])), d["link"], len(p), min(p) if p else None, med, max(p) if p else None,
                 fascia, round(d["benz"] / len(p), 2) if p else None, ", ".join(m for m, _ in d["marche"].most_common(6))))
        conn.commit()
        tot = conn.execute("SELECT count(*) AS n FROM lead_tutti WHERE NOT consegnato").fetchone()["n"]
        fasce = {r["fascia"]: r["n"] for r in conn.execute(
            "SELECT fascia, count(*) AS n FROM lead_tutti WHERE NOT consegnato GROUP BY 1").fetchall()}
        prov = {r["prov"]: r["n"] for r in conn.execute(
            "SELECT prov, count(*) AS n FROM lead_tutti WHERE NOT consegnato GROUP BY 1").fetchall()}
    esito = {"annunci_autoscout": n_as, "annunci_subito": n_sub, "da_pagina_chi_siamo": n_cs, "commercianti_con_cell": len(dealers),
             "gia_consegnati_esclusi": gia, "doppioni_per_nome": len(dealers) - gia - len(righe),
             "nuovi_nel_database": tot, "fasce": fasce, "province": prov}
    log.warning("LEADT_FINE %s", json.dumps(esito, ensure_ascii=False))
    return esito


def esporta(quanti: int) -> int:
    """Righe da consegnare (nei log, una per commerciante), poi segnate come consegnate."""
    from ..db import connect
    with connect() as conn:
        conn.execute(SQL_TAB)
        rows = conn.execute("SELECT * FROM lead_tutti WHERE NOT consegnato ORDER BY prov, comune, nome "
                            "LIMIT %s", (quanti,)).fetchall()
        for r in rows:
            log.warning("LEADT %s", json.dumps({k: r[k] for k in ("tel", "altri_tel", "nome", "comune", "prov", "fonti",
                                                                 "link", "auto", "prezzo_min", "prezzo_mediano",
                                                                 "prezzo_max", "fascia", "quota_benzina", "marche")},
                                              ensure_ascii=False, default=str))
        if rows:
            conn.execute("UPDATE lead_tutti SET consegnato = true WHERE tel = ANY(%s)", ([r["tel"] for r in rows],))
            conn.commit()
    log.warning("LEADT_ESPORTATI %d", len(rows))
    return len(rows)
