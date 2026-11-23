/* MC prototipo — dati dimostrativi, lingua, archivio locale, prezzi, disponibilità.
   Tutto lato client: nessuna chiamata a server. I numeri sono ESEMPI. */
'use strict';
const BIZ={name:'MC',addr:'Via Arbe 95',cap:'20125 Milano',tel:'+39 345 704 4414',telHref:'tel:+393457044414',wa:'https://wa.me/393457044414'};

/* ---------- archivio locale sicuro ---------- */
const mem={};
const store={
  get(k,d){try{const v=localStorage.getItem('mc_'+k);return v==null?(k in mem?mem[k]:d):JSON.parse(v)}catch(e){return k in mem?mem[k]:d}},
  set(k,v){mem[k]=v;try{localStorage.setItem('mc_'+k,JSON.stringify(v))}catch(e){}},
  del(k){delete mem[k];try{localStorage.removeItem('mc_'+k)}catch(e){}}
};

/* ---------- lingua ---------- */
let LANG=store.get('lang','it');
const T=(it,en)=>LANG==='en'?en:it;
const loc=()=>LANG==='en'?'en-GB':'it-IT';
const nf=o=>{try{return new Intl.NumberFormat(loc(),Object.assign({useGrouping:'always'},o))}catch(e){return new Intl.NumberFormat(loc(),o)}};
const eur=n=>nf({style:'currency',currency:'EUR',maximumFractionDigits:0}).format(Math.round(n));
const num=n=>nf({}).format(n);
const PW=()=>T('CV','hp');
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ---------- date ---------- */
const DAY=864e5;
const d0=d=>{const x=new Date(d);x.setHours(0,0,0,0);return x};
const TODAY=d0(new Date());
const addD=(d,n)=>{const x=new Date(d);x.setDate(x.getDate()+n);return x};
const iso=d=>{const x=new Date(d);return x.getFullYear()+'-'+String(x.getMonth()+1).padStart(2,'0')+'-'+String(x.getDate()).padStart(2,'0')};
const fromIso=s=>{const p=String(s||'').split('-').map(Number);return p.length===3&&p.every(n=>!isNaN(n))?new Date(p[0],p[1]-1,p[2]):null};
const fmtD=(d,o)=>new Intl.DateTimeFormat(loc(),o||{day:'numeric',month:'short',year:'numeric'}).format(new Date(d));
const fmtDs=d=>fmtD(d,{day:'numeric',month:'short'});

/* ---------- accento ---------- */
const ACCENTS=[
  {id:'rosso',it:'Rosso corsa',en:'Racing red',c:'#e1061b',on:'#ffffff',hi:'#c40517'},
  {id:'giallo',it:'Giallo Modena',en:'Modena yellow',c:'#ffd60a',on:'#111111',hi:'#7a5f00'},
  {id:'blu',it:'Blu elettrico',en:'Electric blue',c:'#2563eb',on:'#ffffff',hi:'#1d4ed8'},
  {id:'verde',it:'Verde inglese',en:'Racing green',c:'#0a7d45',on:'#ffffff',hi:'#086a3a'},
  {id:'arancio',it:'Arancio',en:'Orange',c:'#ff6b00',on:'#111111',hi:'#a84300'},
  {id:'argento',it:'Argento',en:'Silver',c:'#9aa0a8',on:'#111111',hi:'#555a62'}
];
function applyAccent(id){
  const a=ACCENTS.find(x=>x.id===id)||ACCENTS[0];store.set('accent',a.id);
  const s=document.documentElement.style;s.setProperty('--accent',a.c);s.setProperty('--on-accent',a.on);s.setProperty('--accent-hi',a.hi);
  window.dispatchEvent(new CustomEvent('mc:accent',{detail:a}));return a;
}
const curAccent=()=>ACCENTS.find(x=>x.id===store.get('accent','rosso'))||ACCENTS[0];

