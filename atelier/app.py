"""Sito di Carrozzeria Atelier: auto prese in automatico dalla loro pagina AutoScout24."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import scraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
QUI = Path(__file__).parent
STATIC = QUI / "static"
CACHE = Path(os.environ.get("IMG_CACHE", "/tmp/atelier-img"))
CACHE.mkdir(parents=True, exist_ok=True)
TOKEN = os.environ.get("REFRESH_TOKEN", "")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_img = httpx.Client(timeout=20, headers={"User-Agent": scraper.UA, "Referer": scraper.BASE + "/"})


@app.on_event("startup")
def _avvio():
    threading.Thread(target=scraper.ciclo, daemon=True).start()


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
    f = CACHE / hashlib.sha1(u.encode()).hexdigest()
    if not f.exists():
        r = _img.get(u)
        if r.status_code != 200:
            raise HTTPException(404)
        f.write_bytes(r.content)
    return Response(f.read_bytes(), media_type="image/webp",
                    headers={"Cache-Control": "public, max-age=2592000, immutable"})


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /\nDisallow: /api/\n", media_type="text/plain")


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC), name="static")
