// Yuri Car — scena leggera in 2D (canvas, niente librerie): strada notturna in parallasse, auto al neon e carro
// attrezzi con lampeggiante, guidati dallo scroll. Pesa pochi KB e gira fluida anche sui telefoni.
(() => {
  const cv = document.getElementById('scene'), show = document.getElementById('top');
  const ctx = cv.getContext('2d');
  const copies = [...document.querySelectorAll('.copy')];
  const fade = document.getElementById('fade'), prog = document.getElementById('prog'), hint = document.getElementById('hint');
  const nav = document.getElementById('nav'), fab = document.querySelector('.fab');
  const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
  const smooth = (a, b, v) => { const x = clamp((v - a) / (b - a)); return x * x * (3 - 2 * x); };
  const ease = x => 1 - Math.pow(1 - x, 3);
  let W = 0, H = 0, DPR = 1, target = 0, cur = 0, dist = 0, last = performance.now(), t0 = last, visible = true, ready = false;

  // stelle, palazzi e lampioni generati una volta (stessa città a ogni visita)
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const stars = Array.from({ length: 140 }, () => [rnd(), rnd() * 0.55, rnd() * 1.2 + 0.2, rnd() * 6]);
  const city = []; { let x = 0; while (x < 2.4) { const w = 0.03 + rnd() * 0.07; city.push([x, w, 0.06 + rnd() * 0.22, rnd()]); x += w + 0.004; } }
  const cityFar = []; { let x = 0; while (x < 2.4) { const w = 0.04 + rnd() * 0.09; cityFar.push([x, w, 0.04 + rnd() * 0.14]); x += w; } }

  function resize() {
    DPR = Math.min(devicePixelRatio || 1, innerWidth < 700 ? 1.5 : 2);
    W = cv.clientWidth; H = cv.clientHeight;
    cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }
  function onScroll() {
    const r = show.getBoundingClientRect();
    target = clamp(-r.top / (r.height - innerHeight));
    nav.classList.toggle('solid', r.bottom < innerHeight * 0.6 || target > 0.03);
    fab && fab.classList.toggle('on', target > 0.05);
  }

  // ---------- disegni ----------
  function glowStroke(color, blur, width) { ctx.strokeStyle = color; ctx.shadowColor = color; ctx.shadowBlur = blur; ctx.lineWidth = width; ctx.stroke(); }

  function car(x, y, L, t, lights) {           // coupé di profilo, muso a destra; (x,y) = punto a terra posteriore
    ctx.save(); ctx.translate(x, y); ctx.scale(L, L);
    const lw = 2.2 / L;
    // fari: cono di luce davanti
    if (lights > 0) {
      const g = ctx.createLinearGradient(1, -0.12, 2.4, -0.05);
      g.addColorStop(0, `rgba(170,200,255,${0.35 * lights})`); g.addColorStop(1, 'rgba(170,200,255,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.moveTo(0.98, -0.13); ctx.lineTo(2.6, -0.26); ctx.lineTo(2.6, 0.06); ctx.lineTo(0.98, -0.09); ctx.fill();
    }
    // ombra a terra
    const sh = ctx.createRadialGradient(0.5, 0.01, 0, 0.5, 0.01, 0.62);
    sh.addColorStop(0, 'rgba(36,87,255,.35)'); sh.addColorStop(1, 'rgba(36,87,255,0)');
    ctx.fillStyle = sh; ctx.fillRect(-0.2, -0.05, 1.4, 0.1);
    // carrozzeria
    ctx.beginPath();
    ctx.moveTo(0.02, -0.06);
    ctx.bezierCurveTo(0.0, -0.13, 0.02, -0.19, 0.08, -0.21);
    ctx.bezierCurveTo(0.18, -0.25, 0.26, -0.27, 0.33, -0.33);
    ctx.bezierCurveTo(0.41, -0.39, 0.52, -0.40, 0.6, -0.37);
    ctx.bezierCurveTo(0.67, -0.34, 0.71, -0.28, 0.76, -0.25);
    ctx.bezierCurveTo(0.86, -0.22, 0.95, -0.2, 0.99, -0.15);
    ctx.bezierCurveTo(1.01, -0.11, 1.0, -0.07, 0.98, -0.05);
    ctx.lineTo(0.92, -0.05); ctx.arc(0.8, -0.05, 0.11, 0, Math.PI, true);
    ctx.lineTo(0.32, -0.05); ctx.arc(0.2, -0.05, 0.11, 0, Math.PI, true);
    ctx.closePath();
    const body = ctx.createLinearGradient(0, -0.4, 0, 0);
    body.addColorStop(0, '#0d1633'); body.addColorStop(1, '#03050c');
    ctx.fillStyle = body; ctx.fill();
    glowStroke('#2f62ff', 18, lw * 1.6);
    ctx.shadowBlur = 0; ctx.strokeStyle = 'rgba(190,210,255,.9)'; ctx.lineWidth = lw * 0.6; ctx.stroke();
    // vetri
    ctx.beginPath(); ctx.moveTo(0.36, -0.315); ctx.bezierCurveTo(0.43, -0.365, 0.53, -0.37, 0.59, -0.35);
    ctx.bezierCurveTo(0.64, -0.33, 0.68, -0.29, 0.70, -0.265); ctx.lineTo(0.38, -0.265); ctx.closePath();
    const gl = ctx.createLinearGradient(0.36, -0.37, 0.7, -0.26); gl.addColorStop(0, 'rgba(120,160,255,.35)'); gl.addColorStop(1, 'rgba(20,30,70,.6)');
    ctx.fillStyle = gl; ctx.fill(); ctx.strokeStyle = 'rgba(160,190,255,.7)'; ctx.lineWidth = lw * 0.5; ctx.stroke();
    // linea di fiancata
    ctx.beginPath(); ctx.moveTo(0.06, -0.17); ctx.bezierCurveTo(0.4, -0.2, 0.7, -0.2, 0.97, -0.16);
    glowStroke('rgba(120,160,255,.9)', 8, lw * 0.6); ctx.shadowBlur = 0;
    // fari e luci posteriori
    ctx.fillStyle = '#e8f0ff'; ctx.shadowColor = '#9cc0ff'; ctx.shadowBlur = 20 * lights + 4;
    ctx.beginPath(); ctx.ellipse(0.955, -0.155, 0.035, 0.012, -0.2, 0, 7); ctx.fill();
    ctx.fillStyle = '#ff2a3a'; ctx.shadowColor = '#ff2a3a'; ctx.shadowBlur = 16;
    ctx.fillRect(0.0, -0.175, 0.03, 0.035); ctx.shadowBlur = 0;
    // ruote che girano
    for (const wx of [0.2, 0.8]) wheel(wx, -0.05, 0.085, t, lw);
    ctx.restore();
  }
  function wheel(x, y, r, t, lw) {
    ctx.save(); ctx.translate(x, y);
    ctx.fillStyle = '#05070d'; ctx.beginPath(); ctx.arc(0, 0, r, 0, 7); ctx.fill();
    ctx.beginPath(); ctx.arc(0, 0, r, 0, 7); glowStroke('#7f9cff', 10, lw * 1.1); ctx.shadowBlur = 0;
    ctx.rotate(t); ctx.strokeStyle = 'rgba(200,215,255,.75)'; ctx.lineWidth = lw * 0.6;
    for (let i = 0; i < 5; i++) { ctx.rotate(Math.PI * 2 / 5); ctx.beginPath(); ctx.moveTo(0, r * 0.2); ctx.lineTo(0, r * 0.78); ctx.stroke(); }
    ctx.restore();
  }
  function truck(x, y, L, t, tw) {             // carro attrezzi con pianale, cabina a destra
    ctx.save(); ctx.translate(x, y); ctx.scale(L, L);
    const lw = 2.2 / L;
    // pianale
    ctx.beginPath(); ctx.moveTo(0, -0.17); ctx.lineTo(0.66, -0.17); ctx.lineTo(0.66, -0.11); ctx.lineTo(0.05, -0.11); ctx.closePath();
    ctx.fillStyle = '#0b1020'; ctx.fill(); glowStroke('#c9d3ea', 10, lw * 1.2);
    // telaio
    ctx.beginPath(); ctx.moveTo(0.04, -0.11); ctx.lineTo(0.98, -0.11); glowStroke('#7f8aa6', 4, lw); ctx.shadowBlur = 0;
    // cabina
    ctx.beginPath(); ctx.moveTo(0.68, -0.08); ctx.lineTo(0.68, -0.36); ctx.lineTo(0.86, -0.36);
    ctx.quadraticCurveTo(0.9, -0.36, 0.93, -0.3); ctx.lineTo(0.99, -0.2); ctx.lineTo(1.0, -0.08); ctx.closePath();
    const cb = ctx.createLinearGradient(0, -0.36, 0, -0.08); cb.addColorStop(0, '#e9edf6'); cb.addColorStop(1, '#8e97ad');
    ctx.fillStyle = cb; ctx.fill(); glowStroke('#ffffff', 8, lw * 0.8); ctx.shadowBlur = 0;
    ctx.beginPath(); ctx.moveTo(0.81, -0.33); ctx.lineTo(0.88, -0.33); ctx.lineTo(0.95, -0.22); ctx.lineTo(0.81, -0.22); ctx.closePath();
    ctx.fillStyle = '#122046'; ctx.fill();
    ctx.fillStyle = '#2457ff'; ctx.fillRect(0.7, -0.2, 0.29, 0.03);           // fascia blu Yuri Car
    // lampeggiante
    const on = Math.sin(tw * 9) > 0;
    ctx.fillStyle = on ? '#ffb020' : '#ff3b3b'; ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 40;
    ctx.fillRect(0.73, -0.39, 0.1, 0.03); ctx.shadowBlur = 0;
    if (on) { const g = ctx.createRadialGradient(0.78, -0.38, 0, 0.78, -0.38, 0.5); g.addColorStop(0, 'rgba(255,170,40,.28)'); g.addColorStop(1, 'rgba(255,170,40,0)'); ctx.fillStyle = g; ctx.fillRect(0.2, -0.9, 1.2, 1); }
    // braccio
    ctx.beginPath(); ctx.moveTo(0.62, -0.17); ctx.lineTo(0.66, -0.27); ctx.lineTo(0.68, -0.27); glowStroke('#c9d3ea', 6, lw * 0.8); ctx.shadowBlur = 0;
    for (const wx of [0.16, 0.42, 0.86]) wheel(wx, -0.05, 0.07, t, lw);
    ctx.restore();
  }

  // ---------- fotogramma ----------
  function frame(now) {
    requestAnimationFrame(frame);
    if (!visible || document.hidden) { last = now; return; }
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    const t = (now - t0) / 1000;
    cur += (target - cur) * 0.08;
    const p = cur, mob = W < 700;
    const intro = RM ? 1 : clamp(t / 1.6);
    // velocità: parte piano, accelera a metà, rallenta quando arriva il carro attrezzi
    const speed = RM ? 0 : (0.25 + 1.6 * smooth(0.12, 0.4, p) - 1.3 * smooth(0.52, 0.66, p)) * (0.4 + 0.6 * intro);
    dist += speed * dt;

    const road = H * (mob ? 0.58 : 0.72);
    // cielo
    const sky = ctx.createLinearGradient(0, 0, 0, road);
    sky.addColorStop(0, '#02030a'); sky.addColorStop(0.7, '#060b1f'); sky.addColorStop(1, '#0d1a4a');
    ctx.fillStyle = sky; ctx.fillRect(0, 0, W, H);
    for (const [sx, sy, sr, ph] of stars) { ctx.globalAlpha = 0.35 + 0.35 * Math.sin(t * 1.3 + ph); ctx.fillStyle = '#cfe0ff'; ctx.fillRect(sx * W, sy * road, sr, sr); }
    ctx.globalAlpha = 1;
    // bagliore all'orizzonte
    const hz = ctx.createRadialGradient(W * 0.5, road, 0, W * 0.5, road, Math.max(W, H) * 0.7);
    hz.addColorStop(0, 'rgba(36,87,255,.35)'); hz.addColorStop(1, 'rgba(36,87,255,0)');
    ctx.fillStyle = hz; ctx.fillRect(0, 0, W, road);
    // città lontana e vicina (parallasse)
    const layer = (arr, k, col, lit) => {
      const off = (dist * k) % 1.2;
      for (const b of arr) {
        const bx = ((b[0] - off + 2.4) % 2.4 - 0.2) * W;
        if (bx > W || bx + b[1] * W < 0) continue;
        const bh = b[2] * H;
        ctx.fillStyle = col; ctx.fillRect(bx, road - bh, b[1] * W, bh);
        if (lit && b[3] > 0.35) { ctx.fillStyle = 'rgba(140,175,255,.55)'; for (let wy = road - bh + 8; wy < road - 6; wy += 11) for (let wx = bx + 5; wx < bx + b[1] * W - 4; wx += 9) if (((wx * 7 + wy * 13) | 0) % 5 === 0) ctx.fillRect(wx, wy, 2, 3); }
      }
    };
    layer(cityFar, 0.05, '#0a1230', false);
    layer(city, 0.12, '#070c1e', true);
    // strada
    const rg = ctx.createLinearGradient(0, road, 0, H); rg.addColorStop(0, '#0b0f1c'); rg.addColorStop(1, '#03040a');
    ctx.fillStyle = rg; ctx.fillRect(0, road, W, H - road);
    ctx.fillStyle = 'rgba(80,120,255,.5)'; ctx.fillRect(0, road, W, 1.5);
    // lampioni
    const lampGap = Math.max(260, W * 0.32), lo = (dist * W * 0.6) % lampGap;
    for (let lx = -lo; lx < W + lampGap; lx += lampGap) {
      ctx.fillStyle = '#141a2e'; ctx.fillRect(lx, road - H * 0.34, 3, H * 0.34);
      ctx.fillRect(lx, road - H * 0.34, 40, 3);
      const lg = ctx.createRadialGradient(lx + 38, road - H * 0.33, 0, lx + 38, road - H * 0.33, H * 0.38);
      lg.addColorStop(0, 'rgba(120,160,255,.22)'); lg.addColorStop(1, 'rgba(120,160,255,0)');
      ctx.fillStyle = lg; ctx.fillRect(lx - H * 0.4, road - H * 0.72, H * 0.9, H * 0.8);
    }
    // segnaletica che scorre
    const lane = road + (H - road) * 0.42, dash = 70, gap = 70, off = (dist * W * 1.1) % (dash + gap);
    ctx.fillStyle = 'rgba(200,210,235,.55)';
    for (let x = -off; x < W; x += dash + gap) ctx.fillRect(x, lane, dash, 3);
    // scie di velocità
    const fast = smooth(0.15, 0.4, p) * (1 - smooth(0.5, 0.62, p));
    if (fast > 0.02) { for (let i = 0; i < 26; i++) { const yy = road - H * 0.05 + ((i * 97) % 100) / 100 * (H - road) * 0.9; const xx = W - ((dist * W * 3 + i * 211) % (W + 400)); ctx.fillStyle = `rgba(110,150,255,${0.18 * fast})`; ctx.fillRect(xx, yy, 140 + (i % 4) * 60, 1.5); } }

    // auto e carro attrezzi
    const L = clamp(Math.min(W * (mob ? 0.62 : 0.36), H * 0.9), 160, 560);
    const ground = road + (H - road) * 0.32;
    const tIn = smooth(0.5, 0.74, p);
    const carX = W * 0.5 - L / 2 - tIn * W * (mob ? 0.3 : 0.2) + (1 - ease(intro)) * -W * 0.6;
    const bob = Math.sin(t * 9) * speed * 0.6;
    car(carX, ground + bob, L, dist * 26, 1 - 0.6 * tIn);
    if (tIn > 0.001) {
      const TL = L * 0.95, stop = Math.min(carX + L + W * (mob ? 0.02 : 0.04), W - TL - W * 0.03), tx = W + 40 - tIn * (W + 40 - stop);
      truck(tx, ground, TL, dist * 26, t);
    }

    // testi a tempo
    for (const c of copies) { const a = +c.dataset.from, b = +c.dataset.to; c.classList.toggle('on', p >= a && p < b && (c.classList.contains('c0') ? intro > 0.6 : true)); }
    fade.style.opacity = smooth(0.92, 1, p);
    prog.style.transform = `scaleY(${p})`;
    hint.style.opacity = p > 0.01 || intro < 0.9 ? 0 : 1;
    if (!ready) { ready = true; dispatchEvent(new Event('car-ready')); }
  }

  addEventListener('resize', resize); addEventListener('scroll', onScroll, { passive: true });
  new IntersectionObserver(e => { visible = e[0].isIntersecting; }, { threshold: 0 }).observe(show);
  resize(); onScroll(); requestAnimationFrame(frame);
})();
