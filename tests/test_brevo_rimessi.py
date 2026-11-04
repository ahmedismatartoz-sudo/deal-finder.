"""4/11: i contatti vecchi rimessi in Brevo non comprendono mai disiscritti, Loconte, email non valide né doppioni."""


def test_scegli_rimessi():
    import sys, types
    sys.modules.setdefault("httpx", types.ModuleType("httpx"))   # in locale httpx può mancare: qui non serve
    from dealfinder.marketing.brevo_liste import scegli_rimessi
    from dealfinder.marketing.invii import impronta
    emails = ["A@x.it", "a@x.it", "b@y.it", "foto@2x.avif", "info@loconte.it", "c@z.it", "d@w.it", "nonemail"]
    bloccati = {impronta("b@y.it")}
    out = scegli_rimessi(emails, bloccati, gia_in_brevo={"c@z.it"}, posto=10)
    assert out == ["a@x.it", "d@w.it"]
    assert scegli_rimessi(emails, bloccati, set(), posto=1) == ["a@x.it"]
    assert scegli_rimessi(emails, bloccati, set(), posto=0) == []


def test_scegli_nuovi_esclude_usati_pec_e_tetto():
    from dealfinder.marketing.brevo_liste import scegli_nuovi
    from dealfinder.marketing.invii import impronta
    em = ["Info@A.it", "info@a.it", "x@pec.it", "b@legalmail.it", "c@b.it", "d@c.it", "via@d.it", "logo@2x.png", "e@e.it"]
    out = scegli_nuovi(em, {impronta("via@d.it")}, {"c@b.it"}, {"d@c.it"}, 10)
    assert out == ["info@a.it", "e@e.it"]
    assert scegli_nuovi(em, set(), set(), set(), 1) == ["info@a.it"]
