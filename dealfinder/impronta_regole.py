"""Impronta delle regole di business (16/11, fondamenta): UNA fotografia di soglie, margini, tetti, zona, km, telefono e
trattativa, confrontata con il file versionato regole_impronta.json (radice del repo).

Due usi:
  - run_tests.py --impronta (e il controllo su GitHub): `dati()` in un processo con ambiente PULITO = i soli default del
    codice, che devono essere uguali a "dati" del file;
  - canarino (a runtime): `dati()` nel processo vero = le regole EFFETTIVE, cioè default + variabili d'ambiente di Render
    (scovo.py legge ancora SOGLIE_PV, MARGINE_FINO_*, KM_MASSIMO, SCONTO_RIVENDITA, MODALITA_SITO, POLICY_SITO...;
    sito_regole legge KM_MASSIMO_SITO, JEV_CONF_SCARTA, ...). Devono essere uguali a "dati" con sopra le eccezioni
    dichiarate in "ammessi_produzione" (valori impostati apposta su Render e messi nel file con un commit "REGOLE:").

Solo valori di regola: nessun segreto (niente chiavi, niente ZONA_SITO o altri elenchi di lavoro).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os

RIFERIMENTO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "regole_impronta.json")


def dati() -> dict:
    """Le regole come le vede QUESTO processo (moduli già importati: valgono le variabili lette al loro import)."""
    from . import regole as RG
    from .pricing import policy_v2 as PV
    from .web import scovo
    from .web import sito_regole as SR
    r = RG.regole()
    d = {
        "regole": RG.come_dict(r),
        "regole_versione": RG.VERSIONE,
        "regole_impronta": RG.impronta(r),
        "scovo": {"POLICY_SITO": scovo.POLICY_SITO, "CATEGORIE_SITO": sorted(scovo.CATEGORIE_SITO),
                  "SOGLIE_PV": scovo.SOGLIE_PV, "SOGLIE": scovo.SOGLIE, "MARGINE_MINIMO": scovo.MARGINE_MINIMO,
                  "ANNO_MINIMO": scovo.ANNO_MINIMO, "KM_MASSIMO": scovo.KM_MASSIMO, "COSTI_FISSI": scovo.COSTI_FISSI,
                  "SCONTO_DEFAULT": scovo.SCONTO_DEFAULT, "MIN_MOSTRATO": scovo.MIN_MOSTRATO},
        "sito_regole": dataclasses.asdict(SR.Regole.da_env()),
        "zona": {"PROVINCE_100KM": sorted(SR.PROVINCE_100KM), "PROVINCE_SITO": sorted(SR.PROVINCE_SITO), "RAVENNA": sorted(SR.RAVENNA), "ROMA": sorted(SR.ROMA),
                 "TORINO": sorted(SR.TORINO)},
        "trattativa": PV.TRATTA_MAX,
    }
    return json.loads(json.dumps(d, sort_keys=True, default=str))


def impronta(d: dict) -> str:
    canon = json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:12]


def piatto(d, prefisso: str = "") -> dict:
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            out.update(piatto(v, f"{prefisso}.{k}" if prefisso else str(k)))
        return out
    return {prefisso: d}


def differenze(attesi: dict, ora: dict) -> list[str]:
    """Valori diversi tra due 'dati' (annidati), una riga per valore: 'chiave: atteso -> ora'."""
    a, b = piatto(attesi or {}), piatto(ora or {})
    return [f"{k}: {a.get(k, '<assente>')!r} -> {b.get(k, '<assente>')!r}" for k in sorted(set(a) | set(b))
            if a.get(k, "<assente>") != b.get(k, "<assente>")]


def leggi_riferimento(percorso: str | None = None) -> dict:
    with open(percorso or RIFERIMENTO, encoding="utf-8") as f:
        return json.load(f)


def attesi_produzione(rif: dict) -> dict:
    """'dati' del riferimento con sopra le eccezioni di produzione dichiarate (chiavi piatte 'sezione.campo')."""
    out = piatto(rif.get("dati") or {})
    out.update(rif.get("ammessi_produzione") or {})
    return out


def differenze_effettive(rif: dict | None = None, ora: dict | None = None) -> list[str]:
    """Regole effettive di questo processo contro il riferimento (con le eccezioni di produzione)."""
    rif = rif if rif is not None else leggi_riferimento()
    a, b = attesi_produzione(rif), piatto(ora if ora is not None else dati())
    return [f"{k}: {a.get(k, '<assente>')!r} -> {b.get(k, '<assente>')!r}" for k in sorted(set(a) | set(b))
            if a.get(k, "<assente>") != b.get(k, "<assente>")]


if __name__ == "__main__":
    d = dati()
    print(json.dumps({"impronta": impronta(d), "dati": d}, ensure_ascii=False))
