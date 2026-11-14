"""14/11: gemelle disperse con un nucleo coerente (sito sceso da ~580 a ~420 auto dopo il ricalcolo del 13/11).

La regola del 12/11 (regole.Confronti.dispersione_max) toglieva TUTTE le gemelle quando 2–7 auto uguali avevano
massimo/minimo oltre 1,8, anche se la maggioranza era concorde e il prezzo lontano era un annuncio solo. Ora si usa il
nucleo stretto (maggioranza, almeno n_minimo, entro 1,35) e la mediana non supera mai quella di tutte le gemelle."""
from datetime import datetime, timezone

from dealfinder import regole as RG
from dealfinder.pipeline import confronti_extra as CE
from dealfinder.pricing import policy_v2 as P
from tests.test_policy import CFG, NOW, car


def _mk(prezzi):
    return [car(100 + i, p, year=2016, km=80000) for i, p in enumerate(prezzi)]


T = car(1, 6000, year=2016, km=80000)


def test_un_prezzo_fuori_gruppo_non_toglie_le_altre_gemelle():
    v = P.valuta(T, _mk([8000, 8400, 8800, 16000]), 1500, CFG, NOW)
    assert v["categoria"] != "dati_insufficienti"
    assert v["n"] == 3 and v.get("dispersione") is None
    assert v["nucleo_dispersi"]["tolti"] == 1 and v["nucleo_dispersi"]["tolti_prezzi"] == [16000]
    assert v["M_gemelle"] == 8400                       # mediana del nucleo, sotto quella di tutte (8.600)
    assert any("fuori gruppo" in i for i in v["ipotesi"])


def test_nucleo_alto_mai_sopra_la_mediana_di_tutte():
    v = P.valuta(T, _mk([6000, 11000, 11500, 12000]), 1500, CFG, NOW)
    assert v["n"] == 3
    assert v["M_gemelle"] == 11250                      # nucleo 11.500, tetto = mediana di tutte: niente prezzi gonfiati
    assert v["nucleo_dispersi"]["mediana_nucleo"] == 11500


def test_senza_nucleo_restano_dispersi():
    # 3 prezzi sparsi (10/14/19): nessun nucleo stretto, come il 12/11
    v = P.valuta(T, _mk([10000, 14000, 19000]), 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti" and v["dispersione"] == 1.9 and "nucleo_dispersi" not in v
    # metà e metà: non è una maggioranza
    v = P.valuta(T, _mk([8000, 8200, 16000, 16500]), 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti" and v.get("dispersione")
    # 2 gemelle disperse: mai un nucleo di una sola (minimo 2 confronti del cliente)
    v = P.valuta(T, _mk([8000, 16000]), 1500, CFG, NOW)
    assert v["categoria"] == "dati_insufficienti" and v["n"] == 2


def test_interruttore_regola_del_12_11():
    r = RG.con_campi(RG.regole(), {"confronti.dispersione_nucleo": False})
    v = P.valuta(T, _mk([8000, 8400, 8800, 16000]), 1500, P.PolicyConfig(r), NOW)
    assert v["categoria"] == "dati_insufficienti" and v["dispersione"] == 2.0
    assert RG.INTERRUTTORI["DISPERSIONE_NUCLEO"] == "confronti.dispersione_nucleo"
    assert RG.impronta(r) != RG.impronta(RG.regole())   # cambia l'impronta: il cron riscrive le valutazioni


def test_nucleo_coerente_funzione():
    cc = RG.regole().confronti
    g = [{"id": i, "prezzo": p} for i, p in enumerate([5000, 5200, 5300, 5400, 12000])]
    assert [c["prezzo"] for c in P.nucleo_coerente(g, cc, 2)] == [5000, 5200, 5300, 5400]
    assert P.nucleo_coerente(g[:4], cc, 2) is None                         # non disperse: niente da fare
    otto = [{"id": i, "prezzo": 5000 + 100 * i} for i in range(7)] + [{"id": 9, "prezzo": 20000}]
    assert P.nucleo_coerente(otto, cc, 2) is None                          # da 8 gemelle in su la regola non vale


def test_confronti_extra_turno_alle_citta_lette_meno_di_recente():
    d = lambda g: datetime(2026, 11, g, tzinfo=timezone.utc)   # noqa: E731
    dovute = ["39100", "10121", "12100", "47921", "44121"]
    ultimi = {"39100": d(12), "10121": d(12), "12100": d(10), "47921": d(4)}
    assert CE.ordina_per_turno(dovute, ultimi)[:2] == ["44121", "47921"]    # mai letta, poi la più vecchia
    assert CE.ordina_per_turno(dovute, ultimi)[2] == "12100"
    assert CE.ordina_per_turno(["a", "b"], {}) == ["a", "b"]               # nessun giro: ordine della lista
