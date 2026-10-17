"""Sito di Yuri Car (Desio): auto prese in automatico dalla loro pagina AutoScout24."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

import scraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
QUI = Path(__file__).parent
STATIC = QUI / "static"
CACHE = Path(os.environ.get("IMG_CACHE", "/tmp/yuri-img"))
CACHE.mkdir(parents=True, exist_ok=True)
TOKEN = os.environ.get("REFRESH_TOKEN", "")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=800)
_img = httpx.Client(timeout=20, headers={"User-Agent": scraper.UA, "Referer": scraper.BASE + "/"})


def _scarica(u: str) -> Path | None:
    """Copia locale della foto (se una misura non esiste si prova la successiva)."""
    f = CACHE / hashlib.sha1(u.encode()).hexdigest()
    if f.exists():
        return f
    r = _img.get(u)
    for alt in ("720x540", "480x360", "250x188"):
        if r.status_code == 200:
            break
        r = _img.get(re.sub(r"/\d+x\d+\.webp$", f"/{alt}.webp", u))
    if r.status_code != 200:
        return None
    f.write_bytes(r.content)
    return f


def _prepara_foto():
    """Scarica in anticipo tutte le foto, cosi' il sito le serve subito dal disco."""
    n = 0
    for a in scraper.carica().get("auto", []):
        for u in a.get("foto") or []:
            for size in ("480x360", "720x540", "1280x960"):
                v = re.sub(r"/\d+x\d+\.webp$", f"/{size}.webp", u)
                try:
                    if _scarica(v):
                        n += 1
                except Exception:
                    pass
    logging.info("YURI foto pronte in cache: %d", n)


def _ciclo():
    ore = float(os.environ.get("REFRESH_HOURS", "3"))
    while True:
        try:
            scraper.aggiorna()
            _prepara_foto()
        except Exception:
            logging.exception("YURI aggiornamento")
        import time
        time.sleep(ore * 3600)


@app.on_event("startup")
def _avvio():
    threading.Thread(target=_prepara_foto, daemon=True).start()
    threading.Thread(target=_ciclo, daemon=True).start()



@app.get("/api/auto")
def auto():
    d = scraper.carica()
    d["in_corso"] = scraper.stato["in_corso"]
    return JSONResponse(d, headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/stato")
def stato():
    s = {k: v for k, v in scraper.stato.items() if not k.startswith("_")}
    s["auto"] = len(scraper.carica().get("auto", []))
    return s


@app.post("/api/aggiorna")
def aggiorna(t: str = ""):
    if not TOKEN or t != TOKEN:
        raise HTTPException(403)
    threading.Thread(target=scraper.aggiorna, daemon=True).start()
    return {"ok": True}


@app.get("/img")
def img(u: str = Query(...)):
    """Foto delle auto servite dal nostro dominio (con copia locale)."""
    if not u.startswith("https://prod.pictures.autoscout24.net/"):
        raise HTTPException(400)
    f = _scarica(u)
    if not f:
        raise HTTPException(404)
    return FileResponse(f, media_type="image/webp",
                    headers={"Cache-Control": "public, max-age=2592000, immutable"})


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /\nDisallow: /api/\n", media_type="text/plain")


@app.api_route("/", methods=["GET", "HEAD"])
def home():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC), name="static")
