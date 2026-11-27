"""19/11, automatismo 3: controllo AI di TUTTE le auto tolte dal sito per "sconto_alto_da_controllare".

- ordine della coda: prima la zona di Milano, poi margine/soglia, poi id; le "dopo tetto" dopo le "entra";
- per giro: 40 + la coda, al massimo 120 (limite tecnico); interruttore controllo_tutte spento = 40 come prima;
- una decisione Claude allo stesso prezzo con le foto viste non si richiede (gia_controllata);
- convergenza: mai_controllate va a zero, nessun giro a vuoto ogni 2 minuti (pausa per credito, chiamate fallite);
- nessun tetto di spesa; nessun telefono o email nel testo mandato all'AI né nei log;
- stato_coda() e riga CODA_CONTROLLO: solo conteggi.
Le regole del sito non cambiano (sito_regole.py non è toccato).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import time

from dealfinder.ai import client as AI
from dealfinder.ai import controllo_auto as CA
from dealfinder.web import impostazioni
from dealfinder.web import sito_regole as SR
from tests.test_controllo_coda_sito import Conn, G, OK, auto, env, riga, scheda

NO_ENV = dict(CONTROLLO_TUTTE=None, AUTOMATISMO_CONTROLLO_TUTTE=None, CONTROLLO_SITO_MAX_GIRO=None,
              CONTROLLO_PER_GIRO=None)


def _pulisci():
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    CA._SALTATE.clear()
    CA._MOTIVO_SALTO.clear()
    CA.CODA_SITO.clear()
    CA.CODA_SITO.update(t=0.0, entra=[], fuori=set(), sul_sito=set(), motivi={}, inutili=set(), gia_controllate=set())
    CA.MISURA.update(giorno=None)
    impostazioni.usa_lettore(None)


class _automatismi:
    """Chiave 'automatismi' di sito_impostazioni finta (interruttore senza deploy)."""
    def __init__(self, valore):
        self.valore = valore

    def __enter__(self):
        impostazioni.usa_lettore(lambda: {"automatismi": self.valore})

    def __exit__(self, *a):
        impostazioni.usa_lettore(None)


class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.righe = []

    def emit(self, record):
        self.righe.append(record.getMessage())

    def __enter__(self):
        CA.log.addHandler(self)
        return self

    def __exit__(self, *a):
        CA.log.removeHandler(self)


class ConnViva(Conn):
    """Come il database vero: un'auto con la decisione salvata non torna tra quelle da chiamare (CAND_SQL_SITO)."""
    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO decisione_ai"):
            self.sito = [r for r in self.sito if r["id"] != params[0]]
        return super().execute(sql, params)


class _foto_url:
    def __enter__(self):
        self.prima = AI.image_url_block
        AI.image_url_block = lambda src: {"type": "image", "source": {"type": "url", "url": src}}

    def __exit__(self, *a):
        AI.image_url_block = self.prima


def _coda(items, g=G):
    out, fun = SR.elenco_sito([dict(it, _r=dict(it["_r"]), pv=dict(it["pv"])) for it in items], g, scrivi_log=False)
    return CA.coda_dal_sito(items, out, fun, g), out


def _sito(n, **kw):
    return [riga(i, price_eur=8000, m=13000, margine=3500, **kw) for i in range(1, n + 1)]


# ------------------------------------------------------------------ ordine
def test_ordine_milano_poi_margine_poi_id():
    _pulisci()
    with env(**NO_ENV):
        # 28/11: raggio 200 km, decisione del cliente: Torino ora tra le province del sito; fuori zona qui Padova
        items = [auto(1, prov="PD", margine=6000),      # fuori dai 200 km: dopo, anche col margine più alto
                 auto(2, prov="MI", margine=3500),
                 auto(3, prov="BG", margine=5000),       # zona di Milano, margine più alto: per prima
                 auto(5, prov="MI", margine=3500),       # pari alla 2: decide l'id
                 auto(4, prov="PD", margine=3500)]
        res, out = _coda(items)
        tolte = [i for i in CA.CODA_SITO["entra"]]
        assert tolte == [i for i in (3, 2, 5, 1, 4) if i in tolte] and {2, 3, 5} <= set(tolte)
        assert CA.priorita_sito(items[2])[0] == 0 and CA.priorita_sito(items[0])[0] == 1
        assert CA.priorita_sito(items[1])[1] == CA.priorita_sito(items[3])[1]
        assert CA.priorita_sito(items[1]) < CA.priorita_sito(items[3])
    _pulisci()


