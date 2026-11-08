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


# ---------- 4/11 notte (cliente): rimettere i contatti vecchi e mandare la campagna con gli esempi ----------
import re as _re

_EMAIL_OK = _re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}$")
_NON_EMAIL = _re.compile(r"(\.(png|jpe?g|gif|webp|avif|svg|bmp)$)|@2x", _re.I)
LISTA_RIMESSI = "Skovo contatti vecchi rimessi 04-11"
TETTO_BREVO = 1495                       # regola: meno di 1.500 contatti in Brevo


def scegli_rimessi(emails, bloccati: set, gia_in_brevo: set, posto: int) -> list[str]:
    """Contatti vecchi da rimettere: email valide, mai disiscritti/bloccati (impronte), mai Loconte, senza doppioni,
    entro il posto libero in Brevo. Funzione pura (test in tests/test_brevo_rimessi.py)."""
    from .invii import impronta, norm_email
    out, visti = [], set()
    for e in emails:
        e = norm_email(e)
        if not e or e in visti or not _EMAIL_OK.match(e) or _NON_EMAIL.search(e) or "loconte" in e:
            continue
        if impronta(e) in bloccati:
            continue
        visti.add(e)
        if e not in gia_in_brevo:
            out.append(e)
    return out[:max(0, posto)]


BLOCCATI_MINIMI = 158                    # nell'esportazione del 1/11 i disiscritti/bloccati erano 158


def _rimessi_0111() -> set[str]:
    """Impronte dei 1.186 contatti dell'esportazione del 1/11 da rimettere (gli stessi delle prime campagne)."""
    try:
        return set(json.loads((_DATA / "brevo_rimessi_0111.json").read_text()))
    except Exception:
        return set()


def _bloccati_0111() -> set[str]:
    """Impronte (sha256, mai le email) dei 158 disiscritti/bloccati dell'esportazione Brevo del 1/11."""
    try:
        return set(json.loads((_DATA / "brevo_bloccati_0111.json").read_text()))
    except Exception:
        return set()


def rimetti_e_invia(campagna_id: int) -> dict:
    """Rimette in una lista nuova i commercianti già caricati in Brevo in passato (tabella brevo_inviati) che il
    cliente ha cancellato da Brevo, tranne disiscritti e bloccati, poi manda la campagna `campagna_id` a quella lista.
    Una volta sola (ciclo._una_volta). Nei log solo conteggi."""
    import time as _t
    from ..db import connect
    from . import invii
    from .auto_invio import _contatti_brevo
    key = _chiave()
    if not key:
        return {"errore": "chiave"}
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        if c.get(f"{API}/account").status_code != 200:
            return {"errore": "chiave rifiutata"}
        contatti = _contatti_brevo(c)
        with connect() as conn:
            invii.sincronizza_da_brevo(conn, contatti)
            bloccati = invii.disiscritti(conn) | _bloccati_0111()
            for h in _bloccati_0111():
                conn.execute("INSERT INTO disiscritti (email_hash, motivo) VALUES (%s,'brevo_0111') ON CONFLICT DO NOTHING", (h,))
            conn.commit()
            vecchi = [r["email"] for r in conn.execute("SELECT email FROM brevo_inviati ORDER BY email").fetchall()]
        in_brevo = {(x.get("email") or "").lower() for x in contatti}
        scelti = _rimessi_0111()
        if scelti:                                   # solo i contatti delle prime campagne (esportazione del 1/11)
            vecchi = [e for e in vecchi if invii.impronta(e) in scelti]
        buoni = scegli_rimessi(vecchi, bloccati, in_brevo, TETTO_BREVO - len(in_brevo))
        esito = {"vecchi": len(vecchi), "in_brevo": len(in_brevo), "da_rimettere": len(buoni), "disiscritti": len(bloccati)}
        if len(bloccati) < BLOCCATI_MINIMI:          # elenco dei disiscritti incompleto: si rischia di riscrivere a chi
            esito["fermo"] = "disiscritti_incompleti"  # ha chiesto di non ricevere più email -> niente invio
            return esito
        if not buoni:
            return esito
        lid = _liste(c).get(LISTA_RIMESSI)
        if lid is None:
            r = c.post(f"{API}/contacts/lists", json={"name": LISTA_RIMESSI, "folderId": _cartella(c)})
            r.raise_for_status()
            lid = r.json()["id"]
        r = c.post(f"{API}/contacts/import", json={"jsonBody": [{"email": e} for e in buoni], "listIds": [lid],
                                                   "updateExistingContacts": True, "emptyContactsAttributes": False})
        esito["import"] = r.status_code
        if r.status_code >= 300:
            return esito
        with connect() as conn:
            for e in buoni:
                conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (e,))
            conn.commit()
        n = 0
        for _ in range(40):                                  # Brevo importa in differita: si aspetta la lista piena
            _t.sleep(15)
            n = int((c.get(f"{API}/contacts/lists/{lid}").json() or {}).get("uniqueSubscribers") or 0)
            if n >= 0.9 * len(buoni):
                break
        esito["nella_lista"] = n
        if n == 0:
            return esito
        r = c.put(f"{API}/emailCampaigns/{campagna_id}", json={"recipients": {"listIds": [lid]}})
        esito["destinatari"] = r.status_code
        if r.status_code >= 300:
            return esito
        s = c.post(f"{API}/emailCampaigns/{campagna_id}/sendNow")
        esito["invio"] = s.status_code
        return esito


