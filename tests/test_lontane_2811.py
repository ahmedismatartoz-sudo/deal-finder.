"""28/11: lontane e Subito 200 km, decisione del cliente (28/11 11:09, "ti autorizzo con il sì").
1. le auto già lette delle province entro 200 km arrivano alla valutazione e al sito (mercato_nord non le segna più
   "solo prezzo"; lavoro rivaluta_200km:<etichetta>), senza cambiare regole di margine;
2. Subito (privati) in tutte le province entro 200 km (tempo reale, lettura completa, lavoro lettura_subito_200km)."""
import inspect
from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace

NUOVE_SUBITO = ["AO", "IM", "MO", "MS", "SP", "SV", "TN", "VI", "VR"]


# ------------------------------------------------------------------ diagnosi: mercato_nord e "solo prezzo"

def test_mercato_nord_non_segna_piu_i_privati_dei_200km():
    from dealfinder.pipeline import collect as C
    for p in ("VR", "VI", "TN", "MO", "SV", "IM", "SP", "AO", "MS", "GE"):
        assert C.nord_proponibile(SimpleNamespace(province=p, seller_type="privato")), p
    # commercianti: restano prezzi di riferimento; fuori dai 200 km (Padova, Bolzano, Udine): come prima
    assert not C.nord_proponibile(SimpleNamespace(province="VR", seller_type="commerciante"))
    for p in ("PD", "BZ", "UD", "VE", None):
        assert not C.nord_proponibile(SimpleNamespace(province=p, seller_type="privato")), p
    src = inspect.getsource(C.run)
    assert 'mode == "mercato_nord" and nord_proponibile(listing)' in src
    assert "stage='mercato', stage_reason='mercato_nord'" in src           # gli altri: come prima


def test_mercato_nord_e_le_promozioni_escludono_i_solo_prezzo():
    """La causa: chi è 'mercato_nord' non va mai sul sito (regola che resta: vale per i commercianti e il fuori zona)."""
    from dealfinder.pricing import ricalcolo as R, valutazione as V
    assert V.NON_PROPOSTE == ("mercato_nord", "fuori_regole")
    assert "NOT (stage = 'mercato'" in inspect.getsource(R.promuovi_utilitarie)
    assert "NOT (stage = 'mercato'" in V.PROMUOVI_SQL
    # le utilitarie usano la stessa promozione e la stessa scelta (estratte senza cambiare nulla)
    src = inspect.getsource(R._utilitarie)
    assert "promuovi_utilitarie(conn, ordine)" in src and "scelte_utilitarie(es)" in src


def test_salva_italia_province_200km_non_sono_solo_prezzo(monkeypatch):
    import dealfinder.core.dal_testo as DT
    import dealfinder.core.normalize as NZ
    import dealfinder.db as DB
    from dealfinder.pricing import confronti_mirati as CM
    monkeypatch.setattr(DT, "completa", lambda d, titolo, testo: {})
    monkeypatch.setattr(NZ, "normalize_fields", lambda l: None)
    n = [0]

    def upsert(conn, l):
        n[0] += 1
        return n[0], "nuovo"
    monkeypatch.setattr(DB, "upsert_listing", upsert)
    segnati = []

    class Conn:
        def execute(self, q, par=None):
            if "stage_reason='mercato_nord'" in q:
                segnati.append(par[0])

        def commit(self):
            pass

        def rollback(self):
            pass

    def ann(prov):
        return SimpleNamespace(make="Fiat", model="Panda", fuel="benzina", year=2018, mileage_km=90000, gearbox=None,
                               power_kw=None, version_raw=None, title="", description="", province=prov, city=None,
                               region=None, price_eur=8000, source="subito", seller_type="privato")
    g = {"make": "Fiat", "model": "Panda", "fuel": "benzina", "zona": "italia", "anno_da": 2016, "anno_a": 2020}
    CM.salva_italia(Conn(), [ann("VR"), ann("SV"), ann("NA"), ann("MI")], g, Counter())
    assert segnati == [3]                     # solo Napoli resta "solo prezzo": Verona e Savona sono del sito


# ------------------------------------------------------------------ rivaluta_200km

