"""8/11 (cliente: "carica i primi 1450 su Brevo profilati, due auto per ogni commerciante molto vicine al suo salone").

Email su misura dal mittente nuovo marco@team.skovo.app, a scaglioni (deciso col cliente: 150, 300, 500, il resto):
- chi: email raccolte (lead_email) dei saloni con comune/provincia noti; fuori disiscritti/bloccati, PEC, Loconte,
  Libero/Microsoft (oggi ci rifiutano), un solo indirizzo per salone. Prima chi ha già aperto, poi i più vicini a
  Milano (dove ci sono più auto).
- auto: 2 del sito per salone, le più vicine (stesso comune, poi stessa provincia, poi province vicine entro
  50 km fra capoluoghi), di modello diverso, nella fascia di prezzo del salone se nota (annunci AutoScout24).
  Prezzo mostrato come nei WhatsApp dell'8/11: annuncio -1.500 € (-1.000 € sotto 7.000 €), margine su quello.
- in Brevo: attributi SK_AUTO1/SK_DATI1/SK_AUTO2/SK_DATI2 per contatto, liste per scaglione, campagna del primo
  scaglione programmata; le altre restano in bozza (le auto vanno rinfrescate prima di mandarle).
Nei log solo conteggi, mai indirizzi.
"""
from __future__ import annotations

import logging
import re
import statistics

import httpx

from ..geo import coords, km
from .brevo_liste import API, TETTO_BREVO, _DOMINI_FERMI, _EMAIL_OK, _NON_EMAIL, _cartella, _chiave, _esporta, _liste

lg = logging.getLogger("dealfinder.marketing")

SCAGLIONI = (150, 300, 500)                     # poi il resto
RAGGIO_KM = 50                                  # distanza massima fra capoluoghi di provincia
USO_MAX = 10                                    # stessa auto al massimo a 10 saloni
ATTRIBUTI = ("SK_AUTO1", "SK_DATI1", "SK_AUTO2", "SK_DATI2", "SK_ZONA")
MITTENTE = {"name": "Marco di Skovo", "email": "marco@team.skovo.app"}
RISPOSTE = "info@skovo.app"
NOME_LISTA = "Skovo su misura 08-11 S{}"


def _eur(v) -> str:
    return f"{int(round(v)):,}".replace(",", ".")


def _norm_comune(c: str | None) -> str:
    return re.sub(r"[^a-z]", "", (c or "").lower().split("(")[0])


def email_buona(e: str) -> bool:
    e = (e or "").strip().lower()
    if not e or not _EMAIL_OK.match(e) or _NON_EMAIL.search(e) or "loconte" in e:
        return False
    dom = e.split("@")[-1]
    if "pec" in dom or "legalmail" in dom:
        return False
    return not any(dom == d or dom.startswith(d) for d in _DOMINI_FERMI)


def auto_sito(items: list[dict]) -> list[dict]:
    """Auto del sito utilizzabili: prezzo, mercato e margine noti, almeno 8 auto uguali nel confronto."""
    out = []
    for it in items:
        pv = it.get("pv") or {}
        R, pr, m = pv.get("R_finale") or pv.get("R"), it.get("prezzo"), pv.get("margine")
        zona = it.get("zona") or ""
        p = re.search(r"\(([A-Z]{2})\)", zona)
        nome = str(it.get("nome") or "")
        if not (R and pr and m and p) or (pv.get("n") or 0) < 8 or m < 1500 or re.search(r"\biva\b|\+iva", nome, re.I):
            continue
        if (it.get("km") or 0) < 10000 and (it.get("anno") or 0) < 2022:
            continue
        out.append({"id": it.get("id"), "marca": str(it.get("marca") or ""), "modello": str(it.get("modello") or ""),
                    "nome": nome, "anno": it.get("anno"), "km": it.get("km") or 0, "carb": it.get("carb") or "",
                    "prezzo": int(pr), "R": int(round(R / 100) * 100), "prov": p.group(1),
                    "comune": zona.split(" (")[0].split(" - ")[0].strip().title()})
    return out


def _titolo(a: dict) -> str:
    t = [x for x in a["nome"].split() if not re.fullmatch(r"(19|20)\d\d\w*", x)
         and not re.search(r"prezzo|altro|allestimento", x, re.I)][:5]
    if len(t) < 2:
        t = [a["marca"], a["modello"]]
    return " ".join(t)


def prezzo_mostrato(p: int) -> int:
    return p - (1000 if p < 7000 else 1500)


def testi(a: dict) -> tuple[str, str]:
    p = prezzo_mostrato(a["prezzo"])
    return (f"{_titolo(a)} {a['anno']} · {_eur(a['km'])} km · {a['carb']} · {a['comune']}",
            f"{_eur(p)} € · mercato {_eur(a['R'])} € · margine +{_eur(round((a['R'] - p) / 100) * 100)} €")


