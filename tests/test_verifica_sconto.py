"""24/11 (cliente: OK a verificare in automatico le auto ferme per "sconto_alto_da_controllare"): seconda verifica.

- motivi_non_confermata è lo specchio esatto di sito_regole.sconto_confermato_ai (griglia di valori);
- idonea solo la zona grigia (truffa 0,30-0,50, auto_vera 0,50-0,70) con numero italiano: un segnale netto resta fuori;
- entra solo se TUTTO è pulito (soglie del sito + soglie strette), un dato che manca = fuori, senza chiamata;
- una sola verifica per decisione (segno _verifica), limiti per giro e al giorno, pausa su credito;
- ordine: privati prima, poi il margine più alto;
- riga VERIFICA_SCONTO_ALTO: solo conteggi, mai telefoni o email (nemmeno nel testo mandato all'AI);
- le regole del sito non cambiano: l'auto passata entra con sconto_confermato_ai di sempre e con l'avviso.
"""
from __future__ import annotations

import dataclasses
import itertools
import json
import logging
import time

from dealfinder import automatismi
from dealfinder.ai import controllo_auto as CA
from dealfinder.ai import verifica_sconto as VS
from dealfinder.web import sito_regole as SR
from tests.test_controllo_coda_sito import G, OK, auto, env

NO_ENV = dict(VERIFICA_SCONTO_ALTO=None, AUTOMATISMO_VERIFICA_SCONTO_ALTO=None, VERIFICA_PER_GIRO=None,
              VERIFICA_MAX_GIORNO=None, VERIFICA_FOTO=None, VERIFICA_FOTO_MIN=None, VERIFICA_ORE_MIN=None,
              VERIFICA_CALO_MAX=None, VERIFICA_MODELLO=None, CONTROLLO_TUTTE=None)
GRIGIA = {"fonte": "claude", "danni": .05, "foto_internet": .05, "truffa": .35, "auto_vera": .6, "dati_coerenti": 1,
          "decisione": "tratta", "stesso_prezzo": True, "km_ok": 1, "guasto": 0}
PULITA = dict(OK, truffa=.05, auto_vera=.95, foto_internet=.05, danni_foto=.05, dati_coerenti=.95, targa_visibile=.8)


def _pulisci():
    CA._PAUSA["fino"] = 0.0
    CA._FALLITE.clear()
    CA._SALTATE.clear()
    CA.CODA_SITO.clear()
    CA.CODA_SITO.update(t=0.0, entra=[], fuori=set(), sul_sito=set(), motivi={}, inutili=set(), gia_controllate=set())
    CA.MISURA.update(giorno=None)
    VS._ATTESA.clear()
    VS.STATO.update(t=0.0, ultimo={})


class _Ris:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Conn:
    def __init__(self, righe, fatte=0):
        self.righe, self.fatte, self.scritte, self.commit_n = list(righe), fatte, [], 0

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith(("INSERT", "UPDATE")):
            self.scritte.append((s, params))
            return _Ris([])
        if "count(*) AS n FROM ai_usage" in s:
            return _Ris([{"n": self.fatte}])
        if "to_regclass" in s:
            return _Ris([{"t": None}])
        if "JOIN decisione_ai d" in s:
            ids = set(params["ids"])
            return _Ris([dict(r) for r in self.righe if r["id"] in ids])
        return _Ris([])

    def commit(self):
        self.commit_n += 1

    def rollback(self):
        pass

    def usage(self):
        return [p for s, p in self.scritte if "INTO ai_usage" in s]

    def decisioni(self):
        return [p for s, p in self.scritte if "INTO decisione_ai" in s]

    def segni(self):
        return {p[1]: json.loads(p[0]) for s, p in self.scritte if s.startswith("UPDATE decisione_ai") and "_verifica" in s}


def riga(i, **kw):
    r = {"id": i, "source": "subito", "title": "Fiat Panda 1.2", "description": "unico proprietario, tagliandata",
         "make": "Fiat", "model": "Panda", "version_raw": "1.2 Lounge", "year": 2015, "mileage_km": 90000,
         "fuel": "Benzina", "gearbox": "manuale", "power_kw": 51, "price_eur": 8000, "province": "MI",
         "seller_type": "privato", "n_foto": 6, "foto": [f"https://x/{i}-{k}.jpg" for k in range(6)], "confronti": [],
         "cat": "opportunita", "margine": 3500, "ore_online": 100.0, "prezzo_max": 8000, "d_prezzo": 8000,
         "d_verifica": None}
    r.update(kw)
    return r


