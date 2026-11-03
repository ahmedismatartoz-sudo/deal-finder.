"""Regole del sito (fase 3 del piano di ricostruzione, 4/11/2026): SOLO le regole del cliente decidono quali auto vanno
sul sito. Ogni regola è una funzione pura su un'auto (nessun database, nessun os.environ dentro le regole): il
caricamento in web/app.py prepara i dati, qui si decide. Ogni passo ha il suo contatore e alla fine c'è UNA riga

    SITO_FUNNEL {json}

con, per ogni passo, quante auto entrano, quante escono, quante della fascia richiesta (policy_v2.benzina_richiesta,
ex BENZ_FUNNEL) e i motivi.

Ordine (il margine minimo si controlla UNA volta sola, alla fine, dopo l'unica modifica ammessa al margine):
  valutata         valutazione con rivendita, mercato e margine (categoria 'dati_insufficienti' fuori)
  no_loconte       Loconte sempre fuori
  scarti_cliente   scelte a mano del cliente (data/scarti_sito.json, analisi manuale)
  anno             dal 2007
  km               km scritti e al massimo 150.000
  telefono         numero del venditore obbligatorio; Facebook solo se FB_SITO=1 (lì si scrive in chat)
  no_roma          Roma mai, nemmeno le supercar
  zona             12 province entro 100 km da Milano (provincia ricavata dalla città se manca), zona di Ravenna,
                   supercar europee
  venditore        privati; commercianti: fascia richiesta senza promo (senza tetto) oppure lontani (fuori provincia
                   MI) senza promo/leasing/prezzo esca e valutati col metodo nuovo
  prezzo_intero    niente "solo leasing/finanziamento", niente prezzo a rata (Jev)
  jev              guasti dichiarati, km non credibili, "scarta" con confidenza, danni nelle foto (non incidentate),
                   versioni molto diverse, difetto grave letto dall'AI; supercar solo dopo il controllo AI ok
  margine_sito     (trasformazione) costo del difetto letto dall'AI tolto dal margine: l'UNICA modifica al margine
  truffa           tabella dello studio 6 §4 (avviso "Verificare prima di pagare" nella fascia di dubbio)
  margine_minimo   margine ≥ scovo.soglia(prezzo); supercar: ≥ 10.000 € e ≥ 15% del prezzo
  tetto_commercianti  al massimo 40 commercianti lontani, i migliori per margine (DOPO zona e margine)
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field

from . import scovo

log = logging.getLogger("scovo")

PROVINCE_100KM = frozenset({"MI", "MB", "VA", "CO", "LC", "BG", "LO", "PV", "CR", "BS", "NO", "PC"})
RAVENNA = frozenset({"RA", "FC", "RN", "FE", "BO"})
ROMA = frozenset({"RM", "LT", "FR", "VT", "RI"})

# capoluoghi e città grandi (la provincia si ricava dalla città quando l'annuncio non la dà); il resto da
# data/comuni_60km.json e data/comuni_sesto.json
_CAPOLUOGHI = {
    "milano": "MI", "monza": "MB", "varese": "VA", "como": "CO", "lecco": "LC", "bergamo": "BG", "lodi": "LO",
    "pavia": "PV", "cremona": "CR", "brescia": "BS", "novara": "NO", "piacenza": "PC", "ravenna": "RA",
    "forli": "FC", "cesena": "FC", "rimini": "RN", "ferrara": "FE", "bologna": "BO", "roma": "RM", "latina": "LT",
    "frosinone": "FR", "viterbo": "VT", "rieti": "RI", "torino": "TO", "genova": "GE", "mantova": "MN",
    "sondrio": "SO", "alessandria": "AL", "vercelli": "VC", "biella": "BI", "asti": "AT", "parma": "PR",
    "reggioemilia": "RE", "modena": "MO", "verona": "VR", "trento": "TN", "fiumicino": "RM", "ostia": "RM",
    "sestosangiovanni": "MI", "cinisellobalsamo": "MI", "legnano": "MI", "rho": "MI", "busto arsizio": "VA",
    "bustoarsizio": "VA", "gallarate": "VA", "saronno": "VA", "vigevano": "PV", "voghera": "PV", "crema": "CR",
}
_COMUNI: dict | None = None


def _norm_citta(c) -> str:
    c = unicodedata.normalize("NFKD", str(c or "")).encode("ascii", "ignore").decode().lower()
    c = re.sub(r"\(.*?\)", "", c.split(",")[0])
    return re.sub(r"[^a-z]", "", c)


def _comuni() -> dict:
    global _COMUNI
    if _COMUNI is None:
        from pathlib import Path
        base = Path(__file__).resolve().parents[2] / "data"
        out = {}
        try:
            for k, v in json.loads((base / "comuni_60km.json").read_text()).items():
                out[_norm_citta(v.get("comune") or k)] = str(v.get("pv") or "").upper()
        except Exception:
            pass
        try:
            for c, p in json.loads((base / "comuni_sesto.json").read_text()):
                out.setdefault(_norm_citta(c), str(p).upper())
        except Exception:
            pass
        for k, v in _CAPOLUOGHI.items():
            out[_norm_citta(k)] = v
        _COMUNI = out
    return _COMUNI


def provincia_di(provincia, citta) -> str | None:
    """Sigla della provincia: quella dell'annuncio, altrimenti dalla città ("Milano" → MI). None se non si sa."""
    p = str(provincia or "").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", p):
        return p
    m = re.search(r"\(([A-Za-z]{2})\)\s*$", str(citta or ""))
    if m:
        return m.group(1).upper()
    return _comuni().get(_norm_citta(citta)) or None


