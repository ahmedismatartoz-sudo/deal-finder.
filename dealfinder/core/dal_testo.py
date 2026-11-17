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


# 17/11 (notte-06): km che NON sono la percorrenza dell'auto ("tagliando fatto a 90.000 km", "distribuzione cambiata a
# 120 mila km", "tagliandi ogni 15.000 km", "percorrenza annua 10.000 km", "gomme con 5.000 km", "garanzia 10.000 km",
# "10.000 km fa"): letti come km dell'auto davano km sbagliati (margine gonfiato o "km non credibili" dell'AI).
# Si ignorano; se resta un solo valore vale quello, altrimenti il campo resta vuoto come prima.
_KM_NON_AUTO_PRIMA = re.compile(
    # lavori fatti A un certo km ("tagliando fatto a 90.000 km", "distribuzione cambiata ai 120 mila"): serve la
    # preposizione ("tagliandata 128.000 km" e "tagliandata con 128.000 km" sono i km dell'auto)
    r"(?:\b(?:tagliand\w*|distribuzion\w*|cinghi\w*|frizion\w*|olio|filtri|freni|dischi|pastiglie|batteria|kit|turbin\w*|"
    r"sostituit\w*|cambiat\w*|rifatt\w*|eseguit\w*|fatt[oaie]|revisionat\w*|manutenzion\w*|service)"
    r"(?:\s+[\w'’/]+){0,3}?\s+(?:a|ai|al|agli|dopo|verso|intorno\s+ai|circa\s+a|fino\s+a|fino\s+ai)"
    # gomme/pneumatici con pochi km
    r"|\b(?:gomme|pneumatic\w*)(?:\s+[\w'’/]+){0,2}?\s+(?:con|da|di|a)"
    # intervalli, medie, scadenze, garanzia
    r"|\b(?:ogni|annu[ae]|annui|media|percorre|prossim[oa]|entro|mancano|garanzia(?:\s+(?:di|per|fino\s+a))?))"
    r"\s*(?:km\s*)?$", re.I)
_KM_NON_AUTO_DOPO = re.compile(r"^\s*(?:km|chilometri|kilometri)?\s*(?:annu\w*|all'\s*anno|l'\s*anno|al\s+mese|fa\b|"
                               r"di\s+garanzia|in\s+garanzia|dall'ultimo|dal\s+tagliando|dal\s+cambio|dalla\s+sostituzione|"
                               r"di\s+autonomia|autonomia)", re.I)


def _km_dell_auto(testo: str, m) -> bool:
    """Vero se il numero di km in testo[ini:fin] è la percorrenza dell'auto e non un km di lavori, intervalli, garanzia,
    gomme ("tagliando ogni 15.000 km", "distribuzione a 120.000 km", "garanzia 10.000 km", "5.000 km fa").
    Riusabile: m è un re.Match oppure una coppia (ini, fin) di posizioni nel testo."""
    ini, fin = (m.start(), m.end()) if hasattr(m, "start") else m
    prima = testo[max(0, ini - 45):ini]
    tagli = [x.end() for x in re.finditer(r"[.,;:!?\n()]|\s-\s", prima)]
    if tagli:
        prima = prima[tagli[-1]:]
    return not (_KM_NON_AUTO_PRIMA.search(prima) or _KM_NON_AUTO_DOPO.match(testo[fin:fin + 30]))


def _tutti_km(testo: str, filtra: bool = True) -> set[int]:
    """Tutti i valori di km scritti nel testo (≥ 1.000); con filtra=True solo quelli dell'auto (_km_dell_auto)."""
    valori = set()
    for rx in (RE_KM, RE_KM_DOPO):
        for m in rx.finditer(testo):
            if (not filtra or _km_dell_auto(testo, m)) and (v := _km(m.group(1), m.group(2))):
                valori.add(v)
    return {v for v in valori if v >= 1000}               # "a 2 km dalla stazione" non sono km dell'auto


