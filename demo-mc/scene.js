/* MC prototipo — scena 3D procedurale (three.js r160 da cdn.jsdelivr.net, versione esatta nella importmap).
   L'auto è GENERICA e costruita nel browser da profili estrusi: nessun modello scaricato, nessun logo, nessun
   design di case automobilistiche reali. Due modalità:
   - 'intro'  : regia cinematografica di ~12 s con studio, pavimento riflettente, fasci di luce, particelle e bloom;
   - 'viewer' : visore 360° leggero per la scheda auto (niente post-processing).
   Se il CDN non risponde questo modulo non si carica e la pagina usa il fallback 2D. */
import * as THREE from 'three';
import {Reflector} from 'three/addons/objects/Reflector.js';
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {UnrealBloomPass} from 'three/addons/postprocessing/UnrealBloomPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';

const ss=(a,b,x)=>{const t=Math.min(1,Math.max(0,(x-a)/(b-a)));return t*t*(3-2*t)};
const L=(a,b,t)=>a+(b-a)*t;
const V2=(x,y)=>new THREE.Vector2(x,y),V3=(x,y,z)=>new THREE.Vector3(x,y,z);

/* ---------- carrozzerie generiche: proporzioni per tipo, non per modello ---------- */
const BODIES={
 supercar:{L:4.62,W:1.98,wb:2.66,R:.35,tw:.31,ride:.15,nose:.30,tailLow:.56,cw:.72,top:[[2.30,.47],[1.85,.62],[1.25,.70],[.9,.77],[-.3,.85],[-1.5,.91],[-2.28,.95]],cab:[[1.02,.74],[.45,1.0],[-.05,1.15],[-.70,1.14],[-1.35,1.0],[-2.0,.90]]},
 coupe:{L:4.80,W:1.90,wb:2.86,R:.355,tw:.28,ride:.15,nose:.36,tailLow:.60,cw:.78,top:[[2.39,.60],[1.9,.76],[1.0,.88],[-.6,.95],[-1.9,1.01],[-2.39,1.04]],cab:[[1.04,.86],[.25,1.24],[-.25,1.35],[-.9,1.33],[-1.55,1.13],[-1.98,1.0]]},
 suv:{L:5.0,W:2.0,wb:3.0,R:.41,tw:.32,ride:.24,nose:.48,tailLow:.70,cw:.80,top:[[2.49,.84],[2.0,1.0],[1.15,1.10],[-.6,1.16],[-2.0,1.20],[-2.48,1.22]],cab:[[1.18,1.08],[.45,1.52],[-.1,1.67],[-1.0,1.65],[-1.85,1.48],[-2.36,1.24]]},
 suvs:{L:4.5,W:1.88,wb:2.68,R:.385,tw:.30,ride:.23,nose:.46,tailLow:.68,cw:.80,top:[[2.24,.80],[1.8,.96],[1.0,1.06],[-.5,1.12],[-1.8,1.16],[-2.24,1.18]],cab:[[1.02,1.04],[.35,1.46],[-.1,1.59],[-.85,1.57],[-1.6,1.40],[-2.12,1.20]]},
 wagon:{L:5.0,W:1.95,wb:2.93,R:.37,tw:.29,ride:.16,nose:.38,tailLow:.62,cw:.79,top:[[2.49,.64],[2.0,.80],[1.15,.92],[-.6,.98],[-2.0,1.02],[-2.48,1.04]],cab:[[1.17,.90],[.45,1.28],[-.05,1.41],[-1.2,1.42],[-2.2,1.39],[-2.46,1.16]]},
 hatch:{L:4.4,W:1.85,wb:2.63,R:.35,tw:.27,ride:.15,nose:.36,tailLow:.60,cw:.80,top:[[2.19,.62],[1.75,.78],[1.0,.88],[-.5,.94],[-1.7,1.0],[-2.19,1.02]],cab:[[1.02,.86],[.3,1.25],[-.15,1.39],[-1.05,1.39],[-1.85,1.29],[-2.12,1.08]]}
};

/* normali morbide su geometria non indicizzata (ExtrudeGeometry). Le due facce laterali (gruppo 0) hanno pochi
   triangoli lunghi: le loro normali si calcolano dalla superficie deformata (differenze finite), così niente striature. */
