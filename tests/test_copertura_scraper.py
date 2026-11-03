"""3/11: copertura degli scraper Subito e AutoScout24 (nessuna auto persa, nessun dato perso), con dati finti."""
import json


class _Resp:
    def __init__(self, code=200, text="", url="", data=None):
        self.status_code, self.text, self.url = code, text, url
        self._data = data
        self.headers = {}

    def json(self):
        return self._data


class _Cur:
    def __init__(self, rows=None, rowcount=0):
        self._rows, self.rowcount = rows or [], rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """Finta connessione: registra le query; `risposte` = lista di (frammento SQL, righe)."""
    def __init__(self, risposte=()):
        self.sql, self.risposte = [], list(risposte)
        self.commits = self.rollbacks = 0

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        for frammento, righe in self.risposte:
            if frammento in sql:
                return _Cur(righe)
        return _Cur([])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _pagina(item):
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": {"pageProps": {"ad": item}}})}</script></html>'


# ------------------------------------------------------------------ fix 1: 'scomparso' solo con prova

def test_subito_stato_con_prova():
    from dealfinder.pipeline.tempo_reale import subito_stato_con_prova
    url = "https://www.subito.it/auto/fiat-panda-milano-561234567.htm"

    class C:
        def __init__(self, r):
            self.r = r

        def get(self, u):
            if isinstance(self.r, Exception):
                raise self.r
            return self.r
    ad = {"urn": "id:ad:561234567:list:1", "subject": "Fiat Panda"}
    assert subito_stato_con_prova(C(_Resp(404)), url) == "scomparso"
    assert subito_stato_con_prova(C(_Resp(410)), url) == "scomparso"
    assert subito_stato_con_prova(C(_Resp(200, "<html>ricerca</html>", "https://www.subito.it/annunci-lombardia/vendita/auto/")), url) == "scomparso"
    # titolo cambiato: altro indirizzo, stesso numero, dati dell'annuncio presenti -> attivo
    assert subito_stato_con_prova(C(_Resp(200, _pagina(ad), "https://www.subito.it/auto/fiat-panda-1-2-easy-milano-561234567.htm")), url) == "attivo"
    assert subito_stato_con_prova(C(_Resp(200, "<p>Questo annuncio è scaduto</p>", url)), url) == "scomparso"
    # niente prova: nessuna decisione
    assert subito_stato_con_prova(C(_Resp(200, "<html>pagina strana</html>", url)), url) == "errore"
    assert subito_stato_con_prova(C(_Resp(403, "blocked", url)), url) == "errore"
    assert subito_stato_con_prova(C(_Resp(429, "", url)), url) == "errore"
    assert subito_stato_con_prova(C(TimeoutError("lento")), url) == "errore"


def test_stesso_annuncio_con_titolo_cambiato():
    from dealfinder.pipeline.tempo_reale import _stesso_annuncio
    assert _stesso_annuncio("https://www.subito.it/auto/golf-milano-561234567.htm",
                            "https://www.subito.it/auto/golf-1-6-tdi-milano-561234567.htm")
    assert not _stesso_annuncio("https://www.subito.it/auto/golf-milano-561234567.htm",
                                "https://www.subito.it/annunci-lombardia/vendita/auto/")


def test_scomparsi_solo_con_prova():
    from dealfinder.pipeline.verify import scomparsi_con_prova
    rows = [{"id": i, "url": f"https://www.subito.it/auto/x-{100000 + i}.htm", "price_eur": 1000} for i in range(1, 5)]
    conn = _Conn([("FROM listings", rows)])
    esiti = {1: "scomparso", 2: "attivo", 3: "errore", 4: "scomparso"}
    st = scomparsi_con_prova(conn, stato=lambda u: esiti[int(u.split("-")[-1][:-4]) - 100000], pausa=None, zona=["MI"])
    assert st["scomparso"] == 2 and st["attivo"] == 1 and st["errore"] == 1
    upd = [p for s, p in conn.sql if s.startswith("UPDATE listings SET status='scomparso'")]
    assert sorted(p[1] for p in upd) == [1, 4]                       # solo i due con prova
    assert not any("make_interval(hours" in s and s.lstrip().startswith("UPDATE") for s, _ in conn.sql)   # mai in blocco


def test_scomparsi_si_ferma_se_il_sito_blocca():
    from dealfinder.pipeline.verify import scomparsi_con_prova
    rows = [{"id": i, "url": f"https://www.subito.it/auto/x-{100000 + i}.htm", "price_eur": 1} for i in range(100)]
    conn = _Conn([("FROM listings", rows)])
    chiamate = []
    st = scomparsi_con_prova(conn, stato=lambda u: chiamate.append(u) or "errore", pausa=None, zona=["MI"])
    assert len(chiamate) == 15 and st["fermato_errori"] == 1
    assert not any("status='scomparso'" in s for s, _ in conn.sql)


def test_scomparsi_eccezione_nessun_segno():
    from dealfinder.pipeline.verify import scomparsi_con_prova
    rows = [{"id": 1, "url": "https://www.subito.it/auto/x-100001.htm", "price_eur": 1}]
    conn = _Conn([("FROM listings", rows)])

    def rotto(u):
        raise ValueError("forma inattesa")
    st = scomparsi_con_prova(conn, stato=rotto, pausa=None, zona=["MI"])   # non solleva
    assert st["eccezione"] == 1 and not any("status='scomparso'" in s for s, _ in conn.sql)


def test_fetch_subito_con_unlocker_non_e_scomparso():
    from dealfinder.collectors.subito import SubitoCollector
    ad = {"urn": "id:ad:561234567:list:1", "subject": "Fiat Panda",
          "urls": {"default": "https://www.subito.it/auto/fiat-panda-milano-561234567.htm"}}

    class C:
        def post(self, *a, **k):
            return _Resp(200, _pagina(ad), "https://api.brightdata.com/request")
    col = SubitoCollector(client=C())
    col.unlocker_zone, col.unlocker_key = "zona", "chiave"
    col.pause = lambda: None
    l = col.fetch("https://www.subito.it/auto/fiat-panda-milano-561234567.htm")
    assert l is not None and l.title == "Fiat Panda"