def test_dopo_tetto_dopo_le_entra_e_spento_come_prima():
    _pulisci()
    g = dataclasses.replace(G, commercianti_totale=1)
    sul_sito = auto(50, M=10000, margine=3000, seller="commerciante", _tipo="comm_fascia", commerciante=True)
    forte = auto(2, margine=5000, seller="commerciante", _tipo="comm_fascia", commerciante=True)   # scalzerebbe la 50
    debole = auto(3, margine=2500, seller="commerciante", _tipo="comm_fascia", commerciante=True)  # sotto il tetto
    privata = auto(9, margine=3500)
    fun = SR.Funnel()
    fun.tolti = {"truffa": [2, 3, 9]}
    with env(**NO_ENV):
        res = CA.coda_dal_sito([forte, debole, privata], [sul_sito], fun, g)
        assert CA.CODA_SITO["entra"] == [9, 2]                    # la "dopo tetto" in fondo, anche col margine più alto
        assert res["entra_dopo_tetto_commercianti"] == 1 and CA.CODA_SITO["inutili"] == {3}
        st = CA.stato_coda()
        assert st["totale_sconto_alto"] == 3 and st["mai_controllate"] == 2 and st["inutili:tetto_commercianti"] == 1
        with env(AUTOMATISMO_CONTROLLO_TUTTE="0"):                 # interruttore spento: come prima del 19/11
            CA.coda_dal_sito([forte, debole, privata], [sul_sito], fun, g)
            assert CA.CODA_SITO["entra"] == [9]
            st = CA.stato_coda()
            assert st["mai_controllate"] == 1 and st["inutili:tetto_commercianti"] == 2 and st["attivo"] is False
    _pulisci()


# ------------------------------------------------------------------ quante per giro
def test_per_giro_sito_base_tetto_e_interruttore():
    _pulisci()
    with env(**NO_ENV):
        assert CA.per_giro_sito(0) == 40 and CA.per_giro_sito(-3) == 40
        assert CA.per_giro_sito(10) == 50 and CA.per_giro_sito(80) == 120
        assert CA.per_giro_sito(299) == 120 == CA.SITO_MAX_GIRO   # limite tecnico di un giro (non 300)
        with env(CONTROLLO_SITO_MAX_GIRO="200"):
            assert CA.per_giro_sito(299) == 200
        with env(CONTROLLO_SITO_MAX_GIRO="10"):
            assert CA.per_giro_sito(299) == 40                    # mai sotto il giro normale
        with env(AUTOMATISMO_CONTROLLO_TUTTE="0"):
            assert not CA.controlla_tutte() and CA.per_giro_sito(299) == 40
        with env(CONTROLLO_TUTTE="0"):
            assert not CA.controlla_tutte() and CA.per_giro_sito(299) == 40
        with _automatismi({"controllo_tutte": False}):            # dal database, senza deploy
            assert not CA.controlla_tutte() and CA.per_giro_sito(299) == 40
        with _automatismi({"controllo_tutte": True}):
            assert CA.controlla_tutte() and CA.per_giro_sito(299) == 120
        with _automatismi("rotto"):
            assert CA.controlla_tutte()                           # illeggibile = default acceso
    _pulisci()


