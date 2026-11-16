"""16/11 (fond-server): giro corto del cron opportunità con i lavori pesanti a turno, battito delle righe in corso,
falsi allarmi della sentinella, lavori una tantum dal sito al cron, filtri di build di Render.

I test con "pg" usano un Postgres VERO temporaneo (tests/pg_vero.py); se initdb non c'è non fanno nulla."""
from __future__ import annotations

import inspect
import json
import os
import sys
import time
from contextlib import contextmanager

import yaml

from dealfinder import db as DB
from dealfinder import sentinella as S
from dealfinder.pipeline import ciclo, coda_lavori as CL, collect, turni

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pg_vero  # noqa: E402

QUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JOB_RUNS = ("CREATE TABLE job_runs (id BIGSERIAL PRIMARY KEY, job TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL "
            "DEFAULT now(), finished_at TIMESTAMPTZ, ok BOOLEAN, stats JSONB)")


def _connetti(c):
    @contextmanager
    def cm():
        yield c
    return cm


@contextmanager
def env(**kw):
    vecchie = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in vecchie.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ------------------------------------------------------------------------------------------- 2. battito e interrotti
def test_pg_battito_e_chiusura_interrotti():
    c = pg_vero.nuovo_db("fond_battito")
    if c is None:
        return
    c.execute(JOB_RUNS)
    righe = {
        "vivo": "INSERT INTO job_runs (job, stats) VALUES ('collect:x', jsonb_build_object('battito', now()))",
        "fermo": "INSERT INTO job_runs (job, stats) VALUES ('collect:y', jsonb_build_object('battito', now() - interval '1 hour'))",
        "vecchio_senza": "INSERT INTO job_runs (job, started_at) VALUES ('lead:z', now() - interval '20 hours')",
        "giovane_senza": "INSERT INTO job_runs (job, started_at) VALUES ('lead:w', now() - interval '2 hours')",
        "fatto": "INSERT INTO job_runs (job, finished_at, ok, stats) VALUES ('collect:y', now(), true, "
                 "jsonb_build_object('battito', now() - interval '5 hours'))",
        "mio": "INSERT INTO job_runs (job, stats) VALUES ('collect:m', jsonb_build_object('battito', now() - interval '2 hours'))",
    }
    ids = {k: c.execute(sql + " RETURNING id").fetchone()["id"] for k, sql in righe.items()}
    # battito subito di una riga: stats.battito aggiornato, gli altri dati restano
    c.execute("UPDATE job_runs SET stats = stats || '{\"letti\": 5}'::jsonb WHERE id = %s", (ids["fermo"],))
    assert DB.battito_ora(c, [ids["fermo"]]) == 1
    r = c.execute("SELECT stats FROM job_runs WHERE id = %s", (ids["fermo"],)).fetchone()["stats"]
    assert r["letti"] == 5 and "battito" in r
    c.execute("UPDATE job_runs SET stats = stats || jsonb_build_object('battito', now() - interval '1 hour') "
              "WHERE id = %s", (ids["fermo"],))
    chiuse = DB.chiudi_interrotti(c, escludi=[ids["mio"]])
    assert {r["id"] for r in chiuse} == {ids["fermo"], ids["vecchio_senza"]}
    st = {r["id"]: r for r in c.execute("SELECT id, ok, finished_at, stats FROM job_runs").fetchall()}
    assert st[ids["fermo"]]["ok"] is False and st[ids["fermo"]]["stats"]["interrotto"] is True
    assert st[ids["fermo"]]["stats"]["letti"] == 5                       # i dati del giro restano
    for k in ("vivo", "giovane_senza", "mio"):
        assert st[ids[k]]["finished_at"] is None, k
    assert st[ids["fatto"]]["ok"] is True


