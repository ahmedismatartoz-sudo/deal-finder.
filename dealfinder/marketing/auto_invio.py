"""28/10 notte (cliente: "raccogli in modo aggressivo le email e fai partire subito le email").

Giro continuo sul servizio web (AUTO_INVIO=1):
1. cerca le email dei commercianti non ancora controllati (pipeline/lead_email.giro, in parallelo);
2. prende le email nuove valide (dominio del sito o caselle gratuite; niente segnaposto, PEC, fornitori di siti),
   mai già caricate in Brevo (tabella brevo_inviati, riempita all'avvio con tutti i contatti Brevo);
3. le profila per fascia (prezzo mediano delle auto del commerciante) e, appena sono almeno AUTO_MIN_N,
   le carica in Brevo e manda subito: le prime AUTO_TESTO_RESTANTI con il testo corto, le altre con 2 auto vere
   del sito della loro fascia (ribasso per fascia in _card);
4. non supera mai AUTO_TETTO_CONTATTI contatti in Brevo (limite del piano del cliente: 1.500).
Nei log solo conteggi (AUTO_INVIO ...), mai indirizzi.
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
import time
from pathlib import Path

import httpx

from .brevo_liste import API, MITTENTE, _card, _cartella, _chiave, _foto_ok

lg = logging.getLogger("dealfinder.marketing")
_DATA = Path(__file__).resolve().parents[2] / "data"
FREE = ("gmail.com", "libero.it", "tiscali.it", "hotmail.it", "hotmail.com", "outlook.it", "outlook.com", "yahoo.it",
        "yahoo.com", "alice.it", "virgilio.it", "tin.it", "live.it", "icloud.com", "email.it", "fastwebnet.it")
BAD = re.compile(r"(dominio|myemail|example|test@|mario\.rossi|tuaemail|^nome@|^email@|ferrarimodena|bolognapremium|"
                 r"pagitaly|webador|jimdo|service|supporto|assistenza|wix|sentry|pec\.|legalmail)", re.I)
PROV_100 = {"MI", "MB", "VA", "CO", "LC", "BG", "LO", "PV", "CR", "NO"}
FASCE = {"utilitarie": (3000, 8000), "medie": (8000, 15000), "alte": (15000, 50000), "supercar": (15000, 200000)}
TEL, TELV = "+393482425633", "348 242 5633"
MAIL = ("mailto:info@skovo.app?subject=Sono%20interessato%20-%20richiamatemi&body=Buongiorno%2C%20sono%20interessato."
        "%20Il%20mio%20numero%20%C3%A8%3A%20")
P = 'style="margin:0 0 14px;font-size:15px;line-height:1.55;color:#222"'


def _dom(u):
    if not u:
        return ""
    d = re.sub(r"^https?://(www\.)?", "", u).split("/")[0].lower()
    return ".".join(d.split(".")[-2:])


def _scegli(email: list, sito) -> str | None:
    sd = _dom(sito)
    ems = [e.lower() for e in email if not BAD.search(e)]
    good = [e for e in ems if sd and e.split("@")[1].endswith(sd)] or [e for e in ems if e.split("@")[1] in FREE] or ems
    if not good:
        return None
    return next((x for x in good if x.startswith("info")), good[0])


def _pagina(corpo: list[str], schede: str = "") -> str:
    ps = "".join(f"<p {P}>{t}</p>" for t in corpo[:-1])
    return ('<!DOCTYPE html>\n<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
            'initial-scale=1"><title>Skovo</title></head>\n<body style="margin:0;padding:0;background:#ffffff;'
            'font-family:Arial,Helvetica,sans-serif">\n<div style="max-width:560px;padding:20px 18px">' + ps + schede
            + f"<p {P}>{corpo[-1]}</p>"
            + f'<p style="margin:18px 0 6px;font-size:15px">📞 <a href="tel:{TEL}" style="color:#1a56db;font-weight:bold">Chiamami: {TELV}</a></p>'
            + f'<p style="margin:0 0 18px;font-size:15px">✋ <a href="{MAIL}" style="color:#1a56db;font-weight:bold">Sono interessato, richiamatemi</a></p>'
            + '<p style="margin:0 0 22px;font-size:15px;color:#222">Skovo</p>'
            + '<p style="margin:0;font-size:12px;color:#888">Se non vi interessa rispondete "no" oppure <a href="{{ unsubscribe }}" '
            + 'style="color:#888">cancellatevi qui</a>: non vi scriverò più.</p></div></body></html>')


TESTO_CORTO = ["Ciao,", "trovo privati che vendono auto sotto mercato tramite un portale. Voi comprate da loro e rivendete, "
               "io prendo una piccola commissione.", "Ci sentiamo?"]


def _auto(elenco, fascia: str, usate: set) -> list[dict]:
    lo, hi = FASCE[fascia]
    cand = []
    for x in elenco() if elenco else []:
        m = re.search(r"\((\w\w)\)", str(x.get("zona") or ""))
        if (x.get("id") in usate or not m or m.group(1).upper() not in PROV_100 or not x.get("R") or not x.get("anno")
                or (x.get("n") or 0) < 10 or (x.get("km") or 10**9) > 120000 or not (lo <= (x.get("prezzo") or 0) < hi)
                or (x.get("margine") or 0) < 1500):
            continue
        cand.append(x)
    cand.sort(key=lambda x: -(x.get("margine") or 0))
    out = []
    for x in cand:
        if _foto_ok(_card(x)):
            out.append(x)
        if len(out) == 2:
            break
    return out


def _fascia(prezzi):
    p = [x for x in prezzi if x and x > 300]
    if not p:
        return "medie"
    m = statistics.median(p)
    return "utilitarie" if m < 8000 else "medie" if m < 15000 else "alte" if m < 50000 else "supercar"


def _semina(c: httpx.Client, conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS brevo_inviati (email TEXT PRIMARY KEY, quando TIMESTAMPTZ DEFAULT now())")
    conn.commit()
    off, n = 0, 0
    while True:
        r = c.get(f"{API}/contacts", params={"limit": 1000, "offset": off})
        r.raise_for_status()
        cs = r.json().get("contacts") or []
        for x in cs:
            if x.get("email"):
                conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (x["email"].lower(),))
                n += 1
        conn.commit()
        if len(cs) < 1000:
            break
        off += 1000
    for f in _DATA.glob("marketing_gruppi*.json"):          # anche le liste già usate (contatti cancellati in Brevo)
        try:
            for es in json.loads(f.read_text()).values():
                for e in es:
                    conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (e.lower(),))
        except Exception:
            pass
    conn.commit()
    lg.warning("AUTO_INVIO contatti Brevo segnati come già usati: %d", n)


def _manda(c: httpx.Client, nome: str, oggetto: str, html: str, emails: list[str]) -> bool:
    r = c.post(f"{API}/contacts/lists", json={"name": nome, "folderId": _cartella(c)})
    r.raise_for_status()
    lid = r.json()["id"]
    c.post(f"{API}/contacts/import", json={"jsonBody": [{"email": e} for e in emails], "listIds": [lid],
                                           "updateExistingContacts": True, "emptyContactsAttributes": False})
    for _ in range(8):                                          # Brevo finisce di importare
        time.sleep(60)
        r = c.post(f"{API}/emailCampaigns", json={"name": nome, "subject": oggetto, "sender": MITTENTE,
                                                  "replyTo": MITTENTE["email"], "htmlContent": html,
                                                  "recipients": {"listIds": [lid]}})
        if r.status_code < 300:
            s = c.post(f"{API}/emailCampaigns/{r.json()['id']}/sendNow")
            lg.warning("AUTO_INVIO %s inviata a %d (esito %s)", nome, len(emails), s.status_code)
            return s.status_code < 300
    lg.warning("AUTO_INVIO %s NON inviata: lista non pronta", nome)
    return False


def ciclo(elenco=None) -> None:
    from ..db import connect
    from ..pipeline import lead_email as LE
    key = _chiave()
    tetto = int(os.environ.get("AUTO_TETTO_CONTATTI", "1450"))
    minimo = int(os.environ.get("AUTO_MIN_N", "30"))
    pausa = int(os.environ.get("AUTO_MIN", "20")) * 60
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c, connect() as conn:
        _semina(c, conn)
    usate_auto: set = set()
    giro_n = 0
    while True:
        giro_n += 1
        try:
            LE.giro()
        except Exception as e:
            lg.warning("AUTO_INVIO ricerca email non riuscita: %s", str(e)[:200])
        try:
            with connect() as conn:
                rows = conn.execute(
                    "SELECT e.chiave, e.email, e.sito FROM lead_email e LEFT JOIN lead_tutti_dealer d ON d.chiave = e.chiave "
                    "WHERE cardinality(e.email) > 0 AND (%(prov)s::text[] IS NULL OR upper(d.prov) = ANY(%(prov)s))",
                    {"prov": [x.strip().upper() for x in os.environ["AUTO_PROV"].split(",")] if os.environ.get("AUTO_PROV") else None}).fetchall()
                ann: dict = {}
                for r in conn.execute("SELECT chiave, prezzo FROM lead_tutti_ann").fetchall():
                    ann.setdefault(r["chiave"], []).append(r["prezzo"])
                gia = {r["email"] for r in conn.execute("SELECT email FROM brevo_inviati").fetchall()}
            nuove, visti = {}, set()
            for r in rows:
                e = _scegli(list(r["email"]), r["sito"])
                if not e or e in gia or e in visti:
                    continue
                if any(x.lower() in gia for x in r["email"]):
                    continue
                visti.add(e)
                nuove.setdefault(_fascia(ann.get(r["chiave"], [])), []).append(e)
            tot = sum(len(v) for v in nuove.values())
            lg.warning("AUTO_INVIO giro %d: %d email nuove %s", giro_n, tot, {k: len(v) for k, v in nuove.items()})
            if tot >= minimo:
                with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
                    cnt = c.get(f"{API}/contacts", params={"limit": 1}).json().get("count") or 0
                    spazio = tetto - cnt
                    if spazio <= 0:
                        lg.warning("AUTO_INVIO fermo: %d contatti in Brevo (tetto %d)", cnt, tetto)
                    restanti = int(os.environ.get("AUTO_TESTO_RESTANTI", "0"))
                    ts = time.strftime("%d-%m %H%M")
                    for fascia, ems in nuove.items():
                        if spazio <= 0:
                            break
                        ems = ems[:spazio]
                        corto, esempi = ems[:restanti], ems[restanti:]
                        restanti -= len(corto)
                        with connect() as conn:
                            for e in ems:
                                conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (e,))
                            conn.commit()
                        if corto:
                            _manda(c, f"Skovo testo {fascia} {ts}", "auto da privati sotto mercato", _pagina(TESTO_CORTO), corto)
                        if esempi:
                            auto = _auto(elenco, fascia, usate_auto) or _auto(elenco, "alte" if fascia == "supercar" else "medie", usate_auto)
                            if len(auto) == 2:
                                usate_auto.update(a["id"] for a in auto)
                                nomi = " ".join(str(auto[0]["auto"]).split()[:2])
                                html = _pagina(TESTO_CORTO, f'<p {P}>Due esempi di oggi, vicino a voi:</p>'
                                               '<div style="margin:6px 0 10px">' + "".join(_card(a) for a in auto) + "</div>")
                                _manda(c, f"Skovo esempi {fascia} {ts}", f"{nomi} da privato sotto mercato", html, esempi)
                            else:
                                _manda(c, f"Skovo testo {fascia} {ts}", "auto da privati sotto mercato", _pagina(TESTO_CORTO), esempi)
                        spazio -= len(ems)
        except Exception as e:
            lg.warning("AUTO_INVIO giro non riuscito: %s", str(e)[:300])
        time.sleep(pausa)
