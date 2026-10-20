// Prestige Service — un unico mondo 3D, una sola ripresa continua legata allo scorrimento:
// piazzale di sera -> vetrata che si apre -> salone in marmo con colonne blu notte -> giro attorno alla Ferrari ->
// ci si siede al posto di guida, si accendono le luci -> si esce dal parabrezza verso la parete del salone.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { RectAreaLightUniformsLib } from 'three/addons/lights/RectAreaLightUniformsLib.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';

const CFG = window.SALONE || {};
const Q = new URLSearchParams(location.search);
const FORCE = Q.has('p') && !isNaN(+Q.get('p')) ? +Q.get('p') : null;      // ?p=0.5 : fotogramma fisso (controlli)
const MOBILE = matchMedia('(max-width: 820px)').matches || /Android|iPhone|iPad/i.test(navigator.userAgent);
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const MODELLO = window.SALONE_MODELLO || '/modelli/ferrari.glb';
const OMBRA_AUTO = window.SALONE_OMBRA || '/modelli/ferrari_ao.png';
const DRACO = (document.querySelector('script[type=importmap]')?.textContent.match(/"three\/addons\/":\s*"([^"]+)"/) || [])[1] + 'libs/draco/gltf/';
const canvas = document.getElementById('scena');
const show = document.getElementById('apertura');
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const V = (x, y, z) => new THREE.Vector3(x, y, z);
const pronto = () => window.dispatchEvent(new Event('salone-pronto'));

const NAVY = 0x111c30;

const avvia = () => { try { init(); } catch (e) { console.warn('3D non disponibile', e); document.documentElement.classList.add('nogl'); pronto(); } };
Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 2500))]).then(avvia);

