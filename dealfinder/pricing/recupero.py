"""Recupero delle auto rimaste senza abbastanza confronti (16/11, ramo fond-auto).

    python -m dealfinder.pricing.recupero          (un giro, al massimo RECUPERO_PER_GIRO auto)

Le regole NON cambiano. Si rivalutano, con la stessa strada di sempre (valutazione.valuta_e_scrivi, quindi con la
cascata dei confronti di ricalcolo._cascata e con la stessa guardia sulle scritture), le auto di privati ancora online
la cui ultima valutazione è:
  - "in attesa del giudice" (giudice_mancante: budget AI finito, AI ferma o processo senza chiave AI);
  - "dati_insufficienti" per pochi confronti ("solo N confronti validi"), per confronti dispersi o per un mercato
    "non affidabile" (prezzo oltre il 45% sotto la mediana di pochi confronti).
Le auto già valutate (candidato, opportunità, non conveniente) non si riprendono: la cascata per loro non aggiunge
niente (potrebbe solo abbassare il mercato).
Quelle già passate dalla cascata (campo "cascata" nella valutazione) non si riprendono finché l'auto o le regole non
cambiano (le riprende il ricalcolo normale), salvo le attese del giudice. Le chiamate al giudice sono limitate dal
tetto del cron (valutazione.tetto_attesa: GIUDICE_ATTESA_PER_GIRO, fonte "cron-recupero"), così il budget AI comune
non finisce come il 13/11.

Variabili (nessuna è una regola: decidono solo QUANTE auto si riguardano per giro):
  RECUPERO_CONFRONTI  "0" = spento (default acceso)
  RECUPERO_PER_GIRO   auto al massimo per giro (default 400)
  RECUPERO_GIORNI     solo valutazioni degli ultimi N giorni (default 10)
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

# Ultima valutazione dell'auto (versione di policy in uso), solo privati online. L'ordine mette prima le attese del
# giudice, poi le più recenti (annunci nuovi: si vendono prima).
DA_RECUPERARE_SQL = """
SELECT v.listing_id
  FROM valutazioni_policy v JOIN listings l ON l.id = v.listing_id
 WHERE v.policy_version = %(pv)s AND l.status = 'attivo'
   AND COALESCE(l.seller_type, 'privato') <> 'commerciante'
   AND v.computed_at > now() - make_interval(days => %(giorni)s)
   AND (   (v.dati->>'giudice_mancante') = 'true'
        OR (NOT (v.dati ? 'cascata') AND v.categoria = 'dati_insufficienti'
            AND (v.dati->'motivi'->>0 LIKE 'solo %%confronti validi%%'
                 OR v.dati->'motivi'->>0 LIKE 'confronti troppo dispersi%%'
                 OR v.dati->'motivi'->>0 LIKE 'prezzo oltre il %%sotto la mediana%%')))
 ORDER BY ((v.dati->>'giudice_mancante') = 'true') DESC, l.first_seen_at DESC NULLS LAST
 LIMIT %(lim)s"""


def attivo() -> bool:
    return os.environ.get("RECUPERO_CONFRONTI", "1").strip() != "0"


def _int(nome: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(nome) or default))
    except ValueError:
        return default


def da_recuperare(conn, limite: int | None = None) -> list:
    lim = _int("RECUPERO_PER_GIRO", PER_GIRO) if limite is None else limite
    if lim <= 0:
        return []
    try:
        return [r["listing_id"] for r in conn.execute(
            DA_RECUPERARE_SQL, {"pv": P.POLICY_VERSION, "giorni": _int("RECUPERO_GIORNI", GIORNI), "lim": lim}).fetchall()]
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("RECUPERO_CONFRONTI auto non lette: %s", str(e)[:200])
        return []


def recupera(conn, limite: int | None = None) -> dict:
    """Un giro di recupero. Ritorna i contatori (anche nel log RECUPERO_CONFRONTI)."""
    if not attivo():
        return {"spento": True}
    from .valutazione import valuta_e_scrivi
    t0 = time.time()
    ids = da_recuperare(conn, limite)
    if not ids:
        log.warning("RECUPERO_CONFRONTI %s", json.dumps({"auto": 0}))
        return {"auto": 0}
    es = valuta_e_scrivi(conn, ids, "cron-recupero")
    cat, cascata = Counter(), Counter()
    for lid, v in es.valutazioni.items():
        cat[str(es.salvata.get(lid))] += 1
        if v.get("fonte_confronti"):
            cascata[str(v["fonte_confronti"])] += 1
    out = {"auto": len(ids), "categorie_salvate": dict(cat), "cascata_usata": dict(cascata),
           "sul_sito_possibili": sum(1 for lid in ids if es.sul_sito(lid)),
           "contatori": dict(es.stats), "secondi": round(time.time() - t0, 1)}
    log.warning("RECUPERO_CONFRONTI %s", json.dumps(out, ensure_ascii=False))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from ..db import connect
    with connect() as conn:
        print(recupera(conn))
