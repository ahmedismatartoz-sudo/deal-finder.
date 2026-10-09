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
# Facebook mostra un numero limitato di risultati per ricerca: si divide per zona e per fascia
# di prezzo, così ogni ricerca resta sotto il limite e insieme coprono Milano e dintorni.
FB_CITIES = [("milan", 25), ("monza", 15), ("bergamo", 25), ("brescia", 25), ("como", 20),
             ("varese", 20), ("pavia", 20), ("lecco", 15), ("lodi", 15)]
FB_BANDS = [(500, 2000), (2000, 4000), (4000, 7000), (7000, 12000), (12000, 20000)]
# La maggior parte delle opportunità sono auto con problemi: ricerche mirate su tutta l'area
FB_PROBLEM_QUERIES = ["incidentata", "da sistemare", "non parte", "guasto"]


# raccolta di partenza: fasce più strette, così ogni ricerca resta sotto il limite di Facebook
FB_BANDS_FINE = [(500, 1000), (1000, 1500), (1500, 2000), (2000, 3000), (3000, 4000), (4000, 5500),
                 (5500, 7000), (7000, 9000), (9000, 12000), (12000, 15000), (15000, 20000)]


# Facebook ignora le città che non conosce e ripete i risultati di Milano: per avere annunci
# diversi si cerca su Milano (raggio ampio) per marca e modello, in fasce di prezzo.
FB_BRAND_QUERIES = ["fiat panda", "fiat 500", "fiat punto", "fiat tipo", "fiat", "volkswagen golf", "volkswagen polo",
                    "volkswagen", "audi a3", "audi a4", "audi", "bmw serie 1", "bmw serie 3", "bmw", "mercedes classe a",
                    "mercedes", "ford fiesta", "ford focus", "ford", "opel corsa", "opel", "renault clio", "renault",
                    "peugeot 208", "peugeot", "citroen c3", "citroen", "toyota yaris", "toyota", "lancia ypsilon",
                    "nissan qashqai", "nissan", "hyundai", "kia", "dacia", "skoda", "seat", "mini", "smart",
                    "jeep", "alfa romeo", "suzuki", "mazda", "volvo", "land rover", "mitsubishi", "honda", "ds"]
FB_QUERY_BANDS = [(500, 3000), (3000, 7000), (7000, 12000), (12000, 20000)]


def backfill_searches(days: int = 30) -> list[dict]:
    out = [{"city": "milan", "radius": 60, "min_price": lo, "max_price": hi, "days": days}
           for lo, hi in FB_BANDS_FINE]
    out += [{"city": "milan", "radius": 60, "query": q, "min_price": lo, "max_price": hi, "days": days}
            for q in FB_BRAND_QUERIES for lo, hi in FB_QUERY_BANDS]
    out += [{"city": "milan", "radius": 80, "query": q, "days": days} for q in FB_PROBLEM_QUERIES]
    return out


def default_searches(days: int = 1) -> list[dict]:
    """Raccolta normale (annunci dell'ultimo giorno): fasce di prezzo, marche e ricerche "con problemi"."""
    out = [{"city": "milan", "radius": 60, "min_price": lo, "max_price": hi, "days": days} for lo, hi in FB_BANDS_FINE]
    out += [{"city": "milan", "radius": 60, "query": q, "min_price": 500, "max_price": 20000, "days": days}
            for q in FB_BRAND_QUERIES]
    out += [{"city": "milan", "radius": 80, "query": q, "days": days} for q in FB_PROBLEM_QUERIES]
    return out


