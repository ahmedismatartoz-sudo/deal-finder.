"""Recupero delle auto rimaste senza abbastanza confronti (16/11, ramo fond-auto).

    python -m dealfinder.pricing.recupero          (un giro, al massimo RECUPERO_PER_GIRO auto)

Le regole NON cambiano. Si rivalutano, con la stessa strada di sempre (valutazione.valuta_e_scrivi, quindi con la
cascata dei confronti di ricalcolo._cascata e con la stessa guardia sulle scritture), le auto di privati ancora online
la cui ultima valutazione è:
  - "in attesa del giudice" (giudice_mancante: budget AI finito, AI ferma o processo senza chiave AI);
  - "dati_insufficienti" perché il mercato non si è potuto stimare: pochi confronti ("solo N confronti validi") o
    confronti troppo dispersi, e non ancora passata dalla cascata;
  - 17/11: "dati_insufficienti" per dati dell'auto incompleti (anno, km, alimentazione, km scritti come l'anno) che
    nell'annuncio ORA ci sono (letti dal testo da core/dal_testo.completa_db): senza questo restavano ferme.
Le auto ferme per una protezione (prezzo sotto il 55% del mercato, danni, costi, prezzo non affidabile) e quelle già
valutate non si riprendono: la cascata per loro non parte (ricalcolo.serve_cascata).

Ogni auto ripresa si segna nella tabella recupero_provato (migrazione 059): il giro dopo prende le altre, a partire
dalla valutazione più vecchia, e non riprova la stessa prima di RECUPERO_ORE ore (24).

Budget AI: le chiamate al giudice di TUTTE le auto del giro (anche quelle ripescate che diventano candidato o
opportunità) contano nel tetto RECUPERO_GIUDICE_MAX (default: GIUDICE_ATTESA_PER_GIRO, 150); raggiunto il tetto o
finito il budget comune il giro si ferma. Un solo recupero alla volta (lucchetto 734120).

Variabili (nessuna è una regola: decidono solo QUANTE auto si riguardano per giro):
  RECUPERO_CONFRONTI  "0" = spento (default acceso)
  RECUPERO_PER_GIRO   auto al massimo per giro (default 400)
  RECUPERO_GIORNI     solo valutazioni degli ultimi N giorni (default 10)
  RECUPERO_ORE        ore prima di riprovare la stessa auto (default 24)
  RECUPERO_GIUDICE_MAX chiamate al giudice al massimo per giro (default GIUDICE_ATTESA_PER_GIRO o 150)
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

from . import policy_v2 as P

log = logging.getLogger("policy")

PER_GIRO = 400
GIORNI = 10
ORE = 24
LOCK = 734120                     # pg_try_advisory_lock: anche riprova_giudice del sito può prenderlo (FOND_AUTO.md)
BLOCCO = 20                       # auto per volta tra un controllo del tetto e l'altro

TABELLA_SQL = ("CREATE TABLE IF NOT EXISTS recupero_provato (listing_id BIGINT PRIMARY KEY, "
               "provato_at TIMESTAMPTZ NOT NULL DEFAULT now(), tentativi INTEGER NOT NULL DEFAULT 1, esito TEXT)")

# Ultima valutazione dell'auto (versione di policy in uso), solo privati online, non ripresa nelle ultime ORE ore.
DA_RECUPERARE_SQL = """
SELECT v.listing_id, (v.categoria = 'dati_insufficienti' AND v.dati->'motivi'->>0 LIKE 'dati dell''auto incompleti%%')
       AS incompleti, l.mileage_km, l.title, left(l.description, 4000) AS descr, l.version_raw
  FROM valutazioni_policy v JOIN listings l ON l.id = v.listing_id
  LEFT JOIN recupero_provato rp ON rp.listing_id = v.listing_id
 WHERE v.policy_version = %(pv)s AND l.status = 'attivo'
   AND COALESCE(l.seller_type, 'privato') <> 'commerciante'
   -- 17/11 (giudice): mai i "solo confronto" (mercato_nord, fuori_regole) né il solo finanziamento: non si
   -- pubblicano (valutazione.PROMUOVI_SQL), quindi niente chiamate al giudice per loro
   AND NOT (l.stage = 'mercato' AND btrim(split_part(COALESCE(l.stage_reason, ''), '|', 1)) IN ('mercato_nord', 'fuori_regole'))
   AND btrim(split_part(COALESCE(l.stage_reason, ''), '|', 1)) <> 'solo_finanziamento'
   AND v.computed_at > now() - make_interval(days => %(giorni)s)
   AND (rp.provato_at IS NULL OR rp.provato_at < now() - make_interval(hours => %(ore)s))
   AND (   (v.dati->>'giudice_mancante') = 'true'
        OR (NOT (v.dati ? 'cascata') AND v.categoria = 'dati_insufficienti'
            AND (v.dati->'motivi'->>0 LIKE 'solo %%confronti validi%%'
                 OR v.dati->'motivi'->>0 LIKE 'confronti troppo dispersi%%'))
        -- 17/11: dati dell'auto mancanti o sbagliati (km = anno) ma ORA completi nell'annuncio (dal testo, rilettura)
        OR (v.categoria = 'dati_insufficienti' AND v.dati->'motivi'->>0 LIKE 'dati dell''auto incompleti%%'
            AND l.year IS NOT NULL AND l.mileage_km IS NOT NULL AND l.fuel IS NOT NULL AND l.price_eur IS NOT NULL
            AND NOT (l.mileage_km BETWEEN 1990 AND 2035 AND abs(l.mileage_km - l.year) <= 3)
            -- km riempiti dal testo con la vecchia lettura (poteva prendere "tagliando ogni 15.000 km"): non si
            -- riprendono; solo quelli letti col filtro prudente (segno km_testo_prudente) o dati dalla fonte
            AND NOT (COALESCE(l.field_origin->>'mileage_km', '') = 'dal_testo'
                     AND NOT (COALESCE(l.field_origin, '{}'::jsonb) ? 'km_testo_prudente'))))
 ORDER BY v.computed_at ASC
 LIMIT %(lim)s"""

SEGNA_SQL = """INSERT INTO recupero_provato (listing_id, provato_at, tentativi, esito) VALUES (%s, now(), 1, %s)
   ON CONFLICT (listing_id) DO UPDATE SET provato_at = now(), tentativi = recupero_provato.tentativi + 1,
     esito = EXCLUDED.esito"""


def attivo() -> bool:
    return os.environ.get("RECUPERO_CONFRONTI", "1").strip() != "0"


def _int(nome: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(nome) or default))
    except ValueError:
        return default


def tetto_giudice() -> int:
    return _int("RECUPERO_GIUDICE_MAX", _int("GIUDICE_ATTESA_PER_GIRO", 150))


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


def km_affidabili(r) -> bool:
    """17/11 (giudice): un'auto ferma per dati incompleti si riprende solo se i km non sembrano letti dalla vecchia
    chilometri() (che prendeva "tagliando ogni 15.000 km" -> 15.000): se la vecchia lettura del testo dà proprio i km
    salvati e la lettura prudente no, i km vengono quasi certamente da lì -> non si riprende (in dubbio no)."""
    try:
        if not r.get("incompleti"):
            return True
        from ..core.dal_testo import chilometri, km_percorsi_chiari
        testo = f"{r.get('version_raw') or ''}\n{r.get('title') or ''}\n{r.get('descr') or ''}"
        km = r.get("mileage_km")
        return not (km is not None and chilometri(testo) == km and km_percorsi_chiari(testo) != km)
    except Exception:
        return False


def da_recuperare(conn, limite: int | None = None) -> list:
    lim = _int("RECUPERO_PER_GIRO", PER_GIRO) if limite is None else limite
    if lim <= 0:
        return []
    try:
        conn.execute(TABELLA_SQL)
        return [r["listing_id"] for r in conn.execute(
            DA_RECUPERARE_SQL, {"pv": P.POLICY_VERSION, "giorni": _int("RECUPERO_GIORNI", GIORNI),
                                "ore": _int("RECUPERO_ORE", ORE), "lim": lim}).fetchall() if km_affidabili(r)]
    except Exception as e:
        _rollback(conn)
        log.warning("RECUPERO_CONFRONTI auto non lette: %s", str(e)[:200])
        return []


def _segna(conn, ids: list, salvata: dict) -> None:
    try:
        for lid in ids:
            conn.execute(SEGNA_SQL, (lid, str(salvata.get(lid))[:40]))
        conn.commit()
    except Exception as e:
        _rollback(conn)
        log.warning("RECUPERO_CONFRONTI tentativi non segnati: %s", str(e)[:200])


def _budget_finito(conn) -> bool:
    try:
        from ..ai import client as AI
        return bool(AI.available() and AI.budget_finito(conn))
    except Exception:
        return False


def recupera(conn, limite: int | None = None) -> dict:
    """Un giro di recupero. Ritorna i contatori (anche nel log RECUPERO_CONFRONTI)."""
    if not attivo():
        return {"spento": True}
    try:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK,)).fetchone()["ok"]:
            log.warning("RECUPERO_CONFRONTI già in corso altrove: salto")
            return {"in_corso": True}
    except Exception as e:
        _rollback(conn)
        log.warning("RECUPERO_CONFRONTI lucchetto non preso (%s): salto", str(e)[:120])
        return {"in_corso": True}
    try:
        return _recupera(conn, limite)
    finally:
        try:
            _rollback(conn)
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
            conn.commit()
        except Exception:
            pass


# ------------------------------------------------------------------ diagnosi (17/11 notte, sola lettura)
# Perché le auto di privati online restano in dati_insufficienti: primo motivo, confronti trovati, motivi di esclusione
# dei confronti (anno, km, cambio, potenza...), dati mancanti dell'auto, modello generico e QUANTI annunci attivi ha il
# suo gruppo marca/modello/alimentazione in tutta la base (0-1 = modello raro o nome diverso tra le fonti). Solo numeri
# e marca/modello: niente id, URL, venditori. Riga di log RECUPERO_DIAGNOSI; spenta con RECUPERO_DIAGNOSI=0.
DIAGNOSI_SQL = """
WITH t AS (
  SELECT l.make, l.model, l.fuel, l.year, l.mileage_km, l.gearbox, l.power_kw, v.dati
    FROM valutazioni_policy v JOIN listings l ON l.id = v.listing_id
   WHERE v.policy_version = %(pv)s AND l.status = 'attivo' AND v.categoria = 'dati_insufficienti'
     AND COALESCE(l.seller_type, 'privato') <> 'commerciante'
     AND v.computed_at > now() - make_interval(days => %(giorni)s)),
