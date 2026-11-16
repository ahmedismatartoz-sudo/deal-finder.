"""16/11 (cliente): "dopo l'invio, chi l'ha ricevuta esce da Brevo e ci entrano gli altri dell'archivio che non l'hanno
ancora ricevuta, poi la stessa email anche a loro, finché non l'hanno ricevuta tutti".

Un giro = BREVO_IMPORT_LISTE=giro_lotto:<campagna_base>:<quando>[~<precedenti>]   (web/app.py, una volta sola)
  es. giro_lotto:77:2026-11-17T09:00:00+01:00     (precedenti facoltative, es. ~73,74,75,76; di default le 4 campagne
  prima della base che risultano inviate). <quando> senza fuso = ora di Roma.
Il giro dopo si lancia con la campagna creata da questo giro come base (si legge nel log GIRO_LOTTO, "campagna").

Cosa fa, in ordine (si ferma al primo problema, PRIMA di qualsiasi cancellazione):
 0. legge la campagna base: deve essere "sent", con oggetto, testo HTML con il link di disiscrizione e mittente; la
    campagna del giro (nome "Skovo lotto gg-mm (rNN)") non deve esistere già; <quando> almeno 20 minuti nel futuro.
 1. esporta da Brevo disiscritti, rimbalzi duri e morbidi della base e delle precedenti e TUTTI i destinatari della
    base. Sicurezze: se un'esportazione non riesce ci si ferma; per la base i disiscritti e i rimbalzi duri esportati
    devono essere almeno quanti ne contano le statistiche della campagna, i destinatari almeno il 90% degli inviati, e i
    contatti bloccati nella lista almeno quante le segnalazioni di spam (chi segnala spam Brevo lo mette in blacklist).
    Nel database (tabella disiscritti, solo impronte) entrano: disiscritti, rimbalzi duri, rimbalzi morbidi ripetuti
    (in almeno 2 delle campagne guardate) e tutti i contatti bloccati in Brevo (blacklist = anche le segnalazioni di
    spam). Poi i disiscritti noti devono essere almeno BLOCCATI_MINIMI.
    Chi ha ricevuto la base resta segnato nella tabella invii (lavoro "giro_lotto:r<radice>", radice = la prima
    campagna della catena, es. 77): ai giri successivi non viene mai ricaricato, anche se Brevo non lo ha più.
    I rimbalzi morbidi della base NON contano come ricevuti (l'email non è arrivata): né segnati né cancellati, così
    possono riceverla in un giro dopo (se non sono rimbalzi ripetuti, che diventano bloccati).
 2. sceglie i prossimi dell'archivio (lead_email): un indirizzo per salone, mai chi ha già ricevuto questa email
    (base + giri precedenti), mai disiscritti/bloccati/rimbalzati, mai Loconte, Officine Mulino, ESCLUSI_3_11 e
    sito_regole.COMMERCIANTI_ESCLUSI (su indirizzo e nome del salone), niente PEC e Libero/Microsoft (come per la
    campagna 77); prima i più vicini a Milano. Se non resta nessuno: niente cancellazioni, niente campagna, log.
 3. cancella da Brevo (DELETE /contacts/{email}: il contatto sparisce dal conto, IRREVERSIBILE) solo i contatti
    delle liste della base che risultano fra i destinatari della base (tolti i rimbalzi morbidi), non bloccati (i
    bloccati restano in Brevo, così Brevo continua a bloccarli), con TUTTE le liste fra quelle della base (chi è anche
    in un'altra lista non si tocca) e non in una lista di una campagna programmata o in invio. Rimuoverli solo dalla
    lista non libera posto: il tetto del piano conta i contatti, non le iscrizioni.
 4. ricontare i contatti in Brevo, caricare i scelti entro TETTO_BREVO in una lista nuova "Skovo lotto gg-mm".
 5. creare la campagna con oggetto, anteprima, HTML, mittente (id), risposta, intestazione e piè di pagina della
    base, sulla lista nuova, programmata per <quando> (invia Brevo). Se l'import o la creazione falliscono i
    contatti scelti tornano disponibili per il giro dopo.
Nei log solo numeri (e il codice del motivo di uno stop), mai indirizzi.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

lg = logging.getLogger("dealfinder.marketing")

PRECEDENTI_DEFAULT = 4          # quante campagne prima della base guardare per disiscritti/rimbalzi, se non indicate
QUOTA_DESTINATARI = 0.9         # destinatari esportati / inviati delle statistiche: sotto, esportazione incompleta
ANTICIPO_MIN = 20               # minuti: <quando> deve essere almeno così nel futuro all'avvio


def _dormi(s: float) -> None:
    if s:
        time.sleep(s)


def _scarica(url: str) -> bytes | None:
    """Scarica il file dell'esportazione (fuori da Brevo: niente chiave API nella richiesta)."""
    import httpx
    d = httpx.get(url, timeout=120, follow_redirects=True)
    return d.content if d.status_code == 200 else None


