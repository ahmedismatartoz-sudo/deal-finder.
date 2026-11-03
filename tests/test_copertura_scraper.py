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


# ------------------------------------------------------------------ fix 2: nuovi tentativi sull'interfaccia di Subito

def _ad(n, prov="MI", prezzo=5000, data="2026-11-02 10:00:00", key="8"):
    return {"urn": f"id:ad:{n}:list:1", "subject": f"auto {n}", "body": "",
            "urls": {"default": f"https://www.subito.it/auto/auto-{n}.htm"},
            "dates": {"display": data, "expiration": "2027-01-01 10:00:00"},
            "geo": {"city": {"short_name": prov, "key": key}, "town": {"value": "Milano"}},
            "advertiser": {"type": 0},
            "features": {"/price": {"label": "Prezzo", "values": [{"key": str(prezzo), "value": f"{prezzo} €"}]}}}


class _Hades:
    """Finto hades.subito.it: annunci filtrati per ci/ps/pe, 100 per pagina; `guasti` = risposte da dare prima."""
    def __init__(self, ads, guasti=()):
        self.ads, self.guasti, self.calls = ads, list(guasti), []

    def get(self, url, headers=None, params=None):
        p = dict(params or {})
        self.calls.append(p)
        if self.guasti:
            g = self.guasti.pop(0)
            if isinstance(g, Exception):
                raise g
            return _Resp(g, "", url, {})
        sel = self.ads
        if "ci" in p:
            sel = [a for a in sel if a["geo"]["city"]["key"] == p["ci"]]
        if "ps" in p:
            sel = [a for a in sel if p["ps"] <= int(a["features"]["/price"]["values"][0]["key"]) <= p["pe"]]
        start, lim = int(p.get("start", 0)), int(p.get("lim", 100))
        return _Resp(200, "", url, {"ads": sel[start:start + lim]})


def _sub(client):
    from dealfinder.collectors.subito import SubitoCollector
    c = SubitoCollector(client=client)
    c.pause = lambda: None
    c._dormi = lambda s: None
    return c


def test_recenti_riprova_dopo_429_e_errori_di_rete():
    ads = [_ad(100000 + i) for i in range(150)]
    cl = _Hades(ads, guasti=[503, 429, TimeoutError("rete")])
    got = list(_sub(cl).search_recenti("lombardia", ["MI"], "2026-10-01 00:00", max_pages=5))
    assert len(got) == 150                        # prima: zero (la prima pagina in errore chiudeva tutto)


def test_recenti_si_ferma_se_l_errore_resta():
    cl = _Hades([_ad(100001)], guasti=[429] * 10)
    c = _sub(cl)
    assert list(c.search_recenti("lombardia", ["MI"], "2026-10-01 00:00", max_pages=5)) == []
    assert len(cl.calls) == c.API_RIPROVE + 1      # tentativi limitati, poi come prima


def test_recenti_usa_la_data_display():
    vecchio = _ad(100001)
    vecchio["dates"] = {"expiration": "2027-01-01 10:00:00", "display": "2026-09-01 10:00:00"}
    nuovo = _ad(100002, data="2026-11-01 10:00:00")
    got = list(_sub(_Hades([nuovo, vecchio])).search_recenti("lombardia", ["MI"], "2026-10-01 00:00", max_pages=2))
    assert [l.source_id for l in got] == ["id:ad:100002:list:1"]


def test_profonda_un_errore_salta_solo_la_fascia():
    ads = [_ad(100000 + i, prezzo=1500) for i in range(30)] + [_ad(200000 + i, prezzo=2500) for i in range(30)]
    cl = _Hades(ads)
    c = _sub(cl)
    orig = cl.get

    def get(url, headers=None, params=None):
        if params and params.get("ps") == 1000:
            raise ValueError("risposta strana")
        return orig(url, headers, params)
    cl.get = get
    got = list(c._deep({"price_bands": [(1000, 2000), (2000, 3000)], "band_pages": 5}, {"MI"}, 4, 100))
    assert len(got) == 30 and all(l.price_eur == 2500 for l in got)   # prima: zero (regione intera persa)


# ------------------------------------------------------------------ fix 3: fasce piene divise in due

def test_profonda_fascia_piena_si_divide():
    ads = [_ad(300000 + i, prezzo=1000 + (i * 7) % 1000) for i in range(450)]
    cl = _Hades(ads)
    got = list(_sub(cl)._deep({"price_bands": [(1000, 2000)], "band_pages": 2}, {"MI"}, 4, 100))
    ids = [l.source_id for l in got]
    assert len(ids) == len(set(ids)) == 450        # prima: 200 (solo le prime 2 pagine)


def test_profonda_fascia_non_piena_non_si_divide():
    ads = [_ad(400000 + i, prezzo=1500) for i in range(150)]
    cl = _Hades(ads)
    got = list(_sub(cl)._deep({"price_bands": [(1000, 2000)], "band_pages": 2}, {"MI"}, 4, 100))
    assert len(got) == 150
    assert sum(1 for p in cl.calls if "ps" in p) == 2   # nessuna rilettura in più


