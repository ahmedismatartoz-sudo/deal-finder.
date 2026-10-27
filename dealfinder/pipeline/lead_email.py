"""27/10 (cliente): email aziendali dei commercianti (solo attività, mai privati) per la campagna email grande.

Per ogni commerciante AutoScout24 di lead_tutti_dealer (e, se c'è, Google Maps di lead_maps con il sito):
  1) pagina AutoScout24 "chi siamo" e "informazioni" del commerciante: email scritte nella pagina e link al loro sito;
  2) sito del commerciante: home, /contatti, /contact, /chi-siamo: email scritte nella pagina (anche mailto:).
Si tengono solo indirizzi del commerciante stesso: niente PEC, niente noreply, niente indirizzi di AutoScout24 o
dei fornitori di siti. Gira nel cron (voce COLLECT_PRIMA "leademail@3"), riprende da dove era arrivato
(colonna tentato) e si ferma dopo LEADE_MAX pagine per giro. Nei log solo conteggi.

Consegna a richiesta: LEADE_ESPORTA=<etichetta> sul servizio web -> righe "LEADE {json}" (nome, comune, prov,
email, sito, fascia, prezzo mediano), una per commerciante con email.
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
import time
from html import unescape
from urllib.parse import urljoin, urlparse

log = logging.getLogger("lead")

SQL = """
CREATE TABLE IF NOT EXISTS lead_email (
    chiave     TEXT PRIMARY KEY,
    email      TEXT[] NOT NULL DEFAULT '{}',
    sito       TEXT,
    fonte      TEXT,
    tentato    TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,10}")
NO_DOMINI = re.compile(r"(autoscout24|scout24|subito\.it|example\.|sentry|wixpress|wix\.com|godaddy|aruba\.it$|"
                       r"register\.it|squarespace|wordpress|w3\.org|schema\.org|google|facebook|instagram|"
                       r"cloudflare|jquery|bootstrap|domain\.|yourdomain|email\.com$|sito\.it$|tuodominio|"
                       r"motork|dealerk|automobile\.it|autosupermarket|mobile\.de|png$|jpg$|gif$|webp$|svg$)", re.I)
NO_UTENTI = re.compile(r"^(noreply|no-reply|donotreply|privacy|dpo|gdpr|webmaster|postmaster|abuse|info@example)", re.I)
NO_SITI = re.compile(r"(autoscout24|scout24|facebook|instagram|google|youtube|tiktok|linkedin|whatsapp|wa\.me|"
                     r"twitter|x\.com|apple\.com|play\.google|subito|automobile\.it|mobile\.de|trustpilot|"
                     r"iubenda|cookie|w3\.org|schema\.org|cloudflare|gstatic|doubleclick|youtu\.be)", re.I)
PAGINE_SITO = ["", "contatti", "contact", "contatti/", "chi-siamo", "dove-siamo", "contattaci"]


def _email(testo: str) -> list[str]:
    testo = unescape(testo).replace("[at]", "@").replace("(at)", "@").replace(" @ ", "@")
    out = []
    for e in EMAIL.findall(testo):
        e = e.strip(".").lower()
        dom = e.split("@")[1]
        if "pec" in dom.split(".")[0] or dom.startswith("pec.") or "legalmail" in dom or "arubapec" in dom:
            continue
        if NO_DOMINI.search(dom) or NO_UTENTI.search(e) or len(e) > 60:
            continue
        if e not in out:
            out.append(e)
    return out[:3]


def _sito(html: str, base: str) -> str | None:
    for h in re.findall(r'href="(https?://[^"#?]+)', html):
        d = urlparse(h).netloc.lower()
        if d and not NO_SITI.search(d) and "." in d:
            return f"{urlparse(h).scheme}://{d}/"
    return None


