"""16/11: giro a lotti della stessa email (giro_lotto) con un Brevo finto: prima i disiscritti/rimbalzi nel database,
poi via da Brevo solo chi ha ricevuto la base, poi i prossimi dell'archivio e la stessa campagna programmata."""
import json
import logging
import sys
import types
from datetime import datetime, timezone

sys.modules.setdefault("httpx", types.SimpleNamespace(Client=object, get=None))

from dealfinder.marketing import giro_lotto as GL  # noqa: E402
from dealfinder.marketing import su_misura as SM  # noqa: E402
from dealfinder.marketing.invii import impronta  # noqa: E402

ADESSO = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
QUANDO = "2026-11-17T09:00:00+01:00"
HTML = '<p>Ciao</p><a href="mailto:info@skovo.app">Richiamatemi</a> <a href="{{ unsubscribe }}">Non mi interessa</a>'


class _R:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FintoDB:
    def __init__(self, disiscritti=()):
        self.disiscritti = {h: "vecchio" for h in disiscritti}
        self.invii, self.brevo_inviati = {}, set()

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
        if s.startswith("SELECT destinatario_hash FROM invii WHERE lavoro"):
            return _R([{"destinatario_hash": h} for (lav, h) in self.invii if lav == p[0]])
        if s.startswith("INSERT INTO invii"):
            if (p[0], p[1]) in self.invii:
                return _R()
            self.invii[(p[0], p[1])] = "in_invio"
            return _R([{"destinatario_hash": p[1]}])
        if s.startswith("UPDATE invii SET stato='inviato'"):
            if (p[0], p[1]) in self.invii:
                self.invii[(p[0], p[1])] = "inviato"
            return _R()
        if s.startswith("DELETE FROM invii"):
            if self.invii.get((p[0], p[1])) == "in_invio":
                del self.invii[(p[0], p[1])]
            return _R()
        if s.startswith("INSERT INTO brevo_inviati"):
            self.brevo_inviati.add(p[0])
            return _R()
        raise AssertionError("query inattesa: " + s[:80])


