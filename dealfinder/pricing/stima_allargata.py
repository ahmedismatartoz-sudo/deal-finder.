"""Stima ALLARGATA per le auto "dati insufficienti" (25/11, ramo confronti-ombra). SOLO IN OMBRA di default.

Cliente: più auto sul sito SENZA cambiare nessuna regola (SOGLIE_PV e margini minimi, zone, 150.000 km, anno 2007,
telefono, tetti 60/40 dei commercianti, anti-truffa, no Roma, no Loconte) e "non gonfiare i prezzi".
Oggi il sito toglie ~1.800 auto al passo "valutata" con il motivo dati_insufficienti. Diagnosi (pricing/valore_base):
  pochi_confronti:*            il valore prudente chiede 5 auto uguali (anno ±1 / km ±25%, poi ±2 / ±40%, potenza ±10%,
                               stesso cambio) tra i SOLI commercianti freschi o tra le SOLE vendute della zona, 8 per le
                               auto sotto 5.000 € o con più di 10 anni (:fragile); la stima di ripiego del 22/11 unisce
                               le due basi ma vale solo da 10.000 € in su ("largo" da 15.000 €). Sotto-casi:
                               modello_assente, nessuno_simile, pochi_simili, solo_unendo, rinfrescabile, fragile;
  antitruffa:*, ripiego:margine_oltre_max, auto_non_pulita, costi_non_stimabili, dati_auto_incompleti
                               protezioni o dati dell'auto mancanti (anno, km, prezzo): NON si toccano.
Qui si prova una stima più larga SOLO per le pochi_confronti:* (mai per le auto scartate dall'anti-truffa o con una
stima di ripiego già scartata), con le stesse due basi pulite del metodo (commercianti freschi + vendute, insieme).
Prove in ordine, vince la prima che riesce (PROVE):
  zona        i livelli di oggi (anno ±1 / km ±25%, poi ±2 / ±40%) con le due basi INSIEME, anche sotto 10.000 €;
  zona_larga  anno ±3, km ±60%;
  italia      (istruzioni del cliente, 25/11 sera) stesso modello e alimentazione in TUTTA Italia (mai le province di
              Roma), anno ±2, confronti fino a KM_CONFRONTI_MAX (400.000 km) col prezzo riportato ai km dell'auto
              (la correzione del metodo, BV._adatta: 8,5% per anno, 1,25% ogni 10.000 km; verso l'ALTO mai oltre il
              tetto di regole.Correzione, +25%). Gli annunci arrivano dalle ricerche mirate in tutta Italia
              (confronti_mirati.cerca_italia: Subito e AutoScout24, una ricerca per modello ogni 24 ore, tetto per
              notte) e servono SOLO da confronto: mai sul sito (stage 'mercato', motivo 'mercato_nord' = "mai
              proposta"; le zone del sito e i 150.000 km delle auto mostrate non cambiano).
Potenza ±10% e stesso cambio quando noti (BV._simile: l'allestimento non conta già oggi), prezzi riportati all'anno e
ai km dell'auto (BV._adatta), taglio 0,65–1,5 sulla mediana.
PESI (ogni prova): un confronto pesa 1 / (1 + km di distanza / 100) × 1 / (1 + differenza di km / 50.000). La distanza
è in linea d'aria tra i capoluoghi di provincia, dal capoluogo dell'AUTO valutata (Milano se la provincia manca): per
le auto di Milano è la distanza da Milano, per Torino e Ravenna conta il loro mercato. Esempi: stessa provincia 1,
100 km 0,5, 300 km 0,25, 900 km 0,1; 50.000 km di differenza dimezzano ancora. Servono almeno PESO_MIN (2,5) di peso
totale: otto annunci tutti lontanissimi non fanno una stima.
Prudenze, TUTTE al ribasso (nel dubbio il valore esce più basso, mai più alto):
  - percentile BASSO dei confronti tenuti (35° → 30°; −5 punti senza potenza o cambio noti): il MINORE tra il
    percentile pesato e quello non pesato, mai sopra la mediana (i pesi possono solo abbassare);
  - sconto di vetrina / delle vendute di regole.ValoreBase (lo stesso del valore prudente), poi uno sconto di
    prudenza in più per prova (3% → 5%);
  - più confronti delle prove normali quando si allarga (5 / 6 / 6) e almeno N_MIN_FRAGILE (8) per le auto
    economiche o vecchie (stessa regola "fragile" del valore prudente);
  - tetto di dispersione: (75° − 25° percentile) / mediana oltre DISPERSIONE_MAX = nessuna stima da quella prova;
  - tetti: mai sopra le stime deboli di UNA sola base (almeno 3 confronti, già scontate), della zona e d'Italia;
  - anti-truffa NON alleggerita dal valore più basso: prezzo sotto il 60% del valore di RIFERIMENTO (mediana dei
    confronti meno lo sconto base, senza le prudenze in più) = resta fuori; poi gli stessi controlli di valore_base;
  - al massimo "candidato", sempre col controllo AI (serve_controllo_ai), mai "Da trattare", margine non oltre il 40%
    del valore; margine minimo = scovo.soglia(prezzo) (SOGLIE_PV) come ogni altra auto.

OMBRA (default): le valutazioni e il sito NON cambiano. valutazione.valuta_e_scrivi aggiunge alla valutazione
"dati_insufficienti" il campo `stima_allargata` (solo numeri: esito, prova, n, R, margine); il sito, ogni
LOG_OGNI_S secondi, rifà l'imbuto su una COPIA dell'elenco in cui quelle auto hanno la stima allargata (web/app →
ombra_sito) e scrive UNA riga `CONFRONTI_OMBRA {json}` di soli conteggi + ESEMPI_MAX esempi (id, mai telefoni).
In più, su 1 auto ogni CAMPIONE_PRECISIONE col valore normale, la stima allargata calcolata COME SE mancasse il
valore (campo `allargata_prova`): errore rispetto al valore normale, nella stessa riga ("precisione").

MODO_REALE = True (costante qui sotto, NON una variabile d'ambiente): la valutazione "candidato" con la stima
allargata sostituisce il "dati_insufficienti" (M_fonte "allargata", etichetta ETICHETTA), entra nella coda del
controllo AI come gli altri candidati e sul sito è segnata `stima_allargata` con l'avviso AVVISO. FASCE_REALI limita
il modo reale alle fasce di prezzo decise sulla misura. Tornare a False = quelle auto escono al passo "valutata".
Nessun parametro è in regole.py: l'impronta delle regole non cambia.
"""
from __future__ import annotations

