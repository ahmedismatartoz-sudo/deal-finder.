"""12/11 (cliente: "sul sito l'auto deve essere più completa"): dati della scheda tecnica di un'auto, con chiavi
normalizzate in italiano, letti da quello che le fonti già danno e che prima si buttava.

Fonti:
  - AutoScout24, pagina di ricerca: `vehicle` e `vehicleDetails` (consumo, CO2, proprietari quando ci sono);
  - AutoScout24, pagina dell'annuncio (`listingDetails`): quasi tutto (cilindrata, carrozzeria, porte, posti, colore,
    proprietari, tagliandi, classe Euro, consumo, trazione, optional divisi per gruppo);
  - Subito (ricerca e pagina): le "features" dell'annuncio (cilindrata, colore, porte, posti, classe emissioni,
    optional...), tutte, non solo la prima voce.

Mai dati del venditore: si leggono solo i campi del veicolo e ogni testo che contiene un numero di telefono si scarta.
Tutto difensivo: i nomi dei campi delle fonti non sono documentati, quindi si cercano per sinonimi e un campo che non
si riconosce si ignora (mai un errore verso chi chiama).

Chiavi: allestimento, versione, cambio, marce, cilindrata, potenza_kw, potenza_cv, carrozzeria, porte, posti, colore,
proprietari, tagliandi, classe_euro, consumo, co2, trazione, immatricolazione, garanzia, non_fumatori, optional (elenco),
optional_gruppi ({gruppo: [voci]}).
"""
from __future__ import annotations

import re
from typing import Any

CHIAVI = ("allestimento", "versione", "cambio", "marce", "cilindrata", "potenza_kw", "potenza_cv", "carrozzeria",
          "porte", "posti", "colore", "proprietari", "tagliandi", "classe_euro", "consumo", "co2", "trazione",
          "immatricolazione", "garanzia", "non_fumatori", "optional", "optional_gruppi")

# Nel salvataggio dalla RICERCA non si ripetono i dati che hanno già una colonna (version_raw, gearbox, power_kw) o che
# si ricavano da quelle (allestimento): la colonna resta piccola anche su centinaia di migliaia di annunci.
GIA_IN_COLONNA = ("versione", "allestimento", "cambio", "potenza_kw", "potenza_cv", "immatricolazione")
MAX_OPTIONAL = 120

# ------------------------------------------------------------------------------------------------ telefoni e testi
_RE_TEL = re.compile(r"(?:\+|00)?(?:39[\s.\-]?)?3\d{2}[\s.\-/]?\d{3}[\s.\-/]?\d{3,4}|\b0\d{1,3}[\s.\-/]?\d{5,8}\b")


def _pulito(v: Any, n: int = 60) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip(" -·,;:")
    if not s or _RE_TEL.search(s) or "@" in s or s.lower() in ("-", "n.d.", "nd", "null", "none", "altro", "0"):
        return None
    return s[:n]


def _testo(v: Any) -> Any:
    """Valore leggibile da una voce che può essere un numero, un testo o un oggetto {formatted, value, raw...}."""
    if isinstance(v, dict):
        for k in ("formatted", "value", "label", "text", "name", "data", "displayValue", "raw", "key"):
            x = v.get(k)
            if x not in (None, "", [], {}):
                return _testo(x)
        return None
    if isinstance(v, list):
        xs = [_testo(x) for x in v]
        xs = [x for x in xs if x not in (None, "")]
        return xs[0] if len(xs) == 1 else (", ".join(str(x) for x in xs) if xs else None)
    return v


def _intero(v: Any, lo: int, hi: int) -> int | None:
    v = _testo(v)
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        n = int(v)
    else:
        m = re.search(r"\d[\d.]*", str(v))
        if not m:
            return None
        try:
            n = int(m.group(0).replace(".", ""))
        except ValueError:
            return None
    return n if lo <= n <= hi else None


def _cap(s: str | None) -> str | None:
    return s[:1].upper() + s[1:] if s else s


# ------------------------------------------------------------------------------------------------ normalizzatori
def norm_cambio(v: Any) -> str | None:
    s = str(_testo(v) or "").lower().strip()
    if not s:
        return None
    if re.search(r"semi", s):
        return "Semiautomatico"
    if re.search(r"sequenz", s):
        return "Sequenziale"
    if re.search(r"autom|\baut\b|\bat\b|dsg|cvt|tronic|edc|eat\d|steptronic|dct|pdk|\ba$", s):
        return "Automatico"
    if re.search(r"manual|\bman\b|\bm\b|\bmt\b", s):
        return "Manuale"
    return _cap(_pulito(s, 30))


