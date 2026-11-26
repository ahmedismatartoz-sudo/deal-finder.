/* MC prototipo — guscio, componenti, home, catalogo noleggio, scheda auto (configuratore, 360°, tariffe) */
'use strict';
const $=(s,r)=>(r||document).querySelector(s),$$=(s,r)=>[...(r||document).querySelectorAll(s)];
const ACT={},CHG={},FORM={};
const IC={
  heart:'<svg viewBox="0 0 24 24"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/></svg>',
  cmp:'<svg viewBox="0 0 24 24"><path d="M8 4v16M16 4v16M3 8h5M16 16h5M3 16h5M16 8h5"/></svg>',
  arrow:'<svg class="arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 12h16M14 6l6 6-6 6"/></svg>',
  wa:'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2zm0 18.2a8.2 8.2 0 0 1-4.2-1.15l-.3-.18-2.97.78.79-2.9-.2-.3A8.2 8.2 0 1 1 12 20.2zm4.5-6.15c-.25-.12-1.46-.72-1.69-.8-.23-.08-.39-.12-.56.12-.16.25-.64.8-.78.97-.14.16-.29.18-.54.06-.25-.12-1.04-.38-1.98-1.22-.73-.65-1.23-1.46-1.37-1.7-.14-.25-.02-.38.11-.5.11-.11.25-.29.37-.43.12-.14.16-.25.25-.41.08-.16.04-.31-.02-.43-.06-.12-.56-1.34-.76-1.84-.2-.48-.41-.42-.56-.42h-.48c-.16 0-.43.06-.66.31-.23.25-.86.84-.86 2.05 0 1.21.88 2.38 1 2.54.12.16 1.73 2.65 4.2 3.71.59.25 1.04.4 1.4.52.59.19 1.12.16 1.54.1.47-.07 1.46-.6 1.66-1.17.2-.58.2-1.07.14-1.17-.06-.11-.22-.17-.47-.29z"/></svg>',
  send:'<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12l16-8-6 16-3-7z"/></svg>',
  phone:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/></svg>',
  cal:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>',
  sun:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  moon:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
  share:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.2 10.8l7.6-3.6M8.2 13.2l7.6 3.6"/></svg>',
  pin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M12 21s-7-6.2-7-12a7 7 0 0 1 14 0c0 5.8-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/></svg>',
  play:'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>',
  chat:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z"/></svg>'
};
const waBtn=(cls,txt,text)=>`<a class="btn ${cls||'btn-line'}" href="${text?waHref(text):BIZ.wa}" target="_blank" rel="noopener">${IC.wa}<span>${txt||T('Scrivici su WhatsApp','Message us on WhatsApp')}</span></a>`;
const demoTag=()=>`<span class="demo-tag">${T('esempio','example')}</span>`;
const prod=t=>`<p class="prod">${T('In produzione: ','In production: ')}${t}</p>`;
const secHead=(eb,h,lead,right)=>`<div class="sec-head"><div><p class="eyebrow">${eb}</p><h2>${h}</h2>${lead?`<p class="lead">${lead}</p>`:''}</div>${right||''}</div>`;

/* ---------- toast e modale ---------- */
function toast(m){const t=document.createElement('div');t.className='toast';t.setAttribute('role','status');t.textContent=m;$('#toasts').appendChild(t);setTimeout(()=>{t.classList.add('out');setTimeout(()=>t.remove(),320)},3200)}
let lastFocus=null;
function openModal(html,narrow){lastFocus=document.activeElement;const m=$('#modal');m.innerHTML=`<div class="sheet ${narrow?'narrow':''}" role="dialog" aria-modal="true">${html}</div>`;m.hidden=false;document.body.style.overflow='hidden';const f=$('.x',m);f&&f.focus()}
function closeModal(){const m=$('#modal');if(m.hidden)return;m.hidden=true;m.innerHTML='';document.body.style.overflow='';lastFocus&&lastFocus.focus&&lastFocus.focus()}
ACT.closeModal=closeModal;

/* ---------- ultimo passo comune: notifica WhatsApp al salone (mai automatica, vedi waText in data.js) ---------- */
function waFinal(kind,rec,key){const txt=waText(kind,rec),opened=rec.waAt;
  return `<section class="wa-final" aria-labelledby="waT-${esc(rec.code)}"><div class="wa-head"><span class="wa-ico">${IC.wa}</span><div><p class="eyebrow">${T('Ultimo passo','Final step')}</p><h3 id="waT-${esc(rec.code)}">${T('Avvisa il salone su WhatsApp','Notify the showroom on WhatsApp')}</h3></div></div>
   <p class="small muted">${T('Premendo il pulsante si apre WhatsApp con il messaggio già scritto qui sotto: per mandarlo devi premere «Invia» nella tua app. Il sito non invia nulla in automatico.','The button opens WhatsApp with the message below already written: to send it you must press "Send" in your app. The site sends nothing automatically.')}</p>
   <pre class="wa-preview" aria-label="${T('Anteprima del messaggio','Message preview')}">${esc(txt)}</pre>
   <a class="btn btn-wa btn-block" href="${waHref(txt)}" target="_blank" rel="noopener" data-act="waOpen" data-k="${key}" data-code="${esc(rec.code)}">${IC.wa}<span>${T('Apri WhatsApp con il messaggio','Open WhatsApp with the message')}</span></a>
   <p class="xs dim wa-state" data-wa="${esc(rec.code)}">${opened?T('WhatsApp aperto il ','WhatsApp opened on ')+fmtD(opened,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})+T(': se non hai premuto «Invia», il messaggio non è partito.',': if you did not press "Send", the message was not sent.'):T('Se WhatsApp non si apre, chiama il ','If WhatsApp does not open, call ')+`<a class="link xs" href="${BIZ.telHref}">${BIZ.tel}</a>.`}</p></section>`}
ACT.waOpen=el=>{const k=el.dataset.k,c=el.dataset.code;if(k){const r=patchRec(k,c,{waAt:Date.now()});}
  if(k==='leads'){const L=leads(),x=L.find(l=>l.code===c);if(x){x.waAt=Date.now();store.set('leads',L)}}
  const s=$(`[data-wa="${CSS.escape?CSS.escape(c):c}"]`);if(s)s.textContent=T('WhatsApp aperto: per inviare il messaggio premi «Invia» nell\'app.','WhatsApp opened: press "Send" in the app to deliver the message.')};

