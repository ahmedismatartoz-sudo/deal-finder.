"""12/11 buchi della raccolta (§3.1, §3.2): scelta delle candidate senza posti sprecati e cambi di prezzo in ordine."""
from __future__ import annotations

import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.pipeline import scelta_candidate as SC   # noqa: E402


class _R:
    def __init__(self, righe):
        self._r = righe

    def fetchall(self):
        return list(self._r)

    def fetchone(self):
        return self._r[0] if self._r else None


class FintaConn:
    def __init__(self, righe):
        self.righe = {r["id"]: r for r in righe}

    def execute(self, sql, params=None):
        if "FROM ai_difetti" in sql:
            return _R([])
        if "FROM listings WHERE id = ANY" in sql:
            return _R([self.righe[i] for i in params[0] if i in self.righe])
        raise AssertionError(sql)

    def rollback(self):
        pass


def _auto(i, **k):
    r = {"id": i, "source": "subito", "title": "Fiat Panda", "description": "ottime condizioni", "damage_declared": False,
         "damage_items": None, "damage_class": None, "seller_type": "privato", "make": "fiat", "model": "panda",
         "fuel": "benzina", "price_eur": 5000, "mileage_km": 90000, "year": 2015, "telefono_ok": True,
         "vehicle_identity": None, "problem_search": False, "price_flags": [], "stage_reason": None}
    r.update(k)
    return r


def test_motivi_sicuri():
    assert SC.motivo_sicuro("km_oltre_150000") and SC.motivo_sicuro("senza_numero") and SC.motivo_sicuro("km_mancanti")
    assert SC.motivo_sicuro("danno_grave") and SC.motivo_sicuro("anno_prima_del_2007")
    assert not SC.motivo_sicuro("danni_da_leggere") and not SC.motivo_sicuro(None)


def test_filtra_sicuri_toglie_solo_gli_scarti_certi_e_riempie_il_tetto():
    righe = [_auto(1), _auto(2, mileage_km=180000), _auto(3, telefono_ok=False), _auto(4, mileage_km=None),
             _auto(5, telefono_ok=False, description="chiamare 333 1234567"),       # numero nel testo: resta
             _auto(6, telefono_ok=None), _auto(7, description="motore rotto da sistemare"),   # danni da leggere: resta
             _auto(8), _auto(9)]
    st = Counter()
    out = SC.filtra_sicuri(FintaConn(righe), [{"id": r["id"]} for r in righe], 5, st)
    assert [r["id"] for r in out] == [1, 5, 6, 7, 8]
    assert st["scelta_scartata:km_oltre_150000"] == 1 and st["scelta_scartata:senza_numero"] == 1
    assert st["scelta_scartata:km_mancanti"] == 1 and st["scelta_posti_liberati"] == 3


def test_filtra_sicuri_con_errore_lascia_le_righe():
    class Rotta:
        def execute(self, *a):
            raise RuntimeError("giù")

        def rollback(self):
            pass
    assert [r["id"] for r in SC.filtra_sicuri(Rotta(), [{"id": 1}, {"id": 2}], 1)] == [1]


def _soglia(p):
    return 1000


def test_cambi_in_ordine_ribasso_e_soglia():
    righe = [
        {"id": 1, "price_eur": 9000, "prezzo_prima": 9100, "margine_prima": 100},     # piccolo ribasso, lontana
        {"id": 2, "price_eur": 8000, "prezzo_prima": 9000, "margine_prima": 200},     # +1000: arriva a 1200 >= soglia
        {"id": 3, "price_eur": 7000, "prezzo_prima": 7000, "margine_prima": 900},     # nessun ribasso
        {"id": 4, "price_eur": 5000, "prezzo_prima": 5600, "margine_prima": None},    # mai valutata: vicina
        {"id": 5, "price_eur": 6000, "prezzo_prima": 6500, "margine_prima": -200},    # gap 700: vicina
        {"id": 6, "price_eur": 5500, "prezzo_prima": 5000, "margine_prima": 2000},    # prezzo salito: ultima
        {"id": 7, "price_eur": 4000, "prezzo_prima": 6000, "margine_prima": -500},    # +2000 -> 1500: fascia 0, più grande
    ]
    st = Counter()
    out = SC.ordina_cambi(righe, 5, soglia_fn=_soglia, stats=st)
    assert [r["id"] for r in out] == [7, 2, 4, 5, 1]
    assert st["cambi_fascia_0"] == 2 and st["cambi_oltre_tetto"] == 2


