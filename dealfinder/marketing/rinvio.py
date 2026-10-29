"""29/10 (cliente: "rimanda le stesse e-mail a tutti, con la novità: adesso prendiamo anche gli appuntamenti").

Una volta sola (RINVIO_LISTA=<nome>): prepara in Brevo una lista con TUTTI i commercianti già contattati,
TOLTI quelli che non vogliono più ricevere niente:
- cancellati o bloccati in Brevo (emailBlacklisted) e indirizzi rimbalzati;
- chi ha risposto "no" (RINVIO_ESCLUSI: indirizzi o domini separati da virgola, messi su Render, mai nel codice).
La campagna si crea e si manda da Brevo dopo la prova (nessun invio da qui). Nei log solo conteggi.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

from .brevo_liste import API, _cartella, _chiave

lg = logging.getLogger("dealfinder.marketing")


def lista(nome: str) -> int:
    esclusi = {x.strip().lower() for x in (os.environ.get("RINVIO_ESCLUSI") or "").split(",") if x.strip()}
    with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=60) as c:
        rimbalzati: set = set()
        try:                                           # rimbalzi definitivi delle campagne (non si riscrive a chi non esiste)
            off = 0
            while True:
                r = c.get(f"{API}/smtp/blockedContacts", params={"limit": 100, "offset": off})
                if r.status_code >= 300:
                    break
                cs = r.json().get("contacts") or []
                rimbalzati |= {x["email"].lower() for x in cs if x.get("email")}
                if len(cs) < 100:
                    break
                off += 100
        except Exception:
            pass
        buoni, tolti, off = [], 0, 0
        while True:
            r = c.get(f"{API}/contacts", params={"limit": 1000, "offset": off})
            r.raise_for_status()
            cs = r.json().get("contacts") or []
            for x in cs:
                e = (x.get("email") or "").lower()
                if not e:
                    continue
                if (x.get("emailBlacklisted") or e in esclusi or e.split("@")[-1] in esclusi or e in rimbalzati
                        or e.endswith("@gmail.com") and e.split("@")[0] == "ahmedisma545"):
                    tolti += 1
                    continue
                buoni.append(e)
            if len(cs) < 1000:
                break
            off += 1000
        r = c.post(f"{API}/contacts/lists", json={"name": nome, "folderId": _cartella(c)})
        r.raise_for_status()
        lid = r.json()["id"]
        for i in range(0, len(buoni), 500):
            c.post(f"{API}/contacts/import", json={"jsonBody": [{"email": e} for e in buoni[i:i + 500]], "listIds": [lid],
                                                   "updateExistingContacts": True, "emptyContactsAttributes": False})
            time.sleep(5)
    lg.warning("RINVIO_LISTA %s (id %s): %d commercianti, %d tolti (cancellati, rimbalzati, risposto no)", nome, lid, len(buoni), tolti)
    return lid


def invia(campagna: int) -> int:
    """Manda subito la campagna già preparata e provata (RINVIO_INVIA=<id campagna>), una volta sola."""
    with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=60) as c:
        r = c.post(f"{API}/emailCampaigns/{campagna}/sendNow")
    lg.warning("RINVIO_INVIA campagna %s: esito %s %s", campagna, r.status_code, r.text[:200] if r.status_code >= 300 else "")
    return r.status_code


def non_aperti(campagna: int, quando: str) -> dict:
    """30/10 (cliente: "domani alle 8 rimanda le e-mail a tutti quelli che non hanno aperto").

    RINVIO_NONAPERTI=<id campagna> e RINVIO_ORA=<data ISO>: Brevo esporta chi NON ha aperto quella campagna; si tolgono
    cancellati, bloccati, rimbalzati e chi ha già risposto (RINVIO_ESCLUSI e RINVIO_RISPOSTE, indirizzi o domini); si crea una
    lista e una copia della stessa email programmata in Brevo all'ora data (l'invio lo fa Brevo all'ora giusta). Nei log solo conteggi."""
    import csv, io, re, zipfile
    esclusi = {x.strip().lower() for k in ("RINVIO_ESCLUSI", "RINVIO_RISPOSTE")
               for x in (os.environ.get(k) or "").split(",") if x.strip()}
    with httpx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=90) as c:
        orig = c.get(f"{API}/emailCampaigns/{campagna}").json()
        r = c.post(f"{API}/emailCampaigns/{campagna}/exportRecipients", json={"recipientsType": "nonOpeners"})
        r.raise_for_status()
        pid = r.json()["processId"]
        url = None
        for _ in range(60):
            time.sleep(10)
            p = c.get(f"{API}/processes/{pid}").json()
            if p.get("status") == "completed":
                url = p.get("export_url")
                break
        if not url:
            raise RuntimeError(f"esportazione non pronta (processo {pid})")
        dati = httpx.get(url, timeout=90).content
        if dati[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(dati))
            dati = z.read(z.namelist()[0])
        testo = dati.decode("utf-8", "ignore")
        emails = {m.lower() for m in re.findall(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", testo)}
        bloccati: set = set()
        off = 0
        while True:
            rr = c.get(f"{API}/smtp/blockedContacts", params={"limit": 100, "offset": off})
            if rr.status_code >= 300:
                break
            cs = rr.json().get("contacts") or []
            bloccati |= {x["email"].lower() for x in cs if x.get("email")}
            if len(cs) < 100:
                break
            off += 100
        buoni = sorted(e for e in emails if e not in esclusi and e.split("@")[-1] not in esclusi and e not in bloccati
                       and e != "info@skovo.app" and not e.startswith("ahmedisma"))
        nome = f"Non aperti campagna {campagna}"
        rl = c.post(f"{API}/contacts/lists", json={"name": nome, "folderId": _cartella(c)})
        rl.raise_for_status()
        lid = rl.json()["id"]
        for i in range(0, len(buoni), 500):
            c.post(f"{API}/contacts/import", json={"jsonBody": [{"email": e} for e in buoni[i:i + 500]], "listIds": [lid],
                                                   "updateExistingContacts": False, "emptyContactsAttributes": False})
            time.sleep(5)
        time.sleep(60)
        corpo = {"name": f"{orig['name']} - non aperti", "subject": orig["subject"], "previewText": orig.get("previewText") or "",
                 "sender": {"id": orig["sender"]["id"]}, "replyTo": orig.get("replyTo") or "info@skovo.app",
                 "htmlContent": orig["htmlContent"], "recipients": {"listIds": [lid]}, "scheduledAt": quando}
        rc = c.post(f"{API}/emailCampaigns", json=corpo)
        rc.raise_for_status()
        cid = rc.json()["id"]
    esito = {"non_aperti": len(emails), "da_rimandare": len(buoni), "tolti": len(emails) - len(buoni), "lista": lid,
             "campagna": cid, "quando": quando}
    lg.warning("RINVIO_NONAPERTI %s", esito)
    return esito
