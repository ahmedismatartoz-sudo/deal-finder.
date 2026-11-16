"""16/11 (fondamenta): test del comportamento ATTUALE di web/controllo_foto.py e web/vendute.py, senza database
(connessioni finte). Non cambiano nulla."""
from __future__ import annotations

import io
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from dealfinder.web import controllo_foto as CF
from dealfinder.web import vendute as V


def _jpeg(colore=(200, 10, 10), dim=(640, 480)) -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", dim, colore).save(b, "JPEG")
    return b.getvalue()


class _Ris:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return list(self.righe)

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Conn:
    """Connessione finta: risposte per frammento di SQL, registro delle query."""

    def __init__(self, risposte=None):
        self.risposte = risposte or {}
        self.sql = []
        self.commit_n = 0

    def execute(self, sql, p=None):
        self.sql.append((sql, p))
        for k, v in self.risposte.items():
            if k in sql:
                return _Ris(v(p) if callable(v) else v)
        return _Ris([])

    def commit(self):
        self.commit_n += 1


# ------------------------------------------------------------------ controllo_foto
def test_foglio_griglia_jpeg():
    from PIL import Image
    out = CF._foglio([_jpeg(), b"non un'immagine", _jpeg((0, 200, 0))], "123 Fiat Panda 2015 4000e")
    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG" and im.size == (CF.CELLA[0] * CF.COLONNE, CF.CELLA[1] * CF.RIGHE + 22)
    assert CF._foglio([b"rotta"], "x") is None
    assert CF._foglio([], "x") is None


