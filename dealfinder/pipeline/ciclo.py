"""Ciclo automatico completo: un solo lavoro programmato fa tutto, nessun intervento manuale.

Ad ogni esecuzione (ogni 3 ore):
  1. nuovi annunci Subito (generali + ricerche mirate ad auto con problemi), filtro istantaneo col modello
  2. una volta al giorno: base di mercato della Lombardia e, se serve, addestramento del modello
  3. analisi dei candidati di tutte le fonti (Subito e Facebook), se la chiave AI è presente
  4. una volta al giorno: verifica che le opportunità esistano ancora
  5. una volta a settimana: misura della qualità delle stime
  6. rapporto per fasce di prezzo nei log

Facebook resta nel suo lavoro (due volte al giorno, solo raccolta): i suoi candidati
vengono analizzati qui.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager

from ..db import connect

log = logging.getLogger("ciclo")


def _due(job: str, hours: int) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT max(started_at) > now() - make_interval(hours => %s) AS recent "
                           "FROM job_runs WHERE job=%s AND ok", (hours, job)).fetchone()
    return not (row and row["recent"])


def _una_volta(job: str) -> bool:
    """2/11: per i lavori da fare UNA sola volta (invii SMS/email, ecc.). Controlla e registra subito, in modo
    atomico: un riavvio del server a metà non li fa ripartire da capo (prima _due da solo non registrava nulla
    e ogni riavvio ripeteva l'invio)."""
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (job,))
        if conn.execute("SELECT 1 FROM job_runs WHERE job=%s LIMIT 1", (job,)).fetchone():
            return False
        conn.execute("INSERT INTO job_runs (job, finished_at, ok, stats) VALUES (%s, now(), true, '{}')", (job,))
        conn.commit()
    return True


class LavoroUnaVolta:
    """3/11 (fase 1): lavoro da fare una volta sola, segnato "fatto" SOLO quando è finito davvero.

    _una_volta segna fatto PRIMA di fare: un crash, un riavvio o un errore di memoria a metà lasciano il lavoro
    "fatto" per sempre. Qui invece:
      - inizia(job): lucchetto di sessione Postgres (pg_try_advisory_lock, si libera da solo se il processo muore)
        tenuto per tutta la durata; se il lavoro ha già una riga ok=true non si rifà; le righe rimaste aperte da un
        processo morto si chiudono come interrotte (ok=false) e si riparte; si registra una riga "iniziato"
        (finished_at NULL);
      - fatto(stats): ok=true, finished_at=now();
      - fallito(e): ok=false, finished_at=now(), il prossimo giro lo rifà;
      - dopo `tentativi_max` tentativi non riusciti (errori o crash) non si riprova più da solo (niente ciclo
        crash -> riavvio -> crash), resta nel log.
    Uso: `lav = inizia(job); if not lav: return; ...; lav.fatto()` con `lav.fallito(e)` nell'except,
    oppure `with una_volta_sicura(job) as fare: if fare: ...`.
    Per i lavori che cambiano dati o valutazioni; per gli invii (SMS, email) resta _una_volta.
    """

    def __init__(self, job: str, conn, uscita):
        self.job, self.conn, self._uscita, self.id, self.chiuso = job, conn, uscita, None, False

    def _chiudi(self, ok: bool, stats: dict) -> None:
        if self.chiuso:
            return
        self.chiuso = True
        try:
            self.conn.execute("UPDATE job_runs SET finished_at=now(), ok=%s, stats=%s WHERE id=%s",
                              (ok, json.dumps(stats, default=str), self.id))
            self.conn.commit()
        finally:
            self._rilascia()

    def _rilascia(self) -> None:
        try:
            self.conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (self.job,))
            self.conn.commit()
        except Exception:
            pass
        finally:
            u, self._uscita = self._uscita, None
            if u:
                u(None, None, None)

    def fatto(self, stats: dict | None = None) -> None:
        self._chiudi(True, stats or {})

    def rimanda(self, stats: dict | None = None) -> None:
        """12/11: lavoro interrotto apposta (es. memoria alta nel sito): non è fatto e non conta come tentativo
        fallito; la riga si toglie e il prossimo giro riprende dai passi fatti."""
        if self.chiuso:
            return
        self.chiuso = True
        try:
            self.conn.execute("DELETE FROM job_runs WHERE id=%s", (self.id,))
            self.conn.commit()
            log.warning("LAVORO %s rimandato: %s", self.job, json.dumps(stats or {}, default=str)[:300])
        finally:
            self._rilascia()

    def fallito(self, errore=None) -> None:
        self._chiudi(False, {"errore": (type(errore).__name__ + ": " + str(errore)[:200]) if errore else "errore"})


