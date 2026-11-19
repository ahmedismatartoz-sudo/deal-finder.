"""16/11 fondamenta della velocità (FOND_VELOCITA.md): un test per ciascun punto, con connessioni e HTTP finti."""
from __future__ import annotations

import contextlib
import functools
import logging
import os
import signal
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dealfinder.core.models import Listing
from dealfinder.pipeline import tempo_reale as T
from dealfinder.pipeline import tempo_reale_worker as W
from tests.test_copertura_scraper import _Hades, _ad, _sub


def _azzera() -> None:
    """Stato del tempo reale come a un avvio: senza arresto, senza annunci "visti", senza pause dopo un 429."""
    T.FERMA.clear()
    for s in T._VISTI.values():
        s.clear()
    T._sub_leggero["fermo_fino"] = 0.0
    T._avvio["primo"] = False


def _isolato(fn):
    """16/11: ogni test parte e finisce con lo stato del tempo reale azzerato e con l'elenco del sito com'era (le
    attese tra le richieste AutoScout24 si tolgono nei test che leggono, con monkeypatch.setenv). Decoratore (non fixture) perché run_tests.py non ha le fixture di pytest: la firma
    resta quella del test (functools.wraps), così `monkeypatch` arriva sia da pytest sia da run_tests.py."""
    @functools.wraps(fn)
    def avvolto(*a, **k):
        import copy
        from dealfinder.web import app
        elenco = copy.copy(app._elenco)
        _azzera()
        try:
            return fn(*a, **k)
        finally:
            _azzera()
            app._elenco.clear()
            app._elenco.update(elenco)
    return avvolto


@contextlib.contextmanager
def _registra(nome: str):
    """Righe di log WARNING del logger `nome` (run_tests.py non ha la fixture dei log di pytest)."""
    righe: list = []

    class H(logging.Handler):
        def emit(self, record):
            righe.append(record.getMessage())
    lg, h = logging.getLogger(nome), H(logging.WARNING)
    lg.addHandler(h)
    try:
        yield righe
    finally:
        lg.removeHandler(h)


class Cur:
    def __init__(self, rows=None, rowcount=None):
        self.rows = rows or []
        self.rowcount = len(self.rows) if rowcount is None else rowcount

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class DB:
    """Finto Postgres: annunci {(fonte, id): {id, price_eur, status}} e la tabella tr_coda in memoria."""

    def __init__(self, annunci=None, coda=True):
        self.annunci = dict(annunci or {})
        self.coda: dict = {}
        self.con_coda = coda
        self.ops: list = []

    def transaction(self):
        import contextlib
        return contextlib.nullcontext()

    def commit(self):
        self.ops.append("commit")

    def rollback(self):
        self.ops.append("rollback")

    def execute(self, sql, p=None):
        self.ops.append(sql)
        ora = datetime.now(timezone.utc)
        if "to_regclass('tr_coda')" in sql:
            return Cur([{"ok": self.con_coda}])
        if "FROM listings WHERE source=%s AND source_id = ANY" in sql:
            f, sids = p
            return Cur([dict(r, source_id=s) for (ff, s), r in self.annunci.items() if ff == f and s in sids])
        if "FROM listings WHERE source='subito' AND source_id = ANY" in sql or \
                "FROM listings WHERE source='autoscout24' AND source_id = ANY" in sql:
            f = "subito" if "'subito'" in sql else "autoscout24"
            return Cur([dict(r, source_id=s) for (ff, s), r in self.annunci.items() if ff == f and s in p[0]])
        if sql.startswith("INSERT INTO tr_coda"):
            lid, fonte, ev, ann = p
            r = self.coda.get(lid)
            if r is None or r["valutato_at"] is not None:
                self.coda[lid] = dict(listing_id=lid, fonte=fonte, evento=ev, annuncio_at=ann, letto_at=ora,
                                      preso_at=ora, tentativi=1, valutato_at=None, esito=None, pubblicato_at=None,
                                      sito_at=None)
            return Cur()
        if sql.startswith("UPDATE tr_coda SET preso_at = NULL"):
            for i in p[0]:
                if i in self.coda and self.coda[i]["valutato_at"] is None:
                    self.coda[i]["preso_at"] = None
            return Cur()
        if sql.startswith("UPDATE tr_coda SET preso_at"):
            tmax, rip, escludi, lim = p
            presi = []
            for r in sorted(self.coda.values(), key=lambda x: x["letto_at"]):
                if r["valutato_at"] is None and r["tentativi"] < tmax and r["listing_id"] not in escludi and \
                        (r["preso_at"] is None or r["preso_at"] <= ora - timedelta(minutes=rip)) and len(presi) < lim:
                    r["preso_at"], r["tentativi"] = ora, r["tentativi"] + 1
                    presi.append({"listing_id": r["listing_id"]})
            return Cur(presi)
        if sql.startswith("UPDATE tr_coda SET valutato_at = now(), esito = CASE"):
            pubb, _, ids = p
            out = []
            for i in ids:
                r = self.coda.get(i)
                if r and r["valutato_at"] is None:
                    r.update(valutato_at=ora, esito="pubblicata" if i in pubb else "valutata",
                             pubblicato_at=ora if i in pubb else None)
                    out.append(dict(r))
            return Cur(out)
        if sql.startswith("UPDATE tr_coda SET sito_at"):
            out = []
            for i in p[0]:
                r = self.coda.get(i)
                if r and r["sito_at"] is None and r["valutato_at"] is not None:
                    r["sito_at"] = ora
                    out.append(dict(r))
            return Cur(out)
        if sql.startswith("UPDATE listings SET last_seen_at"):
            return Cur(rowcount=len(p[0]))
        if "SELECT ultimo_at FROM tempo_reale" in sql:
            return Cur([])
        return Cur()


