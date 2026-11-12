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

VERSIONE = "regole-2026-11-12b"


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
    # 12/11 (rapporto 2 §4 E3, cliente "più preciso"): 2–7 gemelle con prezzi corretti molto dispersi (massimo/minimo
    # oltre 1,8) non dicono il prezzo: "dati_insufficienti", salvo un valore reale affidabile
    dispersione_max: float = 1.8
    dispersione_sotto_n: int = 8
    # 12/11 (rapporto 2 §4 E1): il giudice AI vede un campione di 40 confronti scelti per SOMIGLIANZA (prima i 40 più
    # economici: l'AI poteva togliere solo dal basso e la mediana poteva solo salire)
    giudice_campione: int = 40
    ampiezza_poche: bool = True        # 2–4 gemelle: errore dall'ampiezza (False = regola di prima, simulazioni)

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
    due_sensi: bool = False   # 4/11 00:40: spenta finché la prova sulle vendute vere (provacorrezione) non dice che è meglio
    tetto: float = .25
    svalutazione_anno: float = .08     # valori di riserva se il gruppo non permette la stima
    svalutazione_10km: float = .012
    stima_min_annunci: int = 15
    anno_min: float = .02              # stima accettata solo se plausibile
    anno_max: float = .25
    km_min: float = .008               # 12/11 (rapporto 2 E8): almeno 0,8% ogni 10.000 km (prima anche 0%)
    km_max: float = .05
    # 12/11 sera (margini fase 2, punto 3): pooling gerarchico dei tassi modello → marca → tutti. La stima del gruppo
    # (almeno pooling_n_min annunci) si restringe verso il tasso a priori con peso n / (n + pooling_k); a priori = la
    # marca (misurata su almeno pooling_marca_min annunci, calibra.py "tassi") → tutti → riserva. Sempre nei limiti.
    pooling: bool = True               # False = regola di prima (stima del gruppo o riserva)
    pooling_n_min: int = 5
    pooling_k: int = 40
    pooling_marca_min: int = 300
    # 12/11 sera (punto 2): correzione nei due sensi solo per auto da due_sensi_da € in su. SPENTA: alza M; si accende
    # solo se la prova sulle vendute (prova_correzione, prova alla cieca "tutte") dice che è più precisa
    due_sensi_alto: bool = False
    due_sensi_da: int = 20000


@dataclass(frozen=True)
class ModelloLocale:
    """Modello prezzi del gruppo (log-prezzo su anno, km, potenza) sui prezzi CHIESTI: alternativa alla mediana delle
    gemelle quando è preciso. Senza tetto (prima: mai oltre 1,10 × mediana)."""
    attivo: bool = True
    n_min: int = 8
    anni: int = 3                      # 6/11 (verifica prezzi): era 6, mescolava generazioni (430i F36 con G26)
    potenza: float = .10               # 6/11: era 0,25 (motori diversi nello stesso modello)
    errore_max: float = .18
    accordo: float = .10               # 6/11: con almeno n_minimo gemelle il modello vale solo se entro ±10% dalla
                                       # loro mediana; altrimenti si tiene la mediana delle gemelle


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
    gemelle e senza valore reale) dà una stima "candidato" sul prezzo chiesto, che il sito porta al prezzo di vendita.
    6/11: spento di default (era spento in produzione con POLICY_AI=0; il cron mercato non lo sapeva)."""
    attiva: bool = False
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
    italia: bool = True                # 6/11: default = produzione (POLICY_ITALIA=1 su sito e cron opportunità)
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
    # 12/11 (rapporto 2 E2): il fattore non salta più al confine delle fasce: si interpola (in scala log di M) tra i
    # centri delle fasce; fuori dal primo e dall'ultimo centro resta quello della fascia. Le fasce sono di M sia nella
    # misura (pricing/calibra.py, "fasce_su": "M") sia nell'applicazione.
    interpola: bool = True             # False = scalini per fascia (regola di prima, solo per le simulazioni)
    centri: tuple = (("<5k", 3500), ("5-10k", 7071), ("10-20k", 14142), ("20-35k", 26458), ("35-60k", 45826),
                     (">60k", 85000))
    # 12/11 sera (margini fase 2, punto 1): nel metodo "combinato" anche V (valore reale) si porta al venduto con il
    # fattore MISURATO sul valore reale (voci "vr:"): senza misura vale 1,0. Le gemelle lo erano già.
    vr_nel_combinato: bool = True
    # punto 2: fattore oltre 1,0 per le fasce di M da alza_da € in su, solo con misura per M su almeno alza_n_min
    # auto, al massimo alza_max. SPENTO (alza i prezzi): si accende solo dopo la prova alla cieca.
    alza: bool = False
    alza_da: int = 20000
    alza_n_min: int = 40
    alza_max: float = 1.05


@dataclass(frozen=True)
class Allestimento:
    """12/11: allestimento normalizzato (pricing/allestimento.py, livello 0–3) nella scelta dei confronti: prima lo
    stesso livello, poi ±1, poi gli sconosciuti, poi gli altri; i confronti di livello diverso si portano al livello
    dell'auto con `fattore` per livello (mai oltre il suo livello). Interruttore ALLESTIMENTO_CONFRONTI."""
    attivo: bool = True
    fattore: float = .04


