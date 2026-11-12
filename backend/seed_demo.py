"""Fill the database with fake incidents so the dashboard has something to show.
    python -m backend.seed_demo            (adds 150 incidents over the last 14 days)
    python -m backend.seed_demo --clear    (removes only incidents created by this script)"""
import random
import sys
from datetime import datetime, timedelta

from backend import db
from backend.services import incident_service

TYPES = ["no_helmet"] * 4 + ["no_gloves"] * 3 + ["no_goggle"] * 2 + ["no_boots"] * 2 + ["no_vest"] * 2
CAMERAS = [(1, "Main Camera", "General"), (2, "Welding Bay", "Welding"), (3, "Loading Dock", "Logistics")]
USERS = ["Supervisor A", "Supervisor B"]


def main():
    db.init_db()
    db.seed_default_camera()
    conn = db.get_db()
    if "--clear" in sys.argv:
        conn.execute("DELETE FROM incidents WHERE description LIKE '%[demo]'")
        conn.commit()
        print("Demo incidents removed.")
        return
    row = conn.execute("SELECT MAX(CAST(SUBSTR(incident_id,5) AS INTEGER)) FROM incidents WHERE incident_id LIKE 'INC-%'").fetchone()
    n = row[0] or 0
    for _ in range(150):
        n += 1
        v = random.choice(TYPES)
        cam = random.choice(CAMERAS)
        ts = datetime.now() - timedelta(days=random.random() * 14, hours=random.random() * 6)
        hour = random.choice([7, 8, 9, 10, 11, 13, 14, 15, 16, 19, 23])
        ts = ts.replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59))
        if ts > datetime.now():
            ts -= timedelta(days=1)
        status = random.choices(["OPEN", "ASSIGNED", "RESOLVED", "FALSE_POSITIVE"], [4, 2, 6, 1])[0]
        closed = status in ("RESOLVED", "FALSE_POSITIVE")
        stamp = ts.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO incidents (incident_id, violation, confidence, timestamp, screenshot, status, severity,
               camera_id, camera_name, zone, duration_sec, description, assigned_to, resolution_notes, resolved_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"INC-{n:04d}", v, round(random.uniform(0.4, 0.95), 2), stamp, None, status,
             incident_service.severity_for(v), cam[0], cam[1], cam[2], random.randint(3, 90),
             incident_service.enrich_incident_data(v)["description"] + " [demo]",
             random.choice(USERS) if status != "OPEN" else None,
             "Briefed worker and issued PPE." if status == "RESOLVED" else None,
             (ts + timedelta(minutes=random.randint(5, 240))).strftime("%Y-%m-%d %H:%M:%S") if closed else None,
             stamp))
    conn.commit()
    conn.close()
    print("Added 150 demo incidents. Remove them later with: python -m backend.seed_demo --clear")


if __name__ == "__main__":
    main()
