"""24/11: email "come il WhatsApp" ai commercianti (marketing/email_esempi.py). Brevo e database finti, indirizzi finti."""
import asyncio
import datetime as dt
import json
import re

from starlette.requests import Request

from dealfinder.marketing import brevo_liste as BL
from dealfinder.marketing import email_esempi as EE
from dealfinder.marketing import email_templates as ET
from dealfinder.marketing import sms_saloni as SS
from dealfinder.marketing.invii import impronta
from dealfinder.web import app as A

ADESSO = dt.datetime(2026, 11, 25, 9, 0, tzinfo=dt.timezone.utc)
ENV = {"PUBLIC_URL": "https://scovo.onrender.com"}
B = "https://api.brevo.com/v3"


# ---------- finti ----------
class _Cur:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class Db:
    """Database finto: capisce solo le frasi di email_esempi (e le poche degli SMS che servono alle rotte)."""

    def __init__(self):
        self.link, self.clic, self.disiscritti, self.sms = {}, [], {impronta(f"vecchio{i}@x.it") for i in range(200)}, set()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, q, p=()):
        q = " ".join(q.split())
        if q.startswith("CREATE"):
            return _Cur([])
        if q.startswith("SELECT token, email, nome, telefono, giro, creato_at FROM email_link WHERE token"):
            x = self.link.get(p[0])
            return _Cur([dict(x, token=p[0])] if x else [])
        if q.startswith("SELECT email, token FROM email_link WHERE giro"):
            return _Cur([{"email": x["email"], "token": t} for t, x in self.link.items() if x["giro"] == p[0]])
        if q.startswith("SELECT 1 FROM sms_link WHERE token"):
            return _Cur([{"x": 1}] if p[0] in self.sms else [])
        if q.startswith("INSERT INTO email_link"):
            if p[0] in self.link or any(x["email"] == p[1] and x["giro"] == p[4] for x in self.link.values()):
                return _Cur([])
            self.link[p[0]] = {"email": p[1], "nome": p[2], "telefono": p[3], "giro": p[4], "versione": p[5],
                               "creato_at": ADESSO - dt.timedelta(hours=20), "stop": False}
            return _Cur([{"token": p[0]}])
        if q.startswith("UPDATE email_link SET versione"):
            self.link[p[1]]["versione"] = p[0]
            return _Cur([])
        if q.startswith("UPDATE email_link SET stop_at"):
            self.link[p[0]]["stop"] = True
            return _Cur([])
        if q.startswith("INSERT INTO email_clic"):
            self.clic.append({"token": p[0], "user_agent": p[1], "anteprima": p[2]})
            return _Cur([])
        if q.startswith("SELECT count(*) AS n FROM email_clic"):
            return _Cur([{"n": sum(1 for c in self.clic if c["token"] == p[0] and c["anteprima"] is None)}])
        if q.startswith("SELECT email_hash FROM disiscritti"):
            return _Cur([{"email_hash": h} for h in self.disiscritti])
        if q.startswith("INSERT INTO disiscritti"):
            nuovo = p[0] not in self.disiscritti
            self.disiscritti.add(p[0])
            return _Cur([{"email_hash": p[0]}] if nuovo else [])
        if q.startswith("SELECT 'email' AS canale, l.token"):
            out = []
            for t, x in self.link.items():
                miei = [c for c in self.clic if c["token"] == t]
                if miei:
                    out.append({"canale": "email", "token": t, "nome": x["nome"], "telefono": x["telefono"],
                                "email": x["email"], "giro": x["giro"], "clic": sum(1 for c in miei if c["anteprima"] is None),
                                "forse_anteprima": sum(1 for c in miei if c["anteprima"]), "primo_clic": None,
                                "ultimo_clic": None, "stop": x["stop"]})
            return _Cur(out)
        # le frasi degli SMS usate dalle rotte: nessun token SMS in questi test
        if q.startswith(("SELECT nome, telefono FROM sms_link", "SELECT telefono FROM sms_link WHERE token")):
            return _Cur([])
        if q.startswith("SELECT l.token"):
            return _Cur([])
        raise AssertionError("frase non prevista: " + q[:80])


class _R:
    def __init__(self, status=200, dati=None, headers=None):
        self.status_code, self._d, self.text, self.headers = status, dati if dati is not None else {}, "", headers or {}

    def json(self):
        return self._d

    def raise_for_status(self):
        assert self.status_code < 300, self.status_code


