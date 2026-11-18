"""Confronti mirati (18/11 sera, cliente: "più auto senza toccare le regole", OK 18/11 21:01).

Problema misurato: tra le uscite del sito "valutata → dati_insufficienti" (2.139 auto) ci sono auto che restano senza
stima perché nel loro gruppo (marca, modello, alimentazione, zona) i commercianti uguali ci sono, ma l'annuncio non è
stato ricontrollato negli ultimi FRESCHI_GIORNI giorni (base D di valore_base: commercianti "freschi"). Il controllo
generale delle sparite (verify.scomparsi_con_prova) va dal più vecchio, senza sapere quali annunci servono.

Qui, per le auto valutate dati_insufficienti con motivo "pochi_confronti:rinfrescabile" (valore_base._pochi: con i
commercianti vecchi riaperti arriverebbero al minimo di N_MIN_SITO confronti), si riaprono SOLO quei commercianti, con
lo stesso controllo del rinfresco del sito (tempo_reale._controlla_annuncio, lenient=False: serve la prova):
  - vivo e stesso prezzo → last_checked_at adesso (torna "fresco": base D, come prevede il metodo);
  - sparito con prova → 'scomparso' come nella verifica (diventa una "venduta" della base V se era online da almeno
    MIN_GIORNI_ONLINE giorni, come tutte le altre);
  - prezzo cambiato → prezzo nuovo (come la raccolta, con la stessa guardia sulle letture implausibili) e visto
    adesso: il confronto vale col prezzo di oggi; errore o prezzo illeggibile → niente.
Poi si rivalutano le auto interessate con la strada di sempre (valutazione.valuta_e_scrivi, pubblica=True).

NIENTE cambia nel metodo: SOGLIE_PV, margine, N_MIN_SITO, anno/km dei confronti, freschezza (10 giorni), zona, taglio
della mediana, anti-truffa e regole del sito restano quelle. Cambia solo che i dati di quei confronti sono aggiornati.
La precisione misurata (base_vendute, errore mediano sulle vendute) non può peggiorare per costruzione: stesso
calcolo, dati veri più recenti.

Spesa: solo pagine pubbliche (AutoScout24 e Subito con lo stesso client del rinfresco: SCRAPER_PROXY, MAI il Web
Unlocker). Prima le auto che potrebbero davvero entrare: numero non già "no", nella zona/anno/km del sito, ordinate
per margine provvisorio/soglia (stima_priorita: SOLO per l'ordine, mai come valore); sotto META_SOGLIA della soglia
non si spende. Ritmo: al massimo PAGINE_GIRO pagine ogni GIRO_MIN minuti, PAGINE_GIORNO al giorno, pausa TR_PAUSA_S
(0,6 s) tra le pagine, stop dopo STOP_ERRORI blocchi di fila. Nei log solo conteggi (nessun id, link o numero).
Interruttore: CONFRONTI_MIRATI=0 (default acceso; nessuna variabile da impostare).
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

log = logging.getLogger("valutazione")

PAGINE_GIRO = 60
PAGINE_GIORNO = 800
GIRO_MIN = 30
AUTO_GIRO = 40                # auto rivalutate al massimo per giro
RIPROVA_ORE = 24              # la stessa auto non si riprende prima di 24 ore
META_SOGLIA = 0.5             # margine provvisorio sotto metà soglia: anche coi confronti resterebbe fuori
STOP_ERRORI = 8
FONTI = ("autoscout24", "subito")

_PROVATE: dict = {}           # id auto -> ultimo tentativo (memoria del processo)
_GIORNO: dict = {"g": None, "pagine": 0}

CANDIDATE_SQL = """
SELECT l.id, l.price_eur, l.telefono_ok, vp.dati->'rinfresco' AS rinfresco
FROM valutazioni_policy vp JOIN listings l ON l.id = vp.listing_id
WHERE vp.policy_version = %(pv)s AND vp.categoria = 'dati_insufficienti' AND vp.metodo = 'base_vendute'
  AND vp.computed_at > now() - interval '3 days'
  AND vp.dati->>'motivo_dati' = 'pochi_confronti:rinfrescabile'
  AND l.status = 'attivo' AND l.seller_type IS DISTINCT FROM 'commerciante'
  AND l.telefono_ok IS NOT FALSE
  AND l.province = ANY(%(prov)s) AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s
  AND l.last_seen_at > now() - interval '5 days'
