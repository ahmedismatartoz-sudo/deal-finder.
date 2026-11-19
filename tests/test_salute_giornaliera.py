"""Salute giornaliera (dealfinder/salute.py, 19/11): mediana e calo del 15%, storico, motivi anomali e azioni, limiti,
notte in coda, testi senza dati personali, interruttori. Nessun database: connessione finta che riconosce le query
dall'etichetta /*H:nome*/ (salute) o /*S:nome*/ (sentinella) e tiene in memoria salute_giorno e gli allarmi.
"""
import json
import re
import types
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from dealfinder import automatismi, salute as H
from dealfinder import sentinella as S

_TAG = re.compile(r"/\*[HS]:(\w+)\*/")
UTC = timezone.utc
GIORNO = datetime(2026, 11, 19, 11, 5, tzinfo=UTC)      # 12:05 a Roma
NOTTE = datetime(2026, 11, 19, 1, 5, tzinfo=UTC)        # 02:05 a Roma


class _Ris:
    def __init__(self, righe):
        self.righe = list(righe or [])

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Finta:
    def __init__(self, stato=(), imbuto=(), scaduti=(), lucchetto=True):
        self.stato = list(stato)                 # righe di sentinella_stato
        self.imbuto = list(imbuto)               # righe di sito_funnel_giorno già sommate
        self.scaduti = list(scaduti)
        self.lucchetto = lucchetto
        self.giorni: dict = {}                   # salute_giorno
        self.allarmi: list = []                  # sentinella_allarmi
        self.eseguite = []

    # -- le tabelle finte
    def _riga(self, g):
        return self.giorni.setdefault(g, {"giorno": g, "n_sito": None, "n_privati": None, "n_commercianti": None,
                                          "motivi": [], "azioni": [], "aggiornato": None})

    def execute(self, sql, p=None):
        self.eseguite.append((sql, p))
        m = _TAG.search(sql)
        tag = m.group(1) if m else ""
        if tag == "esiste":
            return _Ris([{"c": True}])
        if tag == "prenota":
            g, adesso, soglia = p
            r = self._riga(g)
            if r["aggiornato"] is not None and r["aggiornato"] >= soglia:
                return _Ris([])
            r["aggiornato"] = adesso
            return _Ris([{"giorno": g}])
        if tag == "stato":
            return _Ris([r for r in self.stato if r["ora"] > p[0]])
        if tag == "giorno_salva":
            r = self._riga(p[0])
            r.update(n_sito=p[1], n_privati=p[2], n_commercianti=p[3])
            r["aggiornato"] = r["aggiornato"] or p[4]
            return _Ris([])
        if tag == "giorni":
            return _Ris([dict(r) for g, r in sorted(self.giorni.items()) if g >= p[0]])
        if tag == "ultimo":
            rr = sorted((r for r in self.stato if r["ora"] > p[0]), key=lambda r: r["ora"])
            return _Ris(rr[-1:])
        if tag == "motivi_salva":
            self._riga(p[1])["motivi"] = json.loads(p[0])
        if tag == "azioni_prenota":
            self._riga(p[1])["azioni"] = self._riga(p[1])["azioni"] + json.loads(p[0])
        if tag == "azioni_esito":
            self._riga(p[1])["azioni"][-1] = json.loads(p[0])
        if tag == "lucchetto":
            return _Ris([{"ok": self.lucchetto}])
        if tag == "scaduti":
            return _Ris(self.scaduti[:p["max"]])
        if tag == "colonne":
            return _Ris([{"column_name": c} for c in ("giorno", "passo", "entrate", "uscite")])
        if tag == "imbuto":
            return _Ris(self.imbuto)
        if tag == "allarme_ultimo":
            return _Ris([{"id": i} for i, a in enumerate(self.allarmi) if a["chiave"] == p[0] and a["visto_at"] > p[1]][-1:])
        if tag == "allarme_nuovo":
            self.allarmi.append({"chiave": p[0], "controllo": p[1], "testo": p[2], "creato_at": p[4], "visto_at": p[5],
                                 "inviato_at": None, "id": len(self.allarmi)})
        if tag == "coda_conta":
            return _Ris([{"n": sum(1 for a in self.allarmi if a["inviato_at"] is None)}])
        if tag == "coda":
            return _Ris([a for a in self.allarmi if a["inviato_at"] is None])
        if tag == "coda_inviati":
            for a in self.allarmi:
                if a["id"] in p[1]:
                    a["inviato_at"] = p[0]
        return _Ris([])

    def tag(self, nome):
        return [p for sql, p in self.eseguite if f":{nome}*/" in sql]

    def commit(self):
        pass

    def rollback(self):
        pass


