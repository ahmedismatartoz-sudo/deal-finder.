"""25/11 (cliente: "Skovo deve leggere TUTTI gli annunci di Subito e AutoScout24 della zona, così arrivano più auto di
privati"): LETTURA COMPLETA della zona attiva, fino in fondo ai risultati di ogni ricerca.

Le regole del cliente NON cambiano (soglie, zone, km, anno, telefono, tetti dei commercianti, antifrode): qui si legge
e si salva soltanto, con lo stesso salvataggio del tempo reale (tempo_reale._salva: zona, normalizzazione, doppioni).
La valutazione e la pubblicazione restano quelle di sempre (giro della zona ogni 3 ore, ricalcolo).

Cosa legge, un ciclo al giorno (CICLO_ORE), a pezzi di GIRO_MINUTI minuti ogni PAUSA_GIRI_MIN (filo del worker del tempo
reale), così i portali non ricevono mai una raffica:
- Subito: ogni provincia della zona attiva (zone.tutte()), fasce di prezzo di tempo_reale.FASCE_SUBITO fino a
  PREZZO_MAX, 100 annunci a pagina. Una fascia con più di SUB_PAGINE_MAX pagine (totale dichiarato, o pagine tutte
  piene) si DIVIDE a metà finché ogni pezzo sta sotto il tetto (o è larga meno di SUB_FASCIA_MIN euro);
- AutoScout24: privati, stessi centri del giro della zona (Milano + raggio del tempo reale, Brescia, Torino), fasce di
  tempo_reale._fasce_as(). Il portale mostra al massimo 20 pagine da 20: una ricerca con più di 400 risultati si divide
  per prezzo, poi per anni (AutoScoutCollector._dividi, la stessa regola della raccolta massiva).
I pezzi già letti si ricordano (pipeline/passi, job "lettura_completa:<ciclo>"): un riavvio o la fine del tempo
riprendono da lì.

Rispetto dei portali: una richiesta alla volta, pause tra le richieste (Subito 1,2-2,5 s come il tempo reale;
AutoScout24 2-4,5 s IN PIÙ del limitatore unico del tempo reale), stesse intestazioni e stessi client del tempo reale,
nessun proxy (né Web Unlocker), nessuna riprova su 403/429. Con 403, 429 o una pagina di blocco (risposta senza i dati) la FONTE si
ferma per questo giro e per FERMO_BLOCCO_ORE ore; lo si scrive nei log (LETTURA_COMPLETA_BLOCCO).

Log (solo conteggi): COPERTURA_LETTURA a fine di ogni giro e, a ciclo finito, COPERTURA_LETTURA con giro
"lettura_completa:ciclo" (totale dichiarato dai portali contro annunci letti: la copertura vera).

Nessuna variabile d'ambiente nuova: i valori sono qui sotto. Spegnimento senza deploy: interruttore degli automatismi
"lettura_completa" (dealfinder/automatismi.py). Avvio a mano: BREVO_IMPORT_LISTE=lettura_completa:<etichetta>.
"""
from __future__ import annotations

import json
import logging
import random
import time

from . import copertura_lettura as CL

log = logging.getLogger("lettura_completa")

CICLO_ORE = 20             # un ciclo completo al giorno
CICLO_MAX_ORE = 48         # un ciclo che non finisce (errori ripetuti) si chiude come incompleto e si riparte
GIRO_MINUTI = 12           # durata massima di un pezzo
PAUSA_GIRI_MIN = 30        # tra un pezzo e l'altro (filo del worker)
PRIMO_GIRO_MIN = 25        # dopo l'avvio del worker
FERMO_BLOCCO_ORE = 3       # dopo un blocco la fonte riposa
ERRORI_MAX = 3             # errori di fila (rete, 5xx): la fonte si ferma per questo giro
PREZZO_MAX = 200000        # come il filtro del sito e il tempo reale (_as_prezzo_max)

SUB_LIM = 100
SUB_PAGINE_MAX = 30        # pagine da 100 per ricerca: oltre la fascia si divide
SUB_FASCIA_MIN = 100       # euro: una fascia più stretta non si divide più
SUB_PAUSA = (1.2, 2.5)     # come il tempo reale (TR_SUBITO_PAUSA_*)

