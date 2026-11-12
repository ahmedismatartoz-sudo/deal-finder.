"""Allestimento (trim) normalizzato di un annuncio: classe e livello 0–3 (12/11/2026, cliente: "margini più precisi").

Prima i confronti distinguevano solo "sportivo" e "normale" (policy_v2.allestimento): una Panda Pop si confrontava con
le Panda Lounge e Cross, una Golf Trendline con le Highline. Qui, da version_raw + titolo (+ descrizione solo con le
parole "allestimento …"/"versione …"), si riconosce l'allestimento con regex deterministiche per marca, con la
gerarchia reale di ogni marca:

    livello 0 = base (Pop, Access, Trendline, Life, Active Toyota, Sport Jeep…)
    livello 1 = medio (Easy, Comfortline, Business, Advantage, Zen, Feel…)
    livello 2 = alto (Lounge, Highline, Luxury, Intens, Allure, Titanium, Limited Jeep…)
    livello 3 = top / sportivo (Cross, R-Line, S line, M Sport, AMG Line, GT Line, ST-Line, Tekna, FR, Veloce…)

Più parole riconosciute: vale il livello più alto (es. "Business S line" = 3). Niente riconosciuto: None (l'annuncio
non dice l'allestimento; nei confronti è "sconosciuto", né uguale né diverso).

    livello(auto) -> dict | None     {"classe", "nome", "livello", "origine"}
    allestimento(auto) -> dict | None  stessa cosa più "descrizione" (per la scheda del sito)

`auto` è una riga di listings (make, model, version_raw, title, description) oppure un dict con "marca"/"nome".
"""
from __future__ import annotations

import re
import unicodedata

NOMI_LIVELLO = {0: "base", 1: "medio", 2: "alto", 3: "top / sportivo"}

# Parole che NON sono allestimenti e che le regex devono ignorare: cambio (S tronic, Steptronic), start&stop (S&S),
# carrozzerie (Sportback, Sportwagon, Sports Tourer, Active Tourer), trazione (4motion…).
_RUMORE = re.compile(
    r"(?i)\bs[\s-]?tronic\b|\bsteptronic\b|\bs\s?&\s?s\b|\bstart\s?&\s?stop\b|\bsport\s?back\b|\bspb\b|"
    r"\bsports?\s?wagon\b|\bsports?\s?tourer\b|\bsport\s?brake\b|\bgrand\s?sport\b|\bactive\s?tourer\b|"
    r"\bgran\s?tourer\b|\bsport\s?utility\b|\btouring\s?sports?\b|\bsports?\b(?=\s+(?:tourer|wagon|back))|"
    r"\bdiscovery\s+sport\b|\brange\s+rover\s+sport\b|\bclubsport\b|\beasy\s?power\b")   # Easypower = impianto GPL Fiat

# (regex, classe, nome mostrato, livello). L'ordine non conta: vale il livello più alto trovato.
_T = tuple[str, str, str, int]

_GENERICI: list[_T] = [
    (r"m[\s-]?sport|msport", "m_sport", "M Sport", 3),
    (r"amg[\s-]?line", "amg_line", "AMG Line", 3),
    (r"s[\s-]?line", "s_line", "S line", 3),
    (r"r[\s-]?line", "r_line", "R-Line", 3),
    (r"rs[\s-]?line", "rs_line", "RS Line", 3),
    (r"gt[\s-]?line", "gt_line", "GT Line", 3),
    (r"st[\s-]?line", "st_line", "ST-Line", 3),
    (r"n[\s-]?line", "n_line", "N Line", 3),
    (r"gs[\s-]?line", "gs_line", "GS Line", 3),
    (r"r[\s-]?design", "r_design", "R-Design", 3),
    (r"r[\s-]?dynamic", "r_dynamic", "R-Dynamic", 3),
    (r"black\s+edition", "black_edition", "Black Edition", 3),
    (r"business", "business", "Business", 1),
    (r"executive", "executive", "Executive", 2),
    (r"premium", "premium", "Premium", 2),
    (r"luxury", "luxury", "Luxury", 2),
    (r"elegance", "elegance", "Elegance", 2),
    (r"exclusive", "exclusive", "Exclusive", 2),
]

