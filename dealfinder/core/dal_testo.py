"""Dati mancanti letti dal testo dell'annuncio (titolo + descrizione), senza AI e senza inventare.

Si riempiono SOLO i campi vuoti (alimentazione, anno, km) e solo con indicazioni chiare nel testo:
parola esplicita ("diesel", "benzina", "gpl"…) o sigla di motore inequivocabile (TDI, Multijet, TSI…);
anno con una parola vicina ("anno 2014", "immatricolata 2014", "03/2014"); km scritti come km.
Se nel testo ci sono indicazioni in conflitto (es. due alimentazioni diverse) il campo resta vuoto.
"""
from __future__ import annotations

import datetime
import re

_FUEL_PAROLE = [
    ("gpl", r"\bgpl\b|\blpg\b|bi-?fuel\s+gpl|gpl\s+di\s+serie"),
    ("metano", r"\bmetano\b|\bcng\b|natural\s+power|\btgi\b|ecofuel"),
    ("elettrica", r"\b(100\s*%\s*)?elettrica\b|\bfull\s+electric\b|\bbev\b"),
    ("ibrida", r"\bibrid[ao]\b|\bhybrid\b|\bplug-?in\b|\bphev\b|\bmhev\b|\bmild\s+hybrid\b"),
    ("diesel", r"\bdiesel\b|\bgasolio\b|\btdi\b|\bjtd[m]?\b|multi-?jet|\bmjt\b|\bhdi\b|bluehdi|\bdci\b|\bcrdi\b|"
               r"\btdci\b|\bcdi\b|\bd-?4d\b|\bcdti\b|\bblue\s?motion\s+tdi\b|\bd\s?-?\s?cat\b|\b\d\.\d\s?d\b"),
    ("benzina", r"\bbenzina\b|\btsi\b|\btfsi\b|\bfsi\b|t-?jet|\bfire\b|vvt-?i|ecoboost|puretech|\btce\b|\bmpi\b|\bgdi\b|\bt-?gdi\b"),
]
RE_ANNO = re.compile(r"(?:anno|immatricolat[ao]|immatricolazione|del|dal|registrazione|prima\s+immatricolazione)\s*:?\s*"
                     r"(?:(?:0?[1-9]|1[0-2])\s*[/.-]\s*)?((?:19[89]|20[0-3])\d)\b", re.I)
RE_MESE_ANNO = re.compile(r"\b(?:0?[1-9]|1[0-2])\s*/\s*((?:19[89]|20[0-3])\d)\b")
RE_KM = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.\s]\d{3})+|\d{2,6})\s*(mila|k)?\s*(?:km|chilometri|kilometri)\b", re.I)
RE_KM_DOPO = re.compile(r"\b(?:km|chilometri|kilometri|percorrenza)\s*(?:percorsi|reali|certificati|totali)?\s*:?\s*"
                        r"(\d{1,3}(?:[.\s]\d{3})+|\d{2,6})\s*(mila|k)?\b", re.I)


def alimentazione(testo: str) -> str | None:
    t = testo.lower()
    trovate = {nome for nome, rx in _FUEL_PAROLE if re.search(rx, t)}
    if "gpl" in trovate or "metano" in trovate:          # "benzina/gpl": vale il gas
        trovate.discard("benzina")
    if "ibrida" in trovate:
        trovate -= {"benzina", "diesel", "elettrica"}
    return trovate.pop() if len(trovate) == 1 else None


def anno(testo: str) -> int | None:
    oggi = datetime.date.today().year
    valori = {int(m.group(1)) for m in RE_ANNO.finditer(testo)} | {int(m.group(1)) for m in RE_MESE_ANNO.finditer(testo)}
    valori = {v for v in valori if 1985 <= v <= oggi}
    return valori.pop() if len(valori) == 1 else None


def _km(num: str, mila: str | None) -> int | None:
    n = int(re.sub(r"\D", "", num))
    if mila:
        n *= 1000
    return n if 100 <= n <= 600000 else None


def chilometri(testo: str) -> int | None:
    valori = {v for m in RE_KM.finditer(testo) if (v := _km(m.group(1), m.group(2)))}
    valori |= {v for m in RE_KM_DOPO.finditer(testo) if (v := _km(m.group(1), m.group(2)))}
    valori = {v for v in valori if v >= 1000}               # "a 2 km dalla stazione" non sono km dell'auto
    return valori.pop() if len(valori) == 1 else None


def completa(campi: dict, titolo: str | None, descrizione: str | None) -> dict:
    """Ritorna SOLO i campi mancanti trovati nel testo: {"fuel": ..., "year": ..., "mileage_km": ...}."""
    testo = f"{titolo or ''}\n{descrizione or ''}"
    out = {}
    if not campi.get("fuel"):
        f = alimentazione(testo)
        if f:
            out["fuel"] = f
    if not campi.get("year"):
        a = anno(testo)
        if a:
            out["year"] = a
    if campi.get("mileage_km") in (None, ""):
        k = chilometri(testo)
        if k:
            out["mileage_km"] = k
    return out


def completa_db(conn, limite: int = 20000) -> dict:
    """Riempie nel database i campi mancanti degli annunci attivi di privati (segnati in field_origin come 'dal_testo')."""
    import json
    rows = conn.execute(
        """SELECT id, title, description, fuel, year, mileage_km, field_origin FROM listings
           WHERE status='attivo' AND seller_type='privato' AND (fuel IS NULL OR year IS NULL OR mileage_km IS NULL)
             AND (description IS NOT NULL OR title IS NOT NULL) ORDER BY last_seen_at DESC LIMIT %s""", (limite,)).fetchall()
    st = {"letti": len(rows), "fuel": 0, "year": 0, "mileage_km": 0, "aggiornati": 0}
    for r in rows:
        nuovi = completa(r, r["title"], r["description"])
        if not nuovi:
            continue
        orig = r["field_origin"] if isinstance(r["field_origin"], dict) else json.loads(r["field_origin"] or "{}")
        orig.update({k: "dal_testo" for k in nuovi})
        sets = ", ".join(f"{k}=%({k})s" for k in nuovi)
        conn.execute(f"UPDATE listings SET {sets}, field_origin=%(fo)s WHERE id=%(id)s",
                     {**nuovi, "fo": json.dumps(orig), "id": r["id"]})
        for k in nuovi:
            st[k] += 1
        st["aggiornati"] += 1
    conn.commit()
    return st
