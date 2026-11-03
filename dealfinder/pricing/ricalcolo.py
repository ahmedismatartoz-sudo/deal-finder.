"""Ricalcolo delle auto che il sito può mostrare (cron, voce "ricalcolo" di COLLECT_PRIMA).

    python -m dealfinder.pricing.ricalcolo

Dal 4/11 qui c'è solo la SCELTA delle auto da rivalutare (esegui: auto approfondite + sezione economica;
utilitarie: tutti i privati sani della zona) e cosa pubblicare. Il calcolo e la scrittura sono UNA sola funzione per
tutti, `pricing/valutazione.valuta_e_scrivi` (stesse colonne, stesso telefono, stessi confronti, stessa guardia sulle
scritture). Tolti: taratura (correggeva verso i prezzi CHIESTI), tetto manuale della rivendita, simulazioni con
regole spente (gemella, commercianti, soglie), auto "top" sotto il margine minimo del cliente.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

from .. import regole as RG
from . import policy_v2 as P

log = logging.getLogger("policy")

TARGET_COLS = ("l.id, l.source, l.url, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox, "
               "l.power_kw, l.price_eur, l.price_flags, l.seller_type, l.damage_class, l.damage_items, l.vehicle_id, "
               "l.vehicle_identity, l.stage_reason")
FIN_SQL = (r"COALESCE(description ~* '(solo|vincolat\w*|valid\w*|previo|tramite|con)\s+(a\s+|al\s+|il\s+|un\s+)?"
           r"finanziament', false) AS finanziamento")
# Gemelle: solo annunci attivi (prezzi chiesti). Le auto vendute entrano nel valore reale (prezzo di vendita), non qui.
# (Prima: POLICY_VENDUTE_GIORNI letto all'import, diverso tra sito e cron.)
_STATO_SQL = "status='attivo'"
_COLONNE_MERCATO = ("SELECT id, source, url, vehicle_id, vehicle_identity, make, model, version_raw, year, mileage_km, "
                    "fuel, gearbox, power_kw, price_eur, price_flags, seller_type, damage_class, status, stage_reason, "
                    "problem_search, first_seen_at, last_seen_at, last_checked_at, disappeared_at, " + FIN_SQL +
                    " FROM listings ")
MARKET_SQL = (_COLONNE_MERCATO + "WHERE " + _STATO_SQL + " AND make=%s AND model=%s AND fuel=%s "
              "AND price_eur IS NOT NULL AND {home}")
MARKET_ITALIA_SQL = (_COLONNE_MERCATO + "WHERE " + _STATO_SQL + " AND make=%s AND model=%s AND fuel=%s "
                     "AND price_eur BETWEEN " + str(int(RG.Zona().italia_pmin)) + " AND 400000")
RISCHIO = {"telaio_longheroni", "tetto_montanti", "airbag_esplosi", "frontale_profondo", "airbag",
           "modulo_airbag_volante", "modulo_airbag_passeggero", "centralina_airbag", "motore_sostituzione",
           "cambio_sostituzione", "alluvionata", "incendio"}


# Ripiego (richiesta del 17/10): se nel mercato di casa ci sono meno di 5 auto equivalenti, si cercano i confronti
# in tutto il Nord Italia della nostra base prezzi. L'auto proposta resta entro 150 km; la valutazione lo dichiara.
NORD = ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV", "CR", "MN", "SO", "NO", "VC", "BI", "VB", "AL", "AT", "TO",
        "CN", "AO", "GE", "SV", "IM", "SP", "PC", "PR", "RE", "MO", "BO", "FE", "RA", "FC", "RN", "VR", "VI", "PD", "VE",
        "TV", "BL", "RO", "TN", "BZ", "UD", "PN", "GO", "TS"]
NORD_SQL = "province IN (" + ",".join(f"'{p}'" for p in NORD) + ")"


def valuta_con_ripiego(conn, t: dict, mercato: list, soglia: int, cfg, cache: dict) -> dict:
    v, lista = _valuta_base(conn, t, mercato, soglia, cfg, cache)
    return _giudice_ai(conn, t, v, lista, soglia, cfg)


def _regole(cfg) -> RG.Regole:
    return getattr(cfg, "regole", None) or RG.regole()


def _giudice_ai(conn, t: dict, v: dict, lista: list, soglia: int, cfg) -> dict:
    """28/10: l'AI toglie i confronti che non sono davvero la stessa auto, poi si rifà il calcolo (interruttore
    POLICY_GIUDICE_AI, regole.giudice_ai). Solo per le auto che andrebbero sul sito: le altre non costano niente."""
    if not _regole(cfg).giudice_ai or v.get("categoria") not in ("opportunita", "candidato") or not v.get("confronti"):
        return v
    from ..ai import giudice
    g = giudice.giudica(conn, t, v["confronti"])
    if not g:
        return v
    if not g["via"]:
        v.setdefault("ipotesi", []).append(f"confronti controllati dall'AI: tutti validi ({v.get('n')})")
        v["ai_confronti"] = {"tolti": 0, "nota": g.get("nota")}
        return v
    via = set(g["via"])
    v2 = P.valuta(t, [m for m in lista if m.get("id") not in via], soglia, cfg)
    for k in ("fuori_zona",):
        if v.get(k):
            v2[k] = v[k]
    v2["ai_confronti"] = {"tolti": len(via), "motivi": {str(k): m for k, m in list(g["via"].items())[:15]},
                          "nota": g.get("nota"), "prima": {"M": v.get("M"), "R": v.get("R_finale") or v.get("R"),
                                                           "margine": v.get("margine"), "n": v.get("n")}}
    v2.setdefault("ipotesi", []).append(f"l'AI ha tolto {len(via)} confronti non uguali (versione/allestimento/prezzo): "
                                        f"ricalcolato su {v2.get('n')}")
    logging.getLogger("giudice").warning("GIUDICE_AI %s tolti %d: M %s→%s, margine %s→%s, %s→%s",
                                         t.get("id"), len(via), v.get("M"), v2.get("M"), v.get("margine"),
                                         v2.get("margine"), v.get("categoria"), v2.get("categoria"))
    return v2


def _valuta_base(conn, t: dict, mercato: list, soglia: int, cfg, cache: dict):
    z = _regole(cfg).zona
    v = P.valuta(t, mercato, soglia, cfg)
    lista = mercato
    if not z.ripiego_nord or (v.get("n") or 0) >= cfg.n_preferito or not (t.get("make") and t.get("model") and t.get("fuel")):
        return v, lista
    key = (t["make"], t["model"], t["fuel"])
    if key not in cache:
        cache.clear()                                    # un gruppo alla volta: poca memoria
        cache[key] = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                      for m in conn.execute(MARKET_SQL.format(home=NORD_SQL), key).fetchall()]
    v2 = P.valuta(t, cache[key], soglia, cfg)
    if (v2.get("n") or 0) > (v.get("n") or 0):
        v2["fuori_zona"] = True
        v2.setdefault("ipotesi", []).append("pochi confronti entro 150 km: usati anche annunci uguali nel resto del "
                                            "Nord Italia (prezzi richiesti, possibili piccole differenze di zona)")
        v = v2
        lista = cache[key]
    # 21/10 (cliente): auto di fascia alta (da 50.000 €): pochi esemplari uguali per zona, si usa la base prezzi
    # di tutta Italia (privati e concessionari). Interruttore POLICY_ITALIA (regole.zona.italia).
    if not z.italia or (v.get("n") or 0) >= cfg.n_preferito or (t.get("price_eur") or 0) < z.italia_da:
        return v, lista
    chiave = ("IT",) + tuple(str(x).lower() for x in key)
    if chiave not in cache:
        cache.clear()
        cache[chiave] = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                         for m in conn.execute(MARKET_ITALIA_SQL, key).fetchall()]
    v3 = P.valuta(t, cache[chiave], soglia, cfg)
    if (v3.get("n") or 0) > (v.get("n") or 0):
        v3["fuori_zona"] = True
        v3.setdefault("ipotesi", []).append("auto di fascia alta con pochi confronti vicini: usati gli annunci uguali "
                                            "di tutta Italia della base prezzi (prezzi richiesti, privati e concessionari)")
        return v3, cache[chiave]
    return v, lista


def vr_di(conn, ids: list) -> dict:
    """Valore reale (tabella valore_reale) per questi annunci: l'ULTIMO disponibile, con la data (computed_at).
    4/11: non sparisce più dopo 2 giorni (se il calcolo notturno salta, M non cambia da solo); avviso se vecchio."""
    from .valutazione import ingressi
    return ingressi(conn, ids, solo=("vr",))["vr"] if ids else {}


def ai_di(conn, ids: list) -> dict:
    """Stime del modello AI dei prezzi (tabella stima_ai): l'ultima disponibile per annuncio, con la data."""
    from .valutazione import ingressi
    return ingressi(conn, ids, solo=("ai",))["ai"] if ids else {}


