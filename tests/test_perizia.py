"""Perizia AI da commerciante (ai/perizia.py), regole del sito con la perizia e prova alla cieca: senza rete, senza
database (connessione finta), risposte AI finte."""
import copy
from datetime import datetime, timedelta, timezone

from dealfinder.ai import perizia as P
from dealfinder.ai import prova_cieca as PC
from dealfinder.web import scovo
from dealfinder.web import sito_regole as SR

BUONA = {"dati_sufficienti": True, "prezzo_vendita": {"basso": 9000, "medio": 9800, "alto": 10500},
         "acquisto_max": 8000, "giorni_vendita": {"min": 20, "max": 45},
         "rischi": [{"tipo": "km_sospetti", "gravita": "media", "nota": "60.000 km in 12 anni: chiedere i tagliandi"}],
         "giudizio": "affare", "motivazione": "Le due vendute simili sono andate a 9.700-9.900 € in meno di un mese."}


def _err(d, **k):
    try:
        P.valida(d, **k)
    except P.PeriziaNonValida as e:
        return str(e)
    return None


# ------------------------------------------------------------------ schema JSON
def test_schema_valido_normalizzato():
    d = copy.deepcopy(BUONA)
    d["prezzo_vendita"]["medio"] = "9.800 €"
    d["giudizio"] = "Affare"
    p = P.valida(d)
    assert p["prezzo_vendita"] == {"basso": 9000, "medio": 9800, "alto": 10500}
    assert p["giudizio"] == "affare" and p["acquisto_max"] == 8000 and p["rischi"][0]["tipo"] == "km_sospetti"
    d["giudizio"] = "nella_media"
    assert P.valida(d)["giudizio"] == "nella media"


def test_schema_rifiuta_risposte_sbagliate():
    assert "mancanti" in _err({k: v for k, v in BUONA.items() if k != "motivazione"})
    assert _err(dict(BUONA, giudizio="ottimo")).startswith("giudizio")
    assert "incoerente" in _err(dict(BUONA, prezzo_vendita={"basso": 11000, "medio": 9800, "alto": 10500}))
    assert "larga" in _err(dict(BUONA, prezzo_vendita={"basso": 3000, "medio": 9800, "alto": 10500}))
    assert "acquisto_max" in _err(dict(BUONA, acquisto_max=12000))
    assert "rischio" in _err(dict(BUONA, rischi=[{"tipo": "colore", "gravita": "alta", "nota": "x"}]))
    assert _err(dict(BUONA, dati_sufficienti="si")) is not None
    assert _err(dict(BUONA, prezzo_vendita={"basso": True, "medio": 9800, "alto": 10500})) is not None
    assert _err(dict(BUONA, giorni_vendita={"min": 50, "max": 10})) is not None
    assert _err(dict(BUONA, motivazione="  ")) is not None
    assert _err([BUONA]) is not None
    assert "forbice mancante" in _err(dict(BUONA, prezzo_vendita=None))


def test_dati_insufficienti_mai_giudizio_netto():
    p = P.valida(dict(BUONA, dati_sufficienti=False, prezzo_vendita=None, acquisto_max=None, giudizio="affare"))
    assert p["giudizio"] == "da verificare" and p["prezzo_vendita"] is None
    assert P.valida(BUONA, cieca=True)["giudizio"] == "da verificare"


def test_niente_numeri_di_telefono_nelle_note():
    d = dict(BUONA, motivazione="Chiamare il 333 1234567 prima", rischi=[])
    assert "1234567" not in P.valida(d)["motivazione"]


# ------------------------------------------------------------------ richiesta
AUTO = {"id": 1, "title": "Golf 1.6 TDI", "description": "Tagliandata, unico proprietario. Prezzo 8.500 euro, tel 333 1234567",
        "make": "volkswagen", "model": "golf", "version_raw": "1.6 TDI Comfortline", "year": 2016, "mileage_km": 98000,
        "fuel": "diesel", "gearbox": "manuale", "power_kw": 85, "price_eur": 8500, "seller_type": "privato"}