# ---------- 4/11 sera (cliente: "carica e manda i nuovi"): saloni della zona mai contattati ----------
LISTA_NUOVI_ZONA = "Skovo saloni zona nuovi 05-11"


def scegli_nuovi(emails, bloccati: set, gia_usati: set, in_brevo: set, posto: int) -> list[str]:
    """Email mai contattate: valide, non disiscritte/bloccate (impronte), mai caricate in Brevo, senza doppioni, entro
    il posto libero sotto il tetto dei contatti. Funzione pura (tests/test_brevo_rimessi.py)."""
    from .invii import impronta, norm_email
    out, visti = [], set()
    for e in emails:
        e = norm_email(e)
        if not e or e in visti or not _EMAIL_OK.match(e) or _NON_EMAIL.search(e) or "loconte" in e or ".pec." in e \
                or e.endswith("@pec.it") or "legalmail" in e:
            continue
        visti.add(e)
        if impronta(e) in bloccati or e in gia_usati or e in in_brevo:
            continue
        out.append(e)
    return out[:max(0, posto)]


def carica_e_programma(emails: list[str], modello_id: int, quando: str) -> dict:
    """Carica i saloni nuovi in una lista propria e crea la campagna (copia di `modello_id`: stesso oggetto e testo)
    programmata per `quando`. Se le auto della campagna modello non sono più sul sito non crea niente. Solo conteggi."""
    import time as _t
    from ..db import connect
    from . import invii
    from .auto_invio import _contatti_brevo
    key = _chiave()
    if not key:
        return {"errore": "chiave"}
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        if c.get(f"{API}/account").status_code != 200:
            return {"errore": "chiave rifiutata"}
        contatti = _contatti_brevo(c)
        with connect() as conn:
            invii.sincronizza_da_brevo(conn, contatti)
            bloccati = invii.disiscritti(conn) | _bloccati_0111()
            gia = {invii.norm_email(r["email"]) for r in conn.execute("SELECT email FROM brevo_inviati").fetchall()}
        in_brevo = {(x.get("email") or "").lower() for x in contatti}
        posto = TETTO_BREVO - len(in_brevo)
        tutti = scegli_nuovi(emails, bloccati, gia, in_brevo, 10**6)
        buoni = tutti[:max(0, posto)]
        esito = {"ricevute": len(emails), "valide_mai_usate": len(tutti), "in_brevo": len(in_brevo), "posto": posto,
                 "da_caricare": len(buoni), "fuori_tetto": len(tutti) - len(buoni), "disiscritti": len(bloccati)}
        if len(bloccati) < BLOCCATI_MINIMI:
            esito["fermo"] = "disiscritti_incompleti"
            return esito
        if not buoni:
            return esito
        modello = c.get(f"{API}/emailCampaigns/{modello_id}").json()
        html, oggetto = modello.get("htmlContent") or "", modello.get("subject") or ""
        if not html or not _foto_ok(html):
            esito["fermo"] = "auto_del_modello_non_piu_sul_sito"
            return esito
        lid = _liste(c).get(LISTA_NUOVI_ZONA)
        if lid is None:
            r = c.post(f"{API}/contacts/lists", json={"name": LISTA_NUOVI_ZONA, "folderId": _cartella(c)})
            r.raise_for_status()
            lid = r.json()["id"]
        r = c.post(f"{API}/contacts/import", json={"jsonBody": [{"email": e} for e in buoni], "listIds": [lid],
                                                   "updateExistingContacts": True, "emptyContactsAttributes": False})
        esito["import"] = r.status_code
        if r.status_code >= 300:
            return esito
        with connect() as conn:
            for e in buoni:
                conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (e,))
            conn.commit()
        n = 0
        for _ in range(40):
            _t.sleep(15)
            n = int((c.get(f"{API}/contacts/lists/{lid}").json() or {}).get("uniqueSubscribers") or 0)
            if n >= 0.9 * len(buoni):
                break
        esito["nella_lista"] = n
        if n == 0:
            return esito
        if LISTA_NUOVI_ZONA in nomi_campagne(c):
            esito["fermo"] = "campagna_gia_creata"
            return esito
        corpo = {"name": LISTA_NUOVI_ZONA, "subject": oggetto, "sender": MITTENTE, "replyTo": MITTENTE["email"],
                 "htmlContent": html, "recipients": {"listIds": [lid]}, "scheduledAt": quando}
        r = c.post(f"{API}/emailCampaigns", json=corpo)
        esito["campagna"] = r.status_code
        if r.status_code < 300:
            esito["campagna_id"] = r.json().get("id")
            esito["programmata"] = quando
        return esito


