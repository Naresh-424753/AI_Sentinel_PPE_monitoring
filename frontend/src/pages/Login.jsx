import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";

export default function Login() {
  const { login } = useAuth();
  const [mode, setMode] = useState("login"); // login | signup | forgot
  const [step, setStep] = useState(1);       // forgot-password step
  const [f, setF] = useState({ name: "", email: "", password: "", otp: "", token: "", confirm: "" });
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  function switchTo(m) { setMode(m); setStep(1); setErr(""); setMsg(""); }

  async function submit(e) {
    e.preventDefault();
    setErr(""); setMsg(""); setBusy(true);
    try {
      if (mode === "login") {
        await login(f.email.trim(), f.password);
      } else if (mode === "signup") {
        await api("/signup", { method: "POST", body: { name: f.name, email: f.email.trim(), password: f.password } });
        await login(f.email.trim(), f.password);
      } else if (step === 1) {
        await api("/request-password-reset", { method: "POST", body: { email: f.email.trim() } });
        setMsg("If the account exists, a 6-digit code was emailed to you.");
        setStep(2);
      } else if (step === 2) {
        const r = await api("/verify-password-reset", { method: "POST", body: { email: f.email.trim(), email_otp: f.otp.trim() } });
        setF({ ...f, token: r.reset_token });
        setStep(3);
      } else {
        await api("/reset-password", { method: "POST", body: { reset_token: f.token, new_password: f.password, confirm_password: f.confirm } });
        switchTo("login");
        setMsg("Password changed. You can log in now.");
      }
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  const title = mode === "login" ? "Log in" : mode === "signup" ? "Create account" : "Reset password";
  return (
    <div className="loginwrap">
      <section className="loginhero">
        <h1>Helmets on.<br />Every shift.</h1>
        <p>Live PPE monitoring for your site. Violations are caught on camera, logged, and sent to the right person.</p>
      </section>
      <section className="loginform">
        <form className="panel" onSubmit={submit}>
          <h2 style={{ marginBottom: 14 }}>{title}</h2>
          {mode === "signup" && (
            <div className="field"><label htmlFor="n">Full name</label><input id="n" value={f.name} onChange={set("name")} required /></div>
          )}
          {(mode !== "forgot" || step < 3) && (
            <div className="field"><label htmlFor="e">Email</label><input id="e" type="email" value={f.email} onChange={set("email")} required disabled={mode === "forgot" && step > 1} /></div>
          )}
          {mode === "forgot" && step === 2 && (
            <div className="field"><label htmlFor="o">6-digit code</label><input id="o" inputMode="numeric" maxLength={6} value={f.otp} onChange={set("otp")} required /></div>
          )}
          {(mode !== "forgot" || step === 3) && (
            <div className="field">
              <label htmlFor="p">{mode === "forgot" ? "New password" : "Password"}</label>
              <input id="p" type="password" value={f.password} onChange={set("password")} required minLength={mode === "login" ? 1 : 8} />
              {mode !== "login" && <div className="hint">At least 8 characters.</div>}
            </div>
          )}
          {mode === "forgot" && step === 3 && (
            <div className="field"><label htmlFor="c">Confirm new password</label><input id="c" type="password" value={f.confirm} onChange={set("confirm")} required /></div>
          )}
          {err && <div className="err" role="alert">{err}</div>}
          {msg && <div className="ok">{msg}</div>}
          <button className="hivis" disabled={busy} style={{ width: "100%" }}>
            {busy ? "Please wait" : mode === "forgot" ? (step === 1 ? "Send code" : step === 2 ? "Verify code" : "Change password") : title}
          </button>
          <p style={{ marginBottom: 0, fontSize: 14 }}>
            {mode === "login" && (<><button type="button" className="linkbtn" onClick={() => switchTo("signup")}>Create an account</button>{" | "}<button type="button" className="linkbtn" onClick={() => switchTo("forgot")}>Forgot password</button></>)}
            {mode !== "login" && <button type="button" className="linkbtn" onClick={() => switchTo("login")}>Back to log in</button>}
          </p>
        </form>
      </section>
    </div>
  );
}
