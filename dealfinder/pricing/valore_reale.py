"""Valore reale di mercato (2/11 notte, cliente: "la soluzione migliore per valutare le auto e i prezzi di mercato,
completa, niente lasciato al caso").

Idea: il prezzo giusto non è la media dei prezzi CHIESTI di poche auto gemelle, ma il prezzo a cui auto così si
VENDONO. Dalla storia di ogni annuncio (comparsa, ribassi, sparizione) si ricavano le auto vendute; per ogni modello
(marca, modello, alimentazione) si stima un modello di prezzo su TUTTI gli annunci del modello:

    log(prezzo) = a + b·età + c·età² + d·log(km) + e·log(kW) + cambio auto + allestimento sportivo + carrozzeria
                  + venditore commerciante + "venduta" (differenza tra prezzo chiesto e prezzo di vendita)

con pesi: auto vendute in fretta ×3 (prezzo vero), annunci attivi ×1, annunci fermi da molto o con tanti ribassi ×0,5
(prezzo troppo alto). Stima robusta (si tolgono gli anomali e si rifà il calcolo), regolarizzata (ridge).

Per ogni auto si ottiene: V = prezzo a cui si vende da privato, P25 (il 25% delle vendite sotto), sigma (errore tipico
del modello su quel gruppo, misurato). Le regole del cliente restano: rivendita = min(V × 0,95, P25), soglie di
margine, gemelle come controllo (vedi integrazione in policy).

Prova (backtest): per ogni modello si tolgono dall'addestramento le auto vendute negli ultimi giorni e si confronta
la stima con il loro prezzo finale; si misura lo stesso errore per il metodo attuale (M salvato nella valutazione
quando l'auto era online). Solo conteggi e numeri aggregati nei log, nessun dato personale.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np

log = logging.getLogger("valore_reale")

ANNO_RIF = datetime.now(timezone.utc).year + (datetime.now(timezone.utc).month - 1) / 12

_SPORT = re.compile(r"\bm[\s-]?sport\b|\bs[\s-]?line\b|\brs[\s-]?line\b|\br[\s-]?line\b|\bamg\b|\bgt[\s-]?line\b|"
                    r"\bst[\s-]?line\b|\bn[\s-]?line\b|\bfr\b|\bgts\b|\bvignale\b|\bcompetizione\b|\bveloce\b|\babarth\b", re.I)
_SW = re.compile(r"sports? ?tourer|station ?wagon|sportswagon|\bsw\b|touring|avant|variant|wagon|kombi|shooting ?brake", re.I)
_CAB = re.compile(r"cabrio|cabriolet|roadster|spider|spyder|convertible", re.I)
_COUPE = re.compile(r"coup[eé]", re.I)
_AUTO = re.compile(r"^(auto|semi|sequ|dsg|cvt)", re.I)
_FLAG_NO = {"leasing_o_rata", "importazione", "prezzo_civetta", "dati_incoerenti", "solo_finanziamento"}

# colonne lette per ogni annuncio del gruppo (niente dati del venditore)
SQL_GRUPPO = """
SELECT l.id, l.price_eur, l.year, l.mileage_km, l.power_kw, l.gearbox, l.version_raw, l.title, l.seller_type,
       l.status, l.first_seen_at, l.last_seen_at, l.disappeared_at, l.damage_class, l.price_flags, l.stage_reason,
       l.problem_search, upper(l.province) AS prov,
       (SELECT count(*) FROM price_events e WHERE e.listing_id = l.id AND e.event = 'prezzo') AS ribassi,
       (SELECT e.price_eur FROM price_events e WHERE e.listing_id = l.id AND e.event = 'apparso'
         ORDER BY e.at LIMIT 1) AS prezzo_iniziale,
       EXISTS (SELECT 1 FROM price_events e WHERE e.listing_id = l.id AND e.event = 'riapparso') AS riapparso
FROM listings l
WHERE l.make = %s AND l.model = %s AND l.fuel = %s AND l.price_eur BETWEEN 500 AND 400000
  AND l.year BETWEEN 1990 AND 2027 AND l.mileage_km IS NOT NULL
  AND (l.status = 'attivo' OR (l.status = 'scomparso' AND l.disappeared_at > now() - interval '75 days'))
  AND l.last_seen_at > now() - interval '75 days'
