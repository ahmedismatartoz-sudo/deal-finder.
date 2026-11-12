"""12/11 buchi della raccolta (§2.3): controllo notturno degli annunci Subito spariti (0 controllati ogni notte).

Dai log del cron mercato (11/11 01:27): 15 pagine Subito, tutte "HTTP/1.1 200 OK" sullo stesso indirizzo (nessun
rimando), e tutte "errore": la pagina non ha più __NEXT_DATA__ (Subito usa il JSON escapato di self.__next_f), quindi
"pagina con i dati dell'annuncio" non veniva mai riconosciuta."""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.pipeline import tempo_reale as tr     # noqa: E402

URL = "https://www.subito.it/auto/porsche-cayenne-coupe-2-9-v6-s-milano-666938516.htm"
APP_ROUTER = ('<html><body><script>self.__next_f.push([1,"{\\"ad\\":{\\"urn\\":\\"id:ad:666938516:list:1\\",'
              '\\"subject\\":\\"Porsche Cayenne\\",\\"features\\":[]}}"])</script></body></html>')


class _R:
    def __init__(self, code, text, url=URL):
        self.status_code, self.text, self.url = code, text, url


class Cl:
    def __init__(self, r):
        self.r = r

    def get(self, url):
        return self.r


def test_pagina_app_router_vale_attivo():
    assert tr.subito_esito_con_prova(Cl(_R(200, APP_ROUTER)), URL) == ("attivo", None)


def test_pagina_di_un_altro_annuncio_non_basta():
    altro = APP_ROUTER.replace("666938516", "123456789")
    assert tr.subito_esito_con_prova(Cl(_R(200, altro)), URL) == ("errore", None)


def test_scritte_di_rimozione_restano_prova():
    testo = "<html>Questo annuncio è scaduto</html>"
    assert tr.subito_esito_con_prova(Cl(_R(200, testo)), URL) == ("scomparso", "scaduto")
    assert tr.subito_esito_con_prova(Cl(_R(404, "")), URL) == ("scomparso", "404")
    assert tr.subito_esito_con_prova(Cl(_R(200, "<html></html>", url="https://www.subito.it/annunci-lombardia/")),
                                      URL) == ("scomparso", "reindirizzato")


def test_pagina_vuota_resta_errore_e_niente_marcatura():
    assert tr.subito_esito_con_prova(Cl(_R(200, "<html>blocco</html>")), URL) == ("errore", None)


def test_scomparsi_con_prova_non_si_ferma_piu_sulle_pagine_buone():
    from dealfinder.pipeline import verify as V
    segnati = []

    class Conn:
        def execute(self, sql, params=None):
            class R:
                def fetchall(s):
                    return [{"id": i, "url": URL, "price_eur": 1000} for i in range(30)]
            return R()

        def commit(self):
            pass

        def rollback(self):
            pass
    vecchio = V._mark
    try:
        V._mark = lambda conn, lid, st, price=None, motivo=None, sito=False: segnati.append((lid, st))
        cl = Cl(_R(200, APP_ROUTER))
        st = V.scomparsi_con_prova(Conn(), "subito", stato=lambda u: tr.subito_esito_con_prova(cl, u), pausa=None, zona=["MI"])
    finally:
        V._mark = vecchio
    assert st["attivo"] == 30 and not st.get("fermato_errori") and len(segnati) == 30
    assert isinstance(st, Counter)
