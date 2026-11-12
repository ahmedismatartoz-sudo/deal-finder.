"""12/11 (buchi della raccolta, controllo 1 §3.1 e §3.2): la scelta delle candidate del giro della zona sprecava posti.

1. Il tetto delle candidate (ZC_CANDIDATE, 5.000) era sempre pieno, ma una parte dei posti andava ad auto che la
   regola del sito scarta SICURAMENTE subito dopo (km oltre il massimo, km mancanti, anno troppo vecchio, dati
   incompleti, senza numero, danno grave già letto dall'AI). Ora:
     - in SQL, prima del LIMIT: km obbligatori e ≤ regole.ammissione.km_max (benzina_mi.km_max_scelta), carburante
       presente;
     - in Python, con la STESSA funzione della valutazione (pricing/valutazione.prepara), sulle righe lette con un
       margine in più (SCELTA_SOVRA, 1,4 × il tetto): via quelle con un motivo di esclusione sicuro, poi il taglio.
       "danni_da_leggere" NON è sicuro (la valutazione mette l'auto in coda per la lettura dei difetti): resta.
2. Le stesse auto venivano rivalutate a ogni giro senza che nulla fosse cambiato. Ora un'auto valutata da meno di
   SCELTA_INVARIATE_ORE ore (10, sotto le 36 di validità di regole.scrittura) con le regole di oggi, senza cambi da
   allora (prezzo: price_events; testo/dati: listings.contenuto_at, migrazione 051, se c'è) e con il margine lontano
   dalla soglia del sito (più di SCELTA_GAP_INVARIATE, 1.500 €) non prende un posto: il posto va alla prossima della
   lista. 0 = spento.
3. Cambi di prezzo (§3.2): prima LIMIT senza ordine (oltre i 1.500 si perdevano a caso). Ora tutti i cambi delle
   ultime ore vengono letti con il prezzo di prima e il margine dell'ultima valutazione, e si tengono per primi:
   fascia 0 = con il ribasso il margine raggiunge la soglia del sito; fascia 1 = vicino alla soglia (entro
   SCELTA_GAP_VICINO €, 1.000) o mai valutata; fascia 2 = lontana; fascia 3 = prezzo salito o uguale. Dentro la fascia,
   prima i ribassi più grandi (percentuale + euro). Il calcolo dei margini NON cambia: qui si sceglie solo l'ordine.
"""
from __future__ import annotations

import logging
import os
from collections import Counter

log = logging.getLogger("scelta_candidate")

SOVRA = 1.4                 # righe lette in più rispetto al tetto, per riempirlo dopo il filtro sicuro
INVARIATE_ORE = 10          # 12/11 revisione: 10 ore (sotto le 36 di regole.scrittura.ore_validita)
GAP_INVARIATE = 1500        # euro: si salta solo un'auto il cui margine è lontano dalla soglia del sito più di così
GAP_VICINO = 1000           # euro sotto la soglia di margine per dire "vicina"

# motivi di esclusione di valutazione.prepara che non cambiano rivalutando subito: l'auto verrebbe scartata comunque
SICURI_PREFISSI = ("km_oltre_", "anno_prima_del_")
SICURI = {"km_mancanti", "dati_incompleti", "senza_numero", "danno_grave"}


def _num(nome: str, default: float) -> float:
    try:
        return float(os.environ.get(nome) or default)
    except ValueError:
        return float(default)


def sovra() -> float:
    return max(1.0, _num("SCELTA_SOVRA", SOVRA))


def ore_invariate() -> int:
    return max(0, int(_num("SCELTA_INVARIATE_ORE", INVARIATE_ORE)))


def km_obbligatori() -> bool:
    from ..regole import regole
    try:
        return bool(regole().ammissione.km_obbligatori)
    except Exception:
        return True


def motivo_sicuro(motivo: str | None) -> bool:
    return bool(motivo) and (motivo in SICURI or motivo.startswith(SICURI_PREFISSI))


# ------------------------------------------------------------------ 2. auto invariate valutate da poco

_COL: dict = {}


def ha_contenuto_at(conn) -> bool:
    """La colonna listings.contenuto_at (migrazione 051) c'è? Il cron può partire prima che il sito l'abbia applicata.
    Ricontrollata ogni 10 minuti finché manca."""
    import time
    if _COL.get("ok") or time.time() - _COL.get("at", 0) < 600:
        return bool(_COL.get("ok"))
    try:
        ok = bool(conn.execute("SELECT 1 AS x FROM information_schema.columns WHERE table_name='listings' "
                               "AND column_name='contenuto_at'").fetchone())
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        ok = False
    _COL.update(ok=ok, at=time.time())
    return ok


