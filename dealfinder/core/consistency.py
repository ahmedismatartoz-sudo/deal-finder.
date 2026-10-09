"""Coerenza tra i dati strutturati dell'annuncio e quello che il venditore scrive.

Molti privati compilano a caso i campi di Subito (anno = il primo della lista, marca e modello
sbagliati) e scrivono i dati veri solo nel titolo o nella descrizione. Esempi visti:
  "Opel Corsa 2007" con campi Ford Transit Connect 2020
  "BMW 118d 2004" con anno 2024, "Meriva" con anno 2026 e 155.000 km
Con questi errori la stima del prezzo esplode (margini finti da 10-40.000 €), quindi:
  - l'anno scritto nel testo, se esplicito, corregge quello del campo;
  - se il testo nomina una marca diversa da quella del campo, l'annuncio è incoerente;
  - un'auto "nuova" con tantissimi km è incoerente.
Gli annunci incoerenti non diventano candidati e non insegnano i prezzi al modello.
"""
from __future__ import annotations

import datetime
import re

from .models import Listing

MAKES = {
    "fiat": "fiat", "alfa": "alfa-romeo", "alfa romeo": "alfa-romeo", "lancia": "lancia", "opel": "opel",
    "ford": "ford", "volkswagen": "volkswagen", "vw": "volkswagen", "audi": "audi", "bmw": "bmw",
    "mercedes": "mercedes", "mercedes-benz": "mercedes", "renault": "renault", "peugeot": "peugeot",
    "citroen": "citroen", "citroën": "citroen", "toyota": "toyota", "nissan": "nissan", "hyundai": "hyundai",
    "kia": "kia", "dacia": "dacia", "skoda": "skoda", "seat": "seat", "cupra": "cupra", "mini": "mini",
    "smart": "smart", "suzuki": "suzuki", "mazda": "mazda", "honda": "honda", "volvo": "volvo", "jeep": "jeep",
    "land rover": "land-rover", "range rover": "land-rover", "mitsubishi": "mitsubishi",
    "chevrolet": "chevrolet", "porsche": "porsche", "jaguar": "jaguar", "tesla": "tesla", "subaru": "subaru",
    "mg": "mg", "daihatsu": "daihatsu", "lexus": "lexus", "ds": "ds",
}
# modelli famosi che identificano la marca anche senza nominarla
MODEL_MAKE = {
    "panda": "fiat", "punto": "fiat", "500": "fiat", "tipo": "fiat", "doblo": "fiat", "golf": "volkswagen",
    "polo": "volkswagen", "passat": "volkswagen", "tiguan": "volkswagen", "corsa": "opel", "astra": "opel",
    "meriva": "opel", "zafira": "opel", "clio": "renault", "megane": "renault", "twingo": "renault",
    "yaris": "toyota", "aygo": "toyota", "micra": "nissan", "qashqai": "nissan", "fiesta": "ford",
    "focus": "ford", "ypsilon": "lancia", "musa": "lancia", "fabia": "skoda", "octavia": "skoda",
    "ibiza": "seat", "leon": "seat", "sandero": "dacia", "duster": "dacia", "giulietta": "alfa-romeo",
    "mito": "alfa-romeo", "c3": "citroen", "c4": "citroen", "i10": "hyundai", "i20": "hyundai",
    "picanto": "kia", "sportage": "kia",
}
_ALIASES = sorted(MAKES, key=len, reverse=True)
RE_MAKE = re.compile(r"\b(" + "|".join(re.escape(a) for a in _ALIASES) + r")\b", re.I)
RE_MODEL = re.compile(r"\b(" + "|".join(re.escape(m) for m in MODEL_MAKE) + r")\b", re.I)
RE_YEAR_EXPLICIT = re.compile(r"\b(?:anno|del|immatricolat[ao](?: nel)?|immatricolazione|dal)\s*:?\s*((?:19[6-9]|20[0-2])\d)\b",
                              re.I)
RE_YEAR = re.compile(r"\b((?:19[6-9]|20[0-2])\d)\b")


def text_makes(l: Listing) -> set[str]:
    head = " ".join(filter(None, [l.title, (l.description or "")[:300]])).lower()
    found = {MAKES[m.group(1).lower()] for m in RE_MAKE.finditer(head)}
    if not found:
        found = {MODEL_MAKE[m.group(1).lower()] for m in RE_MODEL.finditer((l.title or "").lower())}
    return found


def text_year(l: Listing) -> int | None:
    """Anno scritto esplicitamente dal venditore ("anno 2005", "del 2008", oppure nel titolo)."""
    years = [int(y) for y in RE_YEAR_EXPLICIT.findall(l.description or "")]
    years += [int(y) for y in RE_YEAR_EXPLICIT.findall(l.title or "")]
    if not years:
        years = [int(y) for y in RE_YEAR.findall(l.title or "")]
    years = [y for y in years if y <= datetime.date.today().year]
    return years[0] if len(set(years)) == 1 else None


def check(l: Listing, fix: bool = True) -> list[str]:
    """Problemi di coerenza (lista vuota = coerente). Con fix=True corregge l'anno dal testo."""
    problems = []
    makes = text_makes(l)
    if l.make and makes and l.make not in makes:
        problems.append("marca_diversa_dal_testo")
    ty = text_year(l)
    if ty and l.year and abs(ty - l.year) >= 2:
        if fix:
            l.year = ty
        else:
            problems.append("anno_diverso_dal_testo")
    if l.year and l.mileage_km is not None:
        age = max(0, datetime.date.today().year - l.year)
        if age <= 1 and l.mileage_km > 60_000 or age <= 3 and l.mileage_km > 200_000:
            problems.append("anno_incoerente_con_km")
    return problems
