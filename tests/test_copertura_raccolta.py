"""17/11 notte (copertura della raccolta): AutoScout24 fasce piene oltre la 20ª pagina, zona Milano+Torino nella
lettura completa, annunci 'scomparsi' di nuovo in ricerca, recupero al primo giro, prezzo fino a 200.000 €,
codici provincia di Subito mancanti."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from dealfinder.pipeline import tempo_reale as T


class Conn:
    """Database finto: `stato` = {id: status} degli annunci AutoScout24 già salvati."""

    def __init__(self, stato=None):
        self.stato = stato or {}

    def execute(self, sql, params=None):
        if "FROM listings" in sql:
            ids = params[0]
            righe = [{"source_id": i, "status": self.stato[i]} for i in ids if i in self.stato]
            return SimpleNamespace(fetchall=lambda: righe, fetchone=lambda: None)
        return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: None)

    def commit(self):
        pass

    def rollback(self):
        pass


def _prepara(monkeypatch, risposta):
    """risposta(qs) -> lista di annunci [{"id", "pv"}] della pagina chiesta."""
    lette = []

    def get(client, url, nome, **k):
        qs = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        lette.append(qs)
        return SimpleNamespace(status_code=200, text=url)

    def nd(text):
        qs = {k: v[0] for k, v in parse_qs(urlparse(text).query).items()}
        return {"props": {"pageProps": {"listings": risposta(qs)}}}

    from dealfinder.pipeline import as_riprova
    from dealfinder.collectors import autoscout, base
    monkeypatch.setattr(as_riprova, "get", get)
    monkeypatch.setattr(as_riprova, "leggi_ripresa", lambda conn: None)
    monkeypatch.setattr(base, "next_data", nd)
    monkeypatch.setattr(autoscout, "parse_item",
                        lambda it: SimpleNamespace(source_id=it["id"], province=it["pv"], seller_type=None))
    monkeypatch.setattr(T, "_client_as", lambda: None)
    monkeypatch.setattr(T, "_as_completa", lambda c, out: out)
    monkeypatch.setenv("TR_AS_INTERVALLO_S", "0")
    T.FERMA.clear()
    T._AS_VISTI.clear()
    return lette


def test_completa_fascia_piena_si_rilegge_dal_piu_vecchio(monkeypatch):
    # fascia 9500-13000: 450 annunci in 24 ore (oltre le 400 che AutoScout24 mostra); le altre fasce vuote
    tutti = [{"id": f"x{i}", "pv": "MI"} for i in range(450)]        # dal più recente al più vecchio

    def risposta(qs):
        if qs["pricefrom"] != "9500":
            return []
        p = int(qs["page"])
        lista = tutti if qs["desc"] == "1" else list(reversed(tutti))
        return lista[(p - 1) * 20:p * 20]
    lette = _prepara(monkeypatch, risposta)
    noti = {f"x{i}": "attivo" for i in range(400)}                  # i primi 400 già nel database
    out = T._autoscout_tutti(Conn(noti))
    assert sorted(l.source_id for l in out) == sorted(f"x{i}" for i in range(400, 450))   # prima: nessuno
    dal_vecchio = [q for q in lette if q["desc"] == "0"]
    assert dal_vecchio and all(q["pricefrom"] == "9500" for q in dal_vecchio)
    assert len(dal_vecchio) <= 4                                    # si ferma dove si sovrappone al già letto


def test_completa_fascia_non_piena_niente_rilettura(monkeypatch):
    def risposta(qs):
        return [{"id": "a1", "pv": "MI"}] if qs["page"] == "1" and qs["desc"] == "1" else []
    lette = _prepara(monkeypatch, risposta)
    out = T._autoscout_tutti(Conn())
    assert [l.source_id for l in out] == ["a1"]
    assert not [q for q in lette if q["desc"] == "0"]


def test_completa_zona_torino_e_prezzo_200mila(monkeypatch):
    def risposta(qs):
        if qs["page"] != "1" or qs["desc"] != "1" or qs["pricefrom"] != "300":
            return []
        return [{"id": "to1", "pv": "TO"}, {"id": "mi1", "pv": "MI"}, {"id": "rm1", "pv": "RM"}]
    lette = _prepara(monkeypatch, risposta)
    out = T._autoscout_tutti(Conn())
    assert sorted(l.source_id for l in out) == ["mi1", "to1"]       # prima Torino era "fuori zona" qui
    assert max(int(q["priceto"]) for q in lette) == 200000


def test_scomparso_di_nuovo_in_ricerca_non_e_noto(monkeypatch):
    def risposta(qs):
        return [{"id": "s1", "pv": "MI"}, {"id": "s2", "pv": "MI"}] if qs["page"] == "1" else []
    lette = _prepara(monkeypatch, risposta)
    T._avvio["primo"] = False
    out = T._autoscout_nuovi(Conn({"s1": "scomparso", "s2": "attivo"}))
    assert [l.source_id for l in out] == ["s1"]                      # torna e si salva come 'riapparso'
    assert int(lette[0]["priceto"]) == 200000
    out = T._autoscout_tutti(Conn({"s1": "scomparso", "s2": "attivo"}))
    assert [l.source_id for l in out] == ["s1"]


def test_veloce_recupero_al_primo_giro_e_tetto_nei_log(monkeypatch):
    def risposta(qs):                                                # sempre annunci nuovi: nessuna pagina nota
        p = int(qs["page"])
        return [{"id": f"n{p}_{i}", "pv": "MI"} for i in range(20)]
    lette = _prepara(monkeypatch, risposta)
    T._avvio["primo"] = True
    try:
        prima = T._as_veloce_diag["tetto_pagine"]
        out = T._autoscout_nuovi(Conn())
        assert len(lette) == 20 and len(out) == 400                  # primo giro: 20 pagine (prima 10)
        assert T._as_veloce_diag["tetto_pagine"] == prima + 1
    finally:
        T._avvio["primo"] = False
    lette.clear()
    T._AS_VISTI.clear()
    T._autoscout_nuovi(Conn())
    assert len(lette) == 10                                          # giri normali: come prima


def test_subito_codice_provincia_mancante_dal_noto(monkeypatch):
    from dealfinder.collectors.subito import SubitoCollector
    col = SubitoCollector.__new__(SubitoCollector)
    ads = [{"geo": {"city": {"short_name": "MI", "key": "8"}}}]
    col._api = lambda params: SimpleNamespace(status_code=200, json=lambda: {"ads": ads})
    col.pause = lambda: None
    keys = col._province_keys(4, {"MI", "BS", "PC"}, 100)
    assert keys == {"MI": "8", "BS": "2"}                            # PC non è in Lombardia: non si inventa
    col._api = lambda params: SimpleNamespace(status_code=200, json=lambda: {"ads": []})
    assert col._province_keys(4, {"MI"}, 100) == {}                  # regione muta: niente codici inventati


# ---- 17/11 correzioni del giudice

class ClientDettaglio:
    """Pagina dell'annuncio: 404 per gli id in `tolti`, altrimenti una pagina valida (status 200)."""

    def __init__(self, tolti):
        self.tolti, self.chieste = set(tolti), []

    def get(self, url):
        self.chieste.append(url)
        sid = url.rsplit("/", 1)[-1]
        if sid in self.tolti:
            return SimpleNamespace(status_code=404, url=url, text="")
        return SimpleNamespace(status_code=200, url=url, text=sid)


