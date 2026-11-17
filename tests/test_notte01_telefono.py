"""Notte 16→17/11 (notte-01): dato del telefono. Numero nel testo in forme nuove, priorità alle auto che col numero
passerebbero il sito, annunci AutoScout24 spariti nel controllo dei telefoni, conti delle auto tolte. Niente rete né
database (connessioni finte); nei test solo numeri finti."""
from collections import Counter

from dealfinder.pipeline import telefono_nascosto as TN
from dealfinder.pipeline import tempo_reale as tr
from dealfinder.pipeline.telefono_testo import numero_esteso, numero_nel_testo
from dealfinder.web import scovo
from dealfinder.web import sito_regole as SR

from tests.test_piu_auto import _Ris, _azzera_tel, auto, env

G = SR.Regole()


# ------------------------------------------------------------------ 1. numero nel testo
SI = ["cell. 333 - 1234567", "tel 333 / 123 4567", "cell.3331234567", "whatsapp3331234567", "tel: 3331 234567",
      "cell 3O8 1234567", "cell 3 3 3 1 2 3 4 5 6 7", "tel 3.3.3.1.2.3.4.5.6.7", "(333) 1234567", "(+39 333) 123 4567",
      "per info chiamare al 333  1234567", "cell 333 1234567 anno 2015"]
NO = ["BMW 320d 2015 150.000 km", "numero telaio WVWZZZ1KZ6W000001", "km 35000 anno 2012",
      "prezzo 3.500 euro trattabili, 150.000 km", "numero proprietari 1, 3 porte, 120 000 km", "tagliandi 2019 2020 2021",
      "cell 3 porte 2015 130000 km", "tel 333 12", "chiamare 0039 333", "numerosi optional 333 444 5555 6666",
      "motore 3.0 V6 2.993 cc 333 cv", "Tel. 02 1234 5678 9999 ore ufficio", "", None,
      # 17/11 (giudice): prezzi, km e date dopo una parola del telefono → "no" (telefono obbligatorio: in dubbio no)
      "tel 3.500 - 120.000 km", "cell. 3.500-130.000", "tel 3500 - 150000 km", "whatsapp 3.200 - 98.000 km",
      "chiamare 3.000.000.000", "numero 31 - 12 - 2019 - 33", "chiamare 3 / 10 / 2019 / 333",
      "per info tel. o whatsapp. Prezzo 3.500 - 120.000 km", "tel 333 1234 5 67",
      # cifre staccate: solo 10 cifre, prefisso 3[1-9]x, vicino a una parola del telefono
      "codice 3 2 1 4 5 6 7 8 9", "km 3 1 5 0 0 0 0 0 0", "3 3 3 1 2 3 4 5 6 7", "tel 3 0 3 1 2 3 4 5 6 7",
      "tel 3 1 5 0 0 0 0 0 0 0 km"]


def test_numero_esteso_forme_nuove_e_niente_falsi():
    for t in SI:
        assert numero_esteso(t), t
    for t in NO:
        assert not numero_esteso(t), t
    # le forme nuove non le riconosceva il sito (prima del 17/11 queste auto risultavano "senza numero")
    assert not any(scovo.RE_MOBILE.search(t) for t in SI[:11])


# forme di sempre e forme nuove: tutte quelle che contano per telefono_ok
TUTTE = SI + ["Panda 333 1234567", "tel 02 12345678", "cell +39 333 1234567", "chiamare 0039 333 123 4567",
              "telefono 393331234567", "tel 333 123456 7", "WhatsApp: 3 3 3-1 2 3-4 5 6 7 ore pasti",
              "Golf 2015 120.000 km. Cell. 333 - 1234567 oppure (333) 7654321, tel 3.3.3.7.6.5.4.3.2.1"]