def test_pg_fratelli_con_la_data_chiusi_solo_se_fermi():
    """valore_reale_notte:YYYYMMDD cambia nome ogni notte: le righe ferme delle notti prima ora si chiudono."""
    c = pg_vero.nuovo_db("fond_fratelli")
    if c is None:
        return
    c.execute(JOB_RUNS)
    ferma = c.execute("INSERT INTO job_runs (job, stats) VALUES ('valore_reale_notte:20261114', "
                      "jsonb_build_object('battito', now() - interval '3 hours')) RETURNING id").fetchone()["id"]
    viva = c.execute("INSERT INTO job_runs (job, stats) VALUES ('valore_reale_notte:20261115', "
                     "jsonb_build_object('battito', now())) RETURNING id").fetchone()["id"]
    altro = c.execute("INSERT INTO job_runs (job, stats) VALUES ('valore_reale_nottex', "
                      "jsonb_build_object('battito', now() - interval '3 hours')) RETURNING id").fetchone()["id"]
    assert ciclo.radice("valore_reale_notte:20261116") == "valore_reale_notte"
    assert ciclo.radice("soglia7k:a") == "soglia7k:a" and ciclo.radice("subito_completo:2026-11-16") == "subito_completo"
    chiuse = ciclo._chiudi_fratelli(c, "valore_reale_notte:20261116")
    assert [r["id"] for r in chiuse] == [ferma]
    aperte = {r["id"] for r in c.execute("SELECT id FROM job_runs WHERE finished_at IS NULL").fetchall()}
    assert aperte == {viva, altro}


def test_log_job_batte_e_smette():
    eseguite = []

    class C:
        def execute(self, sql, p=()):
            eseguite.append((sql, p))

            class R:
                def fetchone(self):
                    return {"id": 77}
            return R()

        def commit(self):
            pass
    with env(BATTITO="0"):                        # niente filo nei test: solo l'insieme delle righe da battere
        fin = DB.log_job(C(), "prova")
        assert 77 in DB._battiti
        fin(True, {"n": 1})
        assert 77 not in DB._battiti
    assert "finished_at=now()" in eseguite[-1][0]


def test_inizia_batte_e_rilascia():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_lavori import FintoDB
    db = FintoDB()
    with env(BATTITO="0"):
        lav = ciclo.inizia("valnuove:fs", connetti=db.connetti)
        assert lav.id in DB._battiti
        lav.fatto()
        assert lav.id not in DB._battiti


# ------------------------------------------------------------------------------------------- 1. giro corto e turni
def test_scelta_a_turno_il_piu_urgente_che_ci_sta():
    T = turni.Turno
    cand = [T("supercar_raccolta", 110, 5.0), T("confronti_extra", 40, 2.0), T("prima:ricalcolo@6", 45, 0.5, priorita=1),
            T("prima:leadsp:3", 200, 900.0)]
    assert turni.scegli(cand, 120).nome == "prima:ricalcolo@6"           # il ricalcolo per primo (come dal 6/11)
    assert turni.scegli(cand[:2] + cand[3:], 120).nome == "supercar_raccolta"
    assert turni.scegli(cand[:2] + cand[3:], 60).nome == "confronti_extra"
    assert turni.scegli(cand, 30) is None
    assert turni.stima_da_durate([], 40) == 40
    assert turni.stima_da_durate([3600, 3000, 4200], 40) == 69.0          # mediana 60 min + 15%


def test_giro_tempo_per_e_tetto_sotto_le_tre_ore():
    assert turni.GIRO_ORE_MAX < 3 and turni.GIRO_ORE_MAX * 8 <= 24      # 8 giri al giorno, ognuno sotto le 3 ore
    g = turni.Giro("prova", t0=time.time() - 120 * 60, ore=2.75, connetti=lambda: None)
    assert 44 < g.resto_min() < 46
    assert g.tempo_per("analisi", 10) and not g.tempo_per("supercar", 40)
    assert g.rimandati == ["supercar"]
    with env(GIRO_LEGGERO="0"):
        assert g.tempo_per("supercar", 400)


def test_lavori_a_turno_nel_tempo_che_resta(monkeypatch):
    fatti = []
    dovuti = {"confronti_extra": (40, 3.0), "supercar_raccolta": (110, 2.0), "prima:ricalcolo@6": (45, 0.0)}

    def finti(_t0, fatti_gia=None):
        return [turni.Turno(n, s, r, (lambda n=n: fatti.append(n) or {}), 1 if "ricalcolo" in n else 0)
                for n, (s, r) in dovuti.items() if n not in (fatti_gia or set())]
    monkeypatch.setattr(collect, "turni_dovuti", finti)
    monkeypatch.setattr(turni, "esegui", lambda nome, fn, connetti=None: fn())
    g = turni.Giro("opportunita", t0=time.time() - 60 * 60, ore=2.75, connetti=lambda: None)   # restano ~105 min
    g._scrivi = lambda extra: None
    collect.lavori_a_turno(g, time.time(), "opportunita")
    assert fatti == ["prima:ricalcolo@6", "confronti_extra"]            # supercar (110 min) non ci sta più
    assert "supercar_raccolta" in g.rimandati


