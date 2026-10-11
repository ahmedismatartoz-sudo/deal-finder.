"""Aggiornamento giornaliero (prototipo: controlli SIMULATI, con log visibile) e avvisi.

Cosa fa davvero:
  1. ricalcola lo stato di ogni misura dalle date del catalogo (apre, chiude, esaurito) e
     registra le variazioni rispetto al giorno prima (tabella inc_stato_misure);
  2. per ogni impresa con una valutazione salvata prepara gli avvisi utili
     ("apre domani", "aperto ora", "chiude tra 7 giorni", "chiuso"), solo per misure compatibili,
     al massimo 2 a settimana salvo urgenze; i canali email/push sono SIMULATI (nessun invio);
  3. elenca le fonti ufficiali da controllare: nel prototipo il controllo è simulato e lo dice
     nel log. Le nuove misure trovate richiedono sempre la revisione umana prima della pubblicazione.
"""
from __future__ import annotations

import datetime as dt

from . import motore
from .store import J, log

FONTI = [
    ("incentivi.gov.it (MIMIT)", "https://www.incentivi.gov.it/"),
    ("Bandi e Servizi – Regione Lombardia", "https://www.bandi.regione.lombardia.it/"),
    ("Camere di Commercio lombarde – bandi", "https://www.unioncamerelombardia.it/bandi-e-incentivi-alle-imprese"),
    ("INPS – messaggi e circolari", "https://www.inps.it/it/it/inps-comunica/atti/circolari-messaggi-e-normativa.html"),
    ("INAIL – bandi e agevolazioni", "https://www.inail.it/"),
    ("MIMIT – incentivi", "https://www.mimit.gov.it/it/incentivi"),
    ("GSE – Conto Termico", "https://www.gse.it/servizi-per-te/efficienza-energetica/conto-termico"),
    ("For.Te. – avvisi", "https://www.fondoforte.it/"),
    ("Fondartigianato – inviti", "https://www.fondartigianato.it/"),
    ("RNA – Registro Nazionale Aiuti (open data)", "https://www.rna.gov.it/"),
]
MAX_SETTIMANA = 2


def esegui(conn, oggi: dt.date | None = None) -> dict:
    oggi = oggi or dt.date.today()
    cat = motore.catalogo()
    stats = {"misure": len(cat["misure"]), "variazioni": 0, "avvisi": 0, "fonti": len(FONTI)}
    log(conn, "info", f"Aggiornamento del {oggi.strftime('%d/%m/%Y')}: catalogo versione {cat['versione']}, "
                      f"{len(cat['misure'])} misure")
    for nome, url in FONTI:
        log(conn, "info", f"Fonte {nome}: controllo SIMULATO nel prototipo (nessuna lettura automatica)", {"url": url})
    # 1) variazioni di stato calcolate dalle date
    prev = {r["misura_id"]: r for r in conn.execute("SELECT * FROM inc_stato_misure").fetchall()}
    cambi = []
    for m in cat["misure"]:
        sb = motore.stato_bando(m, oggi)
        p = prev.get(m["id"])
        if p is None or p["stato"] != sb["stato"]:
            conn.execute("INSERT INTO inc_stato_misure (misura_id, stato, etichetta, aggiornato) VALUES (%s,%s,%s,now()) "
                         "ON CONFLICT (misura_id) DO UPDATE SET stato=EXCLUDED.stato, etichetta=EXCLUDED.etichetta, aggiornato=now()",
                         (m["id"], sb["stato"], sb["etichetta"]))
            if p is not None:
                cambi.append((m, p["stato"], sb))
                stats["variazioni"] += 1
                livello = "novita"
                log(conn, livello, f"{m['nome']}: {p['stato']} → {sb['stato']} ({sb['etichetta']}). "
                                   f"Le chiusure si pubblicano con il link alla fonte; le aperture vanno riviste.",
                    {"misura": m["id"], "fonte": m.get("fonte_url")})
    # misure con dubbi aperti: da rivedere a mano
    da_rivedere = [m["id"] for m in cat["misure"] if m.get("dubbi") and m.get("stato") not in motore.CHIUSI]
    log(conn, "revisione", f"{len(da_rivedere)} misure aperte hanno dati da ricontrollare sulle fonti ufficiali "
                           "prima del lancio", {"misure": da_rivedere})
    # 2) avvisi alle imprese
    imprese = conn.execute(
        """SELECT DISTINCT ON (i.id) i.id, i.profilo, v.risposte FROM inc_imprese i
           JOIN inc_valutazioni v ON v.impresa_id=i.id ORDER BY i.id, v.creato DESC""").fetchall()
    for imp in imprese:
        stats["avvisi"] += _avvisi_impresa(conn, imp, oggi)
    conn.commit()
    log(conn, "info", f"Fine aggiornamento: {stats['variazioni']} variazioni, {stats['avvisi']} avvisi preparati")
    conn.commit()
    return stats


def _avvisi_impresa(conn, imp, oggi) -> int:
    res = motore.valuta_tutto(imp["profilo"], imp["risposte"] or {}, oggi)
    settimana = conn.execute("SELECT count(*) AS n FROM inc_avvisi WHERE impresa_id=%s AND NOT urgente "
                             "AND creato > now() - interval '7 days'", (imp["id"],)).fetchone()["n"]
    fatti = 0
    for r in res["risultati"]:
        if r["etichetta"] == "non_fa_per_te" and r["bando"]["stato"] not in motore.CHIUSI:
            continue
        sb = r["bando"]
        ap, ch = motore._d(sb.get("apertura")), motore._d(sb.get("chiusura"))
        avv = None
        if ap and ap == oggi + dt.timedelta(days=1):
            avv = ("apre_domani", True, f"Domani apre: {r['nome']}", "Tieni pronti i documenti: molti bandi chiudono in poche ore.")
        elif ap and ap == oggi:
            avv = ("aperto", True, f"Aperto ora: {r['nome']}", "Lo sportello è aperto da oggi.")
        elif ch and 0 <= (ch - oggi).days <= 7 and r["etichetta"] != "non_fa_per_te":
            avv = ("chiude_presto", True, f"Chiude il {ch.strftime('%d/%m/%Y')}: {r['nome']}",
                   f"Mancano {(ch - oggi).days} giorni. Prossima azione: {r.get('azione') or 'parla con il commercialista'}.")
        elif ch and 8 <= (ch - oggi).days <= 45 and r["etichetta"] != "non_fa_per_te":
            avv = ("prepara", False, f"Prepara i documenti: {r['nome']}",
                   f"Chiude il {ch.strftime('%d/%m/%Y')}. {r.get('azione') or ''}".strip())
        if not avv:
            continue
        tipo, urgente, titolo, testo = avv
        gia = conn.execute("SELECT 1 FROM inc_avvisi WHERE impresa_id=%s AND misura_id=%s AND tipo=%s",
                           (imp["id"], r["id"], tipo)).fetchone()
        if gia or (not urgente and settimana >= MAX_SETTIMANA):
            continue
        conn.execute("INSERT INTO inc_avvisi (impresa_id, misura_id, tipo, canale, titolo, testo, urgente) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (imp["id"], r["id"], tipo, "email e push (simulati)", titolo, testo, urgente))
        if not urgente:
            settimana += 1
        fatti += 1
    return fatti


def ultimo_oggi(conn) -> bool:
    row = conn.execute("SELECT 1 FROM inc_job_log WHERE messaggio LIKE 'Fine aggiornamento%%' "
                       "AND quando::date = current_date LIMIT 1").fetchone()
    return bool(row)
