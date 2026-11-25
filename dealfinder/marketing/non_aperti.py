"""25/11 (cliente): rinvio a chi NON ha aperto le campagne email. Prepara solo la lista in Brevo: non crea campagne e
non invia niente (la campagna si fa e si manda da Brevo).

Voce una tantum di BREVO_IMPORT_LISTE (una volta per etichetta, nel sito, appena dopo il deploy):
  non_aperti:<campagna1>,<campagna2>:<nome_lista>        es.  non_aperti:101,102:Skovo non aperti 26-11

Che cosa fa:
  1. per ogni campagna chiede a Brevo l'esportazione dei destinatari "nonOpeners" e "openers" (POST
     /emailCampaigns/{id}/exportRecipients, poi GET /processes/{processId} fino a "completed", al massimo ATTESA_S),
     scarica il CSV da export_url e ne legge la colonna delle email;
  2. unisce i non aperti e toglie chi non va scritto:
       - chi ha aperto una delle campagne (in due campagne si può essere "non aperto" di una e "aperto" dell'altra; e
         il 30/10 l'esportazione nonOpeners di Brevo aveva restituito tutti i destinatari, vedi rinvio.escludi_aperti);
       - gli indirizzi nostri, RINVIO_ESCLUSI (indirizzi o domini, se la variabile c'è) e i commercianti esclusi
         (brevo_liste._esclusi: Loconte, Officine Mulino, Cosval, Auto e Auto Varese, SR Auto, GT Automobili...);
       - disiscritti e bloccati (emailBlacklisted, unsubscribed e la tabella disiscritti del database);
       - rimbalzi definitivi (hardBounce del contatto e /smtp/blockedContacts);
       - chi non è più un contatto in Brevo (la lista si riempie solo con contatti che esistono già: così restano i
         loro attributi MI_LINK / STOP_LINK);
  3. crea la lista <nome_lista> nella cartella Skovo (se c'è già, usa quella: un secondo tentativo non la raddoppia);
  4. aggiunge i contatti con POST /contacts/lists/{id}/contacts/add a blocchi di 150;
  5. scrive NON_APERTI {json}: solo conteggi (per campagna, totale, tolti per motivo, aggiunti, id della lista).

Se un'esportazione o una lettura non riesce il lavoro si ferma PRIMA di creare la lista (niente liste a metà) e
l'errore dice quale campagna e quale passo. Nei log mai un indirizzo email, né l'indirizzo del file esportato.
"""
from __future__ import annotations

import csv
import gzip
import io
import logging
import os
import re
import time
import zipfile

import httpx

from .brevo_liste import API, _cartella, _chiave

lg = logging.getLogger("dealfinder.marketing")

BLOCCO = 150                     # limite di Brevo per /contacts/lists/{id}/contacts/add
ATTESA_S = 600                   # al massimo 10 minuti per esportazione
PASSO_S = 10                     # ogni quanto si chiede a Brevo se l'esportazione è pronta
TENTATIVI_EXPORT = 2             # un'esportazione non riuscita si richiede una seconda volta
NOSTRI = ("skovo.app", "skovo.net", "scovo.it")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_COLONNA = re.compile(r"^(e[-_ ]?mail([-_ ]?(id|address))?|indirizzo[-_ ]?e?[-_ ]?mail|email[-_ ]?address)$", re.I)
_FALLITI = ("failed", "cancelled", "canceled", "aborted", "error")


class Fermo(RuntimeError):
    """Il lavoro non può andare avanti (il messaggio non contiene mai indirizzi email)."""


# ---------- la voce ----------
def analizza(v: str) -> tuple[list[int], str]:
    """'non_aperti:101,102:Skovo non aperti 26-11' -> ([101, 102], 'Skovo non aperti 26-11'). Solleva ValueError se
    mancano le campagne o il nome della lista."""
    parti = str(v or "").split(":", 2)
    if len(parti) < 3 or parti[0] != "non_aperti":
        raise ValueError("voce da scrivere come non_aperti:<campagna1>,<campagna2>:<nome_lista>")
    campagne: list[int] = []
    for x in parti[1].split(","):
        x = x.strip()
        if not x:
            continue
        if not (x.isascii() and x.isdigit()) or int(x) <= 0:
            raise ValueError("gli id delle campagne devono essere numeri")
        if int(x) not in campagne:
            campagne.append(int(x))
    nome = " ".join(parti[2].split())
    if not campagne or not nome:
        raise ValueError("mancano le campagne o il nome della lista")
    return campagne, nome[:120]


