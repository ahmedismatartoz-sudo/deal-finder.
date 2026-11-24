"""24/11 (cliente): SMS ai SALONI (solo commercianti, mai privati) "senza esempi", con il link "Mi interessa".

Gli SMS non hanno pulsanti: ogni salone riceve un link personale <PUBLIC_URL>/mi/<token>. Chi lo tocca vede una pagina
di ringraziamento e finisce nell'elenco di chi va richiamato (/api/admin/sms_interessati).

Lavoro una tantum da BREVO_IMPORT_LISTE (una volta per etichetta):
  sms_saloni:<giro>   invia a tutta la lista
  sms_prova:<giro>    invia SOLO al primo numero della lista (prova al titolare) e si ferma
Variabili: SMS_LISTA (JSON [{"tel":"+393...","nome":"..."}]), SMS_MITTENTE (default "Skovo", alfanumerico, massimo 11),
PUBLIC_URL, BREVO_API_KEY (quella che il codice usa già), SMS_TIPO (marketing | transactional, default marketing),
SMS_SALONI_MAX (tetto facoltativo al numero di invii), SMS_PAUSA_S (default 1).

Regole: solo tra le 9:00 e le 20:00 ora di Roma; mai più dei crediti SMS disponibili; mai a chi è in sms_stop; un
numero una volta sola per giro. Nei log MAI il numero intero: solo le ultime 4 cifre. Il telefono intero sta solo
nella tabella sms_link (è un salone) e nella risposta dell'endpoint per l'amministratore.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import re
import secrets
import time

lg = logging.getLogger("scovo")

API = "https://api.brevo.com/v3"
TEL_TITOLARE = "+393482425633"          # il numero di Skovo mostrato nella pagina (pubblico, è il nostro)
TEL_TITOLARE_TESTO = "348 242 5633"
ORA_DA, ORA_A = 9, 20                   # si invia con 9 <= ora di Roma < 20
ALFABETO = "abcdefghjkmnpqrstuvwxyz23456789"   # senza 0/o, 1/l/i: si legge e si detta senza errori
TOKEN_LEN = 7
RE_TOKEN = re.compile(r"[A-Za-z0-9]{6,8}")

# alfabeto GSM 03.38 di base (7 bit): un carattere fuori da qui fa passare l'SMS a 70 caratteri (UCS-2)
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
           "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_DOPPI = set("^{}\\[~]|€")           # ammessi ma contano 2

TESTI = (
    "Skovo: auto di privati sotto prezzo vicino a te, con margine già calcolato. Ti interessa? Tocca qui: {link} Stop: dal link",
    "Skovo: auto di privati sotto prezzo vicino a te, margine già calcolato. Ti interessa? Tocca: {link} Stop: dal link",
    "Skovo: auto di privati sotto prezzo vicino a te. Ti interessa? {link} Stop: dal link",
)

SQL_TABELLE = (
    """CREATE TABLE IF NOT EXISTS sms_link (
        token      TEXT PRIMARY KEY,
        nome       TEXT,
        telefono   TEXT NOT NULL,
        giro       TEXT NOT NULL,
        creato_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        esito      TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS sms_link_giro_tel ON sms_link (giro, telefono)",
    """CREATE TABLE IF NOT EXISTS sms_clic (
        id         BIGSERIAL PRIMARY KEY,
        token      TEXT NOT NULL,
        at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        user_agent TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS sms_clic_token ON sms_clic (token)",
    """CREATE TABLE IF NOT EXISTS sms_stop (
        telefono   TEXT PRIMARY KEY,
        at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        motivo     TEXT
    )""",
)


def assicura(conn) -> None:
    for q in SQL_TABELLE:
        conn.execute(q)
    conn.commit()


# ---------- testo ----------
def lunghezza_gsm7(testo: str) -> int | None:
    """Caratteri che l'SMS occupa in GSM-7; None se c'è un carattere fuori dall'alfabeto (emoji, accenti strani)."""
    n = 0
    for ch in testo:
        if ch in GSM7:
            n += 1
        elif ch in GSM7_DOPPI:
            n += 2
        else:
            return None
    return n


def base_url(env=None) -> str:
    env = os.environ if env is None else env
    return (env.get("PUBLIC_URL") or "https://scovo.onrender.com").strip().rstrip("/")


def link(token: str, env=None) -> str:
    return f"{base_url(env)}/mi/{token}"


def testo_sms(token: str, env=None) -> str:
    """Il testo più completo che sta in UN SMS (160 caratteri GSM-7) con il link personale e lo STOP."""
    l = link(token, env)
    for t in TESTI:
        s = t.format(link=l)
        n = lunghezza_gsm7(s)
        if n is not None and n <= 160:
            return s
    raise ValueError("testo SMS oltre 160 caratteri o non GSM-7: PUBLIC_URL troppo lungo")


# ---------- numeri ----------
def norm_tel(t) -> str | None:
    """Cellulare italiano in forma +393xxxxxxxxx (3xx + 10 cifre in tutto), altrimenti None."""
    grezzo = str(t or "").strip()
    d = re.sub(r"\D", "", grezzo)
    if d.startswith("0039"):
        d = d[4:]
    elif d.startswith("39") and (len(d) > 10 or grezzo.startswith("+")):
        d = d[2:]
    return "+39" + d if re.fullmatch(r"3\d{9}", d) else None


def coda4(t) -> str:
    """Per i log: solo le ultime 4 cifre."""
    d = re.sub(r"\D", "", str(t or ""))
    return "***" + d[-4:] if len(d) >= 4 else "***?"


def nuovo_token(esistenti=()) -> str:
    while True:
        t = "".join(secrets.choice(ALFABETO) for _ in range(TOKEN_LEN))
        if t not in esistenti:
            return t


LISTE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "sms_liste")


