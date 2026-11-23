/* MC prototipo — booking engine dimostrativo in 10 passi.
   Calcolo prezzi, blocco temporaneo e controlli sono reali lato client; pagamento, documenti e firma sono simulati. */
'use strict';
let BK=null,holdTick=0;
const bkMode=()=>store.get('mode','auto');
const BKSTEPS=()=>[T('Auto','Car'),T('Date','Dates'),T('Ritiro','Pick-up'),T('Totale','Total'),T('Extra','Extras'),T('Dati','Details'),T('Documenti','Documents'),T('Acconto','Payment'),T('Firma','Signature'),T('Conferma','Done')];
function bkInit(id,qs){
  if(!BK||BK.car!==id||BK.code){BK={car:id,step:1,max:1,from:'',tf:'10:00',to:'',tt:'10:00',pu:'sede',dr:'sede',extras:[],cust:{},docs:{},paid:false,signed:false,hold:0,code:null,err:{}}}
  if(qs.da&&qs.a&&!BK.from){BK.from=qs.da;BK.to=qs.a;BK.step=2;BK.max=2}
}
const bkQ=()=>quote(carBy(BK.car),BK);
function viewBook(id,qs){const c=carBy(id);if(!c)return view404();bkInit(id,qs);
  return `<div class="page"><div class="wrap">
  <nav class="crumb"><a href="#/">Home</a>/<a href="#/noleggio">${T('Noleggio','Rental')}</a>/<a href="#/auto/${c.id}">${carName(c)}</a>/<span>${T('Prenotazione','Booking')}</span></nav>
  <div class="page-head" style="margin-top:18px"><h1 class="h-op">${T('Prenotazione','Booking')}</h1></div>
  <div class="bk"><div><div id="bkStepper"></div><div id="bkStep"></div></div><aside class="summary" id="bkSum"></aside></div></div></div>`}
