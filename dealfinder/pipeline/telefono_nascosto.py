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
Interruttore: TEL_SUBITO_API=0 (default dal 14/11) | 1.

17/11 (notte-01): perché il 13/11 il recupero dava 0 "sì" (300 ricerche, ~150 annunci trovati, tutti "no"). Non è un
errore di lettura: è il campo stesso. L'auto arriva tolta al passo telefono proprio PERCHÉ nella ricerca il suo
advertiser.phone mancava (il "sì" della ricerca lo segnano già la lettura degli annunci, _salva/segna_invariati, e
il giro giornaliero giro_telefoni_subito); ricercarla rilegge lo stesso campo e dà lo stesso "manca". Nei log del
13/11 gli annunci trovati hanno il venditore con soli company/name/type/user_id (nessun campo del telefono). Il
giro giornaliero conta ~79% "0" e ~21% senza campo (TR TELEFONI SUBITO 10-16/11: 30.500 / 8.000 su 38.700): sono i
venditori che hanno scelto di non mostrare il numero. Quindi resta spento; per Subito resta il controllo del testo
delle auto tolte (tempo_reale.telefoni_candidate, con le espressioni di sempre del sito).

18/11 sera (cliente OK 21:01): come controllo_auto.coda_dal_sito per lo sconto alto. segna_prioritari riceve anche
l'elenco del sito: ORDINE = prioritarie dalla migliore (margine/soglia), INUTILI = tolte al passo telefono che col
numero resterebbero fuori lo stesso (altre regole, margine, tetto commercianti pieno di auto migliori). In
telefoni_candidate le inutili non si aprono più (prima AutoScout24 le riapriva ogni 12 ore) e le prioritarie vanno
in ORDINE. Il controllo del numero nel testo resta su tutte (gratis, solo database). Subito resta come deciso il 17/11:
il "Mostra numero" c'è solo se il venditore mostra il numero, e la ricerca lo dice già (advertiser.phone); la pagina
via Web Unlocker (1,5 $ ogni 1.000 risposte riuscite, listino Bright Data pay-as-you-go) NON si accende: il segno
"phoneSkeleton" non è mai stato provato e un falso "sì" metterebbe sul sito un'auto senza numero (regola).
"""
from __future__ import annotations

import logging
import os
import re
import time

log = logging.getLogger("tempo_reale")

PRIORITARI: frozenset = frozenset()      # sostituito tutto insieme (mai svuotato a metà mentre un altro thread legge)
# 18/11 sera (cliente OK 21:01, "più auto senza toccare le regole"): come controllo_auto.coda_dal_sito per lo sconto
# alto. ORDINE = le prioritarie dalla migliore (margine/soglia), INUTILI = tolte al passo telefono che resterebbero
# fuori ANCHE col numero (altre regole del sito, margine, tetto dei commercianti già pieno di auto migliori): per
# loro nessuna pagina aperta. Valgono solo se l'elenco è recente (FRESCHI_MIN): altrimenti si fa come prima.
ORDINE: tuple = ()
INUTILI: frozenset = frozenset()
_STATO = {"t": 0.0, "log_t": 0.0}
FRESCHI_MIN = 30
_PAUSA = {"fino": 0.0}
_CONTO = {"giorno": None, "n": 0}
_DIAG = {"n": 0}
_GIRO = {"n": 0}


def inizio_giro() -> None:
    _GIRO["n"] = 0


def attivo() -> bool:
    return os.environ.get("TEL_SUBITO_API", "0") == "1"


# 17/11 (notte-01): esiti "in attesa" dei controlli dopo il telefono: non scartano per sempre (deciderà l'AI o il
# giro dopo), quindi per la PRIORITÀ del controllo del numero l'auto resta tra quelle che potrebbero andare sul sito
_IN_ATTESA = {"supercar_senza_controllo_ai", "jev_non_letto", "margine_alto_senza_jev", "aspetta_controllo_ai",
              "sconto_alto_da_controllare"}


def _margine_ok(it: dict, g) -> bool:
    """Margine già sopra il minimo, o "Da trattare" entro lo sconto massimo (criterio del 13/11, invariato)."""
    from ..web import scovo
    from ..web import sito_regole as SR
    m, p = SR.margine(it), int(it.get("prezzo") or 0)
    if m is None or m <= 0 or p <= 0:
        return False
    if m >= scovo.soglia(p):
        return True
    tetto = next(x for fino, x in g.tratta_max if p < fino)
    return any(m + sc >= scovo.soglia(p - sc) for sc in range(50, tetto + 1, 50))


def passerebbe_col_numero(it: dict, g) -> bool:
    return _col_numero(it, g)[0]


def _col_numero(it: dict, g) -> tuple[bool, dict]:
    """17/11 (notte-01): l'auto, tolta SOLO al passo telefono, passerebbe le altre regole del sito per auto (Roma, zona,
    venditore, prezzo intero, Jev, correzioni del margine, anti-truffa, margine minimo) se il numero ci fosse?
    Si lavora su una COPIA (le regole scrivono avvisi nell'auto) e le regole sono quelle del sito, non cambiate: serve
    solo a decidere quali auto controllare PRIMA. I tetti (commercianti, fuori zona, diesel) non si simulano: dipendono
    dalle altre auto; gli esiti "in attesa" (_IN_ATTESA) non escludono."""
    from ..web import sito_regole as SR
    # (18/11 sera: ritorna anche la copia, che ha il tipo del passo venditore per il controllo del tetto)
    c = dict(it, _r=dict(it.get("_r") or {}, tel=True))
    if isinstance(c.get("pv"), dict):
        c["pv"] = dict(c["pv"])
    dopo = False
    for nome, f in SR.REGOLE:
        if nome == "telefono":
            dopo = True
            continue
        if dopo:
            m = f(c, g)
            if m and m not in _IN_ATTESA:
                return False, c
    try:
        SR.t_margine_sito(c, g)                 # correzioni del margine del sito (solo in giù), come nell'elenco
    except Exception:
        pass
    m = SR.r_truffa(c, g)
    if m and m not in _IN_ATTESA:
        return False, c
    return _margine_ok(c, g), c


def _sotto_tetto(it: dict, g, comm: list, lont: list) -> bool:
    """Commerciante che col numero resterebbe comunque fuori dal tetto dei commercianti (margine sotto il più basso di
    quelli già tenuti: controllo_auto._sotto_al_tetto). I privati non hanno tetto."""
    from ..web import sito_regole as SR
    if not SR.e_commerciante(it) or it.get("diesel_extra"):
        return False
    from ..ai.controllo_auto import _sotto_al_tetto
    return _sotto_al_tetto(it, g, comm, lont)


def utili_e_ordine() -> tuple[tuple, frozenset] | None:
    """(ORDINE, INUTILI) se l'elenco che li ha calcolati è recente, altrimenti None (si fa come prima)."""
    if not _STATO["t"] or time.time() - _STATO["t"] > FRESCHI_MIN * 60:
        return None
    return ORDINE, INUTILI


def segna_prioritari(items: list[dict], tolti: list, fonte: dict | None = None, g=None, out: list | None = None) -> int:
    """Auto tolte al passo telefono che col numero passerebbero le altre regole del sito (passerebbe_col_numero):
    margine già sopra il minimo (o "Da trattare" entro lo sconto massimo), in zona, mai Roma, venditore ammesso,
    senza scarti Jev/anti-truffa definitivi. 17/11: prima si guardavano solo zona e margine (373 prioritarie, ma
    molte sarebbero uscite dopo per venditore, Jev o truffa)."""
    from ..web import sito_regole as SR
    global PRIORITARI
    tolti = set(tolti or [])
    if g is None:
        try:
            g = SR.Regole.da_env()
        except Exception:
            g = SR.Regole()
    global ORDINE, INUTILI
    from ..web import scovo
    sito = out
    comm = sorted(int(SR.margine(x) or 0) for x in (sito or []) if SR.e_commerciante(x))
    lont = sorted(int(SR.margine(x) or 0) for x in (sito or []) if x.get("_tipo") == "comm_lontano")
    prio, inutili, conti = [], set(), {"tolti_telefono": 0, "altra_fonte": 0, "prioritarie": 0, "inutili": 0,
                                       "inutili_tetto_commercianti": 0, "errore_regole": 0}
    for it in items:
        if it.get("id") not in tolti:
            continue
        conti["tolti_telefono"] += 1
        if fonte and fonte.get(it["id"]) not in ("Subito", "AutoScout24"):
            conti["altra_fonte"] += 1
            continue
        errore, copia = False, it
        try:
            ok, copia = _col_numero(it, g)
        except Exception:
            # una regola che non sa leggere l'auto: criterio del 13/11 (zona e margine), mai Roma
            errore = True
            conti["errore_regole"] += 1
            ok = not (SR.r_no_roma(it, g) or SR.r_zona(dict(it), g)) and _margine_ok(it, g)
        if ok and sito is not None:
            try:
                if _sotto_tetto(copia, g, comm, lont):
                    ok = False
                    conti["inutili_tetto_commercianti"] += 1
            except Exception:
                pass
        if ok:
            p = int(it.get("prezzo") or 0)
            try:
                r = float(SR.margine(it) or 0) / max(1, scovo.soglia(p))
            except Exception:
                r = 0.0
            prio.append((-r, it["id"]))
        elif not errore and isinstance(it.get("id"), int):
            inutili.add(it["id"])         # anche col numero resterebbe fuori: nessuna pagina (un errore non esclude)
    prio.sort()
    conti["prioritarie"], conti["inutili"] = len(prio), len(inutili)
    PRIORITARI = frozenset(i for _r, i in prio)
    ORDINE = tuple(i for _r, i in prio)
    INUTILI = frozenset(inutili)
    _STATO["t"] = time.time()
    if time.time() - _STATO["log_t"] > 600:            # una riga ogni 10 minuti, solo conteggi
        _STATO["log_t"] = time.time()
        log.warning("TEL_PRIORITA %s", conti)
    return len(prio)


def _oggi() -> str:
    """Giorno di Roma, lo stesso del conto nel database (13/11, giudice N8)."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")
    except Exception:
        return time.strftime("%Y-%m-%d")


_TABELLA_OK = {"ok": False}


def _tabella(conn) -> None:
    """La tabella (migrazione 052) si crea solo se manca (to_regclass), una volta per processo (giudice N9)."""
    if _TABELLA_OK["ok"]:
        return
    if not conn.execute("SELECT to_regclass('telefono_ricerche') AS t").fetchone()["t"]:
        conn.execute("CREATE TABLE IF NOT EXISTS telefono_ricerche (giorno DATE PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0)")
    _TABELLA_OK["ok"] = True


def leggi_conto(conn) -> None:
    """13/11 (giudice M4): il conto delle ricerche del giorno sta nel database (sopravvive ai riavvii)."""
    try:
        _tabella(conn)
        r = conn.execute("SELECT n FROM telefono_ricerche WHERE giorno = %s::date", (_oggi(),)).fetchone()
        conn.commit()
        n = int(r["n"]) if r else 0
        if _CONTO["giorno"] != _oggi():
            _CONTO.update(giorno=_oggi(), n=0)
        _CONTO["n"] = max(_CONTO["n"], n)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("TEL_NASCOSTO conto non letto: %s", str(e)[:120])


def _conta(conn) -> None:
    _CONTO["n"] += 1
    _GIRO["n"] += 1
    if conn is None:
        return
    try:
        conn.execute("INSERT INTO telefono_ricerche (giorno, n) VALUES (%s::date, 1) "
                     "ON CONFLICT (giorno) DO UPDATE SET n = telefono_ricerche.n + 1", (_oggi(),))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


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


def subito_telefono_api(client, r: dict, conn=None) -> tuple[str, bool | None]:
    """('attivo', sì/no) se l'annuncio è nella ricerca; ('non_trovato', None); ('bloccato', None) su 403/429;
    ('errore', None). Il campo letto è solo la presenza del numero (tempo_reale.telefono_subito)."""
    from ..collectors.subito import API_HEADERS, API_SEARCH
    from .tempo_reale import telefono_subito
    if not r.get("price_eur"):
        return "non_trovato", None
    for con_testo in (True, False):
        if puo_cercare():
            return "tetto", None
        _conta(conn)
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
