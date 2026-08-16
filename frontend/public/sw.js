/* Minimal service worker: offline app shell + last-known API data.
 * Bump CACHE_VERSION whenever you want to force clients to drop old caches. */

const CACHE_VERSION = 'v1'
const SHELL_CACHE = `shell-${CACHE_VERSION}`
const ASSET_CACHE = `assets-${CACHE_VERSION}`
const API_CACHE = `api-${CACHE_VERSION}`

const SHELL_URLS = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/favicon.png',
  '/icon-192.png',
  '/icon-512.png',
]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      // Don't let one 404 abort the whole install.
      .then((cache) => Promise.allSettled(SHELL_URLS.map((u) => cache.add(u))))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  const keep = new Set([SHELL_CACHE, ASSET_CACHE, API_CACHE])
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => !keep.has(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting()
})

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName)
  try {
    const response = await fetch(request)
    if (response.ok) cache.put(request, response.clone())
    return response
  } catch (err) {
    const cached = await cache.match(request)
    if (cached) return cached
    throw err
  }
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName)
  const cached = await cache.match(request)
  if (cached) return cached
  const response = await fetch(request)
  if (response.ok) cache.put(request, response.clone())
  return response
}

self.addEventListener('fetch', (event) => {
  const { request } = event

  // Only GETs are cacheable; a POSTed expense must always hit the network.
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  const sameOrigin = url.origin === self.location.origin

  // App shell: serve index.html when offline so the PWA still opens.
  if (request.mode === 'navigate') {
    event.respondWith(
      networkFirst(request, SHELL_CACHE).catch(() => caches.match('/index.html')),
    )
    return
  }

  // Hashed build output never changes under the same name.
  if (sameOrigin && url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirst(request, ASSET_CACHE))
    return
  }

  if (sameOrigin) {
    event.respondWith(networkFirst(request, SHELL_CACHE))
    return
  }

  // Cross-origin = the API. Network-first, falling back to the last good
  // response so the dashboard still renders something on a dead train.
  event.respondWith(networkFirst(request, API_CACHE))
})
