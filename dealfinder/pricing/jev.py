"""AI decisionale (TypeSafe Jev) — il "commerciante esperto" che riguarda le auto migliori dopo i calcoli.

Cosa fa (per ogni auto candidata, una volta sola; di nuovo solo se cambia il prezzo o dopo 7 giorni):
  riceve l'annuncio (testo, numeri, foto contate, telefono sì/no) e i NOSTRI numeri (mercato, rivendita, margine,
  auto uguali usate per il confronto) e risponde con decisioni tipizzate e calibrate:
    - decisione:      compra | tratta | scarta (con probabilità e confidenza)
    - truffa:         probabilità che l'annuncio sia una truffa o abbia un prezzo falso
    - versione_ok:    probabilità che i confronti siano davvero la stessa versione/motore/allestimento
    - dati_coerenti:  probabilità che anno, km, prezzo e descrizione siano coerenti
    - stato:          da riparare … ottimo stato (0–4)
    - correzione:     di quanto correggere la rivendita dei calcoli (−20% … +10%)
Jev NON inventa i prezzi di mercato: ragiona sui nostri dati e corregge entro limiti stretti.
Guarda anche le prime JEV_FOTO foto (Jev 1.14): targa leggibile, danni visibili, foto prese da internet, stato.
Non scrive testi (la spiegazione per il commerciante si compone dalle sue risposte).

Modalità (variabile JEV_MODO):
  prova  (default) → decide e registra (tabella decisione_ai) ma il sito NON cambia; nei log il confronto con i calcoli
  attiva           → le decisioni contano sul sito (da accendere solo dopo aver visto la prova)
Costi: Jev costa ~0,042 $ per milione di token in ingresso (risposte gratis). Tetto giornaliero JEV_MAX_GIORNO chiamate.
La chiave: variabile d'ambiente TYPESAFE_API_KEY (mai nel codice né nei log).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


log = logging.getLogger("jev")

URLS = [u for u in (os.environ.get("TYPESAFE_URL"), "https://api.typesafe.ai/v1/systemone") if u]   # docs.typesafe.ai
N_FOTO = int(os.environ.get("JEV_FOTO", "4"))          # foto per auto mandate a Jev (Jev 1.14 legge le immagini)
MODELLO = os.environ.get("TYPESAFE_MODEL", "jev-latest")
_url_buono = {"u": None}

DECISIONE = {
    "compra": "Affare chiaro per un commerciante: prezzo d'acquisto ben sotto il valore di rivendita, dati coerenti, "
              "rischio basso. Da chiamare subito.",
    "tratta": "Interessante ma non sicuro: margine stretto, qualcosa da verificare (stato, versione, documenti) o "
              "prezzo da trattare prima di comprare.",
    "scarta": "Non conviene o è troppo rischiosa: margine insufficiente o irrealistico, auto con problemi, "
              "confronti sbagliati o possibile truffa.",
}
STATO = ["Da riparare o con guasti seri", "Usata con difetti evidenti", "Normale per età e km",
         "Buono stato, curata", "Ottimo stato, tagliandata e documentata"]
CORREZIONI = [-20, -15, -10, -5, 0, 5, 10]
CORREZIONE_LIVELLI = ["Molto più bassa (−20%)", "Più bassa (−15%)", "Un po' più bassa (−10%)",
                      "Leggermente più bassa (−5%)", "Corretta così (0%)", "Leggermente più alta (+5%)",
                      "Più alta (+10%)"]

ISTRUZIONI = ("Sei un commerciante d'auto usate esperto in Lombardia. Compri auto da privati per rivenderle. "
              "Valuta l'annuncio usando i dati di mercato forniti (auto uguali in vendita ora, già corrette per anno e km): "
              "non inventare prezzi, ragiona su quelli. Considera stato, versione, km, credibilità del venditore.")


def attiva() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def _testo(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:n]


def stato_auto(r: dict) -> dict:
    """Lo "stato" mandato a Jev: annuncio + i nostri numeri. Niente dati personali del venditore (niente numeri, nomi)."""
    from ..web import scovo
    d = r.get("dati") or {}
    if isinstance(d, str):
        d = json.loads(d)
    testo = f"{r.get('title') or ''} {r.get('description') or ''}"
    tel = bool(r.get("telefono_ok")) or bool(scovo.RE_MOBILE.search(testo) or scovo.RE_LANDLINE.search(testo))
    descr = scovo.RE_MOBILE.sub("[numero]", _testo(r.get("description"), 1800))     # il numero non serve a Jev
    confronti = [{"prezzo": c.get("prezzo"), "prezzo_corretto": c.get("prezzo_corretto"), "anno": c.get("anno"),
                  "km": c.get("km"), "kw": c.get("kw"), "cambio": c.get("cambio"), "versione": c.get("versione"),
                  "venditore": c.get("venditore")} for c in (d.get("confronti") or [])[:10]]
    return {
        "annuncio": {
            "titolo": _testo(r.get("title"), 160), "descrizione": descr,
            "marca": r.get("make"), "modello": r.get("model"), "versione": r.get("version_raw"),
            "anno": r.get("year"), "km": r.get("mileage_km"), "alimentazione": r.get("fuel"), "cambio": r.get("gearbox"),
            "potenza_kw": r.get("power_kw"), "prezzo_richiesto_euro": r.get("price_eur"),
            "sito": r.get("source"), "venditore": r.get("seller_type") or "privato", "provincia": r.get("province"),
            "numero_di_telefono_presente": tel, "foto": int(r.get("n_foto") or 0),
        },
        "calcoli_scovo": {
            "valore_di_mercato_mediana": d.get("M"), "quartile_basso": d.get("L"),
            "auto_uguali_confrontate": d.get("n"), "metodo": d.get("metodo"),
            "prezzo_medio_commercianti": d.get("M_commercianti"), "prezzo_medio_privati": d.get("M_privati"),
            "rivendita_stimata": d.get("R_finale") or d.get("R"), "margine_stimato": d.get("margine"),
            "margine_minimo_richiesto": d.get("soglia"), "categoria": r.get("cat"),
            "note": (d.get("motivi") or [])[:4] + (d.get("ipotesi") or [])[:3],
        },
        "confronti": confronti,
    }


DOMANDE = {
    "decisione": {"type": "choice", "instructions": ISTRUZIONI + " Un commerciante dovrebbe comprare questa auto?",
                  "criteria": DECISIONE},
    "truffa": {"type": "noul", "instructions": "L'annuncio ha segnali di truffa o di prezzo non vero (prezzo irrealistico, "
               "auto all'estero, richiesta di anticipo o caparra, testo generico o copiato, dati incoerenti)?"},
    "versione_ok": {"type": "noul", "instructions": "Le auto usate per il confronto sono davvero la stessa auto "
                    "(stesso modello, motore, versione e allestimento simili) di quella in vendita?"},
    "dati_coerenti": {"type": "noul", "instructions": "Anno, chilometri, prezzo e descrizione dell'annuncio sono "
                      "coerenti e credibili tra loro?"},
    "stato": {"type": "score", "instructions": "In che stato è l'auto secondo l'annuncio?", "criteria": STATO},
    "correzione": {"type": "score", "instructions": ISTRUZIONI + " Rispetto alla 'rivendita_stimata' dei calcoli, "
                   "a quanto si rivende realisticamente questa auto considerando stato, versione, km e annuncio?",
                   "criteria": CORREZIONE_LIVELLI},
}


def domande_foto(chiavi: list[str]) -> dict:
    rif = " ".join(f"`{k}`" for k in chiavi)
    return {
        "targa_visibile": {"type": "noul", "instructions": f"In almeno una di queste foto {rif} la targa dell'auto è "
                           "leggibile (caratteri visibili, non coperta, non sfocata, non cancellata)?"},
        "danni_foto": {"type": "noul", "instructions": f"Nelle foto {rif} si vedono danni evidenti (carrozzeria rotta o "
                       "ammaccata, vetri rotti, airbag esplosi, ruggine forte, auto incidentata)?"},
        "foto_internet": {"type": "noul", "instructions": f"Le foto {rif} sembrano prese da internet o da un catalogo "
                          "(foto professionali di concessionario, sfondi da studio, watermark di altri siti) invece che "
                          "scattate dal venditore?"},
    }


def foto_base64(conn, listing_id: int, client) -> dict:
    """Le prime N_FOTO foto dell'annuncio, ridimensionate (lato max 1024 px) e in base64 per Jev."""
    import base64
    import io
    from PIL import Image
    out = {}
    rows = conn.execute("SELECT source_url FROM listing_photos WHERE listing_id=%s ORDER BY position LIMIT %s",
                        (listing_id, N_FOTO)).fetchall() if N_FOTO > 0 else []
    for i, r in enumerate(rows):
        try:
            from ..web.foto import download        # sa trasformare gli "imgid:" di Subito nel link vero
            dati = download(r["source_url"], client)
            if not dati:
                continue
            im = Image.open(io.BytesIO(dati)).convert("RGB")
            im.thumbnail((1024, 1024))
            if min(im.size) < 280:
                continue                                   # Jev vuole almeno 280 px per lato
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            out[f"foto{i + 1}"] = {"type": "image", "format": "jpg", "data_base64": base64.b64encode(buf.getvalue()).decode()}
        except Exception:
            continue
    return out


