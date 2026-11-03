"""Regole di prezzo e di valutazione di Scovo: UN solo modulo, versionato in git (fase 2 del piano, 4/11/2026).

Tutti i parametri che decidono M (mercato), R (rivendita), margine e categoria stanno qui, con i valori di produzione
del codice al 3/11 sera (tolti i resti delle regole prudenti, vedi sotto). `policy_v2`, `ricalcolo` e
`pricing/valutazione` li leggono da qui; NESSUNA soglia si legge più dall'ambiente.

Dall'ambiente si leggono SOLO gli interruttori elencati in `INTERRUTTORI` (on/off di un lavoro o di un passo, mai un
numero). Ogni interruttore impostato finisce nel log (`CONFIG override`) e nell'impronta, così sito e cron con
impostazioni diverse si riconoscono subito (CONFIG_IMPRONTA di regole_log e colonna impronta_regole delle valutazioni).

Regole fisse del cliente (3/11, non negoziabili):
- rivendita R = prezzo VERO di vendita del mercato (non il prezzo chiesto, non un prezzo prudente), R = M una volta;
- margine = R − prezzo dell'annuncio − costi veri. NESSUNO sconto di trattativa sul prezzo d'acquisto;
- costi fissi al massimo 200 € in tutto (COSTI_FISSI_MAX); i costi di ricambi/riparazione letti dall'AI restano;
- margine minimo = scovo.soglia(prezzo) (tabella SOGLIE_PV, che qui non si tocca);
- al massimo 150.000 km, anno dal 2007, auto senza km escluse.

Tolti il 4/11 (erano prudenze non misurate che potevano solo ABBASSARE M o R): tabella delle riduzioni, tetti L/P25,
"vendita veloce", rivendita prudente con pochi confronti, min() tra AI / valore reale / gemelle, taratura (correggeva
verso i prezzi CHIESTI, studio 1 §1.3), tetto 1,10 × mediana del modello locale, tetto manuale della rivendita.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass, field

log = logging.getLogger("config")

VERSIONE = "regole-2026-11-04a"


@dataclass(frozen=True)
class Confronti:
    """Quali annunci sono "la stessa auto" (gemelle) e quando si allarga la ricerca."""
    anni_tol: int = 3
    km_tol: int = 50000
    potenza_tol: float = .10
    amplia: bool = True                # livello 2: anno ±4, km ±70.000, cambio/potenza anche non indicati
    anni_tol_2: int = 4
    km_tol_2: int = 70000
    livello_3: bool = True             # livello 3: anno +1 e km +30.000 oltre il 2 (solo se i primi non bastano)
    freschezza_giorni: int = 10
    prezzo_min: int = 500
    fuori_scala: float = 2.5
    n_preferito: int = 5               # sotto: ripiego sul Nord e campione "provvisorio"
    n_minimo: int = 2
    # minimo che il SITO chiede (si allarga finché non si arriva qui): 8 sotto 10.000 €, 5 sopra
    min_sito: int = 5
    min_eco: int = 8
    eco_sotto: int = 10000
    allestimento: bool = True          # sportivo/normale separati se gli uguali sono almeno n_minimo
    trazione: bool = False             # 4x4 con 4x4 (spento)
    motore_senza_kw: bool = True       # senza kW il motore si riconosce dalla cilindrata scritta

    def serve(self, prezzo) -> int:
        return max(self.n_minimo, self.min_eco if (prezzo or 0) < self.eco_sotto else self.min_sito)


@dataclass(frozen=True)
class Correzione:
    """Prezzo di ogni gemella portato all'anno e ai km dell'auto valutata.
    due_sensi=True (4/11, default): un confronto più nuovo scende, uno più vecchio SALE, entrambi con lo stesso tetto
    ±25%. due_sensi=False: comportamento di prima (pv6: si abbassa e basta, min(p_corretto, p)), che per costruzione
    porta M sotto il vero. Interruttore CORREZIONE_DUE_SENSI=0 per tornare indietro; la prova sulle auto vendute
    (python -m dealfinder.pricing.prova_correzione) confronta le due regole: se la nuova risulta più distorta, si
    rimette il default a False qui."""
    attiva: bool = True
    due_sensi: bool = True
    tetto: float = .25
    svalutazione_anno: float = .08     # valori di riserva se il gruppo non permette la stima
    svalutazione_10km: float = .012
    stima_min_annunci: int = 15
    anno_min: float = .02              # stima accettata solo se plausibile
    anno_max: float = .25
    km_min: float = 0.0
    km_max: float = .05


@dataclass(frozen=True)
class ModelloLocale:
    """Modello prezzi del gruppo (log-prezzo su anno, km, potenza) sui prezzi CHIESTI: alternativa alla mediana delle
    gemelle quando è preciso. Senza tetto (prima: mai oltre 1,10 × mediana)."""
    attivo: bool = True
    n_min: int = 8
    anni: int = 6
    potenza: float = .25
    errore_max: float = .18


@dataclass(frozen=True)
class ValoreReale:
    """Valore reale V (pricing/valore_reale.py): modello su annunci e auto VENDUTE del gruppo, prezzo di vendita.
    Stima principale fino a prezzo_max (prova del 2/11: migliore delle gemelle sotto 20.000 €, peggiore sopra)."""
    attivo: bool = True
    prezzo_max: int = 20000
    accordo: float = .08               # gemelle (portate al prezzo di vendita) entro ±8% da V: M = V
    errore_min: float = .05            # errore minimo attribuito a ciascuna stima nella media pesata
    # "poche gemelle" (2 ≤ gemelle < minimo del sito): il modello garantisce il numero se preciso e su un gruppo grande
    poche_sigma_max: float = .15
    poche_gruppo_min: int = 100
    poche_diff_max: float = .15
    # 0–1 gemelle: M = V solo se il gruppo ha abbastanza auto VENDUTE e il modello è preciso; mai oltre "candidato"
    senza_gemelle_vendute_min: int = 20
    senza_gemelle_sigma_max: float = .15
    max_giorni_avviso: int = 3         # V più vecchio: si usa lo stesso, con un avviso


@dataclass(frozen=True)
class StimaAI:
    """Modello AI dei prezzi (stima_ai, prezzi CHIESTI): solo segnale di disaccordo con le gemelle; da solo (0–1
    gemelle e senza valore reale) dà una stima "candidato" sul prezzo chiesto, che il sito porta al prezzo di vendita."""
    attiva: bool = True
    errore_max: float = .15
    n_min: int = 8
    accordo: float = .08
    disaccordo: float = .30


@dataclass(frozen=True)
class Costi:
    """Costi veri del commerciante. Le voci fisse insieme non superano mai fissi_max (cliente 3/11 23:23)."""
    fissi_max: int = 200
    passaggio: int = 0
    pulizia: int = 0
    trasporto: int = 0
    diagnosi_revisione: int = 0
    vendita: int = 0
    costo_fisso: int = 0
    costo_fisso_alto: int = 0          # mai deciso dal cliente (3/11): 0
    costo_alto_da: int = 20000
    manodopera_su_ricambi: float = 0.0


@dataclass(frozen=True)
class AntiTruffa:
    sotto_m: float = .55
    basse: float = .45
    basse_max: int = 3000
    basse_confronti: int = 5
    minimo_reale: float = .20
    basso_solo_telefono: bool = True   # basta il numero (non serve la targa) per tenere un prezzo molto basso
    eccezione_solo_privati: bool = True  # un commerciante con un prezzo molto basso non è mai ammesso (prezzo esca)


@dataclass(frozen=True)
class Ammissione:
    km_max: int = 150000
    anno_min: int = 2007
    km_obbligatori: bool = True
    telefono_obbligatorio: bool = True  # telefono_ok=False e nessun numero nel testo: esclusa
    # solo_sane: fuori le auto con danno strutturale/grave, airbag, guasti gravi o danni dichiarati NON ancora stimati.
    # Le auto con danni dichiarati e costo di riparazione stimato (ricambi o lettura AI dei difetti) si valutano col
    # costo tolto dal margine (sezione Incidentate, decisione del 4/11).
    solo_sane: bool = True
    perdita_storico_danno: float = 0.0  # non misurata: 0 (prima 8%, solo con solo_sane=False)
    # fascia più richiesta (cliente 3/11 22:54): cambia CHI entra (commercianti della zona), MAI il prezzo
    richiesta_prezzo_max: int = 10000
    richiesta_km_max: int = 100000
    richiesta_diesel_dal: int = 2017
    richiesta_carburanti: tuple = ("benzin", "ibrid", "hybrid", "gpl", "lpg", "metano")


@dataclass(frozen=True)
class Zona:
    ripiego_nord: bool = True
    italia: bool = False
    italia_da: int = 50000
    italia_pmin: int = 25000


@dataclass(frozen=True)
class Calibrazione:
    """Prezzo CHIESTO → prezzo di VENDITA, per fascia di M (tabella calibrazione, misura sulle auto vendute).
    Si applica UNA volta e solo quando M viene da prezzi chiesti (M_fonte="annunci"), mai sul valore reale."""
    attiva: bool = True
    n_min: int = 20
    minimo: float = .80
    massimo: float = 1.0
    # fasce con meno di n_min auto misurate: valori di riserva (misura del 28/10); sopra 20.000 € nessuna riserva (1,0)
    riserva: tuple = (("<5k", .87), ("5-10k", .915), ("10-20k", .97))
    fasce: tuple = ((5000, "<5k"), (10000, "5-10k"), (20000, "10-20k"), (35000, "20-35k"), (60000, "35-60k"),
                    (10**9, ">60k"))


@dataclass(frozen=True)
class Scrittura:
    """Guardia sulle scritture di valutazioni_policy (pricing/valutazione.py)."""
    ore_validita: int = 36             # oltre: si riscrive anche con meno confronti (mercato cambiato davvero)


@dataclass(frozen=True)
class Regole:
    versione: str = VERSIONE
    confronti: Confronti = field(default_factory=Confronti)
    correzione: Correzione = field(default_factory=Correzione)
    modello_locale: ModelloLocale = field(default_factory=ModelloLocale)
    valore_reale: ValoreReale = field(default_factory=ValoreReale)
    ai: StimaAI = field(default_factory=StimaAI)
    costi: Costi = field(default_factory=Costi)
    antitruffa: AntiTruffa = field(default_factory=AntiTruffa)
    ammissione: Ammissione = field(default_factory=Ammissione)
    zona: Zona = field(default_factory=Zona)
    calibrazione: Calibrazione = field(default_factory=Calibrazione)
    scrittura: Scrittura = field(default_factory=Scrittura)
    giudice_ai: bool = False           # l'AI toglie i confronti non uguali (lavoro con chiave e budget)
    fascia_richiesta: bool = True


# Interruttori ammessi dall'ambiente: nome → (sezione.campo). SOLO booleani. Nessun'altra lettura dall'ambiente.
INTERRUTTORI = {
    "CORREZIONE_DUE_SENSI": "correzione.due_sensi",
    "VR_ATTIVO": "valore_reale.attivo",
    "POLICY_AI": "ai.attiva",
    "POLICY_GIUDICE_AI": "giudice_ai",
    "POLICY_RIPIEGO_NORD": "zona.ripiego_nord",
    "POLICY_ITALIA": "zona.italia",
    "BENZ_RICHIESTE": "fascia_richiesta",
    "MERCATO_CALIBRATO": "calibrazione.attiva",
}
_VERO = {"1", "true", "si", "sì", "yes", "on"}
_FALSO = {"0", "false", "no", "off"}


def _leggi_interruttori(env) -> dict:
    out = {}
    for nome, campo in INTERRUTTORI.items():
        v = env.get(nome)
        if v is None or str(v).strip() == "":
            continue
        s = str(v).strip().lower()
        if s in _VERO:
            out[campo] = True
        elif s in _FALSO:
            out[campo] = False
        else:
            log.warning("CONFIG interruttore %s=%r non valido (ammessi 1/0): vale il default", nome, v)
    return out


_CACHE: dict = {}


def regole(environ=None) -> Regole:
    """Le regole in vigore: i default di questo file più gli interruttori dell'ambiente (letti a ogni chiamata, così un
    test o un lavoro che cambia un interruttore lo vede; il risultato è in cache per gli stessi interruttori)."""
    env = os.environ if environ is None else environ
    over = _leggi_interruttori(env)
    chiave = tuple(sorted(over.items()))
    r = _CACHE.get(chiave)
    if r is None:
        r = Regole()
        for campo, val in over.items():
            if "." in campo:
                sez, k = campo.split(".")
                r = dataclasses.replace(r, **{sez: dataclasses.replace(getattr(r, sez), **{k: val})})
            else:
                r = dataclasses.replace(r, **{campo: val})
        if over and environ is None:
            log.warning("CONFIG override %s", json.dumps({k: v for k, v in sorted(over.items())}))
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[chiave] = r
    return r


def come_dict(r: Regole | None = None) -> dict:
    return json.loads(json.dumps(dataclasses.asdict(r or regole()), sort_keys=True, default=str))


def impronta(r: Regole | None = None, environ=None) -> str:
    """sha256 (12 cifre) delle regole effettive + tabella soglie SOGLIE_PV: cambia se cambia qualsiasi valore."""
    r = r or regole(environ)
    dati = come_dict(r)
    try:
        from .web import scovo
        dati["soglie_pv"] = [list(x) for x in scovo.SOGLIE_PV]
    except Exception:                       # import del sito non disponibile: impronta delle sole regole
        pass
    canon = json.dumps(dati, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- funzioni di regola (pure)

def benzina_richiesta(t: dict, r: Regole | None = None) -> bool:
    """Fascia più richiesta: sotto 10.000 € e 100.000 km, benzina/ibride/GPL/metano o diesel dal 2017.
    Decide solo l'AMMISSIONE (es. commercianti della zona), mai il prezzo di rivendita."""
    r = r or regole()
    a = r.ammissione
    if not r.fascia_richiesta:
        return False
    km = t.get("mileage_km", t.get("km"))
    if not (0 < float(t.get("price_eur") or t.get("prezzo") or 0) < a.richiesta_prezzo_max
            and km is not None and int(km) < a.richiesta_km_max):
        return False
    f = str(t.get("fuel") or t.get("carb") or "").lower()
    if any(x in f for x in a.richiesta_carburanti):
        return True
    anno = t.get("year", t.get("anno"))
    return "diesel" in f and anno is not None and int(anno) >= a.richiesta_diesel_dal


