"""15/11 (ramo piu-auto, studio dei buchi della zona): commercianti della fascia richiesta nel giro della zona,
province del sito sempre nel giro, confronti extra senza letture doppie, utilitarie senza rivalutazioni identiche,
cambi di prezzo con la finestra giusta, Torino senza seconda lettura."""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.pipeline import benzina_mi as B          # noqa: E402
from dealfinder.pipeline import scelta_candidate as SC   # noqa: E402


def _pg(nome):
    try:
        import pg_scheda as P
    except Exception:
        return None, None
    c = P.nuovo_db(nome)
    if c is None:
        return None, None
    P.migra_tutte(c)
    return P, c


# ------------------------------------------------------------------ punto 1: commercianti della fascia richiesta

def test_comm_fascia_parametri_dalle_regole_del_cliente():
    from dealfinder.regole import regole
    a = regole().ammissione
    p = B.comm_fascia_parametri(["MI", "TO", "NO", "PC", "MN", "SO", "CN"], 1000, 0.92, 400000, 300)
    assert p["prov"] == ["MI", "NO", "PC"]                       # solo PROVINCE_100KM: niente Torino, Mantova, Sondrio
    assert p["fascia_prezzo"] == a.richiesta_prezzo_max == 10000 and p["fascia_km"] == a.richiesta_km_max == 100000
    assert p["diesel_dal"] == a.richiesta_diesel_dal == 2017 and p["soglia"] == 0.92
    import re
    for f in ("benzina", "ibrida benzina", "hybrid", "gpl", "metano", "benzina/gpl"):
        assert re.search(p["carb_re"], f)
    assert not re.search(p["carb_re"], "diesel") and not re.search(p["carb_re"], "elettrica")
    assert re.search(p["nomi"], "Autosalone Loconte srl", re.I) and re.search(p["nomi"], "OFFICINE  MULINO", re.I)


def test_comm_fascia_spenta_o_fuori_zona():
    class Mai:
        def execute(self, *a, **k):
            raise AssertionError("nessuna query")
    vecchio = os.environ.get("BENZ_COMM_FASCIA")
    try:
        os.environ["BENZ_COMM_FASCIA"] = "0"
        assert B.candidate_comm_fascia(Mai(), ["MI"], 1000, 0.92, 400000, Counter()) == []
        os.environ["BENZ_COMM_FASCIA"] = "1"
        assert B.candidate_comm_fascia(Mai(), ["TO", "CN"], 1000, 0.92, 400000, Counter()) == []   # nessuna provincia del sito
    finally:
        if vecchio is None:
            os.environ.pop("BENZ_COMM_FASCIA", None)
        else:
            os.environ["BENZ_COMM_FASCIA"] = vecchio


