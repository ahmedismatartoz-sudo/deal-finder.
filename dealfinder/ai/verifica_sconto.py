"""24/11 (cliente: OK esplicito a verificare in automatico le auto ferme per "sconto_alto_da_controllare").

Com'era: un'auto con lo sconto in euro oltre il limite della fascia (sito_regole.Regole.sconto_euro) entra solo se il
controllo Claude (ai/controllo_auto.py: modello veloce, 2 foto piccole) la conferma (sito_regole.sconto_confermato_ai).
Il controllo gira già da solo su tutta la coda, senza tetto di spesa (18-19/11). Quelle che restano ferme sono quindi,
quasi tutte, auto GIÀ controllate una volta e non confermate: non si richiedono mai più ("stessa domanda, stessa
risposta") finché non cambia il prezzo, il contenuto o passano 14 giorni.

Cosa aggiunge questo modulo: UNA seconda verifica, più approfondita (modello migliore, fino a VERIFICA_FOTO foto più
grandi), solo per le auto in ZONA GRIGIA, cioè quelle che il primo controllo non ha confermato SOLTANTO perché:
  - truffa tra ai_pulita (0,30) e truffa_avviso (0,50): "non si capisce", non "truffa";
  - auto_vera tra 0,50 e sconto_ai_vera (0,70).
Un segnale netto del primo controllo (truffa da 0,50, auto_vera sotto 0,50, foto da internet, danni, dati non coerenti,
"scarta", perizia "caro"/"da verificare", foto non viste) NON si ridiscute: l'auto resta fuori, nessuna chiamata.

L'auto entra solo se TUTTO è pulito; un dato che manca = resta fuori:
  1. numero di telefono presente e italiano, nessun testo di pagamento estero/anticipo (dati del sito);
  2. almeno VERIFICA_FOTO_MIN foto nell'annuncio e davvero viste dall'AI;
  3. annuncio online da almeno VERIFICA_ORE_MIN ore (gli annunci finti durano poco) e senza un crollo del prezzo oltre
     VERIFICA_CALO_MAX rispetto al massimo dello storico (price_events: prezzo esca);
  4. la risposta della verifica passa le STESSE soglie del sito (controllo_auto.confermabile: sconto_confermato_ai,
     passo jev, prezzo intero) E soglie più strette (PULITA: è un secondo parere che ribalta un dubbio, deve essere
     chiaro, non al limite).
Se passa, la risposta si salva in decisione_ai come ogni decisione Claude (con il segno _verifica): è il sito, con le
sue regole di sempre, a farla entrare con l'avviso "Verificare prima di pagare". Se non passa, la decisione di prima
resta com'è e prende solo il segno _verifica: una sola verifica per decisione, mai "richiedere finché passa".

NON cambia: prezzo di mercato, margine, soglie del sito (sito_regole.py non è toccato, impronta delle regole uguale),
zone, km, anno, telefono obbligatorio, tetti dei commercianti. Mai numeri di telefono o email all'AI né nei log (solo
conteggi e id). Lo storico del VENDITORE non è usato: nel database non c'è (listings.seller_info non è scritto da nessun
collettore) e un dato obbligatorio sempre mancante terrebbe fuori tutto.

Spesa: nessun tetto di spesa esiste più (cliente 18/11); qui valgono limiti tecnici propri: VERIFICA_PER_GIRO (15) auto
per giro, VERIFICA_MAX_GIORNO (300) chiamate al giorno, una sola verifica per decisione, pausa comune su credito/limite.
Ogni chiamata si registra in ai_usage (task "verifica_sconto_alto") e si conferma subito.

Interruttore senza deploy: automatismi "verifica_sconto_alto" (o VERIFICA_SCONTO_ALTO=0 in locale).
Gira dentro il giro del controllo (controllo_auto.giro, stesso thread in background e stesso blocco): mai nel
calcolo dell'elenco. Riga di log: VERIFICA_SCONTO_ALTO.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

from . import client as AI
from . import controllo_auto as CA

log = logging.getLogger("verifica_sconto")

TASK = "verifica_sconto_alto"
ZONA_GRIGIA = ("truffa_zona_grigia", "auto_vera_zona_grigia")
AUTO_VERA_GRIGIA_MIN = 0.5
# soglie della seconda verifica: più strette di quelle del sito (mai più larghe: vedi pulita)
PULITA = {"truffa_max": 0.15, "auto_vera_min": 0.85, "foto_internet_max": 0.2, "dati_coerenti_min": 0.8,
          "danni_max": 0.3, "guasto_max": 0.3, "rata_max": 0.3, "km_credibili_min": 0.7}

AGGIUNTA = """