import copy
import json
import logging
import statistics
import time
from collections import Counter
from typing import NamedTuple

from . import base_vendute as BV
from . import valore_base as VB

log = logging.getLogger("scovo")

# ------------------------------------------------------------------ interruttori (costanti: niente variabili su Render)
OMBRA = True                   # calcola e scrive CONFRONTI_OMBRA; il sito non cambia
MODO_REALE = True             # True = le auto con la stima allargata entrano davvero (candidato + controllo AI)
FASCE_REALI = None             # None = tutte; altrimenti le fasce BV.fascia del valore ammesse nel modo reale,
                               # es. ("10-15k", "15-20k", ">20k"), da decidere sui conteggi "per_fascia" e "precisione"

REGOLA = "allargata-2511a"
ETICHETTA = "stima allargata"
AVVISO = "prezzo stimato con meno confronti"


class Prova(NamedTuple):
    nome: str
    base: str                  # "zona" (le basi della valutazione) o "italia" (tutta Italia, km fino a 400.000)
    livelli: tuple             # (anno ±, km ±) provati in ordine
    n_min: int
    percentile: float
    sconto_extra: float


PROVE = (
    Prova("zona", "zona", BV.LIVELLI, 5, 0.35, 0.03),
    Prova("zona_larga", "zona", ((3, 0.60),), 6, 0.30, 0.05),
    Prova("italia", "italia", ((2, None),), 6, 0.30, 0.05),      # km: fino a KM_CONFRONTI_MAX, prezzo normalizzato
)
KM_CONFRONTI_MAX = 400000      # solo per i CONFRONTI della prova "italia"; le auto del sito restano entro 150.000 km
KM_PESO = 100.0                # peso per distanza = 1 / (1 + km / KM_PESO)
KM_AUTO_PESO = 50000.0         # peso per differenza di km percorsi = 1 / (1 + |Δkm| / KM_AUTO_PESO)
PESO_MIN = 2.5                 # peso totale minimo dei confronti tenuti
N_MIN_FRAGILE = 8              # auto sotto ValoreBase.fragile_valore o oltre fragile_anni: come il valore prudente
N_DEBOLE = 3                   # stime di una sola base con almeno 3 confronti: solo tetti
PERCENTILE_SENZA_DATI = 0.05   # potenza o cambio dell'auto non noti: percentile più basso di 5 punti
PERCENTILE_MIN = 0.20
DISPERSIONE_MAX = 0.35         # (p75 − p25) / mediana dei confronti tenuti
MARGINE_MAX = 0.40             # come regole.StimaRipiego.margine_max
CAMPIONE_PRECISIONE = 10       # 1 auto ogni 10 col valore normale misura la precisione (id % 10 == 0)
LOG_OGNI_S = 600               # una riga CONFRONTI_OMBRA ogni 10 minuti (come DATI_INSUFF_MOTIVI)
ESEMPI_MAX = 10
FASCE = ("<5k", "5-10k", "10-15k", "15-20k", ">20k")

ITALIA_SQL_MARCA = "/*allargata:italia*/"


# ------------------------------------------------------------------ stima

def _vuota(motivo: str = "pochi_confronti") -> dict:
    return {"valore": None, "livello": None, "prova": None, "n": 0, "motivo": motivo, "regola": REGOLA, "tenuti": []}


def _fragile(valore: float, eta: int, vb) -> bool:
    return valore < vb.fragile_valore or eta > vb.fragile_anni


def _netti_deboli(D: list, V: list, t: dict, eta: int, vb, escludi=None) -> list:
    """Stime deboli di UNA sola base (almeno N_DEBOLE confronti ai livelli di oggi), già scontate: solo tetti."""
    out = []
    for base, sconto in ((D, VB.sconto_vetrina), (V, VB.sconto_vendute)):
        x = BV.stima_dettaglio(base, t, escludi, n_min=N_DEBOLE)
        if x:
            out.append(x["valore"] * (1 - sconto(x["valore"], eta, vb)))
    return out


def distanza_km(pa, pb) -> float | None:
    """Km in linea d'aria tra i capoluoghi delle due province (None se una non si conosce)."""
    from ..geo import coords, km
    a, b = coords(pa), coords(pb)
    return km(a, b) if a and b else None


def peso(c: dict, t: dict) -> float:
    """Peso di un confronto: 1 / (1 + distanza / KM_PESO) × 1 / (1 + |differenza di km| / KM_AUTO_PESO). Distanza dal
    capoluogo dell'auto valutata (Milano se manca); confronto senza provincia nota = 300 km (peso 0,25)."""
    d = distanza_km(t.get("province") or "MI", c.get("province"))
    if d is None:
        d = distanza_km("MI", c.get("province"))
    d = 300.0 if d is None else d
    dk = abs(float(c.get("mileage_km") or 0) - float(t.get("mileage_km") or 0))
    return 1.0 / (1.0 + d / KM_PESO) / (1.0 + dk / KM_AUTO_PESO)


def percentile_pesato(coppie: list, q: float) -> float:
    """Percentile q di [(valore, peso)]: i valori in ordine, ognuno al centro del suo peso, interpolazione lineare."""
    v = sorted((float(x), max(0.0, float(w))) for x, w in coppie)
    tot = sum(w for _, w in v)
    if not v or tot <= 0:
        raise ValueError("nessun peso")
    obiettivo, cum, prima = q * tot, 0.0, None
    for x, w in v:
        centro = cum + w / 2
        if centro >= obiettivo:
            if prima is None or centro == prima[1]:
                return x
            return prima[0] + (x - prima[0]) * (obiettivo - prima[1]) / (centro - prima[1])
        prima = (x, centro)
        cum += w
    return v[-1][0]


