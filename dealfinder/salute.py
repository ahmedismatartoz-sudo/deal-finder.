"""Salute giornaliera (19/11, automatismi): una volta all'ora guarda se il sito ha perso auto rispetto ai giorni
scorsi, cerca un motivo anomalo e, se serve, fa da solo le TRE azioni innocue previste. Riusa la sentinella
(storico sentinella_stato, imbuto sito_funnel_giorno, allarmi con dedupe ed email): qui non c'è nessun invio.

    esegui()                 dal thread della sentinella del sito (sentinella._ciclo_web); non solleva mai
    controlla(conn, adesso)  solo lettura: numeri, calo, motivi anomali, allarmi da registrare
    azioni(elenco, ...)      esegue A1/A2/A3

Calo: auto di adesso < 85% della mediana dei 7 giorni precedenti (servono almeno 3 giorni e una mediana >= 20).
Il valore di un giorno è la mediana delle ore 9-21 (ora di Roma) di sentinella_stato.n_sito, salvata in salute_giorno
(migrazione 064, creata anche al primo uso e riempita dallo storico della sentinella).
Motivi anomali: allarmi dell'imbuto (sentinella.c_imbuto), auto con sconto alto mai controllate ferme da più di 2 ore
(controllo_auto.stato_coda), spesa del controllo sopra 5 $ nel giorno (solo avviso: NON è un tetto).

Azioni (solo queste; nessuna scrittura su prezzi, soglie, regole o stati fuori dal percorso normale della valutazione):
  A1 ricalcolo dell'elenco del sito;
  A2 rivalutazione dei valori scaduti (più di 48 ore o regole diverse) delle auto sul sito, a blocchi, al massimo 600;
     saltata se il calcolo grande è in corso o la memoria è alta;
  A3 richiesta di una notte di confronti mirati (confronti_mirati.richiedi_notte).
Un giro di azioni al massimo ogni 6 ore e 3 al giorno, un processo alla volta (lucchetto 734141).
Quali azioni: calo = A1, A2, A3 in ordine; imbuto anomalo senza calo = A1 e A2; coda del controllo ferma o spesa
alta = solo avviso.

Avvisi: sentinella.registra_allarmi (chiavi "salute:calo", "salute:motivo:<passo>") e sentinella.invia_coda (email
solo tra le 8 e le 22 di Roma, di notte in coda). L'allarme dell'imbuto si ripete qui solo insieme a un calo (da
solo lo dà già la sentinella). Testi e log: solo conteggi.

Interruttori senza deploy (dealfinder/automatismi.py): "salute" (spento = non fa niente), "salute_azioni" (spento =
solo avvisi).
"""
from __future__ import annotations

import json
import logging
import statistics
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from . import automatismi, memoria
from . import sentinella as S

log = logging.getLogger("salute")

CALO = 0.85                     # calo: n di adesso < mediana dei 7 giorni x 0,85
MEDIANA_MIN = 20                # sotto queste auto di mediana il confronto non ha senso
GIORNI_MIN = 3                  # giorni di storico minimi
GIORNI = 7
ORA_DA, ORA_A = 9, 21           # ore di Roma (comprese) che fanno il valore del giorno
STORICO_GIORNI = 10             # quanto storico della sentinella si rilegge a ogni giro
OGNI_MIN = 55                   # un giro all'ora (anche con due processi: prenotazione nel database)
CODA_FERMA_MIN = 120            # auto mai controllate ferme da più di 2 ore
SPESA_AVVISO_USD = 5.0          # spesa del controllo nel giorno: solo avviso
AZIONI_OGNI_ORE = 6
AZIONI_GIORNO_MAX = 3
RIVALUTA_MAX = 600
RIVALUTA_ORE = 48
RIVALUTA_MINUTI_MAX = 20
MEMORIA_QUOTA = 0.6
LOCK = 734141
TIMEOUT_S = 60
A1, A2, A3 = "elenco", "rivaluta_scaduti", "confronti_notte"

TABELLA_SQL = """/*H:tabella*/ CREATE TABLE IF NOT EXISTS salute_giorno (
    giorno DATE PRIMARY KEY, n_sito INTEGER, n_privati INTEGER, n_commercianti INTEGER,
    motivi JSONB NOT NULL DEFAULT '[]', azioni JSONB NOT NULL DEFAULT '[]',
    aggiornato TIMESTAMPTZ NOT NULL DEFAULT now())"""

