"""Dati mancanti letti dal testo dell'annuncio (titolo + descrizione), senza AI e senza inventare.

Si riempiono SOLO i campi vuoti (alimentazione, anno, km) e solo con indicazioni chiare nel testo:
parola esplicita ("diesel", "benzina", "gpl"…) o sigla di motore inequivocabile (TDI, Multijet, TSI…);
anno con una parola vicina ("anno 2014", "immatricolata 2014", "03/2014"); km scritti come km.
Se nel testo ci sono indicazioni in conflitto (es. due alimentazioni diverse) il campo resta vuoto.
"""
from __future__ import annotations

import datetime
import os
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
# 17/11: anche "immatricolata nel 2015", "anno di immatricolazione 2015", "immatricolazione: marzo 2015"
_MESI = r"(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)"
RE_ANNO = re.compile(r"(?:anno(?:\s+di\s+immatricolazione)?|immatricolat[ao]|immatricolazione|del|dal|registrazione|"
                     r"prima\s+immatricolazione)\s*:?\s*(?:(?:nel|nell'|a|ad|il|in)\s*)?(?:" + _MESI + r"\s+(?:del\s+)?)?"
                     r"(?:(?:0?[1-9]|1[0-2])\s*[/.-]\s*)?((?:19[89]|20[0-3])\d)\b", re.I)
