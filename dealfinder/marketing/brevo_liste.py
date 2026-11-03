"""22/10 (cliente): carica in Brevo i commercianti che hanno dato il permesso, divisi in 6 liste
(fascia utilitarie/medie/premium x gruppo A/B). Solo import contatti: le campagne si creano a parte.

Gira una volta all'avvio del servizio web se BREVO_IMPORT_LISTE=1. Non scrive email nei log.
4/11: una volta sola per file (anche dopo i riavvii); i gruppi si leggono da dati_riservati (non più da data/ nel
repo); non si reimportano i disiscritti/bloccati né i contatti già caricati e poi cancellati da Brevo.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx

lg = logging.getLogger("dealfinder.marketing")
API = "https://api.brevo.com/v3"
TAG = os.environ.get("BREVO_TAG", "").strip()  # 27/10: campagna nuova in cartella e liste proprie (es. "27-10")
_DATA = Path(__file__).resolve().parents[2] / "data"
NOME_GRUPPI = f"marketing_gruppi_{TAG}" if TAG else "marketing_gruppi"      # nome in dati_riservati


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


def da_importare(emails, bloccati: set, gia_caricati: set, in_brevo: set) -> list[str]:
    """Chi si può (ri)mettere in una lista: mai i disiscritti/bloccati (impronte), mai chi era già stato caricato
    in Brevo e non c'è più (cancellato: tornerebbe come nuovo, senza il blocco). Chi è in Brevo resta."""
    from .invii import impronta, norm_email
    out, visti = [], set()
    for e in emails:
        e = norm_email(e)
        if not e or e in visti or impronta(e) in bloccati or (e in gia_caricati and e not in in_brevo):
            continue
        visti.add(e)
        out.append(e)
    return out


def importa() -> None:
    from ..db import connect
    from . import invii
    from .dati_riservati import leggi_gruppi
    key = _chiave()
    with connect() as conn:
        gruppi = leggi_gruppi(conn, NOME_GRUPPI)
    if not key or not gruppi:
        lg.warning("BREVO_LISTE: chiave o gruppi mancanti (dati_riservati %s)", NOME_GRUPPI)
        return
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        prova = c.get(f"{API}/account")
        if prova.status_code != 200:
            lg.warning("BREVO_LISTE chiave rifiutata: esito=%s", prova.status_code)
            return
        from .auto_invio import _contatti_brevo
        contatti = _contatti_brevo(c)
        with connect() as conn:
            invii.sincronizza_da_brevo(conn, contatti)
            bloccati = invii.disiscritti(conn)
            gia = {r["email"] for r in conn.execute("SELECT email FROM brevo_inviati").fetchall()}
        in_brevo = {x["email"].lower() for x in contatti if x.get("email")}
        cartella = _cartella(c)
        esistenti = _liste(c)
        for nome, emails in gruppi.items():
            nome = f"{nome} {TAG}" if TAG else nome
            buoni = da_importare(emails, bloccati, gia, in_brevo)
            if not buoni:
                lg.warning("BREVO_LISTE %s: nessun contatto da importare (%d esclusi)", nome, len(emails))
                continue
            lid = esistenti.get(nome)
            if lid is None:
                r = c.post(f"{API}/contacts/lists", json={"name": nome, "folderId": cartella})
                r.raise_for_status()
                lid = r.json()["id"]
            r = c.post(f"{API}/contacts/import", json={
                "jsonBody": [{"email": e} for e in buoni],
                "listIds": [lid],
                "updateExistingContacts": True,
                "emptyContactsAttributes": False,
            })
            if r.status_code < 300:
                with connect() as conn:
                    for e in buoni:
                        conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (e,))
                    conn.commit()
            lg.warning("BREVO_LISTE %s id=%s contatti=%d esclusi=%d esito=%s", nome, lid, len(buoni),
                       len(emails) - len(buoni), r.status_code)


CARTELLA_EMAIL = _DATA / "marketing_email" / TAG if TAG else _DATA / "marketing_email"
MITTENTE = {"name": "Skovo", "email": os.environ.get("BREVO_MITTENTE", "info@skovo.app")}  # 27/10: dominio skovo.app, mai il Gmail


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
    """Scheda dell'auto nelle email. 4/11: prezzo VERO dell'annuncio (prima 1.500-5.000 € sotto, come se la trattativa
    fosse già fatta, e il margine calcolato su quel prezzo): nessuno sconto da trattativa. Margine al netto di 100 €."""
    from html import escape

    p = round(x["prezzo"] / 100) * 100
    m = round((x["R"] - x["prezzo"] - 100) / 5) * 5
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


def nomi_campagne(c: httpx.Client) -> set[str]:
    """Nomi di TUTTE le campagne in Brevo (4/11: prima solo le ultime 100, e una campagna vecchia uscita dalla
    pagina veniva creata e mandata di nuovo)."""
    fatte, off = set(), 0
    while True:
        r = c.get(f"{API}/emailCampaigns", params={"limit": 100, "offset": off})
        r.raise_for_status()
        cs = r.json().get("campaigns") or []
        fatte |= {x["name"] for x in cs if x.get("name")}
        if len(cs) < 100:
            return fatte
        off += 100


def campagne(elenco=None) -> None:
    """22/10 (cliente): crea e invia subito le 6 campagne (una per lista). Non rimanda quelle gia' create."""
    key = _chiave()
    oggetti = json.loads((CARTELLA_EMAIL / "oggetti.json").read_text())
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        liste = _liste(c)
        fatte = nomi_campagne(c)
        for gruppo, oggetto in oggetti.items():
            nome = f"Skovo {gruppo} {TAG or '22-10'}"
            lid = liste.get(f"Skovo {gruppo} {TAG}" if TAG else f"Skovo {gruppo}")
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
            quando = (os.environ.get("BREVO_PROGRAMMA") or "").strip()  # 27/10: es. 2026-10-28T09:30:00+01:00 (invio programmato)
            corpo = {"name": nome, "subject": oggetto, "sender": MITTENTE, "replyTo": MITTENTE["email"],
                     "htmlContent": html, "recipients": {"listIds": [lid]}}
            if quando:
                corpo["scheduledAt"] = quando
            r = c.post(f"{API}/emailCampaigns", json=corpo)
            if r.status_code >= 300:
                lg.warning("BREVO_CAMPAGNA %s creazione fallita: %s", gruppo, r.text[:300])
                continue
            cid = r.json()["id"]
            if quando:
                lg.warning("BREVO_CAMPAGNA %s id=%s lista=%s programmata per %s", gruppo, cid, lid, quando)
                continue
            s = c.post(f"{API}/emailCampaigns/{cid}/sendNow")
            lg.warning("BREVO_CAMPAGNA %s id=%s lista=%s invio=%s %s", gruppo, cid, lid, s.status_code, s.text[:200])
