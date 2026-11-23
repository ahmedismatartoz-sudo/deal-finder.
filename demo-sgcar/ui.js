/* MC prototipo — guscio, componenti, home, catalogo noleggio, scheda auto */
'use strict';
const $=(s,r)=>(r||document).querySelector(s),$$=(s,r)=>[...(r||document).querySelectorAll(s)];
const ACT={},CHG={},FORM={};
const IC={
  heart:'<svg viewBox="0 0 24 24"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/></svg>',
  cmp:'<svg viewBox="0 0 24 24"><path d="M8 4v16M16 4v16M3 8h5M16 16h5M3 16h5M16 8h5"/></svg>',
  arrow:'<svg class="arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  wa:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2zm0 18.2a8.200 8.200 0 0 1-4.200-1.150l-.3-.18-2.970.78.790-2.900-.2-.3A8.200 8.200 0 1 1 12 20.200zm4.500-6.150c-.250-.120-1.460-.720-1.690-.800-.230-.080-.390-.120-.560.120-.160.250-.640.800-.780.970-.140.160-.290.180-.540.060-.250-.120-1.040-.380-1.980-1.220-.730-.650-1.230-1.460-1.370-1.700-.140-.250-.020-.380.110-.500.110-.110.250-.290.370-.430.120-.140.160-.250.250-.410.080-.160.040-.310-.020-.430-.060-.120-.560-1.340-.760-1.840-.200-.480-.410-.420-.560-.420h-.480c-.160 0-.430.060-.660.310-.230.250-.860.840-.860 2.050 0 1.210.880 2.380 1 2.540.120.160 1.730 2.650 4.200 3.710.590.250 1.040.400 1.400.520.590.190 1.120.160 1.540.100.470-.070 1.460-.600 1.660-1.170.200-.580.200-1.070.140-1.170-.060-.110-.220-.170-.470-.290z"/></svg>',
  send:'<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12l16-8-6 16-3-7z"/></svg>'
};
const waBtn=(cls,txt)=>`<a class="btn ${cls||'btn-line'}" href="${BIZ.wa}" target="_blank" rel="noopener">${IC.wa}${txt||T('Scrivici su WhatsApp','Message us on WhatsApp')}</a>`;
const demoTag=()=>`<span class="demo-tag">${T('esempio','example')}</span>`;
const prod=t=>`<p class="prod">${T('In produzione: ','In production: ')}${t}</p>`;

/* ---------- toast e modale ---------- */
function toast(m){const t=document.createElement('div');t.className='toast';t.textContent=m;$('#toasts').appendChild(t);setTimeout(()=>{t.classList.add('out');setTimeout(()=>t.remove(),320)},3200)}
let lastFocus=null;
function openModal(html,narrow){lastFocus=document.activeElement;const m=$('#modal');m.innerHTML=`<div class="sheet ${narrow?'narrow':''}" role="dialog" aria-modal="true">${html}</div>`;m.hidden=false;document.body.style.overflow='hidden';const f=$('.x',m);f&&f.focus()}
function closeModal(){const m=$('#modal');if(m.hidden)return;m.hidden=true;m.innerHTML='';document.body.style.overflow='';lastFocus&&lastFocus.focus&&lastFocus.focus()}
ACT.closeModal=closeModal;

/* ---------- guscio ---------- */
const HOURS=()=>[[T('Lunedì','Monday'),'9:00 – 18:00',0],[T('Martedì – Venerdì','Tuesday – Friday'),'9:00 – 18:00',1],[T('Sabato','Saturday'),'10:00 – 17:00',1],[T('Domenica','Sunday'),T('Su appuntamento','By appointment'),1]];
function renderShell(route){
  document.documentElement.lang=LANG;
  $('#ribbon').innerHTML=`<i></i>${T('Prototipo dimostrativo · dati di esempio','Demo prototype · sample data')}`;
  const nav=[['#/noleggio',T('Noleggio','Rental')],['#/vendita',T('Vendita','Sales')]],demo=[['#/progetto',T('Il progetto','The project')],['#/admin','Dashboard'],['#/account',T('Area cliente','My account')]];
  $('#top').innerHTML=`<div class="wrap"><a class="logo" href="#/" aria-label="MC home"><span>MC</span><b></b><small>Milano<br>${T('Auto speciali','Special cars')}</small></a>
   <nav class="nav" id="nav" aria-label="${T('Principale','Main')}">${nav.map(n=>`<a href="${n[0]}" class="${route.indexOf(n[0].slice(1))===0?'on':''}">${n[1]}</a>`).join('')}<a href="#contatti" data-act="goContact">${T('Contatti','Contact')}</a><a class="nav-res" href="#/account">${T('Area riservata','Client area')}</a></nav>
   <div class="lang" role="group" aria-label="${T('Lingua','Language')}"><button data-act="lang" data-l="it" aria-pressed="${LANG==='it'}">IT</button><button data-act="lang" data-l="en" aria-pressed="${LANG==='en'}">EN</button></div>
   ${waBtn('btn-white btn-sm btn-wa','WhatsApp')}
   <button class="burger" data-act="menu" aria-label="Menu" aria-expanded="false" aria-controls="nav"><span></span></button></div>`;
  $('#foot').innerHTML=`<div class="wrap"><div class="foot-grid">
   <div><a class="logo" href="#/"><span>MC</span><b></b></a><p class="muted" style="margin:16px 0 18px;max-width:34ch">${T('Auto speciali a Milano: noleggio, vendita e servizi foto e marketing con le auto.','Special cars in Milan: rental, sales, and photo & marketing services with the cars.')}</p>${waBtn('btn-line btn-sm')}</div>
   <div><h4>${T('Naviga','Explore')}</h4><ul>${nav.map(n=>`<li><a href="${n[0]}">${n[1]}</a></li>`).join('')}<li><a href="#/" data-act="cookieOpen">${T('Preferenze cookie','Cookie preferences')}</a></li></ul>
    <h4 style="margin-top:26px">${T('Demo per MC','Demo for MC')}</h4><ul>${demo.map(n=>`<li><a href="${n[0]}">${n[1]}</a></li>`).join('')}</ul></div>
   <div id="contatti"><h4>${T('Contatti','Contact')}</h4><ul><li>${BIZ.addr}<br>${BIZ.cap}</li><li><a href="${BIZ.telHref}">${BIZ.tel}</a></li><li><a href="${BIZ.wa}" target="_blank" rel="noopener">WhatsApp</a></li><li><a href="https://www.google.com/maps/search/?api=1&query=Via+Arbe+95+20125+Milano" target="_blank" rel="noopener">${T('Indicazioni stradali','Get directions')}</a></li></ul></div>
   <div><h4>${T('Orari','Opening hours')}</h4><ul class="hours">${HOURS().map(h=>`<li><span>${h[0]}</span><span>${h[1]}${h[2]?`<br><small class="dim xs">${T('da confermare','to be confirmed')}</small>`:''}</span></li>`).join('')}</ul></div>
   </div><div class="foot-bot"><span>© ${new Date().getFullYear()} MC · ${BIZ.addr}, ${BIZ.cap}</span><span>${T('Prototipo dimostrativo: prezzi, disponibilità, prenotazioni e numeri sono dati di esempio. Pagamenti, firma e documenti sono simulati.','Demo prototype: prices, availability, bookings and figures are sample data. Payments, signature and documents are simulated.')}</span><span>${T('Modello 3D dimostrativo dagli esempi three.js (Ferrari 458 Italia di vicent091036). Nessun logo di marca.','Demo 3D model from the three.js examples (Ferrari 458 Italia by vicent091036). No brand logos.')}</span></div></div>`;
  $('#agentFab').innerHTML=`<i></i><span>${T('Assistenti','Assistants')}</span>`;$('#agentFab').setAttribute('aria-label',T('Apri gli assistenti AI (demo)','Open AI assistants (demo)'));
  onScrollTop();
}
function onScrollTop(){const t=$('#top');if(t)t.classList.toggle('solid',scrollY>30||!document.body.classList.contains('r-home'))}
ACT.menu=el=>{const o=$('#top').classList.toggle('open');el.setAttribute('aria-expanded',o)};
ACT.goContact=(el,e)=>{e.preventDefault();$('#top').classList.remove('open');const t=$('#contatti');t&&scrollTo({top:t.getBoundingClientRect().top+scrollY-120,behavior:'smooth'})};
ACT.lang=el=>{LANG=el.dataset.l;store.set('lang',LANG);route()};