/* ---------- guscio ---------- */
const HOURS=()=>[[T('Lunedì – Venerdì','Monday – Friday'),'9:30 – 18:30'],[T('Sabato','Saturday'),'10:30 – 17:30'],[T('Domenica','Sunday'),T('Su appuntamento','By appointment')]];
const NAV=()=>[['#/noleggio',T('Noleggio','Rental')],['#/vendita',T('Vendita','Sales')],['#/finanziamento',T('Finanziamento','Finance')],['#/agenda',T('Agenda','Book a visit')],['#/chi-siamo',T('Chi siamo','About')]];
function renderShell(route){
  document.documentElement.lang=LANG;const th=curTheme(),nf=favs().length;
  $('#top').innerHTML=`<div class="wrap top-in"><a class="logo" href="#/" aria-label="MC home"><span>MC</span><b></b><small>Milano<br>${T('Auto speciali','Special cars')}</small></a>
   <nav class="nav" id="nav" aria-label="${T('Principale','Main')}">${NAV().map(n=>`<a href="${n[0]}" class="${route.indexOf(n[0].slice(1))===0?'on':''}">${n[1]}</a>`).join('')}<a href="#contatti" data-act="goContact">${T('Contatti','Contact')}</a>
    <div class="nav-extra"><a href="#/preventivo">${T('Preventivo','Quote')}</a><a href="#/permuta">${T('Permuta','Trade-in')}</a><a href="#/lungo-termine">${T('Lungo termine','Long-term')}</a><a href="#/admin">${T('Area salone (demo)','Showroom area (demo)')}</a></div></nav>
   <div class="tools"><a class="tool fav-link" href="#/preferiti" aria-label="${T('Preferiti','Favourites')} (${nf})">${IC.heart}<span class="cnt" ${nf?'':'hidden'}>${nf}</span></a>
    <div class="lang" role="group" aria-label="${T('Lingua','Language')}"><button data-act="lang" data-l="it" aria-pressed="${LANG==='it'}">IT</button><button data-act="lang" data-l="en" aria-pressed="${LANG==='en'}">EN</button></div>
    <a class="btn btn-primary btn-sm top-cta" href="#/agenda">${T('Prenota una visita','Book a visit')}</a>
    <button class="burger" data-act="menu" aria-label="Menu" aria-expanded="false" aria-controls="nav"><span></span></button></div></div>`;
  $('#foot').innerHTML=`<div class="wrap"><div class="foot-hero"><a class="logo big" href="#/"><span>MC</span><b></b></a><p class="foot-claim">${T('Auto speciali a Milano.<br><em>Noleggio, vendita, immagine.</em>','Special cars in Milan.<br><em>Rental, sales, image.</em>')}</p></div>
   <div class="foot-grid">
   <div><h4>${T('Collezione','Collection')}</h4><ul><li><a href="#/noleggio">${T('Noleggio breve','Short-term rental')}</a></li><li><a href="#/lungo-termine">${T('Noleggio a lungo termine','Long-term rental')}</a></li><li><a href="#/vendita">${T('Auto in vendita','Cars for sale')}</a></li><li><a href="#/preferiti">${T('Preferiti','Favourites')}</a></li></ul></div>
   <div><h4>${T('Servizi','Services')}</h4><ul><li><a href="#/finanziamento">${T('Calcolo rata','Payment calculator')}</a></li><li><a href="#/preventivo">${T('Richiedi un preventivo','Request a quote')}</a></li><li><a href="#/permuta">${T('Valutazione permuta','Trade-in valuation')}</a></li><li><a href="#/agenda">${T('Test drive e visite','Test drives and visits')}</a></li><li><button class="linkish" data-act="callback">${T('Ti richiamiamo noi','We call you back')}</button></li></ul></div>
   <div id="contatti"><h4>${T('Contatti','Contact')}</h4><ul><li>${BIZ.addr}<br>${BIZ.cap}</li><li><a href="${BIZ.telHref}">${BIZ.tel}</a></li><li><a href="${BIZ.wa}" target="_blank" rel="noopener">WhatsApp</a></li><li><a href="${BIZ.dir}" target="_blank" rel="noopener">${T('Indicazioni stradali','Get directions')} ↗</a></li></ul></div>
   <div><h4>${T('Orari','Opening hours')} <span class="demo-tag">${T('da confermare','to be confirmed')}</span></h4><ul class="hours">${HOURS().map(h=>`<li><span>${h[0]}</span><span>${h[1]}</span></li>`).join('')}</ul>
    <h4 style="margin-top:24px">${T('Prototipo','Prototype')}</h4><ul><li><a href="#/chi-siamo">${T('Chi siamo','About us')}</a></li><li><a href="#/admin">${T('Area salone (demo)','Showroom area (demo)')}</a></li><li><a href="#/account">${T('Area cliente (demo)','Client area (demo)')}</a></li><li><a href="#/progetto">${T('Il progetto','The project')}</a></li><li><a href="#/" data-act="cookieOpen">${T('Preferenze cookie','Cookie preferences')}</a></li></ul></div>
   </div><div class="foot-bot"><span class="proto-note">${T('Prototipo dimostrativo · prezzi, disponibilità e dati sono di esempio.','Demo prototype · prices, availability and data are samples.')}</span><span>© ${new Date().getFullYear()} MC · ${BIZ.addr}, ${BIZ.cap}</span><span>${T('Prototipo dimostrativo: prezzi, disponibilità, rate, recensioni e prenotazioni sono dati di esempio. Pagamenti, firma e documenti sono simulati.','Demo prototype: prices, availability, instalments, reviews and bookings are sample data. Payments, signature and documents are simulated.')}</span><span>${T('Illustrazioni e rendering 3D generici, generati nel browser: nessun logo, marchio o design di case automobilistiche. I nomi dei modelli indicano solo il parco auto.','Generic illustrations and 3D renders generated in the browser: no logos, trademarks or car-maker designs. Model names only identify the fleet.')}</span></div></div>`;
  $('#dock').innerHTML=`<a href="${BIZ.telHref}">${IC.phone}<span>${T('Chiama','Call')}</span></a><a href="${BIZ.wa}" target="_blank" rel="noopener">${IC.wa}<span>WhatsApp</span></a><a href="#/agenda">${IC.cal}<span>${T('Prenota','Book')}</span></a><button data-act="agentToggle">${IC.chat}<span>${T('Assistente','Assistant')}</span></button>`;
  $('#agentFab').innerHTML=`<i></i><span>${T('Assistenti','Assistants')}</span>`;$('#agentFab').setAttribute('aria-label',T('Apri gli assistenti AI (demo)','Open AI assistants (demo)'));
  onScrollTop();
}
function onScrollTop(){const t=$('#top');if(t)t.classList.toggle('solid',scrollY>30||!document.body.classList.contains('r-home'))}
ACT.menu=el=>{const o=$('#top').classList.toggle('open');el.setAttribute('aria-expanded',o);document.body.classList.toggle('menu-open',o)};
ACT.goContact=(el,e)=>{e.preventDefault();$('#top').classList.remove('open');document.body.classList.remove('menu-open');const t=$('#contatti');t&&scrollTo({top:t.getBoundingClientRect().top+scrollY-120,behavior:'smooth'})};
ACT.lang=el=>{LANG=el.dataset.l;store.set('lang',LANG);route()};
ACT.theme=()=>{applyTheme(curTheme()==='dark'?'light':'dark');route()};

/* ---------- disponibilità in tempo reale (simulata: legge flotta e prenotazioni, anche da altre schede) ---------- */
function liveState(c){const st=fleetState()[c.id];if(st==='maint')return {cls:'bad',t:T('In manutenzione','In maintenance')};
  const now=overlaps(c.id,iso(TODAY),iso(TODAY));if(!now)return {cls:'ok',t:T('Disponibile ora','Available now')};
  const n=nextFree(c);return {cls:'warn',t:n?T('Noleggiata · libera dal ','On rent · free from ')+fmtDs(n):T('Non disponibile','Unavailable')}}
const liveBadge=c=>{const s=liveState(c);return `<span class="live ${s.cls}" data-live="${c.id}"><i></i><span>${s.t}</span></span>`};
function liveTick(){$$('[data-live]').forEach(el=>{const c=carBy(el.dataset.live);if(!c)return;const s=liveState(c);el.className='live '+s.cls;const sp=$('span',el);if(sp&&sp.textContent!==s.t)sp.textContent=s.t});
  const tm=fmtD(new Date(),{hour:'2-digit',minute:'2-digit',second:'2-digit'});$$('[data-live-time]').forEach(el=>el.textContent=tm)}
setInterval(liveTick,15000);
addEventListener('storage',e=>{if(e.key&&e.key.indexOf('mc_')===0){liveTick();if(e.key==='mc_appts'&&typeof agRefresh==='function')agRefresh()}});

/* ---------- card auto ---------- */
function availBadge(c,from,to){const st=fleetState()[c.id];
  if(st==='maint')return `<span class="badge bad">${T('Manutenzione','Maintenance')}</span>`;
  if(from&&to)return isFree(c,from,to)?`<span class="badge ok">${T('Libera nelle date','Free on your dates')}</span>`:`<span class="badge bad">${T('Occupata nelle date','Booked on your dates')}</span>`;
  const n=nextFree(c);if(n&&+n===+TODAY)return `<span class="badge ok">${T('Disponibile oggi','Available today')}</span>`;
  return `<span class="badge warn">${n?T('Libera dal ','Free from ')+fmtDs(n):T('Non disponibile','Unavailable')}</span>`}
