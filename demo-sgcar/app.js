/* MC prototipo — router a hash, eventi, cookie, aggancio della scena 3D */
'use strict';
window.MC3D={hero:null,viewer:null,state:'idle'};
const REDUCED=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
const isMobile=()=>innerWidth<820||(window.matchMedia&&matchMedia('(pointer: coarse)').matches);
function hasWebGL(){try{const c=document.createElement('canvas');return !!(window.WebGLRenderingContext&&(c.getContext('webgl2')||c.getContext('webgl')))}catch(e){return false}}
let sceneMod=null;
const loadScene=()=>sceneMod||(sceneMod=import('./scene.js').catch(e=>{sceneMod=null;throw e}));
const withTimeout=(p,ms)=>Promise.race([p,new Promise((_,rej)=>setTimeout(()=>rej(new Error('timeout')),ms))]);

/* ---------- router ---------- */
let lastPath=null,routeSeq=0;
function parse(){const h=location.hash.replace(/^#/,'')||'/',[p,qs]=h.split('?'),q={};(qs||'').split('&').forEach(kv=>{const [k,v]=kv.split('=');if(k)q[k]=decodeURIComponent(v||'')});return {path:p,parts:p.split('/').filter(Boolean),q}}
function dispose3D(){['hero','viewer'].forEach(k=>{if(MC3D[k]){try{MC3D[k].dispose()}catch(e){}MC3D[k]=null}})}
function route(){const R=parse(),a=R.parts[0]||'',b=R.parts[1],seq=++routeSeq;dispose3D();clearInterval(holdTick);
  let html,name=a||'home',after=null;
  if(!a){html=viewHome();after=heroInit}
  else if(a==='noleggio'){html=viewRent();after=()=>rentRefresh()}
  else if(a==='auto'&&b){html=viewCar(b);after=()=>viewerInit(carBy(b),seq)}
  else if(a==='prenota'&&b){html=viewBook(b,R.q);after=()=>bkPaint()}
  else if(a==='vendita'&&!b){html=viewSale();after=()=>saleRefresh()}
  else if(a==='vendita'){html=viewSaleCar(b);after=()=>viewerInit(carBy(b),seq)}
  else if(a==='admin'){html=viewAdmin(b);after=admAfter}
  else if(a==='account'){html=viewAccount(b)}
  else if(a==='progetto'){html=viewProject()}
  else{html=view404();name='404'}
  document.body.className='r-'+name+($('#agentPanel').hidden?'':' ag-open');
  renderShell('/'+a);
  const v=$('#view');v.innerHTML=html;v.style.animation='none';void v.offsetWidth;v.style.animation='';
  const titles={home:'MC Milano | '+T('Noleggio e vendita auto speciali','Special car rental and sales'),noleggio:T('Noleggio','Rental')+' | MC Milano',vendita:T('Vendita','Sales')+' | MC Milano',admin:'Dashboard | MC Milano',account:T('Area cliente','My account')+' | MC Milano',progetto:T('Il progetto','The project')+' | MC Milano',prenota:T('Prenotazione','Booking')+' | MC Milano'};
  const c=b&&carBy(b);document.title=(c?carName(c)+' | MC Milano':titles[name]||'MC Milano')+' – '+T('Prototipo dimostrativo','Demo prototype');
  const base=R.parts.slice(0,a==='admin'||a==='account'?1:2).join('/');
  if(base!==lastPath){scrollTo(0,0);lastPath=base}
  renderTray();after&&after();observeReveal();onScrollTop();
  if(!$('#agentPanel').hidden)agentRender();cookieRender();
}
addEventListener('hashchange',route);

/* ---------- eventi delegati ---------- */
document.addEventListener('click',e=>{
  const el=e.target.closest('[data-act]');
  if(el){const f=ACT[el.dataset.act];if(f){if(el.tagName==='BUTTON')e.preventDefault();f(el,e)}}
  if(e.target.id==='modal')closeModal();
  if(e.target.closest('#nav a')){$('#top').classList.remove('open')}
});
const isChg=el=>el.tagName==='SELECT'||['checkbox','radio','file','date','time'].includes(el.type);
document.addEventListener('change',e=>{const el=e.target.closest('[data-chg]');if(el&&isChg(el)&&CHG[el.dataset.chg])CHG[el.dataset.chg](el,e)});
document.addEventListener('input',e=>{const el=e.target.closest('[data-chg]');if(el&&!isChg(el)&&CHG[el.dataset.chg])CHG[el.dataset.chg](el,e)});
document.addEventListener('submit',e=>{const f=e.target.closest('form[data-form]');if(!f)return;e.preventDefault();const fn=FORM[f.dataset.form];if(fn){fn(f,e);if(f.dataset.form==='agent')f.reset()}});
document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;if(!$('#modal').hidden)closeModal();else if(!$('#agentPanel').hidden)ACT.agentToggle();else $('#top').classList.remove('open')});

