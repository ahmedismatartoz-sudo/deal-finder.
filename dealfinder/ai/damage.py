"""Tassonomia dei danni: quali pezzi esistono, quali danni sono accettati
(leggeri) e quali escludono l'auto. Regole, non AI: l'AI deve solo scegliere
da questa lista."""
from __future__ import annotations

# Pezzi riconosciuti. Il lato (sx/dx) si aggiunge quando serve.
PARTS = {
    "paraurti_anteriore": "Paraurti anteriore",
    "paraurti_posteriore": "Paraurti posteriore",
    "griglia_radiatore": "Griglia / calandra",
    "cofano": "Cofano motore",
    "portellone": "Portellone / cofano bagagliaio",
    "parafango_anteriore": "Parafango anteriore",
    "portiera_anteriore": "Portiera anteriore",
    "portiera_posteriore": "Portiera posteriore",
    "specchietto": "Specchietto retrovisore esterno",
    "faro_anteriore": "Faro anteriore",
    "fanale_posteriore": "Fanale posteriore",
    "fendinebbia": "Fendinebbia",
    "parabrezza": "Parabrezza",
    "lunotto": "Lunotto",
    "vetro_laterale": "Vetro laterale",
    "cerchio": "Cerchio",
    "minigonna": "Minigonna / sottoporta in plastica",
    "modanatura": "Modanatura / profilo",
}
SIDED = {"parafango_anteriore", "portiera_anteriore", "portiera_posteriore", "specchietto",
         "faro_anteriore", "fanale_posteriore", "fendinebbia", "vetro_laterale", "minigonna"}

# Danni che escludono l'auto (non "poco incidentata")
SEVERE_FLAGS = {
    "airbag_esplosi": "Airbag esplosi",
    "telaio_longheroni": "Possibile danno a telaio, longheroni o montanti",
    "frontale_profondo": "Urto frontale profondo (radiatore/motore spostati)",
    "alluvionata": "Auto alluvionata",
    "incendio": "Auto bruciata",
    "non_parte": "Non parte / non marciante",
    "motore_guasto": "Motore guasto",
    "cambio_guasto": "Cambio guasto",
    "sospensioni_sterzo": "Danni a sospensioni o sterzo",
    "tetto_montanti": "Tetto o montanti deformati",
}

# Pezzi spesso danneggiati ma invisibili in foto, per zona dell'urto
HIDDEN_BY_PART = {
    "paraurti_anteriore": ["traversa_anteriore", "staffe_paraurti_anteriore", "sensori_parcheggio_anteriori"],
    "paraurti_posteriore": ["traversa_posteriore", "staffe_paraurti_posteriore", "sensori_parcheggio_posteriori"],
    "faro_anteriore": ["staffa_faro"],
    "parafango_anteriore": ["passaruota_anteriore"],
}
# Sensori radar/telecamera ADAS dietro il paraurti/parabrezza: probabili sulle auto recenti
ADAS_PARTS = {"paraurti_anteriore": "calibrazione_radar_adas", "parabrezza": "calibrazione_telecamera_adas"}
ADAS_FROM_YEAR = 2018


def part_key(part: str, side: str | None = None) -> str:
    return f"{part}_{side}" if side and part in SIDED else part


def classify(severe_flags: list[str], items: list[dict]) -> str:
    """nessuno | leggero | medio | grave"""
    if any(f in SEVERE_FLAGS for f in severe_flags):
        return "grave"
    if not items:
        return "nessuno"
    if len(items) >= 5 or any(i.get("severity") == "grave" for i in items):
        return "grave"
    if len(items) >= 3 or any(i.get("severity") == "medio" for i in items):
        return "medio"
    return "leggero"


def hidden_parts(items: list[dict], year: int | None) -> list[str]:
    out: list[str] = []
    for it in items:
        if it.get("action") != "sostituire" and it.get("severity") == "leggero":
            continue
        out += HIDDEN_BY_PART.get(it["part"], [])
        if year and year >= ADAS_FROM_YEAR and it["part"] in ADAS_PARTS:
            out.append(ADAS_PARTS[it["part"]])
    return sorted(set(out))


def sanitize_items(items: list[dict]) -> list[dict]:
    """Tiene solo pezzi della tassonomia, con valori ammessi."""
    clean = []
    for it in items or []:
        part = it.get("part")
        if part not in PARTS:
            continue
        clean.append({
            "part": part,
            "side": it.get("side") if it.get("side") in ("sx", "dx") else None,
            "action": it.get("action") if it.get("action") in ("sostituire", "riparare") else "sostituire",
            "severity": it.get("severity") if it.get("severity") in ("leggero", "medio", "grave") else "medio",
            "source": it.get("source") if it.get("source") in ("testo", "foto") else "foto",
            "confidence": float(it.get("confidence", 0.5)),
            "note": str(it.get("note", ""))[:200],
        })
    return clean
