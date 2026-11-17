"""Mercato quasi in tempo reale (dal 16/10/2026): due agenti che girano in continuazione dentro il sito.

1. VERIFICA (ogni TR_VERIFICA_MIN = 10 minuti): riapre ogni auto pubblicata sul sito (Subito e AutoScout24) e,
   se l'annuncio non esiste più, la toglie subito (status 'scomparso'). Su AutoScout24 controlla anche che il
   venditore abbia un numero di telefono (si salva solo sì/no, mai il numero).
2. NUOVI (ogni TR_NUOVI_MIN = 10 minuti): legge gli annunci pubblicati dall'ultimo giro su Subito (Lombardia,
   Piemonte, Emilia, Liguria entro 150 km da Milano) e AutoScout24 (privati entro 150 km, dal più recente),
   li salva, e quelli di privati con il numero li valuta subito con la policy prudente: se sono opportunità
   (o da approfondire) vanno sul sito nello stesso giro. Gli annunci con danni o guasti nel testo restano
   all'analisi completa (AI o controllo a mano): non si pubblicano in automatico.

Obiettivo: un annuncio nuovo sul sito entro ~15 minuti (10 di raccolta + 5 di analisi), uno sparito tolto entro 10.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

log = logging.getLogger("tempo_reale")

REGIONI = {
    "lombardia": ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV", "CR", "MN", "SO"],
    "piemonte": ["NO", "VC", "BI", "VB", "AL", "AT", "TO", "CN"],        # 9/11: CN per Torino e dintorni
    "emilia-romagna": ["PC", "PR", "RE"],
    "liguria": ["GE"],
}
_codici_regione: dict[str, int] = {}
LOCK = {"nuovi": 734101, "verifica": 734102, "telefoni": 734103, "targhe": 734104, "completo": 734105,
        "rinfresca": 734106}


# ------------------------------------------------------------------ fondamenta della velocità (16/11, FOND_VELOCITA.md)

# Arresto pulito: il worker accende FERMA al SIGTERM; i giri lo guardano tra una fase e l'altra e le attese (pause tra
# le richieste, riprove dopo un 429) si interrompono subito. Nel sito non si accende mai.
FERMA = threading.Event()


def fermato() -> bool:
    return FERMA.is_set()


def _dormi(secondi: float) -> None:
    """Pausa che si interrompe all'arresto (FERMA)."""
    if secondi and secondi > 0:
        FERMA.wait(secondi)


class Limitatore:
    """UN SOLO ritmo di richieste verso AutoScout24 per tutto il processo: il giro veloce e la lettura completa (due
    fili del worker) prima leggevano in parallelo, ognuno con la sua pausa, e raddoppiavano il ritmo (429).
    Tra due richieste passano almeno TR_AS_INTERVALLO_S secondi (default = TR_AS_PAUSA_S, 1,5: il ritmo di prima)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._prossima = 0.0

    def intervallo(self) -> float:
        try:
            return max(0.0, float(os.environ.get("TR_AS_INTERVALLO_S") or os.environ.get("TR_AS_PAUSA_S") or 1.5))
        except ValueError:
            return 1.5

    def attendi(self) -> float:
        """Prenota il prossimo turno e aspetta che arrivi. Ritorna i secondi attesi."""
        with self._lock:
            ora = time.monotonic()
            turno = max(ora, self._prossima)
            self._prossima = turno + self.intervallo()
        attesa = turno - ora
        if attesa > 0:
            _dormi(attesa)
        return attesa


LIMITE_AS = Limitatore()


class ClienteLimitato:
    """Client AutoScout24 che passa dal limitatore unico prima di ogni richiesta (get/head); il resto come httpx."""

    def __init__(self, client, limitatore: Limitatore | None = None):
        self._c, self._lim = client, limitatore or LIMITE_AS

    def get(self, *a, **k):
        self._lim.attendi()
        return self._c.get(*a, **k)

    def head(self, *a, **k):
        self._lim.attendi()
        return self._c.head(*a, **k)

    def __getattr__(self, nome):
        return getattr(self._c, nome)


def _client_as_limitato():
    return ClienteLimitato(_client_as())


# Annunci GIÀ TRATTATI in questo processo ma che non finiscono nel database (fuori zona, dati illeggibili): senza
# ricordarli la pagina che li contiene non risulterebbe mai "tutta nota" e ogni giro rileggerebbe tutte le pagine.
# 16/11: un annuncio in zona si segna SOLO DOPO il salvataggio (_segna_visti): se il giro cade prima, al giro dopo si
# rilegge (prima si segnava prima di salvarlo e, se il giro falliva, restava perso fino alla lettura completa).
_VISTI: dict = {"autoscout24": set(), "subito": set()}
_VISTI_MAX = 60000


def _visti(fonte: str) -> set:
    s = _VISTI.setdefault(fonte, set())
    if len(s) > _VISTI_MAX:
        s.clear()
    return s


def _segna_visti(letti: list) -> int:
    """Dopo _salva: segna come visti gli annunci trattati davvero (salvati o scartati per sempre), non quelli finiti
    in errore di salvataggio (si rileggono al giro dopo)."""
    n = 0
    for l in letti:
        ev = getattr(l, "_tr_ev", None)
        if ev and ev != "errore" and getattr(l, "source", None) in _VISTI:
            _visti(l.source).add(str(l.source_id))
            n += 1
    return n


# ------------------------------------------------------------------ telefono

def telefono_autoscout(det: dict | None) -> bool | None:
    """True se la pagina dell'annuncio AutoScout24 mostra un numero del venditore, False se no, None se non letta."""
    if not isinstance(det, dict):
        return None
    seller = det.get("seller") or {}
    if not isinstance(seller, dict):
        return None

    def pieno(v) -> bool:
        if isinstance(v, str):
            return len(re.sub(r"\D", "", v)) >= 6
        if isinstance(v, dict):
            return any(pieno(x) for k, x in v.items() if k.lower() in ("callto", "formattednumber", "number", "phone",
                                                                         "phonenumber", "value", "tel"))
        if isinstance(v, list):
            return any(pieno(x) for x in v)
        return False

    for k, v in seller.items():
        if "phone" in k.lower() or k.lower() in ("tel", "telefono", "contactnumbers"):
            if pieno(v):
                return True
    return False


def telefono_subito(item: dict | None) -> bool:
    """Subito: il venditore ha messo il numero (bottone "Chiama" di fianco a "Messaggio") → advertiser.phone pieno.
    Si usa SOLO il sì/no: il numero non si salva mai."""
    if not isinstance(item, dict):
        return False
    adv = item.get("advertiser") or {}
    ph = adv.get("phone") if isinstance(adv, dict) else None
    if isinstance(ph, bool):
        return ph
    if isinstance(ph, (int, float)):
        return ph == 1
    if not isinstance(ph, str):
        return False
    # 25/10 21:40 (prova su 8 annunci + screenshot del cliente): Subito mette advertiser.phone (una cifra, "0")
    # SOLO quando il venditore ha il numero (pulsante "Mostra numero", classe phoneSkeleton nella pagina);
    # senza numero il campo manca. Quindi: campo presente = ha il numero. Il numero non si legge mai.
    return ph.strip() != "" or ph == "0"


def salva_telefono(conn, lid: int, tel: bool | None, controllato: bool = False) -> None:
    """3/11 (studio 4, P22): stato del telefono stabile (solo sì/no, mai il numero).
    - tel None: non si sa niente di nuovo, non si tocca nulla (prima un True già noto tornava NULL a ogni rilettura);
    - tel True da una ricerca o dal testo: telefono_ok=True; telefono_at cambia solo se prima non era True;
    - controllato=True (pagina dell'annuncio aperta davvero): si scrive il risultato e telefono_at=now().
    Così telefono_at resta la data dell'ultimo controllo vero e la regola "ricontrolla dopo 12 ore" di
    telefoni_candidate funziona."""
    if tel is None:
        return
    if controllato:
        conn.execute("UPDATE listings SET telefono_ok=%s, telefono_at=now() WHERE id=%s", (bool(tel), lid))
    elif tel:
        conn.execute("UPDATE listings SET telefono_ok=true, telefono_at=now() WHERE id=%s AND telefono_ok IS NOT TRUE",
                     (lid,))


def ha_numero_testo(title: str | None, desc: str | None) -> bool:
    from ..web.scovo import RE_LANDLINE, RE_MOBILE
    t = f"{title or ''} {desc or ''}"
    return bool(RE_MOBILE.search(t) or RE_LANDLINE.search(t))


_diag = {"n": 0}
_diag_sub = {"n": 0}   # diagnostica: forma del campo telefono (cifre → 9, lettere → a), mai il numero


def _diagnostica_as(det: dict) -> None:
    """Prime pagine lette: nei log solo i NOMI dei campi del venditore (mai numeri), per controllare la lettura."""
    if _diag["n"] < 3 and isinstance(det, dict):
        _diag["n"] += 1
        s = det.get("seller") or {}
        log.warning("TR autoscout venditore: campi=%s tipo=%s telefono=%s", sorted(s)[:30] if isinstance(s, dict) else type(s).__name__,
                    (s or {}).get("type") if isinstance(s, dict) else None, telefono_autoscout(det))


# ------------------------------------------------------------------ pagine

def _as_esito(client, url: str) -> tuple[str, dict | None, str | None]:
    """3/11: ('attivo'|'scomparso'|'errore', listingDetails, motivo). Motivo della sparizione (solo con prova):
    '404' (404/410), 'reindirizzato' (rimando alla ricerca /lst), 'rimosso' ("non è più disponibile")."""
    from ..collectors.base import next_data
    try:
        r = client.get(url)
    except Exception as e:
        log.info("TR autoscout %s: %s", url, str(e)[:100])
        return "errore", None, None
    if r.status_code in (404, 410):
        return "scomparso", None, "404"
    if r.status_code != 200:
        return "errore", None, None
    if "/lst" in str(r.url) and "/annunci/" not in str(r.url):
        return "scomparso", None, "reindirizzato"      # rimandati alla ricerca: annuncio tolto
    props = ((next_data(r.text) or {}).get("props") or {}).get("pageProps") or {}
    det = props.get("listingDetails") or props.get("listing")
    if not isinstance(det, dict):
        if "non è più disponibile" in (r.text or "").lower():
            return "scomparso", None, "rimosso"
        return "errore", None, None
    _diagnostica_as(det)
    return "attivo", det, None


def _as_dettaglio(client, url: str) -> tuple[str, dict | None]:
    """('attivo'|'scomparso'|'errore', listingDetails)."""
    st, det, _ = _as_esito(client, url)
    return st, det


def _head_stato(client, url: str) -> str | None:
    """Controllo leggero (solo intestazioni, pochi byte: costa quasi niente sul proxy). None = serve la pagina intera."""
    try:
        r = client.head(url)
    except Exception:
        return None
    if r.status_code in (404, 410):
        return "scomparso"
    if r.status_code == 200:
        if "autoscout24" in url and "/lst" in str(r.url) and "/annunci/" not in str(r.url):
            return "scomparso"
        return "attivo"
    return None


_RE_ID_SUBITO = re.compile(r"(\d{6,})\.htm")


def _stesso_annuncio(url: str, finale: str) -> bool:
    """Dopo i reindirizzamenti si è ancora sulla pagina dell'annuncio? (un annuncio tolto rimanda alla ricerca)
    3/11: anche se il titolo cambia (Subito rimanda a un indirizzo con un altro testo ma lo STESSO numero
    dell'annuncio) è sempre lo stesso annuncio: prima risultava "scomparso" per errore."""
    from urllib.parse import urlparse
    a, f = urlparse(url).path.rstrip("/"), urlparse(finale).path.rstrip("/")
    if a == f or (a.split("/")[-1] and a.split("/")[-1] == f.split("/")[-1]):
        return True
    ma, mf = _RE_ID_SUBITO.search(a), _RE_ID_SUBITO.search(f)
    return bool(ma and mf and ma.group(1) == mf.group(1))


_SUBITO_TOLTO = ("annuncio non è più disponibile", "questo annuncio è scaduto", "annuncio rimosso", "annuncio scaduto",
                 "annuncio non trovato")


def subito_esito_con_prova(client, url: str, lenient: bool = False) -> tuple[str, str | None]:
    """3/11: (stato, motivo) di un annuncio Subito. 'scomparso' SOLO con una prova: 404/410 (motivo '404'), rimando a
    una pagina senza il numero dell'annuncio ('reindirizzato'), pagina senza i dati dell'annuncio e con la scritta
    "annuncio scaduto" ('scaduto': NON è una vendita) o "rimosso / non più disponibile" ('rimosso').
    Pagina con i dati dell'annuncio = 'attivo'. Il resto (errori, blocchi, pagina non riconosciuta) = 'errore'.
    lenient=True (auto del sito, giro_verifica): una pagina 200 dello stesso annuncio senza scritte di rimozione
    vale 'attivo' come prima, ma per toglierla serve la stessa prova del controllo in blocco."""
    from ..collectors.base import find_key, next_data
    try:
        r = client.get(url)
    except Exception as e:
        log.info("SCOMPARSI subito %s: %s", url, str(e)[:100])
        return "errore", None
    code = getattr(r, "status_code", None)
    if code in (404, 410):
        return "scomparso", "404"
    if code != 200:
        return "errore", None
    fin = str(getattr(r, "url", "") or url)
    m = _RE_ID_SUBITO.search(url)
    if m and m.group(1) not in fin:
        return "scomparso", "reindirizzato"      # rimandati alla ricerca o alla pagina principale
    if not m and not _stesso_annuncio(url, fin):
        return ("scomparso", "reindirizzato") if lenient else ("errore", None)
    text = getattr(r, "text", "") or ""
    try:
        data = next_data(text)
    except Exception:
        data = None
    item = find_key(data, "ad", "item") if isinstance(data, dict) else None
    if isinstance(item, dict) and (item.get("urn") or item.get("subject") or item.get("features")):
        return "attivo", None
    low = _testo_chiaro(text)          # 12/11 revisione: scritte cercate anche nel JSON escapato (\u00e8 = è)
    trovate = [k for k in _SUBITO_TOLTO if k in low]
    # 12/11 (buchi della raccolta §2.3): le pagine di Subito non hanno più __NEXT_DATA__ (i dati sono nel JSON escapato
    # di self.__next_f): ogni annuncio dava "errore" e il controllo notturno degli spariti si fermava dopo 15 pagine,
    # ogni notte (0 controllati). I dati DELL'annuncio (il suo urn "id:ad:<numero>:", con titolo e prezzo vicini)
    # valgono come prima "pagina con i dati" = attivo. Dati e scritta di rimozione insieme: nessuna decisione.
    dati = _dati_annuncio_nella_pagina(text, m.group(1) if m else None)
    if trovate and not dati:
        return "scomparso", ("scaduto" if any("scadut" in k for k in trovate) else "rimosso")
    if dati and not trovate:
        return "attivo", "pagina_app"      # contato a parte (motivo:pagina_app nella riga SCOMPARSI)
    if dati and trovate:
        return ("attivo", None) if lenient else ("errore", None)
    _diag_pagina_subito(text, m.group(1) if m else None)
    return ("attivo", None) if lenient else ("errore", None)


_RE_CAMPI_ANNUNCIO = re.compile(r'\\*"(subject|features|advertiser|urn)\\*"\s*:')
_RE_SUBJECT = re.compile(r'\\*"subject\\*"\s*:\s*\\*"[^"\\]{2,}')
_RE_PREZZO = re.compile(r'\\*"(?:/price|price)\\*"|\\*"key\\*"\s*:\s*\\*"\d{3,7}\\*"\s*,\s*\\*"value\\*"\s*:\s*\\*"[\d.]+\s*(?:€|\\u20ac)')
FINESTRA_URN = 6000            # caratteri intorno all'urn in cui devono esserci titolo e prezzo
_diag_pag_sub = {"n": 0}
_campione_app = {"n": 0}


def _testo_chiaro(text: str) -> str:
    """Minuscolo, con le sequenze del JSON escapato rese leggibili (\\u00e8 -> è, \\" -> ")."""
    t = (text or "").lower()
    for a, b in (("\\u00e8", "è"), ("\\u00e9", "é"), ("\\u00f9", "ù"), ("\\u00e0", "à"), ("\\u00ec", "ì"),
                 ("\\u00f2", "ò"), ('\\"', '"'), ("\\/", "/")):
        t = t.replace(a, b)
    return t