def _pg():
    try:
        import pg_scheda as P
    except Exception:
        return None, None
    c = P.nuovo_db("buchi_cand")
    if c is None:
        return None, None
    P.migra_tutte(c)
    return P, c


def test_candidate_sql_su_postgres_vero():
    """CANDIDATI_SQL con la condizione delle auto invariate e CAMBI_SQL, su un Postgres vero (salta se non c'è)."""
    P, c = _pg()
    if c is None:
        return
    from dealfinder.pipeline import benzina_mi as B
    SC._COL.clear()
    ins = ("INSERT INTO listings (source, source_id, url, make, model, fuel, year, mileage_km, price_eur, province, "
           "seller_type, title, description, telefono_ok) VALUES ('subito', %s, %s, 'fiat', 'panda', 'benzina', 2015, "
           "%s, %s, 'MI', 'privato', 'Panda', 'ok', true) RETURNING id")
    ids = {}
    for i in range(8):                                        # gruppo di confronto: mediana 6.000
        ids[f"g{i}"] = c.execute(ins, (f"g{i}", f"u{i}", 80000, 6000)).fetchone()["id"]
    ids["a"] = c.execute(ins, ("a", "ua", 90000, 3000)).fetchone()["id"]          # valutata ora, invariata: salta
    ids["b"] = c.execute(ins, ("b", "ub", 90000, 3100)).fetchone()["id"]          # valutata, poi prezzo cambiato: entra
    ids["c"] = c.execute(ins, ("c", "uc", 90000, 3200)).fetchone()["id"]          # valutata, poi testo cambiato: entra
    ids["d"] = c.execute(ins, ("d", "ud", 90000, 3300)).fetchone()["id"]          # valutata con regole vecchie: entra
    ids["e"] = c.execute(ins, ("e", "ue", 90000, 3400)).fetchone()["id"]          # mai valutata: entra
    ids["f"] = c.execute(ins, ("f", "uf", 90000, 3500)).fetchone()["id"]
    c.execute("UPDATE listings SET mileage_km = NULL WHERE id = %s", (ids["f"],))  # km mancanti: fuori prima del tetto
    imp = SC.parametri_invariate()["inv_regole"]
    vp = ("INSERT INTO valutazioni_policy (listing_id, policy_version, categoria, margine, riassunto, dati, "
          "impronta_regole, computed_at) VALUES (%s, 'v', 'non_conveniente', %s, '{}', '{}', %s, now() - interval '1 hour')")
    for k in ("a", "b", "c"):
        c.execute(vp, (ids[k], -500, imp))                    # margine lontano dalla soglia (1.500): gap 2.000
    c.execute(vp, (ids["d"], -500, "vecchie"))
    ids["h"] = c.execute(ins, ("h", "uh", 90000, 3600)).fetchone()["id"]          # invariata ma vicina alla soglia: entra
    c.execute(vp, (ids["h"], 500, imp))
    c.execute("INSERT INTO price_events (listing_id, event, price_eur, at) VALUES (%s, 'apparso', 3600, now() - interval '3 hours')",
              (ids["b"],))
    c.execute("INSERT INTO price_events (listing_id, event, price_eur) VALUES (%s, 'prezzo', 3100)", (ids["b"],))
    c.execute("UPDATE listings SET description = 'testo nuovo' WHERE id = %s", (ids["c"],))
    c.execute("UPDATE listings SET last_seen_at = now() WHERE id = %s", (ids["a"],))         # rilettura: non conta
    assert c.execute("SELECT contenuto_at FROM listings WHERE id = %s", (ids["a"],)).fetchone()["contenuto_at"] is None
    sql = B.CANDIDATI_SQL.format(invariate=SC.condizione_invariate(c))
    par = {**SC.parametri_invariate(), "km_obblig": True, "prov": ["MI"], "soglia": 0.92, "lim": 100, "pmin": 1000,
           "pmax": 70000, "solo_privati": True, "gmax": 400000, "util": 1000000, "tutti_carb": True, "kmmax": 150000,
           "solo_nuove": False}
    presi = {r["id"] for r in c.execute(sql, par).fetchall()}
    assert presi == {ids["b"], ids["c"], ids["d"], ids["e"], ids["h"]}, presi
    par["inv_ore"] = 0                                         # spento: torna anche "a"
    assert ids["a"] in {r["id"] for r in c.execute(sql, par).fetchall()}
    cambi = c.execute(SC.CAMBI_SQL, {"ore": 8, "prov": ["MI"], "pmax": 70000, "kmmax": 150000, "tutti_carb": True,
                                     "km_obblig": True, "lettura_max": 100}).fetchall()
    assert [(r["id"], r["prezzo_prima"], r["margine_prima"]) for r in cambi] == [(ids["b"], 3600, -500)]


