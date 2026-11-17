"""17/11 (notte-03): il budget del controllo AI va prima alle auto che entrerebbero sul sito.

- coda_dal_sito: dalle auto tolte SOLO per "sconto_alto_da_controllare", quelle che passerebbero tutti gli altri passi;
  le auto tolte a un passo che il controllo non cambia (zona, venditore, margine minimo...) non si pagano;
- niente spreco: rinnovo con lo stesso contenuto, sconto alto senza foto, chiamate ripagate dopo un riavvio;
- misura: auto entrate per dollaro.
Stesse regole del sito (stesse soglie di truffa e ai_pulita): cambia solo l'ordine e chi si paga.
"""
from __future__ import annotations

import dataclasses
import os
import time
from datetime import datetime, timedelta, timezone

from dealfinder.ai import client as AI
from dealfinder.ai import controllo_auto as CA
from dealfinder.web import scovo
from dealfinder.web import sito_regole as SR

G = SR.Regole()
OK = {"guasto": 0, "prezzo_rata": 0, "km_credibili": 1, "danni_foto": .05, "foto_internet": .05,
      "targa_visibile": .5, "dati_coerenti": 1, "truffa": 0.1, "auto_vera": 0.9, "stato": 2,
      "decisione": "tratta", "confidenza": .6, "note": ""}


class env:
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


def _pulisci():
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    CA._SALTATE.clear()
    CA.CODA_SITO.update(t=0.0, entra=[], fuori=set(), sul_sito=set(), motivi={})
    CA.MISURA.update(giorno=None)


def auto(i, prezzo=8000, margine=3500, M=13000, prov="MI", tel=True, seller="privato", jev=None, **kw):
    r = {"tel": tel, "seller_type": seller, "provincia": prov, "jev": jev}
    r.update(kw.pop("_r", {}))
    it = {"id": i, "prezzo": prezzo, "anno": 2015, "km": 90000, "carb": "Diesel", "fonte": "Subito",
          "pv": {"categoria": "opportunita", "M": M, "R": prezzo + margine + 200, "R_finale": prezzo + margine + 200,
                 "margine": margine, "totale_costi": 200, "n": 10},
          "_r": r}
    it.update(kw)
    return it


class _Ris:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Conn:
    """CAND_SQL → cand, CAND_SQL_SITO (unnest) → sito, AUTO_SQL → auto; ai_usage: spesa e chiamate fallite."""
    def __init__(self, cand, auto_, sito=(), fallite=(), spesa_usd=0.0):
        self.cand, self.auto, self.sito, self.fallite, self.spesa = list(cand), list(auto_), list(sito), fallite, spesa_usd
        self.scritte, self.sql = [], []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.sql.append(s[:60])
        if s.startswith(("ALTER", "UPDATE", "INSERT")):
            self.scritte.append((s, params))
            return _Ris([])
        if "SELECT DISTINCT u.listing_id FROM ai_usage" in s:
            return _Ris([{"listing_id": i} for i in self.fallite])
        if "FROM ai_usage" in s:
            return _Ris([{"task": CA.TASK, "model": "claude-haiku-5-5", "n": 1, "i": int(self.spesa * 1e6), "o": 0}]
                        if self.spesa else [])
        if "information_schema" in s:
            return _Ris([{"n": 2}])
        if "unnest(" in s:
            ids = set(params["sito_ids"])
            return _Ris([dict(r) for r in self.sito if r["id"] in ids])
        if "WITH cand AS" in s:
            return _Ris([dict(r) for r in self.cand])
        if "to_regclass" in s:
            return _Ris([{"t": None}])
        if "FROM listings l" in s:
            ids = set(params["ids"])
            return _Ris([dict(a) for a in self.auto if a["id"] in ids])
        return _Ris([])

    def commit(self):
        pass

    def rollback(self):
        pass


def riga(i, **kw):
    r = {"id": i, "price_eur": 5000, "telefono_ok": True, "cat": "opportunita", "margine": 3000, "m": 7000,
         "soglia": 1500, "d_prezzo": None, "d_impronta": None, "d_fonte": None, "d_at": None}
    r.update(kw)
    return r


