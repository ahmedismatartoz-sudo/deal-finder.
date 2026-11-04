"""4/11: un '%' da solo dentro una query (anche in un commento SQL) fa fallire psycopg ("incomplete placeholder").
È successo con la sezione "Già vendute". Questo test legge ogni stringa del codice che sembra SQL e controlla che
ogni '%' sia un segnaposto (%s, %(nome)s, %d) o raddoppiato (%%)."""
import ast
import re
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent / "dealfinder"
SQL = re.compile(r"\b(SELECT|UPDATE|INSERT|DELETE)\b[\s\S]*\b(FROM|SET|INTO|WHERE)\b")
BUONO = re.compile(r"%(%|s|d|b|t|\([A-Za-z_][A-Za-z0-9_]*\)[sbt]|\.\d+f|\d*d)")


def _stringhe(albero):
    for n in ast.walk(albero):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.lineno, n.value


def _usa_parametri(testo: str) -> bool:
    return "%s" in testo or "%(" in testo


def test_nessun_percento_da_solo_nelle_query():
    errori = []
    for f in RADICE.rglob("*.py"):
        try:
            albero = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for riga, s in _stringhe(albero):
            if not SQL.search(s) or not _usa_parametri(s) or " LIKE " in s.upper() and "%%" in s:
                continue
            resto = BUONO.sub("", s)
            if "%" in resto:
                errori.append(f"{f.relative_to(RADICE.parent)}:{riga}")
    assert not errori, "query con '%' da solo (usa %% o togli il simbolo): " + ", ".join(errori)