RE_MESE_ANNO = re.compile(r"\b(?:0?[1-9]|1[0-2])\s*/\s*((?:19[89]|20[0-3])\d)\b")
RE_KM = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.\s]\d{3})+|\d{2,6})\s*(mila|k)?\s*(?:km|chilometri|kilometri)\b", re.I)
RE_KM_DOPO = re.compile(r"\b(?:km|chilometri|kilometri|percorrenza|chilometraggio|kilometraggio)\s*(?:percorsi|reali|certificati|totali)?\s*:?\s*"
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


# 17/11 (giudice): "tagliando del 2023", "revisione del 2024", "gomme del 2022", "distribuzione fatta 03/2021" sono date
# di lavori, non l'anno dell'auto: si ignorano (con "immatricolata"/"immatricolazione" vicino l'anno vale sempre).
_ANNO_LAVORI = re.compile(r"tagliand|revision|gomm|pneumatic|distribuzion|cinghi|frizion|batteri|bollo|assicuraz|"
                          r"garanzi|fattur|ricevut|olio|freni|dischi|pastiglie|manutenzion|service|sostituit|cambiat|"
                          r"rifatt|eseguit|acquistat|comprat", re.I)


def _anno_dell_auto(testo: str, m: re.Match) -> bool:
    if re.match(r"\s*(?:anno\s+di\s+)?(?:prima\s+)?immatricol", m.group(0), re.I):
        return True
    prima = testo[max(0, m.start() - 40):m.start()]
    tagli = [x.end() for x in re.finditer(r"[.,;:!?\n()]|\s-\s", prima)]
    if tagli:
        prima = prima[tagli[-1]:]
    return not _ANNO_LAVORI.search(prima + " " + m.group(0))


def anno(testo: str) -> int | None:
    oggi = datetime.date.today().year
    valori = {int(m.group(1)) for rx in (RE_ANNO, RE_MESE_ANNO) for m in rx.finditer(testo) if _anno_dell_auto(testo, m)}
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


# 17/11 (giudice, "non gonfiare i prezzi"): per SOSTITUIRE km sbagliati (uguali all'anno) servono km scritti come
# percorrenza dell'auto, non numeri qualsiasi seguiti da "km" ("tagliando ogni 15.000 km", "distribuzione rifatta a
# 90.000 km", "garanzia 10.000 km"): km più bassi del vero alzano il valore di mercato e quindi il margine.
# Si accettano SOLO frasi chiare ("km 85.000", "km percorsi 85.000", "85.000 km percorsi/reali/certificati",
# "chilometraggio: 85.000", "percorrenza 85.000 km") e si scartano se vicino c'è un lavoro, un intervallo o una garanzia.
_NUM = r"(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*(mila|k)?"
_RE_KM_CHIARI = [
    re.compile(r"\b(?:km|chilometri|kilometri)\s*(?:percorsi|reali|certificati|totali|effettivi)?\s*:?\s*" + _NUM + r"\b", re.I),
    re.compile(r"(?<![\d.,])" + _NUM + r"\s*(?:km|chilometri|kilometri)\s+(?:percorsi|reali|certificati|totali|effettivi)\b", re.I),
    re.compile(r"\b(?:chilometraggio|kilometraggio|percorrenza)(?:\s+(?:reale|certificat[oa]|totale))?\s*:?\s*" + _NUM
               + r"(?:\s*(?:km|chilometri|kilometri))?\b", re.I),
]
_KM_CONTESTO_NO = re.compile(
    r"tagliand|distribuzion|garanzi|revision|frizion|gomm|pneumatic|cinghi|\bolio\b|\bogni\b|annu[aie]|all'\s*anno|"
    r"\bmedia\b|prossim|entro|mancano|sostituit|cambiat|rifatt|eseguit|manutenzion|service|autonomia|\bfa\b|"
    r"\bdal(?:l')?\s*(?:ultimo|tagliando|cambio)|batteri|turbin|freni|dischi|pastiglie|kit\b|motore\s+nuovo|"
    r"\bmotore\s+(?:con|a|da)\b|ricondizion|rigenerat|al\s+mese|mensil|settiman|al\s+giorno", re.I)
# "a 90.000 km", "ai 90.000", "dopo 90.000": un evento a un certo km, non la percorrenza di oggi
_KM_A_PRIMA = re.compile(r"\b(?:a|ai|al|agli|dopo|verso|circa\s+a|fino\s+a|fino\s+ai|da|di)\s*(?:km\s*)?$", re.I)


KM_MINIMO_AL_POSTO_ANNO = 5000
# segno in field_origin: km riempiti dal testo col filtro prudente (i 'dal_testo' senza segno vengono dalla vecchia
# chilometri() e il recupero non li riprende)
SEGNO_KM_PRUDENTI = "km_testo_prudente"


def km_percorsi_chiari(testo: str) -> int | None:
    """Km dell'auto scritti come percorrenza in modo inequivocabile; None se assenti, dubbi o se ci sono valori diversi."""
    valori = set()
    for rx in _RE_KM_CHIARI:
        for m in rx.finditer(testo):
            v = _km(m.group(1), m.group(2))
            if not v or v < 1000:
                continue
            # la frase del numero: dall'ultimo segno di punteggiatura prima, fino al primo dopo
            prima = testo[max(0, m.start() - 60):m.start()]
            tagli = [x.end() for x in re.finditer(r"[.,;:!?\n()]|\s-\s", prima)]
            if tagli:
                prima = prima[tagli[-1]:]
            dopo = re.split(r"[.,;!?\n()]", testo[m.end():m.end() + 40])[0]
            frase = prima + " " + m.group(0) + " " + dopo
            if _KM_CONTESTO_NO.search(frase) or _KM_A_PRIMA.search(prima):
                continue
            valori.add(v)
    return valori.pop() if len(valori) == 1 else None


def km_dal_testo_prudente(km_dichiarati, anno_auto, km_testo) -> int | None:
    """Km del testo usabili al posto dei dichiarati SOLO se non abbassano il dato vero: o i dichiarati sono palesemente
    l'anno (dato sbagliato del portale), o i km del testo sono >= dei dichiarati. In dubbio None (si tiene il dichiarato)."""
    if not km_testo or km_come_anno(km_testo, anno_auto):
        return None
    if km_come_anno(km_dichiarati, anno_auto):
        # minimo plausibile: "5.000 km" al posto dell'anno è più spesso un intervallo che la percorrenza
        return km_testo if int(km_testo) >= KM_MINIMO_AL_POSTO_ANNO else None
    try:
        return km_testo if km_dichiarati is not None and int(km_testo) >= int(km_dichiarati) else None
    except (TypeError, ValueError):
        return None


def km_come_anno(km, anno_auto) -> bool:
    """Km uguali (±3) all'anno dell'auto ("2017" km per un'auto del 2017): il portale ha messo l'anno nel campo dei km.
    Stessa regola di policy_v2.km_come_anno, che manda l'auto in dati_insufficienti."""
    try:
        return km is not None and anno_auto is not None and 1990 <= int(km) <= 2035 and abs(int(km) - int(anno_auto)) <= 3
    except (TypeError, ValueError):
        return False


# Bandiera: sostituzione dei km uguali all'anno con i km chiari del testo (filtro prudente qui sopra).
# DAL_TESTO_KM_ANNO=0 la spegne (i km restano come sono e l'auto resta in dati_insufficienti, come prima del 17/11).
KM_ANNO_DAL_TESTO = os.environ.get("DAL_TESTO_KM_ANNO", "1") == "1"


def completa(campi: dict, titolo: str | None, descrizione: str | None) -> dict:
    """Ritorna SOLO i campi mancanti trovati nel testo: {"fuel": ..., "year": ..., "mileage_km": ...}.
    17/11: i km "uguali all'anno" (dato sbagliato del portale) contano come mancanti: si sostituiscono SOLO con km
    scritti chiaramente nel testo (un solo valore, non a sua volta uguale all'anno); altrimenti restano come sono."""
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
    anno_auto = campi.get("year") or out.get("year")
    if campi.get("mileage_km") in (None, ""):
        # 17/11 (giudice): anche per i km vuoti solo la percorrenza chiara (chilometri() prendeva "tagliando ogni
        # 15.000 km" -> 15.000, "distribuzione fatta a 90.000 km" -> 90.000: margine gonfiato)
        k = km_percorsi_chiari(testo)
        if k and not km_come_anno(k, anno_auto):
            out["mileage_km"] = k
    elif KM_ANNO_DAL_TESTO and km_come_anno(campi.get("mileage_km"), anno_auto):
        # km uguali all'anno: si sostituiscono SOLO con km di percorrenza chiari nel testo (filtro dei contesti)
        k = km_dal_testo_prudente(campi.get("mileage_km"), anno_auto, km_percorsi_chiari(testo))
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
    mai nel mercato), a pezzi da 3.000 per la memoria; si legge anche la versione dell'annuncio.
    17/11: anche gli annunci con i km uguali all'anno (dato sbagliato del portale: l'auto va in dati_insufficienti e
    come confronto viene scartata), una volta sola (segno 'km_anno_letto'); si corregge solo con km chiari nel testo."""
    import json
    import os
    tetto = int(os.environ.get("DAL_TESTO_MAX", "60000"))
    st = {"letti": 0, "fuel": 0, "year": 0, "mileage_km": 0, "gearbox": 0, "power_kw": 0, "aggiornati": 0}
    ultimo = 0
    while st["letti"] < tetto:
        rows = conn.execute(
            """SELECT id, title, description, version_raw, fuel, year, mileage_km, gearbox, power_kw, field_origin FROM listings
               WHERE status='attivo'
                 AND (((fuel IS NULL OR year IS NULL OR mileage_km IS NULL OR gearbox IS NULL OR power_kw IS NULL)
                       AND NOT (coalesce(field_origin, '{}'::jsonb) ? 'testo_letto_v2'))
                      OR (%s AND mileage_km BETWEEN 1990 AND 2035 AND year IS NOT NULL AND abs(mileage_km - year) <= 3
                          AND NOT (coalesce(field_origin, '{}'::jsonb) ? 'km_anno_letto')))
                 AND (description IS NOT NULL OR title IS NOT NULL OR version_raw IS NOT NULL) AND id > %s
               ORDER BY id LIMIT 3000""", (KM_ANNO_DAL_TESTO, ultimo)).fetchall()
        if not rows:
            break
        ultimo = rows[-1]["id"]
        st["letti"] += len(rows)
        for r in rows:
            nuovi = completa(r, r["title"], r["description"])
            orig = r["field_origin"] if isinstance(r["field_origin"], dict) else json.loads(r["field_origin"] or "{}")
            orig["testo_letto_v2"] = 1                 # già letto: al giro dopo si passa agli annunci successivi
            if KM_ANNO_DAL_TESTO and km_come_anno(r["mileage_km"], r["year"]):
                orig["km_anno_letto"] = 1              # 17/11: km uguali all'anno, riletti una volta sola
            if not nuovi:
                conn.execute("UPDATE listings SET field_origin=%s WHERE id=%s", (json.dumps(orig), r["id"]))
                continue
            orig.update({k: "dal_testo" for k in nuovi})
            if "mileage_km" in nuovi:
                orig[SEGNO_KM_PRUDENTI] = 1            # km letti col filtro prudente (vedi recupero)
            sets = ", ".join(f"{k}=%({k})s" for k in nuovi)
            conn.execute(f"UPDATE listings SET {sets}, field_origin=%(fo)s WHERE id=%(id)s",
                         {**nuovi, "fo": json.dumps(orig), "id": r["id"]})
            for k in nuovi:
                st[k] += 1
            st["aggiornati"] += 1
        conn.commit()
        del rows
    return st
