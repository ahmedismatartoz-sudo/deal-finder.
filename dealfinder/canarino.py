"""Canarino (16/11, fondamenta): UN controllo "da fuori" che il percorso annuncio → sito funzioni davvero.

    python -m dealfinder.canarino          esito su stdout e nel log, codice di uscita 0 = ok, 1 = ko

La sentinella guarda regole e numeri; il canarino risponde a una domanda sola: "in questo momento un'auto nuova
arriva sul sito, con il prezzo giusto e la foto che si apre?". Cinque prove, tutte in lettura:

  1 worker vivo        battito della riga 'worker_nuovi' di tempo_reale più giovane di BATTITO_MAX_MIN minuti
  2 giro 'nuovi'       ultimo giro 'nuovi' di tempo_reale finito da meno di GIRO_NUOVI_MAX_MIN minuti
  3 auto sul sito      almeno MIN_AUTO_SITO auto nell'elenco del sito: API pubblica /api/affari (se risponde),
                       altrimenti la copia salvata dell'elenco (sito_cache 'elenco', web/elenco_salvato.py)
  4 annuncio recente   l'auto pubblicata più di recente (con foto) è nell'elenco, ancora 'attivo' nel database e con
                       lo STESSO prezzo dell'annuncio (listings.price_eur)
  5 foto               la sua prima foto risponde 200 con un'immagine

Esito: una riga di log `CANARINO ok {json}` oppure `CANARINO ko: <motivi> {json}`, e una riga nella tabella
canarino_esiti (creata qui se manca; si svuota da sola dopo 30 giorni). Nessun dato personale: solo id, numeri,
minuti. Nessuna scrittura fuori da canarino_esiti. Nessuna funzione solleva eccezioni verso chi la chiama.

Le soglie qui sotto sono di SORVEGLIANZA (quando dare l'allarme), non regole di business: non decidono quali auto
vanno sul sito.

Richiamo ogni ora: dentro il thread della sentinella del sito (sentinella._ciclo_web, dopo esegui("web")), vedi
REGOLE_DEPLOY.md. Nessun servizio nuovo.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

log = logging.getLogger("canarino")

BATTITO_MAX_MIN = 5            # il worker scrive il battito ogni 60 s (pipeline/tempo_reale_worker.py)
GIRO_NUOVI_MAX_MIN = 15        # giro veloce ogni 90 s; 15 minuti = una decina di giri saltati
MIN_AUTO_SITO = 100            # il sito ha di solito 400-600 auto; sotto 100 qualcosa si è rotto
ELENCO_MAX_ORE = 1.0           # copia salvata dell'elenco: il sito la riscrive almeno ogni 10 minuti se cambia
TIMEOUT_S = 30                 # statement_timeout e timeout HTTP
SITO = "https://scovo.onrender.com"

TABELLA_SQL = """CREATE TABLE IF NOT EXISTS canarino_esiti (
    id BIGSERIAL PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now(), ok BOOLEAN NOT NULL,
    motivi JSONB NOT NULL DEFAULT '[]', numeri JSONB NOT NULL DEFAULT '{}', processo TEXT)"""


def _base_url() -> str:
    return (os.environ.get("CANARINO_URL") or os.environ.get("PUBLIC_URL") or SITO).rstrip("/")


def _http_get(url: str, headers: dict | None = None):
    import httpx
    return httpx.get(url, headers=headers or {}, timeout=TIMEOUT_S, follow_redirects=True)


def _minuti(conn, giro: str) -> float | None:
    r = conn.execute("/*C:tr*/ SELECT extract(epoch FROM now() - ultimo_at) / 60.0 AS minuti FROM tempo_reale "
                     "WHERE giro = %s", (giro,)).fetchone()
    m = (r or {}).get("minuti")
    return round(float(m), 1) if m is not None else None


def _elenco_salvato(conn) -> tuple[list | None, float | None]:
    """(auto, età in ore) dalla copia salvata dell'elenco del sito; (None, None) se non c'è."""
    from .web import elenco_salvato
    r = conn.execute("/*C:elenco*/ SELECT dati, extract(epoch FROM now() - salvato_at) AS eta FROM sito_cache "
                     "WHERE chiave = 'elenco'").fetchone()
    if not r:
        return None, None
    d = elenco_salvato.espandi(r["dati"])
    items = list((d or {}).get("items") or []) if isinstance(d, dict) else []
    return items, round(float(r["eta"] or 0) / 3600, 2)


def _elenco_api(http_get, base: str) -> tuple[list | None, str]:
    """Auto dall'API del sito (/api/affari): da ospite se il sito è aperto, altrimenti con CANARINO_TOKEN (token di
    un account di sola lettura, facoltativo). (None, motivo) se non risponde."""
    prove = [{}]
    tok = os.environ.get("CANARINO_TOKEN")
    if tok:
        prove.append({"authorization": f"Bearer {tok}"})
    stato = "?"
    for h in prove:
        try:
            r = http_get(f"{base}/api/affari", h)
        except Exception as e:
            return None, f"API non raggiungibile ({type(e).__name__})"
        stato = r.status_code
        if r.status_code == 200:
            try:
                return list(r.json().get("items") or []), "api"
            except Exception:
                return None, "API: risposta non leggibile"
    return None, f"API {stato}"


def _quando(it: dict) -> str:
    return str(it.get("nuova") or it.get("visto_dal") or "")


def controlla(conn, items: list | None = None, http_get=None, base_url: str | None = None,
              min_auto: int | None = None) -> dict:
    """Le cinque prove. `items` (facoltativo): l'elenco già in memoria (dentro il sito si passa app._elenco['items']
    e non si chiama l'API). Ritorna {"ok", "motivi", "numeri"}."""
    http_get = http_get or _http_get
    base = (base_url or _base_url()).rstrip("/")
    min_auto = MIN_AUTO_SITO if min_auto is None else min_auto
    motivi, num = [], {}
    conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)

    # 1-2) tempo reale
    num["battito_min"] = _minuti(conn, "worker_nuovi")
    num["giro_nuovi_min"] = _minuti(conn, "nuovi")
    if num["battito_min"] is None or num["battito_min"] > BATTITO_MAX_MIN:
        motivi.append(f"worker fermo: battito {num['battito_min']} min (massimo {BATTITO_MAX_MIN})")
    if num["giro_nuovi_min"] is None or num["giro_nuovi_min"] > GIRO_NUOVI_MAX_MIN:
        motivi.append(f"giro 'nuovi' vecchio: {num['giro_nuovi_min']} min (massimo {GIRO_NUOVI_MAX_MIN})")

    # 3) auto sul sito
    fonte = "memoria"
    if items is None:
        items, fonte = _elenco_api(http_get, base)
        if items is None:
            num["api"] = fonte
            items, eta = _elenco_salvato(conn)
            fonte = "copia_salvata"
            num["elenco_ore"] = eta
            if eta is not None and eta > ELENCO_MAX_ORE:
                motivi.append(f"copia salvata dell'elenco vecchia: {eta} ore (massimo {ELENCO_MAX_ORE})")
    items = [it for it in (items or []) if isinstance(it, dict) and it.get("id") is not None]
    num.update(fonte_elenco=fonte, auto_sito=len(items))
    if len(items) < min_auto:
        motivi.append(f"poche auto sul sito: {len(items)} (minimo {min_auto})")

    # 4) annuncio pubblicato di recente: presente, attivo, stesso prezzo
    con_foto = sorted((it for it in items if it.get("foto") and not it.get("supercar")), key=_quando, reverse=True)
    if not con_foto:
        if items:
            motivi.append("nessuna auto con foto nell'elenco")
        return {"ok": not motivi, "motivi": motivi, "numeri": num}
    it = con_foto[0]
    num.update(recente_id=it["id"], recente_dal=_quando(it) or None)
    prezzo_sito = int(it.get("prezzo_annuncio") or it.get("prezzo") or 0)
    r = conn.execute("/*C:annuncio*/ SELECT price_eur, status FROM listings WHERE id = %s", (int(it["id"]),)).fetchone()
    if not r:
        motivi.append(f"auto {it['id']} sul sito ma non nel database")
    else:
        num.update(prezzo_sito=prezzo_sito, prezzo_annuncio=r["price_eur"], stato=r["status"])
        if r["status"] != "attivo":
            motivi.append(f"auto {it['id']} sul sito ma l'annuncio è '{r['status']}'")
        if r["price_eur"] is None or int(r["price_eur"]) != prezzo_sito:
            motivi.append(f"auto {it['id']}: prezzo sul sito {prezzo_sito} diverso dall'annuncio {r['price_eur']}")

    # 5) foto
    try:
        f = http_get(base + str(it["foto"]), {})
        num["foto_http"] = f.status_code
        tipo = str((getattr(f, "headers", {}) or {}).get("content-type") or "")
        if f.status_code != 200:
            motivi.append(f"foto dell'auto {it['id']}: risposta {f.status_code}")
        elif tipo and not tipo.startswith("image/"):
            motivi.append(f"foto dell'auto {it['id']}: non è un'immagine ({tipo[:40]})")
    except Exception as e:
        num["foto_http"] = None
        motivi.append(f"foto dell'auto {it['id']}: non raggiungibile ({type(e).__name__})")
    return {"ok": not motivi, "motivi": motivi, "numeri": num}


