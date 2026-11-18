"""Ricalcolo di prova su tutta la base (18/11 notte, cliente: "rifare il calcolo su tutte le auto che abbiamo usando
come base solo auto vendute e prezzi dei commercianti"; "un'auto da commerciante ha già il prezzo di vendita: noi
dobbiamo trovare prezzi più convincenti dei loro"; "non vale confrontare un'auto sana con una incidentata").

SOLA LETTURA (connessione propria READ ONLY), nessuna AI, nessuna scrittura di valutazioni: misura soltanto.
Per ogni gruppo marca/modello/alimentazione delle province del sito (sito_regole.PROVINCE_100KM):
- base COMMERCIANTI (D): annunci attivi di commercianti, ricontrollati negli ultimi FRESCHI_GIORNI giorni;
- base VENDUTE (V): annunci spariti negli ultimi VENDUTE_GIORNI giorni dopo almeno MIN_GIORNI_ONLINE giorni online
  (ultimo prezzo prima di sparire), di privati e commercianti;
- le due basi tengono SOLO auto pulite: danno "nessuno" o "sconosciuto", nessun problema dichiarato, nessun segno di
  prezzo finto (rata, civetta, importazione, dati incoerenti), nessuno scarto per truffa/doppione/dati incoerenti;
- un'auto si confronta solo con auto di anno ±1 e km ±25% (secondo giro: anno ±2, km ±40%), stessa potenza ±10% e
  stesso cambio quando entrambi sono noti; ogni prezzo è riportato all'anno e ai km dell'auto (8,5% per anno, 1,25%
  ogni 10.000 km, le svalutazioni delle regole); si tiene la mediana dopo aver tolto i prezzi fuori da 0,65–1,5 volte
  la mediana (annunci falsi o dati sbagliati); servono almeno N_MIN auto.

Due misure, scritte in UNA riga di log "BASE_VENDUTE {json}" (niente id, link, telefoni o nomi; esempi anonimi):
1. PRECISIONE sulle auto vendute negli ultimi 30 giorni (escludendo ogni volta l'auto stessa): errore della stima
   D, della stima V e della combinata (D se c'è, altrimenti V) rispetto all'ultimo prezzo prima di sparire;
   da confrontare con il calcolo attuale (perche_fuori del 17/11: errore mediano 10,7%, medio 17,2%, scostamento
   +4,5%, 47% entro il 10%);
2. AFFARI tra i privati attivi delle province del sito con anno >= ANNO_MIN e km <= KM_MAX: margine = stima − prezzo,
   buone = margine >= scovo.soglia(prezzo) (le stesse SOGLIE_PV), divise per provincia e per telefono.
Lancio: BREVO_IMPORT_LISTE=base_vendute:ora (gira subito nel sito)."""
from __future__ import annotations

import logging
import statistics
import time
from collections import Counter, defaultdict

log = logging.getLogger("dealfinder.pricing")

FRESCHI_GIORNI = 10
VENDUTE_GIORNI = 60
PRECISIONE_GIORNI = 30
MIN_GIORNI_ONLINE = 3
N_MIN = 3
ANNO_MIN = 2007
KM_MAX = 150000
SV_ANNO = 0.085
SV_10KM = 0.0125
TAGLIO = (0.65, 1.5)
LIVELLI = ((1, 0.25), (2, 0.40))
MINUTI_MAX = 45
ATTIVI_GIORNI = 5          # come il sito (scovo: last_seen_at > now() - 5 giorni)
PAUSA_S = 0.05             # dopo ogni gruppo: il calcolo gira nel processo del sito
BAD_FLAGS = {"leasing_o_rata", "prezzo_civetta", "importazione", "importata", "dati_incoerenti", "prezzo_finanziamento"}
BAD_STAGE = {"doppione", "truffa", "prezzo_finto", "dati_incoerenti"}

