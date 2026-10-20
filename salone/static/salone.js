// Salone di giorno — padiglione bianco con specchio d'acqua, porte di vetro che scorrono, sala in marmo con colonne,
// finestre ad arco, tende di velo, scultura cinetica e galleria finale. Tutto legato allo scorrimento della pagina.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { RectAreaLightUniformsLib } from 'three/addons/lights/RectAreaLightUniformsLib.js';

const CFG = window.SALONE || {};
const Q = new URLSearchParams(location.search);
const FORCE = Q.has('p') ? +Q.get('p') : null;            // ?p=0.5 : fotogramma fisso (per i controlli)
const MOBILE = matchMedia('(max-width: 820px)').matches || /Android|iPhone|iPad/i.test(navigator.userAgent);
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const canvas = document.getElementById('scena');
const show = document.getElementById('apertura');
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const V = (x, y, z) => new THREE.Vector3(x, y, z);

// palette (uguale al CSS)
const C = { bianco: 0xfbfcfd, nebbia: 0xeef3f7, azzurro: 0xa9c4da, acciaio: 0x5e7a93, inchiostro: 0x1f2b37, argento: 0xc9d3dc };

function pronto() { window.dispatchEvent(new Event('salone-pronto')); }
const avvia = () => { try { init(); } catch (e) { console.warn('3D non disponibile', e); document.documentElement.classList.add('nogl'); pronto(); } };
Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 2500))]).then(avvia);   // l'insegna usa il carattere del sito

function init() {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: !MOBILE, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, MOBILE ? 1.4 : 1.75));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.92;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = !MOBILE;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  RectAreaLightUniformsLib.init();

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0xeaf1f7, 70, 260);
  const pm = new THREE.PMREMGenerator(renderer);
  scene.environment = pm.fromScene(new RoomEnvironment(), 0.03).texture;
  scene.environmentIntensity = 0.75;

  const camera = new THREE.PerspectiveCamera(42, 1, 0.05, 400);
  const T = textures();
  const mondo = costruisci(scene, T);

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.22, 0.5, 1.05);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  // ---- la ripresa: specchio d'acqua -> porte -> sala -> scultura -> galleria ----
  const P = new THREE.CatmullRomCurve3([
    V(0, 3.0, 46), V(0, 2.2, 30), V(0, 1.75, 16), V(0, 1.7, 6), V(0, 1.75, -3),
    V(-4.2, 2.1, -11), V(-4.6, 2.6, -16.5), V(5.2, 3.0, -18.5), V(5.4, 2.4, -27), V(0.8, 2.2, -33.5), V(0, 2.5, -36.2)], false, 'centripetal');
  const L = new THREE.CatmullRomCurve3([
    V(0, 2.6, 0), V(0, 3.4, 0), V(0, 3.6, -2), V(0, 2.8, -12), V(1, 2.4, -16),
    V(4.8, 1.2, -14), V(0, 3.0, -21), V(0, 3.4, -21), V(-1.5, 3.0, -30), V(0, 3.4, -42), V(0, 3.5, -42)], false, 'centripetal');
  const copies = [...document.querySelectorAll('.racconto [data-da]')];
  const prog = document.getElementById('avanzamento');
  const velo = document.getElementById('velo');
  const nav = document.getElementById('nav');

  let target = 0, p = FORCE ?? 0, mx = 0, my = 0, w = 0, h = 0, visibile = true, primo = true;
  function misura() {
    w = canvas.clientWidth; h = canvas.clientHeight;
    renderer.setSize(w, h, false); composer.setSize(w, h);
    camera.aspect = w / h; camera.fov = w / h < 0.8 ? 58 : 42; camera.updateProjectionMatrix();
  }
  function leggiScroll() {
    const r = show.getBoundingClientRect();
    target = FORCE ?? clamp(-r.top / (r.height - innerHeight));
    visibile = r.bottom > 0;
    nav.classList.toggle('pieno', r.bottom < innerHeight * 0.6);
  }
  addEventListener('resize', () => { misura(); leggiScroll(); });
  addEventListener('scroll', leggiScroll, { passive: true });
  addEventListener('pointermove', e => { mx = e.clientX / innerWidth - 0.5; my = e.clientY / innerHeight - 0.5; }, { passive: true });
  misura(); leggiScroll();

  const pos = new THREE.Vector3(), look = new THREE.Vector3();
  const clock = new THREE.Clock();
  let fotogrammi = 0;
  function frame() {
    if (FORCE != null && ++fotogrammi > 3) return;     // fotogramma fisso: si disegna e ci si ferma
    requestAnimationFrame(frame);
    const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
    p += (target - p) * (REDUCED || FORCE != null ? 1 : 1 - Math.pow(0.0025, dt));
    if (!visibile && !primo) return;
    const e = clamp(p);
    P.getPointAt(Math.min(e, 0.999), pos); L.getPointAt(Math.min(e, 0.999), look);
    camera.position.copy(pos).add(V(mx * 0.35, -my * 0.2, 0));
    camera.lookAt(look);

    // porte di vetro: scorrono nel muro
    const ap = smooth(0.16, 0.3, e);
    mondo.porte[0].position.x = -1.5 - ap * 3.05; mondo.porte[1].position.x = 1.5 + ap * 3.05;
    mondo.lucePorta.intensity = 1 + ap * 5;
    // scultura cinetica e tende
    const s = REDUCED ? 0 : t;
    mondo.anelli.forEach((a, i) => { a.rotation.x = s * (0.11 + i * 0.04) + i; a.rotation.y = s * (0.07 + i * 0.03); });
    mondo.scultura.position.y = 3.2 + Math.sin(s * 0.6) * 0.08;
    mondo.tende.forEach(tn => onda(tn, s));
    mondo.acqua.material.uniforms && (mondo.acqua.material.uniforms.time && (mondo.acqua.material.uniforms.time.value = s));

    // testi sopra la scena
    for (const el of copies) {
      const a = +el.dataset.da, b = +el.dataset.a;
      el.classList.toggle('on', e >= a && e <= b);
    }
    prog.style.transform = `scaleX(${e})`;
    velo.style.opacity = smooth(0.9, 1, e);
    composer.render();
    if (primo) { primo = false; pronto(); }
  }
  frame();
}