class _Conn:
    """Connessione finta: SBLOCCA, IDS e la promozione delle utilitarie."""

    def __init__(self, db):
        self.db = db

    def execute(self, sql, par=None):
        db = self.db
        righe, rc = [], 0
        if sql.startswith("UPDATE listings l SET stage = 'nuovo'"):
            righe = [{"id": i} for i in db["mercato_nord"].get(par["p"], [])]
            db["sbloccati"].extend(par["p"] for _ in righe)
        elif sql.startswith("SELECT l.id, l.make"):
            db["ids_par"].append(par)
            righe = [{"id": i, "make": "fiat", "model": "panda", "fuel": "benzina"}
                     for i in db["auto"].get(par["p"], [])]
        elif "FROM valuations" in sql:
            righe = []
        elif "INSERT INTO valuations" in sql:
            db["valuations"].append(par[0])
        elif sql.startswith("UPDATE listings SET stage='approfondito'"):
            db["promosse"].append(par[1])
            assert "NOT (stage = 'mercato'" in sql
            rc = 1
        return SimpleNamespace(fetchall=lambda: righe, fetchone=lambda: (righe[0] if righe else None), rowcount=rc)

    def commit(self):
        pass

    def rollback(self):
        pass


def _db():
    return {"mercato_nord": {"VR": [11, 12]}, "auto": {"VR": [11, 12, 13], "MO": [21], "MI": [31]},
            "sbloccati": [], "ids_par": [], "valuations": [], "promosse": []}


def _ves_finto(valutate):
    def ves(conn, ids, fonte, pubblica=None, pausa_gruppo=0.0):
        assert fonte == "rivaluta-200km" and pubblica is False
        valutate.extend(ids)
        es = SimpleNamespace(valutazioni={}, salvata={}, telefono={}, stats=Counter())
        for i in ids:
            if i in (11, 21):                 # buone: margine sopra la soglia (soglia di 5.000 € ≤ 1.000 €)
                v = {"categoria": "opportunita", "margine": 4000, "acquisto": 5000, "M": 9000, "R_finale": 9000, "n": 8}
            elif i == 12:
                v = {"categoria": "dati_insufficienti", "margine": None, "acquisto": 5000}
            else:
                v = {"categoria": "non_conveniente", "margine": 100, "acquisto": 5000}
            es.valutazioni[i], es.salvata[i], es.telefono[i] = v, v["categoria"], None
        return es
    return ves


class _Lucchetto:
    def __init__(self, libero=True):
        self.libero, self.preso = libero, 0

    def acquire(self, timeout=None):
        self.preso += 1
        return self.libero

    def release(self):
        self.preso -= 1


def test_rivaluta_200km_province_lontane_prima_e_conteggi(monkeypatch):
    from dealfinder.pricing import rivaluta_200km as R2
    from dealfinder.web import scovo
    from dealfinder.zone import PROVINCE_200KM
    monkeypatch.setattr(scovo, "soglia", lambda p: 1000)
    db, valutate, segnati, finiti = _db(), [], [], []

    @contextmanager
    def connetti():
        yield _Conn(db)
    lk = _Lucchetto()
    es = R2.lavoro("rivaluta_200km:prova", connetti=connetti, ves=_ves_finto(valutate), fatto=lambda k: False,
                   segna=segnati.append, finito=finiti.append, lucchetto=lk)
    assert lk.preso == 0                                           # lucchetto rilasciato
    assert R2.province()[:9] == ["VR", "MO", "VI", "TN", "MS", "SP", "SV", "IM", "AO"]
    assert sorted(R2.province()) == sorted(PROVINCE_200KM) and len(R2.province()) == 33
    assert segnati == [f"prov:{p}" for p in R2.province()] and finiti == ["rivaluta_200km:prova"]
    vr = es["per_provincia"]["VR"]
    assert vr == {"letti": 3, "sbloccati": 2, "valutati": 3, "con_margine": 1, "promosse": 1, "dati_insufficienti": 1}
    assert es["per_provincia"]["MO"]["promosse"] == 1 and es["per_provincia"]["MI"]["con_margine"] == 0
    assert db["promosse"] == [11, 21] and db["valuations"] == [11, 21]
    assert sorted(valutate) == [11, 12, 13, 21, 31]
    # i privati sbloccati si valutano comunque; prezzo fino al tetto del sito, anno e km delle regole di sempre
    p = db["ids_par"][0]
    assert p["p"] == "VR" and p["sbloccati"] == [11, 12] and p["ore"] == 24 and p["hi"] == 200000 and p["lo"] == 500
    from dealfinder.regole import regole
    assert p["anno"] == regole().ammissione.anno_min and p["km"] == regole().ammissione.km_max
    assert es["totale"]["promosse"] == 2 and es["fermata"] is None


