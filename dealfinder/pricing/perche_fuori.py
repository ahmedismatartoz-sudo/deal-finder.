"""Perché le auto restano fuori dal sito (16/11, cliente: "il calcolo fa ancora uscire auto buone").

Sentinella del 16/11: margine_minimo 0,714 delle auto scartate (mediana 7 giorni 0,599), valutata.dati_insufficienti
0,039 (0,020); sito da ~600 a ~440 auto dal 13/11. La prova alla cieca ha misurato solo NOVITA_FASE2 sul prezzo M; qui
si misurano le regole del 12/11 (e quelle vicine) che possono ABBASSARE la rivendita R o il margine, o mandare
un'auto in dati_insufficienti, UNA ALLA VOLTA:

  rivendita_commercianti  R = min(M, R dei commercianti)                  regole.rivendita.attiva
  allestimento_livelli    confronti per livello 0–3, fattore 0,04         regole.allestimento.attivo
  calibrazione_sito       chiesto → venduto sul sito (R = min(R, M×f))    regole.calibrazione.attiva
  calibrazione_scalini    fattore interpolato (False = per fascia)        regole.calibrazione.interpola
  calibrazione_riserva    riserva 0,87/0,915/0,97 nelle fasce non misurate  regole.calibrazione.riserva
  calibra_vr_combinato    V al venduto nel combinato e nell'accordo       regole.calibrazione.vr_nel_combinato
  giudice_ai              l'AI toglie confronti (qui SOLO archivio ai_giudizi: nessuna chiamata, costo zero)
  ripiego_nord_italia     confronti dal Nord / da tutta Italia            regole.zona.ripiego_nord, .italia
  costi                   ricambi e difetti letti dall'AI tolti dal margine (policy_v2.costi e sito)
  dispersione             2–7 gemelle disperse oltre 1,8× = dati_insufficienti (12/11 E3)
  km_min                  tasso km stimato almeno 0,8% ogni 10.000 km (12/11 E8; prima 0)
  ampiezza_poche          errore delle gemelle dall'ampiezza con 2–4 gemelle (12/11 E3)
  pooling_tassi           tassi anno/km ristretti verso marca/tutti (12/11 sera)
  regole_10_11            tutte le novità del 12/11 spente insieme (quelle sopra tranne giudice, ripiego, costi,
                          calibrazione sito e riserva, che c'erano già prima)

`scrittura.giudice_vince` non cambia il calcolo (solo quale riga resta salvata): si contano le righe salvate giudicate
e quelle "in attesa del giudice" nel controllo.

Due misure, sola lettura (sessione READ ONLY, nessuna scrittura, nessuna AI):
1. auto ATTIVE della zona (sito_regole.PROVINCE_100KM), con il numero (valutazione.telefono), privati e commercianti
   della fascia richiesta, la cui ultima valutazione è fuori dal sito: margine del sito (dopo la calibrazione e il
   costo del difetto) sotto la soglia di al massimo 3.000 €, oppure dati_insufficienti. Si rifà la valutazione con le
   regole in vigore (controllo: stesso risultato salvato?) e con ogni variante; per variante: quante passano la soglia
   del sito (scovo.soglia, anche "Da trattare"), quante escono da dati_insufficienti;
2. auto VENDUTE (status scomparso negli ultimi `giorni` giorni, zona, come la prova alla cieca): per variante l'errore
   della rivendita R del sito (dopo la calibrazione) rispetto all'ultimo prezzo, con lo scostamento medio (+ = troppo
   alto), sulle stesse auto e solo sulle auto che la variante cambia.

Righe di log PERCHE_FUORI: controllo, riepilogo_attive, riepilogo_vendute e al massimo 30 esempi anonimi (marca,
modello, anno, km, prezzo, M, R, soglia, margine per variante; niente id, telefono, URL o venditori).
Lancio una tantum: BREVO_IMPORT_LISTE=perche_fuori:600 (web/app.py; perche_fuori:N:venduti:giorni, default 600:300:30).
"""
from __future__ import annotations

import gc
import hashlib
import json
import logging
import re
import statistics
import time
from collections import Counter

from .. import regole as RG

log = logging.getLogger("perche_fuori")

FASCIA_SOTTO = 3000                  # margine del sito al massimo 3.000 € sotto la soglia
MAX_ESEMPI = 30
MEMORIA_MAX_MB = 470                 # come il freno di Subito: sopra, gc + malloc_trim; se resta sopra ci si ferma
                                     # (risultati parziali nel log, "fermata": "memoria")
PAUSA_S = 0.003                      # dopo ogni valutazione: il sito resta reattivo (stesso processo)