def test_hide_phones_nasconde_ogni_forma_che_conta_per_il_telefono():
    """Ogni testo che numero_nel_testo accetta, dopo hide_phones non ha più nessun numero riconoscibile né le cifre
    del numero in chiaro (prima le forme nuove contavano per telefono_ok ma si vedevano sul sito)."""
    import re
    for t in TUTTE:
        assert numero_nel_testo(None, t), t
        h = scovo.hide_phones(t)
        assert "[numero col bottone]" in h and not numero_nel_testo(None, h), (t, h)
        cifre = re.sub(r"\D", "", h)
        for num in ("3331234567", "3081234567", "7654321", "12345678", "3337654321"):
            assert num not in cifre, (t, h)
    # il resto del testo resta
    assert scovo.hide_phones("Golf 2015 120.000 km, cell. 333 - 1234567") == "Golf 2015 120.000 km, cell. [numero col bottone]"
    # sui testi "senza numero nuovo" le forme nuove non nascondono niente in più delle espressioni di sempre
    for t in [x for x in NO if x]:
        vecchio = scovo.RE_LANDLINE.sub(lambda m: m.group(0).replace(m.group(1), "[numero col bottone]"),
                                        scovo.RE_MOBILE.sub("[numero col bottone]", t))
        assert scovo.hide_phones(t) == vecchio, t


def test_find_phone_legge_anche_le_forme_nuove():
    for t in SI:
        assert scovo.find_phone(t) in ("+393331234567", "+393081234567"), t
    assert scovo.find_phone("tel 3500 - 150000 km") is None and scovo.find_phone("codice 3 2 1 4 5 6 7 8 9") is None


def test_ha_numero_testo_usa_anche_le_forme_nuove():
    assert tr.ha_numero_testo("Fiat Panda", "ottima, cell. 333 - 1234567")
    assert tr.ha_numero_testo("Panda 333 1234567", None)                       # forme di sempre
    assert not tr.ha_numero_testo("Panda 1.2", "tagliandata, 120.000 km")
    assert numero_nel_testo(None, "tel 02 12345678")                             # fisso con la parola: come prima


# ------------------------------------------------------------------ 2. prioritarie: passerebbero il sito col numero
def test_prioritari_simulano_le_regole_dopo_il_telefono():
    s = scovo.soglia(6000)
    jev_km = {"km_ok": 0.0, "truffa": 0.0, "decisione": "compra"}
    items = [auto(1, prezzo=6000, margine=s, tel=False),
             auto(2, prezzo=6000, margine=s, tel=False, seller="commerciante"),           # venditore non ammesso
             auto(3, prezzo=6000, margine=s, tel=False, jev=jev_km),                       # Jev: km non credibili
             auto(4, prezzo=6000, margine=s, tel=False, prov="RM"),                        # Roma mai
             auto(5, prezzo=6000, margine=200, tel=False)]                                 # margine sotto il minimo
    n = TN.segna_prioritari(items, [1, 2, 3, 4, 5], {i: "Subito" for i in range(1, 6)}, g=G)
    try:
        assert TN.PRIORITARI == frozenset({1}) and n == 1
        # la simulazione lavora su copie: l'auto vera non cambia (niente tel=True, niente avvisi)
        assert items[0]["_r"]["tel"] is False and "avviso_verifica" not in items[0]
        # da sola la regola dice "senza numero": col numero passerebbe davvero
        out, _ = SR.elenco_sito([dict(items[0], _r=dict(items[0]["_r"], tel=True), pv=dict(items[0]["pv"]))], G,
                                scrivi_log=False)
        assert [it["id"] for it in out] == [1]
    finally:
        TN.PRIORITARI = frozenset()


def test_prioritari_con_regola_rotta_usano_il_criterio_di_prima():
    s = scovo.soglia(6000)
    vecchia = SR.r_jev
    SR.REGOLE[:] = [(n, (lambda it, g: 1 / 0) if n == "jev" else f) for n, f in SR.REGOLE]
    try:
        assert TN.segna_prioritari([auto(1, prezzo=6000, margine=s, tel=False)], [1], {1: "Subito"}, g=G) == 1
    finally:
        SR.REGOLE[:] = [(n, vecchia if n == "jev" else f) for n, f in SR.REGOLE]
        TN.PRIORITARI = frozenset()


