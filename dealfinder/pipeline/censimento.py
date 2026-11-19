"""Censimento delle auto di privati della zona (16/11, cliente: "con i nostri filtri e senza commercianti ci sono più di
30.000 auto di privati a Milano e dintorni: impossibile che non ce ne siano almeno 500 buone").

Sospetto da misurare: il giro di zona (pipeline/benzina_mi.py, CANDIDATI_SQL + scelta_candidate) manda alla
valutazione completa solo le auto che una STIMA RAPIDA (prezzo sotto BENZ_SOGLIA × mediana di marca/modello/anno, al
massimo BENZ_MAX auto, fascia BENZ_MIN–BENZ_PMAX, solo benzina salvo BENZ_TUTTI_CARB) indica sotto il prezzo; le altre
non vengono mai valutate davvero. Se la stima rapida sbaglia, auto buone non arrivano mai al sito.

Lavoro in SOLA LETTURA (connessione propria READ ONLY, nessuna scrittura di valutazioni, NESSUNA AI):
1. conta gli annunci ATTIVI e freschi (visti nelle ultime ORE_FRESCHI ore) di PRIVATI nella zona del sito
   (sito_regole.PROVINCE_100KM, provincia dall'annuncio o dal comune con sito_regole.provincia_di, come il sito) con
   anno e km del sito (sito_regole.Regole.da_env) e prezzo valido (PMIN–PMAX, divisi per fascia); quanti hanno una
   valutazione completa (valutazioni_policy della policy in vigore; anche una riga in `valuations`) e con che
   categoria; quanti NON sono mai stati valutati;
2. campione casuale di N (CENSIMENTO_N, default 600) annunci di privati MAI valutati o valutati da più di VECCHIA_GIORNI (7)
   giorni, valutati con lo STESSO calcolo del sito/cron (valutazione.prepara + ricalcolo.valuta_con_ripiego, gli
   stessi confronti, commercianti, dotazioni, cascata, ingressi, soglie scovo.soglia) ma con il giudice AI SPENTO
   (giudice=False: nessuna chiamata, costo zero) e SENZA la stima rapida: nessun filtro sul prezzo rispetto alla
   mediana. Il margine del sito è quello di perche_fuori.sito (calibrazione chiesto → venduto e costo del difetto
   letto dall'AI già in archivio, come sito_regole.t_margine_sito). "Buona" = categoria opportunita/candidato,
   telefono sì (valutazione.telefono) e margine del sito >= scovo.soglia(prezzo); a parte quelle "Da trattare"
   (perche_fuori.passa) e i segnali che il sito controlla dopo (sito_regole.segnali_duri, sconto da far controllare
   all'AI);
3. scrive UNA riga di log "CENSIMENTO {json}": totali del punto 1, campione valutato, buone nel campione, stima
   proiettata sul totale non valutato (per strato: mai valutate / valutate da più di 7 giorni) e 10 esempi anonimi
   (marca, modello, anno, km, prezzo, M, margine, provincia: niente telefoni, link, id o nomi);
4. tempo massimo CENSIMENTO_MIN minuti (default 25, dall'inizio del lavoro): superato, si ferma e scrive i risultati
   parziali ("fermata": "tempo"). Gira nel SITO (512 MB): conteggio letto a pagine (PAGINA righe, colonne leggere),
   valutazioni un gruppo alla volta con una pausa dopo ogni auto, freno di memoria come perche_fuori.

Lancio una tantum, SUBITO nel sito (non nella coda del cron: coda_lavori.SOLO_SITO), una volta per etichetta:
BREVO_IMPORT_LISTE=censimento:<etichetta> (es. censimento:1611). Non richiede ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import gc
import json
import logging
import os
import random
import time
from collections import Counter

log = logging.getLogger("censimento")

N_DEFAULT = 600
N_MAX = 3000
MINUTI_DEFAULT = 25
PAGINA = 5000                        # righe per pagina del conteggio (keyset su l.id)
PAUSA_S = 0.005                      # dopo ogni valutazione: il sito resta reattivo (stesso processo)
ORE_FRESCHI = 48                     # "freschi": visti nelle ultime 48 ore
VECCHIA_GIORNI = 7                   # valutazione più vecchia di 7 giorni: nel campione come le mai valutate
PMIN, PMAX = 500, 200000             # prezzo valido (come perche_fuori); le fasce sotto dicono il resto
FASCE_PREZZO = ((8000, "fino_8000"), (15000, "8001_15000"), (10**9, "oltre_15000"))   # BENZ_MAX/BENZ_PMAX del giro
MAX_ESEMPI = 10
MEMORIA_MAX_MB = 470        # pensati per 512 MB: esegui() li scala con memoria.soglia (memoria automatica, 19/11)
SCARTI_PRIMA = ("doppione", "truffa", "prezzo_finto", "dati_incoerenti")   # stage_reason: mai sul sito
CATEGORIE_BUONE = ("opportunita", "candidato")

# Punto 1: solo colonne leggere. La provincia si decide in Python con sito_regole.provincia_di (annunci senza sigla:
# dal comune), quindi qui entrano anche le righe senza una sigla valida.
CONTA_SQL = """
SELECT l.id, l.source, l.province, l.city, l.make, l.model, l.fuel, l.year, l.mileage_km, l.price_eur,
       l.telefono_ok, l.stage_reason, l.first_seen_at,
       v.categoria, v.computed_at AS valutata_at,
       EXISTS (SELECT 1 FROM valuations w WHERE w.listing_id = l.id) AS ha_valuations
