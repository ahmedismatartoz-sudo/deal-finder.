"""Ricalcolo delle auto che il sito può mostrare (cron, voce "ricalcolo" di COLLECT_PRIMA).

    python -m dealfinder.pricing.ricalcolo

Dal 4/11 qui c'è solo la SCELTA delle auto da rivalutare (esegui: auto approfondite + sezione economica;
utilitarie: tutti i privati sani della zona) e cosa pubblicare. Il calcolo e la scrittura sono UNA sola funzione per
tutti, `pricing/valutazione.valuta_e_scrivi` (stesse colonne, stesso telefono, stessi confronti, stessa guardia sulle
scritture). Tolti: taratura (correggeva verso i prezzi CHIESTI), tetto manuale della rivendita, simulazioni con
regole spente (gemella, commercianti, soglie), auto "top" sotto il margine minimo del cliente.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

from .. import regole as RG
from . import policy_v2 as P

log = logging.getLogger("policy")

TARGET_COLS = ("l.id, l.source, l.url, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox, "
               "l.power_kw, l.price_eur, l.price_flags, l.seller_type, l.damage_class, l.damage_items, l.vehicle_id, "
               "l.vehicle_identity, l.stage_reason")
FIN_SQL = (r"COALESCE(description ~* '(solo|vincolat\w*|valid\w*|previo|tramite|con)\s+(a\s+|al\s+|il\s+|un\s+)?"
           r"finanziament', false) AS finanziamento")
# Gemelle: solo annunci attivi (prezzi chiesti). Le auto vendute entrano nel valore reale (prezzo di vendita), non qui.
# (Prima: POLICY_VENDUTE_GIORNI letto all'import, diverso tra sito e cron.)
_STATO_SQL = "status='attivo'"
_COLONNE_MERCATO = ("SELECT id, source, url, vehicle_id, vehicle_identity, make, model, version_raw, year, mileage_km, "
                    "fuel, gearbox, power_kw, price_eur, price_flags, seller_type, damage_class, status, stage_reason, "
                    "problem_search, first_seen_at, last_seen_at, last_checked_at, disappeared_at, " + FIN_SQL +
                    " FROM listings ")
MARKET_SQL = (_COLONNE_MERCATO + "WHERE " + _STATO_SQL + " AND make=%s AND model=%s AND fuel=%s "
              "AND price_eur IS NOT NULL AND {home}")
MARKET_ITALIA_SQL = (_COLONNE_MERCATO + "WHERE " + _STATO_SQL + " AND make=%s AND model=%s AND fuel=%s "
                     "AND price_eur BETWEEN " + str(int(RG.Zona().italia_pmin)) + " AND 400000")
RISCHIO = {"telaio_longheroni", "tetto_montanti", "airbag_esplosi", "frontale_profondo", "airbag",
           "modulo_airbag_volante", "modulo_airbag_passeggero", "centralina_airbag", "motore_sostituzione",
           "cambio_sostituzione", "alluvionata", "incendio"}


# Ripiego (richiesta del 17/10): se nel mercato di casa ci sono meno di 5 auto equivalenti, si cercano i confronti
# in tutto il Nord Italia della nostra base prezzi. L'auto proposta resta entro 150 km; la valutazione lo dichiara.
NORD = ["MI", "MB", "BG", "BS", "CO", "VA", "LC", "LO", "PV", "CR", "MN", "SO", "NO", "VC", "BI", "VB", "AL", "AT", "TO",
        "CN", "AO", "GE", "SV", "IM", "SP", "PC", "PR", "RE", "MO", "BO", "FE", "RA", "FC", "RN", "VR", "VI", "PD", "VE",
        "TV", "BL", "RO", "TN", "BZ", "UD", "PN", "GO", "TS"]
NORD_SQL = "province IN (" + ",".join(f"'{p}'" for p in NORD) + ")"


def valuta_con_ripiego(conn, t: dict, mercato: list, soglia: int, cfg, cache: dict, giudice=True) -> dict:
    """giudice: True (valutazioni vere), False (niente AI) o "esame" (misure: AI solo dall'archivio dei giudizi,
    mai una chiamata nuova, e riga di log ESAME_GIUDICE invece di GIUDICE_AI).
    12/11 (rapporto 2 E6, "stessa auto giudicata più volte nello stesso secondo con risultati diversi"): erano le
    misure di pricing/esame.py, che rifacevano la valutazione per ogni variante di regole chiamando il giudice e
    scrivendo GIUDICE_AI come se fossero valutazioni vere (e pagando una chiamata AI per ogni variante, perché i
    confronti di ogni variante sono diversi)."""
    v, lista = _valuta_base(conn, t, mercato, soglia, cfg, cache)
    if not giudice:
        return v
    return _giudice_ai(conn, t, v, lista, soglia, cfg, solo_archivio=(giudice == "esame"))


def _regole(cfg) -> RG.Regole:
    return getattr(cfg, "regole", None) or RG.regole()


def attesa_senza_ai() -> bool:
    """Senza chiave AI una valutazione da giudicare resta in attesa (vedi _giudice_ai)."""
    x = os.environ.get("GIUDICE_SENZA_AI_ATTESA")
    if x is not None and x.strip() != "":
        return x.strip() != "0"
    return bool(os.environ.get("RENDER"))


def _giudice_ai(conn, t: dict, v: dict, lista: list, soglia: int, cfg, solo_archivio: bool = False) -> dict:
    """28/10: l'AI toglie i confronti che non sono davvero la stessa auto, poi si rifà il calcolo (interruttore
    POLICY_GIUDICE_AI, regole.giudice_ai). Solo per le auto che andrebbero sul sito: le altre non costano niente."""
    if not _regole(cfg).giudice_ai or v.get("categoria") not in ("opportunita", "candidato") or not v.get("confronti"):
        return v
    from ..ai import giudice
    from ..ai import client as _AI
    # 16/11: senza chiave AI vale comunque un giudizio GIÀ DATO e archiviato (stessi confronti, stesso prezzo)
    senza_ai = not solo_archivio and not _AI.available()
    g = giudice.giudica(conn, t, v["confronti"], solo_archivio=solo_archivio or senza_ai)
    if not g:
        if solo_archivio:
            return v
        if senza_ai:
            # 16/11: processo SENZA chiave AI (es. il worker del tempo reale con il solo DATABASE_URL). Prima la
            # valutazione usciva con tutti i confronti non controllati e poteva andare sul sito; ora resta "in attesa"
            # (giudice_mancante) come quando il budget è finito: valutazione.py tiene la riga precedente se l'auto e le
            # regole sono le stesse, altrimenti scrive l'attesa e la riprendono riprova_giudice o il ricalcolo del cron
            # (che hanno la chiave). GIUDICE_SENZA_AI_ATTESA: "1" acceso, "0" comportamento di prima; se non impostata
            # vale acceso sui servizi Render (variabile RENDER) e spento in locale e nei test senza AI.
            if not attesa_senza_ai():
                return v
            v = dict(v, categoria="dati_insufficienti", giudice_mancante=True, giudice_senza_ai=True)
            v["motivi"] = list(v.get("motivi") or []) + ["confronti non ancora controllati dal giudice AI (AI non "
                                                         "configurata in questo processo): in attesa"]
            return v
        # 13/11 (giudice N3): il giudice AI non ha potuto controllare i confronti (budget comune finito, errore,
        # risposta illeggibile). Mai una valutazione con tutti i confronti non controllati (margine gonfiato): resta
        # la valutazione precedente (valutazione.py non scrive) oppure, se non c'è, l'auto è in attesa e fuori dal sito.
        v = dict(v, categoria="dati_insufficienti", giudice_mancante=True)
        v["motivi"] = list(v.get("motivi") or []) + ["confronti non ancora controllati dal giudice AI (AI ferma o budget "
                                                     "del giorno finito): in attesa"]
        return v
    if not g["via"]:
        v.setdefault("ipotesi", []).append(f"confronti controllati dall'AI: tutti validi ({v.get('n')})")
        v["ai_confronti"] = {"tolti": 0, "nota": g.get("nota")}
        return v
    via = set(g["via"])
    v2 = P.valuta(t, [m for m in lista if m.get("id") not in via], soglia, cfg)
    for k in ("fuori_zona", "fonte_confronti", "cascata"):      # 16/11: anche la traccia della cascata
        if v.get(k):
            v2[k] = v[k]
    v2["ai_confronti"] = {"tolti": len(via), "motivi": {str(k): m for k, m in list(g["via"].items())[:15]},
                          "nota": g.get("nota"), "prima": {"M": v.get("M"), "R": v.get("R_finale") or v.get("R"),
                                                           "margine": v.get("margine"), "n": v.get("n")}}
    v2.setdefault("ipotesi", []).append(f"l'AI ha tolto {len(via)} confronti non uguali (versione/allestimento/prezzo): "
                                        f"ricalcolato su {v2.get('n')}")
    logging.getLogger("giudice").warning(("ESAME_GIUDICE" if solo_archivio else "GIUDICE_AI") + " %s tolti %d: M %s→%s, margine %s→%s, %s→%s",
                                         t.get("id"), len(via), v.get("M"), v2.get("M"), v.get("margine"),
                                         v2.get("margine"), v.get("categoria"), v2.get("categoria"))
    return v2


def _valuta_base(conn, t: dict, mercato: list, soglia: int, cfg, cache: dict):
    """Zona di casa → ripiego Nord → tutta Italia (fascia alta), come prima; poi, SOLO se i confronti non arrivano al
    minimo che il sito chiede, la cascata del 16/11 (_cascata: auto sparite di recente, altre regioni). Le auto che
    arrivano già al minimo hanno esattamente la valutazione di prima."""
    v, lista = _valuta_zona(conn, t, mercato, soglia, cfg, cache)
    if not serve_cascata(v, t, cfg):
        return v, lista
    return _cascata(conn, t, v, lista, soglia, cfg, cache)


# ------------------------------------------------------------------ cascata dei confronti (16/11)
# Cliente (16/11): "non voglio cambiare nessuna regola, trova nuovi modi per far entrare più auto; se non ci sono
# abbastanza confronti vai a vedere le auto vendute, se non ci sono neanche lì guarda le auto di altre città".
# Le regole NON cambiano (stesse soglie, stesso minimo di confronti del sito, stessa policy, stessi livelli anno/km
# di policy_v2.seleziona): si allarga solo la RICERCA dei dati, in quest'ordine, fermandosi appena si arriva al minimo:
#   1. "vendute": le stesse auto SPARITE dagli annunci negli ultimi CASCATA_VENDUTE_GIORNI giorni (45), stessa zona
#      dei confronti già usati più il Nord (ultimo prezzo chiesto, mai un prezzo inventato);
#   2. "nord":    gli annunci attivi del resto del Nord (se non c'erano già) più le sparite;
#   3. "italia":  gli annunci attivi di tutta Italia della base prezzi (tutti i prezzi) più le sparite.
# Nessuna correzione per area (nel codice non ce n'è una misurata): il cliente ha osservato che scendendo verso sud
# i prezzi calano, quindi i confronti lontani tendono ad ABBASSARE il mercato. "Non gonfiare i prezzi": un passo
# della cascata che porta il mercato M SOPRA quello già stimato con i confronti di prima non si usa (resta il più
# prudente). Ogni valutazione passata dalla cascata lo dichiara (fonte_confronti, ipotesi, campo "cascata").
CASCATA_VENDUTE_GIORNI = 45
CASCATA_ITALIA_MAX = 4000            # righe al massimo per gruppo (memoria: 512 MB su Render)
_SPARITE_SQL = _COLONNE_MERCATO + (
    "WHERE status='scomparso' AND disappeared_at > now() - make_interval(days => %s) "
    "AND make=%s AND model=%s AND fuel=%s AND price_eur IS NOT NULL "
    "AND btrim(split_part(COALESCE(stage_reason, ''), '|', 1)) NOT IN ('doppione', 'truffa', 'prezzo_finto', 'dati_incoerenti') "
    "AND ({home}) ORDER BY disappeared_at DESC LIMIT 3000")
_ITALIA_TUTTI_SQL = (_COLONNE_MERCATO + "WHERE " + _STATO_SQL + " AND make=%s AND model=%s AND fuel=%s "
                     "AND price_eur IS NOT NULL ORDER BY last_seen_at DESC LIMIT " + str(CASCATA_ITALIA_MAX))


def cascata_attiva() -> bool:
    """Interruttore CASCATA_CONFRONTI (default acceso; "0" = comportamento di prima, senza cascata)."""
    return os.environ.get("CASCATA_CONFRONTI", "1").strip() != "0"


def _giorni_vendute() -> int:
    try:
        return max(1, int(os.environ.get("CASCATA_VENDUTE_GIORNI") or CASCATA_VENDUTE_GIORNI))
    except ValueError:
        return CASCATA_VENDUTE_GIORNI


def n_gate(v: dict) -> int:
    """Confronti che contano per il minimo del sito (n_gate di policy_v2.valuta; n se manca)."""
    return int(v.get("n_gate") if v.get("n_gate") is not None else (v.get("n") or 0))


def serve_cascata(v: dict, t: dict, cfg) -> bool:
    """La cascata serve solo alle auto rimaste "dati_insufficienti" PERCHÉ il mercato non si è potuto stimare (pochi
    confronti o confronti troppo dispersi: valutazione senza M) e con confronti sotto il minimo del sito (cfg.serve:
    8 sotto 10.000 €, 5 sopra). Le altre restano identiche:
    - un'auto già valutata (opportunità, candidato, non conveniente): la cascata potrebbe solo abbassarne il mercato;
    - un'auto con M ma ferma per una protezione (prezzo sotto il 55% o il 20% del mercato, danni, costi non stimati,
      prezzo dell'annuncio non affidabile): cercare altri confronti servirebbe solo ad aggirare la protezione."""
    if not cascata_attiva() or not (t.get("make") and t.get("model") and t.get("fuel") and t.get("price_eur")):
        return False
    if v.get("categoria") != "dati_insufficienti" or v.get("M") is not None:
        return False
    if set(t.get("price_flags") or []) & set(P.FLAG_ESCLUSI):
        return False                                     # prezzo dell'annuncio non affidabile: più confronti non servono
    danno = t.get("damage_class") or "sconosciuto"
    if danno not in ("nessuno", "sconosciuto") or t.get("rischio_strutturale") or t.get("damage_declared"):
        return False                                     # danni: la cascata non deve aggirare la protezione
    if v.get("mancanti") and any(m in v["mancanti"] for m in ("anno", "km", "alimentazione", "prezzo")) \
            or any(str(m).startswith("dati dell'auto incompleti") or str(m).startswith("km (scritto")
                   for m in (v.get("motivi") or [])[:1]):
        return False                                     # dati dell'auto mancanti: più confronti non servono
    return n_gate(v) < cfg.serve(t.get("price_eur"))


