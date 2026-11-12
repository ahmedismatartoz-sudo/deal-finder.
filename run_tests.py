"""Esegue i test anche senza pytest.

    python run_tests.py               tutti i tests/test_*.py (trovati da soli), con i default del codice
    python run_tests.py --produzione  con le regole attive in produzione (RIVENDITA_VERA=1, BENZ_RICHIESTE=1);
                                      i test elencati in tests/rosso_noto.txt sono "rossi noti" e non fanno fallire
    python run_tests.py --deploy      controllo prima della messa online: compileall di dealfinder, import dei
                                      moduli principali, poi i test nei due modi. Codice di uscita != 0 se qualcosa
                                      fallisce
    python run_tests.py -k testo      solo i test il cui nome (modulo o funzione) contiene "testo"

Isolamento (3/11, fase 1): prima di ogni test si fotografano os.environ e le variabili di modulo che contano per le
regole (scovo.SOGLIE, policy_v2.TARATURA, ...). Dopo il test si ripristinano; se il test le ha lasciate cambiate
risulta FALLITO ("lascia stato"). Le impostazioni scritte all'import di un modulo di test valgono solo per i test di
quel modulo e si tolgono alla fine del modulo.
"""
from __future__ import annotations

import copy
import glob
import importlib
import os
import subprocess
import sys
import traceback

QUI = os.path.dirname(os.path.abspath(__file__))
PRODUZIONE = {"RIVENDITA_VERA": "1", "BENZ_RICHIESTE": "1"}
ROSSO_NOTO = os.path.join(QUI, "tests", "rosso_noto.txt")

# Variabili di modulo condivise: un test che le cambia le cambia per tutti quelli dopo.
STATO_MODULI = (
    ("dealfinder.web.scovo", ("SOGLIE", "SOGLIE_FASCE", "SOGLIE_PV", "MARGINE_MINIMO", "ANNO_MINIMO", "KM_MASSIMO")),
    ("dealfinder.pipeline.benzina_mi", ("PROVINCE_100",)),
    ("dealfinder.web.app", ("_elenco",)),
)


def _foto_moduli() -> dict:
    out = {}
    for mod, attrs in STATO_MODULI:
        m = sys.modules.get(mod)
        if m is None:
            continue
        for a in attrs:
            if hasattr(m, a):
                v = getattr(m, a)
                try:
                    c = copy.deepcopy(v)
                except Exception:
                    c = v
                out[(mod, a)] = (v, c)
    return out


def _ripristina_moduli(prima: dict) -> list[str]:
    """Ripristina le variabili di modulo; restituisce i nomi di quelle che il test aveva cambiato."""
    cambiate = []
    for (mod, a), (rif, c) in prima.items():
        m = sys.modules[mod]
        ora = getattr(m, a, None)
        if ora is rif and ora == c:
            continue
        cambiate.append(f"{mod.rsplit('.', 1)[-1]}.{a}")
        if isinstance(rif, dict):                       # stesso oggetto (altri moduli possono averlo importato)
            rif.clear()
            rif.update(c)
        elif isinstance(rif, list):
            rif[:] = c
        setattr(m, a, rif)
    return cambiate


def _ripristina_env(prima: dict) -> list[str]:
    """Rimette os.environ com'era; restituisce i NOMI cambiati (mai i valori: possono essere segreti)."""
    diversi = sorted({k for k in set(prima) | set(os.environ) if prima.get(k) != os.environ.get(k)})
    if diversi:
        os.environ.clear()
        os.environ.update(prima)
    return diversi


class MonkeyPatch:
    """12/11: sostituto minimo della fixture `monkeypatch` di pytest (setattr, delattr, setenv, delenv, setitem,
    delitem, syspath_prepend, chdir), annullato alla fine del test. I test che la chiedono come argomento girano
    davvero anche senza pytest (prima: TypeError e 3 "falliti noti")."""
    _NIENTE = object()

    def __init__(self):
        self._annulla = []

    def setattr(self, target, name, value=_NIENTE, raising=True):
        if value is MonkeyPatch._NIENTE:              # forma "modulo.attributo", valore
            if not isinstance(target, str) or "." not in target:
                raise TypeError("setattr(target, name, value) oppure setattr('modulo.attr', value)")
            value, (modulo, name) = name, target.rsplit(".", 1)
            target = importlib.import_module(modulo)
        vecchio = getattr(target, name, MonkeyPatch._NIENTE)
        if vecchio is MonkeyPatch._NIENTE and raising:
            raise AttributeError(f"{target!r} non ha l'attributo {name!r}")
        self._annulla.append(lambda: delattr(target, name) if vecchio is MonkeyPatch._NIENTE
                             else setattr(target, name, vecchio))
        setattr(target, name, value)

    def delattr(self, target, name, raising=True):
        if not hasattr(target, name):
            if raising:
                raise AttributeError(name)
            return
        vecchio = getattr(target, name)
        self._annulla.append(lambda: setattr(target, name, vecchio))
        delattr(target, name)

    def setitem(self, d, k, v):
        vecchio = d.get(k, MonkeyPatch._NIENTE)
        self._annulla.append(lambda: d.pop(k, None) if vecchio is MonkeyPatch._NIENTE else d.__setitem__(k, vecchio))
        d[k] = v

    def delitem(self, d, k, raising=True):
        if k not in d:
            if raising:
                raise KeyError(k)
            return
        vecchio = d[k]
        self._annulla.append(lambda: d.__setitem__(k, vecchio))
        del d[k]

    def setenv(self, name, value, prepend=None):
        value = str(value)
        if prepend and os.environ.get(name):
            value = value + prepend + os.environ[name]
        self.setitem(os.environ, name, value)

    def delenv(self, name, raising=True):
        self.delitem(os.environ, name, raising)

    def syspath_prepend(self, path):
        sys.path.insert(0, str(path))
        self._annulla.append(lambda: sys.path.remove(str(path)) if str(path) in sys.path else None)

    def chdir(self, path):
        vecchia = os.getcwd()
        self._annulla.append(lambda: os.chdir(vecchia))
        os.chdir(path)

    def undo(self):
        while self._annulla:
            self._annulla.pop()()


