"""Auto di Gala Auto pubblicate su Subito (negozio impresapiu 31907).

Le pagine HTML di Subito spesso rispondono 403 ai server cloud; l'interfaccia interna (hades)
invece risponde. Si prova in quest'ordine:
  1) pagina del negozio su impresapiu (se risponde, dentro ci sono gli annunci);
  2) ricerca hades filtrata per negozio (parametri provati uno per uno, tenuti solo se tutti gli
     annunci sono davvero del negozio);
  3) ricerca hades in provincia di Milano per parole del negozio, tenendo solo gli annunci il cui
     inserzionista è Gala Auto.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import httpx

log = logging.getLogger("gala.subito")

SHOP_ID = os.environ.get("SUBITO_SHOP_ID", "31907")
SHOP_URL = os.environ.get("SUBITO_SHOP_URL", f"https://impresapiu.subito.it/shops/{SHOP_ID}-gala-auto-snc")
NOME = re.compile(os.environ.get("SUBITO_SHOP_NOME", r"gala\s*auto"), re.I)
API = "https://hades.subito.it/v1/search/items"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/129.0.0.0 Safari/537.36")
H_HTML = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "it-IT,it;q=0.9"}
H_API = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "it-IT,it;q=0.9",
         "X-Subito-Channel": "web", "Origin": "https://www.subito.it", "Referer": "https://www.subito.it/"}
RE_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
RE_ID = re.compile(r"subito\.it/auto/([a-z0-9\-]+)-(\d{6,})\.htm")

stato: dict = {"metodo": None, "parametro": None}


def _walk(o):
    st = [o]
    while st:
        c = st.pop()
        if isinstance(c, dict):
            yield c
            st.extend(c.values())
        elif isinstance(c, list):
            st.extend(c)


def _ad_id(it: dict) -> str | None:
    u = (it.get("urls") or {}).get("default") or it.get("url") or ""
    m = re.search(r"-(\d{6,})\.htm", u)
    if m:
        return m.group(1)
    m = re.search(r"(\d{6,})$", str(it.get("urn") or ""))
    return m.group(1) if m else None


def _di_gala(it: dict) -> bool:
    adv = it.get("advertiser") or {}
    if str(adv.get("shop_id") or adv.get("shopId") or "") == SHOP_ID:
        return True
    return bool(NOME.search(str(adv.get("shop_name") or adv.get("name") or "")))


def _feats(it: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    feats = it.get("features") or {}
    for f in (feats.values() if isinstance(feats, dict) else feats):
        if not isinstance(f, dict):
            continue
        lab = str(f.get("label") or f.get("uri") or "").lower()
        vals = f.get("values") or []
        if f.get("type") == "pack" and vals and isinstance(vals[0], dict) and vals[0].get("label"):
            for v in vals:
                if isinstance(v, dict) and v.get("label") and v.get("value") is not None:
                    out[str(v["label"]).lower()] = str(v["value"])
            continue
        v = (vals[0].get("value") or vals[0].get("key")) if vals and isinstance(vals[0], dict) else f.get("value")
        if lab and v is not None:
            out[lab] = str(v)
    return out


def _pick(f: dict, *tok):
    for k, v in f.items():
        if any(t in k for t in tok):
            return v
    return None


def _num(v):
    d = re.sub(r"[^\d]", "", str(v or "").split(",")[0])
    return int(d) if d else None


def _norm(it: dict) -> dict | None:
    aid = _ad_id(it)
    if not aid:
        return None
    f = _feats(it)
    foto = []
    if not stato.get("_dbg_img"):
        stato["_dbg_img"] = True
        log.info("GALA_SB immagini esempio %s", json.dumps(it.get("images"))[:900])
    for im in it.get("images") or []:
        u = None
        if isinstance(im, str):
            u = im
        elif isinstance(im, dict):
            u = im.get("cdnBaseUrl") or im.get("cdn_base_url") or im.get("url") or im.get("uri")
            if not (isinstance(u, str) and u.startswith("http")):
                cand = [d for d in _walk(im) if isinstance(d.get("uri") or d.get("url"), str)]
                cand = [d.get("uri") or d.get("url") for d in cand]
                cand = [c for c in cand if c.startswith("http")]
                big = [c for c in cand if re.search(r"big|large|gallery|2x", c)]
                u = (big or cand or [None])[-1]
        if isinstance(u, str) and u.startswith("http"):
            if "images.sbito.it" in u and "rule=" not in u:
                u = u.split("?")[0] + "?rule=gallery-desktop-2x-auto"
            foto.append(u)
    anno = _pick(f, "immatricolazione", "anno")
    km = _pick(f, "km", "chilometr")
    a = {
        "id": "sb-" + aid,
        "fonte": "subito",
        "url": (it.get("urls") or {}).get("default") or "",
        "marca": _pick(f, "marca"),
        "modello": _pick(f, "modello"),
        "versione": _pick(f, "versione", "allestimento") or "",
        "titolo": it.get("subject") or "",
        "prezzo": _num(_pick(f, "prezzo")),
        "immatricolazione": anno,
        "km": _num(km) if km and not re.search(r"\d\s*-\s*\d", km) else None,
        "km_testo": km,
        "carburante": _pick(f, "carburante", "alimentazione"),
        "cambio": _pick(f, "cambio"),
        "potenza": _pick(f, "potenza"),
        "colore": _pick(f, "colore"),
        "porte": _pick(f, "porte"),
        "posti": _pick(f, "posti"),
        "classe": _pick(f, "classe emissioni", "euro"),
        "carrozzeria": _pick(f, "tipologia", "carrozzeria"),
        "foto": foto,
        "descrizione": (it.get("body") or "").strip()[:4000],
        "giudizio": None,
    }
    if km and a["km"] is None:                      # fascia "100.000 - 109.999": si tiene il valore centrale
        n = [int(x.replace(".", "")) for x in re.findall(r"\d[\d.]*", km)]
        if len(n) >= 2:
            a["km"] = (n[0] + n[1]) // 2
            a["km_fascia"] = True
    if a["modello"]:
        a["modello"] = re.sub(r"\s*\(.*?\)|\s*\d+\s*ª\s*s(erie|\.).*$", "", a["modello"]).strip() or a["modello"]
        a["modello"] = re.sub(r"^RR\b", "Range Rover", a["modello"])
    if a["marca"] and a["marca"].isupper():
        a["marca"] = a["marca"].title()
    t = a["titolo"]
    if not a["versione"] and a["marca"] and t.lower().startswith(a["marca"].lower()):
        a["versione"] = t[len(a["marca"]):].strip()
    if not a["marca"]:
        a["marca"] = (a["titolo"].split() or [""])[0]
    return a


def _hades(cl: httpx.Client, params: dict) -> list[dict]:
    try:
        r = cl.get(API, headers=H_API, params=params)
        if r.status_code != 200:
            log.info("GALA_SB hades %s -> HTTP %s", params, r.status_code)
            return []
        return (r.json() or {}).get("ads") or []
    except Exception as e:
        log.info("GALA_SB hades %s errore %s", params, str(e)[:120])
        return []


def _da_negozio(cl: httpx.Client) -> tuple[dict, list[str]]:
    """Pagina impresapiu: annunci completi se ci sono nel JSON, altrimenti solo gli id."""
    trovati: dict[str, dict] = {}
    ids: list[str] = []
    try:
        r = cl.get(SHOP_URL, headers=H_HTML)
        log.info("GALA_SB negozio HTTP %s, %d byte", r.status_code, len(r.text or ""))
        if r.status_code != 200:
            return trovati, ids
        ids = list(dict.fromkeys(RE_ID.findall(r.text)))        # [(slug, id)]
        m = RE_NEXT.search(r.text)
        data = json.loads(m.group(1)) if m else {}
        for d in _walk(data):
            if (d.get("urn") or d.get("urls")) and d.get("subject") and d.get("features"):
                a = _norm(d)
                if a:
                    trovati[a["id"]] = a
        log.info("GALA_SB negozio: %d id nella pagina, %d annunci completi", len(ids), len(trovati))
    except Exception as e:
        log.info("GALA_SB negozio errore %s", str(e)[:160])
    return trovati, ids


def leggi() -> list[dict]:
    """Tutte le auto di Gala su Subito, già nel formato del sito."""
    trovati: dict[str, dict] = {}
    with httpx.Client(timeout=30, follow_redirects=True) as cl:
        dal_negozio, ids = _da_negozio(cl)
        trovati.update(dal_negozio)

        # 2) filtro per negozio sull'interfaccia hades
        base = {"c": 2, "t": "s", "lim": 100, "sort": "datedesc"}
        prove = [stato["parametro"]] if stato.get("parametro") else ["shp", "shop_id", "shop", "sid"]
        for p in prove:
            ads = _hades(cl, {**base, p: SHOP_ID})
            buoni = [a for a in ads if _di_gala(a)]
            log.info("GALA_SB prova %s=%s: %d annunci, %d di Gala", p, SHOP_ID, len(ads), len(buoni))
            if ads and len(buoni) == len(ads):
                stato["parametro"] = p
                for it in buoni:
                    a = _norm(it)
                    if a:
                        trovati[a["id"]] = a
                break
            time.sleep(0.6)

        # 3) ricerca per parole in provincia di Milano (Rho), tenendo solo gli annunci di Gala
        if not stato.get("parametro"):
            for q in ("gala auto", "gala", "rho"):
                for page in range(6):
                    ads = _hades(cl, {**base, "r": 4, "ci": 8, "q": q, "start": page * 100})
                    for it in ads:
                        if _di_gala(it):
                            a = _norm(it)
                            if a:
                                trovati[a["id"]] = a
                    if len(ads) < 100:
                        break
                    time.sleep(0.6)
            log.info("GALA_SB ricerca per parole: %d annunci di Gala", len(trovati))
        # 4) annunci visti nella pagina del negozio ma non ancora letti: ricerca per titolo
        mancanti = [(s, i) for s, i in ids if "sb-" + i not in trovati]
        for slug, i in mancanti[:40]:
            q = " ".join(w for w in slug.split("-") if w not in ("milano", "rho"))[:80]
            for it in _hades(cl, {**base, "r": 4, "q": q}):
                if _ad_id(it) == i:
                    a = _norm(it)
                    if a:
                        trovati[a["id"]] = a
            time.sleep(0.6)
        if mancanti:
            log.info("GALA_SB id del negozio: %d, non trovati: %s", len(ids),
                     [i for _, i in ids if "sb-" + i not in trovati][:30])
    auto = [a for a in trovati.values() if a.get("prezzo")]
    log.info("GALA_SB totale %d auto da Subito (metodo parametro=%s)", len(auto), stato.get("parametro"))
    for a in auto:
        log.info("GALA_SB auto %s | %s %s | %s€ | km=%s | anno=%s | foto=%d", a["id"], a["marca"], a["modello"],
                 a["prezzo"], a.get("km_testo"), a.get("immatricolazione"), len(a["foto"]))
    return auto
