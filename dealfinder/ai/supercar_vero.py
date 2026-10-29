"""29/10 notte (cliente: "pulisci le supercar: margini gonfiati, auto non realmente in vendita, foto prese da internet,
niente leasing o finanziamenti"). Due controlli prima che una supercar vada sul sito:

1) regole fisse (filtra()): niente leasing/rate/noleggio, almeno SC_FOTO_MIN foto, almeno SC_NMIN confronti,
   prezzo non sotto il 70% della mediana (troppo bello per essere vero), margine ricalcolato con il prezzo di
   vendita reale (mediana degli annunci × SC_FATTORE, come la calibrazione delle altre fasce) e non oltre il 25%
   del prezzo;
2) l'AI guarda le foto e legge l'annuncio (controlla()): via le foto da internet (stampa, cataloghi, render,
   foto di concessionari con logo, immagini ripetute), le auto non davvero in vendita (cerco/permuta/solo
   prenotazione/export/da ordinare) e leasing o finanziamenti scritti in altre lingue.
Una supercar va sul sito solo se ha passato ENTRAMBI i controlli (senza controllo AI non si pubblica).
Cache in supercar_controllo (stesso annuncio = nessuna nuova chiamata).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re

log = logging.getLogger("supercar_vero")

LEASING_RE = re.compile(
    r"leasing|übernahme|uebernahme|subentro|noleggio|renting|\blease\b|anzahlung|rata mensile|al mese|monatlich|"
    r"/\s*mese|/\s*monat|mensualit|\bloyer\b|financement|finanziamento|finanzierung|financiering|financiaci|"
    r"maxirata|anticipo|\bLOA\b|\bLLD\b|balloon|restschuld|schlussrate", re.I)

SYSTEM = """Controlli annunci di auto sportive e di lusso vendute da privati in Europa per un commerciante italiano.
Ricevi titolo, prezzo, descrizione e alcune foto. Rispondi NO se ALMENO UNA di queste è vera:
- foto non scattate dal venditore: immagini di stampa o del costruttore, cataloghi, render 3D, sfondi da studio
  fotografico, foto con watermark o logo di siti/concessionari, screenshot, foto della stessa auto prese da internet;
- l'auto non è davvero in vendita a quel prezzo: subentro leasing, noleggio, rate, finanziamento, anticipo, maxi-rata,
  prenotazione/ordine di un'auto non ancora consegnata, "cerco", solo permuta, solo export, prezzo simbolico o finto;
- annuncio che sembra una truffa (prezzo troppo basso, contatti strani, auto all'estero "spedita con corriere").
Altrimenti rispondi OK. Rispondi con UNA riga: OK  oppure  NO <motivo in max 10 parole>"""


def _tabella(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS supercar_controllo (
        listing_id BIGINT PRIMARY KEY, chiave TEXT, ok BOOLEAN, motivo TEXT, computed_at TIMESTAMPTZ DEFAULT now())""")


def filtra(r: dict, v: dict, n_foto: int) -> tuple[str | None, dict]:
    """r: annuncio (title, version_raw, description, price_eur); v: valutazione (m, r, n, costi).
    Ritorna (motivo_esclusione | None, valori corretti {R, margine})."""
    testo = f"{r.get('title') or ''} {r.get('version_raw') or ''} {(r.get('description') or '')[:3000]}"
    if LEASING_RE.search(testo):
        return "leasing_finanziamento", {}
    if n_foto < int(os.environ.get("SC_FOTO_MIN", "5")):
        return "poche_foto", {}
    if int(v.get("n") or 0) < int(os.environ.get("SC_NMIN", "8")):
        return "pochi_confronti", {}
    prezzo, M = int(r["price_eur"]), int(v["m"])
    if prezzo < 0.70 * M:
        return "troppo_bella", {}
    f = float(os.environ.get("SC_FATTORE", "0.92"))           # da prezzo chiesto a prezzo di vendita reale
    R = min(int(v["r"]), int(M * f * 0.95))
    marg = R - prezzo - int(v.get("costi") or 0)
    if marg < int(os.environ.get("SUPERCAR_MARGINE_MIN", "5000")):
        return "margine_basso", {}
    if marg > 0.25 * prezzo:
        return "margine_irreale", {}
    return None, {"R": R, "margine": marg}


def letti(conn, ids: list[int]) -> dict:
    _tabella(conn)
    conn.commit()
    return {x["listing_id"]: x for x in conn.execute(
        "SELECT listing_id, chiave, ok, motivo FROM supercar_controllo WHERE listing_id = ANY(%s)", (ids,)).fetchall()}