def carica_taratura(conn) -> None:
    """4/11: la taratura è tolta (correggeva M verso i prezzi CHIESTI degli annunci attivi, studio 1 §1.3).
    Resta il nome per i lavori che lo chiamano ancora: non fa niente."""
    return None


def targhe_visibili(conn) -> set:
    """Annunci in cui il controllo delle foto ha visto la targa (tabella controllo_targa)."""
    try:
        return {r["listing_id"] for r in conn.execute("SELECT listing_id FROM controllo_targa WHERE esito='visibile'").fetchall()}
    except Exception:
        conn.rollback()
        return set()


def targa_e_telefono(r: dict, targhe: set | None = None, tel=None) -> bool:
    """Prezzo molto basso ammesso: basta il numero del venditore (regole.antitruffa.basso_solo_telefono, cliente 24/10),
    mai per un commerciante. Stessa regola di valutazione.prepara (telefono unico: valutazione.telefono)."""
    from .valutazione import telefono
    rg = RG.regole()
    tel = telefono(r) if tel is None else tel
    if tel is not True:
        return False
    if (r.get("seller_type") or "privato") == "commerciante" and rg.antitruffa.eccezione_solo_privati:
        return False
    if rg.antitruffa.basso_solo_telefono:
        return True
    from ..core.normalize import find_plate
    return r.get("id") in (targhe or set()) or bool(find_plate(f"{r.get('title') or ''} {r.get('description') or ''}"))


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _rischio(r) -> bool:
    parts = {str((d or {}).get("part")) for d in (_j(r.get("damage_items")) or [])}
    return r.get("damage_class") == "alto_rischio" or bool(parts & RISCHIO)