// ===================================================================================
function costruisci(scene, T) {
  const m = {
    bianco: new THREE.MeshStandardMaterial({ color: 0xf7f9fb, roughness: 0.62, map: T.intonaco }),
    biancoLiscio: new THREE.MeshStandardMaterial({ color: 0xfafbfc, roughness: 0.35 }),
    pietra: new THREE.MeshStandardMaterial({ color: 0xe9ecee, roughness: 0.85, map: T.pietra }),
    marmo: new THREE.MeshPhysicalMaterial({ map: T.marmo, roughness: 0.16, clearcoat: 0.6, clearcoatRoughness: 0.12, transparent: true, opacity: 0.78 }),
    argento: new THREE.MeshStandardMaterial({ color: 0xdfe5ea, metalness: 1, roughness: 0.22 }),
    azzurro: new THREE.MeshStandardMaterial({ color: C.azzurro, roughness: 0.5 }),
    velluto: new THREE.MeshPhysicalMaterial({ color: 0xb8cfe2, roughness: 0.9, sheen: 1, sheenColor: new THREE.Color(0xeaf3fb), sheenRoughness: 0.5 }),
    vetro: new THREE.MeshPhysicalMaterial({ color: 0xeaf4fb, metalness: 0, roughness: 0.04, transparent: true, opacity: 0.22, envMapIntensity: 1.6, side: THREE.DoubleSide }),
    luce: new THREE.MeshBasicMaterial({ color: 0xffffff }),
    luceAzzurra: new THREE.MeshBasicMaterial({ color: new THREE.Color(0xd6e9f7).multiplyScalar(1.4) }),
    finestra: new THREE.MeshBasicMaterial({ color: new THREE.Color(0xf4f9fd).multiplyScalar(1.25) }),
  };
  const add = (geo, mat, x = 0, y = 0, z = 0, ombra = false, ricevi = false) => {
    const o = new THREE.Mesh(geo, mat); o.position.set(x, y, z); o.castShadow = ombra; o.receiveShadow = ricevi; scene.add(o); return o;
  };
  const box = (w, h, d) => new THREE.BoxGeometry(w, h, d);

  // ---- cielo di giorno: bianco all'orizzonte, azzurrino in alto ----
  const cielo = new THREE.Mesh(new THREE.SphereGeometry(300, 32, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { alto: { value: new THREE.Color(0x7fa8cc) }, basso: { value: new THREE.Color(0xeef4f9) } },
    vertexShader: 'varying vec3 vp; void main(){ vp = normalize(position); gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.); }',
    fragmentShader: 'uniform vec3 alto; uniform vec3 basso; varying vec3 vp; void main(){ float t = pow(clamp(vp.y*1.6,0.,1.),0.7); gl_FragColor = vec4(mix(basso,alto,t),1.); }',
  }));
  scene.add(cielo);

  // ---- luci: sole basso del mattino + cielo + luce che esce dalla porta ----
  scene.add(new THREE.HemisphereLight(0xdbe9f5, 0xe9e4dc, 1.05));
  const sole = new THREE.DirectionalLight(0xfff6ea, 3.2);
  sole.position.set(-26, 30, 34); sole.target.position.set(0, 0, -8);
  sole.castShadow = true; sole.shadow.mapSize.set(2048, 2048); sole.shadow.bias = -0.0004; sole.shadow.normalBias = 0.03;
  Object.assign(sole.shadow.camera, { left: -32, right: 32, top: 34, bottom: -40, near: 1, far: 120 });
  scene.add(sole, sole.target);
  const lucePorta = new THREE.PointLight(0xf2f8fd, 1, 22, 1.6); lucePorta.position.set(0, 4, -3); scene.add(lucePorta);

  // ---- esterno: piazza in pietra, specchio d'acqua, passerella ----
  add(new THREE.PlaneGeometry(220, 160).rotateX(-Math.PI / 2), m.pietra, 0, 0, 40, false, true);
  const acqua = new Reflector(new THREE.PlaneGeometry(24, 16), {
    textureWidth: MOBILE ? 512 : 1024, textureHeight: MOBILE ? 512 : 1024, color: 0xb9d0e2, clipBias: 0.003 });
  acqua.rotation.x = -Math.PI / 2; acqua.position.set(0, 0.02, 15); scene.add(acqua);
  add(box(25, 0.16, 0.5), m.biancoLiscio, 0, 0.08, 23.2, false, true);
  add(box(25, 0.16, 0.5), m.biancoLiscio, 0, 0.08, 6.8, false, true);
  add(box(0.5, 0.16, 16.9), m.biancoLiscio, -12.3, 0.08, 15, false, true);
  add(box(0.5, 0.16, 16.9), m.biancoLiscio, 12.3, 0.08, 15, false, true);
  for (let z = 22; z >= 8; z -= 1.75) add(box(3.2, 0.1, 1.25), m.biancoLiscio, 0, 0.09, z, true, true);   // lastre sull'acqua
  // gradini davanti all'ingresso
  for (let i = 0; i < 3; i++) add(box(10 - i * 0.8, 0.14, 1.2 - i * 0.3), m.biancoLiscio, 0, 0.07 + i * 0.14, 6 - i * 0.35 - 0.6, false, true);
  // sfere di pietra chiara e alberi stilizzati ai lati
  for (const sx of [-1, 1]) {
    for (let k = 0; k < 3; k++) add(new THREE.SphereGeometry(0.55 - k * 0.12, 32, 16), m.biancoLiscio, sx * (8.2 + k * 1.1), 0.5 - k * 0.12, 4.2 - k * 0.6, true, true);
    for (const z of [26, 32]) {
      add(new THREE.CylinderGeometry(0.07, 0.1, 3.4, 8), m.argento, sx * 16, 1.7, z, true);
      add(new THREE.SphereGeometry(1.5, 24, 16).scale(1, 1.25, 1), new THREE.MeshStandardMaterial({ color: 0xdfe8de, roughness: 0.95 }), sx * 16, 4.4, z, true, true);
    }
  }

  // ---- edificio: facciata bianca con lame verticali, pensilina sottile, insegna ----
  const LUNGO = 42, ALTO = 9, LARGO = 30;
  add(box(12, ALTO, 0.6), m.bianco, -9, ALTO / 2, 0.3, true, true);
  add(box(12, ALTO, 0.6), m.bianco, 9, ALTO / 2, 0.3, true, true);
  add(box(6.2, ALTO - 6.2, 0.6), m.bianco, 0, 6.2 + (ALTO - 6.2) / 2, 0.3, true, true);
  for (let x = -14.4; x <= 14.41; x += 0.9) {
    if (Math.abs(x) < 3.4) continue;
    add(box(0.08, ALTO - 0.4, 0.55), m.biancoLiscio, x, ALTO / 2, 0.85, true);
  }
  add(box(LARGO + 0.4, 0.22, 1.2), m.biancoLiscio, 0, ALTO + 0.11, 0.6, true, true);          // cornice in alto
  add(box(8.4, 0.18, 4.2), m.biancoLiscio, 0, 6.35, 2.1, true, true);                          // pensilina
  add(box(8.4, 0.03, 0.04), m.luceAzzurra, 0, 6.24, 4.18);                                     // filo di luce sotto la pensilina
  const insegna = add(new THREE.PlaneGeometry(9, 1.2), new THREE.MeshBasicMaterial({ map: T.insegna, transparent: true }), 0, 7.65, 0.62);
  insegna.renderOrder = 2;
  // telaio della porta e porte di vetro
  add(box(6.3, 0.12, 0.3), m.argento, 0, 6.15, 0.1);
  add(box(0.1, 6.2, 0.3), m.argento, -3.1, 3.1, 0.1); add(box(0.1, 6.2, 0.3), m.argento, 3.1, 3.1, 0.1);
  const porte = [-1, 1].map(sx => {
    const g = new THREE.Group();
    const lastra = new THREE.Mesh(box(2.96, 6.0, 0.04), m.vetro); lastra.position.y = 3.05; g.add(lastra);
    for (const [w, hh, x, y] of [[3, 0.06, 0, 0.06], [3, 0.06, 0, 6.03], [0.06, 6, -1.47, 3.05], [0.06, 6, 1.47, 3.05]]) {
      const f = new THREE.Mesh(box(w, hh, 0.07), m.argento); f.position.set(x, y, 0); g.add(f);
    }
    const man = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 1.6, 12), m.argento); man.position.set(-sx * 1.2, 3, 0.09); g.add(man);
    g.position.set(sx * 1.5, 0, 0.12); scene.add(g); return g;
  });

  // ---- sala: pareti con finestre ad arco, soffitto a cassettoni, lucernario ----
  const parete = new THREE.Shape([V(0, 0, 0), V(LUNGO, 0, 0), V(LUNGO, ALTO, 0), V(0, ALTO, 0)].map(v => new THREE.Vector2(v.x, v.y)));
  const archi = [];
  for (let z = 4; z <= LUNGO - 4; z += 6) {
    const foro = new THREE.Path(); const r = 1.45;
    foro.moveTo(z - r, 0.6); foro.lineTo(z + r, 0.6); foro.lineTo(z + r, 5.6); foro.absarc(z, 5.6, r, 0, Math.PI, false); foro.lineTo(z - r, 0.6);
    parete.holes.push(foro); archi.push(z);
  }
  const pg = new THREE.ExtrudeGeometry(parete, { depth: 0.5, bevelEnabled: false });
  for (const sx of [-1, 1]) {
    const pw = new THREE.Mesh(pg, m.bianco); pw.receiveShadow = true;
    if (sx < 0) { pw.rotation.y = Math.PI / 2; pw.position.set(-LARGO / 2, 0, 0); }
    else { pw.rotation.y = -Math.PI / 2; pw.position.set(LARGO / 2, 0, -LUNGO); }
    scene.add(pw);
    // luce del giorno dietro le finestre + montanti sottili + fasci di luce
    for (const z of archi) {
      const zz = sx < 0 ? -z : -(LUNGO - z);
      const vetro = new THREE.Mesh(new THREE.PlaneGeometry(3, 6.6), m.finestra);
      vetro.position.set(sx * (LARGO / 2 + 0.9), 3.6, zz); vetro.rotation.y = -sx * Math.PI / 2; scene.add(vetro);
      for (const dz of [-0.75, 0, 0.75]) add(box(0.04, 5.8, 0.04), m.argento, sx * (LARGO / 2 + 0.25), 3.3, zz + dz);
      add(box(0.04, 0.04, 2.9), m.argento, sx * (LARGO / 2 + 0.25), 3.0, zz);
      const fascio = new THREE.Mesh(new THREE.PlaneGeometry(2.6, 11), new THREE.MeshBasicMaterial({
        map: T.fascio, transparent: true, opacity: 0.32, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide }));
      fascio.rotation.order = 'ZYX'; fascio.rotation.set(0, Math.PI / 2, -sx * 0.7);
      fascio.position.set(sx * 11.4, 3.3, zz); scene.add(fascio);
    }
  }
  add(box(LARGO, ALTO, 0.5), new THREE.MeshStandardMaterial({ color: 0xd9e7f2, roughness: 0.8, map: T.intonaco }), 0, ALTO / 2, -LUNGO - 0.25, false, true);   // parete di fondo azzurrina
  add(box(LARGO, 0.3, LUNGO), m.biancoLiscio, 0, ALTO + 0.15, -LUNGO / 2);                  // soffitto
  add(box(3.6, 0.05, LUNGO - 6), m.luce, 0, ALTO - 0.02, -LUNGO / 2);                       // lucernario
  add(box(LARGO, 0.02, LUNGO), new THREE.MeshStandardMaterial({ color: 0xe3edf5, roughness: 0.9 }), 0, ALTO - 0.01, -LUNGO / 2);   // fondo dei cassettoni
  for (let z = -3; z >= -LUNGO + 3; z -= 3) add(box(LARGO, 0.35, 0.18), m.biancoLiscio, 0, ALTO - 0.2, z);   // travetti
  for (const x of [-9, 9]) add(box(0.18, 0.35, LUNGO), m.biancoLiscio, x, ALTO - 0.2, -LUNGO / 2);
  for (const z of [-10, -21, -32]) {
    const la = new THREE.RectAreaLight(0xf4f9ff, 3.2, 3.4, 8); la.position.set(0, ALTO - 0.1, z); la.lookAt(0, 0, z); scene.add(la);
  }

  // pavimento in marmo con riflesso
  const rif = new Reflector(new THREE.PlaneGeometry(LARGO, LUNGO), { textureWidth: MOBILE ? 512 : 1280, textureHeight: MOBILE ? 512 : 1280, color: 0x9fb0c0, clipBias: 0.003 });
  rif.rotation.x = -Math.PI / 2; rif.position.set(0, 0.005, -LUNGO / 2); scene.add(rif);
  add(new THREE.PlaneGeometry(LARGO, LUNGO).rotateX(-Math.PI / 2), m.marmo, 0, 0.012, -LUNGO / 2, false, true);
  const tappeto = new THREE.MeshPhysicalMaterial({ color: 0xaac5db, roughness: 1, sheen: 0.6, sheenColor: new THREE.Color(0xeef5fb) });
  add(box(3.2, 0.02, 15), tappeto, 0, 0.02, -8.6, false, true);                                   // passatoia azzurrina fino alla pedana

  // colonne
  const fusto = new THREE.CylinderGeometry(0.3, 0.33, ALTO - 1.0, 40, 1);
  const base = new THREE.CylinderGeometry(0.5, 0.52, 0.26, 40);
  const capitello = new THREE.CylinderGeometry(0.52, 0.34, 0.5, 40);
  for (const x of [-8, 8]) for (let z = -5; z >= -LUNGO + 5; z -= 6) {
    add(fusto, m.biancoLiscio, x, ALTO / 2 - 0.25, z, true, true);
    add(base, m.biancoLiscio, x, 0.13, z, true, true);
    add(capitello, m.biancoLiscio, x, ALTO - 0.7, z, true);
    add(new THREE.TorusGeometry(0.335, 0.012, 8, 48).rotateX(Math.PI / 2), m.argento, x, 1.4, z);
  }

  // lampade ad anello sospese
  for (const z of [-7, -14, -28, -35]) {
    add(new THREE.TorusGeometry(0.85, 0.035, 12, 96).rotateX(Math.PI / 2), m.luceAzzurra, 0, 5.8, z);
    add(new THREE.TorusGeometry(0.55, 0.02, 12, 96).rotateX(Math.PI / 2), m.luce, 0, 5.95, z);
    for (const a of [0, 2.09, 4.19]) add(new THREE.CylinderGeometry(0.006, 0.006, 3.1, 4), m.argento, Math.cos(a) * 0.85, 7.4, z + Math.sin(a) * 0.85);
  }

  // pedana rotonda con scultura cinetica al centro
  add(new THREE.CylinderGeometry(3.2, 3.3, 0.24, 96), m.biancoLiscio, 0, 0.12, -21, true, true);
  add(new THREE.TorusGeometry(3.25, 0.025, 8, 160).rotateX(Math.PI / 2), m.luceAzzurra, 0, 0.03, -21);
  add(new THREE.CylinderGeometry(0.9, 1.1, 1.0, 64), m.marmo.clone(), 0, 0.74, -21, true, true).material.opacity = 1;
  const scultura = new THREE.Group(); scultura.position.set(0, 3.2, -21); scene.add(scultura);
  const anelli = [1.5, 1.22, 0.96].map((r, i) => {
    const a = new THREE.Mesh(new THREE.TorusGeometry(r, 0.035 + i * 0.006, 24, 200), i === 1 ? m.azzurro : m.argento);
    a.castShadow = true; scultura.add(a); return a;
  });
  scultura.add(new THREE.Mesh(new THREE.SphereGeometry(0.22, 48, 24), new THREE.MeshPhysicalMaterial({ color: 0xffffff, roughness: 0.05, transmission: 0.6, thickness: 0.4, envMapIntensity: 2 })));

  // salotto: poltrone in velluto azzurrino, tavolino in marmo
  const salotto = (x, z, ry) => {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = ry; scene.add(g);
    for (const dx of [-1.15, 1.15]) {
      const c = new THREE.Group(); c.position.x = dx; c.rotation.y = -dx * 0.35; g.add(c);
      const seduta = new THREE.Mesh(new RoundedBoxGeometry(0.95, 0.42, 0.9, 5, 0.16), m.velluto); seduta.position.y = 0.42; c.add(seduta);
      const schienale = new THREE.Mesh(new RoundedBoxGeometry(1.0, 0.75, 0.24, 5, 0.11), m.velluto); schienale.position.set(0, 0.8, -0.38); schienale.rotation.x = -0.12; c.add(schienale);
      for (const sx of [-1, 1]) { const br = new THREE.Mesh(new RoundedBoxGeometry(0.16, 0.5, 0.84, 4, 0.07), m.velluto); br.position.set(sx * 0.52, 0.6, 0); c.add(br); }
      for (const [lx, lz] of [[-0.38, -0.32], [0.38, -0.32], [-0.38, 0.32], [0.38, 0.32]]) { const l = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.012, 0.22, 8), m.argento); l.position.set(lx, 0.11, lz); c.add(l); }
      c.traverse(o => { o.castShadow = o.receiveShadow = true; });
    }
    const piano = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.05, 64), m.marmo.clone()); piano.material.opacity = 1; piano.position.set(0, 0.5, 0.7); g.add(piano);
    const gamba = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.2, 0.48, 24), m.argento); gamba.position.set(0, 0.25, 0.7); g.add(gamba);
    const vaso = new THREE.Mesh(new THREE.SphereGeometry(0.13, 32, 16).scale(1, 1.3, 1), m.biancoLiscio); vaso.position.set(0.15, 0.68, 0.65); g.add(vaso);
    const ramo = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.6, 4), m.argento); ramo.position.set(0.17, 1.0, 0.64); ramo.rotation.z = -0.2; g.add(ramo);
    g.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  };
  salotto(5.0, -14, -0.5);
  salotto(-5.2, -29, 0.6);

  // tende di velo davanti a quattro finestre
  const tende = [];
  for (const [x, z, ry] of [[-14.2, -10, Math.PI / 2], [-14.2, -16, Math.PI / 2], [14.2, -22, -Math.PI / 2], [14.2, -34, -Math.PI / 2]]) {
    const g = new THREE.PlaneGeometry(2.2, 7.6, 22, 30);
    const tn = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0xffffff, transparent: true, opacity: 0.55, roughness: 1, side: THREE.DoubleSide, depthWrite: false }));
    tn.position.set(x, 4.3, z + 1.1); tn.rotation.y = ry; tn.userData.base = g.attributes.position.array.slice(); tn.userData.fase = x + z;
    scene.add(tn); tende.push(tn);
  }

  // galleria finale: cornici sulla parete di fondo, nicchia ad arco luminosa
  const nicchia = new THREE.Shape(); nicchia.moveTo(-2.4, 0); nicchia.lineTo(2.4, 0); nicchia.lineTo(2.4, 4.6); nicchia.absarc(0, 4.6, 2.4, 0, Math.PI, false); nicchia.lineTo(-2.4, 0);
  add(new THREE.ShapeGeometry(nicchia, 48), new THREE.MeshBasicMaterial({ map: T.nicchia }), 0, 0, -LUNGO + 0.02);
  for (const [i, x] of [[0, -9.6], [1, -5.6], [2, 5.6], [3, 9.6]]) {
    add(box(3.0, 2.1, 0.08), m.argento, x, 3.6, -LUNGO + 0.06);
    add(new THREE.PlaneGeometry(2.84, 1.94), new THREE.MeshBasicMaterial({ map: T.quadri[i] }), x, 3.6, -LUNGO + 0.11);
    add(box(0.5, 0.03, 0.03), m.luce, x, 4.95, -LUNGO + 0.3);
  }
  add(box(9, 1.05, 1.2), m.biancoLiscio, 0, 0.53, -36.5, true, true);                            // banco accoglienza
  add(box(9.05, 0.05, 1.25), m.marmo.clone(), 0, 1.08, -36.5).material.opacity = 1;
  add(box(9.02, 0.04, 0.02), m.luceAzzurra, 0, 0.12, -35.88);

  return { porte, lucePorta, anelli, scultura, tende, acqua };
}

