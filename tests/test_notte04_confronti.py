"""17/11 notte (notte-04): auto in dati_insufficienti perché marca/modello o dati letti male non trovavano i confronti.

Casi reali dai log di produzione (WA30_AUTO / DUMP_CANDIDATI, solo marca, modello, versione, anno, km: niente
venditori): lo stesso modello chiamato in due modi da AutoScout24 e Subito, "Altro modello", km uguali all'anno.
"""
from datetime import datetime, timedelta, timezone

from dealfinder.core import normalize as N
from dealfinder.core.dal_testo import anno, chilometri, completa, km_come_anno, km_dal_testo_prudente, km_percorsi_chiari
from dealfinder.core.models import Listing


def _canon(make, model):
    mk = N.canon_make(N.slug(make))
    return mk, N.canon_model(mk, N.slug(model))


# (AutoScout24, Subito) come arrivano dalle fonti nei log del 4-15/11
COPPIE_REALI = [
    (("Opel", "Crossland X"), ("Opel", "Crossland")),
    (("Mazda", "3"), ("Mazda", "Mazda3")),
    (("Mazda", "2"), ("Mazda", "Mazda2")),
    (("Ds Automobiles", "DS 7 Crossback"), ("DS", "DS7")),
    (("Ds Automobiles", "DS 7 Crossback"), ("DS", "DS 7")),
    (("Abarth", "595 C"), ("Abarth", "595C")),
    (("Fiat", "NEW Panda"), ("Fiat", "Panda")),
    (("Fiat", "Pandina"), ("Fiat", "Panda")),
    (("Lexus", "Ux200"), ("Lexus", "UX")),
    (("Lexus", "UX 250h"), ("Lexus", "UX")),
    (("Lexus", "CT 200h"), ("Lexus", "CT")),
    (("Citroën", "C4 Grand Picasso"), ("Citroen", "Grand C4 Picasso")),
    (("Citroën", "C4 SpaceTourer"), ("Citroen", "C4 Picasso")),
]

# 17/11 (giudice): generazioni o allestimenti diversi, prima uniti per errore: devono restare divisi (M non gonfiata)
TOLTE_DAL_GIUDICE = [
    (("Opel", "Mokka X"), ("Opel", "Mokka")),
    (("Opel", "Grandland X"), ("Opel", "Grandland")),
    (("DS", "DS 4 Crossback"), ("DS", "DS4")),
    (("Suzuki", "SX4 S Cross"), ("Suzuki", "S-Cross")),
    (("Suzuki", "SX4 S-Cross"), ("Suzuki", "S-Cross")),
    (("Abarth", "500"), ("Abarth", "595")),
    (("Abarth", "595 Turismo"), ("Abarth", "595")),
    (("Abarth", "595 Competizione"), ("Abarth", "595")),
    (("Abarth", "595 Pista"), ("Abarth", "595")),
    (("Abarth", "595 Esseesse"), ("Abarth", "595")),
    (("Abarth", "595 Yamaha"), ("Abarth", "595")),
    (("Abarth", "500C"), ("Abarth", "595C")),
    (("Fiat", "Grande Punto"), ("Fiat", "Punto")),
    (("Fiat", "Punto Evo"), ("Fiat", "Punto")),
    (("Fiat", "Grande Punto"), ("Fiat", "Punto Evo")),
    (("Kia", "Cee'd"), ("Kia", "Ceed")),
    (("Kia", "Pro Cee'd"), ("Kia", "ProCeed")),
    (("Lexus", "NX 300h"), ("Lexus", "NX")),
    (("Lexus", "NX 350h"), ("Lexus", "NX")),
    (("Lexus", "NX 300h"), ("Lexus", "NX 350h")),
    (("Lexus", "RX 450h"), ("Lexus", "RX")),
    (("Lexus", "IS 300h"), ("Lexus", "IS")),
]


def test_stesso_modello_stesso_gruppo_tra_le_fonti():
    for a, b in COPPIE_REALI:
        assert _canon(*a) == _canon(*b), (a, b, _canon(*a), _canon(*b))


