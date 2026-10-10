"""Voce naturale per l'assistente: sintesi (testo → audio) e trascrizione (audio → testo).

La voce del telefono (speechSynthesis) suona robotica: se c'è una chiave di un servizio
di voce, il sito chiede l'audio al server e lo riproduce.

  OPENAI_API_KEY      voce gpt-4o-mini-tts (stessa famiglia di voci di ChatGPT) e trascrizione
  ELEVENLABS_API_KEY  voce ElevenLabs (molto naturale) e trascrizione Scribe

Variabili facoltative:
  VOCE_PROVIDER       "openai" o "elevenlabs" se ci sono tutte e due le chiavi
  VOCE_OPENAI         nome della voce OpenAI (predefinita "coral")
  VOCE_ISTRUZIONI     come deve parlare (tono, ritmo)
  ELEVENLABS_VOICE_ID voce ElevenLabs (predefinita una voce multilingue)
  ELEVENLABS_MODEL    modello ElevenLabs (predefinito eleven_flash_v2_5, il più veloce)

Senza chiavi il sito usa la voce e il riconoscimento del telefono, come prima.
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections import OrderedDict

log = logging.getLogger("voce")

MAX_TESTO = 700
MAX_AUDIO = 4 * 1024 * 1024
ISTRUZIONI = ("Parla in italiano con pronuncia italiana naturale, come un collega commerciante d'auto di Milano "
              "al telefono: tono cordiale, sicuro e rilassato, ritmo vivace ma chiaro, pause naturali tra le frasi. "
              "Leggi i prezzi in euro in modo naturale.")
_cache: "OrderedDict[str, bytes]" = OrderedDict()


def provider(kind: str = "tts") -> str | None:
    want = (os.environ.get("VOCE_PROVIDER") or "").strip().lower()
    have = [p for p, k in (("openai", "OPENAI_API_KEY"), ("elevenlabs", "ELEVENLABS_API_KEY")) if os.environ.get(k)]
    if want in have:
        return want
    return have[0] if have else None


def info() -> dict:
    p = provider()
    return {"tts": bool(p), "stt": bool(p), "provider": p}


def _http():
    import httpx
    return httpx.Client(timeout=httpx.Timeout(30, connect=8))


def clean(text: str) -> str:
    t = " ".join(str(text or "").split())
    for a, b in (("€", " euro"), ("–", " a "), ("+", " più "), ("km ", "chilometri ")):
        t = t.replace(a, b)
    return t[:MAX_TESTO]


def tts(text: str) -> bytes:
    """Audio MP3 della frase. Solleva un'eccezione se il servizio non risponde."""
    text = clean(text)
    if not text:
        raise ValueError("testo vuoto")
    p = provider()
    if not p:
        raise RuntimeError("nessun servizio di voce configurato")
    key = hashlib.sha256(f"{p}|{os.environ.get('VOCE_OPENAI', '')}|{os.environ.get('ELEVENLABS_VOICE_ID', '')}|{text}".encode()).hexdigest()
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    with _http() as http:
        if p == "openai":
            r = http.post("https://api.openai.com/v1/audio/speech",
                          headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
                          json={"model": os.environ.get("VOCE_OPENAI_MODEL", "gpt-4o-mini-tts"),
                                "voice": os.environ.get("VOCE_OPENAI", "coral"), "input": text,
                                "instructions": os.environ.get("VOCE_ISTRUZIONI", ISTRUZIONI),
                                "response_format": "mp3"})
        else:
            voice = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
            r = http.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_64",
                          headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                          json={"text": text, "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
                                "language_code": "it",
                                "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15}})
    if r.status_code != 200 or not r.content:
        log.warning("voce %s non riuscita: HTTP %s %s", p, r.status_code, r.text[:200])
        raise RuntimeError(f"servizio voce: HTTP {r.status_code}")
    _cache[key] = r.content
    while len(_cache) > 120:
        _cache.popitem(last=False)
    return r.content


def ext_for(mime: str) -> str:
    m = (mime or "").lower()
    for k, e in (("webm", "webm"), ("ogg", "ogg"), ("mp4", "mp4"), ("m4a", "m4a"), ("aac", "m4a"),
                 ("mpeg", "mp3"), ("mp3", "mp3"), ("wav", "wav")):
        if k in m:
            return e
    return "webm"


def stt(audio: bytes, mime: str) -> str:
    """Testo detto nella registrazione."""
    if not audio:
        return ""
    if len(audio) > MAX_AUDIO:
        raise ValueError("registrazione troppo lunga")
    p = provider("stt")
    if not p:
        raise RuntimeError("nessun servizio di voce configurato")
    name = "voce." + ext_for(mime)
    ctype = (mime or "audio/webm").split(";")[0]
    with _http() as http:
        if p == "openai":
            r = http.post("https://api.openai.com/v1/audio/transcriptions",
                          headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
                          data={"model": os.environ.get("VOCE_STT_MODEL", "gpt-4o-mini-transcribe"), "language": "it",
                                "prompt": "Commercianti di auto usate: marche, modelli, targhe italiane come AB123CD, prezzi in euro, ricambi."},
                          files={"file": (name, audio, ctype)})
        else:
            r = http.post("https://api.elevenlabs.io/v1/speech-to-text",
                          headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                          data={"model_id": "scribe_v1", "language_code": "ita"},
                          files={"file": (name, audio, ctype)})
    if r.status_code != 200:
        log.warning("trascrizione %s non riuscita: HTTP %s %s", p, r.status_code, r.text[:200])
        raise RuntimeError(f"servizio voce: HTTP {r.status_code}")
    return str((r.json() or {}).get("text") or "").strip()
