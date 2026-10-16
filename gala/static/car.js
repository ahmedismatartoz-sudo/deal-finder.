// Gala Auto — SUV di lusso in 3D: luce da studio, poi dentro l'abitacolo, poi il vano motore a cofano aperto.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { BokehPass } from 'three/addons/postprocessing/BokehPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { RectAreaLightUniformsLib } from 'three/addons/lights/RectAreaLightUniformsLib.js';

const FORCE = new URLSearchParams(location.search).get('p');
const MOBILE = matchMedia('(max-width: 820px)').matches || /Android|iPhone|iPad/i.test(navigator.userAgent);
const TS = MOBILE ? 256 : 512;                      // risoluzione delle texture generate
const canvas = document.getElementById('car3d');
const show = document.getElementById('top');
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const ease = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const V = (x, y, z) => new THREE.Vector3(x, y, z);
const V2 = (x, y) => new THREE.Vector2(x, y);

function fail(e) {
  console.warn('3D non disponibile', e);
  document.documentElement.classList.add('nogl');
  window.dispatchEvent(new Event('car-ready'));
}
try { init(); } catch (e) { fail(e); }

// ===================================================================================
function init() {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, MOBILE ? 1.5 : 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  RectAreaLightUniformsLib.init();
  const T = makeTextures();
  const envStudio = studioEnv(renderer, false), envBright = studioEnv(renderer, true);

  const camera = new THREE.PerspectiveCamera(30, 1, 0.02, 200);
  const ext = buildExterior(envStudio, T);
  const cab = buildInterior(envBright, T);
  const bay = buildEngineBay(envStudio, T);

  let heroInt = null, heroOn = false;
  fetch('/api/hero').then(r => r.json()).then(h => {
    if (!h.ok) return;
    const L = new THREE.TextureLoader();
    L.load('/hero/ext.webp?v=' + h.id, tex => { tex.colorSpace = THREE.SRGBColorSpace; ext.usePhoto(photoCar(tex)); heroOn = true; });
    if (h.int) L.load('/hero/int.jpg?v=' + h.id, tex => { tex.colorSpace = THREE.SRGBColorSpace; heroInt = photoInterior(tex); });
  }).catch(() => {});

  const W = innerWidth, H = innerHeight;
  const rt = new THREE.WebGLRenderTarget(W, H, { type: THREE.HalfFloatType, samples: MOBILE ? 0 : 4 });
  const composer = new EffectComposer(renderer, rt);
  const rp = new RenderPass(ext.scene, camera);
  composer.addPass(rp);
  const bokeh = new BokehPass(ext.scene, camera, { focus: 6, aperture: 0.0002, maxblur: 0.0 });
  if (!MOBILE) composer.addPass(bokeh);
  const bloom = new UnrealBloomPass(new THREE.Vector2(W, H), 0.25, 0.4, 0.92);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  const path = (pts, looks) => [new THREE.CatmullRomCurve3(pts, false, 'catmullrom', 0.5), new THREE.CatmullRomCurve3(looks, false, 'catmullrom', 0.5)];
  // fuori: dal 3/4 anteriore fino al finestrino del guidatore
  const [extCam, extLook] = path([V(7.2, 1.7, 6.4), V(3.4, 1.55, 4.4), V(0.4, 1.45, 2.25), V(-0.25, 1.36, 1.25)],
    [V(0, 0.85, 0), V(0, 0.95, 0), V(-0.2, 1.2, 0.3), V(-0.35, 1.2, -0.3)]);
  // con la foto: davanti all'auto, poi verso il parabrezza
  const [heroCam, heroLook] = path([V(0, 1.3, 9.2), V(0, 1.35, 7.0), V(0.05, 1.5, 5.0), V(0.1, 1.6, 3.6)],
    [V(0, 1.05, 0), V(0, 1.1, 0), V(0.05, 1.3, 0), V(0.1, 1.45, 0)]);
  // dentro: dalla portiera aperta al posto di guida, sguardo sulla plancia, poi sul quadro strumenti
  const [cabCam, cabLook] = path([V(-1.25, 1.28, 0.05), V(-0.85, 1.22, 0.18), V(-0.4, 1.17, 0.28), V(-0.3, 1.15, 0.22), V(-0.36, 1.08, 0.02), V(-0.38, 1.04, -0.24)],
    [V(0.3, 0.75, -0.4), V(0.1, 0.85, -0.8), V(-0.15, 0.95, -0.9), V(0.05, 0.97, -0.62), V(-0.36, 1.0, -0.64), V(-0.38, 1.0, -0.66)]);
  // vano motore: dall'alto davanti, lungo la testata, primo piano sulla scritta, poi si alza
  const [bayCam, bayLook] = path([V(0.0, 2.6, 2.3), V(1.3, 1.85, 1.2), V(0.95, 1.4, 0.35), V(0.75, 1.3, 0.0), V(0.2, 1.9, 0.6), V(0, 3.0, 1.6)],
    [V(0, 0.75, 0), V(0.1, 0.8, -0.1), V(0.32, 0.92, -0.1), V(0.33, 0.93, -0.2), V(0, 0.8, -0.1), V(0, 0.7, 0)]);

  let target = 0, cur = 0, introStart = null, visible = true, aspect = 1, lastT = 0, lastTarget = 0, rpm = 0;
  const copies = [...document.querySelectorAll('.copy')];
  const fade = document.getElementById('fade'), flash = document.getElementById('flash'), prog = document.getElementById('prog'), hint = document.getElementById('hint');
  const nav = document.getElementById('nav'), fab = document.querySelector('.fab');
  const E1 = 0.11, E2 = 0.5, END = 0.95;

  function onScroll() {
    const r = show.getBoundingClientRect();
    target = clamp(-r.top / (r.height - innerHeight));
    nav.classList.toggle('solid', r.bottom < innerHeight * 0.6 || target > 0.03);
    fab && fab.classList.toggle('on', target > 0.05);
  }
  addEventListener('scroll', onScroll, { passive: true });
  function resize() {
    const w = innerWidth, h = innerHeight; aspect = w / h;
    renderer.setSize(w, h, false); composer.setSize(w, h);
    camera.aspect = aspect; camera.updateProjectionMatrix();
  }
  addEventListener('resize', resize); resize(); onScroll();
  new IntersectionObserver(e => { visible = e[0].isIntersecting; }, { threshold: 0 }).observe(show);

  const pos = V(0, 0, 0), look = V(0, 0, 0), tmp = V(0, 0, 0);
  const clock = new THREE.Clock();
  const bell = (v, c, w) => Math.exp(-Math.pow((v - c) / w, 2));
  let ready = false;

  function place(curveP, curveL, u, far) {
    curveP.getPoint(u, pos); curveL.getPoint(u, look);
    tmp.copy(pos).sub(look).multiplyScalar(far).add(look);
    camera.position.copy(tmp); camera.up.set(0, 1, 0); camera.lookAt(look);
  }

  function frame() {
    requestAnimationFrame(frame);
    if (!visible || document.hidden) return;
    const t = clock.getElapsedTime(), dt = Math.min(0.1, t - lastT); lastT = t;
    if (introStart === null) introStart = t;
    const intro = FORCE !== null ? 1 : clamp((t - introStart) / 4.2);
    cur = FORCE !== null ? +FORCE : cur + (target - cur) * 0.07;
    const p = cur;
    const speed = Math.abs(target - lastTarget) / Math.max(dt, 1e-3); lastTarget = target;
    rpm += ((1 + Math.min(speed * 60, 6)) - rpm) * 0.05;
    const portrait = aspect < 0.8;
    let blur = 0, focus = 4;

    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    if (p < E1) {
      rp.scene = ext.scene; bokeh.scene = ext.scene;
      const u = ease(clamp(p / E1)) * 0.999;
      camera.fov = portrait ? 46 : 30; camera.near = 0.05;
      if (heroOn) place(heroCam, heroLook, u, (portrait ? 1.75 : 1) * (1 - u) + u);
      else place(extCam, extLook, u, (portrait ? 1.6 : 1) * (1 - u) + u);
      // all'inizio la telecamera ondeggia piano attorno all'auto
      const sway = (1 - smooth(0, 0.03, p)) * Math.sin(t * 0.18) * (heroOn ? 0.04 : 0.35);
      camera.position.applyAxisAngle(V(0, 1, 0), sway); camera.lookAt(look);
      ext.update(t, intro, u);
      focus = camera.position.distanceTo(look); blur = bell(p, E1, 0.03) * 0.012;
      if (heroOn) renderer.toneMapping = THREE.NoToneMapping;
    } else if (p < E2) {
      const u = clamp((p - E1) / (E2 - E1)) * 0.999;
      if (heroInt) {
        rp.scene = heroInt.scene; bokeh.scene = heroInt.scene; renderer.toneMapping = THREE.NoToneMapping;
        camera.fov = 40; camera.near = 0.01;
        heroInt.update(t, u, camera);
        focus = camera.position.z;
      } else {
        rp.scene = cab.scene; bokeh.scene = cab.scene;
        camera.fov = portrait ? 70 : 52; camera.near = 0.01;
        place(cabCam, cabLook, u, 1);
        cab.update(t, dt, u, rpm);
      }
      focus = camera.position.distanceTo(look); blur = bell(p, E1, 0.025) * 0.015 + bell(p, E2, 0.03) * 0.015;
    } else {
      rp.scene = bay.scene; bokeh.scene = bay.scene;
      const u = clamp((p - E2) / (END - E2)) * 0.999;
      camera.fov = portrait ? 58 : 38; camera.near = 0.02;
      place(bayCam, bayLook, u, portrait ? 1.35 - 0.25 * smooth(0.3, 0.6, u) : 1);
      bay.update(t, dt, u, rpm);
      focus = camera.position.distanceTo(look); blur = bell(p, E2, 0.03) * 0.015 + smooth(0.88, 1, u) * 0.01;
    }
    camera.updateProjectionMatrix();
    bloom.strength = renderer.toneMapping === THREE.NoToneMapping ? 0.08 : 0.25;
    bokeh.uniforms.focus.value = focus;
    bokeh.uniforms.aperture.value = 0.00025 + blur * 0.02;
    bokeh.uniforms.maxblur.value = 0.004 + blur;

    for (const c of copies) {
      const a = +c.dataset.from, b = +c.dataset.to;
      c.classList.toggle('on', p >= a && p < b && (c.classList.contains('c0') ? intro > 0.8 : true));
    }
    // passaggi: una "chiusura d'occhi" scura invece del lampo
    flash.style.opacity = 0;
    fade.style.opacity = Math.max(bell(p, E1, 0.012) * 0.95, bell(p, E2, 0.014) * 0.95, smooth(0.93, 1, p));
    prog.style.transform = `scaleY(${p})`;
    hint.style.opacity = p > 0.01 || intro < 0.9 ? 0 : 1;
    composer.render();
    if (!ready) { ready = true; window.dispatchEvent(new Event('car-ready')); }
  }
  frame();
}

