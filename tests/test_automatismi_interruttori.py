"""19/11, passo 0 degli automatismi: interruttori senza deploy (dealfinder/automatismi.py) e memoria.alta().

Ordine: variabile d'ambiente AUTOMATISMO_<NOME> (solo test/locale) → chiave 'automatismi' di sito_impostazioni →
default del codice. Mai un errore. La chiave non si scrive dal pannello.
"""
from __future__ import annotations

import os
import tempfile

from dealfinder import automatismi, memoria
from dealfinder.web import impostazioni


class _db:
    """Finto database delle impostazioni: {"automatismi": ...} per la durata del blocco."""

    def __init__(self, valore=None, errore: bool = False):
        self.valore, self.errore = valore, errore

    def _leggi(self):
        if self.errore:
            raise RuntimeError("database giù")
        return {} if self.valore is None else {"automatismi": self.valore}

    def __enter__(self):
        impostazioni.usa_lettore(self._leggi)
        return self

    def __exit__(self, *a):
        impostazioni.usa_lettore(None)


def _senza_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(automatismi.PREFISSO_ENV):
            monkeypatch.delenv(k, raising=False)
    for k in ("MEMORIA_CONTAINER_MB", "ELENCO_MEMORIA_MB", "ELENCO_MEMORIA_QUOTA"):
        monkeypatch.delenv(k, raising=False)


def test_default_del_codice(monkeypatch):
    _senza_env(monkeypatch)
    with _db(None):
        for nome in automatismi.NOMI:
            assert automatismi.attivo(nome) is True
        assert automatismi.attivo("salute", default=False) is False
        assert automatismi.numero("confronti_notte_gruppi", 400) == 400.0
        assert isinstance(automatismi.numero("x", 3), float)


def test_nomi_previsti():
    assert set(automatismi.NOMI) == {"memoria_auto", "confronti_notte", "controllo_tutte", "salute", "salute_azioni",
                                     "chat_subito", "stima_ripiego", "stima_ripiego_largo"}


def test_database_spegne_e_regola(monkeypatch):
    _senza_env(monkeypatch)
    with _db({"confronti_notte": False, "salute": "no", "controllo_tutte": 0, "memoria_auto": "acceso",
              "confronti_notte_gruppi": 120, "confronti_notte_minuti": "90,5"}):
        assert automatismi.attivo("confronti_notte") is False
        assert automatismi.attivo("salute") is False
        assert automatismi.attivo("controllo_tutte") is False
        assert automatismi.attivo("memoria_auto", default=False) is True
        assert automatismi.attivo("salute_azioni") is True            # non scritto: default
        assert automatismi.numero("confronti_notte_gruppi", 400) == 120.0
        assert automatismi.numero("confronti_notte_minuti", 150) == 90.5
        assert automatismi.numero("confronti_notte_pagine", 1500) == 1500.0


def test_env_vince_sul_database(monkeypatch):
    _senza_env(monkeypatch)
    with _db({"confronti_notte": False, "salute": True, "confronti_notte_gruppi": 120}):
        monkeypatch.setenv("AUTOMATISMO_CONFRONTI_NOTTE", "1")
        monkeypatch.setenv("AUTOMATISMO_SALUTE", "0")
        monkeypatch.setenv("AUTOMATISMO_CONFRONTI_NOTTE_GRUPPI", "7")
        assert automatismi.attivo("confronti_notte") is True
        assert automatismi.attivo("salute") is False
        assert automatismi.numero("confronti_notte_gruppi", 400) == 7.0
        # env vuota o illeggibile: non conta, si passa al database
        monkeypatch.setenv("AUTOMATISMO_CONFRONTI_NOTTE", "boh")
        monkeypatch.setenv("AUTOMATISMO_CONFRONTI_NOTTE_GRUPPI", "")
        assert automatismi.attivo("confronti_notte") is False
        assert automatismi.numero("confronti_notte_gruppi", 400) == 120.0


def test_valori_illeggibili_danno_il_default(monkeypatch):
    _senza_env(monkeypatch)
    with _db({"salute": "forse", "controllo_tutte": None, "memoria_auto": [1], "n": "tanti", "m": True,
              "inf": float("inf")}):
        assert automatismi.attivo("salute") is True
        assert automatismi.attivo("salute", default=False) is False
        assert automatismi.attivo("controllo_tutte") is True
        assert automatismi.attivo("memoria_auto") is True
        assert automatismi.numero("n", 5) == 5.0
        assert automatismi.numero("m", 5) == 5.0                      # un sì/no non è un numero
        assert automatismi.numero("inf", 5) == 5.0


