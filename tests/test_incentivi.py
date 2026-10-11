"""Test di accettazione del modulo Incentivi (motore di regole deterministico)."""
import copy
import datetime as dt
import json
import re
from pathlib import Path

from incentivi import motore

OGGI = dt.date(2026, 10, 11)
ROOT = Path(__file__).resolve().parents[1] / "incentivi"
CAMPI = ["stato", "sede", "ateco", "addetti", "data_costituzione"]


def _profilo(demo="due_dipendenti", **kw):
    P = {p["demo_id"]: p for p in json.loads((ROOT / "data" / "profili_demo.json").read_text())}
    p = copy.deepcopy(P[demo])
    p["campi"] = {k: {"stato": "verificato", "fonte": "test"} for k in CAMPI}
    p.update(kw)
    return p


def _by_id(res):
    return {r["id"]: r for r in res["risultati"]}


def test_impresa_non_lombarda_misure_regionali_non_fa_per_te():
    p = _profilo(sede={"comune": "Torino", "provincia": "TO", "regione": "Piemonte"})
    res = _by_id(motore.valuta_tutto(p, {"investimento": 20000, "ordini": False, "assume": True}, OGGI))
    r = res["rl-formazione-continua-4-ed"]
    assert r["etichetta"] == "non_fa_per_te"
    assert "Lombardia" in r["motivo"] and "Piemonte" in r["motivo"]
    terr = next(x for x in r["regole"] if x["id"] == "territorio")
    assert terr["esito"] == "ko" and terr["riferimento"].startswith("http")


def test_ateco_escluso_rinnova_veicoli():
    res = _by_id(motore.valuta_tutto(_profilo(), {}, OGGI))
    r = res["rl-rinnova-veicoli-2026-2027"]
    assert r["etichetta"] == "non_fa_per_te"
    regola = next(x for x in r["regole"] if x["id"] == "ateco_non_escluso")
    assert regola["esito"] == "ko" and "45.11.01" in regola["dato"]
    assert regola["riferimento"].startswith("http")


def test_ordine_gia_firmato_esclude():
    risposte = {"investimento": 30000, "ordini": True, "officina": True}
    res = _by_id(motore.valuta_tutto(_profilo("officina"), risposte, OGGI))
    vietano = [m["id"] for m in motore.catalogo()["misure"] if m.get("vieta_ordini_prima_domanda")]
    assert vietano
    for mid in vietano:
        r = res[mid]
        assert r["etichetta"] == "non_fa_per_te"
        regola = next(x for x in r["regole"] if x["id"] == "niente_ordini")
        assert regola["esito"] == "ko" and regola["dato"] == "sì"


def test_nessuna_misura_utile():
    p = _profilo(stato="cessata")
    res = motore.valuta_tutto(p, {}, OGGI)
    assert res["titolo"] == "Oggi non c'è nulla di utile per te."
    assert res["sottotitolo"] == "Ti avvisiamo appena esce qualcosa."


def test_ogni_importo_ha_il_calcolo():
    for demo in ("titolare", "due_dipendenti", "officina"):
        res = motore.valuta_tutto(_profilo(demo), {"investimento": 15000, "assume": True, "officina": True,
                                                  "ordini": False}, OGGI)
        for r in res["risultati"]:
            b = r["beneficio"]
            if b["valore"]:
                assert b["calcolo"], r["id"]


def test_prestiti_e_garanzie_non_sono_soldi_regalati():
    res = _by_id(motore.valuta_tutto(_profilo(), {"investimento": 50000, "ordini": False}, OGGI))
    for r in res.values():
        if r["tipo"] in ("finanziamento_agevolato", "garanzia"):
            assert r["beneficio"]["valore"] is None and not r["beneficio"]["conta"]
            assert "restituito" in r["beneficio"]["condizione"]
            assert r["etichetta"] != "conviene"


def test_magazzino_mai_finanziato_salvo_garanzia():
    for m in motore.catalogo()["misure"]:
        if m.get("copre_magazzino"):
            assert m["tipo_beneficio"] in ("garanzia", "finanziamento_agevolato"), m["id"]


def test_bandi_chiusi_mai_disponibili():
    res = motore.valuta_tutto(_profilo("officina"), {"investimento": 50000, "assume": True, "officina": True,
                                                     "ordini": False}, OGGI)
    for r in res["risultati"]:
        if r["bando"]["stato"] in motore.CHIUSI:
            assert r["etichetta"] == "non_fa_per_te", r["id"]


def test_voucher_doppia_transizione_chiuso_il_29_luglio():
    r = _by_id(motore.valuta_tutto(_profilo(), {"investimento": 8000}, OGGI))["cciaa-milomb-voucher-doppia-transizione-2026"]
    assert r["bando"]["stato"] == "esaurito" and "29/07/2026" in r["bando"]["etichetta"]


def test_mai_hai_diritto():
    testi = [(ROOT / "data" / "misure.json").read_text()]
    for f in (ROOT / "static").glob("*"):
        if f.suffix in (".html", ".js", ".css"):
            testi.append(f.read_text())
    out = json.dumps(motore.valuta_tutto(_profilo(), {"investimento": 10000, "assume": True}, OGGI), ensure_ascii=False)
    testi.append(out)
    for t in testi:
        assert not re.search(r"hai\s+diritto", t, re.I)


