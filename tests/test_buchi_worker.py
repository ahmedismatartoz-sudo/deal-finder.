"""12/11 buchi della raccolta (§0.2, §3.3): tempo reale "nuovi" come processo separato, con il sito che si fa da parte."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.pipeline import tempo_reale_worker as W      # noqa: E402


class Conn:
    def __init__(self, fresco=None, nuovi=None):
        self.fresco, self.nuovi, self.sql = fresco, nuovi, []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

        class R:
            def __init__(s, r):
                s.r = r

            def fetchone(s):
                return s.r
        if "giro='worker_nuovi'" in sql and sql.startswith("SELECT"):
            return R(None if self.fresco is None else {"fresco": self.fresco})
        if "giro='nuovi'" in sql:
            return R(self.nuovi)
        return R(None)

    def commit(self):
        pass

    def rollback(self):
        pass


def _con_env(k, v, fn):
    vecchio = os.environ.get(k)
    try:
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
        return fn()
    finally:
        if vecchio is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = vecchio


def test_worker_attivo_secondo_interruttore_e_battito():
    assert _con_env("TR_WORKER", None, lambda: W.worker_attivo(Conn(fresco=True))) is True
    assert _con_env("TR_WORKER", None, lambda: W.worker_attivo(Conn(fresco=False))) is False
    assert _con_env("TR_WORKER", None, lambda: W.worker_attivo(Conn(fresco=None))) is False      # mai partito
    assert _con_env("TR_WORKER", "0", lambda: W.worker_attivo(Conn(fresco=True))) is False
    assert _con_env("TR_WORKER", "1", lambda: W.worker_attivo(Conn(fresco=None))) is True

    class Rotta(Conn):
        def execute(self, *a):
            raise RuntimeError("db giù")
    assert _con_env("TR_WORKER", None, lambda: W.worker_attivo(Rotta())) is False                # in dubbio gira il sito


def test_battito_e_ultimo_giro():
    c = Conn(nuovi={"ultimo_at": datetime(2026, 11, 12, tzinfo=timezone.utc), "stats": {"pubblicate": 3}})
    W.battito(c)
    assert "worker_nuovi" in c.sql[0][0]
    quando, pubb = W.ultimo_giro(c)
    assert pubb == 3 and quando.year == 2026
    assert W.ultimo_giro(Conn()) == (None, 0)


def test_un_giro_usa_il_lucchetto_e_i_telefoni():
    chiamate = []

    class TR:
        @staticmethod
        def con_lucchetto(nome, fn, *a):
            chiamate.append((nome, a))
            return {"pubblicate": 2} if nome == "nuovi" else {}

        giro_nuovi = object()
        controlla_telefoni = object()
    assert W.un_giro(True, tr=TR) == {"pubblicate": 2}
    assert chiamate == [("nuovi", (True,)), ("telefoni", ())]

    class Occupato(TR):
        @staticmethod
        def con_lucchetto(nome, fn, *a):
            chiamate.append(nome)
            return None                                  # il sito (o un altro worker) sta già girando
    chiamate.clear()
    assert W.un_giro(False, tr=Occupato) is None and chiamate == ["nuovi"]


def test_senza_database_esce_subito():
    assert _con_env("DATABASE_URL", None, lambda: W.main(giri=1)) is None
