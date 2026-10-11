"""Profilo dell'impresa SENZA collegamenti a pagamento.

Due strade gratuite:
  1. domande semplici al commerciante → dati "dichiarati" (marcati così in ogni requisito);
  2. visura caricata dal commerciante (scaricata gratis dal Cassetto Digitale, impresa.italia.it)
     → si legge il testo del PDF; i dati trovati sono "letti dalla visura caricata", quelli non
     trovati restano da chiedere. Il PDF originale si conserva cifrato.
"""
from __future__ import annotations

import datetime as dt
import io
import re

LOMBARDIA = {"MI": "Milano", "MB": "Monza e Brianza", "BG": "Bergamo", "BS": "Brescia", "CO": "Como",
             "VA": "Varese", "LC": "Lecco", "LO": "Lodi", "PV": "Pavia", "CR": "Cremona", "MN": "Mantova",
             "SO": "Sondrio"}
ALTRE = {"NO": ("Novara", "Piemonte"), "TO": ("Torino", "Piemonte"), "PC": ("Piacenza", "Emilia-Romagna"),
         "VR": ("Verona", "Veneto"), "RM": ("Roma", "Lazio")}

ATTIVITA = {
    "auto": [{"codice": "45.11.01", "descrizione": "Commercio di autovetture e autoveicoli leggeri", "primario": True}],
    "auto_officina": [{"codice": "45.11.01", "descrizione": "Commercio di autovetture e autoveicoli leggeri", "primario": True},
                      {"codice": "45.20.10", "descrizione": "Riparazioni meccaniche di autoveicoli", "primario": False}],
    "officina": [{"codice": "45.20.10", "descrizione": "Riparazioni meccaniche di autoveicoli", "primario": True}],
}
DIPENDENTI = {"0": 0, "1-2": 2, "3-9": 6, "10+": 15}
FORME = {"individuale": "Impresa individuale", "societa": "Società"}

DOMANDE_PROFILO = [
    {"id": "provincia", "testo": "Dove hai la sede?", "tipo": "scelta",
     "opzioni": [[k, v] for k, v in LOMBARDIA.items()] + [[k, f"{v[0]} (fuori Lombardia)"] for k, v in ALTRE.items()]
                + [["XX", "Altra provincia fuori Lombardia"]]},
    {"id": "attivita", "testo": "Cosa fai?", "tipo": "scelta",
     "opzioni": [["auto", "Vendo auto usate"], ["auto_officina", "Vendo auto e ho un'officina"],
                 ["officina", "Solo officina"], ["altro", "Altro settore (scrivi il codice ATECO)"]]},
    {"id": "anno", "testo": "Da che anno è aperta l'attività?", "tipo": "anno"},
    {"id": "dipendenti", "testo": "Quanti dipendenti hai?", "tipo": "scelta",
     "opzioni": [["0", "Nessuno"], ["1-2", "1-2"], ["3-9", "3-9"], ["10+", "10 o più"]]},
    {"id": "forma", "testo": "Che tipo di impresa è?", "tipo": "scelta",
     "opzioni": [["individuale", "Ditta individuale"], ["societa", "Società (srl, snc, sas…)"]]},
    {"id": "artigiana", "testo": "Sei iscritto all'albo artigiani?", "tipo": "scelta",
     "opzioni": [["si", "Sì"], ["no", "No"], ["nonso", "Non so"]]},
]


def _campo(stato: str, fonte: str | None) -> dict:
    return {"stato": stato, "fonte": fonte}


def vuoto(piva: str | None) -> dict:
    return {"piva": piva or "non indicata", "ragione_sociale": None, "forma_giuridica": None, "ateco": [],
            "ateco2025": [], "sede": {}, "unita_locali": [], "data_costituzione": None, "data_inizio_attivita": None,
            "stato": None, "addetti": None, "soci": [], "amministratori": [], "extra": {}, "campi": {}}


