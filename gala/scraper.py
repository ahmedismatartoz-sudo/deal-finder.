"""Legge le auto in vendita dalla pagina AutoScout24 del concessionario e le salva in data.json.

Gira in automatico ogni REFRESH_HOURS ore: auto nuove entrano, auto vendute escono.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("atelier")
BASE = "https://www.autoscout24.it"
DEALER_URL = os.environ.get("DEALER_URL", BASE + "/concessionari/carrozzeria-atelier-srl")
DATA = Path(os.environ.get("DATA_FILE", Path(__file__).parent / "data.json"))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
RE_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

stato = {"ultimo": None, "errore": None, "in_corso": False, "note": []}
_lock = threading.Lock()


def _client():
    return httpx.Client(timeout=30, follow_redirects=True,
                        headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                                 "Accept": "text/html,application/xhtml+xml"})


def _next(html: str) -> dict:
    m = RE_NEXT.search(html or "")
    try:
        return json.loads(m.group(1)) if m else {}
    except Exception:
        return {}


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    d = re.sub(r"[^\d]", "", str(v))
    return int(d) if d else None


def _walk(o):
    st = [o]
    while st:
        c = st.pop()
        if isinstance(c, dict):
            yield c
            st.extend(c.values())
        elif isinstance(c, list):
            st.extend(c)


def _sembra_annuncio(d: dict) -> bool:
    return bool(d.get("id")) and isinstance(d.get("url"), str) and "/annunci/" in d.get("url", "") \
        and (isinstance(d.get("vehicle"), dict) or isinstance(d.get("tracking"), dict))


def _img(u: str, size="1280x960") -> str:
    return re.sub(r"/\d+x\d+(\.(webp|jpg|jpeg|png))?$", f"/{size}.webp", u)


def _find(o, *names):
    for d in _walk(o):
        for n in names:
            v = d.get(n)
            if v not in (None, "", [], {}):
                return v
    return None


def _prezzo(it: dict):
    for src in (it.get("prices"), it.get("price"), it.get("tracking")):
        if isinstance(src, dict):
            for d in _walk(src):
                for k in ("priceRaw", "rawPrice", "amount", "priceValue"):
                    if isinstance(d.get(k), (int, float)) and d[k] > 0:
                        return int(d[k])
    for src in (it.get("tracking") or {}, it.get("price"), it.get("prices")):
        if isinstance(src, (int, float)) and src > 0:
            return int(src)
        if isinstance(src, str):
            return _num(src.split(",")[0])
        if isinstance(src, dict):
            for d in _walk(src):
                for k in ("price", "priceFormatted", "formatted", "value"):
                    v = d.get(k)
                    if isinstance(v, (int, float)) and v > 0:
                        return int(v)
                    if isinstance(v, str) and re.search(r"\d", v):
                        return _num(v.split(",")[0])
    return None


def _norm(it: dict) -> dict:
    veh = it.get("vehicle") or {}
    trk = it.get("tracking") or {}
    price = it.get("price") or {}
    det = {str(d.get("iconName") or ""): d.get("data") for d in (it.get("vehicleDetails") or []) if isinstance(d, dict)}
    url = it.get("url") or ""
    imgs = [i for i in (it.get("images") or []) if isinstance(i, str) and i.startswith("http")]
    p = _prezzo(it)
    ev = ((it.get("prices") or {}).get("public") or {}).get("priceEvaluation")
    lab = {1: "top", 2: "good"}.get(ev)
    return {
        "id": str(it.get("id")),
        "url": (BASE + url) if url.startswith("/") else url,
        "marca": veh.get("make"), "modello": veh.get("model"),
        "versione": veh.get("modelVersionInput") or veh.get("variant") or "",
        "prezzo": p,
        "immatricolazione": det.get("calendar") or trk.get("firstRegistration"),
        "km": _num(trk.get("mileage")) or _num(veh.get("mileageInKm")) or _num(det.get("mileage_road")),
        "carburante": veh.get("fuel") or det.get("gas_pump"),
        "cambio": veh.get("transmission") or det.get("transmission"),
        "potenza": det.get("speedometer") or veh.get("power"),
        "proprietari": det.get("person") or None,
        "foto": [_img(i) for i in imgs],
        "giudizio": lab,
    }


def _dettaglio(cl: httpx.Client, a: dict) -> dict:
    """Pagina dell'annuncio: tutte le foto, descrizione, dotazioni, colore."""
    try:
        r = cl.get(a["url"])
        if r.status_code != 200:
            return a
        props = (_next(r.text).get("props") or {}).get("pageProps") or {}
        d = props.get("listingDetails") or props.get("listing") or props
        if not stato.get("_dbg"):
            stato["_dbg"] = True
            log.info("ATELIER dettaglio chiavi=%s", list(d)[:40] if isinstance(d, dict) else type(d))
        if not a.get("prezzo"):
            a["prezzo"] = _prezzo(d)
        foto = []
        for im in (d.get("images") or []) if isinstance(d, dict) else []:
            u = im if isinstance(im, str) else (im.get("url") or im.get("src") if isinstance(im, dict) else None)
            if isinstance(u, str) and u.startswith("http"):
                foto.append(_img(u))
        if len(foto) > len(a["foto"]):
            a["foto"] = foto
        desc = _find(d, "description")
        if isinstance(desc, str):
            a["descrizione"] = re.sub(r"<br\s*/?>", "\n", desc)
            a["descrizione"] = re.sub(r"<[^>]+>", "", a["descrizione"]).strip()[:4000]
        veh = d.get("vehicle") if isinstance(d, dict) and isinstance(d.get("vehicle"), dict) else {}
        fmt = lambda v: (v.get("formatted") if isinstance(v, dict) else v) or None
        if veh:
            a["km"] = _num(veh.get("mileageInKmRaw")) or a.get("km")
            a["immatricolazione"] = veh.get("firstRegistrationDate") or a.get("immatricolazione")
            a["carburante"] = fmt(veh.get("primaryFuel")) or fmt(veh.get("fuelCategory")) or a.get("carburante")
            a["cambio"] = veh.get("transmissionType") or a.get("cambio")
            if veh.get("powerInKw") or veh.get("powerInHp"):
                a["potenza"] = " ".join(x for x in (veh.get("powerInKw"), f"({veh['powerInHp']})" if veh.get("powerInHp") else None) if x)
            a["versione"] = veh.get("modelVersionInput") or a.get("versione")
            a["marca"] = veh.get("make") or a.get("marca")
            a["modello"] = veh.get("model") or a.get("modello")
            for k_out, k in (("colore", "bodyColor"), ("carrozzeria", "bodyType"), ("porte", "numberOfDoors"), ("posti", "numberOfSeats"),
                             ("cilindrata", "displacementInCCM"), ("trazione", "driveTrain"), ("interni", "upholstery")):
                if veh.get(k) not in (None, "", {}):
                    a[k_out] = str(veh[k])
            cons = fmt(veh.get("fuelConsumptionCombined"))
            if cons:
                a["consumo"] = cons

        for k_out, keys in (("colore", ("bodyColor", "color", "bodyColorOriginal")),
                            ("carrozzeria", ("bodyType",)), ("porte", ("numberOfDoors",)),
                            ("posti", ("numberOfSeats",)), ("cilindrata", ("displacementInCCM", "displacement")),
                            ("classe", ("emissionClass", "environmentEuDirective")),
                            ("trazione", ("driveTrain",)), ("consumo", ("combinedConsumption",))):
            v = None
            for k in keys:
                v = veh.get(k) if veh.get(k) not in (None, "", {}) else _find(veh, k)
                if v not in (None, "", {}):
                    break
            if isinstance(v, dict):
                v = v.get("formatted") or v.get("value") or v.get("raw")
            if v not in (None, "", {}, []) and not a.get(k_out):
                a[k_out] = str(v)
        eq = veh.get("equipment") or _find(d, "equipment")
        dot = []
        if isinstance(eq, dict):
            for gruppo in eq.values():
                if isinstance(gruppo, list):
                    for x in gruppo:
                        t = x.get("id") or x.get("name") if isinstance(x, dict) else x
                        if isinstance(x, dict):
                            t = x.get("name") or x.get("text") or x.get("id")
                        if isinstance(t, str):
                            dot.append(t)
        elif isinstance(eq, list):
            dot = [x if isinstance(x, str) else (x.get("name") or "") for x in eq if x]
        if dot:
            a["dotazioni"] = [x for x in dot if x][:60]
    except Exception as e:
        log.warning("dettaglio %s: %s", a.get("url"), str(e)[:120])
    return a


