"""Sentinella (dealfinder/sentinella.py): ogni controllo, dedupe degli allarmi e coda notturna, con una connessione finta.

Nessun database: la connessione finta risponde alle query riconoscendole dall'etichetta /*S:nome*/ che ognuna porta.
"""
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from dealfinder import sentinella as S

_TAG = re.compile(r"/\*S:(\w+)\*/")
UTC = timezone.utc


class _Ris:
    def __init__(self, righe):
        self.righe = list(righe or [])

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Finta:
    """Connessione finta: risposte[etichetta] = lista di righe oppure funzione(parametri) -> righe."""

    def __init__(self, risposte=None, tabelle=("sito_istantanea", "sentinella_stato", "sentinella_allarmi",
                                              "config_impronte", "decisione_ai")):
        self.risposte = dict(risposte or {})
        self.tabelle = set(tabelle)
        self.eseguite = []
        self.commit_n = 0

    def execute(self, sql, p=None):
        self.eseguite.append((sql, p))
        m = _TAG.search(sql)
        if not m:
            return _Ris([])
        tag = m.group(1)
        if tag == "esiste" and tag not in self.risposte:
            return _Ris([{"c": p[0] in self.tabelle}])
        r = self.risposte.get(tag, [])
        return _Ris(r(p) if callable(r) else r)

    def tag(self, nome):
        return [p for sql, p in self.eseguite if f"/*S:{nome}*/" in sql]

    def commit(self):
        self.commit_n += 1

    def rollback(self):
        pass


def _sito(n, **extra):
    base = {"prezzo": 8000, "margine": 3000, "soglia": 1500, "fonte": "Subito", "commerciante": False,
            "provincia": "MI", "area": "milano", "km": 90000, "sezione": "affari", "categoria": "opportunita"}
    base.update(extra)
    return [dict(base, listing_id=1000 + i) for i in range(n)]


def _ctx(righe, ore=0.5):
    return {"istantanea": righe, "istantanea_ore": ore}


def _chiavi(esito):
    return {a["chiave"] for a in esito["allarmi"]}


# 1 ------------------------------------------------------------------------------------------------------------
def test_sito_conteggio_calo_oltre_20_per_cento():
    c = Finta({"stato_ieri": [{"n_sito": 100}]})
    r = S.c_sito_conteggio(c, _ctx(_sito(79)))
    assert not r["ok"] and "sito_calo" in _chiavi(r) and r["numeri"]["calo_pct"] == 21.0
    assert S.c_sito_conteggio(c, _ctx(_sito(85)))["ok"]                      # -15%: niente allarme


def test_sito_conteggio_senza_ieri_e_fotografia_vecchia():
    assert S.c_sito_conteggio(Finta(), _ctx(_sito(5)))["ok"]                 # nessuna riga di ieri
    assert S.c_sito_conteggio(Finta({"stato_ieri": [{"n_sito": 10}]}), _ctx(_sito(1)))["ok"]   # ieri troppo poche
    r = S.c_sito_conteggio(Finta(), _ctx(_sito(50), ore=5.0))
    assert _chiavi(r) == {"sito_istantanea_vecchia"}
    r = S.c_sito_conteggio(Finta(), {"istantanea": None})
    assert r["ok"] and not r["disponibile"]


# 2 ------------------------------------------------------------------------------------------------------------
def test_sotto_soglia():
    righe = _sito(3) + [dict(_sito(1)[0], listing_id=7, margine=900, soglia=1500),
                        dict(_sito(1)[0], listing_id=8, margine=900, sezione="eco")]   # economiche: soglie loro
    r = S.c_sotto_soglia(Finta(), _ctx(righe))
    assert _chiavi(r) == {"sotto_soglia"} and r["numeri"]["sotto"] == 1 and r["numeri"]["esempi"] == [7]
    assert S.c_sotto_soglia(Finta(), _ctx(_sito(4)))["ok"]


def test_sotto_soglia_usa_scovo_se_manca_la_soglia_salvata():
    soglia = S._soglia_locale()
    assert soglia is not None
    s = int(soglia(8000))
    r = S.c_sotto_soglia(Finta(), _ctx([dict(_sito(1)[0], margine=s - 1, soglia=None)]))
    assert not r["ok"]


