// Bump CACHE on every shell change - the activate handler below deletes any
// cache whose key doesn't match, so a new version is what evicts the old one.
const CACHE = 'mycelial-shell-v26';
const SHELL = ['./', './index.html', './style.css?v=24', './app.js?v=24', './manifest.json'];

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
      .catch(() => caches.match(event.request).then((cached) => {
        if (cached) return cached;
        // caches.match() resolves to UNDEFINED on a miss, and respondWith(undefined)
        // is what Safari reports as "FetchEvent.respondWith received an error:
        // Returned response is null." That message names the service worker, not
        // the real fault, so the grower chasing a dead reservoir dashboard was
        // handed a red herring instead of "the network request failed".
        //
        // A worker that cannot answer must say so in a Response, not by handing
        // back nothing. Same rule as everywhere else here: a check that found
        // nothing must say so, distinctly from one that found the thing to be fine.
        return new Response(
          'Offline: ' + event.request.url + ' is not cached and the network did not '
          + 'answer. If this is https with a self-signed certificate, a service '
          + 'worker fetch cannot show the certificate prompt - trust the certificate '
          + 'in Settings first.',
          { status: 503, statusText: 'Offline and uncached',
            headers: { 'Content-Type': 'text/plain' } });
      }))
  );
});