def _riepilogo(es, extra: dict | None = None) -> dict:
    """Contatori per categoria (e sezione) e per numero di confronti, dalle valutazioni appena fatte."""
    cat, ns = Counter(), Counter()
    for lid, v in es.valutazioni.items():
        sez = (extra or {}).get(lid, {}).get("sezione") or v.get("sezione") or "affari"
        cat[f"{sez}:{v.get('categoria')}"] += 1
        n = v.get("n") or 0
        ns["≥10" if n >= 10 else "5-9" if n >= 5 else "<5"] += 1
    return {"categorie": dict(sorted(cat.items())), "confronti": dict(ns)}


def esegui(conn, limite: int | None = None) -> dict:
    """Rivaluta le auto già approfondite (sezioni affari e incidentate) e la sezione economica, con
    valutazione.valuta_e_scrivi (fonte 'cron'). Nessun limite casuale: tutte, ordinate per gruppo."""
    from ..web import scovo
    from .valutazione import valuta_e_scrivi
    t0 = time.time()
    conn.execute("SET statement_timeout = '60s'")
    affari = [r["id"] for r in conn.execute("SELECT q.id FROM (" + scovo.LIST_SQL + ") q", {"me": 0}).fetchall()]
    eco = [r["id"] for r in conn.execute(
        "SELECT q.id FROM (" + scovo.ECO_SQL + " ORDER BY l.first_seen_at DESC LIMIT %(lim)s) q",
        dict(scovo.eco_params(0), lim=int(os.environ.get("ECONOMICHE_QUANTE", "2500")))).fetchall()]
    extra = {i: {"sezione": "affari"} for i in affari}
    for i in eco:
        extra.setdefault(i, {"sezione": "economiche"})
    ids = list(extra)[:limite] if limite else list(extra)
    es = valuta_e_scrivi(conn, ids, "cron", extra=extra)
    sospette = 0
    for lid, v in es.valutazioni.items():
        if (v.get("categoria") in scovo.CATEGORIE_SITO and v.get("M") and v.get("acquisto")
                and v["M"] > 1.5 * v["acquisto"] and sospette < 12):
            sospette += 1        # controllo a vista: mercato molto sopra il prezzo (solo dati dell'auto, niente venditori)
            log.warning("POLICY_SOSPETTA %s", json.dumps({
                "id": lid, "prezzo": v.get("acquisto"), "M": v.get("M"), "M_fonte": v.get("M_fonte"),
                "metodo": v.get("metodo"), "R": v.get("R_finale"), "margine": v.get("margine"), "cat": v["categoria"],
                "confronti": [[c.get("prezzo"), c.get("prezzo_corretto"), c.get("anno"), c.get("km"), c.get("kw")]
                              for c in (v.get("confronti") or [])[:12]]}, ensure_ascii=False, default=str))
    stats = {"valutate": len(ids), "secondi": round(time.time() - t0, 1), **_riepilogo(es, extra),
             "contatori": dict(es.stats), "impronta_regole": RG.impronta()}
    log.warning("POLICY_RIEPILOGO %s", json.dumps(stats, ensure_ascii=False))
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from ..db import connect
    with connect() as conn:
        print(esegui(conn))


