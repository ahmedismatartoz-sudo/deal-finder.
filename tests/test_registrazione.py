"""23/11: registrazione dal sito con email e password (POST /api/auth/registrati), oltre a Google.

L'account nasce come quello di chi entra con Google (social.crea_account, una sola regola): con ISCRIZIONE=pagamento è
attivo, deve abbonarsi e poi vede le auto. Database finto in memoria per le regole; in fondo le stesse cose sul
Postgres vero (tests/pg_vero.py) con tutte le migrazioni, se Postgres è installato.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import types

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from dealfinder.web import abbonamento, app as A, auth, social

PWD = "Garage2024ok"
BUONO = {"nome": "Mario Rossi", "azienda": "Autosalone Rossi", "telefono": "333 123 4567", "citta": "Monza",
         "email": "mario@esempio.it", "password": PWD, "conferma": PWD, "consenso": True}
_PREDEFINITI = {"active": True, "role": "commerciante", "richiede_abbonamento": False, "paga_carta": False,
                "permessi": ["auto"], "provinces": ["MI", "MB", "BG", "BS"], "password_hash": None, "google_sub": None,
                "apple_sub": None, "company": None, "phone": None, "abbonamento_stato": None,
                "abbonamento_tolleranza": None, "accesso_fino": None}


class _R:
    def __init__(self, righe=()):
        self._r = list(righe)

    def fetchone(self):
        return self._r[0] if self._r else None

    def fetchall(self):
        return list(self._r)


class DBFinto:
    """dealers, dealer_costs, access_requests e dealer_sessions in memoria; ricorda SQL e parametri."""

    def __init__(self, colonne_065=True):
        self.dealers, self.costi, self.richieste, self.sessioni, self.viste = [], [], [], [], []
        self.colonne_065 = colonne_065

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, sql, p=()):
        s = " ".join(sql.split())
        p = tuple(p or ())
        self.viste.append((s, p))
        if s.startswith("SELECT 1 FROM information_schema.columns"):
            return _R([{"x": 1}] if self.colonne_065 else [])
        if s.startswith("SELECT 1 FROM dealers WHERE lower(email)=%s"):
            return _R([{"x": 1}] if any(d["email"].lower() == p[0] for d in self.dealers) else [])
        if s.startswith("SELECT * FROM dealers WHERE email=%s AND active"):
            return _R([dict(d) for d in self.dealers if d["email"] == p[0] and d["active"]])
        m = re.match(r"SELECT \* FROM dealers WHERE (google_sub|apple_sub)=%s", s)
        if m:
            return _R([dict(d) for d in self.dealers if d.get(m.group(1)) == p[0]])
        if s.startswith("SELECT 1 FROM access_requests WHERE email=%s AND NOT handled"):
            return _R([{"x": 1}] if any(q["email"] == p[0] and not q.get("handled") for q in self.richieste) else [])
        if s.startswith("SELECT * FROM dealers WHERE email=%s"):
            return _R([dict(d) for d in self.dealers if d["email"] == p[0]])
        m = re.match(r"INSERT INTO dealers \(([^)]*)\) VALUES \(([^)]*)\) RETURNING \*", s)
        if m:
            nomi = [c.strip() for c in m.group(1).split(",")]
            assert len(nomi) == len(p) == m.group(2).count("%s")
            if any(d["email"] == dict(zip(nomi, p))["email"] for d in self.dealers):
                raise RuntimeError('duplicate key value violates unique constraint "dealers_email_key"')
            d = {**_PREDEFINITI, "id": len(self.dealers) + 1, **dict(zip(nomi, p))}
            self.dealers.append(d)
            return _R([dict(d)])
        if s.startswith("INSERT INTO dealer_costs"):
            self.costi.append(p[0])
            return _R()
        if s.startswith("INSERT INTO access_requests (name, company, email, phone, note, source)"):
            self.richieste.append(dict(zip(("name", "company", "email", "phone", "note", "source"), p)))
            return _R()
        if s.startswith("INSERT INTO dealer_sessions"):
            self.sessioni.append({"id": p[0], "dealer_id": p[1], "method": p[2]})
            return _R()
        if s.startswith("SELECT id FROM dealer_sessions"):
            return _R()
        raise AssertionError("SQL non previsto: " + s[:160])


@contextlib.contextmanager
def _sito(db, **env):
    """Sito finto con le sole rotte di registrazione e accesso; ambiente, limiti e database rimessi a posto alla fine."""
    prima_env = dict(os.environ)
    prima = (A.connect, dict(A._registrazioni_ip), dict(A._attempts), dict(abbonamento._COLONNE))
    for k in ("ISCRIZIONE", "REGISTRAZIONE", "ABBONAMENTO_OBBLIGATORIO"):
        os.environ.pop(k, None)
    os.environ.update(env)
    A.connect = lambda: db
    A._registrazioni_ip.clear()
    abbonamento._COLONNE.clear()
    app = Starlette(routes=[Route("/api/auth/registrati", A.registrati, methods=["POST"]),
                            Route("/api/auth/login", A.login, methods=["POST"]),
                            Route("/api/auth/metodi", A.auth_metodi)],
                    exception_handlers={A.HTTPError: A.http_error})
    try:
        yield TestClient(app)
    finally:
        A.connect = prima[0]
        for diz, v in ((A._registrazioni_ip, prima[1]), (A._attempts, prima[2]), (abbonamento._COLONNE, prima[3])):
            diz.clear()
            diz.update(v)
        os.environ.clear()
        os.environ.update(prima_env)


def _reg(c, ip="93.44.10.1", **campi):
    return c.post("/api/auth/registrati", json={**BUONO, **campi}, headers={"x-real-ip": ip})


def _senza(c, campo):
    d = {k: v for k, v in BUONO.items() if k != campo}
    return c.post("/api/auth/registrati", json=d, headers={"x-real-ip": "93.44.10.1"})


# ------------------------------------------------------------------------------------------------ controlli sul server
def test_campi_obbligatori_mancanti():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        for campo in ("nome", "azienda", "telefono", "citta", "email", "password", "conferma", "consenso"):
            r = _senza(c, campo)
            assert r.status_code == 400 and r.json()["campo"] == campo, (campo, r.json())
            assert r.json()["error"]
            r = _reg(c, **{campo: "   " if campo != "consenso" else False})          # vuoto o solo spazi: uguale
            assert r.status_code == 400 and r.json()["campo"] == campo, (campo, r.json())
        assert c.post("/api/auth/registrati", content=b"non json").status_code == 400
        assert c.post("/api/auth/registrati", json=["a"]).status_code == 400
    assert db.dealers == [] and db.sessioni == []


def test_consenso_obbligatorio_e_vero():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        for v in (False, None, "sì", 1, "true"):                 # vale solo true: nessuna casella "quasi spuntata"
            r = _reg(c, consenso=v)
            assert r.status_code == 400 and r.json()["campo"] == "consenso"
            assert "condizioni" in r.json()["error"] and "privacy" in r.json()["error"]
    assert db.dealers == []


def test_password_corta_o_debole():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        for pwd, pezzo in (("Abc1234", "almeno 8 caratteri"), ("soltantolettere", "una lettera e un numero"),
                           ("1234567890", "una lettera e un numero"), ("a1" * 101, "troppo lunga")):
            r = _reg(c, password=pwd, conferma=pwd)
            assert r.status_code == 400 and r.json()["campo"] == "password", (pwd, r.json())
            assert pezzo in r.json()["error"], r.json()
        assert _reg(c, password=12345678, conferma=12345678).status_code == 400      # non testo
    assert db.dealers == []


def test_password_diverse():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        for conferma in (PWD + "x", PWD.lower(), PWD + " ", ""):
            r = _reg(c, conferma=conferma)
            assert r.status_code == 400 and r.json()["campo"] == "conferma"
            assert r.json()["error"] == "Le due password non sono uguali"
    assert db.dealers == []


def test_email_valida_e_minuscola():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        for em in ("mario", "mario@", "@esempio.it", "mario@esempio", "ma rio@esempio.it", "mario@@esempio.it",
                   "mario@esempio..it", "mario@esempio.it\nBcc: x@y.it", "a" * 196 + "@e.it", ["mario@esempio.it"]):
            r = _reg(c, email=em)
            assert r.status_code == 400 and r.json()["campo"] == "email", (em, r.json())
        assert db.dealers == []
        r = _reg(c, email="  Mario.Rossi+Auto@Esempio.IT ")
        assert r.status_code == 201
    assert db.dealers[0]["email"] == "mario.rossi+auto@esempio.it"
    assert r.json()["user"]["email"] == "mario.rossi+auto@esempio.it"


def test_telefono_italiano_cellulare_o_fisso():
    for buono, atteso in (("3331234567", "+393331234567"), ("+39 333 123 4567", "+393331234567"),
                          ("0039 333-123.4567", "+393331234567"), ("02 1234567", "+39021234567"),
                          ("039/23.45.67", "+39039234567"), ("(0544) 123456", "+390544123456"),
                          ("320 12 34 567", "+393201234567")):
        assert A._telefono_italiano(buono) == atteso, buono
    for cattivo in ("", "123", "333", "33312345678901", "+44 7700 900123", "telefono", "5551234567", "333 12a 4567",
                    "+393", "0", "٣٣٣١٢٣٤٥٦٧"):
        assert A._telefono_italiano(cattivo) is None, cattivo
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        for cattivo in ("123", "+44 7700 900123", "3" * 31, 3331234567):
            r = _reg(c, telefono=cattivo)
            assert r.status_code == 400 and r.json()["campo"] == "telefono", cattivo
        assert _reg(c, telefono="+39 333 123 4567").status_code == 201
    assert db.dealers[0]["phone"] == "+393331234567"


def test_lunghezze_massime():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        for campo, n in (("nome", 121), ("azienda", 121), ("citta", 81)):
            r = _reg(c, **{campo: "a" * n})
            assert r.status_code == 400 and r.json()["campo"] == campo and "massimo" in r.json()["error"]
        assert db.dealers == []
        r = _reg(c, nome="N" * 120, azienda="A" * 120, citta="C" * 80, password="a1" * 100, conferma="a1" * 100)
        assert r.status_code == 201
    d = db.dealers[0]
    assert len(d["name"]) == 120 and len(d["company"]) == 120 and len(d["citta"]) == 80


# ------------------------------------------------------------------------------------------------ email già registrata
def test_email_gia_registrata_409_senza_righe_nuove():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        assert _reg(c).status_code == 201
        prima = (len(db.dealers), len(db.costi), len(db.sessioni), len(db.richieste))
        for em in ("mario@esempio.it", "MARIO@Esempio.it"):
            r = _reg(c, email=em, password="AltraPassword9", conferma="AltraPassword9")
            assert r.status_code == 409
            assert r.json()["error"] == "Questa email ha già un account: accedi"
            assert "token" not in r.json()
        assert (len(db.dealers), len(db.costi), len(db.sessioni), len(db.richieste)) == prima
        # la password del primo account non è cambiata
        assert auth.verify_password(PWD, db.dealers[0]["password_hash"])
        assert not auth.verify_password("AltraPassword9", db.dealers[0]["password_hash"])


def test_email_gia_registrata_con_google_409():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        social.find_or_create(db, "google", {"sub": "g-1", "email": "mario@esempio.it", "name": "Mario"})
        assert _reg(c).status_code == 409
    assert len(db.dealers) == 1 and db.dealers[0]["password_hash"] is None      # nessuna password aggiunta di nascosto


def test_due_richieste_insieme_stessa_email():
    """L'indice unico su dealers.email decide: la seconda riceve 409, non un errore del server."""
    db = DBFinto()
    vero = db.execute

    def execute(sql, p=()):
        if " ".join(sql.split()).startswith("SELECT 1 FROM dealers WHERE lower(email)"):
            vero(sql, p)
            return _R()                         # il controllo non vede ancora l'altra richiesta
        return vero(sql, p)
    db.execute = execute
    with _sito(db, ISCRIZIONE="pagamento") as c:
        assert _reg(c).status_code == 201
        r = _reg(c)
        assert r.status_code == 409 and r.json()["error"] == "Questa email ha già un account: accedi"
    assert len(db.dealers) == 1


