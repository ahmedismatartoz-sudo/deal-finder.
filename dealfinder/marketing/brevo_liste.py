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
# 24/11 (cliente): due numeri diversi.
# TETTO_BREVO: quanti contatti si possono TENERE in Brevo (il piano arriva a 500.000; prima meno di 1.500).
# TETTO_INVIO: a quante persone può arrivare un giro di email. Il cliente ha alzato il primo, NON il secondo: ogni
# lavoro che manda email calcola il suo posto come prima, come se il tetto fosse ancora 1.495 (mai più persone di prima).
TETTO_BREVO = 500_000
TETTO_INVIO = 1495
TETTO_INVIO_GIRO = 1400                  # giro_lotto: al massimo tanti contatti nuovi (e quindi email) per giro
TETTO_INVIO_AUTO = 1450                  # auto_invio (ciclo, invia_2_11): il vecchio predefinito di AUTO_TETTO_CONTATTI


def scegli_rimessi(emails, bloccati: set, gia_in_brevo: set, posto: int) -> list[str]:
    """Contatti vecchi da rimettere: email valide, mai disiscritti/bloccati (impronte), mai Loconte, senza doppioni,
    entro il posto libero in Brevo. Funzione pura (test in tests/test_brevo_rimessi.py)."""
    from .invii import impronta, norm_email
    out, visti = [], set()
    for e in emails:
        e = norm_email(e)
        if not e or e in visti or not _EMAIL_OK.match(e) or _NON_EMAIL.search(e) or "loconte" in e or _esclusi(e):
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


def _esclusi(e: str) -> bool:
    """9/11: clienti/contatti da non scrivere mai (Officine Mulino ha ricevuto la campagna 70 dopo essersi disiscritto)."""
    from .auto_invio import ESCLUSI_3_11
    return bool(ESCLUSI_3_11.search(e or "") or _ESCLUSI_ALTRI.search(e or "") or _ESCLUSI_TESTO.search(e or ""))


# 24/11: gli esclusi di raccolta_numeri che qui mancavano (GT Automobili, Privacar, Vroom) e gli stessi nomi di
# ESCLUSI_3_11 scritti con un separatore in mezzo (officine-mulino, sr.auto, auto-e-auto-varese)
_ESCLUSI_ALTRI = _re.compile(r"(officine[._-]*mulino|cosval|auto[._-]*e?[._-]*auto[._-]*varese|sr[._-]*auto|"
                             r"gt[._-]*automobili|privacar|vroom)", _re.I)


# gli stessi nomi scritti in qualsiasi modo (spazi, punti, trattini, &): "Auto&Auto - Varese", "G.T. Automobili",
# "S R Automobili", "lo-conte", s.r.auto@, www.auto-e-auto-varese.it, officine_mulino. I nomi corti vogliono un confine
# di parola davanti ("Carlo Conte" e "Cosvaldo... " a parte, non sono Loconte né SR Auto).
_SEP = r"[\s._&'/-]*"
_ESCLUSI_TESTO = _re.compile(
    r"((^|[^a-z])lo" + _SEP + r"conte([^a-z]|$)|officine" + _SEP + r"mulino|(^|[^a-z])cosval|"
    r"auto" + _SEP + r"(e|and)?" + _SEP + r"auto" + _SEP + r"varese|"
    r"(^|[^a-z])s" + _SEP + r"r" + _SEP + r"auto|(^|[^a-z])g" + _SEP + r"t" + _SEP + r"automobili|privacar|"
    r"(^|[^a-z])vroom)", _re.I)