function init() {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: !MOBILE, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, MOBILE ? 1.3 : 1.75));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.8;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = !MOBILE;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  RectAreaLightUniformsLib.init();

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x1a2740, 60, 220);
  const pm = new THREE.PMREMGenerator(renderer);
  scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.35;
  const camera = new THREE.PerspectiveCamera(45, 1, 0.03, 600);

  const T = textures();
  const mondo = costruisci(scene, T);

  // riflessi veri sull'auto: una fotografia del salone a 360° fatta una volta sola
  const cubo = new THREE.WebGLCubeRenderTarget(MOBILE ? 128 : 256, { type: THREE.HalfFloatType });
  const cuboCam = new THREE.CubeCamera(0.1, 200, cubo);
  cuboCam.position.set(0, 1.4, -14); scene.add(cuboCam);
  let envSalone = null;

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  composer.addPass(new UnrealBloomPass(new THREE.Vector2(256, 256), MOBILE ? 0.35 : 0.45, 0.5, 0.96));
  composer.addPass(new OutputPass());

  // ---- la Ferrari ----
  const AUTO = { pos: V(0, 0, -14), ry: 0.62 };
  const auto = new THREE.Group(); auto.position.copy(AUTO.pos); auto.rotation.y = AUTO.ry; scene.add(auto);
  const mat = {
    carrozzeria: new THREE.MeshPhysicalMaterial({ color: 0x1a2b4c, metalness: 0.9, roughness: 0.38, clearcoat: 1, clearcoatRoughness: 0.03 }),
    dettagli: new THREE.MeshStandardMaterial({ color: 0xe8ecf0, metalness: 1, roughness: 0.28 }),
    vetro: new THREE.MeshPhysicalMaterial({ color: 0xffffff, metalness: 0.2, roughness: 0, transparent: true, opacity: 0.16, envMapIntensity: 1.4, depthWrite: false }),
  };
  const interni = [];
  let caricata = false;
  const loader = new GLTFLoader();
  const draco = new DRACOLoader(); draco.setDecoderPath(DRACO); loader.setDRACOLoader(draco);
  loader.load(MODELLO, gltf => {
    const m = gltf.scene.children[0];
    m.getObjectByName('body').material = mat.carrozzeria;
    ['rim_fl', 'rim_fr', 'rim_rr', 'rim_rl', 'trim'].forEach(n => { const o = m.getObjectByName(n); if (o) o.material = mat.dettagli; });
    m.getObjectByName('glass').material = mat.vetro;
    m.traverse(o => {
      if (!o.isMesh) return;
      o.castShadow = true;
      const n = (o.material?.name || '').toLowerCase();
      if (n === 'leather_red' || n === 'leather' || n === 'interior_dark') { o.material = o.material.clone(); o.material.color = new THREE.Color(0x1b2945); o.material.roughness = 0.55; interni.push(o.material); }   // pelle blu notte
      else if (n === 'interior_light') { o.material = o.material.clone(); o.material.color = new THREE.Color(0xd9d2c4); o.material.roughness = 0.6; interni.push(o.material); }  // pelle crema
    });
    const ombra = new THREE.Mesh(new THREE.PlaneGeometry(0.655 * 4, 1.3 * 4), new THREE.MeshBasicMaterial({
      map: new THREE.TextureLoader().load(OMBRA_AUTO), blending: THREE.MultiplyBlending, toneMapped: false, transparent: true }));
    ombra.rotation.x = -Math.PI / 2; ombra.position.y = 0.02; ombra.renderOrder = 2; m.add(ombra);
    auto.add(m);
    // altre due auto in esposizione, nere
    for (const [x, z, ry] of [[-10.5, -26, 1.1], [10.5, -27, -1.15]]) {
      const c = m.clone(true); const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = ry;
      c.getObjectByName('body').material = new THREE.MeshPhysicalMaterial({ color: 0x0b0d12, metalness: 0.85, roughness: 0.32, clearcoat: 1, clearcoatRoughness: 0.03 });
      g.add(c); scene.add(g);
    }
    caricata = true;
  }, undefined, e => { console.warn('auto non caricata', e); caricata = true; });

  // ---- la ripresa: un'unica curva per la posizione e una per lo sguardo ----
  auto.updateMatrixWorld(true);
  const AW = v => auto.localToWorld(v.clone());
  const occhio = AW(V(-0.36, 1.07, 0.12)), avanti = AW(V(-0.36, 0.98, -4));        // posto di guida (guida a sinistra)
  const P = new THREE.CatmullRomCurve3([
    V(0, 1.9, 52), V(0, 1.75, 34), V(0, 1.65, 17), V(0, 1.65, 6), V(0, 1.7, -2),
    V(-4.2, 1.75, -7.5), AW(V(-4.8, 1.55, 1.2)), AW(V(-3.6, 1.35, 3.4)), AW(V(-1.55, 1.15, 0.25)), occhio, occhio.clone().add(V(0, 0.01, 0)),
    AW(V(-0.36, 1.25, -2.2)), V(0, 2.6, -24), V(0, 3.2, -31)], false, 'centripetal', 0.5);
  const L = new THREE.CatmullRomCurve3([
    V(0, 4.2, 0), V(0, 3.6, 0), V(0, 3.0, -2), V(0, 2.4, -10), V(0, 1.4, -14),
    V(0, 0.9, -14), AUTO.pos.clone().add(V(0, 0.75, 0)), AUTO.pos.clone().add(V(0, 0.8, 0)), AW(V(-0.36, 1.0, -0.6)), avanti, avanti,
    AW(V(-0.36, 1.2, -8)), V(0, 3.2, -36), V(0, 3.4, -40)], false, 'centripetal', 0.5);

  // a che punto dello scorrimento si arriva a ogni tappa della ripresa (stesso numero di punti delle curve)
  const TAPPE = [0, 0.08, 0.16, 0.22, 0.3, 0.38, 0.46, 0.54, 0.62, 0.69, 0.79, 0.85, 0.93, 1];
  function tappa(e) {
    let i = 0; while (i < TAPPE.length - 2 && e > TAPPE[i + 1]) i++;
    const f = clamp((e - TAPPE[i]) / (TAPPE[i + 1] - TAPPE[i]));
    return (i + f * f * (3 - 2 * f) * 0.5 + f * 0.5) / (TAPPE.length - 1);
  }
  const copies = [...document.querySelectorAll('.racconto [data-da]')];
  const prog = document.getElementById('avanzamento'), velo = document.getElementById('velo'), nav = document.getElementById('nav');
  let target = FORCE ?? 0, p = target, w = 0, h = 0, visibile = true, mx = 0, my = 0;
  function misura() {
    w = canvas.clientWidth; h = canvas.clientHeight;
    renderer.setSize(w, h, false); composer.setSize(w, h);
    camera.aspect = w / h; camera.fov = w / h < 0.8 ? 62 : 45; camera.updateProjectionMatrix();
  }
  function leggi() {
    const r = show.getBoundingClientRect();
    if (FORCE == null) target = clamp(-r.top / Math.max(1, r.height - innerHeight));
    visibile = r.bottom > 0;
    nav.classList.toggle('pieno', r.bottom < innerHeight * 0.6);
    nav.classList.toggle('su-foto', r.bottom >= innerHeight * 0.6);
  }
  addEventListener('resize', () => { misura(); leggi(); });
  addEventListener('scroll', leggi, { passive: true });
  addEventListener('pointermove', e => { mx = e.clientX / innerWidth - 0.5; my = e.clientY / innerHeight - 0.5; }, { passive: true });
  misura(); leggi();

  const pos = new THREE.Vector3(), look = new THREE.Vector3(), lookS = new THREE.Vector3(), clock = new THREE.Clock();
  let fotogrammi = 0, attesa = 0, primo = true, lookPronto = false;
  function frame() {
    requestAnimationFrame(frame);
    if (FORCE != null) { if (!caricata && attesa++ < 600) return; if (fotogrammi > 4) return; }
    const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
    if (!visibile && !primo) return;
    // movimento morbido: si insegue lo scorrimento con un'inerzia leggera
    p += (target - p) * (REDUCED || FORCE != null ? 1 : 1 - Math.pow(0.02, dt));
    const e = clamp(p);
    if (!envSalone && caricata) {                     // riflessi del salone sull'auto
      auto.visible = false; mondo.rifP.visible = mondo.rif.visible = false;
      cuboCam.update(renderer, scene); auto.visible = true;
      envSalone = cubo.texture;
      for (const k of ['carrozzeria', 'dettagli', 'vetro']) { mat[k].envMap = envSalone; mat[k].needsUpdate = true; }
      mat.carrozzeria.envMapIntensity = 1.3;
    }
    mondo.rifP.visible = camera.position.z > -2; mondo.rif.visible = camera.position.z < 9;   // un solo specchio alla volta
    const u = tappa(e); P.getPoint(u, pos); L.getPoint(u, look);
    const seduto = smooth(0.66, 0.7, e) * (1 - smooth(0.8, 0.84, e));
    const vivo = REDUCED ? 0 : (1 - seduto * 0.7);
    camera.position.copy(pos).add(V(mx * 0.25 * vivo + Math.sin(t * 0.3) * 0.02 * vivo, -my * 0.12 * vivo + Math.sin(t * 0.4) * 0.015 * vivo, 0));
    if (!lookPronto || FORCE != null) { lookS.copy(look); lookPronto = true; } else lookS.lerp(look, 1 - Math.pow(0.002, dt));
    camera.lookAt(lookS);
    if (seduto > 0 && !REDUCED) camera.rotateY(Math.sin(t * 0.35) * 0.05 * seduto - mx * 0.25 * seduto);   // ci si guarda attorno

    // porte di vetro, luci dell'abitacolo, paletti del viale
    const ap = smooth(0.15, 0.27, e);
    mondo.porte[0].position.x = -1.5 - ap * 3.0; mondo.porte[1].position.x = 1.5 + ap * 3.0;
    const acceso = smooth(0.7, 0.76, e);
    const dentro = smooth(0.6, 0.67, e) * (1 - smooth(0.8, 0.85, e));
    mat.vetro.opacity = 0.16 * (1 - dentro * 0.85);                                  // da dentro il vetro non fa nebbia
    for (const m of interni) { m.emissive.setHex(0x0a1426); m.emissiveIntensity = acceso * 0.5; }
    mondo.luceAbitacolo.intensity = acceso * 1.6;
    if (!REDUCED) mondo.lucine.forEach((l, i) => { l.material.opacity = 0.65 + 0.35 * Math.sin(t * (0.8 + (i % 5) * 0.2) + i); });

    for (const el of copies) el.classList.toggle('on', e >= +el.dataset.da && e <= +el.dataset.a);
    prog.style.transform = `scaleX(${e})`;
    velo.style.opacity = smooth(0.93, 1, e);
    composer.render();
    fotogrammi++;
    if (primo) { primo = false; pronto(); }
  }
  frame();
}