def test_nessun_tetto_di_spesa():
    _pulisci()
    assert CA.budget_usd() == float("inf")
    CA.CODA_SITO.update(t=time.time(), entra=[1, 2, 3])
    fatte = []

    def chiama(testo, foto):
        fatte.append(1)
        return dict(OK), 800, 80, "claude-haiku-5-5"
    with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="url", CONTROLLO_BUDGET_USD="0.01",
             CONTROLLO_MAX_GIORNO="1", AI_BUDGET_GIORNO="0.01", **NO_ENV), _foto_url():
        res = CA.giro(ConnViva([], [scheda(i, price_eur=8000) for i in (1, 2, 3)], _sito(3), spesa_usd=500.0),
                      chiama=chiama)
    assert len(fatte) == 3 and res["sito_fatte"] == 3 and "saltato" not in res and res["budget_usd"] is None
    assert res["usd_oggi"] >= 500 and CA.stato_coda()["usd_oggi"] >= 500      # la spesa si vede, non si limita
    _pulisci()


# ------------------------------------------------------------------ già controllate
def test_gia_controllata_nessuna_chiamata():
    _pulisci()
    j = {"fonte": "claude", "danni": .05, "foto_internet": .05, "truffa": .1, "auto_vera": .5, "dati_coerenti": 1,
         "decisione": "tratta", "stesso_prezzo": True, "km_ok": 1, "guasto": 0}
    with env(**NO_ENV):
        res, out = _coda([auto(7, jev=j)])
        assert out == [] and CA.CODA_SITO["entra"] == [7] and CA.CODA_SITO["gia_controllate"] == {7}
        assert CA.sito_in_attesa() == 0                           # non sveglia il giro
        st = CA.stato_coda()
        assert st["totale_sconto_alto"] == 1 and st["gia_controllate_non_confermate"] == 1
        assert st["mai_controllate"] == 0 and st["in_attesa"] == 0 and st["eta_coda_s"] == 0

        def chiama(testo, foto):
            raise AssertionError("già controllata: nessuna chiamata")
        # il database non la ridà tra quelle da chiamare (CAND_SQL_SITO: decisione Claude allo stesso prezzo)
        res = CA.giro(Conn([], [scheda(7)], sito=[]), chiama=chiama)
        assert res.get("candidate") == 0 and not res.get("sito_restanti")
        assert CA.attesa_prossimo_giro(res, 3600.0) == 3600.0
        assert "stesso prezzo" in CA.gia_controllata.__doc__ and CA.gia_controllata(auto(7, jev=j))
        assert not CA.gia_controllata(auto(7, jev=dict(j, danni=None)))       # senza foto viste non vale
    _pulisci()


