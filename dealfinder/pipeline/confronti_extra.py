"""29/10 (cliente: "far lavorare meglio gli scraper"): annunci di AutoScout24 FUORI dalla zona, salvati SOLO come
confronti per il prezzo.

Diagnosi del 29/10: su 8.135 auto di privati della zona valutate in 3 giorni, 2.422 restavano senza prezzo perché
"solo N confronti validi": con anno ±1, km ±20.000, stesso motore e cambio, nella zona ci sono troppo poche auto
uguali. Il salvataggio normale scarta tutto ciò che è fuori zona (tempo_reale._salva), quindi la base prezzi del Nord
e di tutta Italia (ricalcolo._valuta_base: ripiego NORD e POLICY_ITALIA) aveva pochi annunci da usare.

Qui si leggono, a giorni alterni (CE_ORE=48), le ricerche AutoScout24 intorno ad altre città (CE_CAP), privati e
commercianti, da CE_MIN a CE_MAX €, e si salvano senza foto e senza valutarli: il sito e le valutazioni restano solo
sulla zona del cliente; questi annunci servono solo a confrontare i prezzi. Si ferma se il database supera CE_DB_MAX_GB.
"""
from __future__ import annotations

import logging
import os
from collections import Counter

log = logging.getLogger("confronti_extra")


def giro() -> dict:
    from ..core.dal_testo import completa
    from ..core.normalize import normalize_fields, PROVINCE_REGION
    from ..db import connect, upsert_listing
    from .lusso import bande_parallele
    st: Counter = Counter()
    with connect() as conn:
        gb = float(conn.execute("SELECT pg_database_size(current_database()) / 1e9 AS gb").fetchone()["gb"])
    if gb > float(os.environ.get("CE_DB_MAX_GB", "15")):
        log.warning("CONFRONTI_EXTRA fermo: database a %.1f GB", gb)
        return {"fermo_db_gb": round(gb, 1)}
    citta = [c.strip() for c in os.environ.get("CE_CAP", "37121,40121,10121,35121,16121,50121,00185").split(",") if c.strip()]   # Verona, Bologna, Torino; dal 31/10 anche Padova, Genova, Firenze, Roma
    lo, hi = int(os.environ.get("CE_MIN", "1000")), int(os.environ.get("CE_MAX", "50000"))
    venditori = [("P", "privato"), ("D", "commerciante")]
    os.environ.pop("PASSI_JOB", None)
    for cap in citta:
        os.environ["AUTOSCOUT_CAP"], os.environ["AUTOSCOUT_RAGGIO"] = cap, os.environ.get("CE_RAGGIO", "100")
        with connect() as conn:
            n = 0
            for l in bande_parallele(lo, hi, int(os.environ.get("CE_PASSO", "1000")), venditori,
                                     workers=int(os.environ.get("CE_PARALLELI", "4"))):
                try:
                    for k, v in completa({"fuel": l.fuel, "year": l.year, "mileage_km": l.mileage_km}, l.title, l.description).items():
                        setattr(l, k, v)
                    normalize_fields(l)
                    if not l.province:
                        st["senza_provincia"] += 1
                        continue
                    l.region = l.region or PROVINCE_REGION.get(l.province.upper())
                    l.photos = []                       # solo confronto: niente foto (spazio)
                    _, ev = upsert_listing(conn, l)
                    st[f"{cap}:{ev}"] += 1
                    n += 1
                    if n % 200 == 0:
                        conn.commit()
                except Exception as e:
                    conn.rollback()
                    st["errori"] += 1
                    if st["errori"] < 4:
                        log.warning("CONFRONTI_EXTRA annuncio non salvato: %s", str(e)[:120])
            conn.commit()
        log.warning("CONFRONTI_EXTRA %s fatto: %s", cap, {k: v for k, v in st.items() if k.startswith(cap)})
    log.warning("CONFRONTI_EXTRA_FINE %s", dict(st))
    return dict(st)
