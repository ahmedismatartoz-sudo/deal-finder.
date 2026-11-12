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


def battito(conn) -> None:
    conn.execute("INSERT INTO tempo_reale (giro, ultimo_at, stats) VALUES ('worker_nuovi', now(), %s) "
                 "ON CONFLICT (giro) DO UPDATE SET ultimo_at=EXCLUDED.ultimo_at, stats=EXCLUDED.stats",
                 (json.dumps({"pid": os.getpid(), "host": socket.gethostname()[:60]}),))
    conn.commit()


def worker_attivo(conn=None) -> bool:
    """Per il sito: il giro "nuovi" lo fa il worker? (vedi TR_WORKER in cima al file). In dubbio: no (il sito gira)."""
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
        r = conn.execute("SELECT ultimo_at > now() - make_interval(mins => %s) AS fresco FROM tempo_reale "
                         "WHERE giro='worker_nuovi'", (int(fresco),)).fetchone()
        return bool(r and r["fresco"])
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


class _Fine:
    def __init__(self):
        self.ev = threading.Event()

    def __call__(self, *_a):
        log.warning("TR WORKER: richiesta di arresto, chiudo dopo il giro in corso")
        self.ev.set()


def _battiti(fine: _Fine) -> None:
    from ..db import connect
    while not fine.ev.is_set():
        try:
            with connect() as c:
                battito(c)
        except Exception as e:
            log.warning("TR WORKER battito non scritto: %s", str(e)[:150])
        fine.ev.wait(BATTITO_S)


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


def main(giri: int | None = None, dormi=None) -> None:
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
    except Exception:
        pass
    threading.Thread(target=_battiti, args=(fine,), daemon=True, name="tr-battito").start()
    from ..memoria import rilascia
    ultimo_completo, n = 0.0, 0
    while not fine.ev.is_set() and (giri is None or n < giri):
        n += 1
        t0 = time.time()
        completo = (t0 - ultimo_completo >= float(os.environ.get("TR_COMPLETO_MIN", "60")) * 60
                    and os.environ.get("TR_COMPLETO", "1") == "1")
        try:
            st = un_giro(completo)
            if st is not None and completo:
                ultimo_completo = t0
            if st is not None:
                log.warning("TR NUOVI (worker) in %.0fs: %s", time.time() - t0, dict(st))
        except Exception as e:
            log.warning("TR WORKER giro non riuscito: %s", str(e)[:300])
        rilascia("tempo reale worker")
        attesa = max(30.0, float(os.environ.get("TR_NUOVI_MIN", "5")) * 60 - (time.time() - t0))
        (dormi or fine.ev.wait)(attesa)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
