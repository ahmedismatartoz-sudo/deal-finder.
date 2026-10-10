"""Sito Scovo: schede compatte, numeri di telefono nascosti, nomi delle auto, firma delle foto."""
from dealfinder.web import foto, scovo


def _row(**k):
    r = {"id": 1, "source": "subito", "make": "mazda", "model": "mx-5", "version_raw": "MX-5 Roadster 2.0",
         "year": 2011, "mileage_km": 87000, "fuel": "benzina", "gearbox": "manuale", "city": "Settala",
         "province": "MI", "price_eur": 3000, "damage_class": "medio", "stage_reason": "opportunita",
         "private_median": 14369, "parts_cost_low": 860, "parts_cost_high": 2030, "n_photos": 4,
         "opened_by_me": False, "opens": 0}
    r.update(k)
    return r


def test_item_compatto():
    it = scovo.item(_row())
    assert it["nome"] == "Mazda MX-5 Roadster 2.0"
    assert (it["prezzo"], it["mercato"], it["rip_lo"], it["rip_hi"]) == (3000, 14369, 860, 2030)
    assert it["zona"] == "Settala (MI)" and it["foto"].startswith("/api/foto/1/0?s=")
    # guadagno al 30% sotto mercato, come lo calcola il telefono
    riv = round(it["mercato"] * 0.7)
    assert riv - it["prezzo"] - 140 - it["rip_hi"] == 4888


def test_item_scarta_danni_senza_stima():
    assert scovo.item(_row(parts_cost_low=None, parts_cost_high=None)) is None
    sana = scovo.item(_row(damage_class="nessuno", parts_cost_low=None, parts_cost_high=None))
    assert sana["rip_lo"] == sana["rip_hi"] == 0
    assert scovo.item(_row(private_median=None, resale_median_private=None, resale_median=None)) is None


def test_visibile_solo_fino_a_7():
    assert scovo.visible({"opened_by_me": False, "opens": 6}, 7)
    assert not scovo.visible({"opened_by_me": False, "opens": 7}, 7)
    assert scovo.visible({"opened_by_me": True, "opens": 9}, 7)


def test_telefoni():
    assert scovo.find_phone("chiamare 347 123 4567") == "+393471234567"
    assert scovo.find_phone("cell 347.123.45.67") == "+393471234567"
    assert scovo.find_phone("tel: 02 12345678") == "+390212345678"
    for t in ("Mazda MX5 20cc", "prezzo 3.500 km 250000", "km 350 000 prezzo 2 500", "anno 2015 323 cv"):
        assert scovo.find_phone(t) is None, t
    d = scovo.clean_desc("Info 3471234567 o mario@rossi.it")
    assert "347" not in d and "@" not in d


def test_nomi():
    assert scovo.model_name("classe-a") == "Classe A"
    assert scovo.model_name("x1") == "X1"
    assert scovo.nome({"make": "volkswagen", "model": "golf", "version_raw": "Golf 1.4 TSI"}) == "Volkswagen Golf 1.4 TSI"
    assert scovo.nome({"make": "fiat", "model": "500x", "version_raw": "500 1.2"}) == "Fiat 500X 500 1.2"


def test_pezzi_da_valutazione():
    items = scovo.parts_items({"lines": [{"label": "paraurti_anteriore", "low": 100, "high": 200}, {"label": "x"}]})
    assert items == [{"pezzo": "Paraurti anteriore", "da": 100, "a": 200}]


def test_firma_foto():
    s = foto.sign(5, 0)
    assert foto.check(5, 0, s) and not foto.check(5, 1, s) and not foto.check(5, 0, None)


def test_foto_ridotta():
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGBA", (3000, 2000), (10, 20, 30, 255)).save(b, "PNG")
    out = foto.shrink(b.getvalue())
    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG" and max(im.size) == 960
    assert foto.shrink(b"non e una foto") is None


def test_stato_accesso_social():
    from dealfinder.web import social
    st = social.make_state("google")
    assert social.check_state(st, st, "google")
    assert not social.check_state(st, st, "apple")          # stato di un altro fornitore
    assert not social.check_state(st, "altro", "google")    # cookie diverso
    assert not social.check_state(st[:-1] + "x", st[:-1] + "x", "google")   # firma alterata


def test_token_fornitore_e_codice_monouso():
    import base64, json
    from dealfinder.web import social
    body = base64.urlsafe_b64encode(json.dumps({"sub": "1", "email": "a@b.it"}).encode()).decode().rstrip("=")
    assert social._claims("x." + body + ".y")["email"] == "a@b.it"
    code = social.one_time_code({"id": 3, "role": "commerciante", "name": "A", "email": "a@b.it"})
    assert social.redeem(code)["id"] == 3 and social.redeem(code) is None


def test_messaggi_sessione_chiusa():
    from dealfinder.web import sessions
    assert "altro dispositivo" in sessions.REVOKED_MSG["altro_dispositivo"]
    assert sessions.MAX_DISPOSITIVI >= 1
    assert sessions.check(None, 1) == (False, None)


def test_indirizzi_foto_subito():
    from dealfinder.web.foto import candidate_urls
    u = candidate_urls("imgid:8ecb9da4-ef52-4eb3-b3cb-cea7a073cafb?rule=x")
    assert u[0].endswith("/images/8e/8ecb9da4-ef52-4eb3-b3cb-cea7a073cafb?rule=gallery-desktop-2x-auto")
    assert candidate_urls("https://a.b/c.jpg") == ["https://a.b/c.jpg"]
    assert candidate_urls(None) == [] and candidate_urls("boh") == []