def leggi_lista(env=None, giro: str | None = None) -> list[dict]:
    """Da env SMS_LISTA; se manca (Render ha un tetto di 300 variabili, 24/11), dal file data/sms_liste/<numero>.json
    del giro (es. sms_prova:2411 e sms_saloni:2411 -> 2411.json). Solo numeri di SALONI, mai privati."""
    env = os.environ if env is None else env
    testo = (env.get("SMS_LISTA") or "").strip()
    if not testo and giro:
        nome = re.sub(r"[^A-Za-z0-9_-]", "", giro.split(":", 1)[-1])
        f = os.path.join(LISTE_DIR, nome + ".json")
        if nome and os.path.isfile(f):
            with open(f, encoding="utf-8") as fh:
                testo = fh.read().strip()
    dati = json.loads(testo or "[]")
    if not isinstance(dati, list):
        raise ValueError("SMS_LISTA deve essere un elenco JSON")
    return [x for x in dati if isinstance(x, dict)]


def prepara(lista: list[dict], stop: set[str]) -> tuple[list[dict], dict]:
    """Destinatari validi nell'ordine della lista, senza doppioni e senza chi ha detto STOP."""
    visti, out = set(), []
    salti = {"non_validi": 0, "doppioni": 0, "stop": 0}
    for x in lista:
        tel = norm_tel(x.get("tel"))
        if not tel:
            salti["non_validi"] += 1
        elif tel in visti:
            salti["doppioni"] += 1
        elif tel in stop:
            salti["stop"] += 1
            visti.add(tel)
        else:
            visti.add(tel)
            out.append({"tel": tel, "nome": str(x.get("nome") or "").strip()[:120]})
    return out, salti


# ---------- orario ----------
def ora_roma(adesso: dt.datetime | None = None) -> dt.datetime:
    from zoneinfo import ZoneInfo
    adesso = adesso or dt.datetime.now(dt.timezone.utc)
    return adesso.astimezone(ZoneInfo("Europe/Rome"))


def in_fascia(adesso: dt.datetime | None = None) -> bool:
    return ORA_DA <= ora_roma(adesso).hour < ORA_A


