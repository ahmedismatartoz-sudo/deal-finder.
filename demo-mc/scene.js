/* MC prototipo — scena 3D (three.js r160). Caricata solo se WebGL c'è e l'utente non chiede meno movimento.
   Se il CDN non risponde questo modulo non si carica e la pagina resta sul fallback statico. */
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {DRACOLoader} from 'three/addons/loaders/DRACOLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {Reflector} from 'three/addons/objects/Reflector.js';

const BASE='https://cdn.jsdelivr.net/gh/mrdoob/three.js@r160/examples/';
const DRACO='https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/libs/draco/gltf/';
let modelP=null;const progL=new Set();
function loadModel(onProg){
  if(onProg)progL.add(onProg);
  if(!modelP){
    modelP=new Promise((res,rej)=>{
      const dl=new DRACOLoader();dl.setDecoderPath(DRACO);
      const ld=new GLTFLoader();ld.setDRACOLoader(dl);
      ld.load(BASE+'models/gltf/ferrari.glb',g=>{progL.forEach(f=>f(1));res(g.scene.children[0])},
        e=>{const p=e&&e.total?e.loaded/e.total:.5;progL.forEach(f=>f(Math.min(.95,p)))},rej);
    });
    modelP.catch(()=>{modelP=null});
  }
  return modelP;
}
const ss=(a,b,x)=>{const t=Math.min(1,Math.max(0,(x-a)/(b-a)));return t*t*(3-2*t)};
const L=(a,b,t)=>a+(b-a)*t;
const out3=t=>1-Math.pow(1-Math.min(1,Math.max(0,t)),3);

