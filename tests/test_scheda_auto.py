"""12/11: scheda tecnica dell'auto (dealfinder/core/dati_auto.py, pipeline/schede_auto.py, web/scheda_tecnica.py)."""
import json
from collections import Counter

from dealfinder.core import dati_auto as DA
from dealfinder.pipeline import schede_auto as SA
from dealfinder.web import scheda_tecnica as ST

TEL = "333 1234567"

# Pagina dell'annuncio AutoScout24 (listingDetails), campi come nelle pagine vere (nomi in camelCase)
DETTAGLIO_AS = {
    "id": "abc", "description": f"Chiamare {TEL}",
    "seller": {"type": "Private", "phones": [{"callTo": "+393331234567", "formattedNumber": TEL}],
               "contactName": "Mario Rossi"},
    "location": {"zip": "20121", "city": "Milano"},
    "prices": {"public": {"price": "€ 9.500"}},
    "vehicle": {
        "make": "Volkswagen", "model": "Golf", "modelVersionInput": "1.6 TDI 115 CV 5p. Business BlueMotion Technology",
        "bodyType": "Berlina", "bodyColor": "Grigio", "bodyColorOriginal": "Grigio Urano", "paintType": "Metallizzato",
        "numberOfSeats": 5, "numberOfDoors": 5, "transmissionType": "Manuale", "gears": 6,
        "displacementInCCM": "1.598 cm³", "driveTrain": "Anteriore", "rawPowerInKw": 85, "rawPowerInHp": 116,
        "fuelConsumptionCombined": {"formatted": "4,1 l/100 km (comb.)"}, "co2emissionInGramPerKm": {"formatted": "106 g/km"},
        "environmentEuDirective": {"formatted": "Euro 6"}, "numberOfPreviousOwners": 1, "hasFullServiceHistory": True,
        "nonSmoking": True, "firstRegistrationDate": "03-2017",
        "equipment": {
            "comfortAndConvenience": [{"id": 1, "formatted": "Climatizzatore automatico"}, {"id": 2, "formatted": "Cruise control"}],
            "entertainmentAndMedia": [{"id": 3, "formatted": "Bluetooth"}, {"id": 4, "formatted": "Sistema di navigazione"}],
            "safetyAndSecurity": [{"id": 5, "formatted": "ABS"}, {"id": 6, "formatted": "Sensori di parcheggio posteriori"}],
            "extras": [{"id": 7, "formatted": "Cerchi in lega"}, {"id": 8, "formatted": f"Chiamare {TEL}"}],
        },
    },
}


def test_autoscout_pagina_completa_e_senza_venditore():
    d = DA.da_autoscout_pagina(DETTAGLIO_AS)
    assert d["versione"].startswith("1.6 TDI")
    assert (d["cambio"], d["marce"], d["cilindrata"], d["potenza_kw"], d["potenza_cv"]) == ("Manuale", 6, 1598, 85, 116)
    assert (d["carrozzeria"], d["porte"], d["posti"], d["trazione"]) == ("Berlina", 5, 5, "Anteriore")
    assert d["colore"] == "Grigio Urano metallizzato"
    assert (d["proprietari"], d["tagliandi"], d["classe_euro"]) == (1, "Sì, documentati", "Euro 6")
    assert d["consumo"] == "4,1 l/100 km (misto)" and d["co2"] == "106 g/km"
    assert d["immatricolazione"] == "03/2017" and d["non_fumatori"] is True
    assert "Climatizzatore automatico" in d["optional"] and "ABS" in d["optional"]
    testo = json.dumps(d, ensure_ascii=False)
    assert "333" not in testo and "Mario" not in testo and "Chiamare" not in testo     # niente dati del venditore


