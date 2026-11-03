"""Sentinella: controlli automatici delle regole del cliente e della salute dei lavori (3/11, piano di ricostruzione).

Ogni giro (una volta per giro di cron in pipeline/collect.py, una volta all'ora dal thread leggero del sito) esegue
dieci controlli SOLO IN LETTURA sul database, ognuno con statement_timeout 60 s e query piccole, e scrive solo
nelle sue tabelle (migrazione 034_sentinella.sql):

  1 sito_conteggio      auto sul sito ora contro la stessa ora di ieri: calo > 20% = allarme (sentinella_stato,
                        una riga per ora); anche fotografia del sito vecchia (> 3 ore: il sito non la scrive più)
  2 sotto_soglia        auto sul sito (affari e incidentate) con margine < scovo.soglia(prezzo)
  3 regole_cliente      sul sito: Roma, Loconte (SITO_COMMERCIANTI_NOMI), oltre 150.000 km, senza telefono
  4 tetto_commercianti  commercianti sul sito oltre i 40
  5 impronte            impronta delle regole di PREZZO diversa tra sito e cron (tabella config_impronte)
  6 cron                ultimo giro riuscito più vecchio del previsto (ATTESE_ORE), fallimenti nelle 24 ore,
                        lavori "in corso" da più di 8 ore
  7 valutazioni         quota delle auto del sito con valutazioni_policy più vecchia di 48 ore (o assente)
  8 jev                 quota delle auto del sito senza decisione di Jev; Jev fermo (0 decisioni in 24 ore)
  9 pagamenti           errori dei webhook Stripe e email degli appuntamenti non partite (se le tabelle esistono)
 10 imbuto              sito_funnel_giorno (se esiste): un passo che toglie più del doppio della sua quota mediana
                        dei 7 giorni prima

Uscita: una riga di log `SENTINELLA {json}` per giro. Gli allarmi vanno in sentinella_allarmi; lo stesso allarme
(stessa chiave) al massimo una volta ogni 6 ore. Le email (Brevo transazionale, gli stessi helper di
_avvisa_appuntamento) partono solo tra le 8 e le 22 ora di Roma con i soli allarmi NUOVI; di notte restano in coda e
alle 8 parte un solo riepilogo.

Per gli altri gruppi:
  - sito:   sentinella.salva_istantanea(items) alla fine di web/app._calcola_elenco (dopo _elenco.update)
  - tutti:  sentinella.salva_impronta(processo) all'avvio, accanto a regole_log.registra(processo)
Nessuna funzione di questo modulo solleva eccezioni verso chi la chiama: al più una riga di log.
Nessun segreto nei log o nelle email: solo numeri, id degli annunci e nomi/valori delle regole (già senza segreti
in regole_log).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import statistics
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("sentinella")

# ---------------------------------------------------------------------------------------------------------------
# Impostazioni (nel codice, non su Render: Render è al limite delle 300 variabili). SENTINELLA=0 la spegne.
# ---------------------------------------------------------------------------------------------------------------
ATTESE_ORE = {                      # lavoro -> ore massime dall'ultimo giro riuscito
    "collect:opportunita": 8,       # cron ogni 3 ore
    "collect:mercato": 30,          # cron ogni notte
}
FALLIMENTI_MIN = 2                  # fallimenti dello stesso lavoro nelle 24 ore per dare l'allarme
IN_CORSO_ORE = 8                    # righe job_runs aperte da più di tante ore = processo morto o bloccato
CALO_SITO_MAX = 0.20                # calo delle auto sul sito rispetto a ieri alla stessa ora
CALO_SITO_MIN_IERI = 20             # sotto queste auto ieri il confronto non ha senso
ISTANTANEA_VECCHIA_ORE = 3
COMMERCIANTI_TETTO = 40
KM_SITO_DEFAULT = 150000
VALUTAZIONI_ORE = 48
VALUTAZIONI_VECCHIE_MAX = 0.25
JEV_SENZA_MAX = 0.50
MIN_AUTO_QUOTE = 10                 # sotto queste auto sul sito le quote (valutazioni, Jev) non danno allarme
IMBUTO_FATTORE = 2.0
IMBUTO_QUOTA_MIN = 0.10             # un passo che toglie meno del 10% non è mai un allarme
IMBUTO_ENTRATI_MIN = 20
IMBUTO_GIORNI_MIN = 3               # giorni di storico minimi per la mediana
DEDUPE_ORE = 6
GIORNO_DA, GIORNO_A = 8, 22         # ora di Roma in cui le email partono
EMAIL = "info@skovo.app"
TIMEOUT_S = 60
BUDGET_S = 240                      # tempo massimo di un giro: i controlli restanti si saltano
ESEMPI = 10                         # id di esempio per allarme

# Regole di PREZZO per il controllo 5 (prefissi dei nomi in regole_log.valori). Esclusi i nomi che dicono solo
# DOVE o QUANDO si ricalcola (legittimamente diversi tra sito e cron).
PREFISSI_PREZZI = ("POLICY_", "SOGLIE_PV", "RIVENDITA_", "CALIBRA_", "TARATURA_", "MARGINE_", "CORR_", "CONFRONTI_MIN",
                   "MERCATO_", "SITO_MARGINE_", "SITO_CONFRONTI_", "SENZA_NUMERO_", "BASSO_SOLO_TELEFONO", "AI_RIALZO_",
                   "ALTA_MARGINE_", "VR_P25_TETTO", "VR_SIGMA_MAX", "VR_GEMELLE_MIN", "VR_ACCORDO", "VR_SENZA_GEMELLE")
ESCLUSE_PREZZI = {"POLICY_RICALCOLO", "POLICY_RICALCOLO_SITO", "POLICY_ORE", "POLICY_RITARDO_S", "POLICY_LOG_SOSPETTE",
                  "POLICY_CAMPIONE"}
INTERRUTTORI = ("JEV_MODO", "TELEFONO_OBBLIGATORIO", "SITO_SOLO_PRIVATI", "KM_MASSIMO_SITO", "SITO_COMMERCIANTI_NOMI",
                "FB_SENZA_NUMERO")

MIGRAZIONE = Path(__file__).resolve().parent.parent / "db" / "migrations" / "034_sentinella.sql"
_NOME_SQL = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


# ---------------------------------------------------------------------------------------------------------------
# Piccoli aiuti
# ---------------------------------------------------------------------------------------------------------------
def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def ora_roma(t: datetime) -> datetime:
    """Ora di Roma; senza il database dei fusi orari, la regola europea (ultima domenica di marzo/ottobre, 01 UTC)."""
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo("Europe/Rome"))
    except Exception:
        def ultima_domenica(anno, mese):
            d = datetime(anno, mese, 31, 1, tzinfo=timezone.utc)
            return d - timedelta(days=(d.weekday() + 1) % 7)
        u = t.astimezone(timezone.utc)
        estate = ultima_domenica(u.year, 3) <= u < ultima_domenica(u.year, 10)
        return u.astimezone(timezone(timedelta(hours=2 if estate else 1)))


def di_giorno(t: datetime) -> bool:
    return GIORNO_DA <= ora_roma(t).hour < GIORNO_A


def _righe(conn, sql: str, p=()) -> list[dict]:
    return [dict(r) for r in (conn.execute(sql, p).fetchall() or [])]


def _una(conn, sql: str, p=()) -> dict | None:
    r = conn.execute(sql, p).fetchone()
    return dict(r) if r else None


def _esiste(conn, tabella: str) -> bool:
    r = _una(conn, "/*S:esiste*/ SELECT to_regclass(%s) IS NOT NULL AS c", (tabella,))
    return bool(r and r.get("c"))


def _colonne(conn, tabella: str) -> set:
    return {r["column_name"] for r in _righe(
        conn, "/*S:colonne*/ SELECT column_name FROM information_schema.columns "
              "WHERE table_schema = current_schema() AND table_name = %s", (tabella,))}


def _lettura(conn) -> None:
    """Transazione in sola lettura con statement_timeout: i controlli non possono scrivere né restare appesi."""
    conn.execute("SET TRANSACTION READ ONLY")
    conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)


def _chiudi(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


def _allarme(chiave: str, testo: str, **dati) -> dict:
    return {"chiave": chiave, "testo": testo, "dati": dati}


def _esito(nome: str, numeri: dict | None = None, allarmi: list | None = None, disponibile: bool = True) -> dict:
    allarmi = allarmi or []
    return {"controllo": nome, "ok": not allarmi, "disponibile": disponibile, "numeri": numeri or {},
            "allarmi": allarmi}


def _sito(ctx: dict) -> list[dict] | None:
    """Righe di sito_istantanea (lette una volta per giro). None se la tabella non c'è ancora."""
    return ctx.get("istantanea")


