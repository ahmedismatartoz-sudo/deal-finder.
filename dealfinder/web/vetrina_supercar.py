"""23/11 (cliente: "Supercar e macchine sportive: le voglio subito sul sito, tutte quelle della Lombardia").

VETRINA A PARTE "Supercar Lombardia": tutte le sportive e supercar in vendita nelle 12 province lombarde, di privati e
di commercianti, al prezzo dell'annuncio. NON sono affari: niente margine, niente stima. 23/11 sera (cliente: "il
prezzo di mercato per ogni auto, quando esiste davvero"): accanto al prezzo c'è il mercato SOLO se una valutazione
già salvata nel database lo dice (vedi MERCATO_SQL e mercato_valido): nessun calcolo nuovo, nessuna AI. Per questo
sta FUORI da sito_regole e dall'elenco del sito (_elenco): le regole degli affari, i tetti dei commercianti, il
conteggio delle auto del sito e le email automatiche non la vedono.

  e_sportiva(make, model, version, price)   funzione pura: è una sportiva / supercar?
  e_supercar(make, price)                   funzione pura: "supercar" (marca di lusso o da 70.000 €) o "sportiva"
  scegli(righe, ...)                        funzione pura: esclusioni (Loconte, rata/leasing, Roma, doppioni...) e schede
  carica(conn, ...)                         letture leggere del database, a blocchi
  elenco(connetti, ...)                     con la memoria di qualche minuto

Nelle schede non c'è mai un numero di telefono (solo ha_telefono sì/no): il contatto passa dal link all'annuncio.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone

from ..ai.supercar_vero import LEASING_RE
from . import foto, scovo
from . import sito_regole as SR

log = logging.getLogger("scovo")

LOMBARDIA = frozenset({"BG", "BS", "CO", "CR", "LC", "LO", "MN", "MI", "MB", "PV", "SO", "VA"})
PREZZO_VERO_MIN = 500           # sotto: prezzo simbolico (1 €, "trattativa"), vale come annuncio senza prezzo
SUPERCAR_DA = 70000             # "supercar" = marca di lusso oppure prezzo da 70.000 €; le altre sono "sportive"
# 23/11 pomeriggio (cliente: "TUTTE le sportive", nessun prezzo minimo): sono migliaia. Le schede mostrate sono al
# massimo LIMITE_SUPERCAR + LIMITE_SPORTIVE, le più care di ciascun gruppo (un tetto unico per prezzo avrebbe
# lasciato fuori tutte le sportive). I totali veri sono nella riga di log VETRINA_SUPERCAR.
LIMITE_SUPERCAR = 1000
LIMITE_SPORTIVE = 2000
LIMITE = LIMITE_SUPERCAR + LIMITE_SPORTIVE
LEGGI_X = 2                     # candidate lette per intero per gruppo: fino a LEGGI_X volte il tetto del gruppo
BLOCCO = 500                    # annunci letti per intero (con la descrizione) per volta
CACHE_SECONDI = 1800              # 23/11: la vetrina non sono affari; lettura lunga, non ogni 10 minuti
TEMPO_QUERY = "20s"             # come l'elenco del sito: meglio un errore che il sito fermo
TEMPO_CANDIDATE = "180s"        # 23/11: la lettura delle candidate (una sola query) in produzione supera i 20 s; gira
                                # in un thread a parte (_carica_sotto), mai dentro la richiesta di una pagina
ATTESA_LOCK = 25                # secondi: chi aspetta il caricamento di un altro non tiene occupato un thread per sempre
PAUSA_DOPO_ERRORE = 60          # a memoria vuota e database fermo: non si riprova a ogni richiesta
# Il testo LEASING_RE è scritto per gli annunci dei privati: contiene parole ("finanziamento", "anticipo") che i
# commercianti scrivono in quasi ogni annuncio ("possibilità di finanziamento"). Per i commercianti LEASING_RE vale su
# titolo e versione; nella descrizione vale il testo "solo con finanziamento/leasing" del sito (scovo.FIN_SOLO_RE).
# True = LEASING_RE anche sulla descrizione dei commercianti (ne toglie la gran parte).
LEASING_DESCRIZIONE_COMMERCIANTI = False
FLAG_RATA = frozenset({"rata", "leasing", "leasing_o_rata"})
MOTIVI_FUORI = frozenset({"doppione", "truffa", "prezzo_finto"})
RATA_AI = SR.Regole.jev_rata    # stessa soglia della regola prezzo_a_rata del sito

_SOLO_FIN_RE = re.compile(scovo.FIN_SOLO_RE, re.I)

# 23/11 (revisione): sito_regole.provincia_di conosce i comuni attorno a Milano (BG, CO, LC, LO, MB, MI, PV, VA quasi
# per intero). Solo per la VETRINA (le regole del sito non cambiano) si riconoscono anche la provincia scritta per
# esteso ("Brescia", "Provincia di Mantova"), i comuni di Brescia, Mantova, Sondrio e Cremona che lì mancano e le
# località turistiche, e la regione "Lombardia" quando l'annuncio la dice.
PROVINCE_ESTESE = {"bergamo": "BG", "brescia": "BS", "como": "CO", "cremona": "CR", "lecco": "LC", "lodi": "LO",
                   "mantova": "MN", "milano": "MI", "monza": "MB", "monzabrianza": "MB", "monzaebrianza": "MB",
                   "monzaedellabrianza": "MB", "monzaedellabrianzamb": "MB", "pavia": "PV", "sondrio": "SO", "varese": "VA"}
_COMUNI_IN_PIU_TESTO = {
    "BS": """Brescia, Desenzano del Garda, Desenzano, Salò, Lumezzane, Darfo Boario Terme, Darfo, Boario Terme, Montichiari,
        Rovato, Chiari, Palazzolo sull'Oglio, Palazzolo, Ghedi, Gussago, Concesio, Rezzato, Carpenedolo, Lonato del Garda,
        Lonato, Sarezzo, Mazzano, Travagliato, Orzinuovi, Manerbio, Leno, Bagnolo Mella, Castenedolo, Ospitaletto,
        Gardone Val Trompia, Villa Carcina, Iseo, Erbusco, Coccaglio, Cazzago San Martino, Castel Mella, Botticino, Nave,
        Bedizzole, Calcinato, Castegnato, Roncadelle, Borgosatollo, Flero, Capriolo, Adro, Cologne, Castrezzato, Pontoglio,
        Rudiano, Gavardo, Vobarno, Villanuova sul Clisi, Toscolano Maderno, Gardone Riviera, Sirmione, Limone sul Garda,
        Limone, Manerba del Garda, Padenghe sul Garda, Moniga del Garda, Pozzolengo, Ponte di Legno, Edolo, Breno, Esine,
        Pisogne, Marone, Sale Marasino, Corte Franca, Passirano, Paderno Franciacorta, Rodengo Saiano, Cellatica,
        Collebeato, Bovezzo, Pontevico, Verolanuova, Quinzano d'Oglio, Gottolengo, Pralboino, Gambara, Isorella, Calvisano,
        Montirone, Poncarale, San Zeno Naviglio, Torbole Casaglia, Lograto, Berlingo, Trenzano, Dello, Pavone del Mella,
        Prevalle, Nuvolento, Nuvolera, Paitone, Roè Volciano, Sabbio Chiese, Odolo, Agnosine, Bienno, Pian Camuno, Artogne,
        Piancogno, Capo di Ponte, Cividate Camuno, Malegno, Borno, Polaveno, Marcheno, Bovegno, Tremosine sul Garda,
        Tremosine, Gargnano, Puegnago del Garda, Polpenazze del Garda, Soiano del Lago, San Felice del Benaco, Muscoline,
        Urago d'Oglio, Roccafranca, Comezzano Cizzago, Castelcovati, Monticelli Brusati, Provaglio d'Iseo, Paratico,
        Mairano, Azzano Mella, Capriano del Colle, Offlaga, Bassano Bresciano, Verolavecchia, Borgo San Giacomo,
        Orzivecchi, Pompiano, Barbariga, Remedello, Visano, Acquafredda, Fiesse, Seniga, Milzano, Cigole, Maclodio,
        Brandico, Corzano, Longhena, Villachiara, Temù, Vezza d'Oglio, Angolo Terme, Gianico, Ome, Monticelli d'Oglio""",
    "MN": """Mantova, Castiglione delle Stiviere, Suzzara, Viadana, Porto Mantovano, Curtatone, Borgo Virgilio, Virgilio,
        Castel Goffredo, Goito, Asola, San Giorgio Bigarello, San Giorgio di Mantova, Marmirolo, Roverbella, Gonzaga,
        Pegognaga, Guidizzolo, Volta Mantovana, Castellucchio, Marcaria, Ostiglia, Poggio Rusco, Sermide e Felonica,
        Sermide, Moglia, San Benedetto Po, Quistello, Bozzolo, Sabbioneta, Medole, Cavriana, Monzambano, Ponti sul Mincio,
        Solferino, Castel d'Ario, Roncoferraro, Bagnolo San Vito, Rodigo, Gazoldo degli Ippoliti, Canneto sull'Oglio,
        Casalmoro, Casaloldo, Ceresara, Piubega, Mariana Mantovana, Redondesco, Acquanegra sul Chiese, Gazzuolo,
        Commessaggio, Dosolo, Pomponesco, Motteggiana, Borgo Mantovano, Revere, Sustinente, Serravalle a Po, Villimpenta,
        San Giovanni del Dosso, Schivenoglia, Magnacavallo, Borgocarbonara, Castelbelforte, San Martino dall'Argine,
        Rivarolo Mantovano""",
    "SO": """Sondrio, Morbegno, Tirano, Chiavenna, Livigno, Bormio, Aprica, Madesimo, Chiesa in Valmalenco, Valdidentro,
        Valdisotto, Valfurva, Santa Caterina Valfurva, Sondalo, Grosio, Grosotto, Teglio, Talamona, Cosio Valtellino,
        Berbenno di Valtellina, Montagna in Valtellina, Albosaggia, Ponte in Valtellina, Chiuro, Tresivio, Poggiridenti,
        Ardenno, Delebio, Dubino, Traona, Piantedo, Samolaco, Gordona, Prata Camportaccio, Piuro, Villa di Chiavenna,
        Campodolcino, Novate Mezzola, Villa di Tirano, Bianzone, Mazzo di Valtellina, Tovo di Sant'Agata, Lovero, Sernio,
        Caspoggio, Lanzada, Castione Andevenno, Caiolo, Cedrasco, Fusine, Colorina, Buglio in Monte, Civo, Cercino,
        Mantello, Rogolo, Andalo Valtellino, Gerola Alta, Val Masino""",
    "CR": """Cremona, Crema, Casalmaggiore, Castelleone, Soresina, Pandino, Rivolta d'Adda, Spino d'Adda, Offanengo, Soncino,
        Pizzighettone, Vaiano Cremasco, Bagnolo Cremasco, Castelverde, Sospiro, Vescovato, Piadena Drizzona, Piadena,
        Casalbuttano ed Uniti, Casalbuttano, Pescarolo ed Uniti, Sergnano, Romanengo, Montodine, Ripalta Cremasca, Dovera,
        Agnadello, Palazzo Pignano, Trescore Cremasco, Capralba, Izano, Madignano, Persico Dosimo, Gadesco Pieve Delmona,
        Malagnino, Bonemerse, Stagno Lombardo, Robecco d'Oglio, Ostiano, Gussola, Rivarolo del Re ed Uniti,
        San Giovanni in Croce, Torre de' Picenardi, Isola Dovarese, Annicco, Paderno Ponchielli,
        Grumello Cremonese ed Uniti, Sesto ed Uniti, Spinadesco, Crotta d'Adda, Formigara, San Bassano, Trigolo, Genivolta,
        Casaletto Ceredano, Chieve, Monte Cremasco, Cremosano, Pianengo, Ricengo, Casale Cremasco Vidolasco, Vailate,
        Torlino Vimercati, Pieranica, Quintano""",
    "PV": "Pavia, Vigevano, Voghera, Mortara, Stradella, Broni, Casteggio, Garlasco, Mede, Varzi, Salice Terme",
    "LO": "Lodi, Codogno, Casalpusterlengo, Sant'Angelo Lodigiano, Lodi Vecchio, Zelo Buon Persico",
    "BG": "Bergamo, Treviglio, Seriate, Dalmine, Romano di Lombardia, Clusone, Lovere, Foppolo, Castione della Presolana",
    "CO": "Como, Cantù, Mariano Comense, Erba, Menaggio, Bellagio, Cernobbio, Campione d'Italia",
    "LC": "Lecco, Merate, Calolziocorte, Casatenovo, Valmadrera, Mandello del Lario, Colico, Varenna, Barzio",
    "VA": "Varese, Busto Arsizio, Gallarate, Saronno, Luino, Tradate, Somma Lombardo, Laveno Mombello",
    "MB": "Monza, Lissone, Seregno, Desio, Cesano Maderno, Limbiate, Brugherio, Vimercate, Giussano, Meda",
    "MI": "Milano, Sesto San Giovanni, Cinisello Balsamo, Legnano, Rho, Cologno Monzese, Paderno Dugnano, Rozzano",
}
_comuni_in_piu: dict = {}


def comuni_in_piu() -> dict:
    if not _comuni_in_piu:
        for sigla, testo in _COMUNI_IN_PIU_TESTO.items():
            for nome in testo.split(","):
                k = SR._norm_citta(nome.strip())
                if k:
                    _comuni_in_piu.setdefault(k, sigla)
    return _comuni_in_piu


def provincia_vetrina(provincia, citta, regione=None) -> tuple[str | None, bool]:
    """(sigla della provincia o None, è in Lombardia?). Prima come il sito (sito_regole.provincia_di); poi la provincia
    scritta per esteso, i comuni in più della vetrina e, se non basta, la regione dell'annuncio. Una provincia scritta
    per esteso che non è lombarda ("Verona", "Roma") vale più del nome del comune (omonimi)."""
    p = SR.provincia_di(provincia, citta)
    if p:
        return p, p in LOMBARDIA
    scritta = re.sub(r"^provinciadi", "", _n(provincia))
    if scritta:
        p = PROVINCE_ESTESE.get(scritta) or PROVINCE_ESTESE.get(re.sub(r"(mb|[a-z]{2})$", "", scritta) if len(scritta) > 6 else "")
        if p:
            return p, True
        if _n(regione) != "lombardia":
            return None, False                      # provincia nota e non lombarda
    p = comuni_in_piu().get(SR._norm_citta(citta))
    if p:
        return p, True
    return None, _n(regione) == "lombardia"


def _n(s) -> str:
    """Marca senza accenti, spazi e trattini: "Aston-Martin" → "astonmartin"."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