def norm_cilindrata(v: Any) -> int | None:
    t = _testo(v)
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        x = float(t)
    else:
        s = str(t or "").replace(" ", "")
        m = re.search(r"(\d{1,2}[.,]\d)(?:l|litri)?$", s, re.I) if not re.search(r"\d\.\d{3}", s) else None
        if m:
            x = float(m.group(1).replace(",", "."))
        else:
            n = _intero(s, 1, 100000)
            if n is None:
                return None
            x = float(n)
    if x < 10:                       # litri (1.6) -> cm³
        x = x * 1000
    n = int(round(x))
    return n if 500 <= n <= 8500 else None


def norm_potenza(v: Any) -> tuple[int | None, int | None]:
    """(kW, CV) da "85 kW (116 CV)", "116 CV", 85..."""
    s = str(_testo(v) or "")
    kw = re.search(r"(\d{2,4})\s*kw", s, re.I)
    cv = re.search(r"(\d{2,4})\s*(?:cv|hp|ps)\b", s, re.I)
    k = int(kw.group(1)) if kw else None
    c = int(cv.group(1)) if cv else None
    if k is None and c is None:
        n = _intero(v, 20, 1200)
        return (n, None) if n else (None, None)
    return k, c


def completa_potenza(d: dict) -> dict:
    k, c = d.get("potenza_kw"), d.get("potenza_cv")
    if k and not c:
        d["potenza_cv"] = int(round(k * 1.35962))
    elif c and not k:
        d["potenza_kw"] = int(round(c / 1.35962))
    for x in ("potenza_kw", "potenza_cv"):
        if d.get(x) is not None and not 15 <= d[x] <= 1600:
            d.pop(x)
    return d


def norm_euro(v: Any) -> str | None:
    s = str(_testo(v) or "")
    m = re.search(r"euro\s*([1-7])\s*([a-d]?)(?:[\s-]*(temp|isc[- ]?fcm))?", s, re.I)
    if m:
        out = "Euro " + m.group(1) + m.group(2).lower()
        if m.group(3):
            out += "-" + m.group(3).upper().replace(" ", "-")
        return out
    m = re.fullmatch(r"\s*([1-7])\s*", s)
    return "Euro " + m.group(1) if m else None


def norm_consumo(v: Any) -> str | None:
    t = _testo(v)
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        return f"{float(t):.1f}".replace(".", ",") + " l/100 km" if 1 <= float(t) <= 30 else None
    s = _pulito(t, 40)
    if not s:
        return None
    if re.search(r"kwh", s, re.I):
        return s
    m = re.search(r"(\d{1,2}[.,]\d{1,2}|\d{1,2})", s)
    if not m:
        return None
    x = float(m.group(1).replace(",", "."))
    if not 1 <= x <= 30:
        return None
    return m.group(1).replace(".", ",") + " l/100 km" + (" (misto)" if re.search(r"comb|misto", s, re.I) else "")


def norm_co2(v: Any) -> str | None:
    n = _intero(v, 0, 600)
    return f"{n} g/km" if n else None


def norm_tagliandi(v: Any) -> str | None:
    t = _testo(v)
    if isinstance(t, bool):
        return "Sì, documentati" if t else None
    s = _pulito(t, 60)
    if not s:
        return None
    if re.fullmatch(r"(?i)s[iì]|true|yes|1", s):
        return "Sì, documentati"
    if re.fullmatch(r"(?i)no|false", s):
        return None
    return _cap(s)


def norm_trazione(v: Any) -> str | None:
    s = str(_testo(v) or "").lower()
    if not s:
        return None
    if re.search(r"4x4|integrale|all.?wheel|awd|4wd|quattro|4motion|xdrive|4matic", s):
        return "Integrale"
    if re.search(r"anterior|front|fwd", s):
        return "Anteriore"
    if re.search(r"posterior|rear|rwd", s):
        return "Posteriore"
    return _cap(_pulito(s, 30))


def norm_si(v: Any) -> bool | None:
    t = _testo(v)
    if isinstance(t, bool):
        return t
    s = str(t or "").strip().lower()
    return True if s in ("si", "sì", "true", "yes", "1") else None


