'use strict';

// Change the release for every app-shell update. Progress uses a separate database.
const RELEASE = '2026-11-24.3';
const ROOT = new URL(self.registration.scope);
const PREFIX = 'examen-civique-cr:' + ROOT.pathname + ':';
const CACHE = PREFIX + RELEASE;
const SHELL = new URL('index.html', ROOT).href;
const ASSETS = [
  'index.html', 'progress-store.js', 'pwa.js', 'manifest.webmanifest',
  'apple-touch-icon.png', 'icon-192.png', 'icon-512.png', 'maskable-512.png'
].map(path => new URL(path, ROOT).href);

// Keep one compact copy of the public files in IndexedDB. A browser can
// retain a service-worker registration but lose its Cache Storage entries.
function snapshots(mode, operation) {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(PREFIX + 'offline-files', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('releases');
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      const tx = db.transaction('releases', mode);
      const result = operation(tx.objectStore('releases'));
      tx.oncomplete = () => { db.close(); resolve(result && result.result); };
      tx.onabort = () => { db.close(); reject(tx.error || new Error('Archive unavailable')); };
    };
  });
}

function responseFor(file) {
  return new Response(file.body, { headers: { 'Content-Type': file.type } });
}

async function saveShell() {
  const entries = await Promise.all(ASSETS.map(async url => {
    const response = await fetch(new Request(url, { cache: 'reload' }));
    if (!response.ok || response.redirected) throw new Error('Incomplete offline release');
    return [url, { body: await response.arrayBuffer(), type: response.headers.get('Content-Type') }];
  }));
  const files = Object.fromEntries(entries);
  const saved = await Promise.allSettled([
    caches.open(CACHE).then(cache => Promise.all(entries.map(([url, file]) =>
      cache.put(url, responseFor(file))))),
    snapshots('readwrite', store => store.put(files, RELEASE))
  ]);
  if (saved.every(result => result.status === 'rejected')) throw new Error('Offline storage unavailable');
}

async function hasShell() {
  const cached = await caches.open(CACHE).then(cache => cache.keys()).catch(() => []);
  const urls = new Set(cached.map(request => request.url));
  if (ASSETS.every(url => urls.has(url))) return true;
  const files = await snapshots('readonly', store => store.get(RELEASE)).catch(() => null);
  return Boolean(files && ASSETS.every(url => files[url]));
}

let repairing;
function readyToUse() {
  if (!repairing) repairing = (async () => {
    if (await hasShell()) return true;
    // Existing clients may still use an earlier release during an update.
    // Only repair from the network if the published worker has this release.
    const script = await fetch(new URL('sw.js', ROOT), {cache: 'no-store'});
    if (!script.ok || !(await script.text()).includes("const RELEASE = '" + RELEASE + "';")) return false;
    await saveShell();
    return true;
  })().catch(() => false).finally(() => { repairing = undefined; });
  return repairing;
}

self.addEventListener('install', event => {
  event.waitUntil(saveShell());
  // A new worker waits until all clients of the current release have closed.
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys().catch(() => []);
    await Promise.all(names.filter(name => name.startsWith(PREFIX) && name !== CACHE)
      .map(name => caches.delete(name)));
    await snapshots('readwrite', store => {
      const keys = store.getAllKeys();
      keys.onsuccess = () => {
        for (const key of keys.result) if (key !== RELEASE) store.delete(key);
      };
    }).catch(() => {});
    await self.clients.claim();
  })());
});

self.addEventListener('message', event => {
  if (event.data !== 'check-offline' || !event.ports.length) return;
  event.waitUntil(readyToUse().then(ready => event.ports[0].postMessage(ready)));
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
    const cached = await caches.open(CACHE).then(cache => cache.match(target)).catch(() => null);
    if (cached) return cached;
    const files = await snapshots('readonly', store => store.get(RELEASE)).catch(() => null);
    if (files && files[target]) return responseFor(files[target]);
    return fetch(event.request);
  })());
});
