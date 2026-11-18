"""18/11 (cliente, OK esplicito): valore di mercato del sito SOLO da auto vendute e commercianti (pricing/valore_base.py).

Dati finti: commercianti freschi, vendute, incidentata e truffa escluse dai confronti, pochi confronti →
dati_insufficienti, margine sopra/sotto la soglia del sito (scovo.soglia, SOGLIE_PV invariate), margine oltre il 55%
del valore = sospetta, tipi Decimal/None dal database, interruttore VALORE_BASE=vecchio, guardia sulle scritture al
cambio di calcolo, rivalutazione una tantum (rivaluta_base:ora) e, su Postgres vero (tests/pg_scheda.py), la strada
intera valuta_e_scrivi → valutazioni_policy → sito. Se Postgres non c'è, i test su Postgres non fanno nulla."""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.pricing import valore_base as VB         # noqa: E402
from dealfinder.pricing import valutazione as VZ         # noqa: E402
from dealfinder.web import scovo                         # noqa: E402


def _c(i, prezzo, status="attivo", sel="commerciante", year=2018, km=60000, **kw):
    """Riga della base come la legge GRUPPO_SQL (tipi del database: Decimal)."""
    r = dict(id=i, source="subito", url=f"https://x/{i}", make="Fiat", model="Panda", fuel="benzina",
             gearbox="manuale", power_kw=Decimal("51"), year=year, mileage_km=km, price_eur=Decimal(str(prezzo)),
             price_flags=None, seller_type=sel, status=status, province="MI", danno="nessuno", problema=False,
             dichiarato=False, stage="", giorni_visto=Decimal("2.5"),
             giorni_sparita=Decimal("10") if status == "scomparso" else None, giorni_online=Decimal("20"))
    r.update(kw)
    return VB.norm(r)


def _t(prezzo, **kw):
    """Auto da valutare come la prepara valutazione.prepara (privato con il numero)."""
    t = dict(id=999, make="Fiat", model="Panda", fuel="benzina", gearbox="manuale", power_kw=Decimal("51"),
             year=2018, mileage_km=60000, price_eur=Decimal(str(prezzo)), price_flags=[], seller_type="privato",
             damage_class="nessuno", problem_search=False, stage_reason=None, targa_e_telefono=True, dichiarato=False)
    t.update(kw)
    return t


COMM = [_c(i, p) for i, p in enumerate((9000, 9100, 9200, 9300, 9400, 9500), start=1)]
VEND = [_c(100 + i, p, status="scomparso", sel="privato") for i, p in enumerate((8000, 8100, 8200, 8300, 8400))]


# ------------------------------------------------------------------ calcolo

def test_commercianti_prima_delle_vendute():
    base = VB.dividi(COMM + VEND)
    assert len(base["D"]) == 6 and len(base["V"]) == 5
    v = VB.valuta(_t(6000), base, scovo.soglia(6000))
    assert v["fonte_base"] == "commercianti" and v["M"] == v["R"] == v["R_finale"] == 9250
    assert v["n"] == 6 and v["stima_D"] == 9250 and v["stima_V"] == 8200 and v["n_V"] == 5
    assert v["metodo"] == "base_vendute" and v["calibrato"] == 1.0 and v["totale_costi"] == 0
    assert v["margine"] == 9250 - 6000 and v["categoria"] == "opportunita"
    assert v["valore_base"] == {"fonte": "commercianti", "R": 9250, "n": 6, "stima_D": 9250, "n_D": 6,
                                "stima_V": 8200, "n_V": 5}
    assert len(v["confronti"]) == 6 and all(c["venditore"] == "commerciante" for c in v["confronti"])


def test_vendute_se_i_commercianti_sono_pochi():
    base = VB.dividi(COMM[:4] + VEND)                      # 4 commercianti: non bastano (N_MIN_SITO = 5)
    v = VB.valuta(_t(6000), base, scovo.soglia(6000))
    assert v["fonte_base"] == "vendute" and v["M"] == 8200 and v["n"] == 5 and v["stima_D"] is None
    assert all(c.get("venduta") for c in v["confronti"])
    assert v["categoria"] == "opportunita"


