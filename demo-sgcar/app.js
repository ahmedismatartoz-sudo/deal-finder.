/* MC prototipo — router a hash, eventi, cookie, hero guidato dallo scroll */
'use strict';
window.MC3D={intro:null,viewer:null,state:'idle',lastGL:null};
const REDUCED=!!(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches);
const isMobile=()=>innerWidth<820||!!(window.matchMedia&&matchMedia('(pointer: coarse)').matches);
function hasWebGL(){try{const c=document.createElement('canvas');const g=window.WebGLRenderingContext&&(c.getContext('webgl2')||c.getContext('webgl'));if(g){const l=g.getExtension('WEBGL_lose_context');l&&l.loseContext()}return !!g}catch(e){return false}}
const withTimeout=(p,ms)=>Promise.race([p,new Promise((_,rej)=>setTimeout(()=>rej(new Error('timeout')),ms))]);

/* ---------- router ---------- */
let lastPath=null,routeSeq=0;
function parse(){const h=location.hash.replace(/^#/,'')||'/',[p,qs]=h.split('?'),q={};(qs||'').split('&').forEach(kv=>{const [k,v]=kv.split('=');if(k)try{q[k]=decodeURIComponent(v||'')}catch(e){q[k]=v||''}});return {path:p,parts:p.split('/').filter(Boolean),q}}
function disposeViewer(){['viewer','story'].forEach(k=>{if(MC3D[k]){try{MC3D[k].dispose()}catch(e){}MC3D[k]=null}})}
function route(){const R=parse(),a=R.parts[0]||'',b=R.parts[1],seq=++routeSeq;disposeViewer();clearInterval(holdTick);
  let html,name=a||'home',after=null;
  if(!a){html=viewHome();after=heroScroll}
  else if(a==='noleggio'){html=viewRent();after=()=>rentRefresh()}
  else if(a==='auto'&&b){html=viewCar(b,R.q);after=()=>viewerInit(carBy(b),seq)}
  else if(a==='prenota'&&b){html=viewBook(b,R.q);after=()=>bkPaint()}
  else if(a==='vendita'&&!b){html=viewSale();after=()=>saleRefresh()}
  else if(a==='vendita'){html=viewSaleCar(b,R.q);after=()=>viewerInit(carBy(b),seq)}
  else if(a==='agenda'){html=viewAgenda(R.q)}
  else if(a==='finanziamento'){html=viewFinance(R.q)}
  else if(a==='preventivo'){html=viewQuote(R.q)}
  else if(a==='permuta'){html=viewTrade(R.q)}
  else if(a==='lungo-termine'){html=viewLong()}
  else if(a==='chi-siamo'){html=viewAbout()}
  else if(a==='preferiti'){html=viewWish(R.q)}
  else if(a==='noleggio-o-acquisto'){html=viewRentVsBuy(R.q)}
  else if(a==='eventi'){html=viewEvents()}
  else if(a==='consegna'){html=viewDelivery()}
  else if(a==='documenti'){html=viewDocs(R.q)}
  else if(a==='crediti-foto'){html=viewCredits()}
  else if(a==='admin'){html=viewAdmin(b);after=admAfter}
  else if(a==='account'){html=viewAccount(b)}
  else if(a==='progetto'){html=viewProject()}
  else{html=view404();name='404'}
  document.body.className='r-'+name+($('#agentPanel').hidden?'':' ag-open');
  renderShell('/'+a);
  const v=$('#view');v.innerHTML=html;v.style.animation='none';void v.offsetWidth;v.style.animation='';
  const titles={home:'MC Milano | '+T('Noleggio e vendita auto speciali','Special car rental and sales'),noleggio:T('Noleggio','Rental'),vendita:T('Vendita','Sales'),admin:T('Area salone','Showroom area'),account:T('Area cliente','My account'),progetto:T('Il progetto','The project'),prenota:T('Prenotazione','Booking'),agenda:'Agenda',finanziamento:T('Finanziamento','Finance'),preventivo:T('Preventivo','Quote'),permuta:T('Permuta','Trade-in'),'lungo-termine':T('Lungo termine','Long-term'),'chi-siamo':T('Chi siamo','About'),preferiti:T('Preferiti','Favourites')};
  const c=b&&carBy(b);document.title=(c?carName(c):titles[name]||'MC')+(name==='home'?'':' | MC Milano')+' – '+T('Prototipo dimostrativo','Demo prototype');
  applySeo({'':'home',noleggio:'noleggio',vendita:b?'':'vendita',finanziamento:'finanziamento',agenda:'agenda','chi-siamo':'chi_siamo'}[a]||'',a==='auto'&&b?b:null);
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
  if(e.target.closest('#nav a')){$('#top').classList.remove('open');document.body.classList.remove('menu-open')}
});
const isChg=el=>el.tagName==='SELECT'||['checkbox','radio','file','date','time'].includes(el.type);
document.addEventListener('change',e=>{const el=e.target.closest('[data-chg]');if(el&&isChg(el)&&CHG[el.dataset.chg])CHG[el.dataset.chg](el,e)});
document.addEventListener('input',e=>{const el=e.target.closest('[data-chg]');if(el&&!isChg(el)&&CHG[el.dataset.chg])CHG[el.dataset.chg](el,e)});
document.addEventListener('submit',e=>{const f=e.target.closest('form[data-form]');if(!f)return;e.preventDefault();const fn=FORM[f.dataset.form];if(fn){fn(f,e);if(f.dataset.form==='agent')f.reset()}});
document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;if(!$('#modal').hidden)closeModal();else if(!$('#agentPanel').hidden)ACT.agentToggle();else{$('#top').classList.remove('open');document.body.classList.remove('menu-open')}});