# ------------------------------------------------------------------------------------------------ anti-abuso
def test_campo_esca_rifiuta_in_silenzio():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        r = _reg(c, sito="https://spam.example")
        assert r.status_code == 200 and r.json() == {"ok": True}          # nessun errore, nessun token
        r = _reg(c, sito="x", password="corta", email="non-valida")       # nemmeno i controlli: niente indizi al robot
        assert r.status_code == 200 and r.json() == {"ok": True}
    assert db.dealers == [] and db.sessioni == [] and db.viste == []


def test_limite_per_ip():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        for i in range(A.REGISTRAZIONI_ORA):
            assert _reg(c, ip="93.44.10.7", email=f"utente{i}@esempio.it").status_code == 201
        r = _reg(c, ip="93.44.10.7", email="sesto@esempio.it")
        assert r.status_code == 429 and "riprova" in r.json()["error"].lower()
        assert len(db.dealers) == A.REGISTRAZIONI_ORA == 5
        assert _reg(c, ip="93.44.10.8", email="altro@esempio.it").status_code == 201      # un altro indirizzo passa
        # passata un'ora si può di nuovo
        A._registrazioni_ip["ok:93.44.10.7"] = [t - 3601 for t in A._registrazioni_ip["ok:93.44.10.7"]]
        A._registrazioni_ip["prove:93.44.10.7"] = [t - 3601 for t in A._registrazioni_ip["prove:93.44.10.7"]]
        assert _reg(c, ip="93.44.10.7", email="sesto@esempio.it").status_code == 201


