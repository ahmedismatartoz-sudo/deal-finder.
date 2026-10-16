// Supercar in 3D costruita nel codice: si disegna, prende forma e apre le porte a forbice mentre scorri.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';

const FORCE = new URLSearchParams(location.search).get('p');
const MOBILE = matchMedia('(max-width: 820px)').matches || /Android|iPhone|iPad/i.test(navigator.userAgent);
const canvas = document.getElementById('car3d');
const show = document.getElementById('top');
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const ease = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

function fail(e) {
  console.warn('3D non disponibile', e);
  document.documentElement.classList.add('nogl');
  window.dispatchEvent(new Event('car-ready'));
}

try { init(); } catch (e) { fail(e); }

function init() {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: !MOBILE, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, MOBILE ? 1.5 : 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x050507);
  scene.fog = new THREE.Fog(0x050507, 11, 30);
  scene.environment = studioEnv(renderer);

  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);

  // ---------- pavimento a specchio ----------
  const W = innerWidth, H = innerHeight;
  const mirror = new Reflector(new THREE.PlaneGeometry(60, 60), {
    textureWidth: W * (MOBILE ? .5 : .75), textureHeight: H * (MOBILE ? .5 : .75), color: 0x8a8a92, clipBias: 0.003,
  });
  mirror.rotation.x = -Math.PI / 2;
  scene.add(mirror);
  const floorTint = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.MeshBasicMaterial({ color: 0x050507, transparent: true, opacity: 0.8 }));
  floorTint.rotation.x = -Math.PI / 2; floorTint.position.y = 0.002;
  scene.add(floorTint);
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(6.4, 3.4), new THREE.MeshBasicMaterial({ map: radial('rgba(0,0,0,1)', 'rgba(0,0,0,0)'), transparent: true, depthWrite: false }));
  shadow.rotation.x = -Math.PI / 2; shadow.position.y = 0.004;
  scene.add(shadow);
  // anelli di luce sul pavimento
  const ringMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(0xa294ff).multiplyScalar(2.2), transparent: true, opacity: 0, toneMapped: false });
  const ring = new THREE.Mesh(new THREE.RingGeometry(3.0, 3.025, 160), ringMat);
  ring.rotation.x = -Math.PI / 2; ring.scale.set(1.15, 0.6, 1); ring.position.y = 0.006;
  scene.add(ring);
  const ring2 = ring.clone(); ring2.material = ringMat.clone(); ring2.material.color = new THREE.Color(0xffffff).multiplyScalar(1.4);
  ring2.scale.set(1.32, 0.74, 1); scene.add(ring2);

  // ---------- auto ----------
  const car = buildCar();
  scene.add(car.group);

  // luci
  scene.add(new THREE.HemisphereLight(0xb8bcff, 0x050507, 0.35));
  const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(4, 8, 5); scene.add(key);
  const rim = new THREE.DirectionalLight(0xa294ff, 1.2); rim.position.set(-6, 3, -4); scene.add(rim);
  const cabin = new THREE.PointLight(0xa294ff, 0, 3.5, 2); cabin.position.set(-0.1, 0.75, 0); scene.add(cabin);

  // ---------- effetti ----------
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(W, H), MOBILE ? 0.45 : 0.6, 0.55, 0.88);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  // ---------- percorso della telecamera (legato allo scroll) ----------
  const camPath = new THREE.CatmullRomCurve3([
    new THREE.Vector3(6.6, 1.55, 6.2),    // 3/4 anteriore
    new THREE.Vector3(1.0, 1.0, 7.8),     // fiancata bassa
    new THREE.Vector3(-5.2, 1.9, 5.4),    // 3/4 posteriore
    new THREE.Vector3(-2.6, 3.4, 5.6),    // dall'alto, porte che si alzano
    new THREE.Vector3(2.8, 2.0, 5.2),     // 3/4 anteriore con porte aperte
    new THREE.Vector3(0.9, 1.5, 3.2),     // verso la portiera
  ], false, 'catmullrom', 0.4);
  const lookPath = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0.2, 0.55, 0), new THREE.Vector3(0, 0.55, 0), new THREE.Vector3(-0.2, 0.6, 0),
    new THREE.Vector3(-0.1, 0.75, 0), new THREE.Vector3(0, 0.8, 0.3), new THREE.Vector3(-0.1, 0.85, 0.4),
  ], false, 'catmullrom', 0.4);

  let target = 0, cur = 0, intro = 0, introStart = null, visible = true, aspect = 1;
  const copies = [...document.querySelectorAll('.copy')];
  const fade = document.getElementById('fade'), prog = document.getElementById('prog'), hint = document.getElementById('hint');
  const nav = document.getElementById('nav'), fab = document.querySelector('.fab');

  function onScroll() {
    const r = show.getBoundingClientRect();
    target = clamp(-r.top / (r.height - innerHeight));
    nav.classList.toggle('solid', r.bottom < innerHeight * 0.6 || target > 0.05);
    fab && fab.classList.toggle('on', target > 0.15);
  }
  addEventListener('scroll', onScroll, { passive: true });

  function resize() {
    const w = innerWidth, h = innerHeight;
    aspect = w / h;
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    camera.aspect = aspect;
    camera.fov = aspect < 0.8 ? 42 : 32;
    camera.updateProjectionMatrix();
  }
  addEventListener('resize', resize);
  resize(); onScroll();

  new IntersectionObserver(e => { visible = e[0].isIntersecting; }, { threshold: 0 }).observe(show);

  const pos = new THREE.Vector3(), look = new THREE.Vector3(), tmp = new THREE.Vector3();
  const clock = new THREE.Clock();
  let ready = false;

  function frame() {
    requestAnimationFrame(frame);
    if (!visible || document.hidden) return;
    const t = clock.getElapsedTime();
    if (introStart === null) introStart = t;
    intro = FORCE !== null ? 1 : clamp((t - introStart) / 3.6);
    cur = FORCE !== null ? +FORCE : cur + (target - cur) * 0.075;
    const p = cur;

    // intro: linee che disegnano l'auto, poi la carrozzeria che appare, poi le luci
    car.setSketch(ease(clamp(intro / 0.6)), 1 - smooth(0.75, 1, intro));
    car.setBody(smooth(0.45, 0.9, intro));
    car.setLights(smooth(0.8, 1, intro));
    ringMat.opacity = smooth(0.7, 1, intro) * 0.55;
    ring2.material.opacity = smooth(0.85, 1, intro) * 0.18;

    // porte a forbice: si aprono tra il 30% e il 62% dello scroll
    const open = ease(smooth(0.3, 0.62, p));
    car.setDoors(open);
    cabin.intensity = open * 1.2;

    // telecamera
    const u = p * 0.999;
    camPath.getPoint(u, pos); lookPath.getPoint(u, look);
    const far = aspect < 0.8 ? 1.75 : aspect < 1.3 ? 1.3 : 1;
    tmp.copy(pos).sub(look).multiplyScalar(far * (1 + (1 - ease(intro)) * 0.25)).add(look);
    camera.position.copy(tmp);
    camera.position.y += Math.sin(t * 0.5) * 0.03;
    camera.lookAt(look);
    car.group.rotation.y = Math.sin(t * 0.25) * 0.02;
    car.spin(p);

    // testi
    for (const c of copies) {
      const a = +c.dataset.from, b = +c.dataset.to;
      const on = p >= a && p < b && (c.classList.contains('c0') ? intro > 0.85 : true);
      c.classList.toggle('on', on);
    }
    fade.style.opacity = smooth(0.92, 1, p);
    prog.style.transform = `scaleY(${p})`;
    hint.style.opacity = p > 0.02 || intro < 0.9 ? 0 : 1;

    composer.render();
    if (!ready) { ready = true; window.dispatchEvent(new Event('car-ready')); }
  }
  frame();
}

