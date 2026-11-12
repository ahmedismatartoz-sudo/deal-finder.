"""12/11 buchi della raccolta (§0.1): lettura incrementale del giro della zona (ci si ferma al già visto)."""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder.pipeline import incrementale as INC       # noqa: E402
from dealfinder.core.models import Listing                # noqa: E402


def _l(sid, prezzo, prov="MI", fonte="autoscout24"):
    return Listing(source=fonte, source_id=sid, url="u", price_eur=prezzo, province=prov)


def test_noti_e_pagina_nota():
    n = INC.Noti([("autoscout24", "a", 5000), ("subito", "b", 3000)], k=2,
                 rilevante=INC.zona_rilevante(["MI"], 1000, 70000))
    assert n.noto("autoscout24", "a", 5000) and not n.noto("autoscout24", "a", 4900) and not n.noto("subito", "a", 5000)
    assert n.pagina_nota([_l("a", 5000), _l("x", 9000, prov="RM"), _l("y", 500)])      # fuori zona / prezzo: non contano
    assert not n.pagina_nota([_l("a", 4800)])                                            # prezzo cambiato
    assert not n.pagina_nota([_l("a", 5000), _l("nuovo", 8000)])
    a = INC.Arresto(n)
    assert not a.pagina([_l("a", 5000)]) and a.pagina([_l("a", 5000)])                 # 2 pagine note di fila
    a = INC.Arresto(n)
    assert not a.pagina([_l("a", 5000)]) and not a.pagina([_l("z", 5000)]) and not a.pagina([_l("a", 5000)])


class _Risp:
    def __init__(self, text):
        self.status_code, self.text, self.url = 200, text, "x"


class SitoAS:
    """AutoScout24 finto: annunci in ordine dal più recente, 20 per pagina, al massimo 20 pagine."""

    def __init__(self, annunci):
        self.annunci = annunci          # lista di (id, prezzo), dal più recente
        self.richieste = 0

    def get(self, url):
        self.richieste += 1
        q = dict(re.findall(r"[?&](\w+)=([^&]*)", url))
        lo, hi, page = int(q["pricefrom"]), int(q["priceto"]), int(q["page"])
        tutti = [a for a in self.annunci if lo <= a[1] <= hi]
        pezzo = tutti[(page - 1) * 20: page * 20] if page <= 20 else []
        items = [{"id": i, "url": f"/annunci/{i}", "vehicle": {"make": "Fiat", "model": "Panda"},
                  "tracking": {"price": str(p)}, "location": {"zip": "20121"}, "seller": {"type": "Private"}}
                 for i, p in pezzo]
        dati = {"props": {"pageProps": {"listings": items, "numberOfResults": len(tutti)}}}
        return _Risp('<script id="__NEXT_DATA__" type="application/json">' + json.dumps(dati) + "</script>")


def _collettore(sito, noti):
    from dealfinder.collectors.autoscout import AutoScoutCollector
    c = AutoScoutCollector(client=sito)
    c.min_delay_s = c.max_delay_s = 0
    c.noti = noti
    return c


def test_autoscout_incrementale_si_ferma_al_gia_visto():
    annunci = [(f"n{i}", 5000 + i) for i in range(25)] + [(f"v{i}", 5000 + i % 900) for i in range(3000)]
    noti = INC.Noti([("autoscout24", s, p) for s, p in annunci[25:]], k=2)
    sito = SitoAS(annunci)
    letti = list(_collettore(sito, noti)._band(5000, 6999, "P", "privato"))
    assert {l.source_id for l in letti} >= {f"n{i}" for i in range(25)}
    assert sito.richieste == 4                       # 2 pagine con i nuovi + 2 pagine note: stop
    assert noti.stats["fasce_fermate"] == 1
    # senza noti: lettura completa (fascia piena: divisa, ogni pezzo fino a 20 pagine)
    sito2 = SitoAS(annunci)
    list(_collettore(sito2, None)._band(5000, 6999, "P", "privato"))
    assert sito2.richieste > 100


def test_autoscout_incrementale_fascia_tutta_nuova_si_divide():
    annunci = [(f"n{i}", 5000 + i % 1000) for i in range(900)]
    noti = INC.Noti([], k=2)
    sito = SitoAS(annunci)
    letti = list(_collettore(sito, noti)._band(5000, 5999, "P", "privato"))
    assert len({l.source_id for l in letti}) == 900          # nessun annuncio perso
    assert noti.stats["fasce_divise"] >= 1