def registra(conn, esito: dict, processo: str) -> None:
    conn.execute(TABELLA_SQL)
    conn.execute("/*C:salva*/ INSERT INTO canarino_esiti (ok, motivi, numeri, processo) VALUES (%s, %s, %s, %s)",
                 (bool(esito.get("ok")), json.dumps(esito.get("motivi") or [], ensure_ascii=False),
                  json.dumps(esito.get("numeri") or {}, ensure_ascii=False, default=str), processo[:60]))
    conn.execute("/*C:pulizia*/ DELETE FROM canarino_esiti WHERE at < now() - interval '30 days'")
    conn.commit()


def riga_log(esito: dict) -> str:
    dati = json.dumps(esito.get("numeri") or {}, ensure_ascii=False, default=str, sort_keys=True)
    if esito.get("ok"):
        return f"CANARINO ok {dati}"
    return "CANARINO ko: " + "; ".join(esito.get("motivi") or ["errore"]) + " " + dati


def esegui(processo: str = "manuale", connetti=None, items: list | None = None, http_get=None,
           salva: bool = True) -> dict:
    """Un giro: prove, riga di log, riga in canarino_esiti. Non solleva mai (un errore è un esito ko)."""
    if os.environ.get("CANARINO", "1") == "0":
        return {"ok": True, "spento": True, "motivi": [], "numeri": {}}
    t0 = time.monotonic()
    try:
        if connetti is None:
            from .db import connect as connetti
        with connetti() as conn:
            esito = controlla(conn, items=items, http_get=http_get)
            try:
                conn.rollback()                   # chiude la transazione di sola lettura
            except Exception:
                pass
            if salva:
                try:
                    registra(conn, esito, processo)
                except Exception as e:
                    esito["numeri"]["registro"] = type(e).__name__
    except Exception as e:
        esito = {"ok": False, "motivi": [f"errore: {type(e).__name__}: {str(e).splitlines()[0][:160] if str(e) else ''}"],
                 "numeri": {}}
    esito["numeri"]["secondi"] = round(time.monotonic() - t0, 1)
    try:
        log.warning("%s", riga_log(esito))
    except Exception:
        pass
    return esito


def da_sito() -> dict:
    """Richiamo dal thread della sentinella del sito: usa l'elenco in memoria (app._elenco), niente chiamata all'API."""
    m = sys.modules.get("dealfinder.web.app")
    el = getattr(m, "_elenco", None) if m else None
    items = list(el.get("items") or []) if isinstance(el, dict) and el.get("items") else None
    return esegui("web", items=items)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    e = esegui("manuale")
    print(riga_log(e))
    sys.exit(0 if e.get("ok") else 1)