# Testo che chiede di pagare prima di vedere l'auto, spedizione, auto o venditore all'estero (studio 6 §3)
TRUFFA_RE = re.compile(
    r"(?i)(caparra|acconto|anticipo|bonifico|deposito|pagamento|versamento)\W+(?:\w+\W+){0,5}"
    r"(prima\s+(di\s+)?(vedere|visionare|ritir|della\s+consegna|del\s+ritiro)|anticipat\w*|per\s+(bloccar|prenotar|riservar|fermar))"
    r"|western\s*union|money\s*gram|postepay|ricarica\s+(poste|paysafe)|paypal\s+amici|escrow"
    r"|spedizion\w*|spedisc\w*|spedit\w*\s+con|corriere|trasportator\w*|consegna\s+a\s+domicilio"
    r"|(auto|macchina|vettura|veicolo)\s+(si\s+trova\s+|è\s+|e'\s+|attualmente\s+)?(in|all')\s*"
    r"(estero|germania|spagna|francia|inghilterra|regno\s+unito|scozia|irlanda|olanda|belgio|romania|polonia|svizzera)"
    r"|(mi\s+trovo|vivo|lavoro|sono|risiedo)\s+(all'?\s*estero|in\s+(germania|spagna|francia|inghilterra|regno\s+unito|"
    r"scozia|irlanda|olanda|belgio|romania|polonia))"
    r"|militare\s+(in\s+)?(missione|trasferit)|trasferit\w+\s+(all'?\s*estero|per\s+lavoro)")
# numero estero scritto nel testo (prefisso internazionale diverso da +39): si guarda SOLO in memoria, mai salvato
TEL_ESTERO_RE = re.compile(r"(?<![\w+])(?:\+|00)(?!39)([1-9]\d{0,2})[\s.\-/]?\(?\d{2,4}\)?[\s.\-/]?\d{3,4}[\s.\-/]?\d{2,4}(?!\d)")
DANNI_DICHIARATI = {"leggero", "medio", "grave", "alto_rischio"}


def testo_truffa(testo: str | None) -> bool:
    return bool(TRUFFA_RE.search(testo or ""))


def tel_estero(testo: str | None) -> bool:
    return bool(TEL_ESTERO_RE.search(testo or ""))


