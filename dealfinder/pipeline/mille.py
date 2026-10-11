"""Selezione dei 1.000 annunci migliori di tutta la base, per il controllo uno per uno.

Regole (le stesse del sito, prima dei ricambi):
  auto dal 2007, privati, Milano e dintorni (o Facebook), annuncio attivo e verificato,
  mercato tra privati −10% − prezzo − 140 € ≥ soglia della fascia di prezzo.
Per ognuno scrive nei log:
  MILLE {json}                         dati, descrizione, stato e ricambi già stimati
  MILLEFOTO <id> <k>/<n> <base64>      foglio con 4 foto (2×2) per guardare i danni
Gira una volta per versione (MILLE_VERSIONE) dentro il ciclo.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
from collections import Counter

from ..config import settings
from ..core.consistency import check as check_consistency
from ..db import connect, log_job
from ..pricing.engine import value_listing
from ..pricing.fallback import apply_model, estimate_missing_km, weak
from ..pricing.train import load_active
from ..store import LIGHT_COLS, load_market, row_to_listing
from ..web import foto, scovo
from .verify import verify_rows

log = logging.getLogger("mille")
VERSIONE = os.environ.get("MILLE_VERSIONE", "v4")      # v4: rivendita al 10% sotto mercato
QUANTI = int(os.environ.get("MILLE_QUANTI", "1000"))
PROVINCES = ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV"]     # Milano e dintorni (selezione di sempre)
CELLA = (270, 200)
PEZZO = 12000


def job_name(versione: str | None = None) -> str:
    return f"mille:{versione or VERSIONE}"


def foglio(images: list[bytes], titolo: str) -> bytes | None:
    from PIL import Image, ImageDraw
    cells = []
    for raw in images:
        try:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            im.thumbnail(CELLA)
            cells.append(im)
        except Exception:
            continue
    if not cells:
        return None
    W, H = CELLA[0] * 2, CELLA[1] * 2 + 18
    sheet = Image.new("RGB", (W, H), (20, 20, 20))
    for i, im in enumerate(cells[:4]):
        x, y = (i % 2) * CELLA[0], 18 + (i // 2) * CELLA[1]
        sheet.paste(im, (x + (CELLA[0] - im.width) // 2, y + (CELLA[1] - im.height) // 2))
    ImageDraw.Draw(sheet).text((5, 3), titolo[:110], fill=(255, 255, 0))
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=52, optimize=True)
    return out.getvalue()


def run(fonte: str | None = None, versione: str | None = None, province: list[str] | None = None) -> dict:
    """fonte: solo gli annunci di quel sito (es. "autoscout24"); versione: nome del lavoro;
    province: zona da selezionare (predefinita Milano e dintorni)."""
    stats: Counter = Counter()
    with connect() as conn:
        finish = log_job(conn, job_name(versione))
        # Memoria limitata (512 MB su Render): prima solo id e chiave del mercato, poi gli annunci a blocchi
        # senza testi, nell'ordine marca/modello/carburante, tenendo in memoria UN solo mercato alla volta.
        where = """status='attivo' AND price_eur BETWEEN 500 AND %s AND seller_type <> 'commerciante'
                  AND (province = ANY(%s) OR (source='facebook' AND %s))
                  AND (year IS NULL OR year >= %s)
                  AND (stage <> 'scartato' OR stage_reason IN ('margine_insufficiente','troppo_vecchia'))
                  AND (%s::text IS NULL OR source = %s)"""
        params = (settings.max_purchase_eur, list(province or PROVINCES), province is None, scovo.ANNO_MINIMO, fonte, fonte)
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM listings WHERE {where} ORDER BY make, model, fuel, id", params).fetchall()]
        stats["esaminati"] = len(ids)
        cols = LIGHT_COLS.replace("description", "NULL AS description")
        cur_key, cur_market = None, []
        model = load_active(conn)
        out = []
        BLOCCO = 500
        for i in range(0, len(ids), BLOCCO):
            chunk = conn.execute(f"SELECT {cols}, stage_reason FROM listings WHERE id = ANY(%s) "
                                 "ORDER BY make, model, fuel, id", (ids[i:i + BLOCCO],)).fetchall()
            for r in chunk:
                l = row_to_listing(r)
                if check_consistency(l, fix=True) or "dati_incoerenti" in l.price_flags:
                    stats["dati_incoerenti"] += 1
                    continue
                km_est = estimate_missing_km(l)
                if not (l.make and l.model and l.year and l.mileage_km is not None) or l.year < scovo.ANNO_MINIMO:
                    stats["dati_mancanti_o_vecchia"] += 1
                    continue
                key = (l.make, l.model, l.fuel)
                if key != cur_key:
                    cur_key, cur_market = key, load_market(conn, *key)
                saved = l.damage_class
                l.damage_class = "nessuno"
                v = value_listing(l, cur_market)
                l.damage_class = saved
                from_model = False
                if weak(v):
                    from_model = apply_model(v, l, model)
                    if not from_model:
                        stats["stima_poco_solida"] += 1
                        continue
                mercato = v.private_median or v.resale_median_private or v.resale_median
                if not mercato:
                    stats["senza_mercato"] += 1
                    continue
                sito = round(mercato * (1 - scovo.SCONTO_DEFAULT / 100)) - l.price_eur - sum(scovo.COSTI_FISSI.values())
                soglia = scovo.soglia(l.price_eur)
                if sito < soglia:
                    stats["sotto_soglia"] += 1
                    continue
                # solo numeri: l'annuncio completo si rilegge dopo, per i migliori
                out.append((sito / soglia, r["id"], r["source"], mercato, sito, soglia, from_model, km_est,
                            v.n_comparables, list(v.fraud_flags or [])))
        cur_market = []
        out.sort(key=lambda x: -x[0])
        stats["sopra_soglia"] = len(out)
        top = out[: int(QUANTI * 1.15)]
        full = {r["id"]: r for r in conn.execute(
            f"SELECT {LIGHT_COLS}, stage_reason FROM listings WHERE id = ANY(%s)", ([x[1] for x in top],)).fetchall()}
        # prima di guardarli: esistono ancora? (solo Subito: il controllo Facebook costa)
        stats.update(verify_rows(conn, [full[x[1]] for x in top if x[2] == "subito" and x[1] in full]))
        alive = {x["id"] for x in conn.execute(
            "SELECT id FROM listings WHERE id = ANY(%s) AND status='attivo'", ([x[1] for x in top],)).fetchall()}
        top = [x for x in top if x[1] in alive and x[1] in full][:QUANTI]

        class _V:
            pass

        def _expand(x):
            score, lid, _src, mercato, sito, soglia, from_model, km_est, ncomp, flags = x
            r = full[lid]
            l = row_to_listing(r)
            check_consistency(l, fix=True)
            v = _V()
            v.n_comparables, v.fraud_flags = ncomp, flags
            return score, r, l, v, mercato, sito, soglia, from_model, km_est
        top = [_expand(x) for x in top]
        for score, r, l, v, mercato, sito, soglia, from_model, km_est in top:
            lid = r["id"]
            val = conn.execute("SELECT parts_cost_low, parts_cost_high FROM valuations WHERE listing_id=%s "
                               "ORDER BY created_at DESC LIMIT 1", (lid,)).fetchone() or {}
            rec = {"id": lid, "fonte": r["source"], "url": r["url"], "titolo": l.title,
                   "descrizione": (l.description or "")[:900], "marca": l.make, "modello": l.model,
                   "versione": l.version_raw, "anno": l.year, "km": l.mileage_km, "km_stimati": km_est,
                   "carburante": l.fuel, "cambio": l.gearbox, "kw": l.power_kw, "citta": l.city,
                   "provincia": l.province, "prezzo": l.price_eur, "mercato": mercato,
                   "confronti": v.n_comparables, "stima": "modello" if from_model else "confronti",
                   "guadagno_prima_ricambi": sito, "soglia": soglia, "stato_attuale": r.get("stage"),
                   "esito_attuale": r.get("stage_reason"), "danno_attuale": r.get("damage_class"),
                   "ricambi_attuali": [val.get("parts_cost_low"), val.get("parts_cost_high")],
                   "segnali": v.fraud_flags}
            log.info("MILLE %s", json.dumps(rec, ensure_ascii=False, default=str))
            imgs = []
            n_ph = conn.execute("SELECT count(*) AS n FROM listing_photos WHERE listing_id=%s", (lid,)).fetchone()["n"]
            for pos in range(min(n_ph, 4)):
                try:
                    c = foto.fetch_one(conn, lid, pos)
                except Exception:
                    c = None
                if c:
                    imgs.append(c)
            sheet = foglio(imgs, f"{lid} {l.make} {l.model} {l.year} {l.price_eur}e")
            if sheet:
                b = base64.b64encode(sheet).decode()
                parts = [b[i:i + PEZZO] for i in range(0, len(b), PEZZO)]
                for k, p in enumerate(parts, 1):
                    log.info("MILLEFOTO %s %d/%d %s", lid, k, len(parts), p)
                stats["con_foto"] += 1
            else:
                stats["senza_foto"] += 1
        stats["esportati"] = len(top)
        finish(True, dict(stats))
    stats["versione"] = versione or VERSIONE
    log.info("MILLE_RIEPILOGO %s", json.dumps(dict(stats)))
    return dict(stats)