class _Raccogli(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.righe = []

    def emit(self, record):
        self.righe.append(record.getMessage())


def test_run_scrive_meta_e_pezzi(monkeypatch):
    from dealfinder.web import foto, scovo
    riga = {"id": 7, "opened_by_me": False, "opens": 0, "n_photos": 2, "url": "https://example.invalid/7"}
    conn = Conn({scovo.LIST_SQL[:40]: [riga, dict(riga, id=8, opens=999)], "SELECT description": [{"description": "Ottima"}]})
    monkeypatch.setattr(scovo, "item", lambda r: {"nome": "Fiat Panda", "anno": 2015, "km": 90000, "prezzo": 4000,
                                                  "mercato": 6000, "danno": "nessuno", "rip_lo": 0, "rip_hi": 0,
                                                  "fonte": "Subito"})
    monkeypatch.setattr(scovo, "abbastanza", lambda it: True)
    monkeypatch.setattr(foto, "fetch_one", lambda conn, lid, pos: _jpeg())
    monkeypatch.setattr(CF, "PEZZO", 500)
    h = _Raccogli()
    CF.log.addHandler(h)
    try:
        assert CF.run(conn) == {"auto": 1}                  # la seconda è nascosta (troppe aperture)
    finally:
        CF.log.removeHandler(h)
    righe = h.righe
    meta = json.loads(next(r for r in righe if r.startswith("FOTOMETA "))[9:])
    assert meta["id"] == 7 and meta["n_foto"] == 2 and meta["descrizione"] == "Ottima"
    pezzi = [r for r in righe if r.startswith("FOTOPEZZO 7 ")]
    assert len(pezzi) > 1 and pezzi[0].split()[2] == f"1/{len(pezzi)}"
    assert righe[-1] == "FOTOFINE 1 auto"


def test_once_una_sola_volta_per_versione(monkeypatch):
    import dealfinder.db as DB
    fatti = []

    def connetti_con(gia_fatto):
        @contextmanager
        def c():
            yield Conn({"FROM job_runs WHERE job=%s AND ok": [{"x": 1}] if gia_fatto else []})
        return c
    monkeypatch.setattr(CF, "run", lambda conn: fatti.append(1) or {"auto": 0})
    monkeypatch.setattr(DB, "log_job", lambda conn, job: (lambda ok, st: fatti.append((job, ok))))
    monkeypatch.setattr(DB, "connect", connetti_con(True))
    CF.once()
    assert fatti == []
    monkeypatch.setattr(DB, "connect", connetti_con(False))
    CF.once()
    assert fatti == [1, (f"controllo_foto:{CF.VERSIONE}", True)]


# ------------------------------------------------------------------ vendute
def test_provincia_e_giorni():
    assert V._provincia("Monza (mb)") == "MB"
    assert V._provincia("Milano") is None and V._provincia(None) is None
    a = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert V._giorni(a, a + timedelta(days=3, hours=13)) == 4
    assert V._giorni(a, a + timedelta(hours=2)) == 1          # almeno un giorno
    assert V._giorni(None, a) is None


def test_zona_senza_roma(monkeypatch):
    monkeypatch.setenv("ZONA_SITO", "mi, RM ,bg")
    assert V._zona() == ["MI", "BG"]
    monkeypatch.delenv("ZONA_SITO")
    z = V._zona()
    assert "MI" in z and "RA" in z and "RM" not in z and z == sorted(z)


def test_salva_sito_solo_privati_con_margine(monkeypatch):
    conn = Conn()

    @contextmanager
    def connetti():
        yield conn
    monkeypatch.setattr(V, "connect", connetti)
    monkeypatch.setitem(V._ultimo_salvataggio, "t", 0.0)
    items = [{"id": 1, "prezzo": 5000, "prezzo_annuncio": 5500, "zona": "Milano (MI)", "pv": {"margine": 1800, "M": 7500}},
             {"id": 2, "prezzo": 5000, "commerciante": True, "pv": {"margine": 1800}},
             {"id": 3, "prezzo": 5000, "pv": {}},
             {"id": 4, "prezzo": None, "pv": {"margine": 100}}]
    V.salva_sito(items)
    ins = [p for s, p in conn.sql if "INSERT INTO sito_storico" in s]
    assert len(ins) == 1 and ins[0][0] == [1]
    d = json.loads(ins[0][1][0])
    assert d["prezzo"] == 5500 and d["margine"] == 1800 and d["prov"] == "MI" and d["mercato"] == 7500
    n = len(conn.sql)
    V.salva_sito(items)                                       # entro 10 minuti: niente
    assert len(conn.sql) == n


def test_calcola_fasce_e_niente_id(monkeypatch):
    ora = datetime(2026, 11, 16, tzinfo=timezone.utc)
    sito = [{"id": i, "dati": {"nome": f"Auto {i}", "prov": "MI", "prezzo": p, "margine": m},
             "first_seen_at": ora - timedelta(days=5), "disappeared_at": ora, "n_foto": 1}
            for i, (p, m) in enumerate([(2000, 1500), (2500, 1600), (8000, 2500), (40000, 6000)], 1)]
    sito.append({"id": 9, "dati": {"nome": "Fuori zona", "prov": "RM", "prezzo": 5000, "margine": 3000},
                 "first_seen_at": ora, "disappeared_at": ora, "n_foto": 0})
    conn = Conn({"FROM sito_storico s JOIN": sito, "SELECT count(*) AS sparite": [{"sparite": 0}],
                 "GROUP BY 1": [], "vp.r AS pv": []})

    @contextmanager
    def connetti():
        yield conn
    monkeypatch.setattr(V, "connect", connetti)
    monkeypatch.setitem(V._diag_fatta, "ok", True)
    monkeypatch.setenv("VENDUTE_QUANTE", "3")
    monkeypatch.setenv("ZONA_SITO", "MI")
    out = V._calcola()
    assert out["totale_sparite"] == 4 and out["giorni"] == 7
    # una per fascia a turno (0-3000, 6000-10000, 25000-50000), poi in ordine di margine
    assert [a["margine"] for a in out["items"]] == [6000, 2500, 1600]
    assert all("id" not in a and a["origine"] == "sito" and a["giorni"] == 5 for a in out["items"])
    assert out["margine_totale"] == 10100