AS_PAGINA = 20
AS_PAGINE_MAX = 20         # AutoScout24 non mostra oltre la 20ª pagina (400 annunci)
AS_PAUSA = (2.0, 4.5)      # come AutoScoutCollector, in più del limitatore del tempo reale
AS_CENTRI_EXTRA = (("25121", "90"), ("10121", "100"))   # Brescia e Torino, come il giro della zona (ZC_CAP_EXTRA)

FONTI = ("subito", "autoscout24")
RIGA = "lettura_completa"


class Blocco(Exception):
    """403, 429 o pagina di blocco: la fonte si ferma per questo giro."""


class Errore(Exception):
    """Errore passeggero (rete, 5xx): la ricerca resta da leggere."""


class Tempo(Exception):
    """Tempo del giro finito (o arresto del processo): si riprende al giro dopo."""


class Lettore:
    """Stato di un giro: tempo, pause, passi fatti, salvataggio. Tutto sostituibile nei test."""

    def __init__(self, salva, scadenza: float | None = None, dormi=None, fatto=None, segna=None, fermato=None,
                 adesso=time.time):
        self.salva, self.scadenza, self.adesso = salva, scadenza, adesso
        self.dormi = dormi or time.sleep
        if fatto is None or segna is None:
            from . import passi
            fatto, segna = fatto or passi.fatto, segna or passi.segna
        self.fatto, self.segna = fatto, segna
        self.fermato = fermato or (lambda: False)
        self.errori_fila = 0
        self.incompleta = False           # almeno una ricerca non letta fino in fondo per un errore

    def tempo(self) -> None:
        if self.fermato() or (self.scadenza is not None and self.adesso() > self.scadenza):
            raise Tempo()

    def pausa(self, intervallo) -> None:
        if intervallo:
            self.dormi(random.uniform(*intervallo))

    def errore(self, fonte: str, ricerca: str, gruppo: str) -> None:
        """Una ricerca fallita per un errore passeggero: resta da leggere; troppi di fila fermano la fonte."""
        self.incompleta = True
        self.errori_fila += 1
        CL.nota(fonte, ricerca, gruppo=gruppo, motivo="errore")
        if self.errori_fila >= ERRORI_MAX:
            raise Blocco(f"{ERRORI_MAX} errori di fila")


# ------------------------------------------------------------------ Subito

def totale_subito(j: dict):
    """Totale dichiarato dall'interfaccia di ricerca di Subito, se c'è (stesse chiavi di conta_mercato)."""
    from .conta_mercato import _totale_subito
    return _totale_subito(j) if isinstance(j, dict) else None


def _subito_pagina(L: Lettore, col, params: dict) -> tuple[list, int | None]:
    """Una pagina dell'interfaccia di ricerca: (annunci, totale dichiarato). Nessuna riprova su 403/429."""
    from ..collectors.subito import API_HEADERS, API_SEARCH
    L.tempo()
    try:
        r = col.client.get(API_SEARCH, headers=API_HEADERS, params=params)
    except Exception as e:
        L.pausa(SUB_PAUSA)
        raise Errore(type(e).__name__)
    L.pausa(SUB_PAUSA)
    if r.status_code in (403, 429):
        raise Blocco(f"HTTP {r.status_code}")
    if r.status_code != 200:
        raise Errore(f"HTTP {r.status_code}")
    try:
        j = r.json()
    except Exception:
        raise Blocco("risposta senza dati")
    ads = j.get("ads") if isinstance(j, dict) else None
    if not isinstance(ads, list):
        raise Blocco("risposta senza dati")
    L.errori_fila = 0
    return ads, totale_subito(j)


