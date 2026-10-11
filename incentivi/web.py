"""Applicazione web del modulo Incentivi (Starlette). Si monta su /incentivi nel sito, ma è a sé.

Ogni impresa appartiene alla sessione del browser che l'ha creata (cookie httpOnly "inc_s").
Il commercialista accede solo con un link firmato che scade dopo 30 giorni; ogni accesso è registrato.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import job, motore, pdf, store, visura

log = logging.getLogger("incentivi.web")
STATIC = Path(__file__).resolve().parent / "static"
DEMO = {p["demo_id"]: p for p in json.loads((Path(__file__).resolve().parent / "data" / "profili_demo.json").read_text())}
COOKIE = "inc_s"
MAX_FILE = 8 * 1024 * 1024
GIORNI_LINK = 30
_pronto = False
_limiti: dict = defaultdict(list)


def _db():
    from dealfinder.db import connect
    return connect()


def _setup():
    global _pronto
    if not _pronto:
        with _db() as conn:
            store.ensure(conn)
        _pronto = True


def _limite(chiave: str, quante: int, secondi: int) -> bool:
    now = time.time()
    v = [t for t in _limiti[chiave] if t > now - secondi]
    _limiti[chiave] = v
    if len(v) >= quante:
        return False
    v.append(now)
    return True


class Errore(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg, self.code = msg, code


def _sessione(request: Request) -> str | None:
    return request.cookies.get(COOKIE)


def _impresa(conn, request: Request, iid: int) -> dict:
    s = _sessione(request)
    row = conn.execute("SELECT * FROM inc_imprese WHERE id=%s", (iid,)).fetchone()
    if not row or not s or row["sessione"] != s:
        raise Errore("Impresa non trovata", 404)
    return row


def api(fn):
    async def h(request: Request):
        try:
            await run_in_threadpool(_setup)
            out = await fn(request)
            return out if isinstance(out, Response) else JSONResponse(out)
        except Errore as e:
            return JSONResponse({"errore": e.msg}, status_code=e.code)
        except (visura.Errore, visura.NonConfigurato) as e:
            return JSONResponse({"errore": str(e)}, status_code=502)
        except Exception:
            log.exception("errore incentivi")
            return JSONResponse({"errore": "Qualcosa è andato storto, riprova tra poco"}, status_code=500)
    return h


async def _json(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


# ------------------------------------------------------------------ pagine
async def index(request: Request):
    return FileResponse(STATIC / "index.html", headers={"cache-control": "no-cache"})


@api
async def stato(request: Request):
    def work():
        with _db() as conn:
            if not job.ultimo_oggi(conn):
                job.esegui(conn)
    await run_in_threadpool(work)
    cat = motore.catalogo()
    return {"visura": {"configurata": visura.configurato(), "ambiente": visura.ambiente()},
            "catalogo": {"versione": cat["versione"], "aggiornato": cat["aggiornato"], "misure": len(cat["misure"])},
            "demo": [{"id": k, "etichetta": v["etichetta"], "descrizione": v["descrizione"]} for k, v in DEMO.items()]}


def _salva_impresa(conn, sessione, piva, profilo, fonte) -> int:
    row = conn.execute("INSERT INTO inc_imprese (sessione, piva, profilo, fonte) VALUES (%s,%s,%s,%s) RETURNING id",
                       (sessione, piva, store.J(profilo), store.J(fonte))).fetchone()
    return row["id"]


def _vista_impresa(conn, row) -> dict:
    docs = conn.execute("SELECT id, tipo, misura_id, doc_id, nome, sha256, fonte, creato FROM inc_documenti "
                        "WHERE impresa_id=%s ORDER BY id", (row["id"],)).fetchall()
    rich = conn.execute("SELECT * FROM inc_visure_richieste WHERE impresa_id=%s ORDER BY id DESC LIMIT 1",
                        (row["id"],)).fetchone()
    vis = next((d for d in docs if d["tipo"] == "visura"), None)
    return {"id": row["id"], "profilo": row["profilo"], "fonte": row["fonte"],
            "visura": {"documento": ({"id": vis["id"], "nome": vis["nome"], "fonte": vis["fonte"],
                                      "data": vis["creato"].isoformat(), "sha256": vis["sha256"]} if vis else None),
                       "richiesta": ({"stato": rich["stato"], "aggiornato": rich["aggiornato"].isoformat()} if rich else None)},
            "documenti": [{"id": d["id"], "tipo": d["tipo"], "misura_id": d["misura_id"], "doc_id": d["doc_id"],
                           "nome": d["nome"], "data": d["creato"].isoformat()} for d in docs if d["tipo"] != "visura"]}


def _aggiorna_visura(conn, row) -> None:
    """Se c'è una richiesta di visura in corso, controlla lo stato e scarica il PDF quando è pronto."""
    rich = conn.execute("SELECT * FROM inc_visure_richieste WHERE impresa_id=%s ORDER BY id DESC LIMIT 1",
                        (row["id"],)).fetchone()
    if not rich or rich["stato"].lower() in visura.STATI_PRONTI + ("errore", "scaricata"):
        return
    try:
        st = visura.stato_visura(rich["tipo"], rich["richiesta_id"])
        if st.lower() in visura.STATI_PRONTI:
            nome, data = visura.scarica_visura(rich["tipo"], rich["richiesta_id"])
            fonte = (f"Openapi Visure Camerali ({visura.ambiente()}), {rich['tipo']}, richiesta {rich['richiesta_id']}, "
                     f"acquisita il {dt.date.today().strftime('%d/%m/%Y')}")
            conn.execute("INSERT INTO inc_documenti (impresa_id, tipo, nome, mime, dati, sha256, fonte) "
                         "VALUES (%s,'visura',%s,'application/pdf',%s,%s,%s)",
                         (row["id"], nome, store.cifra(data), store.sha256(data), fonte))
            st = "scaricata"
        conn.execute("UPDATE inc_visure_richieste SET stato=%s, aggiornato=now() WHERE id=%s", (st, rich["id"]))
        conn.commit()
    except visura.Errore as e:
        conn.execute("UPDATE inc_visure_richieste SET stato='errore', aggiornato=now() WHERE id=%s", (rich["id"],))
        conn.commit()
        log.warning("visura %s: %s", rich["richiesta_id"], e)


