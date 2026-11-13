"""Regole del prezzo vero (4/11): correzione nei due sensi, niente min nascosti, R = V una volta, fascia richiesta
senza prezzi chiesti, 0–1 gemelle solo candidato, margine = R − prezzo − costi (fissi ≤ 200 €), stessa auto stesso
risultato da ogni strada, guardia sulle scritture (19 confronti non sostituiti da 13)."""
import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from dealfinder import regole as RG
from dealfinder.pricing import policy_v2 as P
from dealfinder.pricing import valutazione as VZ

NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)   # 13/11: prima era fissa al 4/11 e dopo 9 giorni le auto risultavano vecchie
CFG = P.PolicyConfig()


def car(i, price, year=2018, km=90000, fuel="diesel", **kw):
    d = {"id": i, "url": f"https://x/{i}", "source": "subito", "make": "volkswagen", "model": "golf", "year": year,
         "mileage_km": km, "fuel": fuel, "gearbox": "manuale", "power_kw": 81, "price_eur": price, "price_flags": [],
         "status": "attivo", "seller_type": "privato", "damage_class": "sconosciuto", "vehicle_id": 1000 + i,
         "last_checked_at": NOW - timedelta(days=1), "first_seen_at": NOW - timedelta(days=30)}
    d.update(kw)
    return d


def gemelle(prezzi, **kw):
    return [car(100 + n, p, km=88000 + n * 400, **kw) for n, p in enumerate(prezzi)]


BASE = [9000, 9200, 9400, 9500, 9600, 9800, 10000, 10100, 10300, 10500]


# ---------------------------------------------------------------- correzione anno/km

def test_correzione_nei_due_sensi_con_tetto():
    import dataclasses
    c0 = RG.regole().correzione
    assert c0.due_sensi is False and c0.tetto == .25          # 4/11 00:40: spenta di base finché non è provata sulle vendute
    c = dataclasses.replace(c0, due_sensi=True)
    f_nuovo = RG.fattore_correzione(1, 0, .08, .012, c)             # confronto 1 anno più nuovo: scende
    f_vecchio = RG.fattore_correzione(-1, 0, .08, .012, c)          # 1 anno più vecchio: SALE
    assert f_nuovo == .92 and abs(f_vecchio - 1 / .92) < 1e-9
    assert RG.fattore_correzione(-10, -5, .08, .012, c) == 1.25     # tetto +25%
    assert RG.fattore_correzione(10, 5, .08, .012, c) == .75        # tetto −25%
    vecchia = dict(c.__dict__, due_sensi=False)
    import dataclasses
    assert RG.fattore_correzione(-1, 0, .08, .012, dataclasses.replace(c, due_sensi=False)) == 1.0
    assert vecchia


def test_gemelle_esatte_secondo_il_modello_m_invariato():
    # 3 gemelle di un anno più vecchie e 3 di un anno più nuove, con prezzi esattamente "giusti": M = prezzo vero
    vero = 10000
    m = [car(200 + k, round(vero / .92), year=2019, km=90000) for k in range(3)] + \
        [car(300 + k, round(vero * .92), year=2017, km=90000) for k in range(3)]
    v = P.valuta(car(1, 6000), m, 1500, P.PolicyConfig(due_sensi=True), NOW)
    assert abs(v["M"] / vero - 1) < .005, v["M"]
    v0 = P.valuta(car(1, 6000), m, 1500, P.PolicyConfig(due_sensi=False), NOW)
    assert v0["M"] < vero * .97                                  # regola di prima: solo verso il basso


# ---------------------------------------------------------------- niente min nascosti

def test_gemelle_piu_care_alzano_piu_basse_abbassano_in_modo_simmetrico():
    v0 = P.valuta(car(1, 6000), gemelle(BASE), 1500, CFG, NOW)
    su = P.valuta(car(1, 6000), gemelle([p * 1.1 for p in BASE]), 1500, CFG, NOW)
    giu = P.valuta(car(1, 6000), gemelle([p / 1.1 for p in BASE]), 1500, CFG, NOW)
    assert abs(su["M"] / v0["M"] - 1.1) < .01 and abs(v0["M"] / giu["M"] - 1.1) < .01