# ---------- il CSV dell'esportazione ----------
def _testo(dati: bytes) -> str:
    """Il file di Brevo può arrivare zip, gzip o CSV semplice."""
    try:
        with zipfile.ZipFile(io.BytesIO(dati)) as z:
            dati = b"\n".join(z.read(n) for n in z.namelist())
    except zipfile.BadZipFile:
        try:
            dati = gzip.decompress(dati)
        except OSError:
            pass
    return dati.decode("utf-8-sig", "ignore")


def emails_da_csv(dati: bytes) -> list[str]:
    """Gli indirizzi della colonna delle email del CSV (minuscoli, senza doppioni, nell'ordine del file). La colonna
    si riconosce dal titolo (Email, EMAIL, E-mail, Email_ID...) e, se il titolo non c'è o è un altro, è quella con più
    indirizzi. Il separatore può essere virgola, punto e virgola o tabulazione. Le email scritte in ALTRE colonne
    (es. un attributo con l'email di un collega) non contano. File senza righe: elenco vuoto. Solleva ValueError se
    il file ha righe ma nessuna colonna di email (non è un'esportazione di destinatari)."""
    testo = _testo(dati or b"").replace("\x00", "")
    righe_testo = [r for r in testo.splitlines() if r.strip()]
    if not righe_testo:
        return []
    sep = max((";", ",", "\t"), key=lambda s: righe_testo[0].count(s))
    righe = list(csv.reader(io.StringIO("\n".join(righe_testo)), delimiter=sep))
    testa = [x.strip().strip('"') for x in righe[0]]
    col = next((i for i, x in enumerate(testa) if _COLONNA.match(x)), None)
    corpo = righe[1:] if col is not None else righe              # senza titolo riconosciuto ogni riga può essere un dato
    if col is None:
        larghezza = max(len(r) for r in righe)
        conti = [sum(1 for r in corpo if i < len(r) and _EMAIL.match(r[i].strip())) for i in range(larghezza)]
        if max(conti) == 0:
            if len(righe) == 1 and any("mail" in x.lower() for x in testa):
                return []                                        # solo la riga dei titoli: nessun destinatario
            raise ValueError("nel file esportato non c'è una colonna di email")
        col = conti.index(max(conti))
    visti, out = set(), []
    for r in corpo:
        e = r[col].strip().lower() if col < len(r) else ""
        if e and _EMAIL.match(e) and e not in visti:
            visti.add(e)
            out.append(e)
    return out


# ---------- i filtri (funzione pura) ----------
def filtra(non_aperti, aperti=(), contatti: dict | None = None, rimbalzati=(), disiscritti=(), esclusi_env=()) -> tuple[list[str], dict]:
    """Chi resta nella lista e quanti ne escono per ogni motivo (un indirizzo conta nel primo motivo che lo ferma).
    contatti: {email: contatto Brevo} di TUTTI i contatti (serve per sapere chi esiste ancora e chi è bloccato);
    rimbalzati: indirizzi di /smtp/blockedContacts; disiscritti: impronte della tabella disiscritti;
    esclusi_env: indirizzi o domini di RINVIO_ESCLUSI."""
    from . import invii
    from .brevo_liste import _esclusi
    contatti = contatti or {}
    aperti = {invii.norm_email(e) for e in aperti}
    rimbalzati = {invii.norm_email(e) for e in rimbalzati}
    disiscritti = set(disiscritti)
    esclusi_env = {str(x).strip().lower().lstrip("@") for x in esclusi_env if str(x).strip()}
    tolti = {"aperto_una_campagna": 0, "nostri": 0, "rinvio_esclusi": 0, "commercianti_esclusi": 0, "disiscritti": 0,
             "rimbalzi_definitivi": 0, "non_in_brevo": 0}
    buoni, visti = [], set()
    for grezzo in non_aperti:
        e = invii.norm_email(grezzo)
        if not e or e in visti:
            continue
        visti.add(e)
        dominio = e.rpartition("@")[2]
        x = contatti.get(e)
        if e in aperti:
            tolti["aperto_una_campagna"] += 1
        elif dominio in NOSTRI or e.startswith("ahmedisma"):
            tolti["nostri"] += 1
        elif e in esclusi_env or dominio in esclusi_env:
            tolti["rinvio_esclusi"] += 1
        elif _esclusi(e):
            tolti["commercianti_esclusi"] += 1
        elif (x and (x.get("emailBlacklisted") or x.get("unsubscribed"))) or invii.impronta(e) in disiscritti:
            tolti["disiscritti"] += 1
        elif (x and x.get("hardBounce")) or e in rimbalzati:
            tolti["rimbalzi_definitivi"] += 1
        elif x is None:
            tolti["non_in_brevo"] += 1
        else:
            buoni.append(e)
    return buoni, tolti


