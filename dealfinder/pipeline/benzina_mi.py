"""20/10 (cliente): più auto a BENZINA da 3.000 a 15.000 € in provincia di MILANO sul sito, con buon margine.

Giro una tantum (BENZ_GIRO=n):
1. raccoglie tutto il nuovo di Subito (provincia di Milano) e di AutoScout24 (entro 30 km da Milano, privati e
   commercianti) da 3.000 a 15.000 €;
2. sceglie le auto a benzina della provincia di Milano molto sotto il prezzo delle auto uguali;
3. per quelle AutoScout24 controlla se l'annuncio mostra il numero del venditore;
4. le valuta con la policy prudente di sempre (nessuna regola allentata): quelle buone vanno sul sito.
Nei log: BENZ {...} e BENZ_FINE.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

log = logging.getLogger("benzina_mi")
# entro circa 100 km da Milano (cliente 20/10)
PMIN, PMAX, SOLO_P = 3000, 15000, False
PROVINCE_100 = [p.strip() for p in os.environ.get("BENZ_PROVINCE", "MI,MB,VA,CO,LC,BG,LO,PV,CR,NO,VB,PC,AL,VC,BS").split(",")]

CANDIDATI_SQL = """
WITH g AS (                     -- prezzo mediano per marca, modello, alimentazione e anno: una sola passata
  SELECT make, model, fuel, year, count(*) AS n, percentile_cont(.5) WITHIN GROUP (ORDER BY price_eur) AS med
  FROM listings
  WHERE price_eur BETWEEN 800 AND %(gmax)s AND year >= 2005 AND make IS NOT NULL AND model IS NOT NULL
  GROUP BY make, model, fuel, year
)
SELECT t.id, t.source, t.url, t.price_eur, t.telefono_ok, t.make, t.model, g.n, g.med
FROM listings t
JOIN g ON g.make=t.make AND g.model=t.model AND g.fuel IS NOT DISTINCT FROM t.fuel AND g.year=t.year
WHERE t.status='attivo' AND upper(t.province) = ANY(%(prov)s)
  AND (%(tutti_carb)s OR t.fuel ILIKE 'benzina%%')   -- 23/10: con BENZ_TUTTI_CARB=1 tutte le alimentazioni (tetto diesel 30 per cento sul sito)
  AND t.price_eur BETWEEN %(pmin)s AND %(pmax)s AND t.year >= 2007
  AND (%(solo_privati)s = false OR t.seller_type = 'privato') AND coalesce(t.mileage_km, 0) <= %(kmmax)s
  AND coalesce(t.damage_class,'') NOT IN ('grave','alto_rischio','medio')
  AND t.stage IS DISTINCT FROM 'approfondito'
  AND g.n >= 5 AND t.price_eur < g.med * %(soglia)s
  AND (NOT %(solo_nuove)s OR NOT EXISTS (SELECT 1 FROM valutazioni_policy vp WHERE vp.listing_id = t.id
                                          AND vp.computed_at > now() - interval '3 days'))   -- 2/11: solo mai valutate di recente
