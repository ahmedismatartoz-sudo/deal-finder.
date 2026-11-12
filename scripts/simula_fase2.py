"""Simulazione margini fase 2 (12/11 sera) sulle righe DUMP_CANDIDATI dei log, senza database.

    python3 scripts/simula_fase2.py <dump.jsonl> [<risultato.md>] [--vr '<5k:0.77,5-10k:0.98'] [--peso-v 0.5]

Tre calcoli sulle stesse auto, tutti con i fattori chiesto → venduto in vigore nei log (scripts/simula_margini.py):
- PRIMA: margini-precisi (4e26d24): mediana/modello calibrati con il fattore interpolato; valore reale puro calibrato
  con la misura "vr" se c'è; combinato mai;
- DOPO (default del ramo): in più il combinato porta V al venduto con il fattore misurato sul valore reale
  (regole.calibrazione.vr_nel_combinato). Dal dump non si conoscono V e il suo peso: M_nuovo = M × f_vr(M)^peso_v
  (peso_v = peso tipico di V nella media pesata, 0,5 di default; R = min(R, M_nuovo));
- TUTTE: in più il fattore oltre 1,0 sopra 20.000 € (regole.calibrazione.alza), solo fasce misurate su almeno 40 auto.
Non simulabili dai log (servono i confronti, cioè il database: misura con la prova alla cieca prima/dopo):
pooling dei tassi anno/km, trazione a livelli, aggiustamento optional, correzione nei due sensi sopra 20.000 €,
guardia delle 36 ore. La forbice della rivendita non cambia il margine per costruzione.
Senza --vr (misura sul valore reale non ancora fatta in produzione) il fattore vr è 1,0: DOPO = PRIMA.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from statistics import median

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dealfinder import regole as RG  # noqa: E402
import simula_margini as SM  # noqa: E402

# misura in vigore nei log del 12/11 21:53 (CALIBRA_RISULTATO, per fascia di prezzo): n per l'interruttore "alza"
MISURA_LOG = {"<5k": (109, .769), "5-10k": (135, .985), "10-20k": (169, .994), "20-35k": (68, 1.047),
              "35-60k": (8, 1.11), ">60k": (22, 1.304)}


def calcola(righe: list[dict], vr: dict, peso_v: float) -> list[dict]:
    fat = SM.fattori(SM.FATTORI_LOG)
    fat_vr = RG.fattori_calibrazione({k: {"n": 999, "prezzo_su_M": v} for k, v in SM.FATTORI_LOG.items()},
                                     misurate_vr={k: {"n": 999, "prezzo_su_M": v} for k, v in vr.items()})
    cal = RG.regole().calibrazione
    alza = {n: min(cal.alza_max, x) for n, (k, x) in MISURA_LOG.items() if k >= cal.alza_n_min and x > 1}
    out = []
    for r in righe:
        p, M = int(r["prezzo"]), float(r["M"])
        R = float(r.get("R") or M)
        costi = max(0, int(round(R - p - int(r["margine"]))))
        met = r.get("metodo")
        if met in ("mediana", "modello", "ai"):
            Rp = min(R, M * RG.fattore_per(M, fat))
            Rd = Rp
            fa = alza.get(RG._fascia_di(M, RG.regole()))
            Rt = M * fa if fa and R >= M - .5 else Rp            # come regole.calibra_vendita con f > 1
        elif met in ("valore_reale", "valore_reale_modello"):
            Rp = Rd = Rt = min(R, M * RG.fattore_per(M, fat_vr, vr=True))
        elif met == "valore_reale_combinato":
            Rp = R
            Rd = Rt = min(R, M * RG.fattore_per(M, fat_vr, vr=True) ** peso_v)
        else:
            Rp = Rd = Rt = R
        s = SM.soglia(p)
        mp, md, mt = (int(round(x - p - costi)) for x in (Rp, Rd, Rt))
        sito = bool(r.get("sito"))
        out.append({"fascia": SM.fascia(p), "sito": sito, "mp": mp, "md": md, "mt": mt,
                    "sp": sito and mp >= s, "sd": sito and md >= s, "st": sito and mt >= s,
                    "ent_d": (not sito) and r.get("cat") in ("opportunita", "candidato") and mp < s <= md,
                    "ent_t": (not sito) and r.get("cat") in ("opportunita", "candidato") and mp < s <= mt})
    return out


def tabella(xs: list[dict]) -> str:
    per = defaultdict(list)
    for x in xs:
        per[x["fascia"]].append(x)
    e = SM._e
    h = ["| Fascia di prezzo | Sul sito prima | Sul sito dopo | Sul sito con tutte | Escono (dopo) | Forse entrano (tutte) | "
         "Margine mediano prima | Margine mediano dopo (stesse auto) | Margine mediano con tutte (stesse auto) |",
         "|---|---|---|---|---|---|---|---|---|"]
    gruppi = [(f, per.get(f, [])) for _, f in SM.FASCE] + [("**Totale**", xs)]
    for f, g in gruppi:
        if not g:
            continue
        sp = [x for x in g if x["sp"]]
        h.append(f"| {f}{' €' if not f.startswith('*') else ''} | {len(sp)} | {sum(x['sd'] for x in g)} | "
                 f"{sum(x['st'] for x in g)} | {sum(1 for x in sp if not x['sd'])} | {sum(x['ent_t'] for x in g)} | "
                 f"{e(median(x['mp'] for x in sp)) if sp else '—'} | {e(median(x['md'] for x in sp)) if sp else '—'} | "
                 f"{e(median(x['mt'] for x in sp)) if sp else '—'} |")
    return "\n".join(h)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    vr, peso = {}, .5
    if "--vr" in argv:
        i = argv.index("--vr")
        vr = {k: float(v) for k, v in (x.split(":") for x in argv[i + 1].split(","))}
        argv = argv[:i] + argv[i + 2:]
    if "--peso-v" in argv:
        i = argv.index("--peso-v")
        peso = float(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    righe = SM.leggi(argv[1])
    xs = calcola(righe, vr, peso)
    testo = (f"Righe DUMP_CANDIDATI: {len(righe)}; misura sul valore reale ipotizzata: {vr or 'nessuna'}; "
             f"peso di V nel combinato: {peso}\n\n" + tabella(xs) + "\n")
    if len(argv) > 2:
        open(argv[2], "w", encoding="utf-8").write(testo)
    print(testo)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