# un solo processo per ora: la riga di oggi si prenota spostando `aggiornato` (se è già recente non torna niente)
PRENOTA_SQL = """/*H:prenota*/ INSERT INTO salute_giorno (giorno, aggiornato) VALUES (%s, %s)
ON CONFLICT (giorno) DO UPDATE SET aggiornato = EXCLUDED.aggiornato WHERE salute_giorno.aggiornato < %s
RETURNING giorno"""

GIORNO_SALVA_SQL = """/*H:giorno_salva*/ INSERT INTO salute_giorno (giorno, n_sito, n_privati, n_commercianti, aggiornato)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (giorno) DO UPDATE SET n_sito = EXCLUDED.n_sito, n_privati = EXCLUDED.n_privati,
    n_commercianti = EXCLUDED.n_commercianti
WHERE (salute_giorno.n_sito, salute_giorno.n_privati, salute_giorno.n_commercianti)
      IS DISTINCT FROM (EXCLUDED.n_sito, EXCLUDED.n_privati, EXCLUDED.n_commercianti)"""

# auto sul sito con la valutazione scaduta: le più vecchie prima (taglio a 600), poi in ordine di gruppo
SCADUTI_SQL = """/*H:scaduti*/ SELECT id, make, model, fuel FROM (
    SELECT l.id, l.make, l.model, l.fuel, vp.computed_at
    FROM listings l JOIN valutazioni_policy vp ON vp.listing_id = l.id AND vp.policy_version = %(pv)s
    WHERE l.status = 'attivo' AND l.stage = 'approfondito' AND l.make IS NOT NULL AND l.model IS NOT NULL
      AND l.fuel IS NOT NULL AND l.price_eur IS NOT NULL
      AND (vp.computed_at < now() - make_interval(hours => %(ore)s) OR vp.impronta_regole IS DISTINCT FROM %(imp)s)
    ORDER BY vp.computed_at LIMIT %(max)s) s
ORDER BY lower(make), lower(model), fuel, id"""

_stato = {"azioni_in_corso": False}


# ---------------------------------------------------------------------------------------------------------------
# Storico: valore del giorno = mediana delle ore 9-21 di Roma
# ---------------------------------------------------------------------------------------------------------------
def assicura_tabella(conn) -> None:
    conn.execute(TABELLA_SQL)


def _giorno(t: datetime):
    return S.ora_roma(t).date()


def _mediana(valori: list) -> int | None:
    v = [float(x) for x in valori if x is not None]
    return int(round(statistics.median(v))) if v else None


def valori_giorno(righe: list[dict]) -> dict:
    """Righe di sentinella_stato (ora, n_sito, n_privati, n_commercianti) -> {giorno di Roma: {n_sito, n_privati,
    n_commercianti}} con la mediana delle sole ore 9-21 di Roma."""
    per: dict = {}
    for r in righe:
        ora = r.get("ora")
        if ora is None or r.get("n_sito") is None:
            continue
        roma = S.ora_roma(ora)
        if ORA_DA <= roma.hour <= ORA_A:
            per.setdefault(roma.date(), []).append(r)
    return {g: {k: _mediana([r.get(k) for r in rr]) for k in ("n_sito", "n_privati", "n_commercianti")}
            for g, rr in per.items()}


def aggiorna_storico(conn, adesso: datetime) -> int:
    """Scrive in salute_giorno il valore di ogni giorno dello storico della sentinella (ultimi 10 giorni: al primo
    avvio riempie i giorni passati, poi tiene aggiornato oggi). Tocca solo i numeri, mai motivi e azioni."""
    if not S._esiste(conn, "sentinella_stato"):
        return 0
    righe = S._righe(conn, "/*H:stato*/ SELECT ora, n_sito, n_privati, n_commercianti FROM sentinella_stato "
                           "WHERE ora > %s AND n_sito IS NOT NULL ORDER BY ora",
                     (adesso - timedelta(days=STORICO_GIORNI),))
    giorni = valori_giorno(righe)
    for g, v in sorted(giorni.items()):
        conn.execute(GIORNO_SALVA_SQL, (g, v["n_sito"], v["n_privati"], v["n_commercianti"], adesso))
    return len(giorni)


# ---------------------------------------------------------------------------------------------------------------
# Controllo (solo lettura)
# ---------------------------------------------------------------------------------------------------------------
def _lista(v) -> list:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return list(v) if isinstance(v, list) else []


