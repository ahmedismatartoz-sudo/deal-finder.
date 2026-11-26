/* MC prototipo — agenda appuntamenti, finanziamento, preventivo, permuta, lungo termine, chi siamo, preferiti, richiamo.
   Tutto nel browser: i dati restano in localStorage (con memoria di riserva). La notifica al salone è un link wa.me
   con il testo già scritto: l'invio automatico richiede un backend (vedi nota in data.js). */
'use strict';
const qsAuto=q=>carBy(q.auto)?q.auto:'';
const field=(name,label,o)=>{o=o||{};return `<label class="field ${o.cls||''}"><span>${label}${o.req?' *':''}</span><input class="inp" name="${name}" type="${o.type||'text'}" value="${esc(o.v||'')}" ${o.ac?`autocomplete="${o.ac}"`:''} ${o.ph?`placeholder="${esc(o.ph)}"`:''} ${o.attr||''} ${o.err?'aria-invalid="true"':''}>${o.err?`<span class="err" role="alert">${o.err}</span>`:''}</label>`};
const telOk=t=>String(t||'').replace(/\D/g,'').length>=8;
const mailOk=m=>!m||/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(m);
const privacyBox=(on,err)=>`<label class="check"><input type="checkbox" name="privacy" ${on?'checked':''}><span>${T('Accetto l\'informativa privacy per essere ricontattato. (obbligatorio)','I accept the privacy notice to be contacted. (required)')}</span></label>${err?`<span class="err" role="alert">${err}</span>`:''}`;
const sumRows=rows=>`<div class="rows">${rows.filter(r=>r&&r[1]!=null&&r[1]!=='').map(r=>`<div class="${r[2]||''}"><span>${r[0]}</span><span>${r[1]}</span></div>`).join('')}</div>`;
const carOpts=(sel,none)=>`${none?`<option value="">${none}</option>`:''}${CARS.map(c=>`<option value="${c.id}" ${sel===c.id?'selected':''}>${carName(c)}</option>`).join('')}`;

/* ================= AGENDA ================= */
let AGD=null;
function agInit(q){const t=APPT[q.tipo]?q.tipo:(AGD&&AGD.type)||'testdrive';
  if(!AGD||AGD.done||q.tipo||q.auto)AGD={type:t,car:qsAuto(q)||(AGD&&AGD.car)||(APPT[t].car?CARS[0].id:''),month:new Date(TODAY.getFullYear(),TODAY.getMonth(),1),date:'',time:'',v:{},err:{},done:null}}
function viewAgenda(q){agInit(q||{});
  return `<div class="page"><div class="wrap"><div class="page-head"><p class="eyebrow">${T('Agenda','Agenda')}</p><h1>${seoH1('agenda',T('Prenota un appuntamento.','Book an appointment.'))}</h1>
   <p class="lead">${T('Test drive, visita in salone o ritiro del noleggio: scegli il giorno e una fascia libera. Una fascia prenotata non si può prenotare due volte.','Test drive, showroom visit or rental pick-up: choose the day and a free slot. A booked slot cannot be booked twice.')}</p></div>
   <div id="agBody">${agBody()}</div></div></div>`}
function agBody(){const A=AGD;if(A.done)return agDone();const ty=APPT[A.type],c=carBy(A.car);
  return `<div class="ag"><div class="ag-main">
   <section class="ag-step"><h2 class="h-step"><span>01</span>${T('Che cosa','What')}</h2>
    <div class="types" role="radiogroup" aria-label="${T('Tipo di appuntamento','Appointment type')}">${Object.keys(APPT).map(k=>`<button class="type" role="radio" aria-checked="${A.type===k}" data-act="agType" data-t="${k}"><b>${T(APPT[k].it,APPT[k].en)}</b><small>${{testdrive:T('Circa 60 minuti, su strada','About 60 minutes, on the road'),visita:T('Consulenza su acquisto, noleggio o finanziamento','Advice on purchase, rental or finance'),ritiro:T('Consegna chiavi e controllo dell\'auto','Key handover and car check')}[k]}</small></button>`).join('')}</div>
    ${ty.car?`<label class="field" style="margin-top:16px"><span>${T('Auto','Car')}</span><select class="inp" data-chg="agCar">${carOpts(A.car)}</select></label>`:''}</section>
   <section class="ag-step"><h2 class="h-step"><span>02</span>${T('Quando','When')}</h2><div id="agCal">${agCal()}</div></section>
   <section class="ag-step"><h2 class="h-step"><span>03</span>${T('A che ora','What time')}</h2><div id="agSlots" aria-live="polite">${agSlots()}</div></section></div>
   <aside class="ag-side"><form class="card" data-form="appt" novalidate><h3>${T('Riepilogo','Summary')}</h3>
    <div id="agSum">${agSum()}</div>
    ${field('nome',T('Nome e cognome','Full name'),{req:1,v:A.v.nome,ac:'name',err:A.err.nome})}${field('tel',T('Telefono','Phone'),{req:1,type:'tel',v:A.v.tel,ac:'tel',ph:'+39 …',err:A.err.tel})}
    ${field('mail','Email',{type:'email',v:A.v.mail,ac:'email',ph:T('facoltativa','optional'),err:A.err.mail})}
    <label class="field"><span>${T('Note','Notes')}</span><textarea class="inp" name="note" rows="2">${esc(A.v.note||'')}</textarea></label>
    ${privacyBox(A.v.privacy,A.err.privacy)}${A.err.slot?`<div class="note bad" role="alert">${A.err.slot}</div>`:''}
    <button class="btn btn-primary btn-block" id="agGo">${T('Conferma l\'appuntamento','Confirm the appointment')} ${IC.arrow}</button>
    <p class="xs dim">${T('Dopo la conferma, l\'ultimo passo è avvisare il salone su WhatsApp.','After confirming, the final step is to notify the showroom on WhatsApp.')}</p></form></aside></div>`}
