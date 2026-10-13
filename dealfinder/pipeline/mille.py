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
from pathlib import Path
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
QUANTI = int(os.environ.get("MILLE_QUANTI", "1000"))      # per as5k: MILLE_QUANTI dal servizio (es. 500)
# selezione prudente (MILLE_PESSIMISTA=1): mercato realistico × MILLE_PESSIMISMO (0,95)
PESSIMISTA = os.environ.get("MILLE_PESSIMISTA", "0") == "1"
PESSIMISMO = float(os.environ.get("MILLE_PESSIMISMO", "0.95"))
# quota massima di diesel tra le auto scelte (MILLE_MAX_DIESEL=0.3 → 30%), 1 = nessun limite
MAX_DIESEL = float(os.environ.get("MILLE_MAX_DIESEL", "1"))
PROVINCES = ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV"]     # Milano e dintorni (selezione di sempre)
CELLA = (270, 200)
PEZZO = 12000


def _gia_controllate() -> list[int]:
    """Auto già guardate una per una (data/analisi_manuale.json): non si ripropongono, così ogni nuova
    selezione porta auto nuove. MILLE_RIVEDI=1 le riprende tutte."""
    if os.environ.get("MILLE_RIVEDI") == "1":
        return []
    try:
        p = Path(__file__).resolve().parents[2] / "data" / "analisi_manuale.json"
        return [int(x["id"]) for x in json.loads(p.read_text()) if x.get("id") is not None]
    except Exception:
        return []


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


def run(fonte: str | None = None, versione: str | None = None, province: list[str] | None = None,
        ovunque: bool = False, danneggiate: bool = False, max_prezzo: int | None = None,
        min_prezzo: int = 500, anche_scartate: bool = False, promettenti: bool = False,
        quanti: int | None = None) -> dict:
    """fonte: solo gli annunci di quel sito (es. "autoscout24"); versione: nome del lavoro;
    province: zona da selezionare (predefinita Milano e dintorni);
    max_prezzo/min_prezzo: fascia di prezzo; anche_scartate: riprende anche le auto scartate per danni
    (auto economiche sane o incidentate: decide il controllo delle foto)."""
    stats: Counter = Counter()
    with connect() as conn:
        finish = log_job(conn, job_name(versione))
        # Memoria limitata (512 MB su Render): prima solo id e chiave del mercato, poi gli annunci a blocchi
        # senza testi, nell'ordine marca/modello/carburante, tenendo in memoria UN solo mercato alla volta.
        where = """status='attivo' AND price_eur BETWEEN %s AND %s AND seller_type <> 'commerciante'
                  AND (%s OR province = ANY(%s) OR (source='facebook' AND %s))
                  AND (NOT %s OR damage_declared OR problem_search OR damage_class IN ('leggero','medio','grave'))
                  AND (year IS NULL OR year >= %s)
                  AND (stage <> 'scartato' OR stage_reason IN ('margine_insufficiente','troppo_vecchia') OR %s OR %s)
                  AND (%s::text IS NULL OR source = %s)
                  AND NOT (id = ANY(%s))
                  AND (NOT %s OR COALESCE(prescreen, '') <> 'non_interessante')"""
        if os.environ.get("MILLE_SOLO_INTERESSANTI") == "1":
            # selezione veloce: solo gli annunci già segnati "interessanti" dal primo filtro della raccolta
            where += " AND prescreen = 'interessante' AND last_seen_at > now() - interval '3 days'"
        params = (min_prezzo, max_prezzo or settings.max_purchase_eur, ovunque, list(province or PROVINCES), province is None and not ovunque,
                  danneggiate, scovo.ANNO_MINIMO, danneggiate, anche_scartate, fonte, fonte, _gia_controllate(), promettenti)
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM listings WHERE {where} ORDER BY make, model, fuel, id", params).fetchall()]
        stats["esaminati"] = len(ids)
        cols = LIGHT_COLS.replace("description", "NULL AS description")
        cur_key, cur_market = None, []
        carb: dict = {}
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
                if scovo.troppi_km(l.mileage_km):
                    stats["oltre_km_massimo"] += 1
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
                if PESSIMISTA:
                    # stima prudente: mercato realistico (meno confronti = più sconto, tetto sul prezzo) e un taglio in più
                    mercato = round(scovo.mercato_realistico(float(mercato), l.price_eur, v.n_comparables) * PESSIMISMO)
                sito = round(mercato * (1 - scovo.SCONTO_DEFAULT / 100)) - l.price_eur - sum(scovo.COSTI_FISSI.values())
                soglia = scovo.soglia(l.price_eur)
                if sito < soglia:
                    stats["sotto_soglia"] += 1
                    continue
                # solo numeri: l'annuncio completo si rilegge dopo, per i migliori
                out.append((sito / soglia, r["id"], r["source"], mercato, sito, soglia, from_model, km_est,
                            v.n_comparables, list(v.fraud_flags or [])))
                carb[r["id"]] = l.fuel
        cur_market = []
        out.sort(key=lambda x: -x[0])
        stats["sopra_soglia"] = len(out)
        if MAX_DIESEL < 1:
            # al massimo MAX_DIESEL (es. 30%) di diesel tra i scelti: il resto benzina, GPL, ibride
            n_tot = int((quanti or QUANTI) * 1.15)
            tetto = int(n_tot * MAX_DIESEL)
            scelti, n_d = [], 0
            for x in out:
                if carb.get(x[1]) == "diesel":
                    if n_d >= tetto:
                        continue
                    n_d += 1
                scelti.append(x)
                if len(scelti) >= n_tot:
                    break
            out = scelti
            stats["diesel_scelti"] = n_d
        top = out[: int((quanti or QUANTI) * 1.15)]
        full = {r["id"]: r for r in conn.execute(
            f"SELECT {LIGHT_COLS}, stage_reason FROM listings WHERE id = ANY(%s)", ([x[1] for x in top],)).fetchall()}
        # prima di guardarli: esistono ancora? (solo Subito: il controllo Facebook costa)
        stats.update(verify_rows(conn, [full[x[1]] for x in top if x[2] == "subito" and x[1] in full]))
        alive = {x["id"] for x in conn.execute(
            "SELECT id FROM listings WHERE id = ANY(%s) AND status='attivo'", ([x[1] for x in top],)).fetchall()}
        top = [x for x in top if x[1] in alive and x[1] in full][:(quanti or QUANTI)]

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


