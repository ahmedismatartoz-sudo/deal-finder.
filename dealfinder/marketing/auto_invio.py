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
                from ..pipeline.lead_maps import SQL as _MAPS_SQL
                conn.execute(_MAPS_SQL)                   # tabella dei commercianti da Google Maps (vuota se non c'è Apify)
                rows = conn.execute(
                    # 28/10 sera: anche i commercianti trovati su Google Maps (chiave "maps:<place_id>"), stessa zona
                    "SELECT e.chiave, e.email, e.sito FROM lead_email e LEFT JOIN lead_tutti_dealer d ON d.chiave = e.chiave "
                    "LEFT JOIN lead_maps m ON e.chiave = 'maps:' || m.place_id "
                    "WHERE cardinality(e.email) > 0 AND (%(prov)s::text[] IS NULL OR upper(COALESCE(d.prov, m.prov)) = ANY(%(prov)s))",
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


TESTO_2_11 = ["Ciao,", "trovo privati che vendono auto sotto mercato con un nostro software, che scarta truffe, annunci falsi e "
              "auto incidentate. Vi fissiamo noi l'appuntamento con il privato: voi comprate e rivendete, io prendo una "
              "piccola commissione.", "Ci sentiamo?"]
ZONA_2_11 = ("MI", "MB", "VA", "CO", "LC", "BG", "LO")
IMMAGINE = re.compile(r"\.(png|jpe?g|gif|webp|avif|svg|bmp)$", re.I)
NON_COMMERCIALI = re.compile(r"^(bewerbung|job|jobs|lavora|careers?|rtdprivacy|ufficio\.stampa|stampa|press|hr|recruiting|cv|"
                             r"curriculum|de\.)", re.I)


def invia_2_11(tag: str) -> dict:
    """2/11 notte (cliente: "mettile tutte su Brevo subito e mandiamo a tutti"): le email raccolte della zona
    (MI, MB, VA, CO, LC, BG, LO) mai caricate prima (non in brevo_inviati, che contiene i 1.345 contatti cancellati),
    senza doppioni né indirizzi finti, in una lista Brevo nuova e il testo corto approvato il 1/11 sera. Una volta.
    Tetto BREVO_TETTO_2_11 (predefinito 1.450, limite del piano). Nei log solo conteggi."""
    from collections import Counter
    from ..db import connect
    tetto = int(os.environ.get("BREVO_TETTO_2_11", "1450"))
    with connect() as conn:
        gia = {r["email"] for r in conn.execute("SELECT email FROM brevo_inviati").fetchall()}
        rows = conn.execute(
            "SELECT e.email, upper(COALESCE(d.prov, m.prov)) AS prov FROM lead_email e "
            "LEFT JOIN lead_tutti_dealer d ON d.chiave = e.chiave LEFT JOIN lead_maps m ON e.chiave = 'maps:' || m.place_id "
            "WHERE cardinality(e.email) > 0 AND upper(COALESCE(d.prov, m.prov)) = ANY(%s)",
            ([p.strip().upper() for p in (os.environ.get("BREVO_ZONA") or ",".join(ZONA_2_11)).split(",") if p.strip()],)).fetchall()
    scelte, per_prov, st = [], Counter(), Counter()
    visti = set()
    for r in rows:
        for e in r["email"] or []:
            e = (e or "").strip().lower()
            if not e or e in visti:
                continue
            visti.add(e)
            if e in gia:
                st["gia_contattate"] += 1
            elif BAD.search(e) or IMMAGINE.search(e) or NON_COMMERCIALI.search(e) or not re.match(r"^[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}$", e):
                st["scartate"] += 1
            else:
                scelte.append(e)
                per_prov[r["prov"]] += 1
    scelte = scelte[:tetto]
    lg.warning("BREVO_2_11 %d email da mandare (per provincia %s), %s", len(scelte), dict(per_prov), dict(st))
    if os.environ.get("BREVO_2_11_PROVA") == "1" or not scelte:
        return {"email": len(scelte)}
    with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=120) as c:
        ok = _manda(c, f"Skovo zona 02-11 {tag}", "auto da privati sotto mercato", _pagina(TESTO_2_11), scelte)
    if ok:
        with connect() as conn:
            for e in scelte:
                conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (e,))
            conn.commit()
    lg.warning("BREVO_2_11 fatto: %s", "inviata" if ok else "NON inviata")
    return {"email": len(scelte), "inviata": ok}


