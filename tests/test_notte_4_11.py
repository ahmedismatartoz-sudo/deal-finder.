"""4/11 notte: due errori trovati dalla verifica indipendente, con un test ciascuno perché non tornino."""


def test_confronti_senza_lazio():
    from dealfinder import store
    for p in ("RM", "LT", "FR", "VT", "RI"):
        assert p not in store._PROV_BASE, p
    assert "MI" in store._PROV_BASE and "RA" in store._PROV_BASE


def test_costo_difetto_non_tolto_due_volte():
    from dealfinder.web import sito_regole as SR
    g = SR.Regole.da_env()
    base = {"id": 1, "prezzo": 5000, "_r": {"difetto": {"costo": 500, "motivo": "frizione"}}}
    gia = dict(base, pv={"M": 9000, "R": 9000, "margine": 3500, "costo_difetti_incluso": True, "calibrato": True})
    SR.t_margine_sito(gia, g)
    assert gia["pv"]["margine"] == 3500
    no = dict(base, pv={"M": 9000, "R": 9000, "margine": 3500, "calibrato": True})
    SR.t_margine_sito(no, g)
    assert no["pv"]["margine"] == 3000


def test_senza_gemelle_aspetta_ai():
    from dealfinder.web import sito_regole as SR
    g = SR.Regole.da_env()
    it = {"id": 2, "prezzo": 6000, "pv": {"M": 9000, "margine": 2000, "serve_controllo_ai": True}, "_r": {"tel": True}}
    assert SR.esito_truffa(it, g) == ("fuori", "aspetta_controllo_ai")
    it["_r"]["jev"] = {"truffa": 0.1}
    assert SR.esito_truffa(it, g)[0] == "tieni"