// ===================================================================================
// Texture generate nel codice: pelle, pelle trapuntata, legno, alluminio spazzolato, fusione, plastica
function makeTextures() {
  const N = TS;
  const hash = (x, y) => { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); };
  const vnoise = (x, y, per) => {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
    const w = (a) => ((a % per) + per) % per;
    const a = hash(w(xi), w(yi)), b = hash(w(xi + 1), w(yi)), c = hash(w(xi), w(yi + 1)), d = hash(w(xi + 1), w(yi + 1));
    const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
  };
  const fbm = (x, y, base, oct = 4) => { let s = 0, amp = 0.5, f = base; for (let i = 0; i < oct; i++) { s += amp * vnoise(x * f, y * f, f); f *= 2; amp *= 0.5; } return s; };
  const height = fn => { const h = new Float32Array(N * N); for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) h[y * N + x] = fn(x / N, y / N); return h; };
  const toNormal = (h, k, rep = 1) => {
    const c = document.createElement('canvas'); c.width = c.height = N; const ctx = c.getContext('2d'); const img = ctx.createImageData(N, N);
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
      const dx = h[y * N + (x + 1) % N] - h[y * N + (x - 1 + N) % N], dy = h[((y + 1) % N) * N + x] - h[((y - 1 + N) % N) * N + x];
      let nx = -dx * k, ny = -dy * k, nz = 1; const l = Math.hypot(nx, ny, nz); nx /= l; ny /= l; nz /= l;
      const i = (y * N + x) * 4; img.data[i] = (nx * .5 + .5) * 255; img.data[i + 1] = (ny * .5 + .5) * 255; img.data[i + 2] = (nz * .5 + .5) * 255; img.data[i + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(rep, rep); t.anisotropy = 4; return t;
  };
  const colorTex = (fn, rep = 1) => {
    const c = document.createElement('canvas'); c.width = c.height = N; const ctx = c.getContext('2d'); const img = ctx.createImageData(N, N);
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) { const [r, g, b] = fn(x / N, y / N); const i = (y * N + x) * 4; img.data[i] = r; img.data[i + 1] = g; img.data[i + 2] = b; img.data[i + 3] = 255; }
    ctx.putImageData(img, 0, 0);
    const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(rep, rep); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; return t;
  };
  // pelle: grana fine
  const leatherH = height((u, v) => fbm(u, v, 24, 4) * 0.6 + Math.pow(1 - Math.abs(fbm(u + 3, v + 7, 40, 2) * 2 - 1), 6) * 0.25);
  // pelle trapuntata a rombi con cuciture
  const quiltH = height((u, v) => {
    const a = (u + v) * 6, b = (u - v) * 6, fa = a - Math.floor(a), fb = b - Math.floor(b);
    const d = Math.min(fa, 1 - fa, fb, 1 - fb);
    let h = Math.min(1, d * 7) * 0.8 + fbm(u, v, 24, 3) * 0.12;
    if (d < 0.05 && d > 0.02) { const s = ((a + b) * 10) % 1; if (s < 0.5) h += 0.15; }
    return h;
  });
  const brushedH = height((u, v) => vnoise(u * 2, v * 300, 300) * 0.5 + vnoise(u * 8, v * 600, 600) * 0.3);
  const castH = height((u, v) => fbm(u, v, 32, 4) * 0.8);
  const plasticH = height((u, v) => fbm(u, v, 64, 3));
  const wood = colorTex((u, v) => {
    const t = v * 26 + fbm(u * 0.3, v * 2, 4, 4) * 1.6;
    const s = 0.5 + 0.5 * Math.sin(t * 6.283), fine = fbm(u * 0.2, v * 40, 8, 3);
    const k = Math.pow(s, 2.2) * 0.7 + fine * 0.35;
    return [52 + k * 70, 32 + k * 40, 20 + k * 22];
  });
  const carpet = toNormal(height((u, v) => hash(Math.floor(u * N), Math.floor(v * N)) * 0.5 + fbm(u, v, 64, 2) * 0.5), 3, 6);
  return {
    leatherN: toNormal(leatherH, 3, 6), quiltN: toNormal(quiltH, 10, 1), brushedN: toNormal(brushedH, 3, 1),
    castN: toNormal(castH, 5, 2), plasticN: toNormal(plasticH, 3, 3), wood, carpet,
  };
}

// ---------- ambiente da studio (riflessi sulle superfici lucide) ----------
function studioEnv(renderer, bright) {
  const s = new THREE.Scene();
  const base = bright ? 0x8a8794 : 0x0d0c12;
  s.add(new THREE.Mesh(new THREE.SphereGeometry(40, 32, 16), new THREE.MeshBasicMaterial({ color: base, side: THREE.BackSide })));
  const panel = (w, h, pos, rot, k, col = 0xffffff) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(col).multiplyScalar(k), side: THREE.DoubleSide }));
    m.position.set(...pos); m.rotation.set(...rot); s.add(m);
  };
  panel(16, 7, [0, 12, 0], [Math.PI / 2, 0, 0], bright ? 4 : 3);                 // grande softbox sopra
  panel(2.5, 9, [-14, 4, 0], [0, Math.PI / 2, 0], bright ? 3 : 2.2);
  panel(2.5, 9, [14, 4, 2], [0, -Math.PI / 2, 0], bright ? 3 : 2, 0xfff2e0);   // luce calda
  panel(12, 1.2, [0, 1.2, -16], [0, 0, 0], bright ? 2.5 : 1.4, 0xb9a6ff);       // riflesso viola del marchio
  panel(12, 1.2, [0, 1.2, 16], [0, Math.PI, 0], bright ? 2 : 1.2, 0x9fb0ff);
  if (bright) panel(30, 12, [0, 6, -25], [0, 0, 0], 2.2, 0xeef0ff);
  const pm = new THREE.PMREMGenerator(renderer);
  const tex = pm.fromScene(s, 0.035).texture; pm.dispose(); return tex;
}

// ===================================================================================
// ESTERNO: SUV di lusso originale (design Gala), studio scuro con ombre morbide e luce che scorre
function buildExterior(env, T) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0910);
  scene.fog = new THREE.Fog(0x0a0910, 14, 34);
  scene.environment = env;

  const floor = new THREE.Mesh(new THREE.CircleGeometry(30, 64), new THREE.MeshStandardMaterial({ color: 0x1a1822, roughness: 0.55, metalness: 0.1 }));
  floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
  // pedana girevole
  const plate = new THREE.Mesh(new THREE.CylinderGeometry(3.6, 3.65, 0.06, 96), new THREE.MeshPhysicalMaterial({ color: 0x15131c, metalness: 0.6, roughness: 0.25, clearcoat: 1 }));
  plate.position.y = 0.03; plate.receiveShadow = true; scene.add(plate);
  const ringM = new THREE.MeshBasicMaterial({ color: new THREE.Color(0x9b5cff).multiplyScalar(1.6), toneMapped: false, transparent: true, opacity: 0 });
  const ring = new THREE.Mesh(new THREE.TorusGeometry(3.63, 0.008, 6, 160), ringM); ring.rotation.x = Math.PI / 2; ring.position.y = 0.062; scene.add(ring);

  const hemi = new THREE.HemisphereLight(0xdcd6ff, 0x0a0910, 0.0); scene.add(hemi);
  const key = new THREE.DirectionalLight(0xfff4e8, 0); key.position.set(4, 9, 6); key.castShadow = true;
  key.shadow.mapSize.set(MOBILE ? 1024 : 2048, MOBILE ? 1024 : 2048); key.shadow.camera.left = -5; key.shadow.camera.right = 5; key.shadow.camera.top = 5; key.shadow.camera.bottom = -5;
  key.shadow.bias = -0.0002; key.shadow.normalBias = 0.03; key.shadow.radius = 6; scene.add(key);
  const rim = new THREE.DirectionalLight(0xb9a6ff, 0); rim.position.set(-6, 4, -5); scene.add(rim);
  // la barra di luce che scorre sull'auto all'inizio
  const fillL = new THREE.RectAreaLight(0xf2eeff, 0, 6, 2); fillL.position.set(5, 1.6, 4); fillL.lookAt(0, 0.8, 0); scene.add(fillL);
  const sweep = new THREE.RectAreaLight(0xffffff, 0, 0.6, 5); sweep.position.set(-6, 3.2, 3); sweep.lookAt(0, 0.8, 0); scene.add(sweep);

  const suv = buildSUV(T);
  scene.add(suv.group);
  let photo = null;

  return {
    scene, suv,
    usePhoto(ph) { photo = ph; suv.group.visible = false; scene.add(ph.group); },
    update(t, intro, u) {
      const a = smooth(0.0, 0.55, intro), b = smooth(0.45, 1, intro);
      sweep.intensity = 14 * Math.sin(Math.PI * clamp(intro / 0.75));
      sweep.position.x = -6 + 12 * clamp(intro / 0.75); sweep.lookAt(0, 0.8, 0);
      key.intensity = 3.4 * b; rim.intensity = 2.2 * b; hemi.intensity = 0.6 * b; fillL.intensity = 4 * b;
      scene.environmentIntensity = 0.3 + 1.3 * a;
      ringM.opacity = 0.7 * b;
      suv.setLights(smooth(0.6, 1, intro));
      suv.group.rotation.y = (1 - smooth(0, 0.6, u)) * Math.sin(t * 0.12) * 0.08;
      plate.rotation.y = t * 0.05;
      if (photo) photo.update(t, intro);
    },
  };
}

