"""Close all open incidents and write an incident analysis report for every incident.

Run from the project root (D:\\project\\ai-sentinel):

    python close_and_report.py --dry-run          # only show what would happen
    python close_and_report.py                    # close OPEN/ASSIGNED incidents + write reports
    python close_and_report.py --status FALSE_POSITIVE   # close them as false alarms instead
    python close_and_report.py --report-only      # write reports, close nothing
    python close_and_report.py --by "Admin Name"  # name shown in the history

Before closing anything the database file is copied to <db>.bak-<time>.
Output: incident_reports/<INCIDENT_ID>.md for each incident, plus Incident_Report_ALL.md.
"""

import argparse
import os
import shutil
from datetime import datetime, timedelta

from backend.db import get_db, log_audit, now_str

OUT_DIR = "incident_reports"
FMT = "%Y-%m-%d %H:%M:%S"

IMPACT = {
    "helmet": "Head injury risk from falling objects, impact or electrical contact.",
    "gloves": "Hand injury risk: cuts, burns, chemical exposure or crushing.",
    "goggles": "Eye injury risk from dust, splashes, sparks or flying particles.",
    "boots": "Foot injury risk from falling or sharp objects, slips or crushing.",
    "vest": "Reduced visibility near vehicles and machinery, raising collision risk.",
}


# ------------------------------------------------------------------ helpers
def val(x, default="-"):
    if x is None or str(x).strip() == "":
        return default
    return str(x).replace("|", "\\|").replace("\n", " ").strip()


def pretty(violation):
    return str(violation or "-").replace("_", " ").strip().title()


def parse(ts):
    try:
        return datetime.strptime(str(ts)[:19], FMT)
    except Exception:
        return None


def minutes_between(a, b):
    da, db_ = parse(a), parse(b)
    if da and db_:
        return round((db_ - da).total_seconds() / 60, 1)
    return None


def impact_for(violation):
    v = str(violation or "").lower()
    for key, text in IMPACT.items():
        if key in v:
            return text
    return "Increased risk of workplace injury and a safety-compliance breach."


def table(rows):
    out = ["| Field Name | Value |", "|---|---|"]
    out += [f"| {k} | {val(v)} |" for k, v in rows]
    return "\n".join(out)


# ------------------------------------------------------------------ closing
def db_file(conn):
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row[1] == "main" and row[2]:
            return row[2]
    return None


def close_all(conn, status, actor, dry_run):
    rows = conn.execute(
        "SELECT incident_id, status, ai_review FROM incidents "
        "WHERE UPPER(COALESCE(status,'')) IN ('OPEN','ASSIGNED') ORDER BY id"
    ).fetchall()

    print(f"{len(rows)} open/assigned incident(s) found.")
    if dry_run or not rows:
        return len(rows)

    path = db_file(conn)
    if path and os.path.exists(path):
        backup = f"{path}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, backup)
        print("Backup saved:", backup)

    ts = now_str()
    note = "Closed in bulk by " + actor
    for r in rows:
        iid = r["incident_id"]
        conn.execute(
            "UPDATE incidents SET status=?, resolved_at=?, updated_at=?, overdue_alerted=1, "
            "resolution_notes=CASE WHEN COALESCE(resolution_notes,'')='' THEN ? ELSE resolution_notes END "
            "WHERE incident_id=?",
            (status, ts, ts, note, iid),
        )
        conn.execute(
            "INSERT INTO incident_comments (incident_id, author, author_email, message, created_at) "
            "VALUES (?,?,?,?,?)",
            (iid, "System", "", f"{note} (status set to {status})", ts),
        )
    conn.commit()

    for r in rows:
        try:
            log_audit(actor, "INCIDENT_BULK_CLOSE", "incident", r["incident_id"], status)
        except Exception:
            pass

    print(f"Closed {len(rows)} incident(s) as {status}.")
    return len(rows)


