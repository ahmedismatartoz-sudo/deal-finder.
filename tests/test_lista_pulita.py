"""8/11: lista pulita di chi apre (niente Libero/Microsoft, rimbalzi, disiscritti, PEC)."""
from dealfinder.marketing.brevo_liste import scegli_pulita
from dealfinder.marketing.invii import impronta


def test_scegli_pulita_filtri_e_ordine():
    aperti = ["A@gmail.com", "b@libero.it", "c@hotmail.it", "d@outlook.com", "e@azienda.it", "a@gmail.com",
              "f@pec.it", "g@gmail.com", "h@gmail.com", "loconte@x.it", "i@tiscali.it", "foto@2x.png"]
    out = scegli_pulita(aperti, bloccati={impronta("g@gmail.com")}, rimbalzati={"h@gmail.com"}, totale=10)
    assert out == ["a@gmail.com", "e@azienda.it", "i@tiscali.it"]
    assert scegli_pulita(aperti, set(), set(), totale=1) == ["a@gmail.com"]


def test_conta_email_solo_numeri():
    from dealfinder.marketing.brevo_liste import conta_email
    testi = ['{"email": "a@gmail.com", "x": "b@libero.it"}', "c@autosalone.it a@gmail.com d@pec.it z@gmail.com",
             '{"e":"loconte@x.it"} logo@2x.png']
    r = conta_email(testi, bloccati={impronta("z@gmail.com")}, inviati={impronta("a@gmail.com")})
    assert r["totale"] == 6 and r["usabili"] == 3 and r["escluse_pec"] == 1 and r["escluse_loconte"] == 1
    assert r["escluse_disiscritte_o_rimbalzate"] == 1 and r["usabili_gia_contattate"] == 1
    assert r["usabili_gmail"] == 1 and r["usabili_libero_microsoft"] == 1 and r["usabili_aziendali"] == 1
