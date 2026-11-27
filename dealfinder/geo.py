"""Posizione delle auto (capoluogo di provincia) e distanze: serve al filtro "entro 100 km" del sito."""
from __future__ import annotations

import math

# Capoluoghi di provincia (lat, lon), arrotondati: bastano per raggi di 50-300 km
PROVINCE = {
    "AG": (37.31, 13.58), "AL": (44.91, 8.61), "AN": (43.62, 13.52), "AO": (45.74, 7.32), "AP": (42.85, 13.58),
    "AQ": (42.35, 13.40), "AR": (43.46, 11.88), "AT": (44.90, 8.21), "AV": (40.91, 14.79), "BA": (41.12, 16.87),
    "BG": (45.70, 9.67), "BI": (45.56, 8.05), "BL": (46.14, 12.22), "BN": (41.13, 14.78), "BO": (44.49, 11.34),
    "BR": (40.63, 17.94), "BS": (45.54, 10.22), "BT": (41.23, 16.29), "BZ": (46.50, 11.35), "CA": (39.22, 9.12),
    "CB": (41.56, 14.66), "CE": (41.07, 14.33), "CH": (42.35, 14.17), "CL": (37.49, 14.06), "CN": (44.38, 7.54),
    "CO": (45.81, 9.09), "CR": (45.13, 10.02), "CS": (39.30, 16.25), "CT": (37.50, 15.09), "CZ": (38.91, 16.59),
    "EN": (37.57, 14.28), "FC": (44.22, 12.04), "FE": (44.84, 11.62), "FG": (41.46, 15.54), "FI": (43.77, 11.25),
    "FM": (43.16, 13.72), "FR": (41.64, 13.34), "GE": (44.41, 8.93), "GO": (45.94, 13.62), "GR": (42.76, 11.11),
    "IM": (43.89, 8.03), "IS": (41.59, 14.23), "KR": (39.08, 17.13), "LC": (45.86, 9.40), "LE": (40.35, 18.17),
    "LI": (43.55, 10.31), "LO": (45.31, 9.50), "LT": (41.47, 12.90), "LU": (43.84, 10.50), "MB": (45.58, 9.27),
    "MC": (43.30, 13.45), "ME": (38.19, 15.55), "MI": (45.46, 9.19), "MN": (45.16, 10.79), "MO": (44.65, 10.93),
    "MS": (44.04, 10.14), "MT": (40.67, 16.60), "NA": (40.85, 14.27), "NO": (45.45, 8.62), "NU": (40.32, 9.33),
    "OR": (39.90, 8.59), "PA": (38.12, 13.36), "PC": (45.05, 9.70), "PD": (45.41, 11.88), "PE": (42.46, 14.21),
    "PG": (43.11, 12.39), "PI": (43.72, 10.40), "PN": (45.96, 12.66), "PO": (43.88, 11.10), "PR": (44.80, 10.33),
    "PT": (43.93, 10.92), "PU": (43.91, 12.91), "PV": (45.19, 9.16), "PZ": (40.64, 15.80), "RA": (44.42, 12.20),
    "RC": (38.11, 15.65), "RE": (44.70, 10.63), "RG": (36.93, 14.73), "RI": (42.40, 12.86), "RM": (41.90, 12.50),
    "RN": (44.06, 12.57), "RO": (45.07, 11.79), "SA": (40.68, 14.77), "SI": (43.32, 11.33), "SO": (46.17, 9.87),
    "SP": (44.10, 9.82), "SR": (37.08, 15.29), "SS": (40.73, 8.56), "SU": (39.17, 8.52), "SV": (44.31, 8.48),
    "TA": (40.47, 17.24), "TE": (42.66, 13.70), "TN": (46.07, 11.12), "TO": (45.07, 7.69), "TP": (38.02, 12.51),
    "TR": (42.56, 12.65), "TS": (45.65, 13.78), "TV": (45.67, 12.24), "UD": (46.06, 13.24), "VA": (45.82, 8.83),
    "VB": (45.92, 8.55), "VC": (45.32, 8.42), "VE": (45.44, 12.33), "VI": (45.55, 11.55), "VR": (45.44, 10.99),
    "VT": (42.42, 12.11), "VV": (38.68, 16.10),
}