def _connetti(c):
    @contextmanager
    def f():
        yield c
    return f


def _ore(giorno: date, n, ore=range(9, 22), privati=None):
    """Righe orarie di sentinella_stato per un giorno (ore di Roma = UTC+1 a novembre)."""
    return [{"ora": datetime(giorno.year, giorno.month, giorno.day, h - 1, tzinfo=UTC), "n_sito": n,
             "n_privati": n - 10 if privati is None else privati, "n_commercianti": 10 if privati is None else n - privati}
            for h in ore]


def _storia(valori, oggi_n=None, adesso=GIORNO):
    """valori = n dei giorni precedenti (dal più lontano a ieri); oggi_n = n delle ore di oggi fino ad adesso."""
    oggi = S.ora_roma(adesso).date()
    righe = []
    for k, n in enumerate(reversed(valori), start=1):
        righe += _ore(oggi - timedelta(days=k), n)
    if oggi_n is not None:
        righe += [r for r in _ore(oggi, oggi_n) if r["ora"] <= adesso]
    return righe


def _acceso(monkeypatch, **valori):
    monkeypatch.setattr(automatismi, "valori", lambda: dict(valori))
    monkeypatch.setattr(automatismi, "_env", lambda nome: None)
    monkeypatch.setattr(H, "_stato", {"azioni_in_corso": False})


class _App:
    """Finto dealfinder.web.app: elenco, calcolo in corso e contatore dei ricalcoli."""

    def __init__(self, in_corso=False):
        self._elenco = {"t": 123.0, "items": [1, 2, 3]}
        self._calcolo = {"in_corso": in_corso}
        self.chiamate = []

    def _aggiorna_elenco(self, attendi, motivo=None):
        self.chiamate.append((attendi, motivo, self._elenco["t"]))


def _finti(monkeypatch, app=None, memoria_alta=False, richiedi="si"):
    """Sostituisce app, memoria, confronti mirati e valuta_e_scrivi; ritorna (app, registro delle chiamate)."""
    app = app or _App()
    reg = {"ordine": [], "ves": [], "notte": []}
    monkeypatch.setattr(H, "_app", lambda: app)
    monkeypatch.setattr(H.memoria, "alta", lambda quota=0.6, **k: memoria_alta)
    from dealfinder.pricing import confronti_mirati as CM
    if richiedi == "si":
        def richiedi_notte(conn, motivo):
            reg["ordine"].append(H.A3)
            reg["notte"].append(motivo)
            return True
        monkeypatch.setattr(CM, "richiedi_notte", richiedi_notte, raising=False)
    else:
        monkeypatch.delattr(CM, "richiedi_notte", raising=False)
    vero = app._aggiorna_elenco

    def agg(attendi, motivo=None):
        reg["ordine"].append(H.A1)
        vero(attendi, motivo)
    app._aggiorna_elenco = agg

    def ves(conn, ids, fonte, pubblica=None, pausa_gruppo=0.0):
        if H.A2 not in reg["ordine"]:
            reg["ordine"].append(H.A2)
        reg["ves"].append((list(ids), fonte, pubblica, pausa_gruppo))
    return app, reg, ves


def _scaduti(n):
    return [{"id": i, "make": "Fiat", "model": "Panda" if i % 2 else "500", "fuel": "benzina"} for i in range(1, n + 1)]


