"""CENSIMENTO (dealfinder/pipeline/censimento.py) con una connessione finta: conteggi della zona, campione valutato
con lo stesso calcolo del sito senza scrivere e senza AI, una sola riga di log anonima, tempo massimo, coda lavori."""
import inspect
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from dealfinder.pipeline import censimento as CE
from dealfinder.pipeline import coda_lavori as CL

QUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORA = datetime.now(timezone.utc)


def _auto(i, prezzo, make="volkswagen", model="golf", fuel="diesel", year=2018, km=90000, **kw):
    d = {"id": i, "source": "subito", "url": f"https://x/{i}", "make": make, "model": model, "version_raw": None,
         "title": f"{make} {model}", "description": "", "year": year, "mileage_km": km, "fuel": fuel,
         "gearbox": "manuale", "power_kw": 81, "price_eur": prezzo, "price_flags": [], "seller_type": "privato",
         "damage_class": "sconosciuto", "damage_items": None, "vehicle_id": 1000 + i, "vehicle_identity": None,
         "stage_reason": None, "telefono_ok": True, "damage_declared": False, "problem_search": False,
         "province": "MI", "city": "Milano", "status": "attivo", "last_seen_at": ORA - timedelta(hours=3),
         "last_checked_at": ORA - timedelta(hours=3), "first_seen_at": ORA - timedelta(days=20),
         "disappeared_at": None}
    d.update(kw)
    return d


GOLF = _auto(1, 6500)                                                         # mai valutata, molto sotto il mercato
PANDA = _auto(2, 4000, make="fiat", model="panda", fuel="benzina", year=2016, km=60000)   # gemelle disperse
SENZA_TEL = _auto(3, 6400, telefono_ok=False)                                 # nessun numero: esclusa
MONZA = _auto(4, 9900, province=None, city="Monza")                           # provincia dal comune, prezzo pieno
ROMA = _auto(5, 6000, province=None, city="Roma")                             # fuori zona (dal comune)
SUL_SITO = _auto(6, 6600)                                                     # valutata ieri: già sul sito
DOPPIONE = _auto(7, 6000, stage_reason="doppione | scomparso")
VECCHIA = _auto(8, 6550, km=88000)                                            # valutata 10 giorni fa
TUTTE = {a["id"]: a for a in (GOLF, PANDA, SENZA_TEL, MONZA, ROMA, SUL_SITO, DOPPIONE, VECCHIA)}
SALVATE = {6: ("opportunita", ORA - timedelta(days=1)), 8: ("non_conveniente", ORA - timedelta(days=10))}
MERCATO = {("volkswagen", "golf", "diesel"): [_auto(100 + n, p, km=85000 + n * 900) for n, p in enumerate(
    [9000, 9200, 9400, 9500, 9600, 9800, 10000, 10100, 10300, 10500, 10800, 11000])],
           ("fiat", "panda", "benzina"): [_auto(200 + n, p, make="fiat", model="panda", fuel="benzina", year=2016,
                                                km=60000) for n, p in enumerate([3000, 5600, 6000, 6200])]}


class _Ris:
    def __init__(self, righe):
        self.righe = [dict(r) for r in righe]

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Conn:
    def __init__(self):
        self.sql = []
        self.commit_n = 0
        self.pagine = 0

    def execute(self, sql, p=None):
        s = " ".join(sql.split())
        self.sql.append(s)
        if "LEFT JOIN valutazioni_policy v" in s:
            assert p["ore"] == 48 and "MI" in p["prov"] and "RM" not in p["prov"] and p["fb"] is False
            assert p["anno"] == 2007 and p["km"] == 150000 and "seller_type = 'privato'" in s
            assert "description" not in s and "v.dati" not in s                     # solo colonne leggere
            assert "ORDER BY l.id LIMIT" in s and p["pagina"] == CE.PAGINA
            self.pagine += 1
            out = []
            for a in sorted(TUTTE.values(), key=lambda a: a["id"]):
                if a["id"] <= p["dopo"]:
                    continue
                cat, at = SALVATE.get(a["id"], (None, None))
                out.append({k: a[k] for k in ("id", "source", "province", "city", "make", "model", "fuel", "year",
                                              "mileage_km", "price_eur", "telefono_ok", "stage_reason",
                                              "first_seen_at")}
                           | {"categoria": cat, "valutata_at": at, "ha_valuations": a["id"] == 8})
            return _Ris(out[:p["pagina"]])
        if s.startswith("SELECT id, make, model, fuel FROM listings WHERE id = ANY"):
            return _Ris([{k: TUTTE[i][k] for k in ("id", "make", "model", "fuel")} for i in p[0] if i in TUTTE])
        if "l.telefono_ok, l.damage_declared" in s and "FROM listings l WHERE l.id = ANY" in s:
            return _Ris([TUTTE[i] for i in p[0] if i in TUTTE])
        if "FROM listings WHERE status='attivo' AND make=%s" in s:
            return _Ris(MERCATO.get(tuple(p), []))
        return _Ris([])          # commercianti, valore reale, stima AI, difetti, ricambi, calibrazione, cascata

    def commit(self):
        self.commit_n += 1

    def rollback(self):
        pass


class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.righe = []

    def emit(self, record):
        self.righe.append(record.getMessage())


def _niente_ai(monkeypatch):
    from dealfinder.ai import client as AI
    from dealfinder.ai import giudice as GI

    def vietato(*a, **k):
        raise AssertionError("il censimento non deve usare l'AI")
    monkeypatch.setattr(GI, "giudica", vietato)
    for nome in ("available", "chiama", "call", "ask"):
        if hasattr(AI, nome):
            monkeypatch.setattr(AI, nome, vietato)


