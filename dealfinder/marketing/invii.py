"""4/11: invii esterni (email, SMS, campagne) UNA volta per destinatario e per lavoro, anche dopo un riavvio.

Tabelle della migrazione 031:
- invii (lavoro, destinatario_hash): il destinatario si "prenota" PRIMA di mandare (INSERT ... ON CONFLICT DO
  NOTHING). Se il server si riavvia a metà, chi era già prenotato non riceve una seconda volta: meglio un invio in
  meno che uno doppio. Dopo l'invio riuscito lo stato diventa "inviato".
- disiscritti (email_hash): chi si è disiscritto, è bloccato, è rimbalzato o ha detto no. Escluso da ogni invio e
  da ogni import in Brevo, per sempre (anche se il contatto è stato cancellato da Brevo).
- consensi (canale, contatto_hash): consenso esplicito; gli SMS promozionali partono solo verso chi l'ha dato.
Indirizzi e numeri sono salvati solo come impronta sha256 (normalizzati), mai in chiaro.
"""
from __future__ import annotations

import hashlib
import logging
import re

lg = logging.getLogger("dealfinder.marketing")


def norm_email(e: str | None) -> str:
    return (e or "").strip().lower()


def norm_tel(t: str | None) -> str:
    """Numero italiano senza spazi e prefisso: "+39 348 1234567" e "3481234567" sono lo stesso contatto."""
    d = re.sub(r"\D", "", t or "")
    if d.startswith("0039"):
        d = d[4:]
    elif d.startswith("39") and len(d) > 10:
        d = d[2:]
    return d


def impronta(dest: str, canale: str = "email") -> str:
    v = norm_tel(dest) if canale == "sms" else norm_email(dest)
    return hashlib.sha256(f"{canale}:{v}".encode()).hexdigest()


# ---------- disiscritti ----------
def disiscritti(conn) -> set[str]:
    """Impronte (canale email) di chi non va mai più contattato."""
    return {r["email_hash"] for r in conn.execute("SELECT email_hash FROM disiscritti").fetchall()}


def aggiungi_disiscritti(conn, emails, motivo: str) -> int:
    n = 0
    for e in emails:
        if norm_email(e):
            r = conn.execute("INSERT INTO disiscritti (email_hash, motivo) VALUES (%s,%s) ON CONFLICT DO NOTHING "
                             "RETURNING email_hash", (impronta(e), motivo[:80])).fetchone()
            n += 1 if r else 0
    conn.commit()
    return n


def contatto_bloccato(x: dict) -> bool:
    """Contatto Brevo che non vuole (o non può) ricevere: disiscritto, in blacklist o rimbalzato."""
    return bool(x.get("emailBlacklisted") or x.get("unsubscribed") or x.get("hardBounce"))


def sincronizza_da_brevo(conn, contatti: list[dict]) -> int:
    """I contatti bloccati in Brevo finiscono nella tabella disiscritti (restano esclusi anche se poi vengono
    cancellati da Brevo per fare posto). Restituisce quanti nuovi."""
    return aggiungi_disiscritti(conn, [x["email"] for x in contatti if x.get("email") and contatto_bloccato(x)],
                                "brevo_blacklist")


# ---------- invii una volta ----------
def prenota(conn, lavoro: str, destinatari, canale: str = "email") -> list[str]:
    """Destinatari che possono ricevere QUESTO lavoro adesso: tolti doppioni, disiscritti (per le email) e chi era già
    prenotato per lo stesso lavoro (anche da un avvio precedente). Quelli restituiti vengono prenotati subito."""
    via = disiscritti(conn) if canale == "email" else set()
    out, visti = [], set()
    for d in destinatari:
        h = impronta(d, canale)
        if not (norm_tel(d) if canale == "sms" else norm_email(d)) or h in visti or h in via:
            continue
        visti.add(h)
        r = conn.execute("INSERT INTO invii (lavoro, destinatario_hash, canale) VALUES (%s,%s,%s) "
                         "ON CONFLICT DO NOTHING RETURNING destinatario_hash", (lavoro, h, canale)).fetchone()
        if r:
            out.append(d)
    conn.commit()
    if len(out) < len(visti):
        lg.warning("INVII %s: %d già mandati o prenotati in un avvio precedente, saltati", lavoro, len(visti) - len(out))
    return out


def segna_inviati(conn, lavoro: str, destinatari, canale: str = "email") -> None:
    for d in destinatari:
        conn.execute("UPDATE invii SET stato='inviato', at=now() WHERE lavoro=%s AND destinatario_hash=%s",
                     (lavoro, impronta(d, canale)))
    conn.commit()


def libera(conn, lavoro: str, destinatari, canale: str = "email") -> None:
    """Invio sicuramente NON partito (errore di Brevo prima della spedizione): i prenotati si possono riprovare."""
    for d in destinatari:
        conn.execute("DELETE FROM invii WHERE lavoro=%s AND destinatario_hash=%s AND stato='in_invio'",
                     (lavoro, impronta(d, canale)))
    conn.commit()


# ---------- consensi ----------
def con_consenso(conn, numeri, canale: str = "sms") -> list[str]:
    """Solo i contatti con consenso esplicito e non revocato per questo canale."""
    ok = {r["contatto_hash"] for r in conn.execute(
        "SELECT contatto_hash FROM consensi WHERE canale=%s AND revocato_at IS NULL", (canale,)).fetchall()}
    return [n for n in numeri if impronta(n, canale) in ok]


def registra_consenso(conn, contatto: str, canale: str, fonte: str) -> None:
    conn.execute("INSERT INTO consensi (canale, contatto_hash, fonte) VALUES (%s,%s,%s) ON CONFLICT (canale, "
                 "contatto_hash) DO UPDATE SET fonte=EXCLUDED.fonte, dato_at=now(), revocato_at=NULL",
                 (canale, impronta(contatto, canale), fonte[:120]))
    conn.commit()


def revoca_consenso(conn, contatto: str, canale: str) -> None:
    conn.execute("UPDATE consensi SET revocato_at=now() WHERE canale=%s AND contatto_hash=%s",
                 (canale, impronta(contatto, canale)))
    conn.commit()