def fattore_correzione(anni_piu_nuovo: float, km_in_meno_10k: float, ya: float, ka: float, c: Correzione) -> float:
    """Fattore per portare il prezzo di un confronto all'auto valutata.
    anni_piu_nuovo = anno confronto − anno auto; km_in_meno_10k = (km auto − km confronto)/10.000.
    Nei due sensi con tetto ±tetto; con due_sensi=False solo verso il basso (regola di prima)."""
    f = (1 - ya) ** anni_piu_nuovo * (1 - ka) ** km_in_meno_10k
    if c.due_sensi:
        return max(1 - c.tetto, min(1 + c.tetto, f))
    return min(f, 1.0)


def fattori_calibrazione(misurate: dict | None, r: Regole | None = None) -> list:
    """[(limite, fascia, fattore)] dalla misura (dati->'fasce' dell'ultima riga di `calibrazione`): fattore misurato
    se la fascia ha almeno n_min auto, altrimenti la riserva; sempre tra minimo e massimo."""
    cal = (r or regole()).calibrazione
    riserva = dict(cal.riserva)
    out = []
    for lim, nome in cal.fasce:
        f = (misurate or {}).get(nome) or {}
        x = f.get("prezzo_su_M") if (f.get("n") or 0) >= cal.n_min else None
        x = riserva.get(nome, 1.0) if x is None else float(x)
        out.append((lim, nome, max(cal.minimo, min(cal.massimo, float(x)))))
    return out