def _giro(c, monkeypatch, adesso=GIORNO, coda=lambda: {}, **k):
    inviate = []
    r = H.esegui(connetti=_connetti(c), invia=lambda o, t: inviate.append((o, t)) or True, adesso=adesso, coda=coda, **k)
    return r, inviate


# ---------------------------------------------------------------------------------------------------- storico
def test_valore_del_giorno_mediana_ore_9_21():
    g = date(2026, 11, 18)
    righe = _ore(g, 100) + _ore(g, 5, ore=(2, 3, 23)) + _ore(g, 300, ore=(8,))      # notte e ore 8 non contano
    v = H.valori_giorno(righe)
    assert v == {g: {"n_sito": 100, "n_privati": 90, "n_commercianti": 10}}
    assert H.valori_giorno(_ore(g, 100, ore=(9, 10)) + _ore(g, 81, ore=(11,)))[g]["n_sito"] == 100


def test_riempimento_dallo_storico_al_primo_avvio():
    c = Finta(stato=_storia([100, 104, 96, 100], oggi_n=98))
    assert H.aggiorna_storico(c, GIORNO) == 5
    oggi = date(2026, 11, 19)
    assert [c.giorni[oggi - timedelta(days=k)]["n_sito"] for k in (4, 3, 2, 1, 0)] == [100, 104, 96, 100, 98]
    c.giorni[oggi]["azioni"] = [{"at": "x"}]
    H.aggiorna_storico(c, GIORNO)                                  # non tocca motivi e azioni
    assert c.giorni[oggi]["azioni"] == [{"at": "x"}]


# ---------------------------------------------------------------------------------------------------- calo
def _controlla(valori, oggi_n, coda=lambda: {}, imbuto=()):
    c = Finta(stato=_storia(valori, oggi_n=oggi_n), imbuto=imbuto)
    H.aggiorna_storico(c, GIORNO)
    return H.controlla(c, GIORNO, coda)


def test_calo_oltre_il_15_per_cento_sulla_mediana():
    r = _controlla([100, 100, 120, 80, 100], 84)
    assert r["mediana_7g"] == 100 and r["calo"] and r["calo_pct"] == 16.0
    assert [a["chiave"] for a in r["allarmi"]] == ["salute:calo"] and r["allarmi"][0]["controllo"] == "salute"
    r = _controlla([100, 100, 120, 80, 100], 85)                   # esattamente 85%: non è un calo
    assert not r["calo"] and r["allarmi"] == []


def test_meno_di_tre_giorni_o_mediana_piccola_niente_allarme():
    r = _controlla([100, 100], 10)
    assert r["mediana_7g"] is None and not r["calo"] and r["allarmi"] == []
    r = _controlla([19, 19, 19, 19], 5)                            # mediana sotto 20
    assert not r["calo"] and r["allarmi"] == []
    assert _controlla([100, 100, 100], 50)["calo"]                 # tre giorni bastano


def test_solo_gli_ultimi_sette_giorni_fanno_la_mediana():
    r = _controlla([500, 500, 100, 100, 100, 100, 100, 100, 100], 90)
    assert r["giorni"] == 7 and r["mediana_7g"] == 100 and not r["calo"]


# ---------------------------------------------------------------------------------------------------- motivi
def _imbuto(passo="km", oggi=0.5, prima=0.1, adesso=GIORNO):
    g = S.ora_roma(adesso).date()
    return ([{"giorno": g, "passo": passo, "entrati": 100, "usciti": int(oggi * 100)}]
            + [{"giorno": g - timedelta(days=k), "passo": passo, "entrati": 100, "usciti": int(prima * 100)}
               for k in (1, 2, 3, 4)])