def stato_coda() -> dict | None:
    """controllo_auto.stato_coda() se c'è (lo fornisce il controllo AI di tutte le auto); None se manca o non
    riesce: il controllo della coda si salta."""
    try:
        from .ai import controllo_auto
        fn = getattr(controllo_auto, "stato_coda", None)
        if fn is None:
            log.warning("SALUTE coda del controllo non letta: stato_coda assente")
            return None
        d = fn()
        return d if isinstance(d, dict) else None
    except Exception as e:
        log.warning("SALUTE coda del controllo non letta: %s", type(e).__name__)
        return None


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def motivi_anomali(conn, coda=None) -> list[dict]:
    """Motivi anomali di adesso: [{passo, tipo, testo, ...}]. Ogni fonte che non si legge si salta."""
    out: list[dict] = []
    try:
        for a in S.c_imbuto(conn, {}).get("allarmi") or []:
            d = a.get("dati") or {}
            passo = str(d.get("passo") or a["chiave"].split(":", 1)[-1])
            out.append({"passo": passo, "tipo": "imbuto", "quota": d.get("quota"), "mediana": d.get("mediana"),
                        "testo": f"Salute: il filtro '{passo}' del sito oggi toglie il {_f(d.get('quota')):.0%} "
                                 f"delle auto (mediana dei 7 giorni {_f(d.get('mediana')):.0%})"})
    except Exception as e:
        log.warning("SALUTE imbuto non letto: %s", type(e).__name__)
        try:
            conn.rollback()
        except Exception:
            pass
    c = (coda or stato_coda)() if callable(coda or stato_coda) else None
    if isinstance(c, dict):
        mai, eta, usd = int(_f(c.get("mai_controllate"))), _f(c.get("eta_coda_min")), _f(c.get("usd_oggi"))
        if mai > 0 and eta > CODA_FERMA_MIN:
            out.append({"passo": "controllo_fermo", "tipo": "coda", "mai_controllate": mai, "minuti": int(eta),
                        "testo": f"Salute: {mai} auto con sconto alto mai controllate, coda ferma da {int(eta)} "
                                 f"minuti (su {int(_f(c.get('totale_sconto_alto')))} da controllare)"})
        if usd > SPESA_AVVISO_USD:
            out.append({"passo": "spesa_controllo", "tipo": "spesa", "usd": round(usd, 2),
                        "testo": f"Salute: spesa del controllo delle auto oggi {usd:.2f} $ (avviso oltre "
                                 f"{SPESA_AVVISO_USD:g} $ al giorno, nessun tetto)"})
    return out


def controlla(conn, adesso: datetime, coda=None) -> dict:
    """Solo lettura. Ritorna {giorno, n, mediana_7g, giorni, calo, calo_pct, motivi, allarmi, righe}: `allarmi` sono
    quelli da passare a sentinella.registra_allarmi, `righe` le righe di salute_giorno lette (per i limiti)."""
    oggi = _giorno(adesso)
    righe = S._righe(conn, "/*H:giorni*/ SELECT giorno, n_sito, azioni FROM salute_giorno WHERE giorno >= %s "
                           "ORDER BY giorno", (oggi - timedelta(days=GIORNI + 1),))
    per = {r["giorno"]: r for r in righe}
    storico = [float(r["n_sito"]) for g, r in per.items()
               if oggi - timedelta(days=GIORNI) <= g < oggi and r.get("n_sito") is not None]
    med = statistics.median(storico) if len(storico) >= GIORNI_MIN else None
    # n di adesso: l'ultima ora della sentinella se è un'ora di giorno (9-21), altrimenti il valore di oggi
    n = None
    u = S._una(conn, "/*H:ultimo*/ SELECT ora, n_sito FROM sentinella_stato WHERE n_sito IS NOT NULL AND ora > %s "
                     "ORDER BY ora DESC LIMIT 1", (adesso - timedelta(hours=3),)) if S._esiste(
        conn, "sentinella_stato") else None
    if u and u.get("ora") is not None and ORA_DA <= S.ora_roma(u["ora"]).hour <= ORA_A:
        n = int(u["n_sito"])
    elif (per.get(oggi) or {}).get("n_sito") is not None:
        n = int(per[oggi]["n_sito"])
    out = {"giorno": str(oggi), "n": n, "mediana_7g": med, "giorni": len(storico), "calo": False, "calo_pct": None,
           "motivi": [], "allarmi": [], "righe": righe}
    if n is not None and med:
        out["calo_pct"] = round((1 - n / med) * 100, 1)
        out["calo"] = bool(med >= MEDIANA_MIN and n < med * CALO)
    out["motivi"] = motivi_anomali(conn, coda)
    if out["calo"]:
        out["allarmi"].append(S._allarme(
            "salute:calo", f"Salute: sul sito {n} auto, mediana dei {GIORNI} giorni {med:g} (calo "
                           f"{out['calo_pct']:.0f}%, soglia {(1 - CALO) * 100:.0f}%)",
            n=n, mediana=med, calo_pct=out["calo_pct"]))
    for m in out["motivi"]:
        if m["tipo"] == "imbuto" and not out["calo"]:
            continue                                    # da solo lo dà già la sentinella (imbuto:<passo>)
        out["allarmi"].append(S._allarme(f"salute:motivo:{m['passo']}", m["testo"],
                                         **{k: v for k, v in m.items() if k not in ("testo", "passo")}))
    for a in out["allarmi"]:
        a["controllo"] = "salute"
    return out


