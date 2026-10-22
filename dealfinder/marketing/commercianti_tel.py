"""22/10 (cliente): elenco dei piccoli commercianti di utilitarie con il cellulare aziendale che AutoScout24
mostra nei loro annunci (gia' letti dai nostri raccoglitori per i prezzi di mercato).

Solo venditori di tipo commerciante, mai privati. Il file si scarica dall'area admin del sito
(/admin/commercianti-tel.csv): niente numeri nei log. Il cliente chiede il permesso a ognuno prima di scrivere.
"""
from __future__ import annotations

import csv
import io
import json
import re

PROVINCE_OK = {"MI", "MB", "BG", "BS", "CO", "CR", "LC", "LO", "MN", "PV", "SO", "VA", "NO", "PC", "VB", "VC"}

SQL = """
SELECT raw->'seller'->>'id' AS sid,
       (array_agg(raw->'seller' ORDER BY last_seen_at DESC))[1] AS seller,
       count(*) AS n,
       min(price_eur) AS pmin, max(price_eur) AS pmax,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY price_eur) AS pmed,
       mode() WITHIN GROUP (ORDER BY city) AS city,
       mode() WITHIN GROUP (ORDER BY province) AS prov,
       array_agg(DISTINCT make) AS makes,
       sum(CASE WHEN lower(coalesce(fuel,'')) LIKE 'benz%%' THEN 1 ELSE 0 END) AS n_benz,
       sum(CASE WHEN price_eur <= 15000 THEN 1 ELSE 0 END) AS n_sotto15
FROM listings
WHERE source = 'autoscout24' AND seller_type = 'commerciante'
  AND raw->'seller'->>'id' IS NOT NULL AND price_eur BETWEEN 1000 AND 60000
  AND last_seen_at > now() - interval '90 days'
GROUP BY 1
"""


def _cellulari(seller: dict) -> list[str]:
    out = []
    for p in (seller or {}).get("phones") or []:
        grezzo = str((p or {}).get("callTo") or (p or {}).get("formattedNumber") or "").strip()
        num = re.sub(r"\D", "", grezzo)
        if grezzo.startswith("+"):
            if not num.startswith("39"):
                continue  # numero estero
            num = num[2:]
        elif num.startswith("0039"):
            num = num[4:]
        if num.startswith("3") and 9 <= len(num) <= 10 and num not in out:
            out.append(num)
    return out


def _nome(seller: dict) -> str:
    d = (seller or {}).get("dealer") or {}
    for v in (d.get("companyName"), seller.get("companyName"), d.get("name"), d.get("contactName"), seller.get("contactName")):
        if v:
            return str(v).strip()
    return ""


def righe(conn, max_auto: int = 40) -> list[dict]:
    out = []
    for r in conn.execute(SQL).fetchall():
        r = dict(r) if not isinstance(r, dict) else r
        seller = r["seller"] if isinstance(r["seller"], dict) else json.loads(r["seller"] or "{}")
        if str(seller.get("type") or "").lower().startswith("priv"):
            continue
        cell = _cellulari(seller)
        prov = (r.get("prov") or "").upper()
        med = float(r.get("pmed") or 0)
        if not cell or prov not in PROVINCE_OK or not (3000 <= med <= 25000) or r["n"] > max_auto:
            continue
        fascia = "3-8k" if med < 8000 else ("8-15k" if med < 15000 else "15-25k")
        makes = [m for m in (r.get("makes") or []) if m][:8]
        out.append({
            "commerciante": _nome(seller), "comune": r.get("city"), "prov": prov,
            "cellulare": cell[0], "altri_cellulari": " ".join(cell[1:]),
            "fascia": fascia, "prezzo_min": int(r["pmin"] or 0), "prezzo_mediano": int(med), "prezzo_max": int(r["pmax"] or 0),
            "auto_in_vendita": r["n"], "quota_benzina": round(r["n_benz"] / r["n"], 2) if r["n"] else 0,
            "quota_sotto_15k": round(r["n_sotto15"] / r["n"], 2) if r["n"] else 0,
            "marche": ", ".join(makes),
            "pagina_autoscout": f"https://www.autoscout24.it/lst?atype=C&cid={r['sid']}",
        })
    out.sort(key=lambda x: (-x["quota_sotto_15k"], x["auto_in_vendita"]))
    return out


def csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    campi = list(rows[0].keys()) if rows else ["commerciante"]
    w = csv.DictWriter(buf, fieldnames=campi + ["permesso_chiesto", "permesso_ok"], delimiter=";")
    w.writeheader()
    for x in rows:
        w.writerow(x)
    return ("﻿" + buf.getvalue()).encode("utf-8")