def inizia(job: str, tentativi_max: int = 3, connetti=None) -> LavoroUnaVolta | None:
    """Prende il lavoro una tantum `job`. None se è già fatto, se un altro processo lo sta facendo o se ha già
    fallito `tentativi_max` volte; altrimenti il lavoro, da chiudere con .fatto() o .fallito()."""
    cm = (connetti or connect)()
    conn = cm.__enter__()
    lav = None
    try:
        if not conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS preso", (job,)).fetchone()["preso"]:
            conn.commit()
            cm.__exit__(None, None, None)
            log.warning("LAVORO %s: già in corso in un altro processo, salto", job)
            return None
        lav = LavoroUnaVolta(job, conn, cm.__exit__)
        if conn.execute("SELECT 1 FROM job_runs WHERE job=%s AND ok LIMIT 1", (job,)).fetchone():
            lav._rilascia()
            return None
        n = conn.execute("SELECT count(*) AS n FROM job_runs WHERE job=%s AND ok IS NOT TRUE", (job,)).fetchone()["n"]
        if n >= tentativi_max:
            lav._rilascia()
            log.warning("LAVORO %s: %d tentativi non riusciti, non riprovo da solo", job, n)
            return None
        # righe aperte da un processo morto (il lucchetto è nostro, quindi nessuno le sta facendo): interrotte
        conn.execute("UPDATE job_runs SET finished_at=now(), ok=false, stats='{\"interrotto\": true}' "
                     "WHERE job=%s AND finished_at IS NULL", (job,))
        lav.id = conn.execute("INSERT INTO job_runs (job) VALUES (%s) RETURNING id", (job,)).fetchone()["id"]
        conn.commit()
        if n:
            log.warning("LAVORO %s: riprendo dopo %d tentativi non finiti", job, n)
        return lav
    except Exception:
        if lav is not None:
            lav._rilascia()
        else:
            cm.__exit__(None, None, None)
        raise


@contextmanager
def una_volta_sicura(job: str, tentativi_max: int = 3, connetti=None):
    """Come inizia/fatto/fallito ma con `with`: dentro si riceve True se va fatto; un'eccezione lo segna fallito."""
    lav = inizia(job, tentativi_max, connetti)
    if not lav:
        yield False
        return
    try:
        yield True
    except BaseException as e:
        lav.fallito(e)
        raise
    lav.fatto()


def _in_corso(job: str, hours: int = 8) -> bool:
    with connect() as conn:
        return bool(conn.execute("SELECT 1 FROM job_runs WHERE job=%s AND finished_at IS NULL "
                                 "AND started_at > now() - make_interval(hours => %s) LIMIT 1", (job, hours)).fetchone())


def _massiva_fatta() -> bool:
    """Raccolta massiva già fatta anche se un errore isolato l'ha segnata come non riuscita."""
    with connect() as conn:
        return bool(conn.execute("SELECT 1 FROM job_runs WHERE job='collect:massiva' AND finished_at IS NOT NULL "
                                 "AND (stats->>'subito:nuovo')::int > 1000 LIMIT 1").fetchone())


def fb_job() -> str:
    """Nome del lavoro Facebook: con FB_PARTE ogni servizio ha il suo (così non si bloccano a vicenda)."""
    fp = os.environ.get("FB_PARTE")
    return "collect:facebook" + (f":p{fp.replace('/', 'di')}" if fp else "")


def facebook_due(hours: int) -> bool:
    """Facebook da raccogliere? Contano solo i giri che hanno portato annunci: se Bright Data era
    fermo (credito finito), appena torna attivo si raccoglie subito, senza aspettare un giorno."""
    with connect() as conn:
        row = conn.execute("SELECT max(started_at) > now() - make_interval(hours => %s) AS recent FROM job_runs "
                           "WHERE job=%s AND ok AND stats::text LIKE '%%facebook:nuovo%%'",
                           (hours, fb_job())).fetchone()
    return not (row and row["recent"])


def _has_model() -> bool:
    with connect() as conn:
        return bool(conn.execute("SELECT 1 FROM price_models WHERE active LIMIT 1").fetchone())


def step(name: str, fn, *args):
    try:
        log.info("CICLO %s: inizio", name)
        out = fn(*args)
        log.info("CICLO %s: fatto %s", name, out if isinstance(out, dict) else "")
    except Exception:
        log.exception("CICLO %s: errore (il ciclo continua)", name)


