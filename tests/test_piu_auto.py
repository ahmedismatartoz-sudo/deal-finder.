"""Ramo piu-auto (13/11): controllo Claude al posto di Jev, versioni solo di nome, sconto alto confermato dall'AI,
numero nascosto, ripiego prudente, elenco a pagine. Niente rete né database (connessioni finte)."""
import os
import time

from dealfinder.ai import client as AI
from dealfinder.ai import controllo_auto as CA
from dealfinder.pipeline import telefono_nascosto as TN
from dealfinder.pricing import jev
from dealfinder.pricing import ripiego_prudente as RP
from dealfinder.pricing import versione_tecnica as VT
from dealfinder.web import scovo
from dealfinder.web import sito_regole as SR

G = SR.Regole()


class env:
    """Variabili d'ambiente solo dentro il blocco (run_tests controlla che nessun test le lasci cambiate)."""
    def __init__(self, **kv):
        self.kv, self.prima = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.prima[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.prima.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def auto(i, prezzo=5000, margine=3000, M=8000, prov="MI", tel=True, seller="privato", jev=None, **kw):
    r = {"tel": tel, "seller_type": seller, "provincia": prov, "jev": jev}
    r.update(kw.pop("_r", {}))
    it = {"id": i, "prezzo": prezzo, "anno": 2015, "km": 90000, "carb": "Diesel", "fonte": "Subito",
          "pv": {"categoria": kw.pop("cat", "opportunita"), "M": M, "R": prezzo + margine + 200,
                 "R_finale": prezzo + margine + 200, "margine": margine, "totale_costi": 200, "n": 10},
          "_r": r}
    it.update(kw)
    return it


def elenco(items, g=G):
    out, fun = SR.elenco_sito([dict(it, _r=dict(it["_r"]), pv=dict(it["pv"])) for it in items], g, scrivi_log=False)
    return out, fun


def ids(items, g=G):
    return [it["id"] for it in elenco(items, g)[0]]


# ------------------------------------------------------------------ 1. Claude al posto di Jev
def test_sostituto_di_default_e_claude():
    with env(JEV_SOSTITUTO=None):
        assert jev.sostituto() == "claude"
    with env(JEV_SOSTITUTO="jev", TYPESAFE_API_KEY=None):
        assert jev.sostituto() == "jev" and jev.attiva() is False
    with env(JEV_SOSTITUTO="boh"):
        assert jev.sostituto() == "claude"


def test_testo_senza_numeri_ne_mercato():
    r = {"title": "Fiat Panda 1.2", "description": "ottima, chiamare 333 1234567 o mario@rossi.it", "make": "fiat",
         "model": "panda", "year": 2015, "mileage_km": 90000, "price_eur": 4000, "n_foto": 6}
    conf = [{"prezzo": 6100, "prezzo_corretto": 6000, "anno": 2015, "km": 85000, "kw": 51, "versione": "1.2 Lounge"}]
    t = CA.testo_richiesta(r, conf, {"giudizio": "nella media", "rischi": [{"tipo": "km_sospetti", "gravita": "bassa",
                                                                             "nota": "pochi km"}]}, foto=2)
    assert "333" not in t and "mario@" not in t and "[numero]" in t
    assert "6100" not in t and "6000" not in t                 # i confronti vanno senza prezzi (niente mercato)
    assert "1.2 Lounge" in t and "km_sospetti" in t and "FOTO ALLEGATE: 2" in t


def test_valida_e_formato_jev():
    d = {"guasto": 0.1, "prezzo_rata": "0,05", "km_credibili": 0.9, "danni_foto": 0.2, "foto_internet": 0.1,
         "targa_visibile": 0.8, "versione_ok": 0.7, "dati_coerenti": 0.9, "truffa": 0.1, "auto_vera": 0.9,
         "stato": 3, "decisione": "Compra", "confidenza": 0.8, "note": "ok, tel 3331234567"}
    x = CA.valida(d, con_foto=True, con_confronti=True)
    assert x["prezzo_rata"] == 0.05 and x["decisione"] == "compra" and "333" not in x["note"]
    senza = CA.valida(d, con_foto=False, con_confronti=False)
    assert senza["danni_foto"] is None and senza["targa_visibile"] is None and senza["versione_ok"] is None
    r = CA.in_risposta(x, "claude-haiku")
    # stesso formato che legge web/app.py: risposta->'guasto'->>'noul' ecc.
    assert r["guasto"]["noul"] == 0.1 and r["km_credibili"]["noul"] == 0.9 and r["auto_vera"]["noul"] == 0.9
    assert r["_fonte"] == "claude" and r["decisione"]["choice"] == "compra"
    for male in ({**d, "truffa": 1.4}, {**d, "decisione": "forse"}, {k: v for k, v in d.items() if k != "guasto"}):
        try:
            CA.valida(male, True, True)
            assert False, male
        except CA.ControlloNonValido:
            pass


def test_cache_auto_e_contenuto():
    a = {"title": "Golf", "description": "bella", "year": 2018, "mileage_km": 60000}
    imp = CA.impronta(a, ["imgid:abc"])
    assert imp == CA.impronta(dict(a, price_eur=9999), ["imgid:abc"])          # il prezzo non conta
    assert imp != CA.impronta(a, ["imgid:xyz"]) and imp != CA.impronta(dict(a, description="motore rotto"), ["imgid:abc"])
    assert CA.riusabile(10000, imp, 10500, imp) and CA.riusabile(10000, imp, 9100, imp)
    assert not CA.riusabile(10000, imp, 8500, imp)                              # calo forte: si ricontrolla
    assert not CA.riusabile(10000, "altro", 10000, imp)


def test_priorita_nuove_prezzo_cambiato_poi_rinnovi():
    righe = [{"id": 1, "m": 9000, "price_eur": 7000, "margine": 2000, "cat": "opportunita"},
             {"id": 2, "m": 20000, "price_eur": 12000, "margine": 8000, "cat": "opportunita"},   # sconto 8.000 > 5.000
             {"id": 3, "m": 9000, "price_eur": 5000, "margine": 3500, "cat": "opportunita"},
             {"id": 4, "m": 20000, "price_eur": 12000, "margine": 8000, "cat": "opportunita", "d_prezzo": 12000,
              "d_fonte": "jev"},                                                                 # rinnovo Jev scaduta
             {"id": 5, "m": 9000, "price_eur": 6000, "margine": 2500, "cat": "opportunita", "d_prezzo": 6500}]
    assert [r["id"] for r in CA.ordina(righe)] == [2, 3, 1, 5, 4]
    with env(JEV_SOSTITUTO=None, CONTROLLO_PER_GIRO="25"):
        assert jev.per_giro() == 25                               # il giro rapido guarda CONTROLLO_PER_GIRO


def test_prezzi_veri_e_budget_separato():
    assert abs(AI.costo("claude-haiku-5-5", 1_000_000, 0) - 1.0) < 1e-9
    assert abs(AI.costo("claude-haiku-5-5", 0, 1_000_000) - 5.0) < 1e-9
    assert abs(AI.costo("claude-sonnet-5-5", 1_000_000, 1_000_000) - 18.0) < 1e-9
    assert "controllo_auto" in AI.TASK_BUDGET_SEPARATO

    class C:
        def execute(self, sql, p=None):
            self.sql, self.p = sql, p
            return _Ris([])
    c = C()
    assert AI.spesa_oggi(c) == 0 and "task <> ALL" in c.sql and p_ok(c.p)
    with env(CONTROLLO_BUDGET_USD=None):
        assert CA.budget_usd() == 12.0  # 14/11 sera: 12 $


def p_ok(p):
    return p == (["controllo_auto", "controllo_auto_rinnovo"],)


def test_costo_per_auto_richiesta_tipo():
    """La richiesta tipo (testo pieno, 10 confronti, 2 foto da 512 px) costa ~0,003 $ con Haiku."""
    r = {"title": "Volkswagen Golf 1.6 TDI Highline", "description": "x" * 1800, "make": "volkswagen", "model": "golf",
         "version_raw": "1.6 TDI 115 CV Highline", "year": 2018, "mileage_km": 90000, "fuel": "diesel",
         "gearbox": "manuale", "power_kw": 85, "price_eur": 12500, "n_foto": 12}
    conf = [{"anno": 2018, "kw": 85, "cambio": "manuale", "km": 80000, "versione": "1.6 TDI Business"}] * 10
    caratteri = len(CA.SISTEMA) + len(CA.testo_richiesta(r, conf, None, foto=2))
    token_in = caratteri / 3.2 + 2 * (512 * 384 / 750)           # ~3,2 caratteri per token in italiano
    usd = AI.costo("claude-haiku-5-5", int(token_in), 180)
    assert 1800 < token_in < 3200 and 0.002 < usd < 0.0045, (token_in, usd)


def test_testo_basta_a_scartare_senza_chiamata():
    assert CA.scartata_dal_testo({"title": "Golf", "description": "chiedo caparra con bonifico prima di vedere l'auto"}) \
        == "testo_pagamento_estero"
    assert CA.scartata_dal_testo({"title": "Golf", "description": "ok", "mileage_km": 190000}) == "km"
    assert CA.scartata_dal_testo({"title": "Golf", "description": "tagliandata", "mileage_km": 90000, "year": 2015}) is None


class _Err(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status_code = status


def test_controlla_ripiega_sulle_foto():
    ok = {"guasto": 0, "prezzo_rata": 0, "km_credibili": 1, "danni_foto": 0, "foto_internet": 0, "targa_visibile": 1,
          "versione_ok": None, "dati_coerenti": 1, "truffa": 0, "auto_vera": 1, "stato": 3, "decisione": "compra",
          "confidenza": .9, "note": ""}
    chiamate = []

    def chiama(testo, foto):
        chiamate.append([b["source"]["type"] for b in foto])
        if foto and foto[0]["source"]["type"] == "url":
            raise _Err("Unable to download the file at the url provided", 400)
        return ok, 1000, 100, "claude-haiku-5-5"
    vecchio = AI.image_block
    AI.image_block = lambda src, http=None, side=None: {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "x"}}
    try:
        with env(CONTROLLO_FOTO_MODO="url"):
            res = CA.controlla({"id": 1, "price_eur": 5000}, ["https://img.example/1.jpg", "imgid:abcdef0123456789abcdef0123456789"],
                           chiama=chiama)
    finally:
        AI.image_block = vecchio
    assert chiamate == [["url", "url"], ["base64", "base64"]]
    assert res["errore"] is None and res["x"]["danni_foto"] == 0 and res["token"] == [(1000, 100, "claude-haiku-5-5")]


def test_controlla_credito_finito_non_insiste():
    def chiama(testo, foto):
        raise _Err("credit balance too low", 402)
    with env(CONTROLLO_FOTO_MODO="0"):
        res = CA.controlla({"id": 1}, [], chiama=chiama)
    assert isinstance(res["errore"], _Err) and CA._tipo_errore(res["errore"]) == "credito"


class _Ris:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None

    def __iter__(self):
        return iter(self.righe)


class ConnFinta:
    def __init__(self, cand, auto):
        self.cand, self.auto, self.scritte = cand, auto, []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("ALTER") or s.startswith("UPDATE") or s.startswith("INSERT"):
            self.scritte.append((s[:40], params))
            return _Ris([])
        if "FROM ai_usage" in s:
            return _Ris([])
        if "information_schema" in s:
            return _Ris([{"n": 2}])
        if "WITH cand AS" in s:
            return _Ris(self.cand)
        if "to_regclass" in s:
            return _Ris([{"t": None}])
        if "FROM listings l" in s:
            return _Ris(self.auto)
        return _Ris([])

    def commit(self):
        self.scritte.append(("COMMIT", None))

    def rollback(self):
        pass


def test_giro_salva_come_jev_e_si_ferma_sul_credito():
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    CA._COLONNE_FATTE["ok"] = False
    cand = [{"id": i, "price_eur": 5000, "telefono_ok": True, "cat": "opportunita", "margine": 3000, "m": 8000,
             "d_prezzo": None, "d_impronta": None, "d_fonte": None, "d_at": None} for i in (1, 2, 3)]
    auto_ = [{"id": i, "title": "Panda", "description": "ok", "price_eur": 5000, "foto": [], "confronti": [],
              "cat": "opportunita", "margine": 3000} for i in (1, 2, 3)]
    ok = {"guasto": 0, "prezzo_rata": 0, "km_credibili": 1, "dati_coerenti": 1, "truffa": 0.1, "auto_vera": 0.9,
          "stato": 2, "decisione": "tratta", "confidenza": .6, "note": ""}
    n = {"c": 0}

    def chiama(testo, foto):
        n["c"] += 1
        if n["c"] == 2:
            raise _Err("credit balance is too low", 402)
        return ok, 800, 80, "claude-haiku-5-5"
    conn = ConnFinta(cand, auto_)
    with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="0"):
        res = CA.giro(conn, chiama=chiama)
    assert res["fatte"] == 1 and res["pausa_credito"] == 1 and n["c"] == 2          # la terza non si chiama
    assert CA._PAUSA["fino"] > time.time()
    dec = [p for s, p in conn.scritte if s.startswith("INSERT INTO decisione_ai")]
    assert len(dec) == 1 and dec[0][0] == 1
    assert len([p for s, p in conn.scritte if s.startswith("INSERT INTO ai_usage")]) == 1     # spesa registrata
    with env(CONTROLLO_FOTO_MODO="0"):
        assert CA.giro(conn, chiama=chiama)["saltato"].startswith("pausa")
    CA._PAUSA["fino"] = 0.0