_LOCONTE_TESTO = _re.compile(r"loconte|(^|[^a-z])lo" + _SEP + r"conte([^a-z]|$)", _re.I)
_DOMINI_PUBBLICI = _re.compile(
    r"^(gmail|googlemail|libero|hotmail|yahoo|outlook|icloud|alice|tiscali|virgilio|live|msn|me|mac|tin|inwind|iol|"
    r"fastwebnet|email|katamail|aruba|pec|legalmail|tim|teletu|vodafone|aol|gmx|mail|ymail|rocketmail|protonmail|"
    r"proton|yandex|blu|giallo|supereva|interfree|poste|postecert|arubapec|tiscalinet|hotmail)\.[a-z.]{2,6}$", _re.I)


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
        buoni = scegli_rimessi(vecchi, bloccati, in_brevo, TETTO_INVIO - len(in_brevo))
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
        if not e or e in visti or not _EMAIL_OK.match(e) or _NON_EMAIL.search(e) or "loconte" in e or _esclusi(e) or ".pec." in e \
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
        posto = TETTO_INVIO - len(in_brevo)
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
        if not e or e in visti or not _EMAIL_OK.match(e) or _NON_EMAIL.search(e) or "loconte" in e or _esclusi(e) \
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
        protette = liste_tutti(c)         # 24/11: mai cancellare chi sta in una lista "Skovo tutti ..." (carica_tutti)
        da_togliere = [x["email"] for x in contatti if lista_vecchia in (x.get("listIds") or [])
                       and not invii.contatto_bloccato(x) and x.get("email") and not protetto(x, protette)]
        tolti = 0
        for e in da_togliere:
            from urllib.parse import quote
            r = c.delete(f"{API}/contacts/{quote(e, safe='')}")
            if r.status_code in (204, 200, 404):
                tolti += 1
            _t.sleep(0.12)
        esito["tolti_da_brevo"] = tolti
        n_brevo = int((c.get(f"{API}/contacts", params={"limit": 1}).json() or {}).get("count") or 0)
        posto = TETTO_INVIO - n_brevo
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
        if not e or e in visti or not _EMAIL_OK.match(e) or _NON_EMAIL.search(e) or "loconte" in e or _esclusi(e):
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
        posto = max(0, TETTO_INVIO - n_brevo)
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
        for e in email_nel_testo(t):      # 24/11: valore per valore (prima "\\ninfo@" del JSON scritto dava "ninfo@")
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


# ---------- 9/11 pomeriggio (cliente: "dammi il numero di quelli che hanno aperto/cliccato le email") ----------
def righe_cliccati(emails: list[str], righe_db: list[dict]) -> list[dict]:
    """Abbina le email di chi ha cliccato ai commercianti dell'archivio (nome, comune, telefoni). Funzione pura.
    Chi non è in archivio resta con il solo indirizzo. Mai Loconte né gli esclusi."""
    per_email: dict = {}
    for r in righe_db:
        for e in r.get("email") or []:
            per_email.setdefault((e or "").strip().lower(), r)
    out, visti = [], set()
    for e in emails:
        e = (e or "").strip().lower()
        if not e or e in visti or "loconte" in e or _esclusi(e):
            continue
        visti.add(e)
        r = per_email.get(e) or {}
        tel = [t for t in (list(r.get("cell") or []) + [r.get("tel")]) if t]
        out.append({"email": e, "nome": r.get("nome") or "", "comune": r.get("comune") or "",
                    "prov": (r.get("prov") or "")[:2].upper(), "tel": list(dict.fromkeys(tel))})
    out.sort(key=lambda x: (not x["tel"], not x["prov"], x["prov"], x["comune"], x["nome"]))
    return out


def cliccati(campagne=(70, 72)) -> dict:
    """Esporta da Brevo chi ha cliccato le campagne, lo abbina all'archivio e manda l'elenco (nomi e numeri) SOLO per
    email a info@skovo.app. Nei log vanno soltanto i conteggi: mai email né telefoni."""
    from ..db import connect
    from ..sentinella import _invia_brevo
    key = _chiave()
    if not key:
        return {"errore": "chiave"}
    emails: list[str] = []
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=120) as c:
        for cid in campagne:
            emails += _esporta(c, int(cid), "clickers")
    with connect() as conn:
        db = [dict(r) for r in conn.execute(
            "SELECT e.email, COALESCE(d.nome, m.nome) AS nome, COALESCE(d.comune, m.comune) AS comune, "
            "COALESCE(d.prov, m.prov) AS prov, d.cell, m.tel FROM lead_email e "
            "LEFT JOIN lead_tutti_dealer d ON d.chiave = e.chiave "
            "LEFT JOIN lead_maps m ON e.chiave = 'maps:' || m.place_id "
            "WHERE cardinality(e.email) > 0").fetchall()]
    righe = righe_cliccati(emails, db)
    testo = "\n".join(f"{i}. {r['nome'] or '-'} | {r['comune'] or '-'} ({r['prov'] or '-'}) | "
                      f"{', '.join(r['tel']) or 'senza numero'} | {r['email']}" for i, r in enumerate(righe, 1))
    ok = _invia_brevo(f"Skovo - chi ha cliccato le email {'+'.join(map(str, campagne))} ({len(righe)})",
                      testo or "nessuno")
    return {"campagne": list(campagne), "cliccati": len(emails), "righe": len(righe),
            "con_numero": sum(1 for r in righe if r["tel"]), "email_mandata": ok}