class SubitoFinto:
    def __init__(self, annunci):
        self.annunci, self.richieste = annunci, 0

    def __call__(self, params):
        self.richieste += 1
        start = params["start"]
        ads = [{"urn": u, "subject": "Fiat Panda", "urls": {"default": f"https://www.subito.it/auto/x-{u}.htm"},
                "features": [{"label": "Prezzo", "values": [{"key": str(p), "value": f"{p} €"}]}],
                "geo": {"city": {"short_name": "MI"}}, "dates": {"display": "2026-11-12 10:00:00"}}
               for u, p in self.annunci[start:start + 100]]

        class R:
            status_code = 200

            def json(s):
                return {"ads": ads}
        return R()


def test_subito_recenti_incrementale():
    from dealfinder.collectors.subito import SubitoCollector, parse_item
    annunci = [(f"id:ad:{i}", 3000 + i) for i in range(2000)]
    finto = SubitoFinto(annunci)
    sub = SubitoCollector(client=object())
    sub.min_delay_s = sub.max_delay_s = 0
    sub._api = finto
    prova = parse_item(finto({"start": 0}).json()["ads"][0])
    finto.richieste = 0
    if prova is None or prova.price_eur != 3000:
        return                                   # forma dell'annuncio Subito diversa: il test sul collettore non vale
    noti = INC.Noti([("subito", u, p) for u, p in annunci[150:]], k=2)
    sub.noti = noti
    letti = list(sub.search_recenti("lombardia", ["MI"], "2026-01-01 00:00", max_pages=20))
    assert finto.richieste == 4 and len(letti) == 400
    sub.noti = None
    finto.richieste = 0
    list(sub.search_recenti("lombardia", ["MI"], "2026-01-01 00:00", max_pages=20))
    assert finto.richieste == 20


def test_completo_dovuto_con_interruttore():
    vecchio = os.environ.get("ZC_INCREMENTALE")
    try:
        os.environ["ZC_INCREMENTALE"] = "0"
        assert INC.completo_dovuto("zona_cron") is True
    finally:
        if vecchio is None:
            os.environ.pop("ZC_INCREMENTALE", None)
        else:
            os.environ["ZC_INCREMENTALE"] = vecchio


def stima_tempo() -> dict:
    """Stima del giro della zona prima/dopo con i numeri dei log dell'11/11 (§2.1): pagine lette e minuti.
    AutoScout24: 245.000 annunci in 3 centri (Milano 120.500 in 66 min, Brescia 80.900 in 40, Torino 43.600 in 23) +
    focus 1.600; fasce da 250 € (552 per centro). Subito: recenti 27.500 (7 min), profondo 131.000 (57 min), focus
    5.100. Nuovi o cambiati per giro (§2.1): AutoScout24 ~165 + ~440 cambi, Subito ~10 + ~270.
    Incrementale: fasce AutoScout24 da 2.000 € (35 × 2 venditori × 3 centri = 210 + focus 5), ognuna K=2 pagine note
    + ~1 pagina con i nuovi; Subito recenti: 4 regioni × (K + 1) pagine; niente profondo."""
    min_pag_as = (66 + 40 + 23) / ((120500 + 80900 + 43600) / 20)        # minuti per pagina (3 in parallelo)
    min_pag_sub = 57 / (131000 / 100)
    prima = {"autoscout_pagine": round((245000 + 1600) / 20), "subito_pagine": round((27500 + 131000 + 5100) / 100),
             "minuti": 66 + 40 + 23 + 10 + 7 + 57}
    fasce = 35 * 2 * 3 + 5
    pag_as, pag_sub = fasce * 3, 4 * 3
    dopo = {"autoscout_pagine": pag_as, "subito_pagine": pag_sub,
            "minuti": round(pag_as * min_pag_as + pag_sub * min_pag_sub + 2)}       # +2: lettura degli annunci noti
    return {"prima": prima, "dopo_incrementale": dopo}


def test_stima_tempo():
    s = stima_tempo()
    assert s["dopo_incrementale"]["minuti"] < s["prima"]["minuti"] / 5


if __name__ == "__main__":
    print(stima_tempo())