def test_spesa_confermata_subito_e_niente_ripagamenti():
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    cand = [{"id": i, "price_eur": 5000, "telefono_ok": True, "cat": "opportunita", "margine": 3000, "m": 8000,
             "d_prezzo": None} for i in (1, 2)]
    auto_ = [{"id": i, "title": "Panda", "description": "ok", "price_eur": 5000, "foto": [], "confronti": []} for i in (1, 2)]
    chiamate = []

    def chiama(testo, foto):
        chiamate.append(1)
        return {"decisione": "forse"}, 900, 50, "claude-haiku-5-5"        # pagata ma non valida
    conn = ConnFinta(cand, auto_)
    with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="0"):
        res = CA.giro(conn, chiama=chiama)
        assert res["non_valida"] == 2 and len(chiamate) == 2
        voci = [q for q, _p in conn.scritte]
        spese = [k for k, q in enumerate(voci) if q.startswith("INSERT INTO ai_usage")]
        assert len(spese) == 2 and all(voci[k + 1] == "COMMIT" for k in spese)   # confermata subito dopo la chiamata
        res = CA.giro(conn, chiama=chiama)                               # stesso giro dopo: nessuna nuova spesa
        assert len(chiamate) == 2 and res.get("candidate", 0) == 0
    CA._FALLITE.clear()


