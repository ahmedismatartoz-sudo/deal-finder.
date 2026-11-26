/* MC prototipo — scena 3D astratta guidata dallo scroll (three.js r160 da cdn.jsdelivr.net, versione esatta nella importmap).
   Nessuna auto: lastre di vetro, un nastro di metallo lucido, una luce che scorre e una linea che firma il monogramma MC.
   Tutto dipende da un solo valore p (0→1) dato dallo scroll: scorrendo avanza, risalendo torna indietro.
   Se il CDN non risponde questo modulo non si carica e la pagina usa il fallback 2D (SVG). */
import * as THREE from 'three';

const ss=(a,b,x)=>{const t=Math.min(1,Math.max(0,(x-a)/(b-a)));return t*t*(3-2*t)};
const L=(a,b,t)=>a+(b-a)*t;
const V3=(x,y,z)=>new THREE.Vector3(x,y,z);

/* ambiente chiaro da studio: riflessi morbidi su vetro e metallo */
function studioEnv(r){const sc=new THREE.Scene(),trash=[];const add=(geo,mat,f)=>{trash.push(geo,mat);const m=new THREE.Mesh(geo,mat);f(m);sc.add(m)};
  add(new THREE.BoxGeometry(30,16,30),new THREE.MeshBasicMaterial({color:0xd9d9de,side:THREE.BackSide}),()=>{});
  const panel=(w,h,x,y,z,rx,ry,k)=>add(new THREE.PlaneGeometry(w,h),new THREE.MeshBasicMaterial({color:new THREE.Color(k,k,k),side:THREE.DoubleSide}),m=>{m.position.set(x,y,z);m.rotation.set(rx,ry,0)});
  panel(20,1.2,0,7.5,-2,Math.PI/2,0,3);panel(20,1.2,0,7.5,2.5,Math.PI/2,0,2.4);panel(2,10,-14,2,0,0,Math.PI/2,2.2);panel(2,10,14,2,0,0,-Math.PI/2,1.6);panel(16,4,0,1,-14,0,0,1.4);
  panel(30,30,0,-7.9,0,-Math.PI/2,0,.55);
  const pm=new THREE.PMREMGenerator(r),t=pm.fromScene(sc,.04).texture;pm.dispose();trash.forEach(x=>x.dispose());return t}

/* lastra con angoli arrotondati */
function slabGeo(w,h,d,rad){const s=new THREE.Shape(),x=-w/2,y=-h/2;
  s.moveTo(x+rad,y);s.lineTo(x+w-rad,y);s.quadraticCurveTo(x+w,y,x+w,y+rad);s.lineTo(x+w,y+h-rad);s.quadraticCurveTo(x+w,y+h,x+w-rad,y+h);s.lineTo(x+rad,y+h);s.quadraticCurveTo(x,y+h,x,y+h-rad);s.lineTo(x,y+rad);s.quadraticCurveTo(x,y,x+rad,y);
  const g=new THREE.ExtrudeGeometry(s,{depth:d,bevelEnabled:true,bevelThickness:.02,bevelSize:.02,bevelSegments:4,curveSegments:16});g.translate(0,0,-d/2);g.computeVertexNormals();return g}

/* monogramma MC come due tratti continui (M a spigoli morbidi, C ad arco) */
function monogram(){const M=new THREE.CatmullRomCurve3([V3(-1.5,-.72,0),V3(-1.5,.72,0),V3(-.83,-.05,0),V3(-.16,.72,0),V3(-.16,-.72,0)],false,'catmullrom',.02);
  const pts=[];for(let i=0;i<=40;i++){const a=THREE.MathUtils.degToRad(48+i*(264/40));pts.push(V3(.92+Math.cos(a)*.74,Math.sin(a)*.74,0))}
  const C=new THREE.CatmullRomCurve3(pts);return [M,C]}