@dataclass(frozen=True)
class Trazione:
    """12/11 sera (margini fase 2, punto 4): trazione "a livelli" (pricing/dotazioni.py: dati_auto della scheda tecnica,
    altrimenti il testo). Auto 4x4: solo confronti 4x4 se sono almeno n_minimo; auto non 4x4: via i confronti 4x4
    riconosciuti se ne restano almeno n_minimo; confronti 4x4 in maggioranza e auto senza indicazione: nessun filtro
    (modello quasi sempre 4x4). Non cambia prezzi, sceglie i confronti. Interruttore TRAZIONE_LIVELLI."""
    attiva: bool = True
    maggioranza: float = .5


@dataclass(frozen=True)
class Dotazioni:
    """12/11 sera (punto 4): aggiustamento per optional importanti (pelle, navigatore, tetto) e 4x4 con il premio
    MISURATO (calibra.py "dotazioni"), solo se noto su entrambe le auto. SPENTO finché la misura non c'è e la prova
    alla cieca non dice che serve. Interruttore DOTAZIONI_PREZZO."""
    aggiusta: bool = False
    n_min: int = 50                    # annunci con l'optional nella misura
    z_min: float = 2.0                 # premio oltre z_min errori standard
    tetto_voce: float = .04
    tetto_totale: float = .06


@dataclass(frozen=True)
class Forbice:
    """12/11 sera (punto 5): forbice della rivendita (P25–P75) e probabilità di vendere ad almeno R dai quantili di
    prezzo/M delle auto sparite (calibra.py). Solo informazione nella scheda: margine e soglia non cambiano."""
    attiva: bool = False               # 12/11 notte: il cliente non vuole la forbice nella scheda (spenta)
    prob_min: float = .05
    prob_max: float = .95


@dataclass(frozen=True)
class RivenditaCommercianti:
    """12/11 (cliente: "la rivendita deve venire dai dati veri dei commercianti"): R dai SOLI annunci di commercianti
    della stessa auto (pricing/rivendita.py). Venduti = spariti entro `venduta_entro` giorni dalla comparsa negli
    ultimi `giorni` giorni e mai riapparsi (ultimo prezzo); attivi = prezzo di oggi meno lo sconto medio osservato tra
    primo e ultimo prezzo dei venduti. Si usa con almeno `min_venduti` venduti o `min_attivi` attivi (con lo sconto
    misurato su almeno `min_sconti` venduti); altrimenti R = M come prima. Interruttore RIVENDITA_COMMERCIANTI."""
    attiva: bool = True
    giorni: int = 90
    venduta_entro: int = 60
    min_venduti: int = 5
    min_attivi: int = 8
    min_sconti: int = 5
    anni_tol: int = 2
    km_tol: int = 40000
    freschezza_giorni: int = 10        # attivi: visti negli ultimi giorni


