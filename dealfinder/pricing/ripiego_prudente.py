"""Stima di ripiego PRUDENTE per le auto "dati insufficienti" (13/11, ramo piu-auto).

Oggi ~136 auto dell'elenco del sito escono al primo passo con la valutazione "dati_insufficienti". Il calcolo ha già
provato i suoi ripieghi (confronti allargati per livello, Nord, Italia, valore reale con le sue soglie): qui NON si
abbassano quelle soglie. Si prova solo a dare una stima quando DUE fonti indipendenti dicono la stessa cosa, e si
prende la più bassa:

    V  valore reale del gruppo (pricing/valore_reale.py, tabella valore_reale): prezzo di VENDITA stimato, con le
       STESSE soglie della regola "valore reale senza gemelle" (regole.ValoreReale.senza_gemelle_sigma_max 15%,
       senza_gemelle_vendute_min 20 auto vendute);
    A  Autohero (tabella autohero_prezzi, stesso abbinamento della prova alla cieca: stessa marca-modello canonica,
       anno ±1, km ±20.000, almeno 2 auto): mediana delle più vicine × 0,92 (prezzo esposto di un rivenditore →
       prezzo di vendita, fattore di calibrazione della fascia 5-10k, la più severa sopra i 5.000 €);
    G  le poche auto uguali trovate (1 sola, NON quando i prezzi erano troppo dispersi): prezzo corretto × 0,92.

Regole (tutte devono valere, altrimenti l'auto resta fuori come oggi):
  - la valutazione è "dati_insufficienti" SOLO per pochi confronti o confronti dispersi (mai per danni, dati mancanti,
    prezzo sotto il 55%/20% del mercato: quelle restano fuori per le regole anti-truffa);
  - auto senza danni dichiarati, con anno, km, alimentazione e prezzo;
  - almeno 2 fonti, la più alta al massimo il 15% sopra la più bassa (fonti che non concordano = stima incerta);
  - stima = la fonte più bassa × 0,97: mai sopra nessuna fonte;
  - prezzo chiesto ≥ 55% della stima (stessa soglia anti-truffa della valutazione).
Sul sito (web/sito_regole.r_valutata, interruttore RIPIEGO_PRUDENTE) l'auto entra come "candidato", con l'avviso
"Verificare prima di pagare" e solo dopo il controllo AI (serve_controllo_ai); margine minimo SOGLIE_PV invariato.
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics

log = logging.getLogger("ripiego_prudente")

FATTORE_VENDITA = 0.92          # prezzo esposto → prezzo di vendita (prudente)
PRUDENZA = 0.97                 # stima = fonte più bassa × 0,97
ACCORDO_MAX = 1.15              # la fonte più alta al massimo +15% sulla più bassa
AH_MIN = 2
SOTTO_M = 0.55                  # come antitruffa.sotto_m della valutazione

_MOTIVI_OK = re.compile(r"(?i)^solo \d+ confronti validi|^confronti troppo dispersi")


def ammessa(auto: dict, dati: dict) -> str | None:
    """None se l'auto può avere una stima di ripiego, altrimenti il motivo per cui no."""
    if (auto.get("damage_class") or "sconosciuto") not in ("nessuno", "sconosciuto"):
        return "danno"
    if auto.get("damage_declared"):
        return "danno"
    for k in ("year", "mileage_km", "fuel", "price_eur"):
        if auto.get(k) in (None, ""):
            return "dati_mancanti"
    motivi = [str(m) for m in (dati.get("motivi") or [])]
    if any("non affidabile" in m for m in motivi):
        return "prezzo_non_affidabile"          # rata, civetta o importazione
    if not motivi or not all(_MOTIVI_OK.search(m) for m in motivi):
        return "altro_motivo"                   # danni, dati mancanti, prezzo troppo basso: restano fuori
    return None