function agSum(){const A=AGD,c=carBy(A.car);return sumRows([[T('Tipo','Type'),T(APPT[A.type].it,APPT[A.type].en)],APPT[A.type].car?[T('Auto','Car'),c?carName(c):'—']:null,[T('Giorno','Day'),A.date?fmtDl(fromIso(A.date)):`<i class="dim">${T('da scegliere','to choose')}</i>`],[T('Ora','Time'),A.time||`<i class="dim">${T('da scegliere','to choose')}</i>`],[T('Durata','Length'),{testdrive:'60 min',visita:'45 min',ritiro:'30 min'}[A.type]],[T('Dove','Where'),BIZ.addr]])}
function agCal(){const A=AGD,m=A.month,first=new Date(m),startW=(first.getDay()+6)%7,days=new Date(m.getFullYear(),m.getMonth()+1,0).getDate(),max=addD(TODAY,60);
  const prevOk=m>new Date(TODAY.getFullYear(),TODAY.getMonth(),1),nextOk=new Date(m.getFullYear(),m.getMonth()+1,1)<=max;let cells='';
  for(let i=0;i<startW;i++)cells+='<span class="cd empty"></span>';
  for(let d=1;d<=days;d++){const dt=new Date(m.getFullYear(),m.getMonth(),d),s=iso(dt),past=dt<TODAY,far=dt>max,ld=dayLoad(s),closed=!ld.n,full=ld.n&&!ld.free;
    const st=past||far?'off':closed?'closed':full?'full':'ok',sel=A.date===s;
    cells+=`<button class="cd ${st} ${sel?'sel':''} ${+dt===+TODAY?'today':''}" ${st==='ok'?`data-act="agDay" data-d="${s}"`:'disabled'} aria-pressed="${sel}" aria-label="${fmtDl(dt)}: ${st==='ok'?ld.free+' '+T('fasce libere','free slots'):st==='full'?T('completo','fully booked'):st==='closed'?T('chiuso','closed'):T('non disponibile','not available')}"><b>${d}</b>${st==='ok'?`<i style="--f:${ld.free/ld.n}"></i>`:st==='full'?`<small>${T('pieno','full')}</small>`:''}</button>`}
  return `<div class="cal"><div class="cal-head"><button class="icon-btn" data-act="agMonth" data-d="-1" ${prevOk?'':'disabled'} aria-label="${T('Mese precedente','Previous month')}">‹</button><b>${fmtD(m,{month:'long',year:'numeric'})}</b><button class="icon-btn" data-act="agMonth" data-d="1" ${nextOk?'':'disabled'} aria-label="${T('Mese successivo','Next month')}">›</button></div>
   <div class="cal-w">${[T('L','M'),T('M','T'),T('M','W'),T('G','T'),T('V','F'),T('S','S'),T('D','S')].map(w=>`<span>${w}</span>`).join('')}</div><div class="cal-g">${cells}</div>
   <div class="cal-leg xs muted"><span><i class="lg ok"></i>${T('fasce libere','free slots')}</span><span><i class="lg full"></i>${T('completo','full')}</span><span><i class="lg closed"></i>${T('chiuso (domenica su appuntamento telefonico)','closed (Sunday by phone appointment)')}</span></div>
   <p class="xs dim">${T('Occupazione delle fasce simulata e uguale per tutti i visitatori; le tue prenotazioni si aggiungono e bloccano la fascia.','Slot occupancy is simulated and the same for every visitor; your bookings add to it and block the slot.')}</p></div>`}
function agSlots(){const A=AGD;if(!A.date)return `<p class="muted small">${T('Scegli prima un giorno nel calendario.','Pick a day in the calendar first.')}</p>`;
  const L={free:T('Libero','Free'),busy:T('Occupato','Booked'),mine:T('Prenotato da te','Booked by you'),past:T('Passato','Past')};
  return `<p class="small" style="margin-bottom:12px"><b>${fmtDl(fromIso(A.date))}</b></p><div class="slots">${daySlots(A.date).map(t=>{const s=slotState(A.date,t),sel=A.time===t&&s==='free';return `<button class="slot ${s} ${sel?'sel':''}" ${s==='free'?`data-act="agSlot" data-t="${t}"`:'disabled'} aria-pressed="${sel}"><b>${t}</b><small>${L[s]||''}</small></button>`}).join('')}</div>`}
function agRefresh(){if(!AGD||AGD.done||!$('#agCal'))return;if(AGD.time&&!slotFree(AGD.date,AGD.time))AGD.time='';$('#agCal').innerHTML=agCal();$('#agSlots').innerHTML=agSlots();$('#agSum').innerHTML=agSum()}
const agSave=()=>{const f=$('form[data-form=appt]');if(f){const d=new FormData(f);['nome','tel','mail','note'].forEach(k=>AGD.v[k]=d.get(k)||'');AGD.v.privacy=!!d.get('privacy')}};
ACT.agType=el=>{agSave();AGD.type=el.dataset.t;if(APPT[AGD.type].car&&!AGD.car)AGD.car=CARS[0].id;$('#agBody').innerHTML=agBody()};
CHG.agCar=el=>{AGD.car=el.value;$('#agSum').innerHTML=agSum()};
ACT.agMonth=el=>{const m=AGD.month;AGD.month=new Date(m.getFullYear(),m.getMonth()+(+el.dataset.d),1);$('#agCal').innerHTML=agCal()};
ACT.agDay=el=>{AGD.date=el.dataset.d;AGD.time='';AGD.err={};agRefresh();const s=$('#agSlots');if(s&&innerWidth<980)s.scrollIntoView({behavior:'smooth',block:'center'})};
ACT.agSlot=el=>{AGD.time=el.dataset.t;AGD.err={};$('#agSlots').innerHTML=agSlots();$('#agSum').innerHTML=agSum();if(innerWidth<980){const f=$('form[data-form=appt]');f&&f.scrollIntoView({behavior:'smooth',block:'start'})}};
FORM.appt=f=>{agSave();const A=AGD,v=A.v,e={};
  if(!(v.nome||'').trim())e.nome=T('Campo obbligatorio','Required field');if(!telOk(v.tel))e.tel=T('Numero non valido','Invalid number');if(!mailOk(v.mail))e.mail=T('Email non valida','Invalid email');
  if(!v.privacy)e.privacy=T('Serve il consenso per fissare l\'appuntamento','Consent is needed to book');
  if(!A.date||!A.time)e.slot=T('Scegli giorno e ora.','Choose day and time.');
  A.err=e;if(Object.keys(e).length){$('#agBody').innerHTML=agBody();const x=$('#agBody [aria-invalid=true],#agBody .note.bad');x&&x.scrollIntoView({block:'center',behavior:'smooth'});return}
  // controllo anti doppia prenotazione: si rilegge l'archivio un attimo prima di salvare
  const rec=takeSlot({type:A.type,car:APPT[A.type].car?A.car:'',date:A.date,time:A.time,who:v.nome.trim(),tel:v.tel.trim(),mail:(v.mail||'').trim(),note:(v.note||'').trim()});
  if(!rec){A.err={slot:T('Questa fascia è appena stata prenotata: non si accettano doppie prenotazioni. Scegline un\'altra.','This slot has just been booked: double bookings are not accepted. Please choose another.')};A.time='';$('#agBody').innerHTML=agBody();toast(T('Fascia non più disponibile','Slot no longer available'));return}
  upsertLead({who:rec.who,mail:rec.mail,tel:rec.tel,type:A.type,car:rec.car,note:rec.code});
  A.done=rec;$('#agBody').innerHTML=agBody();scrollTo({top:0,behavior:'smooth'});toast(T('Appuntamento registrato','Appointment recorded'))};
function agDone(){const r=AGD.done,c=carBy(r.car);
  return `<div class="done"><div class="done-main"><div class="confirm-hero"><div class="tick"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></div>
    <p class="eyebrow">${T('Appuntamento registrato','Appointment recorded')}</p><h2>${T(APPT[r.type].it,APPT[r.type].en)}<br><em>${fmtDl(fromIso(r.date))}, ${r.time}</em></h2>
    <p class="muted">${T('La fascia è bloccata per te in questo prototipo. Il salone la conferma dopo aver ricevuto il tuo messaggio.','The slot is held for you in this prototype. The showroom confirms it after receiving your message.')}</p></div>
   <dl class="kv"><div><dt>${T('Codice','Code')}</dt><dd>${r.code}</dd></div>${c?`<div><dt>${T('Auto','Car')}</dt><dd>${carName(c)}</dd></div>`:''}<div><dt>${T('Giorno','Day')}</dt><dd>${fmtD(fromIso(r.date))}</dd></div><div><dt>${T('Ora','Time')}</dt><dd>${r.time}</dd></div><div><dt>${T('Nome','Name')}</dt><dd>${esc(r.who)}</dd></div><div><dt>${T('Telefono','Phone')}</dt><dd>${esc(r.tel)}</dd></div></dl>
   <div class="cta"><button class="btn btn-line btn-sm" data-act="agIcs">${IC.cal}<span>${T('Aggiungi al calendario (.ics)','Add to calendar (.ics)')}</span></button><button class="btn btn-ghost btn-sm" data-act="agNew">${T('Prenota un altro appuntamento','Book another appointment')}</button><a class="btn btn-ghost btn-sm" href="#/admin/agenda">${T('Vedi nell\'area salone','See in the showroom area')}</a></div></div>
   <div class="done-side">${waFinal('appt',r,'appts')}</div></div>`}