# ------------------------------------------------------------------ convergenza
def test_convergenza_la_coda_si_svuota_e_poi_ci_si_ferma():
    _pulisci()
    n = 300
    senza_foto, rotta = {5, 6}, 7
    schede = [scheda(i, price_eur=8000, n_foto=0 if i in senza_foto else 2,
                     **({"foto": []} if i in senza_foto else {})) for i in range(1, n + 1)]
    chiamate: list[str] = []

    def chiama(testo, foto):
        chiamate.append(testo)
        if len(chiamate) == 3 and rotta is not None:
            return {"decisione": "boh"}, 800, 80, "claude-haiku-5-5"          # risposta non valida: pagata, fallita
        return dict(OK), 800, 80, "claude-haiku-5-5"
    conn = ConnViva([], schede, _sito(n))
    CA.CODA_SITO.update(t=time.time(), entra=list(range(1, n + 1)), sconto_alto=set(range(1, n + 1)),
                        sconto_alto_gia=set(), sconto_alto_fuori={})
    assert CA.stato_coda()["mai_controllate"] == n and CA.sito_in_attesa() == n
    giri, attese, ultimo = 0, [], None
    with env(CONTROLLO_PARALLELI="3", CONTROLLO_FOTO_MODO="url", **NO_ENV), _foto_url(), _Log() as lg:
        while giri < 10:
            giri += 1
            ultimo = CA.giro(conn, chiama=chiama)
            assert ultimo["candidate"] <= 120                     # mai più del limite tecnico in un giro
            for k in ("sito_in_coda", "sito_fatte", "sito_confermabili", "sito_restanti", "usd_oggi"):
                assert k in ultimo or not ultimo["candidate"], k
            attese.append(CA.attesa_prossimo_giro(ultimo, 3600.0))
            if attese[-1] != 120.0:
                break
        # 120 + 120 + 57 controlli; il terzo giro è ancora "pieno" (più di 40): un quarto giro vuoto e ci si ferma
        assert giri == 4 and attese == [120.0, 120.0, 120.0, 3600.0], (giri, attese)
        assert not ultimo.get("sito_restanti") and ultimo["candidate"] == 0
        assert len(chiamate) == n - len(senza_foto)               # ognuna una volta sola, le senza foto mai
        st = CA.stato_coda()
        assert st["senza_foto"] == 2 and st["fallite_6h"] == 1 and st["mai_controllate"] == 1 and st["in_attesa"] == 0
        assert st["gia_decise"] == n - 3 and st["eta_coda_s"] == 0 and CA.sito_in_attesa() == 0
        # un giro in più non richiama nessuno (la fallita aspetta 6 ore, le senza foto 24) e non riparte veloce
        prima = len(chiamate)
        res = CA.giro(conn, chiama=chiama)
        assert len(chiamate) == prima and CA.attesa_prossimo_giro(res, 3600.0) == 3600.0
        assert not res.get("sito_restanti")
        righe = [r for r in lg.righe if r.startswith("JEV_GIRO ")]
        assert len(righe) == 3 and all('"sito_restanti"' in r and '"usd_oggi"' in r for r in righe)
        assert sum(json.loads(r[9:])["sito_fatte"] for r in righe) == n - 3
    _pulisci()


def test_interruttore_spento_40_per_giro():
    _pulisci()
    n = 100
    conn = ConnViva([], [scheda(i, price_eur=8000) for i in range(1, n + 1)], _sito(n))
    CA.CODA_SITO.update(t=time.time(), entra=list(range(1, n + 1)))
    with env(CONTROLLO_PARALLELI="3", CONTROLLO_FOTO_MODO="url", **dict(NO_ENV, AUTOMATISMO_CONTROLLO_TUTTE="0")), \
            _foto_url():
        res = CA.giro(conn, chiama=lambda t, f: (dict(OK), 800, 80, "claude-haiku-5-5"))
        assert res["candidate"] == 40 and res["sito_fatte"] == 40
        assert CA.attesa_prossimo_giro(res, 3600.0) == 120.0      # come prima: giro pieno = giro dopo subito
    _pulisci()


def test_chiamate_tutte_fallite_nessun_ciclo_a_vuoto():
    _pulisci()
    n = 200
    conn = ConnViva([], [scheda(i, price_eur=8000) for i in range(1, n + 1)], _sito(n))
    CA.CODA_SITO.update(t=time.time(), entra=list(range(1, n + 1)))
    tentate = []

    def chiama(testo, foto):
        tentate.append(1)
        raise RuntimeError("rete giù")
    with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="url", **NO_ENV), _foto_url():
        res = CA.giro(conn, chiama=chiama)
        assert not res.get("fatte") and res["sito_restanti"] > 0 and len(tentate) <= 6     # si ferma dopo 5 errori
        assert CA.attesa_prossimo_giro(res, 3600.0) == 3600.0     # niente giro ogni 2 minuti a vuoto
        assert CA.sito_in_attesa() == 0                           # e niente risveglio: la coda è già stata offerta
        with env(AUTOMATISMO_CONTROLLO_TUTTE="0"):
            assert CA.attesa_prossimo_giro(res, 3600.0) == 120.0  # spento: esattamente come prima
    _pulisci()


