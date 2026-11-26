/* MC prototipo — funzioni aggiuntive: noleggio o acquisto, eventi, consegna, documenti, newsletter, lista desideri.
   Tutto nel browser; nessun invio automatico (newsletter: solo raccolta locale). */
'use strict';
const pageHead=(n,lab,h1,lead)=>`<div class="page-head">${lbl(n,lab)}<h1>${h1}</h1>${lead?`<p class="lead">${lead}</p>`:''}</div>`;

/* ================= NOLEGGIO O ACQUISTO? ================= */
let NA={car:CARS[1].id,m:36,km:15000};
function naCalc(){const c=carBy(NA.car),lt=ltCanone(c,NA.m,NA.km,0),f=finance({price:c.sale,down:Math.round(c.sale*.2/500)*500,months:NA.m,balloon:true,bPct:35});
  const resid=Math.round(c.sale*Math.max(.3,.8-.085*NA.m/12-(NA.km-15000)/1e6*1.6*NA.m/12));
  const buyPaid=f.A+f.rata*f.n,buyCost=buyPaid+f.B-resid,ltCost=lt.canone*NA.m,shortCost=tiers(c).month*NA.m;
  return {c,lt,f,resid,buyPaid,buyCost,ltCost,shortCost}}
function naOut(){const r=naCalc(),best=Math.min(r.buyCost,r.ltCost),col=(t,v,sub,extra)=>`<div class="na-col ${v===best?'best':''}"><h3>${t}</h3>${sup(eur(v).replace(/\s?€/,''),'€')}<p class="muted small">${sub}</p>${extra||''}</div>`;
  return `<div class="na-grid">${col(T('Acquisto con finanziamento','Purchase with finance'),Math.round(r.buyCost),T('costo d\'uso in ','cost of use over ')+NA.m+T(' mesi',' months'),sumRows([[T('Anticipo','Down payment'),eur(r.f.A)],[T('Rate','Payments'),eur2(r.f.rata)+' × '+r.f.n],[T('Maxirata','Balloon'),eur(r.f.B)],[T('Valore residuo stimato','Estimated residual value'),'−'+eur(r.resid),'neg']]))}
   ${col(T('Noleggio a lungo termine','Long-term rental'),r.ltCost,T('canoni totali, servizi inclusi','total fees, services included'),sumRows([[T('Canone','Monthly fee'),eur(r.lt.canone)+'/'+T('mese','mo')],[T('Km inclusi','Km included'),num(r.lt.kmTot)]]))}
   ${col(T('Noleggio breve, mese per mese','Short-term, month by month'),r.shortCost,T('tariffa mensile × mesi: solo per confronto','monthly rate × months: comparison only'),'')}</div>
   <div class="note warn">${T('Confronto di esempio con valori indicativi: TAN','Sample comparison with indicative values: rate')} ${pct(FIN.tan)}, ${T('maxirata 35%, anticipo 20%, valore residuo stimato. Non considera assicurazione e manutenzione nell\'acquisto.','35% balloon, 20% down payment, estimated residual value. Purchase excludes insurance and maintenance.')}</div>`}
function viewRentVsBuy(q){if(q&&carBy(q.auto))NA.car=q.auto;return `<div class="page"><div class="wrap">${pageHead('—',T('confronto','comparison'),T('Noleggiare o comprare?','Rent or buy?'),T('Lo stesso periodo, tre modi di guidare la stessa auto. Valori di esempio.','The same period, three ways to drive the same car. Sample values.'))}
  <div class="card"><div class="grid3"><label class="field"><span>${T('Auto','Car')}</span><select class="inp" data-chg="na" name="car">${carOpts(NA.car)}</select></label>
   <label class="field"><span>${T('Durata','Term')}</span><select class="inp" data-chg="na" name="m">${[12,24,36,48].map(m=>`<option value="${m}" ${NA.m===m?'selected':''}>${m} ${T('mesi','months')}</option>`).join('')}</select></label>
   <label class="field"><span>${T('Km all\'anno','Km per year')}</span><select class="inp" data-chg="na" name="km">${[10000,15000,20000,30000].map(k=>`<option value="${k}" ${NA.km===k?'selected':''}>${num(k)}</option>`).join('')}</select></label></div></div>
  <div id="naOut" style="margin-top:20px">${naOut()}</div>
  <div class="cta" style="margin-top:20px"><a class="btn btn-primary" href="#/preventivo?tipo=lungo&auto=${NA.car}" id="naLt">${T('Preventivo lungo termine','Long-term quote')}</a><a class="btn btn-line" href="#/finanziamento?auto=${NA.car}" id="naFin">${T('Calcola la rata','Work out the payment')}</a></div></div></div>`}
