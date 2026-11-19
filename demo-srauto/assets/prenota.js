/* Prenotazione appuntamento Sunmotors.
   Nessun dato va su un server: il tasto verde apre WhatsApp con il messaggio già scritto verso il salone,
   e "Aggiungi al calendario" crea un file .ics sul tuo dispositivo. L'appuntamento vale quando Sunmotors risponde. */
(function () {
  var d = document, box = d.querySelector('[data-book]');
  if (!box) return;
  var $ = function (s) { return box.querySelector(s); };
  var WA = '393284375807', CELL = '328 437 5807';
  var GIORNI = ['domenica', 'lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato'];
  var MESI = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre'];
  // orari reali: lun–sab 9:00–12:30 e 14:30–19:30, appuntamenti ogni 30 minuti
  var MATT = ['9:00', '9:30', '10:00', '10:30', '11:00', '11:30', '12:00'];
  var POM = ['14:30', '15:00', '15:30', '16:00', '16:30', '17:00', '17:30', '18:00', '18:30', '19:00'];
  var MAXG = 62, ANTICIPO = 60; // giorni prenotabili; minuti di preavviso per oggi

  /* festivi nazionali italiani (con Pasquetta) */
  function pasqua(y) {
    var a = y % 19, b = Math.floor(y / 100), c = y % 100, dd = Math.floor(b / 4), e = b % 4, f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3),
      h = (19 * a + b - dd - g + 15) % 30, i = Math.floor(c / 4), k = c % 4, l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451),
      mo = Math.floor((h + l - 7 * m + 114) / 31), da = ((h + l - 7 * m + 114) % 31) + 1;
    return new Date(y, mo - 1, da);
  }
  var cacheF = {};
  function festivi(y) {
    if (cacheF[y]) return cacheF[y];
    var f = {}, add = function (m, g, n) { f[m + '-' + g] = n; };
    add(1, 1, 'Capodanno'); add(1, 6, 'Epifania'); add(4, 25, 'Liberazione'); add(5, 1, 'Festa del lavoro'); add(6, 2, 'Festa della Repubblica');
    add(8, 15, 'Ferragosto'); add(11, 1, 'Ognissanti'); add(12, 8, 'Immacolata'); add(12, 25, 'Natale'); add(12, 26, 'Santo Stefano');
    var p = pasqua(y); add(p.getMonth() + 1, p.getDate(), 'Pasqua');
    var pm = new Date(p); pm.setDate(pm.getDate() + 1); add(pm.getMonth() + 1, pm.getDate(), 'Pasquetta');
    return (cacheF[y] = f);
  }
  function festivo(dt) { return festivi(dt.getFullYear())[(dt.getMonth() + 1) + '-' + dt.getDate()] || ''; }

  var oggi = new Date(); oggi.setHours(0, 0, 0, 0);
  var limite = new Date(oggi); limite.setDate(limite.getDate() + MAXG);
  var st = { giorno: null, ora: null };
  var vista = new Date(oggi.getFullYear(), oggi.getMonth(), 1);
  var cal = $('#cal-g'), mese = $('#cal-m'), prev = $('#cal-p'), next = $('#cal-n'), slots = $('#slots'), dayLbl = $('#bk-day');
  var auto = $('#auto'), nome = $('#nome'), tua = $('#tua'), perm = $('#permuta-box'), wa = $('#go-wa'), ics = $('#ics'), manca = $('#manca');
  var motivi = [].slice.call(box.querySelectorAll('input[name=motivo]'));

  function stesso(a, b) { return a && b && a.getTime() === b.getTime(); }
  function minuti(h) { var p = h.split(':'); return +p[0] * 60 + +p[1]; }
  function passato(dt, h) { if (!stesso(dt, oggi)) return false; var n = new Date(); return minuti(h) < n.getHours() * 60 + n.getMinutes() + ANTICIPO; }
  function liberi(dt) { return MATT.concat(POM).filter(function (h) { return !passato(dt, h); }).length; }
  function stato(dt) { // '' se prenotabile, altrimenti il motivo
    if (dt < oggi) return 'passato';
    if (dt > limite) return 'oltre';
    if (dt.getDay() === 0) return 'chiuso';
    var f = festivo(dt); if (f) return 'festivo';
    if (!liberi(dt)) return 'pieno';
    return '';
  }
  function primoLibero() { var x = new Date(oggi); for (var i = 0; i <= MAXG; i++) { if (!stato(x)) return x; x.setDate(x.getDate() + 1); } return null; }

  function disegna() {
    mese.textContent = MESI[vista.getMonth()] + ' ' + vista.getFullYear();
    prev.disabled = vista <= new Date(oggi.getFullYear(), oggi.getMonth(), 1);
    next.disabled = new Date(vista.getFullYear(), vista.getMonth() + 1, 1) > limite;
    [].slice.call(cal.querySelectorAll('button,.vuoto')).forEach(function (b) { b.remove(); });
    var primo = (vista.getDay() + 6) % 7, n = new Date(vista.getFullYear(), vista.getMonth() + 1, 0).getDate();
    for (var i = 0; i < primo; i++) { var e = d.createElement('span'); e.className = 'vuoto'; cal.appendChild(e); }
    for (var g = 1; g <= n; g++) {
      var dt = new Date(vista.getFullYear(), vista.getMonth(), g), b = d.createElement('button'), s = stato(dt), f = festivo(dt);
      b.type = 'button'; b.disabled = !!s;
      b.innerHTML = g + (stesso(dt, oggi) ? '<small>oggi</small>' : f ? '<small>festivo</small>' : dt.getDay() === 0 && dt >= oggi ? '<small>chiuso</small>' : '');
      b.setAttribute('aria-label', GIORNI[dt.getDay()] + ' ' + g + ' ' + MESI[dt.getMonth()] + (f ? ', ' + f + ', chiuso' : s === 'chiuso' ? ', chiuso' : s ? ', non disponibile' : ''));
      if (stesso(dt, oggi)) b.classList.add('today');
      b.setAttribute('aria-pressed', stesso(dt, st.giorno) ? 'true' : 'false');
      (function (dt) { b.addEventListener('click', function () { st.giorno = dt; st.ora = null; disegna(); orari(); agg(); vai(slots); }); })(dt);
      cal.appendChild(b);
    }
  }
  function vai(el) { // sul telefono porta avanti al passo successivo, se è fuori vista
    if (innerWidth > 1080) return;
    var r = el.getBoundingClientRect();
    if (r.top > innerHeight * .7) el.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
  }
  function dataLunga(dt) { return GIORNI[dt.getDay()] + ' ' + dt.getDate() + ' ' + MESI[dt.getMonth()] + ' ' + dt.getFullYear(); }
  function orari() {
    slots.innerHTML = '';
    if (!st.giorno) { dayLbl.textContent = 'Prima scegli un giorno'; slots.innerHTML = '<p class="empty">Scegli un giorno nel calendario: qui compaiono gli orari liberi, ogni 30 minuti.</p>'; return; }
    dayLbl.textContent = dataLunga(st.giorno);
    [['Mattina · 9:00–12:30', MATT], ['Pomeriggio · 14:30–19:30', POM]].forEach(function (gr) {
      var h = d.createElement('h4'); h.textContent = gr[0]; slots.appendChild(h);
      gr[1].forEach(function (o) {
        var b = d.createElement('button'); b.type = 'button'; b.textContent = o; b.disabled = passato(st.giorno, o);
        b.setAttribute('aria-pressed', st.ora === o ? 'true' : 'false');
        b.setAttribute('aria-label', 'Ore ' + o + (b.disabled ? ', non disponibile' : ''));
        b.addEventListener('click', function () { st.ora = o; orari(); agg(); vai(motivi[0].closest('.bk-card')); });
        slots.appendChild(b);
      });
    });
  }
  prev.addEventListener('click', function () { vista = new Date(vista.getFullYear(), vista.getMonth() - 1, 1); disegna(); });
  next.addEventListener('click', function () { vista = new Date(vista.getFullYear(), vista.getMonth() + 1, 1); disegna(); });

  function motivo() { var m = motivi.filter(function (r) { return r.checked; })[0]; return m ? m.value : ''; }
  motivi.forEach(function (r) { r.addEventListener('change', function () { perm.hidden = motivo() !== 'Valutazione permuta'; agg(); }); });
  [auto, nome, tua].forEach(function (e) { e && e.addEventListener('input', agg); e && e.addEventListener('change', agg); });

  /* dall'indirizzo: ?auto=3, ?motivo=Prova%20su%20strada, ?giorno=2026-11-21&ora=10:30 */
  var q = new URLSearchParams(location.search);
  function setAuto(v) { if (auto.querySelector('option[value="' + v + '"]')) auto.value = v; }
  if (q.get('auto')) setAuto(q.get('auto'));
  if (q.get('motivo')) motivi.forEach(function (r) { if (r.value === q.get('motivo')) r.checked = true; });
  perm.hidden = motivo() !== 'Valutazione permuta';
  if (q.get('giorno')) {
    var p = q.get('giorno').split('-'), gd = new Date(+p[0], +p[1] - 1, +p[2]);
    if (!isNaN(gd) && !stato(gd)) { st.giorno = gd; vista = new Date(gd.getFullYear(), gd.getMonth(), 1); if (q.get('ora') && MATT.concat(POM).indexOf(q.get('ora')) >= 0 && !passato(gd, q.get('ora'))) st.ora = q.get('ora'); }
  }
  // in home i bottoni "Prenota visita" delle auto riempiono il modulo qui sotto invece di cambiare pagina
  [].slice.call(d.querySelectorAll('a[data-auto]')).forEach(function (a) {
    a.addEventListener('click', function (e) { e.preventDefault(); setAuto(a.getAttribute('data-auto')); agg(); d.getElementById('prenota').scrollIntoView({ behavior: 'smooth' }); });
  });

  function autoTxt() { var o = auto.options[auto.selectedIndex]; return auto.value ? o.text : ''; }
  function riga(id, v) { var e = $('#s-' + id); e.textContent = v || '—'; e.classList.toggle('empty', !v); }
  function mancanti() {
    var m = [];
    if (!st.giorno) m.push(['il giorno', cal]); else if (!st.ora) m.push(["l'ora", slots]);
    if (!motivo()) m.push(['il motivo', motivi[0]]);
    if (nome.value.trim().length < 2) m.push(['il tuo nome', nome]);
    return m;
  }
  function testo() {
    var r = ['Buongiorno Sunmotors, vorrei fissare un appuntamento.', '',
      'Giorno: ' + dataLunga(st.giorno), 'Ora: ' + st.ora, 'Motivo: ' + motivo()];
    if (autoTxt()) r.push('Auto: ' + autoTxt());
    if (motivo() === 'Valutazione permuta' && tua.value.trim()) r.push('Auto da valutare: ' + tua.value.trim());
    r.push('Nome: ' + nome.value.trim(), '', 'Mi confermate la disponibilità? Grazie.');
    return r.join('\n');
  }
  function agg() {
    riga('giorno', st.giorno ? dataLunga(st.giorno) : ''); riga('ora', st.ora); riga('motivo', motivo());
    riga('auto', autoTxt()); riga('nome', nome.value.trim());
    var m = mancanti(), ok = !m.length;
    wa.setAttribute('aria-disabled', ok ? 'false' : 'true');
    wa.classList.toggle('ready', ok);
    wa.href = ok ? 'https://wa.me/' + WA + '?text=' + encodeURIComponent(testo()) : 'https://wa.me/' + WA;
    ics.disabled = !(st.giorno && st.ora);
    manca.textContent = ok ? 'Tutto pronto: premi il tasto verde e invia il messaggio su WhatsApp.' : 'Manca ' + m.map(function (x) { return x[0]; }).join(', ') + '.';
    manca.style.color = ok ? '#9fe3b6' : '';
    d.getElementById('f-nome').classList.remove('bad');
  }
  wa.addEventListener('click', function (e) {
    var m = mancanti();
    if (m.length) {
      e.preventDefault();
      if (nome.value.trim().length < 2 && st.giorno && st.ora) d.getElementById('f-nome').classList.add('bad');
      manca.textContent = 'Per confermare manca ' + m.map(function (x) { return x[0]; }).join(', ') + '.';
      var t = m[0][1]; t.scrollIntoView({ behavior: 'smooth', block: 'center' }); if (t.focus) setTimeout(function () { t.focus({ preventScroll: true }); }, 400);
      return;
    }
    var n = box.querySelector('.ok-note');
    if (!n) { n = d.createElement('p'); n.className = 'ok-note'; n.setAttribute('role', 'status'); manca.after(n); }
    n.textContent = 'Si è aperto WhatsApp con il messaggio pronto: invialo a Sunmotors. Se non si apre, scrivi al ' + CELL + '.';
  });

  /* .ics per il calendario del telefono o del computer */
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function stamp(dt, h) { var m = minuti(h); return dt.getFullYear() + pad(dt.getMonth() + 1) + pad(dt.getDate()) + 'T' + pad(Math.floor(m / 60)) + pad(m % 60) + '00'; }
  function esc(s) { return String(s).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n'); }
  ics.addEventListener('click', function () {
    if (!st.giorno || !st.ora) return;
    var m = minuti(st.ora) + 30, fine = pad(Math.floor(m / 60)) + ':' + pad(m % 60), now = new Date();
    var utc = now.getUTCFullYear() + pad(now.getUTCMonth() + 1) + pad(now.getUTCDate()) + 'T' + pad(now.getUTCHours()) + pad(now.getUTCMinutes()) + pad(now.getUTCSeconds()) + 'Z';
    var desc = (motivo() || 'Appuntamento') + (autoTxt() ? ' – ' + autoTxt() : '') + '\nRichiesto su WhatsApp: vale quando Sunmotors conferma. Cell. ' + CELL + '.';
    var c = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Sunmotors//Prenotazione dal sito//IT', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'BEGIN:VEVENT',
      'UID:' + utc + '-' + Math.random().toString(36).slice(2) + '@sunmotors', 'DTSTAMP:' + utc,
      'DTSTART:' + stamp(st.giorno, st.ora), 'DTEND:' + stamp(st.giorno, fine),
      'SUMMARY:' + esc('Sunmotors – ' + (motivo() || 'appuntamento') + ' (da confermare)'),
      'LOCATION:' + esc('Sunmotors, Viale Lombardia 65, 20861 Brugherio (MB)'), 'DESCRIPTION:' + esc(desc),
      'BEGIN:VALARM', 'TRIGGER:-PT2H', 'ACTION:DISPLAY', 'DESCRIPTION:' + esc('Appuntamento da Sunmotors'), 'END:VALARM',
      'END:VEVENT', 'END:VCALENDAR'].join('\r\n');
    var a = d.createElement('a'), url = URL.createObjectURL(new Blob([c], { type: 'text/calendar;charset=utf-8' }));
    a.href = url; a.download = 'appuntamento-sunmotors.ics'; d.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  });

  // mostra subito il mese del primo giorno libero
  if (!st.giorno) { var pl = primoLibero(); if (pl) vista = new Date(pl.getFullYear(), pl.getMonth(), 1); }
  disegna(); orari(); agg();
  window.__book = { testo: function () { return wa.href; } };
})();
