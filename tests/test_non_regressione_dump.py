"""Non regressione su dati VERI (16/11, fondamenta): 804 auto dalle righe DUMP_CANDIDATI dei log del sito del 16/11
(tests/dati/dump_candidati_16_11.json; niente telefoni né dati del venditore, della zona solo la provincia).

Ricostruire l'ingresso completo (confronti, valore reale, AI) dai log non si può: si blocca la parte FINALE della
decisione, quella che dato prezzo, M, R e margine dice se l'auto "va sul sito" e in che categoria:
  - soglia del margine minimo per fascia di prezzo (scovo.soglia),
  - sopra soglia / "Da trattare" con lo sconto da chiedere (policy_v2._sconto_da_trattare) / sotto soglia,
  - costi fissi del commerciante su un'auto senza danni (policy_v2.costi): margine = R − prezzo − costi.
Gli esiti attesi sono stati calcolati col codice del 16/11 e sono coerenti con la categoria e con "sul sito" scritti
dal sito nei log. Se un test qui fallisce, una regola di business è cambiata: se è voluto, si rigenerano gli attesi
con un commit "REGOLE:" (vedi REGOLE_DEPLOY.md); altrimenti è un errore da togliere.
"""
from __future__ import annotations

import json
import os
from collections import Counter

from dealfinder.pricing import policy_v2 as P
from dealfinder.web import scovo

QUI = os.path.dirname(os.path.abspath(__file__))
DATI = os.path.join(QUI, "dati", "dump_candidati_16_11.json")


def _casi() -> list[dict]:
    with open(DATI, encoding="utf-8") as f:
        return json.load(f)["casi"]


def esito(c: dict) -> tuple[int, str]:
    """(soglia, esito) con il codice di adesso: 'sopra' | 'trattare:<sconto>' | 'sotto'."""
    prezzo, margine = int(c["prezzo"]), int(c["margine"])
    s = scovo.soglia(prezzo)
    if margine >= s:
        return s, "sopra"
    t = {"price_eur": prezzo, "mileage_km": c["km"], "fuel": c["carb"], "year": c["anno"]}
    sc = P._sconto_da_trattare(t, prezzo, margine, s)
    return s, (f"trattare:{sc}" if sc else "sotto")


def test_file_dei_casi_senza_dati_personali():
    casi = _casi()
    assert len(casi) >= 800
    ammessi = {"id", "auto", "anno", "km", "carb", "fonte", "prezzo", "M", "R", "margine", "n", "cat", "sito", "metodo",
               "prov", "atteso"}
    for c in casi:
        assert set(c) <= ammessi, set(c) - ammessi
        assert not scovo.find_phone(str(c.get("auto") or "")), c["id"]


def test_soglia_ed_esito_uguali_a_quelli_del_16_11():
    diversi = []
    for c in _casi():
        s, e = esito(c)
        if (s, e) != (c["atteso"]["soglia"], c["atteso"]["esito"]):
            diversi.append((c["id"], c["prezzo"], c["margine"], c["atteso"], {"soglia": s, "esito": e}))
    assert not diversi, f"{len(diversi)} auto con decisione cambiata, prime: {diversi[:5]}"


def test_decisione_coerente_con_quella_del_sito_nei_log():
    """Il codice di adesso decide come il sito in produzione il 16/11: non conveniente = sotto soglia senza
    trattativa; sul sito = sopra soglia o "Da trattare"; opportunità = sempre sopra soglia."""
    conta = Counter()
    for c in _casi():
        e = esito(c)[1].split(":")[0]
        conta[(c["cat"], c["sito"], e)] += 1
        if c["cat"] == "non_conveniente":
            assert e == "sotto", c["id"]
        if c["sito"]:
            assert e in ("sopra", "trattare"), c["id"]
        if c["cat"] == "opportunita":
            assert e == "sopra", c["id"]
        if c["cat"] == "candidato" and e != "sopra":
            assert e == "trattare", c["id"]
    assert conta[("opportunita", True, "sopra")] > 100 and conta[("candidato", True, "trattare")] > 5