def _chiama(fn):
    """Chiama il test passando `monkeypatch` se lo chiede (come pytest); poi annulla le sue modifiche."""
    import inspect
    try:
        parametri = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        parametri = {}
    if "monkeypatch" not in parametri:
        return fn()
    mp = MonkeyPatch()
    try:
        return fn(monkeypatch=mp)
    finally:
        mp.undo()


def _rossi_noti() -> dict:
    out = {}
    try:
        with open(ROSSO_NOTO, encoding="utf-8") as f:
            for riga in f:
                testo, _, motivo = riga.partition("#")
                parti = testo.split()
                if len(parti) == 2:
                    out[tuple(parti)] = motivo.strip()
    except FileNotFoundError:
        pass
    return out


def moduli_di_test() -> list[str]:
    return ["tests." + os.path.basename(p)[:-3] for p in sorted(glob.glob(os.path.join(QUI, "tests", "test_*.py")))]


def esegui(produzione: bool = False, filtro: str | None = None) -> int:
    if produzione:
        os.environ.update(PRODUZIONE)
    noti = _rossi_noti() if produzione else {}
    falliti, rossi, ok = [], [], 0
    for modname in moduli_di_test():
        env_inizio = dict(os.environ)
        try:
            mod = importlib.import_module(modname)
        except Exception:
            falliti.append((modname, "<import>"))
            print("FAIL", modname, "<import>")
            traceback.print_exc()
            _ripristina_env(env_inizio)
            continue
        env_modulo = dict(os.environ)       # impostazioni messe all'import: valgono per i test di questo modulo
        for name in sorted(dir(mod)):
            fn = getattr(mod, name)
            if not (name.startswith("test_") and callable(fn)):
                continue
            if filtro and filtro not in modname + " " + name:
                continue
            moduli_prima = _foto_moduli()
            errore = None
            try:
                _chiama(fn)
            except Exception:
                errore = traceback.format_exc()
            env_cambiati = _ripristina_env(env_modulo)
            mod_cambiati = _ripristina_moduli(moduli_prima)
            if errore is None and (env_cambiati or mod_cambiati):
                errore = "lascia stato dopo il test: " + ", ".join(env_cambiati + mod_cambiati) + "\n"
            chiave = (modname, name)
            if errore is None:
                ok += 1
                nota = " (era nei rossi noti: toglierlo da tests/rosso_noto.txt)" if chiave in noti else ""
                print("OK  ", modname, name + nota)
            elif chiave in noti:
                rossi.append(chiave)
                print("ROSSO-NOTO", modname, name, "-", noti[chiave])
            else:
                falliti.append(chiave)
                print("FAIL", modname, name)
                print(errore, end="")
        _ripristina_env(env_inizio)
    modo = "regole di produzione" if produzione else "default del codice"
    print(f"[{modo}] ok: {ok}  falliti: {len(falliti)}  rossi noti: {len(rossi)}")
    for m, n in falliti:
        print("  FALLITO", m, n)
    return 1 if falliti else 0


def deploy() -> int:
    """Controllo prima della messa online: si ferma al primo passo che fallisce."""
    py = sys.executable
    print("== 1/4 compileall dealfinder", flush=True)
    if subprocess.call([py, "-m", "compileall", "-q", os.path.join(QUI, "dealfinder")], cwd=QUI):
        print("DEPLOY NO: errori di sintassi")
        return 1
    print("== 2/4 import dei moduli principali (senza database)", flush=True)
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    principali = ["dealfinder.web.app", "dealfinder.pipeline.collect", "dealfinder.pipeline.ciclo",
                  "dealfinder.pipeline.tempo_reale", "dealfinder.pricing.ricalcolo", "dealfinder.pricing.policy_v2",
                  "dealfinder.web.scovo", "dealfinder.init_db", "dealfinder.regole_log"]
    codice = "import importlib, sys\nfor m in sys.argv[1:]:\n    importlib.import_module(m)\n    print('import ok', m)"
    if subprocess.call([py, "-c", codice, *principali], cwd=QUI, env=env):
        print("DEPLOY NO: un modulo non si importa")
        return 1
    print("== 3/4 test con i default del codice", flush=True)
    if subprocess.call([py, os.path.join(QUI, "run_tests.py")], cwd=QUI):
        print("DEPLOY NO: test falliti")
        return 1
    print("== 4/4 test con le regole di produzione (rossi noti ammessi)", flush=True)
    if subprocess.call([py, os.path.join(QUI, "run_tests.py"), "--produzione"], cwd=QUI):
        print("DEPLOY NO: test falliti con le regole di produzione")
        return 1
    print("DEPLOY OK")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, QUI)
    args = sys.argv[1:]
    if "--deploy" in args:
        sys.exit(deploy())
    filtro = args[args.index("-k") + 1] if "-k" in args and args.index("-k") + 1 < len(args) else None
    sys.exit(esegui(produzione="--produzione" in args, filtro=filtro))