def mediana_attivi(v: dict) -> float | None:
    """Mediana dei prezzi CORRETTI (anno/km, solo verso il basso) dei confronti ATTIVI della valutazione (le auto
    sparite escluse). È il tetto del mercato nella cascata quando la valutazione di partenza non ha M."""
    p = [c["prezzo_corretto"] for c in (v.get("confronti") or [])
         if not c.get("venduta") and c.get("prezzo_corretto") is not None]
    return P.percentile(p, .5) if p else None


def _righe(conn, sql: str, args) -> list[dict]:
    return [dict(m, vehicle_identity=_j(m.get("vehicle_identity"))) for m in conn.execute(sql, args).fetchall()]


def _sparite(conn, key: tuple, giorni: int) -> list[dict]:
    """Auto uguali sparite di recente (zona di casa + Nord), segnate per policy_v2.pulisci. Lo stage_reason si riporta
    alla prima parte (verify.mark aggiunge " | scomparso ...") così i doppioni e le truffe restano esclusi."""
    from ..store import HOME_REGION_SQL
    righe = _righe(conn, _SPARITE_SQL.format(home=f"{NORD_SQL} OR {HOME_REGION_SQL}"), (giorni,) + tuple(key))
    return [dict(m, fonte_confronto="venduta",
                 stage_reason=(str(m.get("stage_reason") or "").split("|")[0].strip() or None)) for m in righe]


