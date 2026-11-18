"""Valore di mercato del sito dalle auto VENDUTE e dai COMMERCIANTI (18/11, decisione del cliente, OK esplicito).

Cliente: "se un'auto la vende un commerciante quel prezzo è già il prezzo di vendita; noi dobbiamo trovare privati con
prezzi più convincenti dei loro". Il sito usa SOLO questo calcolo, sia per far entrare sia per far uscire le auto.

Per ogni auto (valutazione.valuta_e_scrivi, unico punto che scrive valutazioni_policy):
- base D = annunci ATTIVI di COMMERCIANTI ricontrollati negli ultimi base_vendute.FRESCHI_GIORNI giorni;
- base V = auto VENDUTE (sparite negli ultimi VENDUTE_GIORNI giorni dopo almeno MIN_GIORNI_ONLINE giorni online,
  ultimo prezzo prima di sparire), di privati e commercianti;
- solo auto pulite (base_vendute._pulita), stessa marca/modello/alimentazione, province del sito
  (sito_regole.PROVINCE_100KM), confronti e correzioni di base_vendute.stima_dettaglio (anno ±1 / km ±25%, poi ±2 /
  ±40%, potenza ±10% e stesso cambio se noti, prezzi riportati all'anno/km dell'auto, mediana dopo il taglio 0,65–1,5);
- R = stima D se ha almeno N_MIN_SITO confronti, altrimenti stima V con almeno N_MIN_SITO, altrimenti nessuna stima
  (categoria dati_insufficienti). M = R (nessuna correzione chiesto → venduto: `calibrato` = 1,0, il sito non la rifà).
- margine = R − prezzo; categoria contro scovo.soglia(prezzo) (SOGLIE_PV invariate): opportunita se margine ≥ soglia,
  altrimenti non_conveniente; "candidato" solo dove serviva già (sezione "Da trattare": stessa regola
  policy_v2._sconto_da_trattare; prezzo molto basso ammesso col numero del venditore: stesso controllo anti-truffa).
- anti-truffa: gli stessi controlli di policy_v2 sul prezzo rispetto al mercato (minimo reale, sotto_m / basse con
  l'eccezione del numero) e in più margine oltre SOSPETTO_MARGINE (55%) di R = auto sospetta, fuori (dati sbagliati o
  truffa, come nella prova base_vendute). Le regole del sito (truffa, foto, zona, telefono, tetti...) restano dopo.

Il giudice AI dei confronti (ricalcolo._giudice_ai) qui NON serve: i confronti sono già filtrati per anno, km,
potenza, cambio e stato pulito e la stima è una mediana tagliata; nessuna chiamata AI (credito risparmiato).

Interruttore unico per tornare indietro in emergenza: VALORE_BASE=vecchio (variabile d'ambiente) rimette il calcolo di
prima (policy_v2 + ripiego + cascata + giudice AI) in valuta_e_scrivi. Default (assente o qualunque altro valore) =
questo calcolo. Il vecchio codice resta nel repository, spento.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from decimal import Decimal

from . import base_vendute as BV

log = logging.getLogger("valutazione")

N_MIN_SITO = 5
METODO = "base_vendute"
SOSPETTO_MARGINE = 0.55
CONFRONTI_SCHEDA = 15          # confronti salvati nella valutazione (scheda dell'auto)

# Una query per gruppo marca/modello/alimentazione (indici su make, model, fuel): attivi e sparite di recente nelle
# province del sito. Le varianti di maiuscole di marca e modello si passano come elenco (= ANY usa l'indice) e si
# riconfrontano in Python con lower(), come i gruppi di base_vendute.
GRUPPO_SQL = """
SELECT l.id, l.source, l.url, l.make, l.model, l.version_raw, l.fuel, l.gearbox, l.power_kw, l.year, l.mileage_km,
       l.price_eur, l.price_flags, l.seller_type, l.status, l.province, l.first_seen_at, l.disappeared_at,
       COALESCE(l.damage_class, 'sconosciuto') AS danno,
       COALESCE(l.problem_search, false) AS problema, COALESCE(l.damage_declared, false) AS dichiarato,
       btrim(split_part(COALESCE(l.stage_reason, ''), '|', 1)) AS stage,
       EXTRACT(EPOCH FROM (now() - COALESCE(l.last_checked_at, l.last_seen_at))) / 86400 AS giorni_visto,
       EXTRACT(EPOCH FROM (now() - l.disappeared_at)) / 86400 AS giorni_sparita,
       EXTRACT(EPOCH FROM (COALESCE(l.disappeared_at, now()) - l.first_seen_at)) / 86400 AS giorni_online