def coords(province: str | None) -> tuple[float, float] | None:
    return PROVINCE.get((province or "").upper()[:2])


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distanza in linea d'aria (km)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


# ------------------------------------------------------------------ 27/11 sera: posizione del visitatore e delle auto
# Il sito mostra di default le auto entro 100 km da chi guarda (decisione del cliente 27/11). Coordinate di un'auto,
# in quest'ordine (coords_auto):
#   1. lat/lon dell'annuncio (colonne listings.lat/lon, scritte dai collector quando la fonte le dà: Subito geo.map,
#      AutoScout24 location.latitude/longitude);
#   2. il comune dell'annuncio se è nella tabella COMUNI qui sotto (città principali del Nord, valori noti a 2 decimali);
#   3. il capoluogo della provincia (PROVINCE). Nel repo non c'è un elenco completo dei comuni italiani con lat/lon
#      (data/comuni_60km.json ha solo i km da Milano, senza direzione) e la rete della sandbox non permette di
#      scaricarlo: per i comuni piccoli si usa il capoluogo, errore tipico 10-30 km, che basta per raggi da 20 a 500 km.

# capoluogo di ogni provincia (nome per l'elenco "scegli la città" e per il nome del comune vicino)
NOMI = {
    "AG": "Agrigento", "AL": "Alessandria", "AN": "Ancona", "AO": "Aosta", "AP": "Ascoli Piceno", "AQ": "L'Aquila",
    "AR": "Arezzo", "AT": "Asti", "AV": "Avellino", "BA": "Bari", "BG": "Bergamo", "BI": "Biella", "BL": "Belluno",
    "BN": "Benevento", "BO": "Bologna", "BR": "Brindisi", "BS": "Brescia", "BT": "Barletta", "BZ": "Bolzano",
    "CA": "Cagliari", "CB": "Campobasso", "CE": "Caserta", "CH": "Chieti", "CL": "Caltanissetta", "CN": "Cuneo",
    "CO": "Como", "CR": "Cremona", "CS": "Cosenza", "CT": "Catania", "CZ": "Catanzaro", "EN": "Enna",
    "FC": "Forlì", "FE": "Ferrara", "FG": "Foggia", "FI": "Firenze", "FM": "Fermo", "FR": "Frosinone", "GE": "Genova",
    "GO": "Gorizia", "GR": "Grosseto", "IM": "Imperia", "IS": "Isernia", "KR": "Crotone", "LC": "Lecco", "LE": "Lecce",
    "LI": "Livorno", "LO": "Lodi", "LT": "Latina", "LU": "Lucca", "MB": "Monza", "MC": "Macerata", "ME": "Messina",
    "MI": "Milano", "MN": "Mantova", "MO": "Modena", "MS": "Massa", "MT": "Matera", "NA": "Napoli", "NO": "Novara",
    "NU": "Nuoro", "OR": "Oristano", "PA": "Palermo", "PC": "Piacenza", "PD": "Padova", "PE": "Pescara",
    "PG": "Perugia", "PI": "Pisa", "PN": "Pordenone", "PO": "Prato", "PR": "Parma", "PT": "Pistoia", "PU": "Pesaro",
    "PV": "Pavia", "PZ": "Potenza", "RA": "Ravenna", "RC": "Reggio Calabria", "RE": "Reggio Emilia", "RG": "Ragusa",
    "RI": "Rieti", "RM": "Roma", "RN": "Rimini", "RO": "Rovigo", "SA": "Salerno", "SI": "Siena", "SO": "Sondrio",
    "SP": "La Spezia", "SR": "Siracusa", "SS": "Sassari", "SU": "Carbonia", "SV": "Savona", "TA": "Taranto",
    "TE": "Teramo", "TN": "Trento", "TO": "Torino", "TP": "Trapani", "TR": "Terni", "TS": "Trieste", "TV": "Treviso",
    "UD": "Udine", "VA": "Varese", "VB": "Verbania", "VC": "Vercelli", "VE": "Venezia", "VI": "Vicenza",
    "VR": "Verona", "VT": "Viterbo", "VV": "Vibo Valentia",
}