def chiedi(client, stato: dict, assets: dict | None = None) -> dict:
    """Una chiamata a Jev. Con le foto serve l'intestazione TypeSafe-Danger-Zone-Images; se il servizio di riserva
    rifiuta le immagini si riprova senza."""
    domande = dict(DOMANDE)
    headers = {}
    if assets:
        stato = dict(stato, foto=" ".join(f"`{k}`" for k in assets))
        domande.update(domande_foto(list(assets)))
        headers["TypeSafe-Danger-Zone-Images"] = "true"
    body = {"model": MODELLO, "state": stato, "questions": domande}
    if assets:
        body["assets"] = assets
    urls = [_url_buono["u"]] if _url_buono["u"] else URLS
    ultimo = None
    for u in urls:
        r = client.post(u, json=body, headers=headers)
        if r.status_code in (400, 422) and assets and "asset" in r.text.lower():
            return chiedi(client, {k: v for k, v in stato.items() if k != "foto"}, None)
        if r.status_code in (404, 405):
            ultimo = f"{u} -> HTTP {r.status_code}"
            continue
        if r.status_code == 429:
            time.sleep(3)
            r = client.post(u, json=body, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"Jev HTTP {r.status_code}: {r.text[:200]}")
        _url_buono["u"] = u
        return r.json()
    raise RuntimeError(f"Jev: nessun indirizzo valido ({ultimo})")