def test_pochi_confronti_dati_insufficienti():
    v = VB.valuta(_t(6000), VB.dividi(COMM[:4] + VEND[:4]), scovo.soglia(6000))
    assert v["categoria"] == "dati_insufficienti" and v.get("M") is None and v.get("margine") is None
    assert v["n_D"] == 0 and v["n_V"] == 0


def test_incidentata_e_truffa_escluse_dai_confronti():
    sporche = [_c(50, 30000, danno="grave"), _c(51, 30000, dichiarato=True), _c(52, 30000, problema=True),
               _c(53, 3000, stage="truffa"), _c(54, 3000, stage="doppione"),
               _c(55, 3000, stage="analisi_manuale"), _c(56, 3000, price_flags=["prezzo_civetta"])]
    # stage_reason "motivo | dettaglio": GRUPPO_SQL tiene la prima parte (split_part), come base_vendute.POOL_SQL
    assert "split_part(COALESCE(l.stage_reason, ''), '|', 1)" in VB.GRUPPO_SQL
    base = VB.dividi(COMM[:4] + sporche)
    assert len(base["D"]) == 4                              # restano solo le 4 pulite: troppo poche
    assert VB.valuta(_t(6000), base, scovo.soglia(6000))["categoria"] == "dati_insufficienti"
    v = VB.valuta(_t(6000), VB.dividi(COMM + sporche), scovo.soglia(6000))
    assert v["M"] == 9250 and v["n"] == 6                   # le sporche non spostano il valore


def test_commercianti_vecchi_non_contano_e_vendute_troppo_brevi_neanche():
    vecchi = [_c(60 + i, 20000, giorni_visto=Decimal("11")) for i in range(5)]
    brevi = [_c(70 + i, 20000, status="scomparso", sel="privato", giorni_online=Decimal("2")) for i in range(5)]
    base = VB.dividi(vecchi + brevi)
    assert base == {"D": [], "V": []}


def test_auto_non_valutabile_resta_fuori():
    """Come policy_v2 (sezione Incidentate): fuori solo danni gravi/strutturali, danni senza stima dei ricambi e
    annunci col prezzo non affidabile o scartati."""
    base = VB.dividi(COMM)
    for kw in ({"damage_class": "grave", "parts_cost_high": 500}, {"damage_class": "alto_rischio", "parts_cost_high": 500},
               {"rischio_strutturale": True}, {"damage_class": "leggero"}, {"damage_class": "medio"},
               {"stage_reason": "truffa | foto prese da internet"}, {"price_flags": ["leasing_o_rata"]}):
        v = VB.valuta(_t(6000, **kw), base, scovo.soglia(6000))
        assert v["categoria"] == "dati_insufficienti" and v.get("non_pulita"), kw


def test_incidentata_con_ricambi_valutata_col_nuovo_calcolo():
    """Danno leggero con i ricambi stimati: R dai confronti puliti, margine = R − prezzo − ripristino, al massimo
    candidato (sezione Incidentate come prima)."""
    base = VB.dividi(COMM)
    v = VB.valuta(_t(5000, damage_class="leggero", parts_cost_low=Decimal("600"), parts_cost_high=Decimal("800")),
                  base, scovo.soglia(5000))
    assert v["metodo"] == "base_vendute" and v["M"] == v["R_finale"] == 9250
    assert v["costi"]["ricambi"]["euro"] == 800 and "costi_commerciante" not in v["costi"]
    assert v["totale_costi"] == 800 and v["margine"] == 9250 - 5000 - 800
    assert v["categoria"] == "candidato" and any("danneggiata" in m for m in v["motivi"])
    # stesso danno con margine sotto soglia: non conveniente
    v = VB.valuta(_t(8000, damage_class="medio", parts_cost_high=900), base, scovo.soglia(8000))
    assert v["margine"] == 350 and v["categoria"] == "non_conveniente"


