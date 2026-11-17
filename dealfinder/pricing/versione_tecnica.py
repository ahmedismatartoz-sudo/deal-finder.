"""Versione TECNICA dei confronti (13/11, ramo piu-auto): "versioni molto diverse" solo se la differenza è reale.

Il controllo AI (Jev, ora Claude) a volte dice che i confronti sono "un'altra versione" (versione_ok < 0,25) solo
perché il nome dell'allestimento è diverso (Pop / Lounge, Business / Executive, Trendline / Highline): per il cliente
non è un motivo per togliere l'auto. Qui si guarda la SCHEDA TECNICA, con funzioni pure:

    potenza (kW della colonna o "116 CV"/"85 kW" scritti nella versione)    differenza oltre il 10% = motore diverso
    cilindrata (scheda tecnica dati_auto, oppure "1.2"/"2.0" nella versione)  oltre 0,15 litri = motore diverso
    alimentazione (sigle diesel/ibrido nella versione: TDI, Multijet, HDi, Hybrid…)  diversa = motore diverso
    anno (generazione)                                                     oltre 4 anni = generazione diversa

Le parole degli allestimenti (pricing/allestimento.py) e i rumori come "S tronic" non contano: si tolgono prima di
leggere le sigle. Verdetto:
    "uguale"  almeno CONFRONTI_MIN (3) confronti verificabili (potenza o cilindrata note da entrambe le parti) e
              almeno il 70% compatibili → la differenza è solo nel nome: l'auto NON si toglie;
    "diversa" verificabili ma sotto il 70% → resta fuori come prima;
    None      non si può dire (dati mancanti) → vale il giudizio dell'AI, resta fuori come prima (prudente).
"""
from __future__ import annotations

import re

CONFRONTI_MIN = 3
QUOTA_UGUALI = 1.0            # 13/11 (giudice A3): TUTTI i confronti verificabili compatibili
QUOTA_VERIFICABILI = 0.6      # e verificabili almeno il 60% dei confronti
TOL_KW = 0.10
TOL_LITRI = 0.15
ANNI_GENERAZIONE = 4

_DIESEL = re.compile(r"(?i)\b(tdi|jtd\w*|multijet|mjet|cdi|hdi|bluehdi|dci|crdi|tdci|d4d|d-4d|ecoblue|"
                     r"blue\s?motion\s?tdi|\d{3}d|diesel|dsl|sdi|ddis|cdti|i-dtec|skyactiv-d|jtdm)\b")
_IBRIDO = re.compile(r"(?i)\b(hybrid|ibrid[ao]|phev|plug[\s-]?in|e-?tense|full\s?hybrid|mhev|mild)\b")
_LITRI = re.compile(r"(?<![\d.,])([0-6][.,]\d)(?![\d.,])(?!\s*(?:kw|cv|hp|ps)\b)", re.I)
# cilindrata in cm³: "1598 cm3", "1.598 cc", "1598cm³" (prima "1.598" si leggeva 1,5 litri)
_CC = re.compile(r"(?<![\d.,])(\d[.,]?\d{3})\s*(?:cm3|cm³|cc|ccm)\b", re.I)
_CV = re.compile(r"(\d{2,3})\s*(?:cv|hp|ps)\b", re.I)
_KW = re.compile(r"(\d{2,3})\s*kw\b", re.I)


def _pulisci(testo: str) -> str:
    t = str(testo or "")
    try:
        from .allestimento import _RUMORE
        t = _RUMORE.sub(" ", t)
    except Exception:
        pass
    return t


def kw_da_testo(testo: str | None) -> float | None:
    t = _pulisci(testo or "")
    m = _KW.search(t)
    if m:
        return float(m.group(1))
    m = _CV.search(t)
    return round(float(m.group(1)) * 0.7355, 1) if m else None