def subito_fetta(L: Lettore, col, regione, key, sn: str, ps: int, pe: int, visti: set, solo_privati: bool = False) -> None:
    """Una provincia e una fascia di prezzo, fino in fondo; se è troppo piena si divide a metà (i doppioni si saltano)."""
    from ..collectors.subito import parse_item
    ricerca = f"{sn}:{ps}-{pe}"
    chiave = f"sub:{regione}:{sn}:{ps}-{pe}"
    if L.fatto(chiave):
        return
    divisibile = pe - ps >= 2 * SUB_FASCIA_MIN

    def dividi(totale, pagine):
        mid = (ps + pe) // 2
        CL.nota("subito", ricerca, gruppo=sn, totale=totale, pagine=pagine, motivo="divisa")
        L.segna(chiave + ":div")
        subito_fetta(L, col, regione, key, sn, ps, mid, visti, solo_privati)
        subito_fetta(L, col, regione, key, sn, mid, pe, visti, solo_privati)
        # il genitore NON si segna fatto: un pezzo fallito si rilegge al giro dopo (":div" porta dritto ai pezzi)

    if divisibile and L.fatto(chiave + ":div"):          # già vista troppo piena in un giro precedente
        return dividi(None, 0)
    totale = None
    pagina = 0
    while True:
        try:
            ads, tot = _subito_pagina(L, col, {"c": 2, "r": regione, "ci": key, "t": "s", "ps": ps, "pe": pe,
                                               "lim": SUB_LIM, "start": pagina * SUB_LIM, "sort": "datedesc"})
        except Errore:
            return L.errore("subito", ricerca, sn)
        except Tempo:
            CL.nota("subito", ricerca, gruppo=sn, motivo="tempo")
            raise
        except Blocco:
            CL.nota("subito", ricerca, gruppo=sn, motivo="blocco")
            raise
        if pagina == 0:
            totale = tot
            if totale is not None and totale > SUB_PAGINE_MAX * SUB_LIM and divisibile:
                return dividi(totale, 1)
        tenuti = []
        for it in ads:
            l = parse_item(it) if isinstance(it, dict) else None
            if not l or l.source_id in visti:
                continue
            visti.add(l.source_id)
            if l.province and l.province.upper() != sn:
                continue                  # il filtro per provincia non ha funzionato: si scarta qui
            if solo_privati and l.seller_type != "privato":
                continue
            tenuti.append(l)
        nuovi = L.salva(tenuti) if tenuti else 0
        CL.nota("subito", ricerca, gruppo=sn, totale=totale, pagine=1, letti=len(ads), nuovi=nuovi)
        pagina += 1
        if len(ads) < SUB_LIM or (totale is not None and pagina * SUB_LIM >= totale):
            CL.nota("subito", ricerca, gruppo=sn, motivo="fine_risultati")
            L.segna(chiave)
            return
        if pagina >= SUB_PAGINE_MAX:
            if divisibile:                # totale non dichiarato e tutte le pagine piene: ce ne sono altri
                return dividi(totale, 0)
            CL.nota("subito", ricerca, gruppo=sn, motivo="tetto_pagine")
            L.segna(chiave)
            return


def province_subito(zona: set) -> list:
    """[(codice regione, provincia, codice provincia)] per le province della zona che Subito conosce."""
    from ..collectors.subito import PROVINCE_KEYS_NOTE, REGION_CODES
    from .tempo_reale import REGIONI
    out = []
    for reg, provs in REGIONI.items():
        code = REGION_CODES.get(reg)
        note = PROVINCE_KEYS_NOTE.get(code) or {}
        for sn in provs:
            if sn in zona and sn in note:
                out.append((code, sn, note[sn]))
    return out


def fasce_subito() -> list:
    from .tempo_reale import FASCE_SUBITO
    f = [(a, b) for a, b in FASCE_SUBITO if a < PREZZO_MAX]
    if f[-1][1] < PREZZO_MAX:
        f.append((f[-1][1], PREZZO_MAX))
    return f


def leggi_subito(L: Lettore, col, zona: set, solo_privati: bool = False) -> None:
    for regione, sn, key in province_subito(zona):
        visti: set = set()
        for ps, pe in fasce_subito():
            subito_fetta(L, col, regione, key, sn, ps, pe, visti, solo_privati)


# ------------------------------------------------------------------ AutoScout24

def as_url(cap: str, raggio: str, lo: int, hi: int, page: int, years=None, anno_min: int = 2007) -> str:
    from ..collectors.autoscout import BASE
    y0, y1 = years or (anno_min, None)
    anni = f"&fregfrom={y0}" + (f"&fregto={y1}" if y1 else "")
    return (f"{BASE}/lst?atype=C&cy=I&ustate=N%2CU&damaged_listing=include&custtype=P&zip={cap}&zipr={raggio}"
            f"&pricefrom={lo}&priceto={hi}{anni}&sort=age&desc=1&page={page}")