class Brevo:
    """Brevo finto: contatti, liste, attributi, import, statistiche del contatto, campagne, mittenti."""

    def __init__(self, contatti=(), liste=None, inviati=None, campagne=(), mittenti=()):
        self.contatti = [dict(x) for x in contatti]
        self.liste = dict(liste or {BL.LISTA_TUTTI: 91})
        self.attributi = {"AZIENDA"}
        self.inviati = dict(inviati or {})        # email -> [eventTime]
        self.campagne = list(campagne)
        self.mittenti = list(mittenti)
        self.importi, self.tolti, self.messi, self.post_altri, self.chiamate = [], [], [], [], []
        self.errori_import = 0

    def get(self, url, params=None, **k):
        self.chiamate.append(("GET", url))
        if url == f"{B}/account":
            return _R(200, {})
        if url == f"{B}/contacts/lists":
            nomi = sorted(self.liste.items(), key=lambda x: x[1])[(params or {}).get("offset", 0):][:50]
            return _R(200, {"lists": [{"name": n, "id": i} for n, i in nomi]})
        if url == f"{B}/contacts":
            off = (params or {}).get("offset", 0)
            return _R(200, {"contacts": self.contatti[off:off + 1000]})
        if url == f"{B}/contacts/attributes":
            return _R(200, {"attributes": [{"name": a} for a in sorted(self.attributi)]})
        if url == f"{B}/contacts/folders":
            return _R(200, {"folders": [{"name": "Skovo", "id": 5}]})
        if url == f"{B}/senders":
            return _R(200, {"senders": self.mittenti})
        if url == f"{B}/emailCampaigns":
            return _R(200, {"campaigns": self.campagne})
        m = re.fullmatch(re.escape(B) + r"/contacts/lists/(\d+)", url)
        if m:
            lid = int(m.group(1))
            return _R(200, {"uniqueSubscribers": sum(1 for x in self.contatti if lid in x.get("listIds", []))})
        m = re.fullmatch(re.escape(B) + r"/contacts/(.+)/campaignStats", url)
        if m:
            e = m.group(1).replace("%40", "@")
            if self.inviati.get(e) == "errore":
                return _R(404)
            return _R(200, {"messagesSent": [{"campaignId": 1, "eventTime": t} for t in self.inviati.get(e, [])]})
        if url.startswith(f"{B}/processes/"):
            return _R(200, {"status": "completed"})
        raise AssertionError("GET non previsto: " + url)

    def post(self, url, json=None, **k):
        self.chiamate.append(("POST", url))
        if url == f"{B}/contacts/import":
            if self.errori_import:
                self.errori_import -= 1
                return _R(500)
            self.importi.append(json)
            for x in json["jsonBody"]:
                c = next(c for c in self.contatti if c["email"] == x["email"])
                c.setdefault("attributes", {}).update(x["attributes"])
                c["listIds"] = sorted(set(c.get("listIds", [])) | set(json["listIds"]))
            return _R(202, {"processId": len(self.importi)})
        if url == f"{B}/contacts/lists":
            self.liste[json["name"]] = 100 + len(self.liste)
            return _R(201, {"id": self.liste[json["name"]]})
        m = re.fullmatch(re.escape(B) + r"/contacts/attributes/normal/(\w+)", url)
        if m:
            self.attributi.add(m.group(1))
            return _R(201)
        m = re.fullmatch(re.escape(B) + r"/contacts/lists/(\d+)/contacts/remove", url)
        if m:
            self.tolti.append((int(m.group(1)), list(json["emails"])))
            return _R(201)
        if url == f"{B}/senders":
            self.mittenti.append({"id": 9, "name": json["name"], "email": json["email"], "active": True})
            return _R(201, {"id": 9})
        self.post_altri.append(url)
        raise AssertionError("POST non previsto: " + url)

    def put(self, url, json=None, **k):
        self.messi.append((url, json))
        return _R(204)

    def close(self):
        pass



class _Log:
    def __init__(self):
        self.righe = []

    def warning(self, m, *a):
        self.righe.append(m % a if a else m)


def _auto(i, nome="Fiat Panda 1.2", zona="Monza (MB)", prezzo=6000, margine=2000, **k):
    pv = {"categoria": "opportunita", "margine": margine, "netto": 900, "n": 12}
    pv.update(k.pop("pv", {}))
    return dict({"id": i, "nome": nome, "anno": 2019, "km": 80000, "carb": "benzina", "prezzo": prezzo, "zona": zona,
                 "pv": pv}, **k)


ITEMS = [_auto(1), _auto(2, "Opel Corsa 1.2", "Sesto San Giovanni (MI)", 7000, 1800),
         _auto(3, "Volkswagen Golf 2.0 TDI", "Milano (MI)", 9000, 2500, carb="diesel"),
         _auto(4, "Toyota Yaris 1.0", "Milano (MI)", 7500, 1500),
         _auto(5, "Lancia Ypsilon 1.2", "Cologno Monzese (MI)", 5500, 1300)]


def _salone(email, comune="Monza", prov="MB", **k):
    return dict({"email": email, "nome": "Salone " + email.split("@")[0], "comune": comune, "prov": prov, "tel": "",
                 "pmed": None, "marche": [], "km": EE.km_da_milano(comune, prov) if comune else None}, **k)


