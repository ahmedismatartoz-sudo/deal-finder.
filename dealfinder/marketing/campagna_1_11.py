"""1/11 (cliente): email a tutti i contatti già in Brevo, tre testi (A, B, C) divisi a rotazione, 2 auto vere per fascia.

Testi approvati dal cliente il 1/11: "Buongiorno, siamo l'azienda Skovo", mercato dei privati seguito ogni giorno,
truffe scartate dal software, appuntamento fissato da noi e trattativa seguita da noi. Niente prezzo del servizio.
Schede auto oneste: prezzo vero dell'annuncio, valore di mercato e margine prudente del sito (2.000-4.500 €).

CAMPAGNA_1_11=prova  -> sceglie le auto e scrive nei log quali (nessun invio)
CAMPAGNA_1_11=invia  -> sceglie le stesse regole, divide i contatti e manda (una volta sola: job campagna_1_11:invio)
Nei log solo conteggi e auto (mai indirizzi email).
"""
from __future__ import annotations

import logging
import os
import re
import statistics
from html import escape

import httpx

from .auto_invio import _manda
from .brevo_liste import API, _chiave, _foto_ok

lg = logging.getLogger("dealfinder.marketing")

FASCE = {"utilitarie": (3000, 8000), "medie": (8000, 15000), "alte": (15000, 50000), "supercar": (20000, 70000)}
TELV = "348 242 5633"

# 1/11 (cliente): esempi solo di auto molto richieste che si vendono in fretta: benzina o ibride, sotto 80.000 km
CARB_OK = re.compile(r"benzin|ibrid|hybrid|elettr|gpl|metano", re.I)
POPOLARI = re.compile(r"\b(panda|500x?|500l|ypsilon|yaris|aygo|c-hr|chr|rav ?4|corolla|polo|golf|t-roc|troc|t-cross|tcross|"
                      r"tiguan|up|clio|captur|arkana|208|2008|3008|corsa|mokka|fiesta|puma|kuga|focus|sandero|duster|jogger|"
                      r"i10|i20|i30|kona|tucson|bayon|picanto|rio|stonic|sportage|niro|ceed|qashqai|juke|micra|renegade|"
                      r"compass|avenger|swift|ignis|vitara|s-cross|c3|c4|cooper|countryman|a1|a3|q2|q3|serie 1|118|"
                      r"116|120|x1|x2|classe a|a 180|a 200|gla|cla|mx-5|cx-3|cx-30|mazda2|mazda3|fabia|kamiq|karoq|ibiza|arona|"
                      r"leon|ateca|formentor|tonale|giulietta|zr-v|hr-v|jazz|civic)\b", re.I)
P = 'style="margin:0 0 14px;font-size:15px;line-height:1.55;color:#222"'


def _eur(v) -> str:
    return f"{int(round(v)):,}".replace(",", ".")


def _fascia(prezzi) -> str:
    p = [x for x in prezzi if x and x > 300]
    if not p:
        return "medie"
    m = statistics.median(p)
    return "utilitarie" if m < 8000 else "medie" if m < 15000 else "alte" if m < 50000 else "supercar"


def _breve(it: dict) -> str:
    return " ".join(x for x in [it.get("marca"), it.get("modello")] if x) or str(it.get("nome") or "")


def _card(it: dict) -> str:
    pv = it.get("pv") or {}
    n = escape(str(it.get("nome") or ""))
    imgs = "".join(
        f'<td width="50%" style="padding:0 {1-k}px 0 {k}px"><img src="https://scovo.onrender.com/email-foto/{it["id"]}/{k}.jpg" '
        f'width="279" style="width:100%;display:block;height:auto" alt="{n}"></td>' for k in (0, 1))
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e6e6ee;border-radius:10px;margin:0 0 14px;overflow:hidden">'
        f'<tr><td style="padding:0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{imgs}</tr></table></td></tr>'
        '<tr><td style="padding:12px 16px 14px">'
        f'<div style="font-size:17px;font-weight:700;margin:0 0 4px">{n}</div>'
        f'<div style="font-size:13px;color:#666;margin:0 0 8px">{it.get("anno")} · {_eur(it.get("km") or 0)} km · {escape(str(it.get("carb") or ""))} · venditore privato</div>'
        f'<div style="font-size:14px;color:#222">Prezzo <b>{_eur(it["prezzo"])} €</b> · valore di mercato circa <b>{_eur(pv["M"])} €</b></div>'
        f'<div style="font-size:14px;color:#0b7a43;font-weight:700;margin-top:4px">Margine stimato: +{_eur(pv["margine"])} €</div>'
        '</td></tr></table>')


