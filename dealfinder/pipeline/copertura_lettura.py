"""25/11 (cliente: "leggere TUTTI gli annunci della zona"): misura della copertura della lettura.

A fine di ogni giro di raccolta una riga di log, SOLO conteggi (mai dati degli annunci o dei venditori):

    COPERTURA_LETTURA {"giro": "...", "fonti": {"subito": {...}, "autoscout24": {...}}}

Per ogni fonte: ricerche fatte, totale dichiarato dal portale (somma delle ricerche lette fino in fondo o tagliate; null
se il portale non lo mostra), pagine lette, annunci letti, annunci nuovi, motivi di arresto (quante ricerche per motivo).
Per ogni ricerca gli stessi numeri: tutte se sono poche (DETTAGLIO_MAX), altrimenti raggruppate (provincia / centro) più
l'elenco di quelle NON lette fino in fondo.

Motivi di arresto: fine_risultati, gia_visto (lettura incrementale), data (annunci più vecchi della data chiesta),
tetto_pagine (tetto nostro), tetto_portale (il portale non mostra oltre e la ricerca non si divide più), divisa (troppi
risultati: letta a pezzi, i pezzi sono contati a parte), errore, blocco (403/429 o pagina di blocco), tempo.

Uso: m = apri("nome")  ->  i collettori chiamano nota(...) (non fanno nulla se nessun giro è aperto in questo filo)
->  chiudi(m, nuovi={"subito": n, ...}) scrive la riga. Non solleva mai eccezioni: la misura non ferma la raccolta.
"""
from __future__ import annotations

import json
import logging
import threading
import time

log = logging.getLogger("copertura_lettura")

DETTAGLIO_MAX = 30         # fino a tante ricerche si scrivono tutte; oltre: gruppi + quelle tagliate
TAGLIATE_MAX = 30
MOTIVI_COMPLETI = ("fine_risultati", "divisa")
_loc = threading.local()


class Misura:
    def __init__(self, giro: str):
        self.giro, self.t0 = giro, time.time()
        self.ricerche: dict = {}          # (fonte, ricerca) -> conteggi
        self._lock = threading.Lock()

    def nota(self, fonte: str, ricerca: str, gruppo: str | None = None, pagine: int = 0, letti: int = 0,
             nuovi: int = 0, totale=None, motivo: str | None = None) -> None:
        with self._lock:
            r = self.ricerche.setdefault((fonte, str(ricerca)), {"gruppo": gruppo or "", "totale": None, "pagine": 0,
                                                                  "letti": 0, "nuovi": 0, "motivo": None})
            r["pagine"] += int(pagine or 0)
            r["letti"] += int(letti or 0)
            r["nuovi"] += int(nuovi or 0)
            if isinstance(totale, (int, float)) and not isinstance(totale, bool):
                r["totale"] = int(totale)
            if motivo:
                r["motivo"] = motivo
            if gruppo and not r["gruppo"]:
                r["gruppo"] = gruppo

    @staticmethod
    def _somma(righe: list) -> dict:
        foglie = [r for r in righe if r["motivo"] != "divisa"]       # una ricerca divisa è contata nei suoi pezzi
        tot = [r["totale"] for r in foglie if r["totale"] is not None]
        motivi: dict = {}
        for r in righe:
            m = r["motivo"] or "non_finita"
            motivi[m] = motivi.get(m, 0) + 1
        return {"ricerche": len(righe), "totale_dichiarato": sum(tot) if tot else None,
                "pagine": sum(r["pagine"] for r in righe), "letti": sum(r["letti"] for r in righe),
                "nuovi": sum(r["nuovi"] for r in righe), "motivi": motivi}

    def riepilogo(self, nuovi: dict | None = None, extra: dict | None = None) -> dict:
        with self._lock:
            voci = [(f, k, dict(r)) for (f, k), r in self.ricerche.items()]
        fonti: dict = {}
        for fonte in sorted({f for f, _, _ in voci} | set(nuovi or {})):
            righe = [(k, r) for f, k, r in voci if f == fonte]
            d = self._somma([r for _, r in righe])
            if nuovi and nuovi.get(fonte) is not None:
                d["nuovi"] = int(nuovi[fonte])            # dal salvataggio (evento "nuovo"), più sicuro
            if 0 < len(righe) <= DETTAGLIO_MAX:
                d["dettaglio"] = {k: {x: r[x] for x in ("totale", "pagine", "letti", "nuovi", "motivo")}
                                  for k, r in righe}
            elif righe:
                gruppi: dict = {}
                for _, r in righe:
                    gruppi.setdefault(r["gruppo"] or "-", []).append(r)
                d["gruppi"] = {g: self._somma(rr) for g, rr in sorted(gruppi.items())}
                tagliate = [(k, r) for k, r in righe if (r["motivo"] or "non_finita") not in MOTIVI_COMPLETI + ("gia_visto",)]
                d["tagliate"] = {k: {x: r[x] for x in ("totale", "pagine", "letti", "motivo")}
                                 for k, r in tagliate[:TAGLIATE_MAX]}
                if len(tagliate) > TAGLIATE_MAX:
                    d["tagliate_altre"] = len(tagliate) - TAGLIATE_MAX
            fonti[fonte] = d
        out = {"giro": self.giro, "durata_s": round(time.time() - self.t0), "fonti": fonti}
        if extra:
            out.update(extra)
        return out


def _pila() -> list:
    p = getattr(_loc, "pila", None)
    if p is None:
        p = _loc.pila = []
    return p


def corrente() -> Misura | None:
    p = _pila()
    return p[-1] if p else None


def apri(giro: str) -> Misura:
    m = Misura(giro)
    _loc.pila = [m]            # un giro alla volta per filo: una misura rimasta aperta da un giro fallito si butta
    return m


def usa(m: Misura | None):
    """Per i fili di servizio (letture in parallelo): la misura del filo che li ha lanciati. Ritorna quella di prima."""
    prima = list(_pila())
    _loc.pila = [m] if m is not None else []
    return prima


def ripristina(prima) -> None:
    _loc.pila = list(prima or [])


def nota(fonte: str, ricerca: str, **kw) -> None:
    try:
        m = corrente()
        if m is not None:
            m.nota(fonte, ricerca, **kw)
    except Exception:
        pass


def motivo_http(codice) -> str:
    return "blocco" if codice in (403, 429) else "errore"


def chiudi(m: Misura | None, nuovi: dict | None = None, extra: dict | None = None) -> dict | None:
    """Scrive la riga COPERTURA_LETTURA e toglie la misura dal filo. Mai eccezioni."""
    try:
        p = _pila()
        if m in p:
            del p[p.index(m):]
        if m is None:
            return None
        r = m.riepilogo(nuovi, extra)
        log.warning("COPERTURA_LETTURA %s", json.dumps(r, ensure_ascii=False, default=str))
        return r
    except Exception as e:
        log.warning("COPERTURA_LETTURA non scritta: %s", type(e).__name__)
        return None
