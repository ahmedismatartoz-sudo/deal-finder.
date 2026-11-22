"""Valore di mercato del sito dalle auto VENDUTE e dai COMMERCIANTI (18/11, decisione del cliente, OK esplicito).

Cliente: "se un'auto la vende un commerciante quel prezzo è già il prezzo di vendita; noi dobbiamo trovare privati con
prezzi più convincenti dei loro". Il sito usa SOLO questo calcolo, sia per far entrare sia per far uscire le auto.

Per ogni auto (valutazione.valuta_e_scrivi, unico punto che scrive valutazioni_policy):
- base D = annunci ATTIVI di COMMERCIANTI ricontrollati negli ultimi base_vendute.FRESCHI_GIORNI giorni;
- base V = auto VENDUTE (sparite negli ultimi VENDUTE_GIORNI giorni dopo almeno MIN_GIORNI_ONLINE giorni online,
  ultimo prezzo prima di sparire), di privati e commercianti;
- solo auto pulite (base_vendute._pulita), stessa marca/modello/alimentazione, stessa ZONA del sito dell'auto
  (sito_regole: Milano = PROVINCE_100KM, Torino con Torino, Ravenna con Ravenna; le province lontane con Milano),
  confronti e correzioni di base_vendute.stima_dettaglio (anno ±1 / km ±25%, poi ±2 / ±40%, potenza ±10% e stesso
  cambio se noti, prezzi riportati all'anno/km dell'auto, mediana dopo il taglio 0,65–1,5);
- 19/11 (cliente: "gravissimo errore di gonfiamento prezzi: tante auto hanno un valore di mercato sovrastimato"),
  valore PRUDENTE (stima_prudente, parametri in regole.ValoreBase, dentro l'impronta delle regole);
- 19/11 pomeriggio, regola "mediana-1911b" (la prudente-1911 del mattino, 40° percentile + 3% + 300 €/D, correggeva
  troppo: sito da ~620 a 187 auto, misura BASE_VENDUTE −6,7% in media e −9,2% oltre 20.000 €):
  * stime D e V alla MEDIANA dei confronti tenuti (come il 18/11);
  * D scontata del prezzo "di vetrina" PER FASCIA del valore, interpolato senza scalini (regole.ValoreBase.sconti):
    ~23% fino a 4.500 €, 8,5% a 7.500, 4,5% a 12.500, 2% a 17.500, 0,5% da 22.500 € (scostamento atteso ≈ −1% per
    fascia sulla misura delle vendute del 19/11 10:47);
  * con le sole vendute: sconto della fascia meno la differenza commercianti/vendute (rapporto D/V), mai negativo;
  * R = min(V, D scontata) solo per commercianti sotto 5.000 € (D/V 1,078; vendute con almeno 3 confronti come
    tetto); sopra, R = D scontata (o V scontata senza commercianti);
  * livello 2 (anno ±2, km ±40%) con almeno 5 confronti come il 18/11; auto economiche (valore sotto 5.000 €) o con
    più di 10 anni solo con almeno 8 confronti a ogni livello (le stime più deboli fanno solo da tetto);
  * mai sopra il calcolo del 18/11;
- 19/11 sera, regola "mediana-1911c": stessi passi, `sconti` ricalibrati sulla misura per fascia della STIMA senza sconto
  (base_vendute:1911b, "prima|fascia_stima"): 5% fino a 4.500 € (il min con le vendute abbassa già), 4,5% a 7.500,
  4,4% a 12.500, 3,4% a 17.500, 3,2% da 22.500 € (dettagli in regole.ValoreBase); rilancio rivaluta_base:1911c;
  altrimenti nessuna stima (categoria dati_insufficienti). M = R (nessuna correzione chiesto → venduto: `calibrato` =
  1,0, il sito non la rifà).
- 22/11 (decisione del proprietario sulle misure del 20-22/11; "non gonfiare i prezzi"): le auto senza valore prudente
  (pochi_confronti:*) possono avere la STIMA DI RIPIEGO (stima_ripiego: commercianti e vendute insieme, 40° percentile,
  sconti, tetto delle stime deboli), SOLO nelle coppie livello/fascia di regole.StimaRipiego.fasce (unione 10-15k,
  15-20k, >20k; largo 15-20k, >20k; sconto extra 0; mai sotto 10.000 €) e con gli interruttori automatismi
  "stima_ripiego" / "stima_ripiego_largo" accesi. Marcate M_fonte "ripiego", sempre col controllo AI, al massimo
  "candidato", margine non oltre il 40% del valore; rilancio rivaluta_base:rip2211. Tutte le altre valutazioni restano
  identiche.
- margine = R − prezzo (cliente 18/11: i costi fissi del commerciante, fino a 200 € con policy_v2, NON si tolgono).
  Incidentate e difetti come prima (policy_v2, 4/11): restano fuori solo i danni gravi/strutturali o senza stima dei
  ricambi; le altre hanno R dalle auto pulite, meno lo storico del danno (0 di default) e il ripristino (ricambi,
  manodopera sui ricambi, difetti letti dall'AI: policy_v2.costi), e al massimo "candidato" (sezione Incidentate).
- categoria contro scovo.soglia(prezzo) (SOGLIE_PV invariate): opportunita se margine ≥ soglia, altrimenti
  non_conveniente; "candidato" solo dove serviva già ("Da trattare": policy_v2._sconto_da_trattare; prezzo molto
  basso ammesso col numero; danni/difetti da verificare).
- anti-truffa: gli stessi controlli di policy_v2 sul prezzo rispetto al mercato (minimo reale, sotto_m / basse con
  l'eccezione del numero) e in più margine oltre SOSPETTO_MARGINE (55%) di R = auto sospetta, fuori (dati sbagliati o
  truffa, come nella prova base_vendute). Le regole del sito (truffa, foto, zona, telefono, tetti...) restano dopo.

Il giudice AI dei confronti (ricalcolo._giudice_ai) qui NON serve: i confronti sono già filtrati per anno, km,
potenza, cambio e stato pulito e la stima è una mediana tagliata; nessuna chiamata AI (credito risparmiato).

Interruttore unico per tornare indietro in emergenza: VALORE_BASE=vecchio rimette il calcolo di prima (policy_v2 +
ripiego + cascata + giudice AI) in valuta_e_scrivi, l'esame ogni 3 ore, il valore reale notturno e ai_prezzi. Default
(assente o qualunque altro valore) = questo calcolo. Va impostato SOLO nel gruppo di variabili dealfinder-secrets
(uguale per sito, worker e cron: mai calcoli diversi tra processi). ATTENZIONE: il ritorno a "vecchio" fa ripassare
dal giudice AI tutte le auto buone alla prima rivalutazione (picco di credito AI): controllare il budget prima.
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
# 18/11 sera (cliente: "più auto senza toccare le regole"): commercianti del gruppo NON ricontrollati negli ultimi
# FRESCHI_GIORNI giorni (ma visti negli ultimi VECCHI_MAX_GIORNI): NON entrano MAI nella stima. Servono solo a dire
# quali annunci riaprire (confronti_mirati): riaperto vivo = di nuovo "fresco" (base D), sparito = venduto (base V).
VECCHI_MAX_GIORNI = 60
RINFRESCABILI_MAX = 12         # annunci da riaprire salvati per auto (solo id interni)

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


def zone() -> dict:
    """Zone del sito (sito_regole, le stesse di r_zona): ogni auto si confronta con le auto della SUA zona."""
    from ..web import sito_regole as SR
    out = {"milano": frozenset(SR.PROVINCE_100KM), "ravenna": frozenset(SR.RAVENNA)}
    if SR.TORINO:
        out["torino"] = frozenset(SR.TORINO)
    return out


def zona_di(provincia) -> str:
    """Nome della zona della provincia: Torino con Torino, Ravenna con Ravenna; il resto (Milano e dintorni, province
    lontane o mancanti) con le province di Milano, come la prova base_vendute."""
    p = str(provincia or "").strip().upper()
    for nome, prov in zone().items():
        if nome != "milano" and p in prov:
            return nome
    return "milano"


def province_sito() -> list:
    """Tutte le province delle zone del sito."""
    return sorted(set().union(*zone().values()))


def carica(conn, make: str, model: str, fuel: str, zona: str = "milano") -> dict:
    """{"D": commercianti freschi puliti, "V": vendute pulite} del gruppo nella zona (una query, solo questo gruppo)."""
    prov = zone().get(zona) or zone()["milano"]
    righe = conn.execute(GRUPPO_SQL, {"mk": _varianti(make), "md": _varianti(model), "fu": fuel,
                                      "prov": sorted(prov), "vg": BV.VENDUTE_GIORNI}).fetchall()
    mk, md = str(make or "").lower(), str(model or "").lower()
    rr = [norm(r) for r in righe if str(r["make"] or "").lower() == mk and str(r["model"] or "").lower() == md]
    base = dividi(rr)
    base["Dv"] = vecchi_commercianti(rr)           # solo per sapere cosa riaprire: mai nella stima
    return base


def dividi(righe: list) -> dict:
    """Le due basi di base_vendute (stessi criteri della prova): righe già normalizzate (norm)."""
    pulite = [r for r in righe if BV._pulita(r)]
    D = [r for r in pulite if r["status"] == "attivo" and r.get("seller_type") == "commerciante"
         and (r.get("giorni_visto") if r.get("giorni_visto") is not None else 99) <= BV.FRESCHI_GIORNI]
    V = [r for r in pulite if r["status"] == "scomparso" and (r.get("giorni_online") or 0) >= BV.MIN_GIORNI_ONLINE]
    return {"D": D, "V": V}


def vecchi_commercianti(righe: list) -> list:
    """Commercianti puliti ATTIVI non ricontrollati negli ultimi FRESCHI_GIORNI giorni (ma negli ultimi
    VECCHI_MAX_GIORNI): fuori dalla base D (regola del metodo, invariata). Righe già normalizzate (norm)."""
    out = []
    for r in righe:
        gv = r.get("giorni_visto")
        if (r.get("status") == "attivo" and r.get("seller_type") == "commerciante" and gv is not None
                and BV.FRESCHI_GIORNI < gv <= VECCHI_MAX_GIORNI and BV._pulita(r)):
            out.append(r)
    return out


def _simili(base: list, t: dict) -> list:
    """Confronti del livello più largo del metodo (anno ±2, km ±40%, potenza e cambio) PRIMA del taglio della mediana:
    solo per contare (diagnostica dei dati_insufficienti), mai per stimare."""
    anni, km = BV.LIVELLI[-1]
    return [(c, BV._adatta(c, t)) for c in base if BV._simile(c, t, anni, km)]


# 18/11 sera: motivo di ogni dati_insufficienti (log DATI_INSUFF_MOTIVI del sito e stats di valuta_e_scrivi). Le
# protezioni (anti-truffa, auto non pulita) restano tali: il conto serve a sapere quante auto si possono recuperare
# SENZA toccarle (solo "pochi_confronti:*").
MOTIVI_DATI = ("dati_auto_incompleti", "auto_non_pulita", "costi_non_stimabili", "antitruffa:margine_oltre_55",
               "antitruffa:sotto_minimo_reale", "antitruffa:troppo_sotto_mercato", "pochi_confronti:modello_assente",
               "pochi_confronti:nessuno_simile", "pochi_confronti:rinfrescabile", "pochi_confronti:solo_unendo",
               "pochi_confronti:pochi_simili", "pochi_confronti:fragile")


def motivo_dati(pv) -> str | None:
    """Motivo del dati_insufficienti di una valutazione salvata (riassunto del sito o dati completi). Le valutazioni
    scritte prima del 18/11 sera non hanno motivo_dati: si ricava dai campi che hanno."""
    if not isinstance(pv, dict) or pv.get("categoria") != "dati_insufficienti":
        return None
    if pv.get("motivo_dati"):
        return str(pv["motivo_dati"])
    if pv.get("metodo") != METODO:
        return "calcolo_vecchio"
    if pv.get("sospetto_truffa"):
        return "antitruffa:margine_oltre_55"
    if pv.get("non_pulita"):
        return "auto_non_pulita"
    if pv.get("M") is None:
        return "pochi_confronti"           # (o dati dell'auto incompleti: il riassunto vecchio non li distingue)
    return "antitruffa_o_costi"


def conta_motivi(items: list, ids) -> Counter:
    """Conti per motivo_dati delle auto `ids` (tolte dal sito al passo "valutata") tra `items` (elenco del sito)."""
    ids = set(ids or ())
    c: Counter = Counter()
    for it in items:
        if it.get("id") in ids:
            m = motivo_dati(it.get("pv"))
            if m:
                c[m] += 1
    return c


def _insuff(out: dict, motivo: str, testo: str) -> dict:
    out["categoria"] = "dati_insufficienti"
    out["motivo_dati"] = motivo
    out["motivi"].append(testo)
    return out


def _pochi(out: dict, tt: dict, D: list, V: list, Dv: list) -> dict:
    """Nessuna stima (meno di N_MIN_SITO confronti in D e in V): perché, e quali commercianti vecchi riaprire.
    Le stime restano quelle del metodo: qui solo conti e id da riaprire (confronti_mirati)."""
    sD, sV, sDv = _simili(D, tt), _simili(V, tt), _simili(Dv, tt)
    out.update(simili_D=len(sD), simili_V=len(sV), simili_vecchi=len(sDv), base_vecchi=len(Dv))
    if not D and not V and not Dv:
        sub = "modello_assente"
    elif not sD and not sV and not sDv:
        sub = "nessuno_simile"
    elif sDv and (len(sD) + len(sDv) >= N_MIN_SITO or len(sV) + len(sDv) >= N_MIN_SITO):
        # riaperti: vivi = commercianti freschi (D), spariti = vendute (V). Si salvano i più recenti (più probabilmente
        # ancora online) e una stima SOLO per decidere l'ordine dei controlli (mai usata come valore dell'auto)
        sub = "rinfrescabile"
        vecchi = sorted(sDv, key=lambda cp: (cp[0].get("giorni_visto") or 99, cp[0].get("id") or 0))
        prezzi = sorted(p for _c, p in sD + sDv + sV if p > 0)
        out["rinfresco"] = {"ids": [c.get("id") for c, _p in vecchi[:RINFRESCABILI_MAX] if c.get("id") is not None],
                            "stima_priorita": int(round(prezzi[len(prezzi) // 2])) if prezzi else None}
    elif len(sD) + len(sV) >= N_MIN_SITO:
        sub = "solo_unendo"             # commercianti e vendute insieme arriverebbero al minimo (NON usato: da misurare)
    else:
        sub = "pochi_simili"
    # 19/11 (cliente: confronti mirati ogni notte per TUTTE le pochi_confronti): mediana grezza dei pochi simili, SOLO
    # per l'ordine della raccolta notturna (confronti_mirati.notte: prima chi ha più margine potenziale). Mai un valore
    # dell'auto, mai sul sito, mai nel margine
    if sub != "rinfrescabile":
        prezzi = sorted(p for _c, p in sD + sDv + sV if p > 0)
        if prezzi:
            out["priorita_raccolta"] = {"stima_priorita": int(round(prezzi[len(prezzi) // 2])), "simili": len(prezzi)}
    return _insuff(out, "pochi_confronti:" + sub,
                   f"meno di {N_MIN_SITO} auto uguali pulite tra i commercianti (annunci degli ultimi "
                   f"{BV.FRESCHI_GIORNI} giorni) e tra le vendute degli ultimi {BV.VENDUTE_GIORNI} giorni")


# ------------------------------------------------------------------ valore prudente (19/11)

REGOLA = "mediana-1911c"


def eta_auto(anno, oggi=None) -> int:
    """Anni dell'auto (anno corrente − anno di immatricolazione; 0 se l'anno manca)."""
    import datetime as _dt
    if not anno:
        return 0
    return max(0, int((oggi or _dt.date.today()).year) - int(anno))


def interpola(x: float, punti) -> float:
    """Valore a `x` sulla spezzata dei `punti` ((x, y) in ordine): costante prima del primo e dopo l'ultimo punto,
    lineare tra due punti (niente scalini tra le fasce)."""
    pts = [(float(a), float(b)) for a, b in punti]
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def sconto_vetrina(valore_D: float, eta: int, vb) -> float:
    """Sconto dal prezzo chiesto da un commerciante (valore_D = stima dei commercianti) al valore di un'auto da
    privato: per fascia del valore (regole.ValoreBase.sconti, interpolato), + extra_vecchie oltre fragile_anni anni,
    tra sconto_min e sconto_max."""
    s = interpola(float(valore_D or 0), vb.sconti)
    if eta > vb.fragile_anni:
        s += vb.extra_vecchie
    return round(min(vb.sconto_max, max(vb.sconto_min, s)), 4)


def sconto_vendute(valore_V: float, eta: int, vb) -> float:
    """Sconto sulla stima delle sole VENDUTE (nessuna stima dei commercianti): le vendute sono già sotto i commercianti
    del rapporto D/V della fascia, quindi 1 − (1 − s)·D/V con s = sconto_vetrina del valore equivalente dei
    commercianti (V·D/V). Mai negativo: le vendute non si alzano mai."""
    r = interpola(float(valore_V or 0), vb.rapporto_dv)
    s = sconto_vetrina(valore_V * r, eta, vb)
    return round(max(0.0, 1 - (1 - s) * r), 4)


def stima_prudente(D: list, V: list, t: dict, vb, escludi=None, oggi=None) -> dict:
    """Valore prudente di `t` (riga normalizzata) dalle basi D (commercianti) e V (vendute), regole.ValoreBase
    (regola mediana-1911b). {"valore", "fonte" ("commercianti"/"vendute"/None), "scelta" (stima_dettaglio dei confronti
    mostrati), "stima_D", "n_D", "stima_V", "n_V", "prudenza": {...}, "fragile_poche"}. Nessuna stima: valore None."""
    q = vb.percentile
    nl = (vb.n_min, vb.n_min_livello2)
    d = BV.stima_dettaglio(D, t, escludi, n_min=vb.n_min, q=q, n_min_livelli=nl)
    v = BV.stima_dettaglio(V, t, escludi, n_min=vb.n_min, q=q, n_min_livelli=nl)
    # calcolo del 18/11 (mediana commercianti con ≥ 5 confronti, altrimenti mediana vendute): il valore non lo supera MAI
    if q == 0.5 and nl == (N_MIN_SITO, N_MIN_SITO) and vb.n_min == N_MIN_SITO:
        rif = (d or v or {}).get("valore")
    else:
        r18 = BV.stima_dettaglio(D, t, escludi, n_min=N_MIN_SITO) or BV.stima_dettaglio(V, t, escludi, n_min=N_MIN_SITO)
        rif = r18["valore"] if r18 else None
    eta = eta_auto(t.get("year"), oggi)
    pr: dict = {"regola": REGOLA, "percentile": q, "eta": eta}
    out = {"valore": None, "fonte": None, "scelta": None, "prudenza": pr, "fragile_poche": False,
           "stima_D": None, "n_D": 0, "stima_V": None, "n_V": 0}

    def netto_D(x):
        return x["valore"] * (1 - sconto_vetrina(x["valore"], eta, vb))

    def netto_V(x):
        return x["valore"] * (1 - sconto_vendute(x["valore"], eta, vb))

    def scegli(dd, vv):
        if dd:
            return netto_D(dd), "commercianti", dd
        if vv:
            return netto_V(vv), "vendute", vv
        return None, None, None

    valore, fonte, scelta = scegli(d, v)
    if valore is None:
        return out
    fragile = valore < vb.fragile_valore or eta > vb.fragile_anni
    deboli = (d, v)
    if fragile and scelta["n"] < vb.n_min_fragile:
        # auto economica o vecchia: almeno n_min_fragile confronti a OGNI livello; le stime più deboli restano tetti
        d8 = BV.stima_dettaglio(D, t, escludi, n_min=vb.n_min_fragile, q=q)
        v8 = BV.stima_dettaglio(V, t, escludi, n_min=vb.n_min_fragile, q=q)
        d, v = d8, v8
        valore, fonte, scelta = scegli(d, v)
        if valore is None:
            out.update(fragile_poche=True)
            pr["fragile"] = True
            return out
    if d:
        sc = sconto_vetrina(d["valore"], eta, vb)
        pr.update(D=int(round(d["valore"])), D_mediana=int(round(d["mediana"])), sconto_vetrina=sc,
                  D_netta=int(round(netto_D(d))), livello_D=d["livello"])
    if v:
        pr.update(V=int(round(v["valore"])), V_mediana=int(round(v["mediana"])), livello_V=v["livello"],
                  sconto_vendute=sconto_vendute(v["valore"], eta, vb), V_netta=int(round(netto_V(v))))
    out.update(stima_D=pr.get("D"), n_D=d["n"] if d else 0, stima_V=pr.get("V"), n_V=v["n"] if v else 0)
    # economiche (valore dei commercianti sotto tetto_V_sotto): minimo tra vendute e commercianti scontati
    # (D/V = 1,078 nella misura); le vendute con almeno n_tetto_V confronti fanno solo da tetto
    if d and d["valore"] < vb.tetto_V_sotto:
        tv = v or BV.stima_dettaglio(V, t, escludi, n_min=vb.n_tetto_V, q=q,
                                     n_min_livelli=(vb.n_tetto_V, max(vb.n_tetto_V, vb.n_min_livello2)))
        if tv and tv["valore"] < valore:
            pr.update(tetto_V=int(round(tv["valore"])), n_tetto_V=tv["n"])
            if v:
                fonte, scelta = "vendute", v
            valore = tv["valore"]
    # stime più deboli scartate per auto fragili (meno di n_min_fragile confronti): solo tetti, mai al rialzo
    dd, vd = deboli
    if dd and dd is not d and netto_D(dd) < valore:
        pr.update(tetto_D=int(round(netto_D(dd))), n_tetto_D=dd["n"])
        valore = netto_D(dd)
    if vd and vd is not v and netto_V(vd) < valore:
        pr.update(tetto_V=int(round(netto_V(vd))), n_tetto_V=vd["n"])
        valore = netto_V(vd)
    if rif is not None and valore > rif:
        valore = rif
    pr["fragile"] = valore < vb.fragile_valore or eta > vb.fragile_anni
    if pr["fragile"] and scelta["n"] < vb.n_min_fragile:
        # sceso sotto fragile_valore per lo sconto o un tetto: la stima mostrata deve avere n_min_fragile confronti
        d8 = d if d and d["n"] >= vb.n_min_fragile else BV.stima_dettaglio(D, t, escludi, n_min=vb.n_min_fragile, q=q)
        v8 = v if v and v["n"] >= vb.n_min_fragile else BV.stima_dettaglio(V, t, escludi, n_min=vb.n_min_fragile, q=q)
        alt = [(netto_D(d8), "commercianti", d8)] if d8 else []
        alt += [(netto_V(v8), "vendute", v8)] if v8 else []
        if not alt:
            out["fragile_poche"] = True
            return out
        a_val, fonte, scelta = min(alt, key=lambda x: x[0])
        valore = min(valore, a_val)
    out.update(valore=valore, fonte=fonte, scelta=scelta)
    return out


# ------------------------------------------------------------------ stima di ripiego (20/11 misura, 22/11 attiva)
# Proprietario 20/11 14:18: recuperare i "dati insufficienti" "in qualche modo", MA "non gonfiare i prezzi".
# B1 (20/11) = misura soltanto (base_vendute, log BASE_VENDUTE). B2 (22/11, decisione del proprietario sulle misure):
# valuta() usa stima_ripiego SOLO per le auto senza valore prudente e SOLO nelle coppie (livello, fascia) di
# regole.StimaRipiego.fasce, con gli interruttori automatismi "stima_ripiego" / "stima_ripiego_largo" accesi
# (livelli_accesi); sempre col controllo AI, al massimo "candidato". Ogni altra valutazione resta identica.
# Solo le due basi del metodo (commercianti freschi e vendute, pulite): mai il vecchio ripiego_prudente.

REGOLA_RIPIEGO = "ripiego-2011a"
# prova -> livelli (anno ±, km ±) provati in ordine. "unione": i livelli di oggi con commercianti e vendute INSIEME
# (copre pochi_confronti:solo_unendo e :fragile); "largo": anno ±3, km ±60% (parte di :pochi_simili); "largo4": anno
# ±4, km ±80%, SOLO misurato (mai attivabile).
RIPIEGO_PROVE = {"unione": BV.LIVELLI, "largo": ((3, 0.60),), "largo4": ((4, 0.80),)}
RIPIEGO_ATTIVABILI = ("unione", "largo")
RIPIEGO_PERCENTILE = 0.40      # default di regole.StimaRipiego (B2): percentile dei confronti tenuti
RIPIEGO_N_MIN = {"unione": 5, "largo": 6, "largo4": 6}
RIPIEGO_N_DEBOLE = 3           # stime di UNA sola base con almeno 3 confronti: solo tetti, mai al rialzo
RIPIEGO_MARGINE_MAX = 0.40     # B2: margine oltre il 40% del valore di ripiego = dati_insufficienti
RIPIEGO_EXTRA_MAX = 0.15       # sconto extra oltre il 15%: la coppia (livello, fascia) si scarta


def _ripiego_vuoto() -> dict:
    return {"valore": None, "livello": None, "n": 0, "quota_D": None, "grezzo": None, "mediana": None,
            "sconto_base": None, "sconto_extra": 0.0, "fascia": None, "tenuti": [], "regola": REGOLA_RIPIEGO}


def _ripiego_n_min(livello: str, rp=None) -> int:
    if livello == "unione":
        return int(getattr(rp, "n_min_unione", RIPIEGO_N_MIN["unione"]))
    return int(getattr(rp, "n_min_largo", RIPIEGO_N_MIN.get(livello, 6)))


def _ripiego_livelli(livello: str, rp=None) -> tuple:
    if livello == "largo" and rp is not None:
        return ((int(getattr(rp, "largo_anni", 3)), float(getattr(rp, "largo_km", 0.60))),)
    return tuple(RIPIEGO_PROVE.get(livello) or ())


def sconto_extra_ripiego(livello: str, fascia: str, rp=None) -> float:
    """Sconto extra della coppia (livello, fascia) nelle fasce ammesse (regole.StimaRipiego.fasce, B2); 0 se la coppia
    non c'è (B1: sempre 0, la misura è SENZA sconto extra)."""
    for f in (getattr(rp, "fasce", None) or ()):
        try:
            if f[0] == livello and f[1] == fascia:
                return min(RIPIEGO_EXTRA_MAX, max(0.0, float(f[2])))
        except (IndexError, TypeError, ValueError):
            continue
    return 0.0


