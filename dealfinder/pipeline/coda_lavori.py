"""16/11 (fond-server): lavori una tantum di BREVO_IMPORT_LISTE spostati dal sito al cron.

Prima il sito (512 MB) li faceva TUTTI in thread all'avvio: raccolte di ore (raccolta60), analisi (perche_fuori,
prova_cieca), chiamate Brevo... e si gonfiava. Ora:
  - il sito, all'avvio, per ogni voce SPOSTABILE di BREVO_IMPORT_LISTE scrive una riga nella coda (tabella coda_lavori,
    valore = la voce, una sola volta: un riavvio non la duplica) e non la esegue;
  - il cron la esegue: le voci brevi all'inizio del giro opportunità (ogni 3 ore), le lunghe nel passo notturno del
    cron mercato. Stesso codice di prima (spostato qui senza cambiarlo, tolte solo le attese di avvio del sito) e stesse
    chiavi _una_volta: una voce già fatta (anche dal sito prima di questo cambio, es. raccolta60:tel1611) NON si rifà;
  - se al cron manca una variabile che serve (es. BREVO_API_KEY) la riga passa a 'web' e la fa il sito, come prima;
    se nessun cron la prende entro LAVORI_RIPIEGO_ORE (6) ore, la fa il sito (ripiego), sempre con le stesse chiavi.
Le voci che leggono l'elenco del sito in memoria (su_misura, nuovi_sm, rinvio_sm, su_misura2, zona, wa30, "1") restano
nel sito. LAVORI_NEL_CRON (sul sito): 'lunghi' (predefinito) manda al cron solo le voci lunghe (raccolta60, perche_fuori,
prova_cieca: quelle che gonfiano il sito); 'tutti' anche le brevi (chiamate Brevo: partono al giro dopo, fino a 3 ore);
'0' torna a prima. LAVORI_CRON=0 (sul cron) spegne l'esecuzione nel cron (le voci restano in coda, poi il sito le fa).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

lg = logging.getLogger("scovo")
log = logging.getLogger("coda_lavori")

BREVO = ("BREVO_API_KEY",)
# prefisso -> (lungo, variabili che servono al processo che lo esegue)
SPOSTABILI: dict = {
    "perche_fuori:": (True, ()),
    "prova_cieca:": (True, ("ANTHROPIC_API_KEY",)),
    "raccolta60:": (True, ()),
    "mix:": (False, BREVO),
    "togli_lista:": (False, BREVO),
    "giro_lotto:": (False, BREVO),
    "fuori84:": (False, BREVO),
    "liberoms:": (False, BREVO),
    "mattina:": (False, BREVO),
    "fermi:": (False, BREVO),
    "fermi2:": (False, BREVO),
    "conta_email:": (False, BREVO),
    "pulita:": (False, BREVO),
    "cliccati:": (False, BREVO),
    "aperture:": (False, BREVO),
    "rimetti:": (False, BREVO),
    "utenti:": (False, ()),             # solo una lettura del database
}
RIPIEGO_ORE = 6                 # voci brevi non prese da nessun cron entro tante ore: le fa il sito
RIPIEGO_ORE_LUNGHI = 30         # voci lunghe: il passo notturno passa una volta al giorno

SQL_TABELLA = """CREATE TABLE IF NOT EXISTS coda_lavori (
    valore     TEXT PRIMARY KEY,
    stato      TEXT NOT NULL DEFAULT 'in_coda',      -- in_coda | in_corso | fatto | web | errore
    lungo      BOOLEAN NOT NULL DEFAULT false,
    creato_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    preso_at   TIMESTAMPTZ,
    finito_at  TIMESTAMPTZ,
    chi        TEXT,
    nota       TEXT
)"""


def _prefisso(v: str) -> str | None:
    for p in SPOSTABILI:
        if v.startswith(p):
            return p
    return None


def modo() -> str:
    """LAVORI_NEL_CRON (sul sito): 'lunghi' (predefinito: raccolte e analisi di ore, quelle che gonfiano il sito),
    'tutti' (anche le chiamate Brevo brevi: partono al giro del cron, fino a 3 ore dopo), '0' (tutto nel sito)."""
    m = (os.environ.get("LAVORI_NEL_CRON") or "lunghi").strip().lower()
    return {"1": "lunghi", "si": "lunghi"}.get(m, m)


def spostabile(v: str) -> bool:
    """La voce va al cron? Le lunghe sempre (salvo LAVORI_NEL_CRON=0), le brevi solo con LAVORI_NEL_CRON=tutti."""
    if not v or _prefisso(v) is None:
        return False
    m = modo()
    return m == "tutti" or (m == "lunghi" and lungo(v))


def lungo(v: str) -> bool:
    p = _prefisso(v)
    return bool(p and SPOSTABILI[p][0])


def mancano(v: str, env: dict | None = None) -> list[str]:
    """Variabili che servono a `v` e mancano in questo processo."""
    env = os.environ if env is None else env
    p = _prefisso(v)
    if not p:
        return []
    req = SPOSTABILI[p][1]
    if p == "prova_cieca:" and v.split(":")[3:4] == ["solo_calcolo"]:
        req = ()
    return [k for k in req if not (env.get(k) or "").strip()]


def _connetti():
    from ..db import connect
    return connect()


def assicura(conn) -> None:
    conn.execute(SQL_TABELLA)
    conn.commit()


def metti(v: str, connetti=None) -> bool:
    """Dal sito: la voce va in coda (una volta sola). True se è in coda (nuova o già presente)."""
    with (connetti or _connetti)() as c:
        assicura(c)
        r = c.execute("INSERT INTO coda_lavori (valore, lungo) VALUES (%s, %s) ON CONFLICT (valore) DO NOTHING "
                      "RETURNING valore", (v, lungo(v))).fetchone()
        c.commit()
    lg.warning("LAVORI %s %s per il cron", v[:60], "messo in coda" if r else "già in coda")
    return True


def prendi(chi: str, lunghi: bool | None, connetti=None, ripiego_ore: float | None = None) -> list[str]:
    """Le voci da fare adesso per `chi` ('cron' o 'web'), segnate 'in_corso' (FOR UPDATE SKIP LOCKED: due processi non
    prendono la stessa). cron: 'in_coda' (lunghi=True le lunghe, False le brevi, None tutte). web: 'web' e le 'in_coda'
    più vecchie di ripiego_ore. Una riga 'in_corso' da più di 13 ore (processo morto) torna prendibile."""
    with (connetti or _connetti)() as c:
        assicura(c)
        if chi == "web":
            ore = float(ripiego_ore if ripiego_ore is not None else (os.environ.get("LAVORI_RIPIEGO_ORE") or RIPIEGO_ORE))
            ore_l = max(ore, float(os.environ.get("LAVORI_RIPIEGO_ORE_LUNGHI") or RIPIEGO_ORE_LUNGHI))
            righe = c.execute(
                "SELECT valore FROM coda_lavori WHERE stato = 'web' OR (stato = 'in_coda' AND creato_at < now() - "
                "make_interval(hours => CASE WHEN lungo THEN %s ELSE %s END)) OR (stato = 'in_corso' AND preso_at < "
                "now() - interval '13 hours') ORDER BY creato_at FOR UPDATE SKIP LOCKED", (int(ore_l), int(ore))).fetchall()
        else:
            righe = c.execute(
                "SELECT valore FROM coda_lavori WHERE (stato = 'in_coda' OR (stato = 'in_corso' AND preso_at < now() - "
                "interval '13 hours')) AND (%s::boolean IS NULL OR lungo = %s::boolean) ORDER BY creato_at "
                "FOR UPDATE SKIP LOCKED", (lunghi, lunghi)).fetchall()
        vals = [r["valore"] for r in righe]
        if vals:
            c.execute("UPDATE coda_lavori SET stato = 'in_corso', preso_at = now(), chi = %s WHERE valore = ANY(%s)",
                      (chi, vals))
        c.commit()
    return vals


def segna(v: str, stato: str, nota: str = "", connetti=None) -> None:
    with (connetti or _connetti)() as c:
        c.execute("UPDATE coda_lavori SET stato = %s, finito_at = CASE WHEN %s IN ('fatto', 'errore') THEN now() "
                  "ELSE finito_at END, nota = %s WHERE valore = %s", (stato, stato, nota[:300], v))
        c.commit()


def esegui_coda(lunghi: bool | None, budget_min: float = 60.0, chi: str = "cron", connetti=None, esegui_fn=None) -> dict:
    """Esegue le voci in coda. Nel cron, una voce a cui manca una variabile passa al sito ('web'). Si ferma (le altre
    tornano 'in_coda') quando il tempo `budget_min` è finito."""
    if chi == "cron" and os.environ.get("LAVORI_CRON", "1") == "0":
        return {"spento": True}
    t0 = time.time()
    fatto = esegui_fn or esegui
    esito: dict = {}
    try:
        vals = prendi(chi, lunghi, connetti)
    except Exception as e:
        log.warning("LAVORI coda non letta: %s", type(e).__name__)
        return {"errore": type(e).__name__}
    for i, v in enumerate(vals):
        if (time.time() - t0) / 60 >= budget_min:
            for w in vals[i:]:
                segna(w, "in_coda", "tempo finito", connetti)
            esito["rimandati"] = len(vals) - i
            break
        manca = mancano(v) if chi == "cron" else []
        if manca:
            segna(v, "web", "mancano nel cron: " + ",".join(manca), connetti)
            log.warning("LAVORI %s al sito: nel cron mancano %s", v[:60], ",".join(manca))
            esito[v[:60]] = "web"
            continue
        try:
            fatto(v)
            segna(v, "fatto", chi, connetti)
            esito[v[:60]] = "fatto"
        except Exception as e:
            segna(v, "errore", type(e).__name__ + ": " + str(e)[:200], connetti)
            esito[v[:60]] = "errore"
    if esito:
        log.warning("LAVORI (%s, %s) %s", chi, "lunghi" if lunghi else ("brevi" if lunghi is False else "tutti"),
                    json.dumps(esito, ensure_ascii=False)[:500])
    return esito


def esegui(v: str) -> None:
    """Una voce spostabile: il codice che prima era in web/app.py (brevo_liste), invariato, senza le attese di avvio.
    Ogni ramo ha la sua chiave _una_volta (una voce già fatta non si rifà) e non solleva eccezioni."""
    from ..db import connect
    if v.startswith("perche_fuori:"):     # 16/11 (cliente): quali regole tengono fuori auto buone (solo log, niente AI)
        try:
            from ..pipeline.ciclo import _una_volta
            if not _una_volta(v):
                lg.warning("PERCHE_FUORI %s già fatta (togli il valore da BREVO_IMPORT_LISTE)", v)
                return
            from ..pricing import perche_fuori as _pf
            _pf.lavoro(v)
        except Exception as e:
            lg.warning("PERCHE_FUORI non riuscita: %s", str(e)[:300])
        return
    if v.startswith("prova_cieca:"):      # 12/11 (cliente): prova alla cieca M / perizia AI / Autohero (solo log)
        try:
            from ..ai import prova_cieca as _pc
            from ..pipeline.ciclo import _una_volta
            if not _una_volta(v):
                lg.warning("PROVA_CIECA %s già fatta (togli il valore da BREVO_IMPORT_LISTE)", v)
                return
            parti = v.split(":")
            with connect() as conn:
                _pc.esegui(conn, n=min(int(parti[1] or 30), 200),
                           giorni=min(int(parti[2]), 90) if len(parti) > 2 and parti[2] else 30,
                           ai=not (len(parti) > 3 and parti[3] == "solo_calcolo"))   # 12/11 sera margini fase 2
        except Exception as e:
            lg.warning("PROVA_CIECA non riuscita: %s", str(e)[:300])
        return
    if v.startswith("mix:"):              # 5/11 (cliente): nuove + richiamo del 3/11 fino a 1.200, invio subito
        try:
            from ..marketing import brevo_liste as bl
            from ..pipeline.ciclo import _una_volta
            if _una_volta(f"brevo_mix:{v}"):
                lg.warning("BREVO_MIX %s", json.dumps(bl.mix_e_invia(int(v.split(":")[1]))))
            else:
                lg.warning("BREVO_MIX %s già fatto", v)
        except Exception as e:
            lg.warning("BREVO_MIX non riuscito: %s", str(e)[:300])
        return
    if v.startswith("togli_lista:"):      # 10/11 sera (cliente): dalla lista di domani via chi ha ricevuto le campagne indicate
        try:
            import httpx as _hx
            from ..marketing.brevo_liste import _esporta, API as _API
            from ..marketing.su_misura import _chiave
            from ..pipeline.ciclo import _una_volta
            if not _una_volta(f"togli_lista:{v}"):
                lg.warning("TOGLI_LISTA %s già fatto", v)
                return
            lista, camp = v.split(":", 1)[1].split("@", 1)
            with _hx.Client(headers={"api-key": _chiave(), "accept": "application/json"}, timeout=60) as c:
                via = set()
                for cid in camp.split(","):
                    if cid.startswith("L"):          # L82 = i contatti della lista 82 (chi ha ricevuto quella campagna)
                        off = 0
                        while True:
                            r = c.get(f"{_API}/contacts/lists/{int(cid[1:])}/contacts", params={"limit": 500, "offset": off})
                            cs = (r.json() or {}).get("contacts") or [] if r.status_code < 300 else []
                            via |= {x["email"].lower() for x in cs if x.get("email")}
                            if len(cs) < 500:
                                break
                            off += 500
                    else:
                        via |= set(_esporta(c, int(cid), "all"))
                via = sorted(via)
                tolti = 0
                for k in range(0, len(via), 150):
                    r = c.post(f"{_API}/contacts/lists/{int(lista)}/contacts/remove", json={"emails": via[k:k + 150]})
                    if r.status_code < 300:
                        tolti += len((r.json().get("contacts") or {}).get("success") or [])
                rl = c.get(f"{_API}/contacts/lists/{int(lista)}").json()
            lg.warning("TOGLI_LISTA %s", json.dumps({"da_togliere": len(via), "tolti": tolti,
                                                       "rimasti": rl.get("uniqueSubscribers")}))
        except Exception as e:
            lg.warning("TOGLI_LISTA non riuscito: %s", str(e)[:300])
        return
    if v.startswith("giro_lotto:"):       # 16/11 (cliente): chi ha ricevuto la base esce da Brevo, entrano gli altri
        # (il resto: chi non l'ha ricevuta) dell'archivio con la stessa email programmata (marketing/giro_lotto.py)
        try:
            from ..marketing import giro_lotto as GL
            from ..pipeline.ciclo import _una_volta
            if not _una_volta(f"giro_lotto:{v[11:91]}"):
                lg.warning("GIRO_LOTTO %s già fatto (togli il valore da BREVO_IMPORT_LISTE)", v[:40])
                return
            lg.warning("GIRO_LOTTO %s", json.dumps(GL.lavoro(v)))
        except Exception as e:
            lg.warning("GIRO_LOTTO non riuscito: %s", type(e).__name__)
        return
    if v.startswith("raccolta60:"):        # 16/11 sera (cliente): TUTTI i commercianti entro 60 km, AS24 + Subito
        try:
            from ..pipeline import raccolta60 as R60
            from ..pipeline.ciclo import _una_volta
            if not _una_volta(f"raccolta60:{v[:60]}"):
                lg.warning("R60 %s già fatto", v[:60])
                return
            R60.lavoro(re.sub(r"[^A-Za-z0-9-]", "", v.split(":", 1)[1])[:20] or "1611")
        except Exception as e:
            lg.warning("R60 non riuscito: %s", str(e)[:300])
        return
    if v.startswith(("fuori84:", "liberoms:", "mattina:")):  # 16/11 (cliente): nuovi dal database, mai chi era nella lista 84
        try:
            from ..marketing import su_misura as SM
            from ..pipeline.ciclo import _una_volta
            if not _una_volta(f"fuori84:{v[:60]}"):
                lg.warning("FUORI84 %s già fatto", v[:60])
                return
            corpo, _, pref = v.split(":", 1)[1].replace("#", "~").partition("~")
            n, _, quando = corpo.partition("@")
            lg.warning("FUORI84 %s", json.dumps(SM.carica_fuori84(
                totale=int(n or 1495), quando=quando or "subito",
                oggi_pref={x for x in pref.split(",") if len(x) == 16}, solo_libero_ms=v.startswith("liberoms:"),
                mattina=v.startswith("mattina:"))))
        except Exception as e:
            lg.warning("FUORI84 non riuscito: %s", str(e)[:300])
        return
    if v.startswith(("fermi:", "fermi2:")):  # 12/11 sera (cliente): i contatti fermi nel database, non quelli di oggi
        try:
            from ..marketing import su_misura as SM
            from ..pipeline.ciclo import _una_volta
            if not _una_volta(f"fermi:{v[:80]}"):
                lg.warning("FERMI %s già fatto", v)
                return
            corpo, _, pref = v.split(":", 1)[1].replace("#", "~").partition("~")  # dopo '~': impronte di oggi (Render toglie da '#')
            n, quando = corpo.split("@", 1)
            pref = {x for x in pref.split(",") if len(x) == 16}
            lg.warning("FERMI %s", json.dumps(SM.ripesca_fermi(totale=int(n or 1400), quando=quando,
                                                               oggi_pref=pref)))
        except Exception as e:
            lg.warning("FERMI non riuscito: %s", str(e)[:300])
        return
    if v.startswith("conta_email:"):      # 8/11 (cliente): quante email abbiamo e quante si possono usare (solo numeri)
        try:
            from ..marketing import brevo_liste as bl
            lg.warning("CONTA_EMAIL %s", json.dumps(bl.conteggio_email()))
        except Exception as e:
            lg.warning("CONTA_EMAIL non riuscito: %s", str(e)[:300])
        return
    if v.startswith("pulita:"):           # 8/11 (cliente): lista di chi apre, pulita, per il nuovo mittente (non invia)
        try:
            from ..marketing import brevo_liste as bl
            from ..pipeline.ciclo import _una_volta
            if _una_volta(f"brevo_pulita:{v}"):
                lg.warning("BREVO_PULITA %s", json.dumps(bl.lista_pulita(totale=int(v.split(":")[1] or 400))))
            else:
                lg.warning("BREVO_PULITA %s già fatto", v)
        except Exception as e:
            lg.warning("BREVO_PULITA non riuscito: %s", str(e)[:300])
        return
    if v.startswith("cliccati:"):         # 9/11 (cliente): chi ha cliccato le email, elenco per email a info@
        try:
            from ..marketing import brevo_liste as bl
            from ..pipeline.ciclo import _una_volta
            if _una_volta(f"brevo_cliccati:{v}"):
                ids = [int(x) for x in v.split(":")[1].split(",") if x.strip()]
                lg.warning("BREVO_CLICCATI %s", json.dumps(bl.cliccati(ids)))
            else:
                lg.warning("BREVO_CLICCATI %s già fatto", v)
        except Exception as e:
            lg.warning("BREVO_CLICCATI non riuscito: %s", str(e)[:300])
        return
    if v.startswith("aperture:"):         # 4/11 sera (cliente): chi ha aperto la campagna, da chiamare
        try:
            from ..marketing import brevo_liste as bl
            from ..pipeline.ciclo import _una_volta
            if _una_volta(f"brevo_aperture:{v}"):
                r = bl.aperture(int(v.split(":")[1]))
                hs = r.pop("h", [])
                lg.warning("BREVO_APERTURE %s", json.dumps(r))
                for i in range(0, len(hs), 100):
                    lg.warning("BREVO_APERTURE_H %d %s", i, " ".join(hs[i:i + 100]))
            else:
                lg.warning("BREVO_APERTURE %s già fatto", v)
        except Exception as e:
            lg.warning("BREVO_APERTURE non riuscito: %s", str(e)[:300])
        return
    if v.startswith("utenti:"):           # 6/11 sera (cliente): quanto usano il sito i commercianti registrati (niente email nei log)
        try:
            from ..pipeline.ciclo import _una_volta
            if not _una_volta("utenti:" + v.split(":", 1)[1]):
                lg.warning("UTENTI già fatto")
                return
            with connect() as conn:
                righe = conn.execute("""
                    SELECT d.id, d.name, d.created_at::date AS dal, d.active, d.disattivato_da_admin AS spento,
                           d.abbonamento_stato AS stato, d.abbonamento_fino::date AS fino, d.accesso_fino::date AS accesso,
                           d.richiede_abbonamento AS richiede, d.stripe_customer IS NOT NULL AS stripe,
                           (SELECT a.piano FROM abbonamenti a WHERE a.dealer_id=d.id ORDER BY a.aggiornato_at DESC LIMIT 1) AS piano,
                           (SELECT a.stato FROM abbonamenti a WHERE a.dealer_id=d.id ORDER BY a.aggiornato_at DESC LIMIT 1) AS stato_stripe,
                           (SELECT count(*) FROM dealer_sessions s WHERE s.dealer_id=d.id) AS sessioni,
                           (SELECT max(s.last_seen_at)::timestamp(0) FROM dealer_sessions s WHERE s.dealer_id=d.id) AS ultimo,
                           (SELECT count(DISTINCT s.last_seen_at::date) FROM dealer_sessions s WHERE s.dealer_id=d.id
                              AND s.last_seen_at > now()-interval '30 days') AS giorni30,
                           (SELECT count(*) FROM listing_opens o WHERE o.dealer_id=d.id) AS aperture,
                           (SELECT count(*) FROM listing_opens o WHERE o.dealer_id=d.id AND o.first_opened_at > now()-interval '7 days') AS aperture7,
                           (SELECT count(*) FROM listing_opens o WHERE o.dealer_id=d.id AND o.first_opened_at > now()-interval '30 days') AS aperture30,
                           (SELECT max(o.first_opened_at)::timestamp(0) FROM listing_opens o WHERE o.dealer_id=d.id) AS ultima_apertura,
                           (SELECT count(*) FROM appuntamenti p WHERE p.dealer_id=d.id) AS appuntamenti,
                           (SELECT json_object_agg(status, n) FROM (SELECT status, count(*) n FROM dealer_feedback f
                              WHERE f.dealer_id=d.id GROUP BY status) x) AS esiti
                    FROM dealers d ORDER BY d.id""").fetchall()
            for i in range(0, len(righe), 10):
                lg.warning("UTENTI_RIGHE %d %s", i, json.dumps([dict(r) for r in righe[i:i + 10]], ensure_ascii=False, default=str))
            lg.warning("UTENTI_FINE %d", len(righe))
        except Exception as e:
            lg.warning("UTENTI non riuscito: %s", str(e)[:300])
        return
    if v.startswith("rimetti:"):          # 4/11 notte (cliente): "rimetti:<id campagna>" una volta sola
        try:
            from ..marketing import brevo_liste as bl
            from ..pipeline.ciclo import _una_volta
            if _una_volta(f"brevo_rimetti:{v}"):
                lg.warning("BREVO_RIMETTI %s", json.dumps(bl.rimetti_e_invia(int(v.split(":")[1]))))
            else:
                lg.warning("BREVO_RIMETTI %s già fatto", v)
        except Exception as e:
            lg.warning("BREVO_RIMETTI non riuscito: %s", str(e)[:300])
        return
    lg.warning("LAVORI %s: voce non conosciuta", v[:60])