/* ---------- cookie: rifiutare è facile quanto accettare ---------- */
function cookieRender(){const c=$('#cookie'),v=store.get('cookie',null);if(v){c.hidden=true;return}c.hidden=false;
  c.innerHTML=`<div><b>${T('Cookie','Cookies')}</b><br>${T('Usiamo solo cookie tecnici. Quelli statistici partono soltanto se accetti.','We only use technical cookies. Analytics ones start only if you accept.')}</div>
  <div class="row"><button class="btn btn-line btn-sm" data-act="cookie" data-v="no">${T('Rifiuta','Reject')}</button><button class="btn btn-line btn-sm" data-act="cookie" data-v="ok">${T('Accetta','Accept')}</button></div>`}
ACT.cookie=el=>{store.set('cookie',el.dataset.v);const cs=store.get('consents',{});cs.stat=el.dataset.v==='ok';store.set('consents',cs);$('#cookie').hidden=true};
ACT.cookieOpen=(el,e)=>{e.preventDefault();store.del('cookie');cookieRender()};
addEventListener('scroll',onScrollTop,{passive:true});

/* ================= HERO GUIDATO DALLO SCROLL =================
   La foto parte come riquadro arrotondato e, scorrendo, si allarga a tutta pagina mentre il titolo sale e sfuma;
   risalendo torna indietro. Solo trasformazioni CSS (GPU), niente WebGL. Con «riduci movimento» resta ferma. */
function heroP(){const s=$('#hero');if(!s)return 0;const r=s.getBoundingClientRect(),d=r.height-innerHeight;return d>10?Math.min(1,Math.max(0,-r.top/d)):0}
let heroRaf=0;
function heroScroll(){heroRaf=0;const s=$('#hero');if(!s)return;const p=REDUCED?0:heroP();s.style.setProperty('--p',p.toFixed(4))}
addEventListener('scroll',()=>{if(!heroRaf&&$('#hero'))heroRaf=requestAnimationFrame(heroScroll)},{passive:true});addEventListener('resize',()=>heroScroll());

function viewerInit(){} /* niente 3D nella scheda: galleria di foto e disegni */

/* ---------- date: accanto al campo nativo, la data scritta per esteso ---------- */
const dfm=v=>{const d=fromIso(v);return d?new Intl.DateTimeFormat(loc(),{weekday:'short',day:'2-digit',month:'2-digit',year:'numeric'}).format(d):''};
function decorateDates(root){$$('input[type=date]',root||document).forEach(i=>{let n=i.nextElementSibling;if(!n||!n.classList.contains('dfmt')){n=document.createElement('small');n.className='dfmt';i.after(n)}n.textContent=dfm(i.value)})}
new MutationObserver(()=>{clearTimeout(decorateDates.t);decorateDates.t=setTimeout(()=>decorateDates(),30)}).observe(document.getElementById('view'),{childList:true,subtree:true});
document.addEventListener('change',e=>{if(e.target.type==='date')setTimeout(()=>decorateDates(),0)});

/* ---------- avvio ---------- */
applyTheme(curTheme());applyAccent(store.get('accent','rosso'));
route();
/* intro a tempo sull'auto rimossa su richiesta del cliente */