def _adatta_italia(c: dict, t: dict) -> float:
    """Prezzo del confronto riportato all'anno e ai km dell'auto (stessa correzione di BV._adatta); la parte dei km
    verso l'ALTO (confronto con più km dell'auto) mai oltre il tetto di regole.Correzione (+25%)."""
    from .. import regole as RG
    tetto = 1 + float(RG.regole().correzione.tetto)
    f_km = min(tetto, 1 + BV.SV_10KM * (c["mileage_km"] - t["mileage_km"]) / 10000)
    return float(c["price_eur"]) * (1 + BV.SV_ANNO * (t["year"] - c["year"])) * f_km


def _simile_italia(c: dict, t: dict, anni: int) -> bool:
    """Come BV._simile ma senza finestra sui km: confronti fino a KM_CONFRONTI_MAX, mai dalle province di Roma."""
    if abs(c["year"] - t["year"]) > anni or c["mileage_km"] > KM_CONFRONTI_MAX:
        return False
    if c["power_kw"] and t["power_kw"] and abs(c["power_kw"] - t["power_kw"]) > 0.10 * t["power_kw"]:
        return False
    if c["gearbox"] and t["gearbox"] and c["gearbox"] != t["gearbox"]:
        return False
    return True


def stima(D: list, V: list, t: dict, vb, italia=None, escludi=None, oggi=None, solo=None) -> dict:
    """Stima allargata di `t` (riga normalizzata, VB.norm) dalle basi della zona D (commercianti freschi) e V
    (vendute) e, se serve, dalla base d'Italia (`italia`: {"D": [...], "V": [...]}, oppure una funzione senza argomenti
    che la carica solo quando le prove della zona non bastano; None = prova "italia" saltata).
    `solo`: nomi delle prove da tentare (default tutte, in ordine). Nessuna stima: valore None e `motivo`
    ("pochi_confronti" o "dispersione"). Stessa forma di VB.stima_ripiego, più prova / base / dispersione / peso /
    riferimento (mediana meno lo sconto base: il valore senza le prudenze in più, per l'anti-truffa)."""
    prove = [p for p in PROVE if solo is None or p.nome in ((solo,) if isinstance(solo, str) else tuple(solo))]
    eta = VB.eta_auto(t.get("year"), oggi)
    lid = t.get("id")
    senza_dati = not t.get("power_kw") or not t.get("gearbox")
    it_base = None
    dispersa = False
    for pr in prove:
        if pr.base == "italia":
            if it_base is None:
                it_base = (italia() if callable(italia) else italia) or {}
            bD, bV = it_base.get("D") or [], it_base.get("V") or []
        else:
            bD, bV = D, V
        id_D = {id(c) for c in bD}
        base = [c for c in list(bD) + list(bV) if c is not escludi and (lid is None or c.get("id") != lid)]
        if not base:
            continue
        q = max(PERCENTILE_MIN, pr.percentile - (PERCENTILE_SENZA_DATI if senza_dati else 0.0))
        for anni, km in pr.livelli:
            if km is None:
                coppie = [(c, _adatta_italia(c, t)) for c in base if _simile_italia(c, t, anni)]
            else:
                coppie = [(c, BV._adatta(c, t)) for c in base if BV._simile(c, t, anni, km)]
            coppie = [(c, p) for c, p in coppie if p > 0]
            if len(coppie) < pr.n_min:
                continue
            m = statistics.median(p for _, p in coppie)
            tenuti = [(c, p) for c, p in coppie if BV.TAGLIO[0] * m <= p <= BV.TAGLIO[1] * m]
            if len(tenuti) < pr.n_min:
                continue
            pesi = [peso(c, t) for c, _ in tenuti]
            if sum(pesi) < PESO_MIN:
                continue                            # confronti tutti lontani (distanza o km): non bastano
            prezzi = [p for _, p in tenuti]
            mediana = statistics.median(prezzi)
            # i pesi possono solo ABBASSARE: il minore tra percentile pesato, non pesato e mediana
            grezzo = min(percentile_pesato(list(zip(prezzi, pesi)), q), BV.percentile(prezzi, q), mediana)
            if _fragile(grezzo, eta, vb) and len(tenuti) < max(pr.n_min, N_MIN_FRAGILE):
                continue                            # economica o vecchia: almeno 8 confronti, come il valore prudente
            disp = (BV.percentile(prezzi, 0.75) - BV.percentile(prezzi, 0.25)) / mediana if mediana > 0 else 9.9
            if disp > DISPERSIONE_MAX:
                dispersa = True
                continue                            # confronti troppo diversi tra loro: questa prova non dice il prezzo
            quota_D = sum(1 for c, _ in tenuti if id(c) in id_D) / len(tenuti)
            sb = quota_D * VB.sconto_vetrina(grezzo, eta, vb) + (1 - quota_D) * VB.sconto_vendute(grezzo, eta, vb)
            valore = grezzo * (1 - sb)
            riferimento = mediana * (1 - sb)
            tetto = None
            deboli = _netti_deboli(D, V, t, eta, vb, escludi)
            if pr.base == "italia":
                deboli += _netti_deboli(bD, bV, t, eta, vb, escludi)
            for nx in deboli:
                if nx < valore:
                    valore = tetto = nx
            valore = valore * (1 - pr.sconto_extra)
            return {"valore": valore, "livello": pr.nome, "prova": pr.nome, "base": pr.base, "n": len(tenuti),
                    "quota_D": round(quota_D, 3), "grezzo": grezzo, "mediana": mediana, "percentile": q,
                    "sconto_base": round(sb, 4), "sconto_extra": pr.sconto_extra, "fascia": BV.fascia(grezzo),
                    "tenuti": tenuti, "anni": anni, "km": km, "dispersione": round(disp, 3),
                    "peso": round(sum(pesi), 2), "riferimento": riferimento,
                    "tetto": int(round(tetto)) if tetto is not None else None,
                    "senza_potenza_o_cambio": senza_dati, "regola": REGOLA, "motivo": None}
    return _vuota("dispersione" if dispersa else "pochi_confronti")