# ---------- auto ----------
def test_auto_usabili_regole_del_giro_whatsapp():
    items = ITEMS + [
        _auto(10, margine=1100),                                   # margine sotto 1.200
        _auto(11, pv={"netto": 200}),                              # netto sotto 300
        _auto(12, pv={"n": 4}),                                    # meno di 5 auto uguali
        _auto(13, prezzo=61000),                                   # oltre 60.000 €
        _auto(14, zona="Napoli (NA)"),                             # fuori dai 60 km
        _auto(15, pv={"categoria": "non_conveniente"}),
        _auto(16, zona=""),                                        # senza zona
    ]
    buone = EE.auto_usabili(items)
    assert sorted(a["id"] for a in buone) == [1, 2, 3, 4, 5]
    per = {a["id"]: a for a in buone}
    assert per[3]["diesel"] and not per[1]["diesel"] and per[4]["kmz"] == 0.0 and 0 < per[1]["kmz"] <= 60


def test_diesel_anche_dal_nome():
    assert EE.e_diesel({"nome": "BMW Serie 1 116d", "carb": ""}) and EE.e_diesel({"nome": "Audi A3 2.0 TDI", "carb": "altro"})
    assert EE.e_diesel({"nome": "Range Rover Evoque 1.5 PHEV", "carb": "ibrida"})
    assert not EE.e_diesel({"nome": "Fiat Panda 1.2", "carb": "benzina"})


def test_testi_come_il_whatsapp():
    a = EE.auto_usabili([_auto(1114846, "Volkswagen Passat 2.0 TDI DSG Highline", "Agrate Brianza (MB)", 16999, 4723,
                               anno=2022, km=56666, carb="diesel")])[0]
    t = EE.testi(a, "https://scovo.onrender.com")
    assert t["AUTO"] == "Volkswagen Passat 2.0 TDI DSG Highline 2022 · 56.666 km · diesel · Agrate Brianza"
    assert t["DATI"] == "16.999 € · mercato 21.722 € · margine +4.723 €"
    assert t["FOTO"] == "https://scovo.onrender.com/email-foto/1114846/0.jpg"


def test_mai_diesel_a_un_salone_del_comune_di_milano():
    auto = EE.auto_usabili(ITEMS)
    due = EE.scegli_due(_salone("a@milano.it", "Milano", "MI"), auto, {})
    assert len(due) == 2 and not any(a["diesel"] for a in due)
    solo_diesel = [a for a in auto if a["diesel"]] + [a for a in auto if a["id"] == 4]
    assert EE.scegli_due(_salone("a@milano.it", "Milano", "MI"), solo_diesel, {}) == []      # resta una sola auto: niente
    assert len(EE.scegli_due(_salone("b@sesto.it", "Sesto San Giovanni", "MI"), solo_diesel, {})) == 2


def test_distanza_fascia_uso_e_modelli_diversi():
    auto = EE.auto_usabili(ITEMS + [_auto(6, "Fiat Panda 1.2 Lounge", "Monza (MB)", 6100, 2100)])
    due = EE.scegli_due(_salone("a@x.it"), auto, {})
    assert len({" ".join(a["nome"].split()[:2]) for a in due}) == 2                          # mai due Panda
    assert all(_salone("a@x.it")["km"] + a["kmz"] <= 60 for a in due)
    assert EE.scegli_due(_salone("a@x.it", comune="", prov=""), auto, {}) == []              # senza posizione
    assert EE.scegli_due(_salone("a@x.it", "Napoli", "NA"), auto, {}) == []                  # fuori zona = senza posizione
    lontano = _salone("a@x.it")
    lontano["km"] = 59.0
    assert all(a["kmz"] <= 1 for a in EE.scegli_due(lontano, auto, {}))                      # solo le auto di Milano
    caro = _salone("a@x.it", pmed=40000)
    assert EE.scegli_due(caro, auto, {}) == []                                               # fascia alta: nessuna adatta
    uso = {a["id"]: 5 for a in auto}
    assert EE.scegli_due(_salone("a@x.it"), auto, uso) == []                                 # già date a 5 saloni
    stesso = EE.scegli_due(_salone("a@x.it", marche=["Lancia"]), auto, {})
    assert stesso[0]["id"] == 5                                                              # la marca che tratta


def test_la_prima_auto_deve_avere_la_foto():
    auto = EE.auto_usabili(ITEMS)
    prima = EE.scegli_due(_salone("a@x.it"), auto, {})[0]["id"]
    chieste = []
    due = EE.scegli_due(_salone("a@x.it"), auto, {}, foto_ok=lambda i: chieste.append(i) or i != prima)
    assert len(due) == 2 and due[0]["id"] != prima and chieste[0] == prima
    assert EE.scegli_due(_salone("a@x.it"), EE.auto_usabili(ITEMS), {}, foto_ok=lambda i: False) == []