function carCard(c,o){o=o||{};const sale=o.mode==='sale',f=favs().includes(c.id),q=!sale&&o.from&&o.to?quote(c,{from:o.from,to:o.to}):null,d=LANG==='it'?',':'.';
  const href=sale?'#/vendita/'+c.id:'#/auto/'+c.id,fin=sale?finance({price:c.sale,down:Math.round(c.sale*.25),months:48,balloon:true,bPct:35}):null;
  return `<article class="car reveal">
  <a class="car-img" href="${href}" aria-label="${esc(carName(c))}">${carSVG(c)}
   <div class="tl"><span class="badge plain">${T(CATS[c.cat].it,CATS[c.cat].en)}</span>${sale?'':availBadge(c,o.from,o.to)}</div></a>
  <div class="car-tr"><button class="icon-btn fav" data-act="fav" data-id="${c.id}" aria-pressed="${f}" aria-label="${T('Preferiti','Favourite')}: ${esc(carName(c))}">${IC.heart}</button>
   <button class="icon-btn" data-act="cmp" data-id="${c.id}" aria-pressed="${CMP.includes(c.id)}" aria-label="${T('Confronta','Compare')}: ${esc(carName(c))}">${IC.cmp}</button></div>
  <div class="car-body"><div><div class="brand">${c.brand}</div><h3><a href="${href}">${c.model}</a></h3></div>
   <div class="specs"><span><b>${c.cv}</b> ${PW()}</span><span><b>${String(c.s100).replace('.',d)}</b> s</span>${sale?`<span><b>${c.year}</b></span><span><b>${num(c.km)}</b> km</span>`:`<span><b>${c.kmDay}</b> km/${T('g','d')}</span>`}</div>
   <div class="car-foot"><div class="price">${sale?`<span class="num">${eur(c.sale)}</span><small>${T('o','or')} ${eur(fin.rata)}/${T('mese','mo')} · ${T('esempio','example')}</small>`:
     q&&q.ok?`<span class="num">${eur(q.total)}</span><small>${q.days} ${T(q.days>1?'giorni':'giorno',q.days>1?'days':'day')} · ${T('esempio','example')}</small>`:
     `<span class="num">${eur(c.day)}<small> /${T('giorno','day')}</small></span><small>${T('da','from')} ${eur(tiers(c).month)}/${T('mese','mo')} · ${T('esempio','example')}</small>`}</div>
    <a class="btn btn-line btn-sm" href="${sale?href:'#/prenota/'+c.id+(o.from&&o.to?'?da='+o.from+'&a='+o.to:'')}">${sale?T('Scopri','Discover'):T('Prenota','Book')} ${IC.arrow}</a></div></div></article>`}
function favCount(){const n=favs().length,c=$('.fav-link .cnt');if(c){c.textContent=n;c.hidden=!n}}
ACT.fav=el=>{const on=toggleFav(el.dataset.id);$$(`[data-act=fav][data-id="${el.dataset.id}"]`).forEach(b=>b.setAttribute('aria-pressed',on));favCount();toast(on?T('Aggiunta ai preferiti','Added to favourites'):T('Tolta dai preferiti','Removed from favourites'))};
ACT.cmp=el=>{const id=el.dataset.id,i=CMP.indexOf(id);
  if(i>=0)CMP.splice(i,1);else{if(CMP.length>=3){toast(T('Puoi confrontare al massimo 3 auto','You can compare up to 3 cars'));return}CMP.push(id)}
  $$(`[data-act=cmp][data-id="${id}"]`).forEach(b=>b.setAttribute('aria-pressed',CMP.includes(id)));renderTray()};
function renderTray(){const t=$('#cmpTray');if(!CMP.length){t.hidden=true;return}t.hidden=false;
  t.innerHTML=`<span>${CMP.length}/3 · ${CMP.map(i=>carBy(i).model).join(' · ')}</span><button class="btn btn-primary btn-sm" data-act="cmpOpen" ${CMP.length<2?'disabled':''}>${T('Confronta','Compare')}</button><button class="x" data-act="cmpClear" aria-label="${T('Svuota','Clear')}">×</button>`}
ACT.cmpClear=()=>{CMP=[];$$('[data-act=cmp]').forEach(b=>b.setAttribute('aria-pressed',false));renderTray()};
ACT.cmpOpen=()=>{const cs=CMP.map(carBy),d=LANG==='it'?',':'.';
  const row=(l,f,best)=>{let bi=-1;if(best){const v=cs.map(best);bi=v.indexOf(Math.max(...v))}return `<tr><th scope="row">${l}</th>${cs.map((c,i)=>`<td class="${i===bi?'best':''}">${f(c)}</td>`).join('')}</tr>`};
  openModal(`<div class="sheet-head"><div><p class="eyebrow">${T('Confronto','Comparison')}</p><h2>${T('Fianco a fianco','Side by side')}</h2></div><button class="x" data-act="closeModal" aria-label="${T('Chiudi','Close')}">×</button></div>
  <div class="tbl-wrap"><table class="tbl cmp-tbl" style="min-width:${cs.length*150+110}px"><thead><tr><th></th>${cs.map(c=>`<th><div class="cmp-img">${carSVG(c)}</div><b>${carName(c)}</b></th>`).join('')}</tr></thead><tbody>
  ${row(T('Potenza','Power'),c=>c.cv+' '+PW(),c=>c.cv)}${row('0–100 km/h',c=>String(c.s100).replace('.',d)+' s',c=>-c.s100)}${row(T('Velocità massima','Top speed'),c=>c.vmax+' km/h',c=>c.vmax)}
  ${row(T('Noleggio al giorno','Rental per day'),c=>eur(c.day)+' '+demoTag(),c=>-c.day)}${row(T('Al mese (30 gg)','Per month (30 d)'),c=>eur(tiers(c).month)+' '+demoTag(),c=>-c.day)}${row(T('Prezzo di vendita','Sale price'),c=>eur(c.sale)+' '+demoTag(),c=>-c.sale)}
  ${row(T('Km inclusi al giorno','Km included per day'),c=>c.kmDay,c=>c.kmDay)}${row(T('Cauzione','Security deposit'),c=>eur(c.dep)+' '+demoTag(),c=>-c.dep)}
  ${row(T('Motore','Engine'),c=>T(c.eng.it,c.eng.en))}${row(T('Cambio','Gearbox'),c=>T(c.gear.it,c.gear.en))}${row(T('Trazione','Drive'),c=>T(c.drive.it,c.drive.en))}${row(T('Posti','Seats'),c=>c.seats,c=>c.seats)}
  ${row('',c=>`<div class="cmp-act"><a class="btn btn-primary btn-sm" data-act="closeModal" href="#/auto/${c.id}">${T('Noleggio','Rental')}</a><a class="btn btn-line btn-sm" data-act="closeModal" href="#/vendita/${c.id}">${T('Acquisto','Purchase')}</a></div>`)}
  </tbody></table></div><p class="xs dim" style="margin-top:12px">${T('In evidenza il valore migliore di ogni riga. Prezzi di esempio.','The best value of each row is highlighted. Sample prices.')}</p>`)};

/* ---------- reveal ---------- */
let io=null;
function observeReveal(){const all=$$('.reveal:not(.in)');if(!('IntersectionObserver' in window)){all.forEach(e=>e.classList.add('in'));return}
  if(!io)io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target)}}),{rootMargin:'0px 0px 8% 0px'});
  all.forEach((e,i)=>{e.style.transitionDelay=Math.min(i%6,5)*60+'ms';const r=e.getBoundingClientRect();if(r.top<innerHeight&&r.bottom>0)e.classList.add('in');else io.observe(e)});
  setTimeout(()=>all.forEach(e=>{if(!e.classList.contains('in')){const r=e.getBoundingClientRect();if(r.top<innerHeight*1.2)e.classList.add('in')}}),1500)}
addEventListener('scroll',()=>{if(observeReveal.t)return;observeReveal.t=setTimeout(()=>{observeReveal.t=0;$$('.reveal:not(.in)').forEach(e=>{const r=e.getBoundingClientRect();if(r.top<innerHeight&&r.bottom>0)e.classList.add('in')})},200)},{passive:true});