ACT.agNew=()=>{AGD.done=null;AGD.date='';AGD.time='';$('#agBody').innerHTML=agBody()};
ACT.agIcs=()=>{const r=AGD.done;if(!r)return;const st=r.date.replace(/-/g,'')+'T'+r.time.replace(':','')+'00',[h,m]=r.time.split(':').map(Number),mins={testdrive:60,visita:45,ritiro:30}[r.type],e=new Date(2000,0,1,h,m+mins);
  const en=r.date.replace(/-/g,'')+'T'+String(e.getHours()).padStart(2,'0')+String(e.getMinutes()).padStart(2,'0')+'00';
  const ics=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//MC prototipo//IT','BEGIN:VEVENT','UID:'+r.code+'@mc-prototipo','DTSTAMP:'+new Date().toISOString().replace(/[-:]/g,'').slice(0,15)+'Z','DTSTART;TZID=Europe/Rome:'+st,'DTEND;TZID=Europe/Rome:'+en,'SUMMARY:MC – '+APPT[r.type].it+(r.car?' '+carName(carBy(r.car)):''),'LOCATION:'+BIZ.addr+'\\, '+BIZ.cap,'DESCRIPTION:Codice '+r.code+' (prototipo dimostrativo)','END:VEVENT','END:VCALENDAR'].join('\r\n');
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([ics],{type:'text/calendar'}));a.download=r.code+'.ics';document.body.appendChild(a);a.click();a.remove()};

/* ================= FINANZIAMENTO ================= */
let FN={car:CARS[0].id,price:0,down:null,months:48,balloon:true,bPct:35,tan:FIN.tan,page:false};
function finPrice(){if(FN.page&&!FN.car)return FN.price;const c=carBy(FN.car);return c?c.sale:FN.price}
function finCalcNow(){const P=finPrice();if(FN.down==null||FN.down>P*.9)FN.down=Math.round(P*.25/500)*500;return finance({price:P,down:FN.down,months:FN.months,balloon:FN.balloon,bPct:FN.bPct,tan:FN.tan})}
const finLabel=f=>`${eur2(f.rata)}/mese × ${f.n} · anticipo ${eur(f.A)}${f.B?' · maxirata '+eur(f.B):''} · TAN ${pct(f.tan)} · TAEG ${pct(f.taeg)} (esempio)`;
function finBox(){const f=finCalcNow(),P=finPrice();
  return `<div class="fin" id="fin">
   ${FN.page?`<div class="grid2"><label class="field"><span>${T('Auto','Car')}</span><select class="inp" data-chg="fin" name="car">${carOpts(FN.car,T('Altro importo','Other amount'))}</select></label>
     <label class="field"><span>${T('Prezzo (€)','Price (€)')}</span><input class="inp" type="number" min="5000" step="500" data-chg="fin" name="price" value="${Math.round(P)}" ${FN.car?'readonly':''}></label></div>`:`<div class="fin-price"><span>${T('Prezzo','Price')}</span><b class="num" id="finP">${eur(P)}</b></div>`}
   <label class="field"><span>${T('Anticipo','Down payment')}: <b id="finDownO">${eur(f.A)}</b> <i class="dim" id="finDownP">(${Math.round(f.A/P*100)}%)</i></span><input type="range" data-chg="fin" name="down" min="0" max="${Math.round(P*.5/500)*500}" step="500" value="${f.A}"></label>
   <div class="field"><span>${T('Durata','Term')}</span><div class="chips" role="group">${[24,36,48,60,72,84].map(m=>`<button class="chip" data-act="finM" data-m="${m}" aria-pressed="${FN.months===m}">${m} ${T('mesi','mo')}</button>`).join('')}</div></div>
   <div class="grid2"><label class="check"><input type="checkbox" data-chg="fin" name="balloon" ${FN.balloon?'checked':''}><span>${T('Con maxirata finale','With final balloon')}</span></label>
    <label class="field"><span>${T('Maxirata','Balloon')}: <b id="finBO">${FN.balloon?FN.bPct+'%':'—'}</b></span><input type="range" data-chg="fin" name="bPct" min="10" max="50" step="5" value="${FN.bPct}" ${FN.balloon?'':'disabled'}></label></div>
   <label class="field fin-tan"><span>${T('TAN di esempio (%)','Sample nominal rate (%)')}</span><input class="inp" type="number" min="0" max="20" step="0.01" data-chg="fin" name="tan" value="${FN.tan}"></label>
   <div class="fin-out" id="finOut" aria-live="polite">${finOut(f)}</div>
   <div class="note warn"><b>${T('Valori di esempio','Sample values')}.</b> ${T('TAN, TAEG e spese (istruttoria','Rate, APR and fees (arrangement')} ${eur(FIN.istr)}, ${T('bollo','stamp duty')} ${eur(FIN.bollo)}, ${T('incasso rata','collection fee')} ${eur(FIN.inc)}) ${T('servono solo a orientarsi: non è un\'offerta né un\'approvazione. Le condizioni vere, con il modulo SECCI, le dà la finanziaria dopo la valutazione.','are only for orientation: this is not an offer nor an approval. Real terms, with the SECCI form, come from the lender after assessment.')}</div></div>`}
function finOut(f){if(!f.ok)return `<p class="err">${T('Controlla gli importi: la maxirata non può superare il capitale.','Check the amounts: the balloon cannot exceed the capital.')}</p>`;
  return `<div class="fin-big"><span>${T('Rata mensile','Monthly payment')}</span><b class="num" data-rata="${f.rata.toFixed(2)}">${eur2(f.rata)}</b><small>× ${f.n} ${T('mesi','months')}${f.B?' + '+T('maxirata','balloon')+' '+eur(f.B):''}</small></div>
   ${sumRows([[T('Importo del credito','Credit amount'),eur(f.credit)],[T('Spese istruttoria e bollo (finanziate)','Arrangement & stamp (financed)'),eur(f.fees)],[T('Capitale finanziato','Financed capital'),eur(f.C)],[T('Rata senza spese incasso','Payment before collection fee'),eur2(f.R)],f.B?[T('Maxirata finale','Final balloon'),eur(f.B)]:null,[T('TAN fisso','Fixed nominal rate'),pct(f.tan)],['TAEG',`<b>${pct(f.taeg)}</b>`],[T('Interessi totali','Total interest'),eur(f.interest)],[T('Totale dovuto','Total payable'),eur(f.total),'tot']])}`}
function finPaint(){const box=$('#fin');if(!box)return;const f=finCalcNow(),P=finPrice();
  const o=$('#finOut');if(o)o.innerHTML=finOut(f);const d=$('#finDownO');if(d)d.textContent=eur(f.A);const dp=$('#finDownP');if(dp)dp.textContent='('+Math.round(f.A/P*100)+'%)';
  const r=$('input[name=down]',box);if(r){r.max=Math.round(P*.5/500)*500;r.value=f.A}const bo=$('#finBO');if(bo)bo.textContent=FN.balloon?FN.bPct+'%':'—';const b=$('input[name=bPct]',box);if(b)b.disabled=!FN.balloon;
  const pp=$('#finP');if(pp)pp.textContent=eur(P);const pin=$('input[name=price]',box);if(pin&&FN.car){pin.value=Math.round(P);pin.readOnly=true}else if(pin)pin.readOnly=false;
  $$('[data-finlink]').forEach(a=>{const c=carBy(FN.car);a.setAttribute('href','#/preventivo?tipo=acquisto&fin=1'+(c?'&auto='+c.id:'')+'&ant='+f.A+'&mesi='+f.n+'&maxi='+(FN.balloon?FN.bPct:0)+'&tan='+FN.tan)})}
