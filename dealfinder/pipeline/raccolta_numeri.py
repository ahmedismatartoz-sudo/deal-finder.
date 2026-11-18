"""18/11 (OK del cliente): NUOVI numeri di telefono di autosaloni entro 60 km da Milano, lavoro lungo una tantum.

Voce della coda del cron notturno (come raccolta60): BREVO_IMPORT_LISTE=raccolta_numeri:<etichetta> (es.
raccolta_numeri:1811). Due fonti, in parallelo, fino al tetto di tempo RNUM_ORE (3 ore):
  1) vetrine dei negozi Subito (impresapiu.subito.it/shops/<id>): l'elenco dei negozi viene dall'interfaccia di
     ricerca già usata dai collettori (annunci auto di attività con shop_id, province lombarde della zona e Novara,
     solo comuni entro 60 km; MI e MB tutte), poi per ogni negozio la vetrina: nome, comune, indirizzo, sito,
     cellulari (3xx) e fissi del negozio/referente;
  2) Pagine Gialle, categorie "autoveicoli usati", "automobili commercio", "concessionari auto" per ogni comune entro
     60 km (stesso elenco di raccolta60: data/comuni_60km.json + tutti i comuni di MI e MB), dal più vicino, tramite
     lo sblocco già in uso (Bright Data Web Unlocker, BRIGHTDATA_UNLOCKER_ZONE/BRIGHTDATA_API_KEY). Se Pagine Gialle
     blocca anche dallo sblocco (RNUM_PG_BLOCCHI_MAX risposte bloccate di fila) si registra il problema (solo
     conteggi) e si continua con la fonte 1.
Solo attività che vendono auto: fuori officine, carrozzerie, noleggi, ricambi, moto, camper, demolitori, autoscuole
(nome o categoria) e sempre Loconte, Officine Mulino, Cosvalauto, Auto e Auto Varese, SR Auto, GT Automobili,
Privacar, Vroom. Mai privati: da Subito solo annunci di attività con vetrina (shop_id), Pagine Gialle è un elenco di
attività. Niente Facebook.

Dove finiscono i dati (come raccolta60, mai nei log: nei log solo conteggi):
  - righe "RNUM_GREZZE" in lead_consegne, scritte appena trovate (una per salone e fonte);
  - a lavoro finito, righe "RNUM" (unite: stesso telefono o stesso nome nella stessa provincia = un salone) con
    nome, comune, prov, km, cellulare, altri_cellulari, fisso, altri_fissi, indirizzo, sito, fonte, nuovo (almeno un
    numero che non era già nelle raccolte R60/R60T né in lead_tutti_dealer), link -> /api/admin/lead.csv?tipo=RNUM;
  - copie nei modelli Brevo NON attivi "R60 RNUM-<etichetta> dati k/n" come raccolta60 (RNUM_BREVO=0 per non farle).
Ripresa: i passi fatti (elenco negozi di una provincia, vetrina letta, pagina comune/categoria di Pagine Gialle) sono
in raccolta_numeri_passi; a tetto di tempo il lavoro si RIMANDA (non conta come tentativo fallito) e il cron della
notte dopo riprende da dove era (al massimo RNUM_GIRI_MAX giri, poi si consegna quello che c'è).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from html import unescape
from urllib.parse import quote

from .raccolta60 import comuni_zona, nn, slug

log = logging.getLogger("lead")

TIPO_GREZZE = "RNUM_GREZZE"
TIPO_FINALE = "RNUM"
VETRINA = "https://impresapiu.subito.it/shops/{}"
PG_URL = "https://www.paginegialle.it/ricerca/{cat}/{dove}"
PG_CATEGORIE = ("autoveicoli usati", "automobili commercio", "concessionari auto")
# province delle vetrine Subito: la Lombardia della zona e Novara (regione Subito, codice provincia interno)
PROVINCE_SUBITO = (("MI", 4), ("MB", 4), ("CO", 4), ("VA", 4), ("LC", 4), ("LO", 4), ("PV", 4), ("BG", 4),
                   ("CR", 4), ("NO", 2), ("BS", 4))
BANDE = [(0, 3000), (3000, 5000), (5000, 7000), (7000, 9000), (9000, 11000), (11000, 13000), (13000, 15000),
         (15000, 18000), (18000, 22000), (22000, 27000), (27000, 35000), (35000, 50000), (50000, 400000)]

# --- chi tenere ---------------------------------------------------------------------------------------------------
NON_AUTO = re.compile(
    r"officin|meccanic|carrozz|noleggi|(^|[^a-z])rent([^a-z]|$)|rental|ricamb|(^|[^a-z])moto(\s|$|cicl|veicol|r\s?bike)|"
    r"scooter|camper|caravan|roulotte|demoli|rottam|autoscuol|scuola\s+guida|gommist|pneumatic|elettrauto|"
    r"autolavagg|soccorso|carro\s*attrezzi|revision[ei]", re.I)
VENDE_AUTO = re.compile(r"usat|commercio|concessionar|vendita|autosalon|rivendit|automobil|autoveicol", re.I)
ESCLUSI_EXTRA = (r"gt[\s.-]*automobili", r"privacar", r"(^|[^a-z])vroom", r"cosval")


def _re_esclusi() -> re.Pattern:
    from ..web.sito_regole import nomi_esclusi_re
    return re.compile(nomi_esclusi_re(commercianti=True) + "|" + "|".join(f"({x})" for x in ESCLUSI_EXTRA), re.I)


def vende_auto(nome: str | None, categorie: str | None = None) -> bool:
    """Un'attività che vende auto? Fuori officine, carrozzerie, noleggi, ricambi, moto, camper, demolitori,
    autoscuole (dal nome o dalle categorie) e i commercianti esclusi sempre. Funzione pura."""
    n = (nome or "").strip()
    if not n or _re_esclusi().search(n.lower()) or NON_AUTO.search(n):
        return False
    if categorie and categorie.strip():
        cat = categorie.lower()
        if not VENDE_AUTO.search(cat):
            return False
        # categorie solo "fuori" (es. "Autofficine; Carrozzerie") anche se c'è "auto": fuori
        voci = [c for c in re.split(r"[,;|\n]+", cat) if c.strip()]
        if voci and all(NON_AUTO.search(c) and not VENDE_AUTO.search(c) for c in voci):
            return False
    return True


# --- telefoni -----------------------------------------------------------------------------------------------------
_ID_FISCALI = re.compile(r"(p\.?\s*iva|partita\s+iva|c\.?\s*f\.?(?=[\s:.])|cod(?:ice)?\.?\s*fisc\w*|vat|"
                         r"r\.?\s*e\.?\s*a\.?(?=[\s:.])|n\.?\s*iscr\w*|cap(?:itale)?\.?\s*soc\w*)\s*[:.\-n°]*\s*(?:it)?\s*[\d .]{5,20}",
                         re.I)


def telefoni(testo: str) -> tuple[list, list]:
    """(cellulari, fissi) da un testo, senza partite IVA / codici fiscali / REA. Funzione pura."""
    from .lead import TEL
    from .lead_comuni import cell_validi, fissi
    t = _ID_FISCALI.sub(" ", testo or "")
    return cell_validi(TEL.findall(t)), fissi(t)[:4]


def _testo(html: str) -> str:
    h = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))).strip()


NO_SITO = re.compile(r"subito\.it|adevinta|paginegialle|paginebianche|italiaonline|pgcasa|virgilio|tuttocitta|"
                     r"facebook|instagram|google|youtube|tiktok|linkedin|whatsapp|wa\.me|twitter|x\.com|apple\.com|"
                     r"autoscout24|automobile\.it|schema\.org|w3\.org|iubenda|cookie|cloudflare|gstatic|doubleclick", re.I)


def _sito_ok(u) -> str | None:
    if not isinstance(u, str):
        return None
    u = u.strip()
    if u.startswith("www."):
        u = "https://" + u
    if not re.match(r"https?://[^/\s]+\.[a-z]{2,}", u, re.I) or NO_SITO.search(u):
        return None
    return u


# --- fonte 1: Subito --------------------------------------------------------------------------------------------
def negozi_da_annunci(ads: list, zona: dict, prov: str) -> list[dict]:
    """Dagli annunci dell'interfaccia di ricerca Subito i negozi (attività con vetrina, shop_id) della zona.
    Mai privati: senza company/type 1 o senza shop_id l'annuncio non conta. Funzione pura."""
    out, visti = [], set()
    for it in ads or []:
        adv = (it or {}).get("advertiser") or {}
        if not (adv.get("company") is True or str(adv.get("type")) == "1"):
            continue
        sid = str(adv.get("shop_id") or "").strip()
        if not sid or sid in visti:
            continue
        geo = it.get("geo") or {}
        town = geo.get("town") if isinstance(geo.get("town"), dict) else {}
        citta = str((town or {}).get("value") or "")
        z = zona.get(slug(citta))
        if not z and prov not in ("MI", "MB"):
            continue                                       # fuori dai 60 km
        visti.add(sid)
        nome = str(adv.get("shop_name") or adv.get("name") or "").strip()
        out.append({"sid": sid, "nome": nome[:150], "comune": (z[0] if z else citta) or None,
                    "prov": z[1] if z else prov, "km": z[2] if z else (0.0 if slug(citta) == "milano" else None)})
    return out


