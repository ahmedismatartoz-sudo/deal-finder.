"""20/11 14:18 (proprietario: "i migliori 70 di Subito" senza numero, contattabili con la chat di Subito): corsia
chat_subito nelle regole del sito. Tetto separato dentro i privati, solo Subito, solo privati, solo zona vera, solo
auto senza avvisi e con il controllo Claude pulito. Niente rete né database."""
import dataclasses
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dealfinder import automatismi                 # noqa: E402
from dealfinder import sentinella as S             # noqa: E402
from dealfinder.ai import controllo_auto as CA     # noqa: E402
from dealfinder.web import impostazioni            # noqa: E402
from dealfinder.web import scovo                   # noqa: E402
from dealfinder.web import sito_regole as SR       # noqa: E402
from test_commercianti_totale import _mondo, _tipi  # noqa: E402
from test_diesel_extra import PULITA               # noqa: E402

G = SR.Regole()
SPENTA = dataclasses.replace(G, chat_subito=0)
CLAUDE = dict(PULITA, stesso_prezzo=True)
JEV = {"decisione": "compra", "confidenza": 0.9, "truffa": 0.05, "danni": 0.05, "guasto": 0.0}    # non Claude


def auto(i, prezzo=8000, margine=3000, M=10000, prov="MI", tel=False, seller="privato", fonte="Subito",
         jev="claude", carb="diesel", anno=2012, km=120000, **kw):
    """Privato su Subito senza numero a Milano, fuori dalla fascia richiesta (diesel del 2012), con il controllo Claude
    pulito allo stesso prezzo; sconto sul mercato 2.000 € (sotto il limite dello sconto alto)."""
    r = {"tel": tel, "seller_type": seller, "provincia": prov, "jev": dict(CLAUDE) if jev == "claude" else jev}
    r.update(kw.pop("_r", {}))
    it = {"id": i, "prezzo": prezzo, "anno": anno, "km": km, "carb": carb, "fonte": fonte,
          "pv": {"categoria": "opportunita", "M": M, "R": prezzo + margine, "R_finale": prezzo + margine,
                 "margine": margine, "totale_costi": 0, "n": 10},
          "_r": r}
    it.update(kw)
    return it


def copie(items):
    return [dict(it, _r=dict(it["_r"]), pv=dict(it["pv"])) for it in items]


def elenco(items, g=G):
    return SR.elenco_sito(copie(items), g, scrivi_log=False)


def ids(items, g=G):
    return sorted(it["id"] for it in elenco(items, g)[0])


def _passo(fun, nome):
    return next((p for p in fun.passi if p["passo"] == nome), None)


def _pulisci_coda():
    CA.CODA_SITO.update(t=0.0, entra=[], fuori=set(), sul_sito=set(), motivi={}, inutili=set(), gia_controllate=set(),
                        chat=[])
    CA.CODA_SITO.pop("visti_giro", None)
    CA.MISURA.update(giorno=None)


# ------------------------------------------------------------------ regola e interruttore
def test_default_70_niente_variabili_e_impronta():
    assert (G.chat_subito, G.chat_pulita, G.chat_auto_vera) == (70, 0.30, 0.7)
    assert SR.Regole.da_env().chat_subito == 70             # nessuna variabile d'ambiente la cambia
    prima = dict(os.environ)
    try:
        os.environ.update(CHAT_SUBITO="0", AUTOMATISMO_CHAT_SUBITO="0", AUTOMATISMO_CHAT_SUBITO_MAX="3")
        assert SR.Regole.da_env() == SR.Regole.da_env().__class__.da_env()
        assert SR.Regole.da_env().chat_subito == 70         # da_env NON legge gli automatismi
    finally:
        for k in ("CHAT_SUBITO", "AUTOMATISMO_CHAT_SUBITO", "AUTOMATISMO_CHAT_SUBITO_MAX"):
            if k in prima:
                os.environ[k] = prima[k]
            else:
                os.environ.pop(k, None)
    rif = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      "regole_impronta.json"), encoding="utf-8"))
    sr = rif["dati"]["sito_regole"]
    assert (sr["chat_subito"], sr["chat_pulita"], sr["chat_auto_vera"]) == (70, 0.3, 0.7)
    assert rif["ammessi_produzione"]["regole_impronta"] == "2248a486eb6c"      # regole di prezzo non toccate


