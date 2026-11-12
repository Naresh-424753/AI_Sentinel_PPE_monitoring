import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "./api.js";
import { useLive } from "./live.jsx";
import { Empty, Modal, fmtTime } from "./ui.jsx";

export default function Bell() {
  const { notifTick } = useLive();
  const nav = useNavigate();
  const [data, setData] = useState({ items: [], unread: 0 });
  const [open, setOpen] = useState(false);

  const load = useCallback(() => api("/api/notifications").then(setData).catch(() => {}), []);
  useEffect(() => { load(); }, [load, notifTick]);

  async function close() {
    setOpen(false);
    if (data.unread) {
      await api("/api/notifications/read", { method: "POST" }).catch(() => {});
      load();
    }
  }
  function go(n) {
    close();
    nav(n.incident_id ? `/incidents?open=${encodeURIComponent(n.incident_id)}` : "/incidents");
  }

  return (
    <>
      <button className="ghost" onClick={() => setOpen(true)} aria-label={`Notifications, ${data.unread} unread`}>
        Alerts{data.unread ? ` (${data.unread})` : ""}
      </button>
      {open && (
        <Modal title="Notifications" onClose={close}>
          {data.items.length ? data.items.map((n) => (
            <div key={n.id} className={`notif ${n.is_read ? "" : "new"}`} role="button" tabIndex={0}
              onClick={() => go(n)} onKeyDown={(e) => e.key === "Enter" && go(n)}>
              <b>{n.title}</b>
              <div>{n.message}</div>
              <div className="hint">{fmtTime(n.created_at)}</div>
            </div>
          )) : <Empty>Nothing yet. You will see assignments, replies and overdue alerts here.</Empty>}
        </Modal>
      )}
    </>
  );
}
