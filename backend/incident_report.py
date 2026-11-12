"""Incident analysis report for one incident, as Markdown (download) or HTML (print / save as PDF).
Built only from what is stored in the database: nothing is invented."""
import html
import os
from datetime import datetime, timedelta
from urllib.parse import quote

from backend import config

FMT = "%Y-%m-%d %H:%M:%S"

IMPACT = {
    "helmet": "Head injury risk from falling objects, impact or electrical contact.",
    "gloves": "Hand injury risk: cuts, burns, chemical exposure or crushing.",
    "goggle": "Eye injury risk from dust, splashes, sparks or flying particles.",
    "boots": "Foot injury risk from falling or sharp objects, slips or crushing.",
    "vest": "Reduced visibility near vehicles and machinery, raising collision risk.",
    "restricted_zone": "A person inside a restricted area may be exposed to machinery, vehicles or hazards "
                       "they are not trained or equipped for.",
}


def pretty(violation):
    return str(violation or "-").replace("_", " ").strip().title()


def _parse(ts):
    try:
        return datetime.strptime(str(ts)[:19], FMT)
    except Exception:
        return None


def _minutes(a, b):
    da, db = _parse(a), _parse(b)
    return round((db - da).total_seconds() / 60, 1) if da and db else None


def _impact(violation):
    v = str(violation or "").lower()
    for key, text in IMPACT.items():
        if key in v:
            return text
    return "Increased risk of workplace injury and a safety-compliance breach."


def _shown(x):
    return "-" if x is None or str(x).strip() == "" else str(x).strip()


def sections(inc, comments):
    """The report as data: a list of (title, [blocks]). A block is ("table", rows), ("list", items) or ("text", str)."""
    v = pretty(inc.get("violation"))
    started = _parse(inc.get("timestamp"))
    last_seen = inc.get("timestamp")
    try:
        if started and inc.get("duration_sec"):
            last_seen = (started + timedelta(seconds=float(inc["duration_sec"]))).strftime(FMT)
    except Exception:
        pass
    conf = inc.get("confidence")
    conf_txt = f"{float(conf) * 100:.0f}%" if conf not in (None, "") else "-"
    status = str(inc.get("status") or "").upper()
    resolved_at, due = inc.get("resolved_at"), inc.get("due_at")
    ttr = _minutes(inc.get("timestamp"), resolved_at) if resolved_at else None
    late = bool(due and resolved_at and str(resolved_at) > str(due))

    out = []
    out.append(("General Event Detail", [("table", [
        ("Incident ID", inc.get("incident_id")),
        ("Incident Type", f"PPE / Safety - {v}"),
        ("Severity", inc.get("severity")),
        ("First Seen", inc.get("timestamp")),
        ("Last Seen", last_seen),
        ("Event Source", "Camera detection (AI)"),
        ("Detection Confidence", conf_txt),
        ("Current Status", status or "-"),
    ])]))
    out.append(("Camera & Location", [("table", [
        ("Camera", inc.get("camera_name")),
        ("Camera ID", inc.get("camera_id")),
        ("Zone", inc.get("zone")),
        ("Tracked Person ID", inc.get("track_id")),
        ("Duration (sec)", inc.get("duration_sec")),
    ])]))
    out.append(("Evidence", [("list", [
        f"Screenshot: {_shown(inc.get('screenshot'))}",
        f"Video clip: {_shown(inc.get('clip'))}",
        f"Description: {_shown(inc.get('description'))}",
    ])]))
    out.append(("Assignment & Response", [("table", [
        ("Assigned To", inc.get("assigned_to")),
        ("Assigned By", inc.get("assigned_by")),
        ("Deadline", due),
        ("Resolved At", resolved_at),
        ("Time To Resolve (min)", ttr),
        ("Resolved After Deadline", "Yes" if late else ("No" if resolved_at and due else "-")),
        ("Resolution Notes", inc.get("resolution_notes")),
    ])]))
    out.append(("AI Review", [("table", [
        ("AI Review State", inc.get("ai_review")),
        ("AI Note", inc.get("ai_note")),
    ])]))
    out.append(("Alert Analysis", [("text",
        f"The system detected a {v} event of {_shown(inc.get('severity'))} severity on "
        f"{_shown(inc.get('camera_name'))} (zone: {_shown(inc.get('zone'))}) at {_shown(inc.get('timestamp'))} "
        f"with {conf_txt} detection confidence.")]))
    if comments:
        items = []
        for c in comments:
            line = f"{_shown(c.get('created_at'))} - {_shown(c.get('author'))}: {_shown(c.get('message'))}"
            if c.get("attachment"):
                line += f" (photo: {c['attachment']})"
            items.append(line)
        out.append(("Investigation", [("text", "Activity history for this incident:"), ("list", items)]))
    else:
        out.append(("Investigation", [("text", "No replies or system activity were recorded for this incident.")]))
    out.append(("Potential Impact", [("text", _impact(inc.get("violation")))]))

    if status == "RESOLVED":
        concl = "The incident is resolved" + (f" after about {ttr} minutes" if ttr is not None else "") \
                + (", past its deadline." if late else ".")
    elif status == "FALSE_POSITIVE":
        concl = ("The incident was reviewed and closed as a false alarm. It can be used as a negative example "
                 "when retraining the detection model.")
    else:
        concl = "The incident is still open and needs follow-up."
    out.append(("Conclusion", [("text", concl)]))

    missing = [k for k in ("screenshot", "clip", "assigned_to", "resolution_notes") if not inc.get(k)]
    out.append(("Data Notes", [
        ("text", "Fields with no recorded value: " + (", ".join(missing) if missing else "none") + "."),
        ("text", "This report is generated only from the incident record and its comment history. "
                 "Nothing has been added beyond what the system stored."),
    ]))
    return out