function buildSUV(T) {
  const group = new THREE.Group();
  const DEPTH = 1.66, BT = 0.12, BS = 0.1, HW = DEPTH / 2 + BT, CROWN = 0.035;
  const WF = 1.52, WR = -1.5, WY = 0.4, WRAD = 0.4, ARCH = 0.53;
  const taper = (x, y) => 1 - smooth(1.1, 1.58, y) * 0.15 - smooth(1.9, 2.5, x) * 0.07 - smooth(-2.0, -2.5, x) * 0.04 - smooth(0.5, 0.3, y) * 0.03;
  const hw = (x, y) => HW * taper(x, y);

  // profilo: muso alto e lungo, parabrezza inclinato, tetto lungo e piatto, coda verticale
  const s = new THREE.Shape();
  s.moveTo(-2.36, 0.42);
  s.lineTo(WR - ARCH, 0.42); s.absarc(WR, 0.42, ARCH, Math.PI, 0, true);
  s.lineTo(WF - ARCH, 0.42); s.absarc(WF, 0.42, ARCH, Math.PI, 0, true);
  s.lineTo(2.3, 0.42);
  s.quadraticCurveTo(2.47, 0.44, 2.48, 0.6);
  s.lineTo(2.47, 0.86);
  s.quadraticCurveTo(2.45, 0.99, 2.25, 1.01);
  s.quadraticCurveTo(1.6, 1.06, 0.95, 1.1);
  s.lineTo(0.88, 1.12);
  s.quadraticCurveTo(0.45, 1.42, 0.08, 1.6);
  s.quadraticCurveTo(-0.05, 1.65, -0.3, 1.65);
  s.lineTo(-1.65, 1.63);
  s.quadraticCurveTo(-2.05, 1.6, -2.22, 1.46);
  s.lineTo(-2.37, 1.1);
  s.quadraticCurveTo(-2.44, 0.6, -2.36, 0.42);

  let g = new THREE.ExtrudeGeometry(s, { depth: DEPTH, bevelEnabled: true, bevelThickness: BT, bevelSize: BS, bevelSegments: MOBILE ? 5 : 7, curveSegments: MOBILE ? 28 : 40, steps: 1 });
  g.translate(0, 0, -DEPTH / 2);
  g.deleteAttribute('uv'); g.deleteAttribute('normal');
  g = mergeVertices(g, 1e-4);
  g = refine(g, MOBILE ? 0.08 : 0.06);
  const P = g.attributes.position;
  for (let i = 0; i < P.count; i++) {
    const x = P.getX(i), y = P.getY(i), z = P.getZ(i), zn = clamp(Math.abs(z) / HW);
    P.setXYZ(i, x, y + CROWN * (1 - zn * zn) * smooth(0.95, 1.12, y), z * taper(x, y));
  }
  g.computeVertexNormals();
  const N = g.attributes.normal, I = g.index.array;
  const paintIdx = [], trimIdx = [];
  const a = V(0, 0, 0), b = V(0, 0, 0), c = V(0, 0, 0), n = V(0, 0, 0), m = V(0, 0, 0);
  for (let f = 0; f < I.length; f += 3) {
    a.fromBufferAttribute(P, I[f]); b.fromBufferAttribute(P, I[f + 1]); c.fromBufferAttribute(P, I[f + 2]);
    n.subVectors(c, b).cross(m.subVectors(a, b)).normalize(); m.copy(a).add(b).add(c).multiplyScalar(1 / 3);
    let trim = n.y < -0.35;                 // sottoscocca
    for (const wx of [WF, WR]) { const dx = wx - m.x, dy = 0.42 - m.y; if (Math.hypot(dx, dy) < ARCH + 0.12 && Math.abs(n.z) < 0.75 && (n.x * dx + n.y * dy) > 0) trim = true; }
    (trim ? trimIdx : paintIdx).push(I[f], I[f + 1], I[f + 2]);
  }
  const sub = idx => { const gg = new THREE.BufferGeometry(); gg.setAttribute('position', P); gg.setAttribute('normal', N); gg.setIndex(idx); return gg; };
  const paint = new THREE.MeshPhysicalMaterial({ color: 0x5d5868, metalness: 0.55, roughness: 0.3, clearcoat: 1, clearcoatRoughness: 0.04, iridescence: 0.3, iridescenceIOR: 1.6, envMapIntensity: 1.8 });
  const trimM = new THREE.MeshPhysicalMaterial({ color: 0x111014, metalness: 0.3, roughness: 0.55, normalMap: T.plasticN, normalScale: V2(0.3, 0.3) });
  const glass = new THREE.MeshPhysicalMaterial({ color: 0x050508, metalness: 0.1, roughness: 0.03, clearcoat: 1, envMapIntensity: 2, side: THREE.DoubleSide });
  const chrome = new THREE.MeshPhysicalMaterial({ color: 0xe8e8ee, metalness: 1, roughness: 0.08 });
  const body = new THREE.Mesh(sub(paintIdx), paint); body.castShadow = true; group.add(body);
  const trimMesh = new THREE.Mesh(sub(trimIdx), trimM); trimMesh.castShadow = true; group.add(trimMesh);

  // superfici sopra (cofano, parabrezza) tramite raggi verso il basso
  const bodyMesh = new THREE.Mesh(g); bodyMesh.updateMatrixWorld();
  const rc = new THREE.Raycaster();
  const hitDown = (x, z) => { rc.set(V(x, 4, z), V(0, -1, 0)); const h = rc.intersectObject(bodyMesh)[0]; return h ? h.point.y : null; };
  const hitBack = (y, z) => { rc.set(V(-5, y, z), V(1, 0, 0)); const h = rc.intersectObject(bodyMesh)[0]; return h ? h.point.x : null; };
  const grid = (nu, nv, fn) => {
    const pos = [], idx = [];
    for (let i = 0; i <= nu; i++) for (let j = 0; j <= nv; j++) pos.push(...fn(i / nu, j / nv));
    for (let i = 0; i < nu; i++) for (let j = 0; j < nv; j++) { const p0 = i * (nv + 1) + j, p1 = p0 + nv + 1; idx.push(p0, p1, p0 + 1, p1, p1 + 1, p0 + 1); }
    const gg = new THREE.BufferGeometry(); gg.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); gg.setIndex(idx); gg.computeVertexNormals(); return gg;
  };
  // parabrezza
  group.add(new THREE.Mesh(grid(20, 16, (u, v) => {
    const x = 0.84 - u * 0.78, half = hw(x, 1.4) * 0.84 * (1 - 0.04 * Math.pow(2 * v - 1, 6)), z = (v * 2 - 1) * half;
    return [x, (hitDown(x, z) ?? 1.4) + 0.008, z];
  }), glass));
  // lunotto
  group.add(new THREE.Mesh(grid(10, 16, (u, v) => {
    const y = 1.16 + u * 0.36, z = (v * 2 - 1) * hw(-2.3, y) * 0.8;
    return [(hitBack(y, z) ?? -2.35) - 0.008, y, z];
  }), glass));

  // vetri laterali, montanti neri, profilo cromato (sulla fiancata piatta)
  const onSide = (pts, sg, off, mat, steps = 0) => {
    const sh = new THREE.Shape(); sh.moveTo(...pts[0]); pts.slice(1).forEach(p => sh.lineTo(...p)); sh.closePath();
    let gg = new THREE.ShapeGeometry(sh, 16); gg.deleteAttribute('uv'); gg.deleteAttribute('normal'); gg = mergeVertices(gg, 1e-5); gg = refine(gg, 0.05);
    const Q = gg.attributes.position;
    for (let k = 0; k < Q.count; k++) { const x = Q.getX(k), y = Q.getY(k); Q.setXYZ(k, x, y, sg * (hw(x, y) + off)); }
    if (sg < 0) { const ix = gg.index.array; for (let k = 0; k < ix.length; k += 3) { const t = ix[k + 1]; ix[k + 1] = ix[k + 2]; ix[k + 2] = t; } }
    gg.computeVertexNormals(); const me = new THREE.Mesh(gg, mat); group.add(me); return me;
  };
  const win = [[0.66, 1.17], [0.12, 1.52], [-0.2, 1.555], [-1.66, 1.54], [-2.04, 1.44], [-2.2, 1.17]];
  for (const sg of [1, -1]) {
    onSide(win, sg, 0.004, glass);
    onSide([[-0.08, 1.17], [-0.06, 1.56], [-0.2, 1.56], [-0.22, 1.17]], sg, 0.006, trimM);           // montante B
    onSide([[-1.18, 1.17], [-1.16, 1.55], [-1.28, 1.55], [-1.3, 1.17]], sg, 0.006, trimM);         // montante C
    onSide([[0.7, 1.155], [0.7, 1.172], [-2.24, 1.172], [-2.24, 1.155]], sg, 0.008, chrome);    // profilo cromato
    onSide([[0.98, 0.43], [0.98, 0.6], [-0.96, 0.6], [-0.96, 0.43]], sg, 0.005, trimM);          // fascia bassa scura
    onSide([[0.98, 0.6], [0.98, 0.625], [-0.96, 0.625], [-0.96, 0.6]], sg, 0.008, chrome);          // listello basso
    for (const [x0, x1] of [[0.62, -0.12], [-0.18, -1.2]]) {                                     // linee delle portiere
      const gl = new THREE.BufferGeometry().setFromPoints([V(x0, 1.15, 0), V(x0 + 0.02, 0.6, 0), V(x1 + 0.05, 0.6, 0), V(x1, 1.15, 0)].map(p => V(p.x, p.y, sg * (hw(p.x, p.y) + 0.003))));
      group.add(new THREE.Line(gl, new THREE.LineBasicMaterial({ color: 0x050407 })));
    }
    // maniglie a filo
    onSide([[0.36, 1.04], [0.36, 1.065], [0.14, 1.065], [0.14, 1.04]], sg, 0.006, chrome);
    onSide([[-0.72, 1.04], [-0.72, 1.065], [-0.94, 1.065], [-0.94, 1.04]], sg, 0.006, chrome);
  }
  // specchietti
  for (const sg of [1, -1]) {
    const mir = new THREE.Mesh(new RoundedBoxGeometry(0.26, 0.14, 0.2, 3, 0.05), paint); mir.position.set(0.6, 1.22, sg * (hw(0.6, 1.22) + 0.12)); mir.castShadow = true; group.add(mir);
  }

  // fari a LED sottili, mascherina, fanali posteriori a tutta larghezza
  const ledW = new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false });
  const ledR = new THREE.MeshBasicMaterial({ color: 0xff2030, toneMapped: false });
  const front = x => x;
  const grille = new THREE.Mesh(new RoundedBoxGeometry(0.06, 0.26, 0.95, 2, 0.02), new THREE.MeshPhysicalMaterial({ color: 0x0c0b10, metalness: 0.9, roughness: 0.3, normalMap: T.brushedN })); grille.position.set(2.47, 0.74, 0); group.add(grille);
  for (let k = 0; k < 5; k++) { const bar = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.012, 0.9), chrome); bar.position.set(2.5, 0.64 + k * 0.05, 0); group.add(bar); }
  const heads = [];
  for (const sg of [1, -1]) {
    const hl = new THREE.Mesh(new RoundedBoxGeometry(0.05, 0.1, 0.42, 2, 0.02), new THREE.MeshPhysicalMaterial({ color: 0x07070a, metalness: 0.3, roughness: 0.05, clearcoat: 1 }));
    hl.position.set(2.46, 0.84, sg * 0.66); group.add(hl);
    const strip = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.012, 0.4), ledW); strip.position.set(2.49, 0.875, sg * 0.66); group.add(strip); heads.push(strip);
    const strip2 = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.008, 0.3), ledW); strip2.position.set(2.49, 0.82, sg * 0.7); group.add(strip2); heads.push(strip2);
  }
  const tail = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.025, 1.7), ledR); tail.position.set(-2.4, 1.06, 0); group.add(tail);
  const tailHalo = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.09, 1.72), new THREE.MeshPhysicalMaterial({ color: 0x1a0306, metalness: 0.2, roughness: 0.05, clearcoat: 1 })); tailHalo.position.set(-2.39, 1.06, 0); group.add(tailHalo);
  // targa con il nome
  const plateTex = textPlate('GALA AUTO', '#111', '#fff', 512, 112);
  const pl = new THREE.Mesh(new THREE.PlaneGeometry(0.52, 0.11), new THREE.MeshStandardMaterial({ map: plateTex, roughness: 0.4 })); pl.position.set(2.5, 0.56, 0); pl.rotation.y = Math.PI / 2; group.add(pl);
  const plb = pl.clone(); plb.position.set(-2.44, 0.7, 0); plb.rotation.y = -Math.PI / 2; group.add(plb);
  // barre sul tetto
  for (const sg of [1, -1]) { const rr = new THREE.Mesh(new RoundedBoxGeometry(1.9, 0.04, 0.05, 2, 0.015), chrome); rr.position.set(-0.95, 1.7, sg * hw(-0.95, 1.65) * 0.86); group.add(rr); }

  // cerchi da 22" multirazza
  const tireGeo = (() => { const pr = []; for (let k = 0; k <= 20; k++) { const an = -Math.PI / 2 + Math.PI * k / 20; pr.push(V2(WRAD - 0.07 + Math.cos(an) * 0.07, Math.sin(an) * 0.14)); } return new THREE.LatheGeometry([V2(0.31, -0.14), ...pr, V2(0.31, 0.14)], 64); })();
  const tireM = new THREE.MeshStandardMaterial({ color: 0x111111, roughness: 0.85, normalMap: T.plasticN, normalScale: V2(0.4, 0.4) });
  const rimM = new THREE.MeshPhysicalMaterial({ color: 0xd0d0d8, metalness: 1, roughness: 0.16, clearcoat: 0.6 });
  const darkRim = new THREE.MeshPhysicalMaterial({ color: 0x1b1a20, metalness: 0.9, roughness: 0.3 });
  const discM = new THREE.MeshStandardMaterial({ color: 0x5a5a60, metalness: 0.9, roughness: 0.5, normalMap: T.brushedN });
  const calM = new THREE.MeshPhysicalMaterial({ color: 0x6b46e0, metalness: 0.3, roughness: 0.3, clearcoat: 1 });
  const bodyG = new THREE.Group(); [...group.children].forEach(ch => bodyG.add(ch)); bodyG.scale.set(0.97, 1.07, 1); group.add(bodyG);
  for (const wx of [WF, WR]) for (const sg of [1, -1]) {
    const w = new THREE.Group(); w.position.set(wx * 0.97, 0.455, sg * (hw(wx, WY) - 0.15));
    const tire = new THREE.Mesh(tireGeo, tireM); tire.rotation.x = Math.PI / 2; tire.castShadow = true; w.add(tire);
    const dish = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.02, 48), darkRim); dish.rotation.x = Math.PI / 2; dish.position.z = sg * 0.04; w.add(dish);
    const lip = new THREE.Mesh(new THREE.TorusGeometry(0.3, 0.012, 8, 64), rimM); lip.position.z = sg * 0.12; w.add(lip);
    for (let k = 0; k < 10; k++) {
      const an = k / 10 * Math.PI * 2;
      for (const off of [-0.07, 0.07]) {
        const sp = new THREE.Mesh(new THREE.BoxGeometry(0.25, 0.026, 0.03), rimM);
        sp.position.set(Math.cos(an + off) * 0.165, Math.sin(an + off) * 0.165, sg * 0.105); sp.rotation.z = an + off * 0.3; w.add(sp);
      }
    }
    const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.04, 24), rimM); hub.rotation.x = Math.PI / 2; hub.position.z = sg * 0.12; w.add(hub);
    const disc = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.24, 0.03, 48), discM); disc.rotation.x = Math.PI / 2; disc.position.z = sg * 0.0; w.add(disc);
    const cal = new THREE.Mesh(new RoundedBoxGeometry(0.16, 0.1, 0.07, 2, 0.02), calM); cal.position.set(-0.13, 0.13, sg * 0.03); cal.rotation.z = 0.8; w.add(cal);
    group.add(w);
  }
  group.traverse(o => { if (o.isMesh && !o.material.isMeshBasicMaterial) o.castShadow = true; });

  return {
    group,
    setLights(v) {
      ledW.color.setRGB(1, 1, 1).multiplyScalar(0.2 + v * 5);
      ledR.color.setRGB(1, 0.1, 0.15).multiplyScalar(0.2 + v * 4);
    },
  };
}

