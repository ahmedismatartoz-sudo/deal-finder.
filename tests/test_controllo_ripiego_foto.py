"""22/11 (ripiego foto): Anthropic risponde 400 "Unable to download the file" quando UNA foto mandata per indirizzo
non è più scaricabile (annuncio tolto, foto scaduta) e tutta la chiamata cade.

- si ripete UNA volta con le foto scaricate da noi (base64), saltando quelle che non si scaricano;
- gli altri errori 400 non si ripetono;
- se nemmeno noi scarichiamo una foto: come "senza foto" (FotoNonScaricabili per le auto che vogliono le foto viste,
  la chiamata senza foto di sempre per le altre);
- la spesa passa da `paga` una volta sola (la chiamata rifiutata non si paga).
Nessuna regola di prezzo, soglia o filtro cambia.
"""
from __future__ import annotations

import logging
import os

from dealfinder.ai import client as AI
from dealfinder.ai import controllo_auto as CA

OK = {"guasto": 0, "prezzo_rata": 0, "km_credibili": 1, "danni_foto": .05, "foto_internet": .05,
      "targa_visibile": .5, "dati_coerenti": 1, "truffa": 0.1, "auto_vera": 0.9, "stato": 2,
      "decisione": "tratta", "confidenza": .6, "note": ""}
MSG = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': "
       "'Unable to download the file. Please verify the URL and try again.'}}")
FOTO = ["https://x/1.jpg", "https://x/2.jpg", "https://x/3.jpg"]


class Errore400(Exception):
    """Come anthropic.BadRequestError: status_code e corpo strutturato."""
    status_code = 400

    def __init__(self, testo, messaggio):
        super().__init__(testo)
        self.body = {"type": "error", "error": {"type": "invalid_request_error", "message": messaggio}}


def scarico():
    return Errore400(MSG, "Unable to download the file. Please verify the URL and try again.")


