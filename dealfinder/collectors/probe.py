"""Diagnostica di accesso a Subito dal server (nessun dato salvato).

    python -m dealfinder.collectors.probe

Prova più strade e stampa codice di risposta e un estratto della struttura:
  1. pagina di ricerca con intestazioni da browser completo
  2. pagina di un singolo annuncio (dalla ricerca, se raggiungibile)
  3. API interna usata dal sito/app (hades)
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger("probe")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/129.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="129", "Google Chrome";v="129", "Not=A?Brand";v="8"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

TESTS = [
    ("ricerca_html", "https://www.subito.it/annunci-lombardia/vendita/auto/milano/?pe=20000", BROWSER_HEADERS),
    ("home_html", "https://www.subito.it/", BROWSER_HEADERS),
    ("api_hades", "https://hades.subito.it/v1/search/items?c=2&r=4&t=s&lim=10&start=0&sort=datedesc",
     {"User-Agent": BROWSER_HEADERS["User-Agent"], "Accept": "application/json", "X-Subito-Channel": "web",
      "Origin": "https://www.subito.it", "Referer": "https://www.subito.it/"}),
    ("api_hades_app", "https://hades.subito.it/v1/search/items?c=2&r=4&t=s&lim=10&start=0&sort=datedesc",
     {"User-Agent": "Subito/7.0 (Android)", "Accept": "application/json", "X-Subito-Channel": "android"}),
]


def run() -> list[dict]:
    import httpx
    results = []
    for name, url, headers in TESTS:
        out = {"test": name}
        try:
            with httpx.Client(headers=headers, timeout=25, follow_redirects=True, http2=False) as c:
                r = c.get(url)
            out["status"] = r.status_code
            out["server"] = r.headers.get("server")
            out["blocco"] = next((h for h in ("x-akamai-transformed", "x-datadome", "cf-ray", "x-dd-b")
                                  if h in r.headers), None)
            body = r.text
            out["bytes"] = len(body)
            if "application/json" in r.headers.get("content-type", ""):
                try:
                    data = r.json()
                    out["json_keys"] = list(data)[:10] if isinstance(data, dict) else type(data).__name__
                    ads = data.get("ads") if isinstance(data, dict) else None
                    if ads:
                        out["annunci"] = len(ads)
                        out["campi_annuncio"] = list(ads[0])[:25]
                except Exception:
                    pass
            else:
                out["next_data"] = "__NEXT_DATA__" in body
                out["estratto"] = body[:160].replace("\n", " ")
        except Exception as e:
            out["errore"] = f"{type(e).__name__}: {e}"[:200]
        log.info("PROBE %s", json.dumps(out, ensure_ascii=False))
        results.append(out)
        time.sleep(3)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
