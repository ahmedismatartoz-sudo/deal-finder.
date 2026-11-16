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
        assert CA.budget_usd() == 1.3  # 16/11 sera: tetto AI 100 €/mese in tutto


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


def test_ripiego_con_i_fattori_della_calibrazione():
    """15/11 (giudice C3): V al venduto con il fattore misurato sul valore reale (come il sito per valore_reale_modello)
    e prezzi chiesti col fattore della loro fascia; 0,92 fisso solo senza fattori."""
    from dealfinder import regole as RGM
    fat = RGM.fattori_calibrazione({"<5k": {"n": 80, "prezzo_su_M": .87}, "5-10k": {"n": 80, "prezzo_su_M": .91},
                                    "10-20k": {"n": 80, "prezzo_su_M": .95}},
                                   misurate_vr={"5-10k": {"n": 50, "prezzo_su_M": .92},
                                                "10-20k": {"n": 50, "prezzo_su_M": .93}}, per_M=True)
    vr = {"v": 9000, "sigma": .10, "n": 400, "n_vendute": 60}
    dati = {"motivi": ["solo 1 confronti validi"], "n": 1, "confronti": [{"prezzo": 4300, "prezzo_corretto": 4200}]}
    f = RP.fonti(dati, vr, [10500, 10700], fat)
    fv = RGM.fattore_per(9000, fat, vr=True)
    assert fv < 1 and f["valore_reale"] == round(9000 * fv)
    # stessa base della strada valore_reale_modello sul sito
    pv = {"M": 9000, "R": 9000, "R_finale": 9000, "margine": 2500, "metodo": "valore_reale_modello", "M_fonte": "vendite"}
    assert abs(RGM.calibra_vendita(pv, fat)["R"] - f["valore_reale"]) <= 1
    assert f["autohero"] == round(10600 * min(RP.FATTORE_VENDITA, RGM.fattore_per(10600, fat)))     # mai sopra 0,92
    assert f["gemella"] == round(4200 * RGM.fattore_per(4200, fat)) and RGM.fattore_per(4200, fat) < RP.FATTORE_VENDITA
    # senza fattori: come prima (0,92 sui prezzi chiesti, V senza correzione)
    f0 = RP.fonti(dati, vr, [10500, 10700])
    assert f0 == {"valore_reale": 9000, "autohero": round(10600 * .92), "gemella": round(4200 * .92)}
    est, fonti, mot = RP.stima(5000, dict(dati, confronti=[]), vr, [9000, 9100], fat)
    assert mot == "" and est == round(min(fonti.values()) * RP.PRUDENZA)


def test_ripiego_con_le_riserve_sopra_10k_mai_sopra_092():
    """15/11 (ricontrollo C3): nel giro i fattori ci sono sempre (almeno le riserve: 0,97 tra 10 e 20k, 1,0 sopra i 20k,
    misurati sui privati). Autohero e la gemella non devono mai superare il risultato con × 0,92."""
    from dealfinder import regole as RGM
    riserve = RGM.fattori_calibrazione({})
    assert RGM.fattore_per(15000, riserve) > .92 and RGM.fattore_per(30000, riserve) == 1.0    # il caso del giudice
    for p in range(10000, 60001, 2500):
        dati = {"motivi": ["solo 1 confronti validi"], "n": 1, "confronti": [{"prezzo": p, "prezzo_corretto": p}]}
        vr = {"v": p, "sigma": .10, "n": 400, "n_vendute": 60}
        f, f0 = RP.fonti(dati, vr, [p, p], riserve), RP.fonti(dati, vr, [p, p])
        assert f["autohero"] <= f0["autohero"] == round(p * .92) and f["gemella"] <= f0["gemella"] == round(p * .92)
        assert f["valore_reale"] <= f0["valore_reale"]
        e, _f, _m = RP.stima(int(p * .6), dati, vr, [p, p], riserve)
        e0, _f0, _m0 = RP.stima(int(p * .6), dati, vr, [p, p])
        assert e is not None and e <= e0, (p, e, e0)


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
    i = src.index('torino_del_giro(_giro, solo_subito=_zona_letta)')   # 16/11: dopo la zona (blocco ora in giro_zona)
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