def _ids(righe: list[dict], escludi_sezioni=()) -> list[int]:
    return [int(r["listing_id"]) for r in righe if r.get("sezione") not in escludi_sezioni][:5000]


# ---------------------------------------------------------------------------------------------------------------
# I dieci controlli: f(conn, ctx) -> esito. Solo lettura.
# ---------------------------------------------------------------------------------------------------------------
def carica_istantanea(conn, ctx: dict) -> None:
    if not _esiste(conn, "sito_istantanea"):
        ctx["istantanea"] = None
        return
    ctx["istantanea"] = _righe(conn, "/*S:istantanea*/ SELECT listing_id, prezzo, margine, soglia, fonte, commerciante, "
                                     "provincia, area, km, sezione, categoria, salvata_at FROM sito_istantanea")
    r = _una(conn, "/*S:istantanea_eta*/ SELECT max(salvata_at) AS at, "
                   "extract(epoch FROM now() - max(salvata_at)) / 3600.0 AS ore FROM sito_istantanea")
    ctx["istantanea_at"] = (r or {}).get("at")
    ctx["istantanea_ore"] = (r or {}).get("ore")


def c_sito_conteggio(conn, ctx: dict) -> dict:
    sito = _sito(ctx)
    if sito is None:
        return _esito("sito_conteggio", {"istantanea": "assente"}, disponibile=False)
    n = len(sito)
    ieri = _una(conn, "/*S:stato_ieri*/ SELECT n_sito FROM sentinella_stato "
                      "WHERE ora = date_trunc('hour', now()) - interval '24 hours'")
    n_ieri = (ieri or {}).get("n_sito")
    ore = ctx.get("istantanea_ore")
    num = {"n": n, "ieri": n_ieri, "istantanea_ore": round(float(ore), 1) if ore is not None else None,
           "privati": sum(1 for r in sito if not r.get("commerciante")),
           "commercianti": sum(1 for r in sito if r.get("commerciante"))}
    al = []
    if n_ieri and n_ieri >= CALO_SITO_MIN_IERI and n < n_ieri * (1 - CALO_SITO_MAX):
        calo = 1 - n / n_ieri
        num["calo_pct"] = round(calo * 100, 1)
        al.append(_allarme("sito_calo", f"Auto sul sito: {n} ora contro {n_ieri} ieri alla stessa ora "
                                        f"(-{calo:.0%}, soglia -{CALO_SITO_MAX:.0%})", n=n, ieri=n_ieri))
    if ore is not None and float(ore) > ISTANTANEA_VECCHIA_ORE:
        al.append(_allarme("sito_istantanea_vecchia", f"La fotografia del sito ha {float(ore):.1f} ore: il sito non "
                                                      f"ricostruisce l'elenco o non chiama salva_istantanea", ore=round(float(ore), 1)))
    return _esito("sito_conteggio", num, al)


def _soglia_locale():
    try:
        from .web import scovo
        return scovo.soglia
    except Exception:
        return None


def c_sotto_soglia(conn, ctx: dict) -> dict:
    sito = _sito(ctx)
    if sito is None:
        return _esito("sotto_soglia", {"istantanea": "assente"}, disponibile=False)
    soglia = _soglia_locale()
    sotto, senza, diverse, controllate = [], 0, 0, 0
    for r in sito:
        if r.get("sezione") not in ("affari", "inc"):        # economiche e supercar hanno soglie loro (T2)
            continue
        controllate += 1
        if r.get("margine") is None:
            senza += 1
            continue
        s_loc = None
        if soglia and r.get("prezzo") is not None:
            try:
                s_loc = int(soglia(int(r["prezzo"])))
            except Exception:
                s_loc = None
        s = r.get("soglia") if r.get("soglia") is not None else s_loc
        if s_loc is not None and r.get("soglia") is not None and s_loc != r["soglia"]:
            diverse += 1
        if s is not None and int(r["margine"]) < int(s):
            sotto.append((int(r["margine"]) - int(s), r))
    sotto.sort(key=lambda x: x[0])
    num = {"controllate": controllate, "sotto": len(sotto), "senza_margine": senza, "soglia_diversa_sito_qui": diverse}
    al = []
    if sotto:
        es = [{"id": r["listing_id"], "prezzo": r.get("prezzo"), "margine": r.get("margine"),
               "soglia": r.get("soglia")} for _, r in sotto[:ESEMPI]]
        num["esempi"] = [e["id"] for e in es]
        al.append(_allarme("sotto_soglia", f"{len(sotto)} auto sul sito sotto il margine minimo scovo.soglia(prezzo). "
                                           f"Esempi: " + ", ".join(f"{e['id']} (margine {e['margine']} < {e['soglia']})"
                                                                   for e in es[:5]), esempi=es))
    return _esito("sotto_soglia", num, al)