def test_rivaluta_200km_sql_solo_privati_e_scarti(monkeypatch):
    from dealfinder.pricing import rivaluta_200km as R2
    for frammento in ("l.seller_type = 'privato'", "l.status = 'attivo'", "= 'mercato_nord'"):
        assert frammento in R2.SBLOCCA_SQL, frammento
    assert "fuori_regole" not in R2.SBLOCCA_SQL                   # fuori km/anno resta solo confronto
    for frammento in ("'doppione','truffa','prezzo_finto','dati_incoerenti'", "analisi_manuale",
                      "l.seller_type = 'privato'", "make_interval(hours => %(ore)s)", "interval '14 days'"):
        assert frammento in R2.IDS_SQL, frammento
    assert R2.leggi_spec("rivaluta_200km:a") == ("a", 24) and R2.leggi_spec("rivaluta_200km:b:6") == ("b", 6)


def test_rivaluta_200km_riprende_e_lucchetto(monkeypatch):
    import pytest
    from dealfinder.pricing import rivaluta_200km as R2
    db = _db()

    @contextmanager
    def connetti():
        yield _Conn(db)
    fatti = {f"prov:{p}" for p in R2.province() if p != "AO"}
    valutate = []
    es = R2.lavoro("rivaluta_200km:r", connetti=connetti, ves=_ves_finto(valutate), fatto=lambda k: k in fatti,
                   segna=lambda k: None, finito=lambda j: None, lucchetto=_Lucchetto())
    assert list(es["per_provincia"]) == ["AO"] and valutate == []
    with pytest.raises(RuntimeError):                               # utilitarie in corso: si riprova al prossimo avvio
        R2.lavoro("rivaluta_200km:r", connetti=connetti, ves=_ves_finto([]), fatto=lambda k: False,
                  segna=lambda k: None, finito=lambda j: None, lucchetto=_Lucchetto(libero=False))


def test_lavori_registrati_solo_nel_sito():
    from dealfinder.pipeline import coda_lavori as CL
    for v in ("rivaluta_200km:", "lettura_subito_200km:"):
        assert v in CL.SOLO_SITO and v in CL.SPOSTABILI and f"{v}a".startswith(CL.SOLO_SITO)
        assert not CL.spostabile(f"{v}a")
    src = inspect.getsource(CL.esegui)
    assert 'v.startswith("rivaluta_200km:")' in src and 'v.startswith("lettura_subito_200km:")' in src
    assert "_lungo(v, \"RIVALUTA_200KM\"" in src and "_lungo(v, \"LETTURA_SUBITO_200KM\"" in src


# ------------------------------------------------------------------ Subito nei 200 km

def test_subito_regioni_coprono_tutte_le_33_province_e_solo_quelle():
    from dealfinder.collectors.subito import PROVINCE_KEYS_NOTE, REGION_CODES
    from dealfinder.pipeline import lettura_completa as LC, tempo_reale as tr
    from dealfinder.zone import MILANO, PROVINCE_200KM, TORINO
    tutte = {p for v in tr.REGIONI.values() for p in v}
    assert set(PROVINCE_200KM) <= tutte
    assert tutte <= set(PROVINCE_200KM) | set(MILANO) | set(TORINO)          # niente Padova, Bolzano, Lucca...
    for reg, provs in tr.REGIONI.items():
        codice = REGION_CODES[reg]
        for p in provs:
            assert p in PROVINCE_KEYS_NOTE[codice], (reg, p)
    assert REGION_CODES["veneto"] == 6 and REGION_CODES["trentino-alto-adige"] == 5
    assert REGION_CODES["valle-d-aosta"] == 1 and REGION_CODES["toscana"] == 9
    assert PROVINCE_KEYS_NOTE[3]["IM"] == "2" and PROVINCE_KEYS_NOTE[8]["MO"] == "4"
    # lettura completa periodica: tutte le 33 province (prima zone.tutte(): Lombardia, Piemonte, PC PR RE, GE)
    assert set(PROVINCE_200KM) <= LC.zona_subito()
    assert {sn for _c, sn, _k in LC.province_subito(LC.zona_subito())} >= set(PROVINCE_200KM)
    # Lombardia resta la prima (tempo massimo unico del giro veloce)
    assert list(tr.REGIONI)[:2] == ["lombardia", "piemonte"]


