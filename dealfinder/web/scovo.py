"""Dati per il sito Scovo: schede compatte, lette direttamente dal database (veloci).

Per ogni auto il sito riceve solo quello che mostra:
  prezzo richiesto, riparazioni da–a (solo pezzi), prezzo medio di mercato.
La rivendita (mercato meno lo sconto scelto, 30% di default) e il guadagno da–a
si calcolano sul telefono, così cambiando lo sconto tutto si aggiorna subito:
  guadagno = mercato × (1 − sconto) − prezzo − costi fissi (passaggio 90 + pulizia 50) − riparazioni

I dati personali dei venditori non si mostrano: i numeri di telefono vengono tolti
dalla descrizione; il commerciante chiama solo dal bottone "Parla col venditore",
che conta come apertura (dopo 7 commercianti diversi l'auto sparisce).
"""
from __future__ import annotations

import json
import re

from . import foto

COSTI_FISSI = {"passaggio": 90, "pulizia": 50}
SCONTO_DEFAULT = 30

LIST_SQL = """
SELECT l.id, l.source, l.url, l.title, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox,
       l.power_kw, l.city, l.province, l.price_eur, l.damage_class, l.stage_reason, l.first_seen_at, l.deep_at,
       v.private_median, v.resale_median_private, v.resale_median, v.parts_cost_low, v.parts_cost_high,
       v.n_comparables, v.confidence,
       (SELECT count(*) FROM listing_opens o WHERE o.listing_id=l.id) AS opens,
       EXISTS (SELECT 1 FROM listing_opens o WHERE o.listing_id=l.id AND o.dealer_id=%(me)s) AS opened_by_me,
       (SELECT count(*) FROM listing_photos p WHERE p.listing_id=l.id) AS n_photos
FROM listings l
JOIN LATERAL (SELECT * FROM valuations WHERE listing_id=l.id ORDER BY created_at DESC LIMIT 1) v ON true
WHERE l.stage='approfondito' AND l.status='attivo'
  AND l.stage_reason IN ('opportunita','da_verificare')
  AND l.last_checked_at > now() - interval '48 hours'
"""

DETAIL_EXTRA_SQL = """
SELECT l.description, l.seller_type, v.parts_detail, v.checks, v.motivation
FROM listings l
JOIN LATERAL (SELECT * FROM valuations WHERE listing_id=l.id ORDER BY created_at DESC LIMIT 1) v ON true
WHERE l.id=%s
"""

MAKE_NAMES = {"bmw": "BMW", "mg": "MG", "ds": "DS", "alfa-romeo": "Alfa Romeo", "land-rover": "Land Rover",
              "mercedes": "Mercedes", "volkswagen": "Volkswagen", "citroen": "Citroën", "skoda": "Škoda",
              "mini": "MINI", "seat": "SEAT", "cupra": "Cupra", "byd": "BYD", "kia": "Kia"}
SOURCE_NAMES = {"subito": "Subito", "facebook": "Facebook Marketplace"}
FUEL_NAMES = {"gpl": "GPL", "ibrida": "ibrida", "elettrica": "elettrica", "metano": "metano",
              "diesel": "diesel", "benzina": "benzina"}

# cellulari italiani (3xx xxx xxxx) e fissi solo se preceduti da "tel", "cell", "chiamare"...
_SEP = r"[\s.\-/]?"
RE_MOBILE = re.compile(r"(?<![\w.,])(?:\+|00)?(?:39[\s.\-]?)?(3\d{2}" + _SEP + r"(?:\d{3}" + _SEP + r"\d{3,4}|\d{4}" + _SEP
                       + r"\d{3}|\d{3}" + _SEP + r"\d{2}" + _SEP + r"\d{2}|\d{2}" + _SEP + r"\d{2}" + _SEP + r"\d{2,3}))(?![\d.,]*\d)")
RE_LANDLINE = re.compile(r"(?i)(?:tel(?:efono|efonare)?|cell(?:ulare)?|chiama(?:re|temi)?|numero|whats\s?app|wa)\W{0,6}"
                         r"((?:\+39[\s.\-]?)?0\d{1,3}[\s.\-/]?\d{5,8})(?!\d)")


def make_name(make: str | None) -> str:
    if not make:
        return ""
    return MAKE_NAMES.get(make, make.replace("-", " ").title())


def model_name(model: str | None) -> str:
    if not model:
        return ""
    if re.fullmatch(r"[a-z]{1,3}-\d{1,3}", model):      # mx-5, cx-30, c-3
        return model.upper()
    words = model.replace("-", " ").split()
    out = []
    for w in words:
        if re.fullmatch(r"[a-z]{1,3}\d*|\d+[a-z]*|[a-z]+\d+[a-z]*", w) and len(w) <= 4:
            out.append(w.upper())            # x1, cx, 3008, gti
        else:
            out.append(w.capitalize())
    return " ".join(out)


