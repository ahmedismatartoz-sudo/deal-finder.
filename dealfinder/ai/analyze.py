"""Analisi AI di un annuncio: testo, foto (filtro e approfondimento), identità del veicolo.

L'AI legge e descrive; i numeri li calcola il codice. Ogni risposta viene
validata con regole prima di essere usata.
"""
from __future__ import annotations

import json
from datetime import date

from ..core.models import Listing
from ..core.normalize import FUEL_MAP, GEARBOX_MAP, slug
from . import client as ai
from .damage import PARTS, SEVERE_FLAGS, classify, sanitize_items

PART_LIST = ", ".join(PARTS)
SEVERE_LIST = ", ".join(SEVERE_FLAGS)

# ---------------------------------------------------------------------------
# 1. Lettura del testo
# ---------------------------------------------------------------------------
EXTRACT_SYSTEM = f"""Sei un esperto di auto usate in Italia. Leggi un annuncio e restituisci SOLO JSON.
Regole: non inventare. Se un dato non è scritto o deducibile con certezza, usa null.
Schema:
{{"make": str|null, "model": str|null, "version": str|null, "year": int|null, "mileage_km": int|null,
 "fuel": "benzina|diesel|gpl|metano|ibrida|elettrica"|null, "gearbox": "manuale|automatico"|null,
 "power_kw": int|null, "power_cv": int|null,
 "damage_declared": bool, "damage_items": [{{"part": uno tra [{PART_LIST}], "side": "sx|dx"|null,
   "action": "sostituire|riparare", "severity": "leggero|medio|grave", "note": str}}],
 "severe_flags": [sottoinsieme di [{SEVERE_LIST}]],
 "price_notes": [ "plus_iva" | "rata" | "trattabile" | "permuta" | "prezzo_finto" ],
 "seller_is_dealer": bool|null,
 "red_flags": [str]  // es. "km sospetti", "solo contatto telefonico fuori piattaforma", "anticipo richiesto", "auto all'estero"
}}"""


def extract_text(listing: Listing, usage_sink=None, listing_id=None) -> dict:
    text = f"TITOLO: {listing.title or ''}\nPREZZO: {listing.price_raw or listing.price_eur}\n" \
           f"DATI STRUTTURATI: marca={listing.make} modello={listing.model} anno={listing.year} " \
           f"km={listing.mileage_km} carburante={listing.fuel}\nDESCRIZIONE:\n{(listing.description or '')[:4000]}"
    r = ai.ask_json("estrazione_testo", ai.MODEL_FAST, EXTRACT_SYSTEM,
                    [{"type": "text", "text": text}], max_tokens=1200,
                    usage_sink=usage_sink, listing_id=listing_id)
    return r.data if isinstance(r.data, dict) else {}


def apply_extract(listing: Listing, data: dict) -> dict:
    """Riempie SOLO i campi mancanti, con controlli di plausibilità. Ritorna l'origine dei campi."""
    origin = {}
    this_year = date.today().year

    def fill(field, value, ok):
        if getattr(listing, field) in (None, "") and value not in (None, "") and ok(value):
            setattr(listing, field, value)
            origin[field] = "dedotto_ai"

    fill("make", slug(data.get("make")), lambda v: True)
    fill("model", slug(data.get("model")), lambda v: True)
    fill("version_raw", data.get("version"), lambda v: True)
    fill("year", data.get("year"), lambda v: isinstance(v, int) and 1990 <= v <= this_year)
    fill("mileage_km", data.get("mileage_km"), lambda v: isinstance(v, int) and 0 <= v <= 600_000)
    fill("fuel", FUEL_MAP.get(str(data.get("fuel") or "").lower()), lambda v: True)
    fill("gearbox", GEARBOX_MAP.get(str(data.get("gearbox") or "").lower()), lambda v: True)
    kw = data.get("power_kw") or (round(data["power_cv"] * 0.7355) if data.get("power_cv") else None)
    fill("power_kw", kw, lambda v: isinstance(v, int) and 30 <= v <= 400)

    if data.get("damage_declared"):
        listing.damage_declared = True
    flags = set(listing.price_flags)
    if "plus_iva" in (data.get("price_notes") or []):
        flags.add("plus_iva")
    if "rata" in (data.get("price_notes") or []):
        flags.add("leasing_o_rata")
    if "prezzo_finto" in (data.get("price_notes") or []):
        flags.add("prezzo_civetta")
    listing.price_flags = sorted(flags)
    if data.get("seller_is_dealer") is True and listing.seller_type == "sconosciuto":
        listing.seller_type = "commerciante"
    listing.compute_missing()
    return origin


# ---------------------------------------------------------------------------
# 2. Foto
# ---------------------------------------------------------------------------
PHOTO_SCREEN_SYSTEM = f"""Sei un perito di carrozzeria. Guardi poche foto di un annuncio d'auto usata.
Rispondi SOLO JSON:
{{"damage_visible": "si|no|incerto",
 "damage_items": [{{"part": uno tra [{PART_LIST}], "side": "sx|dx"|null, "action": "sostituire|riparare",
   "severity": "leggero|medio|grave", "confidence": 0-1}}],
 "severe_flags": [sottoinsieme di [{SEVERE_LIST}]],
 "photo_quality": "buona|scarsa", "exterior_fully_visible": bool,
 "stock_or_internet_photo": bool, "plate_text": str|null,
 "notes": str}}
Regole: descrivi solo ciò che si vede. Se una zona non è visibile non dichiararla integra.
Una foto scura, lontana o parziale = "incerto", non "no"."""