# ---------- 24/11 11:35 (cliente: "carica tutti tutti tutti quelli che abbiamo in database tranne quelli esclusi") ----------
LISTA_TUTTI = "Skovo tutti 24-11"
BLOCCO_TUTTI = 500                       # contatti per chiamata a /contacts/import (come gli altri lavori)
LIMITE_PIANO = TETTO_BREVO               # decisione del cliente del 24/11: il piano arriva a 500.000 contatti
ATTRIBUTO_NOME = "AZIENDA"               # lo stesso attributo dei commercianti con il telefono
_TROVA_EMAIL = _re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_CHIAVI_NOME = ("nome", "commerciante", "companyName", "company_name", "company", "ragione_sociale", "ragioneSociale",
                "shop_name", "shopName", "dealer", "salone", "insegna", "name", "azienda")
_RIGA_ESCLUSA = "\x00riga esclusa"       # nome finto: l'indirizzo viene da una riga che nomina un escluso
_RIGA_LOCONTE = "\x00loconte"
# indirizzi che non sono di un commerciante: piattaforme, servizi di posta, segnaposto dei siti, noi stessi
_DOMINI_SPAZZATURA = _re.compile(
    r"(sentry\.io|wixpress|wix\.com|mailchimp|sendgrid|brevo\.com|sendinblue|example\.|gestionaleauto|carmove|motork|"
    r"(^|\.)dominio\.it$|tuosito|miosito|(^|\.)test\.(it|com)$|skovo\.app$)", _re.I)
_LOCALI_SPAZZATURA = _re.compile(
    r"^(noreply|no-reply|no_reply|donotreply|do-not-reply|mailer-daemon|postmaster|abuse|privacy|dpo|gdpr|webmaster|"
    r"hostmaster|bounces?|unsubscribe)([^a-z]|$)", _re.I)
_LOCALI_PIATTAFORMA = _re.compile(r"^(support|supporto|assistenza|helpdesk)([^a-z]|$)", _re.I)
_PIATTAFORME = _re.compile(r"(autoscout|subito\.|automobile\.it|mobile\.de|autouncle|carfax|facebook|instagram|google|"
                           r"aruba|register\.it|godaddy|ovh\.|siteground|wordpress|shopify|zendesk|freshdesk|hubspot|"
                           r"dealerk|cargurus|autohero|brumbrum)", _re.I)
# residui di codifica davanti all'indirizzo vero (u003einfo@, %3Einfo@ -> 3einfo@, %20info@ -> 20info@): "3e" e "20" solo
# davanti a una casella tipica, perché "3emme@", "2000auto@", "20auto@", "22auto@", "27auto@" sono commercianti veri
_LOCALE_CODIFICA = _re.compile(r"^(u00[0-9a-f]{2}[a-z]|(3e|3c|20|22|27)(info|vendite|commerciale|amministrazione|"
                               r"contatti|contact|mail|email|segreteria|sales|direzione|ufficio|admin|posta)([^a-z]|$))",
                               _re.I)
_LOCALE_ESADECIMALE = _re.compile(r"^[0-9a-f-]{24,}$", _re.I)


def _stringhe(v):
    """Tutti i valori di testo di un dato JSON, uno alla volta (anche dentro elenchi e oggetti)."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _stringhe(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _stringhe(x)


def email_nel_testo(t) -> list[str]:
    """Indirizzi in un testo o in un dato JSON, cercati valore per valore: nel JSON scritto un a capo è "\\n" e
    "\\ninfo@x.it" darebbe "ninfo@x.it". Un testo che non è JSON si legge com'è, tolte le sequenze \\n \\t \\r."""
    if isinstance(t, str):
        try:
            t = json.loads(t)
        except ValueError:
            pass
    if isinstance(t, str):
        return _TROVA_EMAIL.findall(_re.sub(r"\\+[ntr]|\\+u[0-9a-fA-F]{4}", " ", t))
    return [e for x in _stringhe(t) for e in _TROVA_EMAIL.findall(_re.sub(r"\\+[ntr]", " ", x))]


_GESTORI_PEC = frozenset((
    "pec.it", "legalmail.it", "postecert.it", "sicurezzapostale.it", "actaliscertymail.it", "ticertifica.it",
    "arubapec.it", "pecaruba.it", "pecimprese.it", "registerpec.it", "mypec.eu", "gigapec.it", "casellapec.com",
    "legalpec.it", "pec-legal.it", "lamiapec.it", "postacert.it", "postacertificata.it", "pecspeciale.it",
    "cert.legalmail.it", "pec.net", "pec.eu", "pec.com", "pecmail.it", "peceasy.it", "pecprivato.it",
    "pecditta.com", "cgn.legalmail.it", "kelipec.it", "open.legalmail.it", "messaggipec.it", "trustpec.it"))