def test_modelli_diversi_restano_divisi():
    # prudenza: carrozzerie o generazioni diverse non si uniscono (non devono alzare M)
    diversi = [(("DS", "DS 3"), ("DS", "DS 3 Crossback")), (("Fiat", "500"), ("Fiat", "500C")),
               (("Abarth", "595"), ("Abarth", "595C")), (("Abarth", "595"), ("Abarth", "695")),
               (("Toyota", "Aygo"), ("Toyota", "Aygo X")), (("Opel", "Zafira"), ("Opel", "Zafira Tourer")),
               (("Kia", "Ceed"), ("Kia", "XCeed")), (("Fiat", "Panda"), ("Fiat", "500")),
               (("Toyota", "Yaris"), ("Toyota", "Yaris Cross")), (("Mazda", "3"), ("Mazda", "CX-3"))]
    for a, b in diversi + TOLTE_DAL_GIUDICE:
        assert _canon(*a) != _canon(*b), (a, b)


def test_canon_model_idempotente():
    # il lavoro rinormalizza (NORM_VERSIONE 3) riapplica le regole allo storico: due passate = una
    for a, b in COPPIE_REALI:
        for mk_, md in (a, b):
            mk, m1 = _canon(mk_, md)
            assert N.canon_model(mk, m1) == m1
    assert N.NORM_VERSIONE >= 3


def test_altro_modello_dal_titolo():
    l = Listing(source="subito", source_id="1", url="u", make="Dodge", model="Altro Modello",
                title="Dodge Journey 2.0 CRD SXT 7 posti", year=2012, mileage_km=140000, fuel="Diesel", price_eur=6000)
    N.normalize_fields(l)
    assert (l.make, l.model) == ("dodge", "journey")
    # il titolo non dice il modello (o dice un'altra marca): il nome resta com'era, niente modello inventato
    for tit in ("Vendo auto in ottimo stato", "Chrysler Voyager 2.8"):
        l = Listing(source="subito", source_id="2", url="u", make="Dodge", model="Altro Modello", title=tit,
                    year=2012, mileage_km=140000, fuel="Diesel", price_eur=6000)
        N.normalize_fields(l)
        assert l.model == "altro-modello"
    assert N.modello_dal_titolo("ds-automobiles", "altro", "DS 7 Crossback BlueHDi 130") == "ds7"
    assert N.modello_dal_titolo("fiat", "panda", "Fiat Punto") is None        # modello vero: non si tocca


def test_km_uguali_all_anno_corretti_dal_testo():
    assert km_come_anno(2017, 2017) and km_come_anno("2016", 2017) and not km_come_anno(20170, 2017)
    campi = {"fuel": "benzina", "year": 2017, "mileage_km": 2017, "gearbox": "manuale", "power_kw": 51}
    assert completa(campi, "Fiat Panda 1.2", "Unico proprietario, km 85.000, tagliandata") == {"mileage_km": 85000}
    assert completa(campi, "Fiat Panda 1.2", "85.000 km percorsi") == {"mileage_km": 85000}
    assert completa(campi, "Fiat Panda 1.2", "chilometraggio: 112.000") == {"mileage_km": 112000}
    # nessun km nel testo, o km a loro volta uguali all'anno: resta com'è (l'auto resta fuori, come prima)
    assert completa(campi, "Fiat Panda 1.2", "ottime condizioni") == {}
    assert completa(campi, "Fiat Panda 1.2", "2017 km") == {}
    # due valori diversi nel testo: non si sceglie
    assert completa(campi, "Fiat Panda 1.2", "km 85.000, km percorsi 60.000") == {}
    # km giusti: il testo non li cambia mai
    ok = dict(campi, mileage_km=91000)
    assert completa(ok, "Fiat Panda", "km 85.000") == {}


