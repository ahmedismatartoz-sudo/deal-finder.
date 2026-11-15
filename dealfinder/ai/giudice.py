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


# 15/11 (giudice, ricontrollo A2+A7): ultimo esito del giudice per auto, (tempo, esito). Esiti: "chiamata" (pagata,
# anche se illeggibile o troncata e archiviata), "archivio" (giudizio già dato), "budget" (budget AI finito),
# "attesa_illeggibile" (illeggibile da meno di 6 ore), "non_disponibile" (AI spenta), "errore". riprova_giudice conta
# un tentativo solo se il giudice non era bloccato (BLOCCATO).
_ESITI: dict = {}
BLOCCATO = ("budget", "attesa_illeggibile", "non_disponibile")


def _esito(lid, e: str) -> None:
    import time
    if lid is None:
        return
    _ESITI[lid] = (time.time(), e)
    if len(_ESITI) > 20000:
        for k, _v in sorted(list(_ESITI.items()), key=lambda kv: kv[1][0])[:5000]:
            _ESITI.pop(k, None)


def esito(lid, dopo: float = 0.0) -> str | None:
    """Ultimo esito del giudice per l'auto lid, se successivo al tempo `dopo` (None: il giudice non è stato chiamato)."""
    x = _ESITI.get(lid)
    return x[1] if x and x[0] >= dopo else None


ORE_ILLEGGIBILE = 6          # 15/11 (giudice A7): una risposta illeggibile o troncata non si ripaga prima di 6 ore


def _chiedi(conn, t: dict, confronti: list[dict]) -> tuple[str, str | None]:
    """(testo, stop_reason). stop_reason è l'attributo del messaggio dell'SDK Anthropic restituito da AI.crea
    ("end_turn", "max_tokens", ...). 15/11 (giudice A7b): la spesa si registra e si conferma (commit) SUBITO dopo la
    chiamata, prima di leggere la risposta, come controllo_auto con paga: un errore dopo non la cancella."""
    resp = AI.crea(
        model=AI.MODEL_FAST, max_tokens=int(os.environ.get("GIUDICE_MAX_TOKENS", "1200")), system=SYSTEM,
        messages=[{"role": "user", "content": _riga_auto(t) + "\n\nCONFRONTI:\n" + _righe_confronti(confronti)}])
    try:
        AI.db_usage_sink(conn)("giudice_confronti", AI.AIResult(data=None, model=AI.MODEL_FAST,
                                                                input_tokens=resp.usage.input_tokens,
                                                                output_tokens=resp.usage.output_tokens),
                               t.get("id"))
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("GIUDICE_AI %s spesa non registrata: %s", t.get("id"), str(e)[:120])
    testo = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    stop = getattr(resp, "stop_reason", None)
    if not testo.strip():
        log.warning("GIUDICE_AI %s risposta vuota (stop=%s, blocchi=%s)", t.get("id"), stop,
                    [getattr(b, "type", "") for b in resp.content])
    return testo, stop


def _recente(at) -> bool:
    """Data entro ORE_ILLEGGIBILE ore (None o illeggibile: no)."""
    from datetime import datetime, timedelta, timezone
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(at, datetime):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - at < timedelta(hours=ORE_ILLEGGIBILE)


def _archivia_illeggibile(conn, t: dict, chiave: str, motivo: str) -> None:
    """15/11 (giudice A7a): l'esito "illeggibile" si archivia (pagato): per ORE_ILLEGGIBILE ore niente nuova chiamata
    per questa auto a questo prezzo. Non conta come giudizio (nessun _giudicati): poi si riprova."""
    salva = {"_illeggibile": True, "_motivo": motivo, "_prezzo": t.get("price_eur")}
    conn.execute("INSERT INTO ai_giudizi (listing_id, chiave, via, nota) VALUES (%s,%s,%s,%s) "
                 "ON CONFLICT (listing_id, chiave) DO UPDATE SET via=EXCLUDED.via, nota=EXCLUDED.nota, at=now()",
                 (t.get("id"), chiave, json.dumps(salva), ""))
    conn.commit()