/* ---------- card auto ---------- */
function availBadge(c,from,to){const st=fleetState()[c.id];
  if(st==='maint')return `<span class="badge bad">${T('Manutenzione','Maintenance')}</span>`;
  if(from&&to)return isFree(c,from,to)?`<span class="badge ok">${T('Libera nelle date','Free on your dates')}</span>`:`<span class="badge bad">${T('Occupata nelle date','Booked on your dates')}</span>`;
  const n=nextFree(c);if(n&&+n===+TODAY)return `<span class="badge ok">${T('Disponibile oggi','Available today')}</span>`;
  return `<span class="badge warn">${n?T('Libera dal ','Free from ')+fmtDs(n):T('Non disponibile','Unavailable')}</span>`}
function carCard(c,o){o=o||{};const sale=o.mode==='sale',f=favs().includes(c.id),q=!sale&&o.from&&o.to?quote(c,{from:o.from,to:o.to}):null;
  const href=sale?'#/vendita/'+c.id:'#/auto/'+c.id;
  return `<article class="car reveal">
  <a class="car-img" href="${href}" aria-label="${esc(carName(c))}">${carSVG(c)}
   <div class="tl"><span class="badge plain">${T(CATS[c.cat].it,CATS[c.cat].en)}</span>${sale?'':availBadge(c,o.from,o.to)}</div></a>
  <div class="car-img-tr tr" style="position:absolute;top:10px;right:10px;z-index:2;display:flex;gap:6px">
   <button class="icon-btn fav" data-act="fav" data-id="${c.id}" aria-pressed="${f}" aria-label="${T('Preferiti','Favourite')}: ${esc(carName(c))}">${IC.heart}</button>
   <button class="icon-btn" data-act="cmp" data-id="${c.id}" aria-pressed="${CMP.includes(c.id)}" aria-label="${T('Confronta','Compare')}: ${esc(carName(c))}">${IC.cmp}</button></div>
  <div class="car-body"><div><div class="brand">${c.brand} · ${paintName(c)}</div><h3><a href="${href}">${c.model}</a></h3></div>
   <div class="specs">${sale?`<span><b>${c.year}</b></span><span><b>${num(c.km)}</b> km</span><span><b>${c.cv}</b> ${PW()}</span>`:`<span><b>${c.cv}</b> ${PW()}</span><span><b>${String(c.s100).replace('.',LANG==='it'?',':'.')}</b> s 0–100</span><span><b>${c.seats}</b> ${T('posti','seats')}</span>`}<span>${T(FUELS[c.fuel].it,FUELS[c.fuel].en)}</span></div>
   <div class="car-foot"><div class="price">${sale?`<span class="num">${eur(c.sale)}</span><small>${T('prezzo di esempio','sample price')}</small>`:
     q&&q.ok?`<span class="num">${eur(q.total)}</span><small>${q.days} ${T(q.days>1?'giorni':'giorno',q.days>1?'days':'day')} · ${eur(c.day)}/${T('giorno','day')} · ${T('esempio','example')}</small>`:
     `<span class="num">${eur(c.day)}<small style="font:500 13px var(--sans)"> /${T('giorno','day')}</small></span><small>${T('tariffa di esempio','sample rate')}</small>`}</div>
    <a class="btn ${sale?'btn-line':'btn-primary'} btn-sm" href="${sale?href:'#/prenota/'+c.id+(o.from&&o.to?'?da='+o.from+'&a='+o.to:'')}">${sale?T('Scheda','Details'):T('Prenota','Book')} ${IC.arrow}</a></div></div></article>`}
ACT.fav=el=>{const on=toggleFav(el.dataset.id);$$(`[data-act=fav][data-id="${el.dataset.id}"]`).forEach(b=>b.setAttribute('aria-pressed',on));toast(on?T('Aggiunta ai preferiti','Added to favourites'):T('Tolta dai preferiti','Removed from favourites'))};
ACT.cmp=el=>{const id=el.dataset.id,i=CMP.indexOf(id);
  if(i>=0)CMP.splice(i,1);else{if(CMP.length>=3){toast(T('Puoi confrontare al massimo 3 auto','You can compare up to 3 cars'));return}CMP.push(id)}
  $$(`[data-act=cmp][data-id="${id}"]`).forEach(b=>b.setAttribute('aria-pressed',CMP.includes(id)));renderTray()};
