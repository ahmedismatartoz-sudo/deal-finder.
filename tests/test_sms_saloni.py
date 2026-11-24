"""24/11: SMS ai saloni con il link "Mi interessa" (marketing/sms_saloni.py). Brevo e database finti, numeri finti."""
import datetime as dt
import json
import logging
import re
import time

from starlette.requests import Request

from dealfinder.marketing import sms_saloni as SS
from dealfinder.web import app as A

ROMA_MEZZOGIORNO = dt.datetime(2026, 11, 24, 11, 0, tzinfo=dt.timezone.utc)      # 12:00 a Roma
ENV = {"PUBLIC_URL": "https://scovo.onrender.com"}


def _lista(n: int) -> str:
    return json.dumps([{"tel": f"+39333000{i:04d}", "nome": f"Salone {i}"} for i in range(n)])


class _R(dict):
    pass


class _Cur:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Db:
    """Database finto: capisce solo le frasi di sms_saloni."""

    def __init__(self):
        self.link, self.clic, self.stop, self.si = {}, [], {}, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def execute(self, q, p=()):
        q = " ".join(q.split())
        if q.startswith("CREATE"):
            return _Cur([])
        if q.startswith("SELECT telefono FROM sms_stop"):
            return _Cur([{"telefono": t} for t in self.stop])
        if q.startswith("SELECT telefono FROM sms_link WHERE giro"):
            return _Cur([{"telefono": x["telefono"]} for x in self.link.values() if x["giro"] == p[0]])
        if q.startswith("INSERT INTO sms_link"):
            if p[0] in self.link:
                return _Cur([])
            self.link[p[0]] = {"nome": p[1], "telefono": p[2], "giro": p[3], "esito": None}
            return _Cur([{"token": p[0]}])
        if q.startswith("UPDATE sms_link SET esito"):
            self.link[p[1]]["esito"] = p[0]
            return _Cur([])
        if q.startswith("SELECT nome, telefono FROM sms_link") or q.startswith("SELECT telefono FROM sms_link WHERE token"):
            x = self.link.get(p[0])
            return _Cur([x] if x else [])
        if q.startswith("INSERT INTO sms_clic"):
            self.clic.append({"token": p[0], "user_agent": p[1]})
            return _Cur([])
        if q.startswith("SELECT count(*) AS n FROM sms_clic"):
            return _Cur([{"n": sum(1 for c in self.clic if c["token"] == p[0])}])
        if q.startswith("INSERT INTO sms_si"):
            self.si.append({"token": p[0], "user_agent": p[1]})
            return _Cur([])
        if q.startswith("SELECT count(*) AS n FROM sms_si"):
            return _Cur([{"n": sum(1 for c in self.si if c["token"] == p[0])}])
        if q.startswith("INSERT INTO sms_stop"):
            self.stop.setdefault(p[0], p[1])
            return _Cur([])
        if q.startswith("SELECT 'sms' AS canale, l.token"):          # i confermati, con gli aperti in una colonna
            out = []
            for t, x in self.link.items():
                n = sum(1 for c in self.si if c["token"] == t)
                if n:
                    out.append({"canale": "sms", "token": t, "nome": x["nome"], "telefono": x["telefono"], "giro": x["giro"],
                                "clic": n, "aperti": sum(1 for c in self.clic if c["token"] == t),
                                "primo_clic": "2026-11-24", "ultimo_clic": "2026-11-24", "stop": x["telefono"] in self.stop})
            return _Cur(out)
        if q.startswith("SELECT 'sms' AS canale, a.token"):          # chi ha solo aperto
            assert "NOT EXISTS (SELECT 1 FROM sms_si" in q
            out = []
            for t, x in self.link.items():
                n = sum(1 for c in self.clic if c["token"] == t)
                if n and not any(c["token"] == t for c in self.si):
                    out.append({"canale": "sms", "token": t, "nome": x["nome"], "telefono": x["telefono"], "giro": x["giro"],
                                "aperti": n, "primo_aperto": "2026-11-24", "ultimo_aperto": "2026-11-24"})
            return _Cur(out)
        raise AssertionError("frase non prevista: " + q[:80])


