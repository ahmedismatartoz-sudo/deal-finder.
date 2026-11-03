"""init_db (3/11 notte): gli account preparati su Render non si riscrivono a ogni avvio; si applica solo il campo
cambiato su Render. Migrazioni sotto lucchetto e solo nel processo principale. Database finto."""
import json

from dealfinder import init_db as I

SEG = b"segreto-di-prova"


class _R:
    def __init__(self, rows=None, rowcount=0):
        self.rows, self.rowcount = rows or [], rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FintoDB:
    def __init__(self):
        self.dealers, self.prep, self.q, self.prossimo = {}, {}, [], 1

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, sql, p=()):
        s = " ".join(sql.split())
        self.q.append((s, p))
        if s.startswith("SELECT impronte FROM account_preparati"):
            v = self.prep.get(p[0])
            return _R([{"impronte": v}] if v is not None else [])
        if s.startswith("INSERT INTO account_preparati"):
            self.prep[p[0]] = json.loads(p[1])
            return _R()
        if s.startswith("SELECT id FROM dealers WHERE lower(email)=%s"):
            d = next((d for d in self.dealers.values() if d["email"] == p[0]), None)
            return _R([{"id": d["id"]}] if d else [])
        if s.startswith("INSERT INTO dealers"):
            did = self.prossimo
            self.prossimo += 1
            self.dealers[did] = {"id": did, "email": p[1], "active": True}
            return _R([{"id": did}])
        if s.startswith("UPDATE dealer_sessions"):
            return _R([])
        if s.startswith("UPDATE dealers SET active=false"):
            self.dealers[p[0]]["active"] = False
        return _R()

    def aggiornamenti(self, parola):
        return [s for s, _ in self.q if s.startswith("UPDATE dealers") and parola in s]


def _c(**k):
    base = {"email": "Cliente@X.it", "password": "password-1", "zona": "milano", "permessi": ["auto"]}
    base.update(k)
    return base


def test_account_creato_poi_non_riscritto():
    db = FintoDB()
    assert I.prepara_account(db, _c(), SEG) == "creato"
    db.q.clear()
    assert I.prepara_account(db, _c(), SEG) == "invariato"
    assert not db.aggiornamenti("password_hash") and not db.aggiornamenti("active") and not db.aggiornamenti("permessi")


def test_account_esistente_primo_avvio_solo_registrato():
    db = FintoDB()
    db.dealers[9] = {"id": 9, "email": "cliente@x.it", "active": False}       # spento dal pannello
    assert I.prepara_account(db, _c(), SEG) == "registrato"
    assert not [s for s, _ in db.q if s.startswith("UPDATE dealers")]
    assert db.dealers[9]["active"] is False                                   # l'avvio non lo riaccende


def test_solo_il_campo_cambiato():
    db = FintoDB()
    I.prepara_account(db, _c(), SEG)
    db.q.clear()
    assert I.prepara_account(db, _c(password="nuova-password-2"), SEG) == "aggiornato:password"
    assert db.aggiornamenti("password_hash") and not db.aggiornamenti("provinces") and not db.aggiornamenti("permessi")
    assert any(s.startswith("UPDATE dealer_sessions") for s, _ in db.q)       # sessioni chiuse col cambio password
    db.q.clear()
    assert I.prepara_account(db, _c(password="nuova-password-2", permessi=["auto", "incentivi"]), SEG) == "aggiornato:permessi"
    upd = db.aggiornamenti("permessi")
    assert upd and "'appuntamenti' = ANY" in upd[0]                            # appuntamenti di Stripe restano
    assert not db.aggiornamenti("password_hash")


def test_disattivazione_una_volta():
    db = FintoDB()
    I.prepara_account(db, _c(), SEG)
    assert I.prepara_account(db, _c(attivo=False), SEG) == "disattivato"
    assert db.aggiornamenti("disattivato_da_admin=true")
    db.dealers[1]["active"] = True                                             # l'admin lo riaccende dal pannello
    db.q.clear()
    assert I.prepara_account(db, _c(attivo=False), SEG) == "spento_gia"
    assert db.dealers[1]["active"] is True


def test_impronte_senza_password_in_chiaro():
    db = FintoDB()
    I.prepara_account(db, _c(password="password-segreta-9"), SEG)
    assert "password-segreta-9" not in json.dumps(db.prep)
    assert I.impronta("a", "password", "x", SEG) != I.impronta("a", "password", "y", SEG)


def test_admin_password_solo_se_cambiata():
    db = FintoDB()
    assert I.prepara_admin(db, "admin@x.it", "password-adm", SEG) == "creato"
    db.q.clear()
    assert I.prepara_admin(db, "admin@x.it", "password-adm", SEG) == "invariato"
    assert not db.aggiornamenti("password_hash")
    assert I.prepara_admin(db, "admin@x.it", "altra-password", SEG) == "password_aggiornata"


def test_permesso_appuntamenti_una_volta():
    db = FintoDB()
    I.prepara_account(db, _c(), SEG)
    assert I.permesso_appuntamenti(db, "cliente@x.it", SEG) == "acceso"
    db.q.clear()
    assert I.permesso_appuntamenti(db, "cliente@x.it", SEG) == "gia_dato"     # Stripe può toglierlo dopo
    assert not db.aggiornamenti("appuntamenti")


def test_processo_principale():
    assert I.processo_principale({"RENDER_SERVICE_TYPE": "web"})
    assert I.processo_principale({})
    assert not I.processo_principale({"RENDER_SERVICE_TYPE": "cron"})
    assert I.processo_principale({"RENDER_SERVICE_TYPE": "cron", "INIT_DB_SEMPRE": "1"})


def test_migrazioni_sotto_lucchetto(tmp=None):
    import tempfile
    from pathlib import Path
    from dealfinder import db as D

    class C(FintoDB):
        def __init__(self):
            super().__init__()
            self.fatte = {"001_a.sql"}

        def execute(self, sql, p=()):
            s = " ".join(sql.split())
            self.q.append((s, p))
            if s.startswith("SELECT name FROM schema_migrations"):
                return _R([{"name": n} for n in self.fatte])
            if s.startswith("SELECT pg_try_advisory_lock"):
                return _R([{"ok": True}])
            if s.startswith("INSERT INTO schema_migrations"):
                self.fatte.add(p[0])
            if s == "ROTTA":
                raise RuntimeError("migrazione rotta")
            return _R()
    with tempfile.TemporaryDirectory() as d:
        Path(d, "001_a.sql").write_text("SELECT 1")
        Path(d, "002_b.sql").write_text("SELECT 2")
        c = C()
        assert D.migrate(c, Path(d)) == ["002_b.sql"]
        sq = [s for s, _ in c.q]
        assert sq[0].startswith("SELECT pg_try_advisory_lock") and sq[-1].startswith("SELECT pg_advisory_unlock")
        assert sq.index("SELECT name FROM schema_migrations") > 0               # elenco riletto dopo il lucchetto
        Path(d, "003_c.sql").write_text("ROTTA")
        c = C()
        try:
            D.migrate(c, Path(d))
            assert False, "doveva fallire"
        except RuntimeError:
            pass
        assert c.q[-1][0].startswith("SELECT pg_advisory_unlock")              # lucchetto liberato anche se fallisce