PHOTO_DEEP_SYSTEM = PHOTO_SCREEN_SYSTEM + """
Analisi approfondita: esamina tutte le foto, indica ogni pezzo da riparare o sostituire,
il lato, e se ci sono segni di riparazioni precedenti (differenze di colore, luci tra i pannelli).
Aggiungi al JSON: "previous_repairs": [str], "visible_identity": {"generation_hint": str|null,
"trim_hint": str|null, "body_type": str|null}"""


def analyze_photos(listing: Listing, deep: bool, usage_sink=None, listing_id=None) -> dict:
    urls = listing.photos[: (12 if deep else 3)]
    blocks = [b for b in (ai.image_block(u) for u in urls) if b]
    if not blocks:
        return {"damage_visible": "incerto", "damage_items": [], "severe_flags": [],
                "photo_quality": "scarsa", "exterior_fully_visible": False, "notes": "nessuna foto utilizzabile"}
    blocks.append({"type": "text", "text": f"Auto: {listing.title or ''} — {listing.make} {listing.model} {listing.year}. "
                                           f"Testo annuncio (estratto): {(listing.description or '')[:600]}"})
    r = ai.ask_json("foto_approfondite" if deep else "foto_filtro",
                    ai.MODEL_DEEP if deep else ai.MODEL_FAST,
                    PHOTO_DEEP_SYSTEM if deep else PHOTO_SCREEN_SYSTEM, blocks,
                    max_tokens=1800 if deep else 900, usage_sink=usage_sink, listing_id=listing_id)
    data = r.data if isinstance(r.data, dict) else {}
    data["damage_items"] = sanitize_items(data.get("damage_items"))
    data["severe_flags"] = [f for f in data.get("severe_flags") or [] if f in SEVERE_FLAGS]
    data["n_photos_analyzed"] = len(blocks) - 1
    return data


def merge_damage(text_data: dict, photo_data: dict) -> tuple[str, list[dict], list[str]]:
    """Unisce danni dichiarati e visibili; restituisce (classe, pezzi, segnali gravi)."""
    items: dict[tuple, dict] = {}
    for src, d in (("testo", text_data), ("foto", photo_data)):
        for it in sanitize_items(d.get("damage_items")):
            it["source"] = src
            key = (it["part"], it["side"])
            if key not in items or it["confidence"] > items[key]["confidence"]:
                items[key] = it
    severe = sorted(set((text_data.get("severe_flags") or []) + (photo_data.get("severe_flags") or [])))
    cls = classify(severe, list(items.values()))
    if cls == "nessuno" and photo_data.get("damage_visible") != "no":
        cls = "sconosciuto"     # nessun danno trovato ma foto insufficienti: non si presume sana
    if cls == "nessuno" and not photo_data.get("exterior_fully_visible", False):
        cls = "sconosciuto"
    return cls, list(items.values()), severe


# ---------------------------------------------------------------------------
# 3. Identificazione del veicolo
# ---------------------------------------------------------------------------
IDENTIFY_SYSTEM = """Identifica esattamente il veicolo per cercare ricambi compatibili.
Usa dati dell'annuncio, indizi dalle foto e (se presenti) dati da targa. Rispondi SOLO JSON:
{"make": str, "model": str, "generation": str|null, "facelift": bool|null, "body_type": str|null,
 "engine": str|null, "power_kw": int|null, "fuel": str|null, "year_from": int|null, "year_to": int|null,
 "search_name": str,   // nome da usare nelle ricerche ricambi, es. "Volkswagen Golf VII (5G1) 1.6 TDI 2017"
 "confidence": 0-1, "uncertain_points": [str]}"""


def identify_vehicle(listing: Listing, photo_data: dict, plate_data: dict | None = None,
                     usage_sink=None, listing_id=None) -> dict:
    info = {"titolo": listing.title, "marca": listing.make, "modello": listing.model,
            "versione": listing.version_raw, "anno": listing.year, "km": listing.mileage_km,
            "carburante": listing.fuel, "potenza_kw": listing.power_kw, "cambio": listing.gearbox,
            "indizi_foto": photo_data.get("visible_identity"), "dati_targa": plate_data}
    r = ai.ask_json("identificazione", ai.MODEL_FAST, IDENTIFY_SYSTEM,
                    [{"type": "text", "text": json.dumps(info, ensure_ascii=False)}], max_tokens=600,
                    usage_sink=usage_sink, listing_id=listing_id)
    data = r.data if isinstance(r.data, dict) else {}
    data["method"] = "targa" if plate_data else ("dati_annuncio+foto" if photo_data.get("visible_identity") else "dati_annuncio")
    if plate_data:
        data["confidence"] = max(float(data.get("confidence", 0)), 0.9)
    return data
