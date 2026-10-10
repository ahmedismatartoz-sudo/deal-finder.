/* Scovo — sito per commercianti, pensato per il telefono.
   Una pagina sola: Affari, scheda auto, Vendi, Ricambi, Profilo. Dati dalle API /api/... */
"use strict";

/* ---------- utilità ---------- */
const $app = document.getElementById("app");
const store = {
  get(k, d) { try { const v = localStorage.getItem("scovo." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("scovo." + k, JSON.stringify(v)); } catch (e) {} },
  del(k) { try { localStorage.removeItem("scovo." + k); } catch (e) {} }
};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const num = n => Math.round(n).toLocaleString("it-IT");
const eur = n => num(n) + " €";
const cap = t => t ? t.charAt(0).toUpperCase() + t.slice(1) : t;
const plateOk = t => /^[A-Z]{2}\d{3}[A-Z]{2}$/.test(t);
const plateFix = t => String(t || "").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 7);

const LOGO = (size, bg = "var(--tag)", fg = "var(--tag-ink)") => `<svg class="logo" width="${size}" height="${size}" viewBox="0 0 48 48" aria-hidden="true"><rect width="48" height="48" rx="13" fill="${bg}"/><circle cx="21" cy="21" r="10.5" fill="none" stroke="${fg}" stroke-width="4.2"/><path d="M29 29l8.5 8.5" stroke="${fg}" stroke-width="4.6" stroke-linecap="round"/><text x="21" y="26.2" text-anchor="middle" font-family="Barlow Semi Condensed, Arial, sans-serif" font-weight="800" font-size="15" fill="${fg}">€</text></svg>`;
const CAR_SVG = '<svg viewBox="0 0 72 40" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M6 28h60v-6l-8-3-8-9H24l-9 9-9 3z"/><circle cx="20" cy="30" r="5"/><circle cx="52" cy="30" r="5"/></svg>';
const ICON = {
  back: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>',
  next: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>',
  filter: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M3 6h18M6 12h12M10 18h4"/></svg>',
  refresh: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
  chat: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  phone: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/></svg>',
  camera: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7h3l2-3h8l2 3h3v13H3z"/><circle cx="12" cy="13" r="4"/></svg>'
};
const TABS = [["affari","Affari",'<path d="M3 12h4l3-8 4 16 3-8h4"/>'],
              ["vendi","Vendi",'<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.5"/>'],
              ["ricambi","Ricambi",'<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/>'],
              ["profilo","Profilo",'<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>']];
const BANDS = [
  {id:"tutti", label:"Tutti i prezzi", min:null, max:null},
  {id:"2", label:"Fino a 2.000 €", min:null, max:2000},
  {id:"5", label:"2.000–5.000 €", min:2000, max:5000},
  {id:"10", label:"5.000–10.000 €", min:5000, max:10000},
  {id:"20", label:"Oltre 10.000 €", min:10000, max:null}
];

/* ---------- stato ---------- */
const saved = store.get("filtri", {});
const S = {
  token: store.get("token", null), user: store.get("user", null),
  route: "affari", sel: null, sheet: null, tip: store.get("tip", true),
  brands: saved.brands || [], min: saved.min || "", max: saved.max || "", stato: saved.stato || "tutte",
  sconto: saved.sconto || 30, sort: saved.sort || "guadagno",
  cars: null, costs: 140, loading: false, loadErr: "", loadedAt: 0, offline: !navigator.onLine,
  detail: {}, scroll: {}, descOpen: false, fbOpen: false
};
const V = {targa:"", km:"", note:"", cambio:"", foto:[], stato:"form", err:"", res:null};
const R = {targa:"", pezzo:"", pezzi:[], stato:"form", err:"", res:null, aperti:{}};
const L = {mode:"login", err:"", busy:false, sent:false, email:"", req:{}};

const guest = () => !S.token && !!(S.metodi && S.metodi.aperto);
const canBrowse = () => !!S.token || guest();
function saveFilters(){ store.set("filtri", {brands:S.brands, min:S.min, max:S.max, stato:S.stato, sconto:S.sconto, sort:S.sort}); }

