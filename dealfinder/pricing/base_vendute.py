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
Lancio: BREVO_IMPORT_LISTE=base_vendute:ora (gira subito nel sito).

19/11 (cliente: "gravissimo errore di gonfiamento prezzi"): la stessa misura anche per il valore PRUDENTE del sito
(valore_base.stima_prudente, regole.ValoreBase) accanto al calcolo del 18/11 (mediane, "sito_n5"), sulle STESSE
vendute, divisa per fascia di prezzo, età e venditore della venduta ("prudente|fascia:<5k", ...); il rapporto
commercianti/vendute (D/V) dove ci sono entrambe e il rapporto 40° percentile/mediana, per fascia ed età; le auto
buone tra i privati col valore prudente ("buone_prudente"). Lancio: base_vendute:<etichetta nuova>.
19/11 pomeriggio (regola mediana-1911b, base_vendute:1911b): anche per fascia della STIMA del 18/11
("prudente|fascia_stima:...", la fascia che usa lo sconto) e per età × fascia della stima ("prudente|eta_fascia:...").
20/11 (proprietario: recuperare i dati insufficienti "in qualche modo", MA "non gonfiare i prezzi"), B1 = SOLA MISURA
della stima di ripiego (valore_base.stima_ripiego, prove di valore_base.RIPIEGO_PROVE: unione, largo, largo4):
- sulle vendute che il valore prudente NON stima: "ripiego|<prova>", "ripiego|<prova>|fascia_stima:<fascia>" (fascia
  del valore grezzo del ripiego, la stessa delle coppie livello/fascia da attivare), "ripiego|<prova>|eta:<fascia>",
  SENZA sconto extra;
- tra i privati attivi senza valore prudente: conteggi "ripiego_stimate:<prova>:<fascia>" e
  "ripiego_buone:<prova>:<fascia>" (margine ≥ soglia e ≤ 40% del valore: quante entrerebbero).
