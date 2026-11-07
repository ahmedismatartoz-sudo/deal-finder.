"""Scheda trattativa di un'auto (7/11): storico del prezzo, comportamento del venditore e previsioni.

Per ogni auto del sito:
  - storico: prezzi nel tempo (anche dagli annunci precedenti della stessa auto, tolti e ripubblicati),
    giorni in vendita, ribassi fatti, doppioni su altri portali;
  - segnali dal testo (urgente, trasferimento, trattabile, "non trattabile"...);
  - concorrenza: auto uguali usate nei confronti e quante costano meno;
  - voto "disponibile a trattare" da 1 a 10, con i motivi;
  - previsione: dagli annunci spariti negli ultimi 90 giorni (vendite probabili), quanto scende ancora un annuncio
    con la stessa età e gli stessi ribassi, con che probabilità e in quanti giorni;
  - tre prezzi: offerta d'apertura, chiusura probabile, massimo da pagare per tenere il margine minimo;
  - argomenti in euro, quando chiamare e il messaggio pronto da mandare.

Limite dichiarato: non vediamo il prezzo finale della trattativa, solo l'ultimo prezzo dell'annuncio prima che
sparisca. Le statistiche si calcolano nel database (ogni 12 ore, in un thread del sito) e qui restano solo poche righe.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("trattativa")

PUNTI = (0, 7, 14, 21, 30, 45, 60, 90)          # età dell'annuncio (giorni) in cui si "fotografa" lo stato
N_MIN = 30                                       # annunci minimi in un gruppo per usarne la statistica
STATS: dict = {"t": 0.0, "gruppi": {}, "n": 0, "in_corso": False, "modelli": {}, "attivi": {}, "esempi": {}}

STATS_SQL = [
    "DROP TABLE IF EXISTS _tr_v",
    """CREATE TEMP TABLE _tr_v AS
       SELECT l.id, l.price_eur AS ultimo, CASE WHEN l.seller_type = 'commerciante' THEN 'commerciante' ELSE 'privato' END AS vend,
              l.first_seen_at AS da, l.disappeared_at AS a
       FROM listings l
       WHERE l.status = 'scomparso' AND l.disappeared_at > now() - interval '90 days'
         AND COALESCE(l.disappear_reason, '') NOT IN ('scaduto', 'ripubblicato')
         AND l.source IN ('subito', 'autoscout24') AND l.price_eur BETWEEN 1000 AND 100000
         AND l.disappeared_at > l.first_seen_at + interval '1 day'
       ORDER BY l.disappeared_at DESC LIMIT 40000""",
    "DROP TABLE IF EXISTS _tr_e",
    """CREATE TEMP TABLE _tr_e AS
       SELECT x.listing_id AS id, x.at, x.price_eur AS p,
              (x.prima IS NOT NULL AND x.price_eur < x.prima)::int AS ribasso
       FROM (SELECT pe.listing_id, pe.at, pe.price_eur, lag(pe.price_eur) OVER (PARTITION BY pe.listing_id ORDER BY pe.at) AS prima
             FROM price_events pe JOIN _tr_v v ON v.id = pe.listing_id
             WHERE pe.event IN ('apparso', 'prezzo') AND pe.price_eur > 0) x""",
    "CREATE INDEX ON _tr_e (id, at)",
]
GRUPPI_SQL = """
WITH cp AS (SELECT unnest(%(punti)s::int[]) AS g),
st AS (
  SELECT v.vend, cp.g, v.ultimo,
         (SELECT e.p FROM _tr_e e WHERE e.id = v.id AND e.at <= v.da + make_interval(days => cp.g)
          ORDER BY e.at DESC LIMIT 1) AS p,
         (SELECT COALESCE(sum(e.ribasso), 0) FROM _tr_e e WHERE e.id = v.id AND e.at <= v.da + make_interval(days => cp.g)) AS rib,
         extract(epoch FROM v.a - (v.da + make_interval(days => cp.g))) / 86400.0 AS resto_giorni
  FROM _tr_v v CROSS JOIN cp
  WHERE v.a > v.da + make_interval(days => cp.g))
SELECT vend, g, LEAST(rib, 3)::int AS rib, count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY ultimo::float / p - 1) AS resto_med,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY ultimo::float / p - 1) AS resto_p25,
       avg((ultimo < p * 0.99)::int) AS quota_scende,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY resto_giorni) AS giorni_med
FROM st WHERE p > 0 GROUP BY 1, 2, 3"""


def aggiorna_statistiche(conn) -> dict:
    """Ricalcola i gruppi (venditore, età, ribassi) dagli annunci spariti. Poche decine di righe in memoria."""
    for q in STATS_SQL:
        conn.execute(q)
    rows = conn.execute(GRUPPI_SQL, {"punti": list(PUNTI)}).fetchall()
    gruppi = {}
    for r in rows:
        gruppi[(r["vend"], int(r["g"]), int(r["rib"]))] = {
            "n": int(r["n"]), "resto_med": float(r["resto_med"] or 0), "resto_p25": float(r["resto_p25"] or 0),
            "quota_scende": float(r["quota_scende"] or 0), "giorni_med": float(r["giorni_med"] or 0)}
    conn.execute("DROP TABLE IF EXISTS _tr_e")
    conn.execute("DROP TABLE IF EXISTS _tr_v")
    conn.commit()
    STATS.update(t=time.time(), gruppi=gruppi, n=sum(g["n"] for k, g in gruppi.items() if k[1] == 0))
    log.warning("SCHEDA_STATS %s", {"gruppi": len(gruppi), "annunci": STATS["n"],
                                    "privato_0g_0rib": gruppi.get(("privato", 0, 0)),
                                    "privato_30g_1rib": gruppi.get(("privato", 30, 1))})
    return gruppi


def avvia_statistiche(connect, ogni_ore: float = 12, attesa_s: int = 240) -> None:
    """Thread del sito: calcola le statistiche dopo l'avvio e poi ogni 12 ore."""
    def giro():
        time.sleep(attesa_s)
        while True:
            STATS["in_corso"] = True
            try:
                with connect() as conn:
                    conn.execute("SET statement_timeout = '600s'")
                    aggiorna_statistiche(conn)
                    aggiorna_modelli(conn)
            except Exception as e:
                log.warning("SCHEDA_STATS non riuscite: %s", str(e)[:300])
            finally:
                STATS["in_corso"] = False
            time.sleep(ogni_ore * 3600)
    threading.Thread(target=giro, name="scheda-statistiche", daemon=True).start()


def gruppo(vend: str, giorni: float, rib: int, gruppi: dict | None = None) -> dict | None:
    """Statistica più vicina allo stato dell'auto, allargando se il gruppo ha pochi annunci."""
    gruppi = STATS["gruppi"] if gruppi is None else gruppi
    if not gruppi:
        return None
    g = max(p for p in PUNTI if p <= max(0, giorni))
    r = min(max(int(rib), 0), 3)
    prove = [(vend, g, r), (vend, g, min(r, 1)), ("privato" if vend == "commerciante" else "commerciante", g, r)]
    for k in prove:
        s = gruppi.get(k)
        if s and s["n"] >= N_MIN:
            return dict(s, chiave=f"{k[0]}, {k[1]}+ giorni, {k[2]}{'+' if k[2] == 3 else ''} ribassi")
    # unione dei venditori sullo stesso stato
    tot = [gruppi[k] for k in gruppi if k[1] == g and k[2] == r]
    n = sum(s["n"] for s in tot)
    if n >= N_MIN:
        media = lambda c: sum(s[c] * s["n"] for s in tot) / n
        return {"n": n, "resto_med": media("resto_med"), "resto_p25": media("resto_p25"),
                "quota_scende": media("quota_scende"), "giorni_med": media("giorni_med"),
                "chiave": f"tutti, {g}+ giorni, {r} ribassi"}
    return None

