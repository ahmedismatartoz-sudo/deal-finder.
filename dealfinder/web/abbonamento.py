"""Abbonamento a pagamento con Stripe (carta di credito) per chi si iscrive da solo, per esempio con Google.

Variabili da mettere su Render (le chiavi le inserisce il titolare, mai in chat):
  STRIPE_SECRET_KEY       chiave segreta (sk_live_… oppure sk_test_… per le prove)
  STRIPE_WEBHOOK_SECRET   firma degli avvisi di Stripe (whsec_…), dal webhook che punta a /api/stripe/webhook
  STRIPE_PRICE_ID         (facoltativo) prezzo già creato su Stripe per il piano Base; se manca lo crea il sito
  STRIPE_PRICE_PRO        (facoltativo) prezzo già creato su Stripe per il piano Max (id interno "pro"); se manca lo crea il sito
  ABBONAMENTO_SCONTO_PRIMO_MESE  sconto in % sul primo mese per i nuovi abbonati (predefinito 50, 0 = nessuno)
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


# 3/11 (cliente): due abbonamenti, Base 100 € e Max 200 € al mese (Max = anche gli appuntamenti), −50% il primo mese.
# L'id interno del Max resta "pro" (lo usano il sito e i metadata degli abbonamenti già fatti).
PIANI = {
    "base": {"nome": "Base", "eur": 100, "lookup": "scovo_base_100_mese", "env": "STRIPE_PRICE_ID",
             "cosa": ["Tutte le auto sotto prezzo dei privati in zona", "Guadagno e prezzo di rivendita già calcolati",
                      "Truffe e annunci falsi già scartati"]},
    "pro": {"nome": "Max", "eur": 200, "lookup": "scovo_pro_200_mese", "env": "STRIPE_PRICE_PRO",
            "cosa": ["Tutto quello del Base", "Fissa l'appuntamento: al privato pensiamo noi",
                     "Garanzia di due auto al mese"]},
}
_CACHE: dict = {}


def attivo() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def sconto_primo_mese() -> int:
    try:
        return max(0, min(100, int(os.environ.get("ABBONAMENTO_SCONTO_PRIMO_MESE", "50") or 0)))
    except ValueError:
        return 0


def info() -> dict:
    sc = sconto_primo_mese()
    return {"attivo": attivo(), "prezzo": os.environ.get("ABBONAMENTO_TESTO", ""),
            "prova_giorni": int(os.environ.get("ABBONAMENTO_PROVA_GIORNI", "0") or 0), "sconto_primo_mese": sc,
            "piani": [{"id": k, "nome": v["nome"], "eur": v["eur"], "primo_mese": round(v["eur"] * (100 - sc) / 100),
                       "cosa": v["cosa"]} for k, v in PIANI.items()]}


def _prezzo(piano: str, http=None) -> str:
    """Prezzo Stripe del piano: da Render se c'è, altrimenti quello creato dal sito (cercato per lookup_key)."""
    p = PIANI[piano]
    if piano in _CACHE:
        return _CACHE[piano]
    if os.environ.get(p["env"]):
        try:                       # 3/11: il prezzo vecchio su Render (80 €) non vale più: si usa solo se è giusto
            if _get(f"/prices/{os.environ[p['env']]}", {}).get("unit_amount") == p["eur"] * 100:
                _CACHE[piano] = os.environ[p["env"]]
                return _CACHE[piano]
        except RuntimeError:
            pass
    trovati = _get("/prices", {"lookup_keys[]": p["lookup"], "active": "true", "limit": 1}).get("data", [])
    if trovati:
        _CACHE[piano] = trovati[0]["id"]
    else:
        _CACHE[piano] = _post("/prices", {"currency": "eur", "unit_amount": str(p["eur"] * 100),
                                         "recurring[interval]": "month", "lookup_key": p["lookup"],
                                         "product_data[name]": f"Scovo {p['nome']}"}, http)["id"]
        log.warning("STRIPE creato il prezzo %s per il piano %s", _CACHE[piano], piano)
    return _CACHE[piano]


