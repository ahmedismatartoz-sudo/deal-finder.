(() => {
  const S = window.SALONE || {};
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const eur = n => n == null ? '' : n.toLocaleString('it-IT') + ' €';
  const kmf = a => a.km_fascia && a.km_testo ? a.km_testo + ' km' : a.km == null ? '' : a.km.toLocaleString('it-IT') + ' km';
  const anno = a => { const m = String(a.immatricolazione || '').match(/(19|20)\d\d/); return m ? +m[0] : null; };
  const cv = a => { const m = String(a.potenza || '').match(/(\d+)\s*CV/i); return m ? m[1] + ' CV' : (a.potenza || ''); };
  const fuelIt = f => ({ Gasoline: 'Benzina', Diesel: 'Diesel', Electric: 'Elettrica' }[f] || f || '');
  const img = (u, size) => !u ? '' : u.startsWith('data:') ? u : '/img?u=' + encodeURIComponent(size ? u.replace(/\/\d+x\d+\.webp$/, '/' + size + '.webp') : u);

  // ---- dati del salone nella pagina ----
  const tel = (S.telefono || '').replace(/[^\d+]/g, '');
  const indirizzo = [S.indirizzo, [S.cap, S.citta].filter(Boolean).join(' ') + (S.provincia ? ` (${S.provincia})` : '')].filter(Boolean).join(', ');
  const mappaQ = encodeURIComponent(indirizzo);
  document.title = `${S.nome || 'Salone'} · ${S.sottotitolo || 'Auto selezionate'}${S.citta ? ' a ' + S.citta : ''}`;
  $$('[data-nome]').forEach(e => e.textContent = S.nome || 'Salone');
  $$('[data-sottotitolo]').forEach(e => e.textContent = S.sottotitolo || 'Auto selezionate');
  $$('[data-citta]').forEach(e => e.textContent = S.citta || '');
  $$('[data-sottotitolo-completo]').forEach(e => e.textContent = (S.sottotitolo || 'Auto selezionate') + (S.citta ? ' a ' + S.citta : ''));
  $$('[data-indirizzo]').forEach(e => e.textContent = indirizzo);
  $$('[data-tel]').forEach(e => e.href = 'tel:' + tel);
  $$('[data-tel-testo]').forEach(e => e.textContent = S.telefono || '');
  $$('[data-wa]').forEach(e => e.href = 'https://wa.me/' + (S.whatsapp || tel.replace('+', '')));
  $$('[data-mappa]').forEach(e => e.href = 'https://www.google.com/maps/dir/?api=1&destination=' + mappaQ);
  $$('[data-mappa-src]').forEach(e => e.src = 'https://www.google.com/maps?q=' + mappaQ + '&output=embed');
  $$('[data-piva]').forEach(e => e.textContent = S.piva ? ' · P.IVA ' + S.piva : '');
  $('#orari').innerHTML = (S.orari || []).map(([g, o]) => `<tr><td>${esc(g)}</td><td>${esc(o)}</td></tr>`).join('');
  $('#anno').textContent = new Date().getFullYear();

  // attesa: via quando la scena è pronta (o dopo 6 secondi)
  const via = () => $('#attesa').classList.add('via');
  addEventListener('salone-pronto', via); setTimeout(via, 6000);
  addEventListener('scroll', () => { $('#scorri').style.opacity = scrollY > 40 ? 0 : 1; }, { passive: true });

  // ---- auto ----
  const griglia = $('#griglia');
  let AUTO = [], ESEMPI = false;
  griglia.innerHTML = Array.from({ length: 6 }, () => '<div class="ombra"></div>').join('');

  async function carica() {
    let d = { auto: [] };
    try { const r = await fetch('/api/auto', { cache: 'no-store' }); if (r.ok) d = await r.json(); } catch (e) { /* senza server: esempi */ }
    AUTO = (d.auto || []).filter(a => a.prezzo);
    if (!AUTO.length && d.in_corso) { setTimeout(carica, 8000); return; }
    if (!AUTO.length && S.esempi !== false) { AUTO = esempi(); ESEMPI = true; }
    $('#avvisoEsempi').hidden = !ESEMPI;
    if (d.aggiornato && !ESEMPI) $('#agg').textContent = 'Auto aggiornate il ' + new Date(d.aggiornato).toLocaleString('it-IT', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
    $('#nAuto').textContent = AUTO.length || 'Le';
    riempi('#fMarca', [...new Set(AUTO.map(a => a.marca).filter(Boolean))].sort());
    riempi('#fFuel', [...new Set(AUTO.map(a => fuelIt(a.carburante)).filter(Boolean))].sort());
    disegna();
    const id = new URLSearchParams(location.search).get('auto');
    if (id) apri(AUTO.find(x => x.id === id));
  }
  const riempi = (sel, vals) => { const s = $(sel); vals.forEach(v => s.insertAdjacentHTML('beforeend', `<option>${esc(v)}</option>`)); };

  function disegna() {
    const q = $('#q').value.trim().toLowerCase(), mk = $('#fMarca').value, fu = $('#fFuel').value, pm = +$('#fPrezzo').value || 0, so = $('#fSort').value;
    let L = AUTO.filter(a => (!mk || a.marca === mk) && (!fu || fuelIt(a.carburante) === fu) && (!pm || a.prezzo <= pm)
      && (!q || `${a.marca} ${a.modello} ${a.versione}`.toLowerCase().includes(q)));
    const O = { pa: (a, b) => a.prezzo - b.prezzo, pd: (a, b) => b.prezzo - a.prezzo, km: (a, b) => (a.km ?? 1e9) - (b.km ?? 1e9), anno: (a, b) => (anno(b) || 0) - (anno(a) || 0) };
    if (O[so]) L = [...L].sort(O[so]);
    $('#conta').textContent = L.length === 1 ? '1 auto disponibile' : `${L.length} auto disponibili`;
    $('#vuoto').hidden = L.length > 0;
    griglia.innerHTML = L.map(a => `
      <article class="auto" data-id="${esc(a.id)}" tabindex="0" aria-label="${esc(a.marca + ' ' + a.modello)}, ${esc(eur(a.prezzo))}">
        <div class="ph">${a.foto?.[0] ? `<img loading="lazy" src="${img(a.foto[0], '720x540')}" alt="" onload="this.classList.add('ok')">` : ''}
          ${a.esempio ? '<span class="etichetta">Esempio</span>' : a.giudizio === 'top' ? '<span class="etichetta">Ottimo prezzo</span>' : ''}</div>
        <div class="riga"><h3>${esc(a.marca)} ${esc(a.modello)}</h3><span class="prezzo">${eur(a.prezzo)}</span></div>
        <p class="ver">${esc(a.versione)}</p>
        <p class="dati">${[anno(a), kmf(a), fuelIt(a.carburante), a.cambio, cv(a)].filter(Boolean).map(x => `<span>${esc(x)}</span>`).join('')}</p>
      </article>`).join('');
    griglia.querySelectorAll('.auto').forEach(el => {
      const go = () => apri(AUTO.find(a => a.id === el.dataset.id));
      el.addEventListener('click', go);
      el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });
  }
  ['#q', '#fMarca', '#fFuel', '#fPrezzo', '#fSort'].forEach(s => $(s).addEventListener('input', disegna));
  $('#azzera').onclick = () => { ['#q', '#fMarca', '#fFuel', '#fPrezzo'].forEach(s => $(s).value = ''); disegna(); };

  // ---- scheda auto ----
  const scheda = $('#scheda');
  let gal = [], gi = 0, prima = null;
  function mostra(i) {
    if (!gal.length) return;
    gi = (i + gal.length) % gal.length;
    $('#sImg').src = img(gal[gi]);
    $('#sN').textContent = `${gi + 1} di ${gal.length}`;
    $('#sMini').querySelectorAll('img').forEach((t, k) => t.classList.toggle('on', k === gi));
  }
  function apri(a) {
    if (!a) return;
    prima = document.activeElement;
    gal = a.foto || [];
    $('#sMini').innerHTML = gal.map((u, k) => `<img loading="lazy" src="${img(u, '250x188')}" data-k="${k}" alt="Foto ${k + 1}">`).join('');
    $('#sMini').querySelectorAll('img').forEach(t => t.onclick = () => mostra(+t.dataset.k));
    mostra(0);
    const nome = `${a.marca} ${a.modello}`;
    $('#sVer').textContent = a.versione || '';
    $('#sTit').textContent = nome;
    $('#sPrezzo').textContent = eur(a.prezzo);
    const dati = [['Immatricolazione', a.immatricolazione], ['Chilometri', kmf(a)], ['Alimentazione', fuelIt(a.carburante)], ['Cambio', a.cambio],
      ['Potenza', a.potenza], ['Proprietari', a.proprietari], ['Colore', a.colore], ['Carrozzeria', a.carrozzeria], ['Porte', a.porte], ['Posti', a.posti],
      ['Cilindrata', a.cilindrata], ['Classe emissioni', a.classe], ['Consumo', a.consumo]].filter(x => x[1]);
    $('#sDati').innerHTML = dati.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join('');
    $('#sDesc').textContent = a.descrizione || '';
    $('#sDot').innerHTML = (a.dotazioni || []).slice(0, 40).map(x => `<li>${esc(x)}</li>`).join('');
    $('#sWa').href = `https://wa.me/${S.whatsapp || tel.replace('+', '')}?text=` + encodeURIComponent(`Buongiorno, sono interessato alla ${nome} a ${eur(a.prezzo)} vista sul vostro sito. È ancora disponibile?`);
    const f = $('#sFonte');
    f.hidden = !a.url; f.href = a.url || '#';
    f.textContent = a.fonte === 'subito' ? "Vedi l'annuncio su Subito" : "Vedi l'annuncio su AutoScout24";
    scheda.hidden = false; document.body.style.overflow = 'hidden';
    scheda.querySelector('.chiudi').focus();
    if (!a.esempio) history.replaceState(null, '', '?auto=' + encodeURIComponent(a.id) + '#auto');
  }
  function chiudi() { scheda.hidden = true; document.body.style.overflow = ''; history.replaceState(null, '', location.pathname + '#auto'); prima?.focus?.(); }
  scheda.querySelectorAll('[data-chiudi]').forEach(b => b.addEventListener('click', chiudi));
  $('#prec').onclick = () => mostra(gi - 1); $('#succ').onclick = () => mostra(gi + 1);
  addEventListener('keydown', e => { if (scheda.hidden) return; if (e.key === 'Escape') chiudi(); if (e.key === 'ArrowLeft') mostra(gi - 1); if (e.key === 'ArrowRight') mostra(gi + 1); });
  let sx = null;
  $('#sImg').addEventListener('touchstart', e => sx = e.touches[0].clientX, { passive: true });
  $('#sImg').addEventListener('touchend', e => { if (sx == null) return; const d = e.changedTouches[0].clientX - sx; if (Math.abs(d) > 40) mostra(gi + (d < 0 ? 1 : -1)); sx = null; });

  // ---- auto di esempio (solo finché non ci sono auto vere) ----
  function esempi() {
    const toni = [['#dbe8f2', '#9fbcd4'], ['#eef3f6', '#c3d2de'], ['#e6eef4', '#a9c4da'], ['#f2f5f7', '#b7c7d3'], ['#e3ecf3', '#b2c8da'], ['#edf2f6', '#a4bdd2']];
    const sagoma = (a, b, k) => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient></defs>
      <rect width="800" height="600" fill="url(#g)"/><g fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" opacity=".85">
      <path d="M110 382 C170 ${330 - k * 6} 250 270 400 266 C540 268 620 318 690 382"/><path d="M80 384 H720"/>
      <path d="M200 384 a52 52 0 0 1 104 0 M500 384 a52 52 0 0 1 104 0"/></g></svg>`);
    const base = [
      ['Audi', 'A4 Avant', '40 TDI S tronic Business', 28900, '03/2021', 64000, 'Diesel', 'Automatico', '150 kW (204 CV)'],
      ['BMW', 'Serie 3', '320d Touring Luxury', 31500, '06/2020', 71000, 'Diesel', 'Automatico', '140 kW (190 CV)'],
      ['Mercedes-Benz', 'Classe A', 'A 180 Sport', 23900, '09/2021', 38000, 'Benzina', 'Automatico', '100 kW (136 CV)'],
      ['Volkswagen', 'Golf', '1.5 eTSI Life', 21400, '02/2022', 29000, 'Benzina', 'Automatico', '96 kW (130 CV)'],
      ['Toyota', 'Yaris Cross', '1.5 Hybrid Trend', 24800, '05/2022', 22000, 'Ibrida', 'Automatico', '85 kW (116 CV)'],
      ['Fiat', '500', '1.0 Hybrid Dolcevita', 13900, '04/2022', 18000, 'Ibrida', 'Manuale', '51 kW (70 CV)'],
    ];
    return base.map(([marca, modello, versione, prezzo, imm, km, carb, cambio, pot], i) => ({
      id: 'esempio-' + i, esempio: true, marca, modello, versione, prezzo, immatricolazione: imm, km, carburante: carb, cambio, potenza: pot,
      foto: [0, 1, 2].map(k => sagoma(...toni[(i + k) % toni.length], i + k)),
      descrizione: 'Auto di esempio. Quando colleghiamo AutoScout24 e Subito, qui compaiono le auto vere del salone con foto, descrizione e dotazioni.',
    }));
  }

  carica();
})();
