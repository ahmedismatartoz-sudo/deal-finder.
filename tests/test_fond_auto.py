"""Ramo fond-auto (16/11): più auto senza cambiare nessuna regola.

- cascata dei confronti (ricalcolo._cascata): auto sparite di recente, poi Nord, poi tutta Italia, SOLO quando i
  confronti non arrivano al minimo del sito; le auto che ci arrivano già hanno la valutazione identica (casi presi
  dalle righe DUMP_CANDIDATI del sito del 16/11: stessi prezzi, anni, km e numero di confronti);
- mai un mercato più alto con la cascata (non gonfiare i prezzi);
- senza chiave AI una valutazione da giudicare resta in attesa;
- SOGLIE_PV dalla variabile: stessa tabella, stessa impronta e stesse soglie del default;
- doppioni Subito/AutoScout24 tolti dall'elenco;
- configurazione del worker solo con riferimenti al sito (nessun valore, nessun segreto);
- recupero delle auto con pochi confronti.
"""
import os
import re
from datetime import datetime, timedelta, timezone

from dealfinder import regole as RG
from dealfinder.pricing import policy_v2 as P
from dealfinder.pricing import ricalcolo as RC

NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
QUI = os.path.dirname(os.path.abspath(__file__))


class env:
    def __init__(self, **kw):
        self.kw, self.prima = kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.prima[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *a):
        for k, v in self.prima.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def auto(i, prezzo, make, model, fuel, anno, km, kw=70, gear="manuale", **extra):
    d = {"id": i, "url": f"https://x/{i}", "source": "subito", "make": make, "model": model, "year": anno,
         "mileage_km": km, "fuel": fuel, "gearbox": gear, "power_kw": kw, "price_eur": prezzo, "price_flags": [],
         "status": "attivo", "seller_type": "privato", "damage_class": "sconosciuto", "vehicle_id": 50000 + i,
         "last_checked_at": NOW - timedelta(days=1), "first_seen_at": NOW - timedelta(days=20)}
    d.update(extra)
    return d


def confronti(t, n, base, passo=.02, inizio=1000, **extra):
    """n confronti uguali all'auto t (stesso motore e cambio, anno uguale, km vicini), prezzi intorno a base."""
    out = []
    for k in range(n):
        p = int(base * (1 + passo * (k - n / 2) / max(1, n)))
        out.append(auto(inizio + k, p, t["make"], t["model"], t["fuel"], t["year"], t["mileage_km"] + 500 * k,
                        kw=t["power_kw"], gear=t["gearbox"], **extra))
    return out


def sparite(t, n, base, inizio=5000):
    out = confronti(t, n, base, inizio=inizio)
    for k, c in enumerate(out):
        c["mileage_km"] = t["mileage_km"] + 7000 + 2000 * k     # auto diverse da quelle attive (non doppioni)
        c.update(status="scomparso", disappeared_at=NOW - timedelta(days=5), source="autoscout24",
                 last_checked_at=NOW - timedelta(days=5), last_seen_at=NOW - timedelta(days=6))
    return out


class Cur:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class ConnCascata:
    """Database finto: registra le query; restituisce le auto sparite, il Nord e tutta Italia se dati."""

    def __init__(self, vendute=(), nord=(), italia=()):
        self.vendute, self.nord, self.italia = list(vendute), list(nord), list(italia)
        self.query = []

    def execute(self, sql, args=()):
        s = " ".join(sql.split())
        self.query.append(s)
        if "status='scomparso'" in s:
            return Cur([dict(m) for m in self.vendute])
        if "province IN (" in s and "status='attivo'" in s:
            return Cur([dict(m) for m in self.nord])
        if "ORDER BY last_seen_at DESC LIMIT" in s:
            return Cur([dict(m) for m in self.italia])
        return Cur([])

    def rollback(self):
        pass

    def commit(self):
        pass


def soglia(p):
    from dealfinder.web import scovo
    return scovo.soglia(p)


# Casi reali (DUMP_CANDIDATI del 16/11, solo dati dell'auto): (descrizione, prezzo, marca, modello, carb, anno, km,
# confronti, mercato M del sito). Tutti con confronti >= minimo del sito (8 sotto 10.000 €, 5 sopra).
CASI_ABBASTANZA = [
    ("Fiat Panda 1.4 metano", 2400, "fiat", "panda", "metano", 2011, 133000, 10, 4532),
    ("BMW X3 2.0 diesel", 12900, "bmw", "x3", "diesel", 2016, 115000, 12, 17472),
    ("Toyota Yaris hybrid", 11200, "toyota", "yaris", "ibrida", 2022, 59500, 5, 13961),
    ("Ford Kuga 2.0 TDCi", 4900, "ford", "kuga", "diesel", 2011, 141550, 8, 6580),
    ("Nissan Qashqai 1.2", 9800, "nissan", "qashqai", "benzina", 2019, 65000, 20, 13814),
]


