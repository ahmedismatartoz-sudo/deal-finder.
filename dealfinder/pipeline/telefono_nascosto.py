"""Numero "nascosto" (bottone "Mostra numero") per le auto tolte dal sito solo per il telefono (13/11, ramo piu-auto).

Il sito toglie ~1.165 auto al passo "telefono" (269 della fascia richiesta). La regola del cliente resta: senza numero
non si va sul sito. Ma molte il numero ce l'hanno e noi non lo sappiamo:
  - Subito: la pagina dell'annuncio dice sempre showPhone=false (provato il 17/10 su 115 pagine), per questo il
    ricontrollo di Subito era spento (TEL_CAND_SUBITO=0). L'interfaccia di RICERCA invece mette advertiser.phone
    solo quando il venditore ha il numero (tempo_reale.telefono_subito, provato il 25/10): qui si cerca UN annuncio
    nella ricerca (marca e modello, prezzo esatto, regione) e se ne legge il sì/no. Come il collettore, mai il numero;
  - AutoScout24: la pagina funziona (log "TR autoscout venditore ... telefono=True" del 12/11); i "non leggibile" del
    ricontrollo erano in realtà annunci SPARITI (pagina che rimanda alla ricerca o 404) contati come "non leggibile"
    e riprovati ogni 2 ore: ora si segnano spariti come fa la verifica delle auto del sito.

Si salva SOLO il sì/no (listings.telefono_ok, telefono_at) come già fa tutto il codice: il numero non si legge, non
si salva e non va mai nei log (regola del cliente, migrazione 021).

Prudenza:
  - solo le auto che senza il problema del numero avrebbero già il margine minimo (o ci arrivano con lo sconto "Da
    trattare"): PRIORITARI, ricalcolati a ogni giro dell'elenco del sito;
  - tetto giornaliero TEL_API_MAX_GIORNO ricerche (300), una ogni TEL_API_PAUSA_S secondi (2);
  - su 403/429 dell'interfaccia: stop e pausa di TEL_API_PAUSA_BLOCCO_S secondi (3.600).
Interruttore: TEL_SUBITO_API=1 (default) | 0.
"""
from __future__ import annotations

import logging
import os
import re
import time

log = logging.getLogger("tempo_reale")

PRIORITARI: set = set()
_PAUSA = {"fino": 0.0}
_CONTO = {"giorno": None, "n": 0}
_DIAG = {"n": 0}
_GIRO = {"n": 0}


def inizio_giro() -> None:
    _GIRO["n"] = 0


def attivo() -> bool:
    return os.environ.get("TEL_SUBITO_API", "1") == "1"


def segna_prioritari(items: list[dict], tolti: list, fonte: dict | None = None) -> int:
    """Auto tolte al passo telefono con margine già sopra il minimo (o "Da trattare" entro lo sconto massimo)."""
    from ..web import scovo
    from ..web import sito_regole as SR
    tolti = set(tolti or [])
    g = SR.Regole()
    out = set()
    for it in items:
        if it.get("id") not in tolti or (fonte and fonte.get(it["id"]) not in ("Subito", "AutoScout24")):
            continue
        m, p = SR.margine(it), int(it.get("prezzo") or 0)
        if m is None or m <= 0 or p <= 0:
            continue
        if m >= scovo.soglia(p):
            out.add(it["id"])
            continue
        tetto = next(x for fino, x in g.tratta_max if p < fino)
        if any(m + sc >= scovo.soglia(p - sc) for sc in range(50, tetto + 1, 50)):
            out.add(it["id"])
    PRIORITARI.clear()
    PRIORITARI.update(out)
    return len(out)


def _oggi() -> str:
    return time.strftime("%Y-%m-%d")


def puo_cercare() -> str | None:
    """None se si può fare un'altra ricerca, altrimenti il motivo."""
    if not attivo():
        return "spento"
    if time.time() < _PAUSA["fino"]:
        return "pausa_blocco"
    if _CONTO["giorno"] != _oggi():
        _CONTO.update(giorno=_oggi(), n=0)
    if _CONTO["n"] >= int(os.environ.get("TEL_API_MAX_GIORNO", "300")):
        return "tetto_giorno"
    if _GIRO["n"] >= int(os.environ.get("TEL_API_PER_GIRO", "30")):
        return "tetto_giro"           # anche dopo un riavvio (contatore del giorno in memoria) mai più di 30 ogni giro
    return None