def test_limite_prove_email_gia_registrata():
    """Chi prova tante email per scoprire quali hanno un account viene fermato (20 richieste l'ora per IP)."""
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        assert _reg(c, ip="93.44.10.9").status_code == 201
        for _ in range(A.REGISTRAZIONI_PROVE_ORA - 1):
            assert _reg(c, ip="93.44.10.9").status_code == 409
        assert _reg(c, ip="93.44.10.9").status_code == 429
        # gli errori di compilazione (400) non consumano il limite
        for _ in range(30):
            assert _reg(c, ip="93.44.10.10", password="corta").status_code == 400
        assert _reg(c, ip="93.44.10.10", email="nuovo@esempio.it").status_code == 201


# ------------------------------------------------------------------------------------------------ account creato
def test_account_come_google_in_modalita_pagamento():
    from dealfinder.zone import tutte
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        g, esito = social.find_or_create(db, "google", {"sub": "g-9", "email": "google@esempio.it", "name": "Gino"})
        assert esito == "ok"
        r = _reg(c)
        assert r.status_code == 201
        out = r.json()
        p = db.dealers[1]
        for k in ("role", "active", "richiede_abbonamento", "permessi", "provinces"):
            assert p[k] == g[k], k
        assert p["role"] == "commerciante" and p["active"] is True and p["richiede_abbonamento"] is True
        assert p["permessi"] == ["auto"] and p["provinces"] == tutte()
        assert db.costi == [g["id"], p["id"]]                             # riga dealer_costs per tutti e due
        assert db.richieste == []                                         # nessuna richiesta d'accesso: entra subito
        assert (p["name"], p["company"], p["phone"], p["citta"]) == ("Mario Rossi", "Autosalone Rossi", "+393331234567", "Monza")
        assert p["consenso_at"] is not None and p["google_sub"] is None
        # stesso accesso alle auto: bloccato finché non paga, aperto con l'abbonamento attivo (regola di abbonamento.puo_vedere)
        riga_g = {**g, "con_password": False}
        riga_p = {**p, "con_password": True}
        assert not abbonamento.puo_vedere(riga_g) and not abbonamento.puo_vedere(riga_p)
        assert abbonamento.puo_vedere({**riga_g, "abbonamento_stato": "active"})
        assert abbonamento.puo_vedere({**riga_p, "abbonamento_stato": "active"})
        assert p["paga_carta"] is True      # senza questo un account con password vedrebbe le auto senza pagare
        # risposta come il login + il segnale per aprire la scelta del piano
        assert out["richiede_abbonamento"] is True
        assert out["user"] == {"id": p["id"], "name": "Mario Rossi", "email": "mario@esempio.it", "role": "commerciante"}
        tok = auth.read_token(out["token"])
        assert tok["id"] == p["id"] and tok["role"] == "commerciante"
        assert db.sessioni == [{"id": tok["sid"], "dealer_id": p["id"], "method": "password"}]