function deform(g,f){const p=g.attributes.position,n=p.count,orig=p.array.slice(0);
  for(let i=0;i<n;i++){const r=f(p.getX(i),p.getY(i),p.getZ(i));p.setXYZ(i,r[0],r[1],r[2])}p.needsUpdate=true;
  const lid=new Uint8Array(n);(g.groups||[]).forEach(gr=>{if(gr.materialIndex===0)for(let i=gr.start;i<gr.start+gr.count;i++)lid[i]=1});
  const map=new Map(),acc=[],ids=new Int32Array(n);
  for(let i=0;i<n;i++){const k=Math.round(p.getX(i)*2e3)+'|'+Math.round(p.getY(i)*2e3)+'|'+Math.round(p.getZ(i)*2e3);let id=map.get(k);if(id===undefined){id=acc.length/3;map.set(k,id);acc.push(0,0,0)}ids[i]=id}
  const a=V3(),b=V3(),c=V3(),cb=V3(),ab=V3();
  for(let i=0;i<n;i+=3){if(lid[i])continue;a.fromBufferAttribute(p,i);b.fromBufferAttribute(p,i+1);c.fromBufferAttribute(p,i+2);cb.subVectors(c,b);ab.subVectors(a,b);cb.cross(ab);
    for(let j=0;j<3;j++){const q=ids[i+j]*3;acc[q]+=cb.x;acc[q+1]+=cb.y;acc[q+2]+=cb.z}}
  const nr=new Float32Array(n*3),e=.004,A=V3(),B=V3(),C=V3(),D=V3();
  for(let i=0;i<n;i++){let x,y,z;
    if(lid[i]){const ox=orig[i*3],oy=orig[i*3+1],oz=orig[i*3+2];A.fromArray(f(ox+e,oy,oz));B.fromArray(f(ox-e,oy,oz));C.fromArray(f(ox,oy+e,oz));D.fromArray(f(ox,oy-e,oz));
      A.sub(B);C.sub(D);A.cross(C);if(Math.sign(A.z)!==Math.sign(oz))A.negate();x=A.x;y=A.y;z=A.z}
    else{const q=ids[i]*3;x=acc[q];y=acc[q+1];z=acc[q+2]}
    const l=Math.hypot(x,y,z)||1;nr[i*3]=x/l;nr[i*3+1]=y/l;nr[i*3+2]=z/l}
  g.setAttribute('normal',new THREE.BufferAttribute(nr,3));return g}

function bodyGeometry(B){const hl=B.L/2,fx=B.wb/2,ar=B.R+.075,y0=B.ride,cy=B.R,a=Math.asin(Math.max(-.99,(y0-cy)/ar)),cx=ar*Math.cos(a),top=B.top;
  const s=new THREE.Shape();s.moveTo(hl-.14,y0);
  s.quadraticCurveTo(hl+.03,y0+.02,hl+.03,B.nose);s.quadraticCurveTo(hl+.03,top[0][1]-.03,top[0][0],top[0][1]);
  s.splineThru(top.slice(1).map(p=>V2(p[0],p[1])));
  const tl=top[top.length-1];s.quadraticCurveTo(-hl-.04,tl[1]-.04,-hl-.04,B.tailLow);s.quadraticCurveTo(-hl-.03,y0+.02,-hl+.16,y0);
  s.lineTo(-fx-cx,y0);s.absarc(-fx,cy,ar,Math.PI-a,a,true);s.lineTo(fx-cx,y0);s.absarc(fx,cy,ar,Math.PI-a,a,true);s.lineTo(hl-.14,y0);
  const g=new THREE.ExtrudeGeometry(s,{depth:B.W,steps:12,bevelEnabled:true,bevelThickness:.085,bevelSize:.05,bevelSegments:5,curveSegments:36});
  g.translate(0,0,-B.W/2);const hw=B.W/2,topY=Math.max(...top.map(p=>p[1]));
  return deform(g,(x,y,z)=>{const zn=Math.min(1.1,Math.abs(z)/hw);
    const fr=x>0?ss(hl-.8,hl+.06,x):ss(hl-.65,hl+.06,-x);
    let k=1-(x>0?.24:.14)*Math.pow(fr,1.6);
    k*=1-.10*ss(topY-.42,topY+.04,y);
    k*=1-.04*Math.exp(-Math.pow((x+.15)/(B.wb*.3),2))*Math.exp(-Math.pow((y-(y0+B.R*.95))/.13,2));
    k*=1+.035*Math.exp(-Math.pow((x+fx)/.62,2))*ss(B.R,B.R+.35,y)*(1-ss(topY-.12,topY,y));
    let nx=x;if(x>0)nx-=.20*zn*zn*ss(hl-.7,hl+.06,x);else nx+=.11*zn*zn*ss(hl-.6,hl+.06,-x);
    let ny=y;if(y>y0+.22)ny+=.05*(1-Math.min(1,zn*zn))*ss(y0+.22,topY-.08,y);
    return [nx,ny,z*k]})}
function cabinGeometry(B){const c=B.cab,s=new THREE.Shape(),base=Math.min(c[0][1],c[c.length-1][1])-.12;
  s.moveTo(c[0][0],c[0][1]);s.splineThru(c.slice(1).map(p=>V2(p[0],p[1])));s.lineTo(c[c.length-1][0]+.05,base);s.lineTo(c[0][0]-.06,base);s.lineTo(c[0][0],c[0][1]);
  const W=B.W*B.cw,g=new THREE.ExtrudeGeometry(s,{depth:W,steps:8,bevelEnabled:true,bevelThickness:.07,bevelSize:.05,bevelSegments:4,curveSegments:30});
  g.translate(0,0,-W/2);const hw=W/2,roof=Math.max(...c.map(p=>p[1])),xf=c[0][0],xr=c[c.length-1][0];
  return deform(g,(x,y,z)=>{const zn=Math.min(1.1,Math.abs(z)/hw);let k=1-.27*ss(c[0][1]-.02,roof+.04,y);
    k*=1-.10*ss(xf-.5,xf+.1,x)-.06*ss(-xr-.6,-xr+.1,-x);
    const ny=y+.035*(1-Math.min(1,zn*zn))*ss(roof-.3,roof,y);return [x,ny,z*k]})}