# (nome, campi di regole da cambiare, opzioni). Opzioni: giudice=False (niente archivio), costi=False (margine senza
# ricambi/difetti), solo_sito=True (il calcolo della policy è quello in vigore: cambia solo il sito)
NOVITA_12_11 = {"rivendita.attiva": False, "allestimento.attivo": False, "calibrazione.interpola": False,
                "calibrazione.vr_nel_combinato": False, "confronti.dispersione_max": 1e9,
                "confronti.ampiezza_poche": False, "correzione.km_min": 0.0, "correzione.pooling": False}
VARIANTI = (
    ("vigore", {}, {}),
    ("rivendita_commercianti", {"rivendita.attiva": False}, {}),
    ("allestimento_livelli", {"allestimento.attivo": False}, {}),
    ("calibrazione_sito", {"calibrazione.attiva": False}, {"solo_sito": True}),
    ("calibrazione_scalini", {"calibrazione.interpola": False}, {}),
    ("calibrazione_riserva", {"calibrazione.riserva": ()}, {}),
    ("calibra_vr_combinato", {"calibrazione.vr_nel_combinato": False}, {}),
    ("giudice_ai", {}, {"giudice": False}),
    ("ripiego_nord_italia", {"zona.ripiego_nord": False, "zona.italia": False}, {}),
    ("costi", {}, {"costi": False, "solo_sito": True}),
    ("dispersione", {"confronti.dispersione_max": 1e9}, {}),
    ("km_min", {"correzione.km_min": 0.0}, {}),
    ("ampiezza_poche", {"confronti.ampiezza_poche": False}, {}),
    ("pooling_tassi", {"correzione.pooling": False}, {}),
    ("regole_10_11", NOVITA_12_11, {}),
)
SIGLA = {"dati_insufficienti": "DI", "esclusa": "ES"}

# 16/11 sera (produzione: "canceling statement due to statement timeout" dopo 3 minuti): prima UNA query leggeva il
# JSON completo di valutazioni_policy.dati (decine di KB per riga, TOAST) e il testo dell'annuncio per TUTTE le auto
# candidate della zona prima di ORDER BY md5 ... LIMIT. Ora due passi: (1) solo colonne leggere (id, categoria,
# margine, prezzo) con il LIMIT; (2) JSON e testo solo per gli id scelti, a blocchi di DETTAGLI_BLOCCO.
ATTIVE_IDS_SQL = """
SELECT l.id FROM valutazioni_policy v
JOIN listings l ON l.id = v.listing_id
WHERE v.policy_version = %(pv)s
  AND v.categoria IN ('dati_insufficienti', 'non_conveniente', 'candidato', 'opportunita')
  AND (v.categoria = 'dati_insufficienti' OR (v.margine IS NOT NULL AND v.margine >= {soglia} - %(sotto)s))
  AND l.status = 'attivo' AND l.province = ANY(%(prov)s) AND l.telefono_ok IS NOT FALSE
  AND l.price_eur BETWEEN 500 AND 200000 AND l.year >= %(anno)s AND l.mileage_km BETWEEN 0 AND %(km)s
  AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL
  AND COALESCE(l.stage_reason, '') NOT IN ('doppione', 'truffa', 'prezzo_finto', 'dati_incoerenti')
  AND left(COALESCE(l.stage_reason, ''), 15) <> 'analisi_manuale'
ORDER BY md5(l.id::text) LIMIT %(pool)s"""

ATTIVE_DETTAGLI_SQL = """
SELECT l.id, l.make, l.model, l.fuel, l.year, l.mileage_km, l.price_eur, l.seller_type, l.first_seen_at,
       l.telefono_ok, CASE WHEN l.telefono_ok IS NOT TRUE
                           THEN left(COALESCE(l.title, '') || ' ' || COALESCE(l.description, ''), 6000) END AS testo,
       v.categoria, v.margine, v.computed_at,
       (v.dati->>'M')::numeric AS m, (v.dati->>'R')::numeric AS r, (v.dati->>'R_finale')::numeric AS r_finale,
       v.dati->>'M_fonte' AS m_fonte, v.dati->>'metodo' AS metodo, v.dati->>'calibrato' AS calibrato,
       (v.dati->>'R_commercianti')::numeric AS r_commercianti,
       COALESCE((v.dati->>'costo_difetti_incluso') = 'true', false) AS costo_difetti_incluso,
       COALESCE((v.dati->>'giudice_mancante') = 'true', false) AS in_attesa,
       COALESCE((v.dati->'ai_confronti'->>'tolti')::int, 0) AS tolti,
       v.dati->'motivi'->>0 AS motivo
FROM listings l
JOIN valutazioni_policy v ON v.listing_id = l.id AND v.policy_version = %(pv)s
WHERE l.id = ANY(%(ids)s)"""
DETTAGLI_BLOCCO = 200

