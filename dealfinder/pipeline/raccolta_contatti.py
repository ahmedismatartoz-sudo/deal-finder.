"""25/11 (cliente): due raccoglitori di contatti di commercianti d'auto dai loro SITI, solo entro 50 km da Milano
(raccolta60.RAGGIO_KM: un sito senza comune riconosciuto entro il raggio non si legge, esito "fuori_zona").

  A) EMAIL aziendali   -> log RACCOLTA_EMAIL (solo conteggi)
  B) CELLULARI (3xx) pubblicati dal salone, per WhatsApp -> log RACCOLTA_CELL (solo conteggi)

Che cosa c'era già (e resta com'è): lead_email (email dalle pagine AutoScout24 e dai siti, senza robots.txt e senza
numeri), raccolta_numeri (numeri da vetrine Subito e Pagine Gialle, entro 60 km, una tantum), lead_maps (Google Maps:
telefono e sito), lead_tutti_dealer / commercianti_as24 (cellulari dagli annunci di commercianti). Mancava: leggere i
SITI dei saloni per i cellulari, i siti trovati da Pagine Gialle/vetrine Subito per le email, e un giro periodico.

Da dove partono i siti (solo dati già nel database, nessuna nuova ricerca):
  - lead_maps.sito (Google Maps);
  - lead_email.sito dei commercianti AutoScout24/Subito di lead_tutti_dealer (nome e comune dagli annunci);
  - il campo "sito" delle righe RNUM / RNUM_GREZZE / R60 / R60T di lead_consegne: sono le schede degli elenchi
    pubblici già raccolti da raccolta_numeri (Pagine Gialle "automobili commercio" comune per comune, vetrine Subito
    Impresa+) e da raccolta60 (elenchi "concessionari a <comune>" di AutoScout24). Per avere più siti si lanciano
    prima quelle (raccolta_numeri:<etichetta>, raccolta60:<etichetta>) e poi questa.
    NON ci sono Autosupermarket.it e Automobile.it: i loro robots.txt vietano le schede dei concessionari
    (/concessionario/, */catalogo-dealer*), che sono le pagine con il sito del salone.
A richiesta (terzo campo della voce, "pg<N>", spento se manca) fino a N commercianti senza sito e senza cellulare si
cercano per nome e comune su Pagine Gialle (stesso sblocco e stesso parser di raccolta_numeri).
Di ogni sito si leggono la home e al massimo RACC_PAGINE-1 pagine "contatti" / "chi siamo" (link interni della home).

Cortesia: user agent dichiarato (RACC_UA), robots.txt rispettato (anche Crawl-delay), al massimo una richiesta ogni
RACC_PAUSA secondi per dominio, un tempo massimo per richiesta (RACC_TIMEOUT) e pagine tagliate a RACC_BYTE.
Niente Facebook, niente annunci, niente privati: solo il sito dell'attività.

Che cosa si tiene:
  - email: solo del dominio del sito o di una casella pubblica (gmail, libero...) scritta sul sito; mai PEC, noreply,
    privacy@/dpo@/webmaster@ e simili, mai indirizzi di fornitori di siti, mai gli esclusi (Loconte, Officine Mulino,
    Cosval, Auto e Auto Varese, SR Auto, GT Automobili, Privacar, Vroom), mai i disiscritti;
  - cellulari: solo 3xx italiani validi scritti dal salone (link tel:, link WhatsApp, dati strutturati, testo della
    pagina); una pagina con troppi numeri diversi (elenco, non contatti) dà solo quelli dei link; un numero che
    compare su RACC_TEL_COMUNE (4) o più siti non è di un salone e non si consegna.
Dove finiscono: tabella lead_contatti (una riga per dominio: email, cell e quelli NUOVI, cioè non già in lead_email,
brevo_inviati, lead_consegne, lead_tutti_dealer, lead_tutti, lead_maps, commercianti_as24 né su un altro sito) e, a
fine giro, righe RACC_EMAIL / RACC_CELL in lead_consegne (/api/admin/lead.csv?tipo=RACC_EMAIL). Con l'interruttore
raccolta_contatti_in_lead_email (spento) le email nuove vanno anche nelle righe VUOTE di lead_email dello stesso
commerciante (da lì le prendono gli invii automatici: per questo è spento).

Come si lancia:
  - una tantum: BREVO_IMPORT_LISTE=raccolta_contatti:<etichetta>[:pg<N>] (voce lunga della coda del cron notturno;
    a tempo finito, RACC_ORE, si rimanda e riprende dai siti non ancora letti);
  - giro periodico: interruttore degli automatismi raccolta_contatti (SPENTO di default), una volta al giorno dal cron
    opportunità, raccolta_contatti_minuti (15) e raccolta_contatti_siti (150) regolabili.
Memoria: i siti si prendono a gruppi di RACC_GRUPPO (40) dal database, mai tutta una tabella in memoria.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from html import unescape
from urllib.parse import urljoin, urlparse

log = logging.getLogger("lead")

TIPO_EMAIL = "RACC_EMAIL"
TIPO_CELL = "RACC_CELL"
UA = "ScovoContatti/1.0 (+https://scovo.it; contatti aziendali pubblici di autosaloni)"
# province con almeno un comune entro 50 km da Milano: primo filtro nel database; quello vero è per comune (nel_raggio)
PROVINCE = ("MI", "MB", "BG", "BS", "CO", "CR", "LC", "LO", "PV", "VA", "NO", "PC", "AL", "VC")
TIPI_SITO = ("RNUM", "RNUM_GREZZE", "R60", "R60T")
PAGINE_FISSE = ("contatti", "chi-siamo")
LINK_UTILI = re.compile(r"contatt|contact|chi[-_ ]?siamo|about|azienda|dove[-_ ]?siamo", re.I)
NO_ESTENSIONI = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|mp4|ics|vcf)$", re.I)
# caselle che non sono "il salone": automatiche, legali, tecniche
NO_LOCALI = re.compile(r"^(noreply|no-reply|no_reply|donotreply|do-not-reply|mailer-daemon|postmaster|hostmaster|abuse|"
                       r"privacy|dpo|rpd|gdpr|legal|legale|webmaster|bounces?|unsubscribe|cookie|pec|postacert\w*|"
                       r"postacertificata|fatture|fatturazione|sdi)([^a-z]|$)", re.I)
PEC = re.compile(r"(^|[.@-])pec([.-]|$)|legalmail|postacert|arubapec|cert\.|pecimprese|registerpec|sicurezzapostale|"
                 r"mypec|gigapec|cgn\.|lamiapec|pec\.it$", re.I)
WA = re.compile(r"(?:wa\.me/|whatsapp\.com/send/?\?(?:[^\"'\s>]*&(?:amp;)?)?phone=)\+?(\d{9,15})", re.I)
TEL_HREF = re.compile(r'href=["\']tel:([^"\']{6,25})["\']', re.I)
DOM_SQL = r"lower(regexp_replace(regexp_replace({c}, '^https?://(www\.)?', '', 'i'), '[/:?#].*$', ''))"

SQL = """CREATE TABLE IF NOT EXISTS lead_contatti (
    dominio     TEXT PRIMARY KEY,           -- dominio del sito del salone (o "pg:<impronta>" senza sito)
    sito        TEXT,
    nome        TEXT, comune TEXT, prov TEXT,
    origine     TEXT,                       -- maps | autoscout | subito | elenchi | pg
    rif         TEXT,                       -- chiave di lead_email dello stesso commerciante, se c'è
    email       TEXT[] NOT NULL DEFAULT '{}',
    cell        TEXT[] NOT NULL DEFAULT '{}',
    email_nuove TEXT[] NOT NULL DEFAULT '{}',
    cell_nuovi  TEXT[] NOT NULL DEFAULT '{}',
    esito       TEXT,
    pagine      INTEGER NOT NULL DEFAULT 0,
    tentativi   INTEGER NOT NULL DEFAULT 0,
    consegnato  BOOLEAN NOT NULL DEFAULT false,
    tentato     TIMESTAMPTZ,
    creato      TIMESTAMPTZ NOT NULL DEFAULT now()
)"""
SQL_INDICE = "CREATE INDEX IF NOT EXISTS lead_contatti_tentato_idx ON lead_contatti (tentato NULLS FIRST)"


def _env_f(nome: str, base: float) -> float:
    try:
        return float(os.environ.get(nome) or base)
    except ValueError:
        return base


# --- funzioni pure -------------------------------------------------------------------------------------------------
def dominio(url) -> str:
    """Dominio senza www di un indirizzo http(s); "" se non è un sito leggibile (indirizzi IP, localhost, porte)."""
    try:
        p = urlparse(str(url or "").strip())
    except ValueError:
        return ""
    h = (p.hostname or "").lower().removeprefix("www.")
    if p.scheme not in ("http", "https") or "." not in h or not re.fullmatch(r"[a-z0-9.-]{4,80}", h):
        return ""
    try:
        porta = p.port
    except ValueError:
        return ""
    if porta not in (None, 80, 443) or re.fullmatch(r"[\d.]+", h) or h.endswith((".local", ".internal", ".lan")):
        return ""
    return h


def sito_ammesso(dom: str) -> bool:
    """Un sito del salone, non una piattaforma (Facebook, Subito, AutoScout24, Pagine Gialle, Google...)."""
    from .lead_email import NO_SITI
    from .raccolta_numeri import NO_SITO
    return bool(dom) and not NO_SITO.search(dom) and not NO_SITI.search(dom)


def escluso(*testi) -> bool:
    """Commercianti da non raccogliere mai, per nome, dominio o indirizzo."""
    from ..marketing.brevo_liste import _LOCONTE_TESTO, _esclusi
    from .raccolta_numeri import _re_esclusi
    for t in testi:
        t = str(t or "").lower()
        if t and (_esclusi(t) or _LOCONTE_TESTO.search(t) or _re_esclusi().search(t)):
            return True
    return False


def _senza_script(html: str) -> str:
    return re.sub(r"<(script|style|noscript|svg)\b[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)


def _stessa_azienda(dom_email: str, dom_sito: str) -> bool:
    a, b = dom_email.removeprefix("www."), dom_sito.removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def email_aziendali(html: str, dom_sito: str) -> tuple[list[str], Counter]:
    """Email del salone in una pagina del suo sito e i conteggi degli scarti. Funzione pura."""
    from ..marketing.brevo_liste import _DOMINI_PUBBLICI, _EMAIL_OK, _NON_EMAIL, spazzatura
    from .lead_email import EMAIL, _cf
    n: Counter = Counter()
    t = html or ""
    t = t + " " + " ".join(_cf(h) for h in re.findall(r'(?:data-cfemail="|email-protection#)([0-9a-fA-F]{6,})', t))
    t = unescape(t).replace("[at]", "@").replace("(at)", "@").replace(" @ ", "@").replace("%40", "@")
    proprie, pubbliche = [], []
    for e in EMAIL.findall(t):
        e = e.strip(".").lower()
        e = re.sub(r"^(?:u003e|%20|20)+", "", e)
        loc, _, dom = e.rpartition("@")
        if not loc or e in proprie or e in pubbliche:
            continue
        if not _EMAIL_OK.match(e) or _NON_EMAIL.search(e) or len(e) > 60:
            n["non_valide"] += 1
        elif PEC.search(dom) or PEC.search(loc + "@"):
            n["pec"] += 1
        elif NO_LOCALI.search(loc) or spazzatura(e):
            n["automatiche"] += 1
        elif escluso(e):
            n["esclusi"] += 1
        elif _stessa_azienda(dom, dom_sito):
            proprie.append(e)
        elif _DOMINI_PUBBLICI.match(dom):
            pubbliche.append(e)
        else:
            n["di_terzi"] += 1              # agenzia web, fornitore del sito, un'altra azienda
    return (proprie + pubbliche)[:3], n


def _cell(grezzi) -> list[str]:
    """Cellulari italiani validi (10 cifre, prefisso di un operatore), normalizzati senza +39."""
    from .lead_comuni import cell_validi
    out = []
    for g in grezzi:
        d = re.sub(r"\D", "", str(g or ""))
        if d.startswith("0039"):
            d = d[4:]
        elif d.startswith("39") and len(d) >= 11:
            d = d[2:]
        if d.startswith("3"):
            out += [c for c in cell_validi([d]) if c not in out]
    return out


def cellulari_salone(html: str) -> tuple[list[str], Counter]:
    """Cellulari che il salone pubblica nella pagina: prima i link (WhatsApp, tel:), poi i dati strutturati e il
    testo. Una pagina con più di RACC_CELL_PAGINA numeri diversi nel testo è un elenco: valgono solo i link."""
    from .raccolta_numeri import _json_ld, _testo, telefoni
    n: Counter = Counter()
    h = html or ""
    link = _cell(WA.findall(unescape(h)) + [unescape(x) for x in TEL_HREF.findall(h)])
    strutt: list = []
    for j in _json_ld(h):
        strutt += re.findall(r'"telephone"\s*:\s*"([^"]{6,25})"', json.dumps(j))
    testo = telefoni(_testo(_senza_script(h)))[0]
    if len(set(testo)) > int(_env_f("RACC_CELL_PAGINA", 6)):
        n["pagine_elenco"] += 1
        testo = []
    out = []
    for c in link + _cell(strutt) + _cell(testo):
        if c not in out:
            out.append(c)
    return out[:4], n


def pagine_utili(html: str, base: str, dom: str, quante: int) -> list[str]:
    """Link interni della home che sembrano "contatti" / "chi siamo"; se non ce ne sono, gli indirizzi consueti."""
    out = []
    for h, testo in re.findall(r'<a\b[^>]*?href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', _senza_script(html), re.S | re.I):
        if h.lower().startswith(("mailto:", "tel:", "javascript:", "whatsapp:")):
            continue
        u = urljoin(base, unescape(h.strip())).split("#")[0]
        if dominio(u) != dom or NO_ESTENSIONI.search(urlparse(u).path):
            continue
        if not (LINK_UTILI.search(urlparse(u).path) or LINK_UTILI.search(re.sub(r"<[^>]+>", " ", testo)[:60])):
            continue
        if u not in out and u.rstrip("/") != base.rstrip("/"):
            out.append(u)
    if not out:
        out = [urljoin(base, "/" + p) for p in PAGINE_FISSE]
    out.sort(key=lambda u: 0 if re.search(r"contatt|contact", u, re.I) else 1)
    return out[:max(0, quante)]


class Robots:
    """robots.txt di un dominio per il nostro user agent. Senza file (404) tutto permesso; file non leggibile
    (errore del server o di rete) = niente letture, come da consuetudine."""

    def __init__(self, status: int, testo: str, ua: str):
        from urllib.robotparser import RobotFileParser
        self.ua = ua.split("/")[0]
        self.rp = None
        self.chiuso = False
        if status == 200:
            self.rp = RobotFileParser()
            self.rp.parse((testo or "").splitlines())
        elif status in (401, 403) or status >= 500 or status == 0:
            self.chiuso = True

    def permesso(self, url: str) -> bool:
        if self.chiuso:
            return False
        if self.rp is None:
            return True
        try:
            return bool(self.rp.can_fetch(self.ua, url))
        except Exception:
            return False

    def attesa(self) -> float:
        try:
            d = self.rp.crawl_delay(self.ua) if self.rp else None
            return float(d) if d else 0.0
        except Exception:
            return 0.0


# --- rete ---------------------------------------------------------------------------------------------------------
class Rete:
    """Una richiesta GET senza seguire i rinvii: (stato, testo, rinvio). Tempo massimo per richiesta e pagina
    tagliata a RACC_BYTE: una pagina enorme o un server lento non pesano su memoria e tempi."""

    def __init__(self):
        import httpx
        self.ua = (os.environ.get("RACC_UA") or UA).strip()
        self.timeout = _env_f("RACC_TIMEOUT", 12)
        self.byte = int(_env_f("RACC_BYTE", 600_000))
        self.client = httpx.Client(headers={"User-Agent": self.ua, "Accept": "text/html,text/plain;q=0.9",
                                            "Accept-Language": "it"},
                                   timeout=httpx.Timeout(self.timeout, connect=min(8.0, self.timeout)),
                                   follow_redirects=False, limits=httpx.Limits(max_connections=12))

    def get(self, url: str) -> tuple[int, str, str | None]:
        fine = time.time() + self.timeout
        with self.client.stream("GET", url) as r:
            if r.status_code in (301, 302, 303, 307, 308):
                return r.status_code, "", r.headers.get("location")
            tipo = (r.headers.get("content-type") or "").lower()
            if r.status_code != 200 or not ("html" in tipo or "text/plain" in tipo):
                return r.status_code, "", None
            pezzi, n = [], 0
            for b in r.iter_bytes(32_768):
                pezzi.append(b)
                n += len(b)
                if n >= self.byte or time.time() > fine:
                    break
            return 200, b"".join(pezzi)[:self.byte].decode(r.encoding or "utf-8", "replace"), None

    def chiudi(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass


# --- archivio -----------------------------------------------------------------------------------------------------
class Archivio:
    """lead_contatti e i confronti con quello che c'è già. Ogni lettura è limitata (LIMIT) o fatta dal database:
    nessuna tabella viene caricata in memoria."""

    def _c(self):
        from ..db import connect
        return connect()

    def prepara(self) -> None:
        with self._c() as c:
            c.execute(SQL)
            c.execute(SQL_INDICE)
            c.commit()

    def semina(self) -> dict:
        """I siti già nel database diventano righe da leggere (una per dominio), tutto dentro il database."""
        n = {}
        prov = list(PROVINCE)
        fonti = (
            ("maps", "INSERT INTO lead_contatti (dominio, sito, nome, comune, prov, origine, rif) "
                     "SELECT DISTINCT ON (dom) dom, sito, nome, comune, upper(prov), 'maps', 'maps:' || place_id FROM ("
                     "SELECT " + DOM_SQL.format(c="sito") + " AS dom, sito, nome, comune, prov, place_id FROM lead_maps "
                     "WHERE sito ~* '^https?://' AND (prov IS NULL OR upper(prov) = ANY(%s::text[]))) s "
                     "WHERE dom <> '' ORDER BY dom ON CONFLICT (dominio) DO NOTHING", (prov,)),
            ("commercianti", "INSERT INTO lead_contatti (dominio, sito, nome, comune, prov, origine, rif) "
                             "SELECT DISTINCT ON (dom) dom, sito, nome, comune, upper(prov), origine, chiave FROM ("
                             "SELECT " + DOM_SQL.format(c="e.sito") + " AS dom, e.sito, d.nome, d.comune, d.prov, e.chiave, "
                             "CASE WHEN e.chiave ~ '^subito' THEN 'subito' ELSE 'autoscout' END AS origine "
                             "FROM lead_email e JOIN lead_tutti_dealer d ON d.chiave = e.chiave "
                             "WHERE e.sito ~* '^https?://' AND (d.prov IS NULL OR upper(d.prov) = ANY(%s::text[]))) s "
                             "WHERE dom <> '' ORDER BY dom ON CONFLICT (dominio) DO NOTHING", (prov,)),
            ("elenchi", "INSERT INTO lead_contatti (dominio, sito, nome, comune, prov, origine) "
                        "SELECT DISTINCT ON (dom) dom, sito, nome, comune, upper(prov), 'elenchi' FROM ("
                        "SELECT " + DOM_SQL.format(c="dati->>'sito'") + " AS dom, dati->>'sito' AS sito, "
                        "dati->>'nome' AS nome, dati->>'comune' AS comune, dati->>'prov' AS prov FROM lead_consegne "
                        "WHERE tipo = ANY(%s::text[]) AND dati->>'sito' ~* '^https?://') s "
                        "WHERE dom <> '' AND (prov IS NULL OR upper(prov) = ANY(%s::text[])) "
                        "ORDER BY dom ON CONFLICT (dominio) DO NOTHING", (list(TIPI_SITO), prov)),
        )
        for nome, sql, par in fonti:
            try:
                with self._c() as c:
                    n[nome] = c.execute(sql, par).rowcount
                    c.commit()
            except Exception as e:                # una tabella che manca non ferma le altre
                n[nome + "_errore"] = type(e).__name__
        return n

    def prendi(self, quanti: int, rifai_giorni: float) -> list[dict]:
        """I prossimi siti: mai letti, oppure andati in errore (al massimo 3 volte) e non più letti da
        `rifai_giorni` giorni. Segnati subito (tentato = now()): un altro processo non li riprende."""
        with self._c() as c:
            r = c.execute(
                "UPDATE lead_contatti SET tentato = now(), tentativi = tentativi + 1 WHERE dominio IN ("
                "SELECT dominio FROM lead_contatti WHERE dominio !~ '^pg:' AND (tentato IS NULL OR "
                "(esito = 'errore' AND tentativi < 3 AND tentato < now() - make_interval(days => %s))) "
                "ORDER BY tentato NULLS FIRST, dominio LIMIT %s FOR UPDATE SKIP LOCKED) "
                "RETURNING dominio, sito, nome, comune, prov, origine, rif", (int(rifai_giorni), int(quanti))).fetchall()
            c.commit()
        return sorted(r, key=lambda x: x["dominio"])

    def da_leggere(self) -> int:
        with self._c() as c:
            return int(c.execute("SELECT count(*) AS n FROM lead_contatti WHERE tentato IS NULL "
                                 "AND dominio !~ '^pg:'").fetchone()["n"])

    def non_contattabili(self, emails: list[str]) -> set[str]:
        """Tra queste email, quelle di chi si è disiscritto o è bloccato (confronto per impronta)."""
        from ..marketing.invii import impronta
        if not emails:
            return set()
        imp = {impronta(e): e for e in emails}
        with self._c() as c:
            r = c.execute("SELECT email_hash FROM disiscritti WHERE email_hash = ANY(%s::text[])", (list(imp),)).fetchall()
        return {imp[x["email_hash"]] for x in r}

    def email_note(self, emails: list[str], dom: str) -> set[str]:
        """Tra queste email, quelle che abbiamo già (lead_email, brevo_inviati, lead_consegne, un altro sito)."""
        if not emails:
            return set()
        note: set = set()
        for sql, par in (
            ("SELECT x AS v FROM unnest(%s::text[]) x WHERE EXISTS (SELECT 1 FROM brevo_inviati b WHERE lower(b.email) = x)",
             (emails,)),
            ("SELECT x AS v FROM unnest(%s::text[]) x WHERE EXISTS (SELECT 1 FROM lead_email l WHERE x = ANY(l.email))",
             (emails,)),
            ("SELECT x AS v FROM unnest(%s::text[]) x WHERE EXISTS (SELECT 1 FROM lead_contatti k WHERE k.dominio <> %s "
             "AND x = ANY(k.email))", (emails, dom)),
            ("SELECT x AS v FROM unnest(%s::text[]) x WHERE EXISTS (SELECT 1 FROM lead_consegne k WHERE k.tipo <> %s "
             "AND k.dati ? 'email' AND position(x in lower(k.dati->>'email')) > 0)", (emails, TIPO_EMAIL)),
        ):
            note |= self._valori(sql, par)
        return note

    def cell_noti(self, cell: list[str], dom: str) -> set[str]:
        """Tra questi cellulari (10 cifre), quelli che abbiamo già in una delle raccolte."""
        if not cell:
            return set()
        cifre = "regexp_replace({}, '[^0-9]', '', 'g')"
        campi = " || ' ' || ".join(f"coalesce(k.dati->>'{f}', '')" for f in
                                   ("cellulare", "altri_cellulari", "cellulari", "cell", "tel", "telefono"))
        noti: set = set()
        for sql, par in (
            ("SELECT x AS v FROM unnest(%s::text[]) x WHERE EXISTS (SELECT 1 FROM lead_contatti k WHERE k.dominio <> %s "
             "AND x = ANY(k.cell))", (cell, dom)),
            ("SELECT x AS v FROM unnest(%s::text[]) x WHERE EXISTS (SELECT 1 FROM lead_tutti_dealer d WHERE "
             "position(x in " + cifre.format("array_to_string(d.cell, ' ')") + ") > 0)", (cell,)),
            ("SELECT x AS v FROM unnest(%s::text[]) x WHERE EXISTS (SELECT 1 FROM lead_tutti t WHERE "
             "position(x in " + cifre.format("coalesce(t.tel, '') || coalesce(t.altri_tel, '')") + ") > 0)", (cell,)),
            ("SELECT x AS v FROM unnest(%s::text[]) x WHERE EXISTS (SELECT 1 FROM lead_maps m WHERE "
             "position(x in " + cifre.format("coalesce(m.tel, '')") + ") > 0)", (cell,)),
            ("SELECT x AS v FROM unnest(%s::text[]) x WHERE EXISTS (SELECT 1 FROM commercianti_as24 a WHERE "
             "position(x in " + cifre.format("array_to_string(a.cellulari, ' ')") + ") > 0)", (cell,)),
            ("SELECT x AS v FROM unnest(%s::text[]) x WHERE EXISTS (SELECT 1 FROM lead_consegne k WHERE k.tipo <> %s "
             "AND position(x in " + cifre.format(campi) + ") > 0)", (cell, TIPO_CELL)),
        ):
            noti |= self._valori(sql, par)
        return noti

    def _valori(self, sql: str, par) -> set[str]:
        try:
            with self._c() as c:
                return {r["v"] for r in c.execute(sql, par).fetchall()}
        except Exception:                         # tabella che manca in questo database: nessun confronto da lì
            return set()

    def salva(self, dom: str, d: dict) -> None:
        with self._c() as c:
            c.execute("UPDATE lead_contatti SET email = %s::text[], cell = %s::text[], email_nuove = %s::text[], "
                      "cell_nuovi = %s::text[], esito = %s, pagine = %s, tentato = now(), "
                      "consegnato = consegnato AND NOT %s WHERE dominio = %s",
                      (d.get("email") or [], d.get("cell") or [], d.get("email_nuove") or [], d.get("cell_nuovi") or [],
                       d.get("esito"), int(d.get("pagine") or 0),
                       bool(d.get("email_nuove") or d.get("cell_nuovi")), dom))
            c.commit()

    def in_lead_email(self, rif: str, emails: list[str], sito: str) -> bool:
        """Solo con l'interruttore: le email nuove nella riga VUOTA di lead_email dello stesso commerciante."""
        with self._c() as c:
            n = c.execute("UPDATE lead_email SET email = %s::text[], sito = coalesce(sito, %s), fonte = 'raccolta_contatti', "
                          "tentato = now() WHERE chiave = %s AND cardinality(email) = 0", (emails, sito, rif)).rowcount
            c.commit()
        return bool(n)

    def da_consegnare(self, quanti: int) -> list[dict]:
        with self._c() as c:
            return list(c.execute(
                "SELECT dominio, sito, nome, comune, prov, origine, email_nuove, cell_nuovi FROM lead_contatti "
                "WHERE NOT consegnato AND (cardinality(email_nuove) > 0 OR cardinality(cell_nuovi) > 0) "
                "ORDER BY dominio LIMIT %s", (int(quanti),)).fetchall())

    def cell_su_tanti_siti(self, cell: list[str], soglia: int) -> set[str]:
        if not cell:
            return set()
        return self._valori("SELECT x AS v FROM unnest(%s::text[]) x WHERE (SELECT count(*) FROM lead_contatti k "
                            "WHERE x = ANY(k.cell)) >= %s", (cell, int(soglia)))

    def consegna(self, dom: str, righe: list[tuple]) -> None:
        """Le righe di consegna e il segno "consegnato" nella stessa transazione: un giro ripreso non le raddoppia."""
        with self._c() as c:
            for tipo, dati in righe:
                c.execute("INSERT INTO lead_consegne (tipo, dati) VALUES (%s, %s)",
                          (tipo, json.dumps(dati, ensure_ascii=False, default=str)))
            c.execute("UPDATE lead_contatti SET consegnato = true WHERE dominio = %s", (dom,))
            c.commit()

    # Pagine Gialle per nome (solo a richiesta)
    def senza_sito(self, quanti: int) -> list[dict]:
        with self._c() as c:
            return list(c.execute(
                "SELECT d.chiave, d.nome, d.comune, upper(d.prov) AS prov FROM lead_tutti_dealer d "
                "LEFT JOIN lead_email e ON e.chiave = d.chiave WHERE coalesce(e.sito, '') = '' AND cardinality(d.cell) = 0 "
                "AND coalesce(d.nome, '') <> '' AND coalesce(d.comune, '') <> '' AND upper(d.prov) = ANY(%s::text[]) "
                "AND NOT EXISTS (SELECT 1 FROM lead_contatti k WHERE k.rif = d.chiave) ORDER BY d.chiave LIMIT %s",
                (list(PROVINCE), int(quanti))).fetchall())

    def aggiungi(self, dom: str, d: dict) -> bool:
        with self._c() as c:
            n = c.execute("INSERT INTO lead_contatti (dominio, sito, nome, comune, prov, origine, rif, cell, cell_nuovi, "
                          "esito, tentato) VALUES (%s,%s,%s,%s,%s,'pg',%s,%s::text[],%s::text[],%s,%s) "
                          "ON CONFLICT (dominio) DO NOTHING",
                          (dom, d.get("sito"), d.get("nome"), d.get("comune"), d.get("prov"), d.get("rif"),
                           d.get("cell") or [], d.get("cell_nuovi") or [], d.get("esito"), d.get("tentato"))).rowcount
            c.commit()
        return bool(n)


