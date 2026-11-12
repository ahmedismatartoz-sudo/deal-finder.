import hashlib
import hmac
import json
import time

from dealfinder.web import abbonamento as ab


def _firma(payload: bytes, secret="whsec_prova", t=None):
    t = t or int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def test_firma_stripe():
    p = json.dumps({"type": "checkout.session.completed"}).encode()
    assert ab.firma_valida(p, _firma(p), "whsec_prova")
    assert not ab.firma_valida(p, _firma(p, "altra"), "whsec_prova")
    assert not ab.firma_valida(p + b"x", _firma(p), "whsec_prova")
    assert not ab.firma_valida(p, _firma(p, t=int(time.time()) - 3600), "whsec_prova")      # troppo vecchio
    assert not ab.firma_valida(p, None, "whsec_prova")


def test_chi_puo_vedere():
    assert ab.puo_vedere({"richiede_abbonamento": False, "abbonamento_stato": None})     # account fatti a mano
    assert not ab.puo_vedere({"richiede_abbonamento": True, "abbonamento_stato": None})
    assert ab.puo_vedere({"richiede_abbonamento": True, "abbonamento_stato": "active"})
    assert ab.puo_vedere({"richiede_abbonamento": True, "abbonamento_stato": "trialing"})
    assert not ab.puo_vedere({"richiede_abbonamento": True, "abbonamento_stato": "past_due"})
    assert not ab.puo_vedere({"richiede_abbonamento": True, "abbonamento_stato": "canceled"})


class _R:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.rowcount = len(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FintoStripeDB:
    """dealers, abbonamenti e stripe_eventi in memoria: quanto basta per le query di abbonamento.applica."""

    def __init__(self):
        from datetime import datetime, timezone
        self.now = datetime.now(timezone.utc)
        self.dealers = {7: {"id": 7, "stripe_customer": None, "abbonamento_stato": None, "active": True,
                            "permessi": ["auto"], "appuntamenti_da_stripe": False, "abbonamento_tolleranza": None}}
        self.abb, self.eventi, self.q = {}, set(), []

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, sql, p=()):
        s = " ".join(sql.split())
        self.q.append(s)
        D = self.dealers
        if s.startswith("INSERT INTO stripe_eventi"):
            if p[0] in self.eventi:
                return _R()
            self.eventi.add(p[0])
            return _R([{"id": p[0]}])
        if s.startswith("UPDATE stripe_eventi"):
            return _R()
        if s.startswith("SELECT dealer_id FROM abbonamenti WHERE subscription_id"):
            a = self.abb.get(p[0])
            return _R([{"dealer_id": a["dealer_id"]}] if a else [])
        if s.startswith("SELECT id FROM dealers WHERE stripe_customer"):
            return _R([{"id": d["id"]} for d in D.values() if d["stripe_customer"] == p[0]][:1])
        if s.startswith("UPDATE dealers SET stripe_customer = COALESCE"):
            D[p[1]]["stripe_customer"] = D[p[1]]["stripe_customer"] or p[0]
            return _R()
        if s.startswith("SELECT 1 FROM abbonamenti WHERE customer"):
            return _R([{"x": 1}] if any(a["customer"] == p[0] for a in self.abb.values()) else [])
        if s.startswith("INSERT INTO abbonamenti"):
            from datetime import datetime, timezone
            v = p
            a = self.abb.get(v["sid"])
            fino = datetime.fromtimestamp(v["fino"], timezone.utc) if v["fino"] else None
            if a is None:
                self.abb[v["sid"]] = {"subscription_id": v["sid"], "dealer_id": v["did"], "customer": v["cu"], "stato": v["st"],
                                      "piano": v["pi"], "fino": fino, "evento_created": v["cr"],
                                      "past_due_dal": self.now if v["st"] == "past_due" else None}
                return _R([{"subscription_id": v["sid"]}])
            if not ab.evento_vince(a["stato"], a["evento_created"], v["st"], v["cr"]):     # stessa regola dell'SQL
                return _R()
            a["dealer_id"] = v["did"] or a["dealer_id"]
            a["customer"] = v["cu"] or a["customer"]
            a["stato"] = v["st"] or a["stato"]
            a["piano"] = v["pi"] or a["piano"]
            a["fino"] = fino or a["fino"]
            a["past_due_dal"] = (a["past_due_dal"] or self.now) if a["stato"] == "past_due" else None
            a["evento_created"] = v["cr"]
            return _R([{"subscription_id": v["sid"]}])
        if s.startswith("SELECT subscription_id, stato, piano, fino, past_due_dal FROM abbonamenti WHERE dealer_id"):
            return _R([dict(a) for a in self.abb.values() if a["dealer_id"] == p[0]])
        if s.startswith("UPDATE dealers SET abbonamento_stato"):
            d = D[p[3]]
            d["abbonamento_stato"], d["abbonamento_tolleranza"] = p[0], p[2]
            return _R()
        if s.startswith("UPDATE dealers SET permessi = array_append(COALESCE(permessi, '{}'), 'auto')"):
            if "auto" not in D[p[0]]["permessi"]:
                D[p[0]]["permessi"].append("auto")
            return _R()
        if s.startswith("UPDATE dealers SET permessi = array_append(COALESCE(permessi, '{}'), 'appuntamenti')"):
            if "appuntamenti" not in D[p[0]]["permessi"]:
                D[p[0]]["permessi"].append("appuntamenti")
                D[p[0]]["appuntamenti_da_stripe"] = True
            return _R()
        if s.startswith("UPDATE dealers SET permessi = array_remove(permessi, 'appuntamenti')"):
            d = D[p[0]]
            if d["appuntamenti_da_stripe"]:
                d["permessi"] = [x for x in d["permessi"] if x != "appuntamenti"]
                d["appuntamenti_da_stripe"] = False
            return _R()
        if s.startswith("UPDATE abbonamenti SET stato='past_due'"):
            created, sid, _, cu, _ = p
            out = []
            for a in self.abb.values():
                if (a["subscription_id"] == sid or (sid is None and a["customer"] == cu)) and \
                        a["stato"] in ("active", "trialing") and a["evento_created"] <= created:
                    a["stato"], a["past_due_dal"], a["evento_created"] = "past_due", a["past_due_dal"] or self.now, created
                    out.append({"dealer_id": a["dealer_id"]})
            return _R(out)
        raise AssertionError("query inattesa: " + s[:120])


