"""Rivendita come forbice (12/11/2026 sera, margini fase 2 punto 5): dove cade di solito il prezzo di vendita di
un'auto come questa e con che probabilità si vende ad almeno R. Solo informazione per la scheda: il margine e la
soglia (SOGLIE_PV) restano quelli di prima.

Dati: le auto sparite entro 7 giorni (pricing/calibra.py), per fascia di M: quantili 10/25/50/75/90 di prezzo/M
(voci "_q:" per M dai prezzi chiesti, "_qvr:" per M dal valore reale, in regole.fattori_calibrazione).

    M dai prezzi chiesti, con quantili "_q:"      → punti = M × q
    M dal valore reale puro, con quantili "_qvr:" → punti = M × q
    altrimenti (combinato, o fascia senza misura)  → punti = M × q / q50 (M è già un prezzo di vendita: si usa solo
                                                     la larghezza misurata)
    probabilità di vendere ad almeno R = 1 − F(R), F interpolata tra i punti (10%, 25%, 50%, 75%, 90%), con le code
    lineari fino a 0% e 100% a una distanza pari a quella tra q10–q25 e q75–q90; sempre tra prob_min e prob_max.
Senza quantili misurati: None (nessun numero inventato).
"""
from __future__ import annotations

from .. import regole as RG

LIVELLI = (.10, .25, .50, .75, .90)


def _fascia(M: float, r: RG.Regole) -> str:
    return next((n for lim, n in r.calibrazione.fasce if M < lim), r.calibrazione.fasce[-1][1])


def _quantili(fattori, pref: str, fascia: str):
    for _, n, x in fattori or []:
        if str(n) == pref + fascia and isinstance(x, (tuple, list)) and len(x) == 5:
            return tuple(float(v) for v in x)
    return None


def probabilita(R: float, punti: list | tuple, pmin: float = .05, pmax: float = .95) -> float | None:
    """P(prezzo di vendita ≥ R) dai punti in euro (p10, p25, p50, p75, p90)."""
    if not R or not punti or len(punti) != 5:
        return None
    p = [float(x) for x in punti]
    xs = [p[0] - (p[1] - p[0]) * .1 / .15] + p + [p[4] + (p[4] - p[3]) * .1 / .15]
    ys = [0.0] + list(LIVELLI) + [1.0]
    R = float(R)
    if R <= xs[0]:
        F = 0.0
    elif R >= xs[-1]:
        F = 1.0
    else:
        F = 1.0
        for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
            if x0 <= R <= x1:
                F = y0 if x1 == x0 else y0 + (R - x0) / (x1 - x0) * (y1 - y0)
                break
    return round(max(pmin, min(pmax, 1.0 - F)), 2)


def calcola(pv: dict, fattori, r: RG.Regole | None = None) -> dict | None:
    """{"p10", "p25", "p50", "p75", "p90", "prob_R", "R", "base"} in euro (arrotondati a 50), oppure None."""
    r = r or RG.regole()
    if not r.forbice.attiva or not isinstance(pv, dict) or not pv.get("M") or not fattori:
        return None
    M = float(pv["M"])
    fascia = _fascia(M, r)
    annunci = RG.da_calibrare(pv) or pv.get("M_fonte") == "annunci"
    puro_vr = pv.get("M_fonte") in ("vendite", "vendite_calibrate") and pv.get("metodo") in ("valore_reale",
                                                                                              "valore_reale_modello")
    q, base = None, None
    if annunci:
        q, base = _quantili(fattori, "_q:", fascia), "annunci"
    elif puro_vr:
        q, base = _quantili(fattori, "_qvr:", fascia), "valore_reale"
    if q is None:
        q0 = _quantili(fattori, "_qvr:" if not annunci else "_q:", fascia) or _quantili(fattori, "_q:", fascia)
        if q0 is None:
            return None
        # M già al venduto (o fascia senza la misura del suo metodo): solo la larghezza misurata
        q, base = tuple(x / q0[2] for x in q0), "larghezza"
    punti = [int(round(M * x / 50.0) * 50) for x in q]
    R = pv.get("R_finale") or pv.get("R")
    out = dict(zip(("p10", "p25", "p50", "p75", "p90"), punti))
    out.update(R=int(R) if R else None, base=base, fascia=fascia,
               prob_R=probabilita(R, punti, r.forbice.prob_min, r.forbice.prob_max))
    return out
