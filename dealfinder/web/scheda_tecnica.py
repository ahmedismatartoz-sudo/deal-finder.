"""12/11 (cliente: "sul sito l'auto deve essere più completa"): scheda tecnica dell'auto per il sito.

- `aggiungi(conn, lid, d)`: nella scheda di un'auto mette d["scheda"] (dati principali, altri dati, optional); una sola
  lettura per chiave primaria, mai un errore verso la scheda. Chiavi SOLO dentro d["scheda"]: d["allestimento"] è del
  ramo dei margini (dizionario con livello e descrizione) e qui non si tocca.
- `arricchisci_elenco(items)`: nell'elenco del sito (circa 600 auto) aggiunge it["allestimento_testo"] e normalizza
  it["cambio"]; una sola query su listings per id, solo le colonne che servono (niente dati_auto: l'elenco resta
  leggero in memoria).

Mai il telefono o altri dati del venditore: si leggono solo i dati del veicolo (dealfinder/core/dati_auto.py).
Senza la colonna dati_auto (migrazione 046 non ancora applicata) tutto funziona con le sole colonne dell'annuncio.
SCHEDA_TECNICA=0 spegne tutto (default acceso).
"""
from __future__ import annotations

import logging
import os

from ..core import dati_auto as DA

log = logging.getLogger("scovo")


def acceso() -> bool:
    return os.environ.get("SCHEDA_TECNICA", "1") == "1"