# ---------- funzioni pure ----------
def leggi_valore(v: str) -> tuple[int, str, tuple[int, ...] | None]:
    """'giro_lotto:77:2026-11-17T09:00:00+01:00[~73,74]' -> (77, quando, (73, 74) | None). Accetta anche '@'."""
    corpo = v.split(":", 1)[1] if v.startswith("giro_lotto:") else v
    corpo, _, prec = corpo.replace("#", "~").partition("~")
    m = re.match(r"\s*(\d+)\s*[:@]\s*(\S+)\s*$", corpo)
    if not m:
        raise ValueError("formato: giro_lotto:<campagna_base>:<quando>")
    precedenti = tuple(int(x) for x in re.findall(r"\d+", prec)) or None
    return int(m.group(1)), m.group(2), precedenti


def normalizza_quando(q: str) -> str:
    """Data e ora ISO; senza fuso = ora di Roma. Restituisce ISO con fuso (come vuole Brevo)."""
    q = q.strip().replace(" ", "T")
    if q.endswith("Z"):
        q = q[:-1] + "+00:00"
    d = datetime.fromisoformat(q)
    if d.tzinfo is None:
        from zoneinfo import ZoneInfo
        d = d.replace(tzinfo=ZoneInfo("Europe/Rome"))
    return d.isoformat(timespec="seconds")


def radice_da_nome(nome: str | None, base: int) -> int:
    """La campagna di un giro si chiama 'Skovo lotto gg-mm (r77)': la catena resta quella della 77."""
    m = re.search(r"\(r(\d+)\)", nome or "")
    return int(m.group(1)) if m else int(base)


def rimbalzi_ripetuti(soft_per_campagna: list[list[str]], volte: int = 2) -> set[str]:
    """Indirizzi con rimbalzo morbido in almeno `volte` campagne diverse."""
    from collections import Counter
    n = Counter()
    for elenco in soft_per_campagna:
        n.update({(e or "").strip().lower() for e in elenco if e})
    return {e for e, k in n.items() if k >= volte}


def _esclusi_re():
    try:
        from ..web.sito_regole import nomi_esclusi_re
        return re.compile(nomi_esclusi_re(commercianti=True), re.I)
    except Exception:
        return re.compile(r"loconte|officine[\s.-]*mulino", re.I)


def scegli_lotto(righe: list[dict], bloccati: set, gia: set, totale: int) -> list[dict]:
    """Prossimi dell'archivio: un indirizzo per salone (prima i più vicini a Milano), mai impronte in `bloccati` o
    `gia` (chi ha già ricevuto: tutto il salone resta fuori), mai esclusi per indirizzo o nome del salone. Funzione pura."""
    from .invii import impronta
    from .su_misura import scegli_secondo
    esc = _esclusi_re()
    buone = []
    for r in righe:
        if esc.search(str(r.get("nome") or "")):
            continue
        if any(impronta(e) in gia for e in (r.get("email") or []) if e):
            continue                                   # il salone l'ha già ricevuta (anche a un altro indirizzo)
        em = [e for e in (r.get("email") or []) if e and not esc.search(e)]
        if em:
            buone.append({**r, "email": em})
    return scegli_secondo(buone, set(bloccati) | set(gia), set(), max(0, totale))


