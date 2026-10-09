"""Crea i file scaricabili della presentazione (PowerPoint e pagina unica con foto incluse).
Gira nel build del sito su Render, dopo il download delle foto in img/."""
import base64, io, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE)
cars = json.load(open("dati.json"))
lo = [c for c in cars if c["prezzo"] < 5000]; hi = [c for c in cars if c["prezzo"] >= 5000]
eur = lambda n: "—" if n is None else f"{n:,.0f} €".replace(",", ".")
DN = {"nessuno": "Sana", "leggero": "Danno leggero", "medio": "Danno medio", "alto_rischio": "Danno importante"}
NAME = "deal-finder-30-occasioni"

def photo(c, i):
    p = c["imgs"][i]["f"] if len(c["imgs"]) > i else None
    return p if p and os.path.exists(p) and os.path.getsize(p) > 1000 else None

def pptx_file():
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from PIL import Image
    BG, PANEL, LINE, FG, MUTED = (RGBColor(0x0e,0x11,0x14), RGBColor(0x16,0x1b,0x20), RGBColor(0x26,0x2e,0x36),
                                  RGBColor(0xee,0xf2,0xf5), RGBColor(0x93,0xa1,0xad))
    ACC, GOOD, WARN = RGBColor(0xff,0xd2,0x3f), RGBColor(0x3d,0xdc,0x97), RGBColor(0xff,0xb3,0x47)
    prs = Presentation(); prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    def slide():
        s = prs.slides.add_slide(blank); s.background.fill.solid(); s.background.fill.fore_color.rgb = BG; return s
    def text(s, x, y, w, h, t, size, color=FG, bold=False, font="Arial", align=None):
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)); tf = tb.text_frame; tf.word_wrap = True
        for k, line in enumerate(t if isinstance(t, list) else [t]):
            p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
            r = p.add_run(); r.text = line; r.font.size = Pt(size); r.font.color.rgb = color; r.font.bold = bold; r.font.name = font
            if align: p.alignment = align
        return tb
    def box(s, x, y, w, h, fill=PANEL, line=LINE):
        r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        r.fill.solid(); r.fill.fore_color.rgb = fill; r.line.color.rgb = line; r.shadow.inherit = False; return r
    def pic(s, path, x, y, w, h):
        im = Image.open(path).convert("RGB"); W, H = im.size; t = w / h
        if W / H > t: nw = int(H * t); im = im.crop(((W - nw)//2, 0, (W - nw)//2 + nw, H))
        else: nh = int(W / t); im = im.crop((0, (H - nh)//2, W, (H - nh)//2 + nh))
        im.thumbnail((1400, 1400)); b = io.BytesIO(); im.save(b, "JPEG", quality=82); b.seek(0)
        s.shapes.add_picture(b, Inches(x), Inches(y), Inches(w), Inches(h))
    tot = sum(c["m"] for c in cars)
    s = slide()
    text(s, .8, .7, 11, .4, "DEAL FINDER · MILANO E DINTORNI · 9 OTTOBRE 2026", 13, ACC, True)
    text(s, .8, 1.2, 11.5, 2.4, ["30 AUTO DA COMPRARE OGGI", "SOTTO IL PREZZO DI MERCATO"], 50, FG, True, "Arial Black")
    text(s, .8, 3.6, 10.5, 1, "Annunci di privati raccolti in automatico da Subito nelle 9 province intorno a Milano, confrontati con oltre 45.000 annunci di mercato. Per ogni auto: prezzo, valore di rivendita, ricambi e guadagno stimato.", 16, MUTED)
    for k, (n, l) in enumerate([("51.000", "annunci raccolti"), ("24.187", "annunci di privati esaminati"), (str(len(lo)), "auto sotto 5.000 €"), (f"{round(tot/1000)}k €", "margine totale stimato")]):
        box(s, .8 + k*3.0, 5.0, 2.8, 1.4); text(s, .95 + k*3.0, 5.1, 2.6, .7, n, 32, FG, True, "Arial Black"); text(s, .95 + k*3.0, 5.8, 2.6, .5, l, 12, MUTED)
    s = slide()
    text(s, .8, .6, 11, .4, "COME SI LEGGONO I NUMERI", 13, ACC, True); text(s, .8, 1.0, 11, .9, "DAL PREZZO AL GUADAGNO", 36, FG, True, "Arial Black")
    M = [("Valore di mercato", "Media dei prezzi delle auto simili (marca, modello, carburante, cambio, anno e km) tra gli annunci di privati in Lombardia."),
         ("Rivendita", "Valore di mercato meno 5% di trattativa. Si vende a un privato: niente IVA e niente garanzia."),
         ("Costi", "Passaggio 90 €, pulizia 50 €. Per le auto danneggiate i ricambi sono stimati pezzo per pezzo (solo pezzi, manodopera esclusa)."),
         ("Margine", "Rivendita meno prezzo, costi e ricambi al prezzo più alto. Accanto, il margine con i ricambi al prezzo più basso.")]
    for k, (t, d) in enumerate(M):
        x = .8 + (k % 2) * 6.0; y = 2.2 + (k // 2) * 2.1
        box(s, x, y, 5.7, 1.85); text(s, x + .2, y + .15, 5.3, .5, t.upper(), 18, FG, True); text(s, x + .2, y + .65, 5.3, 1.2, d, 13, MUTED)
    text(s, .8, 6.6, 11.5, .5, "Ogni auto va vista di persona, con visura e storico delle revisioni, prima di pagare.", 13, MUTED)
    def section(part, title, sub):
        s = slide(); text(s, .8, 2.3, 11, .4, part, 14, ACC, True); text(s, .8, 2.8, 11.5, 1.4, title, 60, ACC, True, "Arial Black"); text(s, .8, 4.3, 11, .6, sub, 18, MUTED)
    def car(c, n):
        s = slide()
        p0, p1 = photo(c, 0), photo(c, 1)
        if p0: pic(s, p0, .5, .5, 6.6, 4.4)
        else: box(s, .5, .5, 6.6, 4.4); text(s, .5, 2.5, 6.6, .5, "Foto sull'annuncio", 14, MUTED, align=2)
        if p1: pic(s, p1, .5, 5.05, 6.6, 1.95)
        t = box(s, .7, .7, .75, .4, ACC, ACC); text(s, .7, .7, .75, .4, n, 14, BG, True, align=2)
        X = 7.45; W = 5.4
        text(s, X, .45, W, .3, f"{c['citta']} ({c['prov']})".upper(), 11, ACC, True)
        text(s, X, .75, W, 1.0, c["name"].upper(), 22, FG, True, "Arial Black")
        km = eur(c["km"]).replace(" €", " km") if c["km"] else "km n.d."
        text(s, X, 1.75, W, .3, f"{c['anno']} · {km} · {c['carb'] or ''} · {c['cambio'] or ''}", 12, MUTED)
        stato = DN.get(c["danno"], c["danno"]); es = "Opportunità" if c["esito"] == "opportunita" else "Da verificare"
        text(s, X, 2.1, W, .3, f"{stato}  ·  {es}  ·  {c['sconto']}% sotto la media", 12, GOOD if c["danno"] == "nessuno" else WARN, True)
        for k, (l, v) in enumerate([("PREZZO", c["prezzo"]), ("VALORE MERCATO", c["mediana"]), ("RIVENDITA", c["rivendita"])]):
            box(s, X + k*1.8, 2.55, 1.75, .85); text(s, X + .08 + k*1.8, 2.58, 1.6, .3, l, 9, MUTED); text(s, X + .08 + k*1.8, 2.85, 1.6, .4, eur(v), 16, FG, True)
        text(s, X, 3.5, W, .8, "+" + eur(c["m"]), 40, GOOD, True, "Arial Black")
        text(s, X, 4.25, W, .3, "margine stimato" + (f" · fino a {eur(c['m2'])}" if c.get("m2") else ""), 12, MUTED)
        y = 4.65
        if c["items"]:
            lines = [f"{it['label']}: {eur(it['low'])}–{eur(it['high'])}" for it in c["items"][:5]]
            if len(c["items"]) > 5: lines.append(f"+ altri {len(c['items'])-5} pezzi")
            text(s, X, y, W, .3, "RICAMBI STIMATI", 10, MUTED, True); text(s, X, y + .25, W, 1.4, lines, 11, FG); y += .4 + .22*len(lines)
        ch = c["controlli"][:max(0, min(4, int((6.75 - y - .3) / .22)))]
        if ch:
            text(s, X, y, W, .3, "DA CONTROLLARE", 10, MUTED, True); text(s, X, y + .25, W, 1.2, ["• " + x for x in ch], 11, FG)
        lk = text(s, X, 6.85, W, .35, "Apri l'annuncio su Subito →", 12, ACC, True)
        r = lk.text_frame.paragraphs[0].runs[0]; r.hyperlink.address = c["url"]; r.font.color.rgb = ACC
    section("PARTE 1", "SOTTO 5.000 €", f"{len(lo)} auto. Quasi tutte con piccoli danni: il guadagno sta nella riparazione.")
    for i, c in enumerate(lo): car(c, f"#{i+1}")
    section("PARTE 2", "OLTRE 5.000 €", f"{len(hi)} auto, in gran parte sane e con molti annunci simili per il confronto.")
    for i, c in enumerate(hi): car(c, f"#{len(lo)+i+1}")
    prs.save(NAME + ".pptx"); print("FILE_OK pptx", os.path.getsize(NAME + ".pptx"))

def html_file():
    s = open("index.html", encoding="utf-8").read()
    def emb(m):
        p = m.group(1)
        if os.path.exists(p): return '"f":"data:image/jpeg;base64,' + base64.b64encode(open(p, "rb").read()).decode() + '"'
        return m.group(0)
    s = re.sub(r'"f": ?"(img/[^"]+)"', emb, s)
    s = s.replace('<a class="dl"', '<a hidden class="dl"')
    open(NAME + ".html", "w", encoding="utf-8").write(s); print("FILE_OK html", os.path.getsize(NAME + ".html"))

for f in (pptx_file, html_file):
    try: f()
    except Exception as e: print("FILE_KO", f.__name__, repr(e)[:300])