def test_province_del_lavoro_subito():
    from dealfinder.pipeline import lettura_completa as LC
    from dealfinder.zone import PROVINCE_200KM
    assert sorted(LC.province_subito_200km("lettura_subito_200km:a")) == NUOVE_SUBITO
    assert LC.province_subito_200km("lettura_subito_200km:a:tutte") == list(PROVINCE_200KM)
    assert LC.province_subito_200km("lettura_subito_200km:a:VR,PD,vi") == ["VI", "VR"]
    regs = {r: p for r, _c, p in LC.regioni_subito(NUOVE_SUBITO)}
    assert regs == {"emilia-romagna": ["MO"], "liguria": ["SV", "SP", "IM"], "veneto": ["VR", "VI"],
                    "trentino-alto-adige": ["TN"], "valle-d-aosta": ["AO"], "toscana": ["MS"]}


def _ad(i, prov, key="6", privato=True, prezzo=5000):
    return {"urn": f"id:ad:{i}", "subject": "Fiat Panda", "body": "",
            "geo": {"city": {"short_name": prov, "key": key, "value": prov}},
            "advertiser": {"type": 0 if privato else 1, "company": not privato},
            "features": [{"uri": "/price", "values": [{"key": str(prezzo), "value": f"{prezzo} €"}]}],
            "urls": {"default": f"https://www.subito.it/auto/x-{i}.htm"}}


def test_codice_provincia_errato_si_scopre_alla_prima_pagina(monkeypatch):
    from dealfinder.pipeline import lettura_completa as LC
    pd = [_ad(i, "PD", "2") for i in range(8)]
    vr = [_ad(i, "VR") for i in range(8)]
    assert LC.codice_errato(pd, "VR") and not LC.codice_errato(vr, "VR")
    assert not LC.codice_errato(pd[:3], "VR")                       # pochi annunci: non si decide
    chiamate, segnati = [], []

    class Client:
        def get(self, url, headers=None, params=None):
            chiamate.append(params)
            return SimpleNamespace(status_code=200, json=lambda: {"ads": pd * 13})   # pagina piena, altra provincia
    col = SimpleNamespace(client=Client())
    L = LC.Lettore(lambda ls: 0, dormi=lambda s: None, fatto=lambda k: False, segna=segnati.append)
    LC.subito_fetta(L, col, 6, "6", "VR", 0, 1500, set(), solo_privati=True)
    assert len(chiamate) == 1 and segnati == ["sub:6:VR:0-1500"]


