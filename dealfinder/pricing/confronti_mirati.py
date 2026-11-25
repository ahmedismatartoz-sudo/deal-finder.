"""Confronti mirati (18/11 sera, cliente: "più auto senza toccare le regole", OK 18/11 21:01).

Problema misurato: tra le uscite del sito "valutata → dati_insufficienti" (2.139 auto) ci sono auto che restano senza
stima perché nel loro gruppo (marca, modello, alimentazione, zona) i commercianti uguali ci sono, ma l'annuncio non è
stato ricontrollato negli ultimi FRESCHI_GIORNI giorni (base D di valore_base: commercianti "freschi"). Il controllo
generale delle sparite (verify.scomparsi_con_prova) va dal più vecchio, senza sapere quali annunci servono.

Qui, per le auto valutate dati_insufficienti con motivo "pochi_confronti:rinfrescabile" (valore_base._pochi: con i
commercianti vecchi riaperti arriverebbero al minimo di N_MIN_SITO confronti), si riaprono SOLO quei commercianti, con
lo stesso controllo del rinfresco del sito (tempo_reale._controlla_annuncio, lenient=False: serve la prova):
  - vivo e stesso prezzo → last_checked_at adesso (torna "fresco": base D, come prevede il metodo);
  - sparito con prova → 'scomparso' come nella verifica (diventa una "venduta" della base V se era online da almeno
    MIN_GIORNI_ONLINE giorni, come tutte le altre);
  - prezzo cambiato → prezzo nuovo (come la raccolta, con la stessa guardia sulle letture implausibili) e visto
    adesso: il confronto vale col prezzo di oggi; errore o prezzo illeggibile → niente.
Poi si rivalutano le auto interessate con la strada di sempre (valutazione.valuta_e_scrivi, pubblica=True).

NIENTE cambia nel metodo: SOGLIE_PV, margine, N_MIN_SITO, anno/km dei confronti, freschezza (10 giorni), zona, taglio
della mediana, anti-truffa e regole del sito restano quelle. Cambia solo che i dati di quei confronti sono aggiornati.
La precisione misurata (base_vendute, errore mediano sulle vendute) non può peggiorare per costruzione: stesso
calcolo, dati veri più recenti.

Spesa: solo pagine pubbliche (AutoScout24 e Subito con lo stesso client del rinfresco: SCRAPER_PROXY, MAI il Web
Unlocker). Prima le auto che potrebbero davvero entrare: numero non già "no", nella zona/anno/km del sito, ordinate
per margine provvisorio/soglia (stima_priorita: SOLO per l'ordine, mai come valore); sotto META_SOGLIA della soglia
non si spende. Ritmo: al massimo PAGINE_GIRO pagine ogni GIRO_MIN minuti, PAGINE_GIORNO al giorno, pausa TR_PAUSA_S
(0,6 s) tra le pagine, stop dopo STOP_ERRORI blocchi di fila. Nei log solo conteggi (nessun id, link o numero).
Interruttore: CONFRONTI_MIRATI=0 (default acceso; nessuna variabile da impostare).

19/11 notte (automatismo 2): la raccolta notturna (notte(), più sotto) ha rotazione dei gruppi salvata nel database,
nessuna finestra di prezzo, interruttore e limiti senza deploy (dealfinder/automatismi.py: "confronti_notte"),
richiedi_notte() per la salute giornaliera e imbuto_candidate() che spiega nel log un "candidate 0".
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

log = logging.getLogger("valutazione")

PAGINE_GIRO = 60
PAGINE_GIORNO = 800
GIRO_MIN = 30
AUTO_GIRO = 40                # auto rivalutate al massimo per giro
RIPROVA_ORE = 24              # la stessa auto non si riprende prima di 24 ore
META_SOGLIA = 0.5             # margine provvisorio sotto metà soglia: anche coi confronti resterebbe fuori
STOP_ERRORI = 8
FONTI = ("autoscout24", "subito")

_PROVATE: dict = {}           # id auto -> ultimo tentativo (memoria del processo)
_GIORNO: dict = {"g": None, "pagine": 0}

CANDIDATE_SQL = """
SELECT l.id, l.price_eur, l.telefono_ok, vp.dati->'rinfresco' AS rinfresco
FROM valutazioni_policy vp JOIN listings l ON l.id = vp.listing_id
WHERE vp.policy_version = %(pv)s AND vp.categoria = 'dati_insufficienti' AND vp.metodo = 'base_vendute'
  AND vp.computed_at > now() - interval '3 days'
  AND split_part(COALESCE(vp.dati->>'motivo_dati', ''), ':', 1) = 'pochi_confronti'
  AND vp.dati->'rinfresco'->'ids'->0 IS NOT NULL
  AND l.status = 'attivo' AND l.seller_type IS DISTINCT FROM 'commerciante'
  AND l.telefono_ok IS NOT FALSE
  AND l.province = ANY(%(prov)s) AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s
  AND GREATEST(l.last_checked_at, l.last_seen_at) > now() - make_interval(hours => %(ore)s)
