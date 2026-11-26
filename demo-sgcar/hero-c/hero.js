/*
 * MC — Variante C "Galleria Bianca"
 * Hero 3D guidata dallo scroll. Uso:
 *   <link rel="stylesheet" href="hero.css">
 *   <script type="importmap">{ "imports": {
 *     "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
 *     "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/" } }</script>
 *   import { initHero } from './hero.js'; initHero(document.querySelector('#hero'));
 * three.js viene importato dinamicamente: se la CDN non risponde, resta il fallback statico con i testi.
 */

const THREE_V = '0.160.0';
const ADDONS = `https://cdn.jsdelivr.net/npm/three@${THREE_V}/examples/jsm/`;
const MODEL_URL = 'https://cdn.jsdelivr.net/gh/mrdoob/three.js@r160/examples/models/gltf/ferrari.glb';
const AO_URL = 'https://cdn.jsdelivr.net/gh/mrdoob/three.js@r160/examples/models/gltf/ferrari_ao.png';
const MODEL_BYTES_ESTIMATE = 1_900_000; // usata solo se il server non manda Content-Length
const LOAD_TIMEOUT = 30000;

const PAINTS = [
  { id: 'rosso',  label: 'Rosso',  hex: '#b5000f', metalness: 1.0,  roughness: 0.42 },
  { id: 'nero',   label: 'Nero',   hex: '#0b0b0d', metalness: 0.85, roughness: 0.32 },
  { id: 'grigio', label: 'Grigio', hex: '#7c8086', metalness: 1.0,  roughness: 0.40 },
  { id: 'bianco', label: 'Bianco', hex: '#eeede8', metalness: 0.12, roughness: 0.30 },
];

const COPY = [
  { a: 0.00, b: 0.13, eyebrow: 'MC · Milano', title: 'La galleria<br>delle <b>supercar</b>.', sub: 'Auto esclusive, esposte come opere.' },
  { a: 0.19, b: 0.43, title: 'Scolpita per la strada.', sub: 'Ogni linea disegnata dal vento. Ogni superficie pensata per la luce.' },
  { a: 0.48, b: 0.68, title: 'Ogni dettaglio,<br>al suo posto.', sub: 'Cerchi, freni e assetto controllati prima di ogni consegna.' },
  { a: 0.80, b: 2.00, title: 'Noleggio e vendita<br>a Milano.', sub: 'Su appuntamento, nel nostro showroom.', cta: true },
];

// Fotogrammi chiave della camera (gradi, metri) lungo il progresso di scroll 0..1
const KEYS = [
  { p: 0.00, r: 22.0, az: 38, el: 7,  ty: 0.55, rot: 0,       ex: 0, veil: 0.6 },
  { p: 0.16, r: 12.5, az: 38, el: 9,  ty: 0.60, rot: 0,       ex: 0, veil: 0 },
  { p: 0.44, r: 12.0, az: 38, el: 12, ty: 0.60, rot: Math.PI, ex: 0, veil: 0 },
  { p: 0.50, r: 11.8, az: 62, el: 5,  ty: 0.45, rot: Math.PI, ex: 0, veil: 0 },
  { p: 0.58, r: 11.4, az: 72, el: 4,  ty: 0.45, rot: Math.PI, ex: 1, veil: 0 },
  { p: 0.68, r: 11.8, az: 82, el: 8,  ty: 0.45, rot: Math.PI, ex: 0, veil: 0 },
  { p: 0.88, r: 16.0, az: 90, el: 86, ty: 0.00, rot: Math.PI, ex: 0, veil: 0 },
  { p: 1.00, r: 16.0, az: 90, el: 86, ty: 0.00, rot: Math.PI, ex: 0, veil: 0 },
];

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const DEG = Math.PI / 180;