def test_autoscout_ricerca_vehicle_details():
    it = {"id": "x", "vehicle": {"make": "Fiat", "model": "Panda", "modelVersionInput": "1.2 Lounge", "transmission": "Manuale"},
          "seller": {"phones": [{"callTo": "+393331234567"}]},
          "vehicleDetails": [{"data": "85.000 km", "iconName": "mileage_road"},
                             {"data": "51 kW (69 CV)", "iconName": "speedometer", "ariaLabel": "Potenza kW (CV)"},
                             {"data": "5,2 l/100 km (comb.)", "iconName": "leaf", "ariaLabel": "Consumo di carburante"},
                             {"data": "2 proprietari", "iconName": "person", "ariaLabel": "Proprietari precedenti"}]}
    d = DA.da_autoscout_ricerca(it)
    assert (d["potenza_kw"], d["potenza_cv"], d["proprietari"], d["cambio"]) == (51, 69, 2, "Manuale")
    assert d["consumo"].startswith("5,2")
    r = DA.per_ricerca(d)                                   # nel salvataggio dalla ricerca: niente doppioni delle colonne
    assert "versione" not in r and "cambio" not in r and "potenza_kw" not in r and r["proprietari"] == 2


def test_parse_item_autoscout_porta_dati_auto():
    from dealfinder.collectors.autoscout import parse_item
    it = {"id": "a1", "url": "/annunci/x", "vehicle": {"make": "Fiat", "model": "Panda", "modelVersionInput": "1.2 Easy"},
          "tracking": {"price": "5000", "mileage": "80000", "firstRegistration": "05-2015"},
          "vehicleDetails": [{"data": "5,2 l/100 km", "iconName": "leaf"}]}
    l = parse_item(it)
    assert l.dati_auto.get("consumo", "").startswith("5,2")


SUBITO = {
    "urn": "id:ad:1", "subject": "Fiat Panda", "urls": {"default": "https://www.subito.it/auto/x-1.htm"},
    "features": {
        "/car": {"label": "Auto", "type": "pack", "values": [{"label": "Marca", "value": "Fiat"},
                                                              {"label": "Versione", "value": "1.2 Easy 69cv"}]},
        "/gearbox": {"label": "Cambio", "values": [{"key": "1", "value": "Manuale"}]},
        "/cubic_capacity": {"label": "Cilindrata", "values": [{"value": "1.242 cc"}]},
        "/car_type": {"label": "Tipo di veicolo", "values": [{"value": "Utilitaria"}]},
        "/doors": {"label": "Porte", "values": [{"value": "5"}]},
        "/seats": {"label": "Posti", "values": [{"value": "4"}]},
        "/color": {"label": "Colore", "values": [{"value": "Bianco"}]},
        "/pollution": {"label": "Classe emissioni", "values": [{"value": "Euro 6"}]},
        "/optional": {"label": "Optional", "values": [{"value": "Climatizzatore"}, {"value": "ABS"},
                                                      {"value": "Radio/lettore CD"}, {"value": "Cerchi in lega"},
                                                      {"value": TEL}]},
    },
}


def test_subito_tutte_le_features():
    d = DA.da_subito(SUBITO)
    assert (d["cambio"], d["cilindrata"], d["carrozzeria"], d["porte"], d["posti"]) == ("Manuale", 1242, "Utilitaria", 5, 4)
    assert (d["colore"], d["classe_euro"], d["versione"]) == ("Bianco", "Euro 6", "1.2 Easy 69cv")
    assert d["optional"] == ["Climatizzatore", "ABS", "Radio/lettore CD", "Cerchi in lega"]       # il numero è scartato
    from dealfinder.collectors.subito import parse_item
    assert parse_item(SUBITO).dati_auto["porte"] == 5


def test_allestimento():
    casi = {"1.6 TDI 115 CV 5p. Business BlueMotion Technology": "Business", "2.0 TDI 150CV DSG Highline": "Highline",
            "1.0 EcoBoost 125 CV ST-Line": "ST-Line", "xDrive20d Msport": "Msport", "1.3 MultiJet 95 CV Easy": "Easy",
            "2.0 TDI quattro S tronic": None, "Altro": None}
    # lettura semplice (vale quando il modulo dei margini non c'è)
    for v, atteso in casi.items():
        assert DA._allestimento_semplice(v) == atteso, v
    assert DA._allestimento_semplice(None, "Fiat Panda 1.2 Lounge") == "Lounge"
    # unione 12/11 con margini-precisi: c'è dealfinder.pricing.allestimento, che diventa l'unica fonte (stessi nomi della
    # valutazione); quello che non riconosce resta senza allestimento, niente seconda lettura
    if DA._modulo_allestimento() is not None:
        con_modulo = {"1.6 TDI 115 CV 5p. Business BlueMotion Technology": "Business", "1.0 EcoBoost 125 CV ST-Line": "ST-Line",
                      "xDrive20d Msport": "M Sport", "2.0 TDI quattro S tronic": None, "Altro": None}
        for v, atteso in con_modulo.items():
            assert DA.allestimento_testo(v) == atteso, v
    else:
        for v, atteso in casi.items():
            assert DA.allestimento_testo(v) == atteso, v