export async function createStage(canvas,o){
  o=o||{};const MOB=!!o.mobile;
  const r=new THREE.WebGLRenderer({canvas,antialias:true,alpha:true,powerPreference:'high-performance'});
  r.setPixelRatio(Math.min(window.devicePixelRatio||1,MOB?1.6:2));r.toneMapping=THREE.ACESFilmicToneMapping;r.toneMappingExposure=1.05;r.setClearColor(0x000000,0);
  const s=new THREE.Scene(),trash=[];const keep=x=>{trash.push(x);return x};
  const env=keep(studioEnv(r));s.environment=env;
  const cam=new THREE.PerspectiveCamera(32,1,.1,100);
  const accent=new THREE.Color(o.accent||'#0071e3');

  /* materiali */
  const glass=keep(new THREE.MeshPhysicalMaterial({color:0xffffff,metalness:0,roughness:.04,transmission:MOB?.0:1,thickness:.6,ior:1.45,transparent:MOB,opacity:MOB?.35:1,clearcoat:1,clearcoatRoughness:.02,envMapIntensity:1.3,iridescence:.35,iridescenceIOR:1.3}));
  const chrome=keep(new THREE.MeshPhysicalMaterial({color:0xe9e9ec,metalness:1,roughness:.07,clearcoat:1,clearcoatRoughness:.03,envMapIntensity:1.4}));
  const ink=keep(new THREE.MeshPhysicalMaterial({color:0x1d1d1f,metalness:.6,roughness:.22,clearcoat:1,clearcoatRoughness:.05,envMapIntensity:1}));
  const dot=keep(new THREE.MeshBasicMaterial({color:accent.clone()}));

  /* lastre di vetro: aperte a ventaglio all'inizio, si allineano in una cornice */
  const slabG=keep(slabGeo(1.9,2.6,.07,.18)),slabs=[];
  const START=[[-1.7,.5,-1.2,.5,-.7,.25],[1.9,-.3,-.6,-.35,.9,-.2],[.2,1.1,-2.4,.9,.2,.6]],END=[[-.02,0,-.55,0,0,0],[0,0,-.85,0,0,0],[.02,0,-1.15,0,0,0]];
  for(let i=0;i<3;i++){const m=new THREE.Mesh(slabG,glass);s.add(m);slabs.push(m)}
  /* nastro di metallo: un profilo sottile estruso lungo una curva morbida */
  const rib=new THREE.Shape();rib.moveTo(-.34,-.012);rib.lineTo(.34,-.012);rib.lineTo(.34,.012);rib.lineTo(-.34,.012);rib.lineTo(-.34,-.012);
  const path=new THREE.CatmullRomCurve3([V3(-4,-1.6,-1.5),V3(-2.2,-.2,.6),V3(-.4,-1.1,1.1),V3(1.2,.3,.2),V3(2.6,-.6,-.8),V3(4.2,.8,-1.6)]);
  const ribG=keep(new THREE.ExtrudeGeometry(rib,{steps:MOB?160:260,bevelEnabled:false,extrudePath:path}));
  const ribbon=new THREE.Mesh(ribG,chrome);s.add(ribbon);
  const ribCount=ribG.index?ribG.index.count:ribG.attributes.position.count;
  /* firma MC: tubo disegnato progressivamente */
  const [cM,cC]=monogram(),SEG=MOB?160:240,RAD=10;
  const gM=keep(new THREE.TubeGeometry(cM,SEG,.04,RAD,false)),gC=keep(new THREE.TubeGeometry(cC,SEG,.04,RAD,false));
  const sign=new THREE.Group();sign.position.z=.12;const mM=new THREE.Mesh(gM,ink),mC=new THREE.Mesh(gC,ink);sign.add(mM,mC);s.add(sign);
  const per=RAD*6,draw=(g,u)=>g.setDrawRange(0,Math.round(SEG*Math.min(1,Math.max(0,u)))*per);
  const tip=new THREE.Mesh(keep(new THREE.SphereGeometry(.055,24,16)),dot);sign.add(tip);
  /* luce che scorre sul metallo */
  s.add(new THREE.HemisphereLight(0xffffff,0xcfcfd4,.6));
  const sweep=new THREE.PointLight(0xffffff,40,12,1.6);s.add(sweep);
  const key=new THREE.DirectionalLight(0xffffff,1.2);key.position.set(2,4,5);s.add(key);

  function dim(){const w=Math.max(1,canvas.clientWidth),h=Math.max(1,canvas.clientHeight);r.setSize(w,h,false);cam.aspect=w/h;cam.updateProjectionMatrix()}
  dim();const ro='ResizeObserver' in window?new ResizeObserver(dim):null;ro&&ro.observe(canvas);addEventListener('resize',dim);

  let target=0,p=0,t=0,last=0,raf=0,running=false,visible=true,dead=false;const P=V3(),T=V3();
  function frame(now){if(!running)return;raf=requestAnimationFrame(frame);const dt=Math.min(.05,(now-last)/1000||0);last=now;t+=dt;
    p+=(target-p)*Math.min(1,dt*7);if(Math.abs(target-p)<1e-4)p=target;
    const a=ss(.08,.55,p),port=cam.aspect<.85;
    slabs.forEach((m,i)=>{const S=START[i],E=END[i],k=ss(.1+i*.05,.55+i*.05,p),fl=(1-k)*Math.sin(t*.6+i*2)*.06;
      m.position.set(L(S[0],E[0],k),L(S[1],E[1],k)+fl,L(S[2],E[2],k));m.rotation.set(L(S[3],E[3],k),L(S[4],E[4],k)+(1-k)*t*.05,L(S[5],E[5],k))});
    ribbon.rotation.set(L(.5,-.15,a),L(-.7,.4,p),L(.2,-.1,a));ribbon.position.set(0,L(-.2,-1.55,ss(.45,.9,p)),L(.4,-.4,p));
    ribG.setDrawRange(0,Math.round(ribCount*L(.35,1,ss(0,.35,p))/3)*3);
    const u1=ss(.38,.62,p),u2=ss(.6,.84,p);draw(gM,u1);draw(gC,u2);
    const cur=u2>0?cC.getPoint(u2):cM.getPoint(u1);tip.position.copy(cur);tip.visible=u1>0.002&&u2<.998;
    sign.scale.setScalar(port?.78:1);sign.rotation.y=L(-.25,0,ss(.35,.9,p));
    sweep.position.set(L(-5,5,ss(0,1,p))+Math.sin(t*.4)*.4,1.8,2.2);
    const orbit=L(-.55,.12,ss(0,.85,p)),dist=L(9.5,7.2,ss(.2,.95,p))*(port?1.45:1);
    P.set(Math.sin(orbit)*dist,L(1.2,.15,ss(0,.8,p)),Math.cos(orbit)*dist);T.set(0,L(.3,0,p),0);cam.position.copy(P);cam.lookAt(T);
    if(!port&&o.offset){const w=canvas.clientWidth,h=canvas.clientHeight;cam.setViewOffset(w,h,-w*.16*(1-ss(.3,.6,p)),0,w,h)}else cam.clearViewOffset();
    r.render(s,cam)}
  function start(){if(dead||running||!visible||document.hidden)return;running=true;last=performance.now();raf=requestAnimationFrame(frame)}
  function stop(){running=false;cancelAnimationFrame(raf)}
  const io='IntersectionObserver' in window?new IntersectionObserver(es=>{visible=es[0].isIntersecting;visible?start():stop()},{threshold:0}):null;io&&io.observe(canvas);
  const onVis=()=>{document.hidden?stop():start()};document.addEventListener('visibilitychange',onVis);
  start();
  return {gl:r.getContext(),
    setProgress(v,jump){target=Math.min(1,Math.max(0,v));if(jump)p=target},progress:()=>p,
    setAccent(h){accent.set(h);dot.color.copy(accent)},
    dispose(){if(dead)return;dead=true;stop();io&&io.disconnect();ro&&ro.disconnect();removeEventListener('resize',dim);document.removeEventListener('visibilitychange',onVis);
      try{trash.forEach(x=>{try{x.dispose()}catch(e){}});r.renderLists.dispose();r.dispose();r.forceContextLoss()}catch(e){}}};
}
