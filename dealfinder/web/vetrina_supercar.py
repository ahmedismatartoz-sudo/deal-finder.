"""23/11 (cliente: "Supercar e macchine sportive: le voglio subito sul sito, tutte quelle della Lombardia").

VETRINA A PARTE "Supercar Lombardia": tutte le sportive e supercar in vendita nelle 12 province lombarde, di privati e
di commercianti, al prezzo dell'annuncio. NON sono affari: niente margine, niente mercato, niente stima. Per questo
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
CACHE_SECONDI = 600
# Il testo LEASING_RE è scritto per gli annunci dei privati: contiene parole ("finanziamento", "anticipo") che i
# commercianti scrivono in quasi ogni annuncio ("possibilità di finanziamento"). Per i commercianti LEASING_RE vale su
# titolo e versione; nella descrizione vale il testo "solo con finanziamento/leasing" del sito (scovo.FIN_SOLO_RE).
# True = LEASING_RE anche sulla descrizione dei commercianti (ne toglie la gran parte).
LEASING_DESCRIZIONE_COMMERCIANTI = False
FLAG_RATA = frozenset({"rata", "leasing", "leasing_o_rata"})
MOTIVI_FUORI = frozenset({"doppione", "truffa", "prezzo_finto"})
RATA_AI = SR.Regole.jev_rata    # stessa soglia della regola prezzo_a_rata del sito

_SOLO_FIN_RE = re.compile(scovo.FIN_SOLO_RE, re.I)


def _n(s) -> str:
    """Marca senza accenti, spazi e trattini: "Aston-Martin" → "astonmartin"."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


# allestimenti solo estetici: NON fanno una sportiva, si tolgono dal testo prima di cercare il modello
_FINTI = re.compile(
    r"\bamg (line|pack\w*|paket|styling|sport\w*|look|style|exterieur|interieur|premium|advanced)\b"
    r"|\b(pacchetto|pack|paket|kit|look|cerchi|allestimento|linea|stile) (amg|m|jcw|abarth|gti|rs|st|cupra|n)\b"
    r"|\bm ?(sport\w*|performance|tech\w*|paket|pack\w*|aerodynam\w*)\b"
    r"|\b(s|r|st|gt|n|rs|gr|sport|fr|x) ?line\b|\bgr ?sport\b|\bf ?sport\b|\bgt ?pack\b"
    r"|\b(john cooper works|jcw) (kit|pack\w*|aero\w*|trim|styling|style|look|exterior|interior)\b")