def stima_ripiego(D: list, V: list, t: dict, vb, rp=None, escludi=None, oggi=None, solo=None) -> dict:
    """Stima di RIPIEGO di `t` quando stima_prudente non dà valore (va chiamata solo in quel caso): commercianti (D) e
    vendute (V) INSIEME, stessa marca/modello/alimentazione/zona (le basi del gruppo), potenza ±10% e stesso cambio se
    noti (BV._simile), prezzi riportati all'anno e ai km dell'auto (BV._adatta), taglio 0,65–1,5 sulla mediana.
    `solo` = prove da tentare in ordine (default RIPIEGO_ATTIVABILI: "unione", poi "largo"); vince la prima con
    abbastanza confronti. `rp` = regole.StimaRipiego (B2; None = default di questo modulo, senza sconto extra).
    Prudenze, tutte al ribasso:
    - `grezzo` = 40° percentile dei confronti tenuti (mai sopra la loro mediana);
    - sconto base = quota_D × sconto di vetrina + (1 − quota_D) × sconto delle vendute (regole.ValoreBase, quota_D =
      parte dei confronti tenuti che sono commercianti);
    - tetto: mai sopra le stime deboli di UNA sola base (almeno 3 confronti ai livelli di oggi, già scontate);
    - sconto extra della coppia (livello, fascia), applicato per ultimo (la misura senza extra scala esattamente).
    `fascia` = fascia di prezzo del valore grezzo (quella della misura "ripiego|<prova>|fascia_stima:" e delle coppie).
    {"valore", "livello", "n", "quota_D", "grezzo", "mediana", "sconto_base", "sconto_extra", "fascia", "tenuti",
    "anni", "km", "tetto"}; nessuna stima: valore None."""
    import statistics
    out = _ripiego_vuoto()
    prove = tuple(RIPIEGO_ATTIVABILI if solo is None else ((solo,) if isinstance(solo, str) else solo))
    q = float(getattr(rp, "percentile", RIPIEGO_PERCENTILE))
    id_D = {id(c) for c in D}
    base = [c for c in list(D) + list(V) if c is not escludi]
    if not base:
        return out
    eta = eta_auto(t.get("year"), oggi)
    for livello in prove:
        nm = _ripiego_n_min(livello, rp)
        for anni, km in _ripiego_livelli(livello, rp):
            coppie = [(c, BV._adatta(c, t)) for c in base if BV._simile(c, t, anni, km)]
            coppie = [(c, p) for c, p in coppie if p > 0]
            if len(coppie) < nm:
                continue
            m = statistics.median(p for _, p in coppie)
            tenuti = [(c, p) for c, p in coppie if BV.TAGLIO[0] * m <= p <= BV.TAGLIO[1] * m]
            if len(tenuti) < nm:
                continue
            prezzi = [p for _, p in tenuti]
            mediana = statistics.median(prezzi)
            grezzo = min(BV.percentile(prezzi, q), mediana)
            quota_D = sum(1 for c, _ in tenuti if id(c) in id_D) / len(tenuti)
            sb = quota_D * sconto_vetrina(grezzo, eta, vb) + (1 - quota_D) * sconto_vendute(grezzo, eta, vb)
            valore = grezzo * (1 - sb)
            tetto = None
            dd = BV.stima_dettaglio(D, t, escludi, n_min=RIPIEGO_N_DEBOLE)
            vd = BV.stima_dettaglio(V, t, escludi, n_min=RIPIEGO_N_DEBOLE)
            for x, netto in ((dd, sconto_vetrina), (vd, sconto_vendute)):
                if x:
                    nx = x["valore"] * (1 - netto(x["valore"], eta, vb))
                    if nx < valore:
                        valore = tetto = nx
            fascia = BV.fascia(grezzo)
            se = sconto_extra_ripiego(livello, fascia, rp)
            valore = valore * (1 - se)
            out.update(valore=valore, livello=livello, n=len(tenuti), quota_D=round(quota_D, 3), grezzo=grezzo,
                       mediana=mediana, sconto_base=round(sb, 4), sconto_extra=se, fascia=fascia, tenuti=tenuti,
                       anni=anni, km=km, tetto=int(round(tetto)) if tetto is not None else None)
            return out
    return out