FROM listings l
WHERE l.make = ANY(%(mk)s) AND l.model = ANY(%(md)s) AND l.fuel = %(fu)s
  AND l.province = ANY(%(prov)s)
  AND l.price_eur BETWEEN 500 AND 400000 AND l.year IS NOT NULL AND l.mileage_km IS NOT NULL
  AND (l.status = 'attivo' OR (l.status = 'scomparso' AND l.disappeared_at > now() - make_interval(days => %(vg)s)))"""


def attivo() -> bool:
    """VALORE_BASE=vecchio (o 0/old) = calcolo di prima; altrimenti (default) il calcolo su vendute e commercianti."""
    return (os.environ.get("VALORE_BASE") or "nuovo").strip().lower() not in ("vecchio", "0", "old")


# ------------------------------------------------------------------ dati

def _num(x):
    """Decimal/str/None dal database → float/None (Decimal * float solleva TypeError)."""
    if x is None:
        return None
    if isinstance(x, Decimal):
        return float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def norm(r: dict) -> dict:
    """Copia con i tipi che servono a base_vendute: anno e km interi, prezzo e potenza float, flag in lista."""
    out = dict(r)
    for k in ("year", "mileage_km"):
        v = _num(r.get(k))
        out[k] = int(v) if v is not None else None
    for k in ("price_eur", "power_kw", "giorni_visto", "giorni_sparita", "giorni_online"):
        out[k] = _num(r.get(k))
    fl = r.get("price_flags")
    if isinstance(fl, str):
        try:
            fl = json.loads(fl)
        except ValueError:
            fl = [fl]
    out["price_flags"] = list(fl or [])
    return out


def _varianti(s: str) -> list[str]:
    s = str(s or "")
    return sorted({s, s.lower(), s.upper(), s.title(), s.capitalize()})


def carica(conn, make: str, model: str, fuel: str) -> dict:
    """{"D": commercianti freschi puliti, "V": vendute pulite} del gruppo (una query, solo questo gruppo)."""
    from ..web.sito_regole import PROVINCE_100KM
    righe = conn.execute(GRUPPO_SQL, {"mk": _varianti(make), "md": _varianti(model), "fu": fuel,
                                      "prov": sorted(PROVINCE_100KM), "vg": BV.VENDUTE_GIORNI}).fetchall()
    mk, md = str(make or "").lower(), str(model or "").lower()
    return dividi([norm(r) for r in righe if str(r["make"] or "").lower() == mk and str(r["model"] or "").lower() == md])


def dividi(righe: list) -> dict:
    """Le due basi di base_vendute (stessi criteri della prova): righe già normalizzate (norm)."""
    pulite = [r for r in righe if BV._pulita(r)]
    D = [r for r in pulite if r["status"] == "attivo" and r.get("seller_type") == "commerciante"
         and (r.get("giorni_visto") if r.get("giorni_visto") is not None else 99) <= BV.FRESCHI_GIORNI]
    V = [r for r in pulite if r["status"] == "scomparso" and (r.get("giorni_online") or 0) >= BV.MIN_GIORNI_ONLINE]
    return {"D": D, "V": V}


# ------------------------------------------------------------------ valutazione di un'auto

def pulita(t: dict) -> str | None:
    """Motivo per cui l'auto da valutare NON è confrontabile con auto pulite, oppure None.
    `dichiarato`: danni dichiarati nell'annuncio (colonna damage_declared originale: valutazione.prepara la azzera)."""
    r = {"danno": t.get("damage_class") or "sconosciuto", "problema": bool(t.get("problem_search")),
         "dichiarato": bool(t.get("dichiarato")),
         "stage": str(t.get("stage_reason") or "").split("|", 1)[0].strip(),
         "price_flags": t.get("price_flags") or []}
    if t.get("rischio_strutturale"):
        return "danno strutturale o airbag"
    if r["danno"] not in ("nessuno", "sconosciuto"):
        return f"danno {r['danno']}"
    if r["problema"] or r["dichiarato"]:
        return "danni o problemi dichiarati nell'annuncio"
    if not BV._pulita(r):
        return "annuncio non pulito (prezzo non affidabile o scartato)"
    return None