def pec(e: str) -> bool:
    """Indirizzo di posta certificata: gestore noto (dominio esatto o suo sottodominio), un pezzo del dominio che è
    "pec" o "cert" o che finisce per "pec" (pec.salone.it, salone.pec.it, ascompec.it, cnapec.it, keypec.it,
    dadapec.com, cert.x.it), o un gestore che comincia per "pec" (pecveneto.it). Non basta "pec" in mezzo a una parola:
    specialcar.it è un salone. Solo per carica_tutti: gli altri lavori restano con gruppo_dominio. Funzione pura."""
    pezzi = e.rpartition("@")[2].lower().split(".")
    if any(x in ("pec", "cert", "legalmail") or x.endswith("pec") or x in _GESTORI_PEC_NOMI for x in pezzi[:-1]):
        return True
    return any(".".join(pezzi[k:]) in _GESTORI_PEC for k in range(len(pezzi) - 1))


_GESTORI_PEC_NOMI = frozenset(("pecveneto", "pecimprese", "pecaruba", "pecmail", "peceasy", "pecprivato", "pecditta",
                               "pecspeciale", "postecert", "postacert", "postacertificata", "sicurezzapostale",
                               "actaliscertymail", "ticertifica", "pec-legal", "pecavvocati", "pecconfesercenti"))


def privato(dati) -> bool:
    """La riga (a qualsiasi livello, anche dentro elenchi) dice di non essere un'attività: "azienda" falsa oppure
    venditore/seller_type "privato". Funzione pura."""
    if isinstance(dati, dict):
        a = dati.get("azienda", True)
        if a is False or (isinstance(a, (int, float)) and a == 0) or \
                (isinstance(a, str) and a.strip().lower() in ("false", "0", "no")):
            return True
        for k in ("venditore", "seller_type", "sellerType", "tipo_venditore", "tipo"):
            if isinstance(dati.get(k), str) and dati[k].strip().lower() in ("privato", "private", "privati"):
                return True
        return any(privato(x) for x in dati.values())
    if isinstance(dati, (list, tuple)):
        return any(privato(x) for x in dati)
    return False


def _nome_escluso(nome: str) -> bool:
    """Nome (o testo intero di una riga) di un commerciante da non scrivere mai: i nomi del sito e di raccolta_numeri,
    le espressioni usate per gli indirizzi (www.auto-e-auto-varese.it, officine_mulino) e gli stessi nomi scritti con
    punti, spazi, trattini o & in mezzo ("G.T. Automobili", "Auto&Auto - Varese", "S R Automobili")."""
    if not nome:
        return False
    if nome == _RIGA_ESCLUSA:
        return True
    from ..pipeline.raccolta_numeri import ESCLUSI_EXTRA
    from ..web.sito_regole import nomi_esclusi_re
    if _re.search(nomi_esclusi_re() + "|" + "|".join(f"({x})" for x in ESCLUSI_EXTRA), nome, _re.I):
        return True
    return _esclusi(nome)


def _dominio_proprio(e: str) -> str:
    """Il dominio dell'indirizzo se è di un'azienda ("" per gmail, libero, hotmail...): serve a escludere gli altri
    indirizzi dello stesso commerciante escluso."""
    dom = e.rpartition("@")[2]
    return "" if _DOMINI_PUBBLICI.match(dom) else dom


def spazzatura(e: str) -> bool:
    """Indirizzo che non è di un commerciante: piattaforme e servizi, caselle automatiche, segnaposto, residui di
    codifica presi dalle pagine (%20info@, u003einfo@), stringhe esadecimali, indirizzi lunghissimi. Funzione pura."""
    from ..pipeline.lead_email import NO_DOMINI
    loc, _, dom = e.rpartition("@")
    # caselle vere che NO_DOMINI (pensato per le pagine dei siti) butterebbe: googlemail, aruba.it, *.altervista.org
    casella = dom in ("gmail.com", "googlemail.com", "aruba.it") or dom.endswith(".altervista.org")
    if (not casella and NO_DOMINI.search(dom)) or _DOMINI_SPAZZATURA.search(dom) or _LOCALI_SPAZZATURA.search(loc):
        return True
    if _LOCALI_PIATTAFORMA.search(loc) and _PIATTAFORME.search(dom):
        return True
    return bool(len(e) > 60 or "%" in e or _LOCALE_CODIFICA.search(loc) or _LOCALE_ESADECIMALE.search(loc))


