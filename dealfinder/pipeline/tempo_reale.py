"""Mercato quasi in tempo reale (dal 16/10/2026): due agenti che girano in continuazione dentro il sito.

1. VERIFICA (ogni TR_VERIFICA_MIN = 10 minuti): riapre ogni auto pubblicata sul sito (Subito e AutoScout24) e,
   se l'annuncio non esiste più, la toglie subito (status 'scomparso'). Su AutoScout24 controlla anche che il
   venditore abbia un numero di telefono (si salva solo sì/no, mai il numero).
2. NUOVI (ogni TR_NUOVI_MIN = 10 minuti): legge gli annunci pubblicati dall'ultimo giro su Subito (Lombardia,
   Piemonte, Emilia, Liguria entro 150 km da Milano) e AutoScout24 (privati entro 150 km, dal più recente),
   li salva, e quelli di privati con il numero li valuta subito con la policy prudente: se sono opportunità
   (o da approfondire) vanno sul sito nello stesso giro. Gli annunci con danni o guasti nel testo restano
   all'analisi completa (AI o controllo a mano): non si pubblicano in automatico.

Obiettivo: un annuncio nuovo sul sito entro ~15 minuti (10 di raccolta + 5 di analisi), uno sparito tolto entro 10.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

log = logging.getLogger("tempo_reale")

REGIONI = {
    "lombardia": ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV", "CR", "MN", "SO"],
    "piemonte": ["NO", "VC", "BI", "VB", "AL", "AT", "TO"],
    "emilia-romagna": ["PC", "PR", "RE"],
    "liguria": ["GE"],
}
_codici_regione: dict[str, int] = {}
LOCK = {"nuovi": 734101, "verifica": 734102}


# ------------------------------------------------------------------ telefono

def telefono_autoscout(det: dict | None) -> bool | None:
    """True se la pagina dell'annuncio AutoScout24 mostra un numero del venditore, False se no, None se non letta."""
    if not isinstance(det, dict):
        return None
    seller = det.get("seller") or {}
    if not isinstance(seller, dict):
        return None

    def pieno(v) -> bool:
        if isinstance(v, str):
            return len(re.sub(r"\D", "", v)) >= 6
        if isinstance(v, dict):
            return any(pieno(x) for k, x in v.items() if k.lower() in ("callto", "formattednumber", "number", "phone",
                                                                         "phonenumber", "value", "tel"))
        if isinstance(v, list):
            return any(pieno(x) for x in v)
        return False

    for k, v in seller.items():
        if "phone" in k.lower() or k.lower() in ("tel", "telefono", "contactnumbers"):
            if pieno(v):
                return True
    return False


def telefono_subito(item: dict | None) -> bool:
    """Subito: il venditore ha messo il numero (bottone "Chiama" di fianco a "Messaggio") → advertiser.phone pieno.
    Si usa SOLO il sì/no: il numero non si salva mai."""
    if not isinstance(item, dict):
        return False
    adv = item.get("advertiser") or {}
    ph = adv.get("phone") if isinstance(adv, dict) else None
    if isinstance(ph, bool):
        return ph
    if isinstance(ph, (int, float)):
        return ph == 1
    if not isinstance(ph, str):
        return False
    ph = ph.strip().lower()
    # Subito manda un indicatore di una cifra ("1" = numero disponibile dietro il bottone), non il numero
    return ph in ("1", "true", "si", "yes") or len(re.sub(r"\D", "", ph)) >= 6


def ha_numero_testo(title: str | None, desc: str | None) -> bool:
    from ..web.scovo import RE_LANDLINE, RE_MOBILE
    t = f"{title or ''} {desc or ''}"
    return bool(RE_MOBILE.search(t) or RE_LANDLINE.search(t))


_diag = {"n": 0}
_diag_sub = {"n": 0}   # diagnostica: forma del campo telefono (cifre → 9, lettere → a), mai il numero


def _diagnostica_as(det: dict) -> None:
    """Prime pagine lette: nei log solo i NOMI dei campi del venditore (mai numeri), per controllare la lettura."""
    if _diag["n"] < 3 and isinstance(det, dict):
        _diag["n"] += 1
        s = det.get("seller") or {}
        log.warning("TR autoscout venditore: campi=%s tipo=%s telefono=%s", sorted(s)[:30] if isinstance(s, dict) else type(s).__name__,
                    (s or {}).get("type") if isinstance(s, dict) else None, telefono_autoscout(det))