def test_sconto_alto_fermo_con_jev_va_in_testa_alla_coda():
    # 14/11 sera: sconto alto con sola decisione Jev e truffa >= ai_pulita (fuori dal sito) -> come un'auto nuova
    base = {"price_eur": 8000, "m": 14000, "d_prezzo": 8000, "d_fonte": "jev", "d_truffa": 0.4}
    assert CA.sconto_fermo(base) and CA.classe(base) == 0
    assert not CA.sconto_fermo(dict(base, d_truffa=0.1))          # Jev pulita: oggi sul sito, non si tocca
    assert not CA.sconto_fermo(dict(base, d_fonte="claude"))       # già deciso da Claude
    assert not CA.sconto_fermo(dict(base, m=10000))               # sconto sotto il limite (4.000 € fino a 10.000)
    assert not CA.sconto_fermo(dict(base, d_prezzo=8500))          # prezzo cambiato: classe 1 come prima
    assert CA.classe(dict(base, d_truffa=0.1)) == 3
    assert not CA.sconto_fermo(dict(base, stage_reason="incidentata"))   # come il sito
    assert not CA.sconto_fermo(dict(base, price_eur=80000, d_prezzo=80000, m=95000))   # supercar: a parte
    p = CA.parametri_diesel()
    assert p["sa_min"] == 4000 and p["sa_pulita"] == 0.30
    assert "%(sa_min)s" in CA.CAND_SQL and "%(sa_pulita)s" in CA.CAND_SQL_RIPIEGO and p["sa_supercar"] == 70000
    assert "risposta->>'truffa'" not in CA.CAND_SQL and "COALESCE(d.truffa, 0)" in CA.CAND_SQL   # colonna REAL


# ------------------------------------------------------------------ 15/11 giudice A1/A6: coda di Claude
_OK_CTRL = {"guasto": 0, "prezzo_rata": 0, "km_credibili": 1, "danni_foto": .05, "foto_internet": .05,
            "targa_visibile": .5, "dati_coerenti": 1, "truffa": 0.1, "auto_vera": 0.9, "stato": 2,
            "decisione": "tratta", "confidenza": .6, "note": ""}


def _risposte_salvate(conn):
    import json
    return [json.loads(p[11]) for q, p in conn.scritte if q.startswith("INSERT INTO decisione_ai")]