@api
async def crea_impresa(request: Request):
    body = await _json(request)
    sessione = _sessione(request) or store.nuovo_token()
    ip = request.client.host if request.client else "?"

    def work():
        with _db() as conn:
            if body.get("demo"):
                d = DEMO.get(body["demo"])
                if not d:
                    raise Errore("Profilo di esempio non trovato")
                prof = {k: v for k, v in d.items() if k not in ("demo_id", "etichetta", "descrizione")}
                prof["campi"] = {k: {"stato": "verificato", "fonte": "profilo di ESEMPIO (dati inventati per la prova)"}
                                 for k in ("ragione_sociale", "forma_giuridica", "ateco", "sede", "unita_locali",
                                           "data_costituzione", "stato", "addetti")}
                for k in ("soci", "amministratori"):
                    prof["campi"][k] = {"stato": "mancante", "fonte": None}
                fonte = {"tipo": "esempio", "fornitore": "profilo di esempio", "data": dt.date.today().isoformat()}
                iid = _salva_impresa(conn, sessione, prof["piva"], prof, fonte)
                conn.commit()
                return _vista_impresa(conn, conn.execute("SELECT * FROM inc_imprese WHERE id=%s", (iid,)).fetchone())
            piva = "".join(ch for ch in str(body.get("piva") or "") if ch.isdigit())
            if not visura.piva_valida(piva):
                raise Errore("La partita IVA non è valida: controlla le 11 cifre")
            if not visura.configurato():
                raise Errore("Il collegamento alla visura camerale non è ancora attivo. Prova con un profilo di esempio.", 503)
            if not _limite("visura:" + ip, 6, 3600):
                raise Errore("Troppe richieste di visura: riprova tra un'ora", 429)
            prof = visura.leggi_impresa(piva)
            fonte = {"tipo": "openapi", "fornitore": "Openapi", "ambiente": visura.ambiente(),
                     "data": dt.date.today().isoformat(),
                     "nota": "Dati strutturati dal servizio Company; PDF originale dal servizio Visure Camerali"}
            iid = _salva_impresa(conn, sessione, piva, prof, fonte)
            try:
                r = visura.richiedi_visura(piva)
                conn.execute("INSERT INTO inc_visure_richieste (impresa_id, tipo, richiesta_id, stato) VALUES (%s,%s,%s,%s)",
                             (iid, r["tipo"], r["richiesta_id"], r["stato"]))
                conn.commit()
                row = conn.execute("SELECT * FROM inc_imprese WHERE id=%s", (iid,)).fetchone()
                visura.attendi_visura(r["tipo"], r["richiesta_id"], secondi=25)
                _aggiorna_visura(conn, row)
            except visura.Errore as e:
                store.log(conn, "errore", f"Visura PDF non ottenuta per l'impresa {iid}: {e}")
            conn.commit()
            return _vista_impresa(conn, conn.execute("SELECT * FROM inc_imprese WHERE id=%s", (iid,)).fetchone())

    out = await run_in_threadpool(work)
    resp = JSONResponse(out)
    resp.set_cookie(COOKIE, sessione, max_age=180 * 86400, httponly=True, samesite="lax",
                    secure=bool(os.environ.get("RENDER")), path="/incentivi")
    return resp


