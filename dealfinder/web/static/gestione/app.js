/* Deal Finder — sito commercianti. Usa la stessa API dell'app futura. */
"use strict";

const $view = document.getElementById("view");
const state = { token: null, user: null, filters: { status: "opportunita", sort: "score", damaged: "1" } };

/* ---------- utilità ---------- */
function store(k, v) { try { v === undefined ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} }
function load(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = (v) => (v === null || v === undefined) ? "n.d." : Math.round(v).toLocaleString("it-IT") + " €";
const km = (v) => (v === null || v === undefined) ? "km n.d." : Math.round(v).toLocaleString("it-IT") + " km";
const FUEL = { diesel: "Diesel", benzina: "Benzina", gpl: "GPL", metano: "Metano", ibrida: "Ibrida", elettrica: "Elettrica" };
const STATUS = { opportunita: "Opportunità", da_verificare: "Da verificare" };
const FIELD = { make: "Marca", model: "Modello", year: "Anno", mileage_km: "Chilometri", fuel: "Carburante",
  price_eur: "Prezzo", gearbox: "Cambio", power_kw: "Potenza", version_raw: "Versione", photos: "Foto" };

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2600);
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  const res = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401 && path !== "/api/auth/login") { logout(); throw new Error("Sessione scaduta: accedi di nuovo"); }
  if (!res.ok) throw new Error((data && data.error) || "Errore del server (" + res.status + ")");
  return data;
}

function plate(value, cls = "") {
  return `<span class="plate ${cls}"><span class="band">I</span><span class="num">${esc(eur(value))}</span></span>`;
}

function logout() { state.token = null; state.user = null; store("df_token"); store("df_user"); location.hash = "#/login"; }

/* ---------- accesso ---------- */
function viewLogin() {
  document.getElementById("topbar").hidden = true;
  $view.innerHTML = `
    <section class="login">
      <span class="plate big"><span class="band">I</span><span class="num">Deal Finder</span></span>
      <h1>Accedi</h1>
      <p class="sub">Le auto sotto prezzo di oggi, con ricambi, rivendita e margine già calcolati.</p>
      <form class="form" id="login-form">
        <div class="field"><label for="email">Email</label><input id="email" type="email" autocomplete="username" required></div>
        <div class="field"><label for="pwd">Password</label><input id="pwd" type="password" autocomplete="current-password" required></div>
        <p class="error" id="login-err" hidden></p>
        <button class="btn primary" type="submit">Accedi</button>
      </form>
    </section>`;
  document.getElementById("login-form").onsubmit = async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("login-err");
    errEl.hidden = true;
    try {
      const r = await api("/api/auth/login", { method: "POST", body: { email: document.getElementById("email").value, password: document.getElementById("pwd").value } });
      state.token = r.token; state.user = r.user;
      store("df_token", r.token); store("df_user", JSON.stringify(r.user));
      location.hash = "#/";
    } catch (err) { errEl.textContent = err.message; errEl.hidden = false; }
  };
}

