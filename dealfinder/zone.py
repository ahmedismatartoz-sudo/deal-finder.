"""Zone di lavoro di Scovo. Milano e dintorni è la zona di partenza; Ravenna e dintorni è stata aggiunta
per un cliente in Romagna (Ravenna, Forlì-Cesena, Rimini, Ferrara, Bologna)."""
from __future__ import annotations

import os

# Milano e dintorni = entro 150 km da Milano (dal 16/10/2026; prima 100 km): Lombardia, Piemonte orientale e
# Torino, Piacenza-Parma-Reggio Emilia, Genova. ZONA_150=0 torna alla zona stretta.
_STRETTA = ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV", "CR", "NO", "PC", "AL", "VC"]
_150 = _STRETTA + ["MN", "SO", "BI", "VB", "AT", "TO", "PR", "RE", "GE"]
MILANO = _150 if os.environ.get("ZONA_150", "1") == "1" else _STRETTA
# province di confine: con la zona stretta solo i comuni entro 60 km da Milano (data/comuni_60km.json)
SOLO_COMUNI_LISTA = set() if os.environ.get("ZONA_150", "1") == "1" else {"NO", "PC", "AL", "VC"}


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
# 9/11 (cliente: presentazione a un commerciante di Torino): Torino e dintorni, con zona e filtro propri sul sito
TORINO = ["TO", "AT", "CN", "BI", "VC"]
ROMA = ["RM", "LT", "FR", "VT", "RI"]


# ------------------------------------------------------------------ 28/11: raggio 200 km (decisione del cliente)
# Cliente (27/11 23:59): "Da AutoScout ti autorizzo a mettere un raggio di 200 km e leggete tutto e fate entrare tutto,
# sempre con posizione attiva. Ho bisogno di più macchine."
# Province italiane con il CAPOLUOGO entro 200 km in linea d'aria da Milano (haversine dalle coordinate di
# geo.PROVINCE, centro geo.PROVINCE["MI"] = 45.46, 9.19), Roma esclusa comunque, niente Svizzera (solo sigle italiane).
# Elenco ottenuto (km dal capoluogo, arrotondati), 33 province:
#   MI 0, MB 15, LO 29, PV 30, CO 40, NO 45, BG 46, LC 47, VA 49, PC 61, VC 62, VB 71, CR 75, AL 76, BS 81, BI 90,
#   SO 95, AT 99, PR 116, GE 119, TO 125, MN 130, SV 140, VR 140, RE 141, AO 149, SP 159, MO 164, TN 164, MS 175,
#   CN 177, VI 184, IM 197.
# Appena fuori: BO 200,6 (già nella zona di Ravenna), FE 203 (idem), BZ 203, LU 208, RO 208, PD 210, PI 216, FI 249.
RAGGIO_KM = 200
CENTRO_MILANO = (45.46, 9.19)


def _entro(raggio: float = RAGGIO_KM) -> list[str]:
    from .geo import PROVINCE, km
    return sorted(p for p, c in PROVINCE.items() if km(CENTRO_MILANO, c) <= raggio and p not in ROMA)


PROVINCE_200KM = _entro()

# Centri delle ricerche AutoScout24 (CAP, raggio in km) che coprono il cerchio di 200 km e le 33 province INTERE.
# Milano 200 km è il cerchio. Le parti delle province fuori dal cerchio (comuni oltre 200 km, misurati sulle coordinate
# note): Imperia ovest (Sanremo 214, Bordighera 222, Ventimiglia 224), Trentino orientale (Fiera di Primiero 220,
# Val di Fassa 229), Vicenza est (Bassano del Grappa 201): due ricerche piccole in più, Imperia 50 km (Ventimiglia a 35)
# e Trento 70 km (Canazei 68, Primiero 56, Bassano 58). Il resto delle province è già
# dentro (Limone Piemonte 189, Bardonecchia 199, Courmayeur 176, Cavalese 198, Pievepelago 180, Carrara 169).
# I centri di prima (Brescia 90 km, Torino 100 km) stanno dentro il cerchio di Milano (Brescia per intero, Torino tranne
# le montagne di confine): rileggerli raddoppierebbe le richieste senza annunci nuovi.
# zipr=200 era già usato dal tempo reale (19/10, TR_RAGGIO 200) e dalla raccolta FOCUS_RAGGIO.
CENTRI_AS_200KM = (("20121", "200"), ("18100", "50"), ("38122", "70"))


def province_lettura() -> set:
    """Province che la lettura TIENE (salvataggio tempo_reale._salva, filtro prima del dettaglio AutoScout24,
    valutazione ricalcolo._utilitarie): Milano e dintorni, Torino, Ravenna e dintorni e, dal 28/11, tutte le province
    entro 200 km da Milano. Mai Roma."""
    return (set(MILANO) | set(TORINO) | set(RAVENNA) | set(PROVINCE_200KM)) - set(ROMA)

ZONE = {
    "milano": {"nome": "Milano e dintorni", "province": MILANO},
    "ravenna": {"nome": "Ravenna e dintorni", "province": RAVENNA, "subito_regione": "emilia-romagna",
                "autoscout_cap": "48121", "autoscout_raggio": os.environ.get("RAVENNA_RAGGIO", "80")},
    "torino": {"nome": "Torino e dintorni", "province": TORINO, "subito_regione": "piemonte",
               "autoscout_cap": "10121", "autoscout_raggio": os.environ.get("TORINO_RAGGIO", "70")},
    "roma": {"nome": "Roma e dintorni", "province": ROMA, "subito_regione": "lazio",
             "autoscout_cap": "00185", "autoscout_raggio": os.environ.get("ROMA_RAGGIO", "70")},
}


# zone su cui si lavora (raccolte e sito): per ora solo Milano e dintorni
ATTIVE = [z.strip() for z in os.environ.get("ZONE_ATTIVE", "milano,torino" if os.environ.get("SITO_TORINO", "1") == "1" else "milano").split(",") if z.strip() in ZONE]


def tutte() -> list[str]:
    return sorted({p for k, z in ZONE.items() if k in ATTIVE for p in z["province"]})
