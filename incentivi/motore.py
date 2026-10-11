"""Motore di regole: valutazione DETERMINISTICA di ogni misura per un'impresa.

Ogni regola del catalogo dà uno di tre esiti, con il dato usato e la sua fonte:
  "ok"     ✅ soddisfatto
  "ko"     ❌ non soddisfatto
  "dubbio" ❓ da verificare (dato mancante o non certo)
Nessuna AI qui dentro: l'AI può solo spiegare a parole il risultato.

Etichetta della misura:
  "conviene"     tutte le regole ✅ e la misura è aperta
  "da_valutare"  nessun ❌ ma almeno un ❓ (o misura in arrivo/ricorrente, o prestito/garanzia)
  "non_fa_per_te" almeno un ❌ (il motivo è il primo ❌)
"""
from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache
from pathlib import Path

CATALOGO = Path(__file__).resolve().parent / "data" / "misure.json"

TIPI = {
    "fondo_perduto": ("Fondo perduto", "Soldi che non restituisci"),
    "sgravio_contributivo": ("Sgravio contributivo", "Paghi meno contributi INPS"),
    "credito_imposta": ("Credito d'imposta", "Paghi meno tasse"),
    "deduzione_fiscale": ("Deduzione fiscale", "Abbassa l'imponibile: paghi meno tasse"),
    "finanziamento_agevolato": ("Finanziamento agevolato", "Prestito DA RESTITUIRE, a condizioni migliori"),
    "garanzia": ("Garanzia", "Lo Stato garantisce il tuo prestito: è più facile averlo, ma va restituito"),
    "servizio_gratuito": ("Formazione gratuita", "Il corso lo paga il fondo, non tu"),
    "riduzione_premio": ("Riduzione premio INAIL", "Paghi meno assicurazione INAIL"),
}
# tipi che contano nel "valore stimato" (soldi non da restituire)
CONTA_NEL_VALORE = {"fondo_perduto", "sgravio_contributivo", "servizio_gratuito", "riduzione_premio", "credito_imposta"}

CHIUSI = {"chiuso", "esaurito", "non_esistente"}


@lru_cache(maxsize=1)
def _load(path: str = str(CATALOGO)) -> dict:
    return json.loads(Path(path).read_text())


def catalogo() -> dict:
    return _load()


def _d(s) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def it_date(s) -> str:
    d = _d(s)
    return d.strftime("%d/%m/%Y") if d else "—"


def eur(n) -> str:
    return f"{int(round(n)):,} €".replace(",", ".")


# ---------------------------------------------------------------- stato del bando
def stato_bando(m: dict, oggi: dt.date) -> dict:
    """Stato calcolato dalle date (prevalgono su quello scritto a mano se più recenti)."""
    st = m.get("stato") or "da_verificare"
    d = m.get("date") or {}
    ap, ch, che = _d(d.get("apertura")), _d(d.get("chiusura")), _d(d.get("chiusura_effettiva"))
    if che and che <= oggi:
        st = "esaurito" if st == "esaurito" else "chiuso"
    elif ch and ch < oggi and st not in ("sempre_attivo",):
        st = "chiuso"
    elif ap and ap > oggi and st not in CHIUSI:
        st = "in_arrivo"
    elif st == "in_arrivo" and ap and ap <= oggi:
        st = "aperto"
    giorni = (ch - oggi).days if ch and ch >= oggi else None
    if st in CHIUSI:
        etichetta = {"non_esistente": "Non esiste nel 2026", "esaurito": "Fondi esauriti"}.get(st, "Chiuso")
        if che or ch:
            etichetta += f" il {(che or ch).strftime('%d/%m/%Y')}"
    elif st == "in_arrivo":
        etichetta = f"Apre il {ap.strftime('%d/%m/%Y')}" if ap else "Apre a breve"
    elif st == "ricorrente":
        etichetta = "Ricorrente" + (f" — scadenza {ch.strftime('%d/%m/%Y')}" if ch else "")
    elif st == "sempre_attivo":
        etichetta = "Sempre attivo" + (f" fino al {ch.strftime('%d/%m/%Y')}" if ch else "")
    else:
        etichetta = f"Aperto — chiude il {ch.strftime('%d/%m/%Y')}" if ch else "Aperto (a sportello, fino a esaurimento)"
    return {"stato": st, "etichetta": etichetta, "giorni_alla_chiusura": giorni,
            "apertura": d.get("apertura"), "chiusura": d.get("chiusura")}