def _stat(camp: dict) -> dict:
    """Statistiche della campagna: globalStats, altrimenti somma delle campaignStats per lista. {} se assenti."""
    s = (camp or {}).get("statistics") or {}
    g = s.get("globalStats") or {}
    if g.get("sent"):
        return g
    tot: dict = {}
    for x in s.get("campaignStats") or []:
        for k, v in x.items():
            if isinstance(v, (int, float)) and k != "listId":
                tot[k] = tot.get(k, 0) + v
    return tot if tot.get("sent") else g


# ---------- Brevo ----------
def _esporta(c, api: str, cid: int, tipo: str, atteso: int | None = None, pausa: float = 5,
             giri: int = 120) -> list[str] | None:
    """Indirizzi esportati, oppure None se l'esportazione non è riuscita (mai una lista vuota al posto di un errore)."""
    from .brevo_liste import emails_da_export
    r = c.post(f"{api}/emailCampaigns/{cid}/exportRecipients", json={"recipientsType": tipo})
    try:
        pid = (r.json() or {}).get("processId") if r.status_code < 300 else None
    except ValueError:
        pid = None
    if not pid:
        return None
    for _ in range(giri):
        _dormi(pausa)
        p = c.get(f"{api}/processes/{pid}").json() or {}
        st = p.get("status")
        if st == "completed":
            if not p.get("export_url"):
                return [] if atteso == 0 else None
            d = _scarica(p["export_url"])
            return None if d is None else emails_da_export(d)
        if st in ("failed", "aborted"):
            return None
    return None


def _contatti_lista(c, api: str, lid: int) -> list[dict] | None:
    out, off = [], 0
    while True:
        r = c.get(f"{api}/contacts/lists/{lid}/contacts", params={"limit": 500, "offset": off})
        if r.status_code >= 300:
            return None
        cs = (r.json() or {}).get("contacts") or []
        out += cs
        if len(cs) < 500:
            return out
        off += 500


def _liste_in_attesa(c, api: str) -> set[int] | None:
    """Liste di campagne programmate o in invio: i loro contatti non si cancellano."""
    out: set[int] = set()
    for st in ("queued", "inProcess"):
        off = 0
        while True:
            r = c.get(f"{api}/emailCampaigns", params={"status": st, "limit": 100, "offset": off})
            if r.status_code >= 300:
                return None
            cs = (r.json() or {}).get("campaigns") or []
            for x in cs:
                out |= {int(i) for i in ((x.get("recipients") or {}).get("lists") or [])}
            if len(cs) < 100:
                break
            off += 100
    return out