function onda(tn, t) {
  const a = tn.geometry.attributes.position, b = tn.userData.base, f = tn.userData.fase;
  for (let i = 0; i < a.count; i++) {
    const x = b[i * 3], y = b[i * 3 + 1];
    const peso = (3.8 - y) / 7.6;           // fermo in alto, si muove in basso
    a.array[i * 3 + 2] = Math.sin(x * 2.2 + t * 0.9 + f) * 0.12 * peso + Math.sin(x * 5.1 - t * 1.3) * 0.03 * peso + Math.cos(x * 3) * 0.08;
  }
  a.needsUpdate = true;
}

// ===================================================================================
function textures() {
  const tela = (w, h, fn, rep) => {
    const c = document.createElement('canvas'); c.width = w; c.height = h; fn(c.getContext('2d'), w, h);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
    if (rep) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(...rep); }
    return t;
  };
  const rnd = (() => { let s = 7; return () => (s = (s * 16807) % 2147483647) / 2147483647; })();
  const marmo = tela(1024, 1024, (g, w, h) => {
    g.fillStyle = '#f6f7f8'; g.fillRect(0, 0, w, h);
    for (let i = 0; i < 26000; i++) { g.fillStyle = `rgba(${200 + rnd() * 40},${205 + rnd() * 40},${215 + rnd() * 35},${rnd() * 0.08})`; g.fillRect(rnd() * w, rnd() * h, 2, 2); }
    for (let k = 0; k < 14; k++) {                         // venature grigio-azzurre
      let x = rnd() * w, y = rnd() * h, a = rnd() * Math.PI * 2;
      g.strokeStyle = `rgba(${130 + rnd() * 40},${150 + rnd() * 30},${170 + rnd() * 30},${0.08 + rnd() * 0.16})`; g.lineWidth = 0.6 + rnd() * 1.8;
      g.beginPath(); g.moveTo(x, y);
      for (let s = 0; s < 60; s++) { a += (rnd() - 0.5) * 0.5; x += Math.cos(a) * 14; y += Math.sin(a) * 14; g.lineTo(x, y); }
      g.stroke();
    }
    g.strokeStyle = 'rgba(160,172,184,.35)'; g.lineWidth = 2;           // giunti delle lastre
    for (let i = 0; i <= 2; i++) { g.beginPath(); g.moveTo(i * w / 2, 0); g.lineTo(i * w / 2, h); g.moveTo(0, i * h / 2); g.lineTo(w, i * h / 2); g.stroke(); }
  }, [6, 8]);
  const intonaco = tela(256, 256, (g, w, h) => {
    g.fillStyle = '#ffffff'; g.fillRect(0, 0, w, h);
    for (let i = 0; i < 9000; i++) { const v = 238 + rnd() * 17; g.fillStyle = `rgb(${v},${v},${v + 2})`; g.fillRect(rnd() * w, rnd() * h, 1, 1); }
  }, [8, 4]);
  const pietra = tela(512, 512, (g, w, h) => {
    g.fillStyle = '#eceeee'; g.fillRect(0, 0, w, h);
    for (let i = 0; i < 20000; i++) { const v = 215 + rnd() * 35; g.fillStyle = `rgba(${v},${v},${v - 4},.5)`; g.fillRect(rnd() * w, rnd() * h, 2, 2); }
    g.strokeStyle = 'rgba(170,175,178,.45)'; g.lineWidth = 2;
    for (let i = 0; i <= 4; i++) { g.beginPath(); g.moveTo(0, i * h / 4); g.lineTo(w, i * h / 4); g.stroke(); }
    for (let r = 0; r < 4; r++) for (let i = 0; i <= 2; i++) { const x = (i + (r % 2) * 0.5) * w / 2; g.beginPath(); g.moveTo(x, r * h / 4); g.lineTo(x, (r + 1) * h / 4); g.stroke(); }
  }, [40, 30]);
  const insegna = tela(2048, 272, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#5e7a93'; g.textAlign = 'center'; g.textBaseline = 'middle';
    const nome = (CFG.nome || 'Salone').toUpperCase().split('').join(String.fromCharCode(8202));
    g.font = '300 150px "Cormorant Garamond", Georgia, serif'; g.fillText(nome, w / 2, h / 2 + 6);
  });
  const fascio = tela(128, 512, (g, w, h) => {
    const gr = g.createLinearGradient(0, 0, 0, h); gr.addColorStop(0, 'rgba(255,255,255,0.9)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = gr; g.fillRect(0, 0, w, h);
    const gx = g.createLinearGradient(0, 0, w, 0); gx.addColorStop(0, 'rgba(0,0,0,1)'); gx.addColorStop(0.5, 'rgba(0,0,0,0)'); gx.addColorStop(1, 'rgba(0,0,0,1)');
    g.globalCompositeOperation = 'destination-out'; g.fillStyle = gx; g.fillRect(0, 0, w, h);
  });
  const nicchia = tela(512, 1024, (g, w, h) => {
    const gr = g.createRadialGradient(w / 2, h * 0.75, 20, w / 2, h * 0.6, h * 0.7);
    gr.addColorStop(0, '#ffffff'); gr.addColorStop(0.5, '#e4eff8'); gr.addColorStop(1, '#bcd3e6');
    g.fillStyle = gr; g.fillRect(0, 0, w, h);
  });
  const toni = [['#dbe8f2', '#9fbcd4'], ['#eef3f6', '#c3d2de'], ['#e6eef4', '#a9c4da'], ['#f2f5f7', '#b7c7d3']];
  const quadri = toni.map(([a, b], i) => tela(512, 352, (g, w, h) => {
    const gr = g.createLinearGradient(0, 0, w, h); gr.addColorStop(0, a); gr.addColorStop(1, b); g.fillStyle = gr; g.fillRect(0, 0, w, h);
    g.strokeStyle = 'rgba(255,255,255,.75)'; g.lineWidth = 2.5;                 // linea di un'auto, astratta
    g.beginPath(); const y = h * 0.62;
    g.moveTo(w * 0.12, y); g.bezierCurveTo(w * 0.22, y - 40 - i * 6, w * 0.36, y - 78, w * 0.55, y - 80); g.bezierCurveTo(w * 0.72, y - 78, w * 0.84, y - 34, w * 0.9, y);
    g.stroke(); g.beginPath(); g.moveTo(w * 0.08, y + 2); g.lineTo(w * 0.94, y + 2); g.stroke();
    for (const cx of [0.3, 0.74]) { g.beginPath(); g.arc(w * cx, y + 2, 26, Math.PI, 0); g.stroke(); }
  }));
  return { marmo, intonaco, pietra, insegna, fascio, nicchia, quadri };
}
