"""AutoScout24 Italia: annunci di PRIVATI in Lombardia (attorno a Milano), letti dalle pagine di ricerca.

Le pagine di AutoScout24 contengono i dati degli annunci in un JSON (__NEXT_DATA__): si legge quello.
Ogni ricerca mostra al massimo 20 pagine da 20 annunci, quindi si divide per fasce di prezzo strette.
Ritmo basso: è un sito pubblico e non va sovraccaricato.

Con AUTOSCOUT_DEBUG=1 nei log compare la struttura del primo annuncio (per adattare la lettura se il sito cambia).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterator

from ..core.models import Listing
from .base import Collector, next_data

log = logging.getLogger(__name__)
BASE = "https://www.autoscout24.it"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

# CAP -> provincia (Lombardia), per gli annunci che riportano solo il CAP
CAP_PROVINCE = [("208", "MB"), ("209", "MB"), ("20", "MI"), ("21", "VA"), ("238", "LC"), ("239", "LC"), ("22", "CO"), ("230", "SO"), ("231", "SO"), ("232", "SO"), ("24", "BG"), ("25", "BS"),
                ("268", "LO"), ("269", "LO"), ("26", "CR"), ("27", "PV"), ("46", "MN")]


def province_from_cap(cap: str | None) -> str | None:
    cap = re.sub(r"\D", "", str(cap or ""))
    if len(cap) != 5:
        return None
    for pre, pv in CAP_PROVINCE:
        if cap.startswith(pre):
            return pv
    return None


def _num(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    d = re.sub(r"[^\d]", "", str(v).split(",")[0])
    return int(d) if d else None


def _year(*vals) -> int | None:
    for v in vals:
        m = re.search(r"(19[89]\d|20[0-3]\d)", str(v or ""))
        if m:
            return int(m.group(1))
    return None


FUEL = {"b": "benzina", "d": "diesel", "l": "gpl", "c": "metano", "e": "elettrica", "2": "ibrida", "3": "ibrida"}


def parse_item(it: dict) -> Listing | None:
    if not isinstance(it, dict):
        return None
    lid, url = it.get("id"), it.get("url")
    if not lid or not url:
        return None
    veh = it.get("vehicle") or {}
    trk = it.get("tracking") or {}
    loc = it.get("location") or {}
    seller = it.get("seller") or {}
    price = it.get("price") or {}
    details = {str(d.get("iconName") or d.get("ariaLabel") or ""): d.get("data")
               for d in (it.get("vehicleDetails") or []) if isinstance(d, dict)}
    km = _num(trk.get("mileage")) or _num(veh.get("mileageInKm")) or _num(details.get("mileage_road"))
    year = _year(trk.get("firstRegistration"), details.get("calendar"), veh.get("firstRegistration"))
    p = _num(trk.get("price")) or _num(price.get("priceFormatted") if isinstance(price, dict) else price)
    fuel_raw = veh.get("fuel") or details.get("gas_pump") or FUEL.get(str(trk.get("fuelType") or ""))
    gear = veh.get("transmission") or details.get("transmission")
    power = details.get("speedometer") or veh.get("power")
    kw = None
    m = re.search(r"(\d+)\s*kW", str(power or ""), re.I)
    if m:
        kw = int(m.group(1))
    stype = str(seller.get("type") or it.get("customerType") or "").lower()
    seller_type = "privato" if stype.startswith("priv") or stype == "p" else ("commerciante" if stype else "sconosciuto")
    images = [re.sub(r"/\d+x\d+\.(webp|jpg)$", "/720x540.webp", i)
              for i in (it.get("images") or []) if isinstance(i, str) and i.startswith("http")]
    title = " ".join(x for x in (veh.get("make"), veh.get("model"), veh.get("modelVersionInput")) if x) or it.get("title")
    city = loc.get("city")
    return Listing(
        source="autoscout24", source_id=str(lid), url=(BASE + url) if str(url).startswith("/") else str(url),
        title=title, description=it.get("description"),
        make=veh.get("make"), model=veh.get("model"), version_raw=veh.get("modelVersionInput"),
        year=year, mileage_km=km, fuel=fuel_raw, gearbox=gear, power_kw=kw,
        price_raw=str(p) if p else None, price_eur=p, seller_type=seller_type,
        city=city, province=province_from_cap(loc.get("zip")), photos=images[:20], raw=it,
    )


class AutoScoutCollector(Collector):
    source = "autoscout24"
    min_delay_s = 2.0
    max_delay_s = 4.5

    def __init__(self, client=None):
        self._client = client
        self.blocked = False

    @property
    def client(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=30, follow_redirects=True, http2=False,
                                        headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                                                 "Accept": "text/html,application/xhtml+xml"})
        return self._client

    def search_url(self, lo: int, hi: int, page: int) -> str:
        zip_ = os.environ.get("AUTOSCOUT_CAP", "20121")
        radius = os.environ.get("AUTOSCOUT_RAGGIO", "100")
        custtype = os.environ.get("AUTOSCOUT_VENDITORI", "P")        # P = privati
        return (f"{BASE}/lst?atype=C&cy=I&ustate=N%2CU&damaged_listing=include&custtype={custtype}"
                f"&zip={zip_}&zipr={radius}&pricefrom={lo}&priceto={hi}&sort=age&desc=1&page={page}")

    def search(self, query: dict) -> Iterator[Listing]:
        bands = query.get("price_bands") or [(500, 20000)]
        pages = int(query.get("pages", 20))
        debug = os.environ.get("AUTOSCOUT_DEBUG") == "1"
        tot = 0
        for lo, hi in bands:
            for page in range(1, pages + 1):
                url = self.search_url(lo, hi, page)
                try:
                    r = self.client.get(url)
                except Exception as e:
                    log.warning("autoscout %s: %s", url, str(e)[:120])
                    break
                self.pause()
                if r.status_code != 200:
                    log.warning("autoscout -> HTTP %s su %s", r.status_code, url)
                    if r.status_code in (403, 429):
                        self.blocked = True
                        log.error("autoscout: accesso bloccato (HTTP %s), mi fermo", r.status_code)
                        return
                    break
                data = next_data(r.text) or {}
                props = (data.get("props") or {}).get("pageProps") or {}
                items = props.get("listings") or []
                if debug and tot == 0 and items:
                    log.info("AUTOSCOUT_DEBUG chiavi pagina=%s", list(props)[:40])
                    log.info("AUTOSCOUT_DEBUG primo annuncio=%s", json.dumps(items[0], ensure_ascii=False)[:3000])
                if not items:
                    if page == 1 and not data:
                        log.warning("autoscout: pagina senza dati (struttura cambiata o blocco) %s", url)
                    break
                for it in items:
                    l = parse_item(it)
                    if l and l.price_eur and lo <= l.price_eur <= hi + 1000:
                        tot += 1
                        yield l
                if len(items) < 20:
                    break
        log.info("autoscout: %d annunci letti", tot)

    def fetch(self, url: str) -> Listing | None:
        r = self.client.get(url)
        if r.status_code != 200:
            return None
        data = next_data(r.text) or {}
        props = (data.get("props") or {}).get("pageProps") or {}
        it = props.get("listingDetails") or props.get("listing")
        return parse_item(it) if isinstance(it, dict) else None