# ---------- Brevo ----------
def mittente(env=None) -> str:
    env = os.environ if env is None else env
    m = re.sub(r"[^A-Za-z0-9]", "", env.get("SMS_MITTENTE") or "")[:11]
    return m or "Skovo"


def crediti_sms(c) -> float | None:
    """Crediti SMS dell'account (GET /v3/account, voce del piano con type sms). None se non si leggono."""
    r = c.get(f"{API}/account")
    if r.status_code >= 300:
        return None
    for p in (r.json() or {}).get("plan") or []:
        if str(p.get("type") or "").lower() == "sms":
            try:
                return float(p.get("credits"))
            except (TypeError, ValueError):
                return None
    return None


def _client():
    import httpx
    from .brevo_liste import _chiave
    key = _chiave()
    if not key:
        raise RuntimeError("manca BREVO_API_KEY")
    return httpx.Client(headers={"api-key": key, "accept": "application/json"}, timeout=30)


def _fmt(n) -> str:
    return "?" if n is None else (str(int(n)) if float(n) == int(n) else f"{n:.2f}")


# ---------- il lavoro ----------
def invia(giro: str, conn, c, *, solo_primo: bool = False, env=None, adesso=None, dormi=time.sleep, log=None) -> dict:
    """Invia gli SMS del giro. `conn` database, `c` client HTTP con la chiave Brevo, `adesso` funzione che dà l'ora
    (per i test). Non solleva per un singolo invio fallito; torna i conteggi."""
    env = os.environ if env is None else env
    log = log or lg
    adesso = adesso or (lambda: dt.datetime.now(dt.timezone.utc))
    st = {"inviati": 0, "falliti": 0, "saltati": 0, "crediti_prima": None, "crediti_dopo": None}

    def fine(motivo: str | None = None) -> dict:
        if motivo:
            st["fermato"] = motivo
        log.warning("SMS_FINE inviati=%d falliti=%d saltati=%d crediti_prima=%s crediti_dopo=%s%s", st["inviati"],
                    st["falliti"], st["saltati"], _fmt(st["crediti_prima"]), _fmt(st["crediti_dopo"]),
                    f" fermato={motivo}" if motivo else "")
        return st

    if not in_fascia(adesso()):
        log.warning("SMS_FUORI_ORARIO ora di Roma %s: si invia solo tra le %d:00 e le %d:00, nessun SMS inviato",
                    ora_roma(adesso()).strftime("%H:%M"), ORA_DA, ORA_A)
        return fine("fuori_orario")
    assicura(conn)
    stop = {r["telefono"] for r in conn.execute("SELECT telefono FROM sms_stop").fetchall()}
    lista = leggi_lista(env, giro)
    if solo_primo:
        lista = lista[:1]          # revisione: la prova va SOLO al primo della lista (il titolare), mai a un salone
    dest, salti = prepara(lista, stop)
    st["saltati"] = sum(salti.values())
    log.warning("SMS_LISTA validi=%d non_validi=%d doppioni=%d stop=%d", len(dest), salti["non_validi"],
                salti["doppioni"], salti["stop"])
    if solo_primo:
        dest = dest[:1]
    try:
        massimo = int(env.get("SMS_SALONI_MAX") or 0)
    except ValueError:
        massimo = 0
    if massimo > 0 and len(dest) > massimo:
        st["saltati"] += len(dest) - massimo
        dest = dest[:massimo]
    if not dest:
        return fine("lista_vuota")
    crediti = crediti_sms(c)
    st["crediti_prima"] = st["crediti_dopo"] = crediti
    if crediti is None:
        log.warning("SMS_CREDITI non letti: nessun SMS inviato")
        st["saltati"] += len(dest)
        return fine("crediti_non_letti")
    pausa = float(env.get("SMS_PAUSA_S") or 1.0)
    tipo = "transactional" if (env.get("SMS_TIPO") or "").strip().lower().startswith("trans") else "marketing"
    costo = 1.0                                  # crediti dell'ultimo SMS (Brevo lo dice nella risposta)
    restano = crediti
    # revisione: mai un secondo SMS a chi l'ha già ricevuto in un altro giro sms_saloni degli ultimi 30 giorni
    gia = {r["telefono"] for r in conn.execute(
        "SELECT telefono FROM sms_link WHERE giro = %s" + ("" if solo_primo else
        " OR (giro LIKE 'sms_saloni:%%' AND esito = 'ok' AND creato_at > now() - interval '30 days')"),
        (giro,)).fetchall()}
    for i, d in enumerate(dest, 1):
        if d["tel"] in gia:                      # stesso giro, già provato (riavvio a metà): mai due volte
            st["saltati"] += 1
            continue
        if restano < costo:
            st["saltati"] += len(dest) - i + 1
            return fine("crediti_finiti")
        if not in_fascia(adesso()):
            st["saltati"] += len(dest) - i + 1
            log.warning("SMS_FUORI_ORARIO dopo %d invii: mi fermo", st["inviati"])
            return fine("fuori_orario")
        token = None
        for _ in range(5):
            t = nuovo_token()
            r = conn.execute("INSERT INTO sms_link (token, nome, telefono, giro) VALUES (%s, %s, %s, %s) "
                             "ON CONFLICT (token) DO NOTHING RETURNING token", (t, d["nome"], d["tel"], giro)).fetchone()
            conn.commit()
            if r:
                token = t
                break
        if not token:
            st["falliti"] += 1
            log.warning("SMS_INVIO n=%d tel=%s esito=errore token", i, coda4(d["tel"]))
            continue
        codice = ""
        try:
            r = c.post(f"{API}/transactionalSMS/sms",
                       json={"sender": mittente(env), "recipient": d["tel"].lstrip("+"), "content": testo_sms(token, env),
                             "type": tipo, "tag": ("skovo_" + re.sub(r"[^A-Za-z0-9_]", "_", giro))[:60],
                             "unicodeEnabled": False})
            ok = r.status_code < 300
            codice = str(r.status_code)
            corpo = (r.json() or {}) if ok else {}
        except Exception as e:
            ok, codice, corpo = False, type(e).__name__, {}
        if ok:
            st["inviati"] += 1
            try:
                costo = max(float(corpo.get("usedCredits") or costo), 0.01)
            except (TypeError, ValueError):
                pass
            try:
                restano = float(corpo["remainingCredits"])
            except (KeyError, TypeError, ValueError):
                restano -= costo
            st["crediti_dopo"] = restano
            log.warning("SMS_INVIO n=%d tel=%s esito=ok", i, coda4(d["tel"]))
        else:
            st["falliti"] += 1
            log.warning("SMS_INVIO n=%d tel=%s esito=errore %s", i, coda4(d["tel"]), codice)
        try:
            conn.execute("UPDATE sms_link SET esito = %s WHERE token = %s", ("ok" if ok else "errore " + codice[:40], token))
            conn.commit()
        except Exception:
            pass
        if not ok and codice == "402":
            st["saltati"] += len(dest) - i
            return fine("crediti_finiti")
        if not ok and codice in ("401", "403"):
            st["saltati"] += len(dest) - i
            return fine("chiave_rifiutata")
        if st["falliti"] >= 5 and st["inviati"] == 0:
            st["saltati"] += len(dest) - i
            return fine("primi_invii_falliti")
        if i < len(dest):
            dormi(pausa)
    try:
        dopo = crediti_sms(c)
        if dopo is not None:
            st["crediti_dopo"] = dopo
    except Exception:
        pass
    return fine()