VENDUTE_SQL = """
SELECT l.id, l.make, l.model, l.fuel, l.price_eur FROM listings l
WHERE l.status = 'scomparso' AND l.last_seen_at > now() - make_interval(days => %(giorni)s + 30)
  AND l.disappeared_at > now() - make_interval(days => %(giorni)s)
  AND l.province = ANY(%(prov)s) AND l.price_eur BETWEEN 1000 AND 200000 AND l.year >= %(anno)s
  AND l.mileage_km BETWEEN 0 AND %(km)s AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL
  AND COALESCE(l.damage_class, 'sconosciuto') IN ('nessuno', 'sconosciuto')
  AND COALESCE(l.stage_reason, '') NOT IN ('doppione', 'truffa', 'prezzo_finto', 'dati_incoerenti')
  AND EXISTS (SELECT 1 FROM valutazioni_policy v WHERE v.listing_id = l.id)       -- chiave primaria (listing_id, ...)
ORDER BY md5(l.id::text) LIMIT %(n)s"""


# ------------------------------------------------------------------ varianti e calibrazione

def varianti(rg: RG.Regole | None = None) -> list[tuple[str, RG.Regole, dict]]:
    rg = rg or RG.regole()
    return [(nome, RG.con_campi(rg, campi) if campi else rg, dict(op)) for nome, campi, op in VARIANTI]


def leggi_calibrazione(conn) -> dict:
    """Ultima misura chiesto → venduto (tabella calibrazione), come il sito (_fattori_calibra) e valutazione.ingressi."""
    try:
        r = conn.execute("SELECT dati FROM calibrazione ORDER BY at DESC LIMIT 1").fetchone()
    except Exception:
        conn.rollback()
        return {}
    d = (r or {}).get("dati") if r else None
    return (json.loads(d) if isinstance(d, str) else d) or {}


def fattori(cal: dict, rg: RG.Regole) -> list:
    return RG.fattori_calibrazione(cal.get("fasce") or {}, rg, misurate_vr=cal.get("fasce_vr") or {},
                                   per_M=cal.get("fasce_su") == "M")


# ------------------------------------------------------------------ giudice: solo archivio

def archivio(conn, lid) -> list[dict]:
    """Giudizi già dati per questo annuncio (ai_giudizi): nessuna chiamata, nessuna scrittura."""
    try:
        righe = conn.execute("SELECT chiave, via FROM ai_giudizi WHERE listing_id = %s", (lid,)).fetchall()
    except Exception:
        conn.rollback()
        return []
    out = []
    for r in righe:
        v = r["via"] if isinstance(r["via"], dict) else json.loads(r["via"] or "{}")
        out.append({"chiave": r["chiave"], "via": v})
    return out


def giudizio_archivio(righe: list[dict], prezzo, confronti: list[dict]) -> tuple[dict | None, str]:
    """Come ai/giudice.giudica(solo_archivio): giudizio con la stessa chiave, oppure i giudizi già dati su questa auto
    allo stesso prezzo confronto per confronto. ("assente": nessun giudizio; "parziale": solo una parte dei confronti
    di oggi è stata giudicata, in produzione l'AI vedrebbe gli altri)."""
    ids = sorted(int(c["id"]) for c in confronti if c.get("id") is not None)
    if not ids:
        return None, "senza_confronti"
    chiave = hashlib.sha1(json.dumps([prezzo, ids]).encode()).hexdigest()[:16]
    giudicati, via = set(), {}
    for r in righe:
        v = r["via"]
        if v.get("_illeggibile"):
            continue
        if r["chiave"] == chiave:
            return {int(k): m for k, m in v.items() if not k.startswith("_")}, "archivio"
        if v.get("_prezzo") != prezzo or not isinstance(v.get("_giudicati"), list):
            continue
        giudicati |= {int(x) for x in v["_giudicati"]}
        via.update({int(k): m for k, m in v.items() if not k.startswith("_")})
    if not giudicati:
        return None, "assente"
    stato = "archivio" if set(ids) <= giudicati else "parziale"
    return {i: m for i, m in via.items() if i in set(ids)}, stato


def applica_giudice(t: dict, v: dict, lista: list, soglia: int, cfg, righe: list[dict]) -> tuple[dict, str]:
    """ricalcolo._giudice_ai con il solo archivio: solo per le auto che andrebbero sul sito (come in produzione)."""
    from . import policy_v2 as P
    if not cfg.regole.giudice_ai or v.get("categoria") not in ("opportunita", "candidato") or not v.get("confronti"):
        return v, "non_serve"
    via, stato = giudizio_archivio(righe, t.get("price_eur"), v["confronti"])
    if not via:
        return v, stato
    v2 = P.valuta(t, [m for m in lista if m.get("id") not in via], soglia, cfg)
    if v.get("fuori_zona"):
        v2["fuori_zona"] = True
    v2["ai_confronti"] = {"tolti": len(via)}
    return v2, stato


# ------------------------------------------------------------------ sito

