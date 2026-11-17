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
    assert conn.salvati                                                # risultato salvato in calibrazione
    # i parametri della popolazione arrivano alla query
    q = [a for s, a in conn.sql if "WITH c AS" in s][0]
    assert q["anno_min"] == 2007 and q["km_max"] == 150000
    # i fattori del sito si leggono dalla misura nuova con le regole di sempre: solo verso il basso
    fat = RG.fattori_calibrazione(out["fasce"], misurate_vr=out["fasce_vr"], per_M=True)
    assert all(x <= 1.0 for _, n, x in fat if not str(n).startswith("_"))
