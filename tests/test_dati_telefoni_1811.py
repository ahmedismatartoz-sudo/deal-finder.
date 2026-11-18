"""18/11 sera (cliente OK 21:01, "più auto senza toccare le regole"): dati_insufficienti e auto senza numero.

A. valore_base: motivo di ogni dati_insufficienti (log DATI_INSUFF_MOTIVI), commercianti vecchi MAI nella stima ma
   segnati da riaprire (confronti_mirati), misura di precisione col minimo del sito in base_vendute.
B. telefono_nascosto: prioritarie dalla migliore (margine/soglia), inutili (fuori anche col numero) mai aperte.
Niente rete né database (connessioni finte); nei log solo conteggi."""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from decimal import Decimal

from dealfinder.pricing import base_vendute as BV
from dealfinder.pricing import confronti_mirati as CM
from dealfinder.pricing import valore_base as VB
from dealfinder.pipeline import telefono_nascosto as TN
from dealfinder.pipeline import tempo_reale as tr
from dealfinder.web import scovo
from dealfinder.web import sito_regole as SR

from tests.test_notte01_telefono import ConnTel
from tests.test_piu_auto import _Ris, _azzera_tel, auto, env
from tests.test_valore_base import COMM, VEND, _c, _t

G = SR.Regole()


def _vecchio(i, prezzo, gv=20, **kw):
    return _c(i, prezzo, giorni_visto=Decimal(str(gv)), **kw)


def _base(righe):
    b = VB.dividi(righe)
    b["Dv"] = VB.vecchi_commercianti(righe)
    return b


# ------------------------------------------------------------------ A. motivi e commercianti vecchi
def test_commercianti_vecchi_mai_nella_stima():
    vecchi = [_vecchio(200 + i, 30000) for i in range(6)]       # prezzi lontanissimi: se contassero, R cambierebbe
    v0 = VB.valuta(_t(6000), _base(COMM), scovo.soglia(6000))
    v1 = VB.valuta(_t(6000), _base(COMM + vecchi), scovo.soglia(6000))
    assert v0["R"] == v1["R"] == 9250 and v0["n"] == v1["n"] == 6 and v1["categoria"] == v0["categoria"]
    assert "rinfresco" not in v1 and "motivo_dati" not in v1
    # oltre VECCHI_MAX_GIORNI non sono nemmeno da riaprire; quelli freschi sono già nella base D
    assert VB.vecchi_commercianti([_vecchio(1, 9000, gv=VB.VECCHI_MAX_GIORNI + 1), COMM[0]]) == []


def test_motivo_rinfrescabile_con_id_da_riaprire():
    vecchi = [_vecchio(200 + i, 9000 + 100 * i, gv=11 + i) for i in range(3)]
    v = VB.valuta(_t(6000), _base(COMM[:3] + vecchi), scovo.soglia(6000))
    assert v["categoria"] == "dati_insufficienti" and v.get("M") is None and v.get("margine") is None
    assert v["motivo_dati"] == "pochi_confronti:rinfrescabile"
    assert v["simili_D"] == 3 and v["simili_vecchi"] == 3 and v["simili_V"] == 0
    assert v["rinfresco"]["ids"] == [200, 201, 202]              # i più recenti prima
    assert 9000 <= v["rinfresco"]["stima_priorita"] <= 9300
    # nel riassunto del sito va solo il motivo (non gli id)
    from dealfinder.pricing import policy_v2 as P
    r = P.riassunto(v)
    assert r["motivo_dati"] == "pochi_confronti:rinfrescabile" and "rinfresco" not in r


