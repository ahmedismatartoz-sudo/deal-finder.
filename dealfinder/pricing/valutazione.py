"""UNA sola strada per valutare e salvare un'auto (fase 2 del piano, 4/11/2026; studio 2 §2).

    valuta_e_scrivi(conn, ids, fonte)  → Esito (contatori, valutazioni, categoria salvata per auto)

La usano tutti: ricalcolo.esegui e ricalcolo.utilitarie (cron), tempo_reale.valuta_e_pubblica (sito, annunci nuovi)
e il lavoro dei commercianti lontani. Ognuno tiene la SUA selezione (quali id e cosa pubblicare), ma il calcolo ha
gli stessi ingressi per tutti:
- le stesse colonne dell'annuncio (COLONNE: anche damage_declared, problem_search, descrizione intera);
- la stessa funzione del telefono (telefono());
- gli stessi confronti (ricalcolo.MARKET_SQL sulla zona di casa, ripiego Nord / Italia di ricalcolo._valuta_base);
- valore reale, stima AI e difetti letti dall'AI: l'ULTIMO valore disponibile, con la sua data (avviso se vecchio,
  ma non sparisce); ricambi dall'ultima riga `valuations`; fattori chiesto → venduto dall'ultima `calibrazione`;
- le stesse regole (dealfinder/regole.py) e la stessa soglia (scovo.soglia).

Scritture (guardia, anche nel database con la condizione dell'upsert): una valutazione salvata NON si sostituisce con
una fatta con MENO confronti (n), salvo che l'auto sia cambiata (prezzo, km, telefono, danni: impronta_auto), che le
regole siano cambiate (impronta_regole) o che la riga abbia più di regole.Scrittura.ore_validita ore. Le auto escluse
dalle regole di ammissione si scrivono comunque (categoria "esclusa" + motivo), così una riga vecchia "candidato" non
resta sul sito.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .. import regole as RG
from . import policy_v2 as P

log = logging.getLogger("valutazione")

COLONNE = ("l.id, l.source, l.url, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox, "
           "l.power_kw, l.price_eur, l.price_flags, l.seller_type, l.damage_class, l.damage_items, l.vehicle_id, "
           "l.vehicle_identity, l.stage_reason, l.title, l.description, l.telefono_ok, l.damage_declared, "
           "l.problem_search, l.province, l.status, l.last_seen_at")

UPSERT_SQL = """INSERT INTO valutazioni_policy (listing_id, policy_version, categoria, margine, riassunto, dati, prima,
                   n, impronta_auto, impronta_regole, fonte, metodo)
   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
   ON CONFLICT (listing_id, policy_version) DO UPDATE SET computed_at=now(), categoria=EXCLUDED.categoria,
     margine=EXCLUDED.margine, riassunto=EXCLUDED.riassunto, dati=EXCLUDED.dati,
     prima=COALESCE(EXCLUDED.prima, valutazioni_policy.prima), n=EXCLUDED.n, impronta_auto=EXCLUDED.impronta_auto,
     impronta_regole=EXCLUDED.impronta_regole, fonte=EXCLUDED.fonte, metodo=EXCLUDED.metodo
   WHERE valutazioni_policy.impronta_auto IS DISTINCT FROM EXCLUDED.impronta_auto
      OR valutazioni_policy.impronta_regole IS DISTINCT FROM EXCLUDED.impronta_regole
      OR valutazioni_policy.computed_at < now() - make_interval(hours => %s)
      OR (COALESCE(EXCLUDED.n, 0) >= COALESCE(valutazioni_policy.n, 0)
          AND NOT (%s AND COALESCE((valutazioni_policy.dati->'ai_confronti'->>'tolti')::int, 0) > 0))
      OR %s
   RETURNING listing_id"""
# 12/11 sera (controllo del giudice M5): penultimo parametro = proteggi (una riga giudicata dall'AI non si sostituisce
# con una NON giudicata entro ore_validita, anche se ha più confronti); ultimo = la nuova è giudicata (si scrive sempre)
# Database senza la migrazione 036 (colonne n, impronte, fonte, metodo): scrittura di prima, senza guardia nel database
# (la guardia resta in deve_scrivere, sulle righe lette).
UPSERT_SQL_VECCHIO = """INSERT INTO valutazioni_policy (listing_id, policy_version, categoria, margine, riassunto, dati, prima)
   VALUES (%s,%s,%s,%s,%s,%s,%s)
   ON CONFLICT (listing_id, policy_version) DO UPDATE SET computed_at=now(), categoria=EXCLUDED.categoria,
     margine=EXCLUDED.margine, riassunto=EXCLUDED.riassunto, dati=EXCLUDED.dati,
     prima=COALESCE(EXCLUDED.prima, valutazioni_policy.prima)
   RETURNING listing_id"""
_SCHEMA = {"nuovo": None}          # None = da provare; False = colonne della 036 mancanti


@dataclass
class Esito:
    stats: Counter = field(default_factory=Counter)
    valutazioni: dict = field(default_factory=dict)      # id → valutazione (completa se tenuta, altrimenti compatta)
    salvata: dict = field(default_factory=dict)          # id → categoria della riga ora nel database
    telefono: dict = field(default_factory=dict)         # id → True / False / None

    def sul_sito(self, lid) -> bool:
        from ..web import scovo
        return self.salvata.get(lid) in scovo.CATEGORIE_SITO


# ---------------------------------------------------------------- ingressi

def telefono(r: dict) -> bool | None:
    """UNA regola per tutte le fonti: True se c'è il numero (bottone/servizio telefono_ok o numero scritto in titolo e
    descrizione INTERA), False se il controllo ha detto di no e nel testo non c'è, None se non si sa ancora."""
    from ..web.scovo import RE_LANDLINE, RE_MOBILE
    testo = f"{r.get('title') or ''} {r.get('description') or ''}"
    if r.get("telefono_ok") or RE_MOBILE.search(testo) or RE_LANDLINE.search(testo):
        return True
    return False if r.get("telefono_ok") is False else None


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _q(conn, sql, args, avviso: str):
    try:
        return conn.execute(sql, args).fetchall()
    except Exception as e:                      # tabella non ancora creata o colonna mancante: si valuta senza
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("%s non letto: %s", avviso, str(e)[:150])
        return []


