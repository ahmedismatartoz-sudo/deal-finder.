"""Motivazione della selezione e controlli prima dell'acquisto.
Testo generato da regole a partire dai numeri: niente AI, niente invenzioni."""
from __future__ import annotations

from ..ai.damage import PARTS, SEVERE_FLAGS
from ..core.models import Listing
from .engine import Valuation

REASON_LABELS = {
    "stato_non_verificato": "Le foto non bastano per confermare che l'auto sia senza danni",
    "danni_da_verificare": "Danni presenti: costo ricambi stimato, da verificare dal vivo",
    "confronti_solo_livello_largo": "Confronti trovati solo tra auto meno simili",
    "versione_incerta": "Versione esatta incerta",
    "segnali_anomali": "Segnali anomali sul prezzo o sull'annuncio",
    "nessun_confronto": "Nessuna auto simile in banca dati",
    "mercato_privato_stimato_da_commercianti": "Prezzo privato stimato dai soli annunci di commercianti",
    "mercato_commercianti_stimato_da_privati": "Prezzo di rivendita stimato dai soli annunci di privati",
}
FRAUD_LABELS = {
    "prezzo_troppo_basso": "Prezzo molto più basso del mercato: possibile truffa o errore",
    "leasing_o_rata": "Il prezzo potrebbe essere una rata o un anticipo",
    "prezzo_civetta": "Prezzo simbolico o finto",
    "importazione": "Auto d'importazione o da immatricolare",
    "plus_iva": "Prezzo indicato + IVA (già convertito IVA inclusa)",
}


def eur(v) -> str:
    return f"{int(round(v)):,} €".replace(",", ".") if v is not None else "n.d."


def reason_text(code: str) -> str:
    base, _, extra = code.partition(":")
    if base == "pochi_confronti":
        return f"Solo {extra} auto simili trovate (ne servono almeno 5)"
    if base == "confronti_dispersi":
        return f"Prezzi delle auto simili molto diversi tra loro (dispersione {float(extra):.0%})"
    if base == "dati_mancanti":
        return "Mancano dati essenziali: " + extra.replace(",", ", ")
    return REASON_LABELS.get(base, base)


def motivation(l: Listing, v: Valuation, parts: dict | None) -> list[str]:
    out = []
    if v.discount_vs_private is not None and v.private_median:
        out.append(f"Prezzo {eur(l.price_eur)}: {v.discount_vs_private:.0%} sotto la mediana del mercato privato "
                   f"({eur(v.private_median)}) su {v.n_comparables} auto simili.")
    if v.resale_prudent:
        out.append(f"Rivendita prudente a privati {eur(v.resale_prudent_private or v.resale_prudent)}: "
                   f"parte bassa dei prezzi dei privati per auto uguali, corretti per anno e km, meno la trattativa.")
    if v.liquidity_days is not None:
        out.append(f"Le auto simili restano online in media {v.liquidity_days:.0f} giorni.")
    if parts and parts.get("lines"):
        out.append(f"Ricambi stimati {eur(parts['parts_cost_low'])} – {eur(parts['parts_cost_high'])} "
                   f"({len(parts['lines'])} voci, manodopera esclusa).")
    for f in v.fraud_flags:
        out.append("Attenzione: " + FRAUD_LABELS.get(f, f))
    return out


def checks(l: Listing, v: Valuation, parts: dict | None, severe: list[str] | None = None) -> list[str]:
    c = [
        "Visura PRA: fermi amministrativi, ipoteche, intestatario coincidente con il venditore",
        "Km dell'ultima revisione sul Portale dell'Automobilista (serve la targa)",
        "Libretto di circolazione e numero di telaio uguale a quello sull'auto",
        "Storico tagliandi e fatture",
        "Diagnosi OBD: errori in memoria, spie",
        "Prova su strada: frizione/cambio, sterzo, freni, rumori",
    ]
    if l.damage_class in ("leggero", "medio", "sconosciuto") or l.damage_items:
        c.append("Spessimetro su tutti i pannelli: verniciature e stucco")
        c.append("Sottoscocca, longheroni e passaruota: segni di urti o raddrizzature")
        c.append("Spia airbag spenta dopo l'avvio")
    for it in l.damage_items:
        if it.part in ("paraurti_anteriore", "paraurti_posteriore"):
            c.append("Dietro il paraurti: traversa, staffe e sensori di parcheggio")
            break
    if parts and any("adas" in ln["part"] for ln in parts.get("lines", [])):
        c.append("Sistemi di assistenza (frenata automatica, mantenimento corsia): possibile calibrazione")
    if l.fuel == "diesel" and (l.mileage_km or 0) > 120_000:
        c.append("Diesel oltre 120.000 km: FAP/DPF, EGR, turbina, volano")
    if l.gearbox == "automatico":
        c.append("Cambio automatico: cambi marcia a freddo e a caldo, olio cambio")
    if "mileage_km" in l.missing_fields:
        c.append("Chilometri non dichiarati: farseli confermare con documenti")
    if l.source == "facebook":
        c.append("Annuncio Facebook: verificare identità del venditore, mai anticipi prima di vedere l'auto")
    if v.fraud_flags:
        c.append("Prezzo anomalo: vedere l'auto di persona prima di qualunque pagamento")
    for s in severe or []:
        c.append("Verificare: " + SEVERE_FLAGS.get(s, s))
    return c


def damage_labels(l: Listing) -> list[str]:
    out = []
    for d in l.damage_items:
        side = {"sx": " sx", "dx": " dx"}.get(d.side or "", "")
        out.append(f"{PARTS.get(d.part, d.part)}{side}: {d.action} ({d.severity}, da {d.source})")
    return out