CHG.na=el=>{NA[el.name]=el.name==='car'?el.value:+el.value;$('#naOut').innerHTML=naOut();$('#naLt').href='#/preventivo?tipo=lungo&auto='+NA.car;$('#naFin').href='#/finanziamento?auto='+NA.car};

/* ================= EVENTI: matrimoni, shooting, aziende ================= */
const EVT=[['matrimonio',T('Matrimonio','Wedding'),T('Auto con nastri e fiori, autista a richiesta.','Car with ribbons and flowers, chauffeur on request.')],['shooting',T('Shooting foto e video','Photo & video shoot'),T('Auto pulita e posizionata, fotografo su richiesta.','Car cleaned and positioned, photographer on request.')],['azienda',T('Evento aziendale','Corporate event'),T('Esposizione, lancio di prodotto, test drive per ospiti.','Display, product launch, guest test drives.')]];
let EV={type:'matrimonio',car:CARS[0].id,ore:4,v:{},err:{},done:null};
const evPrice=()=>{const c=carBy(EV.car);return Math.round((c.day*.35*EV.ore+(EV.type==='shooting'?350:EV.type==='matrimonio'?200:0))/10)*10};
function viewEvents(){return `<div class="page"><div class="wrap">${pageHead('—',T('eventi','events'),T('Matrimoni, shooting, eventi.','Weddings, shoots, events.'),T('L\'auto giusta per il giorno giusto. Prezzo indicativo subito, conferma su WhatsApp.','The right car for the right day. Indicative price at once, confirmation on WhatsApp.'))}<div id="evBody">${evBody()}</div></div></div>`}
function evBody(){if(EV.done)return `<div class="done"><div class="done-main"><article class="card summary-doc"><p class="eyebrow">${T('Richiesta evento','Event request')}</p><h2>${EV.done.code}</h2>${sumRows(EV.done.lines)}</article><button class="btn btn-ghost btn-sm" data-act="evNew">${T('Nuova richiesta','New request')}</button></div><div class="done-side">${waFinal('event',EV.done,'events')}</div></div>`;
  const v=EV.v,e=EV.err;return `<form class="stack" data-form="event" novalidate><div class="types" role="radiogroup">${EVT.map(x=>`<button type="button" class="type" role="radio" aria-checked="${EV.type===x[0]}" data-act="evType" data-t="${x[0]}"><b>${x[1]}</b><small>${x[2]}</small></button>`).join('')}</div>
   <div class="card stack"><div class="grid3"><label class="field"><span>${T('Auto','Car')}</span><select class="inp" data-chg="ev" name="car">${carOpts(EV.car)}</select></label>${field('data',T('Data','Date'),{type:'date',req:1,v:v.data,attr:`min="${iso(addD(TODAY,1))}"`,err:e.data})}
    <label class="field"><span>${T('Ore','Hours')}: <b id="evH">${EV.ore}</b></span><input type="range" data-chg="ev" name="ore" min="2" max="10" value="${EV.ore}"></label></div>
    ${field('luogo',T('Luogo','Location'),{v:v.luogo,ph:T('es. Villa in Brianza','e.g. villa in Brianza')})}
    <div class="est"><span>${T('Prezzo indicativo','Indicative price')}</span><b class="num" id="evP">${eur(evPrice())}</b><small>${T('Esempio, da confermare con MC.','Example, to be confirmed by MC.')}</small></div>
    <div class="grid2">${field('nome',T('Nome e cognome','Full name'),{req:1,v:v.nome,err:e.nome})}${field('tel',T('Telefono','Phone'),{req:1,type:'tel',v:v.tel,err:e.tel})}</div>${privacyBox(v.privacy,e.privacy)}
    <button class="btn btn-primary">${T('Genera il riepilogo','Generate the summary')}</button></div></form>`}