def livelli_accesi(rp=None) -> tuple:
    """Livelli della stima di ripiego che il sito può usare ADESSO: () se nessuna fascia è ammessa (le fasce sono
    in regole.StimaRipiego, dal 22/11) o se l'interruttore automatismi "stima_ripiego" è spento;
    altrimenti i livelli presenti nelle fasce ammesse, senza "largo" se "stima_ripiego_largo" è spento. "largo4" mai.
    Con le fasce vuote non legge nemmeno gli interruttori (nessuna lettura del database). Mai un errore: ()."""
    try:
        if rp is None:
            from .. import regole as RG
            rp = getattr(RG.regole(), "stima_ripiego", None)
        presenti = {f[0] for f in (getattr(rp, "fasce", None) or ())}
        livelli = [x for x in RIPIEGO_ATTIVABILI if x in presenti]
        if not livelli:
            return ()
        from .. import automatismi
        if not automatismi.attivo("stima_ripiego"):
            return ()
        if not automatismi.attivo("stima_ripiego_largo"):
            livelli = [x for x in livelli if x != "largo"]
        return tuple(livelli)
    except Exception:
        return ()


def _quale(vb) -> str:
    return "mediana" if vb.percentile == 0.5 else f"{int(round(vb.percentile * 100))}° percentile"


