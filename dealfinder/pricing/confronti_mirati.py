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
  AND vp.dati->>'motivo_dati' = 'pochi_confronti:rinfrescabile'
  AND l.status = 'attivo' AND l.seller_type IS DISTINCT FROM 'commerciante'
  AND l.telefono_ok IS NOT FALSE
  AND l.province = ANY(%(prov)s) AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s
  AND l.last_seen_at > now() - interval '5 days'
LIMIT 3000"""

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
    from . import policy_v2 as P
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
        righe = [dict(r) for r in conn.execute(CANDIDATE_SQL, {"pv": P.POLICY_VERSION, "prov": VB.province_sito(),
                                                                 "anno": int(a.anno_min), "km": int(a.km_max)}).fetchall()]
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
    log.warning("CONFRONTI_MIRATI %s", json.dumps({"pagine_oggi": _GIORNO["pagine"], **dict(st)}, ensure_ascii=False))


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
  AND l.last_seen_at > now() - interval '5 days'
GROUP BY 1"""


def motivi_zona(conn, a=None) -> dict:
    """{motivo: auto} delle pochi_confronti in zona (solo conteggi). {} se la lettura non riesce."""
    from .. import regole as RG
    from . import policy_v2 as P
    from . import valore_base as VB
    a = a or RG.regole().ammissione
    try:
        righe = conn.execute(MOTIVI_ZONA_SQL, {"pv": P.POLICY_VERSION, "prov": VB.province_sito(),
                                               "anno": int(a.anno_min), "km": int(a.km_max)}).fetchall()
        conn.commit()
        return {str(r["motivo"]): int(r["n"]) for r in righe}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("CONFRONTI_MIRATI motivi non letti: %s", str(e)[:150])
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
# Nei log solo conteggi (nessun id, link, numero o email). Interruttore: CONFRONTI_NOTTE=0.
NOTTE_GRUPPI = 150             # gruppi marca/modello cercati al massimo per notte
NOTTE_PAGINE = 500             # pagine di ricerca al massimo per notte (Subito + AutoScout24)
NOTTE_PAGINE_GRUPPO = 2        # pagine per fonte e gruppo
NOTTE_MINUTI = 90              # tempo massimo della raccolta (poi si rivaluta quello che c'è)
NOTTE_RIVALUTA_MAX = 4000      # auto rivalutate al massimo
NOTTE_ORE = 20                 # una volta al giorno (riga job_runs "confronti_mirati:notte")
JOB_NOTTE = "confronti_mirati:notte"
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
  AND l.last_seen_at > now() - interval '5 days'
  AND l.make IS NOT NULL AND l.model IS NOT NULL AND l.fuel IS NOT NULL AND l.price_eur > 0