VERIFICA APPROFONDITA. Un primo controllo veloce, con meno foto, non è riuscito a capire se questo annuncio è vero.
Guarda TUTTE le foto allegate con attenzione: sono della stessa auto (colore, interni, cerchi, targa)? Sono foto fatte
da chi vende (cortile, strada, garage) o prese da un altro annuncio, da un catalogo, con scritte o marchi di altri siti,
o schermate? Il testo è scritto da chi conosce l'auto o è generico/copiato? Chiede caparre, bonifici, spedizioni,
contatti fuori dal sito? Se un dubbio resta, rispondi con il dubbio (0.5): non arrotondare verso il sì."""

_ATTESA: dict = {}        # listing_id -> tempo fino a cui non si riprova (foto non scaricabili, errore): 6 ore
STATO: dict = {"t": 0.0, "ultimo": {}}


def _env(nome: str, default: str) -> str:
    return os.environ.get(nome, default)


def _num(nome: str, default: float) -> float:
    try:
        return float(_env(nome, str(default)))
    except ValueError:
        return float(default)


def attiva() -> bool:
    if _env("VERIFICA_SCONTO_ALTO", "1") == "0":
        return False
    try:
        from .. import automatismi
        return automatismi.attivo("verifica_sconto_alto", True)
    except Exception:
        return True


def n_foto() -> int:
    return max(1, int(_num("VERIFICA_FOTO", 6)))


def foto_min() -> int:
    return max(2, int(_num("VERIFICA_FOTO_MIN", 3)))


def ore_min() -> float:
    return max(0.0, _num("VERIFICA_ORE_MIN", 48))


def calo_max() -> float:
    return min(0.9, max(0.0, _num("VERIFICA_CALO_MAX", 0.30)))


def per_giro() -> int:
    return max(0, int(_num("VERIFICA_PER_GIRO", 15)))


def max_giorno() -> int:
    return max(0, int(_num("VERIFICA_MAX_GIORNO", 300)))


def modello() -> str:
    return _env("VERIFICA_MODELLO", "") or AI.MODEL_DEEP


# ------------------------------------------------------------------ perché non è confermata (specchio del sito)
def motivi_non_confermata(it: dict, g) -> list[str]:
    """TUTTI i motivi per cui sito_regole.sconto_confermato_ai dice no ([] = confermata). È lo specchio di quella
    funzione, che resta l'unica a decidere sul sito: l'uguaglianza ([] ⇔ True) è controllata dai test su una griglia
    di valori. Qui in più si distingue la zona grigia dal segnale netto."""
    r = it.get("_r") or {}
    j = r.get("jev") or {}
    if not g.sconto_ai:
        return ["sconto_ai_spento"]
    if j.get("fonte") != "claude":
        return ["senza_controllo_claude"]
    if j.get("danni") is None:
        return ["foto_non_viste"]
    if j.get("stesso_prezzo") is False:
        return ["prezzo_cambiato"]
    out = []
    t, av = j.get("truffa"), j.get("auto_vera")
    if t is None:
        out.append("truffa_mancante")
    elif float(t) >= g.ai_pulita:
        out.append("truffa_zona_grigia" if float(t) < g.truffa_avviso else "truffa_alta")
    if av is None:
        out.append("auto_vera_mancante")
    elif float(av) < g.sconto_ai_vera:
        out.append("auto_vera_zona_grigia" if float(av) >= AUTO_VERA_GRIGIA_MIN else "auto_vera_bassa")
    if j.get("dati_coerenti") is None or float(j["dati_coerenti"]) < 0.5:
        out.append("dati_non_coerenti")
    if j.get("foto_internet") is None:
        out.append("foto_internet_mancante")
    elif float(j["foto_internet"]) >= 0.5:
        out.append("foto_internet")
    if float(j["danni"]) >= g.jev_danni:
        out.append("danni_nelle_foto")
    if j.get("decisione") == "scarta":
        out.append("decisione_scarta")
    if (r.get("perizia") or {}).get("giudizio") in ("caro", "da verificare"):
        out.append("perizia")
    return out


def non_idonea(it: dict, g) -> str | None:
    """None = l'auto può avere la seconda verifica; altrimenti il motivo per cui resta fuori SENZA chiamata.
    Solo zona grigia, numero italiano presente, nessun testo di pagamento estero, nessun altro passo AI contrario."""
    from ..web import sito_regole as SR
    m = motivi_non_confermata(it, g)
    if not m:
        return "gia_confermata"
    netti = [x for x in m if x not in ZONA_GRIGIA]
    if netti:
        return "segnale_netto:" + netti[0]
    r = it.get("_r") or {}
    if not r.get("tel"):
        return "senza_numero"
    if r.get("tel_estero"):
        return "numero_estero"
    if r.get("testo_truffa"):
        return "testo_pagamento_estero"
    try:
        x = dict(it)
        if SR.r_jev(x, g) or SR.r_prezzo_intero(x, g):
            return "altro_passo_ai"
    except Exception:
        return "errore"
    return None


def priorita(it: dict) -> tuple:
    """Prima le auto di privati, poi il margine più alto in euro, poi l'id. Solo l'ordine: nessuna regola cambia."""
    from ..web import sito_regole as SR
    return (1 if SR.e_commerciante(it) else 0, -float(SR.margine(it) or 0), it.get("id") or 0)


def prepara(sconto_alto: list, g) -> dict:
    """Da controllo_auto.coda_dal_sito, a ogni elenco: `sconto_alto` = [(auto, motivo per cui non entrerebbe | None,
    già controllata sì/no)] per TUTTE le auto tolte per "sconto_alto_da_controllare". Mette in CODA_SITO["verifica"]
    gli id idonei alla seconda verifica (in ordine di priorità) e in CODA_SITO["verifica_motivi"] il conteggio per
    motivo di tutte (per la riga VERIFICA_SCONTO_ALTO). Solo id e conteggi in memoria."""
    motivi: Counter = Counter()
    idonee = []
    for it, fuori, gia in sconto_alto:
        if fuori:
            motivi["non_entrerebbe:" + str(fuori)] += 1      # margine minimo, tetto commercianti, perizia, chat
            continue
        if not gia:
            motivi["attesa_primo_controllo"] += 1            # mai controllata, senza foto viste o prezzo cambiato
            continue
        try:
            m = non_idonea(it, g)
        except Exception:
            m = "errore"
        if m or not isinstance(it.get("id"), int):
            motivi[m or "errore"] += 1
            continue
        idonee.append(it)
    idonee.sort(key=priorita)
    motivi["idonee_alla_verifica"] = len(idonee)
    CA.CODA_SITO.update(verifica=[it["id"] for it in idonee], verifica_motivi=dict(motivi))
    return dict(motivi)


# ------------------------------------------------------------------ esito della seconda verifica
def pulita(x: dict, g=None) -> list[str]:
    """Motivi per cui la risposta della seconda verifica NON basta ([] = pulita): le soglie del sito (confermabile) e,
    in più, le soglie strette di PULITA. Un valore mancante conta come non pulito."""
    out = []
    if not CA.confermabile(x, g):
        out.append("soglie_del_sito")
    if int(x.get("_foto") or 0) < foto_min():
        out.append("foto_viste_poche")

    def sopra(k, lim, nome):
        if x.get(k) is None or float(x[k]) > lim:
            out.append(nome)

    def sotto(k, lim, nome):
        if x.get(k) is None or float(x[k]) < lim:
            out.append(nome)
    sopra("truffa", PULITA["truffa_max"], "truffa")
    sotto("auto_vera", PULITA["auto_vera_min"], "auto_vera")
    sopra("foto_internet", PULITA["foto_internet_max"], "foto_internet")
    sotto("dati_coerenti", PULITA["dati_coerenti_min"], "dati_coerenti")
    sopra("danni_foto", PULITA["danni_max"], "danni_foto")
    sopra("guasto", PULITA["guasto_max"], "guasto")
    sopra("prezzo_rata", PULITA["rata_max"], "prezzo_rata")
    sotto("km_credibili", PULITA["km_credibili_min"], "km_credibili")
    if x.get("decisione") == "scarta":
        out.append("decisione_scarta")
    return out


def senza_chiamata(a: dict) -> str | None:
    """Dati dell'annuncio che tengono l'auto fuori senza spendere (None = si può chiamare)."""
    if a.get("d_verifica") is not None:
        return "gia_verificata"
    if a.get("d_prezzo") is None or a.get("price_eur") is None or int(a["d_prezzo"]) != int(a["price_eur"]):
        return "prezzo_cambiato"                  # la riprende il primo controllo (prezzo nuovo)
    m = CA.scartata_dal_testo(a)
    if m:
        return "testo:" + m
    if int(a.get("n_foto") or 0) < foto_min():
        return "poche_foto"
    if a.get("ore_online") is None:
        return "storico_mancante"
    if float(a["ore_online"]) < ore_min():
        return "annuncio_troppo_recente"          # torna da sola quando ha l'età minima
    pm = a.get("prezzo_max")
    if pm and float(a["price_eur"]) < (1 - calo_max()) * float(pm):
        return "calo_prezzo_anomalo"
    return None


VERIFICA_SQL = """
SELECT l.id, l.source, l.title, l.description, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel,
       l.gearbox, l.power_kw, l.price_eur, l.province, l.seller_type,
       (SELECT count(*) FROM listing_photos p WHERE p.listing_id = l.id) AS n_foto,
       (SELECT array_agg(source_url ORDER BY position) FROM (SELECT source_url, position FROM listing_photos p
         WHERE p.listing_id = l.id ORDER BY position LIMIT %(nf)s) f) AS foto,
       vp.dati->'confronti' AS confronti, vp.categoria AS cat, vp.margine,
       EXTRACT(EPOCH FROM now() - l.first_seen_at) / 3600.0 AS ore_online,
       (SELECT max(pe.price_eur) FROM price_events pe WHERE pe.listing_id = l.id) AS prezzo_max,
       d.prezzo AS d_prezzo, d.risposta->'_verifica' AS d_verifica
