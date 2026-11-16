"""16/11 (fond-server): il cron "opportunità" (ogni 3 ore) dura meno di 3 ore e i lavori pesanti vanno a turno.

Prima ogni giro faceva in fila: lettura della zona (anche completa, 4-5 ore), Torino completo, supercar Europa (raccolta e
valutazione: la riga 'supercar_eu' non veniva mai scritta, quindi a OGNI giro), confronti extra, ricalcolo e i lavori
COLLECT_PRIMA: 4-10 ore per trovare 2-37 annunci nuovi, e Render saltava i giri successivi.

Ora:
  - Giro: tetto di tempo GIRO_ORE_MAX (2 h 45 min) dall'avvio del cron; una riga 'giro:opportunita' in job_runs con il
    battito e l'avanzamento (fase in corso, secondi di ogni fase) scritti DURANTE il giro.
  - Ogni giro fa solo il necessario: annunci nuovi della zona (lettura incrementale) e la loro valutazione.
  - Lavori pesanti (confronti extra, supercar raccolta e valutazione, ricalcolo, voci di COLLECT_PRIMA) una volta al
    giorno, A TURNO: nel tempo che resta a ogni giro parte il più in ritardo che ci sta (stima = durata mediana degli
    ultimi giri riusciti, 'turno:<nome>' in job_runs; senza storia la stima nel codice). Ognuno con un lucchetto di
    sessione Postgres (si libera da solo se il processo muore): due processi non fanno mai lo stesso lavoro insieme.
  - Giro completo della zona e Torino completo (ore di lettura): una volta per notte nel cron mercato (01:13 UTC), in un
    processo nuovo ('collect notte') con la STESSA configurazione del cron opportunità (fotografia delle sue variabili
    non segrete salvata nel database a ogni giro): stesse funzioni, stesse regole, stesso risultato.

GIRO_LEGGERO=0 torna al comportamento di prima (tutto in fila, solo i vecchi limiti di tempo).
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("turni")

GIRO_ORE_MAX = 2.75            # tetto del giro ogni 3 ore (prima della partenza del giro dopo, con margine)
RISERVA_MIN = 15               # minuti tenuti per la fine del giro (analisi, scheda tecnica, registrazione)
COMPLETO_MAX_ORE = 30          # senza giro completo da tante ore (notte non riuscita) il giro lo fa da sé, come prima
NOTTE_ORE_MAX = 6.0            # tempo massimo del passo notturno nel cron mercato (zona completa + Torino completo)

# Stime iniziali in minuti (dai log del 13-16/11), usate finché job_runs non ha almeno un giro riuscito del turno
STIME_MIN = {
    "confronti_extra": 40,
    "supercar_raccolta": 95,
    "supercar_valuta": 70,
    "ricalcolo": 45,
    "torino_completo": 70,
    "zona_completo": 285,
    "prima": 45,
}


def giro_leggero() -> bool:
    return os.environ.get("GIRO_LEGGERO", "1") != "0"


def ore_max() -> float:
    try:
        return float(os.environ.get("GIRO_ORE_MAX") or GIRO_ORE_MAX)
    except ValueError:
        return GIRO_ORE_MAX


# ------------------------------------------------------------------ scelta dei lavori a turno (pura, testata)

@dataclass
class Turno:
    nome: str
    stima_min: float
    ritardo_ore: float                  # quanto è in ritardo rispetto alla sua cadenza (più alto = prima)
    fn: Callable | None = None
    priorita: int = 0                   # a parità di ritardo; il ricalcolo per primo (come dal 6/11)
    dati: dict = field(default_factory=dict)


def ordina(turni: list[Turno]) -> list[Turno]:
    return sorted(turni, key=lambda t: (-t.priorita, -t.ritardo_ore, t.nome))


def scegli(turni: list[Turno], resto_min: float) -> Turno | None:
    """Il prossimo lavoro da fare con `resto_min` minuti liberi: il più urgente la cui stima ci sta. None se nessuno."""
    for t in ordina(turni):
        if t.stima_min <= resto_min:
            return t
    return None


def stima_da_durate(durate_s: list, default_min: float) -> float:
    """Mediana delle durate (secondi) dei giri riusciti, in minuti, con un 15% di margine; senza storia il default."""
    d = [float(x) for x in durate_s if x is not None and float(x) > 0]
    if not d:
        return float(default_min)
    return round(statistics.median(d) / 60 * 1.15, 1)


# ------------------------------------------------------------------ database

def _connetti():
    from ..db import connect
    return connect()


def stima(nome: str, default_min: float | None = None, connetti=None) -> float:
    default_min = STIME_MIN.get(nome.split(":")[0], STIME_MIN["prima"]) if default_min is None else default_min
    try:
        with (connetti or _connetti)() as c:
            righe = c.execute("SELECT extract(epoch FROM finished_at - started_at) AS s FROM job_runs WHERE job=%s "
                              "AND ok AND finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 3",
                              ("turno:" + nome,)).fetchall()
        return stima_da_durate([r["s"] for r in righe], default_min)
    except Exception:
        return float(default_min)


def ore_dall_ultimo(job: str, connetti=None, prefisso: bool = False) -> float | None:
    """Ore dall'ultimo giro riuscito di `job` (con prefisso=True anche 'job…'); None se mai."""
    try:
        with (connetti or _connetti)() as c:
            if prefisso:
                r = c.execute("SELECT extract(epoch FROM now() - max(started_at)) / 3600.0 AS h FROM job_runs "
                              "WHERE job LIKE %s AND ok", (job.replace("%", r"\%").replace("_", r"\_") + "%",)).fetchone()
            else:
                r = c.execute("SELECT extract(epoch FROM now() - max(started_at)) / 3600.0 AS h FROM job_runs "
                              "WHERE job=%s AND ok", (job,)).fetchone()
        return float(r["h"]) if r and r["h"] is not None else None
    except Exception:
        return None