def distanza(dealer: dict, a: dict) -> float | None:
    """0 stesso comune; altrimenti distanza fra capoluoghi (+5 se comune diverso nella stessa provincia)."""
    if _norm_comune(dealer.get("comune")) and _norm_comune(dealer.get("comune")) == _norm_comune(a["comune"]):
        return 0.0
    c1, c2 = coords(dealer.get("prov")), coords(a["prov"])
    if not c1 or not c2:
        return None
    return km(c1, c2) + 5


def scegli_due(dealer: dict, auto: list[dict], uso: dict) -> list[dict]:
    """Le 2 auto più vicine al salone (modelli diversi), nella sua fascia di prezzo se nota; marca che tratta = +bonus."""
    med = dealer.get("pmed")
    lo, hi = (med * 0.45, med * 1.6) if med else (3000, 40000)
    marche = {m.lower() for m in dealer.get("marche") or []}
    cand = []
    for a in auto:
        if uso.get(a["id"], 0) >= USO_MAX or not lo <= a["prezzo"] <= hi:
            continue
        d = distanza(dealer, a)
        if d is None or d > RAGGIO_KM:
            continue
        punti = d - (15 if a["marca"].lower() in marche else 0) - min(a["R"] - a["prezzo"], 8000) / 1000
        cand.append((punti, a))
    cand.sort(key=lambda x: x[0])
    scelte, modelli = [], set()
    for _, a in cand:
        k = (a["marca"].lower(), a["modello"].lower())
        if k in modelli:
            continue
        scelte.append(a)
        modelli.add(k)
        if len(scelte) == 2:
            break
    return scelte if len(scelte) == 2 else []


def scegli_saloni(righe: list[dict], bloccati: set, aperti: set, totale: int) -> list[dict]:
    """Un indirizzo per salone, filtri, ordine: prima chi ha aperto, poi i più vicini a Milano. Funzione pura."""
    from .invii import impronta, norm_email
    mi = coords("MI")
    visti, out = set(), []
    for r in righe:
        if not r.get("prov") or not coords(r["prov"]):
            continue
        for e in r.get("email") or []:
            e = norm_email(e)
            if e in visti or not email_buona(e) or impronta(e) in bloccati:
                continue
            visti.add(e)
            out.append({**r, "email": e, "aperto": e in aperti, "dmi": km(mi, coords(r["prov"]))})
            break
    out.sort(key=lambda r: (not r["aperto"], r["dmi"]))
    return out[:totale]


def assegna(saloni: list[dict], auto: list[dict], totale: int) -> list[dict]:
    uso: dict = {}
    out = []
    for s in saloni:
        due = scegli_due(s, auto, uso)
        if not due:
            continue
        for a in due:
            uso[a["id"]] = uso.get(a["id"], 0) + 1
        (a1, d1), (a2, d2) = testi(due[0]), testi(due[1])
        out.append({**s, "attr": {"SK_AUTO1": a1, "SK_DATI1": d1, "SK_AUTO2": a2, "SK_DATI2": d2,
                                  "SK_ZONA": (s.get("comune") or s["prov"]).title()}})
        if len(out) >= totale:
            break
    return out


def html_email() -> str:
    card = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e3e3ea;'
            'border-radius:10px;margin:0 0 14px"><tr><td style="padding:14px 16px">'
            '<div style="font-size:16px;font-weight:700;color:#1d1d24">{{{{ contact.SK_AUTO{n} }}}}</div>'
            '<div style="font-size:14px;color:#0b7a43;margin-top:6px">{{{{ contact.SK_DATI{n} }}}}</div>'
            '</td></tr></table>')
    return (
        '<!DOCTYPE html><html><body style="margin:0;background:#ffffff">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;'
        'font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#1d1d24"><tr><td style="padding:20px 16px">'
        '<p>Buongiorno,</p>'
        '<p>sono Marco di Skovo. Ogni giorno il nostro software legge gli annunci dei privati su Subito e AutoScout24, '
        'confronta ogni auto con quelle uguali e tiene solo quelle sotto il prezzo di mercato.</p>'
        '<p>Oggi ne abbiamo due vicino a voi:</p>'
        + card.format(n=1) + card.format(n=2) +
        '<p>Se una vi interessa rispondete a questa email o chiamatemi al 348 242 5633: contattiamo noi il privato '
        'e vi fissiamo l\'appuntamento.</p>'
        '<p>Le altre auto le trovate su <a href="https://scovo.it" style="color:#3b5bdb">scovo.it</a></p>'
        '<p>Buona giornata,<br>Marco<br>Skovo</p>'
        '</td></tr></table></td></tr></table></body></html>')


def _righe_db(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT e.chiave, e.email, COALESCE(d.nome, m.nome) AS nome, COALESCE(d.comune, m.comune) AS comune, "
        "upper(left(COALESCE(d.prov, m.prov), 2)) AS prov FROM lead_email e "
        "LEFT JOIN lead_tutti_dealer d ON d.chiave = e.chiave "
        "LEFT JOIN lead_maps m ON e.chiave = 'maps:' || m.place_id "
        "WHERE cardinality(e.email) > 0").fetchall()
    prezzi: dict = {}
    for a in conn.execute("SELECT chiave, prezzo, marca FROM lead_tutti_ann WHERE prezzo > 500").fetchall():
        p = prezzi.setdefault(a["chiave"], ([], {}))
        p[0].append(a["prezzo"])
        if a["marca"]:
            p[1][a["marca"]] = p[1].get(a["marca"], 0) + 1
    out = []
    for r in rows:
        r = dict(r)
        pz, mc = prezzi.get(r["chiave"], ([], {}))
        r["pmed"] = statistics.median(pz) if len(pz) >= 3 else None
        r["marche"] = [m for m, _ in sorted(mc.items(), key=lambda x: -x[1])[:6]]
        out.append(r)
    return out


