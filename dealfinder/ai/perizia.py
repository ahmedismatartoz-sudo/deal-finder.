"""Perizia AI "da commerciante" (12/11, cliente: "ChatGPT valuta meglio di noi").

Per ogni auto candidata al sito, PRIMA che venga mostrata, l'AI Claude già configurata (ai/client.py: stesso client,
stessa chiave, modello veloce, tetto di spesa AI_BUDGET_GIORNO) fa da perito/commerciante: legge l'annuncio e i 10–15
confronti veri più simili (attivi e venduti, con i giorni online) e risponde con un JSON a schema rigido:

    forbice del prezzo di vendita realistico (basso/medio/alto), prezzo d'acquisto massimo per un commerciante,
    giorni di vendita stimati, rischi, giudizio "affare | nella media | caro | da verificare" e motivazione breve.

La risposta si valida (valida()): se lo schema non torna la perizia NON si salva (si riprova in un giro successivo).
Le perizie restano in tabella perizia_ai (migrazione 045) per auto + prezzo: stessa auto allo stesso prezzo = nessuna
nuova chiamata.

Il prompt NON è una seconda soglia di margine: chiede solo il valore di rivendita realistico (forbice) e un giudizio;
costi e margine minimo (SOGLIE_PV) restano quelli del sito.

Regole sul sito (web/sito_regole.py, interruttore PERIZIA_AI):
  "0"   spenta;
  "log" (default) fase ombra: le perizie si fanno, il SITO_FUNNEL scrive perizia_ombra (sarebbero_caro,
        sarebbero_attesa, sarebbero_prudenti, ...) ma nessuna auto viene tolta, messa in attesa o cambiata;
  "1"   applicata:
  - giudizio "caro" (non per le auto "Da trattare") → l'auto non entra
  - stima media sotto R di oltre il 10%      → entra con l'avviso "Verificare prima di pagare" e il margine usa la stima
                                               del perito (più prudente); se così scende sotto il minimo, esce
  - nessuna perizia (AI ferma, budget finito, tetto PERIZIA_MAX_GIORNO) e nessuna decisione Jev valida
                                             → l'auto NON passa pulita: resta in attesa (chiude il buco di Jev fermo)

Il giro gira in sottofondo nel servizio web (web/app.py, come Jev): pochi elementi per giro (PERIZIA_PER_GIRO),
niente foto, pausa di un'ora su errori di credito o di limite.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone

from . import client as AI

log = logging.getLogger("perizia")

GIUDIZI = ("affare", "nella media", "caro", "da verificare")
TIPI_RISCHIO = ("versione_dubbia", "km_sospetti", "difetti_dichiarati", "prezzo_troppo_basso", "possibile_truffa", "altro")
GRAVITA = ("bassa", "media", "alta")
MAX_CONFRONTI = 15

SISTEMA = """Sei un perito e commerciante di auto usate italiano con vent'anni di mestiere: compri da privati e da altri
commercianti, prepari l'auto (tagliando, pulizia, piccoli ritocchi) e la rivendi ai privati dal tuo piazzale con
12 mesi di garanzia. Ti pagano per una perizia onesta: un errore in alto ti fa comprare un'auto che resta ferma
mesi, un errore in basso ti fa perdere affari. Non gonfiare mai i prezzi.

TI DO
- l'auto da valutare (marca, modello, versione, anno, km, carburante, cambio, potenza, prezzo chiesto, testo
  dell'annuncio tagliato);
- fino a 15 annunci VERI dello stesso modello, i più simili che abbiamo, con prezzo, anno, km, versione, venditore,
  stato ("sparito (probabilmente venduto)" = annuncio tolto dopo N giorni online; "attivo" = ancora in vendita da N
  giorni).

COME RAGIONI (da commerciante, non da calcolatrice)
1. Leggi bene i confronti. Un annuncio SPARITO in pochi giorni (probabilmente venduto) dice il prezzo vero; un annuncio ATTIVO da molte
   settimane è un prezzo chiesto troppo alto, non un prezzo di mercato. I commercianti chiedono di solito il 5-10%
   più dei privati (garanzia, preparazione). Porta i confronti all'anno e ai km di questa auto: ogni anno e ogni
   20.000 km in più pesano. Tieni conto della versione: allestimenti sportivi o ricchi, potenza, cambio automatico,
   ibrido cambiano il valore; un confronto di un'altra versione vale poco.
