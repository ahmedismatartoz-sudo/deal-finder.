"""20/10 (cliente): privati della Lombardia con auto da 60.000 a 200.000 €, confrontati con la base prezzi della stessa
fascia (privati e concessionari, tutta Italia). Le auto molto sotto il prezzo delle auto uguali passano alla valutazione
prudente di sempre: quelle buone vanno sul sito. Nei log: PLUS_CAND {json} (le migliori) e PLUS_FINE."""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter

log = logging.getLogger("privati_lusso")
LOMBARDIA = ["MI", "MB", "VA", "CO", "LC", "BG", "BS", "LO", "PV", "CR", "MN", "SO"]

CANDIDATI_SQL = r"""
WITH g AS (              -- base prezzi: stessa marca, modello, alimentazione e anno, fascia 60-200k (privati e concessionari)
  SELECT lower(make) AS mk, lower(model) AS md, lower(coalesce(fuel,'')) AS fu, year,
         count(*) AS n, percentile_cont(.5) WITHIN GROUP (ORDER BY price_eur) AS med,
         percentile_cont(.25) WITHIN GROUP (ORDER BY price_eur) AS p25
  FROM listings
  WHERE price_eur BETWEEN 40000 AND 450000 AND year >= 2009 AND make IS NOT NULL AND model IS NOT NULL
  GROUP BY 1, 2, 3, 4
)
SELECT t.id, t.source, t.url, t.price_eur, t.telefono_ok, t.make, t.model, t.version_raw, t.year, t.mileage_km,
       t.province, g.n, g.med, g.p25
FROM listings t
JOIN g ON g.mk=lower(t.make) AND g.md=lower(t.model) AND g.fu=lower(coalesce(t.fuel,'')) AND g.year=t.year
WHERE t.status='attivo' AND t.seller_type = ANY(%(venditori)s) AND upper(t.province) = ANY(%(prov)s)
  AND t.price_eur BETWEEN %(pmin)s AND 400000 AND t.year >= %(anno)s
  AND coalesce(t.damage_class,'') NOT IN ('grave','alto_rischio','medio')
  AND t.stage IS DISTINCT FROM 'approfondito'
  AND g.n >= %(nmin)s AND t.price_eur < g.med * %(soglia)s
  AND t.price_eur >= g.med * 0.6                 -- oltre il 40 per cento sotto: quasi sempre anno o modello sbagliato nell'annuncio
  AND NOT (t.year > 2012 AND (coalesce(t.url,'') || ' ' || coalesce(t.title,'') || ' ' || coalesce(t.version_raw,''))
           ~* '(\m(996|993|964|930|911 sc|3\.2)\M|-asi-|\masi\M|epoca|youngtimer)')
ORDER BY t.price_eur / g.med ASC
LIMIT %(lim)s"""


def giro() -> dict:
    from ..db import connect
    from . import tempo_reale as tr
    st: Counter = Counter()
    if os.environ.get("PLUS_RACCOGLI", "1") == "1":            # privati della Lombardia aggiornati prima del confronto
        from .lusso import privati_lombardia
        cap = os.environ.get("AUTOSCOUT_CAP")
        try:
            st["privati_raccolti"] = privati_lombardia()
        finally:
            if cap is not None:
                os.environ["AUTOSCOUT_CAP"] = cap
    with connect() as conn:
        conn.execute("SET statement_timeout = '900s'")
        prov = [p.strip() for p in os.environ["PLUS_PROVINCE"].split(",")] if os.environ.get("PLUS_PROVINCE") else LOMBARDIA
        venditori = [v.strip() for v in os.environ.get("PLUS_VENDITORI", "privato").split(",")]
        cand = conn.execute(CANDIDATI_SQL, {"prov": prov, "venditori": venditori, "anno": int(os.environ.get("PLUS_ANNO", "2010")),
                                            "nmin": int(os.environ.get("PLUS_NMIN", "4")), "pmin": int(os.environ.get("PLUS_MIN", "60000")),
                                            "soglia": float(os.environ.get("PLUS_SOGLIA", "0.92")),
                                            "lim": int(os.environ.get("PLUS_MAX", "400"))}).fetchall()
        conn.execute("SET statement_timeout = 0")
        st["candidate"] = len(cand)
        for r in cand[:int(os.environ.get("PLUS_LOG", "40"))]:
            log.warning("PLUS_CAND %s", json.dumps({"id": r["id"], "auto": f"{r['make']} {r['model']} {r['version_raw'] or ''}"[:80],
                                                   "anno": r["year"], "km": r["mileage_km"], "prezzo": r["price_eur"],
                                                   "mediana": int(r["med"]), "p25": int(r["p25"]), "n": r["n"],
                                                   "sotto_pct": round(100 * (1 - r["price_eur"] / r["med"]), 1),
                                                   "prov": r["province"], "url": r["url"]}, ensure_ascii=False))
        from concurrent.futures import ThreadPoolExecutor
        da_fare = [r for r in cand if r["source"] == "autoscout24" and r["telefono_ok"] is None]

        def numero(r):
            cl = tr._client_as()
            stato, det = tr._as_dettaglio(cl, r["url"])
            return r["id"], stato, (tr.telefono_autoscout(det) if det is not None else None)

        with ThreadPoolExecutor(4) as ex:
            for lid, stato, tel in ex.map(numero, da_fare):
                if stato == "scomparso":
                    st["autoscout_sparite"] += 1
                elif tel is not None or stato == "attivo":
                    conn.execute("UPDATE listings SET telefono_ok=%s, telefono_at=now() WHERE id=%s", (tel, lid))
                    st[f"autoscout_telefono_{tel}"] += 1
        conn.commit()
        tr.valuta_e_pubblica(conn, [r["id"] for r in cand], st)
        conn.commit()
        # 21/10 (cliente): fuori le auto vendute solo con leasing/finanziamento. Su AutoScout24 la descrizione non è
        # nella ricerca: si legge dalla pagina dell'annuncio, solo per le auto da 50.000 € in su già sul sito
        from ..web.scovo import FIN_SOLO_RE
        da_leggere = conn.execute("""SELECT id, url FROM listings WHERE source='autoscout24' AND status='attivo'
                                     AND stage='approfondito' AND price_eur >= 50000 AND description IS NULL
                                     LIMIT %s""", (int(os.environ.get("PLUS_DESC_MAX", "800")),)).fetchall()

        def descrizione(r):
            cl = tr._client_as()
            stato, det = tr._as_dettaglio(cl, r["url"])
            d = (det or {}).get("description") if isinstance(det, dict) else None
            if isinstance(d, dict):
                d = d.get("text") or d.get("html") or ""
            return r["id"], stato, re.sub(r"<[^>]+>", " ", str(d or ""))[:8000]

        with ThreadPoolExecutor(4) as ex:
            for lid, stato, d in ex.map(descrizione, da_leggere):
                if stato == "scomparso":
                    conn.execute("UPDATE listings SET status='scomparso' WHERE id=%s", (lid,))
                    continue
                if d:
                    conn.execute("UPDATE listings SET description=%s WHERE id=%s", (d, lid))
                    if re.search(FIN_SOLO_RE, d, re.I):
                        conn.execute("UPDATE listings SET stage='scartato', stage_reason='solo_finanziamento' WHERE id=%s", (lid,))
                        st["solo_finanziamento"] += 1
                st["descrizioni_lette"] += 1
        conn.commit()
    log.warning("PLUS_FINE %s", json.dumps(dict(st)))
    return dict(st)
