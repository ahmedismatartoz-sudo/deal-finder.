"""22/10 (cliente): TUTTI i commercianti (solo attività, mai privati) entro LEADT_RAGGIO km da Sesto San Giovanni
che pubblicano su AutoScout24 o Subito, con un cellulare aziendale (3xx), senza doppioni e senza quelli gia'
consegnati (data/lead_esclusi.json, solo hash). Tutto resta nel database; nei log solo conteggi (LEADT_FINE) e,
a richiesta, le righe da consegnare a blocchi (LEADT_ESPORTA=blocco:quanti -> righe LEADT {json}).

23/10: gira nel cron (voce COLLECT_PRIMA "leadtutti:n"), non nel sito (512 MB: andava in crash per memoria).
Salva man mano nel database (lead_tutti_dealer / lead_tutti_ann) e riprende da dove era arrivato
(fasce di prezzo gia' lette: PASSI_JOB; pagine "chi siamo" gia' provate: colonna tentato).
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
);
CREATE TABLE IF NOT EXISTS lead_tutti_dealer (
    chiave   TEXT PRIMARY KEY,           -- pagina AutoScout24 del commerciante o "subito:<numero>"
    nome     TEXT, comune TEXT, prov TEXT, cell TEXT[] NOT NULL DEFAULT '{}', fonte TEXT,
    tentato  BOOLEAN NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS lead_tutti_ann (
    chiave     TEXT NOT NULL, annuncio TEXT NOT NULL,
    prezzo     INTEGER, marca TEXT, carb TEXT,
    PRIMARY KEY (chiave, annuncio)
)"""


def _tabelle(conn):
    for q in SQL_TAB.split(";"):
        if q.strip():
            conn.execute(q)
    conn.commit()


def _esclusi():
    es = json.loads((Path(__file__).resolve().parents[2] / "data" / "lead_esclusi.json").read_text())
    return set(es["tel"]), set(es["nome"]), set(es["url"])


class _Buffer:
    """Scrive nel database ogni ~300 annunci: se il processo si ferma, il lavoro fatto resta."""

    def __init__(self):
        self.dealer, self.ann = {}, []

    def add(self, chiave, nome, comune, prov, cell, fonte, annuncio, prezzo, marca, carb):
        if chiave not in self.dealer:
            self.dealer[chiave] = (chiave, (nome or "")[:150], comune, prov, list(cell), fonte)
        if annuncio:
            self.ann.append((chiave, str(annuncio), int(prezzo) if prezzo else None, marca, carb))
        if len(self.ann) >= 300:
            self.flush()

    def flush(self):
        if not self.dealer and not self.ann:
            return
        from ..db import connect
        with connect() as conn:
            conn.cursor().executemany(
                "INSERT INTO lead_tutti_dealer (chiave, nome, comune, prov, cell, fonte) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (chiave) DO UPDATE SET cell = CASE WHEN cardinality(EXCLUDED.cell) > 0 THEN EXCLUDED.cell "
                "ELSE lead_tutti_dealer.cell END", list(self.dealer.values()))
            conn.cursor().executemany(
                "INSERT INTO lead_tutti_ann (chiave, annuncio, prezzo, marca, carb) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING", self.ann)
            conn.commit()
        self.dealer, self.ann = {}, []


def _autoscout(buf: _Buffer, massimo: int) -> int:
    from ..collectors.autoscout import AutoScoutCollector
    os.environ["AUTOSCOUT_CAP"] = "20099"                       # Sesto San Giovanni
    os.environ["AUTOSCOUT_RAGGIO"] = os.environ.get("LEADT_RAGGIO", "50")
    os.environ["AUTOSCOUT_DANNEGGIATE"] = "include"
    os.environ.pop("AUTOSCOUT_KM_MAX", None)
    os.environ["PASSI_JOB"] = "leadtutti:" + os.environ.get("LEADT_GIRO", "1")   # riprende dalle fasce non lette
    anno = os.environ.get("ANNO_MINIMO")
    os.environ["ANNO_MINIMO"] = "1990"
    ric = AutoScoutCollector()
    ric.min_delay_s, ric.max_delay_s = 0.6, 1.4
    n = tenuti = 0
    try:
        for l in ric.search({"massiva": True, "min_price": 300, "max_price": 200000, "step": 2000,
                             "venditori": [("D", "commerciante")]}):
            n += 1
            s = (l.raw or {}).get("seller") or {}
            l.raw = None                                           # memoria: il testo completo non serve piu'
            if not isinstance(s, dict) or str(s.get("type") or "").lower().startswith("priv"):
                continue
            if l.province and l.province.upper() not in PROV_AS:
                continue
            nome = s.get("companyName") or (s.get("dealer") or {}).get("companyName")
            if not nome:
                continue
            cell = _cellulari([p.get("callTo") or p.get("formattedNumber") for p in (s.get("phones") or []) if isinstance(p, dict)])
            links = s.get("links") if isinstance(s.get("links"), dict) else {}
            info = links.get("infoPage") or ""
            link = (info if info.startswith("http") else "https://www.autoscout24.it" + info).split("?")[0]
            buf.add(link, nome, l.city, (l.province or "").upper(), cell, "autoscout24", l.source_id,
                    l.price_eur, l.make, l.fuel)
            tenuti += 1
            if tenuti % 10000 == 0:
                log.warning("LEADT avanzamento AutoScout24: %d annunci di commercianti in zona", tenuti)
            if n >= massimo:
                ric.stop = True
                break
    finally:
        buf.flush()
        os.environ.pop("PASSI_JOB", None)
        if anno is None:
            os.environ.pop("ANNO_MINIMO", None)
        else:
            os.environ["ANNO_MINIMO"] = anno
    return n