LIMIT 3000"""

CONFRONTI_SQL = """
SELECT id, source, url, price_eur FROM listings
WHERE id = ANY(%s) AND status = 'attivo' AND seller_type = 'commerciante' AND source = ANY(%s)
  AND COALESCE(last_checked_at, last_seen_at) < now() - make_interval(days => %s)"""


def attivo() -> bool:
    return os.environ.get("CONFRONTI_MIRATI", "1") != "0"


def _oggi() -> str:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")
    except Exception:
        return time.strftime("%Y-%m-%d")


def _json(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            return None
    return x


def scegli(righe: list[dict], soglia_fn, adesso: float | None = None, provate: dict | None = None) -> list[dict]:
    """Auto da aiutare, in ordine (solo dati, testabile): numero già letto prima, poi margine provvisorio/soglia più
    alto. Fuori: senza id da riaprire, margine provvisorio sotto META_SOGLIA della soglia (resterebbero fuori comunque),
    già provate nelle ultime RIPROVA_ORE ore."""
    adesso = time.time() if adesso is None else adesso
    provate = _PROVATE if provate is None else provate
    out = []
    for r in righe:
        rf = _json(r.get("rinfresco")) or {}
        ids = [int(i) for i in (rf.get("ids") or []) if str(i).isdigit()]
        prezzo = int(r.get("price_eur") or 0)
        stima = rf.get("stima_priorita")
        if not ids or prezzo <= 0 or not stima:
            continue
        if adesso - provate.get(int(r["id"]), -1e18) < RIPROVA_ORE * 3600:
            continue
        sg = max(1, int(soglia_fn(prezzo)))
        rapporto = (float(stima) - prezzo) / sg
        if rapporto < META_SOGLIA:
            continue
        out.append(((0 if r.get("telefono_ok") is True else 1, -rapporto, int(r["id"])), dict(r, _ids=ids)))
    out.sort(key=lambda x: x[0])
    return [r for _k, r in out]


def giro(conn, controlla=None, rivaluta=None) -> Counter:
    """Un giro: sceglie le auto, riapre i loro commercianti vecchi, rivaluta le auto. Mai un'eccezione verso fuori."""
    from .. import regole as RG
    from ..web import scovo
    from . import base_vendute as BV
    from . import policy_v2 as P
    from . import valore_base as VB
    st: Counter = Counter()
    if not attivo() or not VB.attivo():
        st["spento"] = 1
        return st
    if _GIORNO["g"] != _oggi():
        _GIORNO.update(g=_oggi(), pagine=0)
    resto = min(PAGINE_GIRO, PAGINE_GIORNO - _GIORNO["pagine"])
    if resto <= 0:
        st["tetto_giorno"] = 1
        return st
    try:
        a = RG.regole().ammissione
        righe = [dict(r) for r in conn.execute(CANDIDATE_SQL, {"pv": P.POLICY_VERSION, "prov": VB.province_sito(),
                                                                 "anno": int(a.anno_min), "km": int(a.km_max)}).fetchall()]
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("CONFRONTI_MIRATI candidate non lette: %s", str(e)[:150])
        st["errore_lettura"] = 1
        return st
    st["candidate"] = len(righe)
    scelte = scegli(righe, scovo.soglia)
    st["scelte"] = len(scelte)
    # confronti da riaprire, auto per auto (prima le migliori), fino al tetto di pagine del giro
    auto, da_aprire = [], []
    for r in scelte:
        if len(auto) >= AUTO_GIRO or len(da_aprire) >= resto:
            break
        nuovi = [i for i in r["_ids"] if i not in da_aprire]
        auto.append(r)
        da_aprire.extend(nuovi[:max(0, resto - len(da_aprire))])
    if not da_aprire:
        return st
    try:
        conf = [dict(x) for x in conn.execute(CONFRONTI_SQL, (da_aprire, list(FONTI), BV.FRESCHI_GIORNI)).fetchall()]
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("CONFRONTI_MIRATI confronti non letti: %s", str(e)[:150])
        st["errore_lettura"] = 1
        return st
    st["confronti_da_aprire"] = len(conf)
    controlla = controlla or _controlla
    esiti = controlla(conn, conf, st)
    _GIORNO["pagine"] += esiti.get("aperte", 0)
    adesso = time.time()
    for r in auto:
        _PROVATE[int(r["id"])] = adesso
    if len(_PROVATE) > 20000:
        for k in sorted(_PROVATE, key=_PROVATE.get)[:10000]:
            _PROVATE.pop(k, None)
    cambiati = set(esiti.get("cambiati") or ())
    ids_auto = [int(r["id"]) for r in auto if cambiati & set(r["_ids"])]
    # anche le auto senza confronti cambiati si rivalutano se un loro confronto vecchio non è più tra gli attivi
    # vecchi (già rinfrescato da un altro lavoro nel frattempo)
    gia = set(da_aprire) - {c["id"] for c in conf}
    ids_auto += [int(r["id"]) for r in auto if int(r["id"]) not in ids_auto and gia & set(r["_ids"])]
    st["auto_da_rivalutare"] = len(ids_auto)
    if ids_auto:
        try:
            if rivaluta is None:
                from .valutazione import valuta_e_scrivi as rivaluta
            es = rivaluta(conn, ids_auto, "confronti-mirati", pubblica=True)
            conn.commit()
            for k, n in (getattr(es, "stats", None) or {}).items():
                if k.startswith(("categoria:", "dati_insufficienti:")) or k == "pubblicate":
                    st["rivalutate_" + k] += n
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            st["errore_rivalutazione"] += 1
            log.warning("CONFRONTI_MIRATI rivalutazione non riuscita: %s", type(e).__name__)
    return st