def _dati_annuncio_nella_pagina(text: str, numero: str | None) -> bool:
    """Pagina Subito (App Router, JSON escapato) con i dati di QUESTO annuncio: il suo urn ("id:ad:<numero>:" o
    "list:<numero>" con un delimitatore dopo il numero) e, entro FINESTRA_URN caratteri, un titolo (subject) e un
    prezzo. Senza il numero nell'indirizzo non si decide."""
    if not numero or not text:
        return False
    for mu in re.finditer(r"(?:ad:%s:|list:%s(?!\d))" % (re.escape(numero), re.escape(numero)), text):
        a = max(0, mu.start() - FINESTRA_URN)
        pezzo = text[a:mu.end() + FINESTRA_URN]
        ms, mp = _RE_SUBJECT.search(pezzo), _RE_PREZZO.search(pezzo)
        if ms and mp:
            if _campione_app["n"] < 3:              # campione: solo la FORMA, mai i contenuti
                _campione_app["n"] += 1
                log.warning("SCOMPARSI subito attivo dal criterio nuovo (pagina senza __NEXT_DATA__): byte=%d "
                            "titolo_a=%d prezzo_a=%d caratteri dall'urn", len(text), abs(a + ms.start() - mu.start()),
                            abs(a + mp.start() - mu.start()))
            return True
    return False


def _diag_pagina_subito(text: str, numero: str | None) -> None:
    """Le prime 3 pagine non riconosciute per processo: solo la FORMA (byte, marcatori), mai contenuti."""
    if _diag_pag_sub["n"] >= 3:
        return
    _diag_pag_sub["n"] += 1
    t = text or ""
    log.warning("SCOMPARSI subito pagina non riconosciuta: byte=%d next_data=%s next_f=%s numero_nell_urn=%s campi=%s",
                len(t), "__NEXT_DATA__" in t, "self.__next_f" in t, bool(numero and re.search(r"(?:ad:%s:|list:%s(?!\d))" % (numero, numero), t)),
                sorted(set(x.group(1) for x in _RE_CAMPI_ANNUNCIO.finditer(t[:2_000_000])))[:6])


def subito_stato_con_prova(client, url: str) -> str:
    """Stato di un annuncio Subito per la marcatura 'scomparso' IN BLOCCO: vedi subito_esito_con_prova."""
    return subito_esito_con_prova(client, url)[0]


def _subito_stato(client, url: str) -> str:
    # 17/10: niente più controllo "leggero" con le sole intestazioni: un annuncio tolto può rispondere 200 dopo un
    # rimando alla ricerca. Si apre sempre la pagina e si guarda dove si arriva e cosa c'è scritto.
    try:
        r = client.get(url)
    except Exception as e:
        log.info("TR subito %s: %s", url, str(e)[:100])
        return "errore"
    if r.status_code in (404, 410):
        return "scomparso"
    if r.status_code != 200:
        return "errore"
    if not _stesso_annuncio(url, str(r.url)):
        return "scomparso"
    low = r.text.lower()
    if any(k in low for k in ("annuncio non è più disponibile", "questo annuncio è scaduto", "annuncio rimosso",
                              "non è più disponibile", "annuncio scaduto", "è stato rimosso", "annuncio non trovato")):
        return "scomparso"
    return "attivo"


def _client_as():
    import httpx
    from ..collectors.autoscout import UA
    return httpx.Client(timeout=25, follow_redirects=True,
                        headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9", "Accept": "text/html"},
                        proxy=os.environ.get("SCRAPER_PROXY") or None)


def _client_subito():
    import httpx
    from ..collectors.subito import HEADERS
    return httpx.Client(timeout=25, follow_redirects=True, headers=HEADERS, proxy=os.environ.get("SCRAPER_PROXY") or None)


_FB_DIAG = [0]
_FB_VIA = ("non è più disponibile", "non e più disponibile", "this listing is no longer available", "no longer available",
           "articolo non è più disponibile", "questo annuncio non è più disponibile", "listing has been removed",
           "contenuto non disponibile", "this content isn't available", "questo contenuto non è disponibile")


def _fb_stato(client, url: str) -> str:
    """Facebook Marketplace: pagina pubblica dell'annuncio (senza account). 'scomparso' solo con prova chiara
    (404, rimando fuori dall'annuncio, scritta "non più disponibile"); pagina di accesso = 'incerto'."""
    import re as _re
    m = _re.search(r"/item/(\d+)", url or "")
    try:
        r = client.get(url)
    except Exception as e:
        log.info("TR facebook %s", str(e)[:100])
        return "errore"
    low = r.text.lower() if r.status_code == 200 else ""
    fin = str(r.url)
    if _FB_DIAG[0] < 6:
        _FB_DIAG[0] += 1
        log.warning("TR facebook diag: http=%s finale=%s og_title=%s login=%s via=%s", r.status_code,
                    _re.sub(r"\d{6,}", "<id>", fin.split("?")[0])[:80], 'property="og:title"' in low,
                    "/login" in fin or 'name="login"' in low, [k for k in _FB_VIA if k in low][:2])
    if r.status_code in (404, 410):
        return "scomparso"
    if r.status_code != 200:
        return "errore"
    if any(k in low for k in _FB_VIA):
        return "scomparso"
    if "/login" in fin or "/checkpoint" in fin:
        return "incerto"
    if m and m.group(1) not in fin:
        return "scomparso" if "/marketplace" in fin else "incerto"
    if 'property="og:title"' in low or m and m.group(1) in low:
        return "attivo"
    return "incerto"


