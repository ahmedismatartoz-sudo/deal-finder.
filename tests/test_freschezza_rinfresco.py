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
    # 28/11: raggio 200 km, decisione del cliente: RE, VR, GE dentro; PD (210 km) fuori
    assert {"MI", "BG", "TO", "RA", "RE", "VR", "GE"} <= z and "PD" not in z and "RM" not in z


def test_la_regola_delle_48_ore_non_cambia():
    from dealfinder.web import scovo
    assert tr.FRESCHEZZA_ORE == 48
    assert "GREATEST(l.last_checked_at, l.last_seen_at) > now() - interval '48 hours'" in scovo.LIST_SQL
    # la base del rinfresco è quella dell'elenco, senza la freschezza: frammenti comuni (non la stringa esatta, che
    # con notte-09 diventa btrim(split_part(...)) IN (... 'affidabile' ...))
    import re as _re
    for sql in (scovo.LIST_SQL, tr._BASE_FRESCHEZZA):
        assert "l.stage='approfondito' AND l.status='attivo'" in sql
        m = _re.search(r"stage_reason(?:, ''\), '\|', 1\)\))?\s+IN \(([^)]*)\)", sql)
        assert m, sql
        assert {"'opportunita'", "'da_verificare'", "'incidentata'"} <= {x.strip() for x in m.group(1).split(",")}
    assert "GREATEST" not in tr._BASE_FRESCHEZZA and "48" not in tr._BASE_FRESCHEZZA
    assert tr.motivo_stadio_sql("l.stage_reason") == "btrim(split_part(COALESCE(l.stage_reason, ''), '|', 1))"
    assert tr.motivo_stadio_sql("l.stage_reason") + " IN ('opportunita','affidabile','da_verificare','incidentata')" \
        in tr._BASE_FRESCHEZZA
    if hasattr(scovo, "motivo_stadio_sql"):            # dopo il merge con notte-09: lo stesso taglio
        assert scovo.motivo_stadio_sql("l.stage_reason") == tr.motivo_stadio_sql("l.stage_reason")


def test_ordine_prima_sul_sito_poi_buone_della_zona_vicine_alla_scadenza():
    righe = [
        _riga(1, 40, cat="non_conveniente"),                 # né buona né sul sito: non si riapre
        _riga(2, 47),                                        # buona, zona, quasi scaduta
        _riga(3, 38),                                        # buona, zona, scade più tardi
        _riga(4, 60),                                        # buona, zona, già scaduta da 12 ore
        _riga(5, 100),                                       # buona, zona, scaduta da più tempo
        _riga(6, 45, prov="RE"),                             # buona fuori zona
        _riga(7, 45, tel=False),                             # buona senza numero: non si riapre (telefono obbligatorio)
        _riga(8, 90, cat="non_conveniente"),                 # sul sito di recente (passa davanti a tutte)
        _riga(9, 41, cat="non_conveniente", top=True),       # tra le top: buona
        _riga(10, 47, margine=5000),                         # come la 2 ma margine più alto
    ]
    ord_ = [r["id"] for r in tr.ordina_rinfresco(righe, sul_sito={8}, zona={"MI"}, adesso=1e9, tentati={}, fresc=48)]
    assert 1 not in ord_ and 7 not in ord_
    assert ord_[0] == 8
    # fresche prima (dalla più vicina alla scadenza), a parità di ore il margine più alto, poi le scadute più recenti
    assert ord_[1:7] == [10, 2, 9, 3, 4, 5]
    assert ord_[7:] == [6]


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
    tr._rinfresco_pausa["fino"] = 0.0
    v_reg = tr.registra_sparita
    misure = []
    try:
        V._mark = lambda conn, lid, st, price=None, motivo=None, sito=False: segnati.append((lid, st, motivo, sito))

        def ctrl(r, cas, csb, cfb=None, lenient_=True, **k):
            lenient.append(k.get("lenient", lenient_))
            return (r,) + esiti[r["id"]]
        tr._controlla_annuncio = ctrl
        tr._client_as = tr._client_subito = lambda: object()
        tr._freschezza_contata["t"] = 1e18           # niente conteggio in questo test
        tr.registra_sparita = lambda conn, lid: misure.append(lid)
        conn = _Conn(righe)
        st = tr.giro_rinfresca(conn, sul_sito)
    finally:
        V._mark, tr._controlla_annuncio, tr._client_as, tr._client_subito = v_mark, v_ctrl, v_as, v_sb
        tr._freschezza_contata.update(v_t)
        tr.registra_sparita = v_reg
    _giro.conn, _giro.misure = conn, misure
    return st, segnati, lenient