def _regex_telefono():
    try:
        from .web import scovo
        return scovo.RE_MOBILE, scovo.RE_LANDLINE
    except Exception:
        return re.compile(r"(?<!\d)3\d{2}[\s.\-]?\d{3}[\s.\-]?\d{3,4}(?!\d)"), re.compile(r"(?i)tel\W{0,6}0\d{6,10}")


def c_regole_cliente(conn, ctx: dict) -> dict:
    sito = _sito(ctx)
    if sito is None:
        return _esito("regole_cliente", {"istantanea": "assente"}, disponibile=False)
    km_max = int(os.environ.get("KM_MASSIMO_SITO", str(KM_SITO_DEFAULT)) or KM_SITO_DEFAULT)
    roma = [r["listing_id"] for r in sito if (r.get("area") or "").lower() == "roma"
            or (r.get("provincia") or "").upper() == "RM"]
    km = [r["listing_id"] for r in sito if r.get("km") is not None and int(r["km"]) > km_max]
    nomi = [n.strip() for n in os.environ.get("SITO_COMMERCIANTI_NOMI", "loconte").split(",") if n.strip()]
    re_nomi = re.compile("|".join(re.escape(n) for n in nomi), re.I) if nomi else None
    mob, fisso = _regex_telefono()
    fb = {r["listing_id"] for r in sito if "facebook" in str(r.get("fonte") or "").lower()}   # chat di Marketplace
    loconte, senza_tel = [], []
    ids = _ids(sito)
    if ids:
        for t in _righe(conn, "/*S:testi*/ SELECT id, title, description, telefono_ok FROM listings WHERE id = ANY(%s)",
                        (ids,)):
            testo = f"{t.get('title') or ''} {t.get('description') or ''}"
            if re_nomi and re_nomi.search(testo):
                loconte.append(t["id"])
            if t["id"] not in fb and not t.get("telefono_ok") and not (mob.search(testo) or fisso.search(testo)):
                senza_tel.append(t["id"])
    num = {"roma": len(roma), "loconte": len(loconte), "oltre_km": len(km), "km_max": km_max,
           "senza_telefono": len(senza_tel)}
    al = []
    for chiave, lista, testo in (("regole:roma", roma, "auto di Roma sul sito"),
                                 ("regole:loconte", loconte, f"auto di {', '.join(nomi) or '-'} sul sito"),
                                 ("regole:km", km, f"auto oltre {km_max:,} km sul sito".replace(",", ".")),
                                 ("regole:telefono", senza_tel, "auto senza telefono sul sito (non Facebook)")):
        if lista:
            al.append(_allarme(chiave, f"{len(lista)} {testo}. Esempi: {', '.join(map(str, sorted(lista)[:ESEMPI]))}",
                               n=len(lista), esempi=sorted(lista)[:ESEMPI]))
    return _esito("regole_cliente", num, al)


def c_tetto_commercianti(conn, ctx: dict) -> dict:
    sito = _sito(ctx)
    if sito is None:
        return _esito("tetto_commercianti", {"istantanea": "assente"}, disponibile=False)
    n = sum(1 for r in sito if r.get("commerciante"))
    al = []
    if n > COMMERCIANTI_TETTO:
        al.append(_allarme("tetto_commercianti", f"{n} auto di commercianti sul sito: il tetto è {COMMERCIANTI_TETTO}",
                           n=n))
    return _esito("tetto_commercianti", {"commercianti": n, "tetto": COMMERCIANTI_TETTO}, al)


def c_impronte(conn, ctx: dict) -> dict:
    if not _esiste(conn, "config_impronte"):
        return _esito("impronte", {"tabella": "assente"}, disponibile=False)
    righe = _righe(conn, "/*S:impronte*/ SELECT DISTINCT ON (processo) processo, impronta, impronta_prezzi, prezzi, at "
                         "FROM config_impronte WHERE at > now() - interval '3 days' ORDER BY processo, at DESC")
    ctx["impronte"] = {r["processo"]: r for r in righe}
    web = ctx["impronte"].get("web")
    cron = {p: r for p, r in ctx["impronte"].items() if p.startswith("cron")}
    num = {"processi": len(righe), "web": (web or {}).get("impronta_prezzi"),
           "cron": {p: r.get("impronta_prezzi") for p, r in sorted(cron.items())}}
    al = []
    if web:
        pw = _prezzi_piatti(web.get("prezzi"))
        for p, r in sorted(cron.items()):
            if r.get("impronta_prezzi") == web.get("impronta_prezzi"):
                continue
            pc = _prezzi_piatti(r.get("prezzi"))
            diverse = sorted(k for k in set(pw) | set(pc) if pw.get(k) != pc.get(k))
            dett = [f"{k}: sito={pw.get(k, '-')} cron={pc.get(k, '-')}" for k in diverse[:10]]
            al.append(_allarme(f"impronte:{p}", f"Regole di prezzo diverse tra sito e {p} "
                                                f"({web.get('impronta_prezzi')} contro {r.get('impronta_prezzi')}): "
                                                + ("; ".join(dett) or "dettaglio non disponibile"),
                               processo=p, chiavi=diverse[:30]))
    return _esito("impronte", num, al)


def _prezzi_piatti(p) -> dict:
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            return {}
    p = p or {}
    out = {f"env.{k}": v for k, v in (p.get("env") or {}).items()}
    out.update({f"policy.{k}": json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
                for k, v in (p.get("policy") or {}).items()})
    return out


