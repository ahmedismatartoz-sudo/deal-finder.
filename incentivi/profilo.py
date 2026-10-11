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


def leggi_visura(text: str, oggi: dt.date | None = None) -> tuple[dict, list[str]]:
    """Estrae i dati principali dal testo di una visura camerale. Ritorna (profilo, campi_trovati).
    Lettura prudente: un dato si prende solo se l'etichetta è chiara; altrimenti resta mancante."""
    oggi = oggi or dt.date.today()
    t = re.sub(r"[ \t]+", " ", text or "")
    fonte = f"letto dalla visura caricata il {oggi.strftime('%d/%m/%Y')}"
    prof = vuoto(None)
    trovati = []

    def ok(campo):
        prof["campi"][campo] = _campo("verificato", fonte)
        trovati.append(campo)

    m = re.search(r"Partita IVA\s*:?\s*(\d{11})", t, re.I)
    if m:
        prof["piva"] = m.group(1)
    m = re.search(r"(?:Denominazione|Ragione sociale)\s*:?\s*([^\n]{3,120})", t, re.I)
    if m:
        prof["ragione_sociale"] = m.group(1).strip()
        ok("ragione_sociale")
    m = re.search(r"Forma giuridica\s*:?\s*([^\n]{3,80})", t, re.I)
    if m:
        prof["forma_giuridica"] = m.group(1).strip()
        ok("forma_giuridica")
    # sede: "Indirizzo Sede legale ... (MI)" oppure "Sede legale ... ( MI )"
    m = re.search(r"Sede legale[^\n]*?\n?[^\n]*?\b([A-Z][A-Za-zÀ-ú' ]{1,40})\s*\(\s*([A-Z]{2})\s*\)", t)
    if m and m.group(2) in SIGLE:
        pv = m.group(2)
        prof["sede"] = {"comune": m.group(1).strip().title(), "provincia": pv, "regione": REGIONE.get(pv, "fuori Lombardia")}
        ok("sede")
    codes = []
    for m in re.finditer(r"(?:Codice|Codice ATECO|ATECO)\s*:?\s*(\d{2}\.\d{2}(?:\.\d{1,2})?)", t, re.I):
        if m.group(1) not in codes:
            codes.append(m.group(1))
    if codes:
        prof["ateco"] = [{"codice": c, "descrizione": None, "primario": i == 0, "versione": "da visura"}
                         for i, c in enumerate(codes[:6])]
        ok("ateco")
    m = re.search(r"Data (?:atto di )?costituzione\s*:?\s*([0-9/.-]{8,10})", t, re.I)
    if m and _data(m.group(1)):
        prof["data_costituzione"] = _data(m.group(1))
        ok("data_costituzione")
    m = re.search(r"Data (?:d'|di )?inizio (?:dell')?attivit(?:à|a'?)\s*:?\s*([0-9/.-]{8,10})", t, re.I)
    if m and _data(m.group(1)):
        prof["data_inizio_attivita"] = _data(m.group(1))
        if "data_costituzione" not in trovati:
            prof["data_costituzione"] = prof["data_inizio_attivita"]
            ok("data_costituzione")
    m = re.search(r"Stato (?:dell')?attivit(?:à|a'?)\s*:?\s*([A-Za-z ]{4,30})", t, re.I)
    if m:
        prof["stato"] = "attiva" if "attiv" in m.group(1).lower() and "inattiv" not in m.group(1).lower() else m.group(1).strip().lower()
        ok("stato")
    m = re.search(r"(?:Numero )?[Aa]ddetti(?:\s+al\s+\d{1,2}/\d{1,2}/\d{4})?[^\n\d]{0,60}(\d{1,4})\b(?!/)", t)
    if m:
        prof["addetti"] = int(m.group(1))
        ok("addetti")
    if re.search(r"impresa artigiana|albo (?:delle )?imprese artigiane", t, re.I):
        prof["extra"]["artigiana"] = True
    return prof, trovati
