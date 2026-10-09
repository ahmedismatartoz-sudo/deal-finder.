"""Dati del veicolo dalla targa tramite un servizio esterno.

Usato SOLO per identificare il modello esatto (marca, modello, versione, motore,
anno), mai per risalire al proprietario. La targa non viene salvata in chiaro:
si conserva solo l'impronta (hash con PLATE_SALT).

Fornitori (si usa il primo configurato):
  - RegCheck (regcheck.org.uk, endpoint CheckItaly): REGCHECK_USERNAME. Circa 0,30 AUD
    (~0,18 €) a ricerca a pacchetti da 100, 10 ricerche gratis per provare.
  - openapi.com (Automotive, Italia): OPENAPI_TOKEN, circa 0,40 € a ricerca.
    Indirizzo: OPENAPI_PLATE_URL, predefinito https://automotive.openapi.com/IT-car/{plate}
    (da confermare sulla console openapi.com quando si attiva il servizio).
  - generico: PLATE_LOOKUP_URL (con {plate}) + PLATE_LOOKUP_KEY.
"""
from __future__ import annotations

import logging
import os
import re

log = logging.getLogger(__name__)

RE_PLATE_IT = re.compile(r"^[A-Z]{2}\d{3}[A-Z]{2}$")
OPENAPI_DEFAULT_URL = "https://automotive.openapi.com/IT-car/{plate}"

# nomi dei campi come possono arrivare dai fornitori -> nome nostro
FIELD_ALIASES = {
    "make": ("make", "CarMake", "carMake", "brand", "Marca", "marca"),
    "model": ("model", "CarModel", "carModel", "Modello", "modello"),
    "version": ("version", "Version", "Description", "description", "Allestimento", "versione"),
    "fuel": ("fuel", "FuelType", "fuelType", "Alimentazione", "alimentazione"),
    "power_kw": ("power_kw", "PowerKW", "powerKw", "KW", "kw", "PowerKw"),
    "power_cv": ("PowerCV", "powerCv", "CV", "cv", "PowerCv"),
    "engine_cc": ("engine", "EngineSize", "engineSize", "Cilindrata", "cilindrata"),
    "year": ("year", "RegistrationYear", "registrationYear", "AnnoImmatricolazione", "anno"),
    "registration_date": ("registration_date", "RegistrationDate", "ImmatricolationDate", "DataImmatricolazione"),
    "body_type": ("body_type", "BodyType", "bodyType", "Carrozzeria"),
    "gearbox": ("gearbox", "Gearbox", "Transmission", "Cambio"),
    "doors": ("doors", "NumberOfDoors", "Porte"),
}


def normalize_plate(plate: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (plate or "").upper())


def valid_plate(plate: str | None) -> bool:
    return bool(RE_PLATE_IT.match(normalize_plate(plate)))


REGCHECK_URL = "https://www.regcheck.org.uk/api/reg.asmx/CheckItaly"


def configured() -> bool:
    return bool(os.environ.get("REGCHECK_USERNAME") or os.environ.get("OPENAPI_TOKEN")
                or os.environ.get("PLATE_LOOKUP_URL"))


def _pick(data: dict, names: tuple):
    for n in names:
        v = data.get(n)
        if isinstance(v, dict):               # RegCheck: {"CurrentTextValue": "..."}
            v = v.get("CurrentTextValue") or v.get("value")
        if v not in (None, "", 0):
            return v
    return None


def parse_regcheck(xml_text: str) -> dict | None:
    """RegCheck risponde in XML con dentro <vehicleJson> (JSON con i dati tecnici)."""
    import json
    import re as _re
    m = _re.search(r"<vehicleJson>(.*?)</vehicleJson>", xml_text or "", _re.S)
    if not m:
        return None
    import html
    try:
        data = json.loads(html.unescape(m.group(1)))
    except ValueError:
        return None
    return parse_vehicle(data)


def _int(v):
    try:
        return int(float(str(v).replace(",", "."))) if v is not None else None
    except ValueError:
        return None


def parse_vehicle(data: dict) -> dict | None:
    """Traduce la risposta del fornitore nei nostri campi (solo dati tecnici)."""
    if not isinstance(data, dict):
        return None
    for wrap in ("data", "result", "vehicle"):
        if isinstance(data.get(wrap), dict):
            data = data[wrap]
    out = {k: _pick(data, names) for k, names in FIELD_ALIASES.items()}
    if out.get("power_kw") is None and out.get("power_cv") is not None:
        cv = _int(out["power_cv"])
        out["power_kw"] = round(cv * 0.7355) if cv else None
    for k in ("power_kw", "engine_cc", "doors"):
        out[k] = _int(out.get(k))
    year = _int(out.get("year"))
    if year is None and out.get("registration_date"):
        m = re.search(r"(19|20)\d{2}", str(out["registration_date"]))
        year = int(m.group(0)) if m else None
    out["year"] = year
    out.pop("power_cv", None)
    out = {k: v for k, v in out.items() if v is not None}
    return out if out.get("make") and out.get("model") else None


def lookup(plate: str, http=None) -> dict | None:
    plate = normalize_plate(plate)
    if not plate or not configured():
        return None
    try:
        import httpx
        http = http or httpx.Client(timeout=20)
        if os.environ.get("REGCHECK_USERNAME"):
            r = http.get(os.environ.get("REGCHECK_URL", REGCHECK_URL),
                         params={"RegistrationNumber": plate, "username": os.environ["REGCHECK_USERNAME"]})
            r.raise_for_status()
            return parse_regcheck(r.text)
        if os.environ.get("OPENAPI_TOKEN"):
            url = os.environ.get("OPENAPI_PLATE_URL", OPENAPI_DEFAULT_URL).format(plate=plate)
            headers = {"Authorization": f"Bearer {os.environ['OPENAPI_TOKEN']}"}
        else:
            url = os.environ["PLATE_LOOKUP_URL"].format(plate=plate)
            headers = {"Authorization": f"Bearer {os.environ.get('PLATE_LOOKUP_KEY', '')}"}
        r = http.get(url, headers=headers)
        r.raise_for_status()
        return parse_vehicle(r.json())
    except Exception as e:
        log.warning("ricerca targa fallita: %s", str(e)[:200])
        return None


def cached_lookup(conn, plate: str, lookup_fn=None, days: int = 180) -> dict | None:
    """Come lookup(), ma una targa già cercata negli ultimi `days` giorni non si paga di nuovo."""
    import json
    from ..config import settings
    from ..core.normalize import plate_hash
    lookup_fn = lookup_fn or lookup
    p = normalize_plate(plate)
    if conn is None or not p:
        return lookup_fn(p)
    h = plate_hash(p, settings.plate_salt)
    row = conn.execute("SELECT vehicle FROM plate_cache WHERE plate_hash=%s "
                       "AND searched_at > now() - make_interval(days => %s)", (h, days)).fetchone()
    if row and row["vehicle"]:
        v = row["vehicle"]
        return json.loads(v) if isinstance(v, str) else v
    v = lookup_fn(p)
    if v:
        conn.execute("INSERT INTO plate_cache (plate_hash, vehicle) VALUES (%s,%s) ON CONFLICT (plate_hash) "
                     "DO UPDATE SET vehicle=EXCLUDED.vehicle, searched_at=now()", (h, json.dumps(v)))
        conn.commit()
    return v