def test_pausa_credito_senza_ciclo_stretto():
    _pulisci()

    class RateLimitError(Exception):
        status_code = 429

    n = 200
    conn = ConnViva([], [scheda(i, price_eur=8000) for i in range(1, n + 1)], _sito(n))
    CA.CODA_SITO.update(t=time.time(), entra=list(range(1, n + 1)), sconto_alto=set(range(1, n + 1)))
    tentate = []

    def chiama(testo, foto):
        tentate.append(1)
        if len(tentate) > 2:
            raise RateLimitError("limite")
        return dict(OK), 800, 80, "claude-haiku-5-5"
    with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="url", **NO_ENV), _foto_url():
        res = CA.giro(conn, chiama=chiama)
        assert res["fatte"] == 2 and res["pausa_limite"] == 1 and len(tentate) == 3
        assert res["sito_restanti"] > 0 and CA.attesa_prossimo_giro(res, 3600.0) == 3600.0
        st = CA.stato_coda()
        assert st["in_pausa"] and st["pausa_s"] > 0 and st["mai_controllate"] == n - 2
        # in pausa: nessun risveglio per le auto nuove della coda e il giro (se parte) salta senza chiamate
        CA.CODA_SITO["entra"] = list(range(1, n + 2))
        assert CA.sito_in_attesa() == 0
        res = CA.giro(conn, chiama=chiama)
        assert res["saltato"] and len(tentate) == 3 and CA.attesa_prossimo_giro(res, 3600.0) == 3600.0
        CA._PAUSA["fino"] = 0.0                                   # pausa finita: l'auto nuova sveglia il giro
        CA.CODA_SITO.pop("visti_giro", None)
        assert CA.sito_in_attesa() > 0
    _pulisci()


def test_attesa_prossimo_giro_casi():
    _pulisci()
    with env(**NO_ENV):
        f = CA.attesa_prossimo_giro
        assert f({"fonte": "claude", "candidate": 40, "fatte": 40}, 3600.0) == 120.0
        assert f({"fonte": "claude", "candidate": 12, "fatte": 12}, 3600.0) == 3600.0
        assert f({"fonte": "claude", "candidate": 12, "fatte": 12, "sito_restanti": 3}, 3600.0) == 120.0
        assert f({"fonte": "claude", "candidate": 12, "fatte": 12, "sito_restanti": 0}, 3600.0) == 3600.0
        assert f({"fonte": "claude", "candidate": 60, "fatte": 0, "sito_restanti": 9}, 3600.0) == 3600.0
        assert f({"fonte": "claude", "candidate": 60, "fatte": 3, "sito_restanti": 9, "pausa_credito": 1}, 900.0) == 900.0
        assert f({"saltato": "pausa", "candidate": 0, "sito_restanti": 50}, 3600.0) == 3600.0
        assert f({"spenta": "manca la chiave"}, 3600.0) == 3600.0 and f(None, 3600.0) == 3600.0
        assert f({"candidate": 60}, 3600.0, 60) == 120.0          # giro di Jev (senza "fonte"): come prima
        assert f({"candidate": 59}, 3600.0, 60) == 3600.0
    _pulisci()


