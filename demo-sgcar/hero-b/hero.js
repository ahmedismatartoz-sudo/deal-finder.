/**
 * MC — Hero 3D "Notte in città" (variante B)
 *
 * export initHero(container, options?) -> { destroy() }
 *
 * Requisiti nella pagina ospite: un importmap che mappa
 *   "three"          -> https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js
 *   "three/addons/"  -> https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/
 * e il foglio hero.css. Nessuna altra libreria.
 *
 * Il modulo non ha import statici: se three.js o il modello non arrivano,
 * la sezione resta in piedi col fallback CSS e i testi guidati dallo scroll.
 */

const THREE_VER = '0.160.0';
const DRACO_PATH = `https://cdn.jsdelivr.net/npm/three@${THREE_VER}/examples/jsm/libs/draco/gltf/`;
const MODEL_URLS = [
  // Ferrari 458 Italia — dagli esempi ufficiali di three.js (r160), vedi CREDITS.txt
  'https://cdn.jsdelivr.net/gh/mrdoob/three.js@r160/examples/models/gltf/ferrari.glb',
  'https://threejs.org/examples/models/gltf/ferrari.glb',
];
const MODEL_SIZE_ESTIMATE = 2.6e6; // usato solo se il server non manda Content-Length

const TEXTS = [
  { html: 'Milano, di notte.', from: 0.03, to: 0.27, cls: '' },
  { html: 'Pronta quando lo sei tu.', from: 0.36, to: 0.64, cls: '' },
  {
    html: 'Noleggio e vendita di auto speciali.<span class="mc-hero__meta">Milano · su appuntamento</span><a class="mc-hero__cta" href="#collezione">Scopri la collezione</a>',
    from: 0.8, to: 1.5, cls: 'mc-hero__line--end',
  },
];

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const windowFade = (p, from, to, f = 0.045) => smooth(from, from + f, p) * (1 - smooth(to - f, to, p));

function hasWebGL() {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch (e) { return false; }
}