/* ---------- texture generate al volo ---------- */
function canvasTex(w,h,draw){const c=document.createElement('canvas');c.width=w;c.height=h;draw(c.getContext('2d'),w,h);const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;return t}
const shadowTex=()=>canvasTex(256,256,(x,w,h)=>{const g=x.createRadialGradient(w/2,h/2,0,w/2,h/2,w/2);g.addColorStop(0,'rgba(0,0,0,.95)');g.addColorStop(.45,'rgba(0,0,0,.6)');g.addColorStop(1,'rgba(0,0,0,0)');x.fillStyle=g;x.fillRect(0,0,w,h)});
const dotTex=()=>canvasTex(64,64,(x,w)=>{const g=x.createRadialGradient(32,32,0,32,32,32);g.addColorStop(0,'rgba(255,255,255,1)');g.addColorStop(.35,'rgba(255,255,255,.35)');g.addColorStop(1,'rgba(255,255,255,0)');x.fillStyle=g;x.fillRect(0,0,64,64)});

/* ---------- ambiente da studio: scatola scura con pannelli luminosi (riflessi sulla vernice) ---------- */
function studioEnv(r,light){const sc=new THREE.Scene(),geos=[],mats=[];const add=(geo,mat,f)=>{geos.push(geo);mats.push(mat);const m=new THREE.Mesh(geo,mat);f(m);sc.add(m)};
  add(new THREE.BoxGeometry(40,16,40),new THREE.MeshBasicMaterial({color:light?0x8d8d8a:0x030304,side:THREE.BackSide}),m=>m.position.y=6);
  const panel=(w,h,x,y,z,rx,ry,k)=>add(new THREE.PlaneGeometry(w,h),new THREE.MeshBasicMaterial({color:new THREE.Color(k,k,k),side:THREE.DoubleSide}),m=>{m.position.set(x,y,z);m.rotation.set(rx,ry,0)});
  for(let i=-2;i<=2;i++)panel(18,.7,0,7.5,i*2.4,Math.PI/2,0,i===0?6:3.5);
  panel(14,2.2,0,2.6,-15,0,0,2);panel(14,2.2,0,2.6,15,0,Math.PI,1.4);panel(2.2,9,15,3,0,0,-Math.PI/2,2.2);panel(2.2,9,-15,3,0,0,Math.PI/2,1.6);
  panel(30,30,0,-1.5,0,-Math.PI/2,0,light?.6:.02);
  const pm=new THREE.PMREMGenerator(r),tex=pm.fromScene(sc,.03).texture;pm.dispose();geos.forEach(g=>g.dispose());mats.forEach(m=>m.dispose());return tex}