def test_motivi_coda_ferma_e_spesa_sono_avvisi_senza_azioni():
    coda = lambda: {"mai_controllate": 12, "totale_sconto_alto": 299, "usd_oggi": 6.5, "eta_coda_min": 130}
    r = _controlla([100, 100, 100], 100, coda=coda)
    assert [a["chiave"] for a in r["allarmi"]] == ["salute:motivo:controllo_fermo", "salute:motivo:spesa_controllo"]
    assert H.azioni_per(r["calo"], r["motivi"]) == []
    fresca = lambda: {"mai_controllate": 12, "totale_sconto_alto": 299, "usd_oggi": 4.9, "eta_coda_min": 119}
    assert _controlla([100, 100, 100], 100, coda=fresca)["allarmi"] == []
    assert _controlla([100, 100, 100], 100, coda=lambda: None)["motivi"] == []     # stato_coda non disponibile


def test_stato_coda_assente_non_ferma_il_controllo(monkeypatch):
    from dealfinder.ai import controllo_auto
    monkeypatch.delattr(controllo_auto, "stato_coda", raising=False)
    assert H.stato_coda() is None
    monkeypatch.setattr(controllo_auto, "stato_coda", lambda: {"mai_controllate": 0}, raising=False)
    assert H.stato_coda() == {"mai_controllate": 0}

    def rotta():
        raise RuntimeError("x")
    monkeypatch.setattr(controllo_auto, "stato_coda", rotta, raising=False)
    assert H.stato_coda() is None


def test_anomalia_azione_giusta():
    assert H.azioni_per(True, []) == [H.A1, H.A2, H.A3]
    assert H.azioni_per(False, [{"tipo": "imbuto", "passo": "km"}]) == [H.A1, H.A2]
    assert H.azioni_per(False, [{"tipo": "coda"}, {"tipo": "spesa"}]) == []
    assert H.azioni_per(False, []) == []
    r = _controlla([100, 100, 100], 100, imbuto=_imbuto())
    assert [m["passo"] for m in r["motivi"]] == ["km"] and r["allarmi"] == []      # da solo lo dà già la sentinella
    r = _controlla([100, 100, 100], 60, imbuto=_imbuto())
    assert [a["chiave"] for a in r["allarmi"]] == ["salute:calo", "salute:motivo:km"]


# ---------------------------------------------------------------------------------------------------- giro e azioni
def test_calo_fa_le_tre_azioni_in_ordine(monkeypatch):
    _acceso(monkeypatch)
    app, reg, ves = _finti(monkeypatch)
    c = Finta(stato=_storia([100, 100, 100, 100], oggi_n=70), scaduti=_scaduti(450))
    r, inviate = _giro(c, monkeypatch, ves=ves)
    assert reg["ordine"] == [H.A1, H.A2, H.A3] and reg["notte"] == ["salute"]
    assert app._elenco["t"] == 0.0 and app.chiamate == [(False, "salute", 0.0)]
    assert sum(len(v[0]) for v in reg["ves"]) == 450 and len(reg["ves"]) >= 2
    assert all(v[1] == "salute" and v[2] is True and v[3] > 0 for v in reg["ves"])
    assert r["esito"] == "azioni_fatte" and [a["azione"] for a in r["azioni"]] == [H.A1, H.A2, H.A3]
    assert [a["esito"] for a in r["azioni"]] == ["ok", "ok", "richiesta"] and r["azioni"][1]["auto"] == 450
    oggi = date(2026, 11, 19)
    assert len(c.giorni[oggi]["azioni"]) == 1 and len(c.giorni[oggi]["azioni"][0]["esiti"]) == 3
    assert c.giorni[oggi]["motivi"][0]["passo"] == "calo"
    assert len(inviate) == 1 and "Salute" in inviate[0][1]                 # di giorno parte dalla coda della sentinella
    assert c.tag("lucchetto") == [(H.LOCK,)] and c.tag("sblocca") == [(H.LOCK,)] and H.LOCK == 734141