ORDER BY (l.stage = 'approfondito') DESC, vp.computed_at
LIMIT 3000"""
# 19/11 (automatismo 2): (a) non più solo il motivo "rinfrescabile": ogni pochi_confronti:* che ha commercianti vecchi
# da riaprire (rinfresco.ids scritto dalla valutazione); (b) il LIMIT ha un ordine (prima le auto già approfondite, poi
# la valutazione più vecchia: senza ORDER BY le 3.000 righe erano a caso); (c) stessa data dell'elenco del sito
# (scovo.LIST_SQL: vista o controllata nelle ultime FRESCHEZZA_ORE ore), non "vista negli ultimi 5 giorni".


def freschezza_ore() -> int:
    """Le ore di scovo.LIST_SQL (FRESCHEZZA_ORE, 48): solo lette, la regola resta lì."""
    try:
        return int(os.environ.get("FRESCHEZZA_ORE", "48"))
    except ValueError:
        return 48


def _parametri(a) -> dict:
    from . import policy_v2 as P
    from . import valore_base as VB
    return {"pv": P.POLICY_VERSION, "prov": VB.province_sito(), "anno": int(a.anno_min), "km": int(a.km_max),
            "ore": freschezza_ore()}

CONFRONTI_SQL = """
SELECT id, source, url, price_eur FROM listings
WHERE id = ANY(%s) AND status = 'attivo' AND seller_type = 'commerciante' AND source = ANY(%s)
  AND COALESCE(last_checked_at, last_seen_at) < now() - make_interval(days => %s)"""


def attivo() -> bool:
    return os.environ.get("CONFRONTI_MIRATI", "1") != "0"


def _oggi() -> str:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")
    except Exception:
        return time.strftime("%Y-%m-%d")


def _json(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            return None
    return x


def scegli(righe: list[dict], soglia_fn, adesso: float | None = None, provate: dict | None = None) -> list[dict]:
    """Auto da aiutare, in ordine (solo dati, testabile): numero già letto prima, poi margine provvisorio/soglia più
    alto. Fuori: senza id da riaprire, margine provvisorio sotto META_SOGLIA della soglia (resterebbero fuori comunque),
    già provate nelle ultime RIPROVA_ORE ore."""
    adesso = time.time() if adesso is None else adesso
    provate = _PROVATE if provate is None else provate
    out = []
    for r in righe:
        rf = _json(r.get("rinfresco")) or {}
        ids = [int(i) for i in (rf.get("ids") or []) if str(i).isdigit()]
        prezzo = int(r.get("price_eur") or 0)
        stima = rf.get("stima_priorita")
        if not ids or prezzo <= 0 or not stima:
            continue
        if adesso - provate.get(int(r["id"]), -1e18) < RIPROVA_ORE * 3600:
            continue
        sg = max(1, int(soglia_fn(prezzo)))
        rapporto = (float(stima) - prezzo) / sg
        if rapporto < META_SOGLIA:
            continue
        out.append(((0 if r.get("telefono_ok") is True else 1, -rapporto, int(r["id"])), dict(r, _ids=ids)))
    out.sort(key=lambda x: x[0])
    return [r for _k, r in out]


def giro(conn, controlla=None, rivaluta=None) -> Counter:
    """Un giro: sceglie le auto, riapre i loro commercianti vecchi, rivaluta le auto. Mai un'eccezione verso fuori."""
    from .. import regole as RG
    from ..web import scovo
    from . import base_vendute as BV
    from . import valore_base as VB
    st: Counter = Counter()
    if not attivo() or not VB.attivo():
        st["spento"] = 1
        return st
    if _GIORNO["g"] != _oggi():
        _GIORNO.update(g=_oggi(), pagine=0)
    resto = min(PAGINE_GIRO, PAGINE_GIORNO - _GIORNO["pagine"])
    if resto <= 0:
        st["tetto_giorno"] = 1
        return st
    try:
        a = RG.regole().ammissione
        righe = [dict(r) for r in conn.execute(CANDIDATE_SQL, _parametri(a)).fetchall()]
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("CONFRONTI_MIRATI candidate non lette: %s", str(e)[:150])
        st["errore_lettura"] = 1
        return st
    st["candidate"] = len(righe)
    if not righe:
        # 19/11: "candidate 0" spiegato nel log: quante auto pochi_confronti ci sono in zona e con quale motivo (il giro
        # aiuta solo le "rinfrescabile"; le altre le aiuta la raccolta della notte, notte())
        for k, n in motivi_zona(conn, a).items():
            st["zona:" + k] = n
        for k, n in imbuto_candidate(conn, a).items():      # quante auto restano dopo ogni filtro (solo conteggi)
            st["imbuto:" + k] = n
    scelte = scegli(righe, scovo.soglia)
    st["scelte"] = len(scelte)
    # confronti da riaprire, auto per auto (prima le migliori), fino al tetto di pagine del giro
    auto, da_aprire = [], []
    for r in scelte:
        if len(auto) >= AUTO_GIRO or len(da_aprire) >= resto:
            break
        nuovi = [i for i in r["_ids"] if i not in da_aprire]
        if auto and len(da_aprire) + len(nuovi) > resto:
            break                     # (giudice) non mezza auto: le altre al prossimo giro, non segnate come provate
        auto.append(r)
        da_aprire.extend(nuovi[:max(0, resto - len(da_aprire))])
    if not da_aprire:
        return st
    try:
        conf = [dict(x) for x in conn.execute(CONFRONTI_SQL, (da_aprire, list(FONTI), BV.FRESCHI_GIORNI)).fetchall()]
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("CONFRONTI_MIRATI confronti non letti: %s", str(e)[:150])
        st["errore_lettura"] = 1
        return st
    st["confronti_da_aprire"] = len(conf)
    controlla = controlla or _controlla
    esiti = controlla(conn, conf, st)
    _GIORNO["pagine"] += esiti.get("aperte", 0)
    adesso = time.time()
    for r in auto:
        _PROVATE[int(r["id"])] = adesso
    if len(_PROVATE) > 20000:
        for k in sorted(_PROVATE, key=_PROVATE.get)[:10000]:
            _PROVATE.pop(k, None)
    cambiati = set(esiti.get("cambiati") or ())
    ids_auto = [int(r["id"]) for r in auto if cambiati & set(r["_ids"])]
    # anche le auto senza confronti cambiati si rivalutano se un loro confronto vecchio non è più tra gli attivi
    # vecchi (già rinfrescato da un altro lavoro nel frattempo)
    gia = set(da_aprire) - {c["id"] for c in conf}
    ids_auto += [int(r["id"]) for r in auto if int(r["id"]) not in ids_auto and gia & set(r["_ids"])]
    st["auto_da_rivalutare"] = len(ids_auto)
    if ids_auto:
        try:
            if rivaluta is None:
                from .valutazione import valuta_e_scrivi as rivaluta
            es = rivaluta(conn, ids_auto, "confronti-mirati", pubblica=True, pausa_gruppo=VB.PAUSA_GRUPPO_S)
            conn.commit()
            for k, n in (getattr(es, "stats", None) or {}).items():
                if k.startswith(("categoria:", "dati_insufficienti:")) or k == "pubblicate":
                    st["rivalutate_" + k] += n
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            st["errore_rivalutazione"] += 1
            log.warning("CONFRONTI_MIRATI rivalutazione non riuscita: %s", type(e).__name__)
    return st


def _controlla(conn, conf: list[dict], st: Counter) -> dict:
    """Riapre i confronti con il controllo del rinfresco (prova obbligatoria) e li segna come fa giro_rinfresca."""
    from ..pipeline import tempo_reale as tr
    from ..pipeline.verify import _mark
    cas = tr._client_as() if any(r["source"] == "autoscout24" for r in conf) else None
    csb = tr._client_subito() if any(r["source"] == "subito" for r in conf) else None
    cambiati, aperte, di_fila = [], 0, 0
    try:
        for r in conf:
            if di_fila >= STOP_ERRORI:
                st["fermato_blocchi"] = 1
                break
            try:
                _r, stato, motivo = tr._controlla_annuncio(r, cas, csb, None, lenient=False)
            except Exception:
                stato, motivo = "errore", "blocco:eccezione"
            aperte += 1
            bloccato = stato == "errore" and str(motivo or "").startswith("blocco")
            di_fila = di_fila + 1 if bloccato else 0
            if stato == "attivo" and motivo == "prezzo_illeggibile":
                stato = "errore"
            st[f"{r['source']}:{stato}"] += 1
            try:      # (giudice) un errore del database su una riga non ferma il giro né perde il conto delle pagine
                if stato == "attivo" and str(motivo or "").startswith("prezzo_cambiato:"):
                    # un confronto vive del prezzo di OGGI: prezzo nuovo (come la raccolta, variazione plausibile già
                    # controllata da _confronta_prezzo) e visto adesso
                    tr.segna_prezzo_cambiato(conn, r["id"], int(str(motivo).split(":", 1)[1]))
                    _mark(conn, r["id"], "attivo", None)
                    st["prezzo_cambiato"] += 1
                    cambiati.append(r["id"])
                    continue
                if stato in ("attivo", "scomparso"):
                    _mark(conn, r["id"], stato, r.get("price_eur"), motivo=motivo)
                    cambiati.append(r["id"])
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                st["errore_scrittura"] += 1
    finally:
        for c in (cas, csb):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
    st["pagine"] += aperte
    return {"aperte": aperte, "cambiati": cambiati}


def diagnostica(st: Counter) -> None:
    d = {k: v for k, v in dict(st).items() if not str(k).startswith("imbuto:")}
    imbuto = {str(k)[7:]: v for k, v in dict(st).items() if str(k).startswith("imbuto:")}
    if imbuto:
        d["imbuto"] = imbuto
    log.warning("CONFRONTI_MIRATI %s", json.dumps({"pagine_oggi": _GIORNO["pagine"], **d}, ensure_ascii=False))


# ------------------------------------------------------------------ 19/11: perché "candidate 0" (diagnostica)
# Conti per motivo dei dati_insufficienti "pochi_confronti:*" delle auto che il giro potrebbe aiutare (stessi filtri di
# CANDIDATE_SQL senza il motivo): se "rinfrescabile" è 0 il giro non ha niente da fare e il log lo dice.
MOTIVI_ZONA_SQL = """
SELECT vp.dati->>'motivo_dati' AS motivo, count(*) AS n
FROM valutazioni_policy vp JOIN listings l ON l.id = vp.listing_id
WHERE vp.policy_version = %(pv)s AND vp.categoria = 'dati_insufficienti' AND vp.metodo = 'base_vendute'
  AND split_part(COALESCE(vp.dati->>'motivo_dati', ''), ':', 1) = 'pochi_confronti'
  AND l.status = 'attivo' AND l.seller_type IS DISTINCT FROM 'commerciante'
  AND l.telefono_ok IS NOT FALSE
  AND l.province = ANY(%(prov)s) AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s
  AND GREATEST(l.last_checked_at, l.last_seen_at) > now() - make_interval(hours => %(ore)s)
GROUP BY 1"""