def test_due_gruppi_vicini_a_milano_e_simili_per_zona():
    auto = EE.auto_usabili(ITEMS + [_auto(20 + i, f"Marca{i} Modello{i}", "Milano (MI)", 6000 + i, 2000) for i in range(30)])
    comuni = [("Milano", "MI"), ("Sesto San Giovanni", "MI"), ("Cologno Monzese", "MI"), ("Monza", "MB"), ("Brugherio", "MB")]
    saloni = [_salone(f"s{i:02d}@x.it", *comuni[i % 5]) for i in range(40)] + [_salone("ignoto@x.it", "", "")]
    con, senza, n = EE.dividi(saloni, auto, per_gruppo=4)
    assert len(con) == 4 and len(senza) == 4 and not {d["email"] for d in con} & {d["email"] for d in senza}
    assert all(len(d["auto"]) == 2 for d in con) and all(d["auto"] == [] for d in senza)
    assert max(d["km"] for d in con + senza) == 0.0                                          # gli 8 più vicini: Milano
    assert all(d["email"] != "ignoto@x.it" for d in con + senza)
    # chi ha ricevuto una email da poco (o non si sa) si salta
    recenti = {saloni[0]["email"]: True, saloni[5]["email"]: None}
    con, senza, n = EE.dividi(saloni, auto, 6, recente=lambda e: recenti.get(e, False))
    assert n["saltati_email_recente"] == 1 and n["saltati_non_verificati"] == 1
    assert not {saloni[0]["email"], saloni[5]["email"]} & {d["email"] for d in con + senza} and len(con) == len(senza) == 6
    # senza numero: tutti, chi non ha posizione va senza esempi
    con, senza, n = EE.dividi(saloni, auto, 0, uso_max=100)
    assert len(con) + len(senza) == 41 and n["senza_posizione"] == 1 and len(con) == 40


def test_km_da_milano():
    assert EE.km_da_milano("Milano", "MI") == 0.0 and EE.km_da_milano("MILANO (MI)") == 0.0
    assert EE.km_da_milano("Monza", "MB") > 5 and EE.km_da_milano("Monza", "RM") is None
    assert EE.km_da_milano("Roma", "RM") is None and EE.km_da_milano("") is None
    assert EE.km_da_milano("Sesto San Giovanni", "MI") == 9.0 and EE.km_da_milano("Cassano d'Adda") == 27.0
    assert EE.km_da_milano("Comune Che Non So", "MI") == EE.KM_MI_IGNOTO and EE.km_da_milano("Comune Che Non So") is None
    assert all(0 < v <= 60 for v in EE.KM_MI.values()) and "milano" not in EE.KM_MI


# ---------- il lavoro ----------
def _scena_lavoro(n=12, **k):
    contatti = [{"email": f"s{i:02d}@salone{i:02d}.it", "listIds": [91], "attributes": {"AZIENDA": f"Salone {i:02d}"}}
                for i in range(n)]
    contatti += [
        {"email": "bloccato@x.it", "listIds": [91], "emailBlacklisted": True, "attributes": {}},
        {"email": "fuori@lista.it", "listIds": [7], "attributes": {}},
        {"email": "info@officinemulino.it", "listIds": [91], "attributes": {}},
        {"email": "ditta@pec.it", "listIds": [91], "attributes": {}},
        {"email": "ok@loconteauto.it", "listIds": [91], "attributes": {}},
        {"email": "vecchio1@x.it", "listIds": [91], "attributes": {}},                       # nei disiscritti del database
    ]
    comuni = [("Milano", "MI"), ("Monza", "MB"), ("Sesto San Giovanni", "MI")]
    profili = {f"s{i:02d}@salone{i:02d}.it": {"nome": f"Salone {i:02d}", "comune": comuni[i % 3][0], "prov": comuni[i % 3][1],
                                              "tel": "+393330001234" if i == 0 else "", "pmed": None, "marche": []}
               for i in range(n) if i != 3}                                                   # il 3 non ha posizione
    return Brevo(contatti, **k), Db(), profili


def _prepara(brevo, db, profili, per_gruppo=0, items=None, **k):
    vecchio = EE.profili_db
    EE.profili_db = lambda conn: profili
    try:
        return EE.prepara(items if items is not None else ITEMS + [_auto(30 + i, f"Marca{i} Tipo{i}", "Milano (MI)", 6000, 2000)
                                                                    for i in range(20)],
                          "email_esempi:t", per_gruppo, c=brevo, connetti=lambda: db, foto_ok=lambda i: True,
                          dormi=lambda s: None, env=ENV, **dict({"recente": None}, **k))
    finally:
        EE.profili_db = vecchio


