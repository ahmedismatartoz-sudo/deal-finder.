"""Notte 16→17/11 (notte-01): dato del telefono. Numero nel testo con le espressioni di sempre (forme nuove tolte), priorità alle auto che col numero
passerebbero il sito, annunci AutoScout24 spariti nel controllo dei telefoni, conti delle auto tolte. Niente rete né
database (connessioni finte); nei test solo numeri finti."""
import re
from collections import Counter

from dealfinder.pipeline import telefono_nascosto as TN
from dealfinder.pipeline import tempo_reale as tr
from dealfinder.web import scovo
from dealfinder.web import sito_regole as SR

from tests.test_piu_auto import _Ris, _azzera_tel, auto, env

G = SR.Regole()


# ------------------------------------------------------------------ 1. numero nel testo: espressioni di sempre
# 17/11: le forme nuove (telefono_testo) sono state TOLTE (falsi "sì" su prezzi/km/date e numeri visibili sul sito):
# ha_numero_testo, hide_phones e find_phone devono dare esattamente lo stesso risultato di c9f2436 (copiato qui).
_REF_SEP = r"[\s.\-/]?"
_REF_MOBILE = re.compile(r"(?<![\w.,])(?:\+|00)?(?:39[\s.\-]?)?(3\d{2}" + _REF_SEP + r"(?:\d{3}" + _REF_SEP + r"\d{3,4}|\d{4}"
                         + _REF_SEP + r"\d{3}|\d{3}" + _REF_SEP + r"\d{2}" + _REF_SEP + r"\d{2}|\d{2}" + _REF_SEP + r"\d{2}"
                         + _REF_SEP + r"\d{2,3}))(?![\d.,]*\d)")
_REF_LANDLINE = re.compile(r"(?i)(?:tel(?:efono|efonare)?|cell(?:ulare)?|chiama(?:re|temi)?|numero|whats\s?app|wa)\W{0,6}"
                           r"((?:\+39[\s.\-]?)?0\d{1,3}[\s.\-/]?\d{5,8})(?!\d)")


def _ref_ha_numero(title, desc):
    t = f"{title or ''} {desc or ''}"
    return bool(_REF_MOBILE.search(t) or _REF_LANDLINE.search(t))


def _ref_hide(text):
    text = _REF_MOBILE.sub("[numero col bottone]", text)
    return _REF_LANDLINE.sub(lambda m: m.group(0).replace(m.group(1), "[numero col bottone]"), text)


