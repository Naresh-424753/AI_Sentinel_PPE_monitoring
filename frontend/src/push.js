import { api } from "./api.js";

// Why push cannot work here, or { ok: true }.
export function pushSupport() {
  if (!window.isSecureContext) return { ok: false, reason: "insecure" };
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
    return { ok: false, reason: ios ? "ios-install" : "unsupported" };
  }
  return { ok: true };
}

export const SUPPORT_TEXT = {
  insecure:
    "Phone alerts need a secure (https) address. Open the dashboard through your https link instead of http://192.168... (see PHONE_SETUP.md).",
  "ios-install":
    "On iPhone: tap Share, then Add to Home Screen. Open Sentinel from the home screen and turn alerts on there.",
  unsupported:
    "This browser cannot show push alerts. Use Chrome on Android, or Safari on iPhone (home-screen app).",
};

function keyToBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export async function currentSubscription() {
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

export async function enablePush() {
  const k = await api("/api/push/key");
  if (!k.available || !k.public_key) throw new Error("Push is not set up on the server. Run: pip install pywebpush, then restart the backend.");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Notifications are blocked. Allow them for this site in the browser or phone settings.");
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyToBytes(k.public_key) });
  const j = sub.toJSON();
  await api("/api/push/subscribe", { method: "POST", body: { endpoint: j.endpoint, keys: j.keys } });
}

export async function disablePush() {
  const sub = await currentSubscription();
  if (!sub) return;
  await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } }).catch(() => {});
  await sub.unsubscribe();
}

export const sendTestPush = () => api("/api/push/test", { method: "POST" });