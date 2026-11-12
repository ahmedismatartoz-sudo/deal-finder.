"""Postgres VERO per i test che devono eseguire l'SQL (migrazioni, query): un cluster temporaneo creato con initdb,
fermato e cancellato alla fine. Se Postgres non è installato, avvia() restituisce None e il test lo dice e si ferma.

    with pg_vero.avvia() as pg:          # None se non disponibile
        pg.sql("SELECT 1")               # righe come dizionari (valori in testo, NULL = None)
        pg.file("db/migrations/049_x.sql")

Da root il server gira come utente "postgres" (Postgres non parte come root): la cartella del cluster sta in /tmp,
perché la cartella di lavoro di root non è leggibile da quell'utente."""
from __future__ import annotations

import contextlib
import csv
import glob
import io
import os
import shutil
import subprocess
import tempfile

PORTA = 55439


def _bin() -> str | None:
    for d in sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if os.path.exists(os.path.join(d, "initdb")):
            return d
    return None


class PG:
    def __init__(self, cartella: str, db: str = "prova"):
        self.cartella, self.db = cartella, db

    def _psql(self, args: list[str], db: str | None = None) -> str:
        r = subprocess.run(["psql", "-h", self.cartella, "-p", str(PORTA), "-U", "postgres", "-d", db or self.db, "-X",
                            "-q", "-v", "ON_ERROR_STOP=1", *args], capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[:500])
        return r.stdout

    def sql(self, testo: str) -> list[dict]:
        out = self._psql(["--csv", "-c", testo]).strip()
        if not out:
            return []
        return [{k: (v if v != "" else None) for k, v in r.items()} for r in csv.DictReader(io.StringIO(out))]

    def file(self, percorso: str) -> None:
        self._psql(["-f", percorso])


@contextlib.contextmanager
def avvia():
    b = _bin()
    if not b or not shutil.which("psql"):
        yield None
        return
    root = os.geteuid() == 0
    cartella = tempfile.mkdtemp(prefix="pgvero_", dir="/tmp")
    dati = os.path.join(cartella, "dati")

    def come_pg(cmd: list[str]) -> int:
        if root:
            return subprocess.call(["su", "postgres", "-s", "/bin/sh", "-c", " ".join(cmd)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if root:
            shutil.chown(cartella, "postgres")
        if come_pg([f"{b}/initdb", "-D", dati, "-A", "trust", "-U", "postgres"]):
            yield None
            return
        if come_pg([f"{b}/pg_ctl", "-D", dati, "-w", "-l", os.path.join(cartella, "log"), "-o",
                    f"'-k {cartella} -p {PORTA} -c listen_addresses='", "start"]):
            yield None
            return
        pg = PG(cartella)
        try:
            pg._psql(["-c", "CREATE DATABASE prova"], db="postgres")
            yield pg
        finally:
            come_pg([f"{b}/pg_ctl", "-D", dati, "-m", "immediate", "stop"])
    finally:
        shutil.rmtree(cartella, ignore_errors=True)