def run() -> None:
    from ..pricing.backtest import run as backtest
    from ..pricing.train import run as train
    from . import bande, candidati, esporta, manuale, recheck
    from .collect import run as collect
    from .process import run as process

    # Prova una tantum: quante persone cercano un'auto su Facebook (rifatta se si aggiungono i gruppi)
    from . import cerco_probe
    if os.environ.get("BRIGHTDATA_API_KEY") and os.environ.get("FB_CERCO_PROBE", "1") == "1" \
            and _due(cerco_probe.job_name(), 24 * 30):
        step("prova_cerco", cerco_probe.run)
    # Prova del collegamento targhe (una volta, con la targa di prova gratuita di RegCheck)
    if os.environ.get("REGCHECK_USERNAME") and _due("prova_targa", 24 * 365):
        def prova_targa():
            from ..ai import plate
            from ..db import connect as _c, log_job as _lj
            v = plate.lookup(os.environ.get("REGCHECK_TEST_PLATE", "BN071VN"))
            log.info("PROVA_TARGA %s", v)
            with _c() as conn:
                _lj(conn, "prova_targa")(bool(v), {"risultato": v})
            return {"ok": bool(v)}
        step("prova_targa", prova_targa)
    step("opportunita", collect, "opportunita")
    # raccolta aggressiva di tutta la Lombardia (una volta per versione), poi la selezione
    massiva = False
    if _due("collect:massiva", 24 * 365) and not _massiva_fatta():
        step("raccolta_massiva", collect, "massiva")
        massiva = True
    # selezione dei 1.000 migliori di tutta la base, con foto, per il controllo uno per uno (una volta)
    from . import mille
    if _due(mille.job_name(), 24 * 365):
        step("mille", mille.run)
    if not massiva and _due("collect:profondo", 20):
        step("raccolta_profonda", collect, "profondo")
    from . import fb_rilettura
    if _due(fb_rilettura.JOB, 24 * 365):
        step("fb_rilettura", fb_rilettura.run)
    from .collect import _backfill_done
    if os.environ.get("BRIGHTDATA_API_KEY") and os.environ.get("FB_BACKFILL") == "1" and not _backfill_done():
        step("facebook_partenza", collect, "fb_backfill")
    if os.environ.get("BRIGHTDATA_API_KEY") or os.environ.get("BRIGHTDATA_NUOVA"):
        # lotti Facebook diventati pronti dopo la fine di un giro precedente: importati subito, nessuna spesa
        step("facebook_pronti", collect, "fb_importa")
    # Facebook lo raccolgono i servizi dealfinder-facebook (uno per account); nel ciclo solo se FB_NEL_CICLO=1
    if os.environ.get("BRIGHTDATA_API_KEY") and os.environ.get("FB_NEL_CICLO", "0") == "1" and facebook_due(int(os.environ.get("FACEBOOK_ORE", "23"))):
        step("facebook", collect, "facebook")
    # AutoScout24 (privati attorno a Milano): ogni 6 ore; si spegne con AUTOSCOUT=0
    if os.environ.get("AUTOSCOUT", "1") == "1" and not _in_corso("collect:autoscout_massiva") \
            and _due("collect:autoscout", int(os.environ.get("AUTOSCOUT_ORE", "6"))):
        step("autoscout", collect, "autoscout")
    if _due("collect:mercato", 20):
        step("mercato", collect, "mercato")
    # Il modello si riaddestra da solo quando la base cresce del 30% o ha più di 7 giorni
    step("modello", train, not _has_model())
    from ..ai.client import available, provider
    if available():
        log.info("CICLO analisi con %s", provider())
        step("analisi", process, "tutto")
    else:
        log.warning("CICLO analisi: nessuna chiave AI, si esportano i candidati per l'analisi a parte")
        # la selezione dei migliori con foto (mille) ha preso il posto di questo export; con tanti annunci
        # andava fuori memoria: si riaccende con CANDIDATI=1
        if os.environ.get("CANDIDATI", "0") == "1" and _due("candidati:v4", 2):
            step("candidati", candidati.run)
    step("analisi_manuale", manuale.run)
    if _due("recheck", 20):
        step("verifica", recheck.run)
    # Copia delle foto delle auto proposte (i link di Facebook scadono in pochi giorni)
    def foto_auto():
        from ..db import connect as _c
        from ..web import foto
        with _c() as conn:
            return foto.warm(conn, int(os.environ.get("FOTO_AUTO_PER_CICLO", "300")))
    step("foto", foto_auto)
    if _due("backtest", 24 * 7):
        step("qualita", backtest)
    # Modulo Incentivi: aggiornamento giornaliero degli stati dei bandi e degli avvisi (prototipo)
    def incentivi_giorno():
        from incentivi import job as inc_job, store as inc_store
        with connect() as conn:
            inc_store.ensure(conn)
            return {"fatto": False} if inc_job.ultimo_oggi(conn) else inc_job.esegui(conn)
    step("incentivi", incentivi_giorno)
    step("rapporto", bande.run)
    step("esportazione", esporta.run)
