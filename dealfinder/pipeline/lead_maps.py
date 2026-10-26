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
    comuni.sort(key=lambda x: ORDINE_PROV.index(x[1]) if x[1] in ORDINE_PROV else 99)
    out = [(f"{p} Milano {z}", 60) for z in ZONE_MILANO for p in PAROLE[:2]]
    for c, pv in comuni:
        out += [(f"{p} {c} {pv}", 30) for p in PAROLE[:2]]
    return out


def _cell(t: str | None) -> bool:
    d = re.sub(r"\D", "", t or "")
    d = d[2:] if d.startswith("39") and len(d) > 10 else d
    return d.startswith("3")


def _prov(indirizzo: str) -> str | None:
    m = re.search(r"\b\d{5}\s+[^,]+?\s+([A-Z]{2})\b", indirizzo or "")
    return m.group(1) if m else None


def giro() -> dict:
    tok = os.environ.get("APIFY_TOKEN")
    if not tok:
        log.warning("LEADMAPS senza APIFY_TOKEN: niente da fare")
        return {"fatto": False, "motivo": "APIFY_TOKEN mancante"}
    from ..db import connect
    massimo = int(os.environ.get("MAPS_MAX_POSTI", "1200"))
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
            r = httpx.post(f"https://api.apify.com/v2/acts/{ATTORE}/run-sync-get-dataset-items",
                           params={"token": tok}, json=corpo, timeout=330)
            if r.status_code == 402:
                log.warning("LEADMAPS credito Apify finito: mi fermo")
                break
            r.raise_for_status()
            dati = r.json()
            posti = dati if isinstance(dati, list) else []
        except Exception as e:
            log.warning("LEADMAPS ricerca non riuscita (%s): %s", ricerca[:40], str(e)[:160])
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