def test_unisci_pagina_sopra_ricerca_e_colonne():
    colonne = {"version_raw": "1.6 TDI Business", "gearbox": "manuale", "power_kw": 85, "title": "VW Golf"}
    dati = {"ricerca": {"colore": "Nero", "porte": 3}, "pagina": {"porte": 5, "cilindrata": 1598}, "letto_il": "2026-11-12T10:00:00+00:00"}
    d = DA.unisci(colonne, json.dumps(dati))
    assert (d["porte"], d["colore"], d["cilindrata"], d["cambio"], d["potenza_cv"]) == (5, "Nero", 1598, "Manuale", 116)
    assert d["allestimento"] == "Business" and d["letto_il"].startswith("2026-11-12")
    assert DA.unisci({}, None) == {}


def test_scheda_compatta_per_il_sito():
    colonne = {"version_raw": "1.6 TDI Business", "gearbox": "Manuale", "power_kw": 85, "year": 2017, "mileage_km": 120000,
               "fuel": "diesel"}
    s = ST.scheda(colonne, {"pagina": DA.da_autoscout_pagina(DETTAGLIO_AS)}, {"anno": 2017, "km": 120000, "carb": "diesel"})
    # subito al massimo 6 dati, nell'ordine chiesto dal cliente
    assert [x["k"] for x in s["principali"]] == ["allestimento", "cambio", "cilindrata", "potenza", "carrozzeria", "proprietari"]
    assert {x["k"]: x["v"] for x in s["principali"]}["potenza"] == "116 CV"
    altri = {x["k"]: x["v"] for x in s["altri"]}
    assert {"porte", "posti", "consumo", "tagliandi", "colore", "classe_euro", "trazione", "versione"} <= set(altri)
    assert altri["potenza_kw"] == "85 kW · 116 CV" and altri["classe_euro"] == "6"
    assert all(x["v"] for x in s["principali"] + s["altri"])
    assert s["optional"]["n"] == 7 and "ABS" in s["optional"]["voci"]
    assert s["allestimento"] == "Business" and s["cambio"] == "Manuale"
    assert "333" not in json.dumps(s, ensure_ascii=False)
    # dati scarsi: solo quello che c'è, niente righe vuote
    poco = ST.scheda({"year": 2010, "gearbox": "manuale"}, None, {"anno": 2010})
    assert [x["k"] for x in poco["principali"]] == ["cambio"] and poco["altri"] == [] and poco["optional"]["n"] == 0
    # se mancano i primi, trazione e colore riempiono i posti
    s2 = ST.scheda({}, {"pagina": {"trazione": "Integrale", "colore": "Nero", "classe_euro": "Euro 6d"}})
    assert [x["k"] for x in s2["principali"]] == ["classe_euro", "trazione", "colore"]


# ------------------------------------------------------------------ cron: lettura delle pagine
class _Ris:
    def __init__(self, righe):
        self.righe = righe

    def fetchone(self):
        return self.righe[0] if self.righe else None

    def fetchall(self):
        return self.righe


class _Conn:
    def __init__(self, righe, lette=0):
        self.righe, self.lette, self.sql = righe, lette, []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if "FROM scheda_letture" in sql:
            return _Ris([{"lette": self.lette}] if self.lette else [])
        if "FROM listings l" in sql:
            return _Ris(self.righe[:params["n"]])
        return _Ris([])

    def commit(self):
        pass

    def rollback(self):
        pass


class _Resp:
    def __init__(self, code, text="", url=""):
        self.status_code, self.text, self.url = code, text, url


