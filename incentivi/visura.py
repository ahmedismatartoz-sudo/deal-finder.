"""Recupero dei dati ufficiali dell'impresa dalla sola partita IVA, tramite Openapi.

Due servizi distinti (documentazione: incentivi/data/ricerca/openapi.md):
  - Company  (company.openapi.com, sandbox test.company.openapi.com): dati strutturati
    del Registro Imprese → GET /IT-full/{piva}. Da qui si leggono ATECO, sede, stato, date, addetti.
  - Visure Camerali (visurecamerali.openapi.it, sandbox test.visurecamerali.openapi.it):
    il PDF ORIGINALE della visura, con flusso asincrono:
      GET /impresa/{piva} → chiamate_disponibili → POST /ordinaria-<tipo> → GET /<tipo>/{id}
      → quando "Visura evasa": GET /<tipo>/{id}/allegati → zip in base64 → PDF.
Il PDF si conserva così com'è (cifrato), con fonte e data. Nessun dato viene "ricostruito".

Variabili d'ambiente (mai nel codice):
  OPENAPI_TOKEN      token Bearer creato dalla console Openapi con gli scope dei due servizi
  OPENAPI_AMBIENTE   "sandbox" (predefinito) oppure "produzione"
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import logging
import os
import re
import time
import zipfile

log = logging.getLogger("incentivi.visura")


class NonConfigurato(RuntimeError):
    pass


class Errore(RuntimeError):
    pass


def ambiente() -> str:
    return "produzione" if os.environ.get("OPENAPI_AMBIENTE", "sandbox").lower().startswith("prod") else "sandbox"


def configurato() -> bool:
    return bool(os.environ.get("OPENAPI_TOKEN"))


def _host(servizio: str) -> str:
    base = {"company": "company.openapi.com", "visure": "visurecamerali.openapi.it"}[servizio]
    return f"https://{'test.' if ambiente() == 'sandbox' else ''}{base}"


def _http():
    import httpx
    tok = os.environ.get("OPENAPI_TOKEN")
    if not tok:
        raise NonConfigurato("OPENAPI_TOKEN non impostato")
    return httpx.Client(timeout=httpx.Timeout(40, connect=10), headers={"Authorization": f"Bearer {tok}"})


# ---------------------------------------------------------------- partita IVA
def piva_valida(piva: str) -> bool:
    """Controllo formale della partita IVA italiana (11 cifre + cifra di controllo)."""
    p = re.sub(r"\s", "", str(piva or ""))
    if not re.fullmatch(r"\d{11}", p) or p == "0" * 11:
        return False
    s = 0
    for i, ch in enumerate(p[:10]):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        s += n
    return (10 - s % 10) % 10 == int(p[10])


# ---------------------------------------------------------------- dati strutturati (Company)
def _ateco_fmt(code) -> str:
    c = re.sub(r"\D", "", str(code or ""))
    if len(c) < 2:
        return ""
    parts = [c[:2], c[2:4], c[4:6]]
    return ".".join(p for p in parts if p)


def _title(s) -> str:
    return str(s or "").strip().title() if s else ""


def _get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def profilo_da_company(data: dict, piva: str, oggi: dt.date | None = None) -> dict:
    """Converte la risposta IT-full in profilo. Ogni campo letto è marcato 'verificato' con la fonte."""
    oggi = oggi or dt.date.today()
    if isinstance(data, list):
        data = data[0] if data else {}
    fonte = f"dati Registro Imprese via Openapi Company ({ambiente()}) del {oggi.strftime('%d/%m/%Y')}"
    campi: dict = {}

    def ok(campo, valore):
        campi[campo] = {"stato": "verificato" if valore not in (None, "", []) else "mancante",
                        "fonte": fonte if valore not in (None, "", []) else None}
        return valore

    det = data.get("companyDetails") or {}
    addr = data.get("address") or {}
    reg = addr.get("registeredOffice") if isinstance(addr.get("registeredOffice"), dict) else addr
    prov = _get(reg, "province", "code") or reg.get("province")
    regione = _get(reg, "region", "description") or reg.get("region")
    sede = {"indirizzo": reg.get("streetName") or reg.get("street"), "comune": _title(reg.get("town")),
            "provincia": prov if isinstance(prov, str) else None, "regione": _title(regione) if isinstance(regione, str) else None}
    ac = data.get("atecoClassification") or {}
    ateco = []
    for key, sec in (("ateco2022", "secondaryAteco2022"), ("ateco2007", "secondaryAteco2007")):
        prim = ac.get(key)
        if isinstance(prim, dict) and prim.get("code"):
            ateco.append({"codice": _ateco_fmt(prim["code"]), "descrizione": prim.get("description"), "primario": True,
                          "versione": "2007 (agg. 2022)"})
            for s in str(ac.get(sec) or "").split(","):
                if s.strip():
                    ateco.append({"codice": _ateco_fmt(s), "descrizione": None, "primario": False,
                                  "versione": "2007 (agg. 2022)"})
            break
    ateco2025 = []
    if isinstance(ac.get("ateco"), dict) and ac["ateco"].get("code"):
        ateco2025.append({"codice": _ateco_fmt(ac["ateco"]["code"]), "descrizione": ac["ateco"].get("description")})
    status = data.get("companyStatus") or {}
    st = _get(status, "activityStatus", "description") or _get(status, "activityStatus", "code") or data.get("activityStatus")
    st_norm = None
    if st:
        s = str(st).lower()
        st_norm = "attiva" if s in ("a", "attiva", "enable", "active") or s.startswith("attiv") else s
    dates = data.get("companyDates") or {}
    emp = data.get("employees") or {}
    addetti = emp.get("employee")
    if addetti is None:
        addetti = _get(data, "balanceSheets", "last", "employees")
    branches = []
    for b in data.get("branches") or []:
        a = b.get("address") if isinstance(b, dict) else None
        a = a or b if isinstance(b, dict) else {}
        branches.append({"indirizzo": a.get("streetName"), "comune": _title(a.get("town")),
                         "provincia": _get(a, "province", "code") or a.get("province"),
                         "regione": _title(_get(a, "region", "description")) or None})
    legal = _get(data, "legalForm", "detailedLegalForm", "description") or _get(data, "detailedLegalForm", "description")
    prof = {
        "piva": det.get("vatCode") or piva,
        "ragione_sociale": ok("ragione_sociale", det.get("companyName") or data.get("companyName")),
        "forma_giuridica": ok("forma_giuridica", legal),
        "ateco": ok("ateco", ateco),
        "ateco2025": ateco2025,
        "sede": ok("sede", sede if sede.get("comune") else {}),
        "unita_locali": ok("unita_locali", branches),
        "data_costituzione": ok("data_costituzione", (dates.get("incorporationDate") or dates.get("registrationDate") or "")[:10] or None),
        "data_inizio_attivita": (dates.get("startDate") or "")[:10] or None,
        "stato": ok("stato", st_norm),
        "addetti": ok("addetti", addetti),
        "soci": ok("soci", [s.get("companyName") or " ".join(x for x in (s.get("name"), s.get("surname")) if x)
                            for s in (data.get("shareholders") or []) if isinstance(s, dict)]),
        "amministratori": ok("amministratori", [" ".join(x for x in (m.get("name"), m.get("surname")) if x)
                                                for m in (data.get("managers") or []) if isinstance(m, dict)]),
        "extra": {},
    }
    prof["campi"] = campi
    return prof


def leggi_impresa(piva: str) -> dict:
    with _http() as http:
        r = http.get(f"{_host('company')}/IT-full/{piva}")
    if r.status_code in (204, 404):
        raise Errore("Partita IVA non trovata nel Registro Imprese" + (" (in sandbox funzionano solo le imprese di prova)"
                                                                      if ambiente() == "sandbox" else ""))
    if r.status_code == 402:
        raise Errore("Credito Openapi esaurito")
    if r.status_code != 200:
        raise Errore(f"Servizio Openapi Company: HTTP {r.status_code}")
    body = r.json() or {}
    data = body.get("data")
    if not data:
        raise Errore("Risposta Openapi senza dati")
    return profilo_da_company(data, piva)


# ---------------------------------------------------------------- PDF della visura (Visure Camerali)
STATI_PRONTI = ("visura evasa", "evasa", "completed")


def _tipo_visura(chiamate: list[str]) -> str | None:
    for pref in ("ordinaria-societa-capitale", "ordinaria-societa-persone", "ordinaria-impresa-individuale"):
        if any(c.rstrip("/").endswith(pref) for c in chiamate):
            return pref
    return None


def richiedi_visura(piva: str) -> dict:
    """Avvia la richiesta della visura ordinaria. Ritorna {tipo, richiesta_id, stato}."""
    with _http() as http:
        r = http.get(f"{_host('visure')}/impresa/{piva}")
        if r.status_code != 200:
            raise Errore(f"Ricerca impresa (visure): HTTP {r.status_code}")
        items = (r.json() or {}).get("data") or []
        item = items[0] if isinstance(items, list) and items else items if isinstance(items, dict) else {}
        tipo = _tipo_visura(item.get("chiamate_disponibili") or [])
        if not tipo:
            raise Errore("Visura ordinaria non disponibile per questa impresa")
        r = http.post(f"{_host('visure')}/{tipo}", json={"cf_piva_id": piva})
        if r.status_code not in (200, 201):
            raise Errore(f"Richiesta visura: HTTP {r.status_code}")
        d = (r.json() or {}).get("data") or {}
    return {"tipo": tipo, "richiesta_id": d.get("id"), "stato": d.get("stato_richiesta") or "In elaborazione"}


def stato_visura(tipo: str, richiesta_id: str) -> str:
    with _http() as http:
        r = http.get(f"{_host('visure')}/{tipo}/{richiesta_id}")
    if r.status_code != 200:
        raise Errore(f"Stato visura: HTTP {r.status_code}")
    return ((r.json() or {}).get("data") or {}).get("stato_richiesta") or ""


def scarica_visura(tipo: str, richiesta_id: str) -> tuple[str, bytes]:
    """Scarica lo zip (base64) e ne estrae il PDF originale: (nome, bytes)."""
    with _http() as http:
        r = http.get(f"{_host('visure')}/{tipo}/{richiesta_id}/allegati")
        if r.status_code == 404:                       # la pagina prodotto usa /attachments
            r = http.get(f"{_host('visure')}/{tipo}/{richiesta_id}/attachments")
    if r.status_code != 200:
        raise Errore(f"Download visura: HTTP {r.status_code}")
    d = (r.json() or {}).get("data") or {}
    raw = base64.b64decode(d.get("file") or "")
    if raw[:4] == b"%PDF":
        return (d.get("nome") or d.get("name") or "visura.pdf"), raw
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        pdfs = [n for n in z.namelist() if n.lower().endswith(".pdf")]
        if not pdfs:
            raise Errore("Lo zip della visura non contiene un PDF")
        return pdfs[0], z.read(pdfs[0])


def attendi_visura(tipo: str, richiesta_id: str, secondi: int = 40) -> str:
    fine = time.time() + secondi
    stato = ""
    while time.time() < fine:
        stato = stato_visura(tipo, richiesta_id)
        if stato.lower() in STATI_PRONTI:
            return stato
        time.sleep(4)
    return stato
