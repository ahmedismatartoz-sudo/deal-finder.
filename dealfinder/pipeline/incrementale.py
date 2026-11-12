"""12/11 (buchi della raccolta, controllo 1 §0.1): lettura INCREMENTALE del giro della zona.

Ogni giro rileggeva ~411.000 annunci (3 h 50 min) per trovarne di nuovi lo 0,5%. Le ricerche sono già in ordine dal più
recente (AutoScout24 sort=age, Subito sort=datedesc): in lettura incrementale, per ogni fascia, ci si ferma quando si
incontrano K pagine DI FILA in cui ogni annuncio che conta (in zona e nel prezzo del giro) è già nel database con lo
stesso prezzo ("già visto e invariato"). Le pagine dopo sono più vecchie e già lette.

Il giro COMPLETO resta, più raro: ogni ZC_COMPLETO_ORE ore (12) per la zona e TORINO_COMPLETO_ORE (12) per Torino; serve
per i ribassi degli annunci vecchi (che non risalgono in cima), per aggiornare "visto" (last_seen_at) di tutti e per
il controllo degli annunci spariti, che gira solo dopo un giro completo.

Interruttori (default nel codice): ZC_INCREMENTALE=1 (0 = sempre completo), ZC_INCR_PAGINE (K, 2), ZC_INCR_PASSO
(fasce AutoScout24 in lettura incrementale, 2.000 €: la fascia non serve stretta perché si leggono solo le prime pagine;
se una fascia ha 20 pagine piene di nuovi si divide come prima). Subito profondo e focus Subito non girano nel giro
incrementale (ogni fascia è quasi sempre una pagina sola: nessun risparmio); i nuovi di Subito arrivano dalla lettura
"recenti" della regione, che si ferma allo stesso modo.
"""
from __future__ import annotations

import logging
import os
import threading
from array import array
from bisect import bisect_left
from collections import Counter

log = logging.getLogger("incrementale")

K_PAGINE = 2
PASSO_AS = 2000
COMPLETO_ORE = 12


def _int(nome: str, default: int) -> int:
    try:
        return int(os.environ.get(nome) or default)
    except ValueError:
        return default


def attivo() -> bool:
    return os.environ.get("ZC_INCREMENTALE", "1") == "1"


def k_pagine() -> int:
    return max(1, _int("ZC_INCR_PAGINE", K_PAGINE))


def passo_as() -> int:
    return max(250, _int("ZC_INCR_PASSO", PASSO_AS))


def _chiave(fonte: str, sid, prezzo) -> int:
    return hash((fonte, str(sid), int(prezzo or 0)))


class Noti:
    """Annunci già nel database, come impronte (fonte, id, prezzo) in un array ordinato di interi (8 byte l'uno:
    ~3 MB per 400.000 annunci, niente stringhe in memoria). Sicuro tra thread: dopo la costruzione si legge soltanto."""

    def __init__(self, coppie=(), k: int | None = None, rilevante=None):
        self._a = array("q", sorted(_chiave(f, s, p) for f, s, p in coppie))
        self.k = k or k_pagine()
        self.rilevante = rilevante            # funzione listing -> bool (in zona e nel prezzo); None = tutti
        self.stats: Counter = Counter()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._a)

    @classmethod
    def carica(cls, conn, province: list, fonti=("subito", "autoscout24"), k: int | None = None, rilevante=None) -> "Noti":
        """Annunci attivi delle province della zona. Con psycopg: cursore lato server (a pezzi, poca memoria)."""
        sql = ("SELECT source, source_id, price_eur FROM listings WHERE status = 'attivo' AND source = ANY(%s) "
               "AND upper(province) = ANY(%s)")
        par = (list(fonti), [p.upper() for p in province])
        chiavi = array("q")
        try:
            cur = conn.cursor(name="incrementale_noti")
            cur.itersize = 20000
            cur.execute(sql, par)
            righe = cur
        except (AttributeError, TypeError):
            righe = conn.execute(sql, par).fetchall()
        for r in righe:
            chiavi.append(_chiave(r["source"], r["source_id"], r["price_eur"]))
        try:
            righe.close()
        except Exception:
            pass
        n = cls((), k, rilevante)
        n._a = array("q", sorted(chiavi))
        return n

    def noto(self, fonte: str, sid, prezzo) -> bool:
        x = _chiave(fonte, sid, prezzo)
        i = bisect_left(self._a, x)
        return i < len(self._a) and self._a[i] == x

    def pagina_nota(self, listings) -> bool:
        """True se ogni annuncio che conta della pagina è già noto con lo stesso prezzo (pagina senza annunci che
        contano = nota: non c'è niente da prendere)."""
        for l in listings:
            if l is None or not getattr(l, "price_eur", None):
                continue
            if self.rilevante is not None and not self.rilevante(l):
                continue
            if not self.noto(l.source, l.source_id, l.price_eur):
                return False
        return True

    def conta(self, chiave: str, n: int = 1) -> None:
        with self._lock:
            self.stats[chiave] += n


class Arresto:
    """Per UNA fascia: dopo ogni pagina dice se fermarsi (K pagine note di fila)."""

    def __init__(self, noti: Noti):
        self.noti, self.fila = noti, 0

    def pagina(self, listings) -> bool:
        self.noti.conta("pagine")
        if self.noti.pagina_nota(listings):
            self.fila += 1
        else:
            self.fila = 0
        if self.fila >= self.noti.k:
            self.noti.conta("fasce_fermate")
            return True
        return False


def zona_rilevante(province: list, pmin: int, pmax: int):
    """Annunci che il giro salverebbe: provincia nella zona del salvataggio (Milano + Torino, come tempo_reale._salva,
    più le province del giro) e prezzo nel giro. Gli altri (fuori zona, fuori prezzo, senza provincia) non decidono."""
    from ..zone import MILANO, TORINO
    zona = {p.upper() for p in list(MILANO) + list(TORINO) + list(province)}

    def f(l) -> bool:
        pv = (getattr(l, "province", None) or "").upper()
        return bool(pv) and pv in zona and pmin <= (l.price_eur or 0) <= pmax
    return f


def completo_dovuto(nome: str, ore_default: int = COMPLETO_ORE, var_ore: str = "ZC_COMPLETO_ORE") -> bool:
    """Il giro `nome` deve essere COMPLETO? Sì se la lettura incrementale è spenta o se l'ultimo giro completo riuscito
    ("<nome>:completo" in job_runs) è più vecchio di `var_ore` ore. In dubbio (database non leggibile): completo."""
    if not attivo():
        return True
    try:
        from .ciclo import _due
        return _due(nome + ":completo", _int(var_ore, ore_default))
    except Exception as e:
        log.warning("INCREMENTALE %s: ultimo giro completo non letto (%s): giro completo", nome, str(e)[:120])
        return True


def segna_completo(nome: str, esito: dict) -> None:
    try:
        from ..db import connect, log_job
        with connect() as c:
            log_job(c, nome + ":completo")(True, {k: v for k, v in esito.items() if isinstance(v, (int, float, str))})
    except Exception as e:
        log.warning("INCREMENTALE %s: giro completo non registrato: %s", nome, str(e)[:120])
