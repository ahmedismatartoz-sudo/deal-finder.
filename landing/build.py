"""Sito vetrina statico di Scovo per scovo.online (26/10, cliente: "un nuovo sito su Render, non toccare il progetto
attuale; dalla landing si entra nella dashboard sul link Render").

    python landing/build.py          → landing/public/ (pubblicato da Render come sito statico)

Prende la pagina iniziale e le pagine SEO dell'app e le scrive come file statici con il dominio nuovo. I pulsanti
"Entra", "Prova" e "Google" portano all'app (APP_URL, per ora https://scovo.onrender.com)."""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

DOMINIO = os.environ.get("LANDING_URL", "https://scovo.online").rstrip("/")
APP = os.environ.get("APP_URL", "https://scovo.onrender.com").rstrip("/")
os.environ["PUBLIC_URL"] = DOMINIO
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SITE = ROOT / "dealfinder" / "web" / "static" / "scovo"
OUT = ROOT / "landing" / "public"


def collega_app(html: str) -> str:
    """Link dell'app (login, prova, Google, dashboard) verso l'app su Render; il resto resta sul dominio nuovo."""
    html = html.replace('href="#/', f'href="{APP}/#/').replace('href="/#/', f'href="{APP}/#/').replace('href="/auth/', f'href="{APP}/auth/')
    html = html.replace('href="/gestione', f'href="{APP}/gestione')
    html = html.replace("https://scovo.onrender.com/static/", f"{DOMINIO}/static/")
    html = html.replace('content="https://scovo.onrender.com/"', f'content="{DOMINIO}/"')
    return html


def home() -> str:
    from dealfinder.web import seo
    h = (SITE / "index.html").read_text()
    h = re.sub(r'<script src="/static/scovo/app.js[^>]*></script>\n?', "", h)
    h = h.replace("?v=__V__", "").replace('<link rel="manifest" href="/manifest.webmanifest">\n', "")
    verif = os.environ.get("GOOGLE_SITE_VERIFICATION")
    testa = (f'<link rel="canonical" href="{DOMINIO}/">'
             + (f'<meta name="google-site-verification" content="{verif}">' if verif else "") + seo.jsonld_home())
    h = h.replace("<!--SEO-->", testa)
    h = h.replace('<div class="app" id="app" hidden></div>\n', "")
    h = h.replace('<noscript><p style="padding:16px">Per usare Scovo attiva JavaScript nel browser.</p></noscript>\n', "")
    return collega_app(h)


def scrivi(path: str, html: str) -> None:
    f = OUT / ("index.html" if path == "/" else path.strip("/") + "/index.html")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(html)


def main() -> None:
    from dealfinder.web import seo
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "static" / "scovo").mkdir(parents=True)
    for nome in ("app.css", "home.css", "favicon.svg", "icon-192.png", "icon-512.png", "apple-touch-icon.png",
                 "maskable-512.png"):
        shutil.copy(SITE / nome, OUT / "static" / "scovo" / nome)
    scrivi("/", home())
    for nome in ("chi-siamo", "privacy", "condizioni"):
        scrivi(f"/{nome}", collega_app((SITE / f"{nome}.html").read_text()))
    for k in seo.CITTA:
        h = seo.pagina_citta(k)
        if h:
            scrivi(f"/auto-usate/{k}", collega_app(h))
    for k in seo.ARGOMENTI:
        scrivi(f"/{k}", collega_app(seo.pagina_argomento(k)))
    for k in seo.GUIDE:
        h = seo.pagina_guida(k)
        if h:
            scrivi(f"/guide/{k}", collega_app(h))
    urls = "".join(f"<url><loc>{DOMINIO}{p}</loc><lastmod>{seo.OGGI}</lastmod></url>" for p in seo.tutti_gli_indirizzi())
    (OUT / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?><urlset '
                                     'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + "</urlset>")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {DOMINIO}/sitemap.xml\n")
    print("pagine:", sum(1 for _ in OUT.rglob("index.html")))


if __name__ == "__main__":
    main()