_K_TEL = re.compile(r"phone|telefon|mobile|cellular|^tel$|^fax$", re.I)
_K_SITO = {"website", "sito", "site", "web", "url_sito", "websiteurl", "homepage", "sitoweb", "web_site", "siteurl"}
_K_CITTA = {"addresslocality", "city", "town", "comune", "municipality", "locality", "citta"}
_K_IND = {"streetaddress", "street", "indirizzo", "address_line", "addressline", "via"}
_K_NOME = ("shop_name", "shopname", "ragione_sociale", "legalname", "businessname", "company_name", "name")
_K_PROV = {"addressregion", "province", "provincia", "province_short", "sigla"}


def _valore(v) -> str:
    if isinstance(v, dict):
        for k in ("value", "label", "name", "short_name", "number", "text"):
            if isinstance(v.get(k), (str, int)):
                return str(v[k])
        return ""
    if isinstance(v, list):
        return " ; ".join(_valore(x) for x in v)
    return str(v) if isinstance(v, (str, int)) else ""


def _dati_json(obj, out: dict) -> None:
    """Cammina un JSON (dati della pagina o schema.org) e prende telefoni, sito, indirizzo, comune, nome del primo
    oggetto che ha un telefono o un indirizzo (il negozio)."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            stack.extend(reversed(cur))
            continue
        if not isinstance(cur, dict):
            continue
        chiavi = {str(k).lower(): k for k in cur}
        tel = " ; ".join(_valore(cur[k]) for lk, k in chiavi.items() if _K_TEL.search(lk) and not lk.startswith("fax"))
        if tel:
            c, f = telefoni(tel)
            out["cell"] += [x for x in c if x not in out["cell"]]
            out["fissi"] += [x for x in f if x not in out["fissi"]]
        ind = cur.get(chiavi["address"]) if "address" in chiavi else None
        if isinstance(ind, dict):
            stack.append(ind)
        elif isinstance(ind, str) and ind.strip() and not out["indirizzo"]:
            out["indirizzo"] = ind.strip()[:200]
        for lk, k in chiavi.items():
            v = cur[k]
            if (lk in _K_SITO or (lk == "url" and "@type" in cur)) and not out["sito"]:   # url: solo schema.org
                out["sito"] = _sito_ok(_valore(v) if not isinstance(v, str) else v)
            elif lk in _K_IND and not out["indirizzo"] and _valore(v).strip():
                out["indirizzo"] = _valore(v).strip()[:200]
            elif lk in _K_CITTA and not out["comune"] and _valore(v).strip():
                out["comune"] = _valore(v).strip()[:80]
            elif lk in _K_PROV and not out["prov"] and re.fullmatch(r"[A-Za-z]{2}", _valore(v).strip()):
                out["prov"] = _valore(v).strip().upper()
        if (tel or ind) and not out["nome"]:
            for k in _K_NOME:
                if k in chiavi and isinstance(cur[chiavi[k]], str) and cur[chiavi[k]].strip():
                    out["nome"] = cur[chiavi[k]].strip()[:150]
                    break
        stack.extend(reversed([v for v in cur.values() if isinstance(v, (dict, list))]))


def _json_ld(html: str) -> list:
    out = []
    for blob in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html or "", re.S | re.I):
        try:
            out.append(json.loads(blob.strip()))
        except ValueError:
            continue
    return out


def dati_vetrina(html: str) -> dict:
    """Vetrina di un negozio Subito (impresapiu): nome, comune, prov, indirizzo, sito, cellulari, fissi. Prima i dati
    strutturati (schema.org, dati della pagina), poi i link tel: e il testo visibile. Funzione pura."""
    from ..collectors.base import next_data
    out = {"nome": None, "comune": None, "prov": None, "indirizzo": None, "sito": None, "cell": [], "fissi": []}
    for j in _json_ld(html):
        _dati_json(j, out)
    try:
        nd = next_data(html or "")
    except ValueError:
        nd = None
    if nd:
        _dati_json(nd, out)
    extra = " ; ".join(re.findall(r'href="tel:([^"]+)"', html or "", re.I))
    c, f = telefoni(extra + " ; " + _testo(html))
    out["cell"] += [x for x in c if x not in out["cell"]]
    out["fissi"] += [x for x in f if x not in out["fissi"]]
    if not out["nome"]:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.S | re.I) or re.search(r"<title>(.*?)</title>", html or "", re.S | re.I)
        if m:
            out["nome"] = re.split(r"\s+[-|–]\s+", _testo(m.group(1)))[0][:150] or None
    if not out["sito"]:
        for h in re.findall(r'href="(https?://[^"#?]+)"', html or ""):
            if _sito_ok(h):
                out["sito"] = _sito_ok(h)
                break
    if out["comune"] and not out["prov"]:
        m = re.search(r"\(([A-Z]{2})\)", out["comune"])
        if m:
            out["prov"] = m.group(1)
    if out["comune"]:
        out["comune"] = re.sub(r"\s*\([A-Z]{2}\)\s*", "", out["comune"]).strip() or None
    out["cell"], out["fissi"] = out["cell"][:4], out["fissi"][:4]
    return out


# --- fonte 2: Pagine Gialle --------------------------------------------------------------------------------------
_RE_CAP_COMUNE = re.compile(r"(\d{5})\s+([A-Za-zÀ-ÿ'’. -]+?)\s*\(([A-Z]{2})\)")


def _classe(blocco: str, nome_classe: str) -> str:
    m = re.search(r'<(\w+)[^>]*class="[^"]*' + re.escape(nome_classe) + r'[^"]*"[^>]*>(.*?)</\1>', blocco, re.S | re.I)
    return _testo(m.group(2)) if m else ""


def _voce_pg(blocco: str) -> dict | None:
    nome = (_classe(blocco, "search-itm__rag") or _classe(blocco, 'itemprop="name"')
            or (lambda m: _testo(m.group(1)) if m else "")(re.search(r"<h[23][^>]*>(.*?)</h[23]>", blocco, re.S | re.I)))
    if not nome:
        m = re.search(r'itemprop="name"[^>]*>(.*?)<', blocco, re.S | re.I)
        nome = _testo(m.group(1)) if m else ""
    if not nome:
        return None
    ind = _classe(blocco, "search-itm__adr") or _classe(blocco, "address")
    if not ind:
        m = re.search(r'itemprop="streetAddress"[^>]*>(.*?)<', blocco, re.S | re.I)
        ind = _testo(m.group(1)) if m else ""
    comune = prov = None
    m = _RE_CAP_COMUNE.search(ind) or _RE_CAP_COMUNE.search(_testo(blocco))
    if m:
        comune, prov = m.group(2).strip(), m.group(3)
    else:
        m = re.search(r'itemprop="addressLocality"[^>]*>(.*?)<', blocco, re.S | re.I)
        if m:
            comune = _testo(m.group(1)) or None
        m = re.search(r'itemprop="addressRegion"[^>]*>\s*([A-Z]{2})\s*<', blocco)
        prov = m.group(1) if m else None
    cat = _classe(blocco, "search-itm__category") or _classe(blocco, "categor")
    attr_tel = " ; ".join(m[2] for m in re.findall(r'data-([\w-]*(?:tel|phone)[\w-]*)=(["\'])(.*?)\2', blocco, re.I | re.S))
    attr_tel += " ; " + " ; ".join(re.findall(r'href="tel:([^"]+)"', blocco, re.I))
    c, f = telefoni(unescape(attr_tel) + " ; " + _testo(blocco))
    sito = None
    for h in re.findall(r'href="(https?://[^"#]+)"', blocco):
        if _sito_ok(h.split("?")[0]):
            sito = _sito_ok(h.split("?")[0])
            break
    indirizzo = re.sub(r"\s+", " ", ind).strip()[:200] or None
    return {"nome": nome.strip()[:150], "comune": comune, "prov": prov, "indirizzo": indirizzo, "categorie": cat or None,
            "cell": c, "fissi": f, "sito": sito}


def voci_paginegialle(html: str) -> list[dict]:
    """Le attività di una pagina di risultati di Pagine Gialle: nome, comune, prov, indirizzo, categorie, cellulari,
    fissi, sito. Blocchi "search-itm" (o schema.org LocalBusiness se i blocchi non ci sono). Funzione pura."""
    parti = re.split(r'<(?:div|article|li|section)\b[^>]*class="[^"]*\bsearch-itm\b(?!__)[^"]*"', html or "")[1:]
    out = [v for v in (_voce_pg(p) for p in parti) if v]
    if out:
        return out
    for j in _json_ld(html):
        cand = j if isinstance(j, list) else (j.get("itemListElement") or j.get("@graph") or [j]) if isinstance(j, dict) else []
        for x in cand:
            x = x.get("item", x) if isinstance(x, dict) else x
            if not isinstance(x, dict) or not x.get("name"):
                continue
            d = {"nome": None, "comune": None, "prov": None, "indirizzo": None, "sito": None, "cell": [], "fissi": []}
            _dati_json(x, d)
            d["nome"] = str(x["name"]).strip()[:150]
            d["categorie"] = None
            out.append(d)
    return out


def pg_bloccata(status: int, html: str) -> bool:
    if status in (403, 429, 503):
        return True
    t = (html or "")[:5000].lower()
    return status == 200 and ("captcha" in t or "access denied" in t or "are you a robot" in t)


# --- rete (sblocco come i collettori) ------------------------------------------------------------------------------
class Rete:
    """Pagine e interfaccia di ricerca come SubitoCollector: client e pause del collettore; con BRIGHTDATA_UNLOCKER_ZONE
    e BRIGHTDATA_API_KEY le pagine passano dal Web Unlocker (sempre per Pagine Gialle, per le vetrine se bloccate)."""

    def __init__(self):
        from ..collectors.subito import SubitoCollector
        self.sub = SubitoCollector()
        self.sub.min_delay_s = float(os.environ.get("RNUM_PAUSA_MIN", "1.2"))
        self.sub.max_delay_s = float(os.environ.get("RNUM_PAUSA_MAX", "2.4"))
        self.lock = threading.Lock()

    def sblocco(self) -> bool:
        return bool(self.sub.unlocker_zone and self.sub.unlocker_key)

    def api(self, params: dict) -> list:
        r = self.sub._api(params)
        self.sub.pause()
        return ((r.json() or {}).get("ads") or []) if r.status_code == 200 else []

    def pagina(self, url: str, sblocco: bool) -> tuple[int, str]:
        if sblocco and self.sblocco():
            r = self.sub.client.post("https://api.brightdata.com/request",
                                     headers={"Authorization": f"Bearer {self.sub.unlocker_key}"},
                                     json={"zone": self.sub.unlocker_zone, "url": url, "format": "raw", "country": "it"},
                                     timeout=120)
            code = 403 if r.headers.get("x-brd-error") else r.status_code
        else:
            r = self.sub.client.get(url)
            code = r.status_code
        self.sub.pause()
        return code, r.text or ""


# --- archivio (passi fatti e righe) --------------------------------------------------------------------------------
SQL_PASSI = """CREATE TABLE IF NOT EXISTS raccolta_numeri_passi (
    etichetta TEXT NOT NULL,
    passo     TEXT NOT NULL,
    dati      JSONB,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (etichetta, passo)
)"""


class Archivio:
    """Nel database: i passi fatti (raccolta_numeri_passi, solo identificativi e nomi di attività) e le righe dei
    saloni (lead_consegne, come raccolta60)."""

    def __init__(self, etichetta: str):
        self.etichetta = etichetta

    def passi(self) -> dict:
        from ..db import connect
        with connect() as c:
            c.execute(SQL_PASSI)
            c.commit()
            return {r["passo"]: r["dati"] for r in c.execute(
                "SELECT passo, dati FROM raccolta_numeri_passi WHERE etichetta=%s", (self.etichetta,)).fetchall()}

    def segna(self, passo: str, dati=None) -> None:
        from ..db import connect
        with connect() as c:
            c.execute("INSERT INTO raccolta_numeri_passi (etichetta, passo, dati) VALUES (%s,%s,%s) ON CONFLICT "
                      "(etichetta, passo) DO UPDATE SET dati=EXCLUDED.dati, at=now()",
                      (self.etichetta, passo[:300], json.dumps(dati, ensure_ascii=False) if dati is not None else None))
            c.commit()

    def salva(self, riga: dict) -> None:
        from ..marketing.consegne import consegna
        consegna(TIPO_GREZZE, riga)

    def grezze(self) -> list[dict]:
        from ..db import connect
        from ..marketing.consegne import righe
        with connect() as c:
            return [r for r in righe(c, TIPO_GREZZE, 100000) if r.get("etichetta") == self.etichetta]

    def finale(self, righe: list[dict]) -> None:
        from ..marketing.consegne import consegna
        for r in righe:
            consegna(TIPO_FINALE, r)

    def noti(self) -> set:
        """Numeri (ultime 9 cifre) già raccolti: righe R60/R60T e lead_tutti_dealer."""
        from ..db import connect
        out: set = set()
        with connect() as c:
            for tipo in ("R60", "R60T"):
                try:
                    for r in c.execute("SELECT dati FROM lead_consegne WHERE tipo=%s", (tipo,)).fetchall():
                        d = r["dati"] if isinstance(r["dati"], dict) else json.loads(r["dati"])
                        out |= {_n9(x) for x in re.findall(r"\d{8,13}", f"{d.get('cellulari') or ''} {d.get('fissi') or ''}")}
                except Exception:
                    c.rollback()
            try:
                for r in c.execute("SELECT cell FROM lead_tutti_dealer").fetchall():
                    out |= {_n9(x) for x in (r["cell"] or [])}
            except Exception:
                c.rollback()
        out.discard(None)
        return out


def _n9(t) -> str | None:
    d = re.sub(r"\D", "", str(t or ""))
    if d.startswith("39") and len(d) > 10:
        d = d[2:]
    return d[-9:] if len(d) >= 8 else None


def _h(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:16]


# --- il lavoro ---------------------------------------------------------------------------------------------------
class Raccolta:
    def __init__(self, etichetta: str, scadenza: float, archivio=None, rete=None, zona: dict | None = None):
        self.etichetta = etichetta
        self.scadenza = scadenza
        self.arch = archivio or Archivio(etichetta)
        self.rete = rete
        self.zona = zona if zona is not None else comuni_zona()
        self.fatti = self.arch.passi()
        self.lock = threading.Lock()
        self.st = Counter()
        self.http = {"subito": Counter(), "vetrine": Counter(), "paginegialle": Counter()}
        self.completa = {"subito": False, "paginegialle": False}
        self.pg_bloccata = False

    def tempo(self) -> bool:
        return time.time() < self.scadenza

    def _segna(self, passo: str, dati=None) -> None:
        self.arch.segna(passo, dati)
        self.fatti[passo] = dati

    def _salva(self, fonte: str, d: dict, link: str | None, comune_cerca=None) -> bool:
        """Una riga grezza se l'attività vende auto, è nella zona e ha almeno un numero."""
        if not vende_auto(d.get("nome"), d.get("categorie")):
            self.st[f"{fonte}_esclusi"] += 1
            return False
        comune, prov = d.get("comune"), (d.get("prov") or "").upper() or None
        z = self.zona.get(slug(comune or ""))
        if z:
            comune, prov, km = z[0], z[1], z[2]
        elif prov in ("MI", "MB"):
            km = 0.0 if slug(comune or "") == "milano" else None
        elif comune_cerca:                      # comune non riconosciuto: quello della ricerca/annuncio
            comune, prov, km = comune or comune_cerca[0], prov or comune_cerca[1], comune_cerca[2]
            if prov and prov != comune_cerca[1]:
                self.st[f"{fonte}_fuori_zona"] += 1
                return False
        else:
            self.st[f"{fonte}_fuori_zona"] += 1
            return False
        if not (d.get("cell") or d.get("fissi")):
            self.st[f"{fonte}_senza_numero"] += 1
            return False
        self.arch.salva({"etichetta": self.etichetta, "fonte": fonte, "nome": d.get("nome"), "comune": comune,
                         "prov": prov, "km": km, "indirizzo": d.get("indirizzo"), "sito": d.get("sito"),
                         "cell": d.get("cell") or [], "fissi": d.get("fissi") or [], "link": link})
        with self.lock:
            self.st[f"{fonte}_salvati"] += 1
            self.st[f"{fonte}_con_cell"] += bool(d.get("cell"))
        return True

    # fonte 1
    def _elenco_provincia(self, prov: str, reg: int) -> list[dict]:
        from ..collectors.subito import PROVINCE_KEYS_NOTE
        passo = f"elenco:{prov}"
        if passo in self.fatti:
            return self.fatti[passo] or []
        ci = PROVINCE_KEYS_NOTE.get(reg, {}).get(prov)
        if not ci:
            return []
        negozi: dict = {}
        max_pag = int(os.environ.get("RNUM_SUBITO_PAGINE", "40"))
        for ps, pe in BANDE:
            vuote = 0
            for page in range(max_pag):
                if not self.tempo():
                    return []                    # elenco non finito: si rifà al giro dopo
                try:
                    ads = self.rete.api({"c": 2, "r": reg, "ci": ci, "t": "s", "ps": ps, "pe": pe, "lim": 100,
                                         "start": page * 100, "sort": "datedesc"})
                    self.http["subito"]["ok"] += 1
                except Exception:
                    self.http["subito"]["errore"] += 1
                    ads = []
                self.st["annunci_subito"] += len(ads)
                nuovi = 0
                for n in negozi_da_annunci(ads, self.zona, prov):
                    if n["sid"] not in negozi:
                        negozi[n["sid"]] = n
                        nuovi += 1
                vuote = 0 if nuovi else vuote + 1
                if len(ads) < 100 or vuote >= int(os.environ.get("RNUM_PAGINE_VUOTE", "3")):
                    break
        lista = list(negozi.values())
        self._segna(passo, lista)
        return lista

    def subito(self) -> None:
        bloccate = 0
        for prov, reg in PROVINCE_SUBITO:
            if not self.tempo():
                return
            negozi = self._elenco_provincia(prov, reg)
            self.st["negozi_subito"] += len(negozi)
            for n in negozi:
                passo = f"vetrina:{n['sid']}"
                if passo in self.fatti:
                    continue
                if not self.tempo():
                    return
                url = VETRINA.format(quote(n["sid"]))
                try:
                    code, html = self.rete.pagina(url, sblocco=False)
                    if code in (403, 429) and self.rete.sblocco():
                        code, html = self.rete.pagina(url, sblocco=True)
                except Exception:
                    code, html = 0, ""
                self.http["vetrine"][code] += 1
                if code in (403, 429, 0):
                    bloccate += 1
                    if bloccate >= int(os.environ.get("RNUM_SUB_BLOCCHI_MAX", "20")):
                        log.warning("RNUM vetrine Subito bloccate %d volte di fila: stop della fonte 1 per questo giro (%s)",
                                    bloccate, dict(self.http["vetrine"]))
                        return
                    time.sleep(float(os.environ.get("RNUM_PAUSA_BLOCCO", "30")))
                    continue
                bloccate = 0
                if code == 200:
                    d = dati_vetrina(html)
                    d["nome"] = n["nome"] or d["nome"]          # il nome del negozio su Subito
                    d["comune"] = d["comune"] or n["comune"]
                    d["prov"] = d["prov"] or n["prov"]
                    self.st["vetrine_lette"] += 1
                    self._salva("subito", d, url, (n["comune"], n["prov"], n["km"]))
                self._segna(passo)
                if self.st["vetrine_lette"] and self.st["vetrine_lette"] % 200 == 0:
                    log.warning("RNUM vetrine Subito: %d lette, %d salvate, risposte %s", self.st["vetrine_lette"],
                                self.st["subito_salvati"], dict(self.http["vetrine"]))
            log.warning("RNUM Subito provincia %s: %d negozi", prov, len(negozi))
        self.completa["subito"] = True

    # fonte 2
    def _comuni_pg(self) -> list[tuple]:
        return sorted({(v[0], v[1], v[2]) for v in self.zona.values()}, key=lambda x: (x[2] if x[2] is not None else 5, x[0]))

    def paginegialle(self) -> None:
        if os.environ.get("RNUM_PG", "1") == "0":
            self.completa["paginegialle"] = True
            return
        di_fila = 0
        max_bl = int(os.environ.get("RNUM_PG_BLOCCHI_MAX", "8"))
        max_pag = int(os.environ.get("RNUM_PG_PAGINE", "5"))
        tetto = int(os.environ.get("RNUM_PG_MAX_RICHIESTE", "6000"))      # spesa dello sblocco
        for comune, prov, km in self._comuni_pg():
            for cat in PG_CATEGORIE:
                passo = f"pg:{slug(cat)}:{slug(comune)}:{prov}"
                if passo in self.fatti:
                    continue
                visti: set = set()
                finito = True
                for p in range(1, max_pag + 1):
                    if not self.tempo():
                        return
                    if sum(self.http["paginegialle"].values()) >= tetto:
                        log.warning("RNUM Pagine Gialle: tetto di %d richieste raggiunto, stop", tetto)
                        self.completa["paginegialle"] = True
                        return
                    url = PG_URL.format(cat=quote(cat), dove=quote(f"{comune} ({prov})")) + (f"/p-{p}" if p > 1 else "")
                    try:
                        code, html = self.rete.pagina(url, sblocco=True)
                    except Exception:
                        code, html = 0, ""
                    self.http["paginegialle"][code] += 1
                    if pg_bloccata(code, html) or code == 0:
                        di_fila += 1
                        if di_fila >= max_bl:
                            self.pg_bloccata = True
                            self.completa["paginegialle"] = True
                            log.warning("RNUM Pagine Gialle BLOCCATA anche dallo sblocco (%d risposte bloccate di fila, "
                                        "sblocco %s): continuo solo con le vetrine Subito. Risposte %s", di_fila,
                                        "attivo" if self.rete.sblocco() else "NON configurato",
                                        dict(self.http["paginegialle"]))
                            return
                        time.sleep(float(os.environ.get("RNUM_PAUSA_BLOCCO", "30")))
                        finito = False                 # passo non segnato: si riprova al giro dopo
                        break
                    di_fila = 0
                    voci = voci_paginegialle(html) if code == 200 else []
                    nuove = 0
                    for v in voci:
                        k = _h(nn(v["nome"]) + "|" + slug(v.get("comune") or comune))
                        if k in visti:
                            continue
                        visti.add(k)
                        nuove += 1
                        self.st["voci_pg"] += 1
                        pk = f"pgv:{k}"
                        if pk in self.fatti:
                            continue
                        if self._salva("paginegialle", v, None, (comune, prov, km)):
                            self._segna(pk)
                    if code != 200 or not nuove or len(voci) < 10:
                        break
                if finito:
                    self._segna(passo)
            self.st["comuni_pg"] += 1
            if self.st["comuni_pg"] % 50 == 0:
                log.warning("RNUM Pagine Gialle: %d comuni, %d voci, %d salvate, risposte %s", self.st["comuni_pg"],
                            self.st["voci_pg"], self.st["paginegialle_salvati"], dict(self.http["paginegialle"]))
        self.completa["paginegialle"] = True