def test_decisione_senza_foto_viste_non_si_riusa_e_si_riprova():
    from datetime import datetime, timedelta, timezone
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    CA._SALTATE.clear()
    a = {"id": 1, "title": "Panda", "description": "ok", "price_eur": 5000, "foto": ["https://x/1.jpg"], "n_foto": 8,
         "confronti": []}
    imp = CA.impronta(a, a["foto"])
    ieri = datetime.now(timezone.utc) - timedelta(days=1)
    base = {"id": 1, "price_eur": 5000, "telefono_ok": True, "cat": "opportunita", "margine": 3000, "m": 8000,
            "d_prezzo": 5000, "d_fonte": "claude", "d_impronta": imp, "d_at": ieri}
    assert CA.senza_foto_da_rifare(dict(base, d_senza_foto=True, d_tentativi_foto=1))
    assert not CA.senza_foto_da_rifare(dict(base, d_senza_foto=True, d_tentativi_foto=3))    # 3 tentativi: vale
    assert not CA.senza_foto_da_rifare(dict(base, d_senza_foto=False))
    assert CA.classe(dict(base, d_senza_foto=True, d_tentativi_foto=1)) == 2                  # non definitiva
    assert CA.classe(dict(base, d_senza_foto=True, d_tentativi_foto=3)) == 3
    chiamate = []

    def chiama(testo, foto):
        chiamate.append(len(foto))
        return dict(_OK_CTRL), 800, 80, "claude-haiku-5-5"
    blocco = CA.AI.image_block
    CA.AI.image_block = lambda *a, **k: None                              # download delle foto fallito
    try:
        esiti = []
        for tent in (1, 2, 3):
            chiamate.clear()
            CA._FALLITE.clear()
            conn = ConnFinta([dict(base, d_senza_foto=True, d_tentativi_foto=tent)], [dict(a)])
            with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="base64", CONTROLLO_FOTO="2"):
                esiti.append((tent, conn, CA.giro(conn, chiama=chiama), list(chiamate)))
    finally:
        CA.AI.image_block = blocco
    for tent, conn, res, ch in esiti:
        attesa_chiamata = tent < 3
        chiamate[:] = ch
        if attesa_chiamata:
            assert chiamate == [0] and not res.get("riusate") and res["fatte"] == 1 and res["senza_foto_viste"] == 1
            r = _risposte_salvate(conn)[0]
            assert r["_foto"] == 0 and r["_tentativi_foto"] == tent + 1
        else:
            assert chiamate == [] and res["riusate"] == 1                                    # dopo 3: come oggi
    # auto senza foto (n_foto = 0): nessun contatore, la decisione resta definitiva come oggi
    assert CA.tentativi_foto(dict(a, n_foto=0), None, {"_foto": 0}) is None
    # foto spente (CONTROLLO_FOTO=0 o CONTROLLO_FOTO_MODO=0): riprovare non serve
    for spente in ({"CONTROLLO_FOTO": "0"}, {"CONTROLLO_FOTO_MODO": "0"}):
        with env(**spente):
            assert CA.foto_spente() and not CA.senza_foto_da_rifare(dict(base, d_senza_foto=True, d_tentativi_foto=1))
            assert CA.tentativi_foto(a, dict(base, d_senza_foto=True, d_tentativi_foto=1), {"_foto": 0}) is None
            assert CA.classe(dict(base, d_senza_foto=True, d_tentativi_foto=1)) == 3
    assert CA.tentativi_foto(a, None, {"_foto": 2}) is None                                    # foto viste
    assert CA.tentativi_foto(a, None, {"_foto": 0}) == 1                                       # primo salvataggio
    assert CA.tentativi_foto(a, dict(base, d_senza_foto=True, d_tentativi_foto=2), {"_foto": 0}) == 3
    # la coda: colonna e condizione (6 ore, al massimo 3 tentativi) in entrambe le query
    for sql in (CA.CAND_SQL, CA.CAND_SQL_RIPIEGO):
        assert "AS d_senza_foto" in sql and "AS d_tentativi_foto" in sql
        assert "interval '6 hours'" in sql and "_tentativi_foto', '')::int, 1) < 3" in sql
        assert "FROM listing_photos p WHERE p.listing_id = l.id" in sql


def test_decisione_scaduta_non_si_riusa_e_va_coi_rinnovi():
    from datetime import datetime, timedelta, timezone
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    CA._SALTATE.clear()
    ora = datetime.now(timezone.utc)
    vecchia, fresca = ora - timedelta(days=CA.valida_giorni() + 1), ora - timedelta(days=2)
    assert CA.scaduta(vecchia) and not CA.scaduta(fresca) and not CA.scaduta(None)
    assert CA.scaduta(vecchia.replace(tzinfo=None)) and CA.scaduta(vecchia.isoformat())
    assert CA.riusabile(5000, "i", 5200, "i", fresca) and not CA.riusabile(5000, "i", 5200, "i", vecchia)
    assert CA.riusabile(5000, "i", 5200, "i")                             # data sconosciuta: come prima
    r = {"d_prezzo": 5000, "price_eur": 5200, "d_fonte": "claude", "d_at": vecchia}
    assert CA.classe(r) == 1 and CA.classe(dict(r, d_at=fresca)) == 1    # prezzo cambiato: classe 1 anche se scaduta
    assert CA.classe(dict(r, d_prezzo=5200)) == 3                         # scaduta allo stesso prezzo = rinnovo
    assert CA.classe(dict(r, d_fonte="jev")) == 1                         # Jev: come prima
    assert not CA.riusabile(5000, "i", 5200, "i", vecchia)                # riuso escluso comunque
    a = {"id": 1, "title": "Panda", "description": "ok", "price_eur": 5200, "foto": [], "confronti": []}
    cand = [{"id": 1, "price_eur": 5200, "telefono_ok": True, "cat": "opportunita", "margine": 3000, "m": 8000,
             "d_prezzo": 5200, "d_fonte": "claude", "d_impronta": CA.impronta(a, []), "d_at": vecchia}]
    chiamate = []

    def chiama(testo, foto):
        chiamate.append(1)
        return dict(_OK_CTRL), 800, 80, "claude-haiku-5-5"
    conn = ConnFinta(cand, [dict(a)])
    with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="0"):
        res = CA.giro(conn, chiama=chiama)
    assert not res.get("riusate") and chiamate == [1] and res["fatte"] == 1
    assert any(q.startswith("INSERT INTO ai_usage") and p[0] == CA.TASK_RINNOVO for q, p in conn.scritte)
    # quota dei rinnovi finita: la scaduta aspetta (niente riuso gratis di una decisione scaduta)
    chiamate.clear()

    class ConnRinnoviFiniti(ConnFinta):
        def execute(self, sql, params=None):
            if "FROM ai_usage" in " ".join(sql.split()):
                return _Ris([{"task": CA.TASK_RINNOVO, "model": "claude-haiku-5-5", "n": 1, "i": 4 * 10**8, "o": 0}])
            return super().execute(sql, params)
    conn = ConnRinnoviFiniti(cand, [dict(a)])
    ore = CA.ore_roma
    CA.ore_roma = lambda: 23.0                            # budget dosato = budget intero: conta solo la quota rinnovi
    try:
        with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="0", CONTROLLO_BUDGET_USD="1000"):
            res = CA.giro(conn, chiama=chiama)            # 400 $ di rinnovi su 1.000: quota (30%) finita
    finally:
        CA.ore_roma = ore
    assert "saltato" not in res and chiamate == [] and not res.get("riusate")