def test_lavoro_subito_200km_legge_solo_le_province_della_lista(monkeypatch):
    from dealfinder.pipeline import lettura_completa as LC, passi, tempo_reale as tr
    monkeypatch.setattr(passi, "fatto", lambda k: False)
    monkeypatch.setattr(passi, "segna", lambda k: None)
    monkeypatch.setattr(passi, "finito", lambda j: None)
    monkeypatch.setattr(tr, "_dormi", lambda s: None)
    conn = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    monkeypatch.setattr(tr, "con_lucchetto", lambda nome, fn, *a: fn(conn) if nome == "lettura" else None)
    salvati = []

    def salva_finto(conn, listings, stats, coda=False):
        for l in listings:
            l._tr_ev = "nuovo"
            stats["salvati"] += 1
            salvati.append((l.province, l.seller_type))
        return []
    monkeypatch.setattr(tr, "_salva", salva_finto)
    richieste = []

    class Client:
        def get(self, url, headers=None, params=None):
            richieste.append(dict(params))
            if "ci" not in params:            # annunci recenti della regione: codici delle province
                ads = [_ad(1, "VR", "6"), _ad(2, "VI", "7"), _ad(3, "PD", "2")]
            elif params["ps"] == 0:
                sn = {"6": "VR", "7": "VI"}[params["ci"]]
                ads = [_ad(10 + int(params["ci"]), sn, params["ci"]), _ad(20 + int(params["ci"]), sn, params["ci"],
                                                                        privato=False)]
            else:
                ads = []
            return SimpleNamespace(status_code=200, json=lambda: {"ads": ads})
    col = SimpleNamespace(client=Client())
    es = LC.lavoro_subito_200km("lettura_subito_200km:t:VR,VI", dormi=lambda s: None, col=col)
    assert es["province"] == ["VI", "VR"] and es["finito"], es
    assert es["codici"]["veneto"] == {"regione": 6, "VR": "6", "VI": "7"}
    assert sorted(salvati) == [("VI", "privato"), ("VR", "privato")]            # solo privati, solo VR e VI
    assert all(r["r"] == 6 for r in richieste) and {r.get("ci") for r in richieste} == {None, "6", "7"}
    assert es["per_provincia"] == {"VR": {"salvati": 1, "nuovi": 1}, "VI": {"salvati": 1, "nuovi": 1}}
    # una lettura per fascia e per provincia (stesse fasce della lettura completa) + 1 pagina per i codici
    assert len(richieste) == 1 + 2 * len(LC.fasce_subito())


def test_lavoro_subito_200km_si_ferma_al_403(monkeypatch):
    from dealfinder.pipeline import lettura_completa as LC, passi, tempo_reale as tr
    monkeypatch.setattr(passi, "fatto", lambda k: False)
    monkeypatch.setattr(passi, "segna", lambda k: None)
    monkeypatch.setattr(tr, "_dormi", lambda s: None)
    monkeypatch.setattr(tr, "con_lucchetto", lambda nome, fn, *a: fn(None))
    richieste = []

    class Client:
        def get(self, url, headers=None, params=None):
            richieste.append(params)
            return SimpleNamespace(status_code=403, json=lambda: {})
    es = LC.lavoro_subito_200km("lettura_subito_200km:b", dormi=lambda s: None, col=SimpleNamespace(client=Client()))
    assert len(richieste) == 1 and es["subito_fermo"].startswith("Blocco") and not es["finito"]


def test_lavoro_subito_200km_lucchetto_occupato(monkeypatch):
    from dealfinder.pipeline import lettura_completa as LC, tempo_reale as tr
    chiamate = []
    monkeypatch.setattr(tr, "con_lucchetto", lambda nome, fn, *a: chiamate.append(nome))
    es = LC.lavoro_subito_200km("lettura_subito_200km:c", dormi=lambda s: None)
    assert "saltato" in es and chiamate == ["lettura"] * LC.LAVORO_200KM_ATTESA_MIN


# ------------------------------------------------------------------ regole invariate

def test_regole_e_mercato_invariati():
    from dealfinder.pricing import valore_base as VB
    from dealfinder.regole import regole
    from dealfinder.web import sito_regole as SR
    a = regole().ammissione
    assert a.km_max == 150_000 and a.anno_min == 2007
    assert SR.Regole().commercianti_totale == 60
    # mercato: le province lontane si confrontano ancora con la zona di Milano (segnalato al cliente)
    for p in ("VR", "MO", "VI", "TN", "SV", "IM", "AO", "SP", "MS"):
        assert VB.zona_di(p) == "milano", p
    assert VB.zone()["milano"] == SR.PROVINCE_100KM


# ------------------------------------------------------------------ Postgres vero (tests/pg_scheda.py): le query

def _pg():
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import pg_scheda as P
        c = P.nuovo_db("lontane2811")
        if c is not None:
            P.migra_tutte(c)
        return c
    except Exception:
        return None