/* ---------- API ---------- */
async function api(path, opts = {}) {
  const headers = {"Content-Type": "application/json"};
  if (S.token) headers.Authorization = "Bearer " + S.token;
  let res;
  try {
    res = await fetch(path, {method: opts.method || "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined});
  } catch (e) {
    throw new Error(navigator.onLine ? "Il server non risponde, riprova tra poco" : "Sei senza connessione");
  }
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401 && S.token && !path.includes("/auth/")) { const m = (data && data.error) || "Accesso scaduto: entra di nuovo"; logout(m); throw new Error(m); }
  if (!res.ok) { const e = new Error((data && (data.error || data.errore)) || "Errore " + res.status); e.data = data; e.status = res.status; throw e; }
  return data;
}

let toastT;
function toast(msg){
  let el = document.getElementById("toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; el.setAttribute("role", "status"); document.body.appendChild(el); }
  el.textContent = msg; el.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => { el.hidden = true; }, 2600);
}

/* ---------- conti: rivendita al X% sotto mercato e guadagno da–a ---------- */
function numeri(c){
  const riv = Math.round(c.mercato * (1 - S.sconto / 100));
  const base = riv - c.prezzo - S.costs;
  return {riv, gain: base - c.rip_hi, gainMax: base - c.rip_lo};
}
function bandId(){
  const b = BANDS.find(b => String(b.min ?? "") === S.min && String(b.max ?? "") === S.max);
  return b ? b.id : null;
}
function filtered(over){
  const s = Object.assign({}, S, over || {});
  const min = s.min === "" ? 0 : Number(s.min), max = s.max === "" ? Infinity : Number(s.max);
  return (S.cars || []).map(c => Object.assign({}, c, numeri(c)))
    .filter(c => c.gainMax > 0)
    .filter(c => c.prezzo >= min && c.prezzo <= max)
    .filter(c => s.brands.length === 0 || s.brands.includes(c.marca))
    .filter(c => s.stato === "tutte" || (s.stato === "sane" ? c.rip_hi === 0 : c.rip_hi > 0))
    .sort((a,b) => s.sort === "prezzo" ? a.prezzo - b.prezzo : s.sort === "nuove" ? String(b.nuova).localeCompare(String(a.nuova)) : b.gainMax - a.gainMax);
}
function brandsAll(){ return [...new Set((S.cars || []).map(c => c.marca))].sort((a,b) => (a === "Altro") - (b === "Altro") || a.localeCompare(b, "it")); }
function brandCounts(){ const o = {}; filtered({brands: []}).forEach(c => o[c.marca] = (o[c.marca] || 0) + 1); return o; }
function nFiltri(){ return S.brands.length + (S.min !== "" || S.max !== "" ? 1 : 0) + (S.stato !== "tutte" ? 1 : 0); }
function statoTxt(c){ return c.rip_hi === 0 ? (c.danno === "sconosciuto" ? "Senza danni dichiarati" : "Sana") : c.danno === "leggero" ? "Danno leggero" : "Da sistemare"; }
function ripTxt(c){ return c.rip_hi ? num(c.rip_lo) + "–" + num(c.rip_hi) + " €" : "Nessuna"; }
function gainTxt(c){
  const lo = Math.max(c.gain, 0);
  return c.gainMax !== c.gain ? (c.gain < 0 ? "fino a +" + eur(c.gainMax) : "+" + num(lo) + "–" + num(c.gainMax) + " €") : "+" + eur(c.gain);
}
function meta(c){ return [c.anno, c.km ? num(c.km) + " km" : "km da chiedere", c.carb, c.zona].filter(Boolean).join(" · "); }
function img(src, alt, eager){ return src ? `<img src="${esc(src)}" alt="${esc(alt)}" ${eager ? 'fetchpriority="high"' : 'loading="lazy"'} decoding="async">` : ""; }

/* ---------- dati ---------- */
async function loadCars(force){
  if (S.loading) return;
  if (!force && S.cars && Date.now() - S.loadedAt < 5 * 60 * 1000) return;
  S.loading = true; S.loadErr = ""; if (S.route === "affari") render({keep:true});
  try {
    const d = await api("/api/affari");
    S.cars = d.items || []; S.loadedAt = Date.now();
    if (d.costi_fissi) S.costs = Object.values(d.costi_fissi).reduce((a,b) => a + b, 0);
    const known = new Set(S.cars.map(c => c.marca)); S.brands = S.brands.filter(b => known.has(b));
  } catch (e) { S.loadErr = e.message; }
  S.loading = false;
  if (S.route === "affari") render({keep:true});
}
async function loadDetail(id){
  if (S.detail[id] && !S.detail[id].err) return;
  try { S.detail[id] = await api("/api/affari/" + id); }
  catch (e) { S.detail[id] = {err: e.message}; }
  if (S.route === "auto" && S.sel === id) render({keep:true});
}

/* ---------- viste: Affari ---------- */
function header(title, sub, right){
  return `<header class="top"><div class="top-row"><div class="brand">${LOGO(38)}<div style="display:flex;flex-direction:column;gap:1px;min-width:0"><span class="word">${title}</span><span class="sub" id="count">${sub}</span></div></div>${right || ""}</div>`;
}
function viewList(){
  if (!S.cars) {
    const sk = `<div class="skel" aria-hidden="true"><div class="photo"></div><div class="bar"></div><div class="bar s"></div><div class="bar"></div></div>`;
    return header("scovo", S.loadErr ? "Non riesco a caricare le auto" : "Carico le auto…") + `</header><main class="list">${S.loadErr
      ? `<div class="empty"><strong style="font:700 19px var(--disp)">${esc(S.loadErr)}</strong><button class="btn" data-act="reload">Riprova</button></div>`
      : sk + sk + sk}</main>`;
  }
  const list = filtered(), bid = bandId(), nf = nFiltri(), avail = brandCounts();
  const brandChips = brandsAll().filter(b => avail[b] || S.brands.includes(b)).map(b => `<button class="chip" data-act="brand" data-v="${esc(b)}" aria-pressed="${S.brands.includes(b)}">${esc(b)}</button>`).join("");
  const priceChips = BANDS.map(b => `<button class="chip" data-act="band" data-v="${b.id}" aria-pressed="${bid === b.id}">${b.label}</button>`).join("");
  const cards = list.map((c, i) => `
    <button class="card" data-act="open" data-v="${c.id}" aria-label="${esc(c.nome)}, guadagno ${gainTxt(c)}">
      <div class="photo">${CAR_SVG}${img(c.foto, c.nome, i < 2)}<span class="badge${c.verificare ? " warn" : ""}">${c.verificare ? "Da verificare" : statoTxt(c)}</span>${c.aperta ? `<span class="badge seen">Già contattata</span>` : ""}</div>
      <div class="card-body">
        <div><div class="name">${esc(c.nome)}</div><div class="meta">${esc(meta(c))}</div></div>
        <div class="trio">
          <div><span class="lbl">Prezzo</span><span class="val">${eur(c.prezzo)}</span></div>
          <div><span class="lbl">Riparazioni</span><span class="val ${c.rip_hi ? "small warn" : "good"}">${ripTxt(c)}</span></div>
          <div><span class="lbl">Rivendita</span><span class="val">${eur(c.riv)}</span></div>
        </div>
        <div class="gain"><span>Guadagno stimato</span><b>${gainTxt(c)}</b></div>
      </div>
    </button>`).join("");
  const empty = `<div class="empty"><strong style="font:700 19px var(--disp)">${S.cars.length ? "Nessuna auto con questi filtri" : "Nessuna auto in questo momento"}</strong>
      <span class="meta">${S.cars.length ? "Allarga il prezzo, togli qualche marca o abbassa lo sconto di rivendita." : "Cerchiamo nuove auto ogni 3 ore: riprova più tardi."}</span>
      ${S.cars.length ? `<button class="btn" data-act="reset">Togli tutti i filtri</button>` : `<button class="btn" data-act="reload">Aggiorna</button>`}</div>`;
  const right = `<div style="display:flex;gap:8px;align-items:center"><button class="refresh${S.loading ? " spinning" : ""}" data-act="reload" aria-label="Aggiorna l'elenco">${ICON.refresh}</button>
      <button class="chip more" data-act="filters" aria-label="Tutti i filtri${nf ? ", " + nf + " attivi" : ""}">${ICON.filter}Filtri${nf ? ` <span class="count-pill">${nf}</span>` : ""}</button></div>`;
  return header("scovo", `${list.length} auto da girare · rivendita −${S.sconto}%`, right) + `
    <div class="chips" role="group" aria-label="Prezzo">${priceChips}</div>
    <div class="chips" role="group" aria-label="Marca">${brandChips}</div>
  </header>
  <main class="list" aria-live="polite">${guest() ? `<div class="tip" style="font:600 16px/1.3 var(--body)"><span class="eur" aria-hidden="true">i</span><span>Anteprima libera: per contattare i venditori <a href="#/entra" style="color:inherit;font-weight:800">entra</a>.</span></div>` : ""}${S.offline ? `<div class="banner">Sei senza connessione: vedi le auto dell'ultimo aggiornamento.</div>` : ""}${S.loadErr ? `<div class="banner">${esc(S.loadErr)}</div>` : ""}${S.tip ? `<div class="tip"><span class="eur" aria-hidden="true">€</span><span><b>Tratta sempre:</b> più sconto, più guadagno.</span><button class="tip-x" data-act="tip" aria-label="Chiudi il consiglio">×</button></div>` : ""}${list.length ? cards : empty}</main>`;
}

/* ---------- viste: scheda auto ---------- */
const ESITI = [["contattato","Contattato"],["trattativa","In trattativa"],["comprata","Comprata"],["scartata","Non mi interessa"]];
function viewDetail(){
  const base = (S.cars || []).find(c => c.id === S.sel) || S.detail[S.sel];
  const d = S.detail[S.sel];
  if (!base || base.err) {
    return `<div class="detail"><header class="top"><div class="top-row"><button class="link" data-act="back">${ICON.back} Indietro</button></div></header><main class="list">${
      d && d.err ? `<div class="empty"><strong style="font:700 19px var(--disp)">${esc(d.err)}</strong><button class="btn" data-act="back">Torna alle auto</button></div>`
      : `<div class="skel"><div class="photo"></div><div class="bar"></div><div class="bar s"></div></div>`}</main></div>`;
  }
  const c = Object.assign({}, base, d && !d.err ? d : {}, numeri(base));
  const photos = (d && d.foto_tutte && d.foto_tutte.length) ? d.foto_tutte : (c.foto ? [c.foto] : []);
  const pezzi = (d && d.pezzi) || [];
  const desc = d ? (d.descrizione || "") : "";
  const longDesc = desc.length > 260 && !S.descOpen;
  const contact = d && d.tel ? {icon: ICON.phone, label: "Chiama il venditore", note: "Il venditore ha scritto il numero nell'annuncio"}
                             : {icon: ICON.chat, label: "Parla col venditore", note: `Si apre l'annuncio su ${esc(c.fonte || "Subito")} per scrivergli`};
  return `
  <div class="detail">
    <div class="gallery">
      <div class="slides" id="slides" aria-label="Foto">${photos.length ? photos.map((p, i) => `<div class="slide">${CAR_SVG}${img(p, "Foto " + (i + 1) + " di " + photos.length, i === 0)}</div>`).join("") : `<div class="slide">${CAR_SVG}<span>Nessuna foto</span></div>`}</div>
      <button class="round left" data-act="back" aria-label="Torna all'elenco">${ICON.back}</button>
      ${photos.length > 1 ? `<div class="dots" id="dots">${photos.map((_, i) => `<i class="${i ? "" : "on"}"></i>`).join("")}</div>` : ""}
    </div>
    <div class="d-body">
      <div><h1 class="d-name">${esc(c.nome)}</h1><div class="meta" style="margin-top:4px">${esc(meta(c))}</div></div>
      ${c.verificare ? `<div class="banner">Da verificare: chiedi al venditore i dati che mancano prima di andare a vederla.</div>` : ""}
      ${d ? (desc ? `<p class="desc" style="white-space:pre-line">${esc(longDesc ? desc.slice(0, 240).replace(/\s+\S*$/, "") + "…" : desc)}</p>${longDesc ? `<button class="more-desc" data-act="desc">Leggi tutto</button>` : ""}` : "") : `<div class="skel"><div class="bar"></div><div class="bar s"></div></div>`}
      <div class="box">
        <div class="row"><span class="k">Prezzo richiesto</span><span class="v">${eur(c.prezzo)}</span></div>
        <div class="row"><span class="k">Riparazioni (solo pezzi)</span><span class="v ${c.rip_hi ? "warn" : ""}">${ripTxt(c)}</span></div>
        <div class="row" style="border-bottom:0"><span class="k">Rivendita veloce</span><span class="v">${eur(c.riv)}</span></div>
        <div class="note">Il ${S.sconto}% sotto la media del mercato (${eur(c.mercato)}): un prezzo che fa vendere in fretta.</div>
        <div class="row total"><span class="k">Guadagno stimato</span><span class="v">${gainTxt(c)}</span></div>
        <div class="note" style="border:0;margin-top:-8px">Già tolti passaggio (90 €) e pulizia (50 €).${c.rip_hi ? " Più spendi in riparazioni, meno guadagni." : ""}</div>
      </div>
      <div class="tip"><span class="eur" aria-hidden="true">€</span><span><b>Tratta sempre:</b> più sconto, più guadagno.</span></div>
      ${pezzi.length ? `<button class="parts-btn" data-act="parts"><span>Cosa comprare per sistemarla · ${pezzi.length} ${pezzi.length === 1 ? "pezzo" : "pezzi"}</span>${ICON.next}</button>`
          : c.rip_hi ? "" : `<div class="ok-box">Nessuna riparazione dichiarata nell'annuncio</div>`}
      ${d && d.controlli && d.controlli.length ? `<section class="d-sec"><h2>Da controllare</h2><ul class="checks">${d.controlli.map(x => `<li>${esc(x)}</li>`).join("")}</ul></section>` : ""}
      ${c.aperta ? `<section class="box fb" style="padding:14px 16px"><h2>Com'è andata?</h2><div class="wrap">${ESITI.map(([v,l]) => `<button class="pill" data-act="esito" data-v="${v}" aria-pressed="${S.esito && S.esito[c.id] === v}">${l}</button>`).join("")}</div><span class="hint">Ci aiuta a trovarti auto migliori.</span></section>` : ""}
      <span class="where">Annuncio di un ${d && d.privato === false ? "venditore" : "privato"} su ${esc(c.fonte || "")} · ${esc(c.zona)}</span>
    </div>
    <div class="dock"><div class="dock-in">
      <button class="talk" id="talk" data-act="talk" ${d ? "" : 'aria-disabled="true"'}>${contact.icon}${contact.label}</button>
      <span class="talk-note">${contact.note}</span>
    </div></div>
  </div>`;
}

/* ---------- fogli dal basso ---------- */
function sheetFilters(){
  const bid = bandId(), n = filtered().length, counts = brandCounts();
  const seg = (items, cur, act, cols) => `<div class="seg" style="grid-template-columns:repeat(${cols},minmax(0,1fr))">${items.map(([v,l]) => `<button data-act="${act}" data-v="${v}" aria-pressed="${String(cur) === String(v)}">${l}</button>`).join("")}</div>`;
  const badRange = S.min !== "" && S.max !== "" && Number(S.min) > Number(S.max);
  return `
  <div class="sheet-wrap" data-act="close-bg"><div class="sheet" role="dialog" aria-modal="true" aria-labelledby="fh">
    <div class="sheet-head"><h2 id="fh">Filtri</h2><button class="link" data-act="reset">Azzera</button></div>
    <div class="sheet-body">
      <section class="sec"><h3>Prezzo</h3>
        <div class="two">
          <label class="field">Da (€)<input id="fmin" type="number" inputmode="numeric" min="0" step="100" placeholder="0" value="${esc(S.min)}"></label>
          <label class="field">A (€)<input id="fmax" type="number" inputmode="numeric" min="0" step="100" placeholder="20000" value="${esc(S.max)}"></label>
        </div>
        <span class="err" id="range-err" ${badRange ? "" : "hidden"}>Il prezzo "da" è più alto del prezzo "a".</span>
        <div class="wrap">${BANDS.map(b => `<button class="pill" data-act="band" data-v="${b.id}" aria-pressed="${bid === b.id}">${b.label}</button>`).join("")}</div>
      </section>
      <section class="sec"><h3>Marca</h3>
        <div class="wrap">${brandsAll().map(b => `<button class="pill" data-act="brand" data-v="${esc(b)}" aria-pressed="${S.brands.includes(b)}" ${!counts[b] && !S.brands.includes(b) ? "disabled" : ""}>${esc(b)}<span class="n">${counts[b] || 0}</span></button>`).join("")}</div>
      </section>
      <section class="sec"><h3>Stato</h3>${seg([["tutte","Tutte"],["sane","Sane"],["danni","Con danni"]], S.stato, "stato", 3)}</section>
      <section class="sec"><h3>Rivendi sotto il prezzo di mercato</h3>${seg([[10,"−10%"],[20,"−20%"],[30,"−30%"],[40,"−40%"]], S.sconto, "sconto", 4)}
        <span class="hint">Più sconto: vendi prima ma guadagni meno. I guadagni si ricalcolano subito.</span></section>
      <section class="sec"><h3>Ordine</h3>${seg([["guadagno","Più guadagno"],["prezzo","Più economiche"],["nuove","Più nuove"]], S.sort, "sort", 3)}</section>
    </div>
    <div class="sheet-foot"><button class="btn btn-dark" style="width:100%" data-act="close" id="show">${n ? `Mostra ${n} auto` : "Nessuna auto: cambia i filtri"}</button></div>
  </div></div>`;
}
function sheetParts(){
  const d = S.detail[S.sel] || {}, base = (S.cars || []).find(c => c.id === S.sel) || d;
  return `
  <div class="sheet-wrap" data-act="close-bg"><div class="sheet" role="dialog" aria-modal="true" aria-labelledby="ph">
    <div class="sheet-head"><h2 id="ph">Cosa comprare</h2><button class="link" data-act="close">Chiudi</button></div>
    <div class="sheet-body" style="gap:0">
      <span class="hint" style="padding-bottom:6px">${esc(base.nome || "")}</span>
      ${(d.pezzi || []).map(it => `<div class="part"><span>${esc(it.pezzo)}${it.link ? `<br><a href="${esc(it.link)}" target="_blank" rel="noopener noreferrer" style="font-size:13px;font-weight:600">Vedi l'offerta più bassa</a>` : ""}</span><b>${it.da === it.a ? eur(it.a) : num(it.da) + "–" + eur(it.a)}</b></div>`).join("")}
      <div class="part-total"><strong>Totale pezzi</strong><b>${ripTxt(base)}</b></div>
      <span class="hint" style="padding:10px 0 4px">Prezzi trovati online per questo modello (pezzi compatibili o usati), manodopera esclusa. Dove c'è scritto "prezzo stimato" non abbiamo trovato un'offerta: chiedi conferma al venditore.</span>
      <button class="btn" data-act="cerca-pezzi" style="margin:8px 0 4px">Cerca i prezzi migliori in Ricambi</button>
    </div>
  </div></div>`;
}
function sheetComprata(){
  return `
  <div class="sheet-wrap" data-act="close-bg"><div class="sheet" role="dialog" aria-modal="true" aria-labelledby="ch">
    <div class="sheet-head"><h2 id="ch">Comprata!</h2><button class="link" data-act="close">Chiudi</button></div>
    <div class="sheet-body">
      <label class="field">Quanto l'hai pagata? (facoltativo)<input id="paid" type="number" inputmode="numeric" placeholder="es. 3200"></label>
      <span class="hint">Il prezzo vero ci aiuta a stimare meglio le prossime auto. Non lo vede nessun altro.</span>
    </div>
    <div class="sheet-foot"><button class="btn btn-dark" style="width:100%" data-act="comprata-ok">Salva</button></div>
  </div></div>`;
}

/* ---------- Vendi ---------- */
function svcHead(title, sub){ return header(title, esc(sub)) + `</header>`; }
function viewVendi(){
  if (V.stato === "loading") return svcHead("Vendi", "Sto preparando il tuo annuncio…") + `<main class="list"><div class="empty"><div class="spin" aria-hidden="true"></div><strong>Cerco la targa e calcolo il prezzo</strong><span class="meta">Guardo le auto simili in vendita e scrivo titolo e descrizione. Ci vuole meno di un minuto.</span></div></main>`;
  if (V.stato === "result" && V.res) {
    const a = V.res.auto || {}, pr = V.res.prezzi || {}, t = V.res.testo || {};
    const nomeAuto = [cap(String(a.marca || "").toLowerCase()), a.modello, a.versione].filter(Boolean).join(" ");
    return svcHead("Vendi", nomeAuto + (a.anno ? " · " + a.anno : "")) + `<main class="list">
      <section class="box" style="padding:14px 16px"><div class="lbl">Auto trovata dalla targa ${esc(V.targa)}</div>
        <div class="name" style="margin-top:4px">${esc(nomeAuto)}</div><div class="meta">${esc([a.anno, a.km ? num(a.km) + " km" : null, a.carburante, a.potenza_kw ? a.potenza_kw + " kW" : null, a.cambio].filter(Boolean).join(" · "))}</div></section>
      ${pr.ok ? `<section class="prices">
        <div class="pc"><span class="lbl">Vendi in fretta</span><b>${eur(pr.veloce)}</b><span class="meta">per vendere subito</span></div>
        <div class="pc best"><span class="lbl">Prezzo giusto</span><b>${eur(pr.giusto)}</b><span class="meta">consigliato</span></div>
        <div class="pc"><span class="lbl">Prezzo alto</span><b>${eur(pr.alto)}</b><span class="meta">più tempo e trattative</span></div>
      </section>
      <span class="hint" style="padding:0 4px">Calcolato su ${esc(pr.fonte || "auto simili")}${pr.giorni_vendita ? `. Di solito si vende in circa ${pr.giorni_vendita} giorni` : ""}.</span>`
      : `<div class="banner">${esc(pr.motivo || "Non riesco a stimare il prezzo per questa auto")}</div>`}
      <section class="box copybox"><div class="copyhead"><span class="lbl">Titolo</span><button class="link" data-act="copy" data-v="t">Copia</button></div><p class="ctext" id="ct-t">${esc(t.titolo || "")}</p></section>
      <section class="box copybox"><div class="copyhead"><span class="lbl">Descrizione</span><button class="link" data-act="copy" data-v="d">Copia</button></div><p class="ctext" id="ct-d" style="white-space:pre-line">${esc(t.descrizione || "")}</p></section>
      ${(t.da_dichiarare || []).length ? `<section class="box copybox"><span class="lbl">Da scrivere per correttezza</span><ul class="checks" style="margin-top:6px">${t.da_dichiarare.map(x => `<li>${esc(x)}</li>`).join("")}</ul></section>` : ""}
      <button class="btn" data-act="vendi-new" style="margin-top:4px">Nuova auto</button>
    </main>`;
  }
  return svcHead("Vendi", "Targa e foto: al resto pensiamo noi") + `<main class="list">
    <section class="box form">
      <label class="field">Targa<input id="v-targa" class="plate" autocomplete="off" autocapitalize="characters" autocorrect="off" spellcheck="false" placeholder="AB123CD" value="${esc(V.targa)}" maxlength="9"></label>
      <label class="field">Chilometri<input id="v-km" type="text" inputmode="numeric" placeholder="es. 98000" value="${esc(V.km)}"></label>
      <div class="field">Cambio
        <div class="seg" style="grid-template-columns:repeat(2,minmax(0,1fr))">${[["manuale","Manuale"],["automatico","Automatico"]].map(([v,l]) => `<button data-act="cambio" data-v="${v}" aria-pressed="${V.cambio === v}">${l}</button>`).join("")}</div></div>
      <div class="field">Foto (fino a 6)
        <label class="photo-add" for="v-foto">${ICON.camera}Aggiungi foto</label>
        <input id="v-foto" type="file" accept="image/*" multiple hidden>
        ${V.foto.length ? `<div class="thumbs">${V.foto.map((f,i) => `<div class="th"><img src="${f}" alt="Foto ${i+1}"><button data-act="foto-x" data-v="${i}" aria-label="Togli foto ${i+1}">×</button></div>`).join("")}</div>` : ""}
      </div>
      <label class="field">Note (facoltative)<input id="v-note" placeholder="es. tagliandi ufficiali, gomme nuove" value="${esc(V.note)}" maxlength="400"></label>
      ${V.err ? `<span class="err" role="alert">${esc(V.err)}</span>` : ""}
      <button class="btn btn-dark" data-act="vendi-go">Prepara l'annuncio</button>
    </section>
    <span class="hint" style="padding:0 4px">Ti diamo l'auto esatta, il prezzo giusto per venderla e titolo e descrizione pronti da copiare su Subito o Facebook.</span>
  </main>`;
}

/* ---------- Ricambi ---------- */
function viewRicambi(){
  if (R.stato === "loading") return svcHead("Ricambi", "Cerco i prezzi migliori…") + `<main class="list"><div class="empty"><div class="spin" aria-hidden="true"></div><strong>Confronto negozi, eBay e usato</strong><span class="meta">Solo offerte compatibili con la tua auto. Può volerci un minuto per pezzo.</span></div></main>`;
  if (R.stato === "result" && R.res) {
    const res = R.res;
    return svcHead("Ricambi", res.auto || "") + `<main class="list">
      <section class="box sum"><div><span class="lbl">Spendi</span><b>${eur(res.totale_migliori || 0)}</b></div><div><span class="lbl">Risparmi</span><b class="good">${eur(res.risparmio_totale || 0)}</b></div></section>
      ${(res.pezzi || []).map((p, i) => {
        const o = p.offerte || [], best = p.migliore;
        return `<section class="box part-card">
        <div class="pname">${esc(cap(p.pezzo))}</div>
        ${best ? `<div class="offer best"><div><span class="otype">${esc(best.tipo)} · più economico</span><span class="meta">${esc(best.venditore || "")} · ${best.spedizione ? "spedizione " + eur(best.spedizione) : "spedizione gratis o da verificare"}</span></div><b>${eur(best.totale)}</b></div>
        <a class="btn btn-dark go" href="${esc(best.link)}" target="_blank" rel="noopener noreferrer">Vai all'offerta</a>
        ${o.length > 1 ? `<button class="link" data-act="altre" data-v="${i}">${R.aperti[i] ? "Nascondi" : "Altre " + (o.length - 1) + " offerte"}</button>` : ""}
        ${R.aperti[i] ? o.filter(x => x.link !== best.link).map(x => `<a class="offer" href="${esc(x.link)}" target="_blank" rel="noopener noreferrer" style="text-decoration:none;color:inherit"><div><span class="otype">${esc(x.tipo)}</span><span class="meta">${esc(x.venditore || "")}</span></div><b>${eur(x.totale)}</b></a>`).join("") : ""}`
        : `<span class="meta">Nessuna offerta sicura trovata per questo pezzo. Prova a scriverlo in modo diverso.</span>`}
      </section>`; }).join("")}
      <span class="hint" style="padding:0 4px">Prezzi trovati ora sui siti dei venditori: controlla sempre la compatibilità con il telaio prima di comprare.</span>
      <button class="btn" data-act="ricambi-new">Nuova ricerca</button>
    </main>`;
  }
  return svcHead("Ricambi", "Targa e pezzi: troviamo il prezzo più basso") + `<main class="list">
    <section class="box form">
      <label class="field">Targa<input id="r-targa" class="plate" autocomplete="off" autocapitalize="characters" autocorrect="off" spellcheck="false" placeholder="AB123CD" value="${esc(R.targa)}" maxlength="9"></label>
      <label class="field">Che pezzo ti serve?
        <div class="addrow"><input id="r-pezzo" placeholder="es. faro anteriore sinistro" value="${esc(R.pezzo)}" maxlength="80" enterkeyhint="done"><button class="btn btn-dark" data-act="pezzo-add" aria-label="Aggiungi il pezzo">+</button></div></label>
      <div class="wrap">${["Faro anteriore","Paraurti anteriore","Kit frizione","Kit distribuzione","Specchietto"].map(p => `<button class="pill" data-act="pezzo-quick" data-v="${p}">+ ${p}</button>`).join("")}</div>
      ${R.pezzi.length ? `<div class="wrap">${R.pezzi.map((p,i) => `<span class="pill on">${esc(p)}<button data-act="pezzo-x" data-v="${i}" aria-label="Togli ${esc(p)}">×</button></span>`).join("")}</div>` : ""}
      ${R.err ? `<span class="err" role="alert">${esc(R.err)}</span>` : ""}
      <button class="btn btn-dark" data-act="ricambi-go">Trova i prezzi più bassi</button>
    </section>
    <span class="hint" style="padding:0 4px">Cerchiamo nei negozi online, su eBay e tra l'usato, solo pezzi compatibili con la tua auto. Massimo 8 pezzi per ricerca.</span>
  </main>`;
}

/* ---------- Profilo ---------- */
const P = {open:false, err:"", ok:false, busy:false};
function viewProfilo(){
  const u = S.user || {};
  return svcHead("Profilo", u.email || "") + `<main class="list">
    <div class="sec-title">Account</div>
    <div class="plist"><div><span>Nome</span><span class="v">${esc(u.name || "")}</span></div><div><span>Email</span><span class="v">${esc(u.email || "")}</span></div>
      <button data-act="pwd"><span>Cambia password</span>${ICON.next}</button></div>
    ${P.open ? `<section class="box form" style="padding:16px">
      <label class="field">Password attuale<input id="p-old" type="password" autocomplete="current-password"></label>
      <label class="field">Nuova password (almeno 8 caratteri)<input id="p-new" type="password" autocomplete="new-password" minlength="8"></label>
      ${P.err ? `<span class="err" role="alert">${esc(P.err)}</span>` : ""}${P.ok ? `<div class="ok-msg">Password cambiata</div>` : ""}
      <button class="btn btn-dark" data-act="pwd-go" ${P.busy ? "disabled" : ""}>Salva la nuova password</button></section>` : ""}
    <div class="sec-title">Come calcoliamo</div>
    <div class="plist"><div><span>Rivendita</span><span class="v">${S.sconto}% sotto mercato</span></div><div><span>Passaggio + pulizia</span><span class="v">${eur(S.costs)}</span></div><div><span>Riparazioni</span><span class="v">solo pezzi</span></div></div>
    <span class="hint" style="padding:0 4px">Lo sconto di rivendita si cambia dai Filtri. Ogni auto sparisce dopo che 7 commercianti l'hanno contattata.</span>
    ${installHint()}
    <div class="sec-title">Scovo</div>
    <div class="plist">${u.role === "admin" ? `<a href="/gestione"><span>Gestione (amministratore)</span>${ICON.next}</a>` : ""}<a href="/chi-siamo"><span>Chi siamo</span>${ICON.next}</a><a href="/privacy"><span>Privacy</span>${ICON.next}</a><a href="/condizioni"><span>Condizioni d'uso</span>${ICON.next}</a>
      <button data-act="logout"><span class="danger">Esci</span></button></div>
  </main>`;
}
let installEvt = null;
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); installEvt = e; if (S.route === "profilo") render({keep:true}); });
function standalone(){ return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true; }
function installHint(){
  if (standalone()) return "";
  if (installEvt) return `<button class="btn btn-blue" data-act="install">Metti Scovo sulla schermata Home</button>`;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  return `<div class="tip" style="font:500 15px/1.4 var(--body)"><span class="eur" aria-hidden="true">+</span><span><b>Scovo come un'app:</b> ${ios ? "tocca Condividi e poi “Aggiungi alla schermata Home”." : "dal menu del browser scegli “Installa app” o “Aggiungi a schermata Home”."}</span></div>`;
}

/* ---------- entra e chiedi di provare (la vetrina è nella pagina, già pronta) ---------- */
const ESITI_ACCESSO = {
  "attesa": ["info", "Richiesta ricevuta. Attiviamo il tuo account entro un giorno lavorativo e ti scriviamo per email."],
  "in-attesa": ["info", "Il tuo account è in attesa di attivazione: ti scriviamo appena è pronto."],
  "disattivato": ["bad", "Questo account non è attivo. Scrivici se pensi sia un errore."],
  "annullato": ["info", "Accesso annullato. Puoi riprovare quando vuoi."],
  "scaduto": ["bad", "La pagina di accesso è rimasta aperta troppo a lungo. Riprova."],
  "errore": ["bad", "Non siamo riusciti a farti entrare. Riprova o usa email e password."],
  "non-attivo": ["bad", "Questo modo di entrare non è ancora attivo. Usa email e password."]
};
const GOOGLE_SVG = '<svg width="20" height="20" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>';
const APPLE_SVG = '<svg width="18" height="20" viewBox="0 0 17 20" aria-hidden="true"><path fill="currentColor" d="M14.1 10.6c0-2.6 2.1-3.8 2.2-3.9-1.2-1.8-3.1-2-3.7-2-1.6-.2-3.1.9-3.9.9-.8 0-2-.9-3.4-.9-1.7 0-3.3 1-4.2 2.6-1.8 3.1-.5 7.7 1.3 10.2.9 1.2 1.9 2.6 3.2 2.6 1.3-.1 1.8-.8 3.3-.8 1.6 0 2 .8 3.4.8 1.4 0 2.3-1.3 3.1-2.5 1-1.4 1.4-2.8 1.4-2.9-.1 0-2.7-1-2.7-4.1zM11.6 3c.7-.9 1.2-2 1-3.2-1 .1-2.3.7-3 1.6-.7.8-1.3 2-1.1 3.1 1.2.1 2.3-.6 3.1-1.5z"/></svg>';
function socialButtons(){
  const m = S.metodi || {};
  if (!m.google && !m.apple) return "";
  return `<div class="auth-social">
      ${m.google ? `<a class="l-btn l-btn-google" href="/auth/google">${GOOGLE_SVG}Continua con Google</a>` : ""}
      ${m.apple ? `<a class="l-btn l-btn-apple" href="/auth/apple">${APPLE_SVG}Continua con Apple</a>` : ""}
    </div><div class="auth-or">oppure con email</div>`;
}
function viewLogin(){
  const req = S.route === "prova";
  const esito = ESITI_ACCESSO[S.esitoAccesso || ""];
  const top = `<div class="auth-top"><a class="l-brand" href="#/">${LOGO(34)}<span>scovo</span></a><a class="l-link" href="#/">Torna alla pagina iniziale</a></div>`;
  if (req) return `<div class="auth">${top}
    <div><h1>Prova Scovo</h1><p class="l-sub">Lasciaci i tuoi dati: ti attiviamo l'accesso e ti spieghiamo come funziona.</p></div>
    ${L.sent ? `<div class="note-box info">Richiesta inviata. Ti contattiamo entro un giorno lavorativo.</div><a class="l-btn l-btn-blue" href="#/">Torna alla pagina iniziale</a>` : `
    ${socialButtons().replace("oppure con email", "oppure lasciaci i tuoi dati")}
    <form id="req-form" novalidate>
      <label class="field">Nome e cognome<input name="nome" autocomplete="name" required value="${esc(L.req.nome || "")}"></label>
      <label class="field">Autosalone<input name="azienda" autocomplete="organization" value="${esc(L.req.azienda || "")}"></label>
      <label class="field">Email<input name="email" type="email" autocomplete="email" required value="${esc(L.req.email || L.email)}"></label>
      <label class="field">Telefono<input name="telefono" type="tel" autocomplete="tel" value="${esc(L.req.telefono || "")}"></label>
      <label class="hp" aria-hidden="true">Sito<input name="sito" tabindex="-1" autocomplete="off"></label>
      ${L.err ? `<span class="err" role="alert">${esc(L.err)}</span>` : ""}
      <button class="l-btn l-btn-blue" type="submit" ${L.busy ? "disabled" : ""}>${L.busy ? "Invio…" : "Chiedi di provare"}</button>
      <span class="hint">Inviando accetti la <a href="/privacy">privacy</a>.</span>
    </form>`}
    <a class="l-link" href="#/entra">Hai già un account? Entra</a>
  </div>`;
  return `<div class="auth">${top}
    <div><h1>Entra in Scovo</h1><p class="l-sub">Le auto dei privati sotto prezzo, con il guadagno già calcolato.</p></div>
    ${esito ? `<div class="note-box ${esito[0]}" role="status">${esc(esito[1])}</div>` : ""}
    ${socialButtons()}
    <form id="login-form" novalidate>
      <label class="field">Email<input name="email" type="email" autocomplete="username" inputmode="email" required value="${esc(L.email)}"></label>
      <label class="field">Password<input name="password" type="password" autocomplete="current-password" required></label>
      ${L.err ? `<span class="err" role="alert">${esc(L.err)}</span>` : ""}
      <button class="l-btn l-btn-blue" type="submit" ${L.busy ? "disabled" : ""}>${L.busy ? "Entro…" : "Entra"}</button>
    </form>
    <a class="l-link" href="#/prova">Non hai un account? Chiedi di provare Scovo</a>
    <nav class="foot" aria-label="Informazioni"><a href="/chi-siamo">Chi siamo</a><a href="/privacy">Privacy</a><a href="/condizioni">Condizioni d'uso</a></nav>
  </div>`;
}
async function onSubmit(e){
  const f = e.target; if (!f.id) return;
  e.preventDefault();
  if (f.id === "bot-form") { const i = document.getElementById("bot-text"); botAsk(i ? i.value : A.text); return; }
  const data = Object.fromEntries(new FormData(f).entries());
  if (data.email) L.email = data.email;
  if (f.id === "req-form") L.req = data;
  if (f.id === "login-form") {
    if (!data.email || !data.password) { L.err = "Scrivi email e password."; return render(); }
    L.busy = true; L.err = ""; render();
    try {
      const r = await api("/api/auth/login", {method: "POST", body: {email: data.email, password: data.password}});
      L.busy = false; loggedIn(r);
    } catch (err) { L.busy = false; L.err = err.message; render(); }
  } else if (f.id === "req-form") {
    if (!data.email || !data.nome) { L.err = "Scrivi almeno nome ed email."; return render(); }
    L.busy = true; L.err = ""; render();
    try { await api("/api/richiesta-accesso", {method: "POST", body: data}); L.sent = true; }
    catch (err) { L.err = err.message; }
    L.busy = false; render();
  }
}
function loggedIn(r){
  S.token = r.token; S.user = r.user; store.set("token", r.token); store.set("user", r.user);
  S.scroll = {}; go("affari", true); loadCars(true); window.scrollTo(0, 0);
}
async function redeemCode(code){
  try { loggedIn(await api("/api/auth/scambio", {method: "POST", body: {code}})); }
  catch (e) { S.esitoAccesso = "errore"; go("entra", true); }
}
function logout(silent){
  if (!silent && S.token) { fetch("/api/auth/esci", {method: "POST", headers: {Authorization: "Bearer " + S.token}}).catch(() => {}); }
  S.token = null; S.user = null; S.cars = null; S.detail = {}; store.del("token"); store.del("user");
  try { if (window.caches) caches.delete("scovo-dati"); } catch (e) {}
  L.err = typeof silent === "string" ? silent : silent ? "Accesso scaduto: entra di nuovo." : "";
  go(silent ? "entra" : "", true);
}

/* ---------- navigazione (il tasto indietro del telefono funziona) ---------- */
function parseHash(){
  const h = location.hash.replace(/^#\/?/, "");
  const m = h.match(/^auto\/(\d+)$/);
  if (m) return {route: "auto", sel: Number(m[1])};
  const a = h.match(/^accesso\/([\w-]+)$/);
  if (a) return {route: "accesso", code: a[1]};
  const e = h.match(/^entra(?:\/([\w-]+))?$/);
  if (e) return {route: "entra", esito: e[1] || ""};
  if (h === "prova") return {route: "prova"};
  if (!canBrowse()) return {route: "vetrina"};
  return {route: ["vendi","ricambi","profilo"].includes(h) ? h : "affari", sel: null};
}
function go(route, replace){
  const h = "#/" + route;
  if (replace) history.replaceState(null, "", h); else if (location.hash !== h) history.pushState(null, "", h);
  applyRoute();
}
function applyRoute(){
  const prev = S.route;
  S.scroll[prev === "auto" ? "auto" : prev] = window.scrollY;
  const r = parseHash();
  if (r.route === "accesso") { history.replaceState(null, "", "#/entra"); S.route = "entra"; render({top:true}); redeemCode(r.code); return; }
  if (S.token && ["entra","prova","vetrina"].includes(r.route)) { go("affari", true); return; }
  if (r.route === "entra") { S.esitoAccesso = r.esito; if (r.esito) history.replaceState(null, "", "#/entra"); }
  S.route = r.route; S.sel = r.sel; S.sheet = null; S.descOpen = false;
  if (S.route === "auto") { if (!canBrowse()) { go("entra", true); return; } loadDetail(S.sel); render({top:true}); }
  else if (S.route === "entra" || S.route === "prova") render({top:true});
  else render({y: S.scroll[S.route] || 0});
  if (S.route === "affari" && canBrowse()) loadCars();
}
window.addEventListener("popstate", () => { if (S.sheet) { S.sheet = null; } applyRoute(); });

/* ---------- disegno ---------- */
/* ---------- Assistente a voce ---------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const A = {msgs: [], listening: false, busy: false, voice: store.get("voce", true), rec: null, text: ""};
const BOT_IDEE = ["Che affari ci sono oggi sopra i 3.000 € di guadagno?", "Che Audi ci sono oggi?", "Auto sotto i 5.000 € senza danni",
                  "Quanto vale una Audi TT del 2014?", "Controlla la targa AB123CD e dimmi il prezzo di faro e paraurti"];
const MIC = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
function botFab(){ return `<button class="bot-fab" data-act="bot" aria-label="Chiedi a Scovo a voce">${MIC}<span>Chiedi</span></button>`; }
function sheetBot(){
  const msgs = A.msgs.map(m => m.role === "user" ? `<div class="b-msg me">${esc(m.content)}</div>` : `
    <div class="b-msg bot">${esc(m.content)}${(m.auto || []).length ? `<div class="b-cars">${m.auto.map(c => `
      <button class="b-car" data-act="bot-open" data-v="${c.id}">${c.foto ? `<img src="${esc(c.foto)}" alt="" loading="lazy">` : `<span class="b-noimg">${CAR_SVG}</span>`}
        <span class="b-car-t"><b>${esc(c.nome)}</b><span>${esc([c.anno, c.zona].filter(Boolean).join(" · "))}</span>
        <span>${eur(c.prezzo)} · <em>${c.guadagno_da === c.guadagno_a ? "+" + eur(c.guadagno_a) : "+" + num(Math.max(c.guadagno_da, 0)) + "–" + num(c.guadagno_a) + " €"}</em></span></span></button>`).join("")}</div>` : ""}</div>`).join("");
  const ideas = A.msgs.length ? "" : `<div class="b-ideas"><p class="hint">Chiedimi quello che vuoi, a voce o scrivendo. Per esempio:</p>${BOT_IDEE.map(t => `<button class="pill" data-act="bot-idea" data-v="${esc(t)}">${esc(t)}</button>`).join("")}</div>`;
  return `
  <div class="sheet-wrap" data-act="close-bg"><div class="sheet bot-sheet" role="dialog" aria-modal="true" aria-labelledby="bh">
    <div class="sheet-head"><h2 id="bh">Chiedi a Scovo</h2><div style="display:flex;gap:4px;align-items:center">
      <button class="link" data-act="bot-voice" aria-pressed="${A.voice}">${A.voice ? "Voce sì" : "Voce no"}</button><button class="link" data-act="close">Chiudi</button></div></div>
    <div class="sheet-body bot-body" id="bot-body" aria-live="polite">${ideas}${msgs}
      ${A.busy ? `<div class="b-msg bot b-wait"><span class="spin" aria-hidden="true"></span>Ci penso…</div>` : ""}
      <div class="b-live" id="bot-live" ${A.listening ? "" : "hidden"}>Ti ascolto…</div>
    </div>
    <form class="bot-foot" id="bot-form">
      <input id="bot-text" placeholder="${SR ? "Scrivi o tocca il microfono" : "Scrivi la tua domanda"}" autocomplete="off" value="${esc(A.text)}" enterkeyhint="send">
      ${SR ? `<button type="button" class="bot-mic${A.listening ? " on" : ""}" data-act="bot-mic" aria-label="${A.listening ? "Smetti di ascoltare" : "Parla"}">${MIC}</button>` : ""}
      <button type="submit" class="bot-send" aria-label="Invia"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>
    </form>
  </div></div>`;
}
function botScroll(){ const b = document.getElementById("bot-body"); if (b) b.scrollTop = b.scrollHeight; }
function speak(text){
  if (!A.voice || !window.speechSynthesis) return;
  try {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text); u.lang = "it-IT"; u.rate = 1.05;
    const v = speechSynthesis.getVoices().find(v => /^it/i.test(v.lang)); if (v) u.voice = v;
    speechSynthesis.speak(u);
  } catch (e) {}
}
async function botAsk(text){
  text = (text || "").trim(); if (!text || A.busy) return;
  A.msgs.push({role: "user", content: text}); A.text = ""; A.busy = true;
  render({keep:true}); botScroll();
  try {
    const r = await api("/api/assistente", {method: "POST", body: {messages: A.msgs.map(m => ({role: m.role, content: m.content}))}});
    A.msgs.push({role: "assistant", content: r.risposta || "", auto: r.auto || []});
    speak(r.risposta || "");
  } catch (e) { A.msgs.push({role: "assistant", content: e.message}); }
  A.busy = false;
  if (S.sheet === "bot") { render({keep:true}); botScroll(); }
}
function botListen(){
  if (!SR) return;
  if (A.listening && A.rec) { A.rec.stop(); return; }
  try { speechSynthesis && speechSynthesis.cancel(); } catch (e) {}
  const rec = new SR(); A.rec = rec;
  rec.lang = "it-IT"; rec.interimResults = true; rec.continuous = false;
  let finalText = "";
  rec.onresult = ev => {
    let interim = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      if (ev.results[i].isFinal) finalText += ev.results[i][0].transcript; else interim += ev.results[i][0].transcript;
    }
    const live = document.getElementById("bot-live"); if (live) { live.hidden = false; live.textContent = (finalText + " " + interim).trim() || "Ti ascolto…"; }
  };
  rec.onerror = ev => { if (ev.error === "not-allowed") toast("Permetti l'uso del microfono per parlare con Scovo"); };
  rec.onend = () => { A.listening = false; A.rec = null; if (finalText.trim()) botAsk(finalText); else render({keep:true}); };
  A.listening = true; render({keep:true}); botScroll();
  try { rec.start(); } catch (e) { A.listening = false; render({keep:true}); }
}

function tabBar(){
  const cur = S.route === "auto" ? "affari" : S.route;
  return `<nav class="tabs" aria-label="Sezioni">${TABS.map(([id,l,p]) => `<button data-act="tab" data-v="${id}" aria-current="${cur === id ? "page" : "false"}"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>${l}</button>`).join("")}</nav>`;
}
function render(opts){
  opts = opts || {};
  const y = window.scrollY;
  const active = document.activeElement && document.activeElement.id;
  const chipsX = [...document.querySelectorAll(".chips")].map(c => c.scrollLeft);
  const land = document.getElementById("landing-root");
  if (!canBrowse() && S.route === "vetrina") {
    if (land) land.hidden = false; $app.hidden = true; $app.innerHTML = "";
    document.title = "Scovo · le auto da girare, prima degli altri";
    if (opts.y != null) window.scrollTo(0, opts.y);
    return;
  }
  if (land) land.hidden = true; $app.hidden = false;
  let html;
  if (!S.token && (!guest() || S.route === "entra" || S.route === "prova")) { html = viewLogin(); document.title = (S.route === "prova" ? "Prova Scovo" : "Entra") + " · Scovo"; }
  else if (guest() && ["vendi","ricambi","profilo"].includes(S.route)) { html = viewGuestLock() + tabBar(); }
  else if (S.route === "auto") { html = viewDetail(); }
  else html = (S.route === "vendi" ? viewVendi() : S.route === "ricambi" ? viewRicambi() : S.route === "profilo" ? viewProfilo() : viewList()) + tabBar();
  if (canBrowse()) {
    if (S.route !== "auto" && !S.sheet) html += botFab();
    html += S.sheet === "filters" ? sheetFilters() : S.sheet === "parts" ? sheetParts() : S.sheet === "comprata" ? sheetComprata() : S.sheet === "bot" ? sheetBot() : "";
    const t = S.route === "auto" ? ((S.cars || []).find(c => c.id === S.sel) || {}).nome : ({affari:"Affari", vendi:"Vendi", ricambi:"Ricambi", profilo:"Profilo"})[S.route];
    document.title = (t ? t + " · " : "") + "Scovo";
  }
  $app.innerHTML = html;
  document.body.style.overflow = S.sheet ? "hidden" : "";
  if (active) { const el = document.getElementById(active); if (el && el.tagName === "INPUT") { el.focus({preventScroll:true}); try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) {} } }
  document.querySelectorAll(".chips").forEach((c, i) => { if (chipsX[i]) c.scrollLeft = chipsX[i]; });
  const slides = document.getElementById("slides");
  if (slides) slides.addEventListener("scroll", () => {
    const i = Math.round(slides.scrollLeft / slides.clientWidth);
    document.querySelectorAll("#dots i").forEach((d, k) => d.classList.toggle("on", k === i));
  }, {passive:true});
  if (opts.top) window.scrollTo(0, 0);
  else if (opts.y != null) window.scrollTo(0, opts.y);
  else if (opts.keep) window.scrollTo(0, y);
  else window.scrollTo(0, y);
  if (S.sheet && S.sheet !== "bot") { const sh = document.querySelector(".sheet"); if (sh && !sh.contains(document.activeElement)) { const f = sh.querySelector("h2"); if (f) { f.tabIndex = -1; f.focus({preventScroll:true}); } } }
}
/* foto che non si caricano: resta il disegno dell'auto (niente codice nell'HTML: CSP) */
document.addEventListener("error", e => { const t = e.target; if (t && t.tagName === "IMG") t.classList.add("ko"); }, true);

/* ---------- azioni ---------- */
function setBand(id){
  const b = BANDS.find(b => b.id === id);
  if (bandId() === id && id !== "tutti") { S.min = ""; S.max = ""; return; }
  S.min = b.min == null ? "" : String(b.min); S.max = b.max == null ? "" : String(b.max);
}
function addPezzo(p){ p = (p || "").trim(); if (p && !R.pezzi.some(x => x.toLowerCase() === p.toLowerCase()) && R.pezzi.length < 8) R.pezzi.push(p); R.pezzo = ""; R.err = ""; }
function selectText(el){ if (!el) return; const r = document.createRange(); r.selectNodeContents(el); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }
function shrinkPhoto(file){
  // foto ridotte sul telefono (lato lungo 1280 px): invio veloce anche con poco segnale
  return new Promise(resolve => {
    const url = URL.createObjectURL(file), im = new Image();
    im.onload = () => {
      const k = Math.min(1, 1280 / Math.max(im.width, im.height));
      const cv = document.createElement("canvas"); cv.width = Math.round(im.width * k); cv.height = Math.round(im.height * k);
      cv.getContext("2d").drawImage(im, 0, 0, cv.width, cv.height);
      URL.revokeObjectURL(url); resolve(cv.toDataURL("image/jpeg", 0.8));
    };
    im.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
    im.src = url;
  });
}
function viewGuestLock(){
  const t = ({vendi:"Vendi", ricambi:"Ricambi", profilo:"Profilo"})[S.route];
  return svcHead(t, "Serve un account") + `<main class="list"><div class="empty">
    <strong style="font:700 21px var(--disp)">Entra per usare ${t === "Profilo" ? "il tuo profilo" : t}</strong>
    <span class="meta">${t === "Vendi" ? "Dalla targa: auto esatta, prezzo giusto e annuncio pronto." : t === "Ricambi" ? "Dalla targa: i pezzi compatibili al prezzo più basso." : "Il tuo account, le auto che hai contattato e le impostazioni."}</span>
    <a class="l-btn l-btn-blue" href="#/entra" style="width:100%">Entra</a><a class="l-link" href="#/prova">Non hai un account? Chiedi di provare Scovo</a></div></main>`;
}
async function talk(){
  if (!S.token) { toast("Entra per contattare il venditore"); go("entra"); return; }
  const d = S.detail[S.sel]; if (!d || d.err) return;
  const btn = document.getElementById("talk");
  // la finestra si apre subito (dentro il tocco), altrimenti il telefono la blocca
  const w = d.tel ? null : window.open("", "_blank");
  if (btn) btn.setAttribute("aria-disabled", "true");
  try {
    const r = await api("/api/affari/" + S.sel + "/contatto", {method: "POST"});
    const car = (S.cars || []).find(c => c.id === S.sel); if (car) car.aperta = true; d.aperta = true;
    if (d.tel && r.tel) { location.href = "tel:" + r.tel; }
    else if (w) { try { w.opener = null; } catch (e) {} w.location.href = r.url; }
    else { window.location.href = r.url; }
  } catch (e) { if (w) w.close(); toast(e.message); }
  render({keep:true});
}
async function sendEsito(status, extra){
  const id = S.sel;
  try {
    await api("/api/affari/" + id + "/esito", {method: "POST", body: Object.assign({status}, extra || {})});
    S.esito = S.esito || {}; S.esito[id] = status;
    toast(status === "scartata" ? "Ok, non te la mostriamo più come nuova" : "Salvato, grazie");
  } catch (e) { toast(e.message); }
  render({keep:true});
}
async function vendiGo(){
  const t = plateFix(V.targa);
  if (!plateOk(t)) { V.err = "Scrivi la targa giusta, come AB123CD."; return render(); }
  if (!V.km) { V.err = "Scrivi i chilometri."; return render(); }
  V.targa = t; V.err = ""; V.stato = "loading"; render({top:true});
  try {
    V.res = await api("/api/servizi/vendi", {method: "POST", body: {targa: t, km: Number(V.km), note: V.note, cambio: V.cambio || null, foto: V.foto}});
    V.stato = "result";
  } catch (e) { V.stato = "form"; V.err = e.message; }
  if (S.route === "vendi") render({top:true});
}
async function ricambiGo(){
  const t = plateFix(R.targa);
  if (R.pezzo.trim()) addPezzo(R.pezzo);
  if (!plateOk(t)) { R.err = "Scrivi la targa giusta, come AB123CD."; return render(); }
  if (!R.pezzi.length) { R.err = "Aggiungi almeno un pezzo."; return render(); }
  R.targa = t; R.err = ""; R.stato = "loading"; R.aperti = {}; render({top:true});
  try { R.res = await api("/api/servizi/ricambi", {method: "POST", body: {targa: t, pezzi: R.pezzi}}); R.stato = "result"; }
  catch (e) { R.stato = "form"; R.err = e.message; }
  if (S.route === "ricambi") render({top:true});
}
async function pwdGo(){
  const o = document.getElementById("p-old").value, n = document.getElementById("p-new").value;
  if (n.length < 8) { P.err = "La nuova password deve avere almeno 8 caratteri."; P.ok = false; return render(); }
  P.busy = true; P.err = ""; render();
  try { await api("/api/me/password", {method: "PUT", body: {vecchia: o, nuova: n}}); P.ok = true; P.open = true; }
  catch (e) { P.err = e.message; P.ok = false; }
  P.busy = false; render();
}

$app.addEventListener("submit", onSubmit);
$app.addEventListener("click", e => {
  const t = e.target.closest("[data-act]"); if (!t) return;
  const act = t.dataset.act, v = t.dataset.v;
  if (act === "close-bg" && e.target !== t) return;
  switch (act) {
    case "tab": if (v === S.route) { window.scrollTo({top: 0, behavior: "smooth"}); return; } go(v); return;
    case "open": S.fromList = true; go("auto/" + v); return;
    case "back": if (S.fromList && S.route === "auto") { S.fromList = false; history.back(); } else go("affari", true); return;
    case "reload": loadCars(true); return;
    case "talk": talk(); return;
    case "desc": S.descOpen = true; break;
    case "brand": S.brands = S.brands.includes(v) ? S.brands.filter(x => x !== v) : S.brands.concat([v]); saveFilters(); break;
    case "band": setBand(v); saveFilters(); break;
    case "filters": S.sheet = "filters"; break;
    case "parts": S.sheet = "parts"; break;
    case "close": case "close-bg": if (A.rec) A.rec.abort(); try { speechSynthesis.cancel(); } catch (e) {} S.sheet = null; break;
    case "bot": S.sheet = "bot"; render({keep:true}); botScroll(); setTimeout(() => { const i = document.getElementById("bot-text"); if (i && !SR) i.focus(); }, 50); return;
    case "bot-idea": botAsk(v); return;
    case "bot-mic": botListen(); return;
    case "bot-voice": A.voice = !A.voice; store.set("voce", A.voice); if (!A.voice) { try { speechSynthesis.cancel(); } catch (e) {} } break;
    case "bot-open": S.sheet = null; S.fromList = true; go("auto/" + v); return;
    case "tip": S.tip = false; store.set("tip", false); break;
    case "reset": S.brands = []; S.min = ""; S.max = ""; S.stato = "tutte"; saveFilters(); break;
    case "stato": S.stato = v; saveFilters(); break;
    case "sconto": S.sconto = Number(v); saveFilters(); break;
    case "sort": S.sort = v; saveFilters(); break;
    case "esito": if (v === "comprata") { S.sheet = "comprata"; break; } sendEsito(v); return;
    case "comprata-ok": { const p = (document.getElementById("paid") || {}).value; S.sheet = null; sendEsito("comprata", p ? {bought_eur: Number(p)} : {}); return; }
    case "cerca-pezzi": { const d = S.detail[S.sel] || {}; R.pezzi = (d.pezzi || []).map(p => p.pezzo).slice(0, 8); R.stato = "form"; R.err = ""; S.sheet = null; go("ricambi"); return; }
    case "logout": logout(); return;
    case "install": if (installEvt) { installEvt.prompt(); installEvt = null; } break;
    case "pwd": P.open = !P.open; P.err = ""; P.ok = false; break;
    case "pwd-go": pwdGo(); return;
    case "vendi-go": vendiGo(); return;
    case "vendi-new": V.stato = "form"; V.targa = ""; V.km = ""; V.note = ""; V.cambio = ""; V.foto = []; V.res = null; render({top:true}); return;
    case "cambio": V.cambio = V.cambio === v ? "" : v; break;
    case "foto-x": V.foto.splice(Number(v), 1); break;
    case "copy": {
      const el = document.getElementById("ct-" + v), txt = el ? el.textContent : "";
      const done = () => { t.textContent = "Copiato"; setTimeout(() => { t.textContent = "Copia"; }, 1500); };
      try { navigator.clipboard.writeText(txt).then(done, () => selectText(el)); } catch (err) { selectText(el); }
      return;
    }
    case "pezzo-add": addPezzo(R.pezzo); break;
    case "pezzo-quick": addPezzo(v); break;
    case "pezzo-x": R.pezzi.splice(Number(v), 1); break;
    case "ricambi-go": ricambiGo(); return;
    case "altre": R.aperti[v] = !R.aperti[v]; break;
    case "ricambi-new": R.stato = "form"; R.pezzi = []; R.pezzo = ""; R.res = null; render({top:true}); return;
    default: return;
  }
  const sb = document.querySelector(".sheet-body"), sy = sb ? sb.scrollTop : 0;
  render({keep: !["filters","reset"].includes(act) || !!S.sheet});
  const sb2 = document.querySelector(".sheet-body"); if (sb2) sb2.scrollTop = sy;
});
$app.addEventListener("change", async e => {
  if (e.target.id !== "v-foto") return;
  const files = [...e.target.files].slice(0, 6 - V.foto.length);
  for (const f of files) { const d = await shrinkPhoto(f); if (d) V.foto.push(d); }
  render();
});
$app.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.id === "r-pezzo") { e.preventDefault(); addPezzo(R.pezzo); render(); }
  if (e.key === "Enter" && (e.target.id === "v-targa" || e.target.id === "v-km" || e.target.id === "v-note")) { e.preventDefault(); e.target.blur(); }
});
$app.addEventListener("input", e => {
  const id = e.target.id, val = e.target.value;
  if (id === "bot-text") { A.text = val; return; }
  if (id === "v-targa") V.targa = val; else if (id === "v-km") { V.km = val.replace(/\D/g, ""); if (val !== V.km) e.target.value = V.km; }
  else if (id === "v-note") V.note = val; else if (id === "r-targa") R.targa = val; else if (id === "r-pezzo") R.pezzo = val;
  else if (id === "fmin" || id === "fmax") {
    const clean = val.replace(/[^\d]/g, "");
    if (id === "fmin") S.min = clean; else S.max = clean;
    saveFilters();
    const n = filtered().length, btn = document.getElementById("show");
    if (btn) btn.textContent = n ? `Mostra ${n} auto` : "Nessuna auto: cambia i filtri";
    const re = document.getElementById("range-err"); if (re) re.hidden = !(S.min !== "" && S.max !== "" && Number(S.min) > Number(S.max));
    document.querySelectorAll('.pill[data-act="band"]').forEach(p => p.setAttribute("aria-pressed", String(p.dataset.v === bandId())));
  }
});
document.addEventListener("keydown", e => { if (e.key === "Escape" && S.sheet) { S.sheet = null; render({keep:true}); } });
window.addEventListener("online", () => { S.offline = false; if (S.token && S.route === "affari") loadCars(true); });
window.addEventListener("offline", () => { S.offline = true; if (S.route === "affari") render({keep:true}); });
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.token && S.route === "affari") loadCars(); });

