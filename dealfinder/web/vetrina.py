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
    return {"id": l.source_id, "marca": veh.get("make"), "modello": veh.get("model"), "versione": veh.get("modelVersionInput"),
            "titolo": l.title, "prezzo": prezzo, "anno": l.year, "immatricolazione": det.get("calendar"), "km": l.mileage_km,
            "carburante": det.get("gas_pump") or l.fuel, "cambio": det.get("transmission") or l.gearbox,
            "potenza": det.get("speedometer"), "kw": l.power_kw, "foto": foto[:30], "link": l.url,
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
            for it in _annunci(next_data(r.text) or {}):
                a = _auto(it)
                if a and a["id"] not in visti:
                    visti.add(a["id"]); out.append(a); nuovi += 1
            if nuovi < 10:                 # ultima pagina
                break
            time.sleep(1)
    log.warning("VETRINA %s: %d auto", slug, len(out))
    return out


def _leggi_uno(slug: str) -> list:
    with _lock:
        return leggi(slug)


async def api_vetrina(request):
    slug = request.path_params["slug"]
    ammessi = {s.strip() for s in os.environ.get("VETRINA_SLUG", "auto-grandi-srl").split(",") if s.strip()}
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