def ritardo(job: str, ogni_ore: float, connetti=None) -> float | None:
    """Ore di ritardo rispetto alla cadenza (>= 0 = dovuto); None se non dovuto. Mai fatto: molto in ritardo."""
    h = ore_dall_ultimo(job, connetti)
    if h is None:
        return 1000.0
    return h - ogni_ore if h >= ogni_ore else None


@contextmanager
def lucchetto(nome: str, connetti=None, attesa_s: float = 0, passo_s: float = 60):
    """Lucchetto di sessione Postgres per tutta la durata del blocco: dentro si riceve True se è nostro, False se lo
    tiene un altro processo (che sta facendo lo stesso lavoro). Si libera da solo se il processo muore.
    attesa_s: quanto aspettare che l'altro finisca (riprova ogni passo_s). Senza database: True."""
    cm = None
    preso = True
    try:
        cm = (connetti or _connetti)()
        conn = cm.__enter__()
        fine = time.time() + float(attesa_s or 0)
        while True:
            preso = bool(conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS preso",
                                      ("turno:" + nome,)).fetchone()["preso"])
            conn.commit()
            if preso or time.time() + passo_s > fine:
                break
            log.warning("TURNO %s: aspetto il lucchetto (lo tiene un altro processo)", nome)
            time.sleep(passo_s)
    except Exception as e:
        log.warning("TURNO lucchetto %s non disponibile (%s): procedo", nome, type(e).__name__)
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass
        cm = None
    try:
        yield preso
    finally:
        if cm is not None:
            try:
                if preso:
                    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("turno:" + nome,))
                    conn.commit()
            except Exception:
                pass
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass


def esegui(nome: str, fn: Callable, connetti=None) -> dict | None:
    """Un lavoro a turno: lucchetto + riga 'turno:<nome>' (con battito) chiusa ok/fallito. None se occupato.
    La riga si apre e si chiude con connessioni brevi (un lavoro di ore non tiene una connessione ferma: se il database
    la chiudesse, la chiusura della riga fallirebbe)."""
    from ..db import batti, smetti
    cn = connetti or _connetti
    with lucchetto(nome, connetti) as preso:
        if not preso:
            log.warning("TURNO %s: lo sta facendo un altro processo, salto", nome)
            return None
        t0 = time.time()
        jid = None
        try:
            with cn() as c:
                jid = c.execute("INSERT INTO job_runs (job) VALUES (%s) RETURNING id", ("turno:" + nome,)).fetchone()["id"]
                c.commit()
            batti(jid)
        except Exception as e:
            log.warning("TURNO %s: riga non aperta (%s)", nome, type(e).__name__)

        def chiudi(ok: bool, dati: dict) -> None:
            smetti(jid)
            if jid is None:
                return
            try:
                with cn() as c:
                    c.execute("UPDATE job_runs SET finished_at=now(), ok=%s, stats=%s WHERE id=%s",
                              (ok, json.dumps(dati, default=str), jid))
                    c.commit()
            except Exception as e:
                log.warning("TURNO %s: riga non chiusa (%s)", nome, type(e).__name__)
        try:
            esito = fn()
        except Exception as e:
            chiudi(False, {"errore": type(e).__name__ + ": " + str(e)[:200], "secondi": round(time.time() - t0)})
            log.warning("TURNO %s non riuscito: %s", nome, str(e)[:300])
            return {"errore": True}
        dati = {k: v for k, v in esito.items() if isinstance(v, (int, float, str, bool))} if isinstance(esito, dict) else {}
        dati["secondi"] = round(time.time() - t0)
        chiudi(True, dati)
        log.warning("TURNO %s fatto in %d min: %s", nome, dati["secondi"] // 60, json.dumps(dati, default=str)[:400])
        return dati


# ------------------------------------------------------------------ giro con tetto di tempo e avanzamento

class Giro:
    """Riga 'giro:<nome>' in job_runs aperta all'avvio (con battito), avanzamento scritto a ogni fase, chiusa a fine
    giro (anche da sys.exit, con atexit) con le durate delle fasi. resto_min() = minuti che restano nel tetto."""

    def __init__(self, nome: str, t0: float | None = None, ore: float | None = None, connetti=None):
        self.nome, self.t0 = nome, float(t0 if t0 is not None else time.time())
        self.ore = ore if ore is not None else ore_max()
        self.connetti = connetti or _connetti
        self.fasi: dict = {}
        self.fase_ora, self.fase_t = None, None
        self.id, self.chiuso = None, False
        self.rimandati: list = []

    def apri(self) -> "Giro":
        try:
            from ..db import batti
            with self.connetti() as c:
                self.id = c.execute("INSERT INTO job_runs (job, stats) VALUES (%s, %s) RETURNING id",
                                    ("giro:" + self.nome, json.dumps({"tetto_ore": self.ore}))).fetchone()["id"]
                c.commit()
            batti(self.id)
        except Exception as e:
            log.warning("GIRO %s: riga non aperta (%s)", self.nome, type(e).__name__)
        import atexit
        atexit.register(self.chiudi)
        return self

    def passate_min(self, ora: float | None = None) -> float:
        return ((ora if ora is not None else time.time()) - self.t0) / 60

    def resto_min(self, ora: float | None = None) -> float:
        return self.ore * 60 - self.passate_min(ora)

    def _scrivi(self, extra: dict) -> None:
        if self.id is None:
            return
        try:
            with self.connetti() as c:
                c.execute("UPDATE job_runs SET stats = COALESCE(stats, '{}'::jsonb) || %s::jsonb WHERE id=%s",
                          (json.dumps(extra, default=str), self.id))
                c.commit()
        except Exception as e:
            log.debug("GIRO avanzamento non scritto: %s", type(e).__name__)

    def fase(self, nome: str | None) -> None:
        """Chiude la fase in corso (ne somma i secondi) e apre `nome` (None = nessuna)."""
        adesso = time.time()
        if self.fase_ora is not None:
            self.fasi[self.fase_ora] = self.fasi.get(self.fase_ora, 0) + round(adesso - self.fase_t)
        self.fase_ora, self.fase_t = nome, adesso
        self._scrivi({"fase": nome, "fasi": self.fasi, "minuti": round(self.passate_min(adesso), 1),
                      "rimandati": self.rimandati})
        if nome:
            log.warning("GIRO %s fase %s (%.0f min dall'avvio, %.0f min di tetto rimasti)", self.nome, nome,
                        self.passate_min(adesso), self.resto_min(adesso))

    def tempo_per(self, fase: str, minuti: float) -> bool:
        """True se `minuti` stanno nel tetto del giro (meno la riserva per la fine); altrimenti la fase è rimandata."""
        if not giro_leggero():
            return True
        resto = self.resto_min() - float(os.environ.get("GIRO_RISERVA_MIN") or RISERVA_MIN)
        if minuti <= resto:
            return True
        self.rimandati.append(fase)
        log.warning("GIRO %s: %s rimandata (stima %.0f min, restano %.0f min nel tetto)", self.nome, fase, minuti, resto)
        return False

    def chiudi(self, ok: bool = True, **extra) -> None:
        if self.chiuso:
            return
        self.chiuso = True
        self.fase(None)
        if self.id is None:
            return
        try:
            from ..db import smetti
            smetti(self.id)
            dati = {"fasi": self.fasi, "minuti": round(self.passate_min(), 1), "tetto_ore": self.ore,
                    "rimandati": self.rimandati, **extra}
            with self.connetti() as c:
                c.execute("UPDATE job_runs SET finished_at=now(), ok=%s, stats=%s WHERE id=%s",
                          (ok, json.dumps(dati, default=str), self.id))
                c.commit()
            log.warning("GIRO %s finito in %.0f min: %s", self.nome, self.passate_min(), json.dumps(dati, default=str)[:600])
        except Exception as e:
            log.warning("GIRO %s: riga non chiusa (%s)", self.nome, type(e).__name__)


_GIRO: dict = {"g": None}


def giro_corrente() -> Giro | None:
    return _GIRO["g"]


def inizia_giro(nome: str, t0: float | None = None) -> Giro:
    g = Giro(nome, t0).apri()
    _GIRO["g"] = g
    return g


# ------------------------------------------------------------------ fotografia della configurazione del cron opportunità

CHIAVE_CONFIG = "config:opportunita"
SERVIZIO_FOTOGRAFIA = "dealfinder-opportunita"

# Segreti: elenco ESPLICITO dei nomi (tutti quelli letti nel codice e in render.yaml) + poche desinenze inequivocabili.
# Prima un filtro a parole ("KEY", "SALT", "PASS" ovunque nel nome) escludeva anche regole vere (PROBLEM_KEYWORDS,
# BENZ_SALTA_AUTOSCOUT, FILTRA_SALTA_MODELLO, LOMBARDIA_SALTA_SUBITO): la notte girava con regole diverse.
SEGRETI_NOMI = frozenset("""
    ADMIN_EMAIL ADMIN_PASSWORD ANTHROPIC_API_KEY APIFY_TOKEN APIFY_TOKENS APPLE_CLIENT_ID APPLE_KEY_ID APPLE_PRIVATE_KEY
    APPLE_TEAM_ID APPUNTAMENTI_EMAIL BDKEY BRAVE_API_KEY BREVO_API_KEY BRIGHTDATA_API_KEY BRIGHTDATA_NUOVA
    BRIGHTDATA_SEARCHES BRIGHTDATA_UNLOCKER_ZONE CARTESIA_API_KEY COMMERCIANTI_KEY DATABASE_URL ELEVENLABS_API_KEY
    ESPORTA_EMAIL GEMINI_API_KEY GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET META_PROVIDER_TOKEN OPENAI_API_KEY
    OPENAPI_PLATE_URL OPENAPI_TOKEN OUTSCRAPER_KEY PLATE_LOOKUP_KEY PLATE_LOOKUP_URL PLATE_SALT REGCHECK_PASSWORD
    REGCHECK_URL REGCHECK_USERNAME SCRAPER_PROXY SECRET_KEY SENTRY_DSN STRIPE_PUBLISHABLE_KEY STRIPE_SECRET_KEY
    STRIPE_WEBHOOK_SECRET TYPESAFE_API_KEY TYPESAFE_URL HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
""".split())
_SEGRETO_DESINENZA = re.compile(r"(_API_KEY|_SECRET_KEY|_PRIVATE_KEY|_SECRET|_TOKEN|_TOKENS|_PASSWORD|_PASSWD|_DSN|"
                                r"_WEBHOOK|_WEBHOOK_URL|_USERNAME|_CREDENTIALS?|_COOKIE)$")
# Valore che sembra un segreto anche sotto un nome innocuo: mai nel database
_SEGRETO_VALORE = re.compile(r"://[^/\s]*@"                                   # URL con utente/password
                             r"|^(sk|pk|rk|whsec|xkeysib|xsmtpsib|ghp|gho|glpat|AKIA)[-_]?[A-Za-z0-9]"  # chiavi note
                             r"|^ey[A-Za-z0-9_-]{20,}\."                       # JWT
                             r"|hook"                                           # webhook (Slack, Discord, Zapier...)
                             r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"  # email
                             r"|^[A-Za-z0-9+/_=-]{40,}$", re.I)                 # stringa lunga senza spazi (token)
_DI_PROCESSO = {"PATH", "HOME", "PWD", "OLDPWD", "HOSTNAME", "SHLVL", "_", "PORT", "LANG", "TERM", "USER", "SHELL",
                "COLLECT_MODO", "COLLECT_PRIMA", "COLLECT_SOLO_PRIMA", "CRON_T0", "MERCATO_NORD_FASE",
                "MERCATO_NOTTE_FASE", "MERCATO_LAVORI_FASE", "MERCATO_ENV_PRIMA", "NOTTE_IMPRONTA_ATTESA", "NOTTE_CONFIG", "TORINO_ORA",
                "NOVARA_ORA", "RICALCOLO_SUBITO", "PASSI_JOB", "COLLECT_JOB", "AUTOSCOUT_CAP", "AUTOSCOUT_RAGGIO",
                "BREVO_IMPORT_LISTE", "FOTOGRAFIA_SERVIZIO"}
_PREFISSI_PROCESSO = ("RENDER", "PYTHON", "LC_", "BENZ_INCR", "GIT_", "IS_PULL", "UV_", "PIP_", "VIRTUAL_ENV", "XDG_",
                      "SSL_", "REQUESTS_", "CURL_", "NODE_", "KUBERNETES", "OTEL_", "GUNICORN", "UVICORN")


def segreto(nome: str, valore=None) -> bool:
    """Il nome o il valore sono di un segreto (o di un dato personale: email)."""
    if nome in SEGRETI_NOMI or _SEGRETO_DESINENZA.search(nome):
        return True
    return valore is not None and bool(_SEGRETO_VALORE.search(str(valore).strip()))


def di_processo(nome: str) -> bool:
    return nome in _DI_PROCESSO or nome.startswith(_PREFISSI_PROCESSO)


def copiabile(nome: str, valore) -> bool:
    """Variabile che si copia dalla configurazione del cron opportunità alla notte (regole e impostazioni dei giri)."""
    if valore is None or di_processo(nome) or segreto(nome, valore):
        return False
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", nome)) and len(str(valore)) <= 2000


def variabili_da_copiare(env: dict) -> dict:
    """Le variabili del cron opportunità che decidono COSA leggere e COME valutare (ZC_*, TORINO_*, POLICY_*, SOGLIE_PV,
    PROBLEM_KEYWORDS, ...): tutte tranne i segreti (nome nell'elenco o valore da segreto) e quelle del processo."""
    return {k: str(v) for k, v in env.items() if copiabile(k, v)}


def impronta_regole(env=None) -> str | None:
    """Impronta delle regole (regole_log.impronta: variabili di regola + policy risolta) di questo ambiente, senza le
    variabili di processo (MERCATO_NOTTE_FASE, MERCATO_ENV_PRIMA... cominciano per MERCATO_, prefisso di regola)."""
    env = dict(os.environ if env is None else env)
    try:
        from .. import regole_log
        return regole_log.impronta({k: v for k, v in env.items() if not di_processo(k)})[0]
    except Exception:
        return None


def fotografia_ammessa(mode: str, env=None) -> bool:
    """La fotografia si salva SOLO dal servizio dealfinder-opportunita, nel giro normale: mai con COLLECT_SOLO_PRIMA,
    TORINO_ORA o NOVARA_ORA (giri lanciati a mano o con un altro lavoro)."""
    env = os.environ if env is None else env
    servizio = env.get("RENDER_SERVICE_NAME") or env.get("FOTOGRAFIA_SERVIZIO") or ""
    return (mode == "opportunita" and servizio == SERVIZIO_FOTOGRAFIA and env.get("COLLECT_SOLO_PRIMA") != "1"
            and env.get("TORINO_ORA") != "1" and env.get("NOVARA_ORA") != "1")


def salva_config(processo: str = "opportunita", connetti=None, env=None) -> int:
    """Fotografia delle variabili del cron opportunità + impronta delle regole (tabella tempo_reale, riga
    'config:opportunita'). Da chiamare all'avvio, prima di ogni cambio dell'ambiente."""
    env = dict(os.environ if env is None else env)
    dati = {"env": variabili_da_copiare(env), "impronta": impronta_regole(env), "versione": 2}
    try:
        with (connetti or _connetti)() as c:
            c.execute("INSERT INTO tempo_reale (giro, ultimo_at, stats) VALUES (%s, now(), %s) ON CONFLICT (giro) DO "
                      "UPDATE SET ultimo_at=now(), stats=EXCLUDED.stats", (CHIAVE_CONFIG, json.dumps(dati)))
            c.commit()
        return len(dati["env"])
    except Exception as e:
        log.warning("CONFIG %s non salvata: %s", processo, type(e).__name__)
        return -1


def carica_config(connetti=None, ore_max: float = 72) -> tuple[dict, str] | None:
    """(variabili, impronta) salvate dal cron opportunità al massimo `ore_max` ore fa; None se assente, vecchia o del
    formato di prima (senza impronta)."""
    try:
        with (connetti or _connetti)() as c:
            r = c.execute("SELECT stats, extract(epoch FROM now() - ultimo_at) / 3600.0 AS h FROM tempo_reale "
                          "WHERE giro=%s", (CHIAVE_CONFIG,)).fetchone()
        if not r or r["h"] is None or float(r["h"]) > ore_max:
            return None
        s = r["stats"]
        s = json.loads(s) if isinstance(s, str) else dict(s or {})
        if not isinstance(s.get("env"), dict) or not s.get("impronta"):
            return None
        return variabili_da_copiare(s["env"]), str(s["impronta"])   # difesa: anche in lettura mai segreti
    except Exception as e:
        log.warning("CONFIG non letta: %s", type(e).__name__)
        return None


def ambiente_notte(env_mercato: dict, cfg: dict) -> dict:
    """Ambiente del processo notturno, dato PRIMA dell'avvio (execve, quindi prima di ogni import): segreti e variabili
    di processo del cron mercato + TUTTE e sole le variabili copiabili del cron opportunità (quelle che ha solo il
    mercato si tolgono). In MERCATO_ENV_PRIMA le variabili copiabili del mercato, per tornare al suo ambiente dopo."""
    out = {k: v for k, v in env_mercato.items() if not copiabile(k, v)}
    out.update(cfg)
    out["MERCATO_ENV_PRIMA"] = json.dumps(variabili_da_copiare(env_mercato))
    return out


def ambiente_mercato(env_notte: dict) -> dict:
    """L'ambiente del cron mercato di nuovo (per mercato_nord, il modello AI e i lavori in coda)."""
    try:
        prima = json.loads(env_notte.get("MERCATO_ENV_PRIMA") or "{}")
    except ValueError:
        prima = {}
    out = {k: v for k, v in env_notte.items() if not copiabile(k, v) and k not in ("MERCATO_ENV_PRIMA",
                                                                                     "NOTTE_IMPRONTA_ATTESA")}
    out.update(variabili_da_copiare(prima))
    return out


def applica_ambiente(nuovo: dict) -> None:
    os.environ.clear()
    os.environ.update(nuovo)


# ------------------------------------------------------------------ modello AI dei prezzi dentro il tetto del giro

def ai_prezzi_nel_giro(giro: Giro) -> None:
    """Ultimo passo del giro opportunità (correzione del giudice: ai_prezzi contato nel tetto). Il processo della
    raccolta è SOSTITUITO (os.execv, memoria pulita come prima) da un piccolo processo che lancia il modello AI dei
    prezzi con un tempo massimo = fine del tetto del giro e poi chiude la riga giro:opportunita con la sua durata.
    Non ritorna se riesce."""
    import sys
    if os.environ.get("AI_PREZZI", "1") != "1" or giro.id is None:
        return
    from ..db import smetti
    giro.fase("ai_prezzi")
    smetti(giro.id)
    scadenza = giro.t0 + giro.ore * 3600
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, "-m", "dealfinder.pipeline.turni", "ai_prezzi", str(giro.id),
                              str(scadenza)])