class _Risposta:
    def __init__(self, status, dati=None):
        self.status_code, self._d, self.text = status, dati or {}, ""

    def json(self):
        return self._d


class Brevo:
    """Brevo finto: tanti crediti, ogni SMS ne costa `costo`."""

    def __init__(self, crediti=256.0, costo=1.0, errori=()):
        self.crediti, self.costo, self.errori, self.inviati, self.conti = crediti, costo, dict(errori), [], 0

    def get(self, url, **k):
        assert url == "https://api.brevo.com/v3/account"
        self.conti += 1
        return _Risposta(200, {"plan": [{"type": "free", "credits": 300, "creditsType": "sendLimit"},
                                        {"type": "sms", "credits": self.crediti, "creditsType": "sendLimit"}]})

    def post(self, url, json=None, **k):
        assert url == "https://api.brevo.com/v3/transactionalSMS/sms"
        self.chiamate = getattr(self, "chiamate", 0) + 1
        if self.chiamate in self.errori:
            return _Risposta(self.errori[self.chiamate])
        if self.crediti < self.costo:
            return _Risposta(402)
        self.crediti -= self.costo
        self.inviati.append(json)
        return _Risposta(201, {"messageId": 1, "usedCredits": self.costo, "remainingCredits": self.crediti, "smsCount": 1})


class _Log:
    def __init__(self):
        self.righe = []

    def warning(self, m, *a):
        self.righe.append(m % a if a else m)


def _invia(db, brevo, n=5, giro="sms_saloni:t", **k):
    log = _Log()
    env = dict(ENV, SMS_LISTA=k.pop("lista", None) or _lista(n), **k.pop("env", {}))
    st = SS.invia(giro, db, brevo, env=env, adesso=k.pop("adesso", lambda: ROMA_MEZZOGIORNO), dormi=lambda s: None,
                  log=log, **k)
    return st, log


# ---------- testo ----------
def test_testo_in_un_sms_gsm7_con_link_e_stop():
    t = SS.testo_sms("abc2345", ENV)
    assert SS.lunghezza_gsm7(t) is not None and SS.lunghezza_gsm7(t) <= 160, (len(t), t)
    assert "https://scovo.onrender.com/mi/abc2345" in t and "Stop" in t
    assert t.startswith("Skovo: auto di privati sotto prezzo vicino a te, con margine già calcolato. Ti interessa? Tocca qui: ")
    # anche con il token più lungo ammesso
    assert SS.lunghezza_gsm7(SS.testo_sms("abcd2345", ENV)) <= 160


def test_gsm7_rifiuta_emoji_e_accenti_strani():
    assert SS.lunghezza_gsm7("già così è più") == 14
    for strano in ("ciao 🚗", "prezzo ’basso’", "perché no ê", "tre… punti", "trattino – lungo"):
        assert SS.lunghezza_gsm7(strano) is None, strano
    assert SS.lunghezza_gsm7("10 €") == 5              # l'euro conta doppio
    for t in SS.TESTI:
        assert SS.lunghezza_gsm7(t.format(link="x")) is not None


def test_testo_si_accorcia_con_indirizzo_lungo_e_tiene_link_e_stop():
    env = {"PUBLIC_URL": "https://www.un-indirizzo-parecchio-lungo.example.com/"}
    t = SS.testo_sms("abc2345", env)
    assert SS.lunghezza_gsm7(t) <= 160
    assert "https://www.un-indirizzo-parecchio-lungo.example.com/mi/abc2345" in t and "Stop" in t
    try:
        SS.testo_sms("abc2345", {"PUBLIC_URL": "https://" + "x" * 150 + ".example.com"})
        assert False, "doveva rifiutare"
    except ValueError:
        pass