function renderTray(){const t=$('#cmpTray');if(!CMP.length){t.hidden=true;return}t.hidden=false;
  t.innerHTML=`<span>${CMP.length}/3 · ${CMP.map(i=>carBy(i).model).join(' · ')}</span><button class="btn btn-white btn-sm" data-act="cmpOpen" ${CMP.length<2?'disabled':''}>${T('Confronta','Compare')}</button><button class="x" data-act="cmpClear" aria-label="${T('Svuota','Clear')}" style="width:36px;height:36px">×</button>`}
ACT.cmpClear=()=>{CMP=[];$$('[data-act=cmp]').forEach(b=>b.setAttribute('aria-pressed',false));renderTray()};
ACT.cmpOpen=()=>{const cs=CMP.map(carBy),sale=location.hash.indexOf('#/vendita')===0,d=LANG==='it'?',':'.';
  const row=(l,f)=>`<tr><th scope="row">${l}</th>${cs.map(c=>`<td>${f(c)}</td>`).join('')}</tr>`;
  openModal(`<div class="sheet-head"><div><p class="eyebrow">${T('Confronto','Comparison')}</p><h2 style="font-size:40px;margin-top:10px">${T('Fianco a fianco','Side by side')}</h2></div><button class="x" data-act="closeModal" aria-label="${T('Chiudi','Close')}">×</button></div>
  <div class="tbl-wrap"><table class="tbl cmp-tbl" style="min-width:${cs.length*132+96}px"><thead><tr><th></th>${cs.map(c=>`<th><div class="cmp-img">${carSVG(c)}</div><b>${carName(c)}</b></th>`).join('')}</tr></thead><tbody>
  ${row(T('Noleggio al giorno','Rental per day'),c=>eur(c.day)+' '+demoTag())}${row(T('Prezzo di vendita','Sale price'),c=>eur(c.sale)+' '+demoTag())}
  ${row(T('Potenza','Power'),c=>c.cv+' '+PW())}${row('0–100 km/h',c=>String(c.s100).replace('.',d)+' s')}${row(T('Velocità massima','Top speed'),c=>c.vmax+' km/h')}
  ${row(T('Motore','Engine'),c=>T(c.eng.it,c.eng.en))}${row(T('Cambio','Gearbox'),c=>T(c.gear.it,c.gear.en))}${row(T('Trazione','Drive'),c=>T(c.drive.it,c.drive.en))}
  ${row(T('Alimentazione','Fuel'),c=>T(FUELS[c.fuel].it,FUELS[c.fuel].en))}${row(T('Posti','Seats'),c=>c.seats)}${row(T('Cauzione','Security deposit'),c=>eur(c.dep)+' '+demoTag())}${row(T('Km inclusi al giorno','Km included per day'),c=>c.kmDay)}
  ${row('',c=>`<a class="btn btn-primary btn-sm" data-act="closeModal" href="${sale?'#/vendita/'+c.id:'#/prenota/'+c.id}">${sale?T('Scheda','Details'):T('Prenota','Book')}</a>`)}
  </tbody></table></div>`)};

/* ---------- reveal ---------- */
let io=null;
function observeReveal(){const all=$$('.reveal:not(.in)');if(!('IntersectionObserver' in window)){all.forEach(e=>e.classList.add('in'));return}
  if(!io)io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target)}}),{rootMargin:'0px 0px 8% 0px'});
  all.forEach((e,i)=>{e.style.transitionDelay=Math.min(i%6,5)*50+'ms';const r=e.getBoundingClientRect();if(r.top<innerHeight&&r.bottom>0)e.classList.add('in');else io.observe(e)});
  /* rete di sicurezza: dopo 1,5 s niente resta invisibile */
  setTimeout(()=>all.forEach(e=>{if(!e.classList.contains('in')){const r=e.getBoundingClientRect();if(r.top<innerHeight*1.2)e.classList.add('in')}}),1500)}
addEventListener('scroll',()=>{if(observeReveal.t)return;observeReveal.t=setTimeout(()=>{observeReveal.t=0;$$('.reveal:not(.in)').forEach(e=>{const r=e.getBoundingClientRect();if(r.top<innerHeight&&r.bottom>0)e.classList.add('in')})},200)},{passive:true});

