"""Diagnosi PERCHE_FUORI (dealfinder/pricing/perche_fuori.py) con una connessione finta: regole spente una alla volta,
controllo col salvato, vendute, nessuna scrittura e nessun dato del venditore nel log."""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from dealfinder import regole as RG
from dealfinder.pricing import perche_fuori as PF

QUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORA = datetime.now(timezone.utc)


def _auto(i, prezzo, make="volkswagen", model="golf", fuel="diesel", year=2018, km=90000, **kw):
    d = {"id": i, "source": "subito", "url": f"https://x/{i}", "make": make, "model": model, "version_raw": None,
         "title": f"{make} {model}", "description": "", "year": year, "mileage_km": km, "fuel": fuel,
         "gearbox": "manuale", "power_kw": 81, "price_eur": prezzo, "price_flags": [], "seller_type": "privato",
         "damage_class": "sconosciuto", "damage_items": None, "vehicle_id": 1000 + i, "vehicle_identity": None,
         "stage_reason": None, "telefono_ok": True, "damage_declared": False, "problem_search": False,
         "province": "MI", "status": "attivo", "last_seen_at": ORA - timedelta(hours=3),
         "last_checked_at": ORA - timedelta(hours=3), "first_seen_at": ORA - timedelta(days=3), "disappeared_at": None}
    d.update(kw)
    return d


GOLF = _auto(1, 7000, first_seen_at=ORA - timedelta(days=20))   # niente "Da trattare" (annuncio vecchio)
PANDA = _auto(2, 4000, make="fiat", model="panda", fuel="benzina", year=2016, km=60000)
VENDUTA = _auto(3, 9000, status="scomparso", disappeared_at=ORA - timedelta(days=4))
MERCATO = {("volkswagen", "golf", "diesel"): [_auto(100 + n, p, km=85000 + n * 900) for n, p in enumerate(
    [9000, 9200, 9400, 9500, 9600, 9800, 10000, 10100, 10300, 10500, 10800, 11000])],
           ("fiat", "panda", "benzina"): [_auto(200 + n, p, make="fiat", model="panda", fuel="benzina", year=2016,
                                                km=60000) for n, p in enumerate([3000, 5600, 6000, 6200])]}
# commercianti venduti della Golf a ~8.000 €: la rivendita dei commercianti abbassa R
COMMERCIANTI = [_auto(300 + n, 8000 + 50 * n, km=80000 + 3000 * n, seller_type="commerciante", status="scomparso",
                      first_seen_at=ORA - timedelta(days=40), disappeared_at=ORA - timedelta(days=10 + n),
                      prezzo_iniziale=8000 + 50 * n, riapparso=False) for n in range(6)]
CALIBRA = {"fasce_su": "M", "fasce": {k: {"n": 100, "prezzo_su_M": .95}
                                      for k in ("<5k", "5-10k", "10-20k", "20-35k", "35-60k", ">60k")}}


def _salvata(a, categoria, margine=None, **kw):
    r = {k: a[k] for k in ("id", "make", "model", "fuel", "year", "mileage_km", "price_eur", "seller_type",
                           "first_seen_at", "telefono_ok")}
    r.update(testo=None, categoria=categoria, margine=margine, computed_at=ORA - timedelta(hours=5), m=None, r=None,
             r_finale=None, m_fonte=None, metodo=None, calibrato=None, r_commercianti=None,
             costo_difetti_incluso=False, in_attesa=False, tolti=0, motivo=None)
    r.update(kw)
    return r


ATTIVE = [_salvata(GOLF, "candidato", 1100, m=9897, r=8101, r_finale=8101, m_fonte="annunci", metodo="mediana",
                   r_commercianti=8101, motivo="margine 1100 € sotto la soglia di 1800 €: arriva al minimo con 700 € di "
                                               "sconto"),
          _salvata(PANDA, "dati_insufficienti", motivo="confronti troppo dispersi (2.07×) e nessun valore reale affidabile"),
          # già sul sito (margine sopra la soglia): non si sceglie
          _salvata(_auto(4, 5000), "opportunita", 4000, m=9850, r=9850, r_finale=9850, m_fonte="vendite",
                   metodo="valore_reale")]