def test_mittente_alfanumerico_massimo_11():
    assert SS.mittente({}) == "Skovo"
    assert SS.mittente({"SMS_MITTENTE": "Skovo Auto Usate!"}) == "SkovoAutoUs"


# ---------- numeri e token ----------
def test_numeri_validi_doppioni_e_stop():
    lista = [{"tel": "+39 333 000 0001", "nome": "A"}, {"tel": "3330000001", "nome": "A bis"},
             {"tel": "0039 333 0000002", "nome": "B"}, {"tel": "+39021234567", "nome": "fisso"},
             {"tel": "+3933300000", "nome": "corto"}, {"tel": "+393330000003", "nome": "C"}, {"nome": "senza"}]
    dest, salti = SS.prepara(lista, {"+393330000003"})
    assert [d["tel"] for d in dest] == ["+393330000001", "+393330000002"]
    assert salti == {"non_validi": 3, "doppioni": 1, "stop": 1}


def test_token_unici_e_del_formato_giusto():
    visti = set()
    for _ in range(3000):
        t = SS.nuovo_token(visti)
        assert re.fullmatch(r"[a-z0-9]{6,8}", t) and t not in visti
        visti.add(t)
    db, brevo = Db(), Brevo()
    _invia(db, brevo, n=40)
    assert len(db.link) == 40 and len({x["telefono"] for x in db.link.values()}) == 40
    link = [re.search(r"/mi/([a-z0-9]+) ", m["content"]).group(1) for m in brevo.inviati]
    assert len(set(link)) == 40 and set(link) == set(db.link)


# ---------- invio ----------
def test_invio_normale_e_richiesta_a_brevo():
    db, brevo = Db(), Brevo(crediti=256)
    st, log = _invia(db, brevo, n=3, env={"SMS_MITTENTE": "Skovo"})
    assert st["inviati"] == 3 and st["falliti"] == 0 and st["crediti_prima"] == 256 and st["crediti_dopo"] == 253
    m = brevo.inviati[0]
    assert m["sender"] == "Skovo" and m["recipient"] == "393330000000" and m["unicodeEnabled"] is False
    assert SS.lunghezza_gsm7(m["content"]) <= 160
    assert log.righe[-1] == "SMS_FINE inviati=3 falliti=0 saltati=0 crediti_prima=256 crediti_dopo=253"
    assert "SMS_INVIO n=1 tel=***0000 esito=ok" in log.righe


def test_opt_out_non_riceve():
    db, brevo = Db(), Brevo()
    db.stop["+393330000001"] = "ha detto no"
    st, _ = _invia(db, brevo, n=3)
    assert [m["recipient"] for m in brevo.inviati] == ["393330000000", "393330000002"]
    assert st["inviati"] == 2 and st["saltati"] == 1


def test_fuori_fascia_non_invia():
    assert SS.in_fascia(dt.datetime(2026, 11, 24, 8, 0, tzinfo=dt.timezone.utc))            # 9:00 a Roma
    assert not SS.in_fascia(dt.datetime(2026, 11, 24, 7, 59, tzinfo=dt.timezone.utc))       # 8:59
    assert SS.in_fascia(dt.datetime(2026, 11, 24, 18, 59, tzinfo=dt.timezone.utc))          # 19:59
    assert not SS.in_fascia(dt.datetime(2026, 11, 24, 19, 0, tzinfo=dt.timezone.utc))       # 20:00
    assert SS.in_fascia(dt.datetime(2026, 7, 1, 7, 0, tzinfo=dt.timezone.utc))              # estate: 9:00 a Roma
    db, brevo = Db(), Brevo()
    st, log = _invia(db, brevo, adesso=lambda: dt.datetime(2026, 11, 24, 20, 30, tzinfo=dt.timezone.utc))
    assert st["inviati"] == 0 and not brevo.inviati and not db.link and brevo.conti == 0
    assert any(r.startswith("SMS_FUORI_ORARIO") for r in log.righe)


