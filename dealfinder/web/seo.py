"""Pagine pubbliche per Google (SEO): città, argomenti e guide, scritte in HTML dal server.

Ogni pagina della città mostra dati veri presi dal database (annunci di privati letti, modelli più
venduti con il prezzo tipico, comuni coperti), così non sono pagine tutte uguali.
Indirizzi:
  /auto-usate/<città>            es. /auto-usate/milano, /auto-usate/bergamo
  /<argomento>                   es. /auto-incidentate-milano, /auto-sotto-prezzo-milano
  /guide/<guida>                 es. /guide/passaggio-di-proprieta-costo
Variabili: PUBLIC_URL (indirizzo del sito), GOOGLE_SITE_VERIFICATION (codice di Search Console).
"""
from __future__ import annotations

import html
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("seo")

BASE = os.environ.get("PUBLIC_URL", "https://scovo.onrender.com").rstrip("/")
OGGI = time.strftime("%Y-%m-%d")

# ---------------------------------------------------------------- città
CITTA = {
    "milano": {"nome": "Milano", "pv": "MI", "prov": "Città metropolitana di Milano",
               "comuni": ["Sesto San Giovanni", "Cinisello Balsamo", "Rho", "Legnano", "Cologno Monzese",
                          "Paderno Dugnano", "San Donato Milanese", "Rozzano", "Corsico", "Abbiategrasso",
                          "Pioltello", "Segrate", "Bollate", "San Giuliano Milanese", "Magenta", "Melzo",
                          "Gorgonzola", "Parabiago", "Cernusco sul Naviglio", "Buccinasco"],
               "nota": "Milano è il mercato dell'usato più grande d'Italia: ogni giorno escono centinaia di annunci di privati tra città e hinterland, e chi arriva primo sull'auto giusta tratta il prezzo migliore."},
    "monza": {"nome": "Monza", "pv": "MB", "prov": "provincia di Monza e della Brianza",
              "nota": "In Brianza girano tante auto di famiglia tenute bene, spesso con pochi chilometri: sono le prime a sparire dagli annunci."},
    "bergamo": {"nome": "Bergamo", "pv": "BG", "prov": "provincia di Bergamo",
                "nota": "Bergamo e la sua provincia hanno un mercato dell'usato molto vivo, con tante utilitarie e station wagon di privati."},
    "brescia": {"nome": "Brescia", "pv": "BS", "prov": "provincia di Brescia",
                "nota": "Brescia è una delle province con più annunci di auto usate della Lombardia: tante occasioni, ma serve guardarle prima degli altri."},
    "como": {"nome": "Como", "pv": "CO", "prov": "provincia di Como",
             "nota": "Tra Como, la Brianza comasca e il confine svizzero si trovano spesso auto ben accessoriate vendute da privati."},
    "varese": {"nome": "Varese", "pv": "VA", "prov": "provincia di Varese",
               "nota": "Varese, Busto Arsizio, Gallarate e Saronno: un'area con tanti annunci di privati e prezzi spesso più bassi di Milano."},
    "lecco": {"nome": "Lecco", "pv": "LC", "prov": "provincia di Lecco",
              "nota": "A Lecco e in Brianza lecchese le auto usate di privati sono meno, ma spesso tenute meglio della media."},
    "lodi": {"nome": "Lodi", "pv": "LO", "prov": "provincia di Lodi",
             "nota": "Il Lodigiano è vicino a Milano ma con prezzi dell'usato spesso più bassi: un buon posto dove cercare."},
    "pavia": {"nome": "Pavia", "pv": "PV", "prov": "provincia di Pavia",
              "nota": "Pavia, Vigevano e Voghera: tante auto di privati in vendita, a mezz'ora da Milano."},
    "cremona": {"nome": "Cremona", "pv": "CR", "prov": "provincia di Cremona",
                "nota": "Cremona e Crema hanno un usato con prezzi più bassi della media lombarda: utile per chi rivende a Milano."},
    "novara": {"nome": "Novara", "pv": "NO", "prov": "provincia di Novara",
               "nota": "Novara e i comuni tra il Ticino e Milano (Trecate, Galliate, Cameri) sono a pochi chilometri dal capoluogo lombardo."},
}

