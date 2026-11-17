"""Numero di telefono scritto nel TESTO dell'annuncio in forme che scovo.RE_MOBILE / RE_LANDLINE non riconoscono
(17/11, notte-01). Solo sì/no: il numero non si estrae, non si salva e non va nei log (regola del cliente).

La regola resta quella di sempre ("numero nel testo = c'è il numero", vedi app._dal_testo e tempo_reale.ha_numero_testo):
qui si riconoscono solo più FORME dello stesso dato. Forme nuove, tutte di cellulari italiani (3xx, 9-10 cifre):
  1. dopo una parola del telefono (tel, cell, chiamare, whatsapp, sms, contattare, recapito, numero) con separatori
     doppi o strani: "cell. 333 - 1234567", "tel 333 / 123 45 67", "cell.3331234567", "whatsapp3331234567",
     "tel: 3331 234567", anche con la lettera O al posto dello zero ("cell 3O8 1234567");
  2. cifre staccate una per una (modo comune per sfuggire ai filtri), vicino a una parola del telefono:
     "cell 3 3 3 1 2 3 4 5 6 7", "tel 3.3.3.1.2.3.4.5.6.7";
  3. prefisso tra parentesi: "(333) 1234567", "(+39 333) 123 4567".
Prudenza (regola del cliente: telefono obbligatorio, in dubbio "no"; un falso "sì" metterebbe sul sito un'auto senza
numero): la forma 1 vale solo vicino a una parola del telefono ("telaio" non conta), con 9-10 cifre che cominciano per
3, al massimo 3 gruppi, il primo di 3-4 cifre (o il numero tutto attaccato), niente gruppi "000"/"x.000", niente
gruppi che sembrano prezzi/km/date ("tel 3.500 - 120.000 km", "tel 3500 - 150000 km", "numero 31 - 12 - 2019" → no);
la forma 2 vuole 10 cifre col prefisso 3[1-9]x vicino a una parola del telefono ("codice 3 2 1 4 5 6 7 8 9" → no).

Le stesse espressioni servono al sito per NASCONDERE il numero (scovo.hide_phones usa trova_numeri) e per mostrarlo
dopo l'apertura (scovo.find_phone): ogni forma che qui vale come "numero" viene anche mascherata.
"""
from __future__ import annotations

import re

# parola del telefono, intera ("tel" sì, "telaio" no; "numero" sì, "numerosi" no)
_PAROLA = (r"(?i)(?<![a-zà-ù])(?:tel(?:\.|efono|efonare|efonatemi|efonico)?|cell(?:\.|ulare)?|chiam(?:are|atemi|ami|a)"
           r"|contatt(?:are|atemi|ami)|whats\s?app|wapp|sms|recapito(?:\s+telefonico)?|numero(?:\s+di\s+telefono)?)"
           r"(?![a-zà-ù])")
_RE_PAROLA = re.compile(_PAROLA)
# dopo la parola: al massimo 20 caratteri senza cifre (": ", " al ", " o whatsapp ", ...), poi il numero con
# separatori qualsiasi tra le cifre (spazi, punti, trattini, barre, parentesi) e la O al posto dello 0
_DOPO_PAROLA = re.compile(_PAROLA + r"[^\d\n]{0,20}?((?:\+|00)?(?:39)?[\s.\-/()]{0,3}[3][\dOo\s.\-/()]{8,24})")
# cifre staccate una per una: 10 cifre, prefisso 3[1-9]x; vale solo con una parola del telefono poco prima
_STACCATE = re.compile(r"(?<![\d.,])3(?:[ .\-]{1,2})[1-9](?:[ .\-]{1,2}\d){8}(?![ .\-]{0,2}\d)")
_PAROLA_PRIMA = 30          # caratteri prima delle cifre staccate in cui cercare la parola del telefono
_PARENTESI = re.compile(r"\(\s*(?:\+?39\s*)?3\d{2}\s*\)[\s.\-/]{0,3}\d{3}[\s.\-/]?\d{3,4}(?!\d)")
# dopo il numero: unità che dicono "prezzo/km", non telefono
_UNITA_DOPO = re.compile(r"(?i)\s*(?:km|chilometri|€|eur|euro|cc|cv|kw)(?![a-z])")


def _sembra_data_prezzo_km(g: str, sep: str) -> bool:
    """Gruppo di cifre (non il primo) che sembra un prezzo, dei km o una data, non un pezzo di telefono."""
    if re.fullmatch(r"0{3}", g) or (len(g) >= 4 and g.endswith("000")):
        return True                                       # "120.000", "150000", "000"
    if "-" in sep or "/" in sep:
        if re.fullmatch(r"(?:19|20)\d{2}", g) or len(g) <= 2:
            return True                                   # "- 2019", "/ 10 /": date
    return False