function bkPaint(focus){if(!$('#bkStep'))return;const S=BKSTEPS();
  $('#bkStepper').innerHTML=`<div class="stepper" role="list" aria-label="${T('Passaggi','Steps')}">${S.map((s,i)=>{const n=i+1;return `<button role="listitem" data-act="bkGo" data-n="${n}" class="${n===BK.step?'on':n<BK.step?'done':''}" ${n>BK.max||BK.code?'disabled':''} ${n===BK.step?'aria-current="step"':''}><b>${String(n).padStart(2,'0')}</b><span>${s}</span></button>`}).join('')}</div>`;
  $('#bkStep').innerHTML=`<div class="bk-step">${bkStepHtml()}</div>`;bkSum();
  const on=$('.stepper .on');if(on){const st=on.parentNode;st.scrollLeft=Math.max(0,on.getBoundingClientRect().left-st.getBoundingClientRect().left+st.scrollLeft-24)}
  if(BK.step===9)signInit();if(focus){const h=$('#bkStep h2');h&&(h.tabIndex=-1,h.focus({preventScroll:true}));scrollTo({top:Math.max(0,$('#bkStepper').getBoundingClientRect().top+scrollY-110),behavior:'smooth'})}
}
function bkSum(){const el=$('#bkSum');if(!el)return;const c=carBy(BK.car),q=bkQ();
  const left=BK.hold?BK.hold-Date.now():0,exp=BK.hold&&left<=0&&!BK.code;
  el.innerHTML=`<div class="card" style="display:grid;gap:16px">
   <div class="sum-car"><div class="mini">${carSVG(c)}</div><div><div class="xs dim" style="letter-spacing:.16em;text-transform:uppercase">${c.brand}</div><b style="font:600 24px/1 var(--disp);text-transform:uppercase">${c.model}</b><div class="small muted">${paintName(c)}</div></div></div>
   ${BK.hold&&!BK.code?`<div class="hold ${exp?'exp':''}" role="status"><b class="num" id="holdTime">${exp?'00:00':mmss(left)}</b><span>${exp?T('Blocco scaduto: le date sono tornate libere.','Hold expired: the dates are free again.'):T('Date bloccate per te durante il checkout.','Dates held for you during checkout.')}</span></div>
     ${exp?`<button class="btn btn-primary btn-sm" data-act="bkRehold">${T('Rinnova il blocco','Renew the hold')}</button>`:`<button class="link xs" style="justify-self:start" data-act="bkExpire">${T('Demo: simula la scadenza','Demo: simulate expiry')}</button>`}`:''}
   ${q.ok?`<div class="rows">
     <div><span>${fmtDs(fromIso(BK.from))} ${BK.tf} → ${fmtDs(fromIso(BK.to))} ${BK.tt}</span><span>${q.days} ${T(q.days>1?'giorni':'giorno',q.days>1?'days':'day')}</span></div>
     <div><span>${q.days} × ${eur(c.day)}</span><span>${eur(q.base)}</span></div>
     ${q.weekend?`<div><span>${T('Supplemento weekend','Weekend surcharge')} +15%</span><span>${eur(q.weekend)}</span></div>`:''}
     ${q.disc?`<div class="neg"><span>${T('Sconto durata','Duration discount')} −${q.discPct*100}%</span><span>−${eur(q.disc)}</span></div>`:''}
     ${q.delivery?`<div><span>${T('Consegna e ritiro','Delivery & collection')}</span><span>${eur(q.delivery)}</span></div>`:''}
     ${q.extras.map(x=>`<div><span>${T(x.e.it,x.e.en)}</span><span>${eur(x.v)}</span></div>`).join('')}
     <div class="tot"><span>${T('Totale','Total')} ${demoTag()}</span><span>${eur(q.total)}</span></div>
     <div><span>${T('Acconto ora (30%)','Due now (30%)')}</span><span>${eur(q.now)}</span></div>
     <div><span>${T('Saldo al ritiro','Balance at pick-up')}</span><span>${eur(q.later)}</span></div>
     <div><span>${T('Cauzione (non incassata)','Deposit (not charged)')}</span><span>${eur(q.dep)}</span></div></div>`
    :`<p class="small muted">${T('Scegli le date per vedere il totale.','Pick dates to see the total.')}</p><div class="rows"><div><span>${T('Tariffa al giorno','Daily rate')} ${demoTag()}</span><span>${eur(c.day)}</span></div><div><span>${T('Cauzione','Deposit')}</span><span>${eur(c.dep)}</span></div></div>`}
  </div><p class="xs dim">${T('Importi di esempio. ','Sample amounts. ')}${T('Modalità attiva: ','Active mode: ')}<b style="color:var(--muted)">${bkMode()==='auto'?T('conferma automatica dopo le verifiche','automatic confirmation after checks'):T('approvazione del titolare','owner approval')}</b>. ${T('Si cambia dalla','Change it from the')} <a class="link xs" href="#/admin/impostazioni">dashboard</a>.</p>`;
  clearInterval(holdTick);if(BK.hold&&!BK.code&&!exp)holdTick=setInterval(()=>{const t=$('#holdTime');if(!t||!BK){clearInterval(holdTick);return}const l=BK.hold-Date.now();if(l<=0){clearInterval(holdTick);bkPaint();toast(T('Blocco scaduto','Hold expired'))}else t.textContent=mmss(l)},1000);
}
const mmss=ms=>{const s=Math.max(0,Math.ceil(ms/1000));return String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0')};
const bkNav=(nextLabel,o)=>`<div class="bk-nav">${BK.step>1?`<button class="btn btn-line" data-act="bkBack">← ${T('Indietro','Back')}</button>`:'<span></span>'}<button class="btn btn-primary" data-act="bkNext" ${o&&o.dis?'disabled':''} id="bkNextBtn">${nextLabel||T('Continua','Continue')} ${IC.arrow}</button></div>`;
const E=k=>BK.err[k]?`<span class="err" role="alert">${BK.err[k]}</span>`:'';
const inv=k=>BK.err[k]?'aria-invalid="true"':'';

function bkStepHtml(){const c=carBy(BK.car),q=bkQ(),n=BK.step,S=BKSTEPS(),hd=(t,p)=>`<div style="display:grid;gap:10px"><p class="eyebrow">${T('Passo','Step')} ${n} / 10</p><h2>${t}</h2>${p?`<p class="muted">${p}</p>`:''}</div>`;
  const expired=BK.hold&&BK.hold<Date.now()&&n>2&&n<10;
  if(expired)return hd(T('Blocco scaduto','Hold expired'))+`<div class="note bad" role="alert">${T('I 15 minuti di blocco sono finiti e le date sono tornate disponibili per altri clienti. Rinnova il blocco per continuare: ricontrolliamo la disponibilità.','The 15-minute hold ended and the dates are open to other clients again. Renew the hold to continue: availability is checked again.')}</div><div><button class="btn btn-primary" data-act="bkRehold">${T('Rinnova il blocco','Renew the hold')}</button></div>`;
  if(n===1)return hd(T('La tua auto','Your car'),T('Puoi cambiarla in ogni momento prima del pagamento.','You can change it any time before payment.'))+
    `<div style="display:grid;gap:10px">${CARS.map(x=>{const m=fleetState()[x.id]==='maint';return `<label class="extra ${x.id===BK.car?'on':''}" ${m?'style="opacity:.55;cursor:not-allowed"':''}><input type="radio" name="bkcar" value="${x.id}" data-chg="bkCar" ${x.id===BK.car?'checked':''} ${m?'disabled':''} style="position:absolute;opacity:0;pointer-events:none"><div style="display:flex;gap:14px;align-items:center;min-width:0"><div style="width:96px;flex:none">${carSVG(x)}</div><div style="min-width:0"><b>${carName(x)}</b><small>${m?T('In manutenzione','In maintenance'):x.cv+' '+PW()+' · '+paintName(x)}</small></div></div><span class="num">${eur(x.day)}</span></label>`}).join('')}</div>`+bkNav();
  if(n===2)return hd(T('Date e orari','Dates and times'),T('La tariffa è a giornate di 24 ore, con un\'ora di tolleranza alla riconsegna.','The rate is per 24-hour day, with one hour of grace at return.'))+
    `<div class="grid2"><label class="field"><span>${T('Data di ritiro','Pick-up date')}</span><input class="inp" type="date" data-chg="bkF" name="from" min="${iso(TODAY)}" value="${BK.from}" ${inv('from')}>${E('from')}</label>
     <label class="field"><span>${T('Ora di ritiro','Pick-up time')}</span><select class="inp" data-chg="bkF" name="tf">${TIMES.map(t=>`<option ${t===BK.tf?'selected':''}>${t}</option>`).join('')}</select></label>
     <label class="field"><span>${T('Data di riconsegna','Return date')}</span><input class="inp" type="date" data-chg="bkF" name="to" min="${BK.from||iso(TODAY)}" value="${BK.to}" ${inv('to')}>${E('to')}</label>
     <label class="field"><span>${T('Ora di riconsegna','Return time')}</span><select class="inp" data-chg="bkF" name="tt">${TIMES.map(t=>`<option ${t===BK.tt?'selected':''}>${t}</option>`).join('')}</select></label></div>
     <div id="bkAvail" aria-live="polite">${bkAvail()}</div>
     <p class="xs dim">${T('Orari nella fascia di apertura (9:00–18:00, da confermare). Per orari diversi scrivici su WhatsApp.','Times within opening hours (9:00–18:00, to be confirmed). For other times, message us on WhatsApp.')}</p>`+bkNav(T('Blocca le date e continua','Hold the dates and continue'));
  if(n===3){const list=(k,t)=>`<fieldset style="border:0;padding:0;margin:0;display:grid;gap:8px;min-width:0"><legend class="field" style="margin-bottom:8px"><span>${t}</span></legend>${PLACES.map(p=>`<label class="extra ${BK[k]===p.id?'on':''}"><input type="radio" name="${k}" value="${p.id}" data-chg="bkPlace" ${BK[k]===p.id?'checked':''} style="position:absolute;opacity:0;pointer-events:none"><b style="font-size:14.5px">${T(p.it,p.en)}</b><span class="num" style="font-size:19px">${p.fee?'+'+eur(p.fee):T('Gratis','Free')}</span></label>`).join('')}</fieldset>`;
    return hd(T('Ritiro e riconsegna','Pick-up and return'),T('Possono essere due punti diversi.','They can be two different places.'))+`<div class="grid2" style="gap:22px">${list('pu',T('Dove ritiri','Where you collect'))}${list('dr',T('Dove riconsegni','Where you return'))}</div>`+bkNav()}
  if(n===4){const cp=cancelPolicy(BK.from,q.now);
    return hd(T('Il totale, voce per voce','The total, line by line'),T('Nessun costo nascosto: è lo stesso calcolo che vedi nel riepilogo.','No hidden costs: it is the same calculation shown in the summary.'))+
    `<div class="card"><div class="rows" style="font-size:15px"><div><span>${T('Tariffa','Rate')}: ${q.days} × ${eur(c.day)}</span><span>${eur(q.base)}</span></div>
      <div><span>${T('Supplemento weekend (ven–dom, +15%)','Weekend surcharge (Fri–Sun, +15%)')}</span><span>${eur(q.weekend)}</span></div>
      <div class="${q.disc?'neg':''}"><span>${T('Sconto durata','Duration discount')} (${T('3+ giorni −10%, 7+ giorni −20%','3+ days −10%, 7+ days −20%')})</span><span>${q.disc?'−'+eur(q.disc):eur(0)}</span></div>
      <div><span>${T('Ritiro','Pick-up')}: ${T(q.pu.it,q.pu.en)}</span><span>${eur(q.pu.fee)}</span></div><div><span>${T('Riconsegna','Return')}: ${T(q.dr.it,q.dr.en)}</span><span>${eur(q.dr.fee)}</span></div>
      <div><span>${T('Extra (li scegli al prossimo passo)','Extras (chosen at the next step)')}</span><span>${eur(q.extraTot)}</span></div>
      <div class="tot"><span>${T('Totale noleggio','Rental total')} ${demoTag()}</span><span>${eur(q.total)}</span></div></div></div>
     <div class="kv"><div><span>${T('Acconto ora','Due now')}</span><b>${eur(q.now)}</b></div><div><span>${T('Saldo al ritiro','Balance at pick-up')}</span><b>${eur(q.later)}</b></div><div><span>${T('Cauzione pre-autorizzata','Pre-authorised deposit')}</span><b>${eur(q.dep)}</b></div><div><span>${T('Km inclusi','Km included')}</span><b>${num(c.kmDay*q.days)} km</b></div></div>
     <div class="note"><b style="color:var(--text)">${T('Cancellazione','Cancellation')}.</b> ${T('Fino a 7 giorni prima: acconto rimborsato al 100%. Da 7 a 3 giorni: 50%. Sotto i 3 giorni: non rimborsabile.','Up to 7 days before: 100% of the down payment refunded. 7 to 3 days: 50%. Under 3 days: non-refundable.')} ${cp?`<br>${T('Con queste date, annullando oggi','With these dates, cancelling today')} (${cp.days} ${T('giorni prima','days before')}): <b style="color:var(--text)">${T('rimborso di','refund of')} ${eur(cp.refund)}</b>.`:''}</div>`+bkNav()}
  if(n===5)return hd(T('Servizi extra','Extras'),T('Facoltativi. Il totale a destra si aggiorna subito.','Optional. The total updates straight away.'))+
    `<div style="display:grid;gap:10px">${EXTRAS.map(e=>{const on=BK.extras.includes(e.id),p=extraPrice(c,e);return `<label class="extra ${on?'on':''}"><div style="display:flex;gap:12px;align-items:center"><input type="checkbox" data-chg="bkExtra" value="${e.id}" ${on?'checked':''} style="width:20px;height:20px;accent-color:var(--accent);flex:none"><div><b>${T(e.it,e.en)}</b><small>${T(e.dIt,e.dEn)}</small></div></div><span class="num">${eur(p)}<small class="muted" style="font:500 12px var(--sans)"> ${e.per==='day'?'/'+T('g','d'):''}</small></span></label>`}).join('')}</div>`+bkNav();
  if(n===6){const u=BK.cust,f=(k,l,t,ac,ph)=>`<label class="field"><span>${l}</span><input class="inp" type="${t||'text'}" name="${k}" data-chg="bkCust" value="${esc(u[k]||'')}" autocomplete="${ac||'off'}" ${ph?`placeholder="${ph}"`:''} ${inv(k)}>${E(k)}</label>`;
    return hd(T('I tuoi dati','Your details'),T('Servono per il contratto. Restano nel tuo browser: questo prototipo non li invia a nessuno.','Needed for the contract. They stay in your browser: this prototype sends them nowhere.'))+
    `<div class="grid2">${f('nome',T('Nome','First name'),'text','given-name')}${f('cognome',T('Cognome','Last name'),'text','family-name')}${f('mail','Email','email','email','nome@esempio.it')}${f('tel',T('Telefono','Phone'),'tel','tel','+39 …')}${f('nascita',T('Data di nascita','Date of birth'),'date','bday')}${f('citta',T('Città di residenza','City of residence'),'text','address-level2')}</div>
     <label class="check"><input type="checkbox" data-chg="bkCust" name="privacy" ${u.privacy?'checked':''}><span>${T('Ho letto l\'informativa privacy e accetto il trattamento dei dati per la gestione del noleggio. (obbligatorio)','I have read the privacy notice and accept data processing to manage the rental. (required)')}</span></label>${E('privacy')}
     <label class="check"><input type="checkbox" data-chg="bkCust" name="mkt" ${u.mkt?'checked':''}><span>${T('Voglio ricevere offerte e novità da MC. (facoltativo)','I want to receive offers and news from MC. (optional)')}</span></label>
     <p class="xs dim">${T('Età minima per questa auto: ','Minimum age for this car: ')}${c.age} ${T('anni','years')}.</p>`+bkNav()}
  if(n===7){const z=(k,l)=>{const d=BK.docs[k];return `<div class="drop ${d&&d.ok?'ok':''}" id="drop-${k}"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap"><b>${l}</b>${d&&d.ok?`<span class="badge ok">${T('Caricato','Uploaded')}</span>`:''}</div>
      ${d&&d.ok?`<small class="muted">${esc(d.name)} · ${T('controllo demo: file leggibile, dati da verificare da un operatore','demo check: file readable, data to be verified by staff')}</small><button class="link xs" style="justify-self:start" data-act="bkDocDel" data-k="${k}">${T('Sostituisci','Replace')}</button>`
       :d&&d.up?`<small class="muted">${T('Caricamento simulato…','Simulated upload…')}</small><div class="bar"><i id="bar-${k}"></i></div>`
       :`<small class="muted">${T('Foto o PDF, fronte e retro. Il file non lascia il tuo dispositivo.','Photo or PDF, both sides. The file never leaves your device.')}</small><div style="display:flex;gap:8px;flex-wrap:wrap"><label class="btn btn-line btn-sm" style="cursor:pointer">${T('Scegli file','Choose file')}<input type="file" accept="image/*,.pdf" data-chg="bkDoc" data-k="${k}" style="position:absolute;width:1px;height:1px;opacity:0"></label><button class="btn btn-ghost btn-sm" data-act="bkDocDemo" data-k="${k}">${T('Usa un file di esempio','Use a sample file')}</button></div>`}
      ${BK.err[k]?`<span class="err" role="alert">${BK.err[k]}</span>`:''}</div>`};
    return hd(T('Documenti','Documents'),T('Patente e documento d\'identità del guidatore.','Driver\'s licence and ID document.'))+`<div class="grid2">${z('lic',T('Patente di guida','Driving licence'))}${z('idc',T('Documento d\'identità','ID document'))}</div>${prod(T('archivio cifrato, lettura automatica dei dati e verifica di un operatore. L\'AI non sostituisce i controlli d\'identità obbligatori.','encrypted storage, automatic data extraction and staff verification. AI does not replace mandatory identity checks.'))}`+bkNav()}
  if(n===8)return hd(T('Acconto','Down payment'),T('Paghi ora il 30%. Il saldo al ritiro; la cauzione è solo una pre-autorizzazione.','You pay 30% now. The balance at pick-up; the deposit is only a pre-authorisation.'))+
    `<div class="pay-card"><div style="display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap"><span class="muted">${T('Da pagare ora','Due now')}</span><b class="num" style="font-size:44px;line-height:1">${eur(q.now)}</b></div>
      <div class="fake-inp">${T('Campo carta sicuro del fornitore di pagamento','Secure card field from the payment provider')}</div>
      <div class="note warn">${T('Pagamento simulato: qui non si inseriscono dati di carta e non viene addebitato nulla.','Simulated payment: no card details are entered here and nothing is charged.')}</div>
      ${BK.paid?`<div class="note ok" role="status">${T('Acconto simulato riuscito.','Simulated down payment succeeded.')} ${T('Ricevuta demo','Demo receipt')}: <b style="color:var(--text)">${BK.payId}</b></div>`:''}
      ${BK.err.pay?`<div class="note bad" role="alert">${BK.err.pay}</div>`:''}
      ${BK.paid?'':`<div style="display:flex;gap:10px;flex-wrap:wrap"><button class="btn btn-primary" data-act="bkPay" data-ok="1">${T('Simula pagamento riuscito','Simulate successful payment')}</button><button class="btn btn-line" data-act="bkPay" data-ok="0">${T('Simula carta rifiutata','Simulate declined card')}</button></div>`}</div>
     ${prod(T('Stripe Checkout con 3D Secure, webhook idempotenti, pre-autorizzazione della cauzione e rimborsi dal gestionale. Nessun dato di carta salvato da MC.','Stripe Checkout with 3D Secure, idempotent webhooks, deposit pre-authorisation and refunds from the back office. No card data stored by MC.'))}`+bkNav(null,{dis:!BK.paid});
  if(n===9)return hd(T('Firma del contratto','Contract signature'),T('Leggi il riepilogo e firma con il dito o con il mouse.','Read the summary and sign with your finger or mouse.'))+
    `<div class="contract" tabindex="0"><b style="color:var(--text)">${T('Contratto di noleggio — bozza dimostrativa','Rental agreement — demo draft')}</b>
      <h4>1. ${T('Parti e veicolo','Parties and vehicle')}</h4>${T('MC, Via Arbe 95, Milano, concede in noleggio a','MC, Via Arbe 95, Milan, rents to')} ${esc((BK.cust.nome||'')+' '+(BK.cust.cognome||''))} ${T('la vettura','the vehicle')} ${carName(c)}.
      <h4>2. ${T('Periodo','Period')}</h4>${fmtD(fromIso(BK.from))} ${BK.tf} → ${fmtD(fromIso(BK.to))} ${BK.tt} (${q.days} ${T('giorni','days')}). ${T('Ritiro','Pick-up')}: ${T(q.pu.it,q.pu.en)}. ${T('Riconsegna','Return')}: ${T(q.dr.it,q.dr.en)}.
      <h4>3. ${T('Corrispettivo','Price')}</h4>${T('Totale','Total')} ${eur(q.total)}, ${T('acconto','down payment')} ${eur(q.now)}, ${T('saldo al ritiro','balance at pick-up')} ${eur(q.later)}. ${T('Cauzione','Deposit')} ${eur(q.dep)}.
      <h4>4. ${T('Chilometri e uso','Mileage and use')}</h4>${BK.extras.includes('km')?T('Chilometri illimitati.','Unlimited mileage.'):num(c.kmDay*q.days)+' km '+T('inclusi.','included.')} ${T('Vietati gare, traino e sub-noleggio.','No racing, towing or sub-rental.')}
      <h4>5. ${T('Cancellazione','Cancellation')}</h4>${T('Secondo la politica indicata al passo 4.','As per the policy shown at step 4.')}
      <p style="margin-top:12px" class="xs">${T('Testo di esempio senza valore legale: il contratto vero lo fornisce MC con il proprio consulente.','Sample text with no legal value: the real contract is provided by MC with its advisor.')}</p></div>
     <div><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><span class="field"><span>${T('Firma qui','Sign here')}</span></span><button class="link xs" data-act="signClear">${T('Cancella','Clear')}</button></div>
      <canvas class="sign" id="sign" width="800" height="240" aria-label="${T('Area per la firma','Signature area')}"></canvas>${E('sign')}</div>
     <label class="check"><input type="checkbox" id="signOk" data-chg="bkSignOk" ${BK.signOk?'checked':''}><span>${T('Dichiaro di aver letto e accettato il contratto e le condizioni di noleggio.','I declare I have read and accepted the agreement and the rental terms.')}</span></label>${E('signOk')}
     ${prod(T('firma elettronica con valore legale tramite un fornitore certificato, con copia del PDF al cliente.','legally binding e-signature through a certified provider, with a PDF copy sent to the client.'))}`+bkNav(T('Firma e conferma','Sign and confirm'));
  /* 10 */
  if(BK.working)return hd(T('Ultime verifiche','Final checks'))+`<ul class="list-check" id="bkChecks" aria-live="polite">${[T('Disponibilità ricontrollata','Availability re-checked'),T('Documenti presenti','Documents present'),T('Acconto registrato','Down payment recorded'),T('Contratto firmato','Agreement signed')].map((t,i)=>`<li style="opacity:.35;transition:.3s" data-i="${i}"><span>${t}</span></li>`).join('')}</ul><div class="skel" style="height:90px"></div>`;
  if(BK.err.final)return hd(T('Non confermata','Not confirmed'))+`<div class="note bad" role="alert">${BK.err.final}</div><div><button class="btn btn-primary" data-act="bkGo" data-n="2">${T('Scegli altre date','Choose other dates')}</button></div>`;
  const auto=BK.final==='confermata';
  return `<div class="confirm-hero"><div class="tick ${auto?'':'wait'}"><svg viewBox="0 0 24 24">${auto?'<path d="M5 12.500l4.500 4.500L19 7.500"/>':'<path d="M12 6v6l4 2"/>'}</svg></div><p class="eyebrow">${T('Passo','Step')} 10 / 10</p>
    <h2>${auto?T('Prenotazione confermata','Booking confirmed'):T('Richiesta inviata: in attesa di approvazione','Request sent: awaiting approval')}</h2>
    <p class="muted">${auto?T('Tutti i controlli sono andati a buon fine: l\'auto è riservata.','All checks passed: the car is reserved.'):T('Il titolare deve approvare la richiesta. Finché non lo fa la prenotazione non è confermata e le date restano bloccate per te.','The owner must approve the request. Until then the booking is not confirmed and the dates stay on hold for you.')}</p></div>
   <div class="kv"><div><span>${T('Codice','Code')}</span><b>${BK.code}</b></div><div><span>${T('Auto','Car')}</span><b>${carName(c)}</b></div><div><span>${T('Ritiro','Pick-up')}</span><b>${fmtDs(fromIso(BK.from))} · ${BK.tf}</b></div><div><span>${T('Riconsegna','Return')}</span><b>${fmtDs(fromIso(BK.to))} · ${BK.tt}</b></div><div><span>${T('Totale','Total')}</span><b>${eur(q.total)}</b></div><div><span>${T('Acconto (simulato)','Down payment (simulated)')}</span><b>${eur(q.now)}</b></div></div>
   <div class="card"><h3 style="margin-bottom:12px">${T('Cosa succede adesso','What happens next')}</h3><ul class="list-check"><li><span>${T('Porta con te patente e documento originali e la carta di credito per la cauzione.','Bring your original licence and ID and the credit card for the deposit.')}</span></li><li><span>${T('Ritiro','Pick-up')}: <b>${T(q.pu.it,q.pu.en)}</b>.</span></li><li><span>${T('Al ritiro si salda il resto e si controlla insieme l\'auto.','At pick-up you pay the balance and we check the car together.')}</span></li></ul>
    ${prod(T('email di conferma, promemoria automatici di ritiro e riconsegna, messaggio su WhatsApp Business.','confirmation email, automatic pick-up and return reminders, WhatsApp Business message.'))}</div>
   <div style="display:flex;gap:10px;flex-wrap:wrap"><a class="btn btn-primary" href="#/account/prenotazioni">${T('Vedi nell\'area cliente','See in my account')}</a><a class="btn btn-line" href="#/admin">${T('Vedi nella dashboard','See in the dashboard')}</a>${waBtn('btn-line')}</div>`;
}
function bkAvail(){if(!BK.from||!BK.to)return '';const c=carBy(BK.car);if(rentalDays(BK.from,BK.tf,BK.to,BK.tt)<1)return `<div class="note bad" role="alert">${T('La riconsegna deve essere dopo il ritiro.','Return must be after pick-up.')}</div>`;
  if(fromIso(BK.from)<TODAY)return `<div class="note bad" role="alert">${T('La data di ritiro è nel passato.','The pick-up date is in the past.')}</div>`;
  const k=overlaps(c.id,BK.from,BK.to);
  if(fleetState()[c.id]==='maint')return `<div class="note bad" role="alert">${T('Auto in manutenzione: scegline un\'altra.','Car in maintenance: pick another one.')}</div>`;
  if(k)return `<div class="note bad" role="alert">${T('Già prenotata dal','Already booked from')} ${fmtDs(fromIso(k.from))} ${T('al','to')} ${fmtDs(fromIso(k.to))}. ${T('Cambia date o auto: non si accettano doppie prenotazioni.','Change dates or car: double bookings are not accepted.')}</div>`;
  const q=bkQ();return `<div class="note ok">${T('Disponibile','Available')}: ${q.days} ${T(q.days>1?'giorni':'giorno',q.days>1?'days':'day')}, ${T('totale','total')} <b style="color:var(--text)">${eur(q.total)}</b> ${demoTag()}</div>`}
function bkValidate(){const e={},c=carBy(BK.car),n=BK.step;
  if(n===1&&fleetState()[c.id]==='maint')e.car=1;
  if(n===2){if(!BK.from)e.from=T('Scegli la data di ritiro','Pick a pick-up date');if(!BK.to)e.to=T('Scegli la data di riconsegna','Pick a return date');
    if(BK.from&&BK.to&&(rentalDays(BK.from,BK.tf,BK.to,BK.tt)<1||fromIso(BK.from)<TODAY||!isFree(c,BK.from,BK.to)))e.to=T('Date non valide o non disponibili','Dates invalid or unavailable')}
  if(n===6){const u=BK.cust;['nome','cognome','citta'].forEach(k=>{if(!(u[k]||'').trim())e[k]=T('Campo obbligatorio','Required field')});
    if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(u.mail||''))e.mail=T('Email non valida','Invalid email');
    if((u.tel||'').replace(/\D/g,'').length<8)e.tel=T('Numero non valido','Invalid number');
    const b=fromIso(u.nascita);if(!b)e.nascita=T('Inserisci la data di nascita','Enter your date of birth');else{const age=(TODAY-b)/(365.25*DAY);if(age<c.age)e.nascita=T('Per questa auto servono almeno ','This car requires at least ')+c.age+T(' anni',' years')}
    if(!u.privacy)e.privacy=T('Il consenso privacy è necessario per proseguire','Privacy consent is required to continue')}
  if(n===7){['lic','idc'].forEach(k=>{if(!(BK.docs[k]&&BK.docs[k].ok))e[k]=T('Documento mancante','Document missing')})}
  if(n===8&&!BK.paid)e.pay=T('Completa l\'acconto per proseguire','Complete the down payment to continue');
  if(n===9){if(!BK.signed)e.sign=T('Manca la firma','Signature missing');if(!BK.signOk)e.signOk=T('Devi accettare il contratto','You must accept the agreement')}
  BK.err=e;return !Object.keys(e).length}