# ------------------------------------------------------------------ pagine

def _as_dettaglio(client, url: str) -> tuple[str, dict | None]:
    """('attivo'|'scomparso'|'errore', listingDetails)."""
    from ..collectors.base import next_data
    try:
        r = client.get(url)
    except Exception as e:
        log.info("TR autoscout %s: %s", url, str(e)[:100])
        return "errore", None
    if r.status_code in (404, 410):
        return "scomparso", None
    if r.status_code != 200:
        return "errore", None
    if "/lst" in str(r.url) and "/annunci/" not in str(r.url):
        return "scomparso", None               # rimandati alla ricerca: annuncio tolto
    props = ((next_data(r.text) or {}).get("props") or {}).get("pageProps") or {}
    det = props.get("listingDetails") or props.get("listing")
    if not isinstance(det, dict):
        return ("scomparso" if "non è più disponibile" in r.text.lower() else "errore"), None
    _diagnostica_as(det)
    return "attivo", det


def _head_stato(client, url: str) -> str | None:
    """Controllo leggero (solo intestazioni, pochi byte: costa quasi niente sul proxy). None = serve la pagina intera."""
    try:
        r = client.head(url)
    except Exception:
        return None
    if r.status_code in (404, 410):
        return "scomparso"
    if r.status_code == 200:
        if "autoscout24" in url and "/lst" in str(r.url) and "/annunci/" not in str(r.url):
            return "scomparso"
        return "attivo"
    return None


def _subito_stato(client, url: str) -> str:
    st = _head_stato(client, url)
    if st:
        return st
    try:
        r = client.get(url)
    except Exception as e:
        log.info("TR subito %s: %s", url, str(e)[:100])
        return "errore"
    if r.status_code in (404, 410):
        return "scomparso"
    if r.status_code != 200:
        return "errore"
    low = r.text.lower()
    if "annuncio non è più disponibile" in low or "questo annuncio è scaduto" in low or "annuncio rimosso" in low:
        return "scomparso"
    return "attivo"


def _client_as():
    import httpx
    from ..collectors.autoscout import UA
    return httpx.Client(timeout=25, follow_redirects=True,
                        headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9", "Accept": "text/html"},
                        proxy=os.environ.get("SCRAPER_PROXY") or None)


def _client_subito():
    import httpx
    from ..collectors.subito import HEADERS
    return httpx.Client(timeout=25, follow_redirects=True, headers=HEADERS, proxy=os.environ.get("SCRAPER_PROXY") or None)


# ------------------------------------------------------------------ agente 1: verifica

