"""4/11: ruolo dal database, IP vero, niente password nei log, chiave del CSV fuori dall'indirizzo, voce degli ospiti,
PLATE_SALT di esempio rifiutato, abbonamento per assistente e servizi."""
import asyncio
import logging

from starlette.requests import Request

from dealfinder.web import app as A
from dealfinder.web import sessions


def _req(headers=None, client="10.0.0.5", query=b"", path="/", method="GET", body=b"{}", path_params=None):
    scope = {"type": "http", "method": method, "path": path, "query_string": query,
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
             "client": (client, 1234), "path_params": path_params or {}}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request(scope, receive)


class _Patch:
    """Sostituisce attributi di modulo e li rimette com'erano (run_tests.py non ha monkeypatch)."""

    def __init__(self):
        self.vecchi = []

    def set(self, obj, nome, val):
        self.vecchi.append((obj, nome, getattr(obj, nome)))
        setattr(obj, nome, val)

    def undo(self):
        for obj, nome, val in reversed(self.vecchi):
            setattr(obj, nome, val)


def _errore(fn, *a):
    try:
        r = fn(*a)
        if asyncio.iscoroutine(r):
            asyncio.run(r)
    except A.HTTPError as e:
        return e.status
    return None


# ---------- IP ----------
def test_client_ip_intestazioni_del_proxy():
    assert A.client_ip(_req({"CF-Connecting-IP": "93.184.216.34", "X-Forwarded-For": "1.1.1.1"})) == "93.184.216.34"
    assert A.client_ip(_req({"True-Client-IP": "93.184.216.35"})) == "93.184.216.35"
    assert A.client_ip(_req({"X-Real-IP": "93.184.216.36", "X-Forwarded-For": "8.8.8.8"})) == "93.184.216.36"


def test_client_ip_forwarded_salta_privati_e_spazzatura():
    assert A.client_ip(_req({"X-Forwarded-For": "10.1.2.3, 192.168.0.1, 93.184.216.34, 8.8.8.8"})) == "93.184.216.34"
    assert A.client_ip(_req({"X-Forwarded-For": "pippo, 127.0.0.1, 2001:4860:4860::8888"})) == "2001:4860:4860::8888"
    assert A.client_ip(_req({"X-Forwarded-For": "93.184.216.34:5555"})) == "93.184.216.34"
    # intestazione del proxy non valida o privata: non vale, si passa alle altre
    assert A.client_ip(_req({"CF-Connecting-IP": "x' OR 1=1", "X-Forwarded-For": "8.8.4.4"})) == "8.8.4.4"
    assert A.client_ip(_req({"X-Real-IP": "10.0.0.1"}, client="10.9.9.9")) == "10.9.9.9"
    assert A.client_ip(_req({})) == "10.0.0.5"


# ---------- ruolo ----------
def test_ruolo_admin_dal_database_non_dal_token():
    P = _Patch()
    try:
        P.set(A, "read_token", lambda t: {"id": 5, "role": "admin", "sid": "s1"})
        P.set(sessions, "check", lambda sid, did: (True, None))
        P.set(sessions, "ruolo", lambda sid: "commerciante")
        P.set(sessions, "ruolo_fresco", lambda did: "commerciante")        # declassato nel database
        r = _req({"Authorization": "Bearer x"})
        assert _errore(A.current_user, r, True) == 403
        assert A.current_user(r)["role"] == "commerciante"
        P.set(sessions, "ruolo_fresco", lambda did: "admin")
        assert A.current_user(r, True)["role"] == "admin"
        P.set(sessions, "ruolo_fresco", lambda did: None)                  # spento o cancellato
        assert _errore(A.current_user, r, True) == 403
    finally:
        P.undo()


def test_dimentica_account_svuota_la_memoria():
    sessions._cache["a"] = (0, {"dealer_id": 9, "role": "admin"})
    sessions._cache["b"] = (0, {"dealer_id": 8, "role": "admin"})
    sessions.dimentica_account(9)
    assert "a" not in sessions._cache and "b" in sessions._cache
    assert sessions.ruolo("b") == "admin"
    sessions._cache.clear()


# ---------- password nei log ----------
class _ConnVuota:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a):
        class R:
            def fetchone(self):
                return None
        return R()


def test_accesso_fallito_senza_dati_della_password():
    P = _Patch()
    righe = []

    class H(logging.Handler):
        def emit(self, rec):
            righe.append(rec.getMessage())
    h = H()
    A.log.addHandler(h)
    try:
        P.set(A, "connect", lambda: _ConnVuota())
        A._attempts.clear()
        r = _req({"X-Forwarded-For": "93.184.216.99"}, method="POST",
                 body=b'{"email": "mario.rossi@esempio.it", "password": "Segreta123!"}')
        assert _errore(A.login, r) == 401
        testo = " ".join(righe)
        assert "ACCESSO_FALLITO" in testo
        for vietato in ("lunghezza", "Segreta", "uguale_a", "prima_differenza", "maiuscole", "mario.rossi"):
            assert vietato not in testo, vietato
        assert "m***@esempio.it" in testo
        assert not hasattr(A, "_diagnosi_password")
    finally:
        A.log.removeHandler(h)
        A._attempts.clear()
        P.undo()