def _pagine(cl: httpx.Client):
    """Annunci dalla pagina del concessionario (e dalle pagine successive)."""
    trovati: dict[str, dict] = {}
    for page in range(1, 21):
        url = DEALER_URL + (("&" if "?" in DEALER_URL else "?") + f"page={page}" if page > 1 else "")
        r = cl.get(url)
        if r.status_code != 200:
            log.warning("ATELIER pagina %s -> HTTP %s", url, r.status_code)
            break
        data = _next(r.text)
        if page == 1:
            pp = (data.get("props") or {}).get("pageProps") or {}
            log.info("ATELIER pagina concessionario chiavi=%s", list(pp)[:40])
            for k in ("numberOfResults", "totalResults", "numberOfListings"):
                v = _find(pp, k)
                if v:
                    log.info("ATELIER totale dichiarato %s=%s", k, v)
                    stato["dichiarati"] = v
                    break
            cid = _find(pp, "customerId", "dealerId", "dealerInfoId")
            stato["cliente"] = cid
            log.info("ATELIER id cliente=%s", cid)
        nuovi = 0
        for d in _walk(data):
            if _sembra_annuncio(d) and str(d["id"]) not in trovati:
                trovati[str(d["id"])] = _norm(d)
                nuovi += 1
        log.info("ATELIER pagina %d: %d annunci nuovi", page, nuovi)
        if nuovi == 0:
            break
        time.sleep(1.5)
    # ripiego: ricerca AutoScout filtrata per concessionario
    cid = stato.get("cliente")
    dich = _num(stato.get("dichiarati"))
    if cid and (not trovati or (dich and len(trovati) < dich)):
        for par in ("custid", "customerid"):
            prima = len(trovati)
            for page in range(1, 21):
                r = cl.get(f"{BASE}/lst?atype=C&cy=I&{par}={cid}&sort=age&desc=1&page={page}")
                if r.status_code != 200:
                    break
                pp = (_next(r.text).get("props") or {}).get("pageProps") or {}
                items = pp.get("listings") or []
                tot = _num(_find(pp, "numberOfResults"))
                if page == 1:
                    log.info("ATELIER ricerca %s=%s: totale=%s", par, cid, tot)
                if not items or (tot and tot > 2000):         # filtro ignorato: tutta Italia, si scarta
                    break
                for it in items:
                    if _sembra_annuncio(it):
                        trovati.setdefault(str(it["id"]), _norm(it))
                if len(items) < 20:
                    break
                time.sleep(1.5)
            if len(trovati) > prima:
                break
    return trovati