def test_si_ferma_se_arrivano_le_20_a_meta():
    ore = iter([ROMA_MEZZOGIORNO] * 3 + [dt.datetime(2026, 11, 24, 19, 0, 5, tzinfo=dt.timezone.utc)] * 10)
    db, brevo = Db(), Brevo()
    st, _ = _invia(db, brevo, n=5, adesso=lambda: next(ore))
    assert st["inviati"] == 2 and st["saltati"] == 3 and st["fermato"] == "fuori_orario"


def test_tetto_crediti():
    db, brevo = Db(), Brevo(crediti=3)
    st, log = _invia(db, brevo, n=10)
    assert st["inviati"] == 3 and len(brevo.inviati) == 3 and st["saltati"] == 7 and st["fermato"] == "crediti_finiti"
    assert getattr(brevo, "chiamate") == 3                     # nessuna richiesta oltre i crediti
    assert log.righe[-1].startswith("SMS_FINE inviati=3 falliti=0 saltati=7 crediti_prima=3 crediti_dopo=0")
    # un SMS che costa più di un credito: con 5 crediti e 2 a SMS ne partono 2, il terzo non si tenta
    db, brevo = Db(), Brevo(crediti=5, costo=2)
    st, _ = _invia(db, brevo, n=10)
    assert st["inviati"] == 2 and brevo.chiamate == 2 and brevo.crediti == 1
    # senza crediti non parte niente
    db, brevo = Db(), Brevo(crediti=0)
    st, _ = _invia(db, brevo, n=4)
    assert st["inviati"] == 0 and not hasattr(brevo, "chiamate")


def test_prova_solo_il_primo():
    db, brevo = Db(), Brevo()
    st, _ = _invia(db, brevo, n=8, giro="sms_prova:t", solo_primo=True)
    assert st["inviati"] == 1 and [m["recipient"] for m in brevo.inviati] == ["393330000000"]


def test_errori_contati_e_primi_cinque_falliti_fermano():
    db, brevo = Db(), Brevo(errori={2: 400})
    st, log = _invia(db, brevo, n=4)
    assert st["inviati"] == 3 and st["falliti"] == 1
    assert "SMS_INVIO n=2 tel=***0001 esito=errore 400" in log.righe
    db, brevo = Db(), Brevo(errori={i: 400 for i in range(1, 30)})
    st, _ = _invia(db, brevo, n=20)
    assert st["inviati"] == 0 and st["falliti"] == 5 and st["saltati"] == 15 and brevo.chiamate == 5


def test_stesso_giro_non_rimanda():
    db, brevo = Db(), Brevo()
    _invia(db, brevo, n=3)
    st, _ = _invia(db, brevo, n=5)
    assert st["inviati"] == 2 and len(brevo.inviati) == 5 and len({m["recipient"] for m in brevo.inviati}) == 5


def test_nessun_telefono_intero_nei_log():
    db, brevo = Db(), Brevo(errori={2: 400})
    db.stop["+393330000004"] = "no"
    st, log = _invia(db, brevo, n=8)
    for t, x in list(db.link.items()):
        SS.registra_aperto(db, t, "Mozilla/5.0 (iPhone)", log=log)
        SS.registra_clic(db, t, "Mozilla/5.0 (iPhone)", log=log)
        SS.registra_stop(db, t, log=log)
    SS.registra_clic(db, "zzzzzzz", "Mozilla/5.0", log=log)
    assert len(log.righe) > 20
    for r in log.righe:
        assert not re.search(r"\d{6,}", r), r
        assert "333000" not in r, r
    assert any(re.fullmatch(r"SMS_INVIO n=\d+ tel=\*\*\*\d{4} esito=ok", r) for r in log.righe)