_TUTTI = ("vr", "ai", "difetti", "parti", "calibra")


def ingressi(conn, ids: list, solo: tuple = _TUTTI) -> dict:
    """Dati esterni per questi annunci, gli stessi per ogni strada: l'ULTIMO valore disponibile, con la sua data
    (computed_at). Niente scadenza dura: se il lavoro notturno salta, la stima non cambia da sola (avviso nei log)."""
    ids = list(ids)
    out = {"vr": {}, "ai": {}, "difetti": {}, "parti": {}, "fattori_calibra": RG.fattori_calibrazione(None),
           "tassi_priori": None, "tassi_misurati": None, "dotazioni_misura": None}
    if "vr" in solo:
        out["vr"] = {r["listing_id"]: dict(r) for r in _q(conn,
                     "SELECT listing_id, v, p25, sigma, n, n_vendute, computed_at FROM valore_reale "
                     "WHERE listing_id = ANY(%s)", (ids,), "valore reale")}
    if "ai" in solo:
        out["ai"] = {r["listing_id"]: {"prezzo": r["prezzo"], "errore": r["errore"], "n": r["n_gruppo"],
                                       "livello": r["livello"], "computed_at": r["computed_at"]}
                     for r in _q(conn, "SELECT DISTINCT ON (listing_id) listing_id, prezzo, errore, n_gruppo, livello, "
                                       "computed_at FROM stima_ai WHERE listing_id = ANY(%s) "
                                       "ORDER BY listing_id, computed_at DESC", (ids,), "stima AI")}
    if "difetti" in solo:
        out["difetti"] = {r["listing_id"]: dict(r) for r in _q(conn,
                          "SELECT listing_id, chiave, costo, grave, motivo FROM ai_difetti WHERE listing_id = ANY(%s)",
                          (ids,), "ai_difetti")}
    if "parti" in solo:
        # ricambi dall'analisi (AI/manuale): le righe scritte dalla valutazione stessa (engine pv*) hanno 0 e non contano
        out["parti"] = {r["listing_id"]: dict(r) for r in _q(conn,
                        "SELECT DISTINCT ON (listing_id) listing_id, parts_cost_low, parts_cost_high FROM valuations "
                        "WHERE listing_id = ANY(%s) AND COALESCE(engine_version, '') NOT LIKE %s "
                        "ORDER BY listing_id, created_at DESC", (ids, P.POLICY_VERSION + "%"), "ricambi")}
    if "calibra" in solo:
        righe = _q(conn, "SELECT dati FROM calibrazione ORDER BY at DESC LIMIT 1", (), "calibrazione")
        dati = (_j(righe[0]["dati"]) or {}) if righe else {}
        if RG.calibrazione_vecchia(dati):
            log.warning("CALIBRA_VECCHIA la misura chiesto -> venduto è stata fatta con un'altra correzione anno/km: "
                        "va rifatta (CALIBRA=1)")
        out["fattori_calibra"] = RG.fattori_calibrazione(dati.get("fasce") or {}, misurate_vr=dati.get("fasce_vr") or {},
                                                    per_M=dati.get("fasce_su") == "M")
        # 12/11 sera (margini fase 2): tassi anno/km per marca (pooling) e premi di 4x4/optional, dalla stessa misura
        # (ricontrollo M2) le valutazioni usano i tassi solo se la misura stessa è stata fatta con il pooling: al primo
        # CALIBRA nuovo (tassi_pooling=false) resta la regola di prima, dal secondo misura e applicazione coincidono
        out["tassi_priori"] = (dati.get("tassi") or None) if dati.get("tassi_pooling") else None
        out["tassi_misurati"] = dati.get("tassi") or None          # per il lavoro CALIBRA, che li legge sempre
        out["dotazioni_misura"] = dati.get("dotazioni") or None
    return out


