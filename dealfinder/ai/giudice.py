"""Giudice AI dei confronti (28/10, cliente: "margini non precisi, serve un'AI che capisca gli annunci").

Il calcolo resta matematico: il software trova le auto "uguali" (stesso modello, motore, cambio, anno ±1, km ±20.000),
poi l'AI legge annuncio e confronti e SCARTA quelli che non sono davvero la stessa auto (allestimento diverso,
optional molto diversi, versione sportiva contro base, commerciale/furgone, prezzo non vero, danni nel testo).
La mediana e la rivendita si rifanno solo sui confronti rimasti.

Solo per le auto che stanno per andare sul sito; i giudizi restano in tabella ai_giudizi (stessi confronti = nessuna
nuova chiamata). Tetto di spesa: AI_BUDGET_GIORNO (dollari al giorno).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

from . import client as AI

log = logging.getLogger("giudice")

SYSTEM = """Sei un commerciante di auto usate italiano esperto. Ti do un'auto da valutare e un elenco di annunci
"confronto" che un programma ha scelto come uguali (stesso modello, alimentazione, anno ±1, km simili).
Il tuo compito: dire quali confronti NON sono davvero la stessa auto per il prezzo di rivendita.
Scarta un confronto SOLO se c'è un motivo concreto e leggibile nei dati:
- allestimento/versione chiaramente diverso e di valore diverso (es. base vs M Sport/AMG/RS/S line/Competizione, GT, cabrio vs berlina);
- motorizzazione diversa (kW molto diversi, ibrida vs non ibrida), cambio diverso;
- veicolo commerciale/furgone/autocarro contro auto, o viceversa;
- prezzo non reale (rata, anticipo, "prezzo con permuta/finanziamento", noleggio) o auto incidentata/guasta.
NON scartare per piccole differenze di km/anno o colore. Se i dati non bastano, tieni il confronto.
Rispondi SOLO con JSON: {"via": [{"id": <id>, "motivo": "<max 8 parole>"}], "nota": "<max 15 parole>"}"""


def _riga_auto(t: dict) -> str:
    desc = (t.get("description") or "")[:900].replace("\n", " ")
    return (f"AUTO: {t.get('make')} {t.get('model')} | versione: {t.get('version_raw') or '-'} | titolo: {t.get('title') or '-'} | "
            f"anno {t.get('year')} | km {t.get('mileage_km')} | {t.get('fuel')} | cambio {t.get('gearbox') or '?'} | "
            f"kW {t.get('power_kw') or '?'} | prezzo {t.get('price_eur')} €\nDESCRIZIONE: {desc or '-'}")


def _righe_confronti(confronti: list[dict]) -> str:
    return "\n".join(f"id {c.get('id')} | {c.get('versione') or '-'} | anno {c.get('anno')} | km {c.get('km')} | "
                     f"cambio {c.get('cambio') or '?'} | kW {c.get('kw') or '?'} | {c.get('prezzo')} € | {c.get('venditore') or '?'}"
                     for c in confronti)


def _tabella(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS ai_giudizi (
        listing_id BIGINT NOT NULL, chiave TEXT NOT NULL, via JSONB, nota TEXT, at TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (listing_id, chiave))""")


def giudica(conn, t: dict, confronti: list[dict]) -> dict | None:
    """Restituisce {"via": {id: motivo}, "nota": str} oppure None (AI non disponibile / budget finito / errore)."""
    if not confronti or not AI.available():
        return None
    ids = sorted(int(c["id"]) for c in confronti if c.get("id") is not None)
    chiave = hashlib.sha1(json.dumps([t.get("price_eur"), ids]).encode()).hexdigest()[:16]
    try:
        _tabella(conn)
        r = conn.execute("SELECT via, nota FROM ai_giudizi WHERE listing_id=%s AND chiave=%s",
                         (t.get("id"), chiave)).fetchone()
        if r:
            via = r["via"] if isinstance(r["via"], dict) else json.loads(r["via"] or "{}")
            return {"via": {int(k): v for k, v in via.items()}, "nota": r["nota"], "da_archivio": True}
        if AI.budget_finito(conn):
            return None
        res = AI.ask_json("giudice_confronti", AI.MODEL_FAST, SYSTEM,
                          [{"type": "text", "text": _riga_auto(t) + "\n\nCONFRONTI:\n" + _righe_confronti(confronti)}],
                          max_tokens=700, usage_sink=AI.db_usage_sink(conn), listing_id=t.get("id"))
        data = res.data if isinstance(res.data, dict) else {}
        validi = set(ids)
        via = {int(x["id"]): str(x.get("motivo") or "")[:80] for x in (data.get("via") or [])
               if isinstance(x, dict) and str(x.get("id", "")).isdigit() and int(x["id"]) in validi}
        nota = str(data.get("nota") or "")[:200]
        conn.execute("INSERT INTO ai_giudizi (listing_id, chiave, via, nota) VALUES (%s,%s,%s,%s) "
                     "ON CONFLICT (listing_id, chiave) DO NOTHING",
                     (t.get("id"), chiave, json.dumps({str(k): v for k, v in via.items()}), nota))
        return {"via": via, "nota": nota}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("GIUDICE_AI %s non riuscito: %s", t.get("id"), str(e)[:200])
        return None