# ---------------------------------------------------------------- rivendita (tempo di vendita del modello)
# Per marca, modello, carburante e anno: quante ne spariscono (vendute, probabilmente) negli ultimi 90 e 30 giorni,
# dopo quanti giorni, a che prezzo; quante sono in vendita adesso. Calcolato insieme alle statistiche dei ribassi.
MODELLI_SQL = """
SELECT lower(make) AS make, lower(model) AS model, fuel, year,
       count(*) AS venduti_90,
       count(*) FILTER (WHERE disappeared_at > now() - interval '30 days') AS venduti_30,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM disappeared_at - first_seen_at) / 86400.0) AS giorni_med,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY extract(epoch FROM disappeared_at - first_seen_at) / 86400.0) AS giorni_p25,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY price_eur) FILTER (WHERE disappeared_at > now() - interval '30 days') AS prezzo_30,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY price_eur) FILTER (WHERE disappeared_at <= now() - interval '30 days') AS prezzo_prima
FROM listings
WHERE status = 'scomparso' AND disappeared_at > now() - interval '90 days'
  AND COALESCE(disappear_reason, '') NOT IN ('scaduto', 'ripubblicato')
  AND source IN ('subito', 'autoscout24') AND price_eur BETWEEN 500 AND 150000
  AND make IS NOT NULL AND model IS NOT NULL AND year IS NOT NULL
  AND disappeared_at > first_seen_at + interval '12 hours'
GROUP BY 1, 2, 3, 4"""
ESEMPI_SQL = """
SELECT make, model, fuel, year, price_eur, mileage_km, city, province, giorni, quando FROM (
  SELECT lower(make) AS make, lower(model) AS model, fuel, year, price_eur, mileage_km, city, province,
         extract(epoch FROM disappeared_at - first_seen_at) / 86400.0 AS giorni, disappeared_at AS quando,
         row_number() OVER (PARTITION BY lower(make), lower(model), fuel, year ORDER BY disappeared_at DESC) AS rn
  FROM listings
  WHERE status = 'scomparso' AND disappeared_at > now() - interval '90 days'
    AND COALESCE(disappear_reason, '') NOT IN ('scaduto', 'ripubblicato')
    AND source IN ('subito', 'autoscout24') AND price_eur BETWEEN 500 AND 150000
    AND make IS NOT NULL AND model IS NOT NULL AND year IS NOT NULL
    AND disappeared_at > first_seen_at + interval '12 hours') x
WHERE rn <= 6"""
ATTIVI_SQL = """
SELECT lower(make) AS make, lower(model) AS model, fuel, year, count(*) AS attivi,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY price_eur) AS prezzo_med,
       count(*) FILTER (WHERE first_seen_at > now() - interval '30 days') AS nuovi_30
FROM listings
WHERE status = 'attivo' AND source IN ('subito', 'autoscout24') AND price_eur BETWEEN 500 AND 150000
  AND make IS NOT NULL AND model IS NOT NULL AND year IS NOT NULL
GROUP BY 1, 2, 3, 4"""


def aggiorna_modelli(conn) -> int:
    mod, att = {}, {}
    for r in conn.execute(MODELLI_SQL).fetchall():
        mod[(r["make"], r["model"], r["fuel"], int(r["year"]))] = (
            int(r["venduti_90"]), int(r["venduti_30"]), float(r["giorni_med"] or 0), float(r["giorni_p25"] or 0),
            float(r["prezzo_30"]) if r["prezzo_30"] else None, float(r["prezzo_prima"]) if r["prezzo_prima"] else None)
    for r in conn.execute(ATTIVI_SQL).fetchall():
        att[(r["make"], r["model"], r["fuel"], int(r["year"]))] = (int(r["attivi"]), float(r["prezzo_med"] or 0),
                                                                 int(r["nuovi_30"]))
    es = {}
    for r in conn.execute(ESEMPI_SQL).fetchall():           # casi reali: ultime 6 vendute per modello/carburante/anno
        es.setdefault((r["make"], r["model"], r["fuel"], int(r["year"])), []).append(
            (int(r["price_eur"]), int(r["mileage_km"]) if r["mileage_km"] is not None else None, r["city"] or r["province"],
             max(1, int(round(float(r["giorni"])))), r["quando"].date().isoformat() if r["quando"] else None, int(r["year"])))
    STATS.update(modelli=mod, attivi=att, esempi=es)
    log.warning("SCHEDA_MODELLI %s", {"gruppi_venduti": len(mod), "gruppi_attivi": len(att), "esempi": sum(map(len, es.values()))})
    return len(mod)


