"""Precisione di M e R sulle auto vendute (17/11 notte, cliente: "margini molto precisi").

Strumento di misura, nessuna regola: dato un elenco di auto vendute con il prezzo finale ("vero") e la stima del
nostro calcolo fatta senza conoscerlo (prova alla cieca, ai/prova_cieca.py; misura CALIBRA, pricing/calibra.py),
dice per gruppo (fascia di M, età, ...) quanto sbaglia la stima e se lo scostamento è SISTEMATICO.

Una correzione per gruppo si propone solo se:
  - il gruppo ha almeno `n_min` auto (default 20, come calibrazione.n_min);
  - lo scostamento è dimostrato: test dei segni a due code (quante stime sopra il vero contro quante sotto) con
    p < `p_max` (default 0,05), niente ipotesi sulla forma degli errori e nessun numero casuale;
  - la correzione ABBASSA la stima (fattore < 1). Un fattore > 1 (stima troppo bassa) si segnala ma non si applica:
    alzare M va giustificato con prezzi di vendita veri e lo decide il cliente (calibrazione.alza, spento).
Il fattore è la mediana di vero / stima nel gruppo, mai sotto `minimo` (0,80 come calibrazione.minimo).

La tabella "dopo" è onesta: ogni auto è corretta con il fattore misurato SENZA di lei (leave-one-out), quindi non
premia una correzione che impara la risposta.
"""
from __future__ import annotations

import math
from statistics import median


def metriche(coppie: list[tuple]) -> dict:
    """coppie (stima, vero) → n, errore medio e mediano in %, quota entro il 10%, scostamento mediano e medio in %
    (+ = stima troppo alta)."""
    ok = [(float(s), float(v)) for s, v in coppie if s and v]
    if not ok:
        return {"n": 0}
    pct = [(s - v) / v for s, v in ok]
    ass = [abs(p) for p in pct]
    return {"n": len(ok), "errore_medio_pct": round(100 * sum(ass) / len(ok), 1),
            "errore_mediano_pct": round(100 * median(ass), 1),
            "entro_10pct_quota": round(100 * sum(1 for a in ass if a <= .10) / len(ok), 1),
            "scostamento_mediano_pct": round(100 * median(pct), 1),
            "scostamento_medio_pct": round(100 * sum(pct) / len(ok), 1)}


def p_segni(sopra: int, sotto: int) -> float:
    """p a due code del test dei segni (binomiale con p=0,5), pareggi esclusi."""
    n = sopra + sotto
    if n == 0:
        return 1.0
    k = min(sopra, sotto)
    coda = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * coda)


def ic_mediana(valori: list, livello: float = .95) -> tuple | None:
    """Intervallo di confidenza della mediana senza ipotesi sulla forma degli errori (statistiche d'ordine con la
    binomiale p=0,5): (basso, alto). Con poche auto l'intervallo è largo: chi deve essere prudente usa `alto` (la stima
    più vicina a 1 per un rapporto vero/stima sotto 1). None senza valori."""
    xs = sorted(float(x) for x in valori if x is not None)
    n = len(xs)
    if n == 0:
        return None
    coda, cum, k = (1 - livello) / 2, 0.0, 0
    for i in range(n + 1):                 # k = quante statistiche d'ordine si scartano per lato
        p = math.comb(n, i) / 2 ** n
        if cum + p > coda:
            break
        cum += p
        k = i + 1
    if k == 0 or k > n // 2:
        return xs[0], xs[-1]
    return xs[k - 1], xs[n - k]


def fattore_gruppo(coppie: list[tuple], n_min: int = 20, p_max: float = .05, minimo: float = .80) -> dict:
    """Fattore vero/stima di un gruppo e se va applicato. coppie (stima, vero)."""
    ok = [(float(s), float(v)) for s, v in coppie if s and v]
    n = len(ok)
    if n == 0:
        return {"n": 0, "fattore": 1.0, "applica": False, "motivo": "nessuna auto"}
    rapp = [v / s for s, v in ok]
    f = median(rapp)
    sopra = sum(1 for s, v in ok if s > v)
    sotto = sum(1 for s, v in ok if s < v)
    p = p_segni(sopra, sotto)
    out = {"n": n, "fattore": round(f, 3), "p": round(p, 4), "stime_sopra": sopra, "stime_sotto": sotto}
    if n < n_min:
        return dict(out, applica=False, motivo=f"campione piccolo ({n} < {n_min})")
    if p >= p_max:
        return dict(out, applica=False, motivo=f"scostamento non dimostrato (p={p:.2f})")
    if f >= 1:
        return dict(out, applica=False, motivo="stima troppo bassa: non si alza senza decisione del cliente")
    return dict(out, fattore=round(max(minimo, f), 3), applica=True, motivo="stima troppo alta, dimostrato")


def tabella(righe: list[dict], stima: str, gruppo, ordine: tuple | None = None, n_min: int = 20,
            p_max: float = .05, minimo: float = .80) -> dict:
    """Tabella prima/dopo. righe con "vero" e la chiave `stima`; gruppo(riga) → nome del gruppo (None = fuori).
    "dopo": ogni auto corretta con il fattore del suo gruppo misurato senza di lei (leave-one-out), solo se il
    gruppo, senza di lei, supera ancora tutti i controlli di fattore_gruppo."""
    xs = [r for r in righe if r.get(stima) and r.get("vero") and gruppo(r) is not None]
    gruppi: dict = {}
    for r in xs:
        gruppi.setdefault(gruppo(r), []).append(r)
    dopo = {}
    for g, rr in gruppi.items():
        for i, r in enumerate(rr):
            altre = [(x[stima], x["vero"]) for j, x in enumerate(rr) if j != i]
            fg = fattore_gruppo(altre, n_min, p_max, minimo)
            dopo[id(r)] = float(r[stima]) * (fg["fattore"] if fg["applica"] else 1.0)
    nomi = [g for g in (ordine or sorted(gruppi, key=str)) if g in gruppi]
    out = {"tutte": {"prima": metriche([(r[stima], r["vero"]) for r in xs]),
                     "dopo": metriche([(dopo[id(r)], r["vero"]) for r in xs])},
           "gruppi": {}}
    for g in nomi:
        rr = gruppi[g]
        out["gruppi"][g] = {"prima": metriche([(r[stima], r["vero"]) for r in rr]),
                            "dopo": metriche([(dopo[id(r)], r["vero"]) for r in rr]),
                            "correzione": fattore_gruppo([(r[stima], r["vero"]) for r in rr], n_min, p_max, minimo)}
    return out