def giudica(conn, t: dict, confronti: list[dict], solo_archivio: bool = False) -> dict | None:
    """Restituisce {"via": {id: motivo}, "nota": str} oppure None (AI non disponibile / budget finito / errore).
    solo_archivio: solo un giudizio già in tabella (misure e prove: nessuna chiamata nuova)."""
    if not confronti:
        return None
    if not solo_archivio and not AI.available():
        _esito(t.get("id"), "non_disponibile")
        return None
    ids = sorted(int(c["id"]) for c in confronti if c.get("id") is not None)
    chiave = hashlib.sha1(json.dumps([t.get("price_eur"), ids]).encode()).hexdigest()[:16]
    try:
        _tabella(conn)
        # 12/11 (controllo del giudice M1): il campione dei confronti cambia (somiglianza), quindi la chiave esatta
        # cambia spesso. Si riusano i giudizi GIÀ DATI su questa auto allo stesso prezzo, confronto per confronto
        # (ogni riga salva in via["_giudicati"] gli id esaminati): l'AI vede solo i confronti mai giudicati.
        righe = conn.execute("SELECT chiave, via, nota, at FROM ai_giudizi WHERE listing_id=%s", (t.get("id"),)).fetchall()
        giudicati, via_arch, nota_arch = set(), {}, ""
        illeggibile_recente = False
        for r in righe:
            v = r["via"] if isinstance(r["via"], dict) else json.loads(r["via"] or "{}")
            if v.get("_illeggibile"):
                # risposta pagata e non letta: non è un giudizio; blocca i ripagamenti per qualche ora
                illeggibile_recente |= _recente(r.get("at")) and v.get("_prezzo") == t.get("price_eur")
                continue
            if r["chiave"] == chiave:
                if not solo_archivio:
                    _esito(t.get("id"), "archivio")
                return {"via": {int(k): m for k, m in v.items() if not k.startswith("_")}, "nota": r["nota"],
                        "da_archivio": True}
            if v.get("_prezzo") != t.get("price_eur") or not isinstance(v.get("_giudicati"), list):
                continue
            giudicati |= {int(x) for x in v["_giudicati"]}
            via_arch.update({int(k): m for k, m in v.items() if not k.startswith("_")})
            nota_arch = r["nota"] or nota_arch
        nuovi = [c for c in confronti if c.get("id") is not None and int(c["id"]) not in giudicati]
        if not nuovi and giudicati:
            if not solo_archivio:
                _esito(t.get("id"), "archivio")
            return {"via": {i: m for i, m in via_arch.items() if i in set(ids)}, "nota": nota_arch,
                    "da_archivio": True}
        if solo_archivio:
            return None
        if illeggibile_recente:
            _esito(t.get("id"), "attesa_illeggibile")
            return None
        if AI.budget_finito(conn):
            _esito(t.get("id"), "budget")
            return None
        _esito(t.get("id"), "errore")                 # diventa "chiamata" appena la chiamata risponde
        testo, stop = _chiedi(conn, t, nuovi)
        _esito(t.get("id"), "chiamata")
        if stop == "max_tokens":
            # 15/11 (giudice A7c): risposta troncata. I confronti dopo il taglio non sono stati letti: niente "validi"
            log.warning("GIUDICE_AI %s risposta troncata (max_tokens): %s", t.get("id"), testo[-200:].replace("\n", " | "))
            _archivia_illeggibile(conn, t, chiave, "troncata")
            return None
        validi = {int(c["id"]) for c in nuovi}
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
            _archivia_illeggibile(conn, t, chiave, "illeggibile")
            return None
        salva = {str(k): v for k, v in via.items()}
        salva.update(_giudicati=sorted(validi), _prezzo=t.get("price_eur"))
        # una riga "illeggibile" con la stessa chiave (scaduta) si sostituisce con il giudizio vero
        conn.execute("INSERT INTO ai_giudizi (listing_id, chiave, via, nota) VALUES (%s,%s,%s,%s) "
                     "ON CONFLICT (listing_id, chiave) DO UPDATE SET via=EXCLUDED.via, nota=EXCLUDED.nota, at=now() "
                     "WHERE (ai_giudizi.via->>'_illeggibile') = 'true'",
                     (t.get("id"), chiave, json.dumps(salva), nota))
        tutti = {i: m for i, m in via_arch.items() if i in set(ids)}
        tutti.update(via)
        return {"via": tutti, "nota": nota or nota_arch}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("GIUDICE_AI %s non riuscito: %s", t.get("id"), str(e)[:200])
        return None