def motivi_zona(conn, a=None) -> dict:
    """{motivo: auto} delle pochi_confronti in zona (solo conteggi). {} se la lettura non riesce."""
    from .. import regole as RG
    a = a or RG.regole().ammissione
    try:
        righe = conn.execute(MOTIVI_ZONA_SQL, _parametri(a)).fetchall()
        conn.commit()
        return {str(r["motivo"]): int(r["n"]) for r in righe}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("CONFRONTI_MIRATI motivi non letti: %s", type(e).__name__)
        return {}


# Imbuto delle candidate (19/11): quando il giro trova "candidate 0" il log dice a quale filtro si fermano le auto.
# Ogni passo conta le auto che passano quel filtro E tutti quelli prima (l'ultimo = le candidate di CANDIDATE_SQL).
IMBUTO_PASSI = (
    ("pochi_confronti", "split_part(COALESCE(vp.dati->>'motivo_dati', ''), ':', 1) = 'pochi_confronti'"),
    ("privati_attivi", "l.status = 'attivo' AND l.seller_type IS DISTINCT FROM 'commerciante'"),
    ("numero_non_no", "l.telefono_ok IS NOT FALSE"),
    ("zona_anno_km", "l.province = ANY(%(prov)s) AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s"),
    ("viste_di_recente", "GREATEST(l.last_checked_at, l.last_seen_at) > now() - make_interval(hours => %(ore)s)"),
    ("valutate_3_giorni", "vp.computed_at > now() - interval '3 days'"),
    ("con_commercianti_da_riaprire", "vp.dati->'rinfresco'->'ids'->0 IS NOT NULL"),
)


def imbuto_sql() -> str:
    colonne, finora = ["count(*) AS dati_insufficienti"], []
    for nome, cond in IMBUTO_PASSI:
        finora.append("(" + cond + ")")
        colonne.append("count(*) FILTER (WHERE " + " AND ".join(finora) + ") AS " + nome)
    colonne.append("count(*) FILTER (WHERE vp.dati->>'motivo_dati' = 'pochi_confronti:rinfrescabile') AS rinfrescabile")
    return ("SELECT " + ",\n       ".join(colonne) + "\nFROM valutazioni_policy vp JOIN listings l ON l.id = vp.listing_id\n"
            "WHERE vp.policy_version = %(pv)s AND vp.categoria = 'dati_insufficienti' AND vp.metodo = 'base_vendute'")


def imbuto_candidate(conn, a=None) -> dict:
    """{passo: auto rimaste} dei filtri di CANDIDATE_SQL, in ordine (solo conteggi). {} se la lettura non riesce."""
    from .. import regole as RG
    try:
        a = a or RG.regole().ammissione
        r = conn.execute(imbuto_sql(), _parametri(a)).fetchone()
        conn.commit()
        r = dict(r or {})
        nomi = ["dati_insufficienti"] + [n for n, _c in IMBUTO_PASSI] + ["rinfrescabile"]
        return {n: int(r.get(n) or 0) for n in nomi}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("CONFRONTI_MIRATI imbuto non letto: %s", type(e).__name__)
        return {}


# ------------------------------------------------------------------ 19/11: raccolta notturna dei confronti mirati
# Cliente 19/11: "ogni notte, senza toccare niente a mano". Il giro qui sopra riapre solo i commercianti VECCHI già nel
# database (motivo "rinfrescabile"): in produzione sono quasi sempre zero (log CONFRONTI_MIRATI "candidate 0"), perché
# un commerciante attivo non visto dalle ricerche viene già ricontrollato dalla verifica (verify.scomparsi_con_prova,
# dopo 36 ore: o torna "visto adesso", quindi fresco, o diventa 'scomparso'), quindi i commercianti tra 10 e 60 giorni
# (valore_base.vecchi_commercianti) non esistono quasi mai e nessuna auto prende il motivo "rinfrescabile". Le altre
# 2.800 auto senza abbastanza confronti (pochi_simili, nessuno_simile, modello_assente, solo_unendo, fragile) non
# avevano nessun aiuto.
# Qui, una volta per notte nel cron mercato (pipeline/collect.py, dopo la zona completa, processo pulito):
#   1. le auto dati_insufficienti "pochi_confronti:*" della zona del sito (stessi filtri di zona, anno, km, telefono di
#      CANDIDATE_SQL), raggruppate per marca/modello/alimentazione/zona, in ordine di margine potenziale (mediana grezza
#      dei pochi simili, stima_priorita: SOLO per l'ordine) e telefono letto;
#   2. per ogni gruppo una ricerca mirata sulle stesse fonti della raccolta: Subito (interfaccia di ricerca, parole
#      "marca modello", regione della zona) e AutoScout24 (pagina di ricerca marca/modello, concessionari, raggio della
#      zona), anni dell'auto ±2, prezzi intorno; si salvano SOLO gli annunci di COMMERCIANTI della stessa marca/modello
#      nelle province della zona, con upsert_listing come la raccolta, e si segnano "controllati adesso" (visti in una
#      ricerca allo stesso prezzo, come db.segna_invariati del tempo reale): sono la base D del metodo;
#   3. il giro di prima (riapertura dei commercianti vecchi), con il tetto di pagine della notte;
#   4. tutte le auto dei gruppi cercati si rivalutano con la strada di sempre (valuta_e_scrivi, pubblica=True).
# NIENTE cambia nel metodo (SOGLIE_PV, N_MIN_SITO, anno/km dei confronti, freschezza, zona, sconto di vetrina, prudenza,
# anti-truffa, regole del sito): si aggiungono solo confronti veri e attuali. Mai Facebook, mai il Web Unlocker.
# Nei log solo conteggi (nessun id, link, numero o email).
# Automatismo 2 (19/11 notte), correzioni:
#   - rotazione che resta tra una notte e l'altra (tabella confronti_gruppi): un gruppo cercato non si ricerca prima di
#     RIPOSO_ORE (un giorno) se ha dato confronti, RIPOSO_VUOTO_ORE (tre giorni) se non ne ha dati; prima i gruppi mai
#     cercati (in ordine di margine potenziale), poi quelli cercati da più tempo: ogni notte gruppi diversi;
#   - NESSUNA finestra di prezzo nelle ricerche: marca, modello, anni, zona, commercianti, dal più recente;
#   - AutoScout24: se la pagina marca/modello è vuota o illeggibile si ripiega sulla sola marca (il modello lo filtra
#     utile()), contatore autoscout24:slug_non_valido;
#   - interruttore e limiti senza deploy (dealfinder/automatismi.py): attivo("confronti_notte"),
#     numero("confronti_notte_gruppi" | "_pagine" | "_minuti" | "_rivaluta");
#   - richiedi_notte(): la salute giornaliera può chiedere una raccolta in più (riga job_runs JOB_RICHIESTA).
NOTTE_GRUPPI = 400             # gruppi marca/modello cercati al massimo per notte
NOTTE_PAGINE = 1500            # pagine di ricerca al massimo per notte (Subito + AutoScout24)
NOTTE_PAGINE_GRUPPO = 2        # pagine per fonte e gruppo
NOTTE_MINUTI = 150             # tempo massimo della raccolta (poi si rivaluta quello che c'è)
NOTTE_RIVALUTA_MAX = 4000      # auto rivalutate al massimo
NOTTE_ORE = 20                 # una volta al giorno (riga job_runs "confronti_mirati:notte")
RIPROVA_NOTTE_ORE = 3          # dopo un tentativo (riuscito, fallito o ancora in corso) non se ne avvia un altro
RIPOSO_ORE = 20                # "un giorno" (come NOTTE_ORE): un gruppo con confronti torna dalla notte dopo
RIPOSO_VUOTO_ORE = 68          # "tre giorni" per un gruppo senza confronti (4 ore di margine sull'ora del cron)
RICHIESTA_ORE = 24             # una richiesta non ancora servita vale un giorno: nel frattempo non se ne scrive un'altra
JOB_NOTTE = "confronti_mirati:notte"
JOB_RICHIESTA = "confronti_mirati:richiesta"
LOCK = 734131                  # = lucchetto del giro nel sito: mai due raccolte mirate insieme
ZONA_REGIONE = {"milano": "lombardia", "torino": "piemonte", "ravenna": "emilia-romagna"}
ZONA_CAP = {"milano": "20121", "torino": "10121", "ravenna": "48121"}

