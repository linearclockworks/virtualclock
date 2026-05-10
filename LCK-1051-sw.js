// Cache version changes on every build — forces old SW to update immediately
const CACHE = 'lc-LCK-1051-1778452000';

self.addEventListener('install', e => {
  // Cache the HTML using a relative request so it works at any URL path
  e.waitUntil(
    caches.open(CACHE).then(c => c.add(new Request('./LCK-1051.html', {cache: 'reload'})))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  // Delete all old caches for this clock
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k.startsWith('lc-LCK-1051-') && k !== CACHE)
            .map(k => { console.log('SW: deleting old cache', k); return caches.delete(k); })
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // Cache-first for same-origin HTML; network-first for everything else
  if (e.request.url.endsWith('.html') || e.request.url.endsWith('/')) {
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request))
    );
  }
});