FROM listings l
LEFT JOIN valutazioni_policy v ON v.listing_id = l.id AND v.policy_version = %(pv)s
WHERE l.status = 'attivo' AND l.last_seen_at > now() - make_interval(hours => %(ore)s)
  AND l.seller_type = 'privato' AND (%(fb)s OR l.source <> 'facebook')
  AND (upper(l.province) = ANY(%(prov)s) OR l.province IS NULL OR l.province !~ '^[A-Za-z]{2}$')
  AND l.year >= %(anno)s AND l.mileage_km BETWEEN 0 AND %(km)s
  AND l.price_eur BETWEEN %(pmin)s AND %(pmax)s
  AND l.id > %(dopo)s
ORDER BY l.id LIMIT %(pagina)s"""

GRUPPI_SQL = "SELECT id, make, model, fuel FROM listings WHERE id = ANY(%s)"


# ------------------------------------------------------------------ parametri

def minuti_massimi(spec_min=None, env: dict | None = None) -> float:
    """Tempo massimo: spec_min se dato, altrimenti CENSIMENTO_MIN, altrimenti 25 minuti."""
    env = os.environ if env is None else env
    for x in (spec_min, env.get("CENSIMENTO_MIN")):
        try:
            if x not in (None, ""):
                return max(1.0, float(x))
        except ValueError:
            pass
    return float(MINUTI_DEFAULT)


def campione_n(env: dict | None = None) -> int:
    """Auto del campione: CENSIMENTO_N (default 600, al massimo N_MAX)."""
    env = os.environ if env is None else env
    try:
        n = int(env.get("CENSIMENTO_N") or N_DEFAULT)
    except ValueError:
        n = N_DEFAULT
    return max(1, min(n, N_MAX))


def fascia_prezzo(p) -> str:
    p = int(p or 0)
    return next(nome for fino, nome in FASCE_PREZZO if p <= fino)


def carburante(f) -> str:
    f = str(f or "").lower()
    return "benzina" if "benzin" in f else "diesel" if "diesel" in f else "altro"


# ------------------------------------------------------------------ punto 1: conteggi

def _vecchia(at, ora: float) -> bool:
    if at is None:
        return True
    try:
        return at.timestamp() < ora - VECCHIA_GIORNI * 86400
    except Exception:
        return True


def conta(conn, g=None, ora: float | None = None) -> tuple[dict, list[dict]]:
    """Conteggi del punto 1 e righe della zona ancora da valutare (strati "mai" e "vecchia") per il campione."""
    from ..pricing import policy_v2 as P
    from ..web import sito_regole as SR
    g = g or SR.Regole.da_env()
    ora = time.time() if ora is None else ora
    par = {"pv": P.POLICY_VERSION, "ore": ORE_FRESCHI, "fb": bool(g.fb_sito), "prov": sorted(SR.PROVINCE_100KM),
           "anno": int(g.anno_min), "km": int(g.km_max), "pmin": PMIN, "pmax": PMAX, "pagina": PAGINA}
    c: dict = {k: Counter() for k in ("provincia", "fonte", "fascia_prezzo", "carburante", "telefono_ok",
                                      "categoria", "categoria_recente")}
    tot = Counter()
    da_valutare = []
    for r in _pagine(conn, par):
        pr = SR.provincia_di(r.get("province"), r.get("city"))
        if pr not in SR.PROVINCE_100KM:
            tot["fuori_zona_dal_comune"] += 1
            continue
        tot["totale"] += 1
        c["provincia"][pr] += 1
        c["fonte"][str(r.get("source"))] += 1
        c["fascia_prezzo"][fascia_prezzo(r.get("price_eur"))] += 1
        c["carburante"][carburante(r.get("fuel"))] += 1
        c["telefono_ok"][{True: "si", False: "no"}.get(r.get("telefono_ok"), "da_sapere")] += 1
        if r.get("ha_valuations"):
            tot["con_riga_valuations"] += 1
        cat = r.get("categoria")
        vecchia = _vecchia(r.get("valutata_at"), ora)
        if cat is None:
            tot["mai_valutate"] += 1
            strato = "mai"
        else:
            tot["valutate"] += 1
            c["categoria"][cat] += 1
            if vecchia:
                tot["valutate_da_oltre_7_giorni"] += 1
                strato = "vecchia"
            else:
                c["categoria_recente"][cat] += 1
                strato = None
        if cat is None and r.get("ha_valuations"):
            tot["mai_valutate_ma_con_riga_valuations"] += 1
        motivo = str(r.get("stage_reason") or "").split(" |")[0].strip()
        if motivo in SCARTI_PRIMA:
            tot["scartate_prima_" + motivo] += 1
            continue                                      # doppioni, truffe...: mai sul sito, non si campionano
        if strato:
            tot["da_valutare_" + strato] += 1
            da_valutare.append({"id": r["id"], "strato": strato, "categoria_salvata": cat, "provincia": pr,
                                "first_seen_at": r.get("first_seen_at")})
    out = dict(tot)
    for k, v in c.items():
        out[k] = dict(v.most_common())
    out["sul_sito_ora_stima"] = sum(c["categoria"].get(k, 0) for k in CATEGORIE_BUONE)
    out["filtri"] = {"ore_freschi": ORE_FRESCHI, "anno_min": int(g.anno_min), "km_max": int(g.km_max),
                     "prezzo": [PMIN, PMAX], "province": sorted(SR.PROVINCE_100KM), "facebook": bool(g.fb_sito),
                     "policy": P.POLICY_VERSION}
    return out, da_valutare


def _pagine(conn, par: dict):
    """Righe di CONTA_SQL a pagine di PAGINA (keyset su l.id): in memoria mai più di una pagina di righe leggere."""
    dopo = 0
    while True:
        righe = conn.execute(CONTA_SQL, dict(par, dopo=dopo)).fetchall()
        for r in righe:
            yield r
        if len(righe) < par["pagina"]:
            return
        dopo = righe[-1]["id"]
        conn.rollback()                       # niente transazione aperta tra una pagina e l'altra


# ------------------------------------------------------------------ punto 2: valutazione senza scrittura

def valuta_senza_scrivere(conn, r: dict, ing: dict, gruppo: dict, rg, cfg) -> tuple[dict, bool | None, dict]:
    """La valutazione di valutazione.valuta_e_scrivi per UN'auto, SENZA scrivere e con il giudice AI spento:
    (valutazione, telefono, t). Stessi passi: prepara → esclusa oppure valuta_con_ripiego (zona, ripiego, cascata)
    con i commercianti e le dotazioni del gruppo e la stessa soglia del sito."""
    from ..pricing import valutazione as VA
    from ..pricing.ricalcolo import valuta_con_ripiego
    from ..web import scovo
    t, motivo = VA.prepara(r, ing, rg)
    if motivo:
        return VA.esclusa(t, motivo, rg), t.get("telefono"), t       # niente coda dei danni: sola lettura
    t["commercianti"] = gruppo["commercianti"]
    if gruppo["dot"] and r["id"] in gruppo["dot_auto"]:
        t.update(gruppo["dot_auto"][r["id"]])
    v = valuta_con_ripiego(conn, t, gruppo["mercato"], scovo.soglia(int(r["price_eur"])), cfg, gruppo["cache"],
                           giudice=False)
    return v, t.get("telefono"), t


def carica_gruppo(conn, key: tuple, ids: list, rg) -> dict:
    """Confronti, commercianti e dotazioni del gruppo (marca, modello, carburante), come valuta_e_scrivi."""
    from ..pricing import dotazioni as DT
    from ..pricing import rivendita as RV
    from ..pricing.ricalcolo import MARKET_SQL
    from ..pricing.valutazione import _j
    from ..store import HOME_REGION_SQL
    usa_dot = bool(rg.trazione.attiva or rg.dotazioni.aggiusta)
    mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
               for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
    commercianti = RV.carica(conn, *key, HOME_REGION_SQL, rg) if rg.rivendita.attiva else None
    if usa_dot:
        mercato = DT.arricchisci(mercato, DT.carica_mercato(conn, mercato))
    return {"mercato": mercato, "commercianti": commercianti, "cache": {}, "dot": usa_dot,
            "dot_auto": DT.carica_ids(conn, list(ids)) if usa_dot else {}}


def giudica_sito(v: dict, tel, t: dict, r: dict, riga: dict, ing: dict, rg, g) -> dict:
    """Esito per il sito: margine del sito (calibrazione + difetto), buona / da trattare / segnali da controllare."""
    from ..pricing import perche_fuori as PF
    from ..web import scovo
    from ..web import sito_regole as SR
    p = int(r["price_eur"])
    difetto = int((ing["difetti"].get(r["id"]) or {}).get("costo") or 0)
    s = PF.sito(v, ing["fattori_calibra"], rg, difetto)
    cat, m = s["cat"], s["margine"]
    out = {"cat": cat, "M": s.get("M"), "margine": m, "soglia": scovo.soglia(p), "telefono": tel is True,
           "buona": False, "da_trattare": False, "segnali": []}
    if cat not in CATEGORIE_BUONE or tel is not True or m is None:
        return out
    if m >= out["soglia"]:
        out["buona"] = True
    elif PF.passa(cat, m, {"price_eur": p, "mileage_km": r.get("mileage_km"), "fuel": r.get("fuel"),
                           "year": r.get("year"), "first_seen_at": riga.get("first_seen_at")}):
        out["da_trattare"] = True
    else:
        return out
    testo = f"{r.get('title') or ''} {r.get('description') or ''}"
    it = {"pv": {"M": s.get("M"), "margine": m}, "prezzo": p, "incidentata": bool(t.get("danni_testo")),
          "_r": {"seller_type": "privato", "testo_truffa": SR.testo_truffa(testo), "tel_estero": SR.tel_estero(testo)}}
    try:
        out["segnali"] = SR.segnali_duri(it, g)
        if SR.sconto_da_controllare(it, g):
            out["segnali"].append("sconto_alto_da_controllare_ai")
    except Exception:
        pass
    return out


def valuta_campione(conn, campione: list[dict], rg, g, scadenza: float, memoria_mb: float) -> tuple[list, Counter]:
    """Valuta il campione un gruppo alla volta (poca memoria). Si ferma alla scadenza o se la memoria resta alta."""
    from .. import memoria as MEM
    from ..pricing import policy_v2 as P
    from ..pricing import valutazione as VA
    cfg = P.PolicyConfig(rg)
    per_id = {x["id"]: x for x in campione}
    st: Counter = Counter()
    esiti: list = []
    try:
        righe = conn.execute(GRUPPI_SQL, (list(per_id),)).fetchall()
    except Exception as e:
        _rollback(conn)
        st["errore_query:gruppi:" + type(e).__name__] += 1
        return esiti, st
    gruppi: dict = {}
    for x in righe:
        gruppi.setdefault((x["make"], x["model"], x["fuel"]), []).append(x["id"])
    for key in sorted(gruppi, key=lambda k: tuple(str(x) for x in k)):
        if time.time() >= scadenza:
            st["fermata:tempo"] = 1
            break
        if memoria_mb and MEM.rss_mb() > memoria_mb:
            gc.collect()
            MEM.rilascia("censimento", forza=True)
            if MEM.rss_mb() > memoria_mb:
                st["fermata:memoria"] = 1
                break
        pezzo = gruppi[key]
        try:
            complete = {x["id"]: dict(x) for x in conn.execute(
                f"SELECT {VA.COLONNE} FROM listings l WHERE l.id = ANY(%s)", (pezzo,)).fetchall()}
            ing = VA.ingressi(conn, pezzo)
            gr = carica_gruppo(conn, key, pezzo, rg)
        except Exception as e:
            _rollback(conn)
            st["errore_query:gruppo:" + type(e).__name__] += 1
            continue
        for lid in pezzo:
            r = complete.get(lid)
            if r is None:
                st["sparita_nel_frattempo"] += 1
                continue
            if time.time() >= scadenza:
                st["fermata:tempo"] = 1
                break
            try:
                v, tel, t = valuta_senza_scrivere(conn, r, ing, gr, rg, cfg)
                e = giudica_sito(v, tel, t, r, per_id[lid], ing, rg, g)
            except Exception as e:
                _rollback(conn)
                st["errore_auto:" + type(e).__name__] += 1
                continue
            e.update(strato=per_id[lid]["strato"], categoria_salvata=per_id[lid]["categoria_salvata"],
                     provincia=per_id[lid]["provincia"], marca=r.get("make"), modello=r.get("model"),
                     anno=r.get("year"), km=r.get("mileage_km"), prezzo=int(r["price_eur"]),
                     carburante=carburante(r.get("fuel")),
                     motivo_esclusione=v.get("motivo_esclusione") if v.get("categoria") == "esclusa" else None)
            esiti.append(e)
            time.sleep(PAUSA_S)
        del complete, ing, gr
    _rollback(conn)
    return esiti, st


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


# ------------------------------------------------------------------ riepilogo

def riassunto_campione(esiti: list, totali: dict) -> dict:
    """Buone nel campione per strato e proiezione sul totale da valutare di ogni strato."""
    out: dict = {"valutate": len(esiti), "categorie": dict(Counter(e["cat"] for e in esiti).most_common()),
                 "esclusioni": dict(Counter(e["motivo_esclusione"] for e in esiti if e["motivo_esclusione"]).most_common(8)),
                 "telefono_si": sum(1 for e in esiti if e["telefono"])}
    buone = [e for e in esiti if e["buona"]]
    tratta = [e for e in esiti if e["da_trattare"]]
    out["buone"] = len(buone)
    out["buone_senza_segnali"] = sum(1 for e in buone if not e["segnali"])
    out["buone_segnali"] = dict(Counter(s for e in buone for s in e["segnali"]).most_common())
    out["da_trattare"] = len(tratta)
    out["buone_per_fascia_prezzo"] = dict(Counter(fascia_prezzo(e["prezzo"]) for e in buone).most_common())
    out["buone_per_carburante"] = dict(Counter(e["carburante"] for e in buone).most_common())
    out["buone_gia_salvate_sul_sito"] = sum(1 for e in buone if e["categoria_salvata"] in CATEGORIE_BUONE)
    proi: dict = {}
    for strato in ("mai", "vecchia"):
        es = [e for e in esiti if e["strato"] == strato]
        tot = int(totali.get("da_valutare_" + strato) or 0)
        b = sum(1 for e in es if e["buona"])
        bn = sum(1 for e in es if e["buona"] and not e["segnali"])
        nuove = sum(1 for e in es if e["buona"] and e["categoria_salvata"] not in CATEGORIE_BUONE)
        t = sum(1 for e in es if e["da_trattare"])
        q = (lambda k: int(round(k / len(es) * tot)) if es else None)
        proi[strato] = {"totale": tot, "campione": len(es), "buone": b, "quota_pct": round(100 * b / len(es), 2) if es else None,
                        "stima_buone": q(b), "stima_buone_senza_segnali": q(bn), "stima_buone_nuove": q(nuove),
                        "stima_da_trattare": q(t)}
    out["proiezione"] = proi
    somma = lambda k: sum(p[k] or 0 for p in proi.values())         # noqa: E731
    out["stima_buone_non_valutate"] = somma("stima_buone")
    out["stima_buone_senza_segnali"] = somma("stima_buone_senza_segnali")
    out["stima_buone_nuove_non_sul_sito"] = somma("stima_buone_nuove")
    out["stima_da_trattare"] = somma("stima_da_trattare")
    return out


def esempi(esiti: list, quanti: int = MAX_ESEMPI) -> list[dict]:
    """Esempi anonimi delle buone (senza segnali prima, poi per margine): niente id, link, telefono o nomi."""
    buone = sorted((e for e in esiti if e["buona"]), key=lambda e: (bool(e["segnali"]), -int(e["margine"] or 0)))
    return [{"marca": e["marca"], "modello": e["modello"], "anno": e["anno"], "km": e["km"], "prezzo": e["prezzo"],
             "M": int(round(float(e["M"]))) if e.get("M") is not None else None, "margine": e["margine"],
             "soglia": e["soglia"], "cat": e["cat"], "provincia": e["provincia"], "strato": e["strato"],
             **({"segnali": e["segnali"]} if e["segnali"] else {})} for e in buone[:quanti]]


def esegui(conn, n: int | None = None, minuti: float | None = None, seme: int | None = None,
           memoria_mb: float | None = None, t0: float | None = None) -> dict:
    """Censimento completo (sola lettura, niente AI). Una riga di log CENSIMENTO; restituisce il riepilogo."""
    from .. import regole as RG
    from ..web import sito_regole as SR
    t0 = time.time() if t0 is None else t0
    minuti = minuti_massimi() if minuti is None else float(minuti)
    if memoria_mb is None:      # 19/11: 470 MB su 512, scalati sul limite vero del container (memoria.soglia)
        from .. import memoria as _MEM
        memoria_mb = _MEM.soglia(MEMORIA_MAX_MB)
    n = campione_n() if n is None else max(1, min(int(n), N_MAX))
    scadenza = t0 + minuti * 60
    rg, g = RG.regole(), SR.Regole.da_env()
    out: dict = {"n_richiesto": int(n), "minuti_max": minuti, "giudice_ai": "spento", "stima_rapida": "nessuna",
                 "limite": "senza giudice AI: le buone sono un tetto massimo (stima per eccesso)"}
    try:
        totali, da_valutare = conta(conn, g)
    except Exception as e:
        _rollback(conn)
        totali, da_valutare = {"errore": type(e).__name__ + ": " + str(e)[:150]}, []
    _rollback(conn)
    out["zona"] = totali
    out["secondi_conteggio"] = round(time.time() - t0)
    rnd = random.Random(seme if seme is not None else int(time.strftime("%Y%m%d")))
    campione = rnd.sample(da_valutare, min(int(n), len(da_valutare)))
    del da_valutare
    gc.collect()
    esiti, st = valuta_campione(conn, campione, rg, g, scadenza, memoria_mb)
    out["campione"] = {"scelte": len(campione), "per_strato": dict(Counter(x["strato"] for x in campione)),
                       **dict(st), **riassunto_campione(esiti, totali)}
    out["esempi"] = esempi(esiti)
    out["regole"] = rg.versione
    out["secondi"] = round(time.time() - t0)
    log.warning("CENSIMENTO %s", json.dumps(out, ensure_ascii=False, default=str, separators=(",", ":")))
    return out


def lavoro(spec: str) -> dict | None:
    """BREVO_IMPORT_LISTE=censimento:<etichetta> (nel sito, filo di brevo_liste): connessione propria (non il pool
    del sito) in SOLA LETTURA, chiusa alla fine. N da CENSIMENTO_N, minuti da CENSIMENTO_MIN."""
    import psycopg
    from psycopg.rows import dict_row

    from ..config import settings
    t0 = time.time()
    n, minuti = campione_n(), minuti_massimi()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.execute("SET statement_timeout = '600s'")
        conn.commit()
        try:
            return esegui(conn, n=n, minuti=minuti, t0=t0)
        finally:
            conn.rollback()