def test_difetti_letti_dall_ai_tolti_una_volta():
    base = VB.dividi(COMM)
    v = VB.valuta(_t(5000, costo_difetti=400, difetti_motivo="frizione", problem_search=True), base, scovo.soglia(5000))
    assert v["costo_difetti_incluso"] is True and v["totale_costi"] == 400 and v["margine"] == 9250 - 5000 - 400
    assert v["categoria"] == "candidato"
    # auto pulita: margine = R − prezzo, nessun costo fisso (cliente 18/11)
    v = VB.valuta(_t(6000), base, scovo.soglia(6000))
    assert v["totale_costi"] == 0 and v["margine"] == 9250 - 6000 and v["categoria"] == "opportunita"


def test_zone_torino_con_torino():
    from dealfinder.web import sito_regole as SR
    assert VB.zona_di("MI") == "milano" and VB.zona_di(None) == "milano" and VB.zona_di("ra") == "ravenna"
    if SR.TORINO:
        assert VB.zona_di("TO") == "torino" and set(SR.TORINO) <= set(VB.province_sito())
    assert set(SR.PROVINCE_100KM) | set(SR.RAVENNA) <= set(VB.province_sito())
    assert not set(SR.ROMA) & set(VB.province_sito())

    class C:
        def __init__(self):
            self.prov = None

        def execute(self, sql, args):
            self.prov = args["prov"]

            class R:
                def fetchall(s):
                    return []
            return R()
    c = C()
    VB.carica(c, "Fiat", "Panda", "benzina", zona="ravenna")
    assert c.prov == sorted(SR.RAVENNA)


def test_margine_sotto_soglia_non_conveniente():
    prezzo = 8500                                           # margine 750 < soglia 1.800
    v = VB.valuta(_t(prezzo), VB.dividi(COMM), scovo.soglia(prezzo))
    assert v["margine"] == 750 and v["categoria"] == "non_conveniente"
    assert v["margine"] < scovo.soglia(prezzo)


def test_margine_sopra_soglia_opportunita_e_soglia_invariata():
    prezzo = 7400                                           # soglia(7.400) = 1.800; margine 1.850
    assert scovo.soglia(prezzo) == 1800
    v = VB.valuta(_t(prezzo), VB.dividi(COMM), scovo.soglia(prezzo))
    assert v["margine"] == 1850 and v["categoria"] == "opportunita" and v["soglia"] == 1800


def test_margine_oltre_55_per_cento_sospetta():
    v = VB.valuta(_t(4000), VB.dividi(COMM), scovo.soglia(4000))     # margine 5.250 > 55% di 9.250
    assert v["categoria"] == "dati_insufficienti" and v["sospetto_truffa"] is True
    # tra il 45% e il 55% sotto: il controllo di prima (sotto_m) vale ancora; col numero entra solo come candidato
    v = VB.valuta(_t(4600), VB.dividi(COMM), scovo.soglia(4600))
    assert v["categoria"] == "candidato" and v["prezzo_molto_basso_ammesso"]
    v = VB.valuta(_t(4600, targa_e_telefono=False), VB.dividi(COMM), scovo.soglia(4600))
    assert v["categoria"] == "dati_insufficienti"


def test_auto_stessa_esclusa_dai_suoi_confronti():
    base = VB.dividi(COMM[:4] + [_c(999, 6000)])            # l'auto stessa nella base: non è un confronto
    assert VB.valuta(_t(6000), base, scovo.soglia(6000))["categoria"] == "dati_insufficienti"


def test_tipi_del_database():
    r = VB.norm({"year": Decimal("2018"), "mileage_km": None, "price_eur": Decimal("9000.00"), "power_kw": None,
                 "price_flags": '["importata"]'})
    assert r["year"] == 2018 and r["mileage_km"] is None and r["price_eur"] == 9000.0 and r["price_flags"] == ["importata"]
    v = VB.valuta(_t(6000, mileage_km=None), VB.dividi(COMM), 1500)
    assert v["categoria"] == "dati_insufficienti" and "km" in v["mancanti"]
    v = VB.valuta(_t(6000, power_kw=None, gearbox=None), VB.dividi(COMM), 1500)   # potenza e cambio ignoti: si confronta
    assert v["categoria"] == "opportunita"


