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
        prima = re.split(r"\d[\d.]*\s*€", t, maxsplit=1)[0]
        pezzi = [x.strip() for x in prima.split("•") if x.strip() and not re.search(r"(?i)visualizza|preferit", x)]
        out.append({
            "uuid": uid, "url": BASE + m.group(1), "slug": m.group(2),
            "testo": t[:1500],
            "titolo": pezzi[0][:120] if pezzi else None,
            "versione": pezzi[-1][:160] if len(pezzi) > 1 else None,
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
            "INSERT INTO autohero_prezzi (uuid, url, slug, titolo, versione, testo, prezzo, prezzo_barrato, rata, anno, km, carburante, "
            "cambio, primo_visto, ultimo_visto, attivo) VALUES (%(uuid)s, %(url)s, %(slug)s, %(titolo)s, %(versione)s, %(testo)s, %(prezzo)s, "
            "%(prezzo_barrato)s, %(rata)s, %(anno)s, %(km)s, %(carburante)s, %(cambio)s, now(), now(), true) "
            "ON CONFLICT (uuid) DO UPDATE SET prezzo=EXCLUDED.prezzo, prezzo_barrato=EXCLUDED.prezzo_barrato, "
            "rata=EXCLUDED.rata, testo=EXCLUDED.testo, titolo=EXCLUDED.titolo, versione=EXCLUDED.versione, km=EXCLUDED.km, ultimo_visto=now(), attivo=true, "
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
                     "count(*) FILTER (WHERE anno IS NOT NULL AND km IS NOT NULL) AS con_anno_km, "
                     "count(*) FILTER (WHERE dettaglio IS NOT NULL) AS con_dettaglio, "
                     "count(*) FILTER (WHERE allestimento IS NOT NULL) AS con_allestimento "
                     "FROM autohero_prezzi").fetchone()
    return dict(r) if r else {}

# ---------------------------------------------------------------------------------------------------------------
# Pagina della singola auto: tutto quello che c'è (allestimento, potenza, cilindrata, colore, proprietari, tagliandi,
# optional...). Si salva il testo intero e le coppie "voce: valore" riconosciute, più i dati JSON incorporati.
# ---------------------------------------------------------------------------------------------------------------
RE_COPPIE = [
    re.compile(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", re.S | re.I),
    re.compile(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", re.S | re.I),
    re.compile(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", re.S | re.I),
]
VOCI = {
    "allestimento": r"allestimento|versione|trim", "potenza": r"potenza|kw|cv\b", "cilindrata": r"cilindrata",
    "carrozzeria": r"carrozzeria|tipo di (?:veicolo|carrozzeria)", "porte": r"\bporte", "posti": r"\bposti",
    "colore": r"colore", "proprietari": r"proprietari", "immatricolazione": r"immatricola|prima registrazione",
    "tagliandi": r"tagliand|manutenzione|libretto", "classe_emissioni": r"euro\s*\d|emission|classe",
    "consumo": r"consumo", "trazione": r"trazione", "cambio": r"cambio|trasmissione", "chilometraggio": r"chilometr|km",
    "carburante": r"carburante|alimentazione", "garanzia": r"garanzia", "sede": r"sede|filiale|disponibile (?:a|in)",
}


def _pulito(x: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", x))).strip()


def dettaglio_da_html(pagina: str) -> dict:
    """Tutto quello che la pagina dell'auto dice, in forma semplice."""
    coppie = {}
    for rx in RE_COPPIE:
        for a, b in rx.findall(pagina):
            k, v = _pulito(a)[:60], _pulito(b)[:300]
            if k and v and k.lower() not in (x.lower() for x in coppie):
                coppie[k] = v
    testo = _testo(pagina)
    campi = {}
    for nome, rx in VOCI.items():
        for k, v in coppie.items():
            if re.search(rx, k, re.I):
                campi[nome] = v
                break
    # optional / equipaggiamento: le voci di elenco dopo un titolo che parla di equipaggiamento o optional
    optional = []
    m = re.search(r"(?is)(equipaggiament|optional|dotazion|accessori).{0,200}?(<ul.*?</ul>)", pagina)
    if m:
        optional = [_pulito(x)[:80] for x in re.findall(r"<li[^>]*>(.*?)</li>", m.group(2), re.S | re.I)]
        optional = [x for x in optional if x][:150]
    jj = _cerca_json(pagina)
    return {"campi": campi, "coppie": dict(list(coppie.items())[:200]), "optional": optional,
            "json": jj[0] if jj else None, "testo": testo[:20000]}


def dettagli(conn, massimo: int = 800, tempo_max_s: int = 3000, client=None) -> Counter:
    """Apre la pagina delle auto attive ancora senza dettaglio (prima le più recenti) e salva tutto."""
    import httpx
    st = Counter()
    t0 = time.time()
    c = client or httpx.Client(timeout=30, follow_redirects=True,
                               headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9",
                                        "Accept": "text/html,application/xhtml+xml"})
    righe = conn.execute("SELECT uuid, url FROM autohero_prezzi WHERE attivo AND dettaglio IS NULL "
                         "ORDER BY primo_visto DESC LIMIT %s", (massimo,)).fetchall()
    for r in righe:
        if time.time() - t0 > tempo_max_s:
            break
        try:
            resp = c.get(r["url"])
        except Exception as e:
            st["errore_rete"] += 1
            if st["errore_rete"] >= 5:
                break
            continue
        st[f"http_{resp.status_code}"] += 1
        if resp.status_code in (403, 429):
            log.warning("AUTOHERO dettagli bloccati (HTTP %d): mi fermo", resp.status_code)
            break
        if resp.status_code == 404 or resp.status_code == 410:
            conn.execute("UPDATE autohero_prezzi SET attivo=false, sparita_il=COALESCE(sparita_il, now()) WHERE uuid=%s",
                         (r["uuid"],))
            continue
        if resp.status_code != 200:
            continue
        d = dettaglio_da_html(resp.text)
        if not st["struttura_scritta"]:
            log.warning("AUTOHERO_DETTAGLIO_STRUTTURA %s", json.dumps({
                "lunghezza": len(resp.text), "coppie": len(d["coppie"]), "voci": list(d["coppie"])[:40],
                "campi": d["campi"], "optional": len(d["optional"]), "json": bool(d["json"])}, ensure_ascii=False)[:3000])
            st["struttura_scritta"] += 1
        conn.execute("UPDATE autohero_prezzi SET dettaglio=%s, allestimento=%s, dettaglio_at=now() WHERE uuid=%s",
                     (json.dumps(d, ensure_ascii=False), d["campi"].get("allestimento"), r["uuid"]))
        st["letti"] += 1
        st["con_allestimento"] += 1 if d["campi"].get("allestimento") else 0
        st["con_optional"] += 1 if d["optional"] else 0
        if st["letti"] % 50 == 0:
            conn.commit()
        time.sleep(random.uniform(2.0, 4.0))
    conn.commit()
    log.warning("AUTOHERO_DETTAGLI %s", json.dumps(dict(st)))
    return st