FROM listings l
JOIN LATERAL (SELECT dati, categoria, margine FROM valutazioni_policy v WHERE v.listing_id = l.id
              ORDER BY v.computed_at DESC LIMIT 1) vp ON true
JOIN decisione_ai d ON d.listing_id = l.id AND d.fonte = 'claude'
WHERE l.id = ANY(%(ids)s) AND l.status = 'attivo' AND l.price_eur IS NOT NULL"""


def fatte_oggi(conn) -> int:
    r = conn.execute("SELECT count(*) AS n FROM ai_usage WHERE task = %s AND at > date_trunc('day', now() AT TIME ZONE "
                     "'Europe/Rome') AT TIME ZONE 'Europe/Rome'", (TASK,)).fetchone()
    return int((r or {}).get("n") or 0)


def _chiama(testo: str, foto: list[dict]):
    """(json | eccezione, token_in, token_out, modello): come controllo_auto._chiama_claude, con il modello migliore."""
    mod = modello()
    resp = AI.crea(model=mod, max_tokens=int(_num("VERIFICA_MAX_TOKENS", 400)), system=CA.SISTEMA + AGGIUNTA,
                   messages=[{"role": "user", "content": [*foto, {"type": "text", "text": testo}]}])
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    u = resp.usage
    try:
        dati = AI.extract_json(text)
    except Exception as e:
        dati = CA.ControlloNonValido(str(e)[:80])
    return dati, int(u.input_tokens or 0), int(u.output_tokens or 0), mod


def _foto(fonti: list, http=None) -> list[dict]:
    lato = int(_num("VERIFICA_FOTO_LATO", 768))
    out = []
    for src in list(dict.fromkeys(f for f in (fonti or []) if f))[:n_foto()]:
        b = AI.image_block(src, http=http, side=lato)
        if b:
            out.append(b)
    return out


def _segna(conn, lid: int, segno: dict) -> None:
    conn.execute("UPDATE decisione_ai SET risposta = risposta || jsonb_build_object('_verifica', %s::jsonb) "
                 "WHERE listing_id = %s AND fonte = 'claude'", (json.dumps(segno), lid))


def giro(conn, chiama=None, foto=None, g=None) -> dict:
    """Un lotto della seconda verifica (dopo il giro del controllo, stesso thread). `chiama(testo, foto)` e
    `foto(fonti)` si sostituiscono nei test. Ritorna {} se spenta o senza auto; altrimenti chiavi verifica_*."""
    if not attiva() or (chiama is None and not CA.attiva()):
        return {}
    sito = CA.coda_sito_fresca()
    ids = list((sito or {}).get("verifica") or [])
    if not ids:
        # nessuna auto idonea: resta utile sapere PERCHÉ le auto con sconto alto sono fuori (una riga ogni 30 minuti)
        elenco = dict((sito or {}).get("verifica_motivi") or {})
        if elenco and time.time() - STATO["t"] > 1800:
            STATO["t"] = time.time()
            log.warning("VERIFICA_SCONTO_ALTO %s", json.dumps({"idonee": 0, "controllate": 0, "passate": 0, "fuori": 0,
                                                                "elenco_sconto_alto": elenco}, ensure_ascii=False))
        return {}
    ora = time.time()
    if ora < CA._PAUSA["fino"]:
        return {"verifica_saltata": "pausa per credito/limite AI"}
    from ..web import sito_regole as SR
    g = g or SR.Regole.da_env()
    t0 = time.time()
    st: Counter = Counter()
    fuori: Counter = Counter()          # non passate dopo la chiamata, per motivo (il primo)
    senza: Counter = Counter()          # restano fuori senza chiamata, per motivo
    for k in [k for k, t in _ATTESA.items() if t <= ora]:
        _ATTESA.pop(k, None)
    in_attesa = [i for i in ids if i in _ATTESA]
    if in_attesa:
        senza["riprova_tra_6_ore"] = len(in_attesa)
    leggere = [i for i in ids if i not in _ATTESA][:500]
    righe = {r["id"]: dict(r) for r in conn.execute(VERIFICA_SQL, {"ids": leggere, "nf": n_foto()}).fetchall()}
    fatte_g = fatte_oggi(conn)
    posto = max(0, min(per_giro(), max_giorno() - fatte_g))
    da_fare = []
    for i in leggere:                                  # nell'ordine di priorità dell'elenco
        a = righe.get(i)
        if not a:
            senza["annuncio_non_letto"] += 1
            continue
        m = senza_chiamata(a)
        if m:
            senza[m] += 1
            continue
        if len(da_fare) < posto:
            da_fare.append(a)
        else:
            st["restanti"] += 1
    perizie = CA._perizie(conn, [a["id"] for a in da_fare]) if da_fare else {}
    http = None
    if da_fare and foto is None:
        try:
            import httpx
            from ..web.foto import UA
            http = httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": UA})
        except Exception:
            http = None
    chiama = chiama or _chiama
    usd = 0.0
    try:
        for a in da_fare:
            lid = a["id"]
            fonti = list(a.get("foto") or [])
            blocchi = foto(fonti) if foto is not None else _foto(fonti, http)
            if len(blocchi) < foto_min():
                _ATTESA[lid] = time.time() + 6 * 3600          # nessuna chiamata, nessuna spesa
                senza["foto_non_scaricabili"] += 1
                continue
            conf = a.get("confronti") or []
            if isinstance(conf, str):
                conf = json.loads(conf)
            n_mandate = len(blocchi)
            testo = CA.testo_richiesta(a, conf, perizie.get(lid), foto=n_mandate)
            try:
                dati, tin, tout, mod = chiama(testo, blocchi)
            except Exception as e:
                tipo = CA._tipo_errore(e)
                if tipo:
                    from . import perizia as PER
                    CA._PAUSA["fino"] = time.time() + PER.pausa_per(e)
                    st["pausa_" + tipo] += 1
                    st["restanti"] += len(da_fare) - da_fare.index(a)
                    break
                _ATTESA[lid] = time.time() + 6 * 3600
                st["errore"] += 1
                if st["errore"] <= 2:
                    log.warning("VERIFICA_SCONTO_ALTO %s non riuscita: %s", lid, str(e)[:160])
                continue
            finally:
                blocchi = None                                 # la memoria delle foto si libera subito
            # la spesa si registra e si conferma SUBITO dopo la chiamata (anche se poi la risposta non vale)
            usd += AI.costo(mod, tin, tout)
            try:
                CA._registra_spesa(conn, lid, [(tin, tout, mod)], TASK)
                conn.commit()
            except Exception as e:
                log.warning("VERIFICA_SCONTO_ALTO spesa non registrata: %s", str(e)[:120])
            st["controllate"] += 1
            segno = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "modello": mod, "foto": n_mandate}
            try:
                if isinstance(dati, Exception):
                    raise CA.ControlloNonValido("risposta non leggibile")
                x = CA.valida(dati, con_foto=True, con_confronti=bool(conf))
            except CA.ControlloNonValido:
                fuori["risposta_non_valida"] += 1              # pagata: non si richiede (resta la decisione di prima)
                _segna(conn, lid, dict(segno, esito="non_valida"))
                conn.commit()
                continue
            x["_foto"], x["_modello"] = n_mandate, mod
            no = pulita(x, g)
            if no:
                fuori[no[0]] += 1
                _segna(conn, lid, dict(segno, esito="non_pulita", motivi=no[:6]))
                conn.commit()
                continue
            # pulita: diventa la decisione dell'auto (il sito la conferma con le sue regole di sempre). Impronta come
            # quella del primo controllo (prime foto): un piccolo cambio di prezzo la riusa, come ogni decisione
            imp = CA.impronta(a, fonti[:CA.n_foto()])
            CA.salva(conn, a, x, imp, tin)
            _segna(conn, lid, dict(segno, esito="ok"))
            conn.commit()
            st["passate"] += 1
    finally:
        if http is not None:
            http.close()
        try:
            conn.commit()
        except Exception:
            pass
    elenco = dict((sito or {}).get("verifica_motivi") or {})
    riga = {"idonee": len(ids), "controllate": st["controllate"], "passate": st["passate"],
            "fuori": st["controllate"] - st["passate"], "fuori_per_motivo": dict(fuori),
            "senza_chiamata": dict(senza), "restanti": st["restanti"], "errori": st["errore"],
            "costo_usd": round(usd, 4), "usd_auto": round(usd / max(1, st["controllate"]), 4),
            "fatte_oggi": fatte_g + st["controllate"], "max_giorno": max_giorno(), "modello": modello(),
            "foto": f"fino a {n_foto()} (minimo {foto_min()})", "secondi": round(time.time() - t0),
            "elenco_sconto_alto": elenco, **{k: v for k, v in st.items() if k.startswith("pausa_")}}
    # una riga per ogni lotto che ha fatto qualcosa; a vuoto al massimo una ogni 30 minuti
    if st["controllate"] or time.time() - STATO["t"] > 1800:
        STATO["t"] = time.time()
        log.warning("VERIFICA_SCONTO_ALTO %s", json.dumps(riga, ensure_ascii=False))
    STATO["ultimo"] = riga
    out = {"verifica_fatte": st["controllate"], "verifica_ok": st["passate"], "verifica_usd": round(usd, 4)}
    if st["restanti"] and st["controllate"] and not any(k.startswith("pausa_") for k in st):
        out["verifica_restanti"] = st["restanti"]      # solo se il lotto ha concluso qualcosa: mai giri a vuoto
    out.update({"verifica_" + k: v for k, v in st.items() if k.startswith("pausa_")})
    return out