ACT.bkNext=()=>{if(!bkValidate()){bkPaint();const x=$('#bkStep [aria-invalid=true],#bkStep .err');x&&x.scrollIntoView({block:'center',behavior:'smooth'});return}
  if(BK.step===2&&!(BK.hold>Date.now())){BK.hold=Date.now()+RULES.holdMin*6e4;toast(T('Date bloccate per 15 minuti','Dates held for 15 minutes'))}
  if(BK.step===9){BK.step=10;BK.max=10;bkFinish();return}
  BK.step++;BK.max=Math.max(BK.max,BK.step);bkPaint(true)};
ACT.bkBack=()=>{BK.err={};BK.step=Math.max(1,BK.step-1);bkPaint(true)};
ACT.bkGo=el=>{const n=+el.dataset.n;if(n>BK.max&&!BK.err.final)return;BK.err={};BK.step=n;bkPaint(true)};
ACT.bkExpire=()=>{BK.hold=Date.now()-1;bkPaint()};
ACT.bkRehold=()=>{const c=carBy(BK.car);if(!isFree(c,BK.from,BK.to)){BK.step=2;BK.max=2;BK.hold=0;toast(T('Le date non sono più libere','The dates are no longer free'));bkPaint();return}BK.hold=Date.now()+RULES.holdMin*6e4;toast(T('Blocco rinnovato: 15 minuti','Hold renewed: 15 minutes'));bkPaint()};
CHG.bkCar=el=>{location.hash='#/prenota/'+el.value};
CHG.bkF=el=>{BK[el.name]=el.value;BK.err={};if(el.name==='from'&&BK.to&&BK.to<BK.from){BK.to='';bkPaint();return}BK.hold=0;BK.max=Math.min(BK.max,2);BK.paid=false;$('#bkAvail').innerHTML=bkAvail();bkSum();
  $('#bkStepper')&&$$('.stepper button').forEach((b,i)=>b.disabled=i+1>BK.max)};