def _pct(x: float) -> str:
    return f"{x * 100:.1f}".rstrip("0").rstrip(".").replace(".", ",") + "%"


# ------------------------------------------------------------------ valutazione di un'auto

def pulita(t: dict, cfg=None) -> str | None:
    """Motivo per cui l'auto da valutare NON si può valutare con i confronti puliti, oppure None.
    Come policy_v2 (4/11, sezione Incidentate): restano fuori solo i danni gravi/strutturali e quelli non ancora
    stimati; un danno leggero/medio con i ricambi stimati e i difetti letti dall'AI non gravi (valutazione.prepara:
    danni non letti o gravi sono già "esclusa") si valutano col valore delle auto pulite meno il ripristino (valuta).
    Annunci col prezzo non affidabile o scartati (truffa, doppione, ...) restano fuori come nelle due basi."""
    from . import policy_v2 as P
    danno = t.get("damage_class") or "sconosciuto"
    solo_sane = True if cfg is None else bool(cfg.solo_sane)
    if solo_sane and (danno in ("grave", "alto_rischio") or t.get("rischio_strutturale")):
        return "danno grave o strutturale"
    if solo_sane and danno in P.DANNEGGIATE and t.get("parts_cost_high") is None:
        return "danno senza stima dei ricambi"
    r = {"danno": "nessuno", "problema": False, "dichiarato": False,
         "stage": str(t.get("stage_reason") or "").split("|", 1)[0].strip(),
         "price_flags": t.get("price_flags") or []}
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