CHG.fin=el=>{const n=el.name;if(n==='car'){FN.car=el.value;FN.down=null;if(!FN.car)FN.price=FN.price||100000}else if(n==='price'){FN.price=Math.max(0,+el.value||0);FN.down=null}else if(n==='balloon')FN.balloon=el.checked;else FN[n]=+el.value;finPaint()};
ACT.finM=el=>{FN.months=+el.dataset.m;$$('[data-act=finM]').forEach(b=>b.setAttribute('aria-pressed',b===el));finPaint()};
function viewFinance(q){q=q||{};FN.page=true;if(qsAuto(q)){FN.car=q.auto;FN.down=null}
  return `<div class="page"><div class="wrap"><div class="page-head"><p class="eyebrow">${T('Finanziamento','Finance')}</p><h1>${seoH1('finanziamento',T('Calcola la tua rata.','Work out your payment.'))}</h1>
   <p class="lead">${T('Anticipo, durata e maxirata: la rata e il TAEG si aggiornano mentre scegli. Valori di esempio.','Down payment, term and balloon: the payment and APR update as you choose. Sample values.')}</p></div>
   <div class="fin-page"><div class="card">${finBox()}</div>
   <aside class="fin-aside"><div class="card"><h3>${T('Come si calcola','How it is calculated')}</h3><p class="small muted">${T('Rate costanti al TAN indicato. Con la maxirata, l\'ultima quota (una percentuale del prezzo) si paga alla fine: si può saldare, rifinanziare o restituire l\'auto secondo il contratto.','Constant payments at the stated rate. With a balloon, the last instalment (a share of the price) is paid at the end: it can be paid, refinanced or the car returned under the contract.')}</p>
     <p class="formula">R = (C − B/(1+i)<sup>n</sup>) · i / (1 − (1+i)<sup>−n</sup>)</p><p class="xs dim">${T('C capitale finanziato, B maxirata, i = TAN/12, n mesi. Il TAEG include spese di istruttoria, bollo e incasso rata.','C financed capital, B balloon, i = rate/12, n months. APR includes arrangement, stamp duty and collection fees.')}</p></div>
    <div class="card"><h3>${T('Il passo successivo','Next step')}</h3><p class="small muted">${T('Porta la simulazione in un preventivo: il salone la verifica con la finanziaria.','Bring the simulation into a quote: the showroom checks it with the lender.')}</p>
     <a class="btn btn-primary btn-block" data-finlink href="#/preventivo?tipo=acquisto&fin=1">${T('Richiedi un preventivo con questa rata','Request a quote with this payment')} ${IC.arrow}</a><a class="btn btn-line btn-block" href="#/agenda?tipo=visita" style="margin-top:8px">${T('Parlane in salone','Discuss it in the showroom')}</a></div></aside></div></div></div>`}

/* ================= PREVENTIVO (vendita e noleggio) ================= */
let PQ=null;
function pqInit(q){q=q||{};const t=['acquisto','breve','lungo'].includes(q.tipo)?q.tipo:'acquisto';
  PQ={tipo:t,car:qsAuto(q)||CARS[0].id,from:q.da||'',to:q.a||'',m:36,km:15000,ltdown:0,fin:q.fin==='1'||t==='acquisto',perm:false,v:{},err:{},done:null};
  if(q.ant!=null&&q.ant!==''){FN.down=+q.ant;FN.months=+q.mesi||FN.months;FN.balloon=+q.maxi>0;if(+q.maxi>0)FN.bPct=+q.maxi;if(q.tan)FN.tan=+q.tan}}
function viewQuote(q){pqInit(q);FN.page=false;FN.car=PQ.car;
  return `<div class="page"><div class="wrap"><div class="page-head"><p class="eyebrow">${T('Preventivo','Quote')}</p><h1>${T('Il tuo preventivo, <em>su misura.</em>','Your quote, <em>tailored.</em>')}</h1>
   <p class="lead">${T('Acquisto, noleggio breve o lungo termine: raccogliamo i dati, generiamo il riepilogo e lo mandi al salone su WhatsApp.','Purchase, short-term or long-term rental: we collect the details, generate the summary and you send it to the showroom on WhatsApp.')}</p></div><div id="pqBody">${pqBody()}</div></div></div>`}