# ------------------------------------------------------------------ 2. versioni diverse solo di nome
def test_versione_tecnica():
    t = {"power_kw": 51, "fuel": "benzina", "year": 2016, "version_raw": "1.2 Lounge"}
    stessa = [{"kw": 51, "anno": 2016, "versione": "1.2 Pop"}, {"kw": 51, "anno": 2015, "versione": "1.2 Easy"},
              {"kw": 50, "anno": 2017, "versione": "1.2 Lounge"}]
    assert VT.verdetto(t, stessa) == "uguale"
    altra = [{"kw": 63, "anno": 2016, "versione": "0.9 TwinAir"}, {"kw": 70, "anno": 2016, "versione": "1.3 Multijet"},
             {"kw": 51, "anno": 2016, "versione": "1.2"}]
    assert VT.verdetto(t, altra) == "diversa"
    assert VT.verdetto(t, [{"anno": 2016, "versione": "Lounge"}] * 5) is None          # niente da verificare
    assert VT.verdetto({"fuel": "benzina", "version_raw": "Lounge"}, stessa) is None    # auto senza scheda
    misto = stessa + [{"kw": 81, "anno": 2016, "versione": "1.4 T-Jet"}]
    assert VT.verdetto(t, misto) == "diversa"                                    # serve il 100% compatibile
    pochi = stessa + [{"anno": 2016, "versione": "Lounge"}] * 3
    assert VT.verdetto(t, pochi) is None                                         # verificabili 3 su 6 < 60%
    assert VT.litri_da_testo("1598 cm3") == 1.6 and VT.litri_da_testo("1.598 cc") == 1.6
    assert VT.cilindrata_cc("1598 cm3") == 1598
    assert VT.scheda({"version_raw": "Highline", "cilindrata_testo": "1598 cm3"})["litri"] == 1.6
    assert VT.kw_da_testo("2.0 TDI 150 CV") == 110.3 and VT.litri_da_testo("1.6 TDI S tronic") == 1.6
    assert VT.tipo_motore("1.6 TDI Highline") == "diesel"


