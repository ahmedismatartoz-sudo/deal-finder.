"""26/10 notte (cliente): commercianti di auto da Google Maps per Milano e province vicine, via Apify
(attore "compass~crawler-google-places", account del cliente). Gira nel cron (voce COLLECT_PRIMA "leadmaps").

- Chiave: APIFY_TOKEN su Render (mai nel codice o in chat). Senza chiave non fa nulla.
- Budget: si ferma dopo MAPS_MAX_POSTI risultati letti nel giro (predefinito 1200, circa il credito gratuito mensile).
- Riprende da dove era arrivato: le ricerche gia' fatte stanno in lead_maps_fatte.
- Salva in lead_maps (place_id come chiave). Nei log solo conteggi, nessun nome o telefono.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import httpx

log = logging.getLogger("lead")

ATTORE = "compass~crawler-google-places"
PAROLE = ["autosalone auto usate", "compravendita auto", "concessionaria auto"]
ZONE_MILANO = ["Niguarda", "Bovisa", "Affori", "Quarto Oggiaro", "Certosa", "Lampugnano", "San Siro", "Baggio",
               "Lorenteggio", "Barona", "Gratosoglio", "Vigentina", "Corvetto", "Rogoredo", "Forlanini", "Lambrate",
               "Città Studi", "Loreto", "Viale Monza", "Gorla", "Bicocca", "Greco", "Isola", "Porta Romana",
               "Navigli", "Fulvio Testi", "Via Novara", "Precotto", "Ortica", "Bruzzano"]
ORDINE_PROV = ["MI", "MB", "VA", "CO", "LC", "BG", "LO", "PV", "CR"]
CITTA_ZONA = {
    "MB": ["Monza", "Desio", "Seregno", "Lissone", "Brugherio", "Cesano Maderno", "Vimercate"],
    "BG": ["Bergamo", "Treviglio", "Seriate", "Dalmine", "Romano di Lombardia"],
    "BS": ["Brescia", "Desenzano del Garda", "Chiari", "Lumezzane", "Montichiari", "Rovato"],
    "VA": ["Varese", "Busto Arsizio", "Gallarate", "Saronno"],
    "CO": ["Como", "Cantù", "Erba", "Mariano Comense"],
    "LC": ["Lecco", "Merate", "Calolziocorte"],
    "LO": ["Lodi", "Codogno"],
    "CR": ["Cremona", "Crema", "Casalmaggiore"],
    "MN": ["Mantova", "Castiglione delle Stiviere", "Suzzara", "Viadana"],
    "PV": ["Pavia", "Vigevano", "Voghera"],
    "SO": ["Sondrio", "Morbegno"],
    "NO": ["Novara", "Borgomanero", "Trecate"],
    "PC": ["Piacenza", "Fiorenzuola d'Arda", "Castel San Giovanni"],
}

SQL = """
CREATE TABLE IF NOT EXISTS lead_maps (
    place_id   TEXT PRIMARY KEY,
    nome       TEXT, indirizzo TEXT, comune TEXT, cap TEXT, prov TEXT,
    tel        TEXT, cell BOOLEAN, sito TEXT, categoria TEXT,
    voto       REAL, recensioni INTEGER, ricerca TEXT,
    aggiornato TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS lead_maps_fatte (
    ricerca TEXT PRIMARY KEY, trovati INTEGER, fatta TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
CATEGORIE_OK = re.compile(r"auto|concessionari|car dealer|veicoli|usat|automobil", re.I)
CATEGORIE_NO = re.compile(r"noleggio|rental|camper|caravan|ricambi|demolizion|gommist|moto\b|scooter|camion|truck", re.I)


def _ricerche() -> list[tuple[str, int]]:
    base = Path(__file__).resolve().parents[2] / "data"
    comuni: list[tuple[str, str]] = []
    try:
        comuni += [(c, p) for c, p in json.loads((base / "comuni_sesto.json").read_text()) if c != "Milano"]
    except Exception:
        pass
    try:
        visti = {c for c, _ in comuni}
        comuni += [(v["comune"], v["pv"]) for v in json.loads((base / "comuni_60km.json").read_text()).values()
                   if v["pv"] in ("VA", "PV", "LO") and v["comune"] not in visti and v.get("km", 99) <= 40]
    except Exception:
        pass
    # 1/11 (cliente: "solo Milano e dintorni, appuntamenti più vicini, non Como, Bergamo ecc."): MAPS_SOLO_PROV=MI
    solo = {p.strip().upper() for p in (os.environ.get("MAPS_SOLO_PROV") or "").split(",") if p.strip()}
    if solo:
        comuni = [(c, p) for c, p in comuni if p in solo]
    comuni.sort(key=lambda x: ORDINE_PROV.index(x[1]) if x[1] in ORDINE_PROV else 99)
    # 1/11: parole diverse per trovare i commercianti mancanti (MAPS_PAROLE="a|b|c"), senza ripetere le ricerche già fatte
    parole = [p.strip() for p in (os.environ.get("MAPS_PAROLE") or "").split("|") if p.strip()] or PAROLE[:2]
    out = [(f"{p} Milano {z}", int(os.environ.get("MAPS_PER_RICERCA", "60"))) for z in ZONE_MILANO for p in parole]
    # 1/11 sera (cliente: "servono 1.000 email nuove"): i capoluoghi e le città grandi della zona del sito
    if os.environ.get("MAPS_CAPOLUOGHI") == "1":
        for pv, citta in CITTA_ZONA.items():
            if solo and pv not in solo:
                continue
            for c in citta:
                out += [(f"{p} {c} {pv}", int(os.environ.get("MAPS_PER_RICERCA", "60"))) for p in parole]
    for c, pv in comuni:
        out += [(f"{p} {c} {pv}", 30) for p in parole]
    return out


def _cell(t: str | None) -> bool:
    d = re.sub(r"\D", "", t or "")
    d = d[2:] if d.startswith("39") and len(d) > 10 else d
    return d.startswith("3")


def _prov(indirizzo: str) -> str | None:
    m = re.search(r"\b\d{5}\s+[^,]+?\s+([A-Z]{2})\b", indirizzo or "")
    return m.group(1) if m else None


class Fermo(Exception):
    pass


_CHIAVI_VISTE = {"ok": False}


def _outscraper(chiave: str, ricerca: str, quanti: int) -> list[dict]:
    """1/11 (cliente: "usiamo Outscraper"): stessa ricerca su Google Maps via Outscraper (OUTSCRAPER_KEY su Render).
    Restituisce i posti con gli stessi nomi di campo di Apify."""
    r = httpx.get("https://api.app.outscraper.com/maps/search-v3",
                  headers={"X-API-KEY": chiave},
                  params={"query": ricerca, "limit": quanti, "language": "it", "region": "IT", "async": "false"},
                  timeout=330)
    if r.status_code in (401, 402, 403):
        raise Fermo(f"Outscraper rifiuta ({r.status_code}): {r.text[:200]}")
    r.raise_for_status()
    dati = r.json().get("data") or []
    posti = dati[0] if dati and isinstance(dati[0], list) else dati
    out = []
    if posti and isinstance(posti[0], dict) and not _CHIAVI_VISTE["ok"]:
        _CHIAVI_VISTE["ok"] = True
        log.warning("LEADMAPS Outscraper campi: %s", sorted(posti[0].keys()))
    for p in posti or []:
        if not isinstance(p, dict):
            continue
        out.append({"placeId": p.get("place_id") or p.get("google_id"), "title": p.get("name"),
                    "address": p.get("full_address") or p.get("address"), "city": p.get("city"),
                    "postalCode": p.get("postal_code"), "state": p.get("state"), "phone": p.get("phone"),
                    "website": p.get("site") or p.get("website") or p.get("domain"), "categoryName": p.get("type") or p.get("category"),
                    "categories": [x.strip() for x in str(p.get("subtypes") or "").split(",") if x.strip()],
                    "totalScore": p.get("rating"), "reviewsCount": p.get("reviews"),
                    "permanentlyClosed": str(p.get("business_status") or "").upper() == "CLOSED_PERMANENTLY"})
    return out


def completa_siti(limite: int = 300) -> dict:
    """1/11: posti di Milano e provincia salvati senza sito: si richiede a Outscraper la scheda (per place_id, 1 risultato)
    e si salvano sito e telefono. Costa 1 risultato per posto."""
    osk = os.environ.get("OUTSCRAPER_KEY")
    if not osk:
        return {"fatto": False}
    from ..db import connect
    with connect() as conn:
        rows = conn.execute("SELECT place_id FROM lead_maps WHERE sito IS NULL AND prov = ANY(%s) "
                            "AND aggiornato > now() - interval '2 days' LIMIT %s",
                            ([p.strip().upper() for p in (os.environ.get("MAPS_SOLO_PROV") or "MI").split(",")], limite)).fetchall()
    st = {"posti": len(rows), "con_sito": 0, "errori": 0}
    for r in rows:
        try:
            posti = _outscraper(osk, r["place_id"], 1)
        except Fermo as e:
            log.warning("LEADMAPS siti: %s", str(e)[:200])
            break
        except Exception:
            st["errori"] += 1
            continue
        p = posti[0] if posti else None
        if p and p.get("website"):
            with connect() as conn:
                conn.execute("UPDATE lead_maps SET sito=%s, tel=coalesce(tel,%s) WHERE place_id=%s",
                             (p["website"], p.get("phone"), r["place_id"]))
            st["con_sito"] += 1
    log.warning("LEADMAPS siti completati: %s", st)
    return st


def giro() -> dict:
    osk = os.environ.get("OUTSCRAPER_KEY")
    tok = (os.environ.get("APIFY_TOKENS") or os.environ.get("APIFY_TOKEN"))
    if not (osk or tok):
        log.warning("LEADMAPS senza OUTSCRAPER_KEY né APIFY_TOKEN: niente da fare")
        return {"fatto": False, "motivo": "chiave mancante"}
    log.warning("LEADMAPS uso %s", "Outscraper" if osk else "Apify")
    from ..db import connect
    massimo = int(os.environ.get("MAPS_MAX_POSTI", "1000"))
    letti = nuovi = ricerche = 0
    with connect() as conn:
        conn.execute(SQL)
        conn.commit()
        fatte = {r["ricerca"] for r in conn.execute("SELECT ricerca FROM lead_maps_fatte").fetchall()}
    for ricerca, quanti in _ricerche():
        if ricerca in fatte:
            continue
        if letti >= massimo:
            break
        quanti = min(quanti, max(1, massimo - letti))
        corpo = {"searchStringsArray": [ricerca], "locationQuery": "Lombardia, Italia", "language": "it",
                 "maxCrawledPlacesPerSearch": quanti, "skipClosedPlaces": True, "scrapePlaceDetailPage": False}
        try:
            if osk:
                posti = _outscraper(osk, ricerca, quanti)
            else:
                # 1/11: token nell'intestazione, non nell'indirizzo (così non finisce nei log in caso di errore)
                r = httpx.post(f"https://api.apify.com/v2/acts/{ATTORE}/run-sync-get-dataset-items",
                               headers={"Authorization": f"Bearer {tok}"}, json=corpo, timeout=330)
                if r.status_code == 402:
                    log.warning("LEADMAPS credito Apify finito: mi fermo")
                    break
                if r.status_code in (401, 403):
                    try:
                        err = (r.json().get("error") or {})
                        motivo = f"{err.get('type')}: {str(err.get('message'))[:200]}"
                    except Exception:
                        motivo = r.text[:200]
                    log.warning("LEADMAPS Apify rifiuta (%d) %s: mi fermo", r.status_code, motivo)
                    break
                r.raise_for_status()
                dati = r.json()
                posti = dati if isinstance(dati, list) else []
        except Fermo as e:
            log.warning("LEADMAPS %s: mi fermo", str(e)[:250])
            break
        except Exception as e:
            log.warning("LEADMAPS ricerca non riuscita (%s): %s", ricerca[:40], re.sub(r"token=[^&\s']+", "token=***", str(e))[:160])
            time.sleep(5)
            continue
        ricerche += 1
        letti += len(posti)
        with connect() as conn:
            for p in posti:
                cat = " ".join([p.get("categoryName") or ""] + list(p.get("categories") or []))
                if not p.get("placeId") or (cat and (CATEGORIE_NO.search(cat) or not CATEGORIE_OK.search(cat))):
                    continue
                if p.get("permanentlyClosed"):
                    continue
                tel = p.get("phoneUnformatted") or p.get("phone")
                ind = p.get("address") or ""
                res = conn.execute(
                    "INSERT INTO lead_maps (place_id,nome,indirizzo,comune,cap,prov,tel,cell,sito,categoria,voto,recensioni,ricerca) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (place_id) DO NOTHING",
                    (p["placeId"], p.get("title"), ind, p.get("city"), p.get("postalCode"), _prov(ind) or p.get("state"),
                     tel, _cell(tel), p.get("website"), p.get("categoryName"), p.get("totalScore"),
                     p.get("reviewsCount"), ricerca))
                nuovi += res.rowcount or 0
            conn.execute("INSERT INTO lead_maps_fatte (ricerca, trovati) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                         (ricerca, len(posti)))
            conn.commit()
        if ricerche % 10 == 0:
            log.warning("LEADMAPS avanzamento: %d ricerche, %d letti, %d nuovi", ricerche, letti, nuovi)
    with connect() as conn:
        tot = conn.execute("SELECT count(*) n, count(*) FILTER (WHERE cell) c FROM lead_maps").fetchone()
    esito = {"ricerche": ricerche, "letti": letti, "nuovi": nuovi, "totale": tot["n"], "cellulari": tot["c"]}
    log.warning("LEADMAPS_FINE %s", esito)
    return esito