# testi del giudice (attacco.py, attacco2.py: numeri finti) e forme nuove tolte
TESTI_GIUDICE = ['Vendo Golf 7 1.6 TDI, 120.000 km, anno 2017, prezzo 9.500 euro. Per info tel. o whatsapp, no perditempo.',
 'Fiat Panda 1.2, 3 porte, 85.000 km. Contattare per appuntamento. Prezzo 3.500 € trattabili.',
 'Tel. o whatsapp: 3.500 euro trattabili, 150.000 km',
 'tel 3500 - 150000 km',
 'Per info tel. o whatsapp, codice annuncio 3345678901',
 'Contattare citando il rif. 3145678902 grazie',
 'numero 31 - 12 - 2019 revisione fatta',
 'Chiamare dal 31.12.2026, consegna immediata',
 'Numero telaio: ZFA31200000123456, targa EX333AB',
 'Numero di telaio WVWZZZ1KZ3W123456. Cilindrata 1598 cc, 77 kW / 105 CV.',
 'Numero proprietari: 1. Chilometri 33.000. Tagliandi certificati.',
 'Numero porte 3 . 120.000 km . 2018',
 'Optional: 1) clima 2) navigatore 3) cerchi 17 4) sensori 5) cruise. Chiamare per info.',
 'Chiamare per: 3) cerchi in lega 4) gomme nuove 5) tagliando fatto',
 'Whatsapp 320 000 km, motore rifatto',
 'tel 330 400 500 euro trattabili',
 'Prezzo trattabile, tel 300 400 500',
 'cell 349 2019 120',
 'Telefonare 3450 1600 20',
 'contattare 3290 - 3390 - 25',
 'P.IVA 03123456789, tel. o whatsapp in orari ufficio',
 'Bonifico su IBAN IT60X0542811101000000123456, contattare per accordi',
 'contattare per IBAN IT32O0306909606100000012345',
 'Via Roma numero 31 - 20121 Milano, tel. ore pasti',
 'numero 312 - 20121 Milano',
 'numero civico 3, CAP 20123 Milano',
 'Tel. 3 mesi di garanzia, 12.000 km, 2021',
 'Telefono: 3 proprietari, 210.000 km',
 'sms 3x per info, 1.6 benzina 2015',
 'chiamare dopo le 19. 330 cv, 2020',
 'tel 335 12 2019',
 'Cell 340 1234 2019',
 'cell 340 1234 56 cv',
 'tel 3/12/2019 tagliando',
 'chiamare: 312.450 km reali',
 'tel. 312.450 / 3.200 euro',
 'Cell. 3 400 euro',
 'tel 3 4 0 0 0 0 0 0 0 0 km',
 'numero 3 2 1 4 5 6 7 8 9',
 'codice 3 2 1 4 5 6 7 8 9 0',
 'Optional numerati: 3 4 5 6 7 8 9 1 2 3',
 'numero optional: 3 4 5 6 7 8 9 1 2 3',
 'tel 3400 o 3500 euro',
 'tel 345 o 346 o 347',
 'Whatsapp 3 3 4 euro',
 'Cilindrata 3.000 cc, 340 cv, tel. o whatsapp 2019 immatricolata',
 'Targa: EX 333 AB, numero chiavi 2, tel solo interessati',
 'Revisione 03/2026, bollo pagato, chiamare 3 volte se non rispondo',
 'Km 31.500, anno 2019, potenza 110 kW (150 CV), numero 5 posti',
 'Contattare 333 euro di accessori inclusi',
 'Prezzo 33.900 €. Tel per info. Anno 2022, km 33.000',
 'tel 333.900 €',
 'Telefono 3.390.000 lire (prezzo storico)',
 'tel. 31.12.2026 scadenza garanzia',
 'Cellulare: 3 anni di garanzia. Prezzo 3.290, km 31.000',
 'whatsapp: 34.500 km, 2020',
 'Numero motore 3ZZ1234567 originale',
 'numero motore 3341234567X',
 'numero di serie 3341234567',
 'chiamatemi per il codice sconto 3312 3456 78',
 "Tel. 0039 per l'estero, prezzo 3.450",
 'Tel 3401 - 2019 - 120 cv',
 'telefono 3 4 5 euro di bollo',
 'contattatemi al numero in descrizione: 3 porte, 1.2, 69 cv',
 'Riferimento interno numero 3215478963',
 'cell 3 1 2 0 0 0 0 0 0 0',
 'tel 349 / 12 / 2019',
 'tel 3333 / 2018 / 12',
 'numero 3 5 0 0 euro',
 'Chiamare: 34.000 km, 3.300 euro',
 'WhatsApp 3200 km 2019 3500 euro',
 'tel 33 12 345 678',
 'tel 333 1500 km',
 'Cell 333 12 34 cv',
 'cell. 333 - 1234567',
 'tel 333 / 123 45 67',
 'whatsapp3331234567',
 'tel: 3331 234567',
 'cell 3O8 1234567',
 'cell 3 3 3 1 2 3 4 5 6 7',
 'tel 3.3.3.1.2.3.4.5.6.7',
 '(333) 1234567',
 '(+39 333) 123 4567',
 '+39 333.123.45.67',
 'Chiamatemi al 333.1234567 o whatsapp',
 'WhatsApp: +39 345 678 9012',
 'tel.: 0039 333 1234567',
 'cell 33312 34567',
 'tel 333 - 123 - 4567',
 'tel 3-4-5-6-7-8-9-0-1-2',
 'numero 3 4 7 1 2 3 4 5 6 7',
 'chiama 347 1234567 oppure 06 1234567',
 'tel 333 - 1234567, ripeto 333 - 1234567',
 'cell 3 3 3 1 2 3 4 5 6 7 ripeto: 3 3 3 1 2 3 4 5 6 7',
 'Golf ok, whatsapp\n333 - 1234567',
 'tel 333 / 1234567 ore pasti, anno 2019',
 'Cell.3O8.12.34.567',
 'tel 3 4 7 - 1 2 3 - 4 5 6 7',
 'tel 333 123456 7',
 'TEL 3401234567 dopo le 18',
 'cell: +393401234567',
 'contattare (340) 123.4567 Marco',
 'sms 340 / 12 34 567',
 'tel. 340-1234567 / 340 - 7654321',
 'telefono 3 4 0. 1 2 3 4 5 6 7',
 'cell 34O 12345 67',
 'chiamare 3401 23 4567',
 'whats app 3 4 0 1 2 3 4 5 6 7',
 'tel 3 4 0 1 2 3 4 5 6 7 oppure 3 4 0 7 6 5 4 3 2 1',
 'Cell 333-12-34-567',
 'cell 333 12 34 567',
 'BMW Serie 3 (318) 150.000 km, 2012',
 'Peugeot 308 (T9) 1.6 HDi, (308) 120 000 km',
 'Ferrari (360) 450 000 euro',
 'Fiat 500 (312) 1.2 Lounge 69 cv',
 'Alfa 147 (937) 1.9 jtd',
 'Mazda (323) 150 2019',
 'tel 3450 1600 20',
 'chiamare 3290 1450 35 cerchi',
 'whatsapp 3120 4500 90',
 'Telefono 3150 2300 12 rate',
 'tel 334 5 2019 1',
 'numero rate 36 da 250, anticipo 3 1 2 3 4 5 6 7 8 9',
 'numero optional 3 5 6 7 8 9 1 2 3 4',
 'Telefonare. Pneumatici: 3 4 5 6 7 8 9 0 1 2 mm',
 'cell. 3450 120 000',
 'tel 3450 120 00',
 'tel 3999 1500 50 euro',
 'contattare 3 4 5 6 - 7 8 9 0 - 1 2',
 'tel 3450.1990.25 trattabili',
 'Chiamare 3150 2019 1',
 'Numero 3500 1200 75',
 'cell 3O 1500 km',
 'tel 3.4.5.6.7.8.9.0.1.2 euro',
 'tel 3 4 5 6 7 8 9 0 1 2 cv',
 'tel 3333 4444 55 kw',
 'cell 349 1234 5 6 7',
 'tel. 345 678 90 €',
 'Whatsapp 31 dicembre, 3200 euro, 120000 km',
 'tel 3 - 2 - 1 - 4 - 5 - 6 - 7 - 8 - 9 - 0',
 'numero 3 2 1 4 5 6 7 8 9 0 telaio',
 'cell 333 / 1234567. Chi chiama al 333 / 1234567 dopo le 20 non rispondo',
 'Vendo. Whatsapp\n\n\n333 - 1234567',
 'whatsapp: (o sms)................. 333 - 1234567']