def _confronti(n=20):
    return [{"id": 100 + i, "prezzo": 9000 + 100 * i, "anno": 2016 + (i % 3) - 1, "km": 90000 + 3000 * i,
             "versione": "1.6 TDI Comfortline" if i % 2 else "2.0 GTD", "cambio": "manuale", "kw": 85 if i % 2 else 135,
             "venditore": "privato"} for i in range(n)]


def test_confronti_piu_simili_al_massimo_15_mai_se_stessa():
    conf = _confronti(20) + [{"id": 1, "prezzo": 1, "anno": 2016, "km": 98000}]
    sc = P.scegli_confronti(AUTO, conf)
    assert len(sc) == 15 and all(c["id"] != 1 for c in sc)
    assert sc[0]["kw"] == 85 and sc[0]["anno"] == 2016             # prima le stesse versioni, anno e km vicini


def test_testo_richiesta_e_prova_alla_cieca():
    conf = [dict(c, stato="venduto" if c["id"] % 2 else "attivo", giorni=12) for c in _confronti(3)]
    t = P.testo_richiesta(AUTO, conf)
    assert "8.500 €" in t and "1234567" not in t and "sparito (probabilmente venduto) dopo 12 giorni" in t
    assert "attivo da 12 giorni" in t
    tc = P.testo_richiesta(AUTO, conf, cieca=True)
    assert "8.500" not in tc and "NON INDICATO" in tc and "[prezzo]" in tc
    assert "98000" in tc                                         # i km restano


def test_perizia_con_risposta_finta():
    visti = {}

    def finta(sistema, testo, listing_id, sink):
        visti.update(sistema=sistema, testo=testo, id=listing_id)
        return copy.deepcopy(BUONA), 1200, 300
    p = P.perizia(AUTO, _confronti(5), chiama=finta)
    assert p["giudizio"] == "affare" and p["_token"] == (1200, 300) and visti["id"] == 1
    assert "Non inventare" in visti["sistema"] and "CONFRONTI (5" in visti["testo"]
    assert P.per_sito(p) == {"giudizio": "affare", "medio": 9800, "basso": 9000, "alto": 10500}


class ErroreAPI(Exception):
    def __init__(self, status, messaggio="", tipo="invalid_request_error"):
        super().__init__(messaggio)
        self.status_code = status
        self.body = {"type": "error", "error": {"type": tipo, "message": messaggio}}


class RateLimitError(Exception):
    pass


def test_tipo_errore_da_stato_e_classe():
    assert P.tipo_errore(ErroreAPI(402, "payment required")) == "credito"
    assert P.tipo_errore(ErroreAPI(400, "Your credit balance is too low to access the Anthropic API")) == "credito"
    assert P.tipo_errore(ErroreAPI(429, "rate_limit_error")) == "limite"
    assert P.tipo_errore(ErroreAPI(529, "Overloaded", "overloaded_error")) == "limite"
    assert P.tipo_errore(RateLimitError("x")) == "limite"
    assert P.tipo_errore(ErroreAPI(400, "max_tokens: 429 non valido")) is None       # niente sottostringhe
    assert P.tipo_errore(RuntimeError("credit 402 429 nel testo")) is None
    assert P.pausa_per(ErroreAPI(402)) == 3600 and P.pausa_per(ErroreAPI(429)) == 300


# ------------------------------------------------------------------ regole del sito
G = SR.Regole(perizia=True)


def auto(i, prezzo=5000, margine=3000, M=8000, jev=None, perizia=None, **kw):
    r = {"tel": True, "seller_type": "privato", "provincia": "MI", "jev": jev, "perizia": perizia}
    it = {"id": i, "prezzo": prezzo, "anno": 2015, "km": 90000, "carb": "Diesel", "fonte": "Subito",
          "pv": {"categoria": "opportunita", "M": M, "R": prezzo + margine + 200, "R_finale": prezzo + margine + 200,
                 "margine": margine, "totale_costi": 200, "n": 10}, "_r": r}
    it.update(kw)
    return it