def _score_correzione(a: dict) -> float | None:
    if not a or a.get("score") is None:
        return None
    s = float(a["score"])
    lo = int(s)
    hi = min(lo + 1, len(CORREZIONI) - 1)
    return round(CORREZIONI[lo] + (CORREZIONI[hi] - CORREZIONI[lo]) * (s - lo), 1)


def leggi(risposta: dict) -> dict:
    a = risposta.get("answers") or {}
    dec = a.get("decisione") or {}
    return {"decisione": dec.get("choice") or "tratta", "confidenza": dec.get("confidence"),
            "p_compra": (dec.get("probabilities") or {}).get("compra"),
            "truffa": (a.get("truffa") or {}).get("noul"), "versione_ok": (a.get("versione_ok") or {}).get("noul"),
            "dati_coerenti": (a.get("dati_coerenti") or {}).get("noul"), "stato": (a.get("stato") or {}).get("score"),
            "correzione_pct": _score_correzione(a.get("correzione")),
            "targa_visibile": (a.get("targa_visibile") or {}).get("noul"),
            "danni_foto": (a.get("danni_foto") or {}).get("noul"),
            "foto_internet": (a.get("foto_internet") or {}).get("noul"),
            "token": ((risposta.get("usage") or {}).get("input_tokens") or 0)}


