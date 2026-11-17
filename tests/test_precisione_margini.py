"""17/11 notte (cliente: "margini molto precisi"): misura della precisione di M e R sulle auto vendute
(pricing/precisione.py) e campione della calibrazione chiesto → venduto (pricing/calibra.py): casuale, popolazione del
sito, valore reale misurato davvero e corretto solo con uno scostamento dimostrato e solo verso il basso."""
import random

from dealfinder import regole as RG
from dealfinder.pricing import calibra as CB
from dealfinder.pricing import precisione as PR


class _Ris:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return list(self.righe)

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Conn:
    """Connessione finta: risponde per contenuto della query."""

    def __init__(self, vendute=(), vendute_vr=()):
        self.vendute, self.vendute_vr, self.sql, self.salvati = list(vendute), list(vendute_vr), [], []

    def execute(self, s, a=()):
        self.sql.append((s, a))
        if "JOIN valore_reale v" in s:
            return _Ris(self.vendute_vr)
        if "WITH c AS" in s and "md5(l.id::text)" in s:
            return _Ris(self.vendute)
        if s.startswith("INSERT INTO calibrazione"):
            self.salvati.append(a)
        return _Ris([])

    def commit(self):
        pass

    def rollback(self):
        pass


# ------------------------------------------------------------------ strumento di misura

def test_p_segni():
    assert PR.p_segni(10, 10) == 1.0
    assert PR.p_segni(0, 0) == 1.0
    assert PR.p_segni(18, 6) < .05                   # 18 stime sopra su 24: sistematico
    assert PR.p_segni(14, 10) > .4                   # 14 su 24: caso


def test_fattore_gruppo_regole_di_prudenza():
    alte = [(1200, 1000)] * 25                       # stima +20%, dimostrato
    f = PR.fattore_gruppo(alte)
    assert f["applica"] and f["fattore"] == round(1000 / 1200, 3)
    assert not PR.fattore_gruppo(alte[:12])["applica"]                      # campione piccolo
    basse = [(800, 1000)] * 40                       # stima troppo bassa: non si alza mai da qui
    fb = PR.fattore_gruppo(basse)
    assert not fb["applica"] and "non si alza" in fb["motivo"]
    assert not PR.fattore_gruppo([(1050, 1000), (950, 1000)] * 20)["applica"]            # errori simmetrici
    assert PR.fattore_gruppo([(3000, 1000)] * 30)["fattore"] == .80                  # mai sotto il minimo


def test_tabella_prima_dopo_leave_one_out():
    rnd = random.Random(7)
    righe = []
    for i in range(30):                              # gruppo A: stima +20% sistematica
        v = rnd.uniform(2500, 4500)
        righe.append({"g": "A", "vero": v, "m": v * 1.2 * rnd.uniform(.95, 1.05)})
    for i in range(30):                              # gruppo B: errori simmetrici
        v = rnd.uniform(10000, 18000)
        righe.append({"g": "B", "vero": v, "m": v * (1 + (.06 if i % 2 else -.06))})
    for i in range(30):                              # gruppo C: stima troppo bassa (non si corregge all'insù)
        v = rnd.uniform(60000, 90000)
        righe.append({"g": "C", "vero": v, "m": v * .9})
    t = PR.tabella(righe, "m", lambda r: r["g"], ("A", "B", "C"))
    a, b, c = (t["gruppi"][g] for g in "ABC")
    assert a["correzione"]["applica"] and a["prima"]["scostamento_mediano_pct"] > 15
    assert abs(a["dopo"]["scostamento_mediano_pct"]) < 5 and a["dopo"]["entro_10pct_quota"] > 90
    assert b["dopo"] == b["prima"] and not b["correzione"]["applica"]
    assert c["dopo"] == c["prima"] and not c["correzione"]["applica"]          # niente gonfiaggio
    assert t["tutte"]["dopo"]["errore_medio_pct"] < t["tutte"]["prima"]["errore_medio_pct"]
    # nessuna stima "dopo" è più alta di "prima"
    assert all(t["gruppi"][g]["dopo"]["scostamento_medio_pct"] <= t["gruppi"][g]["prima"]["scostamento_medio_pct"]
               for g in "ABC")


# ------------------------------------------------------------------ campione della calibrazione