def scheda(i, n_foto=2, **kw):
    a = {"id": i, "title": "Panda", "description": "ok", "price_eur": 5000, "foto": [f"https://x/{i}-{k}.jpg"
                                                                                  for k in range(n_foto)],
         "n_foto": n_foto, "confronti": [], "cat": "opportunita", "margine": 3000}
    a.update(kw)
    return a


# ------------------------------------------------------------------ coda dal sito
def test_coda_dal_sito_solo_le_auto_che_entrerebbero():
    _pulisci()
    items = [auto(1),                                         # sconto 5.000 € oltre 4.000: fuori, passerebbe il resto
             auto(2, margine=200),                            # sconto alto ma sotto il margine minimo: non entra
             auto(3, _r={"perizia": {"giudizio": "caro"}}),   # perizia "caro": la conferma è impossibile
             auto(4, M=10000),                                # sconto 2.000: già sul sito senza AI
             auto(5, prov="RE"),                              # fuori zona: nessuna chiamata pagata
             auto(6, tel=False)]                              # senza numero: idem
    out, fun = SR.elenco_sito([dict(it, _r=dict(it["_r"]), pv=dict(it["pv"])) for it in items], G, scrivi_log=False)
    assert {it["id"] for it in out} == {4}
    assert set(fun.tolti["truffa"]) == {1, 2, 3}
    res = CA.coda_dal_sito(items, out, fun, G)
    assert CA.CODA_SITO["entra"] == [1]
    assert res["non_entrerebbe:margine_minimo"] == 1 and res["non_entrerebbe:perizia"] == 1
    assert {5, 6} <= CA.CODA_SITO["fuori"] and 1 not in CA.CODA_SITO["fuori"] and CA.CODA_SITO["sul_sito"] == {4}
    assert CA.coda_sito_fresca() is not None
    CA.CODA_SITO["t"] = time.time() - 31 * 60                 # elenco vecchio: il giro fa come prima
    assert CA.coda_sito_fresca() is None
    _pulisci()


def test_conferma_con_la_decisione_claude_entra_davvero():
    """Il controllo della coda è la stessa conferma del sito: con una risposta pulita l'auto 1 entra (con l'avviso)."""
    j = {"fonte": "claude", "danni": .05, "foto_internet": .05, "truffa": .1, "auto_vera": .9, "dati_coerenti": 1,
         "decisione": "tratta", "stesso_prezzo": True, "km_ok": 1, "guasto": 0}
    out, _f = SR.elenco_sito([auto(1, jev=j)], G, scrivi_log=False)
    assert [it["id"] for it in out] == [1] and out[0]["avviso_verifica"]
    assert CA.confermabile(dict(OK), G)
    assert not CA.confermabile(dict(OK, truffa=0.3), G)               # stessa soglia ai_pulita del sito
    assert not CA.confermabile(dict(OK, danni_foto=None), G)          # foto non viste
    assert not CA.confermabile(dict(OK, km_credibili=.2), G)          # esce al passo jev
    assert not CA.confermabile(dict(OK, auto_vera=.6), G)


def test_tetto_commercianti_pieno_non_conta_come_entrata():
    g = dataclasses.replace(G, commercianti_totale=2)
    it = auto(1, _tipo="comm_fascia", commerciante=True)
    assert CA._non_entrerebbe(it, g, [3000, 4000], []) == "tetto_commercianti"     # entrerebbe al posto di un'altra
    assert CA._non_entrerebbe(it, g, [3000], []) is None
    assert CA._non_entrerebbe(dict(it, diesel_extra=True), g, [3000, 4000], []) is None   # tetto a parte