// ===================================================================================
function buildCar() {
  const group = new THREE.Group();
  const DEPTH = 1.62, BT = 0.16, BS = 0.12, HW = DEPTH / 2 + BT, CROWN = 0.05;
  const WF = 1.45, WR = -1.35, WY = 0.36, WRAD = 0.36;

  const taper = (x, y) => 1
    - smooth(0.78, 1.18, y) * 0.36
    - smooth(1.45, 2.45, x) * 0.15
    - smooth(-1.85, -2.45, x) * 0.05
    - smooth(0.45, 0.2, y) * 0.04;
  const hw = (x, y) => HW * taper(x, y);

  // profilo laterale (cuneo da supercar) con i passaruota
  const s = new THREE.Shape();
  s.moveTo(-2.25, 0.30);
  s.lineTo(WR - 0.58, 0.30);
  s.absarc(WR, 0.30, 0.58, Math.PI, 0, true);
  s.lineTo(WF - 0.58, 0.30);
  s.absarc(WF, 0.30, 0.58, Math.PI, 0, true);
  s.lineTo(2.22, 0.30);
  s.quadraticCurveTo(2.37, 0.32, 2.34, 0.44);
  s.quadraticCurveTo(1.85, 0.6, 1.0, 0.73);
  s.lineTo(0.56, 0.80);
  s.quadraticCurveTo(0.02, 1.08, -0.38, 1.10);
  s.quadraticCurveTo(-0.85, 1.11, -1.25, 0.98);
  s.quadraticCurveTo(-1.9, 0.9, -2.28, 0.86);
  s.quadraticCurveTo(-2.37, 0.6, -2.25, 0.30);

  let g = new THREE.ExtrudeGeometry(s, { depth: DEPTH, bevelEnabled: true, bevelThickness: BT, bevelSize: BS, bevelSegments: MOBILE ? 6 : 8, curveSegments: MOBILE ? 32 : 48, steps: 1 });
  g.translate(0, 0, -DEPTH / 2);
  g.deleteAttribute('uv'); g.deleteAttribute('normal');
  g = mergeVertices(g, 1e-4);
  g = refine(g, MOBILE ? 0.075 : 0.055);
  const P = g.attributes.position;
  for (let i = 0; i < P.count; i++) {
    const x = P.getX(i), y = P.getY(i), z = P.getZ(i);
    const zn = clamp(Math.abs(z) / HW);
    P.setXYZ(i, x, y + CROWN * (1 - zn * zn) * smooth(0.6, 0.98, y), z * taper(x, y));
  }
  g.computeVertexNormals();

  // ---------- profili utili: bordo superiore interno (fiancata piatta) ed esterno (tetto) ----------
  const outline = s.getPoints(90);
  const cw = THREE.ShapeUtils.isClockWise(outline);
  const interp = arr => x => {                       // arr ordinato per x crescente: [[x,y]]
    if (x <= arr[0][0]) return arr[0][1];
    for (let k = 1; k < arr.length; k++) if (x <= arr[k][0]) { const [x0, y0] = arr[k - 1], [x1, y1] = arr[k]; return y0 + (y1 - y0) * (x - x0) / ((x1 - x0) || 1); }
    return arr[arr.length - 1][1];
  };
  const topIn = [], topOut = [];
  outline.forEach((p, k) => {
    if (p.y < 0.46) return;
    const q = outline[(k + 1) % outline.length], o = outline[(k - 1 + outline.length) % outline.length];
    let nx = q.y - o.y, ny = -(q.x - o.x); const l = Math.hypot(nx, ny) || 1; nx /= l; ny /= l;
    if (cw) { nx = -nx; ny = -ny; }
    topIn.push([p.x, p.y]); topOut.push([p.x + nx * BS, p.y + ny * BS]);
  });
  topIn.sort((u, v) => u[0] - v[0]); topOut.sort((u, v) => u[0] - v[0]);
  const yIn = interp(topIn), yOut = interp(topOut);
  const crownAt = (x, y, z) => { const zn = clamp(Math.abs(z) / (HW * taper(x, y))); return CROWN * (1 - zn * zn) * smooth(0.6, 0.98, y); };

  // ---------- vernice e plastiche (divise per orientamento: bordi puliti) ----------
  const N = g.attributes.normal, I = g.index.array;
  const paintIdx = [], trimIdx = [];
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), n = new THREE.Vector3(), m = new THREE.Vector3();
  for (let f = 0; f < I.length; f += 3) {
    a.fromBufferAttribute(P, I[f]); b.fromBufferAttribute(P, I[f + 1]); c.fromBufferAttribute(P, I[f + 2]);
    n.subVectors(c, b).cross(m.subVectors(a, b)).normalize();
    m.copy(a).add(b).add(c).multiplyScalar(1 / 3);
    let trim = n.y < -0.35;
    for (const wx of [WF, WR]) { const dx = wx - m.x, dy = 0.30 - m.y; if (Math.hypot(dx, dy) < 0.6 && Math.abs(n.z) < 0.7 && (n.x * dx + n.y * dy) > 0) trim = true; }
    (trim ? trimIdx : paintIdx).push(I[f], I[f + 1], I[f + 2]);
  }
  const subIdx = idx => { const gg = new THREE.BufferGeometry(); gg.setAttribute('position', P); gg.setAttribute('normal', N); gg.setIndex(idx); return gg; };

  // materiali
  const paint = new THREE.MeshPhysicalMaterial({ color: 0xc4c7d3, metalness: 1, roughness: 0.2, clearcoat: 1, clearcoatRoughness: 0.03, envMapIntensity: 1.3, transparent: true, opacity: 0 });
  const glass = new THREE.MeshPhysicalMaterial({ color: 0x020204, metalness: 0.2, roughness: 0.04, clearcoat: 1, envMapIntensity: 1.8, transparent: true, opacity: 0, side: THREE.DoubleSide });
  const trimM = new THREE.MeshPhysicalMaterial({ color: 0x0b0b0e, metalness: 0.5, roughness: 0.42, clearcoat: 0.4, transparent: true, opacity: 0, side: THREE.DoubleSide });
  const recessM = new THREE.MeshStandardMaterial({ color: 0x010102, roughness: 1, metalness: 0, emissive: 0x6e5cff, emissiveIntensity: 0, envMapIntensity: 0, transparent: true, opacity: 0, side: THREE.DoubleSide });
  const doorPaint = paint.clone(); doorPaint.side = THREE.DoubleSide;
  const fadeMats = [paint, glass, trimM, recessM, doorPaint];

  group.add(new THREE.Mesh(subIdx(paintIdx), paint));
  group.add(new THREE.Mesh(subIdx(trimIdx), trimM));

  // ---------- pezzi applicati sulla fiancata (forme lisce, seguono la curvatura) ----------
  const poly = pts => { const sh = new THREE.Shape(); sh.moveTo(...pts[0]); for (let k = 1; k < pts.length; k++) sh.lineTo(...pts[k]); sh.closePath(); return sh; };
  const roofLine = (x0, x1, d, steps = 8) => Array.from({ length: steps + 1 }, (_, k) => { const x = x0 + (x1 - x0) * k / steps; return [x, yIn(x) - d]; });
  const finish = (gg, flip) => {
    if (flip) { const ix = gg.index.array; for (let k = 0; k < ix.length; k += 3) { const t = ix[k + 1]; ix[k + 1] = ix[k + 2]; ix[k + 2] = t; } }
    gg.computeVertexNormals(); return gg;
  };
  const onSide = (shape, sg, off, origin = null, depth = 0) => {
    let gg;
    if (depth) { gg = new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: true, bevelThickness: 0.008, bevelSize: 0.008, bevelSegments: 2, curveSegments: 24 }); gg.translate(0, 0, 0.008); }
    else gg = new THREE.ShapeGeometry(shape, 24);
    gg.deleteAttribute('uv'); gg.deleteAttribute('normal');
    gg = mergeVertices(gg, 1e-5); gg = refine(gg, 0.045);
    const Q = gg.attributes.position;
    for (let k = 0; k < Q.count; k++) {
      const x = Q.getX(k), y = Q.getY(k), zl = Q.getZ(k);
      Q.setXYZ(k, x - (origin?.x || 0), y - (origin?.y || 0), sg * (hw(x, y) + off + zl) - (origin?.z || 0));
    }
    return finish(gg, sg < 0);
  };
  const outlineOnSide = (shape, sg, off, origin = null) => {
    const pts = shape.getPoints(40).map(p => new THREE.Vector3(p.x - (origin?.x || 0), p.y - (origin?.y || 0), sg * (hw(p.x, p.y) + off) - (origin?.z || 0)));
    pts.push(pts[0].clone()); return new THREE.BufferGeometry().setFromPoints(pts);
  };

  const doorShape = poly([[0.60, 0.37], [0.63, 0.70], ...roofLine(0.12, -0.64, 0.05, 10).map((p, k) => k === 0 ? [0.12, p[1]] : p), [-0.80, 0.60], [-0.76, 0.37]]);
  const doorWin = poly([[0.47, 0.80], ...roofLine(0.08, -0.56, 0.095, 10), [-0.64, 0.86]]);
  const quarter = poly([[-0.74, 0.87], ...roofLine(-0.73, -1.1, 0.075, 5), [-1.0, 0.885]]);
  const intake = poly([[-0.85, 0.58], [-0.85, 0.83], [-1.3, 0.9], [-1.13, 0.76]]);
  const skirt = poly([[0.94, 0.30], [0.94, 0.36], [-0.86, 0.41], [-0.86, 0.30]]);

  const doors = [];
  const gapMat = new THREE.LineBasicMaterial({ color: 0x020203, transparent: true, opacity: 0 });
  const glowMat = new THREE.LineBasicMaterial({ color: new THREE.Color(0xa294ff).multiplyScalar(3), transparent: true, opacity: 0, toneMapped: false });
  for (const sg of [1, -1]) {
    group.add(new THREE.Mesh(onSide(quarter, sg, 0.004), glass));
    group.add(new THREE.Mesh(onSide(intake, sg, 0.004), trimM));
    group.add(new THREE.Mesh(onSide(skirt, sg, 0.004), trimM));
    group.add(new THREE.Mesh(onSide(doorShape, sg, 0.003), recessM));
    group.add(new THREE.Line(outlineOnSide(doorShape, sg, 0.006), glowMat));
    group.add(new THREE.Line(outlineOnSide(doorShape, sg, 0.005), gapMat));
    const hinge = new THREE.Vector3(0.6, 0.62, sg * hw(0.6, 0.62));
    const pivot = new THREE.Group(); pivot.position.copy(hinge);
    pivot.add(new THREE.Mesh(onSide(doorShape, sg, 0.004, hinge, 0.04), doorPaint));
    pivot.add(new THREE.Mesh(onSide(doorWin, sg, 0.064, hinge), glass));
    group.add(pivot);
    doors.push({ pivot, sg });
  }

  // ---------- parabrezza e lunotto motore (sul tetto) ----------
  const topGlass = (x0, x1, wf, nu = 26, nv = 14, lift = 0.007) => {
    const pos = [], idx = [];
    for (let i = 0; i <= nu; i++) {
      const u = i / nu, x = x0 + (x1 - x0) * u, y = yOut(x);
      const half = wf(x, y, u);
      for (let j = 0; j <= nv; j++) {
        const z = (j / nv * 2 - 1) * half;
        pos.push(x, y + crownAt(x, y, z) + lift, z * taper(x, y) / taper(x, y));
      }
    }
    for (let i = 0; i < nu; i++) for (let j = 0; j < nv; j++) { const p0 = i * (nv + 1) + j, p1 = p0 + nv + 1; idx.push(p0, p1, p0 + 1, p1, p1 + 1, p0 + 1); }
    const gg = new THREE.BufferGeometry(); gg.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); gg.setIndex(idx); gg.computeVertexNormals(); return gg;
  };
  group.add(new THREE.Mesh(topGlass(0.50, -0.34, (x, y, u) => DEPTH / 2 * taper(x, y) * 0.86 * (1 - 0.05 * Math.pow(2 * u - 1, 4))), glass));
  const eng = topGlass(-1.36, -1.95, () => 0.4, 16, 8);
  group.add(new THREE.Mesh(eng, glass));
  // lamelle sul cofano motore
  const slatPts = [];
  for (let k = 1; k < 6; k++) { const x = -1.36 - 0.59 * k / 6, y = yOut(x) + crownAt(x, yOut(x), 0) + 0.012; slatPts.push(x, y, -0.38, x, y, 0.38); }
  const slatG = new THREE.BufferGeometry(); slatG.setAttribute('position', new THREE.Float32BufferAttribute(slatPts, 3));
  group.add(new THREE.LineSegments(slatG, gapMat));

  // ---------- ruote ----------
  const tireM = new THREE.MeshStandardMaterial({ color: 0x0a0a0a, roughness: 0.92, transparent: true, opacity: 0 });
  const rimM = new THREE.MeshPhysicalMaterial({ color: 0x9a9daa, metalness: 1, roughness: 0.22, clearcoat: 1, transparent: true, opacity: 0 });
  const calM = new THREE.MeshStandardMaterial({ color: 0xa294ff, emissive: 0x4a3cff, emissiveIntensity: 0.4, metalness: 0.4, roughness: 0.35, transparent: true, opacity: 0 });
  fadeMats.push(tireM, rimM, calM);
  const wheels = [];
  const tireProfile = [];
  for (let k = 0; k <= 16; k++) { const ang = -Math.PI / 2 + Math.PI * k / 16; tireProfile.push(new THREE.Vector2(WRAD - 0.06 + Math.cos(ang) * 0.06, Math.sin(ang) * 0.14)); }
  const tireGeo = new THREE.LatheGeometry([new THREE.Vector2(0.26, -0.14), ...tireProfile, new THREE.Vector2(0.26, 0.14)], 56);
  const rimGeo = new THREE.CylinderGeometry(0.255, 0.255, 0.05, 48);
  const spokeGeo = new THREE.BoxGeometry(0.2, 0.034, 0.03);
  for (const wx of [WF, WR]) for (const sg of [1, -1]) {
    const w = new THREE.Group();
    w.position.set(wx, WY, sg * (hw(wx, WY) - 0.13));
    const t = new THREE.Mesh(tireGeo, tireM); t.rotation.x = Math.PI / 2; w.add(t);
    const spin = new THREE.Group(); w.add(spin);
    const r = new THREE.Mesh(rimGeo, rimM); r.rotation.x = Math.PI / 2; r.position.z = sg * 0.1; spin.add(r);
    for (let k = 0; k < 10; k++) {
      const sp = new THREE.Mesh(spokeGeo, rimM); const ang = k / 10 * Math.PI * 2;
      sp.position.set(Math.cos(ang) * 0.14, Math.sin(ang) * 0.14, sg * 0.125); sp.rotation.z = ang; spin.add(sp);
    }
    const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.06, 20), rimM); hub.rotation.x = Math.PI / 2; hub.position.z = sg * 0.12; spin.add(hub);
    const cal = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.09, 0.05), calM); cal.position.set(0.12, 0.14, sg * 0.06); cal.rotation.z = -0.6; w.add(cal);
    group.add(w); wheels.push(spin);
  }

  // ---------- luci (fari e fanali seguono la superficie) ----------
  const bodyMesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial());
  bodyMesh.updateMatrixWorld();
  const ray = new THREE.Raycaster();
  const hit = (from, dir) => { ray.set(from, dir); const h = ray.intersectObject(bodyMesh)[0]; return h ? h.point.add(h.face.normal.clone().multiplyScalar(0.012)) : null; };
  const headM = new THREE.MeshBasicMaterial({ color: new THREE.Color(0xe9efff).multiplyScalar(0.05), toneMapped: false, transparent: true, opacity: 0 });
  const tailM = new THREE.MeshBasicMaterial({ color: new THREE.Color(0xff2a3a).multiplyScalar(0.05), toneMapped: false, transparent: true, opacity: 0 });
  const tube = (pts, mat, r = 0.012) => { const ok = pts.filter(Boolean); if (ok.length < 2) return; group.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(ok), 40, r, 6), mat)); };
  const down = new THREE.Vector3(0, -1, 0), back = new THREE.Vector3(-1, 0, 0), fwd = new THREE.Vector3(1, 0, 0);
  for (const sg of [1, -1]) {
    // faro a "Y": una linea lungo il cofano e due tratti che scendono
    tube([0.36, 0.48, 0.6, 0.72].map(z => hit(new THREE.Vector3(1.98 + (0.72 - z) * 0.18, 3, sg * z), down)), headM, 0.014);
    tube([[2.03, 0.36], [2.18, 0.47], [2.28, 0.56]].map(([x, z]) => hit(new THREE.Vector3(x, 3, sg * z), down)), headM, 0.011);
    tube([[1.98, 0.72], [2.12, 0.66], [2.26, 0.62]].map(([x, z]) => hit(new THREE.Vector3(x, 3, sg * z), down)), headM, 0.011);
  }
  tube([-0.8, -0.5, -0.2, 0, 0.2, 0.5, 0.8].map(z => hit(new THREE.Vector3(-4, 0.72, z), fwd)), tailM, 0.016);
  for (const sg of [1, -1]) tube([[0.55, 0.62], [0.7, 0.68], [0.82, 0.62]].map(([z, y]) => hit(new THREE.Vector3(-4, y, sg * z), fwd)), tailM, 0.011);

  // ---------- schizzo a linee (come il logo) ----------
  const sketchMat = new THREE.LineBasicMaterial({ color: new THREE.Color(0xdfe2ff).multiplyScalar(2.4), transparent: true, opacity: 1, toneMapped: false });
  const sketch = [];
  const mkLine = arr => { const geo = new THREE.BufferGeometry().setFromPoints(arr); const l = new THREE.Line(geo, sketchMat); l.userData.n = arr.length; group.add(l); sketch.push(l); };
  for (const sg of [1, -1]) mkLine(outline.map(p => new THREE.Vector3(p.x, p.y, sg * hw(p.x, p.y) * 1.002)));
  const top = [];
  for (let k = 0; k < outline.length; k++) {
    const p = outline[k], q = outline[(k + 1) % outline.length], o = outline[(k - 1 + outline.length) % outline.length];
    if (p.y < 0.46) continue;
    let nx = q.y - o.y, ny = -(q.x - o.x); const l = Math.hypot(nx, ny) || 1; nx /= l; ny /= l;
    if (cw) { nx = -nx; ny = -ny; }
    top.push(new THREE.Vector3(p.x + nx * BS, p.y + ny * BS + CROWN * smooth(0.6, 0.98, p.y + ny * BS), 0));
  }
  top.sort((u, v) => v.x - u.x);
  mkLine(top);
  for (const wx of [WF, WR]) for (const sg of [1, -1]) {
    const pts = []; for (let k = 0; k <= 48; k++) { const a2 = k / 48 * Math.PI * 2; pts.push(new THREE.Vector3(wx + Math.cos(a2) * WRAD, WY + Math.sin(a2) * WRAD, sg * (hw(wx, WY) + 0.02))); }
    mkLine(pts);
  }
  sketch.forEach(l => l.geometry.setDrawRange(0, 0));

  let bodyOn = -1;
  return {
    group,
    setSketch(draw, alpha) {
      sketchMat.opacity = alpha;
      for (const l of sketch) { l.visible = alpha > 0.01; l.geometry.setDrawRange(0, Math.ceil(l.userData.n * draw)); }
    },
    setBody(v) {
      if (v === bodyOn) return; bodyOn = v;
      for (const mt of fadeMats) { mt.opacity = v; const tr = v < 0.999; if (mt.transparent !== tr) { mt.transparent = tr; mt.needsUpdate = true; } }
      glass.opacity = v; gapMat.opacity = v * 0.9;
    },
    setLights(v) {
      headM.opacity = tailM.opacity = Math.max(0.0001, v);
      headM.color.setRGB(0.91, 0.94, 1).multiplyScalar(0.3 + v * 5);
      tailM.color.setRGB(1, 0.16, 0.22).multiplyScalar(0.3 + v * 4);
    },
    setDoors(o) {
      for (const d of doors) { d.pivot.rotation.set(d.sg * o * 0.2, 0, -o * 1.22); }
      recessM.emissiveIntensity = o * 0.04;
      glowMat.opacity = o * 0.9;
    },
    spin(p) { for (const w of wheels) w.rotation.z = -p * 9; },
  };
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