CHG.bkPlace=el=>{BK[el.name]=el.value;bkPaint()};
CHG.bkExtra=el=>{const i=BK.extras.indexOf(el.value);el.checked?i<0&&BK.extras.push(el.value):i>=0&&BK.extras.splice(i,1);el.closest('.extra').classList.toggle('on',el.checked);bkSum()};
CHG.bkCust=el=>{BK.cust[el.name]=el.type==='checkbox'?el.checked:el.value};
function docRun(k,name){BK.docs[k]={up:true,name};BK.err={};bkPaint();let p=0;const t=setInterval(()=>{p+=12+Math.random()*18;const b=$('#bar-'+k);if(b)b.style.width=Math.min(100,p)+'%';if(p>=100){clearInterval(t);if(!BK)return;BK.docs[k]={ok:true,name};if(BK.step===7)bkPaint()}},160)}
CHG.bkDoc=el=>{const f=el.files&&el.files[0];if(!f)return;if(f.size>8e6){BK.err[el.dataset.k]=T('File troppo grande (massimo 8 MB)','File too large (8 MB max)');bkPaint();return}docRun(el.dataset.k,f.name)};
ACT.bkDocDemo=el=>docRun(el.dataset.k,el.dataset.k==='lic'?T('patente-esempio.pdf','sample-licence.pdf'):T('documento-esempio.pdf','sample-id.pdf'));
ACT.bkDocDel=el=>{delete BK.docs[el.dataset.k];bkPaint()};
ACT.bkPay=el=>{const ok=el.dataset.ok==='1';BK.err={};el.classList.add('loading');$$('[data-act=bkPay]').forEach(b=>b.disabled=true);
  setTimeout(()=>{if(!BK)return;if(ok){BK.paid=true;BK.payId='DEMO-'+Math.random().toString(36).slice(2,8).toUpperCase();toast(T('Acconto simulato riuscito','Simulated payment succeeded'))}else BK.err.pay=T('Carta rifiutata (simulazione). Nessun addebito: riprova o usa un altro metodo.','Card declined (simulation). Nothing charged: try again or use another method.');if(BK.step===8)bkPaint()},1100)};
