"""Prova alla cieca (12/11, cliente: "ChatGPT valuta meglio di noi"): chi indovina meglio il prezzo?

Si prendono N auto realmente sparite (vendute: status 'scomparso', con prova) negli ultimi `giorni` giorni, col loro
ultimo prezzo. Il prezzo si nasconde e si confrontano tre stime:
  - M del nostro calcolo: l'ultima valutazione salvata quando l'auto era ancora online (non usa il suo prezzo);
  - stima media della perizia AI (ai/perizia.py), con prezzo "NON INDICATO" e i confronti di allora (solo quelli
    già online in quel momento, con lo stato che avevano allora);
  - prezzo Autohero, se c'è nella tabella autohero_prezzi un'auto corrispondente (stessa marca-modello con i nomi
    canonici di core/normalize su entrambi i lati, anno ±1, km ±20.000): mediana delle 5 più vicine.
Per ciascuna: errore medio assoluto (€), errore percentuale medio, quante volte entro il 10%, scostamento medio
(+ = stima troppo alta). Solo righe PROVA_CIECA nei log (niente dati del venditore); nessuna scrittura nel database
(i token della perizia non si registrano in ai_usage: la prova non consuma il budget del sito, si scrive il costo
nel riepilogo). Si lancia una volta con BREVO_IMPORT_LISTE=prova_cieca:30 o da scripts/prova_cieca.py.

12/11 sera (margini fase 2 punto 8): nel riepilogo anche l'errore di M per fascia di M e di prezzo, e "prima/dopo":
M rifatto oggi (stesso mercato attivo, senza l'auto, senza AI) con le regole di prima (interruttori nuovi spenti),
quelle in vigore e "tutte" (anche le novità spente di default), portato al venduto con gli stessi fattori; più
l'errore per "sconto apparente" (prezzo / M di allora) per misurare l'effetto selezione. Con
BREVO_IMPORT_LISTE=prova_cieca:N:giorni:solo_calcolo niente perizia AI (costo zero).
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import time
from datetime import datetime, timezone

from . import client as AI
from . import perizia as PER

log = logging.getLogger("prova_cieca")

AUTO_SQL = """
WITH c AS (
  SELECT l.id FROM listings l
  WHERE l.status = 'scomparso' AND l.disappeared_at > now() - make_interval(days => %(giorni)s)
    AND l.price_eur BETWEEN 1000 AND 200000 AND l.year >= 2007 AND l.mileage_km BETWEEN 0 AND 150000
    AND COALESCE(l.damage_class, 'sconosciuto') IN ('nessuno', 'sconosciuto')
    AND EXISTS (SELECT 1 FROM valutazioni_policy v WHERE v.listing_id = l.id)
  ORDER BY md5(l.id::text) LIMIT %(pool)s)
SELECT l.id, l.title, l.description, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox,
       l.power_kw, l.price_eur, l.seller_type, l.disappeared_at, vp.m AS m_calcolo, vp.confronti
FROM c JOIN listings l ON l.id = c.id
JOIN LATERAL (SELECT (v.dati->>'M')::numeric AS m, v.dati->'confronti' AS confronti FROM valutazioni_policy v
              WHERE v.listing_id = l.id AND v.computed_at <= l.disappeared_at AND v.dati->>'M' IS NOT NULL
              ORDER BY v.computed_at DESC LIMIT 1) vp ON true