def test_giro_rinfresca_tiene_fresche_riporta_e_toglie_solo_con_prova():
    righe = [_riga(1, 40), _riga(2, 60, source="autoscout24"), _riga(3, 45), _riga(4, 50), _riga(5, 47, cat="non_conveniente")]
    esiti = {1: ("attivo", None), 2: ("attivo", None), 3: ("scomparso", "404"), 4: ("errore", None)}
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
        tr._as_esito = lambda c, u: ("attivo", {"seller": {}, "tracking": {"price": "5000"}}, None)
        tr._client_as = lambda: object()
        st = tr.telefoni_candidate(Conn([]), [7])
    finally:
        V._mark, tr._as_esito, tr._client_as = v[:3]
        if v[3] is None:
            os.environ.pop("TR_PAUSA_S", None)
        else:
            os.environ["TR_PAUSA_S"] = v[3]
    assert segnati == [(7, "attivo")] and st["autoscout24:rinfrescata"] == 1
    # prezzo della pagina diverso da quello salvato: niente rinfresco, prezzo nuovo e rivalutazione
    segnati.clear()
    try:
        os.environ["TR_PAUSA_S"] = "0"
        V._mark = lambda conn, lid, st, price=None, motivo=None, sito=False: segnati.append((lid, st))
        tr._as_esito = lambda c, u: ("attivo", {"seller": {}, "tracking": {"price": "5900"}}, None)
        tr._client_as = lambda: object()
        st = tr.telefoni_candidate(Conn([]), [7])
    finally:
        V._mark, tr._as_esito, tr._client_as = v[:3]
        if v[3] is None:
            os.environ.pop("TR_PAUSA_S", None)
        else:
            os.environ["TR_PAUSA_S"] = v[3]
    assert segnati == [] and st["autoscout24:prezzo_cambiato"] == 1    # come giro_rinfresca: si rivaluta


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
                "CREATE TABLE top_auto (listing_id BIGINT PRIMARY KEY, posizione INT, margine INT)",
                "CREATE TABLE valuations (id BIGSERIAL PRIMARY KEY, listing_id BIGINT, created_at TIMESTAMPTZ DEFAULT now())",
                "CREATE TABLE price_events (id BIGSERIAL PRIMARY KEY, listing_id BIGINT, event TEXT, price_eur INT, "
                "at TIMESTAMPTZ NOT NULL DEFAULT now())"):
        c.execute(sql)
    # ore dall'ultimo controllo: 10 (fresca), 40 (in scadenza), 60 (fuori da poco), 200 (fuori da 8 giorni)
    for i, ore, cat, extra in ((1, 10, "opportunita", ""), (2, 40, "opportunita", ""), (3, 60, "candidato", ""),
                               (4, 200, "opportunita", ""), (5, 60, "non_conveniente", ""), (6, 70, None, "top"),
                               (7, 40, "opportunita", "venduta"), (8, 60, "opportunita", "fuori_zona"),
                               (9, 40, "opportunita", "motivo_lungo"), (10, 40, "opportunita", "affidabile"),
                               (11, 40, "opportunita", "motivo_altro"), (12, 40, "opportunita", "prezzo_dopo"),
                               (13, 40, "opportunita", "riapparso_dopo"), (14, 40, "opportunita", "prezzo_prima"),
                               (15, 40, None, "solo_valuations_prezzo_dopo"), (16, 40, "opportunita", "senza_valuations")):
        motivo = {"motivo_lungo": "opportunita | scomparso (404)", "affidabile": "affidabile",
                  "motivo_altro": "scartata | opportunita"}.get(extra, "opportunita")
        c.execute("INSERT INTO listings VALUES (%s, 'subito', 'u', 5000, true, %s, 'approfondito', %s, %s, "
                  "NULL, now() - make_interval(hours => %s))",
                  (i, "PD" if extra == "fuori_zona" else "MI",   # 28/11: raggio 200 km, decisione del cliente (RE dentro)
                   "scomparso" if extra == "venduta" else "attivo",
                   motivo, ore))
        if extra != "senza_valuations":
            c.execute("INSERT INTO valuations (listing_id, created_at) VALUES (%s, now() - interval '3 days')", (i,))
        if cat:
            c.execute("INSERT INTO valutazioni_policy (listing_id, policy_version, computed_at, categoria, margine) "
                      "VALUES (%s, 'v', now() - interval '1 day', 'non_conveniente', 0), "
                      "(%s, 'w', now() - interval '2 hours', %s, 900)", (i, i, cat))
        if extra == "top":
            c.execute("INSERT INTO top_auto VALUES (%s, 1, 500)", (i,))
        ev = {"prezzo_dopo": ("prezzo", "1 hour"), "riapparso_dopo": ("riapparso", "1 hour"),
              "prezzo_prima": ("prezzo", "5 hours"), "solo_valuations_prezzo_dopo": ("prezzo", "1 day")}.get(extra)
        if ev:          # 15: senza valutazioni_policy vale valuations.created_at (3 giorni fa): evento dopo
            c.execute("INSERT INTO price_events (listing_id, event, price_eur, at) "
                      "VALUES (%s, %s, 5000, now() - %s::interval)", (i, ev[0], ev[1]))
        c.execute("INSERT INTO price_events (listing_id, event, price_eur, at) "
                  "VALUES (%s, 'apparso', 5000, now() - interval '10 days')", (i,))
    p = {"dopo": 36.0, "fino": 7 * 24.0, "quante": 5000}
    righe = {r["id"]: r for r in c.execute(tr.RINFRESCO_SQL, p).fetchall()}
    # né la fresca, né quella di 8 giorni, né la venduta; il motivo con '| scomparso (404)' e 'affidabile' sì
    # (stesso taglio di scovo), 'scartata | opportunita' no; niente rinfresco con un 'prezzo'/'riapparso' dopo
    # l'ultima valutazione (12, 13, 15); un 'prezzo' PRIMA della valutazione non conta (14); senza valuations no (16)
    assert set(righe) == {2, 3, 5, 6, 8, 9, 10, 14}, sorted(righe)
    assert righe[3]["categoria"] == "candidato" and righe[6]["top"] is True and righe[2]["margine"] == 900
    for i in (9, 10, 14):
        righe.pop(i)
    ord_ = [r["id"] for r in tr.ordina_rinfresco(list(righe.values()), set(), {"MI"}, tentati={}, fresc=48)]
    assert ord_[:3] == [2, 3, 6] and ord_[-1] == 8 and 5 not in ord_
    c.execute("UPDATE listings SET status='scomparso' WHERE id >= 9")    # i conteggi sulle 8 auto di prima
    k = tr.conta_freschezza(c)
    assert k["fresche"] == 2 and k["in_scadenza"] == 1          # fresche: 1, 2 (attive, < 48 h); in scadenza: 2
    assert k["fuori_freschezza"] == 5 and k["fuori_entro_finestra"] == 4
    assert k["buone_in_scadenza"] == 1 and k["buone_fuori"] == 4 and k["buone_fuori_finestra_zona"] == 2   # 3 e 6 (8 fuori zona)
    assert k["opportunita_fuori"] == 2 and k["candidato_fuori"] == 1


