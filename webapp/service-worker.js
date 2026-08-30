// Bump CACHE on every shell change - the activate handler below deletes any
// cache whose key doesn't match, so a new version is what evicts the old one.
const CACHE = 'mycelial-shell-v18';
const SHELL = ['./', './index.html', './style.css?v=18', './app.js?v=18', './manifest.json'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.includes('/execute')) return; // never cache API calls

  // Network-first, cache-fallback. The previous cache-first version meant a
  // shipped UI change never reached an already-installed client unless CACHE
  // was also bumped - a stale app.js/index.html would be served forever, which
  // is exactly how the photo-upload button went missing on an installed client.
  // Offline still works: the cache answers whenever the network doesn't.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