class env:
    def __init__(self, **kv):
        self.kv, self.prima = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.prima[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.prima.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Registro(logging.Handler):
    def __init__(self):
        super().__init__()
        self.righe = []

    def emit(self, record):
        self.righe.append(record.getMessage())


def _prova(modo, scaricabili, chiama, serve_foto, foto=FOTO, id_=77):
    """controlla() con le foto finte: per indirizzo vanno tutte, da noi solo quelle in `scaricabili`."""
    prima = (AI.image_block, AI.image_url_block)
    AI.image_url_block = lambda src: {"type": "image", "source": {"type": "url", "url": src}}
    AI.image_block = lambda src, http=None, side=None: (
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": src}}
        if src in scaricabili else None)
    reg, pagate = Registro(), []
    CA.log.addHandler(reg)
    try:
        with env(CONTROLLO_FOTO_MODO=modo, CONTROLLO_FOTO="3"):
            r = CA.controlla({"id": id_}, list(foto), chiama=chiama, serve_foto=serve_foto,
                             paga=lambda tin, tout, modello: pagate.append((tin, tout)))
    finally:
        CA.log.removeHandler(reg)
        AI.image_block, AI.image_url_block = prima
    return r, pagate, [x for x in reg.righe if "ripiego_base64" in x]


def _chiama_che_cade_sugli_indirizzi(chiamate, errore=None):
    def chiama(testo, foto):
        tipi = [b["source"]["type"] for b in foto]
        chiamate.append(tipi)
        if "url" in tipi:
            raise errore or scarico()
        return dict(OK), 900, 60, "claude-haiku-5-5"
    return chiama


def test_errore_scarico_foto_riconosciuto():
    assert CA.errore_scarico_foto(scarico())
    assert CA.errore_scarico_foto(Exception(MSG))                       # solo il testo (nessun corpo, nessuno stato)
    assert CA.errore_scarico_foto(Errore400("Error code: 400", "Unable to download the file."))   # solo il corpo
    assert not CA.errore_scarico_foto(Errore400("Error code: 400 - prompt is too long", "prompt is too long"))
    e = scarico()
    e.status_code = 500                                                 # stesso testo ma non è un 400
    assert not CA.errore_scarico_foto(e)
    assert not CA.errore_scarico_foto(Exception("timeout"))


def test_ripiego_base64_con_le_sole_foto_scaricabili():
    for serve in (True, False):
        chiamate = []
        r, pagate, righe = _prova("url", {FOTO[0], FOTO[2]}, _chiama_che_cade_sugli_indirizzi(chiamate), serve)
        # prima gli indirizzi (3 foto, 400), poi UNA sola ripetizione in base64 con le 2 che si scaricano
        assert chiamate == [["url"] * 3, ["base64"] * 2]
        assert r["errore"] is None and r["x"]["decisione"] == "tratta" and r["x"]["_foto"] == 2 and r["foto"] == 2
        assert pagate == [(900, 60)] and len(r["token"]) == 1           # una sola spesa: la chiamata rifiutata no
        assert righe == ["CONTROLLO_AI 77 ripiego_base64 foto_ok=2 foto_saltate=1"]


def test_altri_errori_400_non_si_ripetono():
    for serve in (True, False):
        chiamate = []
        altro = Errore400("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
                          "'message': 'prompt is too long: 250000 tokens > 200000 maximum'}}",
                          "prompt is too long: 250000 tokens > 200000 maximum")
        r, pagate, righe = _prova("url", set(FOTO), _chiama_che_cade_sugli_indirizzi(chiamate, altro), serve)
        assert chiamate == [["url"] * 3]                                # nessuna ripetizione
        assert r["x"] is None and r["errore"] is altro and not isinstance(r["errore"], CA.ControlloNonValido)
        assert pagate == [] and righe == []


def test_nessuna_foto_scaricabile_come_senza_foto():
    # auto che vuole le foto viste (sconto alto): nessuna chiamata in più, FotoNonScaricabili come senza foto
    chiamate = []
    r, pagate, righe = _prova("url", set(), _chiama_che_cade_sugli_indirizzi(chiamate), True)
    assert chiamate == [["url"] * 3]
    assert r["x"] is None and isinstance(r["errore"], CA.FotoNonScaricabili) and r["foto"] == 0
    assert pagate == [] and r["token"] == []
    assert righe == ["CONTROLLO_AI 77 ripiego_base64 foto_ok=0 foto_saltate=3"]
    # stesso esito di chi non ha foto per niente
    senza, _, _ = _prova("url", set(), _chiama_che_cade_sugli_indirizzi([]), True, foto=[])
    assert type(senza["errore"]) is type(r["errore"]) and senza["x"] is None

    # modo predefinito (base64 prima, indirizzi dopo): da noi non si scarica niente, Anthropic nemmeno
    chiamate = []
    r, pagate, righe = _prova("base64", set(), _chiama_che_cade_sugli_indirizzi(chiamate), True)
    assert chiamate == [["url"] * 3] and isinstance(r["errore"], CA.FotoNonScaricabili) and pagate == []
    assert righe == ["CONTROLLO_AI 77 ripiego_base64 foto_ok=0 foto_saltate=3"]

    # auto che non vuole le foto viste: come oggi, la chiamata senza foto (pagata una volta, _foto = 0)
    chiamate = []
    r, pagate, righe = _prova("url", set(), _chiama_che_cade_sugli_indirizzi(chiamate), False)
    assert chiamate == [["url"] * 3, []]
    assert r["errore"] is None and r["x"]["_foto"] == 0 and pagate == [(900, 60)]


def test_foto_rifiutata_per_altro_motivo_resta_un_errore():
    # una foto rifiutata per un altro motivo (non lo scarico) con serve_foto: l'errore resta quello, come prima
    chiamate = []
    altro = Errore400("Error code: 400 - image exceeds 5 MB maximum", "image exceeds 5 MB maximum")
    r, pagate, righe = _prova("url", set(), _chiama_che_cade_sugli_indirizzi(chiamate, altro), True)
    assert r["errore"] is altro and pagate == [] and righe == []