def calibrazione_vecchia(dati: dict | None, r: Regole | None = None) -> bool:
    """La misura chiesto → venduto (dati dell'ultima riga di `calibrazione`) è stata fatta con una correzione anno/km
    diversa da quella in vigore (o prima del 4/11, quando si correggeva solo verso il basso): va rimisurata (CALIBRA=1)."""
    if not dati:
        return False
    return bool(dati.get("correzione_due_sensi", False)) != bool((r or regole()).correzione.due_sensi)


def fattore_per(M: float, fattori: list | None, r: Regole | None = None) -> float:
    fattori = fattori if fattori is not None else fattori_calibrazione(None, r)
    return next((x for lim, _, x in fattori if M < lim), 1.0)


def da_calibrare(pv: dict) -> bool:
    """Il mercato M di questa valutazione viene da prezzi CHIESTI e non è ancora stato portato al prezzo di vendita."""
    if not isinstance(pv, dict) or not pv.get("M") or pv.get("calibrato") is not None:
        return False
    fonte = pv.get("M_fonte")
    if fonte is not None:
        return fonte == "annunci"
    return not str(pv.get("metodo") or "").startswith("valore_reale")        # valutazioni salvate prima del 4/11


def calibra_vendita(pv: dict, fattori: list | None, r: Regole | None = None) -> dict:
    """UNICA correzione prezzo chiesto → prezzo di vendita, per il sito (_mercato_calibrato). Restituisce una COPIA di
    pv con M, R, R_finale e margine portati al prezzo di vendita, e `calibrato` = fattore (così non si applica due
    volte). Nessun'altra riduzione (niente ×0,95, niente "metà correzione"). pv invariato se non va calibrato."""
    r = r or regole()
    if not r.calibrazione.attiva or not da_calibrare(pv):
        return pv
    M = float(pv["M"])
    f = fattore_per(M, fattori, r)
    if f >= 1:
        return dict(pv, calibrato=1.0)
    R = float(pv.get("R_finale") or pv.get("R") or M)
    R2 = min(R, M * f)
    giu = int(round(R - R2))
    out = dict(pv, M=int(round(M * f)), M_annunci=int(M), R=int(round(R2)), R_finale=int(round(R2)),
               calibrato=round(f, 3), M_fonte="vendite_calibrate")
    if pv.get("margine") is not None:
        out["margine"] = int(pv["margine"]) - giu
    return out


def media_pesata_log(stime: list[tuple[float, float]], errore_min: float) -> tuple[float, list[float]]:
    """Media pesata con l'inverso della varianza, in scala logaritmica: stime = [(valore, errore tipico in log)].
    log M = Σ w_i·log x_i / Σ w_i, con w_i = 1/max(σ_i, errore_min)². Restituisce (M, pesi normalizzati)."""
    w = [1.0 / max(float(s), errore_min) ** 2 for _, s in stime]
    tot = sum(w)
    lm = sum(wi * math.log(float(x)) for wi, (x, _) in zip(w, stime)) / tot
    return math.exp(lm), [wi / tot for wi in w]
