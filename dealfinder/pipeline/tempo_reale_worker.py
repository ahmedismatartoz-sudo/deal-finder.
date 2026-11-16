"""12/11 (buchi della raccolta, controllo 1 §0.2 e §3.3): il TEMPO REALE "nuovi" anche come processo separato.

Il tempo reale girava solo dentro il sito: quando il sito va fuori memoria o viene ridistribuito si ferma (l'11/11
circa 4 h 10 min in tutto, ~950 annunci letti con ore di ritardo). Questo modulo lo fa girare in un processo suo:

    python -m dealfinder.pipeline.tempo_reale_worker

(un "Background Worker" di Render con lo stesso codice e le stesse variabili del cron). Stesso giro del sito
(tempo_reale.giro_nuovi, nuovi ogni TR_NUOVI_MIN minuti, completo ogni TR_COMPLETO_MIN), stesso lucchetto su Postgres
(con_lucchetto("nuovi"): mai due giri insieme), più il controllo dei telefoni delle auto appena scelte.

Il worker scrive un battito ogni 60 s nella tabella tempo_reale (riga 'worker_nuovi'). Il sito, con TR_WORKER=auto
(default), salta il suo giro "nuovi" finché il battito ha meno di TR_WORKER_FRESCO_MIN minuti (5): se il worker si
ferma, il sito riprende da solo al giro dopo. TR_WORKER=0: il sito gira sempre (il lucchetto evita comunque il doppio
lavoro); TR_WORKER=1: il sito non fa mai il giro "nuovi". Nessuna variabile nuova obbligatoria.

16/11 (fondamenta della velocità, FOND_VELOCITA.md): il sito si fa da parte con battito fresco E ultimo giro "nuovi"
da meno di TR_WORKER_GIRO_OK_MIN minuti (10); il Guardiano del giro veloce chiude un giro bloccato dopo 12 minuti; il
filo della lettura completa ha il suo Guardiano (120) e fa anche la ripresa AutoScout24 dopo un 429; al SIGTERM il
processo chiude entro TR_ARRESTO_MAX_S secondi (25).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import time

log = logging.getLogger("tempo_reale")

FRESCO_MIN = 5
BATTITO_S = 60
GIRO_OK_MIN = 10           # 16/11 revisione: ultimo giro "nuovi" partito da meno di 10 minuti


def battito(conn) -> None:
    conn.execute("INSERT INTO tempo_reale (giro, ultimo_at, stats) VALUES ('worker_nuovi', now(), %s) "
                 "ON CONFLICT (giro) DO UPDATE SET ultimo_at=EXCLUDED.ultimo_at, stats=EXCLUDED.stats",
                 (json.dumps({"pid": os.getpid(), "host": socket.gethostname()[:60]}),))
    conn.commit()


def worker_attivo(conn=None) -> bool:
    """Per il sito: il giro "nuovi" lo fa il worker? (vedi TR_WORKER in cima al file). In dubbio: no (il sito gira).
    Serve il battito fresco E un giro "nuovi" recente (vedi sotto)."""
    modo = (os.environ.get("TR_WORKER") or "auto").strip().lower()
    if modo == "0":
        return False
    if modo == "1":
        return True
    try:
        fresco = float(os.environ.get("TR_WORKER_FRESCO_MIN") or FRESCO_MIN)
    except ValueError:
        fresco = FRESCO_MIN
    try:
        if conn is None:
            from ..db import connect
            with connect() as c:
                return worker_attivo(c)
        # Vivo = battito fresco E ultimo giro "nuovi" partito da meno di TR_WORKER_GIRO_OK_MIN minuti (10): il battito
        # gira in un filo a parte e da solo non vede un giro bloccato. 16/11 revisione: ora è sicuro anche con i giri
        # lunghi, perché la lettura completa scrive la sua riga ('completo') e la ripresa dopo un 429 non è più nel
        # giro veloce (che resta sotto i 2 minuti); il Guardiano chiude un giro veloce bloccato dopo 12 minuti.
        try:
            giro_min = float(os.environ.get("TR_WORKER_GIRO_OK_MIN") or GIRO_OK_MIN)
        except ValueError:
            giro_min = GIRO_OK_MIN
        r = conn.execute("SELECT (SELECT ultimo_at > now() - make_interval(secs => %s) FROM tempo_reale "
                         "WHERE giro='worker_nuovi') AS fresco, (SELECT ultimo_at > now() - make_interval(secs => %s) "
                         "FROM tempo_reale WHERE giro='nuovi') AS giro_ok", (int(fresco * 60), int(giro_min * 60))).fetchone()
        return bool(r and r["fresco"] and r["giro_ok"])
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def ultimo_giro(conn) -> tuple:
    """(ultimo_at, pubblicate) dell'ultimo giro "nuovi" (scritto da giro_nuovi): al sito serve per aggiornare l'elenco
    quando il worker pubblica."""
    try:
        r = conn.execute("SELECT ultimo_at, stats FROM tempo_reale WHERE giro='nuovi'").fetchone()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None, 0
    if not r:
        return None, 0
    st = r["stats"]
    if isinstance(st, str):
        try:
            st = json.loads(st)
        except ValueError:
            st = {}
    return r["ultimo_at"], int((st or {}).get("pubblicate") or 0)


ARRESTO_MAX_S = 25


class _Fine:
    """SIGTERM: si accende l'evento di fine del worker e FERMA del tempo reale (i giri si fermano alla fase dopo, le
    attese si interrompono). Se dopo TR_ARRESTO_MAX_S secondi (25) il processo è ancora vivo esce comunque: Render
    concede 30 s prima del SIGKILL; quello che è già salvato resta nella coda (tr_coda) per il processo dopo."""

    def __init__(self, esci=None, tr=None):
        self.ev = threading.Event()
        self.esci = esci or (lambda: os._exit(0))
        self._tr = tr
        self.t_richiesta = None

    def __call__(self, *_a):
        if self.ev.is_set():
            return
        self.t_richiesta = time.time()
        log.warning("TR WORKER: richiesta di arresto, chiudo entro %s secondi", self.massimo())
        self.ev.set()
        try:
            tr = self._tr
            if tr is None:
                from . import tempo_reale as tr
            tr.FERMA.set()
        except Exception:
            pass
        self._timer = threading.Timer(self.massimo(), self._scaduto)
        self._timer.daemon = True
        self._timer.start()

    def annulla(self) -> None:
        """Arresto finito in tempo: niente uscita forzata."""
        t = getattr(self, "_timer", None)
        if t is not None:
            t.cancel()

    @staticmethod
    def massimo() -> float:
        try:
            return float(os.environ.get("TR_ARRESTO_MAX_S") or ARRESTO_MAX_S)
        except ValueError:
            return ARRESTO_MAX_S

    def _scaduto(self) -> None:
        log.error("TR WORKER: arresto non finito in %s secondi, esco subito (la coda resta nel database)", self.massimo())
        self.esci()


def _battiti(fine: _Fine) -> None:
    from ..db import connect
    while not fine.ev.is_set():
        try:
            with connect() as c:
                battito(c)
        except Exception as e:
            log.warning("TR WORKER battito non scritto: %s", str(e)[:150])
        fine.ev.wait(BATTITO_S)


GIRO_MAX_MIN = 12          # 16/11 revisione: il giro veloce dura < 2 minuti (prima 45); il completo ha il suo limite


class Guardiano:
    """12/11 revisione: un giro bloccato (rete appesa, database fermo) terrebbe il lucchetto "nuovi" per sempre e il
    sito non potrebbe riprendere. Se un giro dura più di TR_WORKER_GIRO_MAX_MIN minuti (45) il processo si chiude:
    la connessione si chiude, Postgres libera il lucchetto, Render riavvia il worker."""

    def __init__(self, esci=None, variabile: str = "TR_WORKER_GIRO_MAX_MIN", massimo_min: float = GIRO_MAX_MIN,
                 nome: str = "giro"):
        self.inizio = None
        self.esci = esci or (lambda: os._exit(3))
        self.variabile, self.massimo_min, self.nome = variabile, massimo_min, nome

    def limite_min(self) -> float:
        try:
            return float(os.environ.get(self.variabile) or self.massimo_min)
        except ValueError:
            return self.massimo_min

    def scaduto(self, ora: float | None = None) -> bool:
        lim = self.limite_min() * 60
        return self.inizio is not None and (ora if ora is not None else time.time()) - self.inizio > lim

    def controlla(self, ora: float | None = None) -> bool:
        if self.scaduto(ora):
            log.error("TR WORKER: %s bloccato da oltre %s minuti, chiudo il processo (lucchetto liberato)",
                      self.nome, self.limite_min())
            self.esci()
            return True
        return False

    def gira(self, fine, altri=()) -> None:
        """Sorveglia questo giro e gli `altri` guardiani (16/11: anche la lettura completa)."""
        while not fine.ev.is_set():
            for g in (self, *altri):
                if g.controlla():
                    break
            fine.ev.wait(30)


COMPLETO_MAX_MIN = 120     # 16/11: la lettura completa (~15 minuti) bloccata oltre 2 ore chiude il processo


def un_giro(completo: bool, tr=None) -> dict | None:
    from . import tempo_reale as _tr
    tr = tr or _tr
    st = tr.con_lucchetto("nuovi", tr.giro_nuovi, completo)
    if st is not None and st.get("pubblicate"):
        try:
            tr.con_lucchetto("telefoni", tr.controlla_telefoni)
        except Exception as e:
            log.warning("TR WORKER telefoni scelte: %s", str(e)[:200])
    return st


def _completo_a_parte(fine, guardiano: Guardiano | None = None, tr=None, adesso=time.time) -> None:
    """16/11 sera: la rilettura COMPLETA (15 minuti, 7.000 annunci quasi tutti già noti) gira in un filo suo, ogni
    TR_COMPLETO_VELOCE_MIN minuti (180), con un lucchetto suo: il giro veloce non si ferma più ad aspettarla.
    16/11 (fondamenta): sorvegliata da un Guardiano (TR_COMPLETO_GIRO_MAX_MIN, 120) e ogni TR_RIPRESA_CONTROLLO_MIN
    minuti (5) riprende la lettura completa AutoScout24 interrotta da un 429 (prima la faceva il giro veloce)."""
    if tr is None:
        from . import tempo_reale as tr
    from ..memoria import rilascia
    guardiano = guardiano or Guardiano()
    fine.ev.wait(float(os.environ.get("TR_COMPLETO_PRIMO_MIN", "20")) * 60)
    ultimo = None
    while not fine.ev.is_set():
        t0 = adesso()
        dovuto = ultimo is None or t0 - ultimo >= float(os.environ.get("TR_COMPLETO_VELOCE_MIN", "180")) * 60
        guardiano.inizio = time.time()
        try:
            if dovuto:
                st = tr.con_lucchetto("completo", tr.giro_nuovi, True)
                ultimo = t0
                if st is not None:
                    log.warning("TR COMPLETO (a parte) in %.0fs: %s", adesso() - t0, dict(st))
            else:
                st = tr.con_lucchetto("completo", tr.giro_ripresa_as)
                if st is not None:
                    log.warning("TR RIPRESA AUTOSCOUT (a parte) in %.0fs: %s", adesso() - t0, dict(st))
        except Exception as e:
            log.warning("TR COMPLETO non riuscito: %s", str(e)[:300])
        finally:
            guardiano.inizio = None
        rilascia("tempo reale completo")
        fine.ev.wait(float(os.environ.get("TR_RIPRESA_CONTROLLO_MIN", "5")) * 60)


def main(giri: int | None = None, dormi=None):
    """Giro veloce ogni TR_VELOCE_S secondi (90): solo il nuovo, dal più recente, fino alla prima pagina già vista.
    La rilettura completa gira a parte (_completo_a_parte)."""
    if not os.environ.get("DATABASE_URL"):
        log.warning("TR WORKER: DATABASE_URL non impostata, nulla da fare")
        return
    try:
        from .. import regole_log
        regole_log.registra("worker:tempo_reale")
    except Exception:
        pass
    fine = _Fine()
    try:
        signal.signal(signal.SIGTERM, fine)
        signal.signal(signal.SIGINT, fine)
    except Exception:
        pass
    threading.Thread(target=_battiti, args=(fine,), daemon=True, name="tr-battito").start()
    guardiano = Guardiano()
    g_completo = Guardiano(variabile="TR_COMPLETO_GIRO_MAX_MIN", massimo_min=COMPLETO_MAX_MIN, nome="lettura completa")
    threading.Thread(target=guardiano.gira, args=(fine, (g_completo,)), daemon=True, name="tr-guardiano").start()
    if giri is None and os.environ.get("TR_COMPLETO", "1") == "1":
        threading.Thread(target=_completo_a_parte, args=(fine, g_completo), daemon=True, name="tr-completo").start()
    from ..memoria import rilascia
    n = 0
    while not fine.ev.is_set() and (giri is None or n < giri):
        n += 1
        t0 = time.time()
        guardiano.inizio = time.time()
        try:
            st = un_giro(False)
            if st is not None:
                log.warning("TR NUOVI (worker) in %.0fs: %s", time.time() - t0, dict(st))
        except Exception as e:
            log.warning("TR WORKER giro non riuscito: %s", str(e)[:300])
        guardiano.inizio = None
        rilascia("tempo reale worker")
        if fine.ev.is_set():
            break
        attesa = max(20.0, float(os.environ.get("TR_VELOCE_S", "90")) - (time.time() - t0))
        (dormi or fine.ev.wait)(attesa)
    if fine.ev.is_set():
        log.warning("TR WORKER: arrestato in %.1fs dalla richiesta", time.time() - (fine.t_richiesta or time.time()))
        fine.annulla()
    return fine


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