def test_pg_coda_senza_foto_e_scadute():
    """Postgres vero: CAND_SQL con decisioni senza foto viste (6 ore, 3 tentativi) e decisioni con foto."""
    import json
    import sys as _s
    _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pg_vero
    c = pg_vero.nuovo_db("piu_auto_coda_foto")
    if c is None:
        return
    pg_vero.psql(c.d, c.db, """
        CREATE TABLE listings (id BIGINT PRIMARY KEY, status TEXT, price_eur INT, telefono_ok BOOLEAN, seller_type TEXT,
          fuel TEXT, stage TEXT, stage_reason TEXT, province TEXT, year INT, mileage_km INT, damage_declared BOOLEAN);
        CREATE TABLE top_auto (listing_id BIGINT);
        CREATE TABLE listing_photos (listing_id BIGINT, source_url TEXT, position INT);
        CREATE TABLE valutazioni_policy (listing_id BIGINT, categoria TEXT, margine INT, riassunto JSONB, dati JSONB,
          computed_at TIMESTAMPTZ DEFAULT now());
        CREATE TABLE decisione_ai (listing_id BIGINT PRIMARY KEY, prezzo INT, risposta JSONB, fonte TEXT, impronta TEXT,
          truffa REAL, computed_at TIMESTAMPTZ);""")
    nul = {"type": "noul", "noul": None}
    vista = {"type": "noul", "noul": .05}
    casi = {   # id: (foto nell'annuncio, risposta, ore fa)
        1: (True, {"guasto": vista, "danni_foto": nul, "foto_internet": nul}, 7),                 # vecchia, senza foto
        2: (True, {"guasto": vista, "danni_foto": nul, "foto_internet": nul}, 2),                 # meno di 6 ore
        3: (True, {"guasto": vista, "danni_foto": nul, "foto_internet": nul, "_foto": 0, "_tentativi_foto": 3}, 7),
        4: (True, {"guasto": vista, "danni_foto": vista, "foto_internet": vista, "_foto": 2}, 7),  # foto viste
        5: (False, {"guasto": vista, "danni_foto": nul, "foto_internet": nul}, 7),                # auto senza foto
        6: (True, {"guasto": vista, "danni_foto": nul, "foto_internet": nul, "_foto": 0, "_tentativi_foto": 2}, 7),
        7: (True, {"guasto": vista, "danni_foto": nul, "foto_internet": nul, "_foto": 2}, 7),     # foto viste, null
    }
    for i, (foto, risp, ore) in casi.items():
        pg_vero.psql(c.d, c.db, f"""
            INSERT INTO listings VALUES ({i}, 'attivo', 5000, true, 'privato', 'benzina', 'approfondito', 'opportunita',
                                         'MI', 2018, 80000, false);
            INSERT INTO top_auto VALUES ({i});
            INSERT INTO valutazioni_policy (listing_id, categoria, margine, riassunto, dati)
              VALUES ({i}, 'opportunita', 3000, '{{"M": 8000}}', '{{"soglia": 1500}}');
            INSERT INTO decisione_ai VALUES ({i}, 5000, '{json.dumps(risp)}', 'claude', 'imp', 0.1,
                                             now() - interval '{ore} hours');""")
        if foto:
            pg_vero.psql(c.d, c.db, f"INSERT INTO listing_photos VALUES ({i}, 'https://x/{i}.jpg', 0)")
    par = dict(CA.parametri_diesel(), quota=0.7, validi=CA.valida_giorni())
    righe = {r["id"]: r for r in c.execute(CA.CAND_SQL, par).fetchall()}
    assert set(righe) == {1, 6}, sorted(righe)
    assert righe[1]["d_senza_foto"] is True and righe[1]["d_tentativi_foto"] == 1 and CA.classe(righe[1]) == 2
    assert righe[6]["d_tentativi_foto"] == 2 and CA.senza_foto_da_rifare(righe[6])
    # scaduta: torna in coda (e conta come rinnovo)
    pg_vero.psql(c.d, c.db, f"UPDATE decisione_ai SET computed_at = now() - interval '{CA.valida_giorni() + 1} days' "
                            "WHERE listing_id = 4")
    righe = {r["id"]: r for r in c.execute(CA.CAND_SQL, par).fetchall()}
    assert set(righe) == {1, 4, 6} and righe[4]["d_senza_foto"] is False and CA.classe(righe[4]) == 3


