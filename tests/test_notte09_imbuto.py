"""17/11 (notte-09): bug dell'imbuto del sito che toglievano auto valide (regole invariate).

- LIST_SQL legge il motivo dello stadio senza le note " | ..." di pipeline/verify (auto riapparse) e accetta
  'affidabile' (scritto per errore dal giro COMMERCIANTI_TOP al posto di 'opportunita');
- scovo.item: "da verificare" e "incidentata" anche con le note dopo " | " (mai una incidentata presa per sana);
- doppioni Subito/AutoScout24: a parità di numero si tiene il privato (la copia del commerciante la toglieva il tetto);
  la potenza (da _r["kw"]) separa davvero due auto diverse;
- provincia dal comune anche con data/comuni_zona_lead.json; "provincia_sconosciuta" sempre nel SITO_FUNNEL.
"""
from __future__ import annotations


def _riga(i, motivo, **kw):
    r = {"id": i, "source": "subito", "url": "u", "title": "t", "make": "fiat", "model": "panda", "version_raw": None,
         "year": 2018, "mileage_km": 60000, "fuel": "benzina", "gearbox": "manuale", "power_kw": 51, "city": "Milano",
         "province": "MI", "price_eur": 7000, "damage_class": "nessuno", "stage_reason": motivo, "first_seen_at": None,
         "deep_at": None, "private_median": 10000, "resale_median_private": None, "resale_median": None,
         "parts_cost_low": 0, "parts_cost_high": 0, "n_comparables": 10, "confidence": "affidabile",
         "engine_version": "x", "pv": None, "top": False, "opens": 0, "opened_by_me": False, "n_photos": 0}
    r.update(kw)
    return r


def test_motivo_stadio_senza_note():
    from dealfinder.web import scovo
    assert scovo.motivo_stadio("opportunita | scomparso (404)") == "opportunita"
    assert scovo.motivo_stadio("incidentata | venduto sito") == "incidentata"
    assert scovo.motivo_stadio(None) == "" and scovo.motivo_stadio(" | scomparso") == ""
    assert scovo.motivo_stadio("opportunita|scomparso") == "opportunita"          # anche senza spazi intorno a "|"
    assert ("btrim(split_part(COALESCE(l.stage_reason, ''), '|', 1)) IN ('opportunita','affidabile','da_verificare',"
            "'incidentata')") in scovo.LIST_SQL
    assert set(scovo.MOTIVI_ELENCO) == {"opportunita", "affidabile", "da_verificare", "incidentata"}


def test_item_incidentata_e_verificare_con_note():
    from dealfinder.web import scovo
    it = scovo.item(_riga(1, "incidentata | scomparso (404)"))
    assert it["incidentata"] is True and it["verificare"] is False
    it = scovo.item(_riga(2, "da_verificare | scomparso (rimosso)"))
    assert it["verificare"] is True and it["incidentata"] is False
    it = scovo.item(_riga(3, "opportunita"))
    assert it["verificare"] is False and it["incidentata"] is False


def test_list_sql_postgres_vero():
    """Su un Postgres vero: entrano le auto con il motivo giusto anche se seguito da note e le 'affidabile'; restano
    fuori le altre (non convenienti, scartate, motivo vuoto con la sola nota, non fresche, non attive)."""
    from tests import pg_vero
    from dealfinder.web import scovo
    c = pg_vero.nuovo_db("notte09_lista")
    if c is None:
        return
    pg_vero.psql(c.d, c.db, """
        CREATE TABLE listings (id int PRIMARY KEY, source text, url text, title text, make text, model text,
          version_raw text, year int, mileage_km int, fuel text, gearbox text, power_kw int, city text, province text,
          price_eur int, damage_class text, stage text, stage_reason text, status text, first_seen_at timestamptz,
          deep_at timestamptz, last_checked_at timestamptz, last_seen_at timestamptz);
        CREATE TABLE valuations (listing_id int, created_at timestamptz DEFAULT now(), private_median int,
          resale_median_private int, resale_median int, parts_cost_low int, parts_cost_high int, n_comparables int,
          confidence text, engine_version text);
        CREATE TABLE valutazioni_policy (listing_id int, computed_at timestamptz DEFAULT now(), riassunto jsonb);
        CREATE TABLE top_auto (listing_id int);
        CREATE TABLE listing_opens (listing_id int, dealer_id int);
        CREATE TABLE listing_photos (listing_id int);
        CREATE TABLE price_events (id bigserial PRIMARY KEY, listing_id int, event text, price_eur int,
          at timestamptz NOT NULL DEFAULT now());
    """)
    casi = [(1, "opportunita", "attivo", "1 hour"), (2, "opportunita | scomparso (404)", "attivo", "1 hour"),
            (3, "affidabile", "attivo", "1 hour"), (4, "incidentata | venduto sito", "attivo", "1 hour"),
            (5, "da_verificare", "attivo", "1 hour"), (6, "non_conveniente", "attivo", "1 hour"),
            (7, " | scomparso", "attivo", "1 hour"), (8, "opportunita", "attivo", "5 days"),
            (9, "opportunita | scomparso (404)", "scomparso", "1 hour"), (10, "opportunitaXX", "attivo", "1 hour")]
    for i, motivo, stato, fa in casi:
        c.execute("INSERT INTO listings (id, source, stage, stage_reason, status, price_eur, last_checked_at, last_seen_at) "
                  "VALUES (%s, 'subito', 'approfondito', %s, %s, 7000, now() - %s::interval, now() - %s::interval)",
                  (i, motivo, stato, fa, fa))
        c.execute("INSERT INTO valuations (listing_id, private_median) VALUES (%s, 10000)", (i,))
    c.execute("INSERT INTO listings (id, source, stage, stage_reason, status, price_eur, last_checked_at) "
              "VALUES (11, 'subito', 'candidato', 'opportunita', 'attivo', 7000, now())")
    c.execute("INSERT INTO valuations (listing_id, private_median) VALUES (11, 10000)")
    ids = sorted(r["id"] for r in c.execute(scovo.LIST_SQL, {"me": 0}).fetchall())
    assert ids == [1, 2, 3, 4, 5]


