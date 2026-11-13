"""Controllo AI delle auto candidate con Claude, al posto di Jev (13/11, cliente: "niente più Jev").

Stesso compito di pricing/jev.py, stesse uscite: per ogni auto candidata al sito, una volta, l'AI Claude già
configurata (ai/client.py: stessa chiave ANTHROPIC_API_KEY, modello veloce MODEL_FAST, registro ai_usage) guarda le
prime foto e legge l'annuncio e risponde con probabilità 0–1:

    guasto, prezzo_rata, km_credibili, danni_foto, foto_internet, targa_visibile, versione_ok, dati_coerenti,
    truffa, auto_vera (annuncio vero e coerente: serve alle auto con sconto alto), stato 0–4, decisione + confidenza.

La risposta si salva nella STESSA tabella decisione_ai e nello stesso formato di Jev (risposta->'guasto'->>'noul'
ecc.), con fonte='claude': il caricamento del sito (web/app.py) e le regole (web/sito_regole.py, passo "jev" e
"truffa") restano identici, e le decisioni Jev già salvate valgono finché non scadono (prezzo cambiato o 7 giorni).

Differenze volute rispetto a Jev:
  - all'AI NON si danno il nostro mercato, la rivendita né il margine (con Jev la domanda "è una truffa?" era
    circolare: l'auto molto scontata prendeva una truffa alta proprio perché scontata, rapporto 3 §2.4);
  - i confronti si mandano senza prezzi, solo per dire se sono la stessa versione TECNICA (motore, potenza,
    generazione): un allestimento con un altro nome non è una versione diversa;
  - cache per auto + contenuto (testo, dati, foto): se cambia solo il prezzo (al rialzo o in giù fino al 10%) la
    decisione si riusa senza nuova chiamata; vale CONTROLLO_VALIDA_GIORNI (14) giorni;
  - foto a blocchi: si lavora CONTROLLO_PARALLELI auto alla volta, ognuna con al massimo CONTROLLO_FOTO (2) foto
    rimpicciolite a CONTROLLO_FOTO_LATO (512 px, ~260 token l'una) e buttate subito dopo la chiamata.
    CONTROLLO_FOTO_MODO=url manda invece gli indirizzi (li scarica Anthropic: zero memoria, ma foto intere ≈ 1.600
    token l'una); se l'indirizzo non si scarica si riprova in base64 e poi senza foto;
  - nessuna chiamata quando il testo e i dati bastano già a togliere l'auto dal sito (segnali duri, solo leasing,
    km o anno fuori regola): scartata_dal_testo.
Priorità: auto nuove, poi prezzo cambiato, poi le altre (sconto alto e margine alto prima); per ultimo il rinnovo
delle decisioni scadute (le vecchie di Jev restano valide sul sito finché il prezzo non cambia).

Spesa (prezzi veri, ai/client.PREZZI): budget PROPRIO CONTROLLO_BUDGET_USD (4 $ al giorno), SEPARATO dal tetto comune
AI_BUDGET_GIORNO (perizia, lettore, difetti, giudice non si fermano per il controllo), più CONTROLLO_MAX_GIORNO
chiamate (1.500). Ogni chiamata si registra in ai_usage e si conferma (commit) subito dopo; un'auto che fallisce non
si richiama per 6 ore (niente ripagamenti). Costo misurato sulla richiesta tipo: ~2.400 token in entrata e ~180 in
uscita = ~0,0033 $ per auto con Haiku (1/5 $ per milione). Errori di credito (402/400 credit balance) → pausa di
un'ora; limite (429/529) → pausa di 5 minuti (stessa logica della perizia).

Interruttore: JEV_SOSTITUTO=claude (default) | jev (torna a TypeSafe Jev, pricing/jev.py).
Mai numeri di telefono o email all'AI né nei log.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import client as AI

log = logging.getLogger("controllo_auto")

TASK = "controllo_auto"
CHIAVI_P = ("guasto", "prezzo_rata", "km_credibili", "danni_foto", "foto_internet", "targa_visibile", "versione_ok",
            "dati_coerenti", "truffa", "auto_vera")
FOTO_CHIAVI = ("danni_foto", "foto_internet", "targa_visibile")
DECISIONI = ("compra", "tratta", "scarta")
STATO = ["Da riparare o con guasti seri", "Usata con difetti evidenti", "Normale per età e km",
         "Buono stato, curata", "Ottimo stato, tagliandata e documentata"]

SISTEMA = """Sei un commerciante di auto usate italiano esperto: controlli gli annunci di privati e commercianti prima
che un collega vada a vedere l'auto. Ricevi i dati dell'annuncio, il testo, alcune foto e (se ci sono) le versioni
delle auto usate per il confronto e i rischi trovati da un perito. NON ti diamo il valore di mercato: il prezzo lo
controlliamo noi con i nostri dati. Un prezzo basso, da solo, NON è un segnale di truffa.