def test_riassunto_per_il_sito():
    from dealfinder.pricing import policy_v2 as P
    v = VB.valuta(_t(6000), VB.dividi(COMM + VEND), scovo.soglia(6000))
    r = P.riassunto(v)
    for k in ("categoria", "M", "R", "R_finale", "margine", "soglia", "n", "metodo", "M_fonte", "fonte_base",
              "valore_base", "calibrato", "acquisto"):
        assert k in r, k
    # il sito non riporta il valore "al venduto" una seconda volta (è già un prezzo di vendita)
    from dealfinder import regole as RG
    assert RG.calibra_vendita(r, [(5000, "x", 0.9), (50000, "y", 0.9)]) == r
    assert not RG.da_calibrare(r)


def test_ripiego_non_fa_rientrare_le_valutazioni_nuove():
    from dealfinder.web import sito_regole as SR
    g = SR.Regole.da_env()
    pv = {"categoria": "dati_insufficienti", "metodo": "base_vendute"}
    it = {"pv": pv, "prezzo": 6000, "_r": {"ripiego": {"margine": 3000, "M": 9000, "R": 9000}}}
    assert SR.r_valutata(it, g) == "dati_insufficienti"


# ------------------------------------------------------------------ interruttore e guardia

def test_interruttore():
    prima = os.environ.get("VALORE_BASE")
    try:
        os.environ.pop("VALORE_BASE", None)
        assert VB.attivo()
        os.environ["VALORE_BASE"] = "vecchio"
        assert not VB.attivo()
        os.environ["VALORE_BASE"] = "nuovo"
        assert VB.attivo()
    finally:
        if prima is None:
            os.environ.pop("VALORE_BASE", None)
        else:
            os.environ["VALORE_BASE"] = prima


def test_guardia_cambio_di_calcolo():
    from datetime import datetime, timezone
    ora = datetime.now(timezone.utc)
    vecchia = {"n": 20, "impronta_auto": "a", "impronta_regole": "r", "computed_at": ora, "metodo": "mediana"}
    assert VZ.deve_scrivere(vecchia, 6, "a", "r", ora, metodo_nuovo="mediana") == (False, "meno_confronti")
    assert VZ.deve_scrivere(vecchia, 6, "a", "r", ora, metodo_nuovo="base_vendute") == (True, "calcolo_cambiato")
    nuova = dict(vecchia, metodo="base_vendute")
    assert VZ.deve_scrivere(nuova, 6, "a", "r", ora, metodo_nuovo="valore_reale") == (True, "calcolo_cambiato")
    assert VZ.deve_scrivere(nuova, 6, "a", "r", ora, metodo_nuovo="base_vendute") == (False, "meno_confronti")
    assert VZ.METODO_BASE == VB.METODO and "'base_vendute'" in VZ.UPSERT_SQL


# ------------------------------------------------------------------ rivalutazione una tantum

def test_rivaluta_base_registrato_nel_sito():
    from dealfinder.pipeline import coda_lavori as CL
    assert CL._prefisso("rivaluta_base:ora") == "rivaluta_base:"
    assert "rivaluta_base:ora".startswith(CL.SOLO_SITO) and not CL.spostabile("rivaluta_base:ora")


def test_blocchi_per_gruppi_interi():
    righe = [{"id": i, "make": "Fiat", "model": "Panda", "fuel": "benzina"} for i in range(5)] + \
            [{"id": 10 + i, "make": "FIAT", "model": "Punto", "fuel": "benzina"} for i in range(3)]
    assert VB._blocchi(righe, blocco=4) == [[0, 1, 2, 3, 4], [10, 11, 12]]
    assert VB._blocchi(righe, blocco=100) == [[0, 1, 2, 3, 4, 10, 11, 12]]


class _ConnRiv:
    def __init__(self, ids, prima):
        self.ids, self.prima = ids, prima

    def execute(self, sql, args=None):
        class R:
            def __init__(s, rows):
                s.rows = rows

            def fetchall(s):
                return list(s.rows)
        if "FROM listings l" in sql:
            return R([{"id": i, "make": "Fiat", "model": "Panda", "fuel": "benzina"} for i in self.ids])
        return R([{"listing_id": k, "categoria": c} for k, c in self.prima.items() if k in args[0]])

    def commit(self):
        pass