# ---------------------------------------------------------------- argomenti
ARGOMENTI = {
    "auto-sotto-prezzo-milano": {
        "titolo": "Auto usate sotto prezzo a Milano: le occasioni dei privati",
        "h1": "Auto usate sotto prezzo a Milano e in Lombardia",
        "desc": "Le auto usate dei privati messe in vendita sotto il prezzo di mercato a Milano e dintorni, con guadagno, pezzi e rivendita già calcolati. Da Subito, AutoScout24 e Facebook.",
        "testo": [
            ("Che cos'è un'auto sotto prezzo", "Un'auto è sotto prezzo quando il privato la mette in vendita a meno di quanto valgono le auto uguali: stessa marca, modello, anno, chilometri e alimentazione. Succede ogni giorno: chi deve vendere in fretta, chi non conosce il mercato, chi eredita un'auto o la sostituisce con una nuova."),
            ("Come le troviamo", "Scovo legge ogni giorno decine di migliaia di annunci di privati su Subito, AutoScout24 e Facebook Marketplace a Milano e in un raggio di circa 100 km. Per ogni auto calcoliamo il prezzo di mercato confrontandola con le auto simili in vendita in zona, e teniamo solo quelle che lasciano un guadagno vero dopo le spese."),
            ("Perché conviene arrivare primi", "Le auto davvero sotto prezzo restano online poche ore. Il commerciante che chiama per primo tratta meglio e spesso chiude l'affare prima che l'annuncio sparisca. Per questo ogni auto su Scovo può essere contattata al massimo da 7 commercianti."),
        ],
        "faq": [("Le auto sono di privati o di concessionari?", "Solo di privati. Gli annunci dei concessionari servono solo come prezzo di confronto."),
                ("Il guadagno è garantito?", "No: è una stima prudente. La rivendita è calcolata al 10% sotto il mercato e dal guadagno sono già tolti passaggio di proprietà, pulizia e pezzi da cambiare. Ogni scheda dice cosa controllare dal vivo.")],
    },
    "auto-incidentate-milano": {
        "titolo": "Auto incidentate in vendita a Milano da privati",
        "h1": "Auto incidentate e da sistemare a Milano e dintorni",
        "desc": "Auto incidentate, grandinate e da sistemare vendute da privati a Milano e in Lombardia, con i pezzi da comprare già stimati. Per carrozzieri e commercianti.",
        "testo": [
            ("Per carrozzieri e commercianti", "Un'auto incidentata comprata al prezzo giusto può rendere più di una sana. Serve però sapere quanto costano i pezzi e quanto vale l'auto una volta sistemata. Scovo lo calcola per te: guardiamo foto e descrizione, stimiamo i pezzi da comprare (nuovi compatibili o usati) e ti mostriamo il guadagno."),
            ("Cosa scartiamo", "Non ti mostriamo auto alluvionate, bruciate, con il telaio piegato o da demolire, né auto con motore o cambio da rifare senza un prezzo chiaro. Se il danno è strutturale o ci sono airbag esplosi lo scriviamo nella scheda, con i pezzi in più già nel conto."),
            ("Dove", "Milano, Monza e Brianza, Bergamo, Brescia, Como, Varese, Lecco, Lodi, Pavia, Cremona e Novara, da Subito, AutoScout24 e Facebook Marketplace."),
        ],
        "faq": [("La manodopera è nel conto?", "No: contiamo solo i pezzi. La manodopera la conosci meglio tu, o è la tua officina."),
                ("Come stimate i pezzi?", "Dalle foto e dalla descrizione: per ogni danno indichiamo il pezzo, il lato e un costo da–a.")],
    },
    "auto-economiche-sotto-1000-euro-milano": {
        "titolo": "Auto usate sotto 1.000 euro a Milano: le occasioni dei privati",
        "h1": "Auto sotto 1.000 € a Milano e in Lombardia",
        "desc": "Auto usate di privati sotto 1.000 euro a Milano e dintorni, dal 2010 in poi: utilitarie, prime auto e auto da commercio, ogni giorno da Subito, AutoScout24 e Facebook.",
        "testo": [
            ("Auto a pochi soldi, ogni giorno", "Sotto i 1.000 € si trovano utilitarie per neopatentati, seconde auto e auto da commercio. Sono anche le più rapide a sparire: Scovo raccoglie ogni giorno quelle dei privati dal 2010 in poi a Milano e in Lombardia."),
            ("Cosa guardare", "A questo prezzo conta lo stato reale: revisione, gomme, frizione, spie accese. Ogni scheda ti dice cosa chiedere al venditore prima di partire."),
        ],
        "faq": [("Sono controllate una per una?", "Le auto della sezione economica non sono ancora tutte controllate a mano: guarda bene foto e auto dal vivo.")],
    },
    "comprare-auto-da-privati-per-rivendere": {
        "titolo": "Comprare auto da privati per rivenderle: come trovare quelle giuste",
        "h1": "Comprare auto usate da privati per rivenderle",
        "desc": "Come trovano le auto i commercianti di usato: dove cercarle, come calcolare il guadagno e cosa controllare prima di comprare un'auto da un privato a Milano e in Lombardia.",
        "testo": [
            ("Il lavoro che fa Scovo al posto tuo", "Chi compra e rivende auto usate passa ore a scorrere annunci su Subito, AutoScout24 e Facebook Marketplace. Scovo lo fa per te più volte al giorno e ti mostra solo le auto dei privati con un margine reale, già calcolato."),
            ("Come si calcola il guadagno", "Guadagno = prezzo di rivendita prudente − prezzo pagato − passaggio di proprietà − pulizia − pezzi da cambiare. La rivendita prudente è il prezzo delle auto uguali in vendita in zona meno il 10%: un prezzo a cui l'auto si vende davvero in poche settimane."),
            ("Cosa controllare prima di comprare", "Chilometri e tagliandi, revisione, stato di frizione e cambio, eventuali spie accese, gomme, segni di incidenti non dichiarati. Per i diesel: FAP, EGR e turbo."),
        ],
        "faq": [("Quanto si guadagna con un'auto usata?", "Dipende dalla fascia: su Scovo mostriamo auto con almeno 800–1.600 € di guadagno stimato dopo le spese, a seconda del prezzo."),
                ("Serve la partita IVA?", "Per fare commercio di auto usate in modo continuativo sì: chiedi al tuo commercialista.")],
    },
    "valutazione-auto-usata-gratis": {
        "titolo": "Valutazione auto usata gratis: quanto vale la tua auto a Milano",
        "h1": "Quanto vale la tua auto usata?",
        "desc": "Scopri il prezzo giusto della tua auto usata dalla targa e dai chilometri, calcolato sulle auto uguali in vendita a Milano e in Lombardia, con un annuncio pronto da copiare.",
        "testo": [
            ("Il prezzo giusto, dai dati veri", "Con la targa e i chilometri Scovo trova le auto uguali alla tua in vendita in zona e ti dice a che prezzo metterla per venderla in fretta, senza svenderla."),
            ("Annuncio pronto", "Ti prepariamo anche il testo dell'annuncio, da copiare su Subito, AutoScout24 o Facebook Marketplace."),
        ],
        "faq": [("È gratis?", "La funzione Vendi è compresa nell'abbonamento Scovo per i commercianti.")],
    },
}

