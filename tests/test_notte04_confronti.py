"""17/11 notte (notte-04): auto in dati_insufficienti perché marca/modello o dati letti male non trovavano i confronti.

Casi reali dai log di produzione (WA30_AUTO / DUMP_CANDIDATI, solo marca, modello, versione, anno, km: niente
venditori): lo stesso modello chiamato in due modi da AutoScout24 e Subito, "Altro modello", km uguali all'anno.
"""
from datetime import datetime, timedelta, timezone

from dealfinder.core import normalize as N
from dealfinder.core.dal_testo import anno, chilometri, completa, km_come_anno
from dealfinder.core.models import Listing


def _canon(make, model):
    mk = N.canon_make(N.slug(make))
    return mk, N.canon_model(mk, N.slug(model))


# (AutoScout24, Subito) come arrivano dalle fonti nei log del 4-15/11
COPPIE_REALI = [
    (("Opel", "Grandland X"), ("Opel", "Grandland")),
    (("Opel", "Crossland X"), ("Opel", "Crossland")),
    (("Opel", "Mokka X"), ("Opel", "Mokka")),
    (("Mazda", "3"), ("Mazda", "Mazda3")),
    (("Mazda", "2"), ("Mazda", "Mazda2")),
    (("Ds Automobiles", "DS 7 Crossback"), ("DS", "DS7")),
    (("Abarth", "500"), ("Abarth", "595")),
    (("Abarth", "595 Turismo"), ("Abarth", "595")),
    (("Fiat", "NEW Panda"), ("Fiat", "Panda")),
    (("Fiat", "Pandina"), ("Fiat", "Panda")),
    (("Fiat", "Grande Punto"), ("Fiat", "Punto")),
    (("Lexus", "Ux200"), ("Lexus", "UX")),
    (("Kia", "Cee'd"), ("Kia", "Ceed")),
    (("Citroën", "C4 Grand Picasso"), ("Citroen", "Grand C4 Picasso")),
    (("Citroën", "C4 SpaceTourer"), ("Citroen", "C4 Picasso")),
    (("Suzuki", "SX4 S Cross"), ("Suzuki", "S-Cross")),
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
    for a, b in diversi:
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
    assert completa(campi, "Fiat Panda 1.2", "Unico proprietario, km 85.000 tagliandati") == {"mileage_km": 85000}
    # nessun km nel testo, o km a loro volta uguali all'anno: resta com'è (l'auto resta fuori, come prima)
    assert completa(campi, "Fiat Panda 1.2", "ottime condizioni") == {}
    assert completa(campi, "Fiat Panda 1.2", "2017 km") == {}
    # due valori diversi nel testo: non si sceglie
    assert completa(campi, "Fiat Panda 1.2", "85.000 km, tagliando a 60.000 km") == {}
    # km giusti: il testo non li cambia mai
    ok = dict(campi, mileage_km=91000)
    assert completa(ok, "Fiat Panda", "km 85.000") == {}


def test_anno_e_km_scritti_in_altro_modo():
    assert anno("immatricolata nel 2015, sempre in garage") == 2015
    assert anno("anno di immatricolazione 2013") == 2013
    assert anno("immatricolazione: marzo 2016") == 2016
    assert anno("immatricolata a giugno del 2014") == 2014
    assert anno("immatricolata nel 2015, tagliando del 2023") is None      # due anni diversi: non si sceglie
    assert chilometri("chilometraggio: 112.000") == 112000


def test_gruppo_unico_porta_i_confronti():
    """Un'auto di Subito "Grandland" con confronti di AutoScout "Grandland X": prima zero confronti (gruppo diverso),
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

    t = riga(1, "subito", "Grandland", 2020, 100000, 13000)
    base = [riga(10 + k, "autoscout24", "Grandland X", 2019 + k % 3, 80000 + 9000 * k, 15000 + 300 * k) for k in range(8)]
    stesso_gruppo = [c for c in base if (c["make"], c["model"], c["fuel"]) == (t["make"], t["model"], t["fuel"])]
    assert len(stesso_gruppo) == 8
    # senza la normalizzazione nuova i confronti erano "grandland-x": zero per la "grandland"
    vecchi = [dict(c, model="grandland-x") for c in base]
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
