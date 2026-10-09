"""Addestramento del modello dei prezzi (automatico, dopo la raccolta notturna del mercato).

    python -m dealfinder.pricing.train            # addestra se il modello attivo ha più di un giorno
    python -m dealfinder.pricing.train --forza    # addestra comunque

Il nuovo modello diventa attivo solo se sbaglia meno del modello attivo sugli stessi annunci di prova.
"""
from __future__ import annotations

import json
import logging
import sys

from ..db import connect, log_job
from ..store import LIGHT_COLS, MARKET_COLS, row_to_listing
from .model import MODEL_VERSION, train

log = logging.getLogger("train")


def load_active(conn) -> dict | None:
    row = conn.execute("SELECT model FROM price_models WHERE active ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    m = row["model"]
    return json.loads(m) if isinstance(m, str) else m


def prescreen_backlog(conn, model: dict) -> dict:
    """Applica il modello agli annunci ancora da valutare (stage nuovo/attesa_mercato)."""
    from collections import Counter

    from .model import prescreen
    stats: Counter = Counter()
    rows = conn.execute(f"SELECT {LIGHT_COLS} FROM listings WHERE stage IN ('nuovo','attesa_mercato') AND status='attivo' "
                        "AND price_eur IS NOT NULL").fetchall()
    for r in rows:
        ps = prescreen(model, row_to_listing(r))
        stats[ps["esito"]] += 1
        conn.execute("UPDATE listings SET model_p50=%s, model_p25=%s, prescreen=%s, prescreen_potential=%s, "
                     "stage = CASE WHEN %s='non_interessante' THEN 'mercato' ELSE stage END WHERE id=%s",
                     (ps.get("model_p50"), ps.get("model_p25"), ps["esito"], ps.get("potenziale"),
                      ps["esito"], r["id"]))
    conn.commit()
    return dict(stats)


GROWTH_RETRAIN = 0.10      # la base è cresciuta del 10%: si riaddestra subito
RETRAIN_HOURS = 22         # altrimenti ogni giorno: il modello impara di continuo dai nuovi annunci


def run(force: bool = False) -> dict:
    with connect() as conn:
        cur = conn.execute("SELECT created_at, metrics, version FROM price_models WHERE active "
                           "ORDER BY created_at DESC LIMIT 1").fetchone()
        where = """price_eur IS NOT NULL AND make IS NOT NULL AND model IS NOT NULL
               AND (province IN ('MI','MB','BG','BS','CO','VA','LC','LO','PV','CR','MN','SO') OR (province IS NULL AND (region IS NULL OR lower(region) = 'lombardia')))
               AND (status='attivo' OR disappeared_at > now() - interval '180 days')
               AND id NOT IN (SELECT listing_id FROM dealer_feedback WHERE status='scartata' AND
                   (reason ILIKE '%%dati%%' OR reason ILIKE '%%prezzo non vero%%' OR reason ILIKE '%%truffa%%'))"""
        n_now = conn.execute(f"SELECT count(*) AS n FROM listings WHERE {where}").fetchone()["n"]
        if cur and not force:
            prev_m = cur["metrics"] if isinstance(cur["metrics"], dict) else json.loads(cur["metrics"] or "{}")
            n_prev = prev_m.get("annunci_base")
            grown = n_prev is None or n_now >= n_prev * (1 + GROWTH_RETRAIN) \
                or cur.get("version") != MODEL_VERSION          # nuova versione del modello: si riaddestra
            age = conn.execute("SELECT now() - %s > make_interval(hours => %s) AS old",
                               (cur["created_at"], RETRAIN_HOURS)).fetchone()
            if not age["old"] and not grown:
                log.info("modello attivo recente (base %s annunci, ora %s): nessun addestramento", n_prev, n_now)
                return {"saltato": True, "annunci": n_now}
            log.info("riaddestramento: base passata da %s a %s annunci", n_prev, n_now)
        finish = log_job(conn, "train")
        listings = [row_to_listing(r) for r in conn.execute(
            f"""SELECT {MARKET_COLS} FROM listings WHERE {where}""")]
        # Impara anche dai commercianti: si aggiungono i prezzi di vendita VERI delle auto
        # rivendute (contano 3 volte); gli annunci segnalati come falsi sono già esclusi sopra
        try:
            cols = ", ".join("f.sold_eur AS price_eur" if c.strip() == "price_eur" else "l." + c.strip()
                             for c in MARKET_COLS.split(","))
            sold = conn.execute(f"SELECT {cols} FROM dealer_feedback f JOIN listings l ON l.id=f.listing_id "
                                "WHERE f.sold_eur IS NOT NULL").fetchall()
            for r in sold:
                x = row_to_listing(r)
                x.damage_declared, x.damage_class, x.peso = False, "nessuno", 3.0
                listings.append(x)
            log.info("feedback commercianti: %d prezzi di vendita veri", len(sold))
        except Exception as e:
            conn.rollback()
            log.warning("feedback commercianti non usato: %s", str(e)[:150])
        model = train(listings)
        n_rows = len(listings)
        del listings
        test = model.pop("_test", [])
        m = model["metrics"]
        m["annunci_base"] = n_now
        # Sfida tra modelli: il nuovo vince solo se sbaglia meno del vecchio SUGLI STESSI annunci di prova
        old_model = load_active(conn)
        old_err = None
        if old_model:
            from .model import evaluate
            try:
                old_err = evaluate(old_model, test).get("median_abs_pct_error")
            except Exception:
                old_err = None
        m["errore_modello_precedente"] = old_err
        ok = m.get("n_test", 0) >= 50 and (old_err is None or m["median_abs_pct_error"] <= old_err * 1.01)
        del test
        conn.execute("INSERT INTO price_models (version, model, metrics, active) VALUES (%s,%s,%s,%s)",
                     (MODEL_VERSION, json.dumps(model), json.dumps(m), ok))
        if ok:
            conn.execute("UPDATE price_models SET active = (id = (SELECT max(id) FROM price_models))")
        conn.commit()
        res = {"annunci": n_rows, "gruppi": len(model["groups"]), "metriche": m, "attivato": ok}
        log.info("MODELLO_STORIA errore %.2f%% (prima %s) impostazioni %s attivato %s",
                 100 * m.get("median_abs_pct_error", 0), f"{100 * old_err:.2f}%" if old_err else "n.d.",
                 m.get("impostazioni"), ok)
        if ok:
            res["prevalutati"] = prescreen_backlog(conn, model)
        finish(True, res)
    log.info("MODELLO %s", json.dumps(res, default=str))
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run("--forza" in sys.argv)
