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
        if s.startswith("SELECT active FROM dealers WHERE lower(email)=%s"):
            return _R(sorted(({"active": d["active"]} for d in self.dealers if d["email"].lower() == p[0]),
                             key=lambda r: not r["active"])[:1])
        if s.startswith("SELECT * FROM dealers WHERE id=%s"):
            return _R([dict(d) for d in self.dealers if d["id"] == p[0]])
        m = re.match(r"UPDATE dealers SET (google_sub|apple_sub)=%s(, password_hash=NULL)? WHERE id=%s", s)
        if m:
            d = next(d for d in self.dealers if d["id"] == p[1])
            d[m.group(1)] = p[0]
            if m.group(2):
                d["password_hash"] = None
            return _R()
        if s.startswith("UPDATE dealers SET email_verificata=true WHERE id=%s"):
            next(d for d in self.dealers if d["id"] == p[0])["email_verificata"] = True
            return _R()
        if s.startswith("UPDATE dealer_sessions SET revoked_at=now(), revoked_why=%s WHERE dealer_id=%s"):
            chiuse = [x for x in self.sessioni if x["dealer_id"] == p[1] and not x.get("revocata")]
            for x in chiuse:
                x["revocata"] = p[0]
            return _R([{"id": x["id"]} for x in chiuse])
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
            d = {**_PREDEFINITI, **({"email_verificata": None} if self.colonne_065 else {}),
                 "id": len(self.dealers) + 1, **dict(zip(nomi, p))}
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
        if " ".join(sql.split()).startswith("SELECT active FROM dealers WHERE lower(email)"):
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
        assert p["email_verificata"] is False and g["email_verificata"] is None      # nessuno ha dimostrato l'indirizzo
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


# ------------------------------------------------------------------------------------------------ solo con il pagamento
def test_senza_pagamento_la_registrazione_e_chiusa():
    """Revisione 23/11: la registrazione con password esiste SOLO con ISCRIZIONE=pagamento. In approvazione (default)
    o con l'iscrizione aperta: 403, nessun account, e /api/auth/metodi dice registrazione=false (pulsante nascosto)."""
    for env in ({}, {"ISCRIZIONE": "approvazione"}, {"ISCRIZIONE": "aperta"}, {"ISCRIZIONE": "Pagamento"},
                {"ISCRIZIONE": "aperta", "REGISTRAZIONE": "1"}):
        db = DBFinto()
        with _sito(db, **env) as c:
            assert social.registrazione_attiva() is False, env
            assert c.get("/api/auth/metodi").json()["registrazione"] is False, env
            r = _reg(c)
            assert r.status_code == 403 and "non è attiva" in r.json()["error"] and "token" not in r.json(), env
            assert _reg(c, sito="robot").status_code == 403           # anche il campo esca: la rotta è proprio chiusa
        assert db.dealers == [] and db.richieste == [] and db.sessioni == [] and db.viste == [], env


def test_account_con_password_sempre_paga_carta():
    """Difesa in più: qualunque sia la modalità, un account creato da soli con la password deve pagare con carta."""
    for env in ({}, {"ISCRIZIONE": "aperta"}, {"ISCRIZIONE": "pagamento"}):
        db = DBFinto()
        with _sito(db, **env):
            d, _ = social.crea_account(db, "Mario Rossi", "mario@esempio.it", {"password_hash": auth.hash_password(PWD)},
                                       "registrazione", "nota")
            assert d["paga_carta"] is True and d["email_verificata"] is False, env
            assert not abbonamento.puo_vedere({**d, "con_password": True}), env
            g, _ = social.crea_account(db, "Gino", "gino@esempio.it", {"google_sub": "g"}, "google", "nota")
            assert g["paga_carta"] is False and g["email_verificata"] is None, env