function pqBody(){if(PQ.done)return pqDone();const P=PQ,c=carBy(P.car),v=P.v,e=P.err;
  let mid='';
  if(P.tipo==='acquisto'){mid=`<label class="check"><input type="checkbox" data-chg="pq" name="fin" ${P.fin?'checked':''}><span>${T('Includi il finanziamento','Include finance')}</span></label>${P.fin?`<div class="card inset">${finBox()}</div>`:''}
    <label class="check"><input type="checkbox" data-chg="pq" name="perm" ${P.perm?'checked':''}><span>${T('Ho un\'auto da dare in permuta','I have a car to trade in')}</span></label>
    ${P.perm?`<div class="grid3">${field('tmod',T('Marca e modello','Make and model'),{v:v.tmod,ph:'es. Audi A4',err:e.tmod})}${field('tyear',T('Anno','Year'),{type:'number',v:v.tyear,attr:'min="1990" max="2026"'})}${field('tkm','Km',{type:'number',v:v.tkm,attr:'min="0"'})}</div>`:''}`}
  if(P.tipo==='breve'){const q=quote(c,{from:P.from,to:P.to});mid=`<div class="grid2"><label class="field"><span>${T('Ritiro','Pick-up')}</span><input class="inp" type="date" data-chg="pq" name="from" min="${iso(TODAY)}" value="${P.from}"></label><label class="field"><span>${T('Riconsegna','Return')}</span><input class="inp" type="date" data-chg="pq" name="to" min="${P.from||iso(TODAY)}" value="${P.to}"></label></div>${e.dates?`<span class="err" role="alert">${e.dates}</span>`:''}
    <div id="pqCalc">${q.ok?sumRows([[q.days+' × '+eur(c.day),eur(q.base)],q.weekend?[T('Weekend','Weekend'),eur(q.weekend)]:null,q.disc?[T('Sconto durata','Duration discount'),'−'+eur(q.disc),'neg']:null,[T('Km inclusi','Km included'),num(q.km)+' km'],[T('Cauzione','Deposit'),eur(c.dep)],[T('Totale indicativo','Indicative total'),eur(q.total),'tot']]):`<p class="small muted">${T('Scegli le date per il totale indicativo.','Pick dates for the indicative total.')}</p>`}</div>`}
  if(P.tipo==='lungo'){const r=ltCanone(c,P.m,P.km,P.ltdown);mid=`<div class="grid3"><label class="field"><span>${T('Durata','Term')}</span><select class="inp" data-chg="pq" name="m">${[12,24,36,48].map(m=>`<option value="${m}" ${P.m===m?'selected':''}>${m} ${T('mesi','months')}</option>`).join('')}</select></label>
    <label class="field"><span>${T('Km all\'anno','Km per year')}</span><select class="inp" data-chg="pq" name="km">${[10000,15000,20000,30000].map(k=>`<option value="${k}" ${P.km===k?'selected':''}>${num(k)}</option>`).join('')}</select></label>
    <label class="field"><span>${T('Anticipo (€)','Upfront (€)')}</span><input class="inp" type="number" min="0" step="1000" data-chg="pq" name="ltdown" value="${P.ltdown}"></label></div>
    <div id="pqCalc">${sumRows([[T('Canone mensile indicativo','Indicative monthly fee'),eur(r.canone)+'/'+T('mese','mo'),'tot'],[T('Km totali inclusi','Total km included'),num(r.kmTot)+' km']])}</div>`}
  return `<form class="pq" data-form="quote" novalidate><div class="pq-main">
   <section class="ag-step"><h2 class="h-step"><span>01</span>${T('Tipo di preventivo','Quote type')}</h2><div class="types" role="radiogroup">${[['acquisto',T('Acquisto','Purchase'),T('Con finanziamento e permuta, se vuoi','With finance and trade-in, if you like')],['breve',T('Noleggio breve','Short-term rental'),T('Giorni o settimane','Days or weeks')],['lungo',T('Lungo termine','Long-term'),T('Da 12 a 48 mesi','12 to 48 months')]].map(t=>`<button type="button" class="type" role="radio" aria-checked="${P.tipo===t[0]}" data-act="pqType" data-t="${t[0]}"><b>${t[1]}</b><small>${t[2]}</small></button>`).join('')}</div></section>
   <section class="ag-step"><h2 class="h-step"><span>02</span>${T('Auto e dettagli','Car and details')}</h2>
    <div class="pq-car"><div class="pq-img">${carSVG(c)}</div><div><label class="field"><span>${T('Auto','Car')}</span><select class="inp" data-chg="pq" name="car">${carOpts(P.car)}</select></label></div></div>${mid}</section>
   <section class="ag-step"><h2 class="h-step"><span>03</span>${T('I tuoi dati','Your details')}</h2>
    <div class="grid2">${field('nome',T('Nome e cognome','Full name'),{req:1,v:v.nome,ac:'name',err:e.nome})}${field('tel',T('Telefono','Phone'),{req:1,type:'tel',v:v.tel,ac:'tel',ph:'+39 …',err:e.tel})}</div>
    ${field('mail','Email',{type:'email',v:v.mail,ac:'email',ph:T('facoltativa','optional'),err:e.mail})}
    <label class="field"><span>${T('Note','Notes')}</span><textarea class="inp" name="note" rows="3">${esc(v.note||'')}</textarea></label>${privacyBox(v.privacy,e.privacy)}
    <button class="btn btn-primary">${T('Genera il riepilogo','Generate the summary')} ${IC.arrow}</button></section></div></form>`}
const pqSave=()=>{const f=$('form[data-form=quote]');if(!f)return;const d=new FormData(f);['nome','tel','mail','note','tmod','tyear','tkm'].forEach(k=>{if(d.has(k))PQ.v[k]=d.get(k)});PQ.v.privacy=!!d.get('privacy')};
ACT.pqType=el=>{pqSave();PQ.tipo=el.dataset.t;PQ.err={};$('#pqBody').innerHTML=pqBody()};
CHG.pq=el=>{pqSave();const n=el.name;if(n==='fin'||n==='perm')PQ[n]=el.checked;else if(['m','km','ltdown'].includes(n))PQ[n]=+el.value||0;else PQ[n]=el.value;
  if(n==='car'){FN.car=PQ.car;FN.down=null}if(n==='from'&&PQ.to&&PQ.to<PQ.from)PQ.to='';$('#pqBody').innerHTML=pqBody()};
FORM.quote=()=>{pqSave();const P=PQ,v=P.v,e={},c=carBy(P.car);
  if(!(v.nome||'').trim())e.nome=T('Campo obbligatorio','Required field');if(!telOk(v.tel))e.tel=T('Numero non valido','Invalid number');if(!mailOk(v.mail))e.mail=T('Email non valida','Invalid email');if(!v.privacy)e.privacy=T('Serve il consenso per ricontattarti','Consent is needed to contact you');
  if(P.tipo==='breve'&&!quote(c,{from:P.from,to:P.to}).ok)e.dates=T('Scegli date valide','Choose valid dates');if(P.tipo==='acquisto'&&P.perm&&!(v.tmod||'').trim())e.tmod=T('Indica la tua auto','Tell us your car');
  P.err=e;if(Object.keys(e).length){$('#pqBody').innerHTML=pqBody();const x=$('#pqBody [aria-invalid=true],#pqBody .err');x&&x.scrollIntoView({block:'center',behavior:'smooth'});return}
  const lines=[],kindLabel={acquisto:'Acquisto',breve:'Noleggio breve',lungo:'Noleggio lungo termine'}[P.tipo];let detail='',finTxt='',amount=0;
  lines.push([T('Auto','Car'),carName(c)]);
  if(P.tipo==='acquisto'){amount=c.sale;lines.push([T('Prezzo','Price'),eur(amount)]);detail='Prezzo '+eur(amount);
    if(P.fin){const f=finCalcNow();finTxt=finLabel(f);lines.push([T('Rata','Payment'),eur2(f.rata)+'/'+T('mese','mo')+' × '+f.n],[T('Anticipo','Down payment'),eur(f.A)],f.B?[T('Maxirata','Balloon'),eur(f.B)]:null,['TAN / TAEG',pct(f.tan)+' / '+pct(f.taeg)])}
    if(P.perm){lines.push([T('Permuta','Trade-in'),[v.tmod,v.tyear,v.tkm?num(+v.tkm)+' km':''].filter(Boolean).join(', ')]);detail+=' · Permuta: '+[v.tmod,v.tyear,v.tkm?v.tkm+' km':''].filter(Boolean).join(', ')}}
  if(P.tipo==='breve'){const q=quote(c,{from:P.from,to:P.to});amount=q.total;lines.push([T('Periodo','Period'),fmtD(fromIso(P.from))+' → '+fmtD(fromIso(P.to))+' ('+q.days+' '+T('giorni','days')+')'],[T('Km inclusi','Km included'),num(q.km)+' km'],[T('Cauzione','Deposit'),eur(c.dep)],[T('Totale indicativo','Indicative total'),eur(q.total)]);detail=fmtD(fromIso(P.from))+' → '+fmtD(fromIso(P.to))+', totale indicativo '+eur(q.total)}
  if(P.tipo==='lungo'){const r=ltCanone(c,P.m,P.km,P.ltdown);amount=r.canone;lines.push([T('Durata','Term'),P.m+' '+T('mesi','months')],[T('Km all\'anno','Km per year'),num(P.km)],[T('Anticipo','Upfront'),eur(P.ltdown)],[T('Canone indicativo','Indicative fee'),eur(r.canone)+'/'+T('mese','mo')]);detail=P.m+' mesi, '+num(P.km)+' km/anno, anticipo '+eur(P.ltdown)+', canone indicativo '+eur(r.canone)+'/mese'}
  const rec=saveQuote({tipo:P.tipo,kindLabel,car:c.id,detail,fin:finTxt,amount,lines:lines.filter(Boolean),who:v.nome.trim(),tel:v.tel.trim(),mail:(v.mail||'').trim(),note:(v.note||'').trim()});
  upsertLead({who:rec.who,mail:rec.mail,tel:rec.tel,type:P.tipo==='lungo'?'lungo':P.tipo==='breve'?'noleggio':(P.fin?'finanziamento':'preventivo'),car:c.id,note:rec.code});
  P.done=rec;$('#pqBody').innerHTML=pqBody();scrollTo({top:0,behavior:'smooth'})};