TUTTE = {a["id"]: a for a in [GOLF, PANDA, VENDUTA, _auto(4, 5000)]}


class _Ris:
    def __init__(self, righe):
        self.righe = [dict(r) for r in righe]

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Conn:
    def __init__(self):
        self.sql = []
        self.commit_n = 0

    def execute(self, sql, p=None):
        s = " ".join(sql.split())
        self.sql.append(s)
        if "prezzo_iniziale" in s:
            return _Ris(COMMERCIANTI if tuple(p[:3]) == ("volkswagen", "golf", "diesel") else [])
        if "FROM calibrazione" in s:
            return _Ris([{"dati": json.dumps(CALIBRA)}])
        if "JOIN valutazioni_policy v ON" in s:
            assert p["sotto"] == 3000 and "MI" in p["prov"] and "RM" not in p["prov"]
            return _Ris(ATTIVE)
        if "l.status = 'scomparso' AND l.disappeared_at" in s:
            return _Ris([{k: VENDUTA[k] for k in ("id", "make", "model", "fuel", "price_eur")}])
        if s.startswith("SELECT id, make, model, fuel FROM listings WHERE id = ANY"):
            return _Ris([{k: TUTTE[i][k] for k in ("id", "make", "model", "fuel")} for i in p[0] if i in TUTTE])
        if "l.telefono_ok, l.damage_declared" in s and "FROM listings l WHERE l.id = ANY" in s:
            return _Ris([TUTTE[i] for i in p[0] if i in TUTTE])
        if "FROM listings WHERE status='attivo' AND make=%s" in s:
            return _Ris(MERCATO.get(tuple(p), []))
        return _Ris([])                                 # valore reale, stima AI, difetti, ricambi, ai_giudizi

    def commit(self):
        self.commit_n += 1

    def rollback(self):
        pass


class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.righe = []

    def emit(self, record):
        self.righe.append(record.getMessage())


def test_varianti_e_regole_10_11():
    v = {n: r for n, r, _ in PF.varianti()}
    assert v["vigore"] is RG.regole() or v["vigore"] == RG.regole()
    assert not v["rivendita_commercianti"].rivendita.attiva and v["rivendita_commercianti"].allestimento.attivo
    assert not v["calibrazione_sito"].calibrazione.attiva
    assert v["calibrazione_riserva"].calibrazione.riserva == ()
    t = v["regole_10_11"]
    assert not t.rivendita.attiva and not t.allestimento.attivo and not t.calibrazione.interpola
    assert t.correzione.km_min == 0 and not t.correzione.pooling and t.confronti.dispersione_max > 100
    assert t.giudice_ai and t.zona.ripiego_nord and t.calibrazione.attiva          # c'erano già prima del 12/11


def test_giudizio_dall_archivio():
    righe = [{"chiave": "x", "via": {"7": "allestimento diverso", "_giudicati": [7, 8], "_prezzo": 5000}},
             {"chiave": "y", "via": {"_illeggibile": True, "_prezzo": 5000}}]
    via, stato = PF.giudizio_archivio(righe, 5000, [{"id": 7}, {"id": 8}])
    assert via == {7: "allestimento diverso"} and stato == "archivio"
    via, stato = PF.giudizio_archivio(righe, 5000, [{"id": 7}, {"id": 9}])
    assert stato == "parziale" and via == {7: "allestimento diverso"}
    assert PF.giudizio_archivio(righe, 5100, [{"id": 7}])[1] == "assente"        # altro prezzo: non vale


def test_sito_calibra_e_difetto():
    fat = RG.fattori_calibrazione(CALIBRA["fasce"], per_M=True)
    v = {"categoria": "candidato", "M": 10000, "R": 10000, "R_finale": 10000, "M_fonte": "annunci",
         "metodo": "mediana", "margine": 3000, "totale_costi": 200}
    s = PF.sito(v, fat, RG.regole(), difetto=300)
    assert s["R"] == 9500 and s["margine"] == 3000 - 500 - 300
    assert PF.sito(v, fat, RG.con_campi(RG.regole(), {"calibrazione.attiva": False}))["margine"] == 3000
    assert PF.sito(v, fat, RG.regole(), difetto=300, costi=False)["margine"] == 3000 - 500 + 200
    assert PF.sito(dict(v, R_commercianti=8000), fat, RG.regole())["R"] == 8000          # R = min(R, M×f, Rc)