def _rischio(r) -> bool:
    from .ricalcolo import RISCHIO
    parts = {str((d or {}).get("part")) for d in (_j(r.get("damage_items")) or [])}
    return r.get("damage_class") == "alto_rischio" or bool(parts & RISCHIO)


def _danni_nel_testo(r: dict) -> bool:
    from ..core.models import Listing
    from ..core.normalize import problem_hint
    return bool(problem_hint(Listing(source=r.get("source") or "", source_id="", url="", title=r.get("title"),
                                     description=r.get("description"))))


def prepara(r: dict, ing: dict, rg: RG.Regole | None = None) -> tuple[dict, str | None]:
    """(t per policy_v2.valuta, motivo di esclusione o None). Stessa funzione per ogni strada."""
    rg = rg or RG.regole()
    a = rg.ammissione
    lid = r["id"]
    tel = telefono(r)
    pa = ing["parti"].get(lid) or {}
    t = dict(r, vehicle_identity=_j(r.get("vehicle_identity")), rischio_strutturale=_rischio(r),
             parts_cost_low=pa.get("parts_cost_low"), parts_cost_high=pa.get("parts_cost_high"),
             ai=ing["ai"].get(lid), vr=ing["vr"].get(lid), fattori_calibra=ing["fattori_calibra"], telefono=tel,
             tassi_priori=ing.get("tassi_priori"), dotazioni_misura=ing.get("dotazioni_misura"))
    privato = (r.get("seller_type") or "privato") != "commerciante"
    # prezzo molto basso ammesso solo con il numero, e mai per un commerciante (prezzo esca)
    t["targa_e_telefono"] = bool(tel is True and (privato or not rg.antitruffa.eccezione_solo_privati)
                                 and rg.antitruffa.basso_solo_telefono)
    danni = bool(r.get("damage_declared")) or _danni_nel_testo(r)
    t["damage_declared"] = False
    t["danni_testo"] = danni
    d = ing["difetti"].get(lid)
    if d is not None and d.get("chiave") and r.get("description") is not None:
        # 13/11 (giudice N3): lettura dei difetti fatta su un ALTRO testo (annuncio cambiato): non vale più
        from ..ai.difetti import chiave as _chiave_difetti
        if d["chiave"] != _chiave_difetti(r.get("description")):
            d = None
    if danni:
        if d is None:
            return t, "danni_da_leggere"
        if d.get("grave"):
            return t, "danno_grave"
        t["costo_difetti"] = int(d.get("costo") or 0)
        t["difetti_motivo"] = d.get("motivo")
    if not (r.get("make") and r.get("model") and r.get("fuel")) or not r.get("price_eur"):
        return t, "dati_incompleti"
    if a.km_obbligatori and r.get("mileage_km") is None:
        return t, "km_mancanti"
    if r.get("mileage_km") is not None and int(r["mileage_km"]) > a.km_max:
        return t, f"km_oltre_{a.km_max}"
    if r.get("year") is None or int(r["year"]) < a.anno_min:
        return t, f"anno_prima_del_{a.anno_min}"
    if a.telefono_obbligatorio and tel is False:
        return t, "senza_numero"
    return t, None