def giro() -> dict:
    from ..collectors.autoscout import AutoScoutCollector
    from ..db import connect
    massimo = int(os.environ.get("LEADE_MAX", "2500"))
    asc = AutoScoutCollector()
    asc.min_delay_s, asc.max_delay_s = 0.7, 1.4
    with connect() as conn:
        conn.execute(SQL)
        conn.commit()
        da_fare = [r["chiave"] for r in conn.execute(
            "SELECT d.chiave FROM lead_tutti_dealer d LEFT JOIN lead_email e ON e.chiave = d.chiave "
            "WHERE e.chiave IS NULL AND d.chiave LIKE '%%/concessionari/%%' ORDER BY d.prov, d.chiave").fetchall()]
        try:
            maps = [(r["place_id"], r["sito"]) for r in conn.execute(
                "SELECT m.place_id, m.sito FROM lead_maps m LEFT JOIN lead_email e ON e.chiave = 'maps:' || m.place_id "
                "WHERE e.chiave IS NULL AND m.sito IS NOT NULL").fetchall()]
        except Exception:
            conn.rollback()
            maps = []
    pagine = trovati = provati = 0
    t0 = time.time()

    def salva(chiave, email, sito, fonte):
        with connect() as conn:
            conn.execute("INSERT INTO lead_email (chiave, email, sito, fonte) VALUES (%s,%s,%s,%s) "
                         "ON CONFLICT (chiave) DO UPDATE SET email = EXCLUDED.email, sito = EXCLUDED.sito, "
                         "fonte = EXCLUDED.fonte, tentato = now()", (chiave, email, sito, fonte))
            conn.commit()

    def dal_sito(sito):
        nonlocal pagine
        for p in PAGINE_SITO:
            try:
                r = asc.client.get(urljoin(sito, p), timeout=15)
                pagine += 1
                if r.status_code == 200 and "html" in (r.headers.get("content-type") or ""):
                    em = _email(r.text) + [m.lower() for m in re.findall(r'mailto:([^"?>\s]+)', r.text)]
                    em = [e for e in dict.fromkeys(em) if e in _email(e)]
                    if em:
                        return em[:3], urljoin(sito, p)
            except Exception:
                pagine += 1
            time.sleep(0.4)
        return [], None

    for url in da_fare:
        if pagine >= massimo:
            break
        provati += 1
        email, sito, fonte = [], None, None
        for u in (url.rstrip("/") + "/chi-siamo", url.rstrip("/") + "/informazioni"):
            try:
                r = asc.client.get(u)
                pagine += 1
                html = r.text if r.status_code == 200 else ""
            except Exception:
                html = ""
            asc.pause()
            email = _email(re.sub(r"<script.*?</script>", " ", html, flags=re.S))
            sito = sito or _sito(html, u)
            if email:
                fonte = u
                break
        if not email and sito:
            email, fonte = dal_sito(sito)
        salva(url, email, sito, fonte)
        trovati += bool(email)
        if provati % 100 == 0:
            log.warning("LEADE avanzamento: %d commercianti, %d con email, %d pagine, %ds", provati, trovati, pagine,
                        int(time.time() - t0))
    for pid, sito in maps:
        if pagine >= massimo:
            break
        provati += 1
        email, fonte = dal_sito(sito)
        salva("maps:" + pid, email, sito, fonte)
        trovati += bool(email)
    with connect() as conn:
        tot = conn.execute("SELECT count(*) n, count(*) FILTER (WHERE cardinality(email) > 0) c FROM lead_email").fetchone()
        resto = conn.execute("SELECT count(*) n FROM lead_tutti_dealer d LEFT JOIN lead_email e ON e.chiave = d.chiave "
                             "WHERE e.chiave IS NULL AND d.chiave LIKE '%%/concessionari/%%'").fetchone()["n"]
    esito = {"provati": provati, "nuove_email": trovati, "pagine": pagine, "totale_provati": tot["n"],
             "totale_con_email": tot["c"], "da_provare": resto}
    log.warning("LEADE_FINE %s", esito)
    return esito


def _fascia(prezzi: list[int]) -> tuple[str, int | None]:
    p = [x for x in prezzi if x and x > 300]
    if not p:
        return "?", None
    m = int(statistics.median(p))
    return ("utilitarie" if m < 8000 else "medie" if m < 15000 else "alte" if m < 50000 else "supercar"), m


def esporta() -> int:
    """Righe LEADE {json} nei log: dati aziendali pubblici dei commercianti con email (mai privati)."""
    from ..db import connect
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.chiave, e.email, e.sito, e.fonte, d.nome, d.comune, d.prov FROM lead_email e "
            "JOIN lead_tutti_dealer d ON d.chiave = e.chiave WHERE cardinality(e.email) > 0").fetchall()
        ann: dict = {}
        for r in conn.execute("SELECT chiave, prezzo FROM lead_tutti_ann").fetchall():
            ann.setdefault(r["chiave"], []).append(r["prezzo"])
        try:
            rows += conn.execute(
                "SELECT e.chiave, e.email, e.sito, e.fonte, m.nome, m.comune, m.prov FROM lead_email e "
                "JOIN lead_maps m ON 'maps:' || m.place_id = e.chiave WHERE cardinality(e.email) > 0").fetchall()
        except Exception:
            conn.rollback()
    for r in rows:
        fascia, med = _fascia(ann.get(r["chiave"], []))
        log.warning("LEADE %s", json.dumps({"nome": r["nome"], "comune": r["comune"], "prov": r["prov"],
                                            "email": list(r["email"]), "sito": r["sito"], "fonte": r["fonte"],
                                            "fascia": fascia, "prezzo_mediano": med,
                                            "auto": len(ann.get(r["chiave"], []))}, ensure_ascii=False))
    log.warning("LEADE_ESPORTATI %d", len(rows))
    return len(rows)