def test_valore_reale_e_gemelle_media_pesata_mai_min_ne_max():
    f = RG.fattore_per(9800, None)
    for k in (.6, .8, 1.25, 1.6):
        vr = {"v": 9000 * k, "sigma": .10, "n": 400, "n_vendute": 60}
        v = P.valuta(car(1, 4000, vr=vr), gemelle(BASE), 1500, CFG, NOW)
        V, G = int(vr["v"]), v["valore_reale"]["gemelle_vendita"]
        if v["vr_accordo"]:
            assert v["M"] == V
        else:
            pesi = v["valore_reale"]["pesi"]
            atteso = math.exp(pesi["V"] * math.log(V) + pesi["gemelle"] * math.log(G))
            assert min(V, G) < v["M"] < max(V, G) and abs(v["M"] / atteso - 1) < .002
        assert v["R"] == v["M"] and f < 1


def test_ai_non_entra_in_m_con_le_gemelle():
    v0 = P.valuta(car(1, 6000), gemelle(BASE), 1500, CFG, NOW)
    for k in (.5, 2.0):
        v = P.valuta(car(1, 6000, ai={"prezzo": v0["M"] * k, "errore": .08, "n": 50}), gemelle(BASE), 1500, CFG, NOW)
        assert v["M"] == v0["M"]


def test_nessun_min_tra_stime_nel_codice():
    import inspect
    src = inspect.getsource(P.valuta)
    assert not re.search(r"\bmin\(\s*M\b|\bmin\(\s*out\[\"M\"\]|M\s*=\s*min\(|M\s*=\s*max\(", src)
    assert "TARATURA" not in src and "riduzione(" not in src and "vendita_veloce" not in src


# ---------------------------------------------------------------- R = V una volta

def test_r_uguale_v_una_volta():
    vr = {"v": 9000, "sigma": .10, "n": 400, "n_vendute": 60}
    v = P.valuta(car(1, 5000, vr=vr), gemelle([p * .98 for p in BASE]), 1500, CFG, NOW)
    assert v["metodo"] == "valore_reale" and v["M"] == v["R"] == v["R_finale"] == 9000 and v["M_fonte"] == "vendite"
    assert v["margine"] == 9000 - 5000 - v["totale_costi"]
    # il sito non la corregge di nuovo (è già un prezzo di vendita)
    assert RG.calibra_vendita(P.riassunto(v), None) == P.riassunto(v)


def test_calibrazione_del_sito_una_volta_e_solo_sui_prezzi_chiesti():
    v = P.valuta(car(1, 5000), gemelle(BASE), 1500, CFG, NOW)
    pv = P.riassunto(v)
    assert pv["M_fonte"] == "annunci"
    pv1 = RG.calibra_vendita(pv, None)
    f = RG.fattore_per(pv["M"], None)
    assert pv1["M"] == round(pv["M"] * f) and pv1["R"] == pv1["M"] and pv1["calibrato"] == round(f, 3)
    assert pv1["margine"] == pv["margine"] - (pv["R"] - pv1["R"])
    assert RG.calibra_vendita(pv1, None) == pv1                  # mai due volte
    vecchia = {"M": 10000, "R": 10000, "margine": 3000, "metodo": "valore_reale_combinato"}
    assert RG.calibra_vendita(vecchia, None) is vecchia          # valutazioni salvate prima: metodo valore reale


def test_oltre_50mila_valutata():
    v = P.valuta(car(1, 45000), gemelle([60000 + n * 400 for n in range(10)]), 4000, CFG, NOW)
    assert v["categoria"] == "opportunita" and v["R"] == v["M"] > 50000


# ---------------------------------------------------------------- fascia richiesta

def test_fascia_richiesta_non_usa_i_prezzi_chiesti():
    # stessa auto e stesso gruppo: benzina (fascia richiesta) e diesel 2016 (fuori fascia) -> stesso M e stessa R
    mb = gemelle(BASE, fuel="benzina")
    md = gemelle(BASE, fuel="diesel")
    tb = car(1, 6000, fuel="benzina", km=80000)
    td = car(1, 6000, fuel="diesel", km=80000)
    assert P.benzina_richiesta(tb) and not P.benzina_richiesta(dict(td, year=2016))
    vb, vd = P.valuta(tb, mb, 1500, CFG, NOW), P.valuta(td, md, 1500, CFG, NOW)
    assert vb["M"] == vd["M"] and vb["R"] == vd["R"]
    # con il valore reale: la fascia richiesta non rimette le gemelle (prezzi chiesti) al posto di V
    vr = {"v": 8500, "sigma": .1, "n": 400, "n_vendute": 50}
    assert P.valuta(dict(tb, vr=vr), mb, 1500, CFG, NOW)["M"] == P.valuta(dict(td, vr=vr), md, 1500, CFG, NOW)["M"]
    # e il sito porta al prezzo di vendita anche la fascia richiesta (nessuna eccezione f = 1, nessuna metà)
    pv = P.riassunto(vb)
    assert RG.calibra_vendita(pv, None)["M"] < pv["M"]