/* ================= HOME ================= */
let searchTab='rent';
function viewHome(){
  const hero=CARS[0],d=LANG==='it'?',':'.';
  return `<section class="hero" id="hero"><div class="hero-stage">
    <div class="hero-fb" aria-hidden="true"><div class="wallmark">MC</div><div class="tube"></div><div class="floor"></div>
      <div class="carwrap">${carSVG(hero)}</div><div class="mirror">${carSVG(hero)}</div></div>
    <canvas id="heroCanvas" aria-hidden="true"></canvas>
    <div class="hero-prog" aria-hidden="true"><i id="heroProg"></i></div>
    <div class="hero-cap" id="heroCap"><div class="wrap"><p class="eyebrow">${T('Sei auto, un solo standard','Six cars, one standard')}</p><h2 style="margin-top:14px">${T('Guardala da ogni lato.','See it from every side.')}</h2><p>${T('Ogni vettura ha la sua scheda, con colori e dettagli.','Every car has its own page, with colours and details.')}</p></div></div>
    <div class="hero-ui"><div class="wrap"><div class="hero-copy" id="heroCopy">
      <div class="mark" aria-hidden="true"><span>MC</span><b></b></div>
      <h1><span class="sr">MC · </span>${T('Noleggio e vendita di auto speciali a Milano','Special cars for rent and sale in Milan')}</h1>
      <p class="tag">${BIZ.addr} · ${T('Supercar, SUV sportivi, wagon: per un giorno o per sempre.','Supercars, performance SUVs, estates: for a day or for good.')}</p>
      <div class="cta"><a class="btn btn-primary" href="#/noleggio">${T('Scegli la tua auto','Choose your car')} ${IC.arrow}</a>${waBtn('btn-line')}</div>
    </div></div></div>
    <div class="hero-meta" id="heroMeta"><div><b class="num">${hero.cv} ${PW()}</b><span>${carName(hero)}</span></div><div><b class="num">${String(hero.s100).replace('.',d)} s</b><span>0–100 km/h</span></div><div><b class="num">6</b><span>${T('auto in flotta','cars in the fleet')}</span></div></div>
    <div class="hero-load" id="heroLoad"><span>${T('Scena 3D','3D scene')}</span><i></i></div>
  </div></section>

  <section class="search"><div class="wrap"><div class="search-box reveal">
    <div class="seg" role="tablist" aria-label="${T('Cosa cerchi','What are you looking for')}"><button role="tab" data-act="searchTab" data-t="rent" aria-selected="${searchTab==='rent'}">${T('Noleggio','Rental')}</button><button role="tab" data-act="searchTab" data-t="buy" aria-selected="${searchTab==='buy'}">${T('Acquisto','Purchase')}</button></div>
    <div id="searchForm">${searchForm()}</div></div></div></section>

  <section class="sec tight"><div class="wrap"><div class="split">
    <a class="path reveal" href="#/noleggio">${carSVG(CARS[0],{cls:'sil'})}<p class="eyebrow">01 · ${T('Noleggio','Rental')}</p><h3>${T('Guidala per un giorno o un mese','Drive it for a day or a month')}</h3><p class="muted">${T('Prenotazione online in dieci passaggi, consegna a domicilio o in aeroporto.','Online booking in ten steps, delivery to your door or the airport.')}</p><span class="link" style="justify-self:start">${T('Vai al noleggio','Go to rental')} →</span></a>
    <a class="path reveal" href="#/vendita">${carSVG(CARS[4],{cls:'sil'})}<p class="eyebrow">02 · ${T('Vendita','Sales')}</p><h3>${T('Portala a casa per sempre','Take it home for good')}</h3><p class="muted">${T('Test drive, permuta, richiesta di finanziamento e preventivo personalizzato.','Test drive, trade-in, finance request and tailored quote.')}</p><span class="link" style="justify-self:start">${T('Vai alla vendita','Go to sales')} →</span></a>
  </div></div></section>

  <section class="sec tight"><div class="wrap"><div class="sec-head"><div><p class="eyebrow">${T('In evidenza','Featured')}</p><h2>${T('La flotta','The fleet')}</h2></div><a class="link" href="#/noleggio">${T('Tutte le auto','All cars')} →</a></div></div>
    <div class="hscroll" tabindex="0" aria-label="${T('Auto in evidenza','Featured cars')}">${CARS.map(c=>carCard(c)).join('')}</div></section>

  <section class="sec tight"><div class="wrap"><div class="sec-head"><div><p class="eyebrow">${T('Categorie','Categories')}</p><h2>${T('Scegli il carattere','Pick a character')}</h2></div></div>
    <div class="cats">${Object.keys(CATS).map(k=>{const cs=CARS.filter(c=>c.cat===k);return `<a class="cat reveal" href="#/noleggio" data-act="goCat" data-cat="${k}">${carSVG(cs[0])}<h3>${T(CATS[k].it,CATS[k].en)}</h3><span>${cs.length} ${T('auto','cars')} · ${T('da','from')} ${eur(Math.min(...cs.map(c=>c.day)))}/${T('giorno','day')} ${demoTag()}</span></a>`}).join('')}</div></div></section>

  <section class="sec"><div class="wrap"><div class="sec-head"><div><p class="eyebrow">${T('Immagine','Image')}</p><h2>${T('Le auto, anche come set','The cars as a set, too')}</h2><p class="lead">${T('Oltre a noleggio e vendita, MC mette le auto al servizio di foto, video ed eventi.','Beyond rental and sales, MC puts its cars to work for photos, videos and events.')}</p></div></div>
    <div class="feat">${[
      [T('Shooting foto e video','Photo & video shoots'),T('Auto, location e fotografo per contenuti social, cataloghi e campagne.','Car, location and photographer for social content, lookbooks and campaigns.')],
      [T('Eventi e cerimonie','Events & weddings'),T('L\'auto giusta per matrimoni, inaugurazioni ed eventi aziendali.','The right car for weddings, openings and corporate events.')],
      [T('Marketing per brand','Brand marketing'),T('Le vetture come scenografia per lanci di prodotto, videoclip e spot.','Cars as a backdrop for product launches, music videos and ads.')]
     ].map((f,i)=>`<div class="reveal"><span class="n">0${i+1}</span><h3>${f[0]}</h3><p>${f[1]}</p></div>`).join('')}</div>
    <div class="grid3" style="margin-top:18px">${[1,2,3].map(()=>`<div class="ph reveal" style="aspect-ratio:4/3"><div><b>${T('Qui le vostre foto','Your photos here')}</b>${T('Scatti reali di shooting ed eventi','Real shots from shoots and events')}</div></div>`).join('')}</div></div></section>

  <section class="sec tight"><div class="wrap"><div class="sec-head"><div><p class="eyebrow">${T('Perché MC','Why MC')}</p><h2>${T('Fatto come si deve','Done properly')}</h2></div></div>
    <div class="feat">${[
      [T('Prezzo chiaro prima di pagare','Clear price before you pay'),T('Giorni, extra, consegna e cauzione: il totale si aggiorna a ogni scelta.','Days, extras, delivery and deposit: the total updates with every choice.')],
      [T('Tutto dal telefono','All from your phone'),T('Documenti, acconto e firma del contratto senza passare in sede.','Documents, down payment and contract signature without visiting us.')],
      [T('Consegna dove vuoi','Delivered where you want'),T('In sede in Via Arbe, a domicilio a Milano o in aeroporto.','At our Via Arbe base, at your door in Milan or at the airport.')],
      [T('Auto curate una per una','Cars cared for one by one'),T('Poche vetture, preparate e controllate prima di ogni consegna.','A small fleet, prepared and checked before every handover.')],
      [T('Una persona vera, sempre','A real person, always'),T('WhatsApp e telefono: quando serve risponde il titolare, non un call center.','WhatsApp and phone: when it matters the owner answers, not a call centre.')],
      [T('Noleggio o acquisto','Rent or buy'),T('Provi l\'auto a noleggio e, se ti conquista, puoi chiedere un preventivo d\'acquisto.','Try the car as a rental and, if it wins you over, ask for a purchase quote.')]
     ].map((f,i)=>`<div class="reveal"><span class="n">0${i+1}</span><h3>${f[0]}</h3><p>${f[1]}</p></div>`).join('')}</div></div></section>

  <section class="sec"><div class="wrap"><div class="sec-head"><div><p class="eyebrow">${T('Come funziona','How it works')}</p><h2>${T('Dalla scelta alle chiavi','From choice to keys')}</h2></div><a class="btn btn-line" href="#/prenota/${CARS[0].id}">${T('Prova il percorso','Try the flow')} ${IC.arrow}</a></div>
    <ol class="steps">${[
      [T('Scegli auto e date','Pick car and dates'),T('Vedi subito disponibilità e totale.','See availability and total right away.')],
      [T('Ritiro ed extra','Pick-up and extras'),T('Sede, domicilio o aeroporto; km, secondo guidatore, shooting.','Showroom, home or airport; mileage, extra driver, shoot.')],
      [T('Documenti e acconto','Documents and down payment'),T('Carichi patente e documento, paghi l\'acconto.','Upload licence and ID, pay the down payment.')],
      [T('Firma e ritira','Sign and collect'),T('Firmi il contratto online e ricevi le istruzioni.','Sign the contract online and get your instructions.')]
     ].map(s=>`<li class="reveal"><h3>${s[0]}</h3><p>${s[1]}</p></li>`).join('')}</ol></div></section>

  <section class="sec tight"><div class="wrap"><div class="sec-head"><div><p class="eyebrow">${T('Recensioni','Reviews')}</p><h2>${T('La parola ai clienti','What clients say')}</h2></div></div>
    <div class="review reveal"><div class="stars" aria-hidden="true">★★★★★</div><b style="font:600 22px/1.1 var(--disp);text-transform:uppercase">${T('Qui le recensioni Google reali','Real Google reviews go here')}</b><q>${T('Nessuna recensione inventata: qui compariranno valutazione media e recensioni pubblicate dai clienti sul profilo Google di MC.','No made-up reviews: the average rating and the reviews published by clients on MC\'s Google profile will appear here.')}</q></div></div></div></section>

  <section class="sec tight"><div class="wrap" style="max-width:920px"><div class="sec-head"><div><p class="eyebrow">FAQ</p><h2>${T('Domande frequenti','Common questions')}</h2></div></div>
    <div class="faq">${FAQ().map(f=>`<details class="reveal"><summary>${f[0]}</summary><p>${f[1]}</p></details>`).join('')}</div>
    <p class="small dim" style="margin-top:16px">${T('Condizioni di esempio: quelle definitive le stabilisce MC.','Sample terms: the final ones are set by MC.')}</p></div></section>

  <section class="sec tight"><div class="wrap"><div class="cta-band reveal"><p class="eyebrow">${T('Pronto?','Ready?')}</p><h2>${T('La prossima auto la scegli tu.','Your next car is your call.')}</h2><p class="lead">${BIZ.addr}, ${BIZ.cap} · ${BIZ.tel}</p>
    <div class="cta"><a class="btn btn-primary" href="#/noleggio">${T('Prenota un\'auto','Book a car')} ${IC.arrow}</a><a class="btn btn-white" href="#/vendita">${T('Guarda le auto in vendita','See cars for sale')}</a>${waBtn('btn-line')}</div></div></div></section>`;
}
const FAQ=()=>[
  [T('Cosa serve per noleggiare?','What do I need to rent?'),T('Patente valida, documento d\'identità e una carta di credito per la cauzione. L\'età minima cambia da auto ad auto ed è indicata in ogni scheda.','A valid driving licence, an ID document and a credit card for the security deposit. Minimum age varies by car and is shown on each page.')],
  [T('Come funziona la cauzione?','How does the security deposit work?'),T('È una pre-autorizzazione sulla carta: non viene incassata e si sblocca alla riconsegna se l\'auto rientra senza danni.','It is a pre-authorisation on your card: it is not charged and is released at return if the car comes back undamaged.')],
  [T('Posso annullare?','Can I cancel?'),T('Sì. Fino a 7 giorni prima l\'acconto viene rimborsato per intero, da 7 a 3 giorni al 50%, sotto i 3 giorni non è rimborsabile.','Yes. Up to 7 days before, the down payment is fully refunded; from 7 to 3 days, 50%; under 3 days it is non-refundable.')],
  [T('Consegnate l\'auto a domicilio o in aeroporto?','Do you deliver to my door or the airport?'),T('Sì: a Milano, a Linate, Malpensa e Bergamo Orio al Serio, con un supplemento che vedi nel totale prima di pagare.','Yes: in Milan and at Linate, Malpensa and Bergamo Orio al Serio, for a fee shown in the total before you pay.')],
  [T('Quanti chilometri sono inclusi?','How many kilometres are included?'),T('Ogni auto ha un numero di km al giorno inclusi; puoi aggiungere i chilometri illimitati tra gli extra.','Each car includes a daily km allowance; you can add unlimited mileage as an extra.')],
  [T('Posso usare l\'auto per uno shooting o un evento?','Can I use the car for a shoot or an event?'),T('Certo: è uno dei servizi di MC. Aggiungi lo shooting tra gli extra o scrivici su WhatsApp per un progetto su misura.','Of course: it is one of MC\'s services. Add the shoot as an extra or message us on WhatsApp for a tailored project.')]
];
function searchForm(){
  if(searchTab==='rent')return `<form class="search-form" data-form="searchRent" novalidate>
    <label class="field"><span>${T('Ritiro','Pick-up')}</span><input class="inp" type="date" name="from" min="${iso(TODAY)}" value="${RF.from}"></label>
    <label class="field"><span>${T('Riconsegna','Return')}</span><input class="inp" type="date" name="to" min="${iso(TODAY)}" value="${RF.to}"></label>
    <label class="field"><span>${T('Categoria','Category')}</span><select class="inp" name="cat"><option value="">${T('Tutte','All')}</option>${Object.keys(CATS).map(k=>`<option value="${k}">${T(CATS[k].it,CATS[k].en)}</option>`).join('')}</select></label>
    <label class="field"><span>${T('Marca','Brand')}</span><select class="inp" name="brand"><option value="">${T('Tutte','All')}</option>${[...new Set(CARS.map(c=>c.brand))].map(b=>`<option>${b}</option>`).join('')}</select></label>
    <button class="btn btn-primary">${T('Cerca disponibilità','Check availability')} ${IC.arrow}</button></form>`;
  return `<form class="search-form" data-form="searchBuy" novalidate>
    <label class="field"><span>${T('Marca','Brand')}</span><select class="inp" name="brand"><option value="">${T('Tutte','All')}</option>${[...new Set(CARS.map(c=>c.brand))].map(b=>`<option>${b}</option>`).join('')}</select></label>
    <label class="field"><span>${T('Budget massimo','Max budget')}</span><select class="inp" name="max"><option value="">${T('Nessun limite','No limit')}</option>${[70000,100000,150000,400000].map(v=>`<option value="${v}">${eur(v)}</option>`).join('')}</select></label>
    <label class="field"><span>${T('Alimentazione','Fuel')}</span><select class="inp" name="fuel"><option value="">${T('Tutte','All')}</option>${Object.keys(FUELS).map(k=>`<option value="${k}">${T(FUELS[k].it,FUELS[k].en)}</option>`).join('')}</select></label>
    <label class="field"><span>${T('Km massimi','Max km')}</span><select class="inp" name="km"><option value="">${T('Nessun limite','No limit')}</option>${[10000,30000,50000].map(v=>`<option value="${v}">${num(v)} km</option>`).join('')}</select></label>
    <button class="btn btn-primary">${T('Cerca auto','Search cars')} ${IC.arrow}</button></form>`}