def test_lavoro_tutti_attributi_liste_e_nessun_invio():
    brevo, db, profili = _scena_lavoro()
    esito = _prepara(brevo, db, profili)
    assert esito["nella_lista"] == 17 and esito["bloccati_in_brevo"] == 1 and esito["validi"] == 12
    assert esito["esclusi"]["escluse_loconte"] == 1 and esito["esclusi"]["escluse_pec"] == 1
    assert esito["esclusi"]["escluse_altri_esclusi"] == 1 and esito["esclusi"]["escluse_disiscritte_o_rimbalzate"] == 1
    assert esito["con_esempi"] == 11 and esito["senza_esempi"] == 1 and esito["senza_posizione"] == 1
    assert esito["token_nuovi"] == 12 and EE.incompleto(esito) is None and esito["mittente"]["creato"]
    assert set(EE.ATTRIBUTI) <= brevo.attributi and {EE.LISTA_CON, EE.LISTA_SENZA} <= set(brevo.liste)
    assert all(i["emptyContactsAttributes"] and i["updateExistingContacts"] for i in brevo.importi)
    mandati = {x["email"]: x["attributes"] for i in brevo.importi for x in i["jsonBody"]}
    assert set(mandati) == {f"s{i:02d}@salone{i:02d}.it" for i in range(12)}                 # mai bloccati né esclusi
    a = mandati["s01@salone01.it"]
    assert re.fullmatch(r"https://scovo\.onrender\.com/mi/[a-z0-9]{7}", a["MI_LINK"]) and a["STOP_LINK"] == a["MI_LINK"] + "/stop"
    assert a["AUTO1"] and a["AUTO2"] and "margine +" in a["DATI1"] and "mercato" in a["DATI2"]
    assert re.fullmatch(r"https://scovo\.onrender\.com/email-foto/\d+/0\.jpg", a["FOTO1"]) and a["FOTO1"] != a["FOTO2"]
    s = mandati["s03@salone03.it"]                                                            # senza posizione: senza esempi
    assert s["MI_LINK"] and all(s[k] == "" for k in EE.ATTRIBUTI_AUTO)
    assert len({x["MI_LINK"] for x in mandati.values()}) == 12                               # un link per salone
    milano = [mandati[f"s{i:02d}@salone{i:02d}.it"] for i in (0, 6, 9)]
    assert not any("diesel" in x["AUTO1"] + x["AUTO2"] for x in milano)
    # il telefono (se c'è) e il canale stanno solo nel database
    assert sorted(x["telefono"] for x in db.link.values())[-1] == "+393330001234"
    assert all(x["giro"] == "email_esempi:t" for x in db.link.values())
    # nessuna campagna, nessun invio
    assert not any("emailCampaigns" in u or "smtp" in u or "sendNow" in u for _, u in brevo.chiamate)
    assert not brevo.messi


def test_lavoro_ripetuto_stessi_token_e_cambio_di_lista():
    brevo, db, profili = _scena_lavoro()
    _prepara(brevo, db, profili)
    prima = {x["email"]: t for t, x in db.link.items()}
    con_id, senza_id = brevo.liste[EE.LISTA_CON], brevo.liste[EE.LISTA_SENZA]
    esito = _prepara(brevo, db, profili, items=[])                                            # il sito non ha più auto adatte
    assert esito["token_nuovi"] == 0 and {x["email"]: t for t, x in db.link.items()} == prima
    assert esito["con_esempi"] == 0 and esito["senza_esempi"] == 12 and esito["tolti_dalle_liste"] == 11
    assert brevo.tolti and all(lid == con_id for lid, _ in brevo.tolti) and senza_id != con_id
    assert all(v == "" for x in brevo.importi[-1]["jsonBody"] for k, v in x["attributes"].items() if k in EE.ATTRIBUTI_AUTO)


def test_lavoro_150_per_gruppo_in_piccolo_e_salta_chi_ha_ricevuto_da_72_ore():
    ieri = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=10)).isoformat()
    vecchia = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=100)).isoformat()
    brevo, db, profili = _scena_lavoro(inviati={"s00@salone00.it": [ieri], "s06@salone06.it": [vecchia],
                                                "s09@salone09.it": "errore"},
                                       mittenti=[{"id": 4, "email": "info@skovo.net", "active": True}])
    esito = _prepara(brevo, db, profili, per_gruppo=3, recente="brevo")
    assert esito["con_esempi"] == 3 and esito["senza_esempi"] == 3 and esito["per_gruppo"] == 3
    assert esito["saltati_email_recente"] == 1 and esito["saltati_non_verificati"] == 1
    assert esito["mittente"] == {"id": 4, "attivo": True, "creato": False}
    scelti = {x["email"] for i in brevo.importi for x in i["jsonBody"]}
    assert len(scelti) == 6 and "s00@salone00.it" not in scelti and "s09@salone09.it" not in scelti
    assert "s06@salone06.it" in scelti and "s03@salone03.it" not in scelti                   # 100 ore fa va bene; senza posizione no
    assert len(db.link) == 6                                                                 # token solo a chi entra


def test_lavoro_si_ferma_senza_disiscritti_o_senza_lista():
    brevo, db, profili = _scena_lavoro()
    db.disiscritti = set()
    brevo.contatti = [x for x in brevo.contatti if not x.get("emailBlacklisted")]
    assert _prepara(brevo, db, profili)["fermo"] == "disiscritti_incompleti" and not brevo.importi
    brevo, db, profili = _scena_lavoro(liste={"Altra": 3})
    assert _prepara(brevo, db, profili)["fermo"] == "lista_tutti_assente" and not brevo.importi


def test_blocco_rifiutato_lavoro_incompleto():
    brevo, db, profili = _scena_lavoro()
    brevo.errori_import = 3                                                                   # i 3 tentativi del primo blocco
    esito = _prepara(brevo, db, profili)
    assert esito["blocchi_falliti"] == 1 and EE.incompleto(esito) == "blocchi_falliti"