def litri_da_testo(testo: str | None) -> float | None:
    t = _pulisci(testo or "")
    m = _CC.search(t)
    if m:
        cc = int(re.sub(r"[.,]", "", m.group(1)))
        return round(cc / 1000, 1) if 600 <= cc <= 6900 else None
    m = _LITRI.search(t)
    if not m:
        return None
    x = float(m.group(1).replace(",", "."))
    return x if 0.6 <= x <= 6.9 else None


def tipo_motore(testo: str | None, fuel: str | None = None) -> str | None:
    """'diesel' | 'ibrido' | 'benzina' (dalla colonna fuel o dalle sigle della versione) | None."""
    f = str(fuel or "").lower()
    if f:
        if "diesel" in f or "gasolio" in f:
            return "diesel"
        if "ibrid" in f or "hybrid" in f:
            return "ibrido"
        if "benzin" in f or "gpl" in f or "metano" in f:
            return "benzina"
    t = _pulisci(testo or "")
    if _IBRIDO.search(t):
        return "ibrido"
    if _DIESEL.search(t):
        return "diesel"
    return None


def cilindrata_cc(v) -> int | None:
    """Cilindrata della scheda tecnica (dati_auto): "1598 cm3", "1.598", 1598, "1,6 l" -> cm³ (core/dati_auto)."""
    if v in (None, ""):
        return None
    try:
        from ..core.dati_auto import norm_cilindrata
        return norm_cilindrata(re.sub(r"(?i)\s*(cm3|cm³|cc|ccm)\s*$", "", str(v).strip()))
    except Exception:
        return None


def _num(v) -> float | None:
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def scheda(auto: dict) -> dict:
    """kW, litri, motore e anno dell'auto in vendita (colonne, scheda tecnica dati_auto, testo della versione)."""
    testo = f"{auto.get('version_raw') or ''} {auto.get('title') or ''}"
    kw = _num(auto.get("power_kw")) or kw_da_testo(testo)
    cil = _num(auto.get("cilindrata")) or cilindrata_cc(auto.get("cilindrata_testo"))
    litri = round(cil / 1000, 1) if cil and cil > 400 else litri_da_testo(auto.get("version_raw")) or litri_da_testo(auto.get("title"))
    return {"kw": kw, "litri": litri, "motore": tipo_motore(testo, auto.get("fuel")), "anno": _num(auto.get("year"))}


def compatibile(t: dict, c: dict) -> tuple[bool | None, str]:
    """(True/False, motivo) se il confronto è verificabile; (None, '') se non si può dire."""
    v = str(c.get("versione") or "")
    kw_c = _num(c.get("kw")) or kw_da_testo(v)
    litri_c = litri_da_testo(v)
    mot_c = tipo_motore(v)
    anno_c = _num(c.get("anno"))
    verificabile = False
    if t.get("kw") and kw_c:
        verificabile = True
        if abs(kw_c - t["kw"]) > TOL_KW * t["kw"]:
            return False, "potenza"
    if t.get("litri") and litri_c:
        verificabile = True
        if abs(litri_c - t["litri"]) > TOL_LITRI + 1e-9:
            return False, "cilindrata"
    if t.get("motore") and mot_c and mot_c != t["motore"]:
        return False, "alimentazione"
    if t.get("anno") and anno_c and abs(anno_c - t["anno"]) > ANNI_GENERAZIONE:
        return False, "generazione"
    return (True, "") if verificabile else (None, "")


def verdetto(auto: dict, confronti: list[dict] | None) -> str | None:
    """'uguale' | 'diversa' | None (vedi il commento in testa)."""
    t = scheda(auto)
    if not (t["kw"] or t["litri"]):
        return None
    esiti = [compatibile(t, c)[0] for c in (confronti or []) if isinstance(c, dict)]
    ver = [e for e in esiti if e is not None]
    if len(ver) < CONFRONTI_MIN or len(ver) < QUOTA_VERIFICABILI * len(esiti):
        return None                                  # troppo pochi verificabili: vale il giudizio dell'AI
    return "uguale" if sum(ver) / len(ver) >= QUOTA_UGUALI - 1e-9 else "diversa"


