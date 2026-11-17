"""Numero di telefono scritto nel TESTO dell'annuncio in forme che scovo.RE_MOBILE / RE_LANDLINE non riconoscono
(17/11, notte-01). Solo sì/no: il numero non si estrae, non si salva e non va nei log (regola del cliente).

La regola resta quella di sempre ("numero nel testo = c'è il numero", vedi app._dal_testo e tempo_reale.ha_numero_testo):
qui si riconoscono solo più FORME dello stesso dato. Forme nuove, tutte di cellulari italiani (3xx, 9-10 cifre):
  1. dopo una parola del telefono (tel, cell, chiamare, whatsapp, sms, contattare, recapito, numero) con separatori
     doppi o strani: "cell. 333 - 1234567", "tel 333 / 123 45 67", "cell.3331234567", "whatsapp3331234567",
     "tel: 3331 234567", "tel 333 1234 5 67", anche con la lettera O al posto dello zero ("cell 3O8 1234567");
  2. cifre staccate una per una (modo comune per sfuggire ai filtri): "3 3 3 1 2 3 4 5 6 7", "3.3.3.1.2.3.4.5.6.7";
  3. prefisso tra parentesi: "(333) 1234567", "(+39 333) 123 4567".
Prudenza (un falso "sì" metterebbe sul sito un'auto senza numero): la forma 1 vale solo vicino a una parola del
telefono ("telaio" non conta), con 9-10 cifre che cominciano per 3; le forme 2 e 3 sono troppo particolari per essere
prezzi, km o anni.
"""
from __future__ import annotations

import re

# parola del telefono, intera ("tel" sì, "telaio" no; "numero" sì, "numerosi" no)
_PAROLA = (r"(?i)(?<![a-zà-ù])(?:tel(?:\.|efono|efonare|efonatemi|efonico)?|cell(?:\.|ulare)?|chiam(?:are|atemi|ami|a)"
           r"|contatt(?:are|atemi|ami)|whats\s?app|wapp|sms|recapito(?:\s+telefonico)?|numero(?:\s+di\s+telefono)?)"
           r"(?![a-zà-ù])")
# dopo la parola: al massimo 20 caratteri senza cifre (": ", " al ", " o whatsapp ", ...), poi il numero con
# separatori qualsiasi tra le cifre (spazi, punti, trattini, barre, parentesi) e la O al posto dello 0
_DOPO_PAROLA = re.compile(_PAROLA + r"[^\d\n]{0,20}?((?:\+|00)?(?:39)?[\s.\-/()]{0,3}[3][\dOo\s.\-/()]{8,24})")
_STACCATE = re.compile(r"(?<![\d.,])3(?:[ .\-]{1,2}\d){8,9}(?![ .\-]{0,2}\d)")
_PARENTESI = re.compile(r"\(\s*(?:\+?39\s*)?3\d{2}\s*\)[\s.\-/]{0,3}\d{3}[\s.\-/]?\d{3,4}(?!\d)")


def _cellulare_da_pezzi(s: str) -> bool:
    """Le cifre del pezzo, un gruppo alla volta: sì se a un certo punto sono 9 o 10 cifre che cominciano per 3
    (le cifre che seguono, es. un anno o i km dopo il numero, non contano)."""
    s = re.sub(r"[Oo]", "0", s)
    cifre = ""
    for g in re.findall(r"\d+", s):
        cifre += g
        c = cifre
        if c.startswith("0039"):
            c = c[4:]
        elif c.startswith("39") and len(c) > 10:
            c = c[2:]
        if len(c) > 10:
            return False
        if c.startswith("3") and len(c) in (9, 10) and re.fullmatch(r"3\d{8,9}", c):
            return True
    return False


def numero_esteso(testo: str | None) -> bool:
    """True se nel testo c'è un cellulare scritto in una delle forme nuove (vedi sopra). Solo sì/no."""
    if not testo:
        return False
    t = str(testo)
    if _STACCATE.search(t) or _PARENTESI.search(t):
        return True
    for m in _DOPO_PAROLA.finditer(t):
        if _cellulare_da_pezzi(m.group(1)):
            return True
    return False


def numero_nel_testo(title: str | None, desc: str | None) -> bool:
    """Forme di sempre (scovo.RE_MOBILE / RE_LANDLINE) o forme nuove (numero_esteso). Solo sì/no."""
    from ..web.scovo import RE_LANDLINE, RE_MOBILE
    t = f"{title or ''} {desc or ''}"
    return bool(RE_MOBILE.search(t) or RE_LANDLINE.search(t) or numero_esteso(t))