/* ---------- lista ---------- */
async function viewList() {
  const f = state.filters;
  $view.innerHTML = `
    <h1>Opportunità</h1>
    <p class="sub" id="list-sub">Caricamento…</p>
    <div class="filters">
      <div class="seg" role="group" aria-label="Stato">
        <button data-status="opportunita" aria-pressed="${f.status === "opportunita"}">Confermate</button>
        <button data-status="da_verificare" aria-pressed="${f.status === "da_verificare"}">Da verificare</button>
      </div>
      <select id="sort" aria-label="Ordina">
        <option value="score">Migliori prima</option>
        <option value="margin">Margine più alto</option>
        <option value="recent">Più recenti</option>
        <option value="price">Prezzo più basso</option>
      </select>
      <select id="damaged" aria-label="Danni">
        <option value="1">Sane e da sistemare</option>
        <option value="0">Solo sane</option>
      </select>
      <select id="source" aria-label="Fonte">
        <option value="">Tutte le fonti</option>
        <option value="subito">Subito</option>
        <option value="facebook">Facebook Marketplace</option>
      </select>
    </div>
    <div id="list"></div>`;
  ["sort", "damaged", "source"].forEach((id) => { const el = document.getElementById(id); el.value = f[id] || ""; el.onchange = () => { f[id] = el.value; renderList(); }; });
  $view.querySelectorAll("[data-status]").forEach((b) => b.onclick = () => {
    f.status = b.dataset.status;
    $view.querySelectorAll("[data-status]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    renderList();
  });
  renderList();
  api("/api/notifications/seen", { method: "POST" }).then(() => { document.getElementById("notif").hidden = true; }).catch(() => {});
}

async function renderList() {
  const f = state.filters, box = document.getElementById("list"), sub = document.getElementById("list-sub");
  const qs = new URLSearchParams({ status: f.status, sort: f.sort, damaged: f.damaged });
  if (f.source) qs.set("source", f.source);
  try {
    const r = await api("/api/opportunities?" + qs);
    sub.textContent = r.count === 1 ? "1 auto supera i tuoi criteri." : `${r.count} auto superano i tuoi criteri.`;
    if (!r.items.length) {
      box.innerHTML = `<div class="empty">${f.status === "opportunita"
        ? "Nessuna opportunità confermata per ora. Le nuove auto vengono analizzate ogni tre ore; intanto puoi guardare quelle da verificare."
        : "Nessuna auto da verificare con i filtri scelti."}</div>`;
      return;
    }
    box.innerHTML = `<div class="list">${r.items.map(rowHtml).join("")}</div>`;
  } catch (err) { box.innerHTML = `<div class="empty error">${esc(err.message)}</div>`; }
}

function rowHtml(c) {
  const name = [c.make, c.model].filter(Boolean).join(" ").replace(/-/g, " ");
  return `<a class="row" href="#/o/${c.id}">
    ${c.photo ? `<img class="thumb" src="${esc(c.photo)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<span class="thumb"></span>`}
    <div>
      <p class="row-title">${esc(c.title || name)}</p>
      <div class="facts"><span>${esc(c.year || "anno n.d.")}</span><span>${esc(km(c.km))}</span><span>${esc(FUEL[c.fuel] || c.fuel || "")}</span>
        <span>${esc(c.city || c.province || "")}</span><span>${esc(c.source_label)}</span></div>
      <div class="money"><span>Prezzo <b>${eur(c.price)}</b></span><span>Rivendita <b>${eur(c.resale_prudent)}</b></span>${c.parts_cost ? `<span>Ricambi <b>${eur(c.parts_cost)}</b></span>` : ""}</div>
      <div class="facts">
        <span class="tag risk-${esc(c.risk)}">Rischio ${esc(c.risk)}</span>
        ${["leggero", "medio"].includes(c.damage_class) ? `<span class="tag damage">Da sistemare (${esc(c.damage_class)})</span>` : ""}
        ${c.damage_class === "alto_rischio" ? `<span class="tag risk-alto">Guasto importante</span>` : ""}
        ${c.opened_by_me ? "<span>Già aperta da te</span>" : ""}
      </div>
    </div>
    ${plate(c.net_margin, c.status === "da_verificare" ? "check" : "")}
  </a>`;
}

/* ---------- scheda ---------- */
async function viewDetail(id) {
  $view.innerHTML = `<a class="back" href="#/">Torna alla lista</a><p class="sub">Caricamento…</p>`;
  let c;
  try { c = await api("/api/opportunities/" + id); }
  catch (err) { $view.innerHTML = `<a class="back" href="#/">Torna alla lista</a><div class="empty">${esc(err.message)}</div>`; return; }
  const v = c.valuation, costs = c.costs;
  const missing = new Set(c.missing_fields || []);
  const spec = (label, val, field) => `<div><dt>${label}</dt><dd class="${missing.has(field) ? "missing" : ""}">${missing.has(field) ? "Non indicato" : esc(val)}</dd></div>`;
  const costRows = [
    ["Acquisto", costs.acquisto], ["Ricambi" + (c.parts ? " (" + esc(c.parts_preferred) + ")" : ""), costs.ricambi],
    ["Trasporto", costs.trasporto], ["Pratiche e passaggio", costs.pratiche], ["Preparazione", costs.preparazione],
    ["Riserva garanzia", costs.riserva_garanzia], ["Riserva imprevisti", costs.riserva_imprevisti], ["IVA sul margine", costs.iva_sul_margine],
  ];
  $view.innerHTML = `
    <a class="back" href="#/">Torna alla lista</a>
    <div class="gallery">${(c.photos || []).map((p) => `<img src="${esc(p)}" alt="" loading="lazy" referrerpolicy="no-referrer">`).join("") || '<div class="empty">Nessuna foto</div>'}</div>
    <div class="detail">
      <div>
        <h1>${esc(c.title || [c.make, c.model].join(" "))}</h1>
        <p class="sub">${esc(c.source_label)}, ${esc(c.city || c.province || "zona n.d.")}, venditore ${esc(c.seller_type)}</p>

        <dl class="specs">
          ${spec("Anno", c.year, "year")}${spec("Chilometri", km(c.km), "mileage_km")}${spec("Carburante", FUEL[c.fuel] || c.fuel, "fuel")}
          ${spec("Cambio", c.gearbox, "gearbox")}${spec("Potenza", c.power_kw ? c.power_kw + " kW (" + Math.round(c.power_kw / 0.7355) + " CV)" : "", "power_kw")}
          ${spec("Versione", c.version, "version_raw")}
        </dl>

        <h2>Perché è stata selezionata</h2>
        <ul class="bullets">${(c.motivation || []).map((m) => `<li>${esc(m)}</li>`).join("")}</ul>
        ${v.confidence_reasons.length ? `<h2>Cosa resta da verificare</h2><ul class="bullets">${v.confidence_reasons.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}
        ${v.fraud_flags.length ? `<h2>Segnali di attenzione</h2><ul class="bullets">${v.fraud_flags.map((m) => `<li class="error">${esc(m)}</li>`).join("")}</ul>` : ""}

        ${c.damage.length ? `<h2>Danni rilevati</h2><ul class="bullets">${c.damage.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
          <p class="note">Rilevati da foto e descrizione: indicano un rischio, non sono una perizia.</p>` : ""}

        ${c.parts && c.parts.lines && c.parts.lines.length ? partsHtml(c.parts) : ""}

        <h2>Auto simili usate per la stima</h2>
        <p class="note">${v.n_comparables} auto, livello di somiglianza ${v.comparable_level} su 3. Prezzi corretti per anno e chilometri.</p>
        <div class="scroll-x"><table class="cmp"><thead><tr><th>Anno</th><th>Km</th><th>Venditore</th><th>Prezzo</th><th>Corretto</th><th></th></tr></thead><tbody>
          ${c.comparables.map((x) => `<tr><td>${esc(x.year)}</td><td>${esc(km(x.km))}</td><td>${esc(x.seller)}</td><td>${eur(x.price)}</td><td>${eur(x.adjusted_price)}</td>
            <td>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">Annuncio</a>` : ""}</td></tr>`).join("")}
        </tbody></table></div>

        <h2>Controlli prima di comprare</h2>
        <ul class="checklist">${(c.checks || []).map((t, i) => `<li><label><input type="checkbox" data-check="${i}"> <span>${esc(t)}</span></label></li>`).join("")}</ul>

        ${c.description ? `<h2>Descrizione dell'annuncio</h2><div class="desc">${esc(c.description)}</div>` : ""}
      </div>

      <aside>
        <div class="panel">
          <div class="verdict">${plate(c.net_margin, "big " + (c.status === "da_verificare" ? "check" : ""))}
            <p>${esc(STATUS[c.status] || c.status)}<br>margine netto stimato, soglia ${eur(c.threshold)}</p></div>
          <table class="bill">
            <tr class="plus"><td>Rivendita prudente (${c.resale_as === "commerciante" ? "come commerciante" : "a privati"})</td><td>${eur(v.resale_prudent)}</td></tr>
            ${costRows.filter(([, val]) => val).map(([l, val]) => `<tr class="minus"><td>${l}</td><td>${eur(val)}</td></tr>`).join("")}
            <tr class="total"><td>Margine netto</td><td>${eur(c.net_margin)}</td></tr>
          </table>
          <p class="note">Manodopera esclusa. Mercato privato ${eur(v.private_median)}, commercianti ${eur(v.dealer_median)}.
            ${c.liquidity_days ? "Auto simili vendute in circa " + Math.round(c.liquidity_days) + " giorni." : ""}
            I costi si cambiano nella pagina Costi.</p>
        </div>
        ${c.my_feedback ? `<p class="note">Il tuo ultimo stato: <b>${esc(c.my_feedback.status)}</b></p>` : ""}
      </aside>
    </div>
    <div class="actionbar">
      <button class="btn primary" id="open">Apri annuncio e contatta</button>
      <button class="btn" id="fb">Segna esito</button>
    </div>`;

  const key = "df_checks_" + id;
  let done = {}; try { done = JSON.parse(load(key) || "{}"); } catch (e) {}
  $view.querySelectorAll("[data-check]").forEach((cb) => {
    cb.checked = !!done[cb.dataset.check];
    cb.onchange = () => { done[cb.dataset.check] = cb.checked; store(key, JSON.stringify(done)); };
  });
  document.getElementById("open").onclick = async () => {
    const w = window.open("", "_blank");
    try {
      const r = await api(`/api/opportunities/${id}/open`, { method: "POST" });
      if (w) { w.opener = null; w.location = r.url; } else { location.href = r.url; }
    } catch (err) { if (w) w.close(); toast(err.message); }
  };
  document.getElementById("fb").onclick = () => feedbackDialog(id);
}

function partsHtml(p) {
  return `<h2>Ricambi</h2>
    <p class="note">Prezzi trovati online per il veicolo identificato (${esc(p.vehicle || "")}). Solo pezzi, manodopera esclusa.
      Totale ${eur(p.parts_cost_low)} – ${eur(p.parts_cost_high)}.</p>
    ${p.lines.map((l) => `<details class="part"><summary><span>${esc(l.label)}${l.probable_hidden ? " (probabile, non visibile)" : ""}</span>
        <span>${l.low != null ? eur(l.low) + " – " + eur(l.high) : "prezzo non trovato"}</span></summary>
        <ul>${(l.offers || []).map((o) => `<li>${esc(o.type)} ${eur(o.price_eur)}, ${esc(o.seller)} <a href="${esc(o.url)}" target="_blank" rel="noopener noreferrer">offerta</a></li>`).join("") || "<li>Nessuna offerta trovata</li>"}</ul>
      </details>`).join("")}`;
}

function feedbackDialog(id) {
  const d = document.createElement("div");
  d.className = "dialog";
  d.innerHTML = `<form class="sheet form" role="dialog" aria-modal="true" aria-labelledby="fb-title">
      <h2 id="fb-title">Com'è andata?</h2>
      <div class="field"><label for="fb-status">Stato</label><select id="fb-status">
        <option value="contattato">Ho contattato il venditore</option><option value="trattativa">In trattativa</option>
        <option value="comprata">Comprata</option><option value="venduta">Rivenduta</option><option value="scartata">Non mi interessa</option></select></div>
      <div class="grid2">
        <div class="field"><label for="fb-bought">Prezzo di acquisto</label><input id="fb-bought" inputmode="numeric"></div>
        <div class="field"><label for="fb-sold">Prezzo di rivendita</label><input id="fb-sold" inputmode="numeric"></div>
      </div>
      <div class="field"><label for="fb-reason">Note</label><input id="fb-reason" placeholder="Es. danni maggiori del previsto"></div>
      <p class="note">I prezzi reali servono a rendere più precise le stime per tutti.</p>
      <div style="display:flex;gap:8px"><button class="btn primary" type="submit">Salva esito</button><button class="btn" type="button" id="fb-cancel">Annulla</button></div>
    </form>`;
  document.body.appendChild(d);
  const close = () => d.remove();
  d.querySelector("#fb-cancel").onclick = close;
  d.onclick = (e) => { if (e.target === d) close(); };
  d.querySelector("#fb-status").focus();
  d.querySelector("form").onsubmit = async (e) => {
    e.preventDefault();
    const n = (sel) => (d.querySelector(sel).value || "").replace(/\D/g, "") || null;
    try {
      await api(`/api/opportunities/${id}/feedback`, { method: "POST", body: {
        status: d.querySelector("#fb-status").value, bought_eur: n("#fb-bought"), sold_eur: n("#fb-sold"),
        reason: d.querySelector("#fb-reason").value } });
      close(); toast("Esito salvato");
    } catch (err) { toast(err.message); }
  };
}

/* ---------- le mie auto ---------- */
async function viewActivity() {
  $view.innerHTML = `<h1>Le mie auto</h1><p class="sub">Le auto per cui hai segnato un esito.</p><div id="act"></div>`;
  const box = document.getElementById("act");
  try {
    const r = await api("/api/activity");
    box.innerHTML = r.items.length ? `<div class="scroll-x"><table class="cmp"><thead><tr><th>Auto</th><th>Stato</th><th>Prezzo annuncio</th><th>Acquisto</th><th>Rivendita</th></tr></thead><tbody>
      ${r.items.map((x) => `<tr><td><a href="#/o/${x.id}">${esc(x.title || "Annuncio " + x.id)}</a></td><td>${esc(x.status)}</td><td>${eur(x.price_eur)}</td><td>${eur(x.bought_eur)}</td><td>${eur(x.sold_eur)}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">Quando segni l'esito di un'auto dalla sua scheda, la trovi qui.</div>`;
  } catch (err) { box.innerHTML = `<div class="empty error">${esc(err.message)}</div>`; }
}

/* ---------- costi e preferenze ---------- */
async function viewProfile() {
  $view.innerHTML = `<h1>Costi e preferenze</h1><p class="sub">Il margine di ogni auto viene ricalcolato con questi valori.</p><div id="prof"></div>`;
  let u;
  try { u = await api("/api/me"); } catch (err) { document.getElementById("prof").innerHTML = `<div class="empty error">${esc(err.message)}</div>`; return; }
  const c = u.costs;
  const num = (id, label, val, help = "") => `<div class="field"><label for="${id}">${label}</label><input id="${id}" inputmode="numeric" value="${esc(val)}">${help ? `<small>${help}</small>` : ""}</div>`;
  document.getElementById("prof").innerHTML = `
    <form class="form" id="prof-form">
      <h2>Zona e budget</h2>
      <div class="field"><label for="provinces">Province</label><input id="provinces" value="${esc((u.provinces || []).join(", "))}"><small>Sigle separate da virgola, es. MI, MB, BG, BS</small></div>
      ${num("max_purchase", "Prezzo di acquisto massimo (€)", u.max_purchase)}
      <div class="field inline"><input type="checkbox" id="accept_damage" ${u.accept_damage ? "checked" : ""}><label for="accept_damage">Mostra anche auto poco incidentate</label></div>
      <div class="field"><label for="resale_as">Come rivendi le auto</label><select id="resale_as">
        <option value="privato">A privati, come privato</option><option value="commerciante">Come commerciante, con garanzia</option></select>
        <small>Cambia il prezzo di rivendita usato per il margine</small></div>
      <div class="field"><label for="preferred_parts">Ricambi che usi di solito</label><select id="preferred_parts">
        <option value="aftermarket">Aftermarket (compatibili)</option><option value="originale">Originali</option><option value="usato">Usati</option></select></div>
      <h2>Costi per auto</h2>
      <div class="grid2">
        ${num("transport_eur", "Trasporto (€)", c.transport_eur)}${num("paperwork_eur", "Pratiche e passaggio (€)", c.paperwork_eur)}
        ${num("preparation_eur", "Preparazione (€)", c.preparation_eur, "Tagliando, pulizia, piccoli interventi")}${num("warranty_reserve_eur", "Riserva garanzia (€)", c.warranty_reserve_eur, "Lascia 0 se vendi a privati senza garanzia")}
        ${num("contingency_pct", "Imprevisti auto sane (%)", Math.round(c.contingency_pct * 100))}${num("contingency_damaged_pct", "Imprevisti carrozzeria (%)", Math.round(c.contingency_damaged_pct * 100))}
        ${num("contingency_fault_pct", "Imprevisti guasti meccanici (%)", Math.round(c.contingency_fault_pct * 100))}
        ${num("contingency_high_risk_pct", "Imprevisti alto rischio (%)", Math.round(c.contingency_high_risk_pct * 100), "Motore, cambio, airbag, non parte")}
      </div>
      <div class="field inline"><input type="checkbox" id="vat_margin_scheme" ${c.vat_margin_scheme ? "checked" : ""}><label for="vat_margin_scheme">Applico l'IVA sul margine (solo se vendo come azienda)</label></div>
      <h2>Soglie di margine netto</h2>
      <div class="grid2">
        ${num("threshold_low_eur", "Auto economiche (€)", c.threshold_low_eur)}${num("threshold_high_eur", "Auto più care (€)", c.threshold_high_eur)}
        ${num("threshold_split_eur", "Confine tra le due fasce (€ di acquisto)", c.threshold_split_eur)}
        ${num("threshold_cheap_eur", "Auto economiche: margine minimo (€)", c.threshold_cheap_eur)}
        ${num("threshold_cheap_max_eur", "Auto economiche: fino a (€ di acquisto)", c.threshold_cheap_max_eur)}
      </div>
      <p class="note">La manodopera non è inclusa: valuta tu il lavoro della tua officina.</p>
      <button class="btn primary" type="submit">Salva costi</button>
    </form>`;
  document.getElementById("preferred_parts").value = u.preferred_parts || "aftermarket";
  document.getElementById("resale_as").value = u.resale_as || "privato";
  document.getElementById("prof-form").onsubmit = async (e) => {
    e.preventDefault();
    const g = (id) => document.getElementById(id);
    const int = (id) => parseInt(String(g(id).value).replace(/\D/g, "") || "0", 10);
    try {
      await api("/api/me", { method: "PUT", body: {
        provinces: g("provinces").value.split(",").map((s) => s.trim()).filter(Boolean),
        max_purchase: int("max_purchase"), accept_damage: g("accept_damage").checked, preferred_parts: g("preferred_parts").value, resale_as: g("resale_as").value,
        costs: { transport_eur: int("transport_eur"), paperwork_eur: int("paperwork_eur"), preparation_eur: int("preparation_eur"),
          warranty_reserve_eur: int("warranty_reserve_eur"), contingency_pct: int("contingency_pct") / 100,
          contingency_damaged_pct: int("contingency_damaged_pct") / 100,
          contingency_fault_pct: int("contingency_fault_pct") / 100, contingency_high_risk_pct: int("contingency_high_risk_pct") / 100, vat_margin_scheme: g("vat_margin_scheme").checked,
          threshold_low_eur: int("threshold_low_eur"), threshold_high_eur: int("threshold_high_eur"), threshold_split_eur: int("threshold_split_eur"),
          threshold_cheap_eur: int("threshold_cheap_eur"), threshold_cheap_max_eur: int("threshold_cheap_max_eur") } } });
      toast("Costi salvati");
    } catch (err) { toast(err.message); }
  };
}

/* ---------- gestione (amministratori) ---------- */
async function viewAdmin() {
  $view.innerHTML = `<h1>Gestione</h1><p class="sub">Stato delle fonti, qualità delle stime, costi AI e commercianti.</p><div id="adm"></div>`;
  const box = document.getElementById("adm");
  try {
    const [o, d, rq] = await Promise.all([api("/api/admin/overview"), api("/api/admin/dealers"), api("/api/admin/richieste")]);
    const table = (rows, cols) => rows.length ? `<table class="kv"><tr>${cols.map((c) => `<th>${esc(c[1])}</th>`).join("")}</tr>
      ${rows.map((r) => `<tr>${cols.map(([k, , f]) => `<td>${f ? f(r[k], r) : esc(r[k])}</td>`).join("")}</tr>`).join("")}</table>` : `<p class="note">Nessun dato.</p>`;
    const q = o.quality[0];
    const al = o.allarmi || [];
    box.innerHTML = `
      <div class="panel" style="margin-bottom:16px"><h2>Allarmi</h2>${al.length ? `<ul>${al.map((a) => `<li class="ko">${esc(a)}</li>`).join("")}</ul>` : '<p class="ok">Tutto regolare</p>'}</div>
      <div class="stats">
        <div class="panel"><h2>Ultimi lavori</h2>${table(o.jobs, [["job", "Lavoro"], ["finished_at", "Fine", (v) => esc(v ? new Date(v).toLocaleString("it-IT") : "in corso")], ["ok", "Esito", (v) => v ? '<span class="ok">ok</span>' : '<span class="ko">errore</span>']])}</div>
        <div class="panel"><h2>Nuovi annunci (24 ore)</h2>${table(o.new_last_24h, [["source", "Fonte"], ["n", "Annunci"]])}</div>
        <div class="panel"><h2>Qualità stime</h2>${q ? `<table class="kv"><tr><td>Casi misurati</td><td>${q.n_cases}</td></tr>
          <tr><td>Errore mediano</td><td>${q.median_abs_pct_error != null ? Math.round(q.median_abs_pct_error * 100) + "%" : "n.d."}</td></tr>
          <tr><td>Prudente sotto il prezzo finale</td><td>${q.prudent_coverage != null ? Math.round(q.prudent_coverage * 100) + "% (obiettivo 75%)" : "n.d."}</td></tr>
          <tr><td>Sconto trattativa dalle vendite</td><td>${q.suggested_negotiation_discount != null ? Math.round(q.suggested_negotiation_discount * 100) + "%" : "servono 10 vendite"}</td></tr></table>` : '<p class="note">Prima misura dopo una settimana di dati.</p>'}</div>
        <div class="panel"><h2>Uso AI oggi</h2>${table(o.ai_usage_today, [["task", "Attività"], ["calls", "Chiamate"], ["input_tokens", "Token in"], ["output_tokens", "Token out"], ["web_searches", "Ricerche"]])}</div>
      </div>
      <h2>Annunci per stato (7 giorni)</h2>${table(o.stages, [["stage", "Stato"], ["stage_reason", "Motivo"], ["n", "Annunci"]])}
      <h2>Vendi e Ricambi oggi</h2>${table(o.servizi_oggi || [], [["service", "Servizio"], ["n", "Usi"], ["ok", "Riusciti"]])}
      <h2>Richieste di accesso</h2>${table(rq.items, [["at", "Quando", (v) => esc(new Date(v).toLocaleString("it-IT"))], ["name", "Nome"], ["company", "Autosalone"], ["email", "Email"], ["phone", "Telefono"],
        ["id", "", (v, r) => `<button class="btn" data-prefill="${esc(JSON.stringify({n: r.name, c: r.company, e: r.email}))}">Crea account</button>`]])}
      <h2>Commercianti</h2>
      ${table(d.items, [["name", "Nome"], ["email", "Email"], ["role", "Ruolo"], ["opens", "Annunci aperti"], ["bought", "Comprate"],
        ["active", "Attivo", (v, r) => `<button class="btn" data-toggle="${r.id}" data-active="${v}">${v ? "Disattiva" : "Attiva"}</button>`]])}
      <h2>Nuovo commerciante</h2>
      <form class="form" id="new-dealer">
        <div class="grid2">
          <div class="field"><label for="nd-name">Nome</label><input id="nd-name" required></div>
          <div class="field"><label for="nd-company">Azienda</label><input id="nd-company"></div>
          <div class="field"><label for="nd-email">Email</label><input id="nd-email" type="email" required></div>
          <div class="field"><label for="nd-pwd">Password iniziale</label><input id="nd-pwd" minlength="8" required></div>
        </div>
        <button class="btn primary" type="submit">Crea account</button>
      </form>`;
    box.querySelectorAll("[data-toggle]").forEach((b) => b.onclick = async () => {
      await api("/api/admin/dealers/" + b.dataset.toggle, { method: "PATCH", body: { active: b.dataset.active !== "true" } });
      viewAdmin();
    });
    box.querySelectorAll("[data-prefill]").forEach((b) => b.onclick = () => {
      const v = JSON.parse(b.dataset.prefill);
      document.getElementById("nd-name").value = v.n || ""; document.getElementById("nd-company").value = v.c || "";
      document.getElementById("nd-email").value = v.e || ""; document.getElementById("nd-pwd").focus();
    });
    document.getElementById("new-dealer").onsubmit = async (e) => {
      e.preventDefault();
      const g = (id) => document.getElementById(id).value;
      try {
        await api("/api/admin/dealers", { method: "POST", body: { name: g("nd-name"), company: g("nd-company"), email: g("nd-email"), password: g("nd-pwd") } });
        toast("Account creato"); viewAdmin();
      } catch (err) { toast(err.message); }
    };
  } catch (err) { box.innerHTML = `<div class="empty error">${esc(err.message)}</div>`; }
}

/* ---------- navigazione ---------- */
async function checkNotifications() {
  if (!state.token) return;
  try {
    const r = await api("/api/notifications");
    const b = document.getElementById("notif");
    b.textContent = r.new_count; b.hidden = !r.new_count;
  } catch (e) {}
}

function route() {
  const h = location.hash || "#/";
  if (!state.token && h !== "#/login") { location.hash = "#/login"; return; }
  if (h === "#/login") return viewLogin();
  document.getElementById("topbar").hidden = false;
  document.getElementById("nav-admin").hidden = !(state.user && state.user.role === "admin");
  const name = h.startsWith("#/o/") ? "list" : (h.slice(2) || "list");
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
  window.scrollTo(0, 0);
  if (h.startsWith("#/o/")) return viewDetail(parseInt(h.slice(4), 10));
  if (h === "#/attivita") return viewActivity();
  if (h === "#/profilo") return viewProfile();
  if (h === "#/admin") return viewAdmin();
  return viewList();
}

state.token = load("df_token");
try { state.user = JSON.parse(load("df_user") || "null"); } catch (e) {}
window.addEventListener("hashchange", route);
route();
checkNotifications();
setInterval(checkNotifications, 5 * 60 * 1000);