CAND_SQL = """
WITH cand AS (
  SELECT ta.listing_id AS id FROM top_auto ta
  UNION
  SELECT vp.listing_id FROM valutazioni_policy vp
  WHERE vp.computed_at > now() - interval '12 hours' AND vp.margine IS NOT NULL
    AND (vp.margine >= %(quota)s * COALESCE(NULLIF(vp.dati->>'soglia', '')::int, 1500)
         OR (vp.categoria = 'dati_insufficienti' AND vp.dati::text LIKE '%%sotto la mediana%%'))
)
SELECT l.id, l.source, l.title, l.description, l.make, l.model, l.version_raw, l.year, l.mileage_km, l.fuel, l.gearbox,
       l.power_kw, l.price_eur, l.province, l.seller_type, l.telefono_ok,
       (SELECT count(*) FROM listing_photos p WHERE p.listing_id = l.id) AS n_foto,
       vp.dati, vp.categoria AS cat, vp.margine
FROM cand c
JOIN listings l ON l.id = c.id AND l.status = 'attivo' AND l.price_eur IS NOT NULL
JOIN LATERAL (SELECT dati, categoria, margine FROM valutazioni_policy v WHERE v.listing_id = l.id
              ORDER BY v.computed_at DESC LIMIT 1) vp ON true
LEFT JOIN decisione_ai d ON d.listing_id = l.id
WHERE d.listing_id IS NULL OR d.prezzo <> l.price_eur OR d.computed_at < now() - interval '7 days'
   OR (d.risposta->'targa_visibile' IS NULL AND d.computed_at < now() - interval '1 hour'
       AND EXISTS (SELECT 1 FROM listing_photos p WHERE p.listing_id = l.id))      -- rifatte con le foto
LIMIT 3000"""