# 3 ------------------------------------------------------------------------------------------------------------
def test_regole_cliente_roma_km_loconte_telefono():
    righe = _sito(4)
    righe[0].update(provincia="RM")
    righe[1].update(km=160000)
    righe[3].update(fonte="Facebook Marketplace")
    testi = [{"id": 1000, "title": "Panda", "description": "chiamare 333 1234567", "telefono_ok": None},
             {"id": 1001, "title": "Punto", "description": "", "telefono_ok": True},
             {"id": 1002, "title": "Golf Loconte auto", "description": "", "telefono_ok": None},   # senza telefono
             {"id": 1003, "title": "Yaris", "description": "", "telefono_ok": None}]               # Facebook: esente
    r = S.c_regole_cliente(Finta({"testi": testi}), _ctx(righe))
    assert _chiavi(r) == {"regole:roma", "regole:km", "regole:loconte", "regole:telefono"}
    n = r["numeri"]
    assert (n["roma"], n["oltre_km"], n["loconte"], n["senza_telefono"]) == (1, 1, 1, 1)
    al = {a["chiave"]: a for a in r["allarmi"]}
    assert al["regole:telefono"]["dati"]["esempi"] == [1002]


def test_regole_cliente_tutto_ok():
    testi = [{"id": 1000 + i, "title": "x", "description": "", "telefono_ok": True} for i in range(3)]
    assert S.c_regole_cliente(Finta({"testi": testi}), _ctx(_sito(3)))["ok"]


# 4 ------------------------------------------------------------------------------------------------------------
def test_tetto_commercianti():
    assert S.c_tetto_commercianti(Finta(), _ctx(_sito(40, commerciante=True) + _sito(5)))["ok"]
    r = S.c_tetto_commercianti(Finta(), _ctx(_sito(41, commerciante=True)))
    assert _chiavi(r) == {"tetto_commercianti"} and r["numeri"]["commercianti"] == 41


# 5 ------------------------------------------------------------------------------------------------------------
def test_impronte_sito_e_cron_diverse_sui_prezzi():
    pw = {"env": {"SOGLIE_PV": "<default>"}, "policy": {"costo_fisso": 200}}
    pc = {"env": {"SOGLIE_PV": "7000:1500"}, "policy": {"costo_fisso": 200}}
    righe = [{"processo": "web", "impronta": "a", "impronta_prezzi": "p1", "prezzi": json.dumps(pw)},
             {"processo": "cron:opportunita", "impronta": "b", "impronta_prezzi": "p2", "prezzi": pc},
             {"processo": "cron:mercato", "impronta": "c", "impronta_prezzi": "p1", "prezzi": pw}]
    r = S.c_impronte(Finta({"impronte": righe}), {})
    assert _chiavi(r) == {"impronte:cron:opportunita"}
    assert r["allarmi"][0]["dati"]["chiavi"] == ["env.SOGLIE_PV"]
    assert S.c_impronte(Finta({"impronte": righe[:1] + righe[2:]}), {})["ok"]


def test_impronta_prezzi_solo_regole_di_prezzo():
    env = {"SOGLIE_PV": "1", "POLICY_COSTO_FISSO": "200", "POLICY_RICALCOLO": "0", "BENZ_CAP": "20121"}
    h1, d = S.impronta_prezzi({"env": env, "policy": {"a": 1}})
    assert set(d["env"]) == {"SOGLIE_PV", "POLICY_COSTO_FISSO"}
    h2, _ = S.impronta_prezzi({"env": dict(env, BENZ_CAP="x", POLICY_RICALCOLO="1"), "policy": {"a": 1}})
    h3, _ = S.impronta_prezzi({"env": dict(env, SOGLIE_PV="2"), "policy": {"a": 1}})
    assert h1 == h2 and h1 != h3


# 6 ------------------------------------------------------------------------------------------------------------
def test_cron_vecchio_fallito_bloccato():
    c = Finta({"cron_ultimi": [{"job": "a", "ore": 3.0}, {"job": "b", "ore": 40.0}],
               "cron_falliti": [{"job": "a", "n": 3}, {"job": "x", "n": 1}],
               "cron_in_corso": [{"job": "c", "n": 1, "ore": 9.5}]})
    r = S.c_cron(c, {}, attese={"a": 8, "b": 30, "nuovo": 5})
    assert _chiavi(r) == {"cron_vecchio:b", "cron_vecchio:nuovo", "cron_fallito:a", "cron_in_corso:c"}
    assert r["numeri"]["ore_dall_ultimo"] == {"a": 3.0, "b": 40.0, "nuovo": None}
    assert S.c_cron(Finta({"cron_ultimi": [{"job": "a", "ore": 1}]}), {}, attese={"a": 8})["ok"]