def _chi_siamo() -> int:
    """Per i commercianti senza cellulare negli annunci: il cellulare dell'attività nella loro pagina AutoScout24."""
    from html import unescape
    from ..collectors.autoscout import AutoScoutCollector
    from ..db import connect
    asc = AutoScoutCollector()
    asc.min_delay_s, asc.max_delay_s = 0.8, 1.6
    with connect() as conn:
        da_fare = [r["chiave"] for r in conn.execute(
            "SELECT d.chiave FROM lead_tutti_dealer d WHERE cardinality(d.cell) = 0 AND NOT d.tentato "
            "AND d.chiave LIKE '%%/concessionari/%%'").fetchall()]
    trovati = 0
    for i, url in enumerate(da_fare):
        cell = []
        for u in (url.rstrip("/") + "/chi-siamo", url):
            try:
                r = asc.client.get(u)
                testo = unescape(re.sub(r"<[^>]+>", " ", r.text)) if r.status_code == 200 else ""
            except Exception:
                testo = ""
            asc.pause()
            cell = _cellulari(TEL.findall(testo))
            if cell:
                break
        with connect() as conn:
            conn.execute("UPDATE lead_tutti_dealer SET tentato = true, cell = %s WHERE chiave = %s", (cell, url))
            conn.commit()
        trovati += bool(cell)
        if (i + 1) % 200 == 0:
            log.warning("LEADT avanzamento chi siamo: %d/%d pagine, %d cellulari trovati", i + 1, len(da_fare), trovati)
    log.warning("LEADT chi siamo: %d pagine provate, %d cellulari trovati", len(da_fare), trovati)
    return trovati


def _subito(buf: _Buffer, max_pagine: int) -> int:
    from ..collectors.subito import SubitoCollector, API_SEARCH, API_HEADERS, parse_item
    from . import passi
    os.environ["PASSI_JOB"] = "leadtutti:" + os.environ.get("LEADT_GIRO", "1")
    sub = SubitoCollector()
    n = 0
    bande = [(0, 3000), (3000, 5000), (5000, 7000), (7000, 9000), (9000, 11000), (11000, 13000), (13000, 15000),
             (15000, 18000), (18000, 22000), (22000, 27000), (27000, 35000), (35000, 50000), (50000, 300000)]
    try:
        for prov in PROV_SUBITO:
            ci = CI_SUBITO.get(prov)
            if not ci:
                continue
            for ps, pe in bande:
                passo = f"leadt_sub:{prov}:{ps}"
                if passi.fatto(passo):
                    continue
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
                        cell = _cellulari(TEL.findall(f"{it.get('subject') or ''}\n{it.get('body') or ''}"))
                        if not cell:
                            continue
                        l = parse_item(it)
                        buf.add("subito:" + cell[0], adv.get("shop_name") or adv.get("name") or "",
                                l.city if l else None, prov, cell, "subito", (l.source_id if l else it.get("urn")),
                                l.price_eur if l else None, l.make if l else None, l.fuel if l else None)
                    if len(ads) < 100:
                        break
                buf.flush()
                passi.segna(passo)
            log.warning("LEADT avanzamento Subito: provincia %s fatta, %d annunci letti", prov, n)
    finally:
        buf.flush()
        os.environ.pop("PASSI_JOB", None)
    return n


