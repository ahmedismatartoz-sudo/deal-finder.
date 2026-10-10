"""Assistente di Scovo: si parla (o si scrive) e lui usa tutto il software.

Strumenti a disposizione (gli "agenti"):
  cerca_affari     filtra le auto proposte (marca, modello, prezzo, guadagno, sane/danni, anno, km, fonte)
  dettaglio_auto   scheda completa di un'auto
  prezzo_mercato   quanto vale oggi un'auto (marca, modello, anno, km) dal nostro mercato
  controlla_targa  dati tecnici dalla targa (a pagamento: solo con accesso, entro i limiti)
  valuta_vendita   prezzo giusto per vendere un'auto dalla targa
  prezzi_ricambi   pezzi di ricambio più economici per una targa

Con la chiave AI (ANTHROPIC_API_KEY) un modello sceglie gli strumenti, li chiama anche più
volte e risponde a voce. Senza chiave c'è un interprete semplice delle domande più comuni
(affari per marca/prezzo/guadagno, valore di un'auto, targa).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable

from ..config import settings
from ..core.consistency import MAKES, MODEL_MAKE
from ..core.vehicles import make_model_from_title
from . import scovo

log = logging.getLogger("assistente")
COSTI = sum(scovo.COSTI_FISSI.values())
MAX_PASSI = 6

SYSTEM = """Sei l'assistente vocale di Scovo, il servizio che trova auto usate di privati sotto prezzo a Milano e dintorni per i commercianti.
Rispondi in italiano, come un collega esperto: frasi brevi e chiare, perché la risposta viene letta ad alta voce.
Usa SEMPRE gli strumenti per i dati: non inventare auto, prezzi o guadagni.
Regole dei conti di Scovo: rivendita = prezzo di mercato meno lo sconto (30% se non specificato); guadagno = rivendita − prezzo − 140 € (passaggio e pulizia) − ricambi (solo pezzi, mai manodopera); il guadagno si dà sempre da–a.
Quando elenchi auto: al massimo 5 nella voce, con nome, anno, prezzo e guadagno; il resto lo vede nelle schede sotto la risposta.
Per targhe e ricambi usa gli strumenti dedicati; se servono km o pezzi che mancano, chiedili in una frase.
Non leggere link o codici. Niente elenchi puntati lunghi: massimo 6 frasi."""

TOOLS = [
    {"name": "cerca_affari", "description": "Cerca tra le auto proposte oggi da Scovo con filtri. Restituisce le auto ordinate.",
     "input_schema": {"type": "object", "properties": {
         "marca": {"type": "string", "description": "es. Audi, BMW, Fiat"},
         "modello": {"type": "string", "description": "es. Golf, Serie 1, TT"},
         "prezzo_min": {"type": "integer"}, "prezzo_max": {"type": "integer"},
         "guadagno_min": {"type": "integer", "description": "guadagno minimo nel caso peggiore, in euro"},
         "sconto": {"type": "integer", "description": "sconto di rivendita sotto mercato in %, predefinito 30"},
         "stato": {"type": "string", "enum": ["tutte", "sane", "danni"]},
         "anno_min": {"type": "integer"}, "km_max": {"type": "integer"},
         "fonte": {"type": "string", "enum": ["subito", "facebook"]},
         "ordina": {"type": "string", "enum": ["guadagno", "prezzo", "nuove"]},
         "limite": {"type": "integer", "description": "quante auto, massimo 10"}}}},
    {"name": "dettaglio_auto", "description": "Scheda completa di un'auto proposta (descrizione, pezzi da comprare, controlli).",
     "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "prezzo_mercato", "description": "Quanto vale oggi un'auto usata in Lombardia, dal mercato raccolto da Scovo.",
     "input_schema": {"type": "object", "properties": {
         "marca": {"type": "string"}, "modello": {"type": "string"}, "anno": {"type": "integer"},
         "km": {"type": "integer"}, "carburante": {"type": "string", "enum": ["benzina", "diesel", "gpl", "metano", "ibrida", "elettrica"]},
         "cambio": {"type": "string", "enum": ["manuale", "automatico"]}}, "required": ["marca", "modello", "anno"]}},
    {"name": "controlla_targa", "description": "Dati tecnici del veicolo dalla targa italiana (marca, modello, versione, motore, anno). Costa: usala solo se l'utente dà una targa.",
     "input_schema": {"type": "object", "properties": {"targa": {"type": "string"}}, "required": ["targa"]}},
    {"name": "valuta_vendita", "description": "Prezzo giusto per vendere un'auto dalla targa e dai km (tre prezzi: in fretta, giusto, alto).",
     "input_schema": {"type": "object", "properties": {"targa": {"type": "string"}, "km": {"type": "integer"}}, "required": ["targa"]}},
    {"name": "prezzi_ricambi", "description": "Offerte più economiche online per i pezzi di ricambio di un'auto, data la targa.",
     "input_schema": {"type": "object", "properties": {"targa": {"type": "string"},
                                                        "pezzi": {"type": "array", "items": {"type": "string"}}},
                      "required": ["targa", "pezzi"]}},
]


# ---------------------------------------------------------------------------
# Strumenti
# ---------------------------------------------------------------------------
def _slug_make(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip().lower()
    return MAKES.get(t) or MAKES.get(t.replace("-", " ")) or t.replace(" ", "-")


def _gain(it: dict, sconto: int) -> tuple[int, int, int]:
    riv = round(it["mercato"] * (1 - sconto / 100))
    base = riv - it["prezzo"] - COSTI
    return riv, base - it["rip_hi"], base - it["rip_lo"]


def cerca_affari(conn, user: dict, a: dict) -> dict:
    sconto = max(0, min(int(a.get("sconto") or 30), 60))
    rows = conn.execute(scovo.LIST_SQL + " LIMIT 3000", {"me": user["id"]}).fetchall()
    items = [it for it in (scovo.item(r) for r in rows if scovo.visible(r, settings.max_dealer_opens)) if it]
    marca = (a.get("marca") or "").strip().lower()
    modello = (a.get("modello") or "").strip().lower().replace("-", " ")
    out = []
    for it in items:
        riv, g_lo, g_hi = _gain(it, sconto)
        if g_hi <= 0:
            continue
        if marca and scovo.make_name(_slug_make(marca)).lower() != it["marca"].lower():
            continue
        if modello and modello not in it["nome"].lower().replace("-", " "):
            continue
        if a.get("prezzo_min") and it["prezzo"] < int(a["prezzo_min"]):
            continue
        if a.get("prezzo_max") and it["prezzo"] > int(a["prezzo_max"]):
            continue
        if a.get("guadagno_min") and g_lo < int(a["guadagno_min"]):
            continue
        if a.get("stato") == "sane" and it["rip_hi"] > 0:
            continue
        if a.get("stato") == "danni" and it["rip_hi"] == 0:
            continue
        if a.get("anno_min") and (it["anno"] or 0) < int(a["anno_min"]):
            continue
        if a.get("km_max") and it["km"] and it["km"] > int(a["km_max"]):
            continue
        if a.get("fonte") and a["fonte"].lower() not in (it["fonte"] or "").lower():
            continue
        out.append({**it, "riv": riv, "guadagno_da": g_lo, "guadagno_a": g_hi})
    ordina = a.get("ordina") or "guadagno"
    if ordina == "prezzo":
        out.sort(key=lambda x: x["prezzo"])
    elif ordina == "nuove":
        out.sort(key=lambda x: str(x.get("nuova")), reverse=True)
    else:
        out.sort(key=lambda x: -x["guadagno_da"])
    lim = max(1, min(int(a.get("limite") or 8), 10))
    filtri = {k: a[k] for k in ("modello", "prezzo_min", "prezzo_max", "guadagno_min", "stato", "anno_min", "km_max", "fonte")
              if a.get(k) not in (None, "", "tutte")}
    if marca:
        filtri["marca"] = scovo.make_name(_slug_make(marca))
    filtri["sconto"] = sconto
    return {"totale": len(out), "sconto": sconto, "filtri": filtri,
            "auto": [{k: x[k] for k in ("id", "nome", "anno", "km", "carb", "zona", "fonte", "prezzo", "mercato", "riv",
                                        "rip_lo", "rip_hi", "guadagno_da", "guadagno_a", "verificare", "foto")}
                     for x in out[:lim]]}


def dettaglio_auto(conn, user: dict, a: dict) -> dict:
    lid = int(a["id"])
    row = conn.execute(scovo.LIST_SQL + " AND l.id=%(id)s", {"me": user["id"], "id": lid}).fetchone()
    if not row or not scovo.visible(row, settings.max_dealer_opens):
        return {"errore": "auto non più disponibile"}
    extra = conn.execute(scovo.DETAIL_EXTRA_SQL, (lid,)).fetchone() or {}
    d = scovo.detail(scovo.item(row), extra)
    riv, g_lo, g_hi = _gain(d, 30)
    return {k: d.get(k) for k in ("id", "nome", "anno", "km", "carb", "zona", "prezzo", "mercato", "rip_lo", "rip_hi",
                                   "descrizione", "pezzi", "controlli", "verificare")} | {
        "rivendita_30": riv, "guadagno_da": g_lo, "guadagno_a": g_hi}


def prezzo_mercato(conn, user: dict, a: dict) -> dict:
    from ..servizi import vendi
    from ..pricing.train import load_active
    from ..store import load_market
    vehicle = {"make": a.get("marca"), "model": a.get("modello"), "year": a.get("anno"), "fuel": a.get("carburante")}
    km = a.get("km") or max(10000, (2026 - int(a.get("anno") or 2015)) * 15000)
    l = vendi.to_listing(vehicle, int(km), a.get("cambio"))
    if not (l.make and l.model):
        return {"errore": "modello non riconosciuto"}
    market = load_market(conn, l.make, l.model, l.fuel)
    pr = vendi.prices(l, market, load_active(conn))
    if pr.get("ok"):
        pr["rivendita_veloce_30"] = round(pr["giusto"] * 0.7)
        pr["km_usati_per_la_stima"] = km
        pr["auto"] = f"{scovo.make_name(l.make)} {scovo.model_name(l.model)} {l.year}"
    return pr


def _plate_tools(gate: Callable, log_use: Callable):
    from ..ai import plate as plates
    from ..servizi import ricambi, vendi

    def controlla_targa(conn, user, a):
        if user.get("role") == "ospite":
            return {"errore": "per le targhe serve entrare con il proprio account"}
        gate(conn, user, "vendi")
        v = plates.cached_lookup(conn, a.get("targa", ""), plates.lookup)
        log_use(conn, user, "vendi", bool(v))
        return v or {"errore": "targa non trovata"}

    def valuta_vendita(conn, user, a):
        if user.get("role") == "ospite":
            return {"errore": "per le targhe serve entrare con il proprio account"}
        gate(conn, user, "vendi")
        out = vendi.run(conn, str(a.get("targa", "")), a.get("km"), [], None, None)
        log_use(conn, user, "vendi", bool(out.get("ok")))
        out.pop("testo", None)
        return out

    def prezzi_ricambi(conn, user, a):
        if user.get("role") == "ospite":
            return {"errore": "per le targhe serve entrare con il proprio account"}
        gate(conn, user, "ricambi")
        out = ricambi.run(conn, str(a.get("targa", "")), [str(p) for p in (a.get("pezzi") or [])][:5])
        log_use(conn, user, "ricambi", bool(out.get("ok")))
        for p in out.get("pezzi") or []:
            p["offerte"] = (p.get("offerte") or [])[:3]
        return out
    return {"controlla_targa": controlla_targa, "valuta_vendita": valuta_vendita, "prezzi_ricambi": prezzi_ricambi}


# ---------------------------------------------------------------------------
# Con l'AI: il modello sceglie gli strumenti
# ---------------------------------------------------------------------------
def ask_ai(conn, user: dict, messages: list[dict], gate, log_use) -> dict:
    from ..ai import client as ai
    handlers = {"cerca_affari": cerca_affari, "dettaglio_auto": dettaglio_auto, "prezzo_mercato": prezzo_mercato,
                **_plate_tools(gate, log_use)}
    msgs = [{"role": m["role"], "content": str(m["content"])[:2000]} for m in messages[-10:]
            if m.get("role") in ("user", "assistant") and m.get("content")]
    cars, steps, filtri = [], [], None
    model = os.environ.get("ASSISTENTE_MODEL", ai.MODEL_DEEP)
    for _ in range(MAX_PASSI):
        resp = ai.client().messages.create(model=model, max_tokens=900, system=SYSTEM, tools=TOOLS, messages=msgs)
        try:
            ai.db_usage_sink(conn)("assistente", ai.AIResult(data=None, model=model, input_tokens=resp.usage.input_tokens,
                                                             output_tokens=resp.usage.output_tokens), None)
            conn.commit()
        except Exception:
            conn.rollback()
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            return {"risposta": text or "Non ho trovato una risposta.", "auto": cars, "passi": steps, "filtri": filtri}
        msgs.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        results = []
        for b in resp.content:
            if getattr(b, "type", "") != "tool_use":
                continue
            steps.append(b.name)
            try:
                out = handlers[b.name](conn, user, dict(b.input or {}))
            except Exception as e:              # limite giornaliero, targa sbagliata, servizio giù...
                msg = getattr(e, "message", None) or str(e)[:200]
                out = {"errore": msg}
            if b.name == "cerca_affari" and isinstance(out, dict):
                cars = out.get("auto") or cars
                filtri = {**(out.get("filtri") or {}), "totale": out.get("totale", 0)}
            results.append({"type": "tool_result", "tool_use_id": b.id,
                            "content": json.dumps(out, ensure_ascii=False, default=str)[:12000]})
        msgs.append({"role": "user", "content": results})
    return {"risposta": "Ho fatto troppi passaggi senza arrivare a una risposta: prova a chiedere in modo più semplice.",
            "auto": cars, "passi": steps}


# ---------------------------------------------------------------------------
# Senza AI: interprete delle domande più comuni
# ---------------------------------------------------------------------------
RE_TARGA = re.compile(r"\b([A-Za-z]{2})\s?(\d{3})\s?([A-Za-z]{2})\b")
RE_NUM = r"(\d{1,3}(?:[.\s]\d{3})+|\d+(?:[.,]\d+)?)\s*(mila|k|mille)?"
_ALIASES = sorted(MAKES, key=len, reverse=True)
RE_MAKE = re.compile(r"\b(" + "|".join(re.escape(a) for a in _ALIASES) + r")\b", re.I)


def _num(s: str, mult: str | None) -> int:
    s = s.replace(" ", "")
    v = float(s.replace(".", "")) if re.fullmatch(r"\d{1,3}(\.\d{3})+", s) else float(s.replace(",", "."))
    if mult or v < 100:                      # "6 mila", "6k", "sopra i 6" = migliaia
        v *= 1000
    return int(v)


STOP = {"ci", "sotto", "sopra", "oggi", "con", "che", "del", "di", "da", "per", "tra", "fino", "senza", "sane", "e",
        "a", "in", "le", "la", "il", "un", "una", "auto", "macchine", "macchina", "opportunita", "opportunità",
        "affari", "quanto", "vale", "costa", "prezzo", "oltre", "più", "piu", "meno", "entro", "massimo", "almeno"}


def _plausible_model(md: str) -> bool:
    w = md.replace("-", " ")
    if w.split()[0] in STOP:
        return False
    return (md in MODEL_MAKE or w.startswith(("serie ", "classe ")) or any(c.isdigit() for c in w)
            or (len(w) <= 3 and w.isalpha()))


def parse(text: str) -> dict:
    t = " " + text.lower().replace("€", " euro ") + " "
    q: dict = {}
    m = RE_TARGA.search(text)
    if m:
        q["targa"] = "".join(m.groups()).upper()
    mk = RE_MAKE.search(t)
    if mk:
        q["marca"] = MAKES[mk.group(1).lower()]
    mk2, md = make_model_from_title(t)
    if md and not _plausible_model(md):
        md = None
    if not md:                                   # modello senza marca: "una golf del 2017"
        for w in re.findall(r"[a-z0-9]+", t):
            if w in MODEL_MAKE and w not in STOP:
                md, mk2 = w, MODEL_MAKE[w]
                break
    if md:
        q["modello"] = md
        q.setdefault("marca", MAKES.get(mk2 or "", mk2) or MODEL_MAKE.get(md))
    for pat, key in ((r"(?:margine|guadagn\w*)[^\d]{0,25}(?:sopra|oltre|più di|piu di|almeno|minimo|da)\s*" + RE_NUM, "guadagno_min"),
                     (r"(?:sopra|oltre|più di|piu di|almeno)\s*(?:i\s*|ai\s*)?" + RE_NUM + r"[^\d]{0,15}(?:di\s+)?(?:margine|guadagn)", "guadagno_min"),
                     (r"(?:sotto|meno di|entro|fino a|massimo|max)\s*(?:i\s*)?" + RE_NUM, "prezzo_max"),
                     (r"(?:sopra|oltre|più di|piu di|almeno|da)\s*(?:i\s*)?" + RE_NUM + r"(?!\s*(?:di\s+)?(?:margine|guadagn|km))", "prezzo_min")):
        m = re.search(pat, t)
        if m and key not in q:
            q[key] = _num(m.group(1), m.group(2))
            t = t[:m.start()] + " " * (m.end() - m.start()) + t[m.end():]     # numero usato: non vale per altro
    m = re.search(r"tra\s*" + RE_NUM + r"\s*e\s*" + RE_NUM, t)
    if m:
        q["prezzo_min"], q["prezzo_max"] = _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
        t = t[:m.start()] + " " * (m.end() - m.start()) + t[m.end():]
    m = re.search(r"\b(19[89]\d|20[0-2]\d)\b(?!\s*(?:euro|km|mila))", t)
    if m:
        q["anno"] = int(m.group(1))
    m = re.search(RE_NUM + r"\s*(?:km|chilometri)", t)
    if m:
        q["km"] = _num(m.group(1), m.group(2))
    if re.search(r"\b(sane|senza danni|non incidentat)", t):
        q["stato"] = "sane"
    elif re.search(r"\b(incidentat|con danni|da sistemare|danneggiat)", t):
        q["stato"] = "danni"
    for f in ("facebook", "subito"):
        if f in t:
            q["fonte"] = f
    for f in ("diesel", "benzina", "gpl", "metano", "ibrida", "elettrica"):
        if f in t:
            q["carburante"] = f
    if re.search(r"ricamb|pezz|faro|paraurti|frizione|distribuzione", t):
        q["intento"] = "ricambi"
    elif "targa" in q and re.search(r"vend|vale|prezzo", t):
        q["intento"] = "vendita"
    elif "targa" in q:
        q["intento"] = "targa"
    elif re.search(r"quanto (?:vale|costa|viene)|che prezzo|prezzo di mercato|valore", t) and q.get("modello"):
        q["intento"] = "mercato"
    else:
        q["intento"] = "affari"
    if q["intento"] == "ricambi":
        m = re.search(r"(?:ricambi|pezzi|prezzi)\s*(?:per|di|del|della)?\s*[:]?\s*(.+)$", text, re.I)
        tail = RE_TARGA.sub("", m.group(1) if m else text)
        parts = [p.strip(" .,") for p in re.split(r",| e |;", tail) if len(p.strip(" .,")) > 3]
        q["pezzi"] = [p for p in parts if not re.search(r"targa|controll|dimmi|prezz", p, re.I)][:5]
    return q


def eur(n) -> str:
    return f"{round(n):,}".replace(",", ".") + " euro"


def ask_simple(conn, user: dict, text: str, gate, log_use) -> dict:
    q = parse(text)
    tools = _plate_tools(gate, log_use)
    try:
        if q["intento"] == "mercato":
            pr = prezzo_mercato(conn, user, {"marca": q.get("marca"), "modello": q["modello"],
                                             "anno": q.get("anno") or 2015, "km": q.get("km"), "carburante": q.get("carburante")})
            if not pr.get("ok"):
                return {"risposta": pr.get("motivo") or pr.get("errore") or "Non ho abbastanza auto simili per stimarla.", "auto": []}
            return {"risposta": f"Una {pr['auto']} con circa {pr['km_usati_per_la_stima']:,} km oggi vale intorno a {eur(pr['giusto'])} tra privati, "
                                f"da {eur(pr['veloce'])} per vendere in fretta a {eur(pr['alto'])} se si ha pazienza. "
                                f"Per rivenderla in fretta al 30% sotto mercato conta circa {eur(pr['rivendita_veloce_30'])}. "
                                f"Stima basata su {pr.get('fonte')}.".replace(",", "."), "auto": []}
        if q["intento"] in ("targa", "vendita", "ricambi"):
            if q["intento"] == "ricambi":
                if not q.get("pezzi"):
                    return {"risposta": "Dimmi quali pezzi ti servono, per esempio: faro anteriore sinistro e paraurti.", "auto": []}
                out = tools["prezzi_ricambi"](conn, user, {"targa": q["targa"], "pezzi": q["pezzi"]})
                if out.get("errore") or not out.get("ok"):
                    return {"risposta": out.get("errore") or "Non riesco a cercare i ricambi adesso.", "auto": []}
                frasi = [f"Per la {out['auto']}:"]
                for p in out["pezzi"]:
                    b = p.get("migliore")
                    frasi.append(f"{p['pezzo']}: il più economico costa {eur(b['totale'])} ({b['tipo'].lower()})." if b
                                 else f"{p['pezzo']}: nessuna offerta sicura trovata.")
                frasi.append(f"In tutto spendi circa {eur(out['totale_migliori'])}.")
                return {"risposta": " ".join(frasi), "auto": []}
            out = tools["valuta_vendita"](conn, user, {"targa": q["targa"], "km": q.get("km")})
            if out.get("errore") or not out.get("ok"):
                return {"risposta": out.get("errore") or "Non trovo questa targa.", "auto": []}
            a, pr = out["auto"], out.get("prezzi") or {}
            nome = " ".join(str(x) for x in (a.get("marca"), a.get("modello"), a.get("versione"), a.get("anno")) if x)
            if not pr.get("ok"):
                return {"risposta": f"La targa è di una {nome}. Non ho abbastanza auto simili per darti un prezzo.", "auto": []}
            return {"risposta": f"La targa è di una {nome}. Prezzo giusto per venderla: {eur(pr['giusto'])}; "
                                f"in fretta {eur(pr['veloce'])}, con calma fino a {eur(pr['alto'])}.", "auto": []}
        args = {k: q[k] for k in ("marca", "modello", "prezzo_min", "prezzo_max", "guadagno_min", "stato", "fonte") if k in q}
        if q.get("anno"):
            args["anno_min"] = q["anno"]
        res = cerca_affari(conn, user, {**args, "limite": 10})
    except Exception as e:
        return {"risposta": getattr(e, "message", None) or "Qualcosa non ha funzionato, riprova tra poco.", "auto": []}
    n = res["totale"]
    filtro = " ".join(x for x in (scovo.make_name(args.get("marca")) if args.get("marca") else "",
                                  scovo.model_name(args.get("modello")) if args.get("modello") else "") if x)
    if not n:
        return {"risposta": f"Oggi non ci sono auto {filtro} con questi filtri. Prova ad allargare il prezzo o il guadagno.".replace("  ", " "),
                "auto": []}
    top = res["auto"][:3]
    frasi = [f"Ho trovato {n} {'auto' if n != 1 else 'auto'}{(' ' + filtro) if filtro else ''}. Le migliori:"]
    for c in top:
        g = (f"guadagno circa {eur(c['guadagno_a'])}" if c["guadagno_da"] == c["guadagno_a"]
             else f"guadagno da {eur(max(c['guadagno_da'], 0))} a {eur(c['guadagno_a'])}")
        frasi.append(f"{c['nome']} del {c['anno']}, costa {eur(c['prezzo'])}, {g}.")
    if n > 3:
        frasi.append(f"Te le metto tutte e {n} sullo schermo.")
    return {"risposta": " ".join(frasi), "auto": res["auto"], "filtri": {**res["filtri"], "totale": n}}


def answer(conn, user: dict, messages: list[dict], gate, log_use) -> dict:
    from ..ai import client as ai
    if os.environ.get("ANTHROPIC_API_KEY") and ai.provider() == "anthropic":
        try:
            return ask_ai(conn, user, messages, gate, log_use)
        except Exception as e:
            log.warning("assistente AI non riuscito: %s", str(e)[:200])
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    out = ask_simple(conn, user, str(last), gate, log_use)
    out["semplice"] = True
    return out
