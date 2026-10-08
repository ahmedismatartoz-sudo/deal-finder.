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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/129.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="129", "Google Chrome";v="129", "Not=A?Brand";v="8"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
API_HEADERS = {
    "User-Agent": HEADERS["User-Agent"], "Accept": "application/json", "Accept-Language": "it-IT,it;q=0.9",
    "Accept-Encoding": "gzip, deflate", "X-Subito-Channel": "web",
    "Origin": "https://www.subito.it", "Referer": "https://www.subito.it/",
}
# Interfaccia interna usata dal sito: annunci già strutturati.
# c=2 auto, t=s vendita, r=4 Lombardia, ordinati dal più recente.
API_SEARCH = "https://hades.subito.it/v1/search/items"
REGION_CODES = {"lombardia": 4}
PROVINCE_CODES = {"milano": "MI", "monza-e-della-brianza": "MB", "bergamo": "BG", "brescia": "BS",
                  "como": "CO", "varese": "VA", "lecco": "LC", "lodi": "LO", "pavia": "PV",
                  "cremona": "CR", "mantova": "MN", "sondrio": "SO"}

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
        # Blocco "Auto" (uri /car): contiene marca, modello e versione, ognuno con la sua etichetta
        if f.get("type") == "pack" and vals and isinstance(vals[0], dict) and vals[0].get("label"):
            for v in vals:
                if isinstance(v, dict) and v.get("label") and v.get("value") is not None:
                    out[str(v["label"]).lower()] = str(v["value"])
                    if v.get("group_label"):
                        out[str(v["label"]).lower() + "_gruppo"] = str(v["group_label"])
            continue
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

    if power_kw is None:
        m = re.search(r"(\d{2,3})\s*(cv|hp)\b", _pick(feats, "version") or "", re.I)
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
        model=feats.get("modello_gruppo") or _pick(feats, "model"),
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
        province=((geo.get("city") or {}).get("short_name") or (geo.get("city") or {}).get("shortName"))
        if isinstance(geo.get("city"), dict) else None,
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
        import os

        import httpx
        self.client = client or httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True,
                                             proxy=proxy)
        # Subito blocca le richieste dai server cloud (HTTP 403): se configurato,
        # le pagine passano dal Web Unlocker di Bright Data (stesso account dello scraper Facebook).
        self.unlocker_zone = os.environ.get("BRIGHTDATA_UNLOCKER_ZONE")
        self.unlocker_key = os.environ.get("BRIGHTDATA_API_KEY")
        if self.unlocker_zone and self.unlocker_key:
            self.min_delay_s, self.max_delay_s = 1.0, 2.5   # il ritmo lo gestisce il fornitore

    def _get(self, url: str):
        if self.unlocker_zone and self.unlocker_key:
            r = self.client.post("https://api.brightdata.com/request",
                                 headers={"Authorization": f"Bearer {self.unlocker_key}"},
                                 json={"zone": self.unlocker_zone, "url": url, "format": "raw", "country": "it"},
                                 timeout=120)
            if r.headers.get("x-brd-error"):
                log.warning("unlocker: %s per %s", r.headers.get("x-brd-error")[:200], url)
        else:
            r = self.client.get(url)
        self.pause()
        return r

    def search(self, query: dict) -> Iterator[Listing]:
        """query: {region, provinces[], max_price, max_pages}. Usa l'interfaccia interna (100 annunci
        per pagina, dal più recente) e filtra per provincia e prezzo; se non risponde, ripiega sulle pagine web."""
        wanted = {PROVINCE_CODES.get(p, p.upper()[:2]) for p in query["provinces"]}
        region = REGION_CODES.get(query.get("region", "lombardia"), 4)
        lim, seen = 100, 0
        for page in range(query.get("max_pages", 20)):
            r = self.client.get(API_SEARCH, headers=API_HEADERS,
                                params={"c": 2, "r": region, "t": "s", "lim": lim, "start": page * lim,
                                        "sort": "datedesc"})
            self.pause()
            if r.status_code != 200:
                log.warning("subito api -> HTTP %s (pagina %s): ripiego sulle pagine web", r.status_code, page)
                yield from self._search_html(query)
                return
            ads = (r.json() or {}).get("ads") or []
            if not ads:
                break
            if page == 0 and __import__("os").environ.get("SUBITO_DEBUG") == "1":
                import json as _json
                a0 = ads[0]
                log.info("SUBITO_DEBUG geo=%s advertiser=%s dates=%s features=%s",
                         _json.dumps(a0.get("geo"))[:600], _json.dumps(a0.get("advertiser"))[:300],
                         _json.dumps(a0.get("dates"))[:200], _json.dumps(a0.get("features"))[:1500])
            for it in ads:
                listing = parse_item(it)
                if not listing:
                    continue
                seen += 1
                if listing.province and listing.province.upper() not in wanted:
                    continue
                if listing.price_eur is not None and listing.price_eur > query["max_price"]:
                    continue
                yield listing
            if len(ads) < lim:
                break
        log.info("subito api: %d annunci letti", seen)

    def _search_html(self, query: dict) -> Iterator[Listing]:
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
                            listing.province = PROVINCE_CODES.get(province, province)
                        yield listing

    def fetch(self, url: str) -> Listing | None:
        """None SOLO se l'annuncio non esiste più (404/410 o rimando alla ricerca).
        Se la pagina c'è ma non si riesce a leggerla, l'annuncio è considerato ancora attivo."""
        r = self._get(url)
        if r.status_code in (404, 410):
            return None
        if r.status_code != 200:
            raise RuntimeError(f"subito HTTP {r.status_code} per {url}")
        final = str(getattr(r, "url", url))
        if final.rstrip("/") != url.rstrip("/") and ".htm" not in final:
            return None          # rimandato a una pagina di ricerca: annuncio rimosso
        data = next_data(r.text)
        item: Any = find_key(data, "ad", "item") if data else None
        if isinstance(item, dict):
            parsed = parse_item(item)
            if parsed:
                return parsed
        return Listing(source="subito", source_id=url, url=url)