def test_profonda_divisione_limitata():
    ads = [_ad(500000 + i, prezzo=1500) for i in range(1000)]    # tutte allo stesso prezzo: dividere non aiuta
    cl = _Hades(ads)
    c = _sub(cl)
    got = list(c._deep({"price_bands": [(1000, 2000)], "band_pages": 1}, {"MI"}, 4, 100))
    assert len(got) == 100
    assert sum(1 for p in cl.calls if "ps" in p) <= 2 ** (c.FASCIA_DIVISIONI + 1)


# ------------------------------------------------------------------ fix 4: una rilettura senza un dato non lo cancella

class _ConnTx(_Conn):
    def __init__(self, risposte=(), rompi=None):
        super().__init__(risposte)
        self.rompi = rompi

    def execute(self, sql, params=None):
        if self.rompi and self.rompi in sql:
            self.sql.append((sql, params))
            raise RuntimeError("sql non valido")
        return super().execute(sql, params)

    def transaction(self):
        import contextlib
        return contextlib.nullcontext()


def _upsert(source, rompi=None, n_foto=1, foto=3):
    from dealfinder.core.models import Listing
    from dealfinder.db import upsert_listing
    conn = _ConnTx([("INSERT INTO vehicles", [{"id": 1}]),
                    ("FROM listings WHERE source", [{"id": 7, "price_eur": 5000, "status": "attivo"}]),
                    ("RETURNING (SELECT count", [{"n_foto": n_foto}])], rompi=rompi)
    l = Listing(source=source, source_id="a1", url="https://x/a1", make="fiat", model="panda", price_eur=5000,
                description=None, photos=[f"https://img/{i}" for i in range(foto)])
    lid, ev = upsert_listing(conn, l)
    upd = [s for s, _ in conn.sql if s.startswith("UPDATE listings SET") and "last_seen_at = now()" in s]
    foto_ins = [p for s, p in conn.sql if s.startswith("INSERT INTO listing_photos")]
    return lid, ev, upd, foto_ins


def test_upsert_non_cancella_la_descrizione():
    lid, ev, upd, foto = _upsert("autoscout24")
    assert (lid, ev) == (7, "invariato") and len(upd) == 1
    assert "COALESCE(NULLIF(%(description)s, ''), listings.description)" in upd[0]
    assert "COALESCE(%(province)s, listings.province)" in upd[0]
    assert "NULLIF(%(seller_type)s, 'sconosciuto')" in upd[0]
    assert [p[1] for p in foto] == [0, 1, 2]       # 1 foto salvata, 3 nella nuova lettura: si aggiungono


def test_upsert_foto_gia_complete_nessun_insert():
    _, _, _, foto = _upsert("subito", n_foto=3, foto=3)
    assert foto == []


def test_upsert_ripiego_se_la_query_nuova_fallisce():
    lid, ev, upd, _ = _upsert("subito", rompi="RETURNING (SELECT count")
    assert lid == 7 and len(upd) == 2               # tentata la nuova, poi quella di prima
    assert "listings.description" not in upd[1]


class _ConnNoti(_Conn):
    def __init__(self, noti):
        super().__init__()
        self.noti = set(noti)

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if "source_id = ANY" in sql:
            return _Cur([{"source_id": i} for i in params[0] if i in self.noti])
        return _Cur([])


def test_subito_leggero_si_ferma_alla_pagina_nota():
    from dealfinder.pipeline import tempo_reale as tr
    tr._sub_leggero["fermo_fino"] = 0.0
    ads = [_ad(600000 + i) for i in range(300)]                 # i primi 30 nuovi, il resto già salvato
    conn = _ConnNoti(f"id:ad:{600000 + i}:list:1" for i in range(30, 300))
    cl = _Hades(ads)
    got = tr._subito_nuovi_leggero(conn, col=_sub(cl), pausa=None)
    assert sorted(l.source_id for l in got) == sorted(f"id:ad:{600000 + i}:list:1" for i in range(30))
    lomb = [p for p in cl.calls if p.get("r") == 4]
    assert len(lomb) == 2                                      # pagina 1 (nuovi) + pagina 2 (tutta nota): stop


def test_subito_leggero_429_si_ferma_e_niente_eccezioni():
    from dealfinder.pipeline import tempo_reale as tr
    tr._sub_leggero["fermo_fino"] = 0.0
    cl = _Hades([_ad(700001)], guasti=[429])
    assert tr._subito_nuovi_leggero(_ConnNoti([]), col=_sub(cl), pausa=None) == []
    assert tr._sub_leggero["fermo_fino"] > 0
    n = len(cl.calls)
    assert tr._subito_nuovi_leggero(_ConnNoti([]), col=_sub(cl), pausa=None) == [] and len(cl.calls) == n   # in pausa
    tr._sub_leggero["fermo_fino"] = 0.0

    class Rotto:
        def get(self, *a, **k):
            raise ValueError("forma inattesa")
    assert tr._subito_nuovi_leggero(_ConnNoti([]), col=_sub(Rotto()), pausa=None) == []


def test_upsert_facebook_invariato():
    _, _, upd, foto = _upsert("facebook")
    assert len(upd) == 1 and "listings.description" not in upd[0] and foto == []