def sito(v: dict, fat: list, rg: RG.Regole, difetto: int = 0, costi: bool = True) -> dict:
    """Numeri del sito per questa valutazione: calibrazione chiesto → venduto (sito_regole.calibra_venduto) e costo
    del difetto letto dall'AI se non è già nel calcolo (sito_regole.t_margine_sito). costi=False: margine senza
    ricambi/difetti (variante "costi")."""
    cat = v.get("categoria")
    R = v.get("R_finale") if v.get("R_finale") is not None else v.get("R")
    out = {"cat": cat, "M": v.get("M"), "R": R, "margine": None}
    if cat in ("dati_insufficienti", "esclusa") or v.get("margine") is None or not v.get("M") or R is None:
        return out
    pv2 = RG.calibra_vendita(v, list(fat), rg)
    R2 = pv2.get("R_finale") if pv2.get("R_finale") is not None else pv2.get("R", R)
    giu = float(R) - float(R2)
    if not rg.calibrazione.alza:
        giu = max(0.0, giu)
    giu = int(round(giu))
    m = int(v["margine"]) - giu
    if difetto and not v.get("costo_difetti_incluso"):
        m -= int(difetto)
    if not costi:
        m += int(v.get("totale_costi") or 0) + (int(difetto) if difetto and not v.get("costo_difetti_incluso") else 0)
    out.update(M=pv2.get("M"), R=int(round(float(R))) - giu, margine=m)
    return out


_G_SITO: dict = {}


def passa(cat, margine, row: dict) -> bool:
    """Margine minimo del sito (sito_regole.r_margine_minimo, senza supercar): soglia o "Da trattare"."""
    from ..web import scovo
    from ..web import sito_regole as SR
    if cat not in ("opportunita", "candidato", "non_conveniente") or margine is None:
        return False
    p = int(row.get("price_eur") or 0)
    if margine >= scovo.soglia(p):
        return True
    if "g" not in _G_SITO:
        _G_SITO["g"] = SR.Regole()
    it = {"pv": {"margine": margine}, "prezzo": p, "price_eur": p, "mileage_km": row.get("mileage_km"),
          "fuel": row.get("fuel"), "year": row.get("year"), "visto_dal": row.get("first_seen_at")}
    try:
        return SR.sconto_trattativa(it, _G_SITO["g"]) is not None
    except Exception:
        return False


def margine_salvato(row: dict, fat: list, rg: RG.Regole, difetto: int = 0) -> int | None:
    """Margine del sito dell'ultima valutazione salvata (colonne ridotte di ATTIVE_DETTAGLI_SQL)."""
    if row.get("margine") is None or row.get("m") is None:
        return None
    pv = {"categoria": row.get("categoria"), "M": float(row["m"]), "margine": int(row["margine"]),
          "R": float(row["r"]) if row.get("r") is not None else None,
          "R_finale": float(row["r_finale"]) if row.get("r_finale") is not None else None,
          "M_fonte": row.get("m_fonte"), "metodo": row.get("metodo"),
          "costo_difetti_incluso": row.get("costo_difetti_incluso")}
    if row.get("calibrato") is not None:
        pv["calibrato"] = row["calibrato"]
    if row.get("r_commercianti") is not None:
        pv["R_commercianti"] = float(row["r_commercianti"])
    return sito(pv, fat, rg, difetto)["margine"]


def motivo_breve(m) -> str:
    """Motivo di dati_insufficienti senza numeri (per contarli)."""
    s = re.sub(r"[\d.,]+\s*(×|€|%)?", "#", str(m or "?"))
    return s[:70]


# ------------------------------------------------------------------ calcolo per gruppo

class Gruppo:
    """Mercato, commercianti e ripiego di UN gruppo (marca, modello, carburante): uno alla volta, poca memoria."""

    def __init__(self, conn, key, rg):
        from ..store import HOME_REGION_SQL
        from . import rivendita as RV
        from .ricalcolo import MARKET_SQL, _j
        self.key = key
        self.mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                        for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
        self.commercianti = RV.carica(conn, *key, HOME_REGION_SQL, rg)
        self.cache: dict = {}                              # ripiego Nord/Italia (ricalcolo._valuta_base)
        self.dot = bool(rg.trazione.attiva or rg.dotazioni.aggiusta)
        if self.dot:
            from . import dotazioni as DT
            self.mercato = DT.arricchisci(self.mercato, DT.carica_mercato(conn, self.mercato))


