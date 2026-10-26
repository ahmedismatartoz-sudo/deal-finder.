"""Policy di valutazione delle opportunità — versione "pv2" (16/10/2026).

UNICO punto in cui si decide se un'auto è un'opportunità. Sostituisce, per il sito, la vecchia regola
"mercato × (1 − sconto 10%) − prezzo − 140 € − ricambi" (policy precedente, che resta riconoscibile:
le valutazioni vecchie sono nella tabella `valuations` con il loro engine_version, quelle nuove in
`valutazioni_policy` con policy_version = POLICY_VERSION).

Passi (tutti configurabili con variabili d'ambiente POLICY_*, vedi `PolicyConfig`):

1. Confronti equivalenti: stessa marca, modello e carburante; stessa generazione quando nota;
   stesso cambio e potenza entro ±POTENZA_TOL (motorizzazione); anno ±1 e km ±20.000.
   Solo auto sane (mai annunci di auto incidentate come prova della rivendita), attive e
   ricontrollate di recente, nel mercato di casa.
   Livello 2 (ampliato, ESPLICITO): anno ±2 e km ±35.000, accetta confronti con cambio/potenza
   non indicati; l'affidabilità scende e la qualifica massima diventa "candidato da approfondire".
   Mai mescolare carburanti, cambi o potenze diverse, né generazioni diverse quando sono note.
2. Pulizia: si escludono, con il motivo, l'auto stessa, i doppioni (stessa auto fisica o stessa
   auto su portali diversi), rate/leasing/anticipi/prezzi civetta, prezzi vincolati al
   finanziamento, auto importate, annunci spariti o non ricontrollati, prezzi chiaramente errati
   (simbolici o fuori scala rispetto alla mediana del gruppo). Un prezzo basso ma plausibile resta.
3. Campione: >= 10 confronti = base preferita; 5–9 = provvisoria; < 5 = dati insufficienti.
4. M = mediana, L = 25° percentile dei prezzi richiesti. Percentile con interpolazione lineare tra
   ranghi (Hyndman & Fan tipo 7: è il metodo di default di numpy e PERCENTILE.INC di Excel):
   posizione h = (n − 1)·q sull'elenco ordinato x[0..n-1], valore = x[⌊h⌋] + (h − ⌊h⌋)·(x[⌊h⌋+1] − x[⌊h⌋]).
   Sono prezzi richiesti negli annunci, non prezzi di vendita conclusa.
5. Riduzione prudenziale per fascia di M (tabella FASCE), R = min(M × (1 − riduzione), L).
   Oltre 50.000 € la tabella non si estende: nessuna valutazione automatica.
   Transizione graduale (POLICY_TRANSIZIONE=1, spenta di default): subito SOPRA ogni confine b,
   per M in [b, b + w], la riduzione scende linearmente da quella della fascia inferiore (più severa)
   a quella della fascia superiore: r(M) = r_inf − (r_inf − r_sup)·(M − b)/w. Così R è continua
   e non supera mai il valore della tabella base (r(M) >= r_base nella zona di passaggio).
6. Margine operativo prudente = R − acquisto − ripristino (ricambi + manodopera) − altri costi
   − IVA (regime del margine) − riserva imprevisti. Un costo sconosciuto non vale zero: si usa una
   stima dichiarata (origine "stima"), oppure, se manca una base, la qualifica si blocca.
   NON è un utile netto dopo le imposte.
7. Auto danneggiate: rivendita = valore da sana (confronti sani) meno la perdita per storico del
   danno; danni strutturali, airbag o alto rischio richiedono sempre approfondimento.
8. Categorie: dati_insufficienti | candidato | opportunita | non_conveniente, contro la soglia minima
   già configurata nel software (scovo.soglia), che qui NON viene abbassata.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

POLICY_VERSION = "pv16-2026-10-19"
# pv15 (18/10, cliente: "più auto e rivendite più precise, con l'AI"): entra la stima del modello AI dei prezzi
#   (pricing/ai_prezzi.py, errore misurato per modello d'auto). Con pochi confronti diretti (meno di 2) l'auto si
#   valuta con l'AI se il suo errore per quel modello è ≤ 15% (sempre "candidato", mai "opportunità" piena);
#   con i confronti, mercato = media pesata confronti/AI; se AI e confronti concordano entro l'8% la stima è
#   considerata molto precisa (riduzione 3%); se differiscono di oltre il 30% l'auto resta candidato da verificare.
# pv16 (19/10, 02:10, cliente: "prezzo realistico a cui si vende velocemente, non medio"): rivendita = al massimo il
#   30° percentile dei confronti (riportato sul valore di mercato), tra −3% e −20% del mercato.
# pv14 (17/10, 14:35, cliente: "più utilitarie"): fino a 3.000 € il blocco "prezzo troppo basso" scatta sotto M × 0,45
#   invece di 0,55, solo con almeno 5 confronti (le stime non cambiano).
# pv13 (17/10, 09:55, cliente: "gonfia un po' la rivendita, 2–5% secondo il tipo, massima prudenza"): rivendita più alta
# di più dove la stima è più sicura: stima molto precisa 8%→3% (+5%), precisa 10%→7% (+3%), pochi dati 12%→10% (+2%).
# pv12 (17/10, 09:35, cliente: "rivendita ancora troppo bassa, alzala in modo realistico e controllato"):
# riduzione che dipende da quanto è sicura la stima del mercato: 8% se il modello prezzi è molto preciso
# (errore tipico ≤ 10% su ≥ 10 annunci), 10% se è abbastanza preciso (≤ 14% su ≥ 6 annunci, o mediana su ≥ 10
# confronti diretti), 12% negli altri casi. Più dati = rivendita più vicina al mercato.
# pv11 (17/10, 09:10): prezzo di mercato più preciso con un modello per ogni marca/modello/alimentazione: si stimano
# insieme l'effetto di anno, km e potenza su TUTTI gli annunci comparabili (anche anni e km lontani) e si calcola il
# prezzo per l'anno e i km esatti dell'auto. Più auto valutate, meno errori; il modello si usa solo se è preciso.
# pv10 (17/10, 09:00, cliente: "rivendita troppo pessimista"): riduzione 12% su tutte le fasce e rivendita = M − 12%
# (L, il 25° percentile, si mostra ma non taglia più la rivendita: con la riduzione era una doppia prudenza).
# pv9 (17/10, 09:00, regole del cliente): riduzione 20% sotto i 5.000 €, 17% da 5.000 a 10.000 €, 15% sopra;
# margini minimi 1.500 / 2.200 / 3.200 € e oltre, crescenti col prezzo (web/scovo.py SOGLIE_PV).
# pv8 (17/10, scelta del cliente): sotto i 10.000 € la riduzione è 25% (prima 30% sotto i 5.000 €).
# pv7 (16/10, 23:50, richiesta): margine = rivendita prudente − prezzo, NESSUN costo (né passaggio né pulizia);
# per ora solo auto sane (incidentate, guasti e danni nel testo esclusi dal sito).
# pv6 (16/10, 23:45): la correzione per anno/km può solo ABBASSARE il prezzo di un confronto (un confronto più
# vecchio o con più km non viene mai "gonfiato" verso l'auto valutata); carrozzerie diverse (coupé, cabrio,
# station wagon, monovolume Tourer) non si confrontano.
# pv5 (16/10, 23:30, richiesta): costi SOLO passaggio 90 + pulizia 50 + ricambi (niente trasporto, diagnosi,
# vendita, giacenza, manodopera stimata, riserva); riduzione della fascia = la più severa tra la fascia del
# prezzo di acquisto e quella di M (auto da 5.000 a 10.000 € → almeno 25%); prezzo sotto il 55% di M = dati
# non affidabili (versione diversa o errore), mai sul sito.
# pv4 (16/10, 23:00): senza IVA del margine e senza accantonamento garanzia (richiesta); anno ±4 e km ±70.000 al
# secondo livello restano come ampliamento esplicito.
# pv3 (16/10 sera, su richiesta): confronti più larghi (anno ±3, km ±50.000) ma con i prezzi CORRETTI
# per anno e km verso l'auto valutata; bastano 2 confronti (2–4 = candidato, ≥ 5 = può essere opportunità).
# pv2 (stessa giornata): anno ±1, km ±20.000, minimo 5 confronti. Le righe pv2 restano nel database.

CATEGORIE = {
    "opportunita": "Opportunità stimata",
    "candidato": "Candidato da approfondire",
    "non_conveniente": "Non conveniente",
    "dati_insufficienti": "Dati insufficienti",
}


def _e(name, default, cast=float):
    v = os.environ.get("POLICY_" + name)
    if v is None or v == "":
        return default
    try:
        return cast(v)
    except ValueError:
        return default


# (limite superiore di M escluso, riduzione). L'ultima fascia include 50.000 €.
FASCE_BASE = [(50000, .10)]      # 17/10 ore 9: 12% su tutte le fasce (prima 20% / 17% / 15%)


@dataclass
class PolicyConfig:
    anni_tol: int = 3
    km_tol: int = 50000
    potenza_tol: float = .10          # stessa motorizzazione: potenza entro ±10%
    amplia: bool = True               # livello 2 esplicito
    anni_tol_2: int = 4
    km_tol_2: int = 70000
    freschezza_giorni: int = 10       # confronto valido se visto online / ricontrollato negli ultimi N giorni
    prezzo_min: int = 500             # sotto: prezzo simbolico
    fuori_scala: float = 2.5          # oltre mediana × 2,5 o sotto mediana / 2,5: prezzo chiaramente errato
    n_preferito: int = 5
    n_minimo: int = 2
    correzione: bool = True           # prezzi dei confronti portati all'anno e ai km dell'auto valutata
    svalutazione_anno: float = .08    # per anno di differenza (stimata sul gruppo quando ci sono abbastanza dati)
    svalutazione_10km: float = .012   # per 10.000 km di differenza
    m_massimo: int = 50000
    fasce: list = field(default_factory=lambda: list(FASCE_BASE))
    reg: bool = True                  # modello prezzi per marca/modello (anno, km, potenza)
    reg_min: int = 8
    reg_anni: int = 6
    reg_potenza: float = .25
    reg_errore_max: float = .18       # errore tipico massimo (≈18%) perché il modello sia usato
    reg_tetto_mediana: float = 1.10   # se ci sono anche ≥5 confronti diretti, M non supera la loro mediana +10%
    adattiva: bool = True             # riduzione secondo la precisione della stima
    rid_precisa: float = .03
    rid_buona: float = .07
    usa_L: bool = False               # True: R = min(M ridotto, L) come nella policy del documento
    transizione: bool = False
    transizione_w: int = 1000
    # costi (euro). origine "config" = valore già usato dal software; "stima" = ipotesi prudenziale dichiarata
    passaggio: int = 0               # tolto il 16/10: margine = rivendita − prezzo
    pulizia: int = 0
    trasporto: int = 0
    diagnosi_revisione: int = 0       # diagnosi + tagliando/revisione minima (tolta il 16/10)
    vendita: int = 0                  # annunci e commissioni (tolta il 16/10)
    garanzia_pct: float = 0.0         # tolta il 16/10 (richiesta): i clienti non la accantonano. POLICY_GARANZIA_PCT per rimetterla
    garanzia_min: int = 0
    giacenza_giorni: int = 0
    giacenza_euro_giorno: float = 3.0
    # 25/10 (cliente): costi del commerciante per rivendere (passaggio, tagliando, pulizia, ritocchi, garanzia)
    costo_fisso: int = 600
    costo_fisso_alto: int = 1000
    costo_alto_da: int = 20000
    iva_margine: bool = False         # tolta il 16/10 (richiesta); POLICY_IVA_MARGINE=1 la rimette. Regime del margine (art. 36 DL 41/95), aliquota 22%
    iva_aliquota: float = .22
    manodopera_su_ricambi: float = 0.0  # manodopera esclusa (come prima): solo pezzi
    riserva_pct: float = 0.0          # riserva imprevisti su acquisto + ripristino, auto senza danni dichiarati
    riserva_pct_danni: float = 0.0
    riserva_pct_alto_rischio: float = 0.0
    riserva_min: int = 0
    sospetto_sotto_m: float = .55     # prezzo sotto M × 0,55: M non affidabile (versione diversa o errore)
    # utilitarie economiche (17/10): fino a 3.000 € un prezzo molto sotto il mercato è normale (auto vecchie vendute in
    # fretta); si accetta fino a M × 0,45 purché il mercato venga da almeno 5 confronti
    sospetto_basse: float = .45
    sospetto_basse_max: int = 3000
    sospetto_basse_confronti: int = 5
    prezzo_minimo_reale: float = .20  # sotto il 20% del mercato il prezzo non è vero (rata, anticipo, errore): mai sul sito
    solo_sane: bool = True            # per ora solo auto sane
    perdita_storico_danno: float = .08  # perdita di valore per storico incidente (su R), auto danneggiate
    # 19/10, cliente: "non voglio un prezzo medio di rivendita ma un prezzo realistico a cui si vende velocemente".
    # Rivendita = prezzo che mette l'auto tra le più economiche del suo gruppo (q_veloce: 30% dei confronti costa
    # meno, 70% di più), non la mediana dei prezzi chiesti. Mai più del 3% sotto… e mai oltre il 20% sotto il mercato.
    vendita_veloce: bool = True
    # 25/10 (cliente): rivendita dai prezzi dei COMMERCIANTI (sono loro che rivendono): mediana dei confronti di
    # concessionari meno uno sconto per vendere in fretta. Usata solo se ci sono abbastanza confronti di commercianti.
    rivendita_commercianti: bool = False
    commercianti_min: int = 3
    commercianti_sconto: float = .08
    commercianti_solo_ribasso: bool = True
    # 25/10 (cliente: "metti il punto due"): correzione automatica per modello misurata sulle auto vendute in fretta
    # dai concessionari (esame.correzioni, tabella correzioni_modello). Spenta finché POLICY_CORREZIONE_MODELLO=1.
    correzione_modello: bool = False
    q_veloce: float = .30
    veloce_max: float = .97
    veloce_min: float = .80
    veloce_max_incerto: float = .92   # pochi confronti (<5) o confronti ampliati: errore tipico misurato 14–16%
    taratura: bool = True             # correzione automatica degli errori sistematici del valore di mercato (19/10)
    # stima AI (pv15)
    ai: bool = True
    ai_errore_max: float = .15        # errore tipico massimo del modello AI per quel modello d'auto
    ai_n_min: int = 8                 # annunci dello stesso modello su cui l'errore è stato misurato
    ai_peso: float = .4               # peso dell'AI nella media con i confronti diretti
    ai_accordo: float = .08           # AI e confronti entro l'8%: stima molto precisa
    ai_disaccordo: float = .30        # oltre il 30%: candidato da verificare

    @classmethod
    def from_env(cls) -> "PolicyConfig":
        c = cls()
        for f in c.__dataclass_fields__.values():
            if f.name == "fasce":
                continue
            cur = getattr(c, f.name)
            cast = (lambda v: v.strip() in ("1", "true", "si", "sì")) if isinstance(cur, bool) else type(cur)
            setattr(c, f.name, _e(f.name.upper(), cur, cast))
        raw = os.environ.get("POLICY_FASCE")     # es. "5000:30,10000:25,15000:20,20000:15,30000:12,50000:10"
        if raw:
            try:
                c.fasce = [(int(a), int(b) / 100) for a, b in (x.split(":") for x in raw.split(","))]
                c.m_massimo = c.fasce[-1][0]
            except ValueError:
                pass
        return c


# ---------------------------------------------------------------- taratura automatica (19/10)
# Fattori per gruppo, calcolati a ogni ricalcolo sugli annunci veri (pricing/ricalcolo.py) e caricati qui prima di valutare.
TARATURA: dict = {}
# 19/10: la taratura può solo ABBASSARE la rivendita, mai alzarla. I prezzi degli annunci sono prezzi
# CHIESTI (le auto si vendono sotto): alzare la stima perché gli annunci sono più cari gonfierebbe i margini.
TARATURA_MIN = float(os.environ.get("TARATURA_MIN", ".85"))
TARATURA_MAX = float(os.environ.get("TARATURA_MAX", "1.0"))


def fascia_m(m: float) -> str:
    return next(f for lim, f in ((3000, "<3k"), (6000, "3-6k"), (10000, "6-10k"), (20000, "10-20k"), (10**12, ">20k")) if m < lim)


def nb(n: int) -> str:
    return "<5" if n < 5 else "5-9" if n < 10 else "≥10"


def chiave_taratura(metodo: str, n: int, m: float) -> str:
    return f"{metodo}|{nb(n)}|{fascia_m(m)}"


def fattore_taratura(metodo: str, n: int, m: float) -> float | None:
    if not TARATURA:
        return None
    f = TARATURA.get(chiave_taratura(metodo, n, m)) or TARATURA.get(f"{metodo}|*|{fascia_m(m)}")
    return None if f is None else max(TARATURA_MIN, min(TARATURA_MAX, f))


# ---------------------------------------------------------------- statistica

def percentile(values, q: float) -> float | None:
    """Percentile con interpolazione lineare (Hyndman & Fan tipo 7)."""
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def riduzione(m: float, cfg: PolicyConfig) -> tuple[float | None, str, float | None]:
    """(riduzione applicata, descrizione fascia, riduzione della tabella base). None oltre il massimo."""
    if m is None or m > cfg.m_massimo:
        return None, f"oltre {cfg.m_massimo:,} €: tabella non estesa".replace(",", "."), None
    prev = 0
    for i, (lim, r) in enumerate(cfg.fasce):
        ultimo = i == len(cfg.fasce) - 1
        if m < lim or (ultimo and m <= lim):
            desc = f"{prev:,}–{lim:,} €".replace(",", ".")
            base = r
            if cfg.transizione and i > 0 and m < prev + cfg.transizione_w:
                r_inf = cfg.fasce[i - 1][1]
                r = r_inf - (r_inf - base) * (m - prev) / cfg.transizione_w
                desc += " (transizione graduale)"
            return r, desc, base
        prev = lim
    return None, "fuori tabella", None


def rivendita(m: float, l: float, cfg: PolicyConfig, prezzo: float | None = None) -> dict:
    r, fascia, base = riduzione(m, cfg)
    if r is None:
        return {"R": None, "fascia": fascia, "riduzione": None}
    if prezzo:                                  # vale la fascia più severa tra prezzo d'acquisto e M
        rp, fp, bp = riduzione(min(float(prezzo), cfg.m_massimo), cfg)
        if rp is not None and rp > r:
            r, base, fascia = rp, bp, fp + " (fascia del prezzo d'acquisto)"
    centrale = m * (1 - r)
    if not cfg.usa_L:
        l = 10**9
    return {"R": int(round(min(centrale, l))), "centrale_ridotto": int(round(centrale)), "fascia": fascia,
            "riduzione": round(r, 4), "riduzione_base": base, "R_da": "L" if l < centrale else "M ridotto"}


# ---------------------------------------------------------------- confronti

FLAG_ESCLUSI = {"leasing_o_rata": "rata, anticipo o leasing", "prezzo_civetta": "prezzo civetta o incompleto",
                "importazione": "auto da importare (prezzo estero)", "dati_incoerenti": "dati incoerenti"}
DANNEGGIATE = {"leggero", "medio", "grave", "alto_rischio"}
RE_FIN = re.compile(r"(?i)(prezzo|offerta|promo\w*)[^.\n]{0,40}(con|tramite|previo|vincolat\w*)[^.\n]{0,15}finanziament"
                    r"|solo con finanziament|in caso di finanziament|valid\w* con finanziament")


def _norm_gear(g):
    if not g:
        return None
    g = str(g).lower()
    return "automatico" if g.startswith(("auto", "semi", "sequ", "dsg", "cvt")) else "manuale" if g.startswith("man") else g


def _gen(x):
    vi = x.get("vehicle_identity") or {}
    if isinstance(vi, str):
        import json
        try:
            vi = json.loads(vi)
        except ValueError:
            vi = {}
    g = (vi or {}).get("generation")
    return str(g).strip().lower() if g else None


def _days(d, now):
    if not d:
        return None
    if isinstance(d, str):
        d = datetime.fromisoformat(d.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (now - d).total_seconds() / 86400


def tassi(gruppo: list[dict], cfg: PolicyConfig) -> tuple[float, float, str]:
    """Svalutazione per anno e per 10.000 km: stimata sul gruppo (regressione del log-prezzo) se ci sono almeno
    15 annunci e i valori sono sensati, altrimenti quelli configurati."""
    g = [c for c in gruppo if c.get("year") and c.get("mileage_km") is not None and c.get("price_eur")]
    if len(g) >= 15:
        try:
            import numpy as np
            y = np.log([c["price_eur"] for c in g])
            X = np.column_stack([np.ones(len(g)), [c["year"] for c in g], [c["mileage_km"] / 10000 for c in g]])
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
            ya, ka = 1 - math.exp(-beta[1]), 1 - math.exp(beta[2])
            if .02 <= ya <= .25 and 0 <= ka <= .05:
                return float(ya), float(ka), f"stimata su {len(g)} annunci"
        except Exception:
            pass
    return cfg.svalutazione_anno, cfg.svalutazione_10km, "valori configurati"


def prezzo_corretto(c: dict, t: dict, ya: float, ka: float) -> float:
    """Prezzo del confronto portato all'anno e ai km dell'auto valutata."""
    p = float(c["price_eur"])
    p *= (1 - ya) ** (int(c["year"]) - int(t["year"]))                  # confronto più nuovo → vale di più → scendo
    p *= (1 - ka) ** ((int(t["mileage_km"]) - int(c["mileage_km"])) / 10000)
    return min(p, float(c["price_eur"]))     # prudenza: si abbassa chi è migliore, non si alza mai chi è peggiore


_CARROZZERIE = [("sw", r"sports? ?tourer|station ?wagon|sportswagon|\bsw\b|touring|avant|variant|wagon|kombi|estate|shooting ?brake"),
                ("monovolume", r"active ?tourer|gran ?tourer|\btourer\b"),
                ("grancoupe", r"gran ?coup[eé]"),
                ("cabrio", r"cabrio|cabriolet|roadster|spider|spyder|convertible"),
                ("coupe", r"coup[eé]")]


def carrozzeria(c: dict) -> str | None:
    txt = f"{c.get('version_raw') or ''} {c.get('title') or ''}".lower()
    for nome, rx in _CARROZZERIE:
        if re.search(rx, txt):
            return nome
    return None


def regressione(t: dict, puliti: list[dict], cfg: PolicyConfig) -> dict | None:
    """Modello di prezzo del gruppo (stessa marca/modello/alimentazione, stessa carrozzeria e cambio, potenza simile):
    log(prezzo) = a + b·anno + c·km + (d·log potenza). Robusto: si tolgono i prezzi anomali e si rifà il calcolo.
    Ritorna il prezzo previsto per l'auto valutata e l'errore tipico (in proporzione), oppure None."""
    import numpy as np
    if not (t.get("year") and t.get("mileage_km") is not None):
        return None
    tg, bt, tp = _norm_gear(t.get("gearbox")), carrozzeria(t), t.get("power_kw")
    gt = _gen(t)
    g = []
    for c in puliti:
        if c.get("fuel") != t.get("fuel") or not c.get("year") or c.get("mileage_km") is None or not c.get("price_eur"):
            continue
        if abs(int(c["year"]) - int(t["year"])) > cfg.reg_anni:
            continue
        cg = _norm_gear(c.get("gearbox"))
        if tg and cg and tg != cg:
            continue
        bc = carrozzeria(c)
        if bc != bt and not ({bc, bt} <= {None, "sw"}):
            continue
        if tp and c.get("power_kw") and abs(c["power_kw"] - tp) > tp * cfg.reg_potenza:
            continue
        gc = _gen(c)
        if gt and gc and gt != gc:
            continue
        g.append(c)
    if len(g) < cfg.reg_min:
        return None
    usa_p = bool(tp) and sum(1 for c in g if c.get("power_kw")) >= .8 * len(g) and len({c.get("power_kw") for c in g}) > 1
    if usa_p:
        g = [c for c in g if c.get("power_kw")]
    anni = [int(c["year"]) for c in g]
    kms = [int(c["mileage_km"]) for c in g]
    # niente estrapolazioni: l'auto deve stare dentro i dati (con un piccolo margine)
    if not (min(anni) - 1 <= int(t["year"]) <= max(anni) + 1 and min(kms) - 20000 <= int(t["mileage_km"]) <= max(kms) + 20000):
        return None

    def riga(c):
        r = [1.0, int(c["year"]) - 2015, int(c["mileage_km"]) / 10000]
        if usa_p:
            r.append(math.log(c["power_kw"]))
        return r
    X = np.array([riga(c) for c in g], dtype=float)
    y = np.log([float(c["price_eur"]) for c in g])
    mad = 0.0
    for _ in range(3):
        if len(y) < cfg.reg_min:
            return None
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        res = y - X @ beta
        mad = float(np.median(np.abs(res - np.median(res))) * 1.4826)
        keep = np.abs(res - np.median(res)) <= max(2.5 * mad, .06)
        if keep.all():
            break
        X, y = X[keep], y[keep]
    if beta[1] <= 0 or beta[2] > .005:              # più nuova deve valere di più, più km di meno
        return None
    prezzo = float(math.exp(float(np.array(riga(t)) @ beta)))
    return {"prezzo": prezzo, "n": int(len(y)), "errore_tipico": round(mad, 4),
            "anno_pct": round(1 - math.exp(-beta[1]), 4), "km10k_pct": round(1 - math.exp(beta[2]), 4), "potenza": usa_p}


def _equivalente(t, c, cfg, livello):
    """None se equivalente, altrimenti il motivo dell'esclusione."""
    if c.get("fuel") != t.get("fuel"):
        return "carburante diverso"
    bt, bc = carrozzeria(t), carrozzeria(c)
    if bt != bc and (bt or bc) and not (bt is None and bc in ("sw",)) and not (bc is None and bt in ("sw",)):
        return "carrozzeria diversa"
    gt, gc = _gen(t), _gen(c)
    if gt and gc and gt != gc:
        return "generazione diversa"
    if t.get("year") is None or c.get("year") is None:
        return "anno mancante"
    atol = cfg.anni_tol if livello == 1 else cfg.anni_tol_2
    if abs(int(c["year"]) - int(t["year"])) > atol:
        return f"anno oltre ±{atol}"
    if t.get("mileage_km") is None or c.get("mileage_km") is None:
        return "km mancanti"
    ktol = cfg.km_tol if livello == 1 else cfg.km_tol_2
    if abs(int(c["mileage_km"]) - int(t["mileage_km"])) > ktol:
        return f"km oltre ±{ktol:,}".replace(",", ".")
    tg, cg = _norm_gear(t.get("gearbox")), _norm_gear(c.get("gearbox"))
    if tg and cg and tg != cg:
        return "cambio diverso"
    if livello == 1 and tg and not cg:
        return "cambio non indicato"
    tp, cp = t.get("power_kw"), c.get("power_kw")
    if not tp and os.environ.get("POLICY_MOTORE_SENZA_KW", "1") == "1":
        # 25/10 (cliente: "stesso motore"): se l'annuncio non dice i kW, il motore si riconosce dalla cilindrata
        # scritta (1.2, 2.0…); senza né kW né cilindrata non si può dire che le auto siano uguali
        import re as _re
        cil = _re.compile(r"\b(\d\.\d)\b")
        tc = set(cil.findall(f"{t.get('version_raw') or ''} {t.get('title') or ''}"))
        cc = set(cil.findall(f"{c.get('version_raw') or ''} {c.get('title') or ''}"))
        if not tc:
            return "motore non indicato (né kW né cilindrata)"
        if not cc or not (tc & cc):
            return "motorizzazione diversa (cilindrata)"
    if tp and cp and abs(cp - tp) > tp * cfg.potenza_tol:
        return "motorizzazione diversa (potenza)"
    if livello == 1 and tp and not cp:
        return "potenza non indicata"
    return None