def test_spenta_e_come_prima():
    a = auto(1)
    out, fun = elenco([a, auto(2, tel=True)], SPENTA)
    assert [it["id"] for it in out] == [2] and fun.tolti["telefono"] == [1]
    assert _passo(fun, "telefono")["motivi"] == {"senza_numero": 1}
    assert _passo(fun, "chat_subito") is None and fun.chat_subito == {} and "chat_subito" not in fun.tolti
    assert not any(k in it for it in out for k in ("chat_subito", "_chat", "_chat_attesa"))
    # acceso: l'auto col numero non cambia in niente
    out2, _ = elenco([auto(2, tel=True)])
    assert out2 == out


def test_privato_subito_in_zona_con_controllo_pulito_entra():
    out, fun = elenco([auto(1), auto(2, tel=True)])
    assert [it["id"] for it in out] == [1, 2]
    a = out[0]
    assert a["chat_subito"] is True and a["avviso_verifica"] is False and "commerciante" not in a
    assert a["_tipo"] == "privato" and a["pv"]["margine"] == 3000 and a["prezzo"] == 8000    # prezzi non toccati
    assert "chat_subito" not in out[1]
    assert fun.chat_subito == {"n": 70, "candidate": 1, "per_bene": 1, "entrate": 1, "attesa_ai": 0}
    assert fun.tolti["telefono"] == [] and fun.tolti["chat_subito"] == []
    assert _passo(fun, "chat_subito")["uscite"] == 0
    # Torino e Ravenna sono zona vera del sito
    assert ids([auto(3, prov="TO"), auto(4, prov="RA"), auto(5, prov="BG")]) == [3, 4, 5]


def test_solo_subito_solo_privati_solo_zona_vera():
    casi = {
        "senza_numero": [auto(1, fonte="AutoScout24"),                         # non ha la chat di Subito
                         auto(2, seller="commerciante", _r={"valutazione_nuova": True}),
                         auto(3, seller=None),                                  # venditore sconosciuto
                         auto(4, prov="RM"), auto(5, prov="LT"),               # Roma mai
                         auto(6, prov="VR"), auto(7, prov=None),               # fuori zona, provincia sconosciuta
                         # fascia richiesta fuori zona (benzina sotto 10.000 €, pochi km): niente corsia
                         auto(8, prov="VR", carb="benzina", anno=2016, km=60000),
                         auto(9, economica=True), auto(10, incidentata=True)],
        "facebook_spento": [auto(11, fonte="Facebook Marketplace")],
    }
    for motivo, lista in casi.items():
        for a in lista:
            out, fun = elenco([a])
            assert out == [], a["id"]
            assert _passo(fun, "telefono")["motivi"] == {motivo: 1}, a["id"]
            assert fun.chat_subito["candidate"] == 0
    # privato con segni da rivenditore: passa il telefono ma esce al passo venditore, come un'auto col numero
    out, fun = elenco([auto(12, _r={"segni_commerciante": True})])
    assert out == [] and fun.tolti["venditore"] == [12]
    # Facebook acceso (FB_SITO=1): entra come prima, MAI nella corsia chat
    out, fun = elenco([auto(13, fonte="Facebook Marketplace")], dataclasses.replace(G, fb_sito=True))
    assert [it["id"] for it in out] == [13] and "chat_subito" not in out[0] and fun.chat_subito["candidate"] == 0
    # Loconte, scarti del cliente, anno e km: come tutte le altre
    assert ids([auto(14, _r={"loconte": True}), auto(15, _r={"scarto_cliente": True}), auto(16, anno=2005),
                auto(17, km=160000), auto(18, km=None)]) == []