NOTTE_SQL = """
SELECT l.id, l.make, l.model, l.fuel, l.year, l.mileage_km, l.price_eur, l.province, l.telefono_ok,
       vp.dati->>'motivo_dati' AS motivo, vp.dati->'rinfresco' AS rinfresco, vp.dati->'priorita_raccolta' AS priorita
FROM valutazioni_policy vp JOIN listings l ON l.id = vp.listing_id
WHERE vp.policy_version = %(pv)s AND vp.categoria = 'dati_insufficienti' AND vp.metodo = 'base_vendute'
  AND split_part(COALESCE(vp.dati->>'motivo_dati', ''), ':', 1) = 'pochi_confronti'
  AND vp.computed_at > now() - interval '14 days'
  AND l.status = 'attivo' AND l.seller_type IS DISTINCT FROM 'commerciante'
  AND l.telefono_ok IS NOT FALSE
  AND l.province = ANY(%(prov)s) AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s
  AND GREATEST(l.last_checked_at, l.last_seen_at) > now() - make_interval(hours => %(ore)s)
  AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL AND l.price_eur > 0
ORDER BY (l.stage = 'approfondito') DESC, vp.computed_at
LIMIT 6000"""

GRUPPI_DDL = """
CREATE TABLE IF NOT EXISTS confronti_gruppi (
    make       TEXT NOT NULL,
    model      TEXT NOT NULL,
    fuel       TEXT NOT NULL,
    zona       TEXT NOT NULL,
    cercato_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    trovati    INTEGER NOT NULL DEFAULT 0,
    salvati    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (make, model, fuel, zona)
)"""
_tabella_fatta = [False]


def chiave_gruppo(g: dict) -> tuple:
    return (str(g.get("make") or "").strip().lower(), str(g.get("model") or "").strip().lower(),
            str(g.get("fuel") or "").strip().lower(), str(g.get("zona") or "").strip().lower())


def _tabella_gruppi(conn) -> None:
    """La tabella della rotazione, al primo uso (la migrazione 063 la crea uguale: qui per chi parte prima)."""
    if _tabella_fatta[0]:
        return
    conn.execute(GRUPPI_DDL)
    conn.commit()
    _tabella_fatta[0] = True


def gruppi_cercati(conn) -> dict:
    """{chiave del gruppo: (ore dall'ultima ricerca, confronti salvati quella volta)}. {} se non si legge (la notte
    va avanti senza rotazione: mai ferma per questo)."""
    try:
        _tabella_gruppi(conn)
        righe = conn.execute("SELECT make, model, fuel, zona, salvati, "
                             "extract(epoch FROM now() - cercato_at) / 3600.0 AS ore FROM confronti_gruppi").fetchall()
        conn.commit()
        return {chiave_gruppo(r): (float(r["ore"] or 0), int(r["salvati"] or 0)) for r in righe}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("CONFRONTI_NOTTE rotazione non letta: %s", type(e).__name__)
        return {}


def segna_gruppo(conn, g: dict, trovati: int, salvati: int) -> bool:
    """Gruppo cercato adesso (annunci letti e confronti salvati): la rotazione delle notti dopo."""
    try:
        _tabella_gruppi(conn)
        conn.execute("INSERT INTO confronti_gruppi (make, model, fuel, zona, cercato_at, trovati, salvati) "
                     "VALUES (%s, %s, %s, %s, now(), %s, %s) ON CONFLICT (make, model, fuel, zona) DO UPDATE SET "
                     "cercato_at = now(), trovati = EXCLUDED.trovati, salvati = EXCLUDED.salvati",
                     (*chiave_gruppo(g), int(trovati), int(salvati)))
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def in_riposo(ore: float, salvati: int) -> bool:
    """Gruppo cercato da poco: tre giorni se non aveva dato confronti, un giorno altrimenti."""
    return float(ore) < (RIPOSO_VUOTO_ORE if int(salvati or 0) <= 0 else RIPOSO_ORE)


def notte_attiva() -> bool:
    """Interruttore senza deploy: automatismi.attivo("confronti_notte") (chiave 'automatismi' di sito_impostazioni).
    CONFRONTI_NOTTE=0 e CONFRONTI_MIRATI=0 restano validi dove ci sono già."""
    if os.environ.get("CONFRONTI_NOTTE", "1") == "0" or not attivo():
        return False
    try:
        from .. import automatismi
        return automatismi.attivo("confronti_notte")
    except Exception:
        return True


def limite_notte(nome: str, default: int) -> int:
    """Limite regolabile senza deploy: automatismi.numero("confronti_notte_<nome>"), mai sotto 1."""
    try:
        from .. import automatismi
        return max(1, int(automatismi.numero("confronti_notte_" + nome, default)))
    except Exception:
        return int(default)


def priorita(r: dict, soglia_fn) -> float | None:
    """Margine potenziale / soglia di un'auto senza stima (stima_priorita della valutazione: rinfresco o
    priorita_raccolta). None se non c'è nessun simile da cui stimarlo. SOLO per l'ordine, mai un valore."""
    prezzo = int(r.get("price_eur") or 0)
    stima = None
    for k in ("priorita", "rinfresco"):
        d = _json(r.get(k)) or {}
        if isinstance(d, dict) and d.get("stima_priorita"):
            stima = float(d["stima_priorita"])
            break
    if stima is None or prezzo <= 0:
        return None
    return (stima - prezzo) / max(1, int(soglia_fn(prezzo)))


def gruppi_notte(righe: list[dict], soglia_fn, zona_fn, massimo: int | None = None, cercati: dict | None = None,
                 st: Counter | None = None) -> list[dict]:
    """Gruppi da cercare (solo dati, testabile): uno per marca/modello/alimentazione/zona. Ordine: prima i gruppi MAI
    cercati, per margine potenziale della loro auto migliore (poi telefono letto, poi id); poi quelli già cercati, da
    quello cercato da più tempo. Fuori i gruppi in riposo (`cercati` = gruppi_cercati(): cercati da meno di un giorno,
    o di tre se non avevano dato confronti) e le auto con margine potenziale sotto META_SOGLIA della soglia (anche con
    i confronti resterebbero fuori); le auto senza alcun simile (nessuna stima) vengono dopo tutte quelle stimate.
    Ogni gruppo: anni delle sue auto ±2. NESSUNA fascia di prezzo: i confronti si cercano a qualunque prezzo."""
    cercati = cercati or {}
    auto = []
    for r in righe:
        prezzo = int(r.get("price_eur") or 0)
        if prezzo <= 0 or not r.get("make") or not r.get("model") or not r.get("year"):
            continue
        rap = priorita(r, soglia_fn)
        if rap is not None and rap < META_SOGLIA:
            continue
        chiave = (rap is None, -(rap or 0.0), 0 if r.get("telefono_ok") is True else 1, int(r["id"]))
        auto.append((chiave, r, rap))
    auto.sort(key=lambda x: x[0])
    gruppi: dict = {}
    for _k, r, rap in auto:
        zona = zona_fn(r.get("province"))
        g = (str(r["make"]).strip().lower(), str(r["model"]).strip().lower(), r.get("fuel"), zona)
        if g not in gruppi:
            gruppi[g] = {"make": r["make"], "model": r["model"], "fuel": r.get("fuel"), "zona": zona, "ids": [],
                         "anni": [int(r["year"]), int(r["year"])], "migliore": rap}
        x = gruppi[g]
        x["ids"].append(int(r["id"]))
        x["anni"] = [min(x["anni"][0], int(r["year"])), max(x["anni"][1], int(r["year"]))]
    mai, gia, riposo = [], [], 0
    for x in gruppi.values():          # dict: ordine di inserimento = ordine dell'auto migliore del gruppo
        x["anno_da"], x["anno_a"] = x["anni"][0] - 2, x["anni"][1] + 2
        visto = cercati.get(chiave_gruppo(x))
        if visto is None:
            mai.append(x)
        elif in_riposo(visto[0], visto[1]):
            riposo += 1
        else:
            gia.append((-float(visto[0]), len(gia), x))
    gia.sort(key=lambda t: t[:2])
    out = mai + [x for _o, _n, x in gia]
    if st is not None:
        st["gruppi_totali"] = len(gruppi)
        st["gruppi_in_riposo"] = riposo
        st["gruppi_mai_cercati"] = len(mai)
    return out[:massimo] if massimo else out