Finestra delle vendute misurate dall'etichetta: "...g60" = ultimi 60 giorni (default 30, massimo VENDUTE_GIORNI);
nel risultato "finestra_giorni". Lancio: base_vendute:rip30 poi base_vendute:rip60g60."""
from __future__ import annotations

import logging
import re
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

N_MIN_SITO = 5             # = valore_base.N_MIN_SITO (qui senza importarlo: modulo di sola misura)

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


def percentile(valori, q: float) -> float:
    """Percentile q (0–1) con interpolazione lineare (q = 0,5 = mediana, come statistics.median)."""
    v = sorted(valori)
    if not v:
        raise ValueError("nessun valore")
    pos = (len(v) - 1) * min(1.0, max(0.0, float(q)))
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def stima_dettaglio(base: list, t: dict, escludi=None, n_min: int | None = None, q: float = 0.5,
                    n_min_livelli: tuple | None = None) -> dict | None:
    """Come stima(), con i confronti tenuti: {"valore", "n", "livello" (1 o 2), "tenuti": [(confronto, prezzo
    riportato all'auto)]} oppure None. n_min: confronti minimi (default N_MIN; il sito usa valore_base.N_MIN_SITO).
    19/11: q = percentile dei tenuti (default 0,5 = mediana, la misura di prima); n_min_livelli = minimo per ogni
    livello (il sito chiede più confronti al livello 2, più largo). Il taglio 0,65–1,5 resta sulla mediana."""
    n_min = N_MIN if n_min is None else n_min
    for livello, (anni, km) in enumerate(LIVELLI, start=1):
        nm = max(n_min, n_min_livelli[livello - 1]) if n_min_livelli else n_min
        coppie = [(c, _adatta(c, t)) for c in base if c is not escludi and _simile(c, t, anni, km)]
        coppie = [(c, p) for c, p in coppie if p > 0]
        if len(coppie) < nm:
            continue
        m = statistics.median(p for _, p in coppie)
        tenuti = [(c, p) for c, p in coppie if TAGLIO[0] * m <= p <= TAGLIO[1] * m]
        if len(tenuti) >= nm:
            prezzi = [p for _, p in tenuti]
            valore = statistics.median(prezzi) if q == 0.5 else percentile(prezzi, q)
            return {"valore": valore, "n": len(tenuti), "livello": livello, "tenuti": tenuti,
                    "mediana": statistics.median(prezzi)}
    return None


def stima(base: list, t: dict, escludi=None, n_min: int | None = None) -> tuple[float | None, int]:
    d = stima_dettaglio(base, t, escludi, n_min)
    return (d["valore"], d["n"]) if d else (None, 0)


def _err(lista: list) -> dict:
    if not lista:
        return {"n": 0}
    pct = [abs(s - v) / v * 100 for s, v in lista]
    sc = [(s - v) / v * 100 for s, v in lista]
    return {"n": len(lista), "errore_mediano_pct": round(statistics.median(pct), 1),
            "errore_medio_pct": round(statistics.mean(pct), 1), "scostamento_medio_pct": round(statistics.mean(sc), 1),
            "entro_10pct_quota": round(100 * sum(1 for p in pct if p <= 10) / len(pct), 1)}


def fascia(prezzo) -> str:
    p = float(prezzo or 0)
    return "<5k" if p < 5000 else "5-10k" if p < 10000 else "10-15k" if p < 15000 else "15-20k" if p < 20000 else ">20k"


def fascia_eta(anno) -> str:
    from .valore_base import eta_auto
    e = eta_auto(anno)
    return "0-5" if e <= 5 else "6-10" if e <= 10 else ">10"


def _rapporti(r: dict) -> dict:
    out = {}
    for k, v in sorted(r.items()):
        if v:
            out[k] = {"n": len(v), "mediana": round(statistics.median(v), 3), "media": round(statistics.mean(v), 3)}
    return out


def finestra(spec: str = "") -> int:
    """Giorni delle vendute misurate, dall'etichetta del lancio: "base_vendute:rip60g60" = 60; senza "g<numero>" in
    fondo = PRECISIONE_GIORNI (30). Tra 1 e VENDUTE_GIORNI (le vendute più vecchie non sono nella base)."""
    etichetta = str(spec or "").rsplit(":", 1)[-1].strip().lower()
    m = re.search(r"(?:^|[\d_\-])g(\d{1,3})$", etichetta)
    if not m:
        return PRECISIONE_GIORNI
    return max(1, min(VENDUTE_GIORNI, int(m.group(1))))


def _ripiego_venduta(VB, D: list, V: list, v: dict, vb, vero: float, st: dict, prec: dict) -> None:
    """20/11 (B1, sola misura): venduta SENZA valore prudente, stimata con ogni prova di ripiego (esclusa l'auto
    stessa), senza sconto extra."""
    st["ripiego_vendute_provate"] += 1
    for prova in VB.RIPIEGO_PROVE:
        rr = VB.stima_ripiego(D, V, v, vb, escludi=v, solo=(prova,))
        if not rr["valore"]:
            continue
        coppia = (rr["valore"], vero)
        prec["ripiego|" + prova].append(coppia)
        prec["ripiego|" + prova + "|fascia_stima:" + rr["fascia"]].append(coppia)
        prec["ripiego|" + prova + "|eta:" + fascia_eta(v["year"])].append(coppia)


def _ripiego_privato(VB, D: list, V: list, t: dict, vb, prezzo: int, sg: int, st: dict) -> None:
    """20/11 (B1, sola misura): privato attivo SENZA valore prudente; quante avrebbero una stima di ripiego e quante
    sarebbero buone (margine ≥ soglia e non oltre RIPIEGO_MARGINE_MAX del valore), per prova e fascia della stima."""
    st["privati_senza_stima_prudente"] += 1
    for prova in VB.RIPIEGO_PROVE:
        rr = VB.stima_ripiego(D, V, t, vb, solo=(prova,))
        if not rr["valore"]:
            continue
        k = prova + ":" + rr["fascia"]
        st["ripiego_stimate:" + k] += 1
        m = rr["valore"] - prezzo
        if sg <= m <= VB.RIPIEGO_MARGINE_MAX * rr["valore"]:
            st["ripiego_buone:" + k] += 1


def _gruppo(righe: list, st: dict, prec: dict, esempi: list, rapp: dict | None = None,
            giorni: int = PRECISIONE_GIORNI) -> None:
    from .. import regole as RG
    from ..web.scovo import soglia
    from . import valore_base as VB
    vb = RG.regole().valore_base
    rapp = rapp if rapp is not None else defaultdict(list)
    pulite = [r for r in righe if _pulita(r)]
    D = [r for r in pulite if r["status"] == "attivo" and r["seller_type"] == "commerciante"
         and (r["giorni_visto"] or 99) <= FRESCHI_GIORNI]
    V = [r for r in pulite if r["status"] == "scomparso" and (r["giorni_online"] or 0) >= MIN_GIORNI_ONLINE]
    for v in V:                                   # 1. precisione sulle vendute recenti
        if (v["giorni_sparita"] or 99) > giorni or v["year"] < ANNO_MIN or v["mileage_km"] > KM_MAX:
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
        # 18/11 sera: la stessa misura con il minimo del SITO (valore_base.N_MIN_SITO = 5) e, a parte, le sole vendute
        # che il sito oggi NON stima ma che con commercianti e vendute INSIEME (≥ 5) avrebbero una stima: l'eventuale
        # allargamento "unendo le due basi" si decide solo se questo errore non è peggiore di quello del sito
        ds, _ = stima(D, v, n_min=N_MIN_SITO)
        ss, _ = stima(V, v, escludi=v, n_min=N_MIN_SITO)
        # 19/11: valore prudente sulle stesse vendute (prima/dopo per fascia, età e venditore della venduta)
        sp = VB.stima_prudente(D, V, v, vb, escludi=v)
        if not sp["valore"]:
            _ripiego_venduta(VB, D, V, v, vb, vero, st, prec)
        chiavi = ("fascia:" + fascia(vero), "eta:" + fascia_eta(v["year"]),
                  "venduta_da:" + ("commerciante" if v["seller_type"] == "commerciante" else "privato"))
        if ds or ss:
            # 19/11 pomeriggio (1911b): anche per fascia della STIMA del 18/11 (come lo sconto per fascia, che conosce
            # solo la stima: la fascia del prezzo vero esagera il gonfiamento delle economiche) e per età × fascia
            chiavi = chiavi + ("fascia_stima:" + fascia(ds or ss),
                               "eta_fascia:" + fascia_eta(v["year"]) + "|" + fascia(ds or ss))
            prec["sito_n5"].append((ds or ss, vero))
            if sp["valore"]:
                prec["prima_su_stesse"].append((ds or ss, vero))
                prec["prudente"].append((sp["valore"], vero))
                for k in chiavi:
                    prec["prima|" + k].append((ds or ss, vero))
                    prec["prudente|" + k].append((sp["valore"], vero))
            else:
                st["vendute_senza_stima_prudente"] += 1
                for k in chiavi:
                    prec["prima_senza_prudente|" + k].append((ds or ss, vero))
            if ds and ss:
                for k in chiavi[:2] + chiavi[3:4]:
                    rapp["D/V|" + k].append(ds / ss)
            d5 = stima_dettaglio(D, v, n_min=N_MIN_SITO)
            if d5:
                rapp["p40/mediana|" + chiavi[0]].append(percentile([p for _, p in d5["tenuti"]], .4) / d5["valore"])
            if d5 and d5["livello"] == 2:
                prec["prima_livello2"].append((ds or ss, vero))
        else:
            u, _ = stima(D + V, v, escludi=v, n_min=N_MIN_SITO)
            if u:
                prec["sito_n5_solo_unendo"].append((u, vero))
            else:
                st["vendute_sito_senza_stima"] += 1
    for t in righe:                                # 2. affari tra i privati attivi
        if (t["status"] != "attivo" or t["seller_type"] == "commerciante" or not _pulita(t)
                or t["year"] < ANNO_MIN or t["mileage_km"] > KM_MAX or t["price_eur"] > 200000
                or (t["giorni_visto"] or 99) > ATTIVI_GIORNI):
            continue
        st["privati"] += 1
        d, nd = stima(D, t)
        s, nv = stima(V, t)
        r = d or s
        prezzo = int(t["price_eur"])
        sp = VB.stima_prudente(D, V, t, vb)
        if not sp["valore"]:
            _ripiego_privato(VB, D, V, t, vb, prezzo, soglia(prezzo), st)
        if not r:
            st["senza_stima"] += 1
            continue
        st["con_stima_D" if d else "con_stima_solo_V"] += 1
        if sp["valore"]:
            mp = sp["valore"] - prezzo
            if mp >= soglia(prezzo) and mp <= 0.55 * sp["valore"]:
                st["buone_prudente"] += 1
                st["buone_prudente:" + fascia(prezzo)] += 1
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
    """spec = "base_vendute:<etichetta>": l'etichetta che finisce con g<numero> misura le vendute di quei giorni."""
    import psycopg
    from psycopg.rows import dict_row

    from ..config import settings
    from ..web.sito_regole import PROVINCE_100KM
    from . import valore_base as VB
    t0 = time.time()
    giorni = finestra(spec)
    st: Counter = Counter()
    prec: dict = defaultdict(list)
    rapp: dict = defaultdict(list)
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
                    _gruppo(righe, st, prec, esempi, rapp, giorni)
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
                _gruppo(righe, st, prec, esempi, rapp, giorni)
                st["gruppi"] += 1
    esempi.sort(key=lambda e: -e["margine"])
    return {"fermata": fermata, "secondi": round(time.time() - t0), "province": sorted(PROVINCE_100KM),
            "finestra_giorni": giorni, "regola_ripiego": VB.REGOLA_RIPIEGO,
            # il nome della chiave resta quello di sempre: le vendute misurate sono quelle di "finestra_giorni"
            "precisione_vendute_30g": {k: _err(v) for k, v in sorted(prec.items())},
            "rapporti": _rapporti(rapp),
            "riferimento_calcolo_attuale": {"errore_mediano_pct": 10.7, "errore_medio_pct": 17.2,
                                            "scostamento_medio_pct": 4.5, "entro_10pct_quota": 47.1},
            "note": ["annunci senza provincia esclusi (come perche_fuori)",
                     "stima commercianti un po' ottimista nella precisione: la stessa auto può essere ancora attiva "
                     "da un commerciante su un altro portale"],
            "conteggi": dict(st), "esempi": esempi[:25]}
