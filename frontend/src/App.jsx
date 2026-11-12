import React from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { useAuth } from "./auth.jsx";
import { LiveProvider, useLive } from "./live.jsx";
import Bell from "./Bell.jsx";
import PushToggle from "./PushToggle.jsx";
import { SEV_COLOR } from "./ui.jsx";
import Login from "./pages/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Incidents from "./pages/Incidents.jsx";
import Cameras from "./pages/Cameras.jsx";
import Admin from "./pages/Admin.jsx";
import TV from "./pages/TV.jsx";

function Toasts() {
  const { toasts, dismiss } = useLive();
  const nav = useNavigate();
  return (
    <div className="toasts" aria-live="assertive">
      {toasts.map((t) => (
        <div key={t.id} className="toast" style={{ "--c": SEV_COLOR[t.severity] || "#2F6B8F" }}
          onClick={() => { dismiss(t.id); nav(t.code ? `/incidents?open=${encodeURIComponent(t.code)}` : "/incidents"); }}>
          <b>{t.title}</b>
          <small>{t.text || `${t.code} - ${t.camera || "camera"} - ${String(t.severity || "").toLowerCase()}`}</small>
        </div>
      ))}
    </div>
  );
}

function Shell() {
  const { user, logout, can } = useAuth();
  const { muted, setMuted } = useLive();
  return (
    <div className="shell">
      <aside className="side">
        <div>
          <div className="stripe" />
          <div className="brand">AI Sentinel<small>PPE monitoring</small></div>
        </div>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/incidents">Incidents</NavLink>
          <NavLink to="/cameras">Cameras</NavLink>
          {can("ADMIN") && <NavLink to="/admin">Admin</NavLink>}
        </nav>
        <div className="me">
          <b>{user.name}</b>
          {String(user.role).replace("_", " ").toLowerCase()}
          <div className="row">
            <Bell />
            <PushToggle />
            <button className="ghost" onClick={() => setMuted(!muted)}>{muted ? "Sound off" : "Sound on"}</button>
            <button className="ghost" onClick={logout}>Log out</button>
          </div>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/cameras" element={<Cameras />} />
          <Route path="/admin" element={can("ADMIN") ? <Admin /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <Toasts />
    </div>
  );
}

export default function App() {
  const { user, ready } = useAuth();
  if (!ready) return null;
  if (!user) return <Login />;
  return (
    <LiveProvider>
      <Routes>
        <Route path="/tv" element={<TV />} />
        <Route path="*" element={<Shell />} />
      </Routes>
    </LiveProvider>
  );
}