def c_cron(conn, ctx: dict, attese: dict | None = None) -> dict:
    attese = dict(ATTESE_ORE if attese is None else attese)
    ultimi = {r["job"]: r for r in _righe(
        conn, "/*S:cron_ultimi*/ SELECT job, extract(epoch FROM now() - max(finished_at)) / 3600.0 AS ore "
              "FROM job_runs WHERE job = ANY(%s) AND ok AND finished_at IS NOT NULL GROUP BY job", (list(attese),))}
    falliti = _righe(conn, "/*S:cron_falliti*/ SELECT job, count(*) AS n FROM job_runs WHERE ok = false "
                           "AND started_at > now() - interval '24 hours' GROUP BY job ORDER BY count(*) DESC LIMIT 30")
    bloccati = _righe(conn, "/*S:cron_in_corso*/ SELECT job, count(*) AS n, "
                            "max(extract(epoch FROM now() - started_at)) / 3600.0 AS ore FROM job_runs "
                            "WHERE finished_at IS NULL AND started_at < now() - make_interval(hours => %s) "
                            "AND started_at > now() - interval '48 hours' GROUP BY job LIMIT 30", (IN_CORSO_ORE,))
    al, eta = [], {}
    for job, ore_max in attese.items():
        ore = (ultimi.get(job) or {}).get("ore")
        eta[job] = round(float(ore), 1) if ore is not None else None
        if ore is None or float(ore) > ore_max:
            al.append(_allarme(f"cron_vecchio:{job}",
                               f"Lavoro '{job}': ultimo giro riuscito " + (f"{float(ore):.1f} ore fa" if ore is not None
                                                                           else "mai (nelle righe di job_runs)")
                               + f", previsto entro {ore_max} ore", job=job, ore=eta[job]))
    for f in falliti:
        if int(f["n"]) >= FALLIMENTI_MIN:
            al.append(_allarme(f"cron_fallito:{f['job']}", f"Lavoro '{f['job']}' fallito {f['n']} volte nelle ultime 24 ore",
                               job=f["job"], n=int(f["n"])))
    for b in bloccati:
        al.append(_allarme(f"cron_in_corso:{b['job']}", f"Lavoro '{b['job']}' risulta in corso da "
                                                        f"{float(b['ore']):.1f} ore ({b['n']} righe aperte): processo morto o bloccato",
                           job=b["job"], n=int(b["n"])))
    return _esito("cron", {"ore_dall_ultimo": eta, "falliti_24h": {f["job"]: int(f["n"]) for f in falliti},
                           "in_corso_oltre": {b["job"]: int(b["n"]) for b in bloccati}}, al)


def c_valutazioni(conn, ctx: dict) -> dict:
    sito = _sito(ctx)
    if sito is None:
        return _esito("valutazioni", {"istantanea": "assente"}, disponibile=False)
    ids = _ids(sito, escludi_sezioni=("supercar",))          # le supercar le valuta supercar_eu, non valutazioni_policy
    if not ids:
        return _esito("valutazioni", {"n": 0})
    ore = {int(r["listing_id"]): r.get("ore") for r in _righe(
        conn, "/*S:valutazioni*/ SELECT listing_id, extract(epoch FROM now() - max(computed_at)) / 3600.0 AS ore "
              "FROM valutazioni_policy WHERE listing_id = ANY(%s) GROUP BY listing_id", (ids,))}
    mancano = [i for i in ids if i not in ore]
    vecchie = [i for i in ids if i in ore and ore[i] is not None and float(ore[i]) > VALUTAZIONI_ORE]
    quota = (len(mancano) + len(vecchie)) / len(ids)
    num = {"n": len(ids), "vecchie": len(vecchie), "senza": len(mancano), "quota": round(quota, 3)}
    al = []
    if len(ids) >= MIN_AUTO_QUOTE and quota > VALUTAZIONI_VECCHIE_MAX:
        al.append(_allarme("valutazioni_vecchie", f"{quota:.0%} delle auto del sito ({len(vecchie)} vecchie, {len(mancano)} "
                                                  f"senza) ha la valutazione più vecchia di {VALUTAZIONI_ORE} ore "
                                                  f"(soglia {VALUTAZIONI_VECCHIE_MAX:.0%})",
                           esempi=sorted(vecchie + mancano)[:ESEMPI]))
    return _esito("valutazioni", num, al)


def c_jev(conn, ctx: dict) -> dict:
    sito = _sito(ctx)
    if sito is None or not _esiste(conn, "decisione_ai"):
        return _esito("jev", {"tabella": "assente" if sito is not None else "istantanea assente"}, disponibile=False)
    ids = _ids(sito, escludi_sezioni=("supercar",))
    con = {int(r["listing_id"]) for r in _righe(
        conn, "/*S:jev_decisioni*/ SELECT d.listing_id FROM decisione_ai d JOIN listings l ON l.id = d.listing_id "
              "WHERE d.listing_id = ANY(%s) AND d.prezzo = l.price_eur", (ids,))} if ids else set()
    c = _una(conn, "/*S:jev_chiamate*/ SELECT count(*) FILTER (WHERE computed_at > now() - interval '24 hours') AS n24, "
                   "count(*) AS n7 FROM decisione_ai WHERE computed_at > now() - interval '7 days'") or {}
    n24, n7 = int(c.get("n24") or 0), int(c.get("n7") or 0)
    senza = len(ids) - len(con & set(ids))
    quota = senza / len(ids) if ids else 0.0
    modo = (((ctx.get("impronte") or {}).get("web") or {}).get("prezzi") or {})
    if isinstance(modo, str):
        try:
            modo = json.loads(modo)
        except Exception:
            modo = {}
    modo = (modo.get("interruttori") or {}).get("JEV_MODO")
    num = {"n": len(ids), "senza_decisione": senza, "quota_senza": round(quota, 3), "chiamate_24h": n24,
           "chiamate_7g": n7, "jev_modo_sito": modo}
    al = []
    if n24 == 0 and n7 > 0:                                   # funzionava e si è fermato
        al.append(_allarme("jev_fermo", f"Jev: nessuna decisione nelle ultime 24 ore ({n7} negli ultimi 7 giorni)"))
    # senza decisione conta se Jev decide il sito (JEV_MODO=attiva) o se ha lavorato negli ultimi 7 giorni
    if len(ids) >= MIN_AUTO_QUOTE and quota > JEV_SENZA_MAX and (modo == "attiva" or n7 > 0):
        al.append(_allarme("jev_senza", f"Jev: {quota:.0%} delle auto del sito senza decisione sul prezzo attuale "
                                        f"({senza} su {len(ids)}, soglia {JEV_SENZA_MAX:.0%})"))
    return _esito("jev", num, al)