# ------------------------------------------------------------------ 15/11 giudice A2/A7: giudice e riscrittura
class _ConnGiudizi:
    """ai_giudizi in memoria (con la data), ai_usage con commit e rollback veri."""
    def __init__(self, rompi=False):
        self.giud, self.usage, self.pend, self.rompi = {}, [], [], rompi

    def execute(self, sql, p=()):
        import json
        s = " ".join(sql.split())
        righe = []
        if s.startswith("SELECT chiave, via, nota"):
            from datetime import datetime, timezone
            righe = [{"chiave": k, "via": json.loads(v), "nota": n, "at": datetime.fromtimestamp(at, timezone.utc)}
                     for (l, k), (v, n, at) in self.giud.items() if l == p[0]]
        elif s.startswith("INSERT INTO ai_usage"):
            self.pend.append(("usage", p))
        elif s.startswith("INSERT INTO ai_giudizi"):
            if self.rompi:
                raise RuntimeError("db giù")
            self.pend.append(("giud", p, "DO UPDATE" in s and "WHERE" not in s.split("DO UPDATE")[1]))
        return _Ris(righe)

    def commit(self):
        import json
        for x in self.pend:
            if x[0] == "usage":
                self.usage.append(x[1])
            else:
                (lid, chiave, via, nota), sempre = x[1], x[2]
                vecchia = self.giud.get((lid, chiave))
                if vecchia is None or sempre or json.loads(vecchia[0]).get("_illeggibile"):
                    self.giud[(lid, chiave)] = (via, nota, time.time())
        self.pend = []

    def rollback(self):
        self.pend = []


def _finto_ai(risposte):
    import types
    chiamate = []

    def crea(**k):
        chiamate.append(1)
        txt, stop = risposte.pop(0)
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=txt)], stop_reason=stop,
                                     usage=types.SimpleNamespace(input_tokens=1000, output_tokens=50))
    return crea, chiamate