# ---------------------------------------------------------------- 0–1 gemelle

def test_zero_una_gemella_solo_candidato():
    pochi_venduti = {"v": 9000, "sigma": .10, "n": 400, "n_vendute": 10}
    abbastanza = {"v": 9000, "sigma": .10, "n": 400, "n_vendute": 30}
    impreciso = {"v": 9000, "sigma": .25, "n": 400, "n_vendute": 30}
    for mercato in ([], gemelle([9100])):
        assert P.valuta(car(1, 5000, vr=pochi_venduti), mercato, 1500, CFG, NOW)["categoria"] == "dati_insufficienti"
        assert P.valuta(car(1, 5000, vr=impreciso), mercato, 1500, CFG, NOW)["categoria"] == "dati_insufficienti"
        v = P.valuta(car(1, 5000, vr=abbastanza), mercato, 1500, CFG, NOW)
        assert v["categoria"] == "candidato" and v["serve_controllo_ai"] and v["metodo"] == "valore_reale_modello"
        assert v["M"] == v["R"] == 9000
    # con il modello AI (prezzi chiesti) e senza gemelle: mai oltre candidato
    v = P.valuta(car(1, 5000, ai={"prezzo": 9000, "errore": .1, "n": 50}), [], 1500, P.PolicyConfig(ai=True), NOW)
    assert v["categoria"] == "candidato" and v["serve_controllo_ai"]


# ---------------------------------------------------------------- margine e costi

def test_margine_prezzo_annuncio_e_costi_fissi_al_massimo_200():
    cfg = P.PolicyConfig(passaggio=500, pulizia=300)             # anche se qualcuno li alzasse: 200 in tutto
    v = P.valuta(car(1, 6000, parts_cost_high=350, damage_class="leggero"), gemelle(BASE), 1500, cfg, NOW)
    assert v["costi"]["costi_commerciante"]["euro"] == 200 and v["costi"]["ricambi"]["euro"] == 350
    assert v["margine"] == v["R"] - 6000 - 200 - 350               # prezzo dell'annuncio, nessuno sconto di trattativa
    v = P.valuta(car(1, 6000), gemelle(BASE), 1500, CFG, NOW)
    assert v["margine"] == v["R"] - 6000 and v["totale_costi"] == 0
    assert RG.regole().costi.fissi_max == 200


def test_soglia_del_cliente_non_cambiata():
    from dealfinder.web import scovo
    assert scovo.soglia(6999) == 1500 and scovo.soglia(7000) == 1800 and scovo.soglia(30000) == 5000
    v = P.valuta(car(1, 6000), gemelle(BASE), 10**6, CFG, NOW)
    assert v["categoria"] == "non_conveniente" and v["soglia"] == 10**6


# ---------------------------------------------------------------- stessa auto, stesso risultato (DB finto)

class Cur:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class ConnFinta:
    """Database finto: annunci, mercato, valutazioni salvate. Registra le scritture."""

    def __init__(self, auto: dict, mercato: list, salvate: dict | None = None):
        self.auto, self.mercato = auto, mercato
        self.salvate = dict(salvate or {})
        self.scritte = []

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT q.id FROM") or s.startswith("SELECT l.id FROM listings l WHERE l.status='attivo' AND l.seller_type='privato'"):
            return Cur([{"id": self.auto["id"]}] if "eco_max" not in str(args) else [])
        if s.startswith("SET ") or s.startswith("DELETE") or s.startswith("UPDATE") or s.startswith("CREATE"):
            return Cur([])
        if s.startswith("INSERT INTO valutazioni_policy"):
            lid, dati, n = args[0], json.loads(args[5]), args[7]
            es = self.salvate.get(lid)
            ok, _ = VZ.deve_scrivere(es, n, args[8], args[9], ore=args[12]) if len(args) > 12 else (True, "")
            if not ok:
                return Cur([])
            self.scritte.append(dati)
            self.salvate[lid] = {"n": n, "impronta_auto": args[8], "impronta_regole": args[9],
                                 "computed_at": datetime.now(timezone.utc), "categoria": args[2]}
            return Cur([{"listing_id": lid}])
        if s.startswith("INSERT"):
            return Cur([])
        if "FROM valutazioni_policy" in s and "impronta_auto" in s:
            return Cur([dict(v, listing_id=k) for k, v in self.salvate.items() if k in args[0]])
        if "FROM valutazioni_policy" in s:
            return Cur([])
        if s.startswith("SELECT id, make, model, fuel FROM listings"):
            return Cur([{k: self.auto[k] for k in ("id", "make", "model", "fuel")}])
        if "FROM listings l WHERE l.id = ANY" in s:
            return Cur([dict(self.auto)])
        if "FROM listings WHERE status='attivo' AND make=%s" in s:
            return Cur([dict(m) for m in self.mercato])
        if s.startswith("SELECT source FROM listings"):
            return Cur([{"source": self.auto["source"]}])
        if "FROM listings" in s and "field_origin" in s:            # dal_testo: niente da completare
            return Cur([])
        return Cur([])                                             # valore_reale, stima_ai, ai_difetti, valuations...