def _foto(fonti):
    return [{"type": "image", "source": {"type": "url", "url": f}} for f in fonti]


def _coda(*ids):
    CA.CODA_SITO.update(t=time.time(), verifica=list(ids), verifica_motivi={"idonee_alla_verifica": len(ids)})


class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.righe = []

    def emit(self, record):
        self.righe.append(record.getMessage())

    def __enter__(self):
        logging.getLogger("verifica_sconto").addHandler(self)
        return self

    def __exit__(self, *a):
        logging.getLogger("verifica_sconto").removeHandler(self)


# ------------------------------------------------------------------ specchio della regola del sito
def test_motivi_specchio_esatto_della_conferma_del_sito():
    valori = {"fonte": ["claude", "jev", None], "danni": [None, 0, .69, .7], "stesso_prezzo": [True, False, None],
              "truffa": [None, 0, .29, .3, .49, .5, .9], "auto_vera": [None, 0, .49, .5, .69, .7, 1],
              "dati_coerenti": [None, .49, .5], "foto_internet": [None, .49, .5], "decisione": ["tratta", "scarta"]}
    chiavi = list(valori)
    n = 0
    for combo in itertools.product(*valori.values()):
        j = dict(zip(chiavi, combo))
        for per in (None, {"giudizio": "caro"}, {"giudizio": "ok"}):
            it = {"_r": {"jev": j, "perizia": per}}
            assert (VS.motivi_non_confermata(it, G) == []) == SR.sconto_confermato_ai(it, G), (j, per)
            n += 1
    assert n > 30000
    spento = dataclasses.replace(G, sconto_ai=False)
    assert VS.motivi_non_confermata({"_r": {"jev": dict(GRIGIA, truffa=0, auto_vera=1)}}, spento) == ["sconto_ai_spento"]
    assert VS.motivi_non_confermata({"_r": {}}, G) == ["senza_controllo_claude"]


def test_idonea_solo_la_zona_grigia_con_numero_italiano():
    assert VS.non_idonea(auto(1, jev=dict(GRIGIA)), G) is None
    assert VS.non_idonea(auto(1, jev=dict(GRIGIA, auto_vera=.9)), G) is None           # solo truffa in zona grigia
    assert VS.non_idonea(auto(1, jev=dict(GRIGIA, truffa=.1)), G) is None              # solo auto_vera
    for cambio, motivo in ((dict(truffa=.5), "segnale_netto:truffa_alta"),
                           (dict(truffa=None), "segnale_netto:truffa_mancante"),
                           (dict(auto_vera=.49), "segnale_netto:auto_vera_bassa"),
                           (dict(auto_vera=None), "segnale_netto:auto_vera_mancante"),
                           (dict(foto_internet=.5), "segnale_netto:foto_internet"),
                           (dict(foto_internet=None), "segnale_netto:foto_internet_mancante"),
                           (dict(dati_coerenti=.4), "segnale_netto:dati_non_coerenti"),
                           (dict(danni=.7), "segnale_netto:danni_nelle_foto"),
                           (dict(danni=None), "segnale_netto:foto_non_viste"),
                           (dict(stesso_prezzo=False), "segnale_netto:prezzo_cambiato"),
                           (dict(decisione="scarta", confidenza=.1), "segnale_netto:decisione_scarta"),
                           (dict(fonte="jev"), "segnale_netto:senza_controllo_claude")):
        assert VS.non_idonea(auto(1, jev=dict(GRIGIA, **cambio)), G) == motivo, cambio
    assert VS.non_idonea(auto(1, jev=dict(GRIGIA), _r={"perizia": {"giudizio": "da verificare"}}), G) == "segnale_netto:perizia"
    assert VS.non_idonea(auto(1, jev=dict(GRIGIA), tel=False), G) == "senza_numero"
    assert VS.non_idonea(auto(1, jev=dict(GRIGIA), _r={"tel_estero": True}), G) == "numero_estero"
    assert VS.non_idonea(auto(1, jev=dict(GRIGIA), _r={"testo_truffa": True}), G) == "testo_pagamento_estero"
    assert VS.non_idonea(auto(1, jev=dict(GRIGIA, guasto=.8)), G) == "altro_passo_ai"
    assert VS.non_idonea(auto(1, jev=dict(GRIGIA, rata=.8)), G) == "altro_passo_ai"
    assert VS.non_idonea(auto(1, jev=dict(GRIGIA, truffa=.1, auto_vera=.9)), G) == "gia_confermata"