def test_giro_prima_la_coda_del_sito_e_niente_spesa_per_le_fuori():
    _pulisci()
    CA.CODA_SITO.update(t=time.time(), entra=[9], fuori={2}, sul_sito={1})
    cand = [riga(1), riga(2)]                                  # 1 in gioco, 2 fuori zona
    sito = [riga(9, price_eur=8000, m=13000, margine=3500, d_prezzo=8000, d_fonte="jev", d_truffa=0.4)]
    schede = [scheda(1), scheda(2), scheda(9, price_eur=8000)]
    ordine = []

    def chiama(testo, foto):
        ordine.append("8000" in testo)
        return dict(OK), 800, 80, "claude-haiku-5-5"
    with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="url"):
        prima = AI.image_url_block
        AI.image_url_block = lambda src: {"type": "image", "source": {"type": "url", "url": src}}
        try:
            res = CA.giro(Conn(cand, schede, sito), chiama=chiama)
        finally:
            AI.image_url_block = prima
    assert ordine == [True, False]                             # prima l'auto del sito, poi la 1; la 2 mai
    assert res["fuori_per_altro"] == 1 and res["sito_entra"] == 1 and res["sito_da_fare"] == 1
    assert res["sito_fatte"] == 1 and res["sito_confermabili"] == 1 and res["sito_usd"] > 0
    # misura: al prossimo elenco l'auto 9 è sul sito → 1 entrata; la 1 era già sul sito e non conta
    fun = SR.Funnel()
    fun.tolti = {}
    CA.coda_dal_sito([], [{"id": 9}, {"id": 1}], fun, G)
    assert CA.MISURA["entrate"] == {9} and CA.MISURA["entrate_sito"] == {9}
    res = CA.giro(Conn([], []), chiama=chiama)
    _pulisci()


def test_coda_del_sito_anticipa_sul_dosato_senza_superare_il_budget():
    _pulisci()
    CA.CODA_SITO.update(t=time.time(), entra=[9], fuori=set(), sul_sito=set())
    cand = [riga(1)]
    sito = [riga(9, price_eur=8000, m=13000, margine=3500)]
    schede = [scheda(1), scheda(9, price_eur=8000)]
    fatte = []

    def chiama(testo, foto):
        fatte.append("8000" in testo)
        return dict(OK), 800, 80, "claude-haiku-5-5"
    vecchia, prima = CA.ore_roma, AI.image_url_block
    CA.ore_roma = lambda: 0.0                                  # dosato = 1,5 × 2/24 = 0,125 $
    AI.image_url_block = lambda src: {"type": "image", "source": {"type": "url", "url": src}}
    try:
        with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="url", CONTROLLO_BUDGET_USD="1.5"):
            res = CA.giro(Conn(cand, schede, sito, spesa_usd=0.2), chiama=chiama)
            assert fatte == [True] and res["sito_fatte"] == 1         # oltre il dosato: solo l'auto del sito
            fatte.clear()
            res = CA.giro(Conn(cand, schede, sito, spesa_usd=0.9), chiama=chiama)   # oltre 0,125 + 0,75
            assert fatte == [] and "dosato" in res["saltato"]
            fatte.clear()
            CA.CODA_SITO.update(entra=[])                             # senza coda del sito: come prima
            res = CA.giro(Conn(cand, schede, [], spesa_usd=0.2), chiama=chiama)
            assert fatte == [] and "dosato" in res["saltato"]
    finally:
        CA.ore_roma, AI.image_url_block = vecchia, prima
    _pulisci()


# ------------------------------------------------------------------ niente spreco
def test_rinnovo_con_lo_stesso_contenuto_non_si_paga():
    _pulisci()
    vecchia = datetime.now(timezone.utc) - timedelta(days=CA.valida_giorni() + 1)
    a = scheda(1, n_foto=0, foto=[])
    imp = CA.impronta(a, [])
    cand = [riga(1, d_prezzo=5000, d_fonte="claude", d_impronta=imp, d_at=vecchia)]

    def chiama(testo, foto):
        raise AssertionError("rinnovo identico: nessuna chiamata")
    conn = Conn(cand, [a])
    with env(CONTROLLO_FOTO_MODO="0"):
        res = CA.giro(conn, chiama=chiama)
    assert res["rinnovi_inutili_evitati"] == 1
    assert any("_verificata_at" in q and p == (1,) for q, p in conn.scritte)
    # ...ma non diventa riusabile a un prezzo nuovo (giudice A6) e la senza foto viste si rifà comunque
    assert not CA.riusabile(5000, imp, 5200, imp, vecchia)
    r = dict(cand[0], d_senza_foto=True, d_tentativi_foto=1)
    with env(CONTROLLO_FOTO_MODO="base64"):
        assert not CA.rinnovo_inutile(r, dict(a, _imp=imp))
    assert not CA.rinnovo_inutile(dict(cand[0], d_impronta="altro"), dict(a, _imp=imp))
    assert "_verificata_at" in CA.CAND_SQL and "_verificata_at" in CA.CAND_SQL_SITO