# 7 ------------------------------------------------------------------------------------------------------------
def test_valutazioni_vecchie():
    righe = _sito(20) + _sito(1, sezione="supercar")
    ore = [{"listing_id": 1000 + i, "ore": 60.0 if i < 4 else 2.0} for i in range(18)]   # 4 vecchie, 2 senza
    r = S.c_valutazioni(Finta({"valutazioni": ore}), _ctx(righe))
    assert r["numeri"] == {"n": 20, "vecchie": 4, "senza": 2, "quota": 0.3}
    assert _chiavi(r) == {"valutazioni_vecchie"}
    ok = [{"listing_id": 1000 + i, "ore": 2.0} for i in range(20)]
    assert S.c_valutazioni(Finta({"valutazioni": ok}), _ctx(righe))["ok"]


# 8 ------------------------------------------------------------------------------------------------------------
def test_jev_fermo_e_senza_decisione():
    c = Finta({"jev_decisioni": [{"listing_id": 1000}], "jev_chiamate": [{"n24": 0, "n7": 50}]})
    r = S.c_jev(c, _ctx(_sito(20)))
    assert _chiavi(r) == {"jev_fermo", "jev_senza"} and r["numeri"]["senza_decisione"] == 19
    tutte = [{"listing_id": 1000 + i} for i in range(20)]
    assert S.c_jev(Finta({"jev_decisioni": tutte, "jev_chiamate": [{"n24": 30, "n7": 200}]}), _ctx(_sito(20)))["ok"]
    # Jev mai partito e non attivo sul sito: nessun allarme
    assert S.c_jev(Finta({"jev_chiamate": [{"n24": 0, "n7": 0}]}), _ctx(_sito(20)))["ok"]


# 9 ------------------------------------------------------------------------------------------------------------
def test_pagamenti_stripe_e_appuntamenti():
    cols = {"stripe_eventi": ["id", "created_at", "errore"], "appuntamenti": ["id", "creato_at", "email_ok"]}
    conteggi = {"stripe_errori": [{"n": 2}], "appuntamenti_errori": [{"n": 1}]}
    risp = {"tabelle_stripe": [{"table_name": "stripe_eventi"}],
            "colonne": lambda p: [{"column_name": c} for c in cols.get(p[0], [])], **conteggi}
    c = Finta(risp, tabelle=("appuntamenti",))
    r = S.c_pagamenti(c, {})
    assert _chiavi(r) == {"stripe:stripe_eventi", "appuntamenti_email"}
    sql = [s for s, _ in c.eseguite if "/*S:appuntamenti_errori*/" in s][0]
    assert "email_ok IS FALSE" in sql and "creato_at >" in sql
    r = S.c_pagamenti(Finta({}, tabelle=()), {})
    assert r["ok"] and not r["disponibile"]


# 10 -----------------------------------------------------------------------------------------------------------
def test_imbuto_passo_oltre_il_doppio_della_mediana():
    from datetime import date
    oggi = date(2026, 11, 4)
    righe = []
    for g in range(8):
        giorno = oggi - timedelta(days=g)
        righe.append({"giorno": giorno, "passo": "telefono", "entrati": 200, "usciti": 90 if g == 0 else 20})
        righe.append({"giorno": giorno, "passo": "zona", "entrati": 200, "usciti": 30})
    c = Finta({"colonne": [{"column_name": x} for x in ("giorno", "passo", "entrati", "usciti")], "imbuto": righe},
              tabelle=("sito_funnel_giorno",))
    r = S.c_imbuto(c, {})
    assert _chiavi(r) == {"imbuto:telefono"}
    assert r["numeri"]["telefono"] == {"oggi": 0.45, "mediana_7g": 0.1}
    assert S.c_imbuto(Finta({}, tabelle=()), {})["disponibile"] is False


