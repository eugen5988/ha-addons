/* Service Worker der Ausgaben-Web-App: Offline-Start und letzter Datenstand.
   Die Version setzt der Server beim Ausliefern ein. */
const VERSION = "__VERSION__";
const SHELL = `shell-${VERSION}`;
const DATEN = "api-daten";
const SHELL_DATEIEN = [
  "./",
  `static/app.css?v=${VERSION}`,
  `static/app.js?v=${VERSION}`,
  "static/manifest.json",
  "static/icon-180.png",
  "static/icon-192.png",
  "static/icon-512.png",
];

self.addEventListener("install", (ev) => {
  ev.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_DATEIEN)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith("shell-") && k !== SHELL) await caches.delete(k);
    await self.clients.claim();
  })());
});

function mitZeitlimit(promise, ms) {
  return Promise.race([promise, new Promise((_, nein) => setTimeout(() => nein(new Error("Zeitlimit")), ms))]);
}

async function netzZuerst(req, cacheName, ms) {
  const cache = await caches.open(cacheName);
  try {
    const r = await mitZeitlimit(fetch(req), ms);
    if (r.ok) {
      const kopf = new Headers(r.headers);
      kopf.set("X-Gespeichert", String(Date.now()));
      const kopie = new Response(await r.clone().blob(), { status: r.status, statusText: r.statusText, headers: kopf });
      cache.put(req, kopie);
    }
    return r;
  } catch (e) {
    const alt = await cache.match(req, { ignoreVary: true });
    if (alt) {
      const kopf = new Headers(alt.headers);
      kopf.set("X-Ausgaben-Offline", alt.headers.get("X-Gespeichert") || "0");
      return new Response(await alt.blob(), { status: 200, headers: kopf });
    }
    return new Response(JSON.stringify({ detail: "offline" }), {
      status: 503, headers: { "Content-Type": "application/json", "X-Ausgaben-Offline": "0" },
    });
  }
}

self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  const pfad = url.pathname;
  if (pfad.startsWith("/api/")) {
    if (pfad === "/api/export" || pfad.startsWith("/api/tr/login") || pfad.includes("/auftrag")) return;
    ev.respondWith(netzZuerst(req, DATEN, 8000));
    return;
  }
  if (req.mode === "navigate" || pfad === "/") {
    ev.respondWith((async () => {
      try {
        const r = await mitZeitlimit(fetch(req), 5000);
        if (r.ok) (await caches.open(SHELL)).put("./", r.clone());
        return r;
      } catch (e) {
        return (await caches.match("./", { cacheName: SHELL })) || Response.error();
      }
    })());
    return;
  }
  if (pfad.startsWith("/static/")) {
    ev.respondWith((async () => (await caches.match(req)) || fetch(req))());
  }
});