/* ---------- avvio ---------- */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => {}); });
}
S.metodi = {};
api("/api/auth/metodi").then(m => {
  S.metodi = m || {};
  document.querySelectorAll("[data-social]").forEach(a => { a.hidden = !S.metodi[a.dataset.social]; });
  if (!S.token && (S.route === "entra" || S.route === "prova")) render({keep:true});
  if (guest() && S.route === "vetrina") { const h = location.hash.replace(/^#\/?/, ""); go(/^(auto\/\d+|vendi|ricambi|profilo)$/.test(h) ? h : "affari", true); }
}).catch(() => {});
(function demo(){
  const card = document.getElementById("demo"), r = document.getElementById("sconto-demo");
  if (!card || !r) return;
  const p = Number(card.dataset.prezzo), m = Number(card.dataset.mercato), lo = Number(card.dataset.lo), hi = Number(card.dataset.hi);
  const upd = () => {
    const sc = Number(r.value), riv = Math.round(m * (1 - sc / 100)), base = riv - p - 140;
    document.getElementById("sconto-val").textContent = sc + "%";
    card.querySelector("[data-riv]").textContent = eur(riv);
    const g = card.querySelector("[data-gain]");
    g.textContent = base - lo <= 0 ? "Nessun guadagno" : base - hi <= 0 ? "fino a +" + eur(base - lo) : "+" + num(base - hi) + "–" + num(base - lo) + " €";
  };
  r.addEventListener("input", upd); upd();
})();
applyRoute();
if (S.token) api("/api/me").then(u => { S.user = Object.assign({}, S.user, {name: u.name, email: u.email, role: u.role}); store.set("user", S.user); if (S.route === "profilo") render({keep:true}); }).catch(() => {});