def _stessa_auto(a, b) -> bool:
    """Stessa auto fisica: stesso veicolo riconosciuto, o stessi dati quasi identici (anche su portali diversi)."""
    if a.get("vehicle_id") and a.get("vehicle_id") == b.get("vehicle_id"):
        return True
    if a.get("source") and a.get("source") == b.get("source"):
        return False                     # stesso portale, annunci diversi: auto diverse salvo veicolo riconosciuto
    if a.get("year") != b.get("year") or a.get("mileage_km") is None or b.get("mileage_km") is None:
        return False
    return (abs(a["mileage_km"] - b["mileage_km"]) <= 1500 and a.get("price_eur") and b.get("price_eur")
            and abs(a["price_eur"] - b["price_eur"]) <= max(200, .07 * b["price_eur"])
            and _norm_gear(a.get("gearbox")) == _norm_gear(b.get("gearbox")))


def km_come_anno(x: dict) -> bool:
    """Facebook: spesso nel campo km finisce l'anno (2017 km per un'auto del 2017). È un dato sbagliato, non km bassi."""
    km, anno = x.get("mileage_km"), x.get("year")
    try:
        return km is not None and anno is not None and 1990 <= int(km) <= 2035 and abs(int(km) - int(anno)) <= 3
    except (TypeError, ValueError):
        return False