CARROZZERIE = {"sedan": "Berlina", "limousine": "Berlina", "hatchback": "Berlina", "compact": "Utilitaria",
               "stationwagon": "Station wagon", "station wagon": "Station wagon", "estate": "Station wagon",
               "suv": "SUV/Fuoristrada", "offroad": "SUV/Fuoristrada", "coupe": "Coupé", "convertible": "Cabrio",
               "cabrio": "Cabrio", "van": "Monovolume", "minivan": "Monovolume", "transporter": "Furgone",
               "pickup": "Pick-up"}


def norm_carrozzeria(v: Any) -> str | None:
    s = _pulito(_testo(v), 40)
    if not s:
        return None
    return CARROZZERIE.get(s.lower(), _cap(s))


# ------------------------------------------------------------------------------------------------ allestimento
# Parole tecniche che NON sono l'allestimento (motore, carburante, cambio, porte, sigle del motore)
_TECNICHE = re.compile(
    r"(?ix)\b(?:\d[.,]\d\d?|\d{2,4}\s*(?:cv|hp|ps|kw|cc|cm3|cm³)|\d{2,4}(?=\s|$)(?![a-z])|[2-5]\s*p(?:orte)?\.?|\d\s*porte|"
    r"tdi|tsi|tfsi|tgi|e-?tsi|fsi|gti?|tdci|tddi|ecoboost|ecoblue|duratorq|multijet|mjt|jtdm?|jtd|jtdm-?2|t-?jet|fire|"
    r"hdi|e-?hdi|bluehdi|puretech|vti|thp|dci|tce|sce|blue\s?dci|crdi|cdi|cgi|gdi|t-?gdi|mpi|vvt-?i|d-?4d|i-?dtec|"
    r"i-?vtec|skyactiv(?:-?[gdx])?|bluetec|d|i|td|tds|turbo|biturbo|diesel|benzina|gpl|metano|bifuel|bi-?fuel|hybrid|"
    r"ibrida|ibrido|mild|full|plug-?in|phev|mhev|hev|ev|elettrica|electric|e-?power|ecojet|natural\s?power|"
    r"dsg|s[\s-]?tronic|tiptronic|steptronic|multitronic|powershift|edc|eat\d|cvt|dct|aut\.?|automatic[oa]?|"
    r"automatica|auto|manuale|man\.?|mt|at|[56]m|[5-9]at|4x2|4x4|awd|q4|quattro|4motion|4matic|[xs]drive\w*|\d{1,2}v|energy|"
    r"start\s?&?\s?stop|s\s?&\s?s|s/s|ss|bluemotion(?:\s+technology)?|bmt|technology|tech|ecoflex|eco|"
    r"euro\s?\d\w*|my\d{2}|my|e\d|"
    r"[a-z]{0,2}\d{2,3}[a-z]?)\b")
# Allestimenti noti (prima si cercano questi: sono la risposta più sicura)
_NOTI = [
    "AMG Line", "M Sport", "Msport", "M-Sport", "S line", "S-line", "R-Line", "R line", "GT Line", "GT-Line", "ST-Line", "ST Line", "N Line",
    "N-Line", "FR", "Xcellence", "Xperience", "Style", "Business", "Executive", "Highline", "Comfortline", "Trendline",
    "Sport", "Sportline", "Lounge", "Pop", "Easy", "Lusso", "Titanium", "Vignale", "Allure", "Active", "Feel", "Shine",
    "Intens", "Zen", "Life", "Icon", "Iconic", "Elegance", "Ambition", "Ambiente", "Advanced", "Avantgarde", "Premium",
    "Luxury", "Exclusive", "Edition", "Plus", "Urban", "City", "Cross", "Connect", "Design", "Dynamic", "Tekna",
    "Acenta", "N-Connecta", "Visia", "Platinum", "Lounge", "Distinctive", "Super", "Veloce", "Ti", "Competizione",
    "Cool", "Mirror", "Dolcevita", "Club", "Launch Edition", "Opening Edition", "Limited", "Trend", "Comfort", "Base",
    "Classic", "Elite", "Innovation", "Cosmo", "Enjoy", "Edition", "Ultimate", "GS Line", "Rebel", "Wild", "Lounge",
    "S", "SE", "SEL", "SX", "LX", "EX", "Plus", "Pure", "Prestige", "Inscription", "Momentum", "R-Design", "Kinetic",
    "Summum", "Sline", "Black Edition", "Red Edition", "Hipster", "Rock Star", "Sportback", "Avant",
]
_RE_NOTI = re.compile(r"(?i)(?<![\w-])(" + "|".join(sorted({re.escape(x) for x in _NOTI}, key=len, reverse=True))
                      + r")(?![\w-])")