# ------------------------------------------------------------------ valutazione

def _con_allargata(out: dict, rr: dict, da: str | None, _id_D: set) -> None:
    """Campi e testi della valutazione con la stima allargata (al posto di VB._con_ripiego, chiamata da VB._valuta)."""
    R = int(round(rr["valore"]))
    n = int(rr["n"])
    out.update(M=R, R=R, R_finale=R, centrale_ridotto=R, riduzione=0.0, M_fonte="allargata",
               fonte_base="allargata:" + rr["prova"], n=n, n_gate=n, n_gemelle=n, livello=len(BV.LIVELLI) + 1,
               campione="stima allargata (meno confronti)", serve_controllo_ai=True, etichetta_stima=ETICHETTA,
               avviso_stima=AVVISO,
               R_da="stima allargata: commercianti e auto vendute insieme, confronti più larghi, valore basso",
               fascia="stima allargata = valore basso dei confronti allargati, meno gli sconti"
                      + (" (con tetto)" if rr.get("tetto") is not None else ""),
               venditori=dict(Counter(c.get("seller_type") or "sconosciuto" for c, _ in rr["tenuti"])),
               confronti=[VB._confronto(c, p, c.get("status") == "scomparso")
                          for c, p in sorted(rr["tenuti"], key=lambda cp: abs(cp[1] - R))[:VB.CONFRONTI_SCHEDA]],
               valore_base={"fonte": "allargata", "R": R, "n": n, "stima_D": out.get("stima_D"),
                            "n_D": out.get("n_D"), "stima_V": out.get("stima_V"), "n_V": out.get("n_V"),
                            "regola": REGOLA, "livello": rr["prova"], "quota_D": rr["quota_D"],
                            "grezzo": int(round(rr["grezzo"])), "mediana": int(round(rr["mediana"])),
                            "sconto_base": rr["sconto_base"], "sconto_extra": rr["sconto_extra"],
                            "tetto": rr.get("tetto")})
    nV = sum(1 for c, _ in rr["tenuti"] if c.get("status") == "scomparso")
    dove = "in tutta Italia (più vicine = più peso)" if rr["base"] == "italia" else "nella zona"
    km_t = f"km fino a {KM_CONFRONTI_MAX}" if rr["km"] is None else f"km ±{int(round(rr['km'] * 100))}%"
    out["ipotesi"].append(f"valore {R} € = stima allargata ({AVVISO}): {n} auto simili {dove} ({n - nV} in vendita "
                          f"da commercianti, {nV} vendute), anno ±{rr['anni']} e {km_t}, "
                          "prezzi riportati all'anno e ai km dell'auto")
    out["ipotesi"].append(f"si prende un valore basso ({int(round(rr['percentile'] * 100))}° percentile, sotto la "
                          f"mediana di {int(round(rr['mediana']))} €): {int(round(rr['grezzo']))} € meno "
                          f"{VB._pct(rr['sconto_base'])} di sconto e {VB._pct(rr['sconto_extra'])} di prudenza in più")
    if rr.get("tetto") is not None:
        out["ipotesi"].append(f"valore limitato dalla stima di una sola base: {rr['tetto']} € già scontati")


def _compatto(rr: dict, esito: str, v: dict | None = None, ombra: bool = True, da: str | None = None) -> dict:
    """Il campo `stima_allargata` della valutazione: solo numeri (nessun confronto, nessun dato del venditore)."""
    out = {"regola": REGOLA, "ombra": bool(ombra), "esito": esito}
    if da:
        out["da"] = da
    if rr.get("valore"):
        out.update(prova=rr["prova"], n=int(rr["n"]), fascia=rr["fascia"], mediana=int(round(rr["mediana"])),
                   dispersione=rr["dispersione"], peso=rr.get("peso"), R=int(round(rr["valore"])))
    if v is not None and v.get("margine") is not None:
        out.update(R=int(v["R"]), R_finale=int(v.get("R_finale") or v["R"]), margine=int(v["margine"]),
                   soglia=int(v.get("soglia") or 0), totale_costi=int(v.get("totale_costi") or 0))
        if v.get("costo_difetti_incluso"):
            out["costo_difetti_incluso"] = True
    return out


def valuta(t: dict, base: dict, soglia: int, rg=None, cfg=None, italia=None) -> tuple[dict | None, dict]:
    """Valutazione di `t` con la stima allargata. Va chiamata SOLO per un'auto che valore_base.valuta lascia
    "dati_insufficienti" con motivo pochi_confronti:* (né valore prudente né stima di ripiego).
    Ritorna (valutazione completa oppure None, campo compatto `stima_allargata`). La valutazione c'è solo se l'auto ha
    un valore e supera gli stessi controlli di valore_base (costi, anti-truffa); categoria "candidato" (margine ≥
    soglia) o "non_conveniente". Esiti del compatto: entra, sotto_soglia, nessuna_stima:<motivo>, margine_oltre_max,
    fuori:<motivo di valore_base>."""
    from .. import regole as RG
    rg = rg or RG.regole()
    tt = VB.norm(t)
    lid = t.get("id")
    D = [c for c in base.get("D") or [] if lid is None or c.get("id") != lid]
    V = [c for c in base.get("V") or [] if lid is None or c.get("id") != lid]
    for k in ("year", "mileage_km", "price_eur"):
        if tt.get(k) in (None, ""):
            return None, _compatto(_vuota(), "nessuna_stima:dati_auto_incompleti")
    rr = stima(D, V, tt, rg.valore_base, italia=italia)
    if not rr.get("valore") or not rr["valore"] > 0:
        return None, _compatto(rr, "nessuna_stima:" + str(rr.get("motivo") or "pochi_confronti"))
    prezzo = float(tt["price_eur"])
    if prezzo < (1 - MARGINE_MAX) * rr["riferimento"]:
        # anti-truffa sul valore di RIFERIMENTO (senza le prudenze in più): un valore più basso non la alleggerisce
        return None, _compatto(rr, "margine_oltre_max")
    stato = {"allargata": rr, "scrivi": _con_allargata}
    v = VB._valuta(t, base, soglia, rg, cfg, stato)
    if stato.get("ripiego") is not rr:
        return None, _compatto(_vuota(), "non_serve")          # l'auto ha già un valore: niente da allargare
    da = (stato.get("senza") or {}).get("motivo_dati")
    if v.get("categoria") == "dati_insufficienti":
        m = str(v.get("motivo_dati") or "altro")
        return None, _compatto(rr, "margine_oltre_max" if m == "ripiego:margine_oltre_max" else "fuori:" + m, da=da)
    v["serve_controllo_ai"] = True
    if v.get("categoria") == "opportunita":
        v["categoria"] = "candidato"                           # mai "opportunita" con una stima allargata
    v["motivi"].append(f"valore da una stima allargata ({AVVISO}): al massimo candidato, da verificare con il "
                       "controllo AI")
    esito = "entra" if v["categoria"] == "candidato" else "sotto_soglia"
    return v, _compatto(rr, esito, v, da=da)