def _it(i, fonte, prezzo=8000, km=60000, prov="MI", tel=True, foto=5, **kw):
    d = {"id": i, "fonte": fonte, "marca": "Fiat", "modello": "500", "anno": 2018, "km": km, "prezzo": prezzo,
         "n_foto": foto, "zona": f"Milano ({prov})", "pv": {"margine": 2000}, "_r": {"provincia": prov, "tel": tel}}
    d.update(kw)
    return d


def test_doppioni_si_tiene_il_privato():
    from dealfinder.web import scovo
    comm = _it(1, "AutoScout24", foto=15, _tipo="comm_lontano", commerciante=True,
               _r={"provincia": "MI", "tel": True, "seller_type": "commerciante"})
    priv = _it(2, "Subito", foto=4, _tipo="privato", _r={"provincia": "MI", "tel": True, "seller_type": "privato"})
    out, via = scovo.togli_doppioni([comm, priv])
    assert via == {1: 2} and [it["id"] for it in out] == [2]
    # il numero conta ancora prima: un privato senza numero non vince su un commerciante col numero
    priv_senza = dict(priv, _r={"provincia": "MI", "tel": False, "seller_type": "privato"})
    assert scovo.togli_doppioni([comm, priv_senza])[1] == {2: 1}
    # due privati: come prima (più foto)
    assert scovo.togli_doppioni([_it(1, "Subito", foto=3), _it(2, "AutoScout24", foto=9)])[1] == {1: 2}


def test_doppioni_potenza_dal_caricamento():
    from dealfinder.web import scovo
    a = _it(1, "Subito", _r={"provincia": "MI", "tel": True, "kw": 51})
    b = _it(2, "AutoScout24", _r={"provincia": "MI", "tel": True, "kw": 74})
    assert not scovo.stessa_auto_due_siti(a, b)                  # motori diversi: due auto
    assert scovo.togli_doppioni([a, b]) == ([a, b], {})
    b2 = dict(b, _r={"provincia": "MI", "tel": True, "kw": 52})
    assert scovo.stessa_auto_due_siti(a, b2)                     # entro il 5%: la stessa auto
    b3 = dict(b, _r={"provincia": "MI", "tel": True})
    assert scovo.stessa_auto_due_siti(a, b3)                     # potenza mancante: non separa (come prima)


def test_provincia_dai_comuni_dei_lead():
    from dealfinder.web import sito_regole as SR
    assert SR.provincia_di(None, "Besate") == "MI"
    assert SR.provincia_di("", "Motta Visconti") == "MI"
    # una sigla dell'annuncio vale sempre; i comuni già noti non cambiano
    assert SR.provincia_di("PV", "Besate") == "PV"
    assert SR.provincia_di(None, "Milano") == "MI" and SR.provincia_di(None, "Brugherio") == "MB"
    assert SR.provincia_di(None, "Comune Inesistente") is None


def test_provincia_sconosciuta_sempre_nel_funnel():
    from dealfinder.web import sito_regole as SR
    items = []
    for k, p in enumerate(["GE", "PR", "RE", "VR", "VI", "MO", "TN", "BZ", "FI", "UD"]):  # 27/11: AL ora nelle province del sito; 27/11: Lombardia intera, decisione del cliente (MN e SO dentro)
        for j in range(3):
            items.append({"id": 100 * k + j, "_r": {"provincia": p}})
    items.append({"id": 9999, "_r": {"provincia": None}})
    fun = SR.Funnel()
    dentro = SR._applica(items, "zona", SR.r_zona, SR.Regole(fascia_fuori_max=0), fun)
    assert dentro == []
    assert fun.passi[-1]["motivi"]["provincia_sconosciuta"] == 1
    assert fun.passi[-1]["uscite"] == len(items)