def _ai_prezzi_con_scadenza(giro_id: int, scadenza: float, connetti=None) -> dict:
    import subprocess
    import sys
    from ..db import batti, smetti
    cn = connetti or _connetti
    batti(giro_id)
    t0 = time.time()
    esito: dict = {}
    try:
        rc = subprocess.run([sys.executable, "-m", "dealfinder.pricing.ai_prezzi"],
                            timeout=max(60.0, scadenza - time.time())).returncode
        esito = {"codice_uscita": rc}
    except subprocess.TimeoutExpired:
        esito = {"fermato": "fine del tetto del giro"}
        log.warning("GIRO ai_prezzi fermato alla fine del tetto del giro")
    except Exception as e:
        esito = {"errore": type(e).__name__}
    secondi = round(time.time() - t0)
    smetti(giro_id)
    try:
        with cn() as c:
            c.execute("INSERT INTO job_runs (job, started_at, finished_at, ok, stats) VALUES ('turno:ai_prezzi', "
                      "now() - make_interval(secs => %s), now(), %s, %s)",
                      (secondi, "errore" not in esito and "fermato" not in esito, json.dumps(esito)))
            c.execute("UPDATE job_runs SET finished_at = now(), ok = true, stats = COALESCE(stats, '{}'::jsonb) || "
                      "jsonb_build_object('fase', NULL::text, 'ai_prezzi', %s::jsonb, 'minuti', round((extract(epoch FROM "
                      "now() - started_at) / 60.0)::numeric, 1)) || jsonb_build_object('fasi', COALESCE(stats->'fasi', "
                      "'{}'::jsonb) || jsonb_build_object('ai_prezzi', %s::int)) WHERE id = %s",
                      (json.dumps(esito), secondi, giro_id))
            c.commit()
    except Exception as e:
        log.warning("GIRO riga non chiusa dopo ai_prezzi: %s", type(e).__name__)
    log.warning("GIRO ai_prezzi in %d min: %s", secondi // 60, esito)
    return esito


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if len(sys.argv) >= 4 and sys.argv[1] == "ai_prezzi":
        _ai_prezzi_con_scadenza(int(sys.argv[2]), float(sys.argv[3]))