function sample(p) {
  let i = 0;
  while (i < KEYS.length - 2 && p > KEYS[i + 1].p) i++;
  const A = KEYS[i], B = KEYS[i + 1];
  const t = ease(clamp((p - A.p) / (B.p - A.p), 0, 1));
  const o = {};
  for (const k of ['r', 'az', 'el', 'ty', 'rot', 'ex', 'veil']) o[k] = lerp(A[k], B[k], t);
  return o;
}

function buildDom(root) {
  root.classList.add('mc-hero');
  root.innerHTML = `
  <div class="mc-stage">
    <canvas class="mc-canvas" aria-label="Supercar in 3D nella galleria MC" role="img"></canvas>
    <div class="mc-veil"></div>
    <header class="mc-bar">
      <a class="mc-logo" href="#" aria-label="MC">MC<span class="mc-dot" aria-hidden="true"></span></a>
      <nav aria-label="Principale"><a href="#">Collezione</a><a href="#">Noleggio</a><a href="#">Vendita</a><a href="#">Contatti</a></nav>
    </header>
    <div class="mc-copy">
      ${COPY.map((c, i) => `
        <div class="mc-line" data-i="${i}">
          ${c.eyebrow ? `<p class="mc-eyebrow">${c.eyebrow}</p>` : ''}
          <h${i === 0 ? 1 : 2} class="mc-title">${c.title}</h${i === 0 ? 1 : 2}>
          <p class="mc-sub">${c.sub}</p>
          ${c.cta ? `<div class="mc-cta"><a href="#">Prenota una visita</a><a href="#">Vedi la collezione</a></div>` : ''}
        </div>`).join('')}
    </div>
    <div class="mc-hint">Scorri</div>
    <div class="mc-swatches" role="group" aria-label="Colore vernice">
      ${PAINTS.map((c, i) => `<button class="mc-swatch" type="button" style="--c:${c.hex}" data-i="${i}" aria-label="${c.label}" aria-pressed="${i === 0}"></button>`).join('')}
      <span class="mc-swatch-label" aria-live="polite">${PAINTS[0].label}</span>
    </div>
    <div class="mc-loader" role="progressbar" aria-label="Caricamento" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
      <div class="mc-loader-track"><div class="mc-loader-fill"></div></div>
      <div class="mc-loader-pct">0%</div>
    </div>
    <p class="mc-credits">Modello 3D “Ferrari 458 Italia” di vicent091036 (CC BY 4.0), via esempi three.js · Rendering three.js (MIT)</p>
  </div>`;
  return {
    stage: root.querySelector('.mc-stage'),
    canvas: root.querySelector('.mc-canvas'),
    veil: root.querySelector('.mc-veil'),
    lines: [...root.querySelectorAll('.mc-line')],
    hint: root.querySelector('.mc-hint'),
    swatches: [...root.querySelectorAll('.mc-swatch')],
    swLabel: root.querySelector('.mc-swatch-label'),
    loader: root.querySelector('.mc-loader'),
    fill: root.querySelector('.mc-loader-fill'),
    pct: root.querySelector('.mc-loader-pct'),
  };
}