def test_sconto_alto_mai_pagato_senza_foto():
    _pulisci()
    cand = [riga(1, price_eur=8000, m=13000), riga(2, price_eur=8000, m=13000)]
    schede = [scheda(1, n_foto=0, foto=[], price_eur=8000), scheda(2, price_eur=8000)]
    chiamate = []

    def chiama(testo, foto):
        chiamate.append(len(foto))
        return dict(OK), 800, 80, "claude-haiku-5-5"
    prima = (AI.image_block, AI.image_url_block)
    AI.image_block = lambda src, http=None, side=None: None            # le foto non si scaricano
    AI.image_url_block = lambda src: None
    try:
        with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="base64"):
            res = CA.giro(Conn(cand, schede), chiama=chiama)
            assert chiamate == [] and res["senza_chiamata:sconto_alto_senza_foto"] == 1
            assert res["foto_non_scaricate_senza_spesa"] == 1 and not res.get("non_valida")
            # senza sconto alto si fa come prima: senza foto, pagata
            CA._FALLITE.clear()
            CA._SALTATE.clear()
            res = CA.giro(Conn([riga(3)], [scheda(3)]), chiama=chiama)
            assert chiamate == [0] and res["fatte"] == 1
            # base64 non va ma gli indirizzi sì: si mandano gli indirizzi
            AI.image_url_block = lambda src: {"type": "image", "source": {"type": "url", "url": src}}
            r = CA.controlla({"id": 2}, ["https://x/1.jpg", "https://x/1.jpg", "https://x/2.jpg"], chiama=chiama,
                             serve_foto=True)
            assert r["x"] is not None and chiamate[-1] == 2 and r["x"]["_foto"] == 2
    finally:
        AI.image_block, AI.image_url_block = prima
    _pulisci()


def test_chiamate_fallite_non_si_ripagano_dopo_un_riavvio():
    _pulisci()
    chiamate = []

    def chiama(testo, foto):
        chiamate.append(1)
        return dict(OK), 800, 80, "claude-haiku-5-5"
    with env(CONTROLLO_PARALLELI="1", CONTROLLO_FOTO_MODO="0"):
        res = CA.giro(Conn([riga(1), riga(2)], [scheda(1), scheda(2)], fallite={1}), chiama=chiama)
    assert chiamate == [1] and res["fallite_da_db"] == 1 and res["fatte"] == 1
    _pulisci()


def test_testo_piu_corto_e_risposta_illeggibile_nel_log():
    a = {"description": "x" * 5000, "title": "t"}
    t = CA.testo_richiesta(a, [{"versione": f"v{i}"} for i in range(10)])
    assert "x" * 1400 in t and "x" * 1401 not in t and "6. versione v5" in t and "7. versione" not in t
    with env(CONTROLLO_TESTO_MAX="1800", CONTROLLO_CONFRONTI_MAX="10"):
        t = CA.testo_richiesta(a, [{"versione": f"v{i}"} for i in range(10)])
        assert "x" * 1800 in t and "10. versione v9" in t

    def chiama(testo, foto):
        return CA.ControlloNonValido("nessun JSON nella risposta [end_turn] 'Non posso'"), 900, 20, "claude-haiku-5-5"
    with env(CONTROLLO_FOTO_MODO="0"):
        r = CA.controlla({"id": 1}, [], chiama=chiama)
    assert "Non posso" in str(r["errore"]) and len(r["token"]) == 1


