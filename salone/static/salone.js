// Apertura con le foto del salone: si arriva davanti alla facciata, la vetrata si apre in due e si entra nella sala,
// poi ci si gira verso l'ingresso. Sopra le foto: faretti che brillano, luce che scorre sul marmo, piccole luci nell'aria.
(() => {
  const CFG = window.SALONE || {};
  const FOTO = window.SALONE_FOTO || { esterno: '/foto/esterno.webp', sala: '/foto/sala.webp', vetrata: '/foto/vetrata.webp' };
  const Q = new URLSearchParams(location.search);
  const FORCE = Q.has('p') ? +Q.get('p') : null;                 // ?p=0.5 : fotogramma fisso (per i controlli)
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const canvas = document.getElementById('scena');
  const show = document.getElementById('apertura');
  const ctx = canvas.getContext('2d');
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
  const mix = (a, b, t) => a + (b - a) * t;
  const pronto = () => window.dispatchEvent(new Event('salone-pronto'));

  const carica = src => new Promise((ok, ko) => { const i = new Image(); i.decoding = 'async'; i.onload = () => ok(i); i.onerror = ko; i.src = src; });
  const puntiP = window.SALONE_PUNTI ? Promise.resolve(window.SALONE_PUNTI) : fetch('/foto/punti.json').then(r => r.json()).catch(() => ({}));
  Promise.all([carica(FOTO.esterno), carica(FOTO.sala), carica(FOTO.vetrata), puntiP])
    .then(([esterno, sala, vetrata, punti]) => avvia({ esterno, sala, vetrata }, punti))
    .catch(e => { console.warn('foto non caricate', e); document.documentElement.classList.add('nogl'); pronto(); });

  function avvia(IMG, PUNTI) {
    // bagliore morbido da stampare sui faretti (caldo) e sulle lucine (azzurro)
    const sprite = (r, g, b) => {
      const c = document.createElement('canvas'); c.width = c.height = 64; const x = c.getContext('2d');
      const gr = x.createRadialGradient(32, 32, 0, 32, 32, 32);
      gr.addColorStop(0, `rgba(255,255,255,1)`); gr.addColorStop(0.18, `rgba(${r},${g},${b},.85)`); gr.addColorStop(0.5, `rgba(${r},${g},${b},.18)`); gr.addColorStop(1, `rgba(${r},${g},${b},0)`);
      x.fillStyle = gr; x.fillRect(0, 0, 64, 64); return c;
    };
    const CALDO = sprite(255, 226, 180), FREDDO = sprite(200, 224, 255);
    const fase = Array.from({ length: 60 }, (_, i) => (i * 2.399) % (Math.PI * 2));
    const pulviscolo = Array.from({ length: 46 }, (_, i) => ({
      x: (i * 0.618) % 1, y: (i * 0.377 + 0.1) % 1, r: 1.2 + (i % 5) * 0.9, v: 0.004 + (i % 7) * 0.0016, f: fase[i % 60], caldo: i % 3 !== 0 }));

    let W = 0, H = 0, D = 1, target = FORCE ?? 0, p = target;
    const nav = document.getElementById('nav'), prog = document.getElementById('avanzamento'), velo = document.getElementById('velo');
    const copies = [...document.querySelectorAll('.racconto [data-da]')];
    function misura() {
      D = Math.min(devicePixelRatio || 1, 2);
      W = canvas.clientWidth; H = canvas.clientHeight;
      canvas.width = Math.round(W * D); canvas.height = Math.round(H * D);
    }
    function leggi() {
      const r = show.getBoundingClientRect();
      if (FORCE == null) target = clamp(-r.top / Math.max(1, r.height - innerHeight));
      nav.classList.toggle('pieno', r.bottom < innerHeight * 0.6);
      nav.classList.toggle('su-foto', r.bottom >= innerHeight * 0.6);
    }
    addEventListener('resize', () => { misura(); leggi(); });
    addEventListener('scroll', leggi, { passive: true });
    misura(); leggi();

    // posizione di una foto "a copertura" dello schermo, ingrandita verso un punto (fx, fy) della foto
    function piazza(img, scala, fx, fy, dx = 0) {
      const s0 = Math.max(W / img.width, H / img.height), s = s0 * scala;
      const ax = W / 2 + (fx - 0.5) * img.width * s0, ay = H / 2 + (fy - 0.5) * img.height * s0;   // dove sta il punto a scala 1
      let ox = ax - fx * img.width * s + dx, oy = ay - fy * img.height * s;
      ox = Math.min(0, Math.max(W - img.width * s, ox)); oy = Math.min(0, Math.max(H - img.height * s, oy));
      return { s, ox, oy, w: img.width * s, h: img.height * s };
    }
    function foto(img, g, alpha, clip) {
      if (alpha <= 0.001) return;
      ctx.save(); ctx.globalAlpha = alpha;
      if (clip) { ctx.beginPath(); ctx.rect(clip[0], 0, clip[1] - clip[0], H); ctx.clip(); }
      ctx.drawImage(img, g.ox, g.oy, g.w, g.h);
      ctx.restore();
    }
    function luci(lista, g, alpha, t, dx = 0, clip) {
      if (alpha <= 0.02 || !lista) return;
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      if (clip) { ctx.beginPath(); ctx.rect(clip[0], 0, clip[1] - clip[0], H); ctx.clip(); }
      lista.forEach(([x, y], i) => {
        const tw = REDUCED ? 0.75 : 0.55 + 0.45 * Math.sin(t * (0.9 + (i % 5) * 0.23) + fase[i % 60]);
        const r = (10 + (i % 4) * 4) * Math.sqrt(g.s) * 1.4;
        ctx.globalAlpha = alpha * tw * 0.85;
        ctx.drawImage(i % 6 === 0 ? FREDDO : CALDO, g.ox + x * g.w - r + dx, g.oy + y * g.h - r, r * 2, r * 2);
      });
      ctx.restore();
    }
    function riflesso(g, alpha, t) {                     // luce che scorre piano sul pavimento di marmo
      if (alpha <= 0.02 || REDUCED) return;
      const ciclo = (t % 9) / 9, x = mix(-0.4, 1.4, ciclo) * W, y0 = g.oy + g.h * 0.66;
      ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = alpha * 0.16 * Math.sin(ciclo * Math.PI);
      ctx.beginPath(); ctx.rect(0, y0, W, H - y0); ctx.clip();
      ctx.translate(x, y0); ctx.rotate(-0.5);
      const gr = ctx.createLinearGradient(-W * 0.12, 0, W * 0.12, 0);
      gr.addColorStop(0, 'rgba(255,240,215,0)'); gr.addColorStop(0.5, 'rgba(255,244,226,1)'); gr.addColorStop(1, 'rgba(255,240,215,0)');
      ctx.fillStyle = gr; ctx.fillRect(-W * 0.12, -H * 2, W * 0.24, H * 4);
      ctx.restore();
    }
    function aria(t, alpha) {                            // piccole luci che salgono nell'aria
      if (REDUCED || alpha <= 0.02) return;
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      for (const q of pulviscolo) {
        const y = ((q.y - t * q.v) % 1 + 1) % 1, x = q.x + Math.sin(t * 0.3 + q.f) * 0.015;
        ctx.globalAlpha = alpha * (0.35 + 0.35 * Math.sin(t * 1.3 + q.f)) * Math.sin(y * Math.PI);
        const r = q.r * 3;
        ctx.drawImage(q.caldo ? CALDO : FREDDO, x * W - r, y * H - r, r * 2, r * 2);
      }
      ctx.restore();
    }

    const t0 = performance.now();
    let disegnati = 0;
    function frame(now) {
      if (FORCE != null && ++disegnati > 2) return;
      requestAnimationFrame(frame);
      const r = show.getBoundingClientRect();
      if (r.bottom < 0 && avvia.fatto) return;
      const t = (now - t0) / 1000;
      p += (target - p) * (REDUCED || FORCE != null ? 1 : 0.085);
      const e = clamp(p), respiro = REDUCED ? 0 : Math.sin(t * 0.25) * 0.006;
      ctx.setTransform(D, 0, 0, D, 0, 0);
      ctx.fillStyle = '#0f1a2e'; ctx.fillRect(0, 0, W, H);

      // ---- 1. facciata: ci si avvicina, poi la vetrata si apre in due ----
      const ap = smooth(0.2, 0.36, e);
      const gE = piazza(IMG.esterno, 1.0 + 0.3 * smooth(0, 0.36, e) + ap * 0.25 + respiro, 0.5, 0.6);
      const taglio = gE.ox + 0.5 * gE.w, apri = ap * W * 0.62;
      const aE = 1 - smooth(0.3, 0.37, e);

      // ---- 2. sala: dietro la vetrata, poi si cammina verso il fondo ----
      const aS = smooth(0.2, 0.26, e) * (1 - smooth(0.63, 0.71, e));
      const giro = smooth(0.62, 0.71, e);
      const gS = piazza(IMG.sala, 1.24 - 0.16 * ap + 0.24 * smooth(0.36, 0.66, e) + respiro, 0.53, 0.57, -giro * W * 0.18);

      // ---- 3. vetrata: ci si gira verso l'ingresso e la Ferrari ----
      const aV = smooth(0.62, 0.71, e);
      const gV = piazza(IMG.vetrata, 1.12 + 0.18 * smooth(0.7, 0.96, e) + respiro, mix(0.3, 0.62, smooth(0.68, 0.96, e)), 0.56, (1 - giro) * W * 0.18);

      if (aS > 0) { foto(IMG.sala, gS, aS); riflesso(gS, aS, t); luci(PUNTI.sala, gS, aS, t); }
      if (aV > 0) { foto(IMG.vetrata, gV, aV); riflesso(gV, aV, t + 4); luci(PUNTI.vetrata, gV, aV, t); }
      if (aE > 0) {
        foto(IMG.esterno, { ...gE, ox: gE.ox - apri }, aE, [0, taglio - apri]);
        foto(IMG.esterno, { ...gE, ox: gE.ox + apri }, aE, [taglio + apri, W]);
        luci(PUNTI.esterno, gE, aE, t, -apri, [0, taglio - apri]);
        luci(PUNTI.esterno, gE, aE, t, apri, [taglio + apri, W]);
        if (ap > 0.001 && ap < 0.999) {            // luce calda che esce dalla fessura
          ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = Math.sin(ap * Math.PI) * 0.55;
          const gr = ctx.createLinearGradient(taglio - apri - 140, 0, taglio + apri + 140, 0);
          gr.addColorStop(0, 'rgba(255,236,205,0)'); gr.addColorStop(0.5, 'rgba(255,240,220,.9)'); gr.addColorStop(1, 'rgba(255,236,205,0)');
          ctx.fillStyle = gr; ctx.fillRect(taglio - apri - 140, 0, apri * 2 + 280, H); ctx.restore();
          ctx.save(); ctx.fillStyle = 'rgba(214,230,244,.55)';                    // profili sottili delle due ante
          ctx.fillRect(taglio - apri - 1, 0, 1.5, H); ctx.fillRect(taglio + apri - 0.5, 0, 1.5, H); ctx.restore();
        }
      }
      aria(t, 1 - smooth(0.88, 0.98, e));
      // velatura blu notte ai bordi, più leggera al centro
      const vg = ctx.createRadialGradient(W / 2, H * 0.5, Math.min(W, H) * 0.35, W / 2, H * 0.55, Math.max(W, H) * 0.8);
      vg.addColorStop(0, 'rgba(15,26,46,0)'); vg.addColorStop(1, 'rgba(15,26,46,.55)');
      ctx.fillStyle = vg; ctx.fillRect(0, 0, W, H);

      for (const el of copies) el.classList.toggle('on', e >= +el.dataset.da && e <= +el.dataset.a);
      prog.style.transform = `scaleX(${e})`;
      velo.style.opacity = smooth(0.92, 1, e);
      if (!avvia.fatto) { avvia.fatto = true; pronto(); }
    }
    requestAnimationFrame(frame);
  }
})();