def test_lavoro_fuori_orario_non_consuma_etichetta():
    chiamate = []
    vecchio = SS.in_fascia
    SS.in_fascia = lambda adesso=None: False
    try:
        assert SS.lavoro("sms_saloni:t", connetti=Db, client=lambda: Brevo(), una_volta=lambda j: chiamate.append(j) or True) is None
    finally:
        SS.in_fascia = vecchio
    assert chiamate == []


# ---------- pagina ----------
def _req(path, token, ua="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)", ip="93.40.1.2", metodo="GET", corpo=b""):
    async def ricevi():
        return {"type": "http.request", "body": corpo, "more_body": False}
    return Request({"type": "http", "method": metodo, "path": path, "query_string": b"", "path_params": {"token": token},
                    "headers": [(b"user-agent", ua.encode()), (b"x-real-ip", ip.encode())], "client": (ip, 1)}, ricevi)


def _si(token, t=None, dopo=10, **k):
    """Il pulsante "Sì, chiamatemi": POST /mi/<token>/si con il campo firmato dalla pagina aperta `dopo` secondi fa."""
    import asyncio
    from urllib.parse import urlencode
    t = A._mi_firma(token, time.time() - dopo) if t is None else t
    return asyncio.run(A.sms_mi_si(_req(f"/mi/{token}/si", token, metodo="POST", corpo=urlencode({"t": t}).encode(), **k)))


class _Scena:
    def __init__(self):
        self.db, self.log = Db(), _Log()
        self.db.link["abc2345"] = {"nome": "Salone Prova", "telefono": "+393330009876", "giro": "t", "esito": "ok"}
        self.v = (A.connect, SS.lg)
        A.connect, SS.lg = (lambda: self.db), self.log
        logging.getLogger("scovo").warning = self.log.warning              # i log delle rotte (scartati, non registrati)
        A._sms_ip.clear()

    def fine(self):
        A.connect, SS.lg = self.v
        logging.getLogger("scovo").__dict__.pop("warning", None)
        A._sms_ip.clear()


def test_aprire_il_link_registra_solo_aperto_e_mostra_il_pulsante():
    s = _Scena()
    try:
        r = A.sms_mi_interessa(_req("/mi/abc2345", "abc2345"))
        corpo = r.body.decode()
        assert r.status_code == 200 and "noindex" in corpo and "noindex" in r.headers["x-robots-tag"]
        assert "Grazie!" not in corpo and "Sì, chiamatemi" in corpo and "348 242 5633" in corpo
        assert '<form class="si" method="post" action="/mi/abc2345/si">' in corpo
        assert re.search(r'<input type="hidden" name="t" value="\d+\.[0-9a-f]{32}">', corpo)
        assert '<a class="no" href="/mi/abc2345/stop">Non mi interessa</a>' in corpo
        assert "Salone Prova" not in corpo and "9876" not in corpo         # niente dati del salone nella pagina
        assert s.log.righe == ["SMS_APERTO token=abc2345 canale=sms n=1"]   # e nemmeno nel log dell'apertura
        assert len(s.db.clic) == 1 and not s.db.si and len(s.db.clic[0]["user_agent"]) <= 200
        # più aperture nello stesso secondo (filtro della posta): sempre e solo "aperto"
        for _ in range(3):
            A.sms_mi_interessa(_req("/mi/abc2345", "abc2345"))
        assert len(s.db.clic) == 4 and not s.db.si and not any("INTERESSATO" in x for x in s.log.righe)
    finally:
        s.fine()