def elenco(items, g=G):
    out, fun = SR.elenco_sito(items, g, scrivi_log=False)
    return {it["id"]: it for it in out}, fun


class Env:
    """Variabili d'ambiente cambiate solo dentro il blocco (run_tests.py non ha monkeypatch)."""
    def __init__(self, **kv):
        self.kv, self.prima = kv, {}

    def __enter__(self):
        import os
        for k, v in self.kv.items():
            self.prima[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *a):
        import os
        for k, v in self.prima.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_perizia_spenta_nei_test_ombra_di_default_applicata_con_1():
    assert SR.Regole().perizia is False and SR.Regole().perizia_ombra is False
    with Env(PERIZIA_AI=None):
        g = SR.Regole.da_env()
        assert g.perizia is False and g.perizia_ombra is True and P.modo() == "log" and P.attiva()
    with Env(PERIZIA_AI="1"):
        assert SR.Regole.da_env().perizia is True and P.modo() == "1"
    with Env(PERIZIA_AI="0"):
        g = SR.Regole.da_env()
        assert g.perizia is False and g.perizia_ombra is False and not P.attiva()
    out, _ = elenco([auto(1)], SR.Regole())
    assert 1 in out                                            # spenta: come prima


def test_senza_perizia_e_senza_jev_in_attesa():
    items = [auto(1), auto(2, jev={"truffa": 0.1}), auto(3, perizia={"giudizio": "nella media", "medio": 8300})]
    out, fun = elenco(items)
    assert set(out) == {2, 3}
    assert fun.tolti["perizia"] == [1] and items[0]["_perizia_attesa"] is True
    passo = next(p for p in fun.passi if p["passo"] == "perizia")
    assert passo["motivi"] == {"aspetta_perizia": 1}


def test_caro_fuori():
    out, fun = elenco([auto(1, jev={"truffa": 0.1}, perizia={"giudizio": "caro", "medio": 8300})])
    assert out == {} and next(p for p in fun.passi if p["passo"] == "perizia")["motivi"] == {"perizia_caro": 1}


def test_caro_non_toglie_le_auto_da_trattare():
    from datetime import datetime, timezone
    s = scovo.soglia(6000)
    it = auto(1, prezzo=6000, margine=s - 300, M=9000, carb="Benzina", anno=2016, km=80000,
              visto_dal=datetime.now(timezone.utc).isoformat(), jev={"truffa": 0.1},
              perizia={"giudizio": "caro", "medio": 9300})
    out, _ = elenco([it])
    assert 1 in out and out[1]["trattare"]


def test_fase_ombra_conta_e_non_cambia_niente():
    g = SR.Regole(perizia_ombra=True)
    s = scovo.soglia(5000)
    items = [auto(1), auto(2, jev={"truffa": 0.1}, perizia={"giudizio": "caro", "medio": 8300}),
             auto(3, perizia={"giudizio": "nella media", "medio": 7000}),
             auto(4, margine=s + 100, perizia={"giudizio": "affare", "medio": 5000})]
    out, fun = elenco(items, g)
    assert set(out) == {1, 2, 3, 4}
    assert out[3]["pv"]["margine"] == 3000 and out[3]["avviso_verifica"] is False and "R_perizia" not in out[3]["pv"]
    o = fun.perizia_ombra
    assert (o["sarebbero_caro"], o["sarebbero_attesa"], o["sarebbero_prudenti"], o["sarebbero_sotto_minimo"]) == (1, 1, 2, 1)
    assert out[1].get("_perizia_attesa") is True                  # il giro la prende per prima anche in ombra
    assert not any(p["passo"] == "perizia" and p["uscite"] for p in fun.passi)


def test_dati_insufficienti_non_tagliano_il_margine():
    p = P.valida(dict(BUONA, dati_sufficienti=False, acquisto_max=None))
    assert P.per_sito(p)["medio"] is None
    conn = ConnFinta()
    P.salva(conn, {"id": 1, "price_eur": 5000}, p)
    assert conn.salvate[0][3:6] == (None, None, None)              # medio, basso, alto NULL
    out, _ = elenco([auto(1, perizia=P.per_sito(p))])
    assert out[1]["pv"]["margine"] == 3000


