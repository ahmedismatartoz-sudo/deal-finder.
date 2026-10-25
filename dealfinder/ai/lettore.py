"""Agente "lettore di annunci" (25/10, cliente: "un'AI legge titolo e descrizione e tira fuori i dati esatti").

Per ogni auto candidata (quelle che i calcoli considerano per il sito, prima le nuove e le più promettenti)
un modello veloce legge titolo + descrizione e restituisce i dati che servono al confronto "auto uguali":
versione, kW/CV, cilindrata, cambio, allestimento, carrozzeria, se il prezzo è vero o è una rata/anticipo,
se l'auto ha danni o guasti dichiarati.

Cosa cambia nell'annuncio (solo con dati scritti, mai inventati):
  - potenza e cambio: riempiti se mancano; la potenza viene corretta se l'annuncio la scrive diversa (>10%)
  - cilindrata: aggiunta alla versione se non c'è, così i confronti trovano lo stesso motore
  - prezzo non vero (rata, anticipo, prezzo civetta): price_flags "rata" → l'auto non va sul sito
  - danni o guasti dichiarati: damage_declared → l'auto non va sul sito come affare
Poi l'auto si rivaluta subito con i dati puliti.

Costo: modello veloce, solo testo (~1.500 token per auto): qualche decimo di centesimo per auto.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter

log = logging.getLogger("lettore")

SISTEMA = """Sei un esperto di auto usate in Italia. Leggi un annuncio e restituisci SOLO un oggetto JSON.
Non inventare: se un dato non è scritto nell'annuncio (titolo o descrizione) usa null.
Schema:
{"versione": str|null,            // versione/motore come scritta, es. "1.5 EcoBlue 120 CV Titanium"
 "cilindrata": str|null,          // es. "1.5" (un decimale)
 "kw": int|null, "cv": int|null,  // potenza se scritta (kW o CV)
 "cambio": "manuale"|"automatico"|null,
 "allestimento": str|null,        // es. "Titanium", "Lounge", "GT Line"
 "carrozzeria": "berlina"|"sw"|"suv"|"monovolume"|"coupe"|"cabrio"|"furgone"|"pickup"|null,
 "anno": int|null, "km": int|null,
 "prezzo_vero": true|false,       // false se il prezzo indicato è una rata, un anticipo, un prezzo "da" o non è il prezzo dell'auto
 "prezzo_nota": str|null,         // es. "rata mensile", "anticipo", "più IVA", "trattabile"
 "danni": true|false,             // true se l'annuncio dichiara danni, incidenti, guasti, spie accese, motore/cambio da rifare
 "danni_nota": str|null}"""


def _schema_sql(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS lettura_ai (
        listing_id BIGINT PRIMARY KEY, prezzo INTEGER, dati JSONB, cambiati JSONB, computed_at TIMESTAMPTZ DEFAULT now())""")
    conn.commit()


CAND_SQL = """
WITH ult AS (
  SELECT DISTINCT ON (listing_id) listing_id, categoria, margine, dati, computed_at
  FROM valutazioni_policy WHERE computed_at > now() - interval '36 hours'
  ORDER BY listing_id, computed_at DESC)
SELECT l.id, l.title, l.description, l.price_eur, l.power_kw, l.gearbox, l.version_raw, l.year, l.mileage_km,
       l.price_flags, u.categoria, u.margine
FROM ult u JOIN listings l ON l.id = u.listing_id
LEFT JOIN lettura_ai a ON a.listing_id = l.id
WHERE l.status = 'attivo' AND COALESCE(l.seller_type, 'privato') <> 'commerciante'
  AND (a.listing_id IS NULL OR a.prezzo IS DISTINCT FROM l.price_eur)
  AND (u.margine >= %(m_min)s
       OR (u.categoria = 'dati_insufficienti' AND (u.dati::text LIKE '%%confronti validi%%'
                                                   OR u.dati::text LIKE '%%motore non indicato%%'
                                                   OR u.dati::text LIKE '%%sotto la mediana%%')))
ORDER BY (u.categoria IN ('opportunita','candidato')) DESC, u.margine DESC NULLS LAST
LIMIT %(lim)s"""

_CIL = re.compile(r"\b(\d\.\d)\b")


def leggi(r: dict, sink=None) -> dict:
    from . import client as ai
    testo = (f"TITOLO: {r.get('title') or ''}\nPREZZO: {r.get('price_eur')} €\n"
             f"DESCRIZIONE:\n{(r.get('description') or '')[:3500]}")
    res = ai.ask_json("lettore_annunci", ai.MODEL_FAST, SISTEMA, [{"type": "text", "text": testo}], max_tokens=500,
                     usage_sink=sink, listing_id=r.get("id"))
    return res.data if isinstance(res.data, dict) else {}