def hash_email(e: str) -> str:
    import hashlib
    return hashlib.sha256(e.strip().lower().encode()).hexdigest()[:12]


def emails_da_export(dati: bytes) -> list[str]:
    """Il file di Brevo può arrivare zip, gzip o CSV semplice: ne tira fuori gli indirizzi, senza doppioni."""
    import gzip
    import io
    import re
    import zipfile
    testo = dati
    try:
        with zipfile.ZipFile(io.BytesIO(dati)) as z:
            testo = b"\n".join(z.read(n) for n in z.namelist())
    except zipfile.BadZipFile:
        try:
            testo = gzip.decompress(dati)
        except OSError:
            pass
    visti, out = set(), []
    for e in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", testo.decode("utf-8", "ignore")):
        k = e.lower()
        if k not in visti:
            visti.add(k)
            out.append(k)
    return out


def aperture(campagna_id: int, tipo: str = "openers") -> dict:
    """4/11 sera (cliente: "preparami la lista" di chi ha aperto, da chiamare): chiede a Brevo l'elenco di chi ha
    aperto la campagna. Nei log SOLO l'impronta corta (sha256, 12 caratteri) di ogni indirizzo: mai email in chiaro.
    Il confronto con nomi e numeri si fa fuori dal sito, sulle liste del cliente."""
    import time
    key = _chiave()
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=120) as c:
        r = c.post(f"{API}/emailCampaigns/{campagna_id}/exportRecipients", json={"recipientsType": tipo})
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
            return {"errore": "export non pronto", "process": pid}
        d = httpx.get(url, timeout=120, follow_redirects=True)
        d.raise_for_status()
    ems = emails_da_export(d.content)
    return {"campagna": campagna_id, "tipo": tipo, "n": len(ems), "h": [hash_email(e) for e in ems]}


# ---------- 5/11 pomeriggio (cliente: "prendi i nuovi 300, fai un mix di altre fino a 1.200, caricali e manda") ----------
LISTA_MIX = "Skovo nuovi + richiamo 05-11"


def scegli_mix(nuove: list[dict], richiamo: list[str], bloccati: set, totale: int) -> list[str]:
    """Prima le email mai contattate (zona, poi senza provincia, poi fuori zona), poi i contattati del 3/11 che non
    hanno aperto, fino a `totale`. Sempre fuori: disiscritti/bloccati, PEC, Loconte, doppioni. Funzione pura."""
    from .invii import impronta, norm_email
    ordine = {"zona": 0, "?": 1}
    nuove = sorted(nuove, key=lambda r: ordine.get("zona" if r.get("zona") else (r.get("prov") or "?"), 2))
    out, visti = [], set()
    for e in [r.get("email") for r in nuove] + list(richiamo):
        e = norm_email(e or "")
        if not e or e in visti or not _EMAIL_OK.match(e) or _NON_EMAIL.search(e) or "loconte" in e \
                or "pec" in e.split("@")[-1] or "legalmail" in e:
            continue
        visti.add(e)
        if impronta(e) in bloccati:
            continue
        out.append(e)
        if len(out) >= totale:
            break
    return out