def _dati_cascata(conn, cache: dict, key: tuple, quale: str, giorni: int) -> list[dict]:
    """Righe della cascata per gruppo, in cache (un gruppo alla volta, come il ripiego Nord)."""
    ck = ("CASCATA", quale) + tuple(key)
    if ck not in cache:
        if quale == "vendute":
            cache[ck] = _sparite(conn, key, giorni)
        elif quale == "nord":
            cache[ck] = cache[key] if key in cache else _righe(conn, MARKET_SQL.format(home=NORD_SQL), key)
        else:
            cache[ck] = _righe(conn, _ITALIA_TUTTI_SQL, key)
    return cache[ck]


def _unisci(*liste) -> list[dict]:
    """Attivi prima delle sparite (pulisci tiene il primo di due doppioni), senza ripetere lo stesso annuncio."""
    visti, out = set(), []
    for lista in liste:
        for m in lista:
            i = m.get("id")
            if i is not None and i in visti:
                continue
            visti.add(i)
            out.append(m)
    attivi = [m for m in out if m.get("fonte_confronto") != "venduta"]
    return attivi + [m for m in out if m.get("fonte_confronto") == "venduta"]


def _cascata(conn, t: dict, v: dict, lista: list, soglia: int, cfg, cache: dict):
    key = (t["make"], t["model"], t["fuel"])
    serve = cfg.serve(t.get("price_eur"))
    giorni = _giorni_vendute()
    M0 = v.get("M")
    tetto0 = M0 if M0 is not None else mediana_attivi(v)    # serve_cascata: qui M0 è sempre None
    gia_nord = bool(v.get("fuori_zona"))
    traccia = []
    migliore, lista_m, fonte_m = v, lista, None
    try:
        vendute = _dati_cascata(conn, cache, key, "vendute", giorni)
        passi = [("vendute", lambda: _unisci(lista, vendute))]
        if not gia_nord:
            passi.append(("nord+vendute", lambda: _unisci(_dati_cascata(conn, cache, key, "nord", giorni), vendute)))
        passi.append(("italia+vendute", lambda: _unisci(_dati_cascata(conn, cache, key, "italia", giorni), vendute)))
        for nome, crea in passi:
            if nome == "vendute" and tetto0 is None:
                # nessun confronto attivo prima della cascata: le sole auto sparite (ultimo prezzo chiesto) non bastano
                traccia.append({"passo": nome, "esito": "nessun confronto attivo: sparite da sole non usate"})
                continue
            m = crea()
            if len(m) <= len(lista_m) and nome != "vendute":
                traccia.append({"passo": nome, "esito": "nessun dato in più"})
                continue
            v2 = P.valuta(t, m, soglia, cfg)
            n2 = n_gate(v2)
            if n2 <= n_gate(migliore):
                traccia.append({"passo": nome, "n": n2, "esito": "non più confronti"})
                continue
            # non gonfiare i prezzi: M mai sopra la mediana dei confronti ATTIVI trovati prima della cascata (o, se
            # non ce n'erano, di quelli attivi di questo passo) né sopra un passo già accettato: il valore più basso
            tetto = tetto0 if tetto0 is not None else mediana_attivi(v2)
            if migliore is not v and migliore.get("M") is not None:
                tetto = migliore["M"] if tetto is None else min(tetto, migliore["M"])
            if tetto is None or v2.get("M") is None or v2["M"] > tetto:
                traccia.append({"passo": nome, "n": n2, "M": v2.get("M"),
                                "tetto": int(round(tetto)) if tetto is not None else None,
                                "esito": "M più alto: non usato" if v2.get("M") is not None and tetto is not None
                                else "mercato non stimabile: non usato"})
                continue
            traccia.append({"passo": nome, "n": n2, "M": v2["M"], "tetto": int(round(tetto)), "esito": "usato"})
            migliore, lista_m, fonte_m = v2, m, nome
            if n2 >= serve:
                break
    except Exception as e:                       # la cascata non deve mai far perdere la valutazione di prima
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("CASCATA %s non riuscita: %s", t.get("id"), str(e)[:200])
        traccia.append({"errore": type(e).__name__})
        fonte_m = None
    if fonte_m is None:
        out = dict(v)
        out["cascata"] = {"passi": traccia, "usata": None, "serve": serve}
        return out, lista
    migliore = dict(migliore)
    n_v = sum(1 for c in (migliore.get("confronti") or []) if c.get("venduta"))
    migliore["fonte_confronti"] = fonte_m
    migliore["cascata"] = {"passi": traccia, "usata": fonte_m, "serve": serve, "n_prima": n_gate(v),
                           "venduta_nel_campione": n_v, "giorni_vendute": giorni}
    if fonte_m != "vendute" or v.get("fuori_zona"):
        migliore["fuori_zona"] = True
    testo = {"vendute": f"confronti entro la zona non sufficienti: aggiunte le auto uguali sparite dagli annunci negli "
                        f"ultimi {giorni} giorni (ultimo prezzo chiesto)",
             "nord+vendute": "confronti non sufficienti: aggiunti gli annunci uguali del resto del Nord Italia e le auto "
                             f"uguali sparite negli ultimi {giorni} giorni (prezzi chiesti)",
             "italia+vendute": "confronti non sufficienti: aggiunti gli annunci uguali di tutta Italia della base prezzi "
                               f"e le auto uguali sparite negli ultimi {giorni} giorni (prezzi chiesti)"}[fonte_m]
    migliore.setdefault("ipotesi", []).append(testo + "; mercato mai sopra la mediana dei confronti attivi trovati "
                                              "prima (prezzi corretti)")
    log.info("CASCATA %s %s: n %s→%s, M %s→%s, %s→%s", t.get("id"), fonte_m, n_gate(v), n_gate(migliore), M0,
             migliore.get("M"), v.get("categoria"), migliore.get("categoria"))
    return migliore, lista_m