ACT.searchTab=el=>{searchTab=el.dataset.t;$$('[data-act=searchTab]').forEach(b=>b.setAttribute('aria-selected',b===el));$('#searchForm').innerHTML=searchForm()};
FORM.searchRent=f=>{const d=new FormData(f);let from=d.get('from'),to=d.get('to');if(from&&to&&to<from){toast(T('La riconsegna deve essere dopo il ritiro','Return must be after pick-up'));return}
  Object.assign(RF,{from:from||'',to:to||'',cat:d.get('cat'),brand:d.get('brand')});location.hash='#/noleggio'};
FORM.searchBuy=f=>{const d=new FormData(f);Object.assign(SF,{brand:d.get('brand'),max:+d.get('max')||SF0.max,fuel:d.get('fuel'),km:+d.get('km')||SF0.km});location.hash='#/vendita'};
ACT.goCat=el=>{Object.assign(RF,{cat:el.dataset.cat})};

/* ================= CATALOGO NOLEGGIO ================= */
const RF0={q:'',brand:'',cat:'',fuel:'',max:2000,from:'',to:'',sort:'rel',free:false};
const RF=Object.assign({},RF0);
function rentList(){let l=CARS.filter(c=>(!RF.brand||c.brand===RF.brand)&&(!RF.cat||c.cat===RF.cat)&&(!RF.fuel||c.fuel===RF.fuel)&&c.day<=RF.max&&(!RF.q||carName(c).toLowerCase().includes(RF.q.toLowerCase())));
  const dates=RF.from&&RF.to&&RF.to>=RF.from;
  if(RF.free)l=l.filter(c=>dates?isFree(c,RF.from,RF.to):fleetState()[c.id]!=='maint');
  const s={rel:(a,b)=>0,up:(a,b)=>a.day-b.day,down:(a,b)=>b.day-a.day,cv:(a,b)=>b.cv-a.cv}[RF.sort];
  l=l.slice().sort(s);if(RF.sort==='rel'&&dates)l.sort((a,b)=>(isFree(b,RF.from,RF.to)?1:0)-(isFree(a,RF.from,RF.to)?1:0));return l}