function pqDone(){const r=PQ.done,c=carBy(r.car);
  return `<div class="done"><div class="done-main"><article class="card summary-doc" id="pqDoc"><div class="sd-head"><div><p class="eyebrow">${T('Riepilogo preventivo','Quote summary')}</p><h2>${r.code}</h2><p class="xs dim">${fmtD(r.made,{day:'numeric',month:'long',year:'numeric',hour:'2-digit',minute:'2-digit'})} · ${T('valori di esempio','sample values')}</p></div><a class="logo" href="#/"><span>MC</span><b></b></a></div>
    <div class="sd-car">${carSVG(c)}</div>
    ${sumRows([[T('Tipo','Type'),T({acquisto:'Acquisto',breve:'Noleggio breve',lungo:'Noleggio a lungo termine'}[r.tipo],{acquisto:'Purchase',breve:'Short-term rental',lungo:'Long-term rental'}[r.tipo])]].concat(r.lines).concat([[T('Nome','Name'),esc(r.who)],[T('Telefono','Phone'),esc(r.tel)],r.mail?['Email',esc(r.mail)]:null,r.note?[T('Note','Notes'),esc(r.note)]:null]))}
    <p class="xs dim" style="margin-top:14px">${T('Documento generato nel browser dal prototipo: non è un\'offerta vincolante. Prezzi, rate e canoni sono di esempio.','Document generated in the browser by the prototype: not a binding offer. Prices, payments and fees are samples.')}</p></article>
   <div class="cta"><button class="btn btn-line btn-sm" data-act="print">${T('Stampa o salva PDF','Print or save PDF')}</button><button class="btn btn-ghost btn-sm" data-act="pqNew">${T('Nuovo preventivo','New quote')}</button><a class="btn btn-ghost btn-sm" href="#/admin/preventivi">${T('Vedi nell\'area salone','See in the showroom area')}</a></div></div>
   <div class="done-side">${waFinal('quote',r,'quotes')}</div></div>`}
ACT.pqNew=()=>{PQ.done=null;PQ.v={};$('#pqBody').innerHTML=pqBody()};
ACT.print=()=>{try{window.print()}catch(e){}};

/* ================= PERMUTA ================= */
let TR={seg:'comp',year:new Date().getFullYear()-5,km:60000,cond:'buone',serv:'completo',v:{},err:{},done:null};
function viewTrade(q){if(TR.done)TR={seg:'comp',year:new Date().getFullYear()-5,km:60000,cond:'buone',serv:'completo',v:{},err:{},done:null};if(q&&qsAuto(q))TR.target=q.auto;
  return `<div class="page"><div class="wrap"><div class="page-head"><p class="eyebrow">${T('Permuta','Trade-in')}</p><h1>${T('Quanto vale <em>la tua auto?</em>','What is <em>your car worth?</em>')}</h1>
   <p class="lead">${T('Una forchetta indicativa in un minuto. La valutazione vera si fa vedendo l\'auto e i documenti.','An indicative range in a minute. The real valuation is made on seeing the car and documents.')}</p></div><div id="trBody">${trBody()}</div></div></div>`}
function trBody(){if(TR.done)return trDone();const v=TR.v,e=TR.err,est=tradeIn(TR),y=new Date().getFullYear();
  return `<form class="tr" data-form="trade" novalidate><div class="tr-main card">
   <div class="grid2">${field('tmod',T('Marca e modello','Make and model'),{req:1,v:v.tmod,ph:'es. BMW Serie 3 320d',err:e.tmod})}<label class="field"><span>${T('Tipo di auto','Car type')}</span><select class="inp" data-chg="tr" name="seg">${Object.keys(SEGS).map(k=>`<option value="${k}" ${TR.seg===k?'selected':''}>${T(SEGS[k].it,SEGS[k].en)}</option>`).join('')}</select></label></div>
   <div class="grid2"><label class="field"><span>${T('Anno di immatricolazione','Registration year')}: <b id="trY">${TR.year}</b></span><input type="range" data-chg="tr" name="year" min="${y-15}" max="${y}" value="${TR.year}"></label>
    <label class="field"><span>${T('Chilometri','Mileage')}: <b id="trK">${num(TR.km)} km</b></span><input type="range" data-chg="tr" name="km" min="0" max="250000" step="5000" value="${TR.km}"></label></div>
   <div class="grid2"><div class="field"><span>${T('Condizioni','Condition')}</span><div class="chips">${[['ottime',T('Ottime','Excellent')],['buone',T('Buone','Good')],['sistemare',T('Da sistemare','Needs work')]].map(o=>`<button type="button" class="chip" data-act="trSet" data-k="cond" data-v="${o[0]}" aria-pressed="${TR.cond===o[0]}">${o[1]}</button>`).join('')}</div></div>
    <div class="field"><span>${T('Tagliandi','Service history')}</span><div class="chips">${[['completo',T('Completi','Complete')],['parziale',T('Parziali','Partial')],['assente',T('Assenti','None')]].map(o=>`<button type="button" class="chip" data-act="trSet" data-k="serv" data-v="${o[0]}" aria-pressed="${TR.serv===o[0]}">${o[1]}</button>`).join('')}</div></div></div>
   <div class="est" id="trEst">${trEst(est)}</div>
   <h3 style="margin-top:8px">${T('Ricevi la valutazione del salone','Get the showroom\'s valuation')}</h3>
   <div class="grid2">${field('nome',T('Nome e cognome','Full name'),{req:1,v:v.nome,ac:'name',err:e.nome})}${field('tel',T('Telefono','Phone'),{req:1,type:'tel',v:v.tel,ac:'tel',ph:'+39 …',err:e.tel})}</div>
   <label class="field"><span>${T('Auto che ti interessa (facoltativo)','Car you are interested in (optional)')}</span><select class="inp" name="target">${carOpts(TR.target,T('Nessuna in particolare','None in particular'))}</select></label>
   ${privacyBox(v.privacy,e.privacy)}<button class="btn btn-primary">${T('Genera il riepilogo','Generate the summary')} ${IC.arrow}</button></div></form>`}
