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
              ["incidentate","Incidentate",'<path d="M5 17h-1a1 1 0 0 1-1-1v-3l2-5h9l3 4h3a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1h-1"/><circle cx="7.5" cy="17" r="1.8"/><circle cx="16.5" cy="17" r="1.8"/><path d="M12 3l-1 3 2 1-1 3"/>'],
              ["economiche","Sotto 3000",'<path d="M4 7h16v10H4z"/><circle cx="12" cy="12" r="2.5"/><path d="M7 10v4M17 10v4"/>'],
              ["vendi","Vendi",'<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.5"/>'],
              ["incentivi","Incentivi",'<circle cx="12" cy="12" r="9"/><path d="M15 9.5a3.5 3.5 0 1 0 0 5M7.5 11h5M7.5 13.5h5"/>'],
              ["profilo","Profilo",'<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>']];
const BANDS = [
  {id:"tutti", label:"Tutti i prezzi", min:null, max:null},
  {id:"1", label:"Fino a 1.500 €", min:null, max:1500},
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
  brands: saved.brands || [], min: saved.min || "", max: saved.max || "", stato: saved.stato || "tutte", zona: "tutte",
  raggio: saved.raggio || 0, centro: saved.centro || "milano", modello: saved.modello || "",
  kmMin: saved.kmMin || "", kmMax: saved.kmMax || "", annoMin: saved.annoMin || "", annoMax: saved.annoMax || "", carb: saved.carb || [],
  pos: store.get("pos", null),
  sconto: ((saved.sv === 2 || saved.sv === 3) && saved.sconto != null) ? Number(saved.sconto) : 10, sort: "guadagno",   /* si entra SEMPRE con l'ordine per guadagno in percentuale sul prezzo, dalla più alta */
  cars: null, costs: 140, soglie: [[5000, 1500], [null, 2000]], loading: false, loadErr: "", loadedAt: 0, offline: !navigator.onLine,
  detail: {}, scroll: {}, descOpen: false, fbOpen: false
};
const V = {targa:"", km:"", note:"", cambio:"", foto:[], stato:"form", err:"", res:null};
const R = {targa:"", pezzo:"", pezzi:[], stato:"form", err:"", res:null, aperti:{}};
const L = {mode:"login", err:"", busy:false, sent:false, email:"", req:{}};

// Il token vive anche in un cookie, così le sezioni aperte come pagina (Incentivi) sanno chi sei.
function syncCookie(){
  try {
    if (S.token) document.cookie = "scovo_t=" + encodeURIComponent(S.token) + "; path=/; max-age=315360000; samesite=lax" + (location.protocol === "https:" ? "; secure" : "");
    else document.cookie = "scovo_t=; path=/; max-age=0; samesite=lax";
  } catch (e) { /* cookie non disponibili */ }
}
const perm = p => !!(S.user && (S.user.role === "admin" || (S.user.permessi || []).includes(p)));
const tabOk = id => id === "profilo" || (id === "incentivi" ? perm("incentivi") : (!S.user || !S.user.permessi || perm("auto")));
const guest = () => !S.token && !!(S.metodi && S.metodi.aperto);
const canBrowse = () => !!S.token || guest();
function saveFilters(){ store.set("filtri", {brands:S.brands, min:S.min, max:S.max, stato:S.stato, sconto:S.sconto, sv:3, sort:S.sort,
  modello:S.modello, kmMin:S.kmMin, kmMax:S.kmMax, annoMin:S.annoMin, annoMax:S.annoMax, carb:S.carb, raggio:S.raggio, centro:S.centro}); }
function resetFilters(){ Object.assign(S, {brands:[], modello:"", min:"", max:"", kmMin:"", kmMax:"", annoMin:"", annoMax:"", carb:[], raggio:0, stato:"tutte", bot:null}); }

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
  if (res.status === 402) { S.paywall = true; if (typeof render === "function") render({keep:true}); }
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
const sezioneDi = r => r === "incidentate" || r === "economiche" ? r : "affari";
function numeri(c){
  // policy prudente (pv2): rivendita R e margine operativo arrivano già calcolati dal server, con tutti i costi
  if (c.pv) return c.pv.margine != null ? {riv: c.pv.R_finale != null ? c.pv.R_finale : c.pv.R, gain: c.pv.margine, gainMax: c.pv.margine}
                                       : {riv: 0, gain: -1e6, gainMax: -1e6};
  if (c.economica && !c.mercato) return {riv: 0, gain: -1e6, gainMax: -1e6};
  const riv = Math.round(c.mercato * (1 - S.sconto / 100));
  const base = riv - c.prezzo - S.costs;
  return {riv, gain: base - c.rip_hi, gainMax: base - c.rip_lo};
}
/* guadagno minimo per comparire, secondo il prezzo: fino a 5.000 € → 1.500, fino a 8.000 € → 2.000, fino a 12.000 € → 3.000, oltre → 4.000 */
function sogliaDi(prezzo){ const s = S.soglie.find(([lim]) => lim == null || prezzo <= lim); return s ? s[1] : 0; }
function bandId(){
  const b = BANDS.find(b => String(b.min ?? "") === S.min && String(b.max ?? "") === S.max);
  return b ? b.id : null;
}
function filtered(over){
  const s = Object.assign({}, S, over || {});
  const min = s.min === "" ? 0 : Number(s.min), max = s.max === "" ? Infinity : Number(s.max);
  const sec = s.sezione || sezioneDi(S.route), inc = sec === "incidentate", eco = sec === "economiche";
  return ordinaLista(Object.assign({}, s, {eco}), (S.cars || []).filter(c => eco ? !!c.economica : (!inc && c.vetrina != null) || (!c.economica && !!c.incidentata === inc)).map(c => Object.assign({}, c, numeri(c)))
    .filter(c => eco || c.pv || c.gain >= (c.soglia_min != null ? Math.min(c.soglia_min, sogliaDi(c.prezzo)) : sogliaDi(c.prezzo)))
    .filter(c => c.prezzo >= min && c.prezzo <= max)
    .filter(c => s.brands.length === 0 || s.brands.includes(c.marca))
    .filter(c => !s.modello || (c.modello || "") === s.modello)
    .filter(c => (s.kmMin === "" || (c.km || 0) >= Number(s.kmMin)) && (s.kmMax === "" || !c.km || c.km <= Number(s.kmMax)))
    .filter(c => (s.annoMin === "" || (c.anno || 0) >= Number(s.annoMin)) && (s.annoMax === "" || !c.anno || c.anno <= Number(s.annoMax)))
    .filter(c => !s.carb.length || s.carb.includes(carbKey(c.carb)))
    .filter(c => s.stato === "tutte" || (s.stato === "sane" ? c.rip_hi === 0 : c.rip_hi > 0))
    .filter(c => inc || !s.zona || s.zona === "tutte" || (c.area || "milano") === s.zona)   // incidentate: tutta Italia
    .filter(c => botMatch(c, s.bot))
    .filter(c => { if (!s.raggio) return true; const d = distKm(c); return d == null || d <= s.raggio; })
    .sort((a,b) => s.sort === "prezzo" ? a.prezzo - b.prezzo : s.sort === "nuove" ? String(b.nuova).localeCompare(String(a.nuova))
      : s.sort === "km" ? (a.km || 9e9) - (b.km || 9e9) : perPct(a, b)));
}
/* vetrina in testa solo con l'elenco "normale" (nessun filtro, ordine per guadagno) */
/* Ordine: prima le 15 migliori "via di mezzo" (tra 1.500 e 12.000 €, guadagno alto rispetto al prezzo),
   poi tutte le altre dalla migliore, le troppo care sempre in fondo */
const CARE = 15000, TOP_MIN = 1500, TOP_MAX = 12000, PRIME = 15;
const topScore = c => c.gainMax * Math.min(1.6, 0.6 + c.gainMax / c.prezzo);   // guadagno, premiato se rende tanto sul prezzo
/* 18/10: "Più guadagno" = guadagno in percentuale sul prezzo (5.000 € su 3.000 € = 167%), dalla più alta;
   a parità di percentuale vince il guadagno in euro */
const rendePct = c => c.gainMax / Math.max(c.prezzo || 0, 1);
/* 26/10 (cliente): per prime le marche più note, che si vendono più in fretta; dentro ogni gruppo vale il guadagno */
const MARCHE_1 = ["bmw","mercedes","fiat","volkswagen","vw","audi","toyota"];
const MARCHE_2 = ["renault","peugeot","ford","opel","citro","dacia","jeep","kia","hyundai","skoda","škoda","seat","cupra","nissan","mini","lancia","alfa","volvo","land rover","suzuki","mazda","smart"];
const gruppoMarca = c => { const m = String(c.marca || c.nome || "").toLowerCase();
  return MARCHE_1.some(x => m.startsWith(x)) ? 0 : MARCHE_2.some(x => m.startsWith(x)) ? 1 : 2; };