def _slug(s) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").strip().lower()).strip("-")


def _stesso_modello(l, g: dict) -> bool:
    return (str(getattr(l, "make", "") or "").strip().lower() == str(g["make"]).strip().lower()
            and str(getattr(l, "model", "") or "").strip().lower() == str(g["model"]).strip().lower())


def utile(l, g: dict, province: set) -> str | None:
    """None se l'annuncio trovato serve come confronto del gruppo (commerciante, stessa marca/modello, anni del gruppo,
    provincia della zona); altrimenti il motivo (solo per i conteggi)."""
    if getattr(l, "seller_type", None) != "commerciante":
        return "non_commerciante"
    if not _stesso_modello(l, g):
        return "altro_modello"
    y = getattr(l, "year", None)
    if not y or not (g["anno_da"] <= int(y) <= g["anno_a"]):
        return "anno"
    if str(getattr(l, "province", "") or "").upper() not in province:
        return "fuori_zona"
    if not getattr(l, "price_eur", None):
        return "senza_prezzo"
    return None


def parametri_subito(g: dict, regione: int, page: int) -> dict:
    """Parametri della ricerca Subito di un gruppo: auto in vendita, parole "marca modello", regione, dal più recente.
    Mai un prezzo minimo o massimo."""
    par = {"c": 2, "t": "s", "q": f"{g['make']} {g['model']}", "r": regione, "lim": 100, "start": page * 100,
           "sort": "datedesc"}
    if regione is None:                # 25/11: ricerca in tutta Italia (cerca_italia): nessuna regione
        par.pop("r")
    return par


def cerca_subito(sub, g: dict, pagine: int, st: Counter) -> list:
    """Ricerca mirata su Subito (stessa interfaccia della raccolta, search_parole): parole "marca modello", regione
    della zona, dal più recente. Nessuna fascia di prezzo. Ritorna gli annunci (Listing)."""
    from ..collectors import subito as S
    reg = None if g["zona"] == ZONA_ITALIA else S.REGION_CODES.get(ZONA_REGIONE.get(g["zona"], "lombardia"), 4)
    out = []
    for page in range(pagine):
        try:
            r = sub._api(parametri_subito(g, reg, page))
        except Exception:
            st["subito:errore"] += 1
            break
        sub.pause()
        st["pagine"] += 1
        st["subito:pagine"] += 1
        if getattr(r, "status_code", None) != 200:
            st[f"subito:http_{getattr(r, 'status_code', 'x')}"] += 1
            if getattr(r, "status_code", None) in (403, 429):
                st["subito:bloccato"] = 1
            break
        try:
            ads = (r.json() or {}).get("ads") or []
        except Exception:
            st["subito:risposta_illeggibile"] += 1
            break
        for it in ads:
            try:
                l = S.parse_item(it)
            except Exception:
                l = None
            if l is not None:
                out.append(l)
        if len(ads) < 100:
            break
    return out


_SLUG_NO: set = set()          # (marca, modello) la cui pagina AutoScout24 è risultata vuota o illeggibile (processo)


def url_autoscout(base: str, percorso: str, g: dict, page: int) -> str:
    """Pagina di ricerca AutoScout24 di un gruppo: `percorso` = "marca/modello" (o la sola marca), solo concessionari,
    100 km dal centro della zona, anni del gruppo, dal più recente. Mai un prezzo minimo o massimo."""
    dove = "" if g["zona"] == ZONA_ITALIA else f"&zip={ZONA_CAP.get(g['zona'], '20121')}&zipr=100"   # 25/11: Italia
    return (f"{base}/lst/{percorso}?atype=C&cy=I&ustate=N%2CU&damaged_listing=exclude&custtype=D"
            f"{dove}&fregfrom={g['anno_da']}&fregto={g['anno_a']}"
            f"&sort=age&desc=1&page={page}")


def _pagine_autoscout(asc, A, percorso: str, g: dict, pagine: int, st: Counter) -> tuple[list, bool]:
    """(annunci, letta): letta=False se la prima pagina non è arrivata."""
    out, letta = [], True
    for page in range(1, pagine + 1):
        items, _tot, ok = asc._page(url_autoscout(A.BASE, percorso, g, page))
        st["pagine"] += 1
        st["autoscout24:pagine"] += 1
        if not ok:
            st["autoscout24:pagina_non_letta"] += 1
            letta = page > 1
            break
        for it in items:
            try:
                l = A.parse_item(it)
            except Exception:
                l = None
            if l is not None:
                l.seller_type = "commerciante"         # deciso dalla ricerca (custtype=D), come _emit della raccolta
                out.append(l)
        if len(items) < 20:
            break
    return out, letta


def cerca_autoscout(asc, g: dict, pagine: int, st: Counter) -> list:
    """Ricerca mirata su AutoScout24 (pagina di ricerca marca/modello, solo concessionari, raggio di 100 km dal centro
    della zona, anni del gruppo, nessuna fascia di prezzo), letta con lo stesso _page della raccolta (riprove,
    blocchi). Se la pagina marca/modello è vuota o non si legge (nome del modello diverso nell'indirizzo) si ripiega
    sulla pagina della sola marca: il modello lo filtra poi utile() in salva(). Un blocco (403/429) non è un nome
    sbagliato: nessun ripiego, e con il blocco confermato autoscout24:bloccato ferma la fonte per la notte."""
    from ..collectors import autoscout as A
    mk, md = _slug(g["make"]), _slug(g["model"])
    if not mk or not md:
        return []
    out: list = []
    if (mk, md) not in _SLUG_NO:
        out, _letta = _pagine_autoscout(asc, A, f"{mk}/{md}", g, pagine, st)
        if not out and not getattr(asc, "blocked", False) and not getattr(asc, "errori_blocco", 0):
            _SLUG_NO.add((mk, md))
    if not out and (mk, md) in _SLUG_NO:
        st["autoscout24:slug_non_valido"] += 1
        out, _letta = _pagine_autoscout(asc, A, mk, g, pagine, st)
    if getattr(asc, "blocked", False):
        st["autoscout24:bloccato"] = 1
    return out


def salva(conn, annunci: list, g: dict, province: set, st: Counter) -> set:
    """Salva gli annunci utili come la raccolta (normalizzazione, upsert_listing) e li segna controllati adesso.
    Ritorna gli id salvati."""
    from ..core.dal_testo import completa
    from ..core.normalize import normalize_fields
    from ..db import upsert_listing
    from ..zone import comune_ok
    ids = set()
    for l in annunci:
        try:
            for k, v in completa({"fuel": l.fuel, "year": l.year, "mileage_km": l.mileage_km, "gearbox": l.gearbox,
                                  "power_kw": l.power_kw, "version_raw": l.version_raw}, l.title, l.description).items():
                setattr(l, k, v)
            normalize_fields(l)
        except Exception:
            st["scarto:dati"] += 1
            continue
        if l.province and not getattr(l, "region", None):
            try:
                from ..core.normalize import PROVINCE_REGION
                l.region = PROVINCE_REGION.get(l.province)
            except Exception:
                pass
        perche = utile(l, g, province)
        if perche:
            st["scarto:" + perche] += 1
            continue
        if not comune_ok(l.province, l.city):
            st["scarto:fuori_60km"] += 1
            continue
        try:
            lid, event = upsert_listing(conn, l)
            # visto ADESSO in una ricerca allo stesso prezzo (upsert ha scritto il prezzo di oggi): controllato adesso,
            # come db.segna_invariati del tempo reale. Prezzo cambiato o riapparso: da rivalutare, come la raccolta
            conn.execute("UPDATE listings SET last_checked_at = now() WHERE id = %s AND status = 'attivo'", (lid,))
            if event in ("prezzo", "riapparso"):
                conn.execute("UPDATE listings SET stage='nuovo' WHERE id=%s AND stage<>'nuovo'", (lid,))
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            st["scarto:non_salvato"] += 1
            st["_non_salvati_di_fila"] += 1
            if st["scarto:non_salvato"] <= 3:
                log.warning("CONFRONTI_NOTTE annuncio non salvato: %s", type(e).__name__)
            if st["_non_salvati_di_fila"] >= SALVATAGGI_FALLITI_MAX:
                break                 # connessione caduta: inutile provare gli altri (notte() ferma la raccolta)
            continue
        st["_non_salvati_di_fila"] = 0
        ids.add(lid)
        st[f"{l.source}:{event}"] += 1
        st["confronti_salvati"] += 1
    return ids