def rimetti_contatti(tag: str) -> None:
    """2/11 (cliente: "dalle email rimetti i contatti vecchi fino ad arrivare a 1500 su Brevo"): ricarica in Brevo i contatti
    vecchi scelti (data/brevo_rimetti.json, solo hash: non bloccati/disiscritti, prima chi aveva aperto, poi la zona),
    presi da brevo_inviati. Solo caricamento in una lista: NESSUN invio. Si ferma a BREVO_TETTO (1.498) contatti."""
    import hashlib
    from ..db import connect
    scelti = set(json.loads((_DATA / "brevo_rimetti.json").read_text()))
    tetto = int(os.environ.get("BREVO_TETTO", "1498"))
    with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=120) as c, connect() as conn:
        ora = c.get(f"{API}/contacts", params={"limit": 1}).json().get("count") or 0
        attuali = set()
        off = 0
        while True:
            cs = c.get(f"{API}/contacts", params={"limit": 1000, "offset": off}).json().get("contacts") or []
            attuali |= {x["email"].lower() for x in cs if x.get("email")}
            if len(cs) < 1000:
                break
            off += 1000
        emails = sorted({r["email"].lower() for r in conn.execute("SELECT email FROM brevo_inviati").fetchall()
                         if hashlib.sha1(r["email"].lower().encode()).hexdigest()[:16] in scelti} - attuali)
        emails = emails[:max(0, tetto - ora)]
        lg.warning("RIMETTI %s: in Brevo %d, trovati %d di %d scelti, da caricare %d", tag, ora, len(emails), len(scelti), len(emails))
        if not emails:
            return
        r = c.post(f"{API}/contacts/lists", json={"name": f"Skovo contatti vecchi rimessi 02-11 {tag}", "folderId": _cartella(c)})
        r.raise_for_status()
        lid = r.json()["id"]
        r = c.post(f"{API}/contacts/import", json={"jsonBody": [{"email": e} for e in emails], "listIds": [lid],
                                                   "updateExistingContacts": True, "emptyContactsAttributes": False})
        lg.warning("RIMETTI %s: lista %s, import %s %s", tag, lid, r.status_code, r.text[:200])


def invia_rimessi(tag: str, elenco) -> dict:
    """2/11 (cliente: "prepara l'email con esempi e mandala a tutti questi"): una campagna con il testo approvato il 2/11
    (TESTO_2_11) e 3 auto di esempio dal sito (privati, zona, almeno 10 confronti, margine >= 1.500, foto visibili),
    alla lista dei contatti vecchi rimessi oggi. Una volta per etichetta."""
    cand = []
    for x in elenco():
        m = re.search(r"\((\w\w)\)", str(x.get("zona") or ""))
        if (not m or m.group(1).upper() not in PROV_100 or not x.get("R") or not x.get("anno") or (x.get("n") or 0) < 10
                or (x.get("km") or 10**9) > 120000 or not (3000 <= (x.get("prezzo") or 0) <= 25000) or (x.get("margine") or 0) < 1500):
            continue
        cand.append(x)
    cand.sort(key=lambda x: -(x.get("margine") or 0))
    auto, modelli = [], set()
    for x in cand:
        mod = " ".join(str(x["auto"]).lower().split()[:2])
        if mod in modelli or not _foto_ok(_card(x)):
            continue
        auto.append(x); modelli.add(mod)
        if len(auto) == 3:
            break
    lg.warning("RIMESSI_ESEMPI %s: candidate %d, scelte %s", tag, len(cand), [(a["id"], a["auto"], a["prezzo"], a["margine"]) for a in auto])
    if len(auto) < 2:
        return {"inviata": False, "motivo": "esempi insufficienti"}
    nomi = " ".join(str(auto[0]["auto"]).split()[:2])
    html = _pagina(TESTO_2_11, f'<p {P}>Alcuni esempi di oggi, vicino a voi:</p>'
                   '<div style="margin:6px 0 10px">' + "".join(_card(a) for a in auto) + "</div>")
    with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=120) as c:
        lid = None
        off = 0
        while lid is None:
            ls = c.get(f"{API}/contacts/lists", params={"limit": 50, "offset": off}).json().get("lists") or []
            lid = next((l["id"] for l in ls if str(l.get("name", "")).startswith("Skovo contatti vecchi rimessi 02-11")), None)
            if len(ls) < 50:
                break
            off += 50
        if lid is None:
            lg.warning("RIMESSI_ESEMPI %s: lista dei contatti rimessi non trovata", tag)
            return {"inviata": False}
        n = c.get(f"{API}/contacts/lists/{lid}").json().get("uniqueSubscribers")
        r = c.post(f"{API}/emailCampaigns", json={"name": f"Skovo esempi contatti rimessi 02-11 {tag}",
                                                  "subject": f"{nomi} da privato sotto mercato", "sender": MITTENTE,
                                                  "replyTo": MITTENTE["email"], "htmlContent": html,
                                                  "recipients": {"listIds": [lid]}})
        if r.status_code >= 300:
            lg.warning("RIMESSI_ESEMPI %s: campagna non creata %s %s", tag, r.status_code, r.text[:200])
            return {"inviata": False}
        s = c.post(f"{API}/emailCampaigns/{r.json()['id']}/sendNow")
        lg.warning("RIMESSI_ESEMPI %s: campagna %s alla lista %s (%s contatti), invio %s", tag, r.json()["id"], lid, n, s.status_code)
        return {"inviata": s.status_code < 300, "contatti": n}