def _chiave(a: dict):
    marca = re.sub(r"[^a-z]", "", str(a.get("marca") or "").lower())
    mod = re.sub(r"[^a-z0-9]", "", (str(a.get("modello") or "").lower().split() or [""])[0])
    m = re.search(r"(19|20)\d\d", str(a.get("immatricolazione") or ""))
    return marca, mod, int(m.group(0)) if m else None


def _stessa_auto(sb: dict, au: dict) -> bool:
    """Stessa auto su Subito e su AutoScout: stessa marca, modello, anno e km vicini (o prezzo quasi uguale)."""
    m1, o1, y1 = _chiave(sb)
    m2, o2, y2 = _chiave(au)
    if not m1 or m1 != m2:
        return False
    if not (o1 and o2 and (o1.startswith(o2) or o2.startswith(o1))):
        return False
    if y1 and y2 and abs(y1 - y2) > 1:
        return False
    k1, k2 = sb.get("km"), au.get("km")
    if k1 and k2:
        tol = 12000 if sb.get("km_fascia") else 4000
        if abs(k1 - k2) <= tol:
            return True
    p1, p2 = sb.get("prezzo"), au.get("prezzo")
    return bool(p1 and p2 and abs(p1 - p2) <= max(500, 0.05 * p2))


def _con_subito(auto: list[dict], vecchie_sb: list[dict]) -> list[dict]:
    """Aggiunge le auto di Subito che non sono già sul sito (quelle doppie restano con la scheda AutoScout)."""
    try:
        import subito
        sb = subito.leggi()
    except Exception:
        log.exception("GALA_SB lettura Subito fallita")
        sb = []
    if not sb:
        sb = vecchie_sb                                   # Subito non ha risposto: si tengono quelle di prima
    nuove, doppie = [], []
    for a in sb:
        gemella = next((x for x in auto if x.get("fonte") != "subito" and _stessa_auto(a, x)), None)
        if gemella:
            gemella.setdefault("url_subito", a.get("url"))
            doppie.append(f"{a.get('marca')} {a.get('modello')}")
        else:
            nuove.append(a)
    log.info("GALA_SB unione: %d da AutoScout, %d da Subito, %d già presenti (%s), %d aggiunte",
             len(auto), len(sb), len(doppie), ", ".join(doppie), len(nuove))
    for a in nuove:
        log.info("GALA_SB aggiunta %s %s %s€", a.get("marca"), a.get("modello"), a.get("prezzo"))
    return auto + nuove