def impronta_auto(t: dict) -> str:
    """Cambia se cambia l'auto (prezzo, km, anno, dati tecnici, telefono, danni, ricambi, difetti AI, segnalazioni)."""
    campi = [t.get(k) for k in ("price_eur", "year", "mileage_km", "fuel", "gearbox", "power_kw", "version_raw",
                                "damage_class", "danni_testo", "problem_search", "telefono", "parts_cost_high",
                                "costo_difetti", "stage_reason", "seller_type", "status")]
    campi.append(sorted(t.get("price_flags") or []))
    return hashlib.sha1(json.dumps(campi, default=str).encode()).hexdigest()[:16]


def impronta_regole(rg: RG.Regole | None = None) -> str:
    """Impronta delle regole (regole.impronta: valori + versione del file + soglie). Mai la revisione del codice."""
    return RG.impronta(rg)


def esclusa(t: dict, motivo: str, rg: RG.Regole | None = None) -> dict:
    rg = rg or RG.regole()
    testi = {"danni_da_leggere": "danni scritti nell'annuncio non ancora letti dall'AI",
             "danno_grave": "danno grave letto dall'AI", "dati_incompleti": "marca, modello, alimentazione o prezzo mancanti",
             "km_mancanti": "km non indicati", "senza_numero": "nessun numero del venditore"}
    return {"policy": P.POLICY_VERSION, "policy_rev": P.POLICY_REV, "regole": rg.versione, "id": t.get("id"),
            "acquisto": t.get("price_eur"), "categoria": "esclusa", "motivo_esclusione": motivo, "n": 0,
            "motivi": [testi.get(motivo) or motivo.replace("_", " ")]}


# ---------------------------------------------------------------- guardia sulle scritture

def giudicata(v: dict | None) -> bool:
    """La valutazione è passata dal giudice AI e ha tolto confronti (12/11 sera, rapporto 2 E5)."""
    return bool(isinstance(v, dict) and ((v.get("ai_confronti") or {}).get("tolti") or 0) > 0)


def deve_scrivere(esistente: dict | None, n_nuovo: int, imp_auto: str, imp_regole: str, now=None,
                  ore: int | None = None, giudicata_nuova: bool = False) -> tuple[bool, str]:
    """Stessa regola della condizione SQL di UPSERT_SQL (qui per i contatori e per i test).
    12/11 sera (regole.Scrittura.giudice_vince): una valutazione passata dal giudice AI con confronti tolti si scrive
    sempre (prima restava rifiutata fino a 36 ore perché il giudice riduce n)."""
    if not esistente:
        return True, "nuova"
    if esistente.get("impronta_auto") != imp_auto:
        return True, "auto_cambiata"
    if esistente.get("impronta_regole") != imp_regole:
        return True, "regole_cambiate"
    vince = RG.regole().scrittura.giudice_vince
    if giudicata_nuova and vince:
        return True, "giudicata"
    now = now or datetime.now(timezone.utc)
    ore = RG.regole().scrittura.ore_validita if ore is None else ore
    at = esistente.get("computed_at")
    if at is not None:
        if isinstance(at, str):
            at = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if now - at > timedelta(hours=ore):
            return True, "vecchia"
    if int(n_nuovo or 0) >= int(esistente.get("n") or 0):
        if vince and int(esistente.get("tolti") or 0) > 0:
            return False, "giudicata_protetta"          # la riga salvata è giudicata, la nuova no
        return True, "dati_pari_o_piu"
    return False, "meno_confronti"


# ---------------------------------------------------------------- calcolo e scrittura

def _ordina(conn, ids: list) -> list:
    righe = conn.execute("SELECT id, make, model, fuel FROM listings WHERE id = ANY(%s)", (list(ids),)).fetchall()
    righe = sorted(righe, key=lambda r: (r["make"] or "", r["model"] or "", r["fuel"] or "", r["id"]))
    return [r["id"] for r in righe]


