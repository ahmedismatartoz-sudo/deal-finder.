// Gala Auto — concessionaria 3D di notte: si arriva dal piazzale, si aprono le porte, dentro lo showroom con le auto.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { RectAreaLightUniformsLib } from 'three/addons/lights/RectAreaLightUniformsLib.js';
import { FontLoader } from 'three/addons/loaders/FontLoader.js';
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js';

const FORCE = new URLSearchParams(location.search).get('p');
const MOBILE = matchMedia('(max-width: 820px)').matches || /Android|iPhone|iPad/i.test(navigator.userAgent);
const TS = MOBILE ? 256 : 512;
const FONT_URL = (document.querySelector('script[type=importmap]')?.textContent.match(/"three":\s*"([^"]+)build\/three\.module\.js"/) || [])[1];
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
  RectAreaLightUniformsLib.init();
  const T = makeTextures();
  const env = studioEnv(renderer, false);
  const W = innerWidth, H = innerHeight;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x070611);
  scene.fog = new THREE.FogExp2(0x0b0a1a, 0.022);
  scene.environment = env;
  scene.environmentIntensity = 0.55;
  const camera = new THREE.PerspectiveCamera(40, 1, 0.05, 160);

  const world = buildDealership(scene, T, W, H);

  const composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(W, H, { type: THREE.HalfFloatType, samples: MOBILE ? 0 : 4 }));
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(W, H), MOBILE ? 0.5 : 0.65, 0.5, 0.85);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  // un'unica ripresa: piazzale -> ingresso -> porte -> showroom -> galleria delle auto
  const camPath = new THREE.CatmullRomCurve3([
    V(15, 4.2, 28), V(8, 2.8, 16), V(1.2, 1.8, 8.5), V(0, 1.75, 4.2), V(0, 1.75, 1.2),
    V(0, 1.8, -3.5), V(0, 1.9, -7.5), V(0, 2.3, -12.5)], false, 'catmullrom', 0.5);
  const lookPath = new THREE.CatmullRomCurve3([
    V(0, 4.6, 0), V(0, 3.8, 0), V(0, 2.4, 0), V(0, 1.8, -2), V(0, 1.7, -6),
    V(-6, 1.0, -7.5), V(6, 1.0, -8), V(0, 3.2, -21.8)], false, 'catmullrom', 0.5);

  let target = 0, cur = 0, introStart = null, visible = true, aspect = 1, lastT = 0;
  const copies = [...document.querySelectorAll('.copy')];
  const fade = document.getElementById('fade'), flash = document.getElementById('flash'), prog = document.getElementById('prog'), hint = document.getElementById('hint');
  const nav = document.getElementById('nav'), fab = document.querySelector('.fab');
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
    camera.aspect = aspect; camera.fov = aspect < 0.8 ? 62 : 40; camera.updateProjectionMatrix();
  }
  addEventListener('resize', resize); resize(); onScroll();
  new IntersectionObserver(e => { visible = e[0].isIntersecting; }, { threshold: 0 }).observe(show);

  // clic sugli schermi della galleria: apre la scheda dell'auto
  const ray = new THREE.Raycaster(), ptr = V2(0, 0);
  const pick = e => { ptr.set(e.clientX / innerWidth * 2 - 1, -(e.clientY / innerHeight) * 2 + 1); ray.setFromCamera(ptr, camera); return ray.intersectObjects(world.screens, false)[0]; };
  canvas.addEventListener('click', e => { const h = pick(e); if (h && window.apriAuto) window.apriAuto(h.object.userData.id); });
  canvas.addEventListener('pointermove', e => { canvas.style.cursor = cur > 0.55 && pick(e) ? 'pointer' : ''; });

  const pos = V(0, 0, 0), look = V(0, 0, 0);
  const clock = new THREE.Clock();
  let ready = false;
  function frame() {
    requestAnimationFrame(frame);
    if (!visible || document.hidden) return;
    const t = clock.getElapsedTime(), dt = Math.min(0.1, t - lastT); lastT = t;
    if (introStart === null) introStart = t;
    const intro = FORCE !== null ? 1 : clamp((t - introStart) / 3.2);
    cur = FORCE !== null ? +FORCE : cur + (target - cur) * 0.07;
    const p = cur, u = clamp(p / 0.93) * 0.999;
    camPath.getPoint(ease(u * 0.5 + 0.5 * u) * 0.999, pos); lookPath.getPoint(ease(u * 0.5 + 0.5 * u) * 0.999, look);
    // all'inizio la telecamera si muove appena, come una ripresa con il drone
    const drift = (1 - smooth(0, 0.05, p));
    pos.x += Math.sin(t * 0.15) * 1.2 * drift; pos.y += Math.sin(t * 0.21) * 0.25 * drift + (1 - ease(intro)) * 3;
    if (aspect < 0.8) pos.z += 6 * (1 - smooth(0.0, 0.25, p)) + 5.5 * smooth(0.68, 0.93, p);
    camera.position.copy(pos); camera.lookAt(look);
    world.update(t, dt, p, intro, camera);
    bloom.strength = (MOBILE ? 0.5 : 0.65) + (1 - smooth(0.2, 0.4, p)) * 0.25;
    if (location.search.includes('nobloom')) bloom.strength = 0;

    for (const c of copies) {
      const a = +c.dataset.from, b = +c.dataset.to;
      c.classList.toggle('on', p >= a && p < b && (c.classList.contains('c0') ? intro > 0.7 : true));
    }
    flash.style.opacity = 0;
    fade.style.opacity = smooth(0.94, 1, p) + (1 - smooth(0, 0.6, intro)) * 0.9;
    prog.style.transform = `scaleY(${p})`;
    hint.style.opacity = p > 0.01 || intro < 0.9 ? 0 : 1;
    composer.render();
    if (!ready) { ready = true; window.dispatchEvent(new Event('car-ready')); }
  }
  frame();
}

