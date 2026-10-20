"""Sito base "Salone" (bianco e azzurro): auto prese in automatico da AutoScout24 e Subito."""
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
from fastapi.staticfiles import StaticFiles

import scraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
QUI = Path(__file__).parent
STATIC = QUI / "static"
CACHE = Path(os.environ.get("IMG_CACHE", "/tmp/salone-img"))
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
    sb = u.startswith("https://images.sbito.it/")
    if not (u.startswith("https://prod.pictures.autoscout24.net/") or sb):
        raise HTTPException(400)
    f = CACHE / hashlib.sha1(u.encode()).hexdigest()
    if not f.exists():
        r = _img.get(u, headers={"Referer": "https://www.subito.it/"} if sb else None)
        for alt in ("720x540", "480x360", "250x188"):           # se una misura non esiste si prova la successiva
            if r.status_code == 200:
                break
            r = _img.get(re.sub(r"/\d+x\d+\.webp$", f"/{alt}.webp", u))
        if r.status_code != 200:
            raise HTTPException(404)
        f.write_bytes(r.content)
    b = f.read_bytes()
    mt = "image/jpeg" if b[:3] == b"\xff\xd8\xff" else "image/png" if b[:4] == b"\x89PNG" else "image/webp"
    return Response(b, media_type=mt,
                    headers={"Cache-Control": "public, max-age=2592000, immutable"})


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /\nDisallow: /api/\n", media_type="text/plain")


@app.api_route("/", methods=["GET", "HEAD"])
def home():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC), name="static")
