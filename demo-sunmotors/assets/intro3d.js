/* Sunmotors – intro 3D.
   Auto disegnata via codice (nessun modello di terzi, nessun marchio), luce da "studio" HDR generata al volo,
   pavimento riflettente, ombra di contatto. three.js r160 da CDN (vedi importmap nella home). */
import * as THREE from 'three';
import { Reflector } from 'three/addons/objects/Reflector.js';

const canvas = document.getElementById('gl');
const root = document.documentElement;

/* ---------- utilità ---------- */
function curve(pts) { // interpolazione cubica monotona (x crescente)
  pts = pts.slice().sort((a, b) => a[0] - b[0]);
  const n = pts.length, xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const d = [], m = [];
  for (let i = 0; i < n - 1; i++) d.push((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]));
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
    const a = m[i] / d[i], b = m[i + 1] / d[i], s = a * a + b * b;
    if (s > 9) { const t = 3 / Math.sqrt(s); m[i] = t * a * d[i]; m[i + 1] = t * b * d[i]; }
  }
  return x => {
    if (x <= xs[0]) return ys[0];
    if (x >= xs[n - 1]) return ys[n - 1];
    let i = 0; while (x > xs[i + 1]) i++;
    const h = xs[i + 1] - xs[i], t = (x - xs[i]) / h, t2 = t * t, t3 = t2 * t;
    return (2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i] + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1];
  };
}
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };

/* ---------- dimensioni (metri) ---------- */
const L2 = 2.2, W2 = 0.9, WB_F = 1.36, WB_R = -1.30, TR = 0.335, WZ = 0.775;

const yTop = curve([[2.2, .62], [2.16, .705], [2.04, .765], [1.6, .835], [1.1, .905], [.86, .935], [.55, 1.10], [.25, 1.29], [.05, 1.375], [-.25, 1.425], [-.75, 1.43], [-1.15, 1.395], [-1.45, 1.30], [-1.72, 1.15], [-1.95, 1.055], [-2.1, 1.02], [-2.2, .90]]);
const yBelt = curve([[2.2, .58], [2.05, .68], [1.6, .79], [.86, .875], [0, .905], [-1.0, .93], [-1.7, .965], [-2.05, .97], [-2.2, .88]]);
const yBase = curve([[2.2, .30], [2.08, .225], [1.8, .20], [0, .19], [-1.8, .21], [-2.1, .27], [-2.2, .36]]);
const archR = TR + .07;
function yBottom(x) {
  let y = yBase(x);
  for (const cx of [WB_F, WB_R]) {
    const dx = x - cx;
    if (Math.abs(dx) < archR) y = Math.max(y, TR + Math.sqrt(archR * archR - dx * dx) * .97);
  }
  return y;
}
function plan(x) { // pianta arrotondata
  const u = Math.abs(x) / L2, p = x > 0 ? 7.5 : 7;
  return Math.pow(Math.max(0, 1 - Math.pow(u, p)), 1 / p);
}
const cabin = x => smooth(1.05, .45, x) * smooth(-2.12, -1.6, x); // 1 dove c'è l'abitacolo
function halfW(x) { return W2 * plan(x); }
function topW(x) { const w = halfW(x); return w - .03 - cabin(x) * (w - .80); }

