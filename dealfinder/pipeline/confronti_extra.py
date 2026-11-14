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


def ordina_per_turno(dovute: list[str], ultimi: dict | None = None) -> list[str]:
    """14/11: prima le città lette meno di recente (mai lette per prime), non nell'ordine della lista. Prima si
    prendevano le prime CE_PER_GIRO città "dovute" in ordine di lista: con 2 per giro le prime 14 tornavano dovute
    ogni 48 ore e le ultime 8 (Udine, Trieste, La Spezia, Savona, Rimini, Ferrara, Alessandria, Novara) non avevano
    mai il turno, così i loro annunci non venivano più rivisti e dopo 10 giorni uscivano dai confronti.
    `ultimi`: {cap: ultimo giro riuscito} (per i test); None = letti da job_runs."""
    if ultimi is None:
        ultimi = {}
        try:
            from ..db import connect
            with connect() as conn:
                for r in conn.execute("SELECT job, max(started_at) AS at FROM job_runs WHERE job = ANY(%s) AND ok "
                                      "GROUP BY job", ([f"ce:{c}" for c in dovute],)).fetchall():
                    ultimi[r["job"][3:]] = r["at"]
        except Exception as e:                       # in dubbio resta l'ordine della lista
            log.warning("CONFRONTI_EXTRA ultimi turni non letti: %s", str(e)[:120])
            return list(dovute)
    pos = {c: i for i, c in enumerate(dovute)}

    def quando(c):
        at = ultimi.get(c)
        if at is None:
            return (0, 0.0, pos[c])
        return (1, at.timestamp() if hasattr(at, "timestamp") else float(at), pos[c])
    return sorted(dovute, key=quando)


def giro() -> dict:
    from ..core.dal_testo import completa
    from ..core.normalize import normalize_fields, PROVINCE_REGION
    from ..db import connect, upsert_listing
    from .lusso import bande_parallele
    st: Counter = Counter()
    from ..db import db_gb, db_pieno
    from .collect import provincia_da_comune, riepilogo_scarti
    with connect() as conn:
        if db_pieno(conn):
            gb = db_gb(conn) or 0.0
            log.warning("CONFRONTI_EXTRA fermo: database a %.1f GB", gb)
            return {"fermo_db_gb": round(gb, 1)}
    # 1/11 (cliente: "raccogliamo gli annunci fuori zona per averli sempre per i confronti"): regioni intorno alla Lombardia.
    # Ogni città ha il suo turno (CE_ORE ore); a ogni giro del cron se ne fanno al massimo CE_PER_GIRO, così il giro
    # della zona non si allunga di ore e le città restano sempre aggiornate a rotazione.
    tutte = [c.strip() for c in os.environ.get("CE_CAP", CAP_ITALIA).split(",") if c.strip()]
    from .ciclo import _due
    dovute = [c for c in tutte if _due(f"ce:{c}", int(os.environ.get("CE_ORE", "48")))]
    citta = ordina_per_turno(dovute)[: int(os.environ.get("CE_PER_GIRO", "5"))]
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
                st["letti:autoscout24"] += 1
                try:
                    # 3/11: anche cambio e kW dal testo (come in _salva), provincia dal comune prima di scartare
                    for k, v in completa({"fuel": l.fuel, "year": l.year, "mileage_km": l.mileage_km, "gearbox": l.gearbox,
                                          "power_kw": l.power_kw, "version_raw": l.version_raw}, l.title, l.description).items():
                        setattr(l, k, v)
                    normalize_fields(l)
                    if not l.province and l.city:
                        l.province = provincia_da_comune(conn, l.city)
                        if l.province:
                            st["provincia_ricavata"] += 1
                    if not l.province:
                        st["senza_provincia"] += 1
                        st["scarto:senza_provincia"] += 1
                        continue
                    l.region = l.region or PROVINCE_REGION.get(l.province.upper())
                    l.photos = []                       # solo confronto: niente foto (spazio)
                    # 3/11: punto di ripristino per annuncio: un errore perde solo quell'annuncio (prima il rollback
                    # buttava anche quelli letti dall'ultimo commit, fino a 199)
                    with conn.transaction():
                        _, ev = upsert_listing(conn, l)
                    st[f"{cap}:{ev}"] += 1
                    st["salvati"] += 1
                    n += 1
                    if n % 200 == 0:
                        conn.commit()
                except Exception as e:
                    st["errori"] += 1
                    st["scarto:non_salvato"] += 1
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
    log.warning("RACCOLTA_SCARTI confronti_extra %s", json.dumps(riepilogo_scarti(st)))
    log.warning("CONFRONTI_EXTRA_FINE %s", dict(st))
    return dict(st)
