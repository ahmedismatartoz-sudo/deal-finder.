"""Costruisce incentivi/data/misure.json dalle schede di ricerca (incentivi/data/ricerca/*.json).

Ogni misura riceve le sue REGOLE ESPLICITE (lista "regole"): sono scritte qui sotto in modo
leggibile, derivate dai campi verificati della scheda più le correzioni manuali (CORREZIONI).
Il file prodotto è quello che usa il motore: ogni modifica si rivede nel diff di git.

    python -m incentivi.strumenti.costruisci_catalogo
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
FILES = ["lavoro", "formazione_inail", "investimenti", "lombardia"]

ZES = ["Abruzzo", "Molise", "Campania", "Basilicata", "Sicilia", "Puglia", "Calabria", "Sardegna", "Marche", "Umbria"]

# Correzioni manuali alle schede (dopo rilettura): solo dati che cambiano le regole.
CORREZIONI: dict[str, dict] = {
    "dl62-bonus-zes-2026": {"territorio": {"livello": "nazionale", "regioni": ZES, "province": [],
                                           "nota": "Solo sedi nelle regioni della ZES unica"}},
    # Unioncamere Lombardia: aperto 28/7/2026 ore 11, chiuso 29/7/2026 ore 10:09 (fondi e lista d'attesa esauriti)
    "cciaa-milomb-voucher-doppia-transizione-2026": {
        "stato": "esaurito",
        "date": {"apertura": "2026-07-28", "chiusura": None, "chiusura_effettiva": "2026-07-29"},
        "prossima_edizione_prevista": "PREVISIONE: nuova edizione probabile tra maggio e luglio 2027 (2024: 22/5, 2025: 6/5, 2026: 28/7); si chiude in minuti o ore, quindi la domanda va preparata prima.",
        "_dubbio_extra": "Chiusura del 29/7/2026 ore 10:09 da pagina Unioncamere Lombardia (bandi-e-incentivi-alle-imprese/dettaglio-bando/bando-voucher-doppia-transizione-lombardia-2026); le pagine della CCIAA MiMBLo risultavano ancora 'aperto' ad agosto."},
    "iperammortamento-2026": {"serve": ["investimento", "beni_40"]},
    "dl62-stabilizzazione-under35-2026": {"serve": ["dipendenti", "stabilizzazione"]},
    "conto-termico-3-imprese": {"serve": ["investimento", "officina", "intervento_energetico"]},
    "fondartigianato-invito-1-2026": {"serve": ["dipendenti", "formazione", "artigiana", "fondo_fondartigianato"]},
    "forte-avviso-1-26-cnfc": {"serve": ["dipendenti", "formazione", "fondo_forte"]},
    "forte-avviso-3-25-voucher": {"serve": ["dipendenti", "formazione", "fondo_forte"]},
    "rl-nuova-impresa-2026": {"serve": ["nuova_impresa", "investimento"]},
    "bonus-retrofit-gpl-metano-2026": {"serve": ["officina", "impianti_gpl_metano"]},
}

# Spiegazione semplice dei documenti (checklist)
DOCUMENTI = {
    "spid": ("SPID del titolare", "Serve per entrare nei portali (INPS, Regione, Camera di Commercio)."),
    "firma_digitale": ("Firma digitale", "Per firmare la domanda: chiavetta o firma remota del legale rappresentante."),
    "pec": ("PEC dell'impresa", "Indirizzo di posta certificata attivo e registrato in Camera di Commercio."),
    "durc": ("DURC regolare", "Il documento che dice che sei in regola con INPS e INAIL."),
    "diritto_camerale": ("Diritto camerale pagato", "La quota annuale della Camera di Commercio deve essere pagata."),
    "polizza_catastrofali": ("Polizza rischi catastrofali", "Obbligatoria per le imprese: senza, molti bandi la escludono."),
    "dichiarazione_de_minimis": ("Dichiarazione de minimis", "Elenco degli aiuti pubblici ricevuti negli ultimi 3 anni."),
    "preventivi": ("Preventivi", "Preventivi dei fornitori per le spese che vuoi fare (meglio due)."),
    "visura": ("Visura camerale", "Recuperata in automatico dalla partita IVA."),
    "contratto_assunzione": ("Contratto di assunzione", "Il contratto firmato e la comunicazione di assunzione."),
    "piano_formativo": ("Piano formativo", "Cosa impari, ore e chi fa il corso: lo prepara l'ente di formazione."),
    "perizia": ("Perizia tecnica", "Relazione di un tecnico sui beni acquistati (per i beni più costosi)."),
    "fatture": ("Fatture e pagamenti", "Fatture intestate all'impresa e pagamenti tracciabili (bonifico)."),
    "iscrizione_fondo_interprofessionale": ("Adesione al fondo interprofessionale",
                                            "Si sceglie nel modello UniEmens: chiedi al consulente del lavoro."),
}

AZIONE_DOC = {
    "preventivi": "Chiedi 2 preventivi", "firma_digitale": "Attiva la firma digitale", "spid": "Attiva lo SPID",
    "polizza_catastrofali": "Controlla la polizza catastrofali", "durc": "Scarica il DURC",
    "pec": "Controlla la PEC", "dichiarazione_de_minimis": "Prepara la dichiarazione de minimis",
    "contratto_assunzione": "Parla con il consulente del lavoro prima di assumere",
    "piano_formativo": "Chiedi un piano formativo a un ente accreditato",
    "iscrizione_fondo_interprofessionale": "Chiedi al consulente del lavoro a quale fondo aderisci",
    "diritto_camerale": "Controlla il pagamento del diritto camerale",
}


UFFICIALI = ("gov.it", "inps.it", "inail.it", "regione.lombardia.it", "camcom.it", "unioncamerelombardia.it",
             "gse.it", "fondoforte.it", "fondartigianato.it", "normattiva.it", "gazzettaufficiale.it",
             "agenziaentrate", "fondidigaranzia.it", "europa.eu", "invitalia.it", "fondimpresa.it")


def ufficiale(url: str | None) -> bool:
    from urllib.parse import urlparse
    host = urlparse(url or "").netloc.lower()
    return any(host.endswith(d) or d in host for d in UFFICIALI)


def regole_per(m: dict) -> list[dict]:
    """Regole esplicite e verificabili, nell'ordine in cui si mostrano."""
    rif = m.get("fonte_url")
    R: list[dict] = []

    def add(id_, tipo, testo, **params):
        R.append({"id": id_, "tipo": tipo, "testo": testo, "params": params, "riferimento": rif})

    add("misura_disponibile", "stato_misura", "La misura è aperta (o sempre attiva) oggi")
    add("impresa_attiva", "impresa_attiva", "L'impresa risulta attiva al Registro Imprese")
    t = m.get("territorio") or {}
    if t.get("province"):
        add("territorio", "provincia_in", "Sede o unità locale in provincia di " + ", ".join(t["province"]),
            province=t["province"])
    elif t.get("regioni"):
        add("territorio", "regione_in", "Sede o unità locale in " + ", ".join(t["regioni"]), regioni=t["regioni"])
    esclusi = list(m.get("ateco_esclusi") or [])
    if m.get("esclude_rivenditori_auto") and not any(e.startswith("45.11") for e in esclusi):
        esclusi += ["45.11"]
    if esclusi:
        add("ateco_non_escluso", "ateco_non_escluso", "Il codice ATECO non è tra quelli esclusi dal bando",
            esclusi=esclusi, nota=m.get("ateco_note"))
    if m.get("ateco_ammessi"):
        add("ateco_ammesso", "ateco_ammesso", "Il codice ATECO è tra quelli ammessi", ammessi=m["ateco_ammessi"])
    dims = [d for d in (m.get("dimensioni") or []) if d in ("micro", "piccola", "media", "grande")]
    if dims and set(dims) != {"micro", "piccola", "media", "grande"}:
        add("dimensione", "dimensione_in", "Dimensione dell'impresa: " + ", ".join(dims), ammesse=dims)
    if m.get("anzianita_min_mesi"):
        add("anzianita", "anzianita_min", f"Impresa attiva da almeno {m['anzianita_min_mesi']} mesi",
            mesi=m["anzianita_min_mesi"])
    if m.get("costituita_dal"):
        add("nuova_impresa", "costituita_dal", f"Impresa avviata dal {m['costituita_dal']}", data=m["costituita_dal"])
    if m.get("costituita_entro"):
        add("costituita_entro", "costituita_entro", f"Impresa avviata entro il {m['costituita_entro']}",
            data=m["costituita_entro"])
    serve = m.get("serve") or []
    if "dipendenti" in serve:
        add("dipendenti", "addetti_min", "Hai almeno un dipendente", minimo=1)
    if "assunzione" in serve:
        add("assunzione", "risposta_vera", "Pensi di assumere qualcuno", domanda="assume")
    if "officina" in serve:
        add("officina", "risposta_vera", "Hai un'officina", domanda="officina")
    if "investimento" in serve:
        smin = (m.get("calcolo") or {}).get("spesa_minima")
        add("investimento", "investimento_min",
            "Vuoi fare un investimento" + (f" di almeno {smin:,} €".replace(",", ".") if smin else ""),
            minimo=smin or 1)
    if "artigiana" in serve:
        add("artigiana", "dato_mancante", "L'impresa è iscritta all'albo artigiani", campo="artigiana")
    if "fondo_forte" in serve:
        add("fondo", "dato_mancante", "Aderisci al fondo For.Te.", campo="fondo_forte")
    if "fondo_fondartigianato" in serve:
        add("fondo", "dato_mancante", "Aderisci a Fondartigianato", campo="fondo_fondartigianato")
    if "assunzione" in serve:
        add("lavoratore", "dato_mancante", "La persona da assumere ha i requisiti (età, periodo senza lavoro, contratto)",
            campo="lavoratore_requisiti")
    if "stabilizzazione" in serve:
        add("stabilizzazione", "dato_mancante", "Hai un dipendente under 35 a tempo determinato da trasformare a tempo indeterminato",
            campo="stabilizzazione")
    if "intervento_energetico" in serve:
        add("energia", "dato_mancante", "L'intervento sostituisce il riscaldamento con una pompa di calore (o simile)",
            campo="intervento_energetico")
    if "beni_40" in serve:
        add("beni_40", "dato_mancante", "I beni sono nuovi, tecnologici e interconnessi (beni 4.0)", campo="beni_40")
    if "veicolo_commerciale_nuovo" in serve:
        add("veicolo", "dato_mancante", "Vuoi comprare un veicolo commerciale NUOVO per l'attività",
            campo="veicolo_commerciale")
    if "impianti_gpl_metano" in serve:
        add("gpl", "dato_mancante", "Sei un'officina abilitata a installare impianti GPL/metano", campo="gpl")
    if m.get("vieta_ordini_prima_domanda"):
        add("niente_ordini", "risposta_falsa", "Non hai già firmato ordini o pagato acconti per queste spese",
            domanda="ordini")
    return R