# ---------------------------------------------------------------- dati del profilo
def _src(profilo: dict, campo: str) -> str:
    c = (profilo.get("campi") or {}).get(campo) or {}
    if c.get("stato") == "verificato":
        return c.get("fonte") or "visura"
    return "mancante"


def _ateco(profilo: dict) -> list[str]:
    """Codici ATECO 2007 (quelli usati dai bandi), primario per primo."""
    out = []
    for a in profilo.get("ateco") or []:
        code = str(a.get("codice") or "")
        if code:
            out.append(code)
    return out


def _sedi(profilo: dict) -> list[dict]:
    sedi = [profilo.get("sede") or {}] + list(profilo.get("unita_locali") or [])
    return [s for s in sedi if s]


def dimensione(profilo: dict) -> tuple[str | None, str]:
    n = profilo.get("addetti")
    if n is None:
        return None, "addetti non disponibili"
    if n < 10:
        return "micro", f"{n} addetti: microimpresa (fatturato non letto, presunta)"
    if n < 50:
        return "piccola", f"{n} addetti: piccola impresa (fatturato non letto, presunta)"
    if n < 250:
        return "media", f"{n} addetti: media impresa (fatturato non letto, presunta)"
    return "grande", f"{n} addetti"


def _mesi_attivita(profilo: dict, oggi: dt.date) -> int | None:
    d = _d(profilo.get("data_inizio_attivita") or profilo.get("data_costituzione"))
    if not d:
        return None
    return (oggi.year - d.year) * 12 + (oggi.month - d.month) - (1 if oggi.day < d.day else 0)


# ---------------------------------------------------------------- regole
def valuta_regola(r: dict, m: dict, profilo: dict, risposte: dict, oggi: dt.date) -> dict:
    t, p = r["tipo"], r.get("params") or {}
    esito, dato, fonte = "dubbio", None, "mancante"

    if t == "stato_misura":
        sb = stato_bando(m, oggi)
        dato, fonte = sb["etichetta"], f"bando, verificato il {it_date(m.get('ultima_verifica'))}"
        esito = "ok" if sb["stato"] in ("aperto", "sempre_attivo") else ("ko" if sb["stato"] in CHIUSI else "dubbio")

    elif t == "impresa_attiva":
        st = (profilo.get("stato") or "").lower()
        dato, fonte = profilo.get("stato") or "non letto", _src(profilo, "stato")
        esito = "ok" if st.startswith("attiv") else ("ko" if st else "dubbio")

    elif t in ("regione_in", "provincia_in"):
        sedi = _sedi(profilo)
        key = "regione" if t == "regione_in" else "provincia"
        vals = [str(s.get(key) or "") for s in sedi if s.get(key)]
        want = [v.lower() for v in (p.get("regioni") or p.get("province") or [])]
        dato, fonte = ", ".join(sorted(set(vals))) or "non letta", _src(profilo, "sede")
        if not vals:
            esito = "dubbio"
        else:
            esito = "ok" if any(v.lower() in want for v in vals) else "ko"

    elif t == "ateco_non_escluso":
        codes = _ateco(profilo)
        dato, fonte = ", ".join(codes) or "non letto", _src(profilo, "ateco")
        if not codes:
            esito = "dubbio"
        else:
            hit = [c for c in codes for e in p["esclusi"] if c.replace(".", "").startswith(e.replace(".", ""))]
            esito = "ko" if hit else "ok"
            if hit:
                dato = f"{hit[0]} è escluso dal bando"

    elif t == "ateco_ammesso":
        codes = _ateco(profilo)
        dato, fonte = ", ".join(codes) or "non letto", _src(profilo, "ateco")
        if codes:
            esito = "ok" if any(c.replace(".", "").startswith(a.replace(".", "")) for c in codes
                                for a in p["ammessi"]) else "ko"

    elif t == "dimensione_in":
        dim, txt = dimensione(profilo)
        dato, fonte = txt, _src(profilo, "addetti")
        esito = "dubbio" if dim is None else ("ok" if dim in p["ammesse"] else "ko")

    elif t == "anzianita_min":
        mesi = _mesi_attivita(profilo, oggi)
        dato, fonte = (f"{mesi} mesi di attività" if mesi is not None else "data non letta"), _src(profilo, "data_costituzione")
        esito = "dubbio" if mesi is None else ("ok" if mesi >= p["mesi"] else "ko")

    elif t in ("costituita_dal", "costituita_entro"):
        d = _d(profilo.get("data_inizio_attivita") or profilo.get("data_costituzione"))
        dato, fonte = (it_date(d.isoformat()) if d else "data non letta"), _src(profilo, "data_costituzione")
        lim = _d(p["data"])
        if d:
            esito = "ok" if ((d >= lim) if t == "costituita_dal" else (d <= lim)) else "ko"

    elif t == "addetti_min":
        n = profilo.get("addetti")
        dato, fonte = (f"{n} addetti" if n is not None else "non letto"), _src(profilo, "addetti")
        esito = "dubbio" if n is None else ("ok" if n >= p["minimo"] else "ko")

    elif t in ("risposta_vera", "risposta_falsa"):
        v = risposte.get(p["domanda"])
        fonte = "risposta del commerciante" if v is not None else "domanda non ancora risposta"
        dato = {True: "sì", False: "no", None: "—"}.get(v, str(v))
        if v is not None:
            esito = "ok" if bool(v) == (t == "risposta_vera") else "ko"

    elif t == "investimento_min":
        v = risposte.get("investimento")
        fonte = "risposta del commerciante" if v is not None else "domanda non ancora risposta"
        dato = eur(v) if v else ("nessuno" if v == 0 else "—")
        if v is not None:
            esito = "ok" if v >= (p.get("minimo") or 1) else "ko"

    elif t == "dato_mancante":
        v = (profilo.get("extra") or {}).get(p["campo"])
        if v is not None:
            esito, dato, fonte = ("ok" if v else "ko"), ("sì" if v else "no"), "dichiarato"
        else:
            dato = "da chiedere al commerciante o al consulente"

    return {"id": r["id"], "testo": r["testo"], "esito": esito, "dato": dato, "fonte": fonte,
            "riferimento": r.get("riferimento"), "nota": p.get("nota")}