def da_allargare(v) -> bool:
    """La valutazione `v` è un "dati_insufficienti" che la stima allargata può provare: solo pochi_confronti:*, mai le
    auto fermate dall'anti-truffa, non pulite, senza dati, o con una stima di ripiego già scartata."""
    return bool(isinstance(v, dict) and v.get("categoria") == "dati_insufficienti" and v.get("metodo") == VB.METODO
                and str(v.get("motivo_dati") or "").startswith("pochi_confronti:") and not v.get("ripiego_scartato"))


def reale(fascia: str | None) -> bool:
    return bool(MODO_REALE and (FASCE_REALI is None or fascia in FASCE_REALI))


def precisione(t: dict, base: dict, v: dict, rg, italia=None) -> dict | None:
    """Su un'auto CON il valore normale (prudente): la stima allargata di ogni prova, come se il valore non ci fosse,
    divisa per il valore normale ({prova: rapporto}). Sotto 1 = più bassa (prudente). Solo misura."""
    R = v.get("R")
    if not R or v.get("M_fonte") not in ("commercianti", "vendute"):
        return None
    tt = VB.norm(t)
    lid = t.get("id")
    D = [c for c in base.get("D") or [] if lid is None or c.get("id") != lid]
    V = [c for c in base.get("V") or [] if lid is None or c.get("id") != lid]
    out = {}
    for pr in PROVE:
        if pr.base == "italia" and not italia:
            continue
        rr = stima(D, V, tt, rg.valore_base, italia=italia, solo=pr.nome)
        if rr.get("valore"):
            out[pr.nome] = round(rr["valore"] / float(R), 3)
    return out or None


def province_italia() -> list:
    """Province dei confronti della prova "italia": tutte quelle note (geo.PROVINCE) tranne la zona di Roma."""
    from ..geo import PROVINCE
    from ..web import sito_regole as SR
    return sorted(set(PROVINCE) - set(SR.ROMA))


def carica_italia(conn, make: str, model: str, fuel: str) -> dict | None:
    """Le due basi pulite del gruppo in tutta Italia (province_italia). Sola lettura, dentro un punto di salvataggio:
    un errore non rovina la transazione della valutazione. None = non letta."""
    par = {"mk": VB._varianti(make), "md": VB._varianti(model), "fu": fuel,
           "prov": province_italia(), "vg": BV.VENDUTE_GIORNI}
    sql = ITALIA_SQL_MARCA + VB.GRUPPO_SQL
    try:
        if hasattr(conn, "transaction"):
            with conn.transaction():
                righe = conn.execute(sql, par).fetchall()
        else:
            righe = conn.execute(sql, par).fetchall()
        mk, md = str(make or "").lower(), str(model or "").lower()
        rr = [VB.norm(r) for r in righe
              if str(r["make"] or "").lower() == mk and str(r["model"] or "").lower() == md]
        return VB.dividi(rr)
    except Exception as e:
        logging.getLogger("valutazione").warning("ALLARGATA base d'Italia non letta: %s", type(e).__name__)
        return None


def dopo_valutazione(v: dict, t: dict, base: dict, soglia: int, rg=None, cfg=None, italia=None, italia_pronta=None,
                     stats=None) -> dict:
    """Da valutazione.valuta_e_scrivi, subito dopo valore_base.valuta. In ombra la valutazione resta quella (stessa
    categoria, stessi valori) con in più `stima_allargata` (auto pochi_confronti:*) o `allargata_prova` (1 auto ogni
    CAMPIONE_PRECISIONE col valore normale). Nel modo reale la valutazione "candidato" con la stima allargata prende
    il posto del dati_insufficienti. `italia`: funzione che carica la base d'Italia del gruppo (una lettura per gruppo,
    solo se serve); `italia_pronta`: funzione che la ridà solo se già letta (la misura non fa letture in più).
    Mai un errore: su qualunque problema torna `v` com'era."""
    if not (OMBRA or MODO_REALE):
        return v
    try:
        from .. import regole as RG
        rg = rg or RG.regole()
        if da_allargare(v):
            va, comp = valuta(copy.copy(t), base, soglia, rg, cfg, italia=italia)
            if stats is not None:
                stats["allargata:" + comp["esito"].split(":")[0]] += 1
            if va is not None and va.get("categoria") == "candidato" and reale(comp.get("fascia")):
                va["stima_allargata"] = dict(comp, ombra=False)
                if stats is not None:
                    stats["allargata_reale"] += 1
                return va
            v["stima_allargata"] = comp
        elif OMBRA and isinstance(t.get("id"), int) and t["id"] % CAMPIONE_PRECISIONE == 0:
            pr = precisione(t, base, v, rg, italia=italia_pronta() if italia_pronta else None)
            if pr:
                v["allargata_prova"] = pr
    except Exception as e:
        logging.getLogger("valutazione").warning("ALLARGATA non calcolata: %s", type(e).__name__)
    return v