def test_controllo_in_errore_diventa_allarme():
    def rotto(p):
        raise RuntimeError("canceling statement due to statement timeout")
    c = Finta({"istantanea": _sito(3), "istantanea_eta": [{"at": None, "ore": 0.1}], "stato_ieri": rotto})
    out = {r["controllo"]: r for r in S.controlla(c, {}, attese={})}
    assert len(out) == 10
    assert _chiavi(out["sito_conteggio"]) == {"errore:sito_conteggio"}
    assert out["tetto_commercianti"]["ok"]
    # ogni controllo gira in una transazione di sola lettura con statement_timeout
    sets = [s for s, _ in c.eseguite if s.startswith("SET")]
    assert sets.count("SET TRANSACTION READ ONLY") == 11 and "SET LOCAL statement_timeout = '60s'" in sets


# dedupe e coda ------------------------------------------------------------------------------------------------
class Allarmi(Finta):
    """Finta con la tabella sentinella_allarmi in memoria."""

    def __init__(self):
        self.righe = []
        super().__init__({"allarme_ultimo": self._ultimo, "coda": self._coda, "coda_conta": self._conta})

    def _ultimo(self, p):
        chiave, dal = p
        r = [a for a in self.righe if a["chiave"] == chiave and a["creato_at"] > dal]
        return [{"id": r[-1]["id"]}] if r else []

    def _coda(self, p):
        return [a for a in self.righe if a["inviato_at"] is None and a["tentativi"] < 5]

    def _conta(self, p):
        return [{"n": len(self._coda(p))}]

    def execute(self, sql, p=None):
        if "/*S:allarme_nuovo*/" in sql:
            self.righe.append({"id": len(self.righe) + 1, "chiave": p[0], "testo": p[2], "creato_at": p[4],
                               "inviato_at": None, "tentativi": 0, "volte": 1})
        elif "/*S:allarme_visto*/" in sql:
            next(a for a in self.righe if a["id"] == p[1])["volte"] += 1
        elif "/*S:coda_inviati*/" in sql:
            for a in self.righe:
                if a["id"] in p[1]:
                    a["inviato_at"] = p[0]
        elif "/*S:coda_tentativo*/" in sql:
            for a in self.righe:
                if a["id"] in p[0]:
                    a["tentativi"] += 1
        return super().execute(sql, p)


def _t(ora_utc, giorno=4):
    return datetime(2026, 11, giorno, ora_utc, 0, tzinfo=UTC)     # novembre: Roma = UTC+1


def test_dedupe_sei_ore():
    c = Allarmi()
    a = [{"chiave": "sito_calo", "testo": "calo", "controllo": "sito_conteggio"}]
    nuovi, rip = S.registra_allarmi(c, a, _t(9))
    assert len(nuovi) == 1 and rip == 0
    nuovi, rip = S.registra_allarmi(c, a, _t(14))                 # 5 ore dopo: stesso allarme, non nuovo
    assert nuovi == [] and rip == 1 and c.righe[0]["volte"] == 2
    nuovi, rip = S.registra_allarmi(c, a, _t(16))                 # 7 ore dopo il primo: di nuovo
    assert len(nuovi) == 1 and len(c.righe) == 2


def test_ora_di_roma_e_finestra_email():
    assert not S.di_giorno(_t(6))            # 07:00 a Roma
    assert S.di_giorno(_t(7))                # 08:00 a Roma
    assert S.di_giorno(_t(20))               # 21:00
    assert not S.di_giorno(_t(21))           # 22:00
    assert S.ora_roma(datetime(2026, 7, 1, 6, tzinfo=UTC)).hour == 8      # estate: UTC+2


def test_coda_notturna_e_riepilogo_del_mattino():
    c = Allarmi()
    mandate = []
    invia = lambda o, t: mandate.append((o, t)) or True
    S.registra_allarmi(c, [{"chiave": "a", "testo": "allarme A"}], _t(23, giorno=3))   # 00:00 a Roma
    S.registra_allarmi(c, [{"chiave": "b", "testo": "allarme B"}], _t(3))              # 04:00 a Roma
    r = S.invia_coda(c, _t(3), invia)
    assert r == {"inviati": 0, "in_coda": 2, "notte": True} and mandate == []
    r = S.invia_coda(c, _t(7), invia)                                                  # 08:00: un solo riepilogo
    assert r["inviati"] == 2 and r["mattino"] and len(mandate) == 1
    assert "riepilogo del mattino" in mandate[0][0] and "allarme A" in mandate[0][1] and "allarme B" in mandate[0][1]
    assert S.invia_coda(c, _t(8), invia) == {"inviati": 0, "in_coda": 0} and len(mandate) == 1
    S.registra_allarmi(c, [{"chiave": "c", "testo": "allarme C"}], _t(10))             # di giorno: subito
    r = S.invia_coda(c, _t(10), invia)
    assert r["inviati"] == 1 and not r["mattino"] and "nuovi" in mandate[1][0]


