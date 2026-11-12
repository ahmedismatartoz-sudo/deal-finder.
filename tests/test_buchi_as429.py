"""12/11 buchi della raccolta (§3.4): AutoScout24 nel tempo reale, 429 ed errori temporanei: riprova e ripresa."""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.pipeline import as_riprova as AR        # noqa: E402


class _R:
    def __init__(self, code, text="", url="x"):
        self.status_code, self.text, self.url = code, text, url


class Client:
    def __init__(self, codici):
        self.codici, self.n = list(codici), 0

    def get(self, url):
        self.n += 1
        c = self.codici.pop(0) if self.codici else 200
        if c == "rete":
            raise RuntimeError("connessione chiusa")
        return _R(c)


def _azzera():
    AR._stato["fermo_fino"] = 0.0


def test_riprova_con_attesa_crescente():
    _azzera()
    dormite = []
    r = AR.get(Client([429, 503, "rete", 200]), "u", "prova", dormi=dormite.append)
    assert r.status_code == 200 and dormite == [15, 45, 90]
    assert not AR.fermo()


def test_429_che_resta_mette_in_pausa():
    _azzera()
    cl = Client([429] * 10)
    assert AR.get(cl, "u", "prova", dormi=lambda s: None) is None and cl.n == 4
    assert AR.fermo()
    cl2 = Client([200])
    assert AR.get(cl2, "u", "prova", dormi=lambda s: None) is None and cl2.n == 0      # in pausa: nessuna richiesta
    _azzera()


def test_404_non_si_riprova():
    _azzera()
    cl = Client([404])
    assert AR.get(cl, "u", "prova", dormi=lambda s: None).status_code == 404 and cl.n == 1


def test_ordine_fasce_dalla_ripresa():
    assert AR.ordine_fasce(4, None) == [(0, 1), (1, 1), (2, 1), (3, 1)]
    assert AR.ordine_fasce(4, {"fascia": 2, "pagina": 5}) == [(2, 5), (3, 1)]          # solo le mancanti
    assert AR.ordine_fasce(4, {"fascia": 9, "pagina": 1}) == [(0, 1), (1, 1), (2, 1), (3, 1)]


class SitoAS:
    """Ricerca AutoScout24 finta: ogni fascia ha 3 pagine (fuori zona: niente pagina di dettaglio). Un 429 fisso su una
    fascia e pagina finché `blocca` è vero."""

    def __init__(self, blocca=None):
        self.blocca, self.richieste = blocca, []

    def get(self, url):
        q = dict(re.findall(r"[?&](\w+)=([^&]*)", url))
        lo, page = int(q["pricefrom"]), int(q["page"])
        self.richieste.append((lo, page))
        if self.blocca and self.blocca == (lo, page):
            return _R(429)
        n = 20 if page < 3 else 5
        items = [{"id": f"{lo}-{page}-{i}", "url": f"/annunci/{lo}-{page}-{i}", "vehicle": {"make": "Fiat", "model": "Panda"},
                  "tracking": {"price": str(lo + i)}, "location": {"zip": "00100"}, "seller": {"type": "Private"}}
                 for i in range(n)]
        return _R(200, '<script id="__NEXT_DATA__" type="application/json">' +
                  json.dumps({"props": {"pageProps": {"listings": items}}}) + "</script>")


class Conn:
    def __init__(self):
        self.ripresa = None

    def execute(self, sql, params=None):
        class Ris:
            def __init__(s, r):
                s.r = r

            def fetchone(s):
                return s.r[0] if s.r else None

            def fetchall(s):
                return s.r
        if "giro='as_ripresa'" in sql and sql.startswith("SELECT"):
            return Ris([{"stats": self.ripresa}] if self.ripresa else [])
        if "INSERT INTO tempo_reale" in sql and "as_ripresa" in sql:
            self.ripresa = json.loads(params[0])
            return Ris([])
        if sql.startswith("DELETE FROM tempo_reale"):
            self.ripresa = None
            return Ris([])
        if "FROM listings" in sql:
            return Ris([])
        raise AssertionError(sql)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_lettura_completa_riprende_dalla_fascia_interrotta():
    from dealfinder.pipeline import tempo_reale as tr
    _azzera()
    vecchi = {k: os.environ.get(k) for k in ("TR_AS_PAUSA_S", "TR_AS_ATTESE")}
    vecchio_client = tr._client_as
    try:
        os.environ["TR_AS_PAUSA_S"], os.environ["TR_AS_ATTESE"] = "0", "0,0"
        conn = Conn()
        sito = SitoAS(blocca=(7000, 2))
        tr._client_as = lambda: sito
        tr._autoscout_tutti(conn)
        assert conn.ripresa == {"fascia": 4, "pagina": 2} and AR.fermo()
        assert (9500, 1) not in sito.richieste                   # dopo il 429 non si martella il sito
        _azzera()
        sito2 = SitoAS()
        tr._client_as = lambda: sito2
        tr._autoscout_tutti(conn)
        assert sito2.richieste[0] == (7000, 2)                   # si riparte dalla fascia e pagina interrotte
        assert {lo for lo, _ in sito2.richieste} == {lo for lo, _ in tr.FASCE_AS[4:]}  # poi solo le mancanti
        assert conn.ripresa is None
    finally:
        tr._client_as = vecchio_client
        _azzera()
        for k, v in vecchi.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