# ---------- CSV ----------
def test_chiave_csv_solo_in_intestazione():
    import os
    vecchia = os.environ.get("COMMERCIANTI_KEY")
    os.environ["COMMERCIANTI_KEY"] = "chiave-lunga-di-prova"
    try:
        assert not A._chiave_export_ok(_req(query=b"k=chiave-lunga-di-prova"))
        assert A._chiave_export_ok(_req({"X-Chiave": "chiave-lunga-di-prova"}))
        assert not A._chiave_export_ok(_req({"X-Chiave": "sbagliata"}))
        assert A.commercianti_tel(_req(query=b"k=chiave-lunga-di-prova")).status_code == 404
    finally:
        if vecchia is None:
            os.environ.pop("COMMERCIANTI_KEY", None)
        else:
            os.environ["COMMERCIANTI_KEY"] = vecchia


# ---------- PLATE_SALT ----------
def test_plate_salt_di_esempio_rifiutato():
    from dealfinder.core.normalize import plate_hash
    assert plate_hash("AB123CD", "cambiami") is None
    assert plate_hash("AB123CD", "") is None
    assert plate_hash("AB123CD", None) is None
    h = plate_hash("AB123CD", "sale-vero-lungo")
    assert isinstance(h, str) and len(h) == 64


def test_ricerca_targa_senza_sale_non_usa_la_memoria():
    from dealfinder.ai import plate
    from dealfinder.config import settings
    vecchio = settings.plate_salt
    settings.plate_salt = "cambiami"

    class Conn:
        def execute(self, *a):
            raise AssertionError("non deve leggere plate_cache")
    try:
        assert plate.cached_lookup(Conn(), "AB123CD", lambda p: {"make": "Fiat"}) == {"make": "Fiat"}
    finally:
        settings.plate_salt = vecchio


# ---------- voce degli ospiti ----------
def test_voce_ospiti_limitata():
    P = _Patch()
    usi = {"n": 0}

    def oggi(kind, conta):
        if conta:
            usi["n"] += 1
        return usi["n"]
    try:
        P.set(A, "_voce_ospiti_oggi", oggi)
        A._voce_uso.clear()
        ospite = {"id": 0, "role": "ospite"}
        r = _req({"X-Forwarded-For": "93.184.216.50"})
        assert _errore(A._voce_gate, r, ospite, "tts", "x" * (A.VOCE_OSPITE_CARATTERI + 1)) == 413
        for _ in range(A.VOCE_OSPITE_MINUTO):
            assert _errore(A._voce_gate, r, ospite, "tts", "ciao") is None
        assert _errore(A._voce_gate, r, ospite, "tts", "ciao") == 429            # troppe in un minuto
        # tetto di tutti gli ospiti insieme (dal database): un altro indirizzo è fermato lo stesso
        usi["n"] = A.VOCE_OSPITI_GIORNO
        assert _errore(A._voce_gate, _req({"X-Forwarded-For": "93.184.216.51"}), ospite, "tts", "ciao") == 429
        # chi ha un account non ha questi limiti
        assert _errore(A._voce_gate, r, {"id": 3, "role": "commerciante"}, "tts", "x" * 600) is None
    finally:
        A._voce_uso.clear()
        P.undo()


# ---------- abbonamento per assistente e servizi ----------
def test_assistente_e_servizi_chiedono_abbonamento():
    import os
    P = _Patch()
    chiesti = []

    def richiede(request, permesso):
        chiesti.append(permesso)
        A.err(402, "Serve l'abbonamento")
    vecchio = os.environ.pop("SITO_APERTO", None)
    try:
        P.set(A, "richiede", richiede)
        auth = {"Authorization": "Bearer x"}
        assert _errore(A.assistente, _req(auth, method="POST")) == 402
        assert _errore(A.servizio_vendi, _req(auth, method="POST")) == 402
        assert _errore(A.servizio_ricambi, _req(auth, method="POST")) == 402
        assert chiesti == ["auto", "auto", "auto"]
        # senza accesso e sito chiuso: niente auto dal bot
        P.undo()
        P = _Patch()
        P.set(A, "read_token", lambda t: None)
        assert _errore(A.assistente, _req(method="POST")) == 401
    finally:
        if vecchio is not None:
            os.environ["SITO_APERTO"] = vecchio
        P.undo()