/* ---------- auto ---------- */
function buildCar(o){const B=BODIES[o.body]||BODIES.supercar,hl=B.L/2,fx=B.wb/2,hw=B.W/2,car=new THREE.Group(),disp=[];
  const keep=x=>{disp.push(x);return x};
  const paint=keep(new THREE.MeshPhysicalMaterial({color:new THREE.Color(o.paint||'#c8102e'),metalness:.6,roughness:.26,clearcoat:1,clearcoatRoughness:.03,envMapIntensity:1.25}));
  const glass=keep(new THREE.MeshPhysicalMaterial({color:0x06070a,metalness:.92,roughness:.05,clearcoat:1,clearcoatRoughness:.02,envMapIntensity:1.5}));
  const trim=keep(new THREE.MeshStandardMaterial({color:0x0b0b0d,roughness:.5,metalness:.25,envMapIntensity:.8}));
  const tireM=keep(new THREE.MeshStandardMaterial({color:0x111113,roughness:.88,metalness:0}));
  const rimM=keep(new THREE.MeshStandardMaterial({color:0xd8dbe0,metalness:1,roughness:.22,envMapIntensity:1.2}));
  const rimDark=keep(new THREE.MeshStandardMaterial({color:0x2a2b2f,metalness:.8,roughness:.45}));
  const discM=keep(new THREE.MeshStandardMaterial({color:0x8d9095,metalness:1,roughness:.38}));
  const calM=keep(new THREE.MeshStandardMaterial({color:new THREE.Color(o.cal||o.accent||'#e1061b'),metalness:.2,roughness:.32}));
  const headM=keep(new THREE.MeshBasicMaterial({color:new THREE.Color(1,1,1)}));
  const tailM=keep(new THREE.MeshBasicMaterial({color:new THREE.Color(1,.05,.08)}));
  const mesh=(geo,mat)=>{keep(geo);return new THREE.Mesh(geo,mat)};
  const body=mesh(bodyGeometry(B),paint);car.add(body);
  const cabin=mesh(cabinGeometry(B),glass);car.add(cabin);
  /* punti sulla superficie via raggi: luci e dettagli si appoggiano alla carrozzeria qualunque sia la forma */
  const rc=new THREE.Raycaster();body.updateMatrixWorld(true);cabin.updateMatrixWorld(true);
  const hit=(from,dir,objs)=>{rc.set(from,dir.normalize());const h=rc.intersectObjects(objs||[body],false)[0];return h?{p:h.point.clone(),n:h.face.normal.clone()}:null};
  const strip=(pts,rad,mat)=>{if(pts.length<2)return;const cu=new THREE.CatmullRomCurve3(pts);car.add(mesh(new THREE.TubeGeometry(cu,48,rad,8,false),mat))};
  // fari: fessura sottile che segue il muso
  const yH=L(B.nose,B.top[0][1],.62);
  for(const sd of[-1,1]){const pts=[];for(let i=0;i<5;i++){const z=sd*L(hw*.30,hw*.80,i/4),h=hit(V3(hl+3,yH+i*.012,z),V3(-1,0,0));if(h)pts.push(h.p.add(h.n.multiplyScalar(.012)))}strip(pts,.013,headM)}
  // fanale posteriore a tutta larghezza
  {const pts=[],yT=B.tailLow+.10;for(let i=0;i<=10;i++){const z=L(-hw*.86,hw*.86,i/10),h=hit(V3(-hl-3,yT,z),V3(1,0,0));if(h)pts.push(h.p.add(h.n.multiplyScalar(.01)))}strip(pts,.014,tailM)}
  // labbro anteriore, presa d'aria, minigonne, estrattore: fasce scure che seguono la superficie
  const band=(y,z0,z1,rad,from,dir,inset)=>{const pts=[];for(let i=0;i<=10;i++){const z=L(z0,z1,i/10),h=hit(V3(from,y,z),V3(dir,0,0));if(h)pts.push(h.p.add(h.n.multiplyScalar(-inset)))}strip(pts,rad,trim)};
  band(B.ride+.05,-hw*.8,hw*.8,.05,hl+3,-1,.02);band(B.ride+.2,-hw*.42,hw*.42,.045,hl+3,-1,.025);
  band(B.ride+.07,-hw*.78,hw*.78,.06,-hl-3,1,.025);
  for(const sd of[-1,1]){const h=hit(V3(0,B.ride+.06,sd*4),V3(0,0,-sd));if(h){const m=mesh(new THREE.BoxGeometry(B.wb-2*(B.R+.1),.06,.05),trim);m.position.set(0,B.ride+.03,h.p.z-sd*.012);car.add(m)}}
  {const h=hit(V3(-hl-3,B.ride+.14,0),V3(1,0,0));if(h)for(const sd of[-1,1]){const e=mesh(new THREE.CylinderGeometry(.045,.045,.16,20,1,true),discM);e.rotation.z=Math.PI/2;e.position.set(h.p.x+.06,B.ride+.14,sd*B.W*.14);car.add(e)}}
  // passaruota scuri
  for(const x of[fx,-fx]){const w=mesh(new THREE.CylinderGeometry(B.R+.07,B.R+.07,B.W*.9,32,1,true),trim);w.material=trim;w.rotation.x=Math.PI/2;w.position.set(x,B.R,0);w.material.side=THREE.DoubleSide;car.add(w)}
  // ombra di contatto
  const shT=keep(shadowTex()),sh=mesh(new THREE.PlaneGeometry(B.L*1.25,B.W*1.7),keep(new THREE.MeshBasicMaterial({map:shT,transparent:true,depthWrite:false,opacity:.92,toneMapped:false})));
  sh.rotation.x=-Math.PI/2;sh.position.y=.004;sh.renderOrder=1;car.add(sh);

  /* ruote: pneumatico (tornio), canale, razze intercambiabili, disco e pinza fissi */
  const tireGeo=(()=>{const R=B.R,w=B.tw,r0=R*.72,p=[V2(r0,-w/2),V2(R-.05,-w/2),V2(R-.015,-w/2+.012),V2(R,-w/2+.05),V2(R,w/2-.05),V2(R-.015,w/2-.012),V2(R-.05,w/2),V2(r0,w/2)];const g=new THREE.LatheGeometry(p,72);g.rotateX(Math.PI/2);return keep(g)})();
  const rimR=B.R*.70,faceZ=B.tw*.36;
  const spokesGeo={};const rimGroup=style=>{const g=new THREE.Group();
    const barrel=new THREE.Mesh(keep(new THREE.CylinderGeometry(rimR,rimR,B.tw*.8,48,1,true)),rimDark);barrel.rotation.x=Math.PI/2;g.add(barrel);
    const lip=new THREE.Mesh(keep(new THREE.TorusGeometry(rimR-.006,.012,10,72)),rimM);lip.position.z=faceZ;g.add(lip);
    if(style==='disc'){const d=new THREE.Mesh(keep(new THREE.CylinderGeometry(rimR*.97,rimR*.9,.05,56)),rimM);d.rotation.x=Math.PI/2;d.position.z=faceZ-.02;g.add(d);
      for(let i=0;i<6;i++){const a=i*Math.PI/3,hole=new THREE.Mesh(keep(new THREE.CylinderGeometry(rimR*.13,rimR*.13,.054,20)),trim);hole.rotation.x=Math.PI/2;hole.position.set(Math.cos(a)*rimR*.55,Math.sin(a)*rimR*.55,faceZ-.019);g.add(hole)}}
    else{const N=style==='m10'?10:5,pair=style==='m10'?[0]:[-.075,.075],wdt=style==='m10'?.026:.034,len=rimR*.9;
      const sg=keep(new THREE.BoxGeometry(len,wdt,.035));sg.translate(len/2+rimR*.08,0,0);
      for(let i=0;i<N;i++)for(const d of pair){const m=new THREE.Mesh(sg,rimM);m.rotation.z=i*Math.PI*2/N+d;m.position.z=faceZ-.025;g.add(m)}}
    const hub=new THREE.Mesh(keep(new THREE.CylinderGeometry(rimR*.2,rimR*.22,.06,28)),rimM);hub.rotation.x=Math.PI/2;hub.position.z=faceZ-.02;g.add(hub);
    const cap=new THREE.Mesh(keep(new THREE.CylinderGeometry(rimR*.09,rimR*.09,.07,20)),trim);cap.rotation.x=Math.PI/2;cap.position.z=faceZ-.01;g.add(cap);return g};
  const wheels=[];
  for(const x of[fx,-fx])for(const sd of[-1,1]){const piv=new THREE.Group();piv.position.set(x,B.R,sd*(hw-B.tw*.22));if(sd<0)piv.rotation.y=Math.PI;
    const spin=new THREE.Group();piv.add(spin);spin.add(new THREE.Mesh(tireGeo,tireM));let rim=rimGroup(o.rim||'y5');spin.add(rim);
    const disc=new THREE.Mesh(keep(new THREE.CylinderGeometry(rimR*.78,rimR*.78,.03,40)),discM);disc.rotation.x=Math.PI/2;disc.position.z=-.01;piv.add(disc);
    const cal=new THREE.Mesh(keep(new THREE.TorusGeometry(rimR*.7,.045,10,18,1.05)),calM);cal.position.z=.035;cal.rotation.z=x>0?Math.PI*.18:Math.PI*.32;cal.scale.z=.8;piv.add(cal);
    car.add(piv);wheels.push({piv,spin,sd,get rim(){return rim},set rim(v){rim=v}})}
  function setRim(style){wheels.forEach(w=>{w.spin.remove(w.rim);w.rim=rimGroup(style);w.spin.add(w.rim)})}
  const envMats=[paint,glass,rimM,trim,discM];
  return {group:car,B,wheels,paint,headM,tailM,envMats,
    setPaint(h){const c=new THREE.Color(h);paint.color.copy(c);const hsl={};c.getHSL(hsl);const flat=hsl.s<.08&&hsl.l>.3&&hsl.l<.75;paint.metalness=flat?.12:hsl.l>.8?.25:.6;paint.roughness=flat?.4:.26},
    setRim,setFinish(h){rimM.color.set(h)},setCaliper(h){calM.color.set(h)},
    spin(a){wheels.forEach(w=>w.spin.rotation.z=-a*(w.sd<0?-1:1))},
    dispose(){disp.forEach(d=>{try{d.dispose()}catch(e){}})}};
}

