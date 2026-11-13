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
  telefono         numero del venditore obbligatorio (supercar europee no: si contattano dall'annuncio); Facebook solo se FB_SITO=1
  no_roma          Roma mai, nemmeno le supercar
  zona             12 province entro 100 km da Milano (fascia richiesta: anche fuori, poi le 100 più vicine,
                   passo tetto_fuori_zona) (provincia ricavata dalla città se manca), zona di Ravenna,
                   supercar europee
  venditore        privati; commercianti: fascia richiesta senza promo (senza tetto) oppure lontani (fuori provincia
                   MI) senza promo/leasing/prezzo esca e valutati col metodo nuovo
  prezzo_intero    niente "solo leasing/finanziamento", niente prezzo a rata (Jev)
  jev              guasti dichiarati, km non credibili, "scarta" con confidenza, danni nelle foto (non incidentate),
                   versioni molto diverse, difetto grave letto dall'AI; supercar solo dopo il controllo AI ok
  margine_sito     (trasformazione) costo del difetto letto dall'AI tolto dal margine: l'UNICA modifica al margine
  truffa           tabella dello studio 6 §4 (avviso "Verificare prima di pagare" nella fascia di dubbio)
  perizia (trasf.) stima media della perizia AI sotto R di oltre il 10%: margine con la stima del perito (più prudente)
                   e avviso "Verificare prima di pagare" (PERIZIA_AI, vedi ai/perizia.py)
  margine_minimo   margine ≥ scovo.soglia(prezzo) (fascia richiesta, annunci degli ultimi 10 giorni: basta arrivarci con
                   uno sconto da chiedere di al massimo 500 € fino a 7.000 €, 700 € sopra: sezione "Da trattare"); supercar: ≥ 10.000 € e ≥ 15% del prezzo; supercar da 300.000 €
                   in su: nessun margine richiesto, prezzo non sopra il mercato (4/11 pom., cliente)
  perizia          (12/11) giudizio "caro" del perito AI: fuori (non le auto "Da trattare"); senza perizia e senza
                   decisione Jev valida: in attesa. PERIZIA_AI=log (default, fase ombra): nessun effetto, nel
                   SITO_FUNNEL "perizia_ombra" con sarebbero_caro / sarebbero_attesa / sarebbero_prudenti
                   (l'auto non passa pulita se l'AI è ferma o il budget è finito)
  tetto_commercianti  al massimo 40 commercianti lontani, i migliori per margine (DOPO zona e margine)
  diesel_extra     (13/11 sera, cliente: "fai entrare più diesel ma solamente macchine per bene, altre 70") al massimo
                   DIESEL_EXTRA (70; 0 = spento) auto DIESEL di commercianti nelle 12 province entro 100 km da Milano
                   (niente Torino, Ravenna, Roma), a qualsiasi prezzo, che oggi restano fuori per
                   commerciante_non_ammesso o perché lontane oltre il tetto dei 40 (calcolato come prima, anche con
                   loro dentro). Promo, solo finanziamento, prezzo esca, Loconte ed esclusi: fuori come oggi.
                   Valutate col metodo nuovo (valutazione_nuova), come i lontani.
                   Solo "per bene": dal 2015, ≤ 150.000 km, margine ≥ soglia, nessun danno/guasto, controllo
                   Claude pulito (non "scarta", danni/truffa/guasto < 0,3, auto_vera ≥ 0,7, dati coerenti ≥ 0,5,
                   foto non da internet, km credibili), numero presente, nessun avviso, versione non "solo nome".
                   Le migliori per margine/soglia, tetto separato N. Coda del controllo Claude: ai/controllo_auto (dz).
                   Nel SITO_FUNNEL "diesel_extra": {n, candidate, entrate}
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
# 9/11: Torino e dintorni sul sito (SITO_TORINO=0 la spegne)
TORINO = frozenset({"TO", "AT", "CN", "BI", "VC"}) if os.environ.get("SITO_TORINO", "1") == "1" else frozenset()

# capoluoghi e città grandi (la provincia si ricava dalla città quando l'annuncio non la dà); il resto da
# data/comuni_60km.json e data/comuni_sesto.json
_CAPOLUOGHI = {
    "milano": "MI", "monza": "MB", "varese": "VA", "como": "CO", "lecco": "LC", "bergamo": "BG", "lodi": "LO",
    "pavia": "PV", "cremona": "CR", "brescia": "BS", "novara": "NO", "piacenza": "PC", "ravenna": "RA",
    "forli": "FC", "cesena": "FC", "rimini": "RN", "ferrara": "FE", "bologna": "BO", "roma": "RM", "latina": "LT",
    "frosinone": "FR", "viterbo": "VT", "rieti": "RI", "torino": "TO", "genova": "GE", "mantova": "MN",
    "sondrio": "SO", "alessandria": "AL", "cuneo": "CN", "moncalieri": "TO", "rivoli": "TO", "collegno": "TO",
    "nichelino": "TO", "settimotorinese": "TO", "grugliasco": "TO", "chieri": "TO", "pinerolo": "TO", "ivrea": "TO",
    "alba": "CN", "bra": "CN", "vercelli": "VC", "biella": "BI", "asti": "AT", "parma": "PR",
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
    # 5/11 notte (cliente: "metti tutti quelli fuori zona, i 100 più vicini"): fascia richiesta anche fuori dalle
    # province della zona (Roma mai), al massimo fascia_fuori_max auto, le più vicine a Milano
    fascia_fuori_max: int = 100
    prezzo_min_su_m_comm: float = 0.75
    sc_margine_min: int = 10000
    sc_margine_pct: float = 0.15
    sc_senza_margine_da: int = 300000  # 4/11 pom. (cliente): super da qui in su senza obbligo di margine, prezzo <= mercato
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
    # 4/11 (cliente): sconto massimo 40% sul mercato; più è scontata più va controllata, e conta la cifra in euro
    # più della percentuale (le auto piccole hanno di solito sconti grandi in %, le fasce alte vanno guardate di più)
    sconto_max: float = 0.40          # oltre il 40% di sconto: fuori (da 3.000 € in su)
    sconto_euro: tuple = ((5000, 10**9), (10000, 4000), (20000, 5000), (35000, 6000), (10**9, 8000))  # (prezzo fino a, sconto € oltre cui serve l'AI pulita)
    ai_pulita: float = 0.30           # sopra la soglia in euro resta solo se l'AI ha deciso e la truffa è sotto 0,30
    # 4/11 sera (cliente: "sotto i 10k benzina con meno di 100k km facciamo fatica: teniamo anche quelle con poco
    # margine se trattando si arriva al minimo, ma lo sconto da chiedere non deve superare 500-700 €"): sezione
    # "Da trattare", solo fascia richiesta. (prezzo fino a, sconto massimo da chiedere)
    tratta_max: tuple = ((7000, 500), (10**9, 700))
    tratta_giorni: int = 10           # 4/11 sera (cliente): "queste auto girano in fretta": solo annunci degli ultimi 10 giorni
    alto_euro: int = 3000             # margine molto alto: oltre 3.000 € E oltre il 100% del prezzo
    alto_pct: float = 1.0
    # 4/11 00:20: correzione misurata prezzo CHIESTO -> prezzo VENDUTO, per le auto il cui mercato M viene dalla
    # mediana dei prezzi chiesti (non dal valore reale, che è già un prezzo di vendita): [(limite_M, nome, fattore)]
    fattori_calibra: tuple = ()
    # 12/11 (cliente: "ChatGPT valuta meglio di noi"): perizia AI da commerciante (ai/perizia.py). Spenta nelle Regole()
    # dei test. PERIZIA_AI: "log" (default) = fase ombra (si conta cosa cambierebbe, il sito non cambia), "1" = applicata
    perizia: bool = False
    perizia_ombra: bool = False
    perizia_scarto: float = 0.10      # stima media del perito sotto R di oltre il 10%: margine prudente + avviso
    perizia_basta_jev: bool = True    # una decisione Jev valida conta come controllo (non serve aspettare la perizia)
    # 13/11 (ramo piu-auto, cliente: "più auto senza cambiare le regole"): tre correzioni di falsi scarti, ognuna con
    # il suo interruttore (default nel codice, acceso):
    #  versione_tecnica (VERSIONE_TECNICA): "versioni molto diverse" dell'AI non toglie l'auto se i confronti hanno la
    #    stessa scheda tecnica (potenza, cilindrata, generazione): la differenza era solo nel nome dell'allestimento
    #    (pricing/versione_tecnica.py, calcolata al caricamento in _r["versione_tecnica"]);
    #  sconto_ai (SCONTO_ALTO_AI): sconto in euro oltre il limite della fascia → entra solo se il controllo Claude
    #    (ai/controllo_auto.py, domanda NON circolare: senza il nostro margine) conferma annuncio vero e coerente, foto
    #    viste, niente truffa, niente perizia "caro"/"da verificare"; sempre con l'avviso "Verificare prima di pagare".
    #    I segnali duri (45%/35%, sconto massimo 40%, testo, numero estero) restano esclusi come prima;
    #  ripiego (RIPIEGO_PRUDENTE): auto "dati insufficienti" con una stima prudente da due fonti indipendenti che
    #    concordano (pricing/ripiego_prudente.py): entra come candidato, con l'avviso e solo dopo il controllo AI.
    versione_tecnica: bool = True
    sconto_ai: bool = True
    sconto_ai_vera: float = 0.7       # auto_vera minima (probabilità che l'annuncio sia vero e coerente)
    ripiego: bool = True
    # 13/11 sera (cliente: "altre 70 macchine diesel, le migliori, solamente macchine per bene"): DIESEL_EXTRA
    diesel_extra: int = 70            # 0 = spento (comportamento di prima)
    diesel_anno_min: int = 2015       # Euro 6
    diesel_km_max: int = 150000
    diesel_pulita: float = 0.30       # danni nelle foto, truffa e guasto letti dall'AI sotto questo valore
    diesel_auto_vera: float = 0.7     # controllo Claude: annuncio vero e coerente almeno a questo valore

    @classmethod
    def da_env(cls) -> "Regole":
        e = os.environ.get
        return cls(km_max=int(e("KM_MASSIMO_SITO", "150000")), anno_min=int(e("ANNO_MINIMO_SITO", "2007")),
                   fb_sito=e("FB_SITO", "0") == "1",
                   jev_conf_scarta=float(e("JEV_CONF_SCARTA", "0.6")),
                   sc_senza_margine_da=int(e("SC_SENZA_MARGINE_DA", "300000")),
                   tratta_giorni=int(e("TRATTA_GIORNI", "10")),
                   fascia_fuori_max=int(e("FASCIA_FUORI_ZONA_MAX", "100")),
                   tratta_max=_fasce_tratta(e("TRATTA_MAX", "7000:500,1000000000:700")),
                   perizia=e("PERIZIA_AI", "log").strip().lower() == "1",
                   perizia_ombra=e("PERIZIA_AI", "log").strip().lower() == "log",
                   versione_tecnica=e("VERSIONE_TECNICA", "1") == "1", sconto_ai=e("SCONTO_ALTO_AI", "1") == "1",
                   ripiego=e("RIPIEGO_PRUDENTE", "1") == "1",
                   diesel_extra=max(0, int(e("DIESEL_EXTRA", "70") or 0)))

    def impronta(self) -> str:
        import dataclasses
        import hashlib
        return hashlib.sha256(json.dumps(dataclasses.asdict(self), sort_keys=True).encode()).hexdigest()[:10]


def _r(it: dict) -> dict:
    """Dati di lavoro preparati dal caricamento (mai mandati al telefono)."""
    return it.get("_r") or {}


def super_senza_margine(it: dict, g: "Regole") -> bool:
    """4/11 pom. (cliente): supercar da sc_senza_margine_da in su: basta un buon prezzo rispetto al mercato."""
    return bool(it.get("supercar")) and int(it.get("prezzo") or 0) >= g.sc_senza_margine_da


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
        rip = _r(it).get("ripiego")
        if not (g.ripiego and isinstance(rip, dict) and rip.get("margine") is not None and rip.get("M")):
            return "dati_insufficienti"
        # 13/11: stima prudente da due fonti indipendenti (pricing/ripiego_prudente.py); candidato, avviso, controllo AI
        it["pv"] = pv = dict(pv, **rip, categoria="candidato", serve_controllo_ai=True, ripiego_prudente=True)
        it["ripiego_prudente"] = True
    if super_senza_margine(it, g) and pv.get("M") is not None and it.get("prezzo"):
        return None
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
    if it.get("supercar"):
        return None             # 4/11 pom.: super in tutta Europa, si contattano dall'annuncio
    if it.get("fonte") == "Facebook Marketplace":
        return None if g.fb_sito else "facebook_spento"
    return None if _r(it).get("tel") else "senza_numero"


def r_no_roma(it, g):
    return "roma" if _r(it).get("provincia") in ROMA else None


def km_da_milano(provincia: str | None) -> float | None:
    from ..geo import coords, km
    c, m = coords(provincia), coords("MI")
    return round(km(m, c)) if c and m else None


def r_zona(it, g):
    p = _r(it).get("provincia")
    if it.get("supercar") or p in PROVINCE_100KM or p in RAVENNA or p in TORINO:
        return None
    if g.fascia_fuori_max > 0 and fascia(it) and p not in ROMA:
        d = km_da_milano(p)
        if d is not None:
            it["fuori_zona_km"] = d                # fascia richiesta fuori zona: resta, poi solo le più vicine
            return None
    return "provincia_" + (p or "sconosciuta")


def tetto_fuori_zona(items: list[dict], g) -> tuple[list[dict], set]:
    fuori = sorted((it for it in items if it.get("fuori_zona_km") is not None),
                   key=lambda it: (it["fuori_zona_km"], -int(margine(it) or 0), it.get("id") or 0))
    via = {it["id"] for it in fuori[g.fascia_fuori_max:]}
    return [it for it in items if it.get("id") not in via], via


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


def e_diesel(it) -> bool:
    return "diesel" in str(it.get("carb") or it.get("fuel") or "").lower()


def diesel_commerciante_zona(it, g: Regole) -> bool:
    """Diesel di commerciante nelle 12 province entro 100 km da Milano (niente Torino, Ravenna, Roma), a qualsiasi
    prezzo. Le condizioni del venditore di oggi restano: niente promo, solo finanziamento, prezzo esca, supercar."""
    r = _r(it)
    if not e_diesel(it) or it.get("supercar") or r.get("seller_type") != "commerciante":
        return False
    if r.get("provincia") not in PROVINCE_100KM or r.get("promo") or r.get("solo_fin"):
        return False
    if not r.get("valutazione_nuova"):              # giudice B1: valutati col metodo nuovo, come i lontani
        return False
    M = (it.get("pv") or {}).get("M")
    return bool(M) and (it.get("prezzo") or 0) >= g.prezzo_min_su_m_comm * float(M)


def fuori_solo_per_diesel(it, g: Regole) -> bool:
    """Candidata al passo venditore: diesel di commerciante in zona che oggi esce con commerciante_non_ammesso
    (tipo_venditore None). I diesel lontani oltre il tetto dei 40 si riprendono in elenco_sito (riprendi_diesel)."""
    return tipo_venditore(it, g) is None and diesel_commerciante_zona(it, g)


def diesel_per_bene(it, g: Regole) -> bool:
    """Condizioni "per bene" che si vedono prima del margine (margine e avvisi di truffa: alla fine, tetto_diesel_extra)."""
    r = _r(it)
    if it.get("incidentata") or it.get("economica") or it.get("supercar") or r.get("danno_dichiarato"):
        return False
    if r.get("difetto") or r.get("scarto_cliente") or r.get("loconte"):
        return False
    if not it.get("anno") or int(it["anno"]) < g.diesel_anno_min:
        return False
    if it.get("km") is None or int(it["km"]) > g.diesel_km_max:
        return False
    if not r.get("tel") or r.get("tel_estero") or r.get("testo_truffa"):
        return False
    if r.get("provincia") not in PROVINCE_100KM:      # Milano e dintorni: nessuna zona nuova
        return False
    j = r.get("jev")
    if not j or r.get("jev_errore") or j.get("decisione") == "scarta":
        return False
    # giudice M2: solo il controllo Claude (domanda non circolare, foto viste), annuncio vero e dati coerenti
    if j.get("fonte") != "claude":
        return False
    if j.get("auto_vera") is None or float(j["auto_vera"]) < g.diesel_auto_vera:
        return False
    if j.get("dati_coerenti") is None or float(j["dati_coerenti"]) < 0.5:
        return False
    if j.get("foto_internet") is None or float(j["foto_internet"]) >= 0.5:
        return False
    if j.get("km_ok") is not None and float(j["km_ok"]) < 0.5:
        return False
    for k in ("danni", "truffa"):                   # foto viste e nessun segno: senza valore non si può confermare
        if j.get(k) is None or float(j[k]) >= g.diesel_pulita:
            return False
    if float(j.get("guasto") or 0) >= g.diesel_pulita:
        return False
    if (r.get("perizia") or {}).get("giudizio") in ("caro", "da verificare"):
        return False
    return True


def r_venditore(it, g):
    t = tipo_venditore(it, g)
    if t is None and g.diesel_extra > 0 and fuori_solo_per_diesel(it, g):
        it["_diesel_fuori"] = True                  # fuori oggi solo perché diesel
        if diesel_per_bene(it, g):
            t = "comm_diesel"
            it["diesel_extra"] = True
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
        if g.versione_tecnica and r.get("versione_tecnica") == "uguale":
            it["versione_solo_nome"] = True    # 13/11: stessa scheda tecnica, diverso solo il nome dell'allestimento
            return None
        return "versioni_molto_diverse"
    return None


def calibra_venduto(it, g) -> int:
    """Euro da togliere alla rivendita: correzione misurata prezzo CHIESTO -> VENDUTO, una sola volta, con la funzione
    unica regole.calibra_vendita (solo se M viene dai prezzi chiesti, mai sul valore reale)."""
    pv = it.get("pv") if isinstance(it.get("pv"), dict) else None
    if not pv or not g.fattori_calibra or not pv.get("M") or it.get("supercar") or pv.get("calibrato") is not None:
        return 0
    from .. import regole as RG
    try:
        pv2 = RG.calibra_vendita(pv, list(g.fattori_calibra))
    except Exception:
        return 0
    R = float(pv.get("R_finale") or pv.get("R") or 0)
    R2 = float(pv2.get("R_finale") or pv2.get("R") or R)
    if RG.regole().calibrazione.alza:      # 12/11 sera: fattore oltre 1 (spento di default), solo misurato sopra 20.000 €
        return int(round(R - R2))
    return int(round(max(0.0, R - R2)))


def t_margine_sito(it, g):
    """Modifiche al margine: correzione misurata chiesto -> venduto (solo mercato da prezzi chiesti) e costo del
    difetto letto dall'AI (tabella ai_difetti)."""
    giu = calibra_venduto(it, g)
    if giu != 0 and it["pv"].get("margine") is not None:
        pv = it["pv"]
        R = int(pv.get("R_finale") or pv.get("R"))
        it["pv"] = dict(pv, R=R - giu, R_finale=R - giu, margine=int(pv["margine"]) - giu, calibrato_venduto=giu, calibrato=True)
    costo = int((_r(it).get("difetto") or {}).get("costo") or 0)
    if isinstance(it.get("pv"), dict) and it["pv"].get("costo_difetti_incluso"):
        costo = 0                          # 4/11: già tolto nella valutazione (policy_v2.costi), non due volte
    if costo > 0 and isinstance(it.get("pv"), dict) and it["pv"].get("margine") is not None:
        it["pv"] = dict(it["pv"], margine=int(it["pv"]["margine"]) - costo, margine_calcolo=int(it["pv"]["margine"]),
                        difetto_costo=costo, difetto=(_r(it).get("difetto") or {}).get("motivo"))
        return "difetto_costo"
    return None


def perizia_prudente(it, g) -> int:
    """Euro da togliere alla rivendita se la stima media del perito è sotto R di oltre perizia_scarto (0 altrimenti).
    Le perizie con dati insufficienti non hanno stima media e non toccano il margine."""
    p = _r(it).get("perizia")
    pv = it.get("pv") if isinstance(it.get("pv"), dict) else None
    if it.get("supercar") or not p or not pv or pv.get("margine") is None or not p.get("medio"):
        return 0
    R = pv.get("R_finale") if pv.get("R_finale") is not None else pv.get("R")
    if not R or float(p["medio"]) >= (1 - g.perizia_scarto) * float(R):
        return 0
    return int(round(float(R) - float(p["medio"])))


def esito_perizia(it, g) -> str | None:
    """'perizia_caro' | 'aspetta_perizia' | None, senza toccare l'auto (serve anche alla fase ombra)."""
    if it.get("supercar"):
        return None                          # le supercar hanno il loro controllo AI (regola jev, sc_ok)
    r = _r(it)
    p = r.get("perizia")
    if p:
        if p.get("giudizio") == "caro" and not it.get("trattare"):
            return "perizia_caro"            # le auto "Da trattare" restano: il prezzo da offrire è già più basso
        return None
    if g.perizia_basta_jev and r.get("jev"):
        return None
    return "aspetta_perizia"


def t_perizia(it, g):
    """12/11 (solo PERIZIA_AI=1): stima media del perito sotto R di oltre perizia_scarto → il margine usa la stima del
    perito (il valore più prudente) e l'auto entra solo con l'avviso "Verificare prima di pagare" (se col margine
    prudente scende sotto il minimo, esce al passo margine_minimo). Giudizio "da verificare": solo l'avviso."""
    if not g.perizia or it.get("supercar"):
        return None
    p = _r(it).get("perizia")
    if not p:
        return None
    esito = None
    if p.get("giudizio") == "da verificare":
        it["perizia_verifica"] = True
        esito = "perizia_da_verificare"
    giu = perizia_prudente(it, g)
    if not giu:
        return esito
    pv = it["pv"]
    R = int(pv.get("R_finale") if pv.get("R_finale") is not None else pv.get("R"))
    it["pv"] = dict(pv, R=R - giu, R_finale=R - giu, margine=int(pv["margine"]) - giu, R_calcolo=R,
                    R_perizia=R - giu, perizia_giu=giu)
    it["perizia_verifica"] = True
    return "perizia_prudente"


def r_perizia(it, g):
    """12/11 (solo PERIZIA_AI=1): giudizio "caro" → fuori. Senza perizia (AI ferma, budget finito, tetto del giorno) e
    senza una decisione Jev valida l'auto NON passa pulita: resta in attesa e il giro della perizia la prende per prima."""
    if not g.perizia:
        return None
    m = esito_perizia(it, g)
    if m == "aspetta_perizia":
        it["_perizia_attesa"] = True
    return m


def ombra_perizia(items: list[dict], g) -> dict:
    """PERIZIA_AI=log: cosa farebbe la perizia sulle auto del sito, senza cambiarle. Le auto che sarebbero in attesa
    si segnano (_perizia_attesa) perché il giro le periziasse per prime."""
    from collections import Counter
    c = Counter()
    for it in items:
        if it.get("supercar"):
            continue
        m = esito_perizia(it, g)
        if m == "perizia_caro":
            c["sarebbero_caro"] += 1
        elif m == "aspetta_perizia":
            c["sarebbero_attesa"] += 1
            it["_perizia_attesa"] = True
        giu = perizia_prudente(it, g)
        if giu:
            c["sarebbero_prudenti"] += 1
            if it.get("trattare") is None and margine(it) is not None and margine(it) - giu < scovo.soglia(int(it.get("prezzo") or 0)):
                c["sarebbero_sotto_minimo"] += 1
        if (_r(it).get("perizia") or {}).get("giudizio") == "da verificare":
            c["sarebbero_da_verificare"] += 1
        if _r(it).get("perizia"):
            c["con_perizia"] += 1
    return {"modo": "log", "auto": len(items), "sarebbero_caro": c["sarebbero_caro"],
            "sarebbero_attesa": c["sarebbero_attesa"], "sarebbero_prudenti": c["sarebbero_prudenti"],
            **{k: v for k, v in c.items() if k not in ("sarebbero_caro", "sarebbero_attesa", "sarebbero_prudenti")}}


def margine_alto(it, g: Regole) -> bool:
    m, p = margine(it), it.get("prezzo") or 0
    return m is not None and m > g.alto_euro and m > g.alto_pct * p


def segnali_duri(it, g: Regole) -> list[str]:
    r, j = _r(it), _r(it).get("jev") or {}
    out = []
    foto_int = float(j.get("foto_internet") or 0) >= g.foto_internet
    M, p = (it.get("pv") or {}).get("M"), it.get("prezzo") or 0
    lim = g.rapporto_duro_basse if p < g.rapporto_basse_sotto else g.rapporto_duro
    danno = it.get("incidentata") or r.get("danno_dichiarato") or float(j.get("guasto") or 0) >= g.jev_guasto
    if M and p < lim * float(M) and not danno:
        out.append("prezzo_troppo_basso")
    elif M and not danno and not it.get("supercar") and p >= g.rapporto_basse_sotto:   # sotto 3.000 € il minimo
        # di 1.500 € impone già sconti forti: lì vale solo il 35%
        if p < (1 - g.sconto_max) * float(M):
            out.append("troppo_bella")
    if r.get("testo_truffa"):
        out.append("testo_pagamento_estero")
    if r.get("tel_estero"):
        out.append("numero_estero")
    # 6/11 (verifica imbuto): "foto da internet" scattava sul 68% delle auto con foto (anche le foto professionali dei
    # concessionari). Per un commerciante non conta; per un privato esce solo insieme a un altro segnale
    # (prezzo troppo basso/troppo bello o AI che vede una truffa probabile).
    com = bool(it.get("commerciante") or it.get("commerciante_top") or r.get("seller_type") == "commerciante")
    if foto_int and not com and (out or float(j.get("truffa") or 0) >= .5):
        out.insert(0, "foto_internet")
    return out


def sconto_da_controllare(it, g: Regole) -> bool:
    """Sconto in euro oltre il limite della fascia di prezzo (supercar e incidentate escluse)."""
    M, p = float((it.get("pv") or {}).get("M") or 0), float(it.get("prezzo") or 0)
    if not M or it.get("supercar") or it.get("incidentata"):
        return False
    lim = next(e for fino, e in g.sconto_euro if p < fino)
    return (M - p) > lim


def sconto_confermato_ai(it, g: Regole) -> bool:
    """13/11 (piu-auto): un'auto con sconto alto entra solo se il controllo Claude ha visto le foto e conferma un
    annuncio vero e coerente, senza segni di truffa, dati coerenti, niente danni o foto da internet, e la perizia (se
    c'è) non dice "caro" o "da verificare". Le decisioni di Jev non bastano (la sua domanda di truffa era circolare)."""
    r = _r(it)
    j = r.get("jev") or {}
    if not g.sconto_ai or j.get("fonte") != "claude":
        return False
    if j.get("danni") is None:                      # foto non viste: non si può confermare
        return False
    if j.get("stesso_prezzo") is False:
        # 13/11 (giudice N5): con sconto alto la decisione vale solo allo stesso prezzo; se il contenuto è uguale il
        # giro del controllo la riporta al prezzo nuovo (ai/controllo_auto.riusabile aggiorna decisione_ai.prezzo)
        return False
    # 13/11 (giudice N1): niente "or 1"/"or 0" su valori che possono valere 0 (truffa 0 = nessun segno, non 1)
    t, av = j.get("truffa"), j.get("auto_vera")
    if t is None or float(t) >= g.ai_pulita or av is None or float(av) < g.sconto_ai_vera:
        return False
    if j.get("dati_coerenti") is None or float(j["dati_coerenti"]) < 0.5:
        return False
    if j.get("foto_internet") is None or float(j["foto_internet"]) >= 0.5 or float(j["danni"]) >= g.jev_danni:
        return False                                # (danni None è già uscito sopra: foto non viste)
    if j.get("decisione") == "scarta":
        return False
    p = r.get("perizia") or {}
    if p.get("giudizio") in ("caro", "da verificare"):
        return False
    return True


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
    if not j and isinstance(it.get("pv"), dict) and it["pv"].get("serve_controllo_ai"):
        return "fuori", "aspetta_controllo_ai"     # 4/11: valore reale senza auto gemelle, prima deve decidere l'AI
    meno = g.truffa_alto_meno if alto else 0.0
    p = float((j or {}).get("truffa") or 0)
    con_numero = bool(r.get("tel"))
    if not con_numero:
        if p >= g.truffa_avviso - meno:
            return "fuori", "truffa_senza_numero"
        return "tieni", ""
    if p >= g.truffa_fuori - meno:
        return "fuori", "truffa_probabile"
    controllo = sconto_da_controllare(it, g)
    if controllo and g.sconto_ai and j and j.get("fonte") == "claude":
        # 13/11 (giudice A1): con il controllo Claude TUTTE le auto con sconto alto passano dalla conferma (truffa sotto
        # ai_pulita, annuncio vero, foto viste...) ed entrano sempre con l'avviso; Jev resta con la regola di prima
        if sconto_confermato_ai(it, g):
            it["sconto_confermato_ai"] = True
            return "avviso", "sconto_alto_confermato_ai"
        return "fuori", "sconto_alto_da_controllare"
    if controllo and (not j or p >= g.ai_pulita):
        return "fuori", "sconto_alto_da_controllare"
    if p >= g.truffa_avviso - meno:
        return "avviso", "truffa_da_verificare"
    return "tieni", ""


def r_truffa(it, g):
    esito, motivo = esito_truffa(it, g)
    if esito == "fuori":
        return motivo
    it["avviso_verifica"] = esito == "avviso"
    return None


def recente(it, giorni: int, ora=None) -> bool:
    """Annuncio online da al massimo `giorni` giorni (prima volta che l'abbiamo visto); senza data: no."""
    from datetime import datetime, timezone
    v = it.get("visto_dal")
    if not v:
        return False
    try:
        d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return ((ora or datetime.now(timezone.utc)) - d).total_seconds() <= giorni * 86400


def _fasce_tratta(v: str) -> tuple:
    """'7000:500,1000000000:700' -> ((7000, 500), (10**9, 700)); l'ultima fascia vale per tutti i prezzi sopra."""
    out = []
    for pezzo in (v or "").split(","):
        if ":" in pezzo:
            a, b = pezzo.split(":", 1)
            out.append((int(a), int(b)))
    out.sort()
    if not out:
        return ((7000, 500), (10**9, 700))
    out[-1] = (10**9, out[-1][1])
    return tuple(out)


def diagnosi_trattabili(items: list[dict], g) -> dict:
    """5/11 (cliente: "dobbiamo far entrare più macchine"): auto della fascia richiesta che arrivano fino al
    margine minimo ma sono sotto: quante per sconto necessario e per età dell'annuncio, per scegliere i limiti."""
    from collections import Counter
    c = Counter()
    for it in items:
        if it.get("supercar") or it.get("economica") or it.get("incidentata") or not fascia(it):
            continue
        m, p = margine(it), int(it.get("prezzo") or 0)
        if m is None or m <= 0 or p <= 0 or m >= scovo.soglia(p):
            continue
        serve = next((sc for sc in range(50, 3001, 50) if m + sc >= scovo.soglia(p - sc)), None)
        sb = "oltre_3000" if serve is None else next(f"fino_{x}" for x in (300, 500, 700, 1000, 1500, 2000, 3000) if serve <= x)
        eta = next((f"{d}g" for d in (3, 7, 10, 14, 20, 30) if recente(it, d)), "oltre_30g" if it.get("visto_dal") else "senza_data")
        c[f"{sb}|{eta}"] += 1
    return dict(sorted(c.items()))


def sconto_trattativa(it, g) -> int | None:
    """Sconto da chiedere (a passi di 50 €) perché un'auto della fascia richiesta arrivi al margine minimo,
    se non supera g.tratta_max; None se non serve o se è troppo."""
    if it.get("supercar") or it.get("economica") or it.get("incidentata") or not fascia(it):
        return None
    m, p = margine(it), int(it.get("prezzo") or 0)
    if m is None or m <= 0 or p <= 0 or not recente(it, g.tratta_giorni):
        return None
    tetto = next(x for fino, x in g.tratta_max if p < fino)
    for sc in range(50, tetto + 1, 50):
        if m + sc >= scovo.soglia(p - sc):
            return sc
    return None


def r_margine_minimo(it, g):
    m, p = margine(it), int(it.get("prezzo") or 0)
    tr = it.get("trattare")
    if tr:                                   # sezione "Da trattare": vale il margine dopo lo sconto da chiedere
        return None if m is not None and m > 0 and m + tr["sconto"] >= scovo.soglia(p - tr["sconto"]) else "sotto_soglia"
    if super_senza_margine(it, g):
        M = (it.get("pv") or {}).get("M")
        return None if M and p <= int(M) else "supercar_sopra_mercato"
    if m is None or m <= 0:
        return "margine_nullo"
    if it.get("supercar"):
        if m < max(g.sc_margine_min, g.sc_margine_pct * p):
            return "supercar_margine_basso"
        return None
    if m < scovo.soglia(p):
        sc = sconto_trattativa(it, g)
        if sc:
            it["trattare"] = {"sconto": sc, "prezzo_obiettivo": p - sc, "margine": m + sc}
            return None
        return "sotto_soglia"
    return None


REGOLE = [("valutata", r_valutata), ("no_loconte", r_no_loconte), ("scarti_cliente", r_scarti_cliente),
          ("anno", r_anno), ("km", r_km), ("telefono", r_telefono), ("no_roma", r_no_roma), ("zona", r_zona),
          ("venditore", r_venditore), ("prezzo_intero", r_prezzo_intero), ("jev", r_jev)]
DOPO_MARGINE = [("truffa", r_truffa), ("margine_minimo", r_margine_minimo), ("perizia", r_perizia)]


def riprendi_diesel(prima: list[dict], via: set, g: Regole) -> set:
    """Diesel lontani tolti dal tetto dei 40 (che resta calcolato come prima): candidati al diesel in più; restano
    (tipo comm_diesel) solo quelli per bene, poi decide tetto_diesel_extra."""
    if g.diesel_extra <= 0 or not via:
        return set()
    out = set()
    for it in prima:
        if it.get("id") in via and it.get("_tipo") == "comm_lontano" and diesel_commerciante_zona(it, g):
            it["_diesel_fuori"] = True
            if diesel_per_bene(it, g):
                it["_tipo"] = "comm_diesel"
                it["diesel_extra"] = True
                out.add(it["id"])
    return out


def tetto_diesel_extra(items: list[dict], g: Regole) -> tuple[list[dict], set]:
    """Diesel in più (DIESEL_EXTRA): dopo tutte le regole restano solo quelle con margine ≥ soglia e senza alcun
    avviso (truffa, perizia, ripiego, sconto alto), le migliori per margine/soglia fino a g.diesel_extra."""
    def ok(it):
        m, p = margine(it), int(it.get("prezzo") or 0)
        return (m is not None and p > 0 and m >= scovo.soglia(p) and not it.get("trattare")
                and not it.get("avviso_verifica") and not it.get("perizia_verifica")
                and not it.get("ripiego_prudente") and not it.get("sconto_confermato_ai")
                and not it.get("versione_solo_nome"))       # giudice M2
    extra = [it for it in items if it.get("_tipo") == "comm_diesel"]
    buone = sorted((it for it in extra if ok(it)),
                   key=lambda it: (-(margine(it) / max(1, scovo.soglia(int(it.get("prezzo") or 0)))), it.get("id") or 0))
    tenute = {it["id"] for it in buone[:max(0, g.diesel_extra)]}
    via = {it["id"] for it in extra if it["id"] not in tenute}
    return [it for it in items if it.get("id") not in via], via


def tetto_commercianti(items: list[dict], g: Regole) -> tuple[list[dict], set]:
    lontani = sorted((it for it in items if it.get("_tipo") == "comm_lontano"),
                     key=lambda it: (-int(margine(it) or 0), it.get("id") or 0))
    via = {it["id"] for it in lontani[g.commercianti_max:]}
    return [it for it in items if it.get("id") not in via], via


@dataclass
class Funnel:
    passi: list = field(default_factory=list)
    perizia_ombra: dict | None = None
    diesel_extra: dict = field(default_factory=dict)   # {n, candidate, entrate}
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
        it.pop("trattare", None)
        it.pop("fuori_zona_km", None)
        it.pop("perizia_verifica", None)
        it.pop("_perizia_attesa", None)
        it.pop("sconto_confermato_ai", None)
        it.pop("versione_solo_nome", None)
        it.pop("ripiego_prudente", None)
        it.pop("diesel_extra", None)
        it.pop("_diesel_fuori", None)
    tutte = items
    for nome, f in REGOLE:
        items = _applica(items, nome, f, g, fun)
    from collections import Counter
    c = Counter(t_margine_sito(it, g) for it in items)
    c.update(t_perizia(it, g) for it in items)
    fun.passo("margine_sito", items, items, {k: v for k, v in c.items() if k})
    for nome, f in DOPO_MARGINE:
        if nome == "margine_minimo" and scrivi_log:
            try:
                log.warning("TRATTA_DIAG %s", json.dumps({"giorni": g.tratta_giorni, "max": g.tratta_max,
                                                         "auto": diagnosi_trattabili(items, g)}))
            except Exception as e:
                log.warning("TRATTA_DIAG non riuscita: %s", str(e)[:200])
        items = _applica(items, nome, f, g, fun)
    prima = items
    items, via = tetto_commercianti(items, g)
    ripresi = riprendi_diesel(prima, via, g)
    if ripresi:                                    # diesel lontani oltre i 40: al tetto separato del diesel in più
        via = via - ripresi
        items = [it for it in prima if it.get("id") not in via]
    mot = {"oltre_%d" % g.commercianti_max: len(via) + len(ripresi)} if via or ripresi else {}
    if ripresi:
        mot["al_diesel_extra"] = len(ripresi)
    fun.passo("tetto_commercianti", prima, items, mot or None)
    fun.tolti["tetto_commercianti"] = sorted(via)
    prima = items
    items, via = tetto_fuori_zona(items, g)
    fun.passo("tetto_fuori_zona", prima, items, {"oltre_%d" % g.fascia_fuori_max: len(via)} if via else None)
    fun.tolti["tetto_fuori_zona"] = sorted(via)
    if g.diesel_extra > 0:
        prima = items
        items, via = tetto_diesel_extra(items, g)
        fun.passo("diesel_extra", prima, items, {"oltre_%d_o_con_avviso" % g.diesel_extra: len(via)} if via else None)
        fun.tolti["diesel_extra"] = sorted(via)
    # controllo finale: nessuna auto sotto il minimo (non deve mai scattare)
    sotto = [it["id"] for it in items if r_margine_minimo(it, g)]
    if sotto:
        log.warning("SITO_SOTTO_SOGLIA %d auto tolte (bug): %s", len(sotto), sotto[:30])
        items = [it for it in items if it["id"] not in set(sotto)]
    ombra = ombra_perizia(items, g) if g.perizia_ombra and not g.perizia else None
    fun.perizia_ombra = ombra
    for it in items:
        if it.get("avviso_verifica") is None:
            it["avviso_verifica"] = False
        if it.get("perizia_verifica"):
            it["avviso_verifica"] = True        # 12/11: stima del perito molto sotto R o perizia "da verificare"
        if it.get("ripiego_prudente") or it.get("sconto_confermato_ai"):
            it["avviso_verifica"] = True        # 13/11: stima di ripiego o sconto alto confermato dall'AI
    fun.diesel_extra = {"n": g.diesel_extra, "candidate": sum(1 for it in tutte if it.get("_diesel_fuori")),
                        "entrate": sum(1 for it in items if it.get("diesel_extra"))}
    if scrivi_log:
        log.warning("SITO_FUNNEL %s", json.dumps({"regole": g.impronta(), "finale": len(items),
                                                  "fascia_finale": sum(1 for it in items if fascia(it)),
                                                  "commercianti_lontani": sum(1 for it in items if it.get("_tipo") == "comm_lontano"),
                                                  "commercianti_fascia": sum(1 for it in items if it.get("_tipo") == "comm_fascia"),
                                                  "avvisi": sum(1 for it in items if it.get("avviso_verifica")),
                                                  "avvisi_perizia": sum(1 for it in items if it.get("perizia_verifica")),
                                                  **({"perizia_ombra": ombra} if ombra else {}),
                                                  "da_trattare": sum(1 for it in items if it.get("trattare")),
                                                  "piu_auto": {"sconto_alto_confermate": sum(1 for it in items if it.get("sconto_confermato_ai")),
                                                               "versione_solo_nome": sum(1 for it in items if it.get("versione_solo_nome")),
                                                               "ripiego_prudente": sum(1 for it in items if it.get("ripiego_prudente"))},
                                                  "diesel_extra": fun.diesel_extra,
                                                  "fascia_fuori_zona": sum(1 for it in items if it.get("fuori_zona_km") is not None),
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