def test_supercar_senza_numero_come_prima():
    sc = auto(1, prov=None, supercar=True, prezzo=100000, margine=20000, M=130000, jev=None, _r={"sc_ok": True})
    for g in (G, SPENTA):
        out, fun = elenco([sc], g)
        assert [it["id"] for it in out] == [1] and "chat_subito" not in out[0]
        assert SR.esito_truffa(copie([sc])[0], g) == ("tieni", "")     # sconto 30.000 €: nessun controllo sconto alto
    assert elenco([sc])[1].chat_subito["candidate"] == 0


def test_esito_truffa_invariato_per_le_auto_col_numero():
    for a in (auto(1, tel=True), auto(2, tel=True, M=13000), auto(3, tel=True, M=13000, jev=None),
              auto(4, tel=True, jev=dict(CLAUDE, truffa=0.6)), auto(5, tel=True, jev=dict(JEV, truffa=0.9))):
        assert SR.esito_truffa(copie([a])[0], G) == SR.esito_truffa(copie([a])[0], SPENTA)
    # senza numero fuori dalla corsia (AutoScout24): "tieni" come prima anche con lo sconto alto
    a = copie([auto(6, fonte="AutoScout24", M=13000, jev=None)])[0]
    assert SR.esito_truffa(a, G) == ("tieni", "")
    # truffa da avviso senza numero: fuori, anche nella corsia
    out, fun = elenco([auto(7, jev=dict(CLAUDE, truffa=0.55))])
    assert out == [] and _passo(fun, "truffa")["motivi"] == {"truffa_senza_numero": 1}


# ------------------------------------------------------------------ controllo Claude
def test_senza_controllo_claude_resta_fuori_e_va_in_coda():
    _pulisci_coda()
    items = copie([auto(1, jev=None),                              # mai controllata
                   auto(2, jev=dict(JEV)),                         # solo Jev: non basta
                   auto(3, jev=dict(CLAUDE, stesso_prezzo=False)),  # Claude a un altro prezzo
                   auto(4),                                        # pulita: entra
                   auto(5, jev=dict(CLAUDE, auto_vera=0.5)),       # controllata e non pulita: non si richiede
                   auto(6, jev=None, margine=6000)])
    out, fun = SR.elenco_sito(items, G, scrivi_log=False)
    assert [it["id"] for it in out] == [4]
    assert _passo(fun, "chat_subito")["motivi"] == {"senza_controllo_ai": 4, "controllo_non_pulito": 1}
    assert fun.chat_subito == {"n": 70, "candidate": 6, "per_bene": 1, "entrate": 1, "attesa_ai": 4}
    assert sorted(fun.tolti["chat_subito"]) == [1, 2, 3, 5, 6]
    res = CA.coda_dal_sito(items, out, fun, G)
    assert CA.CODA_SITO["chat"] == [6, 1, 2, 3]                    # margine/soglia più alto prima, poi l'id
    assert CA.CODA_SITO["entra"] == [6, 1, 2, 3] and res["chat_in_coda"] == 4
    assert not ({1, 2, 3, 5, 6} & CA.CODA_SITO["fuori"])           # il passo chat_subito non ferma il controllo
    assert CA.parametri_diesel()["chat_ids"] == [6, 1, 2, 3]
    # interruttore spento (tetto 0): nessuna coda, e le auto tornano "fuori" al passo telefono
    items = copie([auto(1, jev=None)])
    out, fun = SR.elenco_sito(items, SPENTA, scrivi_log=False)
    res = CA.coda_dal_sito(items, out, fun, SPENTA)
    assert CA.CODA_SITO["chat"] == [] and "chat_in_coda" not in res and 1 in CA.CODA_SITO["fuori"]
    assert CA.parametri_diesel()["chat_ids"] == []
    # coda vecchia (nessun elenco da 30 minuti): lista vuota, come prima
    CA.CODA_SITO.update(chat=[9], t=time.time() - 31 * 60)
    assert CA.parametri_diesel()["chat_ids"] == []
    _pulisci_coda()