def valuta_e_scrivi(conn, ids: list, fonte: str, extra: dict | None = None, tieni=None, blocco: int = 300,
                    soglia_fn=None) -> Esito:
    """Valuta e salva le auto `ids` (unico punto d'ingresso). fonte: 'cron', 'cron-utilitarie', 'web-nuovi',
    'commercianti', ... (solo informazione, nella riga). extra: {id: {chiave: valore}} aggiunto alla valutazione
    (es. sezione). tieni(v) -> bool: valutazioni da restituire complete (le altre in forma compatta).
    Il conn deve avere righe come dict. Ogni auto è una transazione: un errore non ferma le altre."""
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    from .ricalcolo import MARKET_SQL, valuta_con_ripiego
    es = Esito()
    if not ids:
        return es
    rg = RG.regole()
    cfg = P.PolicyConfig(rg)
    imp_r = impronta_regole(rg)
    ore = rg.scrittura.ore_validita
    soglia_fn = soglia_fn or scovo.soglia
    now = datetime.now(timezone.utc)
    ordine = _ordina(conn, ids)
    from . import dotazioni as DT
    from . import rivendita as RV
    usa_dot = bool(rg.trazione.attiva or rg.dotazioni.aggiusta)
    gruppo, mercato, cache, commercianti = None, [], {}, None
    vecchi_vr = 0
    for k in range(0, len(ordine), blocco):
        pezzo = ordine[k:k + blocco]
        righe = {r["id"]: r for r in conn.execute(f"SELECT {COLONNE} FROM listings l WHERE l.id = ANY(%s)",
                                                  (pezzo,)).fetchall()}
        ing = ingressi(conn, pezzo)
        prima = _salvate(conn, pezzo)
        dot_auto = DT.carica_ids(conn, pezzo) if usa_dot else {}
        for lid in pezzo:
            r = righe.get(lid)
            if r is None:
                continue
            r = dict(r)
            try:
                t, motivo = prepara(r, ing, rg)
                es.telefono[lid] = t.get("telefono")
                if motivo:
                    v = esclusa(t, motivo, rg)
                    es.stats[f"esclusa:{motivo}"] += 1
                    if motivo == "danni_da_leggere":
                        _in_coda_danni(conn, lid)
                else:
                    key = (r["make"], r["model"], r["fuel"])
                    if key != gruppo:
                        gruppo, cache = key, {}
                        mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                                   for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
                        # 12/11: annunci di commercianti del gruppo (venduti e attivi) per la rivendita
                        commercianti = RV.carica(conn, *key, HOME_REGION_SQL, rg) if rg.rivendita.attiva else None
                        # 12/11 sera (margini fase 2 punto 4): trazione e optional dalla scheda tecnica (dati_auto)
                        if usa_dot:
                            mercato = DT.arricchisci(mercato, DT.carica_mercato(conn, mercato))
                    t["commercianti"] = commercianti
                    if usa_dot and lid in dot_auto:
                        t.update(dot_auto[lid])
                    v = valuta_con_ripiego(conn, t, mercato, soglia_fn(int(r["price_eur"])), cfg, cache)
                    vr_at = (t.get("vr") or {}).get("computed_at")
                    if vr_at is not None and hasattr(vr_at, "tzinfo"):
                        vr_at = vr_at if vr_at.tzinfo else vr_at.replace(tzinfo=timezone.utc)
                        if now - vr_at > timedelta(days=rg.valore_reale.max_giorni_avviso):
                            vecchi_vr += 1
                            v.setdefault("ipotesi", []).append(f"valore reale del {str(vr_at)[:10]}: più vecchio di "
                                                               f"{rg.valore_reale.max_giorni_avviso} giorni")
                    es.stats[f"categoria:{v['categoria']}"] += 1
                v["fonte_valutazione"] = fonte
                if extra and lid in extra:
                    v.update(extra[lid])
                imp_a = impronta_auto(t)
                giu = giudicata(v) and rg.scrittura.giudice_vince
                ok, perche = deve_scrivere(prima.get(lid), v.get("n") or 0, imp_a, imp_r, now, ore, giu)
                if v.get("giudice_mancante") and prima.get(lid):
                    ok, perche = False, "giudice_mancante"      # 13/11 (N3): resta la valutazione precedente
                es.stats[f"scrittura:{perche}"] += 1
                if ok:
                    pr = v.pop("prima", None)
                    scritta = _scrivi(conn, lid, v, pr, imp_a, imp_r, fonte, ore, giu)
                    if not scritta:
                        es.stats["scrittura:rifiutata_dal_database"] += 1
                        ok = False
                if ok:
                    es.salvata[lid] = v["categoria"]
                else:
                    es.salvata[lid] = (prima.get(lid) or {}).get("categoria")
                    log.info("VALUTAZIONE_RIFIUTATA %s fonte=%s n=%s < %s", lid, fonte, v.get("n"),
                             (prima.get(lid) or {}).get("n"))
                completa = v["categoria"] in scovo.CATEGORIE_SITO or (tieni is not None and tieni(v))
                es.valutazioni[lid] = v if completa else dict(P.riassunto(v), id=lid, motivi=(v.get("motivi") or [])[:2])
                conn.commit()
            except Exception as e:                  # un'auto sbagliata non ferma le altre
                try:
                    conn.rollback()
                except Exception:
                    pass
                es.stats["errore"] += 1
                if es.stats["errore"] <= 5:
                    log.warning("VALUTAZIONE %s non riuscita (%s): %s", lid, fonte, str(e)[:200])
    if vecchi_vr:
        es.stats["valore_reale_vecchio"] = vecchi_vr
    rif = sum(v for k, v in es.stats.items() if k in ("scrittura:meno_confronti", "scrittura:rifiutata_dal_database",
                                                       "scrittura:giudicata_protetta"))
    log.warning("VALUTAZIONE fonte=%s auto=%d rifiutate=%d %s", fonte, len(ordine), rif,
                json.dumps(dict(es.stats), ensure_ascii=False))
    return es


