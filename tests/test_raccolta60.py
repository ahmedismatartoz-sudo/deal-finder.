"""16/11 sera: raccolta di tutti i commercianti entro 60 km (AutoScout24 + Subito)."""
from dealfinder.pipeline import raccolta60 as R


def test_comuni_zona_ha_milano_monza_e_niente_oltre_60():
    z = R.comuni_zona()
    assert "milano" in z and "monza" in z and "sesto-san-giovanni" in z
    assert all(km is None or km <= 60 for _, _, km in z.values())
    assert len(z) > 700


def test_dati_pagina_cellulari_fissi_email():
    html = ('<html><body>Chiamaci 347 123 4567 oppure 02 1234567. Scrivi a <a href="mailto:info@autorossi.it">'
            'info@autorossi.it</a> <a href="https://www.autorossi.it/">sito</a></body></html>')
    d = R.dati_pagina(html)
    assert d["cell"] == ["3471234567"]
    assert "021234567" in d["fissi"]
    assert d["email"] == ["info@autorossi.it"]
    assert d["sito"].startswith("https://www.autorossi.it")


def test_unisci_per_cellulare_e_per_nome():
    s = [{"nome": "Auto Rossi S.r.l.", "prov": "MI", "comune": "Milano", "cell": ["3471234567"], "fonti": {"autoscout24"},
          "link_autoscout": "https://www.autoscout24.it/concessionari/auto-rossi"},
         {"nome": "AUTO ROSSI", "prov": "MI", "cell": [], "email": ["info@autorossi.it"], "fonti": {"subito"}},
         {"nome": "Bianchi Car", "prov": "MB", "cell": ["3471234567"], "fonti": {"subito"}},
         {"nome": "Verdi", "prov": "CO", "cell": ["3330000000"], "fonti": {"subito"}}]
    out = R.unisci(s)
    assert len(out) == 2
    g = out[0]
    assert g["email"] == ["info@autorossi.it"] and g["fonti"] == {"autoscout24", "subito"}
    assert g["link_autoscout"].endswith("auto-rossi")


def test_nome_normalizzato():
    assert R.nn("Auto Rossi S.r.l.") == R.nn("AUTO ROSSI srl") == "rossi"


def test_annunci_autoscout_danno_salone_con_telefono():
    r = R.Raccolta.__new__(R.Raccolta)
    import threading
    r.lock, r.saloni, r.zona = threading.Lock(), {}, {"milano": ("Milano", "MI", 0.0)}
    r.st = {"annunci_autoscout": 0}
    items = [{"url": "/annunci/x-1", "location": {"city": "Milano", "zip": "20121"},
              "seller": {"id": "77", "type": "Dealer", "companyName": "Auto Rossi Srl",
                         "phones": [{"phoneType": "Mobile", "formattedNumber": "+39 347 1234567", "callTo": "+393471234567"},
                                    {"phoneType": "Office", "formattedNumber": "02 1234567", "callTo": "+39021234567"}]}},
             {"seller": {"id": "9", "type": "PrivateSeller"}}]
    r._as_items(items)
    assert list(r.saloni) == ["as:77"]
    s = r.saloni["as:77"]
    assert s["cell"] == ["3471234567"] and s["prov"] == "MI" and s["comune"] == "Milano"
    assert s["link_autoscout"].endswith("/annunci/x-1")


def test_pulisci_toglie_email_e_siti_di_subito():
    d = R._pulisci({"email": ["privacy@subito.it", "info@autorossi.it"], "sito": "https://www.subito.it/x", "cell": [], "fissi": []})
    assert d["email"] == ["info@autorossi.it"] and d["sito"] is None


def test_email_dal_salone_autoscout_e_dal_sito():
    from types import SimpleNamespace as NS
    pagine = {
        "https://www.autoscout24.it/annunci/x-1": '<a href="/concessionari/auto-rossi">salone</a>',
        "https://www.autoscout24.it/concessionari/auto-rossi": '<a href="https://www.autorossi.it/">sito</a>',
        "https://www.autoscout24.it/concessionari/auto-rossi/chi-siamo": "niente",
        "https://www.autorossi.it/contatti": '<a href="mailto:info@autorossi.it">scrivici</a>',
    }
    c = NS(get=lambda u: NS(status_code=200 if u in pagine else 404, text=pagine.get(u, "")))
    d = R.cerca_email_salone(c, {"link_autoscout": "https://www.autoscout24.it/annunci/x-1"}, pausa_as=0)
    assert d["email"] == ["info@autorossi.it"] and d["fonte_email"] == "sito"
    assert R._link_salone_as('<a href="/concessionari/regioni/lombardia/">') is None


def test_osm_saloni_legge_nome_sito_email():
    from types import SimpleNamespace as NS
    el = {"elements": [{"tags": {"shop": "car", "name": "Auto Bianchi", "website": "www.autobianchi.it", "addr:city": "Monza"}},
                       {"tags": {"shop": "car", "name": "Verdi Car", "email": "info@verdicar.it"}},
                       {"tags": {"shop": "car"}}]}
    c = NS(post=lambda *a, **k: NS(status_code=200, json=lambda: el))
    out = R.osm_saloni(c)
    assert [o["nome"] for o in out] == ["Auto Bianchi", "Verdi Car"]
    assert out[0]["sito"] == "https://www.autobianchi.it" and out[1]["email"] == "info@verdicar.it"