/* ---------- cookie: rifiutare è facile quanto accettare ---------- */
function cookieRender(){const c=$('#cookie'),v=store.get('cookie',null);if(v){c.hidden=true;return}c.hidden=false;
  c.innerHTML=`<div><b>${T('Cookie','Cookies')}</b><br>${T('Usiamo solo cookie tecnici. Quelli statistici partono soltanto se accetti. Puoi cambiare idea dal fondo pagina.','We only use technical cookies. Analytics ones start only if you accept. You can change your mind from the footer.')}</div>
  <div class="row"><button class="btn btn-line btn-sm" data-act="cookie" data-v="no">${T('Rifiuta','Reject')}</button><button class="btn btn-line btn-sm" data-act="cookie" data-v="ok">${T('Accetta','Accept')}</button></div>`}
ACT.cookie=el=>{store.set('cookie',el.dataset.v);const cs=store.get('consents',{});cs.stat=el.dataset.v==='ok';store.set('consents',cs);$('#cookie').hidden=true};
ACT.cookieOpen=(el,e)=>{e.preventDefault();store.del('cookie');cookieRender()};

/* ---------- hero ---------- */
function heroProgress(){const h=$('#hero');if(!h)return 0;const r=h.getBoundingClientRect(),d=r.height-innerHeight;return d>10?Math.min(1,Math.max(0,-r.top/d)):0}
function heroScroll(){const h=$('#hero');if(!h)return;const p=heroProgress(),copy=$('#heroCopy'),cap=$('#heroCap'),bar=$('#heroProg'),meta=$('#heroMeta');
  if(MC3D.hero)MC3D.hero.setScroll(p);
  if(h.classList.contains('static'))return;
  const o=Math.max(0,1-p*3.2);copy.style.opacity=o;copy.style.transform=`translateY(${-p*60}px)`;copy.style.pointerEvents=o<.3?'none':'';
  if(meta)meta.style.opacity=o;
  const k=Math.max(0,Math.min(1,(p-.28)/.12))*(1-Math.max(0,Math.min(1,(p-.9)/.1)));cap.style.opacity=k;cap.style.transform=`translateY(${(1-k)*30}px)`;
  if(bar)bar.style.transform=`scaleY(${p})`;
  if(!h.classList.contains('live')){const cw=$('.hero-fb .carwrap'),mi=$('.hero-fb .mirror');if(cw&&(p>0||cw.dataset.m)){cw.dataset.m=1;const tx=`scale(${1+p*.06})`;cw.style.animation=mi.style.animation='none';cw.style.transformOrigin='70% 92%';mi.style.transformOrigin='70% 8%';cw.style.transform=tx;mi.style.transform=tx+' scaleY(-1)'}}
}
function onScroll(){onScrollTop();heroScroll()}
addEventListener('scroll',onScroll,{passive:true});addEventListener('resize',heroScroll);
function heroInit(){const h=$('#hero'),load=$('#heroLoad');if(!h)return;
  const fail=()=>{MC3D.state='fallback';load&&load.classList.add('done');h.classList.add('nofx','static');['#heroCopy','#heroMeta'].forEach(q=>{const e=$(q);if(e){e.style.opacity='';e.style.transform='';e.style.pointerEvents=''}});const c=$('#heroCap');if(c)c.style.opacity=0};
  if(REDUCED){h.classList.add('static');load.classList.add('done');MC3D.state='reduced';return}
  if(!hasWebGL()){fail();return}
  const seq=routeSeq,bar=$('i',load);
  const stageP=loadScene().then(m=>m.createStage($('#heroCanvas'),{mode:'hero',mobile:isMobile(),accent:curAccent().c,paint:PAINTS[CARS[0].paint].hex,onProgress:p=>bar&&bar.style.setProperty('--p',p)}));
  withTimeout(stageP,30000)
   .then(st=>{if(seq!==routeSeq||!$('#hero')){st.dispose();return}MC3D.hero=st;MC3D.state='live';h.classList.add('live');load.classList.add('done');heroScroll()})
   .catch(e=>{console.info('[MC] 3D non disponibile, resta il fallback:',e&&e.message||e);if(seq===routeSeq)fail();stageP.then(st=>{if(MC3D.hero!==st)st.dispose()}).catch(()=>{})});
  heroScroll();
}
window.addEventListener('mc:accent',e=>{['hero','viewer'].forEach(k=>MC3D[k]&&MC3D[k].setAccent(e.detail.c))});