def test_cascata_non_tocca_le_auto_con_abbastanza_confronti():
    cfg = P.PolicyConfig()
    for i, (nome, prezzo, make, model, fuel, anno, km, n, M) in enumerate(CASI_ABBASTANZA):
        t = auto(1 + i, prezzo, make, model, fuel, anno, km)
        m = confronti(t, n, M)
        prima = P.valuta(t, m, soglia(prezzo), cfg)
        assert RC.n_gate(prima) >= cfg.serve(prezzo), nome
        # anche con auto sparite e altre regioni disponibili, nessuna query e risultato identico
        c = ConnCascata(vendute=sparite(t, 20, M * 1.5), nord=confronti(t, 30, M * .8, inizio=3000))
        v, lista = RC._valuta_base(c, t, m, soglia(prezzo), cfg, {})
        assert v == prima, nome
        assert lista is m
        assert not any("scomparso" in q or "province IN (" in q for q in c.query), nome
        assert "cascata" not in v and "fonte_confronti" not in v


def test_cascata_spenta_come_prima():
    cfg = P.PolicyConfig()
    t = auto(1, 2500, "suzuki", "jimny", "diesel", 2009, 90000)
    m = confronti(t, 4, 8000)
    with env(CASCATA_CONFRONTI="0"):
        v, _ = RC._valuta_base(ConnCascata(vendute=sparite(t, 10, 7000)), t, m, soglia(2500), cfg, {})
    assert v == P.valuta(t, m, soglia(2500), cfg) and "cascata" not in v


def test_cascata_con_le_auto_vendute_arriva_al_minimo_e_lo_dichiara():
    # caso reale: Suzuki Jimny 1.5 DDiS 2009, 90.000 km, 2.500 €; in zona un solo confronto: "dati insufficienti"
    cfg = P.PolicyConfig()
    t = auto(1, 2500, "suzuki", "jimny", "diesel", 2009, 90000)
    m = confronti(t, 1, 4400)
    prima = P.valuta(t, m, soglia(2500), cfg)
    assert prima["categoria"] == "dati_insufficienti" and prima.get("M") is None
    assert RC.n_gate(prima) < cfg.serve(2500)
    c = ConnCascata(vendute=sparite(t, 8, 4300))                  # sparite più economiche dell'attivo
    v, lista = RC._valuta_base(c, t, m, soglia(2500), cfg, {})
    assert v["fonte_confronti"] == "vendute" and v["cascata"]["usata"] == "vendute"
    assert RC.n_gate(v) >= cfg.serve(2500)
    assert v["M"] <= RC.mediana_attivi(prima)                     # mai sopra la mediana degli attivi
    assert any(c.get("venduta") for c in v["confronti"])
    assert any("sparite dagli annunci" in x for x in v["ipotesi"])
    assert len(lista) == 9
    assert v["soglia"] == prima["soglia"] == soglia(2500)         # stesse regole


def test_cascata_sparite_piu_care_non_gonfiano():
    # prova del giudice: Jimny a 3.500 €, 1 confronto attivo a 4.400 €, 8 sparite a 6.000 €. Prima: M 5.985 e
    # "opportunità" con 2.485 € di margine. Ora il mercato non supera la mediana degli attivi: passo non usato
    cfg = P.PolicyConfig()
    t = auto(1, 3500, "suzuki", "jimny", "diesel", 2009, 90000)
    m = confronti(t, 1, 4400)
    prima = P.valuta(t, m, soglia(3500), cfg)
    c = ConnCascata(vendute=sparite(t, 8, 6000))
    v, _ = RC._valuta_base(c, t, m, soglia(3500), cfg, {})
    assert v["categoria"] == "dati_insufficienti" and v.get("M") is None
    assert v["cascata"]["usata"] is None
    assert any(p.get("esito") == "M più alto: non usato" for p in v["cascata"]["passi"])
    assert v == dict(prima, cascata=v["cascata"])


