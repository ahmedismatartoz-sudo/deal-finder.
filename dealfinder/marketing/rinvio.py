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