@api
async def leggi_impresa(request: Request):
    iid = int(request.path_params["id"])

    def work():
        with _db() as conn:
            row = _impresa(conn, request, iid)
            _aggiorna_visura(conn, row)
            out = _vista_impresa(conn, row)
            out["domande"] = motore.domande_utili(row["profilo"])
            v = conn.execute("SELECT risposte FROM inc_valutazioni WHERE impresa_id=%s ORDER BY id DESC LIMIT 1",
                             (iid,)).fetchone()
            out["risposte"] = v["risposte"] if v else {}
            return out
    return await run_in_threadpool(work)


def _checklist(conn, iid) -> dict:
    ck: dict = defaultdict(dict)
    for r in conn.execute("SELECT misura_id, doc_id, pronto FROM inc_checklist WHERE impresa_id=%s", (iid,)).fetchall():
        ck[r["misura_id"]][r["doc_id"]] = r["pronto"]
    # la visura recuperata in automatico conta come pronta
    if conn.execute("SELECT 1 FROM inc_documenti WHERE impresa_id=%s AND tipo='visura'", (iid,)).fetchone():
        for m in motore.catalogo()["misure"]:
            if "visura" in (m.get("documenti") or []):
                ck[m["id"]].setdefault("visura", True)
    return ck


def _arricchisci(res: dict, ck: dict) -> dict:
    for r in res["risultati"]:
        docs = r.get("documenti") or []
        c = ck.get(r["id"], {})
        r["documenti_pronti"] = sum(1 for d in docs if c.get(d["id"]))
        r["checklist"] = {d["id"]: bool(c.get(d["id"])) for d in docs}
        # prossima azione: il primo documento non pronto con un'azione
        nxt = next((d for d in docs if not c.get(d["id"]) and d.get("azione")), None)
        if nxt:
            r["azione"] = nxt["azione"]
    return res