def _l(sid, prezzo=5000, fonte="subito", **kw):
    base = dict(source=fonte, source_id=sid, url=f"https://x/{sid}", make="fiat", model="panda", year=2015,
                mileage_km=90000, price_eur=prezzo, province="MI", seller_type="privato", title="Fiat Panda")
    base.update(kw)
    return Listing(**base)


def _finto_upsert(monkeypatch, ev="nuovo"):
    from dealfinder import db
    salvati = []

    def upsert(conn, l):
        salvati.append(l.source_id)
        return 100 + len(salvati), ev
    monkeypatch.setattr(db, "upsert_listing", upsert)
    return salvati


# ------------------------------------------------------------------ 1. Subito: ci si ferma alla prima pagina nota

def test_1_subito_veloce_si_ferma_alla_pagina_nota_e_vede_i_ribassi():
    ads = [_ad(600000 + i) for i in range(300)]
    ads[5]["geo"]["city"]["short_name"] = "RM"                     # fuori dalle province: mai nel database
    noti = {("subito", f"id:ad:{600000 + i}:list:1"): {"id": i, "price_eur": 5000, "status": "attivo"}
            for i in range(30, 300)}
    noti[("subito", "id:ad:600040:list:1")]["price_eur"] = 6000    # ribasso: da rivalutare
    db = DB(noti)
    cl = _Hades(ads)
    got = T._subito_nuovi_leggero(db, col=_sub(cl), pausa=None)
    attesi = {f"id:ad:{600000 + i}:list:1" for i in range(30) if i != 5} | {"id:ad:600040:list:1"}
    assert {l.source_id for l in got} == attesi
    assert len([p for p in cl.calls if p.get("r") == 4]) == 2     # pagina 1 (nuovi) + pagina 2 (tutta nota): stop
    # giro dopo: i nuovi ora sono salvati; quello fuori zona è ricordato (prima la pagina non era mai "tutta nota")
    for i in list(range(30)) + [40]:
        db.annunci[("subito", f"id:ad:{600000 + i}:list:1")] = {"id": i, "price_eur": 5000, "status": "attivo"}
    cl.calls.clear()
    assert T._subito_nuovi_leggero(db, col=_sub(cl), pausa=None) == []
    assert len([p for p in cl.calls if p.get("r") == 4]) == 1
    # riapparso: nel database ma non attivo -> non è "noto", torna entro un giro
    db.annunci[("subito", "id:ad:600010:list:1")]["status"] = "scomparso"
    T._VISTI["subito"].discard("id:ad:600010:list:1")   # il finto Subito dà gli stessi annunci anche alle altre regioni
    assert [l.source_id for l in T._subito_nuovi_leggero(db, col=_sub(cl), pausa=None)] == ["id:ad:600010:list:1"]


