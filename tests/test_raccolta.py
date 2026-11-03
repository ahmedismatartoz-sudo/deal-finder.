"""3/11 (ricostruzione, fase 4): raccolta e qualità dei dati (studio 4 §5), senza rete e con dati finti."""
from dealfinder.core.models import Listing


def _l(**kw):
    base = dict(source="subito", source_id="x1", url="https://x/1")
    base.update(kw)
    return Listing(**base)


# ------------------------------------------------------------------ 8. DS e Abarth coerenti

def test_ds_e_abarth_coerenti():
    from dealfinder.core.consistency import check
    from dealfinder.core.normalize import canon_make, slug
    ds = _l(make=canon_make(slug("DS")), model="ds-3-crossback", title="DS 3 Crossback 1.2 PureTech")
    assert ds.make == "ds-automobiles" and check(ds, fix=False) == []
    ab = _l(make="abarth", model="595", title="Fiat 500 Abarth 595 Turismo")
    assert check(ab, fix=False) == []
    ab2 = _l(make="fiat", model="500", title="Abarth 595 competizione")
    assert check(ab2, fix=False) == []
    # una marca davvero diversa resta incoerente
    assert check(_l(make="ford", model="transit", title="Opel Corsa 2007"), fix=False) == ["marca_diversa_dal_testo"]


# ------------------------------------------------------------------ 7. normalizzazione

def _norm(make, model, fuel=None, gearbox=None, title=None):
    from dealfinder.core.normalize import normalize_fields
    l = _l(make=make, model=model, fuel=fuel, gearbox=gearbox, title=title)
    normalize_fields(l)
    return l


def test_normalizzazione_coppie_uguali_e_diverse():
    uguali = [("fiat", "500X", "500 X"), ("fiat", "500-X", "500X"), ("mercedes", "CLA 200", "Classe CLA"),
              ("bmw", "220 Active Tourer", "Serie 2 Active Tourer"), ("audi", "A3 Sportback", "A3"),
              ("volkswagen", "Golf Variant", "Golf"), ("mazda", "CX-5", "CX5"), ("bmw", "320", "Serie 3"),
              ("mercedes-benz", "A 180", "Classe A"), ("mercedes-benz", "GLA 200", "Classe GLA")]
    for mk, a, b in uguali:
        assert _norm(mk, a).model == _norm(mk, b).model, (mk, a, b)
    diverse = [("fiat", "500", "500X"), ("volkswagen", "Golf", "Golf Plus"), ("toyota", "Yaris", "Yaris Cross"),
               ("mercedes-benz", "Classe GLC", "Classe GLC Coupe")]
    for mk, a, b in diverse:
        assert _norm(mk, a).model != _norm(mk, b).model, (mk, a, b)


def test_normalizzazione_cambio_e_alimentazione():
    for g in ("Cambio manuale", "Manuale", "manuale 6 marce", "Manual"):
        assert _norm("fiat", "panda", gearbox=g).gearbox == "manuale", g
    for g in ("Automatico", "Automatico (DSG)", "Semiautomatico", "Sequenziale", "CVT"):
        assert _norm("fiat", "panda", gearbox=g).gearbox == "automatico", g
    assert _norm("fiat", "panda", fuel="Elettrica/Benzina").fuel == _norm("fiat", "panda", fuel="Ibrida").fuel == "ibrida"
    # stessa auto mild hybrid: "Benzina" su una fonte, "Ibrida" sull'altra -> stesso gruppo, sottotipo a parte
    a = _norm("fiat", "panda", fuel="Benzina", title="Fiat Panda 1.0 Hybrid mild")
    b = _norm("fiat", "panda", fuel="mild-hybrid-benzina")
    assert a.fuel == b.fuel == "ibrida" and a.fuel_tipo == b.fuel_tipo == "mild"
    assert _norm("toyota", "rav4", fuel="plug-in-hybrid-benzina").fuel_tipo == "plug-in"
    assert _norm("fiat", "panda", fuel="Benzina/GPL").fuel_tipo == "bifuel"
    pura = _norm("fiat", "panda", fuel="Benzina", title="Fiat Panda 1.2 Easy")
    assert pura.fuel == "benzina" and pura.fuel_tipo is None


# ------------------------------------------------------------------ regola del cliente: nessun numero dei commercianti