def test_solo_il_pulsante_registra_l_interesse_e_mostra_grazie():
    s = _Scena()
    try:
        corpo = A.sms_mi_interessa(_req("/mi/abc2345", "abc2345")).body.decode()
        t = re.search(r'name="t" value="([^"]+)"', corpo).group(1)
        assert A._mi_firma_valida("abc2345", t) == "presto"                 # appena aperta: ancora presto
        r = _si("abc2345")
        corpo = r.body.decode()
        assert r.status_code == 200 and "Grazie!" in corpo and "Ti chiamiamo a breve dal" in corpo and "348 242 5633" in corpo
        assert "per spiegarti come funziona Skovo" in corpo and 'href="tel:+393482425633"' in corpo and "Chiamaci ora" in corpo
        assert "Sì, chiamatemi" not in corpo and "Non voglio più ricevere SMS" in corpo and "noindex" in r.headers["x-robots-tag"]
        assert "Salone Prova" not in corpo and "9876" not in corpo
        assert s.log.righe[-1] == "SMS_INTERESSATO token=abc2345 canale=sms nome=Salone Prova tel=***9876 n=1"
        _si("abc2345")
        assert s.log.righe[-1].endswith("n=2") and len(s.db.si) == 2 and len(s.db.clic) == 1
    finally:
        s.fine()


def test_pulsante_troppo_presto_o_senza_firma_non_conta():
    s = _Scena()
    try:
        for dopo in (0, 1):                                                 # un programma che invia il modulo subito
            r = _si("abc2345", dopo=dopo)
            assert r.status_code == 200 and "Grazie!" not in r.body.decode() and "Tocca di nuovo il pulsante" in r.body.decode()
            assert 'action="/mi/abc2345/si"' in r.body.decode()
        assert s.log.righe == ["SMS_CONFERMA_SCARTATA token=abc2345 motivo=presto"] * 2 and not s.db.si
        assert _si("abc2345", dopo=A.MI_ATTESA_S + 1).body.decode().count("Grazie!") == 1 and len(s.db.si) == 1
        buono = A._mi_firma("abc2345", time.time() - 10)
        casi = ["", "x", "123", "123.", ".abc", "١٢٣.abc", buono[:-1] + ("0" if buono[-1] != "0" else "1"),
                A._mi_firma("altro99", time.time() - 10),                  # firmato per un altro token
                A._mi_firma("abc2345", time.time() - A.MI_SCADE_S - 60),   # pagina aperta più di un giorno fa
                str(int(buono.split(".")[0]) - 20) + "." + buono.split(".")[1]]    # secondi cambiati a mano
        for t in casi:
            r = _si("abc2345", t=t)
            assert "Grazie!" not in r.body.decode() and "Sì, chiamatemi" in r.body.decode(), t
        assert len(s.db.si) == 1 and s.log.righe[-1].startswith("SMS_CONFERMA_SCARTATA token=abc2345 motivo=")
        assert sum("motivo=scaduta" in x for x in s.log.righe) == 1 and sum("motivo=firma" in x for x in s.log.righe) == len(casi) - 1
        # senza corpo del tutto
        import asyncio
        r = asyncio.run(A.sms_mi_si(_req("/mi/abc2345/si", "abc2345", metodo="POST")))
        assert "Grazie!" not in r.body.decode() and len(s.db.si) == 1
    finally:
        s.fine()


def test_pulsante_database_fermo_mai_grazie():
    s = _Scena()
    try:
        A.connect = lambda: 1 / 0
        r = _si("abc2345")
        assert r.status_code == 503 and "Grazie!" not in r.body.decode() and "Tocca di nuovo il pulsante" in r.body.decode()
        assert r.headers["retry-after"] == "120" and s.log.righe[-1] == "SMS_INTERESSATO non registrato: ZeroDivisionError"
        # l'apertura con il database fermo: la pagina si vede lo stesso
        r = A.sms_mi_interessa(_req("/mi/abc2345", "abc2345"))
        assert r.status_code == 200 and "Sì, chiamatemi" in r.body.decode()
        assert s.log.righe[-1] == "SMS_APERTO non registrato: ZeroDivisionError"
    finally:
        s.fine()


