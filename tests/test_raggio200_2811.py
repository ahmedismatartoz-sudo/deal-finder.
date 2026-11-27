"""28/11: raggio 200 km, decisione del cliente (27/11 23:59: "Da AutoScout ti autorizzo a mettere un raggio di 200 km e
leggete tutto e fate entrare tutto, sempre con posizione attiva. Ho bisogno di più macchine.").
Elenco delle province entro 200 km calcolato, salvataggio, sito, centri AutoScout24, lavoro una tantum, regole invariate."""
import inspect
import os

ATTESE = ["AL", "AO", "AT", "BG", "BI", "BS", "CN", "CO", "CR", "GE", "IM", "LC", "LO", "MB", "MI", "MN", "MO", "MS",
          "NO", "PC", "PR", "PV", "RE", "SO", "SP", "SV", "TN", "TO", "VA", "VB", "VC", "VI", "VR"]
# coordinate dei CAP dei centri AutoScout24 (stessi valori arrotondati di geo)
CAP = {"20121": (45.46, 9.19), "18100": (43.89, 8.03), "38122": (46.07, 11.12)}


# ------------------------------------------------------------------ elenco calcolato

def test_elenco_200km_calcolato_con_haversine():
    from dealfinder import geo, zone
    assert zone.PROVINCE_200KM == ATTESE and len(zone.PROVINCE_200KM) == 33
    for p in zone.PROVINCE_200KM:
        assert geo.km(zone.CENTRO_MILANO, geo.PROVINCE[p]) <= 200, p
    for p in ("GE", "TO", "VR", "PR", "SV", "MO", "TN", "VI", "AO", "IM", "SP", "MS", "CN"):
        assert p in zone.PROVINCE_200KM, p
    # fuori: Firenze ~249 km, Bologna 200,6, Bolzano 203, Padova 210, Roma sempre
    assert 240 < geo.km(zone.CENTRO_MILANO, geo.PROVINCE["FI"]) < 260
    for p in ("FI", "BO", "BZ", "PD", "LU", "RM", "VE"):
        assert p not in zone.PROVINCE_200KM, p
    assert not set(zone.PROVINCE_200KM) & set(zone.ROMA)
    # solo Italia: nessuna sigla svizzera (Ticino, Grigioni, Vallese)
    assert not {"TI", "GR", "VS"} & set(zone.PROVINCE_200KM)


# ------------------------------------------------------------------ salvataggio e valutazione

def test_salva_tiene_le_province_200km():
    from tests.test_raccolta import _l, _salva_finto
    ls = [_l(source="autoscout24", source_id="v1", make="fiat", model="panda", price_eur=4000, province="VR", city="Verona"),
          _l(source="autoscout24", source_id="g1", make="fiat", model="panda", price_eur=4000, province="GE", city="Genova"),
          _l(source="subito", source_id="t1", make="fiat", model="panda", price_eur=4000, province="TN", city="Trento"),
          _l(source="autoscout24", source_id="p1", make="fiat", model="panda", price_eur=4000, province="PD", city="Padova"),
          _l(source="subito", source_id="r1", make="fiat", model="panda", price_eur=4000, province="RM", city="Roma")]
    _, st, salvati = _salva_finto(ls)
    assert [l.source_id for l in salvati] == ["v1", "g1", "t1"]
    assert st["scarto:fuori_zona"] == 2


def test_valutazione_e_filtro_autoscout_coprono_i_200km():
    from dealfinder import zone
    from dealfinder.pipeline import tempo_reale as tr
    from dealfinder.pricing import ricalcolo as R
    assert set(zone.PROVINCE_200KM) <= tr._as_zona() and "PD" not in tr._as_zona() and "RM" not in tr._as_zona()
    assert "PROVINCE_200KM" in inspect.getsource(R._utilitarie)
    assert {"VR", "GE", "CN", "FE", "RA"} <= zone.province_lettura() and not zone.province_lettura() & set(zone.ROMA)


# ------------------------------------------------------------------ sito

def test_sito_ammette_le_province_200km():
    from dealfinder.web import sito_regole as SR
    g = SR.Regole(fascia_fuori_max=0)
    for p in ATTESE:
        assert SR.r_zona({"_r": {"provincia": p}}, g) is None, p
    for p in ("PD", "BZ", "FI", "RM"):
        assert SR.r_zona({"_r": {"provincia": p}}, g) == "provincia_" + p
    assert set(SR.PROVINCE_DIAGNOSI) == set(ATTESE)
    assert not SR.PROVINCE_SITO & SR.ROMA