def test_comm_fascia_sql_su_postgres_vero():
    """Solo commercianti in fascia richiesta, province del sito, sotto la soglia della mediana, niente esca/promo/
    Loconte, auto invariate valutate da poco fuori (salta se Postgres non c'è)."""
    P, c = _pg("piu_auto_comm")
    if c is None:
        return
    SC._COL.clear()
    ins = ("INSERT INTO listings (source, source_id, url, make, model, fuel, year, mileage_km, price_eur, province, "
           "seller_type, title, description, telefono_ok, price_flags) VALUES ('autoscout24', %s, %s, 'fiat', 'panda', "
           "%s, %s, %s, %s, %s, %s, %s, %s, true, %s) RETURNING id")

    def nuova(k, prezzo, venditore="commerciante", prov="MI", km=60000, fuel="benzina", anno=2018, titolo="Panda",
              testo="ok", flag=("x",)):
        return c.execute(ins, (k, "u" + k, fuel, anno, km, prezzo, prov, venditore, titolo, testo, list(flag))).fetchone()["id"]
    for i in range(8):                                           # gruppo di confronto: mediana 8.000
        nuova(f"g{i}", 8000, venditore="privato")
        nuova(f"d{i}", 8000, venditore="privato", fuel="diesel")
        nuova(f"v{i}", 8000, venditore="privato", fuel="diesel", anno=2015)
    ids = {
        "ok": nuova("ok", 6500),                                 # entra
        "pc": nuova("pc", 6600, prov="PC"),                      # Piacenza: entra
        "privato": nuova("privato", 6500, venditore="privato"),  # privato: c'è già la scelta dei privati
        "to": nuova("to", 6500, prov="TO"),                      # Torino: non è zona del sito
        "caro": nuova("caro", 7900),                             # sopra il 92% della mediana
        "esca": nuova("esca", 5000),                             # sotto il 70% della mediana: prezzo esca
        "km": nuova("km", 6500, km=120000),                      # fuori fascia (km)
        "promo": nuova("promo", 6500, flag=("leasing_o_rata",)),
        "loconte": nuova("loconte", 6500, titolo="Panda Loconte auto"),
        "diesel17": c.execute(ins, ("d17", "ud17", "diesel", 2018, 60000, 6500, "MI", "commerciante", "Panda", "ok",
                                    ["x"])).fetchone()["id"],
        "diesel15": c.execute(ins, ("d15", "ud15", "diesel", 2015, 60000, 6500, "MI", "commerciante", "Panda", "ok",
                                    ["x"])).fetchone()["id"],
        "invariata": nuova("inv", 6400),
    }
    imp = SC.parametri_invariate()["inv_regole"]
    c.execute("INSERT INTO valutazioni_policy (listing_id, policy_version, categoria, margine, riassunto, dati, "
              "impronta_regole, computed_at) VALUES (%s, 'v', 'non_conveniente', -2000, '{}', '{}', %s, "
              "now() - interval '1 hour')", (ids["invariata"], imp))
    par = dict(B.comm_fascia_parametri(["MI", "PC", "TO", "NO"], 1000, 0.92, 400000, 100), **SC.parametri_invariate())
    righe = c.execute(B.COMM_FASCIA_SQL.format(invariate=SC.condizione_invariate(c)), par).fetchall()
    presi = {r["id"] for r in righe}
    assert presi == {ids["ok"], ids["pc"], ids["diesel17"]}, {k for k, v in ids.items() if v in presi}


# ------------------------------------------------------------------ punto 2: province del sito sempre nel giro

def test_province_zona_sempre_con_quelle_del_sito():
    from dealfinder.pipeline import collect as C
    from dealfinder.web.sito_regole import PROVINCE_100KM
    pv = C.province_zona("MI,MB,BG,BS,CO,CR,LC,LO,MN,PV,SO,VA")      # lista di Render senza Novara e Piacenza
    assert set(PROVINCE_100KM) <= set(pv) and pv[:12] == "MI,MB,BG,BS,CO,CR,LC,LO,MN,PV,SO,VA".split(",")
    assert "MN" in pv and "SO" in pv                                  # le extra già presenti restano
    assert len(pv) == len(set(pv))
    vecchio = os.environ.get("ZC_PROVINCE_SITO")
    try:
        os.environ["ZC_PROVINCE_SITO"] = "0"
        assert C.province_zona("MI,MB") == ["MI", "MB"]
    finally:
        if vecchio is None:
            os.environ.pop("ZC_PROVINCE_SITO", None)
        else:
            os.environ["ZC_PROVINCE_SITO"] = vecchio


def test_sparite_autoscout_sulle_province_del_sito():
    """lavori_zona del giro della zona: il controllo delle AutoScout24 sparite include sempre Novara e Piacenza."""
    import contextlib
    from dealfinder import db as D
    from dealfinder.pipeline import benzina_mi, collect as C, verify as V
    visto = {}

    @contextlib.contextmanager
    def finta():
        yield object()

    def scomparsi(c, fonte, **k):
        visto["zona"] = k.get("zona")
        return Counter()
    prima = (D.connect, D.log_job, V.scomparsi_con_prova, list(benzina_mi.PROVINCE_100))
    try:
        D.connect, D.log_job = finta, (lambda c, n: (lambda ok, st: None))
        V.scomparsi_con_prova = scomparsi
        benzina_mi.PROVINCE_100[:] = ["MI", "MB", "VA"]
        C.lavori_zona({"letti_autoscout": 5000})
        assert {"NO", "PC", "MI", "BS"} <= set(visto["zona"])
        visto.clear()
        C.lavori_zona({"letti_autoscout": 5000}, nome="torino_cron")      # Torino: le sue province, come prima
        assert visto["zona"] == ["MI", "MB", "VA"]
    finally:
        D.connect, D.log_job, V.scomparsi_con_prova = prima[:3]
        benzina_mi.PROVINCE_100[:] = prima[3]