// ===================================================================================
// FOTO VERE dell'auto di Gala (la loro Range Rover Sport su AutoScout): ritaglio sulla pedana e interni
const photoShader = (tex, flip) => new THREE.ShaderMaterial({
  uniforms: { map: { value: tex }, uReveal: { value: 0 }, uSweep: { value: -1 }, uFlip: { value: flip ? 1 : 0 } },
  transparent: true, depthWrite: false, side: THREE.DoubleSide,
  vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
  fragmentShader: `uniform sampler2D map; uniform float uReveal, uSweep, uFlip; varying vec2 vUv;
    void main(){
      vec4 c = texture2D(map, vUv);
      float band = exp(-pow((vUv.x + vUv.y * 0.3 - uSweep) * 6.0, 2.0));
      vec3 col = c.rgb * (0.06 + 0.94 * uReveal) + vec3(0.92, 0.88, 1.0) * band * 0.5 * c.a;
      float a = c.a;
      if (uFlip > 0.5) { a *= 0.32 * (1.0 - smoothstep(0.0, 0.4, vUv.y)); col *= 0.55; }
      gl_FragColor = vec4(col, a);
      #include <colorspace_fragment>
    }`,
});

function photoCar(tex) {
  const group = new THREE.Group();
  const ar = tex.image.width / tex.image.height, h = Math.min(2.3, 5.4 / ar), w = h * ar;
  const geo = new THREE.PlaneGeometry(w, h);
  const m1 = photoShader(tex, false), m2 = photoShader(tex, true);
  const car = new THREE.Mesh(geo, m1); car.position.set(0, 0.06 + h / 2, 0); group.add(car);
  const refl = new THREE.Mesh(geo, m2); refl.scale.y = -1; refl.position.set(0, 0.06 - h / 2, 0.01); group.add(refl);
  const sh = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.95, 2.2), new THREE.MeshBasicMaterial({ map: radialTex(), transparent: true, depthWrite: false, opacity: 0.9 }));
  sh.rotation.x = -Math.PI / 2; sh.position.set(0, 0.065, 0.2); group.add(sh);
  return {
    group,
    update(t, intro) {
      const rv = smooth(0.15, 0.9, intro);
      m1.uniforms.uReveal.value = m2.uniforms.uReveal.value = rv;
      m1.uniforms.uSweep.value = m2.uniforms.uSweep.value = -0.4 + 2.0 * clamp(intro / 0.8) + (intro >= 1 ? ((t * 0.12) % 3) - 0.0 : 0);
    },
  };
}
function radialTex() {
  const c = document.createElement('canvas'); c.width = c.height = 256; const x = c.getContext('2d');
  const g = x.createRadialGradient(128, 128, 8, 128, 128, 128); g.addColorStop(0, 'rgba(0,0,0,.85)'); g.addColorStop(0.6, 'rgba(0,0,0,.35)'); g.addColorStop(1, 'rgba(0,0,0,0)');
  x.fillStyle = g; x.fillRect(0, 0, 256, 256); return new THREE.CanvasTexture(c);
}