Per ogni domanda rispondi con una probabilità da 0 a 1 (0 = sicuramente no, 1 = sicuramente sì; 0.5 = non si capisce).
- guasto: il testo dice che l'auto non è marciante, ha guasti a motore/cambio/frizione, spie accese, danni da riparare,
  è venduta per ricambi o "così com'è"?
- prezzo_rata: il prezzo indicato NON è il prezzo intero (rata mensile, anticipo, "a partire da", permuta obbligatoria,
  prezzo finto)?
- km_credibili: i km sono credibili per l'anno e per il testo (non un errore di battitura, non un valore finto tipo
  1.000 o 19.999, non in contraddizione con la descrizione)?
- danni_foto: nelle foto si vedono danni evidenti (carrozzeria rotta o ammaccata, vetri rotti, airbag esplosi, ruggine
  forte, auto incidentata)? null se non ci sono foto.
- foto_internet: le foto sono di catalogo/pubblicità, di un'auto diversa da quella dell'annuncio, o con il marchio di un
  ALTRO sito/venditore? (Foto fatte bene da un concessionario con il suo logo o un salone: rispondi basso.) null senza foto.
- targa_visibile: in almeno una foto la targa è leggibile? null senza foto.
- versione_ok: le auto del confronto sono la stessa auto TECNICA (stesso modello, stessa generazione, stesso motore e
  potenza simile, stesso tipo di alimentazione)? Un allestimento con un altro nome (es. Pop/Lounge, Business/Executive,
  Trendline/Highline) NON è una versione diversa: conta solo motore, potenza, generazione. null se non ti diamo confronti.
