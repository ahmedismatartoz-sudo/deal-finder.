"""Modello AI dei prezzi (machine learning, gradient boosting) — "ai-prezzi-1".

Impara da TUTTI gli annunci sani del Nord Italia raccolti nel database (privati e concessionari, attivi e spariti
negli ultimi mesi) a stimare il prezzo di mercato di un'auto da marca, modello, alimentazione, cambio, potenza,
anno, km, tipo di venditore e sito. A differenza delle formule (mediana dei confronti, retta per anno e km):
- impara da solo come anno, km e potenza pesano in modo diverso per ogni tipo di auto (relazioni non lineari);
- condivide le informazioni tra modelli simili, quindi stima anche le auto con pochi annunci uguali;
- dice quanto sbaglia: l'errore è MISURATO su annunci che il modello non aveva visto (validazione incrociata a
  5 parti), separatamente per ogni modello d'auto, e viene salvato accanto a ogni stima.

Gira nel lavoro programmato (cron, contenitore a parte, così non pesa sulla memoria del sito) e scrive le stime nella
tabella `stima_ai`; il calcolo del sito le legge (pricing/policy_v2.py).

    python -m dealfinder.pricing.ai_prezzi           # addestra e scrive le stime
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date

import numpy as np

VERSIONE = "ai-prezzi-1"
log = logging.getLogger("ai_prezzi")

DANNEGGIATE = {"leggero", "medio", "grave", "alto_rischio"}
ESCLUSI = {"leasing_o_rata", "prezzo_civetta", "importata", "prezzo_finanziamento", "dati_incoerenti"}
CAT = ("fuel", "gearbox", "seller", "source")           # categorie con pochi valori: direttamente nel modello
SMOOTH = 8.0                                             # quanti annunci servono perché un modello "conti" da solo


def _norm(s) -> str:
    return " ".join(str(s or "").lower().replace("-", " ").split())


def _righe(conn) -> list[dict]:
    from .ricalcolo import NORD_SQL
    return conn.execute(f"""
        SELECT id, make, model, fuel, gearbox, power_kw, year, mileage_km, price_eur, price_flags, seller_type,
               source, damage_class, status
        FROM listings
        WHERE price_eur BETWEEN 300 AND %s AND make IS NOT NULL AND model IS NOT NULL
          AND year BETWEEN 1990 AND 2027 AND mileage_km IS NOT NULL AND mileage_km BETWEEN 0 AND 500000
          AND {NORD_SQL}
          AND (status = 'attivo' OR disappeared_at > now() - interval '120 days')
        ORDER BY (price_eur <= %s) DESC, last_seen_at DESC     -- 21/10: prima tutte le utilitarie, poi il resto
        LIMIT %s""", (int(os.environ.get("AI_PREZZO_MAX", "120000")),       # 21/10: anche la fascia alta
                      int(os.environ.get("AI_UTILITARIE_MAX", "20000")),
                      int(os.environ.get("AI_MAX_RIGHE", "120000")),)).fetchall()


def _sana(r: dict) -> bool:
    if r.get("damage_class") in DANNEGGIATE:
        return False
    if set(r.get("price_flags") or []) & ESCLUSI:
        return False
    km, y = r.get("mileage_km"), r.get("year")
    if 1990 <= int(km) <= 2035 and abs(int(km) - int(y)) <= 3:         # l'anno scritto al posto dei km
        return False
    return True


class Dati:
    """Matrice numerica + chiavi testuali (marca, modello) per la codifica dei modelli."""

    def __init__(self, righe: list[dict], ref_year: int):
        self.ref = ref_year
        n = len(righe)
        self.ids = np.array([r["id"] for r in righe], dtype=np.int64)
        self.y = np.array([math.log(max(300, int(r["price_eur"]))) for r in righe]) if righe and "price_eur" in righe[0] else None
        self.make = [_norm(r["make"]) for r in righe]
        self.mm = [m + "|" + _norm(r["model"]) for m, r in zip(self.make, righe)]
        self.mmf = [k + "|" + _norm(r["fuel"]) for k, r in zip(self.mm, righe)]
        age = np.array([max(0, ref_year - int(r["year"])) for r in righe], dtype=float)
        km = np.array([min(int(r["mileage_km"]), 500000) / 10000 for r in righe], dtype=float)
        kw = np.array([float(r["power_kw"]) if r.get("power_kw") and 25 <= int(r["power_kw"]) <= 500 else np.nan
                       for r in righe])
        self.num = np.column_stack([age, km, kw, np.log1p(km), km / np.maximum(age, .5)]) if n else np.zeros((0, 5))
        self.cat_raw = {c: [_norm(r.get({"seller": "seller_type"}.get(c, c))) or "?" for r in righe] for c in CAT}


class Codifica:
    """Valori di riferimento per marca / modello / modello+alimentazione: quanto quel modello vale in più o in meno
    rispetto a un'auto "media" con la stessa età, km e potenza (stimato sui residui di un modello base, con
    lisciatura verso la marca quando gli annunci sono pochi)."""

    def __init__(self, d: Dati, idx: np.ndarray, base_res: np.ndarray):
        self.glob = 0.0
        self.tab = {}
        for nome, chiavi, padre in (("make", d.make, None), ("mm", d.mm, "make"), ("mmf", d.mmf, "mm")):
            s, c = defaultdict(float), defaultdict(int)
            for i, r in zip(idx, base_res):
                s[chiavi[i]] += r
                c[chiavi[i]] += 1
            self.tab[nome] = (s, c)
        self.cats = {k: {v: j for j, v in enumerate(sorted(set(d.cat_raw[k][i] for i in idx)))} for k in CAT}

    def _val(self, nome, chiave, prior):
        s, c = self.tab[nome]
        n = c.get(chiave, 0)
        return (s.get(chiave, 0.0) + SMOOTH * prior) / (n + SMOOTH), n

    def righe(self, d: Dati, idx: np.ndarray) -> np.ndarray:
        out = np.zeros((len(idx), 4 + len(CAT)))
        for j, i in enumerate(idx):
            v_make, _ = self._val("make", d.make[i], 0.0)
            v_mm, n_mm = self._val("mm", d.mm[i], v_make)
            v_mmf, _ = self._val("mmf", d.mmf[i], v_mm)
            out[j, :4] = (v_make, v_mm, v_mmf, math.log1p(n_mm))
            for k, c in enumerate(CAT):
                out[j, 4 + k] = self.cats[c].get(d.cat_raw[c][i], -1)
        out[:, 4:][out[:, 4:] < 0] = np.nan
        return out


def _gb(**kw):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(random_state=7, **kw)


def _base_X(d: Dati, idx, cats):
    X = d.num[idx]
    extra = np.array([[cats[c].get(d.cat_raw[c][i], np.nan) for c in ("fuel", "gearbox")] for i in idx], dtype=float)
    return np.column_stack([X, extra])


def _fit(d: Dati, tr: np.ndarray):
    """Addestra su tr: modello base (età/km/potenza) -> codifica dei modelli -> modello finale."""
    cats0 = {k: {v: j for j, v in enumerate(sorted(set(d.cat_raw[k][i] for i in tr)))} for k in CAT}
    base = _gb(max_iter=200, learning_rate=.08, max_leaf_nodes=31, categorical_features=[5, 6])
    base.fit(_base_X(d, tr, cats0), d.y[tr])
    res = d.y[tr] - base.predict(_base_X(d, tr, cats0))
    # codifica fuori campione per le righe di addestramento (3 parti), per non far "barare" il modello finale
    rng = np.random.default_rng(11)
    fold = rng.integers(0, 3, len(tr))
    Xcod = np.zeros((len(tr), 4 + len(CAT)))
    for f in range(3):
        a, b = fold != f, fold == f
        cod_f = Codifica(d, tr[a], res[a])
        cod_f.cats = cats0
        Xcod[b] = cod_f.righe(d, tr[b])
    cod = Codifica(d, tr, res)
    cod.cats = cats0
    X = np.column_stack([d.num[tr], Xcod])
    catmask = [False] * (d.num.shape[1] + 4) + [True] * len(CAT)
    fin = _gb(max_iter=int(os.environ.get("AI_ALBERI", "700")), learning_rate=.05, max_leaf_nodes=63,
              min_samples_leaf=15, l2_regularization=1.0, categorical_features=catmask,
              early_stopping=True, validation_fraction=.1, n_iter_no_change=30)
    fin.fit(X, d.y[tr])
    return base, cod, fin


def _pred(model, d: Dati, idx: np.ndarray) -> np.ndarray:
    base, cod, fin = model
    return fin.predict(np.column_stack([d.num[idx], cod.righe(d, idx)]))


def _come_privato(d: Dati) -> Dati:
    """Le stime si fanno sempre come se a vendere fosse un privato (prezzo prudente, non da concessionario)."""
    d.cat_raw["seller"] = ["privato"] * len(d.ids)
    return d


def allena_e_stima(conn) -> dict:
    t0 = time.time()
    ref = date.today().year
    tutte = _righe(conn)
    sane = [r for r in tutte if _sana(r)]
    # doppioni (stessa auto su più siti o ripubblicata): una sola volta nell'addestramento
    visti, train_rows = set(), []
    for r in sane:
        k = (_norm(r["make"]), _norm(r["model"]), r["year"], round(int(r["mileage_km"]) / 1000), round(int(r["price_eur"]) / 100))
        if k in visti:
            continue
        visti.add(k)
        train_rows.append(r)
    d = Dati(train_rows, ref)
    n = len(train_rows)
    if n < 2000:
        log.warning("AI_PREZZI troppo pochi annunci per addestrare (%d)", n)
        return {"annunci": n, "saltato": True}
    # 1) validazione incrociata a 5 parti: ogni annuncio stimato da un modello che non lo ha mai visto
    rng = np.random.default_rng(7)
    fold = rng.integers(0, 5, n)
    oof = np.zeros(n)
    for f in range(5):
        tr, te = np.where(fold != f)[0], np.where(fold == f)[0]
        m = _fit(d, tr)
        oof[te] = _pred(m, d, te)                 # errore misurato col vero tipo di venditore
        log.info("AI_PREZZI parte %d/5 fatta (%.0fs)", f + 1, time.time() - t0)
    err = np.abs(np.exp(oof) - np.exp(d.y)) / np.exp(d.y)
    # errore tipico per gruppo (mediana dell'errore %), dal più specifico al più generale
    gruppi = {"mmf": defaultdict(list), "mm": defaultdict(list), "make": defaultdict(list)}
    for i in range(n):
        gruppi["mmf"][d.mmf[i]].append(err[i])
        gruppi["mm"][d.mm[i]].append(err[i])
        gruppi["make"][d.make[i]].append(err[i])
    glob = float(np.median(err))

    def errore_di(i_mmf, i_mm, i_make):
        for nome, k, minimo in (("mmf", i_mmf, 8), ("mm", i_mm, 8), ("make", i_make, 20)):
            v = gruppi[nome].get(k)
            if v and len(v) >= minimo:
                return float(np.median(v)), len(v), nome
        return glob * 1.3, 0, "mercato"

    # 2) modello finale su tutto, e stima di TUTTE le auto attive del Nord (anche quelle non usate per imparare)
    finale = _fit(d, np.arange(n))
    attive = [r for r in tutte if r["status"] == "attivo"]
    da = _come_privato(Dati(attive, ref))
    pred_att = _pred(finale, da, np.arange(len(attive))) if attive else np.zeros(0)
    # chi era nell'addestramento usa la stima "fuori campione" (non ha visto il proprio prezzo), come privato
    p_tr_all = np.exp(_pred(finale, d, np.arange(n)))      # modello finale col vero tipo di venditore
    pos = {int(i): j for j, i in enumerate(d.ids)}
    righe_out = []
    for j, r in enumerate(attive):
        i = pos.get(int(r["id"]))
        p = float(np.exp(pred_att[j]))
        if i is not None:
            # correzione privato/concessionario del modello finale applicata alla stima fuori campione
            p_tr = float(p_tr_all[i])
            p = float(np.exp(oof[i])) * (p / p_tr if p_tr > 0 else 1.0)
        e, ng, liv = errore_di(da.mmf[j], da.mm[j], da.make[j])
        righe_out.append((int(r["id"]), int(round(p, -1)), round(e, 4), ng, liv, VERSIONE))
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO stima_ai (listing_id, prezzo, errore, n_gruppo, livello, versione, computed_at)
               VALUES (%s,%s,%s,%s,%s,%s, now())
               ON CONFLICT (listing_id) DO UPDATE SET prezzo=EXCLUDED.prezzo, errore=EXCLUDED.errore,
                 n_gruppo=EXCLUDED.n_gruppo, livello=EXCLUDED.livello, versione=EXCLUDED.versione, computed_at=now()""",
            righe_out)
    conn.execute("DELETE FROM stima_ai WHERE computed_at < now() - interval '3 days'")
    conn.commit()
    fasce = {}
    for lo, hi in ((0, 3000), (3000, 6000), (6000, 10000), (10000, 20000), (20000, 10 ** 9)):
        sel = (np.exp(d.y) >= lo) & (np.exp(d.y) < hi)
        if sel.any():
            fasce[f"{lo}-{hi if hi < 10**9 else 'oltre'}"] = round(float(np.median(err[sel])) * 100, 1)
    precise = sum(1 for x in righe_out if x[2] <= .12)
    res = {"versione": VERSIONE, "annunci_addestramento": n, "auto_stimate": len(righe_out),
           "errore_tipico_pct": round(glob * 100, 1), "entro_10pct": round(float((err <= .10).mean()) * 100, 1),
           "entro_20pct": round(float((err <= .20).mean()) * 100, 1), "errore_per_prezzo_pct": fasce,
           "stime_con_errore_max_12pct": precise, "secondi": round(time.time() - t0)}
    log.warning("AI_PREZZI %s", json.dumps(res))
    return res


def run() -> dict:
    from ..db import connect, log_job
    with connect() as conn:
        fine = log_job(conn, "ai_prezzi")
        try:
            res = allena_e_stima(conn)
            fine(True, res)
            return res
        except Exception as e:
            conn.rollback()
            log.exception("AI_PREZZI non riuscito")
            fine(False, {"errore": str(e)[:300]})
            return {"errore": str(e)[:300]}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
    sys.exit(0)