LIMIT 6000"""


def notte_attiva() -> bool:
    return os.environ.get("CONFRONTI_NOTTE", "1") != "0" and attivo()


def _int_env(nome: str, default: int) -> int:
    try:
        return int(os.environ.get(nome) or default)
    except ValueError:
        return default


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


def gruppi_notte(righe: list[dict], soglia_fn, zona_fn, massimo: int | None = None) -> list[dict]:
    """Gruppi da cercare (solo dati, testabile): uno per marca/modello/alimentazione/zona, in ordine di margine
    potenziale della loro auto migliore (poi telefono letto, poi id). Fuori le auto con margine potenziale sotto
    META_SOGLIA della soglia (anche con i confronti resterebbero fuori); le auto senza alcun simile (nessuna stima)
    vengono dopo tutte quelle stimate. Ogni gruppo: anni dell'auto ±2 e prezzi da 0,6× il più basso a 1,6× il più
    alto tra prezzi e stime."""
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
                         "anni": [int(r["year"]), int(r["year"])], "prezzi": [], "migliore": rap}
        x = gruppi[g]
        x["ids"].append(int(r["id"]))
        x["anni"] = [min(x["anni"][0], int(r["year"])), max(x["anni"][1], int(r["year"]))]
        stima = None if rap is None else int(r["price_eur"]) + rap * max(1, int(soglia_fn(int(r["price_eur"]))))
        x["prezzi"] += [int(r["price_eur"])] + ([int(stima)] if stima else [])
    out = []
    for x in gruppi.values():          # dict: ordine di inserimento = ordine dell'auto migliore del gruppo
        x["anno_da"], x["anno_a"] = x["anni"][0] - 2, x["anni"][1] + 2
        x["prezzo_da"] = max(500, int(min(x["prezzi"]) * 0.6) // 100 * 100)
        x["prezzo_a"] = int(max(x["prezzi"]) * 1.6) // 100 * 100 + 100
        out.append(x)
        if massimo and len(out) >= massimo:
            break
    return out


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


def cerca_subito(sub, g: dict, pagine: int, st: Counter) -> list:
    """Ricerca mirata su Subito (stessa interfaccia della raccolta, search_parole): parole "marca modello", regione
    della zona, fascia di prezzo del gruppo. Ritorna gli annunci (Listing)."""
    from ..collectors import subito as S
    reg = S.REGION_CODES.get(ZONA_REGIONE.get(g["zona"], "lombardia"), 4)
    out = []
    for page in range(pagine):
        try:
            r = sub._api({"c": 2, "t": "s", "q": f"{g['make']} {g['model']}", "r": reg, "ps": g["prezzo_da"],
                          "pe": g["prezzo_a"], "lim": 100, "start": page * 100, "sort": "datedesc"})
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


def cerca_autoscout(asc, g: dict, pagine: int, st: Counter) -> list:
    """Ricerca mirata su AutoScout24 (pagina di ricerca marca/modello, solo concessionari, raggio di 100 km dal centro
    della zona, anni e prezzi del gruppo), letta con lo stesso _page della raccolta (riprove, blocchi)."""
    from ..collectors import autoscout as A
    mk, md = _slug(g["make"]), _slug(g["model"])
    if not mk or not md:
        return []
    out = []
    for page in range(1, pagine + 1):
        url = (f"{A.BASE}/lst/{mk}/{md}?atype=C&cy=I&ustate=N%2CU&damaged_listing=exclude&custtype=D"
               f"&zip={ZONA_CAP.get(g['zona'], '20121')}&zipr=100&fregfrom={g['anno_da']}&fregto={g['anno_a']}"
               f"&pricefrom={g['prezzo_da']}&priceto={g['prezzo_a']}&sort=age&desc=1&page={page}")
        items, _tot, ok = asc._page(url)
        st["pagine"] += 1
        st["autoscout24:pagine"] += 1
        if not ok:
            st["autoscout24:pagina_non_letta"] += 1
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
            if st["scarto:non_salvato"] <= 3:
                log.warning("CONFRONTI_NOTTE annuncio non salvato: %s", type(e).__name__)
            continue
        ids.add(lid)
        st[f"{l.source}:{event}"] += 1
        st["confronti_salvati"] += 1
    return ids


def notte(connetti=None, cerca_sub=None, cerca_as=None, rivaluta=None, riapri=None, adesso=None) -> dict:
    """La raccolta notturna intera (vedi sopra). Mai un'eccezione verso fuori: riga di log CONFRONTI_MIRATI_NOTTE con i
    conteggi prima/dopo (auto in dati_insufficienti:pochi_confronti nella zona, uscite, nuove buone sul sito)."""
    from .. import regole as RG
    from ..web import scovo
    from . import policy_v2 as P
    from . import valore_base as VB
    t0 = time.time() if adesso is None else adesso
    st: Counter = Counter()
    out: dict = {}
    if not notte_attiva() or not VB.attivo():
        out = {"spento": True}
        log.warning("CONFRONTI_MIRATI_NOTTE %s", json.dumps(out))
        return out
    if connetti is None:
        from ..db import connect as connetti
    a = RG.regole().ammissione
    minuti = _int_env("CONFRONTI_NOTTE_MINUTI", NOTTE_MINUTI)
    pagine_max = _int_env("CONFRONTI_NOTTE_PAGINE", NOTTE_PAGINE)
    sub = asc = None
    try:
        with connetti() as conn:
            if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK,)).fetchone()["ok"]:
                out = {"altro_processo": True}
                log.warning("CONFRONTI_MIRATI_NOTTE %s", json.dumps(out))
                return out
            try:
                prima = motivi_zona(conn, a)
                righe = [dict(r) for r in conn.execute(NOTTE_SQL, {"pv": P.POLICY_VERSION, "prov": VB.province_sito(),
                                                                   "anno": int(a.anno_min), "km": int(a.km_max)}).fetchall()]
                conn.commit()
                st["auto_pochi_confronti"] = len(righe)
                gruppi = gruppi_notte(righe, scovo.soglia, VB.zona_di, _int_env("CONFRONTI_NOTTE_GRUPPI", NOTTE_GRUPPI))
                st["gruppi"] = len(gruppi)
                zone = VB.zone()
                if cerca_sub is None or cerca_as is None:
                    from ..collectors.autoscout import AutoScoutCollector
                    from ..collectors.subito import SubitoCollector
                    if cerca_sub is None:
                        sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
                        sub.unlocker_zone = sub.unlocker_key = None        # mai il Web Unlocker (si paga)
                        cerca_sub = lambda g, n, s: cerca_subito(sub, g, n, s)     # noqa: E731
                    if cerca_as is None:
                        asc = AutoScoutCollector()
                        cerca_as = lambda g, n, s: cerca_autoscout(asc, g, n, s)   # noqa: E731
                cercati, nuovi_conf = [], set()
                for g in gruppi:
                    if time.time() - t0 > minuti * 60:
                        st["fermata_tempo"] = 1
                        break
                    if st["pagine"] >= pagine_max:
                        st["fermata_pagine"] = 1
                        break
                    prov = set(zone.get(g["zona"]) or zone["milano"])
                    trovati = []
                    for nome, fn in (("subito", cerca_sub), ("autoscout24", cerca_as)):
                        if st[f"{nome}:bloccato"] or (nome == "autoscout24" and asc is not None and asc.blocked):
                            continue
                        try:
                            trovati += fn(g, NOTTE_PAGINE_GRUPPO, st) or []
                        except Exception as e:
                            st[f"{nome}:errore"] += 1
                            if st[f"{nome}:errore"] <= 2:
                                log.warning("CONFRONTI_NOTTE ricerca %s non riuscita: %s", nome, type(e).__name__)
                    st["annunci_letti"] += len(trovati)
                    nuovi_conf |= salva(conn, trovati, g, prov, st)
                    cercati.append(g)
                st["gruppi_cercati"] = len(cercati)
                st["confronti_nuovi_o_rinfrescati"] = len(nuovi_conf)
                # riapertura dei commercianti vecchi (giro di sempre), con le pagine che restano alla notte
                try:
                    rg = (riapri or giro)(conn)          # rivaluta da solo le auto che aiuta (strada di sempre)
                    for k, n in dict(rg).items():
                        if isinstance(n, int) and (k in ("candidate", "scelte", "confronti_da_aprire", "pagine",
                                                         "auto_da_rivalutare") or k.startswith("rivalutate_")):
                            st["riapertura_" + k] += n
                except Exception as e:
                    st["riapertura_errore"] += 1
                    log.warning("CONFRONTI_NOTTE riapertura non riuscita: %s", type(e).__name__)
                # rivalutazione: tutte le auto dei gruppi cercati (e le rinfrescabili riaperte) con la strada di sempre
                da_rivalutare = {i for g in cercati for i in g["ids"]}
                per_id = {int(r["id"]): r for r in righe}
                pezzi = VB._blocchi([per_id[i] for i in sorted(da_rivalutare, key=lambda i: (
                    str(per_id[i]["make"]).lower(), str(per_id[i]["model"]).lower(), str(per_id[i]["fuel"]), i))
                    if i in per_id][:_int_env("CONFRONTI_NOTTE_RIVALUTA", NOTTE_RIVALUTA_MAX)])
                if rivaluta is None:
                    from .valutazione import valuta_e_scrivi as rivaluta
                salvata: dict = {}
                for pezzo in pezzi:
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
                       "minuti": round((time.time() - t0) / 60, 1), **dict(st)}
            finally:
                try:
                    conn.rollback()
                    conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
                    conn.commit()
                except Exception:
                    pass
    except Exception as e:
        out = dict(out or {}, errore=type(e).__name__, **dict(st))
    finally:
        for c in (getattr(sub, "client", None), getattr(asc, "_client", None)):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
    log.warning("CONFRONTI_MIRATI_NOTTE %s", json.dumps(out, ensure_ascii=False, default=str))
    return out


def notte_dovuta() -> bool:
    """Una volta al giorno (riga job_runs JOB_NOTTE riuscita nelle ultime NOTTE_ORE ore = già fatta)."""
    if not notte_attiva():
        return False
    try:
        from ..pipeline.ciclo import _due
        return _due(JOB_NOTTE, NOTTE_ORE)
    except Exception:
        return False


def notte_nel_cron() -> dict | None:
    """Dal cron mercato (pipeline/collect.py): la raccolta della notte se dovuta, registrata in job_runs. Non solleva."""
    try:
        if not notte_dovuta():
            return None
        from ..db import connect
        with connect() as c:          # riga "in corso" (connessione breve: la raccolta dura fino a un'ora e mezza)
            jid = c.execute("INSERT INTO job_runs (job) VALUES (%s) RETURNING id", (JOB_NOTTE,)).fetchone()["id"]
            c.commit()
        esito: dict = {}
        try:
            esito = notte()
        finally:
            with connect() as c:
                c.execute("UPDATE job_runs SET finished_at=now(), ok=%s, stats=%s WHERE id=%s",
                          (not esito.get("errore") and not esito.get("altro_processo"),
                           json.dumps({k: v for k, v in esito.items() if not isinstance(v, dict)}, default=str), jid))
                c.commit()
        return esito
    except Exception as e:
        log.warning("CONFRONTI_MIRATI_NOTTE non eseguita: %s", type(e).__name__)
        return None
