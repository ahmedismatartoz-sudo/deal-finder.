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