def test_log_solo_conteggi():
    brevo, db, profili = _scena_lavoro()
    log, fatti = _Log(), []
    v = (EE.lg, EE.prepara, EE.profili_db)
    EE.lg = log
    EE.profili_db = lambda conn: profili
    EE.prepara = lambda items, giro, per_gruppo: fatti.append((giro, per_gruppo)) or v[1](
        ITEMS + [_auto(30 + i, f"Marca{i} Tipo{i}", "Milano (MI)", 6000, 2000) for i in range(20)], giro, per_gruppo, c=brevo,
        connetti=lambda: db, foto_ok=lambda i: True, dormi=lambda s: None, env=ENV, recente=None)
    try:
        assert EE.lavoro("email_esempi:2411:4", lambda: ITEMS, lungo=lambda chiave, nome, fn: (fn(), chiave)[1]) \
            == "email_esempi:2411:4"
    finally:
        EE.lg, EE.prepara, EE.profili_db = v
    assert fatti == [("email_esempi:2411", 4)]
    riga = next(r for r in log.righe if r.startswith("EMAIL_ESEMPI "))
    assert "@" not in riga and "+39" not in riga and "Salone" not in riga and json.loads(riga[13:])["con_esempi"] == 4


def test_lavoro_senza_auto_del_sito_non_risulta_fatto():
    def lungo(chiave, nome, fn):
        try:
            fn()
        except RuntimeError as e:
            return str(e)
    assert EE.lavoro("email_esempi:t", lambda: [], lungo=lungo, attesa_min=2, dormi=lambda s: None) \
        == "il sito non ha ancora le auto"


# ---------- clic, anteprime, stop ----------
def _db_con_link(tel="+393330009876"):
    db = Db()
    db.link["abc2345"] = {"email": "titolare@salone.it", "nome": "Salone Prova", "telefono": tel, "giro": "email_esempi:t",
                          "versione": "con", "creato_at": ADESSO - dt.timedelta(hours=20), "stop": False}
    return db


def _iso(secondi_fa):
    return (ADESSO - dt.timedelta(seconds=secondi_fa)).isoformat()


def test_clic_vero_registrato_senza_dati_personali():
    db, log = _db_con_link(), _Log()
    r = EE.registra_clic(db, "abc2345", "Mozilla/5.0 (iPhone)", c=Brevo(inviati={"titolare@salone.it": [_iso(3600)]}),
                         adesso=ADESSO, log=log, env={})
    assert r["contato"] and r["n"] == 1 and r["dopo_s"] == 3600
    assert log.righe == ["EMAIL_INTERESSATO token=abc2345 canale=email nome=Salone Prova tel=***9876 n=1 dopo_s=3600"]
    assert "titolare" not in log.righe[0] and "3330009876" not in log.righe[0]
    db2, log2 = _db_con_link(tel=""), _Log()
    EE.registra_clic(db2, "abc2345", "Mozilla/5.0", c=Brevo(inviati={"titolare@salone.it": [_iso(900)]}), adesso=ADESSO,
                     log=log2, env={})
    assert " tel=- " in log2.righe[0]


def test_clic_pochi_secondi_dopo_l_invio_e_forse_anteprima():
    db, log = _db_con_link(), _Log()
    brevo = Brevo(inviati={"titolare@salone.it": [_iso(8)]})
    r = EE.registra_clic(db, "abc2345", "Mozilla/5.0 (Windows NT 10.0)", c=brevo, adesso=ADESSO, log=log, env={})
    assert r == {"contato": False, "motivo": "anteprima", "perche": "subito_dopo_l_invio", "dopo_s": 8}
    assert log.righe[0].startswith("EMAIL_FORSE_ANTEPRIMA token=abc2345 nome=Salone Prova tel=***9876 motivo=subito_dopo_l_invio")
    assert db.clic[0]["anteprima"] == "subito_dopo_l_invio" and not any("INTERESSATO" in x for x in log.righe)
    # lo stesso salone tocca davvero il pulsante un'ora dopo: conta, ed è il primo clic vero
    r = EE.registra_clic(db, "abc2345", "Mozilla/5.0 (iPhone)", c=brevo, adesso=ADESSO + dt.timedelta(hours=1), log=log, env={})
    assert r["contato"] and r["n"] == 1 and log.righe[-1].startswith("EMAIL_INTERESSATO")
    # soglia regolabile
    db3 = _db_con_link()
    assert EE.registra_clic(db3, "abc2345", "Mozilla/5.0", c=brevo, adesso=ADESSO, log=_Log(),
                            env={"EMAIL_ANTEPRIMA_S": "5"})["contato"]


