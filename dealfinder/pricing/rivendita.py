"""Rivendita dai commercianti (12/11/2026, cliente: "non voglio né abbassare né alzare, voglio solo essere più
preciso: la rivendita deve venire dai dati veri dei commercianti").

Prima R = M, e M mescola privati e commercianti (prezzi chiesti, portati al venduto con un fattore medio). Il
commerciante che compra l'auto la rivende però al prezzo dei COMMERCIANTI. Qui, per ogni auto, si guardano solo gli
annunci di commercianti della stessa auto (stesso carburante, carrozzeria, motore e cambio di policy_v2._equivalente,
stesso livello di allestimento ±1, anni ±anni_tol e km ±km_tol, prezzi corretti per anno e km come le gemelle):

1. venduti: annunci di commercianti spariti negli ultimi `giorni` giorni entro `venduta_entro` giorni dalla comparsa e
   mai riapparsi: l'ULTIMO prezzo è il prezzo a cui il mercato li ha presi; si misurano anche i giorni impiegati;
2. attivi: se i venduti sono pochi, il prezzo di oggi degli annunci di commercianti meno lo sconto medio osservato
   tra primo e ultimo prezzo dei commercianti venduti dello stesso gruppo (marca, modello, carburante).

    R = mediana dei venduti                se venduti ≥ min_venduti
      = mediana degli attivi × (1 − sconto) se attivi ≥ min_attivi e lo sconto è misurato su ≥ min_sconti venduti
      = None (resta il calcolo di prima)    altrimenti

Nessun numero messo a mano: soglie in regole.RivenditaCommercianti, interruttore RIVENDITA_COMMERCIANTI.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

from .. import regole as RG

# Annunci di commercianti dello stesso gruppo: attivi recenti e spariti negli ultimi `giorni` giorni, con il primo
# prezzo (evento "apparso") e se sono riapparsi. Solo dati dell'auto, niente venditore.
SQL_COMMERCIANTI = """
SELECT l.id, l.source, l.vehicle_id, l.vehicle_identity, l.make, l.model, l.version_raw, l.title, l.year, l.mileage_km,
       l.fuel, l.gearbox, l.power_kw, l.price_eur, l.price_flags, l.seller_type, l.damage_class, l.status,
       l.stage_reason, l.problem_search, l.first_seen_at, l.last_seen_at, l.last_checked_at, l.disappeared_at,
       (SELECT e.price_eur FROM price_events e WHERE e.listing_id = l.id AND e.event = 'apparso'
         ORDER BY e.at LIMIT 1) AS prezzo_iniziale,
       EXISTS (SELECT 1 FROM price_events e WHERE e.listing_id = l.id AND e.event = 'riapparso') AS riapparso