/* ================= HOME ================= */
let searchTab='rent';
function viewHome(){
  const hero=CARS[0],d=LANG==='it'?',':'.';
  return `<section class="hero" id="hero"><div class="hero-stage" aria-hidden="true"><div class="hero-light"></div><div class="hero-beam"></div><div class="hero-floor"></div>
     <div class="hero-car"><div class="carwrap">${carSVG(hero,{label:''})}</div><div class="mirror">${carSVG(hero,{label:''})}</div></div></div>
    <div class="wrap hero-in"><div class="hero-copy">
      <p class="eyebrow">${T('Milano · Via Arbe 95','Milan · Via Arbe 95')}</p>
      <h1>${T('L\'eccezione,<br><em>a portata di chiavi.</em>','The exceptional,<br><em>within reach.</em>')}</h1>
      <p class="lead">${T('Noleggio e vendita di auto speciali: supercar, SUV sportivi e wagon ad alte prestazioni, preparate una per una.','Rental and sales of special cars: supercars, performance SUVs and high-performance estates, prepared one by one.')}</p>
      <div class="cta"><a class="btn btn-primary" href="#/noleggio">${T('Scopri la collezione','Explore the collection')} ${IC.arrow}</a><a class="btn btn-line" href="#/agenda">${IC.cal}<span>${T('Prenota una visita','Book a visit')}</span></a></div>
    </div>
    <div class="hero-meta"><div><b class="num">${hero.cv}</b><span>${PW()} · ${hero.model}</span></div><div><b class="num">${String(hero.s100).replace('.',d)} s</b><span>0–100 km/h</span></div><div><b class="num">6</b><span>${T('auto in collezione','cars in the collection')}</span></div></div></div></section>

  <section class="search"><div class="wrap"><div class="search-box reveal">
    <div class="seg" role="tablist" aria-label="${T('Cosa cerchi','What are you looking for')}"><button role="tab" data-act="searchTab" data-t="rent" aria-selected="${searchTab==='rent'}">${T('Noleggio','Rental')}</button><button role="tab" data-act="searchTab" data-t="buy" aria-selected="${searchTab==='buy'}">${T('Acquisto','Purchase')}</button></div>
    <div id="searchForm">${searchForm()}</div></div></div></section>

  <section class="sec"><div class="wrap">${secHead(T('La collezione','The collection'),T('Sei auto, <em>un solo standard.</em>','Six cars, <em>one standard.</em>'),'',`<a class="link" href="#/noleggio">${T('Tutte le auto','All cars')} →</a>`)}</div>
    <div class="hscroll" tabindex="0" aria-label="${T('Auto in evidenza','Featured cars')}">${CARS.map(c=>carCard(c)).join('')}</div></section>

  <section class="sec tight"><div class="wrap">${secHead(T('Servizi','Services'),T('Tutto quello che serve, <em>in un solo posto.</em>','Everything you need, <em>in one place.</em>'))}
    <div class="svc">${[
      ['#/noleggio','01',T('Noleggio breve','Short-term rental'),T('Un giorno, un weekend o una settimana: totale chiaro per le tue date, km inclusi e cauzione.','A day, a weekend or a week: a clear total for your dates, included km and deposit.')],
      ['#/lungo-termine','02',T('Lungo termine','Long-term rental'),T('Da 12 a 48 mesi, canone mensile con servizi inclusi.','12 to 48 months, monthly fee with services included.')],
      ['#/vendita','03',T('Vendita','Sales'),T('Auto selezionate, configurabili e con storico verificato.','Selected cars, configurable, with verified history.')],
      ['#/finanziamento','04',T('Finanziamento','Finance'),T('Rata, anticipo e maxirata: simula in tempo reale.','Instalment, down payment and balloon: simulate live.')],
      ['#/permuta','05',T('Permuta','Trade-in'),T('Una stima indicativa della tua auto in un minuto.','An indicative estimate of your car in a minute.')],
      ['#/agenda','06',T('Test drive e visite','Test drives and visits'),T('Scegli giorno e ora: l\'agenda mostra solo le fasce libere.','Pick day and time: the agenda shows only free slots.')]
     ].map(s=>`<a class="svc-i reveal" href="${s[0]}"><span class="n">${s[1]}</span><h3>${s[2]}</h3><p>${s[3]}</p><span class="go">${IC.arrow}</span></a>`).join('')}</div></div></section>

  <section class="sec tight"><div class="wrap"><div class="live-board reveal"><div class="lb-head"><div><p class="eyebrow">${T('In tempo reale','Live')} <span class="demo-tag">${T('simulato','simulated')}</span></p><h2>${T('Disponibilità <em>adesso.</em>','Availability <em>right now.</em>')}</h2></div>
     <p class="small muted">${T('Aggiornato alle','Updated at')} <b data-live-time>${fmtD(new Date(),{hour:'2-digit',minute:'2-digit',second:'2-digit'})}</b>. ${T('Legge prenotazioni e stato flotta del prototipo, anche se cambiano in un\'altra scheda.','Reads the prototype\'s bookings and fleet status, even if they change in another tab.')}</p></div>
    <ul class="lb-list">${CARS.map(c=>`<li><a href="#/auto/${c.id}"><span class="lb-car">${carSVG(c,{label:''})}</span><span class="lb-name"><small>${c.brand}</small><b>${c.model}</b>${liveBadge(c)}</span><span class="lb-price">${eur(c.day)}<small>/${T('g','d')}</small></span></a></li>`).join('')}</ul></div></div></section>

  <section class="sec"><div class="wrap"><div class="split-feature reveal"><div class="sf-media">${carSVG(CARS[2],{hex:'#1f3f8f',rim:'m10',fin:'bronzo',label:''})}</div>
    <div class="sf-copy"><p class="eyebrow">${T('Configuratore','Configurator')}</p><h2>${T('Il colore giusto, <em>i cerchi giusti.</em>','The right colour, <em>the right wheels.</em>')}</h2><p class="lead">${T('In ogni scheda: otto vernici, tre disegni di cerchi, quattro finiture e il colore delle pinze. Il visore 3D si gira a 360°.','On every page: eight paints, three wheel designs, four finishes and the caliper colour. The 3D viewer turns 360°.')}</p>
    <div class="cta"><a class="btn btn-line" href="#/vendita/${CARS[2].id}">${T('Prova il configuratore','Try the configurator')} ${IC.arrow}</a></div></div></div></div></section>

  <section class="sec tight"><div class="wrap">${secHead(T('Come funziona','How it works'),T('Dalla scelta <em>alle chiavi.</em>','From choice <em>to keys.</em>'))}
    <ol class="steps">${[
      [T('Scegli auto e date','Pick car and dates'),T('Disponibilità e totale subito, km inclusi e cauzione.','Availability and total at once, included km and deposit.')],
      [T('Fissa l\'appuntamento','Book the slot'),T('Ritiro, test drive o visita: scegli giorno e ora liberi.','Pick-up, test drive or visit: choose a free day and time.')],
      [T('Documenti e firma','Documents and signature'),T('Dal telefono, senza passare in sede.','From your phone, without visiting.')],
      [T('Avvisa il salone','Notify the showroom'),T('Ultimo passo: WhatsApp con il riepilogo già scritto.','Final step: WhatsApp with the summary pre-written.')]
     ].map(s=>`<li class="reveal"><h3>${s[0]}</h3><p>${s[1]}</p></li>`).join('')}</ol></div></section>

  <section class="sec tight"><div class="wrap">${secHead(T('Recensioni','Reviews'),T('La parola <em>ai clienti.</em>','In our <em>clients\' words.</em>'))}
    <div class="note warn" role="note"><b>${T('Recensioni di ESEMPIO','SAMPLE reviews')}.</b> ${T('Testi e nomi inventati solo per mostrare l\'impaginazione: non sono recensioni di clienti reali. In produzione qui compariranno le recensioni pubblicate sul profilo Google di MC.','Made-up texts and names, only to show the layout: these are not real client reviews. In production, the reviews published on MC\'s Google profile will appear here.')}</div>
    <div class="reviews">${REVIEWS.map(r=>`<figure class="review reveal"><span class="sample">${T('Esempio','Example')}</span><div class="stars" aria-label="${T('Valutazione di esempio','Sample rating')}">★★★★★</div><blockquote>${T(r.it,r.en)}</blockquote><figcaption><b>${r.who}</b><small>${carName(carBy(r.car))}</small></figcaption></figure>`).join('')}</div></div></section>

  <section class="sec tight"><div class="wrap narrow">${secHead('FAQ',T('Domande <em>frequenti.</em>','Common <em>questions.</em>'))}
    <div class="faq">${FAQ().map(f=>`<details class="reveal"><summary>${f[0]}</summary><p>${f[1]}</p></details>`).join('')}</div>
    <p class="small dim" style="margin-top:16px">${T('Condizioni di esempio: quelle definitive le stabilisce MC.','Sample terms: the final ones are set by MC.')}</p></div></section>

  <section class="sec tight"><div class="wrap">${whereBlock()}</div></section>

  <section class="sec tight"><div class="wrap"><div class="cta-band reveal"><p class="eyebrow">${T('Pronto?','Ready?')}</p><h2>${T('La prossima auto <em>la scegli tu.</em>','Your next car <em>is your call.</em>')}</h2><p class="lead">${BIZ.addr}, ${BIZ.cap} · ${BIZ.tel}</p>
    <div class="cta"><a class="btn btn-primary" href="#/noleggio">${T('Prenota un\'auto','Book a car')} ${IC.arrow}</a><a class="btn btn-line" href="#/vendita">${T('Auto in vendita','Cars for sale')}</a><button class="btn btn-line" data-act="callback">${IC.phone}<span>${T('Ti richiamiamo','We call you back')}</span></button></div></div></div></section>`;
}
/* mappa stilizzata (nessun iframe esterno) + link a Google Maps */
function whereBlock(){return `<div class="where reveal"><div class="map" role="img" aria-label="${T('Mappa schematica della zona di Via Arbe, Milano','Schematic map of the Via Arbe area, Milan')}">
   <svg viewBox="0 0 600 400" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><defs><pattern id="mgrid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="currentColor" stroke-opacity=".06"/></pattern></defs>
    <rect width="600" height="400" fill="url(#mgrid)"/><path d="M-20 300 C120 260 220 250 330 210 S520 120 640 90" stroke="currentColor" stroke-opacity=".22" stroke-width="18" fill="none"/>
    <path d="M60 -20 L210 420" stroke="currentColor" stroke-opacity=".14" stroke-width="10"/><path d="M430 -20 L360 420" stroke="currentColor" stroke-opacity=".14" stroke-width="12"/><path d="M-20 120 L640 170" stroke="currentColor" stroke-opacity=".1" stroke-width="8"/>
    <path d="M250 -20 C270 120 300 200 310 420" stroke="currentColor" stroke-opacity=".1" stroke-width="6" fill="none"/>
    <circle cx="318" cy="214" r="46" fill="var(--accent)" fill-opacity=".12"/><circle cx="318" cy="214" r="9" fill="var(--accent)"/><circle cx="318" cy="214" r="18" fill="none" stroke="var(--accent)" stroke-width="2" class="ping"/></svg>
   <span class="map-tag">${IC.pin}<b>MC</b> · ${BIZ.addr}</span><small class="map-note">${T('Schema indicativo, non in scala','Indicative sketch, not to scale')}</small></div>
  <div class="where-copy"><p class="eyebrow">${T('Dove siamo','Where we are')}</p><h2>${T('Via Arbe 95, <em>Milano.</em>','Via Arbe 95, <em>Milan.</em>')}</h2><p class="lead">${T('Zona Maciachini – Bicocca, a pochi minuti da Linate e dalla tangenziale. Su appuntamento anche fuori orario.','Maciachini – Bicocca area, minutes from Linate and the ring road. By appointment also outside hours.')}</p>
   <ul class="hours">${HOURS().map(h=>`<li><span>${h[0]}</span><span>${h[1]}</span></li>`).join('')}</ul>
   <div class="cta"><a class="btn btn-primary" href="${BIZ.dir}" target="_blank" rel="noopener">${IC.pin}<span>${T('Indicazioni su Google Maps','Directions on Google Maps')}</span></a><a class="btn btn-line" href="${BIZ.maps}" target="_blank" rel="noopener">${T('Apri la mappa','Open the map')} ↗</a><a class="btn btn-line" href="${BIZ.telHref}">${IC.phone}<span>${BIZ.tel}</span></a></div></div></div>`}