def test_km_dal_testo_non_sono_lavori_intervalli_o_garanzie():
    """17/11 (giudice): numeri seguiti da "km" che NON sono la percorrenza: mai al posto dei km (km più bassi = M gonfiata)."""
    campi = {"fuel": "benzina", "year": 2017, "mileage_km": 2017, "gearbox": "manuale", "power_kw": 51}
    falsi = ["tagliando ogni 15000 km", "tagliandi ogni 15.000 km", "distribuzione rifatta a 90.000 km",
             "garanzia 10.000 km", "garanzia di 10.000 km", "tagliando a km 90.000", "revisione a km 80.000",
             "cambio frizione km 70000", "cinghia km 60000", "olio km 80000", "gomme con 5.000 km",
             "percorrenza annua 10.000 km", "fa circa 10.000 km all'anno", "85.000 km",
             "distribuzione fatta a km 120.000", "a 90.000 km fatto il tagliando", "dopo km 60.000"]
    for t in falsi:
        assert km_percorsi_chiari(t) is None, t
        assert "mileage_km" not in completa(campi, "Fiat Panda", t), t
    # la percorrenza vera accanto a un lavoro: vale quella vera
    assert km_percorsi_chiari("km 85.000, distribuzione rifatta a 60.000 km") == 85000
    assert km_percorsi_chiari("85.000 km percorsi; tagliando ogni 15.000 km") == 85000
    # prudenza: mai più bassi dei km dichiarati, salvo dichiarati palesemente uguali all'anno
    assert km_dal_testo_prudente(2017, 2017, 85000) == 85000
    assert km_dal_testo_prudente(91000, 2017, 85000) is None
    assert km_dal_testo_prudente(80000, 2017, 85000) == 85000
    assert km_dal_testo_prudente(2017, 2017, 2018) is None
    assert km_dal_testo_prudente(2017, 2017, None) is None


def test_bandiera_km_anno_spenta(monkeypatch):
    from dealfinder.core import dal_testo as DT
    monkeypatch.setattr(DT, "KM_ANNO_DAL_TESTO", False)
    campi = {"fuel": "benzina", "year": 2017, "mileage_km": 2017, "gearbox": "manuale", "power_kw": 51}
    assert DT.completa(campi, "Fiat Panda", "km 85.000") == {}


def test_anno_non_dalle_date_dei_lavori():
    assert anno("tagliando del 2023") is None
    assert anno("revisione del 2024") is None
    assert anno("gomme del 2022") is None
    assert anno("distribuzione fatta 03/2021") is None
    assert anno("Golf del 2017, revisione del 2024") == 2017
    assert anno("auto del 2015") == 2015


def test_rinormalizza_altro_modello_dal_titolo():
    from dealfinder.pipeline.collect import rinormalizza_righe
    r = {"id": 7, "source": "subito", "make": "dodge", "model": "altro-modello", "fuel": "diesel", "gearbox": "manuale",
         "title": "Dodge Journey 2.0 CRD SXT 7 posti", "version_raw": None, "price_flags": []}
    assert rinormalizza_righe([r]) == [{"id": 7, "model": "journey"}]
    assert rinormalizza_righe([dict(r, title="Vendo auto")]) == []


def test_pg_km_buoni_non_persi_se_il_portale_rimette_l_anno():
    import os
    import sys
    from dealfinder import db
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pg_scheda
    c = pg_scheda.nuovo_db("notte04_km")
    if c is None:
        return                                       # Postgres non disponibile qui
    pg_scheda.migra_tutte(c)
    lid = c.execute("INSERT INTO listings (source, source_id, url, status, year, mileage_km) "
                    "VALUES ('subito', 'k1', 'u', 'attivo', 2017, 85000) RETURNING id").fetchone()["id"]

    def passa(km, anno_=2017):
        c.execute("UPDATE listings SET mileage_km = " + db.KM_TENERE_SQL + " WHERE id = %(id)s",
                  {"mileage_km": km, "year": anno_, "id": lid})
        return c.execute("SELECT mileage_km FROM listings WHERE id = %s", (lid,)).fetchone()["mileage_km"]
    assert passa(2017) == 85000                      # il portale rimette l'anno: si tengono i km buoni
    assert passa(None) == 85000                      # senza dato: come prima
    assert passa(92000) == 92000                     # km veri nuovi: si aggiornano come prima
    c.execute("UPDATE listings SET mileage_km = 2017 WHERE id = %s", (lid,))
    assert passa(2016) == 2016                       # entrambi sbagliati: come prima


def test_anno_e_km_scritti_in_altro_modo():
    assert anno("immatricolata nel 2015, sempre in garage") == 2015
    assert anno("anno di immatricolazione 2013") == 2013
    assert anno("immatricolazione: marzo 2016") == 2016
    assert anno("immatricolata a giugno del 2014") == 2014
    assert anno("immatricolata nel 2015, tagliando del 2023") == 2015      # la data del tagliando non è l'anno
    assert anno("immatricolata nel 2015, prima immatricolazione 2016") is None   # due anni diversi: non si sceglie
    assert chilometri("chilometraggio: 112.000") == 112000


