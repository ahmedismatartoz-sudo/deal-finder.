"""Postgres VERO per i test (12/11): un database temporaneo con initdb/pg_ctl e una connessione minima via psql.

Qui psycopg non c'è, quindi Conn.execute(sql, params) mette i parametri nel testo (con le virgolette giuste) e lancia
psql: abbastanza per provare le migrazioni e le query dei moduli nuovi sul Postgres vero. Se initdb non c'è (o non si
può avviare) avvia() ritorna None e i test che lo usano non fanno nulla.
"""
from __future__ import annotations

import atexit
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile

_srv: dict = {}


def _bin(nome: str) -> str | None:
    for d in sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if os.path.exists(os.path.join(d, nome)):
            return os.path.join(d, nome)
    return shutil.which(nome)


def _come_postgres(cmd: list[str]) -> list[str]:
    if os.geteuid() == 0:                       # Postgres non parte come root
        return ["runuser", "-u", "postgres", "--"] + cmd
    return cmd


def avvia() -> str | None:
    """Avvia (una volta) il server temporaneo; ritorna la cartella del socket o None."""
    if "dir" in _srv:
        return _srv["dir"]
    initdb, pg_ctl = _bin("initdb"), _bin("pg_ctl")
    if not initdb or not pg_ctl or not shutil.which("psql"):
        _srv["dir"] = None
        return None
    d = tempfile.mkdtemp(prefix="pgtest_")
    try:
        if os.geteuid() == 0:
            os.chmod(d, 0o777)
            shutil.chown(d, "postgres")
        dati = os.path.join(d, "dati")
        subprocess.run(_come_postgres([initdb, "-D", dati, "-A", "trust", "-U", "postgres"]), check=True,
                       capture_output=True, timeout=120)
        subprocess.run(_come_postgres([pg_ctl, "-D", dati, "-w", "-l", os.path.join(d, "log"), "-o",
                                       f"-k {d} -c listen_addresses='' -p 54329", "start"]), check=True,
                       capture_output=True, timeout=120)
    except Exception:
        _srv["dir"] = None
        return None
    _srv["dir"] = d
    atexit.register(lambda: subprocess.run(_come_postgres([pg_ctl, "-D", os.path.join(d, "dati"), "-m", "immediate", "stop"]),
                                           capture_output=True))
    return d


def nuovo_db(nome: str) -> "Conn | None":
    d = avvia()
    if not d:
        return None
    psql(d, "postgres", f"DROP DATABASE IF EXISTS {nome}")
    psql(d, "postgres", f"CREATE DATABASE {nome}")
    return Conn(d, nome)


def psql(d: str, db: str, sql: str) -> str:
    r = subprocess.run(["psql", "-h", d, "-p", "54329", "-U", "postgres", "-d", db, "-X", "-q", "-t", "-A",
                        "-v", "ON_ERROR_STOP=1", "-c", sql], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:500])
    return r.stdout


def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "'\\x" + bytes(v).hex() + "'::bytea"
    if isinstance(v, (list, tuple)):
        return "ARRAY[" + ",".join(_lit(x) for x in v) + "]"
    return "'" + str(v).replace("'", "''") + "'"


def componi(sql: str, params=None) -> str:
    """I parametri nel testo, come farebbe psycopg (%s, %(nome)s, %% = %)."""
    if params is None:
        return sql.replace("%%", "%")
    if isinstance(params, dict):
        out = re.sub(r"%\((\w+)\)s", lambda m: _lit(params[m.group(1)]), sql)
    else:
        it = iter(params)
        out = re.sub(r"%s", lambda m: _lit(next(it)), sql)
    return out.replace("%%", "%")


class _Ris:
    def __init__(self, righe, n):
        self._r, self.rowcount = righe, n

    def fetchone(self):
        return self._r[0] if self._r else None

    def fetchall(self):
        return list(self._r)

    def __iter__(self):
        return iter(self._r)


def _da_json(r: dict) -> dict:
    for k, v in list(r.items()):
        if isinstance(v, str) and v.startswith("\\x") and re.fullmatch(r"\\x[0-9a-f]*", v):
            r[k] = bytes.fromhex(v[2:])
    return r


class Conn:
    """Connessione minima: ogni execute è un psql a sé (commit automatico)."""
    closed = False
    autocommit = True

    def __init__(self, d: str, db: str):
        self.d, self.db, self.eseguite = d, db, []

    def execute(self, sql, params=None):
        testo = componi(sql, params).strip().rstrip(";")
        self.eseguite.append(testo)
        if re.match(r"(?is)^\s*SET\s+LOCAL\b", testo):
            return _Ris([], 0)
        low = testo.lower()
        if low.startswith(("select", "with")):
            out = psql(self.d, self.db, f"SELECT coalesce(json_agg(_r_), '[]') FROM ({testo}) _r_")
            righe = [_da_json(r) for r in json.loads(out.strip() or "[]")]
            return _Ris(righe, len(righe))
        if re.search(r"(?is)\breturning\b", testo):
            out = psql(self.d, self.db, f"WITH _r_ AS ({testo}) SELECT coalesce(json_agg(_r_), '[]') FROM _r_")
            righe = [_da_json(r) for r in json.loads(out.strip() or "[]")]
            return _Ris(righe, len(righe))
        r = subprocess.run(["psql", "-h", self.d, "-p", "54329", "-U", "postgres", "-d", self.db, "-X", "-A", "-t",
                            "-v", "ON_ERROR_STOP=1", "-c", testo], capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[:500])
        m = re.search(r"(?m)^(?:UPDATE|DELETE|INSERT \d+) (\d+)\s*$", r.stdout)
        return _Ris([], int(m.group(1)) if m else 0)

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def migra(conn: Conn, *nomi: str) -> None:
    """Applica i file di db/migrations indicati (es. "047_impostazioni_sito.sql")."""
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "migrations")
    for n in nomi:
        psql(conn.d, conn.db, open(os.path.join(base, n)).read())