def _con_prudente(out: dict, sp: dict, scelta: dict, fonte: str, R: int, n: int, vb) -> None:
    """Campi e testi della valutazione col valore PRUDENTE (regola mediana-1911c): invariati dal 19/11."""
    out.update(M=R, R=R, R_finale=R, centrale_ridotto=R, riduzione=0.0, M_fonte=fonte, fonte_base=fonte,
               n=n, n_gate=n, n_gemelle=n, livello=scelta["livello"],
               campione="base preferita" if n >= 10 else "provvisoria (campione limitato)",
               R_da=("prezzi dei commercianti (annunci attivi) meno lo sconto di vetrina" if fonte == "commercianti"
                     else "prezzi delle auto vendute"),
               fascia=f"valore prudente = {_quale(vb)} dei "
                      + ("commercianti meno lo sconto di vetrina" if fonte == "commercianti" else "venduti")
                      + (" (con tetto)" if sp["prudenza"].get("tetto_V") or sp["prudenza"].get("tetto_D") else ""),
               venditori=dict(Counter(c.get("seller_type") or "sconosciuto" for c, _ in scelta["tenuti"])),
               confronti=[_confronto(c, p, fonte == "vendute")
                          for c, p in sorted(scelta["tenuti"], key=lambda cp: abs(cp[1] - R))[:CONFRONTI_SCHEDA]],
               valore_base={"fonte": fonte, "R": R, "n": n, "stima_D": out["stima_D"], "n_D": out["n_D"],
                            "stima_V": out["stima_V"], "n_V": out["n_V"], "regola": REGOLA,
                            "stima_D_netta": sp["prudenza"].get("D_netta"),
                            "sconto_vetrina": sp["prudenza"].get("sconto_vetrina"),
                            "tetto_V": sp["prudenza"].get("tetto_V"),
                            "sconto_vendute": sp["prudenza"].get("sconto_vendute")})
    al = BV.LIVELLI[scelta["livello"] - 1]
    pr = sp["prudenza"]
    out["ipotesi"].append(f"valore {R} € = {_quale(vb)} di "
                          f"{n} auto uguali "
                          + ("in vendita da commercianti (annunci ricontrollati negli ultimi "
                             f"{BV.FRESCHI_GIORNI} giorni)" if fonte == "commercianti" else
                             f"vendute negli ultimi {BV.VENDUTE_GIORNI} giorni (ultimo prezzo)")
                          + f", anno ±{al[0]} e km ±{int(al[1] * 100)}%, prezzi riportati all'anno e ai km dell'auto")
    if fonte == "commercianti" and pr.get("sconto_vetrina"):
        out["ipotesi"].append(f"prezzo dei commercianti {pr['D']} € meno {_pct(pr['sconto_vetrina'])} "
                              "(trattativa, preparazione e garanzia comprese nel prezzo di vetrina, per fascia di "
                              "prezzo: misura sulle auto vendute)")
    if fonte == "vendute" and pr.get("sconto_vendute") and not pr.get("tetto_V"):
        out["ipotesi"].append(f"prezzo delle vendute {pr['V']} € meno {_pct(pr['sconto_vendute'])} "
                              "(lo sconto di vetrina della fascia, tolta la differenza tra commercianti e vendute)")
    if pr.get("tetto_V"):
        out["ipotesi"].append(f"valore limitato dalle auto vendute ({pr['tetto_V']} €, {pr['n_tetto_V']} confronti)")
    if pr.get("tetto_D"):
        out["ipotesi"].append(f"valore limitato dai commercianti meno simili ({pr['tetto_D']} € già scontati, "
                              f"{pr['n_tetto_D']} confronti)")