def unisci_righe(grezze: list[dict], noti: set | None = None) -> list[dict]:
    """Righe finali (una per salone): unite per telefono in comune o per nome normalizzato nella stessa provincia;
    esclusi di nuovo i nomi da non scrivere. nuovo = almeno un numero non già in `noti`. Funzione pura."""
    noti = noti or set()
    gruppi: list[dict] = []
    per_tel: dict = {}
    per_nome: dict = {}
    for r in grezze:
        if not vende_auto(r.get("nome")):
            continue
        tels = [_n9(x) for x in (r.get("cell") or []) + (r.get("fissi") or [])]
        kn = (nn(r.get("nome")), (r.get("prov") or "").upper()) if nn(r.get("nome")) else None
        g = next((per_tel[t] for t in tels if t in per_tel), None) or (per_nome.get(kn) if kn else None)
        if g is None:
            g = {"nome": r.get("nome"), "comune": r.get("comune"), "prov": r.get("prov"), "km": r.get("km"),
                 "cell": [], "fissi": [], "indirizzo": None, "sito": None, "fonti": [], "link": None}
            gruppi.append(g)
        for f in ("cell", "fissi"):
            g[f] += [x for x in (r.get(f) or []) if x and x not in g[f]]
        for f in ("indirizzo", "sito", "link", "comune", "prov"):
            g[f] = g[f] or r.get(f)
        if g["km"] is None:
            g["km"] = r.get("km")
        if r.get("fonte") and r["fonte"] not in g["fonti"]:
            g["fonti"].append(r["fonte"])
        for t in tels:
            if t:
                per_tel[t] = g
        if kn:
            per_nome[kn] = g
    out = []
    for g in gruppi:
        tutti = [_n9(x) for x in g["cell"] + g["fissi"]]
        out.append({"nome": g["nome"], "comune": g["comune"], "prov": g["prov"],
                    "km": round(g["km"], 1) if isinstance(g["km"], (int, float)) else None,
                    "cellulare": g["cell"][0] if g["cell"] else "", "altri_cellulari": " ".join(g["cell"][1:4]),
                    "fisso": g["fissi"][0] if g["fissi"] else "", "altri_fissi": " ".join(g["fissi"][1:4]),
                    "indirizzo": g["indirizzo"], "sito": g["sito"], "fonte": "+".join(sorted(g["fonti"])),
                    "nuovo": any(t and t not in noti for t in tutti), "link": g["link"]})
    out.sort(key=lambda x: (x["km"] if x["km"] is not None else (5 if x["prov"] == "MI" else 20), x["prov"] or "",
                            x["comune"] or "", x["nome"] or ""))
    return out


