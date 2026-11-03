"""Lavori una tantum segnati "fatti" solo a lavoro finito (pipeline/ciclo.inizia / una_volta_sicura), con un
database finto: un crash a metà li fa rifare al giro dopo, un lavoro riuscito non si rifà."""
from contextlib import contextmanager

from dealfinder.pipeline import ciclo


class _Ris:
    def __init__(self, riga):
        self.riga = riga

    def fetchone(self):
        return self.riga


class FintoDB:
    """job_runs in memoria + lucchetti di sessione (si liberano quando la connessione muore)."""

    def __init__(self):
        self.righe, self.lucchetti, self.prossimo = [], {}, 1

    def connetti(self):
        db = self

        @contextmanager
        def cm():
            c = FintaConn(db)
            try:
                yield c
            finally:
                c.chiudi()
        return cm()


class FintaConn:
    def __init__(self, db):
        self.db, self.aperta = db, True

    def chiudi(self):                  # alla chiusura (o alla morte del processo) Postgres libera i lucchetti
        self.aperta = False
        for k in [k for k, v in self.db.lucchetti.items() if v is self]:
            del self.db.lucchetti[k]

    def commit(self):
        pass

    def execute(self, sql, p=()):
        db = self.db
        s = " ".join(sql.split())
        if s.startswith("SELECT pg_try_advisory_lock"):
            if p[0] in db.lucchetti and db.lucchetti[p[0]] is not self:
                return _Ris({"preso": False})
            db.lucchetti[p[0]] = self
            return _Ris({"preso": True})
        if s.startswith("SELECT pg_advisory_unlock"):
            if db.lucchetti.get(p[0]) is self:
                del db.lucchetti[p[0]]
            return _Ris({"pg_advisory_unlock": True})
        if s.startswith("SELECT 1 FROM job_runs WHERE job=%s AND ok LIMIT 1"):
            return _Ris({"?column?": 1} if any(r["job"] == p[0] and r["ok"] for r in db.righe) else None)
        if s.startswith("SELECT count(*) AS n FROM job_runs WHERE job=%s AND ok IS NOT TRUE"):
            return _Ris({"n": sum(r["job"] == p[0] and r["ok"] is not True for r in db.righe)})
        if s.startswith("UPDATE job_runs SET finished_at=now(), ok=false, stats='{\"interrotto\": true}'"):
            for r in db.righe:
                if r["job"] == p[0] and r["finito"] is None:
                    r.update(finito=True, ok=False, stats={"interrotto": True})
            return _Ris(None)
        if s.startswith("INSERT INTO job_runs (job) VALUES (%s) RETURNING id"):
            db.righe.append({"id": db.prossimo, "job": p[0], "ok": None, "finito": None, "stats": None})
            db.prossimo += 1
            return _Ris({"id": db.prossimo - 1})
        if s.startswith("UPDATE job_runs SET finished_at=now(), ok=%s, stats=%s WHERE id=%s"):
            for r in db.righe:
                if r["id"] == p[2]:
                    r.update(finito=True, ok=p[0], stats=p[1])
            return _Ris(None)
        raise AssertionError("SQL non previsto nel finto: " + s)


def test_riuscito_non_si_rifa():
    db, fatti = FintoDB(), []
    for _ in range(3):
        lav = ciclo.inizia("valnuove:v1", connetti=db.connetti)
        if lav:
            fatti.append(1)
            lav.fatto({"n": 5})
    assert fatti == [1]
    assert [r["ok"] for r in db.righe] == [True] and not db.lucchetti


def test_crash_a_meta_si_rifa_al_giro_dopo():
    db = FintoDB()
    lav = ciclo.inizia("riparadanni:v2", connetti=db.connetti)
    assert lav and db.righe[0]["ok"] is None and db.righe[0]["finito"] is None   # iniziato, non fatto
    lav.conn.chiudi()                           # il processo muore a metà: niente fatto() né fallito()
    lav2 = ciclo.inizia("riparadanni:v2", connetti=db.connetti)
    assert lav2, "dopo un crash il lavoro va rifatto"
    assert db.righe[0]["ok"] is False and db.righe[0]["stats"] == {"interrotto": True}
    lav2.fatto()
    assert ciclo.inizia("riparadanni:v2", connetti=db.connetti) is None
    assert [r["ok"] for r in db.righe] == [False, True]


def test_errore_segnato_fallito_e_rifatto():
    db, giri = FintoDB(), []

    def lavoro(fallisce):
        lav = None
        try:
            lav = ciclo.inizia("soglia7k:a", connetti=db.connetti)
            if not lav:
                return
            giri.append(fallisce)
            if fallisce:
                raise RuntimeError("memoria finita")
            lav.fatto()
        except Exception as e:
            if lav:
                lav.fallito(e)
    lavoro(True)
    assert db.righe[0]["ok"] is False and "memoria finita" in db.righe[0]["stats"] and not db.lucchetti
    lavoro(False)
    lavoro(False)
    assert giri == [True, False]


def test_mai_due_insieme():
    db = FintoDB()
    a = ciclo.inizia("valore_reale_notte:20261103", connetti=db.connetti)
    assert a and ciclo.inizia("valore_reale_notte:20261103", connetti=db.connetti) is None   # l'altro processo salta
    a.fatto()


def test_tentativi_massimi_niente_ciclo_di_crash():
    db = FintoDB()
    for _ in range(3):
        ciclo.inizia("incompleti:x", connetti=db.connetti).conn.chiudi()
    assert ciclo.inizia("incompleti:x", connetti=db.connetti) is None
    assert ciclo.inizia("incompleti:x", tentativi_max=5, connetti=db.connetti)


def test_con_with():
    db = FintoDB()
    try:
        with ciclo.una_volta_sicura("rivcosti:1", connetti=db.connetti) as fare:
            assert fare
            raise ValueError("a metà")
    except ValueError:
        pass
    with ciclo.una_volta_sicura("rivcosti:1", connetti=db.connetti) as fare:
        assert fare
    with ciclo.una_volta_sicura("rivcosti:1", connetti=db.connetti) as fare:
        assert not fare
    assert [r["ok"] for r in db.righe] == [False, True] and not db.lucchetti


def test_lavori_del_sito_che_cambiano_dati_usano_inizia():
    import os
    src = open(os.path.join(os.path.dirname(__file__), "..", "dealfinder", "web", "app.py"), encoding="utf-8").read()
    for job in ("valore_reale_notte:", "soglia7k:", "riparadanni:", "rivcosti:", "valnuove:", "incompleti:"):
        assert f'_una_volta("{job}' not in src and f'_una_volta(f"{job}' not in src, job
        assert f'inizia("{job}' in src or f'inizia(f"{job}' in src, job
