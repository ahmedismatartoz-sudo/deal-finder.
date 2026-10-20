"""Abbonamento a pagamento con Stripe (carta di credito) per chi si iscrive da solo, per esempio con Google.

Variabili da mettere su Render (le chiavi le inserisce il titolare, mai in chat):
  STRIPE_SECRET_KEY       chiave segreta (sk_live_… oppure sk_test_… per le prove)
  STRIPE_WEBHOOK_SECRET   firma degli avvisi di Stripe (whsec_…), dal webhook che punta a /api/stripe/webhook
  STRIPE_PRICE_ID         il prezzo dell'abbonamento creato su Stripe (price_…), mensile o annuale
  ABBONAMENTO_TESTO       testo del prezzo mostrato sul sito, es. "49 € al mese"
  ABBONAMENTO_PROVA_GIORNI  giorni di prova gratuita (0 = nessuna)
  ISCRIZIONE=pagamento    chi entra con Google per la prima volta ha l'account, ma vede le auto solo dopo aver pagato

Il pagamento avviene sulla pagina di Stripe (Checkout): il sito non vede né salva mai i dati della carta.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time

log = logging.getLogger("abbonamento")
API = "https://api.stripe.com/v1"
STATI_OK = ("active", "trialing")


def attivo() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY") and os.environ.get("STRIPE_PRICE_ID"))


def info() -> dict:
    return {"attivo": attivo(), "prezzo": os.environ.get("ABBONAMENTO_TESTO", ""),
            "prova_giorni": int(os.environ.get("ABBONAMENTO_PROVA_GIORNI", "0") or 0)}


def _post(path: str, data: dict, http=None) -> dict:
    if http is None:
        import httpx as http
    c = http
    r = c.post(API + path, data=data, auth=(os.environ["STRIPE_SECRET_KEY"], ""), timeout=20)
    if r.status_code >= 400:
        log.warning("stripe %s -> %s %s", path, r.status_code, r.text[:300])
        raise RuntimeError("Stripe non risponde, riprova tra poco")
    return r.json()


def _get(path: str, params: dict) -> dict:
    import httpx
    r = httpx.get(API + path, params=params, auth=(os.environ["STRIPE_SECRET_KEY"], ""), timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"stripe {path} -> {r.status_code} {r.text[:200]}")
    return r.json()


def non_rinnovare(conn) -> None:
    """STRIPE_NON_RINNOVARE="a@x.it,b@y.it": l'abbonamento resta attivo fino alla fine del mese già pagato,
    poi si chiude da solo. Nessun altro addebito."""
    emails = [e.strip().lower() for e in os.environ.get("STRIPE_NON_RINNOVARE", "").split(",") if "@" in e]
    if not emails or not os.environ.get("STRIPE_SECRET_KEY"):
        return
    for em in emails:
        try:
            row = conn.execute("SELECT stripe_customer FROM dealers WHERE lower(email)=%s", (em,)).fetchone()
            clienti = [row["stripe_customer"]] if row and row.get("stripe_customer") else []
            if not clienti:
                clienti = [c["id"] for c in _get("/customers", {"email": em, "limit": 10}).get("data", [])]
            if not clienti:
                log.warning("NON_RINNOVA %s: nessun cliente su Stripe", em)
                continue
            trovati = 0
            for cu in clienti:
                for sub in _get("/subscriptions", {"customer": cu, "status": "all", "limit": 20}).get("data", []):
                    if sub.get("status") not in ("active", "trialing", "past_due"):
                        continue
                    trovati += 1
                    if not sub.get("cancel_at_period_end"):
                        sub = _post(f"/subscriptions/{sub['id']}", {"cancel_at_period_end": "true"})
                    fine = sub.get("current_period_end") or (sub.get("items", {}).get("data") or [{}])[0].get("current_period_end")
                    log.warning("NON_RINNOVA %s: abbonamento %s stato=%s rinnovo_spento=%s attivo_fino=%s", em, sub.get("id"),
                                sub.get("status"), sub.get("cancel_at_period_end"),
                                time.strftime("%Y-%m-%d", time.localtime(fine)) if fine else "?")
            if not trovati:
                log.warning("NON_RINNOVA %s: nessun abbonamento attivo da fermare", em)
        except Exception as e:
            log.warning("NON_RINNOVA %s: errore %s", em, str(e)[:300])


def checkout(dealer: dict, base: str, http=None) -> str:
    """Pagina di pagamento Stripe per abbonarsi: ritorna l'indirizzo dove mandare il cliente."""
    data = {"mode": "subscription", "line_items[0][price]": os.environ["STRIPE_PRICE_ID"],
            "line_items[0][quantity]": "1", "client_reference_id": str(dealer["id"]),
            "success_url": base + "/#/abbonamento/ok", "cancel_url": base + "/#/abbonamento/annullato",
            "allow_promotion_codes": "true", "locale": "it",
            "subscription_data[metadata][dealer_id]": str(dealer["id"])}
    if dealer.get("stripe_customer"):
        data["customer"] = dealer["stripe_customer"]
    elif dealer.get("email"):
        data["customer_email"] = dealer["email"]
    giorni = info()["prova_giorni"]
    if giorni > 0:
        data["subscription_data[trial_period_days]"] = str(giorni)
    return _post("/checkout/sessions", data, http)["url"]