# ---------------------------------------------------------------- beneficio stimato
def beneficio(m: dict, risposte: dict, profilo: dict) -> dict:
    """Stima con il calcolo visibile. Mai presentata come certa."""
    c = m.get("calcolo") or {}
    modo, perc, mass, unit, mesi = c.get("modo"), c.get("percentuale"), c.get("massimale"), c.get("importo_unitario"), c.get("durata_mesi")
    tipo = m.get("tipo_beneficio")
    out = {"valore": None, "calcolo": c.get("formula_testo") or "", "condizione": "", "investimento": None,
           "conta": tipo in CONTA_NEL_VALORE}
    if tipo in ("finanziamento_agevolato", "garanzia"):
        out.update(conta=False, valore=None, calcolo=c.get("formula_testo") or "",
                   condizione="Non sono soldi regalati: il prestito va restituito.")
        return out
    if "formazione" in (m.get("serve") or []) and modo == "percentuale_spesa" and mass:
        out.update(valore=mass, calcolo=f"{perc:g}% del costo del corso, fino a {eur(mass)} per persona",
                   condizione="per ogni persona formata (titolare compreso, se il bando lo ammette)")
        return out
    if tipo in ("deduzione_fiscale",):
        out["calcolo"] = (c.get("formula_testo") or "") + " Il risparmio in euro dipende dalle tue tasse: lo calcola il commercialista."
        out["conta"] = False
        return out
    if modo == "percentuale_spesa" and perc:
        spesa = risposte.get("investimento")
        smax = c.get("spesa_massima")
        if spesa:
            base = min(spesa, smax) if smax else spesa
            v = base * perc / 100
            if mass:
                v = min(v, mass)
            out["valore"] = v
            out["investimento"] = spesa
            out["calcolo"] = (f"{perc:g}% di {eur(base)} = {eur(base * perc / 100)}"
                              + (f", massimo {eur(mass)}" if mass and base * perc / 100 > mass else ""))
            out["condizione"] = "se i preventivi sono confermati e la spesa è ammissibile"
        elif mass:
            out["valore"] = mass
            out["calcolo"] = f"{perc:g}% della spesa, fino a {eur(mass)}"
            out["condizione"] = "valore massimo: dipende da quanto spendi"
    elif modo == "importo_mensile_per_assunto" and unit:
        v = unit * (mesi or 12)
        if mass:
            v = min(v, mass)
        out["valore"] = v
        out["calcolo"] = f"{eur(unit)} al mese × {mesi or 12} mesi = {eur(v)} per ogni assunto"
        out["condizione"] = "per ogni persona assunta che ha i requisiti"
    elif modo == "importo_per_persona" and (unit or mass):
        v = mass or unit
        out["valore"] = v
        out["calcolo"] = f"fino a {eur(v)} per persona"
        out["condizione"] = "per ogni persona formata"
    elif modo == "importo_fisso" and (mass or unit):
        out["valore"] = mass or unit
        out["calcolo"] = (c.get("formula_testo") or f"fino a {eur(mass or unit)}")
        out["condizione"] = "valore massimo"
    elif modo == "percentuale_costo_lavoro" and mass:
        out["valore"] = mass
        out["calcolo"] = (c.get("formula_testo") or "") or f"fino a {eur(mass)} per assunto"
        out["condizione"] = "valore massimo per assunto"
    return out