def _confronto(c: dict, p: float, venduta: bool) -> dict:
    return {"id": c.get("id"), "url": c.get("url"), "fonte": c.get("source"), "prezzo": c.get("price_eur"),
            "prezzo_corretto": int(round(p)), "anno": c.get("year"), "km": c.get("mileage_km"),
            "cambio": c.get("gearbox"), "kw": c.get("power_kw"), "versione": (c.get("version_raw") or "")[:40] or None,
            "venditore": c.get("seller_type"),
            "pubblicato": str(c.get("first_seen_at"))[:10] if c.get("first_seen_at") else None,
            **({"venduta": True, "sparita": str(c.get("disappeared_at") or "")[:10] or None} if venduta else {})}


def valuta(t: dict, base: dict, soglia: int, rg=None) -> dict:
    """Valutazione di `t` (riga di listings preparata da valutazione.prepara) con le basi del gruppo (carica/dividi).
    Stessa forma delle valutazioni di policy_v2 per i campi che il sito e le schede leggono (categoria, M, R,
    R_finale, margine, soglia, n, metodo, confronti, motivi, ipotesi, netto...)."""
    from .. import regole as RG
    from . import policy_v2 as P
    rg = rg or RG.regole()
    at = rg.antitruffa
    prezzo = _num(t.get("price_eur"))
    out = {"policy": P.POLICY_VERSION, "policy_rev": P.POLICY_REV, "regole": rg.versione, "id": t.get("id"),
           "acquisto": int(prezzo) if prezzo else t.get("price_eur"), "soglia": int(soglia), "metodo": METODO,
           "metodo_base": METODO, "calibrato": 1.0, "mancanti": [], "ipotesi": [], "motivi": [], "n": 0,
           "totale_costi": 0, "costi": {}}
    for k, nome in (("year", "anno"), ("mileage_km", "km"), ("price_eur", "prezzo")):
        if t.get(k) in (None, ""):
            out["mancanti"].append(nome)
    if out["mancanti"]:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append("dati dell'auto incompleti: " + ", ".join(out["mancanti"]))
        return out
    sporca = pulita(t)
    if sporca:
        out["categoria"] = "dati_insufficienti"
        out["non_pulita"] = sporca
        out["motivi"].append(f"auto non confrontabile con auto pulite vendute o di commercianti ({sporca})")
        return out
    tt = norm(t)
    lid = t.get("id")
    D = [c for c in base.get("D") or [] if lid is None or c.get("id") != lid]
    V = [c for c in base.get("V") or [] if lid is None or c.get("id") != lid]
    d = BV.stima_dettaglio(D, tt, n_min=N_MIN_SITO)
    v = BV.stima_dettaglio(V, tt, n_min=N_MIN_SITO)
    out.update(stima_D=int(round(d["valore"])) if d else None, n_D=d["n"] if d else 0,
               stima_V=int(round(v["valore"])) if v else None, n_V=v["n"] if v else 0,
               base_D=len(D), base_V=len(V))
    scelta, fonte = (d, "commercianti") if d else ((v, "vendute") if v else (None, None))
    if not scelta:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(f"meno di {N_MIN_SITO} auto uguali pulite tra i commercianti (annunci degli ultimi "
                             f"{BV.FRESCHI_GIORNI} giorni) e tra le vendute degli ultimi {BV.VENDUTE_GIORNI} giorni")
        return out
    R = int(round(scelta["valore"]))
    n = int(scelta["n"])
    out.update(M=R, R=R, R_finale=R, centrale_ridotto=R, riduzione=0.0, M_fonte=fonte, fonte_base=fonte,
               n=n, n_gate=n, n_gemelle=n, livello=scelta["livello"],
               campione="base preferita" if n >= 10 else "provvisoria (campione limitato)",
               R_da=("prezzi dei commercianti (annunci attivi)" if fonte == "commercianti" else "prezzi delle auto vendute"),
               fascia="valore = mediana dei " + ("commercianti" if fonte == "commercianti" else "venduti"),
               venditori=dict(Counter(c.get("seller_type") or "sconosciuto" for c, _ in scelta["tenuti"])),
               confronti=[_confronto(c, p, fonte == "vendute")
                          for c, p in sorted(scelta["tenuti"], key=lambda cp: abs(cp[1] - R))[:CONFRONTI_SCHEDA]],
               valore_base={"fonte": fonte, "R": R, "n": n, "stima_D": out["stima_D"], "n_D": out["n_D"],
                            "stima_V": out["stima_V"], "n_V": out["n_V"]})
    al = BV.LIVELLI[scelta["livello"] - 1]
    out["ipotesi"].append(f"valore {R} € = mediana di {n} auto uguali "
                          + ("in vendita da commercianti (annunci ricontrollati negli ultimi "
                             f"{BV.FRESCHI_GIORNI} giorni)" if fonte == "commercianti" else
                             f"vendute negli ultimi {BV.VENDUTE_GIORNI} giorni (ultimo prezzo)")
                          + f", anno ±{al[0]} e km ±{int(al[1] * 100)}%, prezzi riportati all'anno e ai km dell'auto")
    p = int(prezzo)
    margine = R - p
    out["margine"] = int(margine)
    out["sotto_R"] = p < R
    try:
        from . import netto as NT
        nt = NT.calcola(R, p, 0)
        if nt:
            out["netto"] = nt["netto"]
            out["costi_netto"] = dict(nt["voci"], sconto_pct=nt["sconto_pct"], sconto_da=nt["sconto_da"])
    except Exception:
        pass
    # anti-truffa: margine oltre il 55% del valore = auto sospetta (dati sbagliati, rata, truffa): fuori
    if margine > SOSPETTO_MARGINE * R:
        out["categoria"] = "dati_insufficienti"
        out["sospetto_truffa"] = True
        out["motivi"].append(f"margine oltre il {int(SOSPETTO_MARGINE * 100)}% del valore: prezzo non credibile "
                             "(dati sbagliati, rata o truffa)")
        return out
    # gli stessi controlli di policy_v2 sul prezzo chiesto rispetto al mercato (M = R)
    limite = at.sotto_m
    if p <= at.basse_max and n >= at.basse_confronti:
        limite = min(limite, at.basse)
    if p < R * at.minimo_reale:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(f"prezzo sotto il {int(at.minimo_reale * 100)}% del mercato: non è un prezzo vero")
        return out
    if p < R * limite and t.get("targa_e_telefono"):
        out["prezzo_molto_basso_ammesso"] = True
        out["ipotesi"].append("prezzo molto sotto il mercato: ammessa perché l'annuncio mostra il numero del venditore")
    elif p < R * limite:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(f"prezzo oltre il {int(round((1 - limite) * 100))}% sotto il mercato: dati non "
                             "affidabili (versione diversa, dati sbagliati o truffa)")
        return out
    if margine < soglia:
        sc = P._sconto_da_trattare(t, p, margine, int(soglia))
        if sc:
            out.update(categoria="candidato", da_trattare={"sconto": sc, "prezzo_obiettivo": p - sc})
            out["motivi"].append(f"margine {margine} € sotto la soglia di {soglia} €: arriva al minimo con {sc} € di "
                                 "sconto da chiedere al venditore")
            return out
        out["categoria"] = "non_conveniente"
        out["motivi"].append(f"margine {margine} € sotto la soglia di {soglia} €")
        return out
    if out.get("prezzo_molto_basso_ammesso"):
        out["categoria"] = "candidato"
        out["motivi"].append("prezzo molto sotto il mercato (numero presente): controllare bene auto e documenti")
        return out
    out["categoria"] = "opportunita"
    out["motivi"].append(f"margine {margine} € ≥ soglia {soglia} € con {n} auto uguali "
                         + ("di commercianti" if fonte == "commercianti" else "vendute"))
    return out