def fonti(dati: dict, vr: dict | None, autohero: list[int] | None) -> dict:
    out = {}
    # 13/11 (giudice A4): le stesse soglie della regola del valore reale senza gemelle (regole.ValoreReale)
    from .. import regole as RG
    vrr = RG.regole().valore_reale
    if (vr and vr.get("v") and float(vr.get("sigma") or 1) <= vrr.senza_gemelle_sigma_max
            and int(vr.get("n_vendute") or 0) >= vrr.senza_gemelle_vendute_min):
        out["valore_reale"] = int(vr["v"])
    ah = [int(p) for p in (autohero or []) if p]
    if len(ah) >= AH_MIN:
        out["autohero"] = int(round(statistics.median(ah) * FATTORE_VENDITA))
    if not dati.get("dispersione"):
        conf = [c.get("prezzo_corretto") or c.get("prezzo") for c in (dati.get("confronti") or []) if isinstance(c, dict)]
        conf = [float(p) for p in conf if p]
        if len(conf) == 1 or (int(dati.get("n_gemelle") or dati.get("n") or 0) == 1 and conf):
            out["gemella"] = int(round(conf[0] * FATTORE_VENDITA))
    return out


def stima(prezzo: int, dati: dict, vr: dict | None, autohero: list[int] | None) -> tuple[int | None, dict, str]:
    """(stima, fonti, motivo). stima None = resta fuori (motivo)."""
    f = fonti(dati, vr, autohero)
    if len(f) < 2:
        return None, f, "fonti_insufficienti"
    lo, hi = min(f.values()), max(f.values())
    if lo <= 0 or hi > ACCORDO_MAX * lo:
        return None, f, "fonti_discordi"
    est = int(round(lo * PRUDENZA))
    if prezzo < SOTTO_M * est:
        return None, f, "prezzo_troppo_basso"
    return est, f, ""


def riassunto_pv(auto: dict, est: int, f: dict) -> dict:
    """I campi della valutazione che il sito usa (stesso formato di policy_v2.riassunto)."""
    from . import policy_v2 as P
    cfg = P.PolicyConfig.from_env()
    cs = P.costi(dict(auto, parts_cost_low=None, parts_cost_high=None), est, cfg)
    costi = int(cs["totale_costi"])
    return {"M": est, "L": est, "R": est, "R_finale": est, "margine": int(est - int(auto["price_eur"]) - costi),
            "totale_costi": costi, "n": len(f), "metodo": "ripiego_prudente", "M_fonte": "vendite",
            "R_da": "stima di ripiego (fonte più bassa)", "calibrato": True, "pochi_confronti": True,
            "fonti_ripiego": f}