const FAQ=()=>[
  [T('Cosa serve per noleggiare?','What do I need to rent?'),T('Patente valida, documento d\'identità e una carta di credito per la cauzione. L\'età minima cambia da auto ad auto ed è indicata in ogni scheda.','A valid driving licence, an ID document and a credit card for the security deposit. Minimum age varies by car and is shown on each page.')],
  [T('Come funziona la cauzione?','How does the security deposit work?'),T('È una pre-autorizzazione sulla carta: non viene incassata e si sblocca alla riconsegna se l\'auto rientra senza danni.','It is a pre-authorisation on your card: it is not charged and is released at return if the car comes back undamaged.')],
  [T('Posso annullare?','Can I cancel?'),T('Sì. Fino a 7 giorni prima l\'acconto viene rimborsato per intero, da 7 a 3 giorni al 50%, sotto i 3 giorni non è rimborsabile.','Yes. Up to 7 days before, the down payment is fully refunded; from 7 to 3 days, 50%; under 3 days it is non-refundable.')],
  [T('Quanti chilometri sono inclusi?','How many kilometres are included?'),T('Ogni auto ha un numero di km al giorno inclusi; oltre si paga un importo al km indicato nella scheda. Puoi aggiungere i chilometri illimitati tra gli extra.','Each car includes a daily km allowance; beyond it a per-km fee shown on the page applies. You can add unlimited mileage as an extra.')],
  [T('Il calcolo della rata è un\'offerta?','Is the instalment calculation an offer?'),T('No: TAN, TAEG e spese sono valori di esempio per orientarsi. L\'offerta vera, con il documento informativo europeo (SECCI), la fa la finanziaria dopo la valutazione.','No: rate, APR and fees are sample values to help you orient. The real offer, with the European information sheet (SECCI), is made by the lender after assessment.')],
  [T('Come funziona la permuta?','How does trade-in work?'),T('Il sito dà una forchetta indicativa; la valutazione definitiva si fa vedendo l\'auto e i documenti.','The site gives an indicative range; the final valuation is made on seeing the car and its documents.')],
  [T('Consegnate a domicilio o in aeroporto?','Do you deliver to my door or the airport?'),T('Sì: a Milano, a Linate, Malpensa e Bergamo Orio al Serio, con un supplemento che vedi nel totale prima di pagare.','Yes: in Milan and at Linate, Malpensa and Bergamo Orio al Serio, for a fee shown in the total before you pay.')],
  [T('La prenotazione arriva al salone in automatico?','Does the booking reach the showroom automatically?'),T('Nel prototipo no: l\'ultimo passo apre WhatsApp con il messaggio già scritto e sei tu a inviarlo. Nella versione definitiva la notifica partirà dal server.','Not in the prototype: the last step opens WhatsApp with the message pre-written and you send it. In the final version the notification will come from the server.')]
];
function searchForm(){
  if(searchTab==='rent')return `<form class="search-form" data-form="searchRent" novalidate>
    <label class="field"><span>${T('Ritiro','Pick-up')}</span><input class="inp" type="date" name="from" min="${iso(TODAY)}" value="${RF.from}"></label>
    <label class="field"><span>${T('Riconsegna','Return')}</span><input class="inp" type="date" name="to" min="${iso(TODAY)}" value="${RF.to}"></label>
    <label class="field"><span>${T('Categoria','Category')}</span><select class="inp" name="cat"><option value="">${T('Tutte','All')}</option>${Object.keys(CATS).map(k=>`<option value="${k}">${T(CATS[k].it,CATS[k].en)}</option>`).join('')}</select></label>
    <label class="field"><span>${T('Marca','Brand')}</span><select class="inp" name="brand"><option value="">${T('Tutte','All')}</option>${[...new Set(CARS.map(c=>c.brand))].map(b=>`<option>${b}</option>`).join('')}</select></label>
    <button class="btn btn-primary">${T('Cerca','Search')} ${IC.arrow}</button></form>`;
  return `<form class="search-form" data-form="searchBuy" novalidate>
    <label class="field"><span>${T('Marca','Brand')}</span><select class="inp" name="brand"><option value="">${T('Tutte','All')}</option>${[...new Set(CARS.map(c=>c.brand))].map(b=>`<option>${b}</option>`).join('')}</select></label>
    <label class="field"><span>${T('Budget massimo','Max budget')}</span><select class="inp" name="max"><option value="">${T('Nessun limite','No limit')}</option>${[70000,100000,150000,400000].map(v=>`<option value="${v}">${eur(v)}</option>`).join('')}</select></label>
    <label class="field"><span>${T('Alimentazione','Fuel')}</span><select class="inp" name="fuel"><option value="">${T('Tutte','All')}</option>${Object.keys(FUELS).map(k=>`<option value="${k}">${T(FUELS[k].it,FUELS[k].en)}</option>`).join('')}</select></label>
    <label class="field"><span>${T('Km massimi','Max km')}</span><select class="inp" name="km"><option value="">${T('Nessun limite','No limit')}</option>${[10000,30000,50000].map(v=>`<option value="${v}">${num(v)} km</option>`).join('')}</select></label>
    <button class="btn btn-primary">${T('Cerca','Search')} ${IC.arrow}</button></form>`}
ACT.searchTab=el=>{searchTab=el.dataset.t;$$('[data-act=searchTab]').forEach(b=>b.setAttribute('aria-selected',b===el));$('#searchForm').innerHTML=searchForm()};
FORM.searchRent=f=>{const d=new FormData(f);let from=d.get('from'),to=d.get('to');if(from&&to&&to<from){toast(T('La riconsegna deve essere dopo il ritiro','Return must be after pick-up'));return}
  Object.assign(RF,{from:from||'',to:to||'',cat:d.get('cat'),brand:d.get('brand')});location.hash='#/noleggio'};
FORM.searchBuy=f=>{const d=new FormData(f);Object.assign(SF,{brand:d.get('brand'),max:+d.get('max')||SF0.max,fuel:d.get('fuel'),km:+d.get('km')||SF0.km});location.hash='#/vendita'};