def _as_pagina(L: Lettore, client, url: str) -> tuple[list, int | None]:
    """Una pagina di ricerca: (annunci, totale dichiarato). Nessuna riprova su 403/429; se il tempo reale ha messo
    AutoScout24 in pausa dopo un 429 (as_riprova.fermo) non si chiede nulla."""
    from ..collectors.autoscout import AutoScoutCollector
    from ..collectors.base import next_data
    from . import as_riprova
    L.tempo()
    if as_riprova.fermo():
        raise Blocco("in pausa dopo un 429 del tempo reale")
    try:
        r = client.get(url)
    except Exception as e:
        L.pausa(AS_PAUSA)
        raise Errore(type(e).__name__)
    L.pausa(AS_PAUSA)
    if r.status_code in (403, 429):
        raise Blocco(f"HTTP {r.status_code}")
    if r.status_code != 200:
        raise Errore(f"HTTP {r.status_code}")
    try:
        data = next_data(r.text)
    except Exception:
        data = None
    props = ((data or {}).get("props") or {}).get("pageProps") if isinstance(data, dict) else None
    if not isinstance(props, dict):
        raise Blocco("pagina senza dati")
    L.errori_fila = 0
    items = [it for it in (props.get("listings") or []) if isinstance(it, dict)]
    return items, AutoScoutCollector._total(props)


def as_fetta(L: Lettore, client, cap: str, raggio: str, lo: int, hi: int, years, visti: set, divisore,
             anno_min: int = 2007) -> None:
    """Una ricerca di privati (centro, raggio, prezzo, anni) fino in fondo; oltre 400 risultati si divide."""
    from ..collectors.autoscout import parse_item
    gruppo = f"{cap}:{raggio}"
    ricerca = f"{gruppo}:{lo}-{hi}" + (f":{years[0]}-{years[1]}" if years else "")
    chiave = "as:" + ricerca
    if L.fatto(chiave):
        return

    def dividi(pezzi, totale, pagine):
        CL.nota("autoscout24", ricerca, gruppo=gruppo, totale=totale, pagine=pagine, motivo="divisa")
        L.segna(chiave + ":div")
        for a, b, ys in pezzi:
            as_fetta(L, client, cap, raggio, a, b, ys, visti, divisore, anno_min)

    if L.fatto(chiave + ":div"):
        pezzi = divisore(lo, hi, years)
        if pezzi:
            return dividi(pezzi, None, 0)
    totale = None
    pagina = 0
    oltre_tetto = False
    while True:
        try:
            items, tot = _as_pagina(L, client, as_url(cap, raggio, lo, hi, pagina + 1, years, anno_min))
        except Errore:
            return L.errore("autoscout24", ricerca, gruppo)
        except Tempo:
            CL.nota("autoscout24", ricerca, gruppo=gruppo, motivo="tempo")
            raise
        except Blocco:
            CL.nota("autoscout24", ricerca, gruppo=gruppo, motivo="blocco")
            raise
        if pagina == 0:
            totale = tot
            if totale is not None and totale > AS_PAGINA * AS_PAGINE_MAX:
                pezzi = divisore(lo, hi, years)
                if pezzi:
                    return dividi(pezzi, totale, 1)
                oltre_tetto = True        # non si divide più: si leggono i primi 400 e lo si dice
        tenuti = []
        for it in items:
            l = parse_item(it)
            if not l or not l.price_eur or l.source_id in visti:
                continue
            visti.add(l.source_id)
            l.seller_type = "privato"     # deciso dalla ricerca (custtype=P)
            if isinstance(l.raw, dict):   # memoria: dell'annuncio originale solo il venditore, MAI i numeri di telefono
                v = l.raw.get("seller")
                l.raw = {"seller": {k: x for k, x in v.items() if k != "phones"}} if isinstance(v, dict) else {}
            tenuti.append(l)
        nuovi = L.salva(tenuti) if tenuti else 0
        CL.nota("autoscout24", ricerca, gruppo=gruppo, totale=totale, pagine=1, letti=len(items), nuovi=nuovi)
        pagina += 1
        if len(items) < AS_PAGINA or (totale is not None and pagina * AS_PAGINA >= totale):
            CL.nota("autoscout24", ricerca, gruppo=gruppo, motivo="fine_risultati")
            L.segna(chiave)
            return
        if pagina >= AS_PAGINE_MAX:
            pezzi = [] if oltre_tetto else divisore(lo, hi, years)
            if pezzi:                     # totale non dichiarato e 20 pagine piene: ce ne sono altri
                return dividi(pezzi, totale, 0)
            CL.nota("autoscout24", ricerca, gruppo=gruppo, motivo="tetto_portale")
            log.warning("LETTURA_COMPLETA autoscout %s: oltre 400 risultati e non divisibile, letti i primi 400", ricerca)
            L.segna(chiave)
            return