/* sezione (lato destro, dal fondo al tetto): punti [z, y, s, zona] */
const NB = 6, NC = 5, NS = 12, NL = 3, NU = 26;
function section(x) {
  const yb = yBottom(x), ys = yBelt(x), yt = Math.max(yTop(x), ys + .02);
  const w = halfW(x), a = Math.min(topW(x), w - .005);
  const pts = [];
  const rb = Math.min(.11, (ys - yb) * .45, w * .5);
  for (let i = 0; i < NB; i++) { const t = i / NB; pts.push([t * (w - rb), yb, 0, 0]); }
  for (let i = 0; i < NC; i++) { const t = i / NC * Math.PI / 2; pts.push([w - rb + Math.sin(t) * rb, yb + rb - Math.cos(t) * rb, 0, 0]); }
  for (let i = 0; i < NS; i++) {
    const t = i / NS, y = yb + rb + t * (ys - .035 - yb - rb);
    const bulge = .018 * Math.sin(Math.PI * t) - .02 * t * t;
    pts.push([w + bulge, y, 0, 0]);
  }
  for (let i = 0; i < NL; i++) { const t = i / NL; const e = t * Math.PI / 2; pts.push([a + (w - .02 - a) * Math.cos(e), ys - .035 + .035 * Math.sin(e), 0, 1]); }
  const b = yt - ys, n = 2.6 + 6 * (1 - cabin(x)), tumble = .16 * cabin(x);
  for (let i = 0; i <= NU; i++) {
    const th = i / NU * Math.PI / 2;
    const c = Math.pow(Math.cos(th), 2 / n), s = Math.pow(Math.sin(th), 2 / n);
    pts.push([a * c * (1 - tumble * s), ys + b * s, s, 2]);
  }
  return pts;
}

/* colori per zona */
function classify(x, y, z, s, zone) {
  const az = Math.abs(z), ys = yBelt(x);
  // vetri
  if (zone === 2 && y > ys + .03 && cabin(x) > .5) {
    const inRoofX = x < .2 && x > -1.22;
    if (s > .86) { if (!inRoofX && x < .80 && x > -1.93) return 'glass'; if (inRoofX) return 'roof'; }
    else if (s < .76 && x < .80 && x > -1.30 && !(x > -.46 && x < -.37)) return 'glass';
    else if (s < .76 && x <= -1.30 && x > -1.62 && s < .55) return 'glass';
    return 'body';
  }
  if (x > 2.04 && y > .29 && y < .5 && az < .52) return 'grille';
  if (x > 2.0 && y > .24 && y < .3 && az < .78) return 'trim';
  // linee delle porte e del cofano
  if (zone !== 2 || y < ys + .03) {
    for (const xd of [.80, -.415, -1.33]) if (Math.abs(x - xd) < .0065 && y > yBottom(x) + .09 && y < ys + .01) return 'gap';
    if (y > ys - .01 && x > .86 && x < 1.98 && Math.abs(az - (halfW(x) - .1)) < .006) return 'gap';
  }
  if (y < yBottom(x) + .085 && !(x > 1.9 || x < -2.0)) return 'trim';
  if (x < -2.04 && y < .45) return 'trim';
  return 'body';
}
function lightOf(x, y, z) {
  const az = Math.abs(z);
  if (x > 1.86 && y > .60 && y < .705 && az > .40 && az < .86) return [1, .97, .92];
  if (x > 2.0 && y > .245 && y < .27 && az > .25 && az < .7) return [.9, .85, .8];
  if (x < -1.98 && y > .875 && y < .935 && (az > .30 || (y > .895 && y < .912))) return [1, .08, .04];
  return null;
}

function ringOf(x) {
  const sec = section(x);
  // anello: fondo-sinistra -> tetto -> fondo-destra (la cucitura resta sotto, invisibile)
  return sec.map(p => [-p[0], p[1], p[2], p[3]]).concat(sec.slice().reverse().slice(1));
}
const NX = 160;
const xAt = u => -L2 + 2 * L2 * (.5 - .5 * Math.cos(Math.PI * u));