# ------------------------------------------------------------------ sito (web/sito_regole, web/app)

def pv_sito(pv) -> dict | None:
    """Il riassunto `pv` di un'auto dati_insufficienti come sarebbe con la stima allargata (candidato), oppure None se
    la stima non c'è o l'auto non arriverebbe al margine minimo. Usato da sito_regole.r_valutata SOLO sulle copie
    dell'ombra (_r["allargata_ombra"]): nel modo reale la valutazione arriva già "candidato" dal calcolo."""
    sa = pv.get("stima_allargata") if isinstance(pv, dict) else None
    if not isinstance(sa, dict) or sa.get("esito") != "entra" or sa.get("margine") is None or not sa.get("R"):
        return None
    R = int(sa["R"])
    out = dict(pv, categoria="candidato", M=R, R=R, R_finale=int(sa.get("R_finale") or R), margine=int(sa["margine"]),
               n=int(sa.get("n") or 0), n_gemelle=int(sa.get("n") or 0), M_fonte="allargata",
               fonte_base="allargata:" + str(sa.get("prova")), serve_controllo_ai=True, calibrato=1.0,
               totale_costi=int(sa.get("totale_costi") or 0), etichetta_stima=ETICHETTA, avviso_stima=AVVISO)
    if sa.get("costo_difetti_incluso"):
        out["costo_difetti_incluso"] = True
    return out


_ultimo_log = {"t": 0.0}


def copie_per_ombra(items: list, forza: bool = False) -> list | None:
    """PRIMA di sito_regole.elenco_sito: copia dell'elenco (le regole segnano e cambiano le auto) in cui le auto
    dati_insufficienti con una stima allargata buona sono marcate per l'ombra. None = questo giro non si misura
    (ombra spenta o ultima riga scritta da meno di LOG_OGNI_S secondi)."""
    if not OMBRA or (not forza and time.time() - _ultimo_log["t"] < LOG_OGNI_S):
        return None
    _ultimo_log["t"] = time.time()
    out = []
    for it in items:
        c = dict(it)
        c["_r"] = dict(it.get("_r") or {})
        pv = it.get("pv")
        if isinstance(pv, dict) and pv.get("categoria") == "dati_insufficienti" and pv_sito(pv) is not None:
            c["_r"]["allargata_ombra"] = True
        out.append(c)
    return out


def _venditore(it: dict) -> str:
    return "commerciante" if (it.get("_r") or {}).get("seller_type") == "commerciante" else "privato"


def _quantili(rapporti: list) -> dict:
    err = sorted(abs(x - 1) * 100 for x in rapporti)
    return {"n": len(err), "errore_mediano_pct": round(statistics.median(err), 1),
            "errore_p90_pct": round(BV.percentile(err, 0.90), 1),
            "scostamento_mediano_pct": round((statistics.median(rapporti) - 1) * 100, 1),
            "sopra_il_normale_pct": round(100 * sum(1 for x in rapporti if x > 1) / len(err), 1),
            "oltre_5pct_sopra_pct": round(100 * sum(1 for x in rapporti if x > 1.05) / len(err), 1)}


def misura_precisione(items: list) -> dict:
    """Dalle auto col valore normale che portano `allargata_prova`: errore della stima allargata rispetto al valore
    normale, per prova (mediana, 90° percentile, quante volte esce SOPRA il valore normale)."""
    per: dict = {}
    for it in items:
        pv = it.get("pv")
        ap = pv.get("allargata_prova") if isinstance(pv, dict) else None
        if isinstance(ap, dict):
            for prova, x in ap.items():
                try:
                    per.setdefault(str(prova), []).append(float(x))
                except (TypeError, ValueError):
                    continue
    return {k: _quantili(v) for k, v in sorted(per.items()) if v}