def giro_lotto(base: int, quando: str, precedenti=None, client=None, pausa: float = 5,
               pausa_cancella: float = 0.12, adesso: datetime | None = None) -> dict:
    """Vedi la descrizione del modulo. Restituisce un riepilogo di soli numeri (più 'fermo' con il motivo)."""
    from . import invii
    from .auto_invio import _contatti_brevo
    from .brevo_liste import API, BLOCCATI_MINIMI, TETTO_BREVO, _bloccati_0111, _cartella, _chiave, _liste, nomi_campagne
    from .su_misura import _aspetta_import, _righe_db
    from .. import db as _db
    esito: dict = {"base": int(base)}
    try:
        quando = normalizza_quando(quando)
    except ValueError:
        return {**esito, "fermo": "quando_non_valido"}
    adesso = adesso or datetime.now(timezone.utc)
    if datetime.fromisoformat(quando) < adesso + timedelta(minutes=ANTICIPO_MIN):
        return {**esito, "fermo": "quando_troppo_vicino_o_passato"}
    if client is None:
        import httpx
        key = _chiave()
        if not key:
            return {**esito, "errore": "chiave"}
        with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
            return giro_lotto(base, quando, precedenti, client=c, pausa=pausa, pausa_cancella=pausa_cancella,
                              adesso=adesso)
    c = client
    if c.get(f"{API}/account").status_code != 200:
        return {**esito, "errore": "chiave rifiutata"}

    # ---- 0. campagna base ----
    r = c.get(f"{API}/emailCampaigns/{int(base)}", params={"statistics": "globalStats"})
    camp = r.json() if r.status_code < 300 else {}
    st = _stat(camp)
    liste_base = [int(i) for i in ((camp.get("recipients") or {}).get("lists") or [])]
    snd = camp.get("sender") or {}
    html = camp.get("htmlContent") or ""
    esito.update({"liste_base": len(liste_base), "inviati_base": int(st.get("sent") or 0)})
    if camp.get("status") != "sent":
        return {**esito, "fermo": "campagna_base_non_inviata"}
    if not (camp.get("subject") and html and (snd.get("id") or snd.get("email")) and liste_base):
        return {**esito, "fermo": "campagna_base_non_letta"}
    if "unsubscribe" not in html.lower():
        return {**esito, "fermo": "testo_senza_disiscrizione"}
    if not st:
        return {**esito, "fermo": "statistiche_base_non_lette"}
    radice = radice_da_nome(camp.get("name"), base)
    lavoro = f"giro_lotto:r{radice}"
    gm = quando[8:10] + "-" + quando[5:7]
    nome_lista, nome_camp = f"Skovo lotto {gm}", f"Skovo lotto {gm} (r{radice})"
    esito["radice"] = radice
    if nome_camp in nomi_campagne(c):
        return {**esito, "fermo": "campagna_del_giro_gia_creata"}

    # ---- 1. esportazioni (prima di qualsiasi cancellazione) ----
    lette = {int(base): camp}

    def _campagna(cid: int) -> dict:
        if cid not in lette:
            rp = c.get(f"{API}/emailCampaigns/{cid}", params={"statistics": "globalStats"})
            lette[cid] = (rp.json() or {}) if rp.status_code < 300 else {}
        return lette[cid]

    if precedenti is None:
        precedenti = [cid for cid in range(max(1, int(base) - PRECEDENTI_DEFAULT), int(base))
                      if _campagna(cid).get("status") == "sent"]
    esito["precedenti"] = len(precedenti)
    tutti = _esporta(c, API, base, "all", pausa=pausa)
    if tutti is None:
        return {**esito, "fermo": "esportazione_destinatari_base_non_riuscita"}
    esito["destinatari_base"] = len(tutti)
    if not tutti or len(tutti) < QUOTA_DESTINATARI * int(st.get("sent") or 0):
        return {**esito, "fermo": "esportazione_destinatari_base_incompleta"}
    via: set[str] = set()
    soft: list[list[str]] = []
    for cid in [int(base)] + [int(x) for x in precedenti]:
        sc = st if cid == int(base) else _stat(_campagna(cid))
        for tipo, chiave in (("unsubscribed", "unsubscriptions"), ("hardBounces", "hardBounces"),
                             ("softBounces", "softBounces")):
            atteso = sc.get(chiave)
            # precedenti: Brevo può non avere più i contatti cancellati (export senza file = nessuno)
            ems = _esporta(c, API, cid, tipo, atteso=atteso if cid == int(base) else 0, pausa=pausa)
            if ems is None:
                return {**esito, "fermo": f"esportazione_{tipo}_{cid}_non_riuscita"}
            esito[f"{tipo}_{cid}"] = len(ems)
            # base: le statistiche sono la prova che l'esportazione è completa (per le precedenti Brevo può aver
            # perso i contatti cancellati: lì si accetta quello che c'è, già salvato nei giri di allora)
            if cid == int(base) and tipo != "softBounces" and len(ems) < int(atteso or 0):
                return {**esito, "fermo": f"esportazione_{tipo}_{cid}_incompleta"}
            if tipo == "softBounces":
                soft.append(ems)
            else:
                via |= set(ems)
    ripetuti = rimbalzi_ripetuti(soft)
    soft_base = {(e or "").lower() for e in soft[0]}            # soft[0] = la base (prima nel giro sopra)
    arrivati = [e for e in tutti if (e or "").lower() not in soft_base]
    esito["arrivati_base"] = len(arrivati)
    esito["soft_ripetuti"] = len(ripetuti)
    contatti = _contatti_brevo(c)
    nella_base: list[dict] = []
    for lid in liste_base:
        cl = _contatti_lista(c, API, lid)
        if cl is None:
            return {**esito, "fermo": "contatti_lista_base_non_letti"}
        nella_base += cl
    bloccati_lista = sum(1 for x in nella_base if invii.contatto_bloccato(x))
    esito.update({"contatti_in_brevo": len(contatti), "contatti_lista_base": len(nella_base),
                  "bloccati_lista_base": bloccati_lista, "spam_base": int(st.get("complaints") or 0)})
    if bloccati_lista < int(st.get("complaints") or 0):
        return {**esito, "fermo": "segnalazioni_spam_non_trovate"}
    with _db.connect() as conn:
        esito["nuovi_bloccati_brevo"] = invii.sincronizza_da_brevo(conn, contatti + nella_base)
        esito["nuovi_disiscritti_rimbalzi"] = invii.aggiungi_disiscritti(conn, via, f"giro_lotto_{base}")
        esito["nuovi_soft_ripetuti"] = invii.aggiungi_disiscritti(conn, ripetuti, f"giro_lotto_{base}_soft")
        bloccati = invii.disiscritti(conn) | _bloccati_0111()
        # chi ha ricevuto la base: segnato per sempre come "ricevuto" per questa catena
        gia_prima = {x["destinatario_hash"] for x in conn.execute(
            "SELECT destinatario_hash FROM invii WHERE lavoro=%s", (lavoro,)).fetchall()}
        segnati = invii.prenota(conn, lavoro, arrivati)
        invii.segna_inviati(conn, lavoro, segnati)
        gia = gia_prima | {invii.impronta(e) for e in arrivati}
        righe = _righe_db(conn)
    esito.update({"disiscritti_noti": len(bloccati), "ricevuto_catena": len(gia)})
    if len(bloccati) < BLOCCATI_MINIMI:
        return {**esito, "fermo": "disiscritti_incompleti"}

    # ---- 2. prossimi dell'archivio ----
    candidati = scegli_lotto(righe, bloccati, gia, 10 ** 6)
    esito.update({"archivio_saloni": len(righe), "da_contattare_ancora": len(candidati)})
    if not candidati:
        esito["nota"] = "archivio_finito_niente_campagna"
        return esito

    # ---- 3. posto in Brevo: via chi ha ricevuto la base (solo le liste della base) ----
    attesa = _liste_in_attesa(c, API)
    if attesa is None:
        return {**esito, "fermo": "campagne_programmate_non_lette"}
    ricevuti = {(e or "").lower() for e in arrivati}
    solo_base = set(liste_base)
    da_togliere = sorted({(x.get("email") or "").lower() for x in nella_base
                          if (x.get("email") or "").lower() in ricevuti and not invii.contatto_bloccato(x)
                          and x.get("listIds") and set(x.get("listIds")) <= solo_base
                          and not (set(x.get("listIds") or []) & attesa)})
    tolti = errori = 0
    for e in da_togliere:
        rd = c.delete(f"{API}/contacts/{quote(e, safe='')}")
        if rd.status_code in (200, 204, 404):
            tolti += 1
        else:
            errori += 1
        _dormi(pausa_cancella)
    esito.update({"da_togliere": len(da_togliere), "tolti_da_brevo": tolti, "errori_cancellazione": errori})

    # ---- 4. lista nuova entro il tetto ----
    rc = c.get(f"{API}/contacts", params={"limit": 1})
    n_brevo = (rc.json() or {}).get("count") if rc.status_code < 300 else None
    if n_brevo is None:
        return {**esito, "fermo": "conteggio_brevo_non_letto"}
    posto = TETTO_BREVO - int(n_brevo)
    scelti = candidati[:max(0, posto)]
    esito.update({"in_brevo_dopo": int(n_brevo), "posto": posto, "caricati": len(scelti),
                  "restano_dopo_questo_giro": len(candidati) - len(scelti)})
    if not scelti:
        return {**esito, "fermo": "nessun_posto_sotto_il_tetto"}
    lid = _liste(c).get(nome_lista)
    if lid is not None and int((c.get(f"{API}/contacts/lists/{lid}").json() or {}).get("uniqueSubscribers") or 0):
        return {**esito, "fermo": "lista_del_giro_gia_piena"}
    if lid is None:
        rl = c.post(f"{API}/contacts/lists", json={"name": nome_lista, "folderId": _cartella(c)})
        if rl.status_code >= 300:
            return {**esito, "fermo": "lista_non_creata"}
        lid = rl.json()["id"]
    emails = [s["email"] for s in scelti]
    with _db.connect() as conn:
        emails = invii.prenota(conn, lavoro, emails)          # da qui "ricevuto" per i giri dopo
    esito["prenotati"] = len(emails)

    def _annulla(motivo: str) -> dict:
        with _db.connect() as conn:
            invii.libera(conn, lavoro, emails)
        return {**esito, "fermo": motivo}

    for k in range(0, len(emails), 500):
        ri = c.post(f"{API}/contacts/import", json={"jsonBody": [{"email": e} for e in emails[k:k + 500]],
                                                     "listIds": [lid], "updateExistingContacts": True,
                                                     "emptyContactsAttributes": False})
        if ri.status_code >= 300:
            return _annulla("import_non_riuscito")
        _aspetta_import(c, ri)
    n = 0
    for _ in range(40):
        n = int((c.get(f"{API}/contacts/lists/{lid}").json() or {}).get("uniqueSubscribers") or 0)
        if n >= 0.9 * len(emails):
            break
        _dormi(3 * pausa)
    esito["in_lista"] = n
    if n == 0:
        return _annulla("lista_vuota_dopo_import")
    with _db.connect() as conn:
        for e in emails:
            conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (e,))
        conn.commit()

    # ---- 5. stessa email, programmata ----
    corpo = {"name": nome_camp, "subject": camp["subject"], "previewText": camp.get("previewText") or "",
             "sender": {"id": snd["id"]} if snd.get("id") else {"name": snd.get("name"), "email": snd.get("email")},
             "replyTo": camp.get("replyTo") or snd.get("email"), "htmlContent": html,
             "recipients": {"listIds": [lid]}, "inlineImageActivation": False, "scheduledAt": quando}
    for k in ("header", "footer", "tag"):
        if camp.get(k):
            corpo[k] = camp[k]
    rc = c.post(f"{API}/emailCampaigns", json=corpo)
    if rc.status_code >= 300:
        return _annulla(f"campagna_non_creata_http_{rc.status_code}")
    esito["campagna"] = int((rc.json() or {}).get("id") or 0)
    esito["lista"] = int(lid)
    return esito


def lavoro(v: str) -> dict:
    """Da web/app.py (BREVO_IMPORT_LISTE=giro_lotto:...): il lucchetto _una_volta lo mette chi chiama."""
    base, quando, prec = leggi_valore(v)
    return giro_lotto(base, quando, precedenti=prec)