class _Risp:
    def __init__(self, code=200, data=None):
        self.status_code, self._d = code, data

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FintoBrevo:
    """Brevo in memoria: contatti, liste, campagne, esportazioni (process -> export_url 'u:<cid>:<tipo>')."""

    def __init__(self, ricevuti, bloccati_lista=(), status="sent", stats=None, esportazioni=None, altri=()):
        self.contatti = {}
        for e in ricevuti:
            self.contatti[e] = {"email": e, "listIds": [84], "emailBlacklisted": e in bloccati_lista}
        for e in altri:
            self.contatti[e] = {"email": e, "listIds": [90], "emailBlacklisted": False}
        self.campagne = {77: {"id": 77, "name": "Skovo corta scovo.date 16-11", "status": status,
                              "subject": "Auto da privati sotto mercato", "previewText": "Ogni giorno",
                              "htmlContent": HTML, "sender": {"name": "Skovo", "email": "info@scovo.date", "id": 5},
                              "replyTo": "info@skovo.app", "header": "[DEFAULT_HEADER]",
                              "recipients": {"lists": [84]},
                              "statistics": {"globalStats": stats or {"sent": len(ricevuti), "unsubscriptions": 1,
                                                                      "hardBounces": 1, "softBounces": 1,
                                                                      "complaints": 1}}}}
        for cid in (73, 74, 75, 76):
            self.campagne[cid] = {"id": cid, "name": f"vecchia {cid}", "status": "sent", "recipients": {"lists": [1]},
                                  "statistics": {"globalStats": {"sent": 10, "unsubscriptions": 0, "hardBounces": 0,
                                                                 "softBounces": 1}}}
        self.esportazioni = esportazioni or {}
        self.liste = {84: "Skovo fermi 12-11", 90: "Altra"}
        self.cancellati, self.importati, self.create = [], [], []
        self.chiamate = []

    def _lista(self, lid):
        return [x for x in self.contatti.values() if lid in x["listIds"]]

    def get(self, url, params=None):
        self.chiamate.append(("GET", url))
        p = url.split("/v3", 1)[1]
        if p == "/account":
            return _Risp(200, {})
        if p.startswith("/emailCampaigns/"):
            cid = int(p.rsplit("/", 1)[1])
            return _Risp(200, self.campagne[cid]) if cid in self.campagne else _Risp(404, {})
        if p == "/emailCampaigns":
            st = (params or {}).get("status")
            if st is None:
                return _Risp(200, {"campaigns": [{"name": x["name"]} for x in self.campagne.values()]})
            return _Risp(200, {"campaigns": [x for x in self.campagne.values() if x["status"] == st]})
        if p.startswith("/processes/"):
            return _Risp(200, {"status": "completed", "export_url": "u:" + p.rsplit("/", 1)[1]})
        if p == "/contacts":
            if (params or {}).get("limit") == 1:
                return _Risp(200, {"count": len(self.contatti)})
            off = (params or {}).get("offset", 0)
            return _Risp(200, {"contacts": list(self.contatti.values())[off:off + 1000]})
        if p == "/contacts/folders":
            return _Risp(200, {"folders": [{"name": "Skovo", "id": 3}]})
        if p == "/contacts/lists":
            return _Risp(200, {"lists": [{"name": n, "id": i} for i, n in self.liste.items()]})
        if p.startswith("/contacts/lists/") and p.endswith("/contacts"):
            lid, off = int(p.split("/")[3]), (params or {}).get("offset", 0)
            return _Risp(200, {"contacts": self._lista(lid)[off:off + 500]})
        if p.startswith("/contacts/lists/"):
            return _Risp(200, {"uniqueSubscribers": len(self._lista(int(p.rsplit("/", 1)[1])))})
        raise AssertionError("GET inattesa " + p)

    def post(self, url, json=None):
        self.chiamate.append(("POST", url))
        p = url.split("/v3", 1)[1]
        if p.endswith("/exportRecipients"):
            return _Risp(202, {"processId": f"{p.split('/')[2]}:{json['recipientsType']}"})
        if p == "/contacts/lists":
            lid = max(self.liste) + 1
            self.liste[lid] = json["name"]
            return _Risp(201, {"id": lid})
        if p == "/contacts/import":
            for x in json["jsonBody"]:
                self.contatti.setdefault(x["email"], {"email": x["email"], "listIds": []})["listIds"] += json["listIds"]
                self.importati.append(x["email"])
            return _Risp(202, {"processId": "imp"})
        if p == "/emailCampaigns":
            self.create.append(json)
            return _Risp(201, {"id": 78})
        raise AssertionError("POST inattesa " + p)

    def delete(self, url):
        from urllib.parse import unquote
        self.chiamate.append(("DELETE", url))
        e = unquote(url.rsplit("/", 1)[1])
        self.cancellati.append(e)
        self.contatti.pop(e, None)
        return _Risp(204)


def _prepara(monkeypatch, brevo, righe, db):
    import time

    import dealfinder.db as D
    import dealfinder.marketing.brevo_liste as BL

    def scarica(url):
        cid, tipo = url[2:].split(":")
        if tipo == "imp":
            return None
        return "\n".join(["EMAIL"] + brevo.esportazioni.get((int(cid), tipo), [])).encode()

    monkeypatch.setattr(GL, "_scarica", scarica)
    monkeypatch.setattr(D, "connect", lambda: db)
    monkeypatch.setattr(SM, "_righe_db", lambda conn: righe)
    monkeypatch.setattr(BL, "BLOCCATI_MINIMI", 1)
    # 24/11: si cancella "per fare posto" solo se contatti + giro nuovo passano TETTO_BREVO (500.000 di norma): qui il
    # tetto è 14 (11 contatti + 4 nuovi = 15), come quando era 1.495 e Brevo era pieno
    monkeypatch.setattr(BL, "TETTO_BREVO", 14)
    monkeypatch.setattr(BL, "_bloccati_0111", lambda: set())
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _scenario():
    ricevuti = [f"r{i}@salone{i}.it" for i in range(10)]
    esportazioni = {(77, "all"): ricevuti, (77, "unsubscribed"): ["r1@salone1.it"],
                    (77, "hardBounces"): ["r2@salone2.it"], (77, "softBounces"): ["r3@salone3.it", "r7@salone7.it"],
                    (75, "softBounces"): ["r3@salone3.it"], (74, "softBounces"): ["r4@salone4.it"]}
    brevo = FintoBrevo(ricevuti, bloccati_lista={"r1@salone1.it", "r5@salone5.it"}, esportazioni=esportazioni,
                       altri=["altro@lista90.it"])
    brevo.contatti["r8@salone8.it"]["listIds"].append(90)          # anche in un'altra lista: non si cancella
    righe = [{"chiave": "a", "email": ["r0@salone0.it"], "prov": "MI", "nome": "Salone A"},          # già ricevuto
             {"chiave": "b", "email": ["nuovo1@b.it", "nuovo1bis@b.it"], "prov": "MI", "nome": "B"},  # uno solo
             {"chiave": "c", "email": ["nuovo2@c.it"], "prov": "CO", "nome": "C"},
             {"chiave": "d", "email": ["info@loconte.it"], "prov": "MI", "nome": "Loconte"},
             {"chiave": "e", "email": ["x@officinemulino.it"], "prov": "MI", "nome": "Officine Mulino"},
             {"chiave": "f", "email": ["nuovo3@f.it"], "prov": "MI", "nome": "Cosval Auto"},          # nome escluso
             {"chiave": "g", "email": ["via@g.it"], "prov": "MI", "nome": "G"},                        # disiscritto
             {"chiave": "h", "email": ["pec@pec.h.it"], "prov": "MI", "nome": "H"},
             {"chiave": "i", "email": ["nuovo4@i.it"], "prov": None, "nome": "I"},
             {"chiave": "s7", "email": ["r7@salone7.it"], "prov": "VA", "nome": "S7"}]   # rimbalzo morbido 1 volta
    return brevo, righe, FintoDB(disiscritti={impronta("via@g.it")})