def test_scomparso_in_ricerca_con_dettaglio_404_non_torna_attivo(monkeypatch):
    # s1 è 'scomparso' nel database, la ricerca lo mostra ancora (indice in ritardo), la pagina dà 404:
    # non deve arrivare al salvataggio (upsert_listing lo rimetterebbe 'attivo' con evento 'riapparso')
    def risposta(qs):
        return [{"id": "s1", "pv": "MI"}, {"id": "s3", "pv": "MI"}] if qs["page"] == "1" else []
    _prepara(monkeypatch, risposta)
    monkeypatch.setattr(T, "_as_completa", ORIGINALE_COMPLETA)
    from dealfinder.collectors import autoscout, base
    monkeypatch.setattr(autoscout, "parse_item",
                        lambda it: SimpleNamespace(source_id=it["id"], province=it.get("pv", "MI"), seller_type=None,
                                                   url=f"https://www.autoscout24.it/annunci/{it['id']}", photos=[]))
    monkeypatch.setattr(base, "next_data", lambda text: {"props": {"pageProps": {"listingDetails": {"id": text}}}}
                        if not str(text).startswith("http") else
                        {"props": {"pageProps": {"listings": risposta(
                            {k: v[0] for k, v in parse_qs(urlparse(text).query).items()})}}})
    monkeypatch.setattr(T, "_diagnostica_as", lambda det: None)
    monkeypatch.setattr(T, "telefono_autoscout", lambda det: None)
    monkeypatch.setenv("TR_AS_PAUSA_S", "0")
    client = ClientDettaglio({"s1"})
    monkeypatch.setattr(T, "_client_as_limitato", lambda: client)
    T._avvio["primo"] = False
    salvati = []
    out = T._autoscout_nuovi(Conn({"s1": "scomparso"}))
    salvati += [l.source_id for l in out]
    assert salvati == ["s3"]                                         # s1 non si salva: resta 'scomparso'
    assert "s1" in T._AS_VISTI                                       # e non si riapre a ogni giro veloce
    n = len(client.chieste)
    assert T._autoscout_nuovi(Conn({"s1": "scomparso", "s3": "attivo"})) == []
    assert len(client.chieste) == n                                  # nessun nuovo dettaglio per s1
    # lettura completa: stesso esito
    out = T._autoscout_tutti(Conn({"s1": "scomparso", "s3": "attivo"}))
    assert [l.source_id for l in out] == []


