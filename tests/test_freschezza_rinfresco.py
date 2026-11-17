"""17/11 notte (freschezza): auto buone che uscivano dal sito solo perché nessuno le ricontrollava.

scovo.LIST_SQL mostra solo le auto con GREATEST(last_checked_at, last_seen_at) nelle ultime FRESCHEZZA_ORE (48) ore.
La verifica (giro_verifica) riapre solo le auto già nell'elenco: un'auto buona uscita per un motivo passeggero o mai
entrata, se le ricerche non la rivedono, scadeva e non tornava più. giro_rinfresca riapre poche pagine per giro, prima
quelle che stanno per scadere, e rinfresca solo con la prova che l'annuncio è vivo. La regola delle 48 ore NON cambia.
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.pipeline import tempo_reale as tr     # noqa: E402

URL_SB = "https://www.subito.it/auto/fiat-panda-milano-612345678.htm"
URL_AS = "https://www.autoscout24.it/annunci/fiat-panda-1-2-abc"


def _riga(i, ore, cat="opportunita", prov="MI", tel=True, top=False, margine=1000, source="subito"):
    return {"id": i, "source": source, "url": URL_SB if source == "subito" else URL_AS, "price_eur": 5000,
            "telefono_ok": tel, "prov": prov, "ore": ore, "categoria": cat, "margine": margine, "top": top}


def test_zona_del_rinfresco_e_quella_del_sito():
    z = tr._zona()
    assert {"MI", "BG", "TO", "RA"} <= z and "RE" not in z and "RM" not in z


def test_la_regola_delle_48_ore_non_cambia():
    from dealfinder.web import scovo
    assert tr.FRESCHEZZA_ORE == 48
    assert "GREATEST(l.last_checked_at, l.last_seen_at) > now() - interval '48 hours'" in scovo.LIST_SQL
    # la base del rinfresco è quella dell'elenco, senza la freschezza
    assert "l.stage_reason IN ('opportunita','da_verificare','incidentata')" in scovo.LIST_SQL
    assert "l.stage_reason IN ('opportunita','da_verificare','incidentata')" in tr._BASE_FRESCHEZZA


def test_ordine_prima_sul_sito_poi_buone_della_zona_vicine_alla_scadenza():
    righe = [
        _riga(1, 40, cat="non_conveniente"),                 # né buona né sul sito: non si riapre
        _riga(2, 47),                                        # buona, zona, quasi scaduta
        _riga(3, 38),                                        # buona, zona, scade più tardi
        _riga(4, 60),                                        # buona, zona, già scaduta da 12 ore
        _riga(5, 100),                                       # buona, zona, scaduta da più tempo
        _riga(6, 45, prov="RE"),                             # buona fuori zona
        _riga(7, 45, tel=False),                             # buona senza numero
        _riga(8, 90, cat="non_conveniente"),                 # sul sito di recente (passa davanti a tutte)
        _riga(9, 41, cat="non_conveniente", top=True),       # tra le top: buona
        _riga(10, 47, margine=5000),                         # come la 2 ma margine più alto
    ]
    ord_ = [r["id"] for r in tr.ordina_rinfresco(righe, sul_sito={8}, zona={"MI"}, adesso=1e9, tentati={}, fresc=48)]
    assert 1 not in ord_
    assert ord_[0] == 8
    # fresche prima (dalla più vicina alla scadenza), a parità di ore il margine più alto, poi le scadute più recenti
    assert ord_[1:7] == [10, 2, 9, 3, 4, 5]
    assert set(ord_[7:]) == {6, 7}


def test_tentate_da_poco_senza_esito_si_saltano():
    righe = [_riga(2, 47), _riga(3, 40)]
    ord_ = tr.ordina_rinfresco(righe, set(), {"MI"}, adesso=10_000, tentati={2: 9_000}, riprova_s=7200, fresc=48)
    assert [r["id"] for r in ord_] == [3]
    ord_ = tr.ordina_rinfresco(righe, set(), {"MI"}, adesso=20_000, tentati={2: 9_000}, riprova_s=7200, fresc=48)
    assert [r["id"] for r in ord_] == [2, 3]


class _Conn:
    def __init__(self, righe):
        self.righe, self.sql = righe, []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        righe = self.righe if "WITH vecchie" in sql and "LIMIT %(quante)s" in sql else [{}]

        class R:
            def fetchall(s):
                return righe

            def fetchone(s):
                return righe[0] if righe else None
        return R()

    def commit(self):
        pass

    def rollback(self):
        pass


def _giro(righe, esiti, sul_sito=()):
    from dealfinder.pipeline import verify as V
    segnati, lenient = [], []
    v_mark, v_ctrl, v_as, v_sb = V._mark, tr._controlla_annuncio, tr._client_as, tr._client_subito
    v_t = dict(tr._freschezza_contata)
    tr._rinfresco_tentati.clear()
    try:
        V._mark = lambda conn, lid, st, price=None, motivo=None, sito=False: segnati.append((lid, st, motivo, sito))

        def ctrl(r, cas, csb, cfb=None, lenient_=True, **k):
            lenient.append(k.get("lenient", lenient_))
            return (r,) + esiti[r["id"]]
        tr._controlla_annuncio = ctrl
        tr._client_as = tr._client_subito = lambda: object()
        tr._freschezza_contata["t"] = 1e18           # niente conteggio in questo test
        st = tr.giro_rinfresca(_Conn(righe), sul_sito)
    finally:
        V._mark, tr._controlla_annuncio, tr._client_as, tr._client_subito = v_mark, v_ctrl, v_as, v_sb
        tr._freschezza_contata.update(v_t)
    return st, segnati, lenient


def test_giro_rinfresca_tiene_fresche_riporta_e_toglie_solo_con_prova():
    righe = [_riga(1, 40), _riga(2, 60, source="autoscout24"), _riga(3, 45), _riga(4, 50), _riga(5, 47, cat="non_conveniente")]
    esiti = {1: ("attivo", "pagina_app"), 2: ("attivo", None), 3: ("scomparso", "404"), 4: ("errore", None)}
    st, segnati, lenient = _giro(righe, esiti)
    assert st["scelte"] == 4 and st["candidate"] == 5                     # la 5 (non buona, mai sul sito) no
    assert st["_tornati"] == [2] and st["tenute_fresche"] == 1 and st["_spariti"] == [3]
    assert sorted((s[0], s[1]) for s in segnati) == [(1, "attivo"), (2, "attivo"), (3, "scomparso")]
    assert all(s[3] is False for s in segnati)            # non era sul sito: niente misura delle vendite del sito
    assert 4 in tr._rinfresco_tentati and 4 not in [s[0] for s in segnati]   # errore: nessuna modifica, riprova dopo
    assert set(lenient) == {False}                        # serve la prova che è viva
    tr._rinfresco_tentati.clear()


def test_giro_rinfresca_rispetta_il_tetto_per_giro_e_si_spegne():
    righe = [_riga(i, 40 + i / 100) for i in range(1, 100)]
    esiti = {i: ("attivo", None) for i in range(1, 100)}
    vecchio = os.environ.get("RINFRESCA_MAX")
    try:
        os.environ["RINFRESCA_MAX"] = "7"
        st, segnati, _ = _giro(righe, esiti)
        assert st["scelte"] == 7 and len(segnati) == 7
        os.environ["RINFRESCA"] = "0"
        st, segnati, _ = _giro(righe, esiti)
        assert not segnati and not st
    finally:
        os.environ.pop("RINFRESCA", None)
        if vecchio is None:
            os.environ.pop("RINFRESCA_MAX", None)
        else:
            os.environ["RINFRESCA_MAX"] = vecchio


class _R:
    def __init__(self, code, text, url=URL_SB):
        self.status_code, self.text, self.url = code, text, url


class _Cl:
    def __init__(self, r):
        self.r = r

    def get(self, url):
        return self.r


def test_controlla_annuncio_subito_senza_prova_non_rinfresca():
    # pagina 200 dello stesso annuncio ma non riconosciuta: per la verifica del sito resta 'attivo' (lenient), per
    # il rinfresco è 'errore' (non torna sul sito senza la prova che è viva)
    cl = _Cl(_R(200, "<html>pagina strana</html>"))
    r = {"id": 1, "source": "subito", "url": URL_SB}
    v = os.environ.get("TR_PAUSA_S")
    os.environ["TR_PAUSA_S"] = "0"
    try:
        assert tr._controlla_annuncio(r, None, cl, None, lenient=True)[1] == "attivo"
        assert tr._controlla_annuncio(r, None, cl, None, lenient=False)[1] == "errore"
        # le vendute escono con la prova anche dal rinfresco
        assert tr._controlla_annuncio(r, None, _Cl(_R(404, "")), None, lenient=False)[1:] == ("scomparso", "404")
        assert tr._controlla_annuncio(r, None, _Cl(_R(200, "<html></html>", url="https://www.subito.it/annunci-lombardia/")),
                                      None, lenient=False)[1:] == ("scomparso", "reindirizzato")
        assert tr._controlla_annuncio(r, None, _Cl(_R(200, "Questo annuncio è scaduto")), None,
                                      lenient=False)[1:] == ("scomparso", "scaduto")
    finally:
        if v is None:
            os.environ.pop("TR_PAUSA_S", None)
        else:
            os.environ["TR_PAUSA_S"] = v


def test_giro_verifica_usa_ancora_lenient_per_le_auto_del_sito():
    from dealfinder.pipeline import verify as V
    viste, segnati = [], []

    class Conn(_Conn):
        def execute(self, sql, params=None):
            class R:
                def fetchall(s):
                    return [{"id": 1, "source": "subito", "url": URL_SB, "price_eur": 1, "telefono_ok": True}]
            return R()
    v = (V._mark, tr._controlla_annuncio, tr._client_as, tr._client_subito, tr._client_fb)
    try:
        V._mark = lambda conn, lid, st, price=None, motivo=None, sito=False: segnati.append((lid, st, sito))
        tr._controlla_annuncio = lambda r, cas, csb, cfb=None, lenient=True: (viste.append(lenient) or (r, "attivo", None))
        tr._client_as = tr._client_subito = tr._client_fb = lambda: object()
        st = tr.giro_verifica(Conn([]), [1])
    finally:
        V._mark, tr._controlla_annuncio, tr._client_as, tr._client_subito, tr._client_fb = v
    assert viste == [True] and segnati == [(1, "attivo", True)] and st["subito:attivo"] == 1


def test_telefoni_candidate_autoscout_letta_vale_prova_di_vita():
    from dealfinder.pipeline import verify as V
    segnati = []

    class Conn(_Conn):
        def execute(self, sql, params=None):
            class R:
                def fetchall(s):
                    return [{"id": 7, "source": "autoscout24", "source_id": "x", "url": URL_AS, "make": "fiat",
                             "model": "panda", "price_eur": 5000, "province": "MI"}] if "FROM listings" in sql else []

                def fetchone(s):
                    return None
            return R()
    v = (V._mark, tr._as_esito, tr._client_as, os.environ.get("TR_PAUSA_S"))
    try:
        os.environ["TR_PAUSA_S"] = "0"
        V._mark = lambda conn, lid, st, price=None, motivo=None, sito=False: segnati.append((lid, st))
        tr._as_esito = lambda c, u: ("attivo", {"seller": {}}, None)
        tr._client_as = lambda: object()
        st = tr.telefoni_candidate(Conn([]), [7])
    finally:
        V._mark, tr._as_esito, tr._client_as = v[:3]
        if v[3] is None:
            os.environ.pop("TR_PAUSA_S", None)
        else:
            os.environ["TR_PAUSA_S"] = v[3]
    assert segnati == [(7, "attivo")] and st["autoscout24:rinfrescata"] == 1


# ------------------------------------------------------------------ query vere su un Postgres temporaneo (se c'è)
def test_query_vere_rinfresco_e_conteggi():
    from tests import pg_vero
    c = pg_vero.nuovo_db("freschezza_rinfresco")
    if c is None:
        print("SALTATO test_query_vere_rinfresco_e_conteggi: Postgres temporaneo non disponibile")
        return
    for sql in ("CREATE TABLE listings (id BIGINT PRIMARY KEY, source TEXT, url TEXT, price_eur INT, telefono_ok BOOLEAN, "
                "province TEXT, stage TEXT, status TEXT, stage_reason TEXT, last_checked_at TIMESTAMPTZ, last_seen_at TIMESTAMPTZ)",
                "CREATE TABLE valutazioni_policy (listing_id BIGINT, policy_version TEXT, computed_at TIMESTAMPTZ DEFAULT now(), "
                "categoria TEXT, margine INT, riassunto JSONB DEFAULT '{}')",
                "CREATE TABLE top_auto (listing_id BIGINT PRIMARY KEY, posizione INT, margine INT)"):
        c.execute(sql)
    # ore dall'ultimo controllo: 10 (fresca), 40 (in scadenza), 60 (fuori da poco), 200 (fuori da 8 giorni)
    for i, ore, cat, extra in ((1, 10, "opportunita", ""), (2, 40, "opportunita", ""), (3, 60, "candidato", ""),
                               (4, 200, "opportunita", ""), (5, 60, "non_conveniente", ""), (6, 70, None, "top"),
                               (7, 40, "opportunita", "venduta"), (8, 60, "opportunita", "fuori_zona")):
        c.execute("INSERT INTO listings VALUES (%s, 'subito', 'u', 5000, true, %s, 'approfondito', %s, 'opportunita', "
                  "NULL, now() - make_interval(hours => %s))",
                  (i, "RE" if extra == "fuori_zona" else "MI", "scomparso" if extra == "venduta" else "attivo", ore))
        if cat:
            c.execute("INSERT INTO valutazioni_policy (listing_id, policy_version, computed_at, categoria, margine) "
                      "VALUES (%s, 'v', now() - interval '1 day', 'non_conveniente', 0), (%s, 'w', now(), %s, 900)", (i, i, cat))
        if extra == "top":
            c.execute("INSERT INTO top_auto VALUES (%s, 1, 500)", (i,))
    p = {"dopo": 36.0, "fino": 7 * 24.0, "quante": 5000}
    righe = {r["id"]: r for r in c.execute(tr.RINFRESCO_SQL, p).fetchall()}
    assert set(righe) == {2, 3, 5, 6, 8}                # né la fresca, né quella di 8 giorni, né la venduta
    assert righe[3]["categoria"] == "candidato" and righe[6]["top"] is True and righe[2]["margine"] == 900
    ord_ = [r["id"] for r in tr.ordina_rinfresco(list(righe.values()), set(), {"MI"}, tentati={}, fresc=48)]
    assert ord_[:3] == [2, 3, 6] and ord_[-1] == 8 and 5 not in ord_
    k = tr.conta_freschezza(c)
    assert k["fresche"] == 2 and k["in_scadenza"] == 1          # fresche: 1, 2 (attive, < 48 h); in scadenza: 2
    assert k["fuori_freschezza"] == 5 and k["fuori_entro_7g"] == 4
    assert k["buone_in_scadenza"] == 1 and k["buone_fuori"] == 4 and k["buone_fuori_7g_zona"] == 2   # 3 e 6 (8 fuori zona)
    assert k["opportunita_fuori"] == 2 and k["candidato_fuori"] == 1
