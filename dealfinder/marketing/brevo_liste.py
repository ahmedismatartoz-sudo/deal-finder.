"""22/10 (cliente): carica in Brevo i commercianti che hanno dato il permesso, divisi in 6 liste
(fascia utilitarie/medie/premium x gruppo A/B). Solo import contatti: le campagne si creano a parte.

Gira una volta all'avvio del servizio web se BREVO_IMPORT_LISTE=1. Non scrive email nei log.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx

lg = logging.getLogger("dealfinder.marketing")
API = "https://api.brevo.com/v3"
FILE = Path(__file__).resolve().parents[2] / "data" / "marketing_gruppi.json"


def _cartella(c: httpx.Client) -> int:
    r = c.get(f"{API}/contacts/folders", params={"limit": 50})
    r.raise_for_status()
    for f in r.json().get("folders") or []:
        if f.get("name") == "Skovo":
            return f["id"]
    r = c.post(f"{API}/contacts/folders", json={"name": "Skovo"})
    r.raise_for_status()
    return r.json()["id"]


def _liste(c: httpx.Client) -> dict[str, int]:
    r = c.get(f"{API}/contacts/lists", params={"limit": 50})
    r.raise_for_status()
    return {x["name"]: x["id"] for x in r.json().get("lists") or []}


def importa() -> None:
    key = os.environ.get("BREVO_API_KEY")
    if not key or not FILE.exists():
        lg.warning("BREVO_LISTE: chiave o file mancante")
        return
    gruppi = json.loads(FILE.read_text())
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        cartella = _cartella(c)
        esistenti = _liste(c)
        for nome, emails in gruppi.items():
            lid = esistenti.get(nome)
            if lid is None:
                r = c.post(f"{API}/contacts/lists", json={"name": nome, "folderId": cartella})
                r.raise_for_status()
                lid = r.json()["id"]
            r = c.post(f"{API}/contacts/import", json={
                "jsonBody": [{"email": e} for e in emails],
                "listIds": [lid],
                "updateExistingContacts": True,
                "emptyContactsAttributes": False,
            })
            lg.warning("BREVO_LISTE %s id=%s contatti=%d esito=%s", nome, lid, len(emails), r.status_code)
