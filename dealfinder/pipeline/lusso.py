"""20/10 (cliente): auto di fascia alta a Milano e dintorni.

lead_supercar(): commercianti di Milano e dintorni che vendono auto usate da 50.000 € in su e dichiarano di
    comprare auto dai privati. Solo dati pubblicati dall'attività. Nei log: LEADS {json}.
raccolta_60k(): tutte le auto da 60.000 € in su in vendita a Milano e dintorni (AutoScout24 e Subito, privati e
    commercianti), salvate nel database per analizzarle. Nei log: LUSSO {json} e LUSSO_FINE.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from html import unescape
from pathlib import Path

log = logging.getLogger("lusso")


def bande_parallele(lo: int, hi: int, step: int, venditori, workers: int | None = None):
    """Ricerca AutoScout24 aggressiva: tante fasce di prezzo lette insieme (più collegamenti in parallelo)."""
    from concurrent.futures import ThreadPoolExecutor
    from ..collectors.autoscout import AutoScoutCollector
    workers = workers or int(os.environ.get("RACCOLTA_PARALLELI", "4"))
    pausa = (float(os.environ.get("RACCOLTA_PAUSA_MIN", "0.25")), float(os.environ.get("RACCOLTA_PAUSA_MAX", "0.6")))
    compiti = [(a, min(a + step - 1, hi), ct, sel) for ct, sel in venditori for a in range(lo, hi, step)]

    def leggi(c):
        col = AutoScoutCollector()
        col.min_delay_s, col.max_delay_s = pausa
        try:
            out = list(col._band(*c))
            for l in out:                     # memoria (512 MB): dell'annuncio originale serve solo il venditore
                if isinstance(l.raw, dict):
                    l.raw = {k: l.raw[k] for k in ("seller", "_telefono") if k in l.raw}
            return out, col.blocked
        except Exception as e:
            log.warning("fascia %s: %s", c, str(e)[:120])
            return [], False

    visti = set()
    bloccate = 0
    with ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(compiti), workers):            # un gruppo di fasce alla volta: in memoria solo quel gruppo
            for out, blocc in ex.map(leggi, compiti[i:i + workers]):
                bloccate += bool(blocc)
                for l in out:
                    if l.source_id in visti:
                        continue
                    visti.add(l.source_id)
                    yield l
                del out
    log.warning("RACCOLTA_VELOCE %d fasce, %d annunci, %d fasce bloccate", len(compiti), len(visti), bloccate)


def _ricerca(min_p: int, venditori, fasce):
    """Annunci AutoScout24 entro 70 km da Milano, a fasce di prezzo (ogni fascia sotto i 400 risultati)."""
    from ..collectors.autoscout import AutoScoutCollector
    os.environ["AUTOSCOUT_CAP"], os.environ["AUTOSCOUT_RAGGIO"] = "20121", os.environ.get("LUSSO_RAGGIO", "70")
    os.environ["AUTOSCOUT_DANNEGGIATE"] = "include"
    os.environ.pop("AUTOSCOUT_KM_MAX", None)
    os.environ.pop("PASSI_JOB", None)
    for lo, hi, step in fasce:
        if hi <= min_p:
            continue
        yield from bande_parallele(max(lo, min_p), hi, step, venditori)


FASCE = [(50000, 100000, 5000), (100000, 200000, 10000)]          # tetto 200.000 € (cliente 20/10)


def lead_supercar() -> int:
    from .lead import COMPRA, QUALSIASI, PROV_DINTORNI, _cellulari, _h, _nn, _nu, _tel9
    from ..collectors.autoscout import AutoScoutCollector
    es = json.loads((Path(__file__).resolve().parents[2] / "data" / "lead_esclusi.json").read_text())
    EU, ET, EN = set(es["url"]), set(es["tel"]), set(es["nome"])

    def gia(nome, url, tels):
        return (_h(_nn(nome)) in EN if nome else False) or (url and _h(_nu(url)) in EU) or \
            any(t and _h(t) in ET for t in (_tel9(x) for x in tels))

    dealers: dict = {}
    n = 0
    for l in _ricerca(50000, [("D", "commerciante")], FASCE):
        n += 1
        s = (l.raw or {}).get("seller") or {}
        if not isinstance(s, dict) or (l.province and l.province.upper() not in PROV_DINTORNI):
            continue
        links = s.get("links") if isinstance(s.get("links"), dict) else {}
        url = (links.get("infoPage") or "").split("?")[0]
        nome = s.get("companyName")
        if not url or not nome:
            continue
        d = dealers.setdefault(url, {"nome": nome, "citta": l.city, "prov": l.province, "n": 0, "marche": Counter(), "max": 0,
                                     "tel": [p.get("formattedNumber") for p in (s.get("phones") or []) if isinstance(p, dict) and p.get("formattedNumber")]})
        d["n"] += 1
        d["marche"][l.make or "?"] += 1
        d["max"] = max(d["max"], l.price_eur or 0)
    log.warning("LEADS_RICERCA %d annunci da 50.000 € in su, %d autosaloni in zona", n, len(dealers))
    asc = AutoScoutCollector()
    trovati = dich = controllati = esclusi = 0
    visti_t: set = set()
    for url, d in sorted(dealers.items(), key=lambda kv: -kv[1]["n"]):
        if d["n"] < int(os.environ.get("LEADS_MIN_AUTO", "2")):
            continue
        if gia(d["nome"], url, d["tel"]):
            esclusi += 1
            continue
        cell = _cellulari(d["tel"])
        if not cell or set(cell) & visti_t:
            continue
        try:
            r = asc.client.get(url)
            testo = unescape(re.sub(r"<[^>]+>", " ", r.text)) if r.status_code == 200 else ""
        except Exception:
            testo = ""
        controllati += 1
        m = COMPRA.search(testo)
        frase = ""
        if m:
            a, b = max(0, m.start() - 90), m.end() + 90
            frase = " ".join(testo[a:b].split())[:240]
            dich += 1
        visti_t.update(cell)
        trovati += 1
        log.warning("LEADS %s", json.dumps({"nome": d["nome"], "comune": d["citta"], "prov": d["prov"], "cell": cell[:2], "url": url,
                                           "auto_50k": d["n"], "prezzo_max": d["max"], "marche": [k for k, _ in d["marche"].most_common(5)],
                                           "dichiara": bool(m), "qualsiasi": bool(QUALSIASI.search(testo)) if testo else False,
                                           "frase": frase}, ensure_ascii=False))
        time.sleep(0.9)
    log.warning("LEADS_FINE controllati %d, già consegnati %d, con cellulare %d, dichiarano di comprare %d", controllati, esclusi, trovati, dich)
    return trovati


def raccolta_60k() -> int:
    from ..db import connect, upsert_listing
    from ..zone import comune_ok
    from .lead import PROV_DINTORNI, CI_SUBITO
    from ..collectors.subito import API_HEADERS, API_SEARCH, SubitoCollector, parse_item
    st = Counter()
    zona = [p.strip() for p in os.environ["LUSSO_PROVINCE"].split(",")] if os.environ.get("LUSSO_PROVINCE") else None
    italia = os.environ.get("LUSSO_TUTTA_ITALIA") == "1"           # 20/10 (cliente): base prezzi 60-200k, tutta Italia
    if italia:
        zona = None
    solo_privati = os.environ.get("LUSSO_SOLO_PRIVATI") == "1"        # 20/10 (cliente): solo privati
    anno_min = int(os.environ.get("LUSSO_ANNO_MIN", "0") or 0)
    ci_sub = dict(CI_SUBITO)
    if zona:
        ci_sub.update({p: c for p, c in (("BS", 2), ("MN", 7), ("SO", 11)) if p in zona})

    def salva(conn, l):
        pv = (l.province or "").upper()
        if italia:
            pass
        elif zona:
            if pv not in zona:
                st["fuori_zona"] += 1
                return
        elif not pv or pv not in PROV_DINTORNI or not comune_ok(pv, l.city):
            st["fuori_zona"] += 1
            return
        if not l.price_eur or l.price_eur < 60000 or l.price_eur > 200000:
            st["fuori_fascia"] += 1
            return
        if solo_privati and l.seller_type != "privato":
            st["non_privato"] += 1
            return
        if anno_min and l.year and l.year < anno_min:
            st["troppo_vecchia"] += 1
            return
        try:
            lid, ev = upsert_listing(conn, l)
            conn.commit()
        except Exception as e:
            conn.rollback()
            st["errore"] += 1
            log.warning("LUSSO non salvato %s: %s", l.url, str(e)[:120])
            return
        st[f"{l.source}:{ev}"] += 1
        st["tenute"] += 1
        if os.environ.get("LUSSO_LOG", "1") == "0" and ev != "nuovo":
            return
        log.warning("LUSSO %s", json.dumps({"id": lid, "fonte": l.source, "auto": " ".join(x for x in (l.make, l.model, l.version_raw) if x)[:90],
                                           "anno": l.year, "km": l.mileage_km, "carb": l.fuel, "kw": l.power_kw, "prezzo": l.price_eur,
                                           "prov": pv, "comune": l.city, "venditore": l.seller_type, "url": l.url}, ensure_ascii=False))

    with connect() as conn:
        venditori = [("P", "privato")] if solo_privati else [("P", "privato"), ("D", "commerciante")]
        ricerca = _ricerca(60000, venditori, FASCE)
        if italia:                                                   # senza CAP: AutoScout24 cerca in tutta Italia
            os.environ.pop("PASSI_JOB", None)
            os.environ["AUTOSCOUT_CAP"] = ""
            ricerca = (l for lo, hi, step in FASCE if hi > 60000
                       for l in bande_parallele(max(lo, 60000), hi, int(os.environ.get("LUSSO_PASSO_ITALIA", "2500")), venditori))
        for l in ricerca:
            salva(conn, l)
        log.warning("LUSSO_AUTOSCOUT %s", dict(st))
        sub = SubitoCollector()
        giri_sub = [("IT", None)] if italia else list(ci_sub.items())
        for prov, ci in giri_sub:
            for page in range(int(os.environ.get("LUSSO_SUB_PAGINE", "100")) if italia else 20):
                try:
                    r = sub.client.get(API_SEARCH, headers=API_HEADERS, params={**({"r": 4, "ci": ci} if ci else {}), "c": 2, "t": "s", "lim": 100,
                                                                                 "start": page * 100, "ps": 60000, "sort": "pricedesc"})
                    ads = ((r.json() or {}).get("ads") or []) if r.status_code == 200 else []
                except Exception:
                    ads = []
                sotto = 0
                if page == 0:
                    pr = [x for x in (parse_item(a) for a in ads) if x and x.price_eur]
                    log.warning("LUSSO_SUBITO_PROVA %s: %d annunci, prezzo minimo %s", prov, len(ads), min((x.price_eur for x in pr), default=None))
                for it in ads:
                    l = parse_item(it)
                    if not l:
                        continue
                    if not l.price_eur or l.price_eur < 60000:
                        sotto += 1
                        continue
                    if l.price_eur > 200000:
                        continue
                    l.province = l.province or prov
                    salva(conn, l)
                time.sleep(1.0)
                if len(ads) < 100 or sotto > 80:       # il filtro di prezzo non c'è o siamo scesi sotto 60.000 €
                    break
            log.warning("LUSSO_SUBITO %s fatto: %s", prov, dict(st))
    log.warning("LUSSO_FINE %s", dict(st))
    return st["tenute"]


LOMBARDIA = {"MI", "MB", "VA", "CO", "LC", "BG", "BS", "LO", "PV", "CR", "MN", "SO"}


def privati_lombardia() -> int:
    """20/10 (cliente): solo PRIVATI, tutta la Lombardia, auto da 60.000 € in su (AutoScout24 e Subito).
    Salvate nel database per analizzarle. Nei log: PRIV60 {json} e PRIV60_FINE."""
    from ..db import connect, upsert_listing
    from ..collectors.subito import API_HEADERS, API_SEARCH, SubitoCollector, parse_item
    st = Counter()

    def salva(conn, l):
        pv = (l.province or "").upper()
        if pv not in LOMBARDIA:
            st["fuori_lombardia"] += 1
            return
        if l.seller_type != "privato":
            st["non_privato"] += 1
            return
        if not l.price_eur or l.price_eur < 60000:
            st["sotto_60k"] += 1
            return
        try:
            lid, ev = upsert_listing(conn, l)
            conn.commit()
        except Exception as e:
            conn.rollback()
            st["errore"] += 1
            return
        st[f"{l.source}:{ev}"] += 1
        st["tenute"] += 1
        log.warning("PRIV60 %s", json.dumps({"id": lid, "fonte": l.source, "auto": " ".join(x for x in (l.make, l.model, l.version_raw) if x)[:90],
                                            "anno": l.year, "km": l.mileage_km, "carb": l.fuel, "prezzo": l.price_eur,
                                            "prov": pv, "comune": l.city, "url": l.url}, ensure_ascii=False))

    os.environ["LUSSO_RAGGIO"] = os.environ.get("PRIV60_RAGGIO", "150")
    fasce = [(60000, 100000, 10000), (100000, 200000, 25000), (200000, 3000000, 2800000)]
    with connect() as conn:
        for l in _ricerca(60000, [("P", "privato")], fasce):
            salva(conn, l)
        log.warning("PRIV60_AUTOSCOUT %s", dict(st))
        sub = SubitoCollector()
        for page in range(int(os.environ.get("PRIV60_SUB_PAGINE", "60"))):
            try:
                r = sub.client.get(API_SEARCH, headers=API_HEADERS, params={"c": 2, "r": 4, "t": "s", "lim": 100, "start": page * 100,
                                                                             "ps": 60000, "sort": "pricedesc"})
                ads = ((r.json() or {}).get("ads") or []) if r.status_code == 200 else []
            except Exception:
                ads = []
            sotto = 0
            for it in ads:
                l = parse_item(it)
                if not l:
                    continue
                if not l.price_eur or l.price_eur < 60000:
                    sotto += 1
                    continue
                salva(conn, l)
            time.sleep(1.0)
            if len(ads) < 100 or sotto > 80:
                break
    log.warning("PRIV60_FINE %s", dict(st))
    return st["tenute"]


SPORTIVE_MARCHE = {"porsche", "ferrari", "lamborghini", "maserati", "mclaren", "aston martin", "bentley", "lotus", "alpine",
                   "jaguar", "corvette", "chevrolet", "dodge", "nissan", "abarth", "cupra", "alfa romeo", "audi", "bmw",
                   "mercedes-benz", "ford", "toyota", "honda", "subaru", "morgan", "ktm", "lexus"}
SPORTIVE_MODELLI = re.compile(r"\b(911|99[1-9]|718|cayman|boxster|carrera|gt[234]?|gts|turbo s|amg|rs ?\d|r8|tt ?rs|ttrs|"
                              r"m[2-8]\b|m\d{3}|z4|i8|sls|slk|sl\b|f-?type|gt-?r|370z|nismo|supra|gr86|gt86|mustang|camaro|"
                              r"corvette|viper|giulia quadrifoglio|quadrifoglio|qv\b|4c|695|124 spider|leon cupra|cupra|type r|"
                              r"wrx|sti|lc ?500|rc ?f|huracan|urus|gallardo|aventador|488|458|f8|roma|portofino|california|"
                              r"812|296|sf90|mc20|granturismo|grancabrio|ghibli trofeo|vantage|db11|db9|continental|elise|"
                              r"exige|evora|emira|a110|giulietta qv|focus rs|fiesta st|golf r|gti|clubsport|scirocco r)\b", re.I)
ACQ_DIRETTO = re.compile(r"acquistiamo|compriamo|compro auto|acquisto auto|acquisto (del )?(vostr|tu)\w* (auto|veicol)|"
                         r"compr\w* (la )?(tua|vostra) auto|acquist\w* (la )?(tua|vostra) auto|"
                         r"anche senza (permuta|acquisto|obbligo)|senza (obbligo di )?(permuta|acquist\w* (un|di)\w*)|"
                         r"pagamento (immediato|in contanti|contanti)|vendi(ci)? la tua auto|valutazione e acquisto", re.I)
SENZA_PERMUTA = re.compile(r"senza (obbligo di )?(permuta|acquist)|anche senza (permuta|acquisto)|pagamento (immediato|in contanti)|"
                           r"contanti|acquisto diretto|compriamo (la tua|auto)|acquistiamo (la tua|auto)", re.I)


def lead_sportive() -> int:
    """20/10 (cliente): commercianti di Milano e province che trattano auto SPORTIVE e dichiarano di comprare auto
    dai privati (meglio se anche senza permuta). Pagina AutoScout24 e, se c'è, il sito dell'attività.
    Solo cellulari pubblicati dall'attività. Esclusi quelli già consegnati. Nei log: LEADSP {json}."""
    import httpx
    from concurrent.futures import ThreadPoolExecutor
    from .lead import PROV_DINTORNI, _cellulari, _h, _nn, _nu, _tel9, TEL
    es = json.loads((Path(__file__).resolve().parents[2] / "data" / "lead_esclusi.json").read_text())
    EU, ET, EN = set(es["url"]), set(es["tel"]), set(es["nome"])
    os.environ["AUTOSCOUT_CAP"], os.environ["AUTOSCOUT_RAGGIO"] = "20121", os.environ.get("LEADSP_RAGGIO", "70")
    if os.environ.get("LEADSP_ITALIA") == "1":          # 21/10: tutta Italia (poi si filtra per LEADSP_PROVINCE)
        os.environ["AUTOSCOUT_CAP"] = ""
    os.environ.pop("PASSI_JOB", None)
    dealers: dict = {}
    n = 0
    diag = 0
    zona_lead = [p.strip() for p in os.environ["LEADSP_PROVINCE"].split(",")] if os.environ.get("LEADSP_PROVINCE") else PROV_DINTORNI
    da, a_, passo = (int(os.environ.get(k, v)) for k, v in (("LEADSP_DA", "12000"), ("LEADSP_A", "300000"), ("LEADSP_PASSO", "4000")))
    for l in bande_parallele(da, a_, passo, [("D", "commerciante")]):
        n += 1
        s = (l.raw or {}).get("seller") or {}
        if not isinstance(s, dict) or (l.province and l.province.upper() not in zona_lead):
            continue
        links = s.get("links") if isinstance(s.get("links"), dict) else {}
        if diag < 2:
            diag += 1
            log.warning("LEADSP_CAMPI venditore=%s link=%s", sorted(s)[:30], sorted(links)[:20])
        url = (links.get("infoPage") or "").split("?")[0]
        nome = s.get("companyName")
        if not url or not nome:
            continue
        sito = next((v for k, v in links.items() if isinstance(v, str) and v.startswith("http") and "autoscout24" not in v), None)
        d = dealers.setdefault(url, {"nome": nome, "citta": l.city, "prov": l.province, "n": 0, "sport": 0, "esempi": [], "sito": sito,
                                     "tel": [p.get("formattedNumber") for p in (s.get("phones") or []) if isinstance(p, dict) and p.get("formattedNumber")]})
        d["n"] += 1
        testo = f"{l.model or ''} {l.version_raw or ''} {l.title or ''}"
        if (l.make or "").lower() in ("porsche", "ferrari", "lamborghini", "mclaren", "aston martin", "lotus", "maserati") or SPORTIVE_MODELLI.search(testo):
            d["sport"] += 1
            if len(d["esempi"]) < 4:
                d["esempi"].append(f"{l.make} {l.model}".strip())
    if os.environ.get("LEADSP_FASCIA") == "1":      # 20/10 (cliente): chi tratta auto da 60.000 a 200.000 € (tutte quelle lette sono in fascia)
        scelti = [(u, d) for u, d in dealers.items() if d["n"] >= int(os.environ.get("LEADSP_MIN_AUTO", "2"))]
        scelti.sort(key=lambda kv: -kv[1]["n"])
    else:
        scelti = [(u, d) for u, d in dealers.items() if d["sport"] >= int(os.environ.get("LEADSP_MIN_SPORT", "2"))]
    log.warning("LEADSP_RICERCA %d annunci, %d autosaloni in zona, %d con almeno 2 sportive", n, len(dealers), len(scelti))

    def gia(nome, url, tels):
        return (_h(_nn(nome)) in EN if nome else False) or (url and _h(_nu(url)) in EU) or \
            any(t and _h(t) in ET for t in (_tel9(x) for x in tels))

    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36", "Accept-Language": "it-IT,it;q=0.9"}

    def testo_di(cl, u):
        try:
            r = cl.get(u, timeout=15)
            if r.status_code != 200:
                return "", ""
            t = unescape(re.sub(r"<[^>]+>", " ", r.text))
            t = re.sub(r"reviewText.{0,900}?(?=author|ratingValue|datePublished|$)", " ", t, flags=re.S)   # niente recensioni dei clienti
            return t, r.text
        except Exception:
            return "", ""
        except Exception:
            return "", ""

    def controlla(item):
        url, d = item
        with httpx.Client(headers=hdr, follow_redirects=True) as cl:
            t, _ = testo_di(cl, url)
            fonte, m = url, ACQ_DIRETTO.search(t)
            tel_extra = []
            if d["sito"]:
                ts, html = testo_di(cl, d["sito"])
                tel_extra = TEL.findall(ts)
                if not m:
                    m = ACQ_DIRETTO.search(ts)
                    fonte = d["sito"] if m else fonte
                if not m and html:                      # pagina "acquistiamo / vendi la tua auto" del sito
                    for h in re.findall(r'href="([^"#]+)"', html)[:400]:
                        if re.search(r"acquist|compr|vendi|valutaz|ritir", h, re.I):
                            u2 = h if h.startswith("http") else d["sito"].rstrip("/") + "/" + h.lstrip("/")
                            t2, _ = testo_di(cl, u2)
                            m = ACQ_DIRETTO.search(t2)
                            if m:
                                fonte, ts = u2, t2
                                tel_extra += TEL.findall(t2)
                                break
                if m and fonte != url:
                    t = ts
        frase = ""
        if m:
            a, b = max(0, m.start() - 100), m.end() + 100
            frase = " ".join(t[a:b].split())[:260]
        return url, d, bool(m), bool(SENZA_PERMUTA.search(t)) if m else False, frase, fonte, tel_extra

    trovati = dich = esclusi = 0
    visti_t: set = set()
    da_controllare = []
    for url, d in scelti if os.environ.get("LEADSP_FASCIA") == "1" else sorted(scelti, key=lambda kv: -kv[1]["sport"]):
        if gia(d["nome"], url, d["tel"]):
            esclusi += 1
            continue
        da_controllare.append((url, d))
    with ThreadPoolExecutor(8) as ex:
        for url, d, dichiara, senza, frase, fonte, tel_extra in ex.map(controlla, da_controllare):
            cell = _cellulari(list(d["tel"]) + tel_extra)
            if gia(d["nome"], url, cell):
                esclusi += 1
                continue
            if cell and set(cell) & visti_t:
                continue
            visti_t.update(cell)
            trovati += 1
            dich += dichiara
            log.warning("LEADSP %s", json.dumps({"nome": d["nome"], "comune": d["citta"], "prov": d["prov"], "cell": cell[:2],
                                                 "url": url, "sito": d["sito"], "sportive": d["sport"], "annunci": d["n"],
                                                 "esempi": d["esempi"], "dichiara": dichiara, "senza_permuta": senza,
                                                 "frase": frase, "fonte": fonte}, ensure_ascii=False))
    log.warning("LEADSP_FINE controllati %d, già consegnati %d, nuovi %d, dichiarano di comprare %d", len(da_controllare), esclusi, trovati, dich)
    return trovati


