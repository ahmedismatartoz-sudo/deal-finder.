"""Guadagno netto stimato (12/11/2026): un SECONDO numero accanto al margine lordo, che resta quello di sempre e su
cui restano le soglie SOGLIE_PV.

    netto = R − sconto di trattativa in vendita − prezzo − passaggio − preparazione − garanzia − IVA sul margine
            − ricambi/difetti già stimati

- sconto di trattativa: quello osservato sui commercianti (sconto medio tra primo e ultimo prezzo dei commercianti
  venduti, pricing/rivendita.py) se c'è, altrimenti COSTI_NETTO["trattativa"] (3%) di R;
- preparazione per fascia di prezzo d'acquisto; garanzia legale in % di R con minimo e massimo;
- IVA nel regime del margine: (margine prima dell'IVA) × 22/122, solo se positivo.

Il prezzo è quello MOSTRATO sul sito (prezzo_sito / in_cima): il netto lo segue.
"""
from __future__ import annotations

COSTI_NETTO = {
    "trattativa": .03,                                  # sconto in vendita se non osservato sui commercianti
    "trattativa_min": .0,                               # limiti allo sconto osservato
    "trattativa_max": .10,
    "passaggio": 180,
    "preparazione": ((7000, 250), (15000, 400), (10**9, 600)),   # (prezzo d'acquisto fino a, euro)
    "garanzia": .015, "garanzia_min": 150, "garanzia_max": 600,
    "iva": 22,
}

NOMI = {"trattativa": "Sconto di trattativa in vendita", "passaggio": "Passaggio di proprietà",
        "preparazione": "Preparazione (pulizia, tagliando, ritocchi)", "garanzia": "Garanzia legale",
        "iva_margine": "IVA sul margine (22/122)", "ricambi_difetti": "Ricambi e difetti già stimati"}


def calcola(R, prezzo, ricambi_difetti: int = 0, sconto_osservato: float | None = None, c: dict | None = None) -> dict | None:
    """{"netto", "voci": {voce: euro}, "sconto_pct", "sconto_da"} oppure None senza R o prezzo."""
    c = c or COSTI_NETTO
    if not R or not prezzo:
        return None
    R, prezzo = float(R), float(prezzo)
    if sconto_osservato is not None:
        sc = max(c["trattativa_min"], min(c["trattativa_max"], float(sconto_osservato)))
        da = "osservato sui commercianti"
    else:
        sc, da = c["trattativa"], "valore standard"
    voci = {"trattativa": int(round(R * sc)), "passaggio": int(c["passaggio"]),
            "preparazione": int(next(e for fino, e in c["preparazione"] if prezzo <= fino)),
            "garanzia": int(round(min(c["garanzia_max"], max(c["garanzia_min"], R * c["garanzia"])))),
            "ricambi_difetti": int(ricambi_difetti or 0)}
    prima_iva = R - prezzo - sum(voci.values())
    voci["iva_margine"] = int(round(prima_iva * c["iva"] / (100 + c["iva"]))) if prima_iva > 0 else 0
    netto = int(round(prima_iva - voci["iva_margine"]))
    return {"netto": netto, "voci": voci, "sconto_pct": round(sc, 4), "sconto_da": da}


def per_auto(it: dict) -> dict:
    """Aggiunge `netto` e `costi_netto` all'auto del sito (elenco e scheda), dal pv dell'auto e dal prezzo MOSTRATO.
    Ricambi/difetti = costi della valutazione (totale_costi) + difetto tolto dal sito. Auto senza pv o senza margine:
    invariata."""
    pv = it.get("pv") if isinstance(it.get("pv"), dict) else None
    if not pv or pv.get("margine") is None or it.get("supercar"):
        return it
    R = pv.get("R_finale") or pv.get("R")
    rc = pv.get("rivendita_commercianti") if isinstance(pv.get("rivendita_commercianti"), dict) else {}
    costi = int(pv.get("totale_costi") or 0) + int(pv.get("difetto_costo") or 0)
    prezzo = (it.get("trattare") or {}).get("prezzo_obiettivo") or it.get("prezzo")
    r = calcola(R, prezzo, costi, rc.get("sconto"))
    if not r:
        return it
    return dict(it, netto=r["netto"], costi_netto=dict(r["voci"], sconto_pct=r["sconto_pct"], sconto_da=r["sconto_da"]))