def test_versioni_solo_di_nome_restano():
    j = {"versione_ok": 0.1, "truffa": 0.1}
    assert ids([auto(1, jev=j, _r={"versione_tecnica": "uguale"})]) == [1]
    assert ids([auto(2, jev=j, _r={"versione_tecnica": "diversa"}), auto(3, jev=j)]) == []
    import dataclasses
    assert ids([auto(4, jev=j, _r={"versione_tecnica": "uguale"})], dataclasses.replace(G, versione_tecnica=False)) == []


# ------------------------------------------------------------------ 3. sconto alto: controllo AI invece di esclusione
def test_sconto_alto_confermato_dall_ai():
    vera = {"truffa": 0.1, "auto_vera": 0.9, "dati_coerenti": 0.9, "danni": 0.05, "foto_internet": 0.1,
            "decisione": "compra", "fonte": "claude"}
    base = dict(prezzo=12000, M=17500, margine=5500)                  # sconto 5.500 € > 5.000 € della fascia
    out, _ = elenco([auto(1, jev=dict(vera, truffa=0.2), **base)])
    assert [(it["id"], it["avviso_verifica"]) for it in out] == [(1, True)]      # entra, sempre con l'avviso
    assert ids([auto(2, jev=dict(vera, truffa=0.35, fonte=None), **base)]) == []  # Jev: come prima
    assert ids([auto(21, jev=dict(vera, truffa=0.1, fonte=None), **base)]) == [21]  # Jev pulita: come prima
    assert ids([auto(22, jev=dict(vera, truffa=0.35), **base)]) == []           # Claude: truffa sotto ai_pulita (0,30)
    assert ids([auto(3, jev=dict(vera, truffa=0.1, auto_vera=0.5), **base)]) == []   # Claude pulita ma non confermata
    assert ids([auto(4, jev=dict(vera, truffa=0.1, danni=None), **base)]) == []      # foto non viste
    assert ids([auto(5, jev=dict(vera, truffa=0.1), _r={"perizia": {"giudizio": "caro"}}, **base)]) == []
    assert ids([auto(6, jev=dict(vera, truffa=0.6), **base)]) == []
    assert ids([auto(7, **base)]) == []                                        # nessun controllo: fuori come prima
    # i segnali duri restano: oltre il 40% di sconto fuori anche con l'AI che conferma
    assert ids([auto(8, prezzo=13000, M=21900, margine=8900, jev=vera)]) == []
    assert ids([auto(9, jev=vera, _r={"testo_truffa": True}, **base)]) == []
    import dataclasses
    assert ids([auto(10, jev=dict(vera, truffa=0.35), **base)], dataclasses.replace(G, sconto_ai=False)) == []