@dataclass(frozen=True)
class Regole:
    anno_min: int = 2007
    km_max: int = 150000
    fb_sito: bool = False             # FB_SITO: come in produzione oggi (0 = Facebook fuori)
    commercianti_max: int = 40
    prezzo_min_su_m_comm: float = 0.75
    sc_margine_min: int = 10000
    sc_margine_pct: float = 0.15
    jev_conf_scarta: float = 0.6
    jev_guasto: float = 0.7
    jev_rata: float = 0.7
    jev_km_ok: float = 0.3
    jev_danni: float = 0.7
    jev_versione_forte: float = 0.25  # versione_ok sotto questo valore = versioni MOLTO diverse: fuori
    truffa_avviso: float = 0.5
    truffa_fuori: float = 0.85
    truffa_alto_meno: float = 0.1     # margine molto alto: soglie di truffa abbassate di 0,1
    foto_internet: float = 0.7
    rapporto_duro: float = 0.45       # prezzo sotto il 45% del mercato senza danni dichiarati: segnale duro
    rapporto_duro_basse: float = 0.35
    rapporto_basse_sotto: int = 3000
    alto_euro: int = 3000             # margine molto alto: oltre 3.000 € E oltre il 100% del prezzo
    alto_pct: float = 1.0
    # 4/11 00:20: correzione misurata prezzo CHIESTO -> prezzo VENDUTO, per le auto il cui mercato M viene dalla
    # mediana dei prezzi chiesti (non dal valore reale, che è già un prezzo di vendita): [(limite_M, nome, fattore)]
    fattori_calibra: tuple = ()

    @classmethod
    def da_env(cls) -> "Regole":
        e = os.environ.get
        return cls(km_max=int(e("KM_MASSIMO_SITO", "150000")), anno_min=int(e("ANNO_MINIMO_SITO", "2007")),
                   fb_sito=e("FB_SITO", "0") == "1",
                   jev_conf_scarta=float(e("JEV_CONF_SCARTA", "0.6")))

    def impronta(self) -> str:
        import dataclasses
        import hashlib
        return hashlib.sha256(json.dumps(dataclasses.asdict(self), sort_keys=True).encode()).hexdigest()[:10]


def _r(it: dict) -> dict:
    """Dati di lavoro preparati dal caricamento (mai mandati al telefono)."""
    return it.get("_r") or {}


def margine(it: dict):
    return (it.get("pv") or {}).get("margine") if isinstance(it.get("pv"), dict) else None


def fascia(it: dict) -> bool:
    from ..pricing.policy_v2 import benzina_richiesta
    try:
        return bool(benzina_richiesta(it))
    except Exception:
        return False


# ------------------------------------------------------------------ regole: (auto, regole) -> None se resta, motivo se esce
def r_valutata(it, g: Regole):
    pv = it.get("pv")
    if not isinstance(pv, dict):
        return "senza_valutazione"
    if pv.get("categoria") == "dati_insufficienti":
        return "dati_insufficienti"
    if pv.get("margine") is None or pv.get("M") is None or (pv.get("R_finale") is None and pv.get("R") is None):
        return "senza_margine"
    if not it.get("prezzo"):
        return "senza_prezzo"
    return None


def r_no_loconte(it, g):
    return "loconte" if _r(it).get("loconte") else None


def r_scarti_cliente(it, g):
    return "scelta_cliente" if _r(it).get("scarto_cliente") else None


def r_anno(it, g):
    a = it.get("anno")
    return "prima_del_%d" % g.anno_min if a and int(a) < g.anno_min else None


def r_km(it, g):
    if it.get("km") is None:
        return "km_mancanti"
    return "oltre_%d_km" % g.km_max if int(it["km"]) > g.km_max else None


def r_telefono(it, g):
    if it.get("fonte") == "Facebook Marketplace":
        return None if g.fb_sito else "facebook_spento"
    return None if _r(it).get("tel") else "senza_numero"


def r_no_roma(it, g):
    return "roma" if _r(it).get("provincia") in ROMA else None


def r_zona(it, g):
    p = _r(it).get("provincia")
    if it.get("supercar") or p in PROVINCE_100KM or p in RAVENNA:
        return None
    return "provincia_" + (p or "sconosciuta")