// ---------- ambiente da studio: strisce di luce che si riflettono sulla vernice ----------
function studioEnv(renderer) {
  const s = new THREE.Scene();
  s.add(new THREE.Mesh(new THREE.BoxGeometry(40, 16, 40), new THREE.MeshBasicMaterial({ color: 0x040406, side: THREE.BackSide })));
  const strip = (w, h, pos, rot, intensity, col = 0xffffff) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(col).multiplyScalar(intensity), side: THREE.DoubleSide }));
    m.position.set(...pos); m.rotation.set(...rot); s.add(m);
  };
  for (let i = -3; i <= 3; i++) strip(22, 0.45, [0, 7.5, i * 2.4], [Math.PI / 2, 0, 0], 4);
  strip(14, 4, [0, 3, -18], [0, 0, 0], 1.4, 0xa294ff);
  strip(14, 2.2, [0, 2.5, 18], [0, Math.PI, 0], 1.6);
  strip(1.2, 9, [-18, 4, -4], [0, Math.PI / 2, 0], 3);
  strip(1.2, 9, [-18, 4, 5], [0, Math.PI / 2, 0], 3);
  strip(1.2, 9, [18, 4, 0], [0, -Math.PI / 2, 0], 2.5, 0xc8d4ff);
  strip(30, 0.3, [0, 0.6, -15], [0, 0, 0], 2.5);
  strip(40, 0.5, [0, 1.6, 19.5], [0, Math.PI, 0], 2.2);
  strip(40, 0.5, [19.5, 1.6, 0], [0, -Math.PI / 2, 0], 2.0);
  strip(40, 0.5, [-19.5, 1.6, 0], [0, Math.PI / 2, 0], 2.0);
  strip(40, 0.25, [0, 3.2, -19.5], [0, 0, 0], 1.5, 0xa294ff);
  const pm = new THREE.PMREMGenerator(renderer);
  const tex = pm.fromScene(s, 0.02).texture; pm.dispose();
  return tex;
}

function radial(inner, outer) {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const x = c.getContext('2d'); const g = x.createRadialGradient(128, 128, 10, 128, 128, 128);
  g.addColorStop(0, inner); g.addColorStop(0.55, 'rgba(0,0,0,.7)'); g.addColorStop(1, outer);
  x.fillStyle = g; x.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c); return t;
}