def build() -> dict:
    misure = []
    for f in FILES:
        for m in json.loads((DATA / "ricerca" / f"{f}.json").read_text()):
            corr = dict(CORREZIONI.get(m["id"], {}))
            extra = corr.pop("_dubbio_extra", None)
            m.update(corr)
            if extra:
                m["dubbi"] = list(m.get("dubbi") or []) + [extra]
            if not m.get("fonte_url") and m.get("altre_fonti"):
                m["fonte_url"] = m["altre_fonti"][0]
            m["fonte_ufficiale"] = ufficiale(m.get("fonte_url"))
            m["regole"] = regole_per(m)
            m["documenti_dettaglio"] = [{"id": d, "nome": DOCUMENTI.get(d, (d, ""))[0],
                                         "spiegazione": DOCUMENTI.get(d, (d, ""))[1],
                                         "azione": AZIONE_DOC.get(d)} for d in m.get("documenti") or []]
            m["area"] = f
            misure.append(m)
    lampo = json.loads((DATA / "ricerca" / "lampo.json").read_text())
    body = json.dumps(misure, ensure_ascii=False, sort_keys=True)
    versione = hashlib.sha256(body.encode()).hexdigest()[:10]
    out = {"versione": versione, "aggiornato": max(m.get("ultima_verifica") or "" for m in misure),
           "misure": misure, "lampo": lampo}
    (DATA / "misure.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    o = build()
    print(f"catalogo {o['versione']}: {len(o['misure'])} misure, {len(o['lampo'])} bandi lampo")