def tipo_venditore(it, g: Regole) -> str | None:
    """'privato', 'comm_fascia', 'comm_lontano' oppure None (fuori)."""
    r = _r(it)
    st = r.get("seller_type")
    if st == "privato" and not r.get("segni_commerciante"):
        return "privato"
    if it.get("supercar"):
        return None                                     # supercar solo da privati
    if st != "commerciante":
        return None                                     # privato con segni da rivenditore, o venditore sconosciuto
    if r.get("promo") or r.get("solo_fin"):
        return None
    M = (it.get("pv") or {}).get("M")
    if not M or (it.get("prezzo") or 0) < g.prezzo_min_su_m_comm * float(M):
        return None                                     # prezzo "esca"
    if fascia(it):
        return "comm_fascia"                            # fascia richiesta: commercianti della zona, senza tetto
    if r.get("provincia") and r["provincia"] != "MI" and r.get("valutazione_nuova"):
        return "comm_lontano"
    return None


def r_venditore(it, g):
    t = tipo_venditore(it, g)
    if t is None:
        r = _r(it)
        if r.get("seller_type") == "privato":
            return "privato_con_segni_da_rivenditore"
        if r.get("seller_type") != "commerciante":
            return "venditore_sconosciuto"
        if r.get("promo") or r.get("solo_fin"):
            return "commerciante_promo"
        return "commerciante_non_ammesso"
    it["_tipo"] = t
    if t != "privato":
        it["commerciante"] = True
    return None


def r_prezzo_intero(it, g):
    if _r(it).get("solo_fin"):
        return "solo_leasing_finanziamento"
    j = _r(it).get("jev") or {}
    if float(j.get("rata") or 0) >= g.jev_rata:
        return "prezzo_a_rata"
    return None


def r_jev(it, g):
    r = _r(it)
    d = r.get("difetto") or {}
    if d.get("grave"):
        return "difetto_grave"
    if it.get("supercar") and r.get("sc_ok") is not True:
        return "supercar_senza_controllo_ai" if r.get("sc_ok") is None else "supercar_scartata_ai"
    j = r.get("jev")
    if not j:
        return None
    if float(j.get("guasto") or 0) >= g.jev_guasto:
        return "guasto_dichiarato"
    if j.get("km_ok") is not None and float(j["km_ok"]) <= g.jev_km_ok:
        return "km_non_credibili"
    if j.get("decisione") == "scarta" and float(j.get("confidenza") or 0) >= g.jev_conf_scarta:
        return "scarta"
    if float(j.get("danni") or 0) >= g.jev_danni and not it.get("incidentata"):
        return "danni_nelle_foto"
    if j.get("versione_ok") is not None and float(j["versione_ok"]) < g.jev_versione_forte:
        return "versioni_molto_diverse"
    return None


def calibra_venduto(it, g) -> int:
    """Euro da togliere alla rivendita: se M è una mediana di prezzi CHIESTI, R non supera M × fattore misurato
    chiesto -> venduto (una volta sola). Col valore reale (prezzo di vendita) nessuna correzione."""
    pv = it.get("pv") if isinstance(it.get("pv"), dict) else None
    if not pv or not g.fattori_calibra or not pv.get("M") or str(pv.get("metodo") or "").startswith("valore_reale") \
            or it.get("supercar"):
        return 0
    M = float(pv["M"])
    f = next((x for lim, _, x in g.fattori_calibra if M < lim), 1.0)
    R = float(pv.get("R_finale") or pv.get("R") or 0)
    return int(round(max(0.0, R - M * f))) if f < 1 and R else 0


