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
import re

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
Rispondi SOLO con righe di testo, niente altro:
VIA <id> <motivo in max 8 parole>      (una riga per ogni confronto da scartare; nessuna riga VIA se sono tutti validi)
NOTA <giudizio in max 15 parole>       (sempre, ultima riga)"""


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


def _chiedi(conn, t: dict, confronti: list[dict]) -> str:
    resp = AI.client().messages.create(
        model=AI.MODEL_FAST, max_tokens=int(os.environ.get("GIUDICE_MAX_TOKENS", "1200")), system=SYSTEM,
        messages=[{"role": "user", "content": _riga_auto(t) + "\n\nCONFRONTI:\n" + _righe_confronti(confronti)}])
    testo = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    try:
        AI.db_usage_sink(conn)("giudice_confronti", AI.AIResult(data=None, model=AI.MODEL_FAST,
                                                                input_tokens=resp.usage.input_tokens,
                                                                output_tokens=resp.usage.output_tokens),
                               t.get("id"))
    except Exception:
        pass
    if not testo.strip():
        log.warning("GIUDICE_AI %s risposta vuota (stop=%s, blocchi=%s)", t.get("id"), getattr(resp, "stop_reason", None),
                    [getattr(b, "type", "") for b in resp.content])
    return testo


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
        testo = _chiedi(conn, t, confronti)
        validi = set(ids)
        via, nota = {}, ""
        for riga in testo.splitlines():
            m = re.match(r"\s*[*_#\-]*\s*VIA[*_]*\s*:?\s*(?:id\s*)?(\d+)\s*[-:|]?\s*(.*)", riga, re.I)
            if m and int(m.group(1)) in validi:
                via[int(m.group(1))] = m.group(2).strip()[:80]
                continue
            m = re.match(r"\s*[*_#\-]*\s*NOTA[*_]*\s*:?\s*(.*)", riga, re.I)
            if m:
                nota = m.group(1).strip()[:200]
        if not via and not nota:
            log.warning("GIUDICE_AI %s risposta non leggibile: %s", t.get("id"), testo[:300].replace("\n", " | "))
            return None
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