# ------------------------------------------------------------------ ricerche mirate in tutta Italia (25/11)
# Cliente 25/11 sera: per ogni auto con pochi confronti una ricerca mirata di QUEL modello in tutta Italia su Subito e
# AutoScout24. I risultati servono SOLO da confronto per la stima allargata (pricing/stima_allargata, prova "italia"):
# fuori dalla zona del sito si salvano come stage 'mercato' / 'mercato_nord' ("mai proposta", valutazione.NON_PROPOSTE)
# e il sito ha comunque la sua regola di zona. Stessi collector della raccolta notturna (pause, intestazioni, stop al
# 403/429: cerca_subito / cerca_autoscout), una ricerca per modello ogni ITALIA_RIPOSO_ORE (riga "italia" di
# confronti_gruppi), al massimo ITALIA_GRUPPI modelli e ITALIA_PAGINE pagine per notte.
ZONA_ITALIA = "italia"
ITALIA_GRUPPI = 80             # modelli (marca/modello/alimentazione) cercati al massimo per notte
ITALIA_PAGINE = 200            # pagine al massimo per notte (Subito + AutoScout24)
ITALIA_PAGINE_GRUPPO = 1       # pagine per fonte e modello
ITALIA_RIPOSO_ORE = 24         # la stessa ricerca non si ripete prima di 24 ore
ITALIA_KM_MAX = 400000         # = stima_allargata.KM_CONFRONTI_MAX


def gruppi_italia(righe: list[dict], cercati: dict | None = None, massimo: int | None = None) -> list[dict]:
    """Un gruppo per marca/modello/alimentazione delle auto pochi_confronti (anni delle sue auto ±2), nell'ordine
    delle righe; fuori quelli cercati in tutta Italia da meno di ITALIA_RIPOSO_ORE ore. Solo dati (testabile)."""
    cercati = cercati or {}
    gruppi: dict = {}
    for r in righe:
        if not r.get("make") or not r.get("model") or not r.get("fuel") or not r.get("year"):
            continue
        k = (str(r["make"]).strip().lower(), str(r["model"]).strip().lower(), str(r["fuel"]).strip().lower())
        x = gruppi.setdefault(k, {"make": r["make"], "model": r["model"], "fuel": r["fuel"], "zona": ZONA_ITALIA,
                                  "ids": [], "anno_da": int(r["year"]) - 2, "anno_a": int(r["year"]) + 2})
        x["ids"].append(int(r["id"]))
        x["anno_da"], x["anno_a"] = min(x["anno_da"], int(r["year"]) - 2), max(x["anno_a"], int(r["year"]) + 2)
    out = []
    for x in gruppi.values():
        visto = cercati.get(chiave_gruppo(x))
        if visto is not None and float(visto[0]) < ITALIA_RIPOSO_ORE:
            continue
        out.append(x)
    return out[:massimo] if massimo else out


def salva_italia(conn, annunci: list, g: dict, st: Counter) -> set:
    """Salva gli annunci trovati in tutta Italia come confronti: stesso modello e alimentazione, anni del gruppo, km
    fino a ITALIA_KM_MAX, provincia nota e mai Roma. Un annuncio NUOVO fuori dalla zona del sito (o oltre i 60 km dei
    comuni) è solo un prezzo di riferimento: stage 'mercato', motivo 'mercato_nord' (mai proposto). Ritorna gli id."""
    from ..core.dal_testo import completa
    from ..core.normalize import normalize_fields
    from ..db import upsert_listing
    from ..web import sito_regole as SR
    from ..zone import comune_ok
    from . import valore_base as VB
    zona = set(VB.province_sito())
    ids = set()
    for l in annunci:
        try:
            for k, v in completa({"fuel": l.fuel, "year": l.year, "mileage_km": l.mileage_km, "gearbox": l.gearbox,
                                  "power_kw": l.power_kw, "version_raw": l.version_raw}, l.title, l.description).items():
                setattr(l, k, v)
            normalize_fields(l)
        except Exception:
            st["italia_scarto:dati"] += 1
            continue
        prov = str(getattr(l, "province", "") or "").upper()
        y, km = getattr(l, "year", None), getattr(l, "mileage_km", None)
        if not _stesso_modello(l, g) or str(getattr(l, "fuel", "") or "").lower() != str(g["fuel"]).lower():
            st["italia_scarto:altro_modello"] += 1
            continue
        if not y or not (g["anno_da"] <= int(y) <= g["anno_a"]) or km is None or int(km) > ITALIA_KM_MAX \
                or not getattr(l, "price_eur", None):
            st["italia_scarto:anno_km_prezzo"] += 1
            continue
        if not prov or prov in SR.ROMA:
            st["italia_scarto:provincia"] += 1
            continue
        if not getattr(l, "region", None):
            try:
                from ..core.normalize import PROVINCE_REGION
                l.region = PROVINCE_REGION.get(prov)
            except Exception:
                pass
        in_zona = prov in zona and comune_ok(l.province, l.city)
        try:
            lid, event = upsert_listing(conn, l)
            if event == "nuovo" and not in_zona:
                conn.execute("UPDATE listings SET stage='mercato', stage_reason='mercato_nord' WHERE id=%s", (lid,))
            conn.execute("UPDATE listings SET last_checked_at = now() WHERE id = %s AND status = 'attivo'", (lid,))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            st["italia_scarto:non_salvato"] += 1
            st["_italia_non_salvati"] += 1
            if st["_italia_non_salvati"] >= SALVATAGGI_FALLITI_MAX:
                break
            continue
        st["_italia_non_salvati"] = 0
        ids.add(lid)
        st["italia_confronti_salvati"] += 1
    return ids


def cerca_italia(conn, righe: list[dict], fonti, st: Counter, fermo=None, salva_fn=None) -> set:
    """Le ricerche mirate in tutta Italia per i modelli delle auto `righe` (pochi_confronti). `fonti` = (("subito",
    fn), ("autoscout24", fn)) con fn(g, pagine, st) come nella notte; una fonte bloccata (403/429) non si chiama più.
    `fermo()` = True quando il tempo della notte è finito. Ritorna gli id delle auto da rivalutare (gruppi con
    confronti salvati). Solo conteggi in `st` (chiavi "italia_*")."""
    gruppi = gruppi_italia(righe, gruppi_cercati(conn), ITALIA_GRUPPI)
    st["italia_gruppi"] = len(gruppi)
    pagine0 = st["pagine"]
    da_rivalutare: set = set()
    for g in gruppi:
        if (fermo is not None and fermo()) or st["pagine"] - pagine0 >= ITALIA_PAGINE \
                or st["_italia_non_salvati"] >= SALVATAGGI_FALLITI_MAX:
            st["italia_fermata"] = 1
            break
        if all(st[f"{nome}:bloccato"] for nome, _fn in fonti):
            st["italia_fermata_blocco"] = 1
            break
        trovati, letta = [], False
        for nome, fn in fonti:
            if st[f"{nome}:bloccato"]:
                continue
            try:
                trovati += fn(g, ITALIA_PAGINE_GRUPPO, st) or []
                letta = letta or not st[f"{nome}:bloccato"]
            except Exception as e:
                st[f"italia_{nome}:errore"] += 1
                if st[f"italia_{nome}:errore"] <= 2:
                    log.warning("CONFRONTI_ITALIA ricerca %s non riuscita: %s", nome, type(e).__name__)
        st["italia_annunci_letti"] += len(trovati)
        salvati = (salva_fn or salva_italia)(conn, trovati, g, st)
        if letta:
            segna_gruppo(conn, g, len(trovati), len(salvati))      # 24 ore prima di rifare la stessa ricerca
            st["italia_gruppi_cercati"] += 1
        if salvati:
            da_rivalutare |= set(g["ids"])
    st["italia_pagine"] = st["pagine"] - pagine0
    st.pop("_italia_non_salvati", None)
    return da_rivalutare