# città principali (non capoluogo) delle zone del sito: (provincia, lat, lon), coordinate note arrotondate a 2 decimali
COMUNI = {
    "Sesto San Giovanni": ("MI", 45.53, 9.23), "Cinisello Balsamo": ("MI", 45.56, 9.22), "Legnano": ("MI", 45.60, 8.92),
    "Rho": ("MI", 45.53, 9.04), "Cologno Monzese": ("MI", 45.53, 9.28), "Paderno Dugnano": ("MI", 45.57, 9.17),
    "Rozzano": ("MI", 45.38, 9.16), "San Giuliano Milanese": ("MI", 45.40, 9.29), "Abbiategrasso": ("MI", 45.40, 8.92),
    "Magenta": ("MI", 45.46, 8.88), "Corsico": ("MI", 45.43, 9.11), "Bollate": ("MI", 45.55, 9.12),
    "Segrate": ("MI", 45.49, 9.29), "Pioltello": ("MI", 45.50, 9.33), "Melzo": ("MI", 45.50, 9.42),
    "Gorgonzola": ("MI", 45.53, 9.40), "Cernusco sul Naviglio": ("MI", 45.52, 9.33), "Parabiago": ("MI", 45.56, 8.95),
    "Garbagnate Milanese": ("MI", 45.58, 9.08), "Senago": ("MI", 45.58, 9.13), "Lainate": ("MI", 45.57, 9.03),
    "Arese": ("MI", 45.55, 9.08), "Opera": ("MI", 45.38, 9.21), "Melegnano": ("MI", 45.36, 9.32),
    "Peschiera Borromeo": ("MI", 45.43, 9.31), "Buccinasco": ("MI", 45.42, 9.12), "Trezzano sul Naviglio": ("MI", 45.42, 9.07),
    "Cassano d'Adda": ("MI", 45.53, 9.52), "Bresso": ("MI", 45.54, 9.19), "Cusano Milanino": ("MI", 45.55, 9.18),
    "Novate Milanese": ("MI", 45.53, 9.13), "Cormano": ("MI", 45.55, 9.17), "Settimo Milanese": ("MI", 45.48, 9.06),
    "Lissone": ("MB", 45.61, 9.24), "Seregno": ("MB", 45.65, 9.20), "Desio": ("MB", 45.62, 9.21),
    "Cesano Maderno": ("MB", 45.63, 9.15), "Limbiate": ("MB", 45.60, 9.13), "Brugherio": ("MB", 45.55, 9.30),
    "Vimercate": ("MB", 45.61, 9.37), "Giussano": ("MB", 45.70, 9.21), "Meda": ("MB", 45.66, 9.16),
    "Nova Milanese": ("MB", 45.59, 9.20), "Muggiò": ("MB", 45.59, 9.23), "Carate Brianza": ("MB", 45.68, 9.24),
    "Arcore": ("MB", 45.63, 9.32), "Agrate Brianza": ("MB", 45.58, 9.35), "Seveso": ("MB", 45.65, 9.14),
    "Busto Arsizio": ("VA", 45.61, 8.85), "Gallarate": ("VA", 45.66, 8.79), "Saronno": ("VA", 45.63, 9.04),
    "Tradate": ("VA", 45.71, 8.91), "Luino": ("VA", 46.00, 8.74), "Malnate": ("VA", 45.79, 8.88),
    "Somma Lombardo": ("VA", 45.68, 8.71), "Castellanza": ("VA", 45.61, 8.90), "Cardano al Campo": ("VA", 45.65, 8.77),
    "Sesto Calende": ("VA", 45.73, 8.63), "Cantù": ("CO", 45.74, 9.13), "Erba": ("CO", 45.81, 9.22),
    "Mariano Comense": ("CO", 45.70, 9.18), "Olgiate Comasco": ("CO", 45.78, 8.97), "Lomazzo": ("CO", 45.70, 9.04),
    "Merate": ("LC", 45.70, 9.42), "Calolziocorte": ("LC", 45.80, 9.43), "Casatenovo": ("LC", 45.70, 9.31),
    "Treviglio": ("BG", 45.52, 9.59), "Seriate": ("BG", 45.68, 9.72), "Dalmine": ("BG", 45.65, 9.60),
    "Romano di Lombardia": ("BG", 45.52, 9.75), "Caravaggio": ("BG", 45.50, 9.64), "Albino": ("BG", 45.76, 9.80),
    "Ponte San Pietro": ("BG", 45.70, 9.59), "Clusone": ("BG", 45.89, 9.95), "Desenzano del Garda": ("BS", 45.47, 10.54),
    "Montichiari": ("BS", 45.42, 10.39), "Lumezzane": ("BS", 45.65, 10.26), "Chiari": ("BS", 45.54, 9.93),
    "Rovato": ("BS", 45.56, 9.99), "Palazzolo sull'Oglio": ("BS", 45.60, 9.89), "Ghedi": ("BS", 45.40, 10.28),
    "Gussago": ("BS", 45.59, 10.16), "Darfo Boario Terme": ("BS", 45.88, 10.18), "Salò": ("BS", 45.61, 10.52),
    "Manerbio": ("BS", 45.36, 10.14), "Lonato del Garda": ("BS", 45.46, 10.48), "Crema": ("CR", 45.36, 9.69),
    "Casalmaggiore": ("CR", 44.99, 10.42), "Soresina": ("CR", 45.29, 9.86), "Codogno": ("LO", 45.16, 9.70),
    "Casalpusterlengo": ("LO", 45.18, 9.65), "Sant'Angelo Lodigiano": ("LO", 45.24, 9.41), "Vigevano": ("PV", 45.32, 8.86),
    "Voghera": ("PV", 44.99, 9.01), "Mortara": ("PV", 45.25, 8.74), "Stradella": ("PV", 45.08, 9.30),
    "Broni": ("PV", 45.06, 9.26), "Castiglione delle Stiviere": ("MN", 45.39, 10.49), "Suzzara": ("MN", 44.99, 10.75),
    "Viadana": ("MN", 44.93, 10.52), "Porto Mantovano": ("MN", 45.19, 10.79), "Curtatone": ("MN", 45.15, 10.72),
    "Goito": ("MN", 45.25, 10.67), "Asola": ("MN", 45.22, 10.41), "Ostiglia": ("MN", 45.07, 11.14),
    "Morbegno": ("SO", 46.13, 9.57), "Tirano": ("SO", 46.22, 10.17), "Chiavenna": ("SO", 46.32, 9.40),
    "Livigno": ("SO", 46.54, 10.14), "Bormio": ("SO", 46.47, 10.37), "Borgomanero": ("NO", 45.70, 8.46),
    "Trecate": ("NO", 45.43, 8.74), "Galliate": ("NO", 45.48, 8.70), "Arona": ("NO", 45.76, 8.56),
    "Oleggio": ("NO", 45.60, 8.64), "Castel San Giovanni": ("PC", 45.06, 9.43), "Fiorenzuola d'Arda": ("PC", 44.93, 9.91),
    "Tortona": ("AL", 44.89, 8.86), "Casale Monferrato": ("AL", 45.13, 8.45), "Novi Ligure": ("AL", 44.76, 8.79),
    "Acqui Terme": ("AL", 44.68, 8.47), "Ovada": ("AL", 44.64, 8.64), "Valenza": ("AL", 45.01, 8.64),
    "Domodossola": ("VB", 46.12, 8.29), "Omegna": ("VB", 45.88, 8.41), "Moncalieri": ("TO", 45.00, 7.68),
    "Rivoli": ("TO", 45.07, 7.52), "Collegno": ("TO", 45.08, 7.57), "Nichelino": ("TO", 44.99, 7.64),
    "Settimo Torinese": ("TO", 45.14, 7.77), "Grugliasco": ("TO", 45.07, 7.58), "Chieri": ("TO", 45.01, 7.82),
    "Pinerolo": ("TO", 44.89, 7.33), "Ivrea": ("TO", 45.47, 7.88), "Chivasso": ("TO", 45.19, 7.89),
    "Alba": ("CN", 44.70, 8.03), "Bra": ("CN", 44.70, 7.85), "Fossano": ("CN", 44.55, 7.73), "Mondovì": ("CN", 44.39, 7.82),
    "Savigliano": ("CN", 44.65, 7.66), "Fidenza": ("PR", 44.87, 10.06), "Carpi": ("MO", 44.78, 10.89),
    "Sassuolo": ("MO", 44.55, 10.78), "Imola": ("BO", 44.35, 11.71), "Casalecchio di Reno": ("BO", 44.48, 11.27),
    "San Lazzaro di Savena": ("BO", 44.47, 11.41), "Faenza": ("RA", 44.29, 11.88), "Lugo": ("RA", 44.42, 11.91),
    "Cervia": ("RA", 44.26, 12.36), "Cesena": ("FC", 44.14, 12.24), "Riccione": ("RN", 44.00, 12.65),
    "Cento": ("FE", 44.73, 11.29), "Comacchio": ("FE", 44.69, 12.18), "Rapallo": ("GE", 44.35, 9.23),
    "Chiavari": ("GE", 44.32, 9.32),
}