const trEst=est=>`<span>${T('Stima indicativa di ritiro','Indicative trade-in estimate')}</span><b class="num">${eur(est.lo)} – ${eur(est.hi)}</b><small>${T('Esempio calcolato su tipo, età, km, condizioni e tagliandi. Non è un\'offerta.','Example based on type, age, km, condition and service history. Not an offer.')}</small>`;
const trSave=()=>{const f=$('form[data-form=trade]');if(!f)return;const d=new FormData(f);['tmod','nome','tel'].forEach(k=>TR.v[k]=d.get(k)||'');TR.v.privacy=!!d.get('privacy');TR.target=d.get('target')||''};
const trUpd=()=>{const e=$('#trEst');if(e)e.innerHTML=trEst(tradeIn(TR));const y=$('#trY');if(y)y.textContent=TR.year;const k=$('#trK');if(k)k.textContent=num(TR.km)+' km'};
CHG.tr=el=>{TR[el.name]=el.name==='seg'?el.value:+el.value;trUpd()};
ACT.trSet=el=>{TR[el.dataset.k]=el.dataset.v;$$(`[data-act=trSet][data-k=${el.dataset.k}]`).forEach(b=>b.setAttribute('aria-pressed',b===el));trUpd()};
FORM.trade=()=>{trSave();const v=TR.v,e={};if(!(v.tmod||'').trim())e.tmod=T('Indica la tua auto','Tell us your car');if(!(v.nome||'').trim())e.nome=T('Campo obbligatorio','Required field');if(!telOk(v.tel))e.tel=T('Numero non valido','Invalid number');if(!v.privacy)e.privacy=T('Serve il consenso per ricontattarti','Consent is needed to contact you');
  TR.err=e;if(Object.keys(e).length){$('#trBody').innerHTML=trBody();return}
  const est=tradeIn(TR),L=store.get('trades',[]),rec={code:'PM-'+Date.now().toString(36).slice(-5).toUpperCase(),made:Date.now(),tmod:v.tmod.trim()+' · '+TR.year+' · '+num(TR.km)+' km',seg:TR.seg,cond:TR.cond,serv:TR.serv,range:eur(est.lo)+' – '+eur(est.hi)+' (indicativa)',car:TR.target,who:v.nome.trim(),tel:v.tel.trim()};
  L.unshift(rec);store.set('trades',L);upsertLead({who:rec.who,tel:rec.tel,type:'permuta',car:rec.car,note:rec.code});TR.done=rec;$('#trBody').innerHTML=trBody();scrollTo({top:0,behavior:'smooth'})};
function trDone(){const r=TR.done;return `<div class="done"><div class="done-main"><article class="card summary-doc"><div class="sd-head"><div><p class="eyebrow">${T('Valutazione permuta','Trade-in valuation')}</p><h2>${r.code}</h2></div></div>
  ${sumRows([[T('La tua auto','Your car'),esc(r.tmod)],[T('Condizioni','Condition'),r.cond],[T('Tagliandi','Service'),r.serv],[T('Stima indicativa','Indicative estimate'),r.range,'tot'],r.car?[T('Auto di interesse','Car of interest'),carName(carBy(r.car))]:null,[T('Nome','Name'),esc(r.who)],[T('Telefono','Phone'),esc(r.tel)]])}
  <p class="xs dim" style="margin-top:12px">${T('Stima di esempio, non vincolante.','Sample estimate, not binding.')}</p></article><div class="cta"><button class="btn btn-ghost btn-sm" data-act="trNew">${T('Nuova valutazione','New valuation')}</button></div></div><div class="done-side">${waFinal('trade',r,'trades')}</div></div>`}
ACT.trNew=()=>{TR.done=null;TR.v={};$('#trBody').innerHTML=trBody()};

/* ================= LUNGO TERMINE ================= */
function viewLong(){const L=DT.lt;return `<div class="page"><div class="wrap"><div class="page-head"><p class="eyebrow">${T('Noleggio a lungo termine','Long-term rental')}</p><h1>${T('Un canone, <em>tutto incluso.</em>','One fee, <em>all included.</em>')}</h1>
  <p class="lead">${T('Da 12 a 48 mesi con assicurazione, manutenzione, pneumatici e assistenza. Canoni indicativi di esempio.','12 to 48 months with insurance, maintenance, tyres and assistance. Sample indicative fees.')}</p>
  <div class="seg" role="tablist"><a role="tab" aria-selected="false" href="#/noleggio">${T('Breve termine','Short-term')}</a><a role="tab" aria-selected="true" href="#/lungo-termine">${T('Lungo termine','Long-term')}</a></div></div>
  <div class="card lt-bar"><div class="grid3"><label class="field"><span>${T('Durata','Term')}</span><select class="inp" data-chg="ltp" name="m">${[12,24,36,48].map(m=>`<option value="${m}" ${L.m===m?'selected':''}>${m} ${T('mesi','months')}</option>`).join('')}</select></label>
   <label class="field"><span>${T('Km all\'anno','Km per year')}</span><select class="inp" data-chg="ltp" name="km">${[10000,15000,20000,30000].map(k=>`<option value="${k}" ${L.km===k?'selected':''}>${num(k)}</option>`).join('')}</select></label>
   <label class="field"><span>${T('Anticipo (€)','Upfront (€)')}</span><input class="inp" type="number" min="0" step="1000" data-chg="ltp" name="down" value="${L.down}"></label></div></div>
  <div class="cars" id="ltList">${ltList()}</div></div></div>`}
function ltList(){const L=DT.lt;return CARS.map(c=>{const r=ltCanone(c,L.m,L.km,L.down);return `<article class="car reveal in"><a class="car-img" href="#/auto/${c.id}">${carSVG(c)}</a><div class="car-body"><div><div class="brand">${c.brand}</div><h3>${c.model}</h3></div>
  <div class="car-foot"><div class="price"><span class="num">${eur(r.canone)}<small> /${T('mese','mo')}</small></span><small>${L.m} ${T('mesi','months')} · ${num(L.km)} km/${T('anno','yr')} · ${T('esempio','example')}</small></div><a class="btn btn-line btn-sm" href="#/preventivo?tipo=lungo&auto=${c.id}">${T('Preventivo','Quote')} ${IC.arrow}</a></div></div></article>`}).join('')}
CHG.ltp=el=>{DT.lt[el.name]=+el.value||0;$('#ltList').innerHTML=ltList()};

/* ================= CHI SIAMO ================= */
function viewAbout(){return `<div class="page about"><div class="wrap"><div class="page-head"><p class="eyebrow">${T('Chi siamo','About us')}</p><h1>${seoH1('chi_siamo',T('Poche auto. Scelte una per una.','Few cars. Chosen one by one.'))}</h1>
  <p class="lead">${T('MC è un salone di auto speciali in Via Arbe, a Milano: noleggio, vendita e servizi foto e marketing con le auto. Testi di presentazione da completare con la storia vera di MC.','MC is a special-car showroom in Via Arbe, Milan: rental, sales, and photo & marketing services with the cars. Presentation copy to be completed with MC\'s real story.')} <span class="demo-tag">${T('testo provvisorio','placeholder text')}</span></p></div>
  <div class="about-grid"><div class="about-media">${carSVG(CARS[4],{hex:'#16171a',rim:'disc',fin:'titanio',label:''})}</div>
   <div class="about-copy"><h2>${T('Il nostro <em>metodo.</em>','Our <em>method.</em>')}</h2>${[[T('Selezione','Selection'),T('Ogni auto entra in collezione dopo controlli su storico, km e condizioni.','Each car joins the collection after checks on history, mileage and condition.')],[T('Trasparenza','Transparency'),T('Prezzi, cauzioni, km inclusi e rate sono chiari prima di decidere.','Prices, deposits, included km and payments are clear before you decide.')],[T('Una persona vera','A real person'),T('Al telefono e su WhatsApp risponde chi conosce le auto, non un call center.','On the phone and WhatsApp you talk to someone who knows the cars, not a call centre.')]].map((x,i)=>`<div class="val"><span class="n">0${i+1}</span><div><h3>${x[0]}</h3><p class="muted">${x[1]}</p></div></div>`).join('')}</div></div>
  <div class="statline"><div><b class="num">6</b><span>${T('auto in collezione','cars in the collection')}</span></div><div><b class="num">3</b><span>${T('aeroporti serviti','airports served')}</span></div><div><b class="num">24h</b><span>${T('per un preventivo','for a quote')} ${demoTag()}</span></div><div><b class="num">1</b><span>${T('indirizzo: Via Arbe 95','address: Via Arbe 95')}</span></div></div>
  <section class="sec tight">${secHead(T('Cosa facciamo','What we do'),T('Tre mestieri, <em>una passione.</em>','Three trades, <em>one passion.</em>'))}<div class="svc">${[['#/noleggio',T('Noleggio','Rental'),T('Breve e lungo termine, con consegna a domicilio e in aeroporto.','Short and long term, with home and airport delivery.')],['#/vendita',T('Vendita','Sales'),T('Auto selezionate, permuta e finanziamento su misura.','Selected cars, trade-in and tailored finance.')],['#/agenda?tipo=visita',T('Immagine','Image'),T('Shooting, eventi, cerimonie e campagne con le nostre auto.','Shoots, events, weddings and campaigns with our cars.')]].map((s,i)=>`<a class="svc-i reveal" href="${s[0]}"><span class="n">0${i+1}</span><h3>${s[1]}</h3><p>${s[2]}</p><span class="go">${IC.arrow}</span></a>`).join('')}</div></section>
  <section class="sec tight">${secHead(T('Le persone','The people'),T('Il <em>team.</em>','The <em>team.</em>'))}<div class="grid3">${[1,2,3].map(()=>`<div class="ph" style="aspect-ratio:4/5"><div><b>${T('Foto del team','Team photo')}</b>${T('Da fornire: nessun volto inventato','To be supplied: no invented faces')}</div></div>`).join('')}</div></section>
  <section class="sec tight">${whereBlock()}</section></div></div>`}