def test_giudice_illeggibile_archiviato_spesa_subito_troncata_none():
    import json
    from dealfinder.ai import giudice as GI
    prima = (AI.available, AI.budget_finito, AI.crea)
    t = {"id": 1, "price_eur": 8000, "make": "VW", "model": "Golf"}
    conf = [{"id": i, "prezzo": 9000} for i in range(10, 16)]
    try:
        AI.available = lambda: True
        AI.budget_finito = lambda c: False
        # (a) illeggibile: archiviata, la seconda volta nessuna nuova chiamata (6 ore)
        AI.crea, chiamate = _finto_ai([("Tutti i confronti sono validi.", "end_turn")] * 2)
        c = _ConnGiudizi()
        assert GI.giudica(c, t, conf) is None and GI.giudica(c, t, conf) is None and len(chiamate) == 1
        (via, _n, _at), = c.giud.values()
        assert json.loads(via)["_illeggibile"] and "_giudicati" not in json.loads(via) and len(c.usage) == 1
        # stessa auto a un altro prezzo: l'attesa non vale (il prezzo fa parte della chiave)
        AI.crea, chiamate = _finto_ai([("VIA 10 versione diversa\nNOTA ok", "end_turn")])
        assert GI.giudica(c, dict(t, price_eur=7500), conf)["via"] == {10: "versione diversa"}
        # dopo 6 ore si riprova, e il giudizio vero sostituisce la riga illeggibile con la stessa chiave
        for k, (v, n, at) in list(c.giud.items()):
            c.giud[k] = (v, n, at - 7 * 3600)
        AI.crea, chiamate = _finto_ai([("VIA 11 cabrio\nNOTA ok", "end_turn")])
        g = GI.giudica(c, t, conf)
        c.commit()                                                    # il giudizio lo conferma chi chiama
        assert len(chiamate) == 1 and g["via"] == {11: "cabrio"}
        assert not any(json.loads(v).get("_illeggibile") for (l, _k), (v, _n, _a) in c.giud.items() if l == 1)
        # (b) la spesa si conferma prima di leggere la risposta: un errore nel salvataggio non la cancella
        AI.crea, chiamate = _finto_ai([("VIA 10 versione diversa\nNOTA ok", "end_turn")])
        c = _ConnGiudizi(rompi=True)
        assert GI.giudica(c, t, conf) is None and len(c.usage) == 1
        # (c) troncata (max_tokens): None, niente "validi" per i confronti non letti; archiviata come illeggibile
        AI.crea, chiamate = _finto_ai([("VIA 10 versione diversa\nVIA 1", "max_tokens")])
        c = _ConnGiudizi()
        assert GI.giudica(c, t, conf) is None and len(c.usage) == 1
        (via, _n, _at), = c.giud.values()
        assert json.loads(via) == {"_illeggibile": True, "_motivo": "troncata", "_prezzo": 8000}
        assert GI.giudica(c, t, conf) is None and len(chiamate) == 1                   # niente ripagamento
    finally:
        AI.available, AI.budget_finito, AI.crea = prima


def test_riga_in_attesa_del_giudice_si_sostituisce_sempre():
    from datetime import datetime, timedelta, timezone
    from dealfinder.pricing import valutazione as VAL
    ora = datetime.now(timezone.utc)
    attesa = {"n": 8, "impronta_auto": "a", "impronta_regole": "r", "computed_at": ora - timedelta(hours=1), "tolti": 0,
              "in_attesa": True}
    for n, tolti in ((8, 0), (7, 0), (6, 2), (3, 0)):
        ok, perche = VAL.deve_scrivere(attesa, n, "a", "r", ora, giudicata_nuova=VAL.giudicata({"ai_confronti": {"tolti": tolti}}))
        assert ok and perche == "in_attesa_giudice", (n, tolti, perche)
    vera = dict(attesa, in_attesa=False)
    assert VAL.deve_scrivere(vera, 7, "a", "r", ora) == (False, "meno_confronti")       # contro una vera: come prima
    assert VAL.deve_scrivere(dict(vera, tolti=2), 8, "a", "r", ora) == (False, "giudicata_protetta")
    assert not VAL.tieni_precedente({"giudice_mancante": True}, attesa, "in_attesa_giudice")
    # anche nella condizione del database e nelle righe lette per la guardia
    assert "OR (valutazioni_policy.dati->>'giudice_mancante') = 'true'" in VAL.UPSERT_SQL
    import inspect
    assert "AS in_attesa" in inspect.getsource(VAL._salvate)
    # riprova del giudice: esce dall'attesa solo se riscritta e non di nuovo "in attesa"
    es = VAL.Esito()
    es.scritte |= {1, 2, 3}
    es.salvata.update({1: "opportunita", 2: "dati_insufficienti", 3: "dati_insufficienti", 4: "dati_insufficienti"})
    es.valutazioni.update({2: {"giudice_mancante": True}, 3: {}, 4: {}})
    assert [VAL.esce_dall_attesa(es, i) for i in (1, 2, 3, 4)] == [True, False, True, False]


