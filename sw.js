'use strict';

// Change the release when publishing any app-shell change. Keep the storage keys stable.
const RELEASE = '2026-11-24.1';
const ROOT = new URL(self.registration.scope);
const PREFIX = 'examen-civique-cr:' + ROOT.pathname + ':';
const CACHE = PREFIX + RELEASE;
const SHELL = new URL('index.html', ROOT).href;
const ASSETS = [
  'index.html', 'progress-store.js', 'pwa.js', 'manifest.webmanifest',
  'apple-touch-icon.png', 'icon-192.png', 'icon-512.png', 'maskable-512.png'
].map(path => new URL(path, ROOT).href);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache =>
    cache.addAll(ASSETS.map(url => new Request(url, { cache: 'reload' })))
  ));
  // Let an active quiz finish using its current release.
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith(PREFIX) && name !== CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== ROOT.origin) return;
  const isStart = event.request.mode === 'navigate' &&
    (url.pathname === ROOT.pathname || url.pathname === new URL(SHELL).pathname);
  const target = isStart ? SHELL : url.origin + url.pathname;
  if (!ASSETS.includes(target)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    return await cache.match(target) || await fetch(event.request);
  })());
});