_NON_ALLESTIMENTO = {"sportback", "avant", "s", "edition"}


def _allestimento_semplice(versione: str | None, titolo: str | None = None) -> str | None:
    for testo in (versione, titolo):
        if not testo or re.fullmatch(r"(?i)\s*altr[oa]\s*(allestimento|versione)?\s*", testo):
            continue
        t = re.sub(r"[()\[\],;|/]+", " ", str(testo))
        noti = [m.group(1) for m in _RE_NOTI.finditer(t) if m.group(1).lower() not in _NON_ALLESTIMENTO]
        if noti:
            visti, out = set(), []
            for x in noti:
                if x.lower() not in visti:
                    visti.add(x.lower())
                    out.append(x)
            return " ".join(out[:3])[:40]
        if testo is titolo:
            break                    # dal titolo (marca e modello) si prende solo un allestimento noto
        resto = _TECNICHE.sub(" ", t)
        parole = [p for p in re.split(r"\s+", resto) if p and re.search(r"[a-zA-Z]{2,}", p) and len(p) <= 18]
        if parole:
            return " ".join(parole[-3:])[:40]
    return None


def allestimento_testo(versione: str | None, titolo: str | None = None) -> str | None:
    """Allestimento leggibile (es. "Business", "AMG Line") dalla versione o dal titolo. Interfaccia minima: se c'è il
    modulo dealfinder.pricing.allestimento (ramo margini-precisi) con la stessa funzione, si usa quello."""
    try:
        from ..pricing import allestimento as _a           # type: ignore[attr-defined]
        f = getattr(_a, "allestimento_testo", None)
        if f is not None:
            r = f(versione, titolo)
            if r is None or isinstance(r, str):
                return _pulito(r, 40) if r else None
    except Exception:
        pass
    return _pulito(_allestimento_semplice(versione, titolo), 40)


# ------------------------------------------------------------------------------------------------ optional
GRUPPI = (("comfort", "Comfort"), ("sicurezza", "Sicurezza"), ("multimedia", "Multimedia"), ("esterni", "Esterni"),
          ("altro", "Altro"))
_GRUPPI_FONTE = {"comfortandconvenience": "comfort", "comfort": "comfort", "safetyandsecurity": "sicurezza",
                 "safety": "sicurezza", "sicurezza": "sicurezza", "entertainmentandmedia": "multimedia",
                 "entertainment": "multimedia", "multimedia": "multimedia", "extras": "esterni", "extra": "esterni",
                 "esterni": "esterni"}
_CLASSI = [
    (re.compile(r"(?i)sensore\s+(?:di\s+)?(?:pioggia|luce|crepuscol)"), "comfort"),
    (re.compile(r"(?i)radio|navig|bluetooth|usb|carplay|android\s*auto|mp3|\bcd\b|dab|schermo|display|touch|audio|hi-?fi|"
                r"vivavoce|wi-?fi|\baux\b|infotainment|soundsystem|altoparlant|lettore|tv\b|apple|cockpit|connect|"
                r"strumentazione\s+digitale|ricarica\s+(?:wireless|induzione)|smartphone"), "multimedia"),
    (re.compile(r"(?i)\babs\b|\besp\b|\basr\b|\bebd\b|airbag|isofix|frenata|frenat|assistent|corsia|lane|angolo\s+cieco|"
                r"blind|sensori?\s+(?:di\s+)?parcheggio|park|telecamera|camera|retrocamera|antifurto|immobilizer|"
                r"allarme|controllo\s+(?:della\s+)?(?:trazione|stabilit|pressione)|pressione\s+pneumatici|"
                r"riconoscimento|stanchezza|collision|emergenza|\be-?call\b|hill|partenza\s+in\s+salita|sicurezza|"
                r"cruise\s+control\s+adattiv|acc\b|limitatore|abbaglianti\s+automatici|night"), "sicurezza"),
    (re.compile(r"(?i)cerch|lega|gancio|traino|tetto|barre|portapacchi|vetri\s+oscurat|privacy|fari|faro|\bled\b|xeno|"
                r"bi-?xeno|fendinebbia|luci|spoiler|minigonne|pneumatic|ruotino|ruota\s+di\s+scorta|vernice|"
                r"metallizzat|specchietti\s+(?:in\s+tinta|ripiegabili)|kit\s+estetico|paraurti|portellone|tettuccio"), "esterni"),
    (re.compile(r"(?i)clima|aria\s+condizionata|sedil|volante|cruise|alzacristall|chiusura\s+centralizzata|keyless|"
                r"specchiett|servosterzo|start|bracciolo|head-?up|pelle|alcantara|riscald|massagg|ventilat|"
                r"climatizzat|bagagliaio|tendin|portabicchier|illuminazione\s+interna|ambient|parabrezza\s+riscald|"
                r"computer\s+di\s+bordo|sospensioni|regolazione|lombare|memoria|accensione|chiave|interni"), "comfort"),
]