def _componi() -> dict:
    """Dal database: un commerciante per cellulare, doppioni per nome uniti, gia' consegnati tolti -> lead_tutti."""
    from ..db import connect
    ET, EN, EU = _esclusi()
    with connect() as conn:
        dealers = conn.execute("SELECT chiave, nome, comune, prov, cell, fonte FROM lead_tutti_dealer "
                               "WHERE cardinality(cell) > 0").fetchall()
        ann: dict = {}
        for r in conn.execute("SELECT chiave, prezzo, marca, carb FROM lead_tutti_ann").fetchall():
            ann.setdefault(r["chiave"], []).append(r)
    per_tel: dict = {}
    for d in dealers:
        cell = list(d["cell"])
        k = cell[0]
        g = per_tel.setdefault(k, {"nome": d["nome"], "comune": d["comune"], "prov": d["prov"], "cell": [],
                                   "link": None, "fonti": set(), "ann": []})
        g["cell"] += [c for c in cell if c not in g["cell"]]
        g["fonti"].add(d["fonte"])
        if d["chiave"].startswith("http") and not g["link"]:
            g["link"] = d["chiave"]
        g["ann"] += ann.get(d["chiave"], [])
    per_nome: dict = {}
    righe, gia = [], 0
    for g in per_tel.values():
        nn = _nn(g["nome"])
        if any(_h(t) in ET for t in (_tel9(x) for x in g["cell"]) if t) or (nn and _h(nn) in EN) or \
                (g["link"] and _h(_nu(g["link"])) in EU):
            gia += 1
            continue
        if nn and nn in per_nome:
            altro = per_nome[nn]
            altro["cell"] += [c for c in g["cell"] if c not in altro["cell"]]
            altro["ann"] += g["ann"]
            altro["fonti"] |= g["fonti"]
            continue
        per_nome[nn or id(g)] = g
        righe.append(g)
    with connect() as conn:
        for g in righe:
            p = [a["prezzo"] for a in g["ann"] if a["prezzo"]]
            med = int(statistics.median(p)) if p else None
            fascia = None if med is None else ("sotto 3k" if med < 3000 else "3-8k" if med < 8000 else "8-15k" if med < 15000
                                                else "15-25k" if med < 25000 else "25-50k" if med < 50000 else "oltre 50k")
            marche = Counter(a["marca"] for a in g["ann"] if a["marca"])
            benz = sum(1 for a in g["ann"] if "benz" in str(a["carb"] or "").lower())
            conn.execute(
                "INSERT INTO lead_tutti (tel, nome, comune, prov, altri_tel, fonti, link, auto, prezzo_min, prezzo_mediano, "
                "prezzo_max, fascia, quota_benzina, marche) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tel) DO UPDATE SET nome=EXCLUDED.nome, comune=EXCLUDED.comune, prov=EXCLUDED.prov, "
                "altri_tel=EXCLUDED.altri_tel, fonti=EXCLUDED.fonti, link=EXCLUDED.link, auto=EXCLUDED.auto, "
                "prezzo_min=EXCLUDED.prezzo_min, prezzo_mediano=EXCLUDED.prezzo_mediano, prezzo_max=EXCLUDED.prezzo_max, "
                "fascia=EXCLUDED.fascia, quota_benzina=EXCLUDED.quota_benzina, marche=EXCLUDED.marche, aggiornato=now()",
                (g["cell"][0], (g["nome"] or "")[:150], g["comune"], g["prov"], " ".join(g["cell"][1:]),
                 ",".join(sorted(f for f in g["fonti"] if f)), g["link"], len(g["ann"]), min(p) if p else None, med,
                 max(p) if p else None, fascia, round(benz / len(g["ann"]), 2) if g["ann"] else None,
                 ", ".join(m for m, _ in marche.most_common(6))))
        conn.commit()
        tot = conn.execute("SELECT count(*) AS n FROM lead_tutti WHERE NOT consegnato").fetchone()["n"]
        fasce = {r["fascia"]: r["n"] for r in conn.execute(
            "SELECT fascia, count(*) AS n FROM lead_tutti WHERE NOT consegnato GROUP BY 1").fetchall()}
        prov = {r["prov"]: r["n"] for r in conn.execute(
            "SELECT prov, count(*) AS n FROM lead_tutti WHERE NOT consegnato GROUP BY 1").fetchall()}
    return {"commercianti_con_cell": len(per_tel), "gia_consegnati_esclusi": gia,
            "doppioni_per_nome": len(per_tel) - gia - len(righe), "nuovi_nel_database": tot, "fasce": fasce, "province": prov}


def giro() -> dict:
    from ..db import connect
    with connect() as conn:
        _tabelle(conn)
    buf = _Buffer()
    n_as = _autoscout(buf, int(os.environ.get("LEADT_MAX_ANNUNCI", "250000")))
    n_cs = _chi_siamo() if os.environ.get("LEADT_CHI_SIAMO", "1") == "1" else 0
    n_sub = _subito(buf, int(os.environ.get("LEADT_SUBITO_PAGINE", "30")))
    esito = {"annunci_autoscout": n_as, "da_pagina_chi_siamo": n_cs, "annunci_subito": n_sub, **_componi()}
    log.warning("LEADT_FINE %s", json.dumps(esito, ensure_ascii=False))
    return esito


def esporta(quanti: int) -> int:
    """Righe da consegnare (nei log, una per commerciante), poi segnate come consegnate."""
    from ..db import connect
    with connect() as conn:
        _tabelle(conn)
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