# ------------------------------------------------------------------ 5. dati insufficienti: ripiego prudente
def test_ripiego_prudente_stima():
    dati = {"motivi": ["solo 1 confronti validi (servono almeno 2)"], "n": 1,
            "confronti": [{"prezzo": 9800, "prezzo_corretto": 9500}]}
    a = {"year": 2016, "mileage_km": 80000, "fuel": "benzina", "price_eur": 6000, "damage_class": "nessuno"}
    assert RP.ammessa(a, dati) is None
    assert RP.ammessa(dict(a, damage_class="medio"), dati) == "danno"
    assert RP.ammessa(a, {"motivi": ["prezzo oltre il 45% sotto la mediana: ..."]}) == "altro_motivo"
    vr = {"v": 9000, "sigma": 0.12, "n": 40, "n_vendute": 25}
    est, f, mot = RP.stima(6000, dati, vr, [9900, 10100, 9800])
    assert mot == "" and est == round(min(f.values()) * 0.97) and all(est <= v for v in f.values())
    assert RP.stima(6000, dati, None, [])[2] == "fonti_insufficienti"           # una fonte sola
    assert RP.stima(6000, dati, dict(vr, v=13000), [9900, 10100])[2] == "fonti_discordi"
    assert RP.stima(4000, dati, vr, [9900, 10100])[2] == "prezzo_troppo_basso"
    assert "valore_reale" not in RP.fonti(dati, dict(vr, sigma=0.2), [])      # soglie della regola: errore max 15%
    assert "valore_reale" not in RP.fonti(dati, dict(vr, n_vendute=10), [])   # e almeno 20 auto vendute


def test_ripiego_sul_sito_solo_con_controllo_ai():
    rip = {"M": 8700, "R": 8700, "R_finale": 8700, "margine": 2500, "totale_costi": 200, "metodo": "ripiego_prudente",
           "calibrato": True}
    j = {"truffa": 0.1}
    out, _ = elenco([auto(1, cat="dati_insufficienti", prezzo=6000, jev=j, _r={"ripiego": rip})])
    assert [(it["id"], it["pv"]["categoria"], it["avviso_verifica"]) for it in out] == [(1, "candidato", True)]
    assert ids([auto(2, cat="dati_insufficienti", prezzo=6000, _r={"ripiego": rip})]) == []      # aspetta l'AI
    assert ids([auto(3, cat="dati_insufficienti", prezzo=6000, jev=j)]) == []                    # nessuna stima
    assert ids([auto(4, cat="dati_insufficienti", prezzo=6000, jev=j,
                     _r={"ripiego": dict(rip, margine=1000)})]) == []                               # soglia invariata
    import dataclasses
    assert ids([auto(5, cat="dati_insufficienti", prezzo=6000, jev=j, _r={"ripiego": rip})],
               dataclasses.replace(G, ripiego=False)) == []


# ------------------------------------------------------------------ 4. numero nascosto
class _Resp:
    def __init__(self, code, js=None):
        self.status_code, self._js = code, js

    def json(self):
        return self._js


class _Http:
    def __init__(self, risposte):
        self.risposte, self.params = list(risposte), []

    def get(self, url, headers=None, params=None):
        self.params.append(params)
        return self.risposte.pop(0)


def _azzera_tel():
    TN._PAUSA["fino"] = 0.0
    TN._CONTO.update(giorno=None, n=0)
    TN.inizio_giro()


def test_prioritari_solo_con_margine():
    s = scovo.soglia(6000)
    items = [auto(1, prezzo=6000, margine=s), auto(2, prezzo=6000, margine=s - 300), auto(3, prezzo=6000, margine=200),
             auto(4, prezzo=6000, margine=s), auto(5, prezzo=6000, margine=s, prov="RM"),
             auto(6, prezzo=6000, margine=s, prov="NA")]
    n = TN.segna_prioritari(items, [1, 2, 3, 5, 6], {i: "Subito" for i in range(1, 7)})
    # 2 arriva al minimo con 300 € di sconto; 4 non è stata tolta; Roma mai; Napoli (diesel, non fascia) fuori zona
    assert n == 2 and TN.PRIORITARI == frozenset({1, 2}) and isinstance(TN.PRIORITARI, frozenset)
    TN.PRIORITARI = frozenset()


def test_subito_numero_dalla_ricerca():
    with env(TEL_API_PAUSA_S="0", TEL_SUBITO_API="1"):
        _numero_dalla_ricerca()