def _conta_errori(conn, tabella: str, cols: set, chiave: str) -> tuple[int | None, str | None]:
    """Righe in errore nelle ultime 24 ore di una tabella di registro con colonne riconosciute, o (None, None)."""
    if not _NOME_SQL.match(tabella):
        return None, None
    t = next((c for c in ("at", "created_at", "creato_at", "ricevuto_at", "received_at", "inviato_at") if c in cols), None)
    if not t:
        return None, None
    cond = []
    for c in ("errore", "error", "email_errore"):
        if c in cols:
            cond.append(f"coalesce({c}::text, '') <> ''")
    for c in ("ok", "email_ok"):
        if c in cols:
            cond.append(f"{c} IS FALSE")
    for c in ("esito", "stato", "status", "email_stato"):
        if c in cols:
            cond.append(f"lower({c}::text) IN ('errore','error','failed','fallito','non_inviata')")
    if not cond:
        return None, None
    r = _una(conn, f"/*S:{chiave}*/ SELECT count(*) AS n FROM {tabella} "
                   f"WHERE {t} > now() - interval '24 hours' AND ({' OR '.join(cond)})")
    return int((r or {}).get("n") or 0), t


def c_pagamenti(conn, ctx: dict) -> dict:
    num, al, visto = {}, [], False
    tabelle = [r["table_name"] for r in _righe(
        conn, "/*S:tabelle_stripe*/ SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() "
              "AND (table_name LIKE 'stripe%%' OR table_name LIKE '%%webhook%%') ORDER BY 1 LIMIT 10")]
    for tab in tabelle:
        n, _ = _conta_errori(conn, tab, _colonne(conn, tab), "stripe_errori")
        if n is None:
            continue
        visto = True
        num[f"{tab}:errori_24h"] = n
        if n > 0:
            al.append(_allarme(f"stripe:{tab}", f"Stripe: {n} errori dei webhook nelle ultime 24 ore (tabella {tab})", n=n))
    if _esiste(conn, "appuntamenti"):
        n, _ = _conta_errori(conn, "appuntamenti", _colonne(conn, "appuntamenti"), "appuntamenti_errori")
        if n is not None:
            visto = True
            num["appuntamenti:email_non_partite_24h"] = n
            if n > 0:
                al.append(_allarme("appuntamenti_email", f"Appuntamenti: {n} email a {EMAIL} non partite nelle ultime "
                                                         f"24 ore", n=n))
    if not visto:
        num["registro"] = "nessuna tabella con gli esiti (webhook Stripe / email appuntamenti)"
    return _esito("pagamenti", num, al, disponibile=visto)


def c_imbuto(conn, ctx: dict) -> dict:
    if not _esiste(conn, "sito_funnel_giorno"):
        return _esito("imbuto", {"tabella": "assente"}, disponibile=False)
    cols = _colonne(conn, "sito_funnel_giorno")
    g = next((c for c in ("giorno", "data", "day", "at") if c in cols), None)
    p = next((c for c in ("passo", "filtro", "regola", "nome", "step") if c in cols), None)
    e = next((c for c in ("entrati", "prima", "ingresso", "n_in", "dentro") if c in cols), None)
    u = next((c for c in ("usciti", "tolti", "via", "scartati", "n_out") if c in cols), None)
    d = next((c for c in ("dopo", "restano", "rimasti", "uscita") if c in cols), None)
    if not (g and p and e and (u or d)) or not all(_NOME_SQL.match(x) for x in (g, p, e, u or d)):
        return _esito("imbuto", {"colonne": sorted(cols)[:20], "nota": "colonne non riconosciute"}, disponibile=False)
    usc = u if u else f"({e} - {d})"
    righe = _righe(conn, f"/*S:imbuto*/ SELECT {g}::date AS giorno, {p}::text AS passo, sum({e}) AS entrati, "
                         f"sum({usc}) AS usciti FROM sito_funnel_giorno WHERE {g}::date >= current_date - 8 "
                         f"GROUP BY 1, 2")
    per_passo: dict = {}
    for r in righe:
        if r.get("entrati"):
            per_passo.setdefault(r["passo"], {})[r["giorno"]] = (float(r["usciti"] or 0) / float(r["entrati"]),
                                                                 float(r["entrati"]))
    if not per_passo:
        return _esito("imbuto", {"passi": 0})
    oggi = max(gg for v in per_passo.values() for gg in v)
    num, al = {"giorno": str(oggi), "passi": len(per_passo)}, []
    for passo, giorni in sorted(per_passo.items()):
        if oggi not in giorni:
            continue
        quota, entrati = giorni[oggi]
        storico = [q for gg, (q, _) in giorni.items() if gg != oggi]
        if len(storico) < IMBUTO_GIORNI_MIN:
            continue
        med = statistics.median(storico)
        num[str(passo)] = {"oggi": round(quota, 3), "mediana_7g": round(med, 3)}
        if entrati >= IMBUTO_ENTRATI_MIN and quota >= IMBUTO_QUOTA_MIN and quota > IMBUTO_FATTORE * med:
            al.append(_allarme(f"imbuto:{passo}", f"Filtro '{passo}' del sito oggi toglie il {quota:.0%} delle auto "
                                                  f"(mediana dei 7 giorni {med:.0%}, soglia {IMBUTO_FATTORE:g}x)",
                               passo=str(passo), quota=round(quota, 3), mediana=round(med, 3)))
    return _esito("imbuto", num, al)


CONTROLLI = (("sito_conteggio", c_sito_conteggio), ("sotto_soglia", c_sotto_soglia),
             ("regole_cliente", c_regole_cliente), ("tetto_commercianti", c_tetto_commercianti),
             ("impronte", c_impronte), ("cron", c_cron), ("valutazioni", c_valutazioni), ("jev", c_jev),
             ("pagamenti", c_pagamenti), ("imbuto", c_imbuto))