// ===================================================================================
function costruisci(scene, T) {
  const m = {
    navy: new THREE.MeshStandardMaterial({ color: NAVY, roughness: 0.35, metalness: 0.3 }),
    navyOpaco: new THREE.MeshStandardMaterial({ color: 0x16223a, roughness: 0.7 }),
    bianco: new THREE.MeshStandardMaterial({ color: 0xc9c6c0, roughness: 0.7 }),
    travertino: new THREE.MeshStandardMaterial({ color: 0xd9cfbf, roughness: 0.85, map: T.travertino }),
    pietra: new THREE.MeshStandardMaterial({ color: 0x8e949c, roughness: 0.5, map: T.pietra, transparent: true, opacity: 0.78 }),
    marmo: new THREE.MeshPhysicalMaterial({ map: T.marmo, color: 0xbdbab4, roughness: 0.14, clearcoat: 0.7, clearcoatRoughness: 0.08, transparent: true, opacity: 0.84 }),
    marmoPieno: new THREE.MeshPhysicalMaterial({ map: T.marmo, roughness: 0.18, clearcoat: 0.6 }),
    vetro: new THREE.MeshPhysicalMaterial({ color: 0x9fb6cf, roughness: 0.05, metalness: 0.1, transparent: true, opacity: 0.14, envMapIntensity: 2, side: THREE.DoubleSide, depthWrite: false }),
    led: new THREE.MeshBasicMaterial({ color: new THREE.Color(0xffe2b8).multiplyScalar(2.2) }),
    crema: new THREE.MeshPhysicalMaterial({ color: 0xe6dfd2, roughness: 0.85, sheen: 0.6, sheenColor: new THREE.Color(0xffffff) }),
    nero: new THREE.MeshStandardMaterial({ color: 0x0c0f15, roughness: 0.4, metalness: 0.4 }),
    foglia: new THREE.MeshStandardMaterial({ color: 0x6f7d5c, roughness: 0.95 }),
    cipresso: new THREE.MeshStandardMaterial({ color: 0x1e2a1c, roughness: 1 }),
    tronco: new THREE.MeshStandardMaterial({ color: 0x5c5248, roughness: 1 }),
    fiori: new THREE.MeshStandardMaterial({ color: 0xfaf6ee, roughness: 0.9, emissive: 0x3a3428 }),
  };
  const add = (geo, mat, x = 0, y = 0, z = 0, ombra = false, ricevi = false) => {
    const o = new THREE.Mesh(geo, mat); o.position.set(x, y, z); o.castShadow = ombra; o.receiveShadow = ricevi; scene.add(o); return o;
  };
  const box = (w, h, d) => new THREE.BoxGeometry(w, h, d);

  // ---- cielo della sera con nuvole leggere ----
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(400, 48, 24), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    vertexShader: 'varying vec3 vp; void main(){ vp = normalize(position); gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.); }',
    fragmentShader: `varying vec3 vp;
      float h(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }
      float n(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f); return mix(mix(h(i),h(i+vec2(1,0)),f.x), mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x), f.y); }
      float fbm(vec2 p){ float v=0., a=.5; for(int i=0;i<5;i++){ v+=a*n(p); p*=2.03; a*=.5; } return v; }
      void main(){
        float y = clamp(vp.y, 0., 1.);
        vec3 c = mix(vec3(.80,.70,.60), vec3(.30,.38,.55), smoothstep(0., .18, y));
        c = mix(c, vec3(.09,.13,.24), smoothstep(.15, .7, y));
        vec2 uv = vp.xz / max(vp.y, .08) * .55;
        float nu = smoothstep(.45, .8, fbm(uv * 1.3 + vec2(3., 1.)));
        c = mix(c, vec3(.42,.47,.58), nu * .45 * smoothstep(.04, .35, y));
        gl_FragColor = vec4(c, 1.);
      }` })));
  add(new THREE.PlaneGeometry(600, 40), new THREE.MeshBasicMaterial({ color: 0x0f1726, fog: false }), 0, -6, -200);   // colline lontane

  // ---- luci ----
  scene.add(new THREE.HemisphereLight(0x4b5d80, 0x15120e, 0.35));
  const luna = new THREE.DirectionalLight(0x9db4d8, 0.5); luna.position.set(-30, 40, 40); scene.add(luna);
  const caldo = 0xffd9a8;
  for (const [x, z] of [[0, -9], [0, -24]]) {
    const ra = new THREE.RectAreaLight(caldo, 1.6, 22, 6); ra.position.set(x, 8.9, z); ra.lookAt(x, 0, z); scene.add(ra);
  }
  const faro = new THREE.SpotLight(0xfff1dc, 140, 30, 0.42, 0.6, 1.4);         // luce sulla Ferrari
  faro.position.set(-2.5, 8.8, -11); faro.target.position.set(0, 0.5, -14);
  faro.castShadow = !MOBILE; faro.shadow.mapSize.set(1024, 1024); faro.shadow.bias = -0.0005; scene.add(faro, faro.target);
  const luceAbitacolo = new THREE.PointLight(0xbcd2ec, 0, 2.2, 1.5); luceAbitacolo.position.set(-0.1, 1.3, -13.8); scene.add(luceAbitacolo);
  const ingresso = new THREE.PointLight(caldo, 14, 18, 1.8); ingresso.position.set(0, 4.2, 3.5); scene.add(ingresso);

  // ---- piazzale bagnato che riflette ----
  const rifP = new Reflector(new THREE.PlaneGeometry(140, 70), { textureWidth: MOBILE ? 512 : 1024, textureHeight: MOBILE ? 512 : 1024, color: 0x4d586a, clipBias: 0.003 });
  rifP.rotation.x = -Math.PI / 2; rifP.position.set(0, 0.003, 35); scene.add(rifP);
  add(new THREE.PlaneGeometry(140, 70).rotateX(-Math.PI / 2), m.pietra, 0, 0.01, 35, false, true);
  const lucine = [];
  for (let z = 8; z <= 40; z += 4) for (const sx of [-1, 1]) {                       // paletti luminosi lungo il viale
    add(new THREE.CylinderGeometry(0.07, 0.07, 0.75, 12), m.nero, sx * 6, 0.375, z);
    const l = add(new THREE.CylinderGeometry(0.075, 0.075, 0.12, 12), m.led.clone(), sx * 6, 0.6, z); l.material.transparent = true; lucine.push(l);
  }
  // alberi: cipressi e ulivi illuminati dal basso
  const alone = new THREE.MeshBasicMaterial({ map: T.alone, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0.55 });
  const ulivo = (x, z, s = 1) => {
    add(new THREE.CylinderGeometry(0.12 * s, 0.2 * s, 2.2 * s, 8), m.tronco, x, 1.1 * s, z, true);
    for (let k = 0; k < 6; k++) {
      const a = k * 1.05, r = (0.6 + (k % 3) * 0.25) * s;
      add(new THREE.IcosahedronGeometry(0.75 * s, 1), m.foglia, x + Math.cos(a) * r, (2.4 + (k % 2) * 0.5) * s, z + Math.sin(a) * r, true);
    }
    const al = new THREE.Mesh(new THREE.PlaneGeometry(3.2 * s, 4.2 * s), alone); al.position.set(x, 2.2 * s, z + 1.3); scene.add(al);   // luce calda dal basso, finta
  };
  for (const sx of [-1, 1]) {
    ulivo(sx * 13, 4.5); ulivo(sx * 27, 9, 1.2);
    for (const [x, z] of [[23.5, 2.5], [25.5, 5], [30, 3]]) add(new THREE.ConeGeometry(0.7, 7.5, 12).translate(0, 3.75, 0), m.cipresso, sx * x, 0, z, true);
    add(box(12, 0.7, 2.4), m.cipresso, sx * 14, 0.35, 1.8, false, true);          // siepi basse
  }

  // ---- edificio: cornice blu notte, vetrate a tutta altezza, ali in travertino ----
  const LARGO = 40, ALTO = 9.6, FONDO = 36;
  add(box(LARGO + 2, 1.3, 1.2), m.navy, 0, ALTO + 0.65, 0.2, true);
  for (const sx of [-1, 1]) add(box(1.1, ALTO + 1.3, 1.2), m.navy, sx * (LARGO / 2 + 0.55), (ALTO + 1.3) / 2, 0.2, true);
  for (const sx of [-1, 1]) add(box(14, 7.5, FONDO), m.travertino, sx * (LARGO / 2 + 8), 3.75, -FONDO / 2 + 0.6, true, true);
  for (let x = -LARGO / 2; x <= LARGO / 2 + 0.01; x += 2.5) {                       // montanti sottili
    if (Math.abs(x) < 3.1) continue;
    add(box(0.09, ALTO, 0.14), m.navy, x, ALTO / 2, 0, true);
  }
  add(box(LARGO, 0.12, 0.16), m.navy, 0, 4.7, 0); add(box(LARGO, 0.12, 0.16), m.navy, 0, 0.06, 0);
  for (const sx of [-1, 1]) add(new THREE.PlaneGeometry(LARGO / 2 - 3.1, ALTO), m.vetro, sx * (3.1 + (LARGO / 2 - 3.1) / 2), ALTO / 2, 0);
  add(new THREE.PlaneGeometry(6.2, ALTO - 4.6), m.vetro, 0, 4.6 + (ALTO - 4.6) / 2, 0);
  const porte = [-1, 1].map(sx => {                                                   // porte scorrevoli
    const g = new THREE.Group();
    const lastra = new THREE.Mesh(new THREE.PlaneGeometry(2.96, 4.5), m.vetro); lastra.position.y = 2.3; g.add(lastra);
    for (const [ww, hh, x, y] of [[3, 0.07, 0, 0.04], [3, 0.07, 0, 4.55], [0.07, 4.5, -1.47, 2.3], [0.07, 4.5, 1.47, 2.3]]) {
      const f = new THREE.Mesh(box(ww, hh, 0.06), m.navy); f.position.set(x, y, 0); g.add(f);
    }
    const man = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.018, 1.8, 12), m.nero); man.position.set(-sx * 1.25, 2.1, 0.06); g.add(man);
    g.position.set(sx * 1.5, 0, 0.05); scene.add(g); return g;
  });
  // pensilina con insegna e faretti
  add(box(15, 1.0, 6.5), m.navy, 0.8, 5.25, 3.0, true, true);
  add(new THREE.PlaneGeometry(13, 0.9), new THREE.MeshBasicMaterial({ map: T.insegna, transparent: true, toneMapped: false }), 0.8, 5.25, 6.26).renderOrder = 3;
  for (let x = -5; x <= 6.6; x += 2.2) for (const z of [1.5, 4.5]) add(new THREE.CircleGeometry(0.09, 16).rotateX(Math.PI / 2), m.led, x, 4.74, z);
  add(box(0.6, 4.75, 0.6), m.navy, -6.3, 2.37, 5.8, true);

  // ---- interno ----
  const rif = new Reflector(new THREE.PlaneGeometry(LARGO, FONDO), { textureWidth: MOBILE ? 512 : 1280, textureHeight: MOBILE ? 512 : 1280, color: 0x8d949c, clipBias: 0.003 });
  rif.rotation.x = -Math.PI / 2; rif.position.set(0, 0.004, -FONDO / 2); scene.add(rif);
  add(new THREE.PlaneGeometry(LARGO, FONDO).rotateX(-Math.PI / 2), m.marmo, 0, 0.012, -FONDO / 2, false, true);
  add(box(LARGO, 0.3, FONDO), m.bianco, 0, ALTO + 0.15, -FONDO / 2);                        // soffitto
  for (const x of [-9, -3, 3, 9]) add(box(0.06, 0.03, FONDO - 4), m.led, x, ALTO - 0.02, -FONDO / 2);   // linee di luce
  for (const [x1, z1, x2, z2] of [[-16, -4, -6, -16], [16, -6, 7, -18]]) {
    const len = Math.hypot(x2 - x1, z2 - z1), o = add(box(0.06, 0.03, len), m.led, (x1 + x2) / 2, ALTO - 0.02, (z1 + z2) / 2);
    o.rotation.y = Math.atan2(x2 - x1, z2 - z1);
  }
  for (let x = -16; x <= 16; x += 4) for (let z = -4; z >= -34; z -= 5) add(new THREE.CircleGeometry(0.06, 12).rotateX(Math.PI / 2), m.led, x + 1.2, ALTO - 0.01, z);
  // mezzanino sul fondo con parapetto in vetro
  add(box(LARGO, 0.6, 9), m.bianco, 0, 4.7, -FONDO + 4.5, true, true);
  add(box(LARGO, 0.04, 0.05), m.led, 0, 4.38, -FONDO + 9.02);
  add(new THREE.PlaneGeometry(LARGO, 1.1), m.vetro, 0, 5.55, -FONDO + 9.0);
  for (let x = -18; x <= 18; x += 4) add(box(0.4, 0.04, 0.4), m.led, x, 4.38, -FONDO + 6);
  // parete di fondo blu notte con insegna
  add(box(LARGO, ALTO, 0.4), m.navyOpaco, 0, ALTO / 2, -FONDO - 0.2, false, true);
  add(new THREE.PlaneGeometry(9, 0.62), new THREE.MeshBasicMaterial({ map: T.insegna, transparent: true, toneMapped: false }), 0, 2.6, -FONDO + 0.02);
  // colonne blu notte, lucide
  const col = box(1.0, ALTO, 1.0);
  for (const x of [-7.5, 7.5]) for (const z of [-5, -14.5, -24]) add(col, m.navy, x, ALTO / 2, z, true, true);
  for (const x of [-15, 15]) for (const z of [-7, -18]) add(col, m.navy, x, ALTO / 2, z, true, true);
  // banco accoglienza in marmo con luce sotto, fiori bianchi
  add(box(1.6, 1.1, 8), m.marmoPieno, -16, 0.6, -10, true, true);
  add(box(1.62, 0.03, 8.02), m.led, -16, 0.06, -10);
  for (let k = 0; k < 9; k++) add(new THREE.SphereGeometry(0.11 + (k % 3) * 0.03, 12, 8), m.fiori, -16 + (k % 3 - 1) * 0.15, 1.6 + (k % 4) * 0.12, -12.8 + (k % 2) * 0.15);
  add(new THREE.CylinderGeometry(0.12, 0.1, 0.45, 16), new THREE.MeshPhysicalMaterial({ color: 0xffffff, transmission: 0.8, roughness: 0.05, thickness: 0.2 }), -16, 1.38, -12.8);
  // salotto: divani crema, tavolino nero, lampada
  const divano = (x, z, ry) => {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = ry; scene.add(g);
    const s = new THREE.Mesh(new RoundedBoxGeometry(2.6, 0.42, 0.95, 4, 0.08), m.crema); s.position.y = 0.32; g.add(s);
    const b = new THREE.Mesh(new RoundedBoxGeometry(2.6, 0.5, 0.25, 4, 0.08), m.crema); b.position.set(0, 0.72, -0.36); g.add(b);
    for (const sx of [-1, 1]) { const a = new THREE.Mesh(new RoundedBoxGeometry(0.25, 0.55, 0.95, 4, 0.08), m.crema); a.position.set(sx * 1.32, 0.45, 0); g.add(a); }
    g.traverse(o => { if (o.isMesh) { o.castShadow = o.receiveShadow = true; } });
  };
  divano(14.5, -11, -Math.PI / 2); divano(11.2, -8.4, Math.PI); divano(11.2, -13.6, 0);
  add(box(1.6, 0.36, 1.6), m.nero, 12.2, 0.18, -11, true, true);
  add(new THREE.PlaneGeometry(6, 6.4).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial({ color: 0x8a8a86, roughness: 1 }), 12.6, 0.02, -11, false, true);
  add(new THREE.CylinderGeometry(0.02, 0.02, 1.6, 8), m.nero, 15.6, 0.8, -8.3);
  add(new THREE.CylinderGeometry(0.22, 0.22, 0.5, 24, 1, true), new THREE.MeshBasicMaterial({ color: new THREE.Color(0xffe6c4).multiplyScalar(1.6), side: THREE.DoubleSide }), 15.6, 1.75, -8.3);
  for (const [x, z, s] of [[17.5, -2.5, 0.8], [-17.5, -22, 0.8], [5.2, -30, 0.7]]) {
    ulivo(x, z, s); add(new THREE.CylinderGeometry(0.6, 0.5, 0.7, 24), m.nero, x, 0.35, z, true, true);
  }
  return { porte, luceAbitacolo, lucine, rifP, rif };
}

