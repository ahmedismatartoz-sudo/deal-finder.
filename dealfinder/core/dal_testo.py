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
               r"\btdci\b|\bcdi\b|\bd-?4d\b|\bcdti\b|\bblue\s?motion\s+tdi\b|\bd\s?-?\s?cat\b|\b\d\.\d\s?d\b|"
               # 1/11: sigle dei modelli diesel (BMW 118d/320d/X1 sDrive18d, Mercedes A 180 d/C 220 d, Volvo D3/D4, Mazda Skyactiv-D)
               r"\b[1-8]\d{2}\s?d\b(?!['’])|\b[sx]drive\s?\d{2}d\b|\b[a-z]{1,3}\s?\d{3}\s?(?:cdi|d)\b(?!['’])|\bbluetec\b|\bd[2-5]\b|skyactiv-?d|\becoblue\b|\bmultijet\b"),
    ("benzina", r"\bbenzina\b|\btsi\b|\btfsi\b|\bfsi\b|t-?jet|\bfire\b|vvt-?i|ecoboost|puretech|\btce\b|\bmpi\b|\bgdi\b|\bt-?gdi\b|"
               # 1/11: altre sigle benzina (EcoTSI, FireFly, TwinAir, VTEC, Skyactiv-G, Kompressor, BMW 118i/320i)
               r"ecotsi|firefly|twin-?air|\bi-?vtec\b|\bvtec\b|skyactiv-?g|kompressor|\b[1-8]\d{2}\s?i\b(?!['’])|\b[sx]drive\s?\d{2}i\b|\bturbo\s?benzina\b"),
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


# 1/11: cambio e potenza scritti nel testo (servono per trovare confronti "stesso motore e cambio")
RE_AUTO = re.compile(r"cambio\s+(?:automatico|auto\b|robotizzato|sequenziale)|\bdsg\b|\bs-?\s?tronic\b|steptronic|tiptronic|\beat\s?[68]\b|\bedc\b|"
                     r"\bcvt\b|powershift|\b[579]g-?tronic\b|\bdct\b|\bxtronic\b|\be-?cvt\b|\bauto\s?gear\b", re.I)
RE_MANUALE = re.compile(r"cambio\s+manuale|\bmanuale\s+(?:a\s+)?\d\s+marce|\b\d\s+marce\s+manuale", re.I)
# ("clima automatico", "libretto e manuale" non dicono niente sul cambio: servono parole vicine a "cambio"/"marce")
RE_CV = re.compile(r"\b(\d{2,3})\s?(?:cv|hp|ps)\b", re.I)
RE_KW = re.compile(r"\b(\d{2,3})\s?kw\b", re.I)


def cambio(testo: str) -> str | None:
    a, m = bool(RE_AUTO.search(testo)), bool(RE_MANUALE.search(testo))
    return "automatico" if a and not m else "manuale" if m and not a else None


def potenza_kw(testo: str) -> int | None:
    kw = {int(x) for x in RE_KW.findall(testo)}
    kw |= {int(round(int(x) * 0.7355)) for x in RE_CV.findall(testo)}
    kw = {k for k in kw if 25 <= k <= 500}
    if not kw:
        return None
    # lo stesso motore scritto in CV e in kW dà valori vicini: si accetta se tutti stanno entro ±3 kW
    return round(sum(kw) / len(kw)) if max(kw) - min(kw) <= 3 else None


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
    testo = f"{campi.get('version_raw') or ''}\n{titolo or ''}\n{descrizione or ''}"     # 1/11: anche la versione
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
    if "gearbox" in campi and not campi.get("gearbox"):
        g = cambio(testo)
        if g:
            out["gearbox"] = g
    if "power_kw" in campi and not campi.get("power_kw"):
        kw = potenza_kw(testo)
        if kw:
            out["power_kw"] = kw
    return out


def completa_db(conn, limite: int = 20000) -> dict:
    """Riempie nel database i campi mancanti degli annunci attivi (segnati in field_origin come 'dal_testo').
    1/11: anche commercianti e annunci fuori zona (servono come confronti: senza alimentazione un annuncio non entra
    mai nel mercato), a pezzi da 3.000 per la memoria; si legge anche la versione dell'annuncio."""
    import json
    import os
    tetto = int(os.environ.get("DAL_TESTO_MAX", "60000"))
    st = {"letti": 0, "fuel": 0, "year": 0, "mileage_km": 0, "gearbox": 0, "power_kw": 0, "aggiornati": 0}
    ultimo = 0
    while st["letti"] < tetto:
        rows = conn.execute(
            """SELECT id, title, description, version_raw, fuel, year, mileage_km, gearbox, power_kw, field_origin FROM listings
               WHERE status='attivo' AND (fuel IS NULL OR year IS NULL OR mileage_km IS NULL OR gearbox IS NULL OR power_kw IS NULL)
                 AND (description IS NOT NULL OR title IS NOT NULL OR version_raw IS NOT NULL) AND id > %s
               ORDER BY id LIMIT 3000""", (ultimo,)).fetchall()
        if not rows:
            break
        ultimo = rows[-1]["id"]
        st["letti"] += len(rows)
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
        del rows
    return st