def controlla(conn, ctx: dict | None = None, attese: dict | None = None) -> list[dict]:
    """Esegue tutti i controlli (solo lettura). Un controllo che va in errore diventa un allarme 'errore:<nome>'."""
    ctx = {} if ctx is None else ctx
    t0 = time.monotonic()
    out = []
    try:
        _lettura(conn)
        carica_istantanea(conn, ctx)
    except Exception as e:
        ctx["istantanea"] = None
        log.warning("SENTINELLA istantanea non letta: %s", str(e)[:160])
    finally:
        _chiudi(conn)
    for nome, fn in CONTROLLI:
        if time.monotonic() - t0 > BUDGET_S:
            out.append(_esito(nome, {"saltato": "tempo"}, disponibile=False))
            continue
        try:
            _lettura(conn)
            r = fn(conn, ctx, attese) if fn is c_cron else fn(conn, ctx)
        except Exception as e:
            msg = (type(e).__name__ + ": " + str(e).splitlines()[0][:140]) if str(e) else type(e).__name__
            r = _esito(nome, {"errore": msg}, [_allarme(f"errore:{nome}", f"Controllo '{nome}' non riuscito: {msg}")])
        finally:
            _chiudi(conn)
        out.append(r)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Scritture (solo nelle tabelle della sentinella): stato per ora, allarmi con dedupe, coda delle email
# ---------------------------------------------------------------------------------------------------------------
_TABELLE = {"ok": False}


def assicura_tabelle(conn) -> None:
    """Se la migrazione 034 non è ancora stata applicata (cron partito prima del sito), la applica: è idempotente."""
    if _TABELLE["ok"]:
        return
    if not (_esiste(conn, "sentinella_allarmi") and _esiste(conn, "sito_istantanea") and _esiste(conn, "config_impronte")
            and _esiste(conn, "sentinella_stato")):
        conn.execute(MIGRAZIONE.read_text())
    conn.commit()
    _TABELLE["ok"] = True


def salva_stato(conn, ctx: dict, risultati: list[dict], processo: str, adesso: datetime) -> None:
    sito = _sito(ctx)
    ora = adesso.replace(minute=0, second=0, microsecond=0)
    esito = {r["controllo"]: r["ok"] for r in risultati}
    conn.execute("/*S:stato_salva*/ INSERT INTO sentinella_stato (ora, n_sito, n_privati, n_commercianti, istantanea_at, "
                 "processo, esito, aggiornato_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ora) DO UPDATE SET "
                 "n_sito=EXCLUDED.n_sito, n_privati=EXCLUDED.n_privati, n_commercianti=EXCLUDED.n_commercianti, "
                 "istantanea_at=EXCLUDED.istantanea_at, processo=EXCLUDED.processo, esito=EXCLUDED.esito, "
                 "aggiornato_at=EXCLUDED.aggiornato_at",
                 (ora, len(sito) if sito is not None else None,
                  sum(1 for r in sito if not r.get("commerciante")) if sito is not None else None,
                  sum(1 for r in sito if r.get("commerciante")) if sito is not None else None,
                  ctx.get("istantanea_at"), processo[:60], json.dumps(esito), adesso))


def registra_allarmi(conn, allarmi: list[dict], adesso: datetime) -> tuple[list[dict], int]:
    """Dedupe: lo stesso allarme (chiave) al massimo una volta ogni DEDUPE_ORE. Ritorna (nuovi, ripetuti)."""
    nuovi, ripetuti = [], 0
    dal = adesso - timedelta(hours=DEDUPE_ORE)
    for a in allarmi:
        r = _una(conn, "/*S:allarme_ultimo*/ SELECT id FROM sentinella_allarmi WHERE chiave = %s AND creato_at > %s "
                       "ORDER BY creato_at DESC LIMIT 1", (a["chiave"], dal))
        if r:
            conn.execute("/*S:allarme_visto*/ UPDATE sentinella_allarmi SET visto_at = %s, volte = volte + 1 WHERE id = %s",
                         (adesso, r["id"]))
            ripetuti += 1
            continue
        conn.execute("/*S:allarme_nuovo*/ INSERT INTO sentinella_allarmi (chiave, controllo, testo, dati, creato_at, visto_at) "
                     "VALUES (%s,%s,%s,%s,%s,%s)",
                     (a["chiave"][:200], a.get("controllo") or a["chiave"].split(":")[0], a["testo"][:2000],
                      json.dumps(a.get("dati") or {}, default=str), adesso, adesso))
        nuovi.append(a)
    return nuovi, ripetuti


def _invia_brevo(oggetto: str, testo: str) -> bool:
    """Email transazionale Brevo a info@skovo.app con gli stessi helper di _avvisa_appuntamento (web/app.py)."""
    try:
        import httpx
        from .marketing.brevo_liste import API, MITTENTE, _chiave
        chiave = _chiave()
        if not chiave:
            log.warning("SENTINELLA email non mandata: chiave Brevo assente")
            return False
        r = httpx.post(f"{API}/smtp/email", headers={"api-key": chiave, "accept": "application/json"}, timeout=20,
                       json={"sender": MITTENTE, "to": [{"email": EMAIL}], "subject": oggetto, "textContent": testo})
        if 200 <= r.status_code < 300:
            return True
        log.warning("SENTINELLA email non mandata: Brevo %s", r.status_code)
    except Exception as e:
        log.warning("SENTINELLA email non mandata: %s", type(e).__name__)
    return False


def invia_coda(conn, adesso: datetime, invia=None) -> dict:
    """Manda in UNA email gli allarmi non ancora inviati, solo tra le 8 e le 22 di Roma; di notte restano in coda e il
    primo giro dopo le 8 manda il riepilogo del mattino. Un invio non riuscito si riprova (al massimo 5 volte)."""
    if not di_giorno(adesso):
        r = _una(conn, "/*S:coda_conta*/ SELECT count(*) AS n FROM sentinella_allarmi WHERE inviato_at IS NULL "
                       "AND tentativi < 5")
        return {"inviati": 0, "in_coda": int((r or {}).get("n") or 0), "notte": True}
    righe = _righe(conn, "/*S:coda*/ SELECT id, chiave, testo, creato_at FROM sentinella_allarmi WHERE inviato_at IS NULL "
                         "AND tentativi < 5 ORDER BY creato_at LIMIT 200 FOR UPDATE SKIP LOCKED")
    if not righe:
        return {"inviati": 0, "in_coda": 0}
    oggi8 = ora_roma(adesso).replace(hour=GIORNO_DA, minute=0, second=0, microsecond=0)
    notte = [r for r in righe if r.get("creato_at") is not None and r["creato_at"] < oggi8]
    titolo = (f"Skovo sentinella: riepilogo del mattino, {len(righe)} allarmi" if notte
              else f"Skovo sentinella: {len(righe)} allarmi nuovi")
    corpo = "\n".join(f"- [{ora_roma(r['creato_at']).strftime('%d/%m %H:%M') if r.get('creato_at') else '?'}] {r['testo']}"
                      for r in righe)
    testo = (f"{titolo}\n\n{corpo}\n\nOgni allarme si ripete al massimo una volta ogni {DEDUPE_ORE} ore. "
             f"Dettagli nella tabella sentinella_allarmi e nella riga di log SENTINELLA.")
    ok = (invia or _invia_brevo)(titolo, testo)
    ids = [r["id"] for r in righe]
    if ok:
        conn.execute("/*S:coda_inviati*/ UPDATE sentinella_allarmi SET inviato_at = %s WHERE id = ANY(%s)", (adesso, ids))
    else:
        conn.execute("/*S:coda_tentativo*/ UPDATE sentinella_allarmi SET tentativi = tentativi + 1 WHERE id = ANY(%s)",
                     (ids,))
    return {"inviati": len(ids) if ok else 0, "in_coda": 0 if ok else len(ids), "mattino": bool(notte)}