// interni: la foto riempie lo schermo, la telecamera ci entra piano verso volante e plancia
const INT_FOCUS = [-0.38, 0.06];        // verso il volante (foto 10 di AutoScout)
function photoInterior(tex) {
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0x07060d);
  const ar = tex.image.width / tex.image.height;
  const mat = new THREE.ShaderMaterial({
    uniforms: { map: { value: tex }, uT: { value: 0 }, uU: { value: 0 } },
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: `uniform sampler2D map; uniform float uT, uU; varying vec2 vUv;
      void main(){
        vec3 c = texture2D(map, vUv).rgb;
        float leak = exp(-pow((vUv.x - 0.15 - uU * 0.7) * 3.0, 2.0)) * 0.12;
        c += vec3(0.75, 0.6, 1.0) * leak;
        float v = smoothstep(0.95, 0.35, distance(vUv, vec2(0.5)));
        gl_FragColor = vec4(c * (0.55 + 0.45 * v), 1.0);
        #include <colorspace_fragment>
      }`,
  });
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(ar * 2, 2), mat); scene.add(plane);
  const dust = new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(Array.from({ length: 600 }, () => (Math.random() - 0.5) * 3), 3)),
    new THREE.PointsMaterial({ size: 0.006, color: 0xffffff, transparent: true, opacity: 0.35, depthWrite: false }));
  dust.position.z = 0.6; scene.add(dust);
  return {
    scene,
    update(t, u, cam) {
      const f = Math.tan(cam.fov * Math.PI / 360);
      const d0 = Math.min(1 / f, ar / (f * cam.aspect)) * 0.97;     // la foto copre tutto lo schermo
      const k = ease(u), zoom = 1 + k * 0.9;
      const fx = INT_FOCUS[0] * ar * k, fy = INT_FOCUS[1] * k;
      cam.up.set(0, 1, 0);
      cam.position.set(fx + Math.sin(t * 0.3) * 0.01, fy + Math.cos(t * 0.25) * 0.008, d0 / zoom);
      cam.lookAt(fx, fy, 0);
      mat.uniforms.uU.value = u; dust.rotation.z = t * 0.02; dust.position.z = d0 / zoom - 0.4;
    },
  };
}

// ===================================================================================
// INTERNI di lusso: pelle avorio trapuntata, plancia in pelle scura, legno a poro aperto, alluminio, due schermi
function buildInterior(env, T) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xc9c6d4);
  scene.fog = new THREE.Fog(0xc9c6d4, 4, 18);
  scene.environment = env;
  const sun = new THREE.DirectionalLight(0xfff1de, 2.4); sun.position.set(1.5, 4, -3); sun.castShadow = true;
  sun.shadow.mapSize.set(MOBILE ? 1024 : 2048, MOBILE ? 1024 : 2048); Object.assign(sun.shadow.camera, { left: -2, right: 2, top: 2, bottom: -2 }); sun.shadow.bias = -0.0003; sun.shadow.normalBias = 0.01; sun.shadow.radius = 5;
  scene.add(sun);
  const roofL = new THREE.RectAreaLight(0xfff6ea, 3, 1.2, 1.6); roofL.position.set(0, 1.68, 0.1); roofL.lookAt(0, 0, 0.1); scene.add(roofL);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x3a3440, 0.35));

  const ivory = new THREE.MeshPhysicalMaterial({ color: 0xdccbb0, roughness: 0.62, normalMap: T.leatherN, normalScale: V2(0.18, 0.18), clearcoat: 0.15, clearcoatRoughness: 0.5 });
  const quilt = new THREE.MeshPhysicalMaterial({ color: 0xd3c0a2, roughness: 0.66, normalMap: T.quiltN, normalScale: V2(0.55, 0.55), clearcoat: 0.12, clearcoatRoughness: 0.5 });
  const ebony = new THREE.MeshPhysicalMaterial({ color: 0x24202a, roughness: 0.55, normalMap: T.leatherN, normalScale: V2(0.18, 0.18), clearcoat: 0.2, clearcoatRoughness: 0.45 });
  const woodM = new THREE.MeshPhysicalMaterial({ map: T.wood, roughness: 0.35, clearcoat: 0.8, clearcoatRoughness: 0.12 });
  const alu = new THREE.MeshPhysicalMaterial({ color: 0xc8c8cf, metalness: 1, roughness: 0.28, normalMap: T.brushedN, normalScale: V2(0.4, 0.4) });
  const piano = new THREE.MeshPhysicalMaterial({ color: 0x050507, metalness: 0.2, roughness: 0.06, clearcoat: 1 });
  const suede = new THREE.MeshStandardMaterial({ color: 0xcfc6bb, roughness: 1, normalMap: T.carpet, normalScale: V2(0.2, 0.2) });
  const carpetM = new THREE.MeshStandardMaterial({ color: 0x2a2630, roughness: 1, normalMap: T.carpet });
  const RB = (w, h, d, r, mtl, seg = 4) => { const me = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, seg, r), mtl); me.castShadow = me.receiveShadow = true; return me; };
  const add = (me, x, y, z, rx = 0, ry = 0, rz = 0, parent = scene) => { me.position.set(x, y, z); me.rotation.set(rx, ry, rz); parent.add(me); return me; };

  // abitacolo: pavimento, padiglione, tetto panoramico
  const floor = add(new THREE.Mesh(new THREE.PlaneGeometry(3, 3), carpetM), 0, 0.12, 0, -Math.PI / 2); floor.receiveShadow = true;
  add(RB(1.9, 0.04, 2.6, 0.02, suede), 0, 1.74, 0);
  const sky = new THREE.Mesh(new THREE.PlaneGeometry(1.2, 1.5), new THREE.MeshBasicMaterial({ map: skyTex(), toneMapped: false })); add(sky, 0, 1.755, 0.15, Math.PI / 2);
  add(new THREE.Mesh(new THREE.PlaneGeometry(1.25, 1.55), new THREE.MeshPhysicalMaterial({ color: 0x111118, roughness: 0.03, transparent: true, opacity: 0.35, metalness: 0.4 })), 0, 1.73, 0.15, Math.PI / 2);
  // plancia a strati: base scura, fascia di legno, profilo di alluminio, cappello in pelle
  add(RB(1.84, 0.34, 0.6, 0.06, ebony), 0, 0.72, -0.86);
  add(RB(1.86, 0.075, 0.05, 0.015, woodM), 0, 0.82, -0.555);
  add(RB(1.86, 0.012, 0.055, 0.005, alu), 0, 0.775, -0.553);
  add(RB(1.86, 0.1, 0.42, 0.05, ebony), 0, 0.93, -0.8);
  // bocchette sottili
  for (const [x, w] of [[-0.78, 0.18], [-0.12, 0.16], [0.12, 0.16], [0.78, 0.18]]) {
    for (let k = 0; k < 4; k++) add(new THREE.Mesh(new THREE.BoxGeometry(w, 0.004, 0.02), alu), x, 0.805 + k * 0.012, -0.528);
  }
  // console centrale alta con legno, selettore rotante, bracciolo
  add(RB(0.3, 0.42, 1.25, 0.05, ebony), 0, 0.4, -0.1);
  add(RB(0.28, 0.02, 0.6, 0.01, woodM), 0, 0.62, -0.3);
  const knob = add(new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.038, 0.04, 40), alu), 0, 0.65, -0.2);
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.002, 32), piano), 0, 0.671, -0.2);
  for (let k = 0; k < 4; k++) add(RB(0.04, 0.012, 0.02, 0.004, alu), -0.09 + k * 0.06, 0.635, -0.4);
  add(RB(0.3, 0.09, 0.42, 0.04, ivory), 0, 0.67, 0.28);
  // schermo centrale e quadro strumenti (vetro nero, grafica Gala)
  const nav = add(new THREE.Mesh(new THREE.PlaneGeometry(0.36, 0.2), new THREE.MeshBasicMaterial({ map: navTex(), toneMapped: false })), 0, 0.99, -0.585, -0.32);
  add(RB(0.38, 0.22, 0.012, 0.006, piano), 0, 0.99, -0.592, -0.32);
  const cl = clusterTex();
  add(new THREE.Mesh(new THREE.PlaneGeometry(0.34, 0.12), new THREE.MeshBasicMaterial({ map: cl.tex, toneMapped: false })), -0.38, 1.0, -0.66, -0.18);
  add(RB(0.38, 0.06, 0.12, 0.025, ebony), -0.38, 1.07, -0.68, -0.25);

  // volante in pelle a due razze con inserti di alluminio e logo
  const wheel = new THREE.Group(); add(wheel, -0.38, 0.93, -0.47, -0.36);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(0.185, 0.019, 20, 80), ebony); rim.castShadow = true; wheel.add(rim);
  for (const sx of [-1, 1]) { const sp = RB(0.13, 0.045, 0.03, 0.015, ebony); sp.position.set(sx * 0.11, -0.01, -0.01); wheel.add(sp); const ins = RB(0.1, 0.01, 0.031, 0.004, alu); ins.position.set(sx * 0.11, 0.01, -0.005); wheel.add(ins); }
  const low = RB(0.07, 0.07, 0.025, 0.012, alu); low.position.set(0, -0.13, -0.01); wheel.add(low);
  const pad = RB(0.13, 0.11, 0.05, 0.03, ebony); pad.position.set(0, -0.005, 0); wheel.add(pad);
  const logoTex = new THREE.TextureLoader().load('/logo.png'); logoTex.colorSpace = THREE.SRGBColorSpace;
  const emb = new THREE.Mesh(new THREE.PlaneGeometry(0.075, 0.043), new THREE.MeshPhysicalMaterial({ map: logoTex, transparent: true, metalness: 1, roughness: 0.25, color: 0xd8d8e0 })); emb.position.set(0, 0, 0.026); wheel.add(emb);
  for (const sx of [-1, 1]) { const pd = RB(0.05, 0.09, 0.008, 0.003, alu); pd.position.set(sx * 0.2, 0.03, -0.05); wheel.add(pd); }
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.05, 0.25, 20), ebony), -0.38, 0.86, -0.6, -0.36 + Math.PI / 2);

  // sedili con trapuntatura, fianchetti e poggiatesta
  const seat = (sx) => {
    const s = new THREE.Group(); s.position.set(sx, 0, 0.4); scene.add(s);
    add(RB(0.54, 0.16, 0.56, 0.07, ivory), 0, 0.4, 0, 0, 0, 0, s);
    add(RB(0.36, 0.035, 0.44, 0.015, quilt), 0, 0.485, -0.02, 0, 0, 0, s);
    const back = new THREE.Group(); back.position.set(0, 0.46, 0.27); back.rotation.x = -0.2; s.add(back);
    add(RB(0.54, 0.74, 0.15, 0.07, ivory), 0, 0.38, 0, 0, 0, 0, back);
    add(RB(0.36, 0.56, 0.03, 0.014, quilt), 0, 0.36, -0.08, 0, 0, 0, back);
    for (const bx of [-0.22, 0.22]) add(RB(0.1, 0.62, 0.2, 0.045, ivory), bx, 0.36, -0.04, 0, 0, 0, back);
    add(RB(0.28, 0.2, 0.13, 0.06, ivory), 0, 0.86, 0.02, 0, 0, 0, back);
    const piping = new THREE.Mesh(new THREE.BoxGeometry(0.004, 0.6, 0.004), ebony); add(piping, -0.18, 0.36, -0.096, 0, 0, 0, back); add(piping.clone(), 0.18, 0.36, -0.096, 0, 0, 0, back);
  };
  seat(-0.38); seat(0.38);
  // sedili posteriori
  add(RB(1.4, 0.18, 0.55, 0.07, ivory), 0, 0.42, 1.25); add(RB(1.4, 0.65, 0.16, 0.07, ivory), 0, 0.8, 1.52, -0.15);

  // pannelli porta: pelle, legno, griglia altoparlante in alluminio
  for (const sx of [-1, 1]) {
    add(RB(0.08, 0.62, 1.5, 0.03, ebony), sx * 0.96, 0.72, 0.05);
    add(RB(0.03, 0.06, 1.3, 0.012, woodM), sx * 0.915, 0.92, 0.05);
    add(RB(0.02, 0.012, 1.3, 0.005, alu), sx * 0.912, 0.885, 0.05);
    add(RB(0.06, 0.12, 0.9, 0.04, ivory), sx * 0.92, 0.72, 0.15);
    const spk = add(new THREE.Mesh(new THREE.CircleGeometry(0.075, 40), new THREE.MeshPhysicalMaterial({ color: 0xbcbcc4, metalness: 1, roughness: 0.35, map: dotsTex() })), sx * 0.919, 0.45, -0.25, 0, -sx * Math.PI / 2);
  }
  // montanti e parabrezza con vista sullo studio
  for (const sx of [-1, 1]) add(RB(0.08, 0.95, 0.08, 0.035, ebony), sx * 0.9, 1.33, -0.6, 0.62, 0, 0);
  add(new THREE.Mesh(new THREE.PlaneGeometry(1.8, 0.75), new THREE.MeshPhysicalMaterial({ color: 0xffffff, transmission: 0, transparent: true, opacity: 0.08, roughness: 0, metalness: 0 })), 0, 1.33, -0.62, 0.62);
  // fuori: lo studio luminoso con l'insegna
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(4, 0.9), new THREE.MeshBasicMaterial({ map: textPlate('GALA AUTO · RHO', 'rgba(0,0,0,0)', '#6b46e0', 1024, 220), transparent: true, toneMapped: false }));
  add(sign, 0, 2.2, -5.5);
  for (let k = -2; k <= 2; k++) add(new THREE.Mesh(new THREE.PlaneGeometry(1.2, 0.08), new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false })), k * 1.6, 3.4, -5, 0.4);
  // luce d'ambiente sottile (viola Gala) lungo plancia e porte
  const amb = new THREE.MeshBasicMaterial({ color: new THREE.Color(0x9b5cff).multiplyScalar(1.5), toneMapped: false });
  add(new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.003, 0.003), amb), 0, 0.768, -0.53);
  for (const sx of [-1, 1]) add(new THREE.Mesh(new THREE.BoxGeometry(0.003, 0.003, 1.3), amb), sx * 0.9, 0.876, 0.05);

  let fr = 0;
  return {
    scene,
    update(t, dt, u, rpm) {
      if (++fr % 3 === 0) cl.draw(t, rpm);
      knob.rotation.y = Math.sin(t * 0.6) * 0.3;
      amb.color.setRGB(0.61, 0.36, 1).multiplyScalar(1.2 + Math.sin(t * 1.5) * 0.2);
    },
  };
}

