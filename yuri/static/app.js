(() => {
  const TEL = '+393278772949';
  const $ = s => document.querySelector(s);
  const grid = $('#grid'), empty = $('#empty'), count = $('#count');
  let AUTO = [];
  const mil = n => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const eur = n => n == null ? '' : mil(n) + ' €';
  const kmf = n => n == null ? '' : mil(n) + ' km';
  const img = (u, size) => u ? '/img?u=' + encodeURIComponent(size ? u.replace(/\/\d+x\d+\.webp$/, '/' + size + '.webp') : u) : '';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const anno = a => { const m = String(a.immatricolazione || '').match(/(19|20)\d\d/); return m ? +m[0] : null; };
  const cv = a => { const m = String(a.potenza || '').match(/(\d+)\s*CV/i); return m ? m[1] + ' CV' : (a.potenza || ''); };
  const fuelIt = f => ({ Gasoline: 'Benzina', Diesel: 'Diesel', Electric: 'Elettrica' }[f] || f || '');
  const giud = g => { g = String(g || '').toLowerCase(); if (/top|super|very|ottimo/.test(g)) return ['Super prezzo', 'top']; if (/good|buon/.test(g)) return ['Buon prezzo', '']; return null; };

  $('#yr').textContent = new Date().getFullYear();


  grid.innerHTML = Array.from({ length: 6 }, () => '<div class="skel"></div>').join('');

  async function load() {
    try {
      const r = await fetch('/api/auto', { cache: 'no-store' });
      const d = await r.json();
      AUTO = (d.auto || []).filter(a => a.prezzo);
      if (!AUTO.length && d.in_corso) { setTimeout(load, 8000); return; }
      if (d.aggiornato) $('#upd').textContent = 'Auto aggiornate il ' + new Date(d.aggiornato).toLocaleString('it-IT', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
      fill('#fMarca', [...new Set(AUTO.map(a => a.marca).filter(Boolean))].sort());
      fill('#fFuel', [...new Set(AUTO.map(a => fuelIt(a.carburante)).filter(Boolean))].sort());
      render();
      const id = new URLSearchParams(location.search).get('auto');
      if (id) { const a = AUTO.find(x => x.id === id); a && openCar(a); }
    } catch (e) { console.error(e); setTimeout(load, 8000); }
  }
  const fill = (sel, vals) => { const s = $(sel); vals.forEach(v => s.insertAdjacentHTML('beforeend', `<option>${esc(v)}</option>`)); };

  function render() {
    const q = $('#q').value.trim().toLowerCase(), mk = $('#fMarca').value, fu = $('#fFuel').value, pm = +$('#fPrezzo').value || 0, so = $('#fSort').value;
    let L = AUTO.filter(a => (!mk || a.marca === mk) && (!fu || fuelIt(a.carburante) === fu) && (!pm || a.prezzo <= pm)
      && (!q || `${a.marca} ${a.modello} ${a.versione}`.toLowerCase().includes(q)));
    const S = { pa: (a, b) => a.prezzo - b.prezzo, pd: (a, b) => b.prezzo - a.prezzo, km: (a, b) => (a.km ?? 1e9) - (b.km ?? 1e9), anno: (a, b) => (anno(b) || 0) - (anno(a) || 0) };
    if (S[so]) L = [...L].sort(S[so]);
    count.innerHTML = `<b>${L.length}</b>${L.length === 1 ? 'auto disponibile' : 'auto disponibili'}`;
    empty.hidden = L.length > 0;
    grid.innerHTML = L.map((a, i) => {
      const g = giud(a.giudizio), F = a.foto || [], alt = esc(a.marca + ' ' + (a.modello || ''));
      return `<article class="car" data-id="${esc(a.id)}">
        <div class="ph"><div class="gal">${F.map((u, k) => `<img ${i < 3 && k === 0 ? 'fetchpriority="high"' : 'loading="lazy"'} decoding="async" src="${img(u, '480x360')}" alt="${alt}">`).join('')}</div>
          ${F.length > 1 ? '<button class="ga l" aria-label="Foto precedente">‹</button><button class="ga r" aria-label="Foto successiva">›</button>' : ''}
          ${g ? `<span class="badge ${g[1]}">${g[0]}</span>` : ''}
          ${F.length ? `<span class="nfoto"><i>1</i> / ${F.length}</span>` : ''}</div>
        <div class="body">
          <h3>${esc(a.marca)} ${esc(a.modello || '')}</h3>
          <p class="ver">${esc(a.versione)}</p>
          <div class="chips">${[anno(a), kmf(a.km), fuelIt(a.carburante), a.cambio, cv(a)].filter(Boolean).map(x => `<span>${esc(x)}</span>`).join('')}</div>
          <div class="bottom"><span class="price grad">${eur(a.prezzo)}</span><span class="go">→</span></div>
        </div></article>`;
    }).join('');
    grid.querySelectorAll('.car').forEach(el => {
      const gal = el.querySelector('.gal'), n = el.querySelector('.nfoto i');
      const idx = () => Math.round(gal.scrollLeft / gal.clientWidth);
      gal.addEventListener('scroll', () => { if (n) n.textContent = idx() + 1; }, { passive: true });
      el.querySelectorAll('.ga').forEach(b => b.addEventListener('click', e => {
        e.stopPropagation();
        const tot = gal.children.length; let k = idx() + (b.classList.contains('r') ? 1 : -1);
        k = (k + tot) % tot; gal.scrollTo({ left: k * gal.clientWidth });
      }));
      el.addEventListener('click', () => { const a = AUTO.find(x => x.id === el.dataset.id); openCar(a, idx()); });
    });
  }
  ['#q', '#fMarca', '#fFuel', '#fPrezzo', '#fSort'].forEach(s => $(s).addEventListener('input', render));

  // ---------- scheda auto ----------
  const modal = $('#modal');
  let gal = [], gi = 0;
  function show(i) {
    if (!gal.length) return;
    gi = (i + gal.length) % gal.length;
    const big = innerWidth < 900 ? '720x540' : null;
    $('#mImg').src = img(gal[gi], big);
    [1, -1].forEach(d => { const u = gal[(gi + d + gal.length) % gal.length]; if (u) new Image().src = img(u, big); });
    $('#mN').textContent = `${gi + 1} / ${gal.length}`;
    $('#mThumbs').querySelectorAll('img').forEach((t, k) => t.classList.toggle('on', k === gi));
    const t = $('#mThumbs').children[gi]; if (t) $('#mThumbs').scrollLeft = t.offsetLeft - $('#mThumbs').clientWidth / 2 + t.clientWidth / 2;
  }
  function openCar(a, start = 0) {
    if (!a) return;
    gal = a.foto || [];
    $('#mThumbs').innerHTML = gal.map((u, k) => `<img loading="lazy" src="${img(u, '250x188')}" data-k="${k}" alt="">`).join('');
    $('#mThumbs').querySelectorAll('img').forEach(t => t.onclick = () => show(+t.dataset.k));
    show(start);
    const nome = `${a.marca} ${a.modello || ''}`.trim();
    $('#mVer').textContent = a.versione || '';
    $('#mTit').textContent = nome;
    $('#mPrice').textContent = eur(a.prezzo);
    const spec = [['Immatricolazione', a.immatricolazione], ['Chilometri', kmf(a.km)], ['Alimentazione', fuelIt(a.carburante)], ['Cambio', a.cambio],
      ['Potenza', a.potenza], ['Proprietari', a.proprietari], ['Colore', a.colore], ['Carrozzeria', a.carrozzeria], ['Porte', a.porte], ['Posti', a.posti],
      ['Cilindrata', a.cilindrata], ['Classe emissioni', a.classe], ['Consumo', a.consumo]].filter(x => x[1]);
    $('#mSpecs').innerHTML = spec.map(([k, v]) => `<div><small>${k}</small><b>${esc(v)}</b></div>`).join('');
    $('#mDesc').textContent = a.descrizione || '';
    $('#mEq').innerHTML = (a.dotazioni || []).slice(0, 40).map(x => `<span>${esc(x)}</span>`).join('');
    $('#mWa').href = `https://wa.me/${TEL.replace('+', '')}?text=` + encodeURIComponent(`Buongiorno, sono interessato alla ${nome} a ${eur(a.prezzo)} vista sul sito di Yuri Car. È ancora disponibile?`);
    $('#mSrc').href = a.url; $('#mSrc').hidden = !a.url;
    modal.hidden = false; document.body.style.overflow = 'hidden';
    history.replaceState(null, '', '?auto=' + encodeURIComponent(a.id) + '#auto');
  }
  window.apriAuto = id => openCar(AUTO.find(a => a.id === id));
  function close() { modal.hidden = true; document.body.style.overflow = ''; history.replaceState(null, '', location.pathname + '#auto'); }
  modal.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', close));
  $('#prev').onclick = () => show(gi - 1); $('#next').onclick = () => show(gi + 1);
  addEventListener('keydown', e => { if (modal.hidden) return; if (e.key === 'Escape') close(); if (e.key === 'ArrowLeft') show(gi - 1); if (e.key === 'ArrowRight') show(gi + 1); });
  let sx = null;
  $('#mImg').addEventListener('touchstart', e => sx = e.touches[0].clientX, { passive: true });
  $('#mImg').addEventListener('touchend', e => { if (sx == null) return; const d = e.changedTouches[0].clientX - sx; if (Math.abs(d) > 40) show(gi + (d < 0 ? 1 : -1)); sx = null; });

  load();
})();
