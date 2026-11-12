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
const zonaDi = (prov) => {
  const p = prov || [];
  const ra = ["RA", "FC", "RN", "FE", "BO"].some((x) => p.includes(x));
  const mi = ["MI", "MB", "BG", "BS"].some((x) => p.includes(x));
  return ra && mi ? "tutte" : ra ? "ravenna" : "milano";
};

async function viewAdmin() {
  $view.innerHTML = `<h1>Gestione</h1><p class="sub">Stato delle fonti, qualità delle stime, costi AI e commercianti.</p><div id="adm"></div><div id="adm-funnel"><p class="note">Carico i filtri del sito…</p></div><div id="adm-stato"><p class="note">Carico lo stato delle fonti…</p></div>`;
  const box = document.getElementById("adm");
  viewFunnel(document.getElementById("adm-funnel"));
  try {
    const oP = api("/api/admin/overview");      // lento con tanti annunci: si carica dopo, i commercianti subito
    const [d, rq] = await Promise.all([api("/api/admin/dealers"), api("/api/admin/richieste")]);
    const table = (rows, cols) => rows.length ? `<table class="kv"><tr>${cols.map((c) => `<th>${esc(c[1])}</th>`).join("")}</tr>
      ${rows.map((r) => `<tr>${cols.map(([k, , f]) => `<td>${f ? f(r[k], r) : esc(r[k])}</td>`).join("")}</tr>`).join("")}</table>` : `<p class="note">Nessun dato.</p>`;
    box.innerHTML = `
      <h2>Richieste di accesso</h2>${table(rq.items, [["at", "Quando", (v) => esc(new Date(v).toLocaleString("it-IT"))], ["name", "Nome"], ["company", "Autosalone"], ["email", "Email"], ["phone", "Telefono"],
        ["source", "Da", (v) => esc(v === "google" ? "Google" : v === "apple" ? "Apple" : "Modulo")],
        ["id", "", (v, r) => r.dealer_id ? `<button class="btn primary" data-approve="${v}">Attiva</button>`
          : `<button class="btn" data-prefill="${esc(JSON.stringify({n: r.name, c: r.company, e: r.email, id: v}))}">Crea account</button>`]])}
      <h2>Commercianti</h2>
      <div style="overflow-x:auto;margin:0 -16px;padding:0 16px">${table(d.items, [["name", "Nome"], ["email", "Email"], ["role", "Ruolo"], ["methods", "Entra con"], ["sessions", "Dispositivi"], ["opens", "Annunci aperti"], ["bought", "Comprate"],
        ["provinces", "Zona", (v, r) => r.role === "admin" ? "Tutte" : `<select data-zona="${r.id}">${[["milano", "Milano e dintorni"], ["ravenna", "Ravenna e dintorni"], ["tutte", "Tutte"]].map(([k, l]) =>
          `<option value="${k}" ${zonaDi(v) === k ? "selected" : ""}>${l}</option>`).join("")}</select>`],
        ["permessi", "Accesso a", (v, r) => r.role === "admin" ? "Tutto" : [["auto", "Auto"], ["incentivi", "Incentivi"], ["appuntamenti", "Appuntamenti (200 €)"]].map(([k, l]) =>
          `<label style="margin-right:.6rem;white-space:nowrap"><input type="checkbox" data-perm="${r.id}" data-k="${k}" ${(v || []).includes(k) ? "checked" : ""}> ${l}</label>`).join("")],
        ["paga_carta", "Pagamento", (v, r) => r.role === "admin" ? "" : `<label style="white-space:nowrap"><input type="checkbox" data-paga="${r.id}" ${v ? "checked" : ""}> Deve pagare con carta</label>${v ? ` <small class="note">${["active", "trialing"].includes(r.abbonamento_stato) ? "pagato" : "non ancora pagato"}</small>` : ""}`],
        ["active", "Attivo", (v, r) => `${v ? '<span class="ok">Attivo</span>' : '<b class="ko">Disattivato</b>'} <button class="btn" data-toggle="${r.id}" data-active="${v}">${v ? "Disattiva" : "Riattiva"}</button>`],
        ["id", "Accesso", (v, r) => `<button class="btn primary" data-link="${v}" data-who="${esc(r.email)}">Link di accesso</button> <button class="btn" data-pwd="${v}" data-who="${esc(r.email)}">Nuova password</button>`]])}</div>
      <h2>Nuovo commerciante</h2>
      <form class="form" id="new-dealer">
        <div class="grid2">
          <div class="field"><label for="nd-name">Nome</label><input id="nd-name" required></div>
          <div class="field"><label for="nd-company">Azienda</label><input id="nd-company"></div>
          <div class="field"><label for="nd-email">Email</label><input id="nd-email" type="email" required autocapitalize="off" autocorrect="off" spellcheck="false"></div>
          <div class="field"><label for="nd-pwd">Password iniziale</label><input id="nd-pwd" minlength="8" required autocapitalize="off" autocorrect="off" autocomplete="new-password" spellcheck="false"><small class="note">Scrivila esattamente così al cliente: maiuscole e minuscole contano.</small></div>
        </div>
        <div class="field"><label for="nd-zona">Zona</label><select id="nd-zona">
          <option value="milano">Milano e dintorni</option><option value="ravenna">Ravenna e dintorni</option><option value="tutte">Tutte</option></select></div>
        <fieldset class="field" style="border:0;padding:0"><legend>Accesso a</legend>
          <label style="display:inline-flex;gap:8px;align-items:center;margin:6px 18px 0 0"><input type="checkbox" id="nd-auto" checked style="width:20px;height:20px"> Auto (affari e vendi)</label>
          <label style="display:inline-flex;gap:8px;align-items:center;margin-top:6px"><input type="checkbox" id="nd-inc" style="width:20px;height:20px"> Incentivi</label>
          <label style="display:inline-flex;gap:8px;align-items:center;margin-top:6px"><input type="checkbox" id="nd-app" style="width:20px;height:20px"> Appuntamenti (200 €)</label></fieldset>
        <button class="btn primary" type="submit">Crea account</button>
      </form>`;
    box.querySelectorAll("[data-paga]").forEach((c) => c.onchange = async () => {
      try { await api("/api/admin/dealers/" + c.dataset.paga, { method: "PATCH", body: { paga_carta: c.checked } });
            toast(c.checked ? "Ora vede le auto solo dopo aver pagato con carta" : "Accesso completo senza pagamento"); viewAdmin(); }
      catch (e) { toast(e.message); c.checked = !c.checked; }
    });
    box.querySelectorAll("[data-toggle]").forEach((b) => b.onclick = async () => {
      await api("/api/admin/dealers/" + b.dataset.toggle, { method: "PATCH", body: { active: b.dataset.active !== "true" } });
      viewAdmin();
    });
    box.querySelectorAll("[data-link]").forEach((b) => b.onclick = async () => {
      try {
        const r = await api("/api/admin/dealers/" + b.dataset.link + "/link", { method: "POST" });
        const testo = "Ecco il tuo accesso a Scovo (apri il link, entri subito): " + r.url;
        if (navigator.share) { try { await navigator.share({ title: "Accesso a Scovo", text: testo }); return; } catch (e) { /* annullato */ } }
        try { await navigator.clipboard.writeText(r.url); toast("Link copiato: incollalo su WhatsApp. Vale " + r.giorni + " giorni."); }
        catch (e) { prompt("Copia questo link e mandalo a " + b.dataset.who + ":", r.url); }
      } catch (err) { toast(err.message); }
    });
    box.querySelectorAll("[data-pwd]").forEach((b) => b.onclick = async () => {
      const pwd = prompt("Nuova password per " + b.dataset.who + " (almeno 8 caratteri; maiuscole e minuscole contano):");
      if (pwd == null) return;
      if (pwd.trim().length < 8) { toast("Servono almeno 8 caratteri"); return; }
      try { await api("/api/admin/dealers/" + b.dataset.pwd, { method: "PATCH", body: { password: pwd.trim() } }); toast("Password cambiata: " + pwd.trim()); }
      catch (err) { toast(err.message); }
    });
    box.querySelectorAll("[data-zona]").forEach((sel) => sel.onchange = async () => {
      try { await api("/api/admin/dealers/" + sel.dataset.zona, { method: "PATCH", body: { zona: sel.value } }); toast("Zona aggiornata"); }
      catch (err) { toast(err.message); }
    });
    box.querySelectorAll("[data-perm]").forEach((c) => c.onchange = async () => {
      const id = c.dataset.perm;
      const permessi = [...box.querySelectorAll(`[data-perm="${id}"]`)].filter((x) => x.checked).map((x) => x.dataset.k);
      try { await api("/api/admin/dealers/" + id, { method: "PATCH", body: { permessi } }); toast("Accesso aggiornato: vale dal prossimo caricamento della pagina"); }
      catch (err) { toast(err.message); c.checked = !c.checked; }
    });
    box.querySelectorAll("[data-approve]").forEach((b) => b.onclick = async () => {
      await api("/api/admin/richieste/" + b.dataset.approve, { method: "POST" }); toast("Account attivato"); viewAdmin();
    });
    let fromRequest = null;
    box.querySelectorAll("[data-prefill]").forEach((b) => b.onclick = () => {
      const v = JSON.parse(b.dataset.prefill); fromRequest = v.id;
      document.getElementById("nd-name").value = v.n || ""; document.getElementById("nd-company").value = v.c || "";
      document.getElementById("nd-email").value = v.e || ""; document.getElementById("nd-pwd").focus();
    });
    document.getElementById("new-dealer").onsubmit = async (e) => {
      e.preventDefault();
      const g = (id) => document.getElementById(id).value;
      try {
        await api("/api/admin/dealers", { method: "POST", body: { name: g("nd-name"), company: g("nd-company"), email: g("nd-email"), password: g("nd-pwd"), zona: g("nd-zona"),
          permessi: [["nd-auto", "auto"], ["nd-inc", "incentivi"], ["nd-app", "appuntamenti"]].filter(([i]) => document.getElementById(i).checked).map(([, k]) => k) } });
        if (fromRequest) await api("/api/admin/richieste/" + fromRequest, { method: "POST" });
        toast("Account creato"); viewAdmin();
      } catch (err) { toast(err.message); }
    };
    const st = document.getElementById("adm-stato");
    oP.then((o) => {
      const q = (o.quality || [])[0];
      const al = o.allarmi || [];
      st.innerHTML = `<h2 style="margin-top:28px">Stato del sistema</h2>
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
      <h2>Vendi e Ricambi oggi</h2>${table(o.servizi_oggi || [], [["service", "Servizio"], ["n", "Usi"], ["ok", "Riusciti"]])}`;
    }).catch((err) => { st.innerHTML = `<p class="note">Stato delle fonti non disponibile ora (${esc(err.message)}).</p>`; });
  } catch (err) { box.innerHTML = `<div class="empty error">${esc(err.message)}</div>`; }
}

