"""17/11 (notte-09, correzioni del giudice).

1. Prezzo cambiato dopo la valutazione: LIST_SQL segnala prezzo_dopo_valutazione (non filtra: il ricalcolo sceglie da
   LIST_SQL le auto da rivalutare), l'auto esce dal sito alla regola "valutata" come prezzo_cambiato_da_rivalutare
   (sempre nel SITO_FUNNEL) e il ricalcolo NON la salta come "invariata".
2. Motivo dello stadio: Python (scovo.motivo_stadio) e SQL (scovo.motivo_stadio_sql) tagliano allo stesso modo.
3. Confronti esatti con 'incidentata' allineati: "incidentata | scomparso" resta incidentata.
4. COMMERCIANTI_TOP scrive un motivo che LIST_SQL legge; il tetto 40 lontani / 60 in tutto resta rispettato.
"""
from __future__ import annotations

import inspect

from tests.test_notte09_imbuto import _riga

_SCHEMA = """
    CREATE TABLE listings (id int PRIMARY KEY, source text, url text, title text, make text, model text,
      version_raw text, year int, mileage_km int, fuel text, gearbox text, power_kw int, city text, province text,
      price_eur int, damage_class text, stage text, stage_reason text, status text, first_seen_at timestamptz,
      deep_at timestamptz, last_checked_at timestamptz, last_seen_at timestamptz, contenuto_at timestamptz);
    CREATE TABLE valuations (listing_id int, created_at timestamptz DEFAULT now(), private_median int,
      resale_median_private int, resale_median int, parts_cost_low int, parts_cost_high int, n_comparables int,
      confidence text, engine_version text);
    CREATE TABLE valutazioni_policy (listing_id int, policy_version text DEFAULT 'pv2', computed_at timestamptz DEFAULT now(),
      riassunto jsonb, margine int, impronta_regole text);
    CREATE TABLE top_auto (listing_id int);
    CREATE TABLE listing_opens (listing_id int, dealer_id int);
    CREATE TABLE listing_photos (listing_id int);
    CREATE TABLE price_events (id bigserial PRIMARY KEY, listing_id int, event text, price_eur int,
      at timestamptz NOT NULL DEFAULT now());
"""


def _auto(c, i, prezzo, eventi, vp_fa="2 hours", v_fa="5 hours", margine=-5000, imp=""):
    c.execute("INSERT INTO listings (id, source, stage, stage_reason, status, price_eur, last_checked_at, last_seen_at) "
              "VALUES (%s, 'subito', 'approfondito', 'opportunita', 'attivo', %s, now(), now())", (i, prezzo))
    c.execute("INSERT INTO valuations (listing_id, private_median, created_at) VALUES (%s, 10000, now() - %s::interval)",
              (i, v_fa))
    if vp_fa:
        c.execute("INSERT INTO valutazioni_policy (listing_id, computed_at, riassunto, margine, impronta_regole) "
                  "VALUES (%s, now() - %s::interval, '{\"margine\": 2000}', %s, %s)", (i, vp_fa, margine, imp))
    for ev, p, fa in eventi:
        c.execute("INSERT INTO price_events (listing_id, event, price_eur, at) VALUES (%s, %s, %s, now() - %s::interval)",
                  (i, ev, p, fa))


