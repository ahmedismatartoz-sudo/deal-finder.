/* Prenotazione appuntamento: nessun dato va su un server. Alla conferma si apre WhatsApp (o l'email)
   con il messaggio già scritto verso Sunmotors; l'appuntamento è confermato solo dalla risposta del salone. */
(function(){
  var d=document,$=function(s){return d.querySelector(s)},WA='393284375807',MAIL='sunmotors@libero.it';
  var GIORNI=['domenica','lunedì','martedì','mercoledì','giovedì','venerdì','sabato'];
  var MESI=['gennaio','febbraio','marzo','aprile','maggio','giugno','luglio','agosto','settembre','ottobre','novembre','dicembre'];
  var MATT=['9:00','9:30','10:00','10:30','11:00','11:30','12:00'],POM=['14:30','15:00','15:30','16:00','16:30','17:00','17:30','18:00','18:30','19:00'];
  var MAXG=45;
  var st={motivo:null,giorno:null,ora:null},oggi=new Date();oggi.setHours(0,0,0,0);
  var limite=new Date(oggi);limite.setDate(limite.getDate()+MAXG);
  var vista=new Date(oggi.getFullYear(),oggi.getMonth(),1);
  var cal=$('#cal-g'),mese=$('#cal-m'),prev=$('#cal-p'),next=$('#cal-n'),slots=$('#slots');

  function aperto(g){return g.getDay()!==0} // dal lunedì al sabato
  function stesso(a,b){return a&&b&&a.getTime()===b.getTime()}
  function disegna(){
    mese.textContent=MESI[vista.getMonth()]+' '+vista.getFullYear();
    prev.disabled=vista<=new Date(oggi.getFullYear(),oggi.getMonth(),1);
    next.disabled=new Date(vista.getFullYear(),vista.getMonth()+1,1)>limite;
    [].slice.call(cal.querySelectorAll('button,.vuoto')).forEach(function(b){b.remove()});
    var primo=(vista.getDay()+6)%7,n=new Date(vista.getFullYear(),vista.getMonth()+1,0).getDate();
    for(var i=0;i<primo;i++){var e=d.createElement('span');e.className='vuoto';cal.appendChild(e)}
    for(var g=1;g<=n;g++){
      var dt=new Date(vista.getFullYear(),vista.getMonth(),g),b=d.createElement('button');
      b.type='button';b.textContent=g;
      var ok=dt>=oggi&&dt<=limite&&aperto(dt)&&slotLiberi(dt)>0;
      b.disabled=!ok;
      b.setAttribute('aria-label',GIORNI[dt.getDay()]+' '+g+' '+MESI[dt.getMonth()]+(dt.getDay()===0?', chiuso':ok?'':', non disponibile'));
      if(stesso(dt,oggi))b.classList.add('today');
      b.setAttribute('aria-pressed',stesso(dt,st.giorno)?'true':'false');
      (function(dt){b.addEventListener('click',function(){st.giorno=dt;st.ora=null;disegna();orari();agg()})})(dt);
      cal.appendChild(b);
    }
  }
  function minuti(h){var p=h.split(':');return +p[0]*60+ +p[1]}
  function passato(dt,h){if(!stesso(dt,oggi))return false;var n=new Date();return minuti(h)<n.getHours()*60+n.getMinutes()+60}
  function slotLiberi(dt){return MATT.concat(POM).filter(function(h){return !passato(dt,h)}).length}
  function orari(){
    slots.innerHTML='';
    if(!st.giorno){slots.innerHTML='<p class="small" style="grid-column:1/-1">Scegli prima un giorno nel calendario.</p>';return}
    [['Mattina',MATT],['Pomeriggio',POM]].forEach(function(gr){
      var h3=d.createElement('h3');h3.textContent=gr[0];slots.appendChild(h3);
      gr[1].forEach(function(h){var b=d.createElement('button');b.type='button';b.textContent=h;b.disabled=passato(st.giorno,h);
        b.setAttribute('aria-pressed',st.ora===h?'true':'false');
        b.addEventListener('click',function(){st.ora=h;orari();agg()});slots.appendChild(b)});
    });
  }
  prev.addEventListener('click',function(){vista=new Date(vista.getFullYear(),vista.getMonth()-1,1);disegna()});
  next.addEventListener('click',function(){vista=new Date(vista.getFullYear(),vista.getMonth()+1,1);disegna()});

  /* motivo */
  [].slice.call(d.querySelectorAll('input[name=motivo]')).forEach(function(r){r.addEventListener('change',function(){st.motivo=r.value;$('#permuta-box').hidden=r.value!=='Valutazione permuta';agg()})});

  /* auto dall'indirizzo (?auto=3) */
  var auto=$('#auto'),q=new URLSearchParams(location.search);
  if(q.get('auto')){var o=auto.querySelector('option[value="'+q.get('auto')+'"]');if(o){auto.value=q.get('auto')}}
  if(q.get('motivo')){var rr=d.querySelector('input[name=motivo][value="'+q.get('motivo')+'"]');if(rr){rr.checked=true;st.motivo=rr.value;$('#permuta-box').hidden=rr.value!=='Valutazione permuta'}}

  var nome=$('#nome'),tel=$('#tel'),note=$('#note'),tua=$('#tua');
  [auto,nome,tel,note,tua].forEach(function(e){e&&e.addEventListener('input',agg)});

  function dataLunga(dt){return GIORNI[dt.getDay()]+' '+dt.getDate()+' '+MESI[dt.getMonth()]+' '+dt.getFullYear()}
  function autoTxt(){var o=auto.options[auto.selectedIndex];return o?o.text:''}
  function telOk(){return tel.value.replace(/\D/g,'').length>=8}
  function completo(){return st.motivo&&st.giorno&&st.ora&&nome.value.trim().length>1&&telOk()}
  function riga(id,v){var e=$('#s-'+id);e.textContent=v||'—';e.classList.toggle('empty',!v)}
  function agg(){
    riga('motivo',st.motivo);riga('giorno',st.giorno?dataLunga(st.giorno):'');riga('ora',st.ora);
    riga('auto',auto.value?autoTxt():'');riga('nome',nome.value.trim());riga('tel',tel.value.trim());
    var ok=completo();[$('#go-wa'),$('#go-mail')].forEach(function(b){b.setAttribute('aria-disabled',ok?'false':'true')});
    $('#manca').textContent=ok?'Tutto pronto: scegli come inviare la richiesta.':'Mancano: '+[!st.motivo&&'motivo',!st.giorno&&'giorno',!st.ora&&'orario',nome.value.trim().length<2&&'nome',!telOk()&&'telefono'].filter(Boolean).join(', ')+'.';
    if(ok){var m=messaggio();$('#go-wa').href='https://wa.me/'+WA+'?text='+encodeURIComponent(m);
      $('#go-mail').href='mailto:'+MAIL+'?subject='+encodeURIComponent('Richiesta appuntamento: '+st.motivo+' '+dataLunga(st.giorno)+' ore '+st.ora)+'&body='+encodeURIComponent(m)}
    else{$('#go-wa').removeAttribute('href');$('#go-mail').removeAttribute('href')}
  }
  function messaggio(){
    var l=['Buongiorno Sunmotors, vorrei prenotare un appuntamento.','','Motivo: '+st.motivo,'Giorno: '+dataLunga(st.giorno)+', ore '+st.ora];
    if(auto.value)l.push('Auto: '+autoTxt());
    if(st.motivo==='Valutazione permuta'&&tua.value.trim())l.push('La mia auto da valutare: '+tua.value.trim());
    l.push('Nome: '+nome.value.trim(),'Telefono: '+tel.value.trim());
    if(note.value.trim())l.push('Note: '+note.value.trim());
    l.push('','(Richiesta dal sito: attendo conferma)');return l.join('\n');
  }
  function segna(){
    [['#f-nome',nome.value.trim().length>1],['#f-tel',telOk()]].forEach(function(x){$(x[0]).classList.toggle('bad',!x[1])});
  }
  [$('#go-wa'),$('#go-mail')].forEach(function(b){b.addEventListener('click',function(e){
    if(!completo()){e.preventDefault();segna();agg();$('#manca').focus&&$('#manca').focus();return}
    $('#done').classList.add('on');ics();
  })});
  nome.addEventListener('blur',function(){if(nome.value)segna()});tel.addEventListener('blur',function(){if(tel.value)segna()});

  /* promemoria per il proprio calendario (.ics), generato nel browser */
  function ics(){
    var p=st.ora.split(':'),a=new Date(st.giorno);a.setHours(+p[0],+p[1],0,0);var b=new Date(a.getTime()+45*60000);
    var f=function(x){return x.getFullYear()+String(x.getMonth()+1).padStart(2,'0')+String(x.getDate()).padStart(2,'0')+'T'+String(x.getHours()).padStart(2,'0')+String(x.getMinutes()).padStart(2,'0')+'00'};
    var t=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Sunmotors//Prenotazione//IT','BEGIN:VEVENT','UID:'+Date.now()+'@sunmotors','DTSTAMP:'+f(new Date()),'DTSTART:'+f(a),'DTEND:'+f(b),
      'SUMMARY:Sunmotors – '+st.motivo+' (da confermare)','LOCATION:Viale Lombardia 65\\, 20861 Brugherio (MB)','DESCRIPTION:Richiesta inviata a Sunmotors. Tel. 328 437 5807','END:VEVENT','END:VCALENDAR'].join('\r\n');
    var l=$('#ics');l.href='data:text/calendar;charset=utf-8,'+encodeURIComponent(t);l.hidden=false;
  }
  disegna();orari();agg();
})();
