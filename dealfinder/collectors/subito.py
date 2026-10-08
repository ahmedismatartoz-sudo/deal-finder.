"""Collettore Subito.it.

Le pagine di Subito contengono i dati degli annunci in un JSON
(__NEXT_DATA__). La lettura è difensiva: cerca i campi per etichetta, così
un piccolo cambio di struttura non rompe tutto. Se la struttura cambia
davvero, il collettore restituisce zero annunci e il pannello
amministratore lo segnala.

DA VERIFICARE al primo avvio reale: percorsi JSON e nomi delle province
nell'indirizzo di ricerca (vedi SUBITO_PROVINCES in config).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterator


from ..core.models import Listing
from ..core.normalize import parse_int
from .base import Collector, find_key, next_data

log = logging.getLogger(__name__)

BASE = "https://www.subito.it"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9",
}

LABELS = {
    "price": ("prezzo",),
    "year": ("anno", "immatricolazione"),
    "mileage": ("km", "chilometr"),
    "fuel": ("carburante", "alimentazione"),
    "gearbox": ("cambio",),
    "power": ("potenza",),
    "make": ("marca",),
    "model": ("modello",),
    "version": ("versione", "allestimento"),
    "damage": ("condizion", "danneggiat", "incidentat"),
}


def search_url(region: str, province: str, max_price: int, page: int, private_only: bool = False) -> str:
    url = f"{BASE}/annunci-{region}/vendita/auto/{province}/?pe={max_price}&o={page}"
    if private_only:
        url += "&advt=0"
    return url


def _features(item: dict) -> dict[str, str]:
    """Normalizza le caratteristiche in {etichetta_minuscola: valore}."""
    out: dict[str, str] = {}
    feats = item.get("features") or {}
    seq = feats.values() if isinstance(feats, dict) else feats
    for f in seq:
        if not isinstance(f, dict):
            continue
        label = str(f.get("label") or f.get("uri") or "").lower()
        vals = f.get("values") or []
        if vals and isinstance(vals[0], dict):
            value = vals[0].get("value") or vals[0].get("key")
        else:
            value = f.get("value")
        if label and value is not None:
            out[label] = str(value)
    return out


def _pick(feats: dict[str, str], key: str) -> str | None:
    for label, value in feats.items():
        if any(tok in label for tok in LABELS[key]):
            return value
    return None


def _photos(item: dict) -> list[str]:
    urls = []
    for img in item.get("images") or []:
        if isinstance(img, dict):
            base = img.get("cdnBaseUrl") or img.get("url") or img.get("uri")
            if base:
                urls.append(base if "?" in base else base + "?rule=gallery-desktop-2x-auto")
        elif isinstance(img, str):
            urls.append(img)
    return urls


def parse_item(item: dict) -> Listing | None:
    """Da un oggetto annuncio di Subito a Listing."""
    urn = item.get("urn") or item.get("id")
    url = (item.get("urls") or {}).get("default") or item.get("url")
    if not urn or not url:
        return None
    feats = _features(item)
    geo = item.get("geo") or {}
    advertiser = item.get("advertiser") or {}
    adv_type = advertiser.get("type") if isinstance(advertiser, dict) else None
    seller = {0: "privato", 1: "commerciante", "0": "privato", "1": "commerciante"}.get(adv_type, "sconosciuto")
    if advertiser.get("company") is True:
        seller = "commerciante"

    power_raw = _pick(feats, "power")
    power_kw = None
    if power_raw:
        m = re.search(r"(\d+)\s*kw", power_raw, re.I)
        power_kw = int(m.group(1)) if m else None
        if power_kw is None:
            m = re.search(r"(\d+)\s*(cv|hp)", power_raw, re.I)
            power_kw = round(int(m.group(1)) * 0.7355) if m else None

    year_raw = _pick(feats, "year")
    year = None
    if year_raw:
        m = re.search(r"(19|20)\d{2}", year_raw)
        year = int(m.group(0)) if m else None

    damage_raw = (_pick(feats, "damage") or "").lower()

    listing = Listing(
        source="subito",
        source_id=str(urn),
        url=url,
        title=item.get("subject"),
        description=item.get("body"),
        make=_pick(feats, "make"),
        model=_pick(feats, "model"),
        version_raw=_pick(feats, "version"),
        year=year,
        mileage_km=parse_int(_pick(feats, "mileage")),
        fuel=_pick(feats, "fuel"),
        gearbox=_pick(feats, "gearbox"),
        power_kw=power_kw,
        price_raw=_pick(feats, "price"),
        price_eur=parse_int(_pick(feats, "price")),
        seller_type=seller,
        city=(geo.get("town") or {}).get("value") if isinstance(geo.get("town"), dict) else None,
        province=(geo.get("city") or {}).get("shortName") if isinstance(geo.get("city"), dict) else None,
        region=(geo.get("region") or {}).get("value") if isinstance(geo.get("region"), dict) else None,
        photos=_photos(item),
        damage_declared=True if ("danneg" in damage_raw or "incident" in damage_raw) else None,
        raw=item,
    )
    return listing


def items_from_page(data: dict) -> list[dict]:
    lst = find_key(data, "list", "items")
    out: list[dict] = []
    if isinstance(lst, list):
        for entry in lst:
            if isinstance(entry, dict):
                out.append(entry.get("item") if isinstance(entry.get("item"), dict) else entry)
    return out


class SubitoCollector(Collector):
    source = "subito"

    def __init__(self, client=None, proxy: str | None = None):
        import httpx
        self.client = client or httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True,
                                             proxy=proxy)

    def _get(self, url: str):
        r = self.client.get(url)
        self.pause()
        return r

    def search(self, query: dict) -> Iterator[Listing]:
        """query: {region, provinces[], max_price, max_pages, private_only}"""
        for province in query["provinces"]:
            for page in range(1, query.get("max_pages", 20) + 1):
                url = search_url(query["region"], province, query["max_price"], page,
                                 query.get("private_only", False))
                r = self._get(url)
                if r.status_code != 200:
                    log.warning("subito %s -> HTTP %s", url, r.status_code)
                    break
                data = next_data(r.text)
                if not data:
                    log.warning("subito: dati pagina non trovati (%s)", url)
                    break
                items = items_from_page(data)
                if not items:
                    break
                for it in items:
                    listing = parse_item(it)
                    if listing:
                        if not listing.province:
                            listing.province = province
                        yield listing

    def fetch(self, url: str) -> Listing | None:
        r = self._get(url)
        if r.status_code in (404, 410):
            return None
        if r.status_code != 200:
            raise RuntimeError(f"subito HTTP {r.status_code} per {url}")
        data = next_data(r.text)
        if not data:
            return None
        item: Any = find_key(data, "ad", "item")
        return parse_item(item) if isinstance(item, dict) else None
