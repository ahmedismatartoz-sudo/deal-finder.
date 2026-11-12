"""Trazione e optional importanti nella valutazione (12/11/2026 sera, margini fase 2 punto 4).

Fonte: `listings.dati_auto` (scheda tecnica, migrazione 046: parte "ricerca" e parte "pagina"), letta SOLO per trazione
e quattro bandierine calcolate nel database (niente JSON intero in memoria); se la colonna non c'è o manca il dato, il
testo dell'annuncio (versione e titolo) come prima.

- trazione_di(x): "4x4", "2wd" o None;
- trazione a livelli (regole.Trazione, usata da policy_v2.seleziona): sceglie i confronti, non cambia prezzi;
- misura(conn) (dal lavoro CALIBRA): premio di prezzo di 4x4, pelle, navigatore e tetto dentro le celle marca/modello/
  carburante/anno (differenza media del log-prezzo con e senza, pesata, con errore standard);
- fattore(t, c, misura, regole): aggiustamento del prezzo di un confronto per le dotazioni NOTE su entrambe le auto,
  solo con premio misurato (regole.Dotazioni, spento di default).
Mai dati del venditore.
"""
from __future__ import annotations

import logging
import math
import re

from .. import regole as RG

log = logging.getLogger("dotazioni")

RE_4X4 = re.compile(r"(?i)\b(4x4|4wd|awd|xdrive\w*|quattro|4motion|q4|all4|4matic|integrale|all ?wheel)\b")
RE_2WD = re.compile(r"(?i)\b(sdrive\w*|2wd|fwd|rwd|trazione anteriore|trazione posteriore)\b")
VOCI = ("pelle", "navigatore", "tetto")

_TRAZ_SQL = "COALESCE(l.dati_auto->'pagina'->>'trazione', l.dati_auto->'ricerca'->>'trazione')"
_OPT_SQL = ("NULLIF(concat_ws(' ', l.dati_auto->'pagina'->>'optional', l.dati_auto->'ricerca'->>'optional'), '')")
_FLAG_SQL = {"pelle": r"(?i)\m(pelle|leather|cuoio)",
             "navigatore": r"(?i)(navigat|\mnavi\M|\mgps\M|sistema di navigazione)",
             "tetto": r"(?i)(tetto (apribile|panoramico|in vetro|elettrico)|sunroof|panoram|schiebedach|\mtetto\M)"}
_RIGHE_SQL = ("SELECT l.id, " + _TRAZ_SQL + " AS trazione_dati, (" + _OPT_SQL + ") IS NOT NULL AS opt_noti, "
              + ", ".join(f"COALESCE(({_OPT_SQL}) ~ '{rx}', false) AS {k}" for k, rx in _FLAG_SQL.items())
              + " FROM listings l WHERE l.dati_auto IS NOT NULL AND ")
IDS_SQL = _RIGHE_SQL + "l.id = ANY(%s)"


def _ha_colonna(conn) -> bool:
    try:
        from ..db import ha_colonna
        return ha_colonna(conn, "dati_auto")
    except Exception:
        return False


def _righe(conn, sql: str, args) -> dict:
    if not _ha_colonna(conn):
        return {}
    try:
        righe = conn.execute(sql, args).fetchall()
    except Exception as e:                 # mai bloccare la valutazione per questo
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("DOTAZIONI non lette: %s", str(e)[:150])
        return {}
    out = {}
    for r in righe:
        out[r["id"]] = {"trazione_dati": r.get("trazione_dati"),
                        "opt": ({k: bool(r.get(k)) for k in VOCI} if r.get("opt_noti") else None)}
    return out


def carica_mercato(conn, mercato: list[dict]) -> dict:
    """{id: {"trazione_dati", "opt"}} dei SOLI confronti già caricati (controllo del giudice M4: prima tutto il gruppo
    attivo in Italia)."""
    return carica_ids(conn, [m["id"] for m in mercato if m.get("id") is not None])


def carica_ids(conn, ids: list) -> dict:
    return _righe(conn, IDS_SQL, (list(ids),)) if ids else {}


def arricchisci(righe: list[dict], mappa: dict) -> list[dict]:
    """Aggiunge trazione_dati e opt alle righe (confronti o auto) che li hanno nella mappa (le altre invariate)."""
    if not mappa:
        return righe
    return [dict(r, **mappa[r.get("id")]) if r.get("id") in mappa else r for r in righe]


def trazione_di(x: dict) -> str | None:
    """"4x4", "2wd" o None: prima la scheda tecnica (dati_auto), poi versione e titolo."""
    d = str(x.get("trazione_dati") or "").lower()
    if d:
        if re.search(r"integrale|4x4|awd|4wd|all.?wheel|quattro|4motion|xdrive|4matic", d):
            return "4x4"
        if re.search(r"anterior|posterior|front|rear|fwd|rwd", d):
            return "2wd"
    testo = f"{x.get('version_raw') or ''} {x.get('title') or ''}"
    if RE_4X4.search(testo):
        return "4x4"
    if RE_2WD.search(testo):
        return "2wd"
    return None


