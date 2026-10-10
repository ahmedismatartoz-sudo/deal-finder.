"""Accesso all'AI (Claude API) con risposte JSON e registro dei consumi.

I nomi dei modelli e la versione dello strumento di ricerca web sono
configurabili da variabili d'ambiente, per aggiornarli senza toccare il codice.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

MODEL_FAST = os.environ.get("AI_MODEL_FAST", "claude-haiku-5-5")      # filtro veloce, testo
MODEL_DEEP = os.environ.get("AI_MODEL_DEEP", "claude-sonnet-5-5")     # foto approfondite, ricambi
WEB_SEARCH_TOOL = os.environ.get("AI_WEB_SEARCH_TOOL", "web_search_20250305")
MAX_IMAGE_SIDE = int(os.environ.get("AI_FOTO_LATO", "1024"))         # foto approfondite
SCREEN_IMAGE_SIDE = int(os.environ.get("AI_FOTO_LATO_FILTRO", "768"))  # filtro: foto più piccole, costano meno

# Prezzi in dollari per milione di token (entrata, uscita) e per ricerca web: servono a calcolare
# la spesa del giorno e a fermarsi al tetto AI_BUDGET_GIORNO. Aggiornabili con AI_PREZZI (JSON).
PREZZI = {"haiku": (0.10, 0.50), "sonnet": (2.0, 10.0), "opus": (5.0, 25.0), "gemini": (0.0, 0.0)}
PREZZO_RICERCA_WEB = 0.01
try:
    PREZZI.update({k: tuple(v) for k, v in json.loads(os.environ.get("AI_PREZZI", "{}")).items()})
except Exception:
    pass


def costo(model: str, input_tokens: int, output_tokens: int, web_searches: int = 0) -> float:
    m = (model or "").lower()
    fam = next((k for k in PREZZI if k in m), "sonnet")
    pin, pout = PREZZI[fam]
    return input_tokens / 1e6 * pin + output_tokens / 1e6 * pout + web_searches * PREZZO_RICERCA_WEB


def spesa_oggi(conn) -> float:
    """Dollari spesi oggi (ora italiana) in AI, dal registro ai_usage."""
    rows = conn.execute("SELECT model, sum(input_tokens) AS i, sum(output_tokens) AS o, sum(web_searches) AS w "
                        "FROM ai_usage WHERE at > date_trunc('day', now() AT TIME ZONE 'Europe/Rome') AT TIME ZONE 'Europe/Rome' "
                        "GROUP BY model").fetchall()
    return round(sum(costo(r["model"], int(r["i"] or 0), int(r["o"] or 0), int(r["w"] or 0)) for r in rows), 4)


def budget_giorno() -> float:
    return float(os.environ.get("AI_BUDGET_GIORNO", "5"))


def budget_finito(conn) -> bool:
    try:
        return spesa_oggi(conn) >= budget_giorno()
    except Exception:
        return False


@dataclass
class AIResult:
    data: dict | list | None
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    web_searches: int = 0
    raw_text: str = ""
    sources: list[dict] = field(default_factory=list)


class AIError(RuntimeError):
    pass


_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise AIError("ANTHROPIC_API_KEY non impostata")
        _client = anthropic.Anthropic()
    return _client


def extract_json(text: str):
    """Estrae il primo oggetto/array JSON dalla risposta."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise AIError("nessun JSON nella risposta")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start:end + 1])