def _testo(*parti) -> str:
    """Modello e versione in minuscolo, con uno spazio al posto di trattini e punteggiatura ("RS-6", "F-Type"),
    "R.S." scritto "rs" e senza gli allestimenti estetici ("AMG Line", "M Sport", "S line", "R-Line", "ST-Line",
    "GT Line", "N Line", "GR Sport", "F Sport")."""
    s = unicodedata.normalize("NFKD", " ".join(str(p or "") for p in parti)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\br\.\s?s\.?", "rs ", s)
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

_AMG_NUMERO = re.compile(r"(?<!\d)(32|35|36|43|45|53|55|63|65)(?!\d)|\bgt\b|\bsls\b|\bone\b")


def _c(espr: str):
    """Regola fatta di una sola espressione sul testo di modello e versione."""
    rx = re.compile(espr)
    return lambda t, mod: bool(rx.search(t))


def _mercedes(t, mod):
    if re.search(r"\b(maybach|sls|slk|slc)\b|\b(slk|slc) ?\d{2,3}\b|\bsl\b|\bsl ?\d{2,3}\b", t):
        return True
    # AMG vera: la sigla AMG con il numero di un modello AMG (A 35, A 45, C 43, C 63, E 53, G 63...) o AMG GT / SLS
    return bool(re.search(r"\bamg\b", t) and _AMG_NUMERO.search(t))


_BMW = re.compile(r"\balpina\b|\bm ?[234568]\b|\bx ?[3456] ?m\b(?! ?\d)|\bxm\b|\bz ?4\b"
                  r"|\bm ?(135|140|235|240|340|440|550) ?[id]?\b")
_AUDI = re.compile(r"\br ?8\b|\brs ?[34567]\b|\brs ?q ?[38]\b|\brs e ?tron\b|\btt ?(s|rs)?\b|\bs ?[1345678]\b|\bsq ?[578]\b")
_VW = re.compile(r"\bgolf\b.*\b(gti|gtd|gte|r|r32)\b|\b(gti|gtd|gte|r32)\b.*\bgolf\b|\bpolo\b.*\bgti\b|\bscirocco\b|\bup\b.*\bgti\b")
_ALFA = re.compile(r"\b[48]c\b|\b(brera|spider|gtv)\b")
_FORD = re.compile(r"\bfiesta\b.*\bst\b|\bfocus\b.*\b(st|rs)\b|\bpuma\b.*\bst\b")
_HYUNDAI = re.compile(r"\b(i ?20|i ?30|kona|veloster|ioniq ?5?)\b.*\bn\b|\b(i20|i30)n\b")


def _alfa(t, mod):
    return bool(_ALFA.search(t) or (re.search(r"\b(giulia|stelvio)\b", t) and re.search(r"\b(quadrifoglio|veloce)\b", t)))


def _ford(t, mod):
    # Mustang: la coupé e la cabrio, non il SUV elettrico Mustang Mach-E. Ford GT: il modello si chiama "GT"
    return bool(_FORD.search(t) or (" mustang " in t and not re.search(r"\bmach ?e\b", t)) or mod == "gt")


def _nissan(t, mod):
    return bool(re.search(r"\b3[57]0 ?z\b|\bgt ?r\b", t) or mod == "z")


def _tutte(t, mod):
    return True


# altre marche: (scritture della marca nel database, parole del controllo veloce in SQL o None = tutta la marca,
# regola sul testo di modello e versione)
ALTRE = {
    "mercedes": (("mercedes", "mercedes-benz", "mercedes benz", "mercedesbenz"), ("amg", "maybach", "sl"), _mercedes),
    "mercedesamg": (("mercedes-amg", "mercedes amg"), None, _tutte),
    "bmw": (("bmw",), ("m2", "m3", "m4", "m5", "m6", "m8", "x3m", "x4m", "x5m", "x6m", "xm", "z4", "alpina",
                       "m135", "m140", "m235", "m240", "m340", "m440", "m550"), lambda t, mod: bool(_BMW.search(t))),
    "audi": (("audi",), ("r8", "rs", "tt", "s1", "s3", "s4", "s5", "s6", "s7", "s8", "sq5", "sq7", "sq8"),
             lambda t, mod: bool(_AUDI.search(t))),
    "volkswagen": (("volkswagen", "vw"), ("golf", "gti", "scirocco"), lambda t, mod: bool(_VW.search(t))),
    "mini": (("mini",), ("coopers", "johncooperworks", "jcw"), _c(r"\bcooper s\b|\bjohn cooper works\b|\bjcw\b")),
    "abarth": (("abarth",), None, _tutte),
    "fiat": (("fiat",), ("124", "abarth"), _c(r"\b124\b.*\bspider\b|\bspider\b.*\b124\b|\babarth\b")),
    "alfaromeo": (("alfa-romeo", "alfa romeo", "alfaromeo", "alfa"),
                  ("4c", "8c", "quadrifoglio", "veloce", "brera", "spider", "gtv"), _alfa),
    "hyundai": (("hyundai",), ("i20", "i30", "kona", "veloster", "ioniq"), lambda t, mod: bool(_HYUNDAI.search(t))),
    "ford": (("ford",), ("st", "rs", "mustang", "gt"), _ford),
    "renault": (("renault",), ("rs", "trophy"), _c(r"\bclio\b.*\brs\b|\bmegane\b.*\b(rs|trophy)\b")),
    "toyota": (("toyota",), ("86", "gr", "supra", "celica"),
               _c(r"\bg[rt] ?86\b|\bsupra\b|\bcelica\b|\bgr ?yaris\b|\byaris\b.*\bgr\b")),
    "mazda": (("mazda",), ("mx5", "rx8"), _c(r"\bmx ?5\b|\brx ?8\b")),
    "subaru": (("subaru",), ("brz", "wrx", "sti"), _c(r"\b(brz|wrx|sti)\b")),
    "peugeot": (("peugeot",), ("gti", "rcz"), _c(r"\b(208|308)\b.*\bgti\b|\brcz\b")),
    "cupra": (("cupra",), ("vz", "leon"), _c(r"\bvz\w{0,2}\b|\bleon\b.*\bcupra ?r\b")),
    "seat": (("seat",), ("cupra",), _c(r"\bleon\b.*\bcupra\b|\bcupra\b.*\bleon\b")),
    "skoda": (("skoda", "škoda"), ("rs",), _c(r"\boctavia\b.*\bv?rs\b")),
    "honda": (("honda",), ("nsx", "typer", "s2000"), _c(r"\bnsx\b|\bs ?2000\b|\bcivic\b.*\btype ?r\b")),
    "nissan": (("nissan",), ("350z", "370z", "gtr", "z"), _nissan),
    "tesla": (("tesla",), ("plaid",), _c(r"\bmodel ?s\b.*\bplaid\b")),
    "lexus": (("lexus",), ("rcf", "isf", "lc", "lfa"), _c(r"\b(rc|is) ?f\b|\blc( ?500h?)?\b|\blfa\b")),
    "jaguar": (("jaguar",), ("ftype",), _c(r"\bf ?type\b")),
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
    sql = ("SELECT l.id, l.make, l.model, l.version_raw, l.title, l.price_eur, l.province, l.city "
           "FROM listings l WHERE l.status = 'attivo' AND l.price_eur >= %(min_vero)s "
           "AND (lower(l.make) = ANY(%(intere)s) OR " + " OR ".join(rami) + ") "
           "AND (l.province IS NULL OR btrim(l.province) !~ '^[A-Za-z]{2}$' OR upper(btrim(l.province)) = ANY(%(lomb)s))")
    return sql, par


DETTAGLI_SQL = """
SELECT l.id, l.source, l.url, l.title, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox,
       l.power_kw, l.city, l.province, l.price_eur, l.price_flags, l.seller_type, l.stage, l.stage_reason,
       l.first_seen_at, l.telefono_ok, left(l.description, 3000) AS description,
       (SELECT count(*) FROM listing_photos p WHERE p.listing_id = l.id) AS n_photos
FROM listings l WHERE l.id = ANY(%s) AND l.status = 'attivo'"""

NOMI_SQL = (
    "SELECT l.id, c.nome FROM listings l JOIN commercianti_as24_annunci a ON a.listing_id = l.source_id "
    "JOIN commercianti_as24 c ON c.seller_id = a.seller_id WHERE l.id = ANY(%s) AND c.nome IS NOT NULL",
    "SELECT l.id, d.nome FROM listings l JOIN lead_tutti_ann t ON t.annuncio = l.source_id "
    "JOIN lead_tutti_dealer d ON d.chiave = t.chiave WHERE l.id = ANY(%s) AND d.nome IS NOT NULL",
)

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
    prov = SR.provincia_di(r.get("province"), r.get("city"))
    if prov in SR.ROMA:
        return "roma"
    if prov not in LOMBARDIA:
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


def scheda(r: dict, nome_salone: str | None = None, ora: float | None = None) -> dict:
    """Scheda della vetrina: solo dati dell'annuncio. Niente margine, mercato o stima; mai un numero di telefono."""
    comm = r.get("seller_type") == "commerciante"
    n_ph = int(r.get("n_photos") or 0)
    testo = f"{r.get('title') or ''} {r.get('description') or ''}"
    return {
        "id": r["id"], "marca": scovo.make_name(r.get("make")) or "Altro",
        "modello": scovo.model_name(r.get("model")) or None,
        "versione": scovo.hide_phones(scovo.version_short(r.get("version_raw"), r.get("model")))[:80] or None,
        "nome": scovo.nome(r), "anno": r.get("year"), "km": r.get("mileage_km"),
        "carb": scovo.FUEL_NAMES.get(r.get("fuel") or "", r.get("fuel")), "cambio": r.get("gearbox") or None,
        "prezzo": int(r["price_eur"]),
        "citta": r.get("city") or None, "provincia": SR.provincia_di(r.get("province"), r.get("city")),
        "supercar": e_supercar(r.get("make"), r.get("price_eur")),
        "venditore": "commerciante" if comm else "privato",
        "salone": (scovo.hide_phones(str(nome_salone))[:60] if comm and nome_salone else None),
        "ha_telefono": bool(r.get("telefono_ok") or scovo.RE_MOBILE.search(testo) or scovo.RE_LANDLINE.search(testo)),
        "giorni": _giorni(r.get("first_seen_at"), ora or time.time()),
        "fonte": scovo.SOURCE_NAMES.get(r.get("source"), r.get("source")),
        "foto": foto.url_for(r["id"], 0) if n_ph else None, "n_foto": min(n_ph, foto.MAX_PER_AUTO),
        "url": r.get("url"),
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
           rate: dict | None = None, ora: float | None = None) -> tuple[list[dict], dict]:
    """(schede in ordine di prezzo decrescente, {motivo: quante tolte}). Funzione pura, senza tetto (vedi taglia).
    nomi_tutti / nomi_sempre: espressioni di sito_regole.nomi_esclusi_re (commercianti=True / False)."""
    nomi = re.compile(nomi_sempre, re.I) if nomi_sempre else None
    nomi_comm = re.compile(nomi_tutti, re.I) if nomi_tutti else None
    saloni, rate, tolte, buone = saloni or {}, rate or {}, {}, []
    for r in righe:
        nome = saloni.get(r.get("id"))
        m = motivo_fuori(r, scarti=scarti, nomi=nomi, nomi_comm=nomi_comm, nome_salone=nome, rata=rate.get(r.get("id")))
        if m:
            tolte[m] = tolte.get(m, 0) + 1
        else:
            buone.append(scheda(r, nome, ora))
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
            "commercianti": comm, "per_marca": dict(sorted(per_marca.items(), key=lambda x: (-x[1], x[0])))}


def _leggi_blocco(conn, ids: list, scarti) -> tuple[list[dict], dict]:
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
                  nomi_sempre=SR.nomi_esclusi_re(commercianti=False), saloni=saloni, rate=rate)