export function initHero(container) {
  const ui = buildDom(container);
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const mobile = matchMedia('(pointer: coarse)').matches || innerWidth < 768;
  const api = { state: 'loading', quality: mobile ? 'mobile' : 'desktop', destroy: () => {}, setPaint: () => {} };

  let shownPct = 0;
  const setPct = (v) => {
    shownPct = Math.max(shownPct, Math.round(clamp(v, 0, 100)));
    ui.fill.style.width = shownPct + '%';
    ui.pct.textContent = shownPct + '%';
    ui.loader.setAttribute('aria-valuenow', shownPct);
  };

  const fallback = (why) => {
    if (api.state === 'fallback') return;
    api.state = 'fallback'; api.reason = String(why && why.message || why);
    container.classList.add('mc-static', 'mc-fallback');
    container.classList.remove('mc-reduced', 'mc-ready');
    api.destroy();
    if (why) console.info('[MC hero] fallback:', api.reason);
  };

  // WebGL2 (richiesto da three r160)
  let gl = null;
  try { gl = ui.canvas.getContext('webgl2', { antialias: true, alpha: false, powerPreference: 'high-performance' }); } catch (e) { /* */ }
  if (!gl) { fallback('WebGL2 non disponibile'); return api; }
  if (reduced) container.classList.add('mc-static', 'mc-reduced');

  const timer = setTimeout(() => api.state === 'loading' && fallback('timeout caricamento'), LOAD_TIMEOUT);

  start().catch((e) => { clearTimeout(timer); fallback(e); });
  return api;

  async function start() {
    const [THREE, { GLTFLoader }, { DRACOLoader }, { RoomEnvironment }] = await Promise.all([
      import('three'),
      import('three/addons/loaders/GLTFLoader.js'),
      import('three/addons/loaders/DRACOLoader.js'),
      import('three/addons/environments/RoomEnvironment.js'),
    ]);
    if (api.state === 'fallback') return;
    setPct(6);

    const BG = new THREE.Color('#f5f5f3');
    const renderer = new THREE.WebGLRenderer({ canvas: ui.canvas, context: gl, antialias: true });
    let pr = Math.min(devicePixelRatio || 1, mobile ? 1.5 : 2);
    renderer.setPixelRatio(pr);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.95;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(BG, 1);

    const scene = new THREE.Scene();
    scene.background = BG;
    scene.fog = new THREE.Fog(BG, 16, 42);
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(renderer), 0.04).texture;

    const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 120);

    // Cyclorama: pavimento che curva dolcemente nella parete di fondo
    {
      const prof = [];
      for (let z = 40; z > -6; z -= 2) prof.push([z, 0]);
      const R = 7, N = 24;
      for (let i = 0; i <= N; i++) { const a = (i / N) * Math.PI / 2; prof.push([-6 - Math.sin(a) * R, R - Math.cos(a) * R]); }
      prof.push([-6 - R, 16], [-6 - R, 40]);
      const W = 60, X = 24, pos = [], idx = [];
      prof.forEach(([z, y]) => { for (let j = 0; j <= X; j++) pos.push(-W + (2 * W * j) / X, y, z); });
      for (let i = 0; i < prof.length - 1; i++) for (let j = 0; j < X; j++) {
        const a = i * (X + 1) + j, b = a + X + 1;
        idx.push(a, b, a + 1, b, b + 1, a + 1);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
      g.setIndex(idx); g.computeVertexNormals();
      const cyc = new THREE.Mesh(g, new THREE.MeshLambertMaterial({ color: 0xf2f2f0, toneMapped: false }));
      cyc.receiveShadow = true;
      scene.add(cyc);
    }

    // Luce naturale morbida dall'alto (lucernario) + riempimento
    const hemi = new THREE.HemisphereLight(0xffffff, 0xe8e6e1, 1.9);
    scene.add(hemi);
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(2.5, 12, 4);
    sun.castShadow = true;
    sun.shadow.mapSize.set(mobile ? 1024 : 2048, mobile ? 1024 : 2048);
    Object.assign(sun.shadow.camera, { left: -5, right: 5, top: 5, bottom: -5, near: 4, far: 22 });
    sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.02; sun.shadow.radius = 4;
    sun.shadow.intensity = 0.55;
    scene.add(sun);

    // Materiali (come nell'esempio ufficiale webgl_materials_car)
    const P0 = PAINTS[0];
    const bodyMat = new THREE.MeshPhysicalMaterial({ color: P0.hex, metalness: P0.metalness, roughness: P0.roughness, clearcoat: 1, clearcoatRoughness: 0.03 });
    const detailsMat = new THREE.MeshStandardMaterial({ color: 0xffffff, metalness: 1, roughness: 0.5 });
    const glassMat = mobile
      ? new THREE.MeshPhysicalMaterial({ color: 0x111114, metalness: 0.2, roughness: 0, transparent: true, opacity: 0.55 })
      : new THREE.MeshPhysicalMaterial({ color: 0xffffff, metalness: 0.25, roughness: 0, transmission: 1 });

    // Caricamento modello con percentuale reale
    const draco = new DRACOLoader().setDecoderPath(ADDONS + 'libs/draco/gltf/');
    const loader = new GLTFLoader().setDRACOLoader(draco);
    const gltf = await new Promise((res, rej) => loader.load(MODEL_URL, res, (e) => {
      const total = e.lengthComputable && e.total ? e.total : MODEL_BYTES_ESTIMATE;
      setPct(6 + Math.min(0.97, e.loaded / total) * 86);
    }, rej));
    if (api.state === 'fallback') return;
    setPct(94);

    const car = gltf.scene.children[0] || gltf.scene;
    const pivot = new THREE.Group();
    scene.add(pivot);
    pivot.add(gltf.scene);
    const named = (n) => car.getObjectByName(n) || gltf.scene.getObjectByName(n);
    const body = named('body');
    if (body) body.material = bodyMat;
    ['rim_fl', 'rim_fr', 'rim_rr', 'rim_rl', 'trim'].forEach((n) => { const o = named(n); if (o) o.material = detailsMat; });
    const glass = named('glass'); if (glass) glass.material = glassMat;
    gltf.scene.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = false; } });

    // Normalizza: centrato, appoggiato a terra, lunghezza ~4.6 m
    const box = new THREE.Box3().setFromObject(gltf.scene);
    const size = box.getSize(new THREE.Vector3());
    const s = 4.6 / Math.max(size.x, size.z);
    gltf.scene.scale.setScalar(s);
    box.setFromObject(gltf.scene);
    const c = box.getCenter(new THREE.Vector3());
    gltf.scene.position.set(-c.x, -box.min.y, -c.z);
    const lengthAlongX = size.x > size.z;
    if (lengthAlongX) pivot.rotation.y = Math.PI / 2;

    // Ombra di contatto morbida (texture AO del modello; fallback radiale procedurale)
    const contact = new THREE.Mesh(new THREE.PlaneGeometry(0.655 * 4, 1.3 * 4), new THREE.MeshBasicMaterial({ transparent: true, toneMapped: false, depthWrite: false }));
    contact.rotation.x = -Math.PI / 2; contact.position.y = 0.002; contact.renderOrder = 2;
    const inner = new THREE.Group(); inner.add(contact); pivot.add(inner);
    const radial = () => {
      const cv = document.createElement('canvas'); cv.width = 128; cv.height = 256;
      const x = cv.getContext('2d'); x.translate(64, 128); x.scale(1, 2);
      const gr = x.createRadialGradient(0, 0, 4, 0, 0, 62);
      gr.addColorStop(0, 'rgba(0,0,0,.55)'); gr.addColorStop(0.55, 'rgba(0,0,0,.28)'); gr.addColorStop(1, 'rgba(0,0,0,0)');
      x.fillStyle = gr; x.fillRect(-64, -64, 128, 128);
      const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; return t;
    };
    contact.material.map = radial();
    new THREE.TextureLoader().load(AO_URL, (t) => {
      Object.assign(contact.material, { map: t, blending: THREE.MultiplyBlending, premultipliedAlpha: true });
      contact.material.needsUpdate = true; dirty = true;
    });

    // Ruote per l'"esploso" (traslazione delle sole mesh delle ruote)
    const corners = [];
    for (const k of ['fl', 'fr', 'rl', 'rr']) {
      const w = named('wheel_' + k); if (!w) continue;
      const parts = [w]; const rim = named('rim_' + k);
      if (rim) { let d = false; rim.traverseAncestors((a) => { if (a === w) d = true; }); if (!d) parts.push(rim); }
      const wc = new THREE.Box3().setFromObject(w).getCenter(new THREE.Vector3());
      inner.worldToLocal(wc);
      const side = Math.sign(wc.x) || (k[1] === 'l' ? 1 : -1);
      corners.push(parts.map((o) => {
        // direzione "verso l'esterno" nello spazio del genitore della mesh
        const p0 = o.position.clone();
        const a = o.parent.worldToLocal(inner.localToWorld(new THREE.Vector3(0, 0, 0)));
        const b = o.parent.worldToLocal(inner.localToWorld(new THREE.Vector3(side, 0, 0)));
        return { o, p0, dir: b.sub(a) };
      }));
    }
    const canExplode = corners.length === 4;

    // ---- stato e animazione ----
    let target = 0, sp = 0, dirty = true, running = true, visible = true, raf = 0, last = performance.now();
    const curColor = bodyMat.color.clone(), tgtColor = bodyMat.color.clone();
    let tgtMetal = P0.metalness, tgtRough = P0.roughness;

    const readScroll = () => {
      if (reduced) { target = 0.16; return; }
      const r = container.getBoundingClientRect();
      const span = Math.max(1, r.height - innerHeight);
      target = clamp(-r.top / span, 0, 1);
    };
    readScroll(); sp = target;

    const tmp = new THREE.Vector3();
    function apply(p) {
      const k = sample(p);
      const w = ui.canvas.clientWidth || 1, h = ui.canvas.clientHeight || 1, asp = w / h;
      const top = smooth(30, 80, k.el);
      let az = k.az;
      if (asp < 1) az = lerp(k.az, 0, top); // su mobile in pianta l'auto resta verticale
      if (!canExplode) k.r *= 1 - 0.28 * smooth(0.48, 0.58, p) * (1 - smooth(0.62, 0.72, p)); // zoom sui dettagli
      const fitSide = asp < 1 ? clamp(0.72 / asp, 1, 2.0) : clamp(1.5 / asp, 0.9, 1.25);
      const fitTop = asp < 1 ? clamp(0.62 / asp, 1, 1.55) : clamp(1.6 / asp, 1, 1.3);
      const r = k.r * lerp(fitSide, fitTop, top);
      const el = k.el * DEG, a = az * DEG;
      camera.position.set(r * Math.cos(el) * Math.sin(a), k.ty + r * Math.sin(el), r * Math.cos(el) * Math.cos(a));
      tmp.set(0, k.ty, 0);
      camera.lookAt(tmp);
      scene.fog.near = r + 4; scene.fog.far = r + 34; // nebbia relativa: l'orizzonte sparisce nel bianco, l'auto mai
      const off = asp < 1 ? lerp(0.12, 0.17, top) : lerp(0.1, 0.19, top);
      camera.setViewOffset(w, h, 0, -h * off, w, h);
      camera.updateProjectionMatrix();
      inner.rotation.y = k.rot;
      if (canExplode) {
        const e = k.ex * 0.45;
        corners.forEach((parts) => parts.forEach(({ o, p0, dir }) => o.position.copy(p0).addScaledVector(dir, e)));
      }
      ui.veil.style.opacity = reduced ? 0 : k.veil.toFixed(3);
      ui.hint.style.opacity = reduced ? 0 : (1 - smooth(0.0, 0.04, p)).toFixed(3);
      if (!reduced) ui.lines.forEach((el2, i) => {
        const c = COPY[i];
        const vin = c.a <= 0 ? 1 : smooth(c.a, c.a + 0.05, p);
        const vout = 1 - smooth(c.b - 0.05, c.b, p);
        const v = Math.min(vin, vout);
        el2.style.opacity = v.toFixed(3);
        el2.style.transform = `translate3d(0,${((1 - vin) * 24 - (1 - vout) * 24).toFixed(1)}px,0)`;
        el2.style.pointerEvents = v > 0.5 ? 'auto' : 'none';
      });
    }

    // inquadratura: sposta la proiezione per lasciare spazio ai testi in alto
    function resize() {
      const w = ui.canvas.clientWidth || 1, h = ui.canvas.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.fov = w / h < 1 ? 34 : 28;
      camera.updateProjectionMatrix();
      dirty = true;
    }
    resize();

    api.setPaint = (i) => {
      const pnt = PAINTS[i]; if (!pnt) return;
      tgtColor.set(pnt.hex); tgtMetal = pnt.metalness; tgtRough = pnt.roughness;
      ui.swatches.forEach((b, j) => b.setAttribute('aria-pressed', String(j === i)));
      ui.swLabel.textContent = pnt.label;
      if (reduced) { curColor.copy(tgtColor); bodyMat.metalness = tgtMetal; bodyMat.roughness = tgtRough; }
      dirty = true; api.paint = pnt.id;
    };
    ui.swatches.forEach((b, i) => b.addEventListener('click', () => api.setPaint(i)));

    // qualità adattiva: se i primi fotogrammi sono lenti abbassa la risoluzione
    let fCount = 0, fTime = 0, downgraded = false;
    function frame(now) {
      raf = 0;
      if (!running) return;
      const dt = Math.min(0.1, (now - last) / 1000); last = now;
      if (!reduced) {
        const k = 1 - Math.exp(-dt * 5.5);
        const d = target - sp;
        if (Math.abs(d) > 1e-5) { sp += d * k; dirty = true; } else sp = target;
      }
      const ck = reduced ? 1 : 1 - Math.exp(-dt * 3.2);
      if (Math.abs(curColor.r - tgtColor.r) + Math.abs(curColor.g - tgtColor.g) + Math.abs(curColor.b - tgtColor.b) > 1e-4 ||
          Math.abs(bodyMat.metalness - tgtMetal) + Math.abs(bodyMat.roughness - tgtRough) > 1e-4) {
        curColor.lerp(tgtColor, ck);
        bodyMat.metalness = lerp(bodyMat.metalness, tgtMetal, ck);
        bodyMat.roughness = lerp(bodyMat.roughness, tgtRough, ck);
        dirty = true;
      }
      bodyMat.color.copy(curColor);
      if (dirty) {
        const t0 = performance.now();
        apply(sp);
        renderer.render(scene, camera);
        dirty = false;
        if (!downgraded && fCount < 40) {
          fCount++; fTime += dt;
          if (fCount === 40 && fTime / 40 > 1 / 40 && pr > 1) {
            downgraded = true; pr = Math.max(1, pr * 0.66); renderer.setPixelRatio(pr); resize(); api.quality += '-ridotta';
          }
        }
        api.lastRenderMs = performance.now() - t0;
      }
      if (visible) raf = requestAnimationFrame(frame);
    }
    const kick = () => { if (!raf && running && visible) { last = performance.now(); raf = requestAnimationFrame(frame); } };

    const onScroll = () => { readScroll(); kick(); };
    addEventListener('scroll', onScroll, { passive: true });
    const ro = new ResizeObserver(() => { resize(); readScroll(); kick(); });
    ro.observe(ui.stage);
    const io = new IntersectionObserver(([en]) => { visible = en.isIntersecting; kick(); });
    io.observe(container);
    ui.canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); fallback('contesto WebGL perso'); });

    api.destroy = () => {
      running = false; cancelAnimationFrame(raf);
      removeEventListener('scroll', onScroll); ro.disconnect(); io.disconnect();
      renderer.dispose(); draco.dispose(); pmrem.dispose();
    };
    api.progress = () => sp;
    api.explodes = canExplode;

    // prima immagine pronta prima di togliere il caricamento (compila gli shader)
    apply(sp);
    renderer.compile(scene, camera);
    renderer.render(scene, camera);
    clearTimeout(timer);
    setPct(100);
    api.state = 'ready';
    container.classList.add('mc-ready');
    kick();
  }
}
