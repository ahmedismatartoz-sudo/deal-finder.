"""Simulazione senza database: calcolo vecchio contro nuovo sulle righe DUMP_CANDIDATI / CONTROLLO_FINALE dei log.

    python3 scripts/simula_margini.py <dump.jsonl> [<risultato.md>] [--fattori '<5k:0.8,5-10k:0.985,...'>]

Il file è una riga JSON per auto (campi del log: id, auto, anno, km, prezzo, M, R, margine, n, cat, sito, metodo,
livello; "_tipo" = DUMP_CANDIDATI o CONTROLLO_FINALE).

Cosa si può simulare dai log (il resto richiede i confronti, cioè il database: vedi il lavoro "simula_margini" del
server, BREVO_IMPORT_LISTE=simula_margini):
- calibrazione chiesto → venduto: vecchia a scalini per fascia di M, nuova interpolata (regole.fattore_per), sulle auto
  con M dai prezzi chiesti (metodo mediana/modello: il dump è PRIMA della calibrazione del sito);
- allestimento riconosciuto (pricing/allestimento.py) e il suo livello, dal nome dell'auto;
- guadagno netto stimato (pricing/netto.py) con i costi già nel margine (R − prezzo − margine).
Non si possono simulare dai log: campione del giudice, dispersione dei confronti, ampiezza con 2–4 gemelle, tasso km
minimo, scelta dei confronti per allestimento, rivendita dai commercianti.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from statistics import median

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dealfinder import regole as RG  # noqa: E402
from dealfinder.pricing import allestimento as AL  # noqa: E402
from dealfinder.pricing import netto as NT  # noqa: E402

SOGLIE = ((7000, 1500), (10000, 1800), (15000, 2500), (20000, 3200), (30000, 4000), (10**9, 5000))
FASCE = ((5000, "< 5.000"), (7000, "5–7.000"), (10000, "7–10.000"), (15000, "10–15.000"), (20000, "15–20.000"),
         (30000, "20–30.000"), (10**9, "> 30.000"))
# fattori in vigore nei log del 12/11 17:36 (CALIBRA_FATTORI), misurati per fascia di prezzo
FATTORI_LOG = {"<5k": .8, "5-10k": .985, "10-20k": .998, "20-35k": 1.0, "35-60k": 1.0, ">60k": 1.0}


def soglia(p: int) -> int:
    return next(m for lim, m in SOGLIE if p <= lim)


def fascia(p: int) -> str:
    return next(n for lim, n in FASCE if p < lim)


def fattori(d: dict) -> list:
    return RG.fattori_calibrazione({k: {"n": 999, "prezzo_su_M": v} for k, v in d.items()})


def f_vecchio(M: float, fat: list) -> float:
    return next((x for lim, _, x in fat if M < lim and not str(_).startswith(("vr:", "_"))), 1.0)


def leggi(path: str) -> list[dict]:
    righe, visti = [], set()
    for riga in open(path, encoding="utf-8"):
        riga = riga.strip()
        if not riga:
            continue
        r = json.loads(riga)
        if r.get("_tipo", "DUMP_CANDIDATI") != "DUMP_CANDIDATI" or r.get("id") in visti:
            continue
        if not (r.get("M") and r.get("prezzo") and r.get("margine") is not None):
            continue
        visti.add(r["id"])
        righe.append(r)
    return righe


def simula(righe: list[dict], fat_dict: dict | None = None) -> dict:
    fat = fattori(fat_dict or FATTORI_LOG)
    out = []
    for r in righe:
        p, M = int(r["prezzo"]), float(r["M"])
        R = float(r.get("R") or M)
        costi = max(0, int(round(R - p - int(r["margine"]))))
        annunci = r.get("metodo") in ("mediana", "modello", "ai")
        if annunci:
            fv, fn = f_vecchio(M, fat), RG.fattore_per(M, fat)
        else:
            fv = fn = 1.0
        Rv, Rn = min(R, M * fv), min(R, M * fn)
        mv, mn = int(round(Rv - p - costi)), int(round(Rn - p - costi))
        nome = str(r.get("auto") or "")
        marca = nome.split()[0] if nome else ""
        if nome.startswith(("Land Rover", "Alfa Romeo")):
            marca = " ".join(nome.split()[:2])
        al = AL.livello({"nome": nome, "marca": marca})
        nv, nn = NT.calcola(Rv, p, costi), NT.calcola(Rn, p, costi)
        sito_v = bool(r.get("sito"))
        sito_n = sito_v and mn >= soglia(p)
        entra = (not sito_v and r.get("cat") in ("opportunita", "candidato") and mv < soglia(p) <= mn)
        out.append({"id": r["id"], "prezzo": p, "fascia": fascia(p), "M": int(M), "metodo": r.get("metodo"),
                    "f_vecchio": round(fv, 4), "f_nuovo": round(fn, 4), "marg_v": mv, "marg_n": mn,
                    "netto_v": nv["netto"] if nv else None, "netto_n": nn["netto"] if nn else None,
                    "sito_v": sito_v, "sito_n": sito_n, "entra_forse": entra,
                    "liv": al["livello"] if al else None, "all": al["nome"] if al else None})
    return {"righe": out}


def tabella(res: dict) -> str:
    rr = res["righe"]
    per = defaultdict(list)
    for x in rr:
        per[x["fascia"]].append(x)
    h = ["| Fascia di prezzo | Auto nel dump | Sul sito prima | Sul sito dopo | Escono | Forse entrano | "
         "Margine mediano prima (sito prima) | Margine mediano dopo (stesse auto) | Margine mediano dopo (sito dopo) | Netto mediano (sito, dopo) | Netto/margine | "
         "Allestimento riconosciuto |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    tot = {"n": 0, "sv": 0, "sn": 0, "esc": 0, "ent": 0}
    for _, f in FASCE:
        xs = per.get(f, [])
        if not xs:
            continue
        sv = [x for x in xs if x["sito_v"]]
        sn = [x for x in xs if x["sito_n"]]
        esc = sum(1 for x in sv if not x["sito_n"])
        ent = sum(1 for x in xs if x["entra_forse"])
        mv = median([x["marg_v"] for x in sv]) if sv else None
        ms = median([x["marg_n"] for x in sv]) if sv else None
        mn = median([x["marg_n"] for x in sn]) if sn else None
        nn = median([x["netto_n"] for x in sn if x["netto_n"] is not None]) if sn else None
        ric = sum(1 for x in xs if x["liv"] is not None) / len(xs)
        h.append(f"| {f} € | {len(xs)} | {len(sv)} | {len(sn)} | {esc} | {ent} | {_e(mv)} | {_e(ms)} | {_e(mn)} | {_e(nn)} | "
                 f"{(nn / mn * 100):.0f}% |" if mn and nn is not None else
                 f"| {f} € | {len(xs)} | {len(sv)} | {len(sn)} | {esc} | {ent} | {_e(mv)} | {_e(ms)} | {_e(mn)} | {_e(nn)} | — |")
        h[-1] += f" {ric * 100:.0f}% |"
        for k, v in (("n", len(xs)), ("sv", len(sv)), ("sn", len(sn)), ("esc", esc), ("ent", ent)):
            tot[k] += v
    sv = [x for x in rr if x["sito_v"]]
    sn = [x for x in rr if x["sito_n"]]
    mv = median([x["marg_v"] for x in sv]) if sv else None
    ms = median([x["marg_n"] for x in sv]) if sv else None
    mn = median([x["marg_n"] for x in sn]) if sn else None
    nn = median([x["netto_n"] for x in sn if x["netto_n"] is not None]) if sn else None
    ric = sum(1 for x in rr if x["liv"] is not None) / max(len(rr), 1)
    h.append(f"| **Totale** | {tot['n']} | {tot['sv']} | {tot['sn']} | {tot['esc']} | {tot['ent']} | {_e(mv)} | {_e(ms)} | "
             f"{_e(mn)} | {_e(nn)} | {(nn / mn * 100):.0f}% | {ric * 100:.0f}% |" if mn and nn is not None else "")
    return "\n".join(h)


def _e(x) -> str:
    return "—" if x is None else f"{int(round(x)):,} €".replace(",", ".")


def riassunto(res: dict) -> dict:
    rr = res["righe"]
    cambiati = [x for x in rr if x["f_vecchio"] != x["f_nuovo"]]
    liv = Counter(x["liv"] for x in rr)
    sito = [x for x in rr if x["sito_v"]]
    return {"auto": len(rr), "sul_sito_prima": len(sito), "sul_sito_dopo": sum(1 for x in rr if x["sito_n"]),
            "fattore_cambiato": len(cambiati),
            "fattore_piu_basso": sum(1 for x in cambiati if x["f_nuovo"] < x["f_vecchio"]),
            "fattore_piu_alto": sum(1 for x in cambiati if x["f_nuovo"] > x["f_vecchio"]),
            "livelli_allestimento": {str(k): v for k, v in sorted(liv.items(), key=lambda kv: (kv[0] is None, kv[0]))},
            "netto_negativo_sito": sum(1 for x in rr if x["sito_n"] and (x["netto_n"] or 0) <= 0),
            "netto_sotto_500_sito": sum(1 for x in rr if x["sito_n"] and (x["netto_n"] or 0) < 500)}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    fat = dict(FATTORI_LOG)
    if "--fattori" in argv:
        i = argv.index("--fattori")
        fat.update({k: float(v) for k, v in (x.split(":") for x in argv[i + 1].split(","))})
        argv = argv[:i] + argv[i + 2:]
    righe = leggi(argv[1])
    res = simula(righe, fat)
    ria = riassunto(res)
    testo = ("# Simulazione margini: calcolo vecchio contro nuovo (dai log)\n\n"
             f"Righe DUMP_CANDIDATI lette: {len(righe)} (auto di privati con il numero, fino a 150.000 km).\n\n"
             + tabella(res) + "\n\n```\n" + json.dumps(ria, ensure_ascii=False, indent=1) + "\n```\n")
    if len(argv) > 2:
        open(argv[2], "w", encoding="utf-8").write(testo)
    print(testo)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