def test_giro_completo(monkeypatch):
    brevo, righe, db = _scenario()
    _prepara(monkeypatch, brevo, righe, db)
    log = []

    class H(logging.Handler):
        def emit(self, rec):
            log.append(rec.getMessage())
    h = H()
    logging.getLogger("dealfinder.marketing").addHandler(h)
    try:
        out = GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, pausa_cancella=0, adesso=ADESSO)
    finally:
        logging.getLogger("dealfinder.marketing").removeHandler(h)
    assert "fermo" not in out, out
    # 1. disiscritti, rimbalzi duri, soft ripetuti (r3: 77 e 75) e bloccati in Brevo nel database; r4 (una volta) no
    for e in ("r1@salone1.it", "r2@salone2.it", "r3@salone3.it", "r5@salone5.it"):
        assert impronta(e) in db.disiscritti, e
    assert impronta("r4@salone4.it") not in db.disiscritti
    # 2. cancellati solo i ricevuti della lista 84 non bloccati; restano i bloccati, i rimbalzi morbidi (r3 ripetuto
    # = bloccato, r7 una volta = non arrivata), chi è anche in un'altra lista (r8) e gli altri contatti
    assert sorted(brevo.cancellati) == sorted(f"r{i}@salone{i}.it" for i in range(10) if i not in (1, 3, 5, 7, 8))
    assert "r1@salone1.it" in brevo.contatti and "altro@lista90.it" in brevo.contatti
    assert "r7@salone7.it" in brevo.contatti and "r8@salone8.it" in brevo.contatti
    # 3. caricati: un indirizzo per salone, mai ricevuti/esclusi/disiscritti/PEC; r7 (non arrivata) sì
    assert sorted(brevo.importati) == ["nuovo1@b.it", "nuovo2@c.it", "nuovo4@i.it", "r7@salone7.it"]
    assert brevo.liste[out["lista"]] == "Skovo lotto 17-11"
    # 4. stessa email, stesso mittente (id 5), programmata
    (c,) = brevo.create
    assert c["subject"] == "Auto da privati sotto mercato" and c["previewText"] == "Ogni giorno"
    assert c["htmlContent"] == HTML and c["sender"] == {"id": 5} and c["replyTo"] == "info@skovo.app"
    assert c["scheduledAt"] == QUANDO and c["recipients"] == {"listIds": [out["lista"]]}
    assert c["name"] == "Skovo lotto 17-11 (r77)" and c["header"] == "[DEFAULT_HEADER]"
    assert out["campagna"] == 78 and out["caricati"] == 4 and out["restano_dopo_questo_giro"] == 0
    assert out["arrivati_base"] == 8
    # ricevuti e caricati segnati per la catena 77 (giri dopo)
    segnati = {h for (lav, h) in db.invii if lav == "giro_lotto:r77"}
    assert impronta("r0@salone0.it") in segnati and impronta("nuovo2@c.it") in segnati
    assert impronta("r3@salone3.it") not in segnati          # rimbalzo morbido della base: non è "ricevuto"
    # le cancellazioni vengono DOPO tutte le esportazioni
    esportazioni = [i for i, (m, u) in enumerate(brevo.chiamate) if u.endswith("exportRecipients")]
    cancellazioni = [i for i, (m, u) in enumerate(brevo.chiamate) if m == "DELETE"]
    assert len(esportazioni) == 1 + 3 * 5 and max(esportazioni) < min(cancellazioni)
    # nei log e nel riepilogo solo numeri, mai indirizzi
    testo = json.dumps(out) + " ".join(log)
    assert "@" not in testo
    assert all(isinstance(v, int) for k, v in out.items() if k not in ("fermo", "nota"))