@api
async def valuta(request: Request):
    iid = int(request.path_params["id"])
    body = await _json(request)
    ris = {}
    for k in ("officina", "assume", "ordini"):
        if isinstance(body.get(k), bool):
            ris[k] = body[k]
    if body.get("investimento") not in (None, ""):
        try:
            ris["investimento"] = max(0, min(int(float(body["investimento"])), 10_000_000))
        except (TypeError, ValueError):
            raise Errore("Importo non valido")

    def work():
        with _db() as conn:
            row = _impresa(conn, request, iid)
            prof = row["profilo"]
            extra = body.get("extra")
            if isinstance(extra, dict):
                allowed = {"artigiana", "fondo_forte", "fondo_fondartigianato", "lavoratore_requisiti", "beni_40",
                           "stabilizzazione", "intervento_energetico", "veicolo_commerciale", "gpl"}
                prof.setdefault("extra", {}).update({k: bool(v) for k, v in extra.items() if k in allowed and v is not None})
                conn.execute("UPDATE inc_imprese SET profilo=%s, aggiornato=now() WHERE id=%s", (store.J(prof), iid))
            res = motore.valuta_tutto(prof, ris)
            conn.execute("INSERT INTO inc_valutazioni (impresa_id, risposte, risultato, versione_catalogo) "
                         "VALUES (%s,%s,%s,%s)", (iid, store.J(ris), store.J(res), res["versione_catalogo"]))
            conn.commit()
            res = _arricchisci(res, _checklist(conn, iid))
            res["risposte"] = ris
            res["impresa"] = {"ragione_sociale": prof.get("ragione_sociale"), "extra": prof.get("extra") or {}}
            return res
    return await run_in_threadpool(work)


@api
async def checklist(request: Request):
    iid = int(request.path_params["id"])
    b = await _json(request)
    mid, did, ok = str(b.get("misura_id") or ""), str(b.get("doc_id") or ""), bool(b.get("pronto"))
    if not mid or not did:
        raise Errore("Dati mancanti")

    def work():
        with _db() as conn:
            _impresa(conn, request, iid)
            conn.execute("INSERT INTO inc_checklist (impresa_id, misura_id, doc_id, pronto) VALUES (%s,%s,%s,%s) "
                         "ON CONFLICT (impresa_id, misura_id, doc_id) DO UPDATE SET pronto=EXCLUDED.pronto, aggiornato=now()",
                         (iid, mid, did, ok))
            conn.commit()
            return {"ok": True}
    return await run_in_threadpool(work)


TIPI_FILE = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}


@api
async def carica(request: Request):
    iid = int(request.path_params["id"])
    b = await _json(request)
    mime = str(b.get("mime") or "")
    if mime not in TIPI_FILE:
        raise Errore("Carica un PDF o una foto (JPG/PNG)")
    try:
        data = base64.b64decode(str(b.get("dati") or ""), validate=True)
    except Exception:
        raise Errore("File non leggibile")
    if not data or len(data) > MAX_FILE:
        raise Errore("Il file deve essere più piccolo di 8 MB")
    nome = (str(b.get("nome") or "documento")[:120]).replace("/", "_")

    def work():
        with _db() as conn:
            _impresa(conn, request, iid)
            row = conn.execute("INSERT INTO inc_documenti (impresa_id, tipo, misura_id, doc_id, nome, mime, dati, sha256, fonte) "
                               "VALUES (%s,'allegato',%s,%s,%s,%s,%s,%s,'caricato dal commerciante') RETURNING id",
                               (iid, b.get("misura_id"), b.get("doc_id"), nome, mime, store.cifra(data),
                                store.sha256(data))).fetchone()
            if b.get("misura_id") and b.get("doc_id"):
                conn.execute("INSERT INTO inc_checklist (impresa_id, misura_id, doc_id, pronto) VALUES (%s,%s,%s,true) "
                             "ON CONFLICT (impresa_id, misura_id, doc_id) DO UPDATE SET pronto=true, aggiornato=now()",
                             (iid, b["misura_id"], b["doc_id"]))
            conn.commit()
            return {"id": row["id"], "nome": nome}
    return await run_in_threadpool(work)


def _file_response(doc) -> Response:
    data = store.decifra(doc["dati"])
    return Response(data, media_type=doc["mime"], headers={
        "content-disposition": f'attachment; filename="{doc["nome"]}"', "cache-control": "no-store"})


