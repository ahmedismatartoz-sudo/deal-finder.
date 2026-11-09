"""8/11: email su misura, 2 auto vicine al salone."""
from dealfinder.marketing import su_misura as SM
from dealfinder.marketing.invii import impronta


def it(i, marca, modello, prezzo, R, zona, n=12, km=80000, anno=2019):
    return {"id": i, "marca": marca, "modello": modello, "nome": f"{marca} {modello} 1.0", "anno": anno, "km": km,
            "carb": "benzina", "prezzo": prezzo, "pv": {"R": R, "margine": R - prezzo - 100, "n": n}, "zona": zona}


ITEMS = [it(1, "Fiat", "Panda", 7000, 10000, "Monza (MB)"), it(2, "Fiat", "Panda", 7200, 10200, "Lissone (MB)"),
         it(3, "Ford", "Puma", 12000, 15500, "Milano (MI)"), it(4, "Audi", "A3", 15000, 19000, "Brescia (BS)"),
         it(5, "Opel", "Corsa", 6000, 8000, "Seregno (MB)", n=3), it(6, "Kia", "Rio", 8000, 11000, "Torino (TO)")]


def test_auto_vicine_modelli_diversi_e_prezzo_regola():
    auto = SM.auto_sito(ITEMS)
    assert {a["id"] for a in auto} == {1, 2, 3, 4, 6}                      # 5 ha pochi confronti
    due = SM.scegli_due({"comune": "Monza", "prov": "MB", "pmed": None, "marche": []}, auto, {})
    assert [a["id"] for a in due] == [1, 3]                                  # stessa città, poi Milano; Panda una sola
    a1, d1 = SM.testi(due[0])
    assert "Monza" in a1 and d1.startswith("5.500 €") and "margine +4.500 €" in d1   # 7.000 -1.500
    assert SM.scegli_due({"comune": "Roma", "prov": "RM"}, auto, {}) == []   # niente auto vicine


def test_saloni_filtri_e_ordine():
    righe = [{"chiave": "a", "email": ["x@libero.it", "info@salone.it"], "comune": "Bergamo", "prov": "BG"},
             {"chiave": "b", "email": ["b@gmail.com"], "comune": "Milano", "prov": "MI"},
             {"chiave": "c", "email": ["c@pec.it"], "comune": "Monza", "prov": "MB"},
             {"chiave": "d", "email": ["d@gmail.com"], "comune": "Brescia", "prov": "BS"},
             {"chiave": "e", "email": ["no@gmail.com"], "comune": "Lodi", "prov": "LO"},
             {"chiave": "f", "email": ["f@gmail.com"], "comune": None, "prov": None}]
    s = SM.scegli_saloni(righe, bloccati={impronta("no@gmail.com")}, aperti={"d@gmail.com"}, totale=10)
    assert [x["email"] for x in s] == ["d@gmail.com", "b@gmail.com", "info@salone.it"]


def test_html_ha_i_segnaposto():
    h = SM.html_email()
    assert "{{ contact.SK_AUTO1 }}" in h and "{{ contact.SK_DATI2 }}" in h and "348 242 5633" in h


def test_secondo_giro_esclude_chi_ha_ricevuto_e_copre_senza_luogo():
    righe = [{"chiave": "a", "email": ["a@gmail.com"], "comune": "Monza", "prov": "MB"},
             {"chiave": "b", "email": ["b@gmail.com"], "comune": None, "prov": None},
             {"chiave": "c", "email": ["c@gmail.com"], "comune": "Torino", "prov": "TO"},
             {"chiave": "d", "email": ["gia@gmail.com"], "comune": "Milano", "prov": "MI"}]
    s = SM.scegli_secondo(righe, set(), {"gia@gmail.com"}, 10)
    assert [x["email"] for x in s] == ["a@gmail.com", "c@gmail.com", "b@gmail.com"]
    out = SM.assegna_secondo(s, SM.auto_sito(ITEMS), 10)
    fr = {x["email"]: x["frase"] for x in out}
    assert fr["a@gmail.com"].endswith("vicino a voi:")
    assert "Milano e dintorni" in fr["c@gmail.com"]          # a Torino c'è una sola auto: le 2 migliori di Milano
    assert "Milano e dintorni" in fr["b@gmail.com"]
    assert "{{ contact.SK_FRASE }}" in SM.html_email_secondo()


def test_officine_mulino_mai():
    from dealfinder.marketing.brevo_liste import scegli_pulita
    assert not SM.email_buona("matteo@officinemulino.com")
    assert scegli_pulita(["matteo@officinemulino.com", "a@gmail.com"], set(), set(), 5) == ["a@gmail.com"]


def test_righe_solo_tiene_solo_i_non_aperti():
    from dealfinder.marketing.su_misura import righe_solo
    r = righe_solo([{"nome": "A", "email": ["a@x.it", "b@x.it"]}, {"nome": "B", "email": ["c@x.it"]}], {"b@x.it"})
    assert r == [{"nome": "A", "email": ["b@x.it"]}]