def lavoro(v: str, connetti=None, client=None, una_volta=None) -> dict | None:
    """Voce di BREVO_IMPORT_LISTE: sms_saloni:<giro> oppure sms_prova:<giro>. Una volta per etichetta. Fuori orario,
    senza lista o senza chiave NON consuma l'etichetta: al riavvio dentro la fascia parte."""
    prova = v.startswith("sms_prova:")
    giro = re.sub(r"[^A-Za-z0-9_:.-]", "", v)[:60]
    if not in_fascia():
        lg.warning("SMS_FUORI_ORARIO %s ora di Roma %s: si invia solo tra le %d:00 e le %d:00, nessun SMS inviato",
                   giro, ora_roma().strftime("%H:%M"), ORA_DA, ORA_A)
        return None
    try:
        if not leggi_lista(giro=giro):
            lg.warning("SMS_SALONI %s: SMS_LISTA vuota, nessun SMS inviato", giro)
            return None
    except Exception as e:
        lg.warning("SMS_SALONI %s: SMS_LISTA non leggibile (%s), nessun SMS inviato", giro, type(e).__name__)
        return None
    testo_sms("x" * TOKEN_LEN)                  # PUBLIC_URL troppo lungo: errore PRIMA di consumare l'etichetta
    c = (client or _client)()
    try:
        if una_volta is None:
            from ..pipeline.ciclo import _una_volta as una_volta
        if not una_volta(giro):
            lg.warning("SMS_SALONI %s già fatto (cambia etichetta per un nuovo giro)", giro)
            return None
        if connetti is None:
            from ..db import connect as connetti
        with connetti() as conn:
            return invia(giro, conn, c, solo_primo=prova)
    finally:
        try:
            c.close()
        except Exception:
            pass


