/* AI Sentinel service worker: makes the dashboard installable and shows push alerts.
   It does not cache anything on purpose: a safety dashboard must never show stale data. */

const OFFLINE_PAGE =
  '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<title>AI Sentinel</title><body style="font:16px system-ui;background:#12222B;color:#fff;display:grid;' +
  'place-items:center;min-height:100vh;margin:0;text-align:center;padding:24px"><div><h1>No connection</h1>' +
  "<p>AI Sentinel cannot reach the server. Check your network and open the app again.</p></div></body>";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(
        () => new Response(OFFLINE_PAGE, { headers: { "Content-Type": "text/html; charset=utf-8" } })
      )
    );
  }
});

self.addEventListener("push", (event) => {
  let d = {};
  try {
    d = event.data ? event.data.json() : {};
  } catch (e) {
    d = { body: event.data ? event.data.text() : "" };
  }
  const title = d.title || "AI Sentinel";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: d.body || "",
      icon: "/icons/icon-192.png",
      badge: "/icons/badge-96.png",
      tag: d.tag || undefined,
      renotify: !!d.tag,
      requireInteraction: d.severity === "CRITICAL",
      data: { url: d.url || "/incidents" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  // a notification can only ever open a page of this site
  let url = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin);
  if (url.origin !== self.location.origin) url = new URL("/", self.location.origin);
  const target = url.href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (list) => {
      for (const c of list) {
        if (c.url.startsWith(self.location.origin)) {
          try {
            await c.focus();
            if ("navigate" in c) await c.navigate(target);
            return;
          } catch (e) {
            /* fall through and open a new window */
          }
        }
      }
      return self.clients.openWindow(target);
    })
  );
});