/* ---------- configuratore nella scheda ---------- */
function viewerInit(c,seq){const box=$('#viewer');if(!box||!c)return;if(REDUCED||!hasWebGL())return;
  const stageP=loadScene().then(m=>m.createStage($('#viewerCanvas'),{mode:'viewer',mobile:isMobile(),accent:curAccent().c,paint:DT.hex||PAINTS[c.paint].hex}));
  withTimeout(stageP,30000)
   .then(st=>{if(seq!==routeSeq||!$('#viewer')){st.dispose();return}MC3D.viewer=st;box.classList.add('live');
     $('#viewerTag').textContent=T('3D · trascina per ruotare','3D · drag to rotate');
     const shots=st.snapshots(3),th=$$('#thumbs>*');shots.forEach((u,i)=>{if(th[i]&&u&&u.length>200){const d=document.createElement('div');d.innerHTML=`<img src="${u}" alt="${esc(carName(c))} – ${T('render 3D dimostrativo','demo 3D render')} ${i+1}" loading="lazy">`;d.style.padding='0';th[i].replaceWith(d)}})})
   .catch(e=>{console.info('[MC] configuratore 3D non disponibile:',e&&e.message||e);stageP.then(st=>{if(MC3D.viewer!==st)st.dispose()}).catch(()=>{})});
}

/* ---------- date: accanto al campo nativo, la data scritta all'italiana ---------- */
const dfm=v=>{const d=fromIso(v);return d?new Intl.DateTimeFormat(loc(),{weekday:'short',day:'2-digit',month:'2-digit',year:'numeric'}).format(d):''};
function decorateDates(root){$$('input[type=date]',root||document).forEach(i=>{let n=i.nextElementSibling;if(!n||!n.classList.contains('dfmt')){n=document.createElement('small');n.className='dfmt';i.after(n)}n.textContent=dfm(i.value)})}
new MutationObserver(()=>{clearTimeout(decorateDates.t);decorateDates.t=setTimeout(()=>decorateDates(),30)}).observe(document.getElementById('view'),{childList:true,subtree:true});
document.addEventListener('change',e=>{if(e.target.type==='date')setTimeout(()=>decorateDates(),0)});
/* ---------- avvio ---------- */
applyAccent(store.get('accent','rosso'));
route();