def _esporta(c, campagna_id: int, tipo: str) -> list[str]:
    import time as _t
    r = c.post(f"{API}/emailCampaigns/{campagna_id}/exportRecipients", json={"recipientsType": tipo})
    try:
        pid = (r.json() or {}).get("processId") if r.status_code < 300 else None
    except ValueError:
        pid = None
    if not pid:
        lg.warning("BREVO_MIX esportazione campagna %s (%s) non disponibile: HTTP %s", campagna_id, tipo, r.status_code)
        return []
    for _ in range(60):
        _t.sleep(5)
        p = c.get(f"{API}/processes/{pid}").json()
        if p.get("status") == "completed" and p.get("export_url"):
            d = httpx.get(p["export_url"], timeout=120, follow_redirects=True)
            return emails_da_export(d.content) if d.status_code == 200 else []
    return []


def mix_e_invia(modello_id: int, totale: int = 1200, campagne_richiamo=(64, 65, 66, 67), lista_vecchia: int = 74) -> dict:
    """Toglie da Brevo i contatti della lista di ieri (non quelli bloccati, che restano bloccati), carica le email
    nuove + il richiamo del 3/11 in una lista propria e manda subito la copia della campagna `modello_id`."""
    import time as _t
    from ..db import connect
    from . import consegne as CO
    from . import invii
    from .auto_invio import _contatti_brevo
    key = _chiave()
    if not key:
        return {"errore": "chiave"}
    esito = {}
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        if c.get(f"{API}/account").status_code != 200:
            return {"errore": "chiave rifiutata"}
        contatti = _contatti_brevo(c)
        with connect() as conn:
            invii.sincronizza_da_brevo(conn, contatti)
            bloccati = invii.disiscritti(conn) | _bloccati_0111()
            nuove = CO.righe(conn, "TUTTE_MAI_USATE")
        esito["disiscritti"] = len(bloccati)
        if len(bloccati) < BLOCCATI_MINIMI:
            esito["fermo"] = "disiscritti_incompleti"
            return esito
        # 1) il richiamo: chi il 3/11 ha ricevuto e non ha aperto (prima di cancellare qualsiasi cosa)
        richiamo = []
        for cid in campagne_richiamo:
            richiamo += _esporta(c, cid, "nonOpeners")
        hard = set()
        for cid in campagne_richiamo:
            hard |= set(_esporta(c, cid, "hardBounces"))
        richiamo = [e for e in richiamo if e not in hard]
        esito["richiamo_disponibili"] = len(richiamo)
        scelti = scegli_mix(nuove, richiamo, bloccati, totale)
        esito["scelti"] = len(scelti)
        esito["di_cui_nuove"] = sum(1 for e in scelti if e not in set(richiamo))
        modello = c.get(f"{API}/emailCampaigns/{modello_id}").json()
        html, oggetto = modello.get("htmlContent") or "", modello.get("subject") or ""
        if not html or not _foto_ok(html):
            esito["fermo"] = "auto_del_modello_non_piu_sul_sito"
            return esito
        # 2) posto in Brevo: via i contatti della lista di ieri che non sono bloccati
        da_togliere = [x["email"] for x in contatti if lista_vecchia in (x.get("listIds") or [])
                       and not invii.contatto_bloccato(x) and x.get("email")]
        tolti = 0
        for e in da_togliere:
            from urllib.parse import quote
            r = c.delete(f"{API}/contacts/{quote(e, safe='')}")
            if r.status_code in (204, 200, 404):
                tolti += 1
            _t.sleep(0.12)
        esito["tolti_da_brevo"] = tolti
        n_brevo = int((c.get(f"{API}/contacts", params={"limit": 1}).json() or {}).get("count") or 0)
        posto = TETTO_BREVO - n_brevo
        esito["in_brevo_dopo"] = n_brevo
        scelti = scelti[:max(0, posto)]
        esito["da_caricare"] = len(scelti)
        if not scelti:
            return esito
        lid = _liste(c).get(LISTA_MIX)
        if lid is None:
            r = c.post(f"{API}/contacts/lists", json={"name": LISTA_MIX, "folderId": _cartella(c)})
            r.raise_for_status()
            lid = r.json()["id"]
        r = c.post(f"{API}/contacts/import", json={"jsonBody": [{"email": e} for e in scelti], "listIds": [lid],
                                                   "updateExistingContacts": True, "emptyContactsAttributes": False})
        esito["import"] = r.status_code
        if r.status_code >= 300:
            return esito
        with connect() as conn:
            for e in scelti:
                conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (e,))
            conn.commit()
        n = 0
        for _ in range(40):
            _t.sleep(15)
            n = int((c.get(f"{API}/contacts/lists/{lid}").json() or {}).get("uniqueSubscribers") or 0)
            if n >= 0.9 * len(scelti):
                break
        esito["nella_lista"] = n
        if n == 0 or LISTA_MIX in nomi_campagne(c):
            return esito
        corpo = {"name": LISTA_MIX, "subject": oggetto, "sender": MITTENTE, "replyTo": MITTENTE["email"],
                 "htmlContent": html, "recipients": {"listIds": [lid]}}
        r = c.post(f"{API}/emailCampaigns", json=corpo)
        esito["campagna"] = r.status_code
        if r.status_code >= 300:
            return esito
        cid = r.json().get("id")
        esito["campagna_id"] = cid
        s = c.post(f"{API}/emailCampaigns/{cid}/sendNow")
        esito["invio"] = s.status_code
        return esito