def carica(conn, *, scarti=frozenset()) -> tuple[list[dict], dict]:
    """(schede, numeri). Due letture: 1) candidate con poche colonne (stato, marca, prezzo, provincia: filtri semplici
    in SQL), scelte in Python con e_sportiva e la provincia; 2) per gruppo (supercar / sportive) le più care lette per
    intero a blocchi, con descrizione, nome del salone e prezzo a rata, fino al tetto del gruppo."""
    sql, par = _sql_candidate()
    gruppi = {True: [], False: []}
    for r in conn.execute(sql, par).fetchall():
        if SR.provincia_di(r.get("province"), r.get("city")) in LOMBARDIA and e_sportiva(
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
_cache_lock = threading.Lock()


def svuota_cache() -> None:
    _cache.update(t=0.0, items=None, totale=0)


def elenco(connetti, scarti_ids=None) -> tuple[list[dict], int]:
    """(schede, quante sportive e supercar ci sono in tutto, anche oltre il tetto), dalla memoria se ha meno di
    CACHE_SECONDI. Una lettura alla volta; se il database non risponde resta l'elenco di prima."""
    if _cache["items"] is not None and time.time() - _cache["t"] < CACHE_SECONDI:
        return _cache["items"], _cache["totale"]
    with _cache_lock:
        if _cache["items"] is not None and time.time() - _cache["t"] < CACHE_SECONDI:
            return _cache["items"], _cache["totale"]
        t0 = time.time()
        try:
            scarti = frozenset(scarti_ids() if callable(scarti_ids) else (scarti_ids or ()))
            with connetti() as conn:
                items, numeri = carica(conn, scarti=scarti)
        except Exception as e:
            log.warning("VETRINA_SUPERCAR non caricata: %s", str(e)[:200])
            if _cache["items"] is None:
                raise
            _cache["t"] = time.time() - CACHE_SECONDI + 60        # si riprova tra un minuto
            return _cache["items"], _cache["totale"]
        totale = len(items) + int(numeri.get("oltre_il_tetto") or 0) + int(numeri.get("non_lette") or 0)
        _cache.update(t=time.time(), items=items, totale=totale)
        # solo conteggi: nessun nome, telefono o indirizzo
        log.warning("VETRINA_SUPERCAR %s", json.dumps(numeri.pop("riepilogo", None) or riepilogo(items), ensure_ascii=False))
        log.warning("VETRINA_SUPERCAR_LETTURA %s", json.dumps(dict(numeri, mostrate=len(items),
                                                                   secondi=round(time.time() - t0, 1)), ensure_ascii=False))
        return _cache["items"], _cache["totale"]
