"""4/11: consegna delle liste di commercianti (lead) nel database, non nei log.

Prima ogni lead (nome, telefono, email) era scritto come riga JSON nei log di Render ("LEAD {json}", "LEADE ...",
"LEADT ..."): i log hanno conservazione e accessi che non si controllano. Ora `consegna(tipo, json)` salva la riga
nella tabella lead_consegne (migrazione 032) e nel log va solo il conteggio. Il cliente li scarica dal pannello
admin: /api/admin/lead.csv?tipo=LEADE (con il token di amministratore).
"""
from __future__ import annotations

import json
import logging
from collections import Counter

lg = logging.getLogger("dealfinder.marketing")
_contati: Counter = Counter()


def consegna(tipo: str, riga, **_ignora) -> bool:
    """Salva una riga di lead (dict o testo JSON). Mai il contenuto nel log, neanche in caso di errore."""
    if isinstance(riga, str):
        try:
            riga = json.loads(riga)
        except ValueError:
            riga = {"testo": riga}
    try:
        from ..db import connect
        with connect() as conn:
            conn.execute("INSERT INTO lead_consegne (tipo, dati) VALUES (%s,%s)",
                         (tipo[:40], json.dumps(riga, ensure_ascii=False, default=str)))
            conn.commit()
        ok = True
    except Exception as e:
        lg.warning("LEAD_CONSEGNE %s non salvata: %s", tipo, type(e).__name__)
        ok = False
    _contati[tipo] += 1
    if _contati[tipo] in (1, 10) or _contati[tipo] % 100 == 0:
        lg.warning("LEAD_CONSEGNE %s: %d righe salvate nel database (non nei log)", tipo, _contati[tipo])
    return ok


def righe(conn, tipo: str, limite: int = 20000) -> list[dict]:
    return [r["dati"] if isinstance(r["dati"], dict) else json.loads(r["dati"]) for r in conn.execute(
        "SELECT dati FROM lead_consegne WHERE tipo=%s ORDER BY id LIMIT %s", (tipo, limite)).fetchall()]


def csv_bytes(rows: list[dict]) -> bytes:
    import csv
    import io
    campi: list[str] = []
    for r in rows:
        for k in r:
            if k not in campi:
                campi.append(k)
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=campi or ["vuoto"], extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in r.items()})
    return ("﻿" + out.getvalue()).encode("utf-8")