def _coupon(pct: int, http=None) -> str:
    """Buono "primo mese" (vale solo sulla prima fattura). Lo crea una volta sola, con un nome fisso."""
    cid = f"SCOVO_PRIMO_MESE_{pct}"
    if _CACHE.get("coupon") == cid:
        return cid
    try:
        _get(f"/coupons/{cid}", {})
    except RuntimeError:
        _post("/coupons", {"id": cid, "percent_off": str(pct), "duration": "once",
                           "name": f"-{pct}% il primo mese"}, http)
        log.warning("STRIPE creato il buono %s", cid)
    _CACHE["coupon"] = cid
    return cid


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
                log.warning("NON_RINNOVA %s: nessun cliente su Stripe", _imp(em))
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
                    log.warning("NON_RINNOVA %s: abbonamento %s stato=%s rinnovo_spento=%s attivo_fino=%s", _imp(em), sub.get("id"),
                                sub.get("status"), sub.get("cancel_at_period_end"),
                                time.strftime("%Y-%m-%d", time.localtime(fine)) if fine else "?")
            if not trovati:
                log.warning("NON_RINNOVA %s: nessun abbonamento attivo da fermare", _imp(em))
        except Exception as e:
            log.warning("NON_RINNOVA %s: errore %s", _imp(em), str(e)[:300])


def prova_pronto() -> None:
    """All'avvio: controlla che la chiave possa leggere/creare i prezzi e il buono (log STRIPE PRONTO o STRIPE ERRORE)."""
    if not attivo():
        log.warning("STRIPE ERRORE: STRIPE_SECRET_KEY non c'è su Render")
        return
    out = {}
    for k in PIANI:
        try:
            pid = _prezzo(k)
            pr = _get(f"/prices/{pid}", {})
            out[k] = f"{pid} {(pr.get('unit_amount') or 0) / 100:.0f}€/{(pr.get('recurring') or {}).get('interval')}"
        except Exception as e:
            out[k] = "ERRORE " + str(e)[:200]
    sc = sconto_primo_mese()
    try:
        out["buono"] = _coupon(sc) if sc else "nessuno"
    except Exception as e:
        out["buono"] = "ERRORE " + str(e)[:200]
    log.warning("STRIPE %s %s", "ERRORE" if any("ERRORE" in str(v) for v in out.values()) else "PRONTO", json.dumps(out))


def checkout(dealer: dict, base: str, http=None, piano: str = "base") -> str:
    """Pagina di pagamento Stripe per abbonarsi: ritorna l'indirizzo dove mandare il cliente."""
    piano = "pro" if piano == "max" else (piano if piano in PIANI else "base")
    data = {"mode": "subscription", "line_items[0][price]": _prezzo(piano, http),
            "line_items[0][quantity]": "1", "client_reference_id": str(dealer["id"]),
            "success_url": base + "/#/abbonamento/ok", "cancel_url": base + "/#/abbonamento/annullato",
            "locale": "it", "metadata[piano]": piano,
            "subscription_data[metadata][dealer_id]": str(dealer["id"]), "subscription_data[metadata][piano]": piano}
    sc = sconto_primo_mese()
    if sc and not dealer.get("stripe_customer"):
        data["discounts[0][coupon]"] = _coupon(sc, http)      # Stripe: buono e codici promo non insieme
    else:
        data["allow_promotion_codes"] = "true"
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


STATI_PRIORITA = ("past_due", "unpaid", "incomplete", "incomplete_expired", "canceled", "paused")


def tolleranza_giorni() -> int:
    """Giorni in cui chi ha un pagamento non riuscito (past_due) tiene accesso e appuntamenti (Stripe riprova)."""
    try:
        return max(0, int(os.environ.get("PAGAMENTO_TOLLERANZA_GIORNI", "3") or 0))
    except ValueError:
        return 3


def piano_di(sub: dict) -> str | None:
    """Piano dell'abbonamento dal PREZZO attuale (cambia col Billing Portal), non dai metadata di quando si è pagato.
    lookup_key scovo_pro_200_mese (o STRIPE_PRICE_PRO) = Max; scovo_base_100_mese (o STRIPE_PRICE_ID) = Base."""
    for it in ((sub.get("items") or {}).get("data") or []):
        pr = it.get("price") or it.get("plan") or {}
        pid, lk = pr.get("id"), pr.get("lookup_key")
        for k, p in PIANI.items():
            if (lk and lk == p["lookup"]) or (pid and pid in (os.environ.get(p["env"]), _CACHE.get(k))):
                return k
    return (sub.get("metadata") or {}).get("piano") or None