def test_gruppo_unico_porta_i_confronti():
    """Un'auto di Subito "Crossland" con confronti di AutoScout "Crossland X": prima zero confronti (gruppo diverso),
    ora gli stessi confronti di una ricerca per marca/modello/alimentazione esatti (come MARKET_SQL)."""
    from dealfinder.pricing import policy_v2 as P
    ora = datetime.now(timezone.utc)

    def riga(i, src, model, year, km, prezzo):
        l = Listing(source=src, source_id=str(i), url="u", make="Opel", model=model, version_raw="1.5 Ecotec Business",
                    year=year, mileage_km=km, fuel="Diesel", gearbox="Manuale", power_kw=96, price_eur=prezzo)
        N.normalize_fields(l)
        return {"id": i, "source": src, "make": l.make, "model": l.model, "version_raw": l.version_raw, "year": l.year,
                "mileage_km": l.mileage_km, "fuel": l.fuel, "gearbox": l.gearbox, "power_kw": l.power_kw,
                "price_eur": l.price_eur, "price_flags": [], "seller_type": "privato", "damage_class": "nessuno",
                "status": "attivo", "last_checked_at": ora - timedelta(hours=5), "last_seen_at": ora - timedelta(hours=5)}

    t = riga(1, "subito", "Crossland", 2020, 100000, 13000)
    base = [riga(10 + k, "autoscout24", "Crossland X", 2019 + k % 3, 80000 + 9000 * k, 15000 + 300 * k) for k in range(8)]
    stesso_gruppo = [c for c in base if (c["make"], c["model"], c["fuel"]) == (t["make"], t["model"], t["fuel"])]
    assert len(stesso_gruppo) == 8
    # senza la normalizzazione nuova i confronti erano "crossland-x": zero per la "crossland"
    vecchi = [dict(c, model="crossland-x") for c in base]
    assert not [c for c in vecchi if c["model"] == t["model"]]
    v = P.valuta(t, stesso_gruppo, 2500)
    assert (v.get("n") or 0) >= 2
    assert v.get("M") is None or v["M"] <= max(c["price_eur"] for c in base)


def test_diagnosi_riassunto():
    from dealfinder.pricing.recupero import riassumi_diagnosi, DA_RECUPERARE_SQL
    righe = [
        {"make": "opel", "model": "grandland", "fuel": "diesel", "year": 2020, "mileage_km": 100000, "gearbox": None,
         "power_kw": 96, "motivo": "solo 1 confronti validi (servono almeno 3)", "n": "1",
         "non_eq": {"anno oltre ±3": 4, "km oltre ±50.000": 2}, "cascata": True, "gruppo": 3},
        {"make": "dodge", "model": "altro-modello", "fuel": "benzina", "year": 2018, "mileage_km": 2018,
         "gearbox": "automatico", "power_kw": None, "motivo": "dati dell'auto incompleti: km (scritto l'anno al posto dei km)",
         "n": None, "non_eq": None, "cascata": False, "gruppo": 0},
    ]
    d = riassumi_diagnosi(righe)
    assert d["auto"] == 2 and d["gia_in_cascata"] == 1
    assert d["motivi"]["solo N confronti validi (servono almeno N)"] == 1
    assert d["dati_mancanti"] == {"cambio": 1, "potenza": 1, "km_uguali_anno": 1, "modello_generico": 1}
    assert d["gruppo_attivi"] == {"2-4": 1, "0-1": 1}
    assert d["confronti_esclusi_per"]["anno oltre ±N"] == 4
    assert "dodge/altro-modello/benzina" in d["modelli_rari"]
    # il recupero riprende anche le auto ferme per dati incompleti che ora nell'annuncio ci sono
    assert "dati dell''auto incompleti" in DA_RECUPERARE_SQL and "abs(l.mileage_km - l.year) <= 3" in DA_RECUPERARE_SQL