def _cifre(x) -> str:
    m = re.findall(r"\d{5,}", str(x or ""))
    return m[-1] if m else ""


def stesso_annuncio(item: dict, source_id, url: str | None = None) -> bool:
    urn = str(item.get("urn") or item.get("id") or "")
    if urn and urn == str(source_id):
        return True
    a, b = _cifre(urn), _cifre(source_id) or _cifre(url)
    return bool(a and b and a == b)


def client_api():
    """Client dell'interfaccia di ricerca di Subito, come il collettore (stesso proxy SCRAPER_PROXY)."""
    import httpx
    from ..collectors.subito import HEADERS
    return httpx.Client(timeout=25, follow_redirects=True, headers=HEADERS, proxy=os.environ.get("SCRAPER_PROXY") or None)


def regione_di(provincia: str | None) -> int | None:
    from ..collectors.subito import REGION_CODES
    from .tempo_reale import REGIONI
    for reg, provs in REGIONI.items():
        if (provincia or "").upper() in provs:
            return REGION_CODES.get(reg)
    return None


def parametri(r: dict, con_testo: bool = True) -> dict:
    p = int(r["price_eur"])
    params = {"c": 2, "t": "s", "ps": p, "pe": p, "lim": 100, "sort": "datedesc"}
    reg = regione_di(r.get("province"))
    if reg:
        params["r"] = reg
    q = " ".join(x for x in (r.get("make"), r.get("model")) if x).replace("-", " ").strip()
    if con_testo and q:
        params["q"] = q[:60]
    return params


def subito_telefono_api(client, r: dict) -> tuple[str, bool | None]:
    """('attivo', sì/no) se l'annuncio è nella ricerca; ('non_trovato', None); ('bloccato', None) su 403/429;
    ('errore', None). Il campo letto è solo la presenza del numero (tempo_reale.telefono_subito)."""
    from ..collectors.subito import API_HEADERS, API_SEARCH
    from .tempo_reale import telefono_subito
    if not r.get("price_eur"):
        return "non_trovato", None
    for con_testo in (True, False):
        if puo_cercare():
            return "tetto", None
        _CONTO["n"] += 1
        _GIRO["n"] += 1
        time.sleep(float(os.environ.get("TEL_API_PAUSA_S", "2")))
        try:
            resp = client.get(API_SEARCH, headers=API_HEADERS, params=parametri(r, con_testo))
        except Exception:
            return "errore", None
        if resp.status_code in (403, 429):
            _PAUSA["fino"] = time.time() + int(os.environ.get("TEL_API_PAUSA_BLOCCO_S", "3600"))
            return "bloccato", None
        if resp.status_code != 200:
            return "errore", None
        try:
            ads = (resp.json() or {}).get("ads") or []
        except Exception:
            return "errore", None                     # pagina di blocco al posto del JSON
        for it in ads:
            if isinstance(it, dict) and stesso_annuncio(it, r.get("source_id"), r.get("url")):
                tel = telefono_subito(it)
                if _DIAG["n"] < 3:
                    # diagnostica sicura: solo i NOMI dei campi del venditore e il tipo del campo telefono, mai valori
                    _DIAG["n"] += 1
                    adv = it.get("advertiser") if isinstance(it.get("advertiser"), dict) else {}
                    log.warning("TEL_NASCOSTO subito trovato: campi_venditore=%s tipo_phone=%s esito=%s",
                                sorted(adv)[:20], type(adv.get("phone")).__name__, tel)
                return "attivo", tel
    return "non_trovato", None


def diagnostica(st) -> None:
    """Una riga senza dati personali: quante ricerche oggi, pausa, prioritari (per capire da Render se va)."""
    log.warning("TEL_NASCOSTO %s", {"prioritari": len(PRIORITARI), "ricerche_oggi": _CONTO["n"],
                                     "pausa_min": max(0, int((_PAUSA["fino"] - time.time()) / 60)),
                                     **{k: v for k, v in dict(st).items() if not k.startswith("_")}})