def test_google_invariato_dopo_la_regola_unica():
    """find_or_create scrive le stesse colonne di prima (23/11: la creazione è passata in social.crea_account)."""
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento"):
        social.find_or_create(db, "google", {"sub": "g-1", "email": "a@esempio.it", "name": "A"})
        sql, p = next(v for v in db.viste if v[0].startswith("INSERT INTO dealers"))
        assert sql == ("INSERT INTO dealers (name, email, role, google_sub, active, richiede_abbonamento, permessi, "
                       "provinces) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *")
        assert p[:6] == ("A", "a@esempio.it", "commerciante", "g-1", True, True) and p[6] == ["auto"]
        assert db.dealers[0]["paga_carta"] is False
        d2, esito = social.find_or_create(db, "google", {"sub": "g-1", "email": "a@esempio.it"})
        assert esito == "ok" and d2["id"] == 1 and len(db.dealers) == 1
    db = DBFinto()
    with _sito(db):                                                    # predefinito: approvazione
        d, esito = social.find_or_create(db, "apple", {"sub": "ap-1", "email": "b@esempio.it"}, "Bruno Bianchi")
        assert esito == "in_attesa" and d["active"] is False and d["name"] == "Bruno Bianchi"
        assert d["richiede_abbonamento"] is False and d["apple_sub"] == "ap-1"
        assert db.richieste == [{"name": "Bruno Bianchi", "company": None, "email": "b@esempio.it", "phone": None,
                                 "note": "Iscritto con Apple", "source": "apple"}]
        assert social.find_or_create(db, "apple", {"sub": "ap-1", "email": "b@esempio.it"})[1] == "in_attesa"
        db.richieste[0]["handled"] = True
        assert social.find_or_create(db, "apple", {"sub": "ap-1", "email": "b@esempio.it"})[1] == "disattivato"
    db = DBFinto()
    with _sito(db, ISCRIZIONE="aperta"):
        d, esito = social.find_or_create(db, "google", {"sub": "g-2", "email": "c@esempio.it"})
        assert esito == "ok" and d["active"] is True and d["name"] == "c" and db.richieste == []


