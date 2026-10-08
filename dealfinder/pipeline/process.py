"""Filtro veloce e approfondimento dei candidati.

    python -m dealfinder.pipeline.process          # filtro + approfondimento
    python -m dealfinder.pipeline.process filtro
    python -m dealfinder.pipeline.process approfondimento

Stati di un annuncio (colonna stage):
  nuovo → scartato | attesa_mercato | candidato → approfondito | errore
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from dataclasses import asdict

from ..ai import analyze, parts_agent, plate
from ..ai.damage import EXCLUDE_FLAGS
from ..ai.client import AIError, db_usage_sink
from ..config import settings
from ..core.models import DamageItem
from ..core.normalize import find_plate, plate_hash
from ..db import connect, log_job
from ..pricing import report
from ..pricing.engine import EXCLUDE_FLAGS, value_listing
from ..pricing.margin import DealerCosts, compute_margin
from .verify import verify_rows
from ..store import (load_market, photos_of, row_to_listing, save_valuation, set_stage,
                     update_listing_fields)

log = logging.getLogger("process")

OPPORTUNITY_PROVINCES = ("MI", "MB", "BG", "BS")
BATCH = int(os.environ.get("PROCESS_BATCH", "400"))
DEEP_BATCH = int(os.environ.get("DEEP_BATCH", "60"))


def _to_items(dicts):
    return [DamageItem(**{k: v for k, v in d.items() if k in DamageItem.__dataclass_fields__}) for d in dicts]


def quick_potential(listing, market) -> tuple[bool, str, object]:
    """Stima rapida senza AI: vale la pena guardare le foto?"""
    saved_class = listing.damage_class
    listing.damage_class = "nessuno"        # stima come se fosse sana, solo per il filtro
    v = value_listing(listing, market)
    listing.damage_class = saved_class
    if v.resale_median is None:
        return False, "nessun_confronto", v
    costs = DealerCosts()
    threshold = costs.threshold_low_eur if listing.price_eur < costs.threshold_split_eur else costs.threshold_high_eur
    fixed = costs.transport_eur + costs.paperwork_eur + costs.preparation_eur + costs.warranty_reserve_eur
    potential = v.resale_median - listing.price_eur - fixed
    # Margine teorico generoso: se nemmeno così supera il 70% della soglia, si scarta
    if potential < 0.7 * threshold:
        return False, "margine_insufficiente", v
    return True, "potenziale", v


def screen(conn) -> Counter:
    stats: Counter = Counter()
    sink = db_usage_sink(conn)
    rows = conn.execute(
        """SELECT * FROM listings WHERE stage IN ('nuovo','attesa_mercato') AND status='attivo'
             AND price_eur IS NOT NULL AND price_eur <= %s
             AND (province = ANY(%s) OR source='facebook')
           ORDER BY first_seen_at DESC LIMIT %s""",
        (settings.max_purchase_eur, list(OPPORTUNITY_PROVINCES), BATCH)).fetchall()
    market_cache: dict = {}
    for row in rows:
        lid = row["id"]
        l = row_to_listing(row, photos_of(conn, lid))
        try:
            if l.seller_type == "commerciante":
                # Si compra da privati: gli annunci dei concessionari servono solo alla base dei prezzi
                set_stage(conn, lid, "mercato", "venditore_commerciante")
                stats["solo_mercato_commerciante"] += 1
                continue
            if set(l.price_flags) & EXCLUDE_FLAGS:
                set_stage(conn, lid, "scartato", "prezzo_non_affidabile")
                stats["scartato_prezzo"] += 1
                continue
            # 1. testo (AI economica): completa dati mancanti, danni dichiarati
            text = analyze.extract_text(l, sink, lid) if (l.description or l.missing_fields) else {}
            origin = analyze.apply_extract(l, text)
            if set(text.get("severe_flags") or []) & set(EXCLUDE_FLAGS):
                l.damage_class = "grave"
                update_listing_fields(conn, lid, l)
                set_stage(conn, lid, "scartato", "danno_grave_dichiarato", ai_extract=text)
                stats["scartato_grave"] += 1
                continue
            if not (l.make and l.model and l.year and l.mileage_km is not None):
                update_listing_fields(conn, lid, l)
                set_stage(conn, lid, "scartato", "dati_essenziali_mancanti", ai_extract=text)
                stats["scartato_dati"] += 1
                continue
            # 2. stima rapida dal mercato
            key = (l.make, l.model, l.fuel)
            if key not in market_cache:
                market_cache[key] = load_market(conn, *key)
            ok, why, _ = quick_potential(l, market_cache[key])
            if not ok:
                update_listing_fields(conn, lid, l)
                set_stage(conn, lid, "attesa_mercato" if why == "nessun_confronto" else "scartato", why,
                          ai_extract=text)
                stats[why] += 1
                continue
            # 3. foto (AI economica, 3 foto)
            photos = analyze.analyze_photos(l, deep=False, usage_sink=sink, listing_id=lid)
            cls, items, severe = analyze.merge_damage(text, photos)
            l.damage_class, l.damage_items = cls, _to_items(items)
            update_listing_fields(conn, lid, l)
            if cls == "grave":
                set_stage(conn, lid, "scartato", "danno_grave", photo_screen=photos, ai_extract=text)
                stats["scartato_grave"] += 1
            elif photos.get("stock_or_internet_photo"):
                set_stage(conn, lid, "scartato", "foto_non_originali", photo_screen=photos, ai_extract=text)
                stats["scartato_foto"] += 1
            else:
                set_stage(conn, lid, "candidato", why, photo_screen=photos, ai_extract=text)
                stats["candidato"] += 1
            conn.execute("UPDATE listings SET field_origin=%s WHERE id=%s", (json.dumps(origin), lid))
        except AIError as e:
            log.error("AI non disponibile: %s", e)
            stats["errore_ai"] += 1
            break
        except Exception:
            log.exception("filtro fallito per annuncio %s", lid)
            set_stage(conn, lid, "errore", "filtro")
            stats["errore"] += 1
        finally:
            conn.execute("UPDATE listings SET screened_at=now() WHERE id=%s", (lid,))
            conn.commit()
    return stats


def deep(conn) -> Counter:
    stats: Counter = Counter()
    sink = db_usage_sink(conn)
    cache_get, cache_put = parts_agent.db_cache(conn)
    rows = conn.execute("SELECT * FROM listings WHERE stage='candidato' AND status='attivo' "
                        "ORDER BY first_seen_at DESC LIMIT %s", (DEEP_BATCH,)).fetchall()
    # Prima di spendere in analisi: l'annuncio esiste ancora?
    stats.update(verify_rows(conn, rows))
    alive = {r["id"] for r in conn.execute(
        "SELECT id FROM listings WHERE id = ANY(%s) AND status='attivo' AND last_checked_at > now() - interval '2 hours'",
        ([r["id"] for r in rows],)).fetchall()}
    rows = [r for r in rows if r["id"] in alive]
    for row in rows:
        lid = row["id"]
        l = row_to_listing(row, photos_of(conn, lid))
        text = row.get("ai_extract") or {}
        try:
            photos = analyze.analyze_photos(l, deep=True, usage_sink=sink, listing_id=lid)
            cls, items, severe = analyze.merge_damage(text, photos)
            if cls == "grave":
                l.damage_class, l.damage_items = cls, _to_items(items)
                update_listing_fields(conn, lid, l)
                set_stage(conn, lid, "scartato", "danno_grave_approfondimento", photo_screen=photos)
                stats["scartato_grave"] += 1
                conn.commit()
                continue
            l.damage_class, l.damage_items = cls, _to_items(items)

            # targa: solo per identificare il modello; si conserva solo l'hash
            plate_txt = (photos.get("plate_text") or "").replace(" ", "").upper() or \
                find_plate(" ".join(filter(None, [l.title, l.description])))
            plate_data = plate.lookup(plate_txt) if plate_txt else None
            if plate_txt:
                conn.execute("UPDATE vehicles SET plate_hash=COALESCE(plate_hash,%s) "
                             "WHERE id=(SELECT vehicle_id FROM listings WHERE id=%s)",
                             (plate_hash(plate_txt, settings.plate_salt), lid))
            identity = analyze.identify_vehicle(l, photos, plate_data, sink, lid)

            parts = None
            if items:
                by_type = {}
                for pref in ("aftermarket", "originale", "usato"):
                    by_type[pref] = parts_agent.estimate_parts(identity, items, l.year, pref,
                                                               cache_get, cache_put, sink, lid)
                parts = dict(by_type["aftermarket"])
                parts["by_type"] = {k: {"low": v["parts_cost_low"], "high": v["parts_cost_high"],
                                        "complete": v["complete"]} for k, v in by_type.items()}

            market = load_market(conn, l.make, l.model, l.fuel)
            v = value_listing(l, market, catalog_confidence=identity.get("confidence"))
            m = compute_margin(l, v, DealerCosts(), parts["parts_cost_high"] if parts else None)
            if parts and not parts["complete"] and m:
                m.notes.append("ricambi_non_tutti_prezzati")
                if m.status == "opportunita":
                    m.status = "da_verificare"
            mot = report.motivation(l, v, parts)
            chk = report.checks(l, v, parts, severe)
            update_listing_fields(conn, lid, l)
            save_valuation(conn, lid, v, parts, mot, chk, asdict(m) if m else None)
            set_stage(conn, lid, "approfondito", m.status if m else "non_valutabile",
                      photo_screen=photos, vehicle_identity=identity)
            conn.execute("UPDATE listings SET deep_at=now() WHERE id=%s", (lid,))
            stats[m.status if m else "non_valutabile"] += 1
        except AIError as e:
            log.error("AI non disponibile: %s", e)
            stats["errore_ai"] += 1
            break
        except Exception:
            log.exception("approfondimento fallito per annuncio %s", lid)
            set_stage(conn, lid, "errore", "approfondimento")
            stats["errore"] += 1
        finally:
            conn.commit()
    return stats


def run(what: str = "tutto") -> dict:
    out = {}
    with connect() as conn:
        finish = log_job(conn, f"process:{what}")
        try:
            if what in ("tutto", "filtro"):
                out["filtro"] = dict(screen(conn))
            if what in ("tutto", "approfondimento"):
                out["approfondimento"] = dict(deep(conn))
            finish(True, out)
        except Exception as e:
            finish(False, {**out, "errore": str(e)})
            raise
    log.info("process %s: %s", what, out)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run(sys.argv[1] if len(sys.argv) > 1 else "tutto")