const evSave=()=>{const f=$('form[data-form=event]');if(!f)return;const d=new FormData(f);['data','luogo','nome','tel'].forEach(k=>EV.v[k]=d.get(k)||'');EV.v.privacy=!!d.get('privacy')};
ACT.evType=el=>{evSave();EV.type=el.dataset.t;$('#evBody').innerHTML=evBody()};
CHG.ev=el=>{EV[el.name]=el.name==='car'?el.value:+el.value;$('#evH').textContent=EV.ore;$('#evP').textContent=eur(evPrice())};
FORM.event=()=>{evSave();const v=EV.v,e={};if(!v.data)e.data=T('Scegli la data','Pick the date');if(!(v.nome||'').trim())e.nome=T('Campo obbligatorio','Required field');if(!telOk(v.tel))e.tel=T('Numero non valido','Invalid number');if(!v.privacy)e.privacy=T('Serve il consenso','Consent is needed');
  EV.err=e;if(Object.keys(e).length){$('#evBody').innerHTML=evBody();return}const ty=EVT.find(x=>x[0]===EV.type)[1],c=carBy(EV.car);
  const rec={code:'EV-'+Date.now().toString(36).slice(-5).toUpperCase(),made:Date.now(),car:c.id,kindLabel:ty,detail:fmtD(fromIso(v.data))+', '+EV.ore+' ore'+(v.luogo?', '+v.luogo:'')+', prezzo indicativo '+eur(evPrice()),who:v.nome.trim(),tel:v.tel.trim(),
    lines:[[T('Evento','Event'),ty],[T('Auto','Car'),carName(c)],[T('Data','Date'),fmtD(fromIso(v.data))],[T('Ore','Hours'),EV.ore],[T('Luogo','Location'),esc(v.luogo||'—')],[T('Prezzo indicativo','Indicative price'),eur(evPrice()),'tot'],[T('Nome','Name'),esc(v.nome)],[T('Telefono','Phone'),esc(v.tel)]]};
  const L=store.get('events',[]);L.unshift(rec);store.set('events',L);upsertLead({who:rec.who,tel:rec.tel,type:'info',car:c.id,note:rec.code});EV.done=rec;$('#evBody').innerHTML=evBody()};
ACT.evNew=()=>{EV.done=null;EV.v={};$('#evBody').innerHTML=evBody()};

/* ================= CONSEGNA A DOMICILIO / AEROPORTO ================= */
let CS={place:'milano',km:8};
const csFee=()=>{const p=PLACES.find(x=>x.id===CS.place);return CS.place==='milano'?p.fee+Math.max(0,CS.km-10)*1.5:p.fee};
function viewDelivery(){return `<div class="page"><div class="wrap narrow">${pageHead('—',T('consegna','delivery'),T('Te la portiamo noi.','We bring it to you.'),T('A domicilio a Milano o in aeroporto. Costo indicativo per tratta, consegna e ritiro.','At home in Milan or at the airport. Indicative cost per leg, delivery and collection.'))}
  <div class="card stack"><div class="field"><span>${T('Dove','Where')}</span><div class="chips">${PLACES.map(p=>`<button class="chip" data-act="csPlace" data-id="${p.id}" aria-pressed="${CS.place===p.id}">${T(p.it,p.en)}</button>`).join('')}</div></div>
   <label class="field" id="csKm" ${CS.place==='milano'?'':'hidden'}><span>${T('Distanza dalla sede (km)','Distance from the showroom (km)')}: <b id="csKmO">${CS.km}</b></span><input type="range" data-chg="cs" min="1" max="40" value="${CS.km}"></label>
   <div class="est"><span>${T('Costo indicativo per tratta','Indicative cost per leg')}</span><b class="num" id="csOut">${eur(csFee())}</b><small>${T('Esempio: 90 € a Milano entro 10 km, poi 1,50 €/km; aeroporti a tariffa fissa.','Example: €90 in Milan within 10 km, then €1.50/km; airports at a fixed rate.')}</small></div>
   <div class="cta"><a class="btn btn-primary" href="#/noleggio">${T('Scegli l\'auto','Choose the car')}</a>${waBtn('btn-line',T('Chiedi su WhatsApp','Ask on WhatsApp'),'Salve MC, vorrei la consegna dell\'auto: '+T(PLACES.find(p=>p.id===CS.place).it,'')+'.')}</div></div></div></div>`}
