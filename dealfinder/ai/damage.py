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

# Segnali che ESCLUDONO l'auto: danni strutturali o non stimabili
EXCLUDE_FLAGS = {
    "telaio_longheroni": "Possibile danno a telaio, longheroni o montanti",
    "tetto_montanti": "Tetto o montanti deformati",
    "alluvionata": "Auto alluvionata",
    "incendio": "Auto bruciata",
}
# Segnali AMMESSI ma ad alto rischio: l'auto resta sempre "da verificare", con riserva alta
HIGH_RISK_FLAGS = {
    "airbag_esplosi": "Airbag esplosi",
    "frontale_profondo": "Urto frontale profondo (radiatore/motore spostati)",
    "non_parte": "Non parte, causa non indicata",
    "motore_guasto": "Motore guasto o da sostituire",
    "cambio_guasto": "Cambio guasto o da sostituire",
    "sospensioni_sterzo": "Danni a sospensioni o sterzo",
    "guasto_ignoto": "Guasto dichiarato ma non identificato",
}
SEVERE_FLAGS = {**EXCLUDE_FLAGS, **HIGH_RISK_FLAGS}

# Guasti meccanici/elettrici comuni nelle auto "da sistemare": guasto -> (descrizione, ricambi da cercare)
FAULTS = {
    "frizione": ("Frizione (kit; volano se bimassa)", ["kit_frizione", "volano_bimassa"]),
    "distribuzione": ("Distribuzione", ["kit_distribuzione_pompa_acqua"]),
    "turbina": ("Turbocompressore", ["turbocompressore"]),
    "fap_egr": ("FAP/DPF o valvola EGR", ["filtro_antiparticolato", "valvola_egr"]),
    "iniettori": ("Iniettori", ["set_iniettori"]),
    "batteria": ("Batteria", ["batteria"]),
    "freni": ("Freni", ["kit_dischi_pastiglie_anteriori"]),
    "ammortizzatori": ("Ammortizzatori", ["coppia_ammortizzatori_anteriori"]),
    "clima": ("Climatizzatore", ["compressore_clima"]),
    "alternatore": ("Alternatore", ["alternatore"]),
    "motorino_avviamento": ("Motorino di avviamento", ["motorino_avviamento"]),
    "radiatore": ("Radiatore / raffreddamento", ["radiatore_motore"]),
    "pompa_alta_pressione": ("Pompa alta pressione", ["pompa_alta_pressione"]),
    "motore_sostituzione": ("Motore da sostituire (usato)", ["motore_usato_completo"]),
    "cambio_sostituzione": ("Cambio da sostituire (usato)", ["cambio_usato_completo"]),
    "airbag": ("Airbag e cinture", ["modulo_airbag_volante", "modulo_airbag_passeggero",
                                     "centralina_airbag", "pretensionatori_cinture"]),
}
HIGH_RISK_FAULTS = {"motore_sostituzione", "cambio_sostituzione", "airbag"}
MECH_PART_LABELS = {
    "kit_frizione": "Kit frizione", "volano_bimassa": "Volano bimassa",
    "kit_distribuzione_pompa_acqua": "Kit distribuzione con pompa acqua", "turbocompressore": "Turbocompressore",
    "filtro_antiparticolato": "Filtro antiparticolato (FAP/DPF)", "valvola_egr": "Valvola EGR",
    "set_iniettori": "Set iniettori", "batteria": "Batteria", "kit_dischi_pastiglie_anteriori": "Dischi e pastiglie anteriori",
    "coppia_ammortizzatori_anteriori": "Coppia ammortizzatori anteriori", "compressore_clima": "Compressore climatizzatore",
    "alternatore": "Alternatore", "motorino_avviamento": "Motorino di avviamento", "radiatore_motore": "Radiatore motore",
    "pompa_alta_pressione": "Pompa alta pressione", "motore_usato_completo": "Motore usato completo",
    "cambio_usato_completo": "Cambio usato completo", "modulo_airbag_volante": "Modulo airbag volante",
    "modulo_airbag_passeggero": "Modulo airbag passeggero", "centralina_airbag": "Centralina airbag",
    "pretensionatori_cinture": "Pretensionatori cinture",
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
    """nessuno | leggero | medio | alto_rischio | grave (grave = escluso)"""
    if any(f in EXCLUDE_FLAGS for f in severe_flags):
        return "grave"
    if any(f in HIGH_RISK_FLAGS for f in severe_flags) or any(i.get("part") in HIGH_RISK_FAULTS for i in items):
        return "alto_rischio"
    if not items:
        return "nessuno"
    if len(items) >= 6:
        return "alto_rischio"
    if len(items) >= 3 or any(i.get("severity") in ("medio", "grave") for i in items) \
            or any(i.get("part") in FAULTS for i in items):
        return "medio"
    return "leggero"


def is_fault(item: dict) -> bool:
    return item.get("part") in FAULTS


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
        if part not in PARTS and part not in FAULTS:
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