def test_brevo_non_ha_ancora_scritto_l_invio_vale_la_partenza_della_campagna():
    EE._campagne.update(t=0.0, partenze=[])
    db, log = _db_con_link(), _Log()
    brevo = Brevo(campagne=[{"status": "sent", "sentDate": _iso(200)}, {"status": "draft", "scheduledAt": _iso(5)},
                            {"status": "sent", "sentDate": (ADESSO - dt.timedelta(days=9)).isoformat()}])
    r = EE.registra_clic(db, "abc2345", "Mozilla/5.0", c=brevo, adesso=ADESSO, log=log, env={})
    assert not r["contato"] and r["perche"] == "subito_dopo_la_partenza" and r["dopo_s"] == 200
    EE._campagne.update(t=0.0, partenze=[])
    r = EE.registra_clic(db, "abc2345", "Mozilla/5.0", c=brevo, adesso=ADESSO + dt.timedelta(minutes=30), log=log, env={})
    assert r["contato"] and r["dopo_s"] == 2000
    EE._campagne.update(t=0.0, partenze=[])


def test_invio_ignoto_il_clic_conta_e_i_programmi_automatici_no():
    EE._campagne.update(t=0.0, partenze=[])
    db, log = _db_con_link(), _Log()
    r = EE.registra_clic(db, "abc2345", "Mozilla/5.0", c=Brevo(), adesso=ADESSO, log=log, env={})
    assert r["contato"] and r["dopo_s"] is None and log.righe[0].endswith("dopo_s=?")

    class Rotto:
        def get(self, *a, **k):
            raise OSError("rete")
    assert EE.registra_clic(db, "abc2345", "Mozilla/5.0", c=Rotto(), adesso=ADESSO, log=log, env={})["contato"]
    brevo = Brevo()
    r = EE.registra_clic(db, "abc2345", "Mozilla/5.0 (compatible; GoogleImageProxy)\nfinto", c=brevo, adesso=ADESSO, log=log, env={})
    assert r["perche"] == "programma_automatico" and not brevo.chiamate and "\n" not in log.righe[-1]
    assert EE.registra_clic(db, "nonce99", "Mozilla/5.0", c=brevo, adesso=ADESSO, log=log) == {"contato": False, "motivo": "sconosciuto"}
    assert EE.registra_clic(db, "<b>", "Mozilla/5.0", c=brevo, adesso=ADESSO, log=log)["motivo"] == "sconosciuto"


def test_stop_disiscrive_nel_database_e_in_brevo():
    db, log, brevo = _db_con_link(), _Log(), Brevo()
    assert EE.registra_stop(db, "abc2345", c=brevo, log=log, dormi=lambda s: None)
    assert impronta("titolare@salone.it") in db.disiscritti and db.link["abc2345"]["stop"]
    assert brevo.messi == [(f"{B}/contacts/titolare%40salone.it", {"emailBlacklisted": True})]
    assert log.righe == ["EMAIL_STOP token=abc2345 nome=Salone Prova brevo=ok"] and "titolare" not in log.righe[0]
    assert not EE.registra_stop(db, "nonce99", c=brevo, log=log)

    class Giu(Brevo):
        def put(self, url, json=None, **k):
            return _R(500)
    db, log = _db_con_link(), _Log()
    assert EE.registra_stop(db, "abc2345", c=Giu(), log=log, dormi=lambda s: None)            # nel database resta escluso
    assert impronta("titolare@salone.it") in db.disiscritti and log.righe[-1].endswith("brevo=errore")


# ---------- rotte ----------
def _req(path, token, ua="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)", ip="93.40.1.2", metodo="GET"):
    return Request({"type": "http", "method": metodo, "path": path, "query_string": b"", "path_params": {"token": token},
                    "headers": [(b"user-agent", ua.encode()), (b"x-real-ip", ip.encode())], "client": (ip, 1)})


class _Scena:
    def __init__(self, brevo=None):
        self.db, self.log, self.brevo = _db_con_link(), _Log(), brevo or Brevo(inviati={"titolare@salone.it": [_iso(3600)]})
        self.v = (A.connect, EE.lg, SS.lg, EE._client)
        A.connect, EE.lg, SS.lg, EE._client = (lambda: self.db), self.log, self.log, (lambda: self.brevo)
        A._sms_ip.clear()

    def fine(self):
        A.connect, EE.lg, SS.lg, EE._client = self.v
        A._sms_ip.clear()


def test_pagina_grazie_uguale_agli_sms_con_il_pulsante_delle_email():
    s = _Scena()
    try:
        r = A.sms_mi_interessa(_req("/mi/abc2345", "abc2345"))
        corpo = r.body.decode()
        assert r.status_code == 200 and "Grazie!" in corpo and "Ti chiamiamo a breve" in corpo and "348 242 5633" in corpo
        assert "Non voglio più ricevere email" in corpo and "Non voglio più ricevere SMS" not in corpo
        assert 'action="/mi/abc2345/stop"' in corpo and "noindex" in r.headers["x-robots-tag"]
        assert "Salone Prova" not in corpo and "titolare" not in corpo and "9876" not in corpo
        assert corpo.replace("email</button>", "SMS</button>") == SS.pagina("abc2345")          # per il resto la stessa pagina
        assert not s.db.clic                                                                  # il clic si registra dopo la risposta
        asyncio.run(r.background())
        assert len(s.db.clic) == 1 and s.log.righe[-1].startswith("EMAIL_INTERESSATO token=abc2345 canale=email")
        assert not any(x.startswith("SMS_INTERESSATO") for x in s.log.righe)
        # un token che non è di una email resta sul giro degli SMS
        r = A.sms_mi_interessa(_req("/mi/nonce99", "nonce99"))
        assert r.background is None and "Non voglio più ricevere SMS" in r.body.decode()
        assert s.log.righe[-1] == "SMS_INTERESSATO token=nonce99 nome=? tel=***? n=0"
        # HEAD non conta
        assert A.sms_mi_interessa(_req("/mi/abc2345", "abc2345", metodo="HEAD")).background is None
    finally:
        s.fine()