def test_regole_invariate():
    """Le altre regole non cambiano: zona storica a 16 province per diesel, chat e mercato; tetti dei commercianti."""
    from dealfinder.pricing import valore_base as VB
    from dealfinder.regole import regole
    from dealfinder.web import sito_regole as SR
    assert SR.PROVINCE_100KM == frozenset({"MI", "MB", "VA", "CO", "LC", "BG", "LO", "PV", "CR", "BS", "MN", "SO",
                                           "NO", "PC", "AL", "VB"})
    g = SR.Regole()
    assert g.commercianti_totale == 60
    a = regole().ammissione
    assert a.km_max == 150_000 and a.anno_min == 2007
    # regola diesel e corsia chat: solo la zona storica (Verona no, Milano sì)
    diesel = {"carb": "Diesel", "prezzo": 9000, "pv": {"M": 10000},
              "_r": {"seller_type": "commerciante", "provincia": "VR", "valutazione_nuova": True}}
    assert not SR.diesel_commerciante_zona(diesel, g)
    diesel["_r"]["provincia"] = "MI"
    assert SR.diesel_commerciante_zona(diesel, g)
    assert not SR.chat_candidata({"fonte": "Subito", "_r": {"seller_type": "privato", "provincia": "VR"}}, g)
    # mercato: Verona e Genova si confrontano con la zona di Milano (16 province), che non cambia
    assert VB.zona_di("VR") == "milano" and VB.zona_di("GE") == "milano"
    assert VB.zone()["milano"] == SR.PROVINCE_100KM


# ------------------------------------------------------------------ AutoScout24: centri e lavoro una tantum

def test_centri_autoscout_coprono_il_cerchio_e_le_province():
    from dealfinder import geo, zone
    from dealfinder.pipeline import lettura_completa as LC
    vecchio = os.environ.pop("TR_RAGGIO_AS", None)
    try:
        centri = LC.centri_autoscout()
    finally:
        if vecchio is not None:
            os.environ["TR_RAGGIO_AS"] = vecchio
    assert centri == [("20121", "200"), ("18100", "50"), ("38122", "70")] == list(zone.CENTRI_AS_200KM)

    def coperto(pt):
        return any(geo.km(CAP[c], pt) <= float(r) for c, r in centri)
    # ogni capoluogo, punti vicini al bordo del cerchio e i comuni di confine delle province fuori dal cerchio
    for p in zone.PROVINCE_200KM:
        assert coperto(geo.PROVINCE[p]), p
    bordo = {"Ventimiglia": (43.79, 7.61), "Sanremo": (43.82, 7.78), "Bordighera": (43.78, 7.67),
             "Fiera di Primiero": (46.18, 11.83), "Canazei": (46.48, 11.77), "Bassano del Grappa": (45.77, 11.73),
             "Limone Piemonte": (44.20, 7.58), "Bardonecchia": (45.08, 6.70), "Courmayeur": (45.79, 6.97)}
    for nome, pt in bordo.items():
        assert coperto(pt), nome
    import math
    for gradi in range(0, 360, 15):
        a = math.radians(gradi)
        pt = (45.46 + 195 / 111.2 * math.cos(a), 9.19 + 195 / (111.2 * math.cos(math.radians(45.46))) * math.sin(a))
        if geo.km(zone.CENTRO_MILANO, pt) <= 200:          # punto approssimato: conta solo se è davvero nel cerchio
            assert coperto(pt), gradi


def test_tempo_reale_raggio_200():
    from dealfinder.pipeline import tempo_reale as tr
    vecchio = os.environ.pop("TR_RAGGIO_AS", None)
    try:
        assert tr._raggio_as() == "200" and "zipr=200" in tr._as_url_completo(300, 2000, 1)
    finally:
        if vecchio is not None:
            os.environ["TR_RAGGIO_AS"] = vecchio


def test_lavoro_200km_registrato_e_lucchetto():
    from dealfinder.pipeline import coda_lavori as CL, lettura_completa as LC, tempo_reale as tr
    assert "lettura_200km:" in CL.SOLO_SITO and "lettura_200km:" in CL.SPOSTABILI
    assert 'v.startswith("lettura_200km:")' in inspect.getsource(CL.esegui)
    vero = tr.con_lucchetto
    chiamate = []
    tr.con_lucchetto = lambda nome, fn, *a: chiamate.append(nome)       # sempre occupato
    try:
        es = LC.lavoro_200km("lettura_200km:prova", dormi=lambda s: None)
    finally:
        tr.con_lucchetto = vero
    assert es["job"] == "lettura_200km:prova" and "saltato" in es and not es["finito"]
    assert chiamate == ["lettura"] * LC.LAVORO_200KM_ATTESA_MIN
    assert es["centri"][0] == "20121:" + tr._raggio_as()


def test_lavoro_200km_legge_tutti_i_centri(monkeypatch):
    from dealfinder.pipeline import lettura_completa as LC, passi, tempo_reale as tr
    visti = {}

    def finto_leggi(L, client, fasce=None, centri=None, anno_min=None):
        visti["centri"] = list(centri)
    monkeypatch.setattr(LC, "leggi_autoscout", finto_leggi)
    monkeypatch.setattr(LC, "client_autoscout", lambda tr: None)
    monkeypatch.setattr(passi, "finito", lambda j: visti.setdefault("finito", j))
    monkeypatch.setattr(tr, "con_lucchetto", lambda nome, fn, *a: fn(None))
    es = LC.lavoro_200km("lettura_200km:prova2", dormi=lambda s: None)
    assert es["finito"] and visti["finito"] == "lettura_200km:prova2"
    assert visti["centri"] == LC.centri_autoscout()