def test_lavoro_affamato_parte_se_quasi_ci_sta(monkeypatch):
    fatti = []
    monkeypatch.setattr(collect, "turni_dovuti", lambda _t0, f=None: [] if f else [
        turni.Turno("supercar_raccolta", 110, 30.0, lambda: fatti.append(1) or {})])
    monkeypatch.setattr(turni, "esegui", lambda nome, fn, connetti=None: fn())
    g = turni.Giro("opportunita", t0=time.time() - 30 * 60, ore=2.75, connetti=lambda: None)   # restano ~120 - 15
    g._scrivi = lambda extra: None
    collect.lavori_a_turno(g, time.time(), "opportunita")
    assert fatti == [1]                     # in ritardo di oltre un giorno: 110 * 0,85 <= 120 min
    fatti.clear()
    g2 = turni.Giro("opportunita", t0=time.time() - 90 * 60, ore=2.75, connetti=lambda: None)
    g2._scrivi = lambda extra: None
    collect.lavori_a_turno(g2, time.time(), "opportunita")
    assert fatti == []


def test_turni_dovuti_supercar_valuta_una_volta_dopo_la_raccolta(monkeypatch):
    ore = {"turno:supercar_raccolta": 2.0, "turno:supercar_valuta": 30.0}
    monkeypatch.setattr(turni, "ore_dall_ultimo", lambda job, connetti=None, prefisso=False: ore.get(job))
    monkeypatch.setattr(turni, "stima", lambda nome, default_min=None, connetti=None: 50.0)
    with env(SUPERCAR_EU_CRON="1", CONFRONTI_EXTRA=None, COLLECT_PRIMA=None, ZONA_CRON=None):
        nomi = {t.nome: t.ritardo_ore for t in collect.turni_dovuti(time.time())}
        assert nomi == {"supercar_valuta": 0.0}           # raccolta fatta 2 ore fa: solo la valutazione
        ore["turno:supercar_valuta"] = 1.0
        assert collect.turni_dovuti(time.time()) == []     # valutata dopo la raccolta: niente
        ore["turno:supercar_raccolta"] = 25.0
        assert {t.nome for t in collect.turni_dovuti(time.time())} == {"supercar_raccolta"}


def test_turni_dovuti_ricalcolo_una_volta_al_giorno(monkeypatch):
    ore = {"ricalcolo": 7.0}
    monkeypatch.setattr(turni, "ore_dall_ultimo", lambda job, connetti=None, prefisso=False: ore.get(job))
    monkeypatch.setattr(turni, "stima", lambda nome, default_min=None, connetti=None: 45.0)
    with env(COLLECT_PRIMA="ricalcolo@6", SUPERCAR_EU_CRON=None, CONFRONTI_EXTRA=None, ZONA_CRON=None,
             RICALCOLO_SUBITO=None):
        assert collect.turni_dovuti(time.time()) == []                    # 7 ore fa (anche dal cron mercato): no
        with env(RICALCOLO_SUBITO="1"):                                   # tanti confronti nuovi: subito
            assert [t.nome for t in collect.turni_dovuti(time.time())] == ["prima:ricalcolo@6"]
        ore["ricalcolo"] = 25.0
        assert [t.nome for t in collect.turni_dovuti(time.time())] == ["prima:ricalcolo@6"]


def test_giro_ogni_tre_ore_solo_incrementale(monkeypatch):
    chiamate = []
    monkeypatch.setattr(collect, "giro_zona", lambda completo=None: chiamate.append(completo) or True)
    ore = {"zona_cron:completo": 10.0}
    monkeypatch.setattr(turni, "ore_dall_ultimo", lambda job, connetti=None, prefisso=False: ore.get(job))

    @contextmanager
    def lucchetto(nome, connetti=None, attesa_s=0, passo_s=60):
        yield nome != "zona_cron:lettura" or not ore.get("occupato")
    monkeypatch.setattr(turni, "lucchetto", lucchetto)
    assert collect.zona_del_giro(None) and chiamate == [False]           # completo fatto 10 ore fa: incrementale
    ore["zona_cron:completo"] = 60.0                                       # notte ferma da 2 giorni: decide il giro
    collect.zona_del_giro(None)
    assert chiamate[-1] is None
    ore["occupato"] = True                                                  # la notte sta leggendo la zona: si salta
    assert collect.zona_del_giro(None) is False and len(chiamate) == 2
    with env(GIRO_LEGGERO="0"):
        collect.zona_del_giro(None)
        assert chiamate[-1] is None