def version_short(v: str | None, model: str | None) -> str:
    if not v:
        return ""
    v = re.sub(r"\s+", " ", v).strip()
    if model:
        # toglie il modello ripetuto all'inizio della versione ("MX-5 Roadster" con modello "mx-5")
        key = re.sub(r"[^a-z0-9]", "", model.lower())
        i, acc = 0, ""
        while i < len(v) and len(acc) < len(key):
            if v[i].isalnum():
                acc += v[i].lower()
            i += 1
        if key and acc == key and (i == len(v) or not v[i].isalnum()):
            v = v[i:].strip(" -")
    return v[:28].rsplit(" ", 1)[0] if len(v) > 28 else v


def nome(r: dict) -> str:
    bits = [make_name(r.get("make")), model_name(r.get("model")), version_short(r.get("version_raw"), r.get("model"))]
    n = " ".join(b for b in bits if b).strip()
    return n or (r.get("title") or "Auto")[:60]


def find_phone(text: str | None) -> str | None:
    if not text:
        return None
    m = RE_MOBILE.search(text)
    if m:
        return "+39" + re.sub(r"\D", "", m.group(1))
    m = RE_LANDLINE.search(text)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        digits = digits[2:] if digits.startswith("39") and len(digits) > 10 else digits
        return "+39" + digits
    return None


def hide_phones(text: str) -> str:
    text = RE_MOBILE.sub("[numero col bottone]", text)
    return RE_LANDLINE.sub(lambda m: m.group(0).replace(m.group(1), "[numero col bottone]"), text)


def clean_desc(text: str | None, limit: int = 700) -> str:
    if not text:
        return ""
    t = re.sub(r"[ \t]+", " ", hide_phones(text)).strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"\S+@\S+\.\w+", "[email nascosta]", t)
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0] + "…"
    return t


def zona(r: dict) -> str:
    city, prov = r.get("city"), r.get("province")
    if city and prov:
        return f"{city} ({prov})"
    return city or prov or "Lombardia"


def visible(r: dict, max_opens: int) -> bool:
    return bool(r["opened_by_me"]) or (r["opens"] or 0) < max_opens


def item(r: dict) -> dict | None:
    mercato = r.get("private_median") or r.get("resale_median_private") or r.get("resale_median")
    price = r.get("price_eur")
    if not mercato or not price:
        return None
    damaged = (r.get("damage_class") or "sconosciuto") not in ("nessuno", "sconosciuto")
    lo, hi = r.get("parts_cost_low"), r.get("parts_cost_high")
    if hi is None:
        if damaged:
            return None                       # danni senza stima dei pezzi: non si propone
        lo = hi = 0
    lo = int(lo if lo is not None else hi)
    n_ph = int(r.get("n_photos") or 0)
    return {
        "id": r["id"],
        "marca": make_name(r.get("make")) or "Altro",
        "nome": nome(r),
        "anno": r.get("year"), "km": r.get("mileage_km"),
        "carb": FUEL_NAMES.get(r.get("fuel") or "", r.get("fuel")),
        "cambio": r.get("gearbox"),
        "zona": zona(r),
        "fonte": SOURCE_NAMES.get(r.get("source"), r.get("source")),
        "prezzo": int(price),
        "mercato": int(round(float(mercato))),
        "rip_lo": lo, "rip_hi": int(hi),
        "danno": r.get("damage_class") or "sconosciuto",
        "verificare": r.get("stage_reason") == "da_verificare",
        "confronti": r.get("n_comparables"),
        "foto": foto.url_for(r["id"], 0) if n_ph else None,
        "n_foto": min(n_ph, foto.MAX_PER_AUTO),
        "nuova": r.get("deep_at"),
        "aperta": bool(r.get("opened_by_me")),
    }


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def parts_items(parts) -> list[dict]:
    parts = _j(parts) or {}
    out = []
    for p in parts.get("lines") or []:
        label = p.get("label") or p.get("part")
        hi = p.get("high")
        if not label or hi is None:
            continue
        label = str(label).replace("_", " ").strip()
        out.append({"pezzo": label[:1].upper() + label[1:], "da": int(p.get("low") if p.get("low") is not None else hi),
                    "a": int(hi)})
    return out


CHECK_SKIP = re.compile(r"(?i)margine|soglia|iva|riserva")


def detail(base: dict, extra: dict) -> dict:
    d = dict(base)
    d["descrizione"] = clean_desc(extra.get("description"))
    d["pezzi"] = parts_items(extra.get("parts_detail"))
    d["foto_tutte"] = [foto.url_for(base["id"], i) for i in range(base["n_foto"])]
    d["controlli"] = [c for c in (extra.get("checks") or []) if not CHECK_SKIP.search(c)][:6]
    d["privato"] = (extra.get("seller_type") or "privato") == "privato"
    d["tel"] = bool(find_phone(extra.get("description")))
    return d
