"""17/11 (notte-10): da "valutata buona" a "pubblicata" su Postgres VERO (tests/pg_scheda.py, tutte le migrazioni).

- guardia sulle scritture: la condizione dell'upsert nel database decide come valutazione.deve_scrivere;
- promozione (valutazione.promuovi): ogni auto buona secondo la riga salvata, con il numero, va sul sito (stage
  'approfondito', motivo opportunita/da_verificare, riga in valuations) e si vede nella lista del sito
  (scovo.LIST_SQL); anche quando la nuova valutazione buona è rifiutata dalla guardia (salvata buona di altra
  categoria) o aspetta il giudice; mai gli annunci scartati per sempre né le incidentate; mai quando la nuova
  valutazione rifiutata NON è buona (in dubbio la più prudente);
- riprova del giudice e recupero dei confronti (fonti di FONTI_CHE_PUBBLICANO) ora pubblicano; il cron no;
- tempo_reale.valuta_e_pubblica con la nuova rifiutata ma buona;
- utilitarie: un'auto approfondita con un motivo che il sito non mostra viene ripresa.
Se Postgres non c'è, i test non fanno nulla."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.pricing import policy_v2 as PV          # noqa: E402
from dealfinder.pricing import valutazione as V         # noqa: E402

_C: dict = {}


def _pg():
    if "c" not in _C:
        try:
            import pg_scheda as P
            c = P.nuovo_db("promozione")
            if c is not None:
                P.migra_tutte(c)
        except Exception:
            c = None
        _C["c"] = c
    c = _C["c"]
    if c is not None:
        for t in ("valuations", "valutazioni_policy", "top_auto", "listings"):
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
    return c


_N = {"i": 0}


def _auto(c, stage="nuovo", reason=None, tel=True, prezzo=8000, fonte="subito") -> int:
    _N["i"] += 1
    sid = f"p{_N['i']}"
    return c.execute(
        "INSERT INTO listings (source, source_id, url, make, model, fuel, year, mileage_km, price_eur, province, region, "
        "seller_type, status, telefono_ok, stage, stage_reason, last_seen_at, last_checked_at) VALUES (%s, %s, %s, "
        "'fiat', 'panda', 'benzina', 2016, 80000, %s, 'MI', 'lombardia', 'privato', 'attivo', %s, %s, %s, now(), "
        "now() - interval '3 days') RETURNING id",
        (fonte, sid, "https://x/" + sid, prezzo, tel, stage, reason)).fetchone()["id"]


def _v(cat, n=10, M=11000, margine=2500, acquisto=8000):
    return {"categoria": cat, "n": n, "M": M, "R_finale": M - 500, "margine": margine, "acquisto": acquisto,
            "soglia": 1500, "metodo": "test", "motivi": ["prova"], "confronti": [{"prezzo": M}]}


def _salva(c, lid, v, imp_a="a", imp_r="r", ore_fa=0):
    assert V._scrivi(c, lid, v, None, imp_a, imp_r, "test", 36)
    if ore_fa:
        c.execute("UPDATE valutazioni_policy SET computed_at = now() - make_interval(hours => %s) WHERE listing_id=%s",
                  (ore_fa, lid))


def _stato(c, lid):
    return c.execute("SELECT stage, stage_reason FROM listings WHERE id=%s", (lid,)).fetchone()


def _valuations(c, lid):
    return c.execute("SELECT engine_version, private_median, confidence FROM valuations WHERE listing_id=%s "
                     "ORDER BY created_at DESC", (lid,)).fetchall()


def _sul_sito(c) -> set:
    from dealfinder.web import scovo
    return {r["id"] for r in c.execute("SELECT q.id FROM (" + scovo.LIST_SQL + ") q", {"me": 0}).fetchall()}


# ------------------------------------------------------------------ guardia: database = deve_scrivere

def test_guardia_del_database_uguale_a_deve_scrivere():
    c = _pg()
    if c is None:
        return
    lid = _auto(c)
    _salva(c, lid, _v("non_conveniente", n=10))
    casi = [  # (n nuovo, impronta auto, impronta regole, ore fa della salvata, atteso)
        (6, "a", "r", 0, False),          # meno confronti, stessa auto e regole: resta la salvata
        (10, "a", "r", 0, True),          # pari
        (6, "b", "r", 0, True),           # auto cambiata
        (6, "a", "r2", 0, True),          # regole cambiate
        (6, "a", "r", 40, True),          # salvata più vecchia di 36 ore
    ]
    for n, ia, ir, ore_fa, atteso in casi:
        c.execute("UPDATE valutazioni_policy SET n=10, impronta_auto='a', impronta_regole='r', "
                  "computed_at = now() - make_interval(hours => %s) WHERE listing_id=%s", (ore_fa, lid))
        es = V._salvate(c, [lid])[lid]
        ok, perche = V.deve_scrivere(es, n, ia, ir, ore=36)
        scritta = V._scrivi(c, lid, _v("opportunita", n=n), None, ia, ir, "test", 36)
        assert ok == atteso and scritta == atteso, (n, ia, ir, ore_fa, perche, scritta)


# ------------------------------------------------------------------ promozione

def _esito(lid, salvata, nuova=None, scritta=True, perche="dati_pari_o_piu", tel=True):
    es = V.Esito()
    es.salvata[lid] = salvata
    es.valutazioni[lid] = nuova
    es.perche[lid] = perche
    es.telefono[lid] = tel
    if scritta:
        es.scritte.add(lid)
    return es


def _unisci(*esiti):
    es = V.Esito()
    for e in esiti:
        for k in ("salvata", "valutazioni", "perche", "telefono"):
            getattr(es, k).update(getattr(e, k))
        es.scritte |= e.scritte
    return es


def test_promuove_ogni_buona_e_si_vede_sul_sito():
    c = _pg()
    if c is None:
        return
    a = _auto(c)                                         # scritta ora, opportunità
    _salva(c, a, _v("opportunita"))
    b = _auto(c)                                         # salvata candidato, nuova opportunità rifiutata (meno confronti)
    _salva(c, b, _v("candidato", n=12, M=10500))
    d = _auto(c)                                         # salvata opportunità, nuova in attesa del giudice
    _salva(c, d, _v("opportunita", M=11200))
    e = _auto(c)                                         # salvata opportunità, nuova NON buona rifiutata: prudenza
    _salva(c, e, _v("opportunita", n=12))
    es = _unisci(_esito(a, "opportunita", _v("opportunita")),
                 _esito(b, "candidato", _v("opportunita", n=8, M=12000), scritta=False, perche="meno_confronti"),
                 _esito(d, "opportunita", dict(_v("dati_insufficienti"), giudice_mancante=True), scritta=False,
                        perche="giudice_mancante"),
                 _esito(e, "opportunita", _v("non_conveniente", n=8, margine=200), scritta=False, perche="meno_confronti"))
    st = Counter()
    assert V.promuovi(c, es, "prova", st) == 3
    assert _stato(c, a) == {"stage": "approfondito", "stage_reason": "opportunita"}
    assert _stato(c, b) == {"stage": "approfondito", "stage_reason": "da_verificare"}      # in dubbio da verificare
    assert _stato(c, d) == {"stage": "approfondito", "stage_reason": "opportunita"}
    assert _stato(c, e)["stage"] == "nuovo" and not _valuations(c, e)
    # la riga in valuations è quella SALVATA (la stessa che il sito legge), non la nuova rifiutata
    assert _valuations(c, b)[0]["private_median"] == 10500 and _valuations(c, d)[0]["private_median"] == 11200
    assert st["pubblicate"] == 3 and st["pubblica:dalla_salvata"] == 2
    assert {a, b, d} <= _sul_sito(c) and e not in _sul_sito(c)


def test_non_ripubblica_scartate_per_sempre_incidentate_e_senza_numero():
    c = _pg()
    if c is None:
        return
    dop = _auto(c, stage="scartato", reason="doppione")
    man = _auto(c, stage="scartato", reason="analisi_manuale: foto di un'altra auto")
    tru = _auto(c, stage="scartato", reason="truffa | segnale")
    inc = _auto(c, stage="approfondito", reason="incidentata")
    senza = _auto(c, tel=False)
    vecchio = _auto(c, stage="approfondito", reason="scartata")     # vecchio approfondimento: ora buona, va ripresa
    ids = [dop, man, tru, inc, senza, vecchio]
    for i in ids:
        _salva(c, i, _v("opportunita"))
    es = _unisci(*[_esito(i, "opportunita", _v("opportunita"), tel=(i != senza)) for i in ids])
    es.telefono[senza] = False
    st = Counter()
    assert V.promuovi(c, es, "prova", st) == 1
    assert _stato(c, dop)["stage_reason"] == "doppione" and _stato(c, man)["stage"] == "scartato"
    assert _stato(c, tru)["stage"] == "scartato" and _stato(c, inc)["stage_reason"] == "incidentata"
    assert _stato(c, senza)["stage"] == "nuovo" and st["valuta:senza_numero"] == 1
    assert _stato(c, vecchio) == {"stage": "approfondito", "stage_reason": "opportunita"}
    assert st["pubblica:scarto_fisso_o_incidentata"] == 4
    assert not any(_valuations(c, i) for i in (dop, man, tru, inc, senza))


# ------------------------------------------------------------------ valuta_e_scrivi: chi pubblica

class _Finta:
    """valuta_con_ripiego finta: categoria e confronti decisi dal test (le regole non c'entrano qui)."""

    def __init__(self, esiti):
        self.esiti = esiti

    def __call__(self, conn, t, mercato, soglia, cfg, cache, giudice=True):
        return dict(self.esiti[t["id"]], acquisto=t["price_eur"])


def _con_finta(esiti, fn):
    from dealfinder.pricing import ricalcolo as R
    prima = R.valuta_con_ripiego
    R.valuta_con_ripiego = _Finta(esiti)
    try:
        return fn()
    finally:
        R.valuta_con_ripiego = prima


def test_riprova_del_giudice_e_recupero_pubblicano_il_cron_no():
    c = _pg()
    if c is None:
        return
    r1, r2, cr = _auto(c), _auto(c), _auto(c)
    esiti = {r1: _v("opportunita"), r2: _v("candidato"), cr: _v("opportunita")}
    es1 = _con_finta(esiti, lambda: V.valuta_e_scrivi(c, [r1], "riprova-giudice"))
    es2 = _con_finta(esiti, lambda: V.valuta_e_scrivi(c, [r2], "cron-recupero"))
    es3 = _con_finta(esiti, lambda: V.valuta_e_scrivi(c, [cr], "cron"))
    assert es1.stats["pubblicate"] == 1 and es2.stats["pubblicate"] == 1 and not es3.stats["pubblicate"]
    assert _stato(c, r1)["stage"] == "approfondito" and _stato(c, r2)["stage_reason"] == "da_verificare"
    assert _stato(c, cr)["stage"] == "nuovo"
    assert {r1, r2} <= _sul_sito(c)


def test_tempo_reale_pubblica_anche_la_buona_rifiutata_dalla_guardia():
    from dealfinder.pipeline import tempo_reale as T
    c = _pg()
    if c is None:
        return
    lid = _auto(c)
    # prima valutazione: candidato con 12 confronti (scritta dalla strada vera)
    _con_finta({lid: _v("candidato", n=12, M=10400)}, lambda: V.valuta_e_scrivi(c, [lid], "cron", pubblica=False))
    assert _stato(c, lid)["stage"] == "nuovo"
    # seconda: opportunità con 9 confronti → rifiutata (meno confronti), ma entrambe buone: va sul sito
    st = Counter()
    _con_finta({lid: _v("opportunita", n=9, M=12500)}, lambda: T.valuta_e_pubblica(c, [lid], st))
    assert st["valuta:scrittura:meno_confronti"] == 1 and st["pubblicate"] == 1
    assert _stato(c, lid) == {"stage": "approfondito", "stage_reason": "da_verificare"}
    assert _valuations(c, lid)[0]["private_median"] == 10400          # la salvata, non la nuova (più alta)
    assert lid in _sul_sito(c)
    # terza: non conveniente. Il motivo scritto dalla pubblicazione fa parte dell'impronta dell'auto, quindi si scrive
    # (auto_cambiata): niente nuova pubblicazione (il sito la toglie per la categoria salvata)
    st = Counter()
    _con_finta({lid: _v("non_conveniente", n=5, margine=100)}, lambda: T.valuta_e_pubblica(c, [lid], st))
    assert st["valuta:scrittura:auto_cambiata"] == 1 and not st["pubblicate"] and len(_valuations(c, lid)) == 1


def test_meno_confronti_buona_contata():
    c = _pg()
    if c is None:
        return
    lid = _auto(c)
    _con_finta({lid: _v("non_conveniente", n=12, margine=300)}, lambda: V.valuta_e_scrivi(c, [lid], "cron"))
    es = _con_finta({lid: _v("opportunita", n=8)}, lambda: V.valuta_e_scrivi(c, [lid], "cron"))
    assert es.stats["scrittura:meno_confronti"] == 1 and es.stats["scrittura:meno_confronti_buona_non_scritta"] == 1
    assert es.salvata[lid] == "non_conveniente" and _stato(c, lid)["stage"] == "nuovo"


# ------------------------------------------------------------------ utilitarie: approfondite con motivo vecchio

def test_utilitarie_riprende_le_approfondite_con_motivo_che_il_sito_non_mostra():
    from dealfinder.pricing import ricalcolo as R
    c = _pg()
    if c is None:
        return
    vecchio = _auto(c, stage="approfondito", reason="non_valutabile")
    gia = _auto(c, stage="approfondito", reason="da_verificare")
    inc = _auto(c, stage="approfondito", reason="incidentata")
    nuovo = _auto(c)
    esiti = {i: _v("opportunita") for i in (vecchio, gia, inc, nuovo)}
    prima = (R.senza_invariate, R.UTIL_SQL)
    try:
        R.senza_invariate = lambda conn, ids, var="": (list(ids), 0)
        R.UTIL_SQL = "SELECT id FROM listings WHERE %(lo)s > 0 AND %(hi)s > 0 AND %(anno)s > 0 AND %(km)s > 0 " \
                     "AND province = ANY(%(prov)s) ORDER BY id"
        import dealfinder.core.dal_testo as DT
        salva_dt = DT.completa_db
        DT.completa_db = lambda conn: {}
        try:
            out = _con_finta(esiti, lambda: R._utilitarie(c, pubblica=True))
        finally:
            DT.completa_db = salva_dt
    finally:
        R.senza_invariate, R.UTIL_SQL = prima
    assert _stato(c, vecchio) == {"stage": "approfondito", "stage_reason": "opportunita"}
    assert _stato(c, gia)["stage_reason"] == "da_verificare"          # già sul sito: non si tocca
    assert _stato(c, inc)["stage_reason"] == "incidentata"
    assert _stato(c, nuovo)["stage"] == "approfondito"
    assert out["promosse"] == 2
    assert {vecchio, gia, inc, nuovo} <= _sul_sito(c)


def test_fonti_che_pubblicano_sono_quelle_che_non_pubblicavano():
    # chi pubblica da sé (tempo reale, utilitarie, commercianti) NON deve pubblicare due volte dentro valuta_e_scrivi
    assert set(V.FONTI_CHE_PUBBLICANO) == {"riprova-giudice", "cron-recupero"}
    assert "doppione" in V.SCARTI_FISSI and json.dumps(PV.POLICY_VERSION)


# ------------------------------------------------------------------ confronti extra: cambio di prezzo di un'auto sul sito

def test_confronti_extra_cambio_di_prezzo_toglie_dal_sito_finche_non_si_rivaluta():
    from dealfinder.pipeline import confronti_extra as CE
    c = _pg()
    if c is None:
        return
    sul = _auto(c, stage="approfondito", reason="opportunita")
    _salva(c, sul, _v("opportunita"))
    c.execute(V.VALUATIONS_SQL, (sul, "x", 11000, 11000, 10500, 10, "[]", "affidabile", ["prova"]))
    c.execute("UPDATE listings SET last_checked_at = now() WHERE id=%s", (sul,))
    conf = _auto(c, stage="mercato", reason="fuori_regole")
    assert sul in _sul_sito(c)
    assert CE.rimetti_in_valutazione(c, sul, "invariato") == 0 and sul in _sul_sito(c)
    assert CE.rimetti_in_valutazione(c, sul, "prezzo") == 1
    assert _stato(c, sul) == {"stage": "nuovo", "stage_reason": "opportunita"} and sul not in _sul_sito(c)
    assert CE.rimetti_in_valutazione(c, conf, "prezzo") == 0 and _stato(c, conf)["stage"] == "mercato"