def rivendita(make, model, fuel, anno, km=None, prezzo_rivendita=None, modelli=None, attivi=None, esempi=None) -> dict | None:
    """Quanto velocemente si vende questo modello (anni vicini, stesso carburante) e quanta concorrenza c'è."""
    modelli = STATS["modelli"] if modelli is None else modelli
    attivi = STATS["attivi"] if attivi is None else attivi
    if not (make and model and anno) or not modelli:
        return None
    k0 = (str(make).lower(), str(model).lower(), fuel)
    for tol in (1, 2, 3):
        anni = range(int(anno) - tol, int(anno) + tol + 1)
        v = [modelli[k0 + (a,)] for a in anni if k0 + (a,) in modelli]
        n90 = sum(x[0] for x in v)
        if n90 >= 12:
            break
    if n90 < 6:
        return None
    a = [attivi[k0 + (y,)] for y in anni if k0 + (y,) in attivi]
    n30 = sum(x[1] for x in v)
    giorni = sum(x[2] * x[0] for x in v) / n90
    giorni_p25 = sum(x[3] * x[0] for x in v) / n90
    in_vendita = sum(x[0] for x in a)
    nuovi_30 = sum(x[2] for x in a)
    p30 = [(x[4], x[1]) for x in v if x[4]]
    pp = [(x[5], x[0] - x[1]) for x in v if x[5] and x[0] - x[1] > 0]
    trend = None
    if p30 and pp and sum(w for _, w in p30) >= 5 and sum(w for _, w in pp) >= 5:
        m30 = sum(p * w for p, w in p30) / sum(w for _, w in p30)
        mpr = sum(p * w for p, w in pp) / sum(w for _, w in pp)
        trend = round((m30 / mpr - 1) * 100, 1) if mpr else None
    vendite_mese = n90 / 3.0
    settimane_stock = round(in_vendita / (vendite_mese / 4.33), 1) if vendite_mese else None
    if giorni <= 14:
        vel, vel_t = "molto_veloce", "si vende molto velocemente"
    elif giorni <= 30:
        vel, vel_t = "veloce", "si vende velocemente"
    elif giorni <= 55:
        vel, vel_t = "media", "tempi di vendita nella media"
    else:
        vel, vel_t = "lenta", "si vende lentamente"
    if settimane_stock is None:
        conc, conc_t = None, None
    elif settimane_stock <= 3:
        conc, conc_t = "poca", "poca concorrenza: se ne vendono più di quante ne escono"
    elif settimane_stock <= 8:
        conc, conc_t = "media", "concorrenza nella media"
    else:
        conc, conc_t = "tanta", "tanta concorrenza: ci sono molte auto uguali in vendita"
    punti = {"molto_veloce": 4, "veloce": 3, "media": 2, "lenta": 1}[vel] + {"poca": 2, "media": 1, "tanta": 0, None: 1}[conc]
    if trend is not None:
        punti += 1 if trend > 2 else (-1 if trend < -3 else 0)
    voto = max(1, min(10, round(punti * 10 / 7)))
    frasi = [f"Quest'auto {vel_t}: di solito sparisce in circa {int(round(giorni))} giorni"
             f" (le più rapide in {int(round(giorni_p25))})."]
    if conc_t:
        frasi.append(f"{in_vendita} auto simili in vendita ora contro circa {int(round(vendite_mese))} vendute al mese: {conc_t}.")
    if trend is not None and abs(trend) >= 2:
        frasi.append(f"Prezzi di vendita {'in salita' if trend > 0 else 'in calo'} del {abs(trend)}% nell'ultimo mese.")
    esempi = STATS.get("esempi", {}) if esempi is None else esempi
    casi = [c for y in anni for c in esempi.get(k0 + (y,), [])]
    if km is not None:
        casi.sort(key=lambda c: (abs((c[1] if c[1] is not None else 10 ** 7) - km), -int((c[4] or "0").replace("-", ""))))
    casi = [{"prezzo": c[0], "km": c[1], "dove": c[2], "giorni": c[3], "quando": c[4], "anno": c[5]} for c in casi[:3]]
    return {"casi": casi, "voto": voto, "velocita": vel, "giorni_vendita": int(round(giorni)), "giorni_veloci": int(round(giorni_p25)),
            "vendute_90g": n90, "vendute_30g": n30, "vendute_mese": round(vendite_mese, 1), "in_vendita": in_vendita,
            "nuove_30g": nuovi_30, "settimane_di_offerta": settimane_stock, "concorrenza": conc, "trend_prezzi_pct": trend,
            "anni": f"{min(anni)}–{max(anni)}", "frasi": frasi,
            "nota": "Auto dello stesso modello, carburante e anni vicini sparite dagli annunci negli ultimi 90 giorni "
                    "(Subito e AutoScout24, nord Italia): un annuncio sparito è quasi sempre un'auto venduta."}


# ---------------------------------------------------------------- da sapere (per chi compra e poi rivende)
# Stime: classe Euro dall'anno (la vera è sul libretto), bollo Lombardia, passaggio con IPT di Milano.
# Area B Milano (fonte: Comune di Milano, ottobre 2026): vietati ora benzina fino a Euro 3 e diesel fino a Euro 5;
# dal 1/10/2028 benzina Euro 4 e diesel Euro 6 a-b-c; dal 1/10/2030 diesel Euro 6 d-temp e d.

def classe_euro(fuel: str | None, anno: int | None) -> str | None:
    if not anno:
        return None
    f = (fuel or "").lower()
    if f.startswith("diesel"):
        return ("Euro 3" if anno <= 2005 else "Euro 4" if anno <= 2010 else "Euro 5" if anno <= 2015
                else "Euro 6 (a-b-c)" if anno <= 2019 else "Euro 6 d-temp/d")
    if f in ("elettrica",):
        return "elettrica"
    return ("Euro 2" if anno <= 2000 else "Euro 3" if anno <= 2005 else "Euro 4" if anno <= 2010
            else "Euro 5" if anno <= 2014 else "Euro 6")


def area_b(fuel: str | None, classe: str | None) -> tuple[str, str] | None:
    if not classe or classe == "elettrica":
        return ("ok", "Entra in Area B e Area C a Milano senza limiti (elettrica).") if classe else None
    f = (fuel or "").lower()
    if f.startswith("diesel"):
        if classe in ("Euro 3", "Euro 4", "Euro 5"):
            return ("male", f"Diesel {classe}: NON entra già oggi in Area B a Milano. Più difficile da rivendere a chi vive in città.")
        if classe.startswith("Euro 6 (a"):
            return ("attenzione", "Diesel Euro 6 a-b-c (probabile): entra in Area B fino al 30/9/2028, poi no. Vale meno per i clienti di Milano.")
        return ("ok", "Diesel Euro 6 d (probabile): entra in Area B fino al 30/9/2030.")
    if classe in ("Euro 2", "Euro 3"):
        return ("male", f"Benzina {classe}: NON entra già oggi in Area B a Milano.")
    if classe == "Euro 4":
        return ("attenzione", "Benzina Euro 4: entra in Area B fino al 30/9/2028, poi no.")
    return ("ok", "Entra in Area B a Milano (benzina Euro 5/6, ibride e GPL seguono la benzina).")


def bollo_lombardia(kw: int | None, anno: int | None, fuel: str | None, adesso: int) -> int | None:
    if not kw or (fuel or "").lower() in ("elettrica",):
        return None
    b = 2.58 * min(kw, 100) + 3.87 * max(0, kw - 100)
    if kw > 185 and anno:
        eta = adesso - anno
        f = 1.0 if eta < 5 else .6 if eta < 10 else .3 if eta < 15 else .15 if eta < 20 else 0
        b += 20 * (kw - 185) * f
    return int(round(b))


def passaggio_milano(kw: int | None) -> tuple[int, int] | None:
    if not kw:
        return None
    ipt = (150.81 if kw <= 53 else 3.5119 * kw) * 1.3          # IPT Milano (+30%)
    base = ipt + 100                                            # ACI, bolli, diritti
    return int(round(base / 10) * 10), int(round((base + 200) / 10) * 10)   # da soli / con agenzia


def controlli(fuel: str | None, cambio: str | None, km: int | None, anno: int | None, adesso: int) -> list[str]:
    f, c, km, eta = (fuel or "").lower(), (cambio or "").lower(), km or 0, (adesso - anno) if anno else 0
    out = []
    if f.startswith("diesel"):
        out.append("Diesel: chiedi se fa tanta città. Controlla FAP/DPF ed EGR, spia motore e fumo allo scarico in accelerazione.")
    if f == "gpl" or "gpl" in f:
        out.append("GPL: guarda la data della bombola sul certificato (dura 10 anni) e prova la commutazione benzina/GPL.")
    if f == "metano":
        out.append("Metano: controlla la scadenza della revisione delle bombole (ogni 4 anni).")
    if f == "ibrida":
        out.append("Ibrida: chiedi lo stato della batteria e se c'è ancora garanzia della casa sulla parte ibrida.")
    if f == "elettrica":
        out.append("Elettrica: fatti dare lo stato di salute della batteria (SOH) e verifica la garanzia sulla batteria.")
    if "autom" in c:
        out.append("Cambio automatico: in prova controlla che non dia strappi da fermo e in retromarcia; chiedi quando è stato cambiato l'olio del cambio.")
    if km >= 120000:
        out.append("Oltre 120.000 km: chiedi fatture di distribuzione, frizione, ammortizzatori e freni.")
    elif km >= 80000:
        out.append("Oltre 80.000 km: chiedi se ha fatto la distribuzione e quando è previsto il prossimo tagliando grosso.")
    if eta >= 8:
        out.append("Auto con più di 8 anni: guarda ruggine sotto scocca e nei passaruota, e lo stato dei tubi freno.")
    out.append("Sempre: libretto dei tagliandi, numero di chiavi, verifica gratuita km e revisioni sul Portale dell'Automobilista, visura PRA per fermi o ipoteche.")
    return out


