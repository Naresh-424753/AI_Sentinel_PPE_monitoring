"""Promote an existing account to SUPER_ADMIN.
    python make_super_admin.py you@example.com
(Sign up in the dashboard first, then run this once.)"""
import sqlite3
import sys

from backend import config

if len(sys.argv) != 2:
    sys.exit("Usage: python make_super_admin.py EMAIL")

email = sys.argv[1].strip()
conn = sqlite3.connect(config.DB_PATH)
cur = conn.execute("UPDATE users SET role='SUPER_ADMIN' WHERE LOWER(email)=LOWER(?)", (email,))
conn.commit()
if cur.rowcount == 0:
    sys.exit(f"No user found with email {email}. Sign up first.")
print("USER:", conn.execute("SELECT id, name, email, role FROM users WHERE LOWER(email)=LOWER(?)", (email,)).fetchone())
conn.close()