def _evento_nuovo(conn, evento: dict) -> bool:
    """Idempotenza: ogni evento Stripe (id evt_…) si applica una volta sola."""
    if not evento.get("id"):
        return True
    r = conn.execute("INSERT INTO stripe_eventi (id, tipo, created) VALUES (%s,%s,%s) ON CONFLICT (id) DO NOTHING "
                     "RETURNING id", (evento["id"], evento.get("type"), evento.get("created"))).fetchone()
    return bool(r)


def _dealer_di(conn, customer: str | None, metadata: dict | None, sid: str | None = None) -> int | None:
    md = metadata or {}
    if str(md.get("dealer_id") or "").isdigit():
        return int(md["dealer_id"])
    if sid:
        r = conn.execute("SELECT dealer_id FROM abbonamenti WHERE subscription_id=%s", (sid,)).fetchone()
        if r and r.get("dealer_id"):
            return r["dealer_id"]
    if customer:
        r = conn.execute("SELECT id FROM dealers WHERE stripe_customer=%s", (customer,)).fetchone()
        if r:
            return r["id"]
    return None


def _salva_abbonamento(conn, sid: str, did: int | None, customer: str | None, stato: str | None, piano: str | None,
                       price_id: str | None, fino, created: int) -> bool:
    """Stato per abbonamento. Un evento più vecchio di quello già applicato (Stripe non garantisce l'ordine) non
    sovrascrive nulla: ritorna False."""
    r = conn.execute(
        """INSERT INTO abbonamenti (subscription_id, dealer_id, customer, stato, piano, price_id, fino, past_due_dal,
                                    evento_created, aggiornato_at)
           VALUES (%(sid)s, %(did)s, %(cu)s, %(st)s, %(pi)s, %(pr)s, to_timestamp(%(fino)s::bigint),
                   CASE WHEN %(st)s = 'past_due' THEN now() END, %(cr)s, now())
           ON CONFLICT (subscription_id) DO UPDATE SET
               dealer_id = COALESCE(EXCLUDED.dealer_id, abbonamenti.dealer_id),
               customer = COALESCE(EXCLUDED.customer, abbonamenti.customer),
               stato = COALESCE(EXCLUDED.stato, abbonamenti.stato),
               piano = COALESCE(EXCLUDED.piano, abbonamenti.piano),
               price_id = COALESCE(EXCLUDED.price_id, abbonamenti.price_id),
               fino = COALESCE(EXCLUDED.fino, abbonamenti.fino),
               past_due_dal = CASE WHEN COALESCE(EXCLUDED.stato, abbonamenti.stato) = 'past_due'
                                   THEN COALESCE(abbonamenti.past_due_dal, now()) END,
               evento_created = EXCLUDED.evento_created, aggiornato_at = now()
           WHERE abbonamenti.evento_created <= EXCLUDED.evento_created
           RETURNING subscription_id""",
        {"sid": sid, "did": did, "cu": customer, "st": stato, "pi": piano, "pr": price_id, "fino": fino,
         "cr": int(created or 0)}).fetchone()
    return bool(r)


def _ora():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def stato_complessivo(righe: list[dict], ora=None) -> dict | None:
    """Dallo stato di TUTTI gli abbonamenti dell'account: stato da mostrare, accesso, piano Max, fine tolleranza."""
    from datetime import timedelta
    if not righe:
        return None
    ora = ora or _ora()
    g = timedelta(days=tolleranza_giorni())
    validi = [r for r in righe if r.get("stato") in STATI_OK]
    toll = [r for r in righe if r.get("stato") == "past_due" and r.get("past_due_dal") and r["past_due_dal"] + g > ora]
    if validi:
        stato = "active" if any(r["stato"] == "active" for r in validi) else "trialing"
    elif toll:
        stato = "past_due"
    else:
        stati = [r.get("stato") for r in righe]
        stato = next((s for s in STATI_PRIORITA if s in stati), stati[0])
    buoni = validi + toll
    fini = [r["fino"] for r in (buoni or righe) if r.get("fino")]
    return {"stato": stato, "accesso": bool(buoni), "max": any(r.get("piano") == "pro" for r in buoni),
            "fino": max(fini) if fini else None,
            "tolleranza": (min(r["past_due_dal"] for r in toll) + g) if (toll and not validi) else None}