def test_prezzo_dopo_valutazione_postgres_vero():
    from tests import pg_vero
    from dealfinder.web import scovo
    c = pg_vero.nuovo_db("notte09_prezzo")
    if c is None:
        return
    pg_vero.psql(c.d, c.db, _SCHEMA)
    _auto(c, 1, 7000, [("apparso", 7000, "3 hours")])                                     # niente dopo: valida
    _auto(c, 2, 8000, [("apparso", 7000, "3 hours"), ("prezzo", 8000, "1 hour")])         # prezzo salito dopo
    _auto(c, 3, 7000, [("apparso", 7000, "3 hours"), ("scomparso", 7000, "90 minutes"),
                       ("riapparso", 7000, "1 hour")])                                    # riapparsa, stesso prezzo
    _auto(c, 4, 7000, [("apparso", 7000, "3 hours"), ("prezzo", 8000, "1 hour"),
                       ("prezzo", 7000, "30 minutes")])                                   # prezzo tornato com'era
    _auto(c, 5, 8000, [("prezzo", 8000, "1 hour")])                                       # senza storico prima: prudente
    _auto(c, 6, 6500, [("apparso", 7000, "6 hours"), ("prezzo", 6500, "1 hour")], vp_fa=None)   # solo valuations
    _auto(c, 7, 8000, [("apparso", 7000, "3 hours"), ("scomparso", 7000, "100 minutes"), ("riapparso", 8000, "1 hour"),
                       ("prezzo", 8000, "1 hour")])                                       # riapparsa col prezzo nuovo
    _auto(c, 8, 7000, [("apparso", 7000, "3 hours"), ("ripubblicato", 9000, "1 hour")])  # 'ripubblicato' non conta
    _auto(c, 9, 8000, [("apparso", 7000, "3 hours"), ("prezzo", 8000, "1 hour")], vp_fa="10 minutes")  # già rivalutata
    righe = {r["id"]: r for r in c.execute(scovo.LIST_SQL, {"me": 0}).fetchall()}
    # LIST_SQL NON filtra: tutte presenti (il ricalcolo le sceglie da qui)
    assert sorted(righe) == list(range(1, 10))
    segnate = sorted(i for i, r in righe.items() if r["prezzo_dopo_valutazione"])
    assert segnate == [2, 5, 6, 7]
    # anche come sottoquery del ricalcolo (ricalcolo.esegui, simula)
    assert len(c.execute("SELECT q.id FROM (" + scovo.LIST_SQL + ") q", {"me": 0}).fetchall()) == 9
    # rivalutata col prezzo nuovo (computed_at dopo l'evento): torna valida
    c.execute("UPDATE valutazioni_policy SET computed_at = now() WHERE listing_id = 2")
    r = c.execute(scovo.LIST_SQL + " AND l.id = 2", {"me": 0}).fetchone()
    assert r["prezzo_dopo_valutazione"] is False


def test_ricalcolo_non_salta_le_auto_col_prezzo_cambiato():
    """senza_invariate (ricalcolo.esegui) salta le approfondite valutate da poco, lontane dalla soglia e invariate:
    un'auto con il prezzo cambiato dopo la valutazione si rivaluta sempre."""
    from tests import pg_vero
    from dealfinder.pipeline import scelta_candidate as SC
    from dealfinder.pricing import ricalcolo
    c = pg_vero.nuovo_db("notte09_ricalcolo")
    if c is None:
        return
    pg_vero.psql(c.d, c.db, _SCHEMA)
    imp = SC.parametri_invariate()["inv_regole"]
    _auto(c, 1, 7000, [("apparso", 7000, "3 hours")], imp=imp)
    _auto(c, 2, 8000, [("apparso", 7000, "3 hours"), ("prezzo", 8000, "1 hour")], imp=imp)
    da_fare, saltate = ricalcolo.senza_invariate(c, [1, 2], "RIC_INVARIATE_ORE")
    assert (da_fare, saltate) == ([2], 1), (da_fare, saltate, imp, SC.parametri_invariate())


def test_prezzo_cambiato_esce_alla_regola_valutata():
    from dealfinder.web import scovo
    from dealfinder.web import sito_regole as SR
    pv = {"categoria": "opportunita", "M": 10000, "R": 9500, "R_finale": 9500, "margine": 2000, "n": 10}
    it = scovo.item(_riga(1, "opportunita", pv=pv, prezzo_dopo_valutazione=True))
    assert it["prezzo_dopo_valutazione"] is True
    assert scovo.item(_riga(2, "opportunita", pv=pv))["prezzo_dopo_valutazione"] is False
    g = SR.Regole()
    assert SR.r_valutata(dict(it, pv=dict(pv)), g) == "prezzo_cambiato_da_rivalutare"
    # sempre scritto nel SITO_FUNNEL, anche se non è tra i 12 motivi più frequenti
    items = [{"id": 100 + k, "pv": None} for k in range(3)]
    items += [{"id": 200 + k, "pv": {"categoria": "dati_insufficienti"}, "_r": {}} for k in range(3)]
    items.append(dict(it, pv=dict(pv)))
    fun = SR.Funnel()
    dentro = SR._applica(items, "valutata", SR.r_valutata, g, fun)
    assert dentro == [] and fun.passi[-1]["motivi"]["prezzo_cambiato_da_rivalutare"] == 1
    assert "prezzo_cambiato_da_rivalutare" in SR.MOTIVI_SEMPRE