def test_sconto_da_trattare_entro_i_tetti():
    for c in _casi():
        e = c["atteso"]["esito"]
        if not e.startswith("trattare:"):
            continue
        sc = int(e.split(":")[1])
        assert 0 < sc <= (500 if c["prezzo"] < 7000 else 700), c["id"]
        assert c["margine"] + sc >= scovo.soglia(c["prezzo"] - sc), c["id"]


def test_costi_fissi_senza_danni_come_nei_log():
    """Le auto con costi 0 nel log (R − prezzo − margine = 0, quasi tutte): il costo fisso calcolato oggi per un'auto
    senza danni dichiarati è lo stesso, quindi margine = R − prezzo."""
    cfg = P.PolicyConfig()
    zero = [c for c in _casi() if c["atteso"]["costi"] == 0]
    assert len(zero) > 700
    for c in zero[:200]:
        cs = P.costi({"price_eur": int(c["prezzo"]), "damage_class": "nessuno"}, int(c["R"]), cfg)
        assert cs["totale_costi"] == 0, (c["id"], cs["totale_costi"])
        assert int(c["R"]) - int(c["prezzo"]) - cs["totale_costi"] == int(c["margine"])


def _regole_sito(c: dict) -> dict:
    """Le regole di telefono, zona, km e anno di web/sito_regole sull'auto del log (nel dump solo privati col numero)."""
    from dealfinder.web import sito_regole as SR
    g = SR.Regole()
    it = {"id": c["id"], "anno": c["anno"], "km": c["km"], "carb": c["carb"], "prezzo": c["prezzo"], "fonte": c["fonte"],
          "_r": {"tel": True, "provincia": c["prov"], "seller_type": "privato"}}
    return {n: getattr(SR, "r_" + n)(it, g) for n in ("anno", "km", "telefono", "no_roma", "zona")}


def test_telefono_zona_km_anno_uguali_al_16_11():
    def atteso(c):                     # 27/11 (decisione del cliente): Alessandria e Verbania entrano nella zona
        a = dict(c["atteso"]["regole_sito"])
        # 27/11: Lombardia intera, decisione del cliente (anche Mantova e Sondrio)
        if a.get("zona") in ("provincia_AL", "provincia_VB", "provincia_MN", "provincia_SO"):
            a["zona"] = None
        # 28/11: raggio 200 km, decisione del cliente: entrano tutte le province entro 200 km da Milano
        from dealfinder.web import sito_regole as SR
        if a.get("zona") and a["zona"].startswith("provincia_") and a["zona"][10:] in SR.PROVINCE_SITO:
            a["zona"] = None
        return a
    diversi = [(c["id"], atteso(c), r) for c in _casi() if (r := _regole_sito(c)) != atteso(c)]
    assert not diversi, f"{len(diversi)} auto con esito diverso, prime: {diversi[:5]}"


def test_auto_sul_sito_passano_telefono_zona_km_anno():
    """Coerenza con la produzione: ogni auto che il 16/11 era sul sito passa queste regole; quelle che non passano
    sono tutte fuori per la zona (mai Roma)."""
    fuori = Counter()
    for c in _casi():
        r = {k: v for k, v in _regole_sito(c).items() if v}
        if c["sito"]:
            assert not r, (c["id"], r)
        fuori.update(r.keys())
        assert c["prov"] not in ("RM", "LT", "FR", "VT", "RI") or "zona" in r or "no_roma" in r
    # 27/11: Lombardia intera, decisione del cliente: Mantova e Sondrio dentro, le fuori zona del dump scendono a 94
    # 28/11: raggio 200 km, decisione del cliente: restano fuori solo le 4 di Bolzano (203 km)
    assert set(fuori) <= {"zona"} and fuori["zona"] == 4
    assert {c["prov"] for c in _casi() if _regole_sito(c).get("zona")} == {"BZ"}