def test_invio_non_riuscito_si_riprova_al_massimo_cinque_volte():
    c = Allarmi()
    S.registra_allarmi(c, [{"chiave": "a", "testo": "A"}], _t(9))
    for _ in range(5):
        assert S.invia_coda(c, _t(10), lambda o, t: False)["in_coda"] == 1
    assert c.righe[0]["tentativi"] == 5 and S.invia_coda(c, _t(10), lambda o, t: True)["inviati"] == 0


# giro completo ------------------------------------------------------------------------------------------------
def _connetti(c):
    @contextmanager
    def f():
        yield c
    return f


def test_esegui_giro_completo_e_riga_di_log():
    S._TABELLE["ok"] = True
    try:
        c = Allarmi()
        c.risposte.update({"istantanea": _sito(30, commerciante=True) + _sito(15), "istantanea_eta": [{"ore": 0.2}],
                           "stato_ieri": [{"n_sito": 45}],
                           "testi": lambda p: [{"id": i, "telefono_ok": True} for i in p[0]]})
        mandate = []
        r = S.esegui("prova", connetti=_connetti(c), invia=lambda o, t: mandate.append(o) or True, adesso=_t(10),
                     attese={})
        assert "errore" not in r, r
        assert r["controlli"]["sito_conteggio"]["n"] == 45 and r["controlli"]["tetto_commercianti"]["ok"]
        assert len(c.tag("stato_salva")) == 1 and c.tag("stato_salva")[0][1] == 45
        json.dumps(r, default=str)
        # secondo processo pochi minuti dopo: salta i controlli
        c.risposte["ultimo_giro"] = [{"at": _t(10)}]
        r2 = S.esegui("web", connetti=_connetti(c), invia=lambda o, t: True, adesso=_t(10) + timedelta(minutes=5))
        assert "saltato" in r2 and len(c.tag("stato_salva")) == 1
    finally:
        S._TABELLE["ok"] = False


def test_salva_istantanea_righe_e_nessuna_riscrittura_se_uguale():
    S._TABELLE["ok"] = True
    firma, t = S._ist["firma"], S._ist["t"]
    try:
        items = [{"id": 5, "prezzo": 8000, "pv": {"margine": 2100.4, "categoria": "opportunita"}, "fonte": "Subito",
                  "zona": "Monza (MB)", "area": "milano", "km": 80000},
                 {"id": 6, "prezzo": 900, "pv": {}, "fonte": "AutoScout24", "economica": True, "commerciante_top": True},
                 {"id": "x"}]
        c = Finta()
        assert S.salva_istantanea(items, connetti=_connetti(c)) == 2
        col = c.tag("ist_scrivi")[0]
        assert col[0] == [5, 6] and col[2] == [2100, None] and col[5] == [False, True] and col[6] == ["MB", None]
        assert col[9] == ["affari", "eco"] and col[3][0] == int(S._soglia_locale()(8000))
        assert len(c.tag("ist_svuota")) == 1 and c.commit_n == 1
        assert S.salva_istantanea(items, connetti=_connetti(c)) == 0 and len(c.tag("ist_svuota")) == 1
        assert c.tag("ist_rinfresca") == []
        S._ist["t"] -= S.ISTANTANEA_RINFRESCA_S + 1             # elenco uguale dopo 30 minuti: solo l'ora
        assert S.salva_istantanea(items, connetti=_connetti(c)) == 0
        assert len(c.tag("ist_rinfresca")) == 1 and len(c.tag("ist_svuota")) == 1
    finally:
        S._TABELLE["ok"] = False
        S._ist.update(firma=firma, t=t)


def test_spenta_e_mai_eccezioni():
    def rotta():
        raise RuntimeError("db giù")
    r = S.esegui("prova", connetti=rotta, adesso=_t(10))
    assert "errore" in r
    assert S.salva_istantanea([{"id": 1}], connetti=rotta) == -1