def _sub(sid, stato, created, lookup="scovo_base_100_mese", evid=None, tipo="customer.subscription.updated", **k):
    o = {"id": sid, "customer": "cus_1", "status": stato, "metadata": {"dealer_id": "7"},
         "items": {"data": [{"price": {"id": "price_x_" + lookup, "lookup_key": lookup}, "current_period_end": 1900000000}]}}
    o.update(k)
    return {"id": evid or f"evt_{sid}_{created}_{stato}", "type": tipo, "created": created, "data": {"object": o}}


def test_eventi_stripe():
    db = FintoStripeDB()
    ev = {"id": "evt_1", "type": "checkout.session.completed", "created": 100,
          "data": {"object": {"client_reference_id": "7", "customer": "cus_1", "subscription": "sub_1", "payment_status": "paid"}}}
    assert ab.applica(db, ev) == "pagato:7"
    assert db.dealers[7]["abbonamento_stato"] == "active" and db.dealers[7]["stripe_customer"] == "cus_1"
    assert ab.applica(db, ev) == "doppione"                                        # stesso evento: niente
    assert ab.applica(db, _sub("sub_1", "canceled", 200, tipo="customer.subscription.deleted")).startswith("abbonamento:canceled")
    assert db.dealers[7]["abbonamento_stato"] == "canceled"
    assert ab.applica(db, {"id": "evt_x", "type": "altro"}) == "ignorato"
    assert not any("active=true" in q or "active = true" in q for q in db.q)       # il pagamento non tocca `active`


def test_pagamento_non_riaccende_account_spento():
    db = FintoStripeDB()
    db.dealers[7]["active"] = False                                                # spento dall'amministratore
    ab.applica(db, _sub("sub_1", "active", 100))
    assert db.dealers[7]["active"] is False and db.dealers[7]["abbonamento_stato"] == "active"


