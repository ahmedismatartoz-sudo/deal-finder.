"""Zone di lavoro di Scovo. Milano e dintorni è la zona di partenza; Ravenna e dintorni è stata aggiunta
per un cliente in Romagna (Ravenna, Forlì-Cesena, Rimini, Ferrara, Bologna)."""
from __future__ import annotations

import os

MILANO = ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV", "CR", "NO"]
RAVENNA = ["RA", "FC", "RN", "FE", "BO"]
ROMA = ["RM", "LT", "FR", "VT", "RI"]

ZONE = {
    "milano": {"nome": "Milano e dintorni", "province": MILANO},
    "ravenna": {"nome": "Ravenna e dintorni", "province": RAVENNA, "subito_regione": "emilia-romagna",
                "autoscout_cap": "48121", "autoscout_raggio": os.environ.get("RAVENNA_RAGGIO", "80")},
    "roma": {"nome": "Roma e dintorni", "province": ROMA, "subito_regione": "lazio",
             "autoscout_cap": "00185", "autoscout_raggio": os.environ.get("ROMA_RAGGIO", "70")},
}


def tutte() -> list[str]:
    return sorted({p for z in ZONE.values() for p in z["province"]})