# ---------------------------------------------------------------------------------------------------------------
# Azioni
# ---------------------------------------------------------------------------------------------------------------
def azioni_per(calo: bool, motivi: list[dict]) -> list[str]:
    """Quali azioni per quale anomalia (in ordine)."""
    if calo:
        return [A1, A2, A3]
    if any(m.get("tipo") == "imbuto" for m in motivi):
        return [A1, A2]
    return []


def limite_azioni(righe: list[dict], adesso: datetime) -> str | None:
    """None se un giro di azioni si può fare; altrimenti il motivo ('presto': meno di 6 ore dall'ultimo;
    'tetto_giorno': già 3 oggi). Legge i giri segnati in salute_giorno.azioni (oggi e ieri)."""
    oggi = _giorno(adesso)
    n_oggi, ultimo = 0, None
    for r in righe:
        for g in _lista(r.get("azioni")):
            try:
                at = datetime.fromisoformat(str(g.get("at")))
            except (TypeError, ValueError, AttributeError):
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if _giorno(at) == oggi:
                n_oggi += 1
            ultimo = at if ultimo is None or at > ultimo else ultimo
    if n_oggi >= AZIONI_GIORNO_MAX:
        return "tetto_giorno"
    if ultimo is not None and adesso - ultimo < timedelta(hours=AZIONI_OGNI_ORE):
        return "presto"
    return None


def _app():
    return sys.modules.get("dealfinder.web.app")


def _log_azione(azione: str, auto: int, t0: float, esito: str) -> dict:
    r = {"azione": azione, "auto": int(auto), "secondi": round(time.monotonic() - t0, 1), "esito": esito}
    log.warning("SALUTE_AZIONE %s", json.dumps(r, ensure_ascii=False))
    return r


def a1_elenco() -> dict:
    """A1: ricalcolo dell'elenco del sito (solo nel processo del sito)."""
    t0 = time.monotonic()
    m = _app()
    el, fn = getattr(m, "_elenco", None), getattr(m, "_aggiorna_elenco", None)
    if not isinstance(el, dict) or fn is None:
        return _log_azione(A1, 0, t0, "saltata:non_nel_sito")
    try:
        el["t"] = 0.0
        fn(attendi=False, motivo="salute")
        return _log_azione(A1, len(el.get("items") or []), t0, "ok")
    except Exception as e:
        return _log_azione(A1, 0, t0, "errore:" + type(e).__name__)


def _salta_rivaluta() -> str | None:
    c = getattr(_app(), "_calcolo", None)
    if isinstance(c, dict) and c.get("in_corso"):
        return "calcolo_in_corso"
    if memoria.alta(MEMORIA_QUOTA):
        return "memoria_alta"
    return None