# ------------------------------------------------------------------ privati sani della zona (utilitarie)
UTILITARIE = {"panda", "punto", "grande-punto", "punto-evo", "500", "ypsilon", "fiesta", "ka", "ka+", "fusion", "polo", "up",
              "fox", "lupo", "yaris", "aygo", "iq", "clio", "twingo", "modus", "zoe", "208", "207", "206", "107", "108",
              "c1", "c2", "c3", "c3-picasso", "ds3", "corsa", "agila", "adam", "karl", "micra", "note", "i10", "i20",
              "getz", "atos", "picanto", "rio", "venga", "ibiza", "mii", "arona", "fabia", "citigo", "roomster", "swift",
              "splash", "alto", "celerio", "ignis", "jazz", "sandero", "logan", "spark", "matiz", "colt", "space-star",
              "mito", "a1", "fortwo", "forfour", "one", "cooper", "1-series", "serie-1", "mazda2", "2", "colt", "500l",
              "500x", "qubo", "fiorino", "idea", "musa", "delta", "stonic", "kalos", "aveo", "duster", "captur", "2008",
              "juke", "golf", "a-class", "classe-a", "a-klasse", "b-klasse", "classe-b", "auris", "corolla", "megane", "astra",
              "focus", "bravo", "308", "c4", "leon", "ceed", "i30", "civic"}

# Tutti i privati sani della zona, visti negli ultimi 14 giorni (la descrizione intera la legge valutazione.prepara)
UTIL_SQL = ("SELECT l.id FROM listings l "
            "WHERE l.status='attivo' AND l.seller_type='privato' AND l.price_eur BETWEEN %(lo)s AND %(hi)s "
            "AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s AND l.province = ANY(%(prov)s) "
            "AND l.last_seen_at > now() - interval '14 days' AND l.source IN ('subito','autoscout24','facebook') "
            "AND COALESCE(l.damage_class, 'sconosciuto') IN ('nessuno','sconosciuto') AND NOT COALESCE(l.problem_search, false) "
            "AND COALESCE(l.stage_reason, '') NOT IN ('doppione','truffa','prezzo_finto','dati_incoerenti') "
            "AND COALESCE(l.stage_reason, '') NOT LIKE 'analisi_manuale%%'")      # scartate a mano: mai ripubblicate