def test_modello_dal_titolo_non_rifa_le_unioni_vietate():
    """17/11 (giudice): dal titolo non più "la prima parola": il modello di più parole resta intero (stessa
    normalizzazione dei modelli normali), o None se non si capisce dove finisce."""
    casi = {
        ("opel", "Opel Mokka X 1.6 CDTI"): "mokka-x", ("opel", "Opel Grandland X 1.5 diesel"): "grandland-x",
        ("suzuki", "Suzuki SX4 S-Cross 1.6"): "sx4-s-cross", ("fiat", "Fiat Punto Evo 1.3 Multijet"): "punto-evo",
        ("abarth", "Abarth 595 Competizione 1.4 T-Jet"): "595-competizione",
        ("toyota", "Toyota Yaris Cross Hybrid"): "yaris-cross", ("volkswagen", "Volkswagen Golf Plus 1.4"): "golf-plus",
        ("opel", "Opel Zafira Tourer 1.6"): "zafira-tourer", ("toyota", "Toyota Aygo X 1.0"): "aygo-x",
        ("fiat", "Fiat 500 X 1.3"): "500x", ("fiat", "Fiat Grande Punto 1.2"): "grande-punto",
        ("kia", "Kia Pro Cee'd 1.6 CRDi"): "pro-cee-d", ("bmw", "BMW Serie 3 320d"): "serie-3",
        ("mercedes-benz", "Mercedes Classe A 180 d"): "classe-a", ("mazda", "Mazda 3 2.0 Skyactiv-G"): "3",
        ("fiat", "Fiat Nuova Panda 1.2"): "panda", ("fiat", "Fiat Punto"): "punto",
        ("ds-automobiles", "DS 7 Crossback BlueHDi 130"): "ds7",
        # non si capisce dove finisce il modello: None (l'auto resta "altro-modello", come prima)
        ("opel", "Opel Mokka X"): None, ("fiat", "Fiat Grande Panda"): None, ("fiat", "Fiat Punto bellissima"): None,
        ("dodge", "Dodge 2.0 CRD"): None,
    }
    for (mk, tit), atteso in casi.items():
        assert N.modello_dal_titolo(mk, "altro-modello", tit) == atteso, (tit, N.modello_dal_titolo(mk, "altro-modello", tit))
    # mai il nome corto di un modello diverso
    vietati = {"mokka", "grandland", "sx4", "punto", "595", "yaris", "golf", "zafira", "aygo", "500", "grande", "pro"}
    for (mk, tit), atteso in casi.items():
        if atteso != "punto" or tit != "Fiat Punto":
            assert N.modello_dal_titolo(mk, "altro-modello", tit) not in vietati - ({"500"} if mk == "abarth" else set())


def test_km_vuoti_solo_percorrenza_chiara():
    """17/11 (giudice): anche i km mancanti si riempiono solo col filtro prudente (notte-06 non entra)."""
    campi = {"fuel": "benzina", "year": 2017, "mileage_km": None}
    for t in ("tagliando ogni 15.000 km", "distribuzione fatta a 90.000 km", "percorre 1.000 km al mese",
              "garanzia 10.000 km"):
        assert "mileage_km" not in completa(campi, "Fiat Panda", t), t
    assert completa(campi, "Fiat Panda", "km 85.000")["mileage_km"] == 85000
    assert km_percorsi_chiari("km 1.000 al mese") is None and km_percorsi_chiari("km 1.500 mensili") is None
    # minimo plausibile al posto dei km uguali all'anno
    assert km_dal_testo_prudente(2017, 2017, 4000) is None and km_dal_testo_prudente(2017, 2017, 5000) == 5000


def test_recupero_non_riprende_km_della_vecchia_lettura():
    from dealfinder.pricing.recupero import km_affidabili, DA_RECUPERARE_SQL
    r = {"incompleti": True, "mileage_km": 15000, "title": "Fiat Panda", "descr": "tagliando ogni 15000 km"}
    assert not km_affidabili(r)                                   # km presi da "ogni 15.000 km": non si riprende
    assert km_affidabili(dict(r, mileage_km=85000, descr="km 85.000"))       # percorrenza chiara: sì
    assert km_affidabili(dict(r, mileage_km=85000, descr="ottime condizioni"))  # km della fonte: sì
    assert km_affidabili({"listing_id": 1})                       # altri motivi di recupero: come prima
    assert "km_testo_prudente" in DA_RECUPERARE_SQL
