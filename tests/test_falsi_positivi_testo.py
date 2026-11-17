"""17/11 (notte-06): falsi positivi dei controlli sul testo (truffa, numero estero, segni da rivenditore, promo).
Ogni correzione ha i suoi casi veri (devono passare) e i casi truffa/rivenditore che devono restare scartati."""
from dealfinder.web import sito_regole as SR


# ------------------------------------------------------------------ testo truffa / pagamento estero
ONESTI = [
    "Vendo Golf 2015, no perditempo, no spedizioni, visibile a Monza",
    "Non effettuo spedizioni. Auto visionabile a Milano",
    "Non si effettuano spedizioni, ritiro di persona",
    "NON chiedo caparra prima di vedere l'auto: venite a provarla",
    "Nessuna caparra, nessun anticipo: si paga al passaggio di proprietà",
    "non richiedo alcun anticipo per bloccarla",
    "Astenersi commercianti e trasportatori",
    "Spedizioni escluse, solo ritiro in sede",
    "spedizione: no",
    "Niente corriere, l'auto si ritira a Bergamo",
    "senza caparra, pagamento al ritiro",
    "Vendo perché trasferito per lavoro a Torino",
    "mi sono trasferita per lavoro, la macchina non mi serve più",
    "No a spedizioni o caparre",
]
TRUFFE = [
    "chiedo caparra con bonifico prima di vedere l'auto",
    "l'auto si trova in Germania, spedizione con corriere",
    "Non visionabile, spedizione con corriere a domicilio",
    "Nessun problema per la spedizione in tutta Italia",
    "nessun problema spedizione",
    "Nessuna caparra, pagamento anticipato con bonifico",
    "non posso fartela vedere, spedizione tramite trasportatore",
    "Sono stato trasferito per lavoro in Scozia, auto in Italia con un amico",
    "trasferito all'estero, pagamento anticipato",
    "Non appena ricevuto il bonifico spedisco l'auto",
    "No perditempo. Spedisco in tutta Italia",
    "no perditempo spedisco ovunque",
    "ricarica postepay per bloccarla",
    "pago con western union",
    "Mi trovo in Spagna per lavoro",
    "militare in missione, l'auto è in deposito",
    "senza problemi spedisco ovunque",
    # 17/11 (giudice): chi deve astenersi e i participi non negano il pezzo truffa che segue
    "astenersi curiosi pagamento anticipato",
    "non perditempo spedisco",
    "astenersi commercianti spedisco ovunque",
    "non sono commercianti caparra anticipata",
    "caparra non necessaria spedisco io",
    # la negazione dopo vale solo a fine frase
    "trasporto con corriere esclusa visione",
]


def test_testo_truffa_negazioni_e_trasferimento():
    for t in ONESTI:
        assert not SR.testo_truffa(t), t
    for t in TRUFFE:
        assert SR.testo_truffa(t), t
    assert not SR.testo_truffa("") and not SR.testo_truffa(None)


def test_numero_estero_codici_non_telefoni():
    assert SR.tel_estero("chiamare +44 7700 900123") and not SR.tel_estero("tel +39 333 1234567")
    assert SR.tel_estero("whatsapp 0044 7700 900123")
    assert SR.tel_estero("contatti 0049 151 2345678, rif. pratica 5")
    for t in ("Rif. 0012345678", "Codice annuncio: 00471234567", "cod. 0012 345 6789", "telaio 0041234567890"):
        assert not SR.tel_estero(t), t


# ------------------------------------------------------------------ segni da rivenditore nel testo dei privati
def test_segni_commerciante_luoghi_di_acquisto_e_tagliando():
    privati = [
        "Tagliandi sempre in concessionaria ufficiale, libretto service",
        "acquistata nuova presso Autotorino S.p.A. nel 2016",
        "revisione fatta da Rossi Auto srl a marzo",
        "comprata in autosalone, unico proprietario",
        "manutenzione presso concessionaria autorizzata Fiat",
        "fatture della concessionaria ufficiale disponibili",
    ]
    rivenditori = [
        "Autosalone Rossi propone",
        "Rossi Auto srl vende Golf",
        "Garanzia 12 mesi inclusa",
        "siamo un rivenditore, IVA esposta",
        "Concessionaria ufficiale Fiat vende",
        "Visita il nostro salone a Lodi",
        "La nostra concessionaria offre",
        "tagliandi in concessionaria ufficiale; garanzia di 12 mesi",   # un segno vero basta
        # 17/11 (giudice): la sola preposizione non basta, "nostro" resta sempre segno
        "Venduta da Autosalone Rossi",
        "presso il nostro autosalone",
        "visibile presso autosalone Bianchi",
        "disponibile in autosalone a Milano",
        "si trova presso Rossi srl",
        "Vendita da parte di autosalone",
        "tagliandi presso il nostro autosalone",
        "auto tagliandata, venduta da Rossi srl",
    ]
    for t in privati:
        assert not SR.segni_commerciante(t), t
    for t in rivenditori:
        assert SR.segni_commerciante(t), t


def test_privato_con_tagliandi_in_concessionaria_resta_privato():
    from tests.test_sito_regole import auto, ids
    t = "Tagliandi sempre in concessionaria ufficiale, gomme nuove"
    assert ids([auto(1, _r={"segni_commerciante": SR.segni_commerciante(t)})]) == [1]
    assert ids([auto(2, _r={"segni_commerciante": SR.segni_commerciante("Autosalone Rossi")})]) == []


# ------------------------------------------------------------------ promo dei commercianti
def test_promo_falsi_positivi():
    from dealfinder.web import app
    rx = app._PROMO_RE
    innocui = [
        "Revisione promossa a ottobre",
        "Disponibile a partire da lunedì",
        "Prezzo con passaggio di proprietà incluso",
        "prezzo con garanzia 12 mesi",
        "Prezzo con IVA esposta",
        "Prezzo con tagliando appena fatto",
        "Prezzo non vincolato al finanziamento",
        "Prezzo non vincolato a finanziamento né permuta",
    ]
    promo = [
        "PROMO di novembre",
        "promozione valida fino a fine mese",
        "a partire da 199 € al mese",
        "a partire da € 99",
        "Prezzo con finanziamento",
        "prezzo con permuta",
        "prezzo con rottamazione",
        "Prezzo vincolato al finanziamento",
        "prezzo valido solo con finanziamento",
        "prezzo + IVA",
        "passaggio escluso",
        "TAN 6,99% TAEG 8,5%",
        "Prezzo con passaggio escluso",
        "Revisione promossa. Prezzo promo",
    ]
    for t in innocui:
        assert not SR.promo_testo(t, rx), t
    for t in promo:
        assert SR.promo_testo(t, rx), t


def test_dal_testo_usa_le_nuove_funzioni():
    from dealfinder.web import app
    tx = app._dal_testo("Golf, tagliandi in concessionaria ufficiale, no spedizioni, prezzo con passaggio incluso", None)
    assert not tx["testo_truffa"] and not tx["segni_commerciante"] and not tx["promo_testo"]
    tx = app._dal_testo("Autosalone: Golf, spedizione con corriere, prezzo promo", None)
    assert tx["testo_truffa"] and tx["segni_commerciante"] and tx["promo_testo"]