def test_rivaluta_conta_uscite_ed_entrate_senza_dati_personali():
    conn = _ConnRiv([1, 2, 3, 4], {1: "opportunita", 2: "opportunita", 3: "non_conveniente"})

    @contextmanager
    def connetti():
        yield conn

    chiamate = []

    def ves(c, ids, fonte, pubblica=None, pausa_gruppo=0.0):
        chiamate.append((list(ids), fonte, pubblica, pausa_gruppo))
        es = VZ.Esito()
        es.salvata.update({1: "opportunita", 2: "non_conveniente", 3: "opportunita", 4: "dati_insufficienti"})
        es.stats.update({"categoria:opportunita": 2, "categoria:non_conveniente": 1, "pubblicate": 2})
        return es
    out = VB.rivaluta("rivaluta_base:ora", connetti=connetti, ves=ves)
    assert chiamate == [([1, 2, 3, 4], "rivaluta-base", True, VB.PAUSA_GRUPPO_S)]
    c = out["conteggi"]
    assert c["uscite_dal_sito"] == 1 and c["nuove_buone"] == 1 and c["restano_buone"] == 1 and c["auto"] == 4
    assert set(out) == {"fermata", "secondi", "conteggi"}          # solo conteggi
    prima = os.environ.get("VALORE_BASE")
    os.environ["VALORE_BASE"] = "vecchio"
    try:
        assert VB.rivaluta("x", connetti=connetti, ves=ves) == {"fermata": "valore_base_spento"}
    finally:
        if prima is None:
            os.environ.pop("VALORE_BASE", None)
        else:
            os.environ["VALORE_BASE"] = prima


# ------------------------------------------------------------------ Postgres vero: strada intera

_C: dict = {}


def _pg():
    if "c" not in _C:
        try:
            import pg_scheda as P
            c = P.nuovo_db("valore_base")
            if c is not None:
                P.migra_tutte(c)
        except Exception:
            c = None
        _C["c"] = c
    c = _C["c"]
    if c is not None:
        for t in ("valuations", "valutazioni_policy", "top_auto", "listings"):
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
    return c


_N = {"i": 0}


def _ins(c, prezzo, sel="commerciante", status="attivo", stage="nuovo", reason=None, danno="nessuno", km=60000,
         visto_giorni=1, sparita_giorni=None, online_giorni=20, modello="panda", prov="MI") -> int:
    _N["i"] += 1
    sid = f"vb{_N['i']}"
    return c.execute(
        "INSERT INTO listings (source, source_id, url, make, model, fuel, gearbox, power_kw, year, mileage_km, "
        "price_eur, province, region, seller_type, status, telefono_ok, stage, stage_reason, damage_class, "
        "first_seen_at, last_seen_at, last_checked_at, disappeared_at) VALUES ('subito', %s, %s, 'fiat', %s, "
        "'benzina', 'manuale', 51, 2018, %s, %s, %s, 'lombardia', %s, %s, true, %s, %s, %s, "
        "now() - make_interval(days => %s), now() - make_interval(days => %s), now() - make_interval(days => %s), "
        "CASE WHEN %s::int IS NULL THEN NULL ELSE now() - make_interval(days => %s::int) END) RETURNING id",
        (sid, "https://x/" + sid, modello, km, prezzo, prov, sel, status, stage, reason, danno,
         online_giorni + (sparita_giorni or 0),
         visto_giorni, visto_giorni, sparita_giorni, sparita_giorni)).fetchone()["id"]