def giro(conn, massimo: int | None = None) -> dict:
    """Un giro dell'AI decisionale sulle auto migliori non ancora decise."""
    if not attiva():
        return {"spenta": "manca TYPESAFE_API_KEY"}
    t0 = time.time()
    fatte_oggi = conn.execute("SELECT count(*) AS n FROM decisione_ai WHERE computed_at > date_trunc('day', now())").fetchone()["n"]
    budget = int(os.environ.get("JEV_MAX_GIORNO", "4000")) - fatte_oggi
    if budget <= 0:
        return {"saltato": "tetto giornaliero raggiunto", "fatte_oggi": fatte_oggi}
    righe = conn.execute(CAND_SQL, {"quota": float(os.environ.get("JEV_QUOTA_SOGLIA", "0.7"))}).fetchall()
    righe.sort(key=lambda r: -((r["margine"] or 0) / max(r["price_eur"] or 1, 1)))   # prima le più promettenti
    from ..web import scovo

    def _tel(r):
        t = f"{r.get('title') or ''} {r.get('description') or ''}"
        return bool(r.get("telefono_ok")) or bool(scovo.RE_MOBILE.search(t) or scovo.RE_LANDLINE.search(t))
    # le auto "troppo sotto prezzo" si guardano solo se hanno il numero (la regola le ammette con targa + numero)
    righe = [r for r in righe if r["cat"] != "dati_insufficienti" or _tel(r)]
    righe = righe[:min(budget, massimo or int(os.environ.get("JEV_PER_GIRO", "300")))]
    st, token = Counter(), 0
    nuove_targhe: list[int] = []
    import httpx
    client = httpx.Client(timeout=40, headers={"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}",
                                                "Content-Type": "application/json"})

    foto_client = httpx.Client(timeout=20, follow_redirects=True,
                               headers={"User-Agent": "Mozilla/5.0 (compatible; Scovo/1.0)"})
    tutte_foto = {}
    for r in righe:                                        # le foto si scaricano prima (la connessione DB è una sola)
        tutte_foto[r["id"]] = foto_base64(conn, r["id"], foto_client)
    foto_client.close()

    def uno(r):
        try:
            return r, leggi_e_tieni(chiedi(client, stato_auto(r), tutte_foto.get(r["id"])))
        except Exception as e:
            return r, e

    def leggi_e_tieni(risp):
        x = leggi(risp)
        x["_risposta"] = risp.get("answers")
        return x

    with ThreadPoolExecutor(max_workers=int(os.environ.get("JEV_PARALLELI", "4"))) as ex:
        for r, x in ex.map(uno, righe):
            if isinstance(x, Exception):
                st["errore"] += 1
                if st["errore"] <= 3:
                    log.warning("JEV errore: %s", str(x)[:250])
                if st["errore"] >= 10 and not st["fatte"]:
                    break                          # chiave o indirizzo sbagliati: inutile insistere
                continue
            st["fatte"] += 1
            st[f"jev:{x['decisione']}"] += 1
            st[f"calcoli:{r['cat']}|jev:{x['decisione']}"] += 1
            if (x.get("truffa") or 0) >= .7:
                st["truffa_probabile"] += 1
            if (x.get("versione_ok") or 1) < .4:
                st["confronti_sbagliati"] += 1
            if x.get("targa_visibile") is not None:
                st["con_foto"] += 1
                esito = ("foto_internet" if (x.get("foto_internet") or 0) >= .7 else
                         "visibile" if x["targa_visibile"] >= .7 else "coperta" if x["targa_visibile"] <= .3 else "incerta")
                st[f"targa:{esito}"] += 1
                if esito == "visibile" and r["cat"] == "dati_insufficienti":
                    nuove_targhe.append(r["id"])
                if (x.get("danni_foto") or 0) >= .7:
                    st["danni_nelle_foto"] += 1
                conn.execute("INSERT INTO controllo_targa (listing_id, esito, foto) VALUES (%s,%s,%s) "
                             "ON CONFLICT (listing_id) DO UPDATE SET esito=EXCLUDED.esito, foto=EXCLUDED.foto, checked_at=now()",
                             (r["id"], esito, len(tutte_foto.get(r["id"]) or {})))
            token += x.get("token") or 0
            conn.execute(
                """INSERT INTO decisione_ai (listing_id, prezzo, decisione, confidenza, p_compra, truffa, versione_ok,
                     dati_coerenti, stato, correzione_pct, categoria_calcoli, margine_calcoli, risposta, token, computed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                   ON CONFLICT (listing_id) DO UPDATE SET prezzo=EXCLUDED.prezzo, decisione=EXCLUDED.decisione,
                     confidenza=EXCLUDED.confidenza, p_compra=EXCLUDED.p_compra, truffa=EXCLUDED.truffa,
                     versione_ok=EXCLUDED.versione_ok, dati_coerenti=EXCLUDED.dati_coerenti, stato=EXCLUDED.stato,
                     correzione_pct=EXCLUDED.correzione_pct, categoria_calcoli=EXCLUDED.categoria_calcoli,
                     margine_calcoli=EXCLUDED.margine_calcoli, risposta=EXCLUDED.risposta, token=EXCLUDED.token,
                     computed_at=now()""",
                (r["id"], r["price_eur"], x["decisione"], x["confidenza"], x["p_compra"], x["truffa"], x["versione_ok"],
                 x["dati_coerenti"], x["stato"], x["correzione_pct"], r["cat"], r["margine"],
                 json.dumps(x["_risposta"]), x.get("token")))
            if st["fatte"] % 25 == 0:
                conn.commit()
    conn.commit()
    client.close()
    if nuove_targhe:
        # auto "troppo sotto prezzo" con targa ora vista nelle foto: si rivalutano subito (regola targa + numero)
        try:
            from ..pipeline.tempo_reale import valuta_e_pubblica
            st2 = Counter()
            valuta_e_pubblica(conn, nuove_targhe, st2)
            conn.commit()
            st["rivalutate_con_targa"] = len(nuove_targhe)
            st["pubblicate"] = st2.get("pubblicate", 0)
        except Exception as e:
            conn.rollback()
            log.warning("JEV rivalutazione con targa non riuscita: %s", str(e)[:200])
    res = {"modo": os.environ.get("JEV_MODO", "prova"), "candidate": len(righe), "secondi": round(time.time() - t0),
           "token": token, "costo_usd": round(token * 0.042 / 1e6, 4), "fatte_oggi": fatte_oggi + st["fatte"],
           **dict(st)}
    log.warning("JEV_GIRO %s", json.dumps(res, ensure_ascii=False))
    return res


def riepilogo(conn) -> dict:
    """Confronto AI / calcoli sulle decisioni degli ultimi giorni (per decidere se accendere la modalità attiva)."""
    r = conn.execute("""SELECT count(*) AS n,
          count(*) FILTER (WHERE decisione='compra') AS compra, count(*) FILTER (WHERE decisione='tratta') AS tratta,
          count(*) FILTER (WHERE decisione='scarta') AS scarta, count(*) FILTER (WHERE truffa >= .7) AS truffe,
          count(*) FILTER (WHERE versione_ok < .4) AS confronti_sbagliati,
          avg(correzione_pct) AS correzione_media, sum(token) AS token
        FROM decisione_ai WHERE computed_at > now() - interval '3 days'""").fetchone()
    return {k: (round(float(v), 2) if isinstance(v, float) else v) for k, v in dict(r).items()}