POOL_SQL = """
SELECT l.make, l.model, l.fuel, l.gearbox, l.power_kw, l.year, l.mileage_km, l.price_eur, l.price_flags,
       l.seller_type, l.status, l.province, l.telefono_ok, COALESCE(l.damage_class, 'sconosciuto') AS danno,
       COALESCE(l.problem_search, false) AS problema, COALESCE(l.damage_declared, false) AS dichiarato,
       btrim(split_part(COALESCE(l.stage_reason, ''), '|', 1)) AS stage,
       EXTRACT(EPOCH FROM (now() - COALESCE(l.last_checked_at, l.last_seen_at))) / 86400 AS giorni_visto,
       EXTRACT(EPOCH FROM (now() - l.disappeared_at)) / 86400 AS giorni_sparita,
       EXTRACT(EPOCH FROM (COALESCE(l.disappeared_at, now()) - l.first_seen_at)) / 86400 AS giorni_online
FROM listings l
WHERE l.province = ANY(%(prov)s) AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL
  AND l.price_eur BETWEEN 500 AND 400000 AND l.year IS NOT NULL AND l.mileage_km IS NOT NULL
  AND (l.status = 'attivo' OR (l.status = 'scomparso' AND l.disappeared_at > now() - make_interval(days => %(vg)s)))
ORDER BY lower(l.make), lower(l.model), l.fuel"""


def _pulita(r: dict) -> bool:
    return (r["danno"] in ("nessuno", "sconosciuto") and not r["problema"] and not r["dichiarato"]
            and r["stage"] not in BAD_STAGE
            and not r["stage"].startswith("analisi_manuale")
            and not (set(r["price_flags"] or ()) & BAD_FLAGS))


def _adatta(c: dict, t: dict) -> float:
    return float(c["price_eur"]) * (1 + SV_ANNO * (t["year"] - c["year"])) * \
        (1 + SV_10KM * (c["mileage_km"] - t["mileage_km"]) / 10000)


def _simile(c: dict, t: dict, anni: int, km: float) -> bool:
    if abs(c["year"] - t["year"]) > anni:
        return False
    base = max(t["mileage_km"], 20000)
    if abs(c["mileage_km"] - t["mileage_km"]) > km * base:
        return False
    if c["power_kw"] and t["power_kw"] and abs(c["power_kw"] - t["power_kw"]) > 0.10 * t["power_kw"]:
        return False
    if c["gearbox"] and t["gearbox"] and c["gearbox"] != t["gearbox"]:
        return False
    return True


def stima(base: list, t: dict, escludi=None) -> tuple[float | None, int]:
    for anni, km in LIVELLI:
        prezzi = [_adatta(c, t) for c in base if c is not escludi and _simile(c, t, anni, km)]
        prezzi = [p for p in prezzi if p > 0]
        if len(prezzi) < N_MIN:
            continue
        m = statistics.median(prezzi)
        tenuti = [p for p in prezzi if TAGLIO[0] * m <= p <= TAGLIO[1] * m]
        if len(tenuti) >= N_MIN:
            return statistics.median(tenuti), len(tenuti)
    return None, 0


def _err(lista: list) -> dict:
    if not lista:
        return {"n": 0}
    pct = [abs(s - v) / v * 100 for s, v in lista]
    sc = [(s - v) / v * 100 for s, v in lista]
    return {"n": len(lista), "errore_mediano_pct": round(statistics.median(pct), 1),
            "errore_medio_pct": round(statistics.mean(pct), 1), "scostamento_medio_pct": round(statistics.mean(sc), 1),
            "entro_10pct_quota": round(100 * sum(1 for p in pct if p <= 10) / len(pct), 1)}