def test_caro_incoerente_diventa_nella_media():
    assert P.valida(dict(BUONA, giudizio="caro", acquisto_max=8000), prezzo=7900)["giudizio"] == "nella media"
    assert P.valida(dict(BUONA, giudizio="caro", acquisto_max=None), prezzo=8500)["giudizio"] == "nella media"  # sotto il basso
    assert P.valida(dict(BUONA, giudizio="caro", acquisto_max=8000), prezzo=9500)["giudizio"] == "caro"


def test_prompt_senza_soglie_di_guadagno():
    for x in ("400-1.500", "10-15%", "1.000-1.500"):
        assert x not in P.SISTEMA
    assert "probabilmente venduto" in P.SISTEMA and "margine minimo li applica il nostro sito" in P.SISTEMA


def test_stima_molto_sotto_R_margine_prudente_e_avviso():
    # R = 8.200; stima del perito 7.000 (−14,6%): margine 3.000 → 1.800, avviso
    it = auto(1, perizia={"giudizio": "nella media", "medio": 7000})
    out, _ = elenco([it])
    pv = out[1]["pv"]
    assert pv["margine"] == 1800 and pv["R_finale"] == 7000 and pv["R_calcolo"] == 8200 and pv["perizia_giu"] == 1200
    assert out[1]["avviso_verifica"] is True
    # entro il 10% (7.500 contro 8.200 = −8,5%): niente cambia
    out, _ = elenco([auto(2, perizia={"giudizio": "nella media", "medio": 7500})])
    assert out[2]["pv"]["margine"] == 3000 and out[2]["avviso_verifica"] is False


def test_margine_prudente_sotto_il_minimo_esce():
    s = scovo.soglia(5000)
    it = auto(1, margine=s + 300, perizia={"giudizio": "affare", "medio": 5000})
    out, fun = elenco([it])
    assert out == {} and 1 in fun.tolti["margine_minimo"]
    # nessuna auto sul sito sotto il minimo, anche con la perizia
    items = [auto(k, margine=s + 50 * k, perizia={"giudizio": "affare", "medio": 5000 + s + 200}) for k in range(1, 30)]
    for x in elenco(items)[0].values():
        assert x["pv"]["margine"] >= s


def test_da_verificare_entra_con_avviso_e_supercar_escluse():
    out, _ = elenco([auto(1, perizia={"giudizio": "da verificare", "medio": None})])
    assert out[1]["avviso_verifica"] is True and out[1]["pv"]["margine"] == 3000
    sc = auto(5, prezzo=100000, margine=30000, M=140000, supercar=True)
    sc["_r"]["sc_ok"] = True
    assert SR.r_perizia(sc, G) is None and SR.t_perizia(sc, G) is None


# ------------------------------------------------------------------ connessione finta
class Risultato:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class ConnFinta:
    def __init__(self, auto=None, listings=None, autohero=None, fatte=0):
        self.auto, self.listings, self.autohero, self.fatte = auto or [], listings or {}, autohero or [], fatte
        self.sql = []
        self.salvate = []
        self.usage = []
        self.commit_n = self.rollback_n = 0

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.sql.append(s)
        if s.startswith("INSERT INTO ai_usage"):
            self.usage.append(params)
            return Risultato([])
        if s.startswith("INSERT INTO perizia_ai"):
            self.salvate.append(params)
            return Risultato([])
        if "count(*) AS n FROM ai_usage" in s:
            return Risultato([{"n": self.fatte}])
        if "FROM ai_usage" in s:
            return Risultato([])
        if "to_regclass" in s:
            return Risultato([{"t": "x"}])
        if "FROM autohero_prezzi" in s:
            return Risultato(self.autohero)
        if s.startswith("SELECT id, status, price_eur, first_seen_at"):
            return Risultato([self.listings[i] for i in params[0] if i in self.listings])
        if "FROM listings l LEFT JOIN LATERAL" in s or s.startswith("WITH c AS"):
            return Risultato([dict(a) for a in self.auto])
        return Risultato([])

    def commit(self):
        self.commit_n += 1

    def rollback(self):
        self.rollback_n += 1