@api
async def scarica_documento(request: Request):
    iid, did = int(request.path_params["id"]), int(request.path_params["doc"])

    def work():
        with _db() as conn:
            _impresa(conn, request, iid)
            doc = conn.execute("SELECT * FROM inc_documenti WHERE id=%s AND impresa_id=%s", (did, iid)).fetchone()
            if not doc:
                raise Errore("Documento non trovato", 404)
            return _file_response(doc)
    return await run_in_threadpool(work)


def _dati_pdf(conn, iid: int, misure: list[str], link: str | None, scade: str | None) -> bytes:
    row = conn.execute("SELECT * FROM inc_imprese WHERE id=%s", (iid,)).fetchone()
    v = conn.execute("SELECT * FROM inc_valutazioni WHERE impresa_id=%s ORDER BY id DESC LIMIT 1", (iid,)).fetchone()
    if not v:
        raise Errore("Prima completa le domande")
    ck = _checklist(conn, iid)
    ris = [r for r in v["risultato"]["risultati"] if r["id"] in misure]
    allegati = conn.execute("SELECT nome, tipo, sha256, fonte FROM inc_documenti WHERE impresa_id=%s ORDER BY id",
                            (iid,)).fetchall()
    imp = {"profilo": row["profilo"], "fonte": {**(row["fonte"] or {}), "generato": dt.date.today().isoformat(),
                                                 "versione_catalogo": v["versione_catalogo"]}}
    return pdf.genera(imp, ris, ck, allegati, link, scade)


@api
async def inoltra(request: Request):
    iid = int(request.path_params["id"])
    b = await _json(request)
    misure = [str(x) for x in (b.get("misure") or [])][:40]
    if not misure:
        raise Errore("Scegli almeno un'opportunità da inoltrare")
    base = str(request.base_url).rstrip("/")

    def work():
        with _db() as conn:
            row = _impresa(conn, request, iid)
            v = conn.execute("SELECT id FROM inc_valutazioni WHERE impresa_id=%s ORDER BY id DESC LIMIT 1", (iid,)).fetchone()
            if not v:
                raise Errore("Prima completa le domande")
            tok = store.nuovo_token(24)
            scade = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=GIORNI_LINK)
            conn.execute("INSERT INTO inc_condivisioni (token, impresa_id, valutazione_id, misure, scade) VALUES (%s,%s,%s,%s,%s)",
                         (tok, iid, v["id"], misure, scade))
            conn.commit()
            link = f"{base}/incentivi/c/{tok}"
            nome = row["profilo"].get("ragione_sociale") or "la mia impresa"
            oggetto = f"Incentivi da verificare per {nome}"
            testo = (f"Ciao,\nti inoltro le opportunità di incentivi trovate per {nome}, con requisiti, calcoli e documenti.\n\n"
                     f"Link sicuro (scade il {scade.strftime('%d/%m/%Y')}): {link}\n\n"
                     "Sono opportunità compatibili da verificare, non contributi già ottenuti. Grazie!")
            return {"link": link, "scade": scade.strftime("%d/%m/%Y"), "pdf": f"{link}/pdf",
                    "oggetto": oggetto, "testo": testo}
    return await run_in_threadpool(work)


def _condivisione(conn, tok: str, request: Request, cosa: str):
    c = conn.execute("SELECT * FROM inc_condivisioni WHERE token=%s", (tok,)).fetchone()
    if not c or c["scade"] < dt.datetime.now(dt.timezone.utc):
        raise Errore("Link non valido o scaduto", 404)
    conn.execute("INSERT INTO inc_accessi (token, cosa, ip_hash) VALUES (%s,%s,%s)",
                 (tok, cosa, store.ip_hash(request.client.host if request.client else None)))
    conn.commit()
    return c


