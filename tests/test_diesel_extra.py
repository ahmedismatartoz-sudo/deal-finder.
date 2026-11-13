"""13/11 sera (ramo diesel70, cliente: "fai entrare più diesel ma solamente macchine per bene, altre 70"): ingresso
controllato DIESEL_EXTRA nelle regole del sito. Niente rete né database."""
import dataclasses
import os

from dealfinder.web import scovo
from dealfinder.web import sito_regole as SR

G = SR.Regole()
SPENTO = dataclasses.replace(G, diesel_extra=0)
PULITA = {"decisione": "tieni", "confidenza": 0.9, "danni": 0.05, "truffa": 0.05, "guasto": 0.0, "fonte": "claude"}


def auto(i, carb="diesel", anno=2015, km=90000, prezzo=8000, margine=3000, M=10000, prov="MI", tel=True,
         seller="commerciante", jev="pulita", **kw):
    r = {"tel": tel, "seller_type": seller, "provincia": prov, "jev": dict(PULITA) if jev == "pulita" else jev}
    r.update(kw.pop("_r", {}))
    it = {"id": i, "prezzo": prezzo, "anno": anno, "km": km, "carb": carb, "fonte": "Subito",
          "pv": {"categoria": "opportunita", "M": M, "R": prezzo + margine, "R_finale": prezzo + margine,
                 "margine": margine, "totale_costi": 0, "n": 10},
          "_r": r}
    it.update(kw)
    return it


def elenco(items, g=G):
    return SR.elenco_sito([dict(it, _r=dict(it["_r"]), pv=dict(it["pv"])) for it in items], g, scrivi_log=False)


def ids(items, g=G):
    return sorted(it["id"] for it in elenco(items, g)[0])


def test_default_70_e_interruttore():
    assert G.diesel_extra == 70
    prima = os.environ.get("DIESEL_EXTRA")
    try:
        os.environ.pop("DIESEL_EXTRA", None)
        assert SR.Regole.da_env().diesel_extra == 70
        os.environ["DIESEL_EXTRA"] = "0"
        assert SR.Regole.da_env().diesel_extra == 0
        os.environ["DIESEL_EXTRA"] = "12"
        assert SR.Regole.da_env().diesel_extra == 12
    finally:
        if prima is None:
            os.environ.pop("DIESEL_EXTRA", None)
        else:
            os.environ["DIESEL_EXTRA"] = prima


def test_diesel_per_bene_entra_solo_se_acceso():
    a = auto(1)
    assert ids([a], SPENTO) == []                       # oggi: commerciante_non_ammesso (diesel solo dal 2017)
    out, fun = elenco([a], SPENTO)
    assert any((p.get("motivi") or {}).get("commerciante_non_ammesso") for p in fun.passi)
    out, fun = elenco([a])
    assert [it["id"] for it in out] == [1]
    assert out[0]["diesel_extra"] is True and out[0]["commerciante"] is True
    assert out[0]["pv"]["margine"] == 3000 and out[0]["prezzo"] == 8000      # prezzi e margine non toccati
    assert fun.diesel_extra == {"n": 70, "candidate": 1, "entrate": 1}


def test_diesel_non_per_bene_resta_fuori():
    casi = [
        auto(2, anno=2014),                                             # prima del 2015 (non Euro 6)
        auto(3, _r={"danno_dichiarato": True}),                         # danno dichiarato
        auto(4, incidentata=True),
        auto(5, jev=dict(PULITA, danni=0.5)),                           # danni nelle foto
        auto(6, jev=dict(PULITA, guasto=0.5)),                          # guasto letto dall'AI
        auto(7, jev=dict(PULITA, truffa=0.4)),                          # segnale di truffa
        auto(8, jev=dict(PULITA, decisione="scarta", confidenza=0.3)),  # AI: scarta (anche con confidenza bassa)
        auto(9, jev=None),                                              # controllo AI non fatto
        auto(10, jev=dict(PULITA, danni=None)),                         # foto non viste
        auto(11, tel=False),                                            # senza numero
        auto(12, prov="TO"),                                            # fuori da Milano e dintorni
        auto(13, prov="RM"),                                            # Roma mai
        auto(14, margine=1700),                                         # sotto la soglia (1.800 € a 8.000 €)
        auto(15, _r={"loconte": True}),
        auto(16, _r={"promo": True}),                                   # promo: come oggi
        auto(17, prezzo=7000, M=10000),                                 # prezzo esca (< 75% di M): come oggi
        auto(18, _r={"testo_truffa": True}),
        auto(19, _r={"difetto": {"grave": False, "costo": 300, "motivo": "frizione"}}),
    ]
    assert scovo.soglia(8000) == 1800
    out, fun = elenco(casi)
    assert out == [], [it["id"] for it in out]
    assert fun.diesel_extra["entrate"] == 0


def test_diesel_fuori_fascia_anche_a_benzina_non_entra():
    # fuori anche se fosse a benzina (oltre 10.000 € o 100.000 km): il motivo non è il diesel
    a = auto(20, prezzo=12000, M=16000, margine=4000)
    b = auto(21, km=120000)
    out, fun = elenco([a, b])
    assert out == [] and fun.diesel_extra["candidate"] == 0


def test_tetto_n_le_migliori_per_margine_su_soglia():
    items = [auto(100 + k, margine=2200 + 100 * k) for k in range(8)]
    g = dataclasses.replace(G, diesel_extra=3)
    out, fun = elenco(items, g)
    assert sorted(it["id"] for it in out) == [105, 106, 107]
    assert fun.diesel_extra == {"n": 3, "candidate": 8, "entrate": 3}
    assert len(elenco(items)[0]) == 8                      # con 70 entrano tutte


def test_benzina_e_privati_invariati():
    altre = [
        auto(200, carb="benzina", anno=2012),                           # commerciante fascia: entra come oggi
        auto(201, carb="benzina", seller="privato", anno=2010),
        auto(202, carb="diesel", seller="privato", anno=2012),          # privato diesel: come oggi
        auto(203, carb="diesel", anno=2018),                            # diesel dal 2017: già fascia
        auto(204, carb="benzina", anno=2012, margine=1000),             # sotto soglia: fuori come oggi
        auto(205, carb="benzina", prezzo=12000, M=16000, margine=4000), # commerciante non fascia: fuori come oggi
    ]
    for it in altre:
        it["visto_dal"] = None
    spento = {it["id"]: (it["pv"], it.get("_tipo")) for it in elenco(altre, SPENTO)[0]}
    acceso = {it["id"]: (it["pv"], it.get("_tipo")) for it in elenco(altre + [auto(1)])[0]}
    assert set(acceso) - set(spento) == {1}
    for i, v in spento.items():
        assert acceso[i] == v


def test_tetto_40_commercianti_invariato():
    lontani = [auto(300 + k, carb="benzina", prezzo=12000, M=16000, margine=4000 + k, prov="MB",
                    _r={"valutazione_nuova": True}) for k in range(45)]
    out_s, _ = elenco(lontani, SPENTO)
    out_a, fun = elenco(lontani + [auto(1), auto(2, prov="BG")])
    lont_s = sorted(it["id"] for it in out_s if it.get("_tipo") == "comm_lontano")
    lont_a = sorted(it["id"] for it in out_a if it.get("_tipo") == "comm_lontano")
    assert len(lont_s) == 40 and lont_a == lont_s
    assert sorted(it["id"] for it in out_a if it.get("diesel_extra")) == [1, 2]
    assert fun.diesel_extra["entrate"] == 2