def giro_verifica(conn, ids: list[int]) -> Counter:
    """Riapre gli annunci pubblicati. Ritorna i conteggi; gli id spariti sono in stats['_spariti']."""
    from .verify import _mark
    stats: Counter = Counter()
    # AutoScout24 approfondite senza controllo del numero: si controllano qui (senza numero non vanno sul sito)
    ids = list(ids) + [r["id"] for r in conn.execute(
        "SELECT id FROM listings WHERE source='autoscout24' AND status='attivo' AND stage='approfondito' "
        "AND telefono_ok IS NULL ORDER BY deep_at DESC NULLS LAST LIMIT %s", (int(os.environ.get("TR_AS_TELEFONO_GIRO", "250")),)).fetchall()]
    # IMBUTO, ultimo passo: le auto Subito già selezionate (approfondite) senza numero nel testo si controllano una per
    # una aprendo la pagina (bottone Chiama), poche per giro. Solo quelle col numero vanno sul sito.
    sub_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM listings WHERE source='subito' AND status='attivo' AND stage='approfondito' AND telefono_ok IS NULL "
        "ORDER BY telefono_at NULLS FIRST, deep_at DESC NULLS LAST LIMIT %s", (int(os.environ.get("TR_SUB_TELEFONO_GIRO", "40")),)).fetchall()]
    ids = list(ids) + sub_ids
    if not ids:
        return stats
    rows = conn.execute("SELECT id, source, url, price_eur, telefono_ok FROM listings WHERE id = ANY(%s) AND status='attivo' "
                        "AND source IN ('subito','autoscout24')", (ids,)).fetchall()
    cas, csb = _client_as(), _client_subito()
    sub_set = set(sub_ids)

    def check(r):
        time.sleep(float(os.environ.get("TR_PAUSA_S", "0.6")))
        if r["source"] == "autoscout24":
            if r["telefono_ok"] is not None:          # numero già controllato: basta sapere se l'annuncio c'è
                st = _head_stato(cas, r["url"])
                if st:
                    return r, st, None
            st, det = _as_dettaglio(cas, r["url"])
            return r, st, telefono_autoscout(det) if st == "attivo" else None
        if r["telefono_ok"] is None and r["id"] in sub_set:
            st, tel = subito_telefono_pagina(r["url"])
            return r, st, tel
        return r, _subito_stato(csb, r["url"]), None

    spariti = []
    with ThreadPoolExecutor(max_workers=int(os.environ.get("TR_PARALLELI", "4"))) as ex:
        for r, st, tel in ex.map(check, rows):
            stats[f"{r['source']}:{st}"] += 1
            if st == "errore":
                continue
            _mark(conn, r["id"], st, r.get("price_eur"))
            if st == "scomparso":
                spariti.append(r["id"])
            if tel is None and r["id"] in sub_set:          # pagina non leggibile: si riprova più avanti, dopo gli altri
                conn.execute("UPDATE listings SET telefono_at=now() WHERE id=%s", (r["id"],))
                stats["subito:telefono_non_leggibile"] += 1
            if tel is not None:
                conn.execute("UPDATE listings SET telefono_ok=%s, telefono_at=now() WHERE id=%s", (tel, r["id"]))
                stats[f"{r['source']}:telefono_{'si' if tel else 'no'}"] += 1
    conn.commit()
    stats["_spariti"] = spariti            # type: ignore[assignment]
    return stats


# ------------------------------------------------------------------ agente 2: nuovi