def pulisci(t: dict, mercato: list[dict], cfg: PolicyConfig, now=None) -> tuple[list[dict], list[dict]]:
    """Esclusioni che non dipendono dalla somiglianza. Ritorna (tenuti, esclusi con motivo)."""
    now = now or datetime.now(timezone.utc)
    tenuti, esclusi = [], []

    def via(c, why):
        esclusi.append({"id": c.get("id"), "prezzo": c.get("price_eur"), "motivo": why})

    for c in mercato:
        if c.get("id") == t.get("id"):
            continue                                   # l'auto stessa: non conta nemmeno come esclusa
        if _stessa_auto(c, t):
            via(c, "stessa auto (doppione dell'auto valutata)")
            continue
        if not c.get("price_eur"):
            via(c, "prezzo mancante")
            continue
        if km_come_anno(c):
            via(c, "km uguali all'anno (dato sbagliato)")
            continue
        fl = [FLAG_ESCLUSI[f] for f in (c.get("price_flags") or []) if f in FLAG_ESCLUSI]
        if fl:
            via(c, fl[0])
            continue
        if c.get("finanziamento"):
            via(c, "prezzo vincolato a finanziamento")
            continue
        if c.get("price_eur") < cfg.prezzo_min:
            via(c, f"prezzo simbolico (< {cfg.prezzo_min} €)")
            continue
        if c.get("stage_reason") in ("doppione", "truffa", "prezzo_finto"):
            via(c, {"doppione": "doppione su un altro portale", "truffa": "annuncio sospetto",
                    "prezzo_finto": "prezzo finto"}[c["stage_reason"]])
            continue
        if c.get("status") != "attivo":
            via(c, "annuncio non più online")
            continue
        giorni = [g for g in (_days(c.get("last_checked_at"), now), _days(c.get("last_seen_at"), now)) if g is not None]
        vis = min(giorni) if giorni else None          # ultima verifica = la più recente tra controllo e lettura
        if vis is None or vis > cfg.freschezza_giorni:
            via(c, f"non ricontrollato negli ultimi {cfg.freschezza_giorni} giorni")
            continue
        if (c.get("damage_class") or "sconosciuto") in DANNEGGIATE or c.get("problem_search"):
            via(c, "auto danneggiata o con problemi (non prova il valore da sana)")
            continue
        dup = next((k for k in tenuti if _stessa_auto(c, k)), None)
        if dup:
            via(c, "doppione (stessa auto già contata)")
            continue
        tenuti.append(c)
    return tenuti, esclusi


