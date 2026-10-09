"""Servizio "Vendi": il commerciante mette targa, km e foto; Scovo restituisce
l'auto esatta, il prezzo giusto per venderla e titolo e descrizione pronti.

Flusso:
  1. targa -> dati tecnici del veicolo (servizio esterno, ai/plate.py)
  2. prezzo dal nostro mercato: auto simili raccolte ogni giorno + modello dei prezzi
     -> tre prezzi: "vendi in fretta", "prezzo giusto", "prezzo alto"
  3. titolo e descrizione scritti dall'AI guardando dati e foto (con un testo di
     riserva se l'AI non è disponibile)
La targa non si salva: si usa solo per la ricerca.
"""
from __future__ import annotations

import logging
import re

from ..ai import client as ai
from ..ai import plate as plates
from ..core.consistency import MAKES
from ..core.models import Listing
from ..core.normalize import normalize_fields, slug
from ..core.vehicles import make_model_from_title

log = logging.getLogger("servizi.vendi")

FUEL_IT = {"benzina": "benzina", "diesel": "diesel", "gpl": "GPL", "metano": "metano",
           "ibrida": "ibrida", "elettrica": "elettrica"}


def canonical(vehicle: dict) -> tuple[str | None, str | None]:
    """Marca e modello nel formato della nostra base (es. "mercedes", "classe-a")."""
    raw_make = str(vehicle.get("make") or "").strip()
    raw_model = str(vehicle.get("model") or "").strip()
    make = MAKES.get(raw_make.lower(), slug(raw_make))
    _, model = make_model_from_title(f"{raw_make} {raw_model}")
    return make, model or slug(raw_model.split()[0] if raw_model else None)


def to_listing(vehicle: dict, km: int | None, gearbox: str | None = None) -> Listing:
    make, model = canonical(vehicle)
    l = Listing(source="scovo", source_id="vendi", url="", make=make, model=model,
                version_raw=vehicle.get("version"), year=vehicle.get("year"), mileage_km=km,
                fuel=vehicle.get("fuel"), gearbox=gearbox or vehicle.get("gearbox"),
                power_kw=vehicle.get("power_kw"))
    normalize_fields(l)
    l.make, l.model = make, model            # normalize_fields non deve cambiare la marca canonica
    return l


def round_price(p: float) -> int:
    """Prezzo "da annuncio": 7.950 invece di 7.963."""
    if p < 2000:
        return int(round(p / 50.0) * 50 - 10) if p > 100 else int(p)
    return int(round(p / 100.0) * 100 - 50)


def prices(l: Listing, market: list[Listing], model: dict | None) -> dict:
    """Tre prezzi di vendita a privati, dal nostro mercato."""
    from ..pricing.engine import value_listing
    from ..pricing.model import predict
    tmp = Listing(**{**l.__dict__, "price_eur": 1})
    v = value_listing(tmp, market)
    p = predict(model, l) if model else None
    mid = None
    source = None
    if v.private_median and v.n_comparables >= 5:
        mid, source = v.private_median, f"{v.n_comparables} auto simili in vendita in Lombardia"
        spread = min(max(v.dispersion or 0.12, 0.06), 0.2)
        low, high = mid * (1 - spread), mid * (1 + spread * 0.7)
    elif p:
        mid, source = p["p50"], "modello dei prezzi (poche auto uguali in vendita)"
        low, high = p["p25"], p["p75"]
    if not mid:
        return {"ok": False, "motivo": "Non ci sono abbastanza auto simili per stimare il prezzo"}
    return {"ok": True, "fonte": source, "confronti": v.n_comparables,
            "veloce": round_price(min(low, mid * 0.94)), "giusto": round_price(mid),
            "alto": round_price(max(high, mid * 1.05)),
            "giorni_vendita": round(v.liquidity_days) if v.liquidity_days else None}


SYSTEM_TEXT = """Sei un venditore d'auto esperto che scrive annunci su Subito e Facebook Marketplace in Italia.
Scrivi un annuncio onesto, chiaro e che vende. Usa SOLO i dati forniti e quello che si vede nelle foto:
non inventare optional, tagliandi o condizioni. Se nelle foto vedi danni o usura, dillo con tatto.
Rispondi SOLO JSON:
{"titolo": str (max 60 caratteri, marca modello versione anno, niente maiuscole urlate, niente emoji),
 "descrizione": str (5-10 righe brevi: dati principali, condizioni, punti di forza, cosa è incluso, invito a contattare),
 "punti_forza": [str] (max 4, brevi),
 "da_dichiarare": [str] (difetti visibili nelle foto da scrivere per correttezza; [] se nessuno),
 "colore": str|null}"""