def test_pg_riga_in_attesa_sostituita_nel_database():
    import json
    import sys as _s
    _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pg_vero
    from dealfinder.pricing import valutazione as VAL
    c = pg_vero.nuovo_db("piu_auto_attesa")
    if c is None:
        return
    pg_vero.psql(c.d, c.db, """CREATE TABLE valutazioni_policy (listing_id BIGINT, policy_version TEXT, categoria TEXT,
        margine INT, riassunto JSONB, dati JSONB, prima JSONB, n INT, impronta_auto TEXT, impronta_regole TEXT, fonte TEXT,
        metodo TEXT, computed_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (listing_id, policy_version))""")

    def scrivi(lid, dati, n, tolti_protetti=True, giudicata=False):
        return c.execute(VAL.UPSERT_SQL, (lid, "v2", dati.get("categoria"), 1000, "{}", json.dumps(dati), None, n, "a", "r",
                                          "prova", "mediana", 36, tolti_protetti, giudicata)).fetchall()
    scrivi(1, {"categoria": "dati_insufficienti", "giudice_mancante": True}, 8)
    scrivi(2, {"categoria": "opportunita"}, 8)
    assert scrivi(1, {"categoria": "opportunita", "ai_confronti": {"tolti": 0}}, 6)          # meno confronti: scritta
    assert not scrivi(2, {"categoria": "opportunita"}, 6)                                     # riga vera: rifiutata


def test_riprova_giudice_segna_viste_solo_le_uscite_dall_attesa():
    """15/11 (giudice A2): prima tutte le auto lette finivano in _visti, anche se il giudice era ancora fermo."""
    import inspect
    from dealfinder.web import app
    src = inspect.getsource(app)
    i = src.index("def riprova_giudice")
    blocco = src[i:src.index("def perizia_ai", i)]
    assert "_visti.update(ids)" not in blocco and "esce_dall_attesa as _esce" in blocco
    assert "_esce(es, lid) or _tentativi[lid] >= _max_tent" in blocco
    assert 'os.environ.get("RIPROVA_GIUDICE_TENTATIVI", "3")' in blocco



def test_senza_foto_al_massimo_una_quota_per_giro():
    """15/11 (ricontrollo A1): l'arretrato delle decisioni senza foto non si prende il giro."""
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    CA._SALTATE.clear()
    auto_, cand = [], []
    for i in range(1, 16):
        a = {"id": i, "title": "Panda", "description": "ok", "price_eur": 5000, "foto": ["https://x/%d.jpg" % i],
             "n_foto": 8, "confronti": []}
        auto_.append(a)
        cand.append({"id": i, "price_eur": 5000, "telefono_ok": True, "cat": "opportunita", "margine": 3000, "m": 8000,
                     "d_prezzo": 5000, "d_fonte": "claude", "d_impronta": CA.impronta(a, a["foto"]), "d_at": None,
                     "d_senza_foto": True, "d_tentativi_foto": 1})
    cand.append({"id": 99, "price_eur": 5000, "telefono_ok": True, "cat": "opportunita", "margine": 3000, "m": 8000,
                 "d_prezzo": None})
    auto_.append({"id": 99, "title": "Golf", "description": "ok", "price_eur": 5000, "foto": [], "confronti": []})
    chiamate = []

    def chiama(testo, foto):
        chiamate.append(1)
        return dict(_OK_CTRL), 800, 80, "claude-haiku-5-5"
    blocco = CA.AI.image_block
    CA.AI.image_block = lambda *a, **k: None
    try:
        with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="base64", CONTROLLO_SENZA_FOTO_PER_GIRO=None):
            assert CA.senza_foto_per_giro() == 10
            res = CA.giro(ConnFinta(cand, auto_), chiama=chiama)
    finally:
        CA.AI.image_block = blocco
    assert res["senza_foto_in_giro"] == 10 and res["senza_foto_rimandate"] == 5 and len(chiamate) == 11   # + l'auto nuova
    assert not res.get("riusate")                                          # le rimandate restano in coda, non riusate