def _con_ripiego(out: dict, rr: dict, da: str | None, id_D: set) -> None:
    """22/11: campi e testi della valutazione con la stima di RIPIEGO `rr` (stima_ripiego). `da` = motivo per cui il
    valore prudente non c'era (pochi_confronti:*). Il metodo resta "base_vendute"."""
    R = int(round(rr["valore"]))
    n = int(rr["n"])
    liv = rr["livello"]
    out.update(M=R, R=R, R_finale=R, centrale_ridotto=R, riduzione=0.0, M_fonte="ripiego",
               fonte_base="ripiego:" + liv, n=n, n_gate=n, n_gemelle=n, livello=_livello_numero(rr),
               campione="ripiego (confronti allargati)", serve_controllo_ai=True,
               R_da="stima di ripiego: commercianti e auto vendute insieme (confronti allargati), meno lo sconto",
               fascia="stima di ripiego = valore basso di commercianti e venduti insieme, meno lo sconto"
                      + (" (con tetto)" if rr.get("tetto") is not None else ""),
               venditori=dict(Counter(c.get("seller_type") or "sconosciuto" for c, _ in rr["tenuti"])),
               confronti=[_confronto(c, p, id(c) not in id_D)
                          for c, p in sorted(rr["tenuti"], key=lambda cp: abs(cp[1] - R))[:CONFRONTI_SCHEDA]],
               stima_ripiego={"livello": liv, "n": n, "fascia": rr["fascia"],
                              "sconto_extra": rr["sconto_extra"], "da": da, "regola": REGOLA_RIPIEGO},
               valore_base={"fonte": "ripiego", "R": R, "n": n, "stima_D": out.get("stima_D"), "n_D": out.get("n_D"),
                            "stima_V": out.get("stima_V"), "n_V": out.get("n_V"), "regola": REGOLA_RIPIEGO,
                            "livello": liv, "quota_D": rr["quota_D"], "grezzo": int(round(rr["grezzo"])),
                            "mediana": int(round(rr["mediana"])), "sconto_base": rr["sconto_base"],
                            "sconto_extra": rr["sconto_extra"], "tetto": rr.get("tetto")})
    nD = sum(1 for c, _ in rr["tenuti"] if id(c) in id_D)
    out["ipotesi"].append(f"valore {R} € = stima di ripiego: mancano abbastanza auto uguali per il valore prudente, "
                          f"quindi {n} auto simili prese insieme ({nD} in vendita da commercianti, {n - nD} vendute "
                          f"negli ultimi {BV.VENDUTE_GIORNI} giorni), anno ±{rr['anni']} e km "
                          f"±{int(round(rr['km'] * 100))}%, prezzi riportati all'anno e ai km dell'auto")
    out["ipotesi"].append(f"si prende un valore basso (sotto la mediana di {int(round(rr['mediana']))} €): "
                          f"{int(round(rr['grezzo']))} € meno {_pct(rr['sconto_base'])} (sconto di vetrina e delle "
                          "vendute, per fascia di prezzo)"
                          + (f" e meno {_pct(rr['sconto_extra'])} di prudenza in più" if rr["sconto_extra"] else ""))
    if rr.get("tetto") is not None:
        out["ipotesi"].append(f"valore limitato dalla stima di una sola base (commercianti o vendute): "
                              f"{rr['tetto']} € già scontati")