# ---------- 8/11 (cliente: "dobbiamo ripartire puliti" dopo il calo delle aperture e Libero che rifiuta) ----------
LISTA_PULITA = "Skovo pulita 08-11 (chi apre)"
# Libero/Virgilio/Microsoft ci rifiutano o ci mettono in spam: per ora fuori, si riprovano col sottodominio riscaldato
_DOMINI_FERMI = ("libero.it", "iol.it", "inwind.it", "blu.it", "giallo.it", "virgilio.it",
                 "hotmail.", "outlook.", "live.", "msn.com")


def scegli_pulita(aperti: list[str], bloccati: set, rimbalzati: set, totale: int) -> list[str]:
    """Solo chi ha aperto (nell'ordine dato: prima i più recenti), senza disiscritti/bloccati, senza chi è
    rimbalzato, senza Libero/Microsoft, PEC, Loconte e doppioni. Funzione pura."""
    from .invii import impronta, norm_email
    out, visti = [], set()
    for e in aperti:
        e = norm_email(e or "")
        if not e or e in visti or not _EMAIL_OK.match(e) or _NON_EMAIL.search(e) or "loconte" in e:
            continue
        visti.add(e)
        dom = e.split("@")[-1]
        if "pec" in dom or "legalmail" in dom or any(dom == d or dom.startswith(d) for d in _DOMINI_FERMI):
            continue
        if e in rimbalzati or impronta(e) in bloccati:
            continue
        out.append(e)
        if len(out) >= totale:
            break
    return out