async def condiviso(request: Request):
    tok = request.path_params["token"]

    def work():
        with _db() as conn:
            try:
                c = _condivisione(conn, tok, request, "pagina")
            except Errore:
                return HTMLResponse("<h1>Link non valido o scaduto</h1>", status_code=404)
            row = conn.execute("SELECT * FROM inc_imprese WHERE id=%s", (c["impresa_id"],)).fetchone()
            docs = conn.execute("SELECT id, tipo, nome, sha256, fonte, creato FROM inc_documenti WHERE impresa_id=%s ORDER BY id",
                                (c["impresa_id"],)).fetchall()
            v = conn.execute("SELECT * FROM inc_valutazioni WHERE id=%s", (c["valutazione_id"],)).fetchone()
            return _pagina_professionista(tok, row, c, docs, v)
    await run_in_threadpool(_setup)
    return await run_in_threadpool(work)


def _pagina_professionista(tok, row, c, docs, v) -> HTMLResponse:
    from html import escape as e
    p = row["profilo"]
    ris = [r for r in v["risultato"]["risultati"] if r["id"] in c["misure"]]
    items = "".join(
        f"<li><b>{e(r['nome'])}</b> — {e(r['tipo_nome'])} — {e(r['bando']['etichetta'])}<br>"
        f"<small>{e(r['motivo'])}. Fonte: <a href='{e(r['fonte_url'] or '')}' rel='noopener'>{e(r['fonte_url'] or '')}</a></small></li>"
        for r in ris)
    dl = "".join(f"<li><a href='/incentivi/c/{tok}/doc/{d['id']}'>{e(d['nome'])}</a> "
                 f"<small>({e(d['tipo'])}, SHA-256 {d['sha256'][:12]}…, {e(d['fonte'] or '')})</small></li>" for d in docs)
    html = f"""<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Incentivi — {e(p.get('ragione_sociale') or '')}</title>
<link rel="stylesheet" href="/incentivi/static/app.css">
<body class="pro"><main class="wrap"><h1>Riepilogo per il commercialista</h1>
<p class="muted">{e(p.get('ragione_sociale') or '')} — P.IVA {e(p.get('piva') or '')}. Link valido fino al {c['scade'].strftime('%d/%m/%Y')}. Ogni accesso è registrato.</p>
<p><a class="btn" href="/incentivi/c/{tok}/pdf">Scarica il PDF completo</a></p>
<h2>Opportunità scelte</h2><ul class="plain">{items}</ul>
<h2>Documenti</h2><ul class="plain">{dl or '<li>Nessun documento caricato</li>'}</ul>
<p class="muted">Opportunità compatibili da verificare: non sono contributi già concessi. Catalogo regole versione {e(v['versione_catalogo'])}.</p>
</main></body></html>"""
    return HTMLResponse(html, headers={"cache-control": "no-store", "x-robots-tag": "noindex"})


async def condiviso_pdf(request: Request):
    tok = request.path_params["token"]

    def work():
        with _db() as conn:
            c = _condivisione(conn, tok, request, "pdf")
            base = str(request.base_url).rstrip("/")
            data = _dati_pdf(conn, c["impresa_id"], c["misure"], f"{base}/incentivi/c/{tok}", c["scade"].strftime("%d/%m/%Y"))
            return Response(data, media_type="application/pdf", headers={
                "content-disposition": 'inline; filename="incentivi-riepilogo.pdf"', "cache-control": "no-store"})
    await run_in_threadpool(_setup)
    try:
        return await run_in_threadpool(work)
    except Errore as e:
        return JSONResponse({"errore": e.msg}, status_code=e.code)


async def condiviso_doc(request: Request):
    tok, did = request.path_params["token"], int(request.path_params["doc"])

    def work():
        with _db() as conn:
            c = _condivisione(conn, tok, request, f"documento {did}")
            doc = conn.execute("SELECT * FROM inc_documenti WHERE id=%s AND impresa_id=%s", (did, c["impresa_id"])).fetchone()
            if not doc:
                raise Errore("Documento non trovato", 404)
            return _file_response(doc)
    await run_in_threadpool(_setup)
    try:
        return await run_in_threadpool(work)
    except Errore as e:
        return JSONResponse({"errore": e.msg}, status_code=e.code)