def test_1_giro_veloce_non_usa_piu_la_finestra(monkeypatch):
    chiamate = []
    monkeypatch.setattr(T, "_subito_nuovi", lambda *a, **k: chiamate.append("finestra") or [])
    monkeypatch.setattr(T, "_subito_veloce", lambda conn, st: chiamate.append("veloce") or [])
    monkeypatch.setattr(T, "_autoscout_nuovi", lambda conn: [])
    monkeypatch.setenv("TR_SUBITO", "1")
    T.giro_nuovi(DB(coda=False))
    assert chiamate == ["veloce"]
    monkeypatch.setenv("TR_SUBITO_VELOCE", "0")                   # interruttore per tornare a prima
    chiamate.clear()
    monkeypatch.setattr(T, "_diagnostica_pagina_subito", lambda l: None)
    T.giro_nuovi(DB(coda=False))
    assert chiamate == ["finestra"]


# ------------------------------------------------------------------ 2. AutoScout: visti dopo il salvataggio, ripresa, limitatore

def _as_finto(monkeypatch, pagine):
    from dealfinder.collectors import autoscout, base
    from dealfinder.pipeline import as_riprova
    lette = []

    def get(client, url, nome, **k):
        p = int(url.rsplit("page=", 1)[1])
        lette.append(p)
        return SimpleNamespace(status_code=200, text=str(p))
    monkeypatch.setattr(as_riprova, "get", get)
    monkeypatch.setattr(base, "next_data", lambda t: {"props": {"pageProps": {"listings": pagine.get(int(t), [])}}})
    monkeypatch.setattr(autoscout, "parse_item",
                        lambda it: SimpleNamespace(source="autoscout24", source_id=it["id"], province=it["pv"], seller_type=None))
    monkeypatch.setattr(T, "_client_as", lambda: None)
    monkeypatch.setattr(T, "_as_completa", lambda c, out: out)
    return lette


def test_2_autoscout_visto_solo_dopo_il_salvataggio(monkeypatch):
    monkeypatch.setenv("TR_AS_INTERVALLO_S", "0")
    lette = _as_finto(monkeypatch, {1: [{"id": "a1", "pv": "MI"}, {"id": "a2", "pv": "RM"}], 2: [{"id": "a3", "pv": "MI"}]})
    out = T._autoscout_nuovi(DB())
    assert [l.source_id for l in out] == ["a1", "a3"]
    for l in out:
        l._tr_ev = "errore"                                       # salvataggio fallito (o giro caduto prima)
    T._segna_visti(out)
    assert [l.source_id for l in T._autoscout_nuovi(DB())] == ["a1", "a3"]   # non perso: si rilegge
    for l in out:
        l._tr_ev = "nuovo"
    T._segna_visti(out)
    lette.clear()
    assert T._autoscout_nuovi(DB()) == [] and lette == [1]       # salvati: pagina tutta nota, stop


def test_2_ripresa_429_fuori_dal_giro_veloce(monkeypatch):
    from dealfinder.pipeline import as_riprova
    tutti = []
    monkeypatch.setattr(as_riprova, "leggi_ripresa", lambda c: {"fascia": 3, "pagina": 2})
    monkeypatch.setattr(as_riprova, "fermo", lambda: False)
    monkeypatch.setattr(T, "_autoscout_tutti", lambda c: tutti.append(1) or [])
    monkeypatch.setattr(T, "_autoscout_nuovi", lambda c: [])
    monkeypatch.setattr(T, "_subito_veloce", lambda c, st: [])
    st = T.giro_nuovi(DB(coda=False))
    assert tutti == [] and "as_ripresa" not in st                 # il giro veloce non la fa più
    st = T.giro_ripresa_as(DB(coda=False))
    assert tutti == [1] and st["as_ripresa"] == 1                 # la fa il filo della lettura completa
    monkeypatch.setattr(as_riprova, "leggi_ripresa", lambda c: None)
    assert T.giro_ripresa_as(DB(coda=False)) is None