def test_pagina_token_sconosciuto_stessa_pagina_gentile():
    s = _Scena()
    try:
        senza_t = lambda c: re.sub(r'name="t" value="[^"]*"', "", c)
        buona = A.sms_mi_interessa(_req("/mi/abc2345", "abc2345")).body.decode()
        r = A.sms_mi_interessa(_req("/mi/nonce99", "nonce99"))
        assert r.status_code == 200 and "Sì, chiamatemi" in r.body.decode()
        assert senza_t(r.body.decode()).replace("nonce99", "abc2345") == senza_t(buona)
        assert len(s.log.righe) == 1 and len(s.db.clic) == 1                 # un token che non esiste non si registra
        r = _si("nonce99")
        assert r.body.decode().replace("nonce99", "abc2345") == _si("abc2345").body.decode()
        assert "SMS_INTERESSATO token=nonce99 canale=sms nome=? tel=***? n=0" in s.log.righe and len(s.db.si) == 1
        # token con caratteri strani: mai copiato nel log né nella pagina
        prima = len(s.log.righe)
        r = A.sms_mi_interessa(_req("/mi/x", "<b>\nSMS_FINE finto"))
        assert len(s.log.righe) == prima and "<b>" not in r.body.decode() and "Chiamaci ora" in r.body.decode()
        r = _si("<b>\nSMS_FINE finto", t=A._mi_firma("", time.time() - 10))
        assert s.log.righe[-1] == "SMS_INTERESSATO token=? canale=sms nome=? tel=***? n=0" and "<b>" not in r.body.decode()
    finally:
        s.fine()


def test_anteprime_automatiche_e_head_non_registrano_niente():
    s = _Scena()
    try:
        A.sms_mi_interessa(_req("/mi/abc2345", "abc2345", ua="WhatsApp/2.23"))
        A.sms_mi_interessa(_req("/mi/abc2345", "abc2345", ua="Mozilla/5.0 (compatible; Googlebot/2.1)"))
        r = A.sms_mi_interessa(_req("/mi/abc2345", "abc2345", metodo="HEAD"))
        assert r.status_code == 200 and not s.db.clic and not s.db.si and not s.log.righe
        # un programma automatico che invia anche il modulo, con la firma buona: niente lo stesso
        r = _si("abc2345", ua="python-requests/2.32")
        assert "Grazie!" not in r.body.decode() and not s.db.si and not s.log.righe
        A.sms_mi_interessa(_req("/mi/abc2345", "abc2345", ua="Mozilla/5.0 " + "x" * 900))
        _si("abc2345", ua="Mozilla/5.0 " + "x" * 900)
        assert len(s.db.clic) == 1 and len(s.db.clic[0]["user_agent"]) == 200 and len(s.db.si[0]["user_agent"]) == 200
    finally:
        s.fine()


def test_limite_per_ip():
    s = _Scena()
    try:
        for _ in range(A.SMS_CLIC_IP_ORA):
            assert A.sms_mi_interessa(_req("/mi/abc2345", "abc2345")).status_code == 200
        r = A.sms_mi_interessa(_req("/mi/abc2345", "abc2345"))
        assert r.status_code == 429 and "Sì, chiamatemi" in r.body.decode() and len(s.db.clic) == A.SMS_CLIC_IP_ORA
        r = _si("abc2345")
        assert r.status_code == 429 and "Grazie!" not in r.body.decode() and not s.db.si
        assert A.sms_mi_interessa(_req("/mi/abc2345", "abc2345", ip="93.40.9.9")).status_code == 200
        assert _si("abc2345", ip="93.40.9.9").status_code == 200 and len(s.db.si) == 1
    finally:
        s.fine()


def test_stop_dalla_pagina_e_poi_niente_sms():
    s = _Scena()
    try:
        r = A.sms_mi_stop(_req("/mi/abc2345/stop", "abc2345", metodo="POST"))
        assert r.status_code == 200 and "Non riceverai più SMS" in r.body.decode()
        assert "+393330009876" in s.db.stop and s.log.righe[-1] == "SMS_STOP token=abc2345 tel=***9876"
        brevo = Brevo()
        st, _ = _invia(s.db, brevo, lista=json.dumps([{"tel": "+393330009876", "nome": "Salone Prova"}]))
        assert st["inviati"] == 0 and not brevo.inviati
    finally:
        s.fine()