def test_altri_motivi_pochi_confronti():
    s = scovo.soglia(6000)
    assert VB.valuta(_t(6000), _base([]), s)["motivo_dati"] == "pochi_confronti:modello_assente"
    lontani = [_c(i, 9000, year=2009) for i in range(1, 7)]       # stesso gruppo, anno troppo diverso
    assert VB.valuta(_t(6000), _base(lontani), s)["motivo_dati"] == "pochi_confronti:nessuno_simile"
    v = VB.valuta(_t(6000), _base(COMM[:3] + VEND[:3]), s)        # 3 + 3: solo unendo le due basi (NON usato)
    assert v["motivo_dati"] == "pochi_confronti:solo_unendo" and v["categoria"] == "dati_insufficienti"
    assert VB.valuta(_t(6000), _base(COMM[:2] + VEND[:2]), s)["motivo_dati"] == "pochi_confronti:pochi_simili"


def test_motivi_delle_protezioni_restano_protezioni():
    b = _base(COMM)
    assert VB.valuta(_t(6000, mileage_km=None), b, 1500)["motivo_dati"] == "dati_auto_incompleti"
    assert VB.valuta(_t(6000, damage_class="grave"), b, 1500)["motivo_dati"] == "auto_non_pulita"
    v = VB.valuta(_t(4000), b, scovo.soglia(4000))                # margine oltre il 55%: anti-truffa invariato
    assert v["motivo_dati"] == "antitruffa:margine_oltre_55" and v["sospetto_truffa"] is True
    assert VB.valuta(_t(6000), b, scovo.soglia(6000))["categoria"] == "opportunita"


def test_motivo_dati_anche_sulle_valutazioni_vecchie_e_conta():
    assert VB.motivo_dati({"categoria": "opportunita"}) is None
    assert VB.motivo_dati({"categoria": "dati_insufficienti", "metodo": "valore_reale"}) == "calcolo_vecchio"
    assert VB.motivo_dati({"categoria": "dati_insufficienti", "metodo": "base_vendute"}) == "pochi_confronti"
    assert VB.motivo_dati({"categoria": "dati_insufficienti", "metodo": "base_vendute", "sospetto_truffa": True}) \
        == "antitruffa:margine_oltre_55"
    assert VB.motivo_dati({"categoria": "dati_insufficienti", "metodo": "base_vendute", "non_pulita": "x"}) \
        == "auto_non_pulita"
    assert VB.motivo_dati({"categoria": "dati_insufficienti", "metodo": "base_vendute", "M": 9000}) \
        == "antitruffa_o_costi"
    items = [{"id": 1, "pv": {"categoria": "dati_insufficienti", "motivo_dati": "pochi_confronti:rinfrescabile"}},
             {"id": 2, "pv": {"categoria": "dati_insufficienti", "metodo": "base_vendute"}},
             {"id": 3, "pv": {"categoria": "dati_insufficienti", "motivo_dati": "pochi_confronti:rinfrescabile"}},
             {"id": 4, "pv": None}]
    assert VB.conta_motivi(items, [1, 2, 4]) == Counter({"pochi_confronti:rinfrescabile": 1, "pochi_confronti": 1})


def test_carica_aggiunge_i_vecchi():
    class C:
        def execute(self, sql, p):
            righe = [dict(r, giorni_visto=r["giorni_visto"]) for r in COMM[:2]]
            righe += [dict(_vecchio(300, 9000))]
            return _Ris(righe)
    b = VB.carica(C(), "Fiat", "Panda", "benzina")
    assert len(b["D"]) == 2 and [r["id"] for r in b["Dv"]] == [300]


def test_precisione_col_minimo_del_sito():
    st, prec = Counter(), defaultdict(list)
    righe = [dict(r, telefono_ok=True) for r in COMM[:3] + VEND]
    BV._gruppo(righe, st, prec, [])
    # 3 commercianti + 4 altre vendute (ognuna esclusa da sé): solo unendo si arriva a 5
    assert len(prec["sito_n5_solo_unendo"]) == 5 and not prec["sito_n5"]
    assert BV._err(prec["sito_n5_solo_unendo"])["n"] == 5