def test_cascata_senza_attivi_le_sole_sparite_non_bastano():
    cfg = P.PolicyConfig()
    t = auto(1, 2500, "suzuki", "jimny", "diesel", 2009, 90000)
    c = ConnCascata(vendute=sparite(t, 10, 4000))
    v, _ = RC._valuta_base(c, t, [], soglia(2500), cfg, {})
    assert v["categoria"] == "dati_insufficienti" and v["cascata"]["usata"] is None
    assert v["cascata"]["passi"][0]["esito"].startswith("nessun confronto attivo")


def test_cascata_non_aggira_le_protezioni():
    # 4 confronti a ~8.000 € e prezzo 2.500 €: fermata dall'anti-truffa (oltre il 45% sotto il mercato), con M.
    # La cascata non cerca confronti più bassi per farla passare
    cfg = P.PolicyConfig()
    t = auto(1, 2500, "suzuki", "jimny", "diesel", 2009, 90000)
    m = confronti(t, 4, 8000)
    prima = P.valuta(t, m, soglia(2500), cfg)
    assert prima["categoria"] == "dati_insufficienti" and prima.get("M")
    c = ConnCascata(vendute=sparite(t, 8, 4000))
    v, _ = RC._valuta_base(c, t, m, soglia(2500), cfg, {})
    assert v == prima and not any("scomparso" in q for q in c.query)
    # auto con danni: niente cascata
    td = dict(auto(2, 2500, "suzuki", "jimny", "diesel", 2009, 90000), damage_class="medio")
    assert not RC.serve_cascata({"categoria": "dati_insufficienti", "n": 1}, td, cfg)


def test_cascata_poi_altre_regioni():
    # 6 confronti in zona molto dispersi (il ripiego Nord di prima scatta solo sotto 5): mercato non stimabile.
    # Con gli annunci del Nord il mercato si stima, sotto la mediana degli attivi di partenza
    cfg = P.PolicyConfig()
    t = auto(1, 4000, "fiat", "500x", "diesel", 2016, 120000)
    m = [dict(x, price_eur=p) for x, p in zip(confronti(t, 6, 6000), (3500, 4200, 5000, 6500, 7800, 9500))]
    prima = P.valuta(t, m, soglia(4000), cfg)
    assert prima["categoria"] == "dati_insufficienti" and prima.get("M") is None
    nord = confronti(t, 9, 5600, inizio=3000)
    for k, x in enumerate(nord):
        x["mileage_km"] += 9000 + 2000 * k
    c = ConnCascata(vendute=[], nord=m + nord)
    v, _ = RC._valuta_base(c, t, m, soglia(4000), cfg, {})
    assert v["fonte_confronti"] == "nord+vendute" and v.get("fuori_zona") is True
    assert RC.n_gate(v) >= cfg.serve(4000) and v["M"] <= RC.mediana_attivi(prima)


def test_cascata_solo_per_dati_insufficienti():
    # un candidato con pochi confronti resta identico: la cascata potrebbe solo abbassarne il mercato
    cfg = P.PolicyConfig()
    t = auto(1, 6500, "fiat", "500x", "diesel", 2016, 120000)
    m = confronti(t, 5, 9000)
    prima = P.valuta(t, m, soglia(6500), cfg)
    assert prima["categoria"] != "dati_insufficienti" and RC.n_gate(prima) < cfg.serve(6500)
    c = ConnCascata(vendute=sparite(t, 8, 8000))
    v, _ = RC._valuta_base(c, t, m, soglia(6500), cfg, {})
    assert v == prima and not any("scomparso" in q for q in c.query)


def test_vendute_mai_tra_i_confronti_senza_la_cascata():
    t = auto(1, 2500, "suzuki", "jimny", "diesel", 2009, 90000)
    sp = sparite(t, 6, 7600)
    for c in sp:
        c.pop("fonte_confronto", None)
    tenuti, esclusi = P.pulisci(t, sp, P.PolicyConfig())
    assert not tenuti and all(e["motivo"] == "annuncio non più online" for e in esclusi)


def test_cascata_dati_mancanti_nessuna_ricerca():
    cfg = P.PolicyConfig()
    t = auto(1, 2500, "suzuki", "jimny", "diesel", None, 90000)
    c = ConnCascata(vendute=sparite(auto(9, 2500, "suzuki", "jimny", "diesel", 2009, 90000), 6, 7600))
    v, _ = RC._valuta_base(c, t, [], soglia(2500), cfg, {})
    assert v["categoria"] == "dati_insufficienti" and not any("scomparso" in q for q in c.query)