def test_campione_calibra_casuale_e_popolazione_del_sito():
    s = CB.VENDUTE_SQL
    assert "ORDER BY md5(l.id::text)" in s                    # non più le prime N in ordine di marca
    assert s.rstrip().endswith("ORDER BY l.make, l.model, l.fuel")   # l'ordine per gruppo resta solo per la cache
    assert s.index("LIMIT %(lim)s") < s.index("ORDER BY l.make")      # il limite taglia il campione casuale
    assert "l.seller_type = 'privato'" in s and "%(anno_min)s" in s and "%(km_max)s" in s
    par = CB.parametri_campione()
    a = RG.regole().ammissione
    assert par["anno_min"] == a.anno_min == 2007 and par["km_max"] == a.km_max == 150000
    assert par["gg"] == 7 and par["dal"] == 60
    v = CB.VENDUTE_VR_SQL
    assert "v.computed_at <= l.disappeared_at" in v           # V di quando l'auto era online: niente futuro
    assert "l.seller_type = 'privato'" in v and "%(anno_min)s" in v


def _vendute_vr():
    rnd = random.Random(11)
    out = []
    for _ in range(40):                              # 5-10k: V il 10% sopra il prezzo finale, sistematico
        V = rnd.uniform(6000, 9000)
        out.append({"price_eur": int(V * .9 * rnd.uniform(.97, 1.03)), "v": int(V), "p25": int(V * .9)})
    for i in range(40):                              # 10-20k: errori simmetrici
        V = rnd.uniform(11000, 18000)
        out.append({"price_eur": int(V * (1.05 if i % 2 else .95)), "v": int(V), "p25": int(V * .9)})
    for _ in range(10):                              # <5k: troppo poche
        V = rnd.uniform(3000, 4500)
        out.append({"price_eur": int(V * .7), "v": int(V), "p25": int(V * .8)})
    for _ in range(30):                              # 20-35k: V sotto il prezzo finale (non si alza)
        V = rnd.uniform(22000, 30000)
        out.append({"price_eur": int(V * 1.1), "v": int(V), "p25": int(V * .9)})
    return out


def test_misura_vr_solo_scostamenti_dimostrati_e_verso_il_basso():
    applicate, tutte = CB.misura_vr(Conn(vendute_vr=_vendute_vr()), CB.parametri_campione(), "true")
    assert set(applicate) == {"5-10k"}
    assert .88 <= applicate["5-10k"]["prezzo_su_M"] <= .92 and applicate["5-10k"]["n"] == 40
    assert set(tutte) == {"<5k", "5-10k", "10-20k", "20-35k"}
    assert not tutte["<5k"]["prova"]["applica"] and "piccolo" in tutte["<5k"]["prova"]["motivo"]
    assert not tutte["20-35k"]["prova"]["applica"] and tutte["20-35k"]["prezzo_su_M"] > 1
    # applicazione (regole, invariate): V corretto solo nella fascia misurata, mai sopra V
    fat = RG.fattori_calibrazione({}, misurate_vr=applicate, per_M=True)
    for V in (3000, 7071, 12000, 25000, 50000):
        assert RG.fattore_per(V, fat, vr=True) <= 1.0
    pv = {"M": 7071, "R": 7071, "R_finale": 7071, "margine": 2000, "M_fonte": "vendite", "metodo": "valore_reale"}
    pv2 = RG.calibra_vendita(pv, fat)
    assert pv2["M"] < pv["M"] and pv2["margine"] < pv["margine"] and pv2["calibrato"] <= .92
    assert RG.fattore_per(25000, fat, vr=True) == 1.0                  # 20-35k non misurata come applicabile