def seleziona(t: dict, mercato: list[dict], cfg: PolicyConfig, now=None) -> dict:
    puliti, esclusi = pulisci(t, mercato, cfg, now)
    if cfg.correzione and t.get("year") and t.get("mileage_km") is not None:
        ya, ka, origine = tassi(puliti, cfg)
        puliti = [dict(c, prezzo=prezzo_corretto(c, t, ya, ka) if c.get("year") and c.get("mileage_km") is not None
                       else float(c["price_eur"])) for c in puliti]      # senza anno/km: escluse dopo, non corrette
    else:
        ya = ka = 0.0
        origine = "nessuna correzione"
        puliti = [dict(c, prezzo=float(c["price_eur"])) for c in puliti]
    livelli = [1, 2] if cfg.amplia else [1]
    motivi = {}
    for liv in livelli:            # si amplia SOLO se il livello stretto non arriva al minimo
        validi, scartati = [], []
        for c in puliti:
            why = _equivalente(t, c, cfg, liv)
            if why:
                scartati.append((c, why))
            else:
                validi.append(c)
        # prezzi chiaramente errati: fuori scala rispetto alla mediana del gruppo equivalente
        if len(validi) >= 3:
            med = percentile([c["prezzo"] for c in validi], .5)
            ok = [c for c in validi if med / cfg.fuori_scala <= c["prezzo"] <= med * cfg.fuori_scala]
            for c in validi:
                if c not in ok:
                    scartati.append((c, f"prezzo fuori scala (mediana gruppo {int(med)} €): probabile errore"))
            validi = ok
        if len(validi) >= cfg.n_minimo:
            break
    for c, why in scartati:
        motivi[why] = motivi.get(why, 0) + 1
    reg = regressione(t, puliti, cfg) if cfg.reg else None
    return {"livello": liv, "validi": validi, "esclusi": esclusi, "regressione": reg,
            "correzione": {"anno": round(ya, 4), "km_10000": round(ka, 4), "origine": origine},
            "non_equivalenti": [{"id": c.get("id"), "prezzo": c.get("price_eur"), "motivo": why} for c, why in scartati],
            "motivi_non_equivalenti": motivi}


