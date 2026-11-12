"""12/11 (cliente: "sul sito l'auto deve essere più completa"): scheda tecnica dell'auto per il sito.

- `aggiungi(conn, lid, d)`: nella scheda di un'auto mette d["scheda"] (dati chiave, griglia, optional per gruppo) e
  d["allestimento"]; una sola lettura per chiave primaria, mai un errore verso la scheda.
- `arricchisci_elenco(items)`: nell'elenco del sito (circa 600 auto) aggiunge allestimento e cambio; una sola query
  su listings per id, solo i due campi (niente optional: l'elenco resta leggero in memoria).

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


def scheda(colonne: dict, dati: object, base: dict | None = None) -> dict:
    """Scheda pronta per il sito: solo le righe con un valore (niente righe vuote)."""
    base = base or {}
    d = DA.unisci(colonne, dati)
    km = base.get("km") if base.get("km") is not None else colonne.get("mileage_km")
    chiave = [
        _riga("anno", "Anno", base.get("anno") or colonne.get("year")),
        _riga("km", "Chilometri", f"{_num(km)} km" if km else None),
        _riga("carburante", "Carburante", DA._cap(base.get("carb") or colonne.get("fuel") or None)),
        _riga("cambio", "Cambio", d.get("cambio")),
        _riga("potenza", "Potenza", _potenza(d)),
        _riga("allestimento", "Allestimento", d.get("allestimento")),
    ]
    porte, posti = d.get("porte"), d.get("posti")
    griglia = [
        _riga("versione", "Versione", d.get("versione"), largo=True),
        _riga("immatricolazione", "Immatricolazione", d.get("immatricolazione")),
        _riga("carrozzeria", "Carrozzeria", d.get("carrozzeria")),
        _riga("porte", "Porte", porte),
        _riga("posti", "Posti", posti),
        _riga("colore", "Colore", d.get("colore")),
        _riga("cilindrata", "Cilindrata", f"{_num(d['cilindrata'])} cm³" if d.get("cilindrata") else None),
        _riga("marce", "Marce", d.get("marce")),
        _riga("trazione", "Trazione", d.get("trazione")),
        _riga("classe_euro", "Classe emissioni", d.get("classe_euro")),
        _riga("consumo", "Consumo", d.get("consumo")),
        _riga("co2", "Emissioni CO₂", d.get("co2")),
        _riga("proprietari", "Proprietari", d.get("proprietari")),
        _riga("tagliandi", "Tagliandi", d.get("tagliandi")),
        _riga("garanzia", "Garanzia", d.get("garanzia")),
        _riga("non_fumatori", "Non fumatori", "Sì" if d.get("non_fumatori") else None),
    ]
    voci = [x for x in (d.get("optional") or []) if isinstance(x, str)]
    gruppi = DA.raggruppa_optional(voci, d.get("optional_gruppi") if isinstance(d.get("optional_gruppi"), dict) else None)
    nomi = dict(DA.GRUPPI)
    out = {"chiave": [r for r in chiave if r], "dati": [r for r in griglia if r],
           "optional": {"n": sum(len(v) for v in gruppi.values()),
                        "gruppi": [{"id": g, "nome": nomi[g], "voci": v} for g, v in gruppi.items()]},
           "letto_il": d.get("letto_il")}
    return out


_COLONNE = "l.id, l.version_raw, l.title, l.gearbox, l.power_kw, l.year, l.mileage_km, l.fuel"


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
    """Scheda di un'auto: d["scheda"] e d["allestimento"]. Mai blocca la scheda."""
    if not acceso():
        return d
    try:
        righe = _leggi(conn, f"SELECT {_COLONNE}, l.dati_auto FROM listings l WHERE l.id = %s",
                       f"SELECT {_COLONNE} FROM listings l WHERE l.id = %s", (lid,))
        if not righe:
            return d
        r = righe[0]
        s = scheda(r, r.get("dati_auto"), d)
        d["scheda"] = s
        for x in s["chiave"]:
            if x["k"] == "allestimento":
                d["allestimento"] = x["v"]
            elif x["k"] == "cambio":
                d["cambio"] = x["v"]
    except Exception as e:
        log.warning("SCHEDA_TECNICA %s: %s", lid, str(e)[:200])
        try:
            conn.rollback()
        except Exception:
            pass
    return d


def arricchisci_elenco(items: list[dict]) -> int:
    """Allestimento e cambio per le auto dell'elenco (una query). Ritorna quante auto hanno l'allestimento."""
    if not acceso() or not items:
        return 0
    try:
        from ..db import connect
        ids = [int(it["id"]) for it in items if isinstance(it.get("id"), int)]
        if not ids:
            return 0
        corto = ("l.id, l.version_raw, l.title, l.gearbox, "
                 "COALESCE(l.dati_auto->'pagina'->>'allestimento', l.dati_auto->'ricerca'->>'allestimento') AS allestimento_letto, "
                 "COALESCE(l.dati_auto->'pagina'->>'cambio', l.dati_auto->'ricerca'->>'cambio') AS cambio_letto")
        with connect() as conn:
            righe = _leggi(conn, f"SELECT {corto} FROM listings l WHERE l.id = ANY(%s)",
                           "SELECT l.id, l.version_raw, l.title, l.gearbox FROM listings l WHERE l.id = ANY(%s)", (ids,))
        per_id = {r["id"]: r for r in righe}
        n = 0
        for it in items:
            r = per_id.get(it.get("id"))
            if not r:
                continue
            all_ = DA._pulito(r.get("allestimento_letto"), 40) or DA.allestimento_testo(r.get("version_raw"), r.get("title"))
            cambio = DA.norm_cambio(r.get("cambio_letto") or r.get("gearbox") or it.get("cambio"))
            if all_:
                it["allestimento"] = all_
                n += 1
            if cambio:
                it["cambio"] = cambio
        return n
    except Exception as e:
        log.warning("SCHEDA_TECNICA elenco: %s", str(e)[:200])
        return 0
