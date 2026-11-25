"""25/11 (cliente): i CELLULARI trovati da raccolta_contatti (righe RACC_CELL di lead_consegne) portati in Brevo, in
una lista nuova, perché il database di Render da fuori non si legge. Solo i cellulari: le email (RACC_EMAIL) qui non
si toccano. Non manda SMS né WhatsApp: prepara solo i contatti.

Voce una tantum di BREVO_IMPORT_LISTE (una volta per etichetta, nel sito, appena dopo il deploy):
  esporta_raccolta:<etichetta>        es.  esporta_raccolta:2611  ->  lista "Skovo raccolta cellulari 2611"

Che cosa entra: il cellulare principale di ogni riga RACC_CELL, se
  - è un cellulare italiano (+39 3xx, 10 cifre: sms_saloni.norm_tel);
  - il comune del salone è entro 50 km da Milano (raccolta60.nel_raggio, lo stesso filtro della raccolta);
  - il salone non è tra gli esclusi (raccolta_contatti.escluso su nome e sito: Loconte, Officine Mulino, Cosval,
    Auto e Auto Varese, SR Auto, GT Automobili, Privacar, Vroom...);
  - il numero non è tra quelli che hanno detto basta (tabella sms_stop);
  - non è un doppione.
In Brevo ogni numero è un contatto senza email, riconosciuto dall'attributo SMS (+39...); se l'account ha l'attributo
WHATSAPP si riempie anche quello. In più NOME, COMUNE, PROV e KM (creati se mancano). POST /contacts con
updateEnabled: rifarlo non crea doppioni.

Log: ESPORTA_RACCOLTA {json}, solo conteggi. Mai un numero intero nei log (nemmeno negli errori).
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from .brevo_liste import API, _cartella, _chiave

lg = logging.getLogger("dealfinder.marketing")

TIPO = "RACC_CELL"
LISTA = "Skovo raccolta cellulari {}"
ATTRIBUTI = {"NOME": "text", "COMUNE": "text", "PROV": "text", "KM": "float"}
PAUSA_S = 0.15                   # tra un contatto e l'altro (Brevo: 10 chiamate al secondo su /contacts)
BLOCCO = 150                     # limite di Brevo per /contacts/lists/{id}/contacts/add
RE_ETICHETTA = re.compile(r"[A-Za-z0-9_-]{1,20}")


def etichetta(v: str) -> str:
    """'esporta_raccolta:2611' -> '2611'. Solleva ValueError se l'etichetta manca o ha caratteri strani."""
    parti = str(v or "").split(":")
    if len(parti) != 2 or parti[0] != "esporta_raccolta" or not RE_ETICHETTA.fullmatch(parti[1].strip()):
        raise ValueError("voce da scrivere come esporta_raccolta:<etichetta> (lettere, cifre, - e _)")
    return parti[1].strip()


# ---------- la scelta (funzione pura) ----------
def _pulito(t, n: int) -> str:
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(t or "")).split())[:n]


def scegli(righe, stop=(), zona: dict | None = None) -> tuple[list[dict], dict]:
    """Dalle righe RACC_CELL (dict `dati`, anche come testo JSON) i contatti da caricare:
    [{"tel": "+393...", "nome", "comune", "prov", "km"}] e i conteggi di chi resta fuori, un motivo per riga.
    stop: numeri di sms_stop (in qualsiasi forma). zona: i comuni entro il raggio (per i test)."""
    from ..pipeline.raccolta60 import nel_raggio
    from ..pipeline.raccolta_contatti import escluso
    from .sms_saloni import norm_tel
    stop = {norm_tel(t) for t in stop} - {None}
    conti = {"righe": 0, "non_cellulare": 0, "fuori_zona": 0, "esclusi": 0, "stop": 0, "doppioni": 0,
             "altri_cellulari_lasciati": 0}
    out, visti = [], set()
    for d in righe:
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except ValueError:
                d = None
        conti["righe"] += 1
        if not isinstance(d, dict):
            conti["non_cellulare"] += 1
            continue
        tel = norm_tel(d.get("cellulare"))
        if not tel:
            conti["non_cellulare"] += 1
            continue
        z = nel_raggio(d.get("comune"), d.get("prov"), zona)
        if not z:
            conti["fuori_zona"] += 1
            continue
        if escluso(d.get("nome"), d.get("sito")):
            conti["esclusi"] += 1
            continue
        if tel in stop:
            conti["stop"] += 1
            continue
        if tel in visti:
            conti["doppioni"] += 1
            continue
        visti.add(tel)
        conti["altri_cellulari_lasciati"] += len(str(d.get("altri_cellulari") or "").split())
        comune, prov, km = z
        out.append({"tel": tel, "nome": _pulito(d.get("nome"), 120), "comune": _pulito(comune or d.get("comune"), 80),
                    "prov": _pulito(prov or d.get("prov"), 2).upper(), "km": None if km is None else round(float(km), 1)})
    return out, conti