def test_non_mi_interessa_chiede_conferma_e_solo_il_pulsante_disiscrive():
    s = _Scena()
    try:
        r = A.sms_mi_stop(_req("/mi/abc2345/stop", "abc2345"))                                # GET: il pulsante dell'email
        corpo = r.body.decode()
        assert r.status_code == 200 and "Non ti interessa?" in corpo and "Non voglio più ricevere email" in corpo
        assert 'method="post" action="/mi/abc2345/stop"' in corpo and "Grazie!" not in corpo
        assert not s.brevo.messi and impronta("titolare@salone.it") not in s.db.disiscritti     # un'anteprima non disiscrive
        r = A.sms_mi_stop(_req("/mi/abc2345/stop", "abc2345", metodo="POST"))
        assert "Non riceverai più email" in r.body.decode() and "<form" not in r.body.decode()
        assert s.brevo.messi == [(f"{B}/contacts/titolare%40salone.it", {"emailBlacklisted": True})]
        assert impronta("titolare@salone.it") in s.db.disiscritti and s.log.righe[-1].startswith("EMAIL_STOP token=abc2345")
    finally:
        s.fine()


def test_elenco_interessati_con_le_email():
    s = _Scena(Brevo(inviati={"titolare@salone.it": [_iso(4)]}))
    try:
        EE.registra_clic(s.db, "abc2345", "Mozilla/5.0", adesso=ADESSO)                       # anteprima
        EE.registra_clic(s.db, "abc2345", "Mozilla/5.0", adesso=ADESSO + dt.timedelta(hours=2))
        vecchio = A.current_user
        A.current_user = lambda request, admin=False: {"id": 1, "role": "admin"} if admin else A.err(500, "serve admin=True")
        try:
            d = json.loads(A.admin_sms_interessati(_req("/api/admin/sms_interessati", "")).body)
        finally:
            A.current_user = vecchio
        assert d["totale"] == 1
        x = d["interessati"][0]
        assert x["canale"] == "email" and x["email"] == "titolare@salone.it" and x["clic"] == 1 and x["forse_anteprima"] == 1
    finally:
        s.fine()


def test_rotta_stop_accetta_get_e_post():
    r = next(r for r in A.routes if getattr(r, "path", "") == "/mi/{token}/stop")
    assert {"GET", "POST"} <= set(r.methods)


# ---------- modelli ----------
def test_modelli_brevo():
    con, senza = ET.html("con"), ET.html("senza")
    for h in (con, senza):
        assert 'href="{{ contact.MI_LINK }}"' in h and "Mi interessa" in h
        assert 'href="tel:+393482425633"' in h and "Chiamami" in h
        assert 'href="{{ contact.STOP_LINK }}"' in h and "Non mi interessa" in h
        assert 'href="{{ unsubscribe }}"' in h and "Il team di Skovo" in h and 'lang="it"' in h
        assert "Garanzia: se in un mese non trovate almeno 2 auto che vi interessano, vi restituiamo i soldi." in h
        assert "Chi siamo" not in h and "<script" not in h and "Marco" not in h and len(h.encode()) < 10000
        assert h.index("Mi interessa") < h.index("Chiamami") < h.index("Non mi interessa") < h.index("{{ unsubscribe }}")
        assert "scovo.onrender.com/mi/" not in h                                              # nessun indirizzo in vista
    assert con.count("<img") == 1 and 'src="{{ contact.FOTO1 }}"' in con and 'alt="{{ contact.AUTO1 }}"' in con
    for a in ("AUTO1", "DATI1", "AUTO2", "DATI2"):
        assert "{{ contact.%s }}" % a in con
    assert "FOTO2" not in con and "oggi ne abbiamo due in linea con voi" in con
    assert "<img" not in senza and "contact.AUTO" not in senza and "Vi interessa provarlo?" in senza
    usati = set(re.findall(r"contact\.([A-Z0-9_]+)", con + senza))
    assert usati <= set(EE.ATTRIBUTI)
    assert ET.MITTENTE == EE.MITTENTE == {"name": "Skovo", "email": "info@skovo.net"}
    assert ET.MODELLI["con"]["lista"] == EE.LISTA_CON and ET.MODELLI["senza"]["lista"] == EE.LISTA_SENZA