def _pagina(paragrafi: list[str], schede: str, chiusura: str, bottoni: bool) -> str:
    ps = "".join(f"<p {P}>{t}</p>" for t in paragrafi)
    firma = (f'<p style="margin:18px 0 6px;font-size:15px">📞 <a href="tel:+393482425633" style="color:#1a56db;font-weight:bold">Chiamami: {TELV}</a></p>'
             '<p style="margin:0 0 18px;font-size:15px">✋ <a href="mailto:info@skovo.app?subject=Sono%20interessato%20-%20richiamatemi&body='
             'Buongiorno%2C%20sono%20interessato.%20Il%20mio%20numero%20%C3%A8%3A%20" style="color:#1a56db;font-weight:bold">Sono interessato, richiamatemi</a></p>'
             '<p style="margin:0 0 22px;font-size:15px;color:#222">Skovo</p>') if bottoni else \
        f'<p style="margin:0 0 22px;font-size:15px;color:#222">Skovo – {TELV}</p>'
    return ('<!DOCTYPE html>\n<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
            'initial-scale=1"><title>Skovo</title></head>\n<body style="margin:0;padding:0;background:#ffffff;'
            'font-family:Arial,Helvetica,sans-serif">\n<div style="max-width:560px;padding:20px 18px">'
            + ps + schede + f"<p {P}>{chiusura}</p>" + firma
            + '<p style="margin:0;font-size:12px;color:#888">Se non vi interessa rispondete "no" oppure <a href="{{ unsubscribe }}" '
            'style="color:#888">cancellatevi qui</a>: non vi scriveremo più.</p></div></body></html>')


def testi(auto: list[dict]) -> dict:
    """Le tre email (oggetto, html) per una fascia, con le sue 2 auto."""
    schede = "".join(_card(a) for a in auto)
    a1 = auto[0]
    sotto = int(round(((a1.get("pv") or {})["M"] - a1["prezzo"]) / 100.0)) * 100
    mod = escape(_breve(a1))
    A = ("2 affari da privati vicino a voi", _pagina([
        "Buongiorno, siamo l'azienda Skovo.",
        "Seguiamo ogni giorno il mercato dei privati e troviamo le auto in vendita sotto il prezzo di mercato. "
        "Il nostro software scarta truffe, auto incidentate e annunci falsi.",
        "Quando ne scegliete una, <b>chiamiamo noi il privato, vi fissiamo l'appuntamento e seguiamo la trattativa</b>. "
        "Voi andate solo a vedere l'auto.",
        "Due esempi di oggi:"], schede, "Ci sentiamo?", True))
    B = ("Ho 2 auto da privati per voi", _pagina([
        "Buongiorno, siamo l'azienda Skovo.",
        "Seguiamo il mercato dei privati e troviamo auto sotto prezzo, già controllate dal nostro software contro truffe "
        "e annunci falsi. <b>Vi fissiamo noi l'appuntamento</b> con il proprietario e seguiamo la trattativa.",
        "Due di oggi, vicino a voi:"], schede, "Ci sentiamo?", False))
    C = (f"{_breve(a1)} a {_eur(sotto)} € sotto mercato", _pagina([
        "Buongiorno, siamo l'azienda Skovo.",
        f"Troviamo auto di privati sotto prezzo e scartiamo le truffe con il nostro software. Questa {mod} la vende un "
        f"privato vicino a voi, {_eur(sotto)} € sotto il suo valore.",
        "<b>Se vi interessa, vi fissiamo noi l'appuntamento.</b>"], schede, "Mi rispondete anche solo con un sì?", False))
    return {"A": A, "B": B, "C": C}