def _ora_it(dt: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return (dt + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")


def _subito_nuovi(dal: str) -> list:
    from ..collectors.subito import REGION_CODES, SubitoCollector
    col = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    col.min_delay_s, col.max_delay_s = float(os.environ.get("TR_SUBITO_PAUSA_MIN", "1.2")), float(os.environ.get("TR_SUBITO_PAUSA_MAX", "2.5"))
    out = []
    for reg, provs in REGIONI.items():
        try:
            if reg not in REGION_CODES:           # codice regione trovato una volta sola e poi ricordato
                code = _codici_regione.get(reg) or col._trova_regione(set(provs), 100)
                if not code:
                    log.warning("TR subito: codice regione non trovato per %s", reg)
                    continue
                _codici_regione[reg] = REGION_CODES[reg] = code
            for l in col.search_recenti(reg, provs, dal, max_pages=int(os.environ.get("TR_SUBITO_PAGINE", "6"))):
                out.append(l)
        except Exception as e:
            log.warning("TR subito %s: %s", reg, str(e)[:150])
    return out


def _autoscout_nuovi(conn) -> list:
    """Privati entro 150 km da Milano, dal più recente: si scorre finché una pagina è tutta già nota."""
    from ..collectors.autoscout import BASE, parse_item
    from ..collectors.base import next_data
    client = _client_as()
    noti = set()
    out = []
    for page in range(1, int(os.environ.get("TR_AS_PAGINE", "8")) + 1):
        url = (f"{BASE}/lst?atype=C&cy=I&ustate=N%2CU&damaged_listing=include&custtype=P&zip=20121"
               f"&zipr={os.environ.get('TR_RAGGIO', '150')}&adage=1&pricefrom=300&priceto=150000"
               f"&fregfrom={os.environ.get('ANNO_MINIMO', '2007')}&sort=age&desc=1&page={page}")
        try:
            r = client.get(url)
        except Exception as e:
            log.warning("TR autoscout ricerca: %s", str(e)[:120])
            break
        if r.status_code != 200:
            log.warning("TR autoscout ricerca HTTP %s", r.status_code)
            break
        items = (((next_data(r.text) or {}).get("props") or {}).get("pageProps") or {}).get("listings") or []
        if not items:
            break
        ids = [str(it.get("id")) for it in items if isinstance(it, dict)]
        noti = {x["source_id"] for x in conn.execute(
            "SELECT source_id FROM listings WHERE source='autoscout24' AND source_id = ANY(%s)", (ids,)).fetchall()}
        for it in items:
            l = parse_item(it)
            if l and l.source_id not in noti:
                l.seller_type = "privato"
                out.append(l)
        if len(noti) >= len(ids):          # pagina tutta già vista: i successivi sono più vecchi
            break
        time.sleep(float(os.environ.get("TR_AS_PAUSA_S", "1.5")))
    # pagina dell'annuncio: dati completi e numero di telefono
    for l in out:
        st, det = _as_dettaglio(client, l.url)
        if st == "attivo" and det:
            full = parse_item(det)
            if full:
                full.seller_type = "privato"
                full.photos = full.photos or l.photos
                l.__dict__.update({k: v for k, v in full.__dict__.items() if v not in (None, "", [])})
            l.raw = {"_telefono": telefono_autoscout(det)}
        time.sleep(float(os.environ.get("TR_AS_PAUSA_S", "1.5")))
    return out


def _salva(conn, listings: list, stats: Counter) -> list[int]:
    """Salva gli annunci letti; ritorna gli id da valutare (privati nuovi o con prezzo cambiato, nelle regole)."""
    from ..core.normalize import normalize_fields
    from ..db import upsert_listing
    from ..zone import MILANO
    from .collect import doppione_altro_sito
    da_valutare = []
    zona = set(MILANO)
    km_max, anno_min = int(os.environ.get("KM_MASSIMO", "180000")), int(os.environ.get("ANNO_MINIMO", "2007"))
    for l in listings:
        tel = (l.raw or {}).get("_telefono") if l.source == "autoscout24" else None
        tel_sub = telefono_subito(l.raw) if l.source == "subito" else False
        if l.source == "subito" and _diag_sub["n"] < 12 and isinstance(l.raw, dict):
            _diag_sub["n"] += 1           # solo NOMI dei campi (mai valori): c'è un'indicazione "numero disponibile"?
            adv = l.raw.get("advertiser") or {}
            def _chiavi_tel(o, pre=""):
                out = []
                if isinstance(o, dict):
                    for k, v in o.items():
                        if any(w in k.lower() for w in ("phone", "tel", "contact", "call", "number")):
                            out.append(f"{pre}{k}={type(v).__name__}:{'pieno' if v not in (None, '', [], {}, False) else 'vuoto'}")
                        if isinstance(v, (dict, list)) and len(pre) < 40:
                            out += _chiavi_tel(v if isinstance(v, dict) else {}, pre + k + ".")
                return out
            ph = adv.get("phone") if isinstance(adv, dict) else None
            forma = re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", ph))[:40] if isinstance(ph, str) else type(ph).__name__
            log.warning("TR subito telefono: forma=%r lunghezza=%s cifre=%s", forma, len(ph) if isinstance(ph, str) else None,
                        len(re.sub(r"\D", "", ph)) if isinstance(ph, str) else None)
        from ..core.dal_testo import completa
        for k, v in completa({"fuel": l.fuel, "year": l.year, "mileage_km": l.mileage_km}, l.title, l.description).items():
            setattr(l, k, v)                      # dati mancanti presi dal testo dell'annuncio
        try:
            normalize_fields(l)
        except Exception:
            stats["scartati_dati"] += 1
            continue
        if not l.province or l.province.upper() not in zona:
            stats[f"{l.source}:fuori_zona"] += 1
            continue
        if l.province and not l.region:
            from ..core.normalize import PROVINCE_REGION
            l.region = PROVINCE_REGION.get(l.province.upper())
        try:
            lid, ev = upsert_listing(conn, l)
        except Exception as e:
            conn.rollback()
            stats["scartati_dati"] += 1
            log.info("TR annuncio %s non salvato: %s", l.url, str(e)[:100])
            continue
        if l.source == "autoscout24":
            if tel is not None:
                conn.execute("UPDATE listings SET telefono_ok=%s, telefono_at=now() WHERE id=%s", (tel, lid))
        else:
            conn.execute("UPDATE listings SET telefono_ok=%s, telefono_at=now() WHERE id=%s",
                         (True if (tel_sub or ha_numero_testo(l.title, l.description)) else None, lid))   # Subito: "no" solo dal controllo
        conn.execute("UPDATE listings SET last_checked_at=now() WHERE id=%s", (lid,))
        stats[f"{l.source}:{ev}"] += 1
        if ev in ("prezzo", "riapparso"):
            conn.execute("UPDATE listings SET stage='nuovo' WHERE id=%s AND stage<>'nuovo'", (lid,))
        if l.source == "autoscout24" and ev == "nuovo" and doppione_altro_sito(conn, l, lid):
            stats["autoscout24:doppione"] += 1
            conn.commit()
            continue
        conn.commit()
        if ev in ("nuovo", "prezzo", "riapparso") and l.seller_type == "privato" \
                and (l.year or 0) >= anno_min and not (l.mileage_km and l.mileage_km > km_max) and (l.price_eur or 0) >= 300:
            da_valutare.append(lid)
    return da_valutare


def valuta_e_pubblica(conn, ids: list[int], stats: Counter) -> None:
    """Policy prudente subito sugli annunci nuovi: le opportunità con il numero vanno sul sito in questo giro."""
    from ..core.models import Listing
    from ..core.normalize import problem_hint
    from ..pricing import policy_v2 as P
    from ..pricing.ricalcolo import MARKET_SQL, TARGET_COLS, _j, _rischio
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    if not ids:
        return
    cfg = P.PolicyConfig.from_env()
    rows = conn.execute(f"SELECT {TARGET_COLS}, l.title, l.description, l.telefono_ok, l.damage_declared "
                        f"FROM listings l WHERE l.id = ANY(%s)", (ids,)).fetchall()
    gruppi: dict = {}
    cache_nord: dict = {}
    for r in rows:
        if r["source"] in ("subito", "autoscout24") and not r["telefono_ok"]:
            stats["valuta:senza_numero"] += 1
            continue
        if r["damage_declared"] or problem_hint(Listing(source=r["source"], source_id="", url="", title=r["title"],
                                                        description=r["description"])):
            stats["valuta:danni_nel_testo_ad_analisi"] += 1      # restano all'analisi completa (AI o a mano)
            continue
        if not (r["make"] and r["model"] and r["fuel"]):
            stats["valuta:dati_incompleti"] += 1
            continue
        key = (r["make"], r["model"], r["fuel"])
        if key not in gruppi:
            gruppi[key] = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                           for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
        t = dict(r, vehicle_identity=_j(r.get("vehicle_identity")), parts_cost_low=None, parts_cost_high=None,
                 rischio_strutturale=_rischio(r))
        soglia = scovo.soglia(int(r["price_eur"]))
        from ..pricing.ricalcolo import valuta_con_ripiego
        v = valuta_con_ripiego(conn, t, gruppi[key], soglia, cfg, cache_nord)
        v["sezione"] = "affari"
        stats[f"valuta:{v['categoria']}"] += 1
        conn.execute(
            """INSERT INTO valutazioni_policy (listing_id, policy_version, categoria, margine, riassunto, dati)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (listing_id, policy_version) DO UPDATE SET computed_at=now(),
               categoria=EXCLUDED.categoria, margine=EXCLUDED.margine, riassunto=EXCLUDED.riassunto, dati=EXCLUDED.dati""",
            (r["id"], P.POLICY_VERSION, v["categoria"], v.get("margine"), json.dumps(P.riassunto(v), default=str),
             json.dumps(v, default=str)))
        if v["categoria"] in scovo.CATEGORIE_SITO:
            conf = "affidabile" if v["categoria"] == "opportunita" else "da_verificare"
            conn.execute(
                """INSERT INTO valuations (listing_id, engine_version, private_median, resale_median, resale_prudent,
                     n_comparables, comparables_used, confidence, confidence_reasons, parts_cost_low, parts_cost_high)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0)""",
                (r["id"], P.POLICY_VERSION + "+tempo-reale", v.get("M"), v.get("M"), v.get("R_finale"), v.get("n"),
                 json.dumps(v.get("confronti") or [])[:200000], conf, (v.get("motivi") or [])[:5]))
            conn.execute("UPDATE listings SET stage='approfondito', stage_reason=%s, deep_at=now(), last_checked_at=now() "
                         "WHERE id=%s", ("opportunita" if conf == "affidabile" else "da_verificare", r["id"]))
            stats["pubblicate"] += 1
        conn.commit()


def giro_nuovi(conn) -> Counter:
    stats: Counter = Counter()
    row = conn.execute("SELECT ultimo_at FROM tempo_reale WHERE giro='nuovi'").fetchone()
    inizio = datetime.now(timezone.utc)
    dal_dt = (row["ultimo_at"] if row and row["ultimo_at"] else inizio - timedelta(minutes=int(os.environ.get("TR_PRIMO_GIRO_MIN", "60"))))
    dal_dt -= timedelta(minutes=int(os.environ.get("TR_MARGINE_MIN", "10")))       # sovrapposizione: nessun buco
    letti = []
    if os.environ.get("TR_SUBITO", "1") == "1":
        letti += _subito_nuovi(_ora_it(dal_dt))
        _diagnostica_pagina_subito([l for l in letti if l.source == "subito"][:3])
    if os.environ.get("TR_AUTOSCOUT", "1") == "1":
        try:
            letti += _autoscout_nuovi(conn)
        except Exception as e:
            log.warning("TR autoscout: %s", str(e)[:200])
    stats["letti"] = len(letti)
    ids = _salva(conn, letti, stats)
    valuta_e_pubblica(conn, ids, stats)
    conn.execute("INSERT INTO tempo_reale (giro, ultimo_at, stats) VALUES ('nuovi', %s, %s) ON CONFLICT (giro) DO UPDATE "
                 "SET ultimo_at=EXCLUDED.ultimo_at, stats=EXCLUDED.stats", (inizio, json.dumps(dict(stats))))
    conn.commit()
    return stats


def con_lucchetto(nome: str, fn, *a):
    """Un solo processo alla volta per giro (durante un aggiornamento del sito girano due copie per qualche minuto)."""
    from ..db import connect
    with connect() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK[nome],)).fetchone()["ok"]:
            return None
        try:
            return fn(conn, *a)
        finally:
            try:
                conn.rollback()
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK[nome],))
                conn.commit()
            except Exception:
                pass


