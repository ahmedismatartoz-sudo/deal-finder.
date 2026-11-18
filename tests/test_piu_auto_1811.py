"""18/11 (cliente: "più auto, fallo bene"): elenco senza il tetto fisso di 12.000 righe, protetto dalla memoria vera.

- il default di ELENCO_LIMITE è 30.000 (in pratica tutte le righe valide di LIST_SQL);
- si smette di leggere quando la memoria PREVISTA a fine calcolo (RSS + ELENCO_KB_DOPO per riga) supera
  ELENCO_MEMORIA_MB (450): riga ELENCO_TAGLIATO motivo=memoria, conteggi nel caricamento (SITO_FUNNEL);
- se il tetto taglia, si contano le righe rimaste fuori (stesse condizioni di LIST_SQL).
Le regole non cambiano: si leggono solo più righe della stessa query.
"""
from __future__ import annotations

import logging
import os
from collections import Counter

from dealfinder.web import app, scovo


class env:
    def __init__(self, **kv):
        self.kv, self.prima = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.prima[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.prima.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Ris(list):
    def fetchone(self):
        return self[0] if self else None


class Conn:
    """Righe con id da n a 1; il conteggio delle rimaste risponde con quelle sotto l'ultimo id."""
    def __init__(self, n):
        self.ids, self.chiamate, self.conteggi = list(range(n, 0, -1)), [], []

    def execute(self, sql, p=None):
        if sql.startswith("SET LOCAL"):
            return _Ris()
        if "count(*) AS n" in sql:
            self.conteggi.append(sql)
            return _Ris([{"n": sum(1 for i in self.ids if i < p["ultimo"])}])
        self.chiamate.append(p)
        sotto = [i for i in self.ids if p.get("ultimo") is None or i < p["ultimo"]][:p["lim"]]
        return _Ris({"id": i, "opens": 0} for i in sotto)

    def rollback(self):
        pass


class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.righe = []

    def emit(self, record):
        self.righe.append(record.getMessage())


def _con(f, **kv):
    vecchia_item, vecchia_rss = scovo.item, app._rss_mb
    scovo.item = lambda r: {"id": r["id"]}
    h = _Log()
    logging.getLogger("scovo").addHandler(h)
    try:
        with env(**kv):
            return f(h)
    finally:
        scovo.item, app._rss_mb = vecchia_item, vecchia_rss
        logging.getLogger("scovo").removeHandler(h)


def test_default_30000_tutte_le_righe():
    def f(h):
        app._rss_mb = lambda: 200.0
        st = Counter()
        items, *_ = app._leggi_elenco(Conn(25000), st)
        assert len(items) == 25000 and st["lista"] == 25000 and "lista_tagliata" not in st
        assert not any("ELENCO_TAGLIATO" in r for r in h.righe)
        assert st["lista_rss_mb"] == 200 and "lista_s" in st
    _con(f, ELENCO_LIMITE=None, ELENCO_PAGINA=None, ELENCO_MEMORIA_MB=None)


def test_tetto_taglia_e_conta_le_rimaste():
    def f(h):
        app._rss_mb = lambda: 200.0
        st = Counter()
        c = Conn(1500)
        items, *_ = app._leggi_elenco(c, st)
        assert len(items) == 1000 and st["lista_tagliata"] == 1 and st["lista_tagliata_tetto"] == 1
        assert st["lista_rimaste_fuori"] == 500 and c.conteggi
        assert "WHERE l.stage='approfondito'" in scovo.LIST_SQL and "l.stage='approfondito'" in c.conteggi[0]
        assert any("ELENCO_TAGLIATO motivo=tetto" in r and "rimaste fuori 500" in r for r in h.righe)
    _con(f, ELENCO_LIMITE="1000", ELENCO_PAGINA="300")


def test_memoria_ferma_la_lettura_prima_del_tetto():
    def f(h):
        mem = iter([300.0, 420.0, 449.0, 449.0, 449.0])
        app._rss_mb = lambda: next(mem, 449.0)
        st = Counter()
        items, *_ = app._leggi_elenco(Conn(20000), st)
        # pagine da 2.000: dopo la 1ª 300 + 3 MB, dopo la 2ª 420 + 6 MB, dopo la 3ª 449 + 9 MB > 450: fermo a 6.000
        assert len(items) == 6000 and st["lista_tagliata_memoria"] == 1 and st["lista_rimaste_fuori"] == 14000
        assert any("ELENCO_TAGLIATO motivo=memoria" in r for r in h.righe)
    _con(f, ELENCO_LIMITE=None, ELENCO_PAGINA=None, ELENCO_MEMORIA_MB=None, ELENCO_KB_DOPO=None)


def test_memoria_non_leggibile_non_ferma():
    def f(h):
        app._rss_mb = lambda: 0.0                 # fuori da Linux: niente protezione, vale il tetto
        st = Counter()
        items, *_ = app._leggi_elenco(Conn(5000), st)
        assert len(items) == 5000 and "lista_tagliata" not in st
    _con(f, ELENCO_LIMITE=None)


def test_conteggio_rimaste_non_riuscito_non_rompe():
    class Rotta(Conn):
        def execute(self, sql, p=None):
            if "count(*) AS n" in sql:
                raise RuntimeError("timeout")
            return super().execute(sql, p)

    def f(h):
        app._rss_mb = lambda: 200.0
        st = Counter()
        items, *_ = app._leggi_elenco(Rotta(800), st)
        assert len(items) == 500 and st["lista_tagliata"] == 1 and "lista_rimaste_fuori" not in st
        assert any("rimaste fuori ?" in r for r in h.righe)
    _con(f, ELENCO_LIMITE="500", ELENCO_PAGINA="200")
