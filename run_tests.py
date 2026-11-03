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
    ("dealfinder.pricing.policy_v2", ("TARATURA", "TARATURA_MIN", "TARATURA_MAX", "FASCE_BASE")),
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
                fn()
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
