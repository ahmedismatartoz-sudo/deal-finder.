"""Esegue i test anche senza pytest: python run_tests.py"""
import importlib
import sys
import traceback

fails = 0
for modname in ("tests.test_core", "tests.test_ai_web", "tests.test_servizi", "tests.test_sito", "tests.test_autoscout_massiva"):
    mod = importlib.import_module(modname)
    for name in sorted(dir(mod)):
        if name.startswith("test_") and callable(getattr(mod, name)):
            try:
                getattr(mod, name)()
                print("OK  ", modname, name)
            except Exception:
                fails += 1
                print("FAIL", modname, name)
                traceback.print_exc()
print("falliti:", fails)
sys.exit(1 if fails else 0)