# ------------------------------------------------------------------ report
def build_report(inc, comments):
    iid = inc["incident_id"]
    v = pretty(inc.get("violation"))
    started = parse(inc.get("timestamp"))
    dur = inc.get("duration_sec")
    last_seen = inc.get("timestamp")
    try:
        if started and dur:
            last_seen = (started + timedelta(seconds=float(dur))).strftime(FMT)
    except Exception:
        pass

    conf = inc.get("confidence")
    conf_txt = f"{float(conf) * 100:.0f}%" if conf not in (None, "") else "-"
    status = str(inc.get("status") or "").upper()
    ttr = minutes_between(inc.get("timestamp"), inc.get("resolved_at")) if inc.get("resolved_at") else None
    due = inc.get("due_at")
    late = bool(due and inc.get("resolved_at") and str(inc["resolved_at"]) > str(due))

    L = []
    L.append(f"# Incident Analysis Report - {iid}\n")

    L.append("## 1. General Event Detail\n")
    L.append(table([
        ("Incident ID", iid),
        ("Incident Type", f"PPE Violation - {v}"),
        ("Severity", inc.get("severity")),
        ("First Seen", inc.get("timestamp")),
        ("Last Seen", last_seen),
        ("Event Source", "Camera detection (AI)"),
        ("Event Category", "Safety / PPE compliance"),
        ("Detection Confidence", conf_txt),
        ("Current Status", status or "-"),
    ]))

    L.append("\n\n## 2. Camera & Location Information\n")
    L.append(table([
        ("Camera", inc.get("camera_name")),
        ("Camera ID", inc.get("camera_id")),
        ("Zone", inc.get("zone")),
        ("Tracked Person ID", inc.get("track_id")),
        ("Duration Visible Without PPE (sec)", inc.get("duration_sec")),
    ]))

    L.append("\n\n## 3. Evidence\n")
    L.append(f"- **Screenshot:** {val(inc.get('screenshot'))}")
    L.append(f"- **Video clip:** {val(inc.get('clip'))}")
    L.append(f"- **Description:** {val(inc.get('description'))}")

    L.append("\n## 4. Assignment & Response\n")
    L.append(table([
        ("Assigned To", inc.get("assigned_to")),
        ("Assigned By", inc.get("assigned_by")),
        ("Deadline", due),
        ("Resolved At", inc.get("resolved_at")),
        ("Time To Resolve (min)", ttr),
        ("Resolved After Deadline", "Yes" if late else ("No" if inc.get("resolved_at") and due else "-")),
        ("Resolution Notes", inc.get("resolution_notes")),
    ]))

    L.append("\n\n## 5. AI Review\n")
    L.append(table([
        ("AI Review State", inc.get("ai_review")),
        ("AI Note", inc.get("ai_note")),
    ]))

    L.append("\n\n## 6. Alert Analysis\n")
    L.append(
        f"The system detected a **{v}** violation of **{val(inc.get('severity'))}** severity on "
        f"**{val(inc.get('camera_name'))}** (zone: {val(inc.get('zone'))}) at "
        f"**{val(inc.get('timestamp'))}** with {conf_txt} detection confidence."
    )

    L.append("\n## 7. Investigation\n")
    if comments:
        L.append("Activity history for this incident:\n")
        for c in comments:
            L.append(f"- {val(c.get('created_at'))} - **{val(c.get('author'))}**: {val(c.get('message'))}"
                     + (f" _(photo: {c['attachment']})_" if c.get("attachment") else ""))
    else:
        L.append("No replies or system activity were recorded for this incident.")

    L.append("\n## 8. Potential Impact\n")
    L.append(impact_for(inc.get("violation")))

    L.append("\n## 9. Conclusion\n")
    if status == "RESOLVED":
        L.append(f"The incident is **resolved**" + (f" after about {ttr} minutes" if ttr is not None else "")
                 + (", past its deadline." if late else "."))
    elif status == "FALSE_POSITIVE":
        L.append("The incident was reviewed and closed as a **false alarm**. It can be used as a negative "
                 "example when retraining the detection model.")
    else:
        L.append("The incident is still **open** and needs follow-up.")

    L.append("\n## 10. Data Notes\n")
    missing = [k for k in ("screenshot", "clip", "assigned_to", "resolution_notes") if not inc.get(k)]
    L.append("Fields with no recorded value: " + (", ".join(missing) if missing else "none") + ".")
    L.append("\nThis report is generated only from the incident record and its comment history; "
             "nothing has been added beyond what the system stored.")
    return "\n".join(L) + "\n"


def write_reports(conn):
    os.makedirs(OUT_DIR, exist_ok=True)
    incidents = [dict(r) for r in conn.execute("SELECT * FROM incidents ORDER BY id").fetchall()]

    combined = [f"# AI Sentinel - Incident Reports\n\nGenerated: {now_str()}  |  Incidents: {len(incidents)}\n"]
    for inc in incidents:
        try:
            comments = [dict(c) for c in conn.execute(
                "SELECT * FROM incident_comments WHERE incident_id=? ORDER BY id", (inc["incident_id"],)).fetchall()]
        except Exception:
            comments = []
        text = build_report(inc, comments)
        with open(os.path.join(OUT_DIR, f"{inc['incident_id']}.md"), "w", encoding="utf-8") as f:
            f.write(text)
        combined.append("\n---\n\n" + text)

    with open("Incident_Report_ALL.md", "w", encoding="utf-8") as f:
        f.write("\n".join(combined))
    print(f"Wrote {len(incidents)} report(s) to {OUT_DIR}\\ and Incident_Report_ALL.md")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", default="RESOLVED", choices=["RESOLVED", "FALSE_POSITIVE"])
    ap.add_argument("--by", default="Admin")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    conn = get_db()
    try:
        if not args.report_only:
            close_all(conn, args.status, args.by, args.dry_run)
        if not args.dry_run:
            write_reports(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()