def scegli_tutti(voci, bloccati: set, cancellati: set = frozenset()) -> tuple[list[dict], dict]:
    """voci: coppie (email, nome o None). Restituisce i contatti da caricare ({"email", "nome"}) e i conteggi per
    motivo di esclusione. Funzione pura: mai Loconte né gli esclusi (per indirizzo o per UNO QUALSIASI dei nomi visti
    con quell'indirizzo), mai PEC, mai spazzatura, mai disiscritti/rimbalzati (impronte), mai indirizzi non validi;
    ognuno una volta sola. `cancellati`: chi era stato caricato in Brevo e poi tolto per stare sotto il vecchio tetto;
    per decisione del cliente ("tutti tutti tutti") rientra, e si conta in rimessi_cancellati."""
    from collections import Counter
    from .invii import impronta, norm_email
    nomi: dict[str, list[str]] = {}
    n = Counter()
    for e, nome in voci:
        e = norm_email(e).strip(".")
        if not e:
            continue
        if not _EMAIL_OK.match(e) or _NON_EMAIL.search(e):
            n["non_valide"] += 1          # qui i doppioni si contano: un indirizzo non valido non ha identità
            continue
        nome = nome if nome in (_RIGA_ESCLUSA, _RIGA_LOCONTE) else " ".join(str(nome or "").split())[:80]
        visti = nomi.setdefault(e, [])
        if nome and nome not in visti:
            visti.append(nome)
    n["raccolti"] = len(nomi)
    loconte = {e for e in nomi if _LOCONTE_TESTO.search(e) or any(x == _RIGA_LOCONTE or _LOCONTE_TESTO.search(x)
                                                                  for x in nomi[e])}
    esclusi = {e for e in nomi if e not in loconte and (_esclusi(e) or any(_nome_escluso(x) for x in nomi[e]))}
    # gli altri indirizzi dello stesso commerciante (stesso dominio aziendale) seguono la stessa sorte
    dom_loconte = {_dominio_proprio(e) for e in loconte} - {""}
    dom_esclusi = {_dominio_proprio(e) for e in esclusi} - {""}
    out = []
    for e in sorted(nomi):
        if e in loconte:
            n["escluse_loconte"] += 1
        elif e in esclusi:
            n["escluse_altri_esclusi"] += 1
        elif _dominio_proprio(e) in dom_loconte | dom_esclusi:
            n["escluse_stesso_dominio_di_un_escluso"] += 1
        elif pec(e):
            n["escluse_pec"] += 1
        elif spazzatura(e):
            n["escluse_spazzatura"] += 1
        elif impronta(e) in bloccati:
            n["escluse_disiscritte_o_rimbalzate"] += 1
        else:
            n["rimessi_cancellati"] += e in cancellati
            out.append({"email": e, "nome": next((x for x in nomi[e] if not x.startswith("\x00")), "")})
    n["rimessi_cancellati"] += 0
    n["da_caricare"] = len(out)
    n["con_nome"] = sum(1 for x in out if x["nome"])
    return out, dict(n)


def voci_da_riga(dati) -> list[tuple[str, str | None]] | None:
    """Email (con il nome, se la riga lo dice) di una riga di lead_consegne. None se la riga dice di non essere
    un'attività ("azienda": false): solo commercianti, mai privati. Se il testo della riga nomina Loconte o un altro
    escluso, i suoi indirizzi escono con un nome finto che li fa escludere dovunque compaiano. Funzione pura."""
    if isinstance(dati, str):
        try:
            dati = json.loads(dati)
        except ValueError:
            pass
    if privato(dati):
        return None
    nome = None
    if isinstance(dati, dict):
        for k in _CHIAVI_NOME:
            if isinstance(dati.get(k), str) and dati[k].strip() and dati[k].strip().lower() not in ("true", "false"):
                nome = dati[k]
                break
    emails = email_nel_testo(dati)
    if not emails:
        return []
    testo = " | ".join(_stringhe(dati)) if not isinstance(dati, str) else dati
    out = [(e, nome) for e in emails]
    if _LOCONTE_TESTO.search(testo):
        out += [(e, _RIGA_LOCONTE) for e in emails]
    elif _nome_escluso(testo):
        out += [(e, _RIGA_ESCLUSA) for e in emails]
    return out


# Chi è entrato in Brevo con carica_tutti: tabella a parte (NON brevo_inviati, che per gli altri lavori vuol dire "già
# contattato" e cambierebbe i loro conti). auto_invio la legge come "già raggiunti": a loro il giro automatico non scrive.
SQL_TUTTI_CARICATI = ("CREATE TABLE IF NOT EXISTS brevo_tutti_caricati (email TEXT PRIMARY KEY, lista TEXT, "
                      "quando TIMESTAMPTZ NOT NULL DEFAULT now())")


def segna_tutti(conn, emails, lista: str = LISTA_TUTTI) -> None:
    conn.execute(SQL_TUTTI_CARICATI)
    conn.execute("INSERT INTO brevo_tutti_caricati (email, lista) SELECT unnest(%s::text[]), %s ON CONFLICT DO NOTHING",
                 (list(emails), lista))
    conn.commit()