def _gruppo(righe: list, st: dict, prec: dict, esempi: list) -> None:
    from ..web.scovo import soglia
    pulite = [r for r in righe if _pulita(r)]
    D = [r for r in pulite if r["status"] == "attivo" and r["seller_type"] == "commerciante"
         and (r["giorni_visto"] or 99) <= FRESCHI_GIORNI]
    V = [r for r in pulite if r["status"] == "scomparso" and (r["giorni_online"] or 0) >= MIN_GIORNI_ONLINE]
    for v in V:                                   # 1. precisione sulle vendute recenti
        if (v["giorni_sparita"] or 99) > PRECISIONE_GIORNI or v["year"] < ANNO_MIN or v["mileage_km"] > KM_MAX:
            continue
        vero = float(v["price_eur"])
        d, _ = stima(D, v)
        s, _ = stima(V, v, escludi=v)
        if d:
            prec["D"].append((d, vero))
        if s:
            prec["V"].append((s, vero))
        c = d or s
        if c:
            prec["combinata"].append((c, vero))
        else:
            st["vendute_senza_stima"] += 1
    for t in righe:                                # 2. affari tra i privati attivi
        if (t["status"] != "attivo" or t["seller_type"] == "commerciante" or not _pulita(t)
                or t["year"] < ANNO_MIN or t["mileage_km"] > KM_MAX or t["price_eur"] > 200000
                or (t["giorni_visto"] or 99) > ATTIVI_GIORNI):
            continue
        st["privati"] += 1
        d, nd = stima(D, t)
        s, nv = stima(V, t)
        r = d or s
        if not r:
            st["senza_stima"] += 1
            continue
        st["con_stima_D" if d else "con_stima_solo_V"] += 1
        prezzo = int(t["price_eur"])
        margine = r - prezzo
        sg = soglia(prezzo)
        if margine >= sg:
            if margine > 0.55 * r:                 # oltre metà sotto la base: quasi sempre dati sbagliati o truffa
                st["buone_troppo_belle"] += 1
                continue
            st["buone"] += 1
            st["buone_tel_si" if t["telefono_ok"] is True else
               ("buone_tel_no" if t["telefono_ok"] is False else "buone_tel_da_sapere")] += 1
            st["prov:" + (t["province"] or "?")] += 1
            st["base:" + ("commercianti" if d else "vendute")] += 1
            if len(esempi) < 25:
                esempi.append({"auto": f'{t["make"]} {t["model"]}', "anno": t["year"], "km": t["mileage_km"],
                               "carb": t["fuel"], "prezzo": prezzo, "stima_commercianti": round(d) if d else None,
                               "n_comm": nd, "stima_vendute": round(s) if s else None, "n_vend": nv,
                               "margine": round(margine), "soglia": sg, "prov": t["province"]})


def lavoro(spec: str = "") -> dict:
    import psycopg
    from psycopg.rows import dict_row

    from ..config import settings
    from ..web.sito_regole import PROVINCE_100KM
    t0 = time.time()
    st: Counter = Counter()
    prec: dict = defaultdict(list)
    esempi: list = []
    fermata = None
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '55min'")
        with conn.cursor(name="base_vendute") as cur:
            cur.itersize = 5000
            cur.execute(POOL_SQL, {"prov": sorted(PROVINCE_100KM), "vg": VENDUTE_GIORNI})
            chiave, righe = None, []
            for r in cur:
                k = (r["make"].lower(), r["model"].lower(), r["fuel"])
                if k != chiave and righe:
                    _gruppo(righe, st, prec, esempi)
                    st["gruppi"] += 1
                    righe = []
                    time.sleep(PAUSA_S)
                    if time.time() - t0 > MINUTI_MAX * 60:
                        fermata = "tempo"
                        break
                chiave = k
                st["righe"] += 1
                righe.append(r)
            if righe and not fermata:
                _gruppo(righe, st, prec, esempi)
                st["gruppi"] += 1
    esempi.sort(key=lambda e: -e["margine"])
    return {"fermata": fermata, "secondi": round(time.time() - t0), "province": sorted(PROVINCE_100KM),
            "precisione_vendute_30g": {k: _err(v) for k, v in prec.items()},
            "riferimento_calcolo_attuale": {"errore_mediano_pct": 10.7, "errore_medio_pct": 17.2,
                                            "scostamento_medio_pct": 4.5, "entro_10pct_quota": 47.1},
            "note": ["annunci senza provincia esclusi (come perche_fuori)",
                     "stima commercianti un po' ottimista nella precisione: la stessa auto può essere ancora attiva "
                     "da un commerciante su un altro portale"],
            "conteggi": dict(st), "esempi": esempi[:25]}