def _ricalcola(conn, did: int | None) -> dict | None:
    """Aggiorna l'account dallo stato di tutti i suoi abbonamenti. Non tocca mai `active`: un account spento
    dall'amministratore resta spento anche se paga."""
    if not did:
        return None
    righe = conn.execute("SELECT subscription_id, stato, piano, fino, past_due_dal FROM abbonamenti WHERE dealer_id=%s",
                         (did,)).fetchall()
    s = stato_complessivo([dict(r) for r in righe])
    if not s:
        return None
    conn.execute("UPDATE dealers SET abbonamento_stato=%s, abbonamento_fino=COALESCE(%s, abbonamento_fino), "
                 "abbonamento_tolleranza=%s WHERE id=%s", (s["stato"], s["fino"], s["tolleranza"], did))
    if s["accesso"]:
        conn.execute("UPDATE dealers SET permessi = array_append(COALESCE(permessi, '{}'), 'auto') "
                     "WHERE id=%s AND NOT ('auto' = ANY(COALESCE(permessi, '{}')))", (did,))
    if s["max"]:
        conn.execute("UPDATE dealers SET permessi = array_append(COALESCE(permessi, '{}'), 'appuntamenti'), "
                     "appuntamenti_da_stripe = true WHERE id=%s AND NOT ('appuntamenti' = ANY(COALESCE(permessi, '{}')))",
                     (did,))
    else:
        # si toglie solo se l'aveva dato Stripe (non quello dato a mano dal pannello o da PERMESSO_APPUNTAMENTI)
        conn.execute("UPDATE dealers SET permessi = array_remove(permessi, 'appuntamenti'), appuntamenti_da_stripe = false "
                     "WHERE id=%s AND appuntamenti_da_stripe", (did,))
    return s


def _fine_periodo(o: dict):
    return o.get("current_period_end") or ((o.get("items") or {}).get("data") or [{}])[0].get("current_period_end")


def _price_id(o: dict) -> str | None:
    it = ((o.get("items") or {}).get("data") or [{}])[0]
    return (it.get("price") or it.get("plan") or {}).get("id")


def _sincronizza_customer(conn, customer: str, did: int, leggi=None) -> int:
    """Clienti di prima della tabella abbonamenti: alla prima notizia si rileggono da Stripe TUTTI i loro abbonamenti,
    così la disdetta di uno non chiude l'accesso di chi paga l'altro."""
    if not customer or (leggi is None and not attivo()):
        return 0
    if conn.execute("SELECT 1 FROM abbonamenti WHERE customer=%s LIMIT 1", (customer,)).fetchone():
        return 0
    n = 0
    try:
        for sub in (leggi or _get)("/subscriptions", {"customer": customer, "status": "all", "limit": 20}).get("data", []):
            n += _salva_abbonamento(conn, sub["id"], did, customer, sub.get("status"), piano_di(sub), _price_id(sub),
                                    _fine_periodo(sub), 0)
    except Exception as e:
        log.warning("STRIPE abbonamenti di %s non riletti: %s", customer, str(e)[:200])
    return n