ACT.csPlace=el=>{CS.place=el.dataset.id;$('#view').innerHTML=viewDelivery()};
CHG.cs=el=>{CS.km=+el.value;$('#csKmO').textContent=CS.km;$('#csOut').textContent=eur(csFee())};

/* ================= DOCUMENTI RICHIESTI (checklist) ================= */
function viewDocs(q){const c=carBy(q&&q.auto)||null,ck=store.get('docck',{});
  const items=[['pat',T('Patente B valida, da almeno 3 anni','Valid B licence, held for at least 3 years')],['id',T('Documento d\'identità valido','Valid ID document')],['cf',T('Codice fiscale o tessera sanitaria','Tax code or health card')],['cc',T('Carta di credito intestata al conducente (per la cauzione)','Credit card in the driver\'s name (for the deposit)')],['int',T('Permesso internazionale se la patente è extra-UE','International permit if the licence is non-EU')],['add',T('Dati del secondo guidatore, se previsto','Second driver\'s details, if any')]];
  return `<div class="page"><div class="wrap narrow">${pageHead('—',T('documenti','documents'),T('Cosa serve per noleggiare.','What you need to rent.'),T('Spunta quello che hai già: la lista resta in questo browser.','Tick what you already have: the list stays in this browser.'))}
   <label class="field" style="max-width:360px;margin-bottom:18px"><span>${T('Per quale auto','For which car')}</span><select class="inp" data-chg="docCar">${carOpts(c?c.id:'',T('Tutte','All'))}</select></label>
   ${c?`<div class="bigs bigs-s" style="margin-bottom:24px"><div>${sup(c.age,T('anni','yrs'))}<p>${T('età minima','minimum age')}</p></div><div>${sup(num(c.dep),'€')}<p>${T('cauzione (esempio)','deposit (example)')}</p></div><div>${sup(c.kmDay,'km')}<p>${T('inclusi al giorno','included per day')}</p></div></div>`:''}
   <ul class="checklist">${items.map(i=>`<li><input type="checkbox" id="dk-${i[0]}" data-chg="docck" data-k="${i[0]}" ${ck[i[0]]?'checked':''}><label for="dk-${i[0]}">${i[1]}</label></li>`).join('')}</ul>
   <p class="small muted" style="margin-top:16px" id="docN">${Object.values(ck).filter(Boolean).length}/${items.length} ${T('pronti','ready')}</p>
   <div class="cta" style="margin-top:16px"><a class="btn btn-primary" href="#/noleggio">${T('Prenota un\'auto','Book a car')}</a></div></div></div>`}
CHG.docck=el=>{const ck=store.get('docck',{});ck[el.dataset.k]=el.checked;store.set('docck',ck);const n=$('#docN');if(n)n.textContent=Object.values(ck).filter(Boolean).length+'/6 '+T('pronti','ready')};
CHG.docCar=el=>{location.hash='#/documenti'+(el.value?'?auto='+el.value:'')};

