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
