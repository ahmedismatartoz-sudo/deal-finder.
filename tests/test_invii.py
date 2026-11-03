"""4/11: invii una volta per destinatario (anche dopo un riavvio), disiscritti mai ricontattati né reimportati,
SMS solo con consenso, campagne già fatte riconosciute tutte, prezzo vero nelle email, lead nel database e non nei log."""
import json
import logging
import sys
import types

# httpx finto solo per importare i moduli di marketing (nessuna chiamata vera a Brevo); tolto subito dopo
_vero_httpx = sys.modules.get("httpx")
_gia = {m for m in sys.modules if m.startswith("dealfinder.marketing")}
if _vero_httpx is None:
    _stub = types.ModuleType("httpx")
    _stub.Client = object
    sys.modules["httpx"] = _stub
from dealfinder.marketing import auto_invio, brevo_liste, consegne, invii  # noqa: E402
if _vero_httpx is None:
    sys.modules.pop("httpx", None)
    for _m in [m for m in sys.modules if m.startswith("dealfinder.marketing") and m not in _gia]:
        sys.modules.pop(_m, None)


class _R:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FintoDB:
    """Tabelle invii, disiscritti, consensi, lead_consegne in memoria. Lo stesso oggetto = lo stesso database
    anche dopo un "riavvio" (nuova chiamata)."""

    def __init__(self):
        self.invii, self.disiscritti, self.consensi, self.lead = {}, {}, {}, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def execute(self, sql, p=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT email_hash FROM disiscritti"):
            return _R([{"email_hash": h} for h in self.disiscritti])
        if s.startswith("INSERT INTO disiscritti"):
            if p[0] in self.disiscritti:
                return _R()
            self.disiscritti[p[0]] = p[1]
            return _R([{"email_hash": p[0]}])
        if s.startswith("INSERT INTO invii"):
            k = (p[0], p[1])
            if k in self.invii:
                return _R()
            self.invii[k] = "in_invio"
            return _R([{"destinatario_hash": p[1]}])
        if s.startswith("UPDATE invii SET stato='inviato'"):
            if (p[0], p[1]) in self.invii:
                self.invii[(p[0], p[1])] = "inviato"
            return _R()
        if s.startswith("DELETE FROM invii"):
            if self.invii.get((p[0], p[1])) == "in_invio":
                del self.invii[(p[0], p[1])]
            return _R()
        if s.startswith("SELECT contatto_hash FROM consensi"):
            return _R([{"contatto_hash": h} for (c, h), rev in self.consensi.items() if c == p[0] and not rev])
        if s.startswith("INSERT INTO consensi"):
            self.consensi[(p[0], p[1])] = False
            return _R()
        if s.startswith("UPDATE consensi SET revocato_at"):
            self.consensi[(p[0], p[1])] = True
            return _R()
        if s.startswith("INSERT INTO lead_consegne"):
            self.lead.append((p[0], json.loads(p[1])))
            return _R()
        if s.startswith("INSERT INTO brevo_inviati"):
            return _R()
        raise AssertionError("query inattesa: " + s[:80])


def test_prenota_una_volta_anche_dopo_riavvio():
    db = FintoDB()
    primo = invii.prenota(db, "brevo_2_11:a", ["A@x.it", "a@x.it ", "b@x.it"])
    assert primo == ["A@x.it", "b@x.it"]                         # doppione tolto (maiuscole e spazi)
    # riavvio a metà invio: stesso lavoro, stessa lista -> nessuno riceve due volte, solo i nuovi
    assert invii.prenota(db, "brevo_2_11:a", ["a@x.it", "b@x.it", "c@x.it"]) == ["c@x.it"]
    # un altro lavoro è un'altra cosa
    assert invii.prenota(db, "nuovi_3_11:b", ["a@x.it"]) == ["a@x.it"]
    # invio non partito: i prenotati si liberano; inviato: restano
    invii.segna_inviati(db, "brevo_2_11:a", ["b@x.it"])
    invii.libera(db, "brevo_2_11:a", ["a@x.it", "b@x.it"])
    assert invii.prenota(db, "brevo_2_11:a", ["a@x.it", "b@x.it"]) == ["a@x.it"]
    # nel database solo impronte, mai indirizzi
    assert all("@" not in h for _, h in db.invii)


def test_disiscritti_mai_piu():
    db = FintoDB()
    invii.sincronizza_da_brevo(db, [{"email": "No@x.it", "emailBlacklisted": True}, {"email": "si@x.it"}])
    assert invii.prenota(db, "auto_invio", ["no@x.it", "si@x.it"]) == ["si@x.it"]


def test_sms_solo_con_consenso_e_numeri_normalizzati():
    db = FintoDB()
    invii.registra_consenso(db, "+39 348 111 2222", "sms", "modulo sito 4/11")
    assert invii.con_consenso(db, ["3481112222", "3489999999"]) == ["3481112222"]
    assert invii.prenota(db, "sms_invia:x", ["3481112222", "0039 348 1112222"], "sms") == ["3481112222"]
    invii.revoca_consenso(db, "3481112222", "sms")
    assert invii.con_consenso(db, ["3481112222"]) == []


def test_import_liste_senza_disiscritti_ne_cancellati():
    bloccati = {invii.impronta("stop@x.it")}
    gia = {"vecchio@x.it", "presente@x.it"}           # caricati in passato
    in_brevo = {"presente@x.it"}                       # vecchio@ è stato cancellato da Brevo per fare posto
    out = brevo_liste.da_importare(["stop@x.it", "Vecchio@x.it", "presente@x.it", "nuovo@x.it", "nuovo@x.it"],
                                   bloccati, gia, in_brevo)
    assert out == ["presente@x.it", "nuovo@x.it"]


class _Risp:
    def __init__(self, code=200, data=None):
        self.status_code, self._d, self.text = code, data or {}, ""

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 300:
            raise RuntimeError(self.status_code)


def test_campagne_fatte_lette_tutte():
    class C:
        def get(self, url, params):
            off = params.get("offset", 0)
            tot = 230
            return _Risp(200, {"campaigns": [{"name": f"c{i}"} for i in range(off, min(off + 100, tot))]})
    nomi = brevo_liste.nomi_campagne(C())
    assert len(nomi) == 230 and "c0" in nomi and "c229" in nomi       # prima solo le prime 100


def test_scheda_email_con_prezzo_vero():
    html = brevo_liste._card({"id": 1, "prezzo": 9000, "R": 12000, "auto": "Fiat Panda", "anno": 2018,
                              "km": 80000, "carb": "benzina", "n": 12})
    assert "9.000 €" in html and "+2.900 €" in html                  # niente 6.000 € "dopo trattativa"


def test_manda_due_volte_stesso_lavoro(tmp=None):
    db = FintoDB()
    vecchi = (auto_invio.time.sleep, auto_invio._cartella)
    chiamate = []

    class C:
        def post(self, url, json=None):
            chiamate.append(url.rsplit("/", 1)[-1])
            if url.endswith("/contacts/lists"):
                return _Risp(201, {"id": 5})
            if url.endswith("/emailCampaigns"):
                return _Risp(201, {"id": 9})
            return _Risp(201)
    import dealfinder.db as D
    vecchio_connect = D.connect
    D.connect = lambda: db
    auto_invio.time.sleep = lambda s: None
    auto_invio._cartella = lambda c: 1
    try:
        assert auto_invio._manda(C(), "prova", "ogg", "<p>x</p>", ["a@x.it", "b@x.it"], "lavoro:1")
        n = len(chiamate)
        # riavvio: stessa chiamata -> nessuna lista, nessuna campagna, nessun invio
        assert not auto_invio._manda(C(), "prova", "ogg", "<p>x</p>", ["a@x.it", "b@x.it"], "lavoro:1")
        assert len(chiamate) == n
        assert set(db.invii.values()) == {"inviato"}
    finally:
        D.connect = vecchio_connect
        auto_invio.time.sleep, auto_invio._cartella = vecchi


def test_lead_nel_database_non_nei_log():
    db = FintoDB()
    righe = []

    class H(logging.Handler):
        def emit(self, rec):
            righe.append(rec.getMessage())
    h = H()
    consegne.lg.addHandler(h)
    import dealfinder.db as D
    vecchio = D.connect
    D.connect = lambda: db
    consegne._contati.clear()
    try:
        consegne.consegna("LEADE", json.dumps({"nome": "Auto Rossi", "email": ["info@rossi.it"], "tel": "3331234567"}))
        assert db.lead == [("LEADE", {"nome": "Auto Rossi", "email": ["info@rossi.it"], "tel": "3331234567"})]
        testo = " ".join(righe)
        assert "rossi" not in testo.lower() and "333" not in testo
        csv = consegne.csv_bytes([d for _, d in db.lead]).decode("utf-8")
        assert "info@rossi.it" in csv and csv.startswith("﻿nome,email,tel")
    finally:
        D.connect = vecchio
        consegne.lg.removeHandler(h)
        consegne._contati.clear()


def test_lead_e_invii_nel_codice():
    """Niente righe di lead nei log, invii una tantum atomici, AUTO_INVIO spento di default, data/ senza dati personali."""
    import glob
    import os
    import re
    qui = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for f in glob.glob(os.path.join(qui, "dealfinder", "pipeline", "lead*.py")) + [os.path.join(qui, "dealfinder", "pipeline", "lusso.py")]:
        s = open(f, encoding="utf-8").read()
        assert not re.search(r'log\.(info|warning)\("LEAD[A-Z]* %s", json\.dumps', s), f
    app = open(os.path.join(qui, "dealfinder", "web", "app.py"), encoding="utf-8").read()
    for job in ("rinvio:", "rinvio_invia:", "rinvio_nonaperti:", "brevo_2_11:", "campagna_1_11:invio"):
        assert f'_due(f"{job}' not in app and f'_due("{job}' not in app, job
    assert 'os.environ.get("AUTO_INVIO") != "1"' in app
    assert "ESPORTA_EMAIL_RIGA" not in app and "SMS_STRUTT nomi" not in app
    for nome in ("marketing_gruppi.json", "commercianti.json", "lead_mi.json", "lead_mb.json"):
        assert not os.path.exists(os.path.join(qui, "data", nome)), nome