# ------------------------------------------------------------------ correzioni del giudice (17/11)
def test_prezzo_cambiato_non_rinfresca_e_manda_in_rivalutazione():
    righe = [_riga(1, 60), _riga(2, 60), _riga(3, 40)]
    esiti = {1: ("attivo", "prezzo_cambiato:6500"), 2: ("attivo", "prezzo_illeggibile"), 3: ("attivo", None)}
    st, segnati, _ = _giro(righe, esiti)
    assert [s[0] for s in segnati] == [3]                 # solo quella con il prezzo uguale
    assert st["prezzo_cambiato"] == 1 and st["prezzo_illeggibile"] == 1 and st["_tornati"] == []
    sql = [(q, p) for q, p in _giro.conn.sql if q.startswith(("UPDATE", "INSERT"))]
    assert sql == [("UPDATE listings SET price_eur=%s, stage='nuovo' WHERE id=%s", (6500, 1)),
                   ("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'prezzo',%s)", (1, 6500))]
    assert 2 in tr._rinfresco_tentati                      # illeggibile = errore: riprova più tardi
    tr._rinfresco_tentati.clear()


def test_sparita_che_era_sul_sito_conta_come_del_sito():
    righe = [_riga(1, 60), _riga(2, 60)]
    st, segnati, _ = _giro(righe, {1: ("scomparso", "404"), 2: ("scomparso", "404")}, sul_sito={1})
    assert sorted((s[0], s[3]) for s in segnati) == [(1, True), (2, False)] and _giro.misure == [1]