const perPct = (a, b) => (gruppoMarca(a) - gruppoMarca(b)) || (rendePct(b) - rendePct(a)) || (b.gainMax - a.gainMax);
function ordinaLista(s, arr){
  // 17/10: con l'ordine "guadagno" (quello di default) le auto vanno SEMPRE dalla più alta per margine,
  // qualunque filtro sia attivo: niente gruppi, niente vetrina, niente auto care in fondo.
  if (s.sort === "guadagno") return arr.sort(perPct).map(c => Object.assign(c, {_grp: "all"}));
  const care = [], rest = [];
  for (const c of arr) (c.prezzo > CARE ? care : rest).push(c);
  let topIds = new Set();
  const scelte = rest.filter(c => c.vetrina != null);
  if (!s.eco && s.sort === "guadagno" && scelte.length >= 5) {
    // le auto scelte a mano (data/vetrina.json), nell'ordine deciso
    return scelte.sort((a, b) => a.vetrina - b.vetrina).map(c => Object.assign(c, {_grp: "top"}))
      .concat(rest.filter(c => c.vetrina == null).map(c => Object.assign(c, {_grp: "rest"})), care.map(c => Object.assign(c, {_grp: "care"})));
  }
  if (!s.eco && s.sort === "guadagno") {
    topIds = new Set(rest.filter(c => c.prezzo >= TOP_MIN && c.prezzo <= TOP_MAX && c.gainMax >= 800 && c.gainMax >= c.prezzo * 0.12)
      .sort((a, b) => topScore(b) - topScore(a)).slice(0, PRIME).map(c => c.id));
  }
  const top = rest.filter(c => topIds.has(c.id)).sort((a, b) => topScore(b) - topScore(a)).map(c => Object.assign(c, {_grp: "top"}));
  const altre = rest.filter(c => !topIds.has(c.id)).map(c => Object.assign(c, {_grp: "rest"}));
  return top.concat(altre, care.map(c => Object.assign(c, {_grp: "care"})));
}
const MILANO_C = {lat: 45.4642, lon: 9.19};
function centro(){ return S.centro === "pos" && S.pos ? S.pos : MILANO_C; }
function distKm(c){
  const o = centro();
  if (!c.geo) return null;
  const R = Math.PI / 180, la1 = o.lat * R, la2 = c.geo[0] * R, dla = la2 - la1, dlo = (c.geo[1] - o.lon) * R;
  const h = Math.sin(dla / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dlo / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(h));
}
const RAGGI = [[50,"50 km"],[100,"100 km"],[200,"200 km"],[300,"300 km"],[0,"Ovunque"]];
function chiediPosizione(silenzioso){
  if (!navigator.geolocation) { if (!silenzioso) toast("Questo telefono non dà la posizione"); return; }
  navigator.geolocation.getCurrentPosition(p => {
    S.pos = {lat: p.coords.latitude, lon: p.coords.longitude, t: Date.now()}; store.set("pos", S.pos); store.del("posNo");
    if (S.token) api("/api/me", {method: "PUT", body: {posizione: {lat: S.pos.lat, lon: S.pos.lon}}}).catch(() => {});
    render({keep: true});
    if (!silenzioso) toast("Posizione attiva: vedi prima le auto entro " + (S.raggio || 100) + " km");
  }, () => { store.set("posNo", true); if (!silenzioso) toast("Posizione non concessa: vedi le auto di tutte le zone"); render({keep: true}); },
  {enableHighAccuracy: false, timeout: 15000, maximumAge: 6 * 3600 * 1000});
}
function vetrinaOn(s){ return false; }
function vetrinaOnVecchia(s){ return S.route !== "incidentate" && !s.bot && s.sort === "guadagno" && !s.brands.length && s.min === "" && s.max === "" && s.stato === "tutte" && (!s.zona || s.zona === "tutte"); }
/* filtri in più che mette l'assistente (modello, guadagno minimo, anno, km, fonte) */
function botMatch(c, b){
  if (!b) return true;
  const norm = t => String(t || "").toLowerCase().replace(/[-_]/g, " ");
  if (b.modello && !norm(c.nome).includes(norm(b.modello))) return false;
  if (b.guadagno_min && c.gain < Number(b.guadagno_min)) return false;
  if (b.anno_min && (c.anno || 0) < Number(b.anno_min)) return false;
  if (b.km_max && c.km && c.km > Number(b.km_max)) return false;
  if (b.fonte && !norm(c.fonte).includes(norm(b.fonte))) return false;
  return true;
}
function brandsAll(){ return [...new Set((S.cars || []).map(c => c.marca))].sort((a,b) => (a === "Altro") - (b === "Altro") || a.localeCompare(b, "it")); }
function brandCounts(){ const o = {}; filtered({brands: []}).forEach(c => o[c.marca] = (o[c.marca] || 0) + 1); return o; }
const ZONE_ALL = [["tutte","Tutte le zone"],["milano","Milano e dintorni"],["ravenna","Ravenna e dintorni"],["roma","Roma e dintorni"]];
function zoneCounts(){ const o = {milano:0, ravenna:0, roma:0, italia:0}; filtered({zona:"tutte"}).forEach(c => o[c.area || "milano"]++); o.tutte = o.milano + o.ravenna + o.roma; return o; }
function zoneList(){ return ZONE_ALL.filter(([k]) => ["milano","ravenna","roma"].includes(k)); }
function dueZone(){ return true; }       // Milano e Ravenna sempre visibili nei filtri
const CARB = [["benzina","Benzina"],["diesel","Diesel"],["gpl","GPL"],["metano","Metano"],["ibrida","Ibrida"],["elettrica","Elettrica"]];
const carbKey = t => { t = String(t || "").toLowerCase(); return t.startsWith("ibrid") ? "ibrida" : t; };
function nFiltri(){ return S.brands.length + (S.modello ? 1 : 0) + (S.min !== "" || S.max !== "" ? 1 : 0) + (S.kmMin !== "" || S.kmMax !== "" ? 1 : 0)
  + (S.annoMin !== "" || S.annoMax !== "" ? 1 : 0) + (S.carb.length ? 1 : 0) + (S.raggio ? 1 : 0) + (S.stato !== "tutte" ? 1 : 0); }
/* filtri attivi come chip che si tolgono con un tocco */
function activeChips(){
  const out = [], rng = (a, b, f, u) => a !== "" && b !== "" ? f(a) + "–" + f(b) + u : a !== "" ? "da " + f(a) + u : "fino a " + f(b) + u;
  if (S.brands.length) out.push(["marca", S.brands[0] + (S.modello ? " " + S.modello : "")]);
  if (S.min !== "" || S.max !== "") out.push(["prezzo", rng(S.min, S.max, num, " €")]);
  if (S.kmMin !== "" || S.kmMax !== "") out.push(["km", rng(S.kmMin, S.kmMax, num, " km")]);
  if (S.annoMin !== "" || S.annoMax !== "") out.push(["anno", rng(S.annoMin, S.annoMax, String, "")]);
  if (S.carb.length) out.push(["carb", S.carb.map(k => (CARB.find(c => c[0] === k) || [k, k])[1]).join(", ")]);
  if (S.raggio) out.push(["raggio", "entro " + S.raggio + " km " + (S.centro === "pos" && S.pos ? "da te" : "da Milano")]);
  if (S.stato !== "tutte") out.push(["stato", S.stato === "sane" ? "Senza danni" : "Con danni"]);
  return out;
}
function statoTxt(c){ if (c.incidentata) return c.danno === "grave" ? "Incidente pesante" : c.danno === "leggero" ? "Danno leggero" : "Incidentata"; return c.rip_hi === 0 ? (c.danno === "sconosciuto" ? "Senza danni dichiarati" : "Sana") : c.danno === "leggero" ? "Danno leggero" : "Da sistemare"; }
function ripTxt(c){ return c.rip_hi ? num(c.rip_lo) + "–" + num(c.rip_hi) + " €" : "Nessuna"; }
function gainTxt(c){
  if (c.pv) return c.pv.margine == null ? "Da valutare" : (c.pv.margine >= 0 ? "+" : "−") + eur(Math.abs(c.pv.margine));
  if (c.economica && (!c.mercato || c.gainMax <= 0)) return "Da valutare";
  const lo = Math.max(c.gain, 0);
  return c.gainMax !== c.gain ? (c.gain < 0 ? "fino a +" + eur(c.gainMax) : "+" + num(lo) + "–" + num(c.gainMax) + " €") : "+" + eur(c.gain);
}
function meta(c){ const d = distKm(c); return [c.anno, c.km ? num(c.km) + " km" : "km da chiedere", c.carb, c.zona].filter(Boolean).join(" · "); }
function img(src, alt, eager){ return src ? `<img src="${esc(src)}" alt="${esc(alt)}" ${eager ? 'fetchpriority="high"' : 'loading="lazy"'} decoding="async">` : ""; }