def test_commercianti_as24_senza_numeri():
    from dealfinder.collectors.autoscout import righe_commercianti
    items = [
        {"id": "a1", "seller": {"id": "S1", "type": "Dealer", "phones": [{"callTo": "+393331234567"}],
                                "dealer": {"companyName": "Auto Rossi srl", "contactName": "Mario Rossi"}},
         "vehicle": {"make": "Fiat", "fuel": "Benzina"}, "location": {"city": "Milano", "zip": "20121"},
         "tracking": {"price": "5900"}},
        {"id": "a2", "seller": {"id": "P9", "type": "Private", "phones": [{"callTo": "3471112222"}]}},
        {"id": "a3", "seller": {"id": "S2", "type": "Dealer", "contactName": "Luigi Bianchi"}},
    ]
    righe, annunci = righe_commercianti(items)
    assert set(righe) == {"S1", "S2"}                       # mai i privati
    assert righe["S1"] == ("S1", "Auto Rossi srl") and righe["S2"] == ("S2", "")   # mai il nome della persona
    testo = repr((righe, annunci))
    assert "333" not in testo and "347" not in testo and "Rossi\"" not in testo and "Luigi" not in testo
    assert [a[1] for a in annunci] == ["a1", "a3"]
    import pathlib
    sql = pathlib.Path(__file__).resolve().parents[1].joinpath("db/migrations/033_raccolta_qualita.sql").read_text()
    assert "UPDATE commercianti_as24 SET cellulari = '{}'" in sql


# ------------------------------------------------------------------ connessione finta

class _Cur:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """Registra le query; `risposte` = lista di (frammento SQL, righe). Con colonne=True finge la migrazione 033."""
    def __init__(self, risposte=(), colonne=True, indice=False):
        self.sql, self.risposte = [], list(risposte)
        if colonne:
            self.risposte.insert(0, ("information_schema.columns", [{"column_name": "disappear_reason"},
                                                                    {"column_name": "fuel_tipo"}]))
        if indice:
            self.risposte.insert(0, ("pg_indexes", [{"indexname": "listings_vehicle_idx"}]))
        self.commits = self.rollbacks = 0

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        for frammento, righe in self.risposte:
            if frammento in sql:
                return _Cur(righe(sql, params) if callable(righe) else righe)
        return _Cur([])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def transaction(self):
        import contextlib
        return contextlib.nullcontext()

    def upd(self, inizio):
        return [(s, p) for s, p in self.sql if s.lstrip().startswith(inizio)]


def _schema_nuovo():
    from dealfinder import db
    db._SCHEMA.update(colonne=None, indici=None, at=0.0)


class _Resp:
    def __init__(self, code=200, text="", url=""):
        self.status_code, self.text, self.url = code, text, url


class _Client:
    def __init__(self, per_url):
        self.per_url = per_url

    def get(self, u):
        r = self.per_url[u]
        if isinstance(r, Exception):
            raise r
        return r


