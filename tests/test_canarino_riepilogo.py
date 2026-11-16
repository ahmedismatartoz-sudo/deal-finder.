"""16/11 (fondamenta): canarino (dealfinder/canarino.py), riepilogo giornaliero (dealfinder/riepilogo_giorno.py) e
impronta delle regole di business (run_tests.py --impronta). Senza database e senza rete: connessioni e HTTP finti."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from dealfinder import canarino as CA
from dealfinder import riepilogo_giorno as RG

QUI = os.path.dirname(os.path.abspath(__file__))


class _Ris:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return list(self.righe)

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Conn:
    def __init__(self, risposte):
        self.risposte = risposte
        self.sql = []

    def execute(self, sql, p=None):
        self.sql.append((sql, p))
        for k, v in self.risposte.items():
            if k in sql:
                return _Ris(v(p) if callable(v) else v)
        return _Ris([])

    def commit(self):
        pass

    def rollback(self):
        pass


def _auto(n=150):
    return [{"id": i, "prezzo": 5000 + i, "foto": f"/api/foto/{i}/0?s=x&v=2", "nuova": f"2026-11-16T{i // 60:02d}:{i % 60:02d}:00"}
            for i in range(1, n + 1)]


def _controlla(*a, **kw):
    kw.setdefault("regole", lambda: [])          # le regole effettive si provano a parte (test_canarino_regole_*)
    return CA.controlla(*a, **kw)


def _conn_ok(prezzo=5150, stato="attivo", battito=1.0, giro=2.0):
    return Conn({"giro = %s": lambda p: [{"minuti": battito if p[0] == "worker_nuovi" else giro}],
                 "FROM listings WHERE id": [{"price_eur": prezzo, "status": stato}]})


def _http(status=200, tipo="image/jpeg"):
    chiamate = []

    def get(url, headers):
        chiamate.append(url)
        return SimpleNamespace(status_code=status, headers={"content-type": tipo}, json=lambda: {})
    return get, chiamate


# ------------------------------------------------------------------ canarino
def test_canarino_ok_con_elenco_in_memoria():
    get, chiamate = _http()
    e = _controlla(_conn_ok(), items=_auto(), http_get=get, base_url="https://sito.invalid")
    assert e["ok"], e["motivi"]
    assert e["numeri"]["recente_id"] == 150 and e["numeri"]["auto_sito"] == 150     # l'auto più recente con foto
    assert chiamate == ["https://sito.invalid/api/foto/150/0?s=x&v=2"]


def test_canarino_ko_con_i_motivi():
    get, _ = _http(status=404)
    e = _controlla(_conn_ok(prezzo=1, stato="scomparso", battito=12.0, giro=None), items=_auto(20), http_get=get,
                     base_url="https://sito.invalid")
    assert not e["ok"]
    testo = " | ".join(e["motivi"])
    for parola in ("worker fermo", "giro 'nuovi'", "poche auto sul sito: 20", "l'annuncio è 'scomparso'",
                   "prezzo sul sito", "risposta 404"):
        assert parola in testo, parola
    assert CA.riga_log(e).startswith("CANARINO ko: worker fermo")


def test_canarino_prezzo_annuncio_e_tipo_foto():
    auto = _auto()
    for it in auto:                    # ribasso del sito: si confronta il prezzo VERO dell'annuncio
        it["prezzo_annuncio"], it["prezzo"] = it["prezzo"], it["prezzo"] - 700
    get, _ = _http(tipo="text/html")
    e = _controlla(_conn_ok(), items=auto, http_get=get, base_url="https://sito.invalid")
    assert e["motivi"] == [f"foto dell'auto {e['numeri']['recente_id']}: non è un'immagine (text/html)"]


def test_canarino_api_poi_copia_salvata(monkeypatch):
    from dealfinder.web import elenco_salvato as ES
    monkeypatch.delenv("CANARINO_TOKEN", raising=False)
    blob = ES.comprimi({"v": 1, "items": _auto(), "opens": {}})
    conn = _conn_ok()
    conn.risposte["FROM sito_cache"] = [{"dati": blob, "eta": 7200}]

    def get(url, headers):
        if url.endswith("/api/affari"):
            return SimpleNamespace(status_code=401, headers={}, json=lambda: {})
        return SimpleNamespace(status_code=200, headers={"content-type": "image/jpeg"})
    e = _controlla(conn, http_get=get, base_url="https://sito.invalid")
    assert e["numeri"]["fonte_elenco"] == "copia_salvata" and e["numeri"]["api"] == "API 401"
    assert e["motivi"] == ["copia salvata dell'elenco vecchia: 2.0 ore (massimo 1.0)"]
    # API aperta: si usa la risposta dell'API
    get2 = lambda url, h: SimpleNamespace(status_code=200, headers={"content-type": "image/jpeg"},
                                          json=lambda: {"items": _auto()})
    e2 = _controlla(_conn_ok(), http_get=get2, base_url="https://sito.invalid")
    assert e2["ok"] and e2["numeri"]["fonte_elenco"] == "api"


def test_canarino_esegui_registra_e_non_solleva(monkeypatch):
    monkeypatch.delenv("CANARINO", raising=False)
    conn = _conn_ok()

    @contextmanager
    def connetti():
        yield conn
    get, _ = _http()
    e = CA.esegui("prova", connetti=connetti, items=_auto(), http_get=get, regole=lambda: [])
    assert e["ok"]
    ins = [p for s, p in conn.sql if "INSERT INTO canarino_esiti" in s]
    assert len(ins) == 1 and ins[0][0] is True and ins[0][3] == "prova"

    @contextmanager
    def rotta():
        raise RuntimeError("database giù")
        yield
    e = CA.esegui("prova", connetti=rotta)
    assert not e["ok"] and "database giù" in e["motivi"][0]
    monkeypatch.setenv("CANARINO", "0")
    assert CA.esegui("prova", connetti=rotta)["spento"]


# ------------------------------------------------------------------ riepilogo giornaliero
DATI = {"sito": {"n": 480, "commercianti": 55, "ore": 0.1}, "sito_ieri": 470,
        "movimenti": {"entrate": 40, "uscite": 30},
        "funnel": [{"giorno": "2026-11-16", "passo": "valutata", "entrate": 3000, "uscite": 900}],
        "versioni": [{"commit_git": "7422ccf0abcdef", "dal": datetime(2026, 11, 16, 12, tzinfo=timezone.utc),
                      "processi": "web, worker:tempo_reale"}],
        "canarino": {"ok": 22, "ko": 2}, "canarino_motivi": [{"motivo": "worker fermo: battito 9 min", "n": 2}],
        "cron": [{"job": "collect:opportunita", "ok": 7, "falliti": 1, "in_corso": 0}],
        "allarmi": [{"controllo": "cron", "n": 1, "volte": 3}]}


def test_riepilogo_testo():
    ogg, corpo = RG.testo(DATI, datetime(2026, 11, 16, 7, 30, tzinfo=timezone.utc))
    assert ogg == "Skovo riepilogo del 16/11/2026: 480 auto sul sito, da guardare"
    for pezzo in ("Auto sul sito: 480 (+10 da ieri alla stessa ora: 470)", "entrate 40, uscite 30",
                  "Canarino: 22 ok, 2 ko", "ko 2 volte: worker fermo", "7422ccf0abcd dal 16/11 13:00",
                  "collect:opportunita: 7 ok, 1 falliti, 0 in corso  <-- FALLITI", "valutata: 3000 -> tolte 900",
                  "Allarmi della sentinella (24 ore): cron 1"):
        assert pezzo in corpo, pezzo
    assert not any(c.isdigit() and len(c) > 8 for c in corpo.split() if c.startswith("3"))   # niente telefoni


def test_riepilogo_senza_tabelle():
    ogg, corpo = RG.testo({}, datetime(2026, 11, 16, 9, tzinfo=timezone.utc))
    assert "? auto sul sito, tutto regolare" in ogg
    assert "non disponibile" in corpo and "canarino_esiti assente" in corpo


def test_riepilogo_raccogli_salta_le_tabelle_assenti():
    conn = Conn({"to_regclass": lambda p: [{"c": p[0] in ("job_runs",)}],
                 "FROM job_runs": [{"job": "collect:mercato", "ok": 1, "falliti": 0, "in_corso": 0}]})
    d = RG.raccogli(conn)
    assert d == {"cron": [{"job": "collect:mercato", "ok": 1, "falliti": 0, "in_corso": 0}]}


def test_riepilogo_esegui_una_volta_al_giorno_dopo_le_8_e_riprova(monkeypatch):
    """Simulazione della prenotazione (la stessa SQL è provata su Postgres vero in test_query_vere_*)."""
    monkeypatch.delenv("RIEPILOGO_GIORNO", raising=False)
    righe = {}

    def prenota(p):
        giorno, at, massimo, minuti = p
        r = righe.get(giorno)
        if r is None:
            righe[giorno] = {"tentativi": 1, "at": at, "ok": None}
            return [{"tentativi": 1}]
        if r["ok"] is not True and r["tentativi"] < massimo and (at - r["at"]).total_seconds() > minuti * 60:
            r.update(tentativi=r["tentativi"] + 1, at=at, ok=None)
            return [{"tentativi": r["tentativi"]}]
        return []

    def esito(p):
        righe[p[1]]["ok"] = p[0]
        return []
    conn = Conn({"INSERT INTO riepilogo_inviati": prenota, "UPDATE riepilogo_inviati": esito,
                 "to_regclass": [{"c": False}]})

    @contextmanager
    def connetti():
        yield conn
    mandate, riesce = [], [False]
    invia = lambda o, c: mandate.append(o) or riesce[0]
    ora = lambda h, m=0: datetime(2026, 11, 16, h, m, tzinfo=timezone.utc)
    assert RG.esegui(connetti, invia, ora(5)) == {"presto": True}                    # 6 a Roma
    assert RG.esegui(connetti, invia, ora(8))["inviato"] is False                  # Brevo non risponde
    assert RG.esegui(connetti, invia, ora(8, 10)) == {"gia_fatto": True}           # troppo presto per riprovare
    riesce[0] = True
    r = RG.esegui(connetti, invia, ora(8, 30))
    assert r["inviato"] and r["tentativo"] == 2 and len(mandate) == 2
    assert RG.esegui(connetti, invia, ora(15)) == {"gia_fatto": True}               # riuscito: basta per oggi
    assert len(mandate) == 2
    riesce[0] = False                                                               # giorno dopo: al massimo 3 tentativi
    for h in (8, 9, 10, 11, 12):
        RG.esegui(connetti, invia, datetime(2026, 11, 17, h, tzinfo=timezone.utc))
    assert len(mandate) == 5


# ------------------------------------------------------------------ impronta delle regole (run_tests.py)
def test_impronta_riferimento_coerente_e_differenze():
    import hashlib
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_tests_mod", os.path.join(QUI, "..", "run_tests.py"))
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    with open(rt.IMPRONTA_FILE, encoding="utf-8") as f:
        rif = json.load(f)
    canon = json.dumps(rif["dati"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(canon.encode()).hexdigest()[:12] == rif["impronta"]      # file non ritoccato a mano
    assert rif["dati"]["scovo"]["SOGLIE_PV"][0] == [7000, 1500]
    assert rif["dati"]["zona"]["ROMA"] == ["FR", "LT", "RI", "RM", "VT"]
    cambiato = json.loads(json.dumps(rif))
    cambiato["dati"]["sito_regole"]["km_max"] = 160000
    assert rt.differenze_impronta(rif, cambiato) == ["sito_regole.km_max: 150000 -> 160000"]


# ------------------------------------------------------------------ query vere su un Postgres temporaneo (se c'è)
def _pg():
    import re
    from tests import pg_vero

    class C(pg_vero.Conn):
        def execute(self, sql, params=None):            # pg_vero riconosce le SELECT solo senza il commento iniziale
            return super().execute(re.sub(r"^\s*/\*.*?\*/\s*", "", sql), params)
    c = pg_vero.nuovo_db("fond_canarino")
    if c is None:
        return None
    c = C(c.d, c.db)
    pg_vero.migra(c, "034_sentinella.sql")
    for sql in ("CREATE TABLE job_runs (id BIGSERIAL PRIMARY KEY, job TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL "
                "DEFAULT now(), finished_at TIMESTAMPTZ, ok BOOLEAN, stats JSONB)",
                "CREATE TABLE tempo_reale (giro TEXT PRIMARY KEY, ultimo_at TIMESTAMPTZ, stats JSONB)",
                "CREATE TABLE listings (id BIGINT PRIMARY KEY, price_eur INT, status TEXT)",
                "CREATE TABLE sito_cache (chiave TEXT PRIMARY KEY, dati BYTEA NOT NULL, n INT, firma TEXT, "
                "salvato_at TIMESTAMPTZ NOT NULL DEFAULT now())",
                "CREATE TABLE sito_storico (listing_id BIGINT PRIMARY KEY, dati JSONB NOT NULL, prima TIMESTAMPTZ NOT NULL "
                "DEFAULT now(), ultima TIMESTAMPTZ NOT NULL DEFAULT now())",
                "CREATE TABLE sito_funnel_giorno (giorno DATE NOT NULL, passo TEXT NOT NULL, entrate INT NOT NULL, "
                "uscite INT NOT NULL, aggiornato TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (giorno, passo))"):
        c.execute(sql)
    return c


def _senza_postgres(nome: str) -> bool:
    """Postgres temporaneo assente: su GitHub è un errore (ubuntu-latest ha initdb), altrove un salto VISIBILE."""
    import warnings
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise AssertionError(f"{nome}: Postgres temporaneo non disponibile su GitHub (initdb non trovato o non avviato)")
    warnings.warn(f"SALTATO {nome}: Postgres temporaneo non disponibile (initdb assente)")
    print(f"SALTATO {nome}: Postgres temporaneo non disponibile (initdb assente)")
    return True


def test_query_vere_canarino_e_riepilogo():
    c = _pg()
    if c is None and _senza_postgres("test_query_vere_canarino_e_riepilogo"):
        return
    from dealfinder.web import elenco_salvato as ES
    c.execute("INSERT INTO tempo_reale VALUES ('worker_nuovi', now() - interval '1 minute', '{}'), "
              "('nuovi', now() - interval '3 minutes', '{}')")
    c.execute("INSERT INTO listings VALUES (150, 5150, 'attivo')")
    c.execute("INSERT INTO sito_cache (chiave, dati, n) VALUES ('elenco', %s, 150)",
              (ES.comprimi({"v": 1, "items": _auto(), "opens": {}}),))
    get = lambda url, h: (SimpleNamespace(status_code=503, headers={}, json=lambda: {}) if url.endswith("/api/affari")
                          else SimpleNamespace(status_code=200, headers={"content-type": "image/jpeg"}))
    e = _controlla(c, http_get=get, base_url="https://sito.invalid")
    assert e["ok"], e["motivi"]
    assert e["numeri"]["fonte_elenco"] == "copia_salvata" and e["numeri"]["battito_min"] < 5
    assert CA.precedente_negativo(c) is False                    # tabella non ancora creata
    CA.registra(c, e, "prova")
    assert CA.precedente_negativo(c) is False
    CA.registra(c, {"ok": False, "motivi": ["worker fermo: battito 9 min"], "numeri": {}}, "prova")
    assert CA.precedente_negativo(c) is True

    c.execute("INSERT INTO sito_istantanea (listing_id, prezzo, commerciante) VALUES (1, 5000, false), (2, 9000, true)")
    c.execute("INSERT INTO sito_storico (listing_id, dati, prima, ultima) VALUES "
              "(1, '{}', now(), now()), (3, '{}', now() - interval '3 days', now() - interval '5 hours')")
    c.execute("INSERT INTO sito_funnel_giorno (giorno, passo, entrate, uscite) VALUES (current_date, 'valutata', 100, 20)")
    c.execute("INSERT INTO config_impronte (processo, impronta, commit_git) VALUES ('web', 'abc', '7422ccf')")
    c.execute("INSERT INTO job_runs (job, finished_at, ok) VALUES ('collect:mercato', now(), true), "
              "('collect:opportunita', now(), false)")
    c.execute("INSERT INTO sentinella_allarmi (chiave, controllo, testo) VALUES ('cron_fallito:x', 'cron', 'x')")
    d = RG.raccogli(c)
    assert d["sito"]["n"] == 2 and d["sito"]["commercianti"] == 1
    assert d["movimenti"] == {"entrate": 1, "uscite": 1}
    assert d["canarino"] == {"ok": 1, "ko": 1} and d["canarino_motivi"][0]["motivo"].startswith("worker fermo")
    assert [v["commit_git"] for v in d["versioni"]] == ["7422ccf"]
    assert {x["job"]: x["falliti"] for x in d["cron"]} == {"collect:mercato": 0, "collect:opportunita": 1}
    ogg, corpo = RG.testo(d)
    assert "2 auto sul sito, da guardare" in ogg and "entrate 1, uscite 1" in corpo


def test_riepilogo_prenotazione_su_postgres_vero():
    c = _pg()
    if c is None and _senza_postgres("test_riepilogo_prenotazione_su_postgres_vero"):
        return
    c.execute("DROP TABLE IF EXISTS riepilogo_inviati")
    c.execute(RG.TABELLA_SQL)
    c.execute(RG.COLONNA_SQL)
    c.execute(RG.COLONNA_SQL)                                   # idempotente
    g = "2026-11-16"
    t = lambda m: datetime(2026, 11, 16, 8, m, tzinfo=timezone.utc).isoformat()
    pren = lambda m: c.execute(RG.PRENOTA_SQL, (g, t(m), RG.TENTATIVI_MAX, RG.RIPROVA_MIN)).fetchone()
    assert pren(0)["tentativi"] == 1
    assert pren(5) is None                                       # tentativo in corso o appena fatto
    c.execute("UPDATE riepilogo_inviati SET ok = %s WHERE giorno = %s", (False, g))
    assert pren(25)["tentativi"] == 2
    c.execute("UPDATE riepilogo_inviati SET ok = %s WHERE giorno = %s", (False, g))
    assert pren(50)["tentativi"] == 3
    c.execute("UPDATE riepilogo_inviati SET ok = %s WHERE giorno = %s", (False, g))
    assert pren(59 - 0) is None                                  # già 3 tentativi
    c.execute("DELETE FROM riepilogo_inviati")
    assert pren(0)["tentativi"] == 1
    c.execute("UPDATE riepilogo_inviati SET ok = %s WHERE giorno = %s", (True, g))
    assert pren(40) is None                                      # riuscito: mai una seconda email


# ------------------------------------------------------------------ canarino: regole effettive, auto da 15 minuti, conferma
def test_canarino_regole_effettive_diverse_ko_subito():
    from dealfinder import impronta_regole as IR
    rif = IR.leggi_riferimento()
    attesi = IR.attesi_produzione(rif)
    assert IR.differenze_effettive(rif, {}) and attesi["scovo.MARGINE_MINIMO"] == 800
    # regole come in produzione (dati + eccezioni dichiarate): nessuna differenza
    ora = {}
    for k, v in attesi.items():
        nodo = ora
        parti = k.split(".")
        for p_ in parti[:-1]:
            nodo = nodo.setdefault(p_, {})
        nodo[parti[-1]] = v
    assert IR.differenze_effettive(rif, ora) == []
    ora["sito_regole"]["km_max"] = 160000
    assert IR.differenze_effettive(rif, ora) == ["sito_regole.km_max: 150000 -> 160000"]
    get, _ = _http()
    e = CA.controlla(_conn_ok(), items=_auto(), http_get=get, base_url="https://sito.invalid",
                     regole=lambda: ["sito_regole.km_max: 150000 -> 160000"])
    assert e["motivi"] == ["regole effettive diverse dal riferimento: sito_regole.km_max: 150000 -> 160000"]
    assert CA.conferma(e, False) is e and CA.riga_log(e).startswith("CANARINO ko: regole effettive diverse")


def test_canarino_regole_del_processo_di_test():
    """Le regole effettive del processo dei test (default del codice) differiscono dal riferimento SOLO per le
    eccezioni di produzione dichiarate: il confronto a runtime funziona davvero."""
    from dealfinder import impronta_regole as IR
    rif = IR.leggi_riferimento()
    diverse = IR.differenze_effettive(rif)
    chiavi = {d.split(":")[0] for d in diverse}
    assert chiavi <= set(rif["ammessi_produzione"]), diverse


def test_canarino_auto_entrata_da_almeno_15_minuti():
    adesso = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    auto = [{"id": 1, "prezzo": 5001, "foto": "/f/1", "nuova": "2026-11-16T11:30:00+00:00"},
            {"id": 2, "prezzo": 5002, "foto": "/f/2", "nuova": "2026-11-16T11:50:00+00:00"},   # entrata da 10 minuti
            {"id": 3, "prezzo": 5003, "foto": "/f/3", "nuova": datetime(2026, 11, 16, 11, 40)}]  # in memoria, senza fuso
    get, _ = _http()
    e = _controlla(_conn_ok(prezzo=5003), items=auto, http_get=get, base_url="https://x.invalid", adesso=adesso,
                   min_auto=1)
    assert e["numeri"]["recente_id"] == 3 and e["ok"], e


def test_canarino_ko_solo_dopo_due_negativi():
    neg = {"ok": False, "motivi": ["worker fermo: battito 9 min"], "numeri": {}}
    primo = CA.conferma(neg, prima_negativo=False)
    assert primo["attenzione"] and CA.riga_log(primo).startswith("CANARINO attenzione: worker fermo")
    secondo = CA.conferma(neg, prima_negativo=True)
    assert not secondo.get("attenzione") and CA.riga_log(secondo).startswith("CANARINO ko: worker fermo")
    assert CA.conferma({"ok": True, "motivi": [], "numeri": {}}, True)["ok"]
