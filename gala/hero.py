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


MODELLO_URL = os.environ.get("HERO_MODELLO_URL", "https://github.com/danielgatis/rembg/releases/download/v0.0.0/silueta.onnx")


def _ritaglia(im):
    """Scontorna l'auto: rete U2-Net leggera (silueta) con onnxruntime, senza librerie pesanti."""
    import numpy as np
    import onnxruntime as ort
    from PIL import Image, ImageFilter
    f = DIR / "modello.onnx"
    if not f.exists():
        with httpx.Client(timeout=120, follow_redirects=True) as cl:
            f.write_bytes(cl.get(MODELLO_URL).content)
    so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
    so.enable_cpu_mem_arena = False; so.enable_mem_pattern = False
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    sess = ort.InferenceSession(str(f), so, providers=["CPUExecutionProvider"])
    x = np.asarray(im.resize((320, 320), Image.LANCZOS), dtype=np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    del sess
    out = (out - out.min()) / max(1e-6, float(out.max() - out.min()))
    mask = Image.fromarray((out * 255).astype("uint8")).resize(im.size, Image.LANCZOS)
    mask = mask.filter(ImageFilter.GaussianBlur(1.2)).point(lambda v: 0 if v < 40 else (255 if v > 215 else int((v - 40) * 255 / 175)))
    rgba = im.convert("RGBA"); rgba.putalpha(mask)
    return rgba


def _ritaglia_file(src, dst):
    from PIL import Image
    im = Image.open(src).convert("RGB")
    _ritaglia(im).save(dst)


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
            if ii is not None:
                b = _get(cl, foto[min(int(ii), len(foto) - 1)], "1280x960")
                if b:
                    Image.open(io.BytesIO(b)).convert("RGB").save(DIR / "int.jpg", quality=88)
                    stato["int"] = True
            mark = DIR / "tentativi"
            n = int(mark.read_text()) if mark.exists() else 0
            if ext and n >= 2 and not forza:
                log.warning("HERO ritaglio saltato: fallito %d volte", n)
                ext = None
            if ext:
                mark.write_text(str(n + 1))
                (DIR / "in.jpg").write_bytes(ext)
                import subprocess, sys
                r = subprocess.run([sys.executable, "-c", "import hero, sys; hero._ritaglia_file(sys.argv[1], sys.argv[2])",
                                    str(DIR / "in.jpg"), str(DIR / "raw.png")], cwd=str(Path(__file__).parent),
                                   capture_output=True, text=True, timeout=600)
                if r.returncode != 0 or not (DIR / "raw.png").exists():
                    log.error("HERO ritaglio fallito (codice %s): %s", r.returncode, (r.stderr or "")[-600:])
                    raise RuntimeError("ritaglio fallito")
                mark.unlink(missing_ok=True)
                cut = Image.open(DIR / "raw.png")
                bbox = cut.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()
                if bbox:
                    cut = cut.crop(bbox)
                cut.save(DIR / "ext.png")
                log.info("HERO ritaglio fatto %s", cut.size)
                try:
                    px = list(cut.getchannel("A").resize((70, 30)).getdata())
                    for r in range(30):
                        log.info("HEROMASK %02d |%s|", r, "".join(" .:-=+*#%@"[min(9, v // 26)] for v in px[r * 70:(r + 1) * 70]))
                except Exception:
                    pass
        stato.update(ok=(DIR / "ext.png").exists(), id=a["id"], errore=None,
                     nome=f"{a.get('marca')} {a.get('modello')}", prezzo=a.get("prezzo"))
    except Exception as e:
        stato["errore"] = str(e)[:300]
        log.exception("HERO non pronta")
    finally:
        _lock.release()
