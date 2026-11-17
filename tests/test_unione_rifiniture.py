"""17/11 (unione, giudice finale): rifiniture.
(a) le query di manuale, allarmi, schede_auto e pulizia usano tutti i motivi dell'elenco (scovo.MOTIVI_ELENCO, anche
    'affidabile') con il taglio delle note (motivo_stadio_sql);
(b) valore_reale.pulisci e policy_v2 escludono il motivo BASE: anche "truffa | nota" resta fuori;
(c) tempo_reale._as_completa: un annuncio AutoScout24 già 'scomparso' nel database con la pagina in errore (429, 403,
    rete) non si salva (non torna 'riapparso')."""
from __future__ import annotations

import inspect
from types import SimpleNamespace


# ---------------------------------------------------------------- (a)

def test_motivo_elenco_sql_tutti_i_motivi():
    from dealfinder.web import scovo
    q = scovo.motivo_elenco_sql("stage_reason")
    assert q.startswith(scovo.motivo_stadio_sql("stage_reason"))
    for m in scovo.MOTIVI_ELENCO:
        assert f"'{m}'" in q
    assert "'affidabile'" in q


def test_query_usano_motivi_elenco():
    from dealfinder.pipeline import collect, manuale, schede_auto
    from dealfinder.web import app
    assert "'affidabile'" in schede_auto.DA_LEGGERE_SQL
    for f in (manuale.segna_viste_di_recente, app.alarms):
        src = inspect.getsource(f)
        assert "motivo_elenco_sql" in src and "('opportunita','da_verificare','incidentata')" not in src
    src = inspect.getsource(collect)
    assert "IN ('opportunita','da_verificare','incidentata')" not in src and "_mel('stage_reason')" in src


def test_segna_viste_conta_affidabile():
    from dealfinder.pipeline import manuale
    sql = []

    class C:
        def execute(self, q, p=None):
            sql.append(q)
            return SimpleNamespace(rowcount=0, fetchall=lambda: [])

        def commit(self):
            pass
    manuale.segna_viste_di_recente(C())
    assert sum("'affidabile'" in q for q in sql) == 2


def test_motivo_elenco_sql_postgres_vero():
    from tests import pg_vero
    from dealfinder.web import scovo
    c = pg_vero.nuovo_db("unione_rifiniture")
    if c is None:
        return
    pg_vero.psql(c.d, c.db, "CREATE TABLE listings (id int PRIMARY KEY, stage_reason text);")
    for i, m in [(1, "affidabile"), (2, "affidabile | scomparso (404)"), (3, "opportunita"), (4, "non_conveniente"),
                 (5, None), (6, " | affidabile"), (7, "incidentata|x")]:
        c.execute("INSERT INTO listings (id, stage_reason) VALUES (%s, %s)", (i, m))
    ids = sorted(r["id"] for r in c.execute(
        "SELECT id FROM listings WHERE " + scovo.motivo_elenco_sql("stage_reason")).fetchall())
    assert ids == [1, 2, 3, 7]


# ---------------------------------------------------------------- (b)

def test_valore_reale_motivo_base():
    from dealfinder.pricing import valore_reale as VR
    base = {"damage_class": "nessuno", "price_flags": [], "year": 2015, "mileage_km": 90000}
    assert VR.pulisci({**base, "stage_reason": None})
    assert VR.pulisci({**base, "stage_reason": "opportunita | truffa?"})
    for m in ("truffa", "truffa | foto internet", "doppione|x", " prezzo_finto | y"):
        assert not VR.pulisci({**base, "stage_reason": m}), m


