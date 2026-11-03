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


class _Conn:
    def __init__(self):
        self.q = []

    def execute(self, sql, params=()):
        self.q.append((" ".join(sql.split()), params))

        class R:
            rowcount = 1
        return R()

    def commit(self):
        pass


def test_eventi_stripe():
    c = _Conn()
    assert ab.applica(c, {"type": "checkout.session.completed",
                          "data": {"object": {"client_reference_id": "7", "customer": "cus_1"}}}) == "pagato:7"
    assert "abbonamento_stato='active'" in c.q[-1][0] and c.q[-1][1] == ("cus_1", 7)
    assert ab.applica(c, {"type": "customer.subscription.deleted",
                          "data": {"object": {"customer": "cus_1", "status": "canceled"}}}) == "abbonamento:canceled"
    assert ab.applica(c, {"type": "invoice.payment_failed", "data": {"object": {"customer": "cus_1"}}}) == "pagamento_fallito"
    assert ab.applica(c, {"type": "altro"}) == "ignorato"


def test_piano_pro_da_gli_appuntamenti():
    c = _Conn()
    assert ab.applica(c, {"type": "checkout.session.completed",
                          "data": {"object": {"client_reference_id": "7", "customer": "cus_1", "metadata": {"piano": "pro"}}}}) == "pagato:7"
    assert any("'appuntamenti'" in q for q, _ in c.q)
    c = _Conn()
    ab.applica(c, {"type": "checkout.session.completed", "data": {"object": {"client_reference_id": "8", "customer": "cus_2"}}})
    assert not any("'appuntamenti'" in q for q, _ in c.q)
    c = _Conn()
    ab.applica(c, {"type": "customer.subscription.deleted", "data": {"object": {"customer": "cus_1", "metadata": {"piano": "pro"}}}})
    assert any("array_remove" in q for q, _ in c.q)


def test_checkout_sconto_primo_mese(monkeypatch=None):
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
    for k in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_PRICE_PRO"):
        os.environ.pop(k)