def carica() -> dict:
    try:
        return json.loads(DATA.read_text())
    except Exception:
        return {"auto": [], "aggiornato": None}


def aggiorna():
    if not _lock.acquire(blocking=False):
        return
    stato["in_corso"] = True
    try:
        vecchi = {a["id"]: a for a in carica().get("auto", [])}
        with _client() as cl:
            trovati = _pagine(cl)
            if not trovati:
                stato["errore"] = "nessun annuncio letto"
                log.warning("ATELIER nessun annuncio letto: tengo i dati di prima")
                return
            auto = []
            for i, (aid, a) in enumerate(trovati.items()):
                v = vecchi.get(aid)
                if v and v.get("descrizione") is not None and v.get("prezzo") == a.get("prezzo"):
                    v.update({k: a[k] for k in ("prezzo", "giudizio") if k in a})
                    auto.append(v)
                    continue
                a.setdefault("descrizione", "")
                auto.append(_dettaglio(cl, a))
                time.sleep(1.2)
        auto = [a for a in auto if a.get("prezzo")]
        auto = _con_subito(auto, [a for a in vecchi.values() if a.get("fonte") == "subito"])
        auto.sort(key=lambda a: -(a.get("prezzo") or 0))
        DATA.write_text(json.dumps({"auto": auto, "aggiornato": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                                   ensure_ascii=False))
        stato["ultimo"] = time.time()
        stato["errore"] = None
        log.info("ATELIER aggiornato: %d auto (prima %d)", len(auto), len(vecchi))
        for x in auto:
            log.info("ATELIER auto %s %s %s€ km=%s giudizio=%s foto=%d dot=%d desc=%d", x.get("marca"), x.get("modello"), x.get("prezzo"),
                     x.get("km"), x.get("giudizio"), len(x.get("foto") or []), len(x.get("dotazioni") or []), len(x.get("descrizione") or ""))
    except Exception as e:
        stato["errore"] = str(e)[:200]
        log.exception("ATELIER aggiornamento fallito")
    finally:
        stato["in_corso"] = False
        _lock.release()


def ciclo():
    ore = float(os.environ.get("REFRESH_HOURS", "3"))
    while True:
        aggiorna()
        time.sleep(ore * 3600)