def test_coda_al_massimo_due_volte_il_tetto_dopo_lo_sconto_alto():
    _pulisci_coda()
    g = dataclasses.replace(G, chat_subito=3)
    items = copie([auto(k, jev=None, margine=3000 + k) for k in range(1, 11)]
                  + [auto(50, tel=True, M=13000, jev=None, margine=4000)])     # sconto alto col numero: prima
    out, fun = SR.elenco_sito(items, g, scrivi_log=False)
    assert out == []
    res = CA.coda_dal_sito(items, out, fun, g)
    assert res["chat_in_coda"] == 6 and CA.CODA_SITO["chat"] == [10, 9, 8, 7, 6, 5]
    assert CA.CODA_SITO["entra"] == [50, 10, 9, 8, 7, 6, 5]
    assert CA._conta_coda()["totale_sconto_alto"] == 1             # i conteggi dello sconto alto non cambiano
    _pulisci_coda()


def test_sql_del_controllo_richiede_claude_per_la_corsia():
    riga = "OR (COALESCE(d.fonte, 'jev') <> 'claude' AND c.id = ANY(%(chat_ids)s::bigint[]))"
    for sql in (CA.CAND_SQL, CA.CAND_SQL_RIPIEGO, CA.CAND_SQL_SITO):
        assert riga in sql
    assert CA.parametri_diesel()["chat_ids"] == [] or isinstance(CA.parametri_diesel()["chat_ids"], list)
    assert "telefono" in CA.PASSI_SENZA_AI and "chat_subito" not in CA.PASSI_SENZA_AI


def test_controllo_non_pulito_resta_fuori():
    casi = [auto(1, jev=dict(CLAUDE, danni=0.4)), auto(2, jev=dict(CLAUDE, truffa=0.35)),
            auto(3, jev=dict(CLAUDE, guasto=0.4)), auto(4, jev=dict(CLAUDE, auto_vera=0.6)),
            auto(5, jev=dict(CLAUDE, danni=None)),                          # foto non viste
            auto(6, jev=dict(CLAUDE, foto_internet=0.6)), auto(7, jev=dict(CLAUDE, dati_coerenti=0.3)),
            auto(8, jev=dict(CLAUDE, decisione="scarta", confidenza=0.3)),
            auto(9, _r={"danno_dichiarato": True}), auto(10, _r={"difetto": {"costo": 100, "motivo": "x"}}),
            auto(11, _r={"perizia": {"giudizio": "caro"}}), auto(12, _r={"perizia": {"giudizio": "da verificare"}}),
            auto(13, jev=dict(CLAUDE, km_ok=0.4))]
    for a in casi:
        out, fun = elenco([a])
        assert out == [], a["id"]
        assert _passo(fun, "chat_subito")["motivi"] == {"controllo_non_pulito": 1}, a["id"]
        assert fun.chat_subito["attesa_ai"] == 0
    # decisioni non lette (errore del database): fuori, senza riempire la coda
    out, fun = elenco([auto(14, jev=None, _r={"jev_errore": True})])
    assert out == [] and fun.chat_subito["attesa_ai"] == 0


