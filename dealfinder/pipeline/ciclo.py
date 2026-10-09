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

import logging
import os

from ..db import connect

log = logging.getLogger("ciclo")


def _due(job: str, hours: int) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT max(started_at) > now() - make_interval(hours => %s) AS recent "
                           "FROM job_runs WHERE job=%s AND ok", (hours, job)).fetchone()
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
    step("opportunita", collect, "opportunita")
    if _due("collect:profondo", 20):
        step("raccolta_profonda", collect, "profondo")
    from . import fb_rilettura
    if _due(fb_rilettura.JOB, 24 * 365):
        step("fb_rilettura", fb_rilettura.run)
    from .collect import _backfill_done
    if os.environ.get("BRIGHTDATA_API_KEY") and os.environ.get("FB_BACKFILL") == "1" and not _backfill_done():
        step("facebook_partenza", collect, "fb_backfill")
    if os.environ.get("BRIGHTDATA_API_KEY") and _due("collect:facebook", int(os.environ.get("FACEBOOK_ORE", "8"))):
        step("facebook", collect, "facebook")
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
        if _due("candidati:v3", 2):
            step("candidati", candidati.run)
    step("analisi_manuale", manuale.run)
    if _due("recheck", 20):
        step("verifica", recheck.run)
    if _due("backtest", 24 * 7):
        step("qualita", backtest)
    step("rapporto", bande.run)
    step("esportazione", esporta.run)