def condizione_invariate(conn) -> str:
    """Frammento SQL (per t = listings) vero se l'auto va SALTATA: valutata da poco con le regole di oggi e nulla è
    cambiato da allora. Parametri: %(inv_ore)s (0 = mai vero), %(inv_regole)s."""
    contenuto = ha_contenuto_at(conn)
    extra = " AND (t.contenuto_at IS NULL OR t.contenuto_at <= vp.computed_at)" if contenuto else ""
    # 12/11 revisione: si salta solo se l'ultima valutazione era LONTANA dalla soglia (soglia − margine > GAP); le auto
    # vicine alla soglia si rivalutano a ogni giro (un confronto nuovo può farle entrare)
    return ("(%(inv_ore)s > 0 AND EXISTS (SELECT 1 FROM valutazioni_policy vp WHERE vp.listing_id = t.id"
            " AND vp.computed_at > now() - make_interval(hours => %(inv_ore)s)"
            " AND vp.impronta_regole = %(inv_regole)s"
            " AND vp.margine IS NOT NULL AND " + soglia_sql("t.price_eur") + " - vp.margine > %(inv_gap)s" + extra +
            " AND NOT EXISTS (SELECT 1 FROM price_events pe WHERE pe.listing_id = t.id"
            " AND pe.event IN ('prezzo', 'riapparso') AND pe.at > vp.computed_at)))")


def soglia_sql(col: str) -> str:
    """scovo.soglia(prezzo) scritta come CASE di SQL, dalla stessa tabella (SOGLIE_PV o SOGLIE): niente soglie nuove."""
    from ..web import scovo
    tab, op = (scovo.SOGLIE_PV, "<") if scovo.POLICY_SITO == "pv2" else (scovo.SOGLIE, "<=")
    rami = " ".join(f"WHEN {col} {op} {int(lim)} THEN {int(m)}" for lim, m in tab if lim != 10**9)
    ultima = next((m for lim, m in tab if lim == 10**9), tab[-1][1])
    return f"(CASE {rami} ELSE {int(ultima)} END)"


def parametri_invariate() -> dict:
    from ..pricing.valutazione import impronta_regole
    try:
        imp = impronta_regole()
    except Exception:
        imp = ""
    return {"inv_ore": ore_invariate(), "inv_regole": imp, "inv_gap": int(_num("SCELTA_GAP_INVARIATE", GAP_INVARIATE))}


# ------------------------------------------------------------------ 1. scarti sicuri prima del tetto

_COLONNE = ("id, source, title, description, damage_declared, damage_items, damage_class, seller_type, make, model, "
            "fuel, price_eur, mileage_km, year, telefono_ok, vehicle_identity, problem_search, price_flags, stage_reason")


def filtra_sicuri(conn, righe: list, limite: int | None = None, stats: Counter | None = None) -> list:
    """Toglie (in ordine) le righe che la regola del sito scarta sicuramente, poi taglia a `limite`.
    Stessa funzione della valutazione (valutazione.prepara). In caso di errore restituisce le righe come sono."""
    stats = stats if stats is not None else Counter()
    if not righe:
        return list(righe)
    try:
        from ..pricing import valutazione as V
        from .. import regole as RG
        rg = RG.regole()
        ids = [r["id"] for r in righe]
        dati: dict = {}
        difetti: dict = {}
        for i in range(0, len(ids), 2000):
            pezzo = ids[i:i + 2000]
            for r in conn.execute(f"SELECT {_COLONNE} FROM listings WHERE id = ANY(%s)", (pezzo,)).fetchall():
                dati[r["id"]] = dict(r)
            try:
                for r in conn.execute("SELECT listing_id, costo, grave, motivo FROM ai_difetti WHERE listing_id = ANY(%s)",
                                      (pezzo,)).fetchall():
                    difetti[r["listing_id"]] = dict(r)
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        ing = {"parti": {}, "ai": {}, "vr": {}, "difetti": difetti, "fattori_calibra": None}
        tenute = []
        for r in righe:
            d = dati.get(r["id"])
            if d is not None:
                try:
                    _t, motivo = V.prepara(d, ing, rg)
                except Exception:
                    motivo = None
                if motivo_sicuro(motivo):
                    stats[f"scelta_scartata:{motivo}"] += 1
                    stats["scelta_posti_liberati"] += 1
                    continue
            tenute.append(r)
            if limite is not None and len(tenute) >= limite:
                break
        return tenute
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("SCELTA filtro sicuro non riuscito (righe tenute come sono): %s", str(e)[:200])
        return list(righe)[:limite] if limite is not None else list(righe)


