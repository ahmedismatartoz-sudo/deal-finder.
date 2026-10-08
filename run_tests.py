"""Esegue i test anche senza pytest: python run_tests.py"""
import importlib, sys, traceback
mod = importlib.import_module("tests.test_core")
fails = 0
for name in dir(mod):
    if name.startswith("test_"):
        try:
            getattr(mod, name)(); print("OK  ", name)
        except Exception:
            fails += 1; print("FAIL", name); traceback.print_exc()
sys.exit(1 if fails else 0)
