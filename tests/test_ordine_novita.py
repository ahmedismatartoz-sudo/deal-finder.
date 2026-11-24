"""24/11 (cliente): elenco degli affari con le auto più nuove in cima e ordine "movimentato".

L'ordine si decide nel browser (dealfinder/web/static/scovo/app.js: filtered → ordinaLista): /api/affari manda
tutte le auto in una volta, senza pagine. Qui si prova il blocco ORDINE-NOVITA con node (se c'è) e si controlla nel
testo che l'ordine di base sia cambiato senza toccare gli altri ordinamenti."""
import json
import os
import shutil
import subprocess

JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dealfinder", "web", "static", "scovo", "app.js")


def _js() -> str:
    return open(JS, encoding="utf-8").read()


def _blocco() -> str:
    js = _js()
    return js[js.index("/* ORDINE-NOVITA inizio"):js.index("/* ORDINE-NOVITA fine */")]


def _node(corpo: str):
    node = shutil.which("node")
    if not node:
        return None
    r = subprocess.run([node, "-e", _blocco() + "\n" + corpo], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, TZ="Europe/Rome"))
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


# 24/11/2026 15:30 ora italiana; le auto: 5 per fascia (ultime 2 ore, oggi, ieri, settimana, vecchie) + 3 senza data
PREP = """
const ORA = Date.parse("2026-11-24T15:30:00+01:00");
const fa = (h) => new Date(ORA - h * 3600000).toISOString();
const auto = [];
[[0.2,0.5,1,1.5,2.2],[4,6,8,10,14],[17,20,25,30,38],[45,60,80,100,150],[200,300,400,500,900]].forEach((f, k) =>
  f.forEach((h, i) => auto.push({id: 1000 + k * 10 + i, visto_dal: fa(h), atteso: k})));
auto.push({id: 1, nuova: fa(1), atteso: 0}, {id: 2, atteso: 4}, {id: 3, visto_dal: "boh", atteso: 4});
const copia = () => auto.map(c => Object.assign({}, c));
const ids = (l) => l.map(c => c.id);
"""


def test_default_novita_e_altri_ordinamenti_restano():
    js = _js()
    assert 'sort: "novita"' in js and 'sort: "guadagno",   /* si entra SEMPRE' not in js
    assert '["novita","Più nuove"],["guadagno","Più guadagno %"],["prezzo","Prezzo più basso"],["nuove","Più recenti"],["km","Meno km"]' in js
    i = js.index("function ordinaLista")
    corpo = js[i:js.index("\n}\n", i)]
    # "Più guadagno %" come prima: vetrina in cima, poi perPct; supercar per sconto sul mercato
    assert "const altre = arr.filter(c => c.vetrina == null).sort(perPct);" in corpo
    assert 'if (s.sc && s.sort === "guadagno") return arr.slice().sort((a, b) => scontoM(b) - scontoM(a))' in corpo
    assert 'if (s.sort === "novita") return ordinaNovita(arr, chiOrdine(), S.ordT || Date.now());' in corpo
    # prezzo / più recenti / km: confronto invariato
    assert ('s.sort === "prezzo" ? a.prezzo - b.prezzo : s.sort === "nuove" ? String(b.nuova).localeCompare(String(a.nuova))'
            in js)
    # il seme usa l'ora in cui l'elenco è stato caricato, non quella di ogni ridisegno
    assert "S.ordT = S.loadedAt" in js
    # solo ordine: il blocco non filtra e non tocca soglie o margini
    b = _blocco()
    assert ".filter(" not in b and "soglia" not in b.lower() and "gain" not in b


def test_fasce_di_tempo():
    r = _node(PREP + """
    const l = ordinaNovita(copia(), "u7", ORA);
    console.log(JSON.stringify({n: l.length, fasce: l.map(c => [c.atteso, Number(c._grp.slice(1))]),
                                 insieme: ids(l).slice().sort((a, b) => a - b), tutti: ids(auto).slice().sort((a, b) => a - b)}));
    """)
    if r is None:
        return
    assert r["n"] == 28 and r["insieme"] == r["tutti"]          # nessuna auto persa o doppia
    assert all(a == f for a, f in r["fasce"])                    # fascia giusta, anche senza data (in fondo)
    assert [f for _, f in r["fasce"]] == sorted(f for _, f in r["fasce"])   # le più nuove sempre prima