/* texture della carrozzeria calcolate nello spazio UV: colore (vernice/vetro/plastica), ruvidità+metallo, luci */
function bodyMaps(TW, TH) {
  const cC = document.createElement('canvas'); cC.width = TW; cC.height = TH;
  const cR = document.createElement('canvas'); cR.width = TW; cR.height = TH;
  const cE = document.createElement('canvas'); cE.width = TW; cE.height = TH;
  const dC = cC.getContext('2d').createImageData(TW, TH), dR = cR.getContext('2d').createImageData(TW, TH), dE = cE.getContext('2d').createImageData(TW, TH);
  const C = { body: [255, 255, 255], roof: [255, 255, 255], glass: [4, 5, 6], grille: [7, 7, 8], trim: [12, 12, 13], gap: [10, 10, 10] };
  const RM = { body: [0, 70, 150], roof: [0, 70, 150], glass: [0, 10, 0], grille: [0, 150, 30], trim: [0, 170, 10], gap: [0, 200, 0] };
  for (let i = 0; i < TW; i++) {
    const u = (i + .5) / TW, x = xAt(u), ring = ringOf(x), R = ring.length - 1;
    for (let j = 0; j < TH; j++) {
      const v = 1 - (j + .5) / TH; // flipY
      const f = v * R, k = Math.min(R - 1, Math.floor(f)), t = f - k, p = ring[k], q = ring[k + 1];
      const z = p[0] + (q[0] - p[0]) * t, y = p[1] + (q[1] - p[1]) * t, s = p[2] + (q[2] - p[2]) * t;
      const zone = Math.max(p[3], q[3]) === 2 && Math.min(p[3], q[3]) === 2 ? 2 : (p[3] === 2 || q[3] === 2 ? 1 : p[3]);
      const kk = classify(x, y, z, s, zone), o = (j * TW + i) * 4;
      const c = C[kk], r = RM[kk];
      dC.data[o] = c[0]; dC.data[o + 1] = c[1]; dC.data[o + 2] = c[2]; dC.data[o + 3] = 255;
      dR.data[o] = r[0]; dR.data[o + 1] = r[1]; dR.data[o + 2] = r[2]; dR.data[o + 3] = 255;
      const l = lightOf(x, y, z);
      if (l) { dE.data[o] = l[0] * 255; dE.data[o + 1] = l[1] * 255; dE.data[o + 2] = l[2] * 255; }
      dE.data[o + 3] = 255;
    }
  }
  cC.getContext('2d').putImageData(dC, 0, 0); cR.getContext('2d').putImageData(dR, 0, 0); cE.getContext('2d').putImageData(dE, 0, 0);
  const tx = c => { const t = new THREE.CanvasTexture(c); t.anisotropy = 4; return t; };
  const map = tx(cC); map.colorSpace = THREE.SRGBColorSpace;
  const em = tx(cE); em.colorSpace = THREE.SRGBColorSpace;
  return { map, rm: tx(cR), em };
}

function buildBody(paint, lite) {
  const pos = [], uv = [], idx = [];
  let RN = 0;
  for (let i = 0; i <= NX; i++) {
    const u = i / NX, x = xAt(u), ring = ringOf(x); RN = ring.length;
    ring.forEach((p, j) => { pos.push(x, p[1], p[0]); uv.push(u, j / (ring.length - 1)); });
  }
  for (let i = 0; i < NX; i++) for (let j = 0; j < RN - 1; j++) {
    const a = i * RN + j, b = a + 1, c = a + RN, d = c + 1;
    idx.push(a, b, c, b, d, c);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx); g.computeVertexNormals();
  // cucitura sotto (inizio e fine dell'anello): normali mediate, niente spigolo visibile
  const nr = g.attributes.normal;
  for (let i = 0; i <= NX; i++) {
    const a = i * RN, b = a + RN - 1;
    const nx = nr.getX(a) + nr.getX(b), ny = nr.getY(a) + nr.getY(b), nz = nr.getZ(a) + nr.getZ(b), l = Math.hypot(nx, ny, nz) || 1;
    nr.setXYZ(a, nx / l, ny / l, nz / l); nr.setXYZ(b, nx / l, ny / l, nz / l);
  }
  const m = bodyMaps(lite ? 512 : 1024, lite ? 512 : 1024);
  paint.map = m.map; paint.roughnessMap = m.rm; paint.metalnessMap = m.rm;
  paint.emissiveMap = m.em; paint.emissive = new THREE.Color(0xffffff); paint.emissiveIntensity = 2.2;
  paint.roughness = 1; paint.metalness = 1;
  return new THREE.Mesh(g, paint);
}