def _valuta_zona(conn, t: dict, mercato: list, soglia: int, cfg, cache: dict):
    """Zona di casa, ripiego Nord (meno di n_preferito confronti) e tutta Italia per la fascia alta: invariato."""
    z = _regole(cfg).zona
    v = P.valuta(t, mercato, soglia, cfg)
    lista = mercato
    if not z.ripiego_nord or (v.get("n") or 0) >= cfg.n_preferito or not (t.get("make") and t.get("model") and t.get("fuel")):
        return v, lista
    key = (t["make"], t["model"], t["fuel"])
    if key not in cache:
        cache.clear()                                    # un gruppo alla volta: poca memoria
        cache[key] = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                      for m in conn.execute(MARKET_SQL.format(home=NORD_SQL), key).fetchall()]
    v2 = P.valuta(t, cache[key], soglia, cfg)
    if (v2.get("n") or 0) > (v.get("n") or 0):
        v2["fuori_zona"] = True
        v2.setdefault("ipotesi", []).append("pochi confronti entro 150 km: usati anche annunci uguali nel resto del "
                                            "Nord Italia (prezzi richiesti, possibili piccole differenze di zona)")
        v = v2
        lista = cache[key]
    # 21/10 (cliente): auto di fascia alta (da 50.000 €): pochi esemplari uguali per zona, si usa la base prezzi
    # di tutta Italia (privati e concessionari). Interruttore POLICY_ITALIA (regole.zona.italia).
    if not z.italia or (v.get("n") or 0) >= cfg.n_preferito or (t.get("price_eur") or 0) < z.italia_da:
        return v, lista
    chiave = ("IT",) + tuple(str(x).lower() for x in key)
    if chiave not in cache:
        cache.clear()
        cache[chiave] = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                         for m in conn.execute(MARKET_ITALIA_SQL, key).fetchall()]
    v3 = P.valuta(t, cache[chiave], soglia, cfg)
    if (v3.get("n") or 0) > (v.get("n") or 0):
        v3["fuori_zona"] = True
        v3.setdefault("ipotesi", []).append("auto di fascia alta con pochi confronti vicini: usati gli annunci uguali "
                                            "di tutta Italia della base prezzi (prezzi richiesti, privati e concessionari)")
        return v3, cache[chiave]
    return v, lista