ORIGINALE_COMPLETA = T._as_completa


def _cattura(monkeypatch, logger) -> list:
    """Righe di log (warning) del modulo, senza la fixture caplog (run_tests.py passa solo monkeypatch)."""
    righe: list = []
    monkeypatch.setattr(logger, "warning", lambda msg, *a, **k: righe.append(msg % a if a else msg))
    return righe


def test_dal_piu_vecchio_tetto_pagine_per_giro(monkeypatch):
    # due fasce piene con 1.000 annunci ciascuna: senza tetto 40 pagine dal più vecchio, ora al massimo 10
    def risposta(qs):
        if qs["pricefrom"] not in ("9500", "13000"):
            return []
        tutti = [{"id": f"{qs['pricefrom']}_{i}", "pv": "MI"} for i in range(1000)]
        lista = tutti if qs["desc"] == "1" else list(reversed(tutti))
        p = int(qs["page"])
        return lista[(p - 1) * 20:p * 20]
    lette = _prepara(monkeypatch, risposta)
    monkeypatch.delenv("TR_AS_PAGINE_VECCHIO", raising=False)
    righe = _cattura(monkeypatch, T.log)
    T._autoscout_tutti(Conn())
    assert len([q for q in lette if q["desc"] == "0"]) == 10
    assert any("tetto 10 pagine dal più vecchio raggiunto" in r for r in righe)
    lette.clear()
    monkeypatch.setenv("TR_AS_PAGINE_VECCHIO", "0")
    T._autoscout_tutti(Conn())
    assert not [q for q in lette if q["desc"] == "0"]


def test_desc0_ignorato_si_scrive_nei_log(monkeypatch):
    tutti = [{"id": f"y{i}", "pv": "MI"} for i in range(450)]

    def risposta(qs):                                                # desc ignorato: sempre lo stesso ordine
        if qs["pricefrom"] != "9500":
            return []
        p = int(qs["page"])
        return tutti[(p - 1) * 20:p * 20]
    lette = _prepara(monkeypatch, risposta)
    righe = _cattura(monkeypatch, T.log)
    T._autoscout_tutti(Conn())
    assert len([q for q in lette if q["desc"] == "0"]) == 1          # una sola pagina, poi si smette
    assert any("desc=0 sembra ignorato" in r for r in righe)


def test_subito_codice_provincia_con_annunci_di_altre_province(monkeypatch):
    from dealfinder.collectors import subito
    righe = _cattura(monkeypatch, subito.log)
    col = subito.SubitoCollector.__new__(subito.SubitoCollector)
    pagina = [SimpleNamespace(province="VB"), SimpleNamespace(province="NO"), SimpleNamespace(province="NO")]
    col._controlla_codice(2, "7", "VB", pagina)
    col._controlla_codice(2, "7", "VB", pagina)                      # una volta sola per codice
    assert len([r for r in righe if "restituisce annunci di altre province" in r]) == 1
    assert "'NO': 2" in righe[0]
    righe.clear()
    col._controlla_codice(2, "6", "TO", [SimpleNamespace(province="TO")])
    assert not righe