def test_postgres_valuta_scrive_pubblica_e_fa_uscire():
    c = _pg()
    if c is None:
        return
    for p in (9000, 9100, 9200, 9300, 9400, 9500):
        _ins(c, p)
    _ins(c, 30000, danno="grave")                               # incidentata: non conta
    _ins(c, 30000, reason="truffa | foto da internet")         # truffa con dettaglio: non conta
    _ins(c, 30000, visto_giorni=20)                             # commerciante non ricontrollato da 20 giorni
    buona = _ins(c, 6000, sel="privato")
    cara = _ins(c, 8800, sel="privato", stage="approfondito", reason="opportunita")
    # riga del calcolo di prima per la cara: opportunità con 20 confronti, stesse impronte che avrà la nuova
    VZ._scrivi(c, cara, {"categoria": "opportunita", "n": 20, "M": 12000, "R_finale": 12000, "margine": 3200,
                         "metodo": "mediana", "acquisto": 8800}, None, "x", "x", "test", 36)
    es = VZ.valuta_e_scrivi(c, [buona, cara], "test", pubblica=True)
    assert es.stats["categoria:opportunita"] == 1 and es.stats["categoria:non_conveniente"] == 1
    assert es.stats["valore_base:commercianti"] == 2 and not es.stats["errore"]
    righe = {r["listing_id"]: r for r in c.execute(
        "SELECT listing_id, categoria, margine, metodo, n, dati FROM valutazioni_policy").fetchall()}
    assert righe[buona]["categoria"] == "opportunita" and righe[buona]["metodo"] == "base_vendute"
    assert righe[buona]["n"] == 6 and righe[buona]["dati"]["M"] == 9250 and righe[buona]["margine"] == 3250
    assert righe[cara]["categoria"] == "non_conveniente" and righe[cara]["margine"] == 450
    st = c.execute("SELECT stage, stage_reason FROM listings WHERE id=%s", (buona,)).fetchone()
    assert st == {"stage": "approfondito", "stage_reason": "opportunita"}
    # il sito legge il riassunto dell'ultima valutazione: la cara non passa più (categoria fuori da CATEGORIE_SITO)
    pv = c.execute("SELECT riassunto FROM valutazioni_policy WHERE listing_id=%s", (cara,)).fetchone()["riassunto"]
    assert pv["categoria"] not in scovo.CATEGORIE_SITO and not scovo.abbastanza({"pv": pv, "anno": 2018, "km": 60000})


def test_postgres_guardia_al_cambio_di_calcolo():
    c = _pg()
    if c is None:
        return
    lid = _ins(c, 6000, sel="privato")
    vecchia = {"categoria": "opportunita", "n": 20, "M": 12000, "margine": 3000, "metodo": "mediana"}
    assert VZ._scrivi(c, lid, vecchia, None, "a", "r", "test", 36)
    nuova = {"categoria": "non_conveniente", "n": 6, "M": 7000, "margine": 1000, "metodo": "base_vendute"}
    assert VZ._scrivi(c, lid, nuova, None, "a", "r", "test", 36)           # meno confronti ma calcolo cambiato
    assert not VZ._scrivi(c, lid, dict(nuova, n=5), None, "a", "r", "test", 36)   # stesso calcolo: guardia di prima
    assert VZ._scrivi(c, lid, dict(vecchia, n=3), None, "a", "r", "test", 36)     # ritorno indietro (VALORE_BASE=vecchio)


def test_postgres_rivaluta():
    c = _pg()
    if c is None:
        return
    for p in (9000, 9100, 9200, 9300, 9400, 9500):
        _ins(c, p)
    cara = _ins(c, 8800, sel="privato", stage="approfondito", reason="opportunita")
    VZ._scrivi(c, cara, {"categoria": "opportunita", "n": 20, "M": 12000, "margine": 3200, "metodo": "mediana",
                         "acquisto": 8800}, None, "x", "x", "test", 36)
    c.execute("INSERT INTO top_auto (listing_id, posizione, margine) VALUES (%s, 0, 3200)", (cara,))

    @contextmanager
    def connetti():
        yield c
    out = VB.rivaluta("rivaluta_base:ora", connetti=connetti)
    assert not c.execute("SELECT 1 FROM top_auto WHERE listing_id=%s", (cara,)).fetchall()
    assert out["fermata"] is None and out["conteggi"]["auto"] == 7
    assert out["conteggi"]["uscite_dal_sito"] == 1 and out["conteggi"]["blocchi"] == 1
    assert c.execute("SELECT categoria FROM valutazioni_policy WHERE listing_id=%s", (cara,)).fetchone()["categoria"] \
        == "non_conveniente"