def _numero_dalla_ricerca():
    _azzera_tel()
    r = {"id": 7, "source_id": "id:ad:123456789:list:987654321", "url": "https://www.subito.it/auto/x-987654321.htm",
         "make": "fiat", "model": "panda", "price_eur": 5000, "province": "MI"}
    con = {"ads": [{"urn": "id:ad:1:list:111111"}, {"urn": "id:ad:123456789:list:987654321", "advertiser": {"phone": "0"}}]}
    h = _Http([_Resp(200, con)])
    assert TN.subito_telefono_api(h, r) == ("attivo", True)
    assert h.params[0]["ps"] == 5000 and h.params[0]["pe"] == 5000 and h.params[0]["r"] == 4 and h.params[0]["q"] == "fiat panda"
    senza = {"ads": [{"urn": "id:ad:123456789:list:987654321", "advertiser": {"type": 0}}]}
    assert TN.subito_telefono_api(_Http([_Resp(200, senza)]), r) == ("attivo", False)
    assert TN.subito_telefono_api(_Http([_Resp(200, {"ads": []}), _Resp(200, {"ads": []})]), r) == ("non_trovato", None)
    assert TN.subito_telefono_api(_Http([_Resp(429)]), r) == ("bloccato", None)
    assert TN.puo_cercare() == "pausa_blocco"                                       # stop dopo 403/429
    _azzera_tel()
    with env(TEL_API_MAX_GIORNO="1"):
        TN.subito_telefono_api(_Http([_Resp(200, con)]), r)
        assert TN.puo_cercare() == "tetto_giorno"
    _azzera_tel()


# ------------------------------------------------------------------ 6. elenco a pagine
def test_elenco_a_pagine_e_tetto():
    from collections import Counter
    from dealfinder.web import app

    class Conn:
        def __init__(self, n):
            self.ids, self.chiamate = list(range(n, 0, -1)), []

        def execute(self, sql, p):
            self.chiamate.append(p)
            sotto = [i for i in self.ids if p.get("ultimo") is None or i < p["ultimo"]][:p["lim"]]
            return [{"id": i, "opens": 0} for i in sotto]
    vecchio = scovo.item
    scovo.item = lambda r: {"id": r["id"]}
    try:
        with env(ELENCO_PAGINA="300", ELENCO_LIMITE="1000"):
            _pagine(app, Conn)
    finally:
        scovo.item = vecchio


def _pagine(app, Conn):
    from collections import Counter
    if True:
        st = Counter()
        c = Conn(700)
        items, *_ = app._leggi_elenco(c, st)
        assert len(items) == 700 and st["lista"] == 700 and "lista_tagliata" not in st and len(c.chiamate) == 3
        assert [it["id"] for it in items] == list(range(700, 0, -1))
        st = Counter()
        items, *_ = app._leggi_elenco(Conn(1500), st)
        assert len(items) == 1000 and st["lista_tagliata"] == 1 and items[-1]["id"] == 501


def test_telefoni_candidate_subito_prioritarie_e_spariti_as24(monkeypatch=None):
    from dealfinder.pipeline import tempo_reale as tr
    from dealfinder.pipeline import verify

    class Conn:
        def __init__(self):
            self.scritte, self.params = [], None

        def execute(self, sql, p=None):
            if sql.lstrip().startswith("SELECT"):
                self.params = p
                return _Ris([{"id": 1, "source": "autoscout24", "source_id": "a", "url": "https://as/1", "price_eur": 9000},
                             {"id": 2, "source": "subito", "source_id": "id:ad:1:list:22222", "url": "u", "make": "fiat",
                              "model": "panda", "price_eur": 5000, "province": "MI"}])
            self.scritte.append((" ".join(sql.split())[:60], p))
            return _Ris([])

        def commit(self):
            pass
    segnati = []
    salva = (tr._as_esito, verify._mark, TN.subito_telefono_api, tr._client_as, TN.client_api)
    TN.client_api = lambda: type("H", (), {"close": lambda self: None})()
    tr._as_esito = lambda c, url: ("scomparso", None, "404")
    verify._mark = lambda conn, lid, st, price=None, motivo=None, sito=False: segnati.append((lid, st, motivo))
    TN.subito_telefono_api = lambda client, r, conn=None: ("attivo", True)
    tr._client_as = lambda: None
    TN.PRIORITARI = frozenset({2})
    salva_conto = TN.leggi_conto
    TN.leggi_conto = lambda conn: None
    _azzera_tel()
    try:
        with env(TR_PAUSA_S="0", TEL_CAND_SUBITO=None, TEL_SUBITO_API="1"):   # 14/11: spento di default, qui acceso
            c = Conn()
            st = tr.telefoni_candidate(c, [1, 2])
    finally:
        tr._as_esito, verify._mark, TN.subito_telefono_api, tr._client_as, TN.client_api = salva
        TN.leggi_conto = salva_conto
        TN.PRIORITARI = frozenset()
    assert segnati == [(1, "scomparso", "404")] and st["autoscout24:scomparso"] == 1
    assert st["subito_api:si"] == 1
    assert any(q.startswith("UPDATE listings SET telefono_ok=%s, telefono_at=now()") and p == (True, 2) for q, p in c.scritte)
    assert c.params[2] == [2]                                      # solo le prioritarie per Subito