def applica(conn, evento: dict, leggi=None) -> str:
    """Aggiorna l'account dal messaggio di Stripe, in una sola transazione. Ritorna cosa ha fatto (per i log)."""
    if not _evento_nuovo(conn, evento):
        conn.rollback()
        return "doppione"
    tipo = evento.get("type", "")
    o = (evento.get("data") or {}).get("object") or {}
    created = int(evento.get("created") or 0)
    esito = "ignorato"
    if tipo == "checkout.session.completed":
        did = o.get("client_reference_id")
        if not str(did or "").isdigit():
            esito = "senza_account"
        else:
            did = int(did)
            cu = o.get("customer")
            conn.execute("UPDATE dealers SET stripe_customer = COALESCE(stripe_customer, %s) WHERE id=%s", (cu, did))
            sid = o.get("subscription") if isinstance(o.get("subscription"), str) else (o.get("subscription") or {}).get("id")
            if sid and o.get("payment_status", "paid") in ("paid", "no_payment_required"):
                stato = "trialing" if o.get("payment_status") == "no_payment_required" else "active"
                piano = (o.get("metadata") or {}).get("piano")
                piano = "pro" if piano in ("pro", "max") else (piano if piano in PIANI else None)
                _salva_abbonamento(conn, sid, did, cu, stato, piano, None, None, created)
            # senza id dell'abbonamento lo stato arriva con customer.subscription.* (mai un abbonamento "finto")
            _ricalcola(conn, did)
            esito = f"pagato:{did}"
    elif tipo.startswith("customer.subscription."):
        sid, cu = o.get("id"), o.get("customer")
        stato = "canceled" if tipo.endswith("deleted") else o.get("status")
        did = _dealer_di(conn, cu, o.get("metadata"), sid)
        if did and cu:
            conn.execute("UPDATE dealers SET stripe_customer = COALESCE(stripe_customer, %s) WHERE id=%s", (cu, did))
        if sid:
            _sincronizza_customer(conn, cu, did, leggi)
            nuovo = _salva_abbonamento(conn, sid, did, cu, stato, piano_di(o), _price_id(o), _fine_periodo(o), created)
        else:
            nuovo = False
        if not nuovo:
            esito = f"vecchio:{stato}"
        else:
            s = _ricalcola(conn, did)
            esito = f"abbonamento:{stato}" + (f":{s['stato']}" if s and s["stato"] != stato else "")
    elif tipo == "invoice.payment_failed":
        sid = o.get("subscription") or (((o.get("parent") or {}).get("subscription_details") or {}).get("subscription"))
        cu = o.get("customer")
        rows = conn.execute(
            "UPDATE abbonamenti SET stato='past_due', past_due_dal=COALESCE(past_due_dal, now()), evento_created=%s, "
            "aggiornato_at=now() WHERE (subscription_id=%s OR (%s::text IS NULL AND customer=%s)) "
            "AND stato IN ('active','trialing') AND evento_created <= %s RETURNING dealer_id",
            (created, sid, sid, cu, created)).fetchall()
        for d in {r["dealer_id"] for r in rows if r.get("dealer_id")}:
            _ricalcola(conn, d)
        esito = "pagamento_fallito"
    try:
        if evento.get("id"):
            conn.execute("UPDATE stripe_eventi SET esito=%s WHERE id=%s", (esito, evento["id"]))
    except Exception:
        pass
    conn.commit()
    return esito


def abbonamento_in_corso(conn, dealer: dict, leggi=None) -> bool:
    """Prima di un nuovo checkout: c'è già un abbonamento attivo, in prova o in tolleranza? (database e Stripe)"""
    if (dealer.get("abbonamento_stato") or "") in STATI_OK:
        return True
    righe = conn.execute("SELECT subscription_id, stato, piano, fino, past_due_dal FROM abbonamenti WHERE dealer_id=%s",
                         (dealer["id"],)).fetchall()
    s = stato_complessivo([dict(r) for r in righe])
    if s and s["accesso"]:
        return True
    cu = dealer.get("stripe_customer")
    if cu and (leggi is not None or attivo()):
        try:
            for sub in (leggi or _get)("/subscriptions", {"customer": cu, "status": "all", "limit": 20}).get("data", []):
                if sub.get("status") in ("active", "trialing", "past_due"):
                    return True
        except Exception as e:
            log.warning("STRIPE controllo abbonamenti di %s non riuscito: %s", cu, str(e)[:200])
            raise RuntimeError("Stripe non risponde, riprova tra poco")
    return False


def puo_vedere(row: dict | None) -> bool:
    """Chi si è iscritto da solo vede le auto solo con l'abbonamento attivo (o in prova, o nei giorni di tolleranza
    dopo un pagamento non riuscito)."""
    if os.environ.get("ABBONAMENTO_OBBLIGATORIO", "1") == "0":
        return True              # pagamento spento da Render: tutti gli account entrano
    if row and row.get("role") == "admin":
        return True
    paga = bool(row and row.get("paga_carta"))   # 10/11: creato da noi ma deve pagare con carta
    if not paga:
        if not row or not row.get("richiede_abbonamento"):
            return True
        if row.get("con_password"):
            return True          # account creati da noi (con password): accesso completo senza abbonamento
    stato = row.get("abbonamento_stato") or ""
    if stato == "past_due" and row.get("abbonamento_tolleranza"):
        return row["abbonamento_tolleranza"] > _ora()
    return stato in STATI_OK


def dump(evento: dict) -> str:
    return json.dumps({"id": evento.get("id"), "type": evento.get("type")})


def _imp(em: str) -> str:
    """9/11 (regola del cliente: mai email nei log): impronta corta dell'indirizzo al posto dell'indirizzo."""
    import hashlib
    return "h:" + hashlib.sha256(str(em or "").strip().lower().encode()).hexdigest()[:12]
