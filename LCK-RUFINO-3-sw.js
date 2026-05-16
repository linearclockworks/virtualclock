// Cache version: 1778682677
const CACHE = 'lc-LCK-RUFINO-3-1778682677';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.add(new Request('./LCK-RUFINO-3.html', {cache: 'reload'}))));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => k.startsWith('lc-LCK-RUFINO-3-') && k !== CACHE).map(k => caches.delete(k))
  )));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  if (e.request.url.endsWith('.html') || e.request.url.endsWith('/')) {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
  }
});