def valuta_varianti(conn, r: dict, ing: dict, gr: Gruppo, var: list, fat: dict, giudizi: list,
                    escludi_se_stessa: bool = False) -> dict:
    """{variante: {"cat", "M", "R", "margine", "giudice"}} per l'auto r (riga di valutazione.COLONNE)."""
    from ..web import scovo
    from . import policy_v2 as P
    from . import valutazione as VA
    from .ricalcolo import _valuta_base
    soglia = scovo.soglia(int(r["price_eur"]))
    difetto = int((ing["difetti"].get(r["id"]) or {}).get("costo") or 0)
    mercato = [m for m in gr.mercato if m.get("id") != r["id"]] if escludi_se_stessa else gr.mercato
    out, base = {}, None
    for nome, rg, op in var:
        f = fat[nome]
        if op.get("solo_sito") and base is not None:
            v, stato = base
        else:
            t, motivo = VA.prepara(r, dict(ing, fattori_calibra=f), rg)
            if motivo and not escludi_se_stessa:
                v, stato = VA.esclusa(t, motivo, rg), "non_serve"
            else:
                t["commercianti"] = gr.commercianti if rg.rivendita.attiva else None
                if gr.dot:
                    from . import dotazioni as DT
                    t.update(DT.carica_ids(conn, [r["id"]]).get(r["id"]) or {})
                cfg = P.PolicyConfig(rg)
                v, lista = _valuta_base(conn, t, mercato, soglia, cfg, gr.cache)
                if op.get("giudice", True):
                    v, stato = applica_giudice(t, v, lista, soglia, cfg, giudizi)
                else:
                    stato = "spento"
            if nome == "vigore":
                base = (v, stato)
        s = sito(v, f, rg, difetto, costi=op.get("costi", True))
        s["giudice"] = stato
        out[nome] = s
        time.sleep(PAUSA_S)
    return out


MEMORIA: dict = {}
ERRORI: Counter = Counter()


def _errore(conn, dove: str, e: Exception) -> None:
    """Errore di una query (es. statement timeout): rollback, conteggio e riga di log; la diagnosi continua."""
    try:
        conn.rollback()
    except Exception:
        pass
    ERRORI[dove] += 1
    if ERRORI[dove] <= 3:
        log.warning("PERCHE_FUORI errore %s: %s: %s", dove, type(e).__name__, str(e)[:200])


def _per_gruppi(conn, ids: list, rg, memoria_mb: float):
    """(gruppo, righe complete, ingressi) un gruppo alla volta; si ferma (StopIteration con "memoria") se la memoria
    del processo resta sopra memoria_mb anche dopo il rilascio."""
    from .. import memoria as MEM
    from . import valutazione as VA
    try:
        righe = conn.execute("SELECT id, make, model, fuel FROM listings WHERE id = ANY(%s)", (list(ids),)).fetchall()
    except Exception as e:
        _errore(conn, "gruppi", e)
        return
    gruppi: dict = {}
    for x in righe:
        gruppi.setdefault((x["make"], x["model"], x["fuel"]), []).append(x["id"])
    for key in sorted(gruppi, key=lambda k: tuple(str(x) for x in k)):
        MEMORIA["max"] = max(MEMORIA.get("max") or 0, round(MEM.rss_mb()))
        if memoria_mb and MEM.rss_mb() > memoria_mb:
            gc.collect()
            MEM.rilascia("perche_fuori", forza=True)
            if MEM.rss_mb() > memoria_mb:
                yield None, None, None
                return
        pezzo = gruppi[key]
        try:
            complete = {x["id"]: dict(x) for x in conn.execute(
                f"SELECT {VA.COLONNE} FROM listings l WHERE l.id = ANY(%s)", (pezzo,)).fetchall()}
            ing = VA.ingressi(conn, pezzo)
            gr = Gruppo(conn, key, rg)
        except Exception as e:                        # una query lenta o un errore: si salta il gruppo, non tutto
            _errore(conn, "gruppo", e)
            continue
        yield gr, [complete[i] for i in pezzo if i in complete], ing
        del gr, complete, ing


# ------------------------------------------------------------------ misure

def _mediana(xs):
    return int(statistics.median(xs)) if xs else None


