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

    porta = os.environ.get("PORT", "10000")
    for u in re.findall(r"https://scovo\.onrender\.com(/email-foto/\d+/\d+\.jpg)", html):
        try:
            if httpx.get(f"http://127.0.0.1:{porta}{u}", timeout=30).status_code != 200:
                return False
        except Exception:
            return False
    return True


def _eur(v) -> str:
    return f"{int(round(v)):,}".replace(",", ".")


def _card(x: dict) -> str:
    """Stessa scheda delle email del 22/10: prezzo 1.500 € sotto l'annuncio (concordato), margine al netto di 100 €."""
    from html import escape

    p = round((x["prezzo"] - 1500) / 100) * 100
    m = round((x["R"] - p - 100) / 5) * 5
    n = escape(str(x["auto"]))
    imgs = "".join(
        f'<td width="50%" style="padding:0 {1-k}px 0 {k}px"><img src="https://scovo.onrender.com/email-foto/{x["id"]}/{k}.jpg" '
        f'width="279" style="width:100%;display:block;height:auto" alt="{n}"></td>' for k in (0, 1))
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e6e6ee;border-radius:12px;margin:0 0 16px;overflow:hidden">'
        f'<tr><td style="padding:0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{imgs}</tr></table></td></tr>'
        '<tr><td style="padding:14px 18px 16px">'
        f'<div style="font-size:12px;color:#7a7a88;text-transform:uppercase;letter-spacing:1px">{x["anno"]} · {_eur(x["km"])} km · {escape(str(x["carb"]))}</div>'
        f'<div style="font-size:18px;font-weight:700;margin:4px 0 10px">{n}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="font-size:13px;color:#7a7a88">Prezzo<br><b style="font-size:18px;color:#1d1d24">{_eur(p)} €</b></td>'
        f'<td style="font-size:13px;color:#7a7a88">Rivendita veloce<br><b style="font-size:18px;color:#1d1d24">{_eur(x["R"])} €</b></td>'
        f'<td align="right"><span style="display:inline-block;background:#e7f8ef;color:#0b7a43;font-weight:800;font-size:16px;padding:8px 12px;border-radius:999px">+{_eur(m)} €</span></td>'
        '</tr></table>'
        f'<div style="font-size:12px;color:#7a7a88;margin-top:8px">Venditore privato · confrontata con {x.get("n", 0)} auto uguali</div>'
        '</td></tr></table>')


FASCE = {"utilitarie": (2500, 7000), "medie": (5000, 11000), "premium": (5000, 25000)}


def _sostituisci(gruppo: str, html: str, items: list[dict], usate: set) -> tuple[str, str] | None:
    """Se un'auto dell'email non e' piu' sul sito: rifa' le 2 schede con le auto migliori ora sul sito
    (max 100.000 km, almeno 20 auto uguali nel confronto, fascia di prezzo del gruppo, non gia' usate)."""
    lo, hi = FASCE[gruppo.split("_")[0]]
    cand = [x for x in items
            if x.get("id") not in usate and (x.get("km") or 10**9) <= 100000 and (x.get("n") or 0) >= 20
            and lo <= (x.get("prezzo") or 0) <= hi and (x.get("margine") or 0) >= 1500
            and x.get("R") and x.get("anno")]
    cand.sort(key=lambda x: -(x.get("margine") or 0))
    def _n(f):
        try:
            return sum(1 for x in items if f(x))
        except Exception as e:
            return type(e).__name__
    lg.warning("BREVO_CAMPAGNA %s ricerca sostituti: elenco=%d km<=100k=%s n>=20=%s fascia=%s margine>=1500=%s non_usate=%s esempio=%s",
               gruppo, len(items), _n(lambda x: (x.get("km") or 10**9) <= 100000), _n(lambda x: (x.get("n") or 0) >= 20),
               _n(lambda x: lo <= (x.get("prezzo") or 0) <= hi), _n(lambda x: (x.get("margine") or 0) >= 1500),
               len(cand), {k: items[0].get(k) for k in ("id", "km", "n", "prezzo", "R", "margine", "anno")} if items else None)
    scelte = []
    for x in cand:
        prova = _card(x)
        if _foto_ok(prova):
            scelte.append(x)
        else:
            lg.warning("BREVO_CAMPAGNA %s sostituto %s senza foto", gruppo, x.get("id"))
        if len(scelte) == 2:
            break
    if len(scelte) < 2:
        return None
    a = html.index('<tr><td style="padding:8px 20px">')
    b = html.index('<tr><td style="padding:2px 28px 4px">')
    nuovo = html[:a] + '<tr><td style="padding:8px 20px">' + "".join(_card(x) for x in scelte) + "</td></tr>\n" + html[b:]
    nomi = [" ".join(str(x["auto"]).split()[:2]) for x in scelte]
    usate.update(x["id"] for x in scelte)
    lg.warning("BREVO_CAMPAGNA %s auto sostituite: %s", gruppo, [x["id"] for x in scelte])
    return nuovo, f"{nomi[0]} e {nomi[1]} da privati sotto mercato"


_usate_oggi: set = set()  # auto messe nelle email rifatte in questo giro (le due email della stessa fascia restano diverse)


def campagne(elenco=None) -> None:
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
                import re
                usate = {int(v) for v in re.findall(r"/email-foto/(\d+)/", "".join(
                    f.read_text() for f in CARTELLA_EMAIL.glob(f"{gruppo.split('_')[0]}_*.html")))}
                usate |= _usate_oggi
                rifatta = _sostituisci(gruppo, html, list(elenco() if elenco else []), usate)
                if rifatta is None:
                    lg.warning("BREVO_CAMPAGNA %s NON inviata: auto non piu' sul sito e nessun sostituto", gruppo)
                    continue
                html, oggetto = rifatta
                _usate_oggi.update(usate)
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