def test_password_salvata_solo_come_hash_e_log_puliti():
    db = DBFinto()
    righe = []

    class H(logging.Handler):
        def emit(self, r):
            righe.append(r.getMessage())
    h = H()
    logging.getLogger().addHandler(h)
    try:
        with _sito(db, ISCRIZIONE="pagamento") as c:
            r = _reg(c)
            assert r.status_code == 201
    finally:
        logging.getLogger().removeHandler(h)
    d = db.dealers[0]
    assert d["password_hash"].startswith("scrypt$") and PWD not in d["password_hash"]
    assert auth.verify_password(PWD, d["password_hash"]) and not auth.verify_password(PWD + "x", d["password_hash"])
    # la password in chiaro non compare in nessuna query né in nessun parametro, né nella risposta
    for sql, p in db.viste:
        assert PWD not in sql and all(PWD not in str(x) for x in p), sql
    assert PWD not in r.text and "password" not in r.json() and "password_hash" not in r.text
    assert all(PWD not in str(v) for v in d.values())
    # log: solo id ed email mascherata
    reg = [x for x in righe if "REGISTRAZIONE" in x]
    assert reg == ["REGISTRAZIONE ok id=1 email=m***@esempio.it"], reg
    tutto = "\n".join(righe)
    assert PWD not in tutto and "mario@esempio.it" not in tutto
    assert "3331234567" not in tutto and "333 123 4567" not in tutto and "Rossi" not in tutto