def scegli_attive(conn, n: int, rg, fat_vigore: list) -> tuple[list[dict], Counter]:
    """Le n auto attive fuori dal sito per margine (entro FASCIA_SOTTO sotto la soglia) o dati_insufficienti."""
    from ..pipeline.scelta_candidate import soglia_sql
    from ..web import scovo
    from ..web.sito_regole import PROVINCE_100KM
    from . import policy_v2 as P
    from . import valutazione as VA
    a = rg.ammissione
    ids = [x["id"] for x in conn.execute(ATTIVE_IDS_SQL.format(soglia=soglia_sql("l.price_eur")),
                                         {"pv": P.POLICY_VERSION, "prov": sorted(PROVINCE_100KM), "anno": a.anno_min,
                                          "km": a.km_max, "sotto": FASCIA_SOTTO, "pool": n * 5}).fetchall()]
    pool = []
    for k in range(0, len(ids), DETTAGLI_BLOCCO):
        pezzo = ids[k:k + DETTAGLI_BLOCCO]
        det = {x["id"]: dict(x) for x in conn.execute(ATTIVE_DETTAGLI_SQL,
                                                      {"pv": P.POLICY_VERSION, "ids": pezzo}).fetchall()}
        pool += [det[i] for i in pezzo if i in det]              # stesso ordine (md5) del primo passo
    dif = {}
    for k in range(0, len(pool), DETTAGLI_BLOCCO):
        dif.update(VA.ingressi(conn, [x["id"] for x in pool[k:k + DETTAGLI_BLOCCO]], solo=("difetti",))["difetti"])
    conta, scelte = Counter(), []
    for x in pool:
        x["title"], x["description"] = x.pop("testo", None), ""
        if VA.telefono(x) is not True:
            conta["senza_telefono"] += 1
            continue
        if x.get("seller_type") == "commerciante" and not RG.benzina_richiesta(x, rg):
            conta["commerciante_fuori_fascia"] += 1
            continue
        x.pop("title", None), x.pop("description", None)
        costo = int((dif.get(x["id"]) or {}).get("costo") or 0)
        if x["categoria"] == "dati_insufficienti":
            x["gruppo"] = "dati_insufficienti"
        else:
            ms = margine_salvato(x, fat_vigore, rg, costo)
            x["margine_sito_salvato"] = ms
            if passa(x["categoria"], ms, x):
                conta["gia_sul_margine"] += 1
                continue
            if ms is None or ms < scovo.soglia(int(x["price_eur"])) - FASCIA_SOTTO:
                conta["troppo_sotto"] += 1
                continue
            x["gruppo"] = "sotto_soglia"
        scelte.append(x)
        if len(scelte) >= n:
            break
    conta["pool"] = len(pool)
    return scelte, conta


def misura_attive(conn, n: int, rg, var, fat, memoria_mb: float) -> tuple[dict, list, dict]:
    try:
        sel, conta = scegli_attive(conn, n, rg, fat["vigore"])
    except Exception as e:
        _errore(conn, "scelta_attive", e)
        sel, conta = [], Counter(scelta_non_riuscita=1)
    per_id = {x["id"]: x for x in sel}
    risultati = {}
    fermata = None
    for gr, righe, ing in _per_gruppi(conn, list(per_id), rg, memoria_mb):
        if gr is None:
            fermata = "memoria"
            break
        for r in righe:
            try:
                risultati[r["id"]] = valuta_varianti(conn, r, ing, gr, var, fat, archivio(conn, r["id"]))
            except Exception as e:
                conn.rollback()
                conta["errore"] += 1
                if conta["errore"] <= 3:
                    log.warning("PERCHE_FUORI auto non valutata: %s", str(e)[:200])
    # controllo: le regole in vigore danno il risultato salvato?
    ctr = Counter()
    for lid, res in risultati.items():
        s, v = per_id[lid], res["vigore"]
        cat_s = s["categoria"]
        if s.get("in_attesa"):
            ctr["salvata_in_attesa_giudice"] += 1
        if s.get("tolti"):
            ctr["salvata_giudicata"] += 1
        if v["cat"] == cat_s or (cat_s in ("opportunita", "candidato") and v["cat"] in ("opportunita", "candidato")):
            ms, mv = s.get("margine_sito_salvato"), v.get("margine")
            if ms is None or mv is None or abs(ms - mv) <= 100:
                ctr["uguale"] += 1
            else:
                ctr["stessa_categoria_margine_diverso"] += 1
        else:
            ctr[f"diversa:{cat_s}->{v['cat']}"] += 1
        if passa(v["cat"], v.get("margine"), s):
            ctr["passerebbe_gia_con_le_regole_in_vigore"] += 1
            if v.get("giudice") in ("assente", "parziale"):
                ctr["passerebbe_gia_ma_senza_giudizio_in_archivio"] += 1
    righe = []
    for lid, res in risultati.items():
        s = per_id[lid]
        righe.append({"s": s, "res": res})
    return _riassunto_attive(righe, var, conta, ctr, fermata), righe, dict(ctr)