def test_pulita_soglie_del_sito_e_soglie_strette():
    x = dict(PULITA, _foto=6)
    assert VS.pulita(x, G) == []
    limite = dict(OK, truffa=.25, auto_vera=.75, _foto=6)                          # passa il sito, non la verifica
    assert CA.confermabile(limite, G) and VS.pulita(limite, G) == ["truffa", "auto_vera"]
    assert VS.pulita(dict(x, truffa=.2), G) == ["truffa"]                          # passerebbe il sito (0,30): qui no
    assert VS.pulita(dict(x, auto_vera=.8), G) == ["auto_vera"]
    assert VS.pulita(dict(x, foto_internet=.3), G) == ["foto_internet"]
    assert VS.pulita(dict(x, dati_coerenti=.7), G) == ["dati_coerenti"]
    assert VS.pulita(dict(x, danni_foto=.4), G) == ["danni_foto"]
    assert VS.pulita(dict(x, guasto=.4), G) == ["guasto"]
    assert VS.pulita(dict(x, prezzo_rata=.4), G) == ["prezzo_rata"]
    assert VS.pulita(dict(x, km_credibili=.6), G) == ["km_credibili"]
    assert VS.pulita(dict(x, _foto=2), G) == ["foto_viste_poche"]
    assert "soglie_del_sito" in VS.pulita(dict(x, danni_foto=None), G)             # dato mancante: fuori
    assert "soglie_del_sito" in VS.pulita(dict(x, decisione="scarta"), G)
    for k, lim in (("truffa_max", G.ai_pulita), ("danni_max", G.jev_danni), ("guasto_max", G.jev_guasto),
                   ("rata_max", G.jev_rata), ("foto_internet_max", .5)):
        assert VS.PULITA[k] < lim                                                  # mai più larghe del sito
    assert VS.PULITA["auto_vera_min"] > G.sconto_ai_vera and VS.PULITA["km_credibili_min"] > G.jev_km_ok


def test_senza_chiamata_se_un_dato_manca():
    with env(**NO_ENV):
        assert VS.senza_chiamata(riga(1)) is None
        assert VS.senza_chiamata(riga(1, d_verifica={"esito": "non_pulita"})) == "gia_verificata"
        assert VS.senza_chiamata(riga(1, d_prezzo=8500)) == "prezzo_cambiato"
        assert VS.senza_chiamata(riga(1, n_foto=2)) == "poche_foto"
        assert VS.senza_chiamata(riga(1, ore_online=None)) == "storico_mancante"
        assert VS.senza_chiamata(riga(1, ore_online=47)) == "annuncio_troppo_recente"
        assert VS.senza_chiamata(riga(1, prezzo_max=12000)) == "calo_prezzo_anomalo"      # -33% sul massimo
        assert VS.senza_chiamata(riga(1, prezzo_max=11000)) is None                        # -27%
        assert VS.senza_chiamata(riga(1, prezzo_max=None)) is None                         # nessun cambio di prezzo
        assert VS.senza_chiamata(riga(1, description="pagamento con western union")) == "testo:testo_pagamento_estero"
        assert VS.senza_chiamata(riga(1, mileage_km=G.km_max + 1)) == "testo:km"
        assert VS.senza_chiamata(riga(1, year=G.anno_min - 1)) == "testo:anno"


