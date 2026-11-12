"""12/11: due controlli HTTP usati da SecurityMiddleware (app.py).

1. Limite vero alla grandezza del corpo delle richieste. Prima si guardava solo l'intestazione Content-Length: con
   l'invio "a pezzi" (Transfer-Encoding: chunked, senza Content-Length) il corpo veniva letto tutto in memoria da
   request.body(). Ora i byte si contano mentre arrivano:
   - senza Content-Length il corpo si legge qui, a pezzi, fino al limite: oltre il limite si risponde 413 e non si
     legge altro; sotto il limite si passa all'app quello che è arrivato;
   - con Content-Length oltre il limite: 413 subito; sotto il limite si contano comunque i byte (un client che manda
     più di quanto dichiarato viene fermato).
2. Rinnovo del token di accesso con l'uso (auth.rinnovo_token): intestazione X-Scovo-Token nelle risposte riuscite
   delle API chiamate con "Authorization: Bearer …".
"""
from __future__ import annotations

import json

from .auth import read_token, rinnovo_token

METODI_CON_CORPO = {"POST", "PUT", "PATCH", "DELETE"}
TROPPO_GRANDE = {"error": "Richiesta troppo grande: usa meno foto"}


class CorpoTroppoGrande(Exception):
    pass


async def _rispondi_413(send) -> None:
    corpo = json.dumps(TROPPO_GRANDE).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(corpo)).encode()),
                            (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": corpo})


async def limita_corpo(scope, receive, send, max_bytes: int):
    """Restituisce il `receive` da passare all'app, oppure None se ha già risposto 413."""
    headers = dict(scope.get("headers") or [])
    cl = headers.get(b"content-length")
    if cl is not None:
        try:
            if int(cl) > max_bytes:
                await _rispondi_413(send)
                return None
        except ValueError:
            await _rispondi_413(send)
            return None
        letti = 0

        async def receive_contato():
            nonlocal letti
            msg = await receive()
            if msg.get("type") == "http.request":
                letti += len(msg.get("body") or b"")
                if letti > max_bytes:
                    raise CorpoTroppoGrande()
            return msg
        return receive_contato
    if scope.get("method", "GET").upper() not in METODI_CON_CORPO:
        return receive
    # senza Content-Length: si legge qui, a pezzi, contando i byte
    pezzi, letti = [], 0
    while True:
        msg = await receive()
        if msg.get("type") != "http.request":          # il client se n'è andato
            return _riproduci([msg], receive)
        b = msg.get("body") or b""
        letti += len(b)
        if letti > max_bytes:
            await _rispondi_413(send)
            return None
        pezzi.append(b)
        if not msg.get("more_body"):
            break
    return _riproduci([{"type": "http.request", "body": b"".join(pezzi), "more_body": False}], receive)


def _riproduci(messaggi: list, receive):
    coda = list(messaggi)

    async def receive_bufferizzato():
        if coda:
            return coda.pop(0)
        return await receive()                          # poi solo l'eventuale http.disconnect
    return receive_bufferizzato


def token_rinnovato(scope) -> bytes | None:
    """Token nuovo da mandare al sito, se la richiesta ha un token valido da rinnovare."""
    path = str(scope.get("path") or "")
    if not path.startswith("/api/") or path.startswith("/api/foto/"):      # foto: risposte in cache, niente token
        return None
    auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
    if not auth.lower().startswith("bearer "):
        return None
    nuovo = rinnovo_token(read_token(auth[7:].strip()))
    return nuovo.encode() if nuovo else None