export async function createStage(canvas,o){
  o=o||{};const hero=o.mode!=='viewer',MOB=!!o.mobile;
  const r=new THREE.WebGLRenderer({canvas,antialias:!MOB,alpha:!hero,powerPreference:'high-performance'});
  r.setPixelRatio(Math.min(window.devicePixelRatio||1,MOB?1.25:2));
  r.toneMapping=THREE.ACESFilmicToneMapping;r.toneMappingExposure=hero?1.02:1.05;
  const s=new THREE.Scene();
  const BG=0xf1f1ef; // studio chiaro
  if(hero){s.background=new THREE.Color(BG);s.fog=new THREE.Fog(BG,16,48)}
  const pm=new THREE.PMREMGenerator(r);s.environment=pm.fromScene(new RoomEnvironment(),.04).texture;
  const cam=new THREE.PerspectiveCamera(hero?(MOB?52:32):30,1,.1,200);
  const accent=new THREE.Color(o.accent||'#e1061b');
  const accMats=[];let comp=null,bloom=null;

  /* ---------- studio ---------- */
  const strips=[];let rim=null,wall=null;
  if(hero){
    if(!MOB){const rf=new Reflector(new THREE.PlaneGeometry(80,80),{textureWidth:Math.round(innerWidth*.6),textureHeight:Math.round(innerHeight*.6),color:0xb8b8b6});rf.rotation.x=-Math.PI/2;s.add(rf)}
    const floor=new THREE.Mesh(new THREE.PlaneGeometry(80,80),new THREE.MeshStandardMaterial({color:0xf3f3f1,roughness:.55,metalness:0,transparent:!MOB,opacity:MOB?1:.8}));
    floor.rotation.x=-Math.PI/2;floor.position.y=.002;s.add(floor);
    // strisce di luce a soffitto: si accendono una dopo l'altra
    for(let i=0;i<7;i++){const m=new THREE.MeshBasicMaterial({color:0xd8d8d5});const b=new THREE.Mesh(new THREE.BoxGeometry(15,.04,.2),m);b.position.set(0,6.4,7-i*2.6);s.add(b);strips.push(m)}
    // parete di fondo con il marchio MC disegnato su canvas (nessun logo di terzi)
    const tex=()=>{const c=document.createElement('canvas');c.width=2048;c.height=768;const x=c.getContext('2d');
      x.font='700 640px "Barlow Condensed","Arial Narrow",sans-serif';x.textAlign='center';x.textBaseline='middle';x.fillStyle='#d6d6d2';x.fillText('MC',960,400);
      x.save();x.translate(1560,400);x.transform(1,0,-.25,1,0,0);x.fillStyle='#'+accent.getHexString();x.fillRect(-45,-230,90,460);x.restore();
      const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=4;return t};
    const back=new THREE.Mesh(new THREE.PlaneGeometry(44,10),new THREE.MeshBasicMaterial({color:BG,fog:false,toneMapped:false}));back.position.set(0,5,-10);s.add(back);
    wall=new THREE.Mesh(new THREE.PlaneGeometry(13,4.875),new THREE.MeshBasicMaterial({map:tex(),transparent:true,opacity:.9,depthWrite:false,fog:false,toneMapped:false}));wall.position.set(0,3.5,-9.95);s.add(wall);
    wall.userData.redraw=()=>{wall.material.map.dispose();wall.material.map=tex();wall.material.needsUpdate=true};
    if(document.fonts&&document.fonts.ready)document.fonts.ready.then(()=>wall&&wall.userData.redraw()).catch(()=>{});
    const neonM=new THREE.MeshBasicMaterial({color:accent.clone()});accMats.push(neonM);
    const neon=new THREE.Mesh(new THREE.BoxGeometry(16,.035,.035),neonM);neon.position.set(0,.9,-9.9);s.add(neon);
    const ring=new THREE.Mesh(new THREE.TorusGeometry(3.7,.018,10,160),neonM);ring.rotation.x=Math.PI/2;ring.position.y=.012;s.add(ring);
    for(const sx of[-1,1])for(let i=0;i<4;i++){const c=new THREE.Mesh(new THREE.BoxGeometry(.4,7,.4),new THREE.MeshStandardMaterial({color:0xe4e4e1,roughness:.7,metalness:0}));c.position.set(sx*10.5,3.5,-7+i*5);s.add(c);
      const l=new THREE.Mesh(new THREE.BoxGeometry(.03,6,.03),new THREE.MeshBasicMaterial({color:0xffffff}));l.position.set(sx*(10.5-.22),3.5,-7+i*5);s.add(l)}
    s.add(new THREE.HemisphereLight(0xffffff,0xd8d8d4,1.15));
    const key=new THREE.SpotLight(0xffffff,150,30,.6,.7);key.position.set(0,9,3);key.target.position.set(0,0,0);s.add(key,key.target);
    rim=new THREE.PointLight(accent.clone(),6,10);s.add(rim);
    if(!MOB){const N=900,g=new THREE.BufferGeometry(),p=new Float32Array(N*3);for(let i=0;i<N;i++)p.set([(Math.random()-.5)*22,Math.random()*6,(Math.random()-.5)*20],i*3);
      g.setAttribute('position',new THREE.BufferAttribute(p,3));const dust=new THREE.Points(g,new THREE.PointsMaterial({color:0xb9b9b5,size:.016,transparent:true,opacity:.22,depthWrite:false}));dust.name='dust';s.add(dust)}
  }else{
    const disc=new THREE.Mesh(new THREE.CircleGeometry(4.2,96),new THREE.MeshStandardMaterial({color:0xe9e9e6,roughness:.6,metalness:0}));disc.rotation.x=-Math.PI/2;s.add(disc);
    const ringM=new THREE.MeshBasicMaterial({color:accent.clone()});accMats.push(ringM);
    const ring=new THREE.Mesh(new THREE.TorusGeometry(4.2,.02,10,160),ringM);ring.rotation.x=Math.PI/2;ring.position.y=.01;s.add(ring);
    s.add(new THREE.HemisphereLight(0xffffff,0xd8d8d4,1.1));
    const key=new THREE.SpotLight(0xffffff,110,30,.6,.6);key.position.set(2,8,4);s.add(key,key.target);
  }

  /* ---------- auto ---------- */
  const car=new THREE.Group();s.add(car);const wheels=[];
  const paint=new THREE.MeshPhysicalMaterial({color:0xc8102e,metalness:1,roughness:.5,clearcoat:1,clearcoatRoughness:.03});
  const detail=new THREE.MeshStandardMaterial({color:0xffffff,metalness:1,roughness:.4});
  const glass=MOB?new THREE.MeshStandardMaterial({color:0x0c0f14,metalness:.6,roughness:.05,transparent:true,opacity:.55})
                 :new THREE.MeshPhysicalMaterial({color:0xffffff,metalness:.25,roughness:0,transmission:1});
  function setPaint(hex){const c=new THREE.Color(hex);paint.color.copy(c);const hsl={};c.getHSL(hsl);
    const flat=hsl.s<.08&&hsl.l>.3&&hsl.l<.75; // grigi pastello tipo Nardo: vernice non metallizzata
    paint.metalness=flat?.15:hsl.l<.12?.85:1;paint.roughness=flat?.42:hsl.l<.12?.38:.5}
  setPaint(o.paint||'#c8102e');
  function setAccent(hex){accent.set(hex);accMats.forEach(m=>m.color.copy(accent));if(rim)rim.color.copy(accent);if(wall)wall.userData.redraw()}

  const lampL=new THREE.SpotLight(0xf4f8ff,0,22,.35,.5),lampR=lampL.clone();
  if(hero){lampL.position.set(.62,.7,2.3);lampR.position.set(-.62,.7,2.3);[lampL,lampR].forEach(f=>{f.target.position.set(f.position.x,0,12);car.add(f,f.target)})}

  let src;
  try{src=await loadModel(o.onProgress)}
  catch(e){ // modello non scaricato: libera il contesto WebGL, altrimenti ogni tentativo fallito ne lascia uno aperto
    progL.delete(o.onProgress);try{pm.dispose();r.dispose();r.forceContextLoss()}catch(_){}
    throw e}
  const m=src.clone();
  const set=(n,mat)=>{const x=m.getObjectByName(n);if(x)x.material=mat};
  set('body',paint);['rim_fl','rim_fr','rim_rr','rim_rl','trim'].forEach(n=>set(n,detail));set('glass',glass);
  ['wheel_fl','wheel_fr','wheel_rl','wheel_rr'].forEach(n=>{const w=m.getObjectByName(n);if(w)wheels.push(w)});
  new THREE.TextureLoader().load(BASE+'models/gltf/ferrari_ao.png',t=>{
    const sh=new THREE.Mesh(new THREE.PlaneGeometry(.655*4,1.3*4),new THREE.MeshBasicMaterial({map:t,blending:THREE.MultiplyBlending,toneMapped:false,transparent:true}));
    sh.rotation.x=-Math.PI/2;sh.renderOrder=2;m.add(sh)},undefined,()=>{});
  m.scale.setScalar(1.15);m.position.y=.01;car.add(m);

  /* ---------- post (solo desktop, solo hero) ---------- */
  /* bloom tolto: sullo studio bianco bruciava i chiari. Si disegna diretto, con antialias nativo. */
  function dim(){const w=Math.max(1,canvas.clientWidth),h=Math.max(1,canvas.clientHeight);r.setSize(w,h,false);if(comp)comp.setSize(w,h);cam.aspect=w/h;cam.updateProjectionMatrix()}
  dim();
  const ro='ResizeObserver' in window?new ResizeObserver(dim):null;ro&&ro.observe(canvas);
  addEventListener('resize',dim);

  /* ---------- regia ---------- */
  let q=0,qv=0,mx=0,my=0,t=0,last=0,raf=0,running=false,visible=true,dead=false;
  let ang=.7,drag=false,px=0,idle=0;
  const onMove=e=>{mx=e.clientX/innerWidth-.5;my=e.clientY/innerHeight-.5};
  if(hero&&!MOB)addEventListener('pointermove',onMove,{passive:true});
  if(!hero){
    canvas.addEventListener('pointerdown',e=>{drag=true;px=e.clientX;idle=0;canvas.style.cursor='grabbing'});
    addEventListener('pointerup',()=>{drag=false;canvas.style.cursor='grab'});
    canvas.addEventListener('pointermove',e=>{if(!drag)return;ang-=(e.clientX-px)*.008;px=e.clientX;idle=0});
  }
  const C=new THREE.Vector3(),T=new THREE.Vector3();
  function frame(now){
    if(!running)return;raf=requestAnimationFrame(frame);
    const dt=Math.min(.05,(now-last)/1000||0);last=now;t+=dt;
    if(hero){
      qv+=(q-qv)*.08;const k=out3((t-.25)/3.4),portrait=cam.aspect<.8;
      strips.forEach((mm,i)=>{const on=ss(.15+i*.22,.5+i*.22,t),fl=on<1&&on>0?(.6+.4*Math.sin(t*60+i)):1;mm.color.setScalar(.84+.16*on*fl)});
      // l'auto entra dal fondo dello studio, fari accesi, e si ferma al centro
      const z=L(-7,0,k);car.position.z=z;wheels.forEach(w=>w.rotation.x=-(z/.38)-qv*2);
      car.rotation.y=L(0,-.35,ss(.2,1,k));
      lampL.intensity=lampR.intensity=L(40,6,ss(.7,1,k));
      const a=.42+qv*Math.PI*1.8+mx*.22+Math.sin(t*.18)*.05;
      const wide=!portrait&&!MOB,rad=(L(11.5,wide?9.6:7.4,k)-Math.sin(qv*Math.PI)*(wide?2.4:.9)+ss(.72,1,qv)*2.6)*(portrait?1.42:1);
      C.set(Math.sin(a)*rad,L(.75,1.2,k)+ss(.62,1,qv)*4.4-my*.35,Math.cos(a)*rad);
      T.set(0,portrait?-.55:.28,0);cam.position.copy(C);cam.lookAt(T);
      // su schermi larghi l'auto sta a destra del titolo; scorrendo torna al centro
      if(!portrait&&!MOB){const w=canvas.clientWidth,h=canvas.clientHeight,off=-w*.12*(1-ss(.05,.35,qv));cam.setViewOffset(w,h,off,0,w,h)}
      if(rim)rim.position.set(Math.cos(t*.6)*5,1.3,Math.sin(t*.6)*5);
      const d=s.getObjectByName('dust');if(d)d.rotation.y=t*.02;
      if(comp)comp.render();else r.render(s,cam);
    }else{
      idle+=dt;if(!drag&&idle>1.2)ang+=dt*.25;
      const rad=cam.aspect<1?9.6:7.6;C.set(Math.sin(ang)*rad,1.7,Math.cos(ang)*rad);cam.position.copy(C);T.set(0,.5,0);cam.lookAt(T);
      r.render(s,cam);
    }
  }
  function start(){if(dead||running||!visible||document.hidden)return;running=true;last=performance.now();raf=requestAnimationFrame(frame)}
  function stop(){running=false;cancelAnimationFrame(raf)}
  const io='IntersectionObserver' in window?new IntersectionObserver(es=>{visible=es[0].isIntersecting;visible?start():stop()},{threshold:.01}):null;
  io&&io.observe(canvas);
  const onVis=()=>{document.hidden?stop():start()};document.addEventListener('visibilitychange',onVis);
  start();

  return {
    setPaint,setAccent,
    setScroll(v){q=Math.min(1,Math.max(0,v))},
    isRunning:()=>running,
    /* istantanee del modello per la galleria */
    snapshots(n){const outp=[],bg=s.background,wasRun=running;s.background=new THREE.Color(0xf1f1ef);
      try{for(let i=0;i<n;i++){const a=.7+i*(Math.PI*2/n),rad=cam.aspect<1?9.6:7.2;cam.position.set(Math.sin(a)*rad,i===1?3.4:1.5,Math.cos(a)*rad);cam.lookAt(0,.5,0);r.render(s,cam);outp.push(canvas.toDataURL('image/jpeg',.82))}}catch(e){}
      s.background=bg;if(wasRun)r.render(s,cam);return outp},
    dispose(){dead=true;stop();io&&io.disconnect();ro&&ro.disconnect();removeEventListener('resize',dim);removeEventListener('pointermove',onMove);document.removeEventListener('visibilitychange',onVis);progL.delete(o.onProgress);
      try{pm.dispose();r.dispose();r.forceContextLoss()}catch(e){}}
  };
}