def test_interruttore_registrazione():
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        assert social.registrazione_attiva() is True                   # accesa senza variabili nuove su Render
        assert c.get("/api/auth/metodi").json()["registrazione"] is True
    for spento in ("0", " 0 "):
        with _sito(db, ISCRIZIONE="pagamento", REGISTRAZIONE=spento) as c:
            m = c.get("/api/auth/metodi").json()
            assert m["registrazione"] is False and "google" in m and "abbonamento" in m
            r = _reg(c)
            assert r.status_code == 403 and "non è attiva" in r.json()["error"]
    for acceso in ("1", "", "si"):
        with _sito(db, ISCRIZIONE="pagamento", REGISTRAZIONE=acceso) as c:
            assert c.get("/api/auth/metodi").json()["registrazione"] is True
    assert db.dealers == []


def test_email_di_account_non_attivo_messaggio_diverso():
    """Account disattivato o in attesa di attivazione: 409 con un altro messaggio (non "accedi": non potrebbe entrare)."""
    db = DBFinto()
    with _sito(db, ISCRIZIONE="pagamento") as c:
        assert _reg(c).status_code == 201
        db.dealers[0]["active"] = False
        r = _reg(c, email="Mario@esempio.it")
        assert r.status_code == 409 and r.json()["error"] == "Questa email ha già una richiesta in corso: ti contattiamo noi"
        assert "token" not in r.json() and len(db.dealers) == 1
    db = DBFinto()
    with _sito(db):                                                    # approvazione: Google resta in attesa
        social.find_or_create(db, "google", {"sub": "g-7", "email": "mario@esempio.it"})
    with _sito(db, ISCRIZIONE="pagamento") as c:
        r = _reg(c)
        assert r.status_code == 409 and "richiesta in corso" in r.json()["error"]
        assert len(db.dealers) == 1 and db.dealers[0]["password_hash"] is None


# ------------------------------------------------------------------------------------------------ collegamento con Google
def test_google_sull_email_registrata_azzera_password_e_sessioni():
    """Sicurezza (revisione 23/11): chiunque può registrare l'email di un altro con la propria password. Quando il vero
    proprietario entra con Google (email verificata) l'account passa a lui: password azzerata, sessioni chiuse."""
    from dealfinder.web import sessions
    righe = []

    class H(logging.Handler):
        def emit(self, r):
            righe.append(r.getMessage())
    h = H()
    logging.getLogger().addHandler(h)
    try:
        for colonne in (True, False):                                  # con e senza la migrazione 065
            db = DBFinto(colonne_065=colonne)
            with _sito(db, ISCRIZIONE="pagamento") as c:
                r = _reg(c)
                assert r.status_code == 201
                sid = auth.read_token(r.json()["token"])["sid"]
                assert c.post("/api/auth/login", json={"email": "mario@esempio.it", "password": PWD}).status_code == 200
                assert all(not x.get("revocata") for x in db.sessioni) and len(db.sessioni) == 2
                sessions._cache[sid] = ("finto",)                      # la sessione era in memoria: va dimenticata
                d, esito = social.find_or_create(db, "google", {"sub": "g-vero", "email": "mario@esempio.it", "name": "Mario"})
                assert esito == "ok" and d["id"] == 1 and d["google_sub"] == "g-vero" and d["password_hash"] is None
                riga = db.dealers[0]
                assert riga["password_hash"] is None and riga["google_sub"] == "g-vero"
                assert riga.get("email_verificata") is (True if colonne else None)
                assert riga["richiede_abbonamento"] is True and riga["paga_carta"] is True      # deve sempre pagare
                assert [x["revocata"] for x in db.sessioni] == ["collegato_google"] * 2 and sid not in sessions._cache
                # la password di chi aveva registrato l'indirizzo non entra più
                r = c.post("/api/auth/login", json={"email": "mario@esempio.it", "password": PWD})
                assert r.status_code == 401 and len(db.sessioni) == 2
                # secondo accesso con Google: nessun altro azzeramento, stesso account
                d2, esito = social.find_or_create(db, "google", {"sub": "g-vero", "email": "mario@esempio.it"})
                assert esito == "ok" and d2["id"] == 1 and len(db.dealers) == 1
    finally:
        logging.getLogger().removeHandler(h)
    avvisi = [x for x in righe if "ACCESSO_SOCIAL" in x]
    assert avvisi == ["ACCESSO_SOCIAL password azzerata al collegamento id=1"] * 2, avvisi
    assert all("esempio.it" not in x and PWD not in x for x in avvisi)