FASCE_2_11 = {"utilitarie": (3000, 8000), "medie": (8000, 15000), "alte": (15000, 30000), "supercar": (30000, 300000)}


def fasce_e_esempi(tag: str, elenco) -> dict:
    """2/11 (cliente: "classifica ogni lead per la sua fascia e tre esempi per ogni fascia"): ogni contatto Brevo va nella
    fascia delle auto che vende (prezzi dei suoi annunci; se mancano, la fascia salvata il 1/11), una lista Brevo per fascia,
    e per ogni fascia una campagna in BOZZA (non inviata) con il testo del 2/11 e 3 auto di esempio del sito."""
    import hashlib
    from collections import Counter
    from ..db import connect
    vecchie = json.loads((_DATA / "brevo_fasce.json").read_text())
    with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=120) as c:
        emails, off = [], 0
        while True:
            cs = c.get(f"{API}/contacts", params={"limit": 1000, "offset": off}).json().get("contacts") or []
            emails += [x["email"].lower() for x in cs if x.get("email") and not x.get("emailBlacklisted")]
            if len(cs) < 1000:
                break
            off += 1000
        with connect() as conn:
            prezzi: dict = {}
            for r in conn.execute("SELECT e.email, a.prezzo FROM lead_email e JOIN lead_tutti_ann a ON a.chiave = e.chiave "
                                  "WHERE cardinality(e.email) > 0").fetchall():
                for e in r["email"] or []:
                    prezzi.setdefault((e or "").lower(), []).append(r["prezzo"])
        fascia, fonte = {}, Counter()
        for e in emails:
            p = [x for x in prezzi.get(e, []) if x and x > 300]
            h = hashlib.sha1(e.encode()).hexdigest()[:16]
            if p:
                m = statistics.median(p)
                f = "utilitarie" if m < 8000 else "medie" if m < 15000 else "alte" if m < 30000 else "supercar"
                fonte["annunci"] += 1
            elif vecchie.get(h):
                f = {"premium": "alte"}.get(vecchie[h], vecchie[h])
                fonte["fascia_1_11"] += 1
            else:
                f = "medie"
                fonte["senza_dati_medie"] += 1
            fascia[e] = f
        per = Counter(fascia.values())
        lg.warning("FASCE %s: contatti %d, per fascia %s, fonte %s", tag, len(emails), dict(per), dict(fonte))
        # esempi
        tutte = [x for x in elenco() if x.get("R") and x.get("anno") and (x.get("n") or 0) >= 10
                 and (x.get("km") or 10**9) <= 150000 and (x.get("margine") or 0) >= 1500
                 and (m_ := re.search(r"\((\w\w)\)", str(x.get("zona") or ""))) and m_.group(1).upper() in PROV_100]
        esito = {}
        for f, (lo, hi) in FASCE_2_11.items():
            cand = sorted([x for x in tutte if lo <= (x.get("prezzo") or 0) < hi], key=lambda x: -(x.get("margine") or 0))
            auto, modelli = [], set()
            for x in cand:
                mod = " ".join(str(x["auto"]).lower().split()[:2])
                if mod in modelli or not _foto_ok(_card(x)):
                    continue
                auto.append(x); modelli.add(mod)
                if len(auto) == 3:
                    break
            lg.warning("FASCE %s esempi %s: %s", tag, f, [(a["id"], a["auto"], a["anno"], a["km"], a["prezzo"], a["margine"]) for a in auto])
            dest = [e for e, ff in fascia.items() if ff == f]
            if not dest:
                continue
            r = c.post(f"{API}/contacts/lists", json={"name": f"Skovo fascia {f} 02-11 {tag}", "folderId": _cartella(c)})
            r.raise_for_status()
            lid = r.json()["id"]
            c.post(f"{API}/contacts/import", json={"jsonBody": [{"email": e} for e in dest], "listIds": [lid],
                                                   "updateExistingContacts": True, "emptyContactsAttributes": False})
            cid = None
            if len(auto) >= 2:
                nomi = " ".join(str(auto[0]["auto"]).split()[:2])
                html = _pagina(TESTO_2_11, f'<p {P}>Alcuni esempi di oggi, vicino a voi:</p>'
                               '<div style="margin:6px 0 10px">' + "".join(_card(a) for a in auto) + "</div>")
                for _ in range(6):
                    time.sleep(30)
                    rr = c.post(f"{API}/emailCampaigns", json={"name": f"Skovo fascia {f} 02-11 {tag} (bozza)",
                                                               "subject": f"{nomi} da privato sotto mercato", "sender": MITTENTE,
                                                               "replyTo": MITTENTE["email"], "htmlContent": html,
                                                               "recipients": {"listIds": [lid]}})
                    if rr.status_code < 300:
                        cid = rr.json()["id"]
                        break
            esito[f] = {"contatti": len(dest), "lista": lid, "bozza": cid, "esempi": len(auto)}
        lg.warning("FASCE %s fatto: %s", tag, json.dumps(esito))
        return esito