def test_rivalutazione_al_massimo_600(monkeypatch):
    _acceso(monkeypatch)
    app, reg, ves = _finti(monkeypatch)
    c = Finta(scaduti=_scaduti(900))
    r = H.a2_rivaluta(_connetti(c), ves)
    assert r["auto"] == 600 and c.tag("scaduti")[0]["max"] == 600 and c.tag("scaduti")[0]["ore"] == 48
    _acceso(monkeypatch, salute_rivaluta_max=5000)                 # il numero regolabile non supera 600
    assert H.a2_rivaluta(_connetti(Finta(scaduti=_scaduti(900))), ves)["auto"] == 600
    _acceso(monkeypatch, salute_rivaluta_max=50)
    assert H.a2_rivaluta(_connetti(Finta(scaduti=_scaduti(900))), ves)["auto"] == 50


def test_rivalutazione_salta_con_calcolo_in_corso_o_memoria_alta(monkeypatch):
    _acceso(monkeypatch)
    app, reg, ves = _finti(monkeypatch, app=_App(in_corso=True))
    c = Finta(scaduti=_scaduti(50))
    assert H.a2_rivaluta(_connetti(c), ves)["esito"] == "saltata:calcolo_in_corso"
    app, reg2, ves2 = _finti(monkeypatch, memoria_alta=True)
    assert H.a2_rivaluta(_connetti(c), ves2)["esito"] == "saltata:memoria_alta"
    assert reg["ves"] == [] and reg2["ves"] == [] and c.tag("scaduti") == []
    # a metà: il calcolo grande parte dopo il primo blocco
    app, reg3, ves3 = _finti(monkeypatch)

    def ves_poi(conn, ids, fonte, **k):
        ves3(conn, ids, fonte, **k)
        app._calcolo["in_corso"] = True
    r = H.a2_rivaluta(_connetti(Finta(scaduti=_scaduti(450))), ves_poi)
    assert r["esito"] == "fermata:calcolo_in_corso" and 0 < r["auto"] < 450 and len(reg3["ves"]) == 1


def test_azioni_mancanti_si_saltano(monkeypatch):
    _acceso(monkeypatch)
    _finti(monkeypatch, richiedi="no")
    assert H.a3_confronti(_connetti(Finta()))["esito"] == "saltata:richiedi_notte_assente"
    monkeypatch.setattr(H, "_app", lambda: None)                   # fuori dal sito (cron): niente elenco
    assert H.a1_elenco()["esito"] == "saltata:non_nel_sito"


def test_lucchetto_di_un_altro_processo(monkeypatch):
    _acceso(monkeypatch)
    app, reg, ves = _finti(monkeypatch)
    esiti = H.azioni([H.A1, H.A2, H.A3], _connetti(Finta(lucchetto=False, scaduti=_scaduti(10))), ves)
    assert [e["esito"] for e in esiti] == ["saltata:altro_processo"] and reg["ordine"] == []
    assert H.azioni(["cancella_tutto"], _connetti(Finta()), ves) == []            # solo A1, A2, A3


def test_limiti_sei_ore_e_tre_al_giorno(monkeypatch):
    _acceso(monkeypatch)
    app, reg, ves = _finti(monkeypatch)
    t0 = datetime(2026, 11, 19, 8, 5, tzinfo=UTC)                  # 09:05 a Roma
    c = Finta(scaduti=_scaduti(10))
    esiti = []
    for ore in (0, 1, 5, 6, 7, 12, 13.5):
        adesso = t0 + timedelta(hours=ore)
        c.stato = _storia([100, 100, 100, 100], oggi_n=60, adesso=adesso)
        esiti.append(_giro(c, monkeypatch, adesso=adesso, ves=ves)[0]["esito"])
    assert esiti == ["azioni_fatte", "azioni_rimandate:presto", "azioni_rimandate:presto", "azioni_fatte",
                     "azioni_rimandate:presto", "azioni_fatte", "azioni_rimandate:tetto_giorno"]
    assert reg["ordine"].count(H.A1) == 3 and len(c.giorni[date(2026, 11, 19)]["azioni"]) == 3
    # il limite delle 6 ore vale anche a cavallo della mezzanotte
    ieri_sera = [{"giorno": date(2026, 11, 18), "azioni": [{"at": "2026-11-18T21:30:00+00:00", "azioni": [H.A1]}]}]
    assert H.limite_azioni(ieri_sera, datetime(2026, 11, 19, 1, 0, tzinfo=UTC)) == "presto"
    assert H.limite_azioni(ieri_sera, datetime(2026, 11, 19, 4, 0, tzinfo=UTC)) is None
    assert H.limite_azioni([{"giorno": date(2026, 11, 18), "azioni": json.dumps(ieri_sera[0]["azioni"])}],
                           datetime(2026, 11, 19, 1, 0, tzinfo=UTC)) == "presto"   # jsonb arrivato come testo