function clusterTex() {
  const c = document.createElement('canvas'); c.width = 1024; c.height = 360;
  const x = c.getContext('2d'); const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
  const dial = (cx, val, max, label, unit) => {
    x.lineWidth = 3; x.strokeStyle = 'rgba(255,255,255,.18)';
    x.beginPath(); x.arc(cx, 190, 140, Math.PI * 0.75, Math.PI * 2.25); x.stroke();
    for (let i = 0; i <= 10; i++) { const a = Math.PI * 0.75 + i / 10 * Math.PI * 1.5; x.beginPath(); x.moveTo(cx + Math.cos(a) * 128, 190 + Math.sin(a) * 128); x.lineTo(cx + Math.cos(a) * 140, 190 + Math.sin(a) * 140); x.stroke(); }
    const a = Math.PI * 0.75 + clamp(val / max) * Math.PI * 1.5;
    const gr = x.createLinearGradient(cx - 140, 0, cx + 140, 0); gr.addColorStop(0, '#5b6cff'); gr.addColorStop(1, '#c86bff');
    x.lineWidth = 6; x.strokeStyle = gr; x.beginPath(); x.arc(cx, 190, 140, Math.PI * 0.75, a); x.stroke();
    x.strokeStyle = '#fff'; x.lineWidth = 4; x.beginPath(); x.moveTo(cx, 190); x.lineTo(cx + Math.cos(a) * 120, 190 + Math.sin(a) * 120); x.stroke();
    x.fillStyle = '#fff'; x.textAlign = 'center'; x.font = '300 64px Barlow, sans-serif'; x.fillText(label, cx, 210);
    x.font = '500 20px Barlow, sans-serif'; x.fillStyle = 'rgba(255,255,255,.55)'; x.fillText(unit, cx, 245);
  };
  return {
    tex,
    draw(t = 0, rpm = 1) {
      x.fillStyle = '#060509'; x.fillRect(0, 0, 1024, 360);
      const sp = Math.round(48 + Math.sin(t * 0.3) * 6 + rpm * 6), r = 1.1 + rpm * 0.35 + Math.sin(t * 2) * 0.05;
      dial(190, sp, 260, String(sp), 'km/h'); dial(834, r, 8, r.toFixed(1), 'x1000 giri/min');
      x.textAlign = 'center'; x.fillStyle = '#fff'; x.font = 'italic 700 46px "Barlow Condensed", sans-serif'; x.fillText('GALA AUTO', 512, 120);
      x.font = '500 22px Barlow, sans-serif'; x.fillStyle = 'rgba(255,255,255,.6)'; x.fillText('D  ·  Comfort  ·  21°C', 512, 170);
      x.fillStyle = '#c86bff'; x.font = '600 24px Barlow, sans-serif'; x.fillText('Via Toti 12 · Rho', 512, 300);
      tex.needsUpdate = true;
    },
  };
}
function navTex() {
  const c = document.createElement('canvas'); c.width = 720; c.height = 400; const x = c.getContext('2d');
  const g = x.createLinearGradient(0, 0, 720, 400); g.addColorStop(0, '#15122a'); g.addColorStop(1, '#07060d'); x.fillStyle = g; x.fillRect(0, 0, 720, 400);
  x.strokeStyle = 'rgba(255,255,255,.08)'; x.lineWidth = 1; for (let i = 0; i < 18; i++) { x.beginPath(); x.moveTo(0, i * 24); x.lineTo(470, i * 24 + 40); x.stroke(); x.beginPath(); x.moveTo(i * 30, 0); x.lineTo(i * 30 - 60, 400); x.stroke(); }
  x.strokeStyle = '#9b5cff'; x.lineWidth = 7; x.beginPath(); x.moveTo(60, 360); x.bezierCurveTo(160, 240, 300, 300, 380, 110); x.stroke();
  x.fillStyle = '#fff'; x.beginPath(); x.arc(380, 110, 10, 0, 7); x.fill();
  x.fillStyle = 'rgba(255,255,255,.06)'; x.fillRect(480, 0, 240, 400);
  x.fillStyle = '#fff'; x.font = '600 26px Barlow, sans-serif'; x.fillText('Gala Auto', 500, 60);
  x.font = '400 20px Barlow, sans-serif'; x.fillStyle = 'rgba(255,255,255,.65)'; x.fillText('Via Toti 12, Rho', 500, 92); x.fillText('Arrivo tra 6 min', 500, 122);
  x.font = '300 64px Barlow, sans-serif'; x.fillStyle = '#fff'; x.fillText('21°', 500, 240); x.font = '400 18px Barlow, sans-serif'; x.fillStyle = 'rgba(255,255,255,.6)'; x.fillText('Clima auto', 504, 270);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
function skyTex() {
  const c = document.createElement('canvas'); c.width = 256; c.height = 256; const x = c.getContext('2d');
  const g = x.createLinearGradient(0, 0, 0, 256); g.addColorStop(0, '#9fb3e8'); g.addColorStop(0.6, '#d6c9f0'); g.addColorStop(1, '#f6e6da'); x.fillStyle = g; x.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
function dotsTex() {
  const c = document.createElement('canvas'); c.width = c.height = 256; const x = c.getContext('2d');
  x.fillStyle = '#cfcfd6'; x.fillRect(0, 0, 256, 256); x.fillStyle = '#2a2a30';
  for (let i = 0; i < 26; i++) for (let j = 0; j < 26; j++) { x.beginPath(); x.arc(i * 10 + (j % 2) * 5, j * 10, 2.4, 0, 7); x.fill(); }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
function textPlate(txt, bg, fg, w, h) {
  const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d');
  const draw = () => {
    x.clearRect(0, 0, w, h); if (bg !== 'rgba(0,0,0,0)') { x.fillStyle = bg; x.fillRect(0, 0, w, h); }
    x.fillStyle = fg; x.textAlign = 'center'; x.textBaseline = 'middle'; let fs = Math.round(h * 0.62); x.font = `italic 800 ${fs}px "Barlow Condensed", sans-serif`; const mw = x.measureText(txt).width; if (mw > w * 0.92) { fs = Math.floor(fs * w * 0.92 / mw); x.font = `italic 800 ${fs}px "Barlow Condensed", sans-serif`; } x.fillText(txt, w / 2, h / 2 + 2); t.needsUpdate = true;
  };
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; draw(); document.fonts && document.fonts.ready.then(draw); return t;
}

// ===================================================================================
// VANO MOTORE a cofano aperto: V8 con testate in fusione, bobine, collettore, cinghia che gira
function buildEngineBay(env, T) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b0a10);
  scene.fog = new THREE.Fog(0x0b0a10, 5, 16);
  scene.environment = env;
  const key = new THREE.SpotLight(0xfff3e6, 60, 12, 0.6, 0.6, 1.6); key.position.set(0.8, 3.6, 1.6); key.target.position.set(0, 0.7, -0.1); key.castShadow = true;
  key.shadow.mapSize.set(MOBILE ? 1024 : 2048, MOBILE ? 1024 : 2048); key.shadow.bias = -0.0003; key.shadow.normalBias = 0.01; scene.add(key, key.target);
  const fill = new THREE.RectAreaLight(0xc9bfff, 2.5, 2.4, 0.8); fill.position.set(0, 2.2, -0.9); fill.lookAt(0, 0.6, 0); scene.add(fill);
  scene.add(new THREE.HemisphereLight(0xe6e2ff, 0x0b0a10, 0.35));

  const paint = new THREE.MeshPhysicalMaterial({ color: 0x46424f, metalness: 0.8, roughness: 0.28, clearcoat: 1, iridescence: 0.3 });
  const blackP = new THREE.MeshStandardMaterial({ color: 0x141317, roughness: 0.7, normalMap: T.plasticN, normalScale: V2(0.5, 0.5) });
  const castAl = new THREE.MeshPhysicalMaterial({ color: 0x8e8d93, metalness: 0.85, roughness: 0.52, normalMap: T.castN, normalScale: V2(0.3, 0.3) });
  const polAl = new THREE.MeshPhysicalMaterial({ color: 0xd6d6dc, metalness: 1, roughness: 0.15 });
  const rubber = new THREE.MeshStandardMaterial({ color: 0x0f0f11, roughness: 0.9 });
  const steel = new THREE.MeshPhysicalMaterial({ color: 0x7c7c84, metalness: 1, roughness: 0.35, normalMap: T.brushedN });
  const RB = (w, h, d, r, mtl) => { const me = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 3, r), mtl); me.castShadow = me.receiveShadow = true; return me; };
  const add = (me, x, y, z, rx = 0, ry = 0, rz = 0, parent = scene) => { me.position.set(x, y, z); me.rotation.set(rx, ry, rz); me.castShadow = me.receiveShadow = true; parent.add(me); return me; };
  const tube = (pts, r, mtl, seg = 48) => { const me = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), seg, r, 12), mtl); me.castShadow = me.receiveShadow = true; scene.add(me); return me; };

  // vano: parafanghi interni, duomi, traversa, cofano aperto
  add(new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.MeshStandardMaterial({ color: 0x08080a, roughness: 1 })), 0, 0, 0, -Math.PI / 2);
  for (const sx of [-1, 1]) {
    add(RB(0.08, 0.5, 1.8, 0.03, paint), sx * 0.95, 0.85, -0.1);                 // longherone/parafango
    add(RB(0.35, 0.2, 1.7, 0.05, blackP), sx * 0.78, 0.62, -0.1);               // passaruota interno
    const dome = add(new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.17, 0.22, 32), paint), sx * 0.72, 0.85, -0.55);
    add(new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.03, 20), steel), sx * 0.72, 0.975, -0.55);
  }
  add(RB(1.9, 0.12, 0.1, 0.03, paint), 0, 1.02, -0.98);                          // paratia
  add(RB(1.95, 0.08, 0.12, 0.03, blackP), 0, 0.98, 0.82);                        // traversa anteriore
  const hood = new THREE.Group(); hood.position.set(0, 1.08, -1.0); hood.rotation.x = -1.15; scene.add(hood);
  add(RB(1.9, 0.04, 1.75, 0.04, paint), 0, 0.02, 0.88, 0, 0, 0, hood);
  add(RB(1.6, 0.02, 1.45, 0.02, new THREE.MeshStandardMaterial({ color: 0x222026, roughness: 1, normalMap: T.carpet })), 0, -0.012, 0.88, 0, 0, 0, hood);
  const strut = tube([V(0.82, 0.95, -0.7), V(0.83, 1.4, -0.95)], 0.012, polAl, 4);

  // radiatore con ventola
  add(RB(1.45, 0.5, 0.06, 0.02, blackP), 0, 0.72, 0.72);
  const fan = new THREE.Group(); add(fan, 0, 0.72, 0.66);
  for (let k = 0; k < 7; k++) { const bl = RB(0.2, 0.06, 0.008, 0.004, blackP); bl.position.set(Math.cos(k / 7 * 6.283) * 0.11, Math.sin(k / 7 * 6.283) * 0.11, 0); bl.rotation.z = k / 7 * 6.283; bl.rotation.x = 0.35; fan.add(bl); }

  // ===== motore V8 =====
  const eng = new THREE.Group(); scene.add(eng);
  const head = textCast('GALA');
  for (const sx of [-1, 1]) {
    // testata con coperchio punterie in fusione nervato e scritta
    const bank = new THREE.Group(); bank.position.set(sx * 0.3, 0.86, -0.12); bank.rotation.z = sx * -0.5; eng.add(bank);
    add(RB(0.26, 0.12, 0.88, 0.05, castAl), 0, 0, 0, 0, 0, 0, bank);
    for (let k = 0; k < 6; k++) add(RB(0.012, 0.03, 0.8, 0.006, castAl), -0.1 + k * 0.04, 0.065, 0, 0, 0, 0, bank);
    const plate = new THREE.Mesh(new THREE.PlaneGeometry(0.2, 0.06), new THREE.MeshPhysicalMaterial({ map: head.map, normalMap: head.normal, metalness: 0.9, roughness: 0.35, color: 0xdcdce2 }));
    add(plate, 0, 0.0632, 0.28, -Math.PI / 2, 0, Math.PI / 2 * (sx > 0 ? 1 : -1) * 0 + Math.PI / 2, bank);
    // bobine e connettori
    for (let k = 0; k < 4; k++) {
      add(RB(0.07, 0.07, 0.06, 0.012, blackP), sx * -0.07, 0.09, -0.3 + k * 0.17, 0, 0, 0, bank);
      add(RB(0.03, 0.03, 0.035, 0.006, new THREE.MeshStandardMaterial({ color: 0x3a2a6a, roughness: 0.5 })), sx * -0.07, 0.135, -0.3 + k * 0.17, 0, 0, 0, bank);
    }
    // cablaggio lungo le bobine
    tube([V(sx * 0.18, 0.95, -0.48), V(sx * 0.22, 1.0, -0.2), V(sx * 0.22, 1.0, 0.1), V(sx * 0.18, 0.97, 0.3)], 0.012, rubber);
    // collettori di scarico (acciaio) ai lati
    for (let k = 0; k < 4; k++) tube([V(sx * 0.43, 0.78, -0.42 + k * 0.2), V(sx * 0.56, 0.7, -0.4 + k * 0.2), V(sx * 0.6, 0.5, -0.3 + k * 0.12), V(sx * 0.55, 0.3, -0.2)], 0.025, steel, 24);
  }
  // collettore d'aspirazione al centro con 8 cornetti
  add(RB(0.3, 0.12, 0.7, 0.05, castAl), 0, 0.98, -0.12, 0, 0, 0, eng);
  for (let k = 0; k < 4; k++) for (const sx of [-1, 1]) {
    const z = -0.38 + k * 0.17;
    eng.add(Object.assign(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([V(sx * 0.12, 0.99, z), V(sx * 0.2, 1.04, z), V(sx * 0.24, 0.96, z)]), 16, 0.026, 10), polAl), { castShadow: true }));
  }
  // corpo farfallato e manicotto verso la scatola filtro
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.09, 32), polAl), 0, 0.98, 0.26, Math.PI / 2, 0, 0, eng);
  const duct = [];
  for (let k = 0; k <= 30; k++) { const tt = k / 30; duct.push(V(0.0 + tt * 0.5, 0.98 + Math.sin(tt * 3) * 0.06, 0.31 + tt * 0.25)); }
  const ductG = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(duct), 120, 0.07, 18);
  add(new THREE.Mesh(ductG, rubber), 0, 0, 0);
  add(RB(0.36, 0.2, 0.42, 0.05, blackP), 0.6, 0.98, 0.55);
  const abox = new THREE.Mesh(new THREE.PlaneGeometry(0.22, 0.07), new THREE.MeshBasicMaterial({ map: textPlate('GALA AUTO', 'rgba(0,0,0,0)', '#d8d8e0', 512, 160), transparent: true }));
  add(abox, 0.6, 1.081, 0.55, -Math.PI / 2);
  // distribuzione davanti: pulegge e cinghia che girano
  add(RB(0.6, 0.38, 0.06, 0.04, castAl), 0, 0.68, 0.36, 0, 0, 0, eng);
  const pulleys = [[0, 0.5, 0.11], [0.2, 0.72, 0.07], [-0.2, 0.74, 0.07], [0.22, 0.95, 0.06], [-0.24, 0.93, 0.065], [0.0, 0.8, 0.05]];
  const pg = [];
  for (const [px, py, pr] of pulleys) {
    const pu = new THREE.Group(); pu.position.set(px, py, 0.41); eng.add(pu);
    const disc = new THREE.Mesh(new THREE.CylinderGeometry(pr, pr, 0.04, 40), steel); disc.rotation.x = Math.PI / 2; disc.castShadow = true; pu.add(disc);
    for (let k = 0; k < 4; k++) { const hole = new THREE.Mesh(new THREE.CylinderGeometry(pr * 0.2, pr * 0.2, 0.042, 12), new THREE.MeshBasicMaterial({ color: 0x0a0a0c })); hole.rotation.x = Math.PI / 2; hole.position.set(Math.cos(k * 1.57) * pr * 0.55, Math.sin(k * 1.57) * pr * 0.55, 0); pu.add(hole); }
    pg.push([pu, pr]);
  }
  // cinghia: un anello che passa attorno alle pulegge (inviluppo approssimato)
  const beltPts = [];
  const hull = pulleys.map(([x, y, r]) => [x, y, r]);
  const cx = hull.reduce((s, h) => s + h[0], 0) / hull.length, cy = hull.reduce((s, h) => s + h[1], 0) / hull.length;
  for (let k = 0; k < 120; k++) {
    const an = k / 120 * Math.PI * 2, dir = V2(Math.cos(an), Math.sin(an));
    let best = 0; for (const [x, y, r] of hull) best = Math.max(best, (x - cx) * dir.x + (y - cy) * dir.y + r + 0.006);
    beltPts.push(V(cx + dir.x * best, cy + dir.y * best, 0.41));
  }
  const belt = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(beltPts, true), 240, 0.012, 6, true), rubber); belt.scale.set(1, 1, 1); eng.add(belt);
  // alternatore
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, 0.14, 32), castAl), 0.2, 0.72, 0.32, Math.PI / 2, 0, 0, eng);
  // tappo olio, vaschette, batteria
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.025, 24), new THREE.MeshStandardMaterial({ color: 0xe8c22a, roughness: 0.4 })), -0.3, 0.98, 0.12, 0, 0, 0.5);
  const res = add(RB(0.18, 0.16, 0.14, 0.04, new THREE.MeshPhysicalMaterial({ color: 0xf2eee8, roughness: 0.4, transmission: 0, transparent: true, opacity: 0.85 })), -0.7, 0.98, 0.35);
  add(RB(0.16, 0.06, 0.12, 0.02, new THREE.MeshStandardMaterial({ color: 0xd84a8a, roughness: 0.3, transparent: true, opacity: 0.8 })), -0.7, 0.93, 0.35);
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.03, 24), new THREE.MeshStandardMaterial({ color: 0x1a1a1d, roughness: 0.5 })), -0.7, 1.075, 0.35);
  add(RB(0.3, 0.2, 0.18, 0.02, blackP), -0.62, 0.92, -0.7);
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.03, 16), new THREE.MeshStandardMaterial({ color: 0xc8231e })), -0.53, 1.035, -0.7);
  // tubi del liquido di raffreddamento
  tube([V(-0.2, 0.78, 0.4), V(-0.35, 0.7, 0.55), V(-0.45, 0.72, 0.7)], 0.032, rubber);
  tube([V(0.25, 0.6, 0.4), V(0.4, 0.55, 0.55), V(0.5, 0.6, 0.7)], 0.03, rubber);
  tube([V(-0.62, 0.95, 0.3), V(-0.5, 0.92, 0.1), V(-0.3, 0.95, -0.2)], 0.012, rubber);

  return {
    scene,
    update(t, dt, u, rpm) {
      const w = 6 + rpm * 4;
      pg.forEach(([pu, pr]) => { pu.rotation.z -= dt * w * (0.11 / pr); });
      fan.rotation.z -= dt * w * 0.9;
      eng.position.x = Math.sin(t * 60) * 0.0012; eng.position.y = Math.cos(t * 47) * 0.001;
      hood.rotation.x = -1.15 - Math.sin(t * 0.5) * 0.01;
    },
  };
}

