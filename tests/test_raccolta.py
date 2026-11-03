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