const skelCards=n=>`<div class="cars">${Array.from({length:n},()=>`<div class="skel" style="height:420px"></div>`).join('')}</div>`;
function rentResults(){const l=rentList(),dates=RF.from&&RF.to&&RF.to>=RF.from;
  if(RF.from&&RF.to&&RF.to<RF.from)return `<div class="note bad" role="alert">${T('La data di riconsegna viene prima del ritiro: correggi le date per vedere disponibilità e totale.','The return date is before pick-up: fix the dates to see availability and totals.')}</div>`;
  if(!l.length)return `<div class="empty"><b>${T('Nessuna auto con questi filtri','No cars match these filters')}</b><p>${T('Prova ad allargare il prezzo o a cambiare le date.','Try widening the price or changing the dates.')}</p><button class="btn btn-line btn-sm" data-act="rentReset">${T('Azzera i filtri','Reset filters')}</button></div>`;
  return `<div class="cars">${l.map(c=>carCard(c,dates?{from:RF.from,to:RF.to}:{})).join('')}</div>`}
function viewRent(){const brands=[...new Set(CARS.map(c=>c.brand))];
  const sel=(n,opts,all)=>`<select class="inp" data-chg="rf" name="${n}"><option value="">${all}</option>${opts.map(o=>`<option value="${o[0]}" ${RF[n]===o[0]?'selected':''}>${o[1]}</option>`).join('')}</select>`;
  return `<div class="page"><div class="wrap">
  <div class="page-head"><p class="eyebrow">${T('Noleggio','Rental')}</p><h1>${T('Scegli la tua auto','Choose your car')}</h1><p class="lead">${T('Sei auto speciali, con disponibilità e totale calcolati sulle tue date.','Six special cars, with availability and total worked out on your dates.')} ${demoTag()}</p></div>
  <div class="cat-layout">
   <aside class="filters" id="filters" aria-label="${T('Filtri','Filters')}"><h3>${T('Filtri','Filters')}<button class="link xs" data-act="rentReset">${T('Azzera','Reset')}</button></h3>
    <label class="field"><span>${T('Cerca modello','Search model')}</span><input class="inp" type="search" data-chg="rf" name="q" value="${esc(RF.q)}" placeholder="RS6, Ferrari…"></label>
    <div class="grid2" style="gap:10px"><label class="field"><span>${T('Ritiro','Pick-up')}</span><input class="inp" type="date" data-chg="rf" name="from" min="${iso(TODAY)}" value="${RF.from}"></label>
    <label class="field"><span>${T('Riconsegna','Return')}</span><input class="inp" type="date" data-chg="rf" name="to" min="${iso(TODAY)}" value="${RF.to}"></label></div>
    <label class="field"><span>${T('Marca','Brand')}</span>${sel('brand',brands.map(b=>[b,b]),T('Tutte','All'))}</label>
    <label class="field"><span>${T('Categoria','Category')}</span>${sel('cat',Object.keys(CATS).map(k=>[k,T(CATS[k].it,CATS[k].en)]),T('Tutte','All'))}</label>
    <label class="field"><span>${T('Alimentazione','Fuel')}</span>${sel('fuel',Object.keys(FUELS).map(k=>[k,T(FUELS[k].it,FUELS[k].en)]),T('Tutte','All'))}</label>
    <label class="field"><span>${T('Prezzo massimo al giorno','Max price per day')}: <b id="rfMax" style="color:#fff">${eur(RF.max)}</b></span><input type="range" data-chg="rf" name="max" min="250" max="2000" step="50" value="${RF.max}"></label>
    <label class="check"><input type="checkbox" data-chg="rf" name="free" ${RF.free?'checked':''}><span>${T('Solo auto disponibili','Available cars only')}</span></label>
    <p class="xs dim">${T('Disponibilità dimostrativa: in produzione arriva dal calendario reale.','Demo availability: in production it comes from the live calendar.')}</p>
   </aside>
   <div><div class="toolbar"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><button class="btn btn-line btn-sm filter-toggle" data-act="filterToggle" aria-controls="filters" aria-expanded="false">${T('Filtri','Filters')}</button><span class="muted small" id="rentCount" aria-live="polite"></span></div>
     <label class="field"><span>${T('Ordina','Sort')}</span><select class="inp" data-chg="rf" name="sort">${[['rel',T('Rilevanza','Relevance')],['up',T('Prezzo crescente','Price: low to high')],['down',T('Prezzo decrescente','Price: high to low')],['cv',T('Potenza','Power')]].map(o=>`<option value="${o[0]}" ${RF.sort===o[0]?'selected':''}>${o[1]}</option>`).join('')}</select></label></div>
    <div id="rentRes" aria-busy="true">${skelCards(3)}</div></div>
  </div></div></div>`}
