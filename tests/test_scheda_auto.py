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
    for v, atteso in casi.items():
        assert DA.allestimento_testo(v) == atteso, v
    assert DA.allestimento_testo(None, "Fiat Panda 1.2 Lounge") == "Lounge"


def test_gruppi_optional():
    g = DA.raggruppa_optional(["Climatizzatore", "ABS", "Bluetooth", "Cerchi in lega", "Sensore pioggia", "Gancio traino",
                               "Sensori di parcheggio", "Qualcosa di strano"])
    assert g["comfort"] == ["Climatizzatore", "Sensore pioggia"]
    assert g["sicurezza"] == ["ABS", "Sensori di parcheggio"]
    assert g["multimedia"] == ["Bluetooth"] and g["esterni"] == ["Cerchi in lega", "Gancio traino"]
    assert g["altro"] == ["Qualcosa di strano"]


def test_unisci_pagina_sopra_ricerca_e_colonne():
    colonne = {"version_raw": "1.6 TDI Business", "gearbox": "manuale", "power_kw": 85, "title": "VW Golf"}
    dati = {"ricerca": {"colore": "Nero", "porte": 3}, "pagina": {"porte": 5, "cilindrata": 1598}, "letto_il": "2026-11-12T10:00:00+00:00"}
    d = DA.unisci(colonne, json.dumps(dati))
    assert (d["porte"], d["colore"], d["cilindrata"], d["cambio"], d["potenza_cv"]) == (5, "Nero", 1598, "Manuale", 116)
    assert d["allestimento"] == "Business" and d["letto_il"].startswith("2026-11-12")
    assert DA.unisci({}, None) == {}


def test_scheda_per_il_sito_senza_righe_vuote():
    colonne = {"version_raw": "1.6 TDI Business", "gearbox": "Manuale", "power_kw": 85, "year": 2017, "mileage_km": 120000,
               "fuel": "diesel"}
    s = ST.scheda(colonne, {"pagina": DA.da_autoscout_pagina(DETTAGLIO_AS)}, {"anno": 2017, "km": 120000, "carb": "diesel"})
    chiavi = [x["k"] for x in s["chiave"]]
    assert chiavi == ["anno", "km", "carburante", "cambio", "potenza", "allestimento"]
    assert {x["k"]: x["v"] for x in s["chiave"]}["potenza"] == "85 kW · 116 CV"
    assert all(x["v"] for x in s["dati"]) and {x["k"] for x in s["dati"]} >= {"cilindrata", "porte", "colore", "classe_euro"}
    assert s["optional"]["n"] == 7 and [g["id"] for g in s["optional"]["gruppi"]] == ["comfort", "sicurezza", "multimedia", "esterni"]
    assert "333" not in json.dumps(s, ensure_ascii=False)
    # dati scarsi: solo quello che c'è
    poco = ST.scheda({"year": 2010}, None, {"anno": 2010})
    assert [x["k"] for x in poco["chiave"]] == ["anno"] and poco["dati"] == [] and poco["optional"]["n"] == 0


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


def test_sql_da_leggere_parametri():
    assert "%%" in SA.DA_LEGGERE_SQL and "sito_istantanea" in SA.DA_LEGGERE_SQL
    import re
    nomi = set(re.findall(r"%\((\w+)\)s", SA.DA_LEGGERE_SQL))
    assert nomi == {"giorni_cand", "rileggi", "tentativi", "n"}


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
            if "dati_auto" in sql:
                raise RuntimeError("colonna dati_auto mancante")          # migrazione non ancora applicata
            return _Ris([{"id": 1, "version_raw": "1.6 TDI Highline", "title": "VW Golf", "gearbox": "automatico"},
                         {"id": 2, "version_raw": None, "title": "Fiat Panda", "gearbox": None}])

    vecchio = db.connect
    db.connect = lambda: contextlib.nullcontext(C([]))
    try:
        items = [{"id": 1, "nome": "Volkswagen Golf", "cambio": "automatico"}, {"id": 2, "nome": "Fiat Panda"}]
        assert ST.arricchisci_elenco(items) == 1
    finally:
        db.connect = vecchio
    assert items[0]["allestimento"] == "Highline" and items[0]["cambio"] == "Automatico"
    assert "allestimento" not in items[1]