def lista_pulita(campagne=(69, 68, 65, 54, 53, 52, 44, 43, 42, 41, 40, 39, 35, 34, 33), totale: int = 400) -> dict:
    """Carica in una lista nuova chi ha aperto le ultime campagne, pulito. NON manda niente e NON cancella contatti:
    se in Brevo non c'è posto ne carica solo quanti ci stanno. Nei log solo conteggi."""
    from ..db import connect
    from . import invii
    from .auto_invio import _contatti_brevo
    key = _chiave()
    if not key:
        return {"errore": "chiave"}
    esito = {}
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        if c.get(f"{API}/account").status_code != 200:
            return {"errore": "chiave rifiutata"}
        contatti = _contatti_brevo(c)
        with connect() as conn:
            invii.sincronizza_da_brevo(conn, contatti)
            bloccati = invii.disiscritti(conn) | _bloccati_0111()
        esito["disiscritti"] = len(bloccati)
        if len(bloccati) < BLOCCATI_MINIMI:
            esito["fermo"] = "disiscritti_incompleti"
            return esito
        aperti, rimb = [], set()
        for cid in campagne:
            aperti += _esporta(c, cid, "openers")
            rimb |= set(_esporta(c, cid, "hardBounces")) | set(_esporta(c, cid, "softBounces"))
        esito["aperture_totali"] = len(set(aperti))
        esito["rimbalzati"] = len(rimb)
        scelti = scegli_pulita(aperti, bloccati, rimb, totale)
        esito["scelti"] = len(scelti)
        in_brevo = {(x.get("email") or "").lower() for x in contatti}
        nuovi = [e for e in scelti if e not in in_brevo]
        n_brevo = len(contatti)
        posto = max(0, TETTO_BREVO - n_brevo)
        if len(nuovi) > posto:
            tenuti = set(nuovi[:posto])
            scelti = [e for e in scelti if e in in_brevo or e in tenuti]
        esito.update({"gia_in_brevo": len(scelti) - min(len(nuovi), posto), "nuovi_caricati": min(len(nuovi), posto),
                      "in_brevo_prima": n_brevo, "in_lista": len(scelti)})
        if not scelti:
            return esito
        lid = _liste(c).get(LISTA_PULITA)
        if lid is None:
            r = c.post(f"{API}/contacts/lists", json={"name": LISTA_PULITA, "folderId": _cartella(c)})
            r.raise_for_status()
            lid = r.json()["id"]
        r = c.post(f"{API}/contacts/import", json={"jsonBody": [{"email": e} for e in scelti], "listIds": [lid],
                                                   "updateExistingContacts": True, "emptyContactsAttributes": False})
        esito["import"] = r.status_code
        esito["lista_id"] = lid
    return esito


# ---------- 8/11 (cliente: "quante email abbiamo raccolto in totale, quelle che si possono usare") ----------
def gruppo_dominio(e: str) -> str:
    dom = e.split("@")[-1]
    if "pec" in dom or "legalmail" in dom:
        return "pec"
    if any(dom == d or dom.startswith(d) for d in _DOMINI_FERMI):
        return "libero_microsoft"
    if dom in ("gmail.com", "googlemail.com"):
        return "gmail"
    if dom.endswith((".it", ".com", ".net", ".eu", ".org", ".biz", ".info")) and dom not in (
            "tiscali.it", "alice.it", "tin.it", "yahoo.it", "yahoo.com", "icloud.com", "me.com", "fastwebnet.it", "aruba.it"):
        return "aziendali"
    return "altri_gratuiti"


def conta_email(testi, bloccati: set, inviati: set) -> dict:
    """Conta gli indirizzi unici trovati nei testi (righe lead). Solo numeri, nessun indirizzo. Funzione pura."""
    import re
    from collections import Counter
    from .invii import impronta
    tutte = set()
    for t in testi:
        for e in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", t or ""):
            e = e.lower().strip(".")
            if _EMAIL_OK.match(e) and not _NON_EMAIL.search(e):
                tutte.add(e)
    out = Counter(totale=len(tutte))
    for e in tutte:
        g = gruppo_dominio(e)
        if "loconte" in e:
            out["escluse_loconte"] += 1
            continue
        if g == "pec":
            out["escluse_pec"] += 1
            continue
        if impronta(e) in bloccati:
            out["escluse_disiscritte_o_rimbalzate"] += 1
            continue
        out["usabili"] += 1
        out[f"usabili_{g}"] += 1
        out["usabili_gia_contattate" if impronta(e) in inviati else "usabili_mai_contattate"] += 1
    return dict(out)


def conteggio_email() -> dict:
    from ..db import connect
    from . import invii
    with connect() as conn:
        testi = [r["d"] for r in conn.execute("SELECT dati::text AS d FROM lead_consegne").fetchall()]
        bloccati = invii.disiscritti(conn) | _bloccati_0111()
        inviati = {r["h"] for r in conn.execute(
            "SELECT DISTINCT destinatario_hash AS h FROM invii WHERE canale='email'").fetchall()}
    key = _chiave()
    in_brevo = []
    if key:
        from .auto_invio import _contatti_brevo
        with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
            in_brevo = _contatti_brevo(c)
    testi.append(" ".join(x.get("email") or "" for x in in_brevo))
    esito = conta_email(testi, bloccati, inviati)
    esito["righe_lead"] = len(testi) - 1
    esito["contatti_in_brevo"] = len(in_brevo)
    esito["disiscritti_noti"] = len(bloccati)
    return esito