# ---------- Brevo ----------
def _client():
    key = _chiave()
    if not key:
        raise RuntimeError("manca BREVO_API_KEY")
    return httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60)


def attributi(c, dormi=time.sleep) -> tuple[set[str], bool]:
    """(attributi di ATTRIBUTI che ci sono in Brevo, creando quelli che mancano; True se l'account ha WHATSAPP)."""
    from .email_esempi import chiedi

    def esistenti() -> set[str]:
        r = chiedi(c, "get", f"{API}/contacts/attributes", dormi)
        if r.status_code >= 300:
            raise RuntimeError(f"attributi di Brevo non letti (HTTP {r.status_code})")
        return {str(a.get("name") or "").upper() for a in (r.json() or {}).get("attributes") or []}
    ci_sono = esistenti()
    mancano = [a for a in ATTRIBUTI if a not in ci_sono]
    for a in mancano:
        try:
            chiedi(c, "post", f"{API}/contacts/attributes/normal/{a}", dormi, json={"type": ATTRIBUTI[a]})
        except Exception:
            pass
    if mancano:
        ci_sono = esistenti()
    return {a for a in ATTRIBUTI if a in ci_sono}, "WHATSAPP" in ci_sono


def _lista(c, nome: str, dormi=time.sleep) -> tuple[int, bool]:
    """(id, creata adesso): la lista con quel nome se c'è già (un secondo tentativo non la raddoppia), altrimenti nuova."""
    from .email_esempi import _tutte_le_liste, chiedi
    lid = _tutte_le_liste(c, dormi).get(nome)
    if lid is not None:
        return int(lid), False
    r = chiedi(c, "post", f"{API}/contacts/lists", dormi, json={"name": nome, "folderId": _cartella(c)})
    if r.status_code >= 300:
        raise RuntimeError(f"lista non creata (HTTP {r.status_code})")
    return int(r.json()["id"]), True


def corpo_contatto(d: dict, lid: int, ci_sono: set, whatsapp: bool) -> dict:
    attr = {"SMS": d["tel"]}
    if whatsapp:
        attr["WHATSAPP"] = d["tel"]
    for nome, chiave in (("NOME", "nome"), ("COMUNE", "comune"), ("PROV", "prov"), ("KM", "km")):
        if nome in ci_sono and d.get(chiave) not in (None, ""):
            attr[nome] = d[chiave]
    return {"attributes": attr, "listIds": [lid], "updateEnabled": True}


def _id_del_numero(c, tel: str, dormi) -> int | None:
    """Il contatto che in Brevo ha già questo numero (per metterlo nella lista). None se non si trova."""
    from urllib.parse import quote
    from .email_esempi import chiedi
    try:
        r = chiedi(c, "get", f"{API}/contacts/{quote(tel, safe='')}", dormi, tentativi=2, params={"identifierType": "phone_id"})
        return int(r.json()["id"]) if r.status_code == 200 else None
    except Exception:
        return None