def test_guardia_uscita_dal_sito():
    from datetime import datetime, timezone
    ora = datetime.now(timezone.utc)
    buona = {"n": 9, "impronta_auto": "a", "impronta_regole": "r", "computed_at": ora, "metodo": "base_vendute",
             "categoria": "opportunita"}
    assert VZ.deve_scrivere(buona, 5, "a", "r", ora, metodo_nuovo="base_vendute",
                            categoria_nuova="non_conveniente") == (True, "uscita_dal_sito")
    assert VZ.deve_scrivere(buona, 5, "a", "r", ora, metodo_nuovo="base_vendute",
                            categoria_nuova="candidato") == (False, "meno_confronti")
    assert VZ.deve_scrivere(dict(buona, metodo="mediana"), 5, "a", "r", ora, metodo_nuovo="mediana",
                            categoria_nuova="non_conveniente") == (False, "meno_confronti")   # calcolo di prima: come prima


def test_postgres_uscita_dal_sito_con_meno_confronti():
    c = _pg()
    if c is None:
        return
    lid = _ins(c, 6000, sel="privato")
    assert VZ._scrivi(c, lid, {"categoria": "opportunita", "n": 9, "M": 9000, "margine": 3000,
                               "metodo": "base_vendute"}, None, "a", "r", "test", 36)
    assert not VZ._scrivi(c, lid, {"categoria": "candidato", "n": 5, "M": 8000, "margine": 2000,
                                   "metodo": "base_vendute"}, None, "a", "r", "test", 36)   # resta buona: guardia
    assert VZ._scrivi(c, lid, {"categoria": "non_conveniente", "n": 5, "M": 7000, "margine": 1000,
                               "metodo": "base_vendute"}, None, "a", "r", "test", 36)   # non più buona: esce
    assert c.execute("SELECT categoria FROM valutazioni_policy WHERE listing_id=%s", (lid,)).fetchone()["categoria"] \
        == "non_conveniente"


def test_postgres_errore_in_carica_non_lascia_la_base_del_gruppo_prima():
    c = _pg()
    if c is None:
        return
    for p in (9000, 9100, 9200, 9300, 9400, 9500):
        _ins(c, p)                                             # base delle Panda
    for p in (15000, 15100, 15200, 15300, 15400, 15500):
        _ins(c, p, modello="tipo")                             # base delle Tipo
    a = _ins(c, 6000, sel="privato")
    b1 = _ins(c, 11000, sel="privato", modello="tipo")
    b2 = _ins(c, 11000, sel="privato", modello="tipo")
    vera, chiamate = VB.carica, []

    def carica(conn, make, model, fuel, zona="milano"):
        chiamate.append(model)
        if model == "tipo" and chiamate.count("tipo") == 1:
            raise RuntimeError("lettura non riuscita")
        return vera(conn, make, model, fuel, zona=zona)
    VB.carica = carica
    try:
        es = VZ.valuta_e_scrivi(c, [a, b1, b2], "test", pubblica=False)
    finally:
        VB.carica = vera
    assert es.stats["errore"] == 1 and chiamate.count("tipo") == 2
    m = {r["listing_id"]: r["dati"].get("M") for r in c.execute(
        "SELECT listing_id, dati FROM valutazioni_policy").fetchall()}
    assert m[a] == 9250 and m[b2] == 15250 and b1 not in m       # la seconda Tipo con la base delle Tipo


def test_postgres_torino_con_torino():
    from dealfinder.web import sito_regole as SR
    c = _pg()
    if c is None or not SR.TORINO:
        return
    for p in (9000, 9100, 9200, 9300, 9400, 9500):
        _ins(c, p)                                             # Milano
    for p in (7000, 7100, 7200, 7300, 7400, 7500):
        _ins(c, p, prov="TO")                                  # Torino
    mi, to = _ins(c, 5000, sel="privato"), _ins(c, 5000, sel="privato", prov="TO")
    VZ.valuta_e_scrivi(c, [mi, to], "test", pubblica=False)
    m = {r["listing_id"]: r["dati"].get("M") for r in c.execute(
        "SELECT listing_id, dati FROM valutazioni_policy").fetchall()}
    assert m[mi] == 9250 and m[to] == 7250


def test_ai_prezzi_spento_col_calcolo_nuovo():
    from dealfinder.pricing import ai_prezzi
    assert VB.attivo() and ai_prezzi.run() == {"spento": "valore_base"}