# ------------------------------------------------------------------ punto 5: durata del cron

def _env(**k):
    prima = {n: os.environ.get(n) for n in k}
    for n, v in k.items():
        if v is None:
            os.environ.pop(n, None)
        else:
            os.environ[n] = v
    return prima


def test_torino_dopo_zona_interruttori():
    from dealfinder.pipeline import collect as C
    prima = _env(ZONA_CRON="1", ZONA_TORINO=None, TORINO_DOPO_ZONA=None, ZC_SOLO_VALUTA=None)
    try:
        assert C.torino_dopo_zona()
        os.environ["TORINO_DOPO_ZONA"] = "0"
        assert not C.torino_dopo_zona()
        os.environ["TORINO_DOPO_ZONA"] = "1"
        os.environ["ZONA_TORINO"] = "0"                      # la zona non legge Torino: Torino legge da sé
        assert not C.torino_dopo_zona()
        os.environ.pop("ZONA_TORINO")
        os.environ["ZONA_CRON"] = "0"
        assert not C.torino_dopo_zona()
    finally:
        _env(**prima)


def test_giro_torino_dopo_la_zona_salta_solo_autoscout():
    """Revisione 15/11: dopo la zona Torino salta SOLO AutoScout24; Subito (anche profonda nel completo) resta."""
    from dealfinder.pipeline import benzina_mi, ciclo, collect as C, incrementale as INC
    visto = []

    def giro():
        visto.append({k: os.environ.get(k) for k in ("BENZ_SOLO_VALUTA", "BENZ_SALTA_AUTOSCOUT", "BENZ_INCREMENTALE",
                                                      "BENZ_SUB_PROFONDA", "BENZ_SUB_PAGINE_ALTRE", "BENZ_INCR_NOME")})
        return {"candidate": 3}
    segnati = []
    prima = (benzina_mi.giro, ciclo._due, INC.completo_dovuto, INC.segna_completo, C.lavori_zona)
    env = _env(BENZ_SOLO_VALUTA="0", BENZ_PROVINCE="MI,MB", TORINO_SUB_PROFONDA=None, TORINO_SUB_PAGINE=None)
    try:
        benzina_mi.giro, ciclo._due = giro, (lambda j, h: True)
        INC.completo_dovuto, INC.segna_completo = (lambda *a, **k: True), (lambda *a: segnati.append(a))
        C.lavori_zona = lambda esito, nome="": segnati.append(("lavori", nome))
        C.giro_torino(solo_subito=True)
        v = visto[-1]
        assert v["BENZ_SOLO_VALUTA"] == "0" and v["BENZ_SALTA_AUTOSCOUT"] == "1"
        assert v["BENZ_INCREMENTALE"] == "0" and v["BENZ_SUB_PROFONDA"] == "1" and v["BENZ_SUB_PAGINE_ALTRE"] == "150"
        assert v["BENZ_INCR_NOME"] == "torino_cron"                 # inizio del giro precedente: quello di Torino
        assert segnati[0][0] == "torino_cron" and segnati[1] == ("lavori", "torino_cron")
        assert os.environ["BENZ_SOLO_VALUTA"] == "0" and os.environ["BENZ_PROVINCE"] == "MI,MB"
        assert "BENZ_SALTA_AUTOSCOUT" not in os.environ              # ambiente rimesso
        C.giro_torino()
        assert visto[-1]["BENZ_SALTA_AUTOSCOUT"] == "0"
    finally:
        benzina_mi.giro, ciclo._due, INC.completo_dovuto, INC.segna_completo, C.lavori_zona = prima
        _env(**env)


def test_salta_autoscout_tocca_solo_le_letture_autoscout():
    import inspect
    src = inspect.getsource(B.giro)
    assert src.count("[] if salta_as else bande_parallele(") == 3     # centro, centri extra, focus AutoScout24
    assert "bande_parallele(" not in src.replace("[] if salta_as else bande_parallele(", "").split("def numero")[0].replace(
        "from .lusso import bande_parallele", "")
    assert "[] if solo_valuta else sub.search_recenti(" in src             # Subito recenti: resta
    assert 'if not solo_valuta and os.environ.get("BENZ_SUB_PROFONDA") == "1"' in src   # Subito profonda: resta