def _num(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def _potenza(d: dict) -> str | None:
    k, c = d.get("potenza_kw"), d.get("potenza_cv")
    if k and c:
        return f"{k} kW · {c} CV"
    if k:
        return f"{k} kW"
    return f"{c} CV" if c else None


def _riga(chiave: str, etichetta: str, valore, largo: bool = False) -> dict | None:
    if valore in (None, "", [], {}, False):
        return None
    r = {"k": chiave, "label": etichetta, "v": str(valore)}
    if largo:
        r["largo"] = True
    return r


PRINCIPALI = 6     # dati visibili subito nel blocchetto (2 colonne su telefono, 3 su computer)
# in quest'ordine; trazione e colore solo se mancano i primi
ORDINE_PRINCIPALI = ("allestimento", "cambio", "cilindrata", "potenza", "carrozzeria", "proprietari", "classe_euro",
                     "trazione", "colore")


def scheda(colonne: dict, dati: object, base: dict | None = None) -> dict:
    """Scheda compatta per il sito (cliente 12/11 sera: "un piccolo blocchetto"): `principali` = al massimo 6 dati
    visibili subito; `altri` = il resto, dietro "Altri dati e optional"; `optional` = elenco semplice di etichette.
    Solo le righe con un valore (niente righe vuote). Anno, km e carburante sono già in cima alla scheda: qui no."""
    d = DA.unisci(colonne, dati)
    cv = d.get("potenza_cv")
    tutte = {
        "allestimento": _riga("allestimento", "Allestimento", d.get("allestimento")),
        "cambio": _riga("cambio", "Cambio", d.get("cambio")),
        "cilindrata": _riga("cilindrata", "Cilindrata", f"{_num(d['cilindrata'])} cm³" if d.get("cilindrata") else None),
        "potenza": _riga("potenza", "Potenza", f"{cv} CV" if cv else _potenza(d)),
        "carrozzeria": _riga("carrozzeria", "Carrozzeria", d.get("carrozzeria")),
        "proprietari": _riga("proprietari", "Proprietari", d.get("proprietari")),
        "classe_euro": _riga("classe_euro", "Euro", (d.get("classe_euro") or "").replace("Euro ", "") or None),
        "trazione": _riga("trazione", "Trazione", d.get("trazione")),
        "colore": _riga("colore", "Colore", d.get("colore")),
    }
    principali = [tutte[k] for k in ORDINE_PRINCIPALI if tutte[k]][:PRINCIPALI]
    visti = {r["k"] for r in principali}
    altri = [
        _riga("versione", "Versione", d.get("versione"), largo=True),
        _riga("potenza_kw", "Potenza", _potenza(d) if d.get("potenza_kw") and cv else None),
        _riga("immatricolazione", "Immatricolazione", d.get("immatricolazione")),
        _riga("porte", "Porte", d.get("porte")),
        _riga("posti", "Posti", d.get("posti")),
        _riga("marce", "Marce", d.get("marce")),
        _riga("consumo", "Consumo", d.get("consumo")),
        _riga("co2", "CO₂", d.get("co2")),
        _riga("tagliandi", "Tagliandi", d.get("tagliandi")),
        _riga("garanzia", "Garanzia", d.get("garanzia")),
        _riga("non_fumatori", "Non fumatori", "Sì" if d.get("non_fumatori") else None),
    ] + [tutte[k] for k in ORDINE_PRINCIPALI if tutte[k] and k not in visti]
    voci = []
    for x in d.get("optional") or []:
        if isinstance(x, str) and x.lower() not in {v.lower() for v in voci}:
            voci.append(x)
    return {"principali": principali, "altri": [r for r in altri if r],
            "optional": {"n": len(voci), "voci": voci},
            "allestimento": d.get("allestimento"), "cambio": d.get("cambio"), "letto_il": d.get("letto_il")}


_COLONNE = "l.id, l.make, l.model, l.version_raw, l.title, l.gearbox, l.power_kw, l.year, l.mileage_km, l.fuel"


def _leggi(conn, sql_con: str, sql_senza: str, params) -> list[dict]:
    """Prova con la colonna dati_auto; se non c'è ancora (migrazione 046), senza."""
    try:
        return [dict(r) for r in conn.execute(sql_con, params).fetchall()]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return [dict(r) for r in conn.execute(sql_senza, params).fetchall()]


def aggiungi(conn, lid: int, d: dict) -> dict:
    """Scheda di un'auto: d["scheda"]. Mai blocca la scheda."""
    if not acceso():
        return d
    try:
        righe = _leggi(conn, f"SELECT {_COLONNE}, l.dati_auto FROM listings l WHERE l.id = %s",
                       f"SELECT {_COLONNE} FROM listings l WHERE l.id = %s", (lid,))
        if not righe:
            return d
        r = righe[0]
        d["scheda"] = scheda(r, r.get("dati_auto"), d)
    except Exception as e:
        log.warning("SCHEDA_TECNICA %s: %s", lid, str(e)[:200])
        try:
            conn.rollback()
        except Exception:
            pass
    return d


def arricchisci_elenco(items: list[dict]) -> int:
    """it["allestimento_testo"] e cambio normalizzato per le auto dell'elenco (una query). Ritorna quante hanno
    l'allestimento. L'allestimento viene dalle sole colonne (stessa fonte della scheda: allestimento_testo)."""
    if not acceso() or not items:
        return 0
    try:
        from ..db import connect
        ids = [int(it["id"]) for it in items if it.get("id") is not None]
        if not ids:
            return 0
        with connect() as conn:
            righe = conn.execute("SELECT l.id, l.make, l.model, l.version_raw, l.title, l.gearbox FROM listings l "
                                 "WHERE l.id = ANY(%s)", (ids,)).fetchall()
        per_id = {r["id"]: r for r in righe}
        n = 0
        for it in items:
            r = per_id.get(it.get("id"))
            if not r:
                continue
            all_ = DA.allestimento_testo(r.get("version_raw"), r.get("title"), r.get("make"), r.get("model"))
            if all_:
                it["allestimento_testo"] = all_
                n += 1
            cambio = DA.norm_cambio(r.get("gearbox"))
            if cambio:
                it["cambio"] = cambio
        return n
    except Exception as e:
        log.warning("SCHEDA_TECNICA elenco: %s", str(e)[:200])
        return 0
