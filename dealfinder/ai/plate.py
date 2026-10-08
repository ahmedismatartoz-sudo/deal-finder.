"""Ricerca dati veicolo da targa tramite servizio esterno (facoltativo).

Si attiva impostando PLATE_LOOKUP_URL (con {plate} nel percorso) e
PLATE_LOOKUP_KEY. Usato SOLO per identificare il modello esatto, mai per
risalire al proprietario. Il risultato non viene mostrato ai commercianti.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(os.environ.get("PLATE_LOOKUP_URL"))


def lookup(plate: str) -> dict | None:
    if not configured() or not plate:
        return None
    try:
        import httpx
        url = os.environ["PLATE_LOOKUP_URL"].format(plate=plate)
        r = httpx.get(url, headers={"Authorization": f"Bearer {os.environ.get('PLATE_LOOKUP_KEY', '')}"},
                      timeout=20)
        r.raise_for_status()
        data = r.json()
        # Solo dati tecnici del veicolo
        keep = ("make", "model", "version", "engine", "power_kw", "fuel", "registration_date", "body_type")
        return {k: data.get(k) for k in keep if data.get(k) is not None} or None
    except Exception as e:
        log.warning("ricerca targa fallita: %s", e)
        return None