def test_un_giro_all_ora(monkeypatch):
    _acceso(monkeypatch)
    _finti(monkeypatch)
    c = Finta(stato=_storia([100, 100, 100], oggi_n=100))
    assert _giro(c, monkeypatch)[0]["esito"] == "ok"
    assert _giro(c, monkeypatch, adesso=GIORNO + timedelta(minutes=10))[0] == {"saltato": True}
    assert _giro(c, monkeypatch, adesso=GIORNO + timedelta(minutes=50))[0] == {"saltato": True}
    assert _giro(c, monkeypatch, adesso=GIORNO + timedelta(minutes=60))[0]["esito"] == "ok"
    assert len(c.tag("giorni")) == 2


def test_di_notte_allarme_in_coda_e_nessun_invio(monkeypatch):
    _acceso(monkeypatch)
    app, reg, ves = _finti(monkeypatch)
    # alle 02:05 di Roma l'ultima ora della sentinella è notturna: vale il valore di oggi (assente) -> niente calo;
    # il motivo anomalo (coda ferma) dà comunque l'allarme, che resta in coda
    c = Finta(stato=_storia([100, 100, 100, 100], adesso=NOTTE))
    coda = lambda: {"mai_controllate": 40, "totale_sconto_alto": 299, "usd_oggi": 0.4, "eta_coda_min": 500}
    r, inviate = _giro(c, monkeypatch, adesso=NOTTE, coda=coda, ves=ves)
    assert inviate == [] and r["email"] == {"inviati": 0, "in_coda": 1, "notte": True}
    assert [a["chiave"] for a in c.allarmi] == ["salute:motivo:controllo_fermo"] and c.allarmi[0]["inviato_at"] is None
    assert c.allarmi[0]["controllo"] == "salute" and r["allarmi_nuovi"] == 1
    # il primo giro dopo le 8 lo manda (invio della sentinella, finto)
    r, inviate = _giro(c, monkeypatch, adesso=datetime(2026, 11, 19, 7, 30, tzinfo=UTC), coda=lambda: {}, ves=ves)
    assert len(inviate) == 1 and "mai controllate" in inviate[0][1] and c.allarmi[0]["inviato_at"] is not None


def test_di_sera_dopo_le_22_calo_in_coda(monkeypatch):
    _acceso(monkeypatch)
    app, reg, ves = _finti(monkeypatch)
    sera = datetime(2026, 11, 19, 21, 30, tzinfo=UTC)              # 22:30 a Roma: vale la mediana di oggi
    c = Finta(stato=_storia([100, 100, 100, 100], oggi_n=60, adesso=sera), scaduti=_scaduti(5))
    r, inviate = _giro(c, monkeypatch, adesso=sera, ves=ves)
    assert r["n"] == 60 and inviate == [] and r["email"]["notte"] and r["email"]["in_coda"] == 1
    assert [a["chiave"] for a in c.allarmi] == ["salute:calo"]