SALVATAGGI_FALLITI_MAX = 20     # annunci non salvati DI FILA (anche tra gruppi diversi): connessione caduta, ci si ferma


CHIAVI_LOG = ("gruppi", "gruppi_cercati", "pagine", "confronti_salvati", "rivalutate", "uscite_da_dati_insufficienti",
              "nuove_buone_sito", "subito:nuovo", "subito:invariato", "autoscout24:nuovo", "autoscout24:invariato")


def notte(connetti=None, cerca_sub=None, cerca_as=None, rivaluta=None, riapri=None, adesso=None, minuti_max=None,
          orologio=None, salva_fn=None, scadenza=None, italia=None) -> dict:
    """La raccolta notturna intera (vedi sopra). Mai un'eccezione verso fuori: riga di log CONFRONTI_MIRATI_NOTTE con i
    conteggi prima/dopo (auto in dati_insufficienti:pochi_confronti nella zona, uscite, nuove buone sul sito).
    Si ferma da sola: tempo (minuti, al massimo `minuti_max` se il cron ne ha meno), pagine, tutte le fonti bloccate
    (403/429), 20 salvataggi falliti di fila (connessione caduta); quello che è già stato raccolto si rivaluta comunque.
    `scadenza` (ora dell'orologio, dal cron) è DURA e vale per tutto: oltre non si cerca, non si riapre e la
    rivalutazione esce (`rivalutazione_fermata_tempo`): le auto rimaste le riprende la notte dopo."""
    from .. import regole as RG
    from ..web import scovo
    from . import valore_base as VB
    orologio = orologio or time.time
    t0 = orologio() if adesso is None else adesso
    st: Counter = Counter()
    out: dict = {}
    if not notte_attiva() or not VB.attivo():
        out = {"spento": True}
        log.warning("CONFRONTI_MIRATI_NOTTE %s", json.dumps(out))
        return out
    if connetti is None:
        from ..db import connect as connetti
    a = RG.regole().ammissione
    minuti = limite_notte("minuti", NOTTE_MINUTI)
    if minuti_max is not None:
        minuti = max(1, min(minuti, int(minuti_max)))
    pagine_max = limite_notte("pagine", NOTTE_PAGINE)
    sub = asc = None

    def scaduta() -> bool:
        return scadenza is not None and orologio() >= float(scadenza)
    try:
        with connetti() as conn:
            if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK,)).fetchone()["ok"]:
                out = {"altro_processo": True}
                log.warning("CONFRONTI_MIRATI_NOTTE %s", json.dumps(out))
                return out
            try:
                prima = motivi_zona(conn, a)
                righe = [dict(r) for r in conn.execute(NOTTE_SQL, _parametri(a)).fetchall()]
                conn.commit()
                st["auto_pochi_confronti"] = len(righe)
                gruppi = gruppi_notte(righe, scovo.soglia, VB.zona_di, limite_notte("gruppi", NOTTE_GRUPPI),
                                      cercati=gruppi_cercati(conn), st=st)
                st["gruppi"] = len(gruppi)
                zone = VB.zone()
                # 25/11: ricerche in tutta Italia (cerca_italia) solo coi collector veri, o se chi chiama passa `italia`
                fa_italia = italia is not None or (cerca_sub is None and cerca_as is None)
                if (gruppi or (righe and fa_italia)) and (cerca_sub is None or cerca_as is None):
                    from ..collectors.autoscout import AutoScoutCollector
                    from ..collectors.subito import SubitoCollector
                    if cerca_sub is None:
                        sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
                        sub.unlocker_zone = sub.unlocker_key = None        # mai il Web Unlocker (si paga)
                        cerca_sub = lambda g, n, s: cerca_subito(sub, g, n, s)     # noqa: E731
                    if cerca_as is None:
                        asc = AutoScoutCollector()
                        cerca_as = lambda g, n, s: cerca_autoscout(asc, g, n, s)   # noqa: E731
                fonti = (("subito", cerca_sub), ("autoscout24", cerca_as))
                cercati, nuovi_conf = [], set()
                for g in gruppi:
                    if orologio() - t0 > minuti * 60 or scaduta():
                        st["fermata_tempo"] = 1
                        break
                    if st["_non_salvati_di_fila"] >= SALVATAGGI_FALLITI_MAX:
                        st["fermata_salvataggi"] = 1      # connessione caduta: basta per stanotte
                        break
                    if st["pagine"] >= pagine_max:
                        st["fermata_pagine"] = 1
                        break
                    if all(st[f"{nome}:bloccato"] for nome, _fn in fonti):
                        st["fermata_blocco"] = 1          # 403/429 su tutte le fonti: basta per stanotte
                        break
                    prov = set(zone.get(g["zona"]) or zone["milano"])
                    trovati, letta = [], False
                    for nome, fn in fonti:
                        if st[f"{nome}:bloccato"]:
                            continue
                        try:
                            trovati += fn(g, NOTTE_PAGINE_GRUPPO, st) or []
                            letta = letta or not st[f"{nome}:bloccato"]
                        except Exception as e:
                            st[f"{nome}:errore"] += 1
                            if st[f"{nome}:errore"] <= 2:
                                log.warning("CONFRONTI_NOTTE ricerca %s non riuscita: %s", nome, type(e).__name__)
                    st["annunci_letti"] += len(trovati)
                    salvati = (salva_fn or salva)(conn, trovati, g, prov, st)
                    nuovi_conf |= set(salvati)
                    g["_salvati"] = len(salvati)
                    if letta:                              # un gruppo non letto (blocco, errore) si riprova domani
                        segna_gruppo(conn, g, len(trovati), len(salvati))
                        cercati.append(g)
                    else:
                        st["gruppi_non_letti"] += 1
                if st["_non_salvati_di_fila"] >= SALVATAGGI_FALLITI_MAX:
                    st["fermata_salvataggi"] = 1
                st.pop("_non_salvati_di_fila", None)
                st["gruppi_cercati"] = len(cercati)
                st["gruppi_con_confronti"] = sum(1 for g in cercati if g.get("_salvati"))
                st["confronti_nuovi_o_rinfrescati"] = len(nuovi_conf)
                ids_italia: set = set()
                try:
                    from . import stima_allargata as SA
                    if fa_italia and righe and (SA.OMBRA or SA.MODO_REALE):
                        ids_italia = set((italia or cerca_italia)(
                            conn, righe, fonti, st,
                            lambda: orologio() - t0 > minuti * 60 or scaduta() or st["pagine"] >= pagine_max))
                except Exception as e:
                    st["italia_errore"] += 1
                    log.warning("CONFRONTI_ITALIA non riuscita: %s", type(e).__name__)
                # riapertura dei commercianti vecchi (giro di sempre), con le pagine che restano alla notte
                try:
                    if scaduta():
                        st["riapertura_saltata_tempo"] = 1
                        rg = {}
                    else:
                        rg = (riapri or giro)(conn)      # rivaluta da solo le auto che aiuta (strada di sempre)
                    for k, n in dict(rg).items():
                        if isinstance(n, int) and (k in ("candidate", "scelte", "confronti_da_aprire", "pagine",
                                                         "auto_da_rivalutare") or k.startswith("rivalutate_")):
                            st["riapertura_" + k] += n
                except Exception as e:
                    st["riapertura_errore"] += 1
                    log.warning("CONFRONTI_NOTTE riapertura non riuscita: %s", type(e).__name__)
                # rivalutazione con la strada di sempre: le auto dei gruppi che hanno avuto confronti nuovi o
                # rinfrescati (un gruppo senza niente di nuovo darebbe lo stesso risultato di prima)
                da_rivalutare = {i for g in cercati if g.get("_salvati") for i in g["ids"]} | ids_italia
                per_id = {int(r["id"]): r for r in righe}
                pezzi = VB._blocchi([per_id[i] for i in sorted(da_rivalutare, key=lambda i: (
                    str(per_id[i]["make"]).lower(), str(per_id[i]["model"]).lower(), str(per_id[i]["fuel"]), i))
                    if i in per_id][:limite_notte("rivaluta", NOTTE_RIVALUTA_MAX)])
                if rivaluta is None and pezzi:
                    from .valutazione import valuta_e_scrivi as rivaluta
                salvata: dict = {}
                for n_pezzo, pezzo in enumerate(pezzi):
                    if scaduta():                         # scadenza dura del cron: il resto la notte dopo
                        st["rivalutazione_fermata_tempo"] = 1
                        st["rivalutazione_rimaste"] = sum(len(p) for p in pezzi[n_pezzo:])
                        break
                    try:
                        es = rivaluta(conn, pezzo, "confronti-notte", pubblica=True, pausa_gruppo=0)
                        conn.commit()
                        salvata.update(getattr(es, "salvata", None) or {})
                        for k, n in (getattr(es, "stats", None) or {}).items():
                            if k.startswith(("categoria:", "dati_insufficienti:")) or k == "pubblicate":
                                st["rivalutate_" + k] += n
                    except Exception as e:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        st["errore_rivalutazione"] += 1
                        log.warning("CONFRONTI_NOTTE rivalutazione non riuscita: %s", type(e).__name__)
                st["rivalutate"] = len(salvata)
                st["uscite_da_dati_insufficienti"] = sum(1 for c in salvata.values()
                                                        if c and c != "dati_insufficienti")
                st["nuove_buone_sito"] = sum(1 for c in salvata.values() if c in scovo.CATEGORIE_SITO)
                dopo = motivi_zona(conn, a)
                out = {"prima": {"totale": sum(prima.values()), **prima},
                       "dopo": {"totale": sum(dopo.values()), **dopo},
                       **{k: 0 for k in CHIAVI_LOG}, "minuti": round((orologio() - t0) / 60, 1), **dict(st)}
            finally:
                try:
                    conn.rollback()
                    conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
                    conn.commit()
                except Exception:
                    pass
    except Exception as e:
        out = dict(out or {}, errore=type(e).__name__, **{k: v for k, v in st.items() if not k.startswith("_")})
    finally:
        for c in (getattr(sub, "client", None), getattr(asc, "_client", None)):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
    log.warning("CONFRONTI_MIRATI_NOTTE %s", json.dumps(out, ensure_ascii=False, default=str))
    return out