def a2_rivaluta(connetti=None, ves=None) -> dict:
    """A2: rivaluta (percorso normale: valuta_e_scrivi, pubblica=True) le auto sul sito con la valutazione più
    vecchia di 48 ore o fatta con regole diverse; a blocchi di gruppi interi, al massimo 600 auto e 20 minuti.
    Saltata (anche a metà) se il calcolo grande è in corso o la memoria è alta."""
    t0 = time.monotonic()
    fatte = 0
    try:
        salta = _salta_rivaluta()
        if salta:
            return _log_azione(A2, 0, t0, "saltata:" + salta)
        from .pricing import policy_v2 as P
        from .pricing import valore_base as VB
        from .pricing.valutazione import impronta_regole
        if ves is None:
            from .pricing.valutazione import valuta_e_scrivi as ves
        if connetti is None:
            from .db import connect as connetti
        tetto = int(max(0, min(RIVALUTA_MAX, automatismi.numero("salute_rivaluta_max", RIVALUTA_MAX))))
        if not tetto:
            return _log_azione(A2, 0, t0, "saltata:tetto_zero")
        with connetti() as conn:
            righe = [dict(r) for r in conn.execute(SCADUTI_SQL, {"pv": P.POLICY_VERSION, "ore": RIVALUTA_ORE,
                                                                 "imp": impronta_regole(), "max": tetto}).fetchall()]
            conn.commit()
        righe = righe[:tetto]
        esito, errori = "ok", 0
        for pezzo in VB._blocchi(righe):
            salta = _salta_rivaluta()
            if salta:
                esito = "fermata:" + salta
                break
            if time.monotonic() - t0 > RIVALUTA_MINUTI_MAX * 60:
                esito = "fermata:tempo"
                break
            try:
                with connetti() as conn:
                    ves(conn, pezzo, "salute", pubblica=True, pausa_gruppo=VB.PAUSA_GRUPPO_S)
                    conn.commit()
                fatte += len(pezzo)
            except Exception as e:
                errori += 1
                log.warning("SALUTE rivalutazione: blocco di %d auto non riuscito: %s", len(pezzo), type(e).__name__)
        if errori and esito == "ok":
            esito = f"ok_con_errori:{errori}"
        return _log_azione(A2, fatte, t0, esito)
    except Exception as e:
        return _log_azione(A2, fatte, t0, "errore:" + type(e).__name__)


def a3_confronti(connetti=None) -> dict:
    """A3: chiede una notte di confronti mirati (la fa il cron del mercato). Se la funzione non c'è si salta."""
    t0 = time.monotonic()
    try:
        from .pricing import confronti_mirati
        fn = getattr(confronti_mirati, "richiedi_notte", None)
        if fn is None:
            return _log_azione(A3, 0, t0, "saltata:richiedi_notte_assente")
        if connetti is None:
            from .db import connect as connetti
        with connetti() as conn:
            ok = bool(fn(conn, "salute"))
            conn.commit()
        return _log_azione(A3, 0, t0, "richiesta" if ok else "non_richiesta")
    except Exception as e:
        return _log_azione(A3, 0, t0, "errore:" + type(e).__name__)


def azioni(elenco: list[str], connetti=None, ves=None) -> list[dict]:
    """Esegue le azioni chieste (solo A1, A2, A3, in quest'ordine) con il lucchetto 734141: se un altro processo le
    sta facendo non si fa niente. Non solleva mai."""
    elenco = [a for a in (A1, A2, A3) if a in (elenco or [])]
    if not elenco:
        return []
    if _stato["azioni_in_corso"]:
        return [{"azione": "tutte", "auto": 0, "secondi": 0, "esito": "saltata:gia_in_corso"}]
    _stato["azioni_in_corso"] = True
    out: list[dict] = []
    try:
        if connetti is None:
            from .db import connect as connetti
        with connetti() as lucchetto:
            r = lucchetto.execute("/*H:lucchetto*/ SELECT pg_try_advisory_lock(%s) AS ok", (LOCK,)).fetchone()
            if not (r and r["ok"]):
                lucchetto.rollback()
                return [_log_azione("tutte", 0, time.monotonic(), "saltata:altro_processo")]
            try:
                lucchetto.commit()
                for a in elenco:
                    out.append(a1_elenco() if a == A1 else a2_rivaluta(connetti, ves) if a == A2
                               else a3_confronti(connetti))
            finally:
                try:
                    lucchetto.rollback()
                    lucchetto.execute("/*H:sblocca*/ SELECT pg_advisory_unlock(%s)", (LOCK,))
                    lucchetto.commit()
                except Exception:
                    pass
    except Exception as e:
        out.append({"azione": "tutte", "auto": 0, "secondi": 0, "esito": "errore:" + type(e).__name__})
    finally:
        _stato["azioni_in_corso"] = False
    return out


def _segna_esiti(connetti, giorno, giro: dict, esiti: list[dict]) -> None:
    try:
        with connetti() as conn:
            conn.execute("/*H:azioni_esito*/ UPDATE salute_giorno SET azioni = jsonb_set(azioni, '{-1}', %s::jsonb) "
                         "WHERE giorno = %s AND jsonb_array_length(azioni) > 0",
                         (json.dumps(dict(giro, esiti=esiti), default=str), giorno))
            conn.commit()
    except Exception as e:
        log.warning("SALUTE esiti delle azioni non salvati: %s", type(e).__name__)


