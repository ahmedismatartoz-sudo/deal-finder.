/**
 * MC — Hero 3D "STUDIO" (variante A)
 *
 * Uso:
 *   <link rel="stylesheet" href="hero.css">
 *   <script type="importmap">{ "imports": {
 *     "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
 *     "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/" } }</script>
 *   <script type="module">
 *     import { initHero } from './hero.js';
 *     const hero = initHero(document.querySelector('#hero'));
 *   </script>
 *
 * three.js viene importato dinamicamente: se il CDN o WebGL non sono disponibili il modulo
 * si carica comunque e mostra il fallback elegante con i testi.
 */

const CDN = 'https://cdn.jsdelivr.net';
const DEFAULTS = {
  modelUrl: `${CDN}/gh/mrdoob/three.js@r160/examples/models/gltf/ferrari.glb`,
  aoUrl: `${CDN}/gh/mrdoob/three.js@r160/examples/models/gltf/ferrari_ao.png`,
  dracoPath: `${CDN}/npm/three@0.160.0/examples/jsm/libs/draco/gltf/`,
  fallbackImage: '',          // opzionale: foto dello showroom per il fallback
  quality: 'auto',            // 'auto' | 'high' | 'low'
  adaptive: true,             // riduce la qualità se gli fps calano
  paint: 'rosso',
  credits: 'Modello 3D: “Ferrari 458 Italia” di vicent091036, dagli esempi di three.js (MIT). Solo dimostrativo.',
};

const PAINTS = {
  rosso: { label: 'Rosso Corsa', hex: '#8f0a0e', ui: '#a3141a', metal: 0.55, rough: 0.32 },
  nero:  { label: 'Nero Daytona', hex: '#060607', ui: '#111113', metal: 0.45, rough: 0.28 },
  nardo: { label: 'Grigio Nardò', hex: '#8a8c89', ui: '#8d8f8b', metal: 0.10, rough: 0.42 },
  bianco:{ label: 'Bianco Avus', hex: '#e8e8e4', ui: '#ecebe7', metal: 0.08, rough: 0.36 },
};

// Testi delle fasi: [inizio, fine] in progresso di scroll 0..1
const PHASES = [
  { range: [0.02, 0.22], eyebrow: 'MC — Milano', title: 'Precisione.', sub: 'Supercar selezionate, una alla volta, sotto la luce giusta.' },
  { range: [0.29, 0.48], eyebrow: 'Forma', title: 'La luce, sulla linea.', sub: 'Ogni superficie racconta come è stata pensata.', align: 'right' },
  { range: [0.55, 0.75], eyebrow: 'Dettaglio', title: 'Ogni dettaglio, preparato a mano.', sub: 'Ispezione, detailing e consegna curati in sede.' },
  { range: [0.81, 1.10], eyebrow: 'MC', title: 'Noleggio e vendita a Milano.', sub: 'Su appuntamento, nel nostro showroom.', align: 'center', pos: 'top' },
];

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const easeInOut = (t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}

function hasWebGL() {
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  } catch (e) { return false; }
}

function detectLowTier() {
  if (window.innerWidth < 768) return true;
  const mem = navigator.deviceMemory || 8;
  const cores = navigator.hardwareConcurrency || 8;
  if (mem <= 4 && cores <= 4) return true;
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    const r = ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : '';
    if (/SwiftShader|llvmpipe|softpipe|Mali-[4T]|Adreno \(TM\) [3-5]|PowerVR|Intel\(R\) HD Graphics [2-4]\d{3}/i.test(r)) return true;
  } catch (e) { /* ignore */ }
  return false;
}