@dataclass(frozen=True)
class Scrittura:
    """Guardia sulle scritture di valutazioni_policy (pricing/valutazione.py)."""
    ore_validita: int = 36             # oltre: si riscrive anche con meno confronti (mercato cambiato davvero)
    # 12/11 sera (rapporto 2 E5, punto 7): una valutazione passata dal giudice AI (confronti tolti) si scrive sempre,
    # anche con meno confronti; una NON giudicata non sostituisce una giudicata (finché vale la guardia)
    giudice_vince: bool = True


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
    allestimento: Allestimento = field(default_factory=Allestimento)
    rivendita: RivenditaCommercianti = field(default_factory=RivenditaCommercianti)
    scrittura: Scrittura = field(default_factory=Scrittura)
    trazione: Trazione = field(default_factory=Trazione)
    dotazioni: Dotazioni = field(default_factory=Dotazioni)
    forbice: Forbice = field(default_factory=Forbice)
    giudice_ai: bool = True            # l'AI toglie i confronti non uguali (6/11: default = produzione, così cron mercato,
                                       # cron opportunità e sito calcolano con le STESSE regole anche senza variabili)
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
    "ALLESTIMENTO_CONFRONTI": "allestimento.attivo",
    "RIVENDITA_COMMERCIANTI": "rivendita.attiva",
    # 12/11 sera, margini fase 2
    "TASSI_POOLING": "correzione.pooling",
    "CORREZIONE_DUE_SENSI_ALTO": "correzione.due_sensi_alto",
    "CALIBRA_VR_COMBINATO": "calibrazione.vr_nel_combinato",
    "CALIBRA_ALZA": "calibrazione.alza",
    "TRAZIONE_LIVELLI": "trazione.attiva",
    "DOTAZIONI_PREZZO": "dotazioni.aggiusta",
    "RIVENDITA_FORBICE": "forbice.attiva",
    "GIUDICE_VINCE": "scrittura.giudice_vince",
}

# Interruttori nuovi del 12/11 sera (margini fase 2) con il valore "prima" (comportamento di 4e26d24): la prova alla
# cieca (ai/prova_cieca.py) rifà M con queste regole per misurare prima/dopo sulle stesse auto.
NOVITA_FASE2 = {"correzione.pooling": False, "correzione.due_sensi_alto": False, "calibrazione.vr_nel_combinato": False,
                "calibrazione.alza": False, "trazione.attiva": False, "dotazioni.aggiusta": False}


def con_campi(r: "Regole", campi: dict) -> "Regole":
    """Copia di r con i campi "sezione.campo" (o "campo") cambiati: per prove e misure, mai per il sito."""
    for campo, val in campi.items():
        if "." in campo:
            sez, k = campo.split(".")
            r = dataclasses.replace(r, **{sez: dataclasses.replace(getattr(r, sez), **{k: val})})
        else:
            r = dataclasses.replace(r, **{campo: val})
    return r
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
        if environ is None:
            ign = sorted(k for k in env if k.startswith("POLICY_") and k not in INTERRUTTORI)
            if ign:                        # 6/11: chi le imposta su Render deve sapere che non contano
                log.warning("CONFIG ignorate (le regole sono in regole.py): %s", ", ".join(ign))
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