def test_misura_completa_con_connessione_finta():
    """Misura intera: solo M dai prezzi chiesti, diagnosi per età/carburante/fonte, campione dichiarato, fasce_vr
    filtrate, risultato salvato."""
    import dealfinder.pricing.ricalcolo as RC
    vendute = [{"id": i, "make": "fiat", "model": "panda", "fuel": "benzina", "price_eur": 3000 + 10 * i,
                "year": 2010 if i % 2 else 2020, "mileage_km": 90000, "source": "subito" if i % 3 else "autoscout24",
                "vehicle_identity": None, "damage_items": None, "damage_class": None} for i in range(30)]

    def finto(conn, t, mercato, soglia, cfg, cache):
        if t["id"] % 5 == 0:                         # M dal valore reale: fuori da questa misura
            return {"M": 4000, "R": 4000, "n": 9, "M_fonte": "vendite", "metodo": "valore_reale"}, None
        return {"M": 4000, "R": 3900, "R_finale": 3900, "n": 9, "M_fonte": "annunci", "metodo": "mediana"}, None
    import os
    orig = RC._valuta_base
    RC._valuta_base = finto
    try:
        conn = Conn(vendute=vendute, vendute_vr=_vendute_vr())
        os.environ["CALIBRA_VR_APPLICA"] = "1"
        out = CB.misura(conn)
        del os.environ["CALIBRA_VR_APPLICA"]
        spento = CB.misura(Conn(vendute=vendute, vendute_vr=_vendute_vr()))
    finally:
        RC._valuta_base = orig
        os.environ.pop("CALIBRA_VR_APPLICA", None)
    # senza interruttore la misura del valore reale resta solo in diagnosi: il sito non cambia per V
    assert spento["fasce_vr"] == {} and "5-10k" in spento["diagnosi"]["valore_reale"]
    assert spento["fasce"] == out["fasce"]
    assert out["vendute_lette"] == 30 and out["valutate"] == 24
    assert out["fasce"]["<5k"]["n"] == 24 and out["fasce_su"] == "M"
    assert set(out["fasce_vr"]) == {"5-10k"} and "20-35k" in out["diagnosi"]["valore_reale"]
    assert set(out["diagnosi"]["eta"]) == {"4-8", "13+"}
    assert set(out["diagnosi"]["fonte"]) == {"subito", "autoscout24"}
    assert out["campione"]["tipo"] == "casuale_popolazione_sito" and out["campione"]["venditore"] == "privato"
    # 17/11 notte (giudice): 24 auto valutate sono sotto CALIBRA_MIN_VALUTATE (150): la misura NON si salva
    assert not conn.salvati and not out["salvata"] and "valutate" in out["non_salvata"]
    assert out["fasce"]["<5k"]["misurato"] < .85 and out["fasce"]["<5k"]["prezzo_su_M"] >= .87 - .05
    # i parametri della popolazione arrivano alla query
    q = [a for s, a in conn.sql if "WITH c AS" in s][0]
    assert q["anno_min"] == 2007 and q["km_max"] == 150000
    # i fattori del sito si leggono dalla misura nuova con le regole di sempre: solo verso il basso
    fat = RG.fattori_calibrazione(out["fasce"], misurate_vr=out["fasce_vr"], per_M=True)
    assert all(x <= 1.0 for _, n, x in fat if not str(n).startswith("_"))


# ------------------------------------------------------------------ correzioni del giudice (17/11 notte)

def _stato(fasce, at=None, fasce_vr=None, **altro):
    from datetime import datetime, timedelta, timezone
    return {"at": at or datetime.now(timezone.utc) - timedelta(days=3),
            "dati": dict({"fasce": fasce, "fasce_su": "M", "fasce_vr": fasce_vr or {}}, **altro)}


def _vigore(stato):
    return CB._in_vigore(stato["dati"] if stato else {}, RG.regole())


def test_ic_mediana():
    xs = [i / 100 for i in range(70, 94)]                    # 24 valori
    lo, hi = PR.ic_mediana(xs)
    assert lo == xs[6] and hi == xs[17]                      # statistiche d'ordine 7 e 18 per n = 24
    assert PR.ic_mediana([]) is None and PR.ic_mediana([.9]) == (.9, .9)


def test_fascia_sotto_n_min_eredita_e_non_alza_mai():
    prima = _stato({"20-35k": {"n": 60, "prezzo_su_M": .93}, "35-60k": {"n": 45, "prezzo_su_M": .95},
                    "<5k": {"n": 30, "prezzo_su_M": .95}})
    nuove = {"20-35k": {"n": 12, "prezzo_su_M": 1.0}, "<5k": {"n": 5, "prezzo_su_M": .99}}   # n < 20
    out = CB.fasce_prudenti(nuove, [prima])
    assert out["20-35k"]["prezzo_su_M"] == .93 and out["20-35k"]["ereditata"] == "precedente"
    assert out["20-35k"]["n_nuovo"] == 12 and out["35-60k"]["ereditata"] == "precedente"
    dopo = CB._in_vigore({"fasce": out, "fasce_su": "M"}, RG.regole())
    assert all(dopo[k] <= v + 1e-9 for k, v in _vigore(prima).items())
    # senza misura prima: il minore tra riserva e ultimo valore noto (righe più vecchie)
    vecchia = _stato({">60k": {"n": 25, "prezzo_su_M": .91}, "<5k": {"n": 25, "prezzo_su_M": .99}})
    out2 = CB.fasce_prudenti({}, [_stato({}), vecchia])
    assert out2[">60k"]["prezzo_su_M"] == .91 and out2[">60k"]["ereditata"] == "ultimo_noto"
    assert "<5k" not in out2                                  # riserva .87 < .99: resta la riserva
    f2 = CB._in_vigore({"fasce": out2, "fasce_su": "M"}, RG.regole())
    assert f2["<5k"] == .87 and f2["20-35k"] == 1.0           # nessuna misura mai: la riserva (come oggi)
    # niente stato: identico a oggi
    assert CB.fasce_prudenti({}, []) == {}