def vr_di(conn, ids: list) -> dict:
    """Valore reale (tabella valore_reale) per questi annunci: l'ULTIMO disponibile, con la data (computed_at).
    4/11: non sparisce più dopo 2 giorni (se il calcolo notturno salta, M non cambia da solo); avviso se vecchio."""
    from .valutazione import ingressi
    return ingressi(conn, ids, solo=("vr",))["vr"] if ids else {}


def ai_di(conn, ids: list) -> dict:
    """Stime del modello AI dei prezzi (tabella stima_ai): l'ultima disponibile per annuncio, con la data."""
    from .valutazione import ingressi
    return ingressi(conn, ids, solo=("ai",))["ai"] if ids else {}


def carica_taratura(conn) -> None:
    """4/11: la taratura è tolta (correggeva M verso i prezzi CHIESTI degli annunci attivi, studio 1 §1.3).
    Resta il nome per i lavori che lo chiamano ancora: non fa niente."""
    return None


def targhe_visibili(conn) -> set:
    """Annunci in cui il controllo delle foto ha visto la targa (tabella controllo_targa)."""
    try:
        return {r["listing_id"] for r in conn.execute("SELECT listing_id FROM controllo_targa WHERE esito='visibile'").fetchall()}
    except Exception:
        conn.rollback()
        return set()