def _cellulare_da_pezzi(s: str):
    """Le cifre del pezzo, un gruppo alla volta: la fine (indice nel pezzo) dell'ultima cifra del numero se sono 9 o 10
    cifre che cominciano per 3, altrimenti None. Prudente (in dubbio "no"): al massimo 3 gruppi, il primo di 3-4
    cifre che comincia per 3 (o il numero tutto attaccato, 9-10 cifre), niente gruppi da prezzo/km/data.
    Le cifre che seguono il numero (es. un anno dopo) non contano."""
    s = re.sub(r"[Oo]", "0", s)
    gruppi = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\d+", s)]
    if not gruppi:
        return None
    # prefisso internazionale come gruppo a sé ("+39 333 ...", "0039 333 ...") o attaccato ("393331234567")
    g0 = gruppi[0][0]
    if g0 in ("39", "0039") and len(gruppi) > 1:
        gruppi = gruppi[1:]
    elif g0.startswith("0039") and len(g0) in (13, 14):
        gruppi[0] = (g0[4:],) + gruppi[0][1:]
    elif g0.startswith("39") and len(g0) in (11, 12):
        gruppi[0] = (g0[2:],) + gruppi[0][1:]
    primo = gruppi[0][0]
    if not primo.startswith("3"):
        return None
    if len(primo) in (9, 10):
        if _UNITA_DOPO.match(s, gruppi[0][2]):
            return None
        return gruppi[0][2]
    if len(primo) not in (3, 4) or (len(primo) == 4 and primo.endswith("00")):
        return None                                       # "3", "31", "3500": pezzi di prezzi/date, non prefissi
    cifre = primo
    fine = None
    for k in range(1, min(3, len(gruppi))):
        g, a, b = gruppi[k]
        sep = s[gruppi[k - 1][2]:a]
        if _sembra_data_prezzo_km(g, sep):
            return None
        cifre += g
        if len(cifre) > 10:
            return None
        if len(cifre) in (9, 10):
            fine = b
            if len(cifre) == 9 and k + 1 < min(3, len(gruppi)):
                g2, a2, b2 = gruppi[k + 1]
                if len(g2) == 1 and not _sembra_data_prezzo_km(g2, s[b:a2]):
                    fine = b2                             # "333 123456 7": tutte e 10
            break
    if fine is None or not re.fullmatch(r"3\d{8,9}", cifre[:10]):
        return None
    if _UNITA_DOPO.match(s, fine):
        return None                                       # "... 150000 km"
    return fine


def trova_numeri(testo: str | None) -> list[tuple[int, int]]:
    """Posizioni (inizio, fine) dei cellulari scritti nelle forme nuove (vedi sopra). Servono a numero_esteso (solo
    sì/no) e al sito per nasconderli (scovo.hide_phones) o mostrarli dopo l'apertura (scovo.find_phone)."""
    if not testo:
        return []
    t = str(testo)
    out: list[tuple[int, int]] = []
    for m in _STACCATE.finditer(t):
        cifre = re.sub(r"\D", "", m.group(0))
        if cifre.endswith("000") or _UNITA_DOPO.match(t, m.end()):
            continue                                      # "tel 3 1 5 0 0 0 0 0 0 0 km": km, non telefono
        if _RE_PAROLA.search(t, max(0, m.start() - _PAROLA_PRIMA), m.start()):
            out.append((m.start(), m.end()))
    for m in _PARENTESI.finditer(t):
        out.append((m.start(), m.end()))
    for m in _DOPO_PAROLA.finditer(t):
        fine = _cellulare_da_pezzi(m.group(1))
        if fine is not None:
            out.append((m.start(1), m.start(1) + fine))
    # unisce le posizioni che si sovrappongono
    out.sort()
    unite: list[tuple[int, int]] = []
    for a, b in out:
        if unite and a <= unite[-1][1]:
            unite[-1] = (unite[-1][0], max(b, unite[-1][1]))
        else:
            unite.append((a, b))
    return unite


def cifre_numero(pezzo: str) -> str | None:
    """Le 9-10 cifre del cellulare in un pezzo trovato da trova_numeri (O → 0, senza prefisso +39). Solo per il
    bottone del sito dopo l'apertura: mai nei log."""
    c = re.sub(r"\D", "", re.sub(r"[Oo]", "0", pezzo))
    if c.startswith("0039"):
        c = c[4:]
    elif c.startswith("39") and len(c) > 10:
        c = c[2:]
    return c[:10] if re.match(r"3\d{8,9}", c) else None


def numero_esteso(testo: str | None) -> bool:
    """True se nel testo c'è un cellulare scritto in una delle forme nuove (vedi sopra). Solo sì/no."""
    return bool(trova_numeri(testo))


def numero_nel_testo(title: str | None, desc: str | None) -> bool:
    """Forme di sempre (scovo.RE_MOBILE / RE_LANDLINE) o forme nuove (numero_esteso). Solo sì/no."""
    from ..web.scovo import RE_LANDLINE, RE_MOBILE
    t = f"{title or ''} {desc or ''}"
    return bool(RE_MOBILE.search(t) or RE_LANDLINE.search(t) or numero_esteso(t))
