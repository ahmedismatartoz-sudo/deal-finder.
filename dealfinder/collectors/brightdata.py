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
from ..core.vehicles import make_model_from_title
from .base import Collector

log = logging.getLogger(__name__)

API = "https://api.brightdata.com/datasets/v3"
DEFAULT_DATASET = "gd_lvt9iwuh6fbcwmx1a"
# Facebook mostra un numero limitato di risultati per ricerca: dividere per fasce di prezzo
# fa sì che ogni ricerca resti sotto il limite e insieme coprano quasi tutto.
DEFAULT_SEARCHES = [
    {"city": "milan", "radius": 60, "min_price": 500, "max_price": 6000},
    {"city": "milan", "radius": 60, "min_price": 6000, "max_price": 20000},
    {"city": "brescia", "radius": 30, "min_price": 500, "max_price": 20000},
    # La maggior parte delle opportunità sono auto con problemi: ricerche mirate
    {"city": "milan", "radius": 80, "query": "incidentata"},
    {"city": "milan", "radius": 80, "query": "da sistemare"},
    {"city": "milan", "radius": 80, "query": "non parte"},
    {"city": "milan", "radius": 80, "query": "guasto"},
]
RE_KM = re.compile(r"(?<!\d)(\d{1,3}(?:[ .]\d{3})+|\d{4,7})\s*(?:km|chilometri)\b", re.I)
RE_YEAR = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
RE_YEAR_TEXT = re.compile(r"\b(?:anno|del|immatricolat[ao](?: nel| a)?|immatricolazione|imm\.?)\s*:?\s*(?:\d{1,2}/)?((?:19[89]|20[0-2])\d)\b", re.I)
# carburante dal testo (Facebook non ha un campo): l'ordine conta, GPL/metano/ibrida prima di benzina
FUEL_TEXT = [
    ("gpl", re.compile(r"\b(gpl|lpg|bifuel|bi-fuel)\b", re.I)),
    ("metano", re.compile(r"\b(metano|cng|natural power|ecofuel)\b", re.I)),
    ("ibrida", re.compile(r"\b(hybrid|ibrida|full hybrid|mild hybrid|plug-in|phev|e-tense)\b", re.I)),
    ("elettrica", re.compile(r"\b(elettrica|100% elettric[ao]|full electric)\b", re.I)),
    ("diesel", re.compile(r"\b(diesel|gasolio|tdi|tdci|jtd|jtdm|multijet|mjt|m-?jet|hdi|bluehdi|e-?hdi|dci|cdi|crdi|"
                          r"d-?4d|ecoblue|bluetec|bluemotion tdi|cdti|ddis|i-?dtec|skyactiv-d|\d{2,3}\s?d|[1-9]\d{2}d)\b", re.I)),
    ("benzina", re.compile(r"\b(benzina|tsi|tfsi|tce|puretech|ecoboost|fire|twinair|t-?jet|vti|thp|mpi|t-?gdi|gdi|"
                           r"vvt-?i|i-?vtec|skyactiv-g|turbo benzina|[1-9]\d{2}i)\b", re.I)),
]
RE_AUTO = re.compile(r"\b(cambio automatico|automatica|automatico|dsg|s-?tronic|steptronic|edc|easytronic|"
                     r"dualogic|powershift|cvt|tiptronic|7g-?tronic|aut\.)\b", re.I)
RE_MANUAL = re.compile(r"\b(cambio manuale|manuale)\b", re.I)
NOT_A_CAR = re.compile(r"\b(ricambi|vendo motore|motore in vendita|smembro|monopattino|scooter|moto(?:cicletta)?|"
                       r"bici|camper|roulotte|trattore|furgone)\b", re.I)


def search_url(city: str, radius: int, min_price: int, max_price: int, days: int = 1,
               query: str | None = None) -> str:
    params = {"minPrice": min_price, "maxPrice": max_price, "daysSinceListed": days,
              "sortBy": "creation_time_descend", "radius": radius, "exact": "false"}
    if query:
        return f"https://www.facebook.com/marketplace/{city}/search/?" + urlencode({"query": query, **params})
    return f"https://www.facebook.com/marketplace/{city}/vehicles?" + urlencode(params)


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
    # Nessun km leggibile nel testo: il campo del fornitore è in km (verificato: mai in miglia
    # sui dati italiani) e si usa se plausibile
    if not values and isinstance(miles, (int, float)) and 500 <= miles <= 600_000:
        return int(miles)
    return None