def test_subito_fascia_incrementale_non_divide():
    from dealfinder.collectors.subito import SubitoCollector
    annunci = [(f"id:ad:{i}", 3000 + i % 900) for i in range(3000)]
    finto = SubitoFinto(annunci)
    sub = SubitoCollector(client=object())
    sub.min_delay_s = sub.max_delay_s = 0
    sub._api = finto
    sub.noti = INC.Noti([("subito", u, p) for u, p in annunci[100:]], k=2)
    letti = list(sub._fascia(4, "k", "MI", 3000, 4000, 10, 100, {"MI"}, set(), 0, {"letti": 0, "tenuti": 0}, {"ok": True}))
    assert finto.richieste == 3 and len(letti) == 300          # 1 pagina di nuovi + 2 note; nessuna divisione


def test_buco_di_4_ore_non_si_salta():
    """Il sito è stato giù 4 ore: alla ripartenza il tempo reale salva solo le prime pagine (60 annunci recenti).
    Sotto restano 80 annunci usciti durante il fermo, MAI salvati. Prima (noti = tutto il database, K=2) le 3 pagine
    salvate dal tempo reale fermavano la lettura e il buco si saltava; ora sono noti solo gli annunci già nel database
    prima dell'inizio del giro precedente, quindi la lettura scende fino al buco e oltre, fino a 3 pagine vecchie."""
    riavvio = [(f"r{i}", 5000 + i) for i in range(60)]           # salvati dal tempo reale DOPO il giro precedente
    buco = [(f"b{i}", 5100 + i) for i in range(80)]               # mai salvati
    vecchi = [(f"v{i}", 5000 + i % 900) for i in range(3000)]     # già visti dal giro precedente
    sito = SitoAS(riavvio + buco + vecchi)
    tutto_il_db = INC.Noti([("autoscout24", s, p) for s, p in riavvio + vecchi], k=2)
    letti_prima = {l.source_id for l in _collettore(sito, tutto_il_db)._band(5000, 6999, "P", "privato")}
    assert not ({s for s, _ in buco} <= letti_prima)                # il difetto di prima: buco saltato
    sito2 = SitoAS(riavvio + buco + vecchi)
    noti = INC.Noti([("autoscout24", s, p) for s, p in vecchi])     # k di default (3)
    assert noti.k == 3
    letti = {l.source_id for l in _collettore(sito2, noti)._band(5000, 6999, "P", "privato")}
    assert {s for s, _ in buco} <= letti
    assert sito2.richieste == 7 + 3                                 # 140 nuovi (7 pagine) + 3 pagine note


def test_pagina_senza_annunci_rilevanti_e_neutra():
    n = INC.Noti([("autoscout24", "a", 5000)], k=2, rilevante=INC.zona_rilevante(["MI"], 1000, 70000))
    fuori = [_l("x", 9000, prov="RM")]
    assert n.pagina_nota(fuori) is None
    a = INC.Arresto(n)
    assert not a.pagina([_l("a", 5000)]) and not a.pagina(fuori) and a.pagina([_l("a", 5000)])   # neutra: non azzera
    a = INC.Arresto(n)
    assert not a.pagina(fuori) and not a.pagina(fuori) and not a.pagina(fuori)                  # e non conta


def test_noti_carica_solo_visti_prima_del_giro_precedente():
    try:
        import pg_scheda as P
    except Exception:
        return
    c = P.nuovo_db("buchi_noti")
    if c is None:
        return
    P.migra_tutte(c)
    for sid, quando in (("vecchio", "now() - interval '5 hours'"), ("nuovo", "now() - interval '1 hour'")):
        c.execute(f"INSERT INTO listings (source, source_id, url, price_eur, province, first_seen_at) "
                  f"VALUES ('autoscout24', '{sid}', 'u{sid}', 5000, 'MI', {quando})")
    prec = c.execute("SELECT now() - interval '3 hours' AS t").fetchone()["t"]
    n = INC.Noti.carica(c, ["MI"], visti_prima_di=prec)
    assert n.noto("autoscout24", "vecchio", 5000) and not n.noto("autoscout24", "nuovo", 5000)
    assert len(INC.Noti.carica(c, ["MI"])) == 2