def test_giro_dopo_non_ricarica_chi_ha_gia_ricevuto(monkeypatch):
    brevo, righe, db = _scenario()
    _prepara(monkeypatch, brevo, righe, db)
    GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, pausa_cancella=0, adesso=ADESSO)
    # secondo giro con base = la campagna 78 appena creata, inviata; nell'archivio un salone nuovo
    brevo.campagne[78] = {**brevo.campagne[77], "id": 78, "name": "Skovo lotto 17-11 (r77)",
                          "recipients": {"lists": [91]},
                          "statistics": {"globalStats": {"sent": 4, "unsubscriptions": 0, "hardBounces": 0,
                                                         "softBounces": 0, "complaints": 0}}}
    brevo.esportazioni[(78, "all")] = ["nuovo1@b.it", "nuovo2@c.it", "nuovo4@i.it", "r7@salone7.it"]
    righe.append({"chiave": "z", "email": ["nuovo5@z.it"], "prov": "MI", "nome": "Z"})
    brevo.importati.clear()
    brevo.create.clear()
    out = GL.giro_lotto(78, "2026-11-18T09:00", client=brevo, pausa=0, pausa_cancella=0, adesso=ADESSO)
    assert "fermo" not in out, out
    assert brevo.importati == ["nuovo5@z.it"]                 # né i ricevuti della 77 né quelli della 78
    assert brevo.create[0]["name"] == "Skovo lotto 18-11 (r77)" and brevo.create[0]["scheduledAt"].endswith("+01:00")
    # terzo giro: archivio finito -> niente cancellazioni, niente campagna
    brevo.campagne[79] = {**brevo.campagne[78], "id": 79, "name": "Skovo lotto 18-11 (r77)"}
    brevo.esportazioni[(79, "all")] = ["nuovo5@z.it"]
    brevo.campagne[79]["statistics"] = {"globalStats": {"sent": 1}}
    n_canc, n_create = len(brevo.cancellati), len(brevo.create)
    out = GL.giro_lotto(79, "2026-11-19T09:00", client=brevo, pausa=0, pausa_cancella=0, adesso=ADESSO)
    assert out.get("nota") == "archivio_finito_niente_campagna"
    assert len(brevo.cancellati) == n_canc and len(brevo.create) == n_create


def test_stop_prima_di_cancellare(monkeypatch):
    # campagna non inviata
    brevo, righe, db = _scenario()
    brevo.campagne[77]["status"] = "queued"
    _prepara(monkeypatch, brevo, righe, db)
    assert GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, adesso=ADESSO)["fermo"] == "campagna_base_non_inviata"
    assert not brevo.cancellati and not brevo.create
    # disiscritti esportati meno delle statistiche
    brevo, righe, db = _scenario()
    brevo.campagne[77]["statistics"]["globalStats"]["unsubscriptions"] = 5
    _prepara(monkeypatch, brevo, righe, db)
    out = GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, adesso=ADESSO)
    assert out["fermo"] == "esportazione_unsubscribed_77_incompleta" and not brevo.cancellati and not brevo.importati
    # esportazione di una precedente non riuscita
    brevo, righe, db = _scenario()
    _prepara(monkeypatch, brevo, righe, db)
    vecchio = GL._scarica
    monkeypatch.setattr(GL, "_scarica", lambda u: None if u.startswith("u:74:") else vecchio(u))
    out = GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, adesso=ADESSO)
    assert out["fermo"].startswith("esportazione_unsubscribed_74") and not brevo.cancellati
    # destinatari esportati troppo pochi
    brevo, righe, db = _scenario()
    brevo.esportazioni[(77, "all")] = brevo.esportazioni[(77, "all")][:5]
    _prepara(monkeypatch, brevo, righe, db)
    out = GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, adesso=ADESSO)
    assert out["fermo"] == "esportazione_destinatari_base_incompleta" and not brevo.cancellati
    # segnalazioni di spam più dei bloccati nella lista
    brevo, righe, db = _scenario()
    brevo.campagne[77]["statistics"]["globalStats"]["complaints"] = 9
    _prepara(monkeypatch, brevo, righe, db)
    assert GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, adesso=ADESSO)["fermo"] == "segnalazioni_spam_non_trovate"
    assert not brevo.cancellati
    # orario passato
    assert GL.giro_lotto(77, "2026-11-16T12:30:00+01:00", client=brevo, adesso=ADESSO)["fermo"] \
        == "quando_troppo_vicino_o_passato"
    # campagna del giro già creata
    brevo, righe, db = _scenario()
    brevo.campagne[99] = {"id": 99, "name": "Skovo lotto 17-11 (r77)", "status": "queued"}
    _prepara(monkeypatch, brevo, righe, db)
    assert GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, adesso=ADESSO)["fermo"] == "campagna_del_giro_gia_creata"
    assert not brevo.cancellati