# ---------------------------------------------------------------- costi

def costi(t: dict, R: int, cfg: PolicyConfig) -> dict:
    """Costi per voce. Ogni voce: importo, origine (annuncio | stima_ricambi | config | stima), nota."""
    danno = t.get("damage_class") or "sconosciuto"
    danneggiata = danno in DANNEGGIATE
    voci, mancanti, ipotesi = {}, [], []
    lo, hi = t.get("parts_cost_low"), t.get("parts_cost_high")
    blocco = None
    if danneggiata:
        if hi is None:
            blocco = "danni dichiarati senza stima dei ricambi: ripristino sconosciuto"
            mancanti.append("costo dei ricambi")
            hi = 0
        voci["ricambi"] = {"euro": int(hi), "origine": "stima_ricambi",
                           "nota": f"ricerca prezzi pezzi {int(lo if lo is not None else hi)}–{int(hi)} €, si usa il valore alto"}
        mo = int(round(hi * cfg.manodopera_su_ricambi, -1))
        if mo:
            voci["manodopera"] = {"euro": mo, "origine": "stima",
                                  "nota": f"manodopera e verniciatura stimate al {int(cfg.manodopera_su_ricambi*100)}% dei ricambi"}
        else:
            ipotesi.append("manodopera esclusa: si contano solo i pezzi")
    else:
        voci["ricambi"] = {"euro": int(hi or 0), "origine": "annuncio" if not hi else "stima_ricambi",
                           "nota": "nessun danno dichiarato" if not hi else "ricambi per guasti dichiarati"}
        if hi and cfg.manodopera_su_ricambi:
            voci["manodopera"] = {"euro": int(round(hi * cfg.manodopera_su_ricambi, -1)), "origine": "stima",
                                  "nota": "manodopera stimata sui ricambi"}
        if danno == "sconosciuto":
            ipotesi.append("condizioni non dichiarate: nessun danno stimato, da guardare dal vivo")
    voci["passaggio"] = {"euro": cfg.passaggio, "origine": "config", "nota": "pratiche e passaggio a carico del commerciante"}
    voci["trasporto"] = {"euro": cfg.trasporto, "origine": "stima", "nota": "ritiro dell'auto"}
    voci["diagnosi_revisione"] = {"euro": cfg.diagnosi_revisione, "origine": "stima", "nota": "diagnosi, tagliando o revisione minima"}
    voci["preparazione"] = {"euro": cfg.pulizia, "origine": "config", "nota": "pulizia e preparazione"}
    voci["vendita"] = {"euro": cfg.vendita, "origine": "stima", "nota": "annunci e commissioni"}
    gar = max(cfg.garanzia_min, int(round(R * cfg.garanzia_pct)))
    voci["garanzia"] = {"euro": gar, "origine": "stima",
                        "nota": f"accantonamento garanzia legale {int(cfg.garanzia_pct*100)}% della rivendita (min {cfg.garanzia_min} €)"}
    cf = cfg.costo_fisso_alto if int(t["price_eur"]) > cfg.costo_alto_da else cfg.costo_fisso
    voci["costi_commerciante"] = {"euro": int(cf), "origine": "config",
                                  "nota": "tagliando 150 € e pulizia 50 €"}
    voci["giacenza"] = {"euro": int(round(cfg.giacenza_giorni * cfg.giacenza_euro_giorno)), "origine": "stima",
                        "nota": f"ipotesi {cfg.giacenza_giorni} giorni × {cfg.giacenza_euro_giorno:g} €/giorno"}
    ripristino = voci["ricambi"]["euro"] + voci.get("manodopera", {}).get("euro", 0)
    prezzo = int(t["price_eur"])
    if cfg.iva_margine:
        base = max(0, R - prezzo - ripristino)
        voci["iva_margine"] = {"euro": int(round(base * cfg.iva_aliquota / (1 + cfg.iva_aliquota))), "origine": "config",
                               "nota": "IVA 22% nel regime del margine su (rivendita − acquisto − ripristino)"}
    alto = danno == "alto_rischio" or bool(t.get("rischio_strutturale"))
    pct = cfg.riserva_pct_alto_rischio if alto else cfg.riserva_pct_danni if danneggiata or hi else cfg.riserva_pct
    riserva = max(cfg.riserva_min, int(round(pct * (prezzo + ripristino), -1)))
    voci = {k: v for k, v in voci.items() if v["euro"] or k == "ricambi"}     # voci a zero non si mostrano
    totale = sum(v["euro"] for v in voci.values())
    return {"voci": voci, "ripristino": ripristino, "totale_costi": totale,
            "riserva": {"euro": riserva, "pct": pct, "nota": f"imprevisti {int(pct*100)}% di acquisto + ripristino (min {cfg.riserva_min} €)"},
            "mancanti": mancanti, "ipotesi": ipotesi, "blocco": blocco, "danneggiata": danneggiata, "alto_rischio": alto}