def test_due_abbonamenti_disdetta_di_uno():
    db = FintoStripeDB()
    ab.applica(db, _sub("sub_1", "active", 100))
    ab.applica(db, _sub("sub_2", "active", 110))
    ab.applica(db, _sub("sub_1", "canceled", 200, tipo="customer.subscription.deleted"))
    assert db.dealers[7]["abbonamento_stato"] == "active"                          # l'altro è ancora pagato
    assert ab.puo_vedere({"richiede_abbonamento": True, **db.dealers[7]})


def test_evento_vecchio_ignorato():
    db = FintoStripeDB()
    ab.applica(db, _sub("sub_1", "active", 200))
    assert ab.applica(db, _sub("sub_1", "incomplete", 150, tipo="customer.subscription.created")).startswith("vecchio")
    assert db.dealers[7]["abbonamento_stato"] == "active"


def test_piano_max_segue_il_prezzo_attuale():
    # 12/11: il permesso del Max è automatico (max_valido, letto da permessi_di) e non si scrive in dealers.permessi
    db = FintoStripeDB()
    ab.applica(db, _sub("sub_1", "active", 100, lookup="scovo_pro_200_mese"))
    assert ab.max_valido(db, 7) and "appuntamenti" not in db.dealers[7]["permessi"]
    ab.applica(db, _sub("sub_1", "active", 200, lookup="scovo_base_100_mese", metadata={"dealer_id": "7", "piano": "pro"}))
    assert not ab.max_valido(db, 7)                                                # passato a Base dal portale
    ab.applica(db, _sub("sub_1", "active", 300, lookup="scovo_pro_200_mese", metadata={"dealer_id": "7", "piano": "base"}))
    assert ab.max_valido(db, 7)                                                    # e di nuovo a Max
    ab.applica(db, _sub("sub_1", "unpaid", 400, lookup="scovo_pro_200_mese"))
    assert not ab.max_valido(db, 7)


def test_appuntamenti_dati_a_mano_restano():
    db = FintoStripeDB()
    db.dealers[7]["permessi"] = ["auto", "appuntamenti"]                            # dati dal pannello
    ab.applica(db, _sub("sub_1", "active", 100, lookup="scovo_base_100_mese"))
    ab.applica(db, _sub("sub_1", "canceled", 200, tipo="customer.subscription.deleted"))
    assert "appuntamenti" in db.dealers[7]["permessi"]


def test_pagamento_fallito_tolleranza():
    from datetime import timedelta
    db = FintoStripeDB()
    ab.applica(db, _sub("sub_1", "active", 100, lookup="scovo_pro_200_mese"))
    assert ab.applica(db, {"id": "evt_f", "type": "invoice.payment_failed", "created": 150,
                           "data": {"object": {"customer": "cus_1", "subscription": "sub_1"}}}) == "pagamento_fallito"
    d = db.dealers[7]
    assert d["abbonamento_stato"] == "past_due" and ab.max_valido(db, 7)              # nei giorni di tolleranza resta
    assert ab.puo_vedere({"richiede_abbonamento": True, **d})
    db.abb["sub_1"]["past_due_dal"] = db.now - timedelta(days=10)                      # tolleranza finita
    assert not ab.max_valido(db, 7)                       # 12/11: anche senza nessun evento nuovo (prima restava)
    ab.applica(db, _sub("sub_1", "past_due", 300, lookup="scovo_pro_200_mese"))
    d = db.dealers[7]
    assert not ab.max_valido(db, 7) and not ab.puo_vedere({"richiede_abbonamento": True, **d})


def test_secondo_abbonamento_bloccato():
    db = FintoStripeDB()
    ab.applica(db, _sub("sub_1", "active", 100))
    assert ab.abbonamento_in_corso(db, {"id": 7, "abbonamento_stato": None, "stripe_customer": None})
    db2 = FintoStripeDB()
    leggi = lambda path, params: {"data": [{"id": "sub_9", "status": "trialing"}]}    # noqa: E731 (Stripe finto)
    assert ab.abbonamento_in_corso(db2, {"id": 7, "abbonamento_stato": None, "stripe_customer": "cus_1"}, leggi)
    leggi = lambda path, params: {"data": [{"id": "sub_9", "status": "canceled"}]}    # noqa: E731
    assert not ab.abbonamento_in_corso(db2, {"id": 7, "abbonamento_stato": None, "stripe_customer": "cus_1"}, leggi)


