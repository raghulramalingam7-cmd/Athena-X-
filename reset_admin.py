#!/usr/bin/env python3
"""
Run this script once to reset/create the admin account.
Usage:  python3 reset_admin.py
"""
import sqlite3, os, getpass
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), 'gully_sports.db')

print("=== Athena-X Admin Reset ===")
print(f"Database: {DB_PATH}\n")

username = input("Admin username [admin]: ").strip() or "admin"
email    = input("Admin email [admin@gully.com]: ").strip() or "admin@gully.com"
while True:
    pwd  = getpass.getpass("New password: ")
    pwd2 = getpass.getpass("Confirm password: ")
    if pwd == pwd2 and len(pwd) >= 6:
        break
    print("Passwords don't match or too short (min 6 chars). Try again.")

h = generate_password_hash(pwd)

with sqlite3.connect(DB_PATH) as conn:
    conn.row_factory = sqlite3.Row
    existing = conn.execute("SELECT id FROM users WHERE username=? OR role='admin'", (username,)).fetchone()
    if existing:
        conn.execute("UPDATE users SET username=?, email=?, password_hash=?, role='admin' WHERE id=?",
                     (username, email, h, existing['id']))
        print(f"\n✅ Admin account updated: {username}")
    else:
        conn.execute("INSERT INTO users(username,email,password_hash,full_name,role) VALUES(?,?,?,?,?)",
                     (username, email, h, 'Administrator', 'admin'))
        print(f"\n✅ Admin account created: {username}")
    conn.commit()

print("Done. You can now log in at /admin/login")
