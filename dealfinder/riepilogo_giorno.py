"""Riepilogo giornaliero (16/11, fondamenta): UN testo al giorno con lo stato di Skovo, per la casella che già riceve
gli allarmi della sentinella (info@skovo.app, Brevo transazionale: sentinella._invia_brevo).

    python -m dealfinder.riepilogo_giorno      stampa il testo di adesso (NON manda niente)

Contenuto (tutto in lettura, ultime 24 ore):
  - auto sul sito ora (fotografia sito_istantanea) e ieri alla stessa ora (sentinella_stato);
  - entrate e uscite dal sito (sito_storico: solo auto di privati, come la sezione "Già vendute");
  - imbuto del sito di oggi (sito_funnel_giorno: quante auto toglie ogni passo);
  - versioni del codice (commit) viste dai processi (config_impronte): un commit nuovo = un deploy;
  - esiti del canarino (canarino_esiti: quanti ok/ko e i motivi dei ko);
  - giri dei cron (job_runs: riusciti, falliti, ancora in corso) e allarmi della sentinella.
Una tabella che non c'è si salta (riga "non disponibile"), non ferma il riepilogo. Nessun dato personale.

Invio: `esegui()` manda al massimo UNA email al giorno, dopo le 8 ora di Roma (tabella riepilogo_inviati, una riga
per giorno); se l'invio non riesce riprova fino a 3 volte nello stesso giorno, mai due email. Va richiamata dal thread della sentinella del sito (vedi REGOLE_DEPLOY.md, "agganci"); da sola non
parte mai.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

log = logging.getLogger("riepilogo")

ORA_INVIO = 8                   # ora di Roma dalla quale parte il riepilogo del giorno
TIMEOUT_S = 60


def _ora_roma(t: datetime) -> datetime:
    from zoneinfo import ZoneInfo
    return t.astimezone(ZoneInfo("Europe/Rome"))


def _esiste(conn, tabella: str) -> bool:
    r = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS c", (tabella,)).fetchone()
    return bool(r and r["c"])


def _uno(conn, sql: str, p=()) -> dict:
    return dict(conn.execute(sql, p).fetchone() or {})


def _tutti(conn, sql: str, p=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, p).fetchall()]


def raccogli(conn) -> dict:
    """Numeri delle ultime 24 ore. Chiave assente = tabella non disponibile."""
    conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)
    d: dict = {}
    if _esiste(conn, "sito_istantanea"):
        d["sito"] = _uno(conn, "/*R:sito*/ SELECT count(*) AS n, count(*) FILTER (WHERE commerciante) AS commercianti, "
                               "round(extract(epoch FROM now() - max(salvata_at)) / 3600.0, 1) AS ore FROM sito_istantanea")
    if _esiste(conn, "sentinella_stato"):
        d["sito_ieri"] = _uno(conn, "/*R:ieri*/ SELECT n_sito FROM sentinella_stato "
                                    "WHERE ora = date_trunc('hour', now()) - interval '24 hours'").get("n_sito")
    if _esiste(conn, "sito_storico"):
        fuori = ("AND listing_id NOT IN (SELECT listing_id FROM sito_istantanea)"
                 if "sito" in d else "")
        d["movimenti"] = _uno(conn, "/*R:movimenti*/ SELECT "
                                    "count(*) FILTER (WHERE prima > now() - interval '24 hours') AS entrate, "
                                    "count(*) FILTER (WHERE ultima > now() - interval '24 hours' "
                                    "AND ultima < now() - interval '1 hour' " + fuori + ") AS uscite FROM sito_storico")
    if _esiste(conn, "sito_funnel_giorno"):
        d["funnel"] = _tutti(conn, "/*R:funnel*/ SELECT giorno, passo, entrate, uscite FROM sito_funnel_giorno "
                                   "WHERE giorno = (SELECT max(giorno) FROM sito_funnel_giorno) "
                                   "ORDER BY entrate DESC, passo LIMIT 40")
    if _esiste(conn, "config_impronte"):
        d["versioni"] = _tutti(conn, "/*R:versioni*/ SELECT commit_git, min(at) AS dal, "
                                     "string_agg(DISTINCT processo, ', ' ORDER BY processo) AS processi FROM config_impronte "
                                     "WHERE at > now() - interval '24 hours' AND commit_git IS NOT NULL "
                                     "GROUP BY commit_git ORDER BY min(at) LIMIT 30")
    if _esiste(conn, "canarino_esiti"):
        d["canarino"] = _uno(conn, "/*R:canarino*/ SELECT count(*) FILTER (WHERE ok) AS ok, "
                                   "count(*) FILTER (WHERE NOT ok) AS ko FROM canarino_esiti "
                                   "WHERE at > now() - interval '24 hours'")
        d["canarino_motivi"] = _tutti(conn, "/*R:canarino_ko*/ SELECT m AS motivo, count(*) AS n FROM canarino_esiti, "
                                            "jsonb_array_elements_text(motivi) AS m WHERE NOT ok "
                                            "AND at > now() - interval '24 hours' GROUP BY m ORDER BY count(*) DESC LIMIT 5")
    if _esiste(conn, "job_runs"):
        d["cron"] = _tutti(conn, "/*R:cron*/ SELECT job, count(*) FILTER (WHERE ok) AS ok, "
                                 "count(*) FILTER (WHERE ok = false) AS falliti, "
                                 "count(*) FILTER (WHERE finished_at IS NULL) AS in_corso FROM job_runs "
                                 "WHERE started_at > now() - interval '24 hours' GROUP BY job ORDER BY job LIMIT 60")
    if _esiste(conn, "sentinella_allarmi"):
        d["allarmi"] = _tutti(conn, "/*R:allarmi*/ SELECT controllo, count(*) AS n, sum(volte) AS volte "
                                    "FROM sentinella_allarmi WHERE visto_at > now() - interval '24 hours' "
                                    "GROUP BY controllo ORDER BY count(*) DESC")
    return d


def _nd(x) -> str:
    return "non disponibile" if x is None else str(x)


def testo(d: dict, adesso: datetime | None = None) -> tuple[str, str]:
    """(oggetto, corpo) dell'email. Funzione pura: si prova senza database."""
    adesso = adesso or datetime.now(timezone.utc)
    giorno = _ora_roma(adesso).strftime("%d/%m/%Y")
    righe = []
    sito = d.get("sito")
    n = (sito or {}).get("n")
    ieri = d.get("sito_ieri")
    if sito is not None:
        diff = f" ({int(n) - int(ieri):+d} da ieri alla stessa ora: {ieri})" if n is not None and ieri else ""
        righe.append(f"Auto sul sito: {n}{diff}, di cui commercianti {sito.get('commercianti')}; "
                     f"fotografia di {_nd(sito.get('ore'))} ore fa")
    else:
        righe.append("Auto sul sito: non disponibile (manca sito_istantanea)")
    mv = d.get("movimenti")
    righe.append(f"Ultime 24 ore (privati): entrate {mv.get('entrate')}, uscite {mv.get('uscite')}" if mv
                 else "Entrate/uscite: non disponibili (manca sito_storico)")
    ca = d.get("canarino")
    if ca is not None:
        righe.append(f"Canarino: {ca.get('ok') or 0} ok, {ca.get('ko') or 0} ko")
        for m in d.get("canarino_motivi") or []:
            righe.append(f"   ko {m['n']} volte: {m['motivo']}")
    else:
        righe.append("Canarino: nessun esito (tabella canarino_esiti assente: il canarino non gira ancora)")
    ver = d.get("versioni")
    if ver is not None:
        righe.append(f"Versioni del codice viste dai processi nelle 24 ore: {len(ver)}")
        for v in ver:
            dal = v.get("dal")
            quando = _ora_roma(dal).strftime("%d/%m %H:%M") if isinstance(dal, datetime) else _nd(dal)
            righe.append(f"   {str(v.get('commit_git'))[:12]} dal {quando} ({v.get('processi')})")
    cron = d.get("cron")
    if cron is not None:
        righe.append("Giri dei lavori (24 ore): " + ("nessuno" if not cron else ""))
        for c in cron:
            avviso = "  <-- FALLITI" if c.get("falliti") else ""
            righe.append(f"   {c['job']}: {c.get('ok') or 0} ok, {c.get('falliti') or 0} falliti, "
                         f"{c.get('in_corso') or 0} in corso{avviso}")
    al = d.get("allarmi")
    if al:
        righe.append("Allarmi della sentinella (24 ore): " + ", ".join(f"{a['controllo']} {a['n']}" for a in al))
    elif al is not None:
        righe.append("Allarmi della sentinella (24 ore): nessuno")
    fun = d.get("funnel")
    if fun:
        righe.append(f"Imbuto del sito ({_nd(fun[0].get('giorno'))}): passo, auto entrate, auto tolte")
        for f in fun:
            righe.append(f"   {f['passo']}: {f['entrate']} -> tolte {f['uscite']}")
    problemi = sum(int(c.get("falliti") or 0) for c in cron or []) + int((ca or {}).get("ko") or 0)
    stato = "da guardare" if problemi or (al and len(al)) else "tutto regolare"
    oggetto = f"Skovo riepilogo del {giorno}: {n if n is not None else '?'} auto sul sito, {stato}"
    corpo = (oggetto + "\n\n" + "\n".join(righe) +
             "\n\nNumeri dalle tabelle del database (sito_istantanea, sito_storico, sito_funnel_giorno, config_impronte, "
             "canarino_esiti, job_runs, sentinella_allarmi). Una email al giorno, dopo le 8.")
    return oggetto, corpo