def test_ordine_senza_coda_sito_stima_chi_entra():
    _pulisci()
    righe = [riga(1, margine=1000), riga(2, margine=1600), riga(3, margine=900, telefono_ok=False)]
    assert [r["id"] for r in CA.ordina(righe)] == [2, 1, 3]
    assert CA.entra_stimata(riga(1, margine=1500)) and not CA.entra_stimata(riga(1, margine=1499))
    assert scovo.soglia(5000) > 0


# ------------------------------------------------------------------ Postgres vero
def test_pg_coda_sito_e_verificata():
    import json
    import sys as _s
    _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pg_vero
    c = pg_vero.nuovo_db("controllo_coda_sito")
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
          truffa REAL, computed_at TIMESTAMPTZ);
        CREATE TABLE ai_usage (id BIGSERIAL, at TIMESTAMPTZ DEFAULT now(), task TEXT, model TEXT, input_tokens INT,
          output_tokens INT, web_searches INT, listing_id BIGINT);""")
    vista = {"type": "noul", "noul": .05}
    casi = {  # id: (decisione: (fonte, truffa, giorni fa) | None)
        1: None,                          # nessuna decisione: si fa
        2: ("jev", 0.4, 2),               # Jev con truffa da ai_pulita in su: si fa
        3: ("claude", 0.4, 2),            # Claude già deciso allo stesso prezzo: NON si richiede
        4: ("claude", 0.1, CA.valida_giorni() + 1),   # scaduta: si rinnova...
        5: ("claude", 0.1, CA.valida_giorni() + 1),   # ...ma non se già verificata con lo stesso contenuto
    }
    for i, dec in casi.items():
        pg_vero.psql(c.d, c.db, f"""
            INSERT INTO listings VALUES ({i}, 'attivo', 8000, true, 'privato', 'benzina', 'approfondito', 'opportunita',
                                         'MI', 2018, 80000, false);
            INSERT INTO listing_photos VALUES ({i}, 'https://x/{i}.jpg', 0);
            INSERT INTO valutazioni_policy (listing_id, categoria, margine, riassunto, dati, computed_at)
              VALUES ({i}, 'opportunita', 3500, '{{"M": 13000}}', '{{"soglia": 1500}}', now() - interval '2 days');""")
        if dec:
            risp = {"guasto": vista, "danni_foto": vista, "foto_internet": vista, "_foto": 1}
            pg_vero.psql(c.d, c.db, f"""INSERT INTO decisione_ai VALUES ({i}, 8000, '{json.dumps(risp)}', '{dec[0]}',
                'imp', {dec[1]}, now() - interval '{dec[2]} days');""")
    c.execute("UPDATE decisione_ai SET risposta = risposta || jsonb_build_object('_verificata_at', now()::text) "
              "WHERE listing_id=%s AND fonte='claude'", (5,))
    par = dict(CA.parametri_diesel(), quota=0.7, validi=CA.valida_giorni())
    assert c.execute(CA.CAND_SQL, par).fetchall() == []          # valutazioni vecchie: fuori dalle candidate di prima
    righe = {r["id"]: r for r in c.execute(CA.CAND_SQL_SITO, dict(par, sito_ids=[1, 2, 3, 4, 5])).fetchall()}
    assert set(righe) == {1, 2, 4}, sorted(righe)
    assert righe[1]["soglia"] == 1500 and CA.classe(righe[2]) == 0 and CA.classe(righe[4]) == 3
    # chiamate pagate senza una decisione salvata dopo (ultime 6 ore)
    pg_vero.psql(c.d, c.db, """
        INSERT INTO ai_usage (task, model, input_tokens, output_tokens, web_searches, listing_id)
          VALUES ('controllo_auto', 'h', 1, 1, 0, 1), ('controllo_auto', 'h', 1, 1, 0, 3);
        UPDATE decisione_ai SET computed_at = now() + interval '1 second' WHERE listing_id = 3;
        INSERT INTO ai_usage (task, model, input_tokens, output_tokens, web_searches, listing_id, at)
          VALUES ('controllo_auto_rinnovo', 'h', 1, 1, 0, 4, now() - interval '7 hours');""")
    assert CA.fallite_da_db(c) == {1}
