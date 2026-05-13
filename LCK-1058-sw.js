// Cache version: 1778682224
const CACHE = 'lc-LCK-1058-1778682224';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.add(new Request('./LCK-1058.html', {cache: 'reload'}))));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => k.startsWith('lc-LCK-1058-') && k !== CACHE).map(k => caches.delete(k))
  )));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  if (e.request.url.endsWith('.html') || e.request.url.endsWith('/')) {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
  }
});