def giro_telefoni_subito(conn) -> dict:
    """Una volta al giorno: rilegge le ricerche di Subito (dal più recente, TR_TEL_PAGINE pagine da 100 per regione) e
    segna per ogni annuncio già salvato se il venditore ha il numero (sì/no). Il numero non si salva mai."""
    from ..collectors.subito import API_HEADERS, API_SEARCH, REGION_CODES, SubitoCollector
    col = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    st = Counter()
    for reg, provs in REGIONI.items():
        code = REGION_CODES.get(reg) or _codici_regione.get(reg) or col._trova_regione(set(provs), 100)
        if not code:
            continue
        _codici_regione[reg] = REGION_CODES[reg] = code
        for page in range(int(os.environ.get("TR_TEL_PAGINE", "100"))):
            try:
                r = col.client.get(API_SEARCH, headers=API_HEADERS, params={"c": 2, "r": code, "t": "s", "lim": 100,
                                                                            "start": page * 100, "sort": "datedesc"})
            except Exception as e:
                log.info("TR telefoni subito %s: %s", reg, str(e)[:100])
                break
            time.sleep(float(os.environ.get("TR_TEL_PAUSA_S", "1.0")))
            if r.status_code != 200:
                st[f"http_{r.status_code}"] += 1
                break
            ads = (r.json() or {}).get("ads") or []
            if not ads:
                break
            for a in ads:                               # distribuzione dell'indicatore (valore di 1 carattere, non è un numero)
                v = ((a.get("advertiser") or {}).get("phone"))
                st[f"indicatore={v if isinstance(v, str) and len(v) <= 2 else type(v).__name__}"] += 1
                if v is not None and ha_numero_testo(a.get("subject"), a.get("body")):
                    st[f"numero_nel_testo&indicatore={v if isinstance(v, str) and len(v) <= 2 else '?'}"] += 1
            si = [str(a.get("urn")) for a in ads if a.get("urn") and telefono_subito(a)]
            no = [str(a.get("urn")) for a in ads if a.get("urn") and not telefono_subito(a)]
            st["letti"] += len(ads)
            st["con_numero"] += len(si)
            st["senza_numero"] += len(no)
            if si:
                n = conn.execute("UPDATE listings SET telefono_ok=true, telefono_at=now() WHERE source='subito' "
                                 "AND source_id = ANY(%s) AND telefono_ok IS DISTINCT FROM true", (si,)).rowcount
                st["aggiornati_si"] += n
            conn.commit()
    return dict(st)


