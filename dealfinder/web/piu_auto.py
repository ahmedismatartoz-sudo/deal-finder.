"""Dati in più per le regole del sito (13/11, ramo piu-auto), letti UNA volta per giro dal caricamento (web/app.py,
_arricchisci): pochi id, nessun dato del venditore.

  versione_tecnica  per le sole auto che l'AI ha giudicato "versioni molto diverse" (versione_ok < 0,25): stessa
                    scheda tecnica dei confronti? (pricing/versione_tecnica.py)
  ripiego           per le sole auto "dati insufficienti": stima prudente da due fonti (pricing/ripiego_prudente.py)

Ogni errore → nessun dato in più (le auto restano fuori come prima: niente cambia senza dati).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("scovo")


def dati_caricamento(conn, items: list[dict], jev: dict, stats=None) -> tuple[dict, dict]:
    versioni, ripieghi = {}, {}
    forte = float(os.environ.get("JEV_VERSIONE_FORTE", "0.25"))
    ids_v = [i for i, j in (jev or {}).items() if j and j.get("versione_ok") is not None and float(j["versione_ok"]) < forte]
    if ids_v and os.environ.get("VERSIONE_TECNICA", "1") == "1":
        try:
            from ..pricing import versione_tecnica as VT
            versioni = VT.leggi(conn, ids_v)
        except Exception as e:
            conn.rollback()
            log.warning("VERSIONE_TECNICA non letta: %s", str(e)[:200])
    ids_r = [it["id"] for it in items if isinstance(it.get("pv"), dict) and it["pv"].get("categoria") == "dati_insufficienti"
             and isinstance(it.get("id"), int)]
    if ids_r and os.environ.get("RIPIEGO_PRUDENTE", "1") == "1":
        try:
            from ..pricing import ripiego_prudente as RP
            ripieghi = RP.leggi_per_sito(conn, ids_r)
        except Exception as e:
            conn.rollback()
            log.warning("RIPIEGO_PRUDENTE non letto: %s", str(e)[:200])
    if stats is not None:
        stats["versioni_controllate"] = len(versioni)
        stats["versioni_solo_nome"] = sum(1 for v in versioni.values() if v == "uguale")
        stats["ripieghi"] = len(ripieghi)
    return versioni, ripieghi
