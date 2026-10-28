"""28/10 sera (cliente: "non voglio più margini gonfiati"): l'AI legge la descrizione dell'auto sul sito e trova i
difetti DICHIARATI dal venditore ("segnalo...", "da sistemare", spia accesa, frizione da fare, ammaccature...).

Risposta su una riga:  OK  |  DIFETTO <euro> <motivo>  |  GRAVE <motivo>
- DIFETTO: costo stimato della riparazione per un commerciante, tolto dal margine sul sito;
- GRAVE: auto da non proporre (non marciante, motore/cambio rotti, alluvionata, strutturale, senza documenti).
Cache in ai_difetti (stessa descrizione = nessuna nuova chiamata). Tetto di spesa: AI_BUDGET_GIORNO.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re

from . import client as AI

log = logging.getLogger("difetti")

SYSTEM = """Sei un commerciante di auto usate italiano. Leggi l'annuncio di un privato e cerca SOLO i difetti o problemi
che il venditore dichiara (meccanici, carrozzeria, interni, spie, revisione, documenti, incidenti, lavori da fare).
Non inventare: se il testo non dichiara problemi, rispondi OK. Lavori già fatti o manutenzione eseguita NON sono difetti.
Rispondi con UNA sola riga, niente altro:
OK
oppure DIFETTO <costo stimato in euro per un commerciante, numero intero> <motivo in max 10 parole>
oppure GRAVE <motivo in max 10 parole>   (non marciante, motore o cambio da rifare, alluvionata, danni strutturali,
senza documenti, oppure riparazione che costa più del 30% del prezzo)"""


def _tabella(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS ai_difetti (
        listing_id BIGINT PRIMARY KEY, chiave TEXT, costo INTEGER, grave BOOLEAN DEFAULT false, motivo TEXT,
        computed_at TIMESTAMPTZ DEFAULT now())""")


def leggi(conn, r: dict) -> dict | None:
    """r: id, title, description, price_eur, year, mileage_km. Ritorna {costo, grave, motivo} o None."""
    desc = (r.get("description") or "").strip()
    if not desc or not AI.available():
        return None
    chiave = hashlib.sha1(desc[:3000].encode()).hexdigest()[:16]
    _tabella(conn)
    vecchia = conn.execute("SELECT chiave, costo, grave, motivo FROM ai_difetti WHERE listing_id=%s", (r["id"],)).fetchone()
    if vecchia and vecchia["chiave"] == chiave:
        return dict(vecchia)
    if AI.budget_finito(conn):
        return None
    resp = AI.crea(model=AI.MODEL_FAST, max_tokens=120, system=SYSTEM,
                   messages=[{"role": "user", "content": f"AUTO: {r.get('title') or ''} | anno {r.get('year')} | km {r.get('mileage_km')} | "
                                                         f"prezzo {r.get('price_eur')} €\nDESCRIZIONE: {desc[:2500]}"}])
    testo = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()
    try:
        AI.db_usage_sink(conn)("difetti_annuncio", AI.AIResult(data=None, model=AI.MODEL_FAST, input_tokens=resp.usage.input_tokens,
                                                                output_tokens=resp.usage.output_tokens), r["id"])
    except Exception:
        pass
    costo, grave, motivo = 0, False, None
    riga = testo.splitlines()[0] if testo else ""
    m = re.match(r"\s*\**\s*DIFETTO\**\s*:?\s*(\d[\d.]*)\s*(?:€|euro)?\s*(.*)", riga, re.I)
    if m:
        costo, motivo = int(m.group(1).replace(".", "")), m.group(2).strip()[:120]
    elif re.match(r"\s*\**\s*GRAVE", riga, re.I):
        grave, motivo = True, re.sub(r"^\s*\**\s*GRAVE\**\s*:?\s*", "", riga, flags=re.I)[:120]
    elif not re.match(r"\s*\**\s*OK", riga, re.I):
        log.warning("DIFETTI %s risposta non leggibile: %s", r["id"], riga[:120])
        return None
    conn.execute("""INSERT INTO ai_difetti (listing_id, chiave, costo, grave, motivo, computed_at) VALUES (%s,%s,%s,%s,%s,now())
                    ON CONFLICT (listing_id) DO UPDATE SET chiave=EXCLUDED.chiave, costo=EXCLUDED.costo, grave=EXCLUDED.grave,
                    motivo=EXCLUDED.motivo, computed_at=now()""", (r["id"], chiave, costo, grave, motivo))
    conn.commit()
    if costo or grave:
        log.warning("DIFETTI %s %s: %s", r["id"], "GRAVE" if grave else f"{costo} €", motivo)
    return {"costo": costo, "grave": grave, "motivo": motivo}


def giro(ids: list[int]) -> dict:
    from ..db import connect
    st = {"letti": 0, "difetti": 0, "gravi": 0, "errori": 0}
    if not ids:
        return st
    with connect() as conn:
        _tabella(conn)
        conn.commit()
        rows = conn.execute("SELECT id, title, description, price_eur, year, mileage_km FROM listings WHERE id = ANY(%s)", (ids,)).fetchall()
        for r in rows:
            try:
                d = leggi(conn, dict(r))
                if d is None:
                    continue
                st["letti"] += 1
                st["difetti"] += bool(d.get("costo"))
                st["gravi"] += bool(d.get("grave"))
            except Exception as e:
                conn.rollback()
                st["errori"] += 1
                if st["errori"] < 3:
                    log.warning("DIFETTI %s non letto: %s", r["id"], str(e)[:160])
    log.warning("DIFETTI_GIRO %s", st)
    return st