def prepara(connetti=None, adesso: datetime | None = None) -> tuple[str, str]:
    """(oggetto, corpo) letti adesso dal database. Non manda niente."""
    if connetti is None:
        from .db import connect as connetti
    with connetti() as conn:
        d = raccogli(conn)
        conn.rollback()
    return testo(d, adesso)


TABELLA_SQL = """CREATE TABLE IF NOT EXISTS riepilogo_inviati (giorno DATE PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ok BOOLEAN)"""
COLONNA_SQL = "ALTER TABLE riepilogo_inviati ADD COLUMN IF NOT EXISTS tentativi INT NOT NULL DEFAULT 1"
TENTATIVI_MAX = 3               # invii non riusciti: si riprova fino a 3 volte nello stesso giorno
RIPROVA_MIN = 20                # non prima di 20 minuti dal tentativo precedente (anche se ancora in corso altrove)

# Prenotazione atomica del tentativo: la prima volta del giorno, oppure di nuovo se l'invio non è riuscito, i tentativi
# sono meno di TENTATIVI_MAX e il precedente ha più di RIPROVA_MIN minuti. Una riga sola per giorno: mai due email.
PRENOTA_SQL = """INSERT INTO riepilogo_inviati (giorno, at, tentativi) VALUES (%s, %s, 1)
ON CONFLICT (giorno) DO UPDATE SET tentativi = riepilogo_inviati.tentativi + 1, at = EXCLUDED.at, ok = NULL
WHERE riepilogo_inviati.ok IS DISTINCT FROM true AND riepilogo_inviati.tentativi < %s
  AND riepilogo_inviati.at < EXCLUDED.at - make_interval(mins => %s)
RETURNING tentativi"""