_diag_pag = {"n": 0}   # si azzera a ogni avvio: 6 pagine di prova


def _diagnostica_pagina_subito(lst: list) -> None:
    """Diagnostica (solo nomi dei campi e forma dei valori, cifre → 9): dove la PAGINA dell'annuncio Subito dice
    che il venditore ha il numero (bottone Chiama). Mai il numero."""
    import json as _json
    from ..collectors.base import next_data
    if _diag_pag["n"] >= 6 or not lst:
        return
    from ..collectors.subito import SubitoCollector
    sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    via = "unlocker" if (sub.unlocker_zone and sub.unlocker_key) else ("proxy" if os.environ.get("SCRAPER_PROXY") else "diretto")
    for l in lst:
        _diag_pag["n"] += 1
        try:
            r = sub._get(l.url)
        except Exception as e:
            log.warning("TR subito pagina: errore %s", str(e)[:100])
            continue
        data = next_data(r.text) if r.status_code == 200 else None
        trov = []

        def scan(o, path=""):
            if len(trov) > 25:
                return
            if isinstance(o, dict):
                for k, v in o.items():
                    p = f"{path}.{k}" if path else k
                    if any(w in k.lower() for w in ("phone", "tel", "call", "contact")):
                        forma = re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", _json.dumps(v)))[:50] if not isinstance(v, (dict, list)) else type(v).__name__
                        trov.append(f"{p}={forma}")
                    scan(v, p)
            elif isinstance(o, list):
                for i, v in enumerate(o[:5]):
                    scan(v, f"{path}[{i}]")
        scan(data or {})
        low = r.text.lower()
        log.warning("TR subito pagina (%s): http=%s byte=%s next_data=%s bottone_chiama=%s parole=%s campi=%s", via,
                    r.status_code, len(r.text), bool(data), ("mostra numero" in low or "chiama" in low),
                    [w for w in ("captcha", "datadome", "cloudflare", "access denied", "phone", "telefono", "chiama",
                                 "messaggio", "__next_data__") if w in low], trov[:20])


