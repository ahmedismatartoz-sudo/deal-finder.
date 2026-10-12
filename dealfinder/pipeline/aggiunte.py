"""Annunci aggiunti a mano (data/annunci_aggiunti.json): auto segnalate da noi, non trovate dalla raccolta.

Ogni voce ha i dati dell'annuncio, le foto (file in data/aggiunte/, targhe già coperte, nessun numero di telefono
nel testo) e l'analisi come in analisi_manuale.json (esito, ricambi, nota, controlli). All'avvio del sito e ogni
30 minuti: l'annuncio si salva o si aggiorna, le foto vanno nella cache, l'analisi si importa, e l'auto resta
"vista di recente" fino alla data "fino_al" (dopo sparisce da sola).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from ..core.models import Listing
from ..core.normalize import normalize_fields
from ..db import connect, upsert_listing
from ..web import foto

log = logging.getLogger("aggiunte")
BASE = Path(__file__).resolve().parents[2] / "data"
FILE = BASE / "annunci_aggiunti.json"
CAMPI = ("source", "source_id", "url", "title", "description", "make", "model", "version_raw", "year", "mileage_km",
         "fuel", "gearbox", "power_kw", "price_eur", "seller_type", "city", "province", "region")


def _voci() -> list[dict]:
    try:
        return json.loads(FILE.read_text()) if FILE.exists() else []
    except ValueError:
        log.warning("annunci_aggiunti.json non valido")
        return []


def _attiva(v: dict) -> bool:
    return not v.get("fino_al") or dt.date.fromisoformat(v["fino_al"]) >= dt.date.today()


def run() -> dict:
    """Salva gli annunci aggiunti e importa le loro analisi. Ritorna {source_id: esito}."""
    from . import manuale
    out = {}
    for v in _voci():
        a = v["annuncio"]
        foto_files = [BASE / "aggiunte" / f for f in v.get("foto") or []]
        l = Listing(**{k: a[k] for k in CAMPI if k in a})
        l.photos = [f"aggiunta:{f.name}" for f in foto_files]
        normalize_fields(l)
        for k in ("make", "model"):            # marca e modello scelti a mano restano quelli
            if a.get(k):
                setattr(l, k, a[k])
        with connect() as conn:
            lid, _ = upsert_listing(conn, l)
            stato = "attivo" if _attiva(v) else "scomparso"
            conn.execute("UPDATE listings SET status=%s, last_seen_at=now(), last_checked_at=now() WHERE id=%s",
                         (stato, lid))
            conn.commit()
            for pos, f in enumerate(foto_files):
                if f.exists():
                    foto.save(conn, lid, pos, f.read_bytes())
        if stato == "attivo":
            stats = manuale.run(voci=[dict(v["analisi"], id=lid, aggiorna=True)])
            with connect() as conn:
                r = conn.execute("""SELECT l.stage_reason, v.private_median, v.resale_median_private, v.parts_cost_high
                                    FROM listings l LEFT JOIN LATERAL (SELECT * FROM valuations WHERE listing_id=l.id
                                    ORDER BY created_at DESC LIMIT 1) v ON true WHERE l.id=%s""", (lid,)).fetchone()
                conn.execute("UPDATE listings SET last_checked_at=now() WHERE id=%s", (lid,))
                conn.commit()
            out[l.source_id] = {"id": lid, "esito": r and r["stage_reason"], "mercato": r and r["private_median"],
                                "ricambi": r and r["parts_cost_high"], "manuale": stats}
        else:
            out[l.source_id] = {"id": lid, "esito": "scaduta"}
    log.info("AGGIUNTE %s", json.dumps(out, default=str))
    return out


def tieni_visibili(conn) -> int:
    """Ogni 30 minuti: le aggiunte ancora valide restano "verificate di recente" (non hanno una raccolta che le rivede)."""
    ids = [str(v["annuncio"]["source_id"]) for v in _voci() if _attiva(v)]
    if not ids:
        return 0
    n = conn.execute("UPDATE listings SET last_seen_at=now(), last_checked_at=now() WHERE source_id = ANY(%s) "
                     "AND status='attivo'", (ids,)).rowcount
    conn.commit()
    return n