def test_tetto_e_liste_in_attesa(monkeypatch):
    brevo, righe, db = _scenario()
    # r6 è anche nella lista di una campagna programmata: non si cancella
    brevo.contatti["r6@salone6.it"]["listIds"].append(95)
    brevo.campagne[96] = {"id": 96, "name": "programmata", "status": "queued", "recipients": {"lists": [95]}}
    _prepara(monkeypatch, brevo, righe, db)
    import dealfinder.marketing.brevo_liste as BL
    monkeypatch.setattr(BL, "TETTO_BREVO", 8)        # dopo le cancellazioni: r1, r3, r5, r6, r7, r8, altro = 7
    out = GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, pausa_cancella=0, adesso=ADESSO)
    assert "r6@salone6.it" not in brevo.cancellati and out["caricati"] == 1 and out["restano_dopo_questo_giro"] == 3
    assert len(brevo.contatti) <= 8


def test_funzioni_pure():
    assert GL.leggi_valore("giro_lotto:77:2026-11-17T09:00:00+01:00") == (77, "2026-11-17T09:00:00+01:00", None)
    assert GL.leggi_valore("giro_lotto:77@2026-11-17T09:00~73,74") == (77, "2026-11-17T09:00", (73, 74))
    assert GL.normalizza_quando("2026-11-17T09:00") == "2026-11-17T09:00:00+01:00"
    assert GL.normalizza_quando("2026-11-17T08:00:00Z") == "2026-11-17T08:00:00+00:00"
    assert GL.radice_da_nome("Skovo lotto 17-11 (r77)", 78) == 77 and GL.radice_da_nome("Skovo corta", 77) == 77
    assert GL.rimbalzi_ripetuti([["A@x.it", "b@x.it"], ["a@x.it"], ["c@x.it"]]) == {"a@x.it"}


def test_con_il_tetto_a_500_000_non_si_cancella_e_skovo_tutti_mai(monkeypatch):
    import dealfinder.marketing.brevo_liste as BL
    # tetto vero (500.000): c'è posto, nessuno viene cancellato, il giro carica lo stesso i suoi 4
    brevo, righe, db = _scenario()
    _prepara(monkeypatch, brevo, righe, db)
    monkeypatch.setattr(BL, "TETTO_BREVO", 500_000)
    out = GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, pausa_cancella=0, adesso=ADESSO)
    assert "fermo" not in out and not brevo.cancellati and out["tolti_da_brevo"] == 0 and len(brevo.importati) == 4
    # al massimo TETTO_INVIO_GIRO contatti nuovi per giro, anche se in Brevo c'è posto per 500.000
    brevo, righe, db = _scenario()
    _prepara(monkeypatch, brevo, righe, db)
    monkeypatch.setattr(BL, "TETTO_BREVO", 500_000)
    monkeypatch.setattr(BL, "TETTO_INVIO_GIRO", 2)
    out = GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, pausa_cancella=0, adesso=ADESSO)
    assert out["caricati"] == 2 and len(brevo.importati) == 2 and out["restano_dopo_questo_giro"] == 2
    # anche col tetto pieno: chi sta in una lista "Skovo tutti ..." non si cancella, neppure se è la lista della base
    brevo, righe, db = _scenario()
    brevo.liste[84] = "Skovo tutti 24-11"
    _prepara(monkeypatch, brevo, righe, db)
    GL.giro_lotto(77, QUANDO, client=brevo, pausa=0, pausa_cancella=0, adesso=ADESSO)
    assert not brevo.cancellati