ORDER BY md5(l.id::text) LIMIT %(n)s"""

AUTOHERO_SQL = """
SELECT slug, prezzo, anno, km FROM autohero_prezzi
WHERE lower(slug) LIKE ANY(%(prefissi)s) AND prezzo IS NOT NULL
  AND anno BETWEEN %(a1)s AND %(a2)s AND km BETWEEN %(k1)s AND %(k2)s"""


def _canon(make, model) -> tuple[str | None, str | None]:
    """Marca e modello canonici, con le stesse regole dei confronti (core/normalize: "a-180" = "classe-a",
    "golf-variant" = "golf", "a-6" = "a6")."""
    from ..core import normalize as N
    mk = N.canon_make(N.slug(make)) if make else None
    return mk, (N.canon_model(mk, N.slug(model)) if mk and model else None)


def marche_slug(make) -> list[str]:
    """Come può iniziare lo slug Autohero (marca-modello) per questa marca: forma canonica e nomi brevi."""
    from ..core import normalize as N
    mk, _ = _canon(make, None)
    if not mk:
        return []
    out = {mk} | {k for k, v in N.MAKE_CANON.items() if v == mk}
    return sorted(out, key=len, reverse=True)


def stesso_modello(slug_ah: str, make, model) -> bool:
    """Lo slug Autohero è la stessa marca-modello dell'auto? Si toglie la marca dallo slug e si confrontano i modelli
    canonici da entrambe le parti (niente prefissi: "golf-sportsvan" o "golf-plus" non sono una "golf")."""
    mk, md = _canon(make, model)
    if not mk or not md:
        return False
    s = (slug_ah or "").lower().strip("-")
    for m in marche_slug(make):
        if s.startswith(m + "-"):
            resto = s[len(m) + 1:]
            md_ah = _canon(mk, resto)[1] or resto
            if md_ah == md:
                return True
    return False


def autohero_vicine(righe: list[dict], anno: int, km: int, quante: int = 5) -> int | None:
    """Mediana dei prezzi Autohero delle `quante` auto più vicine (anno ±1, km ±20.000 già filtrati)."""
    ok = [r for r in righe if r.get("prezzo") and r.get("anno") is not None and r.get("km") is not None
          and abs(int(r["anno"]) - int(anno)) <= 1 and abs(int(r["km"]) - int(km)) <= 20000]
    if not ok:
        return None
    ok.sort(key=lambda r: (abs(int(r["anno"]) - int(anno)), abs(int(r["km"]) - int(km))))
    return int(statistics.median(int(r["prezzo"]) for r in ok[:quante]))


def prezzo_autohero(conn, auto: dict) -> int | None:
    if not auto.get("year") or auto.get("mileage_km") is None:
        return None
    a, k = int(auto["year"]), int(auto["mileage_km"])
    prefissi = [m + "-%" for m in marche_slug(auto.get("make"))]
    if not prefissi:
        return None
    righe = conn.execute(AUTOHERO_SQL, {"prefissi": prefissi, "a1": a - 1, "a2": a + 1,
                                        "k1": k - 20000, "k2": k + 20000}).fetchall()
    righe = [dict(r) for r in righe if stesso_modello(r.get("slug"), auto.get("make"), auto.get("model"))]
    return autohero_vicine(righe, a, k)


def confronti_al_momento(conn, confronti: list[dict], momento) -> list[dict]:
    """Stato dei confronti com'era quando l'auto è sparita: quelli comparsi dopo si tolgono, quelli spariti dopo
    erano ancora attivi (niente informazioni dal futuro)."""
    ids = [int(c["id"]) for c in confronti if c.get("id") is not None]
    if not ids:
        return []
    righe = {r["id"]: r for r in conn.execute(
        "SELECT id, status, price_eur, first_seen_at, disappeared_at, seller_type FROM listings WHERE id = ANY(%s)",
        (ids,)).fetchall()}
    out = []
    for c in confronti:
        r = righe.get(c.get("id"))
        if not r:
            continue
        if r.get("first_seen_at") and momento and r["first_seen_at"] > momento:
            continue
        c = dict(c)
        sparito = r.get("status") == "scomparso" and r.get("disappeared_at") and r["disappeared_at"] <= momento
        c["stato"] = "venduto" if sparito else "attivo"
        c["giorni"] = PER._giorni(r.get("first_seen_at"), r["disappeared_at"] if sparito else momento)
        # il prezzo resta quello salvato nella valutazione di allora (il prezzo di oggi verrebbe dal futuro)
        c["venditore"] = c.get("venditore") or r.get("seller_type")
        out.append(c)
    return out


def metriche(coppie: list[tuple]) -> dict:
    """coppie (stima, vero): errore medio assoluto, errore % medio, quante entro il 10%, scostamento medio %."""
    ok = [(float(s), float(v)) for s, v in coppie if s and v]
    if not ok:
        return {"n": 0}
    err = [abs(s - v) for s, v in ok]
    pct = [abs(s - v) / v for s, v in ok]
    entro = sum(1 for p in pct if p <= 0.10)
    return {"n": len(ok), "errore_medio_eur": int(round(sum(err) / len(ok))),
            "errore_medio_pct": round(100 * sum(pct) / len(ok), 1),
            "errore_mediano_pct": round(100 * statistics.median(pct), 1),
            "entro_10pct": entro, "entro_10pct_quota": round(100 * entro / len(ok), 1),
            "scostamento_medio_pct": round(100 * sum((s - v) / v for s, v in ok) / len(ok), 1)}


FASCE = ((5000, "<5k"), (10000, "5-10k"), (20000, "10-20k"), (35000, "20-35k"), (60000, "35-60k"), (10**9, ">60k"))
SCONTO = ((.75, "prezzo < 75% di M"), (.90, "75–90% di M"), (10**9, "≥ 90% di M"))
VARIANTI = ("prima", "dopo", "tutte")


def fascia(x) -> str | None:
    return next((n for lim, n in FASCE if float(x) < lim), None) if x else None


def per_gruppo(righe: list[dict], chiave, stime: tuple, ordine: tuple) -> dict:
    """Metriche di ogni stima per gruppo (fascia di M, di prezzo, sconto apparente), SULLE STESSE AUTO in ogni gruppo
    (solo le auto che hanno tutte le stime: il confronto prima/dopo è alla pari)."""
    out = {}
    for nome in ordine:
        xs = [r for r in righe if chiave(r) == nome and all(r.get(s) for s in stime)]
        if xs:
            out[nome] = {s: metriche([(r[s], r["vero"]) for r in xs]) for s in stime}
    return out


def riepilogo(righe: list[dict]) -> dict:
    out = {"auto": len(righe)}
    for nome in ("m_calcolo", "perizia", "autohero"):
        out[nome] = metriche([(r.get(nome), r["vero"]) for r in righe])
    # 12/11 sera (margini fase 2 punto 8): errore di M per fascia, e prima/dopo i cambi sulle stesse auto e con lo
    # stesso mercato di oggi (M al venduto: dopo la calibrazione del sito)
    ordine_f = tuple(n for _, n in FASCE)
    out["per_fascia_M"] = per_gruppo(righe, lambda r: fascia(r.get("m_calcolo")), ("m_calcolo",), ordine_f)
    out["per_fascia_prezzo"] = per_gruppo(righe, lambda r: fascia(r["vero"]), ("m_calcolo",), ordine_f)
    stime = tuple("m_" + v for v in VARIANTI)
    if any(r.get("m_dopo") for r in righe):
        out["prima_dopo"] = {s: metriche([(r[s], r["vero"]) for r in righe if all(r.get(x) for x in stime)])
                             for s in stime}
        out["prima_dopo_per_fascia_M"] = per_gruppo(righe, lambda r: fascia(r.get("m_prima")), stime, ordine_f)
        out["prima_dopo_per_fascia_prezzo"] = per_gruppo(righe, lambda r: fascia(r["vero"]), stime, ordine_f)
        # effetto selezione (rapporto 2 §2.5): auto che "sembravano" sotto mercato rispetto a M di allora
        out["per_sconto_apparente"] = per_gruppo(
            righe, lambda r: next((n for lim, n in SCONTO if r["vero"] / r["m_calcolo"] < lim), None)
            if r.get("m_calcolo") else None, ("m_calcolo",) + stime, tuple(n for _, n in SCONTO))
    stesse = [r for r in righe if r.get("m_calcolo") and r.get("perizia")]
    out["stesse_auto_calcolo_vs_perizia"] = {"m_calcolo": metriche([(r["m_calcolo"], r["vero"]) for r in stesse]),
                                             "perizia": metriche([(r["perizia"], r["vero"]) for r in stesse])}
    tre = [r for r in stesse if r.get("autohero")]
    if tre:
        out["stesse_auto_tutte_e_tre"] = {k: metriche([(r[k], r["vero"]) for r in tre]) for k in ("m_calcolo", "perizia", "autohero")}
    return out


def regole_varianti(rg=None) -> dict:
    """Regole "prima" (interruttori nuovi del 12/11 sera spenti, cioè il calcolo di 4e26d24), "dopo" (quelle in vigore)
    e "tutte" (anche le novità spente di default)."""
    from .. import regole as RG
    rg = rg or RG.regole()
    return {"prima": RG.con_campi(rg, RG.NOVITA_FASE2), "dopo": rg,
            "tutte": RG.con_campi(rg, {k: True for k in RG.NOVITA_FASE2})}


def rivaluta(conn, lid: int, varianti: dict, cache: dict) -> dict:
    """M al venduto di questa auto (già sparita) con ogni variante di regole, sul mercato attivo di OGGI senza
    l'auto stessa, senza AI (niente giudice): {"m_prima": .., "m_dopo": .., "m_tutte": ..}."""
    from .. import regole as RG
    from ..pricing import dotazioni as DT
    from ..pricing import policy_v2 as P
    from ..pricing import valutazione as VA
    from ..pricing.ricalcolo import MARKET_SQL, _j, _valuta_base
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    r = conn.execute(f"SELECT {VA.COLONNE} FROM listings l WHERE l.id = %s", (lid,)).fetchone()
    if not r:
        return {}
    r = dict(r)
    ing = cache.get("_ing")
    if ing is None:
        ing = cache["_ing"] = VA.ingressi(conn, [], solo=("calibra",))
    ing = dict(ing, **{k: v for k, v in VA.ingressi(conn, [lid], solo=("vr", "ai", "parti")).items()
                       if k in ("vr", "ai", "parti")})
    ing.setdefault("difetti", {})
    t, _motivo = VA.prepara(r, ing, varianti["dopo"])
    t.update(DT.carica_ids(conn, [lid]).get(lid) or {})
    key = (r["make"], r["model"], r["fuel"])
    if cache.get("_chiave") != key:
        cache.clear()
        cache["_ing"] = ing
        mercato = [dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                   for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
        cache.update(_chiave=key, _mercato=DT.arricchisci(mercato, DT.carica_gruppo(conn, *key)))
    confronti = [m for m in cache["_mercato"] if m.get("id") != lid]
    out = {}
    for nome, rg in varianti.items():
        cfg = P.PolicyConfig(rg)
        v, _ = _valuta_base(conn, t, confronti, scovo.soglia(int(r["price_eur"])), cfg, {})
        if not v.get("M"):
            continue
        v2 = RG.calibra_vendita(v, t.get("fattori_calibra"), rg)
        out["m_" + nome] = int(v2.get("M") or v["M"])
    return out


def esegui(conn, n: int = 30, giorni: int = 30, chiama=None, ai: bool = True, prima_dopo: bool = True) -> dict:
    """Prova alla cieca su n auto sparite negli ultimi `giorni` giorni. `chiama` = risposte finte (test).
    ai=False: niente perizia AI (costo zero), solo il nostro calcolo e Autohero. prima_dopo: M rifatto con le regole
    prima/dopo/tutte (rivaluta)."""
    t0 = time.time()
    varianti = regole_varianti() if prima_dopo else None
    cache_m: dict = {}
    n, giorni = max(1, min(int(n), 200)), max(1, min(int(giorni), 90))
    autos = [dict(r) for r in conn.execute(AUTO_SQL, {"giorni": giorni, "n": n, "pool": n * 4}).fetchall()]
    ah_ok = bool(conn.execute("SELECT to_regclass('autohero_prezzi') AS t").fetchone()["t"])
    righe, token, errori = [], [0, 0], {}
    ai_ferma = False
    for a in autos:
        vero = int(a["price_eur"])
        riga = {"id": a["id"], "auto": f"{a.get('make')} {a.get('model')}", "anno": a.get("year"), "km": a.get("mileage_km"),
                "vero": vero, "m_calcolo": int(round(float(a["m_calcolo"]))) if a.get("m_calcolo") is not None else None,
                "perizia": None, "autohero": None}
        cieca = dict(a, price_eur=None)                    # il prezzo si nasconde
        conf = a.get("confronti") or []
        if isinstance(conf, str):
            conf = json.loads(conf)
        if varianti:
            try:
                riga.update(rivaluta(conn, a["id"], varianti, cache_m))
            except Exception as e:
                errori["rivaluta"] = errori.get("rivaluta", 0) + 1
                if errori["rivaluta"] <= 3:
                    log.warning("PROVA_CIECA rivalutazione %s non riuscita: %s", a["id"], str(e)[:150])
                try:
                    conn.rollback()
                except Exception:
                    pass
        if not ai:
            pass
        elif ai_ferma or (chiama is None and AI.budget_finito(conn)):
            errori["ai_ferma_o_budget_finito"] = errori.get("ai_ferma_o_budget_finito", 0) + 1
        else:
            try:
                conf = confronti_al_momento(conn, PER.scegli_confronti(a, conf, n=40), a.get("disappeared_at")
                                            or datetime.now(timezone.utc))
                p = PER.perizia(cieca, conf, chiama=chiama, cieca=True)
                riga["perizia"] = (p.get("prezzo_vendita") or {}).get("medio")
                riga["perizia_forbice"] = [(p.get("prezzo_vendita") or {}).get(k) for k in ("basso", "alto")]
                riga["confronti"] = len(PER.scegli_confronti(a, conf))
                token[0] += p["_token"][0]
                token[1] += p["_token"][1]
            except PER.PeriziaNonValida as e:
                errori["non_valida"] = errori.get("non_valida", 0) + 1
                riga["perizia_errore"] = str(e)[:80]
            except Exception as e:
                errori["errore"] = errori.get("errore", 0) + 1
                riga["perizia_errore"] = str(e)[:80]
                try:
                    conn.rollback()
                except Exception:
                    pass
                if PER.errore_di_credito(e):
                    ai_ferma = True                        # credito o limite: niente altre chiamate in questa prova
        if ah_ok:
            try:
                riga["autohero"] = prezzo_autohero(conn, a)
            except Exception as e:
                conn.rollback()
                errori["autohero"] = str(e)[:80]
        righe.append(riga)
        log.warning("PROVA_CIECA auto %s", json.dumps(riga, ensure_ascii=False, default=str))
    res = riepilogo(righe)
    res.update(giorni=giorni, richieste=n, errori=errori, perizia_ai=bool(ai), secondi=round(time.time() - t0), autohero_tabella=ah_ok,
               token_in=token[0], token_out=token[1],
               costo_usd=round(AI.costo(AI.MODEL_FAST, token[0], token[1]), 4))
    log.warning("PROVA_CIECA riepilogo %s", json.dumps(res, ensure_ascii=False, default=str))
    return res