# ------------------------------------------------------------------ A. confronti mirati
def _cand(i, prezzo, stima, ids, tel=None):
    return {"id": i, "price_eur": prezzo, "telefono_ok": tel, "rinfresco": {"ids": ids, "stima_priorita": stima}}


def test_scegli_ordine_e_risparmio():
    sg = scovo.soglia(6000)
    righe = [_cand(1, 6000, 6000 + sg, [11]),                    # margine provvisorio = soglia
             _cand(2, 6000, 6000 + 2 * sg, [12]),                # migliore
             _cand(3, 6000, 6000 + sg // 4, [13]),               # sotto metà soglia: niente pagine
             _cand(4, 6000, 6000 + sg, [14], tel=True),          # numero già letto: prima di tutte
             _cand(5, 6000, 6000 + sg, []),                      # niente da riaprire
             _cand(6, 6000, 6000 + 3 * sg, [16])]                # provata da poco
    out = CM.scegli(righe, scovo.soglia, adesso=1e6, provate={6: 1e6 - 3600})
    assert [r["id"] for r in out] == [4, 2, 1]
    assert out[0]["_ids"] == [14]


class ConnCM:
    def __init__(self, cand, conf):
        self.cand, self.conf, self.sql = cand, conf, []

    def execute(self, sql, p=None):
        s = " ".join(sql.split())
        self.sql.append((s[:40], p))
        if "FROM valutazioni_policy vp" in s:
            return _Ris(self.cand)
        if s.startswith("SELECT id, source, url, price_eur FROM listings"):
            return _Ris([c for c in self.conf if c["id"] in p[0]])
        return _Ris([])

    def commit(self):
        pass

    def rollback(self):
        pass


def test_giro_riapre_e_rivaluta(caplog=None):
    sg = scovo.soglia(6000)
    cand = [_cand(1, 6000, 6000 + 2 * sg, [11, 12]), _cand(2, 6000, 6000 + sg, [12, 13]),
            _cand(3, 6000, 6000 + sg, [14])]
    conf = [{"id": i, "source": "autoscout24", "url": f"https://x/{i}", "price_eur": 9000} for i in (11, 12, 13)]
    aperti, rivalutati = [], []

    def controlla(conn, c, st):
        aperti.extend(x["id"] for x in c)
        return {"aperte": len(c), "cambiati": [11, 13]}

    class Es:
        stats = Counter({"categoria:opportunita": 1, "pubblicate": 1})

    def rivaluta(conn, ids, fonte, pubblica=None, **k):
        rivalutati.append((list(ids), fonte, pubblica))
        return Es()

    CM._PROVATE.clear()
    CM._GIORNO.update(g=None, pagine=0)
    st = CM.giro(ConnCM(cand, conf), controlla=controlla, rivaluta=rivaluta)
    assert aperti == [11, 12, 13]                                # 14 non è più vecchio: già rinfrescato
    ids, fonte, pubblica = rivalutati[0]
    assert sorted(ids) == [1, 2, 3] and fonte == "confronti-mirati" and pubblica is True
    assert st["rivalutate_pubblicate"] == 1 and st["confronti_da_aprire"] == 3 and st["auto_da_rivalutare"] == 3
    # stessa auto non si riprende prima di RIPROVA_ORE
    st2 = CM.giro(ConnCM(cand, conf), controlla=controlla, rivaluta=rivaluta)
    assert st2["scelte"] == 0 and len(rivalutati) == 1
    CM._PROVATE.clear()


def test_giro_non_spezza_un_auto_e_scrittura_sicura():
    """(giudice 18/11) un'auto i cui confronti non stanno nel resto delle pagine aspetta il giro dopo (non segnata
    provata); un errore del database su una riga non ferma il giro e le pagine aperte si contano."""
    sg = scovo.soglia(6000)
    cand = [_cand(1, 6000, 6000 + 2 * sg, list(range(100, 100 + CM.PAGINE_GIRO - 2))),
            _cand(2, 6000, 6000 + sg, [500, 501, 502])]
    conf = [{"id": i, "source": "subito", "url": "u", "price_eur": 1} for i in list(range(100, 160)) + [500, 501, 502]]
    visti = []
    CM._PROVATE.clear()
    CM._GIORNO.update(g=None, pagine=0)
    CM.giro(ConnCM(cand, conf), controlla=lambda c, x, st: visti.append([r["id"] for r in x]) or {"aperte": len(x)},
            rivaluta=lambda *a, **k: None)
    assert 500 not in visti[0] and 1 in CM._PROVATE and 2 not in CM._PROVATE
    CM._PROVATE.clear()
    from dealfinder.pipeline import tempo_reale as tr
    from dealfinder.pipeline import verify as vf
    vecchi = (tr._controlla_annuncio, tr._client_subito, vf._mark)

    def rotto(*a, **k):
        raise RuntimeError("db giu")
    try:
        tr._controlla_annuncio = lambda r, *a, **k: (r, "attivo", None)
        tr._client_subito = lambda: None
        vf._mark = rotto
        st = Counter()
        es = CM._controlla(ConnCM([], []), [{"id": 7, "source": "subito", "url": "u", "price_eur": 1}] * 3, st)
        assert es["aperte"] == 3 and st["errore_scrittura"] == 3 and es["cambiati"] == []
    finally:
        tr._controlla_annuncio, tr._client_subito, vf._mark = vecchi


def test_giro_tetto_pagine_e_spento():
    sg = scovo.soglia(6000)
    cand = [_cand(i, 6000, 6000 + sg, [1000 + i]) for i in range(1, 200)]
    conf = [{"id": 1000 + i, "source": "subito", "url": "u", "price_eur": 1} for i in range(1, 200)]
    visti = []
    CM._PROVATE.clear()
    CM._GIORNO.update(g=None, pagine=0)
    CM.giro(ConnCM(cand, conf), controlla=lambda c, x, st: visti.append(len(x)) or {"aperte": len(x)},
            rivaluta=lambda *a, **k: None)
    assert visti == [min(CM.PAGINE_GIRO, CM.AUTO_GIRO)]
    CM._GIORNO.update(pagine=CM.PAGINE_GIORNO)
    assert CM.giro(ConnCM(cand, conf))["tetto_giorno"] == 1
    with env(CONFRONTI_MIRATI="0"):
        assert CM.giro(ConnCM(cand, conf))["spento"] == 1
    CM._PROVATE.clear()
    CM._GIORNO.update(g=None, pagine=0)


def test_controlla_segna_come_il_rinfresco():
    import dealfinder.pipeline.verify as VF
    segnati, prezzi = [], []
    esiti = {1: ("attivo", None), 2: ("scomparso", "404"), 3: ("attivo", "prezzo_cambiato:8500"),
             4: ("attivo", "prezzo_illeggibile"), 5: ("errore", None)}
    vecchi = (tr._controlla_annuncio, tr._client_as, tr._client_subito, tr.segna_prezzo_cambiato, VF._mark)
    tr._controlla_annuncio = lambda r, *a, **k: (r, *esiti[r["id"]])
    tr._client_as = tr._client_subito = lambda: None
    tr.segna_prezzo_cambiato = lambda conn, lid, p: prezzi.append((lid, p))
    VF._mark = lambda conn, lid, st, price=None, motivo=None, sito=False: segnati.append((lid, st))
    try:
        st = Counter()
        out = CM._controlla(None, [{"id": i, "source": "autoscout24", "url": "u", "price_eur": 9000} for i in esiti], st)
    finally:
        tr._controlla_annuncio, tr._client_as, tr._client_subito, tr.segna_prezzo_cambiato, VF._mark = vecchi
    assert segnati == [(1, "attivo"), (2, "scomparso"), (3, "attivo")] and prezzi == [(3, 8500)]
    assert out == {"aperte": 5, "cambiati": [1, 2, 3]} and st["autoscout24:errore"] == 2


def test_controlla_si_ferma_sui_blocchi():
    vecchi = (tr._controlla_annuncio, tr._client_as, tr._client_subito)
    tr._controlla_annuncio = lambda r, *a, **k: (r, "errore", "blocco:429")
    tr._client_as = tr._client_subito = lambda: None
    try:
        st = Counter()
        out = CM._controlla(None, [{"id": i, "source": "subito", "url": "u"} for i in range(20)], st)
    finally:
        tr._controlla_annuncio, tr._client_as, tr._client_subito = vecchi
    assert out["aperte"] == CM.STOP_ERRORI and st["fermato_blocchi"] == 1


# ------------------------------------------------------------------ B. telefono: ordine e inutili
def test_prioritarie_in_ordine_e_inutili():
    s = scovo.soglia(6000)
    items = [auto(1, prezzo=6000, margine=s, tel=False),
             auto(2, prezzo=6000, margine=2 * s, tel=False),                      # migliore: prima
             auto(3, prezzo=6000, margine=200, tel=False),                         # margine sotto: inutile
             auto(4, prezzo=6000, margine=s, tel=False, prov="RM"),                # Roma: inutile
             auto(5, prezzo=6000, margine=3 * s, tel=False),                       # non tolta al telefono
             auto(6, prezzo=6000, margine=s, tel=False, fonte="Facebook Marketplace")]
    fonte = {it["id"]: it["fonte"] for it in items}
    try:
        n = TN.segna_prioritari(items, [1, 2, 3, 4, 6], fonte, g=G, out=[])
        assert n == 2 and TN.ORDINE == (2, 1) and TN.PRIORITARI == frozenset({1, 2})
        assert TN.INUTILI == frozenset({3, 4})                                    # 6: altra fonte, non si apre
        assert TN.utili_e_ordine() == ((2, 1), frozenset({3, 4}))
        TN._STATO["t"] = time.time() - (TN.FRESCHI_MIN + 1) * 60                   # elenco vecchio: come prima
        assert TN.utili_e_ordine() is None
    finally:
        TN.PRIORITARI, TN.ORDINE, TN.INUTILI = frozenset(), (), frozenset()
        TN._STATO["t"] = 0.0


def test_commerciante_sotto_il_tetto_pieno_e_inutile():
    s = scovo.soglia(6000)
    g = SR.Regole(commercianti_totale=2)
    comm = sorted([3 * s, 3 * s])                                  # margini dei commercianti già sul sito (tetto pieno)
    debole = auto(1, prezzo=6000, margine=s, tel=False, seller="commerciante", _tipo="comm_lontano")
    forte = auto(2, prezzo=6000, margine=4 * s, tel=False, seller="commerciante", _tipo="comm_lontano")
    privato = auto(3, prezzo=6000, margine=s, tel=False)
    assert TN._sotto_tetto(debole, g, comm, []) is True            # col numero resterebbe comunque fuori
    assert TN._sotto_tetto(forte, g, comm, []) is False            # prenderebbe il posto di una più debole: utile
    assert TN._sotto_tetto(privato, g, comm, []) is False          # i privati non hanno tetto
    assert TN._sotto_tetto(debole, SR.Regole(commercianti_totale=3), comm, []) is False


def test_telefoni_candidate_salta_le_inutili_e_segue_l_ordine():
    tr._TESTO_FATTI.clear()
    tr._TOLTI_DIAG["t"] = 0.0
    TN.PRIORITARI, TN.ORDINE, TN.INUTILI = frozenset({11, 12}), (12, 11), frozenset({10})
    TN._STATO["t"] = time.time()
    _azzera_tel()
    try:
        with env(TR_PAUSA_S="0", TEL_SUBITO_API=None, TEL_CAND_SUBITO=None):
            c = ConnTel([], [])
            st = tr.telefoni_candidate(c, [10, 11, 12])
    finally:
        TN.PRIORITARI, TN.ORDINE, TN.INUTILI = frozenset(), (), frozenset()
        TN._STATO["t"] = 0.0
        tr._TESTO_FATTI.clear()
    auto_p = dict(c.select)["auto"]
    assert auto_p[0] == [11, 12] and st["saltate_inutili"] == 1          # la 10 non si apre
    assert auto_p[3] == [12, 11] and auto_p[6] == [12, 11]               # prima la migliore


def test_log_solo_conteggi(caplog=None):
    righe = []

    class H(logging.Handler):
        def emit(self, rec):
            righe.append(rec.getMessage())
    h = H()
    logging.getLogger("tempo_reale").addHandler(h)
    logging.getLogger("valutazione").addHandler(h)
    try:
        TN._STATO["log_t"] = 0.0
        TN.segna_prioritari([auto(98765, prezzo=6000, margine=200, tel=False)], [98765], {98765: "Subito"}, g=G)
        CM.diagnostica(Counter({"pagine": 3, "autoscout24:attivo": 2}))
    finally:
        logging.getLogger("tempo_reale").removeHandler(h)
        logging.getLogger("valutazione").removeHandler(h)
        TN.PRIORITARI, TN.ORDINE, TN.INUTILI = frozenset(), (), frozenset()
        TN._STATO["t"] = 0.0
    testo = " ".join(righe)
    assert "TEL_PRIORITA" in testo and "CONFRONTI_MIRATI" in testo and "98765" not in testo


# ------------------------------------------------------------------ Postgres vero: dal dati_insufficienti al sito
def test_postgres_confronti_mirati_strada_intera():
    from tests import test_valore_base as TVB
    from dealfinder.pricing import valutazione as VZ
    from dealfinder.pipeline.verify import _mark
    c = TVB._pg()
    if c is None:
        return
    for p in (9000, 9100, 9200):
        TVB._ins(c, p)                                             # 3 commercianti freschi: non bastano
    vecchi = [TVB._ins(c, p, visto_giorni=20) for p in (9300, 9400, 9500)]   # 3 non ricontrollati da 20 giorni
    TVB._ins(c, 9600, visto_giorni=90)                              # troppo vecchio: non si riapre
    buona = TVB._ins(c, 6000, sel="privato")
    es = VZ.valuta_e_scrivi(c, [buona], "test", pubblica=True)
    assert es.stats["dati_insufficienti:pochi_confronti:rinfrescabile"] == 1
    d = c.execute("SELECT dati FROM valutazioni_policy WHERE listing_id=%s", (buona,)).fetchone()["dati"]
    assert sorted(d["rinfresco"]["ids"]) == sorted(vecchi) and d["simili_D"] == 3
    aperti = []

    def controlla(conn, conf, st):
        for r in conf:
            aperti.append(r["id"])
            _mark(conn, r["id"], "attivo", r.get("price_eur"))    # vivi: tornano freschi
        return {"aperte": len(conf), "cambiati": [r["id"] for r in conf]}

    CM._PROVATE.clear()
    CM._GIORNO.update(g=None, pagine=0)
    st = CM.giro(c, controlla=controlla)
    CM._PROVATE.clear()
    assert sorted(aperti) == sorted(vecchi) and st["auto_da_rivalutare"] == 1, dict(st)
    r = c.execute("SELECT categoria, margine, dati FROM valutazioni_policy WHERE listing_id=%s", (buona,)).fetchone()
    # stessa stima del metodo coi 6 commercianti ora freschi: mediana 9.250, margine 3.250 (soglie invariate)
    assert r["categoria"] == "opportunita" and r["margine"] == 3250 and r["dati"]["n"] == 6
    assert c.execute("SELECT stage FROM listings WHERE id=%s", (buona,)).fetchone()["stage"] == "approfondito"