def _per_giro(i):
    return dict(AUTO, id=i, confronti=_confronti(4))


def test_giro_coda_tetto_e_salvataggio():
    with Env(PERIZIA_PER_GIRO="2", PERIZIA_MAX_GIORNO="400", PERIZIA_AI=None):
        _giro_coda_tetto_e_salvataggio()


def _giro_coda_tetto_e_salvataggio():
    P._PAUSA["fino"] = 0
    P.metti_in_coda([11, 12, 13], [12, 14])
    assert P.CODA == {"attesa": [11, 12, 13], "sito": [14]}
    conn = ConnFinta(auto=[_per_giro(i) for i in (11, 12, 13, 14)])
    res = P.giro(conn, chiama=lambda *a: (copy.deepcopy(BUONA), 10, 5))
    assert res["fatte"] == 2 and res["da_attesa"] == 2 and [x[0] for x in conn.salvate] == [11, 12]
    assert P.CODA["attesa"] == [13] and P.CODA["sito"] == [14]
    assert not any("listing_photos" in s for s in conn.sql)       # niente foto
    # tetto del giorno raggiunto: nessuna chiamata
    res = P.giro(ConnFinta(auto=[_per_giro(13)], fatte=400), chiama=lambda *a: 1 / 0)
    assert res["saltato"] == "tetto giornaliero raggiunto"
    # oltre il 60% del tetto le auto già sul sito aspettano: si fanno solo quelle in attesa
    P.metti_in_coda([], [14])
    res = P.giro(ConnFinta(auto=[_per_giro(14)], fatte=250), chiama=lambda *a: 1 / 0)
    assert res["saltato"].startswith("quota")
    P.metti_in_coda([13], [14])
    conn = ConnFinta(auto=[_per_giro(13), _per_giro(14)], fatte=250)
    assert P.giro(conn, chiama=lambda *a: (copy.deepcopy(BUONA), 1, 1))["fatte"] == 1 and conn.salvate[0][0] == 13
    P.metti_in_coda([], [])


def test_giro_pausa_di_un_ora_su_credito():
    P._PAUSA["fino"] = 0
    P.metti_in_coda([21, 22], [])
    chiamate = []

    def finita(*a):
        chiamate.append(1)
        raise ErroreAPI(400, "Your credit balance is too low to access the Anthropic API")
    res = P.giro(ConnFinta(auto=[_per_giro(21), _per_giro(22)]), chiama=finita)
    assert res["pausa_credito"] == 1 and len(chiamate) == 1
    assert P.CODA["attesa"] == [21, 22]                        # restano in coda per dopo la pausa
    assert P.giro(ConnFinta(), chiama=finita)["saltato"].startswith("pausa")
    assert P._PAUSA["fino"] > datetime.now().timestamp() + 3000
    # limite (429): pausa breve
    P._PAUSA["fino"] = 0
    res = P.giro(ConnFinta(auto=[_per_giro(21)]), chiama=lambda *a: (_ for _ in ()).throw(ErroreAPI(429, "rate")))
    assert res["pausa_limite"] == 1 and P._PAUSA["fino"] < datetime.now().timestamp() + 400
    P._PAUSA["fino"] = 0
    P.metti_in_coda([], [])


