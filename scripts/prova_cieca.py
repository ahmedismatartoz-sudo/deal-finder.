"""Prova alla cieca: M del nostro calcolo, stima media della perizia AI e prezzo Autohero contro l'ultimo prezzo delle
auto realmente sparite (vendute). Solo lettura del database, righe PROVA_CIECA nel log (vedi dealfinder/ai/prova_cieca.py).

    python scripts/prova_cieca.py --n 30 --giorni 30          con l'AI vera (ANTHROPIC_API_KEY)
    python scripts/prova_cieca.py --n 30 --finta              senza chiave: risposte finte (mediana dei confronti),
                                                              per provare il percorso e i conti

Sul server la stessa prova parte una volta con BREVO_IMPORT_LISTE=prova_cieca:30 (web/app.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def risposta_finta(sistema: str, testo: str, listing_id=None, sink=None):
    """Perizia finta ma valida: forbice attorno alla mediana dei prezzi dei confronti nel testo della richiesta."""
    prezzi = [int(x) for x in re.findall(r"^\d+\. (\d+) €", testo, re.M)]
    if len(prezzi) < 2:
        return ({"dati_sufficienti": False, "prezzo_vendita": None, "acquisto_max": None, "giorni_vendita": None,
                 "rischi": [], "giudizio": "da verificare", "motivazione": "Confronti insufficienti."}, 0, 0)
    m = int(statistics.median(prezzi))
    return ({"dati_sufficienti": True, "prezzo_vendita": {"basso": int(m * .92), "medio": m, "alto": int(m * 1.06)},
             "acquisto_max": int(m * .8), "giorni_vendita": {"min": 20, "max": 60}, "rischi": [],
             "giudizio": "da verificare", "motivazione": "Risposta finta: mediana dei confronti."}, 0, 0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--giorni", type=int, default=30)
    ap.add_argument("--finta", action="store_true", help="risposte AI finte (nessuna chiamata)")
    ap.add_argument("--solo-calcolo", action="store_true", help="niente perizia AI: solo M (prima/dopo) e Autohero")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    from dealfinder.ai import prova_cieca
    from dealfinder.db import connect
    with connect() as conn:
        res = prova_cieca.esegui(conn, n=a.n, giorni=a.giorni, chiama=risposta_finta if a.finta else None,
                                 ai=not a.solo_calcolo)
        conn.rollback()                                   # solo lettura
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