// ===================================================================================
// LA CONCESSIONARIA
function buildDealership(scene, T, W, H) {
  const add = (m, x, y, z, rx = 0, ry = 0, rz = 0, parent = scene) => { m.position.set(x, y, z); m.rotation.set(rx, ry, rz); parent.add(m); return m; };
  const box = (w, h, d, mat) => new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  const RB = (w, h, d, r, mat) => new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 3, r), mat);
  const emis = (c, k) => new THREE.MeshBasicMaterial({ color: new THREE.Color(c).multiplyScalar(k), toneMapped: false });

  // materiali
  const stone = new THREE.MeshPhysicalMaterial({ color: 0x24222a, roughness: 0.7, normalMap: T.castN, normalScale: V2(0.25, 0.25) });
  const bronze = new THREE.MeshPhysicalMaterial({ color: 0x2b2420, metalness: 1, roughness: 0.32 });
  const darkMetal = new THREE.MeshPhysicalMaterial({ color: 0x121216, metalness: 0.9, roughness: 0.35 });
  const glassOut = new THREE.MeshPhysicalMaterial({ color: 0x9aa4c8, metalness: 0.2, roughness: 0.04, transparent: true, opacity: 0.16, envMapIntensity: 1.6, side: THREE.DoubleSide, depthWrite: false });
  const wallIn = new THREE.MeshStandardMaterial({ color: 0xb8b2a8, roughness: 0.85 });
  const ceilIn = new THREE.MeshStandardMaterial({ color: 0xece8e2, roughness: 0.9 });
  const featureWall = new THREE.MeshPhysicalMaterial({ color: 0x18151f, roughness: 0.45, normalMap: T.castN, normalScale: V2(0.15, 0.15) });

  // cielo notturno
  const sky = new THREE.Mesh(new THREE.SphereGeometry(120, 32, 16), new THREE.MeshBasicMaterial({ map: skyNight(), side: THREE.BackSide, fog: false, depthWrite: false }));
  scene.add(sky);
  const stars = new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(Array.from({ length: 1800 }, (_, i) => {
    const th = Math.random() * Math.PI * 2, ph = Math.random() * 0.45 + 0.05; const r = 110;
    return i % 3 === 0 ? Math.cos(th) * Math.cos(ph) * r : i % 3 === 1 ? Math.sin(ph) * r : Math.sin(th) * Math.cos(ph) * r;
  }), 3)), new THREE.PointsMaterial({ size: 0.35, color: 0xbfc4ff, transparent: true, opacity: 0.7, fog: false }));
  scene.add(stars);
  // città lontana: luci sfocate all'orizzonte
  const city = new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(Array.from({ length: 900 }, (_, i) => {
    const th = Math.random() * Math.PI * 2, r = 70 + Math.random() * 30; return i % 3 === 0 ? Math.cos(th) * r : i % 3 === 1 ? Math.random() * 4 : Math.sin(th) * r;
  }), 3)), new THREE.PointsMaterial({ size: 0.18, color: 0xffd6a0, transparent: true, opacity: 0.55, fog: false }));
  scene.add(city);

  // piazzale bagnato a specchio
  const plazaRef = new Reflector(new THREE.PlaneGeometry(80, 60), { textureWidth: W * (MOBILE ? 0.4 : 0.6), textureHeight: H * (MOBILE ? 0.4 : 0.6), color: 0x8a8a96, clipBias: 0.003 });
  plazaRef.rotation.x = -Math.PI / 2; plazaRef.position.set(0, 0, 20); scene.add(plazaRef);
  const plaza = new THREE.Mesh(new THREE.PlaneGeometry(80, 60), new THREE.MeshPhysicalMaterial({ color: 0x0c0c12, roughness: 0.55, metalness: 0.1, transparent: true, opacity: 0.86, normalMap: T.castN, normalScale: V2(0.4, 0.4) }));
  plaza.rotation.x = -Math.PI / 2; plaza.position.set(0, 0.005, 20); scene.add(plaza);
  // lastre del vialetto d'ingresso
  for (let k = 0; k < 9; k++) add(box(3.2, 0.03, 1.6, stone), 0, 0.02, 2.2 + k * 2.0);
  // bordure luminose e paletti
  for (const sx of [-1, 1]) {
    add(box(0.06, 0.02, 18, emis(0xb05cff, 1.4)), sx * 1.9, 0.04, 11);
    for (let k = 0; k < 6; k++) {
      const bz = 3 + k * 3.2;
      add(new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.8, 16), darkMetal), sx * 2.6, 0.4, bz);
      add(new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.08, 16), emis(0xfff1dc, 3)), sx * 2.6, 0.76, bz);
    }
    // aiuole con siepi
    add(RB(5, 0.5, 1.6, 0.08, stone), sx * 7, 0.25, 1.8);
    for (let k = 0; k < 5; k++) add(new THREE.Mesh(new THREE.SphereGeometry(0.42, 16, 12), new THREE.MeshStandardMaterial({ color: 0x1b2a1e, roughness: 1 })), sx * (5.2 + k * 0.9), 0.75, 1.8);
  }

  // ===== edificio =====
  const BW = 30, BD = 22, BH = 7;               // larghezza, profondità, altezza; facciata a z = 0
  add(box(BW + 1, 0.3, BD + 1, stone), 0, 0.15, -BD / 2);                       // basamento
  // pavimento interno a specchio
  const floorRef = new Reflector(new THREE.PlaneGeometry(BW - 0.4, BD - 0.4), { textureWidth: W * (MOBILE ? 0.4 : 0.6), textureHeight: H * (MOBILE ? 0.4 : 0.6), color: 0xb0aeb8, clipBias: 0.003 });
  floorRef.rotation.x = -Math.PI / 2; floorRef.position.set(0, 0.302, -BD / 2); scene.add(floorRef);
  const floorTop = new THREE.Mesh(new THREE.PlaneGeometry(BW - 0.4, BD - 0.4), new THREE.MeshPhysicalMaterial({ color: 0xbdb8b0, roughness: 0.3, transparent: true, opacity: 0.78, clearcoat: 1 }));
  floorTop.rotation.x = -Math.PI / 2; floorTop.position.set(0, 0.305, -BD / 2); scene.add(floorTop);
  // muri
  add(box(BW, BH, 0.4, featureWall), 0, 0.3 + BH / 2, -BD);                      // parete di fondo
  for (const sx of [-1, 1]) {
    add(box(0.4, BH, BD, wallIn), sx * BW / 2, 0.3 + BH / 2, -BD / 2);
    add(box(1.2, BH + 1.6, BD + 1, stone), sx * (BW / 2 + 0.6), (BH + 1.6) / 2, -BD / 2 + 0.5);   // pilastri esterni in pietra
  }
  // tetto con pensilina sporgente sull'ingresso
  const ROOF_Y = 0.3 + BH;
  add(box(BW + 2.4, 0.9, BD + 4.2, new THREE.MeshStandardMaterial({ color: 0x141318, roughness: 0.7, metalness: 0.3 })), 0, ROOF_Y + 0.45, -BD / 2 + 2.1);
  add(box(BW - 0.4, 0.05, BD - 0.4, ceilIn), 0, ROOF_Y - 0.02, -BD / 2);
  // led sotto la pensilina e faretti
  add(box(BW + 2, 0.03, 0.05, emis(0xb05cff, 2.4)), 0, ROOF_Y - 0.01, 4.1);
  for (let k = -5; k <= 5; k++) add(new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.02, 20), emis(0xfff1e0, 4)), k * 2.6, ROOF_Y - 0.01, 1.6);
  for (const x of [-6, 0, 6]) { const sp = new THREE.SpotLight(0xfff0dc, 40, 14, 0.55, 0.6, 1.6); sp.position.set(x, ROOF_Y - 0.1, 1.8); sp.target.position.set(x, 0, 4); scene.add(sp, sp.target); }

  // insegna GALA AUTO in lettere luminose sulla pensilina
  const signGroup = new THREE.Group(); signGroup.position.set(0, ROOF_Y + 0.45, 4.27); scene.add(signGroup);
  const halo = new THREE.Mesh(new THREE.PlaneGeometry(14, 2.0), new THREE.MeshBasicMaterial({ map: glowTex(), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false, opacity: 0 }));
  halo.position.z = -0.02; signGroup.add(halo);
  const letterFace = new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0xffffff, emissiveIntensity: 0, roughness: 0.3, metalness: 0.2 });
  const letterSide = new THREE.MeshPhysicalMaterial({ color: 0x8f6bff, metalness: 0.9, roughness: 0.25, emissive: 0x6a3cff, emissiveIntensity: 0 });
  if (FONT_URL) new FontLoader().load(FONT_URL + 'examples/fonts/helvetiker_bold.typeface.json', font => {
    const tg = new TextGeometry('GALA AUTO', { font, size: 0.95, height: 0.2, depth: 0.2, curveSegments: 6, bevelEnabled: true, bevelThickness: 0.02, bevelSize: 0.012, bevelSegments: 2 });
    tg.computeBoundingBox(); const bb = tg.boundingBox;
    tg.translate(-(bb.max.x + bb.min.x) / 2, -(bb.max.y + bb.min.y) / 2, 0);
    tg.applyMatrix4(new THREE.Matrix4().makeShear(0, 0, 0.2, 0, 0, 0));    // corsivo come nel biglietto
    const txt = new THREE.Mesh(tg, [letterFace, letterSide]); txt.position.z = 0.02; signGroup.add(txt);
  });
  // logo luminoso sul pilastro sinistro
  const logoTex = new THREE.TextureLoader().load('/logo.png'); logoTex.colorSpace = THREE.SRGBColorSpace;
  const logoM = new THREE.MeshBasicMaterial({ map: logoTex, transparent: true, toneMapped: false, color: new THREE.Color(0, 0, 0) });
  add(new THREE.Mesh(new THREE.PlaneGeometry(2.4, 1.38), logoM), -(BW / 2 + 0.6), 4.6, 0.52);
  add(new THREE.Mesh(new THREE.PlaneGeometry(0.06, 5.5), emis(0xb05cff, 2)), -(BW / 2 + 0.6) + 0.5, 3.4, 0.52);
  add(new THREE.Mesh(new THREE.PlaneGeometry(0.06, 5.5), emis(0x5b6cff, 2)), (BW / 2 + 0.6) - 0.5, 3.4, 0.52);

  // facciata in vetro con montanti in bronzo e porta scorrevole centrale
  const DOOR = 1.25;
  for (const [x0, x1] of [[-BW / 2, -DOOR - 0.35], [DOOR + 0.35, BW / 2]]) {
    add(new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, BH), glassOut), (x0 + x1) / 2, 0.3 + BH / 2, 0);
  }
  add(new THREE.Mesh(new THREE.PlaneGeometry(2 * DOOR + 0.7, BH - 3.3), glassOut), 0, 0.3 + 3.3 + (BH - 3.3) / 2, 0);
  for (let x = -BW / 2; x <= BW / 2 + 0.01; x += 2.5) if (Math.abs(x) > DOOR + 0.2) add(box(0.09, BH, 0.22, bronze), x, 0.3 + BH / 2, 0);
  add(box(BW, 0.12, 0.24, bronze), 0, 0.36, 0);
  // portale dell'ingresso
  add(box(0.22, 3.4, 0.4, bronze), -DOOR - 0.35, 0.3 + 1.7, 0); add(box(0.22, 3.4, 0.4, bronze), DOOR + 0.35, 0.3 + 1.7, 0);
  add(box(2 * DOOR + 0.92, 0.22, 0.4, bronze), 0, 0.3 + 3.35, 0);
  add(box(2 * DOOR + 0.6, 0.02, 0.06, emis(0xfff1e0, 3)), 0, 0.3 + 3.22, 0.22);
  const doors = [];
  for (const sx of [-1, 1]) {
    const d = new THREE.Group(); d.position.set(sx * DOOR / 2, 0.3 + 1.6, 0.06); scene.add(d);
    d.add(new THREE.Mesh(new THREE.PlaneGeometry(DOOR, 3.2), glassOut));
    const fr = new THREE.Mesh(new THREE.BoxGeometry(0.05, 3.2, 0.05), bronze); fr.position.x = -sx * DOOR / 2; d.add(fr);
    const hd = new THREE.Mesh(new THREE.BoxGeometry(0.03, 1.1, 0.06), new THREE.MeshPhysicalMaterial({ color: 0xd8d8de, metalness: 1, roughness: 0.15 })); hd.position.set(-sx * (DOOR / 2 - 0.12), 0, 0.06); d.add(hd);
    doors.push({ d, sx });
  }

  // ===== interno: luci a soffitto, pedane, auto =====
  for (let k = 0; k < 4; k++) add(box(BW - 3, 0.02, 0.18, emis(0xfff4e6, 3.2)), 0, ROOF_Y - 0.05, -2.5 - k * 5);
  const ra1 = new THREE.RectAreaLight(0xfff2e2, 2.2, 22, 6); ra1.position.set(0, ROOF_Y - 0.2, -6); ra1.lookAt(0, 0, -6); scene.add(ra1);
  const ra2 = new THREE.RectAreaLight(0xfff2e2, 2.2, 22, 6); ra2.position.set(0, ROOF_Y - 0.2, -15); ra2.lookAt(0, 0, -15); scene.add(ra2);
  scene.add(new THREE.HemisphereLight(0x9aa0ff, 0x0b0a14, 0.35));
  const moon = new THREE.DirectionalLight(0xb9c4ff, 0.6); moon.position.set(-10, 20, 20); scene.add(moon);

  const suvBase = buildSUV(T);
  const paints = [0x6b6d73, 0x0d0d10, 0xe9e6e0, 0x3a2f5c, 0x1d2a3a];
  const placeCar = (x, z, ry, color, pod) => {
    const c = suvBase.group.clone(true);
    c.traverse(o => { if (o.isMesh && o.material && o.material.userData.paint) { o.material = o.material.clone(); o.material.color.setHex(color); } });
    c.position.set(x, pod ? 0.42 : 0, z); c.rotation.y = ry; scene.add(c);
    c.traverse(o => { if (o.isMesh && o.material && o.material.isMeshBasicMaterial && o.material.color.r > 0.9 && o.material.color.b > 0.9) { o.material = o.material.clone(); o.material.color.setRGB(2.2, 2.2, 2.4); } });
    if (pod) {
      add(new THREE.Mesh(new THREE.CylinderGeometry(3.1, 3.2, 0.12, 64), new THREE.MeshPhysicalMaterial({ color: 0x15141a, metalness: 0.6, roughness: 0.2, clearcoat: 1 })), x, 0.36, z);
      add(new THREE.Mesh(new THREE.TorusGeometry(3.15, 0.012, 6, 128), emis(0xb05cff, 2.2)), x, 0.42, z, Math.PI / 2);
      const sp = new THREE.SpotLight(0xffffff, 30, 12, 0.42, 0.5, 1.5); sp.position.set(x, ROOF_Y - 0.3, z + 1.5); sp.target.position.set(x, 0.4, z); scene.add(sp, sp.target);
    }
    const ct = new THREE.Mesh(new THREE.PlaneGeometry(5.6, 2.6), new THREE.MeshBasicMaterial({ map: contactTex(), transparent: true, depthWrite: false }));
    ct.rotation.set(-Math.PI / 2, 0, ry); ct.position.set(x, (pod ? 0.43 : 0.31) + 0.002, z); scene.add(ct);
    return c;
  };
  // nello showroom (si vedono anche da fuori, attraverso i vetri)
  placeCar(-6.5, -7, 0.75, paints[0], true);
  placeCar(6.5, -7, -0.75, paints[2], true);
  placeCar(-7.5, -15.5, 1.2, paints[3], true);
  placeCar(7.5, -15.5, -1.2, paints[1], true);
  // fuori, parcheggiate sul piazzale
  for (const [x, z, ry, c] of [[-8.5, 9, 1.95, 1], [8.5, 9.5, -1.95, 4]]) {
    const car = placeCar(x, z, ry, paints[c], false);
    car.traverse(o => { if (o.material && o.material.userData && o.material.userData.paint === undefined) {} });
  }
  // ===== galleria: le auto vere di AutoScout sugli schermi della parete di fondo =====
  const screens = [];
  const wallTitle = new THREE.Mesh(new THREE.PlaneGeometry(8, 0.9), new THREE.MeshBasicMaterial({ map: titleTex('LE NOSTRE AUTO'), transparent: true, toneMapped: false }));
  add(wallTitle, 0, 6.6, -BD + 0.22);
  fetch('/api/auto').then(r => r.json()).then(d => {
    const list = (d.auto || []).filter(a => a.prezzo).slice(0, 12);
    const cols = Math.min(4, Math.max(3, Math.ceil(list.length / 3))), sw = 2.6, sh = 1.83, gap = 0.35;
    list.forEach((a, i) => {
      const row = Math.floor(i / cols), col = i % cols, inRow = Math.min(cols, list.length - row * cols);
      const x = (col - (inRow - 1) / 2) * (sw + gap), y = 5.0 - row * (sh + gap);
      const fr = RB(sw + 0.16, sh + 0.16, 0.08, 0.03, darkMetal); add(fr, x, y, -BD + 0.24);
      const m = new THREE.Mesh(new THREE.PlaneGeometry(sw, sh), new THREE.MeshBasicMaterial({ map: cardTex(a), toneMapped: false }));
      m.userData.id = a.id; add(m, x, y, -BD + 0.29); screens.push(m);
    });
  }).catch(() => {});

  const ledBase = add(box(BW, 0.05, 0.05, emis(0xb05cff, 2)), 0, 0.33, 0.25);

  return {
    screens,
    update(t, dt, p, intro, cam) {
      // insegna che si accende
      const on = smooth(0.25, 0.9, intro) * (0.92 + 0.08 * Math.sin(t * 3) * Math.sin(t * 1.3));
      letterFace.emissiveIntensity = on * 1.6; letterSide.emissiveIntensity = on * 0.8; halo.material.opacity = on * 0.22;
      logoM.color.setRGB(1, 1, 1).multiplyScalar(on * 1.6);
      // porte che si aprono scorrendo
      const op = ease(smooth(0.26, 0.4, p));
      for (const { d, sx } of doors) d.position.x = sx * (DOOR / 2 + op * (DOOR + 0.1));
      // un solo specchio attivo alla volta (fuori o dentro)
      const inside = cam.position.z < 0.5;
      plazaRef.visible = !inside; floorRef.visible = inside || cam.position.z < 9;
      stars.rotation.y = t * 0.003;
    },
  };
}