def test_esegui_finta_sola_lettura_e_log_anonimo():
    h = _Log()
    lg = logging.getLogger("perche_fuori")
    lg.addHandler(h)
    try:
        conn = Conn()
        res = PF.esegui(conn, n=10, n_venduti=5, memoria_mb=0)
    finally:
        lg.removeHandler(h)
    att, ven = res["attive"], res["vendute"]
    assert att["auto"] == 2 and att["gruppi"] == {"sotto_soglia": 1, "dati_insufficienti": 1}
    assert att["scelta"]["gia_sul_margine"] == 1
    var = att["varianti"]
    assert var["vigore"]["passano"] == 0
    assert var["rivendita_commercianti"]["in_piu_da_sotto_soglia"] == 1          # Golf: R dei commercianti 8.000
    assert var["regole_10_11"]["in_piu_rispetto_al_vigore"] == 2
    assert var["dispersione"]["escono_da_dati_insufficienti"] == 1               # Panda: 4 gemelle disperse
    assert var["dispersione"]["in_piu_da_dati_insufficienti"] == 1
    assert var["calibrazione_sito"]["passano"] == 0            # Golf: R è già quella dei commercianti nel calcolo
    assert var["giudice_ai"]["passano"] == 0
    assert var["rivendita_commercianti"]["passano_senza_giudizio_archivio"] == 1  # mai giudicata: da confermare
    ctr = res["controllo"]
    assert ctr["uguale"] == 2 and ctr["fattori_calibra"]["<5k"] == .95
    # vendute: la Golf venduta a 9.000 €, R con i commercianti 8.000 (troppo basso), senza ~9.300
    vv = ven["varianti"]
    assert ven["auto"] == 1
    assert vv["vigore"]["tutte_le_sue"]["scostamento_medio_pct"] < -5
    c = vv["rivendita_commercianti"]["cambiate"]
    assert c["n"] == 1 and c["variante"]["errore_medio_eur"] < c["vigore"]["errore_medio_eur"]
    # sola lettura, niente commit, niente dati del venditore nel log
    assert not any(s.split()[0].upper() in ("INSERT", "UPDATE", "DELETE", "CREATE") for s in conn.sql)
    assert conn.commit_n == 0
    righe = [r for r in h.righe if r.startswith("PERCHE_FUORI")]
    tipi = [r.split()[1] for r in righe]
    assert tipi[:2] == ["controllo", "riepilogo_attive"] and tipi[-1] == "riepilogo_vendute"
    esempi = [json.loads(r.split(" ", 2)[2]) for r in righe if r.split()[1] == "esempio"]
    assert 1 <= len(esempi) <= PF.MAX_ESEMPI
    golf = next(e for e in esempi if e["auto"] == "volkswagen golf")
    assert golf["margini"]["rivendita_commercianti"] >= golf["soglia"] > golf["vigore"]["margine"]
    assert golf["margini"]["dispersione"] == golf["vigore"]["margine"]
    testo = "\n".join(righe)
    for vietato in ("https://", "url", "telefono", "\"id\"", "venditore_nome"):
        assert vietato not in testo, vietato


def test_aggancio_una_tantum_in_app():
    src = open(os.path.join(QUI, "dealfinder", "web", "app.py")).read()
    i = src.index('v.startswith("perche_fuori:")')
    blocco = src[i:i + 700]
    assert "_una_volta(v)" in blocco and "_pf.lavoro(v)" in blocco
    pf = open(os.path.join(QUI, "dealfinder", "pricing", "perche_fuori.py")).read()
    assert "READ ONLY" in pf and "from ..ai import client" not in pf and "from ..ai import giudice" not in pf