def caricati_tutti(conn) -> set[str]:
    """Gli indirizzi caricati da carica_tutti (insieme vuoto se non è mai girato)."""
    conn.execute(SQL_TUTTI_CARICATI)
    conn.commit()
    return {r["email"] for r in conn.execute("SELECT email FROM brevo_tutti_caricati").fetchall()}


def incompleto(esito: dict) -> str | None:
    """Perché il caricamento NON si può segnare fatto (None se è completo): errore, fermo, una fonte non letta, blocchi
    rifiutati, un processo di import fallito o non finito in tempo. Funzione pura."""
    if esito.get("errore") or esito.get("fermo"):
        return str(esito.get("errore") or esito.get("fermo"))
    for f in ("lead_consegne", "lead_email"):
        if not isinstance((esito.get("fonti") or {}).get(f), int):
            return f"{f}_non_letta"
    if esito.get("blocchi_falliti"):
        return "blocchi_falliti"
    stati = esito.get("processi") or {}
    if any(k != "completed" for k in stati):
        return "import_fallito" if any(k in ("failed", "cancelled") for k in stati) else "import_non_finito"
    return None


def _scorri(conn, sql: str, blocco: int = 2000):
    """Righe di una lettura grande, a blocchi (cursore sul server: mai tutta la tabella in memoria), al massimo 120 s."""
    conn.execute("SET LOCAL statement_timeout = '120s'")
    with conn.cursor(name="carica_tutti") as cur:
        cur.itersize = blocco
        cur.execute(sql)
        while True:
            righe = cur.fetchmany(blocco)
            if not righe:
                break
            yield from righe


def _voci_db(conn) -> tuple[set, set | None, dict]:
    """Le email di commercianti nel database: lead_consegne (la fonte di conteggio_email, solo le righe con una @) e
    lead_email (con il nome dall'archivio dei commercianti) e brevo_inviati (chi è già stato caricato in Brevo).
    Coppie (email, nome) senza doppioni; a parte gli indirizzi di brevo_inviati (None se non si legge), per contare
    chi rientra. Una tabella che manca non ferma le altre."""
    from .invii import norm_email
    voci, fonti = set(), {}

    def leggi(nome, sql, fn):
        prima = len(voci)
        try:
            for r in _scorri(conn, sql):
                fn(r)
            conn.commit()
        except Exception as e:
            conn.rollback()
            fonti[nome] = f"non letta ({type(e).__name__})"
            return False
        fonti[nome] = len(voci) - prima
        return True

    def consegne(r):
        v = voci_da_riga(r["d"])
        if v is None:
            fonti["privati_esclusi"] = fonti.get("privati_esclusi", 0) + 1
        else:
            voci.update((norm_email(e), nome) for e, nome in v)

    leggi("lead_consegne", "SELECT dati::text AS d FROM lead_consegne WHERE strpos(dati::text, '@') > 0", consegne)
    leggi("lead_email",
          "SELECT e.email, COALESCE(d.nome, m.nome) AS nome FROM lead_email e "
          "LEFT JOIN lead_tutti_dealer d ON d.chiave = e.chiave "
          "LEFT JOIN lead_maps m ON e.chiave = 'maps:' || m.place_id WHERE cardinality(e.email) > 0",
          lambda r: voci.update((norm_email(e), r.get("nome")) for e in (r.get("email") or [])))
    inviati: set = set()
    ok = leggi("brevo_inviati", "SELECT email FROM brevo_inviati", lambda r: inviati.add(norm_email(r["email"])))
    if ok:                                # di nuovo una fonte: anche chi era stato tolto da Brevo per il vecchio tetto
        voci.update((e, None) for e in inviati if e)
        fonti["brevo_inviati"] = len(inviati)
    return voci, (inviati if ok else None), fonti


def _limite_piano() -> tuple[int, str]:
    """Quanti contatti può tenere il conto Brevo e da dove viene il numero. Brevo non lo scrive in /v3/account e qui
    non si stima niente: vale BREVO_LIMITE_PIANO (intero) se c'è, altrimenti 500.000 per decisione esplicita del
    cliente del 24/11 (piano fino a 500.000 contatti)."""
    v = (os.environ.get("BREVO_LIMITE_PIANO") or "").strip().replace(".", "")
    if v.isdigit() and int(v) > 0:
        return int(v), "BREVO_LIMITE_PIANO"
    return LIMITE_PIANO, "decisione_cliente"


PREFISSO_TUTTI = "Skovo tutti"           # liste di carica_tutti: i loro contatti non si cancellano per "fare posto"