# 3/11 (cliente: "togli quelle che si sono lamentate e manda a tutti i 1500, prendi gente nuova mai contattata prima")
ESCLUSI_3_11 = re.compile(r"(officinemulino|cosvalauto|autoeautovarese|srauto|loconte)", re.I)


def nuovi_3_11(tag: str, elenco) -> dict:
    """Commercianti MAI contattati (non in brevo_inviati), prima la zona entro 100 km poi il resto del Nord, fino al
    tetto del piano Brevo (1.498): per fare posto si tolgono da Brevo i contatti già usati (restano in brevo_inviati e
    non vengono mai ricontattati per errore). Ogni contatto nella fascia delle auto che vende, una lista e una
    campagna per fascia con il testo approvato del 2/11 e 3 auto di esempio di oggi (metodo nuovo), INVIATE subito.
    Esclusi chi si è lamentato e i nomi esclusi. Nei log solo conteggi."""
    import hashlib  # noqa: F401
    from collections import Counter
    from ..db import connect
    tetto = int(os.environ.get("BREVO_TETTO", "1498"))
    nord = ("MI", "MB", "VA", "CO", "LC", "BG", "LO", "PV", "CR", "BS", "NO", "PC", "MN", "SO", "VC", "BI", "VB",
            "AL", "AT", "TO", "CN", "GE", "SV", "PR", "RE", "MO", "BO", "VR", "VI", "PD", "VE", "TV", "TN", "BZ")
    st = Counter()
    with connect() as conn:
        gia = {r["email"] for r in conn.execute("SELECT email FROM brevo_inviati").fetchall()}
        rows = conn.execute(
            "SELECT e.chiave, e.email, upper(COALESCE(d.prov, m.prov)) AS prov FROM lead_email e "
            "LEFT JOIN lead_tutti_dealer d ON d.chiave = e.chiave LEFT JOIN lead_maps m ON e.chiave = 'maps:' || m.place_id "
            "WHERE cardinality(e.email) > 0").fetchall()
        prezzi: dict = {}
        for r in conn.execute("SELECT chiave, prezzo FROM lead_tutti_ann WHERE prezzo > 300").fetchall():
            prezzi.setdefault(r["chiave"], []).append(r["prezzo"])
    cand, visti = [], set()
    for r in rows:
        pv = (r["prov"] or "").upper()
        # 3/11 14:46 (cliente): solo la zona; se non si arriva a 1.500 si aggiungono quelli che non hanno mai aperto
        rango = 0 if pv in PROV_100 or pv == "PC" else 2
        for e in r["email"] or []:
            e = (e or "").strip().lower()
            if not e or e in visti:
                continue
            visti.add(e)
            if e in gia:
                st["gia_contattate"] += 1
            elif ESCLUSI_3_11.search(e):
                st["esclusi"] += 1
            elif BAD.search(e) or IMMAGINE.search(e) or NON_COMMERCIALI.search(e) or \
                    not re.match(r"^[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}$", e):
                st["scartate"] += 1
            elif rango < 2:
                cand.append((rango, e, r["chiave"]))
            else:
                st["fuori_nord"] += 1
    cand.sort()
    cand = cand[:tetto]
    st["nuove_zona_100km"] = sum(1 for x in cand if x[0] == 0)
    st["nuove_resto_nord"] = sum(1 for x in cand if x[0] == 1)
    lg.warning("NUOVI_3_11 %s: %d email nuove scelte, %s", tag, len(cand), dict(st))
    if not cand:
        return dict(st)
    # esempi di oggi (metodo nuovo), zona entro 100 km
    tutte = [x for x in elenco() if x.get("R") and x.get("anno") and (x.get("n") or 0) >= 8
             and (x.get("km") or 10**9) <= 150000 and (x.get("margine") or 0) >= 1500 and x.get("regole")
             and (m_ := re.search(r"\((\w\w)\)", str(x.get("zona") or ""))) and m_.group(1).upper() in PROV_100]
    esempi = {}
    for f, (lo, hi) in FASCE_2_11.items():
        cs = sorted([x for x in tutte if lo <= (x.get("prezzo") or 0) < hi], key=lambda x: -(x.get("margine") or 0))
        auto, modelli = [], set()
        for x in cs:
            mod = " ".join(str(x["auto"]).lower().split()[:2])
            if mod in modelli or not _foto_ok(_card(x)):
                continue
            auto.append(x); modelli.add(mod)
            if len(auto) == 3:
                break
        esempi[f] = auto
        lg.warning("NUOVI_3_11 esempi %s: %s", f, [(a["id"], a["auto"], a["anno"], a["km"], a["prezzo"], a["margine"]) for a in auto])
    # le fasce senza esempi usano quelli della fascia più vicina
    for f, alt in (("utilitarie", "medie"), ("medie", "utilitarie"), ("alte", "medie"), ("supercar", "alte")):
        if len(esempi.get(f) or []) < 2:
            esempi[f] = esempi.get(alt) or []
    fascia = {}
    for _, e, ch in cand:
        p = prezzi.get(ch) or []  # noqa
        m = statistics.median(p) if p else 11000
        fascia[e] = "utilitarie" if m < 8000 else "medie" if m < 15000 else "alte" if m < 30000 else "supercar"
    with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=120) as c:
        # posto in Brevo: via i contatti già usati (sono in brevo_inviati: non si perdono e non si ricontattano)
        attuali, off = [], 0
        while True:
            cs = c.get(f"{API}/contacts", params={"limit": 1000, "offset": off}).json().get("contacts") or []
            attuali += [x["email"].lower() for x in cs if x.get("email")]
            if len(cs) < 1000:
                break
            off += 1000
        # chi è già in Brevo e NON ha mai aperto una nostra email resta e la riceve; chi ha aperto fa posto ai nuovi
        mai_aperte, aperte = [], []
        for e in attuali:
            if ESCLUSI_3_11.search(e):
                aperte.append(e)
                continue
            try:
                cs_ = c.get(f"{API}/contacts/{e}/campaignStats").json() or {}
                (aperte if cs_.get("opened") or cs_.get("clicked") else mai_aperte).append(e)
            except Exception:
                aperte.append(e)
            time.sleep(0.12)
        posto = max(0, tetto - len(cand))
        mai_aperte = mai_aperte[:posto]
        lg.warning("NUOVI_3_11 %s: già in Brevo %d, mai aperto %d (ricevono di nuovo), aperto o esclusi %d", tag,
                   len(attuali), len(mai_aperte), len(aperte))
        tenere = set(mai_aperte)
        da_togliere = max(0, len(attuali) + len(cand) - tetto)
        tolti = 0
        with connect() as conn:
            for e in sorted(attuali, key=lambda x: x in tenere):
                if tolti >= da_togliere:
                    break
                conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (e,))
                conn.commit()
                r = c.delete(f"{API}/contacts/{e}")
                if r.status_code < 300:
                    tolti += 1
                time.sleep(0.15)
        lg.warning("NUOVI_3_11 %s: in Brevo %d, tolti %d già contattati per fare posto", tag, len(attuali), tolti)
        for e in mai_aperte:
            fascia.setdefault(e, "medie")
        esito = {}
        for f in FASCE_2_11:
            dest = [e for e, ff in fascia.items() if ff == f]
            auto = esempi.get(f) or []
            if not dest:
                continue
            schede = (f'<p {P}>Alcuni esempi di oggi, vicino a voi:</p><div style="margin:6px 0 10px">'
                      + "".join(_card(a) for a in auto) + "</div>") if auto else ""
            oggetto = (" ".join(str(auto[0]["auto"]).split()[:2]) + " da privato sotto mercato") if auto else "auto da privati sotto mercato"
            ok = _manda(c, f"Skovo nuovi {f} 03-11 {tag}", oggetto, _pagina(TESTO_2_11, schede), dest)
            if ok:
                with connect() as conn:
                    for e in dest:
                        conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (e,))
                    conn.commit()
            esito[f] = {"contatti": len(dest), "esempi": len(auto), "inviata": ok}
        lg.warning("NUOVI_3_11 %s fatto: %s", tag, json.dumps(esito))
        return esito