# ------------------------------------------------------------------ scheda tecnica per la scelta delle gemelle
# 17/11 (notte, precisione di M): policy_v2.seleziona confrontava le auto con le sole colonne power_kw e gearbox. Quando
# una delle due manca (Subito, annunci scritti a mano) un'auto base passava come gemella di una versione top e
# viceversa (Golf 1.6 TDI con Golf GTD, Audi A3 con S3/RS3, Classe A 180 con A 45 AMG, cambio manuale con DSG). Qui si
# leggono dal testo GIÀ salvato (version_raw/titolo) le stesse informazioni: kW/CV scritti, cilindrata, cambio
# automatico e la "variante sportiva" (sigla che cambia motore e prezzo dentro lo stesso modello). Funzioni pure.

_RE_AUTOMATICO = re.compile(
    r"(?i)(?<![\w-])(?:automatic[oa]?|automatik|aut\.|autom\.|cambio\s+automatico|dsg\d?|s[\s-]?tronic|stronic|"
    r"tiptronic|steptronic|multitronic|powershift|x?tronic|e-?cvt|cvt|pdk|dct\d?|\d?dct|edc\d*|eat\s?\d|"
    r"\d?g[\s-]?tronic|\d{1,2}g[\s-]?dct|speedshift|selespeed|dualogic|easytronic|sensodrive|geartronic|sportronic|"
    r"m-?mt|mmt|amt|dkg|at\s?[6-9]|[6-9]\s?at|at)(?![\w-])")
_RE_MANUALE = re.compile(r"(?i)(?<![\w-])(?:manuale|cambio\s+manuale|[56]\s?marce)(?![\w-])")


def cambio_da_testo(testo: str | None) -> str | None:
    """'automatico' (anche robotizzato: DSG, S tronic, Dualogic, M-MT…) | 'manuale' | None se il testo non lo dice
    o dice entrambe le cose."""
    t = str(testo or "")
    a, m = bool(_RE_AUTOMATICO.search(t)), bool(_RE_MANUALE.search(t))
    return "automatico" if a and not m else "manuale" if m and not a else None


# Varianti sportive per marca: (regex, sigla). Per queste marche un annuncio senza sigla è la versione "normale".
_NUM_AMG = r"(?<![\d.,])(35|43|45|53|55|63|65)(?![\d.,])(?!\s*(?:kw|cv|hp|ps|mila|k\b))"
_VARIANTI: dict[str, list[tuple[str, str]]] = {
    "volkswagen": [(r"\bgti\b", "gti"), (r"\bgtd\b", "gtd"), (r"\bgte\b", "gte"),
                   (r"\br32\b|(?<![\w-])r(?![\w-])(?![\s-]*(?:line|design|dynamic))", "r")],
    "audi": [(r"\brs\s?[1-7]\b|\brs\s?q\s?[38]\b|\brs\s?e-?tron\b|\btt\s?rs\b|\brs\b(?![\s-]*line)", "rs"),
             (r"\bs[1-8]\b|\bsq[2-8]\b|\btts\b", "s")],
    "bmw": [(r"\bm[2-8]\b|\bx[3-6]\s?m\b(?![\s-]*sport)", "m"), (r"\bm\d{2,3}[id]?\b", "m_performance")],
    "skoda": [(r"\bv?rs\b(?![\s-]*line)", "rs")],
    "ford": [(r"\bst\b(?![\s-]*line)", "st"), (r"\brs\b(?![\s-]*line)", "rs"), (r"\braptor\b", "raptor")],
    "renault": [(r"(?<![\w.])r\.?s\.?(?![\w.])(?![\s.-]*line)|\btrophy\b", "rs")],
    "peugeot": [(r"\bgti\b", "gti")],
    "honda": [(r"\btype[\s-]?r\b", "type_r")],
    "hyundai": [(r"(?<![\w-])n(?![\w-])(?![\s-]*line)", "n")],
    "toyota": [(r"\bgr\b(?![\s-]*sport)|\bgr(?:yaris|86|supra)\b", "gr")],
    "opel": [(r"\bopc\b", "opc")],
    "alfa-romeo": [(r"\bquadrifoglio\b|\bqv\b", "qv")],
    "fiat": [(r"\babarth\b", "abarth")],
    "subaru": [(r"\bsti\b", "sti")],
    "seat": [(r"\bcupra\b", "cupra")],
    "porsche": [(r"\bgt[234]\s?rs\b", "gt_rs"), (r"\bgt[234]\b", "gt"), (r"\bturbo\s?s\b", "turbo_s"),
                (r"\bturbo\b", "turbo"), (r"\bgts\b", "gts")],
    "mini": [(r"\b(?:john\s+cooper\s+works|jcw)\b(?![\s-]*(?:trim|kit|pack|package|allestimento|style))", "jcw")],
}
_RX_VARIANTI = {k: [(re.compile(rx, re.I), s) for rx, s in v] for k, v in _VARIANTI.items()}
_RE_BMW_NORMALE = re.compile(r"(?i)\b(?:[1-8]\d{2}|[sx]?drive\s?\d{2})[ide]\b")
_RE_AMG = re.compile(r"(?i)\bamg\b(?![\s-]*line)")
_RE_NUM_AMG = re.compile(_NUM_AMG, re.I)