def test_tanti_errori_di_fila_fermano_il_giro_e_mettono_in_pausa():
    righe = [_riga(i, 40 + i / 100) for i in range(1, 30)]
    vecchio = os.environ.get("TR_PARALLELI")
    try:
        os.environ["TR_PARALLELI"] = "1"
        st, segnati, lenient = _giro(righe, {i: ("errore", "blocco:403") for i in range(1, 30)})
        assert st["fermato_errori"] == 1 and len(lenient) == 8 and st["subito:saltato"] == 21
        assert tr._rinfresco_pausa["fino"] > 0
        # errori che non sono blocchi (scritta di rimozione, pagina non riconosciuta): nessuna pausa
        tr._rinfresco_pausa["fino"] = 0.0
        tr._rinfresco_tentati.clear()
        esiti = {i: ("errore", "scritta_rimozione" if i % 2 else None) for i in range(1, 30)}
        st, segnati, lenient = _giro(righe, esiti)
        assert "fermato_errori" not in st and len(lenient) == 29 and tr._rinfresco_pausa["fino"] == 0.0
        # blocchi intervallati da pagine lette: il conto riparte
        tr._rinfresco_tentati.clear()
        esiti = {i: (("errore", "blocco:429") if i % 5 else ("attivo", None)) for i in range(1, 30)}
        st, segnati, lenient = _giro(righe, esiti)
        assert "fermato_errori" not in st and len(lenient) == 29
        tr._rinfresco_tentati.clear()
        st, segnati, lenient = _giro(righe, {i: ("errore", "blocco:eccezione") for i in range(1, 30)})
        assert st["fermato_errori"] == 1 and len(lenient) == 8
    finally:
        tr._rinfresco_tentati.clear()
        if vecchio is None:
            os.environ.pop("TR_PARALLELI", None)
        else:
            os.environ["TR_PARALLELI"] = vecchio
    v = tr._freschezza_contata["t"]
    try:                                                  # nel giro dopo (pausa attiva) non si apre niente
        tr._freschezza_contata["t"] = 1e18
        st = tr.giro_rinfresca(_Conn(righe), ())
    finally:
        tr._freschezza_contata["t"] = v
        tr._rinfresco_pausa["fino"] = 0.0
    assert st["in_pausa"] == 1 and st["_tornati"] == []