# ---------------------------------------------------------------- valutazione completa
def valuta_misura(m: dict, profilo: dict, risposte: dict, oggi: dt.date | None = None) -> dict:
    oggi = oggi or dt.date.today()
    regole = [valuta_regola(r, m, profilo, risposte, oggi) for r in m.get("regole") or []]
    ko = [r for r in regole if r["esito"] == "ko"]
    dub = [r for r in regole if r["esito"] == "dubbio"]
    sb = stato_bando(m, oggi)
    ben = beneficio(m, risposte, profilo)
    if ko:
        etichetta, motivo = "non_fa_per_te", ko[0]["testo"] + (f" — {ko[0]['dato']}" if ko[0].get("dato") else "")
    elif dub or sb["stato"] not in ("aperto", "sempre_attivo") or m.get("tipo_beneficio") in ("finanziamento_agevolato", "garanzia"):
        etichetta, motivo = "da_valutare", (dub[0]["testo"] + ": da verificare") if dub else sb["etichetta"]
    else:
        etichetta, motivo = "conviene", "Tutti i requisiti controllati risultano soddisfatti"
    docs = m.get("documenti_dettaglio") or []
    azione = next((d["azione"] for d in docs if d.get("azione")), None)
    if dub and not azione:
        azione = "Verifica: " + dub[0]["testo"].lower()
    giorni = sb["giorni_alla_chiusura"]
    urgenza = 0 if etichetta == "non_fa_per_te" else (3 if giorni is not None and giorni <= 30 else 2 if giorni is not None and giorni <= 90 else 1)
    return {
        "id": m["id"], "nome": m["nome"], "ente": m.get("ente"), "area": m.get("area"),
        "tipo": m.get("tipo_beneficio"), "tipo_nome": TIPI.get(m.get("tipo_beneficio"), ("", ""))[0],
        "tipo_spiegazione": TIPI.get(m.get("tipo_beneficio"), ("", ""))[1],
        "in_breve": m.get("in_breve"), "fonte_url": m.get("fonte_url"), "fonte_ufficiale": m.get("fonte_ufficiale", False), "altre_fonti": m.get("altre_fonti") or [],
        "etichetta": etichetta, "motivo": motivo, "regole": regole,
        "bando": sb, "beneficio": ben, "urgenza": urgenza,
        "copre_magazzino": bool(m.get("copre_magazzino")),
        "avviso_ordini": bool(m.get("vieta_ordini_prima_domanda")),
        "documenti": docs, "azione": azione,
        "piattaforma": m.get("piattaforma"), "cumulo": m.get("cumulo"),
        "spese_ammissibili": m.get("spese_ammissibili") or [], "serve": m.get("serve") or [], "requisiti_extra": m.get("requisiti_extra") or [],
        "ultima_verifica": m.get("ultima_verifica"), "verificatore": m.get("verificatore"),
        "dubbi_catalogo": m.get("dubbi") or [], "prossima_edizione": m.get("prossima_edizione_prevista"),
    }