# ------------------------------------------------------------------ nessuna auto con avvisi
def test_sconto_alto_avvisi_perizia_e_da_trattare_fuori():
    _pulisci_coda()
    # sconto 5.000 € (limite 4.000 sotto i 10.000 €): senza Claude fuori al passo truffa, come le auto col numero
    items = copie([auto(1, M=13000, jev=None), auto(2, M=13000, jev=dict(JEV))])
    out, fun = SR.elenco_sito(items, G, scrivi_log=False)
    assert out == [] and _passo(fun, "truffa")["motivi"] == {"sconto_alto_da_controllare": 1}
    # con la sola decisione di Jev pulita un'auto col numero resta (regola di prima); nella corsia no: sconto alto
    assert _passo(fun, "chat_subito")["motivi"] == {"con_avviso": 1} and fun.chat_subito["attesa_ai"] == 0
    res = CA.coda_dal_sito(items, out, fun, G)                     # confermata avrebbe l'avviso: nessuna chiamata
    assert res["non_entrerebbe:chat_subito"] == 1 and CA.CODA_SITO["inutili"] == {1}
    assert CA.CODA_SITO["entra"] == [] and res["chat_in_coda"] == 0
    # sconto alto confermato da Claude: avrebbe "Verificare prima di pagare" → la corsia non la tiene
    out, fun = elenco([auto(3, M=13000)])
    assert out == [] and _passo(fun, "chat_subito")["motivi"] == {"con_avviso": 1}
    assert elenco([auto(3, M=13000, tel=True)])[0][0]["avviso_verifica"] is True      # col numero entra con l'avviso
    # segnali duri: fuori al passo truffa come tutte
    out, fun = elenco([auto(4, M=30000), auto(5, _r={"testo_truffa": True}), auto(6, _r={"tel_estero": True})])
    assert out == [] and fun.tolti["truffa"] == [4, 5, 6]
    # "Da trattare" (fascia richiesta, annuncio recente, margine appena sotto il minimo): la corsia non la tiene
    da_tr = dict(carb="benzina", anno=2016, km=60000, margine=scovo.soglia(8000) - 300,
                 visto_dal="2099-01-01T00:00:00+00:00")
    assert elenco([auto(7, tel=True, **da_tr)])[0][0].get("trattare")
    out, fun = elenco([auto(7, **da_tr)])
    assert out == [] and _passo(fun, "chat_subito")["motivi"] == {"con_avviso": 1}
    assert fun.chat_subito["attesa_ai"] == 0
    # con un avviso e senza controllo Claude: non va in coda (non entrerebbe comunque)
    items = copie([auto(8, jev=None, **da_tr)])
    out, fun = SR.elenco_sito(items, G, scrivi_log=False)
    assert CA.coda_dal_sito(items, out, fun, G)["chat_in_coda"] == 0
    # versione "solo nome" e stima di ripiego: fuori
    out, fun = elenco([auto(9, jev=dict(CLAUDE, versione_ok=0.1), _r={"versione_tecnica": "uguale"})])
    assert out == [] and _passo(fun, "chat_subito")["motivi"] == {"con_avviso": 1}
    a = auto(10, _r={"ripiego_livelli": ("unione",)})
    a["pv"]["stima_ripiego"] = {"livello": "unione"}
    out, fun = elenco([a])
    assert out == [] and _passo(fun, "chat_subito")["motivi"] == {"con_avviso": 1}
    # margine sotto il minimo: fuori al passo margine_minimo come tutte
    out, fun = elenco([auto(11, margine=scovo.soglia(8000) - 1)])
    assert out == [] and fun.tolti["margine_minimo"] == [11]
    for it in elenco([auto(k, prezzo=p, margine=m, M=p + 1500) for k, (p, m) in enumerate(
            [(3500, 1500), (8000, 1800), (12000, 2600), (18000, 3300), (25000, 4100)], start=20)])[0]:
        assert it["chat_subito"] and it["pv"]["margine"] >= scovo.soglia(it["prezzo"])
    _pulisci_coda()


# ------------------------------------------------------------------ tetto
def test_tetto_le_migliori_per_margine_su_soglia():
    g = dataclasses.replace(G, chat_subito=3)
    items = [auto(1, margine=3000),                               # 3000/1800 = 1,67
             auto(2, prezzo=25000, M=27000, margine=5000),        # 5000/4000 = 1,25 (più euro, meno rispetto alla soglia)
             auto(3, margine=3600),                               # 2,00
             auto(4, prezzo=5000, M=6500, margine=2700),          # 2700/1500 = 1,80
             auto(5, margine=3000),                               # 1,67: a parità vince l'id più basso (la 1)
             auto(6, tel=True, margine=1900)]                     # col numero: fuori dal tetto, resta
    out, fun = elenco(items, g)
    assert sorted(it["id"] for it in out) == [1, 3, 4, 6]
    assert sorted(it["id"] for it in out if it.get("chat_subito")) == [1, 3, 4]
    assert _passo(fun, "chat_subito")["motivi"] == {"oltre_3": 2} and sorted(fun.tolti["chat_subito"]) == [2, 5]
    assert fun.chat_subito == {"n": 3, "candidate": 5, "per_bene": 5, "entrate": 3, "attesa_ai": 0}
    # default: 80 buone → 70, le migliori
    tante = [auto(100 + k, margine=3000 + 10 * k) for k in range(80)]
    out, fun = elenco(tante)
    assert len(out) == 70 and min(it["id"] for it in out) == 110
    assert _passo(fun, "chat_subito")["motivi"] == {"oltre_70": 10}
    # un secondo elenco sulle stesse auto ripulisce i segni
    lista = copie(tante[:2])
    SR.elenco_sito(lista, G, scrivi_log=False)
    out, _ = SR.elenco_sito(lista, SPENTA, scrivi_log=False)
    assert out == [] and not any(k in it for it in lista for k in ("chat_subito", "_chat", "_chat_attesa"))