def client_autoscout(tr):
    """Stesse intestazioni del tempo reale (tempo_reale._client_as), senza proxy, dietro il limitatore UNICO del tempo
    reale (LIMITE_AS): nel worker il ritmo totale verso AutoScout24 non supera mai quello di prima."""
    import httpx
    from ..collectors.autoscout import UA
    return tr.ClienteLimitato(httpx.Client(timeout=25, follow_redirects=True, headers={
        "User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9", "Accept": "text/html"}))


def centri_autoscout() -> list:
    from .tempo_reale import _raggio_as
    return [("20121", str(_raggio_as()))] + [c for c in AS_CENTRI_EXTRA]


def leggi_autoscout(L: Lettore, client, fasce=None, centri=None, anno_min: int | None = None) -> None:
    import os
    from ..collectors.autoscout import AutoScoutCollector
    from .tempo_reale import _fasce_as
    divisore = AutoScoutCollector()._dividi
    anno = int(anno_min or os.environ.get("ANNO_MINIMO", "2007"))      # variabile già esistente (regola del sito)
    visti: set = set()
    for cap, raggio in (centri or centri_autoscout()):
        for lo, hi in (fasce or _fasce_as()):
            as_fetta(L, client, cap, raggio, lo, hi, None, visti, divisore, anno)


# ------------------------------------------------------------------ stato del ciclo (tabella tempo_reale)

def _stato(conn) -> dict:
    try:
        r = conn.execute("SELECT stats FROM tempo_reale WHERE giro=%s", (RIGA,)).fetchone()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {}
    s = (r or {}).get("stats") if r else None
    if isinstance(s, str):
        try:
            s = json.loads(s)
        except ValueError:
            s = None
    return s if isinstance(s, dict) else {}


def _scrivi_stato(conn, s: dict) -> None:
    try:
        conn.execute("INSERT INTO tempo_reale (giro, ultimo_at, stats) VALUES (%s, now(), %s) "
                     "ON CONFLICT (giro) DO UPDATE SET ultimo_at=EXCLUDED.ultimo_at, stats=EXCLUDED.stats",
                     (RIGA, json.dumps(s)))
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("LETTURA_COMPLETA stato non salvato: %s", type(e).__name__)


def ciclo_da_fare(s: dict, ora: float, forza: bool = False) -> dict | None:
    """Lo stato del ciclo su cui lavorare adesso, oppure None se l'ultimo è finito da meno di CICLO_ORE ore."""
    if s.get("ciclo") and not s.get("finito"):
        if ora - float(s.get("iniziato") or ora) <= CICLO_MAX_ORE * 3600:
            return s
        log.warning("LETTURA_COMPLETA ciclo %s non finito in %s ore: chiuso come incompleto, si riparte", s.get("ciclo"),
                    CICLO_MAX_ORE)
    elif s.get("finito") and not forza and ora - float(s["finito"]) < CICLO_ORE * 3600:
        return None
    return {"ciclo": int(s.get("ciclo") or 0) + 1, "iniziato": ora, "finito": None, "finite": {}, "fermo_fino": {},
            "blocchi": {}, "tot": {}}


CHIUSE = ("fine_risultati", "tetto_pagine", "tetto_portale")