// ===================================================================================
function textures() {
  const tela = (w, h, fn, rep) => {
    const c = document.createElement('canvas'); c.width = w; c.height = h; fn(c.getContext('2d'), w, h);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
    if (rep) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(...rep); }
    return t;
  };
  const rnd = (() => { let s = 11; return () => (s = (s * 16807) % 2147483647) / 2147483647; })();
  const marmo = tela(1024, 1024, (g, w, h) => {
    g.fillStyle = '#eceae6'; g.fillRect(0, 0, w, h);
    for (let i = 0; i < 30000; i++) { const v = 205 + rnd() * 45; g.fillStyle = `rgba(${v},${v},${v + 3},${rnd() * 0.12})`; g.fillRect(rnd() * w, rnd() * h, 2, 2); }
    for (let k = 0; k < 22; k++) {
      let x = rnd() * w, y = rnd() * h, a = rnd() * Math.PI * 2;
      g.strokeStyle = `rgba(${95 + rnd() * 40},${100 + rnd() * 40},${110 + rnd() * 40},${0.12 + rnd() * 0.25})`; g.lineWidth = 0.6 + rnd() * 2.2;
      g.beginPath(); g.moveTo(x, y);
      for (let s = 0; s < 70; s++) { a += (rnd() - 0.5) * 0.55; x += Math.cos(a) * 12; y += Math.sin(a) * 12; g.lineTo(x, y); }
      g.stroke();
    }
    g.strokeStyle = 'rgba(140,140,140,.4)'; g.lineWidth = 2;
    for (let i = 0; i <= 2; i++) { g.beginPath(); g.moveTo(i * w / 2, 0); g.lineTo(i * w / 2, h); g.moveTo(0, i * h / 2); g.lineTo(w, i * h / 2); g.stroke(); }
  }, [10, 9]);
  const pietra = tela(512, 512, (g, w, h) => {
    g.fillStyle = '#b9bec4'; g.fillRect(0, 0, w, h);
    for (let i = 0; i < 24000; i++) { const v = 150 + rnd() * 70; g.fillStyle = `rgba(${v},${v + 2},${v + 5},.45)`; g.fillRect(rnd() * w, rnd() * h, 2, 2); }
    g.strokeStyle = 'rgba(90,95,100,.5)'; g.lineWidth = 2;
    for (let i = 0; i <= 4; i++) { g.beginPath(); g.moveTo(0, i * h / 4); g.lineTo(w, i * h / 4); g.stroke(); }
    for (let r = 0; r < 4; r++) for (let i = 0; i <= 2; i++) { const x = (i + (r % 2) * 0.5) * w / 2; g.beginPath(); g.moveTo(x, r * h / 4); g.lineTo(x, (r + 1) * h / 4); g.stroke(); }
  }, [36, 18]);
  const travertino = tela(256, 512, (g, w, h) => {
    g.fillStyle = '#dcd2c2'; g.fillRect(0, 0, w, h);
    for (let y = 0; y < h; y += 2) { g.fillStyle = `rgba(150,130,105,${rnd() * 0.08})`; g.fillRect(0, y, w, 1 + rnd() * 2); }
    g.strokeStyle = 'rgba(140,125,105,.35)'; for (let y = 0; y <= h; y += h / 4) { g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
  }, [4, 2]);
  const insegna = tela(2600, 180, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#f4efe6'; g.textAlign = 'center'; g.textBaseline = 'middle';
    const nome = ((CFG.nome || 'Salone') + ' SAS').toUpperCase().split('').join(String.fromCharCode(8202, 8202));
    g.font = '400 118px "Cormorant Garamond", Georgia, serif'; g.fillText(nome, w / 2, h / 2 + 6);
  });
  const alone = tela(128, 256, (g, w, h) => {
    const gr = g.createRadialGradient(w / 2, h, 4, w / 2, h * 0.75, h * 0.8);
    gr.addColorStop(0, 'rgba(255,214,160,.9)'); gr.addColorStop(0.4, 'rgba(255,214,160,.25)'); gr.addColorStop(1, 'rgba(255,214,160,0)');
    g.fillStyle = gr; g.fillRect(0, 0, w, h);
  });
  return { marmo, pietra, travertino, insegna, alone };
}