# --- il lavoro ----------------------------------------------------------------------------------------------------
class Raccolta:
    def __init__(self, archivio, rete, scadenza: float, max_siti: int):
        self.arch, self.rete, self.scadenza, self.max_siti = archivio, rete, scadenza, max_siti
        self.ua = getattr(rete, "ua", None) or UA
        self.pausa = max(0.2, _env_f("RACC_PAUSA", 1.5))           # secondi tra due richieste allo stesso dominio
        self.pagine_max = max(1, int(_env_f("RACC_PAGINE", 4)))
        self.lock = threading.Lock()
        self.ultima: dict = {}
        self.robots: dict = {}
        self.st: Counter = Counter()
        self.http: Counter = Counter()

    def tempo(self) -> bool:
        return time.time() < self.scadenza

    def _aspetta(self, dom: str, extra: float = 0.0) -> None:
        """Al massimo una richiesta ogni `pausa` secondi (o Crawl-delay, fino a 20) per dominio."""
        attesa = min(max(self.pausa, extra), 20.0)
        with self.lock:
            ora = time.time()
            quando = max(ora, self.ultima.get(dom, 0.0) + attesa)
            self.ultima[dom] = quando
            if len(self.ultima) > 5000:           # la memoria non cresce con il numero di siti
                for k in [k for k, v in self.ultima.items() if v < ora - 60]:
                    del self.ultima[k]
        if quando > ora:
            time.sleep(quando - ora)

    def _get(self, url: str, dom: str, extra: float = 0.0) -> tuple[int, str, str | None]:
        self._aspetta(dom, extra)
        with self.lock:
            self.st["richieste"] += 1
        try:
            code, testo, rinvio = self.rete.get(url)
        except Exception:
            code, testo, rinvio = 0, "", None
        with self.lock:
            self.http[code] += 1
        return code, testo, rinvio

    def _robots(self, url: str) -> Robots:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        with self.lock:
            r = self.robots.get(base)
        if r is None:
            dom = dominio(url)
            code, testo, rinvio = self._get(base + "/robots.txt", dom)
            if code in (301, 302, 303, 307, 308) and rinvio and dominio(urljoin(base, rinvio)).endswith(dom):
                code, testo, _ = self._get(urljoin(base, rinvio), dom)     # http -> https, senza www -> www
            r = Robots(code, testo, self.ua)
            with self.lock:
                if len(self.robots) > 2000:
                    self.robots.clear()
                self.robots[base] = r
        return r

    def pagina(self, url: str) -> tuple[str, str, str]:
        """(html, indirizzo finale, esito) seguendo fino a 3 rinvii; ogni passo rispetta robots.txt e la pausa."""
        for _ in range(4):
            dom = dominio(url)
            if not sito_ammesso(dom) or escluso(dom):
                return "", url, "non_sito"
            rb = self._robots(url)
            if not rb.permesso(url):
                return "", url, "robots"
            code, testo, rinvio = self._get(url, dom, rb.attesa())
            if code == 200:
                return testo, url, "ok"
            if code in (301, 302, 303, 307, 308) and rinvio:
                url = urljoin(url, rinvio)
                continue
            return "", url, "errore" if code in (0, 429) or code >= 500 else "assente"
        return "", url, "errore"

    def sito(self, r: dict) -> dict:
        """Un sito: home e pagine contatti / chi siamo. Ritorna la riga da salvare."""
        dom = r["dominio"]
        out = {"email": [], "cell": [], "email_nuove": [], "cell_nuovi": [], "pagine": 0, "esito": "vuoto"}
        if not sito_ammesso(dom) or dominio(r.get("sito")) != dom:
            return {**out, "esito": "non_sito"}
        if escluso(r.get("nome"), dom, r.get("sito")):
            return {**out, "esito": "escluso"}
        from .raccolta60 import nel_raggio
        from .raccolta_numeri import vende_auto
        if r.get("nome") and not vende_auto(r["nome"]):
            return {**out, "esito": "non_auto"}
        if not nel_raggio(r.get("comune"), r.get("prov")):      # comune non riconosciuto entro 50 km da Milano: scartato
            return {**out, "esito": "fuori_zona"}
        html, base, esito = self.pagina(r["sito"] if urlparse(r["sito"]).path else r["sito"] + "/")
        if esito != "ok":
            return {**out, "esito": esito}
        dom_vero = dominio(base) or dom
        da_fare, scarti = [None], Counter()
        while da_fare and out["pagine"] < self.pagine_max and self.tempo():
            u = da_fare.pop(0)
            if u is not None:
                html, _, es = self.pagina(u)
                if es != "ok":
                    continue
            out["pagine"] += 1
            if escluso(re.sub(r"<[^>]+>", " ", (re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [0, ""])[1])):
                return {**out, "email": [], "cell": [], "esito": "escluso"}
            em, n1 = email_aziendali(html, dom_vero)
            ce, n2 = cellulari_salone(html)
            scarti += n1 + n2
            out["email"] += [e for e in em if e not in out["email"]]
            out["cell"] += [c for c in ce if c not in out["cell"]]
            if u is None:
                da_fare = pagine_utili(html, base, dom_vero, self.pagine_max - 1)
            html = ""
            if out["email"] and out["cell"]:      # trovati tutti e due: niente altre richieste a questo sito
                break
        out["email"], out["cell"] = out["email"][:3], out["cell"][:4]
        with self.lock:
            self.st.update({f"scarto_{k}": v for k, v in scarti.items()})
        if out["email"]:
            fuori = self.arch.non_contattabili(out["email"])
            if fuori:
                with self.lock:
                    self.st["scarto_disiscritti"] += len(fuori)
                out["email"] = [e for e in out["email"] if e not in fuori]
        if out["email"]:
            note = self.arch.email_note(out["email"], dom)
            out["email_nuove"] = [e for e in out["email"] if e not in note]
        if out["cell"]:
            noti = self.arch.cell_noti(out["cell"], dom)
            out["cell_nuovi"] = [c for c in out["cell"] if c not in noti]
        out["esito"] = "ok" if (out["email"] or out["cell"]) else "vuoto"
        return out

    def uno(self, r: dict) -> None:
        try:
            d = self.sito(r)
        except Exception as e:
            d = {"esito": "errore"}
            log.warning("RACCOLTA sito non letto: %s", type(e).__name__)
        try:
            self.arch.salva(r["dominio"], d)
            if d.get("email_nuove") and r.get("rif") and _in_lead_email():
                with self.lock:
                    self.st["in_lead_email"] += bool(self.arch.in_lead_email(r["rif"], d["email_nuove"], r.get("sito")))
        except Exception as e:
            d = {"esito": "non_salvato"}
            log.warning("RACCOLTA riga non salvata: %s", type(e).__name__)
        with self.lock:
            self.st["siti"] += 1
            self.st[f"esito_{d.get('esito')}"] += 1
            self.st["siti_con_email"] += bool(d.get("email"))
            self.st["siti_con_cell"] += bool(d.get("cell"))
            self.st["email_trovate"] += len(d.get("email") or [])
            self.st["cell_trovati"] += len(d.get("cell") or [])
            self.st["email_nuove"] += len(d.get("email_nuove") or [])
            self.st["cell_nuovi"] += len(d.get("cell_nuovi") or [])

    def siti(self) -> None:
        """Gruppi di RACC_GRUPPO siti dal database, RACC_PARALLELI alla volta (siti diversi: la pausa è per dominio)."""
        from concurrent.futures import ThreadPoolExecutor
        gruppo = max(1, int(_env_f("RACC_GRUPPO", 40)))
        rifai = _env_f("RACC_RIFAI_GIORNI", 7)
        with ThreadPoolExecutor(max(1, int(_env_f("RACC_PARALLELI", 4))), thread_name_prefix="racc") as ex:
            while self.tempo() and self.st["siti"] < self.max_siti:
                righe = self.arch.prendi(min(gruppo, self.max_siti - self.st["siti"]), rifai)
                if not righe:
                    return
                list(ex.map(self.uno, righe))
                if self.st["siti"] % (gruppo * 5) < gruppo:
                    log.warning("RACCOLTA avanzamento: %s", json.dumps(self.conteggi()))

    def paginegialle(self, pg_max: int, rete_pg) -> None:
        """A richiesta: commercianti (annunci AutoScout24/Subito) senza sito e senza cellulare cercati per nome e
        comune su Pagine Gialle. Con un sito, il sito entra tra quelli da leggere; i cellulari della voce si tengono."""
        from urllib.parse import quote
        from .raccolta60 import nel_raggio, nn, slug
        from .raccolta_numeri import PG_URL, pg_bloccata, vende_auto, voci_paginegialle
        import hashlib
        di_fila = 0
        while pg_max > 0 and self.tempo():
            righe = self.arch.senza_sito(min(40, pg_max))
            if not righe:
                return
            for r in righe:
                if not self.tempo() or pg_max <= 0:
                    return
                pg_max -= 1
                h = "pg:" + hashlib.sha1(str(r["chiave"]).encode()).hexdigest()[:20]
                base = {"nome": r["nome"], "comune": r["comune"], "prov": r["prov"], "rif": r["chiave"]}
                if escluso(r["nome"]) or not vende_auto(r["nome"]) or not nel_raggio(r["comune"], r["prov"]):
                    self.arch.aggiungi(h, {**base, "esito": "escluso", "tentato": "now"})
                    continue
                com = str(r["comune"]).split("-")[0].strip()
                url = PG_URL.format(cat=quote(str(r["nome"])[:80]), dove=quote(f"{com} ({r['prov']})"))
                self._aspetta("paginegialle.it", _env_f("RACC_PG_PAUSA", 2.0))
                try:
                    code, html = rete_pg.pagina(url, sblocco=True)
                except Exception:
                    code, html = 0, ""
                self.st["pg_richieste"] += 1
                if code == 0 or pg_bloccata(code, html):
                    di_fila += 1
                    if di_fila >= int(_env_f("RACC_PG_BLOCCHI_MAX", 6)):
                        self.st["pg_bloccata"] = 1
                        return
                    continue                      # riga non segnata: si riprova a un altro giro
                di_fila = 0
                voce = next((v for v in (voci_paginegialle(html) if code == 200 else [])
                             if len(nn(r["nome"])) >= 4 and nn(v.get("nome")) == nn(r["nome"])
                             and slug(v.get("comune") or com) == slug(com)), None)
                if not voce:
                    self.arch.aggiungi(h, {**base, "esito": "pg_niente", "tentato": "now"})
                    continue
                self.st["pg_trovati"] += 1
                cell = _cell(voce.get("cell") or [])
                nuovi = [c for c in cell if c not in self.arch.cell_noti(cell, h)]
                dom = dominio(voce.get("sito"))
                if dom and sito_ammesso(dom) and self.arch.aggiungi(dom, {**base, "sito": voce["sito"], "cell": [],
                                                                         "cell_nuovi": [], "esito": None, "tentato": None}):
                    self.st["pg_siti"] += 1
                self.arch.aggiungi(h, {**base, "cell": cell, "cell_nuovi": nuovi, "esito": "pg", "tentato": "now"})
                self.st["cell_nuovi"] += len(nuovi)
                self.st["cell_trovati"] += len(cell)

    def consegna(self) -> None:
        """Righe RACC_EMAIL / RACC_CELL in lead_consegne per i contatti nuovi non ancora consegnati."""
        soglia = int(_env_f("RACC_TEL_COMUNE", 4))
        for _ in range(100000):
            righe = self.arch.da_consegnare(200)
            if not righe:
                return
            for r in righe:
                base = {"nome": r.get("nome"), "comune": r.get("comune"), "prov": r.get("prov"), "sito": r.get("sito"),
                        "origine": r.get("origine")}
                cell = list(r.get("cell_nuovi") or [])
                tanti = self.arch.cell_su_tanti_siti(cell, soglia)
                self.st["scarto_cell_su_tanti_siti"] += len(tanti)
                cell = [c for c in cell if c not in tanti]
                out = []
                em = list(r.get("email_nuove") or [])
                if em:
                    out.append((TIPO_EMAIL, {**base, "email": em[0], "altre_email": " ".join(em[1:])}))
                    self.st["consegnate_email"] += 1
                if cell:
                    out.append((TIPO_CELL, {**base, "cellulare": "+39" + cell[0],
                                            "altri_cellulari": " ".join("+39" + c for c in cell[1:])}))
                    self.st["consegnati_cell"] += 1
                self.arch.consegna(r["dominio"], out)

    def conteggi(self) -> dict:
        with self.lock:
            return {k: int(v) for k, v in sorted(self.st.items())}