# ------------------------------------------------------------------ coda dall'elenco
def test_coda_dal_sito_prepara_le_idonee_privati_e_margine_prima():
    _pulisci()
    with env(**NO_ENV):
        items = [auto(1),                                                   # mai controllata
                 auto(2, jev=dict(GRIGIA)),                                 # idonea, margine 3.500
                 auto(3, jev=dict(GRIGIA), prezzo=12000, margine=5000, M=17500),   # idonea, margine più alto
                 auto(4, jev=dict(GRIGIA, truffa=.6)),                      # truffa netta: fuori senza chiamata
                 auto(5, jev=dict(GRIGIA), margine=200),                    # sotto il margine minimo: inutile
                 auto(6, jev=dict(GRIGIA), margine=9000),                   # idonea, il margine più alto di tutte
                 auto(7, jev=dict(GRIGIA, foto_internet=.8))]               # foto da internet: netto
        copia = [dict(it, _r=dict(it["_r"]), pv=dict(it["pv"])) for it in items]
        out, fun = SR.elenco_sito(copia, G, scrivi_log=False)
        assert out == [] and set(fun.tolti["truffa"]) == {1, 2, 3, 4, 5, 6, 7}
        CA.coda_dal_sito(copia, out, fun, G)
        v = CA.CODA_SITO["verifica"]
        assert 4 not in v and 5 not in v and 1 not in v and 7 not in v
        assert v == [6, 3, 2]                                               # il margine più alto prima
        # i privati prima dei commercianti, anche con un margine più basso (il tipo lo mette il passo venditore)
        comm = auto(8, margine=9000, _tipo="comm_fascia")
        assert sorted([comm, auto(9, margine=2000, _tipo="privato")], key=VS.priorita)[0]["id"] == 9
        m = CA.CODA_SITO["verifica_motivi"]
        assert m["attesa_primo_controllo"] == 1 and m["segnale_netto:truffa_alta"] == 1
        assert m["non_entrerebbe:margine_minimo"] == 1 and m["idonee_alla_verifica"] == 3
        assert m["segnale_netto:foto_internet"] == 1 and len(CA.CODA_SITO["sconto_alto"]) == 7
        assert sum(k for n, k in m.items() if n != "idonee_alla_verifica") + len(v) == len(CA.CODA_SITO["sconto_alto"])
    _pulisci()


# ------------------------------------------------------------------ il giro
def test_giro_passa_solo_la_pulita_e_segna_tutte():
    _pulisci()
    risposte = {1: dict(PULITA), 2: dict(OK, truffa=.25, auto_vera=.75), 3: dict(PULITA, truffa=.6), 4: {"non": "valida"}}
    viste = []

    def chiama(testo, foto):
        lid = next(i for i in risposte if f"https://x/{i}-0.jpg" in foto[0]["source"]["url"])
        viste.append((lid, len(foto), testo))
        return dict(risposte[lid]), 5000, 200, "claude-sonnet-5-5"
    _coda(1, 2, 3, 4, 5, 6)
    conn = Conn([riga(1), riga(2), riga(3), riga(4), riga(5, ore_online=3), riga(6, d_verifica={"esito": "ok"})])
    with env(**NO_ENV), _Log() as lg:
        res = VS.giro(conn, chiama=chiama, foto=_foto, g=G)
    assert [v[0] for v in viste] == [1, 2, 3, 4] and all(v[1] == 6 for v in viste)
    assert res["verifica_fatte"] == 4 and res["verifica_ok"] == 1 and res["verifica_usd"] > 0
    assert len(conn.usage()) == 4 and all(u[0] == VS.TASK for u in conn.usage())
    # solo la pulita diventa la decisione dell'auto; le altre tengono la decisione di prima con il segno
    dec = conn.decisioni()
    assert len(dec) == 1 and dec[0][0] == 1
    assert CA.confermabile(dict(PULITA, _foto=6), G)
    s = conn.segni()
    assert s[1]["esito"] == "ok" and s[2]["esito"] == "non_pulita" and s[3]["esito"] == "non_pulita"
    assert s[4]["esito"] == "non_valida" and 5 not in s and 6 not in s
    assert "truffa" in s[2]["motivi"] or "auto_vera" in s[2]["motivi"]
    riga_log = next(r for r in lg.righe if r.startswith("VERIFICA_SCONTO_ALTO {"))
    d = json.loads(riga_log.split(" ", 1)[1])
    assert d["controllate"] == 4 and d["passate"] == 1 and d["fuori"] == 3
    assert sum(d["fuori_per_motivo"].values()) == 3 and d["fuori_per_motivo"]["risposta_non_valida"] == 1
    assert d["senza_chiamata"] == {"annuncio_troppo_recente": 1, "gia_verificata": 1}
    assert d["costo_usd"] == res["verifica_usd"] and d["elenco_sconto_alto"] == {"idonee_alla_verifica": 6}
    _pulisci()