function textCast(txt) {
  const w = 512, h = 160;
  const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d');
  x.fillStyle = '#b8b8be'; x.fillRect(0, 0, w, h);
  x.fillStyle = '#e8e8ee'; x.textAlign = 'center'; x.textBaseline = 'middle'; x.font = 'italic 800 120px "Barlow Condensed", sans-serif'; x.fillText(txt, w / 2, h / 2 + 4);
  const map = new THREE.CanvasTexture(c); map.colorSpace = THREE.SRGBColorSpace;
  const hc = document.createElement('canvas'); hc.width = w; hc.height = h; const y = hc.getContext('2d');
  y.fillStyle = '#000'; y.fillRect(0, 0, w, h); y.fillStyle = '#fff'; y.textAlign = 'center'; y.textBaseline = 'middle'; y.font = x.font; y.filter = 'blur(2px)'; y.fillText(txt, w / 2, h / 2 + 4);
  const d = y.getImageData(0, 0, w, h).data, nc = document.createElement('canvas'); nc.width = w; nc.height = h; const nx = nc.getContext('2d'); const img = nx.createImageData(w, h);
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    const g = (ii, jj) => d[((Math.min(h - 1, Math.max(0, jj))) * w + Math.min(w - 1, Math.max(0, ii))) * 4] / 255;
    const dx = g(i + 1, j) - g(i - 1, j), dy = g(i, j + 1) - g(i, j - 1);
    let a = -dx * 4, b = -dy * 4, cz = 1; const l = Math.hypot(a, b, cz); const k = (j * w + i) * 4;
    img.data[k] = (a / l * .5 + .5) * 255; img.data[k + 1] = (b / l * .5 + .5) * 255; img.data[k + 2] = (cz / l * .5 + .5) * 255; img.data[k + 3] = 255;
  }
  nx.putImageData(img, 0, 0);
  const draw = () => { x.fillStyle = '#b8b8be'; x.fillRect(0, 0, w, h); x.fillStyle = '#e8e8ee'; x.fillText(txt, w / 2, h / 2 + 4); map.needsUpdate = true; };
  document.fonts && document.fonts.ready.then(draw);
  return { map, normal: new THREE.CanvasTexture(nc) };
}