STATO_NOTTE_SQL = """
SELECT (SELECT max(started_at) FROM job_runs WHERE job = %(notte)s AND ok) AS riuscita,
       (SELECT max(started_at) FROM job_runs WHERE job = %(notte)s) AS tentata,
       (SELECT max(started_at) FROM job_runs WHERE job = %(richiesta)s) AS richiesta,
       now() AS adesso"""


def _ore_tra(prima, dopo) -> float:
    return (dopo - prima).total_seconds() / 3600.0


def stato_notte(conn) -> dict:
    """{"riuscita", "tentata", "richiesta", "adesso"} (date del database; None se non c'è mai stata)."""
    r = conn.execute(STATO_NOTTE_SQL, {"notte": JOB_NOTTE, "richiesta": JOB_RICHIESTA}).fetchone()
    return dict(r or {})


def richiesta_in_attesa(s: dict) -> bool:
    """C'è una richiesta (richiedi_notte) più recente dell'ultima notte riuscita."""
    return bool(s.get("richiesta") and (s.get("riuscita") is None or s["richiesta"] > s["riuscita"]))


def dovuta(s: dict) -> bool:
    """La notte va fatta? (solo dati, testabile). No se un tentativo è partito da meno di RIPROVA_NOTTE_ORE (in corso,
    appena finito o appena fallito: niente cicli); sì se l'ultima riuscita ha più di NOTTE_ORE ore (o non c'è mai
    stata) oppure se c'è una richiesta più recente dell'ultima riuscita."""
    adesso = s.get("adesso")
    if adesso is None:
        return False
    if s.get("tentata") is not None and _ore_tra(s["tentata"], adesso) < RIPROVA_NOTTE_ORE:
        return False
    if s.get("riuscita") is None or _ore_tra(s["riuscita"], adesso) >= NOTTE_ORE:
        return True
    return richiesta_in_attesa(s)


def notte_dovuta(conn=None) -> bool:
    """Una volta al giorno (riga job_runs JOB_NOTTE riuscita nelle ultime NOTTE_ORE ore = già fatta), e in più quando
    qualcuno l'ha chiesta dopo l'ultima riuscita (richiedi_notte). Interruttore spento o errore = False."""
    if not notte_attiva():
        return False
    try:
        if conn is not None:
            s = stato_notte(conn)
            conn.commit()
            return dovuta(s)
        from ..db import connect
        with connect() as c:
            return dovuta(stato_notte(c))
    except Exception:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return False


def richiedi_notte(conn, motivo: str) -> bool:
    """Chiede una raccolta notturna in più al prossimo cron mercato (la salute giornaliera, azione A3): scrive una
    riga job_runs JOB_RICHIESTA e fa commit. True = richiesta scritta. False = interruttore spento, c'è già una
    richiesta non servita da meno di RICHIESTA_ORE ore, oppure errore (mai un'eccezione). Non avvia niente da sola:
    la raccolta parte dal cron (notte_nel_cron) con i suoi limiti e la rotazione dei gruppi."""
    import re
    if not notte_attiva():
        return False
    try:
        s = stato_notte(conn)
        if richiesta_in_attesa(s) and _ore_tra(s["richiesta"], s["adesso"]) < RICHIESTA_ORE:
            conn.commit()
            return False
        # solo lettere: nel registro dei lavori e nel log mai numeri, indirizzi o testo libero
        motivo = re.sub(r"[^a-z:]+", "_", str(motivo or "").strip().lower()).strip("_")[:40] or "senza_motivo"
        conn.execute("INSERT INTO job_runs (job, finished_at, ok, stats) VALUES (%s, now(), true, %s)",
                     (JOB_RICHIESTA, json.dumps({"motivo": motivo})))
        conn.commit()
        log.warning("CONFRONTI_MIRATI_RICHIESTA %s", json.dumps({"motivo": motivo}))
        return True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("CONFRONTI_MIRATI_RICHIESTA non scritta: %s", type(e).__name__)
        return False


def notte_nel_cron(minuti_max=None, scadenza=None) -> dict | None:
    """Dal cron mercato (pipeline/collect.py): la raccolta della notte se dovuta, registrata in job_runs. Non solleva."""
    try:
        if not notte_dovuta():
            return None
        from ..db import connect
        with connect() as c:          # riga "in corso" (connessione breve: la raccolta dura fino a due ore e mezza)
            jid = c.execute("INSERT INTO job_runs (job) VALUES (%s) RETURNING id", (JOB_NOTTE,)).fetchone()["id"]
            c.commit()
        esito: dict = {}
        try:
            esito = notte(minuti_max=minuti_max, scadenza=scadenza)
        finally:
            with connect() as c:
                c.execute("UPDATE job_runs SET finished_at=now(), ok=%s, stats=%s WHERE id=%s",
                          (bool(esito) and not esito.get("errore") and not esito.get("altro_processo"),
                           json.dumps({k: v for k, v in esito.items() if not isinstance(v, dict)}, default=str), jid))
                c.commit()
        return esito
    except Exception as e:
        log.warning("CONFRONTI_MIRATI_NOTTE non eseguita: %s", type(e).__name__)
        return None