def ombra_sito(copie: list, sul_sito, g, elenco=None) -> dict:
    """Rifà l'imbuto del sito sulle `copie` (copie_per_ombra) e conta, tra le auto oggi fuori per dati_insufficienti,
    quante avrebbero un prezzo e quante entrerebbero con TUTTE le regole di oggi (margine minimo, zona, km, anno,
    telefono, tetti dei commercianti, anti-truffa, Roma, Loconte...). `sul_sito`: id delle auto oggi sul sito.
    Scrive `CONFRONTI_OMBRA {json}` (solo conteggi ed esempi con id e numeri: mai telefoni né nomi) e lo ritorna."""
    from ..web import sito_regole as SR
    elenco = elenco or SR.elenco_sito
    sul_sito = set(sul_sito or ())
    insuff = [it for it in copie if isinstance(it.get("pv"), dict)
              and it["pv"].get("categoria") == "dati_insufficienti" and not it.get("prezzo_dopo_valutazione")]
    motivi_oggi = Counter(VB.motivo_dati(it["pv"]) or "?" for it in insuff)
    prima = {it.get("id"): (it["pv"], int(it.get("prezzo") or 0), _venditore(it)) for it in insuff}
    esiti: Counter = Counter()
    con_prezzo, candidate = [], set()
    for it in insuff:
        sa = it["pv"].get("stima_allargata")
        if not isinstance(sa, dict):
            esiti["non_ancora_calcolata" if da_allargare(it["pv"]) else "non_ammessa"] += 1
            continue
        esiti[str(sa.get("esito") or "?").split(":")[0]] += 1
        if sa.get("R"):
            con_prezzo.append(it)
        if (it.get("_r") or {}).get("allargata_ombra"):
            candidate.add(it.get("id"))
    fuori: Counter = Counter()
    entrano = []
    if candidate:
        out, fun = elenco(copie, g=g, scrivi_log=False)
        dentro = {it.get("id") for it in out}
        entrano = [it for it in out if it.get("id") in candidate]
        for passo, ids in (fun.tolti or {}).items():
            n = sum(1 for i in ids if i in candidate)
            if n:
                fuori[passo] += n
        # motivo preciso al passo truffa: chi aspetta solo il controllo AI entrerebbe dopo il controllo (se pulito)
        attesa = 0
        tolti_truffa = set((fun.tolti or {}).get("truffa") or ())
        for it in copie:
            if it.get("id") in candidate and it.get("id") in tolti_truffa:
                try:
                    if SR.esito_truffa(dict(it), g)[1] == "aspetta_controllo_ai":
                        attesa += 1
                except Exception:
                    pass
        if attesa:
            fuori["truffa"] -= attesa
            fuori["aspetta_controllo_ai"] = attesa
            if not fuori["truffa"]:
                del fuori["truffa"]
        uscite = len(sul_sito - dentro)
    else:
        uscite = 0

    def per(auto: list, chiave) -> dict:
        return dict(sorted(Counter(chiave(a) for a in auto).items()))

    def fascia_prezzo(it):
        return BV.fascia(prima[it.get("id")][1] if it.get("id") in prima else it.get("prezzo"))

    esempi = []
    for it in sorted(entrano, key=lambda x: -int((x.get("pv") or {}).get("margine") or 0)):
        pv = it.get("pv") or {}
        sa = pv.get("stima_allargata") or {}
        esempi.append({"id": it.get("id"), "prezzo_stimato": pv.get("R"), "prezzo_annuncio": int(it.get("prezzo") or 0),
                       "margine": pv.get("margine"), "confronti": sa.get("n"), "prova": sa.get("prova"),
                       "venditore": _venditore(it), "etichetta": ETICHETTA})
    if len(esempi) > ESEMPI_MAX:                    # sparsi su tutti i margini, non solo i più alti
        passo = len(esempi) / ESEMPI_MAX
        esempi = [esempi[int(i * passo)] for i in range(ESEMPI_MAX)]
    riga = {
        "regola": REGOLA, "modo": "reale" if MODO_REALE else "ombra", "etichetta": ETICHETTA,
        "totale": len(insuff), "motivi_oggi": dict(motivi_oggi.most_common()),
        "esiti": dict(esiti.most_common()),
        "con_prezzo": len(con_prezzo), "sopra_soglia": len(candidate), "entrerebbero": len(entrano),
        "aspettano_controllo_ai": int(fuori.get("aspetta_controllo_ai") or 0),
        "fuori_per_regola": dict(fuori.most_common()),
        "sito_oggi": len(sul_sito), "uscirebbero_dal_sito": uscite,
        "per_fascia": {"con_prezzo": per(con_prezzo, fascia_prezzo), "entrerebbero": per(entrano, fascia_prezzo)},
        "per_venditore": {"con_prezzo": per(con_prezzo, _venditore), "entrerebbero": per(entrano, _venditore)},
        "per_prova": {"con_prezzo": per(con_prezzo, lambda a: (a["pv"].get("stima_allargata") or {}).get("prova")
                                        or str((prima.get(a.get("id")) or ({},))[0].get("stima_allargata", {})
                                               .get("prova"))),
                      "entrerebbero": per(entrano, lambda a: (a["pv"].get("stima_allargata") or {}).get("prova"))},
        "precisione": misura_precisione(copie),
        "esempi": esempi,
    }
    log.warning("CONFRONTI_OMBRA %s", json.dumps(riga, ensure_ascii=False, default=str))
    return riga


# ------------------------------------------------------------------ rivalutazione una tantum (26/11)
# Dopo il deploy le auto già "dati_insufficienti" restano con la decisione vecchia: la valutazione gira solo sugli
# annunci nuovi o cambiati (CONFRONTI_OMBRA: non_ancora_calcolata). Lavoro una tantum nel sito:
#     BREVO_IMPORT_LISTE=rivaluta_allargata:<etichetta>
# rivaluta con la strada di sempre (valutazione.valuta_e_scrivi, pubblica=True) SOLO le auto attive della zona con
# l'ultima valutazione dati_insufficienti:pochi_confronti:* che non porta ancora `stima_allargata` (una ripresa dopo
# un'interruzione salta quelle già fatte), a blocchi di RIVALUTA_BLOCCO auto con una pausa tra i blocchi e tra i
# gruppi. Le altre auto non si toccano. Alla fine misura la precisione in SOLA LETTURA su un campione di auto col
# valore normale (nessuna scrittura) e scrive UNA riga RIVALUTA_ALLARGATA {json} di soli conteggi.
RIVALUTA_BLOCCO = 200
RIVALUTA_PAUSA_S = 2.0         # tra un blocco e l'altro: il lavoro gira nel processo del sito (contenitore da 2 GB)
RIVALUTA_MINUTI = 90           # oltre: si ferma e solleva (resta "da finire": _lungo lo riprende al prossimo avvio)
PRECISIONE_AUTO = 400          # auto col valore normale misurate (sola lettura)
PRECISIONE_MINUTI = 10

RIVALUTA_SQL = """
/*allargata:rivaluta*/
SELECT l.id, l.make, l.model, l.fuel FROM listings l
JOIN valutazioni_policy vp ON vp.listing_id = l.id AND vp.policy_version = %(pv)s
WHERE l.status = 'attivo' AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL
  AND l.price_eur IS NOT NULL
  AND (l.stage = 'approfondito'
       OR (l.province = ANY(%(prov)s) AND l.last_seen_at > now() - interval '5 days'
           AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s))
  AND vp.categoria = 'dati_insufficienti' AND vp.metodo = 'base_vendute'
  AND COALESCE(vp.dati->>'motivo_dati', '') LIKE 'pochi_confronti:%%'
  AND vp.dati->'stima_allargata' IS NULL AND vp.dati->'ripiego_scartato' IS NULL
ORDER BY lower(l.make), lower(l.model), l.fuel, l.id"""