def applica(conn, r: dict, d: dict) -> dict:
    """Aggiorna l'annuncio con i dati letti (solo quelli scritti e plausibili). Ritorna i campi cambiati."""
    cambi = {}
    kw = d.get("kw")
    if not kw and d.get("cv"):
        try:
            kw = round(int(d["cv"]) * 0.7355)
        except (TypeError, ValueError):
            kw = None
    try:
        kw = int(kw) if kw else None
    except (TypeError, ValueError):
        kw = None
    if kw and 30 <= kw <= 600:
        if not r.get("power_kw") or abs(int(r["power_kw"]) - kw) > 0.10 * kw:
            cambi["power_kw"] = kw
    if d.get("cambio") in ("manuale", "automatico") and not r.get("gearbox"):
        cambi["gearbox"] = d["cambio"]
    versione = r.get("version_raw") or ""
    cil = d.get("cilindrata")
    if isinstance(cil, (int, float)):
        cil = f"{float(cil):.1f}"
    if isinstance(cil, str) and _CIL.fullmatch(cil.strip()) and not _CIL.search(versione):
        cambi["version_raw"] = f"{cil.strip()} {versione}".strip()[:200]
    if d.get("prezzo_vero") is False:
        flags = list(r.get("price_flags") or [])
        if "rata" not in flags:
            cambi["price_flags"] = flags + ["rata"]
    if d.get("danni") is True:
        cambi["damage_declared"] = True
    if cambi:
        sets = ", ".join(f"{k}=%({k})s" for k in cambi)
        conn.execute(f"UPDATE listings SET {sets} WHERE id=%(id)s", dict(cambi, id=r["id"]))
    conn.execute("INSERT INTO lettura_ai (listing_id, prezzo, dati, cambiati) VALUES (%s,%s,%s,%s) "
                 "ON CONFLICT (listing_id) DO UPDATE SET prezzo=EXCLUDED.prezzo, dati=EXCLUDED.dati, "
                 "cambiati=EXCLUDED.cambiati, computed_at=now()",
                 (r["id"], r.get("price_eur"), json.dumps(d, ensure_ascii=False), json.dumps(cambi, default=str)))
    return cambi


def giro(conn, ids: list[int] | None = None, limite: int | None = None, rivaluta: bool = True) -> dict:
    """Legge le candidate (o gli annunci `ids`), aggiorna i dati e rivaluta subito quelle cambiate."""
    from . import client as ai
    if os.environ.get("LETTORE", "1") != "1":
        return {"spento": True}
    if not ai.available():
        return {"spento": "nessuna chiave AI"}
    if ai.budget_finito(conn):
        return {"saltato": "budget AI del giorno finito"}
    _schema_sql(conn)
    t0 = time.time()
    lim = limite or int(os.environ.get("LETTORE_PER_GIRO", "60"))
    if ids:
        rows = conn.execute("SELECT l.id, l.title, l.description, l.price_eur, l.power_kw, l.gearbox, l.version_raw, "
                            "l.year, l.mileage_km, l.price_flags FROM listings l LEFT JOIN lettura_ai a ON a.listing_id=l.id "
                            "WHERE l.id = ANY(%s) AND (a.listing_id IS NULL OR a.prezzo IS DISTINCT FROM l.price_eur) "
                            "LIMIT %s", (ids, lim)).fetchall()
    else:
        rows = conn.execute(CAND_SQL, {"m_min": int(os.environ.get("LETTORE_MARGINE_MIN", "800")), "lim": lim}).fetchall()
    st, rivalutare = Counter(), []
    sink = ai.db_usage_sink(conn)
    for r in rows:
        try:
            d = leggi(r, sink)
        except Exception as e:
            st["errore"] += 1
            if st["errore"] <= 2:
                log.warning("LETTORE errore: %s", str(e)[:200])
            if st["errore"] >= 5 and not st["lette"]:
                break
            continue
        st["lette"] += 1
        cambi = applica(conn, r, d)
        for k in cambi:
            st[f"cambiato:{k}"] += 1
        if cambi:
            rivalutare.append(r["id"])
        conn.commit()
    if rivalutare and rivaluta:
        try:
            from ..pipeline.tempo_reale import valuta_e_pubblica
            st2 = Counter()
            valuta_e_pubblica(conn, rivalutare, st2)
            conn.commit()
            st["rivalutate"] = len(rivalutare)
            st["pubblicate"] = st2.get("pubblicate", 0)
        except Exception as e:
            conn.rollback()
            log.warning("LETTORE rivalutazione non riuscita: %s", str(e)[:200])
    res = {"candidate": len(rows), "secondi": round(time.time() - t0), **dict(st)}
    log.warning("LETTORE_GIRO %s", json.dumps(res, ensure_ascii=False))
    return res
