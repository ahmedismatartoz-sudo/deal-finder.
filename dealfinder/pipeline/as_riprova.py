"""12/11 (buchi della raccolta, controllo 1 §3.4): AutoScout24 nel TEMPO REALE, "troppe richieste" (HTTP 429) ed errori
temporanei.

Prima: al primo 429 la lettura completa usciva dalla fascia e passava alla successiva (che falliva subito anche lei) e
la lettura dei nuovi si fermava; l'11/11 11 letture orarie su 27 hanno perso le fasce da 7.000-9.500 € in su.
Ora:
  - get(): riprova con attesa crescente (TR_AS_ATTESE, "15,45,90" secondi) su 429, 5xx ed errori di rete;
  - se il 429 resta, AutoScout24 si lascia stare per TR_AS_FERMO_MIN minuti (10) in tutto il processo (niente raffica
    di richieste respinte) e la lettura completa salva il punto in cui si è fermata (fascia e pagina, tabella
    tempo_reale, riga 'as_ripresa': sopravvive a un riavvio del sito): il giro dopo legge solo le fasce mancanti,
    da lì alla fine.
Interruttore: TR_AS_RIPROVA=0 torna al comportamento di prima (una richiesta, nessuna attesa).
"""
from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger("tempo_reale")

ATTESE = (15, 45, 90)
FERMO_MIN = 10
DA_RIPROVARE = (429, 500, 502, 503, 504)
_stato = {"fermo_fino": 0.0}


def attivo() -> bool:
    return os.environ.get("TR_AS_RIPROVA", "1") == "1"


def attese() -> tuple:
    try:
        v = tuple(float(x) for x in (os.environ.get("TR_AS_ATTESE") or "").split(",") if x.strip())
        return v or ATTESE
    except ValueError:
        return ATTESE


def fermo() -> bool:
    """AutoScout24 in pausa dopo un 429 che non passa?"""
    return time.time() < _stato["fermo_fino"]


def ferma(minuti: float | None = None) -> None:
    m = float(os.environ.get("TR_AS_FERMO_MIN") or FERMO_MIN) if minuti is None else minuti
    _stato["fermo_fino"] = time.time() + m * 60


def get(client, url: str, etichetta: str, dormi=time.sleep):
    """Risposta 200 (o un altro codice definitivo, es. 404) oppure None se non è arrivata dopo le riprove.
    Con un 429 finale mette AutoScout24 in pausa (fermo())."""
    if not attivo():
        try:
            return client.get(url)
        except Exception as e:
            log.warning("%s: %s", etichetta, str(e)[:120])
            return None
    if fermo():
        return None
    ultimo = None
    pause = attese()
    for t in range(len(pause) + 1):
        try:
            r = client.get(url)
            codice = r.status_code
        except Exception as e:
            r, codice = None, None
            ultimo = str(e)[:120]
        if r is not None and codice not in DA_RIPROVARE:
            return r
        ultimo = f"HTTP {codice}" if r is not None else ultimo
        if t < len(pause):
            log.warning("%s: %s, riprovo tra %.0fs (tentativo %d)", etichetta, ultimo, pause[t], t + 2)
            dormi(pause[t])
    log.warning("%s: %s anche dopo %d tentativi", etichetta, ultimo, len(pause) + 1)
    if ultimo == "HTTP 429":
        ferma()
        log.warning("TR autoscout: troppe richieste, AutoScout24 in pausa per %s minuti", os.environ.get("TR_AS_FERMO_MIN") or FERMO_MIN)
    return None


# ------------------------------------------------------------------ ripresa della lettura completa

def leggi_ripresa(conn) -> dict | None:
    try:
        r = conn.execute("SELECT stats FROM tempo_reale WHERE giro='as_ripresa'").fetchone()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    s = (r or {}).get("stats") if r else None
    if isinstance(s, str):
        try:
            s = json.loads(s)
        except ValueError:
            s = None
    return s if isinstance(s, dict) and "fascia" in s else None


def salva_ripresa(conn, fascia: int, pagina: int) -> None:
    try:
        conn.execute("INSERT INTO tempo_reale (giro, ultimo_at, stats) VALUES ('as_ripresa', now(), %s) "
                     "ON CONFLICT (giro) DO UPDATE SET ultimo_at=EXCLUDED.ultimo_at, stats=EXCLUDED.stats",
                     (json.dumps({"fascia": int(fascia), "pagina": int(pagina)}),))
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("TR autoscout: punto di ripresa non salvato: %s", str(e)[:120])


def togli_ripresa(conn) -> None:
    try:
        conn.execute("DELETE FROM tempo_reale WHERE giro='as_ripresa'")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def ordine_fasce(n: int, ripresa: dict | None) -> list[tuple[int, int]]:
    """[(indice fascia, pagina da cui partire)]: SOLO le fasce mancanti, cioè dalla fascia interrotta (e dalla sua
    pagina) fino all'ultima: le precedenti erano già state lette (12/11 revisione). Senza ripresa: tutte."""
    if not ripresa or not (0 <= int(ripresa.get("fascia", -1)) < n):
        return [(i, 1) for i in range(n)]
    i0, p0 = int(ripresa["fascia"]), max(1, min(20, int(ripresa.get("pagina") or 1)))
    return [(i0, p0)] + [(j, 1) for j in range(i0 + 1, n)]
