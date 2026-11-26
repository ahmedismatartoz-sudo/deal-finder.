/* MC prototipo — router a hash, eventi, cookie, intro cinematografica 3D, visore 3D della scheda */
'use strict';
window.MC3D={intro:null,viewer:null,state:'idle',lastGL:null};
const REDUCED=!!(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches);
const isMobile=()=>innerWidth<820||!!(window.matchMedia&&matchMedia('(pointer: coarse)').matches);
function hasWebGL(){try{const c=document.createElement('canvas');const g=window.WebGLRenderingContext&&(c.getContext('webgl2')||c.getContext('webgl'));if(g){const l=g.getExtension('WEBGL_lose_context');l&&l.loseContext()}return !!g}catch(e){return false}}
let sceneMod=null;
const loadScene=()=>sceneMod||(sceneMod=import('./scene.js').catch(e=>{sceneMod=null;throw e}));
const withTimeout=(p,ms)=>Promise.race([p,new Promise((_,rej)=>setTimeout(()=>rej(new Error('timeout')),ms))]);

/* ---------- router ---------- */
let lastPath=null,routeSeq=0;
function parse(){const h=location.hash.replace(/^#/,'')||'/',[p,qs]=h.split('?'),q={};(qs||'').split('&').forEach(kv=>{const [k,v]=kv.split('=');if(k)try{q[k]=decodeURIComponent(v||'')}catch(e){q[k]=v||''}});return {path:p,parts:p.split('/').filter(Boolean),q}}
function disposeViewer(){if(MC3D.viewer){try{MC3D.viewer.dispose()}catch(e){}MC3D.viewer=null}}
function route(){const R=parse(),a=R.parts[0]||'',b=R.parts[1],seq=++routeSeq;disposeViewer();clearInterval(holdTick);
  let html,name=a||'home',after=null;
  if(!a){html=viewHome()}
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
  else if(a==='preferiti'){html=viewFavs()}
  else if(a==='admin'){html=viewAdmin(b);after=admAfter}
  else if(a==='account'){html=viewAccount(b)}
  else if(a==='progetto'){html=viewProject()}
  else{html=view404();name='404'}
  document.body.className='r-'+name+($('#agentPanel').hidden?'':' ag-open')+(document.getElementById('intro')?' intro-on':'');
  renderShell('/'+a);
  const v=$('#view');v.innerHTML=html;v.style.animation='none';void v.offsetWidth;v.style.animation='';
  const titles={home:'MC Milano | '+T('Noleggio e vendita auto speciali','Special car rental and sales'),noleggio:T('Noleggio','Rental'),vendita:T('Vendita','Sales'),admin:T('Area salone','Showroom area'),account:T('Area cliente','My account'),progetto:T('Il progetto','The project'),prenota:T('Prenotazione','Booking'),agenda:'Agenda',finanziamento:T('Finanziamento','Finance'),preventivo:T('Preventivo','Quote'),permuta:T('Permuta','Trade-in'),'lungo-termine':T('Lungo termine','Long-term'),'chi-siamo':T('Chi siamo','About'),preferiti:T('Preferiti','Favourites')};
  const c=b&&carBy(b);document.title=(c?carName(c):titles[name]||'MC')+(name==='home'?'':' | MC Milano')+' – '+T('Prototipo dimostrativo','Demo prototype');
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
document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;if(document.getElementById('intro')){introEnd();return}if(!$('#modal').hidden)closeModal();else if(!$('#agentPanel').hidden)ACT.agentToggle();else{$('#top').classList.remove('open');document.body.classList.remove('menu-open')}});

/* ---------- cookie: rifiutare è facile quanto accettare ---------- */
function cookieRender(){const c=$('#cookie'),v=store.get('cookie',null);if(v||document.getElementById('intro')){c.hidden=true;return}c.hidden=false;
  c.innerHTML=`<div><b>${T('Cookie','Cookies')}</b><br>${T('Usiamo solo cookie tecnici. Quelli statistici partono soltanto se accetti.','We only use technical cookies. Analytics ones start only if you accept.')}</div>
  <div class="row"><button class="btn btn-line btn-sm" data-act="cookie" data-v="no">${T('Rifiuta','Reject')}</button><button class="btn btn-line btn-sm" data-act="cookie" data-v="ok">${T('Accetta','Accept')}</button></div>`}
ACT.cookie=el=>{store.set('cookie',el.dataset.v);const cs=store.get('consents',{});cs.stat=el.dataset.v==='ok';store.set('consents',cs);$('#cookie').hidden=true};
ACT.cookieOpen=(el,e)=>{e.preventDefault();store.del('cookie');cookieRender()};
addEventListener('scroll',onScrollTop,{passive:true});

/* ================= INTRO CINEMATOGRAFICA =================
   3D procedurale (scene.js) se WebGL c'è e il modulo si carica; altrimenti sequenza 2D. Con «riduci movimento» una
   schermata ferma. «Salta» sempre disponibile; all'uscita il contesto WebGL viene liberato e il canvas rimosso. */
let introSeq=0,introTimer=0;
function introHtml(){return `<div class="intro" id="intro" role="dialog" aria-modal="true" aria-label="${T('Introduzione','Introduction')}">
  <canvas id="introCanvas" aria-hidden="true"></canvas>
  <div class="intro-fb" aria-hidden="true"><div class="ifb-strips">${'<i></i>'.repeat(5)}</div><div class="ifb-beam"></div><div class="ifb-floor"></div><div class="ifb-car">${carSVG(CARS[0],{label:''})}</div><div class="ifb-mirror">${carSVG(CARS[0],{label:''})}</div><div class="ifb-dust"></div></div>
  <div class="intro-vig" aria-hidden="true"></div>
  <div class="intro-cap c1"><p>${T('Milano · Via Arbe 95','Milan · Via Arbe 95')}</p></div>
  <div class="intro-cap c2"><p>${T('Ogni dettaglio, <em>alla luce giusta.</em>','Every detail, <em>in the right light.</em>')}</p></div>
  <div class="intro-cap c3"><p>${T('Noleggio · Vendita · <em>Finanziamento</em>','Rental · Sales · <em>Finance</em>')}</p></div>
  <div class="intro-title"><div class="it-mark"><span>MC</span><b></b></div><p>${T('Auto speciali a Milano','Special cars in Milan')}</p><button class="btn btn-primary" data-act="introEnd">${T('Entra nel salone','Enter the showroom')} ${IC.arrow}</button></div>
  <div class="intro-ui"><span class="intro-load" id="introLoad">${T('Preparazione della scena','Preparing the scene')}</span><div class="intro-bar"><i id="introBar"></i></div>
   <button class="intro-skip" data-act="introEnd">${T('Salta','Skip')} <span aria-hidden="true">→</span></button></div>
  <p class="intro-note">${T('Rendering generico generato nel browser · nessun modello o marchio reale','Generic render generated in the browser · no real model or brand')}</p></div>`}
function introStart(force){if(document.getElementById('intro'))return;const seq=++introSeq;
  document.body.insertAdjacentHTML('beforeend',introHtml());document.body.classList.add('intro-on');$('#cookie').hidden=true;
  const box=$('#intro'),bar=$('#introBar'),ld=$('#introLoad');setTimeout(()=>{const s=$('.intro-skip');s&&s.focus({preventScroll:true})},50);
  const cue=n=>{box.dataset.cue=n};
  const run2D=why=>{if(seq!==introSeq)return;MC3D.state=why;box.classList.add('fb');ld.textContent='';
    if(REDUCED){box.classList.add('still');cue(4);return}
    const t0=performance.now(),D=8000;box.classList.add('play');
    const tick=()=>{if(seq!==introSeq||!document.getElementById('intro'))return;const t=(performance.now()-t0)/1000;bar.style.transform=`scaleX(${Math.min(1,t*1000/D)})`;cue(t<.3?0:t<2.8?1:t<5?2:t<7?3:4);if(t*1000<D+5000)requestAnimationFrame(tick);else introEnd()};requestAnimationFrame(tick)};
  if(REDUCED){run2D('reduced');return}
  if(!hasWebGL()){run2D('fallback-nowebgl');return}
  const stageP=loadScene().then(m=>m.createStage($('#introCanvas'),{mode:'intro',mobile:isMobile(),accent:curAccent().c,paint:'#c8102e',body:'supercar',
    onCue:cue,onTick:(p,t)=>{bar.style.transform=`scaleX(${p})`;if(t>15.5&&!box.classList.contains('out'))introEnd()}}));
  withTimeout(stageP,12000).then(st=>{if(seq!==introSeq||!document.getElementById('intro')){st.dispose();return}
      MC3D.intro=st;MC3D.lastGL=st.gl;MC3D.state='live';box.classList.add('live');ld.textContent=''})
    .catch(e=>{console.info('[MC] intro 3D non disponibile, uso la versione 2D:',e&&e.message||e);stageP.then(st=>{if(MC3D.intro!==st)st.dispose()}).catch(()=>{});run2D('fallback')});
}
function introEnd(){const box=document.getElementById('intro');if(!box||box.classList.contains('out'))return;introSeq++;sess.set('intro','1');
  box.classList.add('out');
  // libera subito GPU e memoria: stop del ciclo, dispose di geometrie/materiali/target, perdita forzata del contesto
  if(MC3D.intro){try{MC3D.intro.dispose()}catch(e){}MC3D.intro=null}
  clearTimeout(introTimer);introTimer=setTimeout(()=>{const c=$('#introCanvas');if(c){c.width=c.height=1;c.remove()}box.remove();document.body.classList.remove('intro-on');cookieRender();const v=$('#view');v&&v.focus({preventScroll:true})},REDUCED?10:750)}
ACT.introEnd=introEnd;
ACT.introReplay=(el,e)=>{e&&e.preventDefault();if(location.hash&&location.hash!=='#/'){location.hash='#/'}introStart(true)};

/* ---------- visore 3D della scheda (leggero, niente post-processing) ---------- */
function viewerInit(c,seq){return; /* niente auto 3D: la scheda usa la galleria 2D */
  const box=$('#viewer');if(!box||!c)return;if(REDUCED||!hasWebGL())return;
  const stageP=loadScene().then(m=>m.createStage($('#viewerCanvas'),{mode:'viewer',mobile:isMobile(),theme:curTheme(),accent:curAccent().c,body:c.b3,paint:cfgHex(c),rim:DT.rim,fin:finishBy(DT.fin).hex[0],cal:caliperHex(DT.cal)}));
  withTimeout(stageP,15000)
   .then(st=>{if(seq!==routeSeq||!$('#viewer')){st.dispose();return}MC3D.viewer=st;box.classList.add('live');
     const tg=$('#viewerTag');if(tg)tg.textContent='3D · '+T('trascina per girare a 360°','drag to turn 360°')})
   .catch(e=>{console.info('[MC] visore 3D non disponibile, resta la vista 2D:',e&&e.message||e);stageP.then(st=>{if(MC3D.viewer!==st)st.dispose()}).catch(()=>{})});
}
window.addEventListener('mc:accent',e=>{['intro','viewer'].forEach(k=>MC3D[k]&&MC3D[k].setAccent(e.detail.c))});

/* ---------- date: accanto al campo nativo, la data scritta per esteso ---------- */
const dfm=v=>{const d=fromIso(v);return d?new Intl.DateTimeFormat(loc(),{weekday:'short',day:'2-digit',month:'2-digit',year:'numeric'}).format(d):''};
function decorateDates(root){$$('input[type=date]',root||document).forEach(i=>{let n=i.nextElementSibling;if(!n||!n.classList.contains('dfmt')){n=document.createElement('small');n.className='dfmt';i.after(n)}n.textContent=dfm(i.value)})}
new MutationObserver(()=>{clearTimeout(decorateDates.t);decorateDates.t=setTimeout(()=>decorateDates(),30)}).observe(document.getElementById('view'),{childList:true,subtree:true});
document.addEventListener('change',e=>{if(e.target.type==='date')setTimeout(()=>decorateDates(),0)});

/* ---------- avvio ---------- */
applyTheme(curTheme());applyAccent(store.get('accent','rosso'));
route();
/* intro a tempo sull'auto rimossa su richiesta del cliente */
