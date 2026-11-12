// Built app (npm run build): the backend serves the page, so requests go to the same address (works from a phone).
// Dev server (npm run dev): talk to the backend on this computer, or to VITE_API_URL.
export const API = import.meta.env.PROD
  ? (import.meta.env.VITE_API_URL_PROD || "")
  : (import.meta.env.VITE_API_URL || "http://127.0.0.1:8000");
const KEY = "sentinel_token";

export const getToken = () => localStorage.getItem(KEY);
export const setToken = (t) => (t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY));

// <img>, <video> and EventSource cannot send headers, so they carry the token in the URL.
export const tokenUrl = (path) =>
  `${API}${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(getToken() || "")}`;

export const asset = (kind, name) =>
  name ? `${API}/${kind}/${encodeURIComponent(String(name).split(/[\\/]/).pop())}` : null;

function withParams(path, params) {
  if (!params) return API + path;
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null)).toString();
  return API + path + (qs ? (path.includes("?") ? "&" : "?") + qs : "");
}

async function request(path, { method = "GET", body, params } = {}) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (getToken()) headers.Authorization = `Bearer ${getToken()}`;
  const res = await fetch(withParams(path, params), { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && getToken()) {
    setToken(null);
    window.dispatchEvent(new Event("sentinel-logout"));
  }
  return res;
}

export async function api(path, opts) {
  const res = await request(path, opts);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : (d && d[0] && d[0].msg) || `Request failed (${res.status})`);
  }
  return data;
}

export async function download(path, params, filename) {
  const res = await request(path, { params });
  if (!res.ok) throw new Error(`Export failed (${res.status})`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}