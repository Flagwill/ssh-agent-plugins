/* homelab PWA service worker
   策略:
     - HTML/JS/CSS 等应用外壳: network-first(在线永远拿最新, 离线回退缓存)
     - 图标等其余静态资源: cache-first + 后台更新
     - /home/api/*: 永不缓存
   旧版是 cache-first, 导致部署后必须刷两次才更新, 故改为 network-first。 */
const CACHE = "homelab-v4";
const SHELL = [
  "/home/", "/home/index.html", "/home/style.css", "/home/app.js",
  "/home/manifest.webmanifest",
  "/home/icons/icon-192.png", "/home/icons/icon-512.png",
  "/home/icons/apple-touch-icon.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (e) => {
  if (e.data === "skip-waiting") self.skipWaiting();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/home/api/")) return; // API 永不缓存
  if (e.request.method !== "GET") return;

  // 应用外壳(页面导航 + index/app.js/style.css): network-first
  const isShell = e.request.mode === "navigate" ||
    SHELL.some((p) => url.pathname === p);
  if (isShell) {
    e.respondWith(
      fetch(e.request)
        .then((r) => {
          if (r && r.ok) {
            const copy = r.clone();
            caches.open(CACHE).then((c) => c.put(e.request, copy));
          }
          return r;
        })
        .catch(() => caches.match(e.request).then((hit) => hit || caches.match("/home/")))
    );
    return;
  }

  // 其余静态资源: cache-first + 后台刷新
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request)
        .then((r) => {
          if (r && r.ok) caches.open(CACHE).then((c) => c.put(e.request, r.clone()));
          return r;
        })
        .catch(() => hit);
      return hit || net;
    })
  );
});