# ---------------------------------------------------------------- guide
GUIDE = {
    "passaggio-di-proprieta-costo": {
        "titolo": "Passaggio di proprietà auto: quanto costa e come si fa",
        "desc": "Quanto costa il passaggio di proprietà di un'auto usata, quali documenti servono e come si fa tra privati o con un commerciante, spiegato in modo semplice.",
        "testo": [
            ("Cos'è", "Il passaggio di proprietà trasferisce l'auto dal venditore al compratore al PRA e sulla carta di circolazione (il Documento Unico)."),
            ("Quanto costa", "Il costo cambia con la potenza dell'auto e la provincia: c'è l'IPT (imposta provinciale di trascrizione), gli emolumenti ACI, i diritti della Motorizzazione e le marche da bollo. Per un'utilitaria si parte da circa 150–200 €; per chi compra per rivendere esiste la minivoltura, più economica. Su Scovo nel conto del guadagno mettiamo 90 € di passaggio per i commercianti."),
            ("Documenti", "Documento Unico dell'auto, documenti di identità e codice fiscale di venditore e compratore. Si fa presso un'agenzia di pratiche auto, l'ACI o la Motorizzazione."),
        ],
    },
    "come-valutare-auto-usata": {
        "titolo": "Come valutare un'auto usata prima di comprarla",
        "desc": "La lista dei controlli da fare su un'auto usata prima di comprarla da un privato: documenti, chilometri, carrozzeria, motore, cambio, gomme e prova su strada.",
        "testo": [
            ("Documenti e storia", "Chiedi il libretto dei tagliandi, le fatture dei lavori, la data della prossima revisione. Controlla che i chilometri siano coerenti con l'età e con lo stato degli interni."),
            ("Carrozzeria", "Guarda le fessure tra i pannelli, differenze di colore, vernice sulle guarnizioni: sono segni di riparazioni. Controlla sotto i tappetini del bagagliaio per ruggine o pieghe."),
            ("Meccanica", "A motore freddo: avviamento, fumo dallo scarico, rumori. In prova: frizione che stacca alta, cambio che gratta, spie accese, sterzo che tira."),
            ("Il prezzo giusto", "Confronta l'auto con quelle uguali in vendita in zona: stessa versione, anno e chilometri. È quello che Scovo fa in automatico per ogni annuncio."),
        ],
    },
    "come-trovare-auto-sotto-prezzo": {
        "titolo": "Come trovare auto usate sotto prezzo prima degli altri",
        "desc": "Dove e quando cercare le auto usate sotto prezzo dei privati, come riconoscere un vero affare da una truffa e come calcolare il margine di rivendita.",
        "testo": [
            ("Velocità", "Le occasioni vere durano poche ore. Controlla gli annunci più volte al giorno, o usa uno strumento che lo fa per te."),
            ("Prezzo troppo bello", "Un'auto a metà del suo valore senza una spiegazione nel testo è spesso una truffa o un dato sbagliato (chilometri, anno, modello). Diffida di chi chiede anticipi o non fa vedere l'auto."),
            ("Margine vero", "Il margine si calcola sul prezzo a cui rivendi davvero, non sul prezzo più alto che vedi online: togli almeno il 10% per la trattativa, poi passaggio, pulizia e pezzi."),
        ],
    },
}