# ------------------------------------------------------------------ 3. cambi di prezzo in ordine

CAMBI_SQL = """
WITH c AS (
  SELECT listing_id, min(at) AS primo FROM price_events
  WHERE event = 'prezzo' AND at > now() - make_interval(hours => %(ore)s)
  GROUP BY listing_id
)
SELECT t.id, t.source, t.url, t.price_eur, t.telefono_ok, t.make, t.model, NULL::int AS n, NULL::float AS med,
       (SELECT pe.price_eur FROM price_events pe WHERE pe.listing_id = t.id AND pe.price_eur IS NOT NULL
          AND pe.at < c.primo ORDER BY pe.at DESC LIMIT 1) AS prezzo_prima,
       (SELECT vp.margine FROM valutazioni_policy vp WHERE vp.listing_id = t.id AND vp.margine IS NOT NULL
          ORDER BY vp.computed_at DESC LIMIT 1) AS margine_prima
FROM c JOIN listings t ON t.id = c.listing_id
WHERE t.status = 'attivo' AND upper(t.province) = ANY(%(prov)s)
  AND t.price_eur BETWEEN 1000 AND %(pmax)s AND coalesce(t.year, 2007) >= 2007
  AND (t.mileage_km IS NOT NULL OR NOT %(km_obblig)s) AND coalesce(t.mileage_km, 0) <= %(kmmax)s
  AND (%(tutti_carb)s OR t.fuel ILIKE 'benzina%%')
  AND coalesce(t.damage_class,'') NOT IN ('grave','alto_rischio','medio')
LIMIT %(lettura_max)s"""


def chiave_cambio(r: dict, soglia_fn=None, gap_vicino: float | None = None) -> tuple:
    """Ordine dei cambi di prezzo (più piccolo = prima). Vedi il punto 3 in cima al file."""
    if soglia_fn is None:
        from ..web.scovo import soglia as soglia_fn
    gap_vicino = GAP_VICINO if gap_vicino is None else gap_vicino
    ora = int(r.get("price_eur") or 0)
    prima = r.get("prezzo_prima")
    ribasso = (int(prima) - ora) if prima else 0
    pct = (100.0 * ribasso / int(prima)) if prima else 0.0
    punti = pct + ribasso / 100.0                 # 10% e 1.000 € = 20 punti
    if ribasso <= 0:
        return (3, -punti, r.get("id") or 0)
    marg = r.get("margine_prima")
    if marg is None:
        return (1, -punti, r.get("id") or 0)
    try:
        gap = soglia_fn(ora) - (int(marg) + ribasso)
    except Exception:
        return (1, -punti, r.get("id") or 0)
    fascia = 0 if gap <= 0 else (1 if gap <= gap_vicino else 2)
    return (fascia, -punti, r.get("id") or 0)


def ordina_cambi(righe: list, limite: int, soglia_fn=None, stats: Counter | None = None) -> list:
    gap = _num("SCELTA_GAP_VICINO", GAP_VICINO)
    ordinate = sorted(righe, key=lambda r: chiave_cambio(r, soglia_fn, gap))
    if stats is not None:
        for r in ordinate[:limite]:
            stats[f"cambi_fascia_{chiave_cambio(r, soglia_fn, gap)[0]}"] += 1
        stats["cambi_oltre_tetto"] = max(0, len(ordinate) - limite)
    return ordinate[:limite]


def cambi_prezzo(conn, prov: list, pmax: int, kmmax: int, tutti_carb: bool, ore: int, limite: int,
                 stats: Counter | None = None) -> list:
    righe = conn.execute(CAMBI_SQL, {"ore": ore, "prov": prov, "pmax": pmax, "kmmax": kmmax, "tutti_carb": tutti_carb,
                                     "km_obblig": km_obbligatori(),
                                     "lettura_max": int(_num("SCELTA_CAMBI_LETTURA_MAX", 20000))}).fetchall()
    righe = filtra_sicuri(conn, [dict(r) for r in righe], None, Counter())   # scarti sicuri prima del taglio
    return ordina_cambi(righe, limite, stats=stats)