def _esegui(monkeypatch, **kw):
    _niente_ai(monkeypatch)
    h = _Log()
    lg = logging.getLogger("censimento")
    lg.addHandler(h)
    try:
        conn = Conn()
        res = CE.esegui(conn, memoria_mb=0, seme=1, **kw)
    finally:
        lg.removeHandler(h)
    return conn, res, [r for r in h.righe if r.startswith("CENSIMENTO")]


def test_censimento_finto_sola_lettura_senza_ai_e_log_anonimo(monkeypatch):
    conn, res, righe = _esegui(monkeypatch, n=1000, minuti=5)
    assert conn.pagine == 1
    z = res["zona"]
    assert z["totale"] == 7 and z["fuori_zona_dal_comune"] == 1 and z["provincia"] == {"MI": 6, "MB": 1}
    assert z["mai_valutate"] == 5 and z["valutate"] == 2 and z["valutate_da_oltre_7_giorni"] == 1
    assert z["categoria"] == {"opportunita": 1, "non_conveniente": 1} and z["sul_sito_ora_stima"] == 1
    assert z["scartate_prima_doppione"] == 1 and z["con_riga_valuations"] == 1
    assert z["da_valutare_mai"] == 4 and z["da_valutare_vecchia"] == 1           # il doppione non si campiona
    c = res["campione"]
    assert c["scelte"] == 5 and c["valutate"] == 5 and c["per_strato"] == {"mai": 4, "vecchia": 1}
    assert c["esclusioni"] == {"senza_numero": 1} and c["categorie"]["dati_insufficienti"] == 1
    assert c["buone"] == 2                                     # la Golf (mai valutata) e quella valutata 10 giorni fa
    assert c["proiezione"]["mai"] == {"totale": 4, "campione": 4, "buone": 1, "quota_pct": 25.0, "stima_buone": 1,
                                      "stima_buone_senza_segnali": c["proiezione"]["mai"]["stima_buone_senza_segnali"],
                                      "stima_buone_nuove": 1, "stima_da_trattare": 0}
    assert c["stima_buone_non_valutate"] == 2 and c["stima_buone_nuove_non_sul_sito"] == 2
    # sola lettura, niente commit, una sola riga di log, nessun dato del venditore
    assert not any(s.split()[0].upper() in ("INSERT", "UPDATE", "DELETE", "CREATE") for s in conn.sql)
    assert conn.commit_n == 0 and len(righe) == 1
    dati = json.loads(righe[0].split(" ", 1)[1])
    assert dati["giudice_ai"] == "spento" and len(dati["esempi"]) == 2
    e = dati["esempi"][0]
    assert set(e) >= {"marca", "modello", "anno", "km", "prezzo", "M", "margine", "provincia"}
    assert e["marca"] == "volkswagen" and e["margine"] >= e["soglia"] and e["M"] > e["prezzo"]
    for vietato in ("https://", "\"url\"", "\"id\"", "Milano\"", "Monza\""):
        assert vietato not in righe[0], vietato


def test_tempo_massimo_risultati_parziali(monkeypatch):
    import time
    conn, res, righe = _esegui(monkeypatch, n=1000, minuti=1, t0=time.time() - 120)
    assert res["campione"]["fermata:tempo"] == 1 and res["campione"]["valutate"] == 0
    assert res["zona"]["totale"] == 7 and len(righe) == 1                        # i conteggi ci sono comunque


def test_pagine_piccole_stesso_risultato(monkeypatch):
    monkeypatch.setattr(CE, "PAGINA", 3)
    _niente_ai(monkeypatch)
    conn = Conn()
    tot, da = CE.conta(conn)
    assert conn.pagine == 3 and tot["totale"] == 7 and len(da) == 5             # 3 + 3 + 2 righe


def test_parametri_e_voce_solo_nel_sito_senza_ai():
    assert CE.campione_n({}) == 600 and CE.campione_n({"CENSIMENTO_N": "50"}) == 50
    assert CE.campione_n({"CENSIMENTO_N": "999999"}) == CE.N_MAX
    assert CE.minuti_massimi(None, {}) == 25 and CE.minuti_massimi(None, {"CENSIMENTO_MIN": "40"}) == 40
    for modo in (None, "lunghi", "tutti"):                     # mai nella coda del cron: gira subito nel sito
        old = os.environ.pop("LAVORI_NEL_CRON", None)
        if modo:
            os.environ["LAVORI_NEL_CRON"] = modo
        try:
            assert not CL.spostabile("censimento:1611") and CL._prefisso("censimento:1611") == "censimento:"
        finally:
            os.environ.pop("LAVORI_NEL_CRON", None)
            if old is not None:
                os.environ["LAVORI_NEL_CRON"] = old
    assert CL.mancano("censimento:1611", {}) == []                                 # niente ANTHROPIC_API_KEY
    src = inspect.getsource(CL.esegui)
    assert '_una_volta(f"censimento:{v[11:71]}")' in src and "_ce.lavoro(v)" in src
    ce = open(os.path.join(QUI, "dealfinder", "pipeline", "censimento.py")).read()
    assert "READ ONLY" in ce and "giudice=False" in ce
    for vietato in ("from ..ai import", "valuta_e_scrivi(", "_scrivi(", "_in_coda_danni", "INSERT", "UPDATE "):
        assert vietato not in ce, vietato
    # il sito mette la voce nella coda (ramo generico di BREVO_IMPORT_LISTE)
    app = open(os.path.join(QUI, "dealfinder", "web", "app.py")).read()
    i = app.index("if _cl.spostabile(v) and not forza:")
    blocco = app[i:i + 500]
    assert "_cl.metti(v)" in blocco and "if _cl._prefisso(v):" in blocco and "_cl.esegui(v)" in blocco