def da_sapere(b: dict, d: dict, ora=None) -> dict:
    adesso = (ora or datetime.now(timezone.utc)).year
    anno, km, kw, fuel = b.get("year"), b.get("mileage_km"), b.get("power_kw"), b.get("fuel")
    voci = []
    if anno and km is not None:
        eta = max(1, adesso - anno)
        kma = int(round(km / eta / 1000.0)) * 1000
        if km / eta < 5000 and eta >= 4:
            voci.append({"livello": "attenzione", "titolo": "Chilometri molto bassi",
                         "testo": f"Circa {_e(kma)} km all'anno: pochi per l'età. Verifica i km sul Portale dell'Automobilista (revisioni) e sui tagliandi."})
        elif km / eta > 30000:
            voci.append({"livello": "attenzione", "titolo": "Chilometri alti per l'età",
                         "testo": f"Circa {_e(kma)} km all'anno: probabile uso da lavoro o autostrada. Pesa sul prezzo di rivendita."})
        else:
            voci.append({"livello": "ok", "titolo": "Chilometri nella norma", "testo": f"Circa {_e(kma)} km all'anno."})
    classe = classe_euro(fuel, anno)
    ab = area_b(fuel, classe)
    if ab:
        voci.append({"livello": ab[0], "titolo": f"Area B Milano · {classe} (probabile, dall'anno)", "testo": ab[1]})
    nf = d.get("n_foto")
    if nf is not None and nf < 5:
        voci.append({"livello": "attenzione", "titolo": "Poche foto",
                     "testo": f"Solo {nf} foto: chiedi altre foto (interni, sottoscocca, cruscotto acceso) prima di muoverti."})
    if d.get("commerciante") or b.get("seller_type") == "commerciante":
        voci.append({"livello": "info", "titolo": "Venditore commerciante",
                     "testo": "Chiedi fattura e se l'auto è in regime del margine: cambia il conto dell'IVA."})
    bollo = bollo_lombardia(kw, anno, fuel, adesso)
    pas = passaggio_milano(kw)
    return {"voci": voci, "classe_euro": classe, "bollo_anno": bollo, "passaggio": pas,
            "kw": kw, "cv": int(round(kw * 1.36)) if kw else None, "controlli": controlli(fuel, b.get("gearbox"), km, anno, adesso),
            "nota": "Classe Euro stimata dall'anno di immatricolazione (quella vera è sul libretto). Bollo: tariffe "
                    "Lombardia; passaggio: IPT della provincia di Milano, prima cifra fai da te, seconda con agenzia."}


# ---------------------------------------------------------------- piano di rivendita (dopo l'acquisto)
COSTO_GIORNO = 8          # € al giorno di auto ferma: assicurazione, spazio, soldi bloccati (stima, modificabile)
PASSI = (0, 10, 15, 20, 30, 45)


