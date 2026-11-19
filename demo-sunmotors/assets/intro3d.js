/* Sunmotors – intro cinematografica 3D e scrollytelling.
   Auto disegnata via codice (generica, nessun marchio), vernice PBR con trasparente (clearcoat), studio HDR generato
   al volo (PMREM), pavimento bagnato riflettente, polvere nella luce, bloom, ACES, MSAA.
   three.js r160 da CDN con versione esatta (importmap nella home). */
import * as THREE from 'three';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';

const ROOT = document.documentElement;

/* ---------- utilità ---------- */
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const ramp = (a, b, t) => clamp((t - a) / (b - a), 0, 1);
const sstep = (a, b, v) => { const t = clamp((v - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
const eio = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const eos = t => 1 - Math.pow(1 - t, 3);
const eSine = t => -(Math.cos(Math.PI * t) - 1) / 2;
function curve(pts) { // interpolazione cubica monotona
  pts = pts.slice().sort((a, b) => a[0] - b[0]);
  const n = pts.length, xs = pts.map(p => p[0]), ys = pts.map(p => p[1]), d = [], m = [];
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

/* ---------- forma: crossover-coupé premium generico (metri; muso verso +x) ---------- */
const L2 = 2.40, W2 = .952, XF = 1.47, XR = -1.45, TR = .372, WZ = .838, ARCH = TR + .058;
const yTop = curve([[2.40, .56], [2.385, .65], [2.34, .735], [2.25, .79], [2.12, .825], [1.8, .885], [1.3, .945], [1.02, .985], [.86, 1.05], [.6, 1.215], [.32, 1.39], [.05, 1.515], [-.25, 1.572], [-.6, 1.578], [-.95, 1.54], [-1.3, 1.45], [-1.62, 1.335], [-1.88, 1.245], [-2.08, 1.2], [-2.24, 1.185], [-2.34, 1.15], [-2.40, 1.04]]);
const yBelt = curve([[2.40, .54], [2.34, .69], [2.25, .755], [2.12, .79], [1.8, .85], [1.3, .915], [1.0, .965], [.5, 1.0], [0, 1.02], [-.6, 1.045], [-1.2, 1.075], [-1.7, 1.105], [-2.05, 1.13], [-2.24, 1.13], [-2.34, 1.09], [-2.40, 1.0]]);
const yBase = curve([[2.40, .36], [2.31, .25], [2.12, .21], [1.0, .195], [0, .19], [-1.0, .195], [-2.0, .22], [-2.24, .28], [-2.40, .40]]);
function yBottom(x) {
  let y = yBase(x);
  for (const cx of [XF, XR]) { const dx = x - cx; if (Math.abs(dx) < ARCH) y = Math.max(y, TR + Math.sqrt(ARCH * ARCH - dx * dx) * .985); }
  return y;
}
function plan(x) { const u = Math.abs(x) / L2, p = x > 0 ? 4.6 : 5.6; return Math.pow(Math.max(0, 1 - Math.pow(u, p)), 1 / p); }
const halfW = x => W2 * plan(x);
const cabin = x => sstep(1.03, .70, x) * sstep(-2.12, -1.62, x);
const haunch = x => .028 * Math.exp(-Math.pow((x - XF) / .55, 2)) + .036 * Math.exp(-Math.pow((x - XR) / .6, 2));

/* sezione trasversale (metà destra): [z, y, s, zona]  zona 0 fondo, 1 fianco, 2 sopra */
const NB = 6, NC = 5, NS = 14, NL = 5, NU = 30;
function section(x) {
  const yb = yBottom(x), ys = yBelt(x), yt = Math.max(yTop(x), ys + .012);
  const w = halfW(x), cab = cabin(x), hn = haunch(x) * plan(x);
  const a = Math.max(.02, Math.min(w - .012, w - .045 - cab * (w - .80)));
  const pts = [], rb = Math.max(.005, Math.min(.085, (ys - yb) * .35, w * .45)), tuck = Math.min(.035, w * .1);
  for (let i = 0; i < NB; i++) pts.push([i / NB * (w - rb - tuck), yb, 0, 0]);
  for (let i = 0; i < NC; i++) { const t = i / NC * Math.PI / 2; pts.push([w - tuck - rb + Math.sin(t) * rb, yb + rb - Math.cos(t) * rb, 0, 1]); }
  const sideTop = ys - .055;
  let zt = w;
  for (let i = 0; i < NS; i++) {
    const t = i / (NS - 1), y = yb + rb + t * Math.max(0, sideTop - yb - rb);
    const z = w - tuck * Math.pow(1 - t, 2) + .012 * Math.sin(Math.PI * t) + hn * sstep(.3, .9, t);
    pts.push([z, y, 0, 1]); zt = z;
  }
  for (let i = 1; i <= NL; i++) { const e = i / NL * Math.PI / 2; pts.push([a + (zt - a) * Math.cos(e), sideTop + .055 * Math.sin(e), 0, 1]); }
  const b = yt - ys, n = 2.5 + 6 * (1 - cab), tumble = .2 * cab;
  for (let i = 1; i <= NU; i++) {
    const th = i / NU * Math.PI / 2, c = Math.pow(Math.cos(th), 2 / n), s = Math.pow(Math.sin(th), 2 / n);
    pts.push([a * c * (1 - tumble * s), ys + b * s, s, 2]);
  }
  return pts;
}
const ringOf = x => { const s = section(x); return s.map(p => [-p[0], p[1], p[2], p[3]]).reverse().concat(s.slice(1)); };
const xAt = u => -L2 + 2 * L2 * (.5 - .5 * Math.cos(Math.PI * u));

/* zone della carrozzeria (per colori, ruvidità, luci) */
function classify(x, y, z, s, zone) {
  const az = Math.abs(z), ys = yBelt(x), yb0 = yBase(x);
  if (zone === 0) return 'under';
  if (zone === 2 && cabin(x) > .02 && y > ys + .012) {
    if (y < ys + .03 && x < .93 && x > -1.98) return 'chrome';
    if (s > .735 && s < .758 && x < .84 && x > -1.86) return 'chrome';
    if (x > -.47 && x < -.36 && s < .75) return 'piano';
    return 'glass';
  }
  // frontale
  if (x > 2.17) {
    if (y > .698 && y < .716 && az < .9) return 'drl';
    if (y > .59 && y < .69 && az > .45 && az < .9) {
      if (y > .62 && y < .664) for (let k = 0; k < 3; k++) if (Math.abs(az - (.55 + k * .105)) < .03) return 'proj';
      return 'piano';
    }
    if (x > 2.27 && y > .47 && y < .69) return ((y * 110) | 0) % 2 ? 'piano' : 'grille';
    if (x > 2.22 && y > .27 && y < .45 && az < .74) { const gx = (z * 70 + ((y * 70) | 0) * .5) % 1; return Math.abs(gx) < .3 ? 'trim' : 'grille'; }
    if (y < .27) return 'trim';
  }
  // posteriore
  if (x < -2.15) {
    if (y > 1.036 && y < 1.058 && az < .93) return 'tail';
    if (y > 1.0 && y < 1.08 && x < -2.27) return 'piano';
    if (y < .44) { if (x < -2.3 && az < .6 && Math.abs(((z * 9) % 1 + 1) % 1 - .5) < .07) return 'piano'; return 'trim'; }
  }
  // sottoporta e passaruota
  if (y < yb0 + .07 && x < 2.14 && x > -2.12) return 'trim';
  for (const cx of [XF, XR]) { const d = Math.hypot(x - cx, y - TR); if (d > ARCH - .02 && d < ARCH + .034) return 'trim'; }
  // linee di porte, cofano, portellone
  const above = y > yb0 + .07, below = y < ys + .015;
  if (above && below) {
    for (const xd of [.93, -.40, -1.47]) if (Math.abs(x - xd + (y - .6) * .05) < .0055) return 'gap';
    for (const [x0, x1] of [[.34, .55], [-.88, -.67]]) { const hy = ys - .095; if (x > x0 && x < x1 && Math.abs(y - hy) < .016 && (Math.abs(y - hy) > .011 || x < x0 + .005 || x > x1 - .005)) return 'gap'; }
  }
  if (zone === 2 && x > 1.0 && x < 2.2 && Math.abs(az - (halfW(x) - .17)) < .005) return 'gap';
  if (Math.abs(x - 2.17) < .005 && y > .75) return 'gap';
  if (x < -2.0 && Math.abs(y - .62) < .005) return 'gap';
  return 'paint';
}
// albedo (sRGB), [R=clearcoat, G=ruvidità, B=metallo], emissione
const ALB = { paint: [0, 0, 0], glass: [5, 6, 7], piano: [7, 7, 8], chrome: [205, 207, 212], trim: [14, 14, 15], grille: [22, 22, 24], gap: [8, 8, 8], under: [6, 6, 6], drl: [240, 240, 240], proj: [30, 30, 32], tail: [60, 4, 2] };
const RMC = { paint: [255, 92, 166], glass: [255, 6, 0], piano: [255, 22, 0], chrome: [0, 40, 255], trim: [0, 150, 0], grille: [120, 80, 90], gap: [0, 210, 0], under: [0, 240, 0], drl: [255, 30, 0], proj: [255, 20, 0], tail: [255, 30, 0] };
const EMI = { drl: [255, 246, 232], proj: [215, 228, 255], tail: [255, 14, 6] };

function bodyMaps(TW, TH) {
  const mk = () => { const c = document.createElement('canvas'); c.width = TW; c.height = TH; return c; };
  const cC = mk(), cR = mk(), cE = mk();
  const dC = new ImageData(TW, TH), dR = new ImageData(TW, TH), dE = new ImageData(TW, TH);
  for (let i = 0; i < TW; i++) {
    const u = (i + .5) / TW, x = xAt(u), ring = ringOf(x), RN = ring.length - 1;
    for (let j = 0; j < TH; j++) {
      const v = 1 - (j + .5) / TH, f = v * RN, k = Math.min(RN - 1, Math.floor(f)), t = f - k, p = ring[k], q = ring[k + 1];
      const z = p[0] + (q[0] - p[0]) * t, y = p[1] + (q[1] - p[1]) * t, s = p[2] + (q[2] - p[2]) * t;
      const zone = (p[3] === 2 && q[3] === 2) ? 2 : (p[3] === 0 && q[3] === 0 ? 0 : 1);
      const kk = classify(x, y, z, s, zone), o = (j * TW + i) * 4;
      const c = ALB[kk], r = RMC[kk], e = EMI[kk];
      dC.data[o] = c[0]; dC.data[o + 1] = c[1]; dC.data[o + 2] = c[2]; dC.data[o + 3] = 255;
      dR.data[o] = r[0]; dR.data[o + 1] = r[1]; dR.data[o + 2] = r[2]; dR.data[o + 3] = 255;
      if (e) { dE.data[o] = e[0]; dE.data[o + 1] = e[1]; dE.data[o + 2] = e[2]; }
      dE.data[o + 3] = 255;
    }
  }
  cC.getContext('2d').putImageData(dC, 0, 0); cR.getContext('2d').putImageData(dR, 0, 0); cE.getContext('2d').putImageData(dE, 0, 0);
  const tx = (c, srgb) => { const t = new THREE.CanvasTexture(c); t.anisotropy = 8; if (srgb) t.colorSpace = THREE.SRGBColorSpace; return t; };
  return { map: tx(cC, true), rm: tx(cR), em: tx(cE, true) };
}

/* ---------- uniform condivisi (luce che scorre, fari, vernice) ---------- */
const U = {
  uBarX: { value: 5 }, uGateH: { value: 2.7 }, uGateW: { value: 2.3 }, uBarW: { value: .1 }, uBarI: { value: 0 }, uBarC: { value: new THREE.Color(1, .94, .86) },
  uHead: { value: 0 }, uTail: { value: 0 }, uPaint: { value: new THREE.Color('#24272c') },
};
function hook(mat, paint) {
  if (paint) mat.defines = Object.assign(mat.defines || {}, { SUN_PAINT: '' });
  mat.onBeforeCompile = sh => {
    for (const k in U) sh.uniforms[k] = U[k];
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vSW;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvSW = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vSW;\nuniform float uBarX, uGateH, uGateW, uBarW, uBarI, uHead, uTail;\nuniform vec3 uBarC, uPaint;')
      .replace('#include <map_fragment>', `#include <map_fragment>
#ifdef SUN_PAINT
{ vec3 rmT = texture2D(roughnessMap, vRoughnessMapUv).rgb; float pm = 1.0 - smoothstep(0.0, 0.045, abs(rmT.b - 0.651)); diffuseColor.rgb += uPaint * pm; }
#endif`)
      .replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\ntotalEmissiveRadiance *= (vSW.x > 0.0 ? uHead : uTail);')
      .replace('#include <opaque_fragment>', `{
  vec3 Nw = normalize(inverseTransformDirection(normal, viewMatrix));
  vec3 Vw = normalize(cameraPosition - vSW);
  vec3 Rw = reflect(-Vw, Nw);
  float bar = 0.0;
  float tt = abs(Rw.x) > 0.01 ? (uBarX - vSW.x) / Rw.x : -1.0;
  if (uBarI > 0.001 && tt > 0.0) {
    vec3 H = vSW + Rw * tt;
    float az = abs(H.z);
    float dT = abs(H.y - uGateH), dS = abs(az - uGateW);
    float top = (smoothstep(uBarW, uBarW * .35, dT) + .14 * exp(-dT * dT * 6.0)) * (1.0 - smoothstep(uGateW - .05, uGateW + .25, az));
    float side = (smoothstep(uBarW, uBarW * .35, dS) + .14 * exp(-dS * dS * 6.0)) * smoothstep(-.2, .1, H.y) * (1.0 - smoothstep(uGateH - .05, uGateH + .25, H.y));
    bar = max(top, side);
  }
  #ifdef USE_CLEARCOAT
  float cc = material.clearcoat;
  #else
  float cc = 0.0;
  #endif
  float fr = 0.04 + 0.96 * pow(1.0 - clamp(dot(Nw, Vw), 0.0, 1.0), 5.0);
  float gl = max(cc, pow(1.0 - roughnessFactor, 3.0));
  vec3 sc = mix(mix(vec3(1.0), diffuseColor.rgb, metalnessFactor), vec3(1.0), cc);
  outgoingLight += uBarC * uBarI * bar * gl * sc * mix(0.28, 1.0, fr);
}
#include <opaque_fragment>`);
  };
  return mat;
}

/* ---------- carrozzeria ---------- */
function buildBody(lite) {
  const NX = lite ? 150 : 210, pos = [], uv = [], idx = [];
  let RN = 0;
  for (let i = 0; i <= NX; i++) {
    const u = i / NX, x = xAt(u), ring = ringOf(x); RN = ring.length;
    ring.forEach((p, j) => { pos.push(x, p[1], p[0]); uv.push(u, j / (RN - 1)); });
  }
  for (let i = 0; i < NX; i++) for (let j = 0; j < RN - 1; j++) { const a = i * RN + j, b = a + 1, c = a + RN, d = c + 1; idx.push(a, c, b, b, c, d); }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx); g.computeVertexNormals();
  const m = bodyMaps(lite ? 768 : 1536, lite ? 768 : 1024);
  const mat = hook(new THREE.MeshPhysicalMaterial({
    color: 0xffffff, map: m.map, roughnessMap: m.rm, metalnessMap: m.rm, clearcoatMap: m.rm, roughness: 1, metalness: 1,
    clearcoat: 1, clearcoatRoughness: .03, emissive: 0xffffff, emissiveMap: m.em, emissiveIntensity: 1,
  }), true);
  return new THREE.Mesh(g, mat);
}

/* ---------- cerchi da 21", pinze, dischi ---------- */
function discTex() {
  const c = document.createElement('canvas'); c.width = c.height = 256; const x = c.getContext('2d');
  const g = x.createRadialGradient(128, 128, 40, 128, 128, 128); g.addColorStop(0, '#2a2a2c'); g.addColorStop(.42, '#2a2a2c'); g.addColorStop(.45, '#8d8f93'); g.addColorStop(1, '#6e7074');
  x.fillStyle = g; x.fillRect(0, 0, 256, 256);
  x.strokeStyle = 'rgba(30,30,32,.55)'; x.lineWidth = 2;
  for (let k = 0; k < 360; k += 3) { const a = k * Math.PI / 180; x.beginPath(); x.arc(128, 128, 60 + (k % 9) * 6, a, a + .02); x.stroke(); }
  x.fillStyle = '#151516';
  for (let k = 0; k < 48; k++) { const a = k / 48 * Math.PI * 2, r = 74 + (k % 3) * 14; x.beginPath(); x.arc(128 + Math.cos(a) * r, 128 + Math.sin(a) * r, 3.4, 0, 7); x.fill(); }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
function makeWheelParts(lite) {
  const r0 = .268, w = .128, seg = lite ? 48 : 72;
  // pneumatico
  const tp = [new THREE.Vector2(r0 + .012, -w + .012)];
  for (let i = 0; i <= 10; i++) { const t = i / 10 * Math.PI / 2; tp.push(new THREE.Vector2(TR - .045 + Math.sin(t) * .045, -w + .045 - Math.cos(t) * .045)); }
  for (let i = 0; i <= 10; i++) { const t = i / 10 * Math.PI / 2; tp.push(new THREE.Vector2(TR - .045 + Math.cos(t) * .045, w - .045 + Math.sin(t) * .045)); }
  tp.push(new THREE.Vector2(r0 + .03, w - .004)); tp.push(new THREE.Vector2(r0 + .012, w - .02));
  const tyreG = new THREE.LatheGeometry(tp, seg); tyreG.rotateX(Math.PI / 2);
  // faccia del cerchio: 10 razze a coppie, concava, due toni (fronte diamantato, fianchi canna di fucile)
  const sh = new THREE.Shape(); sh.absarc(0, 0, r0 - .006, 0, Math.PI * 2, false);
  for (let k = 0; k < 5; k++) {
    const base = k / 5 * Math.PI * 2;
    for (const [a0, a1, ri] of [[base + .19, base + Math.PI * 2 / 5 - .19, .085], [base - .055, base + .055, .13]]) {
      const h = new THREE.Path(), ro = r0 - .03;
      if (a1 - a0 < .2) { h.moveTo(Math.cos(a0) * ri, Math.sin(a0) * ri); h.absarc(0, 0, ro - .01, a0 + .02, a1 - .02, false); h.lineTo(Math.cos(a1) * ri, Math.sin(a1) * ri); h.absarc(0, 0, ri, a1, a0, true); }
      else { h.moveTo(Math.cos(a0) * ri, Math.sin(a0) * ri); h.lineTo(Math.cos(a0 + .05) * ro, Math.sin(a0 + .05) * ro); h.absarc(0, 0, ro, a0 + .05, a1 - .05, false); h.lineTo(Math.cos(a1) * ri, Math.sin(a1) * ri); h.absarc(0, 0, ri, a1, a0, true); }
      sh.holes.push(h);
    }
  }
  const faceG = new THREE.ExtrudeGeometry(sh, { depth: .022, bevelEnabled: true, bevelThickness: .008, bevelSize: .006, bevelSegments: 2, curveSegments: lite ? 40 : 64 });
  const p = faceG.attributes.position;
  for (let i = 0; i < p.count; i++) { const r = Math.hypot(p.getX(i), p.getY(i)); p.setZ(i, p.getZ(i) + .05 * (1 - sstep(.05, r0 - .02, r))); }
  faceG.computeVertexNormals();
  const lipG = new THREE.TorusGeometry(r0 - .004, .009, 10, seg);
  const barrelG = new THREE.CylinderGeometry(r0 - .004, r0 - .004, w * 2 - .03, seg, 1, true); barrelG.rotateX(Math.PI / 2);
  const discG = new THREE.CylinderGeometry(.205, .205, .028, 48); discG.rotateX(Math.PI / 2);
  const capG = new THREE.CylinderGeometry(.042, .046, .02, 32); capG.rotateX(Math.PI / 2);
  const nutG = new THREE.CylinderGeometry(.011, .011, .02, 6); nutG.rotateX(Math.PI / 2);
  // pinza: arco estruso con spigoli arrotondati
  const cs = new THREE.Shape(), a0 = .35, a1 = 1.25, ri = .148, ro = .232;
  cs.moveTo(Math.cos(a0) * ri, Math.sin(a0) * ri); cs.absarc(0, 0, ri, a0, a1, false); cs.lineTo(Math.cos(a1) * ro, Math.sin(a1) * ro); cs.absarc(0, 0, ro, a1, a0, true);
  const calG = new THREE.ExtrudeGeometry(cs, { depth: .06, bevelEnabled: true, bevelThickness: .012, bevelSize: .012, bevelSegments: 3, curveSegments: 20 });
  calG.translate(0, 0, -.03);
  return { r0, w, tyreG, faceG, lipG, barrelG, discG, capG, nutG, calG };
}
function makeWheel(P, M, front) {
  const g = new THREE.Group();
  g.add(new THREE.Mesh(P.tyreG, M.tyre));
  const face = new THREE.Mesh(P.faceG, [M.face, M.spokeSide]); face.position.z = P.w - .088; g.add(face);
  const lip = new THREE.Mesh(P.lipG, M.face); lip.position.z = P.w - .012; g.add(lip);
  g.add(new THREE.Mesh(P.barrelG, M.barrel));
  const disc = new THREE.Mesh(P.discG, M.disc); disc.position.z = P.w - .17; g.add(disc);
  const cal = new THREE.Mesh(P.calG, M.caliper); cal.position.z = P.w - .17; cal.rotation.z = front ? .55 : 1.45; g.add(cal);
  const cap = new THREE.Mesh(P.capG, M.cap); cap.position.z = P.w - .02; g.add(cap);
  for (let k = 0; k < 5; k++) { const a = k / 5 * Math.PI * 2 + Math.PI / 5, n = new THREE.Mesh(P.nutG, M.face); n.position.set(Math.cos(a) * .065, Math.sin(a) * .065, P.w - .035); g.add(n); }
  return g;
}

/* ---------- studio HDR generato ---------- */
function studioEnv(renderer) {
  const s = new THREE.Scene();
  s.add(new THREE.Mesh(new THREE.SphereGeometry(40, 32, 16), new THREE.MeshBasicMaterial({ color: 0x050506, side: THREE.BackSide })));
  const panel = (w, h, x, y, z, rx, ry, k, tint) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(tint || 0xffffff).multiplyScalar(k), side: THREE.DoubleSide }));
    m.position.set(x, y, z); m.rotation.set(rx, ry, 0); s.add(m);
  };
  panel(9, 2.4, 0, 9, 0, Math.PI / 2, 0, 2.2);                   // soffitto
  panel(14, .28, 0, 8.6, 2.4, Math.PI / 2, 0, 5);                // due tagli di luce sopra (riga sul cofano/tetto)
  panel(14, .28, 0, 8.6, -2.4, Math.PI / 2, 0, 5);
  panel(26, .32, 0, 2.6, 12, 0, Math.PI, 7, 0xfff3e2);           // linee lunghe sulle fiancate
  panel(26, .32, 0, 2.6, -12, 0, 0, 7, 0xfff3e2);
  panel(26, .22, 0, 1.15, 12, 0, Math.PI, 2.2);
  panel(26, .22, 0, 1.15, -12, 0, 0, 2.2);
  panel(30, .5, 0, -.6, 9, Math.PI / 2.6, Math.PI, 2.6, 0xfff1e0); // linee basse: riflesso lungo sulle portiere
  panel(30, .5, 0, -.6, -9, -Math.PI / 2.6, 0, 2.6, 0xfff1e0);
  panel(.5, 7, 14, 4, 0, 0, -Math.PI / 2, 3.4, 0xdfe8ff);       // davanti, fredda
  panel(16, .35, -14, 1.9, 0, 0, Math.PI / 2, 2.4, 0xff8a2a);    // dietro, calda (il "sole" di Sunmotors)
  const pm = new THREE.PMREMGenerator(renderer);
  const tex = pm.fromScene(s, .015).texture; pm.dispose();
  return tex;
}