def test_login_con_email_e_password_dopo_la_registrazione():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        assert _reg(c, email="Mario@Esempio.it").status_code == 201
        r = c.post("/api/auth/login", json={"email": "mario@esempio.it", "password": PWD}, headers={"x-real-ip": "93.44.10.1"})
        assert r.status_code == 200
        assert r.json()["user"]["id"] == 1 and auth.read_token(r.json()["token"])["id"] == 1
        assert [s["method"] for s in db.sessioni] == ["password", "password"]
        r = c.post("/api/auth/login", json={"email": "MARIO@esempio.it ", "password": PWD}, headers={"x-real-ip": "93.44.10.1"})
        assert r.status_code == 200                                    # il login mette l'email in minuscolo
        r = c.post("/api/auth/login", json={"email": "mario@esempio.it", "password": PWD + "!"}, headers={"x-real-ip": "93.44.10.1"})
        assert r.status_code == 401


# ------------------------------------------------------------------------------------------------ altri modi di iscrizione
def test_senza_pagamento_come_google_richiesta_di_accesso():
    db = DBFinto()
    with _sito(db) as c:                                               # ISCRIZIONE non impostata = approvazione
        r = _reg(c)
        assert r.status_code == 202 and r.json()["in_attesa"] is True and "token" not in r.json()
        d = db.dealers[0]
        assert d["active"] is False and d["richiede_abbonamento"] is False and d["paga_carta"] is False
        assert auth.verify_password(PWD, d["password_hash"]) and db.sessioni == [] and db.costi == [1]
        assert len(db.richieste) == 1
        q = db.richieste[0]
        assert (q["name"], q["company"], q["email"], q["phone"], q["source"]) == \
            ("Mario Rossi", "Autosalone Rossi", "mario@esempio.it", "+393331234567", "registrazione")
        assert PWD not in str(q)
        # finché l'amministratore non lo attiva non entra; dopo entra con la sua password
        assert c.post("/api/auth/login", json={"email": "mario@esempio.it", "password": PWD}).status_code == 401
        db.dealers[0]["active"] = True
        assert c.post("/api/auth/login", json={"email": "mario@esempio.it", "password": PWD}).status_code == 200


def test_iscrizione_aperta_entra_subito_senza_abbonamento():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="aperta") as c:
        r = _reg(c)
        assert r.status_code == 201 and r.json()["richiede_abbonamento"] is False and r.json()["token"]
        d = db.dealers[0]
        assert d["active"] is True and d["richiede_abbonamento"] is False and d["paga_carta"] is False
        assert db.richieste == [] and len(db.sessioni) == 1


def test_interruttore_registrazione():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        assert social.registrazione_attiva() is True                   # accesa senza nessuna variabile su Render
        assert c.get("/api/auth/metodi").json()["registrazione"] is True
    for spento in ("0", " 0 "):
        with _sito(db, ISCRIZIONE="pagamento", REGISTRAZIONE=spento) as c:
            m = c.get("/api/auth/metodi").json()
            assert m["registrazione"] is False and "google" in m and "abbonamento" in m
            r = _reg(c)
            assert r.status_code == 403 and "non è attiva" in r.json()["error"]
    for acceso in ("1", "", "si"):
        with _sito(db, REGISTRAZIONE=acceso) as c:
            assert c.get("/api/auth/metodi").json()["registrazione"] is True
    assert db.dealers == []


def test_senza_la_migrazione_065_si_registra_lo_stesso():
    db = DBFinto(colonne_065=False)
    with _sito(db, ISCRIZIONE="pagamento") as c:
        assert _reg(c).status_code == 201
    sql = next(v[0] for v in db.viste if v[0].startswith("INSERT INTO dealers"))
    assert "citta" not in sql and "consenso_at" not in sql and "password_hash" in sql and "paga_carta" in sql


def test_rotta_e_pagine_del_consenso():
    percorsi = {r.path: r for r in A.app.routes if hasattr(r, "path")}
    assert "POST" in percorsi["/api/auth/registrati"].methods
    assert "/condizioni" in percorsi and "/privacy" in percorsi      # i due link della casella del consenso
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dealfinder", "web", "static", "scovo")
    js = open(os.path.join(base, "app.js"), encoding="utf-8").read()
    for pezzo in ('id="reg-form"', '"/api/auth/registrati"', 'name="conferma"', 'name="consenso"', 'href="/condizioni"',
                  'href="/privacy"', 'name="sito"', "#/registrati"):
        assert pezzo in js, pezzo
    assert 'href="#/registrati"' in open(os.path.join(base, "index.html"), encoding="utf-8").read()