def _riassunto_attive(righe: list, var, conta: Counter, ctr: Counter, fermata) -> dict:
    out = {"auto": len(righe), "scelta": dict(conta), "fermata": fermata,
           "gruppi": dict(Counter(r["s"]["gruppo"] for r in righe))}
    di_motivi = Counter(motivo_breve(r["s"].get("motivo")) for r in righe if r["s"]["gruppo"] == "dati_insufficienti")
    out["dati_insufficienti_motivi_salvati"] = dict(di_motivi.most_common(12))
    vig_di = Counter(r["res"]["vigore"]["cat"] for r in righe)
    out["vigore_categorie"] = dict(vig_di)
    per = {}
    for nome, _, _ in var:
        passano = sum(1 for r in righe if passa(r["res"][nome]["cat"], r["res"][nome]["margine"], r["s"]))
        non_giud = sum(1 for r in righe if passa(r["res"][nome]["cat"], r["res"][nome]["margine"], r["s"])
                       and r["res"][nome].get("giudice") in ("assente", "parziale"))
        di_v = [r for r in righe if r["res"]["vigore"]["cat"] == "dati_insufficienti"]
        esce = [r for r in di_v if r["res"][nome]["cat"] not in ("dati_insufficienti", "esclusa")]
        nuove = [r for r in righe if not passa(r["res"]["vigore"]["cat"], r["res"]["vigore"]["margine"], r["s"])
                 and passa(r["res"][nome]["cat"], r["res"][nome]["margine"], r["s"])]
        delta = [r["res"][nome]["margine"] - r["res"]["vigore"]["margine"] for r in righe
                 if r["res"][nome]["margine"] is not None and r["res"]["vigore"]["margine"] is not None]
        per[nome] = {"passano": passano, "passano_senza_giudizio_archivio": non_giud,
                     "in_piu_rispetto_al_vigore": len(nuove),
                     "in_piu_da_sotto_soglia": sum(1 for r in nuove if r["s"]["gruppo"] == "sotto_soglia"),
                     "in_piu_da_dati_insufficienti": sum(1 for r in nuove if r["s"]["gruppo"] == "dati_insufficienti"),
                     "dati_insufficienti": sum(1 for r in righe if r["res"][nome]["cat"] == "dati_insufficienti"),
                     "escono_da_dati_insufficienti": len(esce),
                     "delta_margine_mediano": _mediana(delta),
                     "delta_margine_medio": int(sum(delta) / len(delta)) if delta else None,
                     "auto_con_margine_cambiato": sum(1 for d in delta if abs(d) > 50)}
    out["varianti"] = per
    out["controllo"] = dict(ctr)
    return out


def scegli_vendute(conn, n: int, giorni: int, rg) -> list[dict]:
    from ..web.sito_regole import PROVINCE_100KM
    a = rg.ammissione
    return [dict(x) for x in conn.execute(VENDUTE_SQL, {"giorni": giorni, "prov": sorted(PROVINCE_100KM),
                                                        "anno": a.anno_min, "km": a.km_max, "n": n}).fetchall()]


def misura_vendute(conn, n: int, giorni: int, rg, var, fat, memoria_mb: float) -> dict:
    from ..ai.prova_cieca import metriche
    try:
        sel = {x["id"]: x for x in scegli_vendute(conn, n, giorni, rg)}
    except Exception as e:
        _errore(conn, "scelta_vendute", e)
        sel = {}
    risultati, errori, fermata = {}, 0, None
    for gr, righe, ing in _per_gruppi(conn, list(sel), rg, memoria_mb):
        if gr is None:
            fermata = "memoria"
            break
        for r in righe:
            try:
                risultati[r["id"]] = valuta_varianti(conn, r, ing, gr, var, fat, archivio(conn, r["id"]),
                                                     escludi_se_stessa=True)
            except Exception as e:
                conn.rollback()
                errori += 1
                if errori <= 3:
                    log.warning("PERCHE_FUORI venduta non valutata: %s", str(e)[:200])
    nomi = [n_ for n_, _, _ in var]
    vero = {i: int(sel[i]["price_eur"]) for i in risultati}

    def r_di(i, nome):
        x = risultati[i][nome]
        return x["R"] if x["cat"] not in ("dati_insufficienti", "esclusa") and x.get("R") else None
    tutte = [i for i in risultati if all(r_di(i, k) for k in nomi)]
    out = {"auto": len(risultati), "giorni": giorni, "errori": errori, "fermata": fermata,
           "stesse_auto": len(tutte), "varianti": {}}
    for nome in nomi:
        proprie = [(r_di(i, nome), vero[i]) for i in risultati if r_di(i, nome)]
        cambiate = [i for i in risultati if r_di(i, nome) and r_di(i, "vigore")
                    and abs(r_di(i, nome) / r_di(i, "vigore") - 1) > .01]
        d = {"tutte_le_sue": metriche(proprie), "stesse_auto": metriche([(r_di(i, nome), vero[i]) for i in tutte])}
        if nome != "vigore":
            d["cambiate"] = {"n": len(cambiate),
                             "vigore": metriche([(r_di(i, "vigore"), vero[i]) for i in cambiate]),
                             "variante": metriche([(r_di(i, nome), vero[i]) for i in cambiate])}
        out["varianti"][nome] = d
    return out