DOMANDE = {
    "officina": {"testo": "Hai un'officina?", "tipo": "si_no"},
    "assume": {"testo": "Pensi di assumere qualcuno entro dicembre?", "tipo": "si_no"},
    "investimento": {"testo": "Vuoi comprare attrezzature o fare lavori nei prossimi 6 mesi? Quanto circa?",
                     "tipo": "euro", "aiuto": "Scrivi 0 se non pensi di spendere. Le auto da rivendere non contano."},
    "ordini": {"testo": "Hai già firmato ordini o pagato acconti per queste spese?", "tipo": "si_no"},
}


def domande_utili(profilo: dict, oggi: dt.date | None = None) -> list[dict]:
    """Solo le domande che servono alle misure non già escluse dai dati della visura (max 4)."""
    oggi = oggi or dt.date.today()
    serve: list[str] = []
    for m in catalogo()["misure"]:
        res = valuta_misura(m, profilo, {}, oggi)
        if res["etichetta"] == "non_fa_per_te":
            continue
        for r in m.get("regole") or []:
            d = (r.get("params") or {}).get("domanda") or ("investimento" if r["tipo"] == "investimento_min" else None)
            if d and d not in serve:
                serve.append(d)
    order = ["officina", "assume", "investimento", "ordini"]
    if "investimento" not in serve and "ordini" in serve:
        serve.remove("ordini")
    return [{"id": k, **DOMANDE[k]} for k in order if k in serve][:4]


def valuta_tutto(profilo: dict, risposte: dict, oggi: dt.date | None = None) -> dict:
    oggi = oggi or dt.date.today()
    cat = catalogo()
    res = [valuta_misura(m, profilo, risposte, oggi) for m in cat["misure"]]
    rank = {"conviene": 0, "da_valutare": 1, "non_fa_per_te": 2}
    res.sort(key=lambda r: (rank[r["etichetta"]], -r["urgenza"], -((r["beneficio"]["valore"] or 0) if r["beneficio"]["conta"] else 0)))
    utili = [r for r in res if r["etichetta"] != "non_fa_per_te" and r["bando"]["stato"] not in CHIUSI]
    candidabili = [r for r in utili if r["bando"]["stato"] in ("aperto", "sempre_attivo", "ricorrente")]
    # gli sgravi per le assunzioni sono alternativi tra loro per la stessa persona: si conta solo il più alto
    gruppi: dict = {}
    for r in candidabili:
        if not r["beneficio"]["conta"] or not r["beneficio"]["valore"]:
            continue
        g = "assunzione" if r["tipo"] == "sgravio_contributivo" else r["id"]
        gruppi[g] = max(gruppi.get(g, 0), r["beneficio"]["valore"])
    valore = sum(gruppi.values())
    urgenti = sorted([r for r in candidabili if r["bando"]["giorni_alla_chiusura"] is not None
                      and r["tipo"] not in ("garanzia", "finanziamento_agevolato")],
                     key=lambda r: (r["etichetta"] != "conviene", r["bando"]["giorni_alla_chiusura"]))
    avviso = None
    if urgenti:
        u = urgenti[0]
        avviso = {"id": u["id"], "testo": f"{u['nome']}: {u['bando']['etichetta'].lower()}",
                  "giorni": u["bando"]["giorni_alla_chiusura"]}
    if candidabili:
        n = len(candidabili)
        titolo = (f"Puoi candidarti a {n} opportunità" if n > 1 else "Puoi candidarti a 1 opportunità")
        sotto = (f"Valore stimato fino a {eur(valore)}" if valore else "Valore da stimare con i tuoi dati")
        if gruppi.get("assunzione"):
            sotto += " (per gli sgravi sulle assunzioni conta il migliore, per una persona)"
    else:
        titolo, sotto = "Oggi non c'è nulla di utile per te.", "Ti avvisiamo appena esce qualcosa."
    return {"versione_catalogo": cat["versione"], "data": oggi.isoformat(), "titolo": titolo, "sottotitolo": sotto,
            "valore_stimato": valore, "avviso": avviso, "risultati": res,
            "conteggi": {k: sum(1 for r in res if r["etichetta"] == k) for k in rank}}
