"""Recupero delle auto rimaste senza abbastanza confronti (16/11, ramo fond-auto).

    python -m dealfinder.pricing.recupero          (un giro, al massimo RECUPERO_PER_GIRO auto)

Le regole NON cambiano. Si rivalutano, con la stessa strada di sempre (valutazione.valuta_e_scrivi, quindi con la
cascata dei confronti di ricalcolo._cascata e con la stessa guardia sulle scritture), le auto di privati ancora online
la cui ultima valutazione è:
  - "in attesa del giudice" (giudice_mancante: budget AI finito, AI ferma o processo senza chiave AI);
  - "dati_insufficienti" perché il mercato non si è potuto stimare: pochi confronti ("solo N confronti validi") o
    confronti troppo dispersi, e non ancora passata dalla cascata.
Le auto ferme per una protezione (prezzo sotto il 55% del mercato, danni, costi, prezzo non affidabile) e quelle già
valutate non si riprendono: la cascata per loro non parte (ricalcolo.serve_cascata).

Ogni auto ripresa si segna nella tabella recupero_provato (migrazione 059): il giro dopo prende le altre, a partire
dalla valutazione più vecchia, e non riprova la stessa prima di RECUPERO_ORE ore (24).

Budget AI: le chiamate al giudice di TUTTE le auto del giro (anche quelle ripescate che diventano candidato o
opportunità) contano nel tetto RECUPERO_GIUDICE_MAX (default: GIUDICE_ATTESA_PER_GIRO, 150); raggiunto il tetto o
finito il budget comune il giro si ferma. Un solo recupero alla volta (lucchetto 734120).

Variabili (nessuna è una regola: decidono solo QUANTE auto si riguardano per giro):
  RECUPERO_CONFRONTI  "0" = spento (default acceso)
  RECUPERO_PER_GIRO   auto al massimo per giro (default 400)
  RECUPERO_GIORNI     solo valutazioni degli ultimi N giorni (default 10)
  RECUPERO_ORE        ore prima di riprovare la stessa auto (default 24)
  RECUPERO_GIUDICE_MAX chiamate al giudice al massimo per giro (default GIUDICE_ATTESA_PER_GIRO o 150)
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

from . import policy_v2 as P

log = logging.getLogger("policy")

PER_GIRO = 400
GIORNI = 10
ORE = 24
LOCK = 734120                     # pg_try_advisory_lock: anche riprova_giudice del sito può prenderlo (FOND_AUTO.md)
BLOCCO = 20                       # auto per volta tra un controllo del tetto e l'altro

TABELLA_SQL = ("CREATE TABLE IF NOT EXISTS recupero_provato (listing_id BIGINT PRIMARY KEY, "
               "provato_at TIMESTAMPTZ NOT NULL DEFAULT now(), tentativi INTEGER NOT NULL DEFAULT 1, esito TEXT)")

# Ultima valutazione dell'auto (versione di policy in uso), solo privati online, non ripresa nelle ultime ORE ore.
DA_RECUPERARE_SQL = """
SELECT v.listing_id
  FROM valutazioni_policy v JOIN listings l ON l.id = v.listing_id
  LEFT JOIN recupero_provato rp ON rp.listing_id = v.listing_id
 WHERE v.policy_version = %(pv)s AND l.status = 'attivo'
   AND COALESCE(l.seller_type, 'privato') <> 'commerciante'
   -- 17/11 (giudice): mai i "solo confronto" (mercato_nord, fuori_regole) né il solo finanziamento: non si
   -- pubblicano (valutazione.PROMUOVI_SQL), quindi niente chiamate al giudice per loro
   AND NOT (l.stage = 'mercato' AND btrim(split_part(COALESCE(l.stage_reason, ''), ' |', 1)) IN ('mercato_nord', 'fuori_regole'))
   AND btrim(split_part(COALESCE(l.stage_reason, ''), ' |', 1)) <> 'solo_finanziamento'
   AND v.computed_at > now() - make_interval(days => %(giorni)s)
   AND (rp.provato_at IS NULL OR rp.provato_at < now() - make_interval(hours => %(ore)s))
   AND (   (v.dati->>'giudice_mancante') = 'true'
        OR (NOT (v.dati ? 'cascata') AND v.categoria = 'dati_insufficienti'
            AND (v.dati->'motivi'->>0 LIKE 'solo %%confronti validi%%'
                 OR v.dati->'motivi'->>0 LIKE 'confronti troppo dispersi%%')))
 ORDER BY v.computed_at ASC
 LIMIT %(lim)s"""

SEGNA_SQL = """INSERT INTO recupero_provato (listing_id, provato_at, tentativi, esito) VALUES (%s, now(), 1, %s)
   ON CONFLICT (listing_id) DO UPDATE SET provato_at = now(), tentativi = recupero_provato.tentativi + 1,
     esito = EXCLUDED.esito"""


def attivo() -> bool:
    return os.environ.get("RECUPERO_CONFRONTI", "1").strip() != "0"


def _int(nome: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(nome) or default))
    except ValueError:
        return default


def tetto_giudice() -> int:
    return _int("RECUPERO_GIUDICE_MAX", _int("GIUDICE_ATTESA_PER_GIRO", 150))


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


def da_recuperare(conn, limite: int | None = None) -> list:
    lim = _int("RECUPERO_PER_GIRO", PER_GIRO) if limite is None else limite
    if lim <= 0:
        return []
    try:
        conn.execute(TABELLA_SQL)
        return [r["listing_id"] for r in conn.execute(
            DA_RECUPERARE_SQL, {"pv": P.POLICY_VERSION, "giorni": _int("RECUPERO_GIORNI", GIORNI),
                                "ore": _int("RECUPERO_ORE", ORE), "lim": lim}).fetchall()]
    except Exception as e:
        _rollback(conn)
        log.warning("RECUPERO_CONFRONTI auto non lette: %s", str(e)[:200])
        return []


def _segna(conn, ids: list, salvata: dict) -> None:
    try:
        for lid in ids:
            conn.execute(SEGNA_SQL, (lid, str(salvata.get(lid))[:40]))
        conn.commit()
    except Exception as e:
        _rollback(conn)
        log.warning("RECUPERO_CONFRONTI tentativi non segnati: %s", str(e)[:200])


def _budget_finito(conn) -> bool:
    try:
        from ..ai import client as AI
        return bool(AI.available() and AI.budget_finito(conn))
    except Exception:
        return False


def recupera(conn, limite: int | None = None) -> dict:
    """Un giro di recupero. Ritorna i contatori (anche nel log RECUPERO_CONFRONTI)."""
    if not attivo():
        return {"spento": True}
    try:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK,)).fetchone()["ok"]:
            log.warning("RECUPERO_CONFRONTI già in corso altrove: salto")
            return {"in_corso": True}
    except Exception as e:
        _rollback(conn)
        log.warning("RECUPERO_CONFRONTI lucchetto non preso (%s): salto", str(e)[:120])
        return {"in_corso": True}
    try:
        return _recupera(conn, limite)
    finally:
        try:
            _rollback(conn)
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
            conn.commit()
        except Exception:
            pass


def _recupera(conn, limite: int | None) -> dict:
    from ..ai import giudice as GI
    from .valutazione import valuta_e_scrivi
    t0 = time.time()
    ids = da_recuperare(conn, limite)
    if not ids:
        log.warning("RECUPERO_CONFRONTI %s", json.dumps({"auto": 0}))
        return {"auto": 0}
    tetto = tetto_giudice()
    cat, cascata, stats = Counter(), Counter(), Counter()
    chiamate, fatte, sul_sito, fermo = 0, 0, 0, None
    for k in range(0, len(ids), BLOCCO):
        if chiamate >= tetto:
            fermo = "tetto_giudice"
            break
        if _budget_finito(conn):
            fermo = "budget_ai_finito"
            break
        pezzo = ids[k:k + BLOCCO]
        _t = time.time()
        es = valuta_e_scrivi(conn, pezzo, "cron-recupero")
        # tutte le chiamate al giudice del pezzo, non solo quelle delle righe in attesa
        chiamate += sum(1 for lid in pezzo if GI.esito(lid, _t) == "chiamata")
        fatte += len(pezzo)
        _segna(conn, pezzo, es.salvata)
        stats.update(es.stats)
        for lid in pezzo:
            cat[str(es.salvata.get(lid))] += 1
            v = es.valutazioni.get(lid) or {}
            if v.get("fonte_confronti"):
                cascata[str(v["fonte_confronti"])] += 1
            sul_sito += int(es.sul_sito(lid))
    out = {"auto": fatte, "scelte": len(ids), "categorie_salvate": dict(cat), "cascata_usata": dict(cascata),
           "sul_sito_possibili": sul_sito, "chiamate_giudice": chiamate, "tetto_giudice": tetto,
           **({"fermo": fermo} if fermo else {}), "contatori": dict(stats), "secondi": round(time.time() - t0, 1)}
    log.warning("RECUPERO_CONFRONTI %s", json.dumps(out, ensure_ascii=False))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from ..db import connect
    with connect() as conn:
        print(recupera(conn))