def applica_dichiarato(prof: dict, d: dict, oggi: dt.date | None = None) -> dict:
    """Aggiorna il profilo con le risposte del commerciante (solo i campi forniti e validi)."""
    oggi = oggi or dt.date.today()
    fonte = f"dichiarato dal commerciante il {oggi.strftime('%d/%m/%Y')}"
    campi = prof.setdefault("campi", {})
    pv = str(d.get("provincia") or "").upper()
    if pv in LOMBARDIA:
        prof["sede"] = {"comune": None, "provincia": pv, "regione": "Lombardia"}
        campi["sede"] = _campo("dichiarato", fonte)
    elif pv in ALTRE:
        prof["sede"] = {"comune": None, "provincia": pv, "regione": ALTRE[pv][1]}
        campi["sede"] = _campo("dichiarato", fonte)
    elif pv == "XX":
        prof["sede"] = {"comune": None, "provincia": None, "regione": "fuori Lombardia"}
        campi["sede"] = _campo("dichiarato", fonte)
    att = d.get("attivita")
    if att in ATTIVITA:
        prof["ateco"] = [dict(a, versione="2007") for a in ATTIVITA[att]]
        campi["ateco"] = _campo("dichiarato", fonte)
        if "officina" in att:
            prof.setdefault("extra", {})["officina_dichiarata"] = True
    elif att == "altro" and d.get("ateco"):
        code = re.sub(r"[^\d.]", "", str(d["ateco"]))[:8]
        digits = code.replace(".", "")
        if len(digits) >= 2:
            fmt = ".".join(x for x in (digits[:2], digits[2:4], digits[4:6]) if x)
            prof["ateco"] = [{"codice": fmt, "descrizione": "indicato dal commerciante", "primario": True, "versione": "2007"}]
            campi["ateco"] = _campo("dichiarato", fonte)
    try:
        anno = int(d.get("anno")) if d.get("anno") not in (None, "") else None
    except (TypeError, ValueError):
        anno = None
    if anno and 1900 < anno <= oggi.year:
        mese = d.get("mese")
        try:
            mese = min(12, max(1, int(mese))) if mese not in (None, "") else (1 if anno < oggi.year else oggi.month)
        except (TypeError, ValueError):
            mese = 1
        prof["data_costituzione"] = prof["data_inizio_attivita"] = f"{anno:04d}-{mese:02d}-01"
        campi["data_costituzione"] = _campo("dichiarato", fonte + " (solo anno/mese)")
        if not prof.get("stato"):
            prof["stato"] = "attiva"
            campi["stato"] = _campo("dichiarato", fonte)
    if d.get("dipendenti") in DIPENDENTI:
        prof["addetti"] = DIPENDENTI[d["dipendenti"]]
        prof["addetti_fascia"] = d["dipendenti"]
        campi["addetti"] = _campo("dichiarato", fonte + f" (fascia {d['dipendenti']})")
    if d.get("forma") in FORME:
        prof["forma_giuridica"] = FORME[d["forma"]]
        campi["forma_giuridica"] = _campo("dichiarato", fonte)
    if d.get("artigiana") in ("si", "no"):
        prof.setdefault("extra", {})["artigiana"] = d["artigiana"] == "si"
    return prof


def domande_mancanti(prof: dict) -> list[dict]:
    """Domande sul profilo solo per i dati che mancano."""
    c = prof.get("campi") or {}
    have = {
        "provincia": (c.get("sede") or {}).get("stato") in ("verificato", "dichiarato"),
        "attivita": (c.get("ateco") or {}).get("stato") in ("verificato", "dichiarato"),
        "anno": (c.get("data_costituzione") or {}).get("stato") in ("verificato", "dichiarato"),
        "dipendenti": (c.get("addetti") or {}).get("stato") in ("verificato", "dichiarato"),
        "forma": (c.get("forma_giuridica") or {}).get("stato") in ("verificato", "dichiarato"),
        "artigiana": "artigiana" in (prof.get("extra") or {}),
    }
    return [q for q in DOMANDE_PROFILO if not have[q["id"]]]


# ---------------------------------------------------------------- visura caricata (PDF)
SIGLE = set(LOMBARDIA) | {"AG", "AL", "AN", "AO", "AR", "AP", "AT", "AV", "BA", "BT", "BL", "BN", "BI", "BO", "BZ",
         "BR", "CA", "CL", "CB", "CE", "CT", "CZ", "CH", "CS", "CN", "EN", "FM", "FE", "FI", "FG", "FC", "FR", "GE",
         "GO", "GR", "IM", "IS", "SP", "AQ", "LT", "LE", "LI", "LU", "MC", "MS", "MT", "ME", "MO", "NA", "NO", "NU",
         "OR", "PD", "PA", "PR", "PG", "PU", "PE", "PC", "PI", "PT", "PN", "PZ", "PO", "RG", "RA", "RC", "RE", "RI",
         "RN", "RM", "RO", "SA", "SS", "SV", "SI", "SR", "TA", "TE", "TR", "TO", "TP", "TN", "TV", "TS", "UD", "VE",
         "VB", "VC", "VR", "VV", "VI", "VT", "SU"}