def _listino(x: float) -> int:
    """Prezzo da annuncio: tondo alle centinaia sotto (9.900, 12.400)."""
    return int(x // 100) * 100 if x >= 1000 else _round50(x)


def piano_rivendita(d: dict, prezzo: int, acquisto: int, margine, rv: dict | None, ds: dict | None, b: dict) -> dict | None:
    """Dopo averla comprata: a che prezzo metterla, quando abbassare, quanto si guadagna giorno per giorno.
    Probabilità di vendita stimate dal tempo medio di vendita del modello (curva con quella mediana); prezzi sopra il
    mercato allungano i tempi. Guadagno = prezzo di vendita − acquisto (chiusura probabile) − costi della scheda −
    giorni di giacenza."""
    pv = d.get("pv") if isinstance(d.get("pv"), dict) else {}
    M = pv.get("M")
    R = pv.get("R_finale") or pv.get("R")
    if not M or not R or margine is None or not acquisto:
        return None
    costi = max(0, int(R - prezzo - margine))                     # costi e riserva della scheda (stessi del margine)
    pareggio = acquisto + costi
    mediana = float(rv["giorni_vendita"]) if rv else 30.0
    veloce = float(rv["giorni_veloci"]) if rv else 12.0
    trend = (rv or {}).get("trend_prezzi_pct") or 0
    base = max(M, R)
    livelli = [base * 1.04, base * 1.0, base * .98, base * .96, max(R * .99, pareggio + 300), max(R * .96, pareggio + 100)]
    if trend < -3:                                                 # prezzi in calo: si scende prima
        livelli = [x * .99 for x in livelli]
    passi, prec = [], None
    for g, x in zip(PASSI, livelli):
        prezzo_l = _listino(x)
        if prec is not None and prezzo_l >= prec:
            prezzo_l = prec - 100
        if prezzo_l <= pareggio:
            break
        prec = prezzo_l
        sopra = max(0.0, prezzo_l / M - 1)
        med_eff = mediana * (1 + 4 * sopra)                       # +5% sul mercato ≈ +20% di tempo
        guadagno = int(prezzo_l * .97 - acquisto - costi)        # ~3% di sconto al cliente in trattativa
        passi.append({"dal_giorno": g, "prezzo_annuncio": prezzo_l, "prezzo_atteso_cliente": _round50(prezzo_l * .97),
                      "guadagno_lordo": guadagno, "_med": med_eff})
    if not passi:
        return None
    # probabilità di essere venduta entro la fine di ogni passo (curva con mediana med_eff del passo)
    p_ancora, cum = 1.0, 0.0
    for i, ps in enumerate(passi):
        fine = passi[i + 1]["dal_giorno"] if i + 1 < len(passi) else ps["dal_giorno"] + 15
        durata = fine - ps["dal_giorno"]
        q = 1 - 0.5 ** (durata / max(ps.pop("_med"), 3))
        venduta_qui = p_ancora * q
        cum += venduta_qui
        p_ancora -= venduta_qui
        giorno_tipo = ps["dal_giorno"] + durata / 2
        ps.update(fino_al_giorno=fine, prob_vendita_nel_passo=round(venduta_qui, 2), prob_venduta_entro=round(cum, 2),
                  costo_giacenza=int(giorno_tipo * COSTO_GIORNO),
                  guadagno_netto=int(ps["guadagno_lordo"] - giorno_tipo * COSTO_GIORNO))
    atteso = sum(ps["prob_vendita_nel_passo"] * ps["guadagno_netto"] for ps in passi) / max(cum, .01)
    giorni_attesi = sum(ps["prob_vendita_nel_passo"] * (ps["dal_giorno"] + (ps["fino_al_giorno"] - ps["dal_giorno"]) / 2)
                        for ps in passi) / max(cum, .01)

    consigli = []
    if rv and rv.get("concorrenza") == "tanta":
        consigli.append("Tanta concorrenza: parti già vicino al prezzo di mercato e cura bene le foto, se no resta ferma.")
    if rv and rv.get("velocita") in ("molto_veloce", "veloce"):
        consigli.append(f"Si vende in fretta (le più rapide in {rv['giorni_veloci']} giorni): puoi partire un po' più alto e tenere il prezzo per 10 giorni.")
    if rv and rv.get("velocita") == "lenta":
        consigli.append("Si vende lentamente: metti un prezzo giusto da subito, ogni settimana ferma costa circa "
                        f"{_e(7 * COSTO_GIORNO)} €.")
    if trend < -2:
        consigli.append(f"Prezzi in calo ({trend}% nell'ultimo mese): non aspettare, vendila il prima possibile.")
    elif trend > 2:
        consigli.append(f"Prezzi in salita (+{trend}% nell'ultimo mese): puoi tenere il prezzo qualche giorno in più.")
    for v in (ds or {}).get("voci") or []:
        if v.get("titolo", "").startswith("Area B") and v.get("livello") in ("male", "attenzione"):
            consigli.append("Per l'Area B: pubblicala anche fuori Milano (Brianza, Bergamo, Lodi, Pavia) e scrivilo nell'annuncio; "
                            "a Milano città la cercano meno.")
    anno, fuel = b.get("year"), (b.get("fuel") or "").lower()
    if prezzo and prezzo < 7000:
        clienti = "neopatentati, seconda auto di famiglia, chi cerca un'auto economica per il lavoro"
    elif fuel in ("ibrida", "elettrica"):
        clienti = "chi fa città e vuole consumare poco o entrare in Area B/C senza pensieri"
    elif fuel.startswith("diesel"):
        clienti = "chi fa tanti km (autostrada, pendolari, lavoro)"
    else:
        clienti = "famiglie e chi cerca un'auto affidabile per tutti i giorni"
    nome = d.get("nome") or "Auto"
    titolo = f"{nome} {anno or ''} · {_e(b.get('mileage_km') or 0)} km · tagliandata · garanzia 12 mesi".replace("  ", " ")
    annuncio = {
        "titolo": titolo,
        "punti": ["Prezzo che finisce con 900/400 (es. " + _e(passi[0]["prezzo_annuncio"]) + " €): nelle ricerche per fascia esce prima.",
                  "Almeno 20 foto alla luce del giorno: 3/4 davanti, 3/4 dietro, interni, cruscotto acceso con i km, gomme, bagagliaio.",
                  "Scrivi tagliandi, n° proprietari, garanzia 12 mesi e possibilità di finanziamento.",
                  "Pubblicala su Subito e AutoScout24 nello stesso giorno; rinnova l'annuncio (o mettilo in evidenza) al primo ribasso.",
                  "Rispondi entro 5 minuti alle richieste: chi risponde subito vende molto di più."],
        "clienti": clienti,
    }
    dopo = ["Garanzia legale: vendendo a un privato devi garantire l'auto per almeno 12 mesi. Metti da parte "
            f"circa {_e(max(150, round(passi[0]['prezzo_annuncio'] * .02 / 50) * 50))} € per piccoli interventi.",
            "Passaggio di proprietà: di solito lo paga il cliente; tienine conto in trattativa.",
            "Prima della consegna: tagliando, pulizia, revisione se scade entro 6 mesi; consegna con 2 chiavi e libretto tagliandi.",
            "Dopo la vendita: un messaggio dopo 7 giorni per sapere come va e chiedere una recensione; il cliente contento torna e porta amici."]
    vendita_attesa = sum(ps["prob_vendita_nel_passo"] * ps["prezzo_atteso_cliente"] for ps in passi) / max(cum, .01)
    return {"passi": passi, "pareggio": pareggio, "costi_scheda": costi, "acquisto": acquisto,
            "prezzo_vendita_atteso": _round50(vendita_attesa),
            "guadagno_atteso": int(atteso), "giorni_attesi": int(round(giorni_attesi)),
            "prob_venduta_30g": next((ps["prob_venduta_entro"] for ps in passi if ps["fino_al_giorno"] >= 30), passi[-1]["prob_venduta_entro"]),
            "costo_giorno": COSTO_GIORNO, "consigli": consigli, "annuncio": annuncio, "dopo_vendita": dopo,
            "base_tempi": ("tempo medio di vendita di questo modello: " + str(rv["giorni_vendita"]) + " giorni") if rv
                          else "nessun dato sul modello: tempi stimati su 30 giorni",
            "nota": "Stime: acquisto al prezzo di chiusura probabile, costi della scheda, circa 3% di sconto al cliente e "
                    f"{COSTO_GIORNO} € al giorno di giacenza. Le probabilità vengono dai tempi di vendita del modello e salgono "
                    "o scendono col prezzo."}


# ---------------------------------------------------------------- numeri lampo e parere scritto (7/11 pomeriggio)

def flash(prezzi: dict, piano: dict | None, rv: dict | None, voto: int, margine, prezzo: int) -> list[dict]:
    """Pochi numeri da leggere in 3 secondi, in cima alla scheda."""
    out = [{"k": "Offri", "v": prezzi.get("apertura"), "tipo": "eur"},
           {"k": "Chiudi a", "v": prezzi.get("chiusura_probabile"), "tipo": "eur"}]
    if prezzi.get("massimo") is not None:
        out.append({"k": "Non oltre", "v": prezzi["massimo"], "tipo": "eur", "tono": "warn"})
    if piano:
        out.append({"k": "Rivendi a", "v": piano.get("prezzo_vendita_atteso"), "tipo": "eur"})
        out.append({"k": "Guadagno netto", "v": piano.get("guadagno_atteso"), "tipo": "eur",
                    "tono": "good" if (piano.get("guadagno_atteso") or 0) > 0 else "warn"})
        out.append({"k": "Venduta in", "v": f"~{piano.get('giorni_attesi')} gg", "tipo": "txt"})
    elif prezzi.get("guadagno_a_chiusura") is not None:
        out.append({"k": "Guadagno", "v": prezzi["guadagno_a_chiusura"], "tipo": "eur", "tono": "good"})
    out.append({"k": "Trattativa", "v": f"{voto}/10", "tipo": "txt", "tono": "good" if voto >= 7 else "warn" if voto <= 3 else ""})
    if rv:
        out.append({"k": "Concorrenza", "v": {"poca": "Poca", "media": "Media", "tanta": "Tanta"}.get(rv.get("concorrenza"), "—"),
                    "tipo": "txt", "tono": "good" if rv.get("concorrenza") == "poca" else "warn" if rv.get("concorrenza") == "tanta" else ""})
    return out


def parere(d, b, prezzo, M, margine, soglia, giorni, giorni_ann, rib, sceso, prezzi, prev, piano, rv, ds, voto, vend,
           n_piu_bassi, ripubblicata) -> dict:
    """Il nostro parere in 3-4 paragrafi brevi: l'auto e il prezzo, il venditore e la trattativa, la rivendita
    (con i casi reali venduti), le attenzioni. I numeri chiave sono in grassetto (**...**) per l'app."""
    nome = d.get("nome") or "Quest'auto"
    pv = d.get("pv") if isinstance(d.get("pv"), dict) else {}
    R = pv.get("R_finale") or pv.get("R")
    ok_margine = margine is not None and soglia and margine >= soglia
    veloce = rv and rv.get("velocita") in ("molto_veloce", "veloce")
    rischio = [v for v in (ds or {}).get("voci") or [] if v.get("livello") in ("male", "attenzione")]
    if ok_margine and (veloce or not rv) and not any(v.get("livello") == "male" for v in rischio):
        giudizio, tono = "Affare da prendere", "good"
    elif ok_margine:
        giudizio, tono = "Buona occasione, con qualche attenzione", "good"
    elif margine is not None and margine > 0 and voto >= 6:
        giudizio, tono = "Diventa un affare trattando", "info"
    else:
        giudizio, tono = "Solo se la prendi al prezzo giusto", "warn"
    par = []

    # 1. l'auto e il prezzo
    anno, km = b.get("year"), b.get("mileage_km")
    t = f"{nome}" + (f" del {anno}" if anno else "") + (f" con {_e(km)} km" if km is not None else "") + f", chiesta **{_e(prezzo)} €**."
    if M:
        diff = M - prezzo
        if diff > 0.03 * M:
            t += f" Per anno e chilometri vale circa **{_e(M)} €**: sei **{_e(diff)} € sotto mercato**."
        elif diff < -0.03 * M:
            t += f" Per anno e chilometri vale circa **{_e(M)} €**: il prezzo è alto, l'affare si fa solo trattando."
        else:
            t += f" È in linea con il mercato (circa **{_e(M)} €**)."
    par.append(t)

    # 2. venditore e trattativa
    if giorni_ann < 3 and not ripubblicata:
        t = "Annuncio appena uscito: chi chiama per primo la prende, quindi muoviti oggi e non tirare troppo."
    else:
        t = f"Online da **{int(giorni)} giorni**"
        if rib:
            t += f", ha già abbassato {rib} {'volta' if rib == 1 else 'volte'} (**−{_e(sceso)} €**)"
        else:
            t += ", mai abbassata"
        if ripubblicata:
            t += ", tolta e ripubblicata"
        t += ". " + ("Venditore che vuole chiudere." if voto >= 7 else "C'è spazio per trattare." if voto >= 5
                     else "Non aspettarti grandi sconti.")
    t += f" Proponi **{_e(prezzi['apertura'])} €**"
    if n_piu_bassi:
        t += f" facendo notare le {n_piu_bassi} uguali in vendita a meno"
    t += f", chiudi intorno a **{_e(prezzi['chiusura_probabile'])} €**"
    if prezzi.get("massimo") is not None:
        t += f"; fino a **{_e(prezzi['massimo'])} €** ci guadagni ancora il minimo"
    t += "."
    if prev and prev.get("probabilita_ribasso", 0) >= .4 and giorni_ann >= 3:
        t += f" Probabile un altro ribasso a breve ({int(prev['probabilita_ribasso'] * 100)}%)."
    par.append(t)

    # 3. rivendita
    riv = None
    if R:
        listino = _listino(R * 1.04 + 99)                      # un po' sopra: margine per la trattativa del cliente
        costi = max(0, int(R - prezzo - (margine or 0))) if margine is not None else 0
        giorni_v = rv["giorni_vendita"] if rv else None
        guad = int(R - prezzi["chiusura_probabile"] - costi - (giorni_v or 20) * COSTO_GIORNO) if margine is not None else None
        riv = {"annuncio": listino, "vendita": _round50(R), "giorni": giorni_v, "guadagno": guad}
        t = f"Rimettila in vendita a **{_e(listino)} €**: si chiude intorno a **{_e(R)} €**"
        if giorni_v:
            t += f", in circa **{giorni_v} giorni**"
        t += "."
        if guad is not None:
            t += f" Ti restano circa **{_e(guad)} €** netti."
        if rv:
            if rv.get("concorrenza") == "tanta":
                t += f" Ci sono molte uguali in vendita ({rv['in_vendita']}): foto curate e risposte veloci fanno la differenza."
            elif rv.get("concorrenza") == "poca":
                t += " Poca concorrenza: puoi tenere il prezzo."
            if rv.get("trend_prezzi_pct") is not None and rv["trend_prezzi_pct"] <= -3:
                t += f" Prezzi in calo ({rv['trend_prezzi_pct']}% nel mese): meglio non tenerla ferma."
        par.append(t)

    # 4. attenzioni
    if rischio or vend == "commerciante":
        corti = [v["titolo"].replace(" (probabile, dall'anno)", "").replace("Area B Milano · ", "Area B per ")
                 .replace("Chilometri", "chilometri").replace("Poche foto", "poche foto") for v in rischio[:3]]
        if vend == "commerciante":
            corti.append("venditore professionale (chiedi fattura e regime IVA)")
        par.append("Attenzione a: " + "; ".join(corti) + ".")
    return {"giudizio": giudizio, "tono": tono, "paragrafi": par, "rivendi": riv}


def mercato(d: dict, rv: dict | None) -> dict | None:
    """Il mercato di quest'auto in un riquadro: quante ne abbiamo confrontate, prezzo basso/medio/alto, tempi."""
    val = d.get("valutazione") if isinstance(d.get("valutazione"), dict) else {}
    pv = d.get("pv") if isinstance(d.get("pv"), dict) else {}
    prezzi = sorted(int(c["prezzo"]) for c in (val.get("confronti") or []) if c.get("prezzo"))
    out = {"confrontate": pv.get("n") or val.get("n") or len(prezzi)}
    if prezzi:
        out.update(basso=prezzi[0], medio=_round50(prezzi[len(prezzi) // 2] if len(prezzi) % 2
                                                    else (prezzi[len(prezzi) // 2 - 1] + prezzi[len(prezzi) // 2]) / 2),
                   alto=prezzi[-1])
    elif pv.get("M"):
        out.update(medio=_round50(pv["M"]))
    if rv:
        out.update(in_vendita=rv["in_vendita"], vendute_mese=round(rv["vendute_mese"]), giorni_vendita=rv["giorni_vendita"],
                   giorni_veloci=rv["giorni_veloci"], trend=rv.get("trend_prezzi_pct"), casi=rv.get("casi") or [],
                   concorrenza=rv.get("concorrenza"))
    return out


STORICO_SQL = """
WITH stessa AS (
  SELECT l2.id, l2.source, l2.status, l2.first_seen_at
  FROM listings l JOIN listings l2 ON l2.vehicle_id = l.vehicle_id AND l.vehicle_id IS NOT NULL
  WHERE l.id = %(id)s AND (l2.id = l.id OR (l2.status = 'ripubblicato' AND l2.first_seen_at < l.first_seen_at)))
SELECT s.id, s.source, s.status, pe.event, pe.price_eur, pe.at
FROM stessa s JOIN price_events pe ON pe.listing_id = s.id
WHERE pe.event IN ('apparso', 'prezzo', 'riapparso', 'ripubblicato')
ORDER BY pe.at"""
DOPPIONI_SQL = """
SELECT l2.source, l2.price_eur FROM listings l JOIN listings l2 ON l2.vehicle_id = l.vehicle_id AND l2.id <> l.id
WHERE l.id = %(id)s AND l.vehicle_id IS NOT NULL AND l2.status = 'attivo' LIMIT 5"""
BASE_SQL = """SELECT id, first_seen_at, price_eur, seller_type, description, title, price_flags, year, mileage_km,
                     source, vehicle_id, make, model, fuel, power_kw, gearbox, province FROM listings WHERE id = %(id)s"""

SEGNALI_TESTO = [
    (re.compile(r"\bnon\s+(?:è\s+)?trattabil|\bprezzo\s+fisso\b|\bno\s+perditempo\b.*\bno\s+trattativ|\bnon\s+tratto\b", re.I),
     -2, "scrive che il prezzo non è trattabile"),
    (re.compile(r"(?<!non )\btrattabil|\baccetto\s+(?:offerte|proposte)|\bvaluto\s+(?:offerte|proposte)", re.I),
     1, "scrive che il prezzo è trattabile"),
    (re.compile(r"\burgent|\bvendo\s+(?:subito|in\s+fretta)|\bliberare\b|\bsvendo\b|\bcausa\s+(?:trasferimento|trasloco|"
                r"inutilizzo|cambio|acquisto|lavoro|separazione)|\btrasferimento\s+all'?\s*estero|\binutilizz|\beredit",
                re.I), 2, "ha fretta di vendere (urgente, trasferimento, inutilizzo...)"),
    (re.compile(r"\bdoppia\s+auto|\bseconda\s+auto|\bho\s+(?:già\s+)?(?:preso|comprato)\s+(?:un'?altra|nuova)", re.I),
     1, "ha già un'altra auto"),
]


def _e(n) -> str:
    """Euro all'italiana: 7.100"""
    return f"{int(round(n)):,}".replace(",", ".")


def _round50(x: float) -> int:
    return int(round(x / 50.0)) * 50


def _giorni(da, ora=None) -> float:
    if not da:
        return 0.0
    ora = ora or datetime.now(timezone.utc)
    if da.tzinfo is None:
        da = da.replace(tzinfo=timezone.utc)
    return max(0.0, (ora - da).total_seconds() / 86400.0)


def storico(righe: list[dict], lid: int) -> dict:
    """Riassunto dello storico prezzi (annuncio attuale + annunci precedenti della stessa auto)."""
    punti, ribassi, rialzi, prima = [], 0, 0, None
    ripubblicato = any(r["id"] != lid for r in righe)
    for r in righe:
        p = r.get("price_eur")
        if not p:
            continue
        if prima is not None and p != prima:
            if p < prima:
                ribassi += 1
            else:
                rialzi += 1
        if prima is None or p != prima or r["event"] in ("riapparso", "ripubblicato"):
            punti.append({"data": r["at"].date().isoformat() if hasattr(r["at"], "date") else str(r["at"])[:10],
                          "prezzo": int(p), "evento": r["event"], "fonte": r.get("source"),
                          "variazione": (int(p) - int(prima)) if prima else None})
        prima = p
    iniziale = next((x["prezzo"] for x in punti), None)
    return {"punti": punti[-12:], "ribassi": ribassi, "rialzi": rialzi, "ripubblicato": ripubblicato,
            "prezzo_iniziale": iniziale, "inizio": righe[0]["at"] if righe else None,
            "ultimo_cambio": next((r["at"] for r in reversed(righe) if r["event"] == "prezzo"), None)}


def massimo_per_margine(prezzo: int, margine: int | None, soglia_fn) -> int | None:
    """Prezzo più alto a cui il margine resta sopra la soglia (i costi non cambiano col prezzo d'acquisto)."""
    if margine is None or prezzo <= 0:
        return None
    p = prezzo + margine - soglia_fn(prezzo)
    for _ in range(4):                        # la soglia dipende dal prezzo: punto fisso
        p = prezzo + margine - soglia_fn(max(p, 500))
    return _round50(math.floor(p / 50.0) * 50) if p > 0 else None


def scheda(conn, lid: int, d: dict, soglia_fn, ora=None) -> dict | None:
    """La scheda trattativa per un'auto del sito (d = dettaglio dell'auto)."""
    b = conn.execute(BASE_SQL, {"id": lid}).fetchone()
    if not b:
        return None
    b = dict(b)
    st = storico([dict(r) for r in conn.execute(STORICO_SQL, {"id": lid}).fetchall()], lid)
    dop = [dict(r) for r in conn.execute(DOPPIONI_SQL, {"id": lid}).fetchall()]
    return calcola(b, st, dop, d, soglia_fn, ora=ora)


def calcola(b: dict, st: dict, dop: list, d: dict, soglia_fn, ora=None, gruppi: dict | None = None,
            modelli=None, attivi=None, esempi=None) -> dict:
    prezzo = int(d.get("prezzo") or b.get("price_eur") or 0)
    pv = d.get("pv") if isinstance(d.get("pv"), dict) else {}
    val = d.get("valutazione") if isinstance(d.get("valutazione"), dict) else {}
    M = pv.get("M") or val.get("M")
    margine = pv.get("margine") if pv.get("margine") is not None else val.get("margine")
    soglia = pv.get("soglia") or val.get("soglia") or (soglia_fn(prezzo) if prezzo else None)
    vend = "commerciante" if b.get("seller_type") == "commerciante" else "privato"
    giorni = _giorni(st.get("inizio") or b.get("first_seen_at"), ora)
    giorni_annuncio = _giorni(b.get("first_seen_at"), ora)
    rib = int(st.get("ribassi") or 0)
    iniziale = st.get("prezzo_iniziale") or prezzo
    sceso = (iniziale - prezzo) if iniziale and iniziale > prezzo else 0

    motivi, voto = [], 3.0
    if giorni >= 60:
        voto += 3; motivi.append(f"in vendita da {int(giorni)} giorni")
    elif giorni >= 30:
        voto += 2; motivi.append(f"in vendita da {int(giorni)} giorni")
    elif giorni >= 14:
        voto += 1; motivi.append(f"in vendita da {int(giorni)} giorni")
    elif giorni < 3:
        voto -= 1; motivi.append("appena pubblicata")
    if rib:
        voto += min(rib, 2)
        motivi.append(f"ha già abbassato {rib} {'volta' if rib == 1 else 'volte'}" + (f" (−{_e(sceso)} €)" if sceso else ""))
    if st.get("ultimo_cambio") and _giorni(st["ultimo_cambio"], ora) <= 7 and rib:
        voto += 0.5; motivi.append("l'ultimo ribasso è di questa settimana")
    if st.get("ripubblicato"):
        voto += 1; motivi.append("l'ha già tolta e ripubblicata")
    testo = f"{b.get('title') or ''}\n{b.get('description') or ''}"
    for rx, punti, frase in SEGNALI_TESTO:
        if rx.search(testo):
            voto += punti; motivi.append(frase)
    if "trattabile" in (b.get("price_flags") or []) and not any("trattabile" in m for m in motivi):
        voto += 1; motivi.append("prezzo segnato come trattabile")
    if M and prezzo > M * 1.03:
        voto += 1; motivi.append(f"chiede più del mercato ({_e(prezzo - M)} € sopra)")
    confronti = val.get("confronti") or []
    piu_bassi = sorted([c for c in confronti if c.get("prezzo") and c["prezzo"] < prezzo], key=lambda c: c["prezzo"])
    if len(confronti) >= 8:
        voto += 0.5; motivi.append(f"{len(confronti)} auto uguali in vendita: ha concorrenza")
    if dop:
        motivi.append(f"pubblicata anche su {', '.join(sorted({x['source'] for x in dop}))}")
    if vend == "commerciante":
        voto -= 0.5; motivi.append("è un commerciante: di solito tratta meno di un privato")

    g = gruppo(vend, giorni, rib, gruppi)
    previsione = None
    if g:
        voto += 1 if g["quota_scende"] >= .5 else (-.5 if g["quota_scende"] < .2 else 0)
        previsione = {
            "probabilita_ribasso": round(g["quota_scende"], 2),
            "prezzo_annuncio_previsto": _round50(prezzo * (1 + min(0.0, g["resto_med"]))),
            "prezzo_annuncio_basso": _round50(prezzo * (1 + min(0.0, g["resto_p25"]))),
            "ribasso_previsto_pct": round(-min(0.0, g["resto_med"]) * 100, 1),
            "giorni_alla_vendita": int(round(g["giorni_med"])),
            "base": f"{g['n']} annunci simili spariti negli ultimi 90 giorni ({g['chiave']})"}
    voto = int(max(1, min(10, round(voto))))

    chiusura = previsione["prezzo_annuncio_previsto"] if previsione else prezzo
    # offerta d'apertura: sotto la chiusura probabile, più aggressiva se il venditore è "morbido"
    sconto_apertura = 0.03 + 0.008 * voto                       # dal 3,8% (voto 1) all'11% (voto 10)
    apertura = _round50(min(chiusura, prezzo) * (1 - sconto_apertura))
    massimo = massimo_per_margine(prezzo, margine, soglia_fn)
    if massimo is not None:
        apertura = min(apertura, massimo)
    prezzi = {"apertura": apertura, "chiusura_probabile": chiusura, "massimo": massimo,
              "guadagno_a_chiusura": (margine + prezzo - chiusura) if margine is not None else None,
              "guadagno_ad_apertura": (margine + prezzo - apertura) if margine is not None else None}

    argomenti = []
    if giorni >= 14:
        argomenti.append({"testo": f"È in vendita da {int(giorni)} giorni" + (f" e ha già abbassato {rib} volte" if rib > 1 else
                                                                             " e ha già abbassato una volta" if rib == 1 else ""),
                          "euro": None})
    if piu_bassi:
        x = piu_bassi[0]
        argomenti.append({"testo": f"Ci sono {len(piu_bassi)} auto uguali a meno, la più economica a {_e(x['prezzo'])} €",
                          "euro": prezzo - int(x["prezzo"]), "link": x.get("url")})
    if pv.get("difetto_costo") or val.get("difetto_costo"):
        argomenti.append({"testo": f"Difetto scritto nell'annuncio{(' (' + str(pv.get('difetto')) + ')') if pv.get('difetto') else ''}",
                          "euro": int(pv.get("difetto_costo") or val.get("difetto_costo"))})
    pezzi = d.get("pezzi") or []
    tot_pezzi = sum(int(p.get("a") or 0) for p in pezzi if isinstance(p, dict))
    if tot_pezzi:
        argomenti.append({"testo": f"Da sistemare: {len(pezzi)} {'pezzo' if len(pezzi) == 1 else 'pezzi'}", "euro": tot_pezzi})
    anno, km = b.get("year"), b.get("mileage_km")
    adesso = (ora or datetime.now(timezone.utc)).year
    if anno and adesso - anno >= 4 and (adesso - anno - 4) % 2 == 0:
        argomenti.append({"testo": "La revisione potrebbe scadere quest'anno: chiedi quando è stata fatta", "euro": 80})
    if km and km >= 90000:
        argomenti.append({"testo": f"A {_e(km)} km chiedi se ha già fatto distribuzione, frizione e freni: se no, sono costi da far pesare",
                          "euro": None})
    if M and prezzo > M:
        argomenti.append({"testo": f"Il prezzo di mercato per anno e km è circa {_e(M)} €", "euro": int(prezzo - M)})

    nuova_e_buona = giorni_annuncio < 3 and margine is not None and soglia and margine >= soglia * 1.5
    if nuova_e_buona:
        quando = {"azione": "chiama_subito", "testo": "Non tirare troppo sul prezzo: è appena uscita ed è sotto mercato, rischi che la prenda un altro."}
    elif previsione and previsione["probabilita_ribasso"] >= .5 and margine is not None and soglia and margine < soglia:
        quando = {"azione": "aspetta", "testo": f"C'è il {int(previsione['probabilita_ribasso'] * 100)}% di probabilità che abbassi ancora, "
                                                f"di solito entro {previsione['giorni_alla_vendita']} giorni. Ricontrollala tra qualche giorno."}
    else:
        quando = {"azione": "offri", "testo": "Parti dall'offerta d'apertura: ha margine per scendere."}

    nome = d.get("nome") or "auto"
    fonte = d.get("fonte") or "internet"
    arg_msg = [a["testo"].lower() for a in argomenti[:2] if not a["testo"].startswith("La revisione")]
    messaggio = (f"Buongiorno, ho visto la sua {nome} su {fonte}. È ancora disponibile? Sono un commerciante: posso "
                 f"venire a vederla in giornata e, se è come da annuncio, pagare subito con bonifico istantaneo. "
                 + (f"Considerando che {' e che '.join(arg_msg)}, " if arg_msg else "")
                 + f"le posso offrire {_e(apertura)} €. Mi faccia sapere, grazie.")

    ds = da_sapere(b, d, ora)
    rv = rivendita(b.get("make"), b.get("model"), b.get("fuel"), b.get("year"), b.get("mileage_km"),
                   modelli=modelli, attivi=attivi, esempi=esempi)
    piano = piano_rivendita(d, prezzo, chiusura, margine, rv, ds, b)
    return {
        "piano": piano,
        "mercato": mercato(d, rv),
        "parere": parere(d, b, prezzo, M, margine, soglia, giorni, giorni_annuncio, rib, sceso, prezzi, previsione,
                         piano, rv, ds, voto, vend, len(piu_bassi), st.get("ripubblicato")),
        "voto": voto, "voto_testo": ("molto disponibile a trattare" if voto >= 8 else "disponibile a trattare" if voto >= 6
                                     else "poco margine di trattativa" if voto >= 4 else "difficile da trattare"),
        "motivi": motivi,
        "giorni_in_vendita": int(giorni), "giorni_annuncio_attuale": int(giorni_annuncio),
        "ribassi": rib, "rialzi": st.get("rialzi") or 0, "ripubblicato": bool(st.get("ripubblicato")),
        "prezzo_iniziale": iniziale, "sceso_euro": sceso,
        "sceso_pct": round(sceso / iniziale * 100, 1) if iniziale and sceso else 0,
        "storico": st.get("punti") or [], "doppioni": dop, "venditore": vend,
        "concorrenti": len(confronti), "concorrenti_piu_bassi": len(piu_bassi),
        "previsione": previsione, "prezzi": prezzi,
        "da_sapere": ds, "rivendita": rv, "argomenti": argomenti[:6], "quando": quando,
        "messaggio": messaggio,
        "nota": "Previsioni dagli annunci spariti (probabilmente venduti) negli ultimi 90 giorni: vediamo l'ultimo prezzo "
                "dell'annuncio, non il prezzo finale della trattativa.",
    }