export function initHero(container, options = {}) {
  const reduced = options.reducedMotion ?? matchMedia('(prefers-reduced-motion: reduce)').matches;
  const mobile = options.mobile ?? (matchMedia('(max-width: 820px), (pointer: coarse)').matches || (navigator.hardwareConcurrency || 8) <= 4);
  const log = options.onLog || (() => {});

  /* ---------- DOM ---------- */
  container.classList.add('mc-hero');
  container.innerHTML = `
    <div class="mc-hero__sticky">
      <div class="mc-hero__fallback" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
      <canvas class="mc-hero__canvas" aria-hidden="true"></canvas>
      <div class="mc-hero__ui">
        <div class="mc-hero__brand"><span class="mc-hero__dot"></span>MC<small>Milano</small></div>
        <nav class="mc-hero__nav"><span>Noleggio</span><span>Vendita</span><span>Contatti</span></nav>
        ${TEXTS.map((t) => `<h2 class="mc-hero__line ${t.cls}">${t.html}</h2>`).join('')}
        <div class="mc-hero__scroll">Scorri<i></i></div>
      </div>
      <div class="mc-hero__loader" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
        <div class="mc-hero__loader-bar"><span></span></div>
        <div class="mc-hero__loader-pct">0%</div>
      </div>
    </div>`;
  const $ = (s) => container.querySelector(s);
  const canvas = $('.mc-hero__canvas');
  const lines = [...container.querySelectorAll('.mc-hero__line')];
  const scrollHint = $('.mc-hero__scroll');
  const loaderEl = $('.mc-hero__loader');
  const loaderBar = $('.mc-hero__loader-bar span');
  const loaderPct = $('.mc-hero__loader-pct');

  let destroyed = false;
  let target = 0; // progresso scroll grezzo 0..1
  let prog = 0; // progresso interpolato
  let three = null; // stato 3D, se attivo
  const shownPct = { v: 0 };

  function setProgress(f) {
    const v = Math.round(clamp(f) * 100);
    if (v < shownPct.v) return;
    shownPct.v = v;
    loaderBar.style.width = v + '%';
    loaderPct.textContent = v + '%';
    loaderEl.setAttribute('aria-valuenow', String(v));
  }

  function readScroll() {
    const r = container.getBoundingClientRect();
    const total = container.offsetHeight - window.innerHeight;
    target = total > 0 ? clamp(-r.top / total) : 0;
  }

  function updateText(p) {
    TEXTS.forEach((t, i) => {
      const o = windowFade(p, t.from, t.to);
      const el = lines[i];
      el.style.opacity = o.toFixed(3);
      if (!reduced) el.style.transform = `translate3d(0, ${((1 - o) * 24).toFixed(1)}px, 0)`;
      el.style.pointerEvents = o > 0.5 ? '' : 'none';
    });
    scrollHint.style.opacity = String(1 - smooth(0.0, 0.04, p));
  }

  function fallback(reason) {
    if (destroyed) return;
    log('fallback: ' + reason);
    container.classList.add('is-fallback');
    container.dataset.state = 'fallback';
    if (three) { try { three.dispose(); } catch (e) { /* */ } three = null; }
  }

  /* ---------- loop (testi sempre, 3D se pronto) ---------- */
  let raf = 0;
  let last = performance.now();
  let visible = true;
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (!visible) return;
    prog += (target - prog) * (reduced ? 1 : 1 - Math.exp(-dt * 5));
    if (Math.abs(target - prog) < 1e-5) prog = target;
    updateText(prog);
    if (three) three.render(dt, prog);
  }
  const onScroll = () => readScroll();
  const onResize = () => { readScroll(); if (three) three.resize(); };
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onResize);
  const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; }, { rootMargin: '100px' });
  io.observe(container);
  readScroll();
  prog = target;
  raf = requestAnimationFrame(frame);

  /* ---------- avvio 3D ---------- */
  if (!hasWebGL()) {
    fallback('no-webgl');
  } else {
    setProgress(0.02);
    start().catch((err) => { console.warn('[MC hero]', err); fallback(err && err.message ? err.message : 'error'); });
  }

  async function start() {
    const timeout = setTimeout(() => { if (!container.classList.contains('is-ready')) fallback('timeout'); }, options.timeout || 40000);
    const [THREE, { EffectComposer }, { RenderPass }, { UnrealBloomPass }, { OutputPass }, { Reflector }] = await Promise.all([
      import('three'),
      import('three/addons/postprocessing/EffectComposer.js'),
      import('three/addons/postprocessing/RenderPass.js'),
      import('three/addons/postprocessing/UnrealBloomPass.js'),
      import('three/addons/postprocessing/OutputPass.js'),
      import('three/addons/objects/Reflector.js'),
    ]);
    if (destroyed) return;
    setProgress(0.08);
    const scene3d = buildScene(THREE, { EffectComposer, RenderPass, UnrealBloomPass, OutputPass, Reflector });
    three = scene3d;
    if (!options.noCar) {
      const [{ GLTFLoader }, { DRACOLoader }] = await Promise.all([
        import('three/addons/loaders/GLTFLoader.js'),
        import('three/addons/loaders/DRACOLoader.js'),
      ]);
      setProgress(0.12);
      const gltf = await loadModel(GLTFLoader, DRACOLoader, options.modelUrls || MODEL_URLS, (f) => setProgress(0.12 + f * 0.83));
      if (destroyed) return;
      scene3d.setCar(gltf);
    }
    clearTimeout(timeout);
    if (destroyed || container.classList.contains('is-fallback')) return;
    scene3d.compile();
    setProgress(1);
    container.classList.add('is-ready');
    container.dataset.state = options.noCar ? 'ready-nocar' : 'ready';
  }

  function loadModel(GLTFLoader, DRACOLoader, urls, onProg) {
    const draco = new DRACOLoader();
    draco.setDecoderPath(DRACO_PATH);
    const loader = new GLTFLoader();
    loader.setDRACOLoader(draco);
    return urls.reduce((chain, url) => chain.catch(() => new Promise((res, rej) => {
      log('model: ' + url);
      loader.load(url, res, (e) => onProg(e.total ? e.loaded / e.total : Math.min(0.95, e.loaded / MODEL_SIZE_ESTIMATE)), rej);
    })), Promise.reject(new Error('start'))).catch((e) => { throw new Error('model-failed: ' + (e && e.message)); });
  }

  /* =====================================================================
     SCENA
     ===================================================================== */
  function buildScene(THREE, P) {
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: !mobile, powerPreference: 'high-performance', alpha: false });
    const maxPR = mobile ? 1.5 : 2;
    let pixelRatio = Math.min(window.devicePixelRatio || 1, maxPR);
    renderer.setPixelRatio(pixelRatio);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    const BG = new THREE.Color(0x030409);
    const scene = new THREE.Scene();
    scene.background = BG;
    const FOG = 0.034;
    scene.fog = new THREE.FogExp2(BG, FOG);

    const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 400);

    /* --- environment notturno (niente HDR esterni): strisce di luce urbana --- */
    const pmrem = new THREE.PMREMGenerator(renderer);
    const env = new THREE.Scene();
    env.background = new THREE.Color(0x000000);
    const emi = (r, g, b) => new THREE.MeshBasicMaterial({ color: new THREE.Color(r, g, b), side: THREE.DoubleSide });
    const addBox = (m, sx, sy, sz, x, y, z, ry = 0) => {
      const b = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), m); b.position.set(x, y, z); b.rotation.y = ry; env.add(b);
    };
    addBox(emi(3.2, 2.5, 1.7), 0.25, 0.08, 60, 3.5, 7, 0); // lampioni sx
    addBox(emi(3.2, 2.5, 1.7), 0.25, 0.08, 60, -3.5, 7, 0); // lampioni dx
    addBox(emi(0.5, 0.62, 0.95), 30, 3, 0.2, 0, 2.5, -14); // vetrine fredde
    addBox(emi(0.9, 0.55, 0.3), 0.2, 2, 22, 12, 2, 0); // insegne calde
    addBox(emi(0.25, 0.3, 0.5), 0.2, 4, 22, -12, 3, 0);
    addBox(emi(1.6, 1.6, 1.7), 6, 0.05, 1.2, 0, 9, 4); // softbox alto
    addBox(new THREE.MeshBasicMaterial({ color: 0x050608, side: THREE.DoubleSide }), 80, 0.1, 80, 0, -0.6, 0);
    const envTex = pmrem.fromScene(env, 0.035).texture;
    scene.environment = envTex;
    env.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });

    scene.add(new THREE.HemisphereLight(0x223048, 0x050505, 0.35));
    const moon = new THREE.DirectionalLight(0x9fb4ff, 0.5);
    moon.position.set(-4, 8, -6);
    scene.add(moon);

    /* --- asfalto bagnato riflettente --- */
    const reflRes = mobile ? 512 : 1024;
    const road = new P.Reflector(new THREE.PlaneGeometry(40, 220), {
      textureWidth: reflRes, textureHeight: reflRes, clipBias: 0.003, multisample: mobile ? 0 : 4,
      shader: roadShader(THREE),
    });
    road.rotation.x = -Math.PI / 2;
    road.position.set(0, 0, 40);
    scene.add(road);
    const RU = road.material.uniforms;
    RU.uFogColor.value.copy(BG);
    RU.uFogDensity.value = FOG;

    /* --- strisce di luce della città --- */
    const streakCount = mobile ? 140 : 320;
    const streakGeo = new THREE.BoxGeometry(1, 1, 1);
    const streakMat = new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false });
    const streaks = new THREE.InstancedMesh(streakGeo, streakMat, streakCount);
    streaks.frustumCulled = false;
    const sData = [];
    const palette = [
      [3.4, 2.6, 1.6], [3.0, 3.0, 3.2], [1.4, 1.8, 3.6], [3.6, 1.6, 0.6], [2.6, 2.2, 1.8], [3.6, 0.5, 0.35],
    ];
    const rnd = mulberry(7);
    const colTmp = new THREE.Color();
    for (let i = 0; i < streakCount; i++) {
      const side = rnd() < 0.5 ? -1 : 1;
      const kind = rnd();
      let x, y, th, c;
      if (kind < 0.22) { // lampioni alti, regolari
        x = side * (7.5 + rnd() * 0.6); y = 5.6 + rnd() * 0.3; th = 0.07; c = palette[0];
      } else if (kind < 0.36) { // luci basse vicine: parallasse forte
        x = side * (4.6 + rnd() * 1.4); y = 0.25 + rnd() * 0.9; th = 0.035; c = palette[side < 0 ? 1 : 5];
      } else { // facciate, vetrine, insegne
        x = side * (10 + rnd() * 16); y = 0.6 + rnd() * 9; th = 0.04 + rnd() * 0.08; c = palette[Math.floor(rnd() * 5)];
      }
      const k = 0.5 + rnd() * 0.8;
      sData.push({ x, y, z: -90 + rnd() * 180, th, len: 0.6 + rnd() * 1.4 });
      streaks.setColorAt(i, colTmp.setRGB(c[0] * k, c[1] * k, c[2] * k));
    }
    streaks.instanceColor.needsUpdate = true;
    scene.add(streaks);
    const mtx = new THREE.Matrix4();
    const qId = new THREE.Quaternion();
    const vPos = new THREE.Vector3();
    const vScl = new THREE.Vector3();

    /* --- pioggia sottile --- */
    const rainCount = reduced ? 0 : (mobile ? 1400 : 5200);
    let rain = null;
    if (rainCount) {
      const pos = new Float32Array(rainCount * 2 * 3);
      const end = new Float32Array(rainCount * 2);
      for (let i = 0; i < rainCount; i++) {
        const x = rnd(), y = rnd(), z = rnd();
        for (let j = 0; j < 2; j++) {
          pos.set([x, y, z], (i * 2 + j) * 3);
          end[i * 2 + j] = j;
        }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      g.setAttribute('aEnd', new THREE.BufferAttribute(end, 1));
      rain = new THREE.LineSegments(g, new THREE.ShaderMaterial({
        uniforms: { uTime: { value: 0 }, uOffset: { value: 0 }, uVel: { value: 0 }, uCenter: { value: new THREE.Vector3() }, uAlpha: { value: 0 } },
        vertexShader: /* glsl */`
          attribute float aEnd; uniform float uTime, uOffset, uVel, uAlpha; uniform vec3 uCenter; varying float vA;
          void main(){
            vec3 p = position;
            float fall = 11.0;
            vec3 w;
            w.y = fract(p.y - uTime * fall / 9.0) * 9.0;
            w.x = (p.x - 0.5) * 22.0 + uCenter.x;
            w.z = (fract(p.z - uOffset / 28.0) - 0.5) * 28.0 + uCenter.z;
            vec3 dir = normalize(vec3(0.02, -fall, -uVel));
            w -= dir * aEnd * (0.22 + uVel * 0.006);
            vA = mix(1.0, 0.0, aEnd) * uAlpha;
            vec4 mv = viewMatrix * vec4(w, 1.0);
            vA *= smoothstep(0.6, 3.0, -mv.z) * (1.0 - smoothstep(10.0, 22.0, -mv.z));
            gl_Position = projectionMatrix * mv;
          }`,
        fragmentShader: /* glsl */`varying float vA; void main(){ gl_FragColor = vec4(vec3(0.62, 0.68, 0.82) * vA, 1.0); }`,
        transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      }));
      rain.frustumCulled = false;
      scene.add(rain);
    }

    /* --- fari / fanali (glow + coni) --- */
    const glowTex = makeGlowTexture(THREE);
    const mkSprite = (c) => {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: c, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false, transparent: true }));
      return s;
    };
    const lights = new THREE.Group();
    scene.add(lights);
    const head = [mkSprite(0xffffff), mkSprite(0xffffff)];
    const headFlare = [mkSprite(0xbfd4ff), mkSprite(0xbfd4ff)];
    const tail = [mkSprite(0xff1a0a), mkSprite(0xff1a0a)];
    const tailCore = [mkSprite(0xff5040), mkSprite(0xff5040)];
    [...head, ...headFlare, ...tail, ...tailCore].forEach((s) => lights.add(s));
    const beamMat = new THREE.ShaderMaterial({
      uniforms: { uI: { value: 0 } },
      vertexShader: /* glsl */`varying float vL; varying vec3 vN; varying vec3 vV;
        void main(){ vL = clamp(length(position) / 11.0, 0.0, 1.0); vec4 mv = modelViewMatrix * vec4(position,1.0);
          vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix * mv; }`,
      fragmentShader: /* glsl */`uniform float uI; varying float vL; varying vec3 vN; varying vec3 vV;
        void main(){ float edge = pow(abs(dot(vN, vV)), 2.0); float a = pow(1.0 - vL, 2.2) * smoothstep(0.0, 0.06, vL) * edge * uI;
          gl_FragColor = vec4(vec3(1.0, 0.96, 0.9) * a, 1.0); }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    });
    const beamGeo = new THREE.ConeGeometry(1.5, 11, 32, 1, true);
    beamGeo.translate(0, -5.5, 0);
    beamGeo.rotateX(-Math.PI / 2);
    beamGeo.rotateX(0.05); // leggermente verso l'asfalto
    const beams = [new THREE.Mesh(beamGeo, beamMat), new THREE.Mesh(beamGeo, beamMat)];
    beams.forEach((b) => { b.renderOrder = 5; lights.add(b); });

    // posizioni di default (sostituite dai bound del modello)
    const L = { hx: 0.66, hy: 0.62, hz: 2.2, tx: 0.58, ty: 0.8, tz: -2.3, halfW: 0.98, front: 2.32, rear: -2.32 };
    function placeLights() {
      head.forEach((s, i) => s.position.set((i ? 1 : -1) * L.hx, L.hy, L.hz));
      headFlare.forEach((s, i) => s.position.set((i ? 1 : -1) * L.hx, L.hy, L.hz + 0.02));
      beams.forEach((b, i) => b.position.set((i ? 1 : -1) * L.hx, L.hy, L.hz));
      tail.forEach((s, i) => s.position.set((i ? 1 : -1) * L.tx, L.ty, L.tz));
      tailCore.forEach((s, i) => s.position.set((i ? 1 : -1) * L.tx, L.ty, L.tz - 0.01));
      RU.uCar.value.set(L.halfW, L.front, L.rear, L.hx);
    }
    placeLights();

    /* --- auto --- */
    let wheels = [];
    let carGroup = null;
    function setCar(gltf) {
      const car = gltf.scene.children[0] || gltf.scene;
      const bodyMat = new THREE.MeshPhysicalMaterial({
        color: options.carColor ?? 0x2b2f36, metalness: 1.0, roughness: 0.42, clearcoat: 1.0, clearcoatRoughness: 0.025, envMapIntensity: 1.25,
      });
      const detailsMat = new THREE.MeshStandardMaterial({ color: 0xb8bcc4, metalness: 1.0, roughness: 0.28, envMapIntensity: 1.2 });
      const glassMat = mobile
        ? new THREE.MeshPhysicalMaterial({ color: 0x0a0c10, metalness: 0.4, roughness: 0.02, transparent: true, opacity: 0.78, envMapIntensity: 1.4 })
        : new THREE.MeshPhysicalMaterial({ color: 0xffffff, metalness: 0.25, roughness: 0, transmission: 1.0 });
      const set = (n, m) => { const o = car.getObjectByName(n); if (o) o.material = m; };
      set('body', bodyMat);
      ['rim_fl', 'rim_fr', 'rim_rr', 'rim_rl', 'trim'].forEach((n) => set(n, detailsMat));
      set('glass', glassMat);
      wheels = ['wheel_fl', 'wheel_fr', 'wheel_rl', 'wheel_rr'].map((n) => car.getObjectByName(n)).filter(Boolean);

      carGroup = new THREE.Group();
      carGroup.add(car);
      scene.add(carGroup);
      // bound -> posizione luci
      const box = new THREE.Box3().setFromObject(car);
      const size = box.getSize(new THREE.Vector3());
      if (size.z > 1 && isFinite(size.z)) {
        const h = size.y;
        L.halfW = size.x / 2;
        L.front = box.max.z; L.rear = box.min.z;
        L.hx = L.halfW * 0.66; L.hy = box.min.y + h * 0.5; L.hz = box.max.z - 0.3;
        L.tx = L.halfW * 0.6; L.ty = box.min.y + h * 0.63; L.tz = box.min.z + 0.06;
        placeLights();
      }
      log('car bbox ' + size.toArray().map((v) => v.toFixed(2)).join('x'));
    }

    /* --- post-processing --- */
    const composer = new P.EffectComposer(renderer);
    composer.addPass(new P.RenderPass(scene, camera));
    const bloom = new P.UnrealBloomPass(new THREE.Vector2(256, 256), mobile ? 0.55 : 0.85, mobile ? 0.35 : 0.55, 0.82);
    composer.addPass(bloom);
    composer.addPass(new P.OutputPass());

    /* --- camera: keyframe su spline --- */
    const K = [
      // p,    posizione,              target
      [0.00, [3.4, 0.78, 5.6], [0, 0.55, 0.6]], // 1 buio, tre quarti anteriore
      [0.20, [5.4, 0.42, 1.2], [0, 0.5, 0.3]], // 2 laterale bassa
      [0.44, [5.3, 0.46, -1.4], [0, 0.55, 0.5]],
      [0.62, [0.8, 0.48, 6.6], [0, 0.55, 0.4]], // 3 davanti al muso
      [0.79, [-5.2, 0.95, 0.4], [0, 0.6, 0]],
      [1.00, [1.7, 1.08, -7.0], [0.95, 0.66, -0.6]], // 4 posteriore
    ];
    const posCurve = new THREE.CatmullRomCurve3(K.map((k) => new THREE.Vector3(...k[1])), false, 'centripetal');
    const tgtCurve = new THREE.CatmullRomCurve3(K.map((k) => new THREE.Vector3(...k[2])), false, 'centripetal');
    const toU = (p) => {
      for (let i = 0; i < K.length - 1; i++) {
        if (p <= K[i + 1][0]) {
          const t = (p - K[i][0]) / (K[i + 1][0] - K[i][0]);
          return (i + t * t * (3 - 2 * t)) / (K.length - 1);
        }
      }
      return 1;
    };
    const cPos = new THREE.Vector3();
    const cTgt = new THREE.Vector3();
    let aspect = 1;

    function resize() {
      const w = canvas.clientWidth || container.clientWidth;
      const h = canvas.clientHeight || window.innerHeight;
      aspect = w / h;
      renderer.setPixelRatio(pixelRatio);
      renderer.setSize(w, h, false);
      composer.setPixelRatio(pixelRatio);
      composer.setSize(w, h);
      if (mobile) bloom.setSize(Math.round(w * pixelRatio * 0.5), Math.round(h * pixelRatio * 0.5));
      camera.aspect = aspect;
      camera.updateProjectionMatrix();
    }
    resize();

    /* --- stato animazione --- */
    let time = 0;
    let travel = 0; // metri percorsi (scorrimento mondo)
    let speedS = 0;
    let frames = 0, acc = 0, tuned = false;

    function render(dt, p) {
      // prestazioni: se il primo secondo e mezzo è lento, abbassa la risoluzione
      if (!tuned && dt > 0) {
        frames++; acc += dt;
        if (frames === 90) {
          tuned = true;
          const avg = acc / frames;
          if (avg > 0.024 && pixelRatio > 1) { pixelRatio = Math.max(1, pixelRatio * 0.7); resize(); log('quality down: pr ' + pixelRatio.toFixed(2)); }
        }
      }
      const pp = reduced ? 0.26 : p;
      if (!reduced) time += dt;

      const lightsOn = reduced ? 1 : smooth(0.035, 0.15, p);
      const speedT = reduced ? 0 : smooth(0.17, 0.36, p) * (1 - smooth(0.66, 0.88, p));
      speedS += (speedT - speedS) * (1 - Math.exp(-dt * 3));
      const brake = reduced ? 0 : smooth(0.66, 0.72, p) * (1 - smooth(0.9, 1.0, p) * 0.4);
      const v = speedS * 46; // m/s visivi
      travel += v * dt;

      // camera
      const u = toU(pp);
      posCurve.getPoint(u, cPos);
      tgtCurve.getPoint(u, cTgt);
      const far = aspect < 0.8 ? 1.85 : aspect < 1.2 ? 1.35 : 1;
      cPos.sub(cTgt).multiplyScalar(far).add(cTgt);
      if (aspect < 0.8) cPos.y += 0.25;
      if (speedS > 0.01) { // micro vibrazione
        cPos.y += Math.sin(time * 37.0) * 0.006 * speedS + Math.sin(time * 13.0) * 0.004 * speedS;
        cPos.x += Math.sin(time * 23.0) * 0.004 * speedS;
      }
      camera.position.copy(cPos);
      camera.lookAt(cTgt);
      camera.fov = (aspect < 0.8 ? 40 : 32) + speedS * 7;
      camera.updateProjectionMatrix();

      // ruote
      for (const w of wheels) w.rotation.x += (v / 0.34) * dt;
      if (carGroup) carGroup.position.y = Math.sin(time * 41) * 0.002 * speedS;

      // luci
      const hi = lightsOn;
      const flick = lightsOn < 0.999 && lightsOn > 0.02 ? (0.75 + 0.25 * Math.sin(time * 90)) : 1; // accensione xeno
      // i fari si vedono solo da davanti, i fanali solo da dietro (oltre all'occlusione del modello)
      const dz = camera.position.z, dl = camera.position.length() || 1;
      const frontVis = smooth(-0.15, 0.35, (dz - L.hz) / dl);
      const rearVis = smooth(-0.15, 0.35, (L.tz - dz) / dl);
      head.forEach((s) => { s.scale.setScalar(0.42 * hi); s.material.opacity = hi * flick * frontVis; s.material.color.setRGB(5, 5, 5.2); });
      headFlare.forEach((s) => { s.scale.set(2.4 * hi, 0.06 * hi, 1); s.material.opacity = 0.5 * hi * frontVis; });
      beamMat.uniforms.uI.value = 0.09 * hi * flick;
      const ti = hi * (0.55 + 0.45 * brake);
      tail.forEach((s) => { s.scale.setScalar(0.3 + 0.16 * brake); s.material.opacity = ti * rearVis; s.material.color.setRGB(2.6, 0.1, 0.04); });
      tailCore.forEach((s) => { s.scale.setScalar(0.1 + 0.05 * brake); s.material.opacity = ti * rearVis; s.material.color.setRGB(4, 0.7, 0.45); });

      // mondo
      RU.uScroll.value = travel;
      RU.uHead.value = hi * flick;
      RU.uTail.value = ti;
      RU.uCamPos.value.copy(camera.position);
      renderer.toneMappingExposure = 0.55 + 0.5 * lightsOn;

      const city = 0.15 + 0.85 * smooth(0.0, 0.2, pp) ; // la città si "accende" con i fari
      streakMat.opacity = city;
      const stretch = 0.05 + speedS * 7.5;
      for (let i = 0; i < streakCount; i++) {
        const d = sData[i];
        let z = d.z - travel;
        z = ((z + 90) % 180 + 180) % 180 - 90;
        vPos.set(d.x, d.y, z);
        vScl.set(d.th, d.th, d.th + stretch * d.len);
        mtx.compose(vPos, qId, vScl);
        streaks.setMatrixAt(i, mtx);
      }
      streaks.instanceMatrix.needsUpdate = true;

      if (rain) {
        const RUu = rain.material.uniforms;
        RUu.uTime.value = time;
        RUu.uOffset.value = travel;
        RUu.uVel.value = v;
        RUu.uAlpha.value = 0.14 + 0.18 * lightsOn;
        RUu.uCenter.value.set(camera.position.x * 0.6, 0, camera.position.z * 0.6);
      }

      bloom.strength = (mobile ? 0.5 : 0.8) + 0.25 * speedS;
      composer.render(dt);
    }

    function compile() { try { renderer.compile(scene, camera); } catch (e) { /* */ } }

    function dispose() {
      composer.dispose && composer.dispose();
      renderer.dispose();
      envTex.dispose();
      pmrem.dispose();
      scene.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) [].concat(o.material).forEach((m) => { m.map && m.map.dispose(); m.dispose(); });
      });
    }

    return { render, resize, setCar, compile, dispose, renderer };
  }

  return {
    destroy() {
      destroyed = true;
      cancelAnimationFrame(raf);
      io.disconnect();
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onResize);
      if (three) three.dispose();
      three = null;
    },
    get state() { return { progress: prog, mode: container.dataset.state || 'loading', reduced, mobile }; },
  };
}

/* ---------- helper ---------- */
function mulberry(a) {
  return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

function makeGlowTexture(THREE) {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  r.addColorStop(0, 'rgba(255,255,255,1)');
  r.addColorStop(0.12, 'rgba(255,255,255,0.85)');
  r.addColorStop(0.35, 'rgba(255,255,255,0.18)');
  r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function roadShader(THREE) {
  return {
    name: 'MCWetRoad',
    uniforms: {
      color: { value: null },
      tDiffuse: { value: null },
      textureMatrix: { value: null },
      uScroll: { value: 0 },
      uHead: { value: 0 },
      uTail: { value: 0 },
      uCar: { value: new THREE.Vector4(0.98, 2.3, -2.3, 0.66) }, // halfW, front, rear, headX
      uCamPos: { value: new THREE.Vector3() },
      uFogColor: { value: new THREE.Color() },
      uFogDensity: { value: 0.03 },
    },
    vertexShader: /* glsl */`
      uniform mat4 textureMatrix;
      varying vec4 vUv;
      varying vec3 vWorld;
      void main() {
        vUv = textureMatrix * vec4(position, 1.0);
        vec4 wp = modelMatrix * vec4(position, 1.0);
        vWorld = wp.xyz;
        gl_Position = projectionMatrix * viewMatrix * wp;
      }`,
    fragmentShader: /* glsl */`
      uniform vec3 color;
      uniform sampler2D tDiffuse;
      uniform float uScroll, uHead, uTail, uFogDensity;
      uniform vec4 uCar;
      uniform vec3 uCamPos, uFogColor;
      varying vec4 vUv;
      varying vec3 vWorld;

      float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
      float noise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.0-2.0*f);
        return mix(mix(hash(i), hash(i+vec2(1,0)), f.x), mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), f.x), f.y); }
      float fbm(vec2 p){ float s = 0.0, a = 0.5; for (int i = 0; i < 4; i++){ s += a * noise(p); p *= 2.03; a *= 0.5; } return s; }

      void main() {
        vec2 p = vec2(vWorld.x, vWorld.z + uScroll);
        float dist = length(uCamPos - vWorld);

        // pozzanghere
        float n = fbm(p * vec2(0.22, 0.12));
        float puddle = smoothstep(0.5, 0.6, n);
        float grain = noise(p * 38.0) * 0.6 + noise(p * 7.0) * 0.4;
        vec3 base = vec3(0.010, 0.011, 0.014) * (0.6 + 0.8 * grain);

        // segnaletica
        float dash = (1.0 - smoothstep(0.05, 0.075, abs(vWorld.x + 2.15))) * step(fract(p.y / 7.5), 0.42);
        float edge = 1.0 - smoothstep(0.045, 0.07, abs(vWorld.x - 3.0));
        float mark = max(dash, edge) * (1.0 - puddle * 0.7);
        base = mix(base, vec3(0.16), mark * 0.6);

        // riflesso: nitido nelle pozzanghere, sfumato in verticale sull'asfalto
        float rough = mix(0.018, 0.002, puddle);
        vec2 dis = (vec2(noise(p * 2.5), noise(p * 2.5 + 7.1)) - 0.5) * mix(0.018, 0.004, puddle);
        vec3 refl = vec3(0.0);
        for (int i = 0; i < 5; i++) {
          vec4 uv = vUv;
          uv.xy += (dis + vec2(0.0, (float(i) - 2.0) * rough)) * uv.w;
          refl += texture2DProj(tDiffuse, uv).rgb;
        }
        refl /= 5.0;
        vec3 V = normalize(uCamPos - vWorld);
        float fres = 0.04 + 0.96 * pow(1.0 - clamp(V.y, 0.0, 1.0), 5.0);
        float amt = mix(0.22, 0.9, puddle) * mix(0.45, 1.0, fres) * (1.0 - mark * 0.6);
        vec3 col = base + refl * amt;

        // pozze di luce dei fari e dei fanali
        float hw = uCar.x, front = uCar.y, rear = uCar.z, hx = uCar.w;
        float zf = vWorld.z - front;
        float spread = 0.7 + max(zf, 0.0) * 0.22;
        float pool = 0.0;
        for (int s = -1; s <= 1; s += 2) {
          vec2 d = vec2((vWorld.x - float(s) * hx) / spread, (zf - 6.0) / 7.5);
          pool += exp(-dot(d, d) * 1.6);
        }
        pool *= smoothstep(-0.2, 1.2, zf);
        col += vec3(1.0, 0.94, 0.84) * pool * uHead * (0.05 + 0.08 * grain + 0.25 * puddle);
        float zr = rear - vWorld.z;
        vec2 dt = vec2(vWorld.x / (hw * 1.2), (zr - 0.8) / 1.6);
        col += vec3(1.0, 0.05, 0.02) * exp(-dot(dt, dt) * 1.4) * smoothstep(-0.2, 0.4, zr) * uTail * 0.12;

        // ombra di contatto sotto l'auto
        vec2 q = vec2(abs(vWorld.x) - hw * 0.92, max(vWorld.z - front + 0.2, rear + 0.2 - vWorld.z));
        float sd = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0);
        col *= mix(0.08, 1.0, smoothstep(-0.25, 0.45, sd));

        // nebbia
        float f = 1.0 - exp(-uFogDensity * uFogDensity * dist * dist);
        col = mix(col, uFogColor, f);
        gl_FragColor = vec4(col, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  };
}