REGIONE = {**{k: "Lombardia" for k in LOMBARDIA}, **{"NO": "Piemonte", "TO": "Piemonte", "VB": "Piemonte",
           "VC": "Piemonte", "AL": "Piemonte", "PC": "Emilia-Romagna", "VR": "Veneto", "RM": "Lazio"}}


def testo_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in r.pages[:12])


def _data(s: str) -> str | None:
    m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", s)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


ATECO_2025_A_2007 = {"47.81": "45.11", "46.71": "45.11", "95.31": "45.20", "47.82": "45.32", "47.83": "45.40"}
STATI = ("inattiva", "attiva", "sospesa", "cessata", "in liquidazione", "in scioglimento")


def _dopo(t: str, etichetta: str, n: int = 160) -> str | None:
    """Testo che segue un'etichetta (anche se il valore è sulla riga dopo)."""
    m = re.search(etichetta, t, re.I)
    return t[m.end(): m.end() + n] if m else None


def leggi_visura(text: str, oggi: dt.date | None = None) -> tuple[dict, list[str]]:
    """Estrae i dati principali dal testo di una visura camerale (formato InfoCamere/Registro Imprese).
    Ritorna (profilo, campi_trovati). Un dato si prende solo se l'etichetta è riconosciuta;
    i dati non trovati restano mancanti e diventano domande."""
    oggi = oggi or dt.date.today()
    raw = (text or "").replace("\r", "")
    t = re.sub(r"[ \t ]+", " ", raw)
    t = t.replace("’", "'").replace("`", "'")
    flat = re.sub(r"\s+", " ", t)                       # valori che finiscono sulla riga dopo l'etichetta
    fonte = f"letto dalla visura caricata il {oggi.strftime('%d/%m/%Y')}"
    prof = vuoto(None)
    trovati: list[str] = []

    def ok(campo):
        prof["campi"][campo] = _campo("verificato", fonte)
        if campo not in trovati:
            trovati.append(campo)

    # partita IVA (o codice fiscale numerico)
    m = re.search(r"Partita\s*IVA\s*:?\s*(?:IT)?\s*(\d{11})", flat, re.I) or \
        re.search(r"Codice fiscale[^0-9]{0,60}(\d{11})\b", flat, re.I)
    if m:
        prof["piva"] = m.group(1)
    # denominazione: etichetta esplicita oppure la riga subito dopo il titolo "VISURA ..."
    m = re.search(r"(?:Denominazione|Ragione sociale|Ditta)\s*:?\s*([^\n]{3,120})", t, re.I)
    nome = m.group(1).strip() if m else None
    if not nome:
        lines = [x.strip() for x in t.split("\n") if x.strip()]
        for i, x in enumerate(lines[:25]):
            if re.match(r"VISURA\b", x, re.I):
                for y in lines[i + 1:i + 5]:
                    if not re.search(r"visura|registro|camera|documento|estratto|data|pagina|codice|numero|indirizzo",
                                     y, re.I) and len(y) > 3:
                        nome = y
                        break
                break
    if nome:
        prof["ragione_sociale"] = re.sub(r"\s{2,}", " ", nome)[:120]
        ok("ragione_sociale")
    m = re.search(r"Forma giuridica\s*:?\s*([A-Za-zÀ-ú' .]{3,80}?)(?=\s+(?:Data|Codice|Numero|Indirizzo|Sede|PEC|Partita|Oggetto|Capitale|Durata|$))",
                  flat, re.I)
    if m:
        prof["forma_giuridica"] = m.group(1).strip()
        ok("forma_giuridica")
    else:
        mt = re.search(r"VISURA[^\n]*?(SOCIETA'? DI CAPITAL[EI]|SOCIETA'? DI PERSONE|IMPRESA INDIVIDUALE)", t, re.I)
        if mt:
            prof["forma_giuridica"] = mt.group(1).title().replace("Societa'", "Società")
            ok("forma_giuridica")
    # sede: "Indirizzo Sede legale MILANO (MI) VIA ..." / "Indirizzo Sede MILANO (MI)" / "Sede legale ... (MI)"
    sede = None
    for lab in (r"Indirizzo\s+Sede(?:\s+legale)?", r"Sede\s+legale", r"Indirizzo\s+(?:della\s+)?sede", r"\bSede\b"):
        chunk = _dopo(flat, lab, 200)
        if chunk:
            m = re.search(r"([A-ZÀ-Ú][A-ZÀ-Ú' .-]{1,40}?)\s*\(\s*([A-Z]{2})\s*\)", chunk)
            if m and m.group(2) in SIGLE:
                sede = (m.group(1).strip(), m.group(2))
                break
    if sede:
        prof["sede"] = {"comune": sede[0].title(), "provincia": sede[1], "regione": REGIONE.get(sede[1], "fuori Lombardia")}
        ok("sede")
    # unità locali (sigla tra parentesi dopo "Unità locale" / "U.L.")
    for m in re.finditer(r"(?:Unit[àa] Locale|Unita' locale|U\.L\.)[^()]{0,160}?([A-ZÀ-Ú][A-ZÀ-Ú' .-]{1,40}?)\s*\(\s*([A-Z]{2})\s*\)", flat, re.I):
        if m.group(2) in SIGLE:
            prof["unita_locali"].append({"comune": m.group(1).strip().title(), "provincia": m.group(2),
                                         "regione": REGIONE.get(m.group(2), "fuori Lombardia")})
    if prof["unita_locali"]:
        ok("unita_locali")
    # date
    for lab, campo in ((r"Data\s+(?:dell')?atto\s+di\s+costituzione", "data_costituzione"),
                       (r"Data\s+(?:di\s+)?costituzione", "data_costituzione"),
                       (r"Data\s+(?:d'|di\s+)?inizio\s+(?:dell')?attivit(?:à|a'?)(?:\s+dell'impresa)?", "data_inizio_attivita"),
                       (r"Data\s+iscrizione", "data_iscrizione")):
        chunk = _dopo(flat, lab, 40)
        d = _data(chunk) if chunk else None
        if d and not prof.get(campo):
            prof[campo] = d
    if not prof.get("data_costituzione"):
        prof["data_costituzione"] = prof.get("data_inizio_attivita") or prof.pop("data_iscrizione", None)
    prof.pop("data_iscrizione", None)
    if prof.get("data_costituzione"):
        ok("data_costituzione")
    # stato attività
    chunk = _dopo(flat, r"Stato\s+(?:dell')?attivit(?:à|a'?)", 40)
    if chunk:
        low = chunk.lower()
        st = next((x for x in STATI if low.lstrip(" :").startswith(x)), None)
        if st:
            prof["stato"] = st
            ok("stato")
    elif re.search(r"\bimpresa\s+(?:attiva|risulta attiva)\b", flat, re.I):
        prof["stato"] = "attiva"
        ok("stato")
    # ATECO (2007 e 2025)
    codes = []
    for m in re.finditer(r"(?:Codice\s+ATECO(?:\s*20(?:07|25))?|ATECO(?:RI)?(?:\s*20(?:07|25))?|Codice)\s*:?\s*(\d{2}\.\d{2}(?:\.\d{1,2})?)\b",
                         flat, re.I):
        c = m.group(1)
        if c not in codes:
            codes.append(c)
    ateco07 = []
    for c in codes:
        pre = c[:5]
        if pre in ATECO_2025_A_2007:
            prof["ateco2025"].append({"codice": c, "descrizione": "ATECO 2025 letto dalla visura"})
            alt = ATECO_2025_A_2007[pre]
            if not any(a.startswith(alt) for a in ateco07):
                ateco07.append(alt)
        elif c not in ateco07:
            ateco07.append(c)
    if ateco07:
        prof["ateco"] = [{"codice": c, "descrizione": None, "primario": i == 0, "versione": "da visura"}
                         for i, c in enumerate(ateco07[:8])]
        ok("ateco")
        if any(c.startswith("45.20") for c in ateco07):
            prof["extra"]["officina_dichiarata"] = True
    # addetti: il primo numero dopo "Addetti"/"Numero addetti"/"dipendenti", saltando le date
    for lab in (r"Numero\s+(?:degli\s+)?addetti(?:\s+dell'impresa)?", r"\bAddetti\b", r"Numero\s+dipendenti", r"\bdipendenti\b"):
        chunk = _dopo(flat, lab, 140)
        if not chunk:
            continue
        chunk = re.sub(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", " ", chunk)
        m = re.search(r"(?:Totale|totale|:)?\s*\b(\d{1,4})\b", chunk)
        if m:
            prof["addetti"] = int(m.group(1))
            ok("addetti")
            break
    # artigiana: se la visura lo dice; se la visura è stata letta e non ne parla, si considera non artigiana
    if re.search(r"artigian\w*\s*:?\s*(?:no|non)\b", flat, re.I):
        prof["extra"]["artigiana"] = False
    elif re.search(r"impresa\s+artigiana|albo\s+(?:delle\s+)?imprese\s+artigiane|\bartigian[oa]\b", flat, re.I):
        prof["extra"]["artigiana"] = True
    elif len(trovati) >= 3:
        prof["extra"]["artigiana"] = False
    return prof, trovati