"""


def _giorni(a, b):
    if not a or not b:
        return None
    return (b - a).total_seconds() / 86400


def pulisci(r: dict) -> bool:
    """Annunci che non dicono il prezzo vero di un'auto sana: fuori dall'addestramento."""
    from .policy_v2 import DANNEGGIATE
    if (r.get("damage_class") or "sconosciuto") in DANNEGGIATE or r.get("problem_search"):
        return False
    if set(r.get("price_flags") or []) & _FLAG_NO:
        return False
    if r.get("stage_reason") in ("doppione", "truffa", "prezzo_finto"):
        return False
    if r.get("year") and r.get("mileage_km") is not None and int(r["mileage_km"]) == int(r["year"]):
        return False
    return True


def etichetta(r: dict, now: datetime) -> tuple[str, float]:
    """('venduta' | 'attiva' | 'ferma', peso). Venduta = sparita entro 60 giorni dalla comparsa e mai riapparsa."""
    if r["status"] == "scomparso" and not r.get("riapparso"):
        g = _giorni(r.get("first_seen_at"), r.get("disappeared_at"))
        if g is not None and g <= 60:
            return "venduta", 3.0 if g <= 21 else 2.0
        return "ferma", 0.5
    g = _giorni(r.get("first_seen_at"), now)
    if (g is not None and g > 45) or (r.get("ribassi") or 0) >= 3:
        return "ferma", 0.5            # online da tanto o con tanti ribassi: prezzo chiesto troppo alto
    return "attiva", 1.0


def caratteristiche(r: dict, kw_med: float) -> list[float]:
    eta = max(0.0, ANNO_RIF - int(r["year"]))
    km = max(0, int(r["mileage_km"]))
    kw = float(r["power_kw"]) if r.get("power_kw") else kw_med
    txt = f"{r.get('version_raw') or ''} {r.get('title') or ''}"
    return [1.0, eta, eta * eta, math.log1p(km / 1000.0), math.log(max(kw, 20.0)),
            1.0 if (r.get("gearbox") and _AUTO.search(str(r["gearbox"]))) else 0.0,
            1.0 if _SPORT.search(txt) else 0.0,
            1.0 if _SW.search(txt) else 0.0,
            1.0 if _CAB.search(txt) else 0.0,
            1.0 if _COUPE.search(txt) and not _CAB.search(txt) else 0.0,
            1.0 if r.get("seller_type") == "commerciante" else 0.0,
            0.0 if r.get("power_kw") else 1.0]          # kW mancante (usata la mediana del gruppo)


NOMI = ["costante", "eta", "eta2", "log_km", "log_kw", "automatico", "sportivo", "sw", "cabrio", "coupe",
        "commerciante", "kw_mancante", "venduta"]


def adatta(X: np.ndarray, y: np.ndarray, w: np.ndarray, ridge: float = 1.0, giri: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Minimi quadrati pesati con ridge (non sulla costante) e 3 giri robusti (pesi Huber sui residui)."""
    p = X.shape[1]
    lam = np.eye(p) * ridge
    lam[0, 0] = 0.0
    ww = w.copy()
    beta = np.zeros(p)
    for _ in range(giri):
        Xw = X * ww[:, None]
        try:
            beta = np.linalg.solve(X.T @ Xw + lam, Xw.T @ y)
        except np.linalg.LinAlgError:
            beta = np.linalg.lstsq(X.T @ Xw + lam, Xw.T @ y, rcond=None)[0]
        res = y - X @ beta
        s = 1.4826 * np.median(np.abs(res - np.median(res))) or 0.1
        k = 1.5 * s
        hub = np.where(np.abs(res) <= k, 1.0, k / np.maximum(np.abs(res), 1e-9))
        ww = w * hub
    res = y - X @ beta
    return beta, res


class ModelloGruppo:
    def __init__(self, chiave, beta, sigma, n, n_vendute, kw_med, premio_venduta):
        self.chiave, self.beta, self.sigma, self.n, self.n_vendute = chiave, beta, sigma, n, n_vendute
        self.kw_med, self.premio_venduta = kw_med, premio_venduta

    def stima(self, r: dict) -> dict:
        x = np.array(caratteristiche(dict(r, seller_type="privato"), self.kw_med) + [1.0])   # venduta, da privato
        mu = float(x @ self.beta)
        v = math.exp(mu)
        return {"V": int(round(v)), "P25": int(round(v * math.exp(-0.674 * self.sigma))),
                "sigma": round(self.sigma, 3), "n": self.n, "n_vendute": self.n_vendute,
                "premio_venduta": round(self.premio_venduta, 3)}