def controlla(conn, r: dict, foto: list[str]) -> dict | None:
    from . import client as AI
    if not AI.available():
        return None
    desc = (r.get("description") or "").strip()
    chiave = hashlib.sha1(f"{r.get('price_eur')}|{desc[:3000]}|{'|'.join(foto[:3])}".encode()).hexdigest()[:16]
    _tabella(conn)
    vecchia = conn.execute("SELECT chiave, ok, motivo FROM supercar_controllo WHERE listing_id=%s", (r["id"],)).fetchone()
    if vecchia and vecchia["chiave"] == chiave:
        return dict(vecchia)
    if AI.budget_finito(conn):
        return None
    blocchi = [b for b in (AI.image_block(u, side=640) for u in foto[:3]) if b]
    if not blocchi:
        ok, motivo = False, "foto non scaricabili"
    else:
        testo = (f"TITOLO: {r.get('title') or ''} {r.get('version_raw') or ''} | anno {r.get('year')} | km {r.get('mileage_km')} | "
                 f"prezzo {r.get('price_eur')} € | paese {r.get('region') or r.get('province')}\nDESCRIZIONE: {desc[:2500] or '(nessuna)'}")
        resp = AI.crea(model=AI.MODEL_FAST, max_tokens=60, system=SYSTEM,
                       messages=[{"role": "user", "content": blocchi + [{"type": "text", "text": testo}]}])
        out = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()
        try:
            AI.db_usage_sink(conn)("supercar_vero", AI.AIResult(data=None, model=AI.MODEL_FAST, input_tokens=resp.usage.input_tokens,
                                                                 output_tokens=resp.usage.output_tokens), r["id"])
        except Exception:
            pass
        riga = (out.splitlines() or [""])[0].strip().lstrip("*").strip()
        if re.match(r"OK\b", riga, re.I):
            ok, motivo = True, None
        elif re.match(r"NO\b", riga, re.I):
            ok, motivo = False, re.sub(r"^NO\W*", "", riga, flags=re.I)[:120]
        else:
            log.warning("SUPERCAR_VERO %s risposta non leggibile: %s", r["id"], riga[:120])
            return None
    conn.execute("""INSERT INTO supercar_controllo (listing_id, chiave, ok, motivo, computed_at) VALUES (%s,%s,%s,%s,now())
                    ON CONFLICT (listing_id) DO UPDATE SET chiave=EXCLUDED.chiave, ok=EXCLUDED.ok, motivo=EXCLUDED.motivo,
                    computed_at=now()""", (r["id"], chiave, ok, motivo))
    conn.commit()
    if not ok:
        log.warning("SUPERCAR_VERO %s scartata: %s", r["id"], motivo)
    return {"ok": ok, "motivo": motivo}


def giro(limite: int = 400) -> dict:
    """Controlla con l'AI le supercar che passano le regole fisse e non sono ancora controllate."""
    from collections import Counter
    from ..db import connect
    from ..pipeline.supercar_eu import SITO_SQL
    st = Counter()
    with connect() as conn:
        if not conn.execute("SELECT to_regclass('supercar_eu') AS t").fetchone()["t"]:
            return dict(st)
        val = {x["listing_id"]: x for x in conn.execute(SITO_SQL, {"lim": limite}).fetchall()}
        if not val:
            return dict(st)
        rows = conn.execute("""SELECT id, title, version_raw, description, price_eur, year, mileage_km, region, province
                               FROM listings WHERE id = ANY(%s)""", (list(val),)).fetchall()
        foto = {}
        for p in conn.execute("SELECT listing_id, source_url FROM listing_photos WHERE listing_id = ANY(%s) ORDER BY listing_id, position",
                              (list(val),)).fetchall():
            foto.setdefault(p["listing_id"], []).append(p["source_url"])
        for r in rows:
            r = dict(r)
            motivo, _ = filtra(r, val[r["id"]], len(foto.get(r["id"], [])))
            if motivo:
                st[motivo] += 1
                continue
            try:
                esito = controlla(conn, r, foto.get(r["id"], []))
            except Exception as e:
                conn.rollback()
                st["errori"] += 1
                if st["errori"] < 3:
                    log.warning("SUPERCAR_VERO %s non controllata: %s", r["id"], str(e)[:160])
                continue
            if esito is None:
                st["non_controllate"] += 1
            else:
                st["vere" if esito["ok"] else "scartate_ai"] += 1
    log.warning("SUPERCAR_VERO_GIRO %s", dict(st))
    return dict(st)