/* ================= NEWSLETTER «nuovi arrivi» (solo raccolta locale) ================= */
function nlBox(){return `<form class="nl" data-form="nl" novalidate><h4>${T('Nuovi arrivi','New arrivals')}</h4><p class="small muted">${T('Lascia l\'email: ti avvisiamo quando entra un\'auto nuova.','Leave your email: we tell you when a new car arrives.')}</p>
  <div class="nl-row"><input class="inp" type="email" name="mail" placeholder="nome@esempio.it" aria-label="Email" autocomplete="email"><button class="btn btn-primary btn-sm">${T('Iscrivimi','Subscribe')}</button></div>
  <label class="check xs"><input type="checkbox" name="ok"><span>${T('Acconsento a ricevere email da MC.','I agree to receive emails from MC.')}</span></label><p class="xs dim">${T('Prototipo: l\'indirizzo resta salvato solo in questo browser, nessun invio.','Prototype: the address is only saved in this browser, nothing is sent.')}</p></form>`}
FORM.nl=f=>{const d=new FormData(f),m=(d.get('mail')||'').trim();if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(m)){toast(T('Email non valida','Invalid email'));return}if(!d.get('ok')){toast(T('Serve il consenso','Consent is needed'));return}
  const L=store.get('newsletter',[]);if(!L.some(x=>x.mail===m))L.unshift({mail:m,made:Date.now()});store.set('newsletter',L);f.reset();toast(T('Iscrizione salvata in questo browser','Subscription saved in this browser'))};

/* ================= LISTA DESIDERI CONDIVISIBILE ================= */
function viewWish(q){const shared=(q&&q.l||'').split(',').filter(id=>carBy(id)),mine=favs(),list=shared.length?shared:mine;
  const link=location.href.split('#')[0]+'#/preferiti?l='+mine.join(',');
  return `<div class="page"><div class="wrap">${pageHead('—',T('preferiti','favourites'),shared.length?T('Una lista condivisa con te.','A list shared with you.'):T('La tua lista desideri.','Your wish list.'),shared.length?T('Qualcuno ti ha mandato queste auto. Puoi salvarle nei tuoi preferiti.','Someone sent you these cars. You can save them to your favourites.'):T('Salvata in questo browser. Condividila con un link.','Saved in this browser. Share it with a link.'))}
   ${list.length?`<div class="cta" style="margin-bottom:22px">${shared.length?`<button class="btn btn-primary btn-sm" data-act="wishSave" data-l="${shared.join(',')}">${T('Salva nei miei preferiti','Save to my favourites')}</button>`:`<button class="btn btn-primary btn-sm" data-act="share" data-path="/preferiti?l=${mine.join(',')}" data-title="${T('La mia lista MC','My MC list')}">${T('Condividi la lista','Share the list')}</button><a class="btn btn-line btn-sm" href="https://wa.me/?text=${encodeURIComponent(T('Guarda queste auto: ','Look at these cars: ')+link)}" target="_blank" rel="noopener">WhatsApp</a>`}</div>
    <div class="cars">${list.map(id=>carCard(carBy(id))).join('')}</div>`:`<div class="empty"><b>${T('Nessun preferito','No favourites yet')}</b><p>${T('Tocca il cuore su un\'auto per salvarla qui.','Tap the heart on a car to save it here.')}</p><a class="btn btn-primary btn-sm" href="#/noleggio">${T('Sfoglia la collezione','Browse the collection')}</a></div>`}</div></div>`}
ACT.wishSave=el=>{const f=favs();el.dataset.l.split(',').forEach(id=>{if(!f.includes(id))f.push(id)});store.set('favs',f);favCount();toast(T('Salvate nei preferiti','Saved to favourites'));location.hash='#/preferiti'};

/* ================= AUTO SIMILI ================= */
const similar=c=>CARS.filter(x=>x.id!==c.id).map(x=>({x,s:(x.cat===c.cat?0:1)+Math.abs(Math.log(x.day/c.day))+(x.fuel===c.fuel?0:.2)})).sort((a,b)=>a.s-b.s).slice(0,3).map(o=>o.x);