/* ---------- pavimento bagnato ---------- */
const FloorShader = {
  name: 'WetFloor',
  uniforms: { color: { value: null }, tDiffuse: { value: null }, textureMatrix: { value: null }, uEnv: { value: 0 }, uBarX: { value: 5 }, uBarI: { value: 0 }, uHead: { value: 0 }, uTail: { value: 0 }, uTime: { value: 0 } },
  vertexShader: `uniform mat4 textureMatrix; varying vec4 vUv; varying vec3 vWP;
    #include <common>
    void main(){ vUv = textureMatrix * vec4(position, 1.0); vWP = (modelMatrix * vec4(position, 1.0)).xyz; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: `uniform vec3 color; uniform sampler2D tDiffuse; uniform float uEnv, uBarX, uBarI, uHead, uTail, uTime; varying vec4 vUv; varying vec3 vWP;
    float h21(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
    float vn(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f); return mix(mix(h21(i), h21(i + vec2(1,0)), f.x), mix(h21(i + vec2(0,1)), h21(i + vec2(1,1)), f.x), f.y); }
    float fbm(vec2 p){ float a = .5, s = 0.0; for (int i = 0; i < 4; i++){ s += a * vn(p); p *= 2.03; a *= .5; } return s; }
    void main(){
      vec2 p = vWP.xz;
      float r = length(p * vec2(.78, 1.0));
      float wet = smoothstep(.40, .56, fbm(p * .5 + 3.1));
      float grain = vn(p * 160.0) * .5 + vn(p * 41.0) * .5;
      vec2 dist = (vec2(vn(p * 7.0 + uTime * .03), vn(p * 7.0 + 17.0)) - .5) * .008 * (1.0 - wet);
      vec4 uv = vUv; uv.xy += dist * uv.w;
      vec3 rf = texture2DProj(tDiffuse, uv).rgb;
      vec2 o = vec2(.007, 0.0) * uv.w * (1.0 - wet);
      vec3 rb = (texture2DProj(tDiffuse, uv + vec4(o, 0.0, 0.0)).rgb + texture2DProj(tDiffuse, uv - vec4(o, 0.0, 0.0)).rgb
               + texture2DProj(tDiffuse, uv + vec4(o.yx * 1.6, 0.0, 0.0)).rgb + texture2DProj(tDiffuse, uv - vec4(o.yx * 1.6, 0.0, 0.0)).rgb) * .25;
      rf = mix(rb, rf, .3 + .7 * wet);
      float pool = exp(-r * r / 10.0);
      float barL = exp(-pow((vWP.x - uBarX) / .8, 2.0)) * exp(-vWP.z * vWP.z / 7.0);
      float hx = vWP.x - 2.35;
      float cone = hx > 0.0 ? exp(-pow((abs(vWP.z) - .6 - hx * .12) / (.22 + hx * .2), 2.0)) * exp(-hx * .32) * smoothstep(0.0, .5, hx) : 0.0;
      float tx = -vWP.x - 2.35;
      float tl = tx > 0.0 ? exp(-tx * 1.5) * exp(-vWP.z * vWP.z / 1.4) : 0.0;
      vec3 asph = vec3(.034, .033, .034) * (.7 + .6 * grain) * (1.0 - .5 * wet);
      vec3 li = vec3(1.0, .93, .86) * (uEnv * (.18 + 1.7 * pool) + uBarI * barL * 2.4) + vec3(1.0, .97, .92) * uHead * cone * 2.2 + vec3(1.0, .07, .03) * uTail * tl * .7;
      float fade = 1.0 - smoothstep(4.0, 12.0, r);
      vec3 col = (asph * li + rf * mix(.16, .8, wet) * color) * fade;
      gl_FragColor = vec4(col, 1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`
};
function blobTex() {
  const c = document.createElement('canvas'); c.width = 256; c.height = 128; const x = c.getContext('2d');
  const g = x.createRadialGradient(128, 64, 6, 128, 64, 126);
  g.addColorStop(0, 'rgba(0,0,0,1)'); g.addColorStop(.5, 'rgba(0,0,0,.75)'); g.addColorStop(1, 'rgba(0,0,0,0)');
  x.save(); x.scale(1, .5); x.fillStyle = g; x.fillRect(0, 0, 256, 256); x.restore();
  return new THREE.CanvasTexture(c);
}

/* ---------- polvere nella luce ---------- */
function dust(n) {
  const pos = new Float32Array(n * 3), seed = new Float32Array(n);
  for (let i = 0; i < n; i++) { pos[i * 3] = (Math.random() - .5) * 11; pos[i * 3 + 1] = Math.random() * 3.4; pos[i * 3 + 2] = (Math.random() - .5) * 8; seed[i] = Math.random(); }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1));
  const m = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uBarX: U.uBarX, uBarI: U.uBarI, uGateW: U.uGateW, uEnv: { value: 0 }, uHead: { value: 0 }, uPR: { value: 1 }, uSize: { value: 26 } },
    vertexShader: `uniform float uTime, uBarX, uBarI, uGateW, uEnv, uHead, uPR, uSize; attribute float aSeed; varying float vA;
      void main(){
        vec3 p = position;
        p.x += sin(uTime * .07 + aSeed * 30.0) * .45;
        p.y = mod(p.y + uTime * (.015 + .03 * aSeed), 3.4);
        p.z += cos(uTime * .05 + aSeed * 20.0) * .45;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mv;
        float shaft = exp(-pow((p.x - uBarX) / .55, 2.0)) * uBarI * (.35 + .65 * p.y / 3.4) * (1.0 - smoothstep(uGateW - .2, uGateW + .3, abs(p.z)));
        float hx = p.x - 2.35;
        float cone = hx > 0.0 ? exp(-pow((abs(p.z) - .6 - hx * .12) / (.3 + hx * .2), 2.0)) * exp(-hx * .3) * exp(-pow((p.y - .65) / (.25 + hx * .14), 2.0)) : 0.0;
        vA = (.035 * uEnv + shaft * 1.4 + cone * uHead * 1.2) * (.35 + .65 * fract(aSeed * 7.31)) * smoothstep(.0, .3, p.y) * smoothstep(3.4, 2.9, p.y);
        gl_PointSize = uSize * uPR * (.45 + aSeed) / max(.5, -mv.z);
      }`,
    fragmentShader: `varying float vA; void main(){ float d = length(gl_PointCoord - .5); float a = smoothstep(.5, .0, d); a *= a; gl_FragColor = vec4(vec3(1.0, .9, .78) * vA * a * 2.6, 1.0); }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const pts = new THREE.Points(g, m); pts.frustumCulled = false; return pts;
}
/* ---------- finitura: vignetta, grana, aberrazione leggera, dissolvenza ---------- */
const Finish = {
  uniforms: { tDiffuse: { value: null }, uTime: { value: 0 }, uFade: { value: 0 } },
  vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
  fragmentShader: `uniform sampler2D tDiffuse; uniform float uTime, uFade; varying vec2 vUv;
    float h(vec2 p){ return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
    void main(){
      vec2 c = vUv - .5; float r2 = dot(c, c); vec2 off = c * r2 * .01;
      vec3 col = vec3(texture2D(tDiffuse, vUv + off).r, texture2D(tDiffuse, vUv).g, texture2D(tDiffuse, vUv - off).b);
      col *= 1.0 - .6 * smoothstep(.08, .55, r2 * 1.7);
      col += (h(vUv * 917.0 + fract(uTime) * 61.0) - .5) * .028;
      gl_FragColor = vec4(col * uFade, 1.0);
    }`
};

/* ---------- inquadrature ---------- */
// az: gradi attorno all'auto (0 = davanti, 90 = fianco destro); ox/oy: spostamento dell'inquadratura (frazione dello schermo)
function poses(portrait) {
  if (portrait) return {
    i0: { az: 30, el: 3, d: 9.5, t: [.5, .62, 0], fov: 40, ox: 0, oy: 0 },
    i1: { az: 18, el: 4.5, d: 6.4, t: [1.4, .64, .1], fov: 38, ox: 0, oy: 0 },
    hero: { az: 30, el: 8, fit: 1.98, t: [0, .76, 0], fov: 30, ox: 0, oy: -.02 },
    s1: { az: 62, el: 4, d: 4.1, t: [1.45, .42, .95], fov: 36, ox: 0, oy: .16 },
    s2: { az: 76, el: 3, fit: 2.25, t: [0, .76, 0], fov: 30, ox: 0, oy: .17 },
    s3: { az: 150, el: 7, fit: 2.0, t: [-.25, .8, 0], fov: 30, ox: 0, oy: .17 },
    s4: { az: 318, el: 17, fit: 2.1, t: [0, .6, 0], fov: 30, ox: 0, oy: .17 },
  };
  return {
    i0: { az: 34, el: 3, d: 6.6, t: [.4, .62, 0], fov: 30, ox: 0, oy: 0 },
    i1: { az: 22, el: 4.5, d: 4.7, t: [1.45, .64, .1], fov: 30, ox: 0, oy: 0 },
    hero: { az: 38, el: 7, fit: 3.4, t: [0, .78, 0], fov: 24, ox: -.2, oy: .07 },
    s1: { az: 64, el: 3, d: 3.0, t: [1.47, .42, .95], fov: 28, ox: -.17, oy: 0 },
    s2: { az: 90, el: 2, fit: 2.75, t: [0, .76, 0], fov: 20, ox: 0, oy: .1 },
    s3: { az: 148, el: 7, fit: 2.75, t: [-.25, .8, 0], fov: 24, ox: .16, oy: .02 },
    s4: { az: 318, el: 17, fit: 2.85, t: [0, .62, 0], fov: 24, ox: -.15, oy: .02 },
  };
}
const KEYS = ['az', 'el', 'd', 'fov', 'ox', 'oy'];
function mix(a, b, t) { const o = { t: [lerp(a.t[0], b.t[0], t), lerp(a.t[1], b.t[1], t), lerp(a.t[2], b.t[2], t)] }; for (const k of KEYS) o[k] = lerp(a[k], b[k], t); return o; }

/* ---------- avvio ---------- */
export function start(opts = {}) {
  const canvas = opts.canvas || document.getElementById('gl');
  const show = document.getElementById('show');
  const lite = !!opts.lite, hq = !!opts.hq;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: 'high-performance', stencil: false });
  const isGL2 = renderer.capabilities.isWebGL2;
  let dprMax = Math.min(devicePixelRatio || 1, opts.dpr || 2), dpr = dprMax;
  renderer.setPixelRatio(dpr);
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x040405);
  scene.environment = studioEnv(renderer);
  const camera = new THREE.PerspectiveCamera(30, 1, .1, 80);

  /* auto */
  const car = new THREE.Group(); scene.add(car);
  const body = buildBody(lite); car.add(body);
  const dark = hook(new THREE.MeshPhysicalMaterial({ color: 0x050505, roughness: .9, metalness: 0 }));
  const under = new THREE.Mesh(new THREE.BoxGeometry(4.3, .5, 1.62), dark); under.position.y = .45; car.add(under);
  const linerG = new THREE.CylinderGeometry(ARCH - .01, ARCH - .01, .62, 32, 1, true, Math.PI / 2, Math.PI); linerG.rotateX(Math.PI / 2);
  const linerM = new THREE.MeshBasicMaterial({ color: 0x020202, side: THREE.DoubleSide });
  const M = {
    tyre: hook(new THREE.MeshPhysicalMaterial({ color: 0x121213, roughness: .82, metalness: 0 })),
    face: hook(new THREE.MeshPhysicalMaterial({ color: 0xd9dbe0, roughness: .16, metalness: 1, clearcoat: .6, clearcoatRoughness: .05 })),
    spokeSide: hook(new THREE.MeshPhysicalMaterial({ color: 0x1b1c1f, roughness: .3, metalness: .7, clearcoat: .8, clearcoatRoughness: .08 })),
    barrel: hook(new THREE.MeshPhysicalMaterial({ color: 0x141416, roughness: .45, metalness: .6, side: THREE.DoubleSide })),
    disc: hook(new THREE.MeshPhysicalMaterial({ map: discTex(), roughness: .38, metalness: .9 })),
    caliper: hook(new THREE.MeshPhysicalMaterial({ color: 0xff6a12, roughness: .32, metalness: .1, clearcoat: 1, clearcoatRoughness: .08 })),
    cap: hook(new THREE.MeshPhysicalMaterial({ color: 0x0c0c0d, roughness: .2, metalness: .5, clearcoat: 1 })),
  };
  const WP = makeWheelParts(lite);
  for (const [x, sg] of [[XF, 1], [XF, -1], [XR, 1], [XR, -1]]) {
    const w = makeWheel(WP, M, x > 0); w.position.set(x, TR, sg * WZ); if (sg < 0) w.rotation.y = Math.PI; car.add(w);
    const l = new THREE.Mesh(linerG, linerM); l.position.set(x, TR, sg * (WZ - .2)); car.add(l);
  }
  // specchietti, antenna, spoiler
  const paintMat = hook(new THREE.MeshPhysicalMaterial({ color: 0xffffff, roughness: .4, metalness: .65, clearcoat: 1, clearcoatRoughness: .03 }), false);
  const piano = hook(new THREE.MeshPhysicalMaterial({ color: 0x070708, roughness: .1, metalness: 0, clearcoat: 1, clearcoatRoughness: .02 }));
  const mirG = new THREE.SphereGeometry(1, 28, 16);
  for (const sg of [1, -1]) {
    const mx = .74, my = yBelt(mx) + .075;
    const cap = new THREE.Mesh(mirG, paintMat); cap.scale.set(.075, .048, .105); cap.position.set(mx - .02, my, sg * (.80 + .115)); car.add(cap);
    const baseM = new THREE.Mesh(mirG, piano); baseM.scale.set(.07, .03, .08); baseM.position.set(mx, my - .03, sg * (.80 + .05)); car.add(baseM);
  }
  const fin = new THREE.Mesh(new THREE.SphereGeometry(1, 20, 12), piano); fin.scale.set(.11, .035, .03); fin.position.set(-1.4, yTop(-1.4) + .005, 0); car.add(fin);
  // l'ultima mano di vernice segue il colore scelto
  const syncPaint = () => paintMat.color.copy(U.uPaint.value);
  syncPaint();

  /* pavimento, ombra, luce che scorre, polvere */
  const RS = lite ? 512 : 1024;
  const floor = new Reflector(new THREE.PlaneGeometry(40, 40), { clipBias: .002, textureWidth: RS, textureHeight: RS, color: 0xffffff, shader: FloorShader, multisample: lite ? 0 : 4 });
  floor.rotation.x = -Math.PI / 2; scene.add(floor);
  const FU = floor.material.uniforms;
  const blob = new THREE.Mesh(new THREE.PlaneGeometry(5.5, 2.7), new THREE.MeshBasicMaterial({ map: blobTex(), transparent: true, depthWrite: false, opacity: .92 }));
  blob.rotation.x = -Math.PI / 2; blob.position.y = .004; blob.renderOrder = 2; car.add(blob);
  const gateM = new THREE.MeshBasicMaterial({ color: 0xffffff }), gate = new THREE.Group();
  { const gw = U.uGateW.value, gh = U.uGateH.value, t = .045;
    const top = new THREE.Mesh(new THREE.BoxGeometry(t, t, gw * 2 + t), gateM); top.position.y = gh; gate.add(top);
    for (const sg of [1, -1]) { const p = new THREE.Mesh(new THREE.BoxGeometry(t, gh, t), gateM); p.position.set(0, gh / 2, sg * gw); gate.add(p); } }
  scene.add(gate);
  const motes = dust(lite ? 280 : 720); scene.add(motes);

  /* luci dirette (oltre allo studio HDR) */
  // (niente luci dirette: sul trasparente lucidissimo farebbero punti caldi; basta lo studio HDR)

  /* post-produzione */
  const rt = new THREE.WebGLRenderTarget(2, 2, { type: THREE.HalfFloatType, samples: isGL2 ? (lite ? 2 : 4) : 0 });
  const composer = new EffectComposer(renderer, rt);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), .45, .38, 1.4);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());
  const finish = new ShaderPass(Finish); composer.addPass(finish);

  /* dimensioni */
  let W = 1, H = 1, portrait = false, P = poses(false);
  function resize() {
    const r = canvas.getBoundingClientRect(); W = Math.max(1, Math.round(r.width)); H = Math.max(1, Math.round(r.height));
    renderer.setPixelRatio(dpr); renderer.setSize(W, H, false);
    composer.setPixelRatio(dpr); composer.setSize(W, H);
    camera.aspect = W / H; portrait = camera.aspect < .9; P = poses(portrait);
    motes.material.uniforms.uPR.value = dpr * H / 900;
  }
  addEventListener('resize', resize, { passive: true }); resize();

  function fitD(p) {
    if (p.d) return p.d;
    const v = THREE.MathUtils.degToRad(p.fov) / 2, hz = Math.atan(Math.tan(v) * camera.aspect);
    const az = THREE.MathUtils.degToRad(p.az), span = Math.abs(Math.cos(az)) * L2 + Math.abs(Math.sin(az)) * W2 * 1.05;
    return Math.max((span + 1.0) / Math.tan(hz) * (p.fit / 2.75), 1.1 / Math.tan(v) * 1.3);
  }
  function applyPose(p, extraAz = 0, extraEl = 0) {
    const d = fitD(p), az = THREE.MathUtils.degToRad(p.az + extraAz), el = THREE.MathUtils.degToRad(clamp(p.el + extraEl, 1, 40));
    camera.fov = p.fov;
    camera.position.set(p.t[0] + d * Math.cos(el) * Math.cos(az), p.t[1] + d * Math.sin(el), p.t[2] + d * Math.cos(el) * Math.sin(az));
    camera.lookAt(p.t[0], p.t[1], p.t[2]);
    camera.setViewOffset(W, H, p.ox * W, p.oy * H, W, H);
    camera.updateProjectionMatrix();
  }
  const resolved = p => Object.assign({}, p, { d: fitD(p) });

  /* tempi dell'intro (secondi) */
  const T_TITLE = 3.6, T_OPEN = 5.3, T_END = 6.2;
  const url = new URLSearchParams(location.search);
  const freeze = url.has('t') ? parseFloat(url.get('t')) : null;
  let T = opts.skip ? T_END : 0, clockOn = false, phase = '';
  function setPhase(t) {
    const ph = t >= T_END ? 'done' : t >= T_OPEN ? 'open' : t >= T_TITLE ? 'title' : t >= .05 ? 'run' : '';
    if (ph === phase) return; phase = ph;
    ROOT.classList.toggle('i-run', !!ph); ROOT.classList.toggle('i-title', ['title', 'open', 'done'].includes(ph));
    ROOT.classList.toggle('i-open', ph === 'open' || ph === 'done'); ROOT.classList.toggle('i-done', ph === 'done');
    if (ph === 'done' && opts.onDone) opts.onDone();
  }
  function introState(t) {
    // luce che scorre dal muso alla coda, poi i fari, poi lo studio si accende
    const sw = eio(ramp(.25, 2.55, t));
    U.uBarX.value = lerp(3.6, -3.8, sw);
    U.uBarI.value = sstep(.15, .6, t) * (1 - sstep(2.5, 3.3, t)) * 1.6;
    let hd = 0;
    if (t > 2.05) hd = t < 2.12 ? .8 : t < 2.2 ? .1 : t < 2.27 ? 1 : t < 2.33 ? .35 : Math.min(1, .5 + (t - 2.33) * 3);
    const env = eos(ramp(2.5, 4.6, t));
    const bl = .4 + .12 * sstep(2.05, 2.35, t) * (1 - sstep(2.6, 3.8, t));
    return { hd, env, bl, fade: sstep(0, .5, t) };
  }
  function introPose(t) {
    if (t < 2.4) return mix(resolved(P.i0), resolved(P.i1), eSine(t / 2.4));
    return mix(resolved(P.i1), resolved(P.hero), eio(ramp(2.4, 5.9, t)));
  }

  /* scorrimento: 4 tappe */
  const STOPS = ['hero', 's1', 's2', 's3', 's4'];
  function scrollStage() {
    if (!show) return 0;
    const r = show.getBoundingClientRect(), span = show.offsetHeight - innerHeight;
    return span > 0 ? clamp(-r.top / span, 0, 1) * (STOPS.length - 1) : 0;
  }
  const steps = [].slice.call(document.querySelectorAll('[data-stop]'));
  const dots = [].slice.call(document.querySelectorAll('[data-dot]'));
  let lastStop = -1;
  function stagePose(s) {
    const k = Math.min(STOPS.length - 2, Math.floor(s)), f = sstep(.18, .82, s - k);
    const cur = Math.round(s);
    if (cur !== lastStop) {
      lastStop = cur;
      steps.forEach(e => e.classList.toggle('on', +e.dataset.stop === cur));
      dots.forEach(e => e.classList.toggle('on', +e.dataset.dot === cur));
      ROOT.classList.toggle('st-0', cur === 0);
    }
    return mix(resolved(P[STOPS[k]]), resolved(P[STOPS[k + 1]]), eSine(f));
  }

  /* interazione: trascina per girare attorno all'auto */
  let dragAz = 0, dragEl = 0, drag = false, lx = 0, ly = 0, lastTouch = 0, mx = 0, my = 0;
  canvas.addEventListener('pointerdown', e => { if (phase !== 'done') return; drag = true; lx = e.clientX; ly = e.clientY; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', e => {
    if (e.pointerType === 'mouse') { mx = e.clientX / W - .5; my = e.clientY / H - .5; }
    if (!drag) return;
    dragAz += (e.clientX - lx) / W * 220; dragEl += (e.clientY - ly) / H * 40; dragEl = clamp(dragEl, -6, 20);
    lx = e.clientX; ly = e.clientY; lastTouch = performance.now();
  });
  const up = () => { drag = false; lastTouch = performance.now(); };
  canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up);

  /* qualità adattiva */
  let acc = 0, nf = 0, upgraded = false;
  function adapt(dt) {
    if (hq || phase === '' ) return;
    acc += dt; nf++;
    if (nf < 45) return;
    const avg = acc / nf; acc = 0; nf = 0;
    if (avg > .026 && dpr > .75) { dpr = Math.max(.75, dpr * .8); resize(); }
    else if (avg > .03 && bloom.enabled) { bloom.enabled = false; }
    else if (avg < .011 && !upgraded && dpr < dprMax) { upgraded = true; dpr = Math.min(dprMax, dpr * 1.2); resize(); }
  }

  /* ciclo */
  let visible = true, running = !document.hidden, last = 0, cur = null, glintT = 0, time = 0;
  if ('IntersectionObserver' in window && show) new IntersectionObserver(es => { visible = es[0].isIntersecting; }, { threshold: 0 }).observe(show);
  document.addEventListener('visibilitychange', () => { running = !document.hidden; last = 0; });
  const envMats = [];
  scene.traverse(o => { if (o.isMesh && o.material) [].concat(o.material).forEach(m => { if (m.isMeshStandardMaterial && !envMats.includes(m)) envMats.push(m); }); });

  function render(now) {
    const dt = last ? Math.min(.05, (now - last) / 1000) : .016; last = now; time += dt;
    if (clockOn && freeze == null && T < T_END) T = Math.min(T_END, T + dt);
    if (freeze != null) T = freeze;
    setPhase(T);
    const st = introState(T);
    // dopo l'intro: ogni tanto una passata di luce sulla carrozzeria
    if (T >= T_END) {
      glintT += dt;
      const g = ((glintT + 6) % 9) / 2.6;
      if (g < 1) { U.uBarX.value = lerp(3.6, -3.8, eio(g)); U.uBarI.value = Math.sin(Math.PI * g) * .55; }
      else U.uBarI.value = 0;
    }
    U.uHead.value = st.hd * 3.2; U.uTail.value = st.hd * 3;
    envMats.forEach(m => { m.envMapIntensity = lerp(.035, 1, st.env); });
    FU.uEnv.value = st.env; FU.uBarX.value = U.uBarX.value; FU.uBarI.value = U.uBarI.value; FU.uHead.value = st.hd; FU.uTail.value = st.hd; FU.uTime.value = time;
    const mu = motes.material.uniforms; mu.uTime.value = time; mu.uEnv.value = st.env; mu.uHead.value = st.hd;
    gate.position.x = U.uBarX.value; gateM.color.setScalar(2.6 * U.uBarI.value); gate.visible = T < T_END && U.uBarI.value > .01;
    bloom.strength = st.bl;
    finish.uniforms.uTime.value = time; finish.uniforms.uFade.value = st.fade;

    // camera
    let target;
    if (T < T_END) target = introPose(T);
    else {
      target = stagePose(scrollStage());
      if (!drag && now - lastTouch > 2200) { dragAz *= Math.pow(.25, dt); dragEl *= Math.pow(.25, dt); }
    }
    if (!cur || T < T_END || hq) cur = target;
    else { const k = 1 - Math.exp(-dt * 5); cur = mix(cur, target, k); }
    const par = T >= T_END ? 1 : 0;
    applyPose(cur, dragAz + mx * 4 * par + Math.sin(time * .25) * .6 * par, dragEl - my * 2 * par);
    composer.render(dt);
    adapt(dt);
  }
  function loop(now) { requestAnimationFrame(loop); if (!visible || !running) { last = 0; return; } render(now); }

  // prima compila gli shader (niente scatti), poi parte l'orologio
  applyPose(resolved(P.i0));
  try { renderer.compile(scene, camera); } catch (e) {}
  render(performance.now());
  requestAnimationFrame(t => { last = t; clockOn = true; ROOT.classList.add('gl-on'); requestAnimationFrame(loop); });

  return {
    skip() { if (T < T_END) { T = T_END; cur = null; } },
    seek(t) { T = t; },
    setColor(hex) { U.uPaint.value.set(hex); syncPaint(); },
    get phase() { return phase; },
    renderer, scene, camera,
  };
}