def t_margine_sito(it, g):
    """Modifiche al margine: correzione misurata chiesto -> venduto (solo mercato da prezzi chiesti) e costo del
    difetto letto dall'AI (tabella ai_difetti)."""
    giu = calibra_venduto(it, g)
    if giu > 0 and it["pv"].get("margine") is not None:
        pv = it["pv"]
        R = int(pv.get("R_finale") or pv.get("R"))
        it["pv"] = dict(pv, R=R - giu, R_finale=R - giu, margine=int(pv["margine"]) - giu, calibrato_venduto=giu)
    costo = int((_r(it).get("difetto") or {}).get("costo") or 0)
    if costo > 0 and isinstance(it.get("pv"), dict) and it["pv"].get("margine") is not None:
        it["pv"] = dict(it["pv"], margine=int(it["pv"]["margine"]) - costo, margine_calcolo=int(it["pv"]["margine"]),
                        difetto_costo=costo, difetto=(_r(it).get("difetto") or {}).get("motivo"))
        return "difetto_costo"
    return None


def margine_alto(it, g: Regole) -> bool:
    m, p = margine(it), it.get("prezzo") or 0
    return m is not None and m > g.alto_euro and m > g.alto_pct * p


def segnali_duri(it, g: Regole) -> list[str]:
    r, j = _r(it), _r(it).get("jev") or {}
    out = []
    if float(j.get("foto_internet") or 0) >= g.foto_internet:
        out.append("foto_internet")
    M, p = (it.get("pv") or {}).get("M"), it.get("prezzo") or 0
    lim = g.rapporto_duro_basse if p < g.rapporto_basse_sotto else g.rapporto_duro
    danno = it.get("incidentata") or r.get("danno_dichiarato") or float(j.get("guasto") or 0) >= g.jev_guasto
    if M and p < lim * float(M) and not danno:
        out.append("prezzo_troppo_basso")
    if r.get("testo_truffa"):
        out.append("testo_pagamento_estero")
    if r.get("tel_estero"):
        out.append("numero_estero")
    return out


def esito_truffa(it, g: Regole) -> tuple[str, str]:
    """('tieni'|'avviso'|'fuori', motivo). Studio 6 §4 con le regole del cliente."""
    r = _r(it)
    duri = segnali_duri(it, g)
    if duri:
        return "fuori", "segnale_duro:" + duri[0]
    alto = margine_alto(it, g)
    j = r.get("jev")
    if r.get("jev_errore") and (alto or r.get("da_verificare")):
        return "fuori", "jev_non_letto"              # decisioni non lette: le auto da controllare restano fuori
    if alto and not j:
        return "fuori", "margine_alto_senza_jev"
    meno = g.truffa_alto_meno if alto else 0.0
    p = float((j or {}).get("truffa") or 0)
    con_numero = bool(r.get("tel"))
    if not con_numero:
        if p >= g.truffa_avviso - meno:
            return "fuori", "truffa_senza_numero"
        return "tieni", ""
    if p >= g.truffa_fuori - meno:
        return "fuori", "truffa_probabile"
    if p >= g.truffa_avviso - meno:
        return "avviso", "truffa_da_verificare"
    return "tieni", ""


def r_truffa(it, g):
    esito, motivo = esito_truffa(it, g)
    if esito == "fuori":
        return motivo
    it["avviso_verifica"] = esito == "avviso"
    return None


def r_margine_minimo(it, g):
    m, p = margine(it), int(it.get("prezzo") or 0)
    if m is None or m <= 0:
        return "margine_nullo"
    if it.get("supercar"):
        if m < max(g.sc_margine_min, g.sc_margine_pct * p):
            return "supercar_margine_basso"
        return None
    return "sotto_soglia" if m < scovo.soglia(p) else None


REGOLE = [("valutata", r_valutata), ("no_loconte", r_no_loconte), ("scarti_cliente", r_scarti_cliente),
          ("anno", r_anno), ("km", r_km), ("telefono", r_telefono), ("no_roma", r_no_roma), ("zona", r_zona),
          ("venditore", r_venditore), ("prezzo_intero", r_prezzo_intero), ("jev", r_jev)]
DOPO_MARGINE = [("truffa", r_truffa), ("margine_minimo", r_margine_minimo)]


def tetto_commercianti(items: list[dict], g: Regole) -> tuple[list[dict], set]:
    lontani = sorted((it for it in items if it.get("_tipo") == "comm_lontano"),
                     key=lambda it: (-int(margine(it) or 0), it.get("id") or 0))
    via = {it["id"] for it in lontani[g.commercianti_max:]}
    return [it for it in items if it.get("id") not in via], via