def _in_lead_email() -> bool:
    try:
        from .. import automatismi
        return automatismi.attivo("raccolta_contatti_in_lead_email", False)
    except Exception:
        return False


def _rete_pg():
    """La rete di raccolta_numeri (sblocco) per Pagine Gialle, solo se lo sblocco è configurato."""
    try:
        from .raccolta_numeri import Rete as RetePG
        r = RetePG()
        return r if r.sblocco() else None
    except Exception:
        return None


def lavoro(minuti: float, max_siti: int, pg_max: int = 0, archivio=None, rete=None, rete_pg=None) -> dict:
    """Un giro: semina dei siti, lettura (fino a `minuti` e `max_siti`), consegna. Ritorna solo conteggi;
    "da_riprendere": True se restano siti mai letti."""
    arch = archivio or Archivio()
    arch.prepara()
    semina = arch.semina()
    sua = rete is None
    rete = rete or Rete()
    R = Raccolta(arch, rete, time.time() + minuti * 60, max_siti)
    t0 = time.time()
    try:
        if pg_max > 0:
            rete_pg = rete_pg or _rete_pg()
            if rete_pg is None:
                R.st["pg_senza_sblocco"] = 1
            else:
                try:
                    R.paginegialle(pg_max, rete_pg)
                except Exception as e:
                    log.warning("RACCOLTA Pagine Gialle interrotta: %s", type(e).__name__)
        R.siti()
        R.consegna()
    finally:
        if sua:
            rete.chiudi()
    c = R.conteggi()
    resto = arch.da_leggere()
    comuni = {"siti": c.get("siti", 0), "da_leggere": resto, "richieste": c.get("richieste", 0),
              "minuti": round((time.time() - t0) / 60, 1)}
    log.warning("RACCOLTA_EMAIL %s", json.dumps({**comuni, "siti_con_email": c.get("siti_con_email", 0),
                                                "trovate": c.get("email_trovate", 0), "nuove": c.get("email_nuove", 0),
                                                "consegnate": c.get("consegnate_email", 0),
                                                "scarti": {k[7:]: v for k, v in c.items() if k.startswith("scarto_")
                                                           and "cell" not in k and k != "scarto_pagine_elenco"}}))
    log.warning("RACCOLTA_CELL %s", json.dumps({**comuni, "siti_con_cell": c.get("siti_con_cell", 0),
                                               "trovati": c.get("cell_trovati", 0), "nuovi": c.get("cell_nuovi", 0),
                                               "consegnati": c.get("consegnati_cell", 0),
                                               "scarti": {k[7:]: v for k, v in c.items() if k.startswith("scarto_")
                                                          and ("cell" in k or k == "scarto_pagine_elenco")},
                                               "pagine_gialle": {k[3:]: v for k, v in c.items() if k.startswith("pg_")}}))
    log.warning("RACCOLTA siti %s, risposte %s, semina %s", json.dumps({k[6:]: v for k, v in c.items() if k.startswith("esito_")}),
                json.dumps({str(k): v for k, v in sorted(R.http.items())}), json.dumps(semina))
    return {"siti": 0, "email_nuove": 0, "cell_nuovi": 0, **c, "da_leggere": resto, "da_riprendere": bool(resto)}


