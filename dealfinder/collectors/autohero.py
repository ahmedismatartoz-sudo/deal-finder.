"""12/11 (cliente): prezzi di Autohero Italia come riferimento di rivendita.

Autohero vende auto ricondizionate a prezzo fisso, con 1 anno di garanzia (passaggio, gestione e consegna esclusi).
Qui si leggono solo le pagine di ricerca pubbliche (consentite dal robots.txt del sito), a ritmo basso, e si salvano in
una tabella separata (autohero_prezzi): non entrano negli annunci né nel calcolo del sito finché non lo decide il
cliente. Nota del cliente informato: le condizioni d'uso di Autohero chiedono un consenso scritto per usi oltre quello
personale.

La struttura della pagina non è documentata: il lettore prova prima i dati JSON incorporati e, se non ci sono, legge le
schede auto dal testo (link /it/<marca-modello>/id/<uuid>/ con prezzo, anno, km, carburante e cambio vicini). Il primo
giro scrive nei log AUTOHERO_STRUTTURA per poter sistemare il lettore se il sito cambia.
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import random
import re
import time
from collections import Counter

log = logging.getLogger("autohero")

BASE = "https://www.autohero.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
RE_LINK = re.compile(r'href="(/it/([a-z0-9-]+)/id/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?)"', re.I)
RE_EURO = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{3,6})\s*€(?!\s*/\s*mese)")
RE_RATA = re.compile(r"(\d{1,3}(?:\.\d{3})*)\s*€\s*/\s*mese", re.I)
RE_ANNO = re.compile(r"•\s*((?:19|20)\d{2})\b")
RE_KM = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{1,6})\s*km\b", re.I)
CARBURANTI = ("Ibrido", "Benzina", "Diesel", "Elettrico", "GPL", "Metano", "Plug-in")
CAMBI = ("Manuale", "Automatico")


def _num(s) -> int | None:
    try:
        return int(str(s).replace(".", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _testo(frammento: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", frammento, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " • ", t)
    t = _html.unescape(t)
    return re.sub(r"(\s*•\s*)+", " • ", re.sub(r"\s+", " ", t)).strip(" •")


def schede_da_html(pagina: str) -> list[dict]:
    """Schede auto lette dal testo vicino a ogni link /id/<uuid>/."""
    link = list(RE_LINK.finditer(pagina))
    visti, out = set(), []
    for i, m in enumerate(link):
        uid = m.group(3).lower()
        if uid in visti:
            continue
        visti.add(uid)
        fine = len(pagina)
        for n in link[i + 1:]:
            if n.group(3).lower() != uid:
                fine = n.start()
                break
        inizio = m.start()
        t = _testo(pagina[inizio:fine][:12000])
        # il pezzo della scheda: dal testo dopo l'ultimo link diverso precedente
        prezzi = [_num(x) for x in RE_EURO.findall(t)]
        prezzi = [p for p in prezzi if p and 500 <= p <= 400000]
        anno = RE_ANNO.search(t)
        kms = [_num(x) for x in RE_KM.findall(t)]
        kms = [k for k in kms if k is not None and k < 1_000_000]
        rata = RE_RATA.search(t)
        out.append({
            "uuid": uid, "url": BASE + m.group(1), "slug": m.group(2),
            "testo": t[-600:],
            "prezzo": min(prezzi) if prezzi else None,                 # col prezzo barrato il più basso è quello vero
            "prezzo_barrato": max(prezzi) if len(prezzi) > 1 and max(prezzi) != min(prezzi) else None,
            "rata": _num(rata.group(1)) if rata else None,
            "anno": int(anno.group(1)) if anno else None,
            "km": kms[-1] if kms else None,
            "carburante": next((c for c in CARBURANTI if re.search(r"\b" + c + r"\b", t)), None),
            "cambio": next((c for c in CAMBI if re.search(r"\b" + c + r"\b", t)), None),
        })
    return out


def _cerca_json(pagina: str) -> list[dict]:
    """Auto nei dati JSON incorporati (Next.js, ld+json o stato iniziale), se presenti."""
    blocchi = re.findall(r'<script[^>]*(?:id="__NEXT_DATA__"|type="application/(?:ld\+)?json")[^>]*>(.*?)</script>',
                         pagina, re.S | re.I)
    trovate = []

    def visita(x):
        if isinstance(x, dict):
            chiavi = {k.lower() for k in x}
            if ("price" in chiavi or "offers" in chiavi) and ("mileage" in chiavi or "mileagefromodometer" in chiavi
                                                              or "builtyear" in chiavi or "vehiclemodeldate" in chiavi):
                trovate.append(x)
            for v in x.values():
                visita(v)
        elif isinstance(x, list):
            for v in x:
                visita(v)
    for b in blocchi:
        try:
            visita(json.loads(b))
        except ValueError:
            continue
    return trovate


def struttura(pagina: str) -> dict:
    return {"lunghezza": len(pagina), "link_auto": len(set(m.group(3) for m in RE_LINK.finditer(pagina))),
            "next_data": "__NEXT_DATA__" in pagina, "ld_json": pagina.count("application/ld+json"),
            "json_script": pagina.count('type="application/json"'), "auto_json": len(_cerca_json(pagina))}


def raccogli(conn, massimo: int = 10000, tempo_max_s: int = 1800, client=None) -> Counter:
    """Legge le pagine di ricerca di Autohero Italia e aggiorna autohero_prezzi. Si ferma a `massimo` auto o al tempo."""
    import httpx
    st = Counter()
    t0 = time.time()
    c = client or httpx.Client(timeout=30, follow_redirects=True,
                               headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                                        "Accept": "text/html,application/xhtml+xml"})
    schemi = [os.environ.get("AUTOHERO_PAGINA", "")] if os.environ.get("AUTOHERO_PAGINA") else \
        ["/it/search/?page={n}", "/it/search/?pagina={n}", "/it/search/?p={n}"]
    tutte: dict[str, dict] = {}
    schema_buono = None
    for schema in schemi:
        nuove_schema = 0
        for n in range(1, 2000):
            if len(tutte) >= massimo or time.time() - t0 > tempo_max_s:
                break
            url = BASE + (schema.format(n=n) if n > 1 or "{n}" in schema else schema)
            try:
                r = c.get(url)
            except Exception as e:
                st["errore_rete"] += 1
                log.warning("AUTOHERO errore rete pagina %d: %s", n, str(e)[:120])
                if st["errore_rete"] >= 5:
                    break
                continue
            st[f"http_{r.status_code}"] += 1
            if r.status_code in (403, 429):
                log.warning("AUTOHERO bloccato (HTTP %d): mi fermo", r.status_code)
                st["bloccato"] += 1
                break
            if r.status_code != 200:
                break
            if n == 1 and not st["struttura_scritta"]:
                log.warning("AUTOHERO_STRUTTURA %s", json.dumps(struttura(r.text)))
                st["struttura_scritta"] += 1
            schede = schede_da_html(r.text)
            nuove = [s for s in schede if s["uuid"] not in tutte]
            for s in nuove:
                tutte[s["uuid"]] = s
            st["pagine"] += 1
            nuove_schema += len(nuove)
            if n == 1:
                continue
            if not nuove:                                   # la pagina n non porta auto nuove: schema finito o sbagliato
                break
            time.sleep(random.uniform(2.0, 4.0))
        if nuove_schema > 30 and st["pagine"] > 2:
            schema_buono = schema
            break
    st["auto_lette"] = len(tutte)
    st["con_prezzo"] = sum(1 for s in tutte.values() if s["prezzo"])
    log.warning("AUTOHERO_RACCOLTA %s", json.dumps({"schema": schema_buono, **{k: v for k, v in st.items()}}))
    if tutte:
        salva(conn, list(tutte.values()))
    return st


def salva(conn, schede: list[dict]) -> int:
    n = 0
    for s in schede:
        conn.execute(
            "INSERT INTO autohero_prezzi (uuid, url, slug, testo, prezzo, prezzo_barrato, rata, anno, km, carburante, "
            "cambio, primo_visto, ultimo_visto, attivo) VALUES (%(uuid)s, %(url)s, %(slug)s, %(testo)s, %(prezzo)s, "
            "%(prezzo_barrato)s, %(rata)s, %(anno)s, %(km)s, %(carburante)s, %(cambio)s, now(), now(), true) "
            "ON CONFLICT (uuid) DO UPDATE SET prezzo=EXCLUDED.prezzo, prezzo_barrato=EXCLUDED.prezzo_barrato, "
            "rata=EXCLUDED.rata, testo=EXCLUDED.testo, km=EXCLUDED.km, ultimo_visto=now(), attivo=true, "
            "prezzo_iniziale=COALESCE(autohero_prezzi.prezzo_iniziale, autohero_prezzi.prezzo)", s)
        n += 1
    # le auto non più viste da 2 giorni: probabilmente vendute
    conn.execute("UPDATE autohero_prezzi SET attivo=false, sparita_il=COALESCE(sparita_il, now()) "
                 "WHERE attivo AND ultimo_visto < now() - interval '2 days'")
    conn.commit()
    return n


def riepilogo(conn) -> dict:
    r = conn.execute("SELECT count(*) AS n, count(*) FILTER (WHERE attivo) AS attive, "
                     "count(*) FILTER (WHERE prezzo IS NOT NULL) AS con_prezzo, "
                     "percentile_cont(0.5) WITHIN GROUP (ORDER BY prezzo) AS prezzo_med, "
                     "count(*) FILTER (WHERE anno IS NOT NULL AND km IS NOT NULL) AS con_anno_km "
                     "FROM autohero_prezzi").fetchone()
    return dict(r) if r else {}
