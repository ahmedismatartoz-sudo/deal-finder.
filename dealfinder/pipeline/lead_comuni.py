"""23/10 (cliente): commercianti di auto usate SOLO nei comuni del suo elenco (data/comuni_sesto.json, ~50 minuti
da Sesto). Per ogni comune la pagina AutoScout24 "rivenditori a <comune>", poi per ogni salone la pagina
"chi siamo" (referente e cellulare) e la sua vetrina (prezzi -> fascia). Solo attività, mai privati.
Righe nei log: LEADC {json}; fine: LEADC_FINE."""
from __future__ import annotations

import json
import logging
import re
import statistics
import unicodedata
from html import unescape
from pathlib import Path

from .lead import TEL, _cellulari

PREF_CELL = re.compile(r"^3(2[0-9]|3[0-9]|4[0-9]|5[0-3]|6[0-8]|7[0-35-9]|8[0-9]|9[0-3])")


def cell_validi(tels) -> list:
    """Solo cellulari veri: 10 cifre e prefisso di un operatore italiano (niente pezzi di prezzi o codici)."""
    return [c for c in _cellulari(tels) if len(c) == 10 and PREF_CELL.match(c)]


def fissi(testo: str) -> list:
    out = []
    for m in re.findall(r"(?<![\d])(?:\+39[\s.]?)?(0\d{1,3}[\s./-]?\d{3,4}[\s./-]?\d{2,4})(?![\d])", testo):
        d = re.sub(r"\D", "", m)
        if 9 <= len(d) <= 11 and d not in out and not d.startswith("00"):
            out.append(d)
    return out

log = logging.getLogger("lead")
BASE = "https://www.autoscout24.it"
NON_SALONE = {"regioni", "cerca", "search", "lst", "informazioni", "recensioni", "chi-siamo"}


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _testo(html: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))


def giro() -> dict:
    from ..collectors.autoscout import AutoScoutCollector
    comuni = json.loads((Path(__file__).resolve().parents[2] / "data" / "comuni_sesto.json").read_text())
    asc = AutoScoutCollector()
    asc.min_delay_s, asc.max_delay_s = 0.7, 1.4
    get = asc.client.get
    saloni: dict = {}
    pagine_ok = 0
    for i, (nome, prov) in enumerate(comuni):
        for pag in range(1, 8):
            url = f"{BASE}/concessionari/regioni/lombardia/{_slug(nome)}/" + (f"?page={pag}" if pag > 1 else "")
            try:
                r = get(url)
            except Exception:
                break
            asc.pause()
            if r.status_code != 200:
                break
            pagine_ok += 1
            nuovi = 0
            for s in re.findall(r'href="(?:https://www\.autoscout24\.it)?/concessionari/([a-z0-9][a-z0-9-]+)/?["?#]', r.text):
                if s in NON_SALONE or s in saloni:
                    continue
                saloni[s] = {"comune_elenco": nome, "prov": prov}
                nuovi += 1
            if not nuovi:
                break
        if (i + 1) % 25 == 0:
            log.warning("LEADC avanzamento elenchi: %d/%d comuni, %d saloni", i + 1, len(comuni), len(saloni))
    log.warning("LEADC elenchi fatti: %d pagine, %d saloni", pagine_ok, len(saloni))
    import os
    if os.environ.get("LEADC_DB") == "1":     # anche i saloni AutoScout dei nostri annunci senza cellulare (per i fissi)
        from ..db import connect
        with connect() as conn:
            for r in conn.execute("SELECT chiave, prov FROM lead_tutti_dealer WHERE cardinality(cell) = 0 "
                                  "AND chiave LIKE '%%/concessionari/%%'").fetchall():
                s = r["chiave"].rstrip("/").split("/concessionari/")[-1].split("/")[0]
                if s and s not in saloni and s not in NON_SALONE:
                    saloni[s] = {"comune_elenco": None, "prov": r["prov"]}
        log.warning("LEADC con i saloni del database: %d", len(saloni))
    con_cell = 0
    for j, (s, info) in enumerate(saloni.items()):
        try:
            home = get(f"{BASE}/concessionari/{s}")
            asc.pause()
            chi = get(f"{BASE}/concessionari/{s}/chi-siamo")
            asc.pause()
            th = _testo(home.text) if home.status_code == 200 else ""
            tc = _testo(chi.text) if chi.status_code == 200 else ""
            m = re.search(r"<title>(.*?)</title>", home.text or "", re.S)
            nome = re.split(r"\s+[-|–]\s+", unescape(m.group(1)).strip())[0] if m else s
            cell = cell_validi(TEL.findall(tc + " " + th))
            fisso = fissi(tc)[:3]
            prezzi = sorted(int(p.replace(".", "")) for p in re.findall(r"€\s?(\d{1,3}(?:\.\d{3})+|\d{3,6})(?![\d.])", th)
                            if 500 <= int(p.replace(".", "")) <= 400000)
            ufficiale = bool(re.search(r"(dealer|concessionari[ao]|partner) ufficiale", tc + th, re.I))
            ind = re.search(r"(\d{5})\s+([A-ZÀ-Üa-zà-ü' .-]{2,40}?)\s*(?:-|\(|,)", tc)
            con_cell += bool(cell)
            log.warning("LEADC %s", json.dumps({
                "salone": s, "nome": nome[:150], "comune_elenco": info["comune_elenco"], "prov": info["prov"],
                "comune_pagina": ind.group(2).strip() if ind else None, "cell": cell[:4], "fissi": fisso,
                "prezzi": prezzi[:120], "ufficiale": ufficiale, "link": f"{BASE}/concessionari/{s}"}, ensure_ascii=False))
        except Exception as e:
            log.warning("LEADC errore %s: %s", s, str(e)[:150])
        if (j + 1) % 100 == 0:
            log.warning("LEADC avanzamento saloni: %d/%d, %d con cellulare", j + 1, len(saloni), con_cell)
    esito = {"comuni": len(comuni), "pagine_elenco": pagine_ok, "saloni": len(saloni), "con_cellulare": con_cell}
    log.warning("LEADC_FINE %s", json.dumps(esito))
    return esito
