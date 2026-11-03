"""Applica le migrazioni e crea l'amministratore iniziale.

    python -m dealfinder.init_db

ADMIN_EMAIL e ADMIN_PASSWORD (variabili d'ambiente) creano il primo account
amministratore se non esiste.

3/11 notte (ricostruzione):
- le migrazioni e gli account girano in UN solo processo: il sito web (RENDER_SERVICE_TYPE=web, o in locale).
  Nel cron (facebook-2) si saltano, salvo INIT_DB_SEMPRE=1. Le migrazioni hanno anche un lucchetto Postgres.
- gli account preparati su Render (ACCOUNT_CLIENTI, ACCOUNT_NUOVI, ACCOUNT_AGG_*, ADMIN_*, PERMESSO_APPUNTAMENTI)
  NON si riscrivono a ogni avvio: si creano se mancano; poi si applica solo il campo che è cambiato su Render
  rispetto all'avvio precedente (impronta per campo nella tabella account_preparati, firmata con SECRET_KEY, mai la
  password in chiaro). Password, attivo/disattivo, zona, permessi e scadenza decisi dal pannello o da Stripe restano.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os

from .web.auth import hash_password

CAMPI_ACCOUNT = ("password", "zona", "permessi", "fino", "attivo")


def _segreto() -> bytes:
    from .web.auth import _secret
    try:
        return _secret()
    except RuntimeError:
        return b"init-db-senza-secret-key"


def impronta(chiave: str, campo: str, valore, segreto: bytes | None = None) -> str:
    """Impronta di un campo (HMAC con SECRET_KEY): dice se è cambiato, senza poter ricostruire il valore."""
    msg = f"{chiave}|{campo}|{json.dumps(valore, sort_keys=True, default=str)}".encode()
    return hmac.new(segreto or _segreto(), msg, hashlib.sha256).hexdigest()[:32]


def _impronte_salvate(conn, chiave: str) -> dict | None:
    r = conn.execute("SELECT impronte FROM account_preparati WHERE chiave=%s", (chiave,)).fetchone()
    if not r:
        return None
    v = r["impronte"]
    return json.loads(v) if isinstance(v, str) else dict(v or {})


def _salva_impronte(conn, chiave: str, imp: dict) -> None:
    conn.execute("INSERT INTO account_preparati (chiave, impronte) VALUES (%s,%s) ON CONFLICT (chiave) DO UPDATE "
                 "SET impronte=EXCLUDED.impronte, aggiornato_at=now()", (chiave, json.dumps(imp, sort_keys=True)))


def _chiudi_sessioni(conn, did: int, why: str) -> None:
    try:
        from .web import sessions as _sess
        _sess.revoke_all(conn, did, why)
    except Exception as e:
        print("sessioni non chiuse:", str(e)[:120])


def _province(z: str) -> list[str]:
    from .zone import ZONE, tutte
    return tutte() if z == "tutte" else list(ZONE.get(z, ZONE["milano"])["province"])


def prepara_account(conn, c: dict, segreto: bytes | None = None) -> str:
    """Un account di ACCOUNT_CLIENTI. Ritorna l'esito (per il log, senza dati segreti)."""
    em = str(c.get("email", "")).strip().lower()
    pw = str(c.get("password", "")).strip()
    if "@" not in em:
        return "saltato"
    z = c.get("zona") or "milano"
    perm = sorted({x for x in (c.get("permessi") or ["auto"]) if x in ("auto", "incentivi", "appuntamenti")})
    fino = str(c.get("fino") or "").strip()[:10] or None          # "AAAA-MM-GG": accesso fino a quel giorno compreso
    attivo = c.get("attivo") is not False
    nuove = {k: impronta(em, k, v, segreto) for k, v in
             (("password", pw), ("zona", z), ("permessi", perm), ("fino", fino), ("attivo", attivo))}
    salvate = _impronte_salvate(conn, em)
    row = conn.execute("SELECT id FROM dealers WHERE lower(email)=%s", (em,)).fetchone()
    if not attivo:
        # account da spegnere: si spegne quando la richiesta è nuova (non a ogni avvio: l'admin può riaccenderlo)
        esito = "spento_gia"
        if row and (salvate is None or salvate.get("attivo") != nuove["attivo"]):
            conn.execute("UPDATE dealers SET active=false, disattivato_da_admin=true WHERE id=%s", (row["id"],))
            _chiudi_sessioni(conn, row["id"], "account_disattivato")
            esito = "disattivato"
        if row:
            _salva_impronte(conn, em, nuove)
        return esito
    if len(pw) < 8:
        return "saltato"
    if not row:
        row = conn.execute(
            "INSERT INTO dealers (name, email, password_hash, role, provinces, permessi) "
            "VALUES (%s,%s,%s,'commerciante',%s,%s) RETURNING id",
            (str(c.get("nome") or em)[:120], em, hash_password(pw), _province(z), perm)).fetchone()
        conn.execute("INSERT INTO dealer_costs (dealer_id) VALUES (%s)", (row["id"],))
        cambiati = ["fino"]
        esito = "creato"
    elif salvate is None:
        # primo avvio con le impronte: si prende nota di quello che c'è su Render, senza toccare l'account
        _salva_impronte(conn, em, nuove)
        return "registrato"
    else:
        cambiati = [k for k in CAMPI_ACCOUNT if salvate.get(k) != nuove[k]]
        if not cambiati:
            return "invariato"
        esito = "aggiornato:" + ",".join(cambiati)
    did = row["id"]
    if esito != "creato":
        if "password" in cambiati:
            conn.execute("UPDATE dealers SET password_hash=%s WHERE id=%s", (hash_password(pw), did))
            _chiudi_sessioni(conn, did, "password_cambiata")
        if "zona" in cambiati:
            conn.execute("UPDATE dealers SET provinces=%s WHERE id=%s", (_province(z), did))
        if "permessi" in cambiati:
            # il permesso "appuntamenti" dato da Stripe (piano Max) o dal pannello non si toglie
            conn.execute("UPDATE dealers SET permessi = (SELECT array_agg(DISTINCT x) FROM unnest(%s::text[] || "
                         "CASE WHEN 'appuntamenti' = ANY(COALESCE(permessi, '{}')) THEN ARRAY['appuntamenti'] "
                         "ELSE '{}'::text[] END) x) WHERE id=%s", (perm, did))
        if "attivo" in cambiati:          # su Render era "attivo": false e ora non più: si riaccende
            conn.execute("UPDATE dealers SET active=true, disattivato_da_admin=false WHERE id=%s", (did,))
    if "fino" in cambiati:
        conn.execute("UPDATE dealers SET accesso_fino = CASE WHEN %s::date IS NULL THEN NULL "
                     "ELSE (%s::date + interval '1 day') AT TIME ZONE 'Europe/Rome' END WHERE id=%s", (fino, fino, did))
    _salva_impronte(conn, em, nuove)
    return esito