/* vernice metallizzata con trasparente (clearcoat); vetri e plastiche dalle texture */
function makePaint(hex) {
  return new THREE.MeshPhysicalMaterial({ color: hex, metalness: 1, roughness: 1, clearcoat: 1, clearcoatRoughness: .035, envMapIntensity: 1.35 });
}

function makeWheel(env) {
  const g = new THREE.Group();
  // pneumatico
  const prof = [];
  const R = TR, w = .115, r0 = .228;
  for (let i = 0; i <= 16; i++) { const t = i / 16 * Math.PI; prof.push(new THREE.Vector2(R - .035 + Math.sin(t) * .035, -w + .035 - Math.cos(t) * .0)); }
  const tp = [];
  tp.push(new THREE.Vector2(r0 + .01, -w + .01));
  for (let i = 0; i <= 10; i++) { const t = i / 10 * Math.PI / 2; tp.push(new THREE.Vector2(R - .04 + Math.sin(t) * .04, -w + .04 - Math.cos(t) * .04)); }
  for (let i = 0; i <= 10; i++) { const t = i / 10 * Math.PI / 2; tp.push(new THREE.Vector2(R - .04 + Math.cos(t) * .04, w - .04 + Math.sin(t) * .04)); }
  tp.push(new THREE.Vector2(r0 + .01, w - .01));
  const tyre = new THREE.Mesh(new THREE.LatheGeometry(tp, 64), new THREE.MeshStandardMaterial({ color: 0x0d0d0e, roughness: .86, metalness: 0 }));
  tyre.rotation.x = Math.PI / 2; g.add(tyre);
  // cerchio: disco con 5 razze doppie
  const sh = new THREE.Shape(); sh.absarc(0, 0, r0, 0, Math.PI * 2, false);
  for (let k = 0; k < 10; k++) {
    const a0 = k / 10 * Math.PI * 2 + .14, a1 = a0 + Math.PI * 2 / 10 - .2;
    const h = new THREE.Path();
    const ri = .07, ro = r0 - .028;
    h.moveTo(Math.cos(a0) * ri, Math.sin(a0) * ri);
    h.lineTo(Math.cos(a0) * ro, Math.sin(a0) * ro);
    h.absarc(0, 0, ro, a0, a1, false);
    h.lineTo(Math.cos(a1) * ri, Math.sin(a1) * ri);
    h.absarc(0, 0, ri, a1, a0, true);
    sh.holes.push(h);
  }
  const rimM = new THREE.MeshPhysicalMaterial({ color: 0xe4e6ea, metalness: .85, roughness: .28, clearcoat: .5, clearcoatRoughness: .1, envMapIntensity: 1.6 });
  const rim = new THREE.Mesh(new THREE.ExtrudeGeometry(sh, { depth: .03, bevelEnabled: true, bevelThickness: .012, bevelSize: .008, bevelSegments: 3, curveSegments: 48 }), rimM);
  rim.position.z = w - .07; g.add(rim);
  const barrel = new THREE.Mesh(new THREE.CylinderGeometry(r0 + .004, r0 + .004, w * 2 - .03, 48, 1, true), new THREE.MeshStandardMaterial({ color: 0x1a1b1d, metalness: .8, roughness: .5, side: THREE.DoubleSide }));
  barrel.rotation.x = Math.PI / 2; g.add(barrel);
  const back = new THREE.Mesh(new THREE.CircleGeometry(r0, 32), new THREE.MeshStandardMaterial({ color: 0x0a0a0b, roughness: .9 }));
  back.position.z = -w + .03; g.add(back);
  const disc = new THREE.Mesh(new THREE.CylinderGeometry(.17, .17, .025, 40), new THREE.MeshStandardMaterial({ color: 0x6d6e72, metalness: .9, roughness: .35 }));
  disc.rotation.x = Math.PI / 2; disc.position.z = w - .13; g.add(disc);
  const cal = new THREE.Mesh(new THREE.BoxGeometry(.11, .07, .06), new THREE.MeshStandardMaterial({ color: 0xd9822b, metalness: .2, roughness: .45 }));
  cal.position.set(.11, .11, w - .125); cal.rotation.z = -Math.PI / 4; g.add(cal);
  const hub = new THREE.Mesh(new THREE.CylinderGeometry(.045, .05, .03, 24), rimM);
  hub.rotation.x = Math.PI / 2; hub.position.z = w - .035; g.add(hub);
  g.traverse(o => { if (o.isMesh) o.castShadow = true; });
  return g;
}