CHG.bkSignOk=el=>{BK.signOk=el.checked};
/* firma su canvas */
function signInit(){const cv=$('#sign');if(!cv)return;const x=cv.getContext('2d');x.lineWidth=3;x.lineCap='round';x.lineJoin='round';x.strokeStyle='#111';
  if(BK.signImg){const im=new Image();im.onload=()=>x.drawImage(im,0,0);im.src=BK.signImg}
  let on=false;const pt=e=>{const r=cv.getBoundingClientRect();return [(e.clientX-r.left)*cv.width/r.width,(e.clientY-r.top)*cv.height/r.height]};
  cv.addEventListener('pointerdown',e=>{on=true;try{cv.setPointerCapture(e.pointerId)}catch(_){}const p=pt(e);x.beginPath();x.moveTo(p[0],p[1]);e.preventDefault()});
  cv.addEventListener('pointermove',e=>{if(!on)return;const p=pt(e);x.lineTo(p[0],p[1]);x.stroke();BK.strokes=(BK.strokes||0)+1});
  const end=()=>{if(!on)return;on=false;if((BK.strokes||0)>6){BK.signed=true;try{BK.signImg=cv.toDataURL()}catch(_){}}};
  cv.addEventListener('pointerup',end);cv.addEventListener('pointercancel',end);cv.addEventListener('pointerleave',end)}