/* ---------- flotta (dati tecnici pubblici dei modelli; prezzi, km e anni = esempi) ---------- */
const PAINTS={rosso:{hex:'#c8102e',it:'Rosso',en:'Red'},nero:{hex:'#16171a',it:'Nero',en:'Black'},nardo:{hex:'#8b8f93',it:'Grigio Nardo',en:'Nardo Grey'}};
const CATS={supercar:{it:'Supercar',en:'Supercar'},suv:{it:'SUV sportivi',en:'Performance SUV'},coupe:{it:'Coupé e berline',en:'Coupés & saloons'},wagon:{it:'Wagon e compatte',en:'Estates & hatches'}};
const FUELS={phev:{it:'Ibrida plug-in',en:'Plug-in hybrid'},mhev:{it:'Benzina mild hybrid',en:'Mild-hybrid petrol'},benz:{it:'Benzina',en:'Petrol'}};
const CARS=[
 {id:'ferrari-296-gts',brand:'Ferrari',model:'296 GTS',paint:'rosso',cat:'supercar',fuel:'phev',body:'spider',
  cv:830,s100:2.9,vmax:330,eng:{it:'V6 2.992 cc biturbo + motore elettrico',en:'2,992 cc twin-turbo V6 + electric motor'},gear:{it:'F1 doppia frizione 8 marce',en:'8-speed dual-clutch'},drive:{it:'Posteriore',en:'Rear-wheel'},seats:2,
  day:1900,dep:15000,kmDay:150,age:27,year:2023,km:8500,sale:349000,
  geo:{x0:81,x1:919,nose:264,cowl:[345,206],rs:[458,134],re:[545,137],deck:[715,188],tail:192,cl:22,fw:281,rw:758,r:63}},
 {id:'audi-rs-q8',brand:'Audi',model:'RS Q8',paint:'nero',cat:'suv',fuel:'mhev',body:'suv',
  cv:600,s100:3.8,vmax:250,eng:{it:'V8 4.0 TFSI biturbo mild hybrid',en:'4.0 TFSI twin-turbo V8 mild hybrid'},gear:{it:'Tiptronic 8 marce',en:'8-speed tiptronic'},drive:{it:'Integrale quattro',en:'quattro AWD'},seats:5,
  day:590,dep:5000,kmDay:200,age:25,year:2022,km:31000,sale:148000,
  geo:{x0:40,x1:960,nose:192,cowl:[305,150],rs:[425,44],re:[690,52],deck:[885,146],tail:158,cl:44,fw:214,rw:764,r:72}},
 {id:'bmw-m4-competition',brand:'BMW',model:'M3/M4 Competition',paint:'nero',cat:'coupe',fuel:'benz',body:'coupe',
  cv:510,s100:3.9,vmax:250,eng:{it:'6 cilindri in linea 3.0 biturbo',en:'3.0 twin-turbo straight-six'},gear:{it:'M Steptronic 8 marce',en:'8-speed M Steptronic'},drive:{it:'Posteriore (M xDrive a richiesta)',en:'Rear-wheel (M xDrive optional)'},seats:4,
  day:450,dep:4000,kmDay:200,age:25,year:2022,km:27000,sale:89000,
  geo:{x0:60,x1:940,nose:234,cowl:[338,186],rs:[468,96],re:[600,100],deck:[835,184],tail:190,cl:27,fw:222,rw:747,r:62}},
 {id:'audi-rs-q3-sportback',brand:'Audi',model:'RS Q3 Sportback',paint:'nero',cat:'suv',fuel:'benz',body:'suv',
  cv:400,s100:4.5,vmax:250,eng:{it:'5 cilindri 2.5 TFSI',en:'2.5 TFSI five-cylinder'},gear:{it:'S tronic 7 marce',en:'7-speed S tronic'},drive:{it:'Integrale quattro',en:'quattro AWD'},seats:5,
  day:290,dep:2500,kmDay:250,age:23,year:2023,km:19000,sale:62000,
  geo:{x0:86,x1:913,nose:206,cowl:[335,160],rs:[445,66],re:[640,74],deck:[850,158],tail:168,cl:38,fw:251,rw:743,r:64}},
 {id:'audi-rs6-avant',brand:'Audi',model:'RS6 Avant',paint:'nardo',cat:'wagon',fuel:'mhev',body:'wagon',
  cv:600,s100:3.6,vmax:250,eng:{it:'V8 4.0 TFSI biturbo mild hybrid',en:'4.0 TFSI twin-turbo V8 mild hybrid'},gear:{it:'Tiptronic 8 marce',en:'8-speed tiptronic'},drive:{it:'Integrale quattro',en:'quattro AWD'},seats:5,
  day:620,dep:5000,kmDay:200,age:25,year:2021,km:44000,sale:129000,
  geo:{x0:41,x1:958,nose:224,cowl:[322,180],rs:[448,84],re:[800,96],deck:[928,172],tail:182,cl:28,fw:215,rw:753,r:66}},
 {id:'audi-rs3-sportback',brand:'Audi',model:'RS3 Sportback',paint:'nardo',cat:'wagon',fuel:'benz',body:'hatch',
  cv:400,s100:3.8,vmax:250,eng:{it:'5 cilindri 2.5 TFSI',en:'2.5 TFSI five-cylinder'},gear:{it:'S tronic 7 marce',en:'7-speed S tronic'},drive:{it:'Integrale quattro',en:'quattro AWD'},seats:5,
  day:320,dep:3000,kmDay:250,age:23,year:2022,km:35000,sale:58000,
  geo:{x0:97,x1:903,nose:224,cowl:[348,180],rs:[458,88],re:[720,98],deck:[870,170],tail:180,cl:28,fw:259,rw:742,r:61}}
];
const carBy=id=>CARS.find(c=>c.id===id);
const carName=c=>c.brand+' '+c.model;
const paintName=c=>T(PAINTS[c.paint].it,PAINTS[c.paint].en);

