"""Pannello /gestione → Sito (12/11): ribassi del prezzo mostrato, auto in cima, auto tolte e rimesse, senza deploy.

Tutti gli endpoint sono solo per amministratori (current_user(request, admin=True), come gli altri /api/admin).
Ogni modifica si scrive nel database (web/impostazioni.py) e vale subito in questo processo; un eventuale altro
processo la vede entro SITO_IMPOSTAZIONI_CACHE_S secondi (45). L'elenco già calcolato si ri-filtra (auto tolte) o si
ri-prezza (ribassi, in cima) senza ricalcolo; solo "rimetti" chiede un ricalcolo, in sottofondo (il sito intanto
continua a mostrare l'elenco di prima).
"""
from __future__ import annotations

import logging
import os
import re
import time

from starlette.requests import Request
from starlette.routing import Route

from . import impostazioni as IMP

log = logging.getLogger("impostazioni")


def _app():
    from . import app as A          # import qui: app importa questo modulo
    return A


def _admin(request: Request) -> dict:
    return _app().current_user(request, admin=True)


def _err(status: int, msg: str):
    _app().err(status, msg)


def _descr(it: dict) -> str:
    return " ".join(str(x) for x in (it.get("nome") or it.get("marca"), it.get("anno"),
                                     f"{int(it['km']):,} km".replace(",", ".") if it.get("km") else None) if x)


def _breve(it: dict) -> dict:
    return {"id": it.get("id"), "auto": _descr(it), "prezzo": it.get("prezzo"), "zona": it.get("zona"),
            "fonte": it.get("fonte"), "foto": it.get("foto"), "marca": it.get("marca"), "modello": it.get("modello"),
            "anno": it.get("anno"), "km": it.get("km")}


def _voce_testo(e: dict) -> str:
    """Voce in cima (chiave "id" = annuncio) o riga di sito_scarti (chiave "listing_id" = annuncio)."""
    lid = e.get("listing_id") if "listing_id" in e else e.get("id")
    parti = [str(e.get("marca") or "").title(), str(e.get("modello") or ""), str(e.get("anno") or ""),
             f"{int(e['km']):,} km".replace(",", ".") if e.get("km") is not None else "",
             f"annuncio a {int(e['prezzo']):,} €".replace(",", ".") if e.get("prezzo") is not None else ""]
    testo = " ".join(p for p in parti if p)
    if lid:
        return f"{testo} (annuncio {lid})" if e.get("marca") else f"annuncio {lid}"
    return testo


def _elenco_items() -> list:
    return list(_app()._elenco.get("items") or [])


def stato(request: Request):
    """Tutto quello che mostra la sezione Sito del pannello."""
    _admin(request)
    A = _app()
    IMP.invalida()
    d = IMP.dati()
    items = _elenco_items()
    r = IMP.ribasso()
    if r is None:                       # niente nel database: quello che vale oggi dal codice
        from . import scovo
        fasce = scovo._ribasso_fasce()
        r_out = {"attivo": bool(fasce), "schema": ",".join(f"{a}:{b}" for a, b in fasce) or scovo.RIBASSO_SCHEMA,
                 "fino": os.environ.get("SITO_RIBASSO_FINO", scovo.RIBASSO_FINO) or None, "fonte": "codice",
                 "acceso_ora": bool(fasce)}
    else:
        r_out = dict(r, fonte="database", acceso_ora=IMP.ribasso_acceso(r))
    voci = IMP.in_cima()
    if voci is None:
        from . import scovo
        voci = scovo.IN_CIMA
    cima = []
    for e in voci:
        trovata = next((it for it in items if IMP.corrisponde(e, it)), None)
        cima.append(dict(e, descrizione=_voce_testo(e), sul_sito=_breve(trovata) if trovata else None))
    scarti, rimessi = [], []
    try:
        with A.connect() as conn:
            if conn.execute("SELECT to_regclass('sito_scarti') AS t").fetchone()["t"]:
                for x in conn.execute("SELECT id, listing_id, marca, modello, anno, km, prezzo, motivo, attivo, "
                                      "creato_at, rimesso_at FROM sito_scarti ORDER BY coalesce(rimesso_at, creato_at) DESC, id DESC").fetchall():
                    x = dict(x, descrizione=_voce_testo(dict(x)))
                    (scarti if x["attivo"] else rimessi).append(x)
    except Exception as e:
        log.warning("SITO pannello: auto tolte non lette: %s", str(e)[:160])
    el = A._elenco
    return A.jsonify({
        "ribasso": r_out, "in_cima": cima, "in_cima_fonte": "database" if IMP.in_cima() is not None else "codice",
        "scarti": scarti, "rimessi": rimessi[:30], "scarti_fonte": "database" if d.get("scarti") is not None else "file",
        "elenco": {"auto": len(items), "fonte": el.get("fonte"), "calcolo_in_corso": bool(el.get("busy")),
                   "aggiornato": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(el["t"])) if el.get("t") else None},
        "cache_s": IMP.CACHE_S})