_UTIL_LOCK = __import__("threading").Lock()


def utilitarie(conn, pubblica: bool = False) -> dict:
    """Un solo calcolo completo alla volta nello stesso processo (memoria: 512 MB su Render)."""
    if not _UTIL_LOCK.acquire(blocking=False):
        log.warning("UTILITARIE già in corso: salto")
        return {}
    try:
        return _utilitarie(conn, pubblica)
    finally:
        _UTIL_LOCK.release()


def _utilitarie(conn, pubblica: bool = False) -> dict:
    """Tutti i privati sani della zona da 500 a 50.000 €, valutati con valutazione.valuta_e_scrivi (fonte
    'cron-utilitarie': si scrivono TUTTE le valutazioni, anche escluse e non convenienti, così non resta una riga
    vecchia "candidato"). Con pubblica=True quelle sopra il margine minimo del cliente vanno sul sito
    (stage 'approfondito') e in top_auto (ordinate per margine/soglia)."""
    from ..core.dal_testo import completa_db
    from ..web import scovo
    from ..zone import MILANO
    from .valutazione import valuta_e_scrivi
    t0 = time.time()
    a = RG.regole().ammissione
    log.warning("DAL_TESTO %s", json.dumps(completa_db(conn)))          # anno, km, alimentazione mancanti: dal testo
    ids = [r["id"] for r in conn.execute(UTIL_SQL, {"lo": 500, "hi": 50000, "anno": a.anno_min, "km": a.km_max,
                                                    "prov": list(MILANO)}).fetchall()]
    es = valuta_e_scrivi(conn, ids, "cron-utilitarie")
    pass_ = [(lid, v) for lid, v in es.valutazioni.items()
             if es.salvata.get(lid) in scovo.CATEGORIE_SITO and v.get("categoria") in scovo.CATEGORIE_SITO
             and v.get("margine") is not None and v.get("acquisto")
             and v["margine"] >= scovo.soglia(int(v["acquisto"])) and es.telefono.get(lid) is not False]
    per_fascia = Counter(next(f"<{lim}" for lim in (5000, 10000, 15000, 20000, 30000, 10**9) if int(v["acquisto"]) < lim)
                         for _, v in pass_)
    out = {"lette": len(ids), "valutate": len(es.valutazioni), "stats": dict(es.stats),
           "pubblicabili_per_fascia": dict(per_fascia), "secondi": round(time.time() - t0)}
    log.warning("UTILITARIE_SIM %s", json.dumps(out, ensure_ascii=False))
    if pubblica:
        n = 0
        conn.execute("DELETE FROM top_auto")
        ordine = sorted(pass_, key=lambda x: -x[1]["margine"] / scovo.soglia(int(x[1]["acquisto"])))
        for pos, (lid, v) in enumerate(ordine):
            conn.execute("INSERT INTO top_auto (listing_id, posizione, margine) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                         (lid, pos, v["margine"]))
        for lid, v in ordine:
            if not conn.execute("SELECT 1 FROM valuations WHERE listing_id=%s LIMIT 1", (lid,)).fetchone():
                conn.execute(
                    """INSERT INTO valuations (listing_id, engine_version, private_median, resale_median, resale_prudent,
                         n_comparables, comparables_used, confidence, parts_cost_low, parts_cost_high)
                       VALUES (%s,%s,%s,%s,%s,%s,'[]',%s,0,0)""",
                    (lid, P.POLICY_VERSION + "+utilitarie", v.get("M"), v.get("M"), v.get("R_finale"), v.get("n"),
                     "affidabile" if v["categoria"] == "opportunita" else "da_verificare"))
            conn.execute("UPDATE listings SET stage='approfondito', stage_reason=%s, deep_at=COALESCE(deep_at, now()), "
                         "last_checked_at=now() WHERE id=%s AND stage <> 'approfondito'",
                         ("opportunita" if v["categoria"] == "opportunita" else "da_verificare", lid))
            n += 1
        conn.commit()
        log.warning("UTILITARIE_PUBBLICATE %d", n)
    return out
