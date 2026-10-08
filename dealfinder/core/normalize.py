"""Pulizia prezzi, campi standard, deduplica."""
from __future__ import annotations

import hashlib
import re
import unicodedata

from .models import Listing

FUEL_MAP = {
    "diesel": "diesel", "gasolio": "diesel",
    "benzina": "benzina", "petrol": "benzina", "gasoline": "benzina",
    "gpl": "gpl", "lpg": "gpl", "benzina/gpl": "gpl",
    "metano": "metano", "benzina/metano": "metano", "cng": "metano",
    "ibrida": "ibrida", "hybrid": "ibrida", "elettrica/benzina": "ibrida", "elettrica/diesel": "ibrida",
    "elettrica": "elettrica", "electric": "elettrica",
}
GEARBOX_MAP = {"manuale": "manuale", "manual": "manuale",
               "automatico": "automatico", "automatic": "automatico",
               "semiautomatico": "automatico", "sequenziale": "automatico"}

PROVINCE_REGION = {"MI": "Lombardia", "MB": "Lombardia", "BG": "Lombardia", "BS": "Lombardia",
                   "CO": "Lombardia", "VA": "Lombardia", "LC": "Lombardia", "LO": "Lombardia",
                   "PV": "Lombardia", "CR": "Lombardia", "MN": "Lombardia", "SO": "Lombardia"}

RE_PLUS_IVA = re.compile(r"\+\s*iva|iva\s*esclusa|esclusa\s*iva|oltre\s*iva", re.I)
RE_LEASING = re.compile(r"\b(rata|rate|al\s*mese|/\s*mese|leasing|noleggio|anticipo|finanziamento\s+da)\b", re.I)
RE_IMPORT = re.compile(r"\b(da\s+immatricolare|import(azione)?|km\s*0\s*estero|targa\s+(tedesca|estera))\b", re.I)
RE_PROBLEM = re.compile(
    r"\b(incident(?:at)?[ao]|indidentat[ao]|incidntat[ao]|sinistrat[ao]|danneggiat[ao]|grandinat[ao]|da sistemare|da riparare|da rivedere|"
    r"non (?:parte|si accende|va in moto)|guast[oai]|rott[oaie]|spia (?:motore|accesa|airbag)|"
    r"frizione (?:da|che) |distribuzione da|turbina (?:da|rotta)|motore (?:da|fuso|rotto|grippato)|"
    r"cambio (?:da|rotto)|fumo bianco|perde olio|batte in testa|cos[iì] com['’]? ?[eè]|per commercianti|"
    r"solo esportazione|per pezzi)\b", re.I)
RE_NEGATION = re.compile(r"(mai|non|nessun[ao]?|zero|senza)\s+(\w+\s+){0,2}$", re.I)
RE_PRICE_TEXT = re.compile(r"(?:prezzo|chiedo|richiesta|vendo a|valore)\s*:?\s*(?:€|euro)?\s*(\d{1,2}[.\s]?\d{3})\b|"
                           r"\b(?<![\d.])(\d{1,2}[.]?\d{3})\s*(?:€|euro|eur)\b", re.I)
RE_PLATE = re.compile(r"\b([A-Z]{2})\s?(\d{3})\s?([A-Z]{2})\b")

KW_PER_CV = 0.7355


def slug(s: str | None) -> str | None:
    if s is None:
        return None
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or None


def parse_int(s) -> int | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    digits = re.sub(r"[^\d]", "", str(s))
    return int(digits) if digits else None


def cv_to_kw(cv: int | None) -> int | None:
    return round(cv * KW_PER_CV) if cv else None


def normalize_price(listing: Listing) -> Listing:
    """Porta il prezzo a 'IVA inclusa' e segna i prezzi inaffidabili."""
    text = " ".join(filter(None, [listing.price_raw, listing.title, listing.description]))
    flags = set(listing.price_flags)
    price = listing.price_eur if listing.price_eur is not None else parse_int(listing.price_raw)

    if price is not None and RE_PLUS_IVA.search(text):
        flags.add("plus_iva")
        price = round(price * 1.22)
    if RE_LEASING.search(text):
        flags.add("leasing_o_rata")
    if RE_IMPORT.search(text):
        flags.add("importazione")
    # Prezzo messo a caso nel campo (1 €, 123456 €) ma scritto giusto nel testo: si recupera
    if price is not None and (price < 500 or re.fullmatch(r"(\d)\1{4,}|12345\d*|99999\d*", str(price))):
        found = [int(re.sub(r"\D", "", a or b)) for a, b in RE_PRICE_TEXT.findall(listing.description or "")]
        found = [x for x in found if 500 <= x <= 60000]
        if len(set(found)) == 1 and "leasing_o_rata" not in flags:
            price = found[0]
            flags.add("prezzo_da_descrizione")
    if price is not None and price < 500:
        flags.add("prezzo_civetta")
    if listing.description and re.search(r"trattabil", listing.description, re.I):
        flags.add("trattabile")

    listing.price_eur = price
    listing.price_flags = sorted(flags)
    return listing


def problem_hint(listing: Listing) -> list[str]:
    """Parole che indicano un'auto con problemi; ignora le frasi negate ("mai incidentata")."""
    text = " ".join(filter(None, [listing.title, listing.description]))
    hits = []
    for m in RE_PROBLEM.finditer(text):
        if RE_NEGATION.search(text[max(0, m.start() - 25):m.start()]):
            continue
        hits.append(m.group(0).lower())
    return hits


def normalize_fields(listing: Listing) -> Listing:
    listing.make = slug(listing.make)
    listing.model = slug(listing.model)
    if listing.fuel:
        listing.fuel = FUEL_MAP.get(listing.fuel.strip().lower(), slug(listing.fuel))
    if listing.gearbox:
        listing.gearbox = GEARBOX_MAP.get(listing.gearbox.strip().lower(), slug(listing.gearbox))
    if listing.province and not listing.region:
        listing.region = PROVINCE_REGION.get(listing.province.upper())
    normalize_price(listing)
    if listing.damage_declared is None and problem_hint(listing):
        listing.damage_declared = True     # esclusa dai confronti "sani", usata per il mercato "da sistemare"
    listing.compute_missing()
    return listing


def find_plate(text: str | None) -> str | None:
    """Targa italiana nel testo (formato AA123BB). Va solo hashata, mai salvata."""
    if not text:
        return None
    m = RE_PLATE.search(text.upper())
    return "".join(m.groups()) if m else None


def plate_hash(plate: str, salt: str) -> str:
    return hashlib.sha256((salt + plate).encode()).hexdigest()


def fingerprint(listing: Listing) -> str:
    """Impronta per riconoscere la stessa auto su fonti diverse.

    Km arrotondati a 5.000 e zona per regione: tollera piccole differenze
    tra annunci. Il prezzo non entra (cambia nel tempo). Le foto si
    confrontano a parte con l'hash percettivo.
    """
    km_bucket = (listing.mileage_km or 0) // 5000
    key = "|".join(str(x) for x in (listing.make, listing.model, listing.year,
                                     listing.fuel, listing.power_kw or "", km_bucket,
                                     listing.region or listing.province or ""))
    return hashlib.sha1(key.encode()).hexdigest()


def hamming(a: str, b: str) -> int:
    """Distanza tra due hash percettivi esadecimali."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def same_photo(phash_a: str, phash_b: str, max_distance: int = 6) -> bool:
    return hamming(phash_a, phash_b) <= max_distance