def _accumula(s: dict, m, salvati: dict) -> None:
    """Somma nel ciclo i conteggi del giro. Il totale dichiarato e i "letti_chiuse" contano solo le ricerche CHIUSE in
    questo giro (una ricerca interrotta dal tempo si rilegge da capo al giro dopo: non va contata due volte)."""
    for (fonte, _k), r in list(m.ricerche.items()):
        t = s.setdefault("tot", {}).setdefault(fonte, {})
        for k in ("totale_dichiarato", "letti_chiuse", "pagine", "letti", "nuovi", "salvati", "ricerche", "tagliate"):
            t.setdefault(k, 0)
        t["pagine"] += r["pagine"]
        t["letti"] += r["letti"]
        t["nuovi"] += r["nuovi"]
        if r["motivo"] in CHIUSE:
            t["ricerche"] += 1
            t["totale_dichiarato"] += int(r["totale"] or 0)
            t["letti_chiuse"] += r["letti"]
            t["tagliate"] += r["motivo"] != "fine_risultati"
    for fonte, n in salvati.items():
        s.setdefault("tot", {}).setdefault(fonte, {}).setdefault("salvati", 0)
        s["tot"][fonte]["salvati"] += n


def giro(conn, minuti: float | None = None, forza: bool = False, fonti=FONTI, lettori: dict | None = None,
         adesso=time.time) -> dict | None:
    """Un pezzo della lettura completa (al massimo `minuti`). None se non c'è niente da fare adesso.
    `lettori` (test): {fonte: funzione(L)} al posto delle letture vere."""
    from .. import automatismi
    if not automatismi.attivo("lettura_completa"):
        return None
    ora = adesso()
    s = ciclo_da_fare(_stato(conn), ora, forza)
    if s is None:
        return None
    from . import passi, tempo_reale as tr
    st = {"salvati": 0}
    per_fonte: dict = {}

    def salva(listings: list) -> int:
        from collections import Counter
        c: Counter = Counter()
        try:
            tr._salva(conn, listings, c)
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.warning("LETTURA_COMPLETA salvataggio non riuscito: %s", type(e).__name__)
            return 0
        st["salvati"] += int(c.get("salvati", 0))
        if listings:
            per_fonte[listings[0].source] = per_fonte.get(listings[0].source, 0) + int(c.get("salvati", 0))
        return sum(1 for l in listings if getattr(l, "_tr_ev", None) == "nuovo")

    if lettori is None:
        def _sub(L):
            from ..collectors.subito import SubitoCollector
            from ..db import db_pieno
            from ..zone import tutte
            col = SubitoCollector(proxy=None)        # mai proxy né Web Unlocker: solo l'interfaccia di ricerca
            leggi_subito(L, col, set(tutte()), solo_privati=bool(db_pieno(conn)))

        def _as(L):
            leggi_autoscout(L, client_autoscout(tr))
        lettori = {"subito": _sub, "autoscout24": _as}

    scadenza = ora + float(minuti if minuti is not None else GIRO_MINUTI) * 60
    m = CL.apri("lettura_completa")
    esito = {"ciclo": s["ciclo"], "bloccate": [], "tempo": False}
    with passi.locale(f"{RIGA}:{s['ciclo']}"):
        for fonte in fonti:
            if s["finite"].get(fonte) or adesso() < float(s["fermo_fino"].get(fonte) or 0):
                continue
            L = Lettore(salva, scadenza=scadenza, dormi=tr._dormi, fermato=tr.fermato, adesso=adesso)
            try:
                lettori[fonte](L)
                if not L.incompleta:
                    s["finite"][fonte] = True
            except Tempo:
                esito["tempo"] = True
                break
            except Blocco as b:
                s["fermo_fino"][fonte] = adesso() + FERMO_BLOCCO_ORE * 3600
                s["blocchi"][fonte] = int(s["blocchi"].get(fonte) or 0) + 1
                esito["bloccate"].append(fonte)
                log.warning("LETTURA_COMPLETA_BLOCCO %s", json.dumps({"fonte": fonte, "motivo": str(b)[:80],
                            "ferma_ore": FERMO_BLOCCO_ORE, "blocchi_nel_ciclo": s["blocchi"][fonte]}))
            except Exception as e:                 # un errore imprevisto non ferma l'altra fonte
                log.warning("LETTURA_COMPLETA %s non riuscita: %s", fonte, type(e).__name__)
        finito = all(s["finite"].get(f) for f in fonti)
        esito["finito"] = finito
        _accumula(s, m, per_fonte)
        CL.chiudi(m, extra={"ciclo": s["ciclo"], "ciclo_finito": finito, "salvati": st["salvati"],
                            "bloccate": esito["bloccate"], "fermato_tempo": esito["tempo"]})
        if finito:
            s["finito"] = adesso()
            tot = {}
            for f, t in (s.get("tot") or {}).items():
                dich, sal = t.get("totale_dichiarato") or 0, t.get("salvati") or 0
                tot[f] = dict(t, totale_dichiarato=dich or None,
                              # quanto del dichiarato dal portale è stato letto da questo ciclo
                              copertura_pct=round(100 * t.get("letti_chiuse", 0) / dich, 1) if dich else None,
                              # degli annunci in zona salvati, quanti erano GIÀ nel database (= copertura di prima)
                              gia_noti_pct=round(100 * (1 - t.get("nuovi", 0) / sal), 1) if sal else None)
            log.warning("COPERTURA_LETTURA %s", json.dumps({
                "giro": "lettura_completa:ciclo", "ciclo": s["ciclo"], "ore": round((s["finito"] - float(s["iniziato"])) / 3600, 1),
                "blocchi": s.get("blocchi") or {}, "fonti": tot}))
            try:
                passi.finito(f"{RIGA}:{s['ciclo']}")
            except Exception as e:
                log.warning("LETTURA_COMPLETA passi non cancellati: %s", type(e).__name__)
    _scrivi_stato(conn, s)
    esito["salvati"] = st["salvati"]
    return esito


