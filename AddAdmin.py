import os
import sqlite3
from getpass import getpass

from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secureverify.db")

admin_id = input("Admin ID: ").strip().upper()
if not admin_id:
    raise SystemExit("Admin ID cannot be empty.")

password = getpass("Password: ")
if not password:
    raise SystemExit("Password cannot be empty.")

if password != getpass("Confirm password: "):
    raise SystemExit("Passwords do not match.")

if not os.path.isfile(DB_PATH):
    raise SystemExit(f"Database not found: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)
try:
    exists = conn.execute(
        "SELECT 1 FROM admins WHERE admin_id = ?",
        (admin_id,),
    ).fetchone()

    if exists:
        raise SystemExit(f"Admin ID {admin_id} already exists; no admin was added.")

    conn.execute(
        "INSERT INTO admins (admin_id, password_hash) VALUES (?, ?)",
        (admin_id, generate_password_hash(password)),
    )
    conn.commit()
    print(f"Admin {admin_id} added.")
finally:
    conn.close()