/* ---------- sagoma vettoriale (disegnata a mano, nessun asset di marca) ---------- */
function shade(hex,f){const n=parseInt(hex.slice(1),16);let r=n>>16,g=n>>8&255,b=n&255;
  if(f>0){r+=(255-r)*f;g+=(255-g)*f;b+=(255-b)*f}else{r*=1+f;g*=1+f;b*=1+f}
  return '#'+[r,g,b].map(v=>Math.max(0,Math.min(255,Math.round(v))).toString(16).padStart(2,'0')).join('')}
let SIL=0;
/* profili disegnati per modello: linea superiore (dal muso alla coda), vetrata, montanti, portiere */
const SHAPE={
 'ferrari-296-gts':{nose:274,tail:192,ty:130,belt:197,top:'C150 252 222 214 296 205C336 200 366 200 394 196C432 168 462 139 500 132C530 128 556 130 576 138C618 152 658 178 704 185C760 185 832 183 882 187L905 192',
   glass:'M412 196C442 172 470 147 502 143L556 145C572 152 588 170 598 187Z',pil:[],doors:[404,606],mir:[420,190],intake:1},
 'audi-rs-q8':{nose:194,tail:150,ty:38,belt:160,top:'C112 180 222 170 320 160C372 100 412 48 472 42C560 36 640 42 700 56C780 76 842 106 884 136L946 150',
   glass:'M348 158C388 108 422 62 472 56C560 50 640 56 694 68C760 86 808 112 836 140L348 160Z',pil:[545,700],doors:[336,548,722],mir:[352,150]},
 'bmw-m4-competition':{nose:238,tail:182,ty:90,belt:190,top:'C150 218 262 204 364 190C412 140 452 100 506 94C570 90 630 94 680 106C730 124 770 152 808 172C850 174 890 176 926 182L926 182',
   glass:'M390 188C426 146 460 110 506 106C568 102 625 106 668 118C708 132 740 152 768 172L390 190Z',pil:[552],doors:[380,556,720],mir:[396,182]},
 'audi-rs-q3-sportback':{nose:212,tail:166,ty:60,belt:172,top:'C160 196 262 184 342 172C386 118 422 74 470 66C540 60 610 66 660 80C730 100 792 128 840 152L899 166',
   glass:'M368 170C402 124 432 86 472 80C540 74 606 80 652 92C710 108 760 132 792 156L368 172Z',pil:[540,668],doors:[356,542,700],mir:[372,162]},
 'audi-rs6-avant':{nose:226,tail:180,ty:80,belt:184,top:'C130 208 242 196 342 184C390 134 430 94 482 86C600 78 740 82 832 94L864 98C892 120 916 150 934 176L944 180',
   glass:'M368 182C404 140 438 104 482 98C600 92 730 94 822 106C850 124 870 148 882 172L368 184Z',pil:[534,694,812],doors:[358,536,700],mir:[374,174]},
 'audi-rs3-sportback':{nose:226,tail:178,ty:84,belt:184,top:'C175 208 272 196 364 184C406 136 442 98 490 90C580 84 680 88 752 98L780 102C812 126 840 152 862 174L889 178',
   glass:'M390 182C422 142 450 108 490 102C580 96 670 100 744 110C770 128 792 150 806 170L390 184Z',pil:[548,694],doors:[380,550,704],mir:[396,174]}
};
function carSVG(car,o){o=o||{};const g=car.geo,S=SHAPE[car.id],G=350,hex=o.hex||PAINTS[car.paint].hex,id='s'+(++SIL);
  const by=G-g.cl,cy=G-g.r,ar=g.r+8,dx=Math.sqrt(ar*ar-(by-cy)*(by-cy)),sx=g.x0+26,ty=S.ty,R=g.r;
  const body=`M${g.x0+24} ${by}L${g.x0+8} ${by-12}Q${g.x0-4} ${S.nose+22} ${sx} ${S.nose}${S.top}Q${g.x1+4} ${S.tail+6} ${g.x1} ${S.tail+36}L${g.x1-5} ${by-24}Q${g.x1-8} ${by} ${g.x1-34} ${by}`+
   `L${g.rw+dx} ${by}A${ar} ${ar} 0 1 0 ${g.rw-dx} ${by}L${g.fw+dx} ${by}A${ar} ${ar} 0 1 0 ${g.fw-dx} ${by}Z`;
  const sh=Math.min(.8,(S.belt+24-ty)/(by-ty)),n=parseInt(hex.slice(1),16),lum=(.3*(n>>16)+.59*(n>>8&255)+.11*(n&255))/255,dk=lum<.16;
  const stops=dk?[[0,shade(hex,.55)],[.2,shade(hex,.26)],[sh-.1,shade(hex,.08)],[sh,shade(hex,.34)],[sh+.035,shade(hex,-.25)],[.9,shade(hex,.1)],[1,shade(hex,-.6)]]
               :[[0,shade(hex,.5)],[.2,shade(hex,.2)],[sh-.1,hex],[sh,shade(hex,.36)],[sh+.035,shade(hex,-.3)],[.9,shade(hex,-.12)],[1,shade(hex,-.6)]];
  const wheel=x=>{let sp='';for(let i=0;i<5;i++)for(const d of[-9,9]){const a=(i*72-90+d)*Math.PI/180,b=(i*72-90+d*.35)*Math.PI/180;sp+=`<path d="M${(x+Math.cos(b)*R*.12).toFixed(1)} ${(cy+Math.sin(b)*R*.12).toFixed(1)}L${(x+Math.cos(a)*R*.7).toFixed(1)} ${(cy+Math.sin(a)*R*.7).toFixed(1)}"/>`}
    return `<circle cx="${x}" cy="${cy}" r="${R}" fill="url(#${id}t)"/><circle cx="${x}" cy="${cy}" r="${R*.9}" fill="none" stroke="#1d1e21" stroke-width="1.5"/>
    <circle cx="${x}" cy="${cy}" r="${R*.74}" fill="#101113"/><circle cx="${x}" cy="${cy}" r="${R*.5}" fill="#2b2c30" stroke="#3b3d42" stroke-width="1.5"/>
    <path d="M${x-R*.5} ${cy-R*.12}A${R*.52} ${R*.52} 0 0 1 ${x-R*.2} ${cy-R*.48}" stroke="var(--accent)" stroke-width="${R*.15}" fill="none"/>
    <g stroke="url(#${id}w)" stroke-width="${R*.075}" stroke-linecap="round">${sp}</g>
    <circle cx="${x}" cy="${cy}" r="${R*.73}" fill="none" stroke="url(#${id}w)" stroke-width="${R*.07}"/><circle cx="${x}" cy="${cy}" r="${R*.13}" fill="#1a1b1e" stroke="#8d9097" stroke-width="2"/>`};
  const vb=o.vb||[0,ty-24,1000,G+22-(ty-24)];
  return `<svg viewBox="${vb.join(' ')}" role="img" aria-label="${esc(o.label||carName(car))}" class="${o.cls||''}" ${o.vb?'preserveAspectRatio="xMidYMid slice"':''}>
  <defs><linearGradient id="${id}b" gradientUnits="userSpaceOnUse" x1="0" y1="${ty}" x2="0" y2="${by}">${stops.map(k=>`<stop offset="${k[0].toFixed(3)}" stop-color="${k[1]}"/>`).join('')}</linearGradient>
  <linearGradient id="${id}g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#5a6675"/><stop offset=".42" stop-color="#11151b"/><stop offset=".6" stop-color="#0a0c10"/><stop offset="1" stop-color="#2a323c"/></linearGradient>
  <linearGradient id="${id}w" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c3c6cc"/><stop offset="1" stop-color="#5d6067"/></linearGradient>
  <radialGradient id="${id}t"><stop offset=".7" stop-color="#17171a"/><stop offset=".9" stop-color="#0d0d0f"/><stop offset="1" stop-color="#050506"/></radialGradient>
  <radialGradient id="${id}s"><stop offset="0" stop-color="#14161a" stop-opacity=".5"/><stop offset=".55" stop-color="#14161a" stop-opacity=".22"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>
  <linearGradient id="${id}h" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".3" stop-color="#fff" stop-opacity=".5"/><stop offset=".8" stop-color="#fff" stop-opacity=".35"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>
  <ellipse cx="${(g.x0+g.x1)/2}" cy="${G+2}" rx="${(g.x1-g.x0)*.54}" ry="15" fill="url(#${id}s)"/>
  <circle cx="${g.fw}" cy="${cy}" r="${ar+2}" fill="#050506"/><circle cx="${g.rw}" cy="${cy}" r="${ar+2}" fill="#050506"/>
  <path d="${body}" fill="url(#${id}b)" stroke="${lum>.6?'rgba(17,18,20,.34)':'rgba(17,18,20,.14)'}" stroke-width="1.4" stroke-linejoin="round"/>
  <path d="M${sx+6} ${S.nose+3}${S.top}" fill="none" stroke="url(#${id}h)" stroke-width="1.6" opacity="${dk?.55:.7}"/>
  <path d="${S.glass}" fill="url(#${id}g)"/>
  ${S.pil.map(x=>`<path d="M${x-3} ${ty+16}L${x+3} ${S.belt+2}" stroke="#0b0c0e" stroke-width="7"/>`).join('')}
  <path d="${S.doors.map(x=>`M${x} ${S.belt+10}C${x-4} ${S.belt+60} ${x-2} ${by-50} ${x+4} ${by-22}`).join('')}" fill="none" stroke="rgba(0,0,0,.45)" stroke-width="1.6"/>
  ${S.doors.slice(1).map(x=>`<path d="M${x-52} ${S.belt+30}h30" stroke="${shade(hex,dk?.3:-.35)}" stroke-width="3.5" stroke-linecap="round"/>`).join('')}
  <path d="M${S.mir[0]} ${S.mir[1]}l-26 3q-4 8 4 12l22 -2z" fill="${shade(hex,dk?.12:-.35)}"/>
  ${S.intake?`<path d="M618 214L690 204Q684 240 672 262L634 266Q618 244 618 214Z" fill="#08080a"/><path d="M618 214L690 204" stroke="${shade(hex,.3)}" stroke-width="1.5"/>`:''}
  <path d="M${g.x0+7} ${S.nose+36}L${g.x0+44} ${S.nose+30}L${g.x0+52} ${by-18}L${g.x0+12} ${by-13}Z" fill="#08080a"/>
  <path d="M${g.x1-5} ${by-46}L${g.x1-44} ${by-40}L${g.x1-48} ${by-12}L${g.x1-9} ${by-18}Z" fill="#0a0a0c" opacity=".85"/>
  <path d="M${g.x0+16} ${by-5}H${g.fw-dx-1}M${g.fw+dx+1} ${by-5}H${g.rw-dx-1}M${g.rw+dx+1} ${by-5}H${g.x1-26}" stroke="#09090b" stroke-width="10"/>
  <path d="M${sx-14} ${S.nose+24}l62 -13l-6 9l-48 12z" fill="#eef4ff" stroke="rgba(17,18,20,.35)" stroke-width="1"/>
  <path d="M${g.x1-3} ${S.tail+15}l-58 -5l2 8l54 8z" fill="#d81420"/><path d="M${g.x1-3} ${S.tail+15}l-58 -5" stroke="#ff6a70" stroke-width="1.2"/>
  ${wheel(g.fw)}${wheel(g.rw)}
  </svg>`}