/* ---------- 4/11: auto entrate e uscite dal sito, regola per regola (tabella sito_funnel_giorno) ---------- */
const PASSI = { valutata: "Valutata (rivendita e margine)", no_loconte: "Loconte", scarti_cliente: "Scartate a mano", anno: "Anno (dal 2007)",
  km: "Km (al massimo 150.000)", telefono: "Numero del venditore", no_roma: "Roma", zona: "Zona (100 km da Milano, Ravenna, supercar)",
  venditore: "Venditore (privati e commercianti ammessi)", prezzo_intero: "Prezzo intero (niente rata/leasing)", jev: "Controllo AI (Jev)",
  margine_sito: "Costo dei difetti letti dall'AI", truffa: "Truffe", margine_minimo: "Margine minimo", tetto_commercianti: "Massimo 40 commercianti lontani" };
async function viewFunnel(el) {
  try {
    const d = await api("/api/admin/sito-funnel?giorni=7");
    if (!(d.giorni || []).length) { el.innerHTML = `<h2>Filtri del sito</h2><p class="note">${esc(d.errore || "Ancora nessun conteggio: arriva col primo elenco calcolato.")}</p>`; return; }
    el.innerHTML = `<h2>Filtri del sito: auto entrate e uscite per regola</h2>
      <p class="note">Ultimo elenco calcolato di ogni giorno. "Entrate" = auto arrivate alla regola, "Uscite" = auto tolte da quella regola; sotto ogni regola i motivi.</p>
      ${d.giorni.map((g) => {
        const princ = g.passi.filter((p) => !p.passo.includes("."));
        const fine = princ.length ? princ[princ.length - 1].entrate - princ[princ.length - 1].uscite : 0;
        return `<details class="panel" style="margin-bottom:12px" ${g === d.giorni[0] ? "open" : ""}><summary><b>${esc(new Date(g.giorno).toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" }))}</b> · ${fine} auto sul sito</summary>
          <div style="overflow-x:auto"><table class="kv"><tr><th>Regola</th><th>Entrate</th><th>Uscite</th></tr>
          ${g.passi.map((p) => { const [a, m] = p.passo.split(".");
            return m ? `<tr><td style="padding-left:1.4rem"><small>${esc(m === "fascia_richiesta" ? "di cui fascia richiesta" : m.replace(/_/g, " "))}</small></td><td><small>${m === "fascia_richiesta" ? p.entrate : ""}</small></td><td><small>${p.uscite}</small></td></tr>`
                     : `<tr><td><b>${esc(PASSI[a] || a)}</b></td><td>${p.entrate}</td><td class="${p.uscite ? "ko" : ""}">${p.uscite}</td></tr>`; }).join("")}
          </table></div></details>`; }).join("")}`;
  } catch (err) { el.innerHTML = `<p class="note">Filtri del sito non disponibili ora (${esc(err.message)}).</p>`; }
}

/* ---------- 12/11: Sito — ribassi, auto in cima, auto tolte, senza riavvio (endpoint /api/admin/sito) ---------- */
const SITO = { cima: [] };
const dtLocale = (iso) => { if (!iso) return ""; const d = new Date(iso); if (isNaN(d)) return "";
  const p = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
const dtTesto = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleString("it-IT", { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" }); };
const fasciaRiga = (da, eur) => `<tr><td><input type="number" min="0" step="500" value="${esc(da)}" data-f="da" aria-label="Da prezzo" style="width:8rem"></td>
  <td><input type="number" min="0" max="3000" step="50" value="${esc(eur)}" data-f="eur" aria-label="Euro tolti" style="width:7rem"></td>
  <td><button class="btn" data-f-via>Togli</button></td></tr>`;

const rigaTolta = (x) => `<tr><td>${esc(x.descrizione)}</td><td>${esc(x.motivo || "")}</td><td>${esc(dtTesto(x.creato_at))}</td><td><button class="btn" data-rimetti="${x.id}">Rimetti</button></td></tr>`;

async function viewSito() {
  $view.innerHTML = `<h1>Sito</h1><p class="sub">Modifiche senza riavvio: si vedono sul sito entro un minuto.</p><div id="sito"><p class="note">Carico…</p></div>`;
  const box = document.getElementById("sito");
  let d;
  try { d = await api("/api/admin/sito"); } catch (err) { box.innerHTML = `<div class="empty error">${esc(err.message)}</div>`; return; }
  SITO.cima = (d.in_cima || []).map((e) => ({ ...e }));     // descrizione e sul_sito il server li ignora
  const r = d.ribasso || {}, el = d.elenco || {};
  const fasce = String(r.schema || "").split(",").filter(Boolean).map((x) => x.split(":"));
  const statoRib = r.acceso_ora ? `<b class="ok">Accesi</b>${r.fino ? " fino a " + esc(dtTesto(r.fino)) : " senza scadenza"}` : `<b>Spenti</b>${r.attivo && r.fino ? " (scaduti il " + esc(dtTesto(r.fino)) + ")" : ""}`;
  box.innerHTML = `
    <div class="panel" style="margin-bottom:16px"><b>${el.auto || 0} auto sul sito</b>${el.aggiornato ? " · elenco del " + esc(dtTesto(el.aggiornato)) : ""}
      ${el.fonte === "database" ? ' · <span class="note">ripreso dal database all\'avvio, ricalcolo in corso</span>' : ""}${el.calcolo_in_corso ? ' · <span class="note">ricalcolo in corso</span>' : ""}</div>

    <h2>Ribassi del prezzo mostrato</h2>
    <div class="panel">
      <p>Ora: ${statoRib}${r.fonte === "codice" ? ' <small class="note">(nessuna impostazione salvata: valgono le variabili di Render, dove una data vuota vuol dire spenti; al primo salvataggio comanda questo pannello)</small>' : ""}</p>
      <p class="note">Comanda la spunta "Ribassi accesi". Con la spunta e "Fino a" vuoto i ribassi restano accesi finché non togli la spunta; con una data si spengono da soli a quell'ora. Senza spunta sono spenti, qualunque data ci sia.</p>
      <label style="display:inline-flex;gap:8px;align-items:center"><input type="checkbox" id="rb-on" ${r.attivo ? "checked" : ""} style="width:20px;height:20px"> Ribassi accesi</label>
      <table class="kv" style="max-width:420px;margin-top:10px"><thead><tr><th>Da prezzo dell'annuncio (€)</th><th>Euro tolti</th><th></th></tr></thead><tbody id="rb-fasce">${fasce.map(([a, b]) => fasciaRiga(a, b)).join("")}</tbody></table>
      <button class="btn" id="rb-add" style="margin-top:8px">Aggiungi fascia</button>
      <div class="field" style="max-width:320px;margin-top:12px"><label for="rb-fino">Fino a (ora italiana)</label><input type="datetime-local" id="rb-fino" value="${esc(dtLocale(r.fino))}"><small>Vuoto = accesi finché non togli la spunta. Con una data si spengono da soli a quell'ora.</small></div>
      <button class="btn primary" id="rb-salva">Salva ribassi</button>
      <p class="note">Il prezzo mostrato è quello dell'annuncio meno il ribasso della sua fascia (al massimo 3.000 €); il margine mostrato sale di altrettanto. Le regole del sito usano sempre il prezzo vero.</p>
    </div>

    <h2>Cerca un'auto sul sito</h2>
    <div class="field" style="max-width:420px"><input id="s-q" placeholder="Marca, modello, anno, zona o id annuncio" autocomplete="off"></div>
    <div id="s-ris"></div>

    <h2>Auto in cima</h2>
    <div class="panel"><div id="s-cima"></div>
      <details style="margin-top:10px"><summary>Aggiungi a mano (auto non trovata con la ricerca)</summary>
        <div class="grid2" style="margin-top:8px">
          <div class="field"><label for="c-id">Id annuncio</label><input id="c-id" inputmode="numeric"><small>Oppure marca, km e prezzo qui sotto</small></div>
          <div class="field"><label for="c-marca">Marca</label><input id="c-marca"></div>
          <div class="field"><label for="c-modello">Modello (facoltativo)</label><input id="c-modello"></div>
          <div class="field"><label for="c-km">Km</label><input id="c-km" inputmode="numeric"></div>
          <div class="field"><label for="c-prezzo">Prezzo dell'annuncio (€)</label><input id="c-prezzo" inputmode="numeric"></div>
          <div class="field"><label for="c-mostra">Prezzo da mostrare (€, facoltativo)</label><input id="c-mostra" inputmode="numeric"></div>
        </div><button class="btn" id="c-add">Aggiungi in cima</button></details>
    </div>

    <h2>Auto tolte dal sito</h2>
    <div class="panel">
      <details style="margin-top:10px"><summary>Togli a mano (per id o per marca, km e prezzo)</summary>
        <div class="grid2" style="margin-top:8px">
          <div class="field"><label for="t-id">Id annuncio</label><input id="t-id" inputmode="numeric"><small>Oppure i dati qui sotto</small></div>
          <div class="field"><label for="t-marca">Marca</label><input id="t-marca"></div>
          <div class="field"><label for="t-modello">Modello (facoltativo)</label><input id="t-modello"></div>
          <div class="field"><label for="t-anno">Anno (facoltativo)</label><input id="t-anno" inputmode="numeric"></div>
          <div class="field"><label for="t-km">Km</label><input id="t-km" inputmode="numeric"></div>
          <div class="field"><label for="t-prezzo">Prezzo dell'annuncio (€)</label><input id="t-prezzo" inputmode="numeric"></div>
        </div>
        <div class="field"><label for="t-motivo">Motivo</label><input id="t-motivo" placeholder="es. il cliente non la vuole"></div>
        <button class="btn" id="t-add">Togli dal sito</button></details>
      ${(d.scarti || []).length ? `<div style="overflow-x:auto;margin-top:10px"><table class="kv"><tr><th>Auto</th><th>Motivo</th><th>Dal</th><th></th></tr>
        ${d.scarti.slice(0, 10).map(rigaTolta).join("")}</table></div>
        ${d.scarti.length > 10 ? `<details style="margin-top:8px"><summary>Le altre ${d.scarti.length - 10} auto tolte</summary><div style="overflow-x:auto"><table class="kv">
        ${d.scarti.slice(10).map(rigaTolta).join("")}</table></div></details>` : ""}`
        : '<p class="note">Nessuna auto tolta.</p>'}
      ${(d.rimessi || []).length ? `<details style="margin-top:8px"><summary>Rimesse di recente (${d.rimessi.length})</summary><table class="kv">
        ${d.rimessi.map((x) => `<tr><td>${esc(x.descrizione)}</td><td>${esc(x.motivo || "")}</td><td>rimessa il ${esc(dtTesto(x.rimesso_at))}</td></tr>`).join("")}</table></details>` : ""}
      <p class="note">Un'auto tolta esce subito dall'elenco. "Rimetti" la fa tornare entro uno o due minuti (l'elenco si ricalcola in sottofondo).</p>
    </div>

    <h2>Commercianti</h2>
    <p class="note">"Deve pagare con carta" e "Accesso a" (auto, incentivi, appuntamenti) si cambiano in <a href="#/admin">Gestione → Commercianti</a> e valgono subito, senza riavvio.</p>`;

  const fasceBox = document.getElementById("rb-fasce");
  const legaVia = () => fasceBox.querySelectorAll("[data-f-via]").forEach((b) => b.onclick = () => b.closest("tr").remove());
  legaVia();
  document.getElementById("rb-add").onclick = () => { fasceBox.insertAdjacentHTML("beforeend", fasciaRiga("", "")); legaVia(); };
  document.getElementById("rb-salva").onclick = async () => {
    const schema = [...fasceBox.querySelectorAll("tr")].map((tr) => [tr.querySelector('[data-f="da"]').value, tr.querySelector('[data-f="eur"]').value])
      .filter(([a, b]) => a !== "" && b !== "").map(([a, b]) => `${parseInt(a, 10)}:${parseInt(b, 10)}`).join(",");
    const v = document.getElementById("rb-fino").value;
    const fino = v ? new Date(v).toISOString().replace("Z", "+00:00") : null;
    try { const x = await api("/api/admin/sito/ribasso", { method: "PUT", body: { attivo: document.getElementById("rb-on").checked, schema, fino } });
          toast(x.acceso_ora ? "Ribassi accesi: sul sito entro un minuto" : "Ribassi spenti: sul sito entro un minuto"); viewSito(); }
    catch (err) { toast(err.message); }
  };

  const disegnaCima = () => {
    const el2 = document.getElementById("s-cima");
    if (!SITO.cima.length) { el2.innerHTML = '<p class="note">Nessuna auto in cima.</p>'; return; }
    el2.innerHTML = `<div style="overflow-x:auto"><table class="kv"><tr><th>#</th><th>Auto</th><th>Prezzo mostrato (€)</th><th>Sul sito</th><th></th></tr>
      ${SITO.cima.map((e, k) => { const ss = e.sul_sito;
        return `<tr><td>${k + 1}</td><td>${esc(ss ? ss.auto : (e.nota || e.descrizione || (e.id ? "annuncio " + e.id : e.marca)))}</td>
        <td><input type="number" min="1" value="${esc(e.mostra ?? "")}" data-mostra="${k}" style="width:7rem" placeholder="come annuncio"></td>
        <td>${ss ? '<span class="ok">sì</span> ' + esc(eur(ss.prezzo)) : '<span class="note">non ora</span>'}</td>
        <td style="white-space:nowrap">${k ? `<button class="btn" data-su="${k}" aria-label="Sposta su">Su</button> ` : ""}<button class="btn" data-giu-cima="${k}">Togli dalla cima</button></td></tr>`; }).join("")}</table></div>
      <button class="btn primary" id="cima-salva" style="margin-top:8px">Salva auto in cima</button>`;
    el2.querySelectorAll("[data-mostra]").forEach((i) => i.onchange = () => { const k = +i.dataset.mostra; if (i.value) SITO.cima[k].mostra = parseInt(i.value, 10); else delete SITO.cima[k].mostra; });
    el2.querySelectorAll("[data-su]").forEach((b) => b.onclick = () => { const k = +b.dataset.su; [SITO.cima[k - 1], SITO.cima[k]] = [SITO.cima[k], SITO.cima[k - 1]]; disegnaCima(); });
    el2.querySelectorAll("[data-giu-cima]").forEach((b) => b.onclick = () => { SITO.cima.splice(+b.dataset.giuCima, 1); disegnaCima(); });
    document.getElementById("cima-salva").onclick = salvaCima;
  };
  const salvaCima = async () => {
    try { await api("/api/admin/sito/in-cima", { method: "PUT", body: { voci: SITO.cima } }); toast("Auto in cima salvate: sul sito entro un minuto"); viewSito(); }
    catch (err) { toast(err.message); }
  };
  disegnaCima();
  const num = (id) => document.getElementById(id).value.trim();
  document.getElementById("c-add").onclick = () => {
    const e = num("c-id") ? { id: num("c-id") } : { marca: num("c-marca"), modello: num("c-modello"), km: num("c-km"), prezzo: num("c-prezzo") };
    if (num("c-mostra")) e.mostra = num("c-mostra");
    SITO.cima.push(e); salvaCima();
  };

  const togli = async (body) => {
    try { await api("/api/admin/sito/scarti", { method: "POST", body }); toast("Auto tolta dal sito"); viewSito(); }
    catch (err) { toast(err.message); }
  };
  document.getElementById("t-add").onclick = () => togli(num("t-id") ? { id: num("t-id"), motivo: num("t-motivo") }
    : { marca: num("t-marca"), modello: num("t-modello"), anno: num("t-anno"), km: num("t-km"), prezzo: num("t-prezzo"), motivo: num("t-motivo") });
  box.querySelectorAll("[data-rimetti]").forEach((b) => b.onclick = async () => {
    try { await api("/api/admin/sito/scarti/" + b.dataset.rimetti + "/rimetti", { method: "POST" }); toast("Rimessa: torna sul sito entro uno o due minuti"); viewSito(); }
    catch (err) { toast(err.message); }
  });

  const q = document.getElementById("s-q"), ris = document.getElementById("s-ris");
  let tq = null;
  q.oninput = () => { clearTimeout(tq); tq = setTimeout(async () => {
    if (!q.value.trim()) { ris.innerHTML = ""; return; }
    try {
      const x = await api("/api/admin/sito/cerca?q=" + encodeURIComponent(q.value.trim()));
      ris.innerHTML = x.items.length ? `<div style="overflow-x:auto"><table class="kv"><tr><th>Auto</th><th>Prezzo</th><th>Zona</th><th></th></tr>
        ${x.items.map((it) => `<tr><td>${esc(it.auto)} <small class="note">id ${esc(it.id)}</small></td><td>${esc(eur(it.prezzo))}</td><td>${esc(it.zona || "")}</td>
          <td style="white-space:nowrap"><button class="btn" data-cima="${esc(it.id)}" data-nome="${esc(it.auto)}">Metti in cima</button> <button class="btn" data-via="${esc(it.id)}" data-nome="${esc(it.auto)}">Togli dal sito</button></td></tr>`).join("")}</table></div>`
        : '<p class="note">Nessuna auto sul sito con queste parole.</p>';
      ris.querySelectorAll("[data-cima]").forEach((b) => b.onclick = () => {
        const m = prompt("Prezzo da mostrare per " + b.dataset.nome + " (vuoto = prezzo dell'annuncio):", "");
        if (m === null) return;
        const e = { id: parseInt(b.dataset.cima, 10), nota: b.dataset.nome };
        if (m.trim()) e.mostra = m.trim();
        SITO.cima.unshift(e); salvaCima();
      });
      ris.querySelectorAll("[data-via]").forEach((b) => b.onclick = () => {
        const m = prompt("Togli dal sito " + b.dataset.nome + ". Motivo (facoltativo):", "");
        if (m === null) return;
        togli({ id: parseInt(b.dataset.via, 10), motivo: m.trim() });
      });
    } catch (err) { ris.innerHTML = `<p class="note">${esc(err.message)}</p>`; }
  }, 250); };
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
  document.getElementById("nav-sito").hidden = !(state.user && state.user.role === "admin");
  const name = h.startsWith("#/o/") ? "list" : (h.slice(2) || "list");
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
  window.scrollTo(0, 0);
  if (h.startsWith("#/o/")) return viewDetail(parseInt(h.slice(4), 10));
  if (h === "#/attivita") return viewActivity();
  if (h === "#/profilo") return viewProfile();
  if (h === "#/admin") return viewAdmin();
  if (h === "#/sito") return viewSito();
  return viewList();
}

state.token = load("df_token");
try { state.user = JSON.parse(load("df_user") || "null"); } catch (e) {}
window.addEventListener("hashchange", route);
route();
checkNotifications();
setInterval(checkNotifications, 5 * 60 * 1000);