def lead_subito_alta() -> int:
    """21/10 (cliente): commercianti di auto di ALTA GAMMA di Milano e dintorni (no Bergamo/Brescia) presi da Subito:
    attività con annunci da LEADSUB_MIN € in su; cellulare scritto dall'attività negli annunci o sulla sua vetrina
    impresapiu; segnato chi scrive di comprare auto dai privati. Esclusi i numeri già consegnati. Nei log: LEADSUB {json}."""
    import httpx
    from .lead import TEL, _cellulari, _h, _nn, _tel9
    from ..collectors.subito import API_HEADERS, API_SEARCH, SubitoCollector, parse_item
    es = json.loads((Path(__file__).resolve().parents[2] / "data" / "lead_esclusi.json").read_text())
    ET, EN = set(es["tel"]), set(es["nome"])
    zona = [p.strip() for p in os.environ.get("LEADSUB_PROVINCE", "MI,MB,VA,CO,LC,LO,PV,CR").split(",")]
    minimo = int(os.environ.get("LEADSUB_MIN", "50000"))
    sub = SubitoCollector()
    shops: dict = {}
    letti = 0
    for page in range(int(os.environ.get("LEADSUB_PAGINE", "80"))):
        try:
            r = sub.client.get(API_SEARCH, headers=API_HEADERS, params={"c": 2, "r": 4, "t": "s", "lim": 100, "start": page * 100,
                                                                         "ps": minimo, "sort": "pricedesc"})
            ads = ((r.json() or {}).get("ads") or []) if r.status_code == 200 else []
        except Exception:
            ads = []
        sotto = 0
        for it in ads:
            letti += 1
            l = parse_item(it)
            if not l or not l.price_eur:
                continue
            if l.price_eur < minimo:
                sotto += 1
                continue
            if l.seller_type != "commerciante" or (l.province or "").upper() not in zona:
                continue
            adv = it.get("advertiser") or {}
            sid = str(adv.get("shop_id") or adv.get("user_id") or "")
            nome = adv.get("shop_name") or adv.get("name") or ""
            if not sid or not nome:
                continue
            d = shops.setdefault(sid, {"nome": nome, "prov": l.province, "comune": l.city, "n": 0, "esempi": [], "testo": "",
                                       "shop": bool(adv.get("shop_id"))})
            d["n"] += 1
            if len(d["esempi"]) < 4:
                d["esempi"].append(f"{l.make or ''} {l.model or ''} {l.price_eur}€".strip())
            if len(d["testo"]) < 20000:
                d["testo"] += " " + (l.title or "") + " " + (l.description or "")
        time.sleep(0.8)
        if len(ads) < 100 or sotto > 90:
            break
    log.warning("LEADSUB_RICERCA %d annunci letti, %d attività in zona con auto da %d €", letti, len(shops), minimo)
    trovati = dich = gia = 0
    visti: set = set()
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36",
                               "Accept-Language": "it-IT"}, follow_redirects=True, timeout=15) as cl:
        for sid, d in sorted(shops.items(), key=lambda kv: -kv[1]["n"]):
            testo = d["testo"]
            fonte = "annunci Subito"
            cell = _cellulari(TEL.findall(testo))
            if d["shop"]:
                try:
                    rr = cl.get(f"https://impresapiu.subito.it/shops/{sid}")
                    if rr.status_code == 200:
                        t2 = unescape(re.sub(r"<[^>]+>", " ", rr.text))
                        c2 = _cellulari(TEL.findall(t2))
                        if c2 and not cell:
                            fonte = "vetrina impresapiu"
                        cell = cell or c2
                        testo += " " + t2[:20000]
                except Exception:
                    pass
                time.sleep(0.5)
            if not cell:
                continue
            if _h(_nn(d["nome"])) in EN or any(_h(_tel9(c)) in ET for c in cell):
                gia += 1
                continue
            if set(cell) & visti:
                continue
            visti.update(cell)
            m = ACQ_DIRETTO.search(testo)
            frase = " ".join(testo[max(0, m.start() - 100):m.end() + 100].split())[:250] if m else ""
            trovati += 1
            dich += bool(m)
            log.warning("LEADSUB %s", json.dumps({"nome": d["nome"], "comune": d["comune"], "prov": d["prov"], "cell": cell[:2],
                                                  "url": f"https://impresapiu.subito.it/shops/{sid}" if d["shop"] else "",
                                                  "annunci_alta": d["n"], "esempi": d["esempi"], "dichiara": bool(m),
                                                  "senza_permuta": bool(SENZA_PERMUTA.search(testo)) if m else False,
                                                  "frase": frase, "fonte": fonte}, ensure_ascii=False))
    log.warning("LEADSUB_FINE attività %d, già consegnate %d, nuove con cellulare %d, dichiarano di comprare %d",
                len(shops), gia, trovati, dich)
    return trovati