def costruisci(righe: list[dict], chiave, escludi: set | None = None, now=None) -> ModelloGruppo | None:
    now = now or datetime.now(timezone.utc)
    rr = [r for r in righe if pulisci(r) and (not escludi or r["id"] not in escludi)]
    if len(rr) < int(os.environ.get("VR_MIN_GRUPPO", "40")):
        return None
    kws = [float(r["power_kw"]) for r in rr if r.get("power_kw")]
    kw_med = float(np.median(kws)) if kws else 60.0
    X, y, w, vend = [], [], [], 0
    for r in rr:
        tipo, peso = etichetta(r, now)
        vend += tipo == "venduta"
        X.append(caratteristiche(r, kw_med) + [1.0 if tipo == "venduta" else 0.0])
        y.append(math.log(int(r["price_eur"])))
        w.append(peso)
    X, y, w = np.array(X), np.array(y), np.array(w)
    # colonne costanti (es. nessuna cabrio nel gruppo): si azzerano per non disturbare
    for j in range(1, X.shape[1]):
        if np.all(X[:, j] == X[0, j]):
            X[:, j] = 0.0
    beta, res = adatta(X, y, w)
    sigma = float(np.sqrt(np.average(res ** 2, weights=w)))
    sigma = min(max(sigma, 0.05), 0.6)
    premio = float(beta[-1]) if vend >= 5 else 0.0
    if vend < 5:
        beta[-1] = 0.0          # troppo poche vendute: la differenza chiesto/venduto non si stima
    return ModelloGruppo(chiave, beta, sigma, len(rr), vend, kw_med, premio)


def gruppi_principali(conn, limite: int) -> list[tuple]:
    return [(r["make"], r["model"], r["fuel"]) for r in conn.execute(
        """SELECT make, model, fuel, count(*) AS n FROM listings
           WHERE status='attivo' AND make IS NOT NULL AND model IS NOT NULL AND fuel IS NOT NULL
             AND last_seen_at > now() - interval '30 days'
           GROUP BY 1,2,3 HAVING count(*) >= 60 ORDER BY n DESC LIMIT %s""", (limite,)).fetchall()]


def prova(conn, limite_gruppi: int = 400, giorni_prova: int = 14) -> dict:
    """Backtest: auto vendute negli ultimi `giorni_prova` giorni tolte dall'addestramento e stimate.
    Errore del metodo nuovo (V) e di quello attuale (M dell'ultima valutazione salvata quando era online)."""
    now = datetime.now(timezone.utc)
    err_nuovo, err_vecchio, bias_nuovo, bias_vecchio = [], [], [], []
    err_nuovo_stesse = []          # errore del nuovo sulle STESSE auto che hanno una stima del metodo attuale
    per_fascia = defaultdict(lambda: {"n": 0, "nuovo": [], "vecchio": [], "nuovo_stesse": []})
    copertura = Counter()
    gruppi = gruppi_principali(conn, limite_gruppi)
    for gi, (mk, md, fu) in enumerate(gruppi):
        righe = [dict(r) for r in conn.execute(SQL_GRUPPO, (mk, md, fu)).fetchall()]
        prova_ids = {r["id"] for r in righe if r["status"] == "scomparso" and not r.get("riapparso") and pulisci(r)
                     and r.get("disappeared_at") and _giorni(r["disappeared_at"], now) <= giorni_prova
                     and (_giorni(r.get("first_seen_at"), r["disappeared_at"]) or 999) <= 60}
        if len(prova_ids) < 3:
            copertura["gruppi_senza_vendute_recenti"] += 1
            continue
        m = costruisci(righe, (mk, md, fu), escludi=prova_ids, now=now)
        if not m:
            copertura["gruppi_piccoli"] += 1
            continue
        vecchi = {r["listing_id"]: r for r in conn.execute(
            """SELECT DISTINCT ON (listing_id) listing_id, (dati->>'M')::float AS m, (dati->>'n')::int AS n
               FROM valutazioni_policy WHERE listing_id = ANY(%s) ORDER BY listing_id, computed_at DESC""",
            (list(prova_ids),)).fetchall()}
        for r in righe:
            if r["id"] not in prova_ids:
                continue
            vero = float(r["price_eur"])
            s = m.stima(r)
            if r.get("seller_type") == "commerciante":
                continue                              # si misura sul mercato dei privati, come sul sito
            e_n = (s["V"] - vero) / vero
            err_nuovo.append(abs(e_n)); bias_nuovo.append(e_n)
            fascia = "<5k" if vero < 5000 else "5-10k" if vero < 10000 else "10-20k" if vero < 20000 else ">20k"
            per_fascia[fascia]["n"] += 1
            per_fascia[fascia]["nuovo"].append(abs(e_n))
            vv = vecchi.get(r["id"])
            if vv and vv.get("m"):
                e_v = (float(vv["m"]) - vero) / vero
                err_vecchio.append(abs(e_v)); bias_vecchio.append(e_v)
                err_nuovo_stesse.append(abs(e_n))
                per_fascia[fascia]["vecchio"].append(abs(e_v))
                per_fascia[fascia]["nuovo_stesse"].append(abs(e_n))
                copertura["con_stima_vecchia"] += 1
            else:
                copertura["senza_stima_vecchia"] += 1
        if gi % 50 == 0:
            log.warning("VALORE_REALE prova avanzamento %d/%d gruppi, %d auto provate", gi, len(gruppi), len(err_nuovo))
    md_ = lambda a: round(float(np.median(a)) * 100, 1) if a else None
    return {"gruppi": len(gruppi), "auto_provate": len(err_nuovo),
            "errore_mediano_nuovo_%": md_(err_nuovo), "errore_mediano_attuale_%": md_(err_vecchio),
            "STESSE_AUTO_nuovo_%": md_(err_nuovo_stesse), "STESSE_AUTO_attuale_%": md_(err_vecchio),
            "STESSE_AUTO_n": len(err_nuovo_stesse),
            "STESSE_AUTO_entro_10%_nuovo": round(float(np.mean(np.array(err_nuovo_stesse) <= .10)) * 100, 1) if err_nuovo_stesse else None,
            "entro_10%_nuovo": round(float(np.mean(np.array(err_nuovo) <= .10)) * 100, 1) if err_nuovo else None,
            "entro_10%_attuale": round(float(np.mean(np.array(err_vecchio) <= .10)) * 100, 1) if err_vecchio else None,
            "distorsione_nuovo_%": md_(bias_nuovo) if bias_nuovo else None,
            "distorsione_attuale_%": round(float(np.median(bias_vecchio)) * 100, 1) if bias_vecchio else None,
            "per_fascia": {k: {"n": v["n"], "nuovo_%": md_(v["nuovo"]), "attuale_%": md_(v["vecchio"]),
                               "n_attuale": len(v["vecchio"]), "nuovo_stesse_%": md_(v["nuovo_stesse"])}
                           for k, v in sorted(per_fascia.items())},
            "copertura": dict(copertura)}