PRECISIONE_SQL = """
/*allargata:precisione*/
SELECT l.id, l.make, l.model, l.fuel, l.gearbox, l.power_kw, l.year, l.mileage_km, l.price_eur, l.province
FROM listings l
JOIN valutazioni_policy vp ON vp.listing_id = l.id AND vp.policy_version = %(pv)s
WHERE l.status = 'attivo' AND l.province = ANY(%(prov)s) AND l.year IS NOT NULL AND l.mileage_km IS NOT NULL
  AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL AND l.price_eur IS NOT NULL
  AND vp.metodo = 'base_vendute' AND vp.riassunto->>'M_fonte' IN ('commercianti', 'vendute')
ORDER BY md5(l.id::text) LIMIT %(n)s"""


def misura_campione(connetti, n: int = PRECISIONE_AUTO, minuti: float = PRECISIONE_MINUTI, pausa: float = 0.05) -> dict:
    """SOLA LETTURA: su un campione di auto attive col valore normale, la stima allargata di ogni prova come se il
    valore mancasse, divisa per il valore prudente ricalcolato adesso (VB.stima_prudente). Niente si scrive.
    {prova: {n, errore_mediano_pct, errore_p90_pct, ...}} (_quantili)."""
    from .. import regole as RG
    from . import policy_v2 as P
    vb = RG.regole().valore_base
    t0 = time.time()
    with connetti() as conn:
        righe = [dict(r) for r in conn.execute(PRECISIONE_SQL, {"pv": P.POLICY_VERSION, "prov": VB.province_sito(),
                                                                "n": int(n)}).fetchall()]
        conn.commit()
    righe.sort(key=lambda r: (str(r["make"]).lower(), str(r["model"]).lower(), str(r["fuel"]), r["id"]))
    per: dict = {}
    gruppo, basi, italia = None, {}, None
    with connetti() as conn:
        for r in righe:
            if time.time() - t0 > minuti * 60:
                break
            try:
                key = (r["make"], r["model"], r["fuel"])
                zona = VB.zona_di(r.get("province"))
                if key != gruppo:
                    time.sleep(pausa)
                    basi = {zona: VB.carica(conn, *key, zona=zona)}
                    italia = carica_italia(conn, *key)
                    gruppo = key
                    conn.commit()
                elif zona not in basi:
                    basi[zona] = VB.carica(conn, *key, zona=zona)
                    conn.commit()
                tt = VB.norm(r)
                D = [c for c in basi[zona]["D"] if c.get("id") != r["id"]]
                V = [c for c in basi[zona]["V"] if c.get("id") != r["id"]]
                sp = VB.stima_prudente(D, V, tt, vb)
                if not sp.get("valore"):
                    continue
                for pr in PROVE:
                    rr = stima(D, V, tt, vb, italia=italia, solo=pr.nome)
                    if rr.get("valore"):
                        per.setdefault(pr.nome, []).append(rr["valore"] / sp["valore"])
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                gruppo = None
    return {k: _quantili(v) for k, v in sorted(per.items()) if v}


def rivaluta(spec: str = "", connetti=None, ves=None, misura=None, pausa: float | None = None) -> dict:
    """Rivalutazione una tantum delle auto dati_insufficienti:pochi_confronti:* senza `stima_allargata` (vedi sopra).
    Solo conteggi nel log (RIVALUTA_ALLARGATA). Fermata per tempo = eccezione: si riprende al prossimo avvio."""
    from .. import regole as RG
    from ..web import scovo
    from . import policy_v2 as P
    lg = logging.getLogger("valutazione")
    if connetti is None:
        from ..db import connect as connetti
    if ves is None:
        from .valutazione import valuta_e_scrivi as ves
    t0 = time.time()
    st: Counter = Counter()
    if not VB.attivo() or not (OMBRA or MODO_REALE):
        lg.warning("RIVALUTA_ALLARGATA spento")
        return {"fermata": "spento"}
    a = RG.regole().ammissione
    with connetti() as conn:
        righe = conn.execute(RIVALUTA_SQL, {"pv": P.POLICY_VERSION, "prov": VB.province_sito(),
                                            "anno": int(a.anno_min), "km": int(a.km_max)}).fetchall()
        conn.commit()
    blocchi = VB._blocchi([dict(r) for r in righe], RIVALUTA_BLOCCO)
    st["auto"] = len(righe)
    st["blocchi_totali"] = len(blocchi)
    fermata = None
    for n_b, pezzo in enumerate(blocchi):
        if time.time() - t0 > RIVALUTA_MINUTI * 60:
            fermata = "tempo"
            break
        if n_b:
            time.sleep(RIVALUTA_PAUSA_S if pausa is None else pausa)
        try:
            with connetti() as conn:
                es = ves(conn, pezzo, "rivaluta-allargata", pubblica=True, pausa_gruppo=VB.PAUSA_GRUPPO_S)
                conn.commit()
        except Exception as e:
            st["blocchi_errore"] += 1
            lg.warning("RIVALUTA_ALLARGATA blocco di %d auto non riuscito: %s", len(pezzo), type(e).__name__)
            continue
        st["blocchi"] += 1
        for k, n in es.stats.items():
            if k.startswith(("categoria:", "allargata", "scrittura:", "dati_insufficienti:")) \
                    or k in ("errore", "pubblicate"):
                st[k] += n
        for lid in pezzo:
            c = es.salvata.get(lid)
            st["rivalutate"] += 1 if lid in es.salvata else 0
            if c in scovo.CATEGORIE_SITO:
                st["nuove_buone"] += 1
            elif c and c != "dati_insufficienti":
                st["uscite_da_dati_insufficienti_non_buone"] += 1
    out = {"fermata": fermata, "secondi": round(time.time() - t0), "modo": "reale" if MODO_REALE else "ombra",
           "conteggi": dict(st)}
    if not fermata:
        try:
            out["precisione"] = (misura or misura_campione)(connetti)
        except Exception as e:
            out["precisione_errore"] = type(e).__name__
    lg.warning("RIVALUTA_ALLARGATA %s", json.dumps(out, ensure_ascii=False))
    if fermata:
        raise RuntimeError(f"rivalutazione allargata fermata ({fermata}): si riprende al prossimo avvio")
    return out