# ------------------------------------------------------------------ rivalutazione una tantum (rivaluta_base:ora)

PAUSA_GRUPPO_S = 0.05          # dopo ogni gruppo marca/modello/alimentazione: il lavoro gira nel processo del sito
BLOCCO = 200                   # auto per chiamata a valuta_e_scrivi (gruppi interi)
MINUTI_MAX = 120              # oltre: si ferma e solleva (il lavoro resta "da finire": _lungo lo riprende al riavvio)

IDS_SQL = """
SELECT l.id, l.make, l.model, l.fuel FROM listings l
WHERE l.status = 'attivo' AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL
  AND l.price_eur IS NOT NULL
  AND (l.stage = 'approfondito'
       OR (l.province = ANY(%(prov)s) AND l.last_seen_at > now() - interval '5 days'
           AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s))
  -- ripresa dopo un'interruzione: le auto già valutate col calcolo nuovo nelle ultime ore non si rifanno
  AND NOT EXISTS (SELECT 1 FROM valutazioni_policy vp WHERE vp.listing_id = l.id AND vp.metodo = 'base_vendute'
                  AND vp.computed_at > now() - interval '6 hours')
ORDER BY lower(l.make), lower(l.model), l.fuel, l.id"""


def _blocchi(righe: list, blocco: int = BLOCCO) -> list[list]:
    """Id a blocchi di gruppi interi (marca, modello, alimentazione), al massimo `blocco` auto salvo un gruppo più
    grande (resta intero: una sola lettura della base per gruppo)."""
    out, cur, chiave = [], [], None
    for r in righe:
        k = (str(r["make"]).lower(), str(r["model"]).lower(), r["fuel"])
        if k != chiave and cur and len(cur) >= blocco:
            out.append(cur)
            cur = []
        chiave = k
        cur.append(r["id"])
    if cur:
        out.append(cur)
    return out