def liste_tutti(c: httpx.Client) -> set[int]:
    """Id delle liste il cui nome comincia con PREFISSO_TUTTI (tutte le pagine). Se Brevo non risponde solleva un
    errore: chi sta per cancellare contatti si ferma, non cancella alla cieca."""
    ids, off = set(), 0
    while True:
        r = c.get(f"{API}/contacts/lists", params={"limit": 50, "offset": off})
        r.raise_for_status()
        ls = r.json().get("lists") or []
        ids |= {x["id"] for x in ls if str(x.get("name") or "").startswith(PREFISSO_TUTTI)}
        if len(ls) < 50:
            return ids
        off += 50


def protetto(contatto: dict, ids: set) -> bool:
    """Il contatto Brevo sta in una lista "Skovo tutti ...": non va cancellato dagli altri lavori. Funzione pura."""
    return bool(ids and set(contatto.get("listIds") or []) & set(ids))


def _lista_tutti(c: httpx.Client) -> int:
    """Id della lista LISTA_TUTTI (cercata in tutte le pagine: le liste sono più di 50), creata se manca."""
    off = 0
    while True:
        r = c.get(f"{API}/contacts/lists", params={"limit": 50, "offset": off})
        r.raise_for_status()
        ls = r.json().get("lists") or []
        for x in ls:
            if x.get("name") == LISTA_TUTTI:
                return x["id"]
        if len(ls) < 50:
            break
        off += 50
    r = c.post(f"{API}/contacts/lists", json={"name": LISTA_TUTTI, "folderId": _cartella(c)})
    r.raise_for_status()
    return r.json()["id"]


def _attributo_nome(c: httpx.Client) -> bool:
    """True se in Brevo c'è (o si riesce a creare) l'attributo di testo ATTRIBUTO_NOME; altrimenti i nomi non si mandano."""
    def c_e():
        r = c.get(f"{API}/contacts/attributes")
        return r.status_code == 200 and any(a.get("name") == ATTRIBUTO_NOME for a in (r.json() or {}).get("attributes") or [])
    try:
        if c_e():
            return True
        c.post(f"{API}/contacts/attributes/normal/{ATTRIBUTO_NOME}", json={"type": "text"})
        return c_e()
    except Exception:
        return False


def _importa_blocco(c: httpx.Client, corpo: dict, codici, dormi, tentativi: int = 3):
    """Un blocco a /contacts/import. Su 429, 5xx o errore di rete riprova (attesa da Retry-After, al massimo 60 s).
    Restituisce la risposta riuscita o None; in `codici` quante volte è arrivato ogni codice."""
    for k in range(tentativi):
        try:
            r = c.post(f"{API}/contacts/import", json=corpo)
        except httpx.HTTPError as e:
            codici[f"rete_{type(e).__name__}"] += 1
            attesa = 5.0 * (k + 1)
        else:
            codici[str(r.status_code)] += 1
            if r.status_code < 300:
                return r
            if r.status_code != 429 and r.status_code < 500:
                return None
            try:
                attesa = float((getattr(r, "headers", None) or {}).get("Retry-After") or 5.0 * (k + 1))
            except ValueError:
                attesa = 5.0 * (k + 1)
        if k + 1 < tentativi:
            dormi(min(60.0, max(0.0, attesa)))
    return None