def _salvate(conn, ids: list) -> dict:
    """Righe già salvate (per la guardia): n, impronte, data, categoria."""
    if _SCHEMA["nuovo"] is not False:
        righe = _q(conn, "SELECT listing_id, n, impronta_auto, impronta_regole, computed_at, categoria, "
                         "COALESCE((dati->'ai_confronti'->>'tolti')::int, 0) AS tolti "
                         "FROM valutazioni_policy WHERE listing_id = ANY(%s) AND policy_version = %s",
                   (ids, P.POLICY_VERSION), "valutazioni salvate")
        if righe or _SCHEMA["nuovo"]:
            return {r["listing_id"]: dict(r) for r in righe}
    righe = _q(conn, "SELECT listing_id, (dati->>'n')::int AS n, computed_at, categoria FROM valutazioni_policy "
                     "WHERE listing_id = ANY(%s) AND policy_version = %s", (ids, P.POLICY_VERSION), "valutazioni salvate")
    return {r["listing_id"]: dict(r) for r in righe}


def _scrivi(conn, lid, v: dict, pr, imp_a: str, imp_r: str, fonte: str, ore: int, giudicata_nuova: bool = False) -> bool:
    """Upsert con la guardia anche nel database (vale se due processi scrivono insieme). True se scritta."""
    riass = json.dumps(P.riassunto(v), default=str)
    dati = json.dumps(v, default=str)
    prj = json.dumps(pr, default=str) if pr else None
    if _SCHEMA["nuovo"] is not False:
        try:
            cur = conn.execute(UPSERT_SQL, (lid, P.POLICY_VERSION, v["categoria"], v.get("margine"), riass, dati, prj,
                                            int(v.get("n") or 0), imp_a, imp_r, fonte, v.get("metodo"), ore,
                                            bool(RG.regole().scrittura.giudice_vince), bool(giudicata_nuova)))
            _SCHEMA["nuovo"] = True
            return cur.fetchone() is not None if hasattr(cur, "fetchone") else True
        except Exception as e:
            if "column" not in str(e) or "does not exist" not in str(e):
                raise
            conn.rollback()
            _SCHEMA["nuovo"] = False
            log.warning("VALUTAZIONE migrazione 036 non applicata: scrittura senza colonne di guardia")
    cur = conn.execute(UPSERT_SQL_VECCHIO, (lid, P.POLICY_VERSION, v["categoria"], v.get("margine"), riass, dati, prj))
    return cur.fetchone() is not None if hasattr(cur, "fetchone") else True


def _in_coda_danni(conn, lid) -> None:
    """Annuncio con danni scritti e non ancora letti: in coda per la lettura AI (lavoro danni_in_attesa)."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS analisi_danni (listing_id BIGINT PRIMARY KEY, at TIMESTAMPTZ DEFAULT now())")
        conn.execute("INSERT INTO analisi_danni (listing_id) VALUES (%s) ON CONFLICT DO NOTHING", (lid,))
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
