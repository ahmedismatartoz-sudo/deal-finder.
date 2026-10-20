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
        # Account commercianti preparati da Render (ACCOUNT_CLIENTI, JSON): si creano o si rimettono
        # attivi con quella password, zona e accessi. [{"email","password","nome","zona","permessi"}]
        import json as _json
        try:
            clienti = _json.loads(os.environ.get("ACCOUNT_CLIENTI") or "[]")
            # account aggiunti dopo (stesso formato), senza dover riscrivere ACCOUNT_CLIENTI
            clienti += _json.loads(os.environ.get("ACCOUNT_NUOVI") or "[]")
            # un account per variabile (ACCOUNT_AGG_<NOME>, stesso formato): si aggiunge senza toccare le altre
            for k in sorted(os.environ):
                if k.startswith("ACCOUNT_AGG_"):
                    v = _json.loads(os.environ[k] or "[]")
                    clienti += v if isinstance(v, list) else [v]
        except ValueError:
            clienti = []
            print("ACCOUNT_CLIENTI non è un JSON valido")
        from .zone import ZONE, tutte
        for c in clienti:
            em, pw = str(c.get("email", "")).strip().lower(), str(c.get("password", "")).strip()
            if "@" in em and c.get("attivo") is False:
                # account da spegnere: disattivato e tutte le sessioni aperte chiuse subito
                row = conn.execute("SELECT id FROM dealers WHERE lower(email)=%s", (em,)).fetchone()
                if row:
                    conn.execute("UPDATE dealers SET active=false WHERE id=%s", (row["id"],))
                    try:
                        from .web import sessions as _sess
                        _sess.revoke_all(conn, row["id"], "account_disattivato")
                    except Exception as e:
                        print("sessioni non chiuse:", str(e)[:120])
                    print("Account cliente disattivato:", em)
                continue
            if "@" not in em or len(pw) < 8:
                continue
            z = c.get("zona") or "milano"
            prov = tutte() if z == "tutte" else list(ZONE.get(z, ZONE["milano"])["province"])
            perm = [x for x in (c.get("permessi") or ["auto"]) if x in ("auto", "incentivi")]
            row = conn.execute("SELECT id FROM dealers WHERE lower(email)=%s", (em,)).fetchone()
            fino = str(c.get("fino") or "").strip()[:10] or None          # "AAAA-MM-GG": accesso fino a quel giorno compreso
            if row:
                conn.execute("UPDATE dealers SET password_hash=%s, active=true, provinces=%s, permessi=%s WHERE id=%s",
                             (hash_password(pw), prov, perm, row["id"]))
                print("Account cliente aggiornato:", em)
            else:
                row = conn.execute(
                    "INSERT INTO dealers (name, email, password_hash, role, provinces, permessi) "
                    "VALUES (%s,%s,%s,'commerciante',%s,%s) RETURNING id",
                    (str(c.get("nome") or em)[:120], em, hash_password(pw), prov, perm)).fetchone()
                conn.execute("INSERT INTO dealer_costs (dealer_id) VALUES (%s)", (row["id"],))
                print("Creato account cliente", em)
            conn.execute("UPDATE dealers SET accesso_fino = CASE WHEN %s::date IS NULL THEN NULL "
                         "ELSE (%s::date + interval '1 day') AT TIME ZONE 'Europe/Rome' END WHERE id=%s", (fino, fino, row["id"]))
            if fino:
                print("Accesso fino al", fino, "per", em)
        conn.commit()
        # ACCOUNT_DISATTIVATI="a@x.it,b@y.it": account spenti (vince su tutto il resto), sessioni chiuse subito
        for em in [e.strip().lower() for e in os.environ.get("ACCOUNT_DISATTIVATI", "").split(",") if "@" in e]:
            row = conn.execute("SELECT id FROM dealers WHERE lower(email)=%s", (em,)).fetchone()
            if not row:
                continue
            conn.execute("UPDATE dealers SET active=false WHERE id=%s", (row["id"],))
            try:
                from .web import sessions as _sess
                _sess.revoke_all(conn, row["id"], "account_disattivato")
            except Exception as e:
                print("sessioni non chiuse:", str(e)[:120])
            print("Account disattivato:", em)
        conn.commit()
    # controllo foto delle auto sul sito (una volta per versione), solo sul sito web
    if os.environ.get("RENDER_SERVICE_TYPE", "web") == "web":
        try:
            import logging
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
            from .pipeline import manuale
            with connect() as conn:
                print("Auto scartate a mano tolte dal sito:", manuale.applica_scarti(conn))
            from .web import controllo_foto
            controllo_foto.once()
        except Exception as e:
            print("controllo foto saltato:", str(e)[:200])
    # Prezzi letti male prima della correzione dei numeri all'italiana ("12.500,00" → 1.250.000): si rileggono
    # dal testo originale del prezzo e l'annuncio torna da valutare. Una volta sola (job_runs "ripara:prezzi:v1").
    try:
        from .core.normalize import parse_int
        from .db import log_job
        with connect() as conn:
            if not conn.execute("SELECT 1 FROM job_runs WHERE job='ripara:prezzi:v1' AND ok").fetchone():
                finish = log_job(conn, "ripara:prezzi:v1")
                n = 0
                for r in conn.execute(r"""SELECT id, price_raw, price_eur FROM listings
                                         WHERE price_raw ~ '\d[.,]\d{1,2}\s*(€|eur|euro)?\s*$'""").fetchall():
                    p = parse_int(r["price_raw"])
                    if p and p != r["price_eur"]:
                        conn.execute("UPDATE listings SET price_eur=%s, stage='nuovo' WHERE id=%s", (p, r["id"]))
                        n += 1
                conn.commit()
                finish(True, {"corretti": n})
                print("Prezzi con decimali corretti:", n)
    except Exception as e:
        print("correzione prezzi saltata:", str(e)[:200])