AUTO = car(1, 6000, title="Golf 1.6 TDI", description="Vendo, chiamare al 333 1234567", telefono_ok=None,
           damage_items=None, vehicle_identity=None, stage_reason="da_verificare", damage_declared=False,
           problem_search=False, province="MI", last_seen_at=NOW, version_raw="1.6 TDI")


def _senza_fonte(d):
    return {k: v for k, v in d.items() if k not in ("fonte_valutazione", "sezione")}


def test_stessa_auto_stesso_risultato_da_ogni_strada():
    from dealfinder.pipeline import tempo_reale as tr
    from dealfinder.pricing import ricalcolo
    from dealfinder.web import scovo
    mercato = gemelle(BASE)
    risultati = {}
    c = ConnFinta(AUTO, mercato)
    tr.valuta_e_pubblica(c, [1], Counter())
    risultati["tempo_reale"] = c.scritte[-1]
    c = ConnFinta(AUTO, mercato)
    ricalcolo.esegui(c)
    risultati["cron_esegui"] = c.scritte[-1]
    import dealfinder.core.dal_testo as dt
    vecchio = dt.completa_db
    dt.completa_db = lambda conn: {}
    try:
        c = ConnFinta(AUTO, mercato)
        ricalcolo.utilitarie(c, pubblica=True)
        risultati["cron_utilitarie"] = c.scritte[-1]
    finally:
        dt.completa_db = vecchio
    c = ConnFinta(AUTO, mercato)
    VZ.valuta_e_scrivi(c, [1], "commercianti")
    risultati["commercianti"] = c.scritte[-1]
    ref = _senza_fonte(risultati["tempo_reale"])
    assert ref["categoria"] in scovo.CATEGORIE_SITO and ref["n"] == len(BASE)
    for nome, d in risultati.items():
        assert _senza_fonte(d) == ref, nome
    assert {d["fonte_valutazione"] for d in risultati.values()} == {"tempo-reale", "cron", "cron-utilitarie", "commercianti"}


def test_telefono_unico():
    assert VZ.telefono({"telefono_ok": True}) is True
    assert VZ.telefono({"telefono_ok": None, "description": "x" * 1500 + " cell 333 1234567"}) is True   # oltre 1.200
    assert VZ.telefono({"telefono_ok": False, "description": "niente"}) is False
    assert VZ.telefono({"telefono_ok": None, "description": "niente"}) is None


def test_esclusa_scritta_cosi_non_resta_una_riga_vecchia():
    senza = dict(AUTO, description="Vendo", telefono_ok=False)
    c = ConnFinta(senza, gemelle(BASE), salvate={1: {"n": 19, "impronta_auto": "vecchia", "impronta_regole": "x",
                                                     "computed_at": NOW, "categoria": "candidato"}})
    es = VZ.valuta_e_scrivi(c, [1], "cron")
    assert c.scritte and c.scritte[-1]["categoria"] == "esclusa" and c.scritte[-1]["motivo_esclusione"] == "senza_numero"
    assert es.salvata[1] == "esclusa"
    for t, motivo in ((dict(AUTO, mileage_km=None), "km_mancanti"), (dict(AUTO, mileage_km=150001), "km_oltre_150000"),
                      (dict(AUTO, year=2006), "anno_prima_del_2007")):
        assert VZ.prepara(t, VZ.ingressi(ConnFinta(t, []), [1]))[1] == motivo


