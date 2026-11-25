"""Simulazione SINTETICA (senza database) della stima allargata (pricing/stima_allargata.py).

    python3 scripts/simula_allargata.py [seme]

Il repo non ha un dataset con i confronti veri (tests/dati/dump_candidati_16_11.json ha solo i risultati): qui il
mercato è INVENTATO (prezzo "vero" = base del modello × svalutazione per anno e km, rumore del 10% sugli annunci,
commercianti +6% sulle vendute, prezzi più bassi lontano da Milano fino al −8%, svalutazione dei km che rallenta
oltre 150.000 km: il caso sfavorevole alla correzione lineare). Serve a controllare il VERSO dell'errore (mai sopra il
valore normale) e l'ordine di grandezza; la precisione vera è nel log CONFRONTI_OMBRA ("precisione") dopo il deploy.
Per ogni auto che HA il valore normale (valore_base.stima_prudente) si calcola ogni prova della stima allargata come
se il valore mancasse, e si confronta col valore normale: errore mediano, 90° percentile, quante volte esce sopra.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dealfinder import regole as RG                         # noqa: E402
from dealfinder.pricing import stima_allargata as SA        # noqa: E402
from dealfinder.pricing import valore_base as VB            # noqa: E402

ZONA = ["MI", "MB", "VA", "CO", "LC", "BG", "LO", "PV", "CR", "BS", "NO", "PC"]
ITALIA = ZONA + ["TO", "GE", "VR", "PD", "BO", "FI", "AN", "PG", "NA", "BA", "PA", "CA", "UD", "TN", "PE", "RC"]


def vero(base: float, anno: int, km: int, prov: str) -> float:
    eta = 2026 - anno
    f_km = math.exp(-0.011 * min(km, 150000) / 10000 - 0.006 * max(0, km - 150000) / 10000)
    d = SA.distanza_km("MI", prov) or 0
    return base * (0.915 ** eta) * f_km * (1 - 0.08 * min(1.0, d / 900))


def mercato(rnd: random.Random, base: float, n: int, province: list, km_max: int) -> list:
    out = []
    for i in range(n):
        anno = rnd.randint(2012, 2023)
        km = rnd.randint(10000, km_max)
        prov = rnd.choice(province)
        comm = rnd.random() < 0.6
        p = vero(base, anno, km, prov) * (1.06 if comm else 1.0) * math.exp(rnd.gauss(0, 0.10))
        out.append(dict(id=i + 1, make="x", model="y", fuel="benzina", gearbox="manuale", power_kw=70.0, year=anno,
                        mileage_km=km, price_eur=round(p), price_flags=[], province=prov,
                        seller_type="commerciante" if comm else "privato",
                        status="attivo" if comm else "scomparso", danno="nessuno", problema=False, dichiarato=False,
                        stage="", giorni_visto=2.0, giorni_sparita=None if comm else 10.0, giorni_online=20.0))
    return out


def simula(seme: int = 1, modelli: int = 150) -> dict:
    rnd = random.Random(seme)
    vb = RG.regole().valore_base
    rapporti: dict = {}
    for _m in range(modelli):
        base = rnd.uniform(9000, 45000)
        zona = mercato(rnd, base, rnd.randint(25, 70), ZONA, 190000)
        italia = zona + [dict(c, id=1000 + c["id"]) for c in mercato(rnd, base, rnd.randint(40, 120), ITALIA, 400000)]
        bz, bi = VB.dividi(zona), VB.dividi(italia)
        for t in zona[:12]:
            if t["mileage_km"] > 150000:
                continue
            D = [c for c in bz["D"] if c is not t]
            V = [c for c in bz["V"] if c is not t]
            sp = VB.stima_prudente(D, V, t, vb, escludi=t)
            if not sp["valore"]:
                continue
            for pr in SA.PROVE:
                rr = SA.stima(D, V, t, vb, italia=bi, escludi=t, solo=pr.nome)
                if rr.get("valore"):
                    rapporti.setdefault(pr.nome, []).append(rr["valore"] / sp["valore"])
    return {k: SA._quantili(v) for k, v in rapporti.items()}


if __name__ == "__main__":
    print(json.dumps(simula(int(sys.argv[1]) if len(sys.argv) > 1 else 1), ensure_ascii=False, indent=1))