def test_la_passata_entra_sul_sito_con_le_regole_di_sempre_e_l_avviso():
    # la decisione salvata dalla verifica, riletta dal sito come ogni decisione Claude, conferma lo sconto alto
    j = {"fonte": "claude", "danni": PULITA["danni_foto"], "foto_internet": PULITA["foto_internet"],
         "truffa": PULITA["truffa"], "auto_vera": PULITA["auto_vera"], "dati_coerenti": PULITA["dati_coerenti"],
         "decisione": PULITA["decisione"], "stesso_prezzo": True, "km_ok": PULITA["km_credibili"], "guasto": 0}
    out, _f = SR.elenco_sito([auto(1, jev=j)], G, scrivi_log=False)
    assert [it["id"] for it in out] == [1] and out[0]["avviso_verifica"] and out[0]["sconto_confermato_ai"]
    prima, _f = SR.elenco_sito([auto(1, jev=dict(GRIGIA))], G, scrivi_log=False)
    assert prima == []                                                  # in zona grigia resta fuori, come prima
    assert out[0]["pv"]["M"] == 13000 and out[0]["pv"]["margine"] == 3500   # mercato e margine non toccati


def test_limiti_per_giro_al_giorno_e_una_sola_volta():
    _pulisci()
    n = []

    def chiama(testo, foto):
        n.append(1)
        return dict(PULITA), 5000, 200, "claude-sonnet-5-5"
    righe = [riga(i) for i in range(1, 11)]
    _coda(*range(1, 11))
    with env(**dict(NO_ENV, VERIFICA_PER_GIRO="3")):
        res = VS.giro(Conn(righe), chiama=chiama, foto=_foto, g=G)
        assert len(n) == 3 and res["verifica_restanti"] == 7
        assert CA.attesa_prossimo_giro(dict(res, candidate=0), 3600.0) == 120.0     # coda non finita: giro dopo presto
        res = VS.giro(Conn(righe, fatte=299), chiama=chiama, foto=_foto, g=G)       # tetto al giorno: 300
        assert len(n) == 4 and res["verifica_fatte"] == 1
        res = VS.giro(Conn(righe, fatte=300), chiama=chiama, foto=_foto, g=G)
        assert len(n) == 4 and res["verifica_fatte"] == 0 and "verifica_restanti" not in res
        assert CA.attesa_prossimo_giro(dict(res, candidate=0), 3600.0) == 3600.0     # mai giri a vuoto
        # già verificate (segno _verifica sulla decisione): nessuna chiamata, per sempre
        res = VS.giro(Conn([riga(i, d_verifica={"esito": "non_pulita"}) for i in range(1, 11)]), chiama=chiama,
                      foto=_foto, g=G)
        assert len(n) == 4 and res["verifica_fatte"] == 0
    _pulisci()