def test_tetti_dei_commercianti_e_privati_col_numero_invariati():
    chat = [auto(9000 + k, margine=4000 + k) for k in range(90)]
    prima, fun0 = SR.elenco_sito(copie(_mondo()), G, scrivi_log=False)
    out, fun = SR.elenco_sito(copie(_mondo() + chat), G, scrivi_log=False)
    t0, t = _tipi(prima), _tipi([it for it in out if not it.get("chat_subito")])
    assert t == t0 and sum(n for k, n in t.items() if str(k).startswith("comm_")) == 60 and t["comm_lontano"] == 40
    assert {it["id"] for it in prima} == {it["id"] for it in out if not it.get("chat_subito")}
    assert sum(1 for it in out if it.get("chat_subito")) == 70
    assert all(it["_tipo"] == "privato" and not it.get("commerciante") for it in out if it.get("chat_subito"))
    for nome in ("tetto_commercianti", "tetto_commercianti_totale", "diesel_extra"):
        assert _passo(fun, nome)["uscite"] == _passo(fun0, nome)["uscite"]
        assert fun.tolti[nome] == fun0.tolti[nome]
    nomi = [p["passo"] for p in fun.passi]
    assert nomi.index("diesel_extra") + 1 == nomi.index("chat_subito") == nomi.index("tetto_commercianti_totale") - 1


def test_doppione_col_numero_vince():
    stessa = dict(marca="Fiat", modello="Panda", zona="Milano (MI)")
    a = auto(1, margine=3500, n_foto=10, **stessa)                       # Subito senza numero (più foto, più margine)
    b = auto(2, fonte="AutoScout24", tel=True, n_foto=2, **stessa)       # la stessa auto col numero
    out, fun = elenco([a, b])
    assert [it["id"] for it in out] == [2] and fun.tolti["doppioni"] == [1] and "chat_subito" not in out[0]
    assert fun.chat_subito["entrate"] == 0 and fun.tolti["chat_subito"] == []


# ------------------------------------------------------------------ interruttori senza deploy (web/app)
class _Db:
    def __init__(self, valore):
        self.valore = valore

    def __enter__(self):
        impostazioni.usa_lettore(lambda: {"automatismi": self.valore} if self.valore is not None else {})

    def __exit__(self, *a):
        impostazioni.usa_lettore(None)


def test_interruttore_e_numero_senza_deploy(monkeypatch):
    from dealfinder.web import app as A
    for k in list(os.environ):
        if k.startswith(automatismi.PREFISSO_ENV):
            monkeypatch.delenv(k, raising=False)
    assert "chat_subito" in automatismi.NOMI
    with _Db(None):
        assert A._tetto_chat_subito(G) == 70
    with _Db({"chat_subito": False}):
        assert A._tetto_chat_subito(G) == 0
    with _Db({"chat_subito_max": 25}):
        assert A._tetto_chat_subito(G) == 25
    with _Db({"chat_subito_max": 500}):
        assert A._tetto_chat_subito(G) == 70                    # mai sopra il numero della regola
    with _Db({"chat_subito_max": -4}):
        assert A._tetto_chat_subito(G) == 0
    with _Db({"chat_subito_max": "boh", "chat_subito": "boh"}):
        assert A._tetto_chat_subito(G) == 70                    # illeggibile = regola
    with _Db(None):
        monkeypatch.setenv("AUTOMATISMO_CHAT_SUBITO", "0")
        assert A._tetto_chat_subito(G) == 0
        monkeypatch.setenv("AUTOMATISMO_CHAT_SUBITO", "1")
        monkeypatch.setenv("AUTOMATISMO_CHAT_SUBITO_MAX", "12")
        assert A._tetto_chat_subito(G) == 12
        assert A._tetto_chat_subito(dataclasses.replace(G, chat_subito=5)) == 5
    # con il tetto 0 l'elenco è quello di prima
    g0 = dataclasses.replace(G, chat_subito=0)
    assert ids([auto(1), auto(2, tel=True)], g0) == [2]


