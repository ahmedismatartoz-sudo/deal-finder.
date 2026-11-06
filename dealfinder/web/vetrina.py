"""29/10 (cliente): siti vetrina dei concessionari collegati alla loro pagina AutoScout24.

GET /api/vetrina/<slug>  (slug della pagina concessionario, es. auto-grandi-srl; solo quelli in VETRINA_SLUG)
Legge la pagina pubblica del concessionario su AutoScout24 (tutte le pagine dei risultati), restituisce le auto in
JSON (titolo, prezzo, anno, km, carburante, cambio, potenza, foto, link) per il sito vetrina. Cache 30 minuti:
il sito resta aggiornato da solo quando il concessionario mette o toglie un'auto.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

from starlette.responses import JSONResponse

log = logging.getLogger("vetrina")
_cache: dict = {}
_lock = threading.Lock()


def _annunci(dati) -> list:
    """Trova nella pagina la lista degli annunci (dizionari con 'vehicle' e 'url')."""
    trovati = []

    def giro(x, prof=0):
        if prof > 8:
            return
        if isinstance(x, list):
            if x and all(isinstance(i, dict) for i in x) and sum(1 for i in x if "vehicle" in i and "url" in i) >= max(1, len(x) // 2):
                trovati.append(x)
                return
            for i in x:
                giro(i, prof + 1)
        elif isinstance(x, dict):
            for v in x.values():
                giro(v, prof + 1)
    giro(dati)
    return max(trovati, key=len) if trovati else []


def _auto(it: dict) -> dict | None:
    from ..collectors.autoscout import parse_item
    l = parse_item(it)
    if not l:
        return None
    veh = it.get("vehicle") or {}
    foto = [re.sub(r"/\d+x\d+\.(webp|jpg)$", "/1280x960.webp", f) for f in (it.get("images") or []) if isinstance(f, str) and f.startswith("http")]
    det = {str(d.get("iconName") or ""): d.get("data") for d in (it.get("vehicleDetails") or []) if isinstance(d, dict)}
    prezzo = l.price_eur
    # formato pagina concessionario (5/11): prices / vehicle.*{raw, formatted}
    def raw(k):
        x = veh.get(k)
        return x.get("raw") if isinstance(x, dict) else x
    def fmt(k):
        x = veh.get(k)
        return x.get("formatted") if isinstance(x, dict) else x
    if not prezzo:
        pz = it.get("prices") or {}
        cand = []
        def cerca(o):
            if isinstance(o, dict):
                for kk, vv in o.items():
                    if isinstance(vv, (int, float)) and "price" in kk.lower():
                        cand.append(vv)
                    elif isinstance(vv, str) and "€" in vv:
                        n = re.sub(r"[^0-9]", "", vv.split(",")[0])
                        if n:
                            cand.append(int(n))
                    else:
                        cerca(vv)
            elif isinstance(o, list):
                for vv in o:
                    cerca(vv)
        cerca(pz)
        cand = [c for c in cand if 300 <= c <= 2_000_000]
        prezzo = int(cand[0]) if cand else None
    anno = l.year
    if not anno:
        for k in veh:
            if "regist" in k.lower() or "year" in k.lower():
                m = re.search(r"(19|20)\d\d", str(fmt(k) or raw(k) or ""))
                if m:
                    anno = int(m.group(0)); break
    km_ = l.mileage_km or raw("mileageInKm")
    carb = det.get("gas_pump") or fmt("fuelCategory") or l.fuel
    cambio = det.get("transmission") or fmt("transmissionType") or l.gearbox
    pot = det.get("speedometer") or (" / ".join(x for x in (fmt("powerInKw"), fmt("powerInHp")) if x) or None)
    kw = l.power_kw or raw("powerInKw")
    if "non marciante" in (str(l.title) + " " + str(veh.get("modelVersionInput"))).lower():
        return None                       # auto da ricambi: non va sul sito vetrina
    return {"id": l.source_id, "marca": veh.get("make"), "modello": veh.get("model"), "versione": veh.get("modelVersionInput"),
            "titolo": l.title, "prezzo": prezzo, "anno": anno, "immatricolazione": det.get("calendar"), "km": km_,
            "carburante": carb, "cambio": cambio, "potenza": pot, "kw": kw, "carrozzeria": fmt("bodyType"), "foto": foto[:30], "link": l.url,
            "nuova": str(det.get("calendar") or "").strip() in ("", "- (Prima immatricolazione)", "Nuovo") and (l.mileage_km or 0) < 100}


def leggi(slug: str) -> list:
    import httpx
    from ..collectors.autoscout import BASE, UA
    from ..collectors.base import next_data
    out, visti = [], set()
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"}) as c:
        for pag in range(1, 21):
            r = c.get(f"{BASE}/concessionari/{slug}" + (f"?page={pag}" if pag > 1 else ""))
            if r.status_code != 200:
                log.warning("VETRINA %s pagina %d: HTTP %s", slug, pag, r.status_code)
                break
            nuovi = 0
            lista = _annunci(next_data(r.text) or {})
            for it in lista:
                a = _auto(it)
                if a and a["id"] not in visti:
                    visti.add(a["id"]); out.append(a); nuovi += 1
            if nuovi < 10:                 # ultima pagina
                break
            time.sleep(1)
    log.warning("VETRINA %s: %d auto", slug, len(out))
    return out


QR = "https://www.quattroruote.it"


def leggi_qr(conc: str) -> list:
    """30/10 (cliente: "metti le foto delle loro macchine"): concessionari senza pagina AutoScout24, letti dalla loro pagina
    pubblica su Quattroruote (/auto-usate/concessionario/<conc>): per ogni annuncio titolo, prezzo e foto (og:image e galleria)."""
    import html as H
    import httpx
    from ..collectors.autoscout import UA
    out = []
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"}) as c:
        r = c.get(f"{QR}/auto-usate/concessionario/{conc}")
        log.warning("VETRINA_QR %s pagina concessionario HTTP %s, %d caratteri", conc, r.status_code, len(r.text))
        link = []
        for m in re.findall(r'/auto-usate/annuncio/[a-z0-9\-]+-\d+', r.text):
            if m not in link:
                link.append(m)
        for u in link[:40]:
            try:
                p = c.get(QR + u)
                t = p.text
                og = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', t)
                titolo = H.unescape(og.group(1)) if og else u.rsplit("/", 1)[-1]
                imgs = []
                for f in re.findall(r'https?://[^"\'\s)]+?\.(?:jpe?g|webp|png)(?:\?[^"\'\s)]*)?', t):
                    f = H.unescape(f).replace("\\/", "/")
                    if any(x in f.lower() for x in ("logo", "icon", "sprite", "banner", "placeholder", "avatar", "favicon")):
                        continue
                    if f not in imgs:
                        imgs.append(f)
                ogi = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', t)
                if ogi:
                    f = H.unescape(ogi.group(1))
                    imgs = [f] + [x for x in imgs if x != f]
                pr = re.search(r'(\d{1,3}(?:\.\d{3})+)\s*(?:€|&euro;)', t)
                km = re.search(r'(\d{1,3}(?:\.\d{3})*)\s*[Kk]m\b', t)
                anno = re.search(r'\b(20[12]\d)\b', titolo)
                out.append({"id": u.rsplit("-", 1)[-1], "titolo": titolo, "prezzo": int(pr.group(1).replace(".", "")) if pr else None,
                            "km": int(km.group(1).replace(".", "")) if km else None, "anno": int(anno.group(1)) if anno else None,
                            "foto": imgs[:20], "link": QR + u})
                time.sleep(0.7)
            except Exception as e:
                log.warning("VETRINA_QR annuncio %s: %s", u, str(e)[:120])
    log.warning("VETRINA_QR %s: %d auto, foto per auto %s, esempio %s", conc, len(out), [len(a["foto"]) for a in out],
                (out[0]["foto"][:3] if out else None))
    return out


def _leggi_uno(slug: str) -> list:
    with _lock:
        if slug.startswith("qr-"):
            return leggi_qr(slug[3:])
        return leggi(slug)


# vetrine sempre attive (Render è al limite di 300 variabili: non si aggiunge VETRINA_SLUG per ognuna)
VETRINA_SEMPRE = {"sr-auto-srl", "astro-srl-milano-mi"}     # 3/11: SR AUTO (Osio Sopra, BG); 5/11: Astro srl (Milano)


async def api_vetrina(request):
    slug = request.path_params["slug"]
    ammessi = {s.strip() for s in os.environ.get("VETRINA_SLUG", "auto-grandi-srl").split(",") if s.strip()} | VETRINA_SEMPRE
    h = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"}
    if slug not in ammessi:
        return JSONResponse({"errore": "non disponibile"}, status_code=404, headers=h)
    now = time.time()
    c = _cache.get(slug)
    if not c or now - c["t"] > int(os.environ.get("VETRINA_CACHE_S", "1800")):
        import anyio
        try:
            auto = await anyio.to_thread.run_sync(_leggi_uno, slug)
            if auto or not c:
                c = _cache[slug] = {"t": now, "auto": auto}
        except Exception as e:
            log.warning("VETRINA %s non letta: %s", slug, str(e)[:200])
            if not c:
                return JSONResponse({"errore": "non disponibile ora"}, status_code=503, headers=h)
    return JSONResponse({"aggiornato": int(c["t"]), "auto": c["auto"]}, headers=h)


def _preriscalda():
    """30/10: legge subito all'avvio le vetrine in VETRINA_PRERISCALDA (così la cache è pronta e i log mostrano le foto)."""
    time.sleep(20)
    for slug in [s.strip() for s in (os.environ.get("VETRINA_PRERISCALDA") or "").split(",") if s.strip()] + ["astro-srl-milano-mi"]:
        try:
            _cache[slug] = {"t": time.time(), "auto": _leggi_uno(slug)}
            log.warning("VETRINA_ELENCO %s %s", slug, [(a.get("titolo"), a.get("prezzo"), a.get("anno"), a.get("km"),
                        a.get("carburante"), len(a.get("foto") or [])) for a in _cache[slug]["auto"]][:40])
        except Exception as e:
            log.warning("VETRINA preriscaldo %s: %s", slug, str(e)[:200])


