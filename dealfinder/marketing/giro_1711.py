"""17/11 sera (cliente: "caricali tu tramite API"): giro email del 17/11, due liste e due campagne.

Le email (690 con esempi, con attributi SK_*; 689 senza) sono in marketing/giro_1711.bin, cifrate: la chiave è solo nel
valore di BREVO_IMPORT_LISTE su Render ("giro1711:<chiave>"), mai nel repo né nei log. Prima di caricare si tolgono
di nuovo disiscritti/bloccati (database + Brevo + esportazione del 1/11), esclusi del cliente e si resta sotto il tetto.
Campagna "senza esempi": testo, oggetto e mittente della 84. Campagna "con esempi": HTML con {{ contact.SK_* }}.
Nei log solo conteggi."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .brevo_liste import API, BLOCCATI_MINIMI, TETTO_BREVO, _bloccati_0111, _cartella, _chiave, _esclusi, _liste

lg = logging.getLogger("dealfinder.marketing")
_FILE = Path(__file__).resolve().with_suffix(".bin")
ATTRIBUTI = ("SK_FRASE", "SK_AUTO1", "SK_DATI1", "SK_AUTO2", "SK_DATI2", "SK_ZONA")
LISTA_CON = "Skovo 17-11 con esempi"
LISTA_SENZA = "Skovo 17-11 senza esempi"
MODELLO_SENZA = 84
MITTENTE_CON = 7                       # info@mail.skovo.app (verificato il 16/11)
OGGETTO_CON = "Due auto da privati sotto mercato vicino a voi"
ANTEPRIMA_CON = "Buon margine, niente truffe, appuntamento fissato"
RISPOSTE = "info@skovo.app"


def _flusso(chiave: str, n: int) -> bytes:
    out, i = bytearray(), 0
    while len(out) < n:
        out += hashlib.sha256(f"{chiave}:{i}".encode()).digest()
        i += 1
    return bytes(out[:n])


def cifra(dati: dict, chiave: str) -> bytes:
    raw = json.dumps(dati, ensure_ascii=False).encode()
    return base64.b64encode(bytes(a ^ b for a, b in zip(raw, _flusso(chiave, len(raw)))))


def decifra(blob: bytes, chiave: str) -> dict:
    raw = base64.b64decode(blob)
    return json.loads(bytes(a ^ b for a, b in zip(raw, _flusso(chiave, len(raw)))).decode())


def filtra(righe: list[dict], bloccati: set, in_brevo: set) -> list[dict]:
    from .invii import impronta, norm_email
    out, visti = [], set()
    for r in righe:
        e = norm_email(r.get("EMAIL"))
        if not e or e in visti or e in in_brevo or impronta(e) in bloccati or _esclusi(e):
            continue
        visti.add(e)
        out.append({**r, "EMAIL": e})
    return out


def _importa(c: httpx.Client, nome: str, righe: list[dict], esito: dict, chiave_esito: str) -> int | None:
    from .su_misura import _aspetta_import
    lid = _liste(c).get(nome)
    if lid is None:
        r = c.post(f"{API}/contacts/lists", json={"name": nome, "folderId": _cartella(c)})
        r.raise_for_status()
        lid = r.json()["id"]
    for k in range(0, len(righe), 500):
        corpo = [{"email": x["EMAIL"], "attributes": {a: x[a] for a in ATTRIBUTI if x.get(a)}} for x in righe[k:k + 500]]
        r = c.post(f"{API}/contacts/import", json={"jsonBody": corpo, "listIds": [lid],
                                                   "updateExistingContacts": True, "emptyContactsAttributes": False})
        esito[f"{chiave_esito}_import_{k}"] = r.status_code
        if r.status_code >= 300:
            return None
        _aspetta_import(c, r)
    esito[f"{chiave_esito}_in_lista"] = (c.get(f"{API}/contacts/lists/{lid}").json() or {}).get("uniqueSubscribers")
    return lid


def lavoro(chiave: str) -> dict:
    from ..db import connect
    from . import invii
    from .auto_invio import _contatti_brevo
    esito: dict = {}
    try:
        dati = decifra(_FILE.read_bytes(), chiave)
    except Exception:
        return {"fermo": "dati_non_leggibili"}
    key = _chiave()
    if not key:
        return {"fermo": "chiave_brevo"}
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        base = c.get(f"{API}/emailCampaigns/{MODELLO_SENZA}")
        base = base.json() if base.status_code < 300 else {}
        snd = base.get("sender") or {}
        if not base.get("htmlContent") or "unsubscribe" not in base["htmlContent"] or not snd.get("id"):
            return {"fermo": "modello_84_non_letto"}
        html_con = dati.get("html_con") or ""
        if "unsubscribe" not in html_con or "Marco" in html_con:
            return {"fermo": "html_con_non_valido"}
        contatti = _contatti_brevo(c)
        with connect() as conn:
            invii.sincronizza_da_brevo(conn, contatti)
            bloccati = invii.disiscritti(conn) | _bloccati_0111()
        in_brevo = {invii.norm_email(x.get("email")) for x in contatti if x.get("email")}
        posto = TETTO_BREVO - len(in_brevo)
        esito.update({"in_brevo": len(in_brevo), "posto": posto, "disiscritti": len(bloccati)})
        if len(bloccati) < BLOCCATI_MINIMI:
            return {**esito, "fermo": "disiscritti_incompleti"}
        con = filtra(dati.get("con") or [], bloccati, in_brevo)
        senza = filtra(dati.get("senza") or [], bloccati, in_brevo | {x["EMAIL"] for x in con})
        esito.update({"con_validi": len(con), "senza_validi": len(senza)})
        if posto <= 0:
            return {**esito, "fermo": "brevo_pieno"}
        if len(con) + len(senza) > posto:                 # si taglia metà e metà, i più lontani (in fondo) escono
            n_con = min(len(con), posto // 2 + posto % 2)
            n_senza = min(len(senza), posto - n_con)
            n_con = min(len(con), posto - n_senza)
            con, senza = con[:n_con], senza[:n_senza]
        esito.update({"con": len(con), "senza": len(senza)})
        esistenti = {a.get("name") for a in (c.get(f"{API}/contacts/attributes").json() or {}).get("attributes", [])}
        for a in ATTRIBUTI:
            if a not in esistenti:
                c.post(f"{API}/contacts/attributes/normal/{a}", json={"type": "text"})
        lid_con = _importa(c, LISTA_CON, con, esito, "con") if con else None
        lid_senza = _importa(c, LISTA_SENZA, senza, esito, "senza") if senza else None
        with connect() as conn:
            for x in con + senza:
                conn.execute("INSERT INTO brevo_inviati (email) VALUES (%s) ON CONFLICT DO NOTHING", (x["EMAIL"],))
            conn.commit()
        ora = datetime.now(timezone.utc)
        comuni = {"header": "[DEFAULT_HEADER]", "footer": "[DEFAULT_FOOTER]", "inlineImageActivation": False}
        if lid_senza:
            r = c.post(f"{API}/emailCampaigns", json={
                **comuni, "name": LISTA_SENZA, "subject": base.get("subject"), "previewText": base.get("previewText") or "",
                "sender": {"name": "Skovo", "id": snd["id"]}, "replyTo": base.get("replyTo") or RISPOSTE,
                "htmlContent": base["htmlContent"], "recipients": {"listIds": [lid_senza]},
                "scheduledAt": (ora + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")})
            esito["campagna_senza"] = r.json().get("id") if r.status_code < 300 else f"HTTP {r.status_code}"
        if lid_con:
            r = c.post(f"{API}/emailCampaigns", json={
                **comuni, "name": LISTA_CON, "subject": OGGETTO_CON, "previewText": ANTEPRIMA_CON,
                "sender": {"name": "Skovo", "id": MITTENTE_CON}, "replyTo": RISPOSTE,
                "htmlContent": html_con, "recipients": {"listIds": [lid_con]},
                "scheduledAt": (ora + timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")})
            esito["campagna_con"] = r.json().get("id") if r.status_code < 300 else f"HTTP {r.status_code}"
    return esito
