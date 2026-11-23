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
  {id:'rosso',it:'Rosso corsa',en:'Racing red',c:'#e1061b',on:'#ffffff',hi:'#ff5a66'},
  {id:'giallo',it:'Giallo Modena',en:'Modena yellow',c:'#ffd60a',on:'#111111',hi:'#ffd60a'},
  {id:'blu',it:'Blu elettrico',en:'Electric blue',c:'#2563eb',on:'#ffffff',hi:'#7aa5ff'},
  {id:'verde',it:'Verde inglese',en:'Racing green',c:'#0a7d45',on:'#ffffff',hi:'#3ddc84'},
  {id:'arancio',it:'Arancio',en:'Orange',c:'#ff6b00',on:'#111111',hi:'#ff8a3d'},
  {id:'argento',it:'Argento',en:'Silver',c:'#d7dadf',on:'#111111',hi:'#e6e8eb'}
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
function carSVG(car,o){o=o||{};const g=car.geo,G=350,hex=o.hex||PAINTS[car.paint].hex,id='s'+(++SIL),tz=!!o.teaser;
  const by=G-g.cl,cy=G-g.r,ar=g.r+9,dx=Math.sqrt(ar*ar-(by-cy)*(by-cy));
  const [cx_,cyy]=g.cowl,[rsx,ry]=g.rs,[rex,rey]=g.re,[dkx,dky]=g.deck;
  const top=`C${g.x0+(cx_-g.x0)*.42} ${g.nose-24} ${cx_-46} ${cyy+8} ${cx_} ${cyy}`+
   `C${cx_+(rsx-cx_)*.5} ${cyy-(cyy-ry)*.72} ${rsx-44} ${ry} ${rsx} ${ry}`+
   `C${rsx+(rex-rsx)*.4} ${ry-4} ${rex-30} ${rey-3} ${rex} ${rey}`+
   `C${rex+(dkx-rex)*.5} ${rey+10} ${dkx-34} ${dky-14} ${dkx} ${dky}`+
   `L${g.x1-14} ${g.tail}`;
  const body=`M${g.x0+22} ${by}L${g.x0+6} ${by-14}Q${g.x0-5} ${g.nose+20} ${g.x0+26} ${g.nose}`+top+
   `Q${g.x1+5} ${g.tail+6} ${g.x1} ${g.tail+40}L${g.x1-6} ${by-26}Q${g.x1-9} ${by} ${g.x1-36} ${by}`+
   `L${g.rw+dx} ${by}A${ar} ${ar} 0 1 0 ${g.rw-dx} ${by}L${g.fw+dx} ${by}A${ar} ${ar} 0 1 0 ${g.fw-dx} ${by}Z`;
  const belt=cyy+(dky-cyy)*.12;
  const glass=`M${cx_+28} ${cyy-3}C${cx_+(rsx-cx_)*.55} ${cyy-(cyy-ry)*.7} ${rsx-30} ${ry+13} ${rsx+4} ${ry+12}`+
   `C${rsx+(rex-rsx)*.4} ${ry+8} ${rex-26} ${rey+9} ${rex-6} ${rey+12}`+
   `C${rex+(dkx-rex)*.45} ${rey+20} ${dkx-52} ${dky-12} ${dkx-36} ${dky-1}L${cx_+28} ${belt}Z`;
  const pil=rsx+(rex-rsx)*(car.body==='spider'?.75:.5)+(car.body==='wagon'?-70:car.body==='suv'?-20:10);
  const vy=Math.min(ry,rey)-(tz?60:26),vh=G+24-vy;
  const wheel=x=>{let sp='';for(let i=0;i<5;i++){const a=i*72-90,a1=(a-8)*Math.PI/180,a2=(a+8)*Math.PI/180,R=g.r*.64;
      sp+=`<path d="M${x} ${cy}L${x+Math.cos(a1)*R} ${cy+Math.sin(a1)*R}M${x} ${cy}L${x+Math.cos(a2)*R} ${cy+Math.sin(a2)*R}"/>`}
    return `<circle cx="${x}" cy="${cy}" r="${ar+1}" fill="#040405"/><circle cx="${x}" cy="${cy}" r="${g.r}" fill="#0c0c0e"/><circle cx="${x}" cy="${cy}" r="${g.r-2}" fill="none" stroke="#232428" stroke-width="2.5"/>
    <circle cx="${x}" cy="${cy}" r="${g.r*.7}" fill="#131417" stroke="${tz?'#3a3c42':'#5a5d64'}" stroke-width="2"/>
    <path d="M${x-g.r*.44} ${cy-g.r*.2}a${g.r*.5} ${g.r*.5} 0 0 1 ${g.r*.3} -${g.r*.26}" stroke="var(--accent)" stroke-width="${g.r*.13}" fill="none" stroke-linecap="round"/>
    <g stroke="${tz?'#6c6f76':'#c9ccd2'}" stroke-width="${g.r*.07}" stroke-linecap="round">${sp}</g><circle cx="${x}" cy="${cy}" r="${g.r*.12}" fill="${tz?'#6c6f76':'#c9ccd2'}"/>`};
  const defs=`<defs><linearGradient id="${id}b" x1="0" y1="0" x2="0" y2="1">${tz?`<stop offset="0" stop-color="${shade(hex,-.35)}"/><stop offset=".3" stop-color="${shade(hex,-.72)}"/><stop offset="1" stop-color="#050506"/>`:`<stop offset="0" stop-color="${shade(hex,.45)}"/><stop offset=".3" stop-color="${shade(hex,.12)}"/><stop offset=".6" stop-color="${hex}"/><stop offset="1" stop-color="${shade(hex,-.6)}"/>`}</linearGradient>
  <linearGradient id="${id}g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${tz?'#1a1e26':'#3a4250'}"/><stop offset=".5" stop-color="#0a0c10"/><stop offset="1" stop-color="${tz?'#0d0f14':'#1c2028'}"/></linearGradient>
  <linearGradient id="${id}r" gradientUnits="userSpaceOnUse" x1="${g.x0}" y1="0" x2="${g.x1}" y2="0"><stop offset="0" stop-color="#fff" stop-opacity=".15"/><stop offset=".3" stop-color="#fff"/><stop offset=".72" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity=".1"/></linearGradient>
  <radialGradient id="${id}s"><stop offset="0" stop-color="#000" stop-opacity=".9"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>
  <filter id="${id}f" x="-10%" y="-60%" width="120%" height="220%"><feGaussianBlur stdDeviation="7"/></filter></defs>`;
  const topP=`M${g.x0+26} ${g.nose}${top}`;
  return `<svg viewBox="0 ${vy} 1000 ${vh}" role="img" aria-label="${esc(o.label||carName(car))}" class="${o.cls||''}">${defs}
  <ellipse cx="${(g.x0+g.x1)/2}" cy="${G+3}" rx="${(g.x1-g.x0)*.55}" ry="15" fill="url(#${id}s)"/>
  ${wheel(g.fw)}${wheel(g.rw)}
  <path d="${body}" fill="url(#${id}b)" stroke="rgba(255,255,255,${tz?.06:.22})" stroke-width="1.5"/>
  <path d="${glass}" fill="url(#${id}g)" stroke="rgba(255,255,255,${tz?.08:.16})" stroke-width="1.2"/>
  <path d="M${pil} ${ry+12}L${pil+8} ${belt+(dky-cyy)*.3}" stroke="${shade(hex,tz?-.8:-.3)}" stroke-width="7"/>
  <path d="M${g.x0+44} ${g.nose+15}C${g.x0+(cx_-g.x0)*.5} ${g.nose-3} ${cx_} ${belt+14} ${cx_+60} ${belt+12}L${dkx-10} ${dky+16}L${g.x1-24} ${g.tail+14}" fill="none" stroke="rgba(255,255,255,${tz?.16:.34})" stroke-width="2"/>
  <path d="M${g.x0+10} ${by-10}l44 -2l14 -16l-52 4z" fill="rgba(0,0,0,.55)"/>
  <path d="M${g.fw+dx+12} ${by-12}L${g.rw-dx-12} ${by-12}" stroke="rgba(0,0,0,.5)" stroke-width="10"/>
  <path d="M${cx_+8} ${belt+8}L${cx_+2} ${by-30}M${pil+10} ${belt+16}L${pil+12} ${by-26}" stroke="rgba(0,0,0,.42)" stroke-width="1.6" fill="none"/>
  <ellipse cx="${cx_+40}" cy="${belt-3}" rx="15" ry="6" fill="${shade(hex,tz?-.7:-.25)}"/>
  ${tz?`<path d="${topP}" fill="none" stroke="url(#${id}r)" stroke-width="10" filter="url(#${id}f)" opacity=".75"/><path d="${topP}" fill="none" stroke="url(#${id}r)" stroke-width="2.2" stroke-linecap="round"/>`:''}
  <path d="M${g.x0+13} ${g.nose+23}l54 -10l-11 13z" fill="#f4f7ff"/>${tz?`<path d="M${g.x0+13} ${g.nose+23}l54 -10l-11 13z" fill="#fff" filter="url(#${id}f)"/>`:''}
  <path d="M${g.x1-5} ${g.tail+17}l-50 -3l6 11l42 4z" fill="#ff2a2a"/>${tz?`<path d="M${g.x1-5} ${g.tail+17}l-50 -3l6 11l42 4z" fill="#ff2a2a" filter="url(#${id}f)"/>`:''}
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