threading.Thread(target=_preriscalda, daemon=True, name="vetrina_preriscalda").start()


_foto_cache: dict = {}
_foto_byte = 0
_FOTO_MAX = 1_000_000
_FOTO_CACHE_MAX = 40_000_000
_FOTO_TIPI = {"image/jpeg", "image/png", "image/webp", "image/avif"}


async def foto_vetrina(request):
    """Foto degli annunci AutoScout24 con CORS, per usarle come texture 3D nei siti vetrina (solo host AutoScout).
    6/11: niente redirect, solo immagini, massimo 1 MB, cache limitata in byte."""
    from urllib.parse import urlparse
    from starlette.responses import Response
    u = request.query_params.get("u") or ""
    pu = urlparse(u)
    host = (pu.hostname or "").lower()
    if not (pu.scheme == "https" and pu.port in (None, 443) and not pu.username and not pu.password
            and (host.endswith(".autoscout24.net") or host.endswith(".autoscout24.com"))):
        return Response(status_code=400)
    chiave = f"{host}{pu.path}"
    c = _foto_cache.get(chiave)
    if not c:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers={"User-Agent": UA}) as cl:
                async with cl.stream("GET", f"https://{host}{pu.path}") as r:
                    tipo = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if r.status_code != 200 or tipo not in _FOTO_TIPI:
                        return Response(status_code=502)
                    buf = bytearray()
                    async for pezzo in r.aiter_bytes():
                        buf += pezzo
                        if len(buf) > _FOTO_MAX:
                            return Response(status_code=502)
            c = (bytes(buf), tipo)
            global _foto_byte
            if _foto_byte + len(c[0]) > _FOTO_CACHE_MAX or len(_foto_cache) > 150:   # memoria del web limitata
                _foto_cache.clear()
                _foto_byte = 0
            _foto_cache[chiave] = c
            _foto_byte += len(c[0])
        except Exception:
            return Response(status_code=502)
    return Response(c[0], media_type=c[1], headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=86400",
                                                    "X-Content-Type-Options": "nosniff"})
