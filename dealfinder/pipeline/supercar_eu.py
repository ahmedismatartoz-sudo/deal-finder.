"""28/10 sera (cliente): "ricerca super avanzata in tutta Europa di supercar da 70k in poi, almeno 100 sul sito".

raccolta(): AutoScout24 in 8 paesi (D, A, B, E, F, I, L, NL), marche sportive e di lusso, da SUPERCAR_MIN € (70.000)
    in su, fino a 170.000 km; privati E commercianti (i commercianti servono come confronti di mercato).
    Ogni paese si cerca a parte: il paese dell'annuncio è quello della ricerca (region = "estero:XX").
valuta(): solo auto di PRIVATI fino a 150.000 km, con il metodo del cliente sui confronti europei:
    stessa marca/modello/alimentazione, anno ±1, km ±20.000, stessa potenza (±8%) e cambio, attive o vendute
    negli ultimi 60 giorni; almeno 5 confronti; M = mediana, R = min(M×0,95, P25);
    margine = R − prezzo − costi (200 € + SUPERCAR_COSTI_ESTERO per l'import, 2.500 €, se l'auto è all'estero).
    Con la chiave AI, il giudice toglie i confronti che non sono la stessa versione e si rifà il conto.
    Risultati nella tabella supercar_eu; sul sito vanno quelle con margine ≥ SUPERCAR_MARGINE_MIN (5.000 €).
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from statistics import median

log = logging.getLogger("supercar_eu")

import re
from ..ai.supercar_vero import LEASING_RE  # 29/10: subentro leasing / rate / noleggio: prezzo non reale

PAESI = {"D": "Germania", "A": "Austria", "B": "Belgio", "E": "Spagna", "F": "Francia", "I": "Italia",
         "L": "Lussemburgo", "NL": "Paesi Bassi"}
MARCHE = ["ferrari", "lamborghini", "mclaren", "porsche", "aston-martin", "bentley", "rolls-royce", "maserati",
          "bugatti", "lotus", "mercedes-benz", "bmw", "audi", "land-rover", "pagani", "koenigsegg", "maybach", "alpina"]


def _collettore(marca: str, paese: str):
    from ..collectors.autoscout import BASE, AutoScoutCollector

    class AutoScoutEU(AutoScoutCollector):
        def search_url(self, lo, hi, page, custtype=None, years=None):
            y0, y1 = years or (int(os.environ.get("SUPERCAR_ANNO_MIN", "2000")), None)
            anni = f"&fregfrom={y0}" + (f"&fregto={y1}" if y1 else "")
            return (f"{BASE}/lst/{marca}?atype=C&cy={paese}&ustate=N%2CU&damaged_listing=exclude&custtype={custtype or 'P'}"
                    f"&kmto={int(os.environ.get('SUPERCAR_KM_RACCOLTA', '170000'))}&pricefrom={lo}&priceto={hi}{anni}"
                    f"&sort=age&desc=1&page={page}")

        def _emit(self, items, ctype, seller):
            for l in super()._emit(items, ctype, seller):
                if paese != "I":                       # il paese è quello della ricerca: mai una provincia italiana
                    l.province, l.region = None, f"estero:{paese}"
                yield l

    c = AutoScoutEU()
    c.min_delay_s = float(os.environ.get("SUPERCAR_PAUSA_MIN", "0.3"))
    c.max_delay_s = float(os.environ.get("SUPERCAR_PAUSA_MAX", "0.8"))
    return c


def raccolta() -> dict:
    from concurrent.futures import ThreadPoolExecutor
    from ..core.normalize import normalize_fields
    from ..db import connect, upsert_listing
    st = Counter()
    lo = int(os.environ.get("SUPERCAR_MIN_RACCOLTA", "60000"))           # un po' sotto: confronti per le auto da 70k
    hi = int(os.environ.get("SUPERCAR_MAX", "100000000"))   # 30/10 (cliente): nessun tetto, anche auto da milioni
    passo = int(os.environ.get("SUPERCAR_PASSO", "40000"))
    paesi = [p.strip() for p in os.environ.get("SUPERCAR_PAESI", ",".join(PAESI)).split(",") if p.strip()]
    marche = [m.strip() for m in os.environ.get("SUPERCAR_MARCHE", ",".join(MARCHE)).split(",") if m.strip()]
    venditori = [("P", "privato"), ("D", "commerciante")]
    # fasce di prezzo fino a 300.000 €, poi una fascia unica (poche auto); le fasce troppo piene si dividono da sole
    fasce = [(a, min(a + passo - 1, 299999)) for a in range(lo, 300000, passo)] + [(300000, hi)]
    compiti = [(m, p, a, b, ct, sel) for m in marche for p in paesi for ct, sel in venditori for a, b in fasce]
    t0 = time.time()

    def leggi(c):
        """Una fascia: ogni annuncio si salva appena letto (memoria: mai migliaia di annunci insieme)."""
        m, p, a, b, ct, sel = c
        col = _collettore(m, p)
        loc = Counter()
        try:
            with connect() as cc:
                for l in col._band(a, b, ct, sel):
                    l.raw = None
                    if not l.price_eur or l.price_eur < lo:
                        continue
                    try:
                        _, ev = upsert_listing(cc, normalize_fields(l))
                        cc.commit()
                        loc[ev] += 1
                        loc[l.seller_type] += 1
                    except Exception as e:
                        cc.rollback()
                        loc["errore"] += 1
                        if loc["errore"] < 3:
                            log.warning("SUPERCAR non salvato %s: %s", l.url, str(e)[:160])
                    del l
        except Exception as e:
            log.warning("SUPERCAR fascia %s: %s", c, str(e)[:120])
            return loc, True
        return loc, col.blocked

    workers = int(os.environ.get("SUPERCAR_PARALLELI", "4"))
    # si riprende da dove era arrivata dopo un riavvio (stesso giro: blocco di SUPERCAR_EU_ORE ore)
    giro = os.environ.get("SUPERCAR_GIRO") or str(int(time.time() // (float(os.environ.get("SUPERCAR_EU_ORE", "6")) * 3600)))
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS supercar_passi (giro TEXT, chiave TEXT, PRIMARY KEY (giro, chiave))")
        conn.commit()
        fatte = {r["chiave"] for r in conn.execute("SELECT chiave FROM supercar_passi WHERE giro=%s", (giro,)).fetchall()}
    chiave = lambda c: "|".join(map(str, c))
    if fatte:
        log.warning("SUPERCAR_RACCOLTA riprendo il giro %s: %d fasce già fatte", giro, len(fatte))
    compiti = [c for c in compiti if chiave(c) not in fatte]
    with connect() as conn, ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(compiti), workers):
            gruppo = compiti[i:i + workers]
            for c, (loc, blocc) in zip(gruppo, ex.map(leggi, gruppo)):
                st["bloccate"] += bool(blocc)
                st.update(loc)
                if not blocc:
                    conn.execute("INSERT INTO supercar_passi (giro, chiave) VALUES (%s,%s) ON CONFLICT DO NOTHING", (giro, chiave(c)))
                    conn.commit()
            import gc
            gc.collect()
            if (i // workers) % 25 == 0:
                log.warning("SUPERCAR_RACCOLTA %d/%d fasce, %s, %ds", i + workers, len(compiti), dict(st), time.time() - t0)
    log.warning("SUPERCAR_RACCOLTA_FINE %s in %ds", json.dumps(dict(st)), time.time() - t0)
    return dict(st)


CANDIDATI_SQL = """
SELECT id, make, model, version_raw, title, description, year, mileage_km, fuel, gearbox, power_kw, price_eur,
       region, province, city, price_flags, damage_declared