/* ---------- dati ---------- */
async function loadCars(force){
  if (S.loading) return;
  if (!force && S.cars && Date.now() - S.loadedAt < 5 * 60 * 1000) return;
  S.loading = true; S.loadErr = ""; if (isList(S.route)) render({keep:true});
  try {
    const d = await api("/api/affari");
    S.cars = d.items || []; S.vetrinaTitolo = d.vetrina_titolo || null; S.loadedAt = Date.now(); S.policy = d.policy || null;
    if (d.soglie) S.soglie = d.soglie;
    if (d.costi_fissi) S.costs = Object.values(d.costi_fissi).reduce((a,b) => a + b, 0);
    const known = new Set(S.cars.map(c => c.marca)); S.brands = S.brands.filter(b => known.has(b));
  } catch (e) { S.loadErr = e.message; }
  S.loading = false;
  if (isList(S.route)) render({keep:true});
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
  const vOn = vetrinaOn(S) && list.some(c => c.vetrina != null);
  const zc = zoneCounts();
  const PIN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>';
  const zoneBar = `<div class="zona-box" role="group" aria-label="Zona">${zoneList().map(([k,l]) =>
    `<button class="zona-btn" data-act="zona" data-v="${k}" aria-pressed="${S.zona === k}">${PIN}<span class="zona-txt"><b>${l.replace(" e dintorni","").replace("Resto d'Italia","Italia")}</b><small>${zc[k] || 0} auto</small></span></button>`).join("")}</div>`;
  const TIT = {all: "Dalla più conveniente: margine più alto in cima", top: "⭐ Le 15 migliori di oggi · tra 1.500 e 12.000 €", rest: "Tutte le altre, dalla migliore", care: "Le più care (oltre 15.000 €)"};
  const cards = list.map((c, i) => `${!c.economica && (i === 0 || list[i-1]._grp !== c._grp) && (S.sort === "guadagno" || c._grp === "care") && list.length > 1 ? `<h2 class="vet-h">${c._grp === "top" && S.vetrinaTitolo ? esc(S.vetrinaTitolo) : TIT[c._grp]}</h2>` : ""}
    <button class="card${c._grp === "top" ? " vet" : ""}" data-act="open" data-v="${c.id}" aria-label="${esc(c.nome)}, guadagno ${gainTxt(c)}">
      <div class="photo">${CAR_SVG}${img(c.foto, c.nome, i < 2)}<span class="badge">${statoTxt(c)}</span>${c.aperta ? `<span class="badge seen">Già contattata</span>` : ""}</div>
      <div class="card-body">
        <div><div class="name">${esc(c.nome)}</div><div class="meta">${esc(meta(c))}</div></div>
        <div class="trio">
          <div><span class="lbl">Prezzo</span><span class="val">${eur(c.prezzo)}</span></div>
          ${c.economica ? `<div><span class="lbl">Mercato</span><span class="val">${c.mercato ? "~" + eur(c.mercato) : "da stimare"}</span></div>
          <div><span class="lbl">Rivendita</span><span class="val">${c.mercato ? eur(c.riv) : "—"}</span></div>`
          : `<div><span class="lbl">Riparazioni</span><span class="val ${c.rip_hi ? "small warn" : "good"}">${ripTxt(c)}</span></div>
          <div><span class="lbl">Rivendita</span><span class="val">${eur(c.riv)}</span></div>`}
        </div>
        <div class="gain"><span>${c.pv ? esc(c.pv.etichetta || "") + (c.pv.margine != null ? " · margine prudente" : "") : c.economica ? "Guadagno possibile" : "Guadagno stimato"}</span><b>${gainTxt(c)}</b></div>
      </div>
    </button>`).join("");
  const empty = `<div class="empty"><strong style="font:700 19px var(--disp)">${S.cars.length ? "Nessuna auto con questi filtri" : "Nessuna auto in questo momento"}</strong>
      <span class="meta">${S.cars.length ? "Allarga il prezzo, togli qualche marca o metti la rivendita a mercato pieno." : "Cerchiamo nuove auto ogni 3 ore: riprova più tardi."}</span>
      ${S.cars.length ? `<button class="btn" data-act="reset">Togli tutti i filtri</button>` : `<button class="btn" data-act="reload">Aggiorna</button>`}</div>`;
  const right = `<div style="display:flex;gap:8px;align-items:center"><button class="refresh${S.loading ? " spinning" : ""}" data-act="reload" aria-label="Aggiorna l'elenco">${ICON.refresh}</button></div>`;
  const incV = S.route === "incidentate", ecoV = S.route === "economiche";
  const ac = activeChips();
  const SORTS = [["guadagno","Più guadagno %"],["prezzo","Prezzo più basso"],["nuove","Più recenti"],["km","Meno km"]];
  const fbar = `<div class="fbar">
      <button class="fbtn" data-act="filters" aria-label="Apri tutti i filtri${nf ? ", " + nf + " attivi" : ""}">${ICON.filter}<span>Filtri</span>${nf ? `<span class="count-pill">${nf}</span>` : ""}</button>
      <label class="fsort"><span class="sr">Ordina</span><select id="f-sort" aria-label="Ordina">${SORTS.map(([v,l]) => `<option value="${v}" ${S.sort === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    </div>
    <div class="qf" role="group" aria-label="Filtri veloci">
      <label class="qsel ${S.max !== "" ? "on" : ""}"><span class="sr">Prezzo massimo</span><select id="q-pmax">${opts(P_STEPS.map(v => [v, "Fino a " + num(v) + " €"]), S.max, "Prezzo")}</select></label>
      <label class="qsel ${S.carb.length ? "on" : ""}"><span class="sr">Alimentazione</span><select id="q-carb">${S.carb.length > 1 ? `<option value="__multi" selected>${S.carb.length} alimentazioni</option>` : ""}${opts(CARB, S.carb.length === 1 ? S.carb[0] : "", "Alimentazione")}</select></label>
      <label class="qsel ${S.kmMax !== "" ? "on" : ""}"><span class="sr">Chilometri massimi</span><select id="q-kmax">${opts(KM_STEPS.map(v => [v, "Fino a " + num(v) + " km"]), S.kmMax, "Km")}</select></label>
    </div>
    ${ac.length ? `<div class="chips fchips" role="group" aria-label="Filtri attivi">${ac.map(([k,l]) => `<button class="chip on" data-act="unf" data-v="${k}" aria-label="Togli filtro ${esc(l)}">${esc(l)}<span aria-hidden="true">×</span></button>`).join("")}<button class="chip clear" data-act="reset">Azzera tutto</button></div>` : ""}`;
  return header(incV ? "Incidentate" : ecoV ? "Sotto 3.000" : "scovo", incV ? `${list.length} auto incidentate da riparare · ${S.policy ? "rivendita prudente" : "rivendita −" + S.sconto + "%"}` : ecoV ? `${list.length} auto con il numero, sotto i 3.000 €` : `${list.length} auto da girare · ${S.policy ? "solo margini prudenti" : "rivendita −" + S.sconto + "%"}`, right) + `
    ${fbar}
  </header>
  <main class="list" aria-live="polite">${ecoV ? `<div class="tip inc-tip"><span class="eur" aria-hidden="true">!</span><span><b>Auto di privati sotto i 3.000 €, tutte con il numero.</b> Sopra i 1.500 € solo quelle sotto il prezzo di mercato; guadagni stimati prudenti. Non le abbiamo ancora controllate una per una: mercato e guadagno sono stime, guarda foto e auto dal vivo.</span></div>` : ""}${incV ? `<div class="tip inc-tip"><span class="eur" aria-hidden="true">!</span><span><b>Per carrozzieri e commercianti di incidentate.</b> Danni anche pesanti: il guadagno è già calcolato con i pezzi da cambiare (solo pezzi, manodopera esclusa). Guarda sempre l'auto dal vivo.</span></div>` : ""}${S.bot ? botBanner(list.length) : ""}${guest() && !S.bot ? `<div class="tip" style="font:600 16px/1.3 var(--body)"><span class="eur" aria-hidden="true">i</span><span>Sito aperto: puoi contattare i venditori anche senza account. <a href="#/entra" style="color:inherit;font-weight:800">Entra</a> per salvare le tue auto.</span></div>` : ""}${S.offline ? `<div class="banner">Sei senza connessione: vedi le auto dell'ultimo aggiornamento.</div>` : ""}${S.loadErr ? `<div class="banner">${esc(S.loadErr)}</div>` : ""}${S.tip && !S.bot ? `<div class="tip"><span class="eur" aria-hidden="true">€</span><span><b>Tratta sempre:</b> più sconto, più guadagno.</span><button class="tip-x" data-act="tip" aria-label="Chiudi il consiglio">×</button></div>` : ""}${list.length ? cards : empty}</main>`;
}

function botBanner(n){
  const b = S.bot, bits = [];
  if (S.brands.length) bits.push(S.brands.join(", "));
  if (b.modello) bits.push(cap(b.modello));
  if (S.min !== "" || S.max !== "") bits.push(S.min !== "" && S.max !== "" ? eur(S.min) + "–" + eur(S.max) : S.min !== "" ? "da " + eur(S.min) : "fino a " + eur(S.max));
  if (b.guadagno_min) bits.push("guadagno da " + eur(b.guadagno_min));
  if (S.stato !== "tutte") bits.push(S.stato === "sane" ? "senza danni" : "con danni");
  if (S.zona && S.zona !== "tutte") bits.push((ZONE_ALL.find(z => z[0] === S.zona) || ["", ""])[1]);
  if (b.anno_min) bits.push("dal " + b.anno_min);
  if (b.km_max) bits.push("max " + num(b.km_max) + " km");
  if (b.fonte) bits.push(cap(b.fonte));
  return `<section class="bot-banner" aria-label="Risposta di Scovo">
    <div class="bb-top"><span class="bb-ic" aria-hidden="true">${MIC}</span><b>${n} ${n === 1 ? "auto" : "auto"} per te</b>
      ${A.speaking ? `<button class="link bb-stop" data-act="bot-zitto">Basta voce</button>` : b.testo ? `<button class="link bb-stop" data-act="bot-ripeti">Riascolta</button>` : ""}</div>
    ${b.testo ? `<p class="bb-txt${b.open ? " open" : ""}">${esc(b.testo)}</p>${!b.open && b.testo.length > 120 ? `<button class="link bb-more" data-act="bb-more">Leggi tutta la risposta</button>` : ""}` : ""}
    ${bits.length ? `<div class="bb-tags">${bits.map(t => `<span>${esc(t)}</span>`).join("")}</div>` : ""}
    <div class="bb-act"><button class="btn" data-act="reset">Togli filtri</button></div>
  </section>`;
}

/* ---------- scheda: conti ---------- */
function boxV1(c){ return `
      <div class="box">
        <div class="row"><span class="k">Prezzo richiesto</span><span class="v">${eur(c.prezzo)}</span></div>
        <div class="row"><span class="k">Riparazioni (solo pezzi)</span><span class="v ${c.rip_hi ? "warn" : ""}">${ripTxt(c)}</span></div>
        <div class="row" style="border-bottom:0"><span class="k">Rivendita veloce</span><span class="v">${c.economica && !c.mercato ? "da stimare" : eur(c.riv)}</span></div>
        <div class="note">${c.economica && !c.mercato ? "Per questa auto non abbiamo ancora un prezzo di mercato affidabile." : `Il ${S.sconto}% sotto la media del mercato (${eur(c.mercato)}): un prezzo giusto per rivendere.`}${c.economica ? " Auto non ancora controllata a mano: guarda bene foto e descrizione." : ""}</div>
        <div class="row total"><span class="k">Guadagno stimato</span><span class="v">${gainTxt(c)}</span></div>
        <div class="note" style="border:0;margin-top:-8px">Già tolti passaggio (90 €) e pulizia (50 €).${c.rip_hi ? " Più spendi in riparazioni, meno guadagni." : ""}</div>
      </div>`; }
const VOCI = {ricambi: "Ricambi", manodopera: "Manodopera e verniciatura", passaggio: "Passaggio e pratiche", trasporto: "Trasporto / ritiro",
  diagnosi_revisione: "Diagnosi e revisione", preparazione: "Preparazione e pulizia", vendita: "Costi di vendita", garanzia: "Garanzia (accantonamento)",
  giacenza: "Giacenza", iva_margine: "IVA (regime del margine)", costi_commerciante: "Tagliando e pulizia"};
const ORIG = {annuncio: "dall'annuncio", stima_ricambi: "ricerca prezzi pezzi", config: "impostazione", stima: "stima prudente"};
function pct(x){ return Math.round(x * 1000) / 10 + "%"; }
function boxPolicy(c, v){
  const p = c.pv, row = (k, val, cls) => `<div class="row"><span class="k">${k}</span><span class="v ${cls || ""}">${val}</span></div>`;
  const ok = p.categoria === "opportunita";
  let h = `<div class="box">
    <div class="row"><span class="k"><b>${esc(p.etichetta || "")}</b></span><span class="v ${ok ? "" : "warn"}" style="font-size:15px">${esc(p.campione || "")}</span></div>
    ${row("Prezzo di acquisto", eur(c.prezzo))}
    ${p.M != null ? row(`Prezzo di mercato M <small>(${v && v.metodo === "modello" ? "modello su " + p.n + " annunci dello stesso modello" : p.n + " confronti"}, per anno e km di questa auto)</small>`, eur(p.M)) : row("Confronti validi", p.n || 0)}
    ${p.L != null ? row("Prezzo basso L <small>(25° percentile)</small>", eur(p.L)) : ""}
    ${p.riduzione != null ? row(`Riduzione prudenziale <small>(fascia ${esc(p.fascia || "")})</small>`, "−" + pct(p.riduzione)) : ""}
    ${p.R != null ? row("Rivendita prudente R", eur(p.R_finale != null ? p.R_finale : p.R)) : ""}`;
  if (v && v.R != null) h += `<div class="note">R = il più basso tra M ridotto (${eur(v.centrale_ridotto)}) e L (${eur(v.L)})${v.perdita_storico_danno ? `, meno ${eur(v.perdita_storico_danno)} per lo storico del danno` : ""}. Sono prezzi richiesti negli annunci, non vendite concluse.</div>`;
  if (v && v.costi) {
    h += Object.entries(v.costi).filter(([, x]) => x.euro).map(([k, x]) => `<div class="row"><span class="k">${esc(VOCI[k] || k)} <small>· ${esc(ORIG[x.origine] || x.origine)}</small></span><span class="v" style="font-size:16px">−${eur(x.euro)}</span></div>`).join("");
    if (v.riserva && v.riserva.euro) h += row(`Riserva imprevisti <small>· ${esc(v.riserva.nota || "")}</small>`, "−" + eur(v.riserva.euro));
  } else if (p.totale_costi != null) h += row("Costi e riserva", "−" + eur(p.totale_costi + (p.riserva || 0)));
  h += p.margine != null ? `<div class="row total"><span class="k">Margine operativo prudente</span><span class="v" style="${p.margine < (p.soglia || 0) ? "color:var(--warn)" : ""}">${gainTxt(c)}</span></div>
    <div class="note" style="border:0;margin-top:-8px">${p.riempimento || p.top ? `<b>Margine sotto la soglia piena di ${eur(p.soglia)}</b>: è tra le auto più convenienti che abbiamo in questo momento.` : `Soglia minima richiesta: ${eur(p.soglia)}.`} Non è un utile netto dopo le imposte; l'auto e le condizioni d'acquisto vanno sempre verificate.</div>` : "";
  h += `</div>`;
  if (!v) return h + `<div class="skel"><div class="bar"></div></div>`;
  const li = a => a.map(x => `<li>${esc(x)}</li>`).join("");
  if ((v.motivi || []).length) h += `<section class="d-sec"><h2>Perché questa qualifica</h2><ul class="checks">${li(v.motivi)}</ul></section>`;
  if ((v.mancanti || []).length || (v.ipotesi || []).length) h += `<section class="d-sec"><h2>Dati mancanti e ipotesi</h2><ul class="checks">${li((v.mancanti || []).map(x => "Manca: " + x).concat(v.ipotesi || []))}</ul></section>`;
  if ((v.confronti || []).length) h += `<section class="d-sec"><h2>Confronti usati (${v.n})</h2><ul class="checks">${v.confronti.map(x =>
      `<li>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener" style="color:inherit">` : ""}${eur(x.prezzo)}${x.prezzo_corretto != null && x.prezzo_corretto !== x.prezzo ? ` (→ ${eur(x.prezzo_corretto)})` : ""} · ${x.anno || "?"} · ${x.km != null ? num(x.km) + " km" : "km ?"}${x.cambio ? " · " + esc(x.cambio) : ""}${x.kw ? " · " + x.kw + " kW" : ""} · ${esc(x.fonte || "")}${x.url ? "</a>" : ""}<br><small>pubblicato ${esc(x.pubblicato || "?")} · verificato ${esc(x.verificato || "?")}</small></li>`).join("")}</ul></section>`;
  const ex = Object.entries(v.esclusi_motivi || {}).concat(Object.entries(v.non_equivalenti || {}));
  if (ex.length) h += `<section class="d-sec"><h2>Annunci esclusi dal calcolo</h2><ul class="checks">${ex.sort((a, b) => b[1] - a[1]).slice(0, 8).map(([m, n]) => `<li>${n} × ${esc(m)}</li>`).join("")}</ul></section>`;
  return h;
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
  const c = Object.assign({}, base, d && !d.err ? d : {}, base.pv ? {pv: base.pv} : {}, numeri(base));
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
      ${d ? (desc ? `<p class="desc" style="white-space:pre-line">${esc(longDesc ? desc.slice(0, 240).replace(/\s+\S*$/, "") + "…" : desc)}</p>${longDesc ? `<button class="more-desc" data-act="desc">Leggi tutto</button>` : ""}` : "") : `<div class="skel"><div class="bar"></div><div class="bar s"></div></div>`}
      ${c.pv ? boxPolicy(c, d && !d.err ? d.valutazione : null) : boxV1(c)}
      <div class="tip"><span class="eur" aria-hidden="true">€</span><span><b>Tratta sempre:</b> più sconto, più guadagno.</span></div>
      ${pezzi.length ? `<button class="parts-btn" data-act="parts"><span>Cosa comprare per sistemarla · ${pezzi.length} ${pezzi.length === 1 ? "pezzo" : "pezzi"}</span>${ICON.next}</button>`
          : c.rip_hi ? "" : `<div class="ok-box">Nessuna riparazione dichiarata nell'annuncio</div>`}
      ${d && d.controlli && d.controlli.length ? `<section class="d-sec"><h2>Da controllare</h2><ul class="checks">${d.controlli.map(x => `<li>${esc(x)}</li>`).join("")}</ul></section>` : ""}
      ${c.aperta ? `<section class="box fb" style="padding:14px 16px"><h2>Com'è andata?</h2><div class="wrap">${ESITI.map(([v,l]) => `<button class="pill" data-act="esito" data-v="${v}" aria-pressed="${S.esito && S.esito[c.id] === v}">${l}</button>`).join("")}</div><span class="hint">Ci aiuta a trovarti auto migliori.</span></section>` : ""}
      <span class="where">Annuncio di un ${d && d.privato === false ? "venditore" : "privato"} su ${esc(c.fonte || "")} · ${esc(c.zona)}</span>
    </div>
    <div class="dock"><div class="dock-in">
      ${d && !d.err && !d.tel ? `<a class="talk" id="talk" data-act="talk-link" href="/api/affari/${c.id}/vai"${MOBILE ? "" : ' target="_blank" rel="noopener"'}>${contact.icon}${contact.label}</a>`
          : `<button class="talk" id="talk" data-act="talk" ${d ? "" : 'aria-disabled="true"'}>${contact.icon}${contact.label}</button>`}
      <span class="talk-note">${contact.note}</span>
    </div></div>
  </div>`;
}

/* ---------- fogli dal basso ---------- */
function opts(list, cur, vuoto){ return `<option value="">${vuoto}</option>` + list.map(([v,l]) => `<option value="${esc(v)}" ${String(cur) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join(""); }
const P_STEPS = [500,1000,1500,2000,2500,3000,4000,5000,6000,7000,8000,10000,12500,15000,20000,25000,30000,40000];
const KM_STEPS = [10000,20000,30000,50000,75000,100000,125000,150000,180000];
function sheetFilters(){
  const n = filtered().length;
  const base = filtered({brands: [], modello: ""});
  const bc = {}; base.forEach(c => bc[c.marca] = (bc[c.marca] || 0) + 1);
  const marche = Object.keys(bc).sort((a,b) => (a === "Altro") - (b === "Altro") || a.localeCompare(b, "it"));
  const mc = {}; if (S.brands.length) filtered({modello: ""}).forEach(c => { if (c.modello) mc[c.modello] = (mc[c.modello] || 0) + 1; });
  const modelli = Object.keys(mc).sort((a,b) => a.localeCompare(b, "it"));
  const cc = {}; filtered({carb: []}).forEach(c => { const k = carbKey(c.carb); cc[k] = (cc[k] || 0) + 1; });
  const anni = []; for (let y = new Date().getFullYear(); y >= 2007; y--) anni.push([y, String(y)]);
  const seg = (items, cur, act, cols) => `<div class="seg" style="grid-template-columns:repeat(${cols},minmax(0,1fr))">${items.map(([v,l]) => `<button data-act="${act}" data-v="${v}" aria-pressed="${String(cur) === String(v)}">${l}</button>`).join("")}</div>`;
  const sel = (id, label, inner) => `<label class="fsel"><span>${label}</span><select id="${id}">${inner}</select></label>`;
  return `
  <div class="sheet-wrap fpanel-wrap" data-act="close-bg"><div class="sheet fpanel" role="dialog" aria-modal="true" aria-labelledby="fh">
    <div class="sheet-head"><h2 id="fh">Filtri</h2><button class="link" data-act="close" aria-label="Chiudi i filtri">Chiudi</button></div>
    <div class="sheet-body">
      <section class="fgrp"><h3>Marca e modello</h3><div class="fgrid">
        ${sel("f-marca", "Marca", opts(marche.map(m => [m, m + " (" + bc[m] + ")"]), S.brands[0] || "", "Tutte le marche"))}
        ${sel("f-modello", "Modello", S.brands.length ? opts(modelli.map(m => [m, m + " (" + mc[m] + ")"]), S.modello, "Tutti i modelli") : `<option value="">Scegli prima la marca</option>`)}
      </div></section>
      <section class="fgrp"><h3>Prezzo</h3><div class="fgrid">
        ${sel("f-pmin", "Da", opts(P_STEPS.map(v => [v, num(v) + " €"]), S.min, "Qualsiasi"))}
        ${sel("f-pmax", "A", opts(P_STEPS.map(v => [v, num(v) + " €"]), S.max, "Qualsiasi"))}
      </div></section>
      <section class="fgrp"><h3>Chilometri</h3><div class="fgrid">
        ${sel("f-kmin", "Da", opts(KM_STEPS.map(v => [v, num(v) + " km"]), S.kmMin, "Qualsiasi"))}
        ${sel("f-kmax", "A", opts(KM_STEPS.map(v => [v, num(v) + " km"]), S.kmMax, "Qualsiasi"))}
      </div></section>
      <section class="fgrp"><h3>Anno</h3><div class="fgrid">
        ${sel("f-amin", "Dal", opts(anni.slice().reverse(), S.annoMin, "Qualsiasi"))}
        ${sel("f-amax", "Al", opts(anni, S.annoMax, "Qualsiasi"))}
      </div></section>
      <section class="fgrp"><h3>Alimentazione</h3><div class="fcheck">${CARB.map(([k,l]) =>
        `<button class="fck" data-act="carb" data-v="${k}" aria-pressed="${S.carb.includes(k)}" ${!cc[k] && !S.carb.includes(k) ? "disabled" : ""}><i aria-hidden="true"></i>${l}<span class="n">${cc[k] || 0}</span></button>`).join("")}</div></section>
      <section class="fgrp"><h3>Distanza</h3>
        ${seg([["milano","Da Milano"],["pos","Dalla mia posizione"]], S.centro, "centro", 2)}
        <div class="fgrid" style="margin-top:10px">${sel("f-raggio", "Entro", opts([[10,"10 km"],[25,"25 km"],[50,"50 km"],[75,"75 km"],[100,"100 km"]], S.raggio || "", "Qualsiasi distanza"))}</div>
      </section>
      <section class="fgrp"><h3>Stato</h3>${seg([["tutte","Tutte"],["sane","Senza danni"],["danni","Con danni"]], S.stato, "stato", 3)}</section>
      ${S.policy ? `<section class="fgrp"><h3>Rivendita</h3><span class="hint">Rivendita prudente calcolata per ogni auto: il più basso tra la mediana ridotta per fascia e il 25° percentile dei confronti equivalenti, meno tutti i costi.</span></section>`
        : `<section class="fgrp"><h3>Rivendita sotto il mercato</h3>${seg([[0,"Mercato pieno"],[5,"−5%"],[10,"−10%"]], S.sconto, "sconto", 3)}
        <span class="hint">I guadagni si ricalcolano subito.</span></section>`}
    </div>
    <div class="sheet-foot fpanel-foot"><button class="btn" data-act="reset">Azzera</button><button class="btn btn-dark" data-act="close" id="show">${n ? `Mostra ${n} auto` : "Nessuna auto: cambia i filtri"}</button></div>
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
      <button data-act="pwd"><span>Cambia password</span>${ICON.next}</button>
      ${u.ha_pagamento ? `<button data-act="portale"><span>Abbonamento: carta, fatture, disdetta</span>${ICON.next}</button>` : ""}</div>
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
      <label class="field">Email<input name="email" type="email" autocomplete="username" inputmode="email" autocapitalize="off" autocorrect="off" spellcheck="false" required value="${esc(L.email)}"></label>
      <label class="field">Password<input name="password" id="l-pwd" type="password" autocomplete="current-password" autocapitalize="off" autocorrect="off" spellcheck="false" required></label>
      <label style="display:flex;gap:8px;align-items:center;font-size:15px;margin-top:-4px"><input type="checkbox" onchange="document.getElementById('l-pwd').type=this.checked?'text':'password'" style="width:20px;height:20px"> Mostra password</label>
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
  if (f.id === "bot-form") { unlockVoice(); const i = document.getElementById("bot-text"); botAsk(i ? i.value : A.text); return; }
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
  S.token = r.token; S.user = r.user; store.set("token", r.token); store.set("user", r.user); syncCookie();
  S.scroll = {}; go("affari", true); loadCars(true); window.scrollTo(0, 0);

}
async function redeemCode(code){
  try { loggedIn(await api("/api/auth/scambio", {method: "POST", body: {code}})); }
  catch (e) { S.esitoAccesso = "errore"; go("entra", true); }
}
function logout(silent){
  if (!silent && S.token) { fetch("/api/auth/esci", {method: "POST", headers: {Authorization: "Bearer " + S.token}}).catch(() => {}); }
  S.token = null; S.user = null; S.cars = null; S.detail = {}; store.del("token"); store.del("user"); syncCookie();
  try { if (window.caches) caches.delete("scovo-dati"); } catch (e) {}
  L.err = typeof silent === "string" ? silent : silent ? "Accesso scaduto: entra di nuovo." : "";
  go(silent ? "entra" : "", true);
}

/* ---------- navigazione (il tasto indietro del telefono funziona) ---------- */
const isList = r => r === "affari" || r === "incidentate" || r === "economiche";
function parseHash(){
  const h = location.hash.replace(/^#\/?/, "");
  const m = h.match(/^auto\/(\d+)$/);
  if (m) return {route: "auto", sel: Number(m[1])};
  const a = h.match(/^accesso\/([\w-]+)$/);
  if (a) return {route: "accesso", code: a[1]};
  const e = h.match(/^entra(?:\/([\w-]+))?$/);
  if (e) return {route: "entra", esito: e[1] || ""};
  if (h === "prova") return {route: "prova"};
  const ab = h.match(/^abbonamento\/(ok|annullato)$/);
  if (ab) return {route: "abbonamento", esito: ab[1]};
  if (!canBrowse()) return {route: "vetrina"};
  if (h === "ricambi") { location.replace("/incentivi/"); }
  return {route: ["vendi","profilo","incidentate","economiche"].includes(h) ? h : "affari", sel: null};
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
  if (r.route === "abbonamento") {
    history.replaceState(null, "", "#/affari");
    if (r.esito === "ok") { S.paywall = false; toast("Abbonamento attivo: benvenuto in Scovo!"); }
    else toast("Pagamento annullato");
    S.route = "affari"; render({top:true}); loadCars(true); return;
  }
  if (r.route === "accesso") { history.replaceState(null, "", "#/entra"); S.route = "entra"; render({top:true}); redeemCode(r.code); return; }
  if (S.token && ["entra","prova","vetrina"].includes(r.route)) { go("affari", true); return; }
  if (r.route === "entra") { S.esitoAccesso = r.esito; if (r.esito) history.replaceState(null, "", "#/entra"); }
  S.route = r.route; S.sel = r.sel; S.sheet = null; S.descOpen = false;
  if (S.route === "auto") { if (!canBrowse()) { go("entra", true); return; } loadDetail(S.sel); render({top:true}); }
  else if (S.route === "entra" || S.route === "prova") render({top:true});
  else render({y: S.scroll[S.route] || 0});
  if (isList(S.route) && canBrowse()) loadCars();
}
window.addEventListener("popstate", () => { if (S.sheet) { S.sheet = null; } applyRoute(); });

/* ---------- disegno ---------- */
/* ---------- Assistente a voce ---------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const TTS = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
const A = {msgs: [], listening: false, busy: false, voice: store.get("voce", true), rec: null, text: "", speaking: false, unlocked: false, voiceObj: null};
const BOT_IDEE = ["Che affari ci sono oggi sopra i 3.000 € di guadagno?", "Fammi vedere tutte le Audi", "Auto sotto i 5.000 € senza danni",
                  "Quanto vale una Audi TT del 2014?", "Controlla la targa AB123CD e dimmi il prezzo di faro e paraurti"];
const MIC = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
/* dentro le app (Instagram, Facebook, WhatsApp...) il microfono spesso non c'è */
const IN_APP = /FBAN|FBAV|Instagram|Line\/|WhatsApp|Telegram|GSA\/|; wv\)/i.test(navigator.userAgent || "");
function botFab(){ return `<button class="bot-fab" data-act="bot" data-voce="1" aria-label="Chiedi a Scovo a voce">${MIC}<span>Chiedi</span></button>`; }

/* ---- voce in uscita ----
   Con la voce naturale attiva (chiave sul server) l'audio arriva da /api/voce e suona come una persona.
   Senza, si usa la voce del telefono. */
const VOX = () => !!(S.metodi && S.metodi.voce && S.metodi.voce.tts);
const EARS = () => !!(S.metodi && S.metodi.voce && S.metodi.voce.stt && navigator.mediaDevices && window.MediaRecorder);
let player = null, speakToken = 0, speakEnd = null;
function pickVoice(){
  if (!TTS) return null;
  const vs = speechSynthesis.getVoices().filter(v => /^it/i.test(v.lang));
  const pref = [/premium|enhanced|natural|neural/i, /google/i, /alice|federica|paola|luca|elsa|diego|isabella/i];
  for (const re of pref) { const v = vs.find(v => re.test(v.name)); if (v) return v; }
  return vs[0] || null;
}
if (TTS) { A.voiceObj = pickVoice(); try { speechSynthesis.addEventListener("voiceschanged", () => { A.voiceObj = pickVoice(); }); } catch (e) {} }
function silentWav(){
  const n = 800, buf = new ArrayBuffer(44 + n), d = new DataView(buf);
  const w = (o, t) => { for (let i = 0; i < t.length; i++) d.setUint8(o + i, t.charCodeAt(i)); };
  w(0, "RIFF"); d.setUint32(4, 36 + n, true); w(8, "WAVE"); w(12, "fmt "); d.setUint32(16, 16, true); d.setUint16(20, 1, true);
  d.setUint16(22, 1, true); d.setUint32(24, 8000, true); d.setUint32(28, 8000, true); d.setUint16(32, 1, true); d.setUint16(34, 8, true);
  w(36, "data"); d.setUint32(40, n, true); for (let i = 0; i < n; i++) d.setUint8(44 + i, 128);
  return URL.createObjectURL(new Blob([buf], {type: "audio/wav"}));
}
/* iPhone: l'audio parte solo se il primo "play" avviene dentro un tocco. Lo facciamo subito, in silenzio. */
function unlockVoice(){
  if (!player) { player = new Audio(); player.preload = "auto"; player.setAttribute("playsinline", ""); }
  if (A.unlocked) return;
  A.unlocked = true;
  try { player.src = silentWav(); const pr = player.play(); if (pr && pr.catch) pr.catch(() => {}); } catch (e) {}
  if (TTS) { try { const u = new SpeechSynthesisUtterance(" "); u.volume = 0; u.lang = "it-IT"; speechSynthesis.speak(u); } catch (e) {} }
}
function speakable(t){
  return String(t || "").replace(/€/g, " euro").replace(/(\d)\.(\d{3})/g, "$1$2").replace(/\+(\d)/g, "più $1")
    .replace(/(\d)\s*[–-]\s*(\d)/g, "$1 a $2").replace(/\bkm\b/g, "chilometri").replace(/[*_#•]/g, " ");
}
function chunks(text, max){
  const parts = String(text).split(/(?<=[.!?;:])\s+/), out = [];
  parts.forEach(p => { p = p.trim(); if (!p) return; if (out.length && (out[out.length - 1] + " " + p).length < max) out[out.length - 1] += " " + p; else out.push(p); });
  return out;
}
async function fetchVoice(t){
  const headers = {"Content-Type": "application/json"}; if (S.token) headers.Authorization = "Bearer " + S.token;
  const r = await fetch("/api/voce", {method: "POST", headers, body: JSON.stringify({testo: t})});
  if (!r.ok) throw new Error("voce " + r.status);
  return URL.createObjectURL(await r.blob());
}
function playUrl(url, tok){
  return new Promise(res => {
    if (tok !== speakToken) return res(false);
    const done = ok => { player.onended = player.onerror = null; res(ok); };
    player.onended = () => done(true); player.onerror = () => done(false);
    player.src = url;
    const pr = player.play(); if (pr && pr.catch) pr.catch(() => done(false));
  });
}
function speakDevice(text, tok){
  return new Promise(res => {
    if (!TTS) return res();
    try {
      if (speechSynthesis.speaking || speechSynthesis.pending) speechSynthesis.cancel();
      const cs = chunks(speakable(text), 160); if (!cs.length) return res();
      cs.forEach((c, i) => {
        const u = new SpeechSynthesisUtterance(c); u.lang = "it-IT"; u.rate = 1.04;
        if (A.voiceObj) u.voice = A.voiceObj;
        if (i === cs.length - 1) u.onend = u.onerror = () => res();
        speechSynthesis.speak(u);
      });
      try { speechSynthesis.resume(); } catch (e) {}
      setTimeout(res, 60000);
    } catch (e) { res(); }
  });
}
/* parla e restituisce una promessa che finisce quando ha finito di parlare */
async function speak(text, force){
  if ((!A.voice && !force) || !text) return;
  stopVoice();
  const tok = ++speakToken;
  A.speaking = true; refreshSpeaking();
  try {
    if (VOX()) {
      // la prima frase parte subito, le altre si preparano mentre parla
      const cs = chunks(text, 220), urls = cs.map(c => fetchVoice(c).catch(() => null));
      let ok = true;
      for (let i = 0; i < cs.length && tok === speakToken; i++) {
        const u = await urls[i];
        if (!u) { ok = false; await speakDevice(cs.slice(i).join(" "), tok); break; }
        ok = await playUrl(u, tok); URL.revokeObjectURL(u);
        if (!ok && tok === speakToken) { await speakDevice(cs.slice(i).join(" "), tok); break; }
      }
    } else await speakDevice(text, tok);
  } finally {
    if (tok === speakToken) { A.speaking = false; refreshSpeaking(); }
  }
}
function stopVoice(){
  speakToken++;
  try { if (player) { player.pause(); player.onended && player.onended(); } } catch (e) {}
  try { if (TTS && (speechSynthesis.speaking || speechSynthesis.pending)) speechSynthesis.cancel(); } catch (e) {}
  if (A.speaking) { A.speaking = false; refreshSpeaking(); }
}
function refreshSpeaking(){
  const b = document.querySelector(".bb-stop");
  if (b) { b.dataset.act = A.speaking ? "bot-zitto" : "bot-ripeti"; b.textContent = A.speaking ? "Basta voce" : "Riascolta"; }
  if (VC.on) vcPaint();
}

function sheetBot(){
  const msgs = A.msgs.map((m, k) => m.role === "user" ? `<div class="b-msg me">${esc(m.content)}</div>` : `
    <div class="b-msg bot${m.err ? " b-err" : ""}">${esc(m.content)}${(m.auto || []).length ? `<div class="b-cars">${m.auto.slice(0, 3).map(c => `
      <button class="b-car" data-act="bot-open" data-v="${c.id}">${c.foto ? `<img src="${esc(c.foto)}" alt="" loading="lazy">` : `<span class="b-noimg">${CAR_SVG}</span>`}
        <span class="b-car-t"><b>${esc(c.nome)}</b><span>${esc([c.anno, c.zona].filter(Boolean).join(" · "))}</span>
        <span>${eur(c.prezzo)} · <em>${c.guadagno_da === c.guadagno_a ? "+" + eur(c.guadagno_a) : "+" + num(Math.max(c.guadagno_da, 0)) + "–" + num(c.guadagno_a) + " €"}</em></span></span></button>`).join("")}</div>` : ""}
      ${m.filtri && m.filtri.totale ? `<button class="btn btn-blue b-all" data-act="bot-catalogo" data-v="${k}">Vedi ${m.filtri.totale === 1 ? "l'auto" : "tutte le " + m.filtri.totale + " auto"} sullo schermo</button>` : ""}</div>`).join("");
  const micNote = !SR ? `<p class="hint b-note">${IN_APP ? "Qui dentro l'app il microfono non funziona: apri scovo.onrender.com in Safari o Chrome per parlare. Intanto puoi scrivere." : "Questo browser non ascolta la voce: scrivi la domanda, ti rispondo lo stesso" + (TTS ? " a voce." : ".")}</p>` : "";
  const ideas = A.msgs.length ? "" : `<div class="b-ideas"><p class="hint">${SR ? "Tocca il microfono e parla, oppure scrivi." : ""} Per esempio:</p>${BOT_IDEE.map(t => `<button class="pill" data-act="bot-idea" data-v="${esc(t)}">${esc(t)}</button>`).join("")}</div>`;
  return `
  <div class="sheet-wrap" data-act="close-bg"><div class="sheet bot-sheet" role="dialog" aria-modal="true" aria-labelledby="bh">
    <div class="sheet-head"><h2 id="bh">Chiedi a Scovo</h2><div style="display:flex;gap:4px;align-items:center">
      ${TTS ? `<button class="link" data-act="bot-voice" aria-pressed="${A.voice}">${A.voice ? "Voce sì" : "Voce no"}</button>` : ""}<button class="link" data-act="close">Chiudi</button></div></div>
    <div class="sheet-body bot-body" id="bot-body" aria-live="polite">${micNote}${ideas}${msgs}
      ${A.busy ? `<div class="b-msg bot b-wait"><span class="spin" aria-hidden="true"></span>Ci penso…</div>` : ""}
      <div class="b-live" id="bot-live" ${A.listening ? "" : "hidden"}>Ti ascolto…</div>
    </div>
    <form class="bot-foot" id="bot-form">
      <input id="bot-text" placeholder="${SR ? "Parla o scrivi qui" : "Scrivi la tua domanda"}" autocomplete="off" value="${esc(A.text)}" enterkeyhint="send">
      <button type="button" class="bot-mic${A.listening ? " on" : ""}${SR ? "" : " off"}" data-act="bot-mic" aria-label="${A.listening ? "Smetti di ascoltare" : "Parla"}">${MIC}</button>
      <button type="submit" class="bot-send" aria-label="Invia"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>
    </form>
  </div></div>`;
}
function botScroll(){ const b = document.getElementById("bot-body"); if (b) b.scrollTop = b.scrollHeight; }
function botRender(){ if (S.sheet === "bot") { render({keep:true}); botScroll(); } }

/* mette tutte le auto trovate nell'elenco principale, come un catalogo */
function showCatalog(f, testo){
  if (!f) return;
  S.brands = f.marca ? [f.marca] : [];
  S.min = f.prezzo_min ? String(f.prezzo_min) : ""; S.max = f.prezzo_max ? String(f.prezzo_max) : "";
  S.stato = f.stato || "tutte";
  if (f.sconto != null) S.sconto = Math.min(10, Number(f.sconto));
  if (f.ordina) S.sort = f.ordina;
  S.bot = {testo: testo || "", modello: f.modello || "", guadagno_min: f.guadagno_min || 0, anno_min: f.anno_min || 0, km_max: f.km_max || 0, fonte: f.fonte || ""};
  saveFilters();
  S.sheet = null;
  if (S.route !== "affari") { S.scroll.affari = 0; go("affari"); }
  else render({top:true});
  if (!S.cars) loadCars(true);
}

async function botAsk(text){
  text = (text || "").trim(); if (!text || A.busy) return null;
  const i = document.getElementById("bot-text"); if (i) i.blur();      // chiude la tastiera: si vede la risposta
  A.msgs.push({role: "user", content: text}); A.text = ""; A.busy = true;
  botRender();
  let r;
  try {
    r = await api("/api/assistente", {method: "POST", body: {messages: A.msgs.filter(m => !m.err).slice(-12).map(m => ({role: m.role, content: m.content}))}});
  } catch (e) {
    A.busy = false;
    const msg = e.status === 429 ? (e.message || "Hai fatto tante domande oggi: riprova domani o entra col tuo account.") : e.message || "Non riesco a rispondere adesso, riprova.";
    A.msgs.push({role: "assistant", content: msg, err: true}); botRender(); VC.last = msg; return {talking: speak(msg, VC.on)};
  }
  A.busy = false;
  const risposta = r.risposta || "Non ho trovato una risposta.";
  A.msgs.push({role: "assistant", content: risposta, auto: r.auto || [], filtri: r.filtri || null});
  VC.last = risposta;
  const talking = speak(risposta, VC.on);
  if (r.filtri && r.filtri.totale > 0) showCatalog(r.filtri, risposta);     // le auto trovate vanno tutte sullo schermo
  else if (VC.on && S.sheet !== "bot") vcPaint();
  else botRender();
  return {talking};
}

const SR_ERR = {
  "not-allowed": "Il microfono è bloccato: permettilo nelle impostazioni del browser, oppure scrivi la domanda.",
  "service-not-allowed": IN_APP ? "Qui dentro l'app il microfono non va: apri il sito in Safari o Chrome. Intanto scrivi." : "Il riconoscimento vocale non è attivo su questo telefono: scrivi la domanda.",
  "audio-capture": "Non trovo il microfono: scrivi la domanda.",
  "network": "Per capire la voce serve internet: riprova o scrivi.",
  "no-speech": "Non ti ho sentito: tocca il microfono e parla subito."
};
function botListen(){
  if (!SR) {
    toast(IN_APP ? "Apri il sito in Safari o Chrome per parlare. Qui puoi scrivere." : "Questo browser non ascolta la voce: scrivi la domanda.");
    const i = document.getElementById("bot-text"); if (i) i.focus();
    return;
  }
  if (A.listening && A.rec) { try { A.rec.stop(); } catch (e) {} return; }
  stopVoice();
  let rec;
  try { rec = new SR(); } catch (e) { toast(SR_ERR["service-not-allowed"]); return; }
  A.rec = rec;
  rec.lang = "it-IT"; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
  let finalText = "", interimText = "", failed = false;
  const guard = setTimeout(() => { try { rec.stop(); } catch (e) {} }, 15000);
  rec.onresult = ev => {
    interimText = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      if (ev.results[i].isFinal) finalText += ev.results[i][0].transcript; else interimText += ev.results[i][0].transcript;
    }
    const live = document.getElementById("bot-live"); if (live) { live.hidden = false; live.textContent = (finalText + " " + interimText).trim() || "Ti ascolto…"; }
  };
  rec.onerror = ev => { if (ev.error === "aborted") return; failed = true; toast(SR_ERR[ev.error] || "Non ho capito: riprova o scrivi la domanda."); };
  rec.onend = () => {
    clearTimeout(guard);
    A.listening = false; A.rec = null;
    const said = (finalText || interimText).trim();      // iPhone a volte chiude senza risultato "finale"
    if (said) botAsk(said); else { if (!failed && S.sheet === "bot") toast(SR_ERR["no-speech"]); botRender(); }
  };
  A.listening = true; botRender();
  try { rec.start(); } catch (e) { clearTimeout(guard); A.listening = false; A.rec = null; toast(SR_ERR["service-not-allowed"]); botRender(); }
}

/* ---------- Conversazione a mani libere ----------
   Tocchi "Chiedi" una volta: ascolta, capisce quando hai finito di parlare, risponde a voce
   e si rimette ad ascoltare. Le auto trovate restano sullo schermo. Tocchi la bolla per interromperlo. */
const VC = {on: false, state: "", heard: "", last: "", stream: null, ctx: null, mr: null, raf: 0, lv: 0, sr: null};
const VC_TXT = {ascolto: "Ti ascolto…", capisco: "Ho capito, un attimo…", penso: "Ci penso…", parlo: "", pausa: "In pausa: tocca per parlare"};
function vcDock(){
  if (!VC.on) return "";
  const cap = VC.state === "parlo" ? VC.last : VC.state === "ascolto" && VC.heard ? VC.heard : VC_TXT[VC.state] || "";
  return `<div class="vc-dock" role="region" aria-label="Conversazione con Scovo">
    <button class="vc-orb vc-${VC.state}" data-act="vc-orb" aria-label="${VC.state === "parlo" ? "Interrompi e parla" : VC.state === "ascolto" ? "Ho finito di parlare" : "Parla"}"><span class="vc-core"></span><span class="vc-ring"></span></button>
    <div class="vc-txt"><b>${VC.state === "parlo" ? "Scovo" : VC.state === "ascolto" ? "Tu" : "Scovo"}</b><span id="vc-cap">${esc(cap)}</span></div>
    <button class="vc-btn" data-act="vc-chat" aria-label="Scrivi o vedi la conversazione"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg></button>
    <button class="vc-btn vc-x" data-act="vc-end" aria-label="Chiudi la conversazione"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
  </div>`;
}
function vcPaint(){
  if (S.sheet === "bot") return;
  const old = document.querySelector(".vc-dock");
  const html = vcDock();
  if (!old) { if (html) render({keep:true}); return; }
  if (!html) { old.remove(); return; }
  const t = document.createElement("div"); t.innerHTML = html; old.replaceWith(t.firstElementChild);
}
function vcSet(st, heard){ VC.state = st; if (heard != null) VC.heard = heard; vcPaint(); }

async function vcStart(){
  stopVoice(); unlockVoice();
  if (!SR && !EARS()) {          // niente microfono utilizzabile: si scrive
    toast(IN_APP ? "Apri il sito in Safari o Chrome per parlare. Qui puoi scrivere." : "Questo browser non ascolta la voce: scrivi la domanda.");
    S.sheet = "bot"; render({keep:true}); return;
  }
  // l'audio va "acceso" dentro il tocco (iPhone)
  if (EARS() && !VC.ctx) { try { VC.ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) {} }
  try { VC.ctx && VC.ctx.resume(); } catch (e) {}
  VC.on = true; VC.heard = ""; if (S.sheet === "bot") S.sheet = null;
  render({keep:true});
  vcListen();
}
function vcEnd(){
  VC.on = false; VC.state = ""; stopVoice(); vcStopMic(true);
  if (VC.sr) { try { VC.sr.abort(); } catch (e) {} VC.sr = null; }
  vcPaint(); render({keep:true});
}
function vcStopMic(release){
  cancelAnimationFrame(VC.raf);
  if (VC.mr && VC.mr.state !== "inactive") { try { VC.mr.stop(); } catch (e) {} }
  if (release && VC.stream) { VC.stream.getTracks().forEach(t => t.stop()); VC.stream = null; }
}
async function vcListen(){
  if (!VC.on) return;
  stopVoice();
  vcSet("ascolto", "");
  if (EARS()) return vcRecord();
  return vcSpeechApi();
}
/* ascolto col riconoscimento del telefono (se non c'è la trascrizione sul server) */
function vcSpeechApi(){
  let rec; try { rec = new SR(); } catch (e) { toast(SR_ERR["service-not-allowed"]); return vcEnd(); }
  VC.sr = rec; rec.lang = "it-IT"; rec.interimResults = true; rec.continuous = false;
  let fin = "", inter = "", errored = "";
  rec.onresult = ev => { inter = ""; for (let i = ev.resultIndex; i < ev.results.length; i++) { if (ev.results[i].isFinal) fin += ev.results[i][0].transcript; else inter += ev.results[i][0].transcript; }
    VC.heard = (fin + " " + inter).trim(); const c = document.getElementById("vc-cap"); if (c) c.textContent = VC.heard; };
  rec.onerror = ev => { errored = ev.error; };
  rec.onend = () => {
    VC.sr = null; if (!VC.on) return;
    const said = (fin || inter).trim();
    if (said) return vcSend(said);
    if (errored && errored !== "no-speech" && errored !== "aborted") { toast(SR_ERR[errored] || "Il microfono non funziona: scrivi la domanda."); return vcEnd(); }
    vcSet("pausa");
  };
  try { rec.start(); } catch (e) { toast(SR_ERR["service-not-allowed"]); vcEnd(); }
}
/* ascolto registrando: capisce da solo quando hai smesso di parlare, poi trascrive sul server */
async function vcRecord(){
  try {
    if (!VC.stream || !VC.stream.active) VC.stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
  } catch (e) {
    toast(e && e.name === "NotAllowedError" ? SR_ERR["not-allowed"] : "Non riesco ad aprire il microfono: scrivi la domanda.");
    if (SR) return vcSpeechApi();
    return vcEnd();
  }
  if (!VC.on) return vcStopMic(true);
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  const mime = types.find(t => { try { return MediaRecorder.isTypeSupported(t); } catch (e) { return false; } }) || "";
  let mr; try { mr = new MediaRecorder(VC.stream, mime ? {mimeType: mime} : undefined); } catch (e) { if (SR) return vcSpeechApi(); return vcEnd(); }
  VC.mr = mr; const parts = [];
  mr.ondataavailable = e => { if (e.data && e.data.size) parts.push(e.data); };
  let spoke = false, aborted = false;
  mr.onstop = async () => {
    cancelAnimationFrame(VC.raf);
    if (!VC.on || aborted || VC.mr !== mr) return;
    VC.mr = null;
    if (!spoke) return vcSet("pausa");
    vcSet("capisco");
    const blob = new Blob(parts, {type: mr.mimeType || mime || "audio/webm"});
    const headers = {"Content-Type": blob.type || "audio/webm"}; if (S.token) headers.Authorization = "Bearer " + S.token;
    try {
      const r = await fetch("/api/trascrivi", {method: "POST", headers, body: blob});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || d.errore || "Non ho capito");
      if (!VC.on) return;
      if (!(d.testo || "").trim()) { toast("Non ho capito bene: ripeti?"); return vcListen(); }
      vcSend(d.testo);
    } catch (e) { toast(e.message || "Non ho capito: riprova"); if (VC.on) vcSet("pausa"); }
  };
  mr.start(250);
  // misura del volume: si vede sulla bolla e serve a capire quando hai finito
  let an = null, data = null;
  try { const src = VC.ctx.createMediaStreamSource(VC.stream); an = VC.ctx.createAnalyser(); an.fftSize = 1024; src.connect(an); data = new Uint8Array(an.fftSize); } catch (e) {}
  const t0 = performance.now(); let lastVoice = t0, noise = 0.012;
  const tick = () => {
    if (VC.mr !== mr) return;
    const now = performance.now();
    let rms = 0;
    if (an) { an.getByteTimeDomainData(data); for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; rms += v * v; } rms = Math.sqrt(rms / data.length); }
    if (!spoke && now - t0 < 400) noise = Math.max(noise, rms * 1.2);          // rumore di fondo dei primi istanti
    const thr = Math.max(0.025, noise * 2.2);
    if (rms > thr) { spoke = true; lastVoice = now; }
    VC.lv = Math.min(1, rms * 9); const o = document.querySelector(".vc-orb"); if (o) o.style.setProperty("--lv", VC.lv.toFixed(2));
    const silence = now - lastVoice;
    if ((spoke && silence > 1100) || (!spoke && now - t0 > 7000) || now - t0 > 20000 || (!an && now - t0 > 6000 && (spoke = true))) { try { mr.stop(); } catch (e) {} return; }
    VC.raf = requestAnimationFrame(tick);
  };
  VC.abortRec = () => { aborted = true; try { mr.stop(); } catch (e) {} };
  VC.raf = requestAnimationFrame(tick);
}
async function vcSend(text){
  if (!VC.on) return;
  VC.heard = text; vcSet("penso");
  vcStopMic(true);                         // iPhone: col microfono aperto la voce esce bassa dall'auricolare
  const res = await botAsk(text);          // risposta arrivata, la voce sta partendo
  if (!VC.on) return;
  if (!res) return vcListen();
  vcSet("parlo");
  await res.talking;
  if (VC.on && VC.state === "parlo") vcListen();
}
function vcOrb(){
  unlockVoice();
  if (VC.state === "parlo" || VC.state === "penso") { stopVoice(); return vcListen(); }      // lo interrompi e parli tu
  if (VC.state === "ascolto") { if (VC.mr) { try { VC.mr.stop(); } catch (e) {} } else if (VC.sr) { try { VC.sr.stop(); } catch (e) {} } return; }
  vcListen();
}

function tabBar(){
  const cur = S.route === "auto" ? (S.fromInc || "affari") : S.route;
  return `<nav class="tabs" aria-label="Sezioni">${TABS.filter(([id]) => tabOk(id)).map(([id,l,p]) => `<button data-act="tab" data-v="${id}" aria-current="${cur === id ? "page" : "false"}"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>${l}</button>`).join("")}</nav>`;
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
  else if (S.paywall && S.token && (isList(S.route) || S.route === "auto")) { html = viewPaywall() + tabBar(); }
  else if (S.route === "auto") { html = viewDetail(); }
  else html = (S.route === "vendi" ? viewVendi() : S.route === "ricambi" ? viewRicambi() : S.route === "profilo" ? viewProfilo() : viewList()) + tabBar();
  if (canBrowse()) {
    // pulsante "Chiedi" tolto (sarà sostituito)
    if (VC.on && S.sheet !== "bot" && S.route !== "auto") html += vcDock();
    html += S.sheet === "filters" ? sheetFilters() : S.sheet === "parts" ? sheetParts() : S.sheet === "comprata" ? sheetComprata() : S.sheet === "bot" ? sheetBot() : "";
    const t = S.route === "auto" ? ((S.cars || []).find(c => c.id === S.sel) || {}).nome : ({affari:"Affari", incidentate:"Incidentate", economiche:"Sotto 3.000 €", vendi:"Vendi", ricambi:"Ricambi", profilo:"Profilo"})[S.route];
    document.title = (t ? t + " · " : "") + "Scovo";
  }
  $app.innerHTML = html;
  $app.classList.toggle("vc-on", VC.on);
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
function viewPaywall(){
  const ab = (S.metodi && S.metodi.abbonamento) || {};
  const prova = ab.prova_giorni ? `<span class="meta">I primi ${ab.prova_giorni} giorni sono gratis: puoi disdire quando vuoi.</span>` : `<span class="meta">Puoi disdire quando vuoi dal tuo profilo.</span>`;
  return svcHead("Scovo", "Abbonamento") + `<main class="list"><div class="empty paywall">
    <strong style="font:800 24px var(--disp)">Le auto sotto prezzo, ogni giorno</strong>
    <ul class="checks" style="text-align:left;margin:4px 0 6px">
      <li>Auto di privati sotto il prezzo di mercato a Milano, Ravenna e Roma</li>
      <li>Guadagno già calcolato, con i pezzi da cambiare</li>
      <li>Controllo foto e prezzi realistici, aggiornati più volte al giorno</li></ul>
    ${ab.prezzo ? `<div class="price-big">${esc(ab.prezzo)}</div>` : ""}
    ${ab.attivo ? `<button class="l-btn l-btn-blue" data-act="abbonati" style="width:100%" ${S.payBusy ? "disabled" : ""}>${S.payBusy ? "Apro il pagamento…" : "Abbonati con carta"}</button>${prova}
      <span class="meta" style="font-size:13px">Il pagamento è gestito da Stripe: Scovo non vede né salva i dati della tua carta.</span>`
      : `<span class="meta">I pagamenti online stanno per arrivare. Per attivare l'account scrivici dalla pagina di contatto.</span>`}
    <button class="link" data-act="logout">Esci</button></div></main>`;
}
async function abbonati(){
  if (S.payBusy) return; S.payBusy = true; render({keep:true});
  try { const d = await api("/api/abbonamento/checkout", {method: "POST", body: {}}); location.href = d.url; }
  catch (e) { S.payBusy = false; toast(e.message); render({keep:true}); }
}
async function portaleAbbonamento(){
  try { const d = await api("/api/abbonamento/portale", {method: "POST", body: {}}); location.href = d.url; }
  catch (e) { toast(e.message); }
}
function viewGuestLock(){
  const t = ({vendi:"Vendi", ricambi:"Ricambi", profilo:"Profilo"})[S.route];
  return svcHead(t, "Serve un account") + `<main class="list"><div class="empty">
    <strong style="font:700 21px var(--disp)">Entra per usare ${t === "Profilo" ? "il tuo profilo" : t}</strong>
    <span class="meta">${t === "Vendi" ? "Dalla targa: auto esatta, prezzo giusto e annuncio pronto." : t === "Ricambi" ? "Dalla targa: i pezzi compatibili al prezzo più basso." : "Il tuo account, le auto che hai contattato e le impostazioni."}</span>
    <a class="l-btn l-btn-blue" href="#/entra" style="width:100%">Entra</a><a class="l-link" href="#/prova">Non hai un account? Chiedi di provare Scovo</a></div></main>`;
}
async function talk(){
  // solo per gli annunci con il numero: chiama senza lasciare la pagina
  if (!S.token && !guest()) { toast("Entra per contattare il venditore"); go("entra"); return; }
  const d = S.detail[S.sel]; if (!d || d.err) return;
  try {
    const r = await api("/api/affari/" + S.sel + "/contatto", {method: "POST"});
    segnaAperta();
    if (r.tel) location.href = "tel:" + r.tel;
    else if (MOBILE) location.href = r.url;
    else window.open(r.url, "_blank", "noopener");
  } catch (e) { toast(e.message); }
  render({keep:true});
}
// Sul telefono l'annuncio si apre nella stessa scheda: Subito, AutoScout24 e Facebook aprono la loro app e
// una scheda nuova resterebbe vuota (about:blank). Tornando indietro Scovo riapre la stessa auto.
const MOBILE = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent || "") || (navigator.maxTouchPoints > 1 && /Mac/.test(navigator.platform || ""));
function segnaAperta(){
  const car = (S.cars || []).find(c => c.id === S.sel); if (car) car.aperta = true;
  if (S.detail[S.sel]) S.detail[S.sel].aperta = true;
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
    case "tab": if (v === "incentivi") { location.href = "/incentivi/"; return; } if (v === S.route) { window.scrollTo({top: 0, behavior: "smooth"}); return; } go(v); return;
    case "open": if (VC.on) vcEnd(); S.fromList = true; S.fromInc = S.route === "affari" ? null : S.route; go("auto/" + v); return;
    case "back": if (S.fromList && S.route === "auto") { S.fromList = false; history.back(); } else go(S.fromInc || "affari", true); return;
    case "reload": loadCars(true); return;
    case "talk": talk(); return;
    case "talk-link":  // link vero in una nuova scheda: Scovo resta qui com'era
      if (!S.token && !guest()) { e.preventDefault(); toast("Entra per contattare il venditore"); go("entra"); return; }
      syncCookie(); segnaAperta(); setTimeout(() => render({keep:true}), 300); return;
    case "desc": S.descOpen = true; break;
    case "brand": S.brands = S.brands.includes(v) ? S.brands.filter(x => x !== v) : S.brands.concat([v]); saveFilters(); break;
    case "band": setBand(v); saveFilters(); break;
    case "filters": S.sheet = "filters"; break;
    case "parts": S.sheet = "parts"; break;
    case "close": case "close-bg": if (A.rec) { try { A.rec.abort(); } catch (err) {} } if (S.sheet === "bot") stopVoice(); S.sheet = null; break;
    case "bot": {
      // dal bottone "Chiedi": conversazione a voce, un tocco solo
      if ((t.dataset.voce || t.classList.contains("bb-ask")) && (SR || EARS())) { vcStart(); return; }
      stopVoice(); unlockVoice(); S.sheet = "bot"; render({keep:true}); botScroll();
      setTimeout(() => { const i = document.getElementById("bot-text"); if (i) i.focus(); }, 50);
      return;
    }
    case "vc-orb": vcOrb(); return;
    case "vc-end": vcEnd(); return;
    case "vc-chat": if (VC.abortRec) VC.abortRec(); if (VC.sr) { try { VC.sr.abort(); } catch (err) {} } VC.on = false; vcStopMic(true); stopVoice(); S.sheet = "bot"; render({keep:true}); botScroll(); return;
    case "bot-idea": unlockVoice(); botAsk(v); return;
    case "bot-mic": unlockVoice(); botListen(); return;
    case "bot-voice": A.voice = !A.voice; store.set("voce", A.voice); if (!A.voice) stopVoice(); else unlockVoice(); break;
    case "bot-zitto": stopVoice(); return;
    case "bb-more": if (S.bot) S.bot.open = true; break;
    case "bot-ripeti": unlockVoice(); if (S.bot && S.bot.testo) speak(S.bot.testo, true); return;
    case "bot-catalogo": { const m = A.msgs[Number(v)]; if (m && m.filtri) showCatalog(m.filtri, m.content); return; }
    case "bot-open": S.sheet = null; S.fromList = true; go("auto/" + v); return;
    case "tip": S.tip = false; store.set("tip", false); break;
    case "reset": resetFilters(); stopVoice(); saveFilters(); break;
    case "carb": S.carb = S.carb.includes(v) ? S.carb.filter(x => x !== v) : S.carb.concat([v]); saveFilters(); break;
    case "centro": if (v === "pos" && !S.pos) chiediPosizione(false); S.centro = v; saveFilters(); break;
    case "unf": {
      if (v === "marca") { S.brands = []; S.modello = ""; } else if (v === "prezzo") { S.min = ""; S.max = ""; } else if (v === "km") { S.kmMin = ""; S.kmMax = ""; }
      else if (v === "anno") { S.annoMin = ""; S.annoMax = ""; } else if (v === "carb") S.carb = []; else if (v === "raggio") S.raggio = 0; else if (v === "stato") S.stato = "tutte";
      saveFilters(); break;
    }
    case "stato": S.stato = v; saveFilters(); break;
    case "zona": S.zona = v; saveFilters(); window.scrollTo(0, 0); break;
    case "raggio": S.raggio = Number(v); saveFilters(); window.scrollTo(0, 0); break;
    case "posizione": chiediPosizione(false); return;
    case "sconto": S.sconto = Number(v); saveFilters(); break;
    case "sort": S.sort = v; saveFilters(); break;
    case "esito": if (v === "comprata") { S.sheet = "comprata"; break; } sendEsito(v); return;
    case "comprata-ok": { const p = (document.getElementById("paid") || {}).value; S.sheet = null; sendEsito("comprata", p ? {bought_eur: Number(p)} : {}); return; }
    case "cerca-pezzi": { const d = S.detail[S.sel] || {}; R.pezzi = (d.pezzi || []).map(p => p.pezzo).slice(0, 8); R.stato = "form"; R.err = ""; S.sheet = null; go("ricambi"); return; }
    case "logout": logout(); return;
    case "abbonati": abbonati(); return;
    case "portale": portaleAbbonamento(); return;
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
$app.addEventListener("change", e => {
  const id = e.target.id, v = e.target.value;
  const map = {"f-pmin": "min", "f-pmax": "max", "f-kmin": "kmMin", "f-kmax": "kmMax", "f-amin": "annoMin", "f-amax": "annoMax"};
  if (id === "f-marca") { S.brands = v ? [v] : []; S.modello = ""; }
  else if (id === "f-modello") S.modello = v;
  else if (id === "f-raggio") S.raggio = v ? Number(v) : 0;
  else if (id === "f-sort") S.sort = v;
  else if (id === "q-pmax") S.max = v;
  else if (id === "q-kmax") S.kmMax = v;
  else if (id === "q-carb") { if (v === "__multi") return; S.carb = v ? [v] : []; }
  else if (map[id]) S[map[id]] = v;
  else return;
  saveFilters();
  const sb = document.querySelector(".sheet-body"), sy = sb ? sb.scrollTop : 0;
  render({keep: true});
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
window.addEventListener("online", () => { S.offline = false; if (canBrowse() && isList(S.route)) loadCars(true); });
window.addEventListener("offline", () => { S.offline = true; if (isList(S.route)) render({keep:true}); });
document.addEventListener("visibilitychange", () => { if (!document.hidden && canBrowse() && isList(S.route)) loadCars(); });

/* ---------- avvio ---------- */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => {}); });
}
S.metodi = {};
api("/api/auth/metodi").then(m => {
  S.metodi = m || {};
  document.querySelectorAll("[data-social]").forEach(a => { a.hidden = !S.metodi[a.dataset.social]; });
  document.querySelectorAll("[data-solo-senza-google]").forEach(a => { a.hidden = !!S.metodi.google; });
  if (!S.token && (S.route === "entra" || S.route === "prova")) render({keep:true});
  if (guest() && S.route === "vetrina") { const h = location.hash.replace(/^#\/?/, ""); go(/^(auto\/\d+|vendi|ricambi|profilo|incidentate|economiche)$/.test(h) ? h : "affari", true); }
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
(function home(){
  // pagina d'ingresso: link interni senza toccare l'indirizzo (l'indirizzo #/… è delle pagine del sito),
  // bordo della barra quando scorri, schede 3D che seguono il puntatore
  document.querySelectorAll('.hm a[href^="#"]:not([href^="#/"])').forEach(a => a.addEventListener("click", e => {
    const t = document.getElementById(a.getAttribute("href").slice(1));
    if (!t) return;
    e.preventDefault();
    t.scrollIntoView({behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
  }));
  const nav = document.getElementById("hm-nav"), stage = document.getElementById("hm-stage");
  if (nav) {
    const onScroll = () => nav.classList.toggle("is-scrolled", window.scrollY > 8);
    window.addEventListener("scroll", onScroll, {passive: true}); onScroll();
  }
  // spessore della targa 3D: fette una dietro l'altra
  const plate = document.getElementById("hm-p3");
  if (plate) for (let i = 1; i <= 12; i++) { const d = document.createElement("i"); d.className = "sl"; d.style.setProperty("--i", i); plate.prepend(d); }
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  // profondità che cambia scorrendo: il mazzo ruota, gli strati si separano
  const layers = document.querySelector(".hm-layers");
  let sraf = 0;
  const onDepth = () => {
    cancelAnimationFrame(sraf);
    sraf = requestAnimationFrame(() => {
      const vh = innerHeight;
      if (stage) stage.style.setProperty("--sp", Math.min(1, Math.max(0, scrollY / (vh * .9))).toFixed(3));
      if (layers) { const b = layers.getBoundingClientRect(); layers.style.setProperty("--p", Math.min(1, Math.max(0, (vh - b.top) / (vh * .9))).toFixed(3)); }
    });
  };
  if (stage || layers) { addEventListener("scroll", onDepth, {passive: true}); onDepth(); }
  if (!matchMedia("(hover: hover)").matches) return;
  const tilt = (el, target, kx, ky, onMove) => {
    let raf = 0;
    el.addEventListener("pointermove", e => {
      const b = el.getBoundingClientRect(), x = (e.clientX - b.left) / b.width - .5, y = (e.clientY - b.top) / b.height - .5;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => { if (onMove) onMove(); target.style.setProperty("--ry", (x * kx).toFixed(2) + "deg"); target.style.setProperty("--rx", (-y * ky).toFixed(2) + "deg"); });
    });
    el.addEventListener("pointerleave", () => { target.style.setProperty("--ry", "0deg"); target.style.setProperty("--rx", "0deg"); });
  };
  if (stage) tilt(stage, stage, 14, 10);
  const pst = document.getElementById("hm-p3-stage");
  if (pst && plate) tilt(pst, pst, 50, 26, () => plate.classList.add("is-live"));
})();
applyRoute();
syncCookie();

if (S.token) api("/api/me").then(u => { S.user = Object.assign({}, S.user, {name: u.name, email: u.email, role: u.role, permessi: u.permessi, ha_pagamento: u.ha_pagamento}); syncCookie();
  if (!S.pos && u.lat != null && u.lon != null) { S.pos = {lat: u.lat, lon: u.lon, t: 0}; store.set("pos", S.pos); }
  if (!perm("auto") && perm("incentivi")) { location.href = "/incentivi/"; return; }
  render({keep:true}); store.set("user", S.user); if (S.route === "profilo") render({keep:true}); }).catch(() => {});
