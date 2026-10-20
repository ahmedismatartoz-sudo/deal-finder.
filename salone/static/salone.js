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
  const interno = carica(FOTO.interno || '/foto/interno.webp').catch(() => null);      // foto degli interni (facoltativa)
  Promise.all([carica(FOTO.esterno), carica(FOTO.sala), carica(FOTO.vetrata), puntiP, interno])
    .then(([esterno, sala, vetrata, punti, interno]) => avvia({ esterno, sala, vetrata, interno }, punti))
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

    // abitacolo disegnato: parabrezza con la sala, montanti, plancia con la striscia di luce azzurra,
    // due strumenti rotondi con le lancette, volante in pelle. a = presenza, luce = accensione, ago = lancette, fuori = auto che escono
    function abitacolo(a, luce, ago, t, fuori) {
      const m = Math.min(W, H), cx = W / 2;
      ctx.save(); ctx.globalAlpha = a;
      // fuori dal parabrezza: la sala, un po' scura
      const g = piazza(IMG.sala, 1.15 + fuori * 0.25, 0.53, 0.5);
      ctx.drawImage(IMG.sala, g.ox, g.oy, g.w, g.h);
      ctx.fillStyle = `rgba(7,13,24,${0.35 - 0.15 * luce})`; ctx.fillRect(0, 0, W, H);
      luci(PUNTI.sala, g, a * 0.8, t);
      // tetto e montanti
      const scuro = ctx.createLinearGradient(0, 0, 0, H);
      scuro.addColorStop(0, '#0b1424'); scuro.addColorStop(1, '#050a14');
      ctx.fillStyle = scuro;
      ctx.beginPath();
      ctx.moveTo(0, 0); ctx.lineTo(W, 0); ctx.lineTo(W, H * 0.66);
      ctx.lineTo(W * 0.955, H * 0.6); ctx.quadraticCurveTo(W * 0.86, H * 0.2, W * 0.8, H * 0.13);
      ctx.quadraticCurveTo(cx, H * 0.06, W * 0.2, H * 0.13);
      ctx.quadraticCurveTo(W * 0.14, H * 0.2, W * 0.045, H * 0.6); ctx.lineTo(0, H * 0.66); ctx.closePath(); ctx.fill();
      // cucitura azzurra lungo il cielo dell'auto
      ctx.strokeStyle = `rgba(169,196,218,${0.15 + 0.35 * luce})`; ctx.lineWidth = 1; ctx.setLineDash([5, 6]);
      ctx.beginPath(); ctx.moveTo(W * 0.22, H * 0.145); ctx.quadraticCurveTo(cx, H * 0.08, W * 0.78, H * 0.145); ctx.stroke(); ctx.setLineDash([]);
      // plancia
      const yP = H * 0.6, pl = ctx.createLinearGradient(0, yP - H * 0.05, 0, H);
      pl.addColorStop(0, '#16233a'); pl.addColorStop(0.15, '#0d1729'); pl.addColorStop(1, '#04080f');
      ctx.fillStyle = pl;
      ctx.beginPath(); ctx.moveTo(0, yP + H * 0.06); ctx.quadraticCurveTo(cx, yP - H * 0.09, W, yP + H * 0.06); ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath(); ctx.fill();
      // striscia di luce che si accende dal centro verso i lati
      if (luce > 0) {
        ctx.save(); ctx.globalCompositeOperation = 'lighter';
        const larg = luce * W * 0.5, lg = ctx.createLinearGradient(cx - larg, 0, cx + larg, 0);
        lg.addColorStop(0, 'rgba(169,196,218,0)'); lg.addColorStop(0.5, 'rgba(200,224,248,.95)'); lg.addColorStop(1, 'rgba(169,196,218,0)');
        ctx.strokeStyle = lg; ctx.lineWidth = 2; ctx.shadowColor = 'rgba(169,196,218,.9)'; ctx.shadowBlur = 16;
        ctx.beginPath(); ctx.moveTo(cx - larg, yP + H * 0.06 - (1 - Math.pow(larg / (W / 2), 2)) * H * 0.075);
        for (let i = 0; i <= 40; i++) { const x = cx - larg + (2 * larg * i) / 40, u = (x - cx) / (W / 2); ctx.lineTo(x, yP + H * 0.06 - (1 - u * u) * H * 0.075 + 6); }
        ctx.stroke(); ctx.restore();
      }
      // strumenti
      const r = m * 0.085, yS = yP + H * 0.1;
      for (const [k, sx] of [[0, -1], [1, 1]]) {
        const x = cx + sx * r * 1.35;
        ctx.fillStyle = '#060b15'; ctx.beginPath(); ctx.arc(x, yS, r * 1.08, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = `rgba(213,221,228,${0.25 + 0.4 * luce})`; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, yS, r * 1.08, 0, Math.PI * 2); ctx.stroke();
        for (let i = 0; i <= 10; i++) {                         // tacche
          const ang = Math.PI * 0.75 + (i / 10) * Math.PI * 1.5, r1 = r * (i % 5 ? 0.86 : 0.8);
          ctx.strokeStyle = `rgba(214,230,244,${(0.2 + 0.7 * luce) * (i % 5 ? 0.6 : 1)})`; ctx.lineWidth = i % 5 ? 1 : 2;
          ctx.beginPath(); ctx.moveTo(x + Math.cos(ang) * r1, yS + Math.sin(ang) * r1); ctx.lineTo(x + Math.cos(ang) * r * 0.95, yS + Math.sin(ang) * r * 0.95); ctx.stroke();
        }
        ctx.save(); ctx.globalCompositeOperation = 'lighter';    // arco luminoso
        ctx.strokeStyle = `rgba(169,196,218,${0.55 * luce})`; ctx.lineWidth = 3; ctx.shadowColor = '#a9c4da'; ctx.shadowBlur = 14;
        ctx.beginPath(); ctx.arc(x, yS, r * 0.98, Math.PI * 0.75, Math.PI * 0.75 + Math.PI * 1.5 * clamp(ago + 0.08 + Math.sin(t * 2 + k) * 0.004)); ctx.stroke(); ctx.restore();
        const an = Math.PI * 0.75 + Math.PI * 1.5 * clamp(ago + 0.06 + (REDUCED ? 0 : Math.sin(t * 2 + k) * 0.004));
        ctx.strokeStyle = '#f3e3c6'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x, yS); ctx.lineTo(x + Math.cos(an) * r * 0.78, yS + Math.sin(an) * r * 0.78); ctx.stroke();
        ctx.fillStyle = '#d6dde5'; ctx.beginPath(); ctx.arc(x, yS, 3, 0, Math.PI * 2); ctx.fill();
      }
      // volante in pelle
      const R = m * 0.36, yV = H + R * 0.28;
      ctx.strokeStyle = '#121e33'; ctx.lineWidth = m * 0.045; ctx.beginPath(); ctx.arc(cx, yV, R, Math.PI * 1.08, Math.PI * 1.92); ctx.stroke();
      ctx.strokeStyle = `rgba(214,230,244,${0.18 + 0.22 * luce})`; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(cx, yV, R + m * 0.021, Math.PI * 1.1, Math.PI * 1.9); ctx.stroke();
      ctx.strokeStyle = `rgba(169,196,218,${0.1 + 0.25 * luce})`; ctx.setLineDash([3, 5]); ctx.beginPath(); ctx.arc(cx, yV, R - m * 0.012, Math.PI * 1.12, Math.PI * 1.88); ctx.stroke(); ctx.setLineDash([]);
      ctx.strokeStyle = '#0a1120'; ctx.lineWidth = m * 0.035;
      ctx.beginPath(); ctx.moveTo(cx - R * 0.95, yV - R * 0.22); ctx.lineTo(cx - R * 0.25, yV - R * 0.1); ctx.moveTo(cx + R * 0.95, yV - R * 0.22); ctx.lineTo(cx + R * 0.25, yV - R * 0.1); ctx.stroke();
      ctx.restore();
    }
    // le auto escono dal parabrezza verso chi guarda
    let VOLO = [];
    const prendi = lista => { VOLO = []; (lista || []).slice(0, 12).forEach(src => { const i = new Image(); i.decoding = 'async'; i.src = src; VOLO.push(i); }); };
    prendi(window.SALONE_AUTO_FOTO);
    window.addEventListener('auto-pronte', e => prendi(e.detail));
    function volano(v, t) {
      const n = VOLO.length; if (!n) return;
      const cx = W / 2, cy = H * 0.4, m = Math.min(W, H);
      ctx.save();
      VOLO.forEach((img, i) => {
        if (!img.complete || !img.naturalWidth) return;
        const s = (i / n) * 0.62, q = clamp((v - s) / 0.38);
        if (q <= 0 || q >= 1) return;
        const z = q * q * q, ang = i * 2.399 + 0.6, dist = z * m * 0.95;
        const w = 30 + z * m * 1.15, h = w * 0.75;
        const x = cx + Math.cos(ang) * dist * (W / m) * 0.75 - w / 2, y = cy + Math.sin(ang) * dist * 0.55 - h / 2;
        ctx.globalAlpha = Math.min(1, q * 5) * (1 - smooth(0.8, 1, q));
        ctx.save(); ctx.beginPath(); ctx.roundRect ? ctx.roundRect(x, y, w, h, 6 + z * 14) : ctx.rect(x, y, w, h); ctx.clip();
        ctx.drawImage(img, x, y, w, h); ctx.restore();
        ctx.strokeStyle = 'rgba(214,230,244,.35)'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.roundRect ? ctx.roundRect(x, y, w, h, 6 + z * 14) : ctx.rect(x, y, w, h); ctx.stroke();
      });
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
      const ap = smooth(0.13, 0.25, e);
      const gE = piazza(IMG.esterno, 1.0 + 0.3 * smooth(0, 0.25, e) + ap * 0.25 + respiro, 0.5, 0.6);
      const taglio = gE.ox + 0.5 * gE.w, apri = ap * W * 0.62;
      const aE = 1 - smooth(0.21, 0.26, e);

      // ---- 2. sala: dietro la vetrata, poi si cammina verso il fondo ----
      const aS = smooth(0.13, 0.17, e) * (1 - smooth(0.42, 0.48, e));
      const giro = smooth(0.41, 0.48, e);
      const gS = piazza(IMG.sala, 1.24 - 0.16 * ap + 0.24 * smooth(0.25, 0.45, e) + respiro, 0.53, 0.57, -giro * W * 0.18);

      // ---- 3. vetrata: ci si gira verso l'ingresso, poi si va dritti dentro la Ferrari ----
      const dentro = smooth(0.57, 0.67, e), tuffo = dentro * dentro * (3 - 2 * dentro);
      const aV = smooth(0.41, 0.48, e) * (1 - smooth(0.63, 0.67, e));
      const gV = piazza(IMG.vetrata, 1.12 + 0.12 * smooth(0.47, 0.57, e) + 2.6 * tuffo + respiro,
        mix(mix(0.3, 0.46, smooth(0.47, 0.57, e)), 0.47, smooth(0.55, 0.6, e)), mix(0.56, 0.6, tuffo), (1 - giro) * W * 0.18);

      if (aS > 0) { foto(IMG.sala, gS, aS); riflesso(gS, aS, t); luci(PUNTI.sala, gS, aS, t); }
      if (aV > 0) { foto(IMG.vetrata, gV, aV); riflesso(gV, aV * (1 - tuffo), t + 4); luci(PUNTI.vetrata, gV, aV * (1 - tuffo), t); }
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
      if (tuffo > 0.02 && tuffo < 1) {             // il buio della carrozzeria quando ci si entra
        ctx.save(); ctx.globalAlpha = smooth(0.6, 0.66, e) * (1 - smooth(0.65, 0.69, e)); ctx.fillStyle = '#070d18'; ctx.fillRect(0, 0, W, H); ctx.restore();
      }

      // ---- 4. dentro l'auto: il cruscotto si accende ----
      const aC = smooth(0.65, 0.7, e) * (1 - smooth(0.9, 0.97, e));
      if (aC > 0 && IMG.interno) {                  // foto vera degli interni: ci si siede (la foto si allarga), le luci si accendono
        const I = IMG.interno, gI = piazza(I, 1.5 - 0.38 * smooth(0.65, 0.78, e) + 0.12 * smooth(0.8, 0.97, e) + respiro, 0.5, 0.5);
        foto(I, gI, aC);
        luci(PUNTI.interno, gI, aC * smooth(0.7, 0.77, e), t);
      } else if (aC > 0) {                          // senza foto: la sala vista dal buio dell'auto
        const g = piazza(IMG.sala, 1.15 + 0.25 * smooth(0.8, 0.97, e), 0.53, 0.5);
        foto(IMG.sala, g, aC * 0.55); luci(PUNTI.sala, g, aC * 0.7, t);
      }
      // ---- 5. dal parabrezza escono le auto del salone ----
      const vola = smooth(0.8, 0.98, e);
      if (vola > 0) volano(vola, t);

      aria(t, (1 - smooth(0.6, 0.66, e)) + smooth(0.8, 0.9, e) * (1 - smooth(0.94, 0.99, e)));
      // velatura blu notte ai bordi, più leggera al centro
      const vg = ctx.createRadialGradient(W / 2, H * 0.5, Math.min(W, H) * 0.35, W / 2, H * 0.55, Math.max(W, H) * 0.8);
      vg.addColorStop(0, 'rgba(15,26,46,0)'); vg.addColorStop(1, 'rgba(15,26,46,.55)');
      ctx.fillStyle = vg; ctx.fillRect(0, 0, W, H);

      for (const el of copies) el.classList.toggle('on', e >= +el.dataset.da && e <= +el.dataset.a);
      prog.style.transform = `scaleX(${e})`;
      velo.style.opacity = smooth(0.965, 1, e);
      if (!avvia.fatto) { avvia.fatto = true; pronto(); }
    }
    requestAnimationFrame(frame);
  }
})();