def _fai_azioni(connetti, giorno, giro: dict, ves=None) -> list[dict]:
    esiti = azioni(giro["azioni"], connetti, ves)
    _segna_esiti(connetti, giorno, giro, esiti)
    return esiti


# ---------------------------------------------------------------------------------------------------------------
# Il giro (una volta all'ora, dal thread della sentinella del sito)
# ---------------------------------------------------------------------------------------------------------------
def esegui(connetti=None, invia=None, adesso: datetime | None = None, coda=None, ves=None,
           sottofondo: bool | None = None) -> dict:
    """Un giro: storico, controllo, allarmi (registra_allarmi + invia_coda della sentinella) e, se serve e si può,
    le azioni. Chiamata ogni 10 minuti, lavora una volta all'ora (prenotazione nel database: vale per tutti i
    processi). `sottofondo` (default: sì quando la chiama il sito) = le azioni girano in un thread a parte, così il
    thread della sentinella non aspetta la rivalutazione. Non solleva mai."""
    try:
        if not automatismi.attivo("salute"):
            return {"spento": True}
    except Exception:
        return {"spento": True}
    t0 = time.monotonic()
    adesso = adesso or datetime.now(timezone.utc)
    if sottofondo is None:
        sottofondo = connetti is None
    riga: dict = {"giorno": str(_giorno(adesso)), "n": None, "mediana_7g": None, "calo_pct": None,
                  "motivi_anomali": [], "azioni": [], "esito": "ok"}
    giro = None
    try:
        if connetti is None:
            from .db import connect as connetti
        oggi = _giorno(adesso)
        with connetti() as conn:
            conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)
            S.assicura_tabelle(conn)
            assicura_tabella(conn)
            preso = conn.execute(PRENOTA_SQL, (oggi, adesso, adesso - timedelta(minutes=OGNI_MIN))).fetchone()
            conn.commit()
            if not preso:
                return {"saltato": True}
            aggiorna_storico(conn, adesso)
            conn.commit()
            c = controlla(conn, adesso, coda)
            conn.rollback()
            riga.update(n=c["n"], mediana_7g=c["mediana_7g"], calo_pct=c["calo_pct"],
                        motivi_anomali=[m["passo"] for m in c["motivi"]])
            motivi = [{k: v for k, v in m.items() if k != "testo"} for m in c["motivi"]]
            if c["calo"]:
                motivi.insert(0, {"passo": "calo", "tipo": "calo", "n": c["n"], "mediana": c["mediana_7g"]})
            conn.execute("/*H:motivi_salva*/ UPDATE salute_giorno SET motivi = %s::jsonb WHERE giorno = %s",
                         (json.dumps(motivi, default=str), oggi))
            nuovi, _ = S.registra_allarmi(conn, c["allarmi"], adesso)
            riga["allarmi_nuovi"] = len(nuovi)
            da_fare = azioni_per(c["calo"], c["motivi"])
            if da_fare:
                if not automatismi.attivo("salute_azioni"):
                    riga["esito"] = "solo_avvisi"
                else:
                    limite = limite_azioni(c["righe"], adesso)
                    if limite:
                        riga["esito"] = "azioni_rimandate:" + limite
                    else:
                        giro = {"at": adesso.isoformat(), "azioni": da_fare}
                        conn.execute("/*H:azioni_prenota*/ UPDATE salute_giorno SET azioni = azioni || %s::jsonb "
                                     "WHERE giorno = %s", (json.dumps([giro]), oggi))
            conn.commit()
            riga["email"] = S.invia_coda(conn, adesso, invia)
            conn.commit()
        if giro:
            if sottofondo:
                threading.Thread(target=_fai_azioni, args=(connetti, oggi, giro, ves), daemon=True,
                                 name="salute-azioni").start()
                riga["azioni"], riga["esito"] = list(giro["azioni"]), "azioni_avviate"
            else:
                riga["azioni"] = _fai_azioni(connetti, oggi, giro, ves)
                riga["esito"] = "azioni_fatte"
    except Exception as e:
        riga["esito"] = "errore:" + type(e).__name__
    riga["secondi"] = round(time.monotonic() - t0, 1)
    try:
        log.warning("SALUTE %s", json.dumps(riga, ensure_ascii=False, default=str))
    except Exception:
        pass
    return riga