def targa_e_telefono(r: dict, targhe: set | None = None, tel=None) -> bool:
    """Prezzo molto basso ammesso: basta il numero del venditore (regole.antitruffa.basso_solo_telefono, cliente 24/10),
    mai per un commerciante. Stessa regola di valutazione.prepara (telefono unico: valutazione.telefono)."""
    from .valutazione import telefono
    rg = RG.regole()
    tel = telefono(r) if tel is None else tel
    if tel is not True:
        return False
    if (r.get("seller_type") or "privato") == "commerciante" and rg.antitruffa.eccezione_solo_privati:
        return False
    if rg.antitruffa.basso_solo_telefono:
        return True
    from ..core.normalize import find_plate
    return r.get("id") in (targhe or set()) or bool(find_plate(f"{r.get('title') or ''} {r.get('description') or ''}"))


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _rischio(r) -> bool:
    parts = {str((d or {}).get("part")) for d in (_j(r.get("damage_items")) or [])}
    return r.get("damage_class") == "alto_rischio" or bool(parts & RISCHIO)


def _riepilogo(es, extra: dict | None = None) -> dict:
    """Contatori per categoria (e sezione) e per numero di confronti, dalle valutazioni appena fatte."""
    cat, ns = Counter(), Counter()
    for lid, v in es.valutazioni.items():
        sez = (extra or {}).get(lid, {}).get("sezione") or v.get("sezione") or "affari"
        cat[f"{sez}:{v.get('categoria')}"] += 1
        n = v.get("n") or 0
        ns["≥10" if n >= 10 else "5-9" if n >= 5 else "<5"] += 1
    return {"categorie": dict(sorted(cat.items())), "confronti": dict(ns)}


def esegui(conn, limite: int | None = None) -> dict:
    """Rivaluta le auto già approfondite (sezioni affari e incidentate) e la sezione economica, con
    valutazione.valuta_e_scrivi (fonte 'cron'). Nessun limite casuale: tutte, ordinate per gruppo."""
    from ..web import scovo
    from .valutazione import valuta_e_scrivi
    t0 = time.time()
    conn.execute("SET statement_timeout = '60s'")
    affari = [r["id"] for r in conn.execute("SELECT q.id FROM (" + scovo.LIST_SQL + ") q", {"me": 0}).fetchall()]
    # 15/11 (punto 5): come le utilitarie, le approfondite valutate da poco, invariate e lontane SOTTO la soglia del sito
    # non si rifanno (quelle sul sito, vicine alla soglia o cambiate sì). Solo la sezione affari: la sezione
    # economica non ha soglia di margine. RIC_INVARIATE_ORE=0 = tutte come prima.
    tutte_affari = set(affari)
    n_affari = len(affari)
    affari, saltate = senza_invariate(conn, affari, "RIC_INVARIATE_ORE")
    eco = [r["id"] for r in conn.execute(
        "SELECT q.id FROM (" + scovo.ECO_SQL + " ORDER BY l.first_seen_at DESC LIMIT %(lim)s) q",
        dict(scovo.eco_params(0), lim=int(os.environ.get("ECONOMICHE_QUANTE", "2500")))).fetchall()]
    if saltate:                              # un'auto saltata come affare non si rifà come economica (cambierebbe sezione)
        via = set(affari)
        eco = [i for i in eco if i not in tutte_affari or i in via]
    extra = {i: {"sezione": "affari"} for i in affari}
    for i in eco:
        extra.setdefault(i, {"sezione": "economiche"})
    ids = list(extra)[:limite] if limite else list(extra)
    es = valuta_e_scrivi(conn, ids, "cron", extra=extra)
    sospette = 0
    for lid, v in es.valutazioni.items():
        if (v.get("categoria") in scovo.CATEGORIE_SITO and v.get("M") and v.get("acquisto")
                and v["M"] > 1.5 * v["acquisto"] and sospette < 12):
            sospette += 1        # controllo a vista: mercato molto sopra il prezzo (solo dati dell'auto, niente venditori)
            log.warning("POLICY_SOSPETTA %s", json.dumps({
                "id": lid, "prezzo": v.get("acquisto"), "M": v.get("M"), "M_fonte": v.get("M_fonte"),
                "metodo": v.get("metodo"), "R": v.get("R_finale"), "margine": v.get("margine"), "cat": v["categoria"],
                "confronti": [[c.get("prezzo"), c.get("prezzo_corretto"), c.get("anno"), c.get("km"), c.get("kw")]
                              for c in (v.get("confronti") or [])[:12]]}, ensure_ascii=False, default=str))
    stats = {"valutate": len(ids), "affari_lette": n_affari, "saltate_invariate": saltate,
             "secondi": round(time.time() - t0, 1), **_riepilogo(es, extra),
             "contatori": dict(es.stats), "impronta_regole": RG.impronta()}
    log.warning("POLICY_RIEPILOGO %s", json.dumps(stats, ensure_ascii=False))
    # 16/11 (fond-auto): giro di recupero delle auto rimaste con pochi confronti o in attesa del giudice, con la
    # cascata dei confronti; limitato (RECUPERO_PER_GIRO) e con il tetto del giudice del cron. Spento: RECUPERO_CONFRONTI=0
    try:
        from . import recupero
        if recupero.attivo():
            stats["recupero"] = recupero.recupera(conn)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("RECUPERO_CONFRONTI non riuscito: %s", str(e)[:200])
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from ..db import connect
    with connect() as conn:
        print(esegui(conn))


