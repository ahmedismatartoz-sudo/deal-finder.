"""24/11 (cliente): lo stesso messaggio dei WhatsApp, per email, ai commercianti della lista Brevo "Skovo tutti 24-11".

Due versioni: CON ESEMPI (due auto vicine al salone, con foto, prezzo, mercato e margine, come il template WhatsApp
skovo_due_auto_foto) e SENZA ESEMPI (solo testo). In tutte e due il pulsante "Mi interessa" personale, come negli SMS:
<PUBLIC_URL>/mi/<token> -> pagina "Grazie!", clic registrato, elenco in /api/admin/sms_interessati.

Lavoro una tantum da BREVO_IMPORT_LISTE:  email_esempi:<giro>:<quanti per gruppo>   (es. email_esempi:2411:150)
Con il numero (decisione del cliente per il primo giro: 150 + 150) nelle due liste entrano solo i saloni PIÙ VICINI A
MILANO: <quanti> con esempi e altri <quanti> senza esempi, presi a turno andando da Milano verso fuori, così i due
gruppi sono simili per zona. Senza numero (o 0): tutti i contatti validi, con esempi chi ha 2 auto adatte.
Chi ha già ricevuto una email nelle ultime 72 ore (Brevo, campaignStats del contatto) si salta.
Il mittente "Skovo" <info@skovo.net> viene creato in Brevo se manca (POST /v3/senders; non parte nessuna email).
Gira NEL SITO (gli servono le auto pubblicate in questo momento: le foto /email-foto/<id>/0.jpg esistono solo per quelle).
NON crea campagne e NON invia niente. Per ogni contatto della lista che non è bloccato, disiscritto o escluso (le stesse
regole di carica_tutti):
  - un token personale di 8 caratteri in email_link (canale "email"; quelli degli SMS ne hanno 7);
  - le 2 auto d'esempio con le regole del giro WhatsApp del 24/11 (vedi scegli_due). Senza posizione del salone o senza
    2 auto adatte: versione senza esempi;
  - attributi del contatto in Brevo: MI_LINK, STOP_LINK (MI_LINK + "/stop": il pulsante "Non mi interessa"), AUTO1,
    DATI1, FOTO1, AUTO2, DATI2, FOTO2 (nell'email, come nel WhatsApp, si vede solo la foto della prima auto);
  - due liste nuove: "Skovo email con esempi 24-11" e "Skovo email senza esempi 24-11".
Nei log solo conteggi (EMAIL_ESEMPI): mai email né telefoni.

Un giro (la parte <giro> dell'etichetta) sceglie i suoi contatti UNA volta sola: rifare il lavoro con lo stesso giro
(un tentativo dopo un errore, o la stessa voce con un altro numero) non ne aggiunge e non ne cambia; rinfresca solo le
auto d'esempio di chi è già nel gruppo con esempi e toglie chi nel frattempo si è disiscritto. Per altri contatti
serve un giro nuovo (es. email_esempi:2511:150): chi ha già un token di un giro precedente non viene ripreso.
email_pulisci:<etichetta> (da lanciare prima di ogni invio): toglie dalle due liste chi è nei disiscritti o è
bloccato in Brevo e riprova le disiscrizioni che Brevo non aveva preso.

Clic: le anteprime automatiche dei programmi di posta e degli antivirus aprono il link pochi secondi dopo l'invio. Un
clic entro EMAIL_ANTEPRIMA_S (120) secondi dall'invio a quel contatto (Brevo: campaignStats), o entro
EMAIL_ANTEPRIMA_GIRO_S (600) secondi dalla partenza della campagna se Brevo non ha ancora scritto l'invio del contatto,
è "forse anteprima": si salva, ma non conta come interesse. Un clic successivo conta.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

lg = logging.getLogger("scovo")

API = "https://api.brevo.com/v3"
LISTA_CON = "Skovo email con esempi 24-11"
LISTA_SENZA = "Skovo email senza esempi 24-11"
ATTRIBUTI_AUTO = ("AUTO1", "DATI1", "FOTO1", "AUTO2", "DATI2", "FOTO2")
ATTRIBUTI = ("MI_LINK", "STOP_LINK") + ATTRIBUTI_AUTO
BLOCCO = 500                              # contatti per chiamata a /contacts/import
BLOCCO_TOGLI = 150                        # limite di Brevo per /contacts/lists/{id}/contacts/remove
MITTENTE = {"name": "Skovo", "email": "info@skovo.net"}      # 24/11: dominio nuovo, già autenticato in Brevo
ORE_RECENTI = 72                          # chi ha ricevuto una email da meno di tante ore non entra nel giro

# ---- regole delle auto d'esempio: le stesse del giro WhatsApp del 24/11 ----
MARGINE_MIN = 1200
NETTO_MIN = 300
CONFRONTO_MIN = 5                         # almeno 5 auto uguali nel confronto
PREZZO_MAX = 60000
RAGGIO_KM = 60                            # km del salone da Milano + km dell'auto da Milano: di sicuro entro 60 km
USO_MAX = 5                               # la stessa auto al massimo a 5 saloni (EMAIL_ESEMPI_USO_MAX)
CATEGORIE = ("opportunita",)               # solo opportunità pulite del sito (mai candidate)
NON_PULITE = ("incidentata", "economica", "verificare", "supercar", "stima_ripiego")     # segni dell'auto sul sito
TOKEN_LEN = 8                             # i token delle email (quelli degli SMS ne hanno 7)
FASCE = ((8000, (0, 8000)), (15000, (8000, 15000)), (25000, (15000, 25000)), (10 ** 9, (25000, 10 ** 7)))
FASCIA_PREMIUM = (12000, 60000)
FASCIA_IGNOTA = (2500, 13000)
PREMIUM = {"mercedes-benz", "mercedes", "bmw", "audi", "land rover", "land-rover", "porsche", "maserati", "jaguar",
           "lexus", "volvo", "tesla", "ferrari", "alfa romeo", "alfa-romeo", "jeep", "ds automobiles", "mini", "cupra"}
# indizi di gasolio nel nome: sigle dei motori (TDI, dCi, CDTI, JTD, MJet, HDi, CRDi...), "d" e "de" da sole
# ("2.0 d", "300 de"), 116d / 220 d, D250 / D4, la parola gasolio
DIESEL = re.compile(r"(?i)\b(tdi|cdi|cdti|dci|mjt|mjet|m-jet|multijet|hdi|bluehdi|e-hdi|crdi|tdci|ecoblue|jtd\w*|d4d|d-4d|"
                    r"sd|td\w*|dtec|skyactiv-d|d|de)\b|\b\d{2,3}\s?d\b|\bd\s?\d{1,3}\b|turbodiesel|diesel|bluetec|ddis|"
                    r"gasolio")
NON_DIESEL = re.compile(r"(?i)^(benzina|gpl|metano|elettric[ao]|ibrid[ao]|hybrid|benzina\s*/\s*(gpl|metano)|"
                        r"(gpl|metano)\s*/\s*benzina|ibrida\s*(benzina|plug-?in)?|plug-?in)$")

# data/comuni_60km.json ha i comuni entro 60 km da Milano FUORI dalla provincia di Milano. Per quelli della provincia
# di Milano: chilometri in linea d'aria dal Duomo, STIMATI (bastano per dire "vicino": errore di 2-3 km). Un comune
# che non è né qui né nel file non è riconosciuto: nessuna posizione, quindi niente esempi.
KM_MI = {k: float(v) for v, nomi in {
    8: "bresso cormano novatemilanese corsico buccinasco cesanoboscone segrate",
    9: "sestosangiovanni colognomonzese baranzate pero assago rozzano opera sandonatomilanese vimodrone",
    10: "cinisellobalsamo cusanomilanino settimomilanese trezzanosulnaviglio",
    11: "bollate cusago peschieraborromeo basiglio",
    12: "padernodugnano pieveemanuele sangiulianomilanese pioltello rodano zibidosangiacomo",
    13: "arese cornaredo locateditriulzi cernuscosulnaviglio gaggiano noviglio pantigliate",
    14: "rho senago bareggio carugate mediglia",
    15: "garbagnatemilanese cassinadepecchi pregnanamilanese vignate settala carpiano colturano",
    16: "melegnano bussero liscate paullo binasco lacchiarella cisliano tribiano dresano",
    17: "lainate cesate sedriano pessanoconbornago vanzago vizzolopredabissi",
    18: "vittuone gorgonzola vermezzo poglianomilanese casarile vernate vermezzoconzelo",
    19: "melzo rosate cerroallambro solaro sanzenoneallambro zelosurrigone",
    20: "nerviano gessate arluno albairate gudovisconti",
    21: "corbetta cambiago bellinzagolombardo truccazzano pozzuolomartesana calvignasco",
    22: "parabiago abbiategrasso casorezzo santostefanoticino bubbiano",
    23: "ossona cassinettadilugagnano",
    24: "cerromaggiore rescaldina canegrate inzago basiano masate",
    25: "sanvittoreolona magenta morimondo ozzero",
    26: "bustogarolfo mottavisconti trezzanorosa pozzodadda marcalloconcasone mesero villacortese sangiorgiosulegnano",
    27: "legnano cassanodadda grezzago inveruno robeccosulnaviglio besate",
    28: "vapriodadda arconate boffalorasopraticino dairago",
    29: "trezzosulladda cuggiono bernateticino",
    30: "buscate", 31: "robecchettoconinduno", 32: "magnago", 33: "castanoprimo turbigo vanzaghello", 34: "nosate",
    40: "sancolombanoallambro",
}.items() for k in nomi.split()}
_DATA = Path(__file__).resolve().parents[2] / "data"
_km60: dict = {}

SQL_TABELLE = (
    """CREATE TABLE IF NOT EXISTS email_link (
        token      TEXT PRIMARY KEY,
        canale     TEXT NOT NULL DEFAULT 'email',
        email      TEXT NOT NULL,
        nome       TEXT,
        telefono   TEXT NOT NULL DEFAULT '',
        giro       TEXT NOT NULL,
        versione   TEXT,
        creato_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        stop_at    TIMESTAMPTZ,
        stop_brevo TEXT
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS email_link_giro_email ON email_link (giro, email)",
    """CREATE TABLE IF NOT EXISTS email_clic (
        id         BIGSERIAL PRIMARY KEY,
        token      TEXT NOT NULL,
        at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        user_agent TEXT,
        anteprima  TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS email_clic_token ON email_clic (token)",
    """CREATE TABLE IF NOT EXISTS email_foto (
        id         BIGINT PRIMARY KEY,
        foto       BYTEA NOT NULL,
        giro       TEXT,
        quando     TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
)


_tabelle_fatte = False


def assicura(conn) -> None:
    """Crea le tabelle se mancano (una volta per processo: /mi/<token> passa di qui a ogni clic)."""
    global _tabelle_fatte
    if _tabelle_fatte:
        return
    for q in SQL_TABELLE:
        conn.execute(q)
    conn.commit()
    _tabelle_fatte = True


# ---------- testi ----------
def _eur(n) -> str:
    return f"{int(round(n)):,}".replace(",", ".")


def _chiave_comune(c) -> str:
    k = str(c or "").split("(")[0].split(" - ")[0].strip().lower()
    for a, b in (("à", "a"), ("è", "e"), ("é", "e"), ("ù", "u"), ("ò", "o"), ("ì", "i")):
        k = k.replace(a, b)
    return re.sub(r"[^a-z]", "", k)


def km_da_milano(comune, prov=None) -> float | None:
    """Chilometri del comune dal centro di Milano: data/comuni_60km.json (849 comuni entro 60 km, fuori provincia) e
    KM_MI per la provincia di Milano. None se il comune non si trova (fuori zona o nome ignoto) o se la provincia nota
    non è quella dell'elenco."""
    if not _km60:
        try:
            _km60.update(json.loads((_DATA / "comuni_60km.json").read_text(encoding="utf-8")))
        except Exception:
            _km60["\x00"] = {}
    k = _chiave_comune(comune)
    pv = str(prov or "").strip().upper()[:2]
    if not k:
        return None
    if k == "milano":
        return 0.0 if pv in ("", "MI") else None
    if k in KM_MI and pv in ("", "MI"):
        return KM_MI[k]
    v = _km60.get(k)
    if v and not (pv and v.get("pv") and pv != v["pv"]):
        return float(v["km"])
    return None


def _pulito(t) -> str:
    """Testo che finisce in un attributo Brevo e quindi nell'HTML dell'email (Brevo non fa l'escape): via i caratteri
    di controllo e " < > &."""
    return " ".join(re.sub(r'[\x00-\x1f\x7f"<>&]', " ", str(t or "")).split())


def _citta(zona) -> str:
    c = _pulito(str(zona or "").split("(")[0].split(" - ")[0])
    return c.title() if c.islower() or c.isupper() else c


def e_diesel(a: dict) -> bool:
    nome, carb = str(a.get("nome") or ""), str(a.get("carb") or "").lower()
    return "diesel" in carb or "gasolio" in carb or bool(DIESEL.search(nome)) or ("evoque" in nome.lower() and carb == "ibrida")


def non_diesel_sicuro(a: dict) -> bool:
    """L'auto è CHIARAMENTE non diesel: alimentazione benzina, GPL, metano, elettrica o ibrida, e nessun indizio di
    gasolio nel nome. Alimentazione vuota o dubbia, o un indizio nel nome: non è sicuro (ai saloni di Milano non va)."""
    carb = " ".join(str(a.get("carb") or "").lower().split())
    return bool(NON_DIESEL.match(carb)) and not e_diesel(a)


def auto_pulita(it: dict) -> bool:
    """Opportunità pulita del sito: categoria "opportunita", non incidentata, economica, da verificare, supercar o
    con stima di ripiego, senza danno dichiarato, di un privato."""
    pv = it.get("pv") or {}
    r = it.get("_r") or {}
    if pv.get("categoria") not in CATEGORIE or any(it.get(k) for k in NON_PULITE) or r.get("danno_dichiarato"):
        return False
    return not (str(it.get("_tipo") or "").startswith("comm_") or r.get("seller_type") == "commerciante")


def auto_usabili(items: list[dict]) -> list[dict]:
    """Auto del sito buone come esempio (regole del giro WhatsApp): opportunità pulita (auto_pulita), margine >=
    1.200 €, netto >= 300 €, almeno 5 auto uguali nel confronto, fino a 60.000 €, in un comune entro 60 km da Milano."""
    out = []
    for it in items or []:
        pv = it.get("pv") or {}
        prezzo, margine = it.get("prezzo"), pv.get("margine")
        zona = str(it.get("zona") or "")
        m = re.search(r"\(([A-Za-z]{2})\)", zona)
        if not it.get("id") or not prezzo or not margine or not it.get("anno") or not str(it.get("nome") or "").strip():
            continue
        if not isinstance(it.get("km"), (int, float)) or isinstance(it.get("km"), bool) or it["km"] < 0:
            continue                                          # chilometri mancanti: l'auto non si usa
        if not auto_pulita(it) or margine < MARGINE_MIN or (pv.get("netto") or 0) < NETTO_MIN \
                or (pv.get("n") or 0) < CONFRONTO_MIN or prezzo > PREZZO_MAX:
            continue
        kmz = km_da_milano(zona, m.group(1) if m else None)
        if kmz is None or kmz > RAGGIO_KM:
            continue
        a = {"id": int(it["id"]), "nome": " ".join(str(it["nome"]).split()), "anno": it["anno"], "km": int(it["km"]),
             "carb": str(it.get("carb") or "").strip(), "prezzo": int(prezzo), "margine": int(margine),
             "netto": int(pv.get("netto") or 0), "zona": zona, "kmz": kmz, "comune": _chiave_comune(zona)}
        a["diesel"] = e_diesel(a)
        a["non_diesel"] = non_diesel_sicuro(a)
        out.append(a)
    ordine = sorted(a["id"] for a in out)
    pos = {i: k / len(ordine) for k, i in enumerate(ordine)}
    for a in out:
        a["rk"] = pos[a["id"]]             # gli annunci più nuovi (id più alto) un po' avanti
    return out


def testi(a: dict, base: str) -> dict:
    """Gli attributi di un'auto: AUTO e DATI scritti come nei WhatsApp (skovo_due_auto_foto), più la foto."""
    carb = _pulito(a["carb"])
    pezzi = [f"{_pulito(a['nome'])} {int(a['anno'])}", f"{_eur(a['km'])} km"] + ([carb] if carb else []) + [_citta(a["zona"])]
    return {"AUTO": " · ".join(pezzi),
            "DATI": f"{_eur(a['prezzo'])} € · mercato {_eur(a['prezzo'] + a['margine'])} € · margine +{_eur(a['margine'])} €",
            "FOTO": f"{base}/email-foto/{a['id']}/0.jpg"}


def fascia(d: dict) -> tuple[int, int]:
    """Fascia di prezzo del salone: dalla mediana dei suoi annunci se nota, altrimenti dalla marca che tratta di più."""
    med = d.get("pmed")
    if med:
        return next(f for tetto, f in FASCE if med < tetto)
    marche = [str(m).lower() for m in d.get("marche") or []]
    return FASCIA_PREMIUM if marche and marche[0] in PREMIUM else FASCIA_IGNOTA


def comune_milano(d: dict) -> bool:
    """Salone del comune di Milano: il comune contiene "milano" (Milano, Milano - Lambrate, Milano (MI)...) e la
    provincia è MI o non è scritta. "Novate Milanese" e "Cusano Milanino" sono altri comuni."""
    return bool(re.search(r"(^|[^a-z])milano([^a-z]|$)", str(d.get("comune") or "").lower())) \
        and str(d.get("prov") or "").strip().upper()[:2] in ("", "MI")


def scegli_due(d: dict, auto: list[dict], uso: dict, uso_max: int = USO_MAX, foto_ok=None) -> list[dict]:
    """Le 2 auto d'esempio per il salone `d` (km = chilometri da Milano), con le regole del giro WhatsApp:
    a un salone del comune di Milano solo auto chiaramente non diesel (non_diesel_sicuro); km del salone + km dell'auto da Milano <= 60; prezzo nella fascia
    del salone (-20% / +15%); la stessa auto al massimo a `uso_max` saloni; due modelli diversi. Punti: marca che il
    salone tratta, fascia piena, vicinanza, margine netto, annuncio recente. La prima deve avere la foto (è quella in
    cima all'email). [] se non ce ne sono due."""
    if d.get("km") is None:
        return []
    milano = comune_milano(d)
    lo, hi = fascia(d)
    marche = {str(m).lower().replace("-", " ") for m in d.get("marche") or []}
    com = _chiave_comune(d.get("comune"))
    cand = []
    for a in auto:
        if (milano and not a.get("non_diesel")) or uso.get(a["id"], 0) >= uso_max:
            continue
        if d["km"] + a["kmz"] > RAGGIO_KM or not lo * 0.8 <= a["prezzo"] <= hi * 1.15:
            continue
        nome = a["nome"].lower().replace("-", " ")
        s = 0.0
        if any(nome.startswith(m) or (m.startswith("mercedes") and nome.startswith("mercedes")) for m in marche):
            s += 4
        if lo <= a["prezzo"] <= hi:
            s += 2
        if com and com == a["comune"]:
            s += 3                                            # stesso comune del salone
        s += max(0.0, (40 - abs(d["km"] - a["kmz"])) / 20)
        s += min(a["netto"], 3000) / 1500
        s += 1.5 * a.get("rk", 0)
        s -= uso.get(a["id"], 0) * 1.2
        cand.append((-s, a["id"], a))
    cand.sort(key=lambda x: x[:2])
    scelte, modelli = [], set()
    for _, _, a in cand:
        mod = " ".join(a["nome"].split()[:2]).lower()
        if mod in modelli or (not scelte and a.get("senza_foto")):
            continue
        if not scelte and foto_ok is not None and not a.get("con_foto"):
            f = foto_ok(a["id"])
            if not f:
                a["senza_foto"] = True
                continue
            a["con_foto"] = True
            if isinstance(f, (bytes, bytearray)):
                a["foto"] = bytes(f)                          # la copia da salvare (salva_foto)
        modelli.add(mod)
        scelte.append(a)
        if len(scelte) == 2:
            return scelte
    return []


def dividi(saloni: list[dict], auto: list[dict], per_gruppo: int = 0, uso_max: int = USO_MAX, foto_ok=None,
           recente=None) -> tuple[list[dict], list[dict], dict]:
    """I due gruppi: (con esempi, senza esempi, conteggi). Si va dal salone più vicino a Milano verso fuori.
    per_gruppo > 0: solo saloni con la posizione nota, al massimo `per_gruppo` per gruppo, a turno (prima si prova a
    dare le 2 auto al gruppo che è indietro: chi non le ha va nel gruppo senza esempi), così i due gruppi sono simili
    per zona. per_gruppo = 0: tutti; con esempi chi ha 2 auto adatte, gli altri (anche senza posizione) senza.
    `recente(email)`: True se ha ricevuto una email da poco (si salta), None se non si è riusciti a saperlo (si salta
    lo stesso: nel dubbio non si scrive). Scrive in ogni salone scelto "auto" (2 o nessuna) e "perche"."""
    uso: dict = {}
    n = Counter()
    con, senza = [], []
    for d in sorted(saloni, key=lambda d: (d.get("km") is None, d.get("km") or 0, d["email"])):
        pieno_con = bool(per_gruppo) and len(con) >= per_gruppo
        pieno_senza = bool(per_gruppo) and len(senza) >= per_gruppo
        if per_gruppo and ((pieno_con and pieno_senza) or d.get("km") is None):
            break
        if recente is not None:
            r = recente(d["email"])
            if r or r is None:
                n["saltati_email_recente" if r else "saltati_non_verificati"] += 1
                continue
        due = []
        if not pieno_con and (not per_gruppo or pieno_senza or len(con) <= len(senza)):
            due = scegli_due(d, auto, uso, uso_max, foto_ok)
        if due:
            for a in due:
                uso[a["id"]] = uso.get(a["id"], 0) + 1
            con.append(d)
            n["milano_senza_diesel"] += comune_milano(d)
        elif not pieno_senza:
            senza.append(d)
            n["senza_posizione" if d.get("km") is None else "senza_esempi_con_posizione"] += 1
        else:
            n["non_usati"] += 1
            continue
        d["auto"] = due
        d["perche"] = None if due else ("senza_posizione" if d.get("km") is None else "senza_auto")
    n["auto_usate"] = len(uso)
    n["auto_senza_foto"] = sum(1 for a in auto if a.get("senza_foto"))
    for nome, gruppo in (("con", con), ("senza", senza)):
        kms = sorted(d["km"] for d in gruppo if d.get("km") is not None)
        if kms:
            n[f"km_mediana_{nome}"] = round(kms[len(kms) // 2], 1)
            n[f"km_max_{nome}"] = round(kms[-1], 1)
    return con, senza, dict(n)


# ---------- i saloni: posizione, fascia e telefono dall'archivio ----------
def profili_db(conn) -> dict:
    """email -> {nome, comune, prov, tel, pmed, marche} dall'archivio dei commercianti (lead_email con lead_tutti_dealer
    o lead_maps, prezzi e marche da lead_tutti_ann) e, per chi lì non c'è, dalle righe di lead_consegne che dicono il
    comune. Una tabella che manca non ferma le altre."""
    import statistics
    from .invii import norm_email
    from .sms_saloni import norm_tel
    out: dict = {}
    try:
        prezzi: dict = {}
        for a in conn.execute("SELECT chiave, prezzo, marca FROM lead_tutti_ann WHERE prezzo > 500").fetchall():
            p = prezzi.setdefault(a["chiave"], ([], Counter()))
            p[0].append(a["prezzo"])
            if a["marca"]:
                p[1][a["marca"]] += 1
        righe = conn.execute(
            "SELECT e.chiave, e.email, COALESCE(d.nome, m.nome) AS nome, COALESCE(d.comune, m.comune) AS comune, "
            "upper(left(COALESCE(d.prov, m.prov), 2)) AS prov, d.cell AS cell, m.tel AS tel FROM lead_email e "
            "LEFT JOIN lead_tutti_dealer d ON d.chiave = e.chiave "
            "LEFT JOIN lead_maps m ON e.chiave = 'maps:' || m.place_id WHERE cardinality(e.email) > 0").fetchall()
        for r in righe:
            pz, mc = prezzi.get(r["chiave"], ([], Counter()))
            tel = next((t for t in (norm_tel(x) for x in list(r.get("cell") or []) + [r.get("tel")]) if t), "")
            p = {"nome": r.get("nome") or "", "comune": r.get("comune") or "", "prov": r.get("prov") or "", "tel": tel,
                 "pmed": statistics.median(pz) if len(pz) >= 3 else None, "marche": [m for m, _ in mc.most_common(6)]}
            for e in r.get("email") or []:
                e = norm_email(e)
                if e and (e not in out or (p["comune"] and not out[e]["comune"])):
                    out[e] = p
        conn.commit()
    except Exception as e:
        conn.rollback()
        lg.warning("EMAIL_ESEMPI archivio dei commercianti non letto: %s", type(e).__name__)
    try:
        from .brevo_liste import _scorri, email_nel_testo
        for r in _scorri(conn, "SELECT dati::text AS d FROM lead_consegne WHERE strpos(dati::text, '@') > 0 "
                               "AND strpos(dati::text, 'comune') > 0"):
            try:
                dati = json.loads(r["d"])
            except ValueError:
                continue
            if not isinstance(dati, dict) or not isinstance(dati.get("comune"), str) or not dati["comune"].strip():
                continue
            tel = next((t for t in (norm_tel(dati.get(k)) for k in ("cell", "tel", "telefono")
                                    if isinstance(dati.get(k), (str, int))) if t), "")
            p = {"nome": str(dati.get("nome") or "")[:120], "comune": dati["comune"],
                 "prov": str(dati.get("prov") or dati.get("provincia") or "")[:2].upper(), "tel": tel, "pmed": None,
                 "marche": []}
            for e in email_nel_testo(dati):
                e = norm_email(e)
                if e and (e not in out or not out[e]["comune"]):
                    out[e] = p
        conn.commit()
    except Exception as e:
        conn.rollback()
        lg.warning("EMAIL_ESEMPI lead_consegne non letta: %s", type(e).__name__)
    return out


# ---------- token ----------
def token_per(conn, giro: str, saloni: list[dict]) -> int:
    """Un token per salone in email_link: quello che il salone ha già in questo giro, altrimenti uno nuovo di 8
    caratteri (mai uguale a un token degli SMS). Scrive d["token"]; restituisce quanti ne ha creati."""
    from . import sms_saloni as SS
    assicura(conn)
    SS.assicura(conn)
    gia = {r["email"]: r["token"] for r in
           conn.execute("SELECT email, token, versione FROM email_link WHERE giro = %s", (giro,)).fetchall()}
    nuovi = 0
    for k, d in enumerate(saloni, 1):
        versione = "con" if d.get("auto") else "senza"
        if d["email"] in gia:
            d["token"] = gia[d["email"]]
            conn.execute("UPDATE email_link SET versione = %s WHERE token = %s", (versione, d["token"]))
        else:
            d["token"] = None
            for _ in range(6):
                t = SS.nuovo_token(lunghezza=TOKEN_LEN)
                if conn.execute("SELECT 1 FROM sms_link WHERE token = %s", (t,)).fetchone():
                    continue
                r = conn.execute(
                    "INSERT INTO email_link (token, email, nome, telefono, giro, versione) VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT DO NOTHING RETURNING token",
                    (t, d["email"], (d.get("nome") or "")[:120], d.get("tel") or "", giro, versione)).fetchone()
                if r:
                    d["token"] = t
                    nuovi += 1
                    break
            if not d["token"]:
                raise RuntimeError("token non creato")
        if k % 200 == 0:
            conn.commit()
    conn.commit()
    return nuovi


def gia_scelti(conn, giro: str) -> tuple[dict, set]:
    """({email: versione} dei contatti che questo giro ha già scelto, {email} di chi ha un token di un ALTRO giro)."""
    assicura(conn)
    miei, altri = {}, set()
    for r in conn.execute("SELECT email, giro, versione FROM email_link").fetchall():
        if r["giro"] == giro:
            miei[r["email"]] = r["versione"] or "senza"
        else:
            altri.add(r["email"])
    return miei, altri


# ---------- foto ----------
def salva_foto(conn, giro: str, foto: dict) -> int:
    """Copia della foto della prima auto di ogni email ({id: bytes}): /email-foto/<id>/0.jpg la serve anche quando
    l'auto non è più sul sito (venduta, tolta), così l'email aperta giorni dopo ha ancora la sua foto."""
    assicura(conn)
    n = 0
    for lid, dati in foto.items():
        if isinstance(dati, (bytes, bytearray)) and dati:
            conn.execute("INSERT INTO email_foto (id, foto, giro) VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE "
                         "SET foto = EXCLUDED.foto, giro = EXCLUDED.giro, quando = now()", (int(lid), bytes(dati), giro))
            n += 1
    conn.commit()
    return n


_foto_ids: dict = {"t": 0.0, "ids": frozenset()}


def foto_salvata(conn, lid: int, pos: int = 0) -> bytes | None:
    """La copia della foto di un'auto usata in un giro email (solo la prima foto). L'elenco degli id si rilegge al
    massimo ogni 5 minuti: un id qualsiasi chiesto da fuori non costa una lettura della foto."""
    if pos != 0:
        return None
    if not _foto_ids["t"] or time.monotonic() - _foto_ids["t"] > 300:
        assicura(conn)
        _foto_ids.update(t=time.monotonic(),
                         ids=frozenset(r["id"] for r in conn.execute("SELECT id FROM email_foto").fetchall()))
    if int(lid) not in _foto_ids["ids"]:
        return None
    r = conn.execute("SELECT foto FROM email_foto WHERE id = %s", (int(lid),)).fetchone()
    return bytes(r["foto"]) if r and r["foto"] else None


def _foto_dal_sito(lid: int):
    """I byte della foto /email-foto/<id>/0.jpg (chiesta al sito stesso, come brevo_liste._foto_ok); None se manca."""
    import httpx
    try:
        r = httpx.get(f"http://127.0.0.1:{os.environ.get('PORT', '10000')}/email-foto/{lid}/0.jpg", timeout=30)
        return r.content if r.status_code == 200 and r.content else None
    except Exception:
        return None


# ---------- Brevo ----------
def _client():
    import httpx
    from .brevo_liste import _chiave
    key = _chiave()
    if not key:
        raise RuntimeError("manca BREVO_API_KEY")
    return httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=60)


def _attesa(r, k: int, massimo: float) -> float:
    try:
        v = float((getattr(r, "headers", None) or {}).get("Retry-After") or 0)
    except (TypeError, ValueError):
        v = 0.0
    return min(massimo, max(v, 3.0 * (k + 1)))


def chiedi(c, metodo: str, url: str, dormi=time.sleep, tentativi: int = 4, massimo: float = 60.0, **k):
    """Una chiamata a Brevo che riprova su errore di rete, 429 e 5xx (attesa da Retry-After, al massimo `massimo`
    secondi). Restituisce l'ultima risposta; se la rete non risponde mai solleva l'ultimo errore."""
    ultimo = None
    for n in range(tentativi):
        try:
            r = getattr(c, metodo)(url, **k)
        except AssertionError:
            raise
        except Exception as e:
            ultimo, r = e, None
        else:
            if r.status_code != 429 and r.status_code < 500:
                return r
            ultimo = None
        if n + 1 < tentativi:
            dormi(_attesa(r, n, massimo) if r is not None else min(massimo, 3.0 * (n + 1)))
    if ultimo is not None:
        raise ultimo
    return r


def ritenta(fn, dormi=time.sleep, tentativi: int = 3):
    """Per le letture preliminari fatte da altre funzioni (tutti i contatti, la cartella): riprova se sollevano."""
    for n in range(tentativi):
        try:
            return fn()
        except AssertionError:
            raise
        except Exception:
            if n + 1 == tentativi:
                raise
            dormi(5.0 * (n + 1))


def _tutte_le_liste(c, dormi=time.sleep) -> dict:
    out, off = {}, 0
    while True:
        r = chiedi(c, "get", f"{API}/contacts/lists", dormi, params={"limit": 50, "offset": off})
        r.raise_for_status()
        ls = r.json().get("lists") or []
        out.update({x.get("name"): x["id"] for x in ls})
        if len(ls) < 50:
            return out
        off += 50


def _attributi(c, dormi=time.sleep) -> list[str]:
    """Crea gli attributi di testo che mancano. Restituisce quelli che non ci sono nemmeno dopo."""
    def esistenti():
        r = chiedi(c, "get", f"{API}/contacts/attributes", dormi)
        r.raise_for_status()
        return {a.get("name") for a in (r.json() or {}).get("attributes") or []}
    for a in [a for a in ATTRIBUTI if a not in esistenti()]:
        chiedi(c, "post", f"{API}/contacts/attributes/normal/{a}", dormi, json={"type": "text"})
    ci_sono = esistenti()
    return [a for a in ATTRIBUTI if a not in ci_sono]


def senza_protocollo(url: str) -> str:
    """Brevo traccia i clic dei link presi da un attributo solo se il protocollo è scritto nel modello
    (href="https://{{ contact.MI_LINK }}"): nell'attributo va l'indirizzo senza https://."""
    return re.sub(r"^https?://", "", url)


def corpo_contatto(d: dict, base: str) -> dict:
    """Il contatto come va a /contacts/import. Senza esempi gli attributi delle auto vanno vuoti (con
    emptyContactsAttributes Brevo cancella quelli di un giro prima)."""
    link = senza_protocollo(f"{base}/mi/{d['token']}")
    attr = {"MI_LINK": link, "STOP_LINK": link + "/stop"}
    attr.update({a: "" for a in ATTRIBUTI_AUTO})
    for n, a in enumerate(d.get("auto") or [], 1):
        attr.update({f"{k}{n}": v for k, v in testi(a, base).items()})
    return {"email": d["email"], "attributes": attr}


def _aspetta(c, risposta, dormi, max_s: int = 300) -> str:
    """Brevo importa in differita e un import alla volta: si aspetta la fine del processo prima del blocco dopo."""
    try:
        pid = (risposta.json() or {}).get("processId")
    except ValueError:
        pid = None
    if not pid:
        return "senza_processo"
    st = "in_attesa"
    for _ in range(max(1, max_s // 5)):
        dormi(5)
        try:
            st = (c.get(f"{API}/processes/{pid}").json() or {}).get("status") or "sconosciuto"
        except Exception:
            st = "non_letto"
        if st in ("completed", "failed", "cancelled", "aborted"):
            break
    return st


def _togli(c, lid: int, emails: list[str], dormi, massimo: float = 60.0) -> int:
    """Toglie i contatti da una lista (non li cancella). Restituisce quanti blocchi non sono riusciti."""
    falliti = 0
    for k in range(0, len(emails), BLOCCO_TOGLI):
        try:
            r = chiedi(c, "post", f"{API}/contacts/lists/{lid}/contacts/remove", dormi, tentativi=3, massimo=massimo,
                       json={"emails": emails[k:k + BLOCCO_TOGLI]})
            ok = r.status_code < 300 or r.status_code in (400, 404)      # 400: già fuori dalla lista
        except Exception:
            ok = False
        falliti += not ok
    return falliti


def mittente(c, dormi=time.sleep) -> dict:
    """Il mittente MITTENTE in Brevo: se manca lo crea (POST /v3/senders: non manda email a nessun contatto).
    Restituisce {"id", "attivo", "creato"} oppure {"errore"}: allora il lavoro risulta incompleto."""
    try:
        r = chiedi(c, "get", f"{API}/senders", dormi)
        if r.status_code >= 300:
            return {"errore": f"lettura HTTP {r.status_code}"}
        for x in (r.json() or {}).get("senders") or []:
            if str(x.get("email") or "").lower() == MITTENTE["email"]:
                return {"id": x.get("id"), "attivo": bool(x.get("active")), "creato": False}
        r = chiedi(c, "post", f"{API}/senders", dormi, json=dict(MITTENTE))
        if r.status_code >= 300:
            return {"errore": f"creazione HTTP {r.status_code}"}
        return {"id": (r.json() or {}).get("id"), "attivo": None, "creato": True}
    except Exception as e:
        return {"errore": type(e).__name__}


def recente_brevo(c, ore: float = ORE_RECENTI, adesso: dt.datetime | None = None, dormi=time.sleep):
    """Funzione email -> True se Brevo ha mandato una campagna a quel contatto nelle ultime `ore` ore, False se no,
    None se non si riesce a leggerlo (nel dubbio il contatto si salta)."""
    adesso = adesso or dt.datetime.now(dt.timezone.utc)
    da = adesso - dt.timedelta(hours=ore)

    def recente(email: str):
        try:
            r = chiedi(c, "get", f"{API}/contacts/{quote(email, safe='')}/campaignStats", dormi, tentativi=3, massimo=20)
        except Exception:
            return None
        if r.status_code >= 300:
            return None
        dormi(0.12)                                            # sotto il limite di 10 chiamate al secondo
        return any(d and d >= da for d in (_data(x.get("eventTime")) for x in (r.json() or {}).get("messagesSent") or []))
    return recente


# ---------- disiscrizioni in Brevo ----------
_ids_liste: dict = {}


def ids_liste_email(c, dormi=time.sleep) -> dict:
    """{nome: id} delle due liste email che esistono in Brevo (ricordate nel processo)."""
    if len(_ids_liste) < 2:
        tutte = _tutte_le_liste(c, dormi)
        _ids_liste.update({n: tutte[n] for n in (LISTA_CON, LISTA_SENZA) if tutte.get(n) is not None})
    return dict(_ids_liste)


def stop_in_brevo(c, email: str, dormi=time.sleep, massimo: float = 10.0) -> str:
    """Disiscrive in Brevo: emailBlacklisted e fuori dalle due liste email. "ok", "contatto_assente" o "errore ..."
    (allora la richiesta resta in coda: riprova_stop). Su 429/5xx aspetta (Retry-After, al massimo `massimo` secondi)."""
    try:
        r = chiedi(c, "put", f"{API}/contacts/{quote(email, safe='')}", dormi, tentativi=3, massimo=massimo,
                   json={"emailBlacklisted": True})
        if r.status_code == 404:
            return "contatto_assente"
        if r.status_code >= 300:
            return f"errore {r.status_code}"
        for lid in ids_liste_email(c, dormi).values():
            if _togli(c, lid, [email], dormi, massimo):
                return "errore liste"
    except Exception as e:
        return "errore " + type(e).__name__
    return "ok"


def riprova_stop(conn, c, dormi=time.sleep, massimo: float = 60.0) -> dict:
    """La coda delle disiscrizioni che Brevo non aveva preso (email_link con stop_at e stop_brevo non riuscito): le
    riprova, con attese più lunghe. Restituisce i conteggi."""
    assicura(conn)
    righe = conn.execute("SELECT token, email FROM email_link WHERE stop_at IS NOT NULL AND "
                         "COALESCE(stop_brevo, '') NOT IN ('ok', 'contatto_assente')").fetchall()
    n = Counter(in_coda=len(righe))
    for r in righe:
        esito = stop_in_brevo(c, r["email"], dormi, massimo)
        conn.execute("UPDATE email_link SET stop_brevo = %s WHERE token = %s", (esito[:60], r["token"]))
        conn.commit()
        n["riusciti" if not esito.startswith("errore") else "ancora_in_coda"] += 1
    return dict(n)


ATTESE_CODA_S = (60, 300, 1800, 7200)


def _riprova_piu_tardi(connetti=None, client=None, dormi=time.sleep, attese=ATTESE_CODA_S) -> None:
    """In un thread a parte dopo una disiscrizione che Brevo non ha preso: riprova dopo 1, 5, 30 minuti e 2 ore.
    Se il processo si riavvia prima, la coda la riprendono la prossima disiscrizione, email_pulisci e email_esempi."""
    if connetti is None:
        from ..db import connect as connetti
    for s in attese:
        dormi(s)
        c = None
        try:
            c = (client or _client)()
            with connetti() as conn:
                if not riprova_stop(conn, c, dormi).get("ancora_in_coda"):
                    return
        except Exception as e:
            lg.warning("EMAIL_STOP coda non riprovata: %s", type(e).__name__)
        finally:
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass


def pulisci_liste(c, connetti=None, dormi=time.sleep, contatti=None) -> dict:
    """Da fare prima di ogni invio: riprova le disiscrizioni in coda e toglie dalle due liste email chi è nei
    disiscritti del database o è bloccato in Brevo (disiscritto, in blacklist, rimbalzato). Solo conteggi."""
    from . import invii
    from .auto_invio import _contatti_brevo
    if connetti is None:
        from ..db import connect as connetti
    esito: dict = {}
    with connetti() as conn:
        esito["coda_stop"] = riprova_stop(conn, c, dormi)
    if contatti is None:
        contatti = ritenta(lambda: _contatti_brevo(c), dormi)
    with connetti() as conn:
        invii.sincronizza_da_brevo(conn, contatti)
        bloccati = invii.disiscritti(conn)
    ids = ids_liste_email(c, dormi)
    tolti, falliti = 0, 0
    for nome, lid in ids.items():
        via = [invii.norm_email(x["email"]) for x in contatti if x.get("email") and lid in (x.get("listIds") or [])
               and (invii.contatto_bloccato(x) or invii.impronta(x["email"]) in bloccati)]
        tolti += len(via)
        falliti += _togli(c, lid, via, dormi)
    esito.update(liste=len(ids), disiscritti_tolti=tolti, tolti_falliti=falliti)
    return esito


def incompleto(esito: dict) -> str | None:
    """Perché il lavoro NON si può segnare fatto (None se è completo). Funzione pura."""
    if esito.get("errore") or esito.get("fermo"):
        return str(esito.get("errore") or esito.get("fermo"))
    if esito.get("blocchi_falliti") or esito.get("tolti_falliti"):
        return "blocchi_falliti"
    if any(k not in ("completed", "senza_processo") for k in (esito.get("processi") or {})):
        return "import_non_finito"
    if (esito.get("coda_stop") or {}).get("ancora_in_coda"):
        return "disiscrizioni_in_coda"
    if "errore" in (esito.get("mittente") or {}):
        return "mittente_non_creato"
    return None


def prepara(items: list[dict], giro: str, per_gruppo: int = 0, *, c=None, connetti=None, foto_ok=_foto_dal_sito,
            dormi=time.sleep, env=None, recente="brevo") -> dict:
    """Il lavoro (vedi il testo in cima). `items`: le auto del sito in questo momento; `per_gruppo`: quanti contatti
    per lista (0 = tutti). Restituisce solo conteggi."""
    from . import brevo_liste as BL
    from . import invii
    from . import sms_saloni as SS
    from .auto_invio import _contatti_brevo
    env = os.environ if env is None else env
    if connetti is None:
        from ..db import connect as connetti
    try:
        uso_max = max(1, int(env.get("EMAIL_ESEMPI_USO_MAX") or USO_MAX))
    except ValueError:
        uso_max = USO_MAX
    base = SS.base_url(env)
    auto = auto_usabili(items)
    esito: dict = {"giro": giro, "per_gruppo": per_gruppo, "auto_sito": len(items or []), "auto_usabili": len(auto),
                   "uso_max": uso_max}
    chiudi = c is None
    c = c or _client()
    try:
        if chiedi(c, "get", f"{API}/account", dormi).status_code != 200:
            return {**esito, "errore": "chiave rifiutata"}
        liste = _tutte_le_liste(c, dormi)
        lid_tutti = liste.get(BL.LISTA_TUTTI)
        if lid_tutti is None:
            return {**esito, "fermo": "lista_tutti_assente"}
        with connetti() as conn:                              # prima di tutto le disiscrizioni rimaste in coda
            esito["coda_stop"] = riprova_stop(conn, c, dormi)
        contatti = ritenta(lambda: _contatti_brevo(c), dormi)
        with connetti() as conn:
            invii.sincronizza_da_brevo(conn, contatti)
            nel_db = invii.disiscritti(conn)
            profili = profili_db(conn)
            miei, altri_giri = gia_scelti(conn, giro)
        bloccati = nel_db | BL._bloccati_0111()
        nella = [x for x in contatti if x.get("email") and lid_tutti in (x.get("listIds") or [])]
        esito.update(nella_lista=len(nella), disiscritti_noti=len(bloccati))
        if len(bloccati) < BL.BLOCCATI_MINIMI or not nel_db:
            return {**esito, "fermo": "disiscritti_incompleti"}
        if not nella:
            return {**esito, "fermo": "lista_tutti_vuota"}
        # chi è bloccato, disiscritto o escluso: le stesse regole di carica_tutti (indirizzo, nome, dominio, PEC...)
        voci, azienda, bloccati_brevo = [], {}, 0
        for x in nella:
            e = invii.norm_email(x["email"])
            if invii.contatto_bloccato(x):
                bloccati_brevo += 1
                continue
            attr = x.get("attributes") or {}
            if isinstance(attr.get(BL.ATTRIBUTO_NOME), str) and attr[BL.ATTRIBUTO_NOME].strip():
                azienda[e] = " ".join(attr[BL.ATTRIBUTO_NOME].split())[:120]
            # gli altri attributi (non quelli di questo lavoro, che cambiano a ogni giro) servono SOLO a riconoscere
            # gli esclusi: il nome del salone è AZIENDA, altrimenti quello dell'archivio
            nomi = [v for k, v in attr.items() if isinstance(v, str) and v.strip() and k not in ATTRIBUTI]
            nomi.append((profili.get(e) or {}).get("nome") or None)
            voci += [(e, v) for v in nomi]
        buoni, conti = BL.scegli_tutti(sorted(voci, key=lambda v: (v[0], v[1] or "")), bloccati)
        esito["bloccati_in_brevo"] = bloccati_brevo
        esito["esclusi"] = {k: v for k, v in conti.items() if k.startswith("escluse_") or k == "non_valide"}
        saloni = []
        for x in buoni:
            p = profili.get(x["email"]) or {}
            saloni.append({"email": x["email"], "nome": azienda.get(x["email"]) or p.get("nome") or "",
                           "comune": p.get("comune") or "", "prov": p.get("prov") or "", "tel": p.get("tel") or "",
                           "pmed": p.get("pmed"), "marche": p.get("marche") or [],
                           "km": km_da_milano(p.get("comune"), p.get("prov")) if p.get("comune") else None})
        esito.update(validi=len(saloni), con_comune=sum(1 for d in saloni if d["comune"]),
                     con_posizione=sum(1 for d in saloni if d["km"] is not None))
        if not saloni:
            return {**esito, "fermo": "nessun_contatto_valido"}
        if miei:
            # il giro ha già scelto i suoi contatti: non se ne aggiungono e non si cambiano. Si rinfrescano le auto di
            # chi era nel gruppo con esempi (chi non ne ha più due passa al gruppo senza) e chi non è più valido esce
            esito.update(giro_gia_scelto=len(miei), usciti_dal_giro=len(miei) - sum(1 for d in saloni if d["email"] in miei))
            con, mai, conti = dividi([d for d in saloni if miei.get(d["email"]) == "con"], auto, 0, uso_max, foto_ok)
            senza = mai + [dict(d, auto=[], perche="senza_esempi") for d in saloni if miei.get(d["email"]) == "senza"]
        else:
            if recente == "brevo":
                recente = recente_brevo(c, dormi=dormi)
            liberi = [d for d in saloni if d["email"] not in altri_giri]
            esito["gia_in_un_altro_giro"] = len(saloni) - len(liberi)
            con, senza, conti = dividi(liberi, auto, per_gruppo, uso_max, foto_ok, recente)
        scelti = con + senza
        esito.update(conti)
        esito.update(con_esempi=len(con), senza_esempi=len(senza),
                     copertura_pct=round(100.0 * len(con) / max(1, len(scelti)), 1))
        if not scelti:
            return {**esito, "fermo": "nessun_contatto_scelto"}
        with connetti() as conn:
            esito["token_nuovi"] = token_per(conn, giro, scelti)
            esito["foto_salvate"] = salva_foto(conn, giro, {d["auto"][0]["id"]: d["auto"][0].get("foto") for d in con})
        esito["mittente"] = mittente(c, dormi)
        if "errore" in esito["mittente"]:
            lg.warning("EMAIL_ESEMPI ATTENZIONE: il mittente %s NON c'è in Brevo e non è stato creato (%s). Le liste si "
                       "preparano lo stesso ma il lavoro resta incompleto: senza mittente la campagna non parte",
                       MITTENTE["email"], esito["mittente"]["errore"])
        mancano = _attributi(c, dormi)
        if mancano:
            return {**esito, "fermo": "attributi_non_creati", "attributi_mancanti": len(mancano)}
        cartella = None
        ids = {}
        for nome in (LISTA_CON, LISTA_SENZA):
            if liste.get(nome) is None:
                cartella = cartella or ritenta(lambda: BL._cartella(c), dormi)
                r = chiedi(c, "post", f"{API}/contacts/lists", dormi, json={"name": nome, "folderId": cartella})
                r.raise_for_status()
                liste[nome] = r.json()["id"]
            ids[nome] = liste[nome]
        _ids_liste.update(ids)
        esito["liste"] = {"con": ids[LISTA_CON], "senza": ids[LISTA_SENZA]}
        codici, processi, falliti, caricati = Counter(), Counter(), 0, 0
        for nome, gruppo in ((LISTA_CON, con), (LISTA_SENZA, senza)):
            for k in range(0, len(gruppo), BLOCCO):
                blocco = [corpo_contatto(d, base) for d in gruppo[k:k + BLOCCO]]
                r = BL._importa_blocco(c, {"jsonBody": blocco, "listIds": [ids[nome]], "updateExistingContacts": True,
                                           "emptyContactsAttributes": True}, codici, dormi)
                if r is None:
                    falliti += 1
                    continue
                caricati += len(blocco)
                processi[_aspetta(c, r, dormi)] += 1
        # nelle due liste resta solo chi è di questo giro e nella lista giusta: escono chi ha cambiato gruppo, chi non
        # è più valido (disiscritto, bloccato, escluso) e i contatti di un giro precedente (restano in "Skovo tutti")
        giusta = {d["email"]: ids[LISTA_CON if d["auto"] else LISTA_SENZA] for d in scelti}
        tolti, tolti_falliti = 0, 0
        for nome in (LISTA_CON, LISTA_SENZA):
            via = [invii.norm_email(x["email"]) for x in contatti if x.get("email") and ids[nome] in (x.get("listIds") or [])
                   and giusta.get(invii.norm_email(x["email"])) != ids[nome]]
            tolti += len(via)
            tolti_falliti += _togli(c, ids[nome], via, dormi)
        esito.update(caricati=caricati, blocchi_falliti=falliti, import_codici=dict(codici), processi=dict(processi),
                     tolti_dalle_liste=tolti, tolti_falliti=tolti_falliti)
        for chiave, nome in (("nella_lista_con", LISTA_CON), ("nella_lista_senza", LISTA_SENZA)):
            try:
                esito[chiave] = int((c.get(f"{API}/contacts/lists/{ids[nome]}").json() or {}).get("uniqueSubscribers") or 0)
            except Exception:
                pass
    finally:
        if chiudi:
            try:
                c.close()
            except Exception:
                pass
    return esito


def lavoro(v: str, items_fn, lungo=None, attesa_min: int = 30, dormi=time.sleep) -> bool | None:
    """Voce di BREVO_IMPORT_LISTE: email_esempi:<giro>[:<quanti per gruppo>]. Aspetta che il sito abbia le auto in
    memoria, poi prepara(). Segnata fatta solo a lavoro finito (coda_lavori._lungo); un tentativo rifatto dopo un
    errore riprende gli STESSI contatti del giro (stessi token), non ne sceglie altri: per altri contatti serve un
    giro nuovo. Nei log solo conteggi."""
    chiave = re.sub(r"[^A-Za-z0-9_:.-]", "", v)[:60]
    parti = chiave.split(":")
    giro = ":".join(parti[:2])
    per_gruppo = int(parti[2]) if len(parti) > 2 and parti[2].isdigit() else 0
    if lungo is None:
        from ..pipeline.coda_lavori import _lungo as lungo

    def fai():
        items = []
        for _ in range(max(1, attesa_min)):
            items = list(items_fn() or [])
            if items:
                break
            dormi(60)
        if not items:
            raise RuntimeError("il sito non ha ancora le auto")
        esito = prepara(items, giro, per_gruppo)
        lg.warning("EMAIL_ESEMPI %s", json.dumps(esito, ensure_ascii=False))
        perche = incompleto(esito)
        if perche:
            raise RuntimeError("lavoro incompleto: " + perche)
    return lungo(chiave, "EMAIL_ESEMPI", fai)


def lavoro_pulisci(v: str, lungo=None) -> bool | None:
    """Voce di BREVO_IMPORT_LISTE: email_pulisci:<etichetta>, da lanciare prima di ogni invio (pulisci_liste)."""
    chiave = re.sub(r"[^A-Za-z0-9_:.-]", "", v)[:60]
    if lungo is None:
        from ..pipeline.coda_lavori import _lungo as lungo

    def fai():
        c = _client()
        try:
            esito = pulisci_liste(c)
        finally:
            c.close()
        lg.warning("EMAIL_PULISCI %s", json.dumps(esito, ensure_ascii=False))
        if esito.get("tolti_falliti") or (esito.get("coda_stop") or {}).get("ancora_in_coda"):
            raise RuntimeError("pulizia incompleta")
    return lungo(chiave, "EMAIL_PULISCI", fai)


# ---------- /mi/<token> per il canale email ----------
def nome_log(nome) -> str:
    """Il nome del salone per i log: senza caratteri di controllo e, se dentro ci sono più di 6 cifre (potrebbe
    essere un numero di telefono finito nel nome), senza le cifre."""
    n = re.sub(r"[\x00-\x1f\x7f]", " ", str(nome or "?"))[:80]
    if sum(ch.isdigit() for ch in n) > 6:
        n = " ".join(re.sub(r"[\d+]", "", n).split()) or "?"
    return n


def token_email(token) -> bool:
    """Il token ha la forma di quelli delle email (8 caratteri). Vale per la PAGINA: un token di 8 caratteri vede la
    pagina delle email, che esista o no, così la pagina non dice se un token è valido."""
    from .sms_saloni import token_pulito
    return len(token_pulito(token) or "") == TOKEN_LEN


def trova(conn, token) -> dict | None:
    """La riga di email_link del token (None se il token non è di una email)."""
    from .sms_saloni import token_pulito
    tok = token_pulito(token)
    if not tok:
        return None
    assicura(conn)
    r = conn.execute("SELECT token, email, nome, telefono, giro, creato_at FROM email_link WHERE token = %s", (tok,)).fetchone()
    return dict(r) if r else None


def _data(v) -> dt.datetime | None:
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


_campagne: dict = {"t": 0.0, "partenze": []}


def _partenze_campagne(c, adesso: dt.datetime) -> list[dt.datetime]:
    """Quando sono partite le ultime campagne email (letto da Brevo al massimo una volta ogni 2 minuti)."""
    if time.monotonic() - _campagne["t"] < 120 and _campagne["t"]:
        return _campagne["partenze"]
    out = []
    r = c.get(f"{API}/emailCampaigns", params={"limit": 50, "offset": 0, "sort": "desc", "excludeHtmlContent": "true"})
    if r.status_code < 300:
        for x in (r.json() or {}).get("campaigns") or []:
            if x.get("status") in ("sent", "in_process", "queued"):
                d = _data(x.get("sentDate") or x.get("scheduledAt"))
                if d:
                    out.append(d)
        _campagne.update(t=time.monotonic(), partenze=out)
    return out


def forse_anteprima(c, riga: dict, adesso: dt.datetime, env=None) -> tuple[str | None, int | None]:
    """(motivo, secondi dall'invio) se il clic arriva troppo presto dopo l'invio per essere di una persona; (None, s)
    se è un clic vero; (None, None) se l'invio non si conosce (Brevo non risponde): allora il clic conta."""
    env = os.environ if env is None else env

    def secondi(nome, pred):
        try:
            return max(1, int(env.get(nome) or pred))
        except ValueError:
            return pred
    creato = _data(riga.get("creato_at")) or dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
    try:
        r = c.get(f"{API}/contacts/{quote(riga['email'], safe='')}/campaignStats")
        if r.status_code < 300:
            inviati = [d for d in (_data(x.get("eventTime")) for x in (r.json() or {}).get("messagesSent") or [])
                       if d and creato <= d <= adesso + dt.timedelta(seconds=60)]
            if inviati:
                s = int((adesso - max(inviati)).total_seconds())
                return ("subito_dopo_l_invio" if s < secondi("EMAIL_ANTEPRIMA_S", 120) else None), s
        partenze = [d for d in _partenze_campagne(c, adesso) if creato <= d <= adesso + dt.timedelta(seconds=60)]
        if partenze:
            s = int((adesso - max(partenze)).total_seconds())
            return ("subito_dopo_la_partenza" if s < secondi("EMAIL_ANTEPRIMA_GIRO_S", 600) else None), s
    except Exception:
        pass
    return None, None


def registra_clic(conn, token, user_agent: str = "", c=None, adesso: dt.datetime | None = None, log=None, env=None) -> dict:
    """Clic sul pulsante dell'email: salva in email_clic e scrive EMAIL_INTERESSATO (nome del salone, ultime 4 cifre se
    c'è un telefono, numero di clic veri) oppure EMAIL_FORSE_ANTEPRIMA. Mai l'indirizzo email nei log."""
    from . import sms_saloni as SS
    log = log or lg
    riga = trova(conn, token)
    if not riga:
        return {"contato": False, "motivo": "sconosciuto"}
    adesso = adesso or dt.datetime.now(dt.timezone.utc)
    ua = re.sub(r"[\x00-\x1f\x7f]", " ", str(user_agent or ""))[:200]
    nome = nome_log(riga["nome"])
    tel = SS.coda4(riga["telefono"]) if riga.get("telefono") else "-"
    motivo, dopo = ("programma_automatico", None) if SS.RE_BOT.search(ua) else (None, None)
    if not motivo:
        chiudi = c is None
        try:
            c = c or _client()
            motivo, dopo = forse_anteprima(c, riga, adesso, env)
        except Exception:
            motivo, dopo = None, None
        finally:
            if chiudi and c is not None:
                try:
                    c.close()
                except Exception:
                    pass
    conn.execute("INSERT INTO email_clic (token, user_agent, anteprima) VALUES (%s, %s, %s)", (riga["token"], ua, motivo))
    n = conn.execute("SELECT count(*) AS n FROM email_clic WHERE token = %s AND anteprima IS NULL",
                     (riga["token"],)).fetchone()["n"]
    conn.commit()
    quando = "?" if dopo is None else str(dopo)
    if motivo:
        log.warning("EMAIL_FORSE_ANTEPRIMA token=%s nome=%s tel=%s motivo=%s dopo_s=%s", riga["token"], nome, tel, motivo, quando)
        return {"contato": False, "motivo": "anteprima", "perche": motivo, "dopo_s": dopo}
    log.warning("EMAIL_INTERESSATO token=%s canale=email nome=%s tel=%s n=%d dopo_s=%s", riga["token"], nome, tel, n, quando)
    return {"contato": True, "n": n, "nome": riga["nome"], "dopo_s": dopo}


def registra_stop(conn, token, c=None, log=None, dormi=time.sleep, piu_tardi=None) -> bool:
    """"Non voglio più ricevere email" dalla pagina: l'indirizzo va nei disiscritti (mai più email, anche se il
    contatto viene cancellato da Brevo); in Brevo diventa emailBlacklisted ed esce dalle due liste email. Se Brevo
    non risponde la richiesta resta in coda (stop_brevo) e si riprova più tardi (`piu_tardi`, predefinito: un thread;
    poi a ogni disiscrizione, a email_pulisci e a email_esempi). Se il database non scrive solleva: la pagina dice
    di riprovare."""
    from . import invii
    log = log or lg
    riga = trova(conn, token)
    if not riga:
        return False
    invii.aggiungi_disiscritti(conn, [riga["email"]], "pagina_mi " + riga["token"])
    conn.execute("UPDATE email_link SET stop_at = now(), stop_brevo = %s WHERE token = %s", ("da_fare", riga["token"]))
    conn.commit()
    chiudi = c is None
    try:
        c = c or _client()
        brevo = stop_in_brevo(c, riga["email"], dormi)
    except Exception as e:
        brevo = "errore " + type(e).__name__
    finally:
        if chiudi and c is not None:
            try:
                c.close()
            except Exception:
                pass
    conn.execute("UPDATE email_link SET stop_brevo = %s WHERE token = %s", (brevo[:60], riga["token"]))
    conn.commit()
    in_coda = brevo.startswith("errore")
    log.warning("EMAIL_STOP token=%s nome=%s brevo=%s%s", riga["token"], nome_log(riga["nome"]), brevo,
                " in_coda=si" if in_coda else "")
    if in_coda:
        if piu_tardi is None:
            import threading
            threading.Thread(target=_riprova_piu_tardi, daemon=True, name="email_stop_coda").start()
        else:
            piu_tardi()
    return True


def interessati(conn) -> list[dict]:
    """Per l'amministratore: chi ha toccato il pulsante dell'email. `clic` sono i clic veri, `forse_anteprima` quelli
    arrivati subito dopo l'invio (programmi di posta); chi ha solo quelli ha clic = 0."""
    assicura(conn)
    righe = conn.execute(
        "SELECT 'email' AS canale, l.token, l.nome, l.telefono, l.email, l.giro, "
        "count(*) FILTER (WHERE c.anteprima IS NULL) AS clic, count(*) FILTER (WHERE c.anteprima IS NOT NULL) AS forse_anteprima, "
        "min(c.at) FILTER (WHERE c.anteprima IS NULL) AS primo_clic, max(c.at) FILTER (WHERE c.anteprima IS NULL) AS ultimo_clic, "
        "l.stop_at IS NOT NULL AS stop "
        "FROM email_clic c JOIN email_link l ON l.token = c.token "
        "GROUP BY l.token, l.nome, l.telefono, l.email, l.giro, l.stop_at "
        "ORDER BY max(c.at) FILTER (WHERE c.anteprima IS NULL) DESC NULLS LAST, max(c.at) DESC LIMIT 5000").fetchall()
    return [dict(r) for r in righe]
