"""28/11 (lontane e Subito 200 km, decisione del cliente 28/11 11:09, "ti autorizzo con il sì"): far arrivare alla
valutazione e al sito le auto GIÀ LETTE delle province entro 200 km da Milano (zone.PROVINCE_200KM), prima le
lontane (Verona, Modena, Vicenza, Trento, Massa, La Spezia, Savona, Imperia, Aosta, Genova, Parma, Reggio Emilia),
senza cambiare nessuna regola di margine.

Perché non arrivavano (diagnosi del 28/11):
- mercato_nord (pipeline/collect.py, cron mercato, AutoScout24 entro 350 km) salvava ogni annuncio del Nord fuori da
  zone.tutte() come stage 'mercato' / motivo 'mercato_nord' ("mai proposta", valutazione.NON_PROPOSTE): Verona,
  Vicenza, Trento, Modena, Savona, Imperia, La Spezia, Aosta. La lettura dei 200 km li ha trovati già nel database
  ("invariati": 38 nuovi su 47.430 salvati) e il salvataggio degli invariati non tocca lo stadio; la promozione
  (ricalcolo.promuovi_utilitarie, valutazione.PROMUOVI_SQL) esclude i "solo confronto": mai in scovo.LIST_SQL.
- il giro della zona (benzina_mi, province_zona = 100 km + Torino) non sceglie mai queste province e la lettura
  (lettura_completa, lettura_200km) salva senza valutare: restava solo il ricalcolo delle utilitarie, una volta al
  giorno, che le valutava ma non poteva pubblicarle.

Lavoro una tantum: BREVO_IMPORT_LISTE=rivaluta_200km:<etichetta>[:<ore>] (coda_lavori, SOLO_SITO, _lungo). Per ogni
provincia (prima le lontane):
1. i PRIVATI attivi segnati 'mercato_nord' tornano annunci normali (stage 'nuovo'): solo quelli, i commercianti restano
   prezzi di riferimento;
2. si valutano (valutazione.valuta_e_scrivi, lo stesso calcolo di tutti: pricing/valore_base) i privati attivi con le
   condizioni delle utilitarie (ricalcolo.UTIL_SQL: visti negli ultimi 14 giorni, anno e km del sito, auto sane, mai
   gli scarti fissi né l'analisi manuale) e prezzo fino a 200.000 € (il tetto del sito), NON valutati nelle ultime
   <ore> ore (24), più quelli appena sbloccati e le buone ancora fuori dal sito;
3. le buone (margine almeno alla soglia del sito, numero non escluso) vanno sul sito con la promozione delle
   utilitarie (ricalcolo.promuovi_utilitarie, stesse istruzioni): il numero lo controlla il sito, come sempre.
A blocchi di gruppi interi (marca/modello/alimentazione) con una pausa a ogni gruppo: gira nel processo del sito.
Lucchetto: quello delle utilitarie (ricalcolo._UTIL_LOCK, mai due calcoli completi insieme nello stesso processo; si
aspetta fino a ATTESA_MIN minuti) e la riga di _lungo (mai due processi). Le province finite si ricordano (passi, job
"rivaluta_200km:<etichetta>"): dopo un riavvio si riprende dalla provincia successiva.
Log: RIVALUTA_200KM {per_provincia: {letti, sbloccati, valutati, con_margine, promosse, dati_insufficienti}, ...}.

Mercato (non gonfiare i prezzi): il valore resta quello di valore_base.zona_di, cioè per le province lontane i
commercianti e le vendute delle 16 province della zona di Milano (PROVINCE_100KM). valore_base ha mercati per zona
(Milano, Torino, Ravenna) ma non per le province nuove: crearne uno (es. Veneto/Trentino, Liguria, Emilia) cambierebbe
il calcolo del valore, ed è una decisione del cliente. Segnalato; il conteggio dati_insufficienti per provincia dice
dove il mercato di Milano non basta.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter

log = logging.getLogger("valutazione")

# prima le province lontane (fuori dalla zona del giro e dal mercato di Milano), poi le altre dei 200 km
LONTANE = ("VR", "MO", "VI", "TN", "MS", "SP", "SV", "IM", "AO", "GE", "PR", "RE")
ORE_DEFAULT = 24
PREZZO_MAX = 200000            # tetto del sito (lettura_completa.PREZZO_MAX)
PREZZO_MIN = 500               # come le utilitarie
FONTE = "rivaluta-200km"
PAUSA_GRUPPO_S = 0.05          # come valore_base.rivaluta: il sito resta reattivo
BLOCCO = 200
ORE_MAX = 5                    # oltre: si ferma e solleva (_lungo riprende dalla provincia dopo al prossimo avvio)
ATTESA_MIN = 40                # minuti di attesa al massimo se le utilitarie stanno girando nello stesso processo

_MOT = "btrim(split_part(COALESCE(l.stage_reason, ''), '|', 1))"
# 1. privati "solo prezzo" di mercato_nord: annunci normali (solo il motivo mercato_nord, mai fuori_regole)
SBLOCCA_SQL = ("UPDATE listings l SET stage = 'nuovo', stage_reason = NULL "
               "WHERE l.status = 'attivo' AND l.seller_type = 'privato' AND l.province = %(p)s "
               "AND l.stage = 'mercato' AND " + _MOT + " = 'mercato_nord' RETURNING l.id")
# 2. le auto da valutare: condizioni di ricalcolo.UTIL_SQL, prezzo fino al tetto del sito
IDS_SQL = ("SELECT l.id, l.make, l.model, l.fuel FROM listings l "
           "WHERE l.status = 'attivo' AND l.seller_type = 'privato' AND l.province = %(p)s "
           "AND l.price_eur BETWEEN %(lo)s AND %(hi)s AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s "
           "AND l.last_seen_at > now() - interval '14 days' AND l.source IN ('subito','autoscout24','facebook') "
           "AND COALESCE(l.damage_class, 'sconosciuto') IN ('nessuno','sconosciuto') AND NOT COALESCE(l.problem_search, false) "
           "AND " + _MOT + " NOT IN ('doppione','truffa','prezzo_finto','dati_incoerenti') "
           "AND COALESCE(l.stage_reason, '') NOT LIKE 'analisi_manuale%%' "
           "AND NOT (l.stage = 'mercato' AND " + _MOT + " IN ('mercato_nord','fuori_regole')) "
           "AND (l.id = ANY(%(sbloccati)s) "
           "     OR NOT EXISTS (SELECT 1 FROM valutazioni_policy vp WHERE vp.listing_id = l.id "
           "                    AND vp.computed_at > now() - make_interval(hours => %(ore)s)) "
           "     OR (l.stage IS DISTINCT FROM 'approfondito' AND EXISTS (SELECT 1 FROM valutazioni_policy vp "
           "         WHERE vp.listing_id = l.id AND vp.categoria = ANY(%(buone)s)))) "
           "ORDER BY lower(COALESCE(l.make, '')), lower(COALESCE(l.model, '')), COALESCE(l.fuel, ''), l.id")


def province() -> list:
    """Le 33 province entro 200 km, prima le lontane."""
    from ..zone import PROVINCE_200KM, ROMA
    tutte = [p for p in PROVINCE_200KM if p not in ROMA]
    return [p for p in LONTANE if p in tutte] + [p for p in tutte if p not in LONTANE]


def leggi_spec(spec: str) -> tuple[str, int]:
    """rivaluta_200km:<etichetta>[:<ore>] -> (etichetta, ore)."""
    parti = (spec or "").split(":")
    etichetta = (parti[1] if len(parti) > 1 else "") or "ora"
    ore = ORE_DEFAULT
    if len(parti) > 2 and parti[2].strip().isdigit():
        ore = max(0, int(parti[2]))
    return etichetta[:40], ore


def lavoro(spec: str = "", connetti=None, ves=None, fatto=None, segna=None, finito=None, lucchetto=None,
           adesso=time.time) -> dict:
    """BREVO_IMPORT_LISTE=rivaluta_200km:<etichetta>[:<ore>]. Tutto sostituibile nei test."""
    from .. import regole as RG
    from ..pipeline import passi
    from ..web import scovo
    from . import ricalcolo as R
    from .valore_base import _blocchi
    from .valutazione import valuta_e_scrivi
    if connetti is None:
        from ..db import connect as connetti
    ves = ves or valuta_e_scrivi
    lucchetto = lucchetto if lucchetto is not None else R._UTIL_LOCK
    etichetta, ore = leggi_spec(spec)
    job = f"rivaluta_200km:{etichetta}"
    t0 = adesso()
    esito: dict = {"job": job, "ore": ore, "per_provincia": {}, "fermata": None}
    if not lucchetto.acquire(timeout=ATTESA_MIN * 60):
        esito["fermata"] = "utilitarie_in_corso"
        log.warning("RIVALUTA_200KM %s", json.dumps(esito, ensure_ascii=False))
        raise RuntimeError("utilitarie in corso nello stesso processo: si riprova al prossimo avvio")
    try:
        with passi.locale(job):
            fatto = fatto or passi.fatto
            segna = segna or passi.segna
            a = RG.regole().ammissione
            for p in province():
                if fatto(f"prov:{p}"):
                    continue
                if adesso() - t0 > ORE_MAX * 3600:
                    esito["fermata"] = "tempo"
                    break
                st: Counter = Counter()
                with connetti() as conn:
                    sbloccati = [r["id"] for r in conn.execute(SBLOCCA_SQL, {"p": p}).fetchall()]
                    conn.commit()
                    righe = [dict(r) for r in conn.execute(IDS_SQL, {
                        "p": p, "lo": PREZZO_MIN, "hi": PREZZO_MAX, "anno": int(a.anno_min), "km": int(a.km_max),
                        "ore": int(ore), "sbloccati": sbloccati, "buone": sorted(scovo.CATEGORIE_SITO)}).fetchall()]
                    conn.commit()
                st["sbloccati"] = len(sbloccati)
                st["letti"] = len(righe)
                for pezzo in _blocchi(righe, BLOCCO):
                    if adesso() - t0 > ORE_MAX * 3600:
                        esito["fermata"] = "tempo"
                        break
                    try:
                        with connetti() as conn:
                            es = ves(conn, pezzo, FONTE, pubblica=False, pausa_gruppo=PAUSA_GRUPPO_S)
                            conn.commit()
                            buone = R.scelte_utilitarie(es)
                            n, promosse = R.promuovi_utilitarie(conn, buone)
                            conn.commit()
                    except Exception as e:
                        st["blocchi_errore"] += 1
                        log.warning("RIVALUTA_200KM %s blocco di %d auto non riuscito: %s", p, len(pezzo),
                                    type(e).__name__)
                        continue
                    st["valutati"] += len(es.valutazioni)
                    st["con_margine"] += n
                    st["promosse"] += promosse
                    st["dati_insufficienti"] += sum(1 for v in es.valutazioni.values()
                                                    if v.get("categoria") == "dati_insufficienti")
                    st["errori"] += int(es.stats.get("errore", 0))
                esito["per_provincia"][p] = {k: int(st.get(k, 0)) for k in (
                    "letti", "sbloccati", "valutati", "con_margine", "promosse", "dati_insufficienti")}
                if st.get("blocchi_errore"):
                    esito["per_provincia"][p]["blocchi_errore"] = int(st["blocchi_errore"])
                if esito["fermata"]:
                    break
                segna(f"prov:{p}")
                log.warning("RIVALUTA_200KM provincia %s %s", p, json.dumps(esito["per_provincia"][p]))
    finally:
        lucchetto.release()
    tot: Counter = Counter()
    for v in esito["per_provincia"].values():
        tot.update({k: n for k, n in v.items() if isinstance(n, int)})
    esito["totale"] = dict(tot)
    esito["minuti"] = round((adesso() - t0) / 60)
    log.warning("RIVALUTA_200KM %s", json.dumps(esito, ensure_ascii=False))
    if esito["fermata"]:
        raise RuntimeError(f"rivalutazione 200 km fermata ({esito['fermata']}): si riprende al prossimo avvio")
    try:
        (finito or passi.finito)(job)
    except Exception as e:
        log.warning("RIVALUTA_200KM passi non cancellati: %s", type(e).__name__)
    return esito