# ------------------------------------------------------------------ correzioni del giudice (seconda tornata)
def test_truffa_zero_non_diventa_uno():
    vera = {"truffa": 0, "auto_vera": 0.9, "dati_coerenti": 0.9, "danni": 0, "foto_internet": 0,
            "decisione": "compra", "fonte": "claude"}
    base = dict(prezzo=12000, M=17500, margine=5500)
    out, _ = elenco([auto(1, jev=vera, **base)])
    assert [(it["id"], it["avviso_verifica"]) for it in out] == [(1, True)]       # truffa 0 = nessun segno
    assert ids([auto(2, jev=dict(vera, truffa=None), **base)]) == []              # truffa mancante: non confermata
    assert ids([auto(3, jev=dict(vera, foto_internet=None), **base)]) == []
    # N5: con sconto alto la decisione vale solo allo stesso prezzo
    assert ids([auto(4, jev=dict(vera, stesso_prezzo=False), **base)]) == []
    assert ids([auto(5, jev=dict(vera, stesso_prezzo=True), **base)]) == [5]
    # ripiego: errore tipico 0 non diventa 1
    dati = {"motivi": ["solo 1 confronti validi (servono almeno 2)"], "confronti": [{"prezzo_corretto": 9500}]}
    assert "valore_reale" in RP.fonti(dati, {"v": 9000, "sigma": 0.0, "n_vendute": 30}, [])


def test_budget_dosato_e_rinnovi():
    assert CA.budget_dosato(4.0, 0) == round(4 * 2 / 24, 4)
    assert CA.budget_dosato(4.0, 10) == 2.0 and CA.budget_dosato(4.0, 23.5) == 4.0
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    CA._SALTATE.clear()
    cand = [{"id": 1, "price_eur": 5000, "telefono_ok": True, "cat": "opportunita", "margine": 3000, "m": 8000,
             "d_prezzo": 5000, "d_fonte": "jev"},                                   # rinnovo (stesso prezzo)
            {"id": 2, "price_eur": 5000, "telefono_ok": True, "cat": "opportunita", "margine": 2000, "m": 7000,
             "d_prezzo": None}]                                                     # nuova
    auto_ = [{"id": i, "title": "Panda", "description": "ok", "price_eur": 5000, "foto": [], "confronti": []} for i in (1, 2)]
    ok = {"guasto": 0, "prezzo_rata": 0, "km_credibili": 1, "dati_coerenti": 1, "truffa": 0.1, "auto_vera": 0.9,
          "stato": 2, "decisione": "tratta", "confidenza": .6, "note": ""}
    fatte = []

    def chiama(testo, foto):
        return ok, 800, 80, "claude-haiku-5-5"

    class Conn(ConnFinta):
        def __init__(self, spesa_rinnovi):
            super().__init__(cand, auto_)
            self.rin = spesa_rinnovi

        def execute(self, sql, params=None):
            if "FROM ai_usage" in sql:
                return _Ris([{"task": "controllo_auto_rinnovo", "model": "claude-haiku-5-5", "n": 1,
                              "i": int(self.rin * 1e6), "o": 0}] if self.rin else [])
            r = super().execute(sql, params)
            if sql.startswith("INSERT INTO ai_usage"):
                fatte.append(params[0])
            return r
    vecchia = CA.ore_roma
    CA.ore_roma = lambda: 22.0
    try:
        with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="0", CONTROLLO_BUDGET_USD="4"):
            res = CA.giro(Conn(0), chiama=chiama)
            assert fatte == ["controllo_auto", "controllo_auto_rinnovo"]        # prima la nuova, poi il rinnovo
            fatte.clear()
            CA._FALLITE.clear()
            res = CA.giro(Conn(1.3), chiama=chiama)                            # rinnovi già oltre il 30% di 4 $
            assert fatte == ["controllo_auto"] and res["fatte"] == 1
            CA.ore_roma = lambda: 0.0                                           # 4 × 2/24 = 0,33 $ già spesi
            assert "dosato" in CA.giro(Conn(1.3), chiama=chiama)["saltato"]
    finally:
        CA.ore_roma = vecchia


