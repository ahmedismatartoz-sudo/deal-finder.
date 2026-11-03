"""Confronto in ombra di due regole di prezzo (3/11, piano di ricostruzione: nessun cambio di prezzo senza prova).

Valuta lo STESSO campione di auto con due configurazioni (A = quella di oggi, B = quella proposta) e dice cosa
cambierebbe sul sito, senza scrivere nulla nel database (connessione in sola lettura):

  - auto che ENTRANO nelle categorie del sito (scovo.CATEGORIE_SITO) con B e auto che ESCONO
  - variazione mediana del margine (B - A) sulle auto che hanno un margine in tutti e due i casi
  - le 20 variazioni più grandi, con categoria e margine prima/dopo
  - passaggi di categoria (A -> B)

    python -m dealfinder.pricing.confronto_ombra --n 500
    python -m dealfinder.pricing.confronto_ombra --n 500 --b '{"costo_fisso": 600}'
    python -m dealfinder.pricing.confronto_ombra --n 300 --a '{"SOGLIE_PV": "7000:1500"}' --b-file proposta.json --json

Una configurazione è un dict: i nomi in minuscolo sono campi di policy_v2.PolicyConfig (sopra quella risolta
dall'ambiente), i nomi in MAIUSCOLO sono variabili d'ambiente messe solo durante quella valutazione (valgono per
le regole lette a ogni chiamata, non per le costanti lette all'import dei moduli). Al posto del dict si può passare
una funzione f(t, mercato, soglia) -> valutazione (dict con "categoria" e "margine"): così si prova anche un motore
nuovo (es. pricing/valutazione.py) contro quello di oggi.

Il confronto usa P.valuta con il ripiego sui confronti del Nord (come il ricalcolo) ma senza il giudice AI (costa e
non serve per confrontare due regole). Nessuna scrittura, nessuna chiamata esterna.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import logging
import os
import statistics
import sys
from collections import Counter

from . import policy_v2 as P

log = logging.getLogger("ombra")
TOP = 20

CAMPIONE_SQL = """
/*ombra:campione*/
SELECT listing_id FROM (
    SELECT vp.listing_id, (vp.categoria = ANY(%(sito)s)) AS sito,
           row_number() OVER (PARTITION BY (vp.categoria = ANY(%(sito)s)) ORDER BY md5(vp.listing_id::text || %(seme)s)) AS rn
    FROM valutazioni_policy vp JOIN listings l ON l.id = vp.listing_id
    WHERE vp.policy_version = %(pv)s AND l.status = 'attivo' AND vp.computed_at > now() - interval '7 days'
) x
WHERE (sito AND rn <= %(n_sito)s) OR (NOT sito AND rn <= %(n_altre)s)
"""


def _target_sql() -> str:
    from .ricalcolo import TARGET_COLS
    return (f"/*ombra:auto*/ SELECT {TARGET_COLS}, l.title, l.description, l.telefono_ok, "
            "v.parts_cost_low, v.parts_cost_high FROM listings l "
            "LEFT JOIN LATERAL (SELECT parts_cost_low, parts_cost_high FROM valuations WHERE listing_id = l.id "
            "ORDER BY created_at DESC LIMIT 1) v ON true WHERE l.id = ANY(%s)")


@contextlib.contextmanager
def _ambiente(valori: dict):
    """Variabili d'ambiente messe solo dentro il blocco, poi rimesse com'erano."""
    prima = {k: os.environ.get(k) for k in valori}
    try:
        for k, v in valori.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in prima.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def config_da_dict(d: dict | None) -> tuple[P.PolicyConfig, dict]:
    """(PolicyConfig, variabili d'ambiente) da un dict: minuscolo = campo di PolicyConfig, MAIUSCOLO = ambiente."""
    d = dict(d or {})
    env = {k: v for k, v in d.items() if k.isupper()}
    campi = {k: v for k, v in d.items() if not k.isupper()}
    noti = {f.name for f in dataclasses.fields(P.PolicyConfig)}
    ignoti = sorted(set(campi) - noti)
    if ignoti:
        raise ValueError("campi di PolicyConfig sconosciuti: " + ", ".join(ignoti))
    with _ambiente(env):
        base = P.PolicyConfig.from_env()
    if "fasce" in campi:
        campi["fasce"] = [tuple(x) for x in campi["fasce"]]
    return dataclasses.replace(base, **campi), env


def valutatore(regola, conn=None):
    """Da un dict di configurazione (o da una funzione) a f(t, mercato, soglia) -> valutazione."""
    if callable(regola):
        return regola
    cfg, env = config_da_dict(regola)
    cache: dict = {}

    def f(t, mercato, soglia):
        with _ambiente(env):
            if conn is not None:
                from .ricalcolo import _valuta_base
                return _valuta_base(conn, t, mercato, soglia, cfg, cache)[0]
            return P.valuta(t, mercato, soglia, cfg)
    return f


def _sito_categorie() -> set:
    try:
        from ..web import scovo
        return set(scovo.CATEGORIE_SITO)
    except Exception:
        return {"opportunita", "candidato"}


def confronta(campione, valuta_a, valuta_b, categorie_sito=None, top: int = TOP) -> dict:
    """Nucleo puro: campione = iterabile di (t, mercato, soglia). Ritorna il rapporto."""
    sito = set(categorie_sito or _sito_categorie())
    entrano, escono, delta, righe, errori = [], [], [], [], []
    passaggi = Counter()
    n = 0
    for t, mercato, soglia in campione:
        n += 1
        try:
            a = valuta_a(t, mercato, soglia) or {}
            b = valuta_b(t, mercato, soglia) or {}
        except Exception as e:
            errori.append({"id": t.get("id"), "errore": type(e).__name__ + ": " + str(e)[:120]})
            continue
        ca, cb = a.get("categoria"), b.get("categoria")
        ma, mb = a.get("margine"), b.get("margine")
        passaggi[f"{ca} -> {cb}"] += 1
        r = {"id": t.get("id"), "auto": " ".join(str(t.get(k) or "") for k in ("make", "model", "year")).strip(),
             "prezzo": t.get("price_eur"), "categoria_a": ca, "categoria_b": cb, "margine_a": ma, "margine_b": mb,
             "delta": (mb - ma) if isinstance(ma, (int, float)) and isinstance(mb, (int, float)) else None}
        if ca not in sito and cb in sito:
            entrano.append(r)
        elif ca in sito and cb not in sito:
            escono.append(r)
        if r["delta"] is not None:
            delta.append(r["delta"])
        righe.append(r)
    grandi = sorted((r for r in righe if r["delta"] is not None or r["categoria_a"] != r["categoria_b"]),
                    key=lambda r: (-(abs(r["delta"]) if r["delta"] is not None else float("inf")), str(r["id"])))[:top]
    sito_a = sum(1 for r in righe if r["categoria_a"] in sito)
    sito_b = sum(1 for r in righe if r["categoria_b"] in sito)
    return {
        "valutate": len(righe), "campione": n, "errori": errori[:20], "n_errori": len(errori),
        "sito_a": sito_a, "sito_b": sito_b,
        "entrano": len(entrano), "escono": len(escono),
        "entrano_id": [r["id"] for r in entrano][:100], "escono_id": [r["id"] for r in escono][:100],
        "margine_delta_mediana": statistics.median(delta) if delta else None,
        "margine_delta_media": round(statistics.fmean(delta), 1) if delta else None,
        "margine_cambiati": sum(1 for d in delta if d), "con_margine": len(delta),
        "passaggi": dict(passaggi.most_common()),
        "piu_grandi": grandi,
    }


def campione_dal_db(conn, n: int = 500, seme: str = "ombra", quota_sito: float = 0.5, ids=None):
    """(t, mercato, soglia) per N auto attive già valutate negli ultimi 7 giorni: metà (quota_sito) tra quelle oggi
    nelle categorie del sito, il resto tra le altre (per vedere anche chi entra). Solo letture."""
    from ..store import HOME_REGION_SQL
    from ..web import scovo
    from .ricalcolo import MARKET_SQL, _j, _rischio, ai_di, targa_e_telefono, targhe_visibili, vr_di
    if ids is None:
        n_sito = int(round(n * quota_sito))
        ids = [r["listing_id"] for r in conn.execute(CAMPIONE_SQL, {
            "sito": sorted(_sito_categorie()), "seme": seme, "pv": P.POLICY_VERSION,
            "n_sito": n_sito, "n_altre": n - n_sito}).fetchall()]
    if not ids:
        return []
    targets = conn.execute(_target_sql(), (list(ids),)).fetchall()
    targets.sort(key=lambda r: (r["make"] or "", r["model"] or "", r["fuel"] or ""))
    aimap, vrmap, targhe = ai_di(conn, [r["id"] for r in targets]), vr_di(conn, [r["id"] for r in targets]), \
        targhe_visibili(conn)
    out, gruppo, mercato = [], None, []
    for r in targets:
        key = (r["make"], r["model"], r["fuel"])
        if key != gruppo:
            gruppo = key
            mercato = ([dict(m, vehicle_identity=_j(m.get("vehicle_identity")))
                        for m in conn.execute(MARKET_SQL.format(home=HOME_REGION_SQL), key).fetchall()]
                       if all(key) else [])
        t = dict(r)
        t["vehicle_identity"] = _j(t.get("vehicle_identity"))
        t["rischio_strutturale"] = _rischio(r)
        t["ai"] = aimap.get(r["id"])
        t["vr"] = vrmap.get(r["id"])
        t["targa_e_telefono"] = targa_e_telefono(r, targhe)
        out.append((t, mercato, scovo.soglia(int(r["price_eur"] or 0))))
    return out


def _connessione_lettura():
    """Connessione nuova (non del pool) in sola lettura per tutta la sessione, statement_timeout 60 s."""
    import psycopg
    from psycopg.rows import dict_row
    from ..config import settings
    return psycopg.connect(settings.database_url, row_factory=dict_row,
                           options="-c default_transaction_read_only=on -c statement_timeout=60000")


def esegui(regola_a=None, regola_b=None, n: int = 500, seme: str = "ombra", connetti=None) -> dict:
    """Confronto completo sul database (solo lettura). regola_a/regola_b: dict o funzione (None = regole di oggi)."""
    connetti = connetti or _connessione_lettura
    conn = connetti()
    try:
        from .ricalcolo import carica_taratura
        try:
            carica_taratura(conn)                    # stessa taratura del ricalcolo (solo lettura)
        except Exception as e:
            log.warning("taratura non caricata: %s", type(e).__name__)
        campione = campione_dal_db(conn, n=n, seme=seme)
        rapporto = confronta(campione, valutatore(regola_a, conn), valutatore(regola_b, conn))
    finally:
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass
    rapporto.update(n_richieste=n, seme=seme, policy=P.POLICY_VERSION)
    return rapporto


def testo(r: dict) -> str:
    righe = [f"Confronto in ombra ({r.get('policy')}): {r['valutate']} auto valutate con A e con B"
             + (f", {r['n_errori']} in errore" if r.get("n_errori") else ""),
             f"Sul sito: A {r['sito_a']}  B {r['sito_b']}   entrano {r['entrano']}  escono {r['escono']}",
             f"Margine B - A: mediana {r['margine_delta_mediana']}  media {r['margine_delta_media']}  "
             f"cambiati {r['margine_cambiati']} su {r['con_margine']}",
             "Passaggi di categoria:"]
    righe += [f"  {k}: {v}" for k, v in r["passaggi"].items()]
    righe.append(f"Le {len(r['piu_grandi'])} variazioni più grandi:")
    for x in r["piu_grandi"]:
        righe.append(f"  {x['id']:>9} {x['auto'][:30]:30} {x['prezzo'] or '-':>7}  {x['categoria_a']} {x['margine_a']}"
                     f" -> {x['categoria_b']} {x['margine_b']}  (delta {x['delta']})")
    return "\n".join(righe)


def _carica(testo_json, file):
    if file:
        with open(file, encoding="utf-8") as f:
            return json.load(f)
    return json.loads(testo_json) if testo_json else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Confronto in ombra di due regole di prezzo (sola lettura)")
    ap.add_argument("--n", type=int, default=500, help="auto nel campione (metà oggi sul sito, metà no)")
    ap.add_argument("--a", help="regole A come JSON (default: quelle di oggi)")
    ap.add_argument("--b", help="regole B come JSON (default: quelle di oggi: nessuna differenza attesa)")
    ap.add_argument("--a-file")
    ap.add_argument("--b-file")
    ap.add_argument("--seme", default="ombra", help="seme del campione (stesso seme = stesse auto)")
    ap.add_argument("--json", action="store_true", help="rapporto completo in JSON")
    x = ap.parse_args(argv)
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL non impostata: niente da confrontare")
        return 2
    r = esegui(_carica(x.a, x.a_file), _carica(x.b, x.b_file), n=x.n, seme=x.seme)
    print(json.dumps(r, ensure_ascii=False, default=str, indent=1) if x.json else testo(r))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sys.exit(main())