@api
async def pdf_mio(request: Request):
    iid = int(request.path_params["id"])
    misure = [m for m in (request.query_params.get("misure") or "").split(",") if m]

    def work():
        with _db() as conn:
            _impresa(conn, request, iid)
            v = conn.execute("SELECT risultato FROM inc_valutazioni WHERE impresa_id=%s ORDER BY id DESC LIMIT 1", (iid,)).fetchone()
            ids = misure or [r["id"] for r in (v["risultato"]["risultati"] if v else []) if r["etichetta"] != "non_fa_per_te"]
            data = _dati_pdf(conn, iid, ids, None, None)
            return Response(data, media_type="application/pdf",
                            headers={"content-disposition": 'inline; filename="incentivi-riepilogo.pdf"'})
    return await run_in_threadpool(work)


@api
async def lampo(request: Request):
    oggi = dt.date.today()
    out = []
    for b in motore.catalogo()["lampo"]:
        ap, ch = motore._d(b.get("apertura")), motore._d(b.get("chiusura_effettiva"))
        giorni = (ch - ap).days if ap and ch else b.get("giorni_aperto")
        out.append({**b, "giorni_aperto": giorni,
                    "lampo": giorni is not None and giorni <= 31 and "esaur" in (b.get("motivo_chiusura") or "").lower()})
    out.sort(key=lambda b: (b.get("giorni_aperto") if b.get("giorni_aperto") is not None else 999))
    return {"oggi": oggi.isoformat(), "bandi": out}


@api
async def aggiornamenti(request: Request):
    iid = request.query_params.get("impresa")

    def work():
        with _db() as conn:
            righe = conn.execute("SELECT quando, livello, messaggio, dettagli FROM inc_job_log ORDER BY id DESC LIMIT 80").fetchall()
            avvisi = []
            if iid:
                _impresa(conn, request, int(iid))
                avvisi = conn.execute("SELECT misura_id, tipo, canale, titolo, testo, urgente, creato FROM inc_avvisi "
                                      "WHERE impresa_id=%s ORDER BY id DESC LIMIT 30", (int(iid),)).fetchall()
            return {"log": [{**r, "quando": r["quando"].isoformat()} for r in righe],
                    "avvisi": [{**a, "creato": a["creato"].isoformat()} for a in avvisi]}
    return await run_in_threadpool(work)


@api
async def esegui_job(request: Request):
    if not _limite("job", 4, 600):
        raise Errore("Aggiornamento già eseguito da poco", 429)

    def work():
        with _db() as conn:
            return job.esegui(conn)
    return await run_in_threadpool(work)


routes = [
    Route("/", index),
    Route("/api/stato", stato),
    Route("/api/impresa", crea_impresa, methods=["POST"]),
    Route("/api/impresa/{id:int}", leggi_impresa),
    Route("/api/impresa/{id:int}/valuta", valuta, methods=["POST"]),
    Route("/api/impresa/{id:int}/checklist", checklist, methods=["POST"]),
    Route("/api/impresa/{id:int}/documenti", carica, methods=["POST"]),
    Route("/api/impresa/{id:int}/documenti/{doc:int}", scarica_documento),
    Route("/api/impresa/{id:int}/inoltra", inoltra, methods=["POST"]),
    Route("/api/impresa/{id:int}/pdf", pdf_mio),
    Route("/api/lampo", lampo),
    Route("/api/aggiornamenti", aggiornamenti),
    Route("/api/aggiorna", esegui_job, methods=["POST"]),
    Route("/c/{token}", condiviso),
    Route("/c/{token}/pdf", condiviso_pdf),
    Route("/c/{token}/doc/{doc:int}", condiviso_doc),
    Mount("/static", StaticFiles(directory=STATIC), name="inc_static"),
]

app = Starlette(routes=routes)
