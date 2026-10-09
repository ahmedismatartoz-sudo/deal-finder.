/* Scovo: service worker. Apre il sito anche con poco segnale.
   - pagine e file del sito: prima la rete, se manca la copia salvata
   - foto delle auto: prima la copia salvata (non cambiano mai)
   - elenco affari: prima la rete, se manca l'ultimo elenco scaricato
   - tutto il resto delle API: solo rete (servizi, contatti, accesso) */
const VER = "scovo-v1";
const SHELL = ["/", "/static/scovo/app.css", "/static/scovo/app.js", "/static/scovo/favicon.svg", "/static/scovo/icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VER).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VER && k !== "scovo-foto" && k !== "scovo-dati").map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function networkFirst(req, cacheName, key) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(key || req, res.clone());
    return res;
  } catch (e) {
    const hit = await cache.match(key || req, {ignoreSearch: !key});
    if (hit) return hit;
    throw e;
  }
}
async function cacheFirst(req) {
  const cache = await caches.open("scovo-foto");
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) {
    cache.put(req, res.clone());
    cache.keys().then(keys => { if (keys.length > 400) keys.slice(0, keys.length - 400).forEach(k => cache.delete(k)); });
  }
  return res;
}

self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/foto/")) { e.respondWith(cacheFirst(req)); return; }
  if (url.pathname === "/api/affari") { e.respondWith(networkFirst(req, "scovo-dati", "/api/affari")); return; }
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/gestione")) return;
  if (req.mode === "navigate") { e.respondWith(networkFirst(req, VER, url.pathname === "/" ? "/" : undefined)); return; }
  if (url.pathname.startsWith("/static/")) { e.respondWith(networkFirst(req, VER)); }
});