# ------------------------------------------------------------------ dati dell'auto e log
def test_nessun_numero_nei_dati_e_solo_conteggi_nel_log():
    import logging
    righe = []

    class _H(logging.Handler):
        def emit(self, rec):
            righe.append(rec.getMessage())
    h = _H()
    SR.log.addHandler(h)
    try:
        items = copie([auto(1), auto(2, jev=None), auto(3, tel=True)])
        prima = {it["id"]: set(it) for it in items}
        out, fun = SR.elenco_sito(items, G, scrivi_log=True)
    finally:
        SR.log.removeHandler(h)
    nuove = set().union(*(set(it) - prima[it["id"]] for it in items))
    assert nuove <= {"_tipo", "avviso_verifica", "chat_subito", "_chat", "_chat_attesa"}
    assert all(it.get("chat_subito") in (None, True) for it in items)
    assert not any("tel" in k or "phone" in k or "numero" in k for it in out for k in it if k != "_r")
    riga = next(r for r in righe if r.startswith("SITO_FUNNEL "))
    dati = json.loads(riga[len("SITO_FUNNEL "):])
    assert dati["chat_subito"] == {"n": 70, "candidate": 2, "per_bene": 1, "entrate": 1, "attesa_ai": 1}
    assert dati["regole"] == G.impronta() and "piu_auto" in dati
    assert all(isinstance(v, int) for v in dati["chat_subito"].values())
    p = next(x for x in dati["passi"] if x["passo"] == "chat_subito")
    assert p["motivi"] == {"senza_controllo_ai": 1} and p["uscite"] == 1
    assert ("chat_subito.senza_controllo_ai", 3, 1) in SR.righe_giorno(fun)
    # spenta: nessuna chiave in più nella riga
    righe.clear()
    SR.log.addHandler(h)
    try:
        SR.elenco_sito(copie([auto(1)]), SPENTA, scrivi_log=True)
    finally:
        SR.log.removeHandler(h)
    dati = json.loads(next(r for r in righe if r.startswith("SITO_FUNNEL "))[len("SITO_FUNNEL "):])
    assert "chat_subito" not in dati and not any(x["passo"] == "chat_subito" for x in dati["passi"])


def test_claude_pulito_e_la_stessa_regola_del_diesel():
    for j in (CLAUDE, dict(CLAUDE, danni=0.3), dict(CLAUDE, truffa=0.29), dict(CLAUDE, auto_vera=0.69),
              dict(CLAUDE, fonte="jev"), dict(CLAUDE, km_ok=0.5), None):
        it = {"_r": {"jev": j}}
        atteso = bool(j) and j["fonte"] == "claude" and j["danni"] < 0.3 and j["truffa"] < 0.3 and j["auto_vera"] >= 0.7
        assert SR._claude_pulito(it, G.diesel_pulita, G.diesel_auto_vera) is atteso
        assert SR._claude_pulito(it, G.chat_pulita, G.chat_auto_vera) is atteso


# ------------------------------------------------------------------ sentinella
class _Ris:
    def __init__(self, righe):
        self.righe = righe

    def fetchall(self):
        return self.righe

    def fetchone(self):
        return self.righe[0] if self.righe else None


class _Conn:
    def __init__(self, testi):
        self.testi = testi

    def execute(self, sql, p=None):
        if "/*S:testi*/" not in sql:
            return _Ris([])
        return _Ris([t for t in self.testi if t["id"] in set(p[0])])

    def commit(self):
        pass

    def rollback(self):
        pass


def _riga(i, **extra):
    base = {"listing_id": i, "prezzo": 8000, "margine": 3000, "soglia": 1800, "fonte": "Subito", "commerciante": False,
            "provincia": "MI", "area": "milano", "km": 90000, "sezione": "affari", "categoria": "opportunita"}
    base.update(extra)
    return base


