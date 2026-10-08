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
MAX_IMAGE_SIDE = 1024


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


def image_block(url: str, http=None) -> dict | None:
    """Scarica una foto, la riduce e la passa come base64 (più affidabile dei link diretti)."""
    try:
        import httpx
        from PIL import Image
        http = http or httpx.Client(timeout=20, follow_redirects=True,
                                    headers={"User-Agent": "Mozilla/5.0"})
        r = http.get(url)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(buf.getvalue()).decode()}}
    except Exception as e:  # una foto rotta non deve fermare l'analisi
        log.warning("foto non scaricabile %s: %s", url, e)
        return None


def ask_json(task: str, model: str, system: str, content: list[dict], max_tokens: int = 1500,
             web_search: dict | None = None, usage_sink=None, listing_id: int | None = None) -> AIResult:
    """Chiama il modello e restituisce JSON. `web_search` attiva la ricerca web
    (es. {"max_uses": 5, "user_location": {...}})."""
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