def fuel_from(text: str) -> str | None:
    for fuel, rx in FUEL_TEXT:
        if rx.search(text):
            return fuel
    return None


def year_from(title: str, desc: str) -> int | None:
    m = RE_YEAR.search(title)
    if m:
        return int(m.group(1))
    years = {int(y) for y in RE_YEAR_TEXT.findall(desc[:600])}
    return years.pop() if len(years) == 1 else None


# parole che bastano a riconoscere la marca quando il venditore non la scrive
EXTRA_MODEL_MAKE = {"classe": "mercedes", "serie": "bmw", "x1": "bmw", "x2": "bmw", "x3": "bmw", "x4": "bmw",
                    "x5": "bmw", "x6": "bmw", "a1": "audi", "a3": "audi", "a4": "audi", "a5": "audi", "a6": "audi",
                    "q2": "audi", "q3": "audi", "q5": "audi", "tt": "audi", "500l": "fiat", "500x": "fiat",
                    "208": "peugeot", "2008": "peugeot", "308": "peugeot", "3008": "peugeot", "207": "peugeot",
                    "c1": "citroen", "c5": "citroen", "ds3": "citroen", "kuga": "ford", "puma": "ford", "ka": "ford",
                    "tiguan": "volkswagen", "t-roc": "volkswagen", "troc": "volkswagen", "up": "volkswagen",
                    "touran": "volkswagen", "captur": "renault", "scenic": "renault", "kadjar": "renault",
                    "juke": "nissan", "x-trail": "nissan", "note": "nissan", "rav4": "toyota", "auris": "toyota",
                    "c-hr": "toyota", "chr": "toyota", "tucson": "hyundai", "ix35": "hyundai", "i30": "hyundai",
                    "rio": "kia", "ceed": "kia", "stonic": "kia", "niro": "kia", "swift": "suzuki", "vitara": "suzuki",
                    "ignis": "suzuki", "jimny": "suzuki", "renegade": "jeep", "compass": "jeep", "mokka": "opel",
                    "crossland": "opel", "grandland": "opel", "insignia": "opel", "adam": "opel", "karl": "opel",
                    "delta": "lancia", "stelvio": "alfa-romeo", "giulia": "alfa-romeo", "tonale": "alfa-romeo",
                    "arona": "seat", "ateca": "seat", "kamiq": "skoda", "karoq": "skoda", "kodiaq": "skoda",
                    "citigo": "skoda", "logan": "dacia", "jogger": "dacia", "cooper": "mini", "countryman": "mini",
                    "fortwo": "smart", "forfour": "smart", "qubo": "fiat", "bravo": "fiat", "freemont": "fiat",
                    "sedici": "fiat", "idea": "fiat", "croma": "fiat", "multipla": "fiat", "fiorino": "fiat"}