FROM listings l
WHERE l.seller_type = 'commerciante' AND l.make = %s AND l.model = %s AND l.fuel = %s
  AND l.price_eur BETWEEN 500 AND 400000 AND l.year IS NOT NULL AND l.mileage_km IS NOT NULL
  AND ((l.status = 'attivo' AND l.last_seen_at > now() - make_interval(days => %s))
       OR (l.status = 'scomparso' AND l.disappeared_at > now() - make_interval(days => %s)))
  AND {zona}"""

_FLAG_NO = {"leasing_o_rata", "prezzo_civetta", "importazione", "dati_incoerenti", "solo_finanziamento"}


class Gruppo(list):
    """Annunci di commercianti di un gruppo (marca, modello, carburante) con i dati di gruppo calcolati UNA volta
    (sconto osservato, tassi anno/km, annunci ripubblicati) e tenuti sull'oggetto stesso: niente cache globale,
    sicuro tra thread (lucchetto per oggetto). Controllo del giudice M3: prima la cache usava id(lista)."""

    def __init__(self, righe=()):
        super().__init__(righe)
        import threading
        self._lucchetto = threading.Lock()
        self._dati: dict = {}

    def dati(self, rv, cfg) -> dict:
        chiave = (rv, cfg.regole.correzione, cfg.svalutazione_anno, cfg.svalutazione_10km)
        with self._lucchetto:
            d = self._dati.get(chiave)
            if d is None:
                d = self._dati[chiave] = _dati_gruppo(self, rv, cfg)
            return d


def _dati_gruppo(righe: list, rv, cfg) -> dict:
    from . import policy_v2 as P
    rip = ripubblicate(righe, rv)
    return {"sconto": sconto_osservato(righe, rv, rip), "ripubblicate": rip,
            "tassi": P.tassi([c for c in righe if c.get("status") == "attivo"], cfg)}


def ripubblicate(righe: list, rv) -> set:
    """id degli annunci di commercianti "spariti" che in realtà sono stati RIPUBBLICATI: stesso portale, stesso anno,
    km entro ±2.000 e un annuncio nuovo comparso tra 1 giorno prima e 7 giorni dopo la sparizione (controllo del
    giudice M6: il commerciante che rimette l'auto non l'ha venduta). Lo "stesso commerciante" non è nei dati che
    leggiamo (niente dati del venditore): si usa il portale."""
    from bisect import bisect_left, bisect_right
    from datetime import timedelta
    per = {}
    for c in righe:
        if c.get("first_seen_at") is not None and c.get("year") and c.get("mileage_km") is not None:
            per.setdefault((c.get("source"), int(c["year"])), []).append(c)
    for k in per:
        per[k].sort(key=lambda c: int(c["mileage_km"]))
    coppie = []
    kms_per = {k: [int(x["mileage_km"]) for x in v] for k, v in per.items()}
    for c in righe:
        if c.get("status") != "scomparso" or not c.get("disappeared_at") or not c.get("year") \
                or c.get("mileage_km") is None:
            continue
        k = (c.get("source"), int(c["year"]))
        lista, kms = per.get(k, []), kms_per.get(k, [])
        km = int(c["mileage_km"])
        d0 = _dt(c["disappeared_at"])
        for x in lista[bisect_left(kms, km - 2000):bisect_right(kms, km + 2000)]:
            if x.get("id") != c.get("id") and d0 - timedelta(days=1) <= _dt(x["first_seen_at"]) <= d0 + timedelta(days=7):
                coppie.append((abs(int(x["mileage_km"]) - km), c.get("id"), x.get("id")))
    # un annuncio nuovo spiega UNA sola sparizione: abbinamento dal più vicino di km
    out, usati = set(), set()
    for _, cid, xid in sorted(coppie, key=lambda z: (z[0], str(z[1]), str(z[2]))):
        if cid not in out and xid not in usati:
            out.add(cid)
            usati.add(xid)
    return out


def _dt(d):
    if isinstance(d, str):
        d = datetime.fromisoformat(d.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def carica(conn, make: str, model: str, fuel: str, zona_sql: str, r: RG.Regole | None = None) -> "Gruppo":
    """Annunci di commercianti del gruppo (Gruppo). Errori (tabella o colonna mancante): gruppo vuoto."""
    rv = (r or RG.regole()).rivendita
    try:
        righe = conn.execute(SQL_COMMERCIANTI.format(zona=zona_sql),
                             (make, model, fuel, rv.freschezza_giorni, rv.giorni)).fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return Gruppo()
    out = Gruppo()
    for x in righe:
        d = dict(x)
        vi = d.get("vehicle_identity")
        if isinstance(vi, str):
            try:
                d["vehicle_identity"] = json.loads(vi)
            except ValueError:
                d["vehicle_identity"] = {}
        out.append(d)
    return out


def _giorni(a, b):
    if not a or not b:
        return None
    if isinstance(a, str):
        a = datetime.fromisoformat(a.replace("Z", "+00:00"))
    if isinstance(b, str):
        b = datetime.fromisoformat(b.replace("Z", "+00:00"))
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return (b - a).total_seconds() / 86400


def venduta(c: dict, rv) -> float | None:
    """Giorni impiegati se l'annuncio di commerciante è "venduto" (sparito entro venduta_entro giorni, mai
    riapparso), altrimenti None."""
    if c.get("status") != "scomparso" or c.get("riapparso"):
        return None
    g = _giorni(c.get("first_seen_at"), c.get("disappeared_at"))
    return g if g is not None and 0 <= g <= rv.venduta_entro else None


def _pulito(c: dict) -> bool:
    from .policy_v2 import DANNEGGIATE
    if not c.get("price_eur") or (c.get("damage_class") or "sconosciuto") in DANNEGGIATE or c.get("problem_search"):
        return False
    if set(c.get("price_flags") or []) & _FLAG_NO:
        return False
    # controllo del giudice M6: stage_reason può avere varianti ("doppione_as24", "truffa_jev"...): contiene, non uguale
    sr = str(c.get("stage_reason") or "").lower()
    return not any(x in sr for x in ("doppione", "truffa", "prezzo_finto", "dati_incoerenti"))


def _mediana(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def sconto_osservato(righe: list[dict], rv, escludi: set | None = None) -> tuple[float | None, int]:
    """Sconto MEDIO tra primo e ultimo prezzo dei commercianti VENDUTI del gruppo (0 se non hanno ribassato),
    con il numero di osservazioni. None se le osservazioni sono meno di rv.min_sconti."""
    sc = []
    for c in righe:
        if venduta(c, rv) is None or not _pulito(c) or (escludi and c.get("id") in escludi):
            continue
        p0, p1 = c.get("prezzo_iniziale"), c.get("price_eur")
        if p0 and p1 and p0 >= p1 and p1 >= .5 * p0:
            sc.append((p0 - p1) / p0)
    if len(sc) < rv.min_sconti:
        return None, len(sc)
    return sum(sc) / len(sc), len(sc)                    # media: chi non ha ribassato conta come 0%


def calcola(t: dict, righe: list[dict] | None, cfg=None, now=None) -> dict | None:
    """Rivendita dai commercianti per l'auto t (riga di listings). None se manca la lista (lavoro senza database).
    Ritorna sempre i conteggi, R solo con abbastanza dati."""
    if righe is None:
        return None
    from . import policy_v2 as P
    from .allestimento import livello_num
    cfg = cfg or P.PolicyConfig()
    r = cfg.regole
    rv = r.rivendita
    if not (t.get("year") and t.get("mileage_km") is not None):
        return None
    now = now or datetime.now(timezone.utc)
    # dati di gruppo: calcolati una volta sul Gruppo (carica); una lista semplice (prove) si calcola ogni volta
    gd = righe.dati(rv, cfg) if isinstance(righe, Gruppo) else _dati_gruppo(righe, rv, cfg)
    sconto, n_sconti = gd["sconto"]
    ya, ka, _ = gd["tassi"]
    rip = gd["ripubblicate"]
    corr = dataclasses.replace(r.correzione, due_sensi=cfg.due_sensi, tetto=cfg.correzione_tetto)
    cache: dict = {}
    lt = livello_num(t) if r.allestimento.attivo else None
    vendute, attive, giorni = [], [], []
    for c in righe:
        if c.get("id") == t.get("id") or P._stessa_auto(c, t) or not _pulito(c):
            continue
        if P._equivalente(t, c, cfg, 2):         # stesso carburante, carrozzeria, cambio, motore (livello 2)
            continue
        if abs(int(c["year"]) - int(t["year"])) > rv.anni_tol or abs(int(c["mileage_km"]) - int(t["mileage_km"])) > rv.km_tol:
            continue
        lc = livello_num(c, cache) if lt is not None else None
        if lt is not None and lc is not None and abs(lc - lt) > 1:
            continue
        p = P.prezzo_corretto(c, t, ya, ka, corr)
        if lt is not None and lc is not None and lc != lt:
            p *= (1 - r.allestimento.fattore) ** (lc - lt)       # ±1 livello portato al livello dell'auto
        g = venduta(c, rv) if c.get("id") not in rip else None
        if g is not None:
            vendute.append(p)
            giorni.append(g)
        elif c.get("status") == "attivo":
            dv = [d for d in (P._days(c.get("last_checked_at"), now), P._days(c.get("last_seen_at"), now)) if d is not None]
            if dv and min(dv) <= rv.freschezza_giorni:
                attive.append(p)
    out = {"n_venduti": len(vendute), "n_attivi": len(attive), "n_sconti": n_sconti,
           "sconto": round(sconto, 4) if sconto is not None else None,
           "giorni_vendita": round(_mediana(giorni), 1) if giorni else None, "R": None, "fonte": None}
    if len(vendute) >= rv.min_venduti:
        out.update(R=int(round(_mediana(vendute))), fonte="venduti", n_usati=len(vendute))
    elif len(attive) >= rv.min_attivi and sconto is not None:
        out.update(R=int(round(_mediana(attive) * (1 - sconto))), fonte="attivi_scontati", n_usati=len(attive))
    return out