def test_notte_zona_completa_quando_c_e_tempo(monkeypatch):
    ore = {"zona_cron:completo": 23.0}
    monkeypatch.setattr(turni, "ore_dall_ultimo", lambda job, connetti=None, prefisso=False: ore.get(job))
    monkeypatch.setattr(turni, "stima", lambda nome, default_min=None, connetti=None: 285.0)
    monkeypatch.setattr(ciclo, "_due", lambda job, h: False)
    t0 = time.time() - 3.5 * 3600                     # cron mercato partito alle 01:13, raccolta finita alle 04:43
    assert collect.zona_notte_ok(t0)
    assert not collect.zona_notte_ok(time.time() - 6 * 3600)              # non finirebbe entro 10 ore: no
    ore["zona_cron:completo"] = 5.0
    assert not collect.zona_notte_ok(t0)                                    # già fatta oggi
    ore["zona_cron:completo"] = 23.0
    monkeypatch.setattr(ciclo, "_due", lambda job, h: True)                 # stanotte tocca a mercato_nord
    assert not collect.zona_notte_ok(t0)
    ore["zona_cron:completo"] = 40.0                                        # ma mai oltre 36 ore senza zona completa
    assert collect.zona_notte_ok(t0)


def test_configurazione_copiata_senza_segreti():
    env_ = {"ZONA_CRON": "1", "ZC_SOGLIA": "0.92", "SOGLIE_PV": "7000:1500", "POLICY_COSTO_FISSO": "200",
            "TORINO_MAX": "45000", "ANTHROPIC_API_KEY": "sk-x", "DATABASE_URL": "postgres://u:p@h/d",
            "BRIGHTDATA_API_KEY": "b", "SCRAPER_PROXY": "http://p", "SECRET_KEY": "s", "PLATE_SALT": "x",
            "ADMIN_PASSWORD": "pw", "RENDER_GIT_COMMIT": "abc", "PATH": "/bin", "COLLECT_PRIMA": "leadsp:3",
            "CRON_T0": "1", "BREVO_IMPORT_LISTE": "x", "GOOGLE_CLIENT_SECRET": "g", "PYTHON_VERSION": "3.12"}
    out = turni.variabili_da_copiare(env_)
    assert out == {"ZONA_CRON": "1", "ZC_SOGLIA": "0.92", "SOGLIE_PV": "7000:1500", "POLICY_COSTO_FISSO": "200",
                   "TORINO_MAX": "45000"}
    dove = {"ZC_SOGLIA": "0.9"}
    assert turni.applica_config(out, dove) == sorted(["POLICY_COSTO_FISSO", "SOGLIE_PV", "TORINO_MAX", "ZONA_CRON", "ZC_SOGLIA"])
    assert dove["ZC_SOGLIA"] == "0.92"


def test_pg_configurazione_e_giro_con_avanzamento():
    c = pg_vero.nuovo_db("fond_config")
    if c is None:
        return
    c.execute(JOB_RUNS)
    c.execute("CREATE TABLE tempo_reale (giro TEXT PRIMARY KEY, ultimo_at TIMESTAMPTZ, stats JSONB)")
    with env(ZC_SOGLIA="0.92", ANTHROPIC_API_KEY="sk-segreta"):
        assert turni.salva_config(connetti=_connetti(c)) > 0
    cfg = turni.carica_config(connetti=_connetti(c))
    assert cfg["ZC_SOGLIA"] == "0.92" and "ANTHROPIC_API_KEY" not in cfg
    assert "sk-segreta" not in json.dumps(c.execute("SELECT stats FROM tempo_reale").fetchall())
    with env(BATTITO="0"):
        g = turni.Giro("opportunita", t0=time.time() - 600, ore=2.75, connetti=_connetti(c)).apri()
        g.fase("zona")
        r = c.execute("SELECT finished_at, stats FROM job_runs WHERE id = %s", (g.id,)).fetchone()
        assert r["finished_at"] is None and r["stats"]["fase"] == "zona"   # avanzamento scritto durante il giro
        g.fasi["zona"] = 0
        g.chiudi(True)
        g.chiudi(True)                                                   # due volte (atexit): una sola chiusura
    r = c.execute("SELECT job, ok, stats FROM job_runs WHERE id = %s", (g.id,)).fetchone()
    assert r["job"] == "giro:opportunita" and r["ok"] is True and "zona" in r["stats"]["fasi"]
    assert 9 < r["stats"]["minuti"] < 12