def _brevo(righe: list[dict], etichetta: str) -> int:
    if os.environ.get("RNUM_BREVO", "1") != "1" or not righe:
        return 0
    from .raccolta60 import _brevo_copie
    # stesso formato dei modelli R60 (non attivi, non mandano email), nome "R60 RNUM-<etichetta> dati k/n"
    return _brevo_copie(righe, f"RNUM-{etichetta}")


def lavoro(etichetta: str, archivio=None, rete=None, zona=None, ore: float | None = None) -> dict:
    """Un giro (fino a RNUM_ORE ore). Ritorna l'esito (solo conteggi); "da_riprendere": True se il tempo è finito
    prima di tutte e due le fonti: chi chiama rimanda il lavoro al giro dopo."""
    ore = float(ore if ore is not None else os.environ.get("RNUM_ORE", "3"))
    R = Raccolta(etichetta, time.time() + ore * 3600, archivio=archivio, rete=rete or Rete(), zona=zona)
    giro = int((R.fatti.get("giri") or {}).get("n", 0)) + 1 if isinstance(R.fatti.get("giri"), dict) else 1
    R._segna("giri", {"n": giro})
    log.warning("RNUM parte (giro %d): %d comuni entro 60 km, %d passi già fatti, fino a %.1f ore, sblocco %s", giro,
                len(R.zona), len(R.fatti) - 1, ore, "attivo" if R.rete.sblocco() else "non configurato")
    def sicuro(fn, nome):
        def f():
            try:
                fn()
            except Exception as e:          # la fonte resta "non completa": si riprende al giro dopo
                R.st[f"errore_{nome}"] += 1
                log.warning("RNUM %s interrotta: %s", nome, type(e).__name__)
        return f

    fili = [threading.Thread(target=sicuro(R.subito, "subito"), daemon=True, name="rnum-subito"),
            threading.Thread(target=sicuro(R.paginegialle, "paginegialle"), daemon=True, name="rnum-pg")]
    for t in fili:
        t.start()
    for t in fili:
        t.join()
    esito = {"giro": giro, **{k: v for k, v in R.st.items()}, "http": {k: dict(v) for k, v in R.http.items()},
             "fonti_complete": dict(R.completa), "pagine_gialle_bloccata": R.pg_bloccata}
    if not all(R.completa.values()) and giro < int(os.environ.get("RNUM_GIRI_MAX", "5")):
        esito["da_riprendere"] = True
        log.warning("RNUM_GIRO %s", json.dumps(esito, ensure_ascii=False))
        return esito
    grezze = R.arch.grezze()
    try:
        noti = R.arch.noti()
    except Exception:
        noti = set()
    righe = unisci_righe(grezze, noti)
    R.arch.finale(righe)
    esito.update({"righe_grezze": len(grezze), "saloni": len(righe), "con_cellulare": sum(1 for r in righe if r["cellulare"]),
                  "con_fisso": sum(1 for r in righe if r["fisso"]), "nuovi": sum(1 for r in righe if r["nuovo"]),
                  "nuovi_con_cellulare": sum(1 for r in righe if r["nuovo"] and r["cellulare"]),
                  "modelli_brevo": _brevo(righe, etichetta) if archivio is None else 0})
    log.warning("RNUM_FINE %s", json.dumps(esito, ensure_ascii=False))
    return esito


