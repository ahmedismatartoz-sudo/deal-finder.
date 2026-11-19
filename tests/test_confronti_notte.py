"""19/11 notte, automatismo 2: confronti mirati notturni (pricing/confronti_mirati.py).

Ordine e rotazione dei gruppi (tabella confronti_gruppi), nessuna finestra di prezzo nelle ricerche, ripiego sulla sola
marca di AutoScout24, scarti di utile()/salva(), stop per tempo/pagine/blocco, notte_dovuta e richiedi_notte (li usa
la salute giornaliera), interruttore e limiti senza deploy, imbuto delle candidate, log di soli conteggi.
Niente rete né database: connessioni e ricerche finte.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dealfinder import automatismi
from dealfinder.pricing import confronti_mirati as CM
from dealfinder.web import impostazioni, scovo

QUI = os.path.dirname(os.path.abspath(__file__))
ORA = datetime(2026, 11, 20, 3, 0, tzinfo=timezone.utc)


class _Ris:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Conn:
    """Database finto: auto della notte, gruppi già cercati, stato dei lavori. Registra quello che viene scritto."""

    def __init__(self, auto=(), cercati=(), stato=None, lucchetto=True, rotto=False):
        self.auto, self.cercati, self.stato = list(auto), list(cercati), stato or {}
        self.lucchetto, self.rotto = lucchetto, rotto
        self.sql, self.segnati, self.lavori, self.commit_n = [], [], [], 0

    def execute(self, sql, p=None):
        s = " ".join(str(sql).split())
        self.sql.append(s)
        if self.rotto:
            raise RuntimeError("database giù")
        if "pg_try_advisory_lock" in s:
            return _Ris([{"ok": self.lucchetto}])
        if s.startswith("INSERT INTO confronti_gruppi"):
            self.segnati.append(tuple(p))
            return _Ris([])
        if "FROM confronti_gruppi" in s:
            return _Ris(self.cercati)
        if s.startswith("INSERT INTO job_runs"):
            self.lavori.append(tuple(p))
            return _Ris([{"id": 1}])
        if "FROM job_runs" in s:
            return _Ris([dict({"riuscita": None, "tentata": None, "richiesta": None, "adesso": ORA}, **self.stato)])
        if "count(*) FILTER" in s:
            return _Ris([{"dati_insufficienti": 900, "pochi_confronti": 700, "privati_attivi": 650,
                          "numero_non_no": 600, "zona_anno_km": 300, "viste_di_recente": 250,
                          "valutate_3_giorni": 200, "con_commercianti_da_riaprire": 0, "rinfrescabile": 0}])
        if "GROUP BY 1" in s:
            return _Ris([{"motivo": "pochi_confronti:pochi_simili", "n": len(self.auto)}])
        if "vp.dati->'priorita_raccolta'" in s:
            return _Ris(self.auto)
        return _Ris([])

    def commit(self):
        self.commit_n += 1

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _auto(i, make="Fiat", model="Panda", fuel="benzina", anno=2018, prezzo=6000, stima=None, prov="MI", tel=None):
    return {"id": i, "make": make, "model": model, "fuel": fuel, "year": anno, "mileage_km": 80000,
            "price_eur": prezzo, "province": prov, "telefono_ok": tel, "motivo": "pochi_confronti:pochi_simili",
            "rinfresco": None, "priorita": {"stima_priorita": stima, "simili": 2} if stima else None}


def _zona(p):
    return {"TO": "torino", "RA": "ravenna"}.get(str(p or "").upper(), "milano")


def _acceso(monkeypatch):
    for k in list(os.environ):
        if k.startswith(automatismi.PREFISSO_ENV) or k in ("CONFRONTI_NOTTE", "CONFRONTI_MIRATI", "VALORE_BASE"):
            monkeypatch.delenv(k, raising=False)
    impostazioni.usa_lettore(lambda: {})


def _spegni_lettore():
    impostazioni.usa_lettore(None)


class _Log(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.righe = []

    def emit(self, record):
        self.righe.append(record.getMessage())

    def __enter__(self):
        logging.getLogger("valutazione").addHandler(self)
        return self

    def __exit__(self, *a):
        logging.getLogger("valutazione").removeHandler(self)


# ------------------------------------------------------------------ SQL: ordine e data

def test_sql_ordine_e_data_come_elenco_del_sito():
    for sql in (CM.NOTTE_SQL, CM.CANDIDATE_SQL):
        s = " ".join(sql.split())
        assert re.search(r"ORDER BY \(l\.stage = 'approfondito'\) DESC, vp\.computed_at LIMIT \d+$", s), s[-120:]
        assert "GREATEST(l.last_checked_at, l.last_seen_at) > now() - make_interval(hours => %(ore)s)" in s
        assert "interval '5 days'" not in s
        assert "price_eur BETWEEN" not in s
    # il giro non guarda più solo il motivo "rinfrescabile": ogni pochi_confronti con commercianti da riaprire
    c = " ".join(CM.CANDIDATE_SQL.split())
    assert "= 'pochi_confronti:rinfrescabile'" not in c and "vp.dati->'rinfresco'->'ids'->0 IS NOT NULL" in c
    assert "split_part(COALESCE(vp.dati->>'motivo_dati', ''), ':', 1) = 'pochi_confronti'" in c
    assert "interval '5 days'" not in CM.MOTIVI_ZONA_SQL


def test_freschezza_come_scovo(monkeypatch):
    monkeypatch.delenv("FRESCHEZZA_ORE", raising=False)
    assert CM.freschezza_ore() == 48 and "FRESCHEZZA_ORE" in open(scovo.__file__, encoding="utf-8").read()
    monkeypatch.setenv("FRESCHEZZA_ORE", "x")
    assert CM.freschezza_ore() == 48


# ------------------------------------------------------------------ gruppi: ordine e rotazione

def test_gruppi_ordine_per_margine():
    sg = scovo.soglia(6000)
    righe = [_auto(1, model="Panda", stima=6000 + sg),
             _auto(2, model="Punto", stima=6000 + 3 * sg),          # migliore: primo gruppo
             _auto(3, model="500", stima=6000 + sg // 4),           # sotto metà soglia: fuori
             _auto(4, model="Tipo"),                                 # nessun simile: dopo quelle stimate
             _auto(5, model="Panda", anno=2021, stima=6000 + 2 * sg, tel=True)]
    g = CM.gruppi_notte(righe, scovo.soglia, _zona)
    assert [x["model"] for x in g] == ["Punto", "Panda", "Tipo"]
    panda = g[1]
    assert panda["ids"] == [5, 1] and (panda["anno_da"], panda["anno_a"]) == (2016, 2023)
    assert len(CM.gruppi_notte(righe, scovo.soglia, _zona, massimo=2)) == 2


def test_rotazione_riposo_e_precedenze():
    sg = scovo.soglia(6000)
    righe = [_auto(1, model="Punto", stima=6000 + 5 * sg),     # cercata 10 ore fa, con confronti: riposo (un giorno)
             _auto(2, model="Panda", stima=6000 + 4 * sg),     # cercata 30 ore fa, con confronti: torna
             _auto(3, model="Tipo", stima=6000 + 3 * sg),      # cercata 30 ore fa, 0 confronti: riposo (tre giorni)
             _auto(4, model="500", stima=6000 + 2 * sg),       # cercata 80 ore fa, 0 confronti: torna, è la più vecchia
             _auto(5, model="Bravo", stima=6000 + sg),         # mai cercata: prima di tutte
             _auto(6, model="Doblo")]                           # mai cercata, senza stima: dopo la Bravo
    cercati = {("fiat", "punto", "benzina", "milano"): (10.0, 4), ("fiat", "panda", "benzina", "milano"): (30.0, 4),
               ("fiat", "tipo", "benzina", "milano"): (30.0, 0), ("fiat", "500", "benzina", "milano"): (80.0, 0)}
    st = Counter()
    g = CM.gruppi_notte(righe, scovo.soglia, _zona, cercati=cercati, st=st)
    assert [x["model"] for x in g] == ["Bravo", "Doblo", "500", "Panda"]
    assert st["gruppi_totali"] == 6 and st["gruppi_in_riposo"] == 2 and st["gruppi_mai_cercati"] == 2
    # un'altra zona o alimentazione è un altro gruppo: la sua rotazione è separata
    altra = CM.gruppi_notte([_auto(7, model="Punto", stima=6000 + 5 * sg, prov="TO")], scovo.soglia, _zona,
                            cercati=cercati)
    assert [x["zona"] for x in altra] == ["torino"]


def test_riposo_un_giorno_o_tre():
    assert CM.in_riposo(1, 5) and not CM.in_riposo(CM.RIPOSO_ORE + 0.1, 5)
    assert CM.in_riposo(CM.RIPOSO_ORE + 0.1, 0) and not CM.in_riposo(CM.RIPOSO_VUOTO_ORE + 0.1, 0)
    assert 18 <= CM.RIPOSO_ORE <= 24 and 60 <= CM.RIPOSO_VUOTO_ORE <= 72


def test_tabella_rotazione_lettura_e_scrittura():
    CM._tabella_fatta[0] = False
    c = Conn(cercati=[{"make": "fiat", "model": "panda", "fuel": "benzina", "zona": "milano", "salvati": 3,
                       "ore": 5.5}])
    assert CM.gruppi_cercati(c) == {("fiat", "panda", "benzina", "milano"): (5.5, 3)}
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS confronti_gruppi") for s in c.sql)
    assert CM.segna_gruppo(c, {"make": " Fiat", "model": "Panda ", "fuel": "Benzina", "zona": "milano"}, 40, 7)
    assert c.segnati == [("fiat", "panda", "benzina", "milano", 40, 7)]
    # database giù: la notte va avanti senza rotazione, mai un errore
    CM._tabella_fatta[0] = False
    assert CM.gruppi_cercati(Conn(rotto=True)) == {} and CM.segna_gruppo(Conn(rotto=True), {"make": "a"}, 1, 1) is False
    CM._tabella_fatta[0] = False


def test_migrazione_063_uguale_al_codice():
    sql = open(os.path.join(QUI, "..", "db", "migrations", "063_confronti_gruppi.sql"), encoding="utf-8").read()
    senza = " ".join(r for r in sql.splitlines() if not r.strip().startswith("--"))
    assert " ".join(senza.split()).rstrip(";").strip() == " ".join(CM.GRUPPI_DDL.split())
    assert "IF NOT EXISTS" in sql and "DROP" not in sql.upper() and "ALTER" not in sql.upper()


# ------------------------------------------------------------------ ricerche: nessuna finestra di prezzo

G = {"make": "Fiat", "model": "Grande Punto", "fuel": "benzina", "zona": "milano", "ids": [1], "anno_da": 2014,
     "anno_a": 2020}
PREZZO = ("ps", "pe", "pricefrom", "priceto", "prezzo", "price")


def test_nessuna_finestra_di_prezzo():
    g = CM.gruppi_notte([_auto(1, stima=9000)], scovo.soglia, _zona)[0]
    assert not [k for k in g if "prezz" in k or "price" in k]
    p = CM.parametri_subito(G, 4, 1)
    assert not set(p) & set(PREZZO) and p["sort"] == "datedesc" and p["q"] == "Fiat Grande Punto" and p["start"] == 100
    u = CM.url_autoscout("https://as", "fiat/grande-punto", G, 2)
    assert not any(k in u.lower() for k in ("price", "prezzo")) and "sort=age&desc=1" in u and "custtype=D" in u
    assert "fregfrom=2014&fregto=2020" in u and "zip=20121&zipr=100" in u and u.endswith("page=2")


class _Sub:
    def __init__(self, stati):
        self.stati, self.chiamate = list(stati), []

    def _api(self, p):
        self.chiamate.append(p)
        codice = self.stati.pop(0) if self.stati else 200
        return SimpleNamespace(status_code=codice, json=lambda: {"ads": []})

    def pause(self):
        pass


def test_subito_senza_prezzo_e_blocco():
    sub, st = _Sub([200]), Counter()
    assert CM.cerca_subito(sub, G, 2, st) == [] and st["subito:pagine"] == 1
    assert all(not set(p) & set(PREZZO) for p in sub.chiamate)
    for codice in (403, 429):
        st = Counter()
        CM.cerca_subito(_Sub([codice]), G, 2, st)
        assert st["subito:bloccato"] == 1 and st[f"subito:http_{codice}"] == 1 and st["pagine"] == 1


class _As:
    """AutoScout24 finto: risposte per percorso ("fiat/grande-punto" o "fiat")."""

    def __init__(self, risposte, blocked=False, errori_blocco=0):
        self.risposte, self.urls, self.blocked, self.errori_blocco = risposte, [], blocked, errori_blocco

    def _page(self, url):
        self.urls.append(url)
        percorso = url.split("/lst/")[1].split("?")[0]
        return self.risposte.get(percorso, ([], 0, True))


def _parse(monkeypatch):
    from dealfinder.collectors import autoscout as A
    monkeypatch.setattr(A, "parse_item", lambda it: SimpleNamespace(seller_type=None, **it))


def test_autoscout_ripiego_sola_marca(monkeypatch):
    _parse(monkeypatch)
    CM._SLUG_NO.clear()
    ann = [{"make": "Fiat", "model": "Grande Punto"}, {"make": "Fiat", "model": "Panda"}]
    # pagina marca/modello non letta (nome del modello diverso nell'indirizzo): si ripiega sulla sola marca
    asc, st = _As({"fiat/grande-punto": ([], None, False), "fiat": (ann, 2, True)}), Counter()
    out = CM.cerca_autoscout(asc, G, 2, st)
    assert st["autoscout24:slug_non_valido"] == 1 and len(out) == 2 and st["autoscout24:pagine"] == 2
    assert all(x.seller_type == "commerciante" for x in out)
    assert "/lst/fiat/grande-punto?" in asc.urls[0] and "/lst/fiat?" in asc.urls[1]
    assert not any("price" in u for u in asc.urls)
    # il modello sbagliato lo scarta utile() (filtro in Python), non la ricerca
    assert [CM.utile(SimpleNamespace(seller_type="commerciante", year=2016, province="MI", price_eur=5000, **a), G,
                     {"MI"}) for a in ann] == [None, "altro_modello"]
    # la volta dopo (stesso processo) si va subito sulla marca: una pagina sola
    asc2, st2 = _As({"fiat": (ann, 2, True)}), Counter()
    CM.cerca_autoscout(asc2, G, 2, st2)
    assert len(asc2.urls) == 1 and st2["autoscout24:slug_non_valido"] == 1
    CM._SLUG_NO.clear()
    # pagina vuota ma leggibile: stesso ripiego
    asc3, st3 = _As({"fiat": (ann[:1], 1, True)}), Counter()
    assert len(CM.cerca_autoscout(asc3, G, 2, st3)) == 1 and st3["autoscout24:slug_non_valido"] == 1
    CM._SLUG_NO.clear()
    # pagina buona: nessun ripiego
    asc4, st4 = _As({"fiat/grande-punto": (ann[:1], 1, True)}), Counter()
    assert len(CM.cerca_autoscout(asc4, G, 2, st4)) == 1 and st4["autoscout24:slug_non_valido"] == 0
    assert not CM._SLUG_NO


def test_autoscout_blocco_non_e_slug_sbagliato(monkeypatch):
    _parse(monkeypatch)
    CM._SLUG_NO.clear()
    asc, st = _As({"fiat/grande-punto": ([], None, False)}, errori_blocco=1), Counter()
    assert CM.cerca_autoscout(asc, G, 2, st) == [] and len(asc.urls) == 1
    assert st["autoscout24:slug_non_valido"] == 0 and not CM._SLUG_NO and st["autoscout24:bloccato"] == 0
    asc, st = _As({"fiat/grande-punto": ([], None, False)}, blocked=True, errori_blocco=3), Counter()
    CM.cerca_autoscout(asc, G, 2, st)
    assert st["autoscout24:bloccato"] == 1 and len(asc.urls) == 1 and not CM._SLUG_NO


# ------------------------------------------------------------------ utile() e salva()

def _ann(**kw):
    d = dict(source="subito", seller_type="commerciante", make="Fiat", model="Grande Punto", year=2016, province="MI",
             city="Milano", price_eur=5500, fuel="benzina", mileage_km=90000, gearbox=None, power_kw=None,
             version_raw=None, title="t", description="d", region="lombardia")
    d.update(kw)
    return SimpleNamespace(**d)


def test_utile_scarta():
    prov = {"MI", "MB", "NO"}
    assert CM.utile(_ann(), G, prov) is None
    assert CM.utile(_ann(seller_type="privato"), G, prov) == "non_commerciante"
    assert CM.utile(_ann(seller_type=None), G, prov) == "non_commerciante"
    assert CM.utile(_ann(model="Punto"), G, prov) == "altro_modello"
    assert CM.utile(_ann(make="Lancia"), G, prov) == "altro_modello"
    assert CM.utile(_ann(year=2010), G, prov) == "anno"
    assert CM.utile(_ann(year=None), G, prov) == "anno"
    assert CM.utile(_ann(province="RM"), G, prov) == "fuori_zona"
    assert CM.utile(_ann(province=None), G, prov) == "fuori_zona"
    assert CM.utile(_ann(price_eur=None), G, prov) == "senza_prezzo"
    assert CM.utile(_ann(price_eur=0), G, prov) == "senza_prezzo"


def test_salva_rispetta_comune_ok_e_scarti(monkeypatch):
    from dealfinder import db, zone
    from dealfinder.core import dal_testo, normalize
    monkeypatch.setattr(dal_testo, "completa", lambda campi, titolo, testo: {})
    monkeypatch.setattr(normalize, "normalize_fields", lambda l: None)
    monkeypatch.setattr(zone, "comune_ok", lambda prov, citta: not (prov == "NO" and citta == "Lontano"))
    salvati = []

    def upsert(conn, l):
        salvati.append(l)
        return 100 + len(salvati), "nuovo" if len(salvati) == 1 else "invariato"

    monkeypatch.setattr(db, "upsert_listing", upsert)
    ann = [_ann(), _ann(province="NO", city="Lontano"), _ann(province="NO", city="Novara", source="autoscout24"),
           _ann(seller_type="privato"), _ann(province="RM"), _ann(price_eur=None), _ann(model="Panda")]
    c, st = Conn(), Counter()
    ids = CM.salva(c, ann, G, {"MI", "NO"}, st)
    assert ids == {101, 102} and len(salvati) == 2
    assert all(l.seller_type == "commerciante" and l.province in ("MI", "NO") for l in salvati)
    assert st["scarto:fuori_60km"] == 1 and st["scarto:non_commerciante"] == 1 and st["scarto:fuori_zona"] == 1
    assert st["scarto:senza_prezzo"] == 1 and st["scarto:altro_modello"] == 1
    assert st["confronti_salvati"] == 2 and st["subito:nuovo"] == 1 and st["autoscout24:invariato"] == 1
    # controllati adesso: solo last_checked_at, mai prezzi o regole
    scritture = [s for s in c.sql if s.startswith("UPDATE")]
    assert scritture and all(s.startswith("UPDATE listings SET last_checked_at = now()") for s in scritture)


# ------------------------------------------------------------------ notte(): stop e conti

def _notte(monkeypatch, auto, cerca_sub=None, cerca_as=None, conn=None, **kw):
    _acceso(monkeypatch)
    CM._tabella_fatta[0] = False
    conn = conn or Conn(auto)
    rivalutati = []

    class Es:
        def __init__(self, ids):
            self.salvata = {int(i): ("opportunita" if int(i) == 1 else "dati_insufficienti") for i in ids}
            self.stats = Counter({"categoria:opportunita": 1})

    def rivaluta(c, righe, fonte, pubblica=None, pausa_gruppo=None):
        ids = [int(r["id"]) if isinstance(r, dict) else int(r) for r in righe]
        rivalutati.append((ids, fonte, pubblica))
        return Es(ids)

    def salva(c, trovati, g, prov, st):
        st["confronti_salvati"] += len(trovati)
        return set(trovati)

    try:
        with _Log() as lg:
            out = CM.notte(connetti=lambda: conn, cerca_sub=cerca_sub or (lambda g, n, st: []),
                           cerca_as=cerca_as or (lambda g, n, st: []), rivaluta=rivaluta,
                           riapri=lambda c: Counter(), salva_fn=salva, **kw)
    finally:
        _spegni_lettore()
        CM._tabella_fatta[0] = False
    return out, conn, rivalutati, lg.righe


def _tre_gruppi():
    sg = scovo.soglia(6000)
    return [_auto(1, model="Punto", stima=6000 + 3 * sg), _auto(2, model="Panda", stima=6000 + 2 * sg),
            _auto(3, model="Tipo", stima=6000 + sg), _auto(4, model="Punto", stima=6000 + sg)]


def test_notte_cerca_segna_e_rivaluta_solo_chi_ha_confronti(monkeypatch):
    def cerca_sub(g, n, st):
        st["pagine"] += 1
        return [900] if g["model"] == "Punto" else []

    out, conn, rivalutati, righe = _notte(monkeypatch, _tre_gruppi(), cerca_sub=cerca_sub)
    assert out["gruppi"] == 3 and out["gruppi_cercati"] == 3 and out["pagine"] == 3 and out["confronti_salvati"] == 1
    # ogni gruppo cercato è segnato per la rotazione (annunci letti, confronti salvati)
    assert conn.segnati == [("fiat", "punto", "benzina", "milano", 1, 1), ("fiat", "panda", "benzina", "milano", 0, 0),
                            ("fiat", "tipo", "benzina", "milano", 0, 0)]
    # si rivalutano solo le auto del gruppo che ha avuto confronti (strada di sempre, pubblica=True)
    assert rivalutati == [([1, 4], "confronti-notte", True)]
    assert out["rivalutate"] == 2 and out["uscite_da_dati_insufficienti"] == 1 and out["nuove_buone_sito"] == 1
    for k in ("prima", "dopo", "gruppi", "gruppi_cercati", "pagine", "confronti_salvati", "rivalutate",
              "uscite_da_dati_insufficienti", "nuove_buone_sito", "minuti", "subito:nuovo", "subito:invariato",
              "autoscout24:nuovo", "autoscout24:invariato"):
        assert k in out, k
    assert any("pg_advisory_unlock" in s for s in conn.sql)
    riga = [r for r in righe if r.startswith("CONFRONTI_MIRATI_NOTTE ")]
    assert len(riga) == 1 and json.loads(riga[0].split(" ", 1)[1])["gruppi_cercati"] == 3


def test_notte_rotazione_dal_database(monkeypatch):
    conn = Conn(_tre_gruppi(), cercati=[
        {"make": "fiat", "model": "punto", "fuel": "benzina", "zona": "milano", "salvati": 2, "ore": 3.0},
        {"make": "fiat", "model": "panda", "fuel": "benzina", "zona": "milano", "salvati": 0, "ore": 40.0}])
    visti = []
    out, conn, _r, _l = _notte(monkeypatch, _tre_gruppi(), conn=conn,
                               cerca_sub=lambda g, n, st: visti.append(g["model"]) or [])
    assert visti == ["Tipo"] and out["gruppi_in_riposo"] == 2 and out["gruppi"] == 1


def test_notte_stop_per_tempo(monkeypatch):
    tic = [0, 0, 31 * 60]                               # avvio, primo gruppo, poi oltre i 30 minuti
    visti = []
    out, conn, _r, _l = _notte(monkeypatch, _tre_gruppi(), minuti_max=30,
                               orologio=lambda: tic.pop(0) if len(tic) > 1 else tic[0],
                               cerca_sub=lambda g, n, st: visti.append(g["model"]) or [])
    assert out["fermata_tempo"] == 1 and visti == ["Punto"] and out["gruppi_cercati"] == 1
    assert len(conn.segnati) == 1                       # i gruppi non cercati restano per la notte dopo


def test_notte_stop_per_pagine(monkeypatch):
    def cerca_sub(g, n, st):
        st["pagine"] += 2
        return []

    monkeypatch.setenv("AUTOMATISMO_CONFRONTI_NOTTE_PAGINE", "3")
    out, conn, _r, _l = _notte(monkeypatch, _tre_gruppi(), cerca_sub=cerca_sub)
    # _acceso toglie le variabili AUTOMATISMO_: il limite si prova dal database finto
    assert out["gruppi_cercati"] == 3
    _acceso(monkeypatch)
    impostazioni.usa_lettore(lambda: {"automatismi": {"confronti_notte_pagine": 3}})
    try:
        CM._tabella_fatta[0] = False
        conn = Conn(_tre_gruppi())
        out = CM.notte(connetti=lambda: conn, cerca_sub=cerca_sub, cerca_as=lambda g, n, st: [],
                       rivaluta=lambda *a, **k: None, riapri=lambda c: Counter(), salva_fn=lambda *a: set())
    finally:
        _spegni_lettore()
        CM._tabella_fatta[0] = False
    assert out["fermata_pagine"] == 1 and out["gruppi_cercati"] == 2 and out["pagine"] == 4


def test_notte_stop_per_blocco_403_429(monkeypatch):
    for codice in (403, 429):
        chiamate = []

        def cerca_sub(g, n, st, codice=codice):
            chiamate.append("subito")
            return CM.cerca_subito(_Sub([codice]), g, n, st)

        def cerca_as(g, n, st):
            chiamate.append("autoscout24")
            st["pagine"] += 1
            st["autoscout24:bloccato"] = 1
            return []

        out, conn, rivalutati, _l = _notte(monkeypatch, _tre_gruppi(), cerca_sub=cerca_sub, cerca_as=cerca_as)
        # una richiesta per fonte, poi basta: nessun gruppo segnato come cercato (si riprova domani)
        assert chiamate == ["subito", "autoscout24"] and out["fermata_blocco"] == 1
        assert out["gruppi_cercati"] == 0 and out["gruppi_non_letti"] == 1 and conn.segnati == [] and not rivalutati
        assert out[f"subito:http_{codice}"] == 1


def test_notte_una_fonte_bloccata_l_altra_continua(monkeypatch):
    def cerca_sub(g, n, st):
        st["subito:bloccato"] = 1
        return []

    visti = []
    out, conn, _r, _l = _notte(monkeypatch, _tre_gruppi(), cerca_sub=cerca_sub,
                               cerca_as=lambda g, n, st: visti.append(g["model"]) or [])
    assert visti == ["Punto", "Panda", "Tipo"] and out["gruppi_cercati"] == 3 and "fermata_blocco" not in out


def test_notte_lucchetto_e_errori(monkeypatch):
    out, conn, rivalutati, _l = _notte(monkeypatch, _tre_gruppi(), conn=Conn(_tre_gruppi(), lucchetto=False))
    assert out == {"altro_processo": True} and not rivalutati and not conn.segnati
    # una ricerca che solleva non ferma la notte
    def rotta(g, n, st):
        raise RuntimeError("rete")

    out, conn, _r, _l = _notte(monkeypatch, _tre_gruppi(), cerca_sub=rotta)
    assert out["subito:errore"] == 3 and out["gruppi_cercati"] == 3
    # database giù: nessuna eccezione verso fuori
    out, _c, _r, _l = _notte(monkeypatch, [], conn=Conn(rotto=True))
    assert out.get("errore") == "RuntimeError"


# ------------------------------------------------------------------ interruttore e limiti senza deploy

def test_interruttore_e_limiti(monkeypatch):
    _acceso(monkeypatch)
    try:
        assert CM.notte_attiva()
        assert (CM.limite_notte("gruppi", CM.NOTTE_GRUPPI), CM.limite_notte("pagine", CM.NOTTE_PAGINE),
                CM.limite_notte("minuti", CM.NOTTE_MINUTI), CM.limite_notte("rivaluta", CM.NOTTE_RIVALUTA_MAX)) \
            == (400, 1500, 150, 4000)
        impostazioni.usa_lettore(lambda: {"automatismi": {"confronti_notte": False, "confronti_notte_gruppi": 25,
                                                           "confronti_notte_minuti": "x"}})
        assert not CM.notte_attiva() and CM.limite_notte("gruppi", 400) == 25 and CM.limite_notte("minuti", 150) == 150
        c = Conn(_tre_gruppi())
        assert CM.notte(connetti=lambda: c) == {"spento": True} and c.sql == []
        assert CM.notte_dovuta(c) is False and CM.richiedi_notte(c, "salute") is False and c.sql == []
        assert CM.notte_nel_cron() is None
        # lo spegnimento della notte non spegne il giro di sempre nel sito
        assert CM.attivo()
    finally:
        _spegni_lettore()
    monkeypatch.setenv("AUTOMATISMO_CONFRONTI_NOTTE", "0")
    assert not CM.notte_attiva()
    monkeypatch.setenv("AUTOMATISMO_CONFRONTI_NOTTE", "1")
    monkeypatch.setenv("CONFRONTI_NOTTE", "0")
    assert not CM.notte_attiva()


# ------------------------------------------------------------------ notte_dovuta e richiedi_notte

def _s(riuscita=None, tentata=None, richiesta=None):
    ore = lambda h: None if h is None else ORA - timedelta(hours=h)      # noqa: E731
    return {"riuscita": ore(riuscita), "tentata": ore(tentata), "richiesta": ore(richiesta), "adesso": ORA}


def test_dovuta():
    assert CM.dovuta(_s())                                              # mai fatta
    assert CM.dovuta(_s(riuscita=21, tentata=21))                       # una volta al giorno
    assert not CM.dovuta(_s(riuscita=10, tentata=10))                   # già fatta
    assert CM.dovuta(_s(riuscita=10, tentata=10, richiesta=4))          # richiesta più recente dell'ultima riuscita
    assert not CM.dovuta(_s(riuscita=10, tentata=10, richiesta=12))     # richiesta già servita
    assert not CM.dovuta(_s(riuscita=30, tentata=1))                    # tentativo appena partito o fallito: no cicli
    assert not CM.dovuta(_s(riuscita=10, tentata=1, richiesta=0.5))
    assert CM.dovuta(_s(riuscita=30, tentata=CM.RIPROVA_NOTTE_ORE + 0.1))
    assert not CM.dovuta({})


def test_notte_dovuta_dal_database(monkeypatch):
    _acceso(monkeypatch)
    try:
        assert CM.notte_dovuta(Conn(stato=_s())) is True
        assert CM.notte_dovuta(Conn(stato=_s(riuscita=5, tentata=5))) is False
        assert CM.notte_dovuta(Conn(stato=_s(riuscita=5, tentata=5, richiesta=4))) is True
        assert CM.notte_dovuta(Conn(rotto=True)) is False
    finally:
        _spegni_lettore()


def test_richiedi_notte(monkeypatch):
    _acceso(monkeypatch)
    try:
        c = Conn(stato=_s(riuscita=5, tentata=5))
        with _Log() as lg:
            assert CM.richiedi_notte(c, "Salute: calo del 20%  tel 3331234567 a@b.it") is True
        assert len(c.lavori) == 1 and c.lavori[0][0] == CM.JOB_RICHIESTA == "confronti_mirati:richiesta"
        motivo = json.loads(c.lavori[0][1])["motivo"]
        assert motivo.startswith("salute:_calo_del_tel_a_b") and len(motivo) <= 40
        assert "@" not in motivo and not re.search(r"\d", motivo)
        assert c.commit_n >= 1 and any(r.startswith("CONFRONTI_MIRATI_RICHIESTA ") for r in lg.righe)
        assert not any("@" in r or re.search(r"\d{7,}", r) for r in lg.righe)
        # già richiesta e non ancora servita: non se ne scrive un'altra
        c = Conn(stato=_s(riuscita=5, tentata=5, richiesta=2))
        assert CM.richiedi_notte(c, "salute") is False and c.lavori == []
        # richiesta servita (notte riuscita dopo): se ne può scrivere una nuova
        c = Conn(stato=_s(riuscita=1, tentata=1, richiesta=2))
        assert CM.richiedi_notte(c, "salute") is True
        # richiesta vecchia mai servita (oltre un giorno): si riscrive
        c = Conn(stato=_s(riuscita=60, tentata=60, richiesta=30))
        assert CM.richiedi_notte(c, "salute") is True
        assert CM.richiedi_notte(Conn(rotto=True), "salute") is False
    finally:
        _spegni_lettore()


# ------------------------------------------------------------------ imbuto delle candidate

def test_imbuto_quando_candidate_zero(monkeypatch):
    _acceso(monkeypatch)
    CM._GIORNO.update(g=None, pagine=0)
    c = Conn()
    try:
        st = CM.giro(c)
    finally:
        _spegni_lettore()
    assert st["candidate"] == 0 and st["imbuto:dati_insufficienti"] == 900 and st["imbuto:zona_anno_km"] == 300
    assert st["imbuto:con_commercianti_da_riaprire"] == 0
    sql = CM.imbuto_sql()
    assert sql.count("FILTER") == len(CM.IMBUTO_PASSI) + 1 and "%(ore)s" in sql
    assert list(CM.imbuto_candidate(c))[:2] == ["dati_insufficienti", "pochi_confronti"]
    assert CM.imbuto_candidate(Conn(rotto=True)) == {}
    with _Log() as lg:
        CM.diagnostica(st)
    d = json.loads(lg.righe[0].split(" ", 1)[1])
    assert lg.righe[0].startswith("CONFRONTI_MIRATI {") and d["imbuto"]["viste_di_recente"] == 250
    assert not [k for k in d if k.startswith("imbuto:")]
    CM._GIORNO.update(g=None, pagine=0)


# ------------------------------------------------------------------ mai Facebook, mai Unlocker; log di soli conteggi

def test_niente_facebook_ne_unlocker():
    src = open(CM.__file__, encoding="utf-8").read()
    nomi = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            nomi |= {a.name.lower() for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            nomi |= {(n.module or "").lower()} | {a.name.lower() for a in n.names}
        elif isinstance(n, (ast.FunctionDef, ast.Name)):
            nomi.add((n.name if isinstance(n, ast.FunctionDef) else n.id).lower())
    assert not [x for x in nomi if "facebook" in x or "marketplace" in x or "unlocker" in x or "brightdata" in x], nomi
    assert "facebook" not in [f.lower() for f in CM.FONTI] and set(CM.FONTI) == {"autoscout24", "subito"}
    # l'unico uso del nome è lo spegnimento esplicito del Web Unlocker sul client di Subito della notte
    codice = [r.split("#")[0].strip() for r in src.splitlines()]
    assert [r for r in codice if "unlocker" in r] == ["sub.unlocker_zone = sub.unlocker_key = None"]
    assert "ps" not in CM.parametri_subito(G, 4, 0) and "pricefrom" not in src and "priceto" not in src


def test_log_solo_conteggi(monkeypatch):
    def cerca_sub(g, n, st):
        st["pagine"] += 1
        st["subito:nuovo"] += 1
        return [3331234567]                             # un "id" lungo tra i trovati non deve finire nel log

    out, conn, _r, righe = _notte(monkeypatch, _tre_gruppi(), cerca_sub=cerca_sub)
    assert righe and all(r.startswith(("CONFRONTI_MIRATI_NOTTE ", "CONFRONTI_NOTTE ", "CONFRONTI_MIRATI ")) for r in righe)
    for r in righe:
        assert "@" not in r and not re.search(r"\d{7,}", r) and "http" not in r, r
    # nell'esito e nel log solo numeri e nomi di contatori: nessun elenco di id o di annunci
    assert all(isinstance(v, (int, float, bool, dict)) for v in out.values())
    assert all(isinstance(x, int) for v in out.values() if isinstance(v, dict) for x in v.values())