def carica(c, lid: int, contatti: list[dict], ci_sono: set, whatsapp: bool, dormi=time.sleep) -> dict:
    """Un contatto per numero (POST /contacts). 201 creato, 204 aggiornato. Se Brevo dice che il numero è già di un
    altro contatto (400), quel contatto si cerca per numero e si aggiunge alla lista. Solo conteggi."""
    from .email_esempi import chiedi
    n = {"creati": 0, "aggiornati": 0, "gia_in_brevo_aggiunti": 0, "rifiutati": 0, "errori": 0}
    gia: list[int] = []
    for d in contatti:
        corpo = corpo_contatto(d, lid, ci_sono, whatsapp)
        try:
            r = chiedi(c, "post", f"{API}/contacts", dormi, json=corpo)
            if r.status_code == 400 and whatsapp:                # WHATSAPP rifiutato o già di un altro: solo con SMS
                corpo["attributes"].pop("WHATSAPP", None)
                r = chiedi(c, "post", f"{API}/contacts", dormi, json=corpo)
        except Exception:
            n["errori"] += 1
            continue
        if r.status_code == 201:
            n["creati"] += 1
        elif r.status_code < 300:
            n["aggiornati"] += 1
        elif r.status_code == 400:
            cid = _id_del_numero(c, d["tel"], dormi)
            if cid is None:
                n["rifiutati"] += 1
            else:
                gia.append(cid)
        else:
            n["errori"] += 1
        dormi(PAUSA_S)
    for k in range(0, len(gia), BLOCCO):
        blocco = gia[k:k + BLOCCO]
        try:
            r = chiedi(c, "post", f"{API}/contacts/lists/{lid}/contacts/add", dormi, json={"ids": blocco})
            ok = r.status_code < 300 or r.status_code == 400     # 400: già tutti nella lista
        except Exception:
            ok = False
        n["gia_in_brevo_aggiunti" if ok else "errori"] += len(blocco)
    return n


# ---------- il lavoro ----------
def _righe_e_stop(connetti) -> tuple[list, set]:
    from . import sms_saloni as SS
    with connetti() as conn:
        righe = [r["dati"] for r in conn.execute("SELECT dati FROM lead_consegne WHERE tipo = %s ORDER BY id", (TIPO,)).fetchall()]
        SS.assicura(conn)
        stop = {r["telefono"] for r in conn.execute("SELECT telefono FROM sms_stop").fetchall()}
    return righe, stop


def prepara(et: str, *, c, connetti=None, dormi=time.sleep, zona: dict | None = None) -> dict:
    if connetti is None:
        from ..db import connect as connetti
    righe, stop = _righe_e_stop(connetti)
    contatti, conti = scegli(righe, stop, zona)
    nome = LISTA.format(et)
    esito = {"etichetta": et, "lista_nome": nome, **conti, "da_caricare": len(contatti)}
    if not contatti:
        esito.update(lista=None, nota="nessun cellulare da caricare: lista non creata")
        return esito
    ci_sono, whatsapp = attributi(c, dormi)
    lid, nuova = _lista(c, nome, dormi)
    esito.update(lista=lid, lista_nuova=nuova, attributi=["SMS"] + (["WHATSAPP"] if whatsapp else []) + sorted(ci_sono),
                 **carica(c, lid, contatti, ci_sono, whatsapp, dormi))
    return esito


def lavoro(v: str, client=None, connetti=None, dormi=time.sleep, zona: dict | None = None) -> dict:
    """La voce esporta_raccolta:<etichetta>. Scrive ESPORTA_RACCOLTA {json} (solo conteggi). Solleva se non riesce o se
    qualche contatto non è stato caricato per un errore di Brevo (5xx, rete): chi la chiama (coda_lavori._lungo) la
    segna fatta solo se arriva in fondo; rifarla non crea doppioni."""
    try:
        et = etichetta(v)
    except ValueError as e:
        lg.warning("ESPORTA_RACCOLTA voce non valida: %s", e)
        raise
    c = client or _client()
    try:
        esito = prepara(et, c=c, connetti=connetti, dormi=dormi, zona=zona)
    except Exception as e:
        # mai il testo dell'errore di una chiamata: dentro l'indirizzo può esserci un numero
        lg.warning("ESPORTA_RACCOLTA ERRORE: %s", e if type(e) is RuntimeError else type(e).__name__)
        raise
    finally:
        if client is None:
            try:
                c.close()
            except Exception:
                pass
    lg.warning("ESPORTA_RACCOLTA %s", json.dumps(esito, ensure_ascii=False))
    if esito.get("errori"):
        raise RuntimeError(f"{esito['errori']} cellulari non caricati per errori di Brevo")
    return esito
