"""Policy di valutazione delle opportunità — "pv2", regole del 4/11/2026 (dealfinder/regole.py).

UNICO punto in cui si calcolano mercato M, rivendita R, margine e categoria di un'auto. Tutti i parametri vengono da
`dealfinder.regole` (nessuna soglia letta dall'ambiente: solo gli interruttori elencati lì).

Passi:
1. Gemelle: stessa marca, modello, carburante, carrozzeria, generazione (se nota), cambio e motorizzazione; anno ±3 e
   km ±50.000 (livello 2: ±4 / ±70.000; livello 3: ±5 / ±100.000) solo finché non si arriva al minimo del sito.
   Pulizia: doppioni, rate/leasing/civetta, finanziamento, importazione, spariti o non ricontrollati, danneggiate,
   prezzi simbolici o fuori scala. Allestimento sportivo/normale separato.
2. Correzione anno/km del prezzo di ogni gemella verso l'auto valutata (regole.Correzione): con due_sensi un confronto
   più nuovo scende e uno più vecchio sale, tetto ±25%.
3. Stima sulle gemelle (prezzi CHIESTI): mediana dei prezzi corretti, oppure il modello prezzi del gruppo se preciso.
4. Valore reale V (prezzo di VENDITA, pricing/valore_reale.py) fino a ValoreReale.prezzo_max: è la stima principale,
   le gemelle sono il controllo. Le gemelle si portano al prezzo di vendita col fattore misurato chiesto → venduto
   della loro fascia (regole.fattore_per). Se concordano entro ±accordo: M = V. Se no: media pesata con l'inverso
   della varianza in scala log (regole.media_pesata_log), MAI min o max:
       log M = (log V / σ_V² + log G / σ_G²) / (1/σ_V² + 1/σ_G²)
   σ_V = errore tipico del modello del valore reale (sigma, misurato sul gruppo); σ_G = errore di previsione della
   mediana di n gemelle = σ_gemelle·√(1 + π/(2n)), con σ_gemelle = 1,4826·MAD dei log-prezzi corretti (con meno di
   5 gemelle la dispersione non si stima: si usa σ_V); per il modello prezzi σ_G = errore_tipico·√(1 + 1/n).
   Entrambi gli errori hanno un minimo di ValoreReale.errore_min.
   0–1 gemelle: M = V solo se il gruppo ha almeno senza_gemelle_vendute_min auto VENDUTE e sigma ≤ senza_gemelle_sigma_max;
   categoria al massimo "candidato", serve il controllo dell'AI (serve_controllo_ai).
5. Senza valore reale (o sopra prezzo_max): M = stima delle gemelle (prezzi chiesti, M_fonte="annunci"); il sito la
   porta al prezzo di vendita UNA volta (regole.calibra_vendita). Con 0–1 gemelle e senza V: modello AI dei prezzi
   (prezzi chiesti, sempre "candidato"). Il modello AI con le gemelle è solo un segnale di disaccordo.
6. R = M, una sola volta: niente riduzioni, tetti, vendita veloce, prudenze. Margine = R − prezzo dell'annuncio −
   costi veri (ricambi e difetti letti dall'AI + costi fissi, questi al massimo 200 € in tutto). Nessuno sconto di
   trattativa.
7. Controlli anti-truffa sul prezzo chiesto rispetto a M; categorie dati_insufficienti | candidato | opportunita |
   non_conveniente contro scovo.soglia(prezzo), che qui NON viene abbassata. La fascia più richiesta
   (benzina_richiesta) cambia solo chi entra, mai il prezzo.
"""
from __future__ import annotations

import dataclasses
import functools
import math
import re
from datetime import datetime, timezone

from .. import regole as RG

POLICY_VERSION = "pv16-2026-10-19"
# Revisione del codice: solo informazione nel riassunto. Per decidere se una valutazione è vecchia si usa
# l'impronta delle regole (colonna impronta_regole) e la data, MAI un confronto su questo testo.
POLICY_REV = "2026-11-12a"
# 12/11 (cliente: "né abbassare né alzare, solo più preciso"): giudice AI su un campione di confronti per somiglianza
#   (non i 40 più economici); 2–7 confronti dispersi (max/min > 1,8) non fanno il prezzo; con 2–4 gemelle l'errore
#   viene dalla loro ampiezza; allestimento a livelli 0–3 (pricing/allestimento.py) nella scelta dei confronti;
#   R dai soli commercianti (pricing/rivendita.py) quando i dati bastano; guadagno netto stimato (pricing/netto.py).
# 4/11 (regole.py, cliente: "prezzi di rivendita veri"): una sola stima del prezzo di vendita, correzione anno/km nei
#   due sensi (interruttore), valore reale come stima principale con le gemelle come controllo (media pesata con gli
#   errori quando non concordano), niente min/max tra stime, niente riduzioni, niente taratura, R = M una volta.
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
    "esclusa": "Esclusa (regole di ammissione)",
}

# nome del campo di PolicyConfig → (sezione di regole.Regole, campo)
_MAPPA = {
    "anni_tol": ("confronti", "anni_tol"), "km_tol": ("confronti", "km_tol"), "potenza_tol": ("confronti", "potenza_tol"),
    "amplia": ("confronti", "amplia"), "anni_tol_2": ("confronti", "anni_tol_2"), "km_tol_2": ("confronti", "km_tol_2"),
    "livello_3": ("confronti", "livello_3"), "freschezza_giorni": ("confronti", "freschezza_giorni"),
    "prezzo_min": ("confronti", "prezzo_min"), "fuori_scala": ("confronti", "fuori_scala"),
    "n_preferito": ("confronti", "n_preferito"), "n_minimo": ("confronti", "n_minimo"),
    "allestimento": ("confronti", "allestimento"), "trazione": ("confronti", "trazione"),
    "motore_senza_kw": ("confronti", "motore_senza_kw"),
    "correzione": ("correzione", "attiva"), "due_sensi": ("correzione", "due_sensi"),
    "correzione_tetto": ("correzione", "tetto"), "svalutazione_anno": ("correzione", "svalutazione_anno"),
    "svalutazione_10km": ("correzione", "svalutazione_10km"),
    "reg": ("modello_locale", "attivo"), "reg_min": ("modello_locale", "n_min"), "reg_anni": ("modello_locale", "anni"),
    "reg_potenza": ("modello_locale", "potenza"), "reg_errore_max": ("modello_locale", "errore_max"),
    "reg_accordo": ("modello_locale", "accordo"),
    "ai": ("ai", "attiva"), "ai_errore_max": ("ai", "errore_max"), "ai_n_min": ("ai", "n_min"),
    "ai_accordo": ("ai", "accordo"), "ai_disaccordo": ("ai", "disaccordo"),
    "passaggio": ("costi", "passaggio"), "pulizia": ("costi", "pulizia"), "trasporto": ("costi", "trasporto"),
    "diagnosi_revisione": ("costi", "diagnosi_revisione"), "vendita": ("costi", "vendita"),
    "costo_fisso": ("costi", "costo_fisso"), "costo_fisso_alto": ("costi", "costo_fisso_alto"),
    "costo_alto_da": ("costi", "costo_alto_da"), "costi_fissi_max": ("costi", "fissi_max"),
    "manodopera_su_ricambi": ("costi", "manodopera_su_ricambi"),
    "sospetto_sotto_m": ("antitruffa", "sotto_m"), "sospetto_basse": ("antitruffa", "basse"),
    "sospetto_basse_max": ("antitruffa", "basse_max"), "sospetto_basse_confronti": ("antitruffa", "basse_confronti"),
    "prezzo_minimo_reale": ("antitruffa", "minimo_reale"),
    "solo_sane": ("ammissione", "solo_sane"), "perdita_storico_danno": ("ammissione", "perdita_storico_danno"),
}