def _marca_norm(make) -> str:
    try:
        from .allestimento import _marca
        return _marca(make)
    except Exception:
        return str(make or "").strip().lower().replace(" ", "-")


def variante_sportiva(make, model, testo) -> str | None:
    """Sigla della variante sportiva ('gti', 'rs', 's', 'm', 'amg45'…), 'normale' per le marche note senza sigla,
    None per le marche senza elenco (non si può dire). Prima vale la sigla più "alta" dell'elenco della marca."""
    marca = _marca_norm(make)
    t = _pulisci(f"{model or ''} {testo or ''}")
    if marca in ("mercedes", "mercedes-benz", "mercedes-amg"):
        if _RE_AMG.search(t) or "mercedes-amg" in str(make or "").lower():
            m = _RE_NUM_AMG.search(t)
            if m:
                return "amg" + m.group(1)
        return "normale"
    voci = _RX_VARIANTI.get(marca)
    if voci is None:
        return None
    for rx, sigla in voci:
        if rx.search(t):
            if sigla == "m" and _RE_BMW_NORMALE.search(t):
                continue                       # "X3 M … xDrive20i": il motore scritto è quello normale
            if sigla == "jcw" and (litri_da_testo(t) or 2.0) < 1.9:
                continue                       # "1.5 C JCW": allestimento JCW su un motore normale
            return sigla
    return "normale"


VERSIONE_SQL = """
SELECT l.id, l.power_kw, l.fuel, l.year, l.version_raw, l.title,
       COALESCE(l.dati_auto->'pagina'->>'cilindrata', l.dati_auto->'ricerca'->>'cilindrata') AS cilindrata_testo,
       vp.dati->'confronti' AS confronti
FROM listings l
JOIN LATERAL (SELECT dati FROM valutazioni_policy v WHERE v.listing_id = l.id ORDER BY v.computed_at DESC LIMIT 1) vp ON true
WHERE l.id = ANY(%s)"""


def leggi(conn, ids: list[int]) -> dict:
    """{listing_id: verdetto} per le sole auto che l'AI ha giudicato "versioni molto diverse" (poche centinaia)."""
    import json
    out = {}
    if not ids:
        return out
    for k in range(0, len(ids), 500):
        for r in conn.execute(VERSIONE_SQL, (ids[k:k + 500],)).fetchall():
            conf = r.get("confronti") or []
            if isinstance(conf, str):
                conf = json.loads(conf)
            out[r["id"]] = verdetto(dict(r), conf)
    return out