/* ambiente HDR da "studio": stanza scura con pannelli luminosi */
function studioEnv(renderer) {
  const s = new THREE.Scene();
  const room = new THREE.Mesh(new THREE.BoxGeometry(30, 14, 30), new THREE.MeshBasicMaterial({ color: 0x0b0b0c, side: THREE.BackSide }));
  room.position.y = 5; s.add(room);
  const panel = (w, h, x, y, z, rx, ry, k, tint) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(tint || 0xffffff).multiplyScalar(k), side: THREE.DoubleSide }));
    m.position.set(x, y, z); m.rotation.set(rx, ry, 0); s.add(m);
  };
  panel(10, 3, 0, 9, 0, Math.PI / 2, 0, 3.4);              // softbox sopra
  panel(10, .45, 0, 8.6, 3.6, Math.PI / 2, 0, 6);           // strisce sopra
  panel(10, .45, 0, 8.6, -3.6, Math.PI / 2, 0, 6);
  panel(.8, 6, 12, 4, 0, 0, -Math.PI / 2, 3.2);            // strisce verticali ai lati
  panel(.8, 6, -12, 4, 0, 0, Math.PI / 2, 2.6);
  panel(22, .9, 0, 3.2, 13, 0, Math.PI, 3.0, 0xffd2a0);    // striscia lunga calda: riga di luce sulle fiancate
  panel(22, .7, 0, 1.4, 13, 0, Math.PI, 1.2, 0xffffff);
  panel(22, .9, 0, 3.2, -13, 0, 0, 2.2, 0xcfdcff);         // e dietro, fredda
  panel(5, 3, 0, 4, 14, 0, Math.PI, .9, 0xff9a40);         // il sole
  const pm = new THREE.PMREMGenerator(renderer);
  const rt = pm.fromScene(s, .02);
  pm.dispose();
  return rt.texture;
}

function softShadowTex() {
  const c = document.createElement('canvas'); c.width = 256; c.height = 128;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(128, 64, 8, 128, 64, 120);
  g.addColorStop(0, 'rgba(0,0,0,.95)'); g.addColorStop(.45, 'rgba(0,0,0,.7)'); g.addColorStop(1, 'rgba(0,0,0,0)');
  x.fillStyle = g; x.save(); x.scale(1, .5); x.fillRect(0, 0, 256, 256); x.restore();
  const t = new THREE.CanvasTexture(c); return t;
}
function floorFadeTex() {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(128, 128, 20, 128, 128, 128);
  g.addColorStop(0, 'rgba(10,10,11,.35)'); g.addColorStop(.55, 'rgba(10,10,11,.82)'); g.addColorStop(1, 'rgba(10,10,11,1)');
  x.fillStyle = g; x.fillRect(0, 0, 256, 256);
  return new THREE.CanvasTexture(c);
}