/* ---------- regole di prezzo (reali, lato client) ---------- */
const RULES={weekend:.15,disc3:.10,disc7:.20,depositPct:.30,holdMin:15};
const PLACES=[
  {id:'sede',it:'Sede MC · Via Arbe 95, Milano',en:'MC showroom · Via Arbe 95, Milan',fee:0},
  {id:'milano',it:'Consegna a domicilio a Milano',en:'Home delivery in Milan',fee:90},
  {id:'linate',it:'Aeroporto di Linate',en:'Linate airport',fee:120},
  {id:'malpensa',it:'Aeroporto di Malpensa',en:'Malpensa airport',fee:180},
  {id:'orio',it:'Aeroporto di Bergamo Orio al Serio',en:'Bergamo Orio al Serio airport',fee:190}
];
const EXTRAS=[
  {id:'km',it:'Chilometri illimitati',en:'Unlimited mileage',dIt:'Nessun limite giornaliero',dEn:'No daily cap',per:'day',pct:.18},
  {id:'driver',it:'Secondo guidatore',en:'Additional driver',dIt:'Stessi requisiti del primo',dEn:'Same requirements as main driver',per:'day',eur:25},
  {id:'kasko',it:'Riduzione franchigia',en:'Excess reduction',dIt:'Franchigia danni ridotta',dEn:'Lower damage excess',per:'day',pct:.12},
  {id:'shoot',it:'Shooting foto e video',en:'Photo & video shoot',dIt:'Fotografo e location a Milano',dEn:'Photographer and Milan location',per:'once',eur:350},
  {id:'wedding',it:'Allestimento cerimonia',en:'Wedding set-up',dIt:'Nastri, fiori e autista a richiesta',dEn:'Ribbons, flowers, chauffeur on request',per:'once',eur:200}
];
const TIMES=['09:00','10:00','11:00','12:00','13:00','14:00','15:00','16:00','17:00','18:00'];
function rentalDays(from,tf,to,tt){const a=fromIso(from),b=fromIso(to);if(!a||!b)return 0;
  const [h1,m1]=(tf||'10:00').split(':').map(Number),[h2,m2]=(tt||'10:00').split(':').map(Number);
  a.setHours(h1,m1);b.setHours(h2,m2);const ms=b-a;if(ms<=0)return 0;return Math.max(1,Math.ceil((ms-59*6e4)/DAY))}
