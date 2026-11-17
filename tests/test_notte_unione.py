"""17/11 (unione dei rami della notte): i pezzi venuti da rami diversi devono leggere il motivo dello stadio allo
stesso modo e non pestarsi i nomi."""
from __future__ import annotations


def test_promuovi_sql_usa_motivo_stadio_sql():
    from dealfinder.pricing import valutazione as V
    from dealfinder.web import scovo
    assert V._MOTIVO_SQL == scovo.motivo_stadio_sql("stage_reason")
    assert "' |'" not in V.PROMUOVI_SQL
    assert V.NON_PROPOSTE == ("mercato_nord", "fuori_regole")


def test_nessun_taglio_con_spazio_nel_motivo():
    """Nessuna query taglia più il motivo a ' |' (con lo spazio): "truffa|x" deve restare truffa ovunque."""
    import glob
    import os
    qui = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    trovati = []
    for p in glob.glob(os.path.join(qui, "dealfinder", "**", "*.py"), recursive=True):
        with open(p, encoding="utf-8") as f:
            if "''), ' |', 1)" in f.read():
                trovati.append(os.path.relpath(p, qui))
    assert not trovati, trovati


def test_motivo_stadio_senza_spazio():
    from dealfinder.web import scovo
    assert scovo.motivo_stadio("truffa|scomparso") == "truffa"
    assert scovo.motivo_stadio("opportunita | scomparso (404)") == "opportunita"


def test_due_prezzo_dopo_valutazione_non_collidono():
    """notte-02 (rinfresco, più severa: basta un evento) e notte-09 (sito) hanno nomi diversi."""
    from dealfinder.pipeline import tempo_reale as tr
    from dealfinder.web import scovo
    assert not hasattr(tr, "PREZZO_DOPO_VALUTAZIONE_SQL")
    assert tr.PREZZO_DOPO_VALUTAZIONE_RINFRESCO_SQL != scovo.PREZZO_DOPO_VALUTAZIONE_SQL
    assert "'riapparso'" in tr.PREZZO_DOPO_VALUTAZIONE_RINFRESCO_SQL
    assert tr.PREZZO_DOPO_VALUTAZIONE_RINFRESCO_SQL in tr.RINFRESCO_SQL
    assert tr.PREZZO_DOPO_VALUTAZIONE_RINFRESCO_SQL not in scovo.LIST_SQL
    assert scovo.PREZZO_DOPO_VALUTAZIONE_SQL in scovo.LIST_SQL
    # stessa base dell'elenco del sito
    assert tr.motivo_stadio_sql is scovo.motivo_stadio_sql
    assert tr._BASE_FRESCHEZZA in scovo.LIST_SQL.replace("\n  AND ", " AND ").replace("WHERE ", "")