ORDER BY (t.price_eur > %(util)s) ASC, t.price_eur / g.med ASC   -- 22/10: utilitarie per prime (BENZ_UTIL_PRIMA)
LIMIT %(lim)s"""


def giro() -> dict:
    from ..db import connect
    from . import tempo_reale as tr
    from ..collectors.subito import SubitoCollector
    from ..collectors.autoscout import AutoScoutCollector
    st: Counter = Counter()
    global PMIN, PMAX, SOLO_P, PROVINCE_100
    PROVINCE_100 = [p.strip() for p in os.environ.get("BENZ_PROVINCE", ",".join(PROVINCE_100)).split(",") if p.strip()]
    PMIN, PMAX = int(os.environ.get("BENZ_MIN", "3000")), int(os.environ.get("BENZ_PMAX", "15000"))
    SOLO_P = os.environ.get("BENZ_SOLO_PRIVATI") == "1"
    # 21/10 sera (cliente): si raccolgono TUTTI gli annunci (anche concessionari) per la base prezzi del modello;
    # sul sito vanno comunque solo i privati (le candidate restano filtrate con SOLO_P)
    racc_p = SOLO_P and os.environ.get("BENZ_RACCOLTA_TUTTI") != "1"
    # salvataggio a pacchetti: in memoria non resta mai più di un pacchetto di annunci (512 MB di memoria)
    with connect() as conn:
        pacco: list = []

        def salva(forza=False):
            if pacco and (forza or len(pacco) >= 150):
                tr._salva(conn, pacco, st)
                conn.commit()
                pacco.clear()

        solo_valuta = os.environ.get("BENZ_SOLO_VALUTA") == "1"   # annunci già raccolti: si passa subito alla scelta
        # 1a. Subito: Lombardia entro 100 km, dal più recente
        sub = SubitoCollector(proxy=os.environ.get("SCRAPER_PROXY") or None)
        sub.min_delay_s, sub.max_delay_s = 0.4, 0.9
        n = 0
        try:
            for l in ([] if solo_valuta else sub.search_recenti("lombardia", PROVINCE_100, os.environ.get("BENZ_DAL", "2026-08-01 00:00"),
                                        max_pages=int(os.environ.get("BENZ_SUB_PAGINE", "300")))):
                if l.price_eur and PMIN <= l.price_eur <= PMAX and (not racc_p or l.seller_type == "privato"):
                    pacco.append(l); n += 1
                    salva()
        except Exception as e:
            log.warning("BENZ subito: %s", str(e)[:200])
        salva(True)
        # 1a-bis (21/10 sera, cliente: "tutti gli annunci del mercato, solo privati, a benzina"): raccolta profonda
        # di Subito provincia per provincia e fascia di prezzo per fascia (la ricerca per data si ferma prima),
        # in Lombardia, Piemonte ed Emilia entro il raggio
        if not solo_valuta and os.environ.get("BENZ_SUB_PROFONDA") == "1":
            passo = int(os.environ.get("BENZ_SUB_PASSO", "500"))
            fasce = [(a, min(a + passo, PMAX)) for a in range(PMIN, PMAX, passo)]
            regioni = [(4, ["MI", "MB", "VA", "CO", "LC", "BG", "LO", "PV", "CR", "BS", "SO", "MN"]),
                       (2, ["NO", "VB", "VC", "BI", "AL", "AT", "TO"]), (8, ["PC", "PR", "RE"]),
                       (int(os.environ.get("SUBITO_CODICE_LIGURIA", "3")), ["GE"])]   # 23/10: zona 150 km del sito
            for reg, provs in regioni:
                provs = [p for p in provs if p in PROVINCE_100]
                if not provs:
                    continue
                try:
                    for l in sub._deep({"price_bands": fasce, "band_pages": int(os.environ.get("BENZ_SUB_PAG_FASCIA", "30"))},
                                       set(provs), reg, 100):
                        if (l.price_eur and PMIN <= l.price_eur <= PMAX and (not racc_p or l.seller_type == "privato")
                                and (os.environ.get("BENZ_TUTTI_CARB", "1") == "1" or "benzina" in (l.fuel or "").lower())):
                            pacco.append(l); n += 1
                            salva()
                except Exception as e:
                    log.warning("BENZ subito profonda regione %s: %s", reg, str(e)[:200])
                salva(True)
                log.warning("BENZ subito profonda regione %s: letti finora %d", reg, n)
        st["letti_subito"] = n
        log.warning("BENZ subito letti %d (%d-%d €)", n, PMIN, PMAX)
        # 1b. AutoScout24: entro 100 km da Milano, a fasce di 500 €, più fasce insieme
        from .lusso import bande_parallele
        os.environ["AUTOSCOUT_CAP"], os.environ["AUTOSCOUT_RAGGIO"] = os.environ.get("BENZ_CAP", "20121"), os.environ.get("BENZ_RAGGIO", "100")   # 28/10: BENZ_CAP (es. 24064 Grumello)
        os.environ.pop("PASSI_JOB", None)
        n = 0
        try:
            for l in ([] if solo_valuta else bande_parallele(PMIN, PMAX, int(os.environ.get("BENZ_PASSO", "500")), [("P", "privato")] if racc_p else [("P", "privato"), ("D", "commerciante")], workers=int(os.environ.get("BENZ_PARALLELI", "4")))):
                pacco.append(l); n += 1
                salva()
        except Exception as e:
            log.warning("BENZ autoscout: %s", str(e)[:200])
        salva(True)
        # 31/10 (cliente: "più a fondo sul mercato, senza cambiare regole"): Milano + 130 km non copre tutta la zona
        # (Mantova e la bassa bresciana sono oltre): ricerche AutoScout24 in più da altri centri (BENZ_CAP_EXTRA,
        # "cap:raggio,..."). Stesse fasce, stessi venditori; gli annunci fuori zona vengono comunque scartati al salvataggio.
        for voce in [v.strip() for v in os.environ.get("BENZ_CAP_EXTRA", "").split(",") if ":" in v]:
            cap, rag = voce.split(":", 1)
            os.environ["AUTOSCOUT_CAP"], os.environ["AUTOSCOUT_RAGGIO"] = cap.strip(), rag.strip()
            n0 = n
            try:
                for l in ([] if solo_valuta else bande_parallele(PMIN, PMAX, int(os.environ.get("BENZ_PASSO", "500")), [("P", "privato")] if racc_p else [("P", "privato"), ("D", "commerciante")], workers=int(os.environ.get("BENZ_PARALLELI", "4")))):
                    pacco.append(l); n += 1
                    salva()
            except Exception as e:
                log.warning("BENZ autoscout %s: %s", voce, str(e)[:200])
            salva(True)
            st[f"letti_autoscout_{cap.strip()}"] = n - n0
            log.warning("BENZ autoscout centro %s letti %d", voce, n - n0)
        st["letti_autoscout"] = n
        log.warning("BENZ autoscout letti %d", n)
        # 2/11 (cliente: "concentra gli scraper sulle auto che ti ho detto"): giro mirato sulle BENZINA economiche con pochi
        # km (BENZ_FOCUS=1): AutoScout24 solo benzina, fino a FOCUS_KM km, fasce di prezzo strette (più pagine lette),
        # raggio FOCUS_RAGGIO da Sesto; poi Subito profondo nelle stesse fasce. Solo privati. Le regole di margine non cambiano.
        if not solo_valuta and os.environ.get("BENZ_FOCUS") == "1":
            fmin, fmax = int(os.environ.get("FOCUS_MIN", "1000")), int(os.environ.get("FOCUS_MAX", "10000"))
            fkm = int(os.environ.get("FOCUS_KM", "100000"))
            salvati = {k: os.environ.get(k) for k in ("AUTOSCOUT_CAP", "AUTOSCOUT_RAGGIO", "AUTOSCOUT_KM_MAX", "AUTOSCOUT_CARB", "PASSI_JOB")}
            os.environ.update({"AUTOSCOUT_CAP": os.environ.get("FOCUS_CAP", "20099"), "AUTOSCOUT_RAGGIO": os.environ.get("FOCUS_RAGGIO", "200"),
                               "AUTOSCOUT_KM_MAX": str(fkm), "AUTOSCOUT_CARB": "B",
                               "PASSI_JOB": "focus:" + time.strftime("%Y%m%d") + str(int(time.strftime("%H")) // 6)})
            nf = 0
            try:
                for l in bande_parallele(fmin, fmax, int(os.environ.get("FOCUS_PASSO", "250")), [("P", "privato")],
                                         workers=int(os.environ.get("BENZ_PARALLELI", "3"))):
                    pacco.append(l); nf += 1
                    salva()
            except Exception as e:
                log.warning("BENZ focus autoscout: %s", str(e)[:200])
            salva(True)
            st["focus_autoscout"] = nf
            log.warning("BENZ focus AutoScout benzina %d-%d €, <= %d km: letti %d", fmin, fmax, fkm, nf)
            ns = 0
            if os.environ.get("FOCUS_SUBITO", "1") == "1":
                passo_s = int(os.environ.get("FOCUS_SUB_PASSO", "500"))
                fasce = [(a, min(a + passo_s, fmax)) for a in range(fmin, fmax, passo_s)]
                regioni = [(4, ["MI", "MB", "VA", "CO", "LC", "BG", "LO", "PV", "CR", "BS", "SO", "MN"]),
                           (2, ["NO", "VB", "VC", "BI", "AL", "AT", "TO", "CN"]), (8, ["PC", "PR", "RE", "MO"]),
                           (int(os.environ.get("SUBITO_CODICE_LIGURIA", "3")), ["GE", "SV", "SP"])]
                for reg, provs in regioni:
                    provs = [p for p in provs if p in PROVINCE_100]
                    if not provs:
                        continue
                    try:
                        for l in sub._deep({"price_bands": fasce, "band_pages": int(os.environ.get("FOCUS_SUB_PAGINE", "20"))},
                                           set(provs), reg, 100):
                            if (l.seller_type == "privato" and "benzina" in (l.fuel or "").lower()
                                    and (l.mileage_km or 0) <= fkm and l.price_eur and fmin <= l.price_eur <= fmax):
                                pacco.append(l); ns += 1
                                salva()
                    except Exception as e:
                        log.warning("BENZ focus subito regione %s: %s", reg, str(e)[:200])
                    salva(True)
            st["focus_subito"] = ns
            log.warning("BENZ focus Subito benzina: tenuti %d", ns)
            for k, v in salvati.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        # 2. candidate: benzina, provincia di Milano, molto sotto il prezzo delle auto uguali
        conn.execute("SET statement_timeout = '900s'")
        cand = conn.execute(CANDIDATI_SQL, {"prov": PROVINCE_100, "soglia": float(os.environ.get("BENZ_SOGLIA", "0.85")),
                                            "lim": int(os.environ.get("BENZ_MAX", "500")), "pmin": PMIN, "pmax": PMAX, "solo_privati": SOLO_P,
                                            "gmax": int(os.environ.get("BENZ_G_MAX", "60000")),
                                            "util": int(os.environ.get("BENZ_UTIL_PRIMA", "1000000")),
                                            "tutti_carb": os.environ.get("BENZ_TUTTI_CARB", "1") == "1",
                                            "kmmax": int(os.environ.get("BENZ_KM_MAX", "180000")),
                                            "solo_nuove": os.environ.get("BENZ_SOLO_NUOVE") == "1"}).fetchall()
        # 1/11 (cliente: "il software può fare di più"): le auto della zona che hanno CAMBIATO PREZZO nelle ultime ore
        # (di solito un ribasso) si rivalutano subito in questo giro, anche se non sono tra le prime BENZ_MAX candidate
        if os.environ.get("BENZ_CAMBI_PREZZO", "1") == "1":
            try:
                gia = {r["id"] for r in cand}
                cambi = conn.execute(
                    """SELECT t.id, t.source, t.url, t.price_eur, t.telefono_ok, t.make, t.model, NULL::int AS n, NULL::float AS med
                       FROM listings t
                       WHERE t.id IN (SELECT DISTINCT listing_id FROM price_events
                                      WHERE event = 'prezzo' AND at > now() - make_interval(hours => %(ore)s))
                         AND t.status = 'attivo' AND upper(t.province) = ANY(%(prov)s)
                         AND t.price_eur BETWEEN 1000 AND %(pmax)s AND coalesce(t.year, 2007) >= 2007
                         AND coalesce(t.mileage_km, 0) <= %(kmmax)s
                         AND (%(tutti_carb)s OR t.fuel ILIKE 'benzina%%')
                         AND coalesce(t.damage_class,'') NOT IN ('grave','alto_rischio','medio')
                       LIMIT %(lim)s""",
                    {"ore": int(os.environ.get("BENZ_CAMBI_ORE", "8")), "prov": PROVINCE_100, "pmax": PMAX,
                     "lim": int(os.environ.get("BENZ_CAMBI_MAX", "1500")),
                     "kmmax": int(os.environ.get("BENZ_KM_MAX", "180000")), "tutti_carb": os.environ.get("BENZ_TUTTI_CARB", "1") == "1"}).fetchall()
                nuovi_c = [r for r in cambi if r["id"] not in gia]
                cand = nuovi_c + list(cand)
                st["cambi_prezzo"] = len(cambi)
                st["cambi_prezzo_aggiunti"] = len(nuovi_c)
                log.warning("BENZ cambi di prezzo nelle ultime ore: %d (aggiunti alle candidate %d)", len(cambi), len(nuovi_c))
            except Exception as e:
                conn.rollback()
                log.warning("BENZ cambi di prezzo non letti: %s", str(e)[:200])
        # 1/11 (cliente: "prendi le auto con il super prezzo e verifica se sono davvero opportunità"): le auto di
        # privati della zona che AutoScout24 giudica "super prezzo" o "prezzo molto basso" e che il nostro calcolo non ha
        # mai valutato (erano 2.572 + 525) passano sempre dal calcolo prudente. Decidono le nostre regole, non AutoScout.
        if os.environ.get("BENZ_AS24_TOP", "1") == "1":
            try:
                gia = {r["id"] for r in cand}
                top = conn.execute(
                    """SELECT t.id, t.source, t.url, t.price_eur, t.telefono_ok, t.make, t.model, NULL::int AS n, NULL::float AS med
                       FROM listings t
                       WHERE t.source = 'autoscout24' AND t.status = 'attivo' AND t.seller_type = 'privato'
                         AND upper(t.province) = ANY(%(prov)s)
                         AND (t.price_flags && ARRAY['as24_prezzo:top-price', 'as24_prezzo:toolow-price']::text[])
                         AND t.price_eur BETWEEN 1000 AND %(pmax)s AND coalesce(t.year, 2007) >= 2007
                         AND coalesce(t.mileage_km, 0) <= %(kmmax)s
                         AND (%(tutti_carb)s OR t.fuel ILIKE 'benzina%%')
                         AND coalesce(t.damage_class,'') NOT IN ('grave','alto_rischio','medio')
                         AND t.stage IS DISTINCT FROM 'approfondito'
                         AND NOT EXISTS (SELECT 1 FROM valutazioni_policy vp WHERE vp.listing_id = t.id
                                         AND vp.computed_at > t.last_seen_at - interval '3 days')
                       ORDER BY t.first_seen_at DESC
                       LIMIT %(lim)s""",
                    {"prov": PROVINCE_100, "pmax": PMAX, "lim": int(os.environ.get("BENZ_AS24_MAX", "1500")),
                     "kmmax": int(os.environ.get("BENZ_KM_MAX", "180000")), "tutti_carb": os.environ.get("BENZ_TUTTI_CARB", "1") == "1"}).fetchall()
                agg = [r for r in top if r["id"] not in gia]
                cand = agg + list(cand)
                st["as24_super_prezzo"] = len(top)
                st["as24_super_prezzo_aggiunte"] = len(agg)
                log.warning("BENZ AutoScout 'super prezzo' mai valutate: %d (aggiunte %d)", len(top), len(agg))
            except Exception as e:
                conn.rollback()
                log.warning("BENZ super prezzo non lette: %s", str(e)[:200])
        # 2/11 (cliente: "confronti allargati"): BENZ_SOLO_INSUFF=1 rivaluta SOLO le auto della zona rimaste in
        # "dati insufficienti" (ultima valutazione), ora che la policy ammette i confronti ampliati (anno ±2, km ±30.000)
        if os.environ.get("BENZ_SOLO_INSUFF") == "1":
            try:
                cand = conn.execute(
                    """SELECT t.id, t.source, t.url, t.price_eur, t.telefono_ok, t.make, t.model, NULL::int AS n, NULL::float AS med
                       FROM listings t
                       JOIN LATERAL (SELECT categoria FROM valutazioni_policy vp WHERE vp.listing_id = t.id
                                     ORDER BY computed_at DESC LIMIT 1) v ON true
                       WHERE v.categoria = 'dati_insufficienti' AND t.status = 'attivo' AND t.seller_type = 'privato'
                         AND upper(t.province) = ANY(%(prov)s) AND t.price_eur BETWEEN %(pmin)s AND %(pmax)s
                         AND coalesce(t.year, 2007) >= 2007 AND coalesce(t.mileage_km, 0) <= %(kmmax)s
                         AND (%(tutti_carb)s OR t.fuel ILIKE 'benzina%%')
                         AND coalesce(t.damage_class,'') NOT IN ('grave','alto_rischio','medio')
                       ORDER BY t.price_eur ASC LIMIT %(lim)s""",
                    {"prov": PROVINCE_100, "pmin": PMIN, "pmax": PMAX, "lim": int(os.environ.get("BENZ_MAX", "2000")),
                     "kmmax": int(os.environ.get("BENZ_KM_MAX", "180000")),
                     "tutti_carb": os.environ.get("BENZ_TUTTI_CARB", "1") == "1"}).fetchall()
                log.warning("BENZ dati insufficienti da rivalutare con i confronti ampliati: %d", len(cand))
            except Exception as e:
                conn.rollback()
                log.warning("BENZ dati insufficienti non letti: %s", str(e)[:200])
        conn.execute("SET statement_timeout = 0")
        st["candidate"] = len(cand)
        log.warning("BENZ candidate %d", len(cand))
        # 3. numero del venditore sugli annunci AutoScout24 non ancora controllati
        from concurrent.futures import ThreadPoolExecutor
        da_fare = [r for r in cand if r["source"] == "autoscout24" and r["telefono_ok"] is None]

        def numero(r):
            cl = tr._client_as()
            stato, det = tr._as_dettaglio(cl, r["url"])
            return r["id"], stato, (tr.telefono_autoscout(det) if det is not None else None)

        with ThreadPoolExecutor(int(os.environ.get("BENZ_TEL_PARALLELI", "6"))) as ex:
            for lid, stato, tel in ex.map(numero, da_fare):
                if stato == "scomparso":
                    st["autoscout_sparite"] += 1
                elif tel is not None or stato == "attivo":
                    conn.execute("UPDATE listings SET telefono_ok=%s, telefono_at=now() WHERE id=%s", (tel, lid))
                    st[f"autoscout_telefono_{tel}"] += 1
        conn.commit()
        # 4. valutazione prudente e pubblicazione
        # 28/10: a pezzi (stessa marca/modello insieme), memoria liberata a ogni pezzo: con 1.500 candidate
        # in un colpo solo il web (512 MB) andava fuori memoria e ripartiva da capo
        import gc
        cand = sorted(cand, key=lambda r: (str(r.get("make") or ""), str(r.get("model") or "")))
        ids = [r["id"] for r in cand]
        del cand
        pezzo = int(os.environ.get("BENZ_VAL_PEZZO", "0")) or len(ids) or 1
        for i in range(0, len(ids), pezzo):
            tr.valuta_e_pubblica(conn, ids[i:i + pezzo], st)
            conn.commit()
            gc.collect()
            if pezzo < len(ids):
                log.warning("BENZ valutate %d/%d, pubblicate finora %d", min(i + pezzo, len(ids)), len(ids), st.get("pubblicate", 0))
    log.warning("BENZ_FINE %s", json.dumps(dict(st)))
    return dict(st)