def build_markdown(inc, comments):
    def cell(x):
        return _shown(x).replace("|", "\\|").replace("\n", " ")

    lines = [f"# Incident Analysis Report - {inc.get('incident_id')}", ""]
    for n, (title, blocks) in enumerate(sections(inc, comments), start=1):
        lines += [f"## {n}. {title}", ""]
        for kind, content in blocks:
            if kind == "table":
                lines += ["| Field Name | Value |", "|---|---|"] + [f"| {k} | {cell(v)} |" for k, v in content]
            elif kind == "list":
                lines += [f"- {cell(x)}" for x in content]
            else:
                lines.append(str(content))
            lines.append("")
    return "\n".join(lines)


def build_html(inc, comments):
    e = html.escape
    shot = None
    if inc.get("screenshot"):
        name = os.path.basename(str(inc["screenshot"]))
        if os.path.exists(os.path.join(config.INCIDENTS_DIR, name)):
            shot = f"{config.PUBLIC_API_URL}/images/{quote(name)}"

    body = []
    for n, (title, blocks) in enumerate(sections(inc, comments), start=1):
        body.append(f"<h2>{n}. {e(title)}</h2>")
        for kind, content in blocks:
            if kind == "table":
                rows = "".join(f"<tr><th>{e(k)}</th><td>{e(_shown(v))}</td></tr>" for k, v in content)
                body.append(f"<table>{rows}</table>")
            elif kind == "list":
                body.append("<ul>" + "".join(f"<li>{e(str(x))}</li>" for x in content) + "</ul>")
            else:
                body.append(f"<p>{e(str(content))}</p>")
        if title == "Evidence" and shot:
            body.append(f'<img class="shot" src="{e(shot)}" alt="Incident screenshot">')

    iid = e(str(inc.get("incident_id")))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Incident report {iid}</title>
<style>
body {{ font: 15px/1.5 "Segoe UI", Arial, sans-serif; color: #12222B; max-width: 820px; margin: 24px auto; padding: 0 16px; }}
h1 {{ font-size: 26px; border-bottom: 6px solid #FFD60A; padding-bottom: 6px; }}
h2 {{ font-size: 18px; margin: 22px 0 6px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #C9D1D6; padding: 6px 10px; text-align: left; vertical-align: top; }}
th {{ width: 34%; background: #F4F6F7; font-weight: 600; }}
.shot {{ max-width: 100%; margin-top: 10px; border: 1px solid #C9D1D6; }}
button {{ background: #FFD60A; border: 0; padding: 8px 14px; font-weight: 600; cursor: pointer; }}
@media print {{ button {{ display: none; }} body {{ margin: 0; }} h2 {{ break-after: avoid; }} table, img {{ break-inside: avoid; }} }}
</style></head><body>
<button onclick="window.print()">Print / Save as PDF</button>
<h1>Incident Analysis Report - {iid}</h1>
{''.join(body)}
</body></html>"""