# ------------------------------------------------------------------ privati sani della zona (utilitarie)
UTILITARIE = {"panda", "punto", "grande-punto", "punto-evo", "500", "ypsilon", "fiesta", "ka", "ka+", "fusion", "polo", "up",
              "fox", "lupo", "yaris", "aygo", "iq", "clio", "twingo", "modus", "zoe", "208", "207", "206", "107", "108",
              "c1", "c2", "c3", "c3-picasso", "ds3", "corsa", "agila", "adam", "karl", "micra", "note", "i10", "i20",
              "getz", "atos", "picanto", "rio", "venga", "ibiza", "mii", "arona", "fabia", "citigo", "roomster", "swift",
              "splash", "alto", "celerio", "ignis", "jazz", "sandero", "logan", "spark", "matiz", "colt", "space-star",
              "mito", "a1", "fortwo", "forfour", "one", "cooper", "1-series", "serie-1", "mazda2", "2", "colt", "500l",
              "500x", "qubo", "fiorino", "idea", "musa", "delta", "stonic", "kalos", "aveo", "duster", "captur", "2008",
              "juke", "golf", "a-class", "classe-a", "a-klasse", "b-klasse", "classe-b", "auris", "corolla", "megane", "astra",
              "focus", "bravo", "308", "c4", "leon", "ceed", "i30", "civic"}

# Tutti i privati sani della zona, visti negli ultimi 14 giorni (la descrizione intera la legge valutazione.prepara)
UTIL_SQL = ("SELECT l.id FROM listings l "
            "WHERE l.status='attivo' AND l.seller_type='privato' AND l.price_eur BETWEEN %(lo)s AND %(hi)s "
            "AND l.year >= %(anno)s AND COALESCE(l.mileage_km, 0) <= %(km)s AND l.province = ANY(%(prov)s) "
            "AND l.last_seen_at > now() - interval '14 days' AND l.source IN ('subito','autoscout24','facebook') "
            "AND COALESCE(l.damage_class, 'sconosciuto') IN ('nessuno','sconosciuto') AND NOT COALESCE(l.problem_search, false) "
            # 17/11 (notte-09): motivo base (= scovo.motivo_stadio_sql): "truffa | scomparso" resta fuori
            "AND btrim(split_part(COALESCE(l.stage_reason, ''), '|', 1)) NOT IN ('doppione','truffa','prezzo_finto','dati_incoerenti') "
            "AND COALESCE(l.stage_reason, '') NOT LIKE 'analisi_manuale%%'")      # scartate a mano: mai ripubblicate

_UTIL_LOCK = __import__("threading").Lock()

# 15/11 (punto 5, cron lento): le utilitarie rivalutavano TUTTI i ~45.000 privati della zona a ogni ricalcolo (4 h 40 min
# il 14/11: 38.801 righe riscritte "dati_pari_o_piu", 36.713 non convenienti). Si salta un'auto valutata da meno di
# UTIL_INVARIATE_ORE ore (30 per le utilitarie, sotto le 36 di regole.scrittura; 20 per le approfondite) con le regole di oggi, senza cambi da allora (prezzo,
# testo/dati) e con il margine LONTANO sotto la soglia del sito (più di SCELTA_GAP_INVARIATE, 1.500 €): è la stessa
# condizione della scelta delle candidate (pipeline/scelta_candidate.condizione_invariate). Le auto vicine alla soglia,
# quelle sul sito, cambiate o senza margine si rivalutano sempre. 0 = spento (tutte, come prima).
UTIL_INVARIATE_ORE = 30
RIC_INVARIATE_ORE = 20