def test_confronti_extra_salta_le_citta_gia_lette_dalla_zona():
    import contextlib
    from dealfinder import db as D
    from dealfinder.pipeline import ciclo, confronti_extra as CE
    chieste = []

    class Finta:
        def execute(self, *a, **k):
            raise RuntimeError("nessun database")

    @contextlib.contextmanager
    def finta():
        yield Finta()
    prima = (D.connect, D.db_pieno, ciclo._due)
    env = _env(ZONA_CRON="1", ZONA_TORINO=None, CE_COPERTE=None, CE_CAP=None)
    try:
        D.connect, D.db_pieno = finta, (lambda c: False)
        ciclo._due = lambda job, ore: chieste.append(job) or False
        st = CE.giro()
        assert st["citta_coperte_dalla_zona"] == 3
        assert not {"ce:10121", "ce:28100", "ce:15121"} & set(chieste) and "ce:16121" in chieste and len(chieste) == 19
        os.environ["CE_COPERTE"] = ""                            # tutte di nuovo
        chieste.clear()
        assert "citta_coperte_dalla_zona" not in CE.giro() and len(chieste) == 22
        os.environ.pop("CE_COPERTE")
        os.environ["ZONA_CRON"] = "0"                            # senza giro della zona nessuna città è coperta
        assert CE.coperte_dalla_zona() == set()
    finally:
        D.connect, D.db_pieno, ciclo._due = prima
        _env(**env)


def test_utilitarie_saltano_solo_le_invariate_lontane_su_postgres_vero():
    P, c = _pg("piu_auto_util")
    if c is None:
        return
    from dealfinder.pricing import ricalcolo as R
    SC._COL.clear()
    ins = ("INSERT INTO listings (source, source_id, url, make, model, fuel, year, mileage_km, price_eur, province, "
           "seller_type, title, description, telefono_ok) VALUES ('subito', %s, %s, 'fiat', 'panda', 'benzina', 2015, "
           "80000, %s, 'MI', 'privato', 'Panda', 'ok', true) RETURNING id")
    ids = {k: c.execute(ins, (k, "u" + k, 5000)).fetchone()["id"] for k in ("lontana", "vicina", "vecchia", "cambiata", "mai", "sito")}
    imp = SC.parametri_invariate()["inv_regole"]
    vp = ("INSERT INTO valutazioni_policy (listing_id, policy_version, categoria, margine, riassunto, dati, "
          "impronta_regole, computed_at) VALUES (%s, 'v', %s, %s, '{}', '{}', %s, now() - make_interval(hours => %s))")
    c.execute(vp, (ids["lontana"], "non_conveniente", -2000, imp, 5))     # lontana dalla soglia, valutata 5 ore fa: salta
    c.execute(vp, (ids["vicina"], "non_conveniente", 600, imp, 5))       # vicina alla soglia: si rivaluta
    c.execute(vp, (ids["vecchia"], "non_conveniente", -2000, imp, 40))   # oltre 30 ore: si rivaluta
    c.execute(vp, (ids["cambiata"], "non_conveniente", -2000, imp, 5))
    c.execute(vp, (ids["sito"], "opportunita", 2500, imp, 5))            # sul sito: si rivaluta sempre
    c.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s, 'prezzo', 4800)", (ids["cambiata"],))
    tutte = list(ids.values())
    da_fare, saltate = R.senza_invariate(c, tutte)
    assert saltate == 1 and set(da_fare) == set(tutte) - {ids["lontana"]}
    assert R.senza_invariate(c, tutte, "RIC_INVARIATE_ORE")[1] == 1        # approfondite: 20 ore
    env = _env(UTIL_INVARIATE_ORE="0", RIC_INVARIATE_ORE="4")
    try:
        assert R.senza_invariate(c, tutte) == (tutte, 0)
        assert R.senza_invariate(c, tutte, "RIC_INVARIATE_ORE") == (tutte, 0)   # 5 ore fa > 4: nessuna saltata
    finally:
        _env(**env)