# ------------------------------------------------------------------ 3. telefoni_candidate: testo e ordine
class ConnTel:
    """Risponde alle tre letture di telefoni_candidate: conti (TEL_TOLTI), testi, auto da aprire."""
    def __init__(self, testi, righe):
        self.testi, self.righe, self.scritte, self.select = testi, righe, [], []

    def execute(self, sql, p=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT source, status"):
            self.select.append(("conti", p))
            return _Ris([{"source": "subito", "status": "attivo", "telefono_ok": None, "prio": True, "recente": False,
                          "n": 3}])
        if s.startswith("SELECT l.id, l.price_eur, l.title, l.description"):
            self.select.append(("testi", p))
            prezzi = dict(zip(p[0], p[1]))           # come il JOIN: solo mai lette (-1) o col prezzo cambiato
            return _Ris([t for t in self.testi if t["id"] in prezzi and t.get("price_eur", 0) != prezzi[t["id"]]])
        if s.startswith("SELECT"):
            self.select.append(("auto", p))
            return _Ris(self.righe)
        self.scritte.append((s[:70], p))
        return _Ris([])

    def commit(self):
        pass

    def rollback(self):
        pass


def test_telefoni_candidate_numero_nel_testo_e_prioritarie_prima():
    testi = [{"id": 10, "title": "Golf", "description": "cell. 333 - 1234567"},
             {"id": 11, "title": "Polo", "description": "solo messaggi"}]
    tr._TESTO_FATTI.clear()
    tr._TOLTI_DIAG["t"] = 0.0
    TN.PRIORITARI = frozenset({11})
    _azzera_tel()
    try:
        with env(TR_PAUSA_S="0", TEL_SUBITO_API=None, TEL_CAND_SUBITO=None):
            c = ConnTel(testi, [])
            st = tr.telefoni_candidate(c, [10, 11, 12])
            # secondo giro: i testi già letti non si rileggono
            c2 = ConnTel(testi, [])
            tr.telefoni_candidate(c2, [10, 11, 12])
    finally:
        TN.PRIORITARI = frozenset()
        tr._TESTO_FATTI.clear()
    assert st["testo:si"] == 1 and st["testo_letti"] == 2          # la 12 non è più attiva: niente testo
    assert [p for q, p in c.scritte if q.startswith("UPDATE listings SET telefono_ok=true")] == [(10,)]
    assert [k for k, _ in c.select] == ["conti", "testi", "auto"]
    testi_p = dict(c.select)["testi"][0]
    assert testi_p[0] == 11                                  # le prioritarie si leggono per prime
    auto_p = dict(c.select)["auto"]
    # TEL_SUBITO_API spento: niente ricerca Subito, ma l'ordine delle prioritarie vale lo stesso (prima del 17/11 no)
    assert auto_p[2] == [] and auto_p[3] == [11] and auto_p[6] == [11] and auto_p[4] == 6 and auto_p[5] == 12
    # secondo giro: le già lette tornano dal database solo se il prezzo è cambiato
    assert "testi" in dict(c2.select) and not any(q.startswith("UPDATE") for q, _ in c2.scritte)
    testi[1]["price_eur"] = 5000                             # prezzo cambiato: la Polo si rilegge
    testi[1]["description"] = "ora anche tel 333 - 1234567"
    tr._TESTO_FATTI.update({10: 0, 11: 0, 12: -2})
    _azzera_tel()
    try:
        with env(TR_PAUSA_S="0", TEL_SUBITO_API=None, TEL_CAND_SUBITO=None):
            c3 = ConnTel(testi, [])
            st3 = tr.telefoni_candidate(c3, [10, 11, 12])
    finally:
        tr._TESTO_FATTI.clear()
    assert st3["testo:si"] == 1 and st3["testo_letti"] == 1
    assert [p for q, p in c3.scritte if q.startswith("UPDATE listings SET telefono_ok=true")] == [(11,)]


def test_telefoni_candidate_testo_spento():
    tr._TESTO_FATTI.clear()
    with env(TR_PAUSA_S="0", TEL_TESTO="0"):
        c = ConnTel([{"id": 10, "title": "", "description": "cell. 333 - 1234567"}], [])
        st = tr.telefoni_candidate(c, [10])
    assert "testi" not in dict(c.select) and not st.get("testo:si")


# ------------------------------------------------------------------ 4. controlla_telefoni: annunci spariti
def test_controlla_telefoni_segna_spariti():
    from dealfinder.pipeline import verify

    class Conn:
        def __init__(self):
            self.scritte = []

        def execute(self, sql, p=None):
            if sql.lstrip().startswith("SELECT"):
                return _Ris([{"id": 1, "url": "https://as/1", "price_eur": 9000},
                             {"id": 2, "url": "https://as/2", "price_eur": 7000}])
            self.scritte.append((" ".join(sql.split())[:50], p))
            return _Ris([])

        def commit(self):
            pass
    segnati = []
    salva = (tr._as_esito, verify._mark, tr._client_as)
    tr._as_esito = lambda c, url: ("scomparso", None, "404") if url.endswith("1") else (
        "attivo", {"seller": {"type": "PrivateSeller", "phones": [{"callTo": "+390000000000"}]}}, None)
    verify._mark = lambda conn, lid, st, price=None, motivo=None, sito=False: segnati.append((lid, st, price, motivo))
    tr._client_as = lambda: None
    try:
        with env(TR_PAUSA_S="0"):
            c = Conn()
            st = tr.controlla_telefoni(c)
    finally:
        tr._as_esito, verify._mark, tr._client_as = salva
    assert segnati == [(1, "scomparso", 9000, "404")] and st["scomparso"] == 1 and st["telefono_si"] == 1
    assert len(c.scritte) == 1 and c.scritte[0][0].startswith("UPDATE listings SET telefono_ok=%s") \
        and c.scritte[0][1] == (True, 2)


# ------------------------------------------------------------------ 5. giro giornaliero di Subito: anche il testo
def test_giro_telefoni_subito_conta_e_segna_il_testo():
    from dealfinder.collectors import subito as S

    class Col:
        def __init__(self, proxy=None):
            self.client = self

        def get(self, url, headers=None, params=None):
            ads = [] if params["start"] else [
                {"urn": "a1", "advertiser": {"phone": "0"}, "subject": "Panda", "body": ""},
                {"urn": "a2", "advertiser": {}, "subject": "Golf", "body": "cell. 333 - 1234567"},
                {"urn": "a3", "advertiser": {}, "subject": "Polo", "body": "solo messaggi"}]
            return type("R", (), {"status_code": 200, "json": lambda self: {"ads": ads}})()

        def _trova_regione(self, *a):
            return 4

    class Conn:
        def __init__(self):
            self.p = []

        def execute(self, sql, p=None):
            self.p.append(p)
            return type("X", (), {"rowcount": len(p[0])})()

        def commit(self):
            pass
    salva = (S.SubitoCollector, tr.REGIONI)
    S.SubitoCollector, tr.REGIONI = Col, {"lombardia": ["MI"]}
    try:
        with env(TR_TEL_PAUSA_S="0", TR_TEL_PAGINE="2"):
            c = Conn()
            st = tr.giro_telefoni_subito(c)
    finally:
        S.SubitoCollector, tr.REGIONI = salva
    assert c.p == [(["a1", "a2"],)]
    assert st["con_numero"] == 2 and st["senza_numero"] == 1 and st["numero_nel_testo&indicatore=nessuno"] == 1