/* ================= CATALOGO NOLEGGIO ================= */
const RF0={q:'',brand:'',cat:'',fuel:'',max:2000,from:'',to:'',sort:'rel',free:false};
const RF=Object.assign({},RF0);
function rentList(){let l=CARS.filter(c=>(!RF.brand||c.brand===RF.brand)&&(!RF.cat||c.cat===RF.cat)&&(!RF.fuel||c.fuel===RF.fuel)&&c.day<=RF.max&&(!RF.q||carName(c).toLowerCase().includes(RF.q.toLowerCase())));
  const dates=RF.from&&RF.to&&RF.to>=RF.from;
  if(RF.free)l=l.filter(c=>dates?isFree(c,RF.from,RF.to):fleetState()[c.id]!=='maint');
  const s={rel:(a,b)=>0,up:(a,b)=>a.day-b.day,down:(a,b)=>b.day-a.day,cv:(a,b)=>b.cv-a.cv,acc:(a,b)=>a.s100-b.s100}[RF.sort]||((a,b)=>0);
  l=l.slice().sort(s);if(RF.sort==='rel'&&dates)l.sort((a,b)=>(isFree(b,RF.from,RF.to)?1:0)-(isFree(a,RF.from,RF.to)?1:0));return l}
const skelCards=n=>`<div class="cars">${Array.from({length:n},()=>`<div class="skel" style="height:420px"></div>`).join('')}</div>`;
function rentResults(){const l=rentList(),dates=RF.from&&RF.to&&RF.to>=RF.from;
  if(RF.from&&RF.to&&RF.to<RF.from)return `<div class="note bad" role="alert">${T('La data di riconsegna viene prima del ritiro: correggi le date per vedere disponibilità e totale.','The return date is before pick-up: fix the dates to see availability and totals.')}</div>`;
  if(!l.length)return `<div class="empty"><b>${T('Nessuna auto con questi filtri','No cars match these filters')}</b><p>${T('Prova ad allargare il prezzo o a cambiare le date.','Try widening the price or changing the dates.')}</p><button class="btn btn-line btn-sm" data-act="rentReset">${T('Azzera i filtri','Reset filters')}</button></div>`;
  return `<div class="cars">${l.map(c=>carCard(c,dates?{from:RF.from,to:RF.to}:{})).join('')}</div>`}
function viewRent(){const brands=[...new Set(CARS.map(c=>c.brand))];
  const sel=(n,opts,all)=>`<select class="inp" data-chg="rf" name="${n}"><option value="">${all}</option>${opts.map(o=>`<option value="${o[0]}" ${RF[n]===o[0]?'selected':''}>${o[1]}</option>`).join('')}</select>`;
  return `<div class="page"><div class="wrap">
  <div class="page-head"><p class="eyebrow">${T('Noleggio','Rental')}</p><h1>${T('Scegli <em>la tua auto.</em>','Choose <em>your car.</em>')}</h1><p class="lead">${T('Disponibilità e totale calcolati sulle tue date. Tariffe a giorno, weekend, settimana e mese.','Availability and total worked out on your dates. Day, weekend, week and month rates.')} ${demoTag()}</p>
   <div class="seg" role="tablist"><a role="tab" aria-selected="true" href="#/noleggio">${T('Breve termine','Short-term')}</a><a role="tab" aria-selected="false" href="#/lungo-termine">${T('Lungo termine','Long-term')}</a></div></div>
  <div class="cat-layout">
   <aside class="filters" id="filters" aria-label="${T('Filtri','Filters')}"><h3>${T('Filtri','Filters')}<button class="link xs" data-act="rentReset">${T('Azzera','Reset')}</button></h3>
    <label class="field"><span>${T('Cerca modello','Search model')}</span><input class="inp" type="search" data-chg="rf" name="q" value="${esc(RF.q)}" placeholder="RS6, 296…"></label>
    <div class="grid2" style="gap:10px"><label class="field"><span>${T('Ritiro','Pick-up')}</span><input class="inp" type="date" data-chg="rf" name="from" min="${iso(TODAY)}" value="${RF.from}"></label>
    <label class="field"><span>${T('Riconsegna','Return')}</span><input class="inp" type="date" data-chg="rf" name="to" min="${iso(TODAY)}" value="${RF.to}"></label></div>
    <label class="field"><span>${T('Marca','Brand')}</span>${sel('brand',brands.map(b=>[b,b]),T('Tutte','All'))}</label>
    <label class="field"><span>${T('Categoria','Category')}</span>${sel('cat',Object.keys(CATS).map(k=>[k,T(CATS[k].it,CATS[k].en)]),T('Tutte','All'))}</label>
    <label class="field"><span>${T('Alimentazione','Fuel')}</span>${sel('fuel',Object.keys(FUELS).map(k=>[k,T(FUELS[k].it,FUELS[k].en)]),T('Tutte','All'))}</label>
    <label class="field"><span>${T('Prezzo massimo al giorno','Max price per day')}: <b id="rfMax">${eur(RF.max)}</b></span><input type="range" data-chg="rf" name="max" min="250" max="2000" step="50" value="${RF.max}"></label>
    <label class="check"><input type="checkbox" data-chg="rf" name="free" ${RF.free?'checked':''}><span>${T('Solo auto disponibili','Available cars only')}</span></label>
    <p class="xs dim">${T('Disponibilità dimostrativa: in produzione arriva dal calendario reale.','Demo availability: in production it comes from the live calendar.')}</p>
   </aside>
   <div><div class="toolbar"><div class="tb-l"><button class="btn btn-line btn-sm filter-toggle" data-act="filterToggle" aria-controls="filters" aria-expanded="false">${T('Filtri','Filters')}</button><span class="muted small" id="rentCount" aria-live="polite"></span></div>
     <label class="field"><span>${T('Ordina','Sort')}</span><select class="inp" data-chg="rf" name="sort">${[['rel',T('Rilevanza','Relevance')],['up',T('Prezzo crescente','Price: low to high')],['down',T('Prezzo decrescente','Price: high to low')],['cv',T('Più potenti','Most powerful')],['acc',T('Più veloci 0–100','Quickest 0–100')]].map(o=>`<option value="${o[0]}" ${RF.sort===o[0]?'selected':''}>${o[1]}</option>`).join('')}</select></label></div>
    <div id="rentRes" aria-busy="true">${skelCards(3)}</div></div>
  </div></div></div>`}
let rentTimer=0;
function rentRefresh(fast){const box=$('#rentRes');if(!box)return;box.setAttribute('aria-busy','true');if(!fast)box.innerHTML=skelCards(Math.max(1,Math.min(3,rentList().length)));clearTimeout(rentTimer);
  rentTimer=setTimeout(()=>{if(!$('#rentRes'))return;box.innerHTML=rentResults();box.setAttribute('aria-busy','false');const n=rentList().length;$('#rentCount').textContent=n+' '+T('auto',n===1?'car':'cars');observeReveal()},fast?0:380)}
CHG.rf=el=>{RF[el.name]=el.type==='checkbox'?el.checked:el.type==='range'?+el.value:el.value;if(el.name==='max')$('#rfMax').textContent=eur(RF.max);rentRefresh()};
ACT.rentReset=()=>{Object.assign(RF,RF0);route()};
ACT.filterToggle=el=>{const o=$('#filters').classList.toggle('open');el.setAttribute('aria-expanded',o)};

/* ================= CONFIGURATORE + VISORE (comune a noleggio e vendita) ================= */
let DT={tab:'tariffe',from:'',to:'',view:0,lt:{m:36,km:15000,down:0}};
const svgOpt=c=>({});
/* viste 2D per il visore senza WebGL: si trascina o si usano le frecce */
const VIEWS=()=>[[T('Profilo sinistro','Left side'),{}],[T('Profilo destro','Right side'),{flip:1}],[T('Anteriore','Front'),{front:1}],[T('Posteriore','Rear'),{rear:1}],[T('Cerchio','Wheel'),{rimv:1}]];
function view2D(c,i){const v=VIEWS()[i][1],o=svgOpt(c);if(v.rimv)return `<div class="rimv">${rimSVG(o)}</div>`;
  if(v.front)return carSVG(c,Object.assign({vb:[c.geo.x0-30,SHAPE[c.id].ty+20,420,270]},o));if(v.rear)return carSVG(c,Object.assign({vb:[c.geo.x1-390,SHAPE[c.id].ty+20,420,270]},o));return carSVG(c,Object.assign({flip:v.flip},o))}