def test_pg_turno_registra_durata_per_la_stima():
    c = pg_vero.nuovo_db("fond_turno")
    if c is None:
        return
    c.execute(JOB_RUNS)

    @contextmanager
    def lucchetto(nome, connetti=None, attesa_s=0, passo_s=60):
        yield True
    vecchio = turni.lucchetto
    turni.lucchetto = lucchetto
    try:
        with env(BATTITO="0"):
            assert turni.esegui("confronti_extra", lambda: {"letti": 3}, connetti=_connetti(c))["letti"] == 3
            assert turni.esegui("supercar_valuta", lambda: 1 / 0, connetti=_connetti(c)) == {"errore": True}
    finally:
        turni.lucchetto = vecchio
    righe = {r["job"]: r for r in c.execute("SELECT job, ok, stats FROM job_runs").fetchall()}
    assert righe["turno:confronti_extra"]["ok"] is True and righe["turno:supercar_valuta"]["ok"] is False
    c.execute("UPDATE job_runs SET started_at = finished_at - interval '50 minutes' WHERE job = 'turno:confronti_extra'")
    assert turni.stima("confronti_extra", connetti=_connetti(c)) == 57.5
    assert turni.ore_dall_ultimo("turno:confronti_extra", connetti=_connetti(c)) < 1
    assert turni.ritardo("turno:mai", 24, connetti=_connetti(c)) == 1000.0


def test_supercar_registrato_anche_nel_giro_in_fila():
    """Prima la riga 'supercar_eu' (letta da _due) non si scriveva mai: supercar a ogni giro."""
    src = inspect.getsource(collect._lavori_in_fila)
    assert '_lj(_c, "supercar_eu")' in src and '_due("supercar_eu"' in src


def test_main_giro_corto_cablato():
    src = open(os.path.join(QUI, "dealfinder", "pipeline", "collect.py")).read()
    main = src[src.index('if __name__ == "__main__":'):]
    for pezzo in ("_turni.salva_config()", "inizia_giro(\"opportunita\"", "zona_del_giro(_giro)",
                  "lavori_a_turno(_giro, _t0_loop, mode)", "chiudi_interrotti", "MERCATO_NOTTE_FASE",
                  "notte_dovuta(float(os.environ[\"CRON_T0\"]))", "_giro.chiudi(True)"):
        assert pezzo in main, pezzo
    # la fotografia della configurazione prima di ogni cambio dell'ambiente del giro
    assert main.index("_turni.salva_config()") < main.index("zona_del_giro(_giro)")


# ------------------------------------------------------------------------------------------- 3. sentinella
def _it(**kw):
    base = {"id": 1, "prezzo": 8000, "km": 80000, "carb": "Benzina", "anno": 2015, "pv": {"margine": 2000},
            "zona": "Bergamo (BG)", "commerciante": True}
    base.update(kw)
    return base


def test_istantanea_sezione_fascia_per_i_commercianti_della_fascia():
    assert S._riga_istantanea(_it(), None)[9] == "fascia"
    assert S._riga_istantanea(_it(incidentata=True), None)[9] == "fascia:inc"
    assert S._riga_istantanea(_it(prezzo=25000), None)[9] == "affari"          # fuori fascia: lontano
    assert S._riga_istantanea(_it(commerciante=False), None)[9] == "affari"     # privato
    assert S._riga_istantanea(_it(diesel_extra=True), None)[9] == "diesel_extra"
    assert S.sezione_base("fascia") == "affari" and S.sezione_base("fascia:inc") == "inc"
    assert S.sezione_base("eco") == "eco" and S.sezione_base(None) is None


def test_tetto_commercianti_niente_allarme_falso_con_la_fascia():
    righe = [{"listing_id": i, "commerciante": True, "sezione": "fascia"} for i in range(30)]
    righe += [{"listing_id": 100 + i, "commerciante": True, "sezione": "affari"} for i in range(25)]
    righe += [{"listing_id": 200 + i, "commerciante": False, "sezione": "affari"} for i in range(50)]
    r = S.c_tetto_commercianti(None, {"istantanea": righe})
    assert r["numeri"]["commercianti"] == 25 and r["ok"]                 # 25 lontani su 40, 55 in tutto su 60