TESTI = TESTI_GIUDICE + [
    "cell. 333 - 1234567", "tel 333 / 123 4567", "cell.3331234567", "whatsapp3331234567", "tel: 3331 234567",
    "cell 3O8 1234567", "cell 3 3 3 1 2 3 4 5 6 7", "(333) 1234567", "(+39 333) 123 4567", "Panda 333 1234567",
    "tel 02 12345678", "tel 3.500 - 120.000 km", "tel 3500 - 150000 km", "(318) 150.000 km", "numero optional 3 4 5 6 7 8 9 1 2 3"]


def test_numero_e_mascheramento_come_c9f2436():
    import importlib.util
    assert importlib.util.find_spec("dealfinder.pipeline.telefono_testo") is None      # forme nuove tolte
    for t in TESTI:
        assert tr.ha_numero_testo(None, t) == _ref_ha_numero(None, t), t
        assert tr.ha_numero_testo(t, None) == _ref_ha_numero(t, None), t
        assert scovo.hide_phones(t) == _ref_hide(t), t
        m = _REF_MOBILE.search(t)
        assert bool(scovo.find_phone(t)) == bool(m or _REF_LANDLINE.search(t)), t
    assert scovo.RE_MOBILE.pattern == _REF_MOBILE.pattern and scovo.RE_LANDLINE.pattern == _REF_LANDLINE.pattern
    assert not tr.ha_numero_testo("Fiat Panda", "ottima, cell. 333 - 1234567")         # forma nuova: di nuovo "no"
    assert tr.ha_numero_testo("Panda 333 1234567", None)


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
    testi = [{"id": 10, "title": "Golf", "description": "cell. 333 1234567"},
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
    testi[1]["description"] = "ora anche tel 333 1234567"
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
        c = ConnTel([{"id": 10, "title": "", "description": "cell. 333 1234567"}], [])
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
def test_giro_telefoni_subito_come_prima():
    from dealfinder.collectors import subito as S

    class Col:
        def __init__(self, proxy=None):
            self.client = self

        def get(self, url, headers=None, params=None):
            ads = [] if params["start"] else [
                {"urn": "a1", "advertiser": {"phone": "0"}, "subject": "Panda", "body": ""},
                {"urn": "a2", "advertiser": {}, "subject": "Golf", "body": "cell. 333 1234567"},
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
    # come c9f2436: il "sì" del giro giornaliero è solo l'indicatore di Subito (il testo lo legge il sito)
    assert c.p == [(["a1"],)]
    assert st["con_numero"] == 1 and st["senza_numero"] == 2 and not st.get("numero_nel_testo&indicatore=nessuno")