# ---------------------------------------------------------------- dati veri dal database
_cache: dict = {}


def _comuni_vicini(pv: str, n: int = 18) -> list[str]:
    if CITTA.get(next((k for k, v in CITTA.items() if v["pv"] == pv), ""), {}).get("comuni"):
        return CITTA[next(k for k, v in CITTA.items() if v["pv"] == pv)]["comuni"][:n]
    try:
        d = json.loads((Path(__file__).resolve().parents[2] / "data" / "comuni_60km.json").read_text())
        rows = sorted((v for v in d.values() if v.get("pv") == pv), key=lambda v: v.get("km") or 999)
        return [r["comune"] for r in rows[:n]]
    except Exception:
        return []


def _stats(pv: str) -> dict:
    """Annunci di privati attivi e modelli più presenti, con il prezzo tipico (cache 6 ore)."""
    hit = _cache.get(pv)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    out: dict = {}
    try:
        from ..db import connect
        from .scovo import make_name, model_name
        with connect() as conn:
            r = conn.execute("""SELECT count(*) AS n FROM listings WHERE status='attivo' AND province=%s
                                AND COALESCE(seller_type,'') <> 'commerciante'""", (pv,)).fetchone()
            out["annunci"] = int(r["n"] or 0)
            rows = conn.execute("""SELECT make, model, count(*) AS n,
                                          percentile_cont(0.5) WITHIN GROUP (ORDER BY price_eur) AS prezzo,
                                          percentile_cont(0.5) WITHIN GROUP (ORDER BY year) AS anno
                                   FROM listings WHERE status='attivo' AND province=%s AND price_eur BETWEEN 500 AND 80000
                                     AND make IS NOT NULL AND model IS NOT NULL
                                   GROUP BY make, model HAVING count(*) >= 5 ORDER BY count(*) DESC LIMIT 12""",
                                (pv,)).fetchall()
            out["modelli"] = [{"nome": f"{make_name(x['make'])} {model_name(x['model'])}".strip(), "n": int(x["n"]),
                               "prezzo": int(round(float(x["prezzo"] or 0), -2)), "anno": int(x["anno"] or 0)}
                              for x in rows]
    except Exception as e:
        log.warning("SEO statistiche %s non disponibili: %s", pv, str(e)[:120])
    _cache[pv] = (time.time(), out)
    return out


