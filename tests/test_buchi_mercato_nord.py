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


def test_chiave_dei_passi_per_ciclo():
    class Cn:
        def __init__(self, n):
            self.n = n

        def execute(self, sql, p=None):
            n = self.n

            class R:
                def fetchone(s):
                    return {"n": n}
            return R()
    assert C.chiave_ciclo_nord(Cn(0)) == "collect:mercato_nord:ciclo1"
    assert C.chiave_ciclo_nord(Cn(7)) == "collect:mercato_nord:ciclo8"

    class Rotta:
        def execute(self, *a):
            raise RuntimeError("giù")
    assert C.chiave_ciclo_nord(Rotta()).startswith("collect:mercato_nord:20")


def _nord_finto(interrompi_dopo: int | None):
    """run("mercato_nord") vero, con database, AutoScout24 e passi finti. Ritorna (righe job_runs chiuse, passi fatti,
    passi.finito chiamato?)."""
    import dealfinder.collectors.autoscout as A
    import dealfinder.pipeline.passi as PS
    import dealfinder.pricing.train as TR
    from dealfinder.core.models import Listing
    righe, fatti, finito = [], set(), []

    class Conn:
        def execute(self, sql, p=None):
            class R:
                def fetchall(s):
                    return []

                def fetchone(s):
                    return None
            return R()

        def commit(self):
            pass

        def rollback(self):
            pass

    @contextmanager
    def connect():
        yield Conn()

    def log_job(conn, nome):
        return lambda ok, st: righe.append((nome, ok))

    def search_massiva(self, query):
        for a in range(query["min_price"], query["max_price"], query["step"]):
            chiave = f"as:{a}"
            if PS.fatto(chiave):
                continue
            for i in range(250):
                yield Listing(source="autoscout24", source_id=f"{a}-{i}", url="u", price_eur=a + i, province="VR",
                              make="fiat", model="panda", year=2015, mileage_km=50000)
                if self.stop:
                    return
            PS.segna(chiave)

    vecchi = (C.connect, C.log_job, C.upsert_listing, A.AutoScoutCollector.search_massiva, PS.fatto, PS.segna, PS.finito,
              TR.load_active, C.nord_da_fermare, A.GIA_SALVATI_MAX)
    letti = [0]

    def ferma(stats, *a, **k):
        letti[0] += 200
        if interrompi_dopo is not None and letti[0] >= interrompi_dopo:
            stats["mercato_nord:interrotto"] = 1
            return True
        return False
    try:
        C.connect, C.log_job = connect, log_job
        C.upsert_listing = lambda conn, l: (1, "invariato")
        A.AutoScoutCollector.search_massiva = search_massiva
        PS.fatto, PS.segna = (lambda k: k in fatti), (lambda k: fatti.add(k))
        PS.finito = lambda j: finito.append(j)
        TR.load_active = lambda conn: None
        C.nord_da_fermare = ferma
        with _env(NORD_MAX_PREZZO="3500", NORD_PASSO="1000", PASSI_JOB=None, COLLECT_JOB="collect:mercato",
                  AUTOSCOUT_CAP=None, AUTOSCOUT_RAGGIO=None):
            st = C.run("mercato_nord")
    finally:
        (C.connect, C.log_job, C.upsert_listing, A.AutoScoutCollector.search_massiva, PS.fatto, PS.segna, PS.finito,
         TR.load_active, C.nord_da_fermare, A.GIA_SALVATI_MAX) = vecchi
    return st, righe, fatti, finito


def test_mercato_nord_interrotto_non_risulta_fatto_e_riprende_nello_stesso_ciclo():
    st, righe, fatti, finito = _nord_finto(interrompi_dopo=600)
    assert st["mercato_nord:interrotto"] == 1
    assert righe == [("collect:mercato_nord:giro", False)]        # mai "collect:mercato_nord", mai ok=True
    assert fatti == {"as:500", "as:1500"} and not finito             # passi fatti tenuti, job non chiuso
    # il ciclo non avanza: nessuna riga "collect:mercato_nord" ok -> stessa chiave dei passi la notte dopo
    assert all(n != "collect:mercato_nord" for n, _ in righe)
    # giro completo: riga del giro ok e passi chiusi
    st2, righe2, _f, finito2 = _nord_finto(interrompi_dopo=None)
    assert righe2 == [("collect:mercato_nord:giro", True)] and finito2