def image_block(url: str, http=None, side: int | None = None) -> dict | None:
    """Scarica una foto, la riduce e la passa come base64 (più affidabile dei link diretti).
    Le foto di Subito salvate come "imgid:..." vengono trasformate nel link vero."""
    try:
        import httpx
        from PIL import Image
        from ..web.foto import UA, candidate_urls
        http = http or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": UA})
        content = None
        for u in candidate_urls(url) or [url]:
            headers = {"Referer": "https://www.subito.it/"} if "sbito.it" in u or "subito.it" in u else {}
            r = http.get(u, headers=headers)
            if r.status_code == 200 and r.content:
                content = r.content
                break
        if content is None:
            raise ValueError("foto non scaricabile")
        img = Image.open(io.BytesIO(content)).convert("RGB")
        lato = side or MAX_IMAGE_SIDE
        img.thumbnail((lato, lato))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(buf.getvalue()).decode()}}
    except Exception as e:  # una foto rotta non deve fermare l'analisi
        log.warning("foto non scaricabile %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Fornitore AI: Anthropic (predefinito) oppure Google Gemini (ha un livello gratuito).
# Si sceglie da solo in base alla chiave presente, o con AI_PROVIDER=anthropic|gemini.
# ---------------------------------------------------------------------------
GEMINI_MODEL_FAST = os.environ.get("GEMINI_MODEL_FAST", "gemini-2.5-flash")
GEMINI_MODEL_DEEP = os.environ.get("GEMINI_MODEL_DEEP", "gemini-2.5-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_GEMINI_LAST = 0.0


def provider() -> str | None:
    forced = os.environ.get("AI_PROVIDER")
    if forced:
        return forced
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def available() -> bool:
    return provider() is not None


def _gemini(task, model, system, content, max_tokens, web_search, usage_sink, listing_id) -> AIResult:
    import httpx
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise AIError("GEMINI_API_KEY non impostata")
    gmodel = GEMINI_MODEL_DEEP if model == MODEL_DEEP else GEMINI_MODEL_FAST
    parts = []
    for b in content:
        if b.get("type") == "text":
            parts.append({"text": b["text"]})
        elif b.get("type") == "image" and b.get("source", {}).get("type") == "base64":
            parts.append({"inline_data": {"mime_type": b["source"]["media_type"], "data": b["source"]["data"]}})
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"maxOutputTokens": max(max_tokens, 2048), "temperature": 0.2}}
    if web_search is not None:
        body["tools"] = [{"google_search": {}}]
    else:
        body["generationConfig"]["responseMimeType"] = "application/json"
    # ritmo: il livello gratuito ha limiti di richieste al minuto
    import time
    global _GEMINI_LAST
    wait = float(os.environ.get("GEMINI_MIN_INTERVAL", "7")) - (time.time() - _GEMINI_LAST)
    if wait > 0:
        time.sleep(wait)
    _GEMINI_LAST = time.time()
    last = None
    for attempt in range(4):
        r = httpx.post(GEMINI_URL.format(model=gmodel), params={"key": key}, json=body, timeout=180)
        if r.status_code == 429 or r.status_code >= 500:
            last = r.status_code
            import time
            time.sleep(15 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise AIError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        break
    else:
        raise AIError(f"Gemini non disponibile (HTTP {last}) dopo vari tentativi")
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
    sources = [{"url": c.get("web", {}).get("uri"), "title": c.get("web", {}).get("title")}
               for c in (cand.get("groundingMetadata") or {}).get("groundingChunks", []) if c.get("web")]
    um = data.get("usageMetadata") or {}
    result = AIResult(data=None, model=gmodel, input_tokens=um.get("promptTokenCount", 0),
                      output_tokens=um.get("candidatesTokenCount", 0),
                      web_searches=len((cand.get("groundingMetadata") or {}).get("webSearchQueries", []) or []),
                      raw_text=text, sources=sources)
    if usage_sink:
        usage_sink(task, result, listing_id)
    result.data = extract_json(text)
    return result


def ask_json(task: str, model: str, system: str, content: list[dict], max_tokens: int = 1500,
             web_search: dict | None = None, usage_sink=None, listing_id: int | None = None) -> AIResult:
    """Chiama il modello e restituisce JSON. `web_search` attiva la ricerca web
    (es. {"max_uses": 5, "user_location": {...}})."""
    if provider() == "gemini":
        return _gemini(task, model, system, content, max_tokens, web_search, usage_sink, listing_id)
    kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": content}])
    if web_search is not None:
        kwargs["tools"] = [{"type": WEB_SEARCH_TOOL, "name": "web_search", **web_search}]
    resp = client().messages.create(**kwargs)

    texts, sources = [], []
    for block in resp.content:
        btype = getattr(block, "type", "")
        if btype == "text":
            texts.append(block.text)
            for c in getattr(block, "citations", None) or []:
                url = getattr(c, "url", None)
                if url:
                    sources.append({"url": url, "title": getattr(c, "title", None)})
    text = "".join(texts)
    usage = resp.usage
    searches = 0
    stu = getattr(usage, "server_tool_use", None)
    if stu is not None:
        searches = getattr(stu, "web_search_requests", 0) or 0
    result = AIResult(data=None, model=model, input_tokens=usage.input_tokens,
                      output_tokens=usage.output_tokens, web_searches=searches,
                      raw_text=text, sources=sources)
    if usage_sink:
        usage_sink(task, result, listing_id)
    result.data = extract_json(text)
    return result


def db_usage_sink(conn):
    """Registra i consumi nella tabella ai_usage."""
    def sink(task: str, r: AIResult, listing_id: int | None):
        conn.execute("INSERT INTO ai_usage (task, model, input_tokens, output_tokens, web_searches, listing_id) "
                     "VALUES (%s,%s,%s,%s,%s,%s)",
                     (task, r.model, r.input_tokens, r.output_tokens, r.web_searches, listing_id))
    return sink