def test_2_limitatore_unico_tra_due_fili(monkeypatch):
    monkeypatch.setenv("TR_AS_INTERVALLO_S", "0.05")
    lim = T.Limitatore()
    tempi = []
    chiamate = []

    class C:
        def get(self, url):
            chiamate.append(url)
            tempi.append(time.monotonic())

    def filo():
        c = T.ClienteLimitato(C(), lim)
        for _ in range(3):
            c.get("u")
    fili = [threading.Thread(target=filo) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    tempi.sort()
    assert len(chiamate) == 6
    assert all(b - a >= 0.04 for a, b in zip(tempi, tempi[1:]))   # mai due richieste ravvicinate, anche da fili diversi


def test_2_completo_sorvegliato_e_fa_la_ripresa():
    usciti = []
    g = W.Guardiano(esci=lambda: usciti.append(1), variabile="TR_COMPLETO_GIRO_MAX_MIN", massimo_min=120)
    principale = W.Guardiano(esci=lambda: usciti.append(0))
    fine = W._Fine(esci=lambda: None, tr=SimpleNamespace(FERMA=threading.Event()))
    g.inizio = time.time() - 121 * 60                              # lettura completa bloccata
    t = threading.Thread(target=principale.gira, args=(fine, (g,)))
    t.start()
    time.sleep(.2)
    fine.ev.set()
    t.join(2)
    assert usciti == [1]

    giri = []

    class TR:
        @staticmethod
        def con_lucchetto(nome, fn, *a):
            giri.append((nome, fn, a))
            if len(giri) >= 3:
                fine2.ev.set()
            return Counter()
        giro_nuovi = "giro_nuovi"
        giro_ripresa_as = "giro_ripresa_as"

    os.environ["TR_COMPLETO_PRIMO_MIN"] = "0"
    os.environ["TR_RIPRESA_CONTROLLO_MIN"] = "0"
    try:
        fine2 = W._Fine(esci=lambda: None, tr=SimpleNamespace(FERMA=threading.Event()))
        W._completo_a_parte(fine2, W.Guardiano(esci=lambda: None), tr=TR)
    finally:
        os.environ.pop("TR_COMPLETO_PRIMO_MIN")
        os.environ.pop("TR_RIPRESA_CONTROLLO_MIN")
    assert [x[1] for x in giri] == ["giro_nuovi", "giro_ripresa_as", "giro_ripresa_as"]
    assert all(x[0] == "completo" for x in giri)


# ------------------------------------------------------------------ 3. il sito si fa da parte, log col lucchetto occupato

def test_3_lucchetto_occupato_si_scrive_nei_log(monkeypatch):
    from dealfinder import db

    class Occ(DB):
        def execute(self, sql, p=None):
            if "pg_try_advisory_lock" in sql:
                return Cur([{"ok": False}])
            return super().execute(sql, p)

    import contextlib
    monkeypatch.setattr(db, "connect", lambda: contextlib.nullcontext(Occ()))
    with _registra("tempo_reale") as righe:
        assert T.con_lucchetto("nuovi", lambda c: 1) is None
    assert any("TR LUCCHETTO nuovi occupato" in m for m in righe)


def test_3_sito_aggiorna_l_elenco_solo_per_un_giro_nuovo_del_worker():
    from dealfinder.web import app
    visto = [None]
    assert app._nuove_dal_worker(visto, "t1", 3) is False          # primo controllo: si ricorda solo il giro
    assert app._nuove_dal_worker(visto, "t1", 3) is False          # stesso giro
    assert app._nuove_dal_worker(visto, "t2", 0) is False          # giro nuovo senza pubblicate
    assert app._nuove_dal_worker(visto, "t3", 2) is True


# ------------------------------------------------------------------ 4. coda persistente: niente perso ai riavvii

def test_4_coda_nella_transazione_e_ripresa_dopo_un_crollo(monkeypatch):
    _finto_upsert(monkeypatch)
    monkeypatch.setenv("TR_CODA_RIPRESA_MIN", "0")
    db = DB()
    st = Counter()
    ids = T._salva(db, [_l("s1"), _l("s2")], st, coda=True)
    assert ids == [101, 102] and set(db.coda) == {101, 102}
    i_coda = max(i for i, o in enumerate(db.ops) if isinstance(o, str) and o.startswith("INSERT INTO tr_coda"))
    assert "commit" in db.ops[i_coda:]                            # in coda prima del commit dell'annuncio

    def crollo(*a, **k):
        raise RuntimeError("processo ucciso durante la valutazione")
    monkeypatch.setattr(T, "_lettore_nuovi", lambda *a: None)
    monkeypatch.setattr(T, "valuta_e_pubblica", crollo)
    with pytest.raises(RuntimeError):
        T._valuta(db, ids, st, coda=True)
    assert all(r["valutato_at"] is None for r in db.coda.values())  # restano in coda

    valutati = []

    def valuta(conn, ids, stats, pubblicate=None, **k):
        valutati.extend(ids)
        pubblicate.append(101)
        stats["pubblicate"] += 1
    monkeypatch.setattr(T, "valuta_e_pubblica", valuta)
    st2 = Counter()
    T._valuta(db, [], st2, coda=True)                             # "dopo il riavvio": nessun annuncio nuovo
    assert sorted(valutati) == [101, 102] and st2["coda:ripresi"] == 2
    assert db.coda[101]["esito"] == "pubblicata" and db.coda[102]["esito"] == "valutata"
    valutati.clear()
    T._valuta(db, [], Counter(), coda=True)
    assert valutati == []                                          # chiusi: non si rivalutano


def test_4_senza_migrazione_tutto_come_prima(monkeypatch):
    _finto_upsert(monkeypatch)
    db = DB(coda=False)
    assert T.coda_attiva(db) is False
    T._salva(db, [_l("s1")], Counter(), coda=False)
    assert not any(isinstance(o, str) and "INTO tr_coda" in o for o in db.ops)
    monkeypatch.setenv("TR_CODA", "0")
    assert T.coda_attiva(DB()) is False


def test_4_migrazione_coda_idempotente():
    from pathlib import Path
    sql = (Path(T.__file__).resolve().parents[2] / "db" / "migrations" / "058_coda_tempo_reale.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS tr_coda" in sql and "listing_id    BIGINT PRIMARY KEY" in sql
    assert all("IF NOT EXISTS" in r for r in sql.splitlines() if r.startswith("CREATE"))


# ------------------------------------------------------------------ 5. invariati a blocchi

def test_5_invariati_una_scrittura_a_blocchi(monkeypatch):
    salvati = _finto_upsert(monkeypatch, ev="prezzo")
    noti = {("subito", f"s{i}"): {"id": i, "price_eur": 5000, "status": "attivo"} for i in range(1, 6)}
    noti[("subito", "s5")]["price_eur"] = 6000                     # prezzo cambiato: upsert completo
    noti[("subito", "s4")]["status"] = "scomparso"                 # riapparso: upsert completo
    db = DB(noti, coda=False)
    st = Counter()
    lst = [_l(f"s{i}", raw={"advertiser": {"phone": "0"}} if i == 1 else {}) for i in range(1, 6)]
    T._salva(db, lst, st)
    assert salvati == ["s4", "s5"]
    agg = [o for o in db.ops if isinstance(o, str) and o.startswith("UPDATE listings SET last_seen_at")]
    assert len(agg) == 1 and st["invariati_a_blocchi"] == 3
    tel = [o for o in db.ops if isinstance(o, str) and "telefono_ok = true" in o]
    assert len(tel) == 1                                           # il "sì" del numero di s1, sempre a blocchi
    assert [l._tr_ev for l in lst] == ["invariato", "invariato", "invariato", "prezzo", "prezzo"]
    monkeypatch.setenv("TR_INVARIATI_VELOCE", "0")                 # interruttore: come prima
    salvati.clear()
    T._salva(DB(noti, coda=False), [_l("s1")], Counter())
    assert salvati == ["s1"]


# ------------------------------------------------------------------ 6. arresto pulito

def test_6_arresto_tra_le_fasi_e_attese_interrotte(monkeypatch):
    T.FERMA.set()
    t0 = time.time()
    T._dormi(30)
    assert time.time() - t0 < 1
    letti = []
    monkeypatch.setattr(T, "_subito_veloce", lambda c, st: letti.append("subito") or [])
    monkeypatch.setattr(T, "_autoscout_nuovi", lambda c: letti.append("autoscout") or [])
    db = DB()
    db.coda[7] = dict(listing_id=7, fonte="subito", evento="nuovo", annuncio_at=None, letto_at=datetime.now(timezone.utc),
                      preso_at=None, tentativi=0, valutato_at=None, esito=None, pubblicato_at=None, sito_at=None)
    monkeypatch.setattr(T, "valuta_e_pubblica", lambda *a, **k: pytest.fail("all'arresto non si valuta"))
    st = T.giro_nuovi(db)
    assert letti == ["subito"] and st["fermato_arresto"] == 1 and st["coda:lasciati_arresto"] == 1
    assert db.coda[7]["preso_at"] is None and db.coda[7]["valutato_at"] is None   # rilasciato per il processo dopo
    out = [SimpleNamespace(url="u1"), SimpleNamespace(url="u2")]
    assert T._as_completa(None, out) == []                         # dettagli non letti: niente salvato a metà


def test_6_sigterm_il_worker_chiude_subito(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@127.0.0.1:1/x")
    monkeypatch.setenv("TR_ARRESTO_MAX_S", "30")
    from dealfinder import memoria
    monkeypatch.setattr(memoria, "rilascia", lambda *a: None)
    giri = []

    def un_giro(completo):
        giri.append(completo)
        os.kill(os.getpid(), signal.SIGTERM)                       # arriva il SIGTERM durante il giro
        return Counter()
    monkeypatch.setattr(W, "un_giro", un_giro)
    vecchio = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        t0 = time.time()
        fine = W.main(giri=5, dormi=lambda s: time.sleep(min(s, 60)))
        assert time.time() - t0 < 5 and giri == [False]           # nessun altro giro, nessuna attesa di 90 s
        assert fine.ev.is_set() and T.FERMA.is_set()
        assert fine._timer.finished.is_set()                       # arresto in tempo: uscita forzata annullata
    finally:
        signal.signal(signal.SIGTERM, vecchio[0])
        signal.signal(signal.SIGINT, vecchio[1])


def test_6_uscita_forzata_se_l_arresto_non_finisce(monkeypatch):
    monkeypatch.setenv("TR_ARRESTO_MAX_S", "0.05")
    usciti = []
    f = W._Fine(esci=lambda: usciti.append(1), tr=SimpleNamespace(FERMA=threading.Event()))
    f()
    time.sleep(.3)
    assert usciti == [1] and f._tr.FERMA.is_set()


# ------------------------------------------------------------------ 7. il sito non perde le auto pubblicate durante un calcolo

def test_7_richiesta_durante_il_calcolo_rifa_il_calcolo(monkeypatch):
    from dealfinder.web import app
    entrato, via = threading.Event(), threading.Event()
    calcoli = []

    def calcola():
        calcoli.append(1)
        if len(calcoli) == 1:
            entrato.set()
            via.wait(2)
    monkeypatch.setattr(app, "_calcola_elenco", calcola)
    monkeypatch.setitem(app._elenco, "busy", False)
    app._elenco.pop("ancora", None)
    app._aggiorna_elenco(attendi=False, motivo="verifica")
    assert entrato.wait(2)
    app._aggiorna_elenco(attendi=False, motivo="worker")          # il worker pubblica mentre si calcola
    app._aggiorna_elenco(attendi=False, motivo="worker")          # anche due volte: un solo calcolo in più
    app._aggiorna_elenco(attendi=False)                           # una pagina: niente calcolo in più
    via.set()
    for _ in range(100):
        if not app._elenco["busy"]:
            break
        time.sleep(.02)
    assert calcoli == [1, 1] and not app._elenco["busy"]


def test_7_misura_del_ritardo_sul_sito(monkeypatch):
    from dealfinder.web import app
    db = DB()
    ora = datetime.now(timezone.utc)
    db.coda[5] = dict(listing_id=5, fonte="subito", evento="nuovo", annuncio_at=ora - timedelta(minutes=8),
                      letto_at=ora - timedelta(minutes=6), preso_at=ora, tentativi=1, valutato_at=ora,
                      esito="pubblicata", pubblicato_at=ora - timedelta(minutes=2), sito_at=None)
    import contextlib
    monkeypatch.setattr(app, "connect", lambda: contextlib.nullcontext(db))
    with _registra("scovo") as righe:
        app._ritardo_sul_sito({1, 2}, [{"id": 1}, {"id": 5}], time.time() - 40, "worker")
    assert any(m.startswith("ELENCO_AGGIORNATO") and "nuove=1" in m for m in righe)
    rit = [m for m in righe if m.startswith("SITO_RITARDO")]
    assert rit and '"annuncio_sito_p95_min": 8.0' in rit[0] and db.coda[5]["sito_at"] is not None


# ------------------------------------------------------------------ 8. ritardo vero: solo i nuovi, tutta la catena

def test_8_ritardo_subito_solo_sugli_annunci_nuovi():
    from zoneinfo import ZoneInfo
    ora = datetime.now(ZoneInfo("Europe/Rome"))

    def l(minuti, ev):
        x = _l("s", raw={"dates": {"display": (ora - timedelta(minutes=minuti)).strftime("%Y-%m-%d %H:%M:%S")}})
        x._tr_ev = ev
        return x
    st = Counter()
    T._ritardo_subito([l(3, "nuovo"), l(5, "nuovo"), l(600, "invariato"), l(900, "prezzo")], st)
    assert st["ritardo_subito_max_min"] <= 6 and st["subito_entro_15_min"] == 2


def test_8_catena_annuncio_letto_valutato_pubblicato(monkeypatch):
    db = DB()
    ann = datetime.now(timezone.utc) - timedelta(minutes=4)
    for lid in (1, 2):
        db.coda[lid] = dict(listing_id=lid, fonte="subito", evento="nuovo", annuncio_at=ann,
                            letto_at=datetime.now(timezone.utc) - timedelta(seconds=30), preso_at=None, tentativi=1,
                            valutato_at=None, esito=None, pubblicato_at=None, sito_at=None)
    st = Counter()
    with _registra("tempo_reale") as tutte:
        T._coda_fatti(db, [1, 2], [2], st)
    assert st["coda:valutati"] == 2 and 25 <= st["ritardo_letto_valutato_p95_s"] <= 40
    assert 3.5 <= st["ritardo_annuncio_pubblicata_p95_min"] <= 4.5
    righe = [m for m in tutte if m.startswith("TR_RITARDO")]
    assert len(righe) == 1 and '"id": 2' in righe[0] and "annuncio_letto_min" in righe[0]


def test_8_annuncio_at_dalla_data_di_subito():
    x = _l("s", raw={"dates": {"display": "2026-11-16 10:30:00"}})
    assert T._annuncio_at(x) == datetime(2026, 11, 16, 9, 30, tzinfo=timezone.utc)
    assert T._annuncio_at(_l("a", fonte="autoscout24")) is None


# ------------------------------------------------------------------ regole di business: impronta identica

def test_impronta_delle_regole_invariata():
    """Nessuna regola cambiata: impronta della configurazione e delle regole uguali a origin/main 7422ccf, anche con
    tutte le variabili nuove di questo ramo impostate (nessuna entra nell'impronta)."""
    from dealfinder import regole as RG
    from dealfinder import regole_log as rl
    # 19/11 (REGOLE: valore prudente, regole.ValoreBase): le impronte cambiano SOLO per la sezione valore_base
    # (prima 6d5b70387e73, 004240ac8a48, f23abb1d281a); soglie e altre regole invariate (run_tests.py --impronta)
    assert rl.impronta({})[0] == "0ee03fb95b10"
    assert rl.impronta({"RIVENDITA_VERA": "1", "SOGLIE_PV": "7000:1500", "TR_SUBITO": "1"})[0] == "8bd84b1b1abe"
    assert RG.impronta(RG.regole({})) == "bcce5b11d8f6"
    nuove = {k: "1" for k in ("TR_AS_INTERVALLO_S", "TR_SUBITO_VELOCE", "TR_SUBITO_PAGINE_VELOCE",
                              "TR_SUBITO_SECONDI_VELOCE", "TR_RECUPERO_PAGINE", "TR_RECUPERO_SECONDI", "TR_CODA",
                              "TR_CODA_TENTATIVI", "TR_CODA_RIPRESA_MIN", "TR_CODA_MAX", "TR_INVARIATI_VELOCE",
                              "TR_ARRESTO_MAX_S", "TR_COMPLETO_GIRO_MAX_MIN", "TR_RIPRESA_CONTROLLO_MIN",
                              "TR_SITO_CONTROLLO_S", "TR_WORKER_GIRO_OK_MIN")}
    assert rl.impronta(nuove)[0] == "0ee03fb95b10"


# 16/11: ogni test isolato (vedi _isolato), con pytest e con run_tests.py
for _n, _f in list(globals().items()):
    if _n.startswith("test_") and callable(_f):
        globals()[_n] = _isolato(_f)
