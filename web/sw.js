/* ASMR Mirror service worker — stale-while-revalidate for the app shell and the
   sharded catalog data, so repeat visits are instant and browsing works offline.

   Only same-origin GETs are cached. Media streams from the cross-origin file
   origin and is deliberately NOT cached (55 GB — let the browser/network handle
   it). Bump VERSION whenever the data layout changes so old shards are evicted. */
const VERSION = 'asmr-v6';

self.addEventListener('install', e => self.skipWaiting());

self.addEventListener('activate', e => e.waitUntil((async () => {
  const keys = await caches.keys();
  await Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k)));
  await self.clients.claim();
})()));

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;      // skip media origin

  e.respondWith((async () => {
    const cache = await caches.open(VERSION);
    const cached = await cache.match(req);
    const network = fetch(req).then(res => {
      if (res && res.ok) cache.put(req, res.clone());
      return res;
    }).catch(() => cached);
    return cached || network;                            // fast, refresh in bg
  })());
});