def esegui_voce(v: str) -> bool | None:
    """Ramo della coda (coda_lavori.esegui): chiave raccolta_numeri:<etichetta>, segnata fatta solo a lavoro finito;
    a tempo finito il lavoro si RIMANDA (non conta come tentativo fallito) e il cron lo riprende la notte dopo."""
    from .ciclo import inizia
    etichetta = re.sub(r"[^A-Za-z0-9-]", "", v.split(":", 1)[1])[:20] or "1811"
    chiave = f"raccolta_numeri:{etichetta}"
    lav = inizia(chiave, tentativi_max=int(os.environ.get("RNUM_TENTATIVI", "3")))
    if not lav:
        log.warning("RNUM %s già fatto (o in corso in un altro processo)", chiave)
        return None
    try:
        esito = lavoro(etichetta)
    except Exception as e:
        lav.fallito(e)
        log.warning("RNUM non riuscito: %s", type(e).__name__)
        return False
    if esito.get("da_riprendere"):
        lav.rimanda({"giro": esito.get("giro"), "fonti_complete": esito.get("fonti_complete")})
        return False                      # codice di uscita 1: la riga resta da fare per il cron della notte dopo
    lav.fatto({k: v for k, v in esito.items() if isinstance(v, (int, float, bool))})
    return True