def test_mai_eccezioni(monkeypatch):
    _senza_env(monkeypatch)
    for valore in ("non un oggetto", [1, 2], 3, True):
        with _db(valore):
            assert automatismi.valori() == {}
            assert automatismi.attivo("salute") is True
            assert automatismi.numero("x", 2) == 2.0
    with _db(errore=True):                                            # database giù: resta il default
        assert automatismi.attivo("confronti_notte") is True
        assert automatismi.numero("x", 2) == 2.0

    def rotto():
        raise RuntimeError("impostazioni rotte")
    monkeypatch.setattr(impostazioni, "dati", rotto)
    assert automatismi.valori() == {}
    assert automatismi.attivo("salute") is True
    assert automatismi.attivo("salute", default=False) is False
    assert automatismi.numero("x", 9) == 9.0


def test_cambio_senza_deploy(monkeypatch):
    """Il rollback da SQL vale appena la copia in memoria delle impostazioni si rinfresca (45 secondi)."""
    _senza_env(monkeypatch)
    assert impostazioni.CACHE_S <= 60
    db = _db({"confronti_notte": True})
    with db:
        assert automatismi.attivo("confronti_notte") is True
        db.valore = {"confronti_notte": False}
        assert automatismi.attivo("confronti_notte") is True          # ancora la copia in memoria
        impostazioni.invalida()                                       # = passati i 45 secondi
        assert automatismi.attivo("confronti_notte") is False


def test_non_scrivibile_dal_pannello():
    assert automatismi.CHIAVE == "automatismi"
    assert automatismi.CHIAVE not in impostazioni.CHIAVI


def test_nessuno_stato_residuo():
    assert impostazioni._lettore["f"] is None


# ------------------------------------------------------------------------------------------------ memoria.alta
def test_memoria_alta_soglia():
    lim = memoria.PREDEFINITO_MB                                      # 536,9 MB
    assert memoria.alta(0.6, limite_mb=lim, rss=0.6 * lim + 1) is True
    assert memoria.alta(0.6, limite_mb=lim, rss=0.6 * lim - 1) is False
    assert memoria.alta(0.6, limite_mb=lim, rss=0.6 * lim) is False    # serve "oltre", non "uguale"
    assert memoria.alta(0.6, limite_mb=4 * lim, rss=400.0) is False    # con 2 GB i 400 MB non sono più "alta"
    assert memoria.alta(0.6, limite_mb=lim, rss=400.0) is True
    assert memoria.alta(0.9, limite_mb=lim, rss=400.0) is False
    assert memoria.alta(0.6, limite_mb=lim, rss=0.0) is False          # RSS non leggibile: non alta


def test_memoria_alta_usa_rss_e_limite_veri(monkeypatch):
    monkeypatch.setenv("MEMORIA_CONTAINER_MB", "1000")
    monkeypatch.setattr(memoria, "rss_mb", lambda: 601.0)
    assert memoria.alta() is True                                     # quota di default 0,6
    monkeypatch.setattr(memoria, "rss_mb", lambda: 599.0)
    assert memoria.alta() is False
    assert memoria.alta(0.5) is True
    monkeypatch.setattr(memoria, "rss_mb", lambda: 1 / 0)
    assert memoria.alta() is False                                    # mai un errore


def test_memoria_api_chiusa(monkeypatch):
    for k in ("MEMORIA_CONTAINER_MB", "ELENCO_MEMORIA_MB", "ELENCO_MEMORIA_QUOTA"):
        monkeypatch.delenv(k, raising=False)
    with tempfile.TemporaryDirectory() as d:
        v2, v1, manca = os.path.join(d, "v2"), os.path.join(d, "v1"), os.path.join(d, "manca")
        with open(v2, "w") as f:
            f.write(str(2 * 1024 ** 3) + "\n")
        with open(v1, "w") as f:
            f.write("9223372036854771712\n")
        assert memoria.limite_container(v2, v1) == (2 * 1024 ** 3 / 1e6, "cgroup_v2")
        assert memoria.limite_container(manca, v1) == (memoria.PREDEFINITO_MB, "predefinito")   # v1 senza limite
        assert memoria.limite_container(manca, manca) == (memoria.PREDEFINITO_MB, "predefinito")
        t = memoria.tetto_elenco(v2, v1)
        assert set(t) == {"tetto_mb", "limite_mb", "fonte", "quota"}
        assert t["fonte"] == "cgroup_v2" and t["quota"] == 0.85
        assert abs(t["tetto_mb"] - 0.85 * 2 * 1024 ** 3 / 1e6) < 0.1
    assert memoria.scala(420.0, limite_mb=memoria.PREDEFINITO_MB) == 420.0
    assert memoria.scala(420.0, limite_mb=4 * memoria.PREDEFINITO_MB) == 1680.0
    assert memoria.scala(420.0, limite_mb=100.0) == 420.0             # mai sotto il valore di partenza
    assert memoria.rss_mb() >= 0.0
