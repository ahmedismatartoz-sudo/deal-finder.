"""Adattatore per lo scraper esterno di Facebook Marketplace.

Deal Finder non raccoglie da Facebook direttamente: chiama un fornitore
esterno (default: un "actor" Apify) e traduce i suoi risultati nel formato
comune. Per cambiare fornitore basta un'altra classe con gli stessi metodi.

Il formato dei risultati dipende dallo scraper scelto: FIELD_MAP va
adattata al primo esempio reale di dati.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterator


from ..core.models import Listing
from ..core.normalize import parse_int
from .base import Collector

log = logging.getLogger(__name__)

APIFY_RUN_SYNC = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"

# campo comune -> possibili chiavi nel risultato del fornitore (prima trovata vince)
FIELD_MAP: dict[str, tuple[str, ...]] = {
    "id": ("id", "listingId", "marketplace_listing_id"),
    "url": ("url", "listingUrl", "link"),
    "title": ("title", "marketplace_listing_title", "name"),
    "description": ("description", "redacted_description", "text"),
    "price": ("price", "listing_price", "formatted_price", "amount"),
    "city": ("city", "location", "location_text"),
    "photos": ("images", "photos", "listing_photos", "primary_listing_photo"),
    "year": ("year", "vehicle_year"),
    "mileage": ("mileage", "vehicle_odometer_data", "odometer"),
    "make": ("make", "vehicle_make_display_name"),
    "model": ("model", "vehicle_model_display_name"),
    "fuel": ("fuel", "vehicle_fuel_type"),
    "gearbox": ("transmission", "vehicle_transmission_type"),
}


def _get(item: dict, field: str) -> Any:
    for key in FIELD_MAP[field]:
        if key in item and item[key] not in (None, "", []):
            return item[key]
    return None


def _num(v: Any) -> int | None:
    if isinstance(v, dict):
        v = v.get("value") or v.get("amount") or v.get("formatted_amount")
    return parse_int(v)


def _photos(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, dict):
        v = [v]
    out = []
    for p in v:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict):
            u = p.get("url") or p.get("uri") or (p.get("image") or {}).get("uri")
            if u:
                out.append(u)
    return out


def parse_item(item: dict) -> Listing | None:
    lid = _get(item, "id")
    url = _get(item, "url")
    if not lid and url:
        m = re.search(r"/item/(\d+)", str(url))
        lid = m.group(1) if m else None
    if not lid:
        return None
    if not url:
        url = f"https://www.facebook.com/marketplace/item/{lid}/"
    city = _get(item, "city")
    if isinstance(city, dict):
        city = city.get("city") or city.get("name") or str(city)
    year = _num(_get(item, "year"))
    title = _get(item, "title") or ""
    if not year:
        m = re.search(r"\b(19[89]\d|20[0-3]\d)\b", str(title))
        year = int(m.group(1)) if m else None
    return Listing(
        source="facebook",
        source_id=str(lid),
        url=str(url),
        title=title or None,
        description=_get(item, "description"),
        make=_get(item, "make"),
        model=_get(item, "model"),
        year=year,
        mileage_km=_num(_get(item, "mileage")),
        fuel=_get(item, "fuel"),
        gearbox=_get(item, "gearbox"),
        price_raw=str(_get(item, "price")) if _get(item, "price") is not None else None,
        price_eur=_num(_get(item, "price")),
        seller_type="privato",   # Marketplace: in prevalenza privati; da confermare dai dati
        city=city,
        photos=_photos(_get(item, "photos")),
        raw=item,
    )


class MetaProviderCollector(Collector):
    """Chiama lo scraper esterno. Configurazione tramite variabili d'ambiente:
    META_PROVIDER_TOKEN, META_ACTOR_ID, e i parametri di ricerca in query."""
    source = "facebook"

    def __init__(self, token: str | None = None, actor: str | None = None,
                 client=None):
        self.token = token or os.environ.get("META_PROVIDER_TOKEN")
        self.actor = actor or os.environ.get("META_ACTOR_ID")
        import httpx
        self.client = client or httpx.Client(timeout=600)

    def configured(self) -> bool:
        return bool(self.token and self.actor)

    def search(self, query: dict) -> Iterator[Listing]:
        if not self.configured():
            log.info("meta: fornitore non configurato, salto")
            return
        actor = self.actor.replace("/", "~")
        r = self.client.post(APIFY_RUN_SYNC.format(actor=actor),
                             params={"token": self.token},
                             json=query.get("provider_input", {}))
        r.raise_for_status()
        for item in r.json():
            listing = parse_item(item)
            if listing:
                yield listing

    def fetch(self, url: str) -> Listing | None:
        # La verifica di disponibilità dipende dal fornitore: per ora si
        # considera scomparso un annuncio che non ricompare nelle ricerche.
        raise NotImplementedError