def fattori_calibrazione(misurate: dict | None, r: Regole | None = None, misurate_vr: dict | None = None,
                         per_M: bool = False) -> list:
    """[(limite, fascia, fattore)] dalla misura (dati->'fasce' dell'ultima riga di `calibrazione`): fattore misurato
    se la fascia ha almeno n_min auto, altrimenti la riserva; sempre tra minimo e massimo.
    12/11 (rapporto 2 E4): con misurate_vr (dati->'fasce_vr': prezzo delle auto vendute / M quando M viene dal valore
    reale) si aggiungono le voci (limite, "vr:<fascia>", fattore) SOLO per le fasce misurate su almeno n_min auto:
    il valore reale è già un prezzo di vendita, quindi senza misura non si corregge (nessuna riserva).
    per_M=True: la misura è per fascia di M (calibra.py dal 12/11, "fasce_su": "M"): voce (0, "_per_M", 1.0); senza
    questa voce l'interpolazione non può ALZARE il fattore rispetto alla fascia (misura vecchia, per prezzo)."""
    cal = (r or regole()).calibrazione
    riserva = dict(cal.riserva)
    out = []
    da = 0
    for lim, nome in cal.fasce:
        f = (misurate or {}).get(nome) or {}
        x = f.get("prezzo_su_M") if (f.get("n") or 0) >= cal.n_min else None
        x = riserva.get(nome, 1.0) if x is None else float(x)
        # 12/11 sera (punto 2, spento di default): sopra 1,0 solo fasce di M da alza_da, misura per M su alza_n_min auto
        alto = cal.alza_max if (cal.alza and per_M and da >= cal.alza_da and (f.get("n") or 0) >= cal.alza_n_min) \
            else cal.massimo
        out.append((lim, nome, max(cal.minimo, min(alto, float(x)))))
        da = lim
    for lim, nome in cal.fasce:
        f = (misurate_vr or {}).get(nome) or {}
        if (f.get("n") or 0) >= cal.n_min and f.get("prezzo_su_M") is not None:
            out.append((lim, "vr:" + nome, max(cal.minimo, min(cal.massimo, float(f["prezzo_su_M"])))))
    # 12/11 sera (punto 5): quantili 10/25/50/75/90 di prezzo/M delle sparite per fascia (forbice della rivendita),
    # voci "_q:" (M dai prezzi chiesti) e "_qvr:" (M dal valore reale); ignorate da fattore_per
    for pref, mis in (("_q:", misurate), ("_qvr:", misurate_vr)):
        for lim, nome in cal.fasce:
            q = quantili_di((mis or {}).get(nome), cal.n_min)
            if q:
                out.append((lim, pref + nome, q))
    if per_M:
        out.append((0, "_per_M", 1.0))
    return out


def quantili_di(f: dict | None, n_min: int) -> tuple | None:
    """(q10, q25, q50, q75, q90) di prezzo/M di una fascia misurata (calibra.py "q"), se misurata su n_min auto e
    crescente; altrimenti None."""
    if not isinstance(f, dict) or (f.get("n") or 0) < n_min:
        return None
    q = f.get("q")
    try:
        q = tuple(float(x) for x in q)
    except (TypeError, ValueError):
        return None
    if len(q) != 5 or any(x <= 0 for x in q) or any(b < a for a, b in zip(q, q[1:])):
        return None
    return q


def calibrazione_vecchia(dati: dict | None, r: Regole | None = None) -> bool:
    """La misura chiesto → venduto (dati dell'ultima riga di `calibrazione`) va rifatta (CALIBRA=1): fatta con una
    correzione anno/km diversa da quella in vigore, oppure (12/11) per fascia di prezzo e non di M."""
    if not dati:
        return False
    if dati.get("fasce") and dati.get("fasce_su") != "M":
        return True
    return bool(dati.get("correzione_due_sensi", False)) != bool((r or regole()).correzione.due_sensi)


def _fascia_di(M: float, r: Regole) -> str:
    return next((n for lim, n in r.calibrazione.fasce if M < lim), r.calibrazione.fasce[-1][1])


def _interpola(M: float, punti: list) -> float:
    """Interpolazione lineare in scala log di M tra punti (centro, fattore) ordinati; piatta fuori dagli estremi."""
    if M <= punti[0][0]:
        return punti[0][1]
    if M >= punti[-1][0]:
        return punti[-1][1]
    for (c0, f0), (c1, f1) in zip(punti, punti[1:]):
        if c0 <= M <= c1:
            w = (math.log(M) - math.log(c0)) / (math.log(c1) - math.log(c0))
            return f0 + w * (f1 - f0)
    return punti[-1][1]