def prepara(items: list[dict], totale: int = 1450, quando: str = "2026-11-09T09:00:00+01:00",
            campagne_aperture=(69, 68, 65, 54, 53, 52, 44, 43, 42, 41, 40, 39, 35, 34, 33)) -> dict:
    from ..db import connect
    from . import invii
    from .auto_invio import _contatti_brevo
    from .brevo_liste import BLOCCATI_MINIMI, _bloccati_0111
    key = _chiave()
    if not key:
        return {"errore": "chiave"}
    esito: dict = {}
    auto = auto_sito(items)
    esito["auto_sito"] = len(auto)
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c:
        if c.get(f"{API}/account").status_code != 200:
            return {"errore": "chiave rifiutata"}
        contatti = _contatti_brevo(c)
        with connect() as conn:
            invii.sincronizza_da_brevo(conn, contatti)
            bloccati = invii.disiscritti(conn) | _bloccati_0111()
            righe = _righe_db(conn)
        esito["disiscritti"] = len(bloccati)
        if len(bloccati) < BLOCCATI_MINIMI:
            esito["fermo"] = "disiscritti_incompleti"
            return esito
        aperti: set = set()
        for cid in campagne_aperture:
            aperti |= set(_esporta(c, cid, "openers"))
        bloccati_in_brevo = [x for x in contatti if invii.contatto_bloccato(x)]
        posto = TETTO_BREVO - len(bloccati_in_brevo)
        saloni = scegli_saloni(righe, bloccati, aperti, totale * 2)
        scelti = assegna(saloni, auto, min(totale, posto))
        esito.update({"saloni_candidati": len(saloni), "scelti": len(scelti),
                      "di_cui_aperto_prima": sum(1 for s in scelti if s["aperto"]),
                      "bloccati_tenuti_in_brevo": len(bloccati_in_brevo)})
        if not scelti:
            return esito
        # posto in Brevo: via i contatti non bloccati che non sono fra gli scelti (i bloccati restano bloccati)
        from urllib.parse import quote
        tieni = {s["email"] for s in scelti}
        tolti = 0
        for x in contatti:
            e = (x.get("email") or "").lower()
            if e and e not in tieni and not invii.contatto_bloccato(x):
                if c.delete(f"{API}/contacts/{quote(e, safe='')}").status_code in (200, 204, 404):
                    tolti += 1
        esito["tolti_da_brevo"] = tolti
        # attributi
        esistenti = {a.get("name") for a in (c.get(f"{API}/contacts/attributes").json() or {}).get("attributes", [])}
        for nome in ATTRIBUTI:
            if nome not in esistenti:
                c.post(f"{API}/contacts/attributes/normal/{nome}", json={"type": "text"})
        # liste per scaglione e import
        cart = _cartella(c)
        liste = _liste(c)
        tagli, inizio, ids = list(SCAGLIONI) + [len(scelti)], 0, []
        for i, n in enumerate(tagli, 1):
            parte = scelti[inizio:inizio + n] if i <= len(SCAGLIONI) else scelti[inizio:]
            inizio += len(parte)
            if not parte:
                break
            nome = NOME_LISTA.format(i)
            lid = liste.get(nome)
            if lid is None:
                r = c.post(f"{API}/contacts/lists", json={"name": nome, "folderId": cart})
                r.raise_for_status()
                lid = r.json()["id"]
            for k in range(0, len(parte), 500):
                r = c.post(f"{API}/contacts/import", json={
                    "jsonBody": [{"email": s["email"], "attributes": s["attr"]} for s in parte[k:k + 500]],
                    "listIds": [lid], "updateExistingContacts": True, "emptyContactsAttributes": False})
                esito[f"import_S{i}_{k}"] = r.status_code
            ids.append((i, lid, len(parte)))
            if i > len(SCAGLIONI):
                break
        esito["scaglioni"] = [[i, n] for i, _, n in ids]
        # campagne: la prima programmata, le altre in bozza
        for i, lid, _n in ids:
            corpo = {"name": f"Skovo su misura 08-11 S{i}", "subject": "Due auto da privati vicino a voi",
                     "sender": MITTENTE, "replyTo": RISPOSTE, "htmlContent": html_email(),
                     "recipients": {"listIds": [lid]}, "inlineImageActivation": False}
            if i == 1:
                corpo["scheduledAt"] = quando
            r = c.post(f"{API}/emailCampaigns", json=corpo)
            esito[f"campagna_S{i}"] = r.json().get("id") if r.status_code < 300 else f"HTTP {r.status_code}"
    return esito