export function initHero(container, options = {}) {
  const opt = { ...DEFAULTS, ...options };
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ------------------------------------------------------------------ DOM */
  container.classList.add('mch');
  if (reduced) container.classList.add('is-static');
  container.innerHTML = '';

  const stage = el('div', 'mch__stage');
  const backdrop = el('div', 'mch__backdrop');
  const img = el('img', 'mch__backdrop-img');
  img.alt = '';
  img.decoding = 'async';
  backdrop.appendChild(img);
  const canvas = el('canvas', 'mch__canvas');
  canvas.setAttribute('aria-hidden', 'true');
  const vignette = el('div', 'mch__vignette');
  const fade = el('div', 'mch__fade');

  const top = el('div', 'mch__top', '<span class="mch__brand">MC<i></i></span><span class="mch__place">Milano · Noleggio e vendita</span>');

  const copy = el('div', 'mch__copy');
  const phaseEls = PHASES.map((ph, i) => {
    const n = el('div', 'mch__phase');
    if (ph.align) n.dataset.align = ph.align;
    if (ph.pos) n.dataset.pos = ph.pos;
    const H = i === 0 ? 'h1' : 'h2';
    n.innerHTML = `<span class="mch__eyebrow">${ph.eyebrow}</span><${H} class="mch__title">${ph.title}</${H}><p class="mch__sub">${ph.sub}</p>`;
    copy.appendChild(n);
    return n;
  });
  // blocco unico per prefers-reduced-motion
  const staticPhase = el('div', 'mch__phase',
    '<span class="mch__eyebrow">MC — Milano</span><p class="mch__title">Precisione.</p><p class="mch__sub">Ogni dettaglio, preparato a mano. Noleggio e vendita a Milano.</p>');
  staticPhase.dataset.static = '';
  staticPhase.dataset.onlyStatic = '';
  copy.appendChild(staticPhase);

  const hint = el('div', 'mch__hint', 'Scorri');

  const paint = el('div', 'mch__paint');
  paint.setAttribute('role', 'group');
  paint.setAttribute('aria-label', 'Colore vernice');
  const paintLabel = el('span', 'mch__paint-label');
  const dots = el('div', 'mch__dots');
  Object.entries(PAINTS).forEach(([key, p]) => {
    const b = el('button', 'mch__dot');
    b.type = 'button';
    b.style.setProperty('--c', p.ui);
    b.dataset.paint = key;
    b.setAttribute('aria-label', p.label);
    b.setAttribute('aria-pressed', 'false');
    b.addEventListener('click', () => setPaint(key));
    dots.appendChild(b);
  });
  paint.append(paintLabel, dots);

  const loader = el('div', 'mch__loader', '<div class="mch__loader-track"><div class="mch__loader-bar"></div></div><div class="mch__loader-pct">0%</div>');
  const loaderBar = loader.querySelector('.mch__loader-bar');
  const loaderPct = loader.querySelector('.mch__loader-pct');

  const credits = el('div', 'mch__credits', opt.credits);

  stage.append(backdrop, canvas, vignette, fade, top, copy, hint, paint, loader, credits);
  container.appendChild(stage);

  /* --------------------------------------------------------------- state */
  const S = {
    progress: 0, target: 0, running: true, visible: true, ready: false, fallback: false,
    paint: PAINTS[opt.paint] ? opt.paint : 'rosso', destroyed: false,
  };
  let three = null; // riferimenti al motore 3D quando presente
  let raf = 0;

  function setLoad(f) {
    const pct = Math.round(clamp(f) * 100);
    loaderBar.style.transform = `scaleX(${clamp(f)})`;
    loaderPct.textContent = pct + '%';
  }

  function setPaint(key) {
    if (!PAINTS[key]) return;
    S.paint = key;
    paintLabel.textContent = PAINTS[key].label;
    dots.querySelectorAll('.mch__dot').forEach((d) => d.setAttribute('aria-pressed', String(d.dataset.paint === key)));
    if (three) three.setPaint(PAINTS[key]);
  }
  setPaint(S.paint);

  function readScroll() {
    const r = container.getBoundingClientRect();
    const span = r.height - window.innerHeight;
    S.target = span > 0 ? clamp(-r.top / span) : 0;
  }

  function updateCopy(p) {
    if (reduced) return;
    PHASES.forEach((ph, i) => {
      const [a, b] = ph.range;
      const inn = smooth(a, a + 0.06, p);
      const out = b > 1 ? 0 : smooth(b - 0.06, b, p);
      const o = inn * (1 - out);
      const y = (1 - inn) * 28 - out * 28;
      const n = phaseEls[i];
      n.style.opacity = o.toFixed(3);
      n.style.transform = `translate3d(0, ${y.toFixed(1)}px, 0)`;
      n.style.visibility = o < 0.002 ? 'hidden' : 'visible';
    });
    hint.style.opacity = String(1 - smooth(0.01, 0.05, p));
    fade.style.opacity = (smooth(0.94, 1.0, p) * 0.6).toFixed(3);
  }

  function goFallback(reason) {
    if (S.fallback) return;
    S.fallback = true;
    if (reason) console.warn('[MC hero] fallback:', reason);
    if (opt.fallbackImage) img.src = opt.fallbackImage;
    container.classList.add('is-fallback');
    container.classList.remove('is-ready');
    if (three) { try { three.dispose(); } catch (e) { /* ignore */ } three = null; }
  }

  /* ---------------------------------------------------------------- loop */
  let last = performance.now();
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (!S.visible || document.hidden) return;
    readScroll();
    // lerp morbido dello scroll (indipendente dal frame rate)
    S.progress += (S.target - S.progress) * (1 - Math.exp(-dt * 4.2));
    if (Math.abs(S.target - S.progress) < 1e-4) S.progress = S.target;
    if (window.__mchSnap) S.progress = S.target; // solo per test/screenshot
    updateCopy(S.progress);
    if (three && S.ready) three.render(reduced ? 0.42 : S.progress, dt, now / 1000);
  }

  const io = 'IntersectionObserver' in window
    ? new IntersectionObserver((e) => { S.visible = e[0].isIntersecting; }, { rootMargin: '100px' })
    : null;
  if (io) io.observe(container);

  updateCopy(0);
  raf = requestAnimationFrame(frame);

  /* ---------------------------------------------------------------- boot */
  if (!hasWebGL()) {
    goFallback('WebGL non disponibile');
  } else {
    const quality = opt.quality === 'auto' ? (detectLowTier() ? 'low' : 'high') : opt.quality;
    bootThree({ canvas, stage, opt, quality, reduced, setLoad, paint: PAINTS[S.paint] })
      .then((engine) => {
        if (S.destroyed) { engine.dispose(); return; }
        three = engine;
        S.ready = true;
        container.classList.add('is-ready');
        container.dataset.quality = engine.quality;
      })
      .catch((err) => goFallback(err && err.message ? err.message : String(err)));
  }

  return {
    setPaint,
    get progress() { return S.progress; },
    get state() { return { ready: S.ready, fallback: S.fallback, quality: three ? three.quality : null, paint: S.paint }; },
    destroy() {
      S.destroyed = true;
      cancelAnimationFrame(raf);
      if (io) io.disconnect();
      if (three) three.dispose();
      container.innerHTML = '';
      container.classList.remove('mch', 'is-ready', 'is-fallback', 'is-static');
    },
  };
}