FROM listings
WHERE source='autoscout24' AND status='attivo' AND seller_type='privato'
  AND price_eur >= %(min)s AND COALESCE(mileage_km, 0) <= %(km)s
  AND last_seen_at > now() - interval '3 days'
  AND lower(make) = ANY(%(marche)s)
  AND NOT COALESCE(damage_declared, false)
  AND COALESCE(stage_reason, '') NOT IN ('doppione', 'truffa', 'dati_incoerenti', 'prezzo_finto')
ORDER BY price_eur DESC
"""

CONFRONTI_SQL = """
SELECT id, version_raw, year, mileage_km, gearbox, power_kw, price_eur, seller_type, region, province
FROM listings
WHERE lower(make) = lower(%(make)s) AND lower(model) = lower(%(model)s) AND fuel IS NOT DISTINCT FROM %(fuel)s
  AND id <> %(id)s AND price_eur > 0
  AND year BETWEEN %(y0)s AND %(y1)s
  AND (%(km)s::int IS NULL OR mileage_km BETWEEN %(km)s - 20000 AND %(km)s + 20000)
  AND (status = 'attivo' OR (status = 'scomparso' AND disappeared_at > now() - interval '60 days'))
  AND NOT COALESCE(damage_declared, false)
  AND NOT ('leasing_o_rata' = ANY(COALESCE(price_flags, ARRAY[]::text[])))