ACT.signClear=()=>{const cv=$('#sign');cv.getContext('2d').clearRect(0,0,cv.width,cv.height);BK.signed=false;BK.strokes=0;BK.signImg=null};
/* conferma: nessuna prenotazione è confermata finché tutti i controlli non passano */
function bkFinish(){BK.working=true;bkPaint(true);let i=0;const t=setInterval(()=>{const li=$(`#bkChecks li[data-i="${i}"]`);if(li)li.style.opacity=1;i++;if(i<4)return;clearInterval(t);
  setTimeout(()=>{if(!BK)return;BK.working=false;const c=carBy(BK.car),q=bkQ();
    if(!isFree(c,BK.from,BK.to)){BK.err={final:T('Nel frattempo le date sono state prese da un\'altra prenotazione. L\'acconto simulato verrebbe rimborsato in automatico.','Meanwhile the dates were taken by another booking. The simulated down payment would be refunded automatically.')};BK.max=2;bkPaint();return}
    BK.final=bkMode()==='auto'?'confermata':'attesa';BK.code=newCode();
    const all=myBookings();all.unshift({code:BK.code,car:c.id,from:BK.from,to:BK.to,tf:BK.tf,tt:BK.tt,pu:BK.pu,dr:BK.dr,extras:BK.extras.slice(),total:q.total,now:q.now,dep:q.dep,st:BK.final,who:((BK.cust.nome||'')+' '+(BK.cust.cognome||'')).trim(),mail:BK.cust.mail,payId:BK.payId,made:Date.now()});store.set('bookings',all);
    store.set('profile',Object.assign(store.get('profile',{}),{nome:BK.cust.nome,cognome:BK.cust.cognome,mail:BK.cust.mail,tel:BK.cust.tel,citta:BK.cust.citta}));
    upsertLead({who:((BK.cust.nome||'')+' '+(BK.cust.cognome||'')).trim(),mail:BK.cust.mail,tel:BK.cust.tel,type:'noleggio',car:c.id,note:BK.code});
    clearInterval(holdTick);bkPaint(true)},500)},380)}