import re as _re

# moto, scooter e quad: il modello dei prezzi li valuta come auto (mercato falso), si tolgono prima
_MOTO = _re.compile(
    r"\b(moto|motocicl\w*|scooter|motorino|ciclomotore|quad|motard|enduro|supermotard|minicar|"
    r"ducati|ktm|kymco|yamaha|kawasaki|piaggio|vespa|aprilia|harley|triumph|husqvarna|benelli|"
    r"mv agusta|moto guzzi|gilera|malaguti|keeway|zontes|voge|cf ?moto|royal enfield|"
    r"cbr\s?\d*|crf\s?\d*|cb\s?\d{3}|sh\s?\d{2,3}|x-?adv|adv\s?350|transalp|africa twin|pcx|nc\s?7\d0|hornet|"
    r"r\s?1[0-2]\d0\s?(gs|rt|r)?|f\s?[789]\d0\s?(r|gs)?|k\s?1[2-6]00|s\s?1000\s?rr?|"
    r"\d{3,4}\s?(rt|gs)|xp\s?400\w*|burgman|v-?strom|gsx\w*|t-?max|x-?max|mt-?\s?0\d|tracer|ninja|z\s?[0-9]{3,4})\b", _re.I)


def e_moto(*testi) -> bool:
    return bool(_MOTO.search(" ".join(t for t in testi if t)))


def lampo(quanti: int = 200, max_diesel: float = 0.3, fonte: str | None = "facebook",
          ore: int = 96, danneggiate: bool = False, ovunque: bool = False,
          versione: str = "fb-lampo") -> int:
    """Selezione lampo: niente ricalcolo del mercato (troppo lento col database pieno), si usa la stima del
    primo filtro già salvata in raccolta (model_p50, prescreen_potential). Preferisce le auto con pochi km,
    toglie moto/scooter, privati soltanto, mai le auto già guardate a mano.
    Scrive MILLE / MILLEFOTO come run(), poi MILLE_RIEPILOGO."""
    from ..zone import MILANO
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT {LIGHT_COLS}, stage_reason, model_p50, prescreen_potential FROM listings
                WHERE status='attivo' AND COALESCE(seller_type,'') <> 'commerciante'
                  AND (%s::text IS NULL OR source = %s)
                  AND (%s OR prescreen='interessante')
                  AND COALESCE(prescreen_potential, 0) > 0
                  AND (NOT %s OR damage_declared OR damage_class IN ('leggero','medio','grave','alto_rischio'))
                  AND last_seen_at > now() - make_interval(hours => %s)
                  AND (%s OR province = ANY(%s) OR (province IS NULL AND source='facebook'))
                  AND price_eur BETWEEN 400 AND 40000
                  AND (mileage_km IS NULL OR mileage_km <= %s)
                  AND (year IS NULL OR year >= %s)
                  AND NOT (id = ANY(%s))
                ORDER BY COALESCE(prescreen_potential, 0)
                         + CASE WHEN mileage_km < 80000 THEN 2000 WHEN mileage_km < 120000 THEN 900 ELSE 0 END
                         + CASE WHEN province = ANY(%s) THEN 1500 ELSE 0 END DESC
                LIMIT %s""",
            (fonte, fonte, danneggiate, danneggiate, ore, ovunque, list(MILANO), scovo.KM_MASSIMO,
             scovo.ANNO_MINIMO, _gia_controllate(), list(MILANO), quanti * 4)).fetchall()
        scelti, n_d, n_moto = [], 0, 0
        for r in rows:
            if e_moto(r.get("title"), (r.get("description") or "")[:300], r.get("model"), r.get("make")):
                n_moto += 1
                continue
            if r.get("fuel") == "diesel":
                if n_d >= int(quanti * max_diesel):
                    continue
                n_d += 1
            scelti.append(r)
            if len(scelti) >= quanti:
                break
        for r in scelti:
            l = row_to_listing(r)
            lid = r["id"]
            mercato = int(r.get("model_p50") or 0)
            rec = {"id": lid, "fonte": r["source"], "url": r["url"], "titolo": l.title,
                   "descrizione": (l.description or "")[:900], "marca": l.make, "modello": l.model,
                   "versione": l.version_raw, "anno": l.year, "km": l.mileage_km, "carburante": l.fuel,
                   "cambio": l.gearbox, "citta": l.city, "provincia": l.province, "prezzo": l.price_eur,
                   "mercato": mercato, "stima": "modello", "guadagno_prima_ricambi": int(r.get("prescreen_potential") or 0),
                   "soglia": scovo.soglia(l.price_eur or 0), "danno_attuale": r.get("damage_class")}
            log.info("MILLE %s", json.dumps(rec, ensure_ascii=False, default=str))
            imgs = []
            for pos in range(4):
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
        log.info("MILLE_RIEPILOGO %s", json.dumps({"candidati": len(rows), "esportati": len(scelti), "moto_tolte": n_moto,
                                                  "diesel": n_d, "versione": versione}))
        return len(scelti)