def esegui_voce(v: str) -> bool | None:
    """Ramo della coda (coda_lavori.esegui): raccolta_contatti:<etichetta>[:pg<N>]. Fatta solo quando non restano
    siti da leggere; a tempo finito (RACC_ORE) si RIMANDA e il cron della notte dopo riprende dai siti non letti
    (al massimo RACC_GIRI_MAX giri, contati in lead_contatti con la riga "giri:<etichetta>" di job_runs)."""
    from .ciclo import inizia
    parti = v.split(":")
    etichetta = re.sub(r"[^A-Za-z0-9-]", "", parti[1] if len(parti) > 1 else "")[:20] or "2511"
    m = re.fullmatch(r"pg(\d{1,5})", parti[2] if len(parti) > 2 else "")
    chiave = f"raccolta_contatti:{etichetta}"
    lav = inizia(chiave, tentativi_max=int(_env_f("RACC_TENTATIVI", 3)))
    if not lav:
        log.warning("RACCOLTA %s già fatta (o in corso in un altro processo)", chiave)
        return None
    try:
        esito = lavoro(_env_f("RACC_ORE", 2) * 60, int(_env_f("RACC_MAX_SITI", 20000)), pg_max=int(m.group(1)) if m else 0)
    except Exception as e:
        lav.fallito(e)
        log.warning("RACCOLTA non riuscita: %s", type(e).__name__)
        return False
    if esito.get("da_riprendere") and esito.get("siti", 0) > 0:
        lav.rimanda({"siti": esito.get("siti"), "da_leggere": esito.get("da_leggere")})
        return False                          # la riga resta da fare: il cron della notte dopo riprende
    lav.fatto({k: v for k, v in esito.items() if isinstance(v, (int, float, bool))})
    return True


def giro_periodico(oggi: str | None = None) -> dict:
    """Dal cron opportunità: con l'interruttore raccolta_contatti (SPENTO di default) un giro corto al giorno sui
    siti nuovi (quelli entrati da lead_maps, lead_email e dagli elenchi dopo l'ultimo giro) e su quelli in errore."""
    from .. import automatismi
    if not automatismi.attivo("raccolta_contatti", False):
        return {"spento": True}
    from .ciclo import _una_volta
    oggi = oggi or time.strftime("%Y%m%d")
    if not _una_volta(f"raccolta_contatti_giro:{oggi}"):
        return {"gia_fatto": True}
    minuti = min(120.0, max(1.0, automatismi.numero("raccolta_contatti_minuti", 15)))
    siti = int(min(5000, max(1, automatismi.numero("raccolta_contatti_siti", 150))))
    esito = lavoro(minuti, siti)
    return {k: v for k, v in esito.items() if isinstance(v, (int, float, bool))}