def test_spenta_pausa_foto_non_scaricabili_ed_errori_senza_spesa():
    _pulisci()

    def mai(testo, foto):
        raise AssertionError("nessuna chiamata")
    _coda(1)
    with env(**dict(NO_ENV, VERIFICA_SCONTO_ALTO="0")):
        assert VS.giro(Conn([riga(1)]), chiama=mai, foto=_foto, g=G) == {}
    with env(**dict(NO_ENV, AUTOMATISMO_VERIFICA_SCONTO_ALTO="0")):
        assert VS.giro(Conn([riga(1)]), chiama=mai, foto=_foto, g=G) == {}
    assert "verifica_sconto_alto" in automatismi.NOMI
    with env(**NO_ENV):
        assert VS.giro(Conn([riga(1)]), chiama=None, foto=_foto, g=G) == {} or CA.attiva()   # senza chiave: spenta
        CA._PAUSA["fino"] = time.time() + 600
        assert VS.giro(Conn([riga(1)]), chiama=mai, foto=_foto, g=G) == {"verifica_saltata": "pausa per credito/limite AI"}
        CA._PAUSA["fino"] = 0.0
        # nessuna idonea: nessuna chiamata, ma la riga dice perché le auto con sconto alto sono fuori
        CA.CODA_SITO.update(t=time.time(), verifica=[], verifica_motivi={"segnale_netto:truffa_alta": 7})
        with _Log() as lg:
            assert VS.giro(Conn([]), chiama=mai, foto=_foto, g=G) == {}
            assert VS.giro(Conn([]), chiama=mai, foto=_foto, g=G) == {}
        assert len(lg.righe) == 1 and '"segnale_netto:truffa_alta": 7' in lg.righe[0] and '"controllate": 0' in lg.righe[0]
        _coda(1)
        CA.CODA_SITO["t"] = time.time() - 3600 * 2                      # elenco vecchio: niente
        assert VS.giro(Conn([riga(1)]), chiama=mai, foto=_foto, g=G) == {}
        _coda(1, 2)
        conn = Conn([riga(1), riga(2)])
        res = VS.giro(conn, chiama=mai, foto=lambda fonti: _foto(fonti)[:2], g=G)   # solo 2 foto scaricate su 3
        assert res["verifica_fatte"] == 0 and conn.usage() == [] and set(VS._ATTESA) == {1, 2}
        assert VS.giro(conn, chiama=mai, foto=_foto, g=G)["verifica_fatte"] == 0     # si riprova tra 6 ore
        VS._ATTESA.clear()

        class Credito(Exception):
            status_code = 402

        def finito(testo, foto):
            raise Credito("credit balance is too low")
        res = VS.giro(conn, chiama=finito, foto=_foto, g=G)
        assert res["verifica_fatte"] == 0 and res.get("verifica_pausa_credito") == 1 and CA._PAUSA["fino"] > time.time()
        assert conn.usage() == [] and "verifica_restanti" not in res
        assert CA.attesa_prossimo_giro(dict(res, candidate=0, fonte="claude"), 3600.0) == 3600.0
    _pulisci()


def test_nessun_telefono_o_email_all_ai_ne_nei_log():
    _pulisci()
    testi = []

    def chiama(testo, foto):
        testi.append(testo)
        return dict(PULITA, note="chiamare 3331234567 o mario@example.com"), 5000, 200, "claude-sonnet-5-5"
    _coda(1)
    conn = Conn([riga(1, description="Bella auto, chiamare 333 1234567 oppure +39 02 1234567, mario@example.com")])
    with env(**NO_ENV), _Log() as lg:
        res = VS.giro(conn, chiama=chiama, foto=_foto, g=G)
    assert res["verifica_ok"] == 1
    tutto = " ".join(testi) + " ".join(lg.righe) + json.dumps([str(p) for _s, p in conn.scritte])
    for vietato in ("3331234567", "333 1234567", "1234567", "mario@example.com"):
        assert vietato not in tutto, vietato
    _pulisci()


def test_il_giro_del_controllo_porta_la_verifica_e_non_cade_se_lei_cade():
    _pulisci()
    from tests.test_controllo_coda_sito import Conn as ConnCA
    with env(**NO_ENV):
        res = CA.giro(ConnCA([], []), chiama=lambda t, f: (dict(OK), 1, 1, "claude-haiku-5-5"))
        assert not any(k.startswith("verifica_") for k in res)          # senza auto idonee: come prima
        prima = VS.giro
        VS.giro = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rotta"))
        try:
            res = CA.giro(ConnCA([], []), chiama=lambda t, f: (dict(OK), 1, 1, "claude-haiku-5-5"))
            assert "candidate" in res
            VS.giro = lambda *a, **k: {"verifica_fatte": 2, "verifica_ok": 1}
            res = CA.giro(ConnCA([], []), chiama=lambda t, f: (dict(OK), 1, 1, "claude-haiku-5-5"))
            assert res["verifica_ok"] == 1 and "candidate" in res
        finally:
            VS.giro = prima
    _pulisci()


def test_le_regole_del_sito_non_cambiano():
    # nessun campo nuovo nelle Regole (l'impronta delle regole resta uguale): i parametri della verifica stanno qui
    assert not any("verifica" in f.name for f in dataclasses.fields(SR.Regole))
    assert G.ai_pulita == 0.30 and G.sconto_ai_vera == 0.7 and G.truffa_avviso == 0.5