# ---------------------------------------------------------------- giudice senza AI

def _v_da_giudicare():
    return {"categoria": "candidato", "confronti": [{"id": 1}], "n": 8, "motivi": []}


def test_senza_ai_la_valutazione_resta_in_attesa():
    from dealfinder.ai import client as AI
    from dealfinder.ai import giudice
    salva = (AI.available, giudice.giudica)
    AI.available = lambda: False
    giudice.giudica = lambda conn, t, conf, solo_archivio=False: None
    try:
        with env(GIUDICE_SENZA_AI_ATTESA="1"):
            v = RC._giudice_ai(None, {"id": 1}, _v_da_giudicare(), [], 1500, P.PolicyConfig())
        assert v["categoria"] == "dati_insufficienti" and v["giudice_mancante"] is True
        with env(GIUDICE_SENZA_AI_ATTESA="0"):
            v = RC._giudice_ai(None, {"id": 1}, _v_da_giudicare(), [], 1500, P.PolicyConfig())
        assert v["categoria"] == "candidato"
        with env(GIUDICE_SENZA_AI_ATTESA=None, RENDER="true"):     # sui servizi Render: acceso di default
            assert RC.attesa_senza_ai() is True
        with env(GIUDICE_SENZA_AI_ATTESA=None, RENDER=None):
            assert RC.attesa_senza_ai() is False
    finally:
        AI.available, giudice.giudica = salva


def test_senza_ai_vale_il_giudizio_archiviato():
    from dealfinder.ai import client as AI
    from dealfinder.ai import giudice
    salva = (AI.available, giudice.giudica)
    chiamate = []
    AI.available = lambda: False

    def finto(conn, t, conf, solo_archivio=False):
        chiamate.append(solo_archivio)
        return {"via": {}, "nota": "archivio"}
    giudice.giudica = finto
    try:
        with env(GIUDICE_SENZA_AI_ATTESA="1"):
            v = RC._giudice_ai(None, {"id": 1}, _v_da_giudicare(), [], 1500, P.PolicyConfig())
        assert chiamate == [True] and v["categoria"] == "candidato" and v["ai_confronti"]["tolti"] == 0
    finally:
        AI.available, giudice.giudica = salva


# ---------------------------------------------------------------- SOGLIE_PV

RENDER_SOGLIE = "7000:1500,10000:1800,15000:2500,20000:3200,30000:4000,1000000000:5000"


def test_soglie_pv_dalla_variabile_uguali_al_default():
    from dealfinder.web import scovo
    default = scovo._soglie_pv("")
    da_env = scovo._soglie_pv(RENDER_SOGLIE)
    assert da_env == default
    assert [x for x in da_env if x[0] == 10**9] == [(10**9, 5000)]           # l'ultima fascia una volta sola
    # senza la fascia "oltre" nella variabile si aggiunge come prima (stessa soglia dell'ultima)
    assert scovo._soglie_pv("7000:1500,10000:1800")[-1] == (10**9, 1800)
    assert scovo._soglie_pv("7000:1500,10000:1800,") == default      # virgola finale: default, come prima


def test_soglie_pv_stessa_impronta_e_stessa_decisione():
    from dealfinder.web import scovo
    salva = scovo.SOGLIE_PV
    try:
        scovo.SOGLIE_PV = scovo._soglie_pv("")
        imp_default = RG.impronta()
        decisioni_default = [scovo.soglia(p) for p in range(0, 80000, 250)]
        scovo.SOGLIE_PV = scovo._soglie_pv(RENDER_SOGLIE)
        assert RG.impronta() == imp_default
        assert [scovo.soglia(p) for p in range(0, 80000, 250)] == decisioni_default
        # la tabella col bug (ultima fascia due volte) dava un'impronta diversa a parità di soglie
        scovo.SOGLIE_PV = scovo._soglie_pv("") + [(10**9, 5000)]
        assert RG.impronta() != imp_default
        assert [scovo.soglia(p) for p in range(0, 80000, 250)] == decisioni_default
    finally:
        scovo.SOGLIE_PV = salva