def carica_tutti(attesa_s: int = 180, dormi=None) -> dict:
    """Carica in Brevo, nella lista nuova LISTA_TUTTI, tutte le email di commercianti che abbiamo (lead_consegne,
    lead_email, brevo_inviati e contatti già in Brevo) tranne gli esclusi: Loconte e gli altri esclusi (per indirizzo,
    nome o dominio), PEC, spazzatura, privati, disiscritti/rimbalzati/bloccati. Vale TETTO_BREVO (500.000: i contatti
    che si possono tenere), non i limiti di invio. Non crea campagne e non invia niente; segna i caricati in
    brevo_tutti_caricati, così auto_invio non scrive loro. Restituisce solo conteggi; `incompleto(esito)` dice se il
    lavoro è da rifare (fermo, fonte non letta, blocchi rifiutati, import fallito o non finito)."""
    import time as _t
    from collections import Counter
    from ..db import connect
    from . import invii
    from .auto_invio import _contatti_brevo
    dormi = dormi or _t.sleep
    key = _chiave()
    if not key:
        return {"errore": "chiave"}
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=120) as c:
        r = c.get(f"{API}/account")
        if r.status_code != 200:
            return {"errore": "chiave rifiutata"}
        limite, limite_fonte = _limite_piano()
        piano = next((str(p.get("name") or "")[:30] for p in (r.json() or {}).get("planVerticals") or []
                      if p.get("planCategory") == "Marketing"), "")
        contatti = _contatti_brevo(c)
        with connect() as conn:
            invii.sincronizza_da_brevo(conn, contatti)
            nel_db = invii.disiscritti(conn)
            voci, inviati, fonti = _voci_db(conn)
        bloccati = nel_db | _bloccati_0111()
        fonti["brevo"] = len(contatti)
        in_brevo = {invii.norm_email(x.get("email")) for x in contatti if x.get("email")}
        for x in contatti:                # i nomi scritti in Brevo servono solo a riconoscere gli esclusi
            nomi = [v for v in (x.get("attributes") or {}).values() if isinstance(v, str) and v.strip()] or [None]
            voci.update((invii.norm_email(x.get("email")), v) for v in nomi)
        bloccati |= {invii.impronta(x["email"]) for x in contatti if x.get("email") and invii.contatto_bloccato(x)}
        privati_esclusi = fonti.pop("privati_esclusi", 0)
        buoni, esito = scegli_tutti(sorted(voci, key=lambda v: (v[0] or "", v[1] or "")), bloccati,
                                    (inviati or set()) - in_brevo)
        nuovi = sum(1 for x in buoni if x["email"] not in in_brevo)
        esito.update(privati_esclusi=privati_esclusi, fonti=fonti, in_brevo=len(in_brevo), nuovi_per_brevo=nuovi, disiscritti_noti=len(bloccati),
                     limite_piano=limite, limite_fonte=limite_fonte, piano=piano,
                     totale_in_brevo_dopo=len(in_brevo) + nuovi, caricati=0)
        if len(bloccati) < BLOCCATI_MINIMI or not nel_db:
            esito["fermo"] = "disiscritti_incompleti"
            return esito
        if not all(isinstance(fonti.get(f), int) for f in ("lead_consegne", "lead_email")):
            esito["fermo"] = "fonte_non_letta"    # senza una fonte il caricamento sarebbe parziale: niente, si riprova
            return esito
        if len(in_brevo) + nuovi > limite:
            esito["fermo"] = "piano_insufficiente"
            return esito
        if not buoni:
            return esito
        con_nomi = any(x["nome"] for x in buoni) and _attributo_nome(c)
        lid = _lista_tutti(c)
        esito["lista_id"] = lid
        processi, falliti, codici, nomi_mandati = [], 0, Counter(), 0
        for k in range(0, len(buoni), BLOCCO_TUTTI):
            blocco = buoni[k:k + BLOCCO_TUTTI]
            # il nome solo ai contatti nuovi: l'AZIENDA già scritta in Brevo non si tocca
            corpo = [{"email": x["email"], "attributes": {ATTRIBUTO_NOME: x["nome"]}}
                     if con_nomi and x["nome"] and x["email"] not in in_brevo else {"email": x["email"]} for x in blocco]
            r = _importa_blocco(c, {"jsonBody": corpo, "listIds": [lid], "updateExistingContacts": True,
                                    "emptyContactsAttributes": False}, codici, dormi)
            if r is None:
                falliti += 1
                continue
            esito["caricati"] += len(blocco)
            with connect() as conn:       # marcatore per auto_invio: a chi entra da qui il giro automatico non scrive
                segna_tutti(conn, [x["email"] for x in blocco])
            nomi_mandati += sum(1 for x in corpo if "attributes" in x)
            try:
                pid = (r.json() or {}).get("processId")
            except ValueError:
                pid = None
            if pid:
                processi.append(pid)
        esito.update(blocchi=-(-len(buoni) // BLOCCO_TUTTI), blocchi_falliti=falliti, import_codici=dict(codici),
                     nomi_mandati=nomi_mandati, process_id=processi)
        stati: dict = {}
        fine = _t.monotonic() + attesa_s
        aperti = list(processi)
        while aperti and _t.monotonic() < fine:
            dormi(10 if attesa_s >= 10 else 0)
            for pid in list(aperti):
                try:
                    st = (c.get(f"{API}/processes/{pid}").json() or {}).get("status") or "sconosciuto"
                except Exception:
                    st = "non_letto"
                stati[pid] = st
                if st in ("completed", "failed", "cancelled"):
                    aperti.remove(pid)
        if processi:
            esito["processi"] = dict(Counter(stati.get(p, "in_attesa") for p in processi))
        esito["processi_finiti"] = bool(processi) and not aperti      # False: Brevo sta ancora caricando
        try:                              # i contatti davvero nella lista (i blocchi accettati sono solo "presi in carico")
            esito["nella_lista"] = int((c.get(f"{API}/contacts/lists/{lid}").json() or {}).get("uniqueSubscribers") or 0)
        except Exception:
            pass
    return esito