def fallback_text(vehicle: dict, l: Listing, note: str | None) -> dict:
    name = " ".join(filter(None, [str(vehicle.get("make", "")).title(), str(vehicle.get("model", "")).title(),
                                  vehicle.get("version")]))
    name = re.sub(r"\s+", " ", name).strip()
    title = f"{name} {l.year or ''}".strip()[:60]
    km = f"{l.mileage_km:,}".replace(",", ".") + " km" if l.mileage_km else None
    righe = [f"{name}, anno {l.year}." if l.year else f"{name}.",
             ", ".join(filter(None, [km, FUEL_IT.get(l.fuel or "", l.fuel),
                                     f"{l.power_kw} kW" if l.power_kw else None,
                                     "cambio automatico" if l.gearbox == "automatico" else
                                     ("cambio manuale" if l.gearbox == "manuale" else None)])) + "."]
    if note:
        righe.append(note.strip())
    righe.append("Visibile su appuntamento, disponibile per prova. Contattami per informazioni.")
    return {"titolo": title, "descrizione": "\n".join(righe), "punti_forza": [], "da_dichiarare": [],
            "colore": None, "scritto_da": "modello"}


def write_text(vehicle: dict, l: Listing, price: int | None, photos: list[dict], note: str | None) -> dict:
    if not ai.available():
        return fallback_text(vehicle, l, note)
    facts = {"marca": vehicle.get("make"), "modello": vehicle.get("model"), "versione": vehicle.get("version"),
             "anno": l.year, "km": l.mileage_km, "carburante": l.fuel, "cambio": l.gearbox,
             "potenza_kw": l.power_kw, "cilindrata": vehicle.get("engine_cc"), "carrozzeria": vehicle.get("body_type"),
             "porte": vehicle.get("doors"), "prezzo": price, "note_del_commerciante": note}
    content = [{"type": "text", "text": "Dati dell'auto: " + str({k: v for k, v in facts.items() if v})}]
    content += photos[:6]
    try:
        r = ai.ask_json("vendi_testi", ai.MODEL_FAST, SYSTEM_TEXT, content, max_tokens=1200)
        d = r.data if isinstance(r.data, dict) else {}
        if d.get("titolo") and d.get("descrizione"):
            return {"titolo": str(d["titolo"])[:70], "descrizione": str(d["descrizione"])[:2500],
                    "punti_forza": [str(x)[:80] for x in (d.get("punti_forza") or [])][:4],
                    "da_dichiarare": [str(x)[:120] for x in (d.get("da_dichiarare") or [])][:6],
                    "colore": d.get("colore"), "scritto_da": "ai"}
    except Exception as e:
        log.warning("testi AI non riusciti: %s", str(e)[:200])
    return fallback_text(vehicle, l, note)


def photo_blocks(data_urls: list[str]) -> list[dict]:
    """Foto inviate dall'app come data URL (jpeg/png/webp), max 6."""
    out = []
    for u in (data_urls or [])[:6]:
        m = re.match(r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$", u or "")
        if m and len(m.group(2)) < 8_000_000:
            out.append({"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}})
    return out


def run(conn, targa: str, km: int | None, foto: list[str] | None = None, note: str | None = None,
        cambio: str | None = None, lookup=plates.lookup) -> dict:
    if not plates.valid_plate(targa):
        return {"ok": False, "errore": "Targa non valida: scrivila come AB123CD"}
    vehicle = plates.cached_lookup(conn, targa, lookup)
    if not vehicle:
        return {"ok": False, "errore": "Non troviamo questa targa. Controlla di averla scritta giusta."}
    l = to_listing(vehicle, km, cambio)
    from ..pricing.train import load_active
    from ..store import load_market
    market = load_market(conn, l.make, l.model, l.fuel) if l.make and l.model else []
    pr = prices(l, market, load_active(conn))
    text = write_text(vehicle, l, pr.get("giusto"), photo_blocks(foto or []), note)
    return {"ok": True,
            "auto": {"marca": vehicle.get("make"), "modello": vehicle.get("model"), "versione": vehicle.get("version"),
                     "anno": l.year, "carburante": l.fuel, "potenza_kw": l.power_kw, "km": l.mileage_km,
                     "cambio": l.gearbox},
            "prezzi": pr, "testo": text}