def test_confronti_extra_entro_3_ore_ma_mai_fermi():
    from dealfinder.pipeline import ciclo, collect as C, confronti_extra as CE
    prima = ciclo._due, CE.in_ritardo
    try:
        ciclo._due = lambda job, ore: False                   # girati da poco: solo entro 3 ore dall'avvio
        CE.in_ritardo = lambda: False
        assert C.ore_confronti() == 3.0
        assert C.tempo_ok("confronti", C.ore_confronti(), 0.0, ora=2.5 * 3600)
        assert not C.tempo_ok("confronti", C.ore_confronti(), 0.0, ora=4 * 3600)
        ciclo._due = lambda job, ore: job == "confronti_extra" and ore == 36   # fermi da 36 ore: limite di prima
        assert C.ore_confronti() == 6.0
        ciclo._due = lambda job, ore: False
        CE.in_ritardo = lambda: True                          # una città oltre CE_ORE + 24 ore: limite di prima
        assert C.ore_confronti() == 6.0
    finally:
        ciclo._due, CE.in_ritardo = prima


def test_confronti_extra_in_ritardo_dai_turni_delle_citta():
    from datetime import datetime, timedelta, timezone
    from dealfinder.pipeline import confronti_extra as CE
    ora = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    env = _env(ZONA_CRON="1", ZONA_TORINO=None, CE_COPERTE=None, CE_CAP="16121,17100,10121", CE_ORE=None)
    try:
        recenti = {"16121": ora - timedelta(hours=50), "17100": (ora - timedelta(hours=60)).replace(tzinfo=None)}
        assert not CE.in_ritardo(recenti, ora)                # 10121 coperta dalla zona: non conta
        assert CE.in_ritardo(dict(recenti, **{"17100": ora - timedelta(hours=73)}), ora)   # oltre 48 + 24 ore
        assert CE.in_ritardo({"16121": ora}, ora)             # 17100 mai letta
        os.environ["CE_ORE"] = "100"
        assert not CE.in_ritardo(dict(recenti, **{"17100": ora - timedelta(hours=73)}), ora)
    finally:
        _env(**env)


def test_esegui_salta_le_approfondite_invariate_senza_cambiarle_di_sezione():
    from dealfinder.pricing import ricalcolo as R
    from dealfinder.pricing import valutazione as V

    class Ris:
        def __init__(self, r):
            self.r = r

        def fetchall(self):
            return self.r

    class Conn:
        def execute(self, sql, p=None):
            if "ORDER BY l.first_seen_at DESC LIMIT" in sql:            # sezione economica
                return Ris([{"id": 2}, {"id": 9}])
            if sql.startswith("SELECT q.id FROM ("):                      # approfondite
                return Ris([{"id": 1}, {"id": 2}, {"id": 3}])
            return Ris([])
    visto = {}

    class Es:
        valutazioni, stats = {}, {}

    def finta(conn, ids, fonte, extra=None):
        visto.update(ids=list(ids), extra=extra)
        return Es()
    prima = (R.senza_invariate, V.valuta_e_scrivi)
    try:
        R.senza_invariate = lambda conn, ids, var="": ([i for i in ids if i != 2], 1)
        V.valuta_e_scrivi = finta
        st = R.esegui(Conn())
        assert st["saltate_invariate"] == 1 and st["affari_lette"] == 3
        assert sorted(visto["ids"]) == [1, 3, 9]                          # la 2 non torna come "economica"
        assert visto["extra"][9] == {"sezione": "economiche"} and visto["extra"][1] == {"sezione": "affari"}
    finally:
        R.senza_invariate, V.valuta_e_scrivi = prima


# ------------------------------------------------------------------ punto 6: cambi di prezzo tra un giro e l'altro

def test_finestra_cambi_copre_dal_giro_precedente():
    from datetime import datetime, timedelta, timezone
    ora = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    assert SC.finestra_cambi(8, None, ora) == 8                               # nessun giro precedente: come prima
    assert SC.finestra_cambi(8, ora - timedelta(hours=3), ora) == 8           # giro vicino: almeno 8 ore
    assert SC.finestra_cambi(8, ora - timedelta(hours=10, minutes=20), ora) == 11
    assert SC.finestra_cambi(8, ora - timedelta(days=5), ora) == 48           # tetto
    assert SC.finestra_cambi(8, (ora - timedelta(hours=10)).replace(tzinfo=None), ora) == 11
    env = _env(SCELTA_CAMBI_ADATTIVA="0")
    try:
        assert SC.finestra_cambi(8, ora - timedelta(hours=10), ora) == 8 and not SC.salta_valutate()
    finally:
        _env(**env)