def simula(n_pop: int = 20000, tetto: int = 5000, giri: int = 8, seme: int = 7) -> dict:
    """Simulazione con le proporzioni dei log dell'8-11/11 (§3.1): popolazione della zona sotto il 92% della mediana,
    in ordine di sconto. Ritorna, per prima/dopo, quante auto DIVERSE e valutabili entrano in `giri` giri e quanti
    posti per giro vanno a scarti sicuri o a rivalutazioni identiche. Nuovi annunci e cambi: ~3% della popolazione
    per giro."""
    rnd = random.Random(seme)
    pop = []
    for i in range(n_pop):
        x = rnd.random()
        motivo = ("km_oltre" if x < .44 else "senza_numero" if x < .57 else "km_mancanti" if x < .59
                  else "danno_grave" if x < .64 else None)
        pop.append({"id": i, "motivo": motivo, "valutata_giro": None})
    out = {}
    for modo in ("prima", "dopo"):
        for a in pop:
            a["valutata_giro"] = None
        diverse, sprecati, ripetute = set(), 0, 0
        for g in range(giri):
            for a in rnd.sample(pop, n_pop * 3 // 100):            # cambiate (prezzo/testo) o nuove in questo giro
                a["valutata_giro"] = None
            posti = []
            for a in pop:
                if modo == "dopo":
                    if a["motivo"]:
                        continue                                    # scarto sicuro: fuori prima del tetto
                    if a["valutata_giro"] is not None and g - a["valutata_giro"] < 3:   # < 10 ore (giri da ~3 ore)
                        continue
                posti.append(a)
                if len(posti) >= tetto:
                    break
            for a in posti:
                if a["motivo"]:
                    sprecati += 1
                    continue
                if a["valutata_giro"] is not None and g - a["valutata_giro"] < 3:    # rifatta entro 10 ore, invariata
                    ripetute += 1
                diverse.add(a["id"])
                a["valutata_giro"] = g
        out[modo] = {"auto_diverse_valutabili": len(diverse), "posti_sprecati_per_giro": sprecati // giri,
                     "rivalutazioni_identiche_per_giro": ripetute // giri}
    return out


def test_simulazione_posti_liberati():
    s = simula()
    prima, dopo = s["prima"], s["dopo"]
    assert dopo["posti_sprecati_per_giro"] == 0 and prima["posti_sprecati_per_giro"] > 2000
    assert dopo["rivalutazioni_identiche_per_giro"] < prima["rivalutazioni_identiche_per_giro"] / 2.5
    assert dopo["auto_diverse_valutabili"] > 2 * prima["auto_diverse_valutabili"]


if __name__ == "__main__":
    print(simula())


def test_soglia_sql_uguale_a_scovo():
    P, c = _pg()
    if c is None:
        return
    from dealfinder.web import scovo
    prezzi = [500, 1499, 1500, 4999, 5000, 6999, 7000, 9999, 10000, 14999, 15000, 25000, 29999, 30000, 80000]
    sql = "SELECT " + ", ".join(f"{SC.soglia_sql(str(p))} AS p{p}" for p in prezzi)
    r = c.execute(sql).fetchone()
    assert [r[f"p{p}"] for p in prezzi] == [scovo.soglia(p) for p in prezzi]