# ---------- pagina /mi/<token> ----------
RE_BOT = re.compile(r"bot|crawl|spider|preview|facebookexternalhit|whatsapp|telegram|slack|skype|curl|wget|python|"
                    r"httpclient|okhttp|go-http|headless|scan|monitor|googleimageproxy|bingpreview", re.I)


def token_pulito(t) -> str | None:
    t = str(t or "")
    return t if RE_TOKEN.fullmatch(t) else None


def registra_clic(conn, token, user_agent: str = "", log=None) -> dict:
    """Clic sul link: salva in sms_clic e scrive SMS_INTERESSATO (nome del salone, ultime 4 cifre, numero di clic).
    Token sconosciuto: solo il log, con nome=?. Le anteprime automatiche dei link (bot) non contano."""
    log = log or lg
    tok = token_pulito(token)
    ua = re.sub(r"[\x00-\x1f\x7f]", " ", str(user_agent or ""))[:200]
    if RE_BOT.search(ua):
        return {"contato": False, "motivo": "anteprima"}
    riga = None
    if tok:
        assicura(conn)
        riga = conn.execute("SELECT nome, telefono FROM sms_link WHERE token = %s", (tok,)).fetchone()
    if not riga:
        log.warning("SMS_INTERESSATO token=%s nome=? tel=***? n=0", tok or "?")
        return {"contato": False, "motivo": "sconosciuto"}
    conn.execute("INSERT INTO sms_clic (token, user_agent) VALUES (%s, %s)", (tok, ua))
    n = conn.execute("SELECT count(*) AS n FROM sms_clic WHERE token = %s", (tok,)).fetchone()["n"]
    conn.commit()
    nome = re.sub(r"[\x00-\x1f\x7f]", " ", str(riga["nome"] or "?"))[:80]
    log.warning("SMS_INTERESSATO token=%s nome=%s tel=%s n=%d", tok, nome, coda4(riga["telefono"]), n)
    return {"contato": True, "n": n, "nome": riga["nome"]}


def registra_stop(conn, token, log=None) -> bool:
    """"Non voglio più ricevere SMS" dalla pagina: il numero del link va in sms_stop (mai più SMS)."""
    log = log or lg
    tok = token_pulito(token)
    if not tok:
        return False
    assicura(conn)
    riga = conn.execute("SELECT telefono FROM sms_link WHERE token = %s", (tok,)).fetchone()
    if not riga:
        return False
    conn.execute("INSERT INTO sms_stop (telefono, motivo) VALUES (%s, %s) ON CONFLICT (telefono) DO NOTHING",
                 (riga["telefono"], "pagina " + tok))
    conn.commit()
    log.warning("SMS_STOP token=%s tel=%s", tok, coda4(riga["telefono"]))
    return True


