"""27/11 sera (decisione del cliente): tutta la Lombardia nelle zone del sito (anche Mantova e Sondrio) e auto
entro 100 km dalla posizione di chi guarda (chiesta una volta sola, salvata arrotondata con il nome del comune)."""
import json
import os
import re
import shutil
import subprocess

QUI = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(QUI, "..", "dealfinder", "web", "static", "scovo", "app.js")
LOMBARDIA = ("MI", "MB", "BG", "BS", "CO", "CR", "LC", "LO", "MN", "PV", "SO", "VA")


# ------------------------------------------------------------------ zone: Lombardia intera

def test_mantova_e_sondrio_ammesse_dalle_regole_zona():
    from dealfinder.web import sito_regole as SR
    g = SR.Regole(fascia_fuori_max=0)
    for p in LOMBARDIA + ("NO", "PC", "AL", "VB"):
        assert p in SR.PROVINCE_100KM
        assert SR.r_zona({"_r": {"provincia": p}}, g) is None, p
    # nient'altro cambia: fuori zona resta fuori, Roma mai
    # 28/11: raggio 200 km, decisione del cliente: Verona (140 km) ora dentro, Padova (210 km) fuori
    assert SR.r_zona({"_r": {"provincia": "VR"}}, g) is None
    assert SR.r_zona({"_r": {"provincia": "PD"}}, g) == "provincia_PD"
    assert SR.r_zona({"_r": {"provincia": "RM"}}, g) == "provincia_RM"
    assert SR.chat_candidata({"fonte": "Subito", "_r": {"seller_type": "privato", "provincia": "MN"}}, g)


def test_lettura_e_valutazione_coprono_mantova_e_sondrio():
    from dealfinder import zone
    from dealfinder.pipeline import tempo_reale
    assert {"MN", "SO"} <= set(zone.MILANO)                  # salvataggio (tempo_reale._salva) e valutazione (_utilitarie)
    assert {"MN", "SO"} <= set(tempo_reale.REGIONI["lombardia"])   # lettura Subito
    src = open(os.path.join(QUI, "..", "dealfinder", "pricing", "ricalcolo.py"), encoding="utf-8").read()
    # 28/11: raggio 200 km, decisione del cliente: anche le province entro 200 km nella valutazione
    assert "zona_val = sorted((set(MILANO) | set(RAVENNA) | set(PROVINCE_200KM)) - set(ROMA))" in src


def test_regole_non_toccate():
    from dealfinder.regole import regole
    from dealfinder.web import sito_regole as SR
    assert "RM" in SR.ROMA and not (SR.PROVINCE_100KM & SR.ROMA)
    a = regole().ammissione
    assert a.km_max == 150_000 and a.anno_min == 2007


# ------------------------------------------------------------------ distanze e coordinate delle auto

def test_haversine_python():
    from dealfinder import geo
    assert geo.km((45.46, 9.19), (45.46, 9.19)) == 0
    assert 40 < geo.km(geo.PROVINCE["MI"], geo.PROVINCE["BG"]) < 46       # Milano-Bergamo circa 43 km
    assert 120 < geo.km(geo.PROVINCE["MI"], geo.PROVINCE["MN"]) < 135     # Milano-Mantova circa 125 km


def test_coordinate_auto_annuncio_poi_comune_poi_provincia():
    from dealfinder import geo
    from dealfinder.web import scovo
    assert geo.coords_auto({"lat": 45.6012, "lon": 8.9188, "province": "MI"}) == ((45.6, 8.92), "annuncio")
    assert geo.coords_auto({"city": "Legnano", "province": "MI"}) == ((45.60, 8.92), "comune")
    assert geo.coords_auto({"city": "Borgo Piccolo", "province": "SO"}) == (geo.PROVINCE["SO"], "provincia")
    assert geo.coords_auto({"city": "Legnano", "province": "TO"})[1] == "provincia"    # comune di un'altra provincia
    assert geo.coords_auto({"lat": 0, "lon": 0, "province": "BG"})[1] == "provincia"   # coordinate fuori Italia: no
    assert scovo._geo({"city": "Suzzara", "province": "MN"}) == [44.99, 10.75]
    assert scovo._geo({}) is None


def test_tabella_comuni_coerente_con_i_km_da_milano():
    """Le coordinate scritte a mano dei comuni tornano con data/comuni_60km.json (km da Milano) entro 3 km."""
    from dealfinder import geo
    d = json.load(open(os.path.join(QUI, "..", "data", "comuni_60km.json"), encoding="utf-8"))
    n = 0
    for nome, (pv, la, lo) in geo.COMUNI.items():
        assert pv in geo.PROVINCE and geo.km((la, lo), geo.PROVINCE[pv]) < 75, nome
        k = geo.chiave(nome)
        if k in d:
            n += 1
            assert abs(geo.km((45.4642, 9.19), (la, lo)) - d[k]["km"]) < 3, nome
    assert n >= 40


def test_collector_coordinate_dalla_fonte():
    from dealfinder.collectors.autoscout import parse_item as as24
    from dealfinder.collectors.subito import parse_item as sub
    l = sub({"urn": "a", "urls": {"default": "u"}, "geo": {"town": {"value": "Legnano"},
                                                           "map": {"latitude": "45.5981", "longitude": "8.9152"}}})
    assert (l.lat, l.lon) == (45.6, 8.92)
    assert sub({"urn": "a", "urls": {"default": "u"}, "geo": {"town": {"value": "Monza"}}}).lat is None
    l = as24({"id": 1, "url": "/x", "location": {"zip": "46100", "latitude": 45.156, "longitude": 10.791}})
    assert (l.lat, l.lon, l.province) == (45.16, 10.79, "MN")


