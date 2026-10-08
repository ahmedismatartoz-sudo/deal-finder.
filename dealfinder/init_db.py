"""Applica le migrazioni e crea l'amministratore iniziale.

    python -m dealfinder.init_db

ADMIN_EMAIL e ADMIN_PASSWORD (variabili d'ambiente) creano il primo account
amministratore se non esiste.
"""
import os

from .db import connect, migrate
from .web.auth import hash_password

if __name__ == "__main__":
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL non impostata: servizio non configurato, nulla da fare.")
        raise SystemExit(0)
    with connect() as conn:
        applied = migrate(conn)
        print("Migrazioni applicate:", applied or "nessuna (già aggiornato)")
        email, pwd = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
        if email and pwd:
            exists = conn.execute("SELECT 1 FROM dealers WHERE email=%s", (email.lower(),)).fetchone()
            if not exists:
                row = conn.execute(
                    "INSERT INTO dealers (name, email, password_hash, role) VALUES (%s,%s,%s,'admin') RETURNING id",
                    ("Amministratore", email.lower(), hash_password(pwd))).fetchone()
                conn.execute("INSERT INTO dealer_costs (dealer_id) VALUES (%s)", (row["id"],))
                print("Creato amministratore", email)
        conn.commit()