def test_sotto_soglia_controlla_anche_la_fascia():
    righe = [{"listing_id": 1, "prezzo": 8000, "margine": 100, "soglia": 1500, "sezione": "fascia"},
             {"listing_id": 2, "prezzo": 8000, "margine": 100, "soglia": 1500, "sezione": "eco"}]
    r = S.c_sotto_soglia(None, {"istantanea": righe})
    assert r["numeri"]["controllate"] == 1 and r["numeri"]["sotto"] == 1 and not r["ok"]


class _Finta:
    def __init__(self, risposte):
        self.risposte = risposte

    def execute(self, sql, p=None):
        tag = sql.split("/*S:")[1].split("*/")[0] if "/*S:" in sql else ""
        righe = self.risposte.get(tag, [])

        class R:
            def fetchall(self):
                return list(righe)

            def fetchone(self):
                return righe[0] if righe else None
        return R()


def test_cron_battito_fermo_e_giri_lunghi():
    c = _Finta({"cron_ultimi": [{"job": "giro:opportunita", "ore": 1.0}],
                "cron_battito": [{"job": "valore_reale_notte:20261115", "n": 1, "minuti": 95.0}],
                "cron_giri": [{"n": 8, "lunghi": 1, "max_min": 250.0}],
                "cron_in_corso": [{"job": "giro:opportunita", "n": 1, "ore": 4.0},
                                  {"job": "collect:mercato", "n": 1, "ore": 10.0}]})
    r = S.c_cron(c, {}, attese={"giro:opportunita": 7})
    chiavi = {a["chiave"] for a in r["allarmi"]}
    assert chiavi == {"cron_battito:valore_reale_notte:20261115", "cron_giro_lungo", "cron_in_corso:giro:opportunita"}
    assert r["numeri"]["giri_24h"] == 8 and r["numeri"]["giri_lunghi_24h"] == 1
    assert S.in_corso_limite("collect:mercato") == 12 and S.in_corso_limite("giro:opportunita") == 3.5


# ------------------------------------------------------------------------------------------- 4. lavori dal sito al cron
def test_voci_spostabili_e_chiavi_di_sempre():
    with env(LAVORI_NEL_CRON=None):
        assert CL.spostabile("raccolta60:tel1611") and CL.spostabile("perche_fuori:a") and CL.spostabile("prova_cieca:30")
        assert not CL.spostabile("mix:1200")                                # brevi: restano nel sito
        assert not CL.spostabile("su_misura:1450") and not CL.spostabile("zona:0811") and not CL.spostabile("1")
    with env(LAVORI_NEL_CRON="tutti"):
        assert CL.spostabile("mix:1200") and CL.spostabile("fermi:1400@x") and not CL.spostabile("wa30:x")
    with env(LAVORI_NEL_CRON="0"):
        assert not CL.spostabile("raccolta60:tel1611")
    assert CL.mancano("mix:1", {}) == ["BREVO_API_KEY"] and CL.mancano("mix:1", {"BREVO_API_KEY": "x"}) == []
    assert CL.mancano("prova_cieca:30:30:solo_calcolo", {}) == [] and CL.mancano("raccolta60:x", {}) == []
    # stesse chiavi _una_volta di prima: una voce già fatta dal sito (es. stasera raccolta60:tel1611) non si rifà
    src = inspect.getsource(CL.esegui)
    for chiave in ('_una_volta(f"raccolta60:{v[:60]}")', '_una_volta(v)', '_una_volta(f"brevo_mix:{v}")',
                   '_una_volta(f"fuori84:{v[:60]}")', '_una_volta(f"fermi:{v[:80]}")', '_una_volta(f"giro_lotto:{v[11:91]}")',
                   '_una_volta("utenti:" + v.split(":", 1)[1])', '_una_volta(f"brevo_rimetti:{v}")'):
        assert chiave in src, chiave
    assert "time.sleep(" not in src
    app = open(os.path.join(QUI, "dealfinder", "web", "app.py")).read()
    assert "R60.lavoro(" not in app and "_pf.lavoro(v)" not in app and "_cl.metti(v)" in app


def test_raccolta60_gia_fatta_non_si_rifa(monkeypatch):
    fatti = []
    monkeypatch.setattr(ciclo, "_una_volta", lambda k: fatti.append(k) and False)
    import dealfinder.pipeline.raccolta60 as R60
    monkeypatch.setattr(R60, "lavoro", lambda e: (_ for _ in ()).throw(AssertionError("non deve ripartire")))
    CL.esegui("raccolta60:tel1611")
    assert fatti == ["raccolta60:raccolta60:tel1611"]