def esegui(connetti=None, invia=None, adesso: datetime | None = None) -> dict:
    """Una volta al giorno dopo le 8 di Roma: prepara il testo e lo passa a `invia(oggetto, corpo)` (default: lo
    stesso canale della sentinella, sentinella._invia_brevo). Il tentativo è prenotato PRIMA dell'invio (mai due email
    anche con due processi insieme); se l'invio non riesce si riprova fino a TENTATIVI_MAX volte nello stesso giorno,
    a distanza di almeno RIPROVA_MIN minuti. RIEPILOGO_GIORNO=0 lo spegne. Non solleva mai."""
    if os.environ.get("RIEPILOGO_GIORNO", "1") == "0":
        return {"spento": True}
    adesso = adesso or datetime.now(timezone.utc)
    roma = _ora_roma(adesso)
    if roma.hour < ORA_INVIO:
        return {"presto": True}
    try:
        if connetti is None:
            from .db import connect as connetti
        with connetti() as conn:
            conn.execute(TABELLA_SQL)
            conn.execute(COLONNA_SQL)
            nuovo = conn.execute(PRENOTA_SQL, (roma.date(), adesso, TENTATIVI_MAX, RIPROVA_MIN)).fetchone()
            conn.commit()
            if not nuovo:
                return {"gia_fatto": True}
            d = raccogli(conn)
            conn.rollback()
            oggetto, corpo = testo(d, adesso)
            if invia is None:
                from .sentinella import _invia_brevo as invia
            ok = bool(invia(oggetto, corpo))
            conn.execute("UPDATE riepilogo_inviati SET ok = %s WHERE giorno = %s", (ok, roma.date()))
            conn.commit()
        log.warning("RIEPILOGO_GIORNO %s", json.dumps({"giorno": str(roma.date()), "inviato": ok,
                                                        "tentativo": int(nuovo["tentativi"])}))
        return {"inviato": ok, "oggetto": oggetto, "tentativo": int(nuovo["tentativi"])}
    except Exception as e:
        log.warning("RIEPILOGO_GIORNO non riuscito: %s", type(e).__name__)
        return {"errore": type(e).__name__}


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ogg, corpo = prepara()
    print(corpo)
    sys.exit(0)