def test_motivo_stadio_python_uguale_a_sql():
    from dealfinder.web import scovo
    assert scovo.motivo_stadio_sql("l.stage_reason") == "btrim(split_part(COALESCE(l.stage_reason, ''), '|', 1))"
    casi = ["opportunita", "opportunita | scomparso (404)", "opportunita|scomparso", "incidentata  |venduto sito",
            " da_verificare ", " | scomparso", "", None, "affidabile | a | b"]
    for x in casi:
        assert scovo.motivo_stadio(x) == (x or "").split("|")[0].strip()
    from tests import pg_vero
    c = pg_vero.nuovo_db("notte09_motivo")
    if c is None:
        return
    for x in casi:
        r = c.execute("SELECT " + scovo.motivo_stadio_sql("%(x)s::text") + " AS m", {"x": x}).fetchone()
        assert r["m"] == scovo.motivo_stadio(x), x


def test_incidentata_con_note_resta_incidentata():
    """I confronti esatti con 'incidentata' (e le liste dei motivi del sito) leggono il motivo base."""
    from dealfinder.ai import controllo_auto as CA
    from dealfinder.web import scovo
    espr = scovo.motivo_stadio_sql("l.stage_reason")
    assert espr + " <> 'incidentata'" in CA._CAND_FINE or espr + " <> 'incidentata'" in inspect.getsource(CA)
    assert "l.stage_reason IS DISTINCT FROM 'incidentata'" not in inspect.getsource(CA)
    base = {"d_fonte": "jev", "d_prezzo": 5000, "price_eur": 5000, "m": 20000, "d_truffa": 1.0}
    assert CA.sconto_fermo(dict(base, stage_reason="incidentata | scomparso (404)")) is False
    assert CA.sconto_fermo(dict(base, stage_reason="incidentata")) is False
    # sorgenti allineati: nessun confronto esatto rimasto sulle liste dei motivi del sito
    import dealfinder.pipeline.collect as COL
    import dealfinder.pipeline.manuale as MAN
    import dealfinder.pipeline.schede_auto as SA
    import dealfinder.pricing.ripiego_prudente as RP
    import dealfinder.web.app as APP
    for m in (CA, COL, MAN, SA, RP, APP):
        src = inspect.getsource(m)
        assert "stage_reason IN ('opportunita'" not in src, m.__name__
        assert "stage_reason IN ('opportunita','da_verificare','incidentata')" not in src, m.__name__


def test_commercianti_top_motivo_e_tetto():
    from dealfinder.web import app as APP
    from dealfinder.web import scovo
    from dealfinder.web import sito_regole as SR
    assert APP.motivo_commercianti_top("opportunita") == "opportunita"
    assert APP.motivo_commercianti_top("candidato") == "da_verificare"
    for cat in scovo.CATEGORIE_SITO:
        assert APP.motivo_commercianti_top(cat) in scovo.MOTIVI_ELENCO and APP.motivo_commercianti_top(cat) != "affidabile"
    assert "(motivo_commercianti_top(cat), lid)" in inspect.getsource(APP)
    # le auto dei commercianti lontani pubblicate dal giro (motivo nuovo) passano dal tetto come tutte: 40 lontani
    s = scovo.soglia(5000)

    def comm(i, motivo, prov="BG"):
        it = scovo.item(_riga(i, motivo, price_eur=5000, province=prov, city=None, pv={
            "categoria": "opportunita", "M": 6000, "R": 5000 + s + 300, "R_finale": 5000 + s + 300, "margine": s + i % 97,
            "totale_costi": 200, "n": 10}))
        it.update(carb="Diesel", km=90000, anno=2015, fonte="Subito")
        it["_r"] = {"tel": True, "seller_type": "commerciante", "provincia": prov, "valutazione_nuova": True}
        return it

    lontani = [comm(1000 + k, APP.motivo_commercianti_top("opportunita" if k % 2 else "candidato")) for k in range(70)]
    out, fun = SR.elenco_sito(lontani, SR.Regole(), scrivi_log=False)
    assert 0 < len(out) <= 40 and sum(1 for it in out if it.get("_tipo") == "comm_lontano") <= 40
    # senza il tetto dei 40 (solo per la prova) vale quello dei 60 in tutto
    lontani = [comm(1000 + k, APP.motivo_commercianti_top("opportunita")) for k in range(70)]
    out, _ = SR.elenco_sito(lontani, SR.Regole(commercianti_max=1000), scrivi_log=False)
    assert 0 < len(out) <= 60 and sum(1 for it in out if SR.e_commerciante(it)) <= 60


# ------------------------------------------------------------------ 2a revisione del giudice
def test_riassunto_salva_il_prezzo_d_acquisto():
    from dealfinder.pricing import policy_v2 as P
    assert P.riassunto({"categoria": "opportunita", "acquisto": 7000, "margine": 2000})["acquisto"] == 7000


