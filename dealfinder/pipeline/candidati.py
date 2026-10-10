"""Valutazione SENZA AI di tutti gli annunci di privati, ed esportazione dei candidati nei log.

    python -m dealfinder.pipeline.candidati

Serve quando l'analisi AI automatica non è attiva: i candidati vengono esaminati a parte
(analisi manuale/assistita) e i risultati reimportati con pipeline.manuale.

Per ogni annuncio di privato in zona: confronti con auto simili, rivendita prudente a privati,
margine con i costi standard PRIMA dei ricambi. Esce se il margine supera la soglia (le auto con
problemi dichiarati devono avere 800 € in più di spazio per i ricambi). Prima di esportare si
verifica che l'annuncio esista ancora.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter

from ..config import settings
from ..core.consistency import check as check_consistency
from ..core.normalize import problem_hint
from ..db import connect, log_job
from ..pricing.engine import value_listing
from ..pricing.fallback import apply_model, estimate_missing_km, weak
from ..pricing.train import load_active
from ..pricing.margin import DealerCosts, compute_margin
from ..store import LIGHT_COLS, load_market, photos_of, row_to_listing
from ..web import scovo
from .verify import verify_rows

log = logging.getLogger("candidati")
PROVINCES = ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV"]   # Milano e dintorni
MAX_EXPORT = int(os.environ.get("MAX_CANDIDATI", "1500"))
DAMAGE_ROOM = 400


BANDS = [(500, 2000), (2000, 5000), (5000, 8000), (8000, 12000), (12000, 20001)]


def guadagno_sito(v, price: int | None) -> int | None:
    """Guadagno come lo mostra il sito, prima dei ricambi: rivendita al 30% sotto il mercato tra privati."""
    mercato = v.private_median or v.resale_median_private or v.resale_median
    if not mercato or not price:
        return None
    return round(mercato * (1 - scovo.SCONTO_DEFAULT / 100)) - price - sum(scovo.COSTI_FISSI.values())


def hidden_reasons(l) -> list[str]:
    """Annunci scritti male: pochi li notano, restano disponibili più a lungo e si tratta meglio."""
    out = []
    title = (l.title or "").lower()
    if l.make and l.model and l.make.split("-")[0] not in title and l.model.split("-")[0] not in title:
        out.append("titolo_senza_marca_modello")
    if len((l.description or "").strip()) < 40:
        out.append("descrizione_quasi_vuota")
    if len(l.photos or []) <= 2:
        out.append("poche_foto")
    if "prezzo_da_descrizione" in (l.price_flags or []):
        out.append("prezzo_sbagliato_nel_campo")
    return out


def balanced(items: list, limit: int) -> list:
    """Stessa quota per ogni fascia di prezzo e stato (sana / con problemi); i posti non usati
    vanno ai migliori rimasti. Dentro ogni gruppo vince il margine più alto e solido."""
    items = sorted(items, key=lambda x: -x[0])
    groups: dict = {}
    for it in items:
        price, damaged = it[2].price_eur, it[6]
        band = next((b for b in BANDS if b[0] <= price < b[1]), BANDS[-1])
        groups.setdefault((band, damaged), []).append(it)
    quota = max(1, limit // (len(BANDS) * 2))
    chosen, rest = [], []
    for g in groups.values():
        chosen += g[:quota]
        rest += g[quota:]
    rest.sort(key=lambda x: -x[0])
    chosen += rest[:max(0, limit - len(chosen))]
    return chosen


def run() -> dict:
    stats: Counter = Counter()
    out = []
    with connect() as conn:
        finish = log_job(conn, "candidati:v3")
        rows = conn.execute(
            f"""SELECT {LIGHT_COLS} FROM listings WHERE status='attivo' AND price_eur BETWEEN 500 AND %s
                 AND seller_type <> 'commerciante'
                 AND (province = ANY(%s) OR source='facebook')
                 AND (stage NOT IN ('scartato','approfondito')
                      OR (stage='scartato' AND stage_reason='margine_insufficiente'))""",
            (settings.max_purchase_eur, PROVINCES)).fetchall()
        stats["esaminati"] = len(rows)
        cache: dict = {}
        costs = DealerCosts()
        model = load_active(conn)
        for r in rows:
            l = row_to_listing(r)
            if check_consistency(l, fix=True) or "dati_incoerenti" in l.price_flags:
                stats["dati_incoerenti"] += 1          # campi compilati a caso dal venditore
                continue
            km_est = estimate_missing_km(l)
            if not (l.make and l.model and l.year and l.mileage_km is not None):
                stats["dati_mancanti"] += 1
                continue
            key = (l.make, l.model, l.fuel)
            if key not in cache:
                cache[key] = load_market(conn, *key)
            hints = problem_hint(l)
            damaged = bool(hints or l.damage_declared or r.get("problem_search"))
            l.damage_class = "nessuno"                 # rivendita calcolata da auto sistemata
            v = value_listing(l, cache[key])
            from_model = False
            if weak(v):
                from_model = apply_model(v, l, model)
                if not from_model:
                    stats["stima_poco_solida"] += 1
                    continue
                stats["stima_da_modello"] += 1
            m = compute_margin(l, v, costs)
            # regola del sito: mercato tra privati −30% − prezzo − 140 € ≥ soglia per fascia di prezzo
            # (fino a 5.000 → 1.500; 8.000 → 2.000; 12.000 → 3.000; oltre → 4.000)
            sito = guadagno_sito(v, l.price_eur)
            need = scovo.soglia(l.price_eur) + (DAMAGE_ROOM if damaged else 0)
            if from_model or km_est:
                need = round(need * 1.1)             # stima meno solida: serve più margine
            if sito is None or sito < need:
                stats["margine_insufficiente"] += 1
                continue
            stats["fascia_" + str(scovo.soglia(l.price_eur))] += 1
            if "prezzo_troppo_basso" in v.fraud_flags and not damaged:
                stats["sospetto"] += 1
            l.photos = photos_of(conn, r["id"])
            hidden = hidden_reasons(l)
            if hidden:
                stats["annuncio_nascosto"] += 1
            out.append((m.score, r, l, v, m, hints, damaged, from_model, km_est, hidden))
        out = balanced(out, MAX_EXPORT)
        stats.update(verify_rows(conn, [x[1] for x in out]))
        alive = {x["id"] for x in conn.execute(
            "SELECT id FROM listings WHERE id = ANY(%s) AND status='attivo'", ([x[1]["id"] for x in out],)).fetchall()}
        n = 0
        for score, r, l, v, m, hints, damaged, from_model, km_est, hidden in out:
            if r["id"] not in alive:
                continue
            n += 1
            rec = {"id": r["id"], "fonte": r["source"], "url": r["url"], "titolo": l.title,
                   "descrizione": (l.description or "")[:900], "marca": l.make, "modello": l.model,
                   "versione": l.version_raw, "anno": l.year, "km": l.mileage_km, "carburante": l.fuel,
                   "cambio": l.gearbox, "kw": l.power_kw, "citta": l.city, "provincia": l.province,
                   "prezzo": l.price_eur, "mediana_privati": v.private_median, "rivendita_prudente": v.resale_prudent,
                   "confronti": v.n_comparables, "livello_confronti": v.comparable_level, "dispersione": v.dispersion,
                   "margine_prima_ricambi": guadagno_sito(v, l.price_eur), "soglia": scovo.soglia(l.price_eur),
                   "margine_prudente_motore": m.net_margin, "con_problemi": damaged,
                   "parole_problema": hints, "mediana_da_sistemare": v.asis_median, "segnali": v.fraud_flags,
                   "giorni_vendita_simili": v.liquidity_days,
                   "stima": "modello" if from_model else "confronti", "km_stimati": km_est,
                   "annuncio_nascosto": hidden, "foto": photos_of(conn, r["id"])[:3]}
            log.info("CANDIDATO %s", json.dumps(rec, ensure_ascii=False, default=str))
        stats["esportati"] = n
        finish(True, dict(stats))
    log.info("CANDIDATI_RIEPILOGO %s", json.dumps(dict(stats)))
    return dict(stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