def test_cambi_gia_valutati_dopo_il_cambio_non_prendono_posto_su_postgres_vero():
    P, c = _pg("piu_auto_cambi")
    if c is None:
        return
    ins = ("INSERT INTO listings (source, source_id, url, make, model, fuel, year, mileage_km, price_eur, province, "
           "seller_type, title, description, telefono_ok) VALUES ('subito', %s, %s, 'fiat', 'panda', 'benzina', 2015, "
           "80000, 4000, 'MI', 'privato', 'Panda', 'ok', true) RETURNING id")
    a = c.execute(ins, ("a", "ua")).fetchone()["id"]          # valutata DOPO il ribasso: già fatta
    b = c.execute(ins, ("b", "ub")).fetchone()["id"]          # valutata PRIMA del ribasso: entra
    for i in (a, b):
        c.execute("INSERT INTO price_events (listing_id, event, price_eur, at) VALUES (%s, 'prezzo', 4000, "
                  "now() - interval '10 hours')", (i,))
    vp = ("INSERT INTO valutazioni_policy (listing_id, policy_version, categoria, margine, riassunto, dati, "
          "impronta_regole, computed_at) VALUES (%s, 'v', 'non_conveniente', 0, '{}', '{}', 'x', now() - make_interval(hours => %s))")
    c.execute(vp, (a, 2))
    c.execute(vp, (b, 30))
    par = {"ore": 11, "prov": ["MI"], "pmax": 70000, "kmmax": 150000, "tutti_carb": True, "km_obblig": True,
           "lettura_max": 100, "salta_valutate": True}
    assert [r["id"] for r in c.execute(SC.CAMBI_SQL, par).fetchall()] == [b]
    par["ore"] = 8                                             # finestra fissa di prima: il ribasso di 10 ore fa è perso
    assert c.execute(SC.CAMBI_SQL, par).fetchall() == []
    par.update(ore=11, salta_valutate=False)
    assert {r["id"] for r in c.execute(SC.CAMBI_SQL, par).fetchall()} == {a, b}


# ------------------------------------------------------------------ commercianti esclusi ovunque (revisione 15/11)

ESCLUSI_SI = ["Autosalone LOCONTE srl", "Officine Mulino Milano", "OfficineMulino", "Cosval Auto spa", "cosvalauto",
              "Auto & Auto Varese", "auto e auto varese", "SR Auto", "srauto vende", "venduta da Sr-Auto"]
ESCLUSI_NO = ["Fiat Panda ottime condizioni", "Alfa Romeo Giulia Veloce", "Suzuki Swift Sport", "auto varese centro",
              "garanzia 12 mesi srl auto usate", "Classe A 180 d Automatic", "VW Polo GTI DSG", "Mulino bianco"]


def test_commercianti_esclusi_stessa_lista_per_sito_e_candidate():
    import re
    from dealfinder.web import app, sito_regole as SR
    rx = re.compile(SR.nomi_esclusi_re(""), re.I)
    for t in ESCLUSI_SI:
        assert rx.search(t), t
    for t in ESCLUSI_NO:
        assert not rx.search(t), t
    env = _env(SITO_COMMERCIANTI_NOMI="loconte,Autostar")
    try:
        assert app._commercianti_nomi_re() == SR.nomi_esclusi_re()          # il sito usa la lista unica
        assert B.comm_fascia_parametri(["MI"], 1000, 0.92, 1, 1)["nomi"] == SR.nomi_esclusi_re()
        assert re.search(SR.nomi_esclusi_re(), "Autostar Bergamo", re.I) and re.search(SR.nomi_esclusi_re(), "Loconte", re.I)
    finally:
        _env(**env)


def test_commercianti_esclusi_anche_in_postgres():
    P, c = _pg("piu_auto_nomi")
    if c is None:
        return
    from dealfinder.web import sito_regole as SR
    rx = SR.nomi_esclusi_re("")
    for t in ESCLUSI_SI:
        assert c.execute("SELECT %s ~* %s AS x", (t, rx)).fetchone()["x"] is True, t
    for t in ESCLUSI_NO:
        assert c.execute("SELECT %s ~* %s AS x", (t, rx)).fetchone()["x"] is False, t