def test_sblocco_selezione_e_lista_del_sito_su_postgres():
    c = _pg()
    if c is None:
        return
    from dealfinder.pricing import ricalcolo as R, rivaluta_200km as R2, valutazione as V
    from dealfinder.web import scovo
    n = [0]

    def auto(prov, venditore="privato", stage="mercato", reason="mercato_nord", prezzo=8000):
        n[0] += 1
        return c.execute(
            "INSERT INTO listings (source, source_id, url, make, model, fuel, year, mileage_km, price_eur, province, "
            "seller_type, status, stage, stage_reason, last_seen_at, last_checked_at) VALUES ('autoscout24', %s, %s, "
            "'fiat', 'panda', 'benzina', 2016, 80000, %s, %s, %s, 'attivo', %s, %s, now(), now()) RETURNING id",
            (f"l{n[0]}", f"https://x/l{n[0]}", prezzo, prov, venditore, stage, reason)).fetchone()["id"]
    vr_nord = auto("VR")
    vr_comm = auto("VR", venditore="commerciante")
    pd_nord = auto("PD")
    vr_fuori_regole = auto("VR", reason="fuori_regole")
    vr_nuovo = auto("VR", stage="nuovo", reason=None)
    vr_fresca = auto("VR", stage="nuovo", reason=None)
    vr_caro = auto("VR", stage="nuovo", reason=None, prezzo=250000)
    assert V._scrivi(c, vr_fresca, {"categoria": "non_conveniente", "n": 9, "M": 8100, "R_finale": 8100, "margine": 100,
                                    "acquisto": 8000, "metodo": "test", "motivi": []}, None, "a", "r", "test", 36)
    sbloccati = [r["id"] for r in c.execute(R2.SBLOCCA_SQL, {"p": "VR"}).fetchall()]
    assert sbloccati == [vr_nord]                            # solo il privato di Verona segnato mercato_nord
    st = {r["id"]: (r["stage"], r["stage_reason"]) for r in c.execute(
        "SELECT id, stage, stage_reason FROM listings").fetchall()}
    assert st[vr_nord] == ("nuovo", None) and st[vr_comm] == ("mercato", "mercato_nord")
    assert st[pd_nord] == ("mercato", "mercato_nord") and st[vr_fuori_regole] == ("mercato", "fuori_regole")
    ids = [r["id"] for r in c.execute(R2.IDS_SQL, {"p": "VR", "lo": 500, "hi": 200000, "anno": 2007, "km": 150000,
                                                   "ore": 24, "sbloccati": sbloccati,
                                                   "buone": sorted(scovo.CATEGORIE_SITO)}).fetchall()]
    assert sorted(ids) == sorted([vr_nord, vr_nuovo])        # la valutata da poco e quella oltre i 200.000 € no
    assert vr_caro not in ids and vr_fresca not in ids
    # promozione delle utilitarie: l'auto sbloccata e buona entra nella lista del sito (scovo.LIST_SQL)
    v = {"categoria": "opportunita", "n": 9, "M": 11000, "R_finale": 11000, "margine": 3000, "acquisto": 8000,
         "metodo": "test", "motivi": []}
    assert V._scrivi(c, vr_nord, v, None, "a", "r", "test", 36)
    n_p, promosse = R.promuovi_utilitarie(c, [(vr_nord, v), (vr_comm, v)])
    assert (n_p, promosse) == (2, 1)                         # il commerciante "solo prezzo" resta fuori
    lista = [r["id"] for r in c.execute(scovo.LIST_SQL, {"me": 0}).fetchall()]
    assert lista == [vr_nord]


def test_lettura_completa_province_nuove_solo_privati(monkeypatch):
    from dealfinder.pipeline import lettura_completa as LC
    from dealfinder.zone import tutte
    visti = []
    monkeypatch.setattr(LC, "subito_fetta", lambda L, col, reg, key, sn, ps, pe, v, sp: visti.append((sn, sp)))
    LC.leggi_subito(None, None, LC.zona_subito(), solo_privati=False, privati_in=LC.zona_subito() - set(tutte()))
    per = {}
    for sn, sp in visti:
        per.setdefault(sn, set()).add(sp)
    assert per["VR"] == {True} and per["AO"] == {True} and per["MI"] == {False} and per["GE"] == {False}
    assert "privati_in=zona_subito() - set(tutte())" in inspect.getsource(LC.giro)