def test_risposta_troncata_una_chiamata_spesa_contata_e_fallita():
    """Il JSON troncato arriva da ask_json come AIError DOPO la registrazione dei consumi: la spesa resta (commit, non
    rollback), l'auto va in _FALLITE e alla seconda volta si salta: niente chiamate pagate all'infinito."""
    from dealfinder.ai import client as AI
    P._PAUSA["fino"] = 0
    P._FALLITE.clear()
    chiamate = []
    vera = AI.ask_json

    def troncata(task, model, system, content, max_tokens=0, web_search=None, usage_sink=None, listing_id=None):
        chiamate.append(listing_id)
        usage_sink(task, AI.AIResult(data=None, model=model, input_tokens=2000, output_tokens=900), listing_id)
        return AI.extract_json('{"dati_sufficienti": true, "prezzo_vendita": {"basso": 9000, "medio"')
    AI.ask_json = troncata
    try:
        P.metti_in_coda([41], [])
        conn = ConnFinta(auto=[_per_giro(41)])
        res = P.giro(conn, chiama=P._chiama_claude)
        assert res["non_valida"] == 1 and chiamate == [41] and conn.salvate == []
        assert len(conn.usage) == 1 and conn.usage[0][0] == "perizia" and conn.rollback_n == 0
        assert P._FALLITE[41][0] == 1
        P._segna_fallita(41)
        P.metti_in_coda([41], [])
        P.giro(ConnFinta(auto=[_per_giro(41)]), chiama=P._chiama_claude)
        assert chiamate == [41]                                   # due fallimenti: per 6 ore non si richiama
    finally:
        AI.ask_json = vera
        P.metti_in_coda([], [])
        P._FALLITE.clear()


def test_errore_generico_segna_fallita_e_limite_fallite():
    P._PAUSA["fino"] = 0
    P._FALLITE.clear()
    P.metti_in_coda([51], [])
    conn = ConnFinta(auto=[_per_giro(51)])
    res = P.giro(conn, chiama=lambda *a: (_ for _ in ()).throw(ValueError("boh")))
    assert res["errore"] == 1 and P._FALLITE[51][0] == 1 and P.CODA["attesa"] == [51]
    for i in range(P.FALLITE_MAX + 50):
        P._segna_fallita(10_000 + i)
    assert len(P._FALLITE) == P.FALLITE_MAX
    P._FALLITE.clear()
    P.metti_in_coda([], [])


def test_giro_risposta_non_valida_non_si_salva():
    P._PAUSA["fino"] = 0
    P._FALLITE.clear()
    P.metti_in_coda([31], [])
    conn = ConnFinta(auto=[_per_giro(31)])
    res = P.giro(conn, chiama=lambda *a: ({"giudizio": "boh"}, 1, 1))
    assert res["non_valida"] == 1 and conn.salvate == []
    P.metti_in_coda([], [])
    P._FALLITE.clear()


# ------------------------------------------------------------------ prova alla cieca
def test_metriche():
    m = PC.metriche([(11000, 10000), (9500, 10000), (10000, 0), (None, 5000)])
    assert m["n"] == 2 and m["errore_medio_eur"] == 750 and m["errore_medio_pct"] == 7.5
    assert m["entro_10pct"] == 2 and m["scostamento_medio_pct"] == 2.5
    assert PC.metriche([])["n"] == 0


def test_autohero_corrispondenza():
    # Mercedes-Benz Classe A: slug Autohero "mercedes-benz-classe-a" o con il motore "mercedes-benz-a-180"
    assert PC.stesso_modello("mercedes-benz-classe-a", "Mercedes-Benz", "Classe A")
    assert PC.stesso_modello("mercedes-benz-a-180", "mercedes-benz", "classe-a")
    assert PC.stesso_modello("mercedes-benz-classe-a", "mercedes", "a 180")
    assert not PC.stesso_modello("mercedes-benz-classe-b", "mercedes-benz", "classe-a")
    assert not PC.stesso_modello("mercedes-benz-cla", "mercedes-benz", "classe-a")
    # varianti nello slug: carrozzeria e trattini
    assert PC.stesso_modello("volkswagen-golf-variant", "volkswagen", "golf")
    assert PC.stesso_modello("audi-a-6-avant", "audi", "a6")
    assert PC.stesso_modello("bmw-serie-3-touring", "bmw", "serie-3") and PC.stesso_modello("bmw-320", "bmw", "serie-3")
    assert not PC.stesso_modello("volkswagen-golf-sportsvan", "volkswagen", "golf")
    assert not PC.stesso_modello("fiat-500-x", "fiat", "500") and PC.stesso_modello("fiat-500-x", "fiat", "500x")
    assert PC.marche_slug("Mercedes-Benz") == ["mercedes-benz", "mercedes"]
    righe = [{"prezzo": 15000, "anno": 2017, "km": 90000}, {"prezzo": 14000, "anno": 2016, "km": 100000},
             {"prezzo": 30000, "anno": 2019, "km": 95000}, {"prezzo": 16000, "anno": 2016, "km": 130000}]
    assert PC.autohero_vicine(righe, 2016, 98000) == 14500          # anno ±1, km ±20.000
    assert PC.autohero_vicine(righe, 2010, 98000) is None