@dataclass
class Funnel:
    passi: list = field(default_factory=list)
    tolti: dict = field(default_factory=dict)       # passo -> ids tolti

    def passo(self, nome, prima, dopo, motivi=None):
        self.passi.append({"passo": nome, "entrate": len(prima), "uscite": len(prima) - len(dopo),
                           "fascia_entrate": sum(1 for it in prima if fascia(it)),
                           "fascia_uscite": sum(1 for it in prima if fascia(it)) - sum(1 for it in dopo if fascia(it)),
                           **({"motivi": motivi} if motivi else {})})


def _applica(items, nome, f, g, fun: Funnel):
    from collections import Counter
    dentro, motivi, tolti = [], Counter(), []
    for it in items:
        m = f(it, g)
        if m:
            motivi[m] += 1
            tolti.append(it.get("id"))
        else:
            dentro.append(it)
    fun.passo(nome, items, dentro, dict(motivi.most_common(12)))
    fun.tolti[nome] = tolti
    return dentro


def elenco_sito(items: list[dict], g: Regole | None = None, scrivi_log: bool = True,
                caricamento: dict | None = None) -> tuple[list[dict], Funnel]:
    """Tutte le regole del cliente, nell'ordine. Ritorna (auto sul sito, contatori). caricamento: conteggi del
    caricamento (economiche, supercar, decisioni Jev lette) da scrivere nella stessa riga di log."""
    g = g or Regole.da_env()
    fun = Funnel()
    for it in items:
        it.pop("_tipo", None)
        it.pop("avviso_verifica", None)
    for nome, f in REGOLE:
        items = _applica(items, nome, f, g, fun)
    from collections import Counter
    c = Counter(t_margine_sito(it, g) for it in items)
    fun.passo("margine_sito", items, items, {k: v for k, v in c.items() if k})
    for nome, f in DOPO_MARGINE:
        items = _applica(items, nome, f, g, fun)
    prima = items
    items, via = tetto_commercianti(items, g)
    fun.passo("tetto_commercianti", prima, items, {"oltre_%d" % g.commercianti_max: len(via)} if via else None)
    fun.tolti["tetto_commercianti"] = sorted(via)
    # controllo finale: nessuna auto sotto il minimo (non deve mai scattare)
    sotto = [it["id"] for it in items if r_margine_minimo(it, g)]
    if sotto:
        log.warning("SITO_SOTTO_SOGLIA %d auto tolte (bug): %s", len(sotto), sotto[:30])
        items = [it for it in items if it["id"] not in set(sotto)]
    for it in items:
        if it.get("avviso_verifica") is None:
            it["avviso_verifica"] = False
    if scrivi_log:
        log.warning("SITO_FUNNEL %s", json.dumps({"regole": g.impronta(), "finale": len(items),
                                                  "fascia_finale": sum(1 for it in items if fascia(it)),
                                                  "commercianti_lontani": sum(1 for it in items if it.get("_tipo") == "comm_lontano"),
                                                  "commercianti_fascia": sum(1 for it in items if it.get("_tipo") == "comm_fascia"),
                                                  "avvisi": sum(1 for it in items if it.get("avviso_verifica")),
                                                  "caricamento": caricamento or {},
                                                  "passi": fun.passi}, ensure_ascii=False, default=str))
    return items, fun


def righe_giorno(fun: Funnel) -> list[tuple[str, int, int]]:
    """(passo, entrate, uscite) per la tabella sito_funnel_giorno, con i motivi come 'passo.motivo'."""
    out = []
    for p in fun.passi:
        out.append((p["passo"], int(p["entrate"]), int(p["uscite"])))
        for m, n in (p.get("motivi") or {}).items():
            out.append((f"{p['passo']}.{m}"[:120], int(p["entrate"]), int(n)))
        if p.get("fascia_entrate"):
            out.append((f"{p['passo']}.fascia_richiesta", int(p["fascia_entrate"]), int(p["fascia_uscite"])))
    return out