def portale(customer: str, base: str, http=None) -> str:
    """Pagina Stripe dove il cliente cambia carta, scarica le fatture o disdice."""
    return _post("/billing_portal/sessions", {"customer": customer, "return_url": base + "/#/profilo"}, http)["url"]


def firma_valida(payload: bytes, header: str | None, secret: str | None = None, tolleranza: int = 600) -> bool:
    """Controllo della firma Stripe-Signature (t=…,v1=…): solo gli avvisi veri di Stripe cambiano gli abbonamenti."""
    secret = secret or os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret or not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    firme = [p.split("=", 1)[1] for p in header.split(",") if p.startswith("v1=")]
    try:
        t = int(parts.get("t", "0"))
    except ValueError:
        return False
    if abs(time.time() - t) > tolleranza:
        return False
    atteso = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(atteso, f) for f in firme)


def applica(conn, evento: dict) -> str:
    """Aggiorna l'account dal messaggio di Stripe. Ritorna cosa ha fatto (per i log)."""
    tipo = evento.get("type", "")
    o = (evento.get("data") or {}).get("object") or {}
    if tipo == "checkout.session.completed":
        did = o.get("client_reference_id")
        if not did:
            return "senza_account"
        conn.execute("""UPDATE dealers SET stripe_customer=%s, abbonamento_stato='active', active=true,
                        permessi = CASE WHEN 'auto' = ANY(permessi) THEN permessi ELSE array_append(permessi, 'auto') END
                        WHERE id=%s""", (o.get("customer"), int(did)))
        conn.commit()
        return f"pagato:{did}"
    if tipo.startswith("customer.subscription."):
        stato = "canceled" if tipo.endswith("deleted") else o.get("status")
        fino = o.get("current_period_end") or ((o.get("items") or {}).get("data") or [{}])[0].get("current_period_end")
        n = conn.execute("""UPDATE dealers SET abbonamento_stato=%s,
                            abbonamento_fino = CASE WHEN %s::bigint IS NULL THEN abbonamento_fino ELSE to_timestamp(%s) END
                            WHERE stripe_customer=%s""", (stato, fino, fino, o.get("customer"))).rowcount
        if not n and (o.get("metadata") or {}).get("dealer_id"):
            conn.execute("UPDATE dealers SET stripe_customer=%s, abbonamento_stato=%s WHERE id=%s",
                         (o.get("customer"), stato, int(o["metadata"]["dealer_id"])))
        conn.commit()
        return f"abbonamento:{stato}"
    if tipo == "invoice.payment_failed":
        conn.execute("UPDATE dealers SET abbonamento_stato='past_due' WHERE stripe_customer=%s", (o.get("customer"),))
        conn.commit()
        return "pagamento_fallito"
    return "ignorato"


def puo_vedere(row: dict | None) -> bool:
    """Chi si è iscritto da solo vede le auto solo con l'abbonamento attivo (o in prova)."""
    if os.environ.get("ABBONAMENTO_OBBLIGATORIO", "1") == "0":
        return True              # pagamento spento da Render: tutti gli account entrano
    if not row or not row.get("richiede_abbonamento"):
        return True
    if row.get("con_password") or row.get("role") == "admin":
        return True              # account creati da noi (con password): accesso completo senza abbonamento
    return (row.get("abbonamento_stato") or "") in STATI_OK


def dump(evento: dict) -> str:
    return json.dumps({"id": evento.get("id"), "type": evento.get("type")})