def test_elenco_interessati_solo_admin_solo_i_confermati():
    s = _Scena()
    try:
        s.db.link["def6789"] = {"nome": "Salone Curioso", "telefono": "+393330001111", "giro": "t", "esito": "ok"}
        for _ in range(3):
            A.sms_mi_interessa(_req("/mi/abc2345", "abc2345"))
        A.sms_mi_interessa(_req("/mi/def6789", "def6789"))                  # apre e basta
        _si("abc2345")
        _si("abc2345")
        try:
            A.admin_sms_interessati(_req("/api/admin/sms_interessati", ""))
            assert False, "senza accesso doveva rifiutare"
        except A.HTTPError as e:
            assert e.status in (401, 403)
        vecchio = A.current_user
        A.current_user = lambda request, admin=False: {"id": 1, "role": "admin"} if admin else A.err(500, "serve admin=True")
        try:
            d = json.loads(A.admin_sms_interessati(_req("/api/admin/sms_interessati", "")).body)
        finally:
            A.current_user = vecchio
        assert d["totale"] == 1 and d["interessati"][0]["nome"] == "Salone Prova"
        x = d["interessati"][0]
        assert x["telefono"] == "+393330009876" and x["clic"] == 2 and x["aperti"] == 3 and x["canale"] == "sms"
        assert d["totale_solo_aperti"] == 1 and d["solo_aperti"][0]["nome"] == "Salone Curioso" and d["solo_aperti"][0]["aperti"] == 1
    finally:
        s.fine()


def test_frasi_dell_elenco_confermati_da_sms_si_e_aperti_da_sms_clic():
    viste = []

    class Spia(Db):
        def execute(self, q, p=()):
            viste.append(" ".join(q.split()))
            return super().execute(q, p)
    SS.interessati(Spia())
    q = next(x for x in viste if x.startswith("SELECT 'sms' AS canale, l.token"))
    assert "FROM sms_si c JOIN sms_link l" in q and "(SELECT count(*) FROM sms_clic a WHERE a.token = l.token) AS aperti" in q
    assert any("CREATE TABLE IF NOT EXISTS sms_si" in x for x in viste)


def test_rotte_registrate():
    percorsi = {getattr(r, "path", "") for r in A.routes}
    assert {"/mi/{token}", "/mi/{token}/stop", "/mi/{token}/si", "/api/admin/sms_interessati"} <= percorsi
    si = next(r for r in A.routes if getattr(r, "path", "") == "/mi/{token}/si")
    assert set(si.methods) == {"POST"}                                     # un GET (filtro della posta) non può confermare
    mi = next(r for r in A.routes if getattr(r, "path", "") == "/mi/{token}")
    assert "POST" not in mi.methods


def test_lista_dal_file_del_giro():
    """24/11: Render non accetta più variabili (tetto 300): la lista si legge da data/sms_liste/<giro>.json."""
    import json as _j
    import tempfile
    from dealfinder.marketing import sms_saloni as SS
    vecchia = SS.LISTE_DIR
    with tempfile.TemporaryDirectory() as d:
        with open(d + "/2411.json", "w") as fh:
            fh.write(_j.dumps([{"tel": "+393000000001", "nome": "Prova"}]))
        SS.LISTE_DIR = d
        try:
            assert SS.leggi_lista({}, "sms_prova:2411")[0]["nome"] == "Prova"
            assert SS.leggi_lista({}, "sms_saloni:2411")[0]["nome"] == "Prova"
            assert SS.leggi_lista({}, "sms_saloni:9999") == []
            assert SS.leggi_lista({"SMS_LISTA": '[{"tel":"+393000000002","nome":"Env"}]'}, "sms_prova:2411")[0]["nome"] == "Env"
            assert SS.leggi_lista({}, "sms_prova:../../etc") == []
        finally:
            SS.LISTE_DIR = vecchia
