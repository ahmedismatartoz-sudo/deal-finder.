"""19/11 — memoria automatica (automatismo #1): il tetto dell'elenco e le soglie dei lavori lunghi si calcolano dal
limite vero del container; interruttore `memoria_auto` spento = i numeri fissi di prima (450 MB, 30.000 righe)."""
import logging
import pathlib
import tempfile

from dealfinder import automatismi, memoria

GIB = 1024 ** 3
P = memoria.PREDEFINITO_MB


def _pulito(monkeypatch):
    """Parte uguale con pytest e con run_tests.py (che passa solo `monkeypatch`): niente override, interruttore acceso."""
    for k in ("MEMORIA_CONTAINER_MB", "ELENCO_MEMORIA_MB", "ELENCO_MEMORIA_QUOTA", "ELENCO_LIMITE",
              "AUTOMATISMO_MEMORIA_AUTO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(automatismi, "valori", lambda: {})


def _cg(v2=None, v1=None, manca=False):
    """File finti del cgroup in una cartella temporanea nuova (manca=True: percorsi che non esistono)."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="cgroup_finto_"))
    if manca:
        d = d / "manca"
    a, b = d / "v2", d / "v1"
    if v2 is not None:
        a.write_text(v2 + "\n")
    if v1 is not None:
        b.write_text(v1 + "\n")
    return str(a), str(b)


def _spegni(monkeypatch):
    monkeypatch.setattr(automatismi, "valori", lambda: {"memoria_auto": False})


# ---------- limite del container ----------
def test_cgroup_v2_numerico(monkeypatch):
    _pulito(monkeypatch)
    v2, v1 = _cg(v2=str(2 * GIB), v1=str(GIB))
    assert memoria.limite_container(v2, v1) == (2 * GIB / 1e6, "cgroup_v2")
    t = memoria.tetto_elenco(v2, v1)
    assert t["fonte"] == "cgroup_v2" and t["quota"] == 0.85 and abs(t["tetto_mb"] - 0.85 * 2 * GIB / 1e6) < 0.1
    assert round(t["tetto_mb"]) == 1825


def test_cgroup_v2_max_passa_a_v1(monkeypatch):
    _pulito(monkeypatch)
    v2, v1 = _cg(v2="max", v1=str(GIB))
    assert memoria.limite_container(v2, v1) == (GIB / 1e6, "cgroup_v1")


def test_cgroup_v1_enorme_e_assenti_valgono_512(monkeypatch):
    _pulito(monkeypatch)
    v2, v1 = _cg(v2="max", v1="9223372036854771712")
    assert memoria.limite_container(v2, v1) == (P, "predefinito")
    v2, v1 = _cg(manca=True)                       # cartella e file assenti
    assert memoria.limite_container(v2, v1) == (P, "predefinito")
    t = memoria.tetto_elenco(v2, v1)
    assert t["fonte"] == "predefinito" and round(t["tetto_mb"]) == 456
    v2, v1 = _cg(v2="boh", v1="-1")              # illeggibili
    assert memoria.limite_container(v2, v1) == (P, "predefinito")


# ---------- override dall'ambiente ----------
def test_override_env(monkeypatch):
    _pulito(monkeypatch)
    v2, v1 = _cg(v2=str(2 * GIB))
    monkeypatch.setenv("ELENCO_MEMORIA_MB", "450")
    t = memoria.tetto_elenco(v2, v1)
    assert t["tetto_mb"] == 450.0 and t["fonte"] == "env:ELENCO_MEMORIA_MB" and t["quota"] is None
    assert abs(t["limite_mb"] - 2 * GIB / 1e6) < 0.1        # il limite vero resta nel log
    monkeypatch.setenv("ELENCO_MEMORIA_MB", "tanti")       # illeggibile: si torna al calcolo
    assert memoria.tetto_elenco(v2, v1)["fonte"] == "cgroup_v2"
    monkeypatch.delenv("ELENCO_MEMORIA_MB")
    monkeypatch.setenv("MEMORIA_CONTAINER_MB", "1000")
    assert memoria.limite_container(v2, v1) == (1000.0, "env")
    assert memoria.tetto_elenco(v2, v1)["tetto_mb"] == 850.0
    monkeypatch.setenv("MEMORIA_CONTAINER_MB", "0")
    assert memoria.limite_container(v2, v1)[1] == "cgroup_v2"


def test_quota_limitata(monkeypatch):
    _pulito(monkeypatch)
    v2, v1 = _cg()
    monkeypatch.setenv("MEMORIA_CONTAINER_MB", "1000")
    for env, attesa in (("0.1", 0.3), ("0.3", 0.3), ("0.5", 0.5), ("0.95", 0.95), ("2", 0.95), ("boh", 0.85)):
        monkeypatch.setenv("ELENCO_MEMORIA_QUOTA", env)
        t = memoria.tetto_elenco(v2, v1)
        assert t["quota"] == attesa and t["tetto_mb"] == round(1000 * attesa, 1), env


# ---------- scala / soglia / righe ----------
def test_scala_mai_sotto_il_valore_base(monkeypatch):
    _pulito(monkeypatch)
    assert memoria.scala(420.0, limite_mb=P) == 420.0
    assert memoria.scala(420.0, limite_mb=100.0) == 420.0
    assert memoria.scala(420.0, limite_mb=4 * P) == 1680.0
    assert memoria.soglia(470.0, limite_mb=4 * P) == 1880.0
    assert memoria.soglia(470.0, limite_mb=50.0) == 470.0


def test_righe_elenco_massimo_60000(monkeypatch):
    _pulito(monkeypatch)
    assert memoria.righe_elenco(P) == 30000
    assert memoria.righe_elenco(100.0) == 30000
    assert memoria.righe_elenco(1.5 * P) == 45000
    assert memoria.righe_elenco(2 * P) == 60000
    assert memoria.righe_elenco(4 * P) == 60000 == memoria.ELENCO_RIGHE_MAX
    assert memoria.righe_elenco(64 * P) == 60000


# ---------- interruttore ----------
def test_interruttore_spento_450_e_30000(monkeypatch):
    _pulito(monkeypatch)
    v2, v1 = _cg(v2=str(2 * GIB))
    assert memoria.automatica() is True
    _spegni(monkeypatch)
    assert memoria.automatica() is False
    t = memoria.tetto_elenco(v2, v1)
    assert t["tetto_mb"] == 450.0 and t["fonte"] == "spento" and t["quota"] is None
    assert memoria.righe_elenco(4 * P) == 30000
    assert memoria.soglia(420.0, limite_mb=4 * P) == 420.0
    assert memoria.scala(420.0, limite_mb=4 * P) == 1680.0          # scala resta pura (la usano gli altri automatismi)
    monkeypatch.setenv("ELENCO_MEMORIA_MB", "700")                  # l'override esplicito vince anche da spento
    assert memoria.tetto_elenco(v2, v1)["tetto_mb"] == 700.0


def test_interruttore_da_env_e_mai_un_errore(monkeypatch):
    _pulito(monkeypatch)
    monkeypatch.setenv("AUTOMATISMO_MEMORIA_AUTO", "0")
    assert memoria.automatica() is False
    monkeypatch.delenv("AUTOMATISMO_MEMORIA_AUTO")
    monkeypatch.setattr(automatismi, "attivo", lambda *a, **k: 1 / 0)
    assert memoria.automatica() is True                             # non si legge = acceso


# ---------- agganci nel sito e nei lavori ----------
class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.righe = []

    def emit(self, r):
        self.righe.append(r.getMessage())


def test_app_una_sola_rss_e_attesa_scalata(monkeypatch):
    _pulito(monkeypatch)
    from dealfinder.web import app
    src = open(app.__file__, encoding="utf-8").read()
    assert src.count("def _rss_mb(") == 1
    monkeypatch.setattr(memoria, "rss_mb", lambda: 123.0)
    assert app._rss_mb() == 123.0
    dormite = []
    monkeypatch.setattr(app.time, "sleep", lambda s: dormite.append(s))
    L = logging.getLogger("prova_memoria_auto")
    monkeypatch.setenv("MEMORIA_CONTAINER_MB", str(4 * P))          # 2 GB: soglia 1.680
    monkeypatch.setattr(app, "_rss_mb", lambda: 1000.0)
    app._aspetta_memoria(L, "PROVA")
    assert dormite == []                                            # 1.000 < 1.680: non aspetta
    _spegni(monkeypatch)
    app._aspetta_memoria(L, "PROVA")
    assert len(dormite) == 10                                       # spento: 420 come prima, aspetta
    dormite.clear()
    app._aspetta_memoria(L, "PROVA", 1200.0)                        # limite esplicito: vale quello
    assert dormite == []


def test_elenco_righe_e_log_solo_al_cambio(monkeypatch):
    _pulito(monkeypatch)
    from dealfinder.web import app

    class Vuota:
        def execute(self, sql, p=None):
            return iter(())

    h = _Log()
    lg = logging.getLogger("scovo")
    lg.addHandler(h)
    monkeypatch.setattr(app, "_rss_mb", lambda: 200.0)
    monkeypatch.setattr(app, "_ELENCO_TETTO_LOG", {})
    try:
        from collections import Counter
        monkeypatch.setenv("MEMORIA_CONTAINER_MB", str(4 * P))
        st = Counter()
        app._leggi_elenco(Vuota(), st)
        app._leggi_elenco(Vuota(), Counter())
        righe = [r for r in h.righe if r.startswith("ELENCO tetto memoria")]
        assert len(righe) == 1 and "righe al massimo 60000" in righe[0] and "fonte env" in righe[0]
        assert st["lista_tetto_mb"] == round(4 * P * 0.85) and abs(st["lista_limite_container_mb"] - 4 * P) <= 1
        _spegni(monkeypatch)
        st = Counter()
        app._leggi_elenco(Vuota(), st)
        righe = [r for r in h.righe if r.startswith("ELENCO tetto memoria")]
        assert len(righe) == 2 and "450 MB" in righe[1] and "fonte spento" in righe[1]
        assert "righe al massimo 30000" in righe[1] and st["lista_tetto_mb"] == 450
        monkeypatch.setenv("ELENCO_LIMITE", "1000")                 # override: vince
        app._leggi_elenco(Vuota(), Counter())
        assert "righe al massimo 1000" in [r for r in h.righe if r.startswith("ELENCO tetto memoria")][-1]
    finally:
        lg.removeHandler(h)


def test_soglie_dei_lavori_scalate(monkeypatch):
    _pulito(monkeypatch)
    from dealfinder.pipeline import censimento as CE
    from dealfinder.pricing import perche_fuori as PF
    import inspect
    assert CE.MEMORIA_MAX_MB == 470 and PF.MEMORIA_MAX_MB == 470
    for mod in (CE, PF):
        assert inspect.signature(mod.esegui).parameters["memoria_mb"].default is None
        assert "soglia(MEMORIA_MAX_MB)" in inspect.getsource(mod.esegui)
    monkeypatch.setenv("MEMORIA_CONTAINER_MB", str(4 * P))
    assert memoria.soglia(CE.MEMORIA_MAX_MB) == 1880.0
    _spegni(monkeypatch)
    assert memoria.soglia(PF.MEMORIA_MAX_MB) == 470.0