# ------------------------------------------------------------------ avvii

def filo_worker(fine, guardiano=None, tr=None) -> None:
    """Filo del worker del tempo reale: un pezzo ogni PAUSA_GIRI_MIN minuti, con un lucchetto suo ('lettura')."""
    if tr is None:
        from . import tempo_reale as tr
    fine.ev.wait(PRIMO_GIRO_MIN * 60)
    while not fine.ev.is_set():
        if guardiano is not None:
            guardiano.inizio = time.time()
        try:
            st = tr.con_lucchetto("lettura", giro)
            if st is not None:
                log.warning("LETTURA_COMPLETA giro: %s", json.dumps(st))
        except Exception as e:
            log.warning("LETTURA_COMPLETA non riuscita: %s", type(e).__name__)
        finally:
            if guardiano is not None:
                guardiano.inizio = None
        try:
            from ..memoria import rilascia
            rilascia("lettura completa")
        except Exception:
            pass
        fine.ev.wait(PAUSA_GIRI_MIN * 60)


LAVORO_ORE_MAX = 6


def lavoro(spec: str = "", dormi=time.sleep) -> dict:
    """Avvio a mano (BREVO_IMPORT_LISTE=lettura_completa:<etichetta>): un ciclo NUOVO subito, a pezzi di GIRO_MINUTI
    con 1 minuto di respiro, finché è finito, le fonti rimaste sono in pausa per un blocco, o dopo LAVORO_ORE_MAX ore
    (il resto lo continua il filo del worker). Stesso lucchetto del worker: mai due letture insieme."""
    from . import tempo_reale as tr
    t0 = time.time()
    esiti = {"giri": 0, "salvati": 0, "finito": False}
    forza = True
    while time.time() - t0 < LAVORO_ORE_MAX * 3600:
        st = tr.con_lucchetto("lettura", giro, None, forza)
        forza = False
        if st is None:                    # lucchetto occupato, interruttore spento o ciclo già finito
            break
        esiti["giri"] += 1
        esiti["salvati"] += int(st.get("salvati") or 0)
        esiti["finito"] = bool(st.get("finito"))
        if st.get("finito") or not st.get("tempo"):
            break                         # finito, oppure fonti in pausa per un blocco / ricerche con errori
        dormi(60)
    esiti["minuti"] = round((time.time() - t0) / 60)
    return esiti