function quote(car,q){q=q||{};const days=rentalDays(q.from,q.tf,q.to,q.tt);
  const out={days,lines:[],extras:[],total:0,now:0,later:0,dep:car.dep,ok:days>0};if(!days)return out;
  let base=car.day*days,wk=0;const a=fromIso(q.from);
  for(let i=0;i<days;i++){const w=addD(a,i).getDay();if(w===5||w===6||w===0)wk+=car.day*RULES.weekend}
  const dPct=days>=7?RULES.disc7:days>=3?RULES.disc3:0,disc=(base+wk)*dPct;
  out.base=base;out.weekend=wk;out.disc=disc;out.discPct=dPct;out.rental=base+wk-disc;
  const pu=PLACES.find(p=>p.id===(q.pu||'sede')),dr=PLACES.find(p=>p.id===(q.dr||'sede'));out.pu=pu;out.dr=dr;out.delivery=pu.fee+dr.fee;
  (q.extras||[]).forEach(id=>{const e=EXTRAS.find(x=>x.id===id);if(!e)return;const v=e.per==='day'?(e.pct?Math.round(car.day*e.pct):e.eur)*days:e.eur;out.extras.push({e,v})});
  out.extraTot=out.extras.reduce((s,x)=>s+x.v,0);
  out.total=Math.round(out.rental+out.delivery+out.extraTot);out.now=Math.round(out.total*RULES.depositPct);out.later=out.total-out.now;
  return out}