_SUB_PAGINA = {"n": 0}


def subito_telefono_pagina(url: str) -> tuple[str, bool | None]:
    """Apre la pagina di UN annuncio Subito (Web Unlocker di Bright Data se configurato, altrimenti proxy o diretto) e
    dice se c'è il bottone Chiama. Ritorna (stato annuncio, telefono sì/no/None=non leggibile). Il numero non si legge."""
    from ..collectors.base import next_data
    from ..collectors.subito import SubitoCollector
    sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
    try:
        r = sub._get(url)
    except Exception as e:
        log.info("TR subito pagina %s: %s", url, str(e)[:100])
        return "errore", None
    if r.status_code in (404, 410):
        return "scomparso", None
    if r.status_code != 200:
        return "errore", None
    low = r.text.lower()
    if "annuncio non è più disponibile" in low or "questo annuncio è scaduto" in low:
        return "scomparso", None
    data = next_data(r.text)
    tel = telefono_da_pagina(data, low)
    if _SUB_PAGINA["n"] < 5:
        _SUB_PAGINA["n"] += 1
        log.warning("TR subito pagina controllo: dati=%s esito=%s", bool(data), tel)
    return "attivo", tel


def telefono_da_pagina(data: dict | None, low: str) -> bool | None:
    """Indicatore del bottone Chiama nella pagina. None se la pagina non ha i dati (blocco anti-bot)."""
    if not data:
        return None
    flags = []

    def scan(o, depth=0):
        if depth > 12:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                kl = k.lower()
                if kl in ("phone", "hasphone", "showphone", "phonevisible", "isphonevisible", "phone_visible",
                          "hasphonenumber", "phonenumberavailable"):
                    if isinstance(v, bool):
                        flags.append(v)
                    elif isinstance(v, str) and v.strip():
                        flags.append(v.strip() not in ("0", "false", "no"))
                    elif isinstance(v, (int, float)):
                        flags.append(v != 0)
                scan(v, depth + 1)
        elif isinstance(o, list):
            for v in o[:30]:
                scan(v, depth + 1)
    scan(data)
    if any(flags):
        return True
    if "mostra numero" in low or 'aria-label="chiama' in low:
        return True
    return False if flags else None