class PolicyConfig:
    """Vista piatta di regole.Regole per il calcolo. PolicyConfig() = regole in vigore; PolicyConfig(campo=valore)
    cambia un campo solo per questa istanza (test, prove). Nessuna lettura dall'ambiente qui."""

    def __init__(self, regole: RG.Regole | None = None, **over):
        r = regole or RG.regole()
        self.regole = r
        for nome, (sez, campo) in _MAPPA.items():
            setattr(self, nome, getattr(getattr(r, sez), campo))
        for k, v in over.items():
            if k not in _MAPPA:
                raise TypeError(f"PolicyConfig: campo sconosciuto {k!r}")
            setattr(self, k, v)

    @classmethod
    def from_env(cls) -> "PolicyConfig":
        """Nome storico: le regole in vigore (regole.py + interruttori dell'ambiente), nessun'altra variabile."""
        return cls()

    def asdict(self) -> dict:
        return {k: getattr(self, k) for k in _MAPPA}

    def serve(self, prezzo) -> int:
        """Minimo di gemelle che il sito chiede (si allarga la ricerca finché non si arriva qui)."""
        c = self.regole.confronti
        return max(self.n_minimo, c.min_eco if (prezzo or 0) < c.eco_sotto else c.min_sito)


def benzina_richiesta(t: dict) -> bool:
    """Fascia più richiesta (regole.benzina_richiesta): decide solo l'ammissione, mai il prezzo."""
    return RG.benzina_richiesta(t)


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


def _sigma_robusta(valori) -> float:
    lv = sorted(math.log(float(v)) for v in valori if v and v > 0)
    if len(lv) < 2:
        return 0.0
    med = percentile(lv, .5)
    return 1.4826 * percentile([abs(x - med) for x in lv], .5)


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
    c_ = cfg.regole.correzione
    if len(g) >= c_.stima_min_annunci:
        try:
            import numpy as np
            y = np.log([c["price_eur"] for c in g])
            X = np.column_stack([np.ones(len(g)), [c["year"] for c in g], [c["mileage_km"] / 10000 for c in g]])
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
            ya, ka = 1 - math.exp(-beta[1]), 1 - math.exp(beta[2])
            if c_.anno_min <= ya <= c_.anno_max and c_.km_min <= ka <= c_.km_max:
                return float(ya), float(ka), f"stimata su {len(g)} annunci"
        except Exception:
            pass
    return cfg.svalutazione_anno, cfg.svalutazione_10km, "valori configurati"


def prezzo_corretto(c: dict, t: dict, ya: float, ka: float, corr=None) -> float:
    """Prezzo del confronto portato all'anno e ai km dell'auto valutata (regole.fattore_correzione): un confronto più
    nuovo o con meno km scende, con due_sensi uno più vecchio o con più km sale; tetto ±25%."""
    corr = corr or RG.regole().correzione
    f = RG.fattore_correzione(int(c["year"]) - int(t["year"]), (int(t["mileage_km"]) - int(c["mileage_km"])) / 10000,
                              ya, ka, corr)
    return float(c["price_eur"]) * f