def _controlla(conn, conf: list[dict], st: Counter) -> dict:
    """Riapre i confronti con il controllo del rinfresco (prova obbligatoria) e li segna come fa giro_rinfresca."""
    from ..pipeline import tempo_reale as tr
    from ..pipeline.verify import _mark
    cas = tr._client_as() if any(r["source"] == "autoscout24" for r in conf) else None
    csb = tr._client_subito() if any(r["source"] == "subito" for r in conf) else None
    cambiati, aperte, di_fila = [], 0, 0
    try:
        for r in conf:
            if di_fila >= STOP_ERRORI:
                st["fermato_blocchi"] = 1
                break
            try:
                _r, stato, motivo = tr._controlla_annuncio(r, cas, csb, None, lenient=False)
            except Exception:
                stato, motivo = "errore", "blocco:eccezione"
            aperte += 1
            bloccato = stato == "errore" and str(motivo or "").startswith("blocco")
            di_fila = di_fila + 1 if bloccato else 0
            if stato == "attivo" and motivo == "prezzo_illeggibile":
                stato = "errore"
            st[f"{r['source']}:{stato}"] += 1
            if stato == "attivo" and str(motivo or "").startswith("prezzo_cambiato:"):
                # un confronto vive del prezzo di OGGI: prezzo nuovo (come la raccolta, variazione plausibile già
                # controllata da _confronta_prezzo) e visto adesso
                tr.segna_prezzo_cambiato(conn, r["id"], int(str(motivo).split(":", 1)[1]))
                _mark(conn, r["id"], "attivo", None)
                st["prezzo_cambiato"] += 1
                cambiati.append(r["id"])
                continue
            if stato in ("attivo", "scomparso"):
                _mark(conn, r["id"], stato, r.get("price_eur"), motivo=motivo)
                cambiati.append(r["id"])
    finally:
        for c in (cas, csb):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
    st["pagine"] += aperte
    return {"aperte": aperte, "cambiati": cambiati}


def diagnostica(st: Counter) -> None:
    log.warning("CONFRONTI_MIRATI %s", json.dumps({"pagine_oggi": _GIORNO["pagine"], **dict(st)}, ensure_ascii=False))
