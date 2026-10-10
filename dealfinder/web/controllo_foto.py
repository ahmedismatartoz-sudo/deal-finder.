"""Controllo foto delle auto sul sito: per ogni auto proposta prepara un foglio con fino a 6 foto
(piccole, in griglia) e lo scrive nei log in pezzi base64, così si può guardare a parte e
correggere le auto dichiarate sane ma incidentate.

Gira una sola volta per versione (CONTROLLO_FOTO_VERSIONE) all'avvio del sito.
Righe nei log:
  FOTOMETA {"id":..., "nome":..., ...}
  FOTOPEZZO <id> <n>/<tot> <base64>
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os

log = logging.getLogger("controllo_foto")
VERSIONE = os.environ.get("CONTROLLO_FOTO_VERSIONE", "v1")
PEZZO = 12000
CELLA = (320, 240)
COLONNE, RIGHE = 3, 2


def _foglio(images: list[bytes], titolo: str) -> bytes | None:
    from PIL import Image, ImageDraw
    cells = []
    for raw in images:
        try:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            im.thumbnail(CELLA)
            cells.append(im)
        except Exception:
            continue
    if not cells:
        return None
    W, H = CELLA[0] * COLONNE, CELLA[1] * RIGHE + 22
    sheet = Image.new("RGB", (W, H), (20, 20, 20))
    for i, im in enumerate(cells[: COLONNE * RIGHE]):
        x, y = (i % COLONNE) * CELLA[0], 22 + (i // COLONNE) * CELLA[1]
        sheet.paste(im, (x + (CELLA[0] - im.width) // 2, y + (CELLA[1] - im.height) // 2))
    ImageDraw.Draw(sheet).text((6, 5), titolo[:150], fill=(255, 255, 0))
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=62, optimize=True)
    return out.getvalue()


def run(conn) -> dict:
    from . import foto, scovo
    from ..config import settings
    rows = conn.execute(scovo.LIST_SQL + " LIMIT 3000", {"me": 0}).fetchall()
    n = 0
    for r in rows:
        if not scovo.visible(r, settings.max_dealer_opens):
            continue
        it = scovo.item(r)
        if not scovo.abbastanza(it):
            continue
        desc = conn.execute("SELECT description FROM listings WHERE id=%s", (r["id"],)).fetchone()
        imgs = []
        for pos in range(min(int(r.get("n_photos") or 0), COLONNE * RIGHE)):
            try:
                c = foto.fetch_one(conn, r["id"], pos)
            except Exception:
                c = None
            if c:
                imgs.append(c)
        meta = {"id": r["id"], "nome": it["nome"], "anno": it["anno"], "km": it["km"], "prezzo": it["prezzo"],
                "mercato": it["mercato"], "danno": it["danno"], "rip": [it["rip_lo"], it["rip_hi"]],
                "fonte": it["fonte"], "url": r.get("url"), "n_foto": len(imgs),
                "descrizione": scovo.clean_desc((desc or {}).get("description"), 400)}
        log.warning("FOTOMETA %s", json.dumps(meta, ensure_ascii=False, default=str))
        sheet = _foglio(imgs, f"{r['id']} {it['nome']} {it['anno']} {it['prezzo']}e")
        if sheet:
            b = base64.b64encode(sheet).decode()
            parts = [b[i:i + PEZZO] for i in range(0, len(b), PEZZO)]
            for k, p in enumerate(parts, 1):
                log.warning("FOTOPEZZO %s %d/%d %s", r["id"], k, len(parts), p)
        n += 1
    log.warning("FOTOFINE %d auto", n)
    return {"auto": n}


def once() -> None:
    from ..db import connect, log_job
    with connect() as conn:
        job = f"controllo_foto:{VERSIONE}"
        if conn.execute("SELECT 1 FROM job_runs WHERE job=%s AND ok", (job,)).fetchone():
            return
        finish = log_job(conn, job)
        try:
            finish(True, run(conn))
        except Exception as e:
            log.exception("controllo foto non riuscito")
            finish(False, {"errore": str(e)[:300]})