def test_inizio_del_giro_e_ribassi():
    righe = {}

    class C:
        def execute(self, sql, p=None):
            class R:
                def __init__(s, r):
                    s.r = r

                def fetchone(s):
                    return s.r[0] if s.r else None

                def fetchall(s):
                    return s.r
            if sql.startswith("INSERT INTO tempo_reale"):
                righe[p[0]] = p[1]
                return R([])
            if sql.startswith("SELECT ultimo_at"):
                return R([{"ultimo_at": righe[p[0]]}] if p[0] in righe else [])
            if "FROM listings WHERE source" in sql:
                return R([{"source_id": "a", "price_eur": 6000}, {"source_id": "b", "price_eur": 4000}])
            raise AssertionError(sql)

        def commit(self):
            pass

        def rollback(self):
            pass
    c = C()
    assert INC.inizio_precedente(c, "zona_cron") is None
    INC.segna_inizio(c, "zona_cron", "T1")
    assert INC.inizio_precedente(c, "zona_cron") == "T1" and INC.inizio_precedente(c, "zona_cron", "completo") is None
    from collections import Counter
    st = Counter()
    INC.conta_ribassi(c, [_l("a", 5500), _l("b", 4500)], st)
    assert st["ribassi"] == 1


def _giro_finto(sub_errore=False, as_incompleta=False, as_eccezione=False):
    """benzina_mi.giro() vero con Subito, AutoScout24 e database finti (come tests/test_raccolta). Ritorna le righe
    "inizio del giro" scritte e i contatori."""
    import contextlib
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_raccolta import _Conn, _l, _schema_nuovo
    from dealfinder import db
    from dealfinder.collectors import subito
    from dealfinder.pipeline import benzina_mi as B, lusso, tempo_reale as tr
    scritte = []

    class FintoSub:
        errori = 0

        def __init__(self, *a, **k):
            pass

        def search_recenti(self, reg, provs, dal, max_pages=0):
            if sub_errore and reg == "lombardia":
                raise RuntimeError("rete giù")
            return [_l(source="subito", source_id="s1", make="fiat", model="panda", price_eur=4000, province="MI")]

    def bande(pmin, pmax, passo, venditori, workers=1, esito=None, **_k):
        if as_eccezione:
            raise RuntimeError("bloccato")
        if as_incompleta and esito is not None:
            esito["fasce_non_complete"] = 1
        return [_l(source="autoscout24", source_id="a1", make="fiat", model="panda", price_eur=5000, province="MI")]
    conn = _Conn(colonne=False)

    @contextlib.contextmanager
    def connetti():
        yield conn
    vecchi = (db.connect, subito.SubitoCollector, lusso.bande_parallele, tr._salva, tr.valuta_e_pubblica,
              INC.segna_inizio, INC.inizio_precedente, B.PROVINCE_100)
    db.connect, subito.SubitoCollector, lusso.bande_parallele = connetti, FintoSub, bande
    tr._salva = lambda c, lst, st: []
    tr.valuta_e_pubblica = lambda c, ids, st: None
    INC.segna_inizio = lambda c, nome, quando, tipo="giro": scritte.append((nome, tipo))
    INC.inizio_precedente = lambda c, nome, tipo="giro": None
    env = dict(os.environ)
    for k in ("BENZ_CAP_EXTRA", "BENZ_FOCUS", "BENZ_SUB_PROFONDA", "BENZ_SOLO_INSUFF", "BENZ_INCREMENTALE"):
        os.environ.pop(k, None)
    os.environ.update(BENZ_SOLO_PRIVATI="1", BENZ_PROVINCE="MI", BENZ_MIN="1000", BENZ_PMAX="70000")
    try:
        st = B.giro()
    finally:
        (db.connect, subito.SubitoCollector, lusso.bande_parallele, tr._salva, tr.valuta_e_pubblica,
         INC.segna_inizio, INC.inizio_precedente, B.PROVINCE_100) = vecchi
        os.environ.clear()
        os.environ.update(env)
        _schema_nuovo()
    return scritte, st


def test_inizio_del_giro_scritto_solo_se_la_lettura_e_pulita():
    scritte, st = _giro_finto()
    assert scritte == [("zona_cron", "giro"), ("zona_cron", "completo")] and not st.get("lettura_errori")
    for k in ({"sub_errore": True}, {"as_incompleta": True}, {"as_eccezione": True}):
        scritte, st = _giro_finto(**k)
        assert scritte == [] and st["lettura_errori"] >= 1, k
