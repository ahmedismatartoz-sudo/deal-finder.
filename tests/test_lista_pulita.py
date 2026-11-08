"""8/11: lista pulita di chi apre (niente Libero/Microsoft, rimbalzi, disiscritti, PEC)."""
from dealfinder.marketing.brevo_liste import scegli_pulita
from dealfinder.marketing.invii import impronta


def test_scegli_pulita_filtri_e_ordine():
    aperti = ["A@gmail.com", "b@libero.it", "c@hotmail.it", "d@outlook.com", "e@azienda.it", "a@gmail.com",
              "f@pec.it", "g@gmail.com", "h@gmail.com", "loconte@x.it", "i@tiscali.it", "foto@2x.png"]
    out = scegli_pulita(aperti, bloccati={impronta("g@gmail.com")}, rimbalzati={"h@gmail.com"}, totale=10)
    assert out == ["a@gmail.com", "e@azienda.it", "i@tiscali.it"]
    assert scegli_pulita(aperti, set(), set(), totale=1) == ["a@gmail.com"]
