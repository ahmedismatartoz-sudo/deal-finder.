/* Prenotazione appuntamento Sunmotors: un solo riquadro (giorno, ora, motivo, auto, nome).
   Nessun dato va su un server: il tasto blu apre WhatsApp con il messaggio già scritto verso il salone,
   e "Aggiungi al calendario" crea un file .ics sul tuo dispositivo. L'appuntamento vale quando Sunmotors risponde. */
(function () {
  var d = document, box = d.querySelector('[data-book]');
  if (!box) return;
  var $ = function (s) { return box.querySelector(s); };
  var WA = '393284375807', CELL = '328 437 5807';
  var GIORNI = ['domenica', 'lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato'];
  var GBREVI = ['Dom', 'Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab'];
  var MESI = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre'];
  // appuntamenti lun–sab ogni 30 minuti: 9:00–12:00 e 14:30–19:00
  var MATT = ['9:00', '9:30', '10:00', '10:30', '11:00', '11:30', '12:00'];
  var POM = ['14:30', '15:00', '15:30', '16:00', '16:30', '17:00', '17:30', '18:00', '18:30', '19:00'];
  var MAXG = 62, ANTICIPO = 60; // giorni prenotabili; minuti di preavviso per oggi
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* festivi nazionali italiani (con Pasqua e Pasquetta) */
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
  var strip = $('#days'), more = $('#cal-more'), pop = $('#cal-pop'), cal = $('#cal-g'), mese = $('#cal-m'), prev = $('#cal-p'), next = $('#cal-n');
  var slots = $('#slots'), dayLbl = $('#bk-day'), sum = $('#bk-sum');
  var mot = $('#motivo'), auto = $('#auto'), nome = $('#nome'), tua = $('#tua'), perm = $('#permuta-box'), wa = $('#go-wa'), ics = $('#ics'), manca = $('#manca');

  function stesso(a, b) { return a && b && a.getTime() === b.getTime(); }
  function minuti(h) { var p = h.split(':'); return +p[0] * 60 + +p[1]; }
  function passato(dt, h) { if (!stesso(dt, oggi)) return false; var n = new Date(); return minuti(h) < n.getHours() * 60 + n.getMinutes() + ANTICIPO; }
  function liberi(dt) { return MATT.concat(POM).filter(function (h) { return !passato(dt, h); }); }
  function stato(dt) { // '' se prenotabile, altrimenti il motivo
    if (dt < oggi) return 'passato';
    if (dt > limite) return 'oltre';
    if (dt.getDay() === 0) return 'chiuso';
    if (festivo(dt)) return 'festivo';
    if (!liberi(dt).length) return 'pieno';
    return '';
  }
  function aperti() { var o = [], x = new Date(oggi); for (var i = 0; i <= MAXG; i++) { if (!stato(x)) o.push(new Date(x)); x.setDate(x.getDate() + 1); } return o; }
  function dataLunga(dt) { return GIORNI[dt.getDay()] + ' ' + dt.getDate() + ' ' + MESI[dt.getMonth()] + ' ' + dt.getFullYear(); }
  function dataBreve(dt) { return GIORNI[dt.getDay()] + ' ' + dt.getDate() + ' ' + MESI[dt.getMonth()]; }

  /* striscia dei prossimi giorni aperti */
  var GG = aperti();
  function striscia() {
    strip.innerHTML = '';
    GG.forEach(function (dt) {
      var b = d.createElement('button'); b.type = 'button'; b.className = 'day';
      b.innerHTML = '<small>' + (stesso(dt, oggi) ? 'Oggi' : GBREVI[dt.getDay()]) + '</small><b>' + dt.getDate() + '</b><i>' + MESI[dt.getMonth()].slice(0, 3) + '</i>';
      b.setAttribute('aria-label', dataLunga(dt) + (stesso(dt, oggi) ? ', oggi' : ''));
      b.setAttribute('aria-pressed', stesso(dt, st.giorno) ? 'true' : 'false');
      b.addEventListener('click', function () { scegli(dt); });
      strip.appendChild(b);
    });
  }
  function centra() {
    var b = strip.querySelector('[aria-pressed="true"]'); if (!b) return;
    if (b.offsetLeft >= strip.scrollLeft && b.offsetLeft + b.offsetWidth <= strip.scrollLeft + strip.clientWidth) return; // già in vista
    var l = b.offsetLeft - strip.clientWidth / 2 + b.offsetWidth / 2;
    strip.scrollTo({ left: Math.max(0, l), behavior: reduce ? 'auto' : 'smooth' });
  }
  function scegli(dt) {
    st.giorno = dt; if (st.ora && passato(dt, st.ora)) st.ora = null;
    [].forEach.call(strip.children, function (b, i) { b.setAttribute('aria-pressed', stesso(GG[i], dt) ? 'true' : 'false'); });
    centra(); orari(); if (!pop.hidden) disegna(); agg();
  }

  /* mese intero ("Altre date") */
  function disegna() {
    mese.textContent = MESI[vista.getMonth()] + ' ' + vista.getFullYear();
    prev.disabled = vista <= new Date(oggi.getFullYear(), oggi.getMonth(), 1);
    next.disabled = new Date(vista.getFullYear(), vista.getMonth() + 1, 1) > limite;
    [].slice.call(cal.querySelectorAll('button,.vuoto')).forEach(function (b) { b.remove(); });
    var primo = (vista.getDay() + 6) % 7, n = new Date(vista.getFullYear(), vista.getMonth() + 1, 0).getDate();
    for (var i = 0; i < primo; i++) { var e = d.createElement('span'); e.className = 'vuoto'; cal.appendChild(e); }
    for (var g = 1; g <= n; g++) {
      var dt = new Date(vista.getFullYear(), vista.getMonth(), g), b = d.createElement('button'), s = stato(dt), f = festivo(dt);
      b.type = 'button'; b.disabled = !!s; b.textContent = g;
      b.setAttribute('aria-label', GIORNI[dt.getDay()] + ' ' + g + ' ' + MESI[dt.getMonth()] + (f ? ', ' + f + ', chiuso' : s === 'chiuso' ? ', chiuso' : s ? ', non disponibile' : ''));
      if (stesso(dt, oggi)) b.classList.add('today');
      b.setAttribute('aria-pressed', stesso(dt, st.giorno) ? 'true' : 'false');
      (function (dt) { b.addEventListener('click', function () { apri(false); scegli(dt); }); })(dt);
      cal.appendChild(b);
    }
  }
  function apri(o) {
    pop.hidden = !o; more.setAttribute('aria-expanded', o ? 'true' : 'false');
    more.textContent = o ? 'Chiudi' : 'Altre date';
    if (o) { if (st.giorno) vista = new Date(st.giorno.getFullYear(), st.giorno.getMonth(), 1); disegna(); }
  }
  more.addEventListener('click', function () { apri(pop.hidden); });
  prev.addEventListener('click', function () { vista = new Date(vista.getFullYear(), vista.getMonth() - 1, 1); disegna(); });
  next.addEventListener('click', function () { vista = new Date(vista.getFullYear(), vista.getMonth() + 1, 1); disegna(); });

  /* orari liberi del giorno scelto */
  function orari() {
    slots.innerHTML = '';
    if (!st.giorno) { dayLbl.textContent = ''; slots.innerHTML = '<p class="empty">Scegli un giorno: qui compaiono gli orari liberi.</p>'; return; }
    dayLbl.textContent = '· ' + dataBreve(st.giorno);
    liberi(st.giorno).forEach(function (o) {
      var b = d.createElement('button'); b.type = 'button'; b.textContent = o;
      if (o === '14:30') b.className = 'pm';
      b.setAttribute('aria-pressed', st.ora === o ? 'true' : 'false');
      b.setAttribute('aria-label', 'Ore ' + o);
      b.addEventListener('click', function () {
        st.ora = o; [].forEach.call(slots.children, function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); }); agg();
      });
      slots.appendChild(b);
    });
  }

  mot.addEventListener('change', function () { perm.hidden = mot.value !== 'Valutazione permuta'; agg(); });
  [auto, nome, tua].forEach(function (e) { e && e.addEventListener('input', agg); e && e.addEventListener('change', agg); });

  /* dall'indirizzo: ?auto=3, ?motivo=Prova%20su%20strada, ?giorno=2026-11-21&ora=10:30 */
  var q = new URLSearchParams(location.search);
  function setAuto(v) { if (auto.querySelector('option[value="' + v + '"]')) auto.value = v; }
  if (q.get('auto')) setAuto(q.get('auto'));
  if (q.get('motivo') && mot.querySelector('option[value="' + q.get('motivo') + '"]')) mot.value = q.get('motivo');
  perm.hidden = mot.value !== 'Valutazione permuta';
  if (q.get('giorno')) {
    var p = q.get('giorno').split('-'), gd = new Date(+p[0], +p[1] - 1, +p[2]);
    if (!isNaN(gd) && !stato(gd)) { st.giorno = gd; if (q.get('ora') && MATT.concat(POM).indexOf(q.get('ora')) >= 0 && !passato(gd, q.get('ora'))) st.ora = q.get('ora'); }
  }
  if (!st.giorno && GG.length) st.giorno = GG[0]; // il primo giorno aperto è già scelto: basta toccare l'ora
  // in home i bottoni "Prenota visita" delle auto riempiono il modulo invece di cambiare pagina
  [].slice.call(d.querySelectorAll('a[data-auto]')).forEach(function (a) {
    a.addEventListener('click', function (e) { e.preventDefault(); setAuto(a.getAttribute('data-auto')); agg(); d.getElementById('prenota').scrollIntoView({ behavior: reduce ? 'auto' : 'smooth' }); });
  });

  function autoTxt() { var o = auto.options[auto.selectedIndex]; return auto.value ? o.text : ''; }
  function mancanti() {
    var m = [];
    if (!st.giorno) m.push(['il giorno', strip]); else if (!st.ora) m.push(["l'ora", slots]);
    if (nome.value.trim().length < 2) m.push(['il nome', nome]);
    return m;
  }
  function testo() {
    var r = ['Buongiorno Sunmotors, vorrei fissare un appuntamento.', '',
      'Giorno: ' + dataLunga(st.giorno), 'Ora: ' + st.ora, 'Motivo: ' + mot.value];
    if (autoTxt()) r.push('Auto: ' + autoTxt());
    if (mot.value === 'Valutazione permuta' && tua.value.trim()) r.push('Auto da valutare: ' + tua.value.trim());
    r.push('Nome: ' + nome.value.trim(), '', 'Mi confermate la disponibilità? Grazie.');
    return r.join('\n');
  }
  function agg() {
    // riepilogo in una riga sopra il tasto
    var parti = [];
    parti.push(st.giorno ? '<b>' + dataBreve(st.giorno) + '</b>' : '<span>giorno?</span>');
    parti.push(st.ora ? '<b>ore ' + st.ora + '</b>' : '<span>ora?</span>');
    parti.push(mot.value.toLowerCase());
    if (autoTxt()) parti.push(autoTxt().split(' – ')[0]);
    sum.innerHTML = parti.join(' · ');
    var m = mancanti(), ok = !m.length;
    wa.setAttribute('aria-disabled', ok ? 'false' : 'true');
    wa.classList.toggle('ready', ok);
    wa.href = ok ? 'https://wa.me/' + WA + '?text=' + encodeURIComponent(testo()) : 'https://wa.me/' + WA;
    ics.disabled = !(st.giorno && st.ora);
    manca.textContent = ok ? '' : 'Manca ' + m.map(function (x) { return x[0]; }).join(' e ') + '.';
    d.getElementById('f-nome').classList.remove('bad');
  }
  wa.addEventListener('click', function (e) {
    var m = mancanti();
    if (m.length) {
      e.preventDefault();
      if (nome.value.trim().length < 2 && st.giorno && st.ora) d.getElementById('f-nome').classList.add('bad');
      manca.textContent = 'Per confermare manca ' + m.map(function (x) { return x[0]; }).join(' e ') + '.';
      var t = m[0][1]; t.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' }); if (t.focus) setTimeout(function () { t.focus({ preventScroll: true }); }, 400);
      return;
    }
    manca.textContent = 'Si è aperto WhatsApp con il messaggio pronto: invialo a Sunmotors. Se non si apre, scrivi al ' + CELL + '.';
  });

  /* .ics per il calendario: orari in UTC (Z), calcolati dall'ora di Roma (CET/CEST) */
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function ultimaDomenica(y, m) { var x = new Date(Date.UTC(y, m + 1, 0)); x.setUTCDate(x.getUTCDate() - x.getUTCDay()); return x; }
  function romaUTC(dt, h) { // Date UTC dell'ora h (Europe/Rome) del giorno dt
    var y = dt.getFullYear(), mi = minuti(h);
    var t = Date.UTC(y, dt.getMonth(), dt.getDate(), Math.floor(mi / 60), mi % 60);
    var ini = ultimaDomenica(y, 2).getTime() + 3600e3, fin = ultimaDomenica(y, 9).getTime() + 3600e3; // ora legale UE: 01:00 UTC
    var off = (t - 2 * 3600e3 >= ini && t - 2 * 3600e3 < fin) ? 2 : 1;
    return new Date(t - off * 3600e3);
  }
  function utc(x) { return x.getUTCFullYear() + pad(x.getUTCMonth() + 1) + pad(x.getUTCDate()) + 'T' + pad(x.getUTCHours()) + pad(x.getUTCMinutes()) + pad(x.getUTCSeconds()) + 'Z'; }
  function esc(s) { return String(s).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n'); }
  function icsTesto() {
    var ini = romaUTC(st.giorno, st.ora), fine = new Date(ini.getTime() + 30 * 60e3), now = utc(new Date());
    var desc = (mot.value || 'Appuntamento') + (autoTxt() ? ' – ' + autoTxt() : '') + '\nRichiesto su WhatsApp: vale quando Sunmotors conferma. Cell. ' + CELL + '.';
    return ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Sunmotors//Prenotazione dal sito//IT', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'BEGIN:VEVENT',
      'UID:' + now + '-' + Math.random().toString(36).slice(2) + '@sunmotors', 'DTSTAMP:' + now,
      'DTSTART:' + utc(ini), 'DTEND:' + utc(fine),
      'SUMMARY:' + esc('Sunmotors – ' + (mot.value || 'appuntamento') + ' (da confermare)'),
      'LOCATION:' + esc('Sunmotors, Viale Lombardia 65, 20861 Brugherio (MB)'), 'DESCRIPTION:' + esc(desc),
      'BEGIN:VALARM', 'TRIGGER:-PT2H', 'ACTION:DISPLAY', 'DESCRIPTION:' + esc('Appuntamento da Sunmotors'), 'END:VALARM',
      'END:VEVENT', 'END:VCALENDAR'].join('\r\n');
  }
  ics.addEventListener('click', function () {
    if (!st.giorno || !st.ora) return;
    var a = d.createElement('a'), url = URL.createObjectURL(new Blob([icsTesto()], { type: 'text/calendar;charset=utf-8' }));
    a.href = url; a.download = 'appuntamento-sunmotors.ics'; d.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  });

  striscia(); orari(); agg();
  requestAnimationFrame(function () { var b = strip.querySelector('[aria-pressed="true"]'); if (b && b.offsetLeft + b.offsetWidth > strip.clientWidth) strip.scrollLeft = Math.max(0, b.offsetLeft - strip.clientWidth / 2 + b.offsetWidth / 2); });
  // il tasto WhatsApp fisso si nasconde mentre il riquadro è in vista (non copre "Conferma su WhatsApp")
  if ('IntersectionObserver' in window) new IntersectionObserver(function (es) { d.documentElement.classList.toggle('bk-vis', es[0].isIntersecting); }).observe(box);
  window.__book = { testo: function () { return wa.href; }, ics: icsTesto };
})();