def test_freno_fattore_che_scende():
    prima = _stato({"<5k": {"n": 30, "prezzo_su_M": .95}, "5-10k": {"n": 50, "prezzo_su_M": .92}})
    xs = [.70 + i * .005 for i in range(24)]                  # 24 auto, mediana ~0,76: oltre -10% con poche auto
    d = {"n": 24, "prezzo_su_M": .76, "ic95": [round(xs[6], 3), round(xs[17], 3)]}
    out = CB.fasce_prudenti({"<5k": d}, [prima])
    f = out["<5k"]["prezzo_su_M"]
    assert f == .90 and out["<5k"]["misurato"] == .76 and out["<5k"]["fattore_precedente"] == .95
    assert out["<5k"]["frenata"]
    # IC: limite alto sopra il passo → vale il limite alto (mai sopra il fattore in vigore)
    d2 = {"n": 24, "prezzo_su_M": .88, "ic95": [.84, .93]}
    assert CB.fasce_prudenti({"<5k": d2}, [prima])["<5k"]["prezzo_su_M"] == .93
    d3 = {"n": 24, "prezzo_su_M": .88, "ic95": [.84, .99]}
    assert CB.fasce_prudenti({"<5k": d3}, [prima])["<5k"]["prezzo_su_M"] == .95
    # con 40 auto la misura vale, ma sempre al massimo 0,05 per giro
    d4 = {"n": 45, "prezzo_su_M": .80, "ic95": [.77, .83]}
    assert CB.fasce_prudenti({"<5k": d4}, [prima])["<5k"]["prezzo_su_M"] == .90
    d5 = {"n": 45, "prezzo_su_M": .93, "ic95": [.90, .96]}
    assert CB.fasce_prudenti({"<5k": d5}, [prima])["<5k"]["prezzo_su_M"] == .93     # entro il passo: la misura
    # anche in su al massimo 0,05 per giro
    d6 = {"n": 80, "prezzo_su_M": 1.0, "ic95": [.98, 1.02], "q": [.8, .9, 1.0, 1.1, 1.2]}
    o6 = CB.fasce_prudenti({"5-10k": d6}, [prima])["5-10k"]
    assert o6["prezzo_su_M"] == .97 and o6["q"][2] == .97


class ConnStato(Conn):
    """Connessione finta con lo stato salvato di calibrazione e il mercato degli annunci attivi."""

    def __init__(self, stato=(), mercato=None, errore_stato=False, **kw):
        super().__init__(**kw)
        self.stato, self.mercato, self.errore_stato = list(stato), mercato or {}, errore_stato

    def execute(self, s, a=()):
        if "to_regclass('calibrazione')" in s:
            return _Ris([{"t": "calibrazione"}])
        if "FROM calibrazione ORDER BY at DESC" in s:
            if self.errore_stato:
                raise RuntimeError("lettura fallita")
            return _Ris(self.stato[:1] if "LIMIT 1" in s else self.stato)
        if "status='attivo' AND make=%s AND model=%s AND fuel=%s" in s and "price_eur IS NOT NULL" in s:
            self.sql.append((s, a))
            return _Ris(self.mercato.get(tuple(a), []))
        return super().execute(s, a)