# ------------------------------------------------------------------ database
def _tabella(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS ripiego_prudente (
        listing_id BIGINT PRIMARY KEY, prezzo INTEGER NOT NULL, valutazione_at TIMESTAMPTZ NOT NULL,
        stima INTEGER, pv JSONB, motivo TEXT, computed_at TIMESTAMPTZ NOT NULL DEFAULT now())""")


AUTO_SQL = """
SELECT l.id, l.make, l.model, l.year, l.mileage_km, l.fuel, l.price_eur, l.damage_class, l.damage_declared,
       vp.dati, vp.computed_at AS vp_at, vr.v, vr.sigma, vr.n, vr.n_vendute
FROM listings l
JOIN LATERAL (SELECT dati, categoria, computed_at FROM valutazioni_policy v WHERE v.listing_id = l.id
              ORDER BY v.computed_at DESC LIMIT 1) vp ON vp.categoria = 'dati_insufficienti'
LEFT JOIN valore_reale vr ON vr.listing_id = l.id
LEFT JOIN ripiego_prudente rp ON rp.listing_id = l.id AND rp.prezzo = l.price_eur AND rp.valutazione_at >= vp.computed_at
WHERE l.stage = 'approfondito' AND l.status = 'attivo' AND l.price_eur IS NOT NULL
  AND l.stage_reason IN ('opportunita', 'da_verificare', 'incidentata') AND rp.listing_id IS NULL
  AND GREATEST(l.last_checked_at, l.last_seen_at) > now() - interval '5 days'
LIMIT %(lim)s"""


def giro(conn, limite: int | None = None) -> dict:
    """Calcola (una volta per auto, prezzo e valutazione) la stima di ripiego delle auto dell'elenco "dati
    insufficienti". Scrive solo la tabella ripiego_prudente; la usa il sito (leggi_per_sito)."""
    from collections import Counter
    if os.environ.get("RIPIEGO_PRUDENTE", "1") != "1":
        return {"spento": True}
    st = Counter()
    _tabella(conn)
    conn.commit()
    sql = AUTO_SQL
    if not conn.execute("SELECT to_regclass('valore_reale') AS t").fetchone()["t"]:
        sql = sql.replace("LEFT JOIN valore_reale vr ON vr.listing_id = l.id",
                          "LEFT JOIN (SELECT NULL::bigint AS listing_id, NULL::int AS v, NULL::real AS sigma, "
                          "NULL::int AS n, NULL::int AS n_vendute) vr ON false")
    righe = conn.execute(sql, {"lim": int(limite or os.environ.get("RIPIEGO_PER_GIRO", "200"))}).fetchall()
    ah_ok = bool(conn.execute("SELECT to_regclass('autohero_prezzi') AS t").fetchone()["t"])
    for r in righe:
        r = dict(r)
        dati = r.get("dati") or {}
        if isinstance(dati, str):
            dati = json.loads(dati)
        motivo, est, pv = ammessa(r, dati), None, None
        if motivo is None:
            ah = _autohero(conn, r) if ah_ok else []
            vr = {"v": r.get("v"), "sigma": r.get("sigma"), "n": r.get("n"), "n_vendute": r.get("n_vendute")} if r.get("v") else None
            est, f, motivo = stima(int(r["price_eur"]), dati, vr, ah)
            if est:
                pv = riassunto_pv(r, est, f)
                motivo = "stima"
        st[motivo] += 1
        conn.execute("INSERT INTO ripiego_prudente (listing_id, prezzo, valutazione_at, stima, pv, motivo) "
                     "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (listing_id) DO UPDATE SET prezzo=EXCLUDED.prezzo, "
                     "valutazione_at=EXCLUDED.valutazione_at, stima=EXCLUDED.stima, pv=EXCLUDED.pv, "
                     "motivo=EXCLUDED.motivo, computed_at=now()",
                     (r["id"], r["price_eur"], r["vp_at"], est, json.dumps(pv) if pv else None, motivo))
    conn.commit()
    res = {"auto": len(righe), **dict(st)}
    if righe:
        log.warning("RIPIEGO_PRUDENTE %s", json.dumps(res, ensure_ascii=False))
    return res


def _autohero(conn, r: dict) -> list[int]:
    try:
        from ..ai import prova_cieca as PC
        a, k = int(r["year"]), int(r["mileage_km"])
        prefissi = [m + "-%" for m in PC.marche_slug(r.get("make"))]
        if not prefissi:
            return []
        righe = conn.execute(PC.AUTOHERO_SQL, {"prefissi": prefissi, "a1": a - 1, "a2": a + 1,
                                               "k1": k - 20000, "k2": k + 20000}).fetchall()
        righe = [dict(x) for x in righe if PC.stesso_modello(x.get("slug"), r.get("make"), r.get("model"))]
        righe.sort(key=lambda x: (abs(int(x["anno"]) - a), abs(int(x["km"]) - k)))
        return [int(x["prezzo"]) for x in righe[:5]]
    except Exception:
        conn.rollback()
        return []


def leggi_per_sito(conn, ids: list[int]) -> dict:
    """{listing_id: pv di ripiego} al prezzo attuale e per l'ultima valutazione."""
    if not ids or not conn.execute("SELECT to_regclass('ripiego_prudente') AS t").fetchone()["t"]:
        return {}
    out = {}
    for k in range(0, len(ids), 2000):
        for r in conn.execute(
                "SELECT rp.listing_id, rp.pv FROM ripiego_prudente rp JOIN listings l ON l.id = rp.listing_id "
                "AND rp.prezzo = l.price_eur "
                # 13/11 (giudice M5): solo se nessuna valutazione è più nuova di quella da cui è partita la stima
                "JOIN LATERAL (SELECT max(computed_at) AS at FROM valutazioni_policy v WHERE v.listing_id = l.id) vp "
                "ON vp.at <= rp.valutazione_at "
                "WHERE rp.listing_id = ANY(%s) AND rp.stima IS NOT NULL", (ids[k:k + 2000],)).fetchall():
            pv = r["pv"] if isinstance(r["pv"], dict) else json.loads(r["pv"] or "null")
            if pv:
                out[r["listing_id"]] = pv
    return out