LIMIT 300
"""


def _cambio(g):
    g = (g or "").lower()
    return "auto" if g.startswith(("auto", "semi", "seq", "dopp")) else "man" if g.startswith("man") else None


def _conto(prezzo: int, prezzi: list[int], costi: int) -> dict:
    prezzi = sorted(prezzi)
    M = median(prezzi)
    q = prezzi[max(0, int(round(0.25 * (len(prezzi) - 1))))]
    R = min(M * 0.95, q)
    return {"M": int(M), "P25": int(q), "R": int(R), "n": len(prezzi), "margine": int(R - prezzo - costi)}


def valuta() -> dict:
    from ..db import connect
    st = Counter()
    pmin = int(os.environ.get("SUPERCAR_MIN", "70000"))
    soglia = int(os.environ.get("SUPERCAR_MARGINE_MIN", os.environ.get("ALTA_MARGINE_MIN", "5000")))
    costi_base = int(os.environ.get("POLICY_COSTO_FISSO", "200"))
    costi_est = int(os.environ.get("SUPERCAR_COSTI_ESTERO", "2500"))
    nmin = int(os.environ.get("SUPERCAR_NMIN", "5"))
    kw_tol = float(os.environ.get("SUPERCAR_KW_TOL", "0.08"))
    usa_ai = os.environ.get("SUPERCAR_GIUDICE_AI", "1") == "1"
    marche = [m.replace("-", " ") for m in MARCHE] + MARCHE
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS supercar_eu (
            listing_id BIGINT PRIMARY KEY, m INTEGER, r INTEGER, p25 INTEGER, n INTEGER, margine INTEGER,
            costi INTEGER, categoria TEXT, paese TEXT, dati JSONB, computed_at TIMESTAMPTZ DEFAULT now())""")
        conn.commit()
        cand = conn.execute(CANDIDATI_SQL, {"min": pmin, "km": int(os.environ.get("SUPERCAR_KM_MAX", "150000")),
                                            "marche": marche}).fetchall()
        log.warning("SUPERCAR_VALUTA %d auto di privati da %d € in su", len(cand), pmin)
        for t in cand:
            t = dict(t)
            estero = str(t.get("region") or "").startswith("estero:")
            paese = PAESI.get(str(t["region"]).split(":", 1)[1], str(t["region"])) if estero else "Italia"
            costi = costi_base + (costi_est if estero else 0)
            if not t.get("model") or not t.get("year"):
                st["dati_mancanti"] += 1
                continue
            testo = f"{t.get('title') or ''} {t.get('version_raw') or ''} {(t.get('description') or '')[:1500]}".lower()
            if LEASING_RE.search(testo):
                st["leasing"] += 1                               # subentro leasing / noleggio: il prezzo non è quello d'acquisto
                conn.execute("""INSERT INTO supercar_eu (listing_id, categoria, paese, computed_at) VALUES (%s,'leasing',%s,now())
                                ON CONFLICT (listing_id) DO UPDATE SET categoria='leasing', computed_at=now()""", (t["id"], paese))
                continue
            rows = conn.execute(CONFRONTI_SQL, {"make": t["make"], "model": t["model"], "fuel": t["fuel"], "id": t["id"],
                                                "y0": t["year"] - 1, "y1": t["year"] + 1, "km": t.get("mileage_km")}).fetchall()
            conf = []
            for c in rows:
                if t.get("power_kw") and c.get("power_kw") and abs(c["power_kw"] - t["power_kw"]) > kw_tol * t["power_kw"]:
                    continue                                     # motore diverso (es. Carrera / Turbo S)
                if _cambio(t.get("gearbox")) and _cambio(c.get("gearbox")) and _cambio(t["gearbox"]) != _cambio(c["gearbox"]):
                    continue
                conf.append(dict(c))
            if not t.get("power_kw"):
                st["senza_potenza"] += 1                         # motore non indicato: stima preliminare, non sul sito
                cat = "stima_preliminare"
            if len(conf) < nmin:
                st["pochi_confronti"] += 1
                conn.execute("""INSERT INTO supercar_eu (listing_id, n, categoria, paese, computed_at) VALUES (%s,%s,'pochi_confronti',%s,now())
                                ON CONFLICT (listing_id) DO UPDATE SET n=EXCLUDED.n, categoria=EXCLUDED.categoria, computed_at=now()""",
                             (t["id"], len(conf), paese))
                continue
            v = _conto(int(t["price_eur"]), [int(c["price_eur"]) for c in conf], costi)
            nota = None
            if t["price_eur"] < 0.55 * v["M"]:
                st["troppo_bassa"] += 1                          # oltre il 45% sotto la mediana: annuncio sospetto
                cat = "sospetto"
            else:
                cat = "opportunita" if v["margine"] >= soglia and t.get("power_kw") else "non_conveniente"
            # giudice AI (solo per le auto che andrebbero sul sito): via i confronti di un'altra versione
            if cat == "opportunita" and usa_ai:
                try:
                    from ..ai import giudice
                    g = giudice.giudica(conn, t, [{"id": c["id"], "versione": c.get("version_raw"), "anno": c.get("year"),
                                                   "km": c.get("mileage_km"), "cambio": c.get("gearbox"), "kw": c.get("power_kw"),
                                                   "prezzo": c.get("price_eur"), "venditore": c.get("seller_type")} for c in conf[:60]])
                    if g and g.get("via"):
                        resto = [c for c in conf if c["id"] not in g["via"]]
                        prima = v["margine"]
                        if len(resto) >= nmin:
                            v = _conto(int(t["price_eur"]), [int(c["price_eur"]) for c in resto], costi)
                            cat = "opportunita" if v["margine"] >= soglia else "non_conveniente"
                        else:
                            cat = "pochi_confronti"
                        conf = resto
                        st["giudice_toccate"] += 1
                        log.warning("SUPERCAR_GIUDICE %s tolti %d: margine %s→%s, %s", t["id"], len(g["via"]), prima, v["margine"], cat)
                    nota = (g or {}).get("nota")
                except Exception as e:
                    log.warning("SUPERCAR giudice %s: %s", t["id"], str(e)[:120])
            st[cat] += 1
            dati = {"confronti": [{"id": c["id"], "prezzo": c["price_eur"], "anno": c.get("year"), "km": c.get("mileage_km"),
                                   "kw": c.get("power_kw"), "paese": c.get("region") or c.get("province")} for c in conf[:20]],
                    "nota": nota}
            conn.execute("""INSERT INTO supercar_eu (listing_id, m, r, p25, n, margine, costi, categoria, paese, dati, computed_at)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                            ON CONFLICT (listing_id) DO UPDATE SET m=EXCLUDED.m, r=EXCLUDED.r, p25=EXCLUDED.p25, n=EXCLUDED.n,
                              margine=EXCLUDED.margine, costi=EXCLUDED.costi, categoria=EXCLUDED.categoria, paese=EXCLUDED.paese,
                              dati=EXCLUDED.dati, computed_at=now()""",
                         (t["id"], v["M"], v["R"], v["P25"], v["n"], v["margine"], costi, cat, paese, json.dumps(dati)))
            conn.commit()
            if cat == "opportunita":
                log.warning("SUPERCAR_OK %s", json.dumps({"id": t["id"], "auto": f"{t['make']} {t['model']} {t.get('version_raw') or ''}"[:80],
                                                          "anno": t["year"], "km": t.get("mileage_km"), "prezzo": t["price_eur"],
                                                          "M": v["M"], "R": v["R"], "n": v["n"], "margine": v["margine"],
                                                          "paese": paese}, ensure_ascii=False))
    log.warning("SUPERCAR_VALUTA_FINE %s", json.dumps(dict(st)))
    return dict(st)


SITO_SQL = """
SELECT s.listing_id, s.m, s.r, s.n, s.margine, s.costi, s.paese
FROM supercar_eu s JOIN listings l ON l.id = s.listing_id
WHERE s.categoria = 'opportunita' AND s.computed_at > now() - interval '2 days'
  AND l.status = 'attivo' AND l.seller_type = 'privato'
ORDER BY s.margine DESC
LIMIT %(lim)s
"""
