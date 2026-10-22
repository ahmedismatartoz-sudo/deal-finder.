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


def _chiave() -> str:
    key = (os.environ.get("BREVO_API_KEY") or "").strip().strip('"').strip("'")
    if key.startswith("eyJ"):  # chiave "MCP" di Brevo: base64 di {"api_key": "xkeysib-..."}
        try:
            import base64
            key = json.loads(base64.b64decode(key + "=" * (-len(key) % 4)))["api_key"].strip()
        except Exception as e:
            lg.warning("BREVO_LISTE: chiave MCP non decodificabile: %s", type(e).__name__)
    return key


def importa() -> None:
    key = _chiave()
    if not key or not FILE.exists():
        lg.warning("BREVO_LISTE: chiave o file mancante")
        return
    gruppi = json.loads(FILE.read_text())
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        prova = c.get(f"{API}/account")
        if prova.status_code != 200:
            lg.warning("BREVO_LISTE chiave rifiutata: tipo=%s lung=%d risposta=%s",
                       key[:8], len(key), prova.text[:200])
            return
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


CARTELLA_EMAIL = FILE.parent / "marketing_email"
MITTENTE = {"name": "Skovo", "email": "ahmedisma545@gmail.com"}


def _foto_ok(html: str) -> bool:
    """Le foto arrivano da /email-foto/ del sito: servono solo se l'auto e' ancora pubblicata."""
    import re
    import time

    porta = os.environ.get("PORT", "10000")
    urls = re.findall(r"https://scovo\.onrender\.com(/email-foto/\d+/\d+\.jpg)", html)
    for _ in range(40):  # fino a ~40 minuti: l'elenco del sito si ricalcola dopo l'avvio
        esiti = []
        for u in urls:
            try:
                esiti.append(httpx.get(f"http://127.0.0.1:{porta}{u}", timeout=30).status_code == 200)
            except Exception:
                esiti.append(False)
        if all(esiti):
            return True
        time.sleep(60)
    return False


def campagne() -> None:
    """22/10 (cliente): crea e invia subito le 6 campagne (una per lista). Non rimanda quelle gia' create."""
    key = _chiave()
    oggetti = json.loads((CARTELLA_EMAIL / "oggetti.json").read_text())
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        liste = _liste(c)
        r = c.get(f"{API}/emailCampaigns", params={"limit": 100})
        r.raise_for_status()
        fatte = {x["name"] for x in r.json().get("campaigns") or []}
        for gruppo, oggetto in oggetti.items():
            nome = f"Skovo {gruppo} 22-10"
            lid = liste.get(f"Skovo {gruppo}")
            if nome in fatte or lid is None:
                lg.warning("BREVO_CAMPAGNA %s saltata (gia' fatta o lista assente)", gruppo)
                continue
            html = (CARTELLA_EMAIL / f"{gruppo}.html").read_text()
            if not _foto_ok(html):
                lg.warning("BREVO_CAMPAGNA %s NON inviata: foto non disponibili (auto tolta dal sito?)", gruppo)
                continue
            r = c.post(f"{API}/emailCampaigns", json={
                "name": nome, "subject": oggetto, "sender": MITTENTE, "replyTo": MITTENTE["email"],
                "htmlContent": html, "recipients": {"listIds": [lid]},
            })
            if r.status_code >= 300:
                lg.warning("BREVO_CAMPAGNA %s creazione fallita: %s", gruppo, r.text[:300])
                continue
            cid = r.json()["id"]
            s = c.post(f"{API}/emailCampaigns/{cid}/sendNow")
            lg.warning("BREVO_CAMPAGNA %s id=%s lista=%s invio=%s %s", gruppo, cid, lid, s.status_code, s.text[:200])