# allestimenti solo estetici: NON fanno una sportiva, si tolgono dal testo prima di cercare il modello
_FINTI = re.compile(
    r"\bamg (line|pack\w*|paket|styling|sport\w*|look|style|exterieur|interieur|premium|advanced)\b"
    r"|\b(pacchetto|pack|paket|kit|look|cerchi|allestimento|linea|stile) (amg|m|jcw|abarth|gti|rs|st|cupra|n)\b"
    r"|\bm ?(sport\w*|performance|tech\w*|paket|pack\w*|aerodynam\w*)\b"
    r"|\b(s|r|st|gt|n|rs|gr|sport|fr|x|opc) ?line\b|\bgr ?sport\b|\bf ?sport\b|\bgt ?pack\b"
    r"|\b(rs|s) ?[1-8] (look|style|styling)\b"
    r"|\b(john cooper works|jcw) (kit|pack\w*|aero\w*|trim|styling|style|look|exterior|interior)\b")


def _testo(*parti) -> str:
    """Modello e versione in minuscolo, con uno spazio al posto di trattini e punteggiatura ("RS-6", "F-Type"),
    "R.S." scritto "rs" e senza gli allestimenti estetici ("AMG Line", "M Sport", "S line", "R-Line", "ST-Line",
    "GT Line", "N Line", "GR Sport", "F Sport")."""
    s = unicodedata.normalize("NFKD", " ".join(str(p or "") for p in parti)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\br\.\s?s\.?", "rs ", s)
    # "35.000 km" non è un A 35, "S&S" (start e stop) non è una Audi S: via le migliaia, i km e lo start&stop
    s = re.sub(r"\d{1,3}(\.\d{3})+|\d[\d.]* ?km\b|\bs ?& ?s\b|\bstart ?(&|e|and)? ?stop\b", " ", s)
    s = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip() + " "
    return " " + re.sub(r"\s+", " ", _FINTI.sub(" ", s)).strip() + " "


# marche di lusso: sempre dentro e sempre "supercar" (chiave: marca senza spazi; valore: come si scrive nel database)
MARCHE_SEMPRE = {
    "ferrari": ("ferrari",), "lamborghini": ("lamborghini",), "mclaren": ("mclaren",), "porsche": ("porsche",),
    "astonmartin": ("aston-martin", "aston martin", "astonmartin"), "bentley": ("bentley",),
    "rollsroyce": ("rolls-royce", "rolls royce", "rollsroyce"), "maserati": ("maserati",), "bugatti": ("bugatti",),
    "lotus": ("lotus",), "pagani": ("pagani",), "koenigsegg": ("koenigsegg",), "maybach": ("maybach",),
    "alpina": ("alpina",), "alpine": ("alpine",),
    # scritture della stessa marca
    "mercedesmaybach": ("mercedes-maybach", "mercedes maybach"), "bmwalpina": ("bmw-alpina", "bmw alpina"),
}

_AMG_NUMERO = re.compile(r"(?<![\d.])(32|35|36|43|45|53|55|63|65)(?![\d.])|\bgt\b|\bsls\b|\bone\b")


def _c(espr: str):
    """Regola fatta di una sola espressione sul testo di modello e versione."""
    rx = re.compile(espr)
    return lambda t, mod: bool(rx.search(t))


def _mercedes(t, mod):
    if re.search(r"\b(maybach|sls|slk|slc)\b|\b(slk|slc) ?\d{2,3}\b|\bsl\b|\bsl ?\d{2,3}\b|\bg ?(500|55|63|65)\b", t):
        return True
    # AMG vera: la sigla AMG con il numero di un modello AMG (A 35, A 45, C 43, C 63, E 53, G 63...) o AMG GT / SLS
    return bool(re.search(r"\bamg\b", t) and _AMG_NUMERO.search(t))


_BMW = re.compile(r"\balpina\b|\bm ?[234568]\b|\bx ?[3456] ?m\b(?! ?\d)|\bxm\b|\bz ?4\b"
                  r"|\bm ?(135|140|235|240|340|440|550|760|850) ?(li|[id])?\b|\b(i|z) ?8\b"
                  r"|\bx ?[34]\b.*\bm ?40 ?[id]\b|\bx ?2\b.*\bm ?35 ?i\b|\bi ?4\b.*\bm ?50\b|\bix\b.*\bm ?60\b"
                  r"|\bz ?3 ?m\b|\bz ?3\b.*\bm\b|\bm (roadster|coupe)\b|\b1 ?m\b|\b1er m\b")
_AUDI = re.compile(r"\br ?8\b|\brs ?[34567]\b|\brs ?q ?[38]\b|\brs e ?tron\b|\btt ?(s|rs)?\b|\bs ?[1345678]\b(?! ?(porte|p)\b)|\bsq ?[2578]\b|\be ?tron gt\b")
_VW = re.compile(r"\bgolf\b.*\b(gti|gtd|gte|r|r32)\b|\b(gti|gtd|gte|r32)\b.*\bgolf\b|\bpolo\b.*\bgti\b|\bscirocco\b|\bup\b.*\bgti\b"
                 r"|\b(t ?roc|tiguan|arteon)\b.*\br\b")
_ALFA = re.compile(r"\b[48]c\b|\b(brera|spider|gtv|gtam?)\b")
_FORD = re.compile(r"\bfiesta\b.*\bst(200)?\b|\bfocus\b.*\b(st|rs)\b|\bpuma\b.*\bst\b")
_HYUNDAI = re.compile(r"\b(i ?20|i ?30|kona|veloster|ioniq ?5?)\b.*\bn\b|\b(i20|i30)n\b")


def _alfa(t, mod):
    return bool(_ALFA.search(t) or (re.search(r"\b(giulia|stelvio)\b", t) and re.search(r"\b(quadrifoglio|veloce)\b", t))
                or (re.search(r"\b(giulietta|mito)\b", t) and re.search(r"\b(quadrifoglio|qv)\b", t)))


def _ford(t, mod):
    # Mustang: la coupé e la cabrio, non il SUV elettrico Mustang Mach-E. Ford GT: il modello si chiama "GT"
    return bool(_FORD.search(t) or (" mustang " in t and not re.search(r"\bmach ?e\b", t)) or mod == "gt")


def _nissan(t, mod):
    return bool(re.search(r"\b3[57]0 ?z\b|\bgt ?r\b", t) or mod == "z")


def _cupra(t, mod):
    # tutte le VZ, tutte le Ateca; Leon e Formentor anche senza "VZ" se da 300 CV in su; Leon Cupra R
    if re.search(r"\bvz\w{0,2}\b|\bateca\b|\bleon\b.*\bcupra ?r\b", t):
        return True
    if re.search(r"\b(leon|formentor)\b", t):
        cv = [int(x) for x in re.findall(r"\b(\d{3}) ?(?:cv|ps|hp|ch)\b", t)]
        kw = [int(x) for x in re.findall(r"\b(\d{3}) ?kw\b", t)]
        return any(x >= 300 for x in cv) or any(x >= 221 for x in kw)
    return False


def _tutte(t, mod):
    return True


# altre marche: (scritture della marca nel database, parole del controllo veloce in SQL o None = tutta la marca,
# regola sul testo di modello e versione)
ALTRE = {
    "mercedes": (("mercedes", "mercedes-benz", "mercedes benz", "mercedesbenz"), ("amg", "maybach", "sl", "g500", "g55", "g63", "g65"), _mercedes),
    "mercedesamg": (("mercedes-amg", "mercedes amg"), None, _tutte),
    "bmw": (("bmw",), ("m2", "m3", "m4", "m5", "m6", "m8", "x3m", "x4m", "x5m", "x6m", "xm", "z4", "alpina",
                       "m135", "m140", "m235", "m240", "m340", "m440", "m550", "m760", "m850", "i8", "z8",
                       "m40", "m35", "m50", "m60", "z3", "1m", "mcoupe", "mroadster"), lambda t, mod: bool(_BMW.search(t))),
    "audi": (("audi",), ("r8", "rs", "tt", "s1", "s3", "s4", "s5", "s6", "s7", "s8", "sq2", "sq5", "sq7", "sq8", "etrongt"),
             lambda t, mod: bool(_AUDI.search(t))),
    "volkswagen": (("volkswagen", "vw"), ("golf", "gti", "scirocco", "troc", "tiguan", "arteon"), lambda t, mod: bool(_VW.search(t))),
    "mini": (("mini",), ("coopers", "johncooperworks", "jcw"), _c(r"\bcooper s\b|\bjohn cooper works\b|\bjcw\b")),
    "abarth": (("abarth",), None, _tutte),
    "fiat": (("fiat",), ("124", "abarth"), _c(r"\b124\b.*\bspider\b|\bspider\b.*\b124\b|\babarth\b")),
    "alfaromeo": (("alfa-romeo", "alfa romeo", "alfaromeo", "alfa"),
                  ("4c", "8c", "quadrifoglio", "veloce", "brera", "spider", "gtv", "gta", "qv"), _alfa),
    "hyundai": (("hyundai",), ("i20", "i30", "kona", "veloster", "ioniq"), lambda t, mod: bool(_HYUNDAI.search(t))),
    "ford": (("ford",), ("st", "rs", "mustang", "gt"), _ford),
    "renault": (("renault",), ("rs", "trophy", "gt"), _c(r"\bclio\b.*\brs\b|\bmegane\b.*\b(rs|trophy|gt)\b")),
    "toyota": (("toyota",), ("86", "gr", "supra", "celica"),
               _c(r"\bg[rt] ?86\b|\bsupra\b|\bcelica\b|\bgr ?(yaris|corolla)\b|\b(yaris|corolla)\b.*\bgr\b|\bgrmn\b")),
    "mazda": (("mazda",), ("mx5", "rx8"), _c(r"\bmx ?5\b|\brx ?8\b")),
    "subaru": (("subaru",), ("brz", "wrx", "sti"), _c(r"\b(brz|wrx|sti)\b")),
    "peugeot": (("peugeot",), ("gti", "rcz"), _c(r"\b(208|308)\b.*\bgti\b|\brcz\b")),
    "cupra": (("cupra",), ("vz", "leon", "ateca", "formentor"), _cupra),
    "seat": (("seat",), ("cupra",), _c(r"\b(leon|ibiza|ateca)\b.*\bcupra\b|\bcupra\b.*\b(leon|ibiza|ateca)\b")),
    "skoda": (("skoda", "škoda"), ("rs",), _c(r"\b(octavia|fabia|kodiaq|enyaq)\b.*\bv?rs\b")),
    "honda": (("honda",), ("nsx", "typer", "s2000"), _c(r"\bnsx\b|\bs ?2000\b|\bcivic\b.*\btype ?r\b")),
    "nissan": (("nissan",), ("350z", "370z", "gtr", "z"), _nissan),
    "tesla": (("tesla",), ("plaid",), _c(r"\bmodel ?[sx]\b.*\bplaid\b")),
    "lexus": (("lexus",), ("rcf", "isf", "lc", "lfa"), _c(r"\b(rc|is) ?f\b|\blc( ?500h?)?\b|\blfa\b")),
    "jaguar": (("jaguar",), ("ftype", "xk", "svr"), _c(r"\bf ?type\b|\bxk ?(r|rs|8)?\b|\bf ?pace\b.*\bsvr\b")),
    "landrover": (("land-rover", "land rover", "landrover"), ("svr",), _c(r"\bsvr\b")),
    "jeep": (("jeep",), ("srt", "trackhawk"), _c(r"\bsrt\d?\b|\btrackhawk\b")),
    "lancia": (("lancia",), ("integrale",), _c(r"\bdelta\b.*\bintegrale\b")),
    "opel": (("opel",), ("opc", "vxr"), _c(r"\b(opc|vxr)\b")),
    "kia": (("kia",), ("stinger", "ev6"), _c(r"\bstinger\b|\bev ?6\b.*\bgt\b")),
    "mitsubishi": (("mitsubishi",), ("evo",), _c(r"\blancer\b.*\bevo\w*\b|\bevolution\b")),
    "polestar": (("polestar",), None, lambda t, mod: mod == "1" or bool(re.search(r"\bpolestar 1\b|\bperformance\b", t))),
    "dodge": (("dodge",), ("srt", "hellcat", "viper"), _c(r"\b(challenger|charger)\b.*\b(srt\w*|hellcat)\b|\bviper\b")),
    "chevrolet": (("chevrolet",), ("corvette", "camaro"), _c(r"\b(corvette|camaro)\b")),
    "corvette": (("corvette",), None, _tutte),
    "smart": (("smart",), ("roadster",), _c(r"\broadster\b")),
    "morgan": (("morgan",), None, _tutte), "caterham": (("caterham",), None, _tutte), "ktm": (("ktm",), None, _tutte),
    "ariel": (("ariel",), None, _tutte), "donkervoort": (("donkervoort",), None, _tutte),
}
for _alias, _marca in (("alfa", "alfaromeo"), ("mercedesbenz", "mercedes"), ("vw", "volkswagen")):
    ALTRE[_alias] = ALTRE[_marca]


def e_sportiva(make, model, version, price) -> bool:
    """Sportiva o supercar? Marche di lusso sempre; per le altre marche serve il modello o la versione sportiva (non
    il solo allestimento estetico). Nessun prezzo minimo; senza prezzo: mai."""
    try:
        prezzo = int(price or 0)
    except (TypeError, ValueError):
        return False
    if prezzo <= 0:
        return False
    m = _n(make)
    if m in MARCHE_SEMPRE:
        return True
    regola = ALTRE.get(m)
    return bool(regola and regola[2](_testo(model, version), _n(model)))


def e_supercar(make, price) -> bool:
    """Filtro "Supercar / Sportive" della vetrina: marca di lusso oppure prezzo da SUPERCAR_DA."""
    try:
        return _n(make) in MARCHE_SEMPRE or int(price or 0) >= SUPERCAR_DA
    except (TypeError, ValueError):
        return False


def passa_controllo_veloce(make, model, version, title=None) -> bool:
    """Lo stesso controllo largo che la query fa in SQL sulle altre marche (parole attaccate, senza spazi): serve
    solo a leggere meno righe. Deve lasciar passare tutto quello che e_sportiva accetta (provato nei test)."""
    m = _n(make)
    if m in MARCHE_SEMPRE:
        return True
    regola = ALTRE.get(m)
    if not regola:
        return False
    if regola[1] is None:
        return True
    t = re.sub(r"[^a-z0-9]", "", f"{model or ''} {version or ''} {title or ''}".lower())
    return any(p in t for p in regola[1])


def _sql_candidate() -> tuple[str, dict]:
    sempre = sorted({s for v in MARCHE_SEMPRE.values() for s in v})
    testo = ("regexp_replace(lower(COALESCE(l.model,'') || ' ' || COALESCE(l.version_raw,'') || ' ' || "
             "COALESCE(l.title,'')), '[^a-z0-9]', '', 'g')")
    rami, par, intere, visti = [], {"lomb": sorted(LOMBARDIA), "min_vero": PREZZO_VERO_MIN}, set(sempre), set()
    for chiave, (scritture, parole, _f) in ALTRE.items():
        if id(scritture) in visti:
            continue
        visti.add(id(scritture))
        if parole is None:
            intere |= set(scritture)
            continue
        par["m_" + chiave], par["p_" + chiave] = list(scritture), "|".join(parole)
        rami.append(f"(lower(l.make) = ANY(%(m_{chiave})s) AND {testo} ~ %(p_{chiave})s)")
    par["intere"] = sorted(intere)
    # lower(l.make) con status='attivo': indice listings_cmp_lower_idx. Provincia: la sigla dell'annuncio se c'è
    # (altrimenti si ricava dalla città in Python, con sito_regole.provincia_di, come fa il sito)
    sql = ("SELECT l.id, l.make, l.model, l.version_raw, l.title, l.price_eur, l.province, l.city, l.region "
           "FROM listings l WHERE l.status = 'attivo' AND l.price_eur >= %(min_vero)s "
           "AND (lower(l.make) = ANY(%(intere)s) OR " + " OR ".join(rami) + ") "
           "AND (l.province IS NULL OR btrim(l.province) !~ '^[A-Za-z]{2}$' OR upper(btrim(l.province)) = ANY(%(lomb)s))")
    return sql, par


DETTAGLI_SQL = """
SELECT l.id, l.source, l.url, l.title, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox,
       l.power_kw, l.city, l.province, l.region, l.price_eur, l.price_flags, l.seller_type, l.stage, l.stage_reason,
       l.first_seen_at, l.telefono_ok, left(l.description, 3000) AS description,
       (SELECT count(*) FROM listing_photos p WHERE p.listing_id = l.id) AS n_photos
FROM listings l WHERE l.id = ANY(%s) AND l.status = 'attivo'"""

NOMI_SQL = (
    "SELECT l.id, c.nome FROM listings l JOIN commercianti_as24_annunci a ON a.listing_id = l.source_id "
    "JOIN commercianti_as24 c ON c.seller_id = a.seller_id WHERE l.id = ANY(%s) AND c.nome IS NOT NULL",
    "SELECT l.id, d.nome FROM listings l JOIN lead_tutti_ann t ON t.annuncio = l.source_id "
    "JOIN lead_tutti_dealer d ON d.chiave = t.chiave WHERE l.id = ANY(%s) AND d.nome IS NOT NULL",
)

# 23/11 sera: mercato = valutazioni GIÀ salvate dal software, lette e basta (nessun calcolo, nessuna AI).
#   1) valutazioni_policy.riassunto: "M" (il Mercato che il sito mostra negli affari, scovo.item) e "n" (confronti);
#      vale l'ultima valutazione dell'annuncio; fuori le stime di ripiego (confronti allargati: non è un mercato vero)
#   2) supercar_eu: m (mediana dei confronti europei) e n, con la freschezza che usa già il sito (SITO_SQL: 2 giorni)
MERCATO_N_MIN = 5               # sotto 5 confronti il mercato non si mostra
MERCATO_GIORNI = 7              # valutazione più vecchia: non si mostra
MERCATO_GIORNI_EU = 2           # supercar_eu: come pipeline/supercar_eu.SITO_SQL
MERCATO_SQL = (
    # revisione 23/11: vale solo l'ULTIMA valutazione e solo se il software non l'ha giudicata inaffidabile
    # (dati_insufficienti: prezzo troppo sotto la mediana, confronti dispersi...): filtro fuori dal DISTINCT ON
    ("SELECT x.listing_id, x.m, x.n, x.ripiego, x.quando FROM (SELECT DISTINCT ON (vp.listing_id) vp.listing_id, "
     "vp.riassunto->>'M' AS m, vp.riassunto->>'n' AS n, "
     "COALESCE(vp.riassunto->>'stima_ripiego', CASE WHEN vp.riassunto->>'M_fonte' = 'ripiego' THEN 'true' END) AS ripiego, "
     "vp.categoria, vp.computed_at AS quando FROM valutazioni_policy vp "
     "WHERE vp.listing_id = ANY(%s) ORDER BY vp.listing_id, vp.computed_at DESC) x "
     "WHERE x.categoria IS DISTINCT FROM 'dati_insufficienti'", MERCATO_GIORNI),
    ("SELECT s.listing_id, s.m, s.n, NULL AS ripiego, s.computed_at AS quando FROM supercar_eu s "
     "WHERE s.listing_id = ANY(%s) AND s.categoria IN ('opportunita', 'non_conveniente')", MERCATO_GIORNI_EU),
)


def mercato_valido(m, n, quando, *, ripiego=None, giorni: int = MERCATO_GIORNI, ora: float | None = None) -> tuple[int, int] | None:
    """(mercato in euro, confronti) se la valutazione salvata si può mostrare, altrimenti None. Funzione pura: almeno
    MERCATO_N_MIN confronti, valutazione di al massimo `giorni` giorni, non una stima di ripiego. Senza data: no."""
    try:
        m, n = int(round(float(m))), int(float(n))
        if isinstance(quando, str):                 # data scritta come testo ISO
            quando = datetime.fromisoformat(quando.replace("Z", "+00:00"))
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        eta = (ora or time.time()) - quando.timestamp()
    except Exception:
        return None
    if str(ripiego or "").strip().lower() not in ("", "false", "0", "null", "none"):
        return None
    if m <= 0 or n < MERCATO_N_MIN or eta > giorni * 86400:
        return None
    return m, n


RATA_SQL = ("SELECT d.listing_id, (d.risposta->'prezzo_rata'->>'noul')::float AS rata FROM decisione_ai d "
            "WHERE d.listing_id = ANY(%s)")


def versione_di(r: dict) -> str:
    """Versione per riconoscere il modello: quella dell'annuncio; se manca, il titolo."""
    return str(r.get("version_raw") or "").strip() or str(r.get("title") or "")


def _giorni(first_seen, ora: float) -> int | None:
    try:
        return max(0, int((ora - first_seen.timestamp()) // 86400))
    except Exception:
        return None


def motivo_fuori(r: dict, *, scarti=frozenset(), nomi=None, nomi_comm=None, nome_salone=None, rata=None) -> str | None:
    """Perché un annuncio NON va in vetrina (None = va bene). r: riga di DETTAGLI_SQL."""
    prezzo = int(r.get("price_eur") or 0)
    if prezzo < PREZZO_VERO_MIN:
        return "senza_prezzo"
    prov, lombarda = provincia_vetrina(r.get("province"), r.get("city"), r.get("region"))
    if prov in SR.ROMA or _n(r.get("province")) in ("roma", "latina", "frosinone", "viterbo", "rieti"):
        return "roma"
    if not lombarda:
        return "fuori_lombardia"
    if not e_sportiva(r.get("make"), r.get("model"), versione_di(r), prezzo):
        return "non_sportiva"
    comm = r.get("seller_type") == "commerciante"
    testo = f"{r.get('title') or ''} {r.get('version_raw') or ''} {r.get('description') or ''}"
    # commercianti esclusi (Loconte, Officine Mulino sempre; gli altri solo se il venditore è un commerciante)
    if nomi and nomi.search(testo):
        return "commerciante_escluso"
    if nomi_comm and ((comm and nomi_comm.search(testo)) or (nome_salone and nomi_comm.search(nome_salone))):
        return "commerciante_escluso"
    motivo = scovo.motivo_stadio(r.get("stage_reason"))
    if r.get("id") in scarti or (r.get("stage") == "scartato" and (
            motivo.startswith("analisi_manuale") or "tolta dal cliente" in str(r.get("stage_reason") or ""))):
        return "scarto_cliente"
    if motivo in MOTIVI_FUORI:
        return motivo
    # a rata / leasing: il prezzo dell'annuncio non è il prezzo dell'auto
    if set(r.get("price_flags") or []) & FLAG_RATA:
        return "rata_leasing"
    testa = f"{r.get('title') or ''} {r.get('version_raw') or ''}"
    if LEASING_RE.search(testo if (not comm or LEASING_DESCRIZIONE_COMMERCIANTI) else testa) or _SOLO_FIN_RE.search(testo):
        return "rata_leasing"
    if float(rata or 0) >= RATA_AI:
        return "prezzo_a_rata"
    return None


def _senza_numeri(testo) -> str:
    """Testo senza numeri di telefono e senza il segnaposto "[numero col bottone]" (qui non c'è nessun bottone)."""
    t = scovo.hide_phones(str(testo or "")).replace("[numero col bottone]", " ")
    return re.sub(r"\s+", " ", t).strip(" -·,")


def scheda(r: dict, nome_salone: str | None = None, ora: float | None = None, mercato: tuple | None = None) -> dict:
    """Scheda della vetrina: dati dell'annuncio e, se c'è una valutazione salvata valida (mercato_valido), il mercato
    con il numero di confronti. Niente margine, guadagno o stima; mai un numero di telefono."""
    comm = r.get("seller_type") == "commerciante"
    n_ph = int(r.get("n_photos") or 0)
    testo = f"{r.get('title') or ''} {r.get('description') or ''}"
    return {
        "id": r["id"], "marca": scovo.make_name(r.get("make")) or "Altro",
        "modello": scovo.model_name(r.get("model")) or None,
        "versione": _senza_numeri(scovo.version_short(r.get("version_raw"), r.get("model")))[:80] or None,
        "nome": _senza_numeri(scovo.nome(r)) or "Auto", "anno": r.get("year"), "km": r.get("mileage_km"),
        "carb": scovo.FUEL_NAMES.get(r.get("fuel") or "", r.get("fuel")), "cambio": r.get("gearbox") or None,
        "prezzo": int(r["price_eur"]),
        "citta": r.get("city") or None,
        "provincia": provincia_vetrina(r.get("province"), r.get("city"), r.get("region"))[0],
        "supercar": e_supercar(r.get("make"), r.get("price_eur")),
        "venditore": "commerciante" if comm else "privato",
        "salone": ((_senza_numeri(nome_salone)[:60] or None) if comm and nome_salone else None),
        "ha_telefono": bool(r.get("telefono_ok") or scovo.RE_MOBILE.search(testo) or scovo.RE_LANDLINE.search(testo)),
        "giorni": _giorni(r.get("first_seen_at"), ora or time.time()),
        "fonte": scovo.SOURCE_NAMES.get(r.get("source"), r.get("source")),
        "foto": foto.url_for(r["id"], 0) if n_ph else None, "n_foto": min(n_ph, foto.MAX_PER_AUTO),
        "url": r.get("url"),
        # 23/11 sera: solo da una valutazione salvata (None = "Mercato: non stimato"); la differenza col prezzo la fa
        # la pagina, qui non c'è nessun margine
        "mercato": int(mercato[0]) if mercato else None, "confronti": int(mercato[1]) if mercato else None,
    }


def _chiave_doppione(it: dict):
    if it.get("km") is None or not it.get("anno"):
        return None
    return (_n(it.get("marca")), _n(it.get("modello")), it.get("anno"), it.get("km"), it.get("prezzo"))


def _ordine(it: dict):
    return (-int(it.get("prezzo") or 0), it.get("id") or 0)


def senza_doppioni(items: list[dict]) -> tuple[list[dict], int]:
    """La stessa auto pubblicata due volte (stessi marca, modello, anno, km e prezzo): resta quella con più foto."""
    visti, out, tolti = {}, [], 0
    for it in sorted(items, key=_ordine):
        k = _chiave_doppione(it)
        if k is None or k not in visti:
            if k is not None:
                visti[k] = len(out)
            out.append(it)
            continue
        tolti += 1
        if it["n_foto"] > out[visti[k]]["n_foto"]:
            out[visti[k]] = it
    return out, tolti


def scegli(righe: list[dict], *, scarti=frozenset(), nomi_tutti: str = "", nomi_sempre: str = "", saloni: dict | None = None,
           rate: dict | None = None, ora: float | None = None, mercati: dict | None = None) -> tuple[list[dict], dict]:
    """(schede in ordine di prezzo decrescente, {motivo: quante tolte}). Funzione pura, senza tetto (vedi taglia).
    nomi_tutti / nomi_sempre: espressioni di sito_regole.nomi_esclusi_re (commercianti=True / False)."""
    nomi = re.compile(nomi_sempre, re.I) if nomi_sempre else None
    nomi_comm = re.compile(nomi_tutti, re.I) if nomi_tutti else None
    saloni, rate, mercati, tolte, buone = saloni or {}, rate or {}, mercati or {}, {}, []
    for r in righe:
        nome = saloni.get(r.get("id"))
        m = motivo_fuori(r, scarti=scarti, nomi=nomi, nomi_comm=nomi_comm, nome_salone=nome, rata=rate.get(r.get("id")))
        if m:
            tolte[m] = tolte.get(m, 0) + 1
        else:
            buone.append(scheda(r, nome, ora, mercati.get(r.get("id"))))
    out, doppi = senza_doppioni(buone)
    if doppi:
        tolte["doppione"] = tolte.get("doppione", 0) + doppi
    return out, tolte


def taglia(items: list[dict], limite_supercar: int = LIMITE_SUPERCAR, limite_sportive: int = LIMITE_SPORTIVE) -> list[dict]:
    """Le più care di ciascun gruppo (supercar / sportive), in un solo elenco per prezzo decrescente."""
    ordinate = sorted(items, key=_ordine)
    sc = [it for it in ordinate if it.get("supercar")][:max(0, int(limite_supercar))]
    sp = [it for it in ordinate if not it.get("supercar")][:max(0, int(limite_sportive))]
    return sorted(sc + sp, key=_ordine)


def riepilogo(items: list[dict]) -> dict:
    """Numeri per la riga di log VETRINA_SUPERCAR: solo conteggi, nessun dato personale."""
    per_marca: dict = {}
    for it in items:
        per_marca[it["marca"]] = per_marca.get(it["marca"], 0) + 1
    sc = sum(1 for it in items if it.get("supercar"))
    comm = sum(1 for it in items if it.get("venditore") == "commerciante")
    return {"totale": len(items), "supercar": sc, "sportive": len(items) - sc, "privati": len(items) - comm,
            "commercianti": comm, "con_mercato": sum(1 for it in items if it.get("mercato")), "per_marca": dict(sorted(per_marca.items(), key=lambda x: (-x[1], x[0])))}


def _tempo_massimo(conn) -> None:
    """Tempo massimo per query. SET LOCAL: vale fino a commit/rollback, per questo si ripete a ogni blocco."""
    conn.execute("SET LOCAL statement_timeout = '%s'" % TEMPO_QUERY)


def _leggi_blocco(conn, ids: list, scarti) -> tuple[list[dict], dict]:
    _tempo_massimo(conn)
    righe = [dict(r) for r in conn.execute(DETTAGLI_SQL, (ids,)).fetchall()]
    saloni, rate = {}, {}
    for q in NOMI_SQL:
        try:
            for r in conn.execute(q, (ids,)).fetchall():
                saloni.setdefault(r["id"], r["nome"])
        except Exception as e:
            conn.rollback()
            log.warning("VETRINA_SUPERCAR nomi dei saloni non letti: %s", str(e)[:150])
    try:
        for r in conn.execute(RATA_SQL, (ids,)).fetchall():
            if r.get("rata") is not None:
                rate[r["listing_id"]] = max(float(r["rata"]), rate.get(r["listing_id"], 0.0))
    except Exception as e:
        conn.rollback()
        log.warning("VETRINA_SUPERCAR prezzo a rata non letto: %s", str(e)[:150])
    return scegli(righe, scarti=scarti, nomi_tutti=SR.nomi_esclusi_re(commercianti=True),
                  nomi_sempre=SR.nomi_esclusi_re(commercianti=False), saloni=saloni, rate=rate,
                  mercati=_leggi_mercati(conn, ids))


def _leggi_mercati(conn, ids: list) -> dict:
    """{id: (mercato, confronti)} dalle valutazioni salvate (MERCATO_SQL), solo quelle valide. Se una tabella manca o
    la lettura fallisce l'auto resta senza mercato ("non stimato"): la vetrina non si ferma."""
    mercati: dict = {}
    for q, giorni in MERCATO_SQL:
        try:
            _tempo_massimo(conn)                    # dopo un rollback il SET LOCAL di prima non vale più
            for r in conn.execute(q, (ids,)).fetchall():
                v = mercato_valido(r.get("m"), r.get("n"), r.get("quando"), ripiego=r.get("ripiego"), giorni=giorni)
                if v and r["listing_id"] not in mercati:       # prima la valutazione del sito, poi supercar_eu
                    mercati[r["listing_id"]] = v
        except Exception as e:
            conn.rollback()
            log.warning("VETRINA_SUPERCAR mercato non letto: %s", str(e)[:150])
    return mercati


def carica(conn, *, scarti=frozenset()) -> tuple[list[dict], dict]:
    """(schede, numeri). Due letture: 1) candidate con poche colonne (stato, marca, prezzo, provincia: filtri semplici
    in SQL), scelte in Python con e_sportiva e la provincia; 2) per gruppo (supercar / sportive) le più care lette per
    intero a blocchi, con descrizione, nome del salone e prezzo a rata, fino al tetto del gruppo."""
    sql, par = _sql_candidate()
    # 23/11 pom.: in produzione la lettura delle candidate supera i 20 s (annunci attivi di tutta Italia delle ~30
    # marche) -> tempo più lungo solo per questa query, che ora gira in un thread a parte e non tiene ferma la pagina
    conn.execute("SET LOCAL statement_timeout = '%s'" % TEMPO_CANDIDATE)
    gruppi = {True: [], False: []}
    for r in conn.execute(sql, par).fetchall():
        if provincia_vetrina(r.get("province"), r.get("city"), r.get("region"))[1] and e_sportiva(
                r.get("make"), r.get("model"), versione_di(r), r.get("price_eur")):
            gruppi[e_supercar(r.get("make"), r.get("price_eur"))].append((-int(r["price_eur"]), r["id"]))
    numeri = {"candidate": len(gruppi[True]) + len(gruppi[False]), "candidate_supercar": len(gruppi[True]),
              "candidate_sportive": len(gruppi[False]), "non_lette": 0, "tolte": {}}
    tutte = []
    for sc, tetto in ((True, LIMITE_SUPERCAR), (False, LIMITE_SPORTIVE)):
        cand = sorted(gruppi[sc])
        da_leggere, buone = cand[:tetto * LEGGI_X], []
        numeri["non_lette"] += len(cand) - len(da_leggere)
        for k in range(0, len(da_leggere), BLOCCO):
            if len(buone) >= tetto:
                numeri["non_lette"] += len(da_leggere) - k
                break
            items, tolte = _leggi_blocco(conn, [i for _p, i in da_leggere[k:k + BLOCCO]], scarti)
            buone += items
            for m, n in tolte.items():
                numeri["tolte"][m] = numeri["tolte"].get(m, 0) + n
        tutte += buone
    tutte, doppi = senza_doppioni(tutte)
    if doppi:
        numeri["tolte"]["doppione"] = numeri["tolte"].get("doppione", 0) + doppi
    items = taglia(tutte)
    numeri["oltre_il_tetto"] = len(tutte) - len(items)
    numeri["riepilogo"] = riepilogo(tutte)          # tutte quelle lette e buone, anche oltre il tetto mostrato
    return items, numeri


_cache: dict = {"t": 0.0, "items": None, "totale": 0}
_errore = [0.0]                 # quando è fallito l'ultimo caricamento a memoria vuota
_cache_lock = threading.Lock()


def svuota_cache() -> None:
    _cache.update(t=0.0, items=None, totale=0)
    _errore[0] = 0.0


def totale_in_memoria() -> int | None:
    """Quante auto ha la vetrina, se l'elenco è già in memoria (None = memoria vuota). Non legge il database."""
    return int(_cache["totale"] or 0) if _cache["items"] is not None else None


def da_ricaricare() -> bool:
    """Memoria vuota o vecchia, e non subito dopo un errore."""
    if _cache["items"] is not None and time.time() - _cache["t"] < CACHE_SECONDI:
        return False
    return not (_cache["items"] is None and time.time() - _errore[0] < PAUSA_DOPO_ERRORE)


class InCaricamento(RuntimeError):
    """La vetrina si sta preparando in un thread a parte: la pagina dice di riprovare tra poco."""


def elenco(connetti, scarti_ids=None) -> tuple[list[dict], int]:
    """(schede, quante sportive e supercar ci sono in tutto, anche oltre il tetto), dalla memoria. 23/11 pom.: la
    lettura gira SEMPRE in un thread a parte (in produzione dura più dei 20 s di una richiesta): se la memoria è
    vecchia si risponde con l'elenco di prima e intanto si ricarica; se è vuota -> InCaricamento."""
    if _cache["items"] is not None and time.time() - _cache["t"] < CACHE_SECONDI:
        return _cache["items"], _cache["totale"]
    if not (_cache["items"] is None and time.time() - _errore[0] < PAUSA_DOPO_ERRORE):
        avvia(connetti, scarti_ids)
    if _cache["items"] is not None:
        return _cache["items"], _cache["totale"]
    raise InCaricamento("vetrina in caricamento")


def avvia(connetti, scarti_ids=None) -> bool:
    """Fa partire il caricamento in un thread a parte, se non ne sta già girando uno. True se è partito."""
    if not _cache_lock.acquire(blocking=False):
        return False
    try:
        threading.Thread(target=_carica_sotto, args=(connetti, scarti_ids), name="vetrina_supercar",
                         daemon=True).start()
        return True
    except Exception:
        _cache_lock.release()
        raise


def _carica_sotto(connetti, scarti_ids) -> None:
    """Nel thread: il lock è già preso da avvia() e si rilascia qui."""
    try:
        if da_ricaricare():                             # un altro thread può averla appena ricaricata
            _carica(connetti, scarti_ids)
    except Exception:
        pass                                            # già scritto nel log da _carica
    finally:
        _cache_lock.release()


def _carica(connetti, scarti_ids=None) -> tuple[list[dict], int]:
    """Una lettura completa (chiamata con _cache_lock preso)."""
    if True:
        t0 = time.time()
        try:
            scarti = frozenset(scarti_ids() if callable(scarti_ids) else (scarti_ids or ()))
            with connetti() as conn:
                items, numeri = carica(conn, scarti=scarti)
        except Exception as e:
            log.warning("VETRINA_SUPERCAR non caricata: %s", str(e)[:200])
            if _cache["items"] is None:
                _errore[0] = time.time()
                raise
            _cache["t"] = time.time() - CACHE_SECONDI + 900       # si riprova tra 15 minuti (DB già lento)
            return _cache["items"], _cache["totale"]
        totale = len(items) + int(numeri.get("oltre_il_tetto") or 0) + int(numeri.get("non_lette") or 0)
        _cache.update(t=time.time(), items=items, totale=totale)
        # solo conteggi: nessun nome, telefono o indirizzo
        log.warning("VETRINA_SUPERCAR %s", json.dumps(numeri.pop("riepilogo", None) or riepilogo(items), ensure_ascii=False))
        log.warning("VETRINA_SUPERCAR_LETTURA %s", json.dumps(dict(numeri, mostrate=len(items),
                                                                   secondi=round(time.time() - t0, 1)), ensure_ascii=False))
        return _cache["items"], _cache["totale"]
