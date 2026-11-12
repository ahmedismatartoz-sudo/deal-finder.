"""12/11 (cliente: "sul sito l'auto deve essere più completa"): lettura della PAGINA dell'annuncio per la scheda tecnica
(optional, cilindrata, carrozzeria, porte, posti, colore, proprietari, tagliandi, classe Euro, consumo, trazione...).

Solo per le auto che contano: prima quelle sul sito (sito_istantanea), poi le candidate (stage 'approfondito', ancora
attive). Gira nel cron opportunità (mai nel processo web: il sito è già al limite di memoria), a ritmo basso, con un
tetto per giro, un tempo massimo e un tetto giornaliero (tabella scheda_letture). Una pagina già letta non si rilegge
per RILEGGI_GIORNI giorni; una pagina che dà errore si riprova al massimo TENTATIVI volte.

Mai dati del venditore: si salvano solo i dati del veicolo (dealfinder/core/dati_auto.py). Lo stato dell'annuncio
(attivo/sparito) NON si cambia qui: lo decidono i controlli di sempre (verify, tempo reale).

Le fonti si alternano (Subito non può occupare tutto il giro); Subito solo per le auto senza dati dalla ricerca; una
fonte bloccata (403/429 di fila) si lascia stare per ORE_BLOCCO ore (tabella scheda_fonti). Gira a FINE giro del cron
opportunità, solo se il giro dura da meno di 5 ore (collect.py, CRON_ORE_SCHEDE_PAGINE).

Interruttori (default nel codice, variabili facoltative): SCHEDA_PAGINE=0 spegne tutto; SCHEDA_PAGINE_SUBITO=0 solo
Subito; SCHEDA_PAGINE_GIRO, SCHEDA_PAGINE_GIORNO, SCHEDA_PAGINE_TEMPO_S.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from collections import Counter
from datetime import datetime, timezone

from ..core import dati_auto

log = logging.getLogger("schede_auto")

PER_GIRO = 150            # pagine al massimo per giro del cron (il cron opportunità parte ogni 3 ore)
PER_GIORNO = 600          # pagine al massimo al giorno (sito ~600 auto: in 1-2 giorni sono tutte lette)
TEMPO_S = 1200            # 20 minuti al massimo per giro
PAUSA = (3.0, 6.0)        # secondi tra una pagina e l'altra (ritmo di una persona che sfoglia)
RILEGGI_GIORNI = 30
TENTATIVI = 3
STOP_DOPO_BLOCCHI = 2     # 403/429 di fila sulla stessa fonte: quella fonte si ferma per il giro
CANDIDATE_GIORNI = 5

ORE_BLOCCO = 6           # dopo un blocco (403/429 di fila) la fonte si lascia stare per 6 ore

# Le fonti si ALTERNANO (ROW_NUMBER per fonte): Subito non può riempire tutto il giro e lasciare fuori AutoScout24.
# Subito solo se la ricerca non ha già dato la scheda (la ricerca di Subito ha quasi tutto; la pagina da Render è spesso
# bloccata). Dentro ogni fonte: prima le auto sul sito, poi le candidate più recenti.
DA_LEGGERE_SQL = """
WITH cand AS (
  SELECT l.id, l.source, l.url, l.deep_at, (s.listing_id IS NOT NULL) AS sul_sito
  FROM listings l
  LEFT JOIN sito_istantanea s ON s.listing_id = l.id
  WHERE l.source IN ('autoscout24', 'subito') AND l.status = 'attivo' AND l.url LIKE 'http%%'
    AND l.source NOT IN (SELECT f.fonte FROM scheda_fonti f
                         WHERE f.bloccato_il > now() - make_interval(hours => %(ore_blocco)s))
    AND (l.source <> 'subito' OR (%(subito)s AND COALESCE(l.dati_auto->'ricerca', '{}'::jsonb) = '{}'::jsonb))
    AND (s.listing_id IS NOT NULL
         OR (l.stage = 'approfondito' AND l.stage_reason IN ('opportunita', 'da_verificare', 'incidentata')
             AND GREATEST(l.last_checked_at, l.last_seen_at) > now() - make_interval(days => %(giorni_cand)s)))
    AND (l.dati_auto IS NULL OR l.dati_auto->>'letto_il' IS NULL
         OR (l.dati_auto->>'letto_il')::timestamptz < now() - make_interval(days => %(rileggi)s))
    AND COALESCE((l.dati_auto->>'tentativi_pagina')::int, 0) < %(tentativi)s
), ord AS (
  SELECT c.*, ROW_NUMBER() OVER (PARTITION BY c.source ORDER BY c.sul_sito DESC, c.deep_at DESC NULLS LAST, c.id DESC) AS rn
  FROM cand c
)
SELECT id, source, url, sul_sito FROM ord ORDER BY rn, source LIMIT %(n)s
"""


def _int_env(nome: str, default: int) -> int:
    try:
        return int(os.environ.get(nome, "") or default)
    except ValueError:
        return default


def lette_oggi(conn) -> int:
    r = conn.execute("SELECT lette FROM scheda_letture WHERE giorno = current_date").fetchone()
    return int(r["lette"]) if r else 0


def _conta(conn, lette: int, errori: int) -> None:
    conn.execute("INSERT INTO scheda_letture (giorno, lette, errori) VALUES (current_date, %s, %s) "
                 "ON CONFLICT (giorno) DO UPDATE SET lette = scheda_letture.lette + EXCLUDED.lette, "
                 "errori = scheda_letture.errori + EXCLUDED.errori, aggiornato = now()", (lette, errori))


def da_leggere(conn, n: int) -> list[dict]:
    return [dict(r) for r in conn.execute(DA_LEGGERE_SQL, {
        "n": n, "giorni_cand": CANDIDATE_GIORNI, "rileggi": RILEGGI_GIORNI, "tentativi": TENTATIVI,
        "ore_blocco": ORE_BLOCCO, "subito": os.environ.get("SCHEDA_PAGINE_SUBITO", "1") == "1"}).fetchall()]


def segna_bloccata(conn, fonte: str) -> None:
    conn.execute("INSERT INTO scheda_fonti (fonte, bloccato_il) VALUES (%s, now()) "
                 "ON CONFLICT (fonte) DO UPDATE SET bloccato_il = now()", (fonte,))


# ------------------------------------------------------------------------------------------------ lettura pagine
def leggi_autoscout(client, url: str) -> tuple[str, dict]:
    """('ok'|'non_disponibile'|'bloccato'|'errore', dati). Stessi criteri della lettura del tempo reale (_as_esito):
    404/410 o rimando alla ricerca = non più disponibile."""
    from ..collectors.base import next_data
    try:
        r = client.get(url)
    except Exception:
        return "errore", {}
    code = getattr(r, "status_code", None)
    if code in (404, 410):
        return "non_disponibile", {}
    if code in (403, 429):
        return "bloccato", {}
    if code != 200:
        return "errore", {}
    fin = str(getattr(r, "url", "") or url)
    if "/lst" in fin and "/annunci/" not in fin:
        return "non_disponibile", {}
    try:
        props = ((next_data(getattr(r, "text", "") or "") or {}).get("props") or {}).get("pageProps") or {}
    except Exception:
        props = {}
    det = props.get("listingDetails") or props.get("listing")
    if not isinstance(det, dict):
        return ("non_disponibile", {}) if "non è più disponibile" in (getattr(r, "text", "") or "").lower() else ("errore", {})
    return "ok", dati_auto.da_autoscout_pagina(det)


def leggi_subito(client, url: str) -> tuple[str, dict]:
    from ..collectors.base import find_key, next_data
    from .tempo_reale import _stesso_annuncio
    try:
        r = client.get(url)
    except Exception:
        return "errore", {}
    code = getattr(r, "status_code", None)
    if code in (404, 410):
        return "non_disponibile", {}
    if code in (403, 429):
        return "bloccato", {}
    if code != 200:
        return "errore", {}
    if not _stesso_annuncio(url, str(getattr(r, "url", "") or url)):
        return "non_disponibile", {}
    try:
        data = next_data(getattr(r, "text", "") or "")
    except Exception:
        data = None
    item = find_key(data, "ad", "item") if isinstance(data, dict) else None
    if not isinstance(item, dict):
        return "errore", {}
    return "ok", dati_auto.da_subito(item)


def salva(conn, lid: int, esito: str, dati: dict) -> None:
    """Pagina letta: 'pagina' + 'letto_il'. Errore: solo il contatore dei tentativi (si riprova al giro dopo)."""
    if esito in ("ok", "non_disponibile"):
        parte = {"pagina": dati, "letto_il": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "esito_pagina": esito, "tentativi_pagina": 0}
        conn.execute("UPDATE listings SET dati_auto = COALESCE(dati_auto, '{}'::jsonb) || %s::jsonb WHERE id = %s",
                     (json.dumps(parte, ensure_ascii=False), lid))
    else:
        conn.execute("UPDATE listings SET dati_auto = jsonb_set(COALESCE(dati_auto, '{}'::jsonb), '{tentativi_pagina}', "
                     "to_jsonb(COALESCE((dati_auto->>'tentativi_pagina')::int, 0) + 1)) WHERE id = %s", (lid,))


def giro(conn, per_giro: int | None = None, per_giorno: int | None = None, tempo_s: float | None = None,
         client_as=None, client_sub=None, dormi=time.sleep) -> Counter:
    st: Counter = Counter()
    per_giro = per_giro if per_giro is not None else _int_env("SCHEDA_PAGINE_GIRO", PER_GIRO)
    per_giorno = per_giorno if per_giorno is not None else _int_env("SCHEDA_PAGINE_GIORNO", PER_GIORNO)
    tempo_s = tempo_s if tempo_s is not None else _int_env("SCHEDA_PAGINE_TEMPO_S", TEMPO_S)
    resto = min(per_giro, per_giorno - lette_oggi(conn))
    if resto <= 0:
        st["tetto_giornaliero"] += 1
        return st
    righe = da_leggere(conn, resto)
    conn.commit()
    st["da_leggere"] = len(righe)
    if not righe:
        return st
    da_chiudere = []
    if client_as is None and any(r["source"] == "autoscout24" for r in righe):
        from .tempo_reale import _client_as
        client_as = _client_as()
        da_chiudere.append(client_as)
    if client_sub is None and any(r["source"] == "subito" for r in righe):
        from .tempo_reale import _client_subito
        client_sub = _client_subito()
        da_chiudere.append(client_sub)
    try:
        _leggi_tutte(conn, righe, client_as, client_sub, tempo_s, dormi, st)
    finally:
        for c in da_chiudere:
            try:
                c.close()
            except Exception:
                pass
    return st


def _leggi_tutte(conn, righe, client_as, client_sub, tempo_s, dormi, st: Counter) -> None:
    blocchi: Counter = Counter()
    ferme: set = set()
    t0 = time.time()
    for i, r in enumerate(righe):
        if time.time() - t0 > tempo_s:
            st["tempo_finito"] += 1
            break
        fonte = r["source"]
        if fonte in ferme:
            st[f"{fonte}:saltata_blocco"] += 1
            continue
        if fonte == "autoscout24":
            esito, dati = leggi_autoscout(client_as, r["url"])
        else:
            esito, dati = leggi_subito(client_sub, r["url"])
        if esito == "bloccato":               # un blocco non consuma i tentativi dell'annuncio
            st[f"{fonte}:bloccato"] += 1
            blocchi[fonte] += 1
            if blocchi[fonte] >= STOP_DOPO_BLOCCHI:
                ferme.add(fonte)
                try:
                    segna_bloccata(conn, fonte)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    log.warning("SCHEDA_PAGINE blocco %s non salvato: %s", fonte, str(e)[:150])
                log.warning("SCHEDA_PAGINE %s bloccata: ferma per %d ore", fonte, ORE_BLOCCO)
            continue
        blocchi[fonte] = 0
        try:
            salva(conn, r["id"], esito, dati)
            letta = esito in ("ok", "non_disponibile")
            _conta(conn, 1 if letta else 0, 0 if letta else 1)
            conn.commit()
        except Exception as e:
            conn.rollback()
            st["errore_salvataggio"] += 1
            log.warning("SCHEDA_PAGINE salvataggio %s: %s", r["id"], str(e)[:150])
            continue
        st[f"{fonte}:{esito}"] += 1
        if esito == "ok":
            st["con_optional"] += 1 if dati.get("optional") else 0
            st["campi"] += len(dati)
            st["sul_sito"] += 1 if r.get("sul_sito") else 0
        if i < len(righe) - 1:
            dormi(random.uniform(*PAUSA))


def giornaliero(stats: Counter | None = None) -> Counter:
    """Dal cron opportunità: un giro di letture. Mai un errore verso il giro del cron."""
    if os.environ.get("SCHEDA_PAGINE", "1") != "1":
        return Counter()
    from ..db import connect
    try:
        with connect() as conn:
            st = giro(conn)
        log.warning("SCHEDA_PAGINE %s", json.dumps(dict(st), ensure_ascii=False))
        if stats is not None:
            stats["schede_pagine"] = sum(v for k, v in st.items() if k.endswith(":ok"))
        return st
    except Exception as e:
        log.warning("SCHEDA_PAGINE non riuscito: %s", str(e)[:300])
        return Counter()