def esempi(righe: list, var, quanti: int = MAX_ESEMPI) -> list[dict]:
    """Auto che passerebbero con almeno una variante (le più variate prima): solo dati dell'auto, niente id."""
    from ..web import scovo
    nomi = [n for n, _, _ in var]
    cand = []
    for r in righe:
        s, res = r["s"], r["res"]
        k = sum(1 for n in nomi if passa(res[n]["cat"], res[n]["margine"], s))
        if k and not passa(res["vigore"]["cat"], res["vigore"]["margine"], s):
            cand.append((k, r))
    cand.sort(key=lambda x: (-x[0], str(x[1]["s"].get("make")), int(x[1]["s"].get("price_eur") or 0)))
    out = []
    for _, r in cand[:quanti]:
        s, res = r["s"], r["res"]
        v = res["vigore"]
        out.append({"auto": f"{s.get('make')} {s.get('model')}", "anno": s.get("year"), "km": s.get("mileage_km"),
                    "prezzo": int(s["price_eur"]), "soglia": scovo.soglia(int(s["price_eur"])),
                    "venditore": "commerciante" if s.get("seller_type") == "commerciante" else "privato",
                    "salvata": [s["categoria"], s.get("margine_sito_salvato")],
                    "vigore": {"M": v.get("M"), "R": v.get("R"), "margine": v.get("margine"), "cat": v["cat"],
                               "giudice": v.get("giudice")},
                    "margini": {n: (res[n]["margine"] if res[n]["margine"] is not None
                                    else SIGLA.get(res[n]["cat"], res[n]["cat"])) for n in nomi if n != "vigore"},
                    "R": {n: res[n].get("R") for n in nomi if n != "vigore" and res[n].get("R") != v.get("R")}})
    return out


def esegui(conn, n: int = 600, n_venduti: int = 300, giorni: int = 30, memoria_mb: float = MEMORIA_MAX_MB) -> dict:
    """Diagnosi completa (sola lettura). Righe PERCHE_FUORI nel log; restituisce il riepilogo."""
    from .. import memoria as MEM
    t0 = time.time()
    MEMORIA.clear()
    ERRORI.clear()
    MEMORIA["inizio"] = round(MEM.rss_mb())
    rg = RG.regole()
    var = varianti(rg)
    cal = leggi_calibrazione(conn)
    fat = {nome: fattori(cal, r) for nome, r, _ in var}
    n, n_venduti, giorni = max(1, min(int(n), 2000)), max(0, min(int(n_venduti), 1000)), max(1, min(int(giorni), 90))
    att, righe, _ = misura_attive(conn, n, rg, var, fat, memoria_mb)
    att["secondi"] = round(time.time() - t0)
    att["errori_query"] = dict(ERRORI)
    conn.rollback()
    ctrl = {"regole": rg.versione, "impronta": RG.impronta(rg), "giudice_vince": rg.scrittura.giudice_vince,
            "fattori_calibra": {str(nm): (x if not isinstance(x, tuple) else None) for _, nm, x in fat["vigore"]
                                if not str(nm).startswith("_q")},
            "fattori_calibra_misura": {k: (v or {}).get("n") for k, v in (cal.get("fasce") or {}).items()},
            "calibra_fasce_su": cal.get("fasce_su"), **att.pop("controllo")}
    log.warning("PERCHE_FUORI controllo %s", json.dumps(ctrl, ensure_ascii=False, default=str))
    log.warning("PERCHE_FUORI riepilogo_attive %s", json.dumps(att, ensure_ascii=False, default=str))
    for e in esempi(righe, var):
        log.warning("PERCHE_FUORI esempio %s", json.dumps(e, ensure_ascii=False, default=str))
    del righe
    gc.collect()
    t1 = time.time()
    ven = misura_vendute(conn, n_venduti, giorni, rg, var, fat, memoria_mb) if n_venduti else {"auto": 0}
    ven["secondi"] = round(time.time() - t1)
    ven["memoria_mb"] = dict(MEMORIA)
    ven["errori_query"] = dict(ERRORI)
    conn.rollback()
    log.warning("PERCHE_FUORI riepilogo_vendute %s", json.dumps(ven, ensure_ascii=False, default=str))
    MEM.rilascia("perche_fuori", forza=True)
    return {"controllo": ctrl, "attive": att, "vendute": ven, "secondi": round(time.time() - t0)}


def lavoro(spec: str) -> dict | None:
    """BREVO_IMPORT_LISTE=perche_fuori:N[:venduti[:giorni]] (web/app.py): connessione propria in sola lettura (non
    quella del pool del sito), chiusa alla fine."""
    import psycopg
    from psycopg.rows import dict_row

    from ..config import settings
    parti = spec.split(":")

    def num(i, d):
        try:
            return int(parti[i]) if len(parti) > i and parti[i] else d
        except ValueError:
            return d
    n = num(1, 600)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        # solo questa connessione (non quella del sito): una query può durare fino a 10 minuti; se scade comunque la
        # diagnosi salta quel pezzo e scrive i risultati parziali (_errore)
        conn.execute("SET statement_timeout = '600s'")
        conn.commit()
        try:
            return esegui(conn, n=n, n_venduti=num(2, n // 2), giorni=num(3, 30))
        finally:
            conn.rollback()