def test_domande_al_massimo_quattro():
    d = motore.domande_utili(_profilo("titolare"), OGGI)
    assert 0 < len(d) <= 4 and all(x["id"] in motore.DOMANDE for x in d)


def test_ogni_regola_ha_fonte():
    for m in motore.catalogo()["misure"]:
        assert m["fonte_url"].startswith("http"), m["id"]
        for r in m["regole"]:
            assert r["riferimento"] and r["testo"]


def test_profilo_da_domande_semplici():
    from incentivi import profilo
    p = profilo.applica_dichiarato(profilo.vuoto(None), {"provincia": "MB", "attivita": "auto_officina", "anno": "2012",
                                                          "dipendenti": "3-9", "forma": "societa", "artigiana": "no"}, OGGI)
    assert p["sede"]["regione"] == "Lombardia" and p["addetti"] == 6 and p["stato"] == "attiva"
    assert [a["codice"] for a in p["ateco"]] == ["45.11.01", "45.20.10"]
    assert p["campi"]["sede"]["stato"] == "dichiarato" and profilo.domande_mancanti(p) == []
    res = motore.valuta_tutto(p, {"investimento": 0, "assume": False}, OGGI)
    r = _by_id(res)["rl-formazione-continua-4-ed"]
    terr = next(x for x in r["regole"] if x["id"] == "territorio")
    assert terr["esito"] == "ok" and terr["fonte"].startswith("dichiarato")


def test_altro_settore_edile():
    from incentivi import profilo
    p = profilo.applica_dichiarato(profilo.vuoto(None), {"provincia": "MI", "attivita": "altro", "ateco": "43.21",
                                                          "anno": "2005", "dipendenti": "1-2", "forma": "individuale"}, OGGI)
    assert p["ateco"][0]["codice"] == "43.21"
    rv = _by_id(motore.valuta_tutto(p, {}, OGGI))["rl-rinnova-veicoli-2026-2027"]
    assert next(x for x in rv["regole"] if x["id"] == "ateco_non_escluso")["esito"] == "ok"


def test_lettura_visura_caricata():
    from incentivi import profilo
    testo = """VISURA ORDINARIA SOCIETA' DI PERSONE
Denominazione AUTO PROVA S.N.C. DI ROSSI
Forma giuridica societa' in nome collettivo
Partita IVA 12485671007
Sede legale VIA ROMA 1
20099 SESTO SAN GIOVANNI (MI)
Data atto di costituzione 15/01/2008
Data d'inizio dell'attivita' 01/02/2008
Stato attivita' attiva
Codice ATECO 45.11.01
Codice: 45.20.10 - riparazioni meccaniche
Addetti al 30/06/2026 5"""
    p, trovati = profilo.leggi_visura(testo, OGGI)
    assert p["piva"] == "12485671007" and p["sede"]["provincia"] == "MI" and p["sede"]["regione"] == "Lombardia"
    assert [a["codice"] for a in p["ateco"]] == ["45.11.01", "45.20.10"]
    assert p["data_costituzione"] == "2008-01-15" and p["stato"] == "attiva" and p["addetti"] == 5
    assert set(trovati) >= {"sede", "ateco", "data_costituzione", "stato", "addetti"}
    assert profilo.domande_mancanti(p) == []          # visura letta: nessuna domanda sull'impresa


def test_visura_impaginazione_infocamere():
    """Etichette e valori su righe diverse, ditta individuale, codici ATECO 2025."""
    from incentivi import profilo
    testo = """Camera di Commercio Industria Artigianato e Agricoltura di MILANO MONZA BRIANZA LODI
VISURA ORDINARIA IMPRESA INDIVIDUALE
ROSSI MARIO
DATI ANAGRAFICI
Indirizzo Sede
CINISELLO BALSAMO (MI)
VIA DEI MILLE 10 CAP 20092
Numero REA
MI - 1234567
Codice fiscale
RSSMRA70A01F205X
Partita IVA
12485671007
Data iscrizione
10/03/2015
ATTIVITA'
Stato attività
attiva
Data inizio attività
01/04/2015
Attività esercitata
commercio di autoveicoli usati
Codice ATECO 2025
47.81.10 - commercio al dettaglio di autoveicoli
Addetti
al 30/06/2026
Totale 2
Impresa artigiana: no"""
    p, trovati = profilo.leggi_visura(testo, OGGI)
    assert p["ragione_sociale"] == "ROSSI MARIO" and p["piva"] == "12485671007"
    assert p["sede"] == {"comune": "Cinisello Balsamo", "provincia": "MI", "regione": "Lombardia"}
    assert p["forma_giuridica"].lower().startswith("impresa individuale")
    assert p["ateco"][0]["codice"] == "45.11" and p["ateco2025"][0]["codice"] == "47.81.10"
    assert p["data_costituzione"] == "2015-04-01" and p["stato"] == "attiva" and p["addetti"] == 2
    assert profilo.domande_mancanti(p) == [] and p["extra"]["artigiana"] is False