def test_regole_non_cambiate():
    # nessun valore delle regole cambiato da questo ramo
    assert RG.VERSIONE == "regole-2026-11-12a" and P.POLICY_REV == "2026-11-12a"
    c = RG.Regole().confronti
    assert (c.min_sito, c.min_eco, c.n_minimo, c.anni_tol, c.km_tol, c.freschezza_giorni) == (5, 8, 2, 3, 50000, 10)
    a = RG.Regole().ammissione
    assert (a.km_max, a.anno_min, a.telefono_obbligatorio) == (150000, 2007, True)


# ---------------------------------------------------------------- doppioni Subito / AutoScout24

def _it(i, fonte, prezzo=8000, km=60000, prov="MI", tel=True, foto=5, **kw):
    d = {"id": i, "fonte": fonte, "marca": "Fiat", "modello": "500", "anno": 2018, "km": km, "prezzo": prezzo,
         "n_foto": foto, "zona": f"Milano ({prov})", "pv": {"margine": 2000}, "_r": {"provincia": prov, "tel": tel}}
    d.update(kw)
    return d


def test_doppioni_una_sola_auto_per_coppia():
    from dealfinder.web import scovo
    items = [_it(1, "AutoScout24", tel=False, foto=10), _it(2, "Subito", prezzo=8300, km=61000),
             _it(3, "Subito", prezzo=8000, km=60000, modello="Panda"), _it(4, "AutoScout24", prov="TO"),
             _it(5, "Subito", prov="TO", prezzo=9500)]                 # prezzo oltre il 7%: auto diversa
    out, via = scovo.togli_doppioni(items)
    assert via == {1: 2}                                               # tenuta quella col numero
    assert [it["id"] for it in out] == [2, 3, 4, 5]
    with env(SITO_DOPPIONI="0"):
        assert scovo.togli_doppioni(items)[0] == items


def test_doppioni_regione_e_sito():
    from dealfinder.web import scovo
    a, b = _it(1, "Subito"), _it(2, "AutoScout24")
    assert scovo.stessa_auto_due_siti(a, b)
    assert not scovo.stessa_auto_due_siti(a, _it(6, "AutoScout24", prov="BG"))        # provincia diversa
    assert not scovo.stessa_auto_due_siti(dict(a, carb="diesel"), dict(b, carb="benzina"))
    assert not scovo.stessa_auto_due_siti(dict(a, cambio="manuale"), dict(b, cambio="automatico"))
    assert not scovo.stessa_auto_due_siti(dict(a, kw=51), dict(b, kw=70))
    assert scovo.stessa_auto_due_siti(dict(a, carb="benzina"), b)                     # dato mancante: non separa
    assert not scovo.stessa_auto_due_siti(a, _it(3, "AutoScout24", prov="TO"))
    assert not scovo.stessa_auto_due_siti(a, _it(4, "Subito"))        # stesso sito: non è un doppione di sito
    assert not scovo.stessa_auto_due_siti(a, _it(5, "AutoScout24", km=63000))   # km oltre il 3%
    # a parità di numero vince chi ha più foto
    out, via = scovo.togli_doppioni([_it(1, "Subito", foto=3), _it(2, "AutoScout24", foto=9)])
    assert via == {1: 2}


def test_doppioni_nel_funnel_del_sito():
    from dealfinder.web import sito_regole as SR
    from dealfinder.web import scovo
    items = [_it(1, "AutoScout24", tel=False, foto=10), _it(2, "Subito")]
    fun = SR.Funnel()
    out, via = scovo.togli_doppioni(items)
    fun.passo("doppioni", items, out, {"stessa_auto_altro_sito": len(via)})
    assert fun.passi[-1]["uscite"] == 1


# ---------------------------------------------------------------- worker

def test_worker_solo_riferimenti_al_sito():
    testo = open(os.path.join(QUI, "..", "render-worker.yaml"), encoding="utf-8").read()
    valori = re.findall(r"^\s*value:\s*(.*)$", testo, re.M)
    assert valori and all(v.strip() == '""' for v in valori)          # solo valori VUOTI: nessun segreto
    assert "sync: false" not in testo
    assert re.search(r"^\s*- fromGroup: dealfinder-secrets\s*$", testo, re.M)   # chiave AI e sale dal gruppo
    chiavi = re.findall(r"- key: (\w+)\n\s+fromService: \{ type: (\w+), name: ([\w-]+), envVarKey: (\w+) \}", testo)
    assert chiavi and all(k == ek for k, _, _, ek in chiavi)
    per = {k: (tipo, nome) for k, tipo, nome, _ in chiavi}
    # le variabili del gruppo NON si prendono con fromService (non si risolve: commit 1ce20d2)
    for k in ("ANTHROPIC_API_KEY", "PLATE_SALT", "SECRET_KEY"):
        assert k not in per
    for k in ("TR_SUBITO_SENZA_NUMERO", "LETTORE", "SOGLIE_PV", "POLICY_GIUDICE_AI", "TELEFONO_OBBLIGATORIO",
              "ZONA_SITO", "AI_BUDGET_GIORNO"):
        assert per.get(k) == ("web", "scovo"), k
    assert per["DATABASE_URL"] == ("cron", "dealfinder-opportunita")
    vuote = set(re.findall(r"- key: (\w+)\n\s+value: \"\"", testo))
    assert {"SCRAPER_PROXY", "BRIGHTDATA_API_KEY", "BRIGHTDATA_UNLOCKER_ZONE", "PLATE_LOOKUP_URL"} <= vuote
    assert "TR_SUBITO" not in per