# ---------------------------------------------------------------- valutazione

def _comp_json(c, now):
    return {"id": c.get("id"), "url": c.get("url"), "fonte": c.get("source"), "prezzo": c.get("price_eur"),
            "prezzo_corretto": int(round(c["prezzo"])) if c.get("prezzo") is not None else None,
            "anno": c.get("year"), "km": c.get("mileage_km"), "cambio": c.get("gearbox"), "kw": c.get("power_kw"),
            "versione": (c.get("version_raw") or "")[:40] or None, "venditore": c.get("seller_type"),
            "pubblicato": str(c.get("first_seen_at"))[:10] if c.get("first_seen_at") else None,
            "verificato": str(max((d for d in (c.get("last_checked_at"), c.get("last_seen_at")) if d), default=""))[:10] or None}


def valuta(t: dict, mercato: list[dict], soglia: int, cfg: PolicyConfig | None = None, now=None) -> dict:
    """Valutazione completa e verificabile di un'auto. `t` e `mercato` sono righe di `listings` (dict)."""
    cfg = cfg or PolicyConfig.from_env()
    now = now or datetime.now(timezone.utc)
    out = {"policy": POLICY_VERSION, "id": t.get("id"), "acquisto": t.get("price_eur"), "soglia": soglia,
           "mancanti": [], "ipotesi": [], "motivi": []}
    for k, nome in (("year", "anno"), ("mileage_km", "km"), ("fuel", "alimentazione"), ("price_eur", "prezzo")):
        if t.get(k) in (None, ""):
            out["mancanti"].append(nome)
    if km_come_anno(t):
        out["mancanti"].append("km (scritto l'anno al posto dei km)")
    if out["mancanti"]:
        out.update(categoria="dati_insufficienti", n=0)
        out["motivi"].append("dati dell'auto incompleti: " + ", ".join(out["mancanti"]))
        return out
    if not t.get("gearbox"):
        out["mancanti"].append("cambio")
    if not t.get("power_kw"):
        out["mancanti"].append("potenza (motorizzazione non verificabile)")
    if set(t.get("price_flags") or []) & set(FLAG_ESCLUSI):
        out["motivi"].append("prezzo dell'annuncio non affidabile (rata, civetta o importazione)")

    sel = seleziona(t, mercato, cfg, now)
    validi = sel["validi"]
    n = len(validi)
    prezzi = [c["prezzo"] for c in validi]           # prezzi corretti per anno e km
    out.update(n=n, livello=sel["livello"], esclusi=sel["esclusi"][:40], n_esclusi=len(sel["esclusi"]),
               non_equivalenti=sel["motivi_non_equivalenti"],
               confronti=[_comp_json(c, now) for c in sorted(validi, key=lambda c: c["prezzo"])][:40],
               correzione=sel["correzione"],
               venditori={s: sum(1 for c in validi if (c.get("seller_type") or "sconosciuto") == s)
                          for s in {c.get("seller_type") or "sconosciuto" for c in validi}})
    out["campione"] = ("base preferita" if n >= cfg.n_preferito else "provvisoria (campione limitato)"
                       if n >= cfg.n_minimo else "insufficiente")
    if sel["livello"] == 2:
        out["ipotesi"].append(f"confronti ampliati (anno ±{cfg.anni_tol_2}, km ±{cfg.km_tol_2:,}, cambio/potenza non sempre indicati): "
                              "affidabilità ridotta".replace(",", "."))
    if n and cfg.correzione:
        out["ipotesi"].append(f"prezzi dei confronti corretti verso {t['year']} e {int(t['mileage_km']):,} km: "
                              f"−{sel['correzione']['anno']*100:.1f}% per anno, −{sel['correzione']['km_10000']*100:.1f}% "
                              f"ogni 10.000 km ({sel['correzione']['origine']})".replace(",", "."))
    if n:
        out["M"] = int(round(percentile(prezzi, .5)))
        out["L"] = int(round(percentile(prezzi, .25)))
    # prezzi a cui VENDONO i commercianti (confronti di concessionari) e quelli dei privati, separati: solo informazione
    # (18/10, prova "prezzi dei commercianti"), non cambiano ancora la rivendita
    p_comm = [c["prezzo"] for c in validi if c.get("seller_type") == "commerciante"]
    p_priv = [c["prezzo"] for c in validi if c.get("seller_type") != "commerciante"]
    if p_comm:
        out["M_commercianti"], out["n_commercianti"] = int(round(percentile(p_comm, .5))), len(p_comm)
    if p_priv:
        out["M_privati"], out["n_privati"] = int(round(percentile(p_priv, .5))), len(p_priv)
    reg = sel.get("regressione")
    if reg and reg["errore_tipico"] <= cfg.reg_errore_max:
        m_reg = reg["prezzo"]
        if n >= cfg.n_preferito and out.get("M"):
            m_reg = min(m_reg, out["M"] * cfg.reg_tetto_mediana)      # il modello non va oltre i confronti diretti
        out["M_confronti"] = out.get("M")
        out["M"] = int(round(m_reg))
        out.setdefault("L", int(round(m_reg * (1 - reg["errore_tipico"]))))
        out["metodo"] = "modello"
        out["modello"] = reg
        out["ipotesi"].append(f"mercato stimato con il modello prezzi su {reg['n']} annunci dello stesso modello "
                              f"(−{reg['anno_pct']*100:.1f}% per anno, −{reg['km10k_pct']*100:.1f}% ogni 10.000 km, "
                              f"errore tipico ±{reg['errore_tipico']*100:.0f}%)")
        n = max(n, reg["n"])
        out["n"] = n
        out["campione"] = ("base preferita" if n >= cfg.n_preferito else "provvisoria (campione limitato)")
    else:
        out["metodo"] = "mediana"
    ai = t.get("ai") if cfg.ai else None
    ai_ok = bool(ai and ai.get("prezzo") and ai.get("errore") is not None and ai["errore"] <= cfg.ai_errore_max
                 and (ai.get("n") or 0) >= cfg.ai_n_min)
    if ai_ok:
        out["ai"] = {"prezzo": int(ai["prezzo"]), "errore": round(float(ai["errore"]), 3), "n": int(ai.get("n") or 0)}
        if n >= cfg.n_minimo and out.get("M"):
            diff = ai["prezzo"] / out["M"] - 1
            out["ai"]["differenza"] = round(diff, 3)
            out["M_senza_ai"] = out["M"]
            # 21/10 (cliente: rivendita prudente, mai alzata): l'AI può solo abbassare il mercato dei confronti
            out["M"] = min(out["M"], int(round(out["M"] * (1 - cfg.ai_peso) + ai["prezzo"] * cfg.ai_peso)))
            out["L"] = min(out.get("L") or out["M"], out["M"])
            out["ai"]["concorda"] = abs(diff) <= cfg.ai_accordo
            out["ai"]["disaccordo"] = abs(diff) > cfg.ai_disaccordo
            out["ipotesi"].append(f"mercato = confronti ({out['M_senza_ai']} €) e modello AI ({int(ai['prezzo'])} €, errore "
                                  f"tipico ±{ai['errore']*100:.0f}% su {int(ai.get('n') or 0)} annunci di questo modello)")
        else:
            out["M"] = int(ai["prezzo"])
            out["L"] = int(round(ai["prezzo"] * (1 - ai["errore"])))
            out["metodo"] = "ai"
            out["solo_ai"] = True
            n = max(n, cfg.n_minimo)
            out["ipotesi"].append(f"pochi confronti diretti: mercato stimato dal modello AI dei prezzi ({int(ai['prezzo'])} €, "
                                  f"errore tipico ±{ai['errore']*100:.0f}% misurato su {int(ai.get('n') or 0)} annunci di questo modello)")
    if out.get("M"):
        out["M_grezzo"] = out["M"]                 # valore prima della taratura (serve a misurare l'errore)
        f = fattore_taratura(out.get("metodo") or "mediana", n, out["M"]) if cfg.taratura else None
        if f and abs(f - 1) >= .01:
            out["M"] = int(round(out["M"] * f))
            if out.get("L"):
                out["L"] = min(out["L"], out["M"])
            out["taratura"] = round(f, 3)
            out["ipotesi"].append(f"valore di mercato tarato {'−' if f < 1 else '+'}{abs(f - 1) * 100:.0f}%: in questo gruppo "
                                  "di auto il calcolo sbagliava in modo sistematico rispetto ai prezzi veri degli annunci")
    if n < cfg.n_minimo or not out.get("M"):
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(f"solo {n} confronti validi (servono almeno {cfg.n_minimo})")
        return out

    riv = rivendita(out["M"], out["L"], cfg, t.get("price_eur"))
    if cfg.adattiva and riv.get("riduzione") is not None:
        reg = out.get("modello") or {}
        err = reg.get("errore_tipico")
        if out.get("metodo") == "modello" and err is not None and err <= .10 and n >= 10:
            r_new, perche = cfg.rid_precisa, f"stima molto precisa (modello su {n} annunci, errore ±{err*100:.0f}%)"
        elif (out.get("ai") or {}).get("concorda") and n >= cfg.n_preferito:
            r_new, perche = cfg.rid_precisa, f"stima molto precisa (confronti e modello AI concordano, {n} annunci)"
        elif (out.get("metodo") == "modello" and err is not None and err <= .14 and n >= 6) or \
                (out.get("metodo") == "mediana" and n >= 10) or \
                ((out.get("ai") or {}).get("concorda") and n >= cfg.n_minimo):
            r_new, perche = cfg.rid_buona, f"stima precisa ({n} annunci)"
        else:
            r_new, perche = None, None
        if r_new is not None and r_new < riv["riduzione"]:
            centrale = out["M"] * (1 - r_new)
            riv.update(R=int(round(centrale)), centrale_ridotto=int(round(centrale)), riduzione=r_new,
                       fascia=riv["fascia"] + f" · ridotta a {int(r_new*100)}%: {perche}")
    if cfg.vendita_veloce and riv.get("R") is not None:
        if len(prezzi) >= 2:
            rapp = percentile(prezzi, cfg.q_veloce) / max(percentile(prezzi, .5), 1)
            come = f"il {int(cfg.q_veloce*100)}% dei {len(prezzi)} confronti costa meno, il {100-int(cfg.q_veloce*100)}% di più"
        else:
            err = (out.get("modello") or {}).get("errore_tipico") or .12
            rapp = 1 - .78 * err
            come = f"stimato dall'errore del modello (±{err*100:.0f}%)"
        incerto = n < 5 or sel["livello"] == 2
        rapp = max(cfg.veloce_min, min(cfg.veloce_max_incerto if incerto else cfg.veloce_max, rapp))
        if incerto:
            come += "; stima incerta (pochi confronti o confronti ampliati): almeno −8%"
        v = int(round(out["M"] * rapp))
        out["V"] = v
        if v < riv["R"]:
            riv.update(R=v, R_da="vendita veloce",
                       fascia=riv["fascia"] + f" · prezzo di vendita veloce −{(1-rapp)*100:.0f}%")
            out["ipotesi"].append(f"rivendita = prezzo per vendere in fretta ({v} €, −{(1-rapp)*100:.0f}% sul mercato): {come}")
    if cfg.rivendita_commercianti and riv.get("R") is not None:
        pc = [c["prezzo"] for c in validi if c.get("seller_type") == "commerciante"]
        if len(pc) >= cfg.commercianti_min:
            rc = int(round(percentile(pc, .5) * (1 - cfg.commercianti_sconto)))
            if cfg.commercianti_solo_ribasso and rc >= riv["R"]:
                # 26/10 (cliente: "margini molto gonfiati"): i commercianti chiedono più dei privati (garanzia,
                # preparazione); sulle auto vecchie ed economiche la loro mediana superava perfino il mercato M.
                # I prezzi dei commercianti possono solo ABBASSARE la rivendita prudente, mai alzarla.
                rc = None
        if len(pc) >= cfg.commercianti_min and rc:
            riv.update(R=rc, R_da="commercianti",
                       fascia=riv.get("fascia", "") + f" · rivendita dai prezzi di {len(pc)} commercianti −{int(cfg.commercianti_sconto*100)}%")
            out["ipotesi"].append(f"rivendita = mediana di {len(pc)} auto uguali in vendita da commercianti "
                                  f"({int(round(percentile(pc, .5)))} €) meno {int(cfg.commercianti_sconto*100)}% per vendere in fretta")
    out.update(riv)
    if riv["R"] is None:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(f"mercato M {out['M']} € oltre {cfg.m_massimo} €: la tabella prudenziale non si estende")
        return out

    R = riv["R"]
    if cfg.correzione_modello:
        f = correzione_modello(t.get("make"), t.get("model")) or correzione_modello("*SEG", segmento(t.get("year"), R))
        if f:
            R2 = int(round(R * (1 + f["fattore"])))
            out["correzione_modello"] = f
            out["ipotesi"].append(f"correzione del modello {'+' if f['fattore'] > 0 else ''}{f['fattore']*100:.0f}%: "
                                  f"su {f['n']} auto simili vendute dai concessionari il calcolo sbagliava di "
                                  f"{f['errore_pct']:+.0f}% ({R} → {R2} €)")
            R = R2
            out["R"] = R
    danno = t.get("damage_class") or "sconosciuto"
    if cfg.solo_sane and (danno in DANNEGGIATE or t.get("parts_cost_high") or t.get("rischio_strutturale")
                          or t.get("damage_declared")):
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append("auto incidentata o con guasti: per ora si propongono solo auto sane")
        return out
    if danno in DANNEGGIATE:
        perdita = int(round(R * cfg.perdita_storico_danno, -1))
        out["perdita_storico_danno"] = perdita
        out["ipotesi"].append(f"valore dopo la riparazione da confronti di auto sane, meno {int(cfg.perdita_storico_danno*100)}% "
                              "per lo storico del danno")
        R -= perdita
    tetto = t.get("tetto_rivendita")         # rivendita già abbassata da un controllo a mano: vale la più bassa
    if tetto and tetto < R:
        out["ipotesi"].append(f"controllo a mano: rivendita limitata a {int(tetto)} €")
        R = int(tetto)
    out["R_finale"] = R
    cs = costi(t, R, cfg)
    out["costi"] = cs["voci"]
    out["totale_costi"] = cs["totale_costi"]
    out["riserva"] = cs["riserva"]
    out["mancanti"] += cs["mancanti"]
    out["ipotesi"] += cs["ipotesi"]
    margine = R - int(t["price_eur"]) - cs["totale_costi"] - cs["riserva"]["euro"]
    out["margine"] = int(margine)
    out["sotto_R"] = int(t["price_eur"]) < R

    if cs["blocco"]:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(cs["blocco"])
        return out
    limite = cfg.sospetto_sotto_m
    if int(t["price_eur"]) <= cfg.sospetto_basse_max and (out.get("n") or 0) >= cfg.sospetto_basse_confronti:
        limite = min(limite, cfg.sospetto_basse)
        if int(t["price_eur"]) < out["M"] * cfg.sospetto_sotto_m:
            out["ipotesi"].append("prezzo molto sotto il mercato: normale per un'utilitaria economica, ma controllare bene lo stato")
    if int(t["price_eur"]) < out["M"] * cfg.prezzo_minimo_reale:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(f"prezzo sotto il {int(cfg.prezzo_minimo_reale*100)}% del mercato: non è un prezzo vero "
                             "(rata, anticipo, errore o truffa)")
        return out
    if int(t["price_eur"]) < out["M"] * limite and t.get("targa_e_telefono"):
        # 19/10 (cliente): prezzo molto basso ma con TARGA visibile e NUMERO di telefono -> si tiene, da verificare
        out["prezzo_molto_basso_ammesso"] = True
        out["ipotesi"].append("prezzo molto sotto il mercato: ammessa perché l'annuncio mostra la targa e il numero di telefono")
    elif int(t["price_eur"]) < out["M"] * limite:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(f"prezzo oltre il {int(round((1 - limite) * 100))}% sotto la mediana: il mercato "
                             "trovato non è affidabile (versione diversa, dati sbagliati o truffa)")
        return out
    if margine < soglia:
        out["categoria"] = "non_conveniente"
        out["motivi"].append(f"margine operativo prudente {margine} € sotto la soglia di {soglia} €")
        return out
    dubbi = []
    if out.get("prezzo_molto_basso_ammesso"):
        dubbi.append("prezzo molto sotto il mercato (targa e numero presenti): controllare bene auto e documenti")
    if out.get("solo_ai"):
        dubbi.append("stima del modello AI senza abbastanza confronti diretti: verificare il prezzo")
    elif n < cfg.n_preferito:
        dubbi.append(f"campione limitato ({n} confronti)")
    if (out.get("ai") or {}).get("disaccordo"):
        dubbi.append("modello AI e confronti diretti non concordano: verificare versione e stato")
    if sel["livello"] == 2:
        dubbi.append("confronti ampliati")
    if not t.get("gearbox") or not t.get("power_kw"):
        dubbi.append("motorizzazione o cambio non indicati nell'annuncio")
    if cs["danneggiata"]:
        dubbi.append("auto danneggiata: ripristino e manodopera da verificare")
    if cs["alto_rischio"]:
        dubbi.append("danno strutturale, airbag o guasto grave: serve un controllo approfondito")
    if set(t.get("price_flags") or []) & set(FLAG_ESCLUSI):
        dubbi.append("prezzo dell'annuncio da verificare")
    if dubbi:
        out["categoria"] = "candidato"
        out["motivi"] += dubbi
    else:
        out["categoria"] = "opportunita"
        out["motivi"].append(f"margine {margine} € ≥ soglia {soglia} € con {n} confronti equivalenti e costi documentati")
    return out


