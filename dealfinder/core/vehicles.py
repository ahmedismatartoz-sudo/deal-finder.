"""Marca e modello dal titolo dell'annuncio (es. Facebook: "2016 Volkswagen Golf 1.6 TDI").

Regole semplici e verificabili; i casi non riconosciuti restano vuoti e li completa l'AI.
"""
from __future__ import annotations

import re

from .normalize import slug

# alias (minuscolo) -> marca canonica
MAKES = {
    "alfa romeo": "alfa-romeo", "alfa": "alfa-romeo", "audi": "audi", "bmw": "bmw", "byd": "byd",
    "chevrolet": "chevrolet", "citroen": "citroen", "citroën": "citroen", "cupra": "cupra", "dacia": "dacia",
    "daihatsu": "daihatsu", "dodge": "dodge", "ds": "ds", "fiat": "fiat", "ford": "ford", "honda": "honda",
    "hyundai": "hyundai", "infiniti": "infiniti", "isuzu": "isuzu", "jaguar": "jaguar", "jeep": "jeep",
    "kia": "kia", "lancia": "lancia", "land rover": "land-rover", "range rover": "land-rover", "lexus": "lexus",
    "mazda": "mazda", "mercedes-benz": "mercedes-benz", "mercedes benz": "mercedes-benz", "mercedes": "mercedes-benz",
    "mg": "mg", "mini": "mini", "mitsubishi": "mitsubishi", "nissan": "nissan", "opel": "opel",
    "peugeot": "peugeot", "porsche": "porsche", "renault": "renault", "seat": "seat", "skoda": "skoda",
    "škoda": "skoda", "smart": "smart", "ssangyong": "ssangyong", "subaru": "subaru", "suzuki": "suzuki",
    "tesla": "tesla", "toyota": "toyota", "volkswagen": "volkswagen", "vw": "volkswagen", "volvo": "volvo",
    "lynk & co": "lynk-co", "lynk&co": "lynk-co", "dr": "dr", "maserati": "maserati", "abarth": "abarth",
    "polestar": "polestar", "saab": "saab", "chrysler": "chrysler", "genesis": "genesis",
}
# modelli che sono solo numeri (non vanno scambiati per una cilindrata)
NUMERIC_MODELS = {
    "peugeot": {"106", "107", "108", "205", "206", "207", "208", "306", "307", "308", "407", "408", "508", "607",
                "807", "1007", "2008", "3008", "4007", "4008", "5008"},
    "fiat": {"500", "600", "124"}, "abarth": {"500", "595", "695", "124"},
    "alfa-romeo": {"145", "146", "147", "156", "159", "166"}, "mazda": {"2", "3", "5", "6"},
    "ds": {"3", "4", "5", "7"}, "renault": {"4", "5"}, "volvo": {"240", "740", "850", "940"},
}
# modelli che occupano due parole
TWO_WORD_PREFIX = {"serie", "classe", "series", "class", "range", "grand", "c4", "nuova", "new", "up!"}
_ALIASES = sorted(MAKES, key=len, reverse=True)
_RE_YEAR = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")


def make_model_from_title(title: str | None) -> tuple[str | None, str | None]:
    if not title:
        return None, None
    t = _RE_YEAR.sub(" ", title.lower())
    t = re.sub(r"\s+", " ", t).strip()
    for alias in _ALIASES:
        m = re.search(r"(?:^|\s)" + re.escape(alias) + r"(?:\s|$)", t)
        if not m:
            continue
        make = MAKES[alias]
        rest = t[m.end():].split()
        if not rest:
            return make, None
        words = [rest[0]]
        if rest[0] in TWO_WORD_PREFIX and len(rest) > 1:
            words.append(rest[1])
        if rest[0] in ("nuova", "new") and len(rest) > 1:
            words = [rest[1]]
        model = slug(" ".join(words))
        # una cilindrata o una sigla motore non è un modello
        if model and re.fullmatch(r"\d(\.\d)?|\d{3,4}|tdi|tsi|hdi|cdi|jtd|mjt|gpl|diesel|benzina", model) \
                and model not in NUMERIC_MODELS.get(make, ()):
            model = None
        return make, model
    return None, None