def per_trazione(t: dict, validi: list[dict], n_minimo: int, r: RG.Regole) -> tuple[list, list, str | None]:
    """Trazione a livelli: (confronti tenuti, [(confronto, motivo)] scartati, nota per le ipotesi o None)."""
    if not r.trazione.attiva or not validi:
        return validi, [], None
    tt = trazione_di(t)
    quattro = [c for c in validi if trazione_di(c) == "4x4"]
    altri = [c for c in validi if trazione_di(c) != "4x4"]
    if tt == "4x4":
        if len(quattro) >= n_minimo and altri:
            return quattro, [(c, "trazione diversa (non 4x4)") for c in altri], None
        if altri:
            return validi, [], f"auto 4x4 con solo {len(quattro)} confronti 4x4: usati anche quelli a 2 ruote"
        return validi, [], None
    if not quattro:
        return validi, [], None
    if tt is None and len(quattro) / len(validi) >= r.trazione.maggioranza:
        return validi, [], None            # modello quasi sempre 4x4 e l'annuncio non lo scrive: nessun filtro
    if len(altri) >= n_minimo:
        return altri, [(c, "trazione diversa (4x4)") for c in quattro], None
    return validi, [], None


# ---------------------------------------------------------------- misura dei premi (lavoro CALIBRA)

_MISURA_SQL = """
WITH a AS (
  SELECT l.make, l.model, l.fuel, l.year,
         ln(l.price_eur::float) + {km_rate} * l.mileage_km / 10000.0 AS lp,
         {flag} AS f
  FROM listings l
  WHERE l.status = 'attivo' AND l.price_eur BETWEEN 1000 AND 200000 AND l.year IS NOT NULL
    AND l.mileage_km IS NOT NULL AND l.make IS NOT NULL AND l.model IS NOT NULL AND {noti}),
c AS (
  SELECT avg(lp) FILTER (WHERE f) AS m1, count(*) FILTER (WHERE f) AS n1,
         avg(lp) FILTER (WHERE NOT f) AS m0, count(*) FILTER (WHERE NOT f) AS n0
  FROM a GROUP BY make, model, fuel, year)
SELECT m1 - m0 AS d, n1, n0 FROM c WHERE n1 > 0 AND n0 > 0"""


def _flag_4x4() -> str:
    return ("(COALESCE(" + _TRAZ_SQL + ", '') ~* 'integrale|4x4|awd|4wd|all.?wheel' OR "
            "concat_ws(' ', l.version_raw, l.title) ~* '\\m(4x4|4wd|awd|xdrive\\w*|quattro|4motion|q4|all4|4matic|integrale)\\M')")


def premio(celle: list[tuple]) -> dict | None:
    """Da [(differenza log-prezzo, n con, n senza)] per cella: premio medio pesato (peso n1·n0/(n1+n0)), errore
    standard, numeri."""
    ok = [(float(d), float(n1) * float(n0) / (float(n1) + float(n0)), int(n1), int(n0)) for d, n1, n0 in celle
          if d is not None and n1 and n0]
    if not ok:
        return None
    W = sum(w for _, w, _, _ in ok)
    m = sum(d * w for d, w, _, _ in ok) / W
    var = sum(w * (d - m) ** 2 for d, w, _, _ in ok) / W
    se = math.sqrt(var / len(ok)) if len(ok) > 1 else None
    return {"premio": round(m, 4), "se": round(se, 4) if se is not None else None, "celle": len(ok),
            "n_con": sum(n1 for _, _, n1, _ in ok), "n_senza": sum(n0 for _, _, _, n0 in ok)}


def misura(conn, r: RG.Regole | None = None) -> dict:
    """{"4x4": {...}, "pelle": {...}, "navigatore": {...}, "tetto": {...}} (solo le voci con almeno una cella)."""
    if not _ha_colonna(conn):
        return {}
    r = r or RG.regole()
    km_rate = -math.log(1 - r.correzione.svalutazione_10km)
    out = {}
    voci = {"4x4": (_flag_4x4(), "true")}
    for k, rx in _FLAG_SQL.items():
        voci[k] = (f"COALESCE(({_OPT_SQL}) ~ '{rx}', false)", f"({_OPT_SQL}) IS NOT NULL")
    for nome, (flag, noti) in voci.items():
        try:
            righe = conn.execute(_MISURA_SQL.format(km_rate=km_rate, flag=flag, noti=noti)).fetchall()
        except Exception as e:
            conn.rollback()
            log.warning("DOTAZIONI misura %s non riuscita: %s", nome, str(e)[:150])
            continue
        p = premio([(x["d"], x["n1"], x["n0"]) for x in righe])
        if p:
            out[nome] = p
    return out


def misurato(m: dict | None, r: RG.Regole) -> float | None:
    """Premio in log usabile (abbastanza annunci e significativo) oppure None."""
    d = r.dotazioni
    if not m or m.get("premio") is None or not m.get("se") or (m.get("n_con") or 0) < d.n_min:
        return None
    if abs(m["premio"]) < d.z_min * m["se"]:
        return None
    return max(-d.tetto_voce, min(d.tetto_voce, float(m["premio"])))


def fattore(t: dict, c: dict, misure: dict | None, r: RG.Regole) -> float:
    """Fattore per il prezzo del confronto c verso le dotazioni dell'auto t (1,0 se spento o se non si sa)."""
    if not r.dotazioni.aggiusta or not misure:
        return 1.0
    tot = 0.0
    p4 = misurato(misure.get("4x4"), r)
    if p4 is not None:
        a, b = trazione_di(t), trazione_di(c)
        if a and b and a != b:
            tot += p4 * ((a == "4x4") - (b == "4x4"))
    ot, oc = t.get("opt"), c.get("opt")
    if isinstance(ot, dict) and isinstance(oc, dict):
        for k in VOCI:
            p = misurato(misure.get(k), r)
            if p is not None and bool(ot.get(k)) != bool(oc.get(k)):
                tot += p * (int(bool(ot.get(k))) - int(bool(oc.get(k))))
    lim = math.log(1 + r.dotazioni.tetto_totale)
    return math.exp(max(-lim, min(lim, tot)))