const extraPrice=(car,e)=>e.per==='day'?(e.pct?Math.round(car.day*e.pct):e.eur):e.eur;
/* politica di cancellazione: quanto dell'acconto torna indietro oggi */
function cancelPolicy(from,now){const a=fromIso(from);if(!a)return null;const dd=Math.floor((a-TODAY)/DAY);
  const pct=dd>=7?1:dd>=3?.5:0;return {days:dd,pct,refund:Math.round((now||0)*pct)}}

/* ---------- prenotazioni dimostrative e disponibilità ---------- */
const SEED=[
  {code:'MC-D101',car:'audi-rs6-avant',a:-2,b:0,who:'Cliente demo 01',st:'attiva'},
  {code:'MC-D102',car:'audi-rs-q8',a:0,b:3,who:'Cliente demo 02',st:'confermata'},
  {code:'MC-D103',car:'ferrari-296-gts',a:2,b:4,who:'Cliente demo 03',st:'confermata'},
  {code:'MC-D104',car:'bmw-m4-competition',a:1,b:2,who:'Cliente demo 04',st:'attesa'},
  {code:'MC-D105',car:'audi-rs3-sportback',a:5,b:9,who:'Cliente demo 05',st:'confermata'},
  {code:'MC-D106',car:'audi-rs6-avant',a:6,b:8,who:'Cliente demo 06',st:'confermata'},
  {code:'MC-D107',car:'ferrari-296-gts',a:9,b:10,who:'Cliente demo 07',st:'attesa'},
  {code:'MC-D108',car:'audi-rs-q8',a:8,b:12,who:'Cliente demo 08',st:'confermata'}
].map(s=>Object.assign(s,{from:iso(addD(TODAY,s.a)),to:iso(addD(TODAY,s.b)),demo:true}));
const myBookings=()=>store.get('bookings',[]);
const allBookings=()=>SEED.concat(myBookings().filter(b=>b.st!=='annullata'));
const FLEET0={'ferrari-296-gts':'ok','audi-rs-q8':'out','bmw-m4-competition':'ok','audi-rs-q3-sportback':'maint','audi-rs6-avant':'out','audi-rs3-sportback':'ok'};
const fleetState=()=>Object.assign({},FLEET0,store.get('fleet',{}));
const STATE={ok:{it:'Disponibile',en:'Available',cls:'ok'},out:{it:'Noleggiata',en:'On rent',cls:'warn'},maint:{it:'Manutenzione',en:'Maintenance',cls:'bad'}};
function overlaps(carId,from,to,skip){const a=fromIso(from),b=fromIso(to);if(!a||!b)return null;
  return allBookings().find(k=>k.car===carId&&k.code!==skip&&fromIso(k.from)<=b&&fromIso(k.to)>=a)||null}
