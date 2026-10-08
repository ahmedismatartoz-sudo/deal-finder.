"""Facebook Marketplace tramite Bright Data (Web Scraper API, asincrona).

Flusso automatico ad ogni giro:
  1. trigger: invia a Bright Data gli indirizzi di ricerca Marketplace (città, raggio, prezzo)
  2. progress: controlla finché lo snapshot è pronto
  3. download: scarica gli annunci e li traduce nel formato comune

Nomi dei campi verificati su dati reali del dataset Marketplace
(product_id, url, title, description, final_price/initial_price, currency,
country_code, location, images, brand, transmission, car_miles, is_sold).

Variabili d'ambiente:
  BRIGHTDATA_API_KEY      chiave API (obbligatoria)
  BRIGHTDATA_DATASET      default gd_lvt9iwuh6fbcwmx1a (Marketplace)
  BRIGHTDATA_SEARCHES     JSON, lista di ricerche; default Milano 60 km e Brescia 30 km, per fasce di prezzo
  BRIGHTDATA_LIMIT        annunci massimi per ricerca (default 300): limita la spesa
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Iterator
from urllib.parse import urlencode

from ..core.models import Listing
from .base import Collector

log = logging.getLogger(__name__)

API = "https://api.brightdata.com/datasets/v3"
DEFAULT_DATASET = "gd_lvt9iwuh6fbcwmx1a"
# Facebook mostra un numero limitato di risultati per ricerca: dividere per fasce di prezzo
# fa sì che ogni ricerca resti sotto il limite e insieme coprano quasi tutto.
DEFAULT_SEARCHES = [
    {"city": "milan", "radius": 60, "min_price": 500, "max_price": 3000},
    {"city": "milan", "radius": 60, "min_price": 3000, "max_price": 6000},
    {"city": "milan", "radius": 60, "min_price": 6000, "max_price": 10000},
    {"city": "milan", "radius": 60, "min_price": 10000, "max_price": 20000},
    {"city": "brescia", "radius": 30, "min_price": 500, "max_price": 8000},
    {"city": "brescia", "radius": 30, "min_price": 8000, "max_price": 20000},
]
RE_KM = re.compile(r"(?<!\d)(\d{1,3}(?:[ .]\d{3})+|\d{4,7})\s*(?:km|chilometri)\b", re.I)
RE_YEAR = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
NOT_A_CAR = re.compile(r"\b(ricambi|vendo motore|motore in vendita|smembro|monopattino|scooter|moto(?:cicletta)?|"
                       r"bici|camper|roulotte|trattore|furgone)\b", re.I)


def search_url(city: str, radius: int, min_price: int, max_price: int, days: int = 1) -> str:
    q = urlencode({"minPrice": min_price, "maxPrice": max_price, "daysSinceListed": days,
                   "sortBy": "creation_time_descend", "radius": radius, "exact": "false"})
    return f"https://www.facebook.com/marketplace/{city}/vehicles?{q}"


def mileage_from(row: dict) -> int | None:
    """Chilometri: dal testo dell'annuncio; il campo car_miles del fornitore ha unità incerta
    e si usa solo se coincide con un valore in km scritto nella descrizione."""
    desc = row.get("description") or ""
    values = {int(re.sub(r"[ .]", "", x)) for x in RE_KM.findall(desc)}
    miles = row.get("car_miles")
    if isinstance(miles, (int, float)) and int(miles) in values:
        return int(miles)
    if len(values) == 1:
        km = values.pop()
        return km if km <= 900_000 else None
    return None


def parse_row(row: dict, max_price: int = 20_000) -> Listing | None:
    if not isinstance(row, dict) or row.get("error") or row.get("error_code"):
        return None
    if row.get("country_code") not in (None, "IT") or row.get("currency") not in (None, "EUR"):
        return None
    if row.get("is_sold") is True:
        return None
    pid, url = row.get("product_id"), row.get("url")
    if pid is None or not url:
        return None
    title = (row.get("title") or "").strip()
    desc = row.get("description") or ""
    if NOT_A_CAR.search(title) or NOT_A_CAR.search(desc[:300]):
        return None
    price = row.get("final_price") if row.get("final_price") is not None else row.get("initial_price")
    try:
        price = int(float(price))
    except (TypeError, ValueError):
        return None
    if not 0 < price < max_price:
        return None
    m = RE_YEAR.search(title)
    loc = row.get("location")
    city = loc.get("city") if isinstance(loc, dict) else (str(loc).split(",")[0].strip() if loc else None)
    trans = row.get("transmission")
    images = [i for i in (row.get("images") or []) if isinstance(i, str) and i.startswith("https://")]
    return Listing(
        source="facebook", source_id=str(pid), url=str(url).split("?")[0],
        title=title or None, description=desc or None,
        make=row.get("brand") or None,
        year=int(m.group(1)) if m else None,
        mileage_km=mileage_from(row),
        gearbox={"MANUAL": "manuale", "AUTOMATIC": "automatico"}.get(str(trans).upper()) if trans else None,
        price_raw=str(price), price_eur=price,
        seller_type="privato",   # Marketplace: in prevalenza privati; l'AI corregge se il testo indica un'azienda
        city=city, photos=images[:30], raw=row,
    )


class BrightDataFacebookCollector(Collector):
    source = "facebook"

    def __init__(self, key: str | None = None, client=None):
        self.key = key or os.environ.get("BRIGHTDATA_API_KEY")
        self.dataset = os.environ.get("BRIGHTDATA_DATASET", DEFAULT_DATASET)
        self.limit = int(os.environ.get("BRIGHTDATA_LIMIT", "300"))
        raw = os.environ.get("BRIGHTDATA_SEARCHES")
        self.searches = json.loads(raw) if raw else DEFAULT_SEARCHES
        self._client = client

    def configured(self) -> bool:
        return bool(self.key)

    @property
    def client(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=120, headers={"Authorization": f"Bearer {self.key}"})
        return self._client

    def trigger(self, urls: list[str]) -> str:
        r = self.client.post(f"{API}/trigger",
                             params={"dataset_id": self.dataset, "include_errors": "true",
                                     "type": "discover_new", "discover_by": "url",
                                     "limit_per_input": self.limit},
                             json=[{"url": u, "country": "IT"} for u in urls])
        r.raise_for_status()
        sid = r.json().get("snapshot_id")
        if not sid:
            raise RuntimeError("Bright Data: nessuno snapshot_id nella risposta")
        return sid

    def wait(self, snapshot_id: str, max_minutes: int = 40) -> None:
        deadline = time.time() + max_minutes * 60
        while time.time() < deadline:
            r = self.client.get(f"{API}/progress/{snapshot_id}")
            r.raise_for_status()
            status = r.json().get("status")
            if status == "ready":
                return
            if status == "failed":
                raise RuntimeError(f"Bright Data: snapshot {snapshot_id} fallito")
            time.sleep(30)
        raise TimeoutError(f"Bright Data: snapshot {snapshot_id} non pronto dopo {max_minutes} minuti")

    def download(self, snapshot_id: str) -> list[dict]:
        r = self.client.get(f"{API}/snapshot/{snapshot_id}", params={"format": "json"})
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def search(self, query: dict) -> Iterator[Listing]:
        if not self.configured():
            log.info("Bright Data non configurato (BRIGHTDATA_API_KEY), salto Facebook")
            return
        max_price = query.get("max_price", 20_000)
        urls = [search_url(s["city"], s.get("radius", 40), s.get("min_price", 500),
                           s.get("max_price", max_price), s.get("days", 1)) for s in self.searches]
        sid = self.trigger(urls)
        log.info("Bright Data: snapshot %s avviato per %d ricerche", sid, len(urls))
        self.wait(sid)
        rows = self.download(sid)
        kept = 0
        for row in rows:
            listing = parse_row(row, max_price)
            if listing:
                kept += 1
                yield listing
        log.info("Bright Data: %d righe ricevute, %d auto valide", len(rows), kept)

    def fetch(self, url: str) -> Listing | None:
        raise NotImplementedError("Disponibilità Facebook: annuncio non più restituito per 48 ore = scomparso")