def rivaluta(spec: str = "", connetti=None, ves=None) -> dict:
    """Rivaluta col calcolo nuovo tutte le auto attive delle province del sito (anno e km del sito) e quelle oggi sul
    sito (stage 'approfondito'), scrive le valutazioni e pubblica le buone (valuta_e_scrivi(pubblica=True)): le auto
    che non sono più opportunità escono (il sito legge l'ultima valutazione). Solo conteggi nel log."""
    from .. import regole as RG
    from ..web import scovo
    from ..web.sito_regole import PROVINCE_100KM
    from . import policy_v2 as P
    from .valutazione import valuta_e_scrivi
    if connetti is None:
        from ..db import connect as connetti
    ves = ves or valuta_e_scrivi
    t0 = time.time()
    st: Counter = Counter()
    if not attivo():
        log.warning("RIVALUTA_BASE spento: VALORE_BASE=vecchio")
        return {"fermata": "valore_base_spento"}
    a = RG.regole().ammissione
    with connetti() as conn:
        righe = conn.execute(IDS_SQL, {"prov": sorted(PROVINCE_100KM), "anno": int(a.anno_min),
                                       "km": int(a.km_max)}).fetchall()
        conn.commit()
    blocchi = _blocchi([dict(r) for r in righe])
    st["auto"] = len(righe)
    fermata = None
    for pezzo in blocchi:
        if time.time() - t0 > MINUTI_MAX * 60:
            fermata = "tempo"
            break
        try:
            with connetti() as conn:
                prima = {r["listing_id"]: r["categoria"] for r in conn.execute(
                    "SELECT listing_id, categoria FROM valutazioni_policy WHERE listing_id = ANY(%s) "
                    "AND policy_version = %s", (pezzo, P.POLICY_VERSION)).fetchall()}
                conn.commit()
                es = ves(conn, pezzo, "rivaluta-base", pubblica=True, pausa_gruppo=PAUSA_GRUPPO_S)
                conn.commit()
        except Exception as e:
            st["blocchi_errore"] += 1
            log.warning("RIVALUTA_BASE blocco di %d auto non riuscito: %s", len(pezzo), type(e).__name__)
            continue
        st["blocchi"] += 1
        for k, n in es.stats.items():
            if k.startswith(("categoria:", "esclusa:", "scrittura:", "pubblica")) or k in ("errore", "pubblicate"):
                st[k] += n
        fuori = []
        for lid in pezzo:
            era, ora = prima.get(lid) in scovo.CATEGORIE_SITO, es.salvata.get(lid) in scovo.CATEGORIE_SITO
            if era and not ora:
                st["uscite_dal_sito"] += 1
            elif ora and not era:
                st["nuove_buone"] += 1
            elif era and ora:
                st["restano_buone"] += 1
            if lid in es.salvata and not ora:
                fuori.append(lid)
        if fuori:
            # top_auto (ricalcolo.utilitarie) fa passare anche una "non_conveniente" (scovo.abbastanza): un'auto che
            # col valore nuovo non è più buona non deve restare lì fino al prossimo giro delle utilitarie
            try:
                with connetti() as conn:
                    conn.execute("DELETE FROM top_auto WHERE listing_id = ANY(%s)", (fuori,))
                    conn.commit()
            except Exception as e:
                st["top_auto_errore"] += 1
                log.warning("RIVALUTA_BASE top_auto non aggiornata: %s", type(e).__name__)
    out = {"fermata": fermata, "secondi": round(time.time() - t0), "conteggi": dict(st)}
    log.warning("RIVALUTA_BASE %s", json.dumps(out, ensure_ascii=False))
    if fermata:
        raise RuntimeError(f"rivalutazione fermata ({fermata}): si riprende al prossimo avvio")
    return out