# ------------------------------------------------------------------ stato e righe di log
def test_stato_coda_chiavi_e_riga_coda_controllo():
    _pulisci()
    vuoto = CA.stato_coda()
    for k in ("totale_sconto_alto", "mai_controllate", "gia_controllate_non_confermate", "senza_foto",
              "scartate_dal_testo", "gia_decise", "fallite_6h", "in_attesa", "eta_coda_s", "usd_oggi", "eta_elenco_s",
              "fresca", "ultimo_giro_s", "in_pausa", "pausa_s", "attivo"):
        assert k in vuoto, k
    assert vuoto["totale_sconto_alto"] == 0 and vuoto["eta_elenco_s"] is None and vuoto["fresca"] is False
    assert "errore" not in vuoto
    j = {"fonte": "claude", "danni": .05, "foto_internet": .05, "truffa": .1, "auto_vera": .5, "dati_coerenti": 1,
         "decisione": "tratta", "stesso_prezzo": True, "km_ok": 1, "guasto": 0}
    items = [auto(1), auto(2, margine=200), auto(3, _r={"perizia": {"giudizio": "caro"}}), auto(7, jev=j), auto(8)]
    with env(**NO_ENV), _Log() as lg:
        _coda(items)
        st = CA.stato_coda()
        assert st["totale_sconto_alto"] == 5 and st["gia_controllate_non_confermate"] == 1
        assert st["mai_controllate"] == 2 and st["in_attesa"] == 2 and st["fresca"] and st["eta_elenco_s"] is not None
        assert st["inutili:margine_minimo"] == 1 and st["inutili:perizia"] == 1
        assert st["totale_sconto_alto"] == (st["gia_controllate_non_confermate"] + st["mai_controllate"]
                                            + st["senza_foto"] + st["scartate_dal_testo"] + st["gia_decise"]
                                            + sum(v for k, v in st.items() if k.startswith("inutili:")))
        riga_log = [r for r in lg.righe if r.startswith("CODA_CONTROLLO ")]
        assert len(riga_log) == 1
        d = json.loads(riga_log[0][len("CODA_CONTROLLO "):])
        for k in ("totale_sconto_alto", "mai_controllate", "gia_controllate_non_confermate", "senza_foto",
                  "fallite_6h", "inutili:perizia", "inutili:margine_minimo", "entra"):
            assert k in d, k
        assert d["totale_sconto_alto"] == 5 and d["mai_controllate"] == 2
        # coda ferma: l'età cresce finché nessun giro conclude un controllo, poi riparte da zero
        CA.CODA_SITO["attesa_da"] = time.time() - 9000
        assert CA.stato_coda()["eta_coda_s"] >= 9000
        CA._segna_fallita(1)
        st = CA.stato_coda()
        assert st["fallite_6h"] == 1 and st["in_attesa"] == 1 and st["mai_controllate"] == 2
        CA._salta(8, "senza_foto")
        st = CA.stato_coda()
        assert st["senza_foto"] == 1 and st["mai_controllate"] == 1 and st["in_attesa"] == 0 and st["eta_coda_s"] == 0
    # mai un errore, nemmeno con lo stato rovinato
    CA.CODA_SITO["sconto_alto"] = 12
    assert isinstance(CA.stato_coda(), dict)
    _pulisci()


def test_nessun_telefono_o_email_all_ai_e_nei_log():
    _pulisci()
    tel, tel2, mail = "3331234567", "+39 02 1234 5678", "mario.rossi@example.com"
    descr = f"Vendo auto perfetta, chiamare {tel} oppure {tel2}, scrivere a {mail}. Solo interessati."
    schede = [scheda(1, price_eur=8000, description=descr, title=f"Panda {tel}")]
    CA.CODA_SITO.update(t=time.time(), entra=[1], sconto_alto={1})
    testi = []

    def chiama(testo, foto):
        testi.append(testo)
        return dict(OK, note=f"richiamare il {tel}"), 800, 80, "claude-haiku-5-5"
    with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="url", **NO_ENV), _foto_url(), _Log() as lg:
        res = CA.giro(ConnViva([], schede, _sito(1)), chiama=chiama)
        CA.CODA_SITO.pop("log_t", None)
        fun = SR.Funnel()
        fun.tolti = {}
        CA.coda_dal_sito([], [], fun, G)
    assert res["sito_fatte"] == 1 and len(testi) == 1
    for pezzo in (tel, "1234 5678", "12345678", mail, "@"):
        assert pezzo not in testi[0], pezzo
    assert "[numero]" in testi[0] and "[email]" in testi[0]
    assert CA._senza_dati_personali(descr).count("[numero]") == 2
    righe = [r for r in lg.righe if r.startswith(("JEV_GIRO", "CODA_CONTROLLO"))]
    assert len(righe) == 2
    for r in lg.righe:
        assert "@" not in r and tel not in r and not re.search(r"\d{8,}", r), r
    assert not re.search(r"\d{8,}|@", json.dumps(CA.stato_coda()))
    _pulisci()
