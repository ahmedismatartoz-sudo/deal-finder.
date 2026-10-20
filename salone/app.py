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
    threading.Thread(target=_prova_foto, daemon=True).start()


def _prova_foto():
    """Controllo nei log: misure vere delle foto AutoScout (per non tagliarle)."""
    import time as _t, struct as _s
    _t.sleep(40)
    for a in scraper.carica().get("auto", [])[:2]:
        for u in (a.get("foto") or [])[:2]:
            base = re.sub(r"/\d+x\d+\.webp$", "", u)
            for v in (u, base, base + "/1920x1080.webp", base + "/1280x960.webp"):
                try:
                    r = _img.get(v)
                    b = r.content
                    dim = None
                    if b[:4] == b"RIFF" and b[12:16] == b"VP8X":
                        dim = (1 + int.from_bytes(b[24:27], "little"), 1 + int.from_bytes(b[27:30], "little"))
                    elif b[:4] == b"RIFF" and b[12:16] == b"VP8 ":
                        dim = (_s.unpack("<H", b[26:28])[0] & 0x3FFF, _s.unpack("<H", b[28:30])[0] & 0x3FFF)
                    elif b[:3] == b"\xff\xd8\xff":
                        i = 2
                        while i < len(b) - 9:
                            if b[i] == 0xFF and b[i + 1] in (0xC0, 0xC2):
                                dim = (_s.unpack(">H", b[i + 7:i + 9])[0], _s.unpack(">H", b[i + 5:i + 7])[0]); break
                            i += 2 + _s.unpack(">H", b[i + 2:i + 4])[0]
                    logging.info("FOTO_PROVA %s %s %s %s byte=%d", a.get("modello"), v[-60:], r.status_code, dim, len(b))
                except Exception as e:
                    logging.info("FOTO_PROVA errore %s %s", v[-60:], str(e)[:80])



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