def test_prova_cieca_finta_senza_scritture():
    ora = datetime.now(timezone.utc)
    sparita = ora - timedelta(days=5)
    a = dict(AUTO, id=7, price_eur=9000, disappeared_at=sparita, m_calcolo=9600,
             confronti=[{"id": 201, "prezzo": 9500, "anno": 2016, "km": 95000}, {"id": 202, "prezzo": 9900, "anno": 2016, "km": 99000},
                        {"id": 203, "prezzo": 5000, "anno": 2016, "km": 99000}])
    listings = {201: {"id": 201, "status": "scomparso", "price_eur": 9400, "first_seen_at": sparita - timedelta(days=20),
                      "disappeared_at": sparita - timedelta(days=2), "seller_type": "privato"},
                202: {"id": 202, "status": "scomparso", "price_eur": 9900, "first_seen_at": sparita - timedelta(days=9),
                      "disappeared_at": sparita + timedelta(days=2), "seller_type": "commerciante"},
                203: {"id": 203, "status": "attivo", "price_eur": 5000, "first_seen_at": sparita + timedelta(days=1),
                      "disappeared_at": None, "seller_type": "privato"}}
    conn = ConnFinta(auto=[a], listings=listings, autohero=[{"slug": "volkswagen-golf", "prezzo": 11000, "anno": 2016, "km": 100000},
                                                            {"slug": "volkswagen-golf-plus", "prezzo": 6000, "anno": 2016, "km": 100000}])
    visti = {}

    def finta(sistema, testo, listing_id, sink):
        visti["testo"] = testo
        return dict(BUONA, prezzo_vendita={"basso": 8500, "medio": 9300, "alto": 9900}, acquisto_max=7800), 100, 50
    res = PC.esegui(conn, n=5, giorni=30, chiama=finta)
    t = visti["testo"]
    assert "NON INDICATO" in t, t
    assert "8.500" not in t, t
    assert " 9000 €" not in t and "9.000" not in t, t
    assert "venduto) dopo 18 giorni" in t and "attivo da 9 giorni" in t    # 202 sparita DOPO: allora era attiva
    assert "9500 €" in t and "9400 €" not in t                            # prezzo della valutazione, non quello di oggi
    assert "5000 €" not in t                                               # 203 comparsa dopo: tolta
    assert res["m_calcolo"]["n"] == 1 and res["m_calcolo"]["errore_medio_eur"] == 600
    assert res["perizia"]["errore_medio_eur"] == 300 and res["autohero"]["errore_medio_eur"] == 2000
    assert res["perizia"]["entro_10pct"] == 1 and res["token_in"] == 100
    assert not any(s.split()[0] in ("INSERT", "UPDATE", "DELETE") for s in conn.sql)


def test_scheda_mostra_il_parere_del_perito():
    import os
    js = open(os.path.join(os.path.dirname(__file__), "..", "dealfinder", "web", "static", "scovo", "app.js")).read()
    assert "function boxPerito(" in js and "Parere del perito" in js and "boxPerito(d.perizia, c)" in js


def test_coda_dal_calcolo_dell_elenco():
    from dealfinder.web import app
    items = [{"id": 1, "_perizia_attesa": True}, {"id": 2}, {"id": 3, "_r": {"perizia": {"giudizio": "affare"}}},
             {"id": 4, "supercar": True}]
    app._coda_perizia(items, [items[1], items[2], items[3]])
    assert P.CODA == {"attesa": [1], "sito": [2]}
    P.metti_in_coda([], [])