# ---------------------------------------------------------------- HTML
def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _eur(n: int) -> str:
    return f"{n:,}".replace(",", ".") + " €"


CSS_EXTRA = """
.page{max-width:760px}
.seo-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.seo-stats div{background:var(--card);border-radius:14px;padding:12px 14px}
.seo-stats b{display:block;font:800 24px/1.1 var(--disp)}
.seo-stats span{font-size:13px;color:var(--muted)}
.seo-tab{width:100%;border-collapse:collapse;font-size:15px;margin:8px 0}
.seo-tab th,.seo-tab td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line)}
.seo-tab td:last-child,.seo-tab th:last-child{text-align:right}
.seo-links{display:flex;flex-wrap:wrap;gap:8px;padding:0;list-style:none}
.seo-links a{display:inline-block;padding:7px 12px;border-radius:999px;background:var(--card);text-decoration:none;color:var(--ink);font-size:14px}
.seo-cta{display:inline-block;margin:10px 0;padding:14px 20px;border-radius:12px;background:var(--tag);color:#fff!important;font-weight:700;text-decoration:none}
.seo-faq details{background:var(--card);border-radius:12px;padding:12px 14px;margin:8px 0}
.seo-faq summary{font-weight:700;cursor:pointer}
"""


def _pagina(path: str, titolo: str, desc: str, corpo: str, jsonld: list[dict], breadcrumb: list[tuple[str, str]]) -> str:
    url = BASE + path
    bc = {"@context": "https://schema.org", "@type": "BreadcrumbList",
          "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": BASE + p}
                              for i, (n, p) in enumerate(breadcrumb)]}
    ld = "".join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False)}</script>' for x in [bc] + jsonld)
    verif = os.environ.get("GOOGLE_SITE_VERIFICATION")
    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(titolo)} · Scovo</title>