def gruppo_optional(voce: str, gruppo_fonte: str | None = None) -> str:
    for rx, g in _CLASSI:
        if rx.search(voce):
            return g
    if gruppo_fonte:
        g = _GRUPPI_FONTE.get(re.sub(r"[^a-z]", "", gruppo_fonte.lower()))
        if g:
            return g
    return "altro"


def _voci(v: Any) -> list[str]:
    out = []
    if isinstance(v, dict) and not any(k in v for k in ("formatted", "value", "label", "text", "name", "data", "id")):
        for x in v.values():
            out += _voci(x)
        return out
    if isinstance(v, list):
        for x in v:
            out += _voci(x)
        return out
    t = _pulito(_testo(v), 70)
    if t and not re.fullmatch(r"\d+", t):
        out.append(_cap(t))
    return out


def raggruppa_optional(voci: list[str], gruppi_fonte: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """{gruppo: [voci]} nei 5 gruppi del sito, senza doppioni (si confronta senza maiuscole)."""
    origine = {}
    for g, xs in (gruppi_fonte or {}).items():
        for x in xs:
            origine.setdefault(x.lower(), g)
    out: dict[str, list[str]] = {}
    visti = set()
    for x in voci:
        if x.lower() in visti:
            continue
        visti.add(x.lower())
        out.setdefault(gruppo_optional(x, origine.get(x.lower())), []).append(x)
    return {g: out[g] for g, _ in GRUPPI if g in out}


def _con_optional(d: dict, voci: list[str], gruppi_fonte: dict[str, list[str]] | None = None) -> dict:
    voci = [x for x in voci if x][:MAX_OPTIONAL]
    if voci:
        visti, uniche = set(), []
        for x in voci:
            if x.lower() not in visti:
                visti.add(x.lower())
                uniche.append(x)
        d["optional"] = uniche
        if gruppi_fonte:
            d["optional_gruppi"] = {g: xs for g, xs in gruppi_fonte.items() if xs}
    return d


# ------------------------------------------------------------------------------------------------ JSON (AutoScout24)
# nome del campo (minuscolo, solo lettere e cifre) -> chiave nostra
ALIAS = {
    "modelversioninput": "versione", "versione": "versione",
    "transmission": "cambio", "transmissiontype": "cambio", "gearbox": "cambio", "gearingtype": "cambio",
    "gears": "marce", "numberofgears": "marce",
    "displacement": "cilindrata", "displacementinccm": "cilindrata", "cubiccapacity": "cilindrata",
    "enginecapacity": "cilindrata", "enginedisplacement": "cilindrata",
    "powerinkw": "potenza_kw", "rawpowerinkw": "potenza_kw", "powerkw": "potenza_kw",
    "powerinhp": "potenza_cv", "rawpowerinhp": "potenza_cv", "powerhp": "potenza_cv", "power": "potenza",
    "bodytype": "carrozzeria", "bodystyle": "carrozzeria", "body": "carrozzeria",
    "numberofdoors": "porte", "doors": "porte", "doorcount": "porte",
    "numberofseats": "posti", "seats": "posti", "seatcount": "posti",
    "bodycolororiginal": "colore_nome", "manufacturercolorname": "colore_nome", "bodycolor": "colore", "color": "colore",
    "colour": "colore", "exteriorcolor": "colore", "painttype": "vernice",
    "numberofpreviousowners": "proprietari", "previousowners": "proprietari", "previousownercount": "proprietari",
    "hasfullservicehistory": "tagliandi", "fullservicehistory": "tagliandi", "servicehistory": "tagliandi",
    "servicebook": "tagliandi",
    "environmenteudirective": "classe_euro", "emissionclass": "classe_euro", "eurostandard": "classe_euro",
    "euronorm": "classe_euro", "emissionstandard": "classe_euro", "pollutionclass": "classe_euro",
    "fuelconsumptioncombined": "consumo", "fuelconsumption": "consumo", "consumptioncombined": "consumo",
    "combinedconsumption": "consumo", "consumption": "consumo",
    "co2emissioningramperkm": "co2", "co2emission": "co2", "co2emissions": "co2", "co2": "co2",
    "drivetrain": "trazione", "drivetype": "trazione", "drivetraintype": "trazione",
    "firstregistrationdate": "immatricolazione", "firstregistration": "immatricolazione",
    "warranty": "garanzia", "warrantyinmonths": "garanzia_mesi", "warrantyduration": "garanzia",
    "nonsmoking": "non_fumatori", "nonsmokingvehicle": "non_fumatori",
    "equipment": "equipment", "equipments": "equipment", "features": "equipment_lista",
}
# rami che non si visitano mai: dati del venditore, posizione, statistiche, prezzi, annunci simili
_SALTA = re.compile(r"(?i)seller|contact|phone|dealer|user|owner(?!s)|location|address|tracking|similar|recommend|"
                    r"price|financ|leasing|image|media|seo|breadcrumb|description")


def _metti(d: dict, chiave: str, v: Any) -> None:
    if v in (None, "", [], {}):
        return
    if chiave == "versione":
        x = _pulito(_testo(v), 120)
        if x:
            d.setdefault("versione", x)
    elif chiave == "cambio":
        x = norm_cambio(v)
        if x:
            d.setdefault("cambio", x)
    elif chiave == "marce":
        x = _intero(v, 3, 10)
        if x:
            d.setdefault("marce", x)
    elif chiave == "cilindrata":
        x = norm_cilindrata(v)
        if x:
            d.setdefault("cilindrata", x)
    elif chiave in ("potenza_kw", "potenza_cv"):
        x = _intero(v, 15, 1600)
        if x:
            d.setdefault(chiave, x)
    elif chiave == "potenza":
        k, c = norm_potenza(v)
        if k:
            d.setdefault("potenza_kw", k)
        if c:
            d.setdefault("potenza_cv", c)
    elif chiave == "carrozzeria":
        x = norm_carrozzeria(v)
        if x:
            d.setdefault("carrozzeria", x)
    elif chiave == "porte":
        x = _intero(v, 2, 7)
        if x:
            d.setdefault("porte", x)
    elif chiave == "posti":
        x = _intero(v, 1, 9)
        if x:
            d.setdefault("posti", x)
    elif chiave in ("colore", "colore_nome", "vernice"):
        x = _pulito(_testo(v), 40)
        if x:
            d.setdefault("_" + chiave, _cap(x))
    elif chiave == "proprietari":
        x = _intero(v, 1, 20)
        if x:
            d.setdefault("proprietari", x)
    elif chiave == "tagliandi":
        x = norm_tagliandi(v)
        if x:
            d.setdefault("tagliandi", x)
    elif chiave == "classe_euro":
        x = norm_euro(v)
        if x:
            d.setdefault("classe_euro", x)
    elif chiave == "consumo":
        x = norm_consumo(v)
        if x:
            d.setdefault("consumo", x)
    elif chiave == "co2":
        x = norm_co2(v)
        if x:
            d.setdefault("co2", x)
    elif chiave == "trazione":
        x = norm_trazione(v)
        if x:
            d.setdefault("trazione", x)
    elif chiave == "immatricolazione":
        s = str(_testo(v) or "")
        m = re.search(r"(\d{1,2})[-/.](\d{4})|(\d{4})-(\d{2})", s)
        if m:
            d.setdefault("immatricolazione", f"{int(m.group(1) or m.group(4)):02d}/{m.group(2) or m.group(3)}")
    elif chiave == "garanzia":
        t = _testo(v)
        if isinstance(t, bool):
            if t:
                d.setdefault("garanzia", "Sì")
        else:
            x = _pulito(t, 50)
            if x and not re.fullmatch(r"(?i)no|false", x):
                d.setdefault("garanzia", _cap(x))
    elif chiave == "garanzia_mesi":
        x = _intero(v, 1, 120)
        if x:
            d.setdefault("garanzia", f"{x} mesi")
    elif chiave == "non_fumatori":
        if norm_si(v):
            d["non_fumatori"] = True


def _equipment(d: dict, v: Any) -> None:
    gruppi: dict[str, list[str]] = {}
    voci: list[str] = []
    if isinstance(v, dict):
        for g, xs in v.items():
            vv = _voci(xs)
            if vv:
                gruppi[str(g)] = vv
                voci += vv
    else:
        voci = _voci(v)
    if voci:
        _con_optional(d, d.get("optional", []) + voci, {**d.get("optional_gruppi", {}), **gruppi} if gruppi else None)


def da_json(obj: Any, d: dict | None = None, livello: int = 0) -> dict:
    """Cerca nel JSON (pagina o risultato di ricerca di AutoScout24) i campi del veicolo, per sinonimi."""
    d = {} if d is None else d
    if livello > 6:
        return d
    if isinstance(obj, dict):
        for k, v in obj.items():
            kk = re.sub(r"[^a-z0-9]", "", str(k).lower())
            if _SALTA.search(str(k)):
                continue
            c = ALIAS.get(kk)
            if c == "equipment":
                _equipment(d, v)
            elif c == "equipment_lista":
                if isinstance(v, list) and v and all(isinstance(x, (str, dict)) for x in v):
                    _equipment(d, v)
            elif c:
                _metti(d, c, v)
                if isinstance(v, (dict, list)) and _testo(v) is None:
                    da_json(v, d, livello + 1)
            elif isinstance(v, (dict, list)):
                da_json(v, d, livello + 1)
    elif isinstance(obj, list):
        for x in obj[:200]:
            if isinstance(x, (dict, list)):
                da_json(x, d, livello + 1)
    return d


def _chiudi(d: dict) -> dict:
    """Colore (nome del costruttore se c'è, più "metallizzato") e potenza completa; via le chiavi di servizio."""
    col = d.pop("_colore", None)
    nome = d.pop("_colore_nome", None)
    vern = d.pop("_vernice", None)
    colore = nome or col
    if colore and vern and re.search(r"(?i)metal", vern) and not re.search(r"(?i)metal", colore):
        colore += " metallizzato"
    if colore:
        d.setdefault("colore", colore[:50])
    completa_potenza(d)
    return {k: v for k, v in d.items() if k in CHIAVI and v not in (None, "", [], {})}


# icone e etichette dei "vehicleDetails" delle pagine di ricerca di AutoScout24
_DETTAGLI_AS = [
    (re.compile(r"(?i)transmission|cambio"), "cambio"),
    (re.compile(r"(?i)speedometer|potenza|power"), "potenza"),
    (re.compile(r"(?i)co2"), "co2"),
    (re.compile(r"(?i)leaf|consum|fuel_consumption"), "consumo"),
    (re.compile(r"(?i)owner|propriet|person"), "proprietari"),
    (re.compile(r"(?i)calendar|immatricol|registr"), "immatricolazione"),
    (re.compile(r"(?i)euro|emission|classe"), "classe_euro"),
]


def da_autoscout_ricerca(it: dict) -> dict:
    """Risultato di ricerca di AutoScout24: `vehicle` + `vehicleDetails` (mai `seller`)."""
    if not isinstance(it, dict):
        return {}
    try:
        d: dict = {}
        for x in it.get("vehicleDetails") or []:
            if not isinstance(x, dict):
                continue
            nome = f"{x.get('iconName') or ''} {x.get('ariaLabel') or ''}"
            for rx, chiave in _DETTAGLI_AS:
                if rx.search(nome):
                    _metti(d, chiave, x.get("data"))
                    break
        veh = it.get("vehicle")
        if isinstance(veh, dict):
            da_json(veh, d)
        return _chiudi(d)
    except Exception:
        return {}


def da_autoscout_pagina(det: dict) -> dict:
    """Pagina dell'annuncio di AutoScout24 (`listingDetails`): prima `vehicle`, poi il resto (senza venditore)."""
    if not isinstance(det, dict):
        return {}
    try:
        d: dict = {}
        if isinstance(det.get("vehicle"), dict):
            da_json(det["vehicle"], d)
        da_json({k: v for k, v in det.items() if k != "vehicle"}, d)
        return _chiudi(d)
    except Exception:
        return {}


# ------------------------------------------------------------------------------------------------ Subito
_ETICHETTE_SUB = [
    (re.compile(r"(?i)optional|accessor|dotazion|equipaggiam|comfort|sicurezza|extra"), "optional"),
    (re.compile(r"(?i)co2|emissioni\s+co"), "co2"),
    (re.compile(r"(?i)classe\s+(?:di\s+)?emission|classe\s+euro|\beuro\b|inquinament|emission"), "classe_euro"),
    (re.compile(r"(?i)cilindrata"), "cilindrata"),
    (re.compile(r"(?i)^cambio|tipo\s+di\s+cambio"), "cambio"),
    (re.compile(r"(?i)marce"), "marce"),
    (re.compile(r"(?i)potenza"), "potenza"),
    (re.compile(r"(?i)carrozzeria|tipo\s+di\s+(?:auto|veicolo)|tipologia|^tipo$"), "carrozzeria"),
    (re.compile(r"(?i)porte"), "porte"),
    (re.compile(r"(?i)posti"), "posti"),
    (re.compile(r"(?i)colore"), "colore"),
    (re.compile(r"(?i)proprietari"), "proprietari"),
    (re.compile(r"(?i)tagliand|libretto|manutenz"), "tagliandi"),
    (re.compile(r"(?i)consumo"), "consumo"),
    (re.compile(r"(?i)trazione"), "trazione"),
    (re.compile(r"(?i)versione|allestimento"), "versione"),
    (re.compile(r"(?i)garanzia"), "garanzia"),
    (re.compile(r"(?i)immatricola"), "immatricolazione"),
    (re.compile(r"(?i)fumator"), "non_fumatori"),
]


def da_subito(item: dict) -> dict:
    """Annuncio di Subito (ricerca o pagina): tutte le "features", con tutti i valori (gli optional sono un elenco)."""
    if not isinstance(item, dict):
        return {}
    try:
        d: dict = {}
        feats = item.get("features") or {}
        seq = list(feats.values()) if isinstance(feats, dict) else list(feats) if isinstance(feats, list) else []
        optional: list[str] = []
        for f in seq:
            if not isinstance(f, dict):
                continue
            label = str(f.get("label") or f.get("uri") or "")
            vals = f.get("values") or []
            if f.get("type") == "pack" and vals and isinstance(vals[0], dict) and vals[0].get("label"):
                for v in vals:                       # blocco "Auto": marca, modello, versione
                    if isinstance(v, dict) and re.search(r"(?i)versione|allestimento", str(v.get("label") or "")):
                        _metti(d, "versione", v.get("value"))
                continue
            for rx, chiave in _ETICHETTE_SUB:
                if rx.search(label):
                    if chiave == "optional":
                        optional += _voci([x.get("value") if isinstance(x, dict) else x for x in vals]
                                          if isinstance(vals, list) else vals)
                    elif chiave == "colore":
                        _metti(d, "colore", vals[0] if vals else f.get("value"))
                    else:
                        _metti(d, chiave, vals[0] if isinstance(vals, list) and vals else f.get("value"))
                    break
        _con_optional(d, optional)
        return _chiudi(d)
    except Exception:
        return {}


# ------------------------------------------------------------------------------------------------ uso
def per_ricerca(d: dict) -> dict:
    """Parte da salvare dalla ricerca: senza i dati che hanno già una colonna."""
    return {k: v for k, v in (d or {}).items() if k not in GIA_IN_COLONNA}


def unisci(colonne: dict, dati_auto: Any) -> dict:
    """Dati completi di un'auto: colonne dell'annuncio (version_raw, gearbox, power_kw, title) + dati della ricerca +
    dati della pagina (questi valgono sopra tutto il resto). `dati_auto` è il contenuto di listings.dati_auto."""
    import json
    if isinstance(dati_auto, str):
        try:
            dati_auto = json.loads(dati_auto)
        except ValueError:
            dati_auto = None
    dati_auto = dati_auto if isinstance(dati_auto, dict) else {}
    out: dict = {}
    vr = _pulito(colonne.get("version_raw"), 120)
    if vr and not re.fullmatch(r"(?i)altr[oa]\s*(allestimento|versione)?", vr):
        out["versione"] = vr
    if colonne.get("gearbox"):
        g = norm_cambio(colonne.get("gearbox"))
        if g:
            out["cambio"] = g
    if colonne.get("power_kw"):
        k = _intero(colonne.get("power_kw"), 15, 1600)
        if k:
            out["potenza_kw"] = k
    for parte in ("ricerca", "pagina"):
        p = dati_auto.get(parte)
        if isinstance(p, dict):
            for k, v in p.items():
                if k in CHIAVI and v not in (None, "", [], {}):
                    out[k] = v
    if out.get("potenza_kw") and out.get("potenza_cv") and abs(out["potenza_cv"] - out["potenza_kw"] * 1.36) > 15:
        out.pop("potenza_cv")                # potenze che non tornano tra loro: si ricalcola dai kW
    completa_potenza(out)
    if not out.get("allestimento"):
        a = allestimento_testo(out.get("versione"), colonne.get("title"))
        if a:
            out["allestimento"] = a
    if dati_auto.get("letto_il"):
        out["letto_il"] = dati_auto["letto_il"]
    return out