_CARROZZERIE = [("commerciale", r"\bvan\b|\bcargo\b|furgon|autocarro|\bn1\b"),   # 6/11: Doblò Cargo, Panda Van
                ("sw", r"sports? ?tourer|station ?wagon|sportswagon|\bsw\b|touring|avant|variant|wagon|kombi|estate|shooting ?brake"),
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


# 6/11 (verifica prezzi): le ibride sono tutte fuel="ibrida"; mild, full e plug-in, diesel e benzina valgono cifre
# molto diverse (318d mhev ≠ 330e plug-in). Sottotipo dalla colonna fuel_tipo o dal testo, base diesel dal testo.
_RE_DIESEL_BASE = re.compile(r"(?i)\b(\d{2,3}d|\d{2,3}\s+d|tdi|dci|hdi|crdi|multijet|mjet|cdi|bluehdi|ecoblue|"
                             r"d\d{3}|diesel|jtd\w*|tdci|bluetec)\b")
_RE_PLUGIN_SIGLE = re.compile(r"(?i)\b(\d{2,3}\s?x?e|\d{3}\s?de|xdrive\s?\d{2}e|e-?hybrid|tfsi\s?e|gte|recharge|t8|"
                              r"phev|plug[\s-]?in)\b")


def ibrida_tipo(c: dict) -> tuple:
    """(sottotipo, base) di un'ibrida: sottotipo 'mild' | 'full' | 'plug-in' | None, base 'diesel' | 'benzina'."""
    if c.get("fuel") != "ibrida":
        return (None, None)
    from ..core import normalize as N
    testo = f"{c.get('version_raw') or ''} {c.get('title') or ''}"
    st = c.get("fuel_tipo") if c.get("fuel_tipo") in ("mild", "full", "plug-in") else N.fuel_tipo("ibrida", testo)
    if st != "plug-in" and _RE_PLUGIN_SIGLE.search(testo):
        st = "plug-in"
    base = "diesel" if (st != "plug-in" or "de" in testo.lower().split()) and _RE_DIESEL_BASE.search(testo) else "benzina"
    return (st, base)


def _ibride_diverse(t: dict, c: dict) -> str | None:
    if t.get("fuel") != "ibrida" or c.get("fuel") != "ibrida":
        return None
    (st, bt), (sc, bc) = ibrida_tipo(t), ibrida_tipo(c)
    if bt != bc:
        return "ibrida diesel/benzina diversa"
    if (st == "plug-in") != (sc == "plug-in"):
        return "ibrida plug-in con non plug-in"
    if st and sc and st != sc:
        return "tipo di ibrida diverso"
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
        if _ibride_diverse(t, c):
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
    ib = _ibride_diverse(t, c)
    if ib:
        return ib
    bt, bc = carrozzeria(t), carrozzeria(c)
    if bt != bc and (bt or bc) and not (bt is None and bc in ("sw",)) and not (bc is None and bt in ("sw",)):
        return "carrozzeria diversa"
    gt, gc = _gen(t), _gen(c)
    if gt and gc and gt != gc:
        return "generazione diversa"
    if t.get("year") is None or c.get("year") is None:
        return "anno mancante"
    atol = cfg.anni_tol if livello == 1 else cfg.anni_tol_2 if livello == 2 else cfg.anni_tol_2 + 1
    if abs(int(c["year"]) - int(t["year"])) > atol:
        return f"anno oltre ±{atol}"
    if t.get("mileage_km") is None or c.get("mileage_km") is None:
        return "km mancanti"
    ktol = cfg.km_tol if livello == 1 else cfg.km_tol_2 if livello == 2 else cfg.km_tol_2 + 30000
    if abs(int(c["mileage_km"]) - int(t["mileage_km"])) > ktol:
        return f"km oltre ±{ktol:,}".replace(",", ".")
    if cfg.trazione:    # 27/10 sera (cliente): stessa trazione (4x4 con 4x4)
        import re as _re2
        t4 = _re2.compile(r"(?i)\b(4x4|4wd|awd|xdrive|quattro|4motion|q4|all4|4matic|integrale)\b")
        if bool(t4.search(f"{t.get('version_raw') or ''} {t.get('title') or ''}")) != \
                bool(t4.search(f"{c.get('version_raw') or ''} {c.get('title') or ''}")):
            return "trazione diversa"
    tg, cg = _norm_gear(t.get("gearbox")), _norm_gear(c.get("gearbox"))
    if tg and cg and tg != cg:
        return "cambio diverso"
    if livello == 1 and tg and not cg:
        return "cambio non indicato"
    tp, cp = t.get("power_kw"), c.get("power_kw")
    if not tp and cfg.motore_senza_kw:
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
            # 4/11: le auto vendute entrano nel valore reale (prezzo di vendita), non tra le gemelle (prezzi chiesti)
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


_ALLESTIMENTI_SPORT = re.compile(
    r"\bm[\s-]?sport\b|\bmsport\b|\bm[\s-]?performance\b|\bs[\s-]?line\b|\brs[\s-]?line\b|\br[\s-]?line\b|"
    r"\bamg[\s-]?line\b|\bamg\b|\bgt[\s-]?line\b|\bst[\s-]?line\b|\bn[\s-]?line\b|\bfr\b|\bgts\b|"
    r"\br[\s-]?design\b|\bs[\s-]?design\b|\bvignale\b|\bblack\s+edition\b|\bcompetizione\b|\bveloce\b", re.I)


def allestimento(x: dict) -> str:
    """'sportivo' per gli allestimenti sportivi/premium scritti nella versione o nel titolo, altrimenti 'normale'."""
    return "sportivo" if _ALLESTIMENTI_SPORT.search(f"{x.get('version_raw') or ''} {x.get('title') or ''}") else "normale"


@functools.lru_cache(maxsize=100000)
def _liv_testo(make, model, versione, titolo):
    from .allestimento import livello
    r = livello({"make": make, "model": model, "version_raw": versione, "title": titolo})
    return r["livello"] if r else None


def _liv(x: dict):
    """Livello di allestimento 0–3 (pricing/allestimento.py) o None (in cache per testo: lo stesso annuncio è
    confronto di molte auto)."""
    return _liv_testo(x.get("make"), x.get("model"), x.get("version_raw"), x.get("title"))


def _per_allestimento(t: dict, validi: list[dict], serve: int, cfg: PolicyConfig) -> tuple[list, list]:
    """12/11: confronti per livello di allestimento. Prima lo stesso livello; se non bastano (serve) si aggiungono
    quelli a ±1, poi quelli senza allestimento scritto, poi (solo sotto n_minimo) tutti gli altri. I confronti di livello diverso si portano
    al livello dell'auto con (1 − fattore)^(livello confronto − livello auto): un confronto più accessoriato scende di
    tutti i livelli, uno meno accessoriato sale al massimo di UN livello (mai oltre il livello dell'auto). Quelli
    senza allestimento scritto non si correggono."""
    lt = _liv(t)
    f = cfg.regole.allestimento.fattore
    gruppi = {"stesso": [], "vicino": [], "ignoto": [], "lontano": []}
    for c in validi:
        lc = _liv(c)
        k = "ignoto" if lc is None else "stesso" if lc == lt else "vicino" if abs(lc - lt) == 1 else "lontano"
        gruppi[k].append(c)
    presi, scartati = [], []
    for k in ("stesso", "vicino", "ignoto", "lontano"):
        # i livelli lontani (±2 o più) solo se senza di loro non si arriva nemmeno al minimo assoluto (n_minimo):
        # prima si allarga anno/km (livelli 2 e 3 della ricerca) per trovare lo stesso allestimento
        basta = cfg.n_minimo if k == "lontano" else serve
        if len(presi) >= basta and presi:
            scartati += [(c, f"allestimento diverso ({k})") for c in gruppi[k]]
            continue
        for c in gruppi[k]:
            lc = _liv(c)
            if lc is not None and lc != lt:
                # più accessoriato: scende di tutti i livelli; meno accessoriato: sale al massimo di UN livello
                d = max(lc - lt, -1)
                c = dict(c, prezzo=c["prezzo"] * (1 - f) ** d, allestimento_corr=round((1 - f) ** d, 4))
            presi.append(c)
    return presi, scartati


def somiglianza(t: dict, c: dict) -> float:
    """Distanza (più bassa = più simile) per scegliere il campione di confronti del giudice AI: anno, km, potenza,
    livello di allestimento, cambio. Non dipende dal prezzo."""
    d = abs(int(c.get("year") or 0) - int(t.get("year") or 0)) / 3.0
    if c.get("mileage_km") is not None and t.get("mileage_km") is not None:
        d += abs(int(c["mileage_km"]) - int(t["mileage_km"])) / 50000.0
    tp, cp = t.get("power_kw"), c.get("power_kw")
    d += abs(cp - tp) / (0.1 * tp) if tp and cp else 0.5
    lt, lc = _liv(t), _liv(c)
    d += abs(lc - lt) if lt is not None and lc is not None else 0.5 if (lt is None) != (lc is None) else 0
    if _norm_gear(t.get("gearbox")) != _norm_gear(c.get("gearbox")):
        d += 0.5
    return d


def campione_giudice(t: dict, validi: list[dict], quanti: int) -> list[dict]:
    """I `quanti` confronti più simili all'auto (rapporto 2 E1), poi in ordine di prezzo per la scheda."""
    scelti = sorted(validi, key=lambda c: (somiglianza(t, c), c.get("id") or 0))[:quanti]
    return sorted(scelti, key=lambda c: c["prezzo"])


def seleziona(t: dict, mercato: list[dict], cfg: PolicyConfig, now=None) -> dict:
    puliti, esclusi = pulisci(t, mercato, cfg, now)
    if cfg.correzione and t.get("year") and t.get("mileage_km") is not None:
        ya, ka, origine = tassi(puliti, cfg)
        corr = dataclasses.replace(cfg.regole.correzione, due_sensi=cfg.due_sensi, tetto=cfg.correzione_tetto)
        puliti = [dict(c, prezzo=prezzo_corretto(c, t, ya, ka, corr) if c.get("year") and c.get("mileage_km") is not None
                       else float(c["price_eur"])) for c in puliti]      # senza anno/km: escluse dopo, non corrette
    else:
        ya = ka = 0.0
        origine = "nessuna correzione"
        puliti = [dict(c, prezzo=float(c["price_eur"])) for c in puliti]
    livelli = [1, 2] if cfg.amplia else [1]
    # 2/11 notte (cliente): livello 3 (anno +1 e km +30.000 oltre il livello 2), solo se i primi non bastano.
    # 4/11: il prezzo non si abbassa più per questo; la valutazione resta "candidato" (confronti ampliati)
    if cfg.livello_3 and 2 in livelli:
        livelli = livelli + [3]
    motivi = {}
    _serve = cfg.serve(t.get("price_eur"))
    _migliore = None
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
        # 1/11 (cliente): stesso allestimento. Una "M Sport", "S line", "R-Line", "AMG Line"… vale più della versione
        # normale: se tra i confronti validi ce ne sono abbastanza (n_minimo) dello stesso tipo, si usano solo quelli
        if cfg.allestimento and validi and cfg.regole.allestimento.attivo and _liv(t) is not None:
            validi, scart_all = _per_allestimento(t, validi, _serve, cfg)
            scartati += scart_all
        elif cfg.allestimento and validi:
            ta = allestimento(t)
            stessi = [c for c in validi if allestimento(c) == ta]
            # 3/11 (controllo indipendente): soglia n_minimo, non il minimo del sito: con _serve una "Business" veniva
            # confrontata con le "R-Line" (+21% sul mercato) quando le Business uguali erano 3-7
            if len(stessi) >= cfg.n_minimo and len(stessi) < len(validi):
                for c in validi:
                    if allestimento(c) != ta:
                        scartati.append((c, "allestimento diverso (sportivo/normale)"))
                validi = stessi
        if _migliore is None or len(validi) > len(_migliore[1]):
            _migliore = (liv, validi, scartati)
        if len(validi) >= _serve:
            break
    if len(validi) < _serve and _migliore is not None:
        liv, validi, scartati = _migliore      # il livello con più confronti validi
    for c, why in scartati:
        motivi[why] = motivi.get(why, 0) + 1
    reg = regressione(t, puliti, cfg) if cfg.reg else None
    return {"livello": liv, "validi": validi, "esclusi": esclusi, "regressione": reg,
            "correzione": {"anno": round(ya, 4), "km_10000": round(ka, 4), "origine": origine,
                           "due_sensi": bool(cfg.due_sensi) if cfg.correzione else None},
            "non_equivalenti": [{"id": c.get("id"), "prezzo": c.get("price_eur"), "motivo": why} for c, why in scartati],
            "motivi_non_equivalenti": motivi}


# ---------------------------------------------------------------- costi

def costi(t: dict, R: int, cfg: PolicyConfig) -> dict:
    """Costi VERI per voce: ricambi (stima dei pezzi), difetti letti dall'AI (ai_difetti, non gravi), e le voci fisse
    del commerciante che insieme non superano mai cfg.costi_fissi_max (200 €). Nessuna riserva, IVA o garanzia
    inventata; nessuno sconto di trattativa."""
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
    else:
        voci["ricambi"] = {"euro": int(hi or 0), "origine": "annuncio" if not hi else "stima_ricambi",
                           "nota": "nessun danno dichiarato" if not hi else "ricambi per guasti dichiarati"}
        if danno == "sconosciuto":
            ipotesi.append("condizioni non dichiarate: nessun danno stimato, da guardare dal vivo")
    mo = int(round((voci["ricambi"]["euro"]) * cfg.manodopera_su_ricambi, -1))
    if mo:
        voci["manodopera"] = {"euro": mo, "origine": "stima", "nota": "manodopera stimata sui ricambi"}
    cd = int(t.get("costo_difetti") or 0)
    if cd:
        voci["difetti"] = {"euro": cd, "origine": "ai_difetti",
                           "nota": f"riparazione dei difetti scritti nell'annuncio (lettura AI): {t.get('difetti_motivo') or ''}"[:160]}
    fissi = cfg.passaggio + cfg.pulizia + cfg.trasporto + cfg.diagnosi_revisione + cfg.vendita + (
        cfg.costo_fisso_alto if int(t["price_eur"]) > cfg.costo_alto_da else cfg.costo_fisso)
    fissi = max(0, min(int(fissi), int(cfg.costi_fissi_max)))        # cliente 3/11 23:23: mai oltre 200 € in tutto
    voci["costi_commerciante"] = {"euro": fissi, "origine": "config",
                                  "nota": f"costi fissi del commerciante (al massimo {cfg.costi_fissi_max} € in tutto)"}
    ripristino = voci["ricambi"]["euro"] + voci.get("manodopera", {}).get("euro", 0) + voci.get("difetti", {}).get("euro", 0)
    alto = danno == "alto_rischio" or bool(t.get("rischio_strutturale"))
    voci = {k: v for k, v in voci.items() if v["euro"] or k == "ricambi"}     # voci a zero non si mostrano
    totale = sum(v["euro"] for v in voci.values())
    return {"voci": voci, "ripristino": ripristino, "totale_costi": totale, "fissi": fissi,
            "mancanti": mancanti, "ipotesi": ipotesi, "blocco": blocco, "danneggiata": danneggiata, "alto_rischio": alto}


# ---------------------------------------------------------------- valutazione

def _comp_json(c, now):
    return {"id": c.get("id"), "url": c.get("url"), "fonte": c.get("source"), "prezzo": c.get("price_eur"),
            "prezzo_corretto": int(round(c["prezzo"])) if c.get("prezzo") is not None else None,
            "anno": c.get("year"), "km": c.get("mileage_km"), "cambio": c.get("gearbox"), "kw": c.get("power_kw"),
            "versione": (c.get("version_raw") or "")[:40] or None, "venditore": c.get("seller_type"),
            "pubblicato": str(c.get("first_seen_at"))[:10] if c.get("first_seen_at") else None,
            "verificato": str(max((d for d in (c.get("last_checked_at"), c.get("last_seen_at")) if d), default=""))[:10] or None}


def _errore_gemelle(prezzi: list, reg: dict | None, metodo: str, sigma_v: float, errore_min: float,
                    ampiezza: bool = True) -> float:
    """Errore di previsione (in log) della stima delle gemelle per una nuova auto uguale (vedi docstring del modulo)."""
    n = len(prezzi)
    if metodo == "modello" and reg:
        return max(errore_min, float(reg["errore_tipico"]) * math.sqrt(1 + 1 / max(int(reg["n"]), 1)))
    if n >= 5 or not ampiezza:
        s = _sigma_robusta(prezzi) if n >= 5 else sigma_v
    else:
        # 12/11 (rapporto 2 E3): con 2–4 gemelle la dispersione si stima dall'ampiezza (ln(max/min)/d2) e non è mai
        # sotto l'errore del valore reale: due confronti lontanissimi non pesano più come se fossero concordi
        d2 = {2: 1.128, 3: 1.693, 4: 2.059}.get(n)
        amp = math.log(max(prezzi) / min(prezzi)) / d2 if d2 and min(prezzi) > 0 else 0.0
        s = max(sigma_v, amp)
    return max(errore_min, s * math.sqrt(1 + math.pi / (2 * max(n, 1))))


def valuta(t: dict, mercato: list[dict], soglia: int, cfg: PolicyConfig | None = None, now=None) -> dict:
    """Valutazione completa e verificabile di un'auto. `t` e `mercato` sono righe di `listings` (dict).
    Ingressi facoltativi in t: vr (valore reale: v, p25, sigma, n, n_vendute), ai (stima_ai), fattori_calibra
    (regole.fattori_calibrazione), targa_e_telefono, parts_cost_*, costo_difetti, rischio_strutturale."""
    cfg = cfg or PolicyConfig()
    r = cfg.regole
    now = now or datetime.now(timezone.utc)
    out = {"policy": POLICY_VERSION, "policy_rev": POLICY_REV, "regole": r.versione, "id": t.get("id"),
           "acquisto": t.get("price_eur"), "soglia": soglia, "mancanti": [], "ipotesi": [], "motivi": []}
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
    prezzo_t = int(t["price_eur"])

    # 1-3) gemelle e stima sui prezzi chiesti
    sel = seleziona(t, mercato, cfg, now)
    validi = sel["validi"]
    n = len(validi)
    n_gem = n
    prezzi = [c["prezzo"] for c in validi]           # prezzi corretti per anno e km
    out.update(n=n, n_gemelle=n_gem, livello=sel["livello"], esclusi=sel["esclusi"][:40], n_esclusi=len(sel["esclusi"]),
               non_equivalenti=sel["motivi_non_equivalenti"],
               confronti=[_comp_json(c, now) for c in campione_giudice(t, validi, cfg.regole.confronti.giudice_campione)],
               correzione=sel["correzione"],
               venditori={s: sum(1 for c in validi if (c.get("seller_type") or "sconosciuto") == s)
                          for s in {c.get("seller_type") or "sconosciuto" for c in validi}})
    if sel["livello"] >= 2:
        _at = cfg.anni_tol_2 + (1 if sel["livello"] == 3 else 0)
        _kt = cfg.km_tol_2 + (30000 if sel["livello"] == 3 else 0)
        out["ipotesi"].append(f"confronti ampliati (livello {sel['livello']}: anno ±{_at}, km ±{_kt:,}, cambio/potenza non sempre "
                              "indicati): affidabilità ridotta".replace(",", "."))
    if n and cfg.correzione:
        cz = sel["correzione"]
        out["ipotesi"].append(f"prezzi dei confronti corretti verso {t['year']} e {int(t['mileage_km']):,} km "
                              f"({'nei due sensi, tetto ±' + str(int(cfg.correzione_tetto * 100)) + '%' if cfg.due_sensi else 'solo verso il basso'}): "
                              f"{cz['anno']*100:.1f}% per anno, {cz['km_10000']*100:.1f}% ogni 10.000 km "
                              f"({cz['origine']})".replace(",", "."))
    # 12/11 (rapporto 2 E3): 2–7 gemelle con prezzi corretti molto dispersi (max/min oltre dispersione_max) non dicono
    # il prezzo: si usa solo un valore reale affidabile, altrimenti "dati_insufficienti"
    cc = r.confronti
    disp = (max(prezzi) / min(prezzi)) if len(prezzi) >= 2 and min(prezzi) > 0 else None
    dispersi = disp is not None and n_gem < cc.dispersione_sotto_n and disp > cc.dispersione_max
    if dispersi:
        out["dispersione"] = round(disp, 2)
        out["ipotesi"].append(f"{n_gem} auto uguali con prezzi troppo diversi (il più alto è {disp:.1f} volte il più "
                              "basso): non usate per il prezzo")
    try:
        from .allestimento import allestimento as _all
        if _all(t):
            out["allestimento"] = _all(t)
    except Exception:
        pass
    M_ann = None
    metodo = None
    if n:
        M_ann = percentile(prezzi, .5)
        out["L"] = int(round(percentile(prezzi, .25)))           # solo informazione: non taglia niente
        metodo = "mediana"
    p_comm = [c["prezzo"] for c in validi if c.get("seller_type") == "commerciante"]
    p_priv = [c["prezzo"] for c in validi if c.get("seller_type") != "commerciante"]
    if p_comm:
        out["M_commercianti"], out["n_commercianti"] = int(round(percentile(p_comm, .5))), len(p_comm)
    if p_priv:
        out["M_privati"], out["n_privati"] = int(round(percentile(p_priv, .5))), len(p_priv)
    reg = sel.get("regressione")
    if reg and reg["errore_tipico"] <= cfg.reg_errore_max and M_ann and n_gem >= cfg.n_minimo \
            and abs(reg["prezzo"] / M_ann - 1) > cfg.reg_accordo:
        # 6/11 (verifica prezzi): il modello del gruppo non concorda con le auto uguali vere (generazione, versione o
        # motore diversi nel gruppo): vale la mediana delle gemelle
        out["modello_scartato"] = {"prezzo": int(round(reg["prezzo"])), "n": reg["n"],
                                   "differenza": round(reg["prezzo"] / M_ann - 1, 3)}
        out["ipotesi"].append(f"modello prezzi ({int(round(reg['prezzo']))} €) lontano dalla mediana delle {n_gem} auto "
                              f"uguali ({int(round(M_ann))} €): usata la mediana")
        reg = None
    if reg and reg["errore_tipico"] <= cfg.reg_errore_max:
        out["M_confronti"] = int(round(M_ann)) if M_ann else None
        M_ann = reg["prezzo"]
        metodo = "modello"
        out["modello"] = reg
        out["ipotesi"].append(f"prezzo chiesto stimato con il modello prezzi su {reg['n']} annunci dello stesso modello "
                              f"({reg['anno_pct']*100:.1f}% per anno, {reg['km10k_pct']*100:.1f}% ogni 10.000 km, "
                              f"errore tipico ±{reg['errore_tipico']*100:.0f}%)")
        n = max(n, reg["n"])                         # base della stima: annunci del modello (le gemelle sono in n_gemelle)
        out["n_modello"] = reg["n"]                  # 6/11: il sito mostra le auto uguali vere (n = n_gemelle)
    if M_ann:
        out["M_gemelle"] = int(round(M_ann))         # stima sui prezzi CHIESTI (gemelle o modello), prima del valore reale
    out["campione"] = ("base preferita" if n >= cfg.n_preferito else "provvisoria (campione limitato)"
                       if n >= cfg.n_minimo else "insufficiente")

    # segnale del modello AI (prezzi chiesti): mai dentro M quando ci sono le gemelle
    ai = t.get("ai") if cfg.ai else None
    ai_ok = bool(ai and ai.get("prezzo") and ai.get("errore") is not None and ai["errore"] <= cfg.ai_errore_max
                 and (ai.get("n") or 0) >= cfg.ai_n_min)
    if ai_ok:
        out["ai"] = {"prezzo": int(ai["prezzo"]), "errore": round(float(ai["errore"]), 3), "n": int(ai.get("n") or 0)}
        if M_ann and n >= cfg.n_minimo:
            diff = ai["prezzo"] / M_ann - 1
            out["ai"].update(differenza=round(diff, 3), concorda=abs(diff) <= cfg.ai_accordo,
                             disaccordo=abs(diff) > cfg.ai_disaccordo)

    if dispersi:
        M_ann, reg, metodo, n_gem = None, None, None, 0
        out.pop("modello", None)
    # 4) valore reale (prezzo di vendita): stima principale fino a prezzo_max
    vrr = r.valore_reale
    vr = t.get("vr") if (vrr.attivo and prezzo_t <= vrr.prezzo_max) else None
    if vr and not vr.get("v"):
        vr = None
    serve = cfg.serve(prezzo_t)
    n_gate = n
    M = None
    fonte = None
    out["metodo_gemelle"] = metodo
    if vr:
        V, sg = int(vr["v"]), float(vr.get("sigma") or 1)
        out["valore_reale"] = {"V": V, "P25": vr.get("p25"), "sigma": sg, "n_gruppo": vr.get("n"),
                               "n_vendute": vr.get("n_vendute"), "calcolato": str(vr.get("computed_at") or "")[:19] or None}
    if vr and M_ann and n_gem >= cfg.n_minimo:
        f = RG.fattore_per(M_ann, t.get("fattori_calibra"), r)
        G = M_ann * f                                # gemelle portate al prezzo di vendita (fattore misurato)
        diff = G / V - 1
        accordo = abs(diff) <= vrr.accordo
        out["valore_reale"].update(differenza_gemelle=round(diff, 3), fattore_gemelle=round(f, 3),
                                   gemelle_vendita=int(round(G)))
        out["vr_accordo"] = accordo
        if accordo:
            M, metodo = float(V), "valore_reale"
            out["valore_reale"]["pesi"] = {"V": 1.0, "gemelle": 0.0}
            out["ipotesi"].append(f"valore reale {V} € (modello su {vr.get('n')} annunci e auto vendute, errore tipico "
                                  f"±{sg*100:.0f}%); le gemelle al prezzo di vendita ({int(round(G))} €) concordano")
        else:
            sG = _errore_gemelle(prezzi, reg, out["metodo_gemelle"], sg, vrr.errore_min, r.confronti.ampiezza_poche)
            M, pesi = RG.media_pesata_log([(V, sg), (G, sG)], vrr.errore_min)
            metodo = "valore_reale_combinato"
            out["valore_reale"].update(pesi={"V": round(pesi[0], 3), "gemelle": round(pesi[1], 3)},
                                       errore_gemelle=round(sG, 3))
            out["ipotesi"].append(f"valore reale {V} € e gemelle al prezzo di vendita {int(round(G))} € non concordano "
                                  f"({diff*100:+.0f}%): media pesata con gli errori misurati (peso valore reale "
                                  f"{pesi[0]*100:.0f}%, gemelle {pesi[1]*100:.0f}%)")
        fonte = "vendite"
        preciso = sg <= vrr.poche_sigma_max and (vr.get("n") or 0) >= vrr.poche_gruppo_min
        if n_gem < serve and preciso and abs(diff) <= vrr.poche_diff_max:
            out["vr_poche_gemelle"] = True
            n_gate = max(n, serve)                   # il numero minimo lo garantisce il modello su tutto il gruppo
    elif vr and n_gem < cfg.n_minimo:
        nv = vr.get("n_vendute")
        sg = float(vr.get("sigma") or 1)
        if nv is not None and int(nv) >= vrr.senza_gemelle_vendute_min and sg <= vrr.senza_gemelle_sigma_max:
            M, metodo, fonte = float(vr["v"]), "valore_reale_modello", "vendite"
            out.update(vr_poche_gemelle=True, vr_solo_modello=True, serve_controllo_ai=True)
            n_gate = max(n, serve)
            out["ipotesi"].append(f"meno di {cfg.n_minimo} auto uguali in vendita: prezzo di vendita dal modello del "
                                  f"valore reale ({nv} auto vendute e {vr.get('n')} annunci dello stesso modello, errore "
                                  f"tipico ±{sg*100:.0f}%): solo candidato, da controllare con l'AI")
        else:
            out["ipotesi"].append(f"valore reale senza gemelle non usato: servono almeno {vrr.senza_gemelle_vendute_min} "
                                  f"auto vendute nel gruppo (sono {nv if nv is not None else 'sconosciute'}) e un errore "
                                  f"tipico fino al {vrr.senza_gemelle_sigma_max*100:.0f}% (è {sg*100:.0f}%)")
    # 5) senza valore reale: stima sui prezzi chiesti (il sito la porta al prezzo di vendita una volta)
    if M is None and M_ann and n >= cfg.n_minimo:
        M, fonte = float(M_ann), "annunci"
    if M is None and ai_ok and n_gem < cfg.n_minimo:
        M, metodo, fonte = float(ai["prezzo"]), "ai", "annunci"
        out["solo_ai"] = True
        n_gate = max(n, cfg.n_minimo)
        out["ipotesi"].append(f"pochi confronti diretti: prezzo chiesto stimato dal modello AI ({int(ai['prezzo'])} €, "
                              f"errore tipico ±{ai['errore']*100:.0f}% su {int(ai.get('n') or 0)} annunci di questo modello)")
    if ai_ok and M and (out.get("ai") or {}).get("disaccordo") is None:
        out["ai"]["disaccordo"] = abs(ai["prezzo"] / M - 1) > cfg.ai_disaccordo and metodo != "ai"
    out["metodo"] = metodo or "mediana"
    out["metodo_base"] = out["metodo_gemelle"] or out["metodo"]
    out["n_gate"] = n_gate
    if M is None or (n_gate < cfg.n_minimo and fonte != "vendite"):
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(f"confronti troppo dispersi ({out['dispersione']}×) e nessun valore reale affidabile"
                             if dispersi else f"solo {n} confronti validi (servono almeno {cfg.n_minimo})")
        return out
    out["M"] = int(round(M))
    out["M_fonte"] = fonte
    out.setdefault("L", out["M"])
    out["L"] = min(out["L"], out["M"]) if fonte == "annunci" else out["L"]
    if prezzi and (n_gate < serve or sel["livello"] == 3) and len(prezzi) >= 3:
        # pochi confronti (o ricerca allargata al livello 3): il prezzo NON si abbassa più; resta "candidato".
        # prudente_pochi è il nome storico letto dal sito per l'eccezione al minimo di confronti.
        out["pochi_confronti"] = True
        out["prudente_pochi"] = True

    # 6) rivendita = prezzo vero di vendita, una volta sola
    R = out["M"]
    out.update(R=R, centrale_ridotto=R, riduzione=0.0, R_da="mercato vero", fascia="rivendita = prezzo vero di mercato")
    # 12/11 (cliente: "rivendita dai dati veri dei commercianti"): R dai soli annunci di commercianti della stessa
    # auto (venduti, o attivi meno lo sconto osservato), quando i dati bastano (pricing/rivendita.py)
    rc = None
    if r.rivendita.attiva and t.get("commercianti") is not None:
        from . import rivendita as RV
        try:
            rc = RV.calcola(t, t.get("commercianti"), cfg, now)
        except Exception as e:                      # mai bloccare la valutazione per questo
            out["ipotesi"].append(f"rivendita dai commercianti non calcolata: {str(e)[:80]}")
    if rc:
        out["rivendita_commercianti"] = rc
        out["giorni_vendita_commercianti"] = rc.get("giorni_vendita")
        out["n_dati_rivendita"] = rc.get("n_usati") or 0
        if rc.get("R"):
            # 12/11 (controllo del giudice, cliente "non gonfiare"): i commercianti servono solo a CONFERMARE o
            # ABBASSARE la rivendita (R è il più basso tra M e R_commercianti; M non cambia). R_commercianti resta a parte (anche per il sito, che
            # dopo la calibrazione rifà il minimo: regole.calibra_vendita).
            Rc = int(rc["R"])
            out["R_commercianti"] = Rc
            fonte_txt = (f"ultimo prezzo di {rc['n_venduti']} auto uguali vendute da commercianti negli ultimi "
                         f"{r.rivendita.giorni} giorni" + (f" (in media in {rc['giorni_vendita']:.0f} giorni)"
                                                          if rc.get("giorni_vendita") is not None else "")
                         if rc["fonte"] == "venduti" else
                         f"{rc['n_attivi']} auto uguali in vendita da commercianti meno lo sconto osservato del "
                         f"{rc['sconto']*100:.1f}%")
            if Rc < R:
                R = Rc
                out.update(R=R, centrale_ridotto=R, R_da="commercianti",
                           fascia="rivendita = prezzo dei commercianti, più basso del mercato")
                out["ipotesi"].append(f"rivendita abbassata a {R} € dai commercianti: {fonte_txt}")
            else:
                out["R_da"] = "mercato vero (confermato dai commercianti)"
                out["ipotesi"].append(f"rivendita dei commercianti {Rc} € non sotto il mercato ({fonte_txt}): "
                                      f"resta {R} €")
    danno = t.get("damage_class") or "sconosciuto"
    # 4/11 (Incidentate): con solo_sane restano fuori solo i danni gravi/strutturali e quelli non ancora stimati;
    # un danno dichiarato con il costo di riparazione stimato (ricambi o lettura AI) si valuta col costo tolto
    if cfg.solo_sane and (danno in ("grave", "alto_rischio") or t.get("rischio_strutturale") or t.get("damage_declared")
                          or (danno in DANNEGGIATE and t.get("parts_cost_high") is None)):
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append("auto con danno grave, strutturale o non ancora stimato: si propongono solo auto sane "
                             "o con il costo di riparazione stimato")
        return out
    if danno in DANNEGGIATE and cfg.perdita_storico_danno:
        perdita = int(round(R * cfg.perdita_storico_danno, -1))
        out["perdita_storico_danno"] = perdita
        out["ipotesi"].append(f"valore dopo la riparazione da confronti di auto sane, meno {int(cfg.perdita_storico_danno*100)}% "
                              "per lo storico del danno")
        R -= perdita
    out["R_finale"] = R
    cs = costi(t, R, cfg)
    out["costi"] = cs["voci"]
    out["totale_costi"] = cs["totale_costi"]
    out["riserva"] = {"euro": 0, "pct": 0.0, "nota": "nessuna riserva: solo costi veri"}
    if cs["voci"].get("difetti"):
        out["costo_difetti_incluso"] = True          # il sito non deve togliere di nuovo il costo dei difetti AI
    out["mancanti"] += cs["mancanti"]
    out["ipotesi"] += cs["ipotesi"]
    margine = R - prezzo_t - cs["totale_costi"]
    out["margine"] = int(margine)
    out["sotto_R"] = prezzo_t < R
    from . import netto as NT                         # 12/11: secondo numero, il margine lordo resta quello sopra
    # trattativa: standard (3%). Lo sconto osservato dei commercianti è già dentro R_commercianti (ultimo prezzo dei
    # venduti, o attivi già scontati): non si conta due volte (controllo del giudice M4)
    nt = NT.calcola(R, prezzo_t, cs["totale_costi"])
    if nt:
        out["netto"] = nt["netto"]
        out["costi_netto"] = dict(nt["voci"], sconto_pct=nt["sconto_pct"], sconto_da=nt["sconto_da"])

    if cs["blocco"]:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(cs["blocco"])
        return out
    # 7) anti-truffa: sul prezzo CHIESTO dell'annuncio rispetto a M
    limite = cfg.sospetto_sotto_m
    if prezzo_t <= cfg.sospetto_basse_max and (out.get("n") or 0) >= cfg.sospetto_basse_confronti:
        limite = min(limite, cfg.sospetto_basse)
        if prezzo_t < out["M"] * cfg.sospetto_sotto_m:
            out["ipotesi"].append("prezzo molto sotto il mercato: normale per un'utilitaria economica, ma controllare bene lo stato")
    if prezzo_t < out["M"] * cfg.prezzo_minimo_reale:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(f"prezzo sotto il {int(cfg.prezzo_minimo_reale*100)}% del mercato: non è un prezzo vero "
                             "(rata, anticipo, errore o truffa)")
        return out
    if prezzo_t < out["M"] * limite and t.get("targa_e_telefono"):
        out["prezzo_molto_basso_ammesso"] = True
        out["ipotesi"].append("prezzo molto sotto il mercato: ammessa perché l'annuncio mostra il numero del venditore")
    elif prezzo_t < out["M"] * limite:
        out["categoria"] = "dati_insufficienti"
        out["motivi"].append(f"prezzo oltre il {int(round((1 - limite) * 100))}% sotto la mediana: il mercato "
                             "trovato non è affidabile (versione diversa, dati sbagliati o truffa)")
        return out
    if margine < soglia:
        sc = _sconto_da_trattare(t, prezzo_t, margine, soglia)
        if sc:
            # 6/11 (verifica imbuto): auto della fascia richiesta che arriva al margine minimo chiedendo al massimo
            # 500/700 € di sconto: va sul sito come "candidato" e il sito la mette in "Da trattare" (sito_regole
            # .sconto_trattativa ricontrolla tutto: fascia, annuncio recente, sconto massimo). Prima restava
            # "non_conveniente", non veniva mai pubblicata e la sezione "Da trattare" restava quasi vuota.
            out.update(categoria="candidato", da_trattare={"sconto": sc, "prezzo_obiettivo": prezzo_t - sc})
            out["motivi"].append(f"margine {margine} € sotto la soglia di {soglia} €: arriva al minimo con "
                                 f"{sc} € di sconto da chiedere al venditore")
            return out
        out["categoria"] = "non_conveniente"
        out["motivi"].append(f"margine {margine} € sotto la soglia di {soglia} €")
        return out
    dubbi = []
    if out.get("prezzo_molto_basso_ammesso"):
        dubbi.append("prezzo molto sotto il mercato (numero presente): controllare bene auto e documenti")
    if out.get("solo_ai"):
        dubbi.append("stima del modello AI senza abbastanza confronti diretti: verificare il prezzo")
    elif out.get("vr_solo_modello"):
        dubbi.append("valore reale del modello senza auto uguali in vendita: serve il controllo dell'AI")
    elif n_gem < cfg.n_minimo:
        dubbi.append(f"meno di {cfg.n_minimo} auto uguali in vendita: stima dal modello, da controllare")
    elif n_gem < cfg.n_preferito:                    # 6/11: anche col modello (prima solo con la mediana)
        dubbi.append(f"campione limitato ({n_gem} auto uguali)")
    if n_gem < cfg.n_minimo:
        out["serve_controllo_ai"] = True             # 0–1 gemelle: mai oltre "candidato", Jev/AI obbligatorio
    if out.get("pochi_confronti") and sel["livello"] == 3:
        dubbi.append("pochi confronti: ricerca allargata")
    if (out.get("ai") or {}).get("disaccordo"):
        dubbi.append("modello AI e stima del mercato non concordano: verificare versione e stato")
    if sel["livello"] >= 2:
        dubbi.append("confronti ampliati")
    if not t.get("gearbox") or not t.get("power_kw"):
        dubbi.append("motorizzazione o cambio non indicati nell'annuncio")
    if cs["danneggiata"]:
        dubbi.append("auto danneggiata: ripristino e manodopera da verificare")
    if cs["voci"].get("difetti"):
        dubbi.append("difetti scritti nell'annuncio: costo di riparazione stimato dall'AI, da verificare")
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


TRATTA_MAX = ((7000, 500), (10**9, 700))           # stesso default del sito (sito_regole.Regole.tratta_max)


def _sconto_da_trattare(t: dict, prezzo: int, margine: int, soglia: int) -> int | None:
    """Sconto (a passi di 50 €) con cui un'auto della fascia richiesta arriva al margine minimo, entro TRATTA_MAX."""
    if margine is None or margine <= 0 or prezzo <= 0 or t.get("supercar"):
        return None
    try:
        if not benzina_richiesta(t):
            return None
        from ..web import scovo
        if int(soglia) != scovo.soglia(prezzo):      # soglia diversa da quella del cliente (prove): niente trattativa
            return None
    except Exception:
        return None
    tetto = next(x for fino, x in TRATTA_MAX if prezzo < fino)
    for sc in range(50, tetto + 1, 50):
        if margine + sc >= scovo.soglia(prezzo - sc):
            return sc
    return None


def riassunto(v: dict) -> dict:
    """Versione compatta per l'elenco del sito (la valutazione completa resta nel database)."""
    keys = ("categoria", "M", "L", "n", "n_gemelle", "livello", "riduzione", "fascia", "R", "R_finale", "margine", "soglia",
            "totale_costi", "campione", "policy", "metodo", "M_fonte", "prudente_pochi", "pochi_confronti", "vr_accordo",
            "vr_poche_gemelle", "policy_rev", "regole", "R_da", "vr_solo_modello", "serve_controllo_ai",
            "costo_difetti_incluso", "motivo_esclusione", "fonte_valutazione", "da_trattare", "netto",
            "giorni_vendita_commercianti", "n_dati_rivendita", "dispersione", "R_commercianti")
    out = {k: v.get(k) for k in keys if v.get(k) is not None}
    if v.get("riserva"):
        out["riserva"] = v["riserva"]["euro"]
    rc = v.get("rivendita_commercianti")
    if isinstance(rc, dict):                          # compatto: serve al sito per il netto (sconto osservato)
        out["rivendita_commercianti"] = {k: rc.get(k) for k in ("R", "fonte", "n_venduti", "n_attivi", "sconto",
                                                                 "giorni_vendita")}
    if isinstance(v.get("allestimento"), dict):
        out["allestimento"] = {k: v["allestimento"].get(k) for k in ("nome", "livello")}
    return out
