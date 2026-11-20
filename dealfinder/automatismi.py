"""Interruttori degli automatismi (19/11): si accendono, si spengono e si regolano SENZA deploy e senza nuove
variabili su Render.

    attivo("confronti_notte")            -> bool   (default: acceso)
    numero("confronti_notte_gruppi", 400) -> float

Ordine di lettura, il primo che c'è vince:
  1. variabile d'ambiente AUTOMATISMO_<NOME> (solo prove in locale e test: su Render non va messa);
  2. chiave 'automatismi' (jsonb) della tabella sito_impostazioni, letta con web/impostazioni.dati() (copia in
     memoria rinfrescata al massimo ogni 45 secondi: un cambio vale entro un minuto in ogni processo);
  3. il default del codice.
Mai un errore: database assente, chiave scritta male o valore illeggibile = default.

Nomi degli interruttori: memoria_auto, confronti_notte, controllo_tutte, salute, salute_azioni, stima_ripiego,
stima_ripiego_largo (20/11: stima di ripiego, pricing/valore_base.livelli_accesi; senza fasce ammesse non fa nulla).
La chiave NON è tra impostazioni.CHIAVI: dal pannello non si scrive. Per spegnere (rollback senza deploy):
    INSERT INTO sito_impostazioni (chiave, valore) VALUES ('automatismi','{"confronti_notte": false}')
    ON CONFLICT (chiave) DO UPDATE SET valore = sito_impostazioni.valore || EXCLUDED.valore
(con || gli altri interruttori già scritti restano: si cambia solo quello indicato)
"""
from __future__ import annotations

import os

CHIAVE = "automatismi"
NOMI = ("memoria_auto", "confronti_notte", "controllo_tutte", "salute", "salute_azioni",
        "stima_ripiego", "stima_ripiego_largo")
PREFISSO_ENV = "AUTOMATISMO_"
_SI = {"1", "true", "t", "si", "sì", "s", "on", "yes", "y", "acceso", "attivo"}
_NO = {"0", "false", "f", "no", "n", "off", "spento", "fermo"}


def _env(nome: str) -> str | None:
    v = os.environ.get(PREFISSO_ENV + str(nome).upper())
    return v.strip() if v is not None and v.strip() != "" else None


def valori() -> dict:
    """Il contenuto della chiave 'automatismi' nel database ({} se manca, non è un oggetto o non si legge)."""
    try:
        from .web import impostazioni
        d = (impostazioni.dati() or {}).get(CHIAVE)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _SI:
            return True
        if s in _NO:
            return False
    return None


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(str(v).strip().replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def attivo(nome: str, default: bool = True) -> bool:
    """Interruttore acceso? env AUTOMATISMO_<NOME> → sito_impostazioni['automatismi'][nome] → default."""
    for v in (_env(nome), valori().get(nome)):
        b = _bool(v)
        if b is not None:
            return b
    return bool(default)


def numero(nome: str, default: float) -> float:
    """Valore numerico regolabile: stesso ordine di attivo(); illeggibile = default."""
    for v in (_env(nome), valori().get(nome)):
        x = _num(v)
        if x is not None:
            return x
    return float(default)