def fattore_per(M: float, fattori: list | None, r: Regole | None = None, vr: bool = False) -> float:
    """Fattore chiesto → venduto per questo M (12/11, rapporto 2 E2): interpolato in scala log tra i centri delle
    fasce, niente scalini. Con una misura vecchia (per fascia di prezzo, senza la voce "_per_M") il fattore non può
    superare quello della sua fascia (l'interpolazione può solo abbassarlo finché non c'è la misura nuova).
    vr=True: solo le fasce MISURATE sul valore reale (voci "vr:"); al centro di una fascia non misurata vale 1,0, tra i
    centri si interpola (nessuna misura = 1,0 ovunque)."""
    r = r or regole()
    fattori = fattori if fattori is not None else fattori_calibrazione(None, r)
    M = float(M or 0)
    centri = dict(r.calibrazione.centri)
    ordine = [n for _, n in r.calibrazione.fasce]
    fascia = _fascia_di(M, r)
    if vr:
        mis = {str(n)[3:]: x for _, n, x in fattori if str(n).startswith("vr:")}
        if not mis:
            return 1.0
        # fasce misurate al loro fattore, le altre a 1,0 (nessuna correzione), interpolando in scala log tra i centri:
        # oltre il centro dell'ultima fascia misurata si torna gradualmente a 1,0 fino al centro della fascia vicina
        # (niente scalino al confine della fascia, ricontrollo del giudice M-a)
        punti = sorted((centri[n], mis.get(n, 1.0)) for n in ordine if n in centri)
        return _interpola(M, punti)
    per_nome = {str(n): x for _, n, x in fattori if not str(n).startswith(("vr:", "_"))}
    if not per_nome:
        return 1.0
    if not r.calibrazione.interpola:
        return per_nome.get(fascia, 1.0)
    punti = sorted((centri[n], x) for n, x in per_nome.items() if n in centri)
    f = _interpola(M, punti)
    if not any(str(n) == "_per_M" for _, n, _ in fattori) and fascia in per_nome:
        f = min(f, per_nome[fascia])
    return f


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
    volte). pv invariato se non va calibrato.
    12/11: fattore interpolato (fattore_per). M dal valore reale (metodo valore_reale / valore_reale_modello, mai il
    "combinato", dove le gemelle sono già portate al venduto) si corregge solo con il fattore MISURATO sul valore
    reale (voci "vr:", rapporto 2 E4). R = min(R, M × f, R_commercianti): la rivendita dei commercianti può solo
    confermare o abbassare R, mai alzarlo (cliente: "non gonfiare")."""
    r = r or regole()
    if not r.calibrazione.attiva or not isinstance(pv, dict) or not pv.get("M") or pv.get("calibrato") is not None:
        return pv
    vr = not da_calibrare(pv)
    if vr and not (pv.get("M_fonte") == "vendite" and pv.get("metodo") in ("valore_reale", "valore_reale_modello")
                   and any(str(n).startswith("vr:") for _, n, _ in (fattori or []))):
        return pv
    M = float(pv["M"])
    f = fattore_per(M, fattori, r, vr=vr)
    if f == 1:
        return dict(pv, calibrato=1.0)
    R = float(pv.get("R_finale") or pv.get("R") or M)
    # 12/11 sera: f > 1 solo con calibrazione.alza (spento di default) e solo per R uguale al mercato (mai sopra i
    # commercianti né sopra una R già abbassata da costi o difetti)
    limiti = ([M * f] if f > 1 and R >= M - .5 else [R, M * f]) + \
        ([float(pv["R_commercianti"])] if pv.get("R_commercianti") else [])
    R2 = min(limiti)
    giu = int(round(R - R2))
    out = dict(pv, M=int(round(M * f)), M_annunci=int(M), R=int(round(R2)), R_finale=int(round(R2)),
               calibrato=round(f, 3), M_fonte="vendite_calibrate" if not vr else "vendite")
    if pv.get("R_commercianti"):                       # chi fa davvero il minimo dopo la calibrazione
        Rc = float(pv["R_commercianti"])
        out["R_da"] = "commercianti" if Rc <= M * f and Rc <= R else "mercato vero (confermato dai commercianti)"
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