def test_clienti_di_prima_riletti_da_stripe():
    db = FintoStripeDB()
    db.dealers[7]["stripe_customer"] = "cus_1"
    letti = []

    def leggi(path, params):
        letti.append(params["customer"])
        return {"data": [{"id": "sub_vecchio", "status": "active", "customer": "cus_1",
                          "items": {"data": [{"price": {"lookup_key": "scovo_base_100_mese"}}]}}]}
    ab.applica(db, _sub("sub_nuovo", "canceled", 200, tipo="customer.subscription.deleted"), leggi)
    assert letti == ["cus_1"] and db.dealers[7]["abbonamento_stato"] == "active"


def test_checkout_sconto_primo_mese(monkeypatch=None):
    import os
    env_prima = dict(os.environ)          # 3/11: ripristinato alla fine anche se il test fallisce
    try:
        _checkout_sconto_primo_mese()
    finally:
        os.environ.clear()
        os.environ.update(env_prima)


def _checkout_sconto_primo_mese():
    import os
    os.environ.update(STRIPE_SECRET_KEY="sk_test_x", STRIPE_PRICE_ID="price_b", STRIPE_PRICE_PRO="price_p")
    os.environ.pop("ABBONAMENTO_SCONTO_PRIMO_MESE", None)
    ab._CACHE.update(coupon="SCOVO_PRIMO_MESE_50", pro="price_p", base="price_b")
    inviati = []

    class H:
        @staticmethod
        def post(url, data=None, **k):
            inviati.append(data)

            class R:
                status_code = 200
                def json(self): return {"url": "https://checkout"}
            return R()
    assert ab.checkout({"id": 3, "email": "a@b.it"}, "https://x", H, "pro") == "https://checkout"
    d = inviati[-1]
    assert d["line_items[0][price]"] == "price_p" and d["discounts[0][coupon]"] == "SCOVO_PRIMO_MESE_50"
    assert "allow_promotion_codes" not in d and d["subscription_data[metadata][piano]"] == "pro"
    assert [p["primo_mese"] for p in ab.info()["piani"]] == [50, 100]
    assert [p["nome"] for p in ab.info()["piani"]] == ["Base", "Max"]


def test_eventi_nello_stesso_secondo():
    """12/11: Stripe manda subscription.created (incomplete), subscription.updated (active) e checkout.session.completed
    nello stesso secondo e senza ordine: il pagante non deve restare "incomplete"."""
    db = FintoStripeDB()
    ab.applica(db, _sub("sub_1", "active", 100))
    assert ab.applica(db, _sub("sub_1", "incomplete", 100, tipo="customer.subscription.created")).startswith("vecchio")
    assert db.dealers[7]["abbonamento_stato"] == "active"
    # "incomplete" con un secondo in più (orologi diversi) non riporta indietro un abbonamento già pagato
    assert ab.applica(db, _sub("sub_1", "incomplete", 101, tipo="customer.subscription.created")).startswith("vecchio")
    assert db.dealers[7]["abbonamento_stato"] == "active"
    # nell'ordine giusto, stesso secondo: incomplete poi active
    db2 = FintoStripeDB()
    ab.applica(db2, _sub("sub_2", "incomplete", 100, tipo="customer.subscription.created"))
    ab.applica(db2, _sub("sub_2", "active", 100))
    assert db2.dealers[7]["abbonamento_stato"] == "active"


def test_regola_ordine_eventi():
    v = ab.evento_vince
    assert not v("active", 100, "incomplete", 100) and not v("active", 100, "incomplete", 200)
    assert v("incomplete", 100, "active", 100) and v("active", 100, "past_due", 100)
    assert not v("past_due", 100, "active", 100) and v("past_due", 100, "active", 101)
    assert not v("canceled", 100, "active", 200) and not v("incomplete_expired", 100, "active", 200)   # definitivi
    assert not v("active", 200, "canceled", 100)                                                     # più vecchio
    assert v(None, 0, "active", 0) and v("active", 100, None, 100)
    # l'SQL contiene le stesse regole (controllate a mano su Postgres 16: 192 combinazioni identiche)
    import inspect
    src = inspect.getsource(ab._salva_abbonamento)
    assert "<= EXCLUDED.evento_created" not in src and "incomplete_expired" in src and "_RANGO_SQL" in src