function viewerBlock(c){const V=VIEWS();return `<div class="viewer" id="viewer" data-car="${c.id}"><div class="viewer-fb" id="viewerFb" data-drag>${view2D(c,DT.view)}</div>
   <div class="viewer-hud"><span class="xs muted" id="viewerTag">${V[DT.view][0]}</span><span class="xs dim">${T('Illustrazione generica','Generic illustration')}</span></div>
   <div class="viewer-nav"><button class="icon-btn" data-act="vPrev" aria-label="${T('Vista precedente','Previous view')}">‹</button><span id="viewerHint" class="xs">${T('Trascina per girare','Drag to turn')}</span><button class="icon-btn" data-act="vNext" aria-label="${T('Vista successiva','Next view')}">›</button></div></div>
  <div class="thumbs" id="thumbs">${V.map((v,i)=>`<button class="tv ${i===DT.view?'on':''}" data-act="vGo" data-i="${i}" aria-label="${v[0]}">${view2D(c,i)}<small>${v[0]}</small></button>`).join('')}</div>`}
function vSet(i){const c=carBy(curId());const n=VIEWS().length;DT.view=(i+n)%n;const fb=$('#viewerFb');if(fb){fb.innerHTML=view2D(c,DT.view);fb.classList.remove('swap');void fb.offsetWidth;fb.classList.add('swap')}
  $$('#thumbs .tv').forEach((b,k)=>b.classList.toggle('on',k===DT.view));const tg=$('#viewerTag');
  if(tg)tg.textContent=VIEWS()[DT.view][0]}
ACT.vPrev=()=>vSet(DT.view-1);ACT.vNext=()=>vSet(DT.view+1);ACT.vGo=el=>vSet(+el.dataset.i);
/* trascinamento nel visore 2D */
document.addEventListener('pointerdown',e=>{const fb=e.target.closest('[data-drag]');if(!fb)return;const x0=e.clientX;let done=false;
  const mv=ev=>{if(done)return;const dx=ev.clientX-x0;if(Math.abs(dx)>40){done=true;vSet(DT.view+(dx<0?1:-1))}};const up=()=>{removeEventListener('pointermove',mv);removeEventListener('pointerup',up)};
  addEventListener('pointermove',mv);addEventListener('pointerup',up)});

/* condivisione scheda */
ACT.share=async el=>{const url=location.href.split('#')[0]+'#'+(el.dataset.path||location.hash.slice(1)),title=el.dataset.title||document.title;
  try{if(navigator.share){await navigator.share({title,url});return}}catch(e){if(e&&e.name==='AbortError')return}
  let copied=false;try{await navigator.clipboard.writeText(url);copied=true}catch(e){}
  openModal(`<div class="sheet-head"><div><p class="eyebrow">${T('Condividi','Share')}</p><h2>${esc(title.split('–')[0])}</h2></div><button class="x" data-act="closeModal" aria-label="${T('Chiudi','Close')}">×</button></div>
   <label class="field"><span>${T('Link della scheda','Page link')}</span><input class="inp" readonly value="${esc(url)}" onfocus="this.select()"></label>
   <p class="small muted" style="margin:10px 0 16px">${copied?T('Link copiato negli appunti.','Link copied to the clipboard.'):T('Copia il link qui sopra.','Copy the link above.')}</p>
   <div class="cta">${`<a class="btn btn-line" href="https://wa.me/?text=${encodeURIComponent(title+' '+url)}" target="_blank" rel="noopener">${IC.wa}<span>${T('Invia a un amico su WhatsApp','Send to a friend on WhatsApp')}</span></a>`}<a class="btn btn-line" href="mailto:?subject=${encodeURIComponent(title)}&body=${encodeURIComponent(url)}">Email</a></div>`,true)};

/* ================= SCHEDA AUTO (noleggio) ================= */
function specGrid(c){const d=LANG==='it'?',':'.';return `<dl class="kv">${[
  [T('Potenza','Power'),c.cv+' '+PW()],['0–100 km/h',String(c.s100).replace('.',d)+' s'],[T('Velocità massima','Top speed'),c.vmax+' km/h'],[T('Motore','Engine'),T(c.eng.it,c.eng.en)],
  [T('Cambio','Gearbox'),T(c.gear.it,c.gear.en)],[T('Trazione','Drive'),T(c.drive.it,c.drive.en)],[T('Alimentazione','Fuel'),T(FUELS[c.fuel].it,FUELS[c.fuel].en)],[T('Posti','Seats'),c.seats],[T('Colore di serie','Standard colour'),paintName(c)]
 ].map(r=>`<div><dt>${r[0]}</dt><dd>${r[1]}</dd></div>`).join('')}</dl><p class="xs dim" style="margin-top:10px">${T('Dati tecnici indicativi dei modelli di serie; l\'allestimento della singola vettura va confermato.','Indicative figures for the standard models; each car\'s exact spec is to be confirmed.')}</p>`}
function tierTable(c){const t=tiers(c);return `<div class="tiers">${[[T('Giorno feriale','Weekday'),t.day,T('per 24 ore','per 24 hours')],[T('Giorno nel weekend','Weekend day'),t.we,T('ven–dom, +15%','Fri–Sun, +15%')],[T('Settimana','Week'),t.week,T('7 giorni, −20%','7 days, −20%')],[T('Mese','Month'),t.month,T('30 giorni, −35%','30 days, −35%')]].map(r=>`<div><span>${r[0]}</span><b class="num">${eur(r[1])}</b><small>${r[2]}</small></div>`).join('')}</div>
  <dl class="kv kv-s"><div><dt>${T('Km inclusi','Km included')}</dt><dd>${c.kmDay} ${T('al giorno','per day')}</dd></div><div><dt>${T('Km extra','Extra km')}</dt><dd>${eur2(c.kmEx)}/km</dd></div><div><dt>${T('Cauzione','Deposit')}</dt><dd>${eur(c.dep)}</dd></div><div><dt>${T('Età minima','Minimum age')}</dt><dd>${c.age} ${T('anni','years')}</dd></div></dl>
  <p class="xs dim" style="margin-top:10px">${T('Tariffe di esempio. Gli sconti per durata si applicano in automatico al totale (da 3 giorni −10%, da 7 −20%, da 28 −35%).','Sample rates. Duration discounts apply automatically to the total (3+ days −10%, 7+ −20%, 28+ −35%).')}</p>`}
function ltBox(c){const r=ltCanone(c,DT.lt.m,DT.lt.km,DT.lt.down);return `<div class="lt" id="ltBox"><div class="grid3">
   <label class="field"><span>${T('Durata','Term')}</span><select class="inp" data-chg="lt" name="m">${[12,24,36,48].map(m=>`<option value="${m}" ${DT.lt.m===m?'selected':''}>${m} ${T('mesi','months')}</option>`).join('')}</select></label>
   <label class="field"><span>${T('Km all\'anno','Km per year')}</span><select class="inp" data-chg="lt" name="km">${[10000,15000,20000,30000].map(k=>`<option value="${k}" ${DT.lt.km===k?'selected':''}>${num(k)}</option>`).join('')}</select></label>
   <label class="field"><span>${T('Anticipo (€)','Upfront (€)')}</span><input class="inp" type="number" min="0" step="1000" data-chg="lt" name="down" value="${DT.lt.down}"></label></div>
   <div class="lt-out"><span>${T('Canone mensile indicativo','Indicative monthly fee')}</span><b class="num" id="ltOut">${eur(r.canone)}<small>/${T('mese','mo')} ${T('IVA incl.','VAT incl.')}</small></b><span class="demo-tag">${T('esempio','example')}</span></div>
   <ul class="list-check cols2">${[T('RCA, furto e incendio','Liability, theft and fire'),T('Manutenzione ordinaria e straordinaria','Routine and extra maintenance'),T('Pneumatici','Tyres'),T('Assistenza stradale 24/7','24/7 roadside assistance')].map(x=>`<li><span>${x}</span></li>`).join('')}</ul>
   <a class="btn btn-line btn-sm" href="#/preventivo?tipo=lungo&auto=${c.id}">${T('Chiedi il preventivo lungo termine','Request a long-term quote')} ${IC.arrow}</a></div>`}