def test_google_non_tocca_gli_account_del_gestionale():
    """Gli account con password creati da noi dal gestionale (email_verificata NULL, niente richiede_abbonamento) e gli
    amministratori restano intatti: Google si collega e la password continua a valere."""
    for colonne in (True, False):
        db = DBFinto(colonne_065=colonne)
        with _sito(db, ISCRIZIONE="pagamento") as c:
            imp = auth.hash_password(PWD)
            casi = [("gestionale@esempio.it", {"role": "commerciante"}),
                    ("carta@esempio.it", {"role": "commerciante", "paga_carta": True}),       # "Deve pagare con carta"
                    ("capo@esempio.it", {"role": "admin", "richiede_abbonamento": True, "paga_carta": True})]
            for i, (em, extra) in enumerate(casi, 1):
                db.dealers.append({**_PREDEFINITI, **({"email_verificata": None} if colonne else {}), "id": i,
                                   "name": "X", "email": em, "password_hash": imp, **extra})
                db.sessioni.append({"id": f"s{i}", "dealer_id": i, "method": "password"})
            for i, (em, _) in enumerate(casi, 1):
                d, esito = social.find_or_create(db, "google", {"sub": f"g-{i}", "email": em})
                assert esito == "ok" and d["id"] == i
                assert db.dealers[i - 1]["password_hash"] == imp and db.dealers[i - 1]["google_sub"] == f"g-{i}", em
                assert c.post("/api/auth/login", json={"email": em, "password": PWD}).status_code == 200, em
            assert all(not x.get("revocata") for x in db.sessioni)
            assert not any("email_verificata=true" in v[0] or "password_hash=NULL" in v[0] for v in db.viste)
        # un account registrato che ha già Google collegato (verificato) non viene più toccato
        assert social._da_registrazione_non_verificata({"email_verificata": True, "password_hash": "x"}) is False
        assert social._da_registrazione_non_verificata({"email_verificata": False, "google_sub": "g"}) is False
        assert social._da_registrazione_non_verificata({"email_verificata": False}) is True


def test_hash_calcolato_prima_della_connessione():
    """scrypt dura decine di millisecondi: la connessione al database si prende dopo, non si tiene ferma."""
    db = DBFinto()
    ordine = []
    vero = A.hash_password
    with _sito(db, ISCRIZIONE="pagamento") as c:
        A.connect = lambda: (ordine.append("connessione"), db)[1]
        A.hash_password = lambda pw: (ordine.append("hash"), vero(pw))[1]
        try:
            assert _reg(c).status_code == 201
        finally:
            A.hash_password = vero
    assert ordine[:2] == ["hash", "connessione"] and ordine.count("hash") == 1


def test_gestionale_mostra_la_citta():
    """Elenco commercianti del gestionale: colonna citta se c'è la migrazione 065, NULL se non c'è (mai un errore)."""
    import inspect
    viste = []

    class C(DBFinto):
        def execute(self, sql, p=()):
            s = " ".join(sql.split())
            if s.startswith("SELECT d.id, d.name"):
                viste.append(s)
                return _R([{"id": 1, "citta": "Monza"}])
            return super().execute(sql, p)
    orig = A.current_user
    try:
        A.current_user = lambda request, admin=False: {"id": 1, "role": "admin"}
        for colonne, atteso in ((True, "d.phone, d.citta AS citta, d.role"), (False, "d.phone, NULL::text AS citta, d.role")):
            with _sito(C(colonne_065=colonne)):
                assert A.admin_dealers(None).status_code == 200
            assert atteso in viste[-1], viste[-1][:120]
    finally:
        A.current_user = orig
    assert "{" not in inspect.getsource(A.admin_dealers).replace("{citta}", "").replace('{"items": rows}', "")
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dealfinder", "web", "static")
    assert '["citta", "Città"]' in open(os.path.join(base, "gestione", "app.js"), encoding="utf-8").read()