def test_testi_e_log_solo_conteggi(monkeypatch):
    _acceso(monkeypatch)
    app, reg, ves = _finti(monkeypatch)
    righe = []
    monkeypatch.setattr(H.log, "warning", lambda msg, *a: righe.append(msg % a if a else msg))
    c = Finta(stato=_storia([100, 100, 100, 100], oggi_n=60), scaduti=_scaduti(30), imbuto=_imbuto())
    coda = lambda: {"mai_controllate": 40, "totale_sconto_alto": 299, "usd_oggi": 7.25, "eta_coda_min": 500}
    r, inviate = _giro(c, monkeypatch, coda=coda, ves=ves)
    assert len(c.allarmi) == 4
    testi = [a["testo"] for a in c.allarmi] + righe + [inviate[0][1]]
    for t in testi:
        assert "@" not in t and not re.search(r"\d{7,}", t) and "http" not in t, t
    salute = [json.loads(x.split(" ", 1)[1]) for x in righe if x.startswith("SALUTE {")]
    assert len(salute) == 1 and {"giorno", "n", "mediana_7g", "calo_pct", "motivi_anomali", "azioni", "esito"} <= set(salute[0])
    assert salute[0]["motivi_anomali"] == ["km", "controllo_fermo", "spesa_controllo"]
    az = [json.loads(x.split(" ", 1)[1]) for x in righe if x.startswith("SALUTE_AZIONE ")]
    assert [a["azione"] for a in az] == [H.A1, H.A2, H.A3] and all(set(a) == {"azione", "auto", "secondi", "esito"} for a in az)


# ---------------------------------------------------------------------------------------------------- interruttori
def test_interruttore_spento_non_fa_niente(monkeypatch):
    _acceso(monkeypatch, salute=False)
    c = Finta(stato=_storia([100, 100, 100, 100], oggi_n=60))
    assert _giro(c, monkeypatch)[0] == {"spento": True} and c.eseguite == []


def test_azioni_spente_solo_avvisi(monkeypatch):
    _acceso(monkeypatch, salute_azioni=False)
    app, reg, ves = _finti(monkeypatch)
    c = Finta(stato=_storia([100, 100, 100, 100], oggi_n=60), scaduti=_scaduti(30))
    r, inviate = _giro(c, monkeypatch, ves=ves)
    assert r["esito"] == "solo_avvisi" and r["azioni"] == [] and reg["ordine"] == [] and app.chiamate == []
    assert [a["chiave"] for a in c.allarmi] == ["salute:calo"] and len(inviate) == 1
    assert c.giorni[date(2026, 11, 19)]["azioni"] == [] and c.tag("lucchetto") == []


def test_esegui_non_solleva_mai(monkeypatch):
    _acceso(monkeypatch)

    @contextmanager
    def rotto():
        raise RuntimeError("database giù")
        yield
    r = H.esegui(connetti=rotto, adesso=GIORNO)
    assert r["esito"] == "errore:RuntimeError"


# ---------------------------------------------------------------------------------------------------- regole fisse
def test_nessuna_scrittura_fuori_da_salute_e_sentinella():
    testo = open(H.__file__, encoding="utf-8").read()
    scritture = [t for t in re.findall(r"(?:UPDATE|INSERT INTO|DELETE FROM)\s+(\w+)", testo) if t != "SET"]
    assert scritture and set(scritture) <= {"salute_giorno"}, scritture
    for vietato in ("_invia_brevo", "httpx", "smtp", "os.environ", "facebook", "unlocker"):
        assert vietato not in testo.lower().replace("sentinella._invia_brevo", ""), vietato


def test_migrazione_064_solo_aggiunta_e_aggancio_nella_sentinella():
    import os
    radice = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sql = open(os.path.join(radice, "db", "migrations", "064_salute.sql"), encoding="utf-8").read()
    comandi = [r for r in sql.splitlines() if r.strip() and not r.strip().startswith("--")]
    assert comandi[0].startswith("CREATE TABLE IF NOT EXISTS salute_giorno")
    assert not re.search(r"\b(DROP|ALTER|UPDATE|DELETE|INSERT)\b", "\n".join(comandi))
    for col in ("giorno", "n_sito", "n_privati", "n_commercianti", "motivi", "azioni", "aggiornato"):
        assert col in sql and col in H.TABELLA_SQL
    import inspect
    ciclo = inspect.getsource(S._ciclo_web)
    assert ciclo.count("salute.esegui()") == 1 and ciclo.index("riepilogo_giorno.esegui()") < ciclo.index("salute.esegui()")