/* true = libera, false = occupata, null = date mancanti */
function isFree(car,from,to){if(fleetState()[car.id]==='maint')return false;if(!from||!to)return null;return !overlaps(car.id,from,to)}
function nextFree(car){if(fleetState()[car.id]==='maint')return null;let d=TODAY;for(let i=0;i<40;i++){if(!overlaps(car.id,iso(d),iso(d)))return d;d=addD(d,1)}return null}

function newCode(){const d=new Date();return 'MC-'+String(d.getDate()).padStart(2,'0')+String(d.getMonth()+1).padStart(2,'0')+'-'+String(47+myBookings().length).padStart(3,'0')}
function wipeDemo(){Object.keys(mem).forEach(k=>delete mem[k]);try{Object.keys(localStorage).filter(k=>k.indexOf('mc_')===0).forEach(k=>localStorage.removeItem(k))}catch(e){}}
/* ---------- preferiti, confronto ---------- */
const favs=()=>store.get('favs',[]);
const toggleFav=id=>{const f=favs(),i=f.indexOf(id);i<0?f.push(id):f.splice(i,1);store.set('favs',f);return i<0};
let CMP=[];

/* ---------- lead (CRM) ---------- */
const STAGES=[['nuovo','Nuovo','New'],['contatto','Contattato','Contacted'],['appuntamento','Appuntamento','Appointment'],['preventivo','Preventivo','Quote'],['chiuso','Chiuso','Closed']];
const LEAD0=[
  {id:'L1',who:'Contatto demo A',mail:'demo.a@example.com',type:'testdrive',car:'audi-rs6-avant',stage:'nuovo',d:0},
  {id:'L2',who:'Contatto demo B',mail:'demo.b@example.com',type:'permuta',car:'audi-rs-q8',stage:'nuovo',d:-1},
  {id:'L3',who:'Contatto demo C',mail:'demo.c@example.com',type:'finanziamento',car:'bmw-m4-competition',stage:'contatto',d:-2},
  {id:'L4',who:'Contatto demo D',mail:'demo.d@example.com',type:'noleggio',car:'ferrari-296-gts',stage:'appuntamento',d:-3},
  {id:'L5',who:'Contatto demo E',mail:'demo.e@example.com',type:'preventivo',car:'audi-rs3-sportback',stage:'preventivo',d:-5},
  {id:'L6',who:'Contatto demo F',mail:'demo.f@example.com',type:'info',car:'audi-rs-q3-sportback',stage:'chiuso',d:-9}
];
const leads=()=>store.get('leads',null)||LEAD0.map(l=>Object.assign({},l));
const LEADT={info:['Informazioni','Information'],testdrive:['Test drive','Test drive'],permuta:['Permuta','Trade-in'],finanziamento:['Finanziamento','Finance'],preventivo:['Preventivo','Quote'],noleggio:['Noleggio','Rental']};
/* ogni richiesta crea o aggiorna un contatto: niente doppioni sulla stessa email */
function upsertLead(d){const L=leads(),m=(d.mail||'').trim().toLowerCase();let x=L.find(l=>l.mail.toLowerCase()===m);
  if(x){x.type=d.type;x.car=d.car;x.note=d.note;x.d=0;x.upd=(x.upd||0)+1;if(x.stage==='chiuso')x.stage='nuovo'}
  else{x={id:'L'+Date.now().toString(36),who:d.who,mail:d.mail,tel:d.tel,type:d.type,car:d.car,note:d.note,stage:'nuovo',d:0,mine:true};L.unshift(x)}
  store.set('leads',L);return x}