def _pagina_as(det):
    import json
    return ('<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"listingDetails": det}}}) + "</script>")


# ------------------------------------------------------------------ 10. verify_rows su AutoScout24

def test_verify_rows_autoscout_reindirizzato_e_scomparso():
    from dealfinder.collectors import autoscout
    from dealfinder.pipeline.verify import verify_rows
    _schema_nuovo()
    u1, u2 = "https://www.autoscout24.it/annunci/fiat-panda-aaa", "https://www.autoscout24.it/annunci/fiat-punto-bbb"
    cl = _Client({u1: _Resp(200, "<html>ricerca</html>", "https://www.autoscout24.it/lst/fiat?atype=C"),
                  u2: _Resp(200, _pagina_as({"id": "bbb", "seller": {}}), u2)})

    class FintoAS:
        def __init__(self):
            self.client = cl
    vero = autoscout.AutoScoutCollector
    autoscout.AutoScoutCollector = FintoAS
    try:
        conn = _Conn()
        st = verify_rows(conn, [{"id": 1, "source": "autoscout24", "url": u1, "price_eur": 1},
                                {"id": 2, "source": "autoscout24", "url": u2, "price_eur": 1}])
    finally:
        autoscout.AutoScoutCollector = vero
        _schema_nuovo()
    assert st["autoscout24:scomparso"] == 1 and st["autoscout24:attivo"] == 1
    via = conn.upd("UPDATE listings SET status='scomparso'")
    assert len(via) == 1 and via[0][1]["id"] == 1 and via[0][1]["motivo"] == "reindirizzato"
    assert "disappear_reason" in via[0][0]
    # il reindirizzato NON diventa "fresco": nessun last_seen_at=now() per la riga 1
    assert not any("last_seen_at=now()" in s and p == (1,) for s, p in conn.sql)


# ------------------------------------------------------------------ 11. scomparsi con prova anche per AutoScout24

def test_scomparsi_con_prova_autoscout():
    from dealfinder.pipeline import tempo_reale as tr
    from dealfinder.pipeline.verify import scomparsi_con_prova
    _schema_nuovo()
    base = "https://www.autoscout24.it/annunci/x-"
    pagine = {base + "1": _Resp(404), base + "2": _Resp(200, "", "https://www.autoscout24.it/lst/fiat"),
              base + "3": _Resp(200, _pagina_as({"id": "3", "seller": {}}), base + "3"),
              base + "4": _Resp(403, "blocked", base + "4"), base + "5": TimeoutError("lento"),
              base + "6": _Resp(200, "<p>L'annuncio non è più disponibile</p>", base + "6")}
    rows = [{"id": i, "url": base + str(i), "price_eur": 1000} for i in range(1, 7)]
    conn = _Conn([("FROM listings\n", rows), ("FROM listings ", rows)])
    vero = tr._client_as
    tr._client_as = lambda: _Client(pagine)
    try:
        st = scomparsi_con_prova(conn, "autoscout24", pausa=None, zona=["MI"])
    finally:
        tr._client_as = vero
        _schema_nuovo()
    assert st["scomparso"] == 3 and st["attivo"] == 1 and st["errore"] == 2
    assert st["motivo:404"] == st["motivo:reindirizzato"] == st["motivo:rimosso"] == 1
    via = conn.upd("UPDATE listings SET status='scomparso'")
    assert sorted(p["id"] for _, p in via) == [1, 2, 6]          # nessuna riga segnata senza prova
    assert "source=%s" in conn.sql[0][0] or any(p and p[0] == "autoscout24" for _, p in conn.sql if isinstance(p, tuple))


# ------------------------------------------------------------------ 12. motivo: "scaduto" non è una vendita

def test_subito_scaduto_non_e_venduta():
    from dealfinder.pipeline.tempo_reale import subito_esito_con_prova
    from dealfinder.pipeline.verify import mark
    url = "https://www.subito.it/auto/fiat-panda-milano-561234567.htm"
    assert subito_esito_con_prova(_Client({url: _Resp(200, "<p>Questo annuncio è scaduto</p>", url)}), url) == ("scomparso", "scaduto")
    assert subito_esito_con_prova(_Client({url: _Resp(200, "<p>annuncio rimosso</p>", url)}), url) == ("scomparso", "rimosso")
    assert subito_esito_con_prova(_Client({url: _Resp(410)}), url) == ("scomparso", "404")
    _schema_nuovo()
    conn = _Conn()
    mark(conn, 5, "scomparso", 4000, motivo="scaduto")
    _schema_nuovo()
    upd = conn.upd("UPDATE listings SET status=")
    assert len(upd) == 1 and upd[0][0].startswith("UPDATE listings SET status='scaduto'")
    assert upd[0][1]["motivo"] == "scaduto"
    # niente evento 'scomparso': chi conta le vendute dagli eventi non la vede
    ev = [p for s, p in conn.sql if s.startswith("INSERT INTO price_events")]
    assert ev == [(5, "scaduto", 4000)]


# ------------------------------------------------------------------ 14. data della vendita

def test_data_vendita_tra_ultimo_visto_e_controllo():
    from dealfinder.pipeline.verify import mark
    _schema_nuovo()
    conn = _Conn(colonne=False)
    mark(conn, 9, "scomparso", 1000, motivo="404", sito=True)
    _schema_nuovo()
    sql, p = conn.upd("UPDATE listings SET status='scomparso'")[0]
    # disappeared_at = punto di mezzo tra l'ultima volta online e il controllo, mai dopo adesso
    assert "disappeared_at = LEAST(now(), GREATEST(last_seen_at" in sql and "/ 2" in sql
    assert "last_checked_at=now()" in sql and "disappear_reason" not in sql      # colonna non ancora creata
    assert p["nota"] == "scomparso sito (404)"


# ------------------------------------------------------------------ 13. ripubblicazione: non è una vendita

def test_ripubblicazione_stesso_veicolo():
    from dealfinder.db import upsert_listing
    _schema_nuovo()
    vecchi = [{"id": 70, "city": "Milano", "mileage_km": 98_500},       # stesso veicolo: ripubblicato
              {"id": 71, "city": "Bergamo", "mileage_km": 98_000},      # altro comune: un'altra auto
              {"id": 72, "city": "Milano", "mileage_km": 120_000}]      # km lontani: un'altra auto
    conn = _Conn([("INSERT INTO vehicles", [{"id": 33}]), ("INSERT INTO listings", [{"id": 99}]),
                  ("status IN ('scomparso','scaduto')", vecchi)], indice=True)
    l = Listing(source="autoscout24", source_id="nuovo1", url="https://x/n1", make="fiat", model="panda", year=2016,
                mileage_km=99_000, price_eur=5200, city="Milano", province="MI")
    lid, ev = upsert_listing(conn, l)
    _schema_nuovo()
    assert (lid, ev) == (99, "nuovo")
    rip = conn.upd("UPDATE listings SET status='ripubblicato'")
    assert [p for _, p in rip] == [(70,)] and "disappear_reason='ripubblicato'" in rip[0][0]
    cerca = [p for s, p in conn.sql if "status IN ('scomparso','scaduto')" in s][0]
    assert cerca[0] == 33 and cerca[2] == 30                  # stesso vehicle_id, entro 30 giorni
    ev = [p for s, p in conn.sql if "'ripubblicato'" in s and s.startswith("INSERT INTO price_events")]
    assert ev == [(70, 5200)]                                  # prezzo di arrivo = quello del nuovo annuncio


def test_ripubblicazione_senza_indice_non_fa_niente():
    from dealfinder.db import upsert_listing
    _schema_nuovo()
    conn = _Conn([("INSERT INTO vehicles", [{"id": 33}]), ("INSERT INTO listings", [{"id": 99}])])
    upsert_listing(conn, Listing(source="subito", source_id="n2", url="https://x/n2", make="fiat", model="panda"))
    _schema_nuovo()
    assert not any("status IN ('scomparso','scaduto')" in s for s, _ in conn.sql)   # niente scansione della tabella


# ------------------------------------------------------------------ 4, 5, 18. _salva: provincia, telefono, contatori

def _salva_finto(listings, tel_prima=None, mappa=None):
    """_salva con upsert finto (un id per annuncio) e mappa comuni -> province finta."""
    from collections import Counter
    from dealfinder import db
    from dealfinder.pipeline import collect, tempo_reale as tr
    salvati = []

    def upsert(conn, l):
        salvati.append(l)
        return 100 + len(salvati), "invariato"
    vero = db.upsert_listing
    db.upsert_listing = upsert
    collect._PROV_COMUNE.update(mappa=mappa or {}, at=9e18)
    try:
        conn, st = _Conn(), Counter()
        tr._salva(conn, listings, st)
    finally:
        db.upsert_listing = vero
        collect._PROV_COMUNE.update(mappa=None, at=0.0)
    return conn, st, salvati


def test_salva_provincia_dal_comune_e_contatori():
    ls = [_l(source="autoscout24", source_id="a1", make="fiat", model="panda", price_eur=4000, city="Rho"),
          _l(source="autoscout24", source_id="a2", make="fiat", model="panda", price_eur=4000, city="Paesino ignoto"),
          _l(source="subito", source_id="s1", make="fiat", model="panda", price_eur=4000, province="RM", city="Roma"),
          _l(source="subito", source_id="s2", make="fiat", model="panda", price_eur=4000, province="MI", city="Milano")]
    conn, st, salvati = _salva_finto(ls, mappa={"rho": "MI"})
    assert [l.source_id for l in salvati] == ["a1", "s2"] and salvati[0].province == "MI"
    assert st["provincia_ricavata"] == 1 and st["scarto:senza_provincia"] == 1 and st["scarto:fuori_zona"] == 1
    letti = sum(v for k, v in st.items() if k.startswith("letti:"))
    scarti = sum(v for k, v in st.items() if k.startswith("scarto:"))
    assert letti == 4 and letti == st["salvati"] + scarti


def test_salva_telefono_stabile():
    sub = {"advertiser": {"phone": "0"}}
    con = _l(source="subito", source_id="s1", make="fiat", model="panda", price_eur=4000, province="MI", raw=sub)
    conn, _, _ = _salva_finto([con])
    tel = [s for s, _ in conn.sql if "telefono_ok" in s]
    assert len(tel) == 1 and "telefono_ok=true" in tel[0] and "telefono_ok IS NOT TRUE" in tel[0]   # telefono_at solo se cambia
    # rilettura senza indicatore: nessuna scrittura (prima tornava NULL e telefono_at=now())
    senza = _l(source="subito", source_id="s1", make="fiat", model="panda", price_eur=4000, province="MI", raw={})
    conn, _, _ = _salva_finto([senza])
    assert not any("telefono" in s for s, _ in conn.sql)
    # AutoScout24 dalla pagina (controllo vero): si scrive il risultato con la data
    asx = _l(source="autoscout24", source_id="a9", make="fiat", model="panda", price_eur=4000, province="MI",
             raw={"_telefono": False})
    conn, _, _ = _salva_finto([asx])
    assert [p for s, p in conn.sql if "telefono_ok" in s] == [(False, 101)]


def test_vecchio_filtro_senza_numero_considera_il_bottone():
    from dealfinder.pipeline.collect import _fuori_regole_raccolta, senza_numero
    con_bottone = _l(source="subito", make="fiat", model="panda", year=2015, mileage_km=90000,
                     title="Fiat Panda", description="ottime condizioni", raw={"advertiser": {"phone": "0"}})
    assert not senza_numero(con_bottone) and _fuori_regole_raccolta(con_bottone) is None
    nulla = _l(source="subito", make="fiat", model="panda", year=2015, mileage_km=90000, title="Fiat Panda", raw={})
    assert senza_numero(nulla)
    assert _fuori_regole_raccolta(nulla) is None              # senza numero NON è più un motivo di scarto
    vecchia = _l(source="subito", make="fiat", model="panda", year=2003, mileage_km=90000, raw={})
    assert _fuori_regole_raccolta(vecchia) == "regole"


# ------------------------------------------------------------------ 1. collect.run salva tutto (anche senza numero)

def test_collect_run_non_scarta_subito_senza_numero():
    import contextlib
    from dealfinder.collectors import autoscout
    from dealfinder.pipeline import collect
    from dealfinder.pricing import train
    annunci = [_l(source="subito", source_id=f"s{i}", url=f"https://www.subito.it/auto/x-{500000 + i}.htm",
                  make="Fiat", model="Panda", year=2003 if i < 10 else 2015, mileage_km=90000, fuel="Benzina",
                  price_eur=3000 + i, province="MI", city="Milano", title="Fiat Panda", description="ottima", raw={})
               for i in range(100)]

    class FintoSub:
        source = "subito"

        def __init__(self, *a, **k):
            self.min_delay_s = self.max_delay_s = 0

        def search(self, q):
            yield from annunci

    salvati = []

    def upsert(conn, l):
        salvati.append(l)
        return len(salvati), "nuovo"
    conn = _Conn(colonne=False)

    @contextlib.contextmanager
    def connetti():
        yield conn
    vecchi = (collect.SubitoCollector, collect.connect, collect.log_job, collect.upsert_listing, train.load_active,
              autoscout.AutoScoutCollector)
    collect.SubitoCollector, collect.connect, collect.upsert_listing = FintoSub, connetti, upsert
    collect.log_job = lambda c, job: (lambda ok, st: None)
    train.load_active = lambda c: None
    autoscout.AutoScoutCollector = lambda *a, **k: type("A", (), {"source": "autoscout24", "stop": False})()
    import os
    env = dict(os.environ)
    try:
        st = collect.run("full_sub")
    finally:
        os.environ.clear()
        os.environ.update(env)
        (collect.SubitoCollector, collect.connect, collect.log_job, collect.upsert_listing, train.load_active,
         autoscout.AutoScoutCollector) = vecchi
        _schema_nuovo()
    assert len(salvati) == 100 and st["subito:senza_numero_letti"] == 100
    assert st["subito:fuori_regole"] == 10                           # auto del 2003: salvate come solo confronto
    mercato = [p for s, p in conn.sql if "stage_reason='fuori_regole'" in s]
    assert sorted(p[0] for p in mercato) == list(range(1, 11))
    r = collect.riepilogo_scarti(st)
    assert r["letti"] == 100 and r["salvati"] == 100 and r["non_contati"] == 0