- dati_coerenti: anno, km, alimentazione, cambio, potenza e testo sono coerenti tra loro e con le foto?
- truffa: ci sono segnali di truffa (caparra o pagamento prima di vedere l'auto, auto o venditore all'estero,
  spedizione, testo generico o copiato, foto che non c'entrano, dati incoerenti)? Il prezzo basso NON conta.
- auto_vera: l'annuncio descrive un'auto vera, davvero in vendita qui, con foto, testo e dati coerenti con marca,
  modello e anno dichiarati (nessun segno di annuncio finto o "esca")?
- stato: da 0 a 4 (0 da riparare, 1 difetti evidenti, 2 normale, 3 buono e curato, 4 ottimo e documentato).
- decisione: "compra" (auto in ordine, da andare a vedere), "tratta" (qualcosa da verificare), "scarta" (SOLO per un
  problema serio che la rende non comprabile: non marciante, guasto grave, incidentata non dichiarata, annuncio finto).
  Il prezzo non decide la decisione. "confidenza": quanto sei sicuro della decisione (0-1).
- note: al massimo 120 caratteri, in italiano, senza numeri di telefono o nomi.

Non inventare nulla: usa solo testo, dati e foto. RISPONDI SOLO CON QUESTO JSON:
{"guasto": p, "prezzo_rata": p, "km_credibili": p, "danni_foto": p|null, "foto_internet": p|null,
 "targa_visibile": p|null, "versione_ok": p|null, "dati_coerenti": p, "truffa": p, "auto_vera": p, "stato": 0-4,
 "decisione": "compra"|"tratta"|"scarta", "confidenza": p, "note": str}"""


class ControlloNonValido(ValueError):
    pass


# ------------------------------------------------------------------ interruttori
def _env(nome: str, default: str) -> str:
    return os.environ.get(nome, default)


def n_foto() -> int:
    return max(0, int(_env("CONTROLLO_FOTO", "2")))


def modo_foto() -> str:
    m = _env("CONTROLLO_FOTO_MODO", "base64").strip().lower()
    return m if m in ("base64", "url", "0") else "base64"


def valida_giorni() -> int:
    return int(_env("CONTROLLO_VALIDA_GIORNI", "14"))


def attiva() -> bool:
    return AI.available() and AI.provider() == "anthropic"


# ------------------------------------------------------------------ testo per l'AI (nessun dato personale)
_RE_TEL = re.compile(r"(?<!\d)(?:\+?\d[\s.\-/]?){8,13}\d(?!\d)")


def _senza_dati_personali(testo: str) -> str:
    try:
        from ..web import scovo
        testo = scovo.RE_MOBILE.sub("[numero]", testo)
    except Exception:
        pass
    testo = _RE_TEL.sub("[numero]", testo)
    return re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", testo)


def _t(s, n: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def testo_richiesta(r: dict, confronti: list[dict] | None = None, perizia: dict | None = None,
                    foto: int = 0) -> str:
    descr = _t(_senza_dati_personali(str(r.get("description") or "")[:6000]), 1800)
    titolo = _t(_senza_dati_personali(str(r.get("title") or "")), 160)
    righe = [
        "ANNUNCIO",
        f"titolo: {titolo or '-'}",
        f"marca: {r.get('make') or '-'} | modello: {r.get('model') or '-'} | versione: "
        f"{_t(_senza_dati_personali(str(r.get('version_raw') or '')), 80) or '-'}",
        f"anno: {r.get('year') or '-'} | km: {r.get('mileage_km') if r.get('mileage_km') is not None else '-'} | "
        f"alimentazione: {r.get('fuel') or '-'} | cambio: {r.get('gearbox') or '-'} | potenza: "
        f"{str(r.get('power_kw')) + ' kW' if r.get('power_kw') else '-'}",
        f"prezzo chiesto: {r.get('price_eur') or '-'} € | venditore: {r.get('seller_type') or 'privato'} | "
        f"sito: {r.get('source') or '-'} | provincia: {r.get('province') or '-'} | foto nell'annuncio: {int(r.get('n_foto') or 0)}",
        f"testo: {descr or '-'}",
        "",
    ]
    conf = [c for c in (confronti or []) if isinstance(c, dict)][:10]
    if conf:
        righe.append(f"VERSIONI DELLE AUTO USATE PER IL CONFRONTO ({len(conf)}, senza prezzi)")
        for i, c in enumerate(conf, 1):
            righe.append(f"{i}. versione {_t(_senza_dati_personali(str(c.get('versione') or '')), 40) or '?'} | anno {c.get('anno') or '?'} | "
                         f"kW {c.get('kw') or '?'} | cambio {c.get('cambio') or '?'} | km {c.get('km') if c.get('km') is not None else '?'}")
    else:
        righe.append("CONFRONTI: nessuno (versione_ok = null)")
    if perizia:
        rischi = "; ".join(f"{x.get('tipo')} ({x.get('gravita')}): {_t(x.get('nota'), 120)}"
                           for x in (perizia.get("rischi") or [])[:5] if isinstance(x, dict))
        righe += ["", f"PERIZIA GIÀ FATTA: giudizio {perizia.get('giudizio') or '?'}; rischi: {rischi or 'nessuno'}"]
    righe += ["", f"FOTO ALLEGATE: {foto}" + (" (danni_foto, foto_internet, targa_visibile = null)" if not foto else "")]
    return "\n".join(righe)


# ------------------------------------------------------------------ risposta
def _p(v, campo: str, nullo: bool) -> float | None:
    if v is None:
        if nullo:
            return None
        raise ControlloNonValido(f"{campo} mancante")
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        x = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        raise ControlloNonValido(f"{campo} non numerico")
    if not 0 <= x <= 1:
        raise ControlloNonValido(f"{campo} fuori da 0-1")
    return round(x, 3)


def valida(d, con_foto: bool, con_confronti: bool) -> dict:
    """Schema rigido; solleva ControlloNonValido. Senza foto le domande sulle foto valgono null (mai inventate)."""
    if not isinstance(d, dict):
        raise ControlloNonValido("non è un oggetto JSON")
    out = {}
    for k in CHIAVI_P:
        nullo = k in FOTO_CHIAVI or k == "versione_ok"
        out[k] = _p(d.get(k), k, nullo)
    if not con_foto:
        for k in FOTO_CHIAVI:
            out[k] = None
    if not con_confronti:
        out["versione_ok"] = None
    dec = str(d.get("decisione") or "").strip().lower()
    if dec not in DECISIONI:
        raise ControlloNonValido(f"decisione non ammessa: {dec[:20]}")
    out["decisione"] = dec
    out["confidenza"] = _p(d.get("confidenza"), "confidenza", True)
    try:
        st = float(d.get("stato")) if d.get("stato") is not None else None
    except (TypeError, ValueError):
        st = None
    out["stato"] = None if st is None else max(0.0, min(4.0, st))
    out["note"] = _t(_senza_dati_personali(str(d.get("note") or "")[:500]), 160)
    return out


def in_risposta(x: dict, modello: str) -> dict:
    """Formato di decisione_ai.risposta uguale a Jev (answers): il sito lo legge senza cambiare nulla."""
    risp = {k: {"type": "noul", "noul": x.get(k)} for k in CHIAVI_P}
    risp["decisione"] = {"type": "choice", "choice": x["decisione"], "confidence": x.get("confidenza"),
                         "probabilities": {x["decisione"]: x.get("confidenza")}}
    risp["stato"] = {"type": "score", "score": x.get("stato")}
    risp["_fonte"] = "claude"
    risp["_modello"] = modello
    risp["_note"] = x.get("note") or ""
    return risp


# ------------------------------------------------------------------ cache per auto + contenuto
def impronta(r: dict, foto_src: list[str]) -> str:
    """Contenuto che decide le risposte (non il prezzo): testo, dati dell'auto, foto."""
    parti = [str(r.get(k) or "") for k in ("title", "description", "version_raw", "year", "mileage_km", "fuel",
                                            "gearbox", "power_kw", "seller_type")]
    parti += list(foto_src or [])
    return hashlib.sha1("\x1f".join(parti).encode("utf-8", "ignore")).hexdigest()[:20]


def riusabile(d_prezzo, d_impronta, prezzo, imp: str) -> bool:
    """Decisione Claude già fatta sullo stesso contenuto: si riusa se il prezzo è salito o sceso al massimo del 10%
    (un calo forte si ricontrolla: può essere un prezzo esca)."""
    if not d_impronta or d_impronta != imp or not d_prezzo or not prezzo:
        return False
    return float(prezzo) >= 0.9 * float(d_prezzo)


# ------------------------------------------------------------------ foto
def blocchi_foto(fonti: list[str], modo: str | None = None, http=None) -> list[dict]:
    modo = modo or modo_foto()
    out = []
    if modo == "0":
        return out
    for src in fonti[:n_foto()]:
        b = AI.image_url_block(src) if modo == "url" else AI.image_block(src, http=http, side=int(_env("CONTROLLO_FOTO_LATO", "512")))
        if b:
            out.append(b)
    return out


# ------------------------------------------------------------------ chiamata
def _chiama_claude(testo: str, foto: list[dict]):
    """(json, token_in, token_out, modello). L'AIResult non serve: la spesa la registra chi chiama."""
    kwargs = dict(model=AI.MODEL_FAST, max_tokens=int(_env("CONTROLLO_MAX_TOKENS", "350")), system=SISTEMA,
                  messages=[{"role": "user", "content": [*foto, {"type": "text", "text": testo}]}])
    resp = AI.crea(**kwargs)
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    u = resp.usage
    try:
        dati = AI.extract_json(text)
    except Exception as e:
        dati = e                                   # pagata comunque: chi chiama registra i token e scarta la risposta
    return dati, int(u.input_tokens or 0), int(u.output_tokens or 0), AI.MODEL_FAST


def controlla(r: dict, fonti_foto: list[str], confronti=None, perizia=None, chiama=None, http=None,
              paga=None) -> dict:
    """Un controllo con ripieghi sulle foto. Ritorna {"x": risposta validata | None, "errore": eccezione | None,
    "token": [(in, out, modello), ...], "foto": n}. `paga(token_in, token_out, modello)` si chiama SUBITO dopo ogni
    chiamata riuscita (registrazione e commit della spesa, anche se poi la risposta non è valida)."""
    chiama = chiama or _chiama_claude
    token: list = []
    modi = [modo_foto()] if fonti_foto and n_foto() else ["0"]
    if modi[0] == "url":
        modi += ["base64", "0"]
    elif modi[0] == "base64":
        modi += ["0"]
    ultimo = None
    for m in modi:
        foto = blocchi_foto(fonti_foto, m, http=http) if m != "0" else []
        if m != "0" and not foto:
            continue                              # nessuna foto scaricabile in questo modo: si passa al prossimo
        testo = testo_richiesta(r, confronti, perizia, foto=len(foto))
        try:
            dati, tin, tout, modello = chiama(testo, foto)
        except Exception as e:
            if foto and AI.errore_immagine(e) and not _tipo_errore(e):
                ultimo = e
                continue                          # foto rifiutata: stesso controllo con le foto in un altro modo
            return {"x": None, "errore": e, "token": token, "foto": len(foto)}
        finally:
            foto = None                           # la memoria delle foto si libera subito
        token.append((tin, tout, modello))
        if paga is not None:
            paga(tin, tout, modello)
        if isinstance(dati, Exception):
            return {"x": None, "errore": ControlloNonValido(f"risposta non leggibile: {str(dati)[:80]}"),
                    "token": token, "foto": 0}
        try:
            x = valida(dati, con_foto=m != "0", con_confronti=bool(confronti))
        except ControlloNonValido as e:
            return {"x": None, "errore": e, "token": token, "foto": 0}
        x["_foto"] = 0 if m == "0" else min(len(fonti_foto), n_foto())
        x["_modello"] = modello
        return {"x": x, "errore": None, "token": token, "foto": x["_foto"]}
    return {"x": None, "errore": ultimo or ControlloNonValido("nessun modo di mandare la richiesta"), "token": token,
            "foto": 0}


def _tipo_errore(e) -> str | None:
    from . import perizia as PER
    return PER.tipo_errore(e)


# ------------------------------------------------------------------ database
_COLONNE_FATTE = {"ok": False}


def assicura_colonne(conn) -> None:
    """Le colonne di migrazione 052 (fonte, impronta), una volta per processo (se la migrazione non è ancora passata)."""
    if _COLONNE_FATTE["ok"]:
        return
    n = conn.execute("SELECT count(*) AS n FROM information_schema.columns WHERE table_name = 'decisione_ai' "
                     "AND column_name IN ('fonte', 'impronta')").fetchone()["n"]
    if int(n or 0) < 2:                    # ALTER solo se servono (prende un blocco sulla tabella)
        conn.execute("ALTER TABLE decisione_ai ADD COLUMN IF NOT EXISTS fonte TEXT, ADD COLUMN IF NOT EXISTS impronta TEXT")
    conn.commit()
    _COLONNE_FATTE["ok"] = True


CAND_SQL = """
WITH cand AS (
  SELECT ta.listing_id AS id FROM top_auto ta
  UNION
  SELECT vp.listing_id FROM valutazioni_policy vp
  WHERE vp.computed_at > now() - interval '12 hours' AND vp.margine IS NOT NULL
    AND (vp.margine >= %(quota)s * COALESCE(NULLIF(vp.dati->>'soglia', '')::int, 1500)
         OR (vp.categoria = 'dati_insufficienti' AND vp.dati::text LIKE '%%sotto la mediana%%'))
  UNION
  SELECT rp.listing_id FROM ripiego_prudente rp WHERE rp.stima IS NOT NULL      -- 13/11: senza AI non entrano
)
SELECT l.id, l.price_eur, l.telefono_ok, vp.categoria AS cat, vp.margine,
       NULLIF(vp.riassunto->>'M', '')::numeric AS m, d.prezzo AS d_prezzo, d.impronta AS d_impronta,
       d.fonte AS d_fonte, d.computed_at AS d_at, (d.listing_id IS NOT NULL AND d.risposta->'guasto' IS NULL) AS d_incompleta
FROM cand c
JOIN listings l ON l.id = c.id AND l.status = 'attivo' AND l.price_eur IS NOT NULL
JOIN LATERAL (SELECT categoria, margine, riassunto FROM valutazioni_policy v WHERE v.listing_id = l.id
              ORDER BY v.computed_at DESC LIMIT 1) vp ON true
LEFT JOIN decisione_ai d ON d.listing_id = l.id
WHERE d.listing_id IS NULL OR d.prezzo <> l.price_eur OR d.risposta->'guasto' IS NULL
   OR (COALESCE(d.fonte, 'jev') <> 'claude' AND d.computed_at < now() - interval '7 days')
   OR (d.fonte = 'claude' AND d.computed_at < now() - make_interval(days => %(validi)s))
LIMIT 3000"""

AUTO_SQL = """
SELECT l.id, l.source, l.title, l.description, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel,
       l.gearbox, l.power_kw, l.price_eur, l.province, l.seller_type, l.telefono_ok,
       (SELECT count(*) FROM listing_photos p WHERE p.listing_id = l.id) AS n_foto,
       (SELECT array_agg(source_url ORDER BY position) FROM (SELECT source_url, position FROM listing_photos p
         WHERE p.listing_id = l.id ORDER BY position LIMIT %(nf)s) f) AS foto,
       vp.dati->'confronti' AS confronti, vp.categoria AS cat, vp.margine
FROM listings l
JOIN LATERAL (SELECT dati, categoria, margine FROM valutazioni_policy v WHERE v.listing_id = l.id
              ORDER BY v.computed_at DESC LIMIT 1) vp ON true
WHERE l.id = ANY(%(ids)s)"""


def sconto_alto(m, prezzo) -> bool:
    """Stesso limite in euro del sito (sito_regole.Regole.sconto_euro): queste auto senza controllo AI restano fuori."""
    from ..web.sito_regole import Regole
    if not m or not prezzo:
        return False
    lim = next(e for fino, e in Regole().sconto_euro if float(prezzo) < fino)
    return float(m) - float(prezzo) > lim


def classe(r: dict) -> int:
    """0 auto nuova (mai decisa), 1 prezzo cambiato, 2 decisione incompleta, 3 rinnovo di una decisione scaduta
    (soprattutto le vecchie di Jev, che sul sito valgono finché il prezzo non cambia): per ultime."""
    if r.get("d_prezzo") is None:
        return 0
    if r.get("price_eur") is not None and int(r["d_prezzo"]) != int(r["price_eur"]):
        return 1
    if r.get("d_incompleta"):
        return 2
    return 3


def ordina(righe: list[dict]) -> list[dict]:
    """Prima le auto nuove, poi quelle col prezzo cambiato, poi le altre; in ogni gruppo prima lo sconto alto (fuori dal
    sito finché l'AI non le conferma) e il margine più alto rispetto al prezzo."""
    return sorted(righe, key=lambda r: (classe(r), not sconto_alto(r.get("m"), r.get("price_eur")),
                                        r.get("cat") == "dati_insufficienti",
                                        -((r.get("margine") or 0) / max(r.get("price_eur") or 1, 1))))


def scartata_dal_testo(a: dict) -> str | None:
    """Il testo e i dati da soli bastano a togliere l'auto dal sito (segnali duri e regole fisse di sito_regole):
    nessuna chiamata, nessuna spesa."""
    from ..web import scovo
    from ..web import sito_regole as SR
    t = f"{a.get('title') or ''} {a.get('description') or ''}"
    if SR.testo_truffa(t):
        return "testo_pagamento_estero"
    if SR.tel_estero(t):
        return "numero_estero"
    if re.search(scovo.FIN_SOLO_RE, t, re.I):
        return "solo_leasing_finanziamento"
    g = SR.Regole()
    if a.get("mileage_km") is not None and int(a["mileage_km"]) > g.km_max:
        return "km"
    if a.get("year") and int(a["year"]) < g.anno_min:
        return "anno"
    return None


def spesa_oggi(conn) -> tuple[int, float]:
    rows = conn.execute("SELECT model, count(*) AS n, sum(input_tokens) AS i, sum(output_tokens) AS o FROM ai_usage "
                        "WHERE task = %s AND at > date_trunc('day', now() AT TIME ZONE 'Europe/Rome') AT TIME ZONE 'Europe/Rome' "
                        "GROUP BY model", (TASK,)).fetchall()
    n = sum(int(r["n"] or 0) for r in rows)
    usd = sum(AI.costo(r["model"], int(r["i"] or 0), int(r["o"] or 0)) for r in rows)
    return n, round(usd, 4)


def _perizie(conn, ids: list[int]) -> dict:
    try:
        if not conn.execute("SELECT to_regclass('perizia_ai') AS t").fetchone()["t"]:
            return {}
        out = {}
        for r in conn.execute("SELECT DISTINCT ON (listing_id) listing_id, dati FROM perizia_ai WHERE listing_id = ANY(%s) "
                              "ORDER BY listing_id, computed_at DESC", (ids,)).fetchall():
            d = r["dati"] if isinstance(r["dati"], dict) else json.loads(r["dati"] or "{}")
            out[r["listing_id"]] = {"giudizio": d.get("giudizio"), "rischi": d.get("rischi") or []}
        return out
    except Exception:
        conn.rollback()
        return {}


def salva(conn, r: dict, x: dict, imp: str, token: int) -> None:
    risp = in_risposta(x, x.get("_modello") or AI.MODEL_FAST)
    conn.execute(
        """INSERT INTO decisione_ai (listing_id, prezzo, decisione, confidenza, p_compra, truffa, versione_ok,
             dati_coerenti, stato, correzione_pct, categoria_calcoli, margine_calcoli, risposta, token, fonte, impronta,
             computed_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,'claude',%s, now())
           ON CONFLICT (listing_id) DO UPDATE SET prezzo=EXCLUDED.prezzo, decisione=EXCLUDED.decisione,
             confidenza=EXCLUDED.confidenza, p_compra=EXCLUDED.p_compra, truffa=EXCLUDED.truffa,
             versione_ok=EXCLUDED.versione_ok, dati_coerenti=EXCLUDED.dati_coerenti, stato=EXCLUDED.stato,
             correzione_pct=NULL, categoria_calcoli=EXCLUDED.categoria_calcoli, margine_calcoli=EXCLUDED.margine_calcoli,
             risposta=EXCLUDED.risposta, token=EXCLUDED.token, fonte='claude', impronta=EXCLUDED.impronta,
             computed_at=now()""",
        (r["id"], r["price_eur"], x["decisione"], x.get("confidenza"),
         x.get("confidenza") if x["decisione"] == "compra" else None, x.get("truffa"), x.get("versione_ok"),
         x.get("dati_coerenti"), x.get("stato"), r.get("cat"), r.get("margine"), json.dumps(risp), token, imp))


def _registra_spesa(conn, lid: int, token: list) -> None:
    for tin, tout, modello in token:
        conn.execute("INSERT INTO ai_usage (task, model, input_tokens, output_tokens, web_searches, listing_id) "
                     "VALUES (%s,%s,%s,%s,0,%s)", (TASK, modello, tin, tout, lid))


# ------------------------------------------------------------------ giro
_PAUSA = {"fino": 0.0}
_FALLITE: dict = {}          # listing_id -> (tentativi, ultimo tempo): auto fallite (pagate o no) si saltano 6 ore
_SALTATE: dict = {}          # listing_id -> tempo: il testo basta a scartarle (nessuna chiamata) per 24 ore


def _segna_fallita(lid: int) -> None:
    n, _ = _FALLITE.get(lid, (0, 0))
    _FALLITE[lid] = (n + 1, time.time())
    if len(_FALLITE) > 3000:
        for k, _v in sorted(_FALLITE.items(), key=lambda kv: kv[1][1])[:len(_FALLITE) - 3000]:
            _FALLITE.pop(k, None)


def _da_saltare(lid: int, ora: float) -> bool:
    if lid in _FALLITE and ora - _FALLITE[lid][1] < 6 * 3600:
        return True                      # niente ripagamenti: un'auto fallita non si richiama per 6 ore
    return lid in _SALTATE and ora - _SALTATE[lid] < 24 * 3600


def per_giro() -> int:
    return int(_env("CONTROLLO_PER_GIRO", "40"))


def budget_usd() -> float:
    """Budget proprio, SEPARATO da AI_BUDGET_GIORNO (client.TASK_BUDGET_SEPARATO): dollari veri al giorno."""
    return float(_env("CONTROLLO_BUDGET_USD", "4"))


def giro(conn, massimo: int | None = None, chiama=None) -> dict:
    """Un giro sulle auto candidate non ancora controllate (stesso elenco di Jev). `chiama(testo, foto)` ->
    (json, token_in, token_out, modello) si sostituisce nei test."""
    import threading
    if chiama is None and not attiva():
        return {"spenta": "manca ANTHROPIC_API_KEY"}
    if time.time() < _PAUSA["fino"]:
        return {"saltato": "pausa per credito/limite AI", "ripresa_tra_min": int((_PAUSA["fino"] - time.time()) / 60)}
    t0 = time.time()
    assicura_colonne(conn)
    fatte_oggi, usd_oggi = spesa_oggi(conn)
    tetto = int(_env("CONTROLLO_MAX_GIORNO", "1500")) - fatte_oggi
    budget = budget_usd()
    if tetto <= 0:
        return {"saltato": "tetto giornaliero raggiunto", "fatte_oggi": fatte_oggi}
    if usd_oggi >= budget:
        return {"saltato": "budget del controllo finito", "usd_oggi": usd_oggi}
    sql = CAND_SQL
    if not conn.execute("SELECT to_regclass('ripiego_prudente') AS t").fetchone()["t"]:
        sql = sql.replace("  UNION\n  SELECT rp.listing_id FROM ripiego_prudente rp WHERE rp.stima IS NOT NULL", "")
    righe = [dict(r) for r in conn.execute(sql, {"quota": float(_env("JEV_QUOTA_SOGLIA", "0.7")),
                                                 "validi": valida_giorni()}).fetchall()]
    # le auto "troppo sotto prezzo" solo con il numero (come Jev); fallite o scartate dal testo aspettano
    ora = time.time()
    righe = [r for r in righe if (r["cat"] != "dati_insufficienti" or r.get("telefono_ok")) and not _da_saltare(r["id"], ora)]
    n_cand = len(righe)
    quante = min(tetto, massimo or per_giro())
    righe = ordina(righe)[:quante * 2]                 # qualcuna si riusa o si scarta senza chiamata
    leggere = {r["id"]: r for r in righe}
    st: Counter = Counter()
    if not righe:
        return {"candidate": 0, "in_coda": n_cand, "fatte_oggi": fatte_oggi}
    auto = {a["id"]: dict(a) for a in conn.execute(AUTO_SQL, {"ids": list(leggere), "nf": n_foto()}).fetchall()}
    perizie = _perizie(conn, list(auto))
    da_fare = []
    for r in righe:
        a = auto.get(r["id"])
        if not a:
            continue
        a["_imp"] = impronta(a, list(a.get("foto") or []))
        if r.get("d_fonte") == "claude" and riusabile(r.get("d_prezzo"), r.get("d_impronta"), a["price_eur"], a["_imp"]):
            # stesso contenuto, solo il prezzo cambiato di poco: la decisione vale anche al prezzo nuovo
            conn.execute("UPDATE decisione_ai SET prezzo=%s WHERE listing_id=%s AND fonte='claude'", (a["price_eur"], a["id"]))
            st["riusate"] += 1
            continue
        motivo = scartata_dal_testo(a)
        if motivo:
            _SALTATE[a["id"]] = time.time()               # il sito la toglie comunque: nessuna spesa
            st["senza_chiamata:" + motivo] += 1
            continue
        if len(da_fare) < quante:
            da_fare.append(a)
    if len(_SALTATE) > 5000:
        for k in [k for k, t in _SALTATE.items() if time.time() - t > 24 * 3600]:
            _SALTATE.pop(k, None)
    conn.commit()
    http = None
    if modo_foto() == "base64" and n_foto():
        try:                                       # un solo client per le foto del giro (chiuso alla fine)
            import httpx
            from ..web.foto import UA
            http = httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": UA})
        except Exception:
            http = None
    blocco = max(1, int(_env("CONTROLLO_PARALLELI", "3")))
    lock = threading.Lock()                        # la connessione si usa da un thread alla volta
    token_tot = [0, 0]
    spesa = {"usd": usd_oggi}
    nuove_targhe: list[int] = []
    fermo = False

    def paga_per(lid):
        def paga(tin, tout, modello):
            # 13/11 (giudice C2): la spesa si registra e si conferma SUBITO dopo la chiamata (anche se poi il giro cade)
            with lock:
                try:
                    _registra_spesa(conn, lid, [(tin, tout, modello)])
                    conn.commit()
                except Exception as e:
                    log.warning("CONTROLLO_AI spesa non registrata: %s", str(e)[:120])
                token_tot[0] += tin
                token_tot[1] += tout
                spesa["usd"] += AI.costo(modello, tin, tout)
        return paga

    def uno(a):
        try:
            conf = a.get("confronti") or []
            if isinstance(conf, str):
                conf = json.loads(conf)
            return a, controlla(a, list(a.get("foto") or []), conf, perizie.get(a["id"]), chiama=chiama, http=http,
                                paga=paga_per(a["id"]))
        except Exception as e:                     # un'auto rotta non ferma il blocco
            return a, {"x": None, "errore": e, "token": [], "foto": 0}

    def esito(a, res):
        e = res["errore"]
        if e is not None:
            st["errore" if not isinstance(e, ControlloNonValido) else "non_valida"] += 1
            tipo = _tipo_errore(e)
            if tipo:
                from . import perizia as PER
                _PAUSA["fino"] = time.time() + PER.pausa_per(e)
                st["pausa_" + tipo] += 1
                log.warning("CONTROLLO_AI %s: pausa di %d minuti (%s)", tipo, PER.pausa_per(e) // 60, str(e)[:160])
                return True
            _segna_fallita(a["id"])                # niente ripagamenti: per 6 ore non si richiama
            if st["errore"] + st["non_valida"] <= 3:
                log.warning("CONTROLLO_AI %s non riuscito: %s", a["id"], str(e)[:200])
            return st["errore"] >= 5 and not st["fatte"]
        x = res["x"]
        salva(conn, a, x, a["_imp"], sum(t[0] for t in res["token"]))
        _FALLITE.pop(a["id"], None)
        st["fatte"] += 1
        st[f"decisione:{x['decisione']}"] += 1
        st["con_foto"] += 1 if x.get("_foto") else 0
        for chiave, motivo in (("guasto", "guasto"), ("danni_foto", "danni_nelle_foto"), ("truffa", "truffa_alta")):
            if (x.get(chiave) or 0) >= .7:
                st[motivo] += 1
        if x.get("km_credibili") is not None and x["km_credibili"] <= .3:
            st["km_non_credibili"] += 1
        if x.get("versione_ok") is not None and x["versione_ok"] < .25:
            st["versioni_molto_diverse"] += 1
        if sconto_alto(leggere[a["id"]].get("m"), a["price_eur"]):
            st["sconto_alto"] += 1
        if x.get("targa_visibile") is not None:
            et = ("foto_internet" if (x.get("foto_internet") or 0) >= .7 else
                  "visibile" if x["targa_visibile"] >= .7 else "coperta" if x["targa_visibile"] <= .3 else "incerta")
            conn.execute("INSERT INTO controllo_targa (listing_id, esito, foto) VALUES (%s,%s,%s) "
                         "ON CONFLICT (listing_id) DO UPDATE SET esito=EXCLUDED.esito, foto=EXCLUDED.foto, "
                         "checked_at=now()", (a["id"], et, x.get("_foto") or 0))
            if et == "visibile" and a.get("cat") == "dati_insufficienti":
                nuove_targhe.append(a["id"])
        return False

    try:
        with ThreadPoolExecutor(max_workers=blocco) as ex:
            for k in range(0, len(da_fare), blocco):
                if spesa["usd"] >= budget:
                    st["budget_finito"] += 1
                    break
                for a, res in ex.map(uno, da_fare[k:k + blocco]):
                    with lock:
                        try:
                            fermo = esito(a, res) or fermo
                            conn.commit()
                        except Exception as e:     # errore del database su un'auto: le altre vanno avanti
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            _segna_fallita(a["id"])
                            st["errore_salvataggio"] += 1
                            log.warning("CONTROLLO_AI %s non salvato: %s", a["id"], str(e)[:200])
                if fermo:
                    break
    finally:
        if http is not None:
            http.close()
        conn.commit()
    if nuove_targhe:
        try:
            from ..pipeline.tempo_reale import valuta_e_pubblica
            st2 = Counter()
            valuta_e_pubblica(conn, nuove_targhe, st2)
            conn.commit()
            st["rivalutate_con_targa"] = len(nuove_targhe)
            st["pubblicate"] = st2.get("pubblicate", 0)
        except Exception as e:
            conn.rollback()
            log.warning("CONTROLLO_AI rivalutazione con targa non riuscita: %s", str(e)[:200])
    usd = AI.costo(AI.MODEL_FAST, token_tot[0], token_tot[1])
    res = {"fonte": "claude", "modello": AI.MODEL_FAST, "candidate": len(da_fare), "in_coda": n_cand,
           "secondi": round(time.time() - t0), "token_in": token_tot[0], "token_out": token_tot[1],
           "costo_usd": round(usd, 4), "usd_auto": round(usd / max(1, st["fatte"] + st["non_valida"]), 5),
           "fatte_oggi": fatte_oggi + st["fatte"], "usd_oggi": round(spesa["usd"], 4), "budget_usd": budget,
           "foto": f"{n_foto()}x{_env('CONTROLLO_FOTO_LATO', '512')}px {modo_foto()}", **dict(st)}
    log.warning("JEV_GIRO %s", json.dumps(res, ensure_ascii=False))   # stesso nome di riga di Jev (sentinella, cruscotti)
    return res