g AS (
  SELECT l.make, l.model, l.fuel, count(*) AS n
    FROM listings l JOIN (SELECT DISTINCT make, model, fuel FROM t) k
      ON l.make = k.make AND l.model = k.model AND l.fuel = k.fuel
   WHERE l.status = 'attivo' AND l.price_eur IS NOT NULL
   GROUP BY 1, 2, 3)
SELECT t.make, t.model, t.fuel, t.year, t.mileage_km, t.gearbox, t.power_kw,
       t.dati->'motivi'->>0 AS motivo, (t.dati->>'n') AS n, t.dati->'non_equivalenti' AS non_eq,
       (t.dati ? 'cascata') AS cascata, COALESCE(g.n, 0) AS gruppo
  FROM t LEFT JOIN g ON g.make = t.make AND g.model = t.model AND g.fuel = t.fuel"""


def _motivo_corto(m: str | None) -> str:
    import re
    return re.sub(r"\d[\d.,]*", "N", (m or "nessun motivo"))[:70]


def riassumi_diagnosi(righe: list[dict]) -> dict:
    """Conteggi della diagnosi (separata dalla query per i test)."""
    from ..core import normalize as N
    from ..core.dal_testo import km_come_anno
    mot, neq, mancano, gruppo, nconf, modelli = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    cascata = 0
    for r in righe:
        mot[_motivo_corto(r.get("motivo"))] += 1
        cascata += int(bool(r.get("cascata")))
        for k, nome in (("year", "anno"), ("mileage_km", "km"), ("fuel", "alimentazione"), ("gearbox", "cambio"),
                        ("power_kw", "potenza")):
            if r.get(k) in (None, ""):
                mancano[nome] += 1
        if km_come_anno(r.get("mileage_km"), r.get("year")):
            mancano["km_uguali_anno"] += 1
        if (r.get("model") or "") in N.MODELLI_GENERICI:
            mancano["modello_generico"] += 1
        g = int(r.get("gruppo") or 0)
        gruppo["0-1" if g <= 1 else "2-4" if g <= 4 else "5-19" if g <= 19 else "20-99" if g <= 99 else "100+"] += 1
        try:
            n = int(r.get("n") or 0)
        except (TypeError, ValueError):
            n = 0
        nconf[str(n) if n < 4 else "4+"] += 1
        ne = r.get("non_eq")
        if isinstance(ne, str):
            try:
                ne = json.loads(ne)
            except ValueError:
                ne = None
        for k, c in (ne or {}).items() if isinstance(ne, dict) else ():
            try:
                neq[_motivo_corto(k)] += int(c)
            except (TypeError, ValueError):
                pass
        if g <= 4:
            modelli[f"{r.get('make')}/{r.get('model')}/{r.get('fuel')}"] += 1
    return {"auto": len(righe), "motivi": dict(mot.most_common(12)), "confronti_trovati": dict(nconf),
            "gruppo_attivi": dict(gruppo), "dati_mancanti": dict(mancano), "gia_in_cascata": cascata,
            "confronti_esclusi_per": dict(neq.most_common(15)), "modelli_rari": dict(modelli.most_common(40))}


def diagnosi(conn) -> dict | None:
    if os.environ.get("RECUPERO_DIAGNOSI", "1").strip() == "0":
        return None
    try:
        righe = [dict(r) for r in conn.execute(DIAGNOSI_SQL, {"pv": P.POLICY_VERSION,
                                                               "giorni": _int("RECUPERO_GIORNI", GIORNI)}).fetchall()]
        out = riassumi_diagnosi(righe)
        log.warning("RECUPERO_DIAGNOSI %s", json.dumps(out, ensure_ascii=False))
        return out
    except Exception as e:
        _rollback(conn)
        log.warning("RECUPERO_DIAGNOSI non riuscita: %s", str(e)[:200])
        return None


def _recupera(conn, limite: int | None) -> dict:
    from ..ai import giudice as GI
    from .valutazione import valuta_e_scrivi
    t0 = time.time()
    diagnosi(conn)                  # 17/11: misura del perché (sola lettura), prima del giro
    ids = da_recuperare(conn, limite)
    if not ids:
        log.warning("RECUPERO_CONFRONTI %s", json.dumps({"auto": 0}))
        return {"auto": 0}
    tetto = tetto_giudice()
    cat, cascata, stats = Counter(), Counter(), Counter()
    chiamate, fatte, sul_sito, fermo = 0, 0, 0, None
    for k in range(0, len(ids), BLOCCO):
        if chiamate >= tetto:
            fermo = "tetto_giudice"
            break
        if _budget_finito(conn):
            fermo = "budget_ai_finito"
            break
        pezzo = ids[k:k + BLOCCO]
        _t = time.time()
        es = valuta_e_scrivi(conn, pezzo, "cron-recupero")
        # tutte le chiamate al giudice del pezzo, non solo quelle delle righe in attesa
        chiamate += sum(1 for lid in pezzo if GI.esito(lid, _t) == "chiamata")
        fatte += len(pezzo)
        _segna(conn, pezzo, es.salvata)
        stats.update(es.stats)
        for lid in pezzo:
            cat[str(es.salvata.get(lid))] += 1
            v = es.valutazioni.get(lid) or {}
            if v.get("fonte_confronti"):
                cascata[str(v["fonte_confronti"])] += 1
            sul_sito += int(es.sul_sito(lid))
    out = {"auto": fatte, "scelte": len(ids), "categorie_salvate": dict(cat), "cascata_usata": dict(cascata),
           "sul_sito_possibili": sul_sito, "chiamate_giudice": chiamate, "tetto_giudice": tetto,
           **({"fermo": fermo} if fermo else {}), "contatori": dict(stats), "secondi": round(time.time() - t0, 1)}
    log.warning("RECUPERO_CONFRONTI %s", json.dumps(out, ensure_ascii=False))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from ..db import connect
    with connect() as conn:
        print(recupera(conn))
