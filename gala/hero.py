"""Auto di copertina del sito: la Range Rover Sport di Gala (foto loro, da AutoScout24).

- foto esterna ritagliata dallo sfondo (rembg) -> /hero/ext.png
- foto degli interni -> /hero/int.jpg
HERO_EXT / HERO_INT = indice della foto da usare (si sceglie guardando le miniature nei log).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import threading
from pathlib import Path

import httpx

import scraper

log = logging.getLogger("atelier")
DIR = Path(os.environ.get("HERO_DIR", "/tmp/hero"))
DIR.mkdir(parents=True, exist_ok=True)
stato = {"ok": False, "id": None, "errore": None, "int": False}
_lock = threading.Lock()


def _scegli(auto: list[dict]) -> dict | None:
    cerca = os.environ.get("HERO_CERCA", "range rover").lower()
    for a in auto:
        if cerca in f"{a.get('marca')} {a.get('modello')} {a.get('versione')}".lower():
            return a
    return max(auto, key=lambda a: a.get("prezzo") or 0) if auto else None


def _get(cl: httpx.Client, u: str, size: str) -> bytes | None:
    u = re.sub(r"/\d+x\d+\.webp$", f"/{size}.webp", u)
    r = cl.get(u)
    return r.content if r.status_code == 200 else None


def prepara(forza: bool = False):
    if not _lock.acquire(blocking=False):
        return
    try:
        from PIL import Image
        auto = scraper.carica().get("auto", [])
        a = _scegli(auto)
        if not a or not a.get("foto"):
            stato["errore"] = "nessuna auto"
            return
        foto = a["foto"]
        if stato["ok"] and stato["id"] == a["id"] and not forza:
            return
        log.info("HERO auto=%s %s %s foto=%d", a.get("marca"), a.get("modello"), a.get("prezzo"), len(foto))
        with httpx.Client(timeout=30, headers={"User-Agent": scraper.UA, "Referer": scraper.BASE + "/"}) as cl:
            # miniature nei log (per scegliere a occhio le foto giuste)
            if os.environ.get("HERO_MINIATURE", "1") == "1":
                for i, u in enumerate(foto):
                    b = _get(cl, u, "250x188")
                    if not b:
                        continue
                    im = Image.open(io.BytesIO(b)).convert("RGB").resize((200, 150))
                    buf = io.BytesIO(); im.save(buf, "JPEG", quality=55)
                    s = base64.b64encode(buf.getvalue()).decode()
                    parti = [s[k:k + 1400] for k in range(0, len(s), 1400)]
                    for k, p in enumerate(parti):
                        log.info("HEROIMG %d %d/%d %s", i, k + 1, len(parti), p)
            ie = int(os.environ.get("HERO_EXT", "0"))
            ii = os.environ.get("HERO_INT")
            ext = _get(cl, foto[min(ie, len(foto) - 1)], "1280x960")
            if ext:
                from rembg import new_session, remove
                sess = new_session(os.environ.get("HERO_MODELLO", "silueta"))
                im = Image.open(io.BytesIO(ext)).convert("RGB")
                cut = remove(im, session=sess, post_process_mask=True)
                bbox = cut.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()
                if bbox:
                    cut = cut.crop(bbox)
                cut.save(DIR / "ext.png")
                log.info("HERO ritaglio fatto %s", cut.size)
                bg = Image.new("RGB", cut.size, (40, 30, 70)); bg.paste(cut, mask=cut.getchannel("A"))
                bg.thumbnail((240, 180)); buf = io.BytesIO(); bg.save(buf, "JPEG", quality=60)
                s = base64.b64encode(buf.getvalue()).decode()
                parti = [s[k:k + 1400] for k in range(0, len(s), 1400)]
                for k, p in enumerate(parti):
                    log.info("HEROIMG cut %d/%d %s", k + 1, len(parti), p)
            if ii is not None:
                b = _get(cl, foto[min(int(ii), len(foto) - 1)], "1280x960")
                if b:
                    Image.open(io.BytesIO(b)).convert("RGB").save(DIR / "int.jpg", quality=88)
                    stato["int"] = True
        stato.update(ok=(DIR / "ext.png").exists(), id=a["id"], errore=None,
                     nome=f"{a.get('marca')} {a.get('modello')}", prezzo=a.get("prezzo"))
    except Exception as e:
        stato["errore"] = str(e)[:300]
        log.exception("HERO non pronta")
    finally:
        _lock.release()