def chilometri(testo: str) -> int | None:
    valori = _tutti_km(testo)
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
                 AND NOT (coalesce(field_origin, '{}'::jsonb) ? 'testo_letto_v2')
                 AND (description IS NOT NULL OR title IS NOT NULL OR version_raw IS NOT NULL) AND id > %s
               ORDER BY id LIMIT 3000""", (ultimo,)).fetchall()
        if not rows:
            break
        ultimo = rows[-1]["id"]
        st["letti"] += len(rows)
        for r in rows:
            nuovi = completa(r, r["title"], r["description"])
            orig = r["field_origin"] if isinstance(r["field_origin"], dict) else json.loads(r["field_origin"] or "{}")
            orig["testo_letto_v2"] = 1                 # già letto: al giro dopo si passa agli annunci successivi
            if not nuovi:
                conn.execute("UPDATE listings SET field_origin=%s WHERE id=%s", (json.dumps(orig), r["id"]))
                continue
            orig.update({k: "dal_testo" for k in nuovi})
            if "mileage_km" in nuovi:
                # 17/11 (giudice): si ricorda il valore scritto, così la rilettura sa se lo scraper l'ha poi cambiato
                # (upsert_listing riscrive mileage_km ma non field_origin)
                orig["mileage_km_testo"] = nuovi["mileage_km"]
            sets = ", ".join(f"{k}=%({k})s" for k in nuovi)
            conn.execute(f"UPDATE listings SET {sets}, field_origin=%(fo)s WHERE id=%(id)s",
                         {**nuovi, "fo": json.dumps(orig), "id": r["id"]})
            for k in nuovi:
                st[k] += 1
            st["aggiornati"] += 1
        conn.commit()
        del rows
    st.update(rileggi_km_db(conn, tetto))
    return st


def rileggi_km_db(conn, limite: int = 60000) -> dict:
    """17/11 (notte-06): i km presi dal testo (field_origin mileage_km = 'dal_testo') si rileggono UNA volta con le
    regole nuove (km dei lavori/tagliandi/intervalli ignorati); si rileggono anche gli annunci già letti dal testo con
    km vuoti (prima due valori in conflitto, ora magari uno solo). Segno 'km_riletto_v3' per non rifarlo.
    Regole (17/11, giudice: margini mai gonfiati, in dubbio il valore più prudente):
      - si riscrive SOLO se mileage_km è ancora il valore preso dal testo: field_origin.mileage_km_testo se salvato,
        altrimenti (righe vecchie) il valore deve comparire tra i km scritti nel testo; se lo scraper l'ha cambiato
        (upsert_listing non tocca field_origin) il km resta e non è più 'dal_testo';
      - un solo km dell'auto: si scrive quello;
      - più km dell'auto in conflitto: il km presente non si svuota mai, si tiene il più ALTO (meno margine);
      - nessun km dell'auto e il valore presente è un km di lavori/intervalli: il campo torna vuoto;
      - km vuoto: si riempie solo con un valore unico (come completa).
    L'UPDATE controlla che mileage_km non sia cambiato nel frattempo."""
    import json
    st = {"km_riletti": 0, "km_corretti": 0, "km_tolti": 0, "km_riempiti": 0, "km_dello_scraper": 0}
    ultimo = 0
    while st["km_riletti"] < limite:
        rows = conn.execute(
            """SELECT id, title, description, version_raw, mileage_km, field_origin FROM listings
               WHERE status='attivo' AND NOT (field_origin ? 'km_riletto_v3')
                 AND (field_origin->>'mileage_km' = 'dal_testo'
                      OR (mileage_km IS NULL AND field_origin ? 'testo_letto_v2'))
                 AND id > %s
               ORDER BY id LIMIT 3000""", (ultimo,)).fetchall()
        if not rows:
            break
        ultimo = rows[-1]["id"]
        st["km_riletti"] += len(rows)
        for r in rows:
            testo = f"{r.get('version_raw') or ''}\n{r.get('title') or ''}\n{r.get('description') or ''}"
            orig = r["field_origin"] if isinstance(r["field_origin"], dict) else json.loads(r["field_origin"] or "{}")
            orig["km_riletto_v3"] = 1
            ora = r.get("mileage_km")
            valori = _tutti_km(testo)
            nuovo = ora
            if ora is None:
                if len(valori) == 1:
                    nuovo = next(iter(valori))
                    st["km_riempiti"] += 1
            elif orig.get("mileage_km") != "dal_testo":
                pass                                            # (non dovrebbe capitare: km non dal testo, non si tocca)
            elif (orig.get("mileage_km_testo") is not None and orig["mileage_km_testo"] != ora) or \
                    (orig.get("mileage_km_testo") is None and ora not in _tutti_km(testo, filtra=False)):
                orig.pop("mileage_km", None)                    # km scritto dallo scraper dopo: resta com'è
                orig.pop("mileage_km_testo", None)
                st["km_dello_scraper"] += 1
            elif len(valori) == 1:
                nuovo = next(iter(valori))
            elif len(valori) > 1:
                nuovo = max(valori)                             # in dubbio il più alto: mai un margine gonfiato
            else:
                nuovo = None                                    # era un km di lavori/intervalli, nessun km dell'auto
            if nuovo == ora:
                conn.execute("UPDATE listings SET field_origin=%(fo)s WHERE id=%(id)s",
                             {"fo": json.dumps(orig), "id": r["id"]})
                continue
            if nuovo is None:
                orig.pop("mileage_km", None)
                orig.pop("mileage_km_testo", None)
                st["km_tolti"] += 1
            else:
                orig["mileage_km"] = "dal_testo"
                orig["mileage_km_testo"] = nuovo
                if ora is not None:
                    st["km_corretti"] += 1
            conn.execute("UPDATE listings SET mileage_km=%(km)s, field_origin=%(fo)s "
                         "WHERE id=%(id)s AND mileage_km IS NOT DISTINCT FROM %(ora)s",
                         {"km": nuovo, "fo": json.dumps(orig), "id": r["id"], "ora": ora})
        conn.commit()
        del rows
    return st