def _senza_stima(out: dict, sp: dict, tt: dict, D: list, V: list, base: dict, lid, vb) -> dict:
    """Il valore prudente non c'è: dati_insufficienti col suo motivo (pochi_confronti:*), come dal 18-19/11."""
    if sp.get("fragile_poche"):
        # 19/11: solo l'ordine della raccolta notturna dei confronti (confronti_mirati.notte), mai un valore
        _pz = sorted(p for _c, p in _simili(D, tt) + _simili(V, tt) if p > 0)
        if _pz:
            out["priorita_raccolta"] = {"stima_priorita": int(round(_pz[len(_pz) // 2])), "simili": len(_pz)}
        return _insuff(out, "pochi_confronti:fragile",
                       f"auto economica o con più di {vb.fragile_anni} anni: servono almeno {vb.n_min_fragile} "
                       "auto uguali per un valore prudente")
    Dv = [c for c in base.get("Dv") or [] if lid is None or c.get("id") != lid]
    return _pochi(out, tt, D, V, Dv)


def ripiego_ammesso(D: list, V: list, tt: dict, vb, rp, livelli=None) -> dict | None:
    """22/11: la stima di ripiego di `tt` (auto senza valore prudente) se si può usare ADESSO, altrimenti None.
    Livelli accesi (livelli_accesi: fasce di regole.StimaRipiego e interruttori automatismi), nell'ordine "unione"
    poi "largo": vale la prima prova con abbastanza confronti, e solo se la sua coppia (livello, fascia del valore
    grezzo) è tra le fasce ammesse. Coppia spenta (sotto 10.000 €, "largo" 10-15k...) = None: non si prova altro."""
    livelli = livelli_accesi(rp) if livelli is None else tuple(livelli)
    if not livelli:
        return None
    rr = stima_ripiego(D, V, tt, vb, rp=rp, solo=livelli)
    if rr.get("valore") is None or not rr["valore"] > 0:
        return None
    for f in (getattr(rp, "fasce", None) or ()):
        if f[0] == rr["livello"] and f[1] == rr["fascia"]:
            return rr
    return None


def _livello_numero(rr: dict) -> int:
    """Numero del livello dei confronti di una stima di ripiego: 1 o 2 = i livelli di oggi (unione), 3 = largo."""
    for i, (anni, km) in enumerate(BV.LIVELLI, start=1):
        if rr.get("livello") == "unione" and (rr.get("anni"), rr.get("km")) == (anni, km):
            return i
    return len(BV.LIVELLI) + 1


def valuta(t: dict, base: dict, soglia: int, rg=None, cfg=None) -> dict:
    """Valutazione di `t` (riga di listings preparata da valutazione.prepara) con le basi del gruppo (carica/dividi).
    Stessa forma delle valutazioni di policy_v2 per i campi che il sito e le schede leggono (categoria, M, R,
    R_finale, margine, soglia, n, metodo, confronti, motivi, ipotesi, netto...).
    22/11: un'auto senza valore prudente può avere la stima di RIPIEGO (ripiego_ammesso). In quel caso: metodo
    "base_vendute", M_fonte "ripiego", fonte_base "ripiego:<livello>", stima_ripiego {...}, serve_controllo_ai, e la
    categoria non supera MAI "candidato". Se col ripiego l'auto finirebbe comunque "dati insufficienti" (margine
    oltre regole.StimaRipiego.margine_max del valore, anti-truffa, costi non stimabili) torna ESATTAMENTE la
    valutazione senza stima di prima (stesso motivo pochi_confronti:*), con in più `ripiego_scartato` (senza valori)."""
    stato: dict = {}
    out = _valuta(t, base, soglia, rg, cfg, stato)
    rr = stato.get("ripiego")
    if rr is None:
        return out                         # valore prudente o nessuna stima: come prima, niente da toccare
    if out.get("categoria") == "dati_insufficienti":
        senza = stato["senza"]
        senza["ripiego_scartato"] = {"livello": rr["livello"], "fascia": rr["fascia"],
                                     "motivo": out.get("motivo_dati")}
        return senza
    out["serve_controllo_ai"] = True
    if out.get("categoria") == "opportunita":
        out["categoria"] = "candidato"     # mai "opportunita" con una stima di ripiego
    out["motivi"].append("valore da una stima di ripiego (confronti allargati): al massimo candidato, da verificare "
                         "con il controllo AI")
    return out


def _valuta(t: dict, base: dict, soglia: int, rg, cfg, stato: dict) -> dict:
    import copy
    from .. import regole as RG
    from . import policy_v2 as P
    rg = rg or RG.regole()
    cfg = cfg or P.PolicyConfig(rg)
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
        return _insuff(out, "dati_auto_incompleti", "dati dell'auto incompleti: " + ", ".join(out["mancanti"]))
    sporca = pulita(t, cfg)
    if sporca:
        out["non_pulita"] = sporca
        return _insuff(out, "auto_non_pulita",
                       f"auto non confrontabile con auto pulite vendute o di commercianti ({sporca})")
    tt = norm(t)
    lid = t.get("id")
    D = [c for c in base.get("D") or [] if lid is None or c.get("id") != lid]
    V = [c for c in base.get("V") or [] if lid is None or c.get("id") != lid]
    vb = rg.valore_base
    sp = stima_prudente(D, V, tt, vb)
    out.update(stima_D=sp["stima_D"], n_D=sp["n_D"], stima_V=sp["stima_V"], n_V=sp["n_V"],
               base_D=len(D), base_V=len(V), prudenza=sp["prudenza"])
    scelta, fonte = sp["scelta"], sp["fonte"]
    rr = None
    if not scelta:
        rp = getattr(rg, "stima_ripiego", None)
        rr = ripiego_ammesso(D, V, tt, vb, rp) if rp is not None else None
        if rr is None:
            return _senza_stima(out, sp, tt, D, V, base, lid, vb)
        # 22/11: stima di ripiego. La valutazione senza stima (identica a prima) resta da parte: si ridà se col
        # ripiego l'auto non arriva a una categoria (valuta)
        stato["senza"] = _senza_stima(copy.deepcopy(out), sp, tt, D, V, base, lid, vb)
        stato["ripiego"] = rr
        _con_ripiego(out, rr, stato["senza"].get("motivo_dati"), set(map(id, D)))
        R, n, fonte = out["R"], out["n"], "ripiego"
    else:
        R = int(round(sp["valore"]))
        n = int(scelta["n"])
        _con_prudente(out, sp, scelta, fonte, R, n, vb)
    p = int(prezzo)
    # incidentate e difetti (come policy_v2, 4/11): valore delle auto pulite, meno lo storico del danno (0 di default)
    # e il ripristino (ricambi stimati, manodopera sui ricambi, difetti letti dall'AI). I costi fissi del commerciante
    # NON si tolgono (cliente 18/11: margine = valore − prezzo): totale_costi = solo il ripristino
    danno = t.get("damage_class") or "sconosciuto"
    Rf = R
    if danno in P.DANNEGGIATE and cfg.perdita_storico_danno:
        perdita = int(round(R * cfg.perdita_storico_danno, -1))
        out["perdita_storico_danno"] = perdita
        Rf = R - perdita
    out["R_finale"] = Rf
    cs = P.costi(t, Rf, cfg)
    if cs["blocco"]:
        return _insuff(out, "costi_non_stimabili", cs["blocco"])
    voci = {k: x for k, x in cs["voci"].items() if k != "costi_commerciante"}
    ripristino = int(cs["ripristino"])
    out["costi"] = voci
    out["totale_costi"] = ripristino
    if voci.get("difetti"):
        out["costo_difetti_incluso"] = True          # il sito non toglie di nuovo il costo dei difetti AI
    if ripristino:
        out["ipotesi"].append(f"ripristino stimato {ripristino} € (ricambi, manodopera, difetti) tolto dal margine")
    da_verificare = bool(cs["danneggiata"] or voci.get("difetti") or cs["alto_rischio"])
    margine = Rf - p - ripristino
    out["margine"] = int(margine)
    out["sotto_R"] = p < Rf
    try:
        from . import netto as NT
        nt = NT.calcola(Rf, p, ripristino)
        if nt:
            out["netto"] = nt["netto"]
            out["costi_netto"] = dict(nt["voci"], sconto_pct=nt["sconto_pct"], sconto_da=nt["sconto_da"])
    except Exception:
        pass
    if rr is not None and margine > float(getattr(rg.stima_ripiego, "margine_max", RIPIEGO_MARGINE_MAX)) * R:
        # 22/11: con la stima di ripiego un margine così alto non è credibile: resta senza stima (valuta)
        return _insuff(out, "ripiego:margine_oltre_max",
                       "margine troppo alto per una stima di ripiego: resta senza stima")
    # anti-truffa: margine oltre il 55% del valore = auto sospetta (dati sbagliati, rata, truffa): fuori
    if margine > SOSPETTO_MARGINE * R:
        out["sospetto_truffa"] = True
        return _insuff(out, "antitruffa:margine_oltre_55",
                       f"margine oltre il {int(SOSPETTO_MARGINE * 100)}% del valore: prezzo non credibile "
                       "(dati sbagliati, rata o truffa)")
    # gli stessi controlli di policy_v2 sul prezzo chiesto rispetto al mercato (M = R)
    limite = at.sotto_m
    if p <= at.basse_max and n >= at.basse_confronti:
        limite = min(limite, at.basse)
    if p < R * at.minimo_reale:
        return _insuff(out, "antitruffa:sotto_minimo_reale",
                       f"prezzo sotto il {int(at.minimo_reale * 100)}% del mercato: non è un prezzo vero")
    if p < R * limite and t.get("targa_e_telefono"):
        out["prezzo_molto_basso_ammesso"] = True
        out["ipotesi"].append("prezzo molto sotto il mercato: ammessa perché l'annuncio mostra il numero del venditore")
    elif p < R * limite:
        return _insuff(out, "antitruffa:troppo_sotto_mercato",
                       f"prezzo oltre il {int(round((1 - limite) * 100))}% sotto il mercato: dati non "
                       "affidabili (versione diversa, dati sbagliati o truffa)")
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
    if out.get("prezzo_molto_basso_ammesso") or da_verificare:
        out["categoria"] = "candidato"
        if out.get("prezzo_molto_basso_ammesso"):
            out["motivi"].append("prezzo molto sotto il mercato (numero presente): controllare bene auto e documenti")
        if cs["danneggiata"]:
            out["motivi"].append("auto danneggiata: ripristino e manodopera da verificare")
        if voci.get("difetti"):
            out["motivi"].append("difetti scritti nell'annuncio: costo di riparazione stimato dall'AI, da verificare")
        return out
    out["categoria"] = "opportunita"
    out["motivi"].append(f"margine {margine} € ≥ soglia {soglia} € con {n} auto "
                         + ("simili tra commercianti e vendute (stima di ripiego)" if rr is not None else
                            "uguali " + ("di commercianti" if fonte == "commercianti" else "vendute")))
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
  -- ripresa dopo un'interruzione: le auto già valutate col calcolo nuovo E con le regole di oggi (19/11: impronta delle
  -- regole, che contiene regole.ValoreBase) nelle ultime ore non si rifanno; quelle valutate con le regole di prima sì
  AND NOT EXISTS (SELECT 1 FROM valutazioni_policy vp WHERE vp.listing_id = l.id AND vp.metodo = 'base_vendute'
                  AND vp.impronta_regole = %(imp)s AND vp.computed_at > now() - interval '6 hours')
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
        from .valutazione import impronta_regole
        righe = conn.execute(IDS_SQL, {"prov": province_sito(), "anno": int(a.anno_min),
                                       "km": int(a.km_max), "imp": impronta_regole()}).fetchall()
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