def test_danno_dichiarato_letto_dall_ai_valutato_col_costo():
    t = dict(AUTO, damage_declared=True)
    ing = {"vr": {}, "ai": {}, "parti": {}, "fattori_calibra": None, "difetti": {}}
    assert VZ.prepara(t, ing)[1] == "danni_da_leggere"
    assert VZ.prepara(t, dict(ing, difetti={1: {"grave": True, "costo": 0}}))[1] == "danno_grave"
    tt, motivo = VZ.prepara(t, dict(ing, difetti={1: {"grave": False, "costo": 600, "motivo": "frizione"}}))
    assert motivo is None and tt["costo_difetti"] == 600 and tt["damage_declared"] is False
    v = P.valuta(tt, gemelle(BASE), 1500, CFG, NOW)
    assert v["categoria"] != "dati_insufficienti" and v["margine"] == v["R"] - 6000 - 600


# ---------------------------------------------------------------- guardia sulle scritture

def test_guardia_19_confronti_non_sostituiti_da_13():
    ora = datetime.now(timezone.utc)
    es = {"n": 19, "impronta_auto": "a", "impronta_regole": "r", "computed_at": ora - timedelta(hours=10)}
    assert VZ.deve_scrivere(es, 13, "a", "r", ora) == (False, "meno_confronti")
    assert VZ.deve_scrivere(es, 19, "a", "r", ora)[0] is True
    assert VZ.deve_scrivere(es, 13, "b", "r", ora) == (True, "auto_cambiata")
    assert VZ.deve_scrivere(es, 13, "a", "r2", ora) == (True, "regole_cambiate")
    assert VZ.deve_scrivere(dict(es, computed_at=ora - timedelta(hours=40)), 13, "a", "r", ora) == (True, "vecchia")
    assert VZ.deve_scrivere(None, 0, "a", "r", ora) == (True, "nuova")


def test_guardia_nella_scrittura_vera():
    # riga salvata con 19 confronti, stessa auto e stesse regole: una valutazione con 13 non la sostituisce
    mercato13 = gemelle(BASE + [9900, 10200, 9700])
    t, _ = VZ.prepara(dict(AUTO), {"vr": {}, "ai": {}, "parti": {}, "difetti": {}, "fattori_calibra": None})
    salvata = {1: {"n": 19, "impronta_auto": VZ.impronta_auto(t), "impronta_regole": VZ.impronta_regole(),
                   "computed_at": datetime.now(timezone.utc) - timedelta(hours=2), "categoria": "opportunita"}}
    c = ConnFinta(AUTO, mercato13, salvate=salvata)
    es = VZ.valuta_e_scrivi(c, [1], "web-nuovi")
    assert c.scritte == [] and es.stats["scrittura:meno_confronti"] == 1 and es.salvata[1] == "opportunita"
    # prezzo cambiato: si riscrive
    c = ConnFinta(dict(AUTO, price_eur=5800), mercato13, salvate=salvata)
    VZ.valuta_e_scrivi(c, [1], "web-nuovi")
    assert len(c.scritte) == 1 and c.scritte[0]["n"] == 13


def test_impronta_regole_senza_revisione_del_codice():
    assert P.POLICY_REV not in VZ.impronta_regole() and len(VZ.impronta_regole()) == 12
    import dataclasses
    r2 = dataclasses.replace(RG.regole(), correzione=dataclasses.replace(RG.regole().correzione, tetto=.2))
    assert RG.impronta(r2) != RG.impronta()


def test_nessun_confronto_di_testo_su_policy_rev():
    import pathlib
    radice = pathlib.Path(__file__).resolve().parent.parent / "dealfinder"
    for f in radice.rglob("*.py"):
        testo = f.read_text(encoding="utf-8")
        assert not re.search(r"POLICY_REV\s*[<>=!]=|[<>=!]=\s*_?P?\.?POLICY_REV|policy_rev[^\n]{0,40}(>=|<=|==|<>|!=) *[\"'%]",
                             testo), f.name


def test_prova_correzione_sintetica_gira():
    from dealfinder.pricing import prova_correzione as PC
    r = PC.sintetica(n_gruppi=6)
    a, b = r["regole"]["prima_solo_giu"]["tutte"], r["regole"]["nuova_due_sensi"]["tutte"]
    assert r["usate"] > 50 and b["mdape_tarato_fasce_pct"] <= a["mdape_tarato_fasce_pct"] + .5
    assert b["distorsione_pct"] > a["distorsione_pct"]              # la regola di prima abbassa M per costruzione