def senza_invariate(conn, ids: list, var: str = "UTIL_INVARIATE_ORE") -> tuple[list, int]:
    """(ids da valutare, quante saltate). `var`: variabile delle ore (UTIL_INVARIATE_ORE per le utilitarie, default
    30; RIC_INVARIATE_ORE per le approfondite di esegui, default 20). In caso di errore: tutte."""
    base = RIC_INVARIATE_ORE if var == "RIC_INVARIATE_ORE" else UTIL_INVARIATE_ORE
    try:
        ore = int(os.environ.get(var) or base)
    except ValueError:
        ore = base
    if ore <= 0 or not ids:
        return list(ids), 0
    try:
        from ..pipeline import scelta_candidate as SC
        par = dict(SC.parametri_invariate(), inv_ore=ore, ids=list(ids))
        via = {r["id"] for r in conn.execute("SELECT t.id FROM listings t WHERE t.id = ANY(%(ids)s) AND "
                                             + SC.condizione_invariate(conn), par).fetchall()}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("UTILITARIE invariate non lette (si valutano tutte): %s", str(e)[:200])
        return list(ids), 0
    return [i for i in ids if i not in via], len(via)


def utilitarie(conn, pubblica: bool = False) -> dict:
    """Un solo calcolo completo alla volta nello stesso processo (memoria: 512 MB su Render)."""
    if not _UTIL_LOCK.acquire(blocking=False):
        log.warning("UTILITARIE già in corso: salto")
        return {}
    try:
        return _utilitarie(conn, pubblica)
    finally:
        _UTIL_LOCK.release()


def _utilitarie(conn, pubblica: bool = False) -> dict:
    """Tutti i privati sani della zona da 500 a 50.000 €, valutati con valutazione.valuta_e_scrivi (fonte
    'cron-utilitarie': si scrivono TUTTE le valutazioni, anche escluse e non convenienti, così non resta una riga
    vecchia "candidato"). Con pubblica=True quelle sopra il margine minimo del cliente vanno sul sito
    (stage 'approfondito') e in top_auto (ordinate per margine/soglia)."""
    from ..core.dal_testo import completa_db
    from ..web import scovo
    from ..zone import MILANO
    from .valutazione import valuta_e_scrivi
    t0 = time.time()
    a = RG.regole().ammissione
    log.warning("DAL_TESTO %s", json.dumps(completa_db(conn)))          # anno, km, alimentazione mancanti: dal testo
    ids = [r["id"] for r in conn.execute(UTIL_SQL, {"lo": 500, "hi": 50000, "anno": a.anno_min, "km": a.km_max,
                                                    "prov": list(MILANO)}).fetchall()]
    lette = len(ids)
    ids, saltate = senza_invariate(conn, ids)
    es = valuta_e_scrivi(conn, ids, "cron-utilitarie")
    pass_ = [(lid, v) for lid, v in es.valutazioni.items()
             if es.salvata.get(lid) in scovo.CATEGORIE_SITO and v.get("categoria") in scovo.CATEGORIE_SITO
             and v.get("margine") is not None and v.get("acquisto")
             and v["margine"] >= scovo.soglia(int(v["acquisto"])) and es.telefono.get(lid) is not False]
    per_fascia = Counter(next(f"<{lim}" for lim in (5000, 10000, 15000, 20000, 30000, 10**9) if int(v["acquisto"]) < lim)
                         for _, v in pass_)
    out = {"lette": lette, "saltate_invariate": saltate, "valutate": len(es.valutazioni), "stats": dict(es.stats),
           "pubblicabili_per_fascia": dict(per_fascia), "secondi": round(time.time() - t0)}
    log.warning("UTILITARIE_SIM %s", json.dumps(out, ensure_ascii=False))
    if pubblica:
        n = 0
        conn.execute("DELETE FROM top_auto")
        ordine = sorted(pass_, key=lambda x: -x[1]["margine"] / scovo.soglia(int(x[1]["acquisto"])))
        for pos, (lid, v) in enumerate(ordine):
            conn.execute("INSERT INTO top_auto (listing_id, posizione, margine) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                         (lid, pos, v["margine"]))
        for lid, v in ordine:
            if not conn.execute("SELECT 1 FROM valuations WHERE listing_id=%s LIMIT 1", (lid,)).fetchone():
                conn.execute(
                    """INSERT INTO valuations (listing_id, engine_version, private_median, resale_median, resale_prudent,
                         n_comparables, comparables_used, confidence, parts_cost_low, parts_cost_high)
                       VALUES (%s,%s,%s,%s,%s,%s,'[]',%s,0,0)""",
                    (lid, P.POLICY_VERSION + "+utilitarie", v.get("M"), v.get("M"), v.get("R_finale"), v.get("n"),
                     "affidabile" if v["categoria"] == "opportunita" else "da_verificare"))
            conn.execute("UPDATE listings SET stage='approfondito', stage_reason=%s, deep_at=COALESCE(deep_at, now()), "
                         "last_checked_at=now() WHERE id=%s AND stage <> 'approfondito'",
                         ("opportunita" if v["categoria"] == "opportunita" else "da_verificare", lid))
            n += 1
        conn.commit()
        log.warning("UTILITARIE_PUBBLICATE %d", n)
    return out