# ---------- Brevo ----------
def _client():
    key = _chiave()
    if not key:
        raise Fermo("manca BREVO_API_KEY")
    return httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=90)


def _scarica(url: str) -> bytes:
    """Il file esportato (indirizzo firmato, fuori dall'API: senza la chiave di Brevo)."""
    r = httpx.get(url, timeout=120, follow_redirects=True)
    if r.status_code != 200:
        raise Fermo(f"HTTP {r.status_code} allo scarico")
    return r.content


def esporta(c, campagna: int, tipo: str, dormi=time.sleep, scarica=None, attesa_s: int = ATTESA_S) -> list[str]:
    """Le email dei destinatari `tipo` (nonOpeners, openers) della campagna. Solleva Fermo se Brevo non accetta la
    richiesta, se l'esportazione fallisce o non è pronta entro `attesa_s` secondi, se il file non si legge."""
    from .email_esempi import chiedi
    scarica = scarica or _scarica
    cosa = f"esportazione {tipo} della campagna {campagna}"
    try:
        r = chiedi(c, "post", f"{API}/emailCampaigns/{campagna}/exportRecipients", dormi, json={"recipientsType": tipo})
    except Exception as e:
        raise Fermo(f"{cosa}: richiesta non riuscita ({type(e).__name__})") from None
    try:
        pid = (r.json() or {}).get("processId") if r.status_code < 300 else None
    except ValueError:
        pid = None
    if not pid:
        raise Fermo(f"{cosa}: Brevo non l'ha accettata (HTTP {r.status_code})")
    url, stato = None, "in_attesa"
    for _ in range(max(1, int(attesa_s // PASSO_S))):
        dormi(PASSO_S)
        try:
            p = c.get(f"{API}/processes/{pid}").json() or {}
        except Exception:
            stato = "non_letto"
            continue
        stato = str(p.get("status") or "sconosciuto")
        if stato == "completed":
            url = p.get("export_url")
            break
        if stato.lower() in _FALLITI:
            raise Fermo(f"{cosa}: processo {pid} finito in '{stato}'")
    if stato != "completed":
        raise Fermo(f"{cosa}: non pronta dopo {int(attesa_s // 60)} minuti (processo {pid}, stato '{stato}')")
    if not url:
        raise Fermo(f"{cosa}: finita senza il file (processo {pid})")
    try:
        return emails_da_csv(scarica(url))
    except Fermo as e:
        raise Fermo(f"{cosa}: file non scaricato ({e})") from None
    except ValueError as e:
        raise Fermo(f"{cosa}: file non leggibile ({e})") from None
    except Exception as e:                                       # mai l'indirizzo firmato del file nel log
        raise Fermo(f"{cosa}: file non scaricato ({type(e).__name__})") from None


def _esporta_con_riprova(c, campagna: int, tipo: str, dormi, scarica, attesa_s: int) -> list[str]:
    for k in range(TENTATIVI_EXPORT):
        try:
            return esporta(c, campagna, tipo, dormi, scarica, attesa_s)
        except Fermo as e:
            if k + 1 == TENTATIVI_EXPORT:
                raise
            lg.warning("NON_APERTI riprovo: %s", e)
            dormi(15)
    return []


def contatti_brevo(c, dormi=time.sleep) -> dict:
    """{email: {emailBlacklisted, unsubscribed, hardBounce}} di tutti i contatti (solo quello che serve ai filtri:
    con decine di migliaia di contatti il sito non tiene in memoria le schede intere)."""
    from .email_esempi import chiedi
    out, off = {}, 0
    while True:
        r = chiedi(c, "get", f"{API}/contacts", dormi, params={"limit": 1000, "offset": off})
        if r.status_code >= 300:
            raise Fermo(f"contatti di Brevo non letti (HTTP {r.status_code})")
        cs = (r.json() or {}).get("contacts") or []
        for x in cs:
            e = str(x.get("email") or "").strip().lower()
            if e:
                out[e] = {k: True for k in ("emailBlacklisted", "unsubscribed", "hardBounce") if x.get(k)}
        if len(cs) < 1000:
            return out
        off += 1000


def bloccati_smtp(c, dormi=time.sleep) -> set[str]:
    """Indirizzi di /smtp/blockedContacts (rimbalzi definitivi, disiscritti e bloccati degli invii singoli)."""
    from .email_esempi import chiedi
    out, off = set(), 0
    while True:
        r = chiedi(c, "get", f"{API}/smtp/blockedContacts", dormi, params={"limit": 100, "offset": off})
        if r.status_code >= 300:
            raise Fermo(f"indirizzi bloccati di Brevo non letti (HTTP {r.status_code})")
        cs = (r.json() or {}).get("contacts") or []
        out |= {str(x["email"]).strip().lower() for x in cs if x.get("email")}
        if len(cs) < 100:
            return out
        off += 100


def _lista(c, nome: str, dormi=time.sleep) -> tuple[int, bool]:
    """(id, creata adesso) della lista `nome`: quella che c'è già con quel nome, altrimenti nuova nella cartella Skovo."""
    from .email_esempi import _tutte_le_liste, chiedi
    lid = _tutte_le_liste(c, dormi).get(nome)
    if lid is not None:
        return int(lid), False
    r = chiedi(c, "post", f"{API}/contacts/lists", dormi, json={"name": nome, "folderId": _cartella(c)})
    if r.status_code >= 300:
        raise Fermo(f"lista non creata (HTTP {r.status_code})")
    return int(r.json()["id"]), True


def aggiungi(c, lid: int, emails: list[str], dormi=time.sleep) -> dict:
    """Aggiunge alla lista contatti che esistono già, a blocchi di BLOCCO. Conta gli aggiunti (dalla risposta di
    Brevo), quelli che Brevo rifiuta (già nella lista o non più contatti) e i blocchi non riusciti."""
    from .email_esempi import chiedi
    esito = {"aggiunti": 0, "rifiutati": 0, "blocchi": 0, "blocchi_falliti": 0}
    for k in range(0, len(emails), BLOCCO):
        blocco = emails[k:k + BLOCCO]
        esito["blocchi"] += 1
        try:
            r = chiedi(c, "post", f"{API}/contacts/lists/{lid}/contacts/add", dormi, json={"emails": blocco})
        except Exception:
            esito["blocchi_falliti"] += 1
            continue
        if r.status_code < 300:
            try:
                d = ((r.json() or {}).get("contacts") or {})
            except ValueError:
                d = {}
            if isinstance(d.get("success"), list) or isinstance(d.get("failure"), list):
                esito["aggiunti"] += len(d.get("success") or [])
                esito["rifiutati"] += len(d.get("failure") or [])
            else:
                esito["aggiunti"] += len(blocco)
        elif r.status_code == 400:                               # tutti già nella lista (o nessuno più contatto)
            esito["rifiutati"] += len(blocco)
        else:
            esito["blocchi_falliti"] += 1
    return esito


# ---------- il lavoro ----------
def raccogli(campagne: list[int], *, c, connetti=None, dormi=time.sleep, scarica=None, env=None,
             attesa_s: int = ATTESA_S) -> tuple[list[str], dict]:
    """Passi 1 e 2, senza toccare le liste: (indirizzi da aggiungere, conteggi). Solleva Fermo se un'esportazione o
    una lettura non riesce."""
    from . import invii
    env = os.environ if env is None else env
    if connetti is None:
        from ..db import connect as connetti
    esito: dict = {"campagne": {}}
    non_aperti: list[str] = []
    aperti: set[str] = set()
    for cid in campagne:
        na = _esporta_con_riprova(c, cid, "nonOpeners", dormi, scarica, attesa_s)
        ap = _esporta_con_riprova(c, cid, "openers", dormi, scarica, attesa_s)
        esito["campagne"][str(cid)] = {"non_aperti": len(na), "aperti": len(ap)}
        non_aperti += na
        aperti |= set(ap)
    contatti = contatti_brevo(c, dormi)
    rimbalzati = bloccati_smtp(c, dormi)
    try:
        with connetti() as conn:
            disiscritti = invii.disiscritti(conn)
    except Exception as e:
        raise Fermo(f"disiscritti del database non letti ({type(e).__name__})") from None
    esclusi_env = [x for x in (env.get("RINVIO_ESCLUSI") or "").split(",") if x.strip()]
    buoni, tolti = filtra(non_aperti, aperti, contatti, rimbalzati, disiscritti, esclusi_env)
    esito.update(totale=len({invii.norm_email(e) for e in non_aperti} - {""}), tolti=tolti, da_aggiungere=len(buoni))
    return buoni, esito


def prepara(campagne: list[int], nome: str, *, c, connetti=None, dormi=time.sleep, scarica=None, env=None,
            attesa_s: int = ATTESA_S) -> dict:
    """Tutto il giro; restituisce i conteggi. La lista si crea solo dopo che esportazioni e filtri sono riusciti."""
    buoni, esito = raccogli(campagne, c=c, connetti=connetti, dormi=dormi, scarica=scarica, env=env, attesa_s=attesa_s)
    esito["lista_nome"] = nome
    if not buoni:
        esito.update(lista=None, aggiunti=0, nota="nessun contatto da aggiungere: lista non creata")
        return esito
    try:
        lid, nuova = _lista(c, nome, dormi)
    except Fermo:
        raise
    except Exception as e:
        raise Fermo(f"lista non creata ({type(e).__name__})") from None
    esito.update(lista=lid, lista_nuova=nuova, **aggiungi(c, lid, buoni, dormi))
    return esito


def incompleto(esito: dict) -> str | None:
    """Perché il lavoro non è da segnare fatto (None se è finito bene)."""
    if esito.get("blocchi_falliti"):
        return f"{esito['blocchi_falliti']} blocchi non aggiunti alla lista {esito.get('lista')}"
    return None


def lavoro(v: str, client=None, connetti=None, dormi=time.sleep, scarica=None) -> dict:
    """La voce non_aperti:<campagne>:<nome_lista>. Scrive NON_APERTI {json} (solo conteggi). Solleva se non riesce:
    chi la chiama (coda_lavori._lungo) la segna fatta solo se arriva in fondo."""
    import json
    try:
        campagne, nome = analizza(v)
    except ValueError as e:
        lg.warning("NON_APERTI voce non valida: %s", e)
        raise
    c = client or _client()
    try:
        esito = prepara(campagne, nome, c=c, connetti=connetti, dormi=dormi, scarica=scarica)
    except Exception as e:                                       # Fermo: il lavoro si è fermato prima di riempire la lista
        lg.warning("NON_APERTI ERRORE: %s. Nessun contatto aggiunto a nessuna lista.",
                   e if isinstance(e, Fermo) else type(e).__name__)
        raise
    finally:
        if client is None:
            try:
                c.close()
            except Exception:
                pass
    lg.warning("NON_APERTI %s", json.dumps(esito, ensure_ascii=False))
    perche = incompleto(esito)
    if perche:
        raise RuntimeError("lista incompleta: " + perche)
    return esito