def interessati(conn) -> list[dict]:
    """Per l'amministratore: chi ha toccato il link, con nome, telefono, primo e ultimo clic e quante volte."""
    assicura(conn)
    righe = conn.execute(
        "SELECT l.token, l.nome, l.telefono, l.giro, count(*) AS clic, min(c.at) AS primo_clic, max(c.at) AS ultimo_clic, "
        "EXISTS (SELECT 1 FROM sms_stop s WHERE s.telefono = l.telefono) AS stop "
        "FROM sms_clic c JOIN sms_link l ON l.token = c.token "
        "GROUP BY l.token, l.nome, l.telefono, l.giro ORDER BY max(c.at) DESC LIMIT 2000").fetchall()
    return [dict(r) for r in righe]


def pagina(token: str | None = None, stop: bool = False) -> str:
    """Pagina pubblica di ringraziamento, uguale per ogni token (valido o no). Niente dati del salone."""
    tok = token_pulito(token)
    if stop:
        titolo, testo = "Fatto.", "Non riceverai più SMS da Skovo. Se cambi idea, chiamaci quando vuoi."
        sotto = ""
    else:
        titolo = "Grazie!"
        testo = (f"Ti chiamiamo a breve dal <strong>{TEL_TITOLARE_TESTO}</strong> per spiegarti come funziona Skovo.")
        sotto = (f'<form method="post" action="/mi/{html.escape(tok)}/stop"><button class="no" type="submit">'
                 f'Non voglio più ricevere SMS</button></form>') if tok else ""
    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<meta name="color-scheme" content="light dark">
<meta name="format-detection" content="telephone=no">
<title>Skovo</title>
<link rel="icon" href="/static/scovo/favicon.svg" type="image/svg+xml">
<style>
:root{{--bg:#f6f4ef;--card:#fff;--ink:#16181d;--muted:#5d6470;--line:#e4e0d6;--acc:#0f7b4b;--acc-ink:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1114;--card:#191c21;--ink:#f1efe9;--muted:#a3a9b4;--line:#2a2e36;--acc:#2fbf7f;--acc-ink:#06130c}}}}
*{{box-sizing:border-box}}
html,body{{margin:0;min-height:100%}}
body{{background:var(--bg);color:var(--ink);font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;min-height:100dvh;padding:24px 16px calc(24px + env(safe-area-inset-bottom))}}
main{{width:100%;max-width:420px;background:var(--card);border:1px solid var(--line);border-radius:20px;padding:32px 24px 24px;text-align:center}}
.marca{{font-weight:800;letter-spacing:.02em;font-size:15px;text-transform:uppercase;color:var(--acc);margin:0 0 20px}}
.ok{{width:56px;height:56px;border-radius:50%;background:var(--acc);color:var(--acc-ink);display:flex;align-items:center;justify-content:center;margin:0 auto 16px}}
h1{{font-size:32px;line-height:1.15;margin:0 0 10px;letter-spacing:-.01em}}
p{{margin:0 0 24px;color:var(--muted);text-wrap:pretty}}
p strong{{color:var(--ink);white-space:nowrap}}
.cta{{display:block;padding:16px 20px;border-radius:14px;background:var(--acc);color:var(--acc-ink);font-weight:700;font-size:18px;text-decoration:none}}
.cta:active{{transform:translateY(1px)}}
.cta:focus-visible,.no:focus-visible{{outline:3px solid var(--ink);outline-offset:3px}}
.nota{{font-size:14px;margin:14px 0 0}}
form{{margin:18px 0 0}}
.no{{background:none;border:0;padding:14px 8px;color:var(--muted);font:inherit;font-size:13px;text-decoration:underline;cursor:pointer}}
</style>
</head>
<body>
<main>
<p class="marca">Skovo</p>
<div class="ok" aria-hidden="true"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></div>
<h1>{titolo}</h1>
<p>{testo}</p>
<a class="cta" href="tel:{TEL_TITOLARE}">Chiamaci ora</a>
<p class="nota">Auto di privati sotto prezzo, con il margine già calcolato.</p>
{sotto}
</main>
</body>
</html>"""
