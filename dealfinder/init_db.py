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
        email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
        pwd = (os.environ.get("ADMIN_PASSWORD") or "").strip()
        if not email or not pwd:
            print("ADMIN_EMAIL o ADMIN_PASSWORD non impostate: nessun amministratore creato o aggiornato.")
        elif len(pwd) < 8:
            print("ADMIN_PASSWORD troppo corta (servono almeno 8 caratteri): amministratore non creato.")
        else:
            row = conn.execute("SELECT id FROM dealers WHERE lower(email)=%s", (email,)).fetchone()
            if row:
                # la password su Render è quella che vale: si aggiorna a ogni avvio
                conn.execute("UPDATE dealers SET password_hash=%s, role='admin', active=true WHERE id=%s",
                             (hash_password(pwd), row["id"]))
                print("Amministratore aggiornato:", email)
            else:
                row = conn.execute(
                    "INSERT INTO dealers (name, email, password_hash, role) VALUES (%s,%s,%s,'admin') RETURNING id",
                    ("Amministratore", email, hash_password(pwd))).fetchone()
                conn.execute("INSERT INTO dealer_costs (dealer_id) VALUES (%s)", (row["id"],))
                print("Creato amministratore", email)
        conn.commit()