def _pulizia(conn, adesso: datetime) -> None:
    conn.execute("/*S:pulizia_allarmi*/ DELETE FROM sentinella_allarmi WHERE creato_at < %s", (adesso - timedelta(days=60),))
    conn.execute("/*S:pulizia_stato*/ DELETE FROM sentinella_stato WHERE ora < %s", (adesso - timedelta(days=30),))


def _recente(conn, adesso: datetime, minuti: int) -> bool:
    r = _una(conn, "/*S:ultimo_giro*/ SELECT max(aggiornato_at) AS at FROM sentinella_stato")
    at = (r or {}).get("at")
    return bool(at and at > adesso - timedelta(minutes=minuti))


def esegui(processo: str, connetti=None, invia=None, adesso: datetime | None = None, min_minuti: int = 30,
           attese: dict | None = None) -> dict:
    """Un giro completo: controlli, stato dell'ora, allarmi con dedupe, email (o coda di notte), riga di log.
    Salta i controlli se un altro processo li ha fatti negli ultimi `min_minuti` (ma manda comunque la coda).
    Non solleva mai."""
    if os.environ.get("SENTINELLA", "1") == "0":
        return {"spenta": True}
    t0 = time.monotonic()
    adesso = adesso or _adesso()
    riepilogo: dict = {"processo": processo}
    try:
        if connetti is None:
            from .db import connect as connetti
        with connetti() as conn:
            conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)
            assicura_tabelle(conn)
            if min_minuti and _recente(conn, adesso, min_minuti):
                _chiudi(conn)
                riepilogo["saltato"] = f"giro fatto negli ultimi {min_minuti} minuti"
            else:
                _chiudi(conn)
                ctx: dict = {}
                risultati = controlla(conn, ctx, attese)
                allarmi = [dict(a, controllo=r["controllo"]) for r in risultati for a in r["allarmi"]]
                conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)
                conn.execute("SELECT pg_advisory_xact_lock(hashtext('sentinella:scrivi'))")   # un processo alla volta
                salva_stato(conn, ctx, risultati, processo, adesso)
                nuovi, ripetuti = registra_allarmi(conn, allarmi, adesso)
                _pulizia(conn, adesso)
                conn.commit()
                riepilogo.update(controlli={r["controllo"]: dict(ok=r["ok"], **({} if r["disponibile"] else {"disponibile": False}),
                                                                  **r["numeri"]) for r in risultati},
                                 allarmi=len(allarmi), nuovi=[a["chiave"] for a in nuovi], ripetuti=ripetuti)
            conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)
            riepilogo["email"] = invia_coda(conn, adesso, invia)
            conn.commit()
    except Exception as e:
        riepilogo["errore"] = (type(e).__name__ + ": " + str(e).splitlines()[0][:160]) if str(e) else type(e).__name__
    riepilogo["secondi"] = round(time.monotonic() - t0, 1)
    try:
        log.warning("SENTINELLA %s", json.dumps(riepilogo, ensure_ascii=False, default=str, sort_keys=True))
    except Exception:
        pass
    return riepilogo


def solo_coda(connetti=None, invia=None, adesso: datetime | None = None) -> dict:
    """Solo l'invio della coda (il thread del sito lo fa ogni 10 minuti: il riepilogo del mattino parte alle 8)."""
    adesso = adesso or _adesso()
    if not di_giorno(adesso) or os.environ.get("SENTINELLA", "1") == "0":
        return {"inviati": 0}
    try:
        if connetti is None:
            from .db import connect as connetti
        with connetti() as conn:
            conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)
            assicura_tabelle(conn)
            out = invia_coda(conn, adesso, invia)
            conn.commit()
        if out.get("inviati"):
            log.warning("SENTINELLA_EMAIL %s", json.dumps(out))
        return out
    except Exception as e:
        log.warning("SENTINELLA coda non inviata: %s", type(e).__name__)
        return {"inviati": 0}


# ---------------------------------------------------------------------------------------------------------------
# Scrittori chiamati dagli altri: fotografia del sito, impronta delle regole
# ---------------------------------------------------------------------------------------------------------------
_ist = {"firma": None, "t": 0.0}
ISTANTANEA_RINFRESCA_S = 1800         # elenco uguale: solo l'ora della fotografia, al massimo ogni 30 minuti
_ist_lock = threading.Lock()


def _riga_istantanea(it: dict, soglia) -> tuple | None:
    try:
        lid = int(it["id"])
    except Exception:
        return None
    pv = it.get("pv") if isinstance(it.get("pv"), dict) else {}
    prezzo = it.get("prezzo")
    prezzo = int(prezzo) if isinstance(prezzo, (int, float)) else None
    marg = pv.get("margine")
    marg = int(round(marg)) if isinstance(marg, (int, float)) else None
    s = None
    if soglia and prezzo is not None:
        try:
            s = int(soglia(prezzo))
        except Exception:
            s = None
    m = re.search(r"\((\w\w)\)\s*$", str(it.get("zona") or ""))
    sez = ("supercar" if it.get("supercar") else "eco" if it.get("economica") else
           "inc" if it.get("incidentata") else "affari")
    km = it.get("km")
    km = int(km) if isinstance(km, (int, float)) else None
    return (lid, prezzo, marg, s, str(it.get("fonte") or "")[:40] or None,
            bool(it.get("commerciante") or it.get("commerciante_top")), m.group(1).upper() if m else None,
            (str(it.get("area")) if it.get("area") else None), km, sez,
            (str(pv.get("categoria")) if pv.get("categoria") else None))