class _Client:
    def __init__(self, risposte):
        self.risposte, self.chiesti = list(risposte), []

    def get(self, url):
        self.chiesti.append(url)
        return self.risposte.pop(0)


def _pagina_as():
    data = {"props": {"pageProps": {"listingDetails": DETTAGLIO_AS}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_giro_legge_salva_e_rispetta_tetti():
    righe = [{"id": i, "source": "autoscout24", "url": f"https://www.autoscout24.it/annunci/x-{i}", "sul_sito": True}
             for i in range(1, 6)]
    conn = _Conn(righe, lette=597)                       # tetto 600: restano 3 letture oggi
    cl = _Client([_Resp(200, _pagina_as(), "https://www.autoscout24.it/annunci/x-1"), _Resp(404), _Resp(500)])
    st = SA.giro(conn, per_giro=150, per_giorno=600, tempo_s=60, client_as=cl, client_sub=object(), dormi=lambda s: None)
    assert len(cl.chiesti) == 3
    assert st["autoscout24:ok"] == 1 and st["autoscout24:non_disponibile"] == 1 and st["autoscout24:errore"] == 1
    upd = [p for s, p in conn.sql if s.startswith("UPDATE listings SET dati_auto = COALESCE")]
    salvato = json.loads(upd[0][0])
    assert salvato["pagina"]["cilindrata"] == 1598 and salvato["letto_il"] and "333" not in upd[0][0]
    assert any("tentativi_pagina" in s and "jsonb_set" in s for s, _ in conn.sql)       # errore: solo un tentativo in più
    assert Counter(s.split()[0] for s, _ in conn.sql if "scheda_letture (giorno" in s)["INSERT"] == 3


def test_giro_tetto_giornaliero_e_blocchi():
    conn = _Conn([{"id": 1, "source": "subito", "url": "https://www.subito.it/auto/x-123456.htm"}], lette=600)
    assert SA.giro(conn, per_giro=10, per_giorno=600, tempo_s=60, dormi=lambda s: None)["tetto_giornaliero"] == 1
    righe = [{"id": i, "source": "subito", "url": f"https://www.subito.it/auto/x-12345{i}.htm"} for i in range(4)]
    conn = _Conn(righe)
    cl = _Client([_Resp(403), _Resp(429)])
    st = SA.giro(conn, per_giro=10, per_giorno=600, tempo_s=60, client_as=object(), client_sub=cl, dormi=lambda s: None)
    assert st["subito:bloccato"] == 2 and st["subito:saltata_blocco"] == 2 and len(cl.chiesti) == 2
    assert not any(s.startswith("UPDATE") for s, _ in conn.sql)              # un blocco non consuma i tentativi
    assert [p for s, p in conn.sql if "INSERT INTO scheda_fonti" in s] == [("subito",)]   # ferma per 6 ore


def test_sql_da_leggere_parametri():
    assert "%%" in SA.DA_LEGGERE_SQL and "sito_istantanea" in SA.DA_LEGGERE_SQL
    import re
    nomi = set(re.findall(r"%\((\w+)\)s", SA.DA_LEGGERE_SQL))
    assert nomi == {"giorni_cand", "rileggi", "tentativi", "n", "ore_blocco", "subito"}
    assert "ROW_NUMBER() OVER (PARTITION BY c.source" in SA.DA_LEGGERE_SQL


def test_upsert_parte_ricerca():
    from dealfinder import db
    from dealfinder.core.models import Listing
    l = Listing(source="subito", source_id="1", url="u", dati_auto={"porte": 5, "cambio": "Manuale"})
    assert db._dati_auto_ricerca(l) == {"porte": 5}
    assert "'{ricerca}'" in db.DATI_AUTO_SQL and "listings.dati_auto)" in db.DATI_AUTO_SQL


def test_elenco_allestimento_e_cambio():
    import contextlib
    from dealfinder import db

    class C(_Conn):
        def execute(self, sql, params=None):
            assert "dati_auto" not in sql                          # l'elenco legge solo colonne leggere
            return _Ris([{"id": 1, "make": "volkswagen", "model": "golf", "version_raw": "1.6 TDI Highline", "title": "VW Golf",
                          "gearbox": "automatico"},
                         {"id": 2, "make": "fiat", "model": "panda", "version_raw": None, "title": "Fiat Panda", "gearbox": None}])

    vecchio = db.connect
    db.connect = lambda: contextlib.nullcontext(C([]))
    try:
        items = [{"id": 1, "nome": "Volkswagen Golf", "cambio": "automatico", "allestimento": {"livello": 2}},
                 {"id": 2, "nome": "Fiat Panda"}]
        assert ST.arricchisci_elenco(items) == 1
    finally:
        db.connect = vecchio
    assert items[0]["allestimento_testo"] == "Highline" and items[0]["cambio"] == "Automatico"
    assert items[0]["allestimento"] == {"livello": 2}                # la chiave del ramo dei margini non si tocca
    assert "allestimento_testo" not in items[1]


def test_allestimento_una_sola_fonte_col_modulo_dei_margini():
    import sys
    import types
    finto = types.ModuleType("dealfinder.pricing.allestimento")
    visti = []

    def allestimento(auto):
        visti.append(auto)
        return {"classe": "lounge", "nome": "Lounge", "livello": 2, "descrizione": "alto"} if "lounge" in str(auto).lower() else None
    finto.allestimento = allestimento
    vecchio = sys.modules.get("dealfinder.pricing.allestimento")
    sys.modules["dealfinder.pricing.allestimento"] = finto
    import dealfinder.pricing as P
    prima = getattr(P, "allestimento", None)
    P.allestimento = finto
    try:
        assert DA.allestimento_testo("1.2 Lounge", "Fiat Panda", "fiat", "panda") == "Lounge"
        assert visti[0]["make"] == "fiat" and visti[0]["version_raw"] == "1.2 Lounge"
        assert DA.allestimento_testo("1.6 TDI Business", None) is None    # niente seconda fonte: vale il modulo dei margini
    finally:
        if vecchio is None:
            sys.modules.pop("dealfinder.pricing.allestimento", None)
        else:
            sys.modules["dealfinder.pricing.allestimento"] = vecchio
        if prima is None:
            delattr(P, "allestimento")
        else:
            P.allestimento = prima


def test_subito_etichette_difficili():
    item = {"features": {
        "/c1": {"label": "Consumo extraurbano", "values": [{"value": "4,1 l/100km"}]},
        "/c2": {"label": "Consumo urbano", "values": [{"value": "6,9 l/100km"}]},
        "/c3": {"label": "Consumo misto", "values": [{"value": "5,2 l/100km"}]},
        "/cc": {"label": "Cilindrata", "values": [{"value": "1,598"}]},
        "/t": {"label": "Tipo", "values": [{"value": "Usato"}]},
        "/tp": {"label": "Tipologia", "values": [{"value": "Usato"}]},
        "/x": {"label": "Extra", "values": [{"value": "Gancio traino"}]}}}
    d = DA.da_subito(item)
    assert d["consumo"].startswith("5,2") and d["cilindrata"] == 1598
    assert "carrozzeria" not in d and d["optional"] == ["Gancio traino"]
    assert DA.norm_cilindrata("1.598 cm³") == 1598 and DA.norm_cilindrata("1.6") == 1600 and DA.norm_cilindrata("1,6 l") == 1600


def test_autoscout_pagina_solo_veicolo_e_chiavi_elencate():
    det = {"vehicle": {"previousOwnerCount": 2, "gears": float("nan"), "numberOfSeats": float("inf"), "numberOfDoors": 5},
           "features": {"showFinancing": True, "hasVideo": True, "numberOfDoors": 3},
           "ratings": {"numberOfSeats": 7, "bodyType": "Valutazione"},
           "warranty": "12 mesi", "equipment": {"extras": [{"formatted": "Gancio traino"}]}}
    d = DA.da_autoscout_pagina(det)
    assert d["proprietari"] == 2 and d["porte"] == 5            # NaN e infinito saltano solo il loro campo
    assert "posti" not in d and "marce" not in d and "carrozzeria" not in d
    assert d["garanzia"] == "12 mesi" and d["optional"] == ["Gancio traino"]


def test_giro_chiude_i_client_creati():
    from dealfinder.pipeline import tempo_reale as TR

    class Cl(_Client):
        chiuso = False

        def close(self):
            Cl.chiuso = True
    vecchio = TR._client_as
    TR._client_as = lambda: Cl([_Resp(404)])
    try:
        conn = _Conn([{"id": 1, "source": "autoscout24", "url": "https://www.autoscout24.it/annunci/x"}])
        SA.giro(conn, per_giro=5, per_giorno=600, tempo_s=60, dormi=lambda s: None)
    finally:
        TR._client_as = vecchio
    assert Cl.chiuso


# ------------------------------------------------------------------ Postgres vero
def _pg():
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pg_scheda
    c = pg_scheda.nuovo_db("scheda_auto")
    if c is not None:
        pg_scheda.migra_tutte(c)
    return c


def _annuncio(c, sid, fonte, ricerca=None, sul_sito=False, stage="approfondito"):
    r = c.execute("INSERT INTO listings (source, source_id, url, stage, stage_reason, status, last_seen_at, deep_at, dati_auto) "
                  "VALUES (%s, %s, %s, %s, 'opportunita', 'attivo', now(), now(), %s) RETURNING id",
                  (fonte, sid, f"https://www.{fonte}.it/annunci/{sid}-1234567.htm", stage,
                   json.dumps({"ricerca": ricerca}) if ricerca is not None else None)).fetchone()["id"]
    if sul_sito:
        c.execute("INSERT INTO sito_istantanea (listing_id) VALUES (%s)", (r,))
    return r


def test_pg_coda_alterna_le_fonti_e_rispetta_i_blocchi():
    c = _pg()
    if c is None:
        return                                       # Postgres non disponibile qui
    sub = [_annuncio(c, f"s{i}", "subito") for i in range(6)]
    sub_ricerca = _annuncio(c, "sr", "subito", ricerca={"porte": 5}, sul_sito=True)
    as_ = [_annuncio(c, f"a{i}", "autoscout24") for i in range(3)]
    as_sito = _annuncio(c, "asito", "autoscout24", sul_sito=True)
    righe = SA.da_leggere(c, 4)
    assert [r["source"] for r in righe] == ["autoscout24", "subito", "autoscout24", "subito"]
    assert righe[0]["id"] == as_sito                          # dentro la fonte: prima quella sul sito
    assert sub_ricerca not in {r["id"] for r in SA.da_leggere(c, 50)}   # Subito con la ricerca già piena: no
    SA.segna_bloccata(c, "subito")
    assert {r["source"] for r in SA.da_leggere(c, 50)} == {"autoscout24"}
    c.execute("UPDATE scheda_fonti SET bloccato_il = now() - interval '7 hours'")
    assert {r["source"] for r in SA.da_leggere(c, 50)} == {"autoscout24", "subito"}
    # pagina letta: fuori dalla coda; errori: dopo 3 tentativi fuori
    SA.salva(c, as_[0], "ok", {"porte": 5})
    for _ in range(3):
        SA.salva(c, sub[0], "errore", {})
    ids = {r["id"] for r in SA.da_leggere(c, 50)}
    assert as_[0] not in ids and sub[0] not in ids and sub[1] in ids
    SA._conta(c, 1, 0)
    assert SA.lette_oggi(c) == 1


def test_pg_parte_ricerca_si_unisce_e_non_si_riscrive():
    from dealfinder import db
    c = _pg()
    if c is None:
        return
    lid = _annuncio(c, "u1", "autoscout24", ricerca={"optional": ["ABS", "Bluetooth"], "porte": 3})
    c.execute("UPDATE listings SET dati_auto = dati_auto || '{\"pagina\": {\"porte\": 5}}'::jsonb WHERE id = %s", (lid,))

    def aggiorna(nuovo):
        c.execute("UPDATE listings SET dati_auto = " + db.DATI_AUTO_SQL + " WHERE id = %(id)s",
                  {"dati_auto": json.dumps({"ricerca": nuovo}), "id": lid})
        return c.execute("SELECT dati_auto FROM listings WHERE id = %s", (lid,)).fetchone()["dati_auto"]
    d = aggiorna({"porte": 3, "colore": "Nero"})                # una ricerca SENZA optional: gli optional restano
    assert d["ricerca"] == {"optional": ["ABS", "Bluetooth"], "porte": 3, "colore": "Nero"} and d["pagina"] == {"porte": 5}
    assert aggiorna({"porte": 3}) == d                           # niente di nuovo: resta uguale
    d2 = aggiorna({"optional": ["Clima"], "colore": "Blu"})      # optional più corto: resta quello vecchio, più lungo
    assert d2["ricerca"]["optional"] == ["ABS", "Bluetooth"] and d2["ricerca"]["colore"] == "Blu"
    assert aggiorna({"optional": ["ABS", "Bluetooth", "Clima"]})["ricerca"]["optional"] == ["ABS", "Bluetooth", "Clima"]
    c.execute("UPDATE listings SET stage = 'nuovo' WHERE id = %s", (lid,))
    prima = c.execute("SELECT dati_auto FROM listings WHERE id = %s", (lid,)).fetchone()["dati_auto"]
    assert aggiorna({"colore": "Rosso"}) == prima                # annuncio già salvato e non approfondito: mai riscritto


def test_pg_upsert_vero_solo_nel_cron_giusto():
    import contextlib
    from dealfinder import db
    from dealfinder.core.models import Listing
    c = _pg()
    if c is None:
        return
    c.transaction = lambda: contextlib.nullcontext()
    vecchio_schema, vecchio_lettura = dict(db._SCHEMA), db._LETTURA_OK
    db._SCHEMA["colonne"] = None
    db._LETTURA_OK = False                       # database nuovo: la tabella lettura_ai va creata

    def mk(d):
        return Listing(source="subito", source_id="x1", url="https://www.subito.it/a-1234567.htm", title="Fiat Panda",
                       make="fiat", model="panda", year=2015, mileage_km=90000, fuel="benzina", price_eur=5000, dati_auto=d,
                       price_flags=["p"], missing_fields=["gearbox"])
    try:
        db.upsert_listing(c, mk({"porte": 5, "optional": ["ABS"]}))            # cron mercato: niente scheda
        db.upsert_listing(c, mk({"porte": 5, "optional": ["ABS"]}))
        assert c.execute("SELECT dati_auto FROM listings").fetchone()["dati_auto"] is None
        db.dati_auto_ricerca_processo(True)                                      # cron opportunità
        c.execute("DELETE FROM price_events")
        c.execute("DELETE FROM listings")
        db.upsert_listing(c, mk({"porte": 5, "optional": ["ABS"]}))          # nuovo: sì
        db.upsert_listing(c, mk({"colore": "Nero"}))                          # già salvato, non approfondito: no
        assert c.execute("SELECT dati_auto FROM listings").fetchone()["dati_auto"] == {"ricerca": {"porte": 5, "optional": ["ABS"]}}
        c.execute("UPDATE listings SET stage = 'approfondito'")
        db.upsert_listing(c, mk({"colore": "Nero"}))                          # approfondito: sì, unito
        assert c.execute("SELECT dati_auto FROM listings").fetchone()["dati_auto"] == \
            {"ricerca": {"porte": 5, "colore": "Nero", "optional": ["ABS"]}}
    finally:
        db.dati_auto_ricerca_processo(False)
        db._SCHEMA.update(vecchio_schema)
        db._LETTURA_OK = vecchio_lettura


def test_lettura_pagine_parte_anche_con_giro_di_8_ore():
    import time
    from dealfinder.pipeline import collect
    t0 = time.time()
    assert collect.tempo_schede_pagine(t0, t0 + 8 * 3600) == 1200          # giro di 8 ore: parte, 20 minuti
    assert collect.tempo_schede_pagine(t0, t0 + 9.5 * 3600) == 1200
    assert 0 < collect.tempo_schede_pagine(t0, t0 + 9.99 * 3600) < 1200      # vicino al limite: solo il tempo fino a 10,3 ore
    assert collect.tempo_schede_pagine(t0, t0 + 10.5 * 3600) == 0           # oltre 10 ore: rimandata
    d = DA.da_subito({"features": {"/tp": {"label": "Tipologia", "values": [{"value": "SUV/Fuoristrada"}]}}})
    assert d["carrozzeria"] == "SUV/Fuoristrada"