def _chiavi(esito):
    return {a["chiave"] for a in esito["allarmi"]}


def test_sentinella_fotografia_e_sezione():
    soglia = S._soglia_locale()
    r = S._riga_istantanea({"id": 5, "prezzo": 8000, "pv": {"margine": 3000}, "fonte": "Subito", "chat_subito": True,
                            "zona": "Monza (MB)"}, soglia)
    assert r[9] == "chat_subito" and r[5] is False and r[4] == "Subito"
    assert S._riga_istantanea({"id": 6, "prezzo": 8000, "pv": {"margine": 3000}, "fonte": "Subito"}, soglia)[9] == "affari"
    assert S.sezione_base("chat_subito") == "affari" and S.sezione_base("fascia") == "affari"
    assert S.sezione_base("fascia:inc") == "inc" and S.sezione_base("diesel_extra") == "diesel_extra"
    # la corsia è controllata sul margine minimo come le altre
    sotto = S.c_sotto_soglia(_Conn([]), {"istantanea": [_riga(1, sezione="chat_subito", margine=900)]})
    assert _chiavi(sotto) == {"sotto_soglia"} and sotto["numeri"]["controllate"] == 1


def test_sentinella_esente_dal_telefono_e_allarme_sul_tetto():
    testi = [{"id": i, "title": "Panda", "description": "", "telefono_ok": None} for i in range(1, 80)]
    ctx = {"istantanea": [_riga(i, sezione="chat_subito") for i in range(1, 71)], "istantanea_ore": 0.5}
    r = S.c_regole_cliente(_Conn(testi), ctx)
    assert r["ok"] and r["numeri"]["senza_telefono"] == 0
    assert r["numeri"]["chat_subito"] == 70 and r["numeri"]["chat_subito_tetto"] == 70
    # la stessa auto senza numero fuori dalla corsia: allarme regole:telefono come prima
    r = S.c_regole_cliente(_Conn(testi), {"istantanea": [_riga(1), _riga(2, sezione="chat_subito")]})
    assert _chiavi(r) == {"regole:telefono"} and r["numeri"]["senza_telefono"] == 1
    # oltre il tetto
    ctx = {"istantanea": [_riga(i, sezione="chat_subito") for i in range(1, 72)]}
    r = S.c_regole_cliente(_Conn(testi), ctx)
    assert _chiavi(r) == {"regole:chat_subito"}
    al = r["allarmi"][0]
    assert al["dati"]["n"] == 71 and al["dati"]["tetto"] == 70 and "71" in al["testo"]
    # non Subito o di un commerciante nella corsia
    ctx = {"istantanea": [_riga(1, sezione="chat_subito", fonte="AutoScout24"),
                          _riga(2, sezione="chat_subito", commerciante=True), _riga(3, sezione="chat_subito")]}
    r = S.c_regole_cliente(_Conn(testi), ctx)
    assert _chiavi(r) == {"regole:chat_subito"} and r["allarmi"][0]["dati"]["esempi"] == [1, 2]
    # i commercianti del tetto non contano la corsia
    assert S.c_tetto_commercianti(_Conn([]), {"istantanea": [_riga(i, sezione="chat_subito") for i in range(70)]})["ok"]


# ------------------------------------------------------------------ scheda (app.js)
def test_scheda_bottone_chat_e_link_invariato():
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dealfinder", "web", "static",
                        "scovo")
    js = open(os.path.join(base, "app.js"), encoding="utf-8").read()
    assert "Contatta con la chat di Subito" in js
    assert "Il venditore non mostra il numero: si apre l'annuncio su Subito per scrivergli in chat" in js
    assert 'apRiga("Contatto", "Solo chat di Subito", "info")' in js and '{t: "Chat Subito", cls: "chat"}' in js
    assert js.count("/api/affari/${c.id}/vai") == 1 and "Parla col venditore" in js      # il link resta /vai
    assert ".k5-sg.chat" in open(os.path.join(base, "app.css"), encoding="utf-8").read()