def prepara_admin(conn, email: str, pwd: str, segreto: bytes | None = None) -> str:
    """Amministratore da ADMIN_EMAIL/ADMIN_PASSWORD: creato se manca; la password si riscrive solo se su Render è
    cambiata rispetto all'avvio precedente."""
    chiave = "admin:" + email
    nuove = {"password": impronta(chiave, "password", pwd, segreto)}
    row = conn.execute("SELECT id FROM dealers WHERE lower(email)=%s", (email,)).fetchone()
    if not row:
        row = conn.execute(
            "INSERT INTO dealers (name, email, password_hash, role) VALUES (%s,%s,%s,'admin') RETURNING id",
            ("Amministratore", email, hash_password(pwd))).fetchone()
        conn.execute("INSERT INTO dealer_costs (dealer_id) VALUES (%s)", (row["id"],))
        _salva_impronte(conn, chiave, nuove)
        return "creato"
    salvate = _impronte_salvate(conn, chiave)
    conn.execute("UPDATE dealers SET role='admin', active=true, disattivato_da_admin=false WHERE id=%s", (row["id"],))
    if salvate is not None and salvate.get("password") != nuove["password"]:
        conn.execute("UPDATE dealers SET password_hash=%s WHERE id=%s", (hash_password(pwd), row["id"]))
        _chiudi_sessioni(conn, row["id"], "password_cambiata")
        esito = "password_aggiornata"
    else:
        esito = "invariato" if salvate is not None else "registrato"
    _salva_impronte(conn, chiave, nuove)
    return esito