# ---------------------------------------------------------------- recupero

class ConnRecupero:
    def __init__(self, ids, lucchetto=True):
        self.ids, self.lucchetto, self.q = ids, lucchetto, []

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        self.q.append((s, args))
        if "pg_try_advisory_lock" in s:
            return Cur([{"ok": self.lucchetto}])
        if s.startswith("SELECT v.listing_id"):
            return Cur([{"listing_id": i} for i in self.ids[:args["lim"]]])
        return Cur([])

    def rollback(self):
        pass

    def commit(self):
        pass


def _finta_valutazione(chiamate, giudicate=()):
    from dealfinder.ai import giudice
    from dealfinder.pricing import valutazione

    def finta(conn, ids, fonte, **kw):
        chiamate.append((list(ids), fonte))
        for lid in ids:
            if lid in giudicate:
                giudice._esito(lid, "chiamata")
        es = valutazione.Esito()
        es.valutazioni = {i: ({"fonte_confronti": "vendute"} if i == 7 else {}) for i in ids}
        es.salvata = {i: ("candidato" if i == 7 else "dati_insufficienti") for i in ids}
        return es
    return finta


def test_recupero_rivaluta_e_segna_le_auto():
    from dealfinder.pricing import recupero, valutazione
    chiamate = []
    salva = valutazione.valuta_e_scrivi
    valutazione.valuta_e_scrivi = _finta_valutazione(chiamate)
    try:
        c = ConnRecupero([7, 9])
        with env(RECUPERO_PER_GIRO="2"):
            out = recupero.recupera(c)
        assert chiamate == [([7, 9], "cron-recupero")]
        assert out["cascata_usata"] == {"vendute": 1} and out["auto"] == 2
        sel = next(a for q, a in c.q if q.startswith("SELECT v.listing_id"))
        assert sel["lim"] == 2 and sel["ore"] == 24
        q = next(q for q, a in c.q if q.startswith("SELECT v.listing_id"))
        assert "ORDER BY v.computed_at ASC" in q and "recupero_provato" in q
        segnate = [a[0] for q, a in c.q if q.startswith("INSERT INTO recupero_provato")]
        assert segnate == [7, 9]                                      # tentate: non riprese per 24 ore
        assert any("pg_advisory_unlock" in q for q, _ in c.q)
        with env(RECUPERO_CONFRONTI="0"):
            assert recupero.recupera(c) == {"spento": True}
        assert recupero.recupera(ConnRecupero([7], lucchetto=False)) == {"in_corso": True}
    finally:
        valutazione.valuta_e_scrivi = salva


def test_recupero_conta_tutte_le_chiamate_al_giudice():
    from dealfinder.pricing import recupero, valutazione
    chiamate = []
    salva = valutazione.valuta_e_scrivi
    ids = list(range(1000, 1060))
    valutazione.valuta_e_scrivi = _finta_valutazione(chiamate, giudicate=set(ids))
    try:
        with env(RECUPERO_GIUDICE_MAX="25"):
            out = recupero.recupera(ConnRecupero(ids))
        assert out["fermo"] == "tetto_giudice" and out["auto"] == 40 and len(chiamate) == 2
        assert out["chiamate_giudice"] == 40
    finally:
        valutazione.valuta_e_scrivi = salva


def test_migrazione_059():
    testo = open(os.path.join(QUI, "..", "db", "migrations", "059_recupero_confronti.sql"), encoding="utf-8").read()
    assert "CREATE TABLE IF NOT EXISTS recupero_provato" in testo
    assert "WHERE status = 'scomparso'" in testo