const FloorShader = {
  name: 'FloorShader',
  uniforms: { color: { value: null }, tDiffuse: { value: null }, textureMatrix: { value: null }, strength: { value: .42 } },
  vertexShader: `uniform mat4 textureMatrix; varying vec4 vUv; varying vec2 vP;
    #include <common>
    void main(){ vUv = textureMatrix * vec4(position,1.0); vP = position.xy; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
  fragmentShader: `uniform vec3 color; uniform sampler2D tDiffuse; uniform float strength; varying vec4 vUv; varying vec2 vP;
    void main(){
      vec4 base = texture2DProj(tDiffuse, vUv);
      float r = length(vP * vec2(1.0, 1.35));
      float a = (1.0 - smoothstep(1.2, 6.5, r));
      // la riflessione si spegne allontanandosi dall'auto: pavimento lucido ma non uno specchio
      gl_FragColor = vec4(base.rgb * color * strength, a);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`
};

/* ---------- avvio ---------- */
export function start(opts = {}) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  const dprMax = opts.dpr || 1.75;
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, dprMax));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.18;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  scene.environment = studioEnv(renderer);

  const camera = new THREE.PerspectiveCamera(28, 1, .1, 60);

  const car = new THREE.Group(); scene.add(car);
  const paint = makePaint(opts.color || 0xa7a6a3);
  const body = buildBody(paint, opts.lite);
  car.add(body);
  // sottoscocca e passaruota scuri
  const under = new THREE.Mesh(new THREE.BoxGeometry(4.0, .5, 1.22), new THREE.MeshBasicMaterial({ color: 0x050505 }));
  under.position.set(0, .45, 0); car.add(under);
  // specchietti
  const mirG = new THREE.SphereGeometry(1, 24, 16);
  for (const sgn of [1, -1]) {
    const m = new THREE.Mesh(mirG, paint); m.scale.set(.075, .05, .085);
    m.position.set(.62, yBelt(.62) + .1, sgn * (topW(.62) + .1)); car.add(m);
    const arm = new THREE.Mesh(new THREE.BoxGeometry(.06, .025, .12), new THREE.MeshStandardMaterial({ color: 0x111111, roughness: .6 }));
    arm.position.set(.64, yBelt(.62) + .07, sgn * (topW(.62) + .04)); car.add(arm);
    // maniglie
    for (const hx of [-.05, -.9]) {
      const h = new THREE.Mesh(new THREE.BoxGeometry(.2, .022, .02), new THREE.MeshStandardMaterial({ color: 0xd8d8da, metalness: 1, roughness: .2 }));
      h.position.set(hx, yBelt(hx) - .085, sgn * (halfW(hx) + .004)); car.add(h);
    }
    // modanatura cromata sotto i vetri
    const tg = new THREE.BoxGeometry(2.1, .012, .012);
    const t = new THREE.Mesh(tg, new THREE.MeshStandardMaterial({ color: 0xcfd0d3, metalness: 1, roughness: .15 }));
    t.position.set(-.25, yBelt(-.25) + .03, sgn * (topW(-.25) + .002)); car.add(t);
  }
  const wheels = [];
  for (const [x, sgn] of [[WB_F, 1], [WB_F, -1], [WB_R, 1], [WB_R, -1]]) {
    const w = makeWheel(); w.position.set(x, TR, sgn * WZ);
    if (sgn < 0) w.rotation.y = Math.PI;
    car.add(w); wheels.push(w);
  }
  body.castShadow = true;

  // pavimento riflettente che sfuma nel fondo della pagina
  if (!opts.lite) {
    const mirror = new Reflector(new THREE.PlaneGeometry(16, 16), { clipBias: .003, textureWidth: 1024, textureHeight: 1024, color: 0xffffff, shader: FloorShader });
    mirror.material.transparent = true; mirror.material.depthWrite = false;
    mirror.rotation.x = -Math.PI / 2; scene.add(mirror);
  }
  const sh = new THREE.Mesh(new THREE.PlaneGeometry(5.6, 2.9), new THREE.MeshBasicMaterial({ map: softShadowTex(), transparent: true, depthWrite: false, opacity: .9 }));
  sh.rotation.x = -Math.PI / 2; sh.position.y = .004; sh.renderOrder = 2; car.add(sh);

  // luci (oltre all'ambiente)
  const key = new THREE.DirectionalLight(0xfff1de, 1.6); key.position.set(3, 7, 4); scene.add(key);
  const rim = new THREE.DirectionalLight(0xffb36b, 1.2); rim.position.set(-6, 2.5, -5); scene.add(rim);
  /* camera, input */
  let W = 0, Hh = 0, portrait = false;
  function resize() {
    const r = canvas.getBoundingClientRect(); W = Math.max(1, r.width); Hh = Math.max(1, r.height);
    renderer.setSize(W, Hh, false);
    camera.aspect = W / Hh; portrait = camera.aspect < .9;
    camera.fov = portrait ? 34 : 26;
    camera.updateProjectionMatrix();
  }
  addEventListener('resize', resize, { passive: true }); resize();

  let rot = opts.angle != null ? opts.angle : -.62, vel = 0, drag = false, lx = 0, idle = 0;
  const auto = opts.still ? 0 : .11;
  canvas.addEventListener('pointerdown', e => { drag = true; lx = e.clientX; vel = 0; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', e => { if (!drag) return; const d = (e.clientX - lx) / W * 4; rot += d; vel = d * 60; lx = e.clientX; idle = 0; });
  const up = () => { drag = false; };
  canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up);

  let scrollP = 0;
  const onScroll = () => { scrollP = clamp(scrollY / (innerHeight * .9), 0, 1); };
  addEventListener('scroll', onScroll, { passive: true }); onScroll();

  function place(p) {
    // distanza che fa stare l'auto nell'inquadratura; su desktop l'auto sta a destra del testo, sul telefono sotto
    const need = portrait ? 4.7 : 5.0;
    const halfH = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const fitW = need / 2 / (halfH * camera.aspect), fitH = (portrait ? 2.0 : 2.75) / halfH;
    let dist = Math.max(fitW, fitH) * (1 - .1 * p);
    const elev = THREE.MathUtils.lerp(portrait ? .28 : .2, .12, p);
    camera.position.set(0, Math.sin(elev) * dist + .4, Math.cos(elev) * dist);
    camera.lookAt(0, .72, 0);
    if (portrait) camera.setViewOffset(W, Hh, 0, -Hh * (.12 - .08 * p), W, Hh);
    else camera.setViewOffset(W, Hh, -W * (.16 - .16 * p), -Hh * .015, W, Hh);
  }

  let visible = true, running = true, last = performance.now(), slow = 0, frames = 0, t0 = last;
  const io = new IntersectionObserver(es => { visible = es[0].isIntersecting; }, { threshold: 0 });
  io.observe(canvas);
  document.addEventListener('visibilitychange', () => { running = !document.hidden; last = performance.now(); });

  function frame(now) {
    requestAnimationFrame(frame);
    if (!visible || !running) return;
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    if (!drag) { vel *= Math.pow(.05, dt); rot += (auto + vel * .016) * dt * (opts.still ? 0 : 1) + vel * dt * .0; rot += vel * dt * .02; }
    car.rotation.y = rot + scrollP * 1.1;
    place(scrollP);
    renderer.render(scene, camera);
    // telefoni lenti: se i primi 60 fotogrammi vanno sotto i 30 fps, si abbassa la risoluzione
    frames++;
    if (frames === 60) {
      const fps = 60 / ((now - t0) / 1000);
      if (fps < 30) { renderer.setPixelRatio(1); resize(); }
      if (fps < 18) { root.classList.add('gl-slow'); }
    }
  }
  requestAnimationFrame(t => { last = t0 = t; frame(t); });
  root.classList.add('gl-on');
  return {
    setColor(hex) { paint.color.set(hex); },
    renderer, scene, camera,
  };
}
