"""PDF "Inoltra al commercialista": riepilogo impresa, misure scelte, requisiti, documenti, link sicuro."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from . import motore

SEGNO = {"ok": "SI", "ko": "NO", "dubbio": "DA VERIFICARE"}
ETICH = {"conviene": "Conviene", "da_valutare": "Da valutare", "non_fa_per_te": "Non fa per te"}


def genera(impresa: dict, risultati: list[dict], checklist: dict, allegati: list[dict], link: str | None,
           scade: str | None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=16, spaceAfter=4)
    H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12.5, spaceBefore=10, spaceAfter=4)
    N = ParagraphStyle("n", parent=ss["BodyText"], fontSize=9.2, leading=12)
    S = ParagraphStyle("s", parent=N, fontSize=8, textColor=colors.HexColor("#555555"))

    def P(t, st=N):
        return Paragraph(escape(str(t or "")), st)

    p = impresa["profilo"]
    f = impresa.get("fonte") or {}
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title="Incentivi - riepilogo per il commercialista")
    el = [P("Riepilogo incentivi per il commercialista", H1),
          P(f"Preparato il {motore.it_date(f.get('generato'))} - catalogo regole versione {f.get('versione_catalogo', '')}. "
            "Le opportunità indicate sono COMPATIBILI con il profilo: non sono diritti acquisiti. "
            "Gli importi sono STIME, da verificare sui bandi ufficiali.", S)]
    if link:
        el.append(P(f"Link sicuro con documenti (scade il {scade}): {link}", S))
    el.append(P("Impresa", H2))
    campi = p.get("campi") or {}

    def fonte(c):
        x = campi.get(c) or {}
        return x.get("fonte") if x.get("stato") == "verificato" else "MANCANTE"

    sede = p.get("sede") or {}
    righe = [
        ["Ragione sociale", p.get("ragione_sociale"), fonte("ragione_sociale")],
        ["Partita IVA", p.get("piva"), "inserita dal commerciante"],
        ["Forma giuridica", p.get("forma_giuridica"), fonte("forma_giuridica")],
        ["ATECO", ", ".join(a["codice"] for a in p.get("ateco") or []), fonte("ateco")],
        ["Sede", ", ".join(x for x in (sede.get("comune"), sede.get("provincia"), sede.get("regione")) if x), fonte("sede")],
        ["Costituzione", motore.it_date(p.get("data_costituzione")), fonte("data_costituzione")],
        ["Stato", p.get("stato"), fonte("stato")],
        ["Addetti", p.get("addetti"), fonte("addetti")],
    ]
    t = Table([[P(a), P(b), P(c, S)] for a, b, c in righe], colWidths=[34 * mm, 70 * mm, 74 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el.append(t)
    for r in risultati:
        el.append(P(f"{r['nome']} - {ETICH.get(r['etichetta'], '')}", H2))
        el.append(P(f"{r['ente']} | {r['tipo_nome']}: {r['tipo_spiegazione']} | {r['bando']['etichetta']}", S))
        b = r["beneficio"]
        if b.get("valore"):
            el.append(P(f"Beneficio STIMATO: {motore.eur(b['valore'])} - {b['calcolo']} ({b['condizione']})"))
        elif b.get("calcolo"):
            el.append(P(f"Beneficio: {b['calcolo']} {b.get('condizione') or ''}"))
        el.append(P(("Copre il magazzino auto: sì" if r["copre_magazzino"] else "Copre il magazzino auto: NO (le auto da rivendere sono merce)"), S))
        if r["avviso_ordini"]:
            el.append(P("ATTENZIONE: non firmare ordini e non pagare acconti prima della domanda.", N))
        rows = [[P("Requisito", S), P("Esito", S), P("Dato usato e fonte", S)]]
        for x in r["regole"]:
            rows.append([P(x["testo"]), P(SEGNO[x["esito"]]), P(f"{x.get('dato') or ''} - {x.get('fonte') or ''}", S)])
        tt = Table(rows, colWidths=[78 * mm, 24 * mm, 76 * mm])
        tt.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
                                ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        el += [Spacer(1, 3), tt]
        docs = r.get("documenti") or []
        if docs:
            ck = checklist.get(r["id"], {})
            pronti = sum(1 for d in docs if ck.get(d["id"]))
            el.append(P(f"Documenti pronti: {pronti}/{len(docs)}", N))
            for d in docs:
                el.append(P(f"[{'x' if ck.get(d['id']) else ' '}] {d['nome']} - {d['spiegazione']}", S))
        el.append(P(f"Fonte ufficiale: {r['fonte_url']}" + ("" if r.get("fonte_ufficiale") else " (fonte NON ufficiale: verificare)"), S))
        el.append(P(f"Domanda su: {r.get('piattaforma') or 'da verificare'}. Ultima verifica del catalogo: "
                    f"{motore.it_date(r.get('ultima_verifica'))} ({r.get('verificatore')}).", S))
    if allegati:
        el.append(P("Documenti caricati", H2))
        for a in allegati:
            el.append(P(f"{a['nome']} ({a['tipo']}) - impronta SHA-256 {a['sha256'][:16]}... - {a.get('fonte') or ''}", S))
    el.append(Spacer(1, 8))
    el.append(P("La domanda la presenta il legale rappresentante (SPID/CNS/firma digitale) o il professionista con delega. "
                "Il software prepara, ricorda e controlla: non invia domande.", S))
    doc.build(el)
    return out.getvalue()