# ------------------------------------------------------------------------------------------------ Postgres vero (se c'è)
def test_registrazione_su_postgres_vero():
    """Le stesse query sullo schema vero (tutte le migrazioni): colonne, tipi, indice unico sull'email."""
    from tests import pg_vero
    pg = pg_vero.nuovo_db("registrazione_2311")
    if pg is None:
        print("    (Postgres non installato: prova saltata)")
        return
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "migrations")
    pg_vero.migra(pg, *sorted(f for f in os.listdir(base) if f.endswith(".sql")))
    pg_vero.migra(pg, "065_registrazione.sql")                          # idempotente: due volte non dà errore
    from dealfinder.zone import tutte
    with _sito(pg, ISCRIZIONE="pagamento") as c:
        g, _ = social.find_or_create(pg, "google", {"sub": "g-pg", "email": "google@esempio.it", "name": "Gino"})
        r = _reg(c)
        assert r.status_code == 201, r.text
        p = pg.execute("SELECT * FROM dealers WHERE email=%s", ("mario@esempio.it",)).fetchone()
        for k in ("role", "active", "richiede_abbonamento", "permessi", "provinces", "max_purchase", "resale_as"):
            assert p[k] == g[k], k
        assert p["active"] is True and p["richiede_abbonamento"] is True and p["permessi"] == ["auto"]
        assert sorted(p["provinces"]) == tutte() and p["paga_carta"] is True and g["paga_carta"] is False
        assert (p["company"], p["phone"], p["citta"]) == ("Autosalone Rossi", "+393331234567", "Monza")
        assert p["consenso_at"] and auth.verify_password(PWD, p["password_hash"])
        assert pg.execute("SELECT count(*) AS n FROM dealer_costs WHERE dealer_id IN (%s,%s)", (g["id"], p["id"])).fetchone()["n"] == 2
        # la regola vera dell'elenco auto: bloccati tutti e due finché non pagano
        assert not A.puo_vedere_auto(pg, p["id"]) and not A.puo_vedere_auto(pg, g["id"])
        pg.execute("UPDATE dealers SET abbonamento_stato='active' WHERE id=%s", (p["id"],))
        assert A.puo_vedere_auto(pg, p["id"])
        # email già registrata (anche con maiuscole): 409 e nessuna riga nuova
        n = pg.execute("SELECT count(*) AS n FROM dealers").fetchone()["n"]
        assert _reg(c, email="MARIO@esempio.it").status_code == 409 and _reg(c, email="google@esempio.it").status_code == 409
        assert pg.execute("SELECT count(*) AS n FROM dealers").fetchone()["n"] == n
        # sessione aperta e login con la password scelta
        s = pg.execute("SELECT method FROM dealer_sessions WHERE dealer_id=%s", (p["id"],)).fetchall()
        assert [x["method"] for x in s] == ["password"]
        assert c.post("/api/auth/login", json={"email": "mario@esempio.it", "password": PWD}).status_code == 200
        assert c.post("/api/auth/login", json={"email": "mario@esempio.it", "password": "Sbagliata1"}).status_code == 401
    with _sito(pg) as c:                                                # approvazione: richiesta d'accesso
        r = _reg(c, email="attesa@esempio.it")
        assert r.status_code == 202, r.text
        d = pg.execute("SELECT active, richiede_abbonamento, paga_carta FROM dealers WHERE email='attesa@esempio.it'").fetchone()
        assert d == {"active": False, "richiede_abbonamento": False, "paga_carta": False}
        q = pg.execute("SELECT name, company, phone, source, handled FROM access_requests WHERE email='attesa@esempio.it'").fetchall()
        assert q == [{"name": "Mario Rossi", "company": "Autosalone Rossi", "phone": "+393331234567",
                      "source": "registrazione", "handled": False}]
