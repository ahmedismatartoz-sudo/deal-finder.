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
      OR (valutazioni_policy.dati->>'giudice_mancante') = 'true'
      OR (COALESCE(valutazioni_policy.metodo, '') = 'base_vendute') <> (COALESCE(EXCLUDED.metodo, '') = 'base_vendute')
      OR (COALESCE(EXCLUDED.metodo, '') = 'base_vendute' AND valutazioni_policy.categoria = ANY(%s)
          AND NOT (EXCLUDED.categoria = ANY(%s)))
   RETURNING listing_id"""
METODO_BASE = "base_vendute"          # = valore_base.METODO (scritto anche nella condizione SQL qui sopra)
# 18/11: passaggio tra il calcolo di prima e valore_base (colonna metodo): si scrive sempre (deve_scrivere
# "calcolo_cambiato"), anche nel ritorno indietro con VALORE_BASE=vecchio. Col calcolo nuovo, una salvata buona
# (CATEGORIE_SITO, ultimi due parametri) sostituita da una non buona si scrive sempre ("uscita_dal_sito").
# 15/11 (giudice A2): una riga "in attesa del giudice" (giudice_mancante) si sostituisce sempre: la guardia dei meno
# confronti e la protezione delle giudicate valgono contro una valutazione vera, non contro un'attesa
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
    scritte: set = field(default_factory=set)            # id riscritti davvero in questo giro (15/11, giudice A2)
    telefono: dict = field(default_factory=dict)         # id → True / False / None
    perche: dict = field(default_factory=dict)           # id → motivo della guardia (17/11: per la promozione)
    vecchie: set = field(default_factory=set)            # id con la riga salvata più vecchia di ore_validita (17/11)

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


def tieni_precedente(v: dict, esistente: dict | None, perche: str) -> bool:
    """13/11 (giudice N3/N12): senza il giudice AI resta la valutazione precedente SOLO se l'auto e le regole sono le
    stesse (vecchia, dati pari o più, meno confronti). Se l'auto è cambiata (prezzo, testo, dati) o sono cambiate le
    regole, la precedente non vale più: si scrive la valutazione "in attesa" (dati_insufficienti) e l'auto esce dal
    sito finché il giudice non lavora, invece di restare col margine vecchio."""
    return bool(v.get("giudice_mancante") and esistente and perche in ("vecchia", "dati_pari_o_piu", "meno_confronti"))


def _cat_sito() -> set:
    from ..web import scovo
    return set(scovo.CATEGORIE_SITO)


def deve_scrivere(esistente: dict | None, n_nuovo: int, imp_auto: str, imp_regole: str, now=None,
                  ore: int | None = None, giudicata_nuova: bool = False,
                  metodo_nuovo: str | None = None, categoria_nuova: str | None = None) -> tuple[bool, str]:
    """Stessa regola della condizione SQL di UPSERT_SQL (qui per i contatori e per i test).
    12/11 sera (regole.Scrittura.giudice_vince): una valutazione passata dal giudice AI con confronti tolti si scrive
    sempre (prima restava rifiutata fino a 36 ore perché il giudice riduce n)."""
    if not esistente:
        return True, "nuova"
    if esistente.get("impronta_auto") != imp_auto:
        return True, "auto_cambiata"
    if esistente.get("impronta_regole") != imp_regole:
        return True, "regole_cambiate"
    if metodo_nuovo is not None and "metodo" in esistente \
            and (str(esistente.get("metodo") or "") == METODO_BASE) != (str(metodo_nuovo) == METODO_BASE):
        # 18/11: passaggio tra calcolo vecchio e valore_base (nei due sensi, anche VALORE_BASE=vecchio): il numero di
        # confronti dei due calcoli non è confrontabile, la nuova si scrive sempre
        return True, "calcolo_cambiato"
    if metodo_nuovo == METODO_BASE and categoria_nuova is not None and esistente.get("categoria") in _cat_sito() \
            and categoria_nuova not in _cat_sito():
        # 18/11 (giudice): col calcolo nuovo un'auto che non è più buona esce subito, anche con meno confronti
        return True, "uscita_dal_sito"
    if esistente.get("in_attesa"):
        return True, "in_attesa_giudice"                # 15/11 (giudice A2): la riga salvata aspetta il giudice
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

def tetto_attesa(fonte: str) -> int | None:
    """15/11 (giudice, prudenza sul budget): nel ricalcolo del cron (fonte "cron...") al massimo GIUDICE_ATTESA_PER_GIRO
    (150) chiamate al giudice per giro sulle righe in attesa (giudice_mancante); le altre restano in attesa al giro
    dopo (o le prende riprova_giudice), così il budget comune non finisce come il 13/11. None = nessun tetto."""
    import os
    if not str(fonte or "").startswith("cron"):
        return None
    return max(0, int(os.environ.get("GIUDICE_ATTESA_PER_GIRO", "150")))


def _ordina(conn, ids: list) -> list:
    righe = conn.execute("SELECT id, make, model, fuel FROM listings WHERE id = ANY(%s)", (list(ids),)).fetchall()
    righe = sorted(righe, key=lambda r: (r["make"] or "", r["model"] or "", r["fuel"] or "", r["id"]))
    return [r["id"] for r in righe]


def valuta_e_scrivi(conn, ids: list, fonte: str, extra: dict | None = None, tieni=None, blocco: int = 300,
                    soglia_fn=None, pubblica: bool | None = None, pausa_gruppo: float = 0.0) -> Esito:
    """Valuta e salva le auto `ids` (unico punto d'ingresso). fonte: 'cron', 'cron-utilitarie', 'web-nuovi',
    'commercianti', ... (solo informazione, nella riga). extra: {id: {chiave: valore}} aggiunto alla valutazione
    (es. sezione). tieni(v) -> bool: valutazioni da restituire complete (le altre in forma compatta).
    pubblica: True = alla fine promuove sul sito le auto buone (promuovi); None = solo per le fonti di
    FONTI_CHE_PUBBLICANO (riprova del giudice, recupero dei confronti: prima scrivevano "opportunita"/"candidato" ma
    l'auto restava fuori dal sito, stage 'nuovo'); False = mai (chi pubblica da sé: tempo reale, utilitarie, ...).
    Il conn deve avere righe come dict. Ogni auto è una transazione: un errore non ferma le altre.
    18/11 (cliente): il valore di mercato è quello di pricing/valore_base.py (auto vendute e commercianti, nessuna AI);
    il calcolo di prima (policy_v2, ripiego, cascata, giudice AI) resta spento dietro VALORE_BASE=vecchio.
    pausa_gruppo: secondi di pausa a ogni cambio di gruppo marca/modello/alimentazione (rivalutazione nel sito)."""
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    from . import valore_base as VB
    from .ricalcolo import MARKET_SQL, valuta_con_ripiego
    es = Esito()
    nuovo = VB.attivo()
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
    basi, n_gruppo = {}, 0
    vecchi_vr = 0
    tetto = tetto_attesa(fonte)
    from ..ai import giudice as GI
    for k in range(0, len(ordine), blocco):
        pezzo = ordine[k:k + blocco]
        righe = {r["id"]: r for r in conn.execute(f"SELECT {COLONNE} FROM listings l WHERE l.id = ANY(%s)",
                                                  (pezzo,)).fetchall()}
        # col valore nuovo servono solo i ricambi e i difetti letti dall'AI (prepara): niente valore reale, stima AI,
        # calibrazione né dotazioni (letture risparmiate)
        ing = ingressi(conn, pezzo, solo=("difetti", "parti") if nuovo else _TUTTI)
        prima = _salvate(conn, pezzo)
        dot_auto = DT.carica_ids(conn, pezzo) if usa_dot and not nuovo else {}
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
                    if nuovo:
                        v["metodo"] = METODO_BASE      # scritta dal calcolo nuovo (guardia al cambio di calcolo)
                    es.stats[f"esclusa:{motivo}"] += 1
                    if motivo == "danni_da_leggere":
                        _in_coda_danni(conn, lid)
                elif not nuovo and tetto is not None and (prima.get(lid) or {}).get("in_attesa") \
                        and es.stats["giudice_attesa_chiamate"] >= tetto:
                    es.stats["attesa_rimandata_tetto_giudice"] += 1   # resta in attesa: nessuna chiamata, nessuna scrittura
                    es.salvata[lid] = (prima.get(lid) or {}).get("categoria")
                    conn.rollback()
                    continue
                elif nuovo:
                    # 18/11: valore dalle auto vendute e dai commercianti (pricing/valore_base.py); una lettura per
                    # gruppo e zona. Il gruppo si segna SOLO dopo una lettura riuscita: se VB.carica fallisce, l'auto
                    # dopo la rilegge (prima restava la base del gruppo precedente)
                    key = (r["make"], r["model"], r["fuel"])
                    zona = VB.zona_di(r.get("province"))
                    if key != gruppo:
                        if gruppo is not None and pausa_gruppo:
                            __import__("time").sleep(pausa_gruppo)
                        nuova_base = VB.carica(conn, *key, zona=zona)
                        gruppo, basi, n_gruppo = key, {zona: nuova_base}, 0
                    elif zona not in basi:
                        basi[zona] = VB.carica(conn, *key, zona=zona)
                    n_gruppo += 1
                    if pausa_gruppo and n_gruppo % 20 == 0:
                        __import__("time").sleep(0.01)           # gruppi grandi: il sito resta reattivo
                    v = VB.valuta(t, basi[zona], soglia_fn(int(r["price_eur"])), rg, cfg)
                    es.stats[f"categoria:{v['categoria']}"] += 1
                    es.stats["valore_base:" + str(v.get("fonte_base") or "nessuna_stima")] += 1
                else:
                    key = (r["make"], r["model"], r["fuel"])
                    if key != gruppo:
                        nuovo_m = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                                   for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
                        # 12/11: annunci di commercianti del gruppo (venduti e attivi) per la rivendita
                        nuovi_c = RV.carica(conn, *key, HOME_REGION_SQL, rg) if rg.rivendita.attiva else None
                        # 12/11 sera (margini fase 2 punto 4): trazione e optional dalla scheda tecnica (dati_auto)
                        if usa_dot:
                            nuovo_m = DT.arricchisci(nuovo_m, DT.carica_mercato(conn, nuovo_m))
                        # 18/11 (giudice): il gruppo si segna solo a lettura riuscita (un errore non lascia i confronti
                        # del gruppo precedente all'auto dopo)
                        gruppo, cache, mercato, commercianti = key, {}, nuovo_m, nuovi_c
                    t["commercianti"] = commercianti
                    if usa_dot and lid in dot_auto:
                        t.update(dot_auto[lid])
                    _t0 = __import__("time").time()
                    v = valuta_con_ripiego(conn, t, mercato, soglia_fn(int(r["price_eur"])), cfg, cache)
                    if tetto is not None and (prima.get(lid) or {}).get("in_attesa") and GI.esito(lid, _t0) == "chiamata":
                        es.stats["giudice_attesa_chiamate"] += 1
                    vr_at = (t.get("vr") or {}).get("computed_at")
                    if vr_at is not None and hasattr(vr_at, "tzinfo"):
                        vr_at = vr_at if vr_at.tzinfo else vr_at.replace(tzinfo=timezone.utc)
                        if now - vr_at > timedelta(days=rg.valore_reale.max_giorni_avviso):
                            vecchi_vr += 1
                            v.setdefault("ipotesi", []).append(f"valore reale del {str(vr_at)[:10]}: più vecchio di "
                                                               f"{rg.valore_reale.max_giorni_avviso} giorni")
                    es.stats[f"categoria:{v['categoria']}"] += 1
                    if isinstance(v.get("cascata"), dict):       # 16/11: cascata dei confronti (riga VALUTAZIONE)
                        es.stats["cascata:" + str(v["cascata"].get("usata") or "nessun_aiuto")] += 1
                v["fonte_valutazione"] = fonte
                if extra and lid in extra:
                    v.update(extra[lid])
                imp_a = impronta_auto(t)
                giu = giudicata(v) and rg.scrittura.giudice_vince
                # 17/11: l'ora di ADESSO per ogni auto (un giro lungo dura più di un'ora: con l'ora d'inizio la riga
                # salvata risultava meno "vecchia" di quanto la vede la condizione SQL, che usa now() alla scrittura)
                ok, perche = deve_scrivere(prima.get(lid), v.get("n") or 0, imp_a, imp_r,
                                           datetime.now(timezone.utc), ore, giu, metodo_nuovo=v.get("metodo"),
                                           categoria_nuova=v.get("categoria"))
                if perche == "vecchia":
                    # 17/11 (giudice): il motivo ORIGINALE si salva prima di "giudice_mancante": una riga salvata più
                    # vecchia di ore_validita resta nel database (tieni_precedente) ma NON si pubblica (margine su un
                    # mercato vecchio)
                    es.vecchie.add(lid)
                if tieni_precedente(v, prima.get(lid), perche):
                    ok, perche = False, "giudice_mancante"      # 13/11 (N3): resta la valutazione precedente
                es.stats[f"scrittura:{perche}"] += 1
                es.perche[lid] = perche
                if perche == "meno_confronti" and v.get("categoria") in scovo.CATEGORIE_SITO \
                        and (prima.get(lid) or {}).get("categoria") not in scovo.CATEGORIE_SITO:
                    # 17/11: quante buone nuove restano fuori per la guardia (resta la salvata, più prudente)
                    es.stats["scrittura:meno_confronti_buona_non_scritta"] += 1
                if ok:
                    pr = v.pop("prima", None)
                    scritta = _scrivi(conn, lid, v, pr, imp_a, imp_r, fonte, ore, giu)
                    if not scritta:
                        es.stats["scrittura:rifiutata_dal_database"] += 1
                        ok = False
                if ok:
                    es.salvata[lid] = v["categoria"]
                    es.scritte.add(lid)
                else:
                    es.salvata[lid] = (prima.get(lid) or {}).get("categoria")
                    log.info("VALUTAZIONE_RIFIUTATA %s fonte=%s n=%s < %s", lid, fonte, v.get("n"),
                             (prima.get(lid) or {}).get("n"))
                completa = v["categoria"] in scovo.CATEGORIE_SITO or (tieni is not None and tieni(v))
                es.valutazioni[lid] = v if completa else dict(P.riassunto(v), id=lid, motivi=(v.get("motivi") or [])[:2],
                                                              **({"giudice_mancante": True} if v.get("giudice_mancante") else {}))
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
    if pubblica or (pubblica is None and fonte in FONTI_CHE_PUBBLICANO):
        promuovi(conn, es, fonte, es.stats)
    rif = sum(v for k, v in es.stats.items() if k in ("scrittura:meno_confronti", "scrittura:rifiutata_dal_database",
                                                       "scrittura:giudicata_protetta"))
    log.warning("VALUTAZIONE fonte=%s auto=%d rifiutate=%d %s", fonte, len(ordine), rif,
                json.dumps(dict(es.stats), ensure_ascii=False))
    return es


# ---------------------------------------------------------------- promozione sul sito (17/11)
# Prima la scelta di cosa mettere sul sito era solo in tempo_reale.valuta_e_pubblica (e copie in ricalcolo/app.py), con
# tre buchi: (1) la riprova del giudice e il recupero dei confronti scrivevano "opportunita"/"candidato" ma l'auto
# restava con stage 'nuovo' (fuori dal sito finché un altro giro non la riprendeva, se la riprendeva); (2) una
# valutazione nuova buona rifiutata dalla guardia con la salvata buona di ALTRA categoria (candidato ↔ opportunita)
# non si pubblicava; (3) la valutazione precedente tenuta perché il giudice non ha risposto (tieni_precedente: "resta la
# valutazione precedente") valeva per le auto già sul sito ma non per le altre. In più la pubblicazione rimetteva sul
# sito annunci scartati per sempre (doppione, truffa, prezzo finto, dati incoerenti, analisi manuale).
FONTI_CHE_PUBBLICANO = ("riprova-giudice", "cron-recupero")
SCARTI_FISSI = ("doppione", "truffa", "prezzo_finto", "dati_incoerenti")     # = censimento.SCARTI_PRIMA, ricalcolo.UTIL_SQL

# 17/11 (giudice): mai gli annunci "solo confronto" (stage 'mercato' di collect: mercato_nord "mai proposta",
# fuori_regole fuori km/anno) né quelli col solo prezzo del finanziamento. Le 'mercato' del modello dei prezzi
# (modello_prezzi: solo una stima veloce scartata) restano promuovibili come prima del 17/11 (tempo reale): la
# valutazione completa vale più della stima. Il motivo si confronta con la prima parte (verify.mark aggiunge " | ...").
NON_PROPOSTE = ("mercato_nord", "fuori_regole")
# 17/11 (unione notte-09 + notte-10): il motivo si taglia al PRIMO '|' come scovo.motivo_stadio_sql("stage_reason")
# (prima qui ' |': "truffa|x" senza spazio non risultava uno scarto fisso). Testo copiato per non importare il sito
# all'avvio del modulo; tests/test_notte_unione.py controlla che resti identico.
_MOTIVO_SQL = "btrim(split_part(COALESCE(stage_reason, ''), '|', 1))"
PROMUOVI_SQL = """UPDATE listings SET stage = 'approfondito', stage_reason = %s,
       deep_at = CASE WHEN stage = 'approfondito' THEN COALESCE(deep_at, now()) ELSE now() END, last_checked_at = now()
 WHERE id = %s
   AND """ + _MOTIVO_SQL + """ <> ALL(%s)
   AND COALESCE(stage_reason, '') NOT LIKE 'analisi_manuale%%'
   AND NOT (stage = 'approfondito' AND """ + _MOTIVO_SQL + """ = 'incidentata')
   AND NOT (stage = 'mercato' AND """ + _MOTIVO_SQL + """ = ANY(%s))
   AND """ + _MOTIVO_SQL + """ <> 'solo_finanziamento'
 RETURNING id"""

VALUATIONS_SQL = """INSERT INTO valuations (listing_id, engine_version, private_median, resale_median, resale_prudent,
     n_comparables, comparables_used, confidence, confidence_reasons, parts_cost_low, parts_cost_high)
   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0)"""


def da_promuovere(es: Esito, lid) -> str | None:
    """Quale valutazione mettere sul sito: "nuova" (scritta adesso), "salvata" (quella nel database, buona, tenuta
    dalla guardia) o None. La riga salvata decide (è quella che il sito legge); se la nuova è stata rifiutata e NON è
    buona (es. meno confronti e margine sotto soglia) non si pubblica: in dubbio la più prudente."""
    from ..web import scovo
    cat = es.salvata.get(lid)
    if cat not in scovo.CATEGORIE_SITO:
        return None
    if lid in es.scritte:
        return "nuova"
    if lid in es.vecchie:
        return None                            # 17/11: salvata più vecchia di ore_validita: non si pubblica
    v = es.valutazioni.get(lid) or {}
    if v.get("categoria") in scovo.CATEGORIE_SITO:
        return "salvata"                       # rifiutata dalla guardia, ma buona anche la nuova
    if es.perche.get(lid) == "giudice_mancante":
        return "salvata"                       # la nuova aspetta il giudice: vale la precedente (tieni_precedente)
    return None


def _salvata_completa(conn, lid) -> dict | None:
    """La riga salvata, solo se più giovane di ore_validita (17/11: una riga vecchia non si pubblica, anche se la
    guardia l'ha tenuta perché il giudice non ha risposto)."""
    r = conn.execute("SELECT categoria, dati, now() - computed_at < make_interval(hours => %s) AS fresca "
                     "FROM valutazioni_policy WHERE listing_id = %s AND policy_version = %s",
                     (int(RG.regole().scrittura.ore_validita), lid, P.POLICY_VERSION)).fetchone()
    if not r or not r.get("fresca"):
        return None
    d = _j(r.get("dati")) or {}
    return dict(d, categoria=r.get("categoria") or d.get("categoria"))


def promuovi(conn, es: Esito, fonte: str, stats: Counter, pubblicate: list | None = None) -> int:
    """Mette sul sito (stage 'approfondito', stage_reason opportunita/da_verificare, riga in `valuations`) le auto di
    `es` buone secondo la riga salvata, con il numero del venditore (valutazione.telefono), salvo le eccezioni
    dichiarate (TR_SUBITO_SENZA_NUMERO: Subito non ancora controllato; TR_LUSSO_SENZA_NUMERO: da TR_SENZA_NUMERO_DA €).
    Non tocca gli annunci scartati per sempre (SCARTI_FISSI, analisi manuale) né le incidentate già sul sito.
    Ritorna quante auto ha pubblicato."""
    import os
    lusso_da = int(os.environ.get("TR_SENZA_NUMERO_DA", "60000"))
    n = 0
    for lid in list(es.salvata):
        quale = da_promuovere(es, lid)
        if quale is None:
            continue
        nuova = es.valutazioni.get(lid) or {}
        try:
            v = nuova if quale == "nuova" else _salvata_completa(conn, lid)
            if not v or v.get("categoria") != es.salvata.get(lid):
                if quale == "salvata" and not v:
                    stats["pubblica:salvata_vecchia_o_assente"] += 1
                continue                          # riga cambiata nel frattempo da un altro processo (o vecchia)
            tel = es.telefono.get(lid)
            if tel is not True:
                fonte_l = (conn.execute("SELECT source FROM listings WHERE id=%s", (lid,)).fetchone() or {}).get("source")
                ok = (tel is None and fonte_l == "subito" and os.environ.get("TR_SUBITO_SENZA_NUMERO", "0") == "1") or (
                    os.environ.get("TR_LUSSO_SENZA_NUMERO") == "1" and (v.get("acquisto") or 0) >= lusso_da)
                if not ok:
                    stats["valuta:senza_numero"] += 1
                    continue
            # opportunità piena solo se lo dicono la riga salvata e (se c'è) la nuova: in dubbio "da verificare"
            piena = v["categoria"] == "opportunita" and nuova.get("categoria") in ("opportunita", None, "dati_insufficienti")
            conf = "affidabile" if piena else "da_verificare"
            fatto = conn.execute(PROMUOVI_SQL, ("opportunita" if piena else "da_verificare", lid,
                                                list(SCARTI_FISSI), list(NON_PROPOSTE))).fetchone()
            if not fatto:
                conn.rollback()
                stats["pubblica:scarto_fisso_o_incidentata"] += 1
                continue
            conn.execute(VALUATIONS_SQL, (lid, P.POLICY_VERSION + "+" + fonte, v.get("M"), v.get("M"), v.get("R_finale"),
                                          v.get("n"), json.dumps(v.get("confronti") or [], default=str)[:200000], conf,
                                          (v.get("motivi") or [])[:5]))
            conn.commit()
            n += 1
            stats["pubblicate"] += 1
            if quale == "salvata":
                stats["pubblica:dalla_salvata"] += 1
            if pubblicate is not None:
                pubblicate.append(lid)            # 16/11: per la misura del ritardo (tr_coda.pubblicato_at)
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            stats["valuta:errore"] += 1
            if stats["valuta:errore"] <= 5:
                log.warning("PUBBLICA %s non riuscita (%s): %s", lid, fonte, str(e)[:200])
    return n


# 17/11 (riprova veloce): la riprova del giudice faceva ~460 auto/ora, ~12 s per auto quasi tutti in attesa della
# risposta dell'AI (giudice) e delle letture del mercato: lavoro di rete, non di calcolo. Più lavoratori (thread) in
# parallelo, ognuno con la SUA connessione al database; regole, margini e modello identici. Costante nel codice (il
# servizio web ha già troppe variabili d'ambiente).
RIPROVA_PARALLELI = 4


def rivaluta_in_parallelo(ids: list, fonte: str, connetti, budget_finito, paralleli: int = RIPROVA_PARALLELI,
                          blocco: int = 20, ves=None) -> list:
    """Rivaluta `ids` a blocchi di `blocco` auto con `paralleli` lavoratori. Ogni blocco: connessione propria
    (connetti(), un context manager come db.connect), controllo del budget PRIMA (se finito il blocco si salta e i
    successivi non partono), valuta_e_scrivi, commit. I blocchi partono nell'ordine di `ids` (coda FIFO: le auto in
    cima per prime). Nessuna connessione è condivisa tra thread.
    Restituisce [(pezzo, t0, Esito | None, errore | None)] nell'ordine dei blocchi; Esito None = blocco saltato
    (budget finito) o non riuscito (errore: si scrive nel log, i blocchi non ancora partiti non partono, le auto si
    riprovano al giro dopo)."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    ves = ves or valuta_e_scrivi
    pezzi = [list(ids[k:k + blocco]) for k in range(0, len(ids), blocco)]
    if not pezzi:
        return []
    fermo = threading.Event()

    def lavora(pezzo):
        if fermo.is_set():
            return None
        with connetti() as c:
            try:
                finito = budget_finito(c)
            finally:
                c.commit()                      # niente transazione aperta durante la chiamata all'AI
            if finito:
                fermo.set()
                return None
            t0 = time.time()
            try:
                es = ves(c, pezzo, fonte)
                c.commit()
            except BaseException:
                fermo.set()
                raise
            return t0, es

    n = max(1, min(int(paralleli or 1), len(pezzi)))
    out = []
    with ThreadPoolExecutor(max_workers=n, thread_name_prefix="riprova-giudice") as ex:
        futuri = [ex.submit(lavora, p) for p in pezzi]
        for p, f in zip(pezzi, futuri):
            try:
                r = f.result()
            except Exception as e:
                log.warning("RIPROVA_GIUDICE blocco di %d auto non riuscito: %s", len(p), str(e)[:200])
                out.append((p, 0.0, None, e))
                continue
            out.append((p, r[0], r[1], None) if r else (p, 0.0, None, None))
    return out


def esce_dall_attesa(es: "Esito", lid) -> bool:
    """15/11 (giudice A2, riprova del giudice): l'auto è stata davvero riscritta e NON è di nuovo "in attesa"
    (dati_insufficienti con giudice_mancante). Le altre si riprovano al giro dopo."""
    if lid not in es.scritte:
        return False
    return not (es.salvata.get(lid) == "dati_insufficienti" and (es.valutazioni.get(lid) or {}).get("giudice_mancante"))


def _salvate(conn, ids: list) -> dict:
    """Righe già salvate (per la guardia): n, impronte, data, categoria, in attesa del giudice."""
    if _SCHEMA["nuovo"] is not False:
        righe = _q(conn, "SELECT listing_id, n, impronta_auto, impronta_regole, computed_at, categoria, metodo, "
                         "COALESCE((dati->'ai_confronti'->>'tolti')::int, 0) AS tolti, "
                         "COALESCE((dati->>'giudice_mancante') = 'true', false) AS in_attesa "
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
                                            bool(RG.regole().scrittura.giudice_vince), bool(giudicata_nuova),
                                            sorted(_cat_sito()), sorted(_cat_sito())))
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