APP = ('<script>self.__next_f.push([1,"{\\"ad\\":{\\"urn\\":\\"id:ad:612345678:list:1\\",\\"subject\\":\\"Fiat Panda\\",'
       '\\"features\\":[{\\"uri\\":\\"/price\\",\\"values\\":[{\\"key\\":\\"5000\\",\\"value\\":\\"5.000 €\\"}]}]}}"])</script>')


def _senza_pausa(fn):
    v = os.environ.get("TR_PAUSA_S")
    os.environ["TR_PAUSA_S"] = "0"
    try:
        return fn()
    finally:
        if v is None:
            os.environ.pop("TR_PAUSA_S", None)
        else:
            os.environ["TR_PAUSA_S"] = v


def test_subito_rinfresca_solo_con_lo_stesso_prezzo_nella_pagina():
    assert tr.prezzo_subito_pagina(APP, "612345678") == 5000
    r = {"id": 1, "source": "subito", "url": URL_SB, "price_eur": 5000}
    ctrl = lambda r, testo, len_=False: _senza_pausa(lambda: tr._controlla_annuncio(r, None, _Cl(_R(200, testo)), None, lenient=len_))
    assert ctrl(r, APP)[1:] == ("attivo", None)
    r2 = dict(r, price_eur=4500)                          # rialzo: nella pagina 5.000, salvato 4.500
    assert ctrl(r2, APP)[1:] == ("attivo", "prezzo_cambiato:5000")
    # prezzo non leggibile (il titolo e un "price" restano, la pagina vale "attiva"): non si rinfresca
    senza = APP.replace('{\\"uri\\":\\"/price\\",\\"values\\":[{\\"key\\":\\"5000\\",', '{\\"price\\":{\\"x\\":\\"y\\",')
    assert tr.prezzo_subito_pagina(senza, "612345678") is None
    assert ctrl(r, senza)[1:] == ("attivo", "prezzo_illeggibile")   # giro_rinfresca lo tratta come errore
    # due feature /price dopo l'urn: vale la più vicina (quella dell'annuncio)
    due = APP.replace('}]}]}}', '}]},{\\"uri\\":\\"/price\\",\\"values\\":[{\\"key\\":\\"9000\\"}]}]}}')
    assert tr.prezzo_subito_pagina(due, "612345678") == 5000
    # la verifica delle auto del sito (lenient) non guarda il prezzo
    assert ctrl(r2, APP, True)[1:] == ("attivo", "pagina_app")


def test_autoscout_senza_prova_o_con_altro_prezzo_non_rinfresca():
    import json as _j
    det = {"tracking": {"price": "5000"}, "seller": {}}
    pagina = ('<script id="__NEXT_DATA__" type="application/json">'
              + _j.dumps({"props": {"pageProps": {"listingDetails": det}}}) + '</script>')
    r = {"id": 2, "source": "autoscout24", "url": URL_AS, "price_eur": 5000}
    ctrl = lambda r, testo, len_=False: _senza_pausa(
        lambda: tr._controlla_annuncio(r, _Cl(_R(200, testo, url=URL_AS)), None, None, lenient=len_))
    assert ctrl(r, pagina)[1:] == ("attivo", None)
    tolto = pagina + "<div>Questo veicolo non è più disponibile</div>"
    assert ctrl(r, tolto)[1:] == ("errore", "scritta_rimozione")
    assert ctrl(dict(r, price_eur=4000), pagina)[1:] == ("attivo", "prezzo_cambiato:5000")
    assert ctrl(r, pagina.replace('"5000"', '""'))[1:] == ("attivo", "prezzo_illeggibile")
    assert ctrl(r, tolto, True)[1] == "attivo"            # verifica delle auto del sito: come prima


# ------------------------------------------------------------------ correzioni del giudice (17/11, seconda parte)
NUM = "612345678"


def _app(*feature_json, urn=NUM, prima="", dopo=""):
    """Pagina App Router finta (JSON escapato in self.__next_f) con le feature date, in formato JSON normale."""
    corpo = ('{"ad":{"urn":"id:ad:%s:list:1","subject":"Fiat Panda","features":[%s]}}' % (urn, ",".join(feature_json)))
    tutto = prima + corpo + dopo
    return '<script>self.__next_f.push([1,"%s"])</script>' % tutto.replace('"', '\\"')