def test_prezzo_diverso_da_quello_valutato_postgres_vero():
    """Con riassunto->'acquisto' vale il confronto diretto (niente corsa tra transazioni sugli eventi)."""
    from tests import pg_vero
    from dealfinder.web import scovo
    c = pg_vero.nuovo_db("notte09_acquisto")
    if c is None:
        return
    pg_vero.psql(c.d, c.db, _SCHEMA)
    _auto(c, 1, 8000, [("apparso", 7000, "3 hours")])          # evento 'prezzo' perso nella corsa: lo dice 'acquisto'
    _auto(c, 2, 8000, [("apparso", 7000, "3 hours"), ("prezzo", 8000, "1 hour")])   # valutata già a 8.000
    _auto(c, 3, 7000, [("apparso", 7000, "3 hours")])
    _auto(c, 4, 8000, [("apparso", 7000, "3 hours"), ("prezzo", 8000, "1 hour")])   # 'acquisto' illeggibile: eventi
    for i, acq in ((1, '7000'), (2, '8000'), (3, '7000.0'), (4, '"abc"')):
        c.execute("UPDATE valutazioni_policy SET riassunto = riassunto || jsonb_build_object('acquisto', %s::jsonb) "
                  "WHERE listing_id = %s", (acq, i))
    righe = {r["id"]: r["prezzo_dopo_valutazione"] for r in c.execute(scovo.LIST_SQL, {"me": 0}).fetchall()}
    assert righe == {1: True, 2: False, 3: False, 4: True}


def test_eco_sql_prezzo_dopo_valutazione_postgres_vero():
    from tests import pg_vero
    from dealfinder.web import scovo
    c = pg_vero.nuovo_db("notte09_eco")
    if c is None:
        return
    pg_vero.psql(c.d, c.db, _SCHEMA + """
        ALTER TABLE listings ADD COLUMN model_p50 int, ADD COLUMN seller_type text;""")
    for i in (1, 2, 3, 4):
        c.execute("INSERT INTO listings (id, source, stage, stage_reason, status, price_eur, year, province, "
                  "last_checked_at, last_seen_at) VALUES (%s, 'subito', 'nuovo', %s, 'attivo', 900, 2015, 'MI', now(), now())",
                  (i, "truffa | scomparso (404)" if i == 4 else None))
    # 1: nessuna valutazione (LEFT JOIN vuota) → falso; 2: valutata a 800, oggi 900 → vero; 3: valutata a 900 → falso
    c.execute("INSERT INTO valutazioni_policy (listing_id, riassunto) VALUES (2, '{\"acquisto\": 800}'), "
              "(3, '{\"acquisto\": 900}')")
    c.execute("INSERT INTO price_events (listing_id, event, price_eur, at) VALUES (1, 'prezzo', 900, now())")
    righe = {r["id"]: r["prezzo_dopo_valutazione"] for r in c.execute(scovo.ECO_SQL, scovo.eco_params(0)).fetchall()}
    assert righe == {1: False, 2: True, 3: False}             # 4: "truffa | scomparso" resta fuori (motivo base)


def test_eco_e_supercar():
    from dealfinder.web import app as APP
    from dealfinder.web import scovo
    from dealfinder.web import sito_regole as SR
    r = dict(_riga(1, None, price_eur=900, pv={"categoria": "opportunita", "M": 2500, "R": 2400, "R_finale": 2400,
                                              "margine": 900, "n": 10}), model_p50=None, prezzo_dopo_valutazione=True)
    it = scovo.item_eco(r)
    assert it["prezzo_dopo_valutazione"] is True
    assert SR.r_valutata(it, SR.Regole()) == "prezzo_cambiato_da_rivalutare"
    assert scovo.item_eco(dict(r, prezzo_dopo_valutazione=None))["prezzo_dopo_valutazione"] is False
    sc = APP._supercar_item(dict(r, price_eur=150000), {"m": 200000, "paese": "DE", "n": 8, "costi": 3000},
                            {"R": 190000, "margine": 30000}, {}, lusso=False)
    assert sc["prezzo_dopo_valutazione"] is False and SR.r_valutata(sc, SR.Regole()) is None


def test_ricalcolo_motivo_base():
    from dealfinder.pricing import ricalcolo
    assert "btrim(split_part(COALESCE(l.stage_reason, ''), '|', 1)) NOT IN ('doppione','truffa'" in ricalcolo.UTIL_SQL
    assert "COALESCE(l.stage_reason, '') NOT IN" not in ricalcolo.UTIL_SQL