function skyNight() {
  const c = document.createElement('canvas'); c.width = 16; c.height = 512; const x = c.getContext('2d');
  const g = x.createLinearGradient(0, 0, 0, 512);
  g.addColorStop(0, '#020208'); g.addColorStop(0.42, '#0a0a22'); g.addColorStop(0.5, '#2a1f4a'); g.addColorStop(0.53, '#3b2a55'); g.addColorStop(0.6, '#0a0910'); g.addColorStop(1, '#050508');
  x.fillStyle = g; x.fillRect(0, 0, 16, 512);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
function glowTex() {
  const c = document.createElement('canvas'); c.width = 512; c.height = 128; const x = c.getContext('2d');
  const g = x.createRadialGradient(256, 64, 4, 256, 64, 256); g.addColorStop(0, 'rgba(176,92,255,.55)'); g.addColorStop(0.5, 'rgba(110,80,255,.18)'); g.addColorStop(1, 'rgba(0,0,0,0)');
  x.save(); x.scale(1, 0.25); x.translate(0, 192); x.fillStyle = g; x.fillRect(0, -192, 512, 512); x.restore();
  return new THREE.CanvasTexture(c);
}
function titleTex(txt) {
  const c = document.createElement('canvas'); c.width = 1024; c.height = 116; const x = c.getContext('2d');
  const draw = () => {
    x.clearRect(0, 0, 1024, 116);
    const g = x.createLinearGradient(200, 0, 824, 0); g.addColorStop(0, '#d16bff'); g.addColorStop(0.45, '#9b5cff'); g.addColorStop(1, '#5b6cff');
    x.fillStyle = g; x.textAlign = 'center'; x.textBaseline = 'middle'; x.font = 'italic 800 92px "Barlow Condensed", sans-serif'; x.fillText(txt, 512, 60); t.needsUpdate = true;
  };
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; draw(); document.fonts && document.fonts.ready.then(draw); return t;
}
function cardTex(a) {
  const c = document.createElement('canvas'); c.width = 640; c.height = 450;
  const x = c.getContext('2d');
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
  const label = () => {
    const g = x.createLinearGradient(0, 360, 0, 450); g.addColorStop(0, 'rgba(8,7,14,.0)'); g.addColorStop(0.35, 'rgba(8,7,14,.85)'); g.addColorStop(1, '#08070e');
    x.fillStyle = g; x.fillRect(0, 330, 640, 120);
    x.fillStyle = '#ffffff'; x.font = 'italic 700 34px "Barlow Condensed", sans-serif'; x.textBaseline = 'middle';
    x.fillText(`${a.marca || ''} ${a.modello || ''}`.toUpperCase().slice(0, 28), 22, 410);
    x.textAlign = 'right'; x.font = 'italic 800 40px "Barlow Condensed", sans-serif'; x.fillStyle = '#d6b8ff';
    x.fillText((a.prezzo || 0).toLocaleString('it-IT') + ' €', 618, 410); x.textAlign = 'left';
    tex.needsUpdate = true;
  };
  x.fillStyle = '#15121f'; x.fillRect(0, 0, 640, 450); label();
  const u = (a.foto || [])[0];
  if (u) {
    const im = new Image();
    im.onload = () => { const r = Math.max(640 / im.width, 450 / im.height), w = im.width * r, h = im.height * r; x.drawImage(im, (640 - w) / 2, (450 - h) / 2, w, h); label(); };
    im.src = '/img?u=' + encodeURIComponent(u.replace(/\/\d+x\d+\.webp$/, '/720x540.webp'));
  }
  return tex;
}


// ===================================================================================
// SUV (stesso modello usato per tutte le auto dello showroom)
function buildSUV(T) {
  const group = new THREE.Group();
  const DEPTH = 1.72, BT = 0.13, BS = 0.1, HW = DEPTH / 2 + BT, CROWN = 0.045;
  const WF = 1.5, WR = -1.5, WY = 0.47, WRAD = 0.42, ARCH = 0.56, AY = 0.45;
  // pianta e sezioni: abitacolo rastremato, spalla bombata, parafanghi allargati, fondo rientrante
  const taper = (x, y) => {
    let k = 1 - smooth(1.15, 1.7, y) * 0.16 - smooth(2.1, 2.52, x) * 0.035 - smooth(-2.0, -2.45, x) * 0.045 - smooth(0.68, 0.44, y) * 0.04
      + 0.022 * Math.exp(-Math.pow((y - 0.98) / 0.17, 2));
    for (const wx of [WF, WR]) { const d = Math.hypot(x - wx, y - AY); if (y > AY - 0.05) k += 0.035 * Math.exp(-Math.pow((d - 0.64) / 0.11, 2)); }
    return k;
  };
  const hw = (x, y) => HW * taper(x, y);

  const s = new THREE.Shape();
  s.moveTo(-2.36, AY);
  s.lineTo(WR - ARCH, AY); s.absarc(WR, AY, ARCH, Math.PI, 0, true);
  s.lineTo(WF - ARCH, AY); s.absarc(WF, AY, ARCH, Math.PI, 0, true);
  s.lineTo(2.3, AY);
  s.quadraticCurveTo(2.47, 0.47, 2.49, 0.6);
  s.lineTo(2.5, 0.84);
  s.quadraticCurveTo(2.49, 0.99, 2.34, 1.04);
  s.quadraticCurveTo(1.7, 1.11, 0.98, 1.15);
  s.lineTo(0.92, 1.17);
  s.quadraticCurveTo(0.5, 1.48, 0.15, 1.7);
  s.quadraticCurveTo(0.0, 1.75, -0.3, 1.75);
  s.quadraticCurveTo(-1.1, 1.745, -1.75, 1.685);
  s.quadraticCurveTo(-2.06, 1.655, -2.18, 1.58);
  s.lineTo(-2.38, 1.25);
  s.quadraticCurveTo(-2.44, 1.1, -2.43, 0.8);
  s.quadraticCurveTo(-2.44, 0.52, -2.36, AY);

  const makeBody = (maxLen, segs) => {
    let g = new THREE.ExtrudeGeometry(s, { depth: DEPTH, bevelEnabled: true, bevelThickness: BT, bevelSize: BS, bevelSegments: segs, curveSegments: segs * 5, steps: 1 });
    g.translate(0, 0, -DEPTH / 2);
    g.deleteAttribute('uv'); g.deleteAttribute('normal');
    g = mergeVertices(g, 1e-4);
    g = refine(g, maxLen);
    const P = g.attributes.position;
    const uv = new Float32Array(P.count * 2);
    for (let i = 0; i < P.count; i++) {
      const x = P.getX(i), y = P.getY(i), z = P.getZ(i), zn = clamp(Math.abs(z) / HW);
      const crown = CROWN * (1 - zn * zn) * smooth(0.95, 1.12, y) + 0.018 * (1 - zn * zn) * smooth(1.6, 1.74, y);
      P.setXYZ(i, x, y + crown, z * taper(x, y));
      uv[i * 2] = x * 1.5 + z * 0.7; uv[i * 2 + 1] = y * 1.5 + z * 0.4;
    }
    g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    g.computeVertexNormals();
    return g;
  };
  const g = makeBody(MOBILE ? 0.09 : 0.065, MOBILE ? 6 : 8);
  const P = g.attributes.position;
  const N = g.attributes.normal, I = g.index.array;
  const paintIdx = [], trimIdx = [];
  const a = V(0, 0, 0), b = V(0, 0, 0), c = V(0, 0, 0), n = V(0, 0, 0), m = V(0, 0, 0);
  for (let f = 0; f < I.length; f += 3) {
    a.fromBufferAttribute(P, I[f]); b.fromBufferAttribute(P, I[f + 1]); c.fromBufferAttribute(P, I[f + 2]);
    n.subVectors(c, b).cross(m.subVectors(a, b)).normalize(); m.copy(a).add(b).add(c).multiplyScalar(1 / 3);
    let trim = n.y < -0.3;
    for (const wx of [WF, WR]) { const dx = wx - m.x, dy = AY - m.y; if (Math.hypot(dx, dy) < ARCH + 0.08 && Math.abs(n.z) < 0.8 && (n.x * dx + n.y * dy) > 0) trim = true; }
    (trim ? trimIdx : paintIdx).push(I[f], I[f + 1], I[f + 2]);
  }
  const sub = idx => { const gg = new THREE.BufferGeometry(); gg.setAttribute('position', P); gg.setAttribute('normal', N); gg.setAttribute('uv', g.attributes.uv); gg.setIndex(idx); return gg; };

  const paint = new THREE.MeshPhysicalMaterial({ color: 0x6b6d73, metalness: 0.7, roughness: 0.34,
    clearcoat: 1, clearcoatRoughness: 0.025, envMapIntensity: 1.35 });
  paint.userData.paint = true;
  const gloss = new THREE.MeshPhysicalMaterial({ color: 0x08080a, metalness: 0.2, roughness: 0.12, clearcoat: 1, clearcoatRoughness: 0.03, envMapIntensity: 1.4 });
  const satin = new THREE.MeshPhysicalMaterial({ color: 0x141416, metalness: 0.3, roughness: 0.55, normalMap: T.plasticN, normalScale: V2(0.3, 0.3) });
  const glass = new THREE.MeshPhysicalMaterial({ color: 0x050608, metalness: 0.1, roughness: 0.05, clearcoat: 0.6, envMapIntensity: 0.9, side: THREE.DoubleSide });
  const darkChrome = new THREE.MeshPhysicalMaterial({ color: 0x3a3b40, metalness: 1, roughness: 0.18 });
  const chrome = new THREE.MeshPhysicalMaterial({ color: 0xdedfe6, metalness: 1, roughness: 0.08 });
  const body = new THREE.Mesh(sub(paintIdx), paint); group.add(body);
  group.add(new THREE.Mesh(sub(trimIdx), satin));

  const bodyMesh = new THREE.Mesh(makeBody(0.34, 2)); bodyMesh.updateMatrixWorld();
  const rc = new THREE.Raycaster();
  const hit = (o, d) => { rc.set(o, d); return rc.intersectObject(bodyMesh)[0] || null; };
  const hitDown = (x, z) => { const h = hit(V(x, 4, z), V(0, -1, 0)); return h ? h.point.y + 0.008 : null; };
  const hitFront = (y, z) => { const h = hit(V(5, y, z), V(-1, 0, 0)); return (h ? h.point.x : 2.5) + 0.025; };
  const hitBack = (y, z) => { const h = hit(V(-5, y, z), V(1, 0, 0)); return (h ? h.point.x : -2.43) - 0.025; };
  const grid = (nu, nv, fn) => {
    const pos = [], idx = [], uvs = [];
    for (let i = 0; i <= nu; i++) for (let j = 0; j <= nv; j++) { pos.push(...fn(i / nu, j / nv)); uvs.push(j / nv, i / nu); }
    for (let i = 0; i < nu; i++) for (let j = 0; j < nv; j++) { const p0 = i * (nv + 1) + j, p1 = p0 + nv + 1; idx.push(p0, p1, p0 + 1, p1, p1 + 1, p0 + 1); }
    const gg = new THREE.BufferGeometry(); gg.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); gg.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2)); gg.setIndex(idx); gg.computeVertexNormals(); return gg;
  };
  // parabrezza con cornice nera
  group.add(new THREE.Mesh(grid(10, 10, (u, v) => {
    const x = 0.9 - u * 0.78, half = hw(x, 1.45) * 0.9, z = (v * 2 - 1) * half;
    return [x, (hitDown(x, z) ?? 1.45) + 0.012, z];
  }), gloss));
  group.add(new THREE.Mesh(grid(10, 10, (u, v) => {
    const x = 0.87 - u * 0.72, half = hw(x, 1.45) * 0.84, z = (v * 2 - 1) * half;
    return [x, (hitDown(x, z) ?? 1.45) + 0.016, z];
  }), glass));
  // lunotto
  group.add(new THREE.Mesh(grid(6, 10, (u, v) => {
    const y = 1.27 + u * 0.34, z = (v * 2 - 1) * hw(-2.28, y) * 0.78;
    return [(hitBack(y, z)) - 0.014, y, z];
  }), glass));

  // elementi sulla fiancata (seguono la curvatura)
  const onSide = (pts, sg, off, mat) => {
    const sh = new THREE.Shape(); sh.moveTo(...pts[0]); pts.slice(1).forEach(p => sh.lineTo(...p)); sh.closePath();
    let gg = new THREE.ShapeGeometry(sh, 8); gg.deleteAttribute('uv'); gg.deleteAttribute('normal'); gg = mergeVertices(gg, 1e-5); gg = refine(gg, 0.09);
    const Q = gg.attributes.position;
    for (let k = 0; k < Q.count; k++) { const x = Q.getX(k), y = Q.getY(k); Q.setXYZ(k, x, y, sg * (hw(x, y) + off)); }
    if (sg < 0) { const ix = gg.index.array; for (let k = 0; k < ix.length; k += 3) { const t = ix[k + 1]; ix[k + 1] = ix[k + 2]; ix[k + 2] = t; } }
    gg.computeVertexNormals(); const me = new THREE.Mesh(gg, mat); group.add(me); return me;
  };
  const line = (pts, sg, off, color = 0x060608) => {
    const gl = new THREE.BufferGeometry().setFromPoints(pts.map(([x, y]) => V(x, y, sg * (hw(x, y) + off))));
    group.add(new THREE.Line(gl, new THREE.LineBasicMaterial({ color })));
  };
  const winFrame = [[0.82, 1.18], [0.2, 1.645], [-0.05, 1.675], [-1.65, 1.615], [-2.0, 1.535], [-2.17, 1.27], [-2.19, 1.225]];
  const win = [[0.74, 1.21], [0.19, 1.61], [-0.06, 1.64], [-1.62, 1.585], [-1.95, 1.505], [-2.1, 1.28], [-2.12, 1.25]];
  for (const sg of [1, -1]) {
    onSide(winFrame, sg, 0.003, gloss);
    onSide(win, sg, 0.006, glass);
    onSide([[-0.07, 1.2], [-0.06, 1.64], [-0.2, 1.645], [-0.21, 1.2]], sg, 0.008, gloss);           // montante B nero lucido
    onSide([[-1.2, 1.23], [-1.18, 1.61], [-1.32, 1.605], [-1.34, 1.235]], sg, 0.009, paint);           // montante C in tinta
    onSide([[1.0, 0.5], [1.0, 0.64], [-0.98, 0.64], [-0.98, 0.5]], sg, 0.004, gloss);                  // fascia bassa nera
    onSide([[1.0, 0.64], [1.0, 0.652], [-0.98, 0.652], [-0.98, 0.64]], sg, 0.006, darkChrome);
    // linee delle portiere
    line([[0.86, 1.18], [0.86, 0.72], [0.82, 0.66]], sg, 0.002);
    line([[-0.14, 1.18], [-0.14, 0.66]], sg, 0.002);
    line([[-1.24, 1.23], [-1.24, 1.02], [-1.0, 0.86], [-0.98, 0.66]], sg, 0.002);
    // maniglie a filo
    onSide([[0.42, 1.06], [0.42, 1.08], [0.16, 1.08], [0.16, 1.06]], sg, 0.006, darkChrome);
    onSide([[-0.68, 1.06], [-0.68, 1.08], [-0.94, 1.08], [-0.94, 1.06]], sg, 0.006, darkChrome);
    // presa d'aria sul parafango anteriore
    // specchietto
    const mir = new THREE.Group(); mir.position.set(0.72, 1.24, sg * (hw(0.72, 1.24) + 0.05)); group.add(mir);
    const arm = new THREE.Mesh(new RoundedBoxGeometry(0.14, 0.05, 0.12, 2, 0.02), gloss); arm.position.set(0, 0, sg * 0.04); mir.add(arm);
    const cap = new THREE.Mesh(new RoundedBoxGeometry(0.24, 0.15, 0.13, 4, 0.05), gloss); cap.position.set(-0.03, 0.04, sg * 0.15); mir.add(cap);
    const back = new THREE.Mesh(new RoundedBoxGeometry(0.2, 0.12, 0.04, 3, 0.02), paint); back.position.set(0.06, 0.04, sg * 0.15); mir.add(back);
  }

  // ---------- muso: mascherina a nido d'ape, fari a LED sottili, prese d'aria ----------
  const meshTex = hexMesh();
  const fx = (y, z) => hitFront(y, z);
  const frontPlate = (w, h, y, z, mat, off = 0.005, d = 0.03, r = 0.012) => {
    const me = new THREE.Mesh(new RoundedBoxGeometry(d, h, w, 3, r), mat); me.position.set(fx(y, z) + off, y, z); group.add(me); return me;
  };
  frontPlate(1.02, 0.27, 0.8, 0, darkChrome, 0.005, 0.03, 0.03);
  const gr = new THREE.Mesh(new THREE.PlaneGeometry(0.96, 0.22), new THREE.MeshStandardMaterial({ map: meshTex, alphaMap: meshTex, transparent: true, alphaTest: 0.4, color: 0x3a3b42, metalness: 0.9, roughness: 0.3, side: THREE.DoubleSide }));
  gr.position.set(fx(0.8, 0) + 0.023, 0.8, 0); gr.rotation.y = Math.PI / 2; group.add(gr);
  frontPlate(0.95, 0.2, 0.8, 0, new THREE.MeshBasicMaterial({ color: 0x020203 }), -0.005, 0.02, 0.01);
  // presa bassa
  frontPlate(1.36, 0.13, 0.55, 0, gloss, 0.002, 0.03, 0.03);
  const low = gr.clone(); low.scale.set(1.35, 0.5, 1); low.position.set(fx(0.55, 0) + 0.02, 0.55, 0); group.add(low);
  const heads = [];
  const ledW = new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false });
  const ledR = new THREE.MeshBasicMaterial({ color: 0xff1a28, toneMapped: false });
  for (const sg of [1, -1]) {
    // gruppo ottico
    const z = sg * 0.66;
    const hl = frontPlate(0.42, 0.105, 0.93, z, new THREE.MeshPhysicalMaterial({ color: 0x0a0b0e, metalness: 0.5, roughness: 0.05, clearcoat: 1 }), 0.0, 0.05, 0.03);
    hl.rotation.y = -sg * 0.18;
    const drl = new THREE.Mesh(new THREE.BoxGeometry(0.01, 0.011, 0.38), ledW); drl.position.set(fx(0.95, z) + 0.03, 0.955, z); drl.rotation.y = -sg * 0.18; group.add(drl); heads.push(drl);
    for (let k = 0; k < 3; k++) {
      const pr = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.01, 24), new THREE.MeshPhysicalMaterial({ color: 0xcfd8ff, metalness: 1, roughness: 0.05, emissive: 0xdde6ff, emissiveIntensity: 0.15 }));
      pr.rotation.z = Math.PI / 2; const zz = z - sg * (0.08 - k * 0.07); pr.position.set(fx(0.91, zz) + 0.026, 0.91, zz); group.add(pr);
    }
    // prese laterali verticali
    frontPlate(0.12, 0.22, 0.6, sg * 0.86, gloss, 0.0, 0.03, 0.02);
    const fogBar = new THREE.Mesh(new THREE.BoxGeometry(0.008, 0.16, 0.012), ledW); fogBar.position.set(fx(0.6, sg * 0.86) + 0.022, 0.6, sg * 0.86); group.add(fogBar);
  }
  // scritta sul cofano e targa
  const badge = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.06), new THREE.MeshPhysicalMaterial({ map: textPlate('G A L A', 'rgba(0,0,0,0)', '#ececf2', 512, 64), transparent: true, metalness: 1, roughness: 0.2 }));
  const bx = 2.3; badge.position.set(bx, (hitDown(bx, 0) ?? 1.05) + 0.004, 0); badge.rotation.set(-Math.PI / 2, 0, -Math.PI / 2); badge.rotation.order = 'YXZ'; badge.rotation.set(-Math.PI / 2 + 0.12, Math.PI / 2, 0); group.add(badge);
  const plateTex = textPlate('GALA AUTO', '#f4f4f6', '#15151a', 512, 112);
  const pl = new THREE.Mesh(new THREE.PlaneGeometry(0.52, 0.11), new THREE.MeshStandardMaterial({ map: plateTex, roughness: 0.4 })); pl.position.set(fx(0.66, 0) + 0.035, 0.66, 0); pl.rotation.y = Math.PI / 2; group.add(pl);

  // coda: fanale sottile a tutta larghezza, targa
  const tz = 1.62;
  const tailBar = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.022, tz), ledR); tailBar.position.set(hitBack(1.18, 0) - 0.012, 1.18, 0); group.add(tailBar);
  for (const sg of [1, -1]) {
    const tl = new THREE.Mesh(new RoundedBoxGeometry(0.04, 0.12, 0.34, 3, 0.02), new THREE.MeshPhysicalMaterial({ color: 0x2a0306, metalness: 0.2, roughness: 0.05, clearcoat: 1, emissive: 0x6a0010, emissiveIntensity: 0.6 }));
    tl.position.set(hitBack(1.15, sg * 0.7) - 0.01, 1.15, sg * 0.7); group.add(tl);
  }
  const plb = pl.clone(); plb.position.set(hitBack(0.82, 0) - 0.02, 0.82, 0); plb.rotation.y = -Math.PI / 2; group.add(plb);
  const diff = new THREE.Mesh(new RoundedBoxGeometry(0.06, 0.12, 1.4, 3, 0.03), gloss); diff.position.set(hitBack(0.55, 0) - 0.01, 0.55, 0); group.add(diff);
  for (const sg of [1, -1]) { const tp = new THREE.Mesh(new RoundedBoxGeometry(0.05, 0.05, 0.18, 3, 0.02), darkChrome); tp.position.set(hitBack(0.52, sg * 0.55) - 0.02, 0.52, sg * 0.55); group.add(tp); }

  // ---------- cerchi da 22" a razze sdoppiate, dischi forati, pneumatici ----------
  const tireGeo = (() => { const pr = []; for (let k = 0; k <= 24; k++) { const an = -Math.PI / 2 + Math.PI * k / 24; pr.push(V2(WRAD - 0.075 + Math.cos(an) * 0.075, Math.sin(an) * 0.15 * (1 - 0.15 * Math.pow(Math.sin(an), 8)))); } return new THREE.LatheGeometry([V2(0.33, -0.15), ...pr, V2(0.33, 0.15)], 72); })();
  const tireM = new THREE.MeshStandardMaterial({ color: 0x0f0f10, roughness: 0.88, normalMap: T.plasticN, normalScale: V2(0.6, 0.6) });
  const spokeM = new THREE.MeshPhysicalMaterial({ color: 0x2c2d31, metalness: 0.9, roughness: 0.32, clearcoat: 0.5 });
  const faceM = new THREE.MeshPhysicalMaterial({ color: 0xbfc0c6, metalness: 1, roughness: 0.2, normalMap: T.brushedN, normalScale: V2(0.2, 0.2) });
  const discM = new THREE.MeshStandardMaterial({ color: 0x6a6a70, metalness: 0.9, roughness: 0.45, normalMap: T.brushedN });
  const calM = new THREE.MeshPhysicalMaterial({ color: 0x141416, metalness: 0.4, roughness: 0.35, clearcoat: 1 });
  const wheels = [];
  for (const wx of [WF, WR]) for (const sg of [1, -1]) {
    const w = new THREE.Group(); w.position.set(wx, WY, sg * (hw(wx, WY) - 0.16));
    const tire = new THREE.Mesh(tireGeo, tireM); tire.rotation.x = Math.PI / 2; w.add(tire);
    const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.33, 0.33, 0.26, 48, 1, true), spokeM); barrel.rotation.x = Math.PI / 2; w.add(barrel);
    const lip = new THREE.Mesh(new THREE.TorusGeometry(0.33, 0.012, 8, 72), faceM); lip.position.z = sg * 0.13; w.add(lip);
    const spin = new THREE.Group(); w.add(spin);
    for (let k = 0; k < 5; k++) {
      const an = k / 5 * Math.PI * 2;
      for (const off of [-0.11, 0.11]) {
        const sp = new THREE.Mesh(new RoundedBoxGeometry(0.27, 0.034, 0.04, 2, 0.012), spokeM);
        sp.position.set(Math.cos(an + off * 0.6) * 0.18, Math.sin(an + off * 0.6) * 0.18, sg * 0.11); sp.rotation.z = an + off * 0.9; spin.add(sp);
        const fc = new THREE.Mesh(new THREE.BoxGeometry(0.24, 0.012, 0.004), faceM);
        fc.position.set(Math.cos(an + off * 0.6) * 0.18, Math.sin(an + off * 0.6) * 0.18, sg * 0.132); fc.rotation.z = an + off * 0.9; spin.add(fc);
      }
    }
    const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.065, 0.07, 0.05, 32), spokeM); hub.rotation.x = Math.PI / 2; hub.position.z = sg * 0.12; spin.add(hub);
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.012, 32), faceM); cap.rotation.x = Math.PI / 2; cap.position.z = sg * 0.147; spin.add(cap);
    for (let k = 0; k < 5; k++) { const nut = new THREE.Mesh(new THREE.CylinderGeometry(0.011, 0.011, 0.02, 6), faceM); nut.rotation.x = Math.PI / 2; nut.position.set(Math.cos(k * 1.2566) * 0.085, Math.sin(k * 1.2566) * 0.085, sg * 0.14); spin.add(nut); }
    const disc = new THREE.Mesh(new THREE.CylinderGeometry(0.27, 0.27, 0.03, 64), discM); disc.rotation.x = Math.PI / 2; disc.position.z = sg * 0.0; w.add(disc);
    const cal = new THREE.Mesh(new RoundedBoxGeometry(0.2, 0.11, 0.08, 3, 0.025), calM); cal.position.set(-0.17, 0.12, sg * 0.03); cal.rotation.z = 0.75; w.add(cal);
    group.add(w); wheels.push(spin);
  }
  group.traverse(o => { if (o.isMesh && !o.material.isMeshBasicMaterial) { o.castShadow = true; o.receiveShadow = false; } });

  return {
    group,
    setLights(v) {
      ledW.color.setRGB(1, 1, 1).multiplyScalar(0.2 + v * 6);
      ledR.color.setRGB(1, 0.1, 0.15).multiplyScalar(0.2 + v * 4);
    },
  };
}