def test_giudice_e_difetti_chiudono_senza_ai():
    import dataclasses
    import types
    from dealfinder import regole as RGM
    from dealfinder.ai import giudice
    from dealfinder.pricing import ricalcolo
    cfg = types.SimpleNamespace(regole=dataclasses.replace(RGM.regole(), giudice_ai=True))
    v = {"categoria": "opportunita", "confronti": [{"id": 1}], "M": 9000, "margine": 3000, "motivi": []}
    salva = (giudice.giudica, AI.available)
    giudice.giudica = lambda conn, t, c, solo_archivio=False: None
    AI.available = lambda: True
    try:
        v2 = ricalcolo._giudice_ai(None, {"id": 5}, v, [], 1500, cfg)
        assert v2["categoria"] == "dati_insufficienti" and v2["giudice_mancante"] and v["categoria"] == "opportunita"
        assert ricalcolo._giudice_ai(None, {"id": 5}, v, [], 1500, cfg, solo_archivio=True) is v
    finally:
        giudice.giudica, AI.available = salva
    # difetti letti su un altro testo: non valgono più, l'auto aspetta la nuova lettura (fuori)
    from dealfinder.ai import difetti
    from dealfinder.pricing import valutazione as VAL
    r = {"id": 9, "title": "Golf", "description": "frizione da fare, motore da sistemare", "make": "vw", "model": "golf",
         "fuel": "benzina", "price_eur": 5000, "mileage_km": 90000, "year": 2015, "telefono_ok": True}
    ing = {"parti": {}, "ai": {}, "vr": {}, "fattori_calibra": None,
           "difetti": {9: {"chiave": difetti.chiave("auto perfetta"), "costo": 0, "grave": False, "motivo": None}}}
    assert VAL.prepara(r, ing)[1] == "danni_da_leggere"
    ing["difetti"][9]["chiave"] = difetti.chiave(r["description"])
    assert VAL.prepara(r, ing)[1] != "danni_da_leggere"


def test_ripiego_tra_le_candidate_solo_se_valido():
    assert "ripiego_prudente" not in CA.CAND_SQL and "ripiego_prudente" in CA.CAND_SQL_RIPIEGO
    for pezzo in ("l2.price_eur = rp.prezzo", "v2.computed_at <= rp.valutazione_at", "(rp.pv->>'margine')::int >="):
        assert pezzo in CA.CAND_SQL_RIPIEGO


def test_giorno_di_roma_per_il_contatore():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    assert TN._oggi() == datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")


def test_senza_giudice_auto_cambiata_non_tiene_il_margine_vecchio():
    from dealfinder.pricing import valutazione as VAL
    v = {"categoria": "dati_insufficienti", "giudice_mancante": True, "n": 12}
    esistente = {"impronta_auto": "prezzo-8000", "impronta_regole": "r1", "n": 12, "computed_at": None,
                 "categoria": "opportunita", "margine": 3000}
    ok, perche = VAL.deve_scrivere(esistente, 12, "prezzo-9000", "r1")       # prezzo salito: impronta diversa
    assert ok and perche == "auto_cambiata" and not VAL.tieni_precedente(v, esistente, perche)   # si scrive "in attesa"
    ok, perche = VAL.deve_scrivere(esistente, 12, "prezzo-8000", "r1")
    assert perche == "dati_pari_o_piu" and VAL.tieni_precedente(v, esistente, perche)            # stessa auto: resta
    assert not VAL.tieni_precedente(v, esistente, "regole_cambiate")
    assert not VAL.tieni_precedente(dict(v, giudice_mancante=False), esistente, "dati_pari_o_piu")


def test_riuso_gratis_anche_col_budget_finito():
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    CA._SALTATE.clear()
    a = {"id": 1, "title": "Panda", "description": "ok", "price_eur": 5200, "foto": [], "confronti": []}
    imp = CA.impronta(a, [])
    cand = [{"id": 1, "price_eur": 5200, "telefono_ok": True, "cat": "opportunita", "margine": 3000, "m": 8000,
             "d_prezzo": 5000, "d_fonte": "claude", "d_impronta": imp}]

    def chiama(testo, foto):
        raise AssertionError("nessuna chiamata pagata col budget finito")
    conn = ConnFinta(cand, [a])
    with env(CONTROLLO_BUDGET_USD="0", CONTROLLO_FOTO_MODO="0"):
        res = CA.giro(conn, chiama=chiama)
    assert res["saltato"].startswith("budget") and res["riusate"] == 1
    assert any(q.startswith("UPDATE decisione_ai SET prezzo") and p == (5200, 1) for q, p in conn.scritte)


def test_autohero_anche_con_zona_cron():
    """13/11: con ZONA_CRON=1 il cron opportunità usciva prima di run() e Autohero non girava mai."""
    import inspect
    from dealfinder.pipeline import collect
    src = inspect.getsource(collect)
    i = src.index('log.warning("ZONA_CRON non riuscita')
    assert "_autohero_giornaliero(Counter())" in src[i:i + 400]


def test_riprova_giudice_rivaluta_le_auto_in_attesa():
    """13/11 notte: le auto 'in attesa' (giudice_mancante) vanno rivalutate quando il budget AI torna disponibile."""
    import inspect
    from dealfinder.web import app
    src = inspect.getsource(app)
    i = src.index("def riprova_giudice")
    blocco = src[i:i + 2500]
    assert "giudice_mancante" in blocco and "budget_finito" in blocco and "valuta_e_scrivi" in blocco
    assert 'name="riprova-giudice"' in src
