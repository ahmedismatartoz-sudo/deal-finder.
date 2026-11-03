"""4/11: liste con dati personali (email e telefoni di commercianti) fuori dal repo, nella tabella dati_riservati.

Prima erano in data/*.json su GitHub (marketing_gruppi*.json, commercianti.json, lead_mi.json, lead_mb.json).
Il codice le legge solo da qui. Per caricarne una (dal computer del cliente o dalla shell di Render, con il file
tenuto FUORI dal repo):

    python -m dealfinder.marketing.dati_riservati carica marketing_gruppi /percorso/marketing_gruppi.json
    python -m dealfinder.marketing.dati_riservati elenco

Nei log solo nomi e conteggi, mai il contenuto.
"""
from __future__ import annotations

import json
import logging
import sys

lg = logging.getLogger("dealfinder.marketing")


def leggi(conn, nome: str):
    r = conn.execute("SELECT dati FROM dati_riservati WHERE nome=%s", (nome,)).fetchone()
    if not r:
        return None
    d = r["dati"]
    return json.loads(d) if isinstance(d, str) else d


def lista(nome: str) -> list:
    """Lista caricata in dati_riservati (es. "lead_mi", "lead_mb"); vuota se non c'è (avviso nel log)."""
    from ..db import connect
    with connect() as conn:
        d = leggi(conn, nome)
    if d is None:
        lg.warning("DATI_RISERVATI %s non caricato: caricalo con python -m dealfinder.marketing.dati_riservati", nome)
    return list(d or [])


def leggi_gruppi(conn, nome: str | None = None) -> dict[str, list[str]]:
    """Gruppi di email per lista Brevo ({"Skovo utilitarie_A": [...], ...}). Senza nome: tutti i caricamenti
    marketing_gruppi* uniti (per segnare come già usati i contatti delle liste vecchie)."""
    if nome:
        return leggi(conn, nome) or {}
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT dati FROM dati_riservati WHERE nome LIKE 'marketing\\_gruppi%%'").fetchall():
        d = r["dati"]
        for k, v in (json.loads(d) if isinstance(d, str) else d or {}).items():
            out.setdefault(k, []).extend(v or [])
    return out


def carica(conn, nome: str, dati) -> None:
    conn.execute("INSERT INTO dati_riservati (nome, dati) VALUES (%s,%s) ON CONFLICT (nome) DO UPDATE "
                 "SET dati=EXCLUDED.dati, caricato_at=now()", (nome, json.dumps(dati)))
    conn.commit()
    n = sum(len(v) for v in dati.values()) if isinstance(dati, dict) else len(dati)
    lg.warning("DATI_RISERVATI %s caricato (%d voci)", nome, n)


def main(argv: list[str]) -> int:
    from ..db import connect
    if len(argv) >= 3 and argv[0] == "carica":
        with open(argv[2], encoding="utf-8") as f:
            dati = json.load(f)
        with connect() as conn:
            carica(conn, argv[1], dati)
        print(f"caricato {argv[1]}")
        return 0
    if argv[:1] == ["elenco"]:
        with connect() as conn:
            for r in conn.execute("SELECT nome, caricato_at, jsonb_typeof(dati) AS tipo FROM dati_riservati "
                                  "ORDER BY nome").fetchall():
                print(r["nome"], r["caricato_at"], r["tipo"])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