def cerca(request: Request):
    """Auto ora sul sito che contengono tutte le parole cercate (nome, anno, zona, fonte o id)."""
    _admin(request)
    q = str(request.query_params.get("q") or "").strip().lower()
    parole = [p for p in re.split(r"\s+", q) if p][:6]
    if not parole:
        return _app().jsonify({"items": []})
    out = []
    for it in _elenco_items():
        testo = " ".join(str(it.get(k) or "") for k in ("id", "nome", "marca", "modello", "anno", "km", "zona", "fonte", "prezzo")).lower()
        if all(p in testo for p in parole):
            out.append(_breve(it))
            if len(out) >= 30:
                break
    return _app().jsonify({"items": out})


async def salva_ribasso(request: Request):
    user = _admin(request)
    A = _app()
    data = await A.body(request)
    try:
        v = IMP.valida_ribasso(data)
    except ValueError as e:
        _err(400, str(e) or "Fasce scritte male: usa da:euro, per esempio 2000:700,6000:1000")
    except Exception:
        _err(400, "Fasce scritte male: usa da:euro, per esempio 2000:700,6000:1000")
    with A.connect() as conn:
        IMP.scrivi(conn, "ribasso", v, str(user["id"]))
    log.warning("SITO_IMPOSTAZIONI ribasso da admin %s: %s", user["id"], v)
    return A.jsonify({"ok": True, "ribasso": v, "acceso_ora": IMP.ribasso_acceso(v)})


async def salva_in_cima(request: Request):
    """Sostituisce l'elenco delle auto in cima (nell'ordine dato)."""
    user = _admin(request)
    A = _app()
    data = await A.body(request)
    voci = data.get("voci")
    if not isinstance(voci, list) or len(voci) > 30:
        _err(400, "Elenco non valido (al massimo 30 auto in cima)")
    try:
        pulite = [IMP.valida_voce(v, mostra=True) for v in voci if isinstance(v, dict)]
    except ValueError as e:
        _err(400, str(e))
    items = _elenco_items()
    for v in pulite:                     # per id: il prezzo mostrato non può superare quello dell'annuncio
        it = next((x for x in items if IMP.corrisponde(v, x)), None)
        if it and v.get("mostra") and v["mostra"] > int(it.get("prezzo") or 0):
            _err(400, "Il prezzo mostrato non può essere più alto di quello dell'annuncio (%s €)" % it.get("prezzo"))
    with A.connect() as conn:
        IMP.scrivi(conn, "in_cima", pulite, str(user["id"]))
    log.warning("SITO_IMPOSTAZIONI in cima da admin %s: %d auto", user["id"], len(pulite))
    return A.jsonify({"ok": True, "voci": pulite})


async def togli(request: Request):
    """Toglie un'auto dal sito (per id annuncio o marca/modello/anno/km/prezzo): esce subito dall'elenco."""
    user = _admin(request)
    A = _app()
    data = await A.body(request)
    try:
        voce = IMP.valida_voce(data)
    except ValueError as e:
        _err(400, str(e))
    motivo = str(data.get("motivo") or "").strip()[:200] or "tolta dal cliente"
    it = next((x for x in _elenco_items() if x.get("id") == voce.get("id")), None) if voce.get("id") else None
    if it:                               # per il pannello: che auto era (la voce resta per id)
        voce = dict(voce, marca=str(it.get("marca") or "").lower() or None, modello=str(it.get("modello") or "").lower() or None,
                    anno=it.get("anno"), km=it.get("km"), prezzo=int(it.get("prezzo_annuncio") or it.get("prezzo") or 0) or None)
    with A.connect() as conn:
        if not conn.execute("SELECT to_regclass('sito_scarti') AS t").fetchone()["t"]:
            _err(503, "Tabella delle auto tolte non ancora pronta (migrazione 047): riprova tra qualche minuto")
        sid = IMP.aggiungi_scarto(conn, voce, motivo, str(user["id"]))
    A._filtra_impostazioni()
    log.warning("SITO_IMPOSTAZIONI tolta da admin %s: %s (%s)", user["id"], _voce_testo(voce), motivo)
    return A.jsonify({"ok": True, "id": sid, "auto_sul_sito": len(_elenco_items())})


def rimetti(request: Request):
    """La voce si spegne, l'annuncio torna "da verificare" e l'elenco si ricalcola in sottofondo."""
    user = _admin(request)
    A = _app()
    sid = int(request.path_params["id"])
    with A.connect() as conn:
        e = IMP.rimetti_scarto(conn, sid, str(user["id"]))
    if not e:
        _err(404, "Voce non trovata o già rimessa")
    A._elenco["t"] = 0.0
    A._aggiorna_elenco(attendi=False)      # l'auto era fuori dall'elenco calcolato: serve un ricalcolo
    log.warning("SITO_IMPOSTAZIONI rimessa da admin %s: %s", user["id"], _voce_testo(e))
    return A.jsonify({"ok": True, "ricalcolo": True})


ROTTE = [
    Route("/api/admin/sito", stato),
    Route("/api/admin/sito/cerca", cerca),
    Route("/api/admin/sito/ribasso", salva_ribasso, methods=["PUT"]),
    Route("/api/admin/sito/in-cima", salva_in_cima, methods=["PUT"]),
    Route("/api/admin/sito/scarti", togli, methods=["POST"]),
    Route("/api/admin/sito/scarti/{id:int}/rimetti", rimetti, methods=["POST"]),
]