CHG.lt=el=>{DT.lt[el.name]=+el.value||0;const c=carBy(curId()),r=ltCanone(c,DT.lt.m,DT.lt.km,DT.lt.down),o=$('#ltOut');if(o)o.innerHTML=`${eur(r.canone)}<small>/${T('mese','mo')} ${T('IVA incl.','VAT incl.')}</small>`};
function viewCar(id,qs){const c=carBy(id);if(!c)return view404();DT.view=0;
  const cond=[[T('Età minima','Minimum age'),c.age+' '+T('anni','years')],[T('Patente','Licence'),T('B da almeno 3 anni','B, held for at least 3 years')],[T('Cauzione (pre-autorizzazione)','Security deposit (pre-authorisation)'),eur(c.dep)+' '+demoTag()],[T('Km inclusi','Km included'),c.kmDay+' '+T('al giorno','per day')+' · '+T('extra','extra')+' '+eur2(c.kmEx)+'/km'],[T('Acconto alla prenotazione','Down payment at booking'),'30%'],[T('Cancellazione','Cancellation'),T('Gratis fino a 7 giorni prima','Free up to 7 days before')],[T('Carburante','Fuel'),T('Pieno alla consegna, pieno al rientro','Full at handover, full at return')]];
  DT.cond=cond;
  return `<div class="page"><div class="wrap">
  <nav class="crumb" aria-label="breadcrumb"><a href="#/">Home</a><span>/</span><a href="#/noleggio">${T('Noleggio','Rental')}</a><span>/</span><span>${carName(c)}</span></nav>
  <div class="detail"><div class="d-main">${viewerBlock(c)}
   <div class="d-tabs"><div class="tabs" role="tablist">${[['tariffe',T('Tariffe','Rates')],['spec',T('Scheda tecnica','Tech specs')],['cond',T('Condizioni','Terms')],['extra',T('Servizi extra','Extras')],['lungo',T('Lungo termine','Long-term')]].map(t=>`<button role="tab" data-act="dtTab" data-t="${t[0]}" aria-selected="${DT.tab===t[0]}">${t[1]}</button>`).join('')}</div>
    <div id="dtPanel">${dtPanel(c)}</div></div></div>
   <aside class="buybox"><div><p class="bb-brand">${c.brand} · ${T(CATS[c.cat].it,CATS[c.cat].en)}</p><h1>${c.model}</h1></div>
    <div class="bb-live">${liveBadge(c)}<span class="xs dim">${T('agg.','upd.')} <span data-live-time>${fmtD(new Date(),{hour:'2-digit',minute:'2-digit',second:'2-digit'})}</span> · ${T('simulato','simulated')}</span></div>
    <div class="bigprice"><span class="num">${eur(c.day)}</span><span class="muted">/${T('giorno','day')}</span>${demoTag()}</div>
    <div class="card bb-card"><div class="grid2" style="gap:10px"><label class="field"><span>${T('Ritiro','Pick-up')}</span><input class="inp" type="date" data-chg="dtDate" name="from" min="${iso(TODAY)}" value="${DT.from}"></label><label class="field"><span>${T('Riconsegna','Return')}</span><input class="inp" type="date" data-chg="dtDate" name="to" min="${iso(TODAY)}" value="${DT.to}"></label></div>
     <div id="dtQuote" aria-live="polite">${dtQuote(c)}</div>
     <a class="btn btn-primary btn-block" id="dtBook" href="#/prenota/${c.id}${DT.from&&DT.to&&DT.to>=DT.from?'?da='+DT.from+'&a='+DT.to:''}">${T('Prenota questa auto','Book this car')} ${IC.arrow}</a>
     <div class="grid2" style="gap:8px"><a class="btn btn-line btn-sm" href="#/agenda?tipo=ritiro&auto=${c.id}">${IC.cal}<span>${T('Fissa il ritiro','Book pick-up')}</span></a><a class="btn btn-line btn-sm" href="#/preventivo?tipo=breve&auto=${c.id}">${T('Preventivo','Quote')}</a></div></div>
    <div class="bb-actions"><button class="chip" data-act="fav" data-id="${c.id}" aria-pressed="${favs().includes(c.id)}">${IC.heart}${T('Preferiti','Favourite')}</button><button class="chip" data-act="cmp" data-id="${c.id}" aria-pressed="${CMP.includes(c.id)}">${IC.cmp}${T('Confronta','Compare')}</button><button class="chip" data-act="share" data-title="${esc(carName(c))} – MC Milano">${IC.share}${T('Condividi','Share')}</button><a class="chip" href="#/vendita/${c.id}">${T('Acquisto','Purchase')} →</a></div>
    ${waBtn('btn-line btn-block',T('Domande? Scrivici su WhatsApp','Questions? Message us on WhatsApp'),'Salve MC, vorrei informazioni sul noleggio di '+carName(c)+'.')}
   </aside></div>
  <section class="sec tight"><div class="sec-head"><div><p class="eyebrow">${T('Altre auto','More cars')}</p><h2>${T('Potrebbe <em>piacerti anche.</em>','You might <em>also like.</em>')}</h2></div></div><div class="cars">${CARS.filter(x=>x.id!==c.id).slice(0,3).map(x=>carCard(x)).join('')}</div></section>
  </div></div>`}
function dtPanel(c){
  if(DT.tab==='tariffe')return tierTable(c);
  if(DT.tab==='spec')return specGrid(c);
  if(DT.tab==='lungo')return ltBox(c);
  if(DT.tab==='cond')return `<ul class="list-check">${(DT.cond||[]).map(r=>`<li><span><b>${r[0]}:</b> ${r[1]}</span></li>`).join('')}</ul><p class="xs dim" style="margin-top:10px">${T('Condizioni di esempio, da definire con MC.','Sample terms, to be agreed with MC.')}</p>`;
  return `<div class="stack-s">${EXTRAS.map(e=>`<div class="extra" style="cursor:default"><div><b>${T(e.it,e.en)}</b><small>${T(e.dIt,e.dEn)}</small></div><span class="num">${eur(extraPrice(c,e))}<small class="muted"> ${e.per==='day'?'/'+T('giorno','day'):T('una tantum','one-off')}</small></span></div>`).join('')}</div><p class="xs dim" style="margin-top:10px">${T('Prezzi di esempio. Gli extra si scelgono durante la prenotazione.','Sample prices. Extras are chosen during booking.')}</p>`}
function dtQuote(c){if(!DT.from||!DT.to)return `<p class="small muted">${T('Scegli le date per vedere disponibilità, totale, km inclusi e cauzione.','Pick dates to see availability, total, included km and deposit.')}</p>`;
  if(DT.to<DT.from)return `<p class="err" role="alert">${T('La riconsegna deve essere dopo il ritiro.','Return must be after pick-up.')}</p>`;
  const free=isFree(c,DT.from,DT.to),q=quote(c,{from:DT.from,to:DT.to});
  if(!free)return `<div class="note bad" role="alert">${T('Non disponibile in queste date.','Not available on these dates.')} ${nextFree(c)?T('Prima data libera: ','First free date: ')+fmtD(nextFree(c)):''}</div>`;
  return `<div class="rows"><div><span>${q.days} × ${eur(c.day)}</span><span>${eur(q.base)}</span></div>${q.weekend?`<div><span>${T('Supplemento weekend','Weekend surcharge')}</span><span>${eur(q.weekend)}</span></div>`:''}${q.disc?`<div class="neg"><span>${T('Sconto durata','Duration discount')} −${Math.round(q.discPct*100)}%</span><span>−${eur(q.disc)}</span></div>`:''}
   <div><span>${T('Km inclusi','Km included')}</span><span>${num(q.km)} km</span></div><div><span>${T('Cauzione (non incassata)','Deposit (not charged)')}</span><span>${eur(c.dep)}</span></div><div class="tot"><span>${T('Totale','Total')} ${demoTag()}</span><span>${eur(q.total)}</span></div></div>`}
ACT.dtTab=el=>{DT.tab=el.dataset.t;$$('[data-act=dtTab]').forEach(b=>b.setAttribute('aria-selected',b===el));$('#dtPanel').innerHTML=dtPanel(carBy(curId()))};
CHG.dtDate=el=>{DT[el.name]=el.value;const c=carBy(curId());$('#dtQuote').innerHTML=dtQuote(c);const ok=DT.from&&DT.to&&DT.to>=DT.from;$('#dtBook').href='#/prenota/'+c.id+(ok?'?da='+DT.from+'&a='+DT.to:'')};
const curId=()=>location.hash.split('?')[0].split('/')[2];
function view404(){return `<div class="page"><div class="wrap"><div class="empty" style="margin:40px 0"><b>${T('Pagina non trovata','Page not found')}</b><p>${T('Il link non corrisponde a nessuna pagina del prototipo.','This link does not match any page of the prototype.')}</p><a class="btn btn-primary btn-sm" href="#/">${T('Torna alla home','Back to home')}</a></div></div></div>`}
