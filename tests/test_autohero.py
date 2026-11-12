from dealfinder.collectors import autohero

PAGINA = """<html><body>
<div class="card"><a href="/it/fiat-panda/id/77bc1218-6d14-41fe-8004-64bc952810f5/"><img alt="Fiat Panda"></a>
<h3>Fiat Panda</h3><p>1.0 Mild-Hybrid City Life</p><span><s>11.999 €</s></span><span>10.999 €</span><span>182 €/mese</span>
<ul><li>•2021</li><li>•Ibrido</li><li>•61.081 km</li><li>•Manuale</li></ul>
<a href="/it/fiat-panda/id/77bc1218-6d14-41fe-8004-64bc952810f5/">Visualizza quest'auto</a></div>
<div class="card"><a href="/it/audi-a-6/id/2fa5d904-0f68-4579-9803-fd0c64450c19/">Audi A6</a>
<p>40 TDI S tronic Business</p><span>27.490 €</span><span>399 €/mese</span><ul><li>•2020</li><li>•Diesel</li>
<li>•98.500 km</li><li>•Automatico</li></ul></div></body></html>"""


def test_schede():
    s = autohero.schede_da_html(PAGINA)
    assert [x["uuid"][:8] for x in s] == ["77bc1218", "2fa5d904"]
    a, b = s
    assert (a["prezzo"], a["prezzo_barrato"], a["rata"], a["anno"], a["km"], a["carburante"], a["cambio"]) == \
        (10999, 11999, 182, 2021, 61081, "Ibrido", "Manuale")
    assert (b["prezzo"], b["anno"], b["km"], b["carburante"], b["cambio"]) == (27490, 2020, 98500, "Diesel", "Automatico")


def test_struttura():
    st = autohero.struttura(PAGINA)
    assert st["link_auto"] == 2 and st["next_data"] is False


DETTAGLIO = """<html><body><h1>Volkswagen Golf</h1><dl><dt>Allestimento</dt><dd>Life 1.0 eTSI DSG</dd>
<dt>Potenza</dt><dd>81 kW (110 CV)</dd><dt>Colore</dt><dd>Grigio</dd><dt>Proprietari</dt><dd>1</dd></dl>
<h2>Equipaggiamento</h2><ul><li>Navigatore</li><li>Cruise control adattivo</li><li>Sensori di parcheggio</li></ul>
</body></html>"""


def test_dettaglio():
    d = autohero.dettaglio_da_html(DETTAGLIO)
    assert d["campi"]["allestimento"] == "Life 1.0 eTSI DSG"
    assert d["campi"]["potenza"].startswith("81 kW") and d["campi"]["proprietari"] == "1"
    assert d["optional"] == ["Navigatore", "Cruise control adattivo", "Sensori di parcheggio"]


def test_versione_scheda():
    a = autohero.schede_da_html(PAGINA)[0]
    assert "City Life" in (a["versione"] or "")