def riassunto(v: dict) -> dict:
    """Versione compatta per l'elenco del sito (la valutazione completa resta nel database)."""
    keys = ("categoria", "M", "L", "n", "livello", "riduzione", "fascia", "R", "R_finale", "margine", "soglia",
            "totale_costi", "campione", "policy", "metodo")
    out = {k: v.get(k) for k in keys if v.get(k) is not None}
    if v.get("riserva"):
        out["riserva"] = v["riserva"]["euro"]
    return out


_CORR = {"t": 0.0, "dati": {}}


def correzione_modello(make, model) -> dict | None:
    """Correzione per modello (tabella correzioni_modello, scritta da esame.correzioni ogni ESAME_ORE ore).
    Letta dal database al massimo ogni 30 minuti; se il database non risponde non corregge niente."""
    import time as _t
    if not make or not model:
        return None
    if _t.time() - _CORR["t"] > 1800:
        _CORR["t"] = _t.time()
        try:
            from ..db import connect
            with connect() as conn:
                rows = conn.execute("SELECT make, model, n, errore_pct, fattore FROM correzioni_modello").fetchall()
            _CORR["dati"] = {(r["make"].lower(), r["model"].lower()): {"n": r["n"], "errore_pct": r["errore_pct"],
                                                                       "fattore": r["fattore"]} for r in rows}
        except Exception:
            pass
    return _CORR["dati"].get((str(make).lower(), str(model).lower()))


def segmento(anno, R) -> str:
    """Segmento per la correzione automatica: età (≤5, 6–10, 11–15, oltre) × fascia di rivendita."""
    from datetime import date
    try:
        eta = date.today().year - int(anno)
    except (TypeError, ValueError):
        eta = 99
    e = "0-5" if eta <= 5 else "6-10" if eta <= 10 else "11-15" if eta <= 15 else "16+"
    try:
        r = float(R)
    except (TypeError, ValueError):
        r = 0
    b = "<5k" if r < 5000 else "5-10k" if r < 10000 else "10-20k" if r < 20000 else "20k+"
    return f"{e}|{b}"
