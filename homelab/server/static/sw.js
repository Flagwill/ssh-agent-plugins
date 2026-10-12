/* homelab PWA service worker: 静态资源 cache-first, API network-only */
const CACHE = "homelab-v2";
const SHELL = [
  "/home/", "/home/style.css", "/home/app.js", "/home/manifest.webmanifest",
  "/home/icons/icon-192.png", "/home/icons/icon-512.png",
  "/home/icons/apple-touch-icon.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) =>
    Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/home/api/")) return; // API 永不缓存
  if (e.request.method !== "GET") return;
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request).then((r) => {
        if (r.ok) caches.open(CACHE).then((c) => c.put(e.request, r.clone()));
        return r;
      }).catch(() => hit || caches.match("/home/"));
      return hit || net;
    })
  );
});