// nido d'ape per la mascherina
function hexMesh() {
  const c = document.createElement('canvas'); c.width = 512; c.height = 128; const x = c.getContext('2d');
  x.fillStyle = '#000'; x.fillRect(0, 0, 512, 128); x.fillStyle = '#fff';
  const r = 7, h = r * Math.sqrt(3);
  for (let row = -1; row < 128 / h + 2; row++) for (let col = -1; col < 512 / (r * 3) + 2; col++) {
    const cx = col * r * 3 + (row % 2 ? r * 1.5 : 0), cy = row * h / 2;
    x.beginPath(); for (let k = 0; k < 6; k++) { const an = k / 6 * Math.PI * 2; x.lineTo(cx + Math.cos(an) * r, cy + Math.sin(an) * r); } x.closePath();
    x.lineWidth = 2.6; x.strokeStyle = '#fff'; x.stroke();
  }
  const t = new THREE.CanvasTexture(c); t.anisotropy = 8; return t;
}


function contactTex() {
  const c = document.createElement('canvas'); c.width = 512; c.height = 256; const x = c.getContext('2d');
  const g = x.createRadialGradient(256, 128, 10, 256, 128, 250); g.addColorStop(0, 'rgba(0,0,0,.9)'); g.addColorStop(0.5, 'rgba(0,0,0,.55)'); g.addColorStop(1, 'rgba(0,0,0,0)');
  x.save(); x.scale(1, 0.5); x.translate(0, 128); x.fillStyle = g; x.fillRect(0, -128, 512, 512); x.restore();
  for (const [px, py] of [[102, 70], [410, 70], [102, 186], [410, 186]]) { const w = x.createRadialGradient(px, py, 2, px, py, 46); w.addColorStop(0, 'rgba(0,0,0,.95)'); w.addColorStop(1, 'rgba(0,0,0,0)'); x.fillStyle = w; x.fillRect(px - 50, py - 50, 100, 100); }
  return new THREE.CanvasTexture(c);
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
  const flakeH = height((u, v) => hash(Math.floor(u * N), Math.floor(v * N)));
  const carpet = toNormal(height((u, v) => hash(Math.floor(u * N), Math.floor(v * N)) * 0.5 + fbm(u, v, 64, 2) * 0.5), 3, 6);
  return {
    leatherN: toNormal(leatherH, 3, 6), quiltN: toNormal(quiltH, 10, 1), brushedN: toNormal(brushedH, 3, 1),
    castN: toNormal(castH, 5, 2), plasticN: toNormal(plasticH, 3, 3), wood, carpet, flakeN: toNormal(flakeH, 2, 8),
  };
}