def test_prezzo_subito_pagina_ordine_delle_chiavi_e_formati():
    f = tr.prezzo_subito_pagina
    # formato classico
    assert f(_app('{"uri":"/price","values":[{"key":"5000","value":"5.000 €"}]}'), NUM) == 5000
    # altre chiavi prima di "values" e "key" dopo "value"
    assert f(_app('{"label":"Prezzo","uri":"/price","type":"list","values":[{"value":"5.000 €","key":"5000"}]}'), NUM) == 5000
    # "values" prima di "uri"
    assert f(_app('{"values":[{"key":"5000","value":"5.000 €"}],"label":"Prezzo","uri":"/price"}'), NUM) == 5000
    # formato "/price":{...}
    pag = '<script>self.__next_f.push([1,"%s"])</script>' % (
        '{"ad":{"urn":"id:ad:%s:list:1","subject":"Fiat Panda","features":{"/mileage":{"values":[{"key":"90000"}]},'
        '"/price":{"label":"Prezzo","values":[{"value":"5.000 €","key":"5000"}]}}}}' % NUM).replace('"', '\\"')
    assert f(pag, NUM) == 5000
    # barre escapate (\/price)
    assert f(_app('{"uri":"\\/price","values":[{"key":"5000"}]}'), NUM) == 5000
    # chiavi del chilometraggio prima del prezzo non si confondono
    assert f(_app('{"uri":"/mileage","values":[{"key":"90000"}]}', '{"uri":"/price","values":[{"key":"5000"}]}'), NUM) == 5000
    # /price senza chiave leggibile: None
    assert f(_app('{"uri":"/price","values":[{"value":"5.000 €"}]}'), NUM) is None


def test_prezzo_subito_pagina_altra_auto_nella_finestra():
    f = tr.prezzo_subito_pagina
    altra = ',{"ad":{"urn":"id:ad:699999999:list:2","subject":"Opel Corsa","features":[{"uri":"/price","values":[{"key":"2500"}]}]}}'
    # l'auto dell'annuncio senza prezzo, subito dopo un'altra auto col suo prezzo: NON si prende quello dell'altra
    assert f(_app('{"uri":"/mileage","values":[{"key":"90000"}]}', dopo=altra), NUM) is None
    # col prezzo dell'annuncio: vale il suo, anche con l'altra auto nella finestra (prima o dopo)
    assert f(_app('{"uri":"/price","values":[{"key":"5000"}]}', dopo=altra), NUM) == 5000
    prima = '{"ad":{"urn":"id:ad:699999999:list:2","features":[{"uri":"/price","values":[{"key":"2500"}]}]}},'
    assert f(_app('{"uri":"/price","values":[{"key":"5000"}]}', prima=prima), NUM) == 5000
    # due volte l'urn dell'annuncio con due prezzi diversi: non si sceglie
    doppia = _app('{"uri":"/price","values":[{"key":"5000"}]}') + _app('{"uri":"/price","values":[{"key":"5900"}]}')
    assert f(doppia, NUM) is None
    assert f(_app('{"uri":"/price","values":[{"key":"5000"}]}') * 2, NUM) == 5000


def test_prezzo_subito_pagina_next_data_con_l_urn_giusto():
    import json as _j

    def nd(urn, prezzo="5000", come_dict=False):
        fe = {"uri": "/price", "values": [{"key": prezzo, "value": "x"}]}
        item = {"urn": urn, "subject": "Fiat Panda", "features": ({"/price": fe} if come_dict else [fe])}
        return ('<script id="__NEXT_DATA__" type="application/json">'
                + _j.dumps({"props": {"pageProps": {"ad": item}}}) + "</script>")
    f = tr.prezzo_subito_pagina
    assert f(nd("id:ad:%s:list:1" % NUM), NUM) == 5000
    assert f(nd("id:ad:%s:list:1" % NUM, come_dict=True), NUM) == 5000
    # item di un'altra auto (urn diverso): il suo prezzo non vale
    assert f(nd("id:ad:699999999:list:1"), NUM) is None
    assert f(nd("id:ad:%s:list:1" % NUM), None) is None