def salva_istantanea(items: list, connetti=None) -> int:
    """Fotografia dell'elenco del sito in sito_istantanea (sostituita a ogni costruzione). Da chiamare alla fine di
    web/app._calcola_elenco con la lista finale. Se l'elenco non è cambiato dall'ultima volta rinfresca solo l'ora
    (al massimo ogni 30 minuti), così il controllo 1 non lo scambia per un sito fermo.
    Ritorna quante righe ha scritto (-1 se non riuscito). Non solleva mai."""
    try:
        soglia = _soglia_locale()
        righe = [r for r in (_riga_istantanea(it, soglia) for it in list(items or []) if isinstance(it, dict)) if r]
        uniche = list({r[0]: r for r in righe}.values())
        firma = hashlib.sha256(json.dumps(sorted(uniche), default=str).encode()).hexdigest()
        with _ist_lock:
            uguale = firma == _ist["firma"]
            if uguale and time.monotonic() - _ist["t"] < ISTANTANEA_RINFRESCA_S:
                return 0
            if connetti is None:
                from .db import connect as connetti
            if uguale:
                with connetti() as conn:
                    conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)
                    conn.execute("/*S:ist_rinfresca*/ UPDATE sito_istantanea SET salvata_at = now()")
                    conn.commit()
                _ist["t"] = time.monotonic()
                return 0
            with connetti() as conn:
                conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)
                assicura_tabelle(conn)
                conn.execute("/*S:ist_svuota*/ DELETE FROM sito_istantanea")
                if uniche:
                    col = list(zip(*uniche))
                    conn.execute("/*S:ist_scrivi*/ INSERT INTO sito_istantanea (listing_id, prezzo, margine, soglia, fonte, "
                                 "commerciante, provincia, area, km, sezione, categoria) SELECT * FROM unnest("
                                 "%s::bigint[], %s::int[], %s::int[], %s::int[], %s::text[], %s::bool[], %s::text[], "
                                 "%s::text[], %s::int[], %s::text[], %s::text[])", [list(c) for c in col])
                conn.commit()
            _ist["firma"], _ist["t"] = firma, time.monotonic()
        return len(uniche)
    except Exception as e:
        log.warning("SENTINELLA istantanea non salvata: %s", type(e).__name__ + ": " + str(e)[:120])
        return -1


def impronta_prezzi(regole: dict) -> tuple[str, dict]:
    """(sha256 a 12 cifre, dati) delle sole regole di prezzo: env con i PREFISSI_PREZZI + PolicyConfig risolta."""
    env = regole.get("env") or {}
    dati = {"env": {k: v for k, v in sorted(env.items()) if k.startswith(PREFISSI_PREZZI) and k not in ESCLUSE_PREZZI},
            "policy": regole.get("policy") or {}}
    canon = json.dumps(dati, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()[:12], dati


def salva_impronta(processo: str, connetti=None, in_sottofondo: bool = False) -> bool:
    """Scrive in config_impronte l'impronta delle regole di questo processo (accanto a regole_log.registra).
    Con in_sottofondo=True lo fa in un thread (l'avvio del sito non aspetta il database). Non solleva mai."""
    if in_sottofondo:
        threading.Thread(target=salva_impronta, args=(processo, connetti), daemon=True, name="sentinella-impronta").start()
        return True
    try:
        if not os.environ.get("DATABASE_URL") and connetti is None:
            return False
        from . import regole_log
        h, regole = regole_log.impronta()
        hp, prezzi = impronta_prezzi(regole)
        prezzi["interruttori"] = {k: (regole.get("env") or {}).get(k) for k in INTERRUTTORI}
        commit = (os.environ.get("RENDER_GIT_COMMIT") or "")[:12] or None
        if connetti is None:
            from .db import connect as connetti
        with connetti() as conn:
            conn.execute("SET LOCAL statement_timeout = '%ds'" % TIMEOUT_S)
            assicura_tabelle(conn)
            conn.execute("/*S:impronta_salva*/ INSERT INTO config_impronte (processo, impronta, impronta_prezzi, prezzi, "
                         "commit_git, at) VALUES (%s,%s,%s,%s,%s, now()) ON CONFLICT (processo, impronta) DO UPDATE SET "
                         "impronta_prezzi=EXCLUDED.impronta_prezzi, prezzi=EXCLUDED.prezzi, commit_git=EXCLUDED.commit_git, "
                         "at=now()", (processo[:80], h, hp, json.dumps(prezzi, default=str), commit))
            conn.commit()
        return True
    except Exception as e:
        log.warning("SENTINELLA impronta non salvata: %s", type(e).__name__)
        return False


# ---------------------------------------------------------------------------------------------------------------
# Thread leggero del sito
# ---------------------------------------------------------------------------------------------------------------
_thread = {"avviato": False}


def istantanea_da_app() -> int:
    """Difesa: se il sito non chiama ancora salva_istantanea, la fotografia si prende da app._elenco (se c'è)."""
    m = sys.modules.get("dealfinder.web.app")
    el = getattr(m, "_elenco", None) if m else None
    if not isinstance(el, dict) or el.get("busy") and not el.get("items"):
        return 0
    items = list(el.get("items") or [])
    if not items:                                   # elenco non ancora costruito: non si cancella la fotografia
        return 0
    return salva_istantanea(items)


def _ciclo_web(ogni_min: int, passo_s: int, attesa_s: int) -> None:
    time.sleep(attesa_s)                            # prima si lascia partire il sito
    ultimo = 0.0
    while True:
        try:
            istantanea_da_app()
            if time.monotonic() - ultimo >= ogni_min * 60 or not ultimo:
                salva_impronta("web")               # rinfrescata ogni ora: il controllo 5 guarda gli ultimi 3 giorni
                esegui("web")
                ultimo = time.monotonic()
            else:
                solo_coda()
        except Exception as e:
            log.warning("SENTINELLA thread: %s", type(e).__name__)
        time.sleep(passo_s)


def avvia_thread_web(ogni_min: int = 60, passo_s: int = 600, attesa_s: int = 180) -> bool:
    """Un solo thread daemon nel sito: ogni 10 minuti fotografia (se serve) e coda delle email, ogni ora il giro."""
    if _thread["avviato"] or os.environ.get("SENTINELLA", "1") == "0":
        return False
    _thread["avviato"] = True
    threading.Thread(target=_ciclo_web, args=(ogni_min, passo_s, attesa_s), daemon=True, name="sentinella").start()
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(json.dumps(esegui("manuale", min_minuti=0), ensure_ascii=False, default=str, indent=1))
