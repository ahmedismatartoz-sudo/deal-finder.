from dealfinder.marketing.brevo_liste import righe_cliccati


def test_abbina_ordina_ed_esclude():
    db = [{"email": ["a@x.it"], "nome": "Auto A", "comune": "Monza", "prov": "MB", "cell": ["3331112222"], "tel": None},
          {"email": ["b@x.it"], "nome": "Auto B", "comune": "Pavia", "prov": "PV", "cell": [], "tel": None}]
    r = righe_cliccati(["B@x.it", "a@x.it", "a@x.it", "info@loconte.it", "matteo@officinemulino.com", "z@y.it"], db)
    assert [x["email"] for x in r] == ["a@x.it", "b@x.it", "z@y.it"]
    assert r[0]["tel"] == ["3331112222"] and r[1]["tel"] == [] and r[2]["nome"] == ""
