"""CSV / Excel / PDF exports."""
import csv
import io
import os
from datetime import datetime

from backend import config

COLUMNS = [
    ("incident_id", "ID"), ("timestamp", "Time"), ("violation", "Violation"),
    ("severity", "Severity"), ("camera_name", "Camera"), ("zone", "Zone"),
    ("confidence", "Confidence"), ("status", "Status"), ("assigned_to", "Assigned to"),
    ("duration_sec", "Duration (s)"), ("resolution_notes", "Resolution notes"),
]


def _val(row, key):
    v = row.get(key)
    if key == "confidence" and v is not None:
        return f"{float(v) * 100:.1f}%"
    if key == "duration_sec" and v is not None:
        return round(float(v), 1)
    return "" if v is None else v


def to_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([h for _, h in COLUMNS])
    for r in rows:
        w.writerow([_val(r, k) for k, _ in COLUMNS])
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx(rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Incidents"
    ws.append([h for _, h in COLUMNS])
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="FFD60A")
    for r in rows:
        ws.append([_val(r, k) for k, _ in COLUMNS])
    for i, (_, h) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(14, len(h) + 4)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate_pdf_report(rows, title="AI Sentinel - Safety Audit Report"):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    path = os.path.join(config.REPORTS_DIR, f"report_{datetime.now():%Y%m%d_%H%M%S}.pdf")
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), leftMargin=24, rightMargin=24,
                            topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    sev = {}
    for r in rows:
        s = r.get("severity") or "MEDIUM"
        sev[s] = sev.get(s, 0) + 1
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(sev.items())) or "no incidents"
    story = [
        Paragraph(title, styles["Title"]),
        Paragraph(f"Generated {datetime.now():%Y-%m-%d %H:%M} | {len(rows)} incidents | {summary}", styles["Normal"]),
        Spacer(1, 12),
    ]
    cols = [("incident_id", "ID"), ("timestamp", "Time"), ("violation", "Violation"),
            ("severity", "Severity"), ("camera_name", "Camera"), ("confidence", "Conf."), ("status", "Status")]
    data = [[h for _, h in cols]] + [[str(_val(r, k)) for k, _ in cols] for r in rows[:500]]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FFD60A")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#999999")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F4F5")]),
    ]))
    story.append(table)
    if len(rows) > 500:
        story.append(Paragraph(f"Showing first 500 of {len(rows)} incidents. Use the Excel export for the full list.", styles["Italic"]))
    doc.build(story)
    return path
