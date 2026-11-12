import React, { useEffect, useState } from "react";
import { SUPPORT_TEXT, currentSubscription, disablePush, enablePush, pushSupport, sendTestPush } from "./push.js";
import { Modal } from "./ui.jsx";

// "Push: On/Off" button. Turns alerts on for THIS phone or browser, even when the app is closed.
export default function PushToggle() {
  const support = pushSupport();
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState(null);   // { title, text }

  useEffect(() => {
    if (!support.ok) return;
    currentSubscription()
      .then((s) => setOn(!!s && Notification.permission === "granted"))
      .catch(() => {});
  }, [support.ok]);

  async function click() {
    if (!support.ok) return setInfo({ title: "Phone alerts", text: SUPPORT_TEXT[support.reason] });
    setBusy(true);
    try {
      if (on) {
        await disablePush();
        setOn(false);
      } else {
        await enablePush();
        setOn(true);
        setInfo({ title: "Phone alerts are on", text: "New incidents and replies will appear as notifications on this device. Use Send test to check." });
      }
    } catch (e) {
      setInfo({ title: "Phone alerts", text: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    try {
      await sendTestPush();
      setInfo({ title: "Test sent", text: "A notification should arrive in a few seconds." });
    } catch (e) {
      setInfo({ title: "Test failed", text: e.message });
    }
  }

  return (
    <>
      <button type="button" className="ghost" disabled={busy} onClick={click} aria-pressed={on}
        title="Alerts on this phone or browser, even when the app is closed">
        Push {on ? "on" : "off"}
      </button>
      {info && (
        <Modal title={info.title} onClose={() => setInfo(null)}>
          <p>{info.text}</p>
          {on && <button type="button" className="ghost" onClick={test}>Send test</button>}
        </Modal>
      )}
    </>
  );
}