DEFAULT_SEARCHES = default_searches(1)
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
SPARE_PARTS_ONLY = re.compile(r"\b(vendo (solo )?ricambi|vendo pezzi|smembro)\b", re.I)
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
    # filtro largo: si scarta solo ciò che dal titolo NON è un'auto (moto, bici, ricambi venduti a parte);
    # tutto il resto si salva con foto, prezzo, link e testo e lo giudica la stima dei prezzi
    if NOT_A_CAR.search(title) or SPARE_PARTS_ONLY.search(desc[:200]):
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

    def __init__(self, key: str | None = None, client=None, backfill: bool = False):
        self.key = key or os.environ.get("BRIGHTDATA_API_KEY")
        self.dataset = os.environ.get("BRIGHTDATA_DATASET", DEFAULT_DATASET)
        # annunci massimi per singola ricerca: con molte ricerche piccole si spende poco e si
        # prendono quasi solo annunci nuovi (BRIGHTDATA_LIMIT vale solo con BRIGHTDATA_SEARCHES)
        self.limit = int(os.environ.get("FB_LIMIT_PER_RICERCA", "40"))
        raw = os.environ.get("BRIGHTDATA_SEARCHES")
        self.searches = json.loads(raw) if raw else DEFAULT_SEARCHES
        if raw:
            self.limit = int(os.environ.get("BRIGHTDATA_LIMIT", "300"))
        # Raccolta di partenza (una volta sola): gli annunci degli ultimi 30 giorni
        if backfill:
            self.searches = backfill_searches(30)
            self.limit = int(os.environ.get("FB_BACKFILL_LIMIT", "300"))
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
        problem_urls = {u for u, s in zip(urls, self.searches) if s.get("query") in FB_PROBLEM_QUERIES}
        problem_set = set(problem_urls)
        # Lotti piccoli, tutti avviati insieme su Bright Data (lavorano in parallelo).
        # Ogni lotto si scarica appena è pronto, nell'ordine in cui finiscono: uno lento non blocca gli altri.
        size = int(os.environ.get("FB_LOTTO", "5"))
        chunks = [urls[i:i + size] for i in range(0, len(urls), size)]
        pending = []
        for ch in chunks:
            try:
                pending.append(self.trigger(ch))
            except Exception as e:
                log.error("Bright Data: lotto non avviato: %s", str(e)[:200])
        log.info("Bright Data: %d lotti avviati per %d ricerche (max %d annunci per ricerca): %s",
                 len(pending), len(urls), self.limit, ",".join(pending))
        yield from self.collect_snapshots(pending, max_price, problem_set)

    def snapshot_status(self, sid: str) -> str:
        r = self.client.get(f"{API}/progress/{sid}")
        r.raise_for_status()
        return str(r.json().get("status") or "")

    def ready_snapshots(self, hours: int = 24) -> list[str]:
        """Lotti già pronti su Bright Data (anche avviati da un altro giro), più recenti prima."""
        r = self.client.get(f"{API}/snapshots", params={"dataset_id": self.dataset, "status": "ready"})
        if r.status_code >= 400:
            log.warning("Bright Data elenco lotti HTTP %s: %s", r.status_code, r.text[:200])
            return []
        data = r.json()
        items = data if isinstance(data, list) else (data.get("snapshots") or data.get("data") or [])
        out = []
        from datetime import datetime, timedelta, timezone
        limit = datetime.now(timezone.utc) - timedelta(hours=hours)
        for it in items:
            sid = it.get("id") or it.get("snapshot_id")
            created = str(it.get("created") or it.get("created_at") or "")
            try:
                when = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                when = None
            if sid and (when is None or when >= limit):
                out.append(sid)
        return out

    def collect_snapshots(self, pending: list[str], max_price: int, problem_set: set | None = None,
                          max_minutes: int | None = None) -> Iterator[Listing]:
        problem_set = problem_set or set()
        deadline = time.time() + 60 * (max_minutes or int(os.environ.get("FB_ATTESA_MIN", "180")))
        pending = list(pending)
        total = kept = 0
        per: dict = {}
        while pending and time.time() < deadline:
            done_any = False
            for sid in list(pending):
                try:
                    st = self.snapshot_status(sid)
                except Exception as e:
                    log.warning("Bright Data: stato lotto %s non letto: %s", sid, str(e)[:120])
                    continue
                if st == "failed":
                    log.error("Bright Data: lotto %s fallito", sid)
                    pending.remove(sid)
                    continue
                if st != "ready":
                    continue
                pending.remove(sid)
                done_any = True
                try:
                    rows = self.download(sid)
                except Exception as e:
                    log.error("Bright Data: lotto %s non scaricato: %s", sid, str(e)[:200])
                    continue
                total += len(rows)
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    src = (row.get("input") or {}).get("url") if isinstance(row.get("input"), dict) else None
                    k = (src or "?").split("marketplace/")[-1][:60]
                    per[k] = per.get(k, 0) + 1
                    listing = parse_row(row, max_price)
                    if listing:
                        listing.problem_search = src in problem_set if src else False
                        kept += 1
                        yield listing
                log.info("Bright Data: lotto %s: %d righe (mancano %d lotti)", sid, len(rows), len(pending))
                del rows
            if pending and not done_any:
                time.sleep(20)
        if pending:
            log.warning("Bright Data: %d lotti non pronti in tempo (li raccoglie il giro dopo): %s",
                        len(pending), ",".join(pending))
        log.info("Bright Data: %d righe ricevute, %d auto valide", total, kept)

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