let rentTimer=0;
function rentRefresh(fast){const box=$('#rentRes');if(!box)return;box.setAttribute('aria-busy','true');if(!fast)box.innerHTML=skelCards(Math.max(1,Math.min(3,rentList().length)));clearTimeout(rentTimer);
  rentTimer=setTimeout(()=>{if(!$('#rentRes'))return;box.innerHTML=rentResults();box.setAttribute('aria-busy','false');const n=rentList().length;$('#rentCount').textContent=n+' '+T(n===1?'auto':'auto',n===1?'car':'cars');observeReveal()},fast?0:420)}
CHG.rf=el=>{RF[el.name]=el.type==='checkbox'?el.checked:el.type==='range'?+el.value:el.value;if(el.name==='max')$('#rfMax').textContent=eur(RF.max);rentRefresh()};
ACT.rentReset=()=>{Object.assign(RF,RF0);route()};
ACT.filterToggle=el=>{const o=$('#filters').classList.toggle('open');el.setAttribute('aria-expanded',o)};

/* ================= SCHEDA AUTO (noleggio) ================= */
const CONFIG=[['#c8102e','Rosso','Red'],['#16171a','Nero','Black'],['#8b8f93','Grigio Nardo','Nardo Grey'],['#eceff1','Bianco','White'],['#f2c200','Giallo','Yellow'],['#1f3f8f','Blu','Blue']];
let DT={tab:'spec',hex:null,from:'',to:''};
function specGrid(c){const d=LANG==='it'?',':'.';return `<dl class="kv" style="margin:0">${[
  [T('Potenza','Power'),c.cv+' '+PW()],['0–100 km/h',String(c.s100).replace('.',d)+' s'],[T('Velocità massima','Top speed'),c.vmax+' km/h'],[T('Motore','Engine'),T(c.eng.it,c.eng.en)],
  [T('Cambio','Gearbox'),T(c.gear.it,c.gear.en)],[T('Trazione','Drive'),T(c.drive.it,c.drive.en)],[T('Alimentazione','Fuel'),T(FUELS[c.fuel].it,FUELS[c.fuel].en)],[T('Posti','Seats'),c.seats],[T('Colore','Colour'),paintName(c)]
 ].map(r=>`<div><dt>${r[0]}</dt><b>${r[1]}</b></div>`).join('')}</dl><p class="xs dim" style="margin-top:10px">${T('Dati tecnici indicativi dei modelli di serie; l\'allestimento della singola vettura va confermato.','Indicative figures for the standard models; each car\'s exact spec is to be confirmed.')}</p>`}
function viewerBlock(c){return `<div class="viewer" id="viewer"><div class="viewer-fb" id="viewerFb">${carSVG(c,{hex:DT.hex})}</div><canvas id="viewerCanvas" aria-label="${T('Modello 3D ruotabile','Rotatable 3D model')}"></canvas>
   <div class="viewer-hud"><span class="badge plain" id="viewerTag">${T('Anteprima','Preview')}</span><span class="badge plain">${T('Modello 3D dimostrativo','Demo 3D model')}</span></div>
   <div class="viewer-bar"><div class="swatches" role="group" aria-label="${T('Colore carrozzeria','Body colour')}">${CONFIG.map(k=>`<button class="swatch" style="background:${k[0]}" data-act="paint" data-hex="${k[0]}" aria-pressed="${(DT.hex||PAINTS[c.paint].hex)===k[0]}" aria-label="${T(k[1],k[2])}" title="${T(k[1],k[2])}"></button>`).join('')}</div>
   <span class="xs muted" id="viewerHint">${T('Configuratore colore','Colour configurator')}</span></div></div>
  <div class="thumbs" id="thumbs"><div class="tv">${carSVG(c,{hex:DT.hex})}<small>${T('Profilo','Side')}</small></div><div class="tv">${carSVG(c,{hex:DT.hex,vb:[c.geo.x0-20,SHAPE[c.id].ty+40,400,250]})}<small>${T('Anteriore','Front')}</small></div><div class="tv">${carSVG(c,{hex:DT.hex,vb:[c.geo.x1-380,SHAPE[c.id].ty+40,400,250]})}<small>${T('Posteriore','Rear')}</small></div><div class="ph"><div>${T('Foto reali in arrivo','Real photos coming')}</div></div></div>`}
