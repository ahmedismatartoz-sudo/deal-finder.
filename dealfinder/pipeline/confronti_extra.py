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

import json
import logging
import os
from collections import Counter

log = logging.getLogger("confronti_extra")

# 1/11 (cliente): solo intorno alla Lombardia, perché più si scende più i prezzi calano. Raggio 100 km da ogni città:
# Piemonte, Valle d'Aosta, Liguria, Emilia, Veneto, Trentino-Alto Adige. Niente Centro, Sud e isole.
CAP_ITALIA = ",".join([
    "12100", "11100", "43121", "41121", "36100", "31100", "38121", "39100",   # Cuneo, Aosta, Parma, Modena, Vicenza, Treviso, Trento, Bolzano
    "10121", "16121", "40121", "37121", "35121",                              # Torino, Genova, Bologna, Verona, Padova (già fatte il 31/10)
    # 3/11 22:39 (cliente: "raccogliamo più auto di altre zone così non restano senza confronti"): resto del Nord
    "30121", "33100", "34121", "19121", "17100", "47921", "44121", "15121", "28100",   # Venezia, Udine, Trieste, La Spezia,
    # Savona, Rimini, Ferrara, Alessandria, Novara
])


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
    # 1/11 (cliente: "raccogliamo gli annunci fuori zona per averli sempre per i confronti"): regioni intorno alla Lombardia.
    # Ogni città ha il suo turno (CE_ORE ore); a ogni giro del cron se ne fanno al massimo CE_PER_GIRO, così il giro
    # della zona non si allunga di ore e le città restano sempre aggiornate a rotazione.
    tutte = [c.strip() for c in os.environ.get("CE_CAP", CAP_ITALIA).split(",") if c.strip()]
    from .ciclo import _due
    citta = [c for c in tutte if _due(f"ce:{c}", int(os.environ.get("CE_ORE", "48")))][: int(os.environ.get("CE_PER_GIRO", "5"))]
    log.warning("CONFRONTI_EXTRA turno: %s (di %d città)", citta, len(tutte))
    lo, hi = int(os.environ.get("CE_MIN", "1000")), int(os.environ.get("CE_MAX", "70000"))
    venditori = [("P", "privato"), ("D", "commerciante")]
    os.environ.pop("PASSI_JOB", None)
    for cap in citta:
        with connect() as _c:
            _jid = _c.execute("INSERT INTO job_runs (job) VALUES (%s) RETURNING id", (f"ce:{cap}",)).fetchone()["id"]
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
        try:
            with connect() as _c:
                _c.execute("UPDATE job_runs SET finished_at=now(), ok=true, stats=%s WHERE id=%s",
                           (json.dumps({k: v for k, v in st.items() if k.startswith(cap)}), _jid))
        except Exception as e:
            log.warning("CONFRONTI_EXTRA turno non salvato: %s", str(e)[:120])
    log.warning("CONFRONTI_EXTRA_FINE %s", dict(st))
    return dict(st)