/* ======================================================================
   Motore 3D
   ====================================================================== */
async function bootThree({ canvas, stage, opt, quality, reduced, setLoad, paint }) {
  const [THREE, { GLTFLoader }, { DRACOLoader }, { RectAreaLightUniformsLib }] = await Promise.all([
    import('three'),
    import('three/addons/loaders/GLTFLoader.js'),
    import('three/addons/loaders/DRACOLoader.js'),
    import('three/addons/lights/RectAreaLightUniformsLib.js'),
  ]);
  setLoad(0.04);

  const high = quality === 'high';
  let post = null;
  if (high) {
    try {
      const [{ EffectComposer }, { RenderPass }, { UnrealBloomPass }, { OutputPass }, { Reflector }] = await Promise.all([
        import('three/addons/postprocessing/EffectComposer.js'),
        import('three/addons/postprocessing/RenderPass.js'),
        import('three/addons/postprocessing/UnrealBloomPass.js'),
        import('three/addons/postprocessing/OutputPass.js'),
        import('three/addons/objects/Reflector.js'),
      ]);
      post = { EffectComposer, RenderPass, UnrealBloomPass, OutputPass, Reflector };
    } catch (e) { post = null; }
  }
  const BG = 0x0b0a09;

  /* renderer */
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: !high || !post, powerPreference: 'high-performance', alpha: false });
  let maxPR = high ? 2 : 1.5;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, maxPR));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.95;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(BG, 1);
  RectAreaLightUniformsLib.init();

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BG);
  scene.fog = new THREE.Fog(BG, 12, 30);

  const camera = new THREE.PerspectiveCamera(28, 1, 0.05, 80);

  /* ambiente HDR da studio: softbox emissive generate proceduralmente (niente download) */
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envScene = new THREE.Scene();
  envScene.background = new THREE.Color(0x060505);
  const panel = (w, h, v, pos, look, tint = 0xffffff) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(tint).multiplyScalar(v), side: THREE.DoubleSide }));
    m.position.set(...pos); m.lookAt(...look); envScene.add(m);
  };
  panel(1.6, 9, 9, [0, 4.5, 0], [0, 0, 0.001]);             // striscia a soffitto (lungo Z)
  panel(9, 0.5, 5, [-1.6, 4.4, 0], [0, 0, 0]);
  panel(9, 0.5, 5, [1.6, 4.4, 0], [0, 0, 0]);
  panel(6, 2.6, 3.2, [7, 2.2, -1], [0, 1, 0], 0xfff3e6);     // softbox laterale caldo
  panel(6, 2.6, 2.4, [-7, 2.2, 1], [0, 1, 0], 0xe9eeff);     // softbox laterale freddo
  panel(10, 0.35, 6, [0, 1.4, -8], [0, 1.4, 0]);             // linea di orizzonte
  panel(10, 0.35, 4, [0, 1.4, 8], [0, 1.4, 0]);
  const floorEnv = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.MeshBasicMaterial({ color: 0x0a0908 }));
  floorEnv.rotation.x = -Math.PI / 2; envScene.add(floorEnv);
  const envRT = pmrem.fromScene(envScene, 0.02);
  scene.environment = envRT.texture;
  envScene.traverse((o) => { if (o.isMesh) { o.geometry.dispose(); o.material.dispose(); } });
  setLoad(0.08);

  /* luci da set fotografico: RectAreaLight + pannelli visibili */
  const lights = [];
  const addSoftbox = (w, h, intensity, pos, look, color = 0xffffff) => {
    const l = new THREE.RectAreaLight(color, 0, w, h);
    l.position.set(...pos); l.lookAt(...look);
    scene.add(l);
    const vis = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: 0x000000, side: THREE.BackSide, fog: false }));
    vis.visible = false;
    l.add(vis);
    lights.push({ light: l, vis, base: intensity, color: new THREE.Color(color) });
  };

  /* pavimento lucido */
  const floorGroup = new THREE.Group();
  scene.add(floorGroup);
  const alphaTex = (() => {
    const c = document.createElement('canvas'); c.width = c.height = 256;
    const g = c.getContext('2d');
    const gr = g.createRadialGradient(128, 128, 10, 128, 128, 128);
    gr.addColorStop(0, '#b4b4b4'); gr.addColorStop(0.55, '#d6d6d6'); gr.addColorStop(1, '#ffffff');
    g.fillStyle = gr; g.fillRect(0, 0, 256, 256);
    const t = new THREE.CanvasTexture(c); return t;
  })();
  const gloss = new THREE.Mesh(
    new THREE.PlaneGeometry(36, 36),
    new THREE.MeshStandardMaterial({ color: BG, roughness: 0.22, metalness: 0.0, transparent: true, opacity: 1, alphaMap: alphaTex, depthWrite: false }),
  );
  gloss.rotation.x = -Math.PI / 2;
  gloss.position.y = 0.001;
  gloss.renderOrder = 1;
  floorGroup.add(gloss);

  let reflector = null;
  const hideInReflection = [];
  function addReflector() {
    if (!post) return;
    const w = Math.max(256, Math.round(stage.clientWidth * renderer.getPixelRatio() * 0.5));
    const h = Math.max(256, Math.round(stage.clientHeight * renderer.getPixelRatio() * 0.5));
    reflector = new post.Reflector(new THREE.PlaneGeometry(36, 36), { clipBias: 0.003, textureWidth: w, textureHeight: h, color: 0x8a8a8a });
    reflector.rotation.x = -Math.PI / 2;
    const orig = reflector.onBeforeRender;
    reflector.onBeforeRender = function (...args) {
      const prev = hideInReflection.map((o) => o.visible);
      hideInReflection.forEach((o) => { o.visible = false; });
      lights.forEach((L) => { L.vis.visible = true; }); // i softbox si vedono solo nel riflesso
      orig.apply(this, args);
      lights.forEach((L) => { L.vis.visible = false; });
      hideInReflection.forEach((o, i) => { o.visible = prev[i]; });
    };
    floorGroup.add(reflector);
    gloss.material.alphaMap = alphaTex;
    gloss.material.needsUpdate = true;
  }
  function removeReflector() {
    if (!reflector) return;
    floorGroup.remove(reflector);
    reflector.getRenderTarget().dispose();
    reflector.geometry.dispose(); reflector.material.dispose();
    reflector = null;
    gloss.material.alphaMap = null; gloss.material.transparent = false; gloss.material.roughness = 0.3;
    gloss.material.needsUpdate = true;
  }
  if (post) addReflector(); else { gloss.material.alphaMap = null; gloss.material.transparent = false; gloss.material.roughness = 0.3; }
  hideInReflection.push(gloss);

  /* materiali auto (come webgl_materials_car, rifiniti per lo studio) */
  const bodyMat = new THREE.MeshPhysicalMaterial({
    color: new THREE.Color(paint.hex), metalness: paint.metal, roughness: paint.rough,
    clearcoat: 1.0, clearcoatRoughness: 0.03, envMapIntensity: 1,
  });
  const detailsMat = new THREE.MeshStandardMaterial({ color: 0xd8d8d8, metalness: 1.0, roughness: 0.3 });
  const glassMat = high
    ? new THREE.MeshPhysicalMaterial({ color: 0xffffff, metalness: 0.25, roughness: 0, transmission: 1.0 })
    : new THREE.MeshPhysicalMaterial({ color: 0x0c0c0e, metalness: 0.2, roughness: 0.05, transparent: true, opacity: 0.55, clearcoat: 1 });
  const paintTarget = { color: new THREE.Color(paint.hex), metal: paint.metal, rough: paint.rough };

  /* caricamento modello con percentuale reale */
  const draco = new DRACOLoader();
  draco.setDecoderPath(opt.dracoPath);
  const loader = new GLTFLoader();
  loader.setDRACOLoader(draco);
  const gltf = await new Promise((resolve, reject) => {
    loader.load(opt.modelUrl, resolve, (e) => {
      if (e && e.lengthComputable && e.total) setLoad(0.08 + 0.84 * (e.loaded / e.total));
    }, (err) => reject(new Error('Modello non caricato: ' + (err && err.message ? err.message : err))));
  });
  setLoad(0.93);

  const model = gltf.scene;
  const byName = (n) => model.getObjectByName(n);
  const setMat = (n, m) => { const o = byName(n); if (o) o.material = m; };
  setMat('body', bodyMat);
  ['rim_fl', 'rim_fr', 'rim_rr', 'rim_rl', 'trim'].forEach((n) => setMat(n, detailsMat));
  setMat('glass', glassMat);

  // normalizza: asse lungo su Z, centrata, appoggiata al pavimento
  const pivot = new THREE.Group();
  const align = new THREE.Group();
  align.add(model);
  pivot.add(align);
  scene.add(pivot);
  let box = new THREE.Box3().setFromObject(model);
  let size = box.getSize(new THREE.Vector3());
  if (size.x > size.z * 1.15) { align.rotation.y = Math.PI / 2; align.updateMatrixWorld(true); }
  box = new THREE.Box3().setFromObject(align);
  size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  align.position.set(-center.x, -box.min.y, -center.z);
  align.updateMatrixWorld(true);
  const carLen = size.z || 4.5;

  model.traverse((o) => {
    if (o.isMesh) {
      o.castShadow = false; o.receiveShadow = false;
      const ms = Array.isArray(o.material) ? o.material : [o.material];
      ms.forEach((m) => { if (m && 'envMapIntensity' in m) m.userData.mchEnv = m.envMapIntensity ?? 1; });
    }
  });

  // ombra AO (come nell'esempio ufficiale)
  let aoMesh = null;
  try {
    const tex = await new THREE.TextureLoader().loadAsync(opt.aoUrl);
    aoMesh = new THREE.Mesh(
      new THREE.PlaneGeometry(0.655 * 4, 1.3 * 4),
      new THREE.MeshBasicMaterial({ map: tex, blending: THREE.MultiplyBlending, toneMapped: false, transparent: true, premultipliedAlpha: true }),
    );
    aoMesh.rotation.x = -Math.PI / 2;
    aoMesh.position.y = 0.003;
    aoMesh.renderOrder = 2;
    model.add(aoMesh);
    hideInReflection.push(aoMesh);
  } catch (e) { /* l'AO è un di più */ }
  if (!aoMesh) { // ombra morbida di riserva
    const c = document.createElement('canvas'); c.width = c.height = 128;
    const g = c.getContext('2d'); const gr = g.createRadialGradient(64, 64, 4, 64, 64, 64);
    gr.addColorStop(0, 'rgba(0,0,0,0.85)'); gr.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = gr; g.fillRect(0, 0, 128, 128);
    aoMesh = new THREE.Mesh(new THREE.PlaneGeometry(size.x * 1.5, size.z * 1.25), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false, toneMapped: false }));
    aoMesh.rotation.x = -Math.PI / 2; aoMesh.position.y = 0.003; aoMesh.renderOrder = 2;
    pivot.add(aoMesh); hideInReflection.push(aoMesh);
  }

  // orientamento: lato anteriore = segno Z delle ruote anteriori
  const tmp = new THREE.Vector3();
  const fl = byName('rim_fl') || byName('wheel_fl');
  const fr = byName('rim_fr') || byName('wheel_fr');
  let front = 1;
  if (fl) { fl.getWorldPosition(tmp); front = tmp.z >= 0 ? 1 : -1; }
  const FINAL_YAW = -0.32 * front;
  let rimPos = new THREE.Vector3(size.x * 0.42, size.y * 0.3, front * carLen * 0.31);
  for (const w of [fl, fr]) {
    if (!w) continue;
    w.getWorldPosition(tmp);
    const p = tmp.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), FINAL_YAW);
    if (p.x > 0) { rimPos = p; break; }
  }

  /* softbox (accese una a una) */
  const L = carLen;
  addSoftbox(1.3, L * 1.15, 7, [0, 4.0, 0], [0, 0, 0.001]);                        // 1 soffitto
  addSoftbox(3.2, 1.8, 9, [5.2, 1.9, -front * 1.2], [0, 0.6, 0], 0xfff1e2);     // 2 key laterale
  addSoftbox(4.0, 1.0, 8, [-5.0, 3.4, -front * 3.6], [0, 0.6, 0], 0xe8eeff);    // 3 rim posteriore
  addSoftbox(2.4, 1.4, 5, [3.4, 2.8, front * 6.2], [0, 0.5, 0]);                // 4 fill frontale

  /* post-processing */
  let composer = null; let bloom = null; let bokeh = null;
  function buildComposer() {
    if (!post) return;
    const rt = new THREE.WebGLRenderTarget(2, 2, { type: THREE.HalfFloatType, samples: 4 });
    composer = new post.EffectComposer(renderer, rt);
    composer.addPass(new post.RenderPass(scene, camera));
    bloom = new post.UnrealBloomPass(new THREE.Vector2(256, 256), 0.32, 0.55, 0.86);
    composer.addPass(bloom);
    composer.addPass(new post.OutputPass());
  }
  buildComposer();
  // profondità di campo leggera (solo desktop, opzionale: se il modulo manca si continua senza)
  if (composer) {
    try {
      const { BokehPass } = await import('three/addons/postprocessing/BokehPass.js');
      bokeh = new BokehPass(scene, camera, { focus: 7, aperture: 0.0012, maxblur: 0.0045 });
      const origRender = bokeh.render.bind(bokeh);
      const noop = () => {};
      bokeh.render = (...a) => { // il riflesso non va ri-renderizzato nel passaggio di profondità
        const r = reflector; const ob = r ? r.onBeforeRender : null;
        if (r) r.onBeforeRender = noop;
        try { origRender(...a); } finally { if (r) r.onBeforeRender = ob; }
      };
      composer.insertPass(bokeh, 1);
    } catch (e) { bokeh = null; }
  }

  /* regia della camera: keyframe calcolati sulle misure reali dell'auto */
  const V = (x, y, z) => new THREE.Vector3(x, y, z);
  let keys = [];
  let posCurve = null; let tgtCurve = null;
  function buildKeys(aspect) {
    const s = clamp(Math.pow(1.55 / aspect, 0.9), 1, 2.7);
    const sc = Math.pow(s, 0.55);
    const k = (t, pos, tgt, fov, scale = s) => ({ t, pos: tgt.clone().add(pos.clone().sub(tgt).multiplyScalar(scale)), tgt, fov });
    const rp = rimPos;
    keys = [
      k(0.00, V(9.6, 0.78, front * 0.15), V(0, 0.55, 0), 27),
      k(0.22, V(8.8, 0.92, front * 0.7), V(0, 0.55, front * 0.05), 27),
      k(0.46, V(5.4, 1.35, front * 5.6), V(0, 0.48, front * 0.35), 31),
      k(0.58, V(rp.x + 2.1, 0.80, rp.z + front * 1.9), V(rp.x, 0.42, rp.z + front * 0.4), 29, sc),
      k(0.71, V(rp.x + 1.05, rp.y + 0.14, rp.z + front * 0.6), V(rp.x - 0.05, rp.y, rp.z), 26, sc),
      k(0.86, V(0.6, 12.5, front * 0.2), V(-1.2, 0.2, 0), 30),
      k(1.00, V(0.0, 17, front * 0.1), V(-1.4, 0, 0), 30),
    ];
    posCurve = new THREE.CatmullRomCurve3(keys.map((q) => q.pos), false, 'centripetal');
    tgtCurve = new THREE.CatmullRomCurve3(keys.map((q) => q.tgt), false, 'centripetal');
  }
  function sampleKeys(p, outPos, outTgt) {
    let i = 0;
    while (i < keys.length - 2 && p > keys[i + 1].t) i++;
    const a = keys[i], b = keys[i + 1];
    const lt = easeInOut(clamp((p - a.t) / (b.t - a.t)));
    const u = (i + lt) / (keys.length - 1);
    posCurve.getPoint(u, outPos);
    tgtCurve.getPoint(u, outTgt);
    return a.fov + (b.fov - a.fov) * lt;
  }

  /* resize */
  let W = 0, H = 0;
  function resize() {
    const w = stage.clientWidth || window.innerWidth;
    const h = stage.clientHeight || window.innerHeight;
    if (w === W && h === H) return;
    W = w; H = h;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    if (composer) composer.setSize(w, h);
    if (reflector) {
      reflector.getRenderTarget().setSize(Math.round(w * renderer.getPixelRatio() * 0.5), Math.round(h * renderer.getPixelRatio() * 0.5));
    }
    buildKeys(w / h);
  }
  resize();
  const ro = 'ResizeObserver' in window ? new ResizeObserver(resize) : null;
  if (ro) ro.observe(stage); else window.addEventListener('resize', resize);

  /* governatore prestazioni: se gli fps calano, riduce la qualità */
  let tier = post ? 3 : 1; // 3 = bloom+riflesso+dof, 2 = senza dof/bloom, 1 = semplice
  let acc = 0, n = 0, settle = 0;
  function governor(dt) {
    if (settle < 1.0) { settle += dt; return; }
    acc += dt; n++;
    if (n < 90) return;
    const avg = acc / n; acc = 0; n = 0;
    if (avg > 0.024 && tier === 3) {
      tier = 2;
      if (bokeh) { composer.removePass(bokeh); bokeh = null; }
      if (bloom) { bloom.enabled = false; }
    } else if (avg > 0.024 && tier === 2) {
      tier = 1;
      removeReflector();
      if (composer) { composer.dispose(); composer = null; }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1));
      W = 0; resize();
    }
  }

  /* render */
  const camPos = new THREE.Vector3(); const camTgt = new THREE.Vector3();
  let introT = 0;
  const envMats = [];
  scene.traverse((o) => {
    if (o.isMesh && o.material && 'envMapIntensity' in o.material && o !== gloss) envMats.push(o.material);
  });
  envMats.push(bodyMat, detailsMat, glassMat);

  function render(p, dt, time) {
    introT = Math.min(1, introT + dt / 1.8);
    if (!reduced && opt.adaptive) governor(dt);

    // 1) luci: si accendono una a una sul profilo
    const on = reduced ? [1, 1, 1, 1] : [
      Math.max(0.55 * smooth(0, 1, introT), smooth(0.0, 0.06, p)),
      smooth(0.05, 0.11, p),
      smooth(0.10, 0.16, p),
      smooth(0.15, 0.22, p),
    ];
    lights.forEach((L, i) => {
      L.light.intensity = L.base * on[i];
      L.vis.material.color.copy(L.color).multiplyScalar(on[i] * (i === 0 ? 1.6 : 0.9));
    });
    const envK = reduced ? 1 : 0.12 + 0.88 * smooth(0.03, 0.24, p) * Math.max(on[0], 0.3);
    for (const m of envMats) m.envMapIntensity = (m.userData.mchEnv ?? 1) * envK;
    gloss.material.envMapIntensity = envK * (reflector ? 0.35 : 0.22);
    if (reflector) reflector.material.uniforms.color.value.setScalar(0.12 + 0.3 * envK);

    // 2) auto che ruota lentamente (i riflessi scorrono sulla carrozzeria)
    const idle = reduced ? 0 : Math.sin(time * 0.25) * 0.012;
    pivot.rotation.y = FINAL_YAW * smooth(0.18, 0.52, p) * (1 - smooth(0.73, 0.86, p)) + idle;

    // vernice: transizione morbida
    const k = 1 - Math.exp(-dt * 3.5);
    bodyMat.color.lerp(paintTarget.color, k);
    bodyMat.metalness += (paintTarget.metal - bodyMat.metalness) * k;
    bodyMat.roughness += (paintTarget.rough - bodyMat.roughness) * k;

    // camera
    const fov = sampleKeys(p, camPos, camTgt);
    if (!reduced) camPos.y += Math.sin(time * 0.4) * 0.01;
    camera.position.copy(camPos);
    camera.lookAt(camTgt);
    if (Math.abs(camera.fov - fov) > 1e-3) { camera.fov = fov; camera.updateProjectionMatrix(); }

    if (bokeh) bokeh.uniforms.focus.value = camPos.distanceTo(camTgt);

    if (composer) {
      try { composer.render(dt); }
      catch (e) { composer = null; removeReflector(); renderer.render(scene, camera); }
    } else {
      renderer.render(scene, camera);
    }
  }

  setLoad(1);
  // primo frame + compilazione shader prima di mostrare
  render(0, 0.016, 0);

  return {
    quality: post ? 'high' : 'low',
    render,
    setPaint(pp) { paintTarget.color.set(pp.hex); paintTarget.metal = pp.metal; paintTarget.rough = pp.rough; },
    dispose() {
      if (ro) ro.disconnect(); else window.removeEventListener('resize', resize);
      draco.dispose();
      removeReflector();
      if (composer) composer.dispose();
      envRT.dispose(); pmrem.dispose();
      scene.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        const ms = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
        ms.forEach((m) => { Object.values(m).forEach((v) => { if (v && v.isTexture) v.dispose(); }); m.dispose(); });
      });
      renderer.dispose();
    },
  };
}
