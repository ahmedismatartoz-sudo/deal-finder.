"""23/10 (cliente): foto delle auto di un commerciante AutoScout24 per il suo sito vetrina.

Legge la pagina del concessionario, trova i suoi annunci, scarica le foto di ciascuno (max FOTO_MAX),
le rimpicciolisce e le scrive nei log in pezzi base64 (FOTODEALER ...), da cui Claude ricompone le immagini.
Nessun dato personale: solo foto e dati delle auto.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re

import httpx

lg = logging.getLogger("dealfinder.marketing")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "it-IT,it;q=0.9"}
IMG = re.compile(r"listing-images/([0-9a-f-]{36})_([0-9a-f-]{36})\.jpg")
PEZZO = 7000


def _jpeg(raw: bytes, larg: int = 1100) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    if im.width > larg:
        im = im.resize((larg, round(im.height * larg / im.width)), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80, optimize=True, progressive=True)
    return out.getvalue()


def _titolo(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:160] if m else ""


def giro(pagina: str, massimo: int = 10) -> None:
    with httpx.Client(headers=UA, timeout=40, follow_redirects=True) as c:
        h = c.get(pagina).text
        annunci = list(dict.fromkeys(m.group(1) for m in IMG.finditer(h)))
        annunci += [g for g in dict.fromkeys(re.findall(r"/annunci/[^\"'\s]*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", h)) if g not in annunci]
        lg.warning("FOTODEALER_ANNUNCI %s", json.dumps(annunci))
        for g in annunci:
            try:
                p = c.get(f"https://www.autoscout24.it/annunci/-{g}")
                foto = list(dict.fromkeys(f"{a}_{b}" for a, b in IMG.findall(p.text) if a == g))[:massimo]
                lg.warning("FOTODEALER_AUTO %s", json.dumps({"guid": g, "titolo": _titolo(p.text), "foto": len(foto), "http": p.status_code}, ensure_ascii=False))
                for i, f in enumerate(foto):
                    r = c.get(f"https://prod.pictures.autoscout24.net/listing-images/{f}.jpg/1280x960.webp")
                    if r.status_code != 200:
                        r = c.get(f"https://prod.pictures.autoscout24.net/listing-images/{f}.jpg/1920x1080.webp")
                    if r.status_code != 200:
                        lg.warning("FOTODEALER_ERR %s %d %d", g, i, r.status_code)
                        continue
                    b = base64.b64encode(_jpeg(r.content)).decode()
                    n = (len(b) + PEZZO - 1) // PEZZO
                    for k in range(n):
                        lg.warning("FOTODEALER %s %d %d/%d %s", g, i, k + 1, n, b[k * PEZZO:(k + 1) * PEZZO])
            except Exception as e:
                lg.warning("FOTODEALER_ERR %s %s", g, str(e)[:200])
        lg.warning("FOTODEALER_FINE")