def test_prezzo_letto_oltre_il_40_per_cento_e_illeggibile():
    f = tr.prezzo_subito_pagina
    pag = _app('{"uri":"/price","values":[{"key":"5000"}]}')
    assert f(pag, NUM, salvato=4000) == 5000                 # +25%: creduto
    assert f(pag, NUM, salvato=3000) is None                 # +67%: lettura non creduta
    assert f(pag, NUM, salvato=9000) is None                 # -44%
    r = {"id": 1, "source": "subito", "url": URL_SB, "price_eur": 9000}
    esito = _senza_pausa(lambda: tr._controlla_annuncio(r, None, _Cl(_R(200, pag)), None, lenient=False))
    assert esito[1:] == ("attivo", "prezzo_illeggibile")     # giro_rinfresca: errore, price_eur NON scritto
    # anche per AutoScout (stesso confronto)
    assert tr._confronta_prezzo({"price_eur": 10000}, 5000) == "prezzo_illeggibile"
    assert tr._confronta_prezzo({"price_eur": 10000}, 7000) == "prezzo_cambiato:7000"


def test_autoscout_scritta_solo_dentro_next_data_non_blocca():
    import json as _j
    det = {"tracking": {"price": "5000"}, "seller": {}, "testi": {"x": "Questo veicolo non è più disponibile"}}
    pagina = ('<script id="__NEXT_DATA__" type="application/json">'
              + _j.dumps({"props": {"pageProps": {"listingDetails": det}}}, ensure_ascii=False) + '</script>')
    r = {"id": 2, "source": "autoscout24", "url": URL_AS, "price_eur": 5000}
    ctrl = lambda testo: _senza_pausa(
        lambda: tr._controlla_annuncio(r, _Cl(_R(200, testo, url=URL_AS)), None, None, lenient=False))
    assert ctrl(pagina)[1:] == ("attivo", None)
    assert ctrl(pagina + "<p>Il veicolo non è più disponibile</p>")[1:] == ("errore", "scritta_rimozione")


def test_motivo_blocco_solo_per_403_429_ed_eccezioni():
    class Boom:
        def get(self, url):
            raise RuntimeError("rete")
    r = {"id": 1, "source": "subito", "url": URL_SB, "price_eur": 5000}
    c = lambda cl: _senza_pausa(lambda: tr._controlla_annuncio(r, None, cl, None, lenient=False))
    assert c(_Cl(_R(403, "")))[1:] == ("errore", "blocco:403")
    assert c(_Cl(_R(429, "")))[1:] == ("errore", "blocco:429")
    assert c(Boom())[1:] == ("errore", "blocco:eccezione")
    assert c(_Cl(_R(500, "")))[1:] == ("errore", None)
    assert c(_Cl(_R(200, "<html>pagina strana</html>")))[1:] == ("errore", None)
    ra = dict(r, source="autoscout24", url=URL_AS)
    ca = lambda cl: _senza_pausa(lambda: tr._controlla_annuncio(ra, cl, None, None, lenient=False))
    assert ca(_Cl(_R(429, "", url=URL_AS)))[1:] == ("errore", "blocco:429")
    assert ca(Boom())[1:] == ("errore", "blocco:eccezione")
    assert ca(_Cl(_R(200, "<html></html>", url=URL_AS)))[1:] == ("errore", None)


def test_giro_rinfresca_prezzo_cambiato_esce_subito_dall_elenco():
    righe = [_riga(1, 60), _riga(2, 40)]
    st, segnati, _ = _giro(righe, {1: ("attivo", "prezzo_cambiato:5500"), 2: ("attivo", None)}, sul_sito={1})
    assert st["_prezzo_cambiato"] == [1] and [s[0] for s in segnati] == [2]