/* ---------- fascio volumetrico finto (cono additivo con sfumatura) ---------- */
function beamMat(color){return new THREE.ShaderMaterial({transparent:true,depthWrite:false,blending:THREE.AdditiveBlending,side:THREE.DoubleSide,
  uniforms:{uC:{value:new THREE.Color(color)},uO:{value:0}},
  vertexShader:'varying vec2 vUv;varying vec3 vN;varying vec3 vV;void main(){vUv=uv;vec4 mv=modelViewMatrix*vec4(position,1.);vV=normalize(-mv.xyz);vN=normalize(normalMatrix*normal);gl_Position=projectionMatrix*mv;}',
  fragmentShader:'uniform vec3 uC;uniform float uO;varying vec2 vUv;varying vec3 vN;varying vec3 vV;void main(){float f=pow(abs(dot(vN,vV)),1.6);float a=pow(vUv.y,1.4)*f*uO;gl_FragColor=vec4(uC*a,a);}'})}

export async function createStage(canvas,o){
  o=o||{};const mode=o.mode==='viewer'?'viewer':'intro',intro=mode==='intro',MOB=!!o.mobile,light=o.theme==='light';
  const r=new THREE.WebGLRenderer({canvas,antialias:!intro||!MOB,alpha:!intro,powerPreference:'high-performance',preserveDrawingBuffer:false});
  r.setPixelRatio(Math.min(window.devicePixelRatio||1,MOB?1.5:(intro?1.75:2)));
  r.toneMapping=THREE.ACESFilmicToneMapping;r.toneMappingExposure=intro?1.0:1.05;
  const s=new THREE.Scene(),disp=[];const keep=x=>{disp.push(x);return x};
  const env=keep(studioEnv(r,light&&!intro));s.environment=env;
  const cam=new THREE.PerspectiveCamera(intro?(MOB?46:34):30,1,.05,200);
  const accent=new THREE.Color(o.accent||'#e1061b');
  const car=buildCar({body:o.body||'supercar',paint:o.paint,rim:o.rim,cal:o.cal,accent:o.accent});s.add(car.group);if(o.fin)car.setFinish(o.fin);
  let comp=null,bloom=null,refl=null,dust=null,dustV=null;const strips=[],beams=[],spots=[];let rim=null,headSpots=[],headBeams=[];

  if(intro){
    s.background=new THREE.Color(0x040405);s.fog=new THREE.FogExp2(0x040405,.045);
    if(!MOB){refl=new Reflector(keep(new THREE.PlaneGeometry(70,70)),{textureWidth:Math.min(1600,Math.round(innerWidth*.75)),textureHeight:Math.min(1000,Math.round(innerHeight*.75)),color:0x6a6a6a});refl.rotation.x=-Math.PI/2;s.add(refl)}
    const floor=new THREE.Mesh(keep(new THREE.PlaneGeometry(70,70)),keep(new THREE.MeshStandardMaterial({color:0x09090a,roughness:MOB?.3:.42,metalness:MOB?.6:.1,transparent:!MOB,opacity:MOB?1:.78,envMapIntensity:.5})));
    floor.rotation.x=-Math.PI/2;floor.position.y=.002;s.add(floor);
    // linee guida sul pavimento
    const lineM=keep(new THREE.MeshBasicMaterial({color:new THREE.Color(.25,.25,.27)}));
    for(const z of[-2.2,2.2]){const ln=new THREE.Mesh(keep(new THREE.PlaneGeometry(40,.025)),lineM);ln.rotation.x=-Math.PI/2;ln.position.set(-6,.004,z);s.add(ln)}
    // strisce di luce a soffitto (si accendono una dopo l'altra) e pannello di fondo
    for(let i=0;i<5;i++){const m=keep(new THREE.MeshBasicMaterial({color:new THREE.Color(0,0,0)}));const b=new THREE.Mesh(keep(new THREE.BoxGeometry(9,.05,.16)),m);b.position.set(0,5.4,(i-2)*1.15);s.add(b);strips.push(m)}
    const accM=keep(new THREE.MeshBasicMaterial({color:accent.clone().multiplyScalar(2.2)}));
    for(const sd of[-1,1]){const v=new THREE.Mesh(keep(new THREE.BoxGeometry(.05,5,.05)),accM);v.position.set(-9,2.5,sd*5.5);s.add(v)}
    const wallM=keep(new THREE.MeshBasicMaterial({color:new THREE.Color(.05,.05,.055)}));const wall=new THREE.Mesh(keep(new THREE.PlaneGeometry(26,.06)),accM);wall.position.set(-10,.7,0);wall.rotation.y=Math.PI/2;s.add(wall);
    const back=new THREE.Mesh(keep(new THREE.PlaneGeometry(40,12)),wallM);back.position.set(-12,6,0);back.rotation.y=Math.PI/2;s.add(back);
    // luci
    s.add(new THREE.HemisphereLight(0x8890a0,0x050505,.15));
    for(const x of[1.4,0,-1.4]){const sp=new THREE.SpotLight(0xffffff,0,14,.42,.85,1.4);sp.position.set(x,5.3,0);sp.target.position.set(x,0,0);s.add(sp,sp.target);spots.push(sp);
      const bm=keep(beamMat(0xdfe6ff)),cone=new THREE.Mesh(keep(new THREE.ConeGeometry(2.2,5.3,64,1,true)),bm);cone.position.set(x,5.3/2,0);cone.rotation.x=Math.PI;s.add(cone);beams.push(bm)}
    rim=new THREE.PointLight(accent.clone(),0,9,1.6);rim.position.set(-3,1.1,-2.5);s.add(rim);
    // fari: luce sul pavimento e fascio nell'aria
    for(const sd of[-1,1]){const hs=new THREE.SpotLight(0xf2f6ff,0,24,.32,.6,1.2);hs.position.set(car.B.L/2-.1,.62,sd*.62);hs.target.position.set(car.B.L/2+8,0,sd*1.2);car.group.add(hs,hs.target);headSpots.push(hs);
      const bm=keep(beamMat(0xeef3ff)),cone=new THREE.Mesh(keep(new THREE.ConeGeometry(1.1,7,40,1,true)),bm);cone.rotation.z=Math.PI/2;cone.position.set(car.B.L/2+3.4,.6,sd*.62);car.group.add(cone);headBeams.push(bm)}
    // particelle nei fasci
    const N=MOB?700:1800,g=keep(new THREE.BufferGeometry()),p=new Float32Array(N*3);dustV=new Float32Array(N);
    for(let i=0;i<N;i++){p[i*3]=(Math.random()-.5)*9;p[i*3+1]=Math.random()*5;p[i*3+2]=(Math.random()-.5)*6;dustV[i]=.03+Math.random()*.08}
    g.setAttribute('position',new THREE.BufferAttribute(p,3));
    dust=new THREE.Points(g,keep(new THREE.PointsMaterial({map:keep(dotTex()),color:0xcfd8ff,size:MOB?.05:.035,transparent:true,opacity:0,depthWrite:false,blending:THREE.AdditiveBlending})));s.add(dust);
    comp=new EffectComposer(r);comp.addPass(new RenderPass(s,cam));
    bloom=new UnrealBloomPass(new THREE.Vector2(256,256),.6,.55,.9);comp.addPass(bloom);comp.addPass(new OutputPass());
  }else{
    const disc=new THREE.Mesh(keep(new THREE.CircleGeometry(4.4,96)),keep(new THREE.MeshStandardMaterial({color:light?0xe6e5e1:0x121214,roughness:light?.7:.35,metalness:light?0:.5,envMapIntensity:.6})));
    disc.rotation.x=-Math.PI/2;s.add(disc);
    const ringM=keep(new THREE.MeshBasicMaterial({color:accent.clone()}));const ring=new THREE.Mesh(keep(new THREE.TorusGeometry(4.4,.016,8,160)),ringM);ring.rotation.x=Math.PI/2;ring.position.y=.01;s.add(ring);strips.push(ringM);
    s.add(new THREE.HemisphereLight(0xffffff,0x202020,light?.9:.35));
    const key=new THREE.SpotLight(0xffffff,light?60:90,20,.6,.7,1.3);key.position.set(1.5,7,3);s.add(key,key.target);
    car.headM.color.setScalar(1);
  }
  if(!intro){car.headM.color.setRGB(1,1,1)}

  function dim(){const w=Math.max(1,canvas.clientWidth),h=Math.max(1,canvas.clientHeight);r.setSize(w,h,false);if(comp){comp.setSize(w,h);if(bloom)bloom.setSize(MOB?w/2:w,MOB?h/2:h)}cam.aspect=w/h;cam.updateProjectionMatrix()}
  dim();const ro='ResizeObserver' in window?new ResizeObserver(dim):null;ro&&ro.observe(canvas);addEventListener('resize',dim);

  /* ---------- regia dell'intro: posizioni e punti di mira nel tempo ---------- */
  const KEY=[[0,[7.4,.42,3.4],[0,.5,0]],[2.4,[5.2,.36,2.7],[-1.2,.5,0]],[4.2,[3.5,.42,2.35],[1.2,.42,.7]],[5.7,[.6,.5,2.6],[-.6,.45,.6]],[7.1,[-4.8,.95,3.1],[0,.5,0]],[8.6,[-4.9,3.6,-4.4],[0,.4,0]],[9.9,[.8,6.4,2.6],[0,.3,0]],[11.6,[5.4,1.3,4.4],[0,.55,0]]];
  const END=KEY[KEY.length-1][0];
  const cP=new THREE.CatmullRomCurve3(KEY.map(k=>V3(...k[1])),false,'centripetal'),cT=new THREE.CatmullRomCurve3(KEY.map(k=>V3(...k[2])),false,'centripetal');
  const keyIdx=t=>{if(t<=0)return 0;if(t>=END)return KEY.length-1;let i=0;while(KEY[i+1][0]<t)i++;const a=KEY[i][0],b=KEY[i+1][0],u=(t-a)/(b-a);return i+u};
  const P=V3(),Tg=V3();
  let t=0,last=0,raf=0,running=false,visible=true,dead=false,ang=.75,drag=false,px=0,py=0,idle=0,tilt=.18,zoom=1,tgtAng=null;
  // viewer: trascina per ruotare (360°), rotella/pizzico per lo zoom leggero
  const onDown=e=>{drag=true;px=e.clientX;py=e.clientY;idle=0;tgtAng=null;canvas.style.cursor='grabbing';try{canvas.setPointerCapture(e.pointerId)}catch(_){}};
  const onUp=()=>{drag=false;canvas.style.cursor='grab'};
  const onMv=e=>{if(!drag)return;ang-=(e.clientX-px)*.009;tilt=Math.min(.75,Math.max(.04,tilt+(e.clientY-py)*.004));px=e.clientX;py=e.clientY;idle=0};
  const onWheel=e=>{if(!e.ctrlKey&&!e.metaKey&&Math.abs(e.deltaY)<40)return;e.preventDefault();zoom=Math.min(1.35,Math.max(.75,zoom+e.deltaY*.001))};
  if(!intro){canvas.addEventListener('pointerdown',onDown);canvas.addEventListener('pointerup',onUp);canvas.addEventListener('pointercancel',onUp);canvas.addEventListener('pointermove',onMv);canvas.addEventListener('wheel',onWheel,{passive:false});canvas.style.cursor='grab'}
  let cue=-1;
  function frame(now){
    if(!running)return;raf=requestAnimationFrame(frame);
    const dt=Math.min(.05,(now-last)/1000||0);last=now;t+=dt;
    if(intro){
      // l'auto entra dal buio con i fari accesi e si ferma al centro
      const k=1-Math.pow(1-ss(.5,4.3,t),2.2),x=L(-12,0,k);car.group.position.x=x;car.spin((x+12)/car.B.R);
      const envK=.18+.82*ss(1.2,3.6,t);car.envMats.forEach(m=>m.envMapIntensity=(m===car.paint?1.25:m===car.envMats[1]?1.5:1)*envK);
      strips.forEach((m,i)=>{const on=ss(1.4+i*.28,1.7+i*.28,t),fl=on>0&&on<1?(.5+.5*Math.sin(t*70+i*3)):1;m.color.setScalar(4*on*fl)});
      spots.forEach((sp,i)=>sp.intensity=30*ss(2.3+i*.3,3.4+i*.3,t));
      beams.forEach((b,i)=>b.uniforms.uO.value=.075*ss(2.6+i*.3,4.6,t));
      headSpots.forEach(h=>h.intensity=L(90,30,ss(4,6,t)));headBeams.forEach(b=>b.uniforms.uO.value=L(.16,.035,ss(4,6.5,t)));
      car.headM.color.setScalar(L(9,5,ss(4,6,t)));car.tailM.color.setRGB(L(1.2,5,ss(6.4,7.4,t)),.06,.1);
      if(rim)rim.intensity=14*ss(6.5,8,t);
      if(dust){dust.material.opacity=.65*ss(1.5,4,t);const pa=dust.geometry.attributes.position,ar=pa.array;for(let i=0;i<dustV.length;i++){let y=ar[i*3+1]+dustV[i]*dt;if(y>5)y=0;ar[i*3+1]=y;ar[i*3]+=Math.sin(t*.3+i)*.0008}pa.needsUpdate=true}
      if(bloom)bloom.strength=.55+.35*ss(3.6,4.4,t)*(1-ss(5,7,t))+.15*ss(10,11.5,t);
      if(t<=END){const u=keyIdx(t)/(KEY.length-1);cP.getPoint(u,P);cT.getPoint(u,Tg)}
      else{const a0=Math.atan2(KEY[KEY.length-1][1][2],KEY[KEY.length-1][1][0]),rr=Math.hypot(5.4,4.4),a=a0-(t-END)*.06;P.set(Math.cos(a)*rr,1.3,Math.sin(a)*rr);Tg.set(0,.55,0)}
      if(cam.aspect<.8){P.multiplyScalar(1.28);P.y=Math.max(P.y,.45)}
      cam.position.copy(P);cam.lookAt(Tg);
      const c=t<.2?0:t<4.3?1:t<7?2:t<END-.8?3:4;if(c!==cue){cue=c;o.onCue&&o.onCue(c)}
      o.onTick&&o.onTick(Math.min(1,t/END),t);
      comp.render();
    }else{
      idle+=dt;if(tgtAng!=null){ang+=(tgtAng-ang)*Math.min(1,dt*4);if(Math.abs(tgtAng-ang)<.002)tgtAng=null}else if(!drag&&idle>1.6&&!o.still)ang+=dt*.22;
      const rad=(cam.aspect<1?11.5:8.6)*zoom*(car.B.L/4.62);cam.position.set(Math.sin(ang)*rad*Math.cos(tilt),.6+Math.sin(tilt)*rad,Math.cos(ang)*rad*Math.cos(tilt));Tg.set(0,.55,0);cam.lookAt(Tg);
      r.render(s,cam);
    }
  }
  function start(){if(dead||running||!visible||document.hidden)return;running=true;last=performance.now();raf=requestAnimationFrame(frame)}
  function stop(){running=false;cancelAnimationFrame(raf)}
  const io='IntersectionObserver' in window?new IntersectionObserver(es=>{visible=es[0].isIntersecting;visible?start():stop()},{threshold:.01}):null;io&&io.observe(canvas);
  const onVis=()=>{document.hidden?stop():start()};document.addEventListener('visibilitychange',onVis);
  // prima immagine subito (anche per le istantanee), poi l'animazione
  if(intro){comp.render()}else{frame(performance.now());stop()}
  start();
  const gl=r.getContext();
  return {
    gl,duration:END,
    setPaint:h=>car.setPaint(h),setRim:v=>car.setRim(v),setFinish:h=>car.setFinish(h),setCaliper:h=>car.setCaliper(h),
    setAccent(h){accent.set(h);if(rim)rim.color.copy(accent);strips.length&&!intro&&strips[0].color.copy(accent)},
    view(name){const m={front:Math.PI/2,side:0,rear:-Math.PI/2,three:.75,back3:-2.3};if(name in m){tgtAng=m[name]+Math.round((ang-m[name])/(Math.PI*2))*Math.PI*2;idle=0;if(name==='top')tilt=.7}},
    isRunning:()=>running,time:()=>t,
    snapshots(n){const out=[],a0=ang;try{for(let i=0;i<n;i++){const a=.75+i*(Math.PI*2/n),rad=(cam.aspect<1?11.5:8.6)*(car.B.L/4.62);cam.position.set(Math.sin(a)*rad,i===1?3.2:1.6,Math.cos(a)*rad);cam.lookAt(0,.55,0);r.render(s,cam);out.push(canvas.toDataURL('image/jpeg',.82))}}catch(e){}ang=a0;return out},
    dispose(){if(dead)return;dead=true;stop();io&&io.disconnect();ro&&ro.disconnect();removeEventListener('resize',dim);document.removeEventListener('visibilitychange',onVis);
      if(!intro){canvas.removeEventListener('pointerdown',onDown);canvas.removeEventListener('pointerup',onUp);canvas.removeEventListener('pointercancel',onUp);canvas.removeEventListener('pointermove',onMv);canvas.removeEventListener('wheel',onWheel)}
      try{car.dispose();disp.forEach(d=>{try{d.dispose()}catch(e){}});if(refl)refl.getRenderTarget().dispose();if(comp){comp.passes.forEach(p=>p.dispose&&p.dispose());comp.dispose()}
        s.traverse(x=>{if(x.isLight&&x.shadow&&x.shadow.map)x.shadow.map.dispose()});r.renderLists.dispose();r.dispose();r.forceContextLoss()}catch(e){}}
  };
}