def scegli(items: list[dict]) -> dict[str, list[dict]]:
    """2 auto per fascia dal sito: privati della zona, margine prudente credibile, tanti confronti, 2 foto che si aprono."""
    prov = {p.strip().upper() for p in (os.environ.get("ZONA_SITO") or "").split(",") if p.strip()}
    mmin, mmax = int(os.environ.get("C111_MARG_MIN", "2000")), int(os.environ.get("C111_MARG_MAX", "4500"))
    usate, out = set(), {}
    for fascia, (lo, hi) in FASCE.items():
        cand = []
        for it in items:
            pv = it.get("pv") or {}
            m = re.search(r"\((\w\w)\)\s*$", str(it.get("zona") or ""))
            if (it.get("supercar") or it.get("incidentata") or it["id"] in usate or not pv.get("M")
                    or pv.get("margine") is None or pv.get("categoria") not in ("opportunita", "candidato")
                    or not (mmin <= pv["margine"] <= mmax) or not (lo <= it["prezzo"] < hi)
                    or (pv.get("n") or 0) < 8 or (it.get("km") or 10**9) > int(os.environ.get("C111_KM_MAX", "80000"))
                    or not it.get("anno") or not CARB_OK.search(str(it.get("carb") or ""))
                    or not POPOLARI.search(f"{it.get('modello') or ''} {it.get('nome') or ''}")
                    or (it.get("n_foto") or 0) < 2 or (prov and (not m or m.group(1).upper() not in prov))):
                continue
            cand.append(it)
        # le più credibili: più confronti uguali, poi margine
        cand.sort(key=lambda x: (-((x.get("pv") or {}).get("n") or 0), -(x["pv"]["margine"])))
        scelte = []
        for it in cand:
            if _foto_ok(_card(it)):
                scelte.append(it)
                usate.add(it["id"])
            if len(scelte) == 2:
                break
        out[fascia] = scelte
        lg.warning("CAMPAGNA_1_11 auto %s (candidate %d): %s", fascia, len(cand), [
            {"id": a["id"], "auto": a.get("nome"), "anno": a.get("anno"), "km": a.get("km"), "zona": a.get("zona"),
             "prezzo": a["prezzo"], "M": a["pv"]["M"], "margine": a["pv"]["margine"], "n": a["pv"].get("n"),
             "fonte": a.get("fonte")} for a in scelte])
    return out


def _contatti(c: httpx.Client) -> list[str]:
    out, off = [], 0
    while True:
        r = c.get(f"{API}/contacts", params={"limit": 1000, "offset": off})
        r.raise_for_status()
        cs = r.json().get("contacts") or []
        out += [x["email"].lower() for x in cs if x.get("email") and not x.get("emailBlacklisted")]
        if len(cs) < 1000:
            break
        off += 1000
    return sorted(set(out))


def _fasce_contatti(conn, emails: list[str]) -> dict[str, list[str]]:
    per_email = {}
    for r in conn.execute("SELECT chiave, email FROM lead_email WHERE cardinality(email) > 0").fetchall():
        for e in r["email"] or []:
            per_email.setdefault(e.lower(), r["chiave"])
    prezzi = {}
    try:
        for r in conn.execute("SELECT chiave, prezzo FROM lead_tutti_ann").fetchall():
            prezzi.setdefault(r["chiave"], []).append(r["prezzo"])
    except Exception:
        pass
    out: dict[str, list[str]] = {f: [] for f in FASCE}
    for e in emails:
        out[_fascia(prezzi.get(per_email.get(e), []))].append(e)
    return out


def esegui(items_fn, modo: str) -> dict:
    from ..db import connect
    auto = scegli(items_fn())
    mancano = [f for f, a in auto.items() if len(a) < 2]
    if mancano:
        lg.warning("CAMPAGNA_1_11 auto insufficienti per %s: niente invio", mancano)
        return {"auto_mancanti": mancano}
    if modo != "invia":
        return {"prova": {f: [a["id"] for a in v] for f, v in auto.items()}}
    key = _chiave()
    with httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60) as c, connect() as conn:
        emails = _contatti(c)
        gruppi = _fasce_contatti(conn, emails)
        lg.warning("CAMPAGNA_1_11 contatti %d per fascia %s", len(emails), {f: len(v) for f, v in gruppi.items()})
        esito = {}
        for fascia, ems in gruppi.items():
            if not ems:
                continue
            tt = testi(auto[fascia])
            for k, lettera in enumerate("ABC"):
                parte = ems[k::3]
                if not parte:
                    continue
                ogg, html = tt[lettera]
                ok = _manda(c, f"Skovo 1-11 {lettera} {fascia}", ogg, html, parte)
                esito[f"{fascia}:{lettera}"] = (len(parte), ok)
        lg.warning("CAMPAGNA_1_11_FINE %s", esito)
        return {"inviate": esito}
