"""12/11 buchi della raccolta (§2.3, §4.1): mercato_nord fuori memoria (11/11, 4/11) e fermato a 12 ore (10/11).

Dai log del cron mercato: mercato_nord parte nello STESSO processo subito dopo l'addestramento del modello (~270 MB)
e cresce fino a 532 MB; il 10/11 parte alle 06:57 (cron dalle 01:13) e Render lo ferma alle 12 ore. Ora: processo nuovo
(os.execv), fermata pulita su memoria/tempo con ripresa dai passi, riga "fatto" solo se finito davvero."""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dealfinder.pipeline import collect as C     # noqa: E402


@contextmanager
def _env(**kv):
    vecchi = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in vecchi.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_fermata_per_tempo():
    with _env(CRON_T0=str(time.time() - 10 * 3600), NORD_ORE_MAX=None):
        st = Counter()
        assert C.nord_da_fermare(st, rss=lambda: 100) and st["mercato_nord:interrotto_tempo"] == 1


def test_fermata_per_memoria_dopo_il_rilascio():
    with _env(CRON_T0=str(time.time()), NORD_RSS_MB=None, MEMORIA_RILASCIA="0"):
        st = Counter()
        assert C.nord_da_fermare(st, rss=lambda: 470) and st["mercato_nord:interrotto_memoria"] == 1
        st = Counter()
        assert not C.nord_da_fermare(st, rss=lambda: 300) and not st


class _Conn:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append(sql)

    def commit(self):
        pass


def _con_finto(fn, stats):
    import dealfinder.db as D
    eseguite = []
    vecchio_run, vecchio_conn = C.run, D.connect

    @contextmanager
    def connect():
        yield _Conn(eseguite)
    try:
        C.run = lambda mode: Counter(stats)
        D.connect = connect
        fn()
    finally:
        C.run, D.connect = vecchio_run, vecchio_conn
    return eseguite


def test_riga_fatto_solo_se_finito():
    fatte = _con_finto(C.fase_mercato_nord, {"salvati": 10, "mercato_nord:interrotto": 1})
    assert not fatte
    fatte = _con_finto(C.fase_mercato_nord, {"salvati": 10})
    assert len(fatte) == 1 and "collect:mercato_nord" not in fatte[0] and "INSERT INTO job_runs" in fatte[0]


def test_massiva_svuota_i_gia_visti_per_fascia():
    from test_buchi_incrementale import SitoAS
    from dealfinder.collectors.autoscout import AutoScoutCollector
    annunci = [(f"a{i}", 1000 + (i * 7) % 3000) for i in range(600)]
    c = AutoScoutCollector(client=SitoAS(annunci))
    c.min_delay_s = c.max_delay_s = 0
    c.svuota_visti = True
    with _env(PASSI_JOB=None):
        letti = list(c.search_massiva({"min_price": 1000, "max_price": 4000, "step": 1000, "venditori": [("P", "privato")]}))
    assert len({l.source_id for l in letti}) == 600 and len(c.visti) == 0