def test_policy_v2_motivo_base():
    from dealfinder.pricing import policy_v2 as PV
    t = {"id": 1, "make": "Fiat", "model": "Panda", "year": 2018, "mileage_km": 60000, "price_eur": 7000}
    merc = [{"id": 10 + i, "make": "Fiat", "model": "Panda", "year": 2017, "mileage_km": 70000 + i * 1000,
             "price_eur": 8000 + i * 100, "status": "attivo", "stage_reason": m}
            for i, m in enumerate(["truffa | foto internet", "doppione|x", " prezzo_finto ", "truffa", None,
                                   "opportunita | nota"])]
    _tenuti, esclusi = PV.pulisci(t, merc, PV.PolicyConfig())
    motivi = {e["id"]: e["motivo"] for e in esclusi}
    assert motivi.get(10) == "annuncio sospetto" and motivi.get(13) == "annuncio sospetto"
    assert motivi.get(11) == "doppione su un altro portale" and motivi.get(12) == "prezzo finto"
    # 14 e 15 (motivo vuoto, "opportunita | nota") non escono per il motivo dello stadio
    assert motivi.get(14) not in ("annuncio sospetto", "doppione su un altro portale", "prezzo finto")
    assert motivi.get(15) not in ("annuncio sospetto", "doppione su un altro portale", "prezzo finto")


# ---------------------------------------------------------------- (c)

class _Client:
    """Pagina dell'annuncio: codice per id (200 = pagina valida)."""

    def __init__(self, codici):
        self.codici, self.chieste = codici, []

    def get(self, url, *a, **k):
        self.chieste.append(url)
        sid = url.rsplit("/", 1)[-1]
        code = self.codici.get(sid, 200)
        if code == "eccezione":
            raise TimeoutError("timeout")
        return SimpleNamespace(status_code=code, url=url, text=sid)


def _prepara(monkeypatch):
    from dealfinder.pipeline import tempo_reale as T
    from dealfinder.collectors import autoscout, base
    monkeypatch.setattr(base, "next_data", lambda text: {"props": {"pageProps": {"listingDetails": {"id": text}}}})
    monkeypatch.setattr(autoscout, "parse_item", lambda det: None)
    monkeypatch.setattr(T, "_diagnostica_as", lambda det: None)
    monkeypatch.setattr(T, "telefono_autoscout", lambda det: None)
    monkeypatch.setenv("TR_AS_PAUSA_S", "0")
    T.FERMA.clear()
    T._AS_VISTI.clear()
    T._AS_SCOMPARSI_DB.clear()
    return T


def _l(sid):
    return SimpleNamespace(source_id=sid, url=f"https://www.autoscout24.it/annunci/{sid}", photos=[], seller_type=None)


def test_scomparso_nel_db_con_errore_non_si_salva(monkeypatch):
    T = _prepara(monkeypatch)

    class Conn:
        def execute(self, sql, params=None):
            st = {"s1": "scomparso", "s2": "scomparso", "s3": "scomparso", "s4": "scomparso", "a1": "attivo"}
            return SimpleNamespace(fetchall=lambda: [{"source_id": i, "status": st[i]} for i in params[0] if i in st])
    assert T._as_noti(Conn(), ["s1", "s2", "s3", "s4", "a1", "n1"]) == {"a1"}
    assert T._AS_SCOMPARSI_DB == {"s1", "s2", "s3", "s4"}
    # s1 429, s2 403, s3 timeout: nessuna prova di vita -> fuori; s4 pagina valida -> torna; n1 nuovo con 429 -> come prima
    client = _Client({"s1": 429, "s2": 403, "s3": "eccezione", "n1": 429})
    out = T._as_completa(client, [_l("s1"), _l("s2"), _l("s3"), _l("s4"), _l("n1")])
    assert [l.source_id for l in out] == ["s4", "n1"]
    assert not ({"s1", "s2", "s3"} & T._AS_VISTI)                 # non segnati visti: si riprova a un giro dopo
    assert T._AS_SCOMPARSI_DB == {"s1", "s2", "s3"}                 # s4 salvato di nuovo: non più scomparso


def test_scomparso_nel_db_con_scritta_di_rimozione_non_si_salva(monkeypatch):
    T = _prepara(monkeypatch)
    T._AS_SCOMPARSI_DB.add("s9")

    class C(_Client):
        def get(self, url, *a, **k):
            r = super().get(url)
            r.text = "<p>Questo annuncio è stato rimosso</p>"
            return r
    # pagina con listingDetails ma con la scritta di rimozione fuori dai dati: senza prova, non si salva
    monkeypatch.setattr(T, "_AS_TOLTO", ("rimosso",))
    out = T._as_completa(C({}), [_l("s9")])
    assert out == []