def calcola_e_salva(conn, limite_gruppi: int = 600, prov_zona: list | None = None) -> dict:
    """Addestra i modelli sui gruppi principali e salva V/P25/sigma per gli annunci ATTIVI di privati della zona
    (tabella valore_reale), senza toccare il sito: serve alla prova in parallelo e poi alla valutazione."""
    conn.execute("""CREATE TABLE IF NOT EXISTS valore_reale (
        listing_id BIGINT PRIMARY KEY, v INTEGER, p25 INTEGER, sigma REAL, n INTEGER, n_vendute INTEGER,
        premio_venduta REAL, gruppo TEXT, computed_at TIMESTAMPTZ DEFAULT now())""")
    conn.commit()
    now = datetime.now(timezone.utc)
    st = Counter()
    for gi, (mk, md, fu) in enumerate(gruppi_principali(conn, limite_gruppi)):
        righe = [dict(r) for r in conn.execute(SQL_GRUPPO, (mk, md, fu)).fetchall()]
        m = costruisci(righe, (mk, md, fu), now=now)
        if not m:
            st["gruppi_piccoli"] += 1
            continue
        st["gruppi"] += 1
        for r in righe:
            if r["status"] != "attivo" or r.get("seller_type") != "privato" or (prov_zona and r.get("prov") not in prov_zona):
                continue
            s = m.stima(r)
            conn.execute("""INSERT INTO valore_reale (listing_id, v, p25, sigma, n, n_vendute, premio_venduta, gruppo, computed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT (listing_id) DO UPDATE SET v=EXCLUDED.v, p25=EXCLUDED.p25,
                sigma=EXCLUDED.sigma, n=EXCLUDED.n, n_vendute=EXCLUDED.n_vendute, premio_venduta=EXCLUDED.premio_venduta,
                gruppo=EXCLUDED.gruppo, computed_at=now()""",
                         (r["id"], s["V"], s["P25"], s["sigma"], s["n"], s["n_vendute"], s["premio_venduta"], f"{mk}|{md}|{fu}"))
            st["auto_stimate"] += 1
        conn.commit()
        if gi % 100 == 0:
            log.warning("VALORE_REALE calcolo avanzamento %d gruppi, %s", gi, dict(st))
    return dict(st)