// suddivide i triangoli lunghi decidendo per lato (niente giunzioni a T: superficie liscia)
function refine(geo, maxLen) {
  const pos = Array.from(geo.attributes.position.array);
  let idx = Array.from(geo.index.array);
  const L2 = maxLen * maxLen, K = 2097152;
  const len2 = (a, b) => { const dx = pos[a * 3] - pos[b * 3], dy = pos[a * 3 + 1] - pos[b * 3 + 1], dz = pos[a * 3 + 2] - pos[b * 3 + 2]; return dx * dx + dy * dy + dz * dz; };
  for (let it = 0; it < 16; it++) {
    const mids = new Map(); let any = false; const out = [];
    const mid = (a, b) => {
      const k = a < b ? a * K + b : b * K + a; let v = mids.get(k);
      if (v === undefined) { v = pos.length / 3; pos.push((pos[a * 3] + pos[b * 3]) / 2, (pos[a * 3 + 1] + pos[b * 3 + 1]) / 2, (pos[a * 3 + 2] + pos[b * 3 + 2]) / 2); mids.set(k, v); }
      return v;
    };
    for (let f = 0; f < idx.length; f += 3) {
      const a = idx[f], b = idx[f + 1], c = idx[f + 2];
      const sab = len2(a, b) > L2, sbc = len2(b, c) > L2, sca = len2(c, a) > L2, n = sab + sbc + sca;
      if (!n) { out.push(a, b, c); continue; }
      any = true;
      if (n === 3) { const m1 = mid(a, b), m2 = mid(b, c), m3 = mid(c, a); out.push(a, m1, m3, m1, b, m2, m3, m2, c, m1, m2, m3); }
      else if (n === 1) {
        if (sab) { const m = mid(a, b); out.push(a, m, c, m, b, c); }
        else if (sbc) { const m = mid(b, c); out.push(a, b, m, a, m, c); }
        else { const m = mid(c, a); out.push(a, b, m, m, b, c); }
      } else {
        let A = a, B = b, C = c; if (!sab) { A = b; B = c; C = a; } else if (!sbc) { A = c; B = a; C = b; }
        const m1 = mid(A, B), m2 = mid(B, C); out.push(m1, B, m2, A, m1, m2, A, m2, C);
      }
    }
    idx = out; if (!any) break;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx.length > 65535 ? new THREE.Uint32BufferAttribute(idx, 1) : new THREE.Uint16BufferAttribute(idx, 1));
  return g;
}
