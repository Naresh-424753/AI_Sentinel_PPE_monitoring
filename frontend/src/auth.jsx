import React, { createContext, useContext, useEffect, useState } from "react";
import { api, getToken, setToken } from "./api.js";

const RANK = { USER: 1, VIEWER: 1, SUPERVISOR: 2, ADMIN: 3, SUPER_ADMIN: 4 };
export const rank = (role) => RANK[String(role || "").toUpperCase()] || 1;

const Ctx = createContext(null);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const onLogout = () => setUser(null);
    window.addEventListener("sentinel-logout", onLogout);
    if (!getToken()) {
      setReady(true);
    } else {
      api("/api/me")
        .then((r) => setUser(r.user))
        .catch(() => setToken(null))
        .finally(() => setReady(true));
    }
    return () => window.removeEventListener("sentinel-logout", onLogout);
  }, []);

  async function login(email, password) {
    const r = await api("/login", { method: "POST", body: { email, password } });
    setToken(r.token);
    setUser(r.user);
  }
  function logout() {
    setToken(null);
    setUser(null);
  }
  const can = (minRole) => user && rank(user.role) >= rank(minRole);

  return <Ctx.Provider value={{ user, ready, login, logout, can }}>{children}</Ctx.Provider>;
}