# Gerarchie per marca (chiave = marca normalizzata, vedi _marca). Le voci della marca valgono sopra le generiche
# con la stessa classe (es. "sport" per Jeep è la base, per Fiat è il top).
_MARCHE: dict[str, list[_T]] = {
    "fiat": [
        (r"pop(?!\s?star)", "pop", "Pop", 0), (r"base", "base", "Base", 0), (r"actual", "actual", "Actual", 0),
        (r"active", "active", "Active", 0), (r"young", "young", "Young", 0), (r"classic", "classic", "Classic", 0),
        (r"easy", "easy", "Easy", 1), (r"pop\s?star", "pop_star", "Pop Star", 1),
        (r"cult", "cult", "Cult", 1), (r"club", "club", "Club", 1), (r"connect", "connect", "Connect", 1),
        (r"dynamic", "dynamic", "Dynamic", 1), (r"mirror", "mirror", "Mirror", 1), (r"urban", "urban", "Urban", 1),
        (r"city\s?life", "city_life", "City Life", 1), (r"street", "street", "Street", 1),
        (r"lounge", "lounge", "Lounge", 2), (r"emotion", "emotion", "Emotion", 2), (r"dolce\s?vita", "dolcevita", "Dolcevita", 2),
        (r"gq", "gq", "GQ", 2), (r"collezione", "collezione", "Collezione", 2), (r"anniversar\w*|50th", "anniversario", "Anniversario", 2),
        (r"rock\s?star|(?<!pop\s)(?<!pop)star", "star", "Star", 2), (r"city\s?cross", "city_cross", "City Cross", 2),
        (r"trekking", "trekking", "Trekking", 2), (r"red", "red", "(RED)", 2), (r"mopar", "mopar", "Mopar", 2),
        (r"opening\s+edition|lusso", "lusso", "Lusso", 2), (r"la\s?prima", "la_prima", "La Prima", 3),
        (r"sport", "sport", "Sport", 3), (r"s[\s-]?design", "s_design", "S-Design", 3), (r"(?<!city\s)(?<!city)cross(?!\s?over)", "cross", "Cross", 3),
        (r"riva|by\s+gucci|by\s+diesel", "serie_speciale", "Serie speciale", 3),
    ],
    "abarth": [(r"custom", "custom", "Custom", 1), (r"turismo", "turismo", "Turismo", 2),
               (r"competizione|esseesse|ss\b|scorpioneoro|70th|monster|rivale", "competizione", "Competizione", 3)],
    "volkswagen": [
        (r"trendline|take\s?up!?|base", "trendline", "Trendline", 0), (r"united|move\s?up!?", "move", "Move up!", 1),
        (r"comfortline|comfort", "comfortline", "Comfortline", 1), (r"life", "life", "Life", 1),
        (r"advanced?", "advanced", "Advanced", 1), (r"business", "business", "Business", 1),
        (r"style", "style", "Style", 2), (r"highline|high\.?(?=\s|$)|high\s?up!?", "highline", "Highline", 2),
        (r"elegance", "elegance", "Elegance", 2), (r"sport(?:\s+edition)?", "sport", "Sport", 2),
        (r"executive", "executive", "Executive", 2), (r"join", "join", "Join", 1), (r"sound", "sound", "Sound", 1),
        (r"r[\s-]?line", "r_line", "R-Line", 3), (r"gti|gtd|gte|\bgt\b", "gti", "GTI/GTD", 3), (r"\br\b(?=\s|$)", "r", "R", 3),
        (r"cross\s?up!?|cross", "cross", "Cross", 3),
    ],
    "audi": [
        (r"attraction|young|base", "attraction", "Attraction", 0), (r"ambiente|ambition", "ambition", "Ambition", 1),
        (r"business(?!\s+(?:plus|advanced|sport))", "business", "Business", 1), (r"advanced", "advanced", "Advanced", 1),
        (r"business\s+plus|design|metal\s?plus|admired", "design", "Design", 2), (r"sport", "sport", "Sport", 2),
        (r"s[\s-]?line", "s_line", "S line", 3), (r"black\s+edition|identity\s+black|edition\s+one|competition",
                                                   "black_edition", "Black edition", 3),
    ],
    "bmw": [
        (r"attiva|base", "attiva", "Attiva", 0), (r"advantage|eletta", "advantage", "Advantage", 1),
        (r"business", "business", "Business", 1), (r"urban(?:\s?line)?", "urban", "Urban", 1),
        (r"sport(?:\s?line)?", "sport", "Sport Line", 2), (r"luxury(?:\s?line)?|futura|x[\s-]?line|modern(?:\s?line)?",
                                                           "luxury", "Luxury / xLine", 2),
        (r"m[\s-]?sport|msport|m\s+performance", "m_sport", "M Sport", 3),
    ],
    "mercedes": [
        (r"classic|base", "classic", "Classic", 0), (r"business(?:\s+extra)?|style|executive", "executive", "Executive / Business", 1),
        (r"sport(?:\s?plus)?|avantgarde|elegance|progressive", "sport", "Sport", 2),
        (r"premium(?:\s?plus)?|amg[\s-]?line|amg|night\s+edition|exclusive", "premium", "Premium / AMG Line", 3),
    ],
    "toyota": [
        (r"active|now|base|x[\s-]?play|x[\s-]?cool|cool|sol|luna", "active", "Active", 0),
        (r"business|trend|x[\s-]?black|comfort", "trend", "Trend / Business", 1),
        (r"style|lounge|executive|more\s+business|x[\s-]?clusiv|y20|adventure", "lounge", "Lounge / Style", 2),
        (r"gr[\s-]?sport|\bgr\b|premiere|premium|circuit", "gr_sport", "GR Sport / Premiere", 3),
    ],
    "renault": [
        (r"life|wave|authentique|access|base|basic", "life", "Life", 0), (r"zen|equilibre|business|live|dynamique|evolution",
                                                                        "zen", "Zen", 1),
        (r"intens|limited|techno|bose|sport\s*&\s*sound|sport\s+edition\d?|moschino|yahoo!?|baccara|initiale",
         "intens", "Intens / Limited", 2),
        (r"initiale\s+paris|rs\s*line|r\.?s\.?(?:\s|$)|rs\d+|trophy|iconic|esprit\s+alpine|gt\s?line|\bgt\b", "rs_line", "R.S. Line / Initiale", 3),
    ],
    "peugeot": [
        (r"access|like|xs|urban|base", "access", "Access", 0), (r"active|business|style|tendance|xr", "active", "Active", 1),
        (r"allure|premium|feline|xsi|tecno", "allure", "Allure", 2),
        (r"gt[\s-]?line|\bgt\b|gt\s+pack|gti", "gt", "GT / GT Line", 3),
    ],
    "citroen": [
        (r"live|seduction|attraction|base|you", "live", "Live", 0), (r"feel|business|plus", "feel", "Feel", 1),
        (r"shine|exclusive|c[\s-]?series|origins|max|elegance", "shine", "Shine", 2),
        (r"shine\s+pack|max\b|sport\s?chic|racing", "top", "Shine Pack / Max", 3),
    ],
    "ds": [(r"chic|be\s+chic|so\s+chic|business", "chic", "Chic", 1), (r"performance\s+line|rivoli|grand\s+chic|opera",
                                                                        "performance_line", "Performance Line", 3),
           (r"sport\s?chic", "sport_chic", "Sport Chic", 2)],
    "opel": [
        (r"selection|essentia|base|club|edition(?!\s*\w)", "selection", "Selection", 0),
        (r"enjoy|advance|design|edition|business|elective", "enjoy", "Enjoy / Advance", 1),
        (r"elegance|cosmo|innovation|b[\s-]?color|glam|rocks|sport", "elegance", "Elegance / Cosmo", 2),
        (r"ultimate|gs[\s-]?line|\bgs\b|opc|black\s+edition", "ultimate", "Ultimate / GS Line", 3),
    ],
    "ford": [
        (r"ambiente|base|plus(?!\s?\w)|ikon", "ambiente", "Ambiente", 0), (r"trend|business|connected|zetec|cool\s*&?\s*connect",
                                                                            "trend", "Trend / Business", 1),
        (r"titanium|ghia|active|limited|sport|vignale\b(?!x)", "titanium", "Titanium", 2),
        (r"st[\s-]?line|\bst\b|\brs\b|vignale|titanium\s+x|raptor|wildtrak|gt\b", "st_line", "ST-Line / Vignale", 3),
    ],
    "jeep": [
        (r"sport", "sport", "Sport", 0), (r"longitude|business|altitude", "longitude", "Longitude", 1),
        (r"limited|night\s+eagle|upland|sahara|1st\s+edition|opening\s+edition", "limited", "Limited", 2),
        (r"trailhawk|overland|summit|rubicon|srt|high\s+altitude", "trailhawk", "Trailhawk / Overland", 3),
    ],
    "kia": [
        (r"urban|cool|active|lx|base|city", "urban", "Urban", 0), (r"business|style|energy|evolution|feel",
                                                                   "business", "Business / Style", 1),
        (r"high\s?tech|class|premium", "high_tech", "High Tech", 2),
        (r"gt[\s-]?line|\bgt\b", "gt_line", "GT Line", 3),
    ],
    "hyundai": [
        (r"classic|base|go!?|login|pure|access", "classic", "Classic", 0), (r"comfort|business|style|xtech|x[\s-]?possible",
                                                                             "comfort", "Comfort / Xtech", 1),
        (r"x?prime|xline|x[\s-]?class|exellence|excellence|sound\s+edition|tech", "xline", "Xline / Prime", 2),
        (r"n[\s-]?line|\bn\b(?=\s|$)", "n_line", "N Line", 3),
    ],
    "dacia": [
        (r"access|base|ambiance|essential", "access", "Access / Essential", 0),
        (r"comfort|expression|lauréate|laureate|journey|streetway", "comfort", "Comfort / Expression", 1),
        (r"prestige|stepway|techroad|extreme|serie\s+speciale", "prestige", "Prestige / Extreme", 2),
        (r"journey\s+up|extreme\s+up|explorer", "top", "Journey / Extreme Up", 3),
    ],
    "nissan": [
        (r"visia|base|xe", "visia", "Visia", 0), (r"acenta|business|n[\s-]?tec|360", "acenta", "Acenta", 1),
        (r"n[\s-]?connecta|connect\s?edition|n[\s-]?way|n[\s-]?motion", "n_connecta", "N-Connecta", 2),
        (r"tekna|n[\s-]?design|nismo|platinum", "tekna", "Tekna / N-Design", 3),
    ],
    "skoda": [
        (r"active|base|easy", "active", "Active", 0), (r"ambition|ambiente|executive", "ambition", "Ambition", 1),
        (r"style|elegance|design\s+edition|scout", "style", "Style", 2),
        (r"sportline|monte\s?carlo|l\s?&\s?k|laurin|\brs\b", "sportline", "Sportline / L&K", 3),
    ],
    "seat": [
        (r"reference|entry|base", "reference", "Reference", 0), (r"style|business|move!?|copa", "style", "Style", 1),
        (r"xcellence|excellence|xperience|plus", "xcellence", "Xcellence", 2),
        (r"\bfr\b|cupra|black\s+edition", "fr", "FR", 3),
    ],
    "cupra": [(r"base|business", "base", "Base", 1), (r"\bvz\w*|tribe|impulse|adrenaline", "vz", "VZ", 3)],
    "volvo": [
        (r"kinetic|base|core", "kinetic", "Kinetic / Core", 0), (r"momentum|business(?:\s+plus)?|plus", "momentum", "Momentum", 1),
        (r"inscription|summum|ocean\s+race", "inscription", "Inscription", 2),
        (r"r[\s-]?design|ultimate|polestar", "r_design", "R-Design / Ultimate", 3),
    ],
    "mini": [
        (r"essential|classic|base|salt", "classic", "Classic", 0), (r"pepper|business|baker\s+street|boost", "pepper", "Pepper / Business", 1),
        (r"chili|hype|favoured|seven|camden|resolute", "chili", "Chili / Hype", 2),
        (r"john\s+cooper\s+works|jcw|yours|iconic|untamed", "jcw", "JCW / Yours", 3),
    ],
    "lancia": [
        (r"silver|base|elefantino|ecochic", "silver", "Silver", 0), (r"mya|elle|monogram|momo|unyca|black\s*(?:and|&)\s*red|blu",
                                                                           "gold_lite", "Elefantino Blu / Mya", 1),
        (r"gold|oro|argento|platino|platinum|alberta\s+ferretti|diva", "gold", "Gold", 2),
        (r"platinum|cassina|s\s+momodesign|hf", "platinum", "Platinum / HF", 3),
    ],
    "alfa-romeo": [
        (r"progression|impression|base|super(?!\s?sport)", "progression", "Progression / Super", 0),
        (r"distinctive|business|sprint", "distinctive", "Distinctive / Sprint", 1),
        (r"exclusive|ti\b|executive|tributo", "exclusive", "Exclusive / Ti", 2),
        (r"veloce|quadrifoglio|qv\b|competizione|sportiva", "veloce", "Veloce", 3),
    ],
    "suzuki": [
        (r"base|easy|\bgl\b|comfort", "base", "Base / Easy", 0), (r"cool|i[\s-]?cool|\bb[\s-]?cool\b|\bgl\+",
                                                                  "cool", "Cool", 1),
        (r"top|i[\s-]?top|\bglx\b|\bb[\s-]?top\b|yoru", "top", "Top", 2), (r"starview|sport|4\s?grip\s+top", "sport", "Sport / Starview", 3),
    ],
    "mazda": [
        (r"prime[\s-]?line|base|essence", "prime_line", "Prime-line", 0), (r"evolve|center[\s-]?line|business|executive",
                                                                            "evolve", "Evolve / Center-line", 1),
        (r"exceed|exclusive[\s-]?line|skycruise|sport", "exceed", "Exceed / Exclusive-line", 2),
        (r"homura|signature|takumi|zenith", "homura", "Homura / Signature", 3),
    ],
    "land-rover": [
        (r"\bs\b(?=\s|$)|pure|base", "s", "S / Pure", 0), (r"\bse\b|business", "se", "SE", 1),
        (r"\bhse\b|dynamic", "hse", "HSE", 2), (r"autobiography|first\s+edition|svr|hse\s+dynamic|r[\s-]?dynamic\s+hse",
                                                "autobiography", "Autobiography", 3),
        (r"r[\s-]?dynamic", "r_dynamic", "R-Dynamic", 2),
    ],
}
_ALIAS = {"vw": "volkswagen", "mercedes-benz": "mercedes", "mercedesbenz": "mercedes", "alfa": "alfa-romeo",
          "alfaromeo": "alfa-romeo", "landrover": "land-rover", "land": "land-rover", "citroën": "citroen",
          "škoda": "skoda", "ds-automobiles": "ds"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.lower()


def _marca(make) -> str:
    m = _norm(make).strip().replace(" ", "-")
    return _ALIAS.get(m, _ALIAS.get(m.split("-")[0], m))


def _compila(voci: list[_T]):
    return [(re.compile(r"(?i)(?<![\w&-])(?:" + rx + r")(?![\w&])"), cl, nome, lv) for rx, cl, nome, lv in voci]


_RX_MARCHE = {k: _compila(v) for k, v in _MARCHE.items()}
_RX_GENERICI = _compila(_GENERICI)
_RX_DESCRIZIONE = re.compile(r"(?i)\b(?:allestimento|versione)\s*:?\s+([\w&!\-\. ]{2,30})")


def _cerca(testo: str, marca: str) -> dict | None:
    testo = _RUMORE.sub(" ", testo)
    trovati = []
    voci = _RX_MARCHE.get(marca)
    if voci:
        trovati = [(lv, cl, nome) for rx, cl, nome, lv in voci if rx.search(testo)]
    if not trovati:
        trovati = [(lv, cl, nome) for rx, cl, nome, lv in _RX_GENERICI if rx.search(testo)]
    if not trovati:
        return None
    lv, cl, nome = max(trovati, key=lambda x: x[0])
    return {"classe": cl, "nome": nome, "livello": lv}


def livello(auto: dict) -> dict | None:
    """{"classe", "nome", "livello", "origine"} oppure None se l'annuncio non dice l'allestimento."""
    if not isinstance(auto, dict):
        return None
    marca = _marca(auto.get("make") or auto.get("marca") or "")
    if not marca and auto.get("nome"):
        marca = _marca(str(auto["nome"]).split()[0])
    testo = _norm(" ".join(str(auto.get(k) or "") for k in ("version_raw", "title", "nome")))
    # marca e modello non contano (es. "Yaris Cross", "T-Cross", "Discovery Sport" sono modelli, non allestimenti)
    for k in ("make", "marca", "model", "modello"):
        v = _norm(auto.get(k) or "").replace("-", " ").strip()
        if v:
            testo = re.sub(r"(?<![\w-])" + re.escape(v).replace("\\ ", "[\\s-]+") + r"(?![\w])", " ", testo)
    r = _cerca(testo, marca)
    if r:
        return dict(r, origine="titolo")
    desc = str(auto.get("description") or "")[:3000]
    for m in _RX_DESCRIZIONE.finditer(desc):
        r = _cerca(_norm(m.group(1)), marca)
        if r:
            return dict(r, origine="descrizione")
    return None


def allestimento(auto: dict) -> dict | None:
    """Per la scheda del sito: allestimento riconosciuto con livello e descrizione del livello, oppure None."""
    r = livello(auto)
    if not r:
        return None
    return dict(r, descrizione=NOMI_LIVELLO[r["livello"]])


def livello_num(auto: dict, cache: dict | None = None) -> int | None:
    """Solo il livello (0–3) o None; con cache per id (nei confronti lo stesso annuncio si legge molte volte)."""
    if cache is not None and auto.get("id") is not None:
        k = auto["id"]
        if k not in cache:
            r = livello(auto)
            cache[k] = r["livello"] if r else None
        return cache[k]
    r = livello(auto)
    return r["livello"] if r else None
