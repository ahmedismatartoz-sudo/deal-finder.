"""Zone di lavoro di Scovo. Milano e dintorni è la zona di partenza; Ravenna e dintorni è stata aggiunta
per un cliente in Romagna (Ravenna, Forlì-Cesena, Rimini, Ferrara, Bologna)."""
from __future__ import annotations

import os

MILANO = ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV", "CR", "NO", "PC", "AL", "VC"]
# province di confine: si tengono solo i comuni entro 60 km da Milano (data/comuni_60km.json)
SOLO_COMUNI_LISTA = {"NO", "PC", "AL", "VC"}


def _carica_comuni() -> dict:
    import json
    from pathlib import Path
    try:
        return json.loads((Path(__file__).resolve().parents[1] / "data" / "comuni_60km.json").read_text())
    except Exception:
        return {}


COMUNI_60KM = _carica_comuni()


def comune_ok(provincia: str | None, citta: str | None) -> bool:
    """Province di confine (Novara, Piacenza, Alessandria, Vercelli): solo i comuni della lista dei 60 km."""
    if (provincia or "").upper() not in SOLO_COMUNI_LISTA:
        return True
    import re
    import unicodedata
    c = unicodedata.normalize("NFKD", str(citta or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", c.split(",")[0]) in COMUNI_60KM
RAVENNA = ["RA", "FC", "RN", "FE", "BO"]
ROMA = ["RM", "LT", "FR", "VT", "RI"]

ZONE = {
    "milano": {"nome": "Milano e dintorni", "province": MILANO},
    "ravenna": {"nome": "Ravenna e dintorni", "province": RAVENNA, "subito_regione": "emilia-romagna",
                "autoscout_cap": "48121", "autoscout_raggio": os.environ.get("RAVENNA_RAGGIO", "80")},
    "roma": {"nome": "Roma e dintorni", "province": ROMA, "subito_regione": "lazio",
             "autoscout_cap": "00185", "autoscout_raggio": os.environ.get("ROMA_RAGGIO", "70")},
}


# zone su cui si lavora (raccolte e sito): per ora solo Milano e dintorni
ATTIVE = [z.strip() for z in os.environ.get("ZONE_ATTIVE", "milano").split(",") if z.strip() in ZONE]


def tutte() -> list[str]:
    return sorted({p for k, z in ZONE.items() if k in ATTIVE for p in z["province"]})
