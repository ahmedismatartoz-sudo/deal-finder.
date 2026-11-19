(function(){
  var d=document,r=d.documentElement;
  /* menu mobile */
  var m=d.getElementById('menu'),bs=[].slice.call(d.querySelectorAll('[data-menu]'));
  function set(o){if(!m)return;bs.forEach(function(b){b.setAttribute('aria-expanded',o?'true':'false')});
    if(o){m.removeAttribute('hidden');var f=m.querySelector('a,button');f&&f.focus()}else m.setAttribute('hidden','');d.body.style.overflow=o?'hidden':''}
  bs.forEach(function(b){b.addEventListener('click',function(){set(b.getAttribute('data-menu')==='open')})});
  addEventListener('keydown',function(e){if(e.key==='Escape'&&m&&!m.hidden)set(false)});
  /* header in home */
  if(d.body.classList.contains('home')){var f=function(){d.body.classList.toggle('scrolled',scrollY>40)};addEventListener('scroll',f,{passive:true});f()}
  /* comparse */
  var els=[].slice.call(d.querySelectorAll('.rv'));
  if('IntersectionObserver' in window){var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target)}})},{rootMargin:'0px 0px -8% 0px'});els.forEach(function(e){io.observe(e)})}
  else els.forEach(function(e){e.classList.add('in')});
  /* orari: evidenzia oggi */
  var day=(new Date().getDay()+6)%7;
  [].slice.call(d.querySelectorAll('.hours')).forEach(function(h){var rows=h.querySelectorAll('div');if(rows[day])rows[day].classList.add('today')});
  /* mappa caricata solo su richiesta (niente cookie di terzi finché non serve) */
  [].slice.call(d.querySelectorAll('[data-map]')).forEach(function(b){b.addEventListener('click',function(){
    var box=b.closest('.mapbox'),i=d.createElement('iframe');i.src=b.getAttribute('data-map');i.title='Mappa: Sunmotors, Viale Lombardia 65, Brugherio';i.loading='lazy';i.referrerPolicy='no-referrer-when-downgrade';box.appendChild(i)})});

  /* slider (auto in evidenza, recensioni): frecce, barra di avanzamento, scorrimento automatico gentile */
  var reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
  [].slice.call(d.querySelectorAll('[data-slider]')).forEach(function(sl){
    var tr=sl.querySelector('.track'),pv=sl.querySelector('[data-prev]'),nx=sl.querySelector('[data-next]'),bar=sl.querySelector('.sl-bar i');
    function step(){var c=tr.children[0];return c?c.getBoundingClientRect().width+16:300}
    function upd(){var max=tr.scrollWidth-tr.clientWidth,p=max>0?tr.scrollLeft/max:0,w=tr.scrollWidth?tr.clientWidth/tr.scrollWidth:1;
      if(bar){bar.style.width=Math.max(8,w*100)+'%';bar.style.transform='translateX('+(p*(1/Math.max(w,.01)-1)*100)+'%)'}
      pv.disabled=tr.scrollLeft<4;nx.disabled=tr.scrollLeft>max-4}
    pv.addEventListener('click',function(){tr.scrollBy({left:-step(),behavior:reduce?'auto':'smooth'})});
    nx.addEventListener('click',function(){tr.scrollBy({left:step(),behavior:reduce?'auto':'smooth'})});
    tr.addEventListener('scroll',upd,{passive:true});addEventListener('resize',upd);upd();
    var ms=+sl.getAttribute('data-auto');
    if(ms&&!reduce){var hold=false,vis=false;
      ['pointerenter','focusin','touchstart'].forEach(function(e){sl.addEventListener(e,function(){hold=true},{passive:true})});
      ['pointerleave','focusout'].forEach(function(e){sl.addEventListener(e,function(){hold=false})});
      if('IntersectionObserver' in window)new IntersectionObserver(function(es){vis=es[0].isIntersecting}).observe(sl);
      setInterval(function(){if(hold||!vis||d.hidden)return;var max=tr.scrollWidth-tr.clientWidth;tr.scrollTo({left:tr.scrollLeft>=max-4?0:tr.scrollLeft+step(),behavior:'smooth'})},ms)}
  });
  /* numeri che contano quando entrano in vista */
  var cs=[].slice.call(d.querySelectorAll('[data-count]'));
  function fmt(v,dec){return dec?(v/10).toFixed(1).replace('.',','):String(Math.round(v))}
  function run(e){var to=+e.getAttribute('data-count'),dec=+e.getAttribute('data-dec'),t0=performance.now(),D=1400;
    (function f(t){var k=Math.min(1,(t-t0)/D),v=to*(1-Math.pow(1-k,3));e.textContent=fmt(v,dec);if(k<1)requestAnimationFrame(f)})(t0)}
  if(cs.length&&!reduce&&'IntersectionObserver' in window){var io2=new IntersectionObserver(function(es){es.forEach(function(x){if(x.isIntersecting){run(x.target);io2.unobserve(x.target)}})},{threshold:.6});cs.forEach(function(e){io2.observe(e)})}
  /* filtri auto */
  var grid=d.getElementById('cars');
  if(grid&&grid.hasAttribute('data-filter')){
    var cards=[].slice.call(grid.children),st={f:'tutte',sort:'rilevanza'},cnt=d.getElementById('count');
    var chips=[].slice.call(d.querySelectorAll('[data-f]')),sel=d.getElementById('sort');
    function apply(){var n=0;cards.forEach(function(c){var ok=st.f==='tutte'||c.getAttribute('data-tipo')===st.f||c.getAttribute('data-alim')===st.f||c.getAttribute('data-cambio')===st.f||(st.f==='sotto10'&&+c.getAttribute('data-prezzo')<10000);c.hidden=!ok;if(ok)n++});
      var s=cards.slice();if(st.sort==='prezzo-asc')s.sort(function(a,b){return a.dataset.prezzo-b.dataset.prezzo});else if(st.sort==='prezzo-desc')s.sort(function(a,b){return b.dataset.prezzo-a.dataset.prezzo});else if(st.sort==='km')s.sort(function(a,b){return a.dataset.km-b.dataset.km});else if(st.sort==='anno')s.sort(function(a,b){return b.dataset.anno-a.dataset.anno});else s.sort(function(a,b){return a.dataset.i-b.dataset.i});
      s.forEach(function(c){grid.appendChild(c)});if(cnt)cnt.textContent=n+(n===1?' auto':' auto')+' mostrate'}
    chips.forEach(function(c){c.addEventListener('click',function(){st.f=c.getAttribute('data-f');chips.forEach(function(x){x.setAttribute('aria-pressed',x===c?'true':'false')});apply()})});
    sel&&sel.addEventListener('change',function(){st.sort=sel.value;apply()});apply();
  }
})();
