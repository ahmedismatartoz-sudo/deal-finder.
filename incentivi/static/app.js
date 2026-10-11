"use strict";
/* Incentivi — prototipo. Tre passaggi: partita IVA → poche domande → risultati. */
const $app = document.getElementById("app");
const S = { stato: null, imp: null, res: null, risposte: {}, extra: {} };
const LS = {
  get(k, d) { try { const v = localStorage.getItem("inc_" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("inc_" + k, JSON.stringify(v)); } catch (e) { /* niente */ } },
};
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = n => (Math.round(n) || 0).toLocaleString("it-IT") + " €";
const dIt = s => s ? new Date(s).toLocaleDateString("it-IT") : "—";
const corto = n => String(n || "").split(/ [-–(]| \u2013 /)[0].trim();
const frase = t => { const x = String(t || ""); const i = x.search(/[.;]\s/); const f = i > 0 ? x.slice(0, i) : x; return f.length > 110 ? f.slice(0, 107) + "…" : f; };
const ICON = { ok: "✅", ko: "❌", dubbio: "❓" };
const LAB = { conviene: "🟢 Conviene", da_valutare: "🟡 Da valutare", non_fa_per_te: "⚪ Non fa per te" };
const EXTRA_DOMANDE = {  // regola "dato mancante" → domanda e campo salvato nel profilo
  artigiana: ["artigiana", "L'impresa è iscritta all'albo artigiani?"],
  fondo: [null, "Aderisci a questo fondo interprofessionale? (lo sa il consulente del lavoro)"],
  lavoratore: ["lavoratore_requisiti", "La persona che vuoi assumere ha i requisiti richiesti?"],
  beni_40: ["beni_40", "I beni sono nuovi, tecnologici e collegati al gestionale (beni 4.0)?"],
  stabilizzazione: ["stabilizzazione", "Hai un dipendente under 35 a tempo determinato da stabilizzare?"],
  energia: ["intervento_energetico", "Vuoi sostituire il riscaldamento con una pompa di calore?"],
  veicolo: ["veicolo_commerciale", "Vuoi comprare un veicolo commerciale nuovo per l'attività?"],
  gpl: ["gpl", "Sei un'officina abilitata agli impianti GPL/metano?"],
};

async function api(path, opts = {}) {
  const r = await fetch("/incentivi" + path, {
    method: opts.method || "GET", credentials: "same-origin",
    headers: opts.body ? { "content-type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let j = null;
  try { j = await r.json(); } catch (e) { /* risposta non JSON */ }
  if (r.status === 401) { location.href = "/#/entra"; throw new Error("Accesso richiesto"); }
  if (!r.ok) throw new Error((j && j.errore) || "Errore di rete, riprova");
  return j;
}

function nav(on) { document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("on", a.dataset.nav === on)); }
function go(h) { location.hash = h; }
function busy(msg) { $app.innerHTML = `<p class="muted"><span class="spin"></span> ${esc(msg)}</p>`; }
function fail(e) { $app.insertAdjacentHTML("afterbegin", `<div class="err">${esc(e.message || e)}</div>`); }

/* ------------------------------------------------------------- 1. partita IVA */
async function home() {
  nav("home");
  if (!S.stato) { try { S.stato = await api("/api/stato"); } catch (e) { S.stato = { visura: {}, demo: [] }; } }
  const last = LS.get("impresa", null);
  $app.innerHTML = `
    <h1>Quanti soldi stai lasciando sul tavolo?</h1>
    <p class="muted">Carica la visura: in 10 secondi vedi quanto puoi ottenere, risparmiare e quanto hai già perso.</p>
    <label class="upload" id="up">
      <input type="file" id="vis" accept="application/pdf">
      <span class="up-ic" aria-hidden="true">📄</span>
      <b id="upt">Carica la visura camerale</b>
      <span class="small">PDF · la scarichi gratis dal <a href="https://impresa.italia.it" target="_blank" rel="noopener">Cassetto Digitale</a> con lo SPID</span>
    </label>
    <div id="ferr"></div>
    <p class="center"><button type="button" class="linkbtn" id="nodoc">Non ho la visura: rispondo a 6 domande</button></p>
    ${last ? `<p class="center"><a href="#/risultati/${last}">Riprendi l'ultima impresa →</a></p>` : ""}
    <details class="esempi"><summary>Prova con un esempio</summary>
      <div class="demo">${(S.stato.demo || []).map(d => `<button data-demo="${esc(d.id)}"><b>${esc(d.etichetta)}</b><span class="muted small">${esc(d.descrizione)}</span></button>`).join("")}</div>
    </details>`;
  const inp = document.getElementById("vis");
  const box = document.getElementById("ferr");
  inp.addEventListener("change", async () => {
    const f = inp.files[0]; if (!f) return;
    box.innerHTML = "";
    if (f.type !== "application/pdf" || f.size > 8 * 1024 * 1024) { box.innerHTML = `<div class="err">Carica la visura in PDF (massimo 8 MB).</div>`; return; }
    document.getElementById("upt").innerHTML = `<span class="spin"></span> Leggo la visura…`;
    try {
      const dati = await new Promise((ok, ko) => { const rd = new FileReader(); rd.onload = () => ok(String(rd.result).split(",")[1]); rd.onerror = ko; rd.readAsDataURL(f); });
      const imp = await api("/api/impresa", { method: "POST", body: { modo: "visura_pdf", visura: { nome: f.name, dati } } });
      S.imp = imp; S.res = null; S.risposte = {}; S.extra = {}; S.prof = {};
      LS.set("impresa", imp.id);
      calcola(imp.id);           // con la visura: nessuna domanda, subito i soldi
    } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; document.getElementById("upt").textContent = "Carica la visura camerale"; inp.value = ""; }
  });
  document.getElementById("nodoc").addEventListener("click", async ev => {
    ev.target.disabled = true;
    try { apriImpresa(await api("/api/impresa", { method: "POST", body: { modo: "domande" } })); }
    catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; ev.target.disabled = false; }
  });
  $app.querySelectorAll("[data-demo]").forEach(b => b.addEventListener("click", async () => {
    b.disabled = true;
    try { const imp = await api("/api/impresa", { method: "POST", body: { demo: b.dataset.demo } }); S.imp = imp; S.res = null; S.risposte = {}; LS.set("impresa", imp.id); calcola(imp.id); }
    catch (e) { fail(e); b.disabled = false; }
  }));
}

function pivaOk(p) {
  let s = 0;
  for (let i = 0; i < 10; i++) { let n = +p[i]; if (i % 2) { n *= 2; if (n > 9) n -= 9; } s += n; }
  return (10 - s % 10) % 10 === +p[10];
}

function apriImpresa(imp) {
  S.imp = imp; S.res = null; S.risposte = {}; S.extra = {}; S.prof = {};
  LS.set("impresa", imp.id);
  go("#/impresa/" + imp.id);
}

/* ------------------------------------------------------------- 2. riepilogo + domande */
function tag(campi, k) {
  const c = (campi || {})[k] || {};
  if (c.stato === "verificato") return `<span class="tag v" title="${esc(c.fonte || "")}">verificato</span>`;
  if (c.stato === "dichiarato") return `<span class="tag e" title="${esc(c.fonte || "")}">dichiarato</span>`;
  return `<span class="tag m">mancante</span>`;
}

async function impresa(id) {
  nav("home");
  busy("Carico i dati dell'impresa…");
  let imp;
  try { imp = await api("/api/impresa/" + id); } catch (e) { $app.innerHTML = ""; fail(e); return; }
  S.imp = imp;
  S.risposte = Object.assign({}, imp.risposte || {});
  const p = imp.profilo, c = p.campi || {};
  const sede = p.sede || {};
  const vis = imp.visura || {};
  const fonteData = (imp.fonte || {}).data ? dIt(imp.fonte.data) : "";
  const visBox = vis.documento
    ? `<p class="small">📄 Visura originale conservata: <a href="/incentivi/api/impresa/${imp.id}/documenti/${vis.documento.id}">${esc(vis.documento.nome)}</a><br><span class="muted">${esc(vis.documento.fonte)}</span></p>`
    : (vis.richiesta ? `<p class="small note">Visura PDF in preparazione (${esc(vis.richiesta.stato)}): ricarica tra poco.</p>`
      : `<p class="small muted">${(imp.fonte || {}).tipo === "esempio" ? "Profilo di ESEMPIO: nessuna visura reale." : "Visura PDF non disponibile."}</p>`);
  const qs = imp.domande || [];
  const qp = imp.domande_profilo || [];
  S.prof = S.prof || {};
  const noti = Object.values(c).some(x => x && (x.stato === "verificato" || x.stato === "dichiarato"));
  $app.innerHTML = `
    <a class="back" href="#/">← Cambia impresa</a>
    <h1>${esc(p.ragione_sociale || "La tua impresa")}</h1>
    <p class="muted small">P.IVA ${esc(p.piva || "non indicata")}${fonteData ? " · " + fonteData : ""}</p>
    ${qp.length ? `<h2>La tua impresa</h2><section class="card" id="qp">${qp.map(domandaProfilo).join("")}</section>` : ""}
    <section class="card" ${noti ? "" : 'hidden'}>
      <dl class="kv">
        <dt>Forma giuridica</dt><dd>${esc(p.forma_giuridica || "—")} ${tag(c, "forma_giuridica")}</dd>
        <dt>Attività (ATECO)</dt><dd>${(p.ateco || []).map(a => `${esc(a.codice)}${a.primario ? " (principale)" : ""}`).join(", ") || "—"} ${tag(c, "ateco")}
          ${(p.ateco2025 || []).length ? `<br><span class="small muted">ATECO 2025: ${p.ateco2025.map(a => esc(a.codice)).join(", ")}${(p.ateco2025[0].descrizione || "").includes("indicativa") ? " (corrispondenza indicativa)" : ""}</span>` : ""}</dd>
        <dt>Sede</dt><dd>${esc([sede.comune, sede.provincia, sede.regione].filter(Boolean).join(", ") || "—")} ${tag(c, "sede")}</dd>
        ${(p.unita_locali || []).length ? `<dt>Unità locali</dt><dd>${p.unita_locali.map(u => esc([u.comune, u.provincia].filter(Boolean).join(" "))).join("; ")} ${tag(c, "unita_locali")}</dd>` : ""}
        <dt>Costituzione</dt><dd>${dIt(p.data_costituzione)} ${tag(c, "data_costituzione")}</dd>
        <dt>Stato</dt><dd>${esc(p.stato || "—")} ${tag(c, "stato")}</dd>
        <dt>Addetti</dt><dd>${p.addetti == null ? "—" : esc(p.addetti)} ${tag(c, "addetti")}</dd>
        <dt>Soci e amministratori</dt><dd>${[...(p.soci || []), ...(p.amministratori || [])].length ? esc([...(p.soci || []), ...(p.amministratori || [])].join(", ")) : "—"} ${tag(c, "soci")}</dd>
      </dl>
      ${visBox}
    </section>
    <h2>${qs.length ? (qp.length ? "E ancora " : "Ancora ") + qs.length + " domande veloci" : "Nessuna domanda: abbiamo tutto"}</h2>
    <section class="card" id="qs">${qs.map(domanda).join("")}</section>
    <div class="sticky"><button class="btn block" id="vai">Vedi i risultati</button>
    <p class="small muted">Puoi rispondere dopo: le risposte mancanti restano "da verificare".</p></div>`;
  wireDomande();
  wireProfilo();
  document.getElementById("vai").addEventListener("click", () => calcola(imp.id));
}

function domandaProfilo(q) {
  const v = S.prof[q.id];
  if (q.tipo === "anno") {
    return `<div class="q"><p>${esc(q.testo)}</p><input type="text" inputmode="numeric" maxlength="4" data-p="anno" placeholder="Es. 2015" value="${v ? esc(v) : ""}" aria-label="${esc(q.testo)}"></div>`;
  }
  if (q.opzioni.length > 4) {
    return `<div class="q"><p>${esc(q.testo)}</p><select data-p="${q.id}" aria-label="${esc(q.testo)}" class="sel">
      <option value="">Scegli…</option>${q.opzioni.map(o => `<option value="${esc(o[0])}" ${v === o[0] ? "selected" : ""}>${esc(o[1])}</option>`).join("")}</select></div>`;
  }
  return `<div class="q"><p>${esc(q.testo)}</p><div class="yn wrap" role="group" aria-label="${esc(q.testo)}">${q.opzioni.map(o =>
    `<button type="button" data-p="${q.id}" data-v="${esc(o[0])}" class="${v === o[0] ? "on" : ""}" aria-pressed="${v === o[0]}">${esc(o[1])}</button>`).join("")}</div>
    ${q.id === "attivita" ? `<input type="text" data-p="ateco" placeholder="Codice ATECO, es. 43.21" value="${esc(S.prof.ateco || "")}" ${v === "altro" ? "" : "hidden"} style="margin-top:8px" aria-label="Codice ATECO">` : ""}</div>`;
}

function wireProfilo() {
  $app.querySelectorAll("#qp button[data-p]").forEach(b => b.addEventListener("click", () => {
    S.prof[b.dataset.p] = b.dataset.v;
    b.parentNode.querySelectorAll("button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
    if (b.dataset.p === "attivita") { const a = $app.querySelector('input[data-p="ateco"]'); if (a) a.hidden = b.dataset.v !== "altro"; }
    if (b.dataset.p === "attivita" && /officina/.test(b.dataset.v)) S.risposte.officina = true;
  }));
  $app.querySelectorAll("#qp select[data-p]").forEach(sl => sl.addEventListener("change", () => { S.prof[sl.dataset.p] = sl.value; }));
  $app.querySelectorAll("#qp input[data-p]").forEach(i => i.addEventListener("input", () => { S.prof[i.dataset.p] = i.value.trim(); }));
}

function domanda(q) {
  const v = S.risposte[q.id];
  if (q.tipo === "euro") {
    return `<div class="q"><p>${esc(q.testo)}</p><input type="text" inputmode="numeric" data-q="${q.id}" placeholder="Es. 15000" value="${v != null ? esc(v) : ""}" aria-label="${esc(q.testo)}">
      ${q.aiuto ? `<span class="small muted">${esc(q.aiuto)}</span>` : ""}</div>`;
  }
  return `<div class="q"><p>${esc(q.testo)}</p><div class="yn" role="group" aria-label="${esc(q.testo)}">
    <button type="button" data-q="${q.id}" data-v="1" class="${v === true ? "on" : ""}" aria-pressed="${v === true}">Sì</button>
    <button type="button" data-q="${q.id}" data-v="0" class="${v === false ? "on" : ""}" aria-pressed="${v === false}">No</button></div></div>`;
}

function wireDomande() {
  $app.querySelectorAll(".yn button").forEach(b => b.addEventListener("click", () => {
    S.risposte[b.dataset.q] = b.dataset.v === "1";
    b.parentNode.querySelectorAll("button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
  }));
  $app.querySelectorAll("input[data-q]").forEach(i => i.addEventListener("input", () => {
    const n = i.value.replace(/\D/g, "");
    if (n === "") delete S.risposte[i.dataset.q]; else S.risposte[i.dataset.q] = +n;
  }));
}

async function calcola(id, extra) {
  busy("Controllo i requisiti uno per uno…");
  try {
    const prof = S.prof && Object.keys(S.prof).length ? { profilo: S.prof } : {};
    S.res = await api(`/api/impresa/${id}/valuta`, { method: "POST", body: Object.assign({}, S.risposte, prof, extra ? { extra } : {}) });
    S.prof = {};
    go("#/risultati/" + id);
  } catch (e) { $app.innerHTML = ""; fail(e); }
}

/* ------------------------------------------------------------- 3. risultati */
async function ensureRes(id) {
  if (S.res && S.imp && String(S.imp.id) === String(id)) return S.res;
  const imp = await api("/api/impresa/" + id);
  S.imp = imp; S.risposte = imp.risposte || {};
  S.res = await api(`/api/impresa/${id}/valuta`, { method: "POST", body: S.risposte });
  return S.res;
}

function valoreHtml(r) {
  const b = r.beneficio || {};
  if (b.valore) return `<p class="val">fino a ${eur(b.valore)} <span class="tag e">stima</span></p><p class="calc">${esc(b.calcolo)}${b.condizione ? " — " + esc(b.condizione) : ""}</p>`;
  if (b.calcolo) return `<p class="calc">${esc(b.calcolo)}${b.condizione ? " — " + esc(b.condizione) : ""}</p>`;
  return "";
}

function soldiDi(r) {
  const b = r.beneficio || {};
  return b.valore && b.conta ? b.valore : 0;
}

function card(r, id) {
  const v = soldiDi(r);
  const prestito = r.tipo === "finanziamento_agevolato" || r.tipo === "garanzia";
  return `<a class="row-opp" href="#/misura/${id}/${encodeURIComponent(r.id)}">
    <span class="ro-main"><b>${esc(corto(r.nome))}</b>
      <span class="small muted">${esc(r.tipo_nome)}${prestito ? " · da restituire" : ""} · ${esc(r.bando.etichetta)}</span></span>
    <span class="ro-eur ${v ? "" : "muted"}">${v ? eur(v) : (prestito ? "prestito" : "—")}<i>${r.etichetta === "conviene" ? "🟢" : "🟡"}</i></span>
  </a>`;
}

async function risultati(id) {
  nav("home");
  busy("Calcolo i tuoi soldi…");
  let res;
  try { res = await ensureRes(id); } catch (e) { $app.innerHTML = ""; fail(e); return; }
  const m = res.soldi || {};
  const utili = res.risultati.filter(r => r.etichetta !== "non_fa_per_te");
  const nofa = res.risultati.filter(r => r.etichetta === "non_fa_per_te");
  utili.sort((a, b) => (a.etichetta !== "conviene") - (b.etichetta !== "conviene") || soldiDi(b) - soldiDi(a));
  const nome = (res.impresa || {}).ragione_sociale || (S.imp && S.imp.profilo && S.imp.profilo.ragione_sociale) || "La tua impresa";
  const assume = utili.some(r => r.tipo === "sgravio_contributivo" && soldiDi(r));
  $app.innerHTML = `
    <p class="muted small" style="margin:0">${esc(nome)}</p>
    <section class="money">
      <div class="m-tile m-get"><span>Puoi ottenere</span><b>${eur(m.ottenere || 0)}</b><small>contributi a fondo perduto</small></div>
      <div class="m-tile m-save"><span>Puoi risparmiare</span><b>${eur(m.risparmiare || 0)}</b><small>${assume ? "contributi, se assumi" : "contributi e tasse"}</small></div>
      <div class="m-tile m-lost"><span>Hai perso negli ultimi ${m.persi_mesi || 6} mesi</span><b>${eur(m.persi || 0)}</b><small>bandi chiusi a cui potevi partecipare</small></div>
    </section>
    <p class="small muted center">Stime: i requisiti vanno confermati sui bandi. ${(m.persi_dettaglio || []).length ? `<button type="button" class="linkbtn small" id="vedipersi">Quali ho perso?</button>` : ""}</p>
    <div id="persi" hidden class="card small">${(m.persi_dettaglio || []).map(x => `<div class="pl"><span>${esc(corto(x.nome))}<br><span class="muted">chiuso il ${dIt(x.chiuso)}</span></span><b>${eur(x.valore)}</b></div>`).join("")}
      <p class="muted">Calcolato su una spesa tipica di ${eur(m.spesa_tipica || 20000)}. Con noi ricevi l'avviso prima che aprano.</p></div>
    <button class="btn block big" id="tutto">📨 Manda tutto al commercialista</button>
    <div id="out"></div>
    ${res.avviso ? alertHtml(res, id) : ""}
    <h2>Le tue opportunità (${utili.length})</h2>
    <section class="card list-opp">${utili.map(r => card(r, id)).join("") || `<p><b>Oggi non c'è nulla di utile per te.</b><br><span class="muted">Ti avvisiamo appena esce qualcosa.</span></p>`}</section>
    ${Object.keys(res.risposte || {}).length < 2 ? `<p class="center small"><a href="#/impresa/${id}">🎯 Rendi la stima più precisa (3 domande facoltative)</a></p>` : ""}
    <details class="nofa"><summary>Non fanno per te (${nofa.length})</summary>
      ${nofa.map(r => `<div class="nofa-item"><b>${esc(corto(r.nome))}</b><span class="muted">${esc(r.motivo)}</span></div>`).join("")}
    </details>`;
  const vp = document.getElementById("vedipersi");
  if (vp) vp.addEventListener("click", () => { const d = document.getElementById("persi"); d.hidden = !d.hidden; });
  document.getElementById("tutto").addEventListener("click", ev => inviaTutto(id, utili.map(r => r.id), ev.target));
}

async function inviaTutto(id, misure, btn) {
  const out = document.getElementById("out");
  if (!misure.length) { out.innerHTML = `<div class="note">Non ci sono opportunità da inoltrare oggi.</div>`; return; }
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span> Preparo il file…`;
  try {
    const r = await api(`/api/impresa/${id}/inoltra`, { method: "POST", body: { misure } });
    const mail = `mailto:?subject=${encodeURIComponent(r.oggetto)}&body=${encodeURIComponent(r.testo)}`;
    const wa = `https://wa.me/?text=${encodeURIComponent(r.testo)}`;
    btn.hidden = true;
    out.innerHTML = `<section class="card"><p><b>Pronto.</b> Dentro c'è tutto: dati dell'impresa, visura, opportunità, calcoli e documenti. Link valido fino al ${esc(r.scade)}.</p>
      <div class="share"><a class="btn" href="${esc(mail)}">✉️ Email</a><a class="btn" href="${esc(wa)}" target="_blank" rel="noopener">💬 WhatsApp</a><a class="btn sec" href="${esc(r.pdf)}" target="_blank" rel="noopener">📄 PDF</a></div></section>`;
  } catch (e) { out.innerHTML = `<div class="err">${esc(e.message)}</div>`; btn.disabled = false; btn.textContent = "📨 Manda tutto al commercialista"; }
}

function alertHtml(res, id) {
  const r = res.risultati.find(x => x.id === res.avviso.id) || {};
  const g = res.avviso.giorni;
  return `<a class="alert" href="#/misura/${id}/${encodeURIComponent(res.avviso.id)}"><span aria-hidden="true">⏰</span>
    <span>${esc(corto(r.nome || res.avviso.testo))}: chiude il ${dIt(r.bando && r.bando.chiusura)}${g != null ? ` — mancano ${g} giorni` : ""}</span></a>`;
}

async function misura(id, mid) {
  nav("home");
  busy("Apro l'opportunità…");
  let res;
  try { res = await ensureRes(id); } catch (e) { $app.innerHTML = ""; fail(e); return; }
  const r = res.risultati.find(x => x.id === mid);
  if (!r) { $app.innerHTML = `<p>Opportunità non trovata.</p>`; return; }
  const extra = r.regole.filter(x => x.esito === "dubbio" && EXTRA_DOMANDE[x.id] && /da chiedere/.test(x.dato || ""))
    .map(x => ({ campo: EXTRA_DOMANDE[x.id][0] || (r.id.startsWith("forte") ? "fondo_forte" : "fondo_fondartigianato"), testo: EXTRA_DOMANDE[x.id][1] }));
  $app.innerHTML = `
    <a class="back" href="#/risultati/${id}">← Tutte le opportunità</a>
    <div class="row" style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
      <h1>${esc(r.nome)}</h1><span class="lab ${r.etichetta}">${LAB[r.etichetta]}</span></div>
    <p class="muted">${esc(r.ente || "")} · ${esc(r.bando.etichetta)}</p>
    <section class="card">
      <p><b>${esc(r.tipo_nome)}</b>: ${esc(r.tipo_spiegazione)}</p>
      <p>${esc(r.in_breve || "")}</p>
      ${valoreHtml(r)}
      <p class="small">${r.copre_magazzino ? "✔ Può aiutare anche per le scorte (è una garanzia sul prestito, da restituire)." : "✖ Non copre il magazzino: le auto comprate per rivenderle sono merce."}</p>
      ${r.etichetta === "non_fa_per_te" ? `<div class="err">${esc(r.motivo)}</div>` : ""}
    </section>
    ${r.avviso_ordini ? `<div class="warnbox">⚠️ Non firmare ordini e non pagare acconti prima della domanda: renderebbero la spesa non ammissibile.</div>` : ""}
    ${r.azione && r.etichetta !== "non_fa_per_te" ? `<p><button type="button" class="btn block" data-scroll="documenti">Prossima azione: ${esc(r.azione)}</button></p>` : ""}
    ${extra.length ? `<h2>Ci servono ancora queste risposte</h2><section class="card" id="extra">${extra.map(q => `<div class="q"><p>${esc(q.testo)}</p><div class="yn"><button type="button" data-x="${q.campo}" data-v="1">Sì</button><button type="button" data-x="${q.campo}" data-v="0">No</button></div></div>`).join("")}</section>` : ""}
    ${(r.documenti || []).length ? `<h2 id="documenti">Documenti da preparare (${r.documenti_pronti || 0}/${r.documenti.length})</h2>
    <section class="card"><ul class="docs">${r.documenti.map(d => `<li>
      <input type="checkbox" data-doc="${esc(d.id)}" ${r.checklist && r.checklist[d.id] ? "checked" : ""} aria-label="${esc(d.nome)} pronto">
      <span><b>${esc(d.nome)}</b><small>${esc(d.spiegazione)}</small></span>
      ${d.id === "visura" ? "" : `<label class="up">Carica<input type="file" accept="application/pdf,image/jpeg,image/png" data-up="${esc(d.id)}"></label>`}
    </li>`).join("")}</ul></section>` : ""}
    <h2>Requisiti, uno per uno</h2>
    <section class="card"><ul class="req">${r.regole.map(x => `<li><span class="ic" aria-label="${x.esito}">${ICON[x.esito]}</span>
      <span><b>${esc(x.testo)}</b><small>Dato: ${esc(x.dato || "—")} · Fonte: ${esc(x.fonte || "—")}${x.nota ? " · " + esc(x.nota) : ""}</small>
      ${x.riferimento ? `<small><a href="${esc(x.riferimento)}" target="_blank" rel="noopener">Regola dal bando ufficiale</a></small>` : ""}</span></li>`).join("")}</ul></section>
    ${r.requisiti_extra.length ? `<h2>Altre condizioni del bando</h2><section class="card"><ul class="plain">${r.requisiti_extra.map(t => `<li>${esc(t)}</li>`).join("")}</ul></section>` : ""}
    <section class="card small">
      <p><b>Dove si fa la domanda:</b> ${esc(r.piattaforma || "da verificare")}</p>
      ${r.cumulo ? `<p><b>Cumulo:</b> ${esc(r.cumulo)}</p>` : ""}
      ${r.prossima_edizione ? `<p><span class="prev">previsione</span> ${esc(r.prossima_edizione)}</p>` : ""}
      <p><b>Fonte:</b> <a href="${esc(r.fonte_url)}" target="_blank" rel="noopener">${esc(r.fonte_url)}</a>${r.fonte_ufficiale ? "" : ` <span class="tag e">fonte non ufficiale: da verificare</span>`}</p>
      <p class="muted">Scheda verificata il ${dIt(r.ultima_verifica)} — ${esc(r.verificatore || "")}.</p>
      ${r.dubbi_catalogo.length ? `<details><summary>Punti ancora da verificare sulla scheda (${r.dubbi_catalogo.length})</summary><ul class="plain">${r.dubbi_catalogo.map(t => `<li>${esc(t)}</li>`).join("")}</ul></details>` : ""}
    </section>`;
  $app.querySelectorAll("[data-scroll]").forEach(b => b.addEventListener("click", () => {
    const t = document.getElementById(b.dataset.scroll); if (t) t.scrollIntoView({ behavior: "smooth" });
  }));
  $app.querySelectorAll("[data-x]").forEach(b => b.addEventListener("click", () => {
    S.extra[b.dataset.x] = b.dataset.v === "1";
    rivaluta(id, mid);
  }));
  $app.querySelectorAll("[data-doc]").forEach(cb => cb.addEventListener("change", async () => {
    try { await api(`/api/impresa/${id}/checklist`, { method: "POST", body: { misura_id: mid, doc_id: cb.dataset.doc, pronto: cb.checked } }); S.res = null; }
    catch (e) { fail(e); }
  }));
  $app.querySelectorAll("[data-up]").forEach(inp => inp.addEventListener("change", async () => {
    const f = inp.files[0]; if (!f) return;
    if (f.size > 8 * 1024 * 1024) { fail(new Error("Il file deve essere più piccolo di 8 MB")); return; }
    const lab = inp.parentNode; lab.firstChild.textContent = "Carico…";
    const b64 = await new Promise((ok, ko) => { const rd = new FileReader(); rd.onload = () => ok(String(rd.result).split(",")[1]); rd.onerror = ko; rd.readAsDataURL(f); });
    try {
      await api(`/api/impresa/${id}/documenti`, { method: "POST", body: { misura_id: mid, doc_id: inp.dataset.up, nome: f.name, mime: f.type, dati: b64 } });
      lab.firstChild.textContent = "Caricato ✓"; S.res = null;
      const cb = $app.querySelector(`[data-doc="${inp.dataset.up}"]`); if (cb) cb.checked = true;
    } catch (e) { lab.firstChild.textContent = "Carica"; fail(e); }
  }));
}


async function rivaluta(id, mid) {
  busy("Ricalcolo…");
  try {
    S.res = await api(`/api/impresa/${id}/valuta`, { method: "POST", body: Object.assign({}, S.risposte, { extra: S.extra }) });
    misura(id, mid);
  } catch (e) { $app.innerHTML = ""; fail(e); }
}

/* ------------------------------------------------------------- inoltra */
async function inoltra(id) {
  nav("home");
  let res;
  try { res = await ensureRes(id); } catch (e) { fail(e); return; }
  const utili = res.risultati.filter(r => r.etichetta !== "non_fa_per_te");
  $app.innerHTML = `
    <a class="back" href="#/risultati/${id}">← Risultati</a>
    <h1>Inoltra al commercialista</h1>
    <p class="muted">Prepariamo un PDF con l'impresa, le opportunità scelte, i requisiti e i documenti, più un link sicuro che scade tra 30 giorni.</p>
    <section class="card">${utili.map(r => `<label class="pick"><input type="checkbox" value="${esc(r.id)}" ${r.etichetta === "conviene" ? "checked" : ""}>
      <span><b>${esc(r.nome)}</b><br><span class="small muted">${LAB[r.etichetta]} · ${esc(r.bando.etichetta)}</span></span></label>`).join("")}</section>
    <div id="out"></div>
    <div class="sticky"><button class="btn block" id="crea">Crea PDF e link sicuro</button></div>`;
  document.getElementById("crea").addEventListener("click", async ev => {
    const misure = [...$app.querySelectorAll(".pick input:checked")].map(i => i.value);
    const out = document.getElementById("out");
    if (!misure.length) { out.innerHTML = `<div class="err">Scegli almeno un'opportunità.</div>`; return; }
    ev.target.disabled = true;
    try {
      const r = await api(`/api/impresa/${id}/inoltra`, { method: "POST", body: { misure } });
      const mail = `mailto:?subject=${encodeURIComponent(r.oggetto)}&body=${encodeURIComponent(r.testo)}`;
      out.innerHTML = `<section class="card"><p><b>Pronto.</b> Link valido fino al ${esc(r.scade)}.</p>
        <p><a class="btn block" href="${esc(mail)}">✉️ Invia per email</a></p>
        <p><a class="btn sec block" href="${esc(r.pdf)}" target="_blank" rel="noopener">📄 Apri il PDF</a></p>
        <p class="small"><input type="text" readonly value="${esc(r.link)}" aria-label="Link sicuro" style="font-size:14px"></p>
        <p class="small muted">Chi apre il link vede solo queste opportunità e i documenti caricati. Ogni accesso viene registrato.</p></section>`;
    } catch (e) { out.innerHTML = `<div class="err">${esc(e.message)}</div>`; ev.target.disabled = false; }
  });
}

/* ------------------------------------------------------------- bandi lampo */
async function lampo() {
  nav("lampo");
  busy("Carico lo storico…");
  let d;
  try { d = await api("/api/lampo"); } catch (e) { $app.innerHTML = ""; fail(e); return; }
  const veloci = d.bandi.filter(b => b.lampo), altri = d.bandi.filter(b => !b.lampo);
  const item = b => `<li class="${b.lampo ? "" : "slow"}">
    <p style="margin:0"><span class="days">${b.giorni_aperto != null ? (b.giorni_aperto <= 1 ? (b.giorni_aperto === 0 ? "poche ore" : "1 giorno") : b.giorni_aperto + " giorni") : "—"}</span> <span class="muted small">${b.giorni_aperto != null ? "aperto" : "chiusura non confermata"}</span></p>
    <b>${esc(b.nome)}</b><br><span class="small muted">${esc(b.ente)}</span><br>
    <span class="small">Aperto ${dIt(b.apertura)} · chiuso ${dIt(b.chiusura_effettiva)} · ${esc(b.motivo_chiusura || "")}${b.dotazione_eur ? " · dotazione " + eur(b.dotazione_eur) : ""}</span>
    ${b.per_rivenditori_auto ? `<br><span class="small">Per i rivenditori auto: ${esc(b.per_rivenditori_auto)}</span>` : ""}
    ${b.prossima_prevista ? `<br><span class="prev">previsione</span> <span class="small">${esc(String(b.prossima_prevista).replace(/^PREVISIONE:\s*/i, ""))}</span>` : ""}
    <br><a class="small" href="${esc(b.fonte_url)}" target="_blank" rel="noopener">Fonte</a></li>`;
  $app.innerHTML = `
    <h1>Bandi lampo</h1>
    <p><b>Questi li hanno presi gli altri. Con noi saresti stato pronto il primo giorno.</b></p>
    <p class="muted">Bandi lombardi chiusi in fretta per esaurimento dei fondi. Dati da fonti verificate, da ricontrollare prima del lancio.</p>
    <section class="card"><ul class="tl">${veloci.map(item).join("")}</ul></section>
    ${altri.length ? `<h2>Altri bandi dello storico</h2><section class="card"><ul class="tl">${altri.map(item).join("")}</ul></section>` : ""}`;
}

/* ------------------------------------------------------------- aggiornamenti */
async function aggiornamenti() {
  nav("aggiornamenti");
  busy("Carico il registro…");
  const imp = LS.get("impresa", null);
  let d;
  try { d = await api("/api/aggiornamenti" + (imp ? "?impresa=" + imp : "")); }
  catch (e) { try { d = await api("/api/aggiornamenti"); } catch (e2) { $app.innerHTML = ""; fail(e2); return; } }
  $app.innerHTML = `
    <h1>Aggiornamenti e avvisi</h1>
    <p class="muted">Ogni notte controlliamo stato e scadenze dei bandi e prepariamo gli avvisi. Nel prototipo la lettura delle fonti e l'invio di email/notifiche sono <b>simulati</b>.</p>
    <p><button class="btn sec" id="run">Esegui l'aggiornamento ora</button></p>
    ${d.avvisi.length ? `<h2>I tuoi avvisi</h2><section class="card"><ul class="log">${d.avvisi.map(a => `<li>${a.urgente ? "⏰ " : ""}<b>${esc(a.titolo)}</b><br>${esc(a.testo)}<br><span class="small muted">${esc(a.canale)} · ${new Date(a.creato).toLocaleString("it-IT")}</span></li>`).join("")}</ul></section>` : ""}
    <h2>Registro</h2>
    <section class="card"><ul class="log">${d.log.map(l => `<li><span class="lv ${esc(l.livello)}">${esc(l.livello)}</span>${esc(l.messaggio)}<br><span class="small muted">${new Date(l.quando).toLocaleString("it-IT")}</span></li>`).join("") || "<li>Nessun aggiornamento ancora.</li>"}</ul></section>`;
  document.getElementById("run").addEventListener("click", async ev => {
    ev.target.disabled = true;
    try { await api("/api/aggiorna", { method: "POST" }); aggiornamenti(); } catch (e) { fail(e); ev.target.disabled = false; }
  });
}

/* ------------------------------------------------------------- router */
function route() {
  const h = location.hash.replace(/^#\/?/, "");
  const p = h.split("/");
  window.scrollTo(0, 0);
  if (p[0] === "impresa" && p[1]) return impresa(p[1]);
  if (p[0] === "risultati" && p[1]) return risultati(p[1]);
  if (p[0] === "misura" && p[1] && p[2]) return misura(p[1], decodeURIComponent(p[2]));
  if (p[0] === "inoltra" && p[1]) return inoltra(p[1]);
  if (p[0] === "lampo") return lampo();
  if (p[0] === "aggiornamenti") return aggiornamenti();
  return home();
}
window.addEventListener("hashchange", route);
route();