function viewCar(id){const c=carBy(id);if(!c)return view404();DT.hex=null;
  const cond=[[T('Età minima','Minimum age'),c.age+' '+T('anni','years')],[T('Patente','Licence'),T('B da almeno 3 anni','B, held for at least 3 years')],[T('Cauzione (pre-autorizzazione)','Security deposit (pre-authorisation)'),eur(c.dep)+' '+demoTag()],[T('Km inclusi','Km included'),c.kmDay+' '+T('al giorno','per day')],[T('Acconto alla prenotazione','Down payment at booking'),'30%'],[T('Cancellazione','Cancellation'),T('Gratis fino a 7 giorni prima','Free up to 7 days before')],[T('Carburante','Fuel'),T('Pieno alla consegna, pieno al rientro','Full at handover, full at return')]];
  return `<div class="page"><div class="wrap">
  <nav class="crumb" aria-label="breadcrumb"><a href="#/">Home</a>/<a href="#/noleggio">${T('Noleggio','Rental')}</a>/<span>${carName(c)}</span></nav>
  <div class="detail" style="margin-top:20px"><div>${viewerBlock(c)}
   <div style="margin-top:34px"><div class="tabs" role="tablist">${[['spec',T('Scheda tecnica','Tech specs')],['cond',T('Condizioni di noleggio','Rental terms')],['extra',T('Servizi extra','Extras')]].map(t=>`<button role="tab" data-act="dtTab" data-t="${t[0]}" aria-selected="${DT.tab===t[0]}">${t[1]}</button>`).join('')}</div>
    <div id="dtPanel">${dtPanel(c,cond)}</div></div></div>
   <aside class="buybox"><div><div class="brand" style="font:600 12px/1 var(--sans);letter-spacing:.2em;color:var(--dim);text-transform:uppercase;margin-bottom:10px">${c.brand} · ${T(CATS[c.cat].it,CATS[c.cat].en)}</div><h1>${c.model}</h1></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">${availBadge(c)}<span class="badge plain">${paintName(c)}</span></div>
    <div class="bigprice"><span class="num">${eur(c.day)}</span><span class="muted">/${T('giorno','day')}</span>${demoTag()}</div>
    <div class="card" style="display:grid;gap:14px"><div class="grid2" style="gap:10px"><label class="field"><span>${T('Ritiro','Pick-up')}</span><input class="inp" type="date" data-chg="dtDate" name="from" min="${iso(TODAY)}" value="${DT.from}"></label><label class="field"><span>${T('Riconsegna','Return')}</span><input class="inp" type="date" data-chg="dtDate" name="to" min="${iso(TODAY)}" value="${DT.to}"></label></div>
     <div id="dtQuote" aria-live="polite">${dtQuote(c)}</div>
     <a class="btn btn-primary btn-block" id="dtBook" href="#/prenota/${c.id}">${T('Prenota questa auto','Book this car')} ${IC.arrow}</a>${waBtn('btn-line btn-block')}</div>
    <div style="display:flex;gap:10px;flex-wrap:wrap"><button class="chip" data-act="fav" data-id="${c.id}" aria-pressed="${favs().includes(c.id)}">♥ ${T('Preferiti','Favourite')}</button><button class="chip" data-act="cmp" data-id="${c.id}" aria-pressed="${CMP.includes(c.id)}">${T('Confronta','Compare')}</button><a class="chip" href="#/vendita/${c.id}">${T('È anche in vendita','Also for sale')} →</a></div>
   </aside></div>
  <section class="sec tight"><div class="sec-head"><div><p class="eyebrow">${T('Altre auto','More cars')}</p><h2 style="font-size:clamp(30px,4vw,48px)">${T('Potrebbe piacerti anche','You might also like')}</h2></div></div><div class="cars">${CARS.filter(x=>x.id!==c.id).slice(0,3).map(x=>carCard(x)).join('')}</div></section>
  </div></div>`}
function dtPanel(c,cond){
  if(DT.tab==='spec')return specGrid(c);
  if(DT.tab==='cond'){cond=cond||[];return `<ul class="list-check">${(cond.length?cond:[[T('Cauzione','Deposit'),eur(c.dep)],[T('Km inclusi','Km included'),c.kmDay+T(' al giorno',' per day')],[T('Età minima','Minimum age'),c.age]]).map(r=>`<li><span><b>${r[0]}:</b> ${r[1]}</span></li>`).join('')}</ul><p class="xs dim" style="margin-top:10px">${T('Condizioni di esempio, da definire con MC.','Sample terms, to be agreed with MC.')}</p>`}
  return `<div style="display:grid;gap:10px">${EXTRAS.map(e=>`<div class="extra" style="cursor:default"><div><b>${T(e.it,e.en)}</b><small>${T(e.dIt,e.dEn)}</small></div><span class="num">${eur(extraPrice(c,e))}<small class="muted" style="font:500 12px var(--sans)"> ${e.per==='day'?'/'+T('giorno','day'):T('una tantum','one-off')}</small></span></div>`).join('')}</div><p class="xs dim" style="margin-top:10px">${T('Prezzi di esempio. Gli extra si scelgono durante la prenotazione.','Sample prices. Extras are chosen during booking.')}</p>`}
function dtQuote(c){if(!DT.from||!DT.to)return `<p class="small muted">${T('Scegli le date per vedere disponibilità e totale.','Pick dates to see availability and total.')}</p>`;
  if(DT.to<DT.from)return `<p class="err" role="alert">${T('La riconsegna deve essere dopo il ritiro.','Return must be after pick-up.')}</p>`;
  const free=isFree(c,DT.from,DT.to),q=quote(c,{from:DT.from,to:DT.to});
  if(!free)return `<div class="note bad" role="alert">${T('Non disponibile in queste date.','Not available on these dates.')} ${nextFree(c)?T('Prima data libera: ','First free date: ')+fmtD(nextFree(c)):''}</div>`;
  return `<div class="rows"><div><span>${q.days} × ${eur(c.day)}</span><span>${eur(q.base)}</span></div>${q.weekend?`<div><span>${T('Supplemento weekend','Weekend surcharge')}</span><span>${eur(q.weekend)}</span></div>`:''}${q.disc?`<div class="neg"><span>${T('Sconto durata','Duration discount')} −${q.discPct*100}%</span><span>−${eur(q.disc)}</span></div>`:''}<div class="tot"><span>${T('Totale','Total')} ${demoTag()}</span><span>${eur(q.total)}</span></div></div>`}
ACT.dtTab=el=>{DT.tab=el.dataset.t;$$('[data-act=dtTab]').forEach(b=>b.setAttribute('aria-selected',b===el));$('#dtPanel').innerHTML=dtPanel(carBy(curId()))};
CHG.dtDate=el=>{DT[el.name]=el.value;const c=carBy(curId());$('#dtQuote').innerHTML=dtQuote(c);const ok=DT.from&&DT.to&&DT.to>=DT.from;$('#dtBook').href='#/prenota/'+c.id+(ok?'?da='+DT.from+'&a='+DT.to:'')};
ACT.paint=el=>{DT.hex=el.dataset.hex;$$('.swatch').forEach(b=>b.setAttribute('aria-pressed',b===el));const c=carBy(curId());$('#viewerFb').innerHTML=carSVG(c,{hex:DT.hex});const tb=$('#thumbs');if(tb&&!$('img',tb)){const d=document.createElement('div');d.innerHTML=viewerBlock(c);tb.innerHTML=$('#thumbs',d).innerHTML}if(window.MC3D&&MC3D.viewer)MC3D.viewer.setPaint(DT.hex)};
const curId=()=>location.hash.split('?')[0].split('/')[2];
function view404(){return `<div class="page"><div class="wrap"><div class="empty" style="margin:40px 0"><b>${T('Pagina non trovata','Page not found')}</b><p>${T('Il link non corrisponde a nessuna pagina del prototipo.','This link does not match any page of the prototype.')}</p><a class="btn btn-primary btn-sm" href="#/">${T('Torna alla home','Back to home')}</a></div></div></div>`}