def test_seme_per_utente_e_per_ora_stabile_dentro_l_ora():
    r = _node(PREP + """
    const a = ids(ordinaNovita(copia(), "u7", ORA));
    const b = ids(ordinaNovita(copia().reverse(), "u7", ORA + 20 * 60000));      // stessa ora, arrivo rovesciato
    const altroUtente = ids(ordinaNovita(copia(), "u8", ORA));
    const oraDopo = ids(ordinaNovita(copia(), "u7", ORA + 3600000));
    // "carica altre" / filtri: un pezzo dell'elenco tiene lo stesso ordine relativo dell'elenco intero
    const meta = copia().filter(c => c.id % 2 === 0);
    const sub = ids(ordinaNovita(meta, "u7", ORA));
    const pari = a.filter(i => i % 2 === 0);
    const vecchie = (l) => l.filter(i => i >= 1040);                              // fascia che non cambia tra un'ora e l'altra
    console.log(JSON.stringify({stabile: a.join() === b.join(), utente: a.join() !== altroUtente.join(),
      ora: vecchie(a).join() !== vecchie(oraDopo).join(), sotto: sub.join() === pari.join(),
      mescolato: vecchie(a).join() !== vecchie(a).slice().sort((x, y) => x - y).join(),
      semi: [semeNovita("u7", ORA), semeNovita("u7", ORA + 20 * 60000), semeNovita("u7", ORA + 3600000)]}));
    """)
    if r is None:
        return
    assert r["stabile"], "dentro la stessa ora l'ordine non deve cambiare"
    assert r["utente"], "due commercianti vedono ordini diversi"
    assert r["ora"], "all'ora dopo l'ordine cambia"
    assert r["sotto"], "un sottoinsieme (filtri, altre auto caricate) tiene lo stesso ordine relativo"
    assert r["mescolato"]
    assert r["semi"][0] == r["semi"][1] != r["semi"][2]


def test_mescolamento_uniforme():
    """Ogni auto di una fascia deve poter finire in cima: su 400 ore diverse la prima non è sempre la stessa."""
    r = _node(PREP + """
    const prime = {};
    for (let k = 0; k < 400; k++) { const l = ordinaNovita(copia().filter(c => c.atteso === 4), "u7", ORA + (k + 1) * 3600000);
      prime[l[0].id] = (prime[l[0].id] || 0) + 1; }
    console.log(JSON.stringify(prime));
    """)
    if r is None:
        return
    assert len(r) == 7 and min(r.values()) >= 25, r


def test_vetrina_non_bloccata_in_cima_con_piu_nuove():
    """24/11 sera (cliente): con "Più nuove" le scelte a mano seguono l'ordine come le altre; con "Più guadagno %" restano in cima."""
    js = _js()
    i = js.index("function ordinaLista")
    corpo = js[i:js.index("\n}\n", i)]
    a, b = corpo.index('if (s.sort === "novita")'), corpo.index('if (s.sc && s.sort === "guadagno")')
    ramo = corpo[a:b]
    assert a < b and "vetrina" not in ramo.split("\n")[0] and "return ordinaNovita(arr, chiOrdine()" in ramo
    assert "const vet = arr.filter(c => c.vetrina != null).sort((a, b) => a.vetrina - b.vetrina);" in corpo[b:]   # guadagno: come prima
    r = _node(PREP + """
    const S = {ordT: ORA, user: {id: 7}}, store = {get: () => null, set: () => {}};
    const perPct = (a, b) => a.id - b.id, scontoM = () => 0, CARE = 15000, TOP_MIN = 0, TOP_MAX = 0, PRIME = 15, topScore = () => 0;
    """ + js[js.index("function chiOrdine"):js.index("\n}\n", js.index("function chiOrdine")) + 3] + corpo + """
}
    const con = () => copia().map(c => Object.assign(c, {prezzo: 5000}, c.id === 1044 ? {vetrina: 0} : c.id === 1021 ? {vetrina: 1} : {}));
    const nov = ordinaLista({sort: "novita"}, con()), gua = ordinaLista({sort: "guadagno"}, con());
    console.log(JSON.stringify({nov: nov.map(c => [c.id, c._grp]), senza: ids(ordinaNovita(copia(), "u7", ORA)), gua: gua.slice(0, 3).map(c => [c.id, c._grp])}));
    """)
    if r is None:
        return
    assert [i for i, _ in r["nov"]] == r["senza"]                      # stesso ordine che senza vetrina
    assert dict(map(tuple, r["nov"]))[1044] == "n4" and dict(map(tuple, r["nov"]))[1021] == "n2"
    assert r["nov"][0][1] == "n0" and all(g != "top" for _, g in r["nov"])
    assert r["gua"][:2] == [[1044, "top"], [1021, "top"]] and r["gua"][2][1] == "rest"