def _client_fb():
    import httpx
    return httpx.Client(timeout=25, follow_redirects=True, proxy=os.environ.get("SCRAPER_PROXY") or None, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9", "Accept": "text/html"})


# ------------------------------------------------------------------ agente 1: verifica

def _controlla_annuncio(r, cas, csb, cfb=None, lenient: bool = True):
    """Una pagina riaperta: (riga, stato, motivo). Stesso criterio per la verifica delle auto del sito e per il
    rinfresco delle auto fuori per freschezza (giro_rinfresca, con lenient=False: serve la prova che è viva).
    17/11 (giudice): con lenient=False si confronta anche il PREZZO della pagina con price_eur salvato: prezzo diverso
    = motivo 'prezzo_cambiato:<nuovo>', prezzo non leggibile = 'errore' (non si rinfresca un'auto con un prezzo vecchio:
    un rialzo arriverebbe sul sito con il margine gonfiato). AutoScout24: pagina con i dati ma con la scritta
    "non è più disponibile" (o simili) = 'errore'."""
    time.sleep(float(os.environ.get("TR_PAUSA_S", "0.6")))
    if r["source"] == "autoscout24":
        if lenient:
            st, _det, motivo = _as_esito(cas, r["url"])   # pagina intera: un annuncio tolto può rispondere 200
            return r, st, motivo
        st, det, motivo = _as_vivo_con_prova(cas, r["url"])
        if st != "attivo":
            return r, st, motivo
        return r, st, _confronta_prezzo(r, _prezzo_as(det))
    if r["source"] == "facebook":
        return r, (_fb_stato(cfb, r["url"]) if cfb is not None else "errore"), None
    # 3/11 (studio 4 §4.4): per togliere un'auto Subito serve la stessa prova del controllo in blocco (prima
    # bastava la frase "non è più disponibile" in un punto qualsiasi della pagina, anche negli annunci simili)
    if lenient:
        st, motivo = subito_esito_con_prova(csb, r["url"], lenient=True)
        return r, st, motivo
    reg = _Registra(csb)
    st, motivo = subito_esito_con_prova(reg, r["url"], lenient=False)
    if st != "attivo":
        return r, st, (motivo or _motivo_blocco(reg)) if st == "errore" else motivo
    m = _RE_ID_SUBITO.search(r["url"] or "")
    return r, st, _confronta_prezzo(r, prezzo_subito_pagina(getattr(reg.ultima, "text", "") or "",
                                                            m.group(1) if m else None, salvato=r.get("price_eur")))


_AS_TOLTO = ("non è più disponibile", "annuncio non disponibile", "annuncio scaduto", "annuncio rimosso",
             "veicolo non è più disponibile", "is no longer available")
_RE_NEXT_DATA = re.compile(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>.*?</script>', re.S | re.I)


class _Registra:
    """Client che ricorda l'ultima risposta (per leggere il prezzo dalla stessa pagina, senza una seconda richiesta)."""
    def __init__(self, client):
        self.client, self.ultima = client, None

    def get(self, url, *a, **k):
        self.ultima = None
        self.ultima = self.client.get(url, *a, **k)
        return self.ultima


def _motivo_blocco(reg) -> str | None:
    """17/11 (giudice): 'blocco:<codice>' se l'errore è un blocco vero (403, 429 o eccezione, cioè nessuna risposta);
    None per gli altri errori (pagina non riconosciuta, scritta di rimozione...). Solo i blocchi fermano il rinfresco."""
    risposta = getattr(reg, "ultima", None)
    if risposta is None:
        return "blocco:eccezione"
    code = getattr(risposta, "status_code", None)
    return f"blocco:{code}" if code in (403, 429) else None


def _as_vivo_con_prova(cas, url: str) -> tuple[str, dict | None, str | None]:
    """AutoScout24 con la prova che l'annuncio è vivo (rinfresco e telefoni_candidate, 17/11 giudice): la pagina deve
    avere listingDetails E nessuna scritta di rimozione FUORI da __NEXT_DATA__ (nei dati JSON ci sono anche testi di
    servizio: lì una frase non è una prova). Scritta trovata = ('errore', det, 'scritta_rimozione'): nessuna modifica.
    Errore di rete, 403 o 429 = motivo 'blocco:...'."""
    reg = _Registra(cas)
    st, det, motivo = _as_esito(reg, url)
    if st == "errore":
        return st, det, motivo or _motivo_blocco(reg)
    if st != "attivo":
        return st, det, motivo
    low = _testo_chiaro(_RE_NEXT_DATA.sub(" ", getattr(reg.ultima, "text", "") or ""))
    if any(k in low for k in _AS_TOLTO):
        return "errore", det, "scritta_rimozione"
    return st, det, motivo


VARIAZIONE_MAX = 0.40       # oltre il 40% di differenza dal prezzo salvato la lettura non è creduta (prudenza)


def _variazione_plausibile(prezzo, salvato) -> bool:
    if not prezzo or not salvato:
        return True
    return abs(int(prezzo) - int(salvato)) <= VARIAZIONE_MAX * int(salvato)


def _confronta_prezzo(r, prezzo) -> str | None:
    """None se il prezzo della pagina è quello salvato; 'prezzo_cambiato:<nuovo>' se è diverso; 'prezzo_illeggibile'
    anche se la differenza supera VARIAZIONE_MAX (una lettura sbagliata scriverebbe un prezzo falso: con un prezzo più
    basso il margine verrebbe gonfiato)."""
    if not prezzo:
        return "prezzo_illeggibile"
    if r.get("price_eur") is not None and int(prezzo) == int(r["price_eur"]):
        return None
    if not _variazione_plausibile(prezzo, r.get("price_eur")):
        return "prezzo_illeggibile"
    return f"prezzo_cambiato:{int(prezzo)}"


def segna_prezzo_cambiato(conn, lid: int, nuovo: int) -> None:
    """Annuncio vivo con un altro prezzo: come nella raccolta, prezzo nuovo, evento 'prezzo' e stage 'nuovo' (la
    valutazione si rifà col prezzo nuovo; M/R e margine del prezzo vecchio non restano sul sito)."""
    conn.execute("UPDATE listings SET price_eur=%s, stage='nuovo' WHERE id=%s", (int(nuovo), lid))
    conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'prezzo',%s)", (lid, int(nuovo)))
    conn.commit()


def _prezzo_as(det) -> int | None:
    """Prezzo dalla pagina AutoScout24 (listingDetails), come collectors.autoscout.parse_item."""
    if not isinstance(det, dict):
        return None
    from ..collectors.autoscout import _num
    trk, price = det.get("tracking") or {}, det.get("price") or {}
    try:
        return _num(trk.get("price")) or _num(price.get("priceFormatted") if isinstance(price, dict) else price)
    except Exception:
        return None


# 17/11 (giudice): la feature /price in qualsiasi ordine delle chiavi: {"uri":"/price", ...altre..., "values":[...]},
# {"values":[...], "uri":"/price"} oppure "/price":{...}; anche con le virgolette escapate (\") e "\/price".
_RE_PRICE_URI = re.compile(r'\\*"uri\\*"\s*:\s*\\*"\\*/price\\*"')
_RE_PRICE_CHIAVE = re.compile(r'\\*"\\*/price\\*"\s*:\s*\{')
_RE_PRICE_KEY = re.compile(r'\\*"key\\*"\s*:\s*\\*"(\d{2,7})\\*"')
_RE_ALTRO_URN = re.compile(r"ad:(\d{5,}):")


def _oggetto_intorno(text: str, pos: int, lim: int = 3000) -> str:
    """L'oggetto JSON { ... } che contiene la posizione pos (parentesi contate, al massimo lim caratteri per parte)."""
    a, prof = None, 0
    for i in range(pos - 1, max(-1, pos - 1 - lim), -1):
        ch = text[i]
        if ch == "}":
            prof += 1
        elif ch == "{":
            if prof == 0:
                a = i
                break
            prof -= 1
    if a is None:
        return ""
    prof = 0
    for j in range(a, min(len(text), a + 2 * lim)):
        ch = text[j]
        if ch == "{":
            prof += 1
        elif ch == "}":
            prof -= 1
            if prof == 0:
                return text[a:j + 1]
    return ""


def _prezzo_feature(text: str, inizio: int, fine: int) -> tuple[int, int | None] | None:
    """La prima feature /price che comincia in [inizio, fine): (posizione, prezzo o None se non leggibile)."""
    cand = []
    m1 = _RE_PRICE_URI.search(text, inizio, fine)
    if m1:
        cand.append((m1.start(), _oggetto_intorno(text, m1.start())))
    m2 = _RE_PRICE_CHIAVE.search(text, inizio, fine)
    if m2:
        cand.append((m2.start(), _oggetto_intorno(text, m2.end())))
    if not cand:
        return None
    pos, ogg = min(cand, key=lambda x: x[0])
    chiavi = {int(x) for x in _RE_PRICE_KEY.findall(ogg)}
    return pos, (chiavi.pop() if len(chiavi) == 1 else None)


def prezzo_subito_pagina(text: str, numero: str | None, salvato: int | None = None) -> int | None:
    """Prezzo dell'annuncio Subito dalla pagina. __NEXT_DATA__: la feature /price dell'item, solo se l'urn dell'item è
    quello di QUESTO annuncio. Pagine App Router: per ogni urn dell'annuncio la feature /price più VICINA DOPO l'urn
    (entro FINESTRA_URN), purché in mezzo non ci sia l'urn di un'altra auto. Più prezzi diversi, nessuno, o (con
    salvato) una differenza oltre VARIAZIONE_MAX dal prezzo salvato = None (lettura non creduta: si resta prudenti)."""
    from ..collectors.base import find_key, next_data

    def plausibile(p):
        return p if (p and _variazione_plausibile(p, salvato)) else None
    if not text:
        return None
    try:
        data = next_data(text)
    except Exception:
        data = None
    item = find_key(data, "ad", "item") if isinstance(data, dict) else None
    if isinstance(item, dict) and numero:
        urn = str(item.get("urn") or "")
        if re.search(r"(?:ad:%s:|list:%s(?!\d))" % (re.escape(numero), re.escape(numero)), urn):
            feats = item.get("features") or []
            coppie = feats.items() if isinstance(feats, dict) else ((None, f) for f in feats)
            for chiave, f in coppie:                     # lista di feature o dizionario {"/price": {...}}
                if isinstance(f, dict) and (f.get("uri") == "/price" or chiave == "/price"):
                    vals = f.get("values") or [f]
                    try:
                        return plausibile(int(str((vals[0] or {}).get("key"))))
                    except (TypeError, ValueError, AttributeError, IndexError):
                        return None
    if not numero:
        return None
    trovati = set()
    for mu in re.finditer(r"(?:ad:%s:|list:%s(?!\d))" % (re.escape(numero), re.escape(numero)), text):
        fine = min(len(text), mu.end() + FINESTRA_URN)
        pf = _prezzo_feature(text, mu.end(), fine)
        if pf is None:
            continue
        pos, prezzo = pf
        altri = [x for x in _RE_ALTRO_URN.findall(text, mu.end(), pos) if x != numero]
        if altri:
            continue                        # la /price più vicina è di un'altra auto
        if prezzo is None:
            return None                     # feature /price non leggibile: non si indovina
        trovati.add(prezzo)
    return plausibile(trovati.pop()) if len(trovati) == 1 else None


def giro_verifica(conn, ids: list[int]) -> Counter:
    """AGENTE 1 (ogni 10 minuti): riapre SOLO gli annunci che sono sul sito e toglie quelli che non esistono più.
    Non controlla numeri di telefono (quello lo fa controlla_telefoni, una volta per auto, solo sulle auto scelte).
    Ritorna i conteggi; gli id spariti sono in stats['_spariti']."""
    from .verify import _mark
    stats: Counter = Counter()
    ids = list(ids)
    if not ids:
        return stats
    rows = conn.execute("SELECT id, source, url, price_eur, telefono_ok FROM listings WHERE id = ANY(%s) AND status='attivo' "
                        "AND source IN ('subito','autoscout24','facebook')", (ids,)).fetchall()
    cas, csb, cfb = _client_as(), _client_subito(), _client_fb()

    def check(r):
        return _controlla_annuncio(r, cas, csb, cfb, lenient=True)

    spariti = []
    with ThreadPoolExecutor(max_workers=int(os.environ.get("TR_PARALLELI", "4"))) as ex:
        for r, st, motivo in ex.map(check, rows):
            stats[f"{r['source']}:{st}"] += 1
            log.warning("VERIFICA_AUTO %s", json.dumps({"id": r["id"], "fonte": r["source"], "esito": st, "motivo": motivo}))
            if st in ("errore", "incerto"):
                continue
            _mark(conn, r["id"], st, r.get("price_eur"), motivo=motivo, sito=True)
            if st == "scomparso":
                spariti.append(r["id"])           # tolta dal sito in ogni caso
                if motivo != "scaduto":           # annuncio scaduto: non è una vendita, niente misura
                    registra_sparita(conn, r["id"])
    conn.commit()
    stats["_spariti"] = spariti            # type: ignore[assignment]
    return stats


# ------------------------------------------------------------------ agente 1 bis: rinfresco (17/11)
# Il sito mostra solo le auto controllate nelle ultime FRESCHEZZA_ORE ore (scovo.LIST_SQL: GREATEST(last_checked_at,
# last_seen_at)). La verifica qui sopra riapre SOLO le auto già nell'elenco: un'auto buona uscita dall'elenco per un
# motivo passeggero (numero non ancora letto, tetto, ricalcolo, verifica in errore) o mai entrata, se le ricerche non
# la rivedono più (Subito oltre le pagine lette, AutoScout fuori dai giri), "scade" dopo 48 ore anche se l'annuncio è
# vivo e non torna più. Il rinfresco riapre poche pagine per giro, scelte tra le auto approfondite che sono o
# sarebbero sul sito, PRIMA che scadano. La regola delle 48 ore non cambia: un'auto torna "fresca" solo con la prova
# che l'annuncio è vivo (pagina con i dati dell'annuncio); senza prova (errore, pagina non riconosciuta) niente.
# Le auto vendute escono con la stessa prova della verifica (404, rimando, "scaduto/rimosso").

FRESCHEZZA_ORE = int(os.environ.get("FRESCHEZZA_ORE", "48"))     # la stessa di scovo.LIST_SQL: NON si cambia qui
_rinfresco_tentati: dict[int, float] = {}       # id -> ultimo tentativo senza esito (errore): si riprova più tardi
_rinfresco_tot: Counter = Counter()             # totali da avvio (riga FRESCHEZZA dei log)
_freschezza_contata = {"t": 0.0, "ultimo": None}
_rinfresco_pausa = {"fino": 0.0}

# (unione notte-09 + notte-02): il taglio del motivo e i motivi dell'elenco sono quelli di scovo (una sola copia)
from ..web.scovo import MOTIVI_ELENCO, motivo_stadio_sql  # noqa: E402

# stessa base di scovo.LIST_SQL, senza la condizione di freschezza
_BASE_FRESCHEZZA = ("l.stage='approfondito' AND l.status='attivo' AND " + motivo_stadio_sql("l.stage_reason")
                    + " IN (" + ",".join(f"'{m}'" for m in MOTIVI_ELENCO) + ")")

# 17/11 (giudice): prezzo cambiato (o annuncio riapparso) DOPO l'ultima valutazione: M/R e margine sono del prezzo
# vecchio, quindi niente rinfresco finché la valutazione non si rifà. Ultima valutazione = valutazioni_policy.computed_at,
# senza = valuations.created_at (alias v: l'ultima valuations dell'auto). Più prudente di scovo.PREZZO_DOPO_VALUTAZIONE_SQL
# di notte-09 (lì serve anche il prezzo diverso da quello valutato): qui basta l'evento, e vale anche 'riapparso'.
# (unione) Nome diverso da quello di scovo: questa vale SOLO per il rinfresco (RINFRESCO_SQL, telefoni_candidate);
# il sito (LIST_SQL, regola "valutata") usa scovo.PREZZO_DOPO_VALUTAZIONE_SQL.
PREZZO_DOPO_VALUTAZIONE_RINFRESCO_SQL = """EXISTS (SELECT 1 FROM price_events pe WHERE pe.listing_id=l.id
         AND pe.event IN ('prezzo','riapparso')
         AND pe.at > COALESCE((SELECT max(vp.computed_at) FROM valutazioni_policy vp WHERE vp.listing_id=l.id),
                              v.created_at))"""

RINFRESCO_SQL = """
WITH vecchie AS (
  SELECT l.id, l.source, l.url, l.price_eur, l.telefono_ok, upper(coalesce(l.province, '')) AS prov,
         EXTRACT(EPOCH FROM now() - GREATEST(l.last_checked_at, l.last_seen_at)) / 3600.0 AS ore
  FROM listings l
  JOIN LATERAL (SELECT created_at FROM valuations WHERE listing_id=l.id ORDER BY created_at DESC LIMIT 1) v ON true
  WHERE """ + _BASE_FRESCHEZZA + """ AND l.source IN ('subito','autoscout24')
    AND GREATEST(l.last_checked_at, l.last_seen_at) < now() - %(dopo)s * interval '1 hour'
    AND GREATEST(l.last_checked_at, l.last_seen_at) > now() - %(fino)s * interval '1 hour'
    AND NOT """ + PREZZO_DOPO_VALUTAZIONE_RINFRESCO_SQL + """
  ORDER BY GREATEST(l.last_checked_at, l.last_seen_at) DESC
  LIMIT %(quante)s
)
SELECT vc.id, vc.source, vc.url, vc.price_eur, vc.telefono_ok, vc.prov, vc.ore, c.categoria, c.margine,
       EXISTS (SELECT 1 FROM top_auto ta WHERE ta.listing_id = vc.id) AS top
FROM vecchie vc
LEFT JOIN LATERAL (SELECT vp.categoria, vp.margine FROM valutazioni_policy vp WHERE vp.listing_id = vc.id
                   ORDER BY vp.computed_at DESC LIMIT 1) c ON true
"""

FRESCHEZZA_SQL = """
WITH base AS (
  SELECT l.id, upper(coalesce(l.province, '')) AS prov, l.telefono_ok,
         EXTRACT(EPOCH FROM now() - GREATEST(l.last_checked_at, l.last_seen_at)) / 3600.0 AS ore
  FROM listings l
  WHERE """ + _BASE_FRESCHEZZA + """
    AND GREATEST(l.last_checked_at, l.last_seen_at) > now() - %(giorni)s * interval '1 day'
), vecchie AS (
  SELECT b.*, c.categoria, EXISTS (SELECT 1 FROM top_auto ta WHERE ta.listing_id = b.id) AS top
  FROM base b
  LEFT JOIN LATERAL (SELECT vp.categoria FROM valutazioni_policy vp WHERE vp.listing_id = b.id
                     ORDER BY vp.computed_at DESC LIMIT 1) c ON true
  WHERE b.ore >= %(dopo)s
)
SELECT (SELECT count(*) FROM base WHERE ore < %(fresc)s) AS fresche,
       count(*) FILTER (WHERE ore < %(fresc)s) AS in_scadenza,
       count(*) FILTER (WHERE ore >= %(fresc)s) AS fuori_freschezza,
       count(*) FILTER (WHERE ore >= %(fresc)s AND ore < %(fino)s) AS fuori_entro_finestra,
       count(*) FILTER (WHERE ore < %(fresc)s AND (categoria = ANY(%(cat)s) OR top)) AS buone_in_scadenza,
       count(*) FILTER (WHERE ore >= %(fresc)s AND (categoria = ANY(%(cat)s) OR top)) AS buone_fuori,
       count(*) FILTER (WHERE ore >= %(fresc)s AND ore < %(fino)s AND (categoria = ANY(%(cat)s) OR top)
                        AND prov = ANY(%(zona)s) AND telefono_ok IS NOT FALSE) AS buone_fuori_finestra_zona,
       count(*) FILTER (WHERE ore >= %(fresc)s AND categoria = 'opportunita') AS opportunita_fuori,
       count(*) FILTER (WHERE ore >= %(fresc)s AND categoria = 'candidato') AS candidato_fuori
FROM vecchie
"""


def _rinfresco_param() -> dict:
    anticipo = float(os.environ.get("RINFRESCA_ANTICIPO_ORE", "12"))
    return {"fresc": FRESCHEZZA_ORE, "dopo": max(1.0, FRESCHEZZA_ORE - anticipo),
            "fino": float(os.environ.get("RINFRESCA_FINO_GIORNI", "7")) * 24,
            "max": int(os.environ.get("RINFRESCA_MAX", "40")),
            "riprova_s": float(os.environ.get("RINFRESCA_RIPROVA_MIN", "120")) * 60}


def _categorie_sito() -> list[str]:
    return sorted(set(os.environ.get("POLICY_CATEGORIE_SITO", "opportunita,candidato").split(",")))


def _zona() -> set:
    """Province che passano il filtro zona del SITO (sito_regole.r_zona): Milano 100 km, Torino, Ravenna."""
    try:
        from ..web import sito_regole as SR
        return set(SR.PROVINCE_100KM) | set(SR.RAVENNA) | set(SR.TORINO)
    except Exception:
        return set()


def conta_freschezza(conn) -> dict:
    """Quante auto approfondite (stage_reason opportunita/da_verificare/incidentata, attive) stanno per uscire o sono
    già uscite dal sito SOLO per la freschezza. 'buone' = categoria del sito (opportunita/candidato) o tra le top.
    Finestra: ultimi 30 giorni. Mai un errore (dict vuoto)."""
    p = _rinfresco_param()
    try:
        r = conn.execute(FRESCHEZZA_SQL, {"giorni": 30, "dopo": p["dopo"], "fresc": p["fresc"], "fino": p["fino"],
                                          "cat": _categorie_sito(), "zona": sorted(_zona())}).fetchone()
        return {k: int(v or 0) for k, v in dict(r or {}).items()}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.info("FRESCHEZZA non contata: %s", str(e)[:200])
        return {}


def ordina_rinfresco(righe: list[dict], sul_sito: set, zona: set, adesso: float | None = None,
                     tentati: dict | None = None, riprova_s: float = 7200, fresc: float = 48) -> list[dict]:
    """Ordine di priorità (solo dati, testabile): 1) auto che erano sul sito di recente, 2) buone (categoria del sito
    o top) della zona, 3) altre buone; le altre auto e le buone senza numero (telefono_ok false) non si riaprono. Dentro ogni gruppo prima quelle
    ANCORA fresche più vicine alla scadenza (restano sul sito senza buchi), poi le scadute più recenti (le più
    probabilmente vive); a parità il margine più alto. Escluse quelle già tentate senza esito da meno di riprova_s."""
    cat = set(_categorie_sito())
    adesso = time.time() if adesso is None else adesso
    tentati = _rinfresco_tentati if tentati is None else tentati
    out = []
    for r in righe:
        if adesso - tentati.get(int(r["id"]), -1e18) < riprova_s:
            continue
        buona = (r.get("categoria") in cat) or bool(r.get("top"))
        recente = int(r["id"]) in sul_sito
        if not (buona or recente):
            continue
        if not recente and r.get("telefono_ok") is False:
            continue                            # senza numero non va sul sito (telefono obbligatorio)
        gruppo = 0 if recente else (1 if r.get("prov") in zona else 2)
        ore = float(r.get("ore") or 0)
        scaduta = ore >= fresc
        out.append(((gruppo, scaduta, ore if scaduta else -ore, -(r.get("margine") or 0)), r))
    out.sort(key=lambda x: x[0])
    return [r for _k, r in out]


def giro_rinfresca(conn, sul_sito=()) -> Counter:
    """Rinfresco mirato (dopo ogni verifica, stesso ritmo): riapre al massimo RINFRESCA_MAX (40) annunci tra le auto
    approfondite controllate da più di FRESCHEZZA_ORE − RINFRESCA_ANTICIPO_ORE ore (36) e da meno di
    RINFRESCA_FINO_GIORNI giorni (7), nell'ordine di ordina_rinfresco. 'attivo' con prova = last_checked_at adesso
    (l'auto resta o torna sull'elenco al prossimo calcolo, dove passa TUTTE le regole del sito come sempre);
    'scomparso' con prova = tolta come nella verifica; errore = nessuna modifica, si riprova tra 2 ore.
    Subito con lenient=False: una pagina non riconosciuta NON rinfresca. Gli id tornati vivi in stats['_tornati'].
    Ogni ora (RINFRESCA_CONTA_MIN) la riga FRESCHEZZA con i conteggi di conta_freschezza."""
    from .verify import _mark
    stats: Counter = Counter()
    if os.environ.get("RINFRESCA", "1") != "1":
        return stats
    p = _rinfresco_param()
    if time.time() - _freschezza_contata["t"] >= float(os.environ.get("RINFRESCA_CONTA_MIN", "60")) * 60:
        _freschezza_contata.update(t=time.time(), ultimo=conta_freschezza(conn))
        log.warning("FRESCHEZZA %s", json.dumps({"ore": FRESCHEZZA_ORE, "anticipo_da_ore": p["dopo"],
                                                  "conteggi": _freschezza_contata["ultimo"],
                                                  "rinfresco_da_avvio": dict(_rinfresco_tot)}, ensure_ascii=False))
    if p["max"] <= 0:
        return stats
    if time.time() < _rinfresco_pausa["fino"]:
        stats["in_pausa"] = 1                  # troppi errori di fila nell'ultimo giro (blocco 403/429?)
        stats["_tornati"] = []             # type: ignore[assignment]
        return stats
    righe = conn.execute(RINFRESCO_SQL, {"dopo": p["dopo"], "fino": p["fino"],
                                         "quante": int(os.environ.get("RINFRESCA_POOL", "5000"))}).fetchall()
    adesso = time.time()
    for k in [k for k, t in _rinfresco_tentati.items() if adesso - t > p["riprova_s"]]:
        _rinfresco_tentati.pop(k, None)                 # memoria piccola
    scelte = ordina_rinfresco([dict(r) for r in righe], {int(i) for i in sul_sito or ()}, _zona(), adesso,
                              riprova_s=p["riprova_s"], fresc=p["fresc"])[:p["max"]]
    stats["candidate"] = len(righe)
    stats["scelte"] = len(scelte)
    if not scelte:
        stats["_tornati"] = []             # type: ignore[assignment]
        return stats
    cas = _client_as() if any(r["source"] == "autoscout24" for r in scelte) else None
    csb = _client_subito() if any(r["source"] == "subito" for r in scelte) else None
    tornati, spariti, cambiati = [], [], []
    sul_sito = {int(i) for i in sul_sito or ()}
    stop_n = int(os.environ.get("RINFRESCA_STOP_ERRORI", "8"))
    di_fila = {"n": 0}

    def uno(r):
        if di_fila["n"] >= stop_n:
            return r, "saltato", None           # giro fermato: il sito risponde solo errori
        out = _controlla_annuncio(r, cas, csb, None, lenient=False)
        # 17/11 (giudice): contano solo i blocchi veri (403, 429, eccezioni), non la scritta di rimozione o una
        # pagina non riconosciuta
        bloccato = out[1] == "errore" and str(out[2] or "").startswith("blocco")
        di_fila["n"] = di_fila["n"] + 1 if bloccato else 0
        return out

    with ThreadPoolExecutor(max_workers=int(os.environ.get("TR_PARALLELI", "4"))) as ex:
        for r, st, motivo in ex.map(uno, scelte):
            scaduta = float(r.get("ore") or 0) >= p["fresc"]
            if st == "attivo" and motivo == "prezzo_illeggibile":
                st = "errore"                   # senza il prezzo non si rinfresca (margine non verificabile)
                stats["prezzo_illeggibile"] += 1
            stats[f"{r['source']}:{st}"] += 1
            stats[f"{'scadute' if scaduta else 'in_scadenza'}:{st}"] += 1
            if st == "saltato":
                continue
            if st not in ("attivo", "scomparso"):
                _rinfresco_tentati[int(r["id"])] = time.time()
                continue
            if st == "attivo" and (motivo or "").startswith("prezzo_cambiato:"):
                # annuncio vivo ma con un altro prezzo: NON si rinfresca (M/R e margine sono del prezzo vecchio);
                # come nella raccolta: prezzo nuovo, evento 'prezzo' e stage 'nuovo' = rivalutazione
                segna_prezzo_cambiato(conn, r["id"], int(motivo.split(":", 1)[1]))
                stats["prezzo_cambiato"] += 1
                cambiati.append(r["id"])            # se è sull'elenco esce subito (non aspetta il ricalcolo)
                continue
            era_sul_sito = int(r["id"]) in sul_sito
            _mark(conn, r["id"], st, r.get("price_eur"), motivo=motivo, sito=(st == "scomparso" and era_sul_sito))
            if st == "scomparso" and era_sul_sito and motivo != "scaduto":
                registra_sparita(conn, r["id"])     # come nella verifica: era sul sito negli ultimi 7 giorni
            if st == "attivo":
                if scaduta:
                    tornati.append(r["id"])         # era fuori dal sito solo per la freschezza
                else:
                    stats["tenute_fresche"] += 1    # sarebbe uscita entro FRESCHEZZA_ORE − ore
            else:
                spariti.append(r["id"])
    conn.commit()
    if di_fila["n"] >= stop_n:
        _rinfresco_pausa["fino"] = time.time() + float(os.environ.get("RINFRESCA_PAUSA_MIN", "30")) * 60
        stats["fermato_errori"] = 1
        log.warning("TR RINFRESCO %d errori di fila (blocco?): pausa di %s minuti", di_fila["n"],
                    os.environ.get("RINFRESCA_PAUSA_MIN", "30"))
    for k, v in stats.items():
        if k != "candidate":
            _rinfresco_tot[k] += v
    stats["_tornati"] = tornati            # type: ignore[assignment]
    stats["_spariti"] = spariti            # type: ignore[assignment]
    stats["_prezzo_cambiato"] = cambiati   # type: ignore[assignment]
    return stats


def controlla_telefoni(conn, limite: int | None = None) -> Counter:
    """IMBUTO, ultimo passo: SOLO le auto AutoScout24 appena scelte (approfondite) e mai controllate: si apre la pagina
    una volta per auto e si salva sì/no (mai il numero). Non è un controllo periodico: ogni auto si guarda una volta."""
    stats: Counter = Counter()
    lim = limite or int(os.environ.get("TR_AS_TELEFONO_MAX", "400"))
    rows = conn.execute(
        "SELECT id, url FROM listings WHERE source='autoscout24' AND status='attivo' AND stage='approfondito' "
        "AND telefono_ok IS NULL AND (telefono_at IS NULL OR telefono_at < now() - interval '1 day') "
        "ORDER BY telefono_at NULLS FIRST, deep_at DESC NULLS LAST LIMIT %s", (lim,)).fetchall()
    if not rows:
        return stats
    cas = _client_as()

    def check(r):
        time.sleep(float(os.environ.get("TR_PAUSA_S", "0.6")))
        st, det = _as_dettaglio(cas, r["url"])
        return r, st, telefono_autoscout(det) if st == "attivo" else None

    with ThreadPoolExecutor(max_workers=int(os.environ.get("TR_PARALLELI", "4"))) as ex:
        for r, st, tel in ex.map(check, rows):
            if st == "errore":
                stats["errore"] += 1
                continue
            conn.execute("UPDATE listings SET telefono_ok=%s, telefono_at=now() WHERE id=%s", (tel, r["id"]))
            stats[f"telefono_{'si' if tel else ('no' if tel is False else 'non_leggibile')}"] += 1
    conn.commit()
    return stats


_diag_tel_sub = {"n": 0}


def _maschera(v) -> str:
    import json as _json
    t = v if isinstance(v, str) else _json.dumps(v, default=str)
    return re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", t))[:60]


def diag_subito_telefono(sub, urn, url) -> None:
    """Diagnostica (25/10): dove Subito dice se il venditore ha il numero (bottone Chiama). Nei log solo i NOMI dei
    campi e la FORMA dei valori (cifre → 9, lettere → a): il numero non si legge e non si salva mai."""
    from ..collectors.subito import API_HEADERS
    prove = []
    for ep in (f"https://hades.subito.it/v1/ads/{urn}", f"https://hades.subito.it/v1/items/{urn}"):
        try:
            r = sub.client.get(ep, headers=API_HEADERS)
            campi = []

            def scan(o, p=""):
                if len(campi) > 15:
                    return
                if isinstance(o, dict):
                    for k, v in o.items():
                        q = f"{p}.{k}" if p else k
                        if any(w in k.lower() for w in ("phone", "tel", "call", "contact", "show")):
                            campi.append(f"{q}={_maschera(v) if not isinstance(v, (dict, list)) else type(v).__name__}")
                        scan(v, q)
                elif isinstance(o, list):
                    for i, v in enumerate(o[:3]):
                        scan(v, f"{p}[{i}]")
            try:
                scan(r.json())
            except Exception:
                pass
            prove.append(f"{ep.split('/v1/')[1].split('/')[0]}: http={r.status_code} campi={campi}")
        except Exception as e:
            prove.append(f"{ep}: errore {str(e)[:60]}")
    try:
        h = sub._get(url).text
        ctx = []
        for m in re.finditer(r"(?i)(showphone|phone|telefono|chiama)", h):
            seg = h[max(0, m.start() - 80):m.end() + 60]
            seg = re.sub(r"\d", "9", seg)
            seg = re.sub(r'"[^"]{25,}"', '"…"', seg)
            ctx.append(seg.replace("\n", " "))
            if len(ctx) >= 6:
                break
        prove.append(f"pagina contesti={ctx}")
    except Exception as e:
        prove.append(f"pagina errore {str(e)[:60]}")
    log.warning("DIAG_SUBITO_TEL %s", prove)


_RE_MARK_ATTR = re.compile(r'(class|aria-label|data-testid|id|name|title|alt|href)="([^"]{0,120}?(?:phone|Phone|PHONE|tel:|call|Call|chiama|Chiama|numero|Numero)[^"]{0,60}?)"')
_RE_MARK_JSON = re.compile(r'\\*"(\w{0,40}(?:phone|Phone)\w{0,40})\\*"\s*:\s*(true|false|null|\\*"[^"\\]{0,3}|\d+|\{|\[)')


_PAROLE_UI = {"mostra", "numero", "telefono", "chiama", "chiamare", "venditore", "contatta", "contattare", "il", "di",
              "del", "al", "phone", "call", "show", "number", "icon", "button", "telefonico", "visualizza", "vedi"}


def marcatori_telefono_subito(html: str) -> list[str]:
    """Solo i NOMI tecnici (classi, aria-label, chiavi JSON) che parlano di telefono, con le cifre → 9 e senza
    testi liberi: servono a trovare il segno grafico del telefono. Mai numeri o nomi del venditore."""
    out = set()
    for m in _RE_MARK_ATTR.finditer(html or ""):
        a, v = m.group(1), m.group(2)
        if v.startswith("tel:"):
            out.add("href=tel:")
            continue
        if a in ("aria-label", "title", "alt", "name", "href"):
            # testo libero: restano solo parole tecniche/di interfaccia, il resto (es. nomi) diventa "_"
            v = " ".join(w if w.lower().strip(".,:") in _PAROLE_UI else "_" for w in v.split())
        out.add(f"{a}:" + re.sub(r"\d", "9", v)[:60])
    for m in _RE_MARK_JSON.finditer(html or ""):
        val = m.group(2)
        val = "s" if val.startswith(("\\", '"')) else val
        out.add(f"json:{m.group(1)}={val}")
    return sorted(out)[:40]


def diag_icona_telefono(conn, n: int = 20) -> None:
    """25/10 (cliente: "il software deve entrare nell'annuncio e vedere lo stemma del telefono"): apre N annunci
    Subito di privati e scrive nei log quali segni tecnici del telefono ci sono in ciascuno, per capire quale
    segno c'è SOLO negli annunci con il numero. Nei log: id dell'annuncio, se il numero è scritto nel testo (sì/no)
    e i nomi tecnici. Mai il numero, mai il nome del venditore."""
    from ..collectors.subito import SubitoCollector
    sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    rows = conn.execute("SELECT id, url, title, description FROM listings WHERE source='subito' AND status='attivo' "
                        "AND COALESCE(seller_type,'privato') <> 'commerciante' ORDER BY first_seen_at DESC LIMIT %s",
                        (n,)).fetchall()
    from collections import Counter as _C
    tutti = _C()
    for r in rows:
        time.sleep(1.5)
        try:
            resp = sub._get(r["url"])
            html = resp.text if resp.status_code == 200 else ""
        except Exception as e:
            log.warning("ICONA_TEL %s errore %s", r["id"], str(e)[:80])
            continue
        mk = marcatori_telefono_subito(html)
        tutti.update(mk)
        log.warning("ICONA_TEL %s http=%s byte=%s numero_nel_testo=%s segni=%s", r["id"], resp.status_code, len(html),
                    ha_numero_testo(r["title"], r["description"]), mk)
    log.warning("ICONA_TEL_RIASSUNTO pagine=%s segni=%s", len(rows), dict(tutti.most_common(40)))


def diag_indicatore_subito(n: int = 4) -> None:
    """25/10 (cliente, screenshot: "Mostra numero" visibile anche senza accesso): verifica se l'indicatore della
    ricerca Subito (advertiser.phone = "0" oppure assente) dice chi ha il numero. Scrive nei log il LINK di n annunci
    con indicatore "0" e n senza indicatore, con i segni tecnici della pagina, così si confrontano con quello che
    si vede aprendo l'annuncio. Nei log: solo link, indicatore e nomi tecnici. Mai numeri o nomi."""
    from ..collectors.subito import API_HEADERS, API_SEARCH, SubitoCollector
    col = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    r = col.client.get(API_SEARCH, headers=API_HEADERS, params={"c": 2, "r": 4, "t": "s", "lim": 100, "start": 0,
                                                                 "sort": "datedesc"})
    ads = (r.json() or {}).get("ads") or []
    con, senza = [], []
    for a in ads:
        adv = a.get("advertiser") or {}
        if adv.get("company"):
            continue
        v = adv.get("phone") if isinstance(adv, dict) else None
        url = (a.get("urls") or {}).get("default")
        if not url:
            continue
        (con if v is not None else senza).append((url, "nessuno" if v is None else re.sub(r"\d", "9", str(v))[:3],
                                                  ha_numero_testo(a.get("subject"), a.get("body"))))
    for url, ind, testo in con[:n] + senza[:n]:
        time.sleep(1.5)
        try:
            h = col._get(url).text
        except Exception:
            h = ""
        low = h.lower()
        log.warning("INDICATORE_TEL indicatore=%s numero_nel_testo=%s mostra_numero=%s segni=%s link=%s", ind, testo,
                    "mostra numero" in low, marcatori_telefono_subito(h)[:15], url)
    log.warning("INDICATORE_TEL_CONTI privati=%s con_indicatore=%s senza=%s", len(con) + len(senza), len(con), len(senza))


PROVINCE_100KM = ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV", "CR", "NO", "PC", "AL", "VC", "BI", "VB"]
FASCE_SUBITO = [(0, 1500), (1500, 2500), (2500, 3500), (3500, 5000), (5000, 6500), (6500, 8000), (8000, 10000),
                (10000, 13000), (13000, 17000), (17000, 22000), (22000, 30000), (30000, 45000), (45000, 80000)]


def subito_completo(conn, minuti: float | None = None, col=None, basta=None) -> dict:
    """25/10 (cliente: "leggi TUTTE le auto di Subito con le nostre regole nel raggio dei 100 km"): rilegge Subito
    provincia per provincia e fascia di prezzo per fascia (così non c'è il tetto delle 10.000 auto più recenti),
    salva gli annunci nuovi o cambiati e segna chi ha il numero (sì/no, mai il numero). La valutazione la fa poi
    il ricalcolo del cron. Le province sono quelle entro circa 100 km da Milano (SUBITO_PROVINCE per cambiarle).
    3/11 (studio 4, P7/P8): si usa SubitoCollector._deep, che DIVIDE le fasce piene (prima oltre 40 pagine il resto
    si perdeva senza avviso), con i passi già fatti (PASSI_JOB) per riprendere dopo un riavvio. Solo l'interfaccia
    di ricerca (gratuita), mai il Web Unlocker. È anche la rilettura settimanale del cron (rilettura_subito): gli
    annunci ancora in vendita tornano "visti" e restano confronti validi. `minuti`: tempo massimo per giro (il resto
    al giro dopo, dai passi). `basta`: funzione senza argomenti; se ritorna True il giro si ferma come per il tempo
    (12/11: dentro il sito, quando la memoria è alta)."""
    from ..collectors.subito import REGION_CODES, SubitoCollector
    col = col or SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    province = [p.strip().upper() for p in os.environ.get("SUBITO_PROVINCE", ",".join(PROVINCE_100KM)).split(",") if p.strip()]
    pagine = int(os.environ.get("SUBITO_COMPLETO_PAGINE", "40"))
    st = Counter()
    t0 = time.time()
    fine = t0 + minuti * 60 if minuti else None
    pacco: list = []

    def salva():
        if pacco:
            try:
                _salva(conn, pacco, st)
                conn.commit()
            except Exception as e:
                conn.rollback()
                st["errore_salva"] += 1
                log.info("SUBITO_COMPLETO salva: %s", str(e)[:150])
            pacco.clear()

    for reg, provs in REGIONI.items():
        voluti = set(provs) & set(province)
        if not voluti:
            continue
        code = REGION_CODES.get(reg) or _codici_regione.get(reg) or col._trova_regione(set(provs), 100)
        if not code:
            st["regione_non_trovata"] += 1
            continue
        _codici_regione[reg] = REGION_CODES[reg] = code
        gen = col._deep({"price_bands": FASCE_SUBITO, "band_pages": pagine}, voluti, code, 100)
        try:
            for l in gen:
                st["con_numero" if telefono_subito(l.raw if isinstance(l.raw, dict) else None) else "senza_numero"] += 1
                pacco.append(l)
                if len(pacco) >= 150:              # in memoria mai più di un pacchetto (512 MB)
                    salva()
                if fine and time.time() > fine:
                    st["fermato_tempo"] += 1
                    break
                if basta is not None and basta():  # deve essere economica (subito_completo_notte: un sì/no già deciso)
                    salva()                        # niente di letto va perso
                    st["fermato_tempo"] += 1
                    st["fermato_memoria"] += 1
                    break
        except Exception as e:
            st["errore"] += 1
            log.warning("SUBITO_COMPLETO %s: %s", reg, str(e)[:150])
        finally:
            gen.close()                            # fascia a metà: non segnata fatta, si riprende da lì
        salva()
        log.warning("SUBITO_COMPLETO %s fatto in %.0fs: %s", reg, time.time() - t0, dict(st))
        if st["fermato_tempo"]:
            break
    st["letti"] = sum(v for k, v in st.items() if k.startswith("letti:"))
    log.warning("SUBITO_COMPLETO_FINE in %.0fs: %s", time.time() - t0, dict(st))
    return dict(st)


def subito_completo_notte(conn, giorno: str, basta=None) -> dict:
    """12/11: lettura completa notturna dentro il sito, con i passi del giorno (job "subito_completo:"+giorno, come
    rilettura_subito, ma SOLO per questo thread: passi.locale, così gli altri giri del sito non lo vedono). Il freno
    `basta()` (facoltativo) si chiede solo a fascia chiusa, subito dopo che il passo è segnato: se dice sì, il giro si
    ferma alla fascia dopo e il prossimo riprende da lì. Finita tutta la lettura, i passi si cancellano."""
    from . import passi
    job = "subito_completo:" + giorno
    fermo = {"si": False}

    def dopo_segna(chiave):
        if basta is not None and not fermo["si"] and basta():
            fermo["si"] = True
    with passi.locale(job, dopo_segna=dopo_segna):
        st = subito_completo(conn, basta=(lambda: fermo["si"]) if basta is not None else None)
    if not st.get("fermato_memoria"):
        try:
            passi.finito(job)
        except Exception as e:
            log.warning("SUBITO_COMPLETO passi non cancellati: %s", str(e)[:150])
    return st


def rilettura_subito(minuti: float | None = None) -> dict:
    """3/11 (studio 4 §4.2): rilettura settimanale di tutti gli annunci Subito della zona, nel CRON (mai nel sito).
    A pezzi: ogni giro al massimo RS_MINUTI minuti, i passi fatti restano (PASSI_JOB per settimana); il lavoro
    risulta finito (job_runs ok) solo quando tutte le fasce sono state lette. Con il database oltre CE_DB_MAX_GB
    non parte."""
    from ..db import connect, db_pieno, log_job
    from . import passi
    settimana = datetime.now(timezone.utc).strftime("%G-%V")
    job = f"rilettura_subito:{settimana}"
    vecchio = os.environ.get("PASSI_JOB")
    os.environ["PASSI_JOB"] = job
    passi._cache = None
    try:
        with connect() as conn:
            if db_pieno(conn):
                log.warning("RILETTURA_SUBITO saltata: database oltre CE_DB_MAX_GB")
                return {"saltata": "db_pieno"}
            fin = log_job(conn, "rilettura_subito")
            st = subito_completo(conn, minuti=float(os.environ.get("RS_MINUTI", "90")) if minuti is None else minuti)
            completa = not st.get("fermato_tempo") and not st.get("errore")
            fin(completa, st)
        if completa:
            passi.finito(job)
        return st
    finally:
        if vecchio is None:
            os.environ.pop("PASSI_JOB", None)
        else:
            os.environ["PASSI_JOB"] = vecchio
        passi._cache = None


def telefoni_candidate(conn, ids) -> Counter:
    """25/10 (cliente: "ci servono più auto, trova un'altra soluzione"): le auto che sarebbero sul sito ma sono state
    tolte solo perché il numero non è mai stato controllato (AutoScout24 fuori dagli "approfonditi", Subito vecchi
    oltre le pagine del giro giornaliero). Si apre la pagina una volta e si salva sì/no (mai il numero).
    Ogni auto al massimo una volta ogni TEL_CAND_ORE ore (12)."""
    from . import telefono_nascosto as TN
    stats: Counter = Counter()
    ids = [int(i) for i in ids][:5000]
    if not ids:
        return stats
    # 13/11 (piu-auto): Subito anche dall'interfaccia di ricerca (telefono_nascosto), solo per le auto PRIORITARIE
    # (margine già sopra il minimo); prima le prioritarie, poi le altre
    tutti = set(ids)
    prio_set = TN.PRIORITARI if TN.attivo() else frozenset()      # una fotografia per tutto il giro
    prio = sorted(i for i in prio_set if i in tutti)
    TN.inizio_giro()
    if prio:
        TN.leggi_conto(conn)
    rows = conn.execute(
        "SELECT l.id, l.source, l.source_id, l.url, l.make, l.model, l.price_eur, l.province, "
        + PREZZO_DOPO_VALUTAZIONE_RINFRESCO_SQL + " AS prezzo_dopo_valutazione FROM listings l "
        "LEFT JOIN LATERAL (SELECT created_at FROM valuations WHERE listing_id=l.id ORDER BY created_at DESC LIMIT 1) v "
        "ON true WHERE l.id = ANY(%s) "
        "AND l.status='attivo' AND (l.source = 'autoscout24' OR (l.source = 'subito' AND (%s OR l.id = ANY(%s)))) "
        "AND l.telefono_ok IS NOT TRUE AND (l.telefono_at IS NULL OR l.telefono_at < now() - make_interval(hours => %s)) "
        "ORDER BY (l.id = ANY(%s)) DESC, l.telefono_at NULLS FIRST LIMIT %s",
        (ids, os.environ.get("TEL_CAND_SUBITO", "0") == "1",       # Subito: la pagina pubblica dice sempre showPhone=false
         prio, int(os.environ.get("TEL_CAND_ORE", "12")), prio, int(os.environ.get("TEL_CAND_MAX", "150")))).fetchall()
    if not rows:
        return stats
    cas = _client_as() if any(r["source"] == "autoscout24" for r in rows) else None
    sub = None
    api = None
    for r in rows:
        time.sleep(float(os.environ.get("TR_PAUSA_S", "0.6")))
        tel = None
        try:
            if r["source"] == "autoscout24":
                # 17/11 (giudice): lo stesso controllo del rinfresco (_controlla_annuncio): listingDetails E nessuna
                # scritta di rimozione fuori da __NEXT_DATA__
                st, det, motivo = _as_vivo_con_prova(cas, r["url"])
                if st == "errore" and motivo == "scritta_rimozione":
                    # dati e scritta insieme: nessuna decisione (né rinfresco né numero), si riprova tra ~2 ore
                    stats["autoscout24:scritta_rimozione"] += 1
                    rip = max(0, int(os.environ.get("TEL_CAND_ORE", "12")) - int(os.environ.get("TEL_CAND_RIPROVA_ORE", "2")))
                    conn.execute("UPDATE listings SET telefono_at=now() - make_interval(hours => %s) WHERE id=%s",
                                 (rip, r["id"]))
                    conn.commit()
                    continue
                if st == "errore":
                    stats["errore"] += 1
                    continue
                if st == "scomparso":
                    # 13/11: erano contati "non leggibile" e riprovati ogni 2 ore; sono annunci spariti (404, rimando
                    # alla ricerca, "non più disponibile"): si segnano come fa la verifica delle auto del sito
                    from .verify import _mark
                    _mark(conn, r["id"], "scomparso", r.get("price_eur"), motivo=motivo)
                    stats["autoscout24:scomparso"] += 1
                    conn.commit()
                    continue
                # 17/11 (freschezza): pagina con i dati dell'annuncio = prova che è vivo, come nella verifica
                # (giudice 17/11) solo se il prezzo della pagina è quello salvato: un prezzo diverso lo rivaluta la raccolta
                # (giudice 17/11 bis) né se il prezzo è cambiato (o l'annuncio è riapparso) dopo l'ultima valutazione
                esito_prezzo = _confronta_prezzo(r, _prezzo_as(det))
                if r.get("prezzo_dopo_valutazione"):
                    stats["autoscout24:non_rinfrescata_da_rivalutare"] += 1
                elif esito_prezzo is None:
                    from .verify import _mark
                    _mark(conn, r["id"], "attivo", r.get("price_eur"))
                    stats["autoscout24:rinfrescata"] += 1
                elif esito_prezzo.startswith("prezzo_cambiato:"):
                    # come giro_rinfresca: prezzo nuovo, evento 'prezzo', stage 'nuovo' (si rivaluta)
                    segna_prezzo_cambiato(conn, r["id"], int(esito_prezzo.split(":", 1)[1]))
                    stats["autoscout24:prezzo_cambiato"] += 1
                else:
                    stats["autoscout24:non_rinfrescata_prezzo"] += 1
                tel = telefono_autoscout(det)
            elif r["id"] in prio_set and os.environ.get("TEL_CAND_SUBITO", "0") != "1":
                if TN.puo_cercare():
                    stats["subito:" + TN.puo_cercare()] += 1
                    continue
                if api is None:
                    api = TN.client_api()
                st, tel = TN.subito_telefono_api(api, dict(r), conn)
                if st in ("bloccato", "tetto", "errore"):
                    stats[f"subito_api:{st}"] += 1
                    if st == "bloccato":
                        log.warning("TEL_NASCOSTO subito: 403/429, pausa")
                    continue
                if st == "non_trovato":
                    # non nella ricerca (prezzo appena cambiato o annuncio tolto): si riprova tra TEL_CAND_ORE ore
                    stats["subito_api:non_trovato"] += 1
                    conn.execute("UPDATE listings SET telefono_at=now() WHERE id=%s", (r["id"],))
                    conn.commit()
                    continue
                if tel is not None:
                    salva_telefono(conn, r["id"], tel, controllato=True)
                    stats[f"subito_api:{'si' if tel else 'no'}"] += 1
                    conn.commit()
                    continue
            else:
                if sub is None:
                    from ..collectors.subito import SubitoCollector
                    sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
                # 25/10 20:55: diagnostica tolta (scriveva nei log il nome utente del venditore: mai dati personali)
                st, tel = subito_telefono_pagina(r["url"])
                if st == "errore":
                    stats["errore"] += 1
                    continue
        except Exception as e:
            stats["errore"] += 1
            if stats["errore"] <= 2:
                log.warning("TEL_CANDIDATE %s: %s", r["source"], str(e)[:150])
            continue
        if tel is True:
            conn.execute("UPDATE listings SET telefono_ok=true, telefono_at=now() WHERE id=%s", (r["id"],))
        elif tel is False:
            conn.execute("UPDATE listings SET telefono_ok=false, telefono_at=now() WHERE id=%s", (r["id"],))
        else:
            # 6/11 (verifica imbuto): "non leggibile" non è "senza numero": si riprova tra ~2 ore invece che tra 12
            rip = max(0, int(os.environ.get("TEL_CAND_ORE", "12")) - int(os.environ.get("TEL_CAND_RIPROVA_ORE", "2")))
            conn.execute("UPDATE listings SET telefono_at=now() - make_interval(hours => %s) WHERE id=%s", (rip, r["id"]))
        stats[f"{r['source']}:{'si' if tel else ('no' if tel is False else 'non_leggibile')}"] += 1
        conn.commit()
    conn.commit()
    if api is not None:
        api.close()
    if prio or any(k.startswith(("subito_api", "autoscout24:scomparso")) for k in stats):
        TN.diagnostica(stats)
    return stats


# ------------------------------------------------------------------ agente 2: nuovi

def _ora_it(dt: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return (dt + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")


def _subito_nuovi(dal: str, pagine: int | None = None, solo: str | None = None) -> list:
    from ..collectors.subito import REGION_CODES, SubitoCollector
    col = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    col.min_delay_s, col.max_delay_s = float(os.environ.get("TR_SUBITO_PAUSA_MIN", "1.2")), float(os.environ.get("TR_SUBITO_PAUSA_MAX", "2.5"))
    out = []
    for reg, provs in REGIONI.items():
        if solo and reg != solo:
            continue
        try:
            if reg not in REGION_CODES:           # codice regione trovato una volta sola e poi ricordato
                code = _codici_regione.get(reg) or col._trova_regione(set(provs), 100)
                if not code:
                    log.warning("TR subito: codice regione non trovato per %s", reg)
                    continue
                _codici_regione[reg] = REGION_CODES[reg] = code
            for l in col.search_recenti(reg, provs, dal, max_pages=pagine or int(os.environ.get("TR_SUBITO_PAGINE", "40"))):
                out.append(l)
        except Exception as e:
            log.warning("TR subito %s: %s", reg, str(e)[:150])
    return out


# 3/11: lettura LEGGERA degli annunci nuovi di Subito (vale anche con TR_SUBITO spento): per ogni regione le pagine
# dal più recente, fermandosi alla prima pagina tutta già nota; al massimo SUB_LEGGERO_PAGINE pagine per regione e
# SUB_LEGGERO_SECONDI in tutto, una richiesta alla volta con pausa. Con 403/429 si ferma per SUB_LEGGERO_PAUSA_MIN
# minuti; con qualsiasi altro problema restituisce quello che ha (anche niente) e il giro continua come prima.
SUB_LEGGERO = True
SUB_LEGGERO_PAGINE = 3
SUB_LEGGERO_SECONDI = 60
SUB_LEGGERO_PAUSA_MIN = 30
_SUB_REGIONI_NOTE = {"piemonte": 2, "liguria": 3}    # codici usati anche dal giro della zona (benzina_mi)
_sub_leggero = {"fermo_fino": 0.0}


def _subito_nuovi_leggero(conn, col=None, pagine: int | None = None, secondi: float | None = None, pausa=(1.2, 2.5),
                          stats: Counter | None = None) -> list:
    """16/11 (fondamenta della velocità): è anche la lettura del GIRO VELOCE con TR_SUBITO=1 (prima _subito_nuovi
    rileggeva ogni volta la finestra "dall'ultimo giro meno 10 minuti": ~8.000 annunci invariati al giorno).
    Un annuncio è "noto" se è nel database con lo STESSO prezzo (un ribasso torna da valutare) oppure se in questo
    processo è già stato trattato e scartato (fuori zona, _VISTI). Ci si ferma alla prima pagina tutta nota."""
    import random
    out: list = []
    st = stats if stats is not None else Counter()
    if time.time() < _sub_leggero["fermo_fino"]:
        st["subito_fermo_429"] += 1
        return out
    try:
        from ..collectors.subito import API_HEADERS, API_SEARCH, REGION_CODES, SubitoCollector, parse_item
        col = col or SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
        fine = time.time() + (secondi or SUB_LEGGERO_SECONDI)
        visti = _visti("subito")
        for reg, provs in REGIONI.items():
            code = REGION_CODES.get(reg) or _codici_regione.get(reg) or _SUB_REGIONI_NOTE.get(reg)
            if not code:
                continue
            voluti = set(provs)
            for page in range(pagine or SUB_LEGGERO_PAGINE):
                if time.time() > fine or fermato():
                    st["subito_fermato_tempo"] += 1
                    return out
                r = col.client.get(API_SEARCH, headers=API_HEADERS, params={"c": 2, "r": code, "t": "s", "lim": 100,
                                                                            "start": page * 100, "sort": "datedesc"})
                st["subito_pagine"] += 1
                if pausa:
                    _dormi(random.uniform(*pausa))
                if r.status_code in (403, 429):
                    _sub_leggero["fermo_fino"] = time.time() + SUB_LEGGERO_PAUSA_MIN * 60
                    log.warning("TR subito leggero: HTTP %s, fermo per %d minuti", r.status_code, SUB_LEGGERO_PAUSA_MIN)
                    return out
                if r.status_code != 200:
                    break
                ads = (r.json() or {}).get("ads")
                if not isinstance(ads, list):
                    log.warning("TR subito leggero: risposta inattesa (%s), salto", type(ads).__name__)
                    return out
                ids = [str(a.get("urn") or a.get("id")) for a in ads if isinstance(a, dict) and (a.get("urn") or a.get("id"))]
                if not ids:
                    break
                # 16/11 revisione: "noto" solo se ATTIVO (un annuncio riapparso torna entro un giro)
                prezzi = {str(x["source_id"]): x.get("price_eur") for x in conn.execute(
                    "SELECT source_id, price_eur, status FROM listings WHERE source='subito' AND source_id = ANY(%s)",
                    (ids,)).fetchall() if (x.get("status") or "attivo") == "attivo"}
                note = 0
                for a in ads:
                    l = parse_item(a) if isinstance(a, dict) else None
                    if l is None:
                        note += 1                      # illeggibile: non si salverebbe comunque
                        continue
                    sid = str(l.source_id)
                    if (sid in prezzi and (prezzi[sid] in (None, l.price_eur) or not l.price_eur)) or sid in visti:
                        note += 1
                        continue
                    if l.province and l.province.upper() not in voluti:
                        visti.add(sid)                 # fuori dalle province: mai nel database, si ricorda qui
                        note += 1
                        continue
                    out.append(l)
                st["subito_noti"] += note
                if note >= len(ads) or len(ads) < 100:
                    break                              # pagina tutta già vista: i successivi sono più vecchi
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("TR subito leggero: %s (il giro continua)", str(e)[:150])
    return out


# 16/11 sera (cliente: "in 10 minuti le auto già sul sito"): gli annunci FUORI ZONA non finiscono mai nel database,
# quindi la regola "fermati alla prima pagina tutta già nota" non scattava mai: ogni giro leggeva tutte le 30 pagine
# e apriva la pagina di dettaglio di centinaia di annunci fuori zona (giri da 15 minuti, 476 fuori zona su 495).
# Ora: gli id già visti (anche fuori zona) si ricordano, il fuori zona si scarta PRIMA del dettaglio, raggio 150 km.
_AS_VISTI: set = _VISTI["autoscout24"]        # 16/11: stesso insieme di _VISTI (si segna dopo il salvataggio)
_AS_VISTI_MAX = _VISTI_MAX


def _as_zona() -> set:
    from ..zone import MILANO, TORINO
    return set(MILANO) | set(TORINO)


def _raggio_as() -> str:
    return os.environ.get("TR_RAGGIO_AS", "150")


def _autoscout_nuovi(conn) -> list:
    """Annunci nuovi entro TR_RAGGIO_AS km da Milano, dal più recente: ci si ferma alla prima pagina tutta già vista."""
    from ..collectors.autoscout import BASE, parse_item
    from ..collectors.base import next_data
    client = _client_as_limitato()                 # 16/11: ritmo unico con la lettura completa (Limitatore)
    zona = _as_zona()
    visti = _visti("autoscout24")
    out = []
    for page in range(1, int(os.environ.get("TR_AS_PAGINE_VELOCE", "10")) + 1):
        if fermato():
            break
        url = (f"{BASE}/lst?atype=C&cy=I&ustate=N%2CU&damaged_listing=include{_ct()}&zip=20121"
               f"&zipr={_raggio_as()}&adage=1&pricefrom=300&priceto=150000"
               f"&fregfrom={os.environ.get('ANNO_MINIMO', '2007')}&sort=age&desc=1&page={page}")
        from . import as_riprova                   # 12/11 (§3.4): riprova con attesa crescente su 429/5xx/rete
        r = as_riprova.get(client, url, "TR autoscout ricerca", dormi=_dormi)
        if r is None:
            break
        if r.status_code != 200:
            log.warning("TR autoscout ricerca HTTP %s", r.status_code)
            break
        items = (((next_data(r.text) or {}).get("props") or {}).get("pageProps") or {}).get("listings") or []
        if not items:
            break
        ids = [str(it.get("id")) for it in items if isinstance(it, dict)]
        noti = {str(x["source_id"]) for x in conn.execute(
            "SELECT source_id FROM listings WHERE source='autoscout24' AND source_id = ANY(%s)", (ids,)).fetchall()}
        gia = noti | (set(ids) & visti)
        nuovi_pagina = set()
        for it in items:
            l = parse_item(it)
            if not l:
                visti.add(str(it.get("id")))       # illeggibile: non si salverebbe comunque
                continue
            if str(l.source_id) in gia or str(l.source_id) in nuovi_pagina:
                continue
            nuovi_pagina.add(str(l.source_id))
            if l.province and l.province.upper() not in zona:
                visti.add(str(l.source_id))        # fuori zona: mai nel database, niente pagina di dettaglio
                continue
            if _solo_privati():
                l.seller_type = "privato"
            out.append(l)                          # in zona: "visto" solo dopo il salvataggio (_segna_visti)
        if len(gia) >= len(ids):           # pagina tutta già vista: i successivi sono più vecchi
            break
    return _as_completa(client, out)


def _solo_privati() -> bool:
    """19/10, cliente: "raccogliere TUTTI gli annunci pubblicati in Lombardia negli ultimi 5 minuti": ora anche i
    concessionari (TR_SOLO_PRIVATI=1 torna ai soli privati)."""
    return os.environ.get("TR_SOLO_PRIVATI", "0") == "1"


def _ct() -> str:
    return "&custtype=P" if _solo_privati() else ""


def _as_completa(client, out: list) -> list:
    """Pagina dell'annuncio: dati completi e numero di telefono."""
    from ..collectors.autoscout import parse_item
    limitato = isinstance(client, ClienteLimitato)
    for i, l in enumerate(out):
        if fermato():
            # arresto: quelli senza dettaglio non si salvano e non si segnano visti: li rilegge il processo dopo
            log.warning("TR autoscout: arresto, %d annunci lasciati al prossimo avvio", len(out) - i)
            return out[:i]
        st, det = _as_dettaglio(client, l.url)
        if st == "attivo" and det:
            full = parse_item(det)
            if full:
                full.seller_type = "privato" if _solo_privati() else (full.seller_type or l.seller_type)
                full.photos = full.photos or l.photos
                l.__dict__.update({k: v for k, v in full.__dict__.items() if v not in (None, "", [])})
            l.raw = {"_telefono": telefono_autoscout(det)}
        if not limitato:                          # col client limitato la pausa la fa il Limitatore
            time.sleep(float(os.environ.get("TR_AS_PAUSA_S", "1.5")))
    return out


# fasce di prezzo per la lettura completa: AutoScout mostra al massimo 20 pagine (400 annunci) per ricerca
FASCE_AS = [(300, 2000), (2000, 3500), (3500, 5000), (5000, 7000), (7000, 9500), (9500, 13000),
            (13000, 18000), (18000, 26000), (26000, 150000)]


def _autoscout_tutti(conn) -> list:
    """Lettura COMPLETA: tutti gli annunci di privati delle ultime 24 ore nel raggio, fascia di prezzo per fascia,
    tutte le pagine (non si ferma alla prima pagina già vista). Si apre il dettaglio solo dei nuovi in zona."""
    from ..collectors.autoscout import BASE, parse_item
    from ..collectors.base import next_data
    from ..zone import MILANO
    zona = set(MILANO)
    from . import as_riprova
    client = _client_as_limitato()                 # 16/11: ritmo unico con il giro veloce (Limitatore)
    visti, out, pagine, fuori = set(), [], 0, 0
    # 12/11 (§3.4): si parte dalla fascia (e pagina) dove la lettura precedente si era fermata per un 429
    ripresa = as_riprova.leggi_ripresa(conn) if as_riprova.attivo() else None
    interrotta = False
    for idx, p0 in as_riprova.ordine_fasce(len(FASCE_AS), ripresa):
        lo, hi = FASCE_AS[idx]
        if interrotta:
            break
        for page in range(p0, 21):
            url = (f"{BASE}/lst?atype=C&cy=I&ustate=N%2CU&damaged_listing=include{_ct()}&zip=20121"
                   f"&zipr={_raggio_as()}&adage=1&pricefrom={lo}&priceto={hi}"
                   f"&fregfrom={os.environ.get('ANNO_MINIMO', '2007')}&sort=age&desc=1&page={page}")
            r = None if fermato() else as_riprova.get(client, url, f"TR completo autoscout fascia {lo}-{hi} pagina {page}",
                                                       dormi=_dormi)
            if r is None:
                if as_riprova.attivo() or fermato():
                    as_riprova.salva_ripresa(conn, idx, page)     # il prossimo giro riparte da qui
                    interrotta = True
                break
            if r.status_code != 200:
                log.warning("TR completo autoscout HTTP %s (fascia %s-%s pagina %s)", r.status_code, lo, hi, page)
                break
            pagine += 1
            items = [it for it in ((((next_data(r.text) or {}).get("props") or {}).get("pageProps") or {}).get("listings") or [])
                     if isinstance(it, dict)]
            if not items:
                break
            ids = [str(it.get("id")) for it in items]
            noti = {x["source_id"] for x in conn.execute(
                "SELECT source_id FROM listings WHERE source='autoscout24' AND source_id = ANY(%s)", (ids,)).fetchall()}
            for it in items:
                l = parse_item(it)
                if not l or l.source_id in noti or l.source_id in visti:
                    continue
                visti.add(l.source_id)
                if l.province and l.province.upper() not in zona:
                    fuori += 1                 # fuori zona: niente dettaglio
                    continue
                if _solo_privati():
                    l.seller_type = "privato"
                out.append(l)
            if len(items) < 20:
                break
    if not interrotta and ripresa:
        as_riprova.togli_ripresa(conn)
    log.warning("TR completo autoscout: %d pagine lette, %d nuovi in zona, %d fuori zona%s%s", pagine, len(out), fuori,
                f", ripresa dalla fascia {FASCE_AS[ripresa['fascia']]}" if ripresa else "",
                ", interrotta (si riprende al giro dopo)" if interrotta else "")
    return _as_completa(client, out)


def _gia_salvati(conn, listings: list) -> dict:
    """16/11: {(fonte, id annuncio): riga} degli annunci già nel database (una query per fonte, non una per annuncio)."""
    out: dict = {}
    per_fonte: dict = {}
    for l in listings:
        if getattr(l, "source", None) in ("subito", "autoscout24") and getattr(l, "source_id", None):
            per_fonte.setdefault(l.source, set()).add(str(l.source_id))
    try:
        for fonte, sids in per_fonte.items():
            for r in conn.execute("SELECT id, source_id, price_eur, status FROM listings WHERE source=%s "
                                  "AND source_id = ANY(%s)", (fonte, sorted(sids))).fetchall():
                if r.get("id") is not None:
                    out[(fonte, str(r["source_id"]))] = r
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.info("TR annunci già salvati non letti (si salva come prima): %s", str(e)[:120])
        return {}
    return out


def _invariati_veloci() -> bool:
    return os.environ.get("TR_INVARIATI_VELOCE", "1") != "0"


def _scrivi_invariati(conn, ids: list, tel_si: list, stats: Counter) -> None:
    """16/11: annunci già salvati, attivi e con lo stesso prezzo: UNA scrittura a blocchi (visto/controllato adesso,
    più il "sì" del telefono di Subito) invece di 6 istruzioni e un commit per annuncio."""
    if not ids:
        return
    from ..db import segna_invariati
    try:
        segna_invariati(conn, ids, tel_si)
        conn.commit()
        stats["invariati_a_blocchi"] += len(ids)
    except Exception as e:
        conn.rollback()
        stats["errore_invariati"] += 1
        log.warning("TR invariati non segnati (%d): %s", len(ids), str(e)[:150])
    ids.clear()
    tel_si.clear()


def _annuncio_at(l) -> datetime | None:
    """Ora di pubblicazione sul portale (Subito: data con cui ordina i risultati, ora italiana), in UTC. None se manca."""
    if getattr(l, "source", None) != "subito" or not isinstance(getattr(l, "raw", None), dict):
        return None
    try:
        from ..collectors.subito import SubitoCollector
        from zoneinfo import ZoneInfo
        d = SubitoCollector._data_annuncio(l.raw)
        if not d:
            return None
        return datetime.strptime(d, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/Rome")).astimezone(timezone.utc)
    except Exception:
        return None


def _salva(conn, listings: list, stats: Counter, coda: bool = False) -> list[int]:
    """Salva gli annunci letti; ritorna gli id da valutare (privati nuovi o con prezzo cambiato, nelle regole).
    16/11: ogni annuncio riceve l'esito in l._tr_ev ('nuovo'|'prezzo'|'riapparso'|'invariato'|'scartato'|'errore');
    gli invariati si scrivono a blocchi (TR_INVARIATI_VELOCE); con coda=True gli id da valutare entrano nella coda
    persistente (tabella tr_coda) NELLA STESSA transazione dell'annuncio: un riavvio non li perde."""
    from ..core.normalize import normalize_fields
    from ..db import upsert_listing_con_scheda as upsert_listing      # 12/11: anche la scheda tecnica (dati_auto)
    from ..zone import MILANO, TORINO
    from .collect import doppione_altro_sito
    da_valutare = []
    zona = set(MILANO) | set(TORINO)
    km_max, anno_min = int(os.environ.get("KM_MASSIMO", "180000")), int(os.environ.get("ANNO_MINIMO", "2007"))
    prima = _gia_salvati(conn, listings) if _invariati_veloci() else {}
    invariati: list = []
    tel_si: list = []
    for l in listings:
        l._tr_ev = "scartato"
        tel = (l.raw or {}).get("_telefono") if l.source == "autoscout24" else None
        tel_sub = telefono_subito(l.raw) if l.source == "subito" else False
        if l.source == "subito" and _diag_sub["n"] < 12 and isinstance(l.raw, dict):
            _diag_sub["n"] += 1           # solo NOMI dei campi (mai valori): c'è un'indicazione "numero disponibile"?
            adv = l.raw.get("advertiser") or {}
            def _chiavi_tel(o, pre=""):
                out = []
                if isinstance(o, dict):
                    for k, v in o.items():
                        if any(w in k.lower() for w in ("phone", "tel", "contact", "call", "number")):
                            out.append(f"{pre}{k}={type(v).__name__}:{'pieno' if v not in (None, '', [], {}, False) else 'vuoto'}")
                        if isinstance(v, (dict, list)) and len(pre) < 40:
                            out += _chiavi_tel(v if isinstance(v, dict) else {}, pre + k + ".")
                return out
            ph = adv.get("phone") if isinstance(adv, dict) else None
            forma = re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", ph))[:40] if isinstance(ph, str) else type(ph).__name__
            log.warning("TR subito telefono: forma=%r lunghezza=%s cifre=%s", forma, len(ph) if isinstance(ph, str) else None,
                        len(re.sub(r"\D", "", ph)) if isinstance(ph, str) else None)
        from ..core.dal_testo import completa
        # 2/11 sera: anche cambio e potenza dal testo (senza kW/cambio l'auto non trova confronti -> dati insufficienti)
        for k, v in completa({"fuel": l.fuel, "year": l.year, "mileage_km": l.mileage_km, "gearbox": l.gearbox,
                              "power_kw": l.power_kw, "version_raw": l.version_raw}, l.title, l.description).items():
            setattr(l, k, v)                      # dati mancanti presi dal testo dell'annuncio
        stats[f"letti:{l.source}"] += 1
        try:
            normalize_fields(l)
        except Exception:
            stats["scartati_dati"] += 1
            stats["scarto:dati"] += 1
            continue
        if not l.province and l.city:
            # 3/11 (studio 4, P4): provincia dal comune prima di dire "fuori zona" (AutoScout24 senza CAP)
            from .collect import provincia_da_comune
            pv = provincia_da_comune(conn, l.city)
            if pv:
                l.province = pv
                stats["provincia_ricavata"] += 1
        if not l.province:
            stats[f"{l.source}:fuori_zona"] += 1
            stats["scarto:senza_provincia"] += 1
            continue
        if l.province.upper() not in zona:
            stats[f"{l.source}:fuori_zona"] += 1
            stats["scarto:fuori_zona"] += 1
            continue
        if l.province and not l.region:
            from ..core.normalize import PROVINCE_REGION
            l.region = PROVINCE_REGION.get(l.province.upper())
        p = prima.get((l.source, str(l.source_id)))
        if p is not None and p.get("status") == "attivo" and p.get("price_eur") == l.price_eur \
                and not (l.source == "autoscout24" and tel is not None):
            # 16/11: già salvato, attivo, stesso prezzo: niente upsert completo, solo "visto" a blocchi
            invariati.append(p["id"])
            if l.source == "subito" and (tel_sub or ha_numero_testo(l.title, l.description)):
                tel_si.append(p["id"])
            l._tr_ev = "invariato"
            stats["salvati"] += 1
            stats[f"{l.source}:invariato"] += 1
            if len(invariati) >= 500:
                _scrivi_invariati(conn, invariati, tel_si, stats)
            continue
        try:
            lid, ev = upsert_listing(conn, l)
        except Exception as e:
            conn.rollback()
            l._tr_ev = "errore"
            stats["scartati_dati"] += 1
            stats["scarto:non_salvato"] += 1
            log.info("TR annuncio %s non salvato: %s", l.url, str(e)[:100])
            continue
        l._tr_ev = ev
        stats["salvati"] += 1
        if l.source == "autoscout24":
            salva_telefono(conn, lid, tel, controllato=tel is not None)        # dalla pagina dell'annuncio
        else:
            # Subito: "sì" dalla ricerca (advertiser.phone) o dal testo; il "no" lo dà solo il controllo della pagina
            salva_telefono(conn, lid, True if (tel_sub or ha_numero_testo(l.title, l.description)) else None)
        conn.execute("UPDATE listings SET last_checked_at=now() WHERE id=%s", (lid,))
        stats[f"{l.source}:{ev}"] += 1
        if ev in ("prezzo", "riapparso"):
            conn.execute("UPDATE listings SET stage='nuovo' WHERE id=%s AND stage<>'nuovo'", (lid,))
        if l.source == "autoscout24" and ev == "nuovo" and doppione_altro_sito(conn, l, lid):
            stats["autoscout24:doppione"] += 1
            conn.commit()
            continue
        valutare = ev in ("nuovo", "prezzo", "riapparso") and (l.seller_type == "privato" or not _solo_privati()) \
            and (l.year or 0) >= anno_min and not (l.mileage_km and l.mileage_km > km_max) and (l.price_eur or 0) >= 300
        if valutare and coda:
            _coda_metti(conn, lid, l, ev, stats)
        conn.commit()
        if valutare:
            da_valutare.append(lid)
    _scrivi_invariati(conn, invariati, tel_si, stats)
    return da_valutare


def valuta_e_pubblica(conn, ids: list[int], stats: Counter, fonte: str = "tempo-reale",
                      pubblicate: list | None = None) -> None:
    """Valuta gli annunci (pricing/valutazione.valuta_e_scrivi: la stessa funzione del cron, stessi ingressi, stessa
    guardia sulle scritture) e mette sul sito quelle buone con il numero del venditore (valutazione.promuovi, la
    stessa promozione della riprova del giudice e del recupero dei confronti; eccezioni dichiarate:
    TR_SUBITO_SENZA_NUMERO, TR_LUSSO_SENZA_NUMERO).
    17/11: prima si pubblicava solo se la valutazione NUOVA aveva la stessa categoria della salvata: una nuova buona
    rifiutata dalla guardia con la salvata buona di altra categoria, o la precedente buona tenuta in attesa del giudice,
    restavano fuori; e un annuncio scartato per sempre (doppione, truffa, analisi manuale) tornava sul sito."""
    from ..pricing.valutazione import promuovi, valuta_e_scrivi
    if not ids:
        return
    es = valuta_e_scrivi(conn, list(ids), fonte, pubblica=False)
    for k, n in es.stats.items():
        if k.startswith(("categoria:", "esclusa:")):
            stats["valuta:" + k.split(":", 1)[1]] += n
        elif k == "errore":
            stats["valuta:errore"] += n
        elif k.startswith("scrittura:"):
            stats["valuta:" + k] += n
    promuovi(conn, es, fonte, stats, pubblicate)


def _lettore_nuovi(conn, ids: list[int], stats: Counter) -> None:
    """25/10: l'agente lettore legge subito gli annunci nuovi di privati con il numero (versione, kW, cambio,
    prezzo vero, danni) prima della valutazione, così le occasioni vanno sul sito già con i dati giusti."""
    if not ids or os.environ.get("LETTORE_NUOVI", "1") != "1":
        return
    try:
        con_num = [r["id"] for r in conn.execute(
            "SELECT id, title, description, telefono_ok FROM listings WHERE id = ANY(%s) "
            "AND COALESCE(seller_type, 'privato') <> 'commerciante'", (ids,)).fetchall()
            if r["telefono_ok"] or ha_numero_testo(r["title"], r["description"])]
        if not con_num:
            return
        from ..ai import lettore
        res = lettore.giro(conn, ids=con_num, limite=int(os.environ.get("LETTORE_NUOVI_MAX", "60")), rivaluta=False)
        stats["lettore:letti"] += int(res.get("lette") or 0)
        stats["lettore:cambiati"] += sum(v for k, v in res.items() if k.startswith("cambiato:"))
    except Exception as e:
        conn.rollback()
        log.warning("TR lettore nuovi: %s", str(e)[:200])


# ------------------------------------------------------------------ coda persistente (16/11, migrazione 058)

CODA_TENTATIVI = 5          # dopo 5 valutazioni non finite l'annuncio esce dalla coda (lo riprende il cron)
CODA_RIPRESA_MIN = 5        # un annuncio "preso" da un giro che non ha finito torna disponibile dopo 5 minuti
CODA_MAX = 300              # annunci rimasti in coda ripresi per giro
_coda_pulita = {"t": 0.0}


def _intenv(nome: str, default: int) -> int:
    try:
        return int(os.environ.get(nome) or default)
    except ValueError:
        return default


def coda_attiva(conn) -> bool:
    """La coda c'è (migrazione 058 applicata) e non è spenta (TR_CODA=0)? Se no tutto come prima (id in memoria)."""
    if os.environ.get("TR_CODA", "1") == "0":
        return False
    try:
        r = conn.execute("SELECT to_regclass('tr_coda') IS NOT NULL AS ok").fetchone()
        return bool(r and r.get("ok"))
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _coda_metti(conn, lid: int, l, ev: str, stats: Counter) -> None:
    """In coda nella transazione dell'annuncio (punto di ripristino: un errore qui non perde l'annuncio). Un annuncio
    già valutato che torna (ribasso, riapparso) si rimette in coda; uno ancora in attesa resta com'è."""
    from contextlib import nullcontext
    try:
        tx = conn.transaction() if hasattr(conn, "transaction") else nullcontext()
        with tx:
            conn.execute(
                "INSERT INTO tr_coda (listing_id, fonte, evento, annuncio_at, letto_at, preso_at, tentativi) "
                "VALUES (%s, %s, %s, %s, now(), now(), 1) "
                "ON CONFLICT (listing_id) DO UPDATE SET evento=EXCLUDED.evento, annuncio_at=EXCLUDED.annuncio_at, "
                "letto_at=now(), preso_at=now(), tentativi=1, valutato_at=NULL, esito=NULL, pubblicato_at=NULL, "
                "sito_at=NULL WHERE tr_coda.valutato_at IS NOT NULL",
                (lid, l.source, ev, _annuncio_at(l)))
        stats["coda:messi"] += 1
    except Exception as e:
        stats["coda:errore"] += 1
        log.warning("TR coda: annuncio %s non messo in coda: %s", lid, str(e)[:150])


def _coda_prendi(conn, escludi: list, stats: Counter) -> list[int]:
    """Annunci rimasti in coda (giro caduto, riavvio, arresto): si prendono (preso_at) per non valutarli in due."""
    try:
        righe = conn.execute(
            "UPDATE tr_coda SET preso_at = now(), tentativi = tentativi + 1 WHERE listing_id IN ("
            " SELECT listing_id FROM tr_coda WHERE valutato_at IS NULL AND tentativi < %s"
            " AND (preso_at IS NULL OR preso_at < now() - make_interval(mins => %s))"
            " AND NOT (listing_id = ANY(%s::bigint[])) ORDER BY letto_at LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING listing_id",
            (_intenv("TR_CODA_TENTATIVI", CODA_TENTATIVI), _intenv("TR_CODA_RIPRESA_MIN", CODA_RIPRESA_MIN),
             [int(i) for i in escludi], _intenv("TR_CODA_MAX", CODA_MAX))).fetchall()
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        stats["coda:errore"] += 1
        log.warning("TR coda non letta: %s", str(e)[:150])
        return []
    ids = [int(r["listing_id"]) for r in righe]
    if ids:
        stats["coda:ripresi"] += len(ids)
        log.warning("TR CODA: ripresi %d annunci rimasti da valutare (riavvio o giro interrotto)", len(ids))
    return ids


def _coda_rilascia(conn, ids: list) -> None:
    """Arresto: gli annunci presi e non valutati tornano liberi (preso_at NULL)."""
    try:
        conn.execute("UPDATE tr_coda SET preso_at = NULL WHERE listing_id = ANY(%s::bigint[]) AND valutato_at IS NULL",
                     ([int(i) for i in ids],))
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.info("TR coda non rilasciata: %s", str(e)[:150])


def _percentile(valori: list, q: float):
    v = sorted(x for x in valori if x is not None)
    if not v:
        return None
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def _coda_fatti(conn, ids: list, pubblicate: list, stats: Counter) -> None:
    """Valutati: si chiude la riga e si misura il ritardo vero (pubblicato sul portale → letto → valutato → sul sito
    dell'annuncio); una riga TR_RITARDO per ogni auto pubblicata, riassunto nei contatori del giro."""
    if not ids:
        return
    try:
        righe = conn.execute(
            "UPDATE tr_coda SET valutato_at = now(), esito = CASE WHEN listing_id = ANY(%s::bigint[]) THEN 'pubblicata' "
            "ELSE 'valutata' END, pubblicato_at = CASE WHEN listing_id = ANY(%s::bigint[]) THEN now() END "
            "WHERE listing_id = ANY(%s::bigint[]) AND valutato_at IS NULL "
            "RETURNING listing_id, fonte, annuncio_at, letto_at, valutato_at, pubblicato_at",
            (list(pubblicate), list(pubblicate), [int(i) for i in ids])).fetchall()
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        stats["coda:errore"] += 1
        log.warning("TR coda non chiusa: %s", str(e)[:150])
        return
    stats["coda:valutati"] += len(righe)

    def sec(a, b):
        return (b - a).total_seconds() if a is not None and b is not None else None
    val = [sec(r["letto_at"], r["valutato_at"]) for r in righe]
    da_annuncio = [sec(r["annuncio_at"], r["pubblicato_at"]) for r in righe if r.get("pubblicato_at")]
    if _percentile(val, .95) is not None:
        stats["ritardo_letto_valutato_p95_s"] = int(_percentile(val, .95))
    if _percentile(da_annuncio, .95) is not None:
        stats["ritardo_annuncio_pubblicata_p95_min"] = round(_percentile(da_annuncio, .95) / 60, 1)
    for r in righe:
        if r.get("pubblicato_at"):
            log.warning("TR_RITARDO %s", json.dumps({
                "id": r["listing_id"], "fonte": r["fonte"],
                "annuncio_letto_min": None if sec(r["annuncio_at"], r["letto_at"]) is None
                else round(sec(r["annuncio_at"], r["letto_at"]) / 60, 1),
                "letto_valutato_s": None if sec(r["letto_at"], r["valutato_at"]) is None
                else int(sec(r["letto_at"], r["valutato_at"])),
                "annuncio_pubblicata_min": None if sec(r["annuncio_at"], r["pubblicato_at"]) is None
                else round(sec(r["annuncio_at"], r["pubblicato_at"]) / 60, 1)}))
    _coda_pulisci(conn)


def _coda_pulisci(conn) -> None:
    """Una volta all'ora: via le righe chiuse da più di 7 giorni e quelle abbandonate (troppi tentativi)."""
    if time.time() - _coda_pulita["t"] < 3600:
        return
    _coda_pulita["t"] = time.time()
    try:
        n = conn.execute("UPDATE tr_coda SET valutato_at = now(), esito = 'abbandonata' WHERE valutato_at IS NULL "
                         "AND tentativi >= %s AND preso_at < now() - make_interval(mins => %s)",
                         (_intenv("TR_CODA_TENTATIVI", CODA_TENTATIVI), _intenv("TR_CODA_RIPRESA_MIN", CODA_RIPRESA_MIN))
                         ).rowcount
        conn.execute("DELETE FROM tr_coda WHERE valutato_at < now() - interval '7 days'")
        conn.commit()
        if n:
            log.warning("TR CODA: %d annunci abbandonati dopo troppi tentativi (li valuterà il cron)", n)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.info("TR coda non pulita: %s", str(e)[:150])


def segna_sul_sito(conn, ids) -> dict:
    """Per il sito (dopo il calcolo dell'elenco): le auto della coda comparse sull'elenco. Scrive sito_at e ritorna
    il riassunto del ritardo vero annuncio→sito (minuti) per la riga SITO_RITARDO dei log. Mai un errore."""
    ids = [int(i) for i in ids or []]
    if not ids:
        return {}
    try:
        righe = conn.execute(
            "UPDATE tr_coda SET sito_at = now() WHERE listing_id = ANY(%s::bigint[]) AND sito_at IS NULL AND valutato_at IS NOT NULL "
            "RETURNING listing_id, annuncio_at, letto_at, pubblicato_at, sito_at", (ids,)).fetchall()
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.info("TR coda sito_at non scritto: %s", str(e)[:150])
        return {}
    if not righe:
        return {}

    def m(a, b):
        return (b - a).total_seconds() / 60 if a is not None and b is not None else None
    da_annuncio = [m(r["annuncio_at"], r["sito_at"]) for r in righe]
    da_letto = [m(r["letto_at"], r["sito_at"]) for r in righe]
    da_pubbl = [m(r["pubblicato_at"], r["sito_at"]) for r in righe]
    out = {"auto": len(righe)}
    for nome, v in (("annuncio_sito", da_annuncio), ("letto_sito", da_letto), ("pubblicata_sito", da_pubbl)):
        if _percentile(v, .5) is not None:
            out[nome + "_p50_min"] = round(_percentile(v, .5), 1)
            out[nome + "_p95_min"] = round(_percentile(v, .95), 1)
    return out


# ------------------------------------------------------------------ giro nuovi

_avvio = {"primo": True}


def _valuta(conn, ids: list, stats: Counter, coda: bool) -> None:
    """Lettore AI + valutazione + pubblicazione. Con la coda: anche gli annunci rimasti da giri caduti o riavvii, e
    all'arresto non si valuta (restano in coda e li prende il processo dopo)."""
    tutti = list(dict.fromkeys(int(i) for i in ids))
    if coda:
        tutti += _coda_prendi(conn, tutti, stats)
    if not tutti:
        return
    if coda and fermato():
        stats["coda:lasciati_arresto"] += len(tutti)
        _coda_rilascia(conn, tutti)          # 16/11 revisione: il processo dopo li riprende subito, non dopo 5 minuti
        return
    _lettore_nuovi(conn, tutti, stats)
    pubblicate: list = []
    valuta_e_pubblica(conn, tutti, stats, pubblicate=pubblicate)
    if coda:
        _coda_fatti(conn, tutti, pubblicate, stats)


def _subito_veloce(conn, stats: Counter) -> list:
    """Giro veloce Subito: le pagine dal più recente fino alla prima tutta nota. Al primo giro dopo l'avvio si legge
    di più (TR_RECUPERO_PAGINE per regione, TR_RECUPERO_SECONDI): è il giro di recupero di quello uscito da fermi."""
    if _avvio["primo"]:
        pag = _intenv("TR_RECUPERO_PAGINE", 15)
        sec = _intenv("TR_RECUPERO_SECONDI", 240)
        stats["recupero_avvio"] = 1
    else:
        pag = _intenv("TR_SUBITO_PAGINE_VELOCE", 5)
        sec = _intenv("TR_SUBITO_SECONDI_VELOCE", 60)
    return _subito_nuovi_leggero(conn, pagine=pag, secondi=sec, stats=stats)


def giro_nuovi(conn, completo: bool = False) -> Counter:
    """completo=False: solo il nuovo, dal più recente, fino alla prima pagina già vista (giro veloce).
    completo=True: rilettura di TUTTI gli annunci delle ultime TR_COMPLETO_ORE ore, cosi' non se ne perde nessuno.
    16/11: gli annunci da valutare passano dalla coda persistente (tr_coda) se c'è; tra una fase e l'altra si guarda
    l'arresto (FERMA): quello che è già salvato resta in coda per il processo dopo."""
    stats: Counter = Counter()
    t_giro = time.time()
    coda = coda_attiva(conn)
    stats["coda"] = int(coda)
    row = conn.execute("SELECT ultimo_at FROM tempo_reale WHERE giro='nuovi'").fetchone()
    inizio = datetime.now(timezone.utc)
    dal_dt = (row["ultimo_at"] if row and row["ultimo_at"] else inizio - timedelta(minutes=int(os.environ.get("TR_PRIMO_GIRO_MIN", "60"))))
    dal_dt -= timedelta(minutes=int(os.environ.get("TR_MARGINE_MIN", "10")))       # sovrapposizione: nessun buco
    if completo:
        dal_dt = inizio - timedelta(hours=float(os.environ.get("TR_COMPLETO_ORE", "24")))
        stats["completo"] = 1
    if completo:
        # a pezzi (una regione alla volta, poi AutoScout): letto, salvato, valutato e buttato, per non riempire la memoria
        import gc
        pezzi = [("subito", r) for r in REGIONI] if os.environ.get("TR_SUBITO", "1") == "1" else []
        if os.environ.get("TR_AUTOSCOUT", "1") == "1":
            pezzi.append(("autoscout", None))
        for fonte, reg in pezzi:
            if fermato():
                stats["fermato_arresto"] = 1
                break
            try:
                letti = (_subito_nuovi(_ora_it(dal_dt), int(os.environ.get("TR_COMPLETO_SUB_PAGINE", "150")), reg)
                         if fonte == "subito" else _autoscout_tutti(conn))
            except Exception as e:
                log.warning("TR completo %s %s: %s", fonte, reg or "", str(e)[:200])
                continue
            stats["letti"] += len(letti)
            stats[f"letti_{fonte}"] += len(letti)
            ids = _salva(conn, letti, stats, coda=coda)
            _segna_visti(letti)
            del letti
            _valuta(conn, ids, stats, coda)
            conn.commit()
            gc.collect()
    else:
        letti = []
        t0 = time.time()
        if os.environ.get("TR_SUBITO", "1") == "1" and os.environ.get("TR_SUBITO_VELOCE", "1") == "0":
            letti += _subito_nuovi(_ora_it(dal_dt))            # come prima del 16/11 (rilegge la finestra)
            _diagnostica_pagina_subito([l for l in letti if l.source == "subito"][:3])
        elif SUB_LEGGERO:
            # 16/11: anche con TR_SUBITO=1 il giro veloce legge solo le pagine nuove (prima la finestra intera)
            letti += _subito_veloce(conn, stats)
            stats["letti_subito_leggero"] = len(letti)
        stats["tempo_subito_s"] = round(time.time() - t0, 1)
        if os.environ.get("TR_AUTOSCOUT", "1") == "1" and not fermato():
            t0 = time.time()
            try:
                # 16/11: la ripresa dopo un 429 (_autoscout_tutti) non gira più qui: la fa il filo della lettura
                # completa del worker (giro_ripresa_as), così il giro veloce resta sotto i 2 minuti
                letti += _autoscout_nuovi(conn)
            except Exception as e:
                log.warning("TR autoscout: %s", str(e)[:200])
            stats["tempo_autoscout_s"] = round(time.time() - t0, 1)
        stats["letti"] = len(letti)
        t0 = time.time()
        ids = _salva(conn, letti, stats, coda=coda)
        _segna_visti(letti)
        _ritardo_subito(letti, stats)                       # 16/11: solo sugli annunci NUOVI (dopo il salvataggio)
        stats["tempo_salva_s"] = round(time.time() - t0, 1)
        t0 = time.time()
        _valuta(conn, ids, stats, coda)
        stats["tempo_valuta_s"] = round(time.time() - t0, 1)
        _avvio["primo"] = False
    stats["tempo_giro_s"] = round(time.time() - t_giro, 1)
    if fermato():
        stats["fermato_arresto"] = 1
    conn.execute("INSERT INTO tempo_reale (giro, ultimo_at, stats) VALUES (%s, %s, %s) ON CONFLICT (giro) DO UPDATE "
                 "SET ultimo_at=EXCLUDED.ultimo_at, stats=EXCLUDED.stats",
                 ("completo" if completo else "nuovi", inizio, json.dumps(dict(stats))))
    conn.commit()
    return stats


def giro_ripresa_as(conn) -> Counter | None:
    """16/11: lettura completa AutoScout24 interrotta da un 429 (punto di ripresa in tempo_reale 'as_ripresa'):
    la riprende il filo della lettura completa del worker, non più il giro veloce. None se non c'è niente da riprendere."""
    from . import as_riprova
    if not (as_riprova.attivo() and not as_riprova.fermo() and as_riprova.leggi_ripresa(conn)):
        return None
    stats: Counter = Counter({"as_ripresa": 1})
    coda = coda_attiva(conn)
    letti = _autoscout_tutti(conn)
    stats["letti"] = len(letti)
    ids = _salva(conn, letti, stats, coda=coda)
    _segna_visti(letti)
    del letti
    _valuta(conn, ids, stats, coda)
    conn.commit()
    return stats


def ritardo_minuti(data_annuncio: str | None, ora_it: str) -> int | None:
    """Minuti tra la data dell'annuncio su Subito ('AAAA-MM-GG HH:MM', ora italiana) e adesso (stessa forma)."""
    if not data_annuncio:
        return None
    try:
        d = datetime.strptime(data_annuncio, "%Y-%m-%d %H:%M")
        o = datetime.strptime(ora_it, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    m = int((o - d).total_seconds() // 60)
    return m if m >= 0 else 0


def _ritardo_subito(letti: list, stats: Counter) -> None:
    """5/11 (cliente: "ho paura che non raccogliamo subito gli annunci appena scesi"): quanti minuti dopo la
    pubblicazione su Subito leggiamo gli annunci NUOVI (mediana e peggiore del giro), nei contatori di TR NUOVI.
    16/11: solo gli annunci davvero nuovi (l._tr_ev == 'nuovo', dopo _salva): prima contavano anche i già visti
    riletti dalla finestra, e la "peggiore" era sempre la sovrapposizione di 10 minuti. Se l'esito manca
    (annunci non passati da _salva) si contano tutti come prima."""
    try:
        from ..collectors.subito import SubitoCollector
        ora = _ora_it(datetime.now(timezone.utc))
        rit = sorted(m for m in (ritardo_minuti(SubitoCollector._data_annuncio(l.raw), ora)
                                 for l in letti if l.source == "subito" and isinstance(l.raw, dict)
                                 and getattr(l, "_tr_ev", "nuovo") == "nuovo") if m is not None)
        if rit:
            stats["ritardo_subito_mediana_min"] = rit[len(rit) // 2]
            stats["ritardo_subito_max_min"] = rit[-1]
            stats["subito_entro_15_min"] = sum(1 for m in rit if m <= 15)
    except Exception as e:
        log.info("ritardo subito non calcolato: %s", str(e)[:100])


_lucchetto_occupato: Counter = Counter()


def con_lucchetto(nome: str, fn, *a):
    """Un solo processo alla volta per giro (durante un aggiornamento del sito girano due copie per qualche minuto).
    16/11: se il lucchetto è occupato lo si scrive nei log (TR LUCCHETTO), prima il giro saltava in silenzio."""
    from ..db import connect
    with connect() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK[nome],)).fetchone()["ok"]:
            _lucchetto_occupato[nome] += 1
            log.warning("TR LUCCHETTO %s occupato da un altro processo o filo: giro saltato (%d volte da avvio)",
                        nome, _lucchetto_occupato[nome])
            return None
        try:
            return fn(conn, *a)
        finally:
            try:
                conn.rollback()
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK[nome],))
                conn.commit()
            except Exception:
                pass



def giro_telefoni_subito(conn) -> dict:
    """Una volta al giorno: rilegge le ricerche di Subito (dal più recente, TR_TEL_PAGINE pagine da 100 per regione) e
    segna per ogni annuncio già salvato se il venditore ha il numero (sì/no). Il numero non si salva mai."""
    from ..collectors.subito import API_HEADERS, API_SEARCH, REGION_CODES, SubitoCollector
    col = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    st = Counter()
    for reg, provs in REGIONI.items():
        code = REGION_CODES.get(reg) or _codici_regione.get(reg) or col._trova_regione(set(provs), 100)
        if not code:
            continue
        _codici_regione[reg] = REGION_CODES[reg] = code
        for page in range(int(os.environ.get("TR_TEL_PAGINE", "100"))):
            try:
                r = col.client.get(API_SEARCH, headers=API_HEADERS, params={"c": 2, "r": code, "t": "s", "lim": 100,
                                                                            "start": page * 100, "sort": "datedesc"})
            except Exception as e:
                log.info("TR telefoni subito %s: %s", reg, str(e)[:100])
                break
            time.sleep(float(os.environ.get("TR_TEL_PAUSA_S", "1.0")))
            if r.status_code != 200:
                st[f"http_{r.status_code}"] += 1
                break
            ads = (r.json() or {}).get("ads") or []
            if not ads:
                break
            for a in ads:                               # distribuzione dell'indicatore (valore di 1 carattere, non è un numero)
                v = ((a.get("advertiser") or {}).get("phone"))
                st[f"indicatore={v if isinstance(v, str) and len(v) <= 2 else type(v).__name__}"] += 1
                if v is not None and ha_numero_testo(a.get("subject"), a.get("body")):
                    st[f"numero_nel_testo&indicatore={v if isinstance(v, str) and len(v) <= 2 else '?'}"] += 1
            si = [str(a.get("urn")) for a in ads if a.get("urn") and telefono_subito(a)]
            no = [str(a.get("urn")) for a in ads if a.get("urn") and not telefono_subito(a)]
            st["letti"] += len(ads)
            st["con_numero"] += len(si)
            st["senza_numero"] += len(no)
            if si:
                n = conn.execute("UPDATE listings SET telefono_ok=true, telefono_at=now() WHERE source='subito' "
                                 "AND source_id = ANY(%s) AND telefono_ok IS DISTINCT FROM true", (si,)).rowcount
                st["aggiornati_si"] += n
            conn.commit()
    return dict(st)


_diag_pag = {"n": 0}   # si azzera a ogni avvio: 6 pagine di prova


def _diagnostica_pagina_subito(lst: list) -> None:
    """Diagnostica (solo nomi dei campi e forma dei valori, cifre → 9): dove la PAGINA dell'annuncio Subito dice
    che il venditore ha il numero (bottone Chiama). Mai il numero."""
    import json as _json
    from ..collectors.base import next_data
    if _diag_pag["n"] >= 6 or not lst:
        return
    from ..collectors.subito import SubitoCollector
    sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    via = "unlocker" if (sub.unlocker_zone and sub.unlocker_key) else ("proxy" if os.environ.get("SCRAPER_PROXY") else "diretto")
    for l in lst:
        _diag_pag["n"] += 1
        try:
            r = sub._get(l.url)
        except Exception as e:
            log.warning("TR subito pagina: errore %s", str(e)[:100])
            continue
        data = next_data(r.text) if r.status_code == 200 else None
        trov = []

        def scan(o, path=""):
            if len(trov) > 25:
                return
            if isinstance(o, dict):
                for k, v in o.items():
                    p = f"{path}.{k}" if path else k
                    if any(w in k.lower() for w in ("phone", "tel", "call", "contact")):
                        forma = re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", _json.dumps(v)))[:50] if not isinstance(v, (dict, list)) else type(v).__name__
                        trov.append(f"{p}={forma}")
                    scan(v, p)
            elif isinstance(o, list):
                for i, v in enumerate(o[:5]):
                    scan(v, f"{path}[{i}]")
        scan(data or {})
        low = r.text.lower()
        log.warning("TR subito pagina (%s): http=%s byte=%s next_data=%s bottone_chiama=%s parole=%s campi=%s", via,
                    r.status_code, len(r.text), bool(data), ("mostra numero" in low or "chiama" in low),
                    [w for w in ("captcha", "datadome", "cloudflare", "access denied", "phone", "telefono", "chiama",
                                 "messaggio", "__next_data__") if w in low], trov[:20])


_SUB_PAGINA = {"n": 0}


def subito_telefono_pagina(url: str) -> tuple[str, bool | None]:
    """Apre la pagina di UN annuncio Subito (Web Unlocker di Bright Data se configurato, altrimenti proxy o diretto) e
    dice se c'è il bottone Chiama. Ritorna (stato annuncio, telefono sì/no/None=non leggibile). Il numero non si legge."""
    from ..collectors.base import next_data
    from ..collectors.subito import SubitoCollector
    sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    try:
        r = sub._get(url)
    except Exception as e:
        log.info("TR subito pagina %s: %s", url, str(e)[:100])
        return "errore", None
    if r.status_code in (404, 410):
        return "scomparso", None
    if r.status_code != 200:
        return "errore", None
    low = r.text.lower()
    if "annuncio non è più disponibile" in low or "questo annuncio è scaduto" in low:
        return "scomparso", None
    data = next_data(r.text)
    tel = telefono_da_pagina(data, low, r.text)
    if _SUB_PAGINA["n"] < 5:
        _SUB_PAGINA["n"] += 1
        log.warning("TR subito pagina controllo: dati=%s esito=%s", bool(data), tel)
    return "attivo", tel


_diag_str = {"n": 0}


def _diag_struttura(html: str) -> None:
    """Diagnostica sicura: solo i NOMI dei campi intorno a showPhone/phone (ogni valore testo diventa "s", cifre 9)."""
    if _diag_str["n"] >= 4 or not html:
        return
    _diag_str["n"] += 1
    out = []
    for m in re.finditer(r'(?i)(showPhone|\\"phone\\")', html):
        seg = html[max(0, m.start() - 220):m.start() + 120]
        seg = re.sub(r'\\"([^"\\]{0,80})\\":', lambda x: "<" + x.group(1) + ">:", seg)      # chiavi leggibili
        seg = re.sub(r'\\"[^"\\]*\\"', '"s"', seg)                                            # valori testo nascosti
        seg = re.sub(r"\d", "9", seg)
        out.append(seg[-300:])
        if len(out) >= 3:
            break
    log.warning("TR subito struttura: %s", out)


RE_SHOWPHONE = re.compile(r'showPhone\\*"\s*:\s*(true|false)', re.I)


def telefono_da_pagina(data: dict | None, low: str, html: str = "") -> bool | None:
    """Indicatore del bottone Chiama nella pagina. Subito (Next.js) mette i dati dell'annuncio nell'HTML come JSON
    escapato: il venditore ha "showPhone": true/false. None se la pagina non ha i dati (blocco anti-bot)."""
    if "phoneSkeleton" in (html or "") or re.search(r'\\*"phone\\*"\s*:\s*\\*"', html or ""):
        return True                      # 25/10: segno del pulsante "Mostra numero" (solo se il venditore ha il numero)
    vals = [m.group(1).lower() == "true" for m in RE_SHOWPHONE.finditer(html or "")]
    if any(vals):
        return True
    if vals:
        # 17/10: "showPhone": false è risultato su TUTTE le pagine controllate (0 su 115): non è un "no" affidabile.
        # Finché non si conferma il segnale giusto, un false non esclude l'auto: resta "da controllare".
        return None
    if not data:
        return None
    flags = []

    def scan(o, depth=0):
        if depth > 12:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                kl = k.lower()
                if kl in ("phone", "hasphone", "showphone", "phonevisible", "isphonevisible", "phone_visible",
                          "hasphonenumber", "phonenumberavailable"):
                    if isinstance(v, bool):
                        flags.append(v)
                    elif isinstance(v, str) and v.strip():
                        flags.append(v.strip() not in ("0", "false", "no"))
                    elif isinstance(v, (int, float)):
                        flags.append(v != 0)
                scan(v, depth + 1)
        elif isinstance(o, list):
            for v in o[:30]:
                scan(v, depth + 1)
    scan(data)
    if any(flags):
        return True
    if "mostra numero" in low or 'aria-label="chiama' in low:
        return True
    return False if flags else None


def registra_sparita(conn, lid: int) -> None:
    """Misura delle stime: un'auto del sito è sparita (probabilmente venduta). Si registra cosa avevamo stimato."""
    try:
        conn.execute(
            """INSERT INTO misure_vendite (listing_id, fonte, make, model, anno, km, prezzo_iniziale, prezzo_finale, ribassi,
                   mercato_m, rivendita_r, margine, n_confronti, metodo, giorni_online, giorni_sul_sito, policy)
               SELECT l.id, l.source, l.make, l.model, l.year, l.mileage_km,
                      (SELECT pe.price_eur FROM price_events pe WHERE pe.listing_id=l.id AND pe.event='apparso' ORDER BY pe.id LIMIT 1),
                      l.price_eur,
                      (SELECT count(*) FROM price_events pe WHERE pe.listing_id=l.id AND pe.event='prezzo'),
                      (vp.riassunto->>'M')::int, COALESCE((vp.riassunto->>'R_finale')::int, (vp.riassunto->>'R')::int),
                      (vp.riassunto->>'margine')::int, (vp.riassunto->>'n')::int, vp.riassunto->>'metodo',
                      EXTRACT(EPOCH FROM now() - l.first_seen_at) / 86400,
                      EXTRACT(EPOCH FROM now() - COALESCE(l.deep_at, l.first_seen_at)) / 86400, vp.policy_version
               FROM listings l
               LEFT JOIN LATERAL (SELECT * FROM valutazioni_policy v WHERE v.listing_id=l.id ORDER BY v.computed_at DESC LIMIT 1) vp ON true
               WHERE l.id=%s
               ON CONFLICT (listing_id) DO NOTHING""", (lid,))
    except Exception as e:
        conn.rollback()
        log.info("misura vendita %s non registrata: %s", lid, str(e)[:120])


def riepilogo_misure(conn) -> dict:
    """Riepilogo per calibrare: quanto restano online le auto sparite e a che prezzo rispetto al nostro mercato."""
    r = conn.execute(
        """SELECT count(*) AS n,
                  percentile_cont(.5) WITHIN GROUP (ORDER BY giorni_online) AS giorni_online_mediana,
                  percentile_cont(.5) WITHIN GROUP (ORDER BY giorni_sul_sito) AS giorni_sul_sito_mediana,
                  percentile_cont(.5) WITHIN GROUP (ORDER BY prezzo_finale::numeric / NULLIF(mercato_m, 0)) AS prezzo_su_mercato,
                  avg((ribassi > 0)::int) AS quota_con_ribasso,
                  avg((giorni_sul_sito < 2)::int) AS quota_sparite_entro_2_giorni
           FROM misure_vendite WHERE sparita_at > now() - make_interval(days => %s)""",
        (int(os.environ.get("MISURA_GIORNI", "3")),)).fetchone()
    return {k: (round(float(v), 3) if v is not None and not isinstance(v, int) else v) for k, v in dict(r).items()}


# ------------------------------------------------------------------ controllo targa (auto senza numero)

TARGA_SYSTEM = """Guardi le foto di un annuncio di auto usata. Devi dire SOLO se la targa si legge in almeno una foto.
Non scrivere MAI i caratteri della targa. Rispondi solo con questo JSON:
{"targa": "visibile" | "coperta" | "non_inquadrata" | "incerta", "foto_da_internet": true | false}
- "visibile": i caratteri della targa si leggono in almeno una foto (anche se piccoli).
- "coperta": targa oscurata, sfocata, cancellata, coperta da adesivo, scritta, logo o emoji, oppure targa finta o di esempio.
- "non_inquadrata": nessuna foto mostra il davanti o il dietro dell'auto dove sta la targa.
- "incerta": foto troppo piccole o scure per dirlo.
- "foto_da_internet": true se sembrano foto di catalogo, di un concessionario o prese da internet."""


def controlla_targa(conn, lid: int) -> str:
    """Esito del controllo targa sulle foto già salvate dell'auto (mai la targa stessa)."""
    import base64
    from ..web import foto
    from ..ai import client as ai
    blocks = []
    for pos in range(foto.MAX_PER_AUTO):
        try:
            c = foto.fetch_one(conn, lid, pos)
        except Exception:
            c = None
        if c:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                       "data": base64.b64encode(c).decode()}})
    if not blocks:
        esito = "senza_foto"
    else:
        blocks.append({"type": "text", "text": "Si legge la targa in almeno una di queste foto?"})
        try:
            r = ai.ask_json("targa", ai.MODEL_FAST, TARGA_SYSTEM, blocks, max_tokens=120,
                            usage_sink=ai.db_usage_sink(conn), listing_id=lid)
            d = r.data if isinstance(r.data, dict) else {}
            esito = str(d.get("targa") or "incerta")
            if esito not in ("visibile", "coperta", "non_inquadrata", "incerta"):
                esito = "incerta"
            if d.get("foto_da_internet"):
                esito = "foto_internet"
        except Exception as e:
            log.warning("TR targa %s non controllata: %s", lid, str(e)[:150])
            esito = "errore"
    conn.execute("INSERT INTO controllo_targa (listing_id, esito, foto) VALUES (%s,%s,%s) ON CONFLICT (listing_id) "
                 "DO UPDATE SET esito=EXCLUDED.esito, foto=EXCLUDED.foto, checked_at=now()", (lid, esito, len(blocks)))
    conn.commit()
    return esito


def giro_targhe(conn, ids: list[int]) -> Counter:
    """Controlla la targa (una volta per auto; "errore" si riprova dopo un giorno) delle auto senza numero candidate."""
    from ..ai import client as ai
    stats: Counter = Counter()
    if not ids:
        return stats
    if not ai.available():
        stats["ai_non_configurata"] = len(ids)
        return stats
    if ai.budget_finito(conn):
        stats["budget_ai_finito"] = len(ids)
        return stats
    fatti = {r["listing_id"] for r in conn.execute(
        "SELECT listing_id FROM controllo_targa WHERE listing_id = ANY(%s) AND (esito <> 'errore' "
        "OR checked_at > now() - interval '1 day')", (list(ids),)).fetchall()}
    for lid in [i for i in ids if i not in fatti][:int(os.environ.get("TARGA_PER_GIRO", "40"))]:
        stats[controlla_targa(conn, lid)] += 1
    return stats


def esiti_targa(conn, ids: list[int]) -> dict:
    if not ids:
        return {}
    return {r["listing_id"]: r["esito"] for r in conn.execute(
        "SELECT listing_id, esito FROM controllo_targa WHERE listing_id = ANY(%s)", (list(ids),)).fetchall()}