/* ================= PREFERITI ================= */
function viewFavs(){const f=favs().map(carBy).filter(Boolean);return `<div class="page"><div class="wrap"><div class="page-head"><p class="eyebrow">${T('Preferiti','Favourites')}</p><h1>${T('Le tue <em>preferite.</em>','Your <em>favourites.</em>')}</h1><p class="lead">${T('Salvate in questo browser. Seleziona fino a 3 auto per confrontarle.','Saved in this browser. Select up to 3 cars to compare them.')}</p></div>
  ${f.length?`<div class="cars">${f.map(c=>carCard(c)).join('')}</div>`:`<div class="empty"><b>${T('Nessun preferito','No favourites yet')}</b><p>${T('Tocca il cuore su un\'auto per salvarla qui.','Tap the heart on a car to save it here.')}</p><a class="btn btn-primary btn-sm" href="#/noleggio">${T('Sfoglia la collezione','Browse the collection')}</a></div>`}</div></div>`}

/* ================= TI RICHIAMIAMO ================= */
let CB={v:{},err:{},done:null};
ACT.callback=()=>{CB={v:{},err:{},done:null};openModal(cbHtml(),true)};
function cbHtml(){const v=CB.v,e=CB.err;if(CB.done)return `<div class="sheet-head"><div><p class="eyebrow">${T('Ti richiamiamo','We call you back')}</p><h2>${T('Richiesta registrata','Request recorded')}</h2></div><button class="x" data-act="closeModal" aria-label="${T('Chiudi','Close')}">×</button></div>${waFinal('call',CB.done,'calls')}`;
  return `<div class="sheet-head"><div><p class="eyebrow">${T('Ti richiamiamo','We call you back')}</p><h2>${T('Lascia il numero','Leave your number')}</h2></div><button class="x" data-act="closeModal" aria-label="${T('Chiudi','Close')}">×</button></div>
  <form class="stack-s" data-form="callback" novalidate>${field('nome',T('Nome','Name'),{req:1,v:v.nome,ac:'name',err:e.nome})}${field('tel',T('Telefono','Phone'),{req:1,type:'tel',v:v.tel,ac:'tel',ph:'+39 …',err:e.tel})}
   <label class="field"><span>${T('Quando preferisci','When do you prefer')}</span><select class="inp" name="slot">${[T('Mattina (9:30–12:30)','Morning (9:30–12:30)'),T('Pausa pranzo (12:30–14:30)','Lunch (12:30–14:30)'),T('Pomeriggio (14:30–18:30)','Afternoon (14:30–18:30)')].map(s=>`<option ${v.slot===s?'selected':''}>${s}</option>`).join('')}</select></label>
   <label class="field"><span>${T('Per cosa','About')}</span><select class="inp" name="car">${carOpts(v.car,T('Informazioni generali','General information'))}</select></label>
   ${privacyBox(v.privacy,e.privacy)}<button class="btn btn-primary btn-block">${T('Continua','Continue')} ${IC.arrow}</button></form>`}
FORM.callback=f=>{const d=new FormData(f),v=CB.v={nome:(d.get('nome')||'').trim(),tel:(d.get('tel')||'').trim(),slot:d.get('slot'),car:d.get('car'),privacy:!!d.get('privacy')},e={};
  if(!v.nome)e.nome=T('Campo obbligatorio','Required field');if(!telOk(v.tel))e.tel=T('Numero non valido','Invalid number');if(!v.privacy)e.privacy=T('Serve il consenso per richiamarti','Consent is needed to call you back');
  CB.err=e;if(!Object.keys(e).length){const L=store.get('calls',[]),rec={code:'RC-'+Date.now().toString(36).slice(-5).toUpperCase(),made:Date.now(),who:v.nome,tel:v.tel,slot:v.slot,car:v.car};L.unshift(rec);store.set('calls',L);upsertLead({who:v.nome,tel:v.tel,type:'richiamo',car:v.car,note:rec.code});CB.done=rec}
  $('#modal .sheet').innerHTML=cbHtml()};

/* ================= CREDITI FOTO (obbligatori per CC BY / BY-SA) ================= */
function viewCredits(){const rows=[];CARS.forEach(c=>carPhotos(c).forEach((p,i)=>rows.push([carName(c)+' · '+(i+1),p])));PHOTOS.atmo.forEach((p,i)=>rows.push([T('Atmosfera','Atmosphere')+' '+(i+1),p]));
  return `<div class="page"><div class="wrap narrow"><div class="page-head">${lbl('—',T('crediti','credits'))}<h1>${T('Crediti foto.','Photo credits.')}</h1><p class="lead">${T('Le foto del prototipo sono immagini di esempio con licenza libera, collegate dalla fonte originale. Non ritraggono le auto di MC: nella versione definitiva saranno sostituite dalle foto reali del salone.','The prototype\'s photos are freely licensed sample images, linked from the original source. They do not show MC\'s cars: in the final version they will be replaced by real showroom photos.')}</p></div>
   ${rows.length?`<div class="tbl-wrap"><table class="tbl"><thead><tr><th>${T('Foto','Photo')}</th><th>${T('Autore','Author')}</th><th>${T('Licenza','Licence')}</th><th>${T('Fonte','Source')}</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r[0])}${r[1].view?`<br><small class="dim">${esc(r[1].view)}</small>`:''}</td><td>${esc(r[1].author||'—')}</td><td>${r[1].licenseUrl?`<a class="link" href="${esc(r[1].licenseUrl)}" target="_blank" rel="noopener">${esc(r[1].license||'')}</a>`:esc(r[1].license||'')}</td><td>${r[1].source?`<a class="link" href="${esc(r[1].source)}" target="_blank" rel="noopener">${T('originale','original')} ↗</a>`:'—'}</td></tr>`).join('')}</tbody></table></div>`:`<p class="muted">${T('Nessuna foto esterna: il sito mostra illustrazioni generiche.','No external photos: the site shows generic illustrations.')}</p>`}
   <p class="small muted" style="margin-top:16px">${T('Le illustrazioni vettoriali sono generiche e non riproducono loghi o design di marca.','Vector illustrations are generic and do not reproduce logos or brand designs.')}</p></div></div>`}