def make_model_from(title: str, desc: str, brand: str | None) -> tuple[str | None, str | None]:
    """Marca e modello: dal titolo, poi con la marca del fornitore, poi dal modello famoso
    (es. "Golf 7 tdi" -> volkswagen golf), infine dall'inizio della descrizione."""
    from ..core.consistency import MODEL_MAKE
    make, model = make_model_from_title(title)
    if model:
        return make, model
    if brand and not make:
        b_make, b_model = make_model_from_title(f"{brand} {title}")
        if b_model:
            return b_make, b_model
        make = make or b_make
    known = {**MODEL_MAKE, **EXTRA_MODEL_MAKE}
    words = re.findall(r"[a-z0-9]+", title.lower())
    for w in words:
        if w in known:
            mk, md = make_model_from_title(f"{known[w]} {w} " + " ".join(words[words.index(w) + 1:]))
            if md:
                return make or mk, md
    d_make, d_model = make_model_from_title(desc[:160])
    if d_model and (make is None or d_make == make):
        return d_make, d_model
    return make, model


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
    loc = row.get("location")
    city = loc.get("city") if isinstance(loc, dict) else (str(loc).split(",")[0].strip() if loc else None)
    trans = row.get("transmission")
    images = [i for i in (row.get("images") or []) if isinstance(i, str) and i.startswith("https://")]
    t_make, t_model = make_model_from(title, desc, row.get("brand"))
    text = f"{title} {desc[:800]}"
    gearbox = {"MANUAL": "manuale", "AUTOMATIC": "automatico"}.get(str(trans).upper()) if trans else None
    if not gearbox:
        gearbox = "automatico" if RE_AUTO.search(text) else ("manuale" if RE_MANUAL.search(text) else None)
    return Listing(
        source="facebook", source_id=str(pid), url=str(url).split("?")[0],
        title=title or None, description=desc or None,
        make=t_make or row.get("brand"),
        model=t_model,
        year=year_from(title, desc),
        mileage_km=mileage_from(row),
        fuel=fuel_from(text),
        gearbox=gearbox,
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
        # Formato verificato con questo dataset: limiti nella query e nel corpo, input dentro "input"
        r = self.client.post(f"{API}/trigger",
                             params={"dataset_id": self.dataset, "include_errors": "true", "notify": "false",
                                     "type": "discover_new", "discover_by": "url",
                                     "limit_multiple_results": self.limit * len(urls)},
                             json={"input": [{"url": u, "country": "IT"} for u in urls],
                                   "limit_per_input": self.limit})
        if r.status_code >= 400:
            log.error("Bright Data trigger HTTP %s: %s", r.status_code, r.text[:400])
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
                           s.get("max_price", max_price), s.get("days", 1), s.get("query")) for s in self.searches]
        problem_urls = {u for u, s in zip(urls, self.searches) if s.get("query")}
        sid = self.trigger(urls)
        log.info("Bright Data: snapshot %s avviato per %d ricerche", sid, len(urls))
        self.wait(sid)
        rows = self.download(sid)
        same = miles = other = 0
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("car_miles"), (int, float)):
                continue
            vals = {int(re.sub(r"[ .]", "", x)) for x in RE_KM.findall(row.get("description") or "")}
            if not vals:
                continue
            cm = int(row["car_miles"])
            if cm in vals:
                same += 1
            elif any(abs(v / 1.609 - cm) < 0.03 * v for v in vals):
                miles += 1
            else:
                other += 1
        log.info("Bright Data km: campo fornitore uguale ai km scritti %d, in miglia %d, diverso %d", same, miles, other)
        kept = 0
        for row in rows:
            listing = parse_row(row, max_price)
            if listing:
                src = (row.get("input") or {}).get("url") if isinstance(row.get("input"), dict) else None
                listing.problem_search = src in problem_urls if src else False
                kept += 1
                yield listing
        log.info("Bright Data: %d righe ricevute, %d auto valide", len(rows), kept)
        per = {}
        for row in rows:
            src = (row.get("input") or {}).get("url") if isinstance(row, dict) and isinstance(row.get("input"), dict) else None
            k = (src or "?").split("marketplace/")[-1][:70]
            per[k] = per.get(k, 0) + 1
        log.info("FB_PER_RICERCA %s", json.dumps(per))

    def check_urls(self, urls: list[str]) -> dict[str, str]:
        """Verifica se gli annunci esistono ancora: {url: attivo|scomparso|venduto|errore}.
        Costa un record per annuncio: si usa solo sui candidati."""
        if not urls:
            return {}
        r = self.client.post(f"{API}/trigger", params={"dataset_id": self.dataset, "include_errors": "true",
                                                         "notify": "false"},
                             json={"input": [{"url": u} for u in urls]})
        if r.status_code >= 400:
            log.error("Bright Data verifica HTTP %s: %s", r.status_code, r.text[:400])
        r.raise_for_status()
        sid = r.json().get("snapshot_id")
        self.wait(sid, max_minutes=20)
        rows = self.download(sid)
        out = {u: "errore" for u in urls}
        by_id = {u.rstrip("/").split("/")[-1]: u for u in urls}
        for row in rows:
            if not isinstance(row, dict):
                continue
            src = (row.get("input") or {}).get("url") if isinstance(row.get("input"), dict) else None
            key = src or by_id.get(str(row.get("product_id")))
            if key not in out:
                continue
            err = str(row.get("error") or row.get("error_code") or "").lower()
            if err:
                out[key] = "scomparso" if any(x in err for x in ("not found", "dead", "removed", "unavailable", "404")) else "errore"
            elif row.get("is_sold") is True:
                out[key] = "venduto"
            else:
                out[key] = "attivo"
        return out

    def fetch(self, url: str) -> Listing | None:
        raise NotImplementedError("Per Facebook usare check_urls su più annunci insieme")
