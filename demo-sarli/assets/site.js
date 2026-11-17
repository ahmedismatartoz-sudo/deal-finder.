(function(){
  var bs=[].slice.call(document.querySelectorAll('.menubtn')),m=document.getElementById('menu');
  if(!bs.length||!m)return;
  var open=false;
  function set(o){open=o;bs.forEach(function(b){if(!b.classList.contains('mclose'))b.setAttribute('aria-expanded',o?'true':'false');});
    if(o)m.removeAttribute('hidden');else m.setAttribute('hidden','');document.body.classList.toggle('menu-open',o);}
  bs.forEach(function(b){b.addEventListener('click',function(){set(b.classList.contains('mclose')?false:!open);});});
  addEventListener('keydown',function(e){if(e.key==='Escape'&&open)set(false);});
  m.addEventListener('click',function(e){if(e.target.closest('a'))set(false);});
})();