def test_senza_foto_in_eccesso_non_tolgono_il_posto_ai_rinnovi():
    """15/11 (giudice): 100 decisioni senza foto e 5 rinnovi: i rinnovi entrano nel giro."""
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    CA._SALTATE.clear()
    from datetime import datetime, timedelta, timezone
    vecchia = datetime.now(timezone.utc) - timedelta(days=60)
    auto_, cand = [], []
    for i in range(1, 106):
        a = {"id": i, "title": "Panda", "description": "ok", "price_eur": 5000, "foto": ["https://x/%d.jpg" % i],
             "n_foto": 8, "confronti": []}
        auto_.append(a)
        r = {"id": i, "price_eur": 5000, "telefono_ok": True, "cat": "opportunita", "margine": 3000, "m": 8000,
             "d_prezzo": 5000, "d_fonte": "claude", "d_impronta": "diversa", "d_at": vecchia}
        if i <= 100:
            r.update(d_senza_foto=True, d_tentativi_foto=1)
        cand.append(r)
    fatti = []

    def chiama(testo, foto):
        fatti.append(1)
        return dict(_OK_CTRL), 800, 80, "claude-haiku-5-5"
    with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="base64", CONTROLLO_SENZA_FOTO_PER_GIRO=None):
        res = CA.giro(ConnFinta(cand, auto_), chiama=chiama)
    assert res["senza_foto_in_giro"] == 10 and res["senza_foto_rimandate"] == 90
    assert len(fatti) == 15                                               # 10 senza foto + i 5 rinnovi


def test_giudice_registra_l_esito_e_riprova_non_conta_i_blocchi():
    import inspect
    from dealfinder.ai import giudice as GI
    from dealfinder.web import app
    prima = (AI.available, AI.budget_finito, AI.crea)
    t = {"id": 7, "price_eur": 8000}
    conf = [{"id": 1}, {"id": 2}]
    try:
        AI.available = lambda: True
        AI.budget_finito = lambda c: True
        t0 = time.time()
        assert GI.giudica(_ConnGiudizi(), t, conf) is None and GI.esito(7, t0) == "budget"
        AI.budget_finito = lambda c: False
        AI.crea, _ch = _finto_ai([("bla", "end_turn")])
        c = _ConnGiudizi()
        assert GI.giudica(c, t, conf) is None and GI.esito(7) == "chiamata"            # pagata, archiviata illeggibile
        assert GI.giudica(c, t, conf) is None and GI.esito(7) == "attesa_illeggibile"
        AI.available = lambda: False
        assert GI.giudica(c, t, conf) is None and GI.esito(7) == "non_disponibile"
        assert GI.esito(7, time.time() + 10) is None                                    # esito vecchio: non vale
        assert set(GI.BLOCCATO) == {"budget", "attesa_illeggibile", "non_disponibile"}
    finally:
        AI.available, AI.budget_finito, AI.crea = prima
    src = inspect.getsource(app)
    i = src.index("def riprova_giudice")
    blocco = src[i:src.index("def perizia_ai", i)]
    j = blocco.index("_GI.esito(lid, _t0) in _GI.BLOCCATO")
    assert j < blocco.index("_tentativi[lid] = _tentativi.get(lid, 0) + 1")           # prima di contare il tentativo
    assert "_visti.clear()" in blocco and 'RIPROVA_GIUDICE_AZZERA_ORE", "12"' in blocco


def test_tetto_giudice_sulle_righe_in_attesa_nel_cron():
    from datetime import datetime, timezone
    from dealfinder.pricing import valutazione as VAL
    from tests import test_prezzo_vero as TPV
    with env(GIUDICE_ATTESA_PER_GIRO=None):
        assert VAL.tetto_attesa("cron") == 150 and VAL.tetto_attesa("cron-utilitarie") == 150
        assert VAL.tetto_attesa("riprova-giudice") is None and VAL.tetto_attesa("web-nuovi") is None
    attesa = {1: {"n": 8, "impronta_auto": "x", "impronta_regole": "y", "computed_at": datetime.now(timezone.utc),
                  "categoria": "dati_insufficienti", "in_attesa": True}}
    with env(GIUDICE_ATTESA_PER_GIRO="0"):                                 # tetto già pieno: resta in attesa
        c = TPV.ConnFinta(TPV.AUTO, TPV.gemelle(TPV.BASE), salvate=dict(attesa))
        es = VAL.valuta_e_scrivi(c, [1], "cron")
        assert c.scritte == [] and es.stats["attesa_rimandata_tetto_giudice"] == 1
        assert es.salvata[1] == "dati_insufficienti"
        c = TPV.ConnFinta(TPV.AUTO, TPV.gemelle(TPV.BASE), salvate=dict(attesa))
        VAL.valuta_e_scrivi(c, [1], "web-nuovi")                            # fuori dal cron: nessun tetto
        assert len(c.scritte) == 1