def chiave(nome: str | None) -> str:
    """Nome di comune in chiave di confronto: senza accenti, minuscolo, solo lettere (come zone.comune_ok)."""
    import re
    import unicodedata
    c = unicodedata.normalize("NFKD", str(nome or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", c.split(",")[0].split("(")[0])


def _luoghi() -> dict:
    """{chiave: (nome, provincia, lat, lon)}: capoluoghi e città della tabella COMUNI."""
    out = {chiave(n): (n, pv, *PROVINCE[pv]) for pv, n in NOMI.items() if pv in PROVINCE}
    out.update({chiave(n): (n, pv, la, lo) for n, (pv, la, lo) in COMUNI.items()})
    out.setdefault("forli", ("Forlì", "FC", *PROVINCE["FC"]))
    out["monzaebrianza"] = out["monza"]
    return out


LUOGHI = _luoghi()


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def coords_auto(r: dict) -> tuple[tuple[float, float] | None, str | None]:
    """((lat, lon) a 2 decimali, fonte) di un annuncio: 'annuncio' (lat/lon della fonte), 'comune' o 'provincia'."""
    la, lo = _num(r.get("lat")), _num(r.get("lon"))
    if la is not None and lo is not None and 35 <= la <= 48 and 6 <= lo <= 19:      # solo dentro l'Italia
        return (round(la, 2), round(lo, 2)), "annuncio"
    luogo = LUOGHI.get(chiave(r.get("city")))
    pv = (r.get("province") or "").upper()[:2]
    if luogo and (not pv or luogo[1] == pv):
        return (luogo[2], luogo[3]), "comune"
    c = coords(pv)
    return (c, "provincia") if c else (None, None)


def comune_vicino(lat: float, lon: float) -> dict:
    """Il luogo noto più vicino (capoluogo o città della tabella): il nome che si salva al posto della posizione."""
    best = min(LUOGHI.values(), key=lambda x: km((lat, lon), (x[2], x[3])))
    return {"comune": best[0], "provincia": best[1], "km": round(km((lat, lon), (best[2], best[3])), 1)}


def luogo(testo: str | None) -> dict | None:
    """Una città (nome) o un CAP scritti dal visitatore -> {comune, provincia, lat, lon}; None se non si trova."""
    import re
    t = str(testo or "").strip()
    cap = re.sub(r"\D", "", t)
    if len(cap) == 5 and len(cap) >= len(re.sub(r"\s", "", t)) - 1:
        from .collectors.autoscout import province_from_cap
        pv = province_from_cap(cap)
        if pv and pv in PROVINCE:
            la, lo = PROVINCE[pv]
            return {"comune": NOMI.get(pv, pv), "provincia": pv, "lat": la, "lon": lo, "cap": cap}
        return None
    x = LUOGHI.get(chiave(t))
    if x:
        return {"comune": x[0], "provincia": x[1], "lat": x[2], "lon": x[3]}
    pv = t.upper()
    if len(pv) == 2 and pv in PROVINCE:
        return {"comune": NOMI.get(pv, pv), "provincia": pv, "lat": PROVINCE[pv][0], "lon": PROVINCE[pv][1]}
    return None


def elenco_luoghi() -> list[list]:
    """[[nome, provincia, lat, lon], ...] per l'elenco "scegli la città" del sito (ordinato per nome)."""
    return sorted(([n, pv, la, lo] for n, pv, la, lo in set(LUOGHI.values())), key=lambda x: chiave(x[0]))