2. "Prezzo di vendita realistico" = il prezzo a cui un commerciante RIVENDE davvero questa auto a un privato entro
   circa 60 giorni, dopo una preparazione normale, con garanzia. Non il prezzo chiesto dagli annunci. Dai una
   forbice: basso (vendita veloce o trattativa dura), medio (il più probabile), alto (cliente giusto, auto perfetta).
3. Il guadagno del commerciante NON lo calcoli tu: costi e margine minimo li applica il nostro sito con le sue
   regole. "acquisto_max" è solo un'indicazione: il prezzo oltre il quale, secondo te, comprare questa auto per
   rivenderla non ha più senso (non applicare soglie di guadagno tue; se non sai dirlo, null).
4. Tempi di vendita: quanti giorni servono al commerciante per rivenderla al prezzo medio, guardando quanto ci
   hanno messo gli annunci spariti simili.
5. Rischi: segnala solo ciò che vedi nei dati, con una nota breve e concreta:
   versione_dubbia (versione non chiara o confronti di un'altra versione), km_sospetti (troppo pochi per l'anno,
   cifre tonde o finte, in contraddizione col testo), difetti_dichiarati (guasti, spie, danni, "da sistemare"),
   prezzo_troppo_basso (molto sotto gli annunci spariti senza un motivo scritto), possibile_truffa (caparra, spedizione,
   auto o venditore all'estero, testo generico o copiato), altro.
6. Giudizio sul PREZZO CHIESTO rispetto al valore di rivendita realistico:
   "affare"        = prezzo chiesto ben sotto il valore di rivendita e niente rischi seri;
   "nella media"   = prezzo chiesto sotto il valore di rivendita, con uno spazio normale o stretto;
   "caro"          = prezzo chiesto pari o sopra il valore di rivendita medio: non c'è spazio per rivenderla;
   "da verificare" = i dati non bastano, oppure c'è un rischio serio (truffa, km, difetti) da controllare dal vivo.

REGOLE FERREE
- Non inventare nulla: niente optional, storia o difetti che non sono scritti. Usa solo i dati che ti do e la tua
  esperienza di mercato italiano per leggerli.
- Se i confronti sono pochi (meno di 4), troppo diversi o i dati dell'auto mancano, dillo: "dati_sufficienti":
  false, giudizio "da verificare", e una forbice larga o null.
- Se il prezzo chiesto non è indicato (prova alla cieca) valuta lo stesso la forbice; il giudizio sarà
  "da verificare" e nella motivazione non parlare del prezzo chiesto.
- Cifre in euro intere. basso <= medio <= alto. L'acquisto massimo non supera il medio.
- Scrivi in italiano semplice, motivazione al massimo 2 frasi (300 caratteri), ogni nota al massimo 160 caratteri.

RISPONDI SOLO CON QUESTO JSON, niente altro testo:
{"dati_sufficienti": true|false,
 "prezzo_vendita": {"basso": int, "medio": int, "alto": int} | null,
 "acquisto_max": int | null,
 "giorni_vendita": {"min": int, "max": int} | null,
 "rischi": [{"tipo": "versione_dubbia"|"km_sospetti"|"difetti_dichiarati"|"prezzo_troppo_basso"|"possibile_truffa"|"altro",
             "gravita": "bassa"|"media"|"alta", "nota": str}],
 "giudizio": "affare"|"nella media"|"caro"|"da verificare",
 "motivazione": str}"""


class PeriziaNonValida(ValueError):
    pass


# ------------------------------------------------------------------ input
_RE_TEL = None


def _senza_numeri(testo: str) -> str:
    """Il numero del venditore non va all'AI (né nei log)."""
    global _RE_TEL
    try:
        from ..web import scovo
        testo = scovo.RE_MOBILE.sub("[numero]", testo)
    except Exception:
        pass
    if _RE_TEL is None:
        _RE_TEL = re.compile(r"(?<!\d)(?:\+?\d[\s.\-/]?){8,13}\d(?!\d)")
    testo = _RE_TEL.sub("[numero]", testo)
    return re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", testo)


def _testo(s, n: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _vicinanza(auto: dict, c: dict) -> float:
    """Più basso = più simile: anno, km, potenza, cambio, versione."""
    d = 0.0
    try:
        d += abs(int(c.get("anno") or 0) - int(auto.get("year") or 0)) if c.get("anno") and auto.get("year") else 1.5
    except (TypeError, ValueError):
        d += 1.5
    try:
        d += abs(int(c.get("km") or 0) - int(auto.get("mileage_km") or 0)) / 25000 if c.get("km") is not None and auto.get("mileage_km") is not None else 1
    except (TypeError, ValueError):
        d += 1
    kw, ckw = auto.get("power_kw"), c.get("kw")
    if kw and ckw:
        try:
            if abs(float(ckw) - float(kw)) > 0.1 * float(kw):
                d += 1
        except (TypeError, ValueError):
            pass
    if auto.get("gearbox") and c.get("cambio") and str(auto["gearbox"])[:4].lower() != str(c["cambio"])[:4].lower():
        d += 0.7
    v1 = set(re.findall(r"[a-z0-9.]+", str(auto.get("version_raw") or "").lower()))
    v2 = set(re.findall(r"[a-z0-9.]+", str(c.get("versione") or "").lower()))
    if v1 and v2:
        d += 0.5 * (1 - len(v1 & v2) / max(len(v1 | v2), 1))
    return d


def scegli_confronti(auto: dict, confronti: list[dict], n: int = MAX_CONFRONTI) -> list[dict]:
    """I confronti più simili (mai l'auto stessa), al massimo n."""
    validi = [c for c in (confronti or []) if c and c.get("prezzo") and c.get("id") != auto.get("id")]
    validi.sort(key=lambda c: (_vicinanza(auto, c), c.get("prezzo") or 0))
    return validi[:n]


def _giorni(a, b) -> int | None:
    try:
        return max(0, int((b - a).total_seconds() // 86400))
    except Exception:
        return None


def arricchisci_confronti(conn, confronti: list[dict], ora=None) -> list[dict]:
    """Stato vero (venduto/attivo), giorni online e ultimo prezzo dei confronti, dalla tabella listings."""
    ids = [int(c["id"]) for c in confronti if c.get("id") is not None]
    if not ids:
        return confronti
    ora = ora or datetime.now(timezone.utc)
    righe = {r["id"]: r for r in conn.execute(
        "SELECT id, status, price_eur, first_seen_at, disappeared_at, seller_type FROM listings WHERE id = ANY(%s)",
        (ids,)).fetchall()}
    out = []
    for c in confronti:
        r = righe.get(c.get("id"))
        c = dict(c)
        if r:
            venduto = r.get("status") == "scomparso"
            c["stato"] = "venduto" if venduto else ("attivo" if r.get("status") == "attivo" else r.get("status"))
            c["giorni"] = _giorni(r.get("first_seen_at"), (r.get("disappeared_at") if venduto else None) or ora)
            if r.get("price_eur"):
                c["prezzo"] = r["price_eur"]           # ultimo prezzo (per le vendute: quello a cui è sparita)
            c["venditore"] = c.get("venditore") or r.get("seller_type")
        out.append(c)
    return out


_RE_PREZZO = re.compile(
    r"(?i)(?:€|eur(?:o)?)\s*\d[\d.,\s]{2,9}\d|\d[\d.,\s]{2,9}\d\s*(?:€|eur(?:o|i)?\b|mila\b)"
    r"|\b\d{1,3}(?:[.,]\d{1,3})?\s*mila\b(?:\s*(?:€|euro))?"
    r"|\b(?:prezzo|chiedo|richiesta|trattabil\w*|vendo\s+a|a\s+soli)\W{0,4}(?:\w+\W+){0,2}\d[\d.,]{2,8}")


def senza_prezzi(testo: str) -> str:
    """Prova alla cieca: anche nel testo dell'annuncio il prezzo si nasconde."""
    return _RE_PREZZO.sub("[prezzo]", testo or "")


def testo_richiesta(auto: dict, confronti: list[dict], cieca: bool = False) -> str:
    descr = _testo(_senza_numeri(f"{auto.get('title') or ''}. {auto.get('description') or ''}"[:6000]), 1500)
    if cieca:
        descr = senza_prezzi(descr)
    prezzo = "NON INDICATO (prova alla cieca)" if cieca else (f"{int(auto['price_eur']):,} €".replace(",", ".")
                                                              if auto.get("price_eur") else "non indicato")
    righe = [
        "AUTO DA VALUTARE",
        f"marca: {auto.get('make') or '-'} | modello: {auto.get('model') or '-'} | versione: {_testo(auto.get('version_raw'), 80) or '-'}",
        f"anno: {auto.get('year') or '-'} | km: {auto.get('mileage_km') if auto.get('mileage_km') is not None else '-'} | "
        f"carburante: {auto.get('fuel') or '-'} | cambio: {auto.get('gearbox') or '-'} | potenza: "
        f"{str(auto.get('power_kw')) + ' kW' if auto.get('power_kw') else '-'}",
        f"venditore: {auto.get('seller_type') or 'non indicato'} | prezzo chiesto: {prezzo}",
        f"testo dell'annuncio (tagliato): {descr or '-'}",
        "",
        f"CONFRONTI ({len(confronti)} annunci veri, i più simili)" if confronti else "CONFRONTI: nessuno",
    ]
    for i, c in enumerate(confronti, 1):
        stato = c.get("stato") or "?"
        giorni = c.get("giorni")
        st = (f"sparito (probabilmente venduto) dopo {giorni} giorni" if stato == "venduto" else f"attivo da {giorni} giorni"
              if stato == "attivo" else stato) if giorni is not None else stato
        righe.append(f"{i}. {c.get('prezzo')} € | anno {c.get('anno') or '?'} | km {c.get('km') if c.get('km') is not None else '?'} | "
                     f"{_testo(c.get('versione'), 40) or 'versione ?'} | cambio {c.get('cambio') or '?'} | "
                     f"kW {c.get('kw') or '?'} | {c.get('venditore') or '?'} | {st}")
    return "\n".join(righe)


# ------------------------------------------------------------------ schema
def _intero(v, campo: str, nullo: bool = False) -> int | None:
    if v is None:
        if nullo:
            return None
        raise PeriziaNonValida(f"{campo} mancante")
    if isinstance(v, bool):
        raise PeriziaNonValida(f"{campo} non numerico")
    if isinstance(v, str):
        s = re.sub(r"[€\s]", "", v)
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
        v = s.replace(",", ".")
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        raise PeriziaNonValida(f"{campo} non numerico")


def _giudizio(v) -> str:
    g = re.sub(r"[_\s]+", " ", str(v or "")).strip().lower()
    if g not in GIUDIZI:
        raise PeriziaNonValida(f"giudizio non ammesso: {str(v)[:30]}")
    return g


def valida(d, cieca: bool = False, prezzo=None) -> dict:
    """Schema rigido. Ritorna la perizia normalizzata o solleva PeriziaNonValida. `prezzo` = prezzo chiesto: "caro" non
    è ammesso se l'acquisto massimo indicato arriva al prezzo chiesto, o se il prezzo è sotto la forbice."""
    if not isinstance(d, dict):
        raise PeriziaNonValida("non è un oggetto JSON")
    chiavi = {"dati_sufficienti", "prezzo_vendita", "acquisto_max", "giorni_vendita", "rischi", "giudizio", "motivazione"}
    mancanti = chiavi - set(d)
    if mancanti:
        raise PeriziaNonValida("campi mancanti: " + ", ".join(sorted(mancanti)))
    if not isinstance(d["dati_sufficienti"], bool):
        raise PeriziaNonValida("dati_sufficienti non è vero/falso")
    suff = d["dati_sufficienti"]
    giudizio = _giudizio(d["giudizio"])
    pv = d["prezzo_vendita"]
    forbice = None
    if pv is not None:
        if not isinstance(pv, dict):
            raise PeriziaNonValida("prezzo_vendita non è un oggetto")
        b, m, a = (_intero(pv.get(k), "prezzo_vendita." + k) for k in ("basso", "medio", "alto"))
        if not (300 <= b <= m <= a <= 2_000_000):
            raise PeriziaNonValida(f"forbice incoerente: {b}/{m}/{a}")
        if a > 3 * b:
            raise PeriziaNonValida("forbice troppo larga per essere una stima")
        forbice = {"basso": b, "medio": m, "alto": a}
    elif suff:
        raise PeriziaNonValida("dati sufficienti ma forbice mancante")
    acq = _intero(d["acquisto_max"], "acquisto_max", nullo=True)
    if acq is not None:
        if acq <= 0 or (forbice and acq > forbice["medio"]):
            raise PeriziaNonValida("acquisto_max oltre la stima media")
    gv = d["giorni_vendita"]
    giorni = None
    if gv is not None:
        if not isinstance(gv, dict):
            raise PeriziaNonValida("giorni_vendita non è un oggetto")
        gmin, gmax = _intero(gv.get("min"), "giorni_vendita.min"), _intero(gv.get("max"), "giorni_vendita.max")
        if not (1 <= gmin <= gmax <= 365):
            raise PeriziaNonValida("giorni_vendita incoerenti")
        giorni = {"min": gmin, "max": gmax}
    rischi_in = d["rischi"]
    if not isinstance(rischi_in, list):
        raise PeriziaNonValida("rischi non è un elenco")
    rischi = []
    for r in rischi_in[:6]:
        if not isinstance(r, dict):
            raise PeriziaNonValida("rischio non è un oggetto")
        tipo = str(r.get("tipo") or "").strip().lower()
        grav = str(r.get("gravita") or "").strip().lower()
        if tipo not in TIPI_RISCHIO or grav not in GRAVITA:
            raise PeriziaNonValida(f"rischio non ammesso: {tipo[:30]}/{grav[:10]}")
        rischi.append({"tipo": tipo, "gravita": grav, "nota": _testo(_senza_numeri(str(r.get("nota") or "")[:1000]), 160)})
    mot = _testo(_senza_numeri(str(d["motivazione"] or "")[:2000]), 300)
    if not mot:
        raise PeriziaNonValida("motivazione vuota")
    if not suff or forbice is None:
        giudizio = "da verificare"                    # dati insufficienti: mai un giudizio netto
    if cieca:
        giudizio = "da verificare"
    if giudizio == "caro" and prezzo:
        if (acq is not None and acq >= int(prezzo)) or (forbice and int(prezzo) < forbice["basso"]):
            giudizio = "nella media"                  # incoerente: chi può pagare il prezzo chiesto non lo trova caro
    return {"dati_sufficienti": suff and forbice is not None, "prezzo_vendita": forbice, "acquisto_max": acq,
            "giorni_vendita": giorni, "rischi": rischi, "giudizio": giudizio, "motivazione": mot}


# ------------------------------------------------------------------ chiamata
def _chiama_claude(sistema: str, testo: str, listing_id=None, sink=None):
    """Chiamata vera (ai/client.py): modello veloce, solo testo. Ritorna (json, token_in, token_out)."""
    try:
        res = AI.ask_json("perizia", AI.MODEL_FAST, sistema, [{"type": "text", "text": testo}],
                          max_tokens=int(os.environ.get("PERIZIA_MAX_TOKENS", "900")), usage_sink=sink,
                          listing_id=listing_id)
    except (AI.AIError, json.JSONDecodeError) as e:
        # risposta troncata o non JSON: la chiamata è già pagata e registrata (usage_sink prima di leggere il JSON)
        raise PeriziaNonValida(f"risposta non leggibile: {str(e)[:120]}")
    return res.data, res.input_tokens, res.output_tokens


def perizia(auto: dict, confronti: list[dict], chiama=None, cieca: bool = False, sink=None) -> dict:
    """Una perizia validata per un'auto. `chiama(sistema, testo, listing_id, sink)` -> (json, token_in, token_out):
    si sostituisce nei test con risposte finte. Solleva PeriziaNonValida o l'errore dell'AI."""
    testo = testo_richiesta(auto, scegli_confronti(auto, confronti), cieca=cieca)
    dati, tin, tout = (chiama or _chiama_claude)(SISTEMA, testo, auto.get("id"), sink)
    p = valida(dati, cieca=cieca, prezzo=None if cieca else auto.get("price_eur"))
    p["_token"] = (int(tin or 0), int(tout or 0))
    return p


def per_sito(p: dict) -> dict:
    """Riassunto per le regole del sito (pochi campi, in memoria per ogni auto)."""
    pv = (p.get("prezzo_vendita") or {}) if p.get("dati_sufficienti") else {}   # dati insufficienti: nessuna stima
    return {"giudizio": p.get("giudizio"), "medio": pv.get("medio"), "basso": pv.get("basso"), "alto": pv.get("alto")}


def per_scheda(p: dict) -> dict:
    """Cosa vede il commerciante nel riquadro "Parere del perito"."""
    return {k: p.get(k) for k in ("giudizio", "prezzo_vendita", "acquisto_max", "giorni_vendita", "rischi",
                                  "motivazione", "dati_sufficienti")}


# ------------------------------------------------------------------ database
def _tabella(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS perizia_ai (
        listing_id BIGINT NOT NULL, prezzo INTEGER NOT NULL, giudizio TEXT NOT NULL, medio INTEGER, basso INTEGER,
        alto INTEGER, acquisto_max INTEGER, dati JSONB NOT NULL, modello TEXT, token_in INTEGER, token_out INTEGER,
        computed_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (listing_id, prezzo))""")
    conn.execute("CREATE INDEX IF NOT EXISTS perizia_ai_at_idx ON perizia_ai (computed_at)")


def valida_giorni() -> int:
    return int(os.environ.get("PERIZIA_VALIDA_GIORNI", "14"))


def leggi_per_sito(conn, ids: list[int]) -> dict:
    """{listing_id: per_sito} per le perizie al prezzo attuale dell'annuncio (cache auto + prezzo)."""
    if not ids:
        return {}
    if not conn.execute("SELECT to_regclass('perizia_ai') AS t").fetchone()["t"]:
        return {}
    out = {}
    for k in range(0, len(ids), 2000):
        for r in conn.execute(
                "SELECT p.listing_id, p.giudizio, p.medio, p.basso, p.alto FROM perizia_ai p "
                "JOIN listings l ON l.id = p.listing_id AND p.prezzo = l.price_eur "
                "WHERE p.listing_id = ANY(%s) AND p.computed_at > now() - make_interval(days => %s)",
                (ids[k:k + 2000], valida_giorni())).fetchall():
            out[r["listing_id"]] = {"giudizio": r["giudizio"], "medio": r["medio"], "basso": r["basso"], "alto": r["alto"]}
    # (le perizie con dati insufficienti hanno medio NULL: salva() non scrive la forbice, il margine non si tocca)
    return out


def leggi_per_scheda(conn, listing_id: int, prezzo) -> dict | None:
    try:
        if not conn.execute("SELECT to_regclass('perizia_ai') AS t").fetchone()["t"]:
            return None
        r = conn.execute("SELECT dati, computed_at FROM perizia_ai WHERE listing_id=%s AND prezzo=%s "
                         "AND computed_at > now() - make_interval(days => %s)",
                         (listing_id, prezzo, valida_giorni())).fetchone()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    if not r:
        return None
    d = r["dati"] if isinstance(r["dati"], dict) else json.loads(r["dati"] or "{}")
    return dict(per_scheda(d), data=str(r["computed_at"])[:10])


AUTO_SQL = """
SELECT l.id, l.title, l.description, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox,
       l.power_kw, l.price_eur, l.seller_type, vp.dati->'confronti' AS confronti
FROM listings l
LEFT JOIN LATERAL (SELECT dati FROM valutazioni_policy v WHERE v.listing_id = l.id
                   ORDER BY v.computed_at DESC LIMIT 1) vp ON true
LEFT JOIN perizia_ai p ON p.listing_id = l.id AND p.prezzo = l.price_eur
                       AND p.computed_at > now() - make_interval(days => %(validi)s)
WHERE l.id = ANY(%(ids)s) AND l.status = 'attivo' AND l.price_eur IS NOT NULL AND p.listing_id IS NULL"""


def salva(conn, auto: dict, p: dict) -> None:
    pv = (p.get("prezzo_vendita") or {}) if p.get("dati_sufficienti") else {}   # dati insufficienti: medio NULL
    tin, tout = p.get("_token") or (0, 0)
    dati = {k: v for k, v in p.items() if not k.startswith("_")}
    conn.execute(
        "INSERT INTO perizia_ai (listing_id, prezzo, giudizio, medio, basso, alto, acquisto_max, dati, modello, token_in, token_out) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (listing_id, prezzo) DO UPDATE SET giudizio=EXCLUDED.giudizio, "
        "medio=EXCLUDED.medio, basso=EXCLUDED.basso, alto=EXCLUDED.alto, acquisto_max=EXCLUDED.acquisto_max, dati=EXCLUDED.dati, "
        "modello=EXCLUDED.modello, token_in=EXCLUDED.token_in, token_out=EXCLUDED.token_out, computed_at=now()",
        (auto["id"], auto["price_eur"], p["giudizio"], pv.get("medio"), pv.get("basso"), pv.get("alto"), p.get("acquisto_max"),
         json.dumps(dati, ensure_ascii=False), AI.MODEL_FAST, tin, tout))


# ------------------------------------------------------------------ giro in sottofondo
# coda riempita dal calcolo dell'elenco del sito (web/app.py): prima le auto in attesa, poi quelle già sul sito senza
# perizia (per il riquadro nella scheda). Solo id: niente in memoria oltre a due elenchi di interi.
CODA: dict = {"attesa": [], "sito": []}
_PAUSA = {"fino": 0.0}
_FALLITE: dict = {}          # listing_id -> (tentativi, ultimo tempo): errori ripetuti si saltano per 6 ore
FALLITE_MAX = 2000


def modo() -> str:
    """PERIZIA_AI: "0" spenta, "log" (default) fase ombra: le perizie si fanno e il SITO_FUNNEL dice cosa
    cambierebbe, ma il sito non cambia; "1" le regole valgono sul sito."""
    m = os.environ.get("PERIZIA_AI", "log").strip().lower()
    return m if m in ("0", "log", "1") else "log"


def attiva() -> bool:
    return modo() in ("log", "1")


def _segna_fallita(lid: int) -> None:
    n, _ = _FALLITE.get(lid, (0, 0))
    _FALLITE[lid] = (n + 1, time.time())
    if len(_FALLITE) > FALLITE_MAX:                    # niente crescita senza fine: via le più vecchie
        for k, _v in sorted(_FALLITE.items(), key=lambda kv: kv[1][1])[:len(_FALLITE) - FALLITE_MAX]:
            _FALLITE.pop(k, None)


def metti_in_coda(attesa: list[int], sito: list[int]) -> None:
    massimo = int(os.environ.get("PERIZIA_CODA_MAX", "1500"))
    CODA["attesa"] = [int(i) for i in attesa][:massimo]
    gia = set(CODA["attesa"])
    CODA["sito"] = [int(i) for i in sito if int(i) not in gia][:massimo]


def _status(e) -> int | None:
    for x in (getattr(e, "status_code", None), getattr(getattr(e, "response", None), "status_code", None)):
        if isinstance(x, int):
            return x
    return None


def _credito_nel_corpo(e) -> bool:
    """Anthropic risponde 400 invalid_request_error con il messaggio sul credito: si legge il corpo strutturato."""
    body = getattr(e, "body", None)
    err = body.get("error") if isinstance(body, dict) else None
    msg = (err.get("message") if isinstance(err, dict) else "") or ""
    return "credit balance" in msg.lower() or "billing" in msg.lower()


def tipo_errore(e) -> str | None:
    """'credito' (402, credito finito: pausa di un'ora), 'limite' (429, 529/sovraccarico: pausa breve) o None."""
    st, nome = _status(e), type(e).__name__
    if st == 402 or (st == 400 and _credito_nel_corpo(e)):
        return "credito"
    if st in (429, 529) or nome in ("RateLimitError", "OverloadedError"):
        return "limite"
    return None


def errore_di_credito(e) -> bool:
    return tipo_errore(e) is not None


def pausa_per(e) -> int:
    if tipo_errore(e) == "credito":
        return int(os.environ.get("PERIZIA_PAUSA_S", "3600"))
    return int(os.environ.get("PERIZIA_PAUSA_LIMITE_S", "300"))


def fatte_oggi(conn) -> int:
    """Chiamate PAGATE oggi (registro ai_usage, task 'perizia'): contano anche le risposte non valide."""
    return int(conn.execute("SELECT count(*) AS n FROM ai_usage WHERE task = 'perizia' AND at > "
                            "date_trunc('day', now() AT TIME ZONE 'Europe/Rome') AT TIME ZONE 'Europe/Rome'").fetchone()["n"])


def giro(conn, chiama=None, massimo: int | None = None) -> dict:
    """Un giro: al massimo PERIZIA_PER_GIRO auto dalla coda, rispettando PERIZIA_MAX_GIORNO e AI_BUDGET_GIORNO."""
    if not attiva():
        return {"spenta": "PERIZIA_AI=0"}
    if chiama is None and not AI.available():
        return {"saltato": "nessuna chiave AI"}
    if time.time() < _PAUSA["fino"]:
        return {"saltato": "pausa per credito/limite AI", "ripresa_tra_min": int((_PAUSA["fino"] - time.time()) / 60)}
    if not (CODA["attesa"] or CODA["sito"]):
        return {"candidate": 0}
    t0 = time.time()
    _tabella(conn)
    conn.commit()
    oggi = fatte_oggi(conn)
    massimo_giorno = int(os.environ.get("PERIZIA_MAX_GIORNO", "400"))
    tetto = massimo_giorno - oggi
    if tetto <= 0:
        return {"saltato": "tetto giornaliero raggiunto", "fatte_oggi": oggi}
    # le auto già sul sito (solo per il riquadro nella scheda) usano al massimo il 60% del tetto: il resto resta per
    # quelle in attesa, che senza perizia non entrano
    ids = CODA["attesa"] + (CODA["sito"] if oggi < float(os.environ.get("PERIZIA_QUOTA_SITO", "0.6")) * massimo_giorno else [])
    if not ids:
        return {"saltato": "quota per le auto già sul sito finita, nessuna in attesa", "fatte_oggi": oggi}
    if AI.budget_finito(conn):
        return {"saltato": "budget AI del giorno finito", "fatte_oggi": oggi}
    per_giro = min(tetto, massimo or int(os.environ.get("PERIZIA_PER_GIRO", "15")))
    ora = time.time()
    ids = [i for i in ids if not (_FALLITE.get(i, (0, 0))[0] >= 2 and ora - _FALLITE[i][1] < 6 * 3600)]
    scelte = ids[:per_giro * 3]                    # qualcuna può essere già periziata o non più valida
    trovate = {r["id"]: dict(r) for r in conn.execute(AUTO_SQL, {"ids": scelte, "validi": valida_giorni()}).fetchall()}
    righe = [trovate[i] for i in scelte if i in trovate][:per_giro]
    st = Counter()
    sink = AI.db_usage_sink(conn)          # ogni chiamata pagata finisce in ai_usage (budget e tetto del giorno)
    attesa = set(CODA["attesa"])
    fatte: set = set()                 # tolte dalla coda: salvate o risposta non valida (le altre si riprovano)
    for r in righe:
        if chiama is None and AI.budget_finito(conn):
            st["budget_finito"] += 1
            break
        try:
            conf = r.get("confronti") or []
            if isinstance(conf, str):
                conf = json.loads(conf)
            conf = arricchisci_confronti(conn, scegli_confronti(r, conf))
            p = perizia(r, conf, chiama=chiama, sink=sink)
        except PeriziaNonValida as e:
            st["non_valida"] += 1
            _segna_fallita(r["id"])
            fatte.add(r["id"])
            if st["non_valida"] <= 3:
                log.warning("PERIZIA %s risposta non valida: %s", r["id"], str(e)[:200])
            conn.commit()                        # la spesa registrata in ai_usage resta
            continue
        except Exception as e:
            try:
                conn.commit()                    # se la chiamata è avvenuta, la spesa in ai_usage resta contata
            except Exception:
                try:
                    conn.rollback()              # errore del database prima della chiamata
                except Exception:
                    pass
            st["errore"] += 1
            tipo = tipo_errore(e)
            if tipo:
                _PAUSA["fino"] = time.time() + pausa_per(e)
                st["pausa_" + tipo] += 1
                log.warning("PERIZIA %s AI: pausa di %d minuti (%s)", tipo, pausa_per(e) // 60, str(e)[:160])
                break
            _segna_fallita(r["id"])             # un'auto che dà sempre errore non blocca la coda
            if st["errore"] <= 3:
                log.warning("PERIZIA errore %s: %s", r["id"], str(e)[:200])
            if st["errore"] >= 5 and not st["fatte"]:
                break
            continue
        salva(conn, r, p)
        conn.commit()
        fatte.add(r["id"])
        st["fatte"] += 1
        st["giudizio:" + p["giudizio"]] += 1
        if r["id"] in attesa:
            st["da_attesa"] += 1
        _FALLITE.pop(r["id"], None)
    fatte |= {i for i in scelte if i not in trovate}          # già periziate o non più in vendita
    CODA["attesa"] = [i for i in CODA["attesa"] if i not in fatte]
    CODA["sito"] = [i for i in CODA["sito"] if i not in fatte]
    res = {"candidate": len(righe), "in_coda": len(CODA["attesa"]) + len(CODA["sito"]), "secondi": round(time.time() - t0),
           "fatte_oggi": oggi + st["fatte"], **dict(st)}
    log.warning("PERIZIA_GIRO %s", json.dumps(res, ensure_ascii=False))
    return res