def test_pg_coda_lavori_cron_e_ripiego_nel_sito():
    c = pg_vero.nuovo_db("fond_coda")
    if c is None:
        return
    cn = _connetti(c)
    assert CL.metti("raccolta60:tel1611", connetti=cn) and CL.metti("raccolta60:tel1611", connetti=cn)
    CL.metti("mix:1200", connetti=cn)
    CL.metti("utenti:0811", connetti=cn)
    assert c.execute("SELECT count(*) AS n FROM coda_lavori").fetchone()["n"] == 3       # un riavvio non duplica
    fatti = []
    # il giro ogni 3 ore: solo le brevi; a mix manca BREVO_API_KEY nel cron -> al sito
    with env(BREVO_API_KEY=None, LAVORI_CRON=None):
        es = CL.esegui_coda(False, connetti=cn, esegui_fn=fatti.append)
    assert fatti == ["utenti:0811"] and es == {"mix:1200": "web", "utenti:0811": "fatto"}
    # la notte: le lunghe
    CL.esegui_coda(True, connetti=cn, esegui_fn=fatti.append)
    assert fatti[-1] == "raccolta60:tel1611"
    st = {r["valore"]: r["stato"] for r in c.execute("SELECT valore, stato FROM coda_lavori").fetchall()}
    assert st == {"raccolta60:tel1611": "fatto", "mix:1200": "web", "utenti:0811": "fatto"}
    # il sito fa solo quelle passate a lui (e le vecchie non prese)
    CL.metti("perche_fuori:b", connetti=cn)
    CL.esegui_coda(None, chi="web", connetti=cn, esegui_fn=fatti.append)
    assert fatti[-1] == "mix:1200" and "perche_fuori:b" not in fatti
    c.execute("UPDATE coda_lavori SET creato_at = now() - interval '31 hours' WHERE valore = 'perche_fuori:b'")
    CL.esegui_coda(None, chi="web", connetti=cn, esegui_fn=fatti.append)
    assert fatti[-1] == "perche_fuori:b"
    # tempo finito: le altre tornano in coda
    CL.metti("perche_fuori:c", connetti=cn)
    CL.metti("prova_cieca:5", connetti=cn)
    with env(ANTHROPIC_API_KEY="x"):
        es = CL.esegui_coda(True, budget_min=0, connetti=cn, esegui_fn=fatti.append)
    assert es["rimandati"] == 2
    assert c.execute("SELECT count(*) AS n FROM coda_lavori WHERE stato = 'in_coda'").fetchone()["n"] == 2


# ------------------------------------------------------------------------------------------- 5. render.yaml
def test_render_filtri_di_build_e_facebook_senza_deploy_automatico():
    bp = yaml.safe_load(open(os.path.join(QUI, "render.yaml")))
    servizi = {s["name"]: s for s in bp["services"]}
    for nome, s in servizi.items():
        bf = s.get("buildFilter") or {}
        assert bf.get("paths") or bf.get("ignoredPaths"), nome
        assert "envVars" in s                                    # le variabili non si toccano
    for nome in ("dealfinder-facebook", "dealfinder-facebook-2"):
        assert servizi[nome]["autoDeploy"] is False
    cron = servizi["dealfinder-opportunita"]["buildFilter"]["ignoredPaths"]
    assert "dealfinder/web/static/**" in cron
    # web/app.py NON si ignora: pipeline/esporta.py e bande.py ne importano LIST_SQL
    assert not any("app.py" in x for x in cron)
    # nessun codice Python sotto le cartelle ignorate (altrimenti il filtro salterebbe un deploy che serve)
    for x in cron:
        if x.endswith("/**") and not x.startswith("tests"):
            base = x[:-3].replace("*", "")
            for rad, _, files in os.walk(QUI):
                rel = os.path.relpath(rad, QUI)
                if rel.startswith(base.rstrip("/")) and rel.startswith("dealfinder"):
                    assert not [f for f in files if f.endswith(".py")], rad
    wk = yaml.safe_load(open(os.path.join(QUI, "render-worker.yaml")))["services"][0]
    assert wk["buildFilter"]["ignoredPaths"] and wk["autoDeploy"] is True