def test_voce_solo_con_abbonamento():
    """La voce (TTS/STT) costa a ogni chiamata: chi ha un account e non può vedere le auto (non ha pagato) riceve 402."""
    db = DBFinto()
    orig = A.puo_vedere_auto
    req = types.SimpleNamespace(headers={}, client=types.SimpleNamespace(host="93.44.10.1"))
    prima = dict(A._voce_uso)
    try:
        with _sito(db, ISCRIZIONE="pagamento"):
            A._voce_uso.clear()
            A.puo_vedere_auto = lambda conn, did: did == 2
            for kind in ("tts", "stt"):
                try:
                    A._voce_gate(req, {"id": 1, "role": "commerciante"}, kind, "ciao")
                    raise AssertionError("doveva rifiutare")
                except A.HTTPError as e:
                    assert e.status == 402 and "abbonamento" in e.message
                A._voce_gate(req, {"id": 2, "role": "commerciante"}, kind, "ciao")      # abbonato: passa
                A._voce_gate(req, {"id": 9, "role": "admin"}, kind, "ciao")             # amministratore: passa
            assert not A._voce_uso.get("tts:1") and not A._voce_uso.get("stt:1")        # il rifiuto non conta come uso
            assert len(A._voce_uso["tts:2"]) == 1
    finally:
        A.puo_vedere_auto = orig
        A._voce_uso.clear()
        A._voce_uso.update(prima)


def test_senza_la_migrazione_065_si_registra_lo_stesso():
    db = DBFinto(colonne_065=False)
    with _sito(db, ISCRIZIONE="pagamento") as c:
        assert _reg(c).status_code == 201
    sql = next(v[0] for v in db.viste if v[0].startswith("INSERT INTO dealers"))
    assert "citta" not in sql and "consenso_at" not in sql and "email_verificata" not in sql
    assert "password_hash" in sql and "paga_carta" in sql


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
        # collegamento con Google sull'email registrata: password azzerata, sessioni chiuse, email verificata
        assert p["email_verificata"] is False and g["email_verificata"] is None
        d, esito = social.find_or_create(pg, "google", {"sub": "g-mario", "email": "mario@esempio.it"})
        assert esito == "ok" and d["id"] == p["id"] and d["password_hash"] is None and d["email_verificata"] is True
        assert d["google_sub"] == "g-mario" and d["paga_carta"] is True
        s = pg.execute("SELECT revoked_why FROM dealer_sessions WHERE dealer_id=%s", (p["id"],)).fetchall()
        assert [x["revoked_why"] for x in s] == ["collegato_google"] * 2
        assert c.post("/api/auth/login", json={"email": "mario@esempio.it", "password": PWD}).status_code == 401
        # account del gestionale (password, email_verificata NULL): Google si collega e la password resta
        pg.execute("INSERT INTO dealers (name, email, password_hash) VALUES ('Vecchio', 'vecchio@esempio.it', %s)",
                   (auth.hash_password(PWD),))
        social.find_or_create(pg, "google", {"sub": "g-vecchio", "email": "vecchio@esempio.it"})
        v = pg.execute("SELECT * FROM dealers WHERE email='vecchio@esempio.it'").fetchone()
        assert v["password_hash"] and v["google_sub"] == "g-vecchio" and v["email_verificata"] is None
        assert c.post("/api/auth/login", json={"email": "vecchio@esempio.it", "password": PWD}).status_code == 200
        # account non attivo: messaggio diverso
        pg.execute("UPDATE dealers SET active=false WHERE id=%s", (v["id"],))
        r = _reg(c, email="vecchio@esempio.it")
        assert r.status_code == 409 and "richiesta in corso" in r.json()["error"]
        # elenco del gestionale con la città (query vera)
        orig = A.current_user
        A.current_user = lambda request, admin=False: {"id": 1, "role": "admin"}
        try:
            import json
            righe = json.loads(A.admin_dealers(None).body)["items"]
        finally:
            A.current_user = orig
        assert {x["email"]: x["citta"] for x in righe}["mario@esempio.it"] == "Monza"
    with _sito(pg) as c:                                                # senza pagamento: chiusa, nessuna riga
        n = pg.execute("SELECT count(*) AS n FROM dealers").fetchone()["n"]
        assert _reg(c, email="attesa@esempio.it").status_code == 403
        assert pg.execute("SELECT count(*) AS n FROM dealers").fetchone()["n"] == n