def _gruppo(make, model, fuel, prezzo, n, base_id, venduto=None):
    from datetime import datetime, timedelta, timezone
    ora = datetime.now(timezone.utc)
    att = [{"id": base_id + i, "source": "subito", "url": f"u{base_id + i}", "vehicle_id": None,
            "vehicle_identity": None, "make": make, "model": model, "version_raw": None, "year": 2016,
            "mileage_km": 80000, "fuel": fuel, "gearbox": "manuale", "power_kw": 70,
            "price_eur": int(prezzo * (1 + ((i % 7) - 3) / 100)), "price_flags": None, "seller_type": "privato",
            "damage_class": None, "status": "attivo", "stage_reason": None, "problem_search": None,
            "first_seen_at": ora - timedelta(days=20), "last_seen_at": ora, "last_checked_at": ora,
            "disappeared_at": None, "finanziamento": False} for i in range(25)]
    vend = [dict(att[0], id=base_id + 1000 + i, url=f"v{base_id + i}", status="scomparso",
                 price_eur=int(prezzo * (venduto or .97) * (1 + ((i % 5) - 2) / 100)), damage_items=None,
                 title=f"{make} {model}", description="", telefono_ok=True, disappeared_at=ora,
                 first_seen_at=ora - timedelta(days=3), version_raw=None) for i in range(n)]
    return {(make, model, fuel): att}, vend


def test_misura_reale_fascia_sotto_n_min_non_alza_e_salva_solo_se_solida():
    """_valuta_base VERO (nessuna valutazione finta): 160 vendute tra 5 e 10.000 € e 8 tra 20 e 35.000 € (sotto n_min).
    La fascia 20-35k eredita 0,93 invece di tornare alla riserva 1,0; con poche auto valutate non si salva nulla."""
    m1, v1 = _gruppo("fiat", "panda", "benzina", 7000, 160, 1)
    m2, v2 = _gruppo("bmw", "x3", "diesel", 28000, 8, 5000)
    prima = _stato({"20-35k": {"n": 60, "prezzo_su_M": .93}, "5-10k": {"n": 40, "prezzo_su_M": .95},
                    "<5k": {"n": 30, "prezzo_su_M": .95}})
    conn = ConnStato(stato=[prima], mercato={**m1, **m2}, vendute=v1 + v2)
    out = CB.misura(conn)
    assert out["valutate"] >= 150 and out["salvata"] and conn.salvati
    assert out["fasce_misurate"]["5-10k"]["n"] >= 150
    assert (out["fasce_misurate"].get("20-35k") or {}).get("n", 0) < 20
    assert out["fasce"]["20-35k"]["prezzo_su_M"] == .93 and out["fasce"]["20-35k"]["ereditata"] == "precedente"
    vig, dopo = _vigore(prima), CB._in_vigore(out, RG.regole())
    for nome in ("20-35k", "35-60k", ">60k", "<5k"):
        assert dopo[nome] <= vig[nome] + 1e-9, nome
    for M in (22000, 26458, 30000, 34000):                     # e il fattore applicato al sito non sale
        assert RG.fattore_per(M, RG.fattori_calibrazione(out["fasce"], per_M=True)) <= \
            RG.fattore_per(M, RG.fattori_calibrazione(prima["dati"]["fasce"], per_M=True)) + 1e-9
    # stesso campione ma solo 100 auto valutate: niente salvato
    conn2 = ConnStato(stato=[prima], mercato={**m1, **m2}, vendute=v1[:100] + v2)
    out2 = CB.misura(conn2)
    assert not out2["salvata"] and not conn2.salvati and "valutate" in out2["non_salvata"]


def test_campione_vuoto_o_errore_non_cambia_nulla():
    prima = _stato({"20-35k": {"n": 60, "prezzo_su_M": .93}})
    conn = ConnStato(stato=[prima])                            # nessuna venduta
    out = CB.misura(conn)
    assert not out["salvata"] and not conn.salvati
    m1, v1 = _gruppo("fiat", "panda", "benzina", 7000, 160, 1)
    conn = ConnStato(stato=[prima], mercato=m1, vendute=v1, errore_stato=True)     # stato illeggibile
    out = CB.misura(conn)
    assert not out["salvata"] and not conn.salvati and "stato precedente" in out["non_salvata"]
    from datetime import datetime, timedelta, timezone                             # riavvio: un giro solo
    recente = dict(prima, at=datetime.now(timezone.utc) - timedelta(hours=2))
    conn = ConnStato(stato=[recente], mercato=m1, vendute=v1)
    out = CB.misura(conn)
    assert not out["salvata"] and not conn.salvati and "ore" in out["non_salvata"]