def permesso_appuntamenti(conn, em: str, segreto: bytes | None = None) -> str:
    """PERMESSO_APPUNTAMENTI: il permesso si dà UNA volta (poi lo gestiscono pannello e Stripe)."""
    chiave = "appuntamenti:" + em
    if _impronte_salvate(conn, chiave) is not None:
        return "gia_dato"
    r = conn.execute("SELECT id FROM dealers WHERE lower(email)=%s", (em,)).fetchone()
    if not r:
        return "account non trovato"
    conn.execute("""UPDATE dealers SET permessi = array_append(COALESCE(permessi, '{}'), 'appuntamenti')
                    WHERE id=%s AND NOT ('appuntamenti' = ANY(COALESCE(permessi, '{}')))""", (r["id"],))
    _salva_impronte(conn, chiave, {"on": impronta(chiave, "on", True, segreto)})
    return "acceso"


def clienti_da_env(env=None) -> list[dict]:
    env = os.environ if env is None else env
    clienti = json.loads(env.get("ACCOUNT_CLIENTI") or "[]")
    clienti += json.loads(env.get("ACCOUNT_NUOVI") or "[]")        # account aggiunti dopo (stesso formato)
    for k in sorted(env):                                            # un account per variabile (ACCOUNT_AGG_<NOME>)
        if k.startswith("ACCOUNT_AGG_"):
            v = json.loads(env[k] or "[]")
            clienti += v if isinstance(v, list) else [v]
    return clienti


def processo_principale(env=None) -> bool:
    """Migrazioni e account solo nel sito web (o in locale); nel cron solo con INIT_DB_SEMPRE=1."""
    env = os.environ if env is None else env
    return env.get("RENDER_SERVICE_TYPE", "web") == "web" or env.get("INIT_DB_SEMPRE") == "1"


def _maschera(em: str) -> str:
    u, _, d = em.partition("@")
    return (u[:1] + "***@" + d) if d else "***"


def main() -> None:
    from . import regole_log as _rl                  # 3/11 fase 1: impronta delle regole all'avvio
    from .db import connect, migrate
    _rl.registra("init_db:" + os.environ.get("RENDER_SERVICE_TYPE", "web"))
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL non impostata: servizio non configurato, nulla da fare.")
        raise SystemExit(0)
    if not processo_principale():
        print("init_db nel cron: migrazioni, account e Stripe li fa solo il sito web (INIT_DB_SEMPRE=1 per forzare).")
    else:
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
                print("Amministratore", _maschera(email), ":", prepara_admin(conn, email, pwd))
            conn.commit()
            try:
                clienti = clienti_da_env()
            except ValueError:
                clienti = []
                print("ACCOUNT_CLIENTI non è un JSON valido")
            for c in clienti:
                esito = prepara_account(conn, c)
                if esito not in ("saltato", "invariato"):
                    print("Account cliente", _maschera(str(c.get("email", "")).strip().lower()), ":", esito)
            conn.commit()
            try:                                                   # abbonamenti da non rinnovare (STRIPE_NON_RINNOVARE)
                import logging as _lg
                _lg.basicConfig(level=_lg.INFO)
                from .web import abbonamento as _abb
                _abb.non_rinnovare(conn)
                _abb.prova_pronto()
            except Exception as e:
                print("non_rinnovare:", str(e)[:200])
            # PERMESSO_APPUNTAMENTI="a@x.it,b@y.it": accende il tasto "Fissa l'appuntamento" una volta (prove, abbonati Max)
            for em in [e.strip().lower() for e in os.environ.get("PERMESSO_APPUNTAMENTI", "ahmedismatartoz@gmail.com,astrame87@gmail.com").split(",") if "@" in e]:
                print("Appuntamenti per", _maschera(em), ":", permesso_appuntamenti(conn, em), flush=True)
            conn.commit()
            # ACCOUNT_DISATTIVATI="a@x.it,b@y.it": account spenti (vince su tutto il resto), sessioni chiuse subito
            for em in [e.strip().lower() for e in os.environ.get("ACCOUNT_DISATTIVATI", "").split(",") if "@" in e]:
                row = conn.execute("SELECT id FROM dealers WHERE lower(email)=%s", (em,)).fetchone()
                if not row:
                    continue
                conn.execute("UPDATE dealers SET active=false, disattivato_da_admin=true WHERE id=%s", (row["id"],))
                _chiudi_sessioni(conn, row["id"], "account_disattivato")
                print("Account disattivato:", _maschera(em))
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


if __name__ == "__main__":
    main()