<meta name="description" content="{_e(desc)}">
<link rel="canonical" href="{_e(url)}">
<meta name="robots" content="index,follow,max-image-preview:large">
{f'<meta name="google-site-verification" content="{_e(verif)}">' if verif else ''}
<meta property="og:type" content="article">
<meta property="og:site_name" content="Scovo">
<meta property="og:locale" content="it_IT">
<meta property="og:title" content="{_e(titolo)}">
<meta property="og:description" content="{_e(desc)}">
<meta property="og:url" content="{_e(url)}">
<meta property="og:image" content="{BASE}/static/scovo/icon-512.png">
<meta name="twitter:card" content="summary">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="/static/scovo/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/static/scovo/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&family=Barlow+Semi+Condensed:wght@600;700;800&display=swap">
<link rel="stylesheet" href="/static/scovo/app.css">
<style>{CSS_EXTRA}</style>
{ld}
</head>
<body>
<main class="page">
<nav aria-label="Percorso" style="font-size:14px;margin-bottom:8px">{' › '.join(f'<a href="{_e(p)}">{_e(n)}</a>' for n, p in breadcrumb)}</nav>
{corpo}
{_link_interni(path)}
<nav class="foot" aria-label="Informazioni"><a href="/">Scovo</a><a href="/chi-siamo">Chi siamo</a><a href="/privacy">Privacy</a><a href="/condizioni">Condizioni d'uso</a></nav>
</main>
</body>
</html>"""


def _link_interni(corrente: str) -> str:
    citta = "".join(f'<li><a href="/auto-usate/{k}">Auto usate {v["nome"]}</a></li>' for k, v in CITTA.items()
                    if f"/auto-usate/{k}" != corrente)
    arg = "".join(f'<li><a href="/{k}">{_e(v["h1"])}</a></li>' for k, v in ARGOMENTI.items() if f"/{k}" != corrente)
    gui = "".join(f'<li><a href="/guide/{k}">{_e(v["titolo"])}</a></li>' for k, v in GUIDE.items()
                  if f"/guide/{k}" != corrente)
    return (f'<h2>Auto usate da privati nelle altre città</h2><ul class="seo-links">{citta}</ul>'
            f'<h2>Altre ricerche</h2><ul class="seo-links">{arg}</ul>'
            f'<h2>Guide</h2><ul class="seo-links">{gui}</ul>')


def _faq_html(faq: list[tuple[str, str]]) -> tuple[str, dict | None]:
    if not faq:
        return "", None
    h = '<h2>Domande frequenti</h2><div class="seo-faq">' + "".join(
        f"<details><summary>{_e(q)}</summary><p>{_e(a)}</p></details>" for q, a in faq) + "</div>"
    ld = {"@context": "https://schema.org", "@type": "FAQPage",
          "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]}
    return h, ld


CTA = '<p><a class="seo-cta" href="/#/entra">Guarda le auto su Scovo</a></p>'


def pagina_citta(slug: str) -> str | None:
    c = CITTA.get(slug)
    if not c:
        return None
    inp = ("nella " if c["prov"].startswith("Città") else "in ") + c["prov"]
    st = _stats(c["pv"])
    comuni = _comuni_vicini(c["pv"])
    titolo = f"Auto usate {c['nome']} da privati: occasioni sotto prezzo"
    desc = (f"Auto usate in vendita da privati a {c['nome']} e {inp}, sotto il prezzo di mercato: "
            f"guadagno, pezzi e rivendita già calcolati. Annunci da Subito, AutoScout24 e Facebook Marketplace.")
    stats = ""
    if st.get("annunci"):
        stats = (f'<div class="seo-stats"><div><b>{st["annunci"]:,}</b><span>annunci di privati attivi {_e(inp)}</span></div>'
                 .replace(",", ".") +
                 '<div><b>3</b><span>siti controllati: Subito, AutoScout24, Facebook</span></div>'
                 '<div><b>Ogni giorno</b><span>nuove auto, più volte al giorno</span></div></div>')
    tab = ""
    if st.get("modelli"):
        righe = "".join(f"<tr><td>{_e(m['nome'])}</td><td>{m['n']}</td><td>{m['anno'] or ''}</td><td>{_eur(m['prezzo'])}</td></tr>"
                        for m in st["modelli"])
        tab = (f"<h2>Le auto usate più in vendita a {_e(c['nome'])} oggi</h2>"
               f"<p>I modelli con più annunci di privati {_e(inp)}, con l'anno e il prezzo tipico (mediana) degli annunci attivi.</p>"
               f'<table class="seo-tab"><thead><tr><th>Modello</th><th>Annunci</th><th>Anno tipico</th><th>Prezzo tipico</th></tr></thead><tbody>{righe}</tbody></table>')
    com = ""
    if comuni:
        com = (f"<h2>Comuni coperti intorno a {_e(c['nome'])}</h2><p>Leggiamo gli annunci di {_e(c['nome'])} e di tutta la "
               f"{_e(c['prov'])}, tra cui {_e(', '.join(comuni))}.</p>")
    faq = [(f"Ci sono auto usate sotto prezzo a {c['nome']}?",
            f"Sì: ogni giorno alcune auto di privati a {c['nome']} e {inp} sono messe in vendita sotto il prezzo di mercato. Scovo le trova e ti mostra il guadagno stimato dopo le spese."),
           (f"Da dove arrivano gli annunci di {c['nome']}?",
            "Da annunci pubblici di venditori privati su Subito, AutoScout24 e Facebook Marketplace. Niente concessionari né aste."),
           ("Quanto costa Scovo?", "79,99 € al mese IVA inclusa, disdici quando vuoi.")]
    fh, fld = _faq_html(faq)
    corpo = (f"<h1>Auto usate a {_e(c['nome'])}: le occasioni dei privati</h1>"
             f"<p>{_e(c['nota'])}</p>"
             f"<p><b>Scovo</b> legge ogni giorno tutti gli annunci di auto usate dei privati a {_e(c['nome'])} e "
             f"{_e(inp)} e ti mostra solo quelle sotto prezzo: per ogni auto trovi il prezzo di mercato, i pezzi da "
             f"cambiare, la rivendita prudente e il guadagno, già tolti passaggio di proprietà e pulizia. È pensato per "
             f"commercianti di auto usate, autosaloni, carrozzieri e chi compra auto da privati per rivenderle.</p>"
             f"{stats}{CTA}{tab}"
             f"<h2>Come funziona a {_e(c['nome'])}</h2><ol>"
             f"<li><b>Leggiamo gli annunci</b> di Subito, AutoScout24 e Facebook Marketplace a {_e(c['nome'])} e provincia, solo privati.</li>"
             f"<li><b>Confrontiamo ogni auto</b> con quelle uguali in vendita in zona: marca, modello, anno, chilometri, alimentazione.</li>"
             f"<li><b>Togliamo quelle che non rendono</b>: se dopo pezzi, passaggio e pulizia il guadagno non c'è, l'auto non arriva a te.</li>"
             f"<li><b>Chiami per primo</b>: ogni auto si può contattare al massimo da 7 commercianti.</li></ol>"
             f"<h2>Auto incidentate ed economiche a {_e(c['nome'])}</h2>"
             f"<p>Oltre alle auto sane trovi una sezione di <a href=\"/auto-incidentate-milano\">auto incidentate</a> con i pezzi già stimati, "
             f"per carrozzieri, e una di <a href=\"/auto-economiche-sotto-1000-euro-milano\">auto sotto 1.000 €</a>.</p>"
             f"{com}{fh}{CTA}")
    jl = [x for x in [fld] if x]
    return _pagina(f"/auto-usate/{slug}", titolo, desc, corpo, jl,
                   [("Scovo", "/"), ("Auto usate", "/auto-usate/milano"), (c["nome"], f"/auto-usate/{slug}")])


def pagina_argomento(slug: str) -> str | None:
    a = ARGOMENTI.get(slug)
    if not a:
        return None
    sez = "".join(f"<h2>{_e(t)}</h2><p>{_e(p)}</p>" for t, p in a["testo"])
    fh, fld = _faq_html(a.get("faq") or [])
    corpo = f"<h1>{_e(a['h1'])}</h1><p>{_e(a['desc'])}</p>{CTA}{sez}{fh}{CTA}"
    return _pagina(f"/{slug}", a["titolo"], a["desc"], corpo, [x for x in [fld] if x],
                   [("Scovo", "/"), (a["h1"], f"/{slug}")])


def pagina_guida(slug: str) -> str | None:
    g = GUIDE.get(slug)
    if not g:
        return None
    sez = "".join(f"<h2>{_e(t)}</h2><p>{_e(p)}</p>" for t, p in g["testo"])
    art = {"@context": "https://schema.org", "@type": "Article", "headline": g["titolo"], "description": g["desc"],
           "inLanguage": "it-IT", "dateModified": OGGI, "author": {"@type": "Organization", "name": "Scovo"},
           "publisher": {"@type": "Organization", "name": "Scovo", "logo": {"@type": "ImageObject", "url": BASE + "/static/scovo/icon-512.png"}},
           "mainEntityOfPage": BASE + f"/guide/{slug}"}
    corpo = f"<h1>{_e(g['titolo'])}</h1><p>{_e(g['desc'])}</p>{sez}{CTA}"
    return _pagina(f"/guide/{slug}", g["titolo"], g["desc"], corpo, [art],
                   [("Scovo", "/"), ("Guide", "/guide/come-trovare-auto-sotto-prezzo"), (g["titolo"], f"/guide/{slug}")])


def tutti_gli_indirizzi() -> list[str]:
    return (["/", "/chi-siamo", "/privacy", "/condizioni"] + [f"/auto-usate/{k}" for k in CITTA]
            + [f"/{k}" for k in ARGOMENTI] + [f"/guide/{k}" for k in GUIDE])


def jsonld_home() -> str:
    """Dati strutturati della pagina iniziale: organizzazione, sito, servizio con prezzo."""
    org = {"@context": "https://schema.org", "@type": "Organization", "name": "Scovo", "url": BASE + "/",
           "logo": BASE + "/static/scovo/icon-512.png", "areaServed": ["Milano", "Lombardia"]}
    site = {"@context": "https://schema.org", "@type": "WebSite", "name": "Scovo", "url": BASE + "/", "inLanguage": "it-IT"}
    app = {"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "Scovo",
           "applicationCategory": "BusinessApplication", "operatingSystem": "Web, iOS, Android",
           "description": "Le auto usate dei privati sotto prezzo a Milano e in Lombardia, con guadagno, pezzi e rivendita già calcolati, per commercianti di auto usate.",
           "offers": {"@type": "Offer", "price": "79.99", "priceCurrency": "EUR"}}
    return "".join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False)}</script>' for x in (org, site, app))