def _tel_cand(riga_extra, pagina_testo=None, esito=None):
    """telefoni_candidate su un'auto AutoScout finta; ritorna (stats, segnati, sql eseguite)."""
    from dealfinder.pipeline import verify as V
    segnati = []
    riga = dict({"id": 7, "source": "autoscout24", "source_id": "x", "url": URL_AS, "make": "fiat",
                 "model": "panda", "price_eur": 5000, "province": "MI", "prezzo_dopo_valutazione": False}, **riga_extra)

    class Conn(_Conn):
        def execute(self, sql, params=None):
            self.sql.append((sql, params))

            class R:
                def fetchall(s):
                    return [riga] if "FROM listings l" in sql else []

                def fetchone(s):
                    return None
            return R()
    conn = Conn([])
    v = (V._mark, tr._as_esito, tr._client_as, os.environ.get("TR_PAUSA_S"))
    try:
        os.environ["TR_PAUSA_S"] = "0"
        V._mark = lambda conn, lid, st, price=None, motivo=None, sito=False: segnati.append((lid, st))
        if esito is not None:
            tr._as_esito = lambda c, u: esito
            tr._client_as = lambda: object()
        else:
            tr._client_as = lambda: _Cl(_R(200, pagina_testo, url=URL_AS))
        st = tr.telefoni_candidate(conn, [7])
    finally:
        V._mark, tr._as_esito, tr._client_as = v[:3]
        if v[3] is None:
            os.environ.pop("TR_PAUSA_S", None)
        else:
            os.environ["TR_PAUSA_S"] = v[3]
    return st, segnati, conn.sql


def test_telefoni_candidate_stesso_controllo_del_rinfresco():
    import json as _j
    det = {"tracking": {"price": "5000"}, "seller": {}}
    pagina = ('<script id="__NEXT_DATA__" type="application/json">'
              + _j.dumps({"props": {"pageProps": {"listingDetails": det}}}) + '</script>')
    st, segnati, _ = _tel_cand({}, pagina)
    assert segnati == [(7, "attivo")] and st["autoscout24:rinfrescata"] == 1
    # listingDetails ma con la scritta di rimozione nella pagina: niente rinfresco, niente numero
    st, segnati, sql = _tel_cand({}, pagina + "<div>Questo veicolo non è più disponibile</div>")
    assert segnati == [] and st["autoscout24:scritta_rimozione"] == 1
    assert not any("telefono_ok=" in q for q, _p in sql)
    # prezzo cambiato dopo l'ultima valutazione: niente rinfresco (M/R del prezzo vecchio)
    st, segnati, _ = _tel_cand({"prezzo_dopo_valutazione": True}, pagina)
    assert segnati == [] and st["autoscout24:non_rinfrescata_da_rivalutare"] == 1
    # la query porta la colonna con lo stesso controllo di RINFRESCO_SQL
    assert any(tr.PREZZO_DOPO_VALUTAZIONE_RINFRESCO_SQL in q for q, _p in sql)


def test_telefoni_candidate_prezzo_diverso_va_in_rivalutazione():
    st, segnati, sql = _tel_cand({}, esito=("attivo", {"seller": {}, "tracking": {"price": "5900"}}, None))
    assert segnati == [] and st["autoscout24:prezzo_cambiato"] == 1
    assert ("UPDATE listings SET price_eur=%s, stage='nuovo' WHERE id=%s", (5900, 7)) in sql
    assert ("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,'prezzo',%s)", (7, 5900)) in sql
    # oltre il 40%: lettura non creduta, nessun prezzo scritto
    st, segnati, sql = _tel_cand({}, esito=("attivo", {"seller": {}, "tracking": {"price": "1500"}}, None))
    assert segnati == [] and st["autoscout24:non_rinfrescata_prezzo"] == 1
    assert not any("stage='nuovo'" in q for q, _p in sql)
