"""Impronta della configurazione (dealfinder/regole_log.py): niente segreti, impronta stabile."""
import json

from dealfinder import regole_log as rl

SEGRETI = {"DATABASE_URL": "postgresql://utente:pw-segreta@host/db", "ANTHROPIC_API_KEY": "sk-ant-segretissima",
           "SECRET_KEY": "chiave-segreta-1", "PLATE_SALT": "sale-segreto", "STRIPE_WEBHOOK_SECRET": "whsec_abc",
           "ADMIN_PASSWORD": "password-segreta", "COMMERCIANTI_KEY": "ck-segreta", "APIFY_TOKEN": "tok-segreto",
           "POLICY_API_TOKEN": "policy-segreto", "SITO_NOTE": "https://u:pw-nascosta@x.it/"}


def _riga(env):
    r = rl.riga("test", env)
    assert r.startswith("CONFIG_IMPRONTA ")
    _, h, js = r.split(" ", 2)
    return h, json.loads(js)


def test_nessun_segreto_nella_riga():
    env = {**SEGRETI, "RIVENDITA_VERA": "1", "SOGLIE_PV": "7000:1500,10000:1800"}
    r = rl.riga("web", env)
    for k, v in SEGRETI.items():
        assert v not in r, k
    for k in ("DATABASE_URL", "ANTHROPIC_API_KEY", "SECRET_KEY", "PLATE_SALT", "ADMIN_PASSWORD", "COMMERCIANTI_KEY",
              "APIFY_TOKEN", "POLICY_API_TOKEN"):
        assert k not in r, k
    _, d = _riga(env)
    assert d["env"]["SITO_NOTE"] == rl.NASCOSTO         # valore che sembra una credenziale sotto un nome innocuo


def test_valori_effettivi_e_default():
    _, d = _riga({"RIVENDITA_VERA": "1", "POLICY_COSTO_FISSO": "200", "POLICY_NUOVA_REGOLA": "3"})
    e = d["env"]
    assert e["RIVENDITA_VERA"] == "1" and e["BENZ_RICHIESTE"] == rl.DEFAULT and e["SOGLIE_PV"] == rl.DEFAULT
    assert e["POLICY_COSTO_FISSO"] == "200" and e["POLICY_NUOVA_REGOLA"] == "3"   # POLICY_* trovate anche se nuove
    assert d["policy"]["costo_fisso"] == 200 and d["policy"]["rivendita_commercianti"] is False
    assert d["processo"] == "test"


def test_impronta_deterministica_e_sensibile():
    env = {"RIVENDITA_VERA": "1", "SOGLIE_PV": "7000:1500", "RENDER_GIT_COMMIT": "abc"}
    h1, _ = _riga(env)
    h2, _ = _riga(dict(reversed(list(env.items()))))
    assert h1 == h2 and len(h1) == 12
    assert rl.impronta({**env, "RENDER_GIT_COMMIT": "zzz", "DATABASE_URL": "x"})[0] == h1   # commit e segreti no
    assert rl.impronta({**env, "SOGLIE_PV": "7000:1600"})[0] != h1
    assert rl.impronta({**env, "POLICY_COSTO_FISSO": "200"})[0] != h1
    assert rl.riga("web", env).split(" ")[1] == rl.riga("cron:ricalcolo", env).split(" ")[1]


def test_registra_non_solleva():
    assert rl.registra("test").startswith("CONFIG_IMPRONTA ")
