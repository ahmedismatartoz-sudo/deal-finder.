"""Verifica che gli annunci esistano ancora PRIMA di analizzarli e PRIMA di mostrarli.

Un'opportunità è visibile ai commercianti solo se verificata nelle ultime 24 ore.
Subito: apertura della pagina. Facebook: controllo per URL con Bright Data (a lotti).
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import logging
from collections import Counter

from ..collectors.brightdata import BrightDataFacebookCollector
from ..collectors.subito import SubitoCollector
from ..config import settings

log = logging.getLogger("verify")
VISIBLE_HOURS = 24


# 3/11 (studio 4 §4.4): motivi della sparizione. 'scaduto' (annuncio scaduto sul portale) NON è una vendita: va nello
# stato a parte 'scaduto', così valore reale, vendute e calibrazione (che leggono status='scomparso') non lo contano.
MOTIVI = ("404", "reindirizzato", "rimosso", "scaduto", "ripubblicato")


def mark(conn, lid: int, status: str, price=None, motivo: str | None = None, sito: bool = False) -> None:
    """'attivo': visto adesso. 'scomparso'/'venduto': tolto, con il motivo se c'è una prova (MOTIVI).
    La vendita è avvenuta tra l'ultima volta che l'annuncio era online (last_seen_at / last_checked_at) e adesso:
    disappeared_at è il punto di mezzo (prima era la data del controllo, che gonfiava i "giorni online" degli
    annunci ricontrollati tardi); la data del controllo resta in last_checked_at.
    sito=True: auto che era sul sito (campione distorto: le più convenienti), segnata in stage_reason
    ('venduta_sito') per tenerla separata nelle stime."""
    if status == "attivo":
        conn.execute("UPDATE listings SET last_checked_at=now(), last_seen_at=now() WHERE id=%s", (lid,))
    elif status in ("scomparso", "venduto"):
        from ..db import ha_colonna
        nuovo = "scaduto" if motivo == "scaduto" else "scomparso"
        extra = ", disappear_reason=%(motivo)s" if motivo in MOTIVI and ha_colonna(conn, "disappear_reason") else ""
        nota = status + (" sito" if sito else "") + (f" ({motivo})" if motivo else "")
        conn.execute(
            f"UPDATE listings SET status='{nuovo}', "
            "disappeared_at = LEAST(now(), GREATEST(last_seen_at, COALESCE(last_checked_at, last_seen_at)) "
            "+ (now() - GREATEST(last_seen_at, COALESCE(last_checked_at, last_seen_at))) / 2), "
            "last_checked_at=now(), stage_reason=COALESCE(stage_reason,'') || ' | ' || %(nota)s" + extra +
            " WHERE id=%(id)s", {"nota": nota, "motivo": motivo, "id": lid})
        conn.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s,%s,%s)",
                     (lid, nuovo, price))


def _mark(conn, lid: int, status: str, price=None, motivo: str | None = None, sito: bool = False) -> None:
    """Segna e salva subito, una riga alla volta: i blocchi restano brevissimi e un incrocio con un altro
    processo (deadlock) non fa più fallire tutto il giro: si riprova e, se serve, si salta quella riga."""
    for tent in range(3):
        try:
            mark(conn, lid, status, price, motivo=motivo, sito=sito)
            conn.commit()
            return
        except Exception as e:
            if type(e).__name__ not in ("DeadlockDetected", "LockNotAvailable", "SerializationFailure"):
                raise
            conn.rollback()
            log.warning("verifica: riga %s bloccata (%s), tentativo %s", lid, type(e).__name__, tent + 1)
            import time
            time.sleep(1 + tent * 2)


# 3/11: annunci Subito non più visti nelle ricerche. Prima diventavano TUTTI 'scomparso' dopo 36 ore (mark_disappeared),
# anche se la ricerca della notte non li aveva cercati (si leggono solo le pagine più recenti): migliaia di auto ancora
# in vendita risultavano "vendute" (6.239 'riapparso' in un giro) e falsavano i prezzi. Ora si apre la pagina di
# ognuno e si segna 'scomparso' solo con una prova; con errori o blocchi non si segna niente.
SCOMPARSI_MAX = 1200            # pagine aperte al massimo per giro
SCOMPARSI_MINUTI = 30           # tempo massimo per giro
SCOMPARSI_PAUSA = (1.0, 2.0)    # secondi tra una pagina e l'altra (ritmo rispettoso, una alla volta)


def stato_con_prova(source: str):
    """Funzione url -> (stato, motivo) con lo stesso criterio per tutte le fonti: 'scomparso' solo con una prova
    (404/410, rimando alla ricerca, scritta "scaduto/rimosso"), 'errore' = nessuna decisione.
    AutoScout24: pagine gratuite, stessa lettura di _as_dettaglio (prima le AutoScout sparite restavano 'attivo'
    per sempre e non diventavano mai vendute)."""
    from . import tempo_reale as tr
    if source == "autoscout24":
        client = tr._client_as()

        def f(u):
            st, _det, motivo = tr._as_esito(client, u)
            return st, motivo
        return f
    client = tr._client_subito()

    def g(u):
        return tr.subito_esito_con_prova(client, u)
    return g


def scomparsi_con_prova(conn, source: str = "subito", not_seen_hours: int = 36, limite: int = SCOMPARSI_MAX,
                        minuti: float = SCOMPARSI_MINUTI, stato=None, pausa=SCOMPARSI_PAUSA, zona=None) -> Counter:
    """Annunci non visti da `not_seen_hours` ore: si apre la pagina (prima quelli della zona, poi i più vecchi).
    'scomparso' solo con prova, 'attivo' = visto adesso (last_seen_at aggiornato), 'errore' = nessuna modifica.
    Si ferma da solo se il sito blocca (molti errori di fila). Non solleva mai eccezioni: in caso di problemi
    smette e nessun annuncio viene segnato senza prova."""
    import random
    import time
    stats: Counter = Counter()
    try:
        if zona is None:
            from ..zone import tutte
            zona = tutte()
        rows = conn.execute(
            """SELECT id, url, price_eur FROM listings
               WHERE source=%s AND status='attivo' AND last_seen_at < now() - make_interval(hours => %s)
               ORDER BY (upper(coalesce(province, '')) = ANY(%s)) DESC, last_seen_at ASC LIMIT %s""",
            (source, not_seen_hours, list(zona), limite)).fetchall()
        stats["da_controllare"] = len(rows)
        if not rows:
            return stats
        if stato is None:
            stato = stato_con_prova(source)
        fine = time.time() + minuti * 60
        errori_di_fila = 0
        for r in rows:
            if time.time() > fine:
                stats["fermato_tempo"] += 1
                break
            st = stato(r["url"])
            motivo = None
            if isinstance(st, tuple):            # (stato, motivo della sparizione)
                st, motivo = st
            stats[st] += 1
            if motivo:
                stats[f"motivo:{motivo}"] += 1
            if st in ("attivo", "scomparso"):
                errori_di_fila = 0
                _mark(conn, r["id"], st, r.get("price_eur"), motivo=motivo)
            else:
                errori_di_fila += 1
                if errori_di_fila >= 15:
                    stats["fermato_errori"] += 1
                    log.warning("SCOMPARSI %s: %d errori di fila (sito bloccato?), mi fermo", source, errori_di_fila)
                    break
            if pausa:
                time.sleep(random.uniform(*pausa))
    except Exception as e:
        stats["eccezione"] += 1
        log.warning("SCOMPARSI %s non completato (nessun annuncio segnato senza prova): %s", source, str(e)[:200])
        try:
            conn.rollback()
        except Exception:
            pass
    log.warning("SCOMPARSI %s con prova: %s", source, dict(stats))
    return stats


def verify_rows(conn, rows: list[dict]) -> Counter:
    """Verifica un gruppo di annunci; ritorna i conteggi per esito."""
    stats: Counter = Counter()
    fb = [r for r in rows if r["source"] == "facebook"]
    sb = [r for r in rows if r["source"] == "subito"]
    asx = [r for r in rows if r["source"] == "autoscout24"]
    if asx:
        # AutoScout24: visti dalla raccolta nelle ultime 48 ore = attivi; gli altri si riaprono (404/410 = venduto)
        fresh = {x["id"] for x in conn.execute(
            "SELECT id FROM listings WHERE id = ANY(%s) AND last_seen_at > now() - make_interval(hours => %s)",
            ([r["id"] for r in asx], int(os.environ.get("VERIFICA_ORE_AUTOSCOUT", "48")))).fetchall()}
        from ..collectors.autoscout import AutoScoutCollector
        col = AutoScoutCollector()
        for r in asx:
            if r["id"] in fresh:
                _mark(conn, r["id"], "attivo", r.get("price_eur"))
                stats["autoscout24:attivo_recente"] += 1
                continue
            # 3/11 (studio 4, bug): prima qualsiasi 200 valeva "attivo", anche il rimando alla ricerca /lst di un
            # annuncio tolto (e mark('attivo') lo rendeva di nuovo "fresco" come confronto). Ora la stessa lettura
            # della pagina di _as_dettaglio: 404/410, rimando a /lst, "non è più disponibile" = scomparso.
            from .tempo_reale import _as_esito
            st, _det, motivo = _as_esito(col.client, r["url"])
            if st != "errore":
                _mark(conn, r["id"], st, r.get("price_eur"), motivo=motivo)
            stats[f"autoscout24:{st}"] += 1
    if fb:
        bd = BrightDataFacebookCollector()
        if bd.configured():
            try:
                res = bd.check_urls([r["url"] for r in fb])
                for r in fb:
                    st = res.get(r["url"], "errore")
                    _mark(conn, r["id"], st, r.get("price_eur"))
                    stats[f"facebook:{st}"] += 1
            except Exception as e:
                log.warning("verifica Facebook fallita: %s", e)
                stats["facebook:errore"] += len(fb)
    if sb:
        # Visti da Subito nelle ultime ore durante la raccolta: sono attivi, non serve riaprirli
        fresh = {x["id"] for x in conn.execute(
            "SELECT id FROM listings WHERE id = ANY(%s) AND last_seen_at > now() - make_interval(hours => %s)",
            ([r["id"] for r in sb], int(os.environ.get("VERIFICA_ORE_FRESCHI", "6")))).fetchall()}
        for r in sb:
            if r["id"] in fresh:
                _mark(conn, r["id"], "attivo", r.get("price_eur"))
                stats["subito:attivo_recente"] += 1
        todo = [r for r in sb if r["id"] not in fresh]

        def check(r):
            col = SubitoCollector(proxy=settings.scraper_proxy)
            try:
                return r, ("attivo" if col.fetch(r["url"]) is not None else "scomparso")
            except Exception as e:
                log.warning("verifica Subito fallita %s: %s", r["url"], e)
                return r, "errore"

        # pochi controlli in parallelo: più veloce, ma sempre con un ritmo rispettoso per il sito
        with ThreadPoolExecutor(max_workers=int(os.environ.get("VERIFICA_PARALLELI", "3"))) as ex:
            for r, st in ex.map(check, todo):
                _mark(conn, r["id"], st, r.get("price_eur"))
                stats[f"subito:{st}"] += 1
    conn.commit()
    return stats