// ---------- ambiente da studio (riflessi sulle superfici lucide) ----------
function studioEnv(renderer, bright) {
  const s = new THREE.Scene();
  const base = bright ? 0x8a8794 : 0x17161d;
  s.add(new THREE.Mesh(new THREE.SphereGeometry(40, 32, 16), new THREE.MeshBasicMaterial({ color: base, side: THREE.BackSide })));
  const panel = (w, h, pos, rot, k, col = 0xffffff) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(col).multiplyScalar(k), side: THREE.DoubleSide }));
    m.position.set(...pos); m.rotation.set(...rot); s.add(m);
  };
  panel(16, 7, [0, 12, 0], [Math.PI / 2, 0, 0], bright ? 4 : 3.5);                 // grande softbox sopra
  if (!bright) { panel(9, 2.2, [10, 3.5, 10], [0, -Math.PI / 4, 0], 2.6); panel(9, 2.2, [-10, 3.5, 10], [0, Math.PI / 4, 0], 1.8); panel(30, 0.5, [0, 0.8, 18], [0, Math.PI, 0], 2.2); }
  panel(2.5, 9, [-14, 4, 0], [0, Math.PI / 2, 0], bright ? 3 : 2.2);
  panel(2.5, 9, [14, 4, 2], [0, -Math.PI / 2, 0], bright ? 3 : 2, 0xfff2e0);   // luce calda
  panel(12, 1.2, [0, 1.2, -16], [0, 0, 0], bright ? 2.5 : 1.4, 0xb9a6ff);       // riflesso viola del marchio
  panel(12, 1.2, [0, 1.2, 16], [0, Math.PI, 0], bright ? 2 : 1.2, 0x9fb0ff);
  if (bright) panel(30, 12, [0, 6, -25], [0, 0, 0], 2.2, 0xeef0ff);
  const pm = new THREE.PMREMGenerator(renderer);
  const tex = pm.fromScene(s, 0.035).texture; pm.dispose(); return tex;
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