def test_luogo_citta_cap_e_comune_vicino():
    from dealfinder import geo
    assert geo.luogo("Sondrio")["provincia"] == "SO"
    assert geo.luogo("busto arsizio (VA)")["comune"] == "Busto Arsizio"
    x = geo.luogo("46100")
    assert x["provincia"] == "MN" and (x["lat"], x["lon"]) == geo.PROVINCE["MN"]
    assert geo.luogo("Paese Che Non Esiste") is None
    assert geo.comune_vicino(45.70, 9.67)["comune"] == "Bergamo"


# ------------------------------------------------------------------ salvataggio della posizione dell'utente

def test_posizione_salvata_arrotondata_con_il_comune():
    from dealfinder.web import app as A
    p = A.posizione_da_salvare({"lat": 45.698123, "lon": 9.667711, "fonte": "gps"})
    assert p == {"lat": 45.7, "lon": 9.67, "comune": "Bergamo", "fonte": "gps"}
    p = A.posizione_da_salvare({"comune": "Mantova (MN)", "fonte": "manuale"})
    assert p["comune"] == "Mantova" and p["lat"] == 45.16 and p["fonte"] == "manuale"
    assert A.posizione_da_salvare({"fonte": "milano"}) == {"lat": None, "lon": None, "comune": None, "fonte": "milano"}
    assert A.posizione_da_salvare({"lat": "x", "lon": 9}) is None
    assert A.posizione_da_salvare({"lat": 120, "lon": 9}) is None
    assert A.posizione_da_salvare({"comune": "Atlantide", "fonte": "manuale"}) is None
    import inspect
    src = inspect.getsource(A.update_me)
    assert "pos_comune=%s, pos_fonte=%s" in src and "round(lat, 3)" not in src
    mig = open(os.path.join(QUI, "..", "db", "migrations", "065_posizione_comune.sql"), encoding="utf-8").read()
    assert "pos_comune" in mig and "pos_fonte" in mig


def test_update_me_scrive_la_posizione():
    """PUT /api/me con la posizione: un solo UPDATE con lat/lon a 2 decimali e il comune (database finto)."""
    import asyncio
    from dealfinder.web import app as A
    scritti = []

    class Conn:
        def execute(self, q, p=None):
            scritti.append((q, p))
            return self

        def fetchone(self):
            return None

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    vecchi = A.current_user, A.connect, A.body, A.me
    try:
        A.current_user = lambda r: {"id": 7}
        A.connect = lambda: Conn()

        async def corpo(r):
            return {"posizione": {"lat": 45.1563, "lon": 10.7914, "fonte": "gps", "comune": "ignorato"}}
        A.body = corpo
        A.me = lambda r: "ok"
        assert asyncio.run(A.update_me(None)) == "ok"
    finally:
        A.current_user, A.connect, A.body, A.me = vecchi
    up = [p for q, p in scritti if q.startswith("UPDATE dealers SET lat")]
    assert up == [(45.16, 10.79, "Mantova", "gps", 7)]


def test_header_permissions_policy_con_geolocation_self():
    from dealfinder.web import app as A
    h = dict(A.SECURITY_HEADERS)[b"permissions-policy"]
    assert b"geolocation=(self)" in h and b"geolocation=()" not in h


# ------------------------------------------------------------------ frontend: raggio e distanza (app.js)

def _js() -> str:
    return open(JS, encoding="utf-8").read()


def test_frontend_raggi_default_e_testi():
    js = _js()
    assert 'const RAGGI = [[20,"20 km"],[50,"50 km"],[100,"100 km"],[200,"200 km"],[500,"500 km"],[0,"Tutte"]];' in js
    assert "const RAGGIO_BASE = 100;" in js
    assert '["vicine","Più vicine"]' in js
    assert "Ti mostriamo le auto entro ${S.raggio || RAGGIO_BASE} km da te. Consenti la posizione?" in js
    assert "salviamo solo il comune" in js and "Scegli città a mano" in js and "Cambia posizione" in js
    assert 'store.set("posChiesta", true)' in js
    assert js.count("navigator.geolocation.getCurrentPosition") == 1        # una sola richiesta, riusata


def _funz(js: str, nome: str) -> str:
    i = js.index("function " + nome + "(")
    j = js.index("\n}\n", i)
    return js[i:j + 3]


def test_haversine_e_filtro_raggio_in_node():
    node = shutil.which("node")
    if not node:
        return
    corpo = _funz(_js(), "haversine") + """
const BG = [45.70, 9.67];
const auto = [{id: 1, geo: [45.46, 9.19]}, {id: 2, geo: [45.16, 10.79]}, {id: 3, geo: [46.17, 9.87]},
              {id: 4, geo: [45.07, 7.69]}, {id: 5, geo: null}, {id: 6, geo: [45.69, 9.66]}];
const d = c => c.geo ? haversine(BG[0], BG[1], c.geo[0], c.geo[1]) : null;
const entro = r => auto.filter(c => !r || d(c) == null || d(c) <= r).map(c => c.id);
console.log(JSON.stringify({mi: d(auto[0]), r20: entro(20), r100: entro(100), r200: entro(200), tutte: entro(0)}));
"""
    r = subprocess.run([node, "-e", corpo], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    o = json.loads(r.stdout)
    assert 40 < o["mi"] < 46
    assert o["r20"] == [5, 6]                     # senza posizione: resta (come prima)
    assert o["r100"] == [1, 3, 5, 6]              # Mantova (~100,5 km da Bergamo in linea d'aria) fuori
    assert o["r200"] == [1, 2, 3, 4, 5, 6] and o["tutte"] == [1, 2, 3, 4, 5, 6]
