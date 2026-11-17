"""Create the database tables and add the demo users from DEMO_USERS (.env), with hashed passwords.

Run from the backend folder:
    .\\.venv\\Scripts\\python.exe scripts\\seed_users.py

The same code runs at every server start (app/db/tables.init_db, first warm-up), so this script is only needed
to seed a database without starting the server. DEMO_USERS format: name:password:role separated by commas,
role = employee, analyst or lead.

Safe to run again: only usernames that are not in the users table yet are added. An existing user is never
overwritten (to change a password, delete that user's row first).
Prints usernames and roles only, never a password or a hash.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.users import DemoUsersError, parse_demo_users  # noqa: E402
from app.common.env import load_dotenv  # noqa: E402
from app.db.tables import init_db  # noqa: E402


def main() -> int:
    load_dotenv()
    try:
        users = parse_demo_users(os.getenv("DEMO_USERS", ""))
    except DemoUsersError as exc:
        print(f"Problem: {exc}")
        return 1
    if not users:
        print("DEMO_USERS is empty in backend/.env: add e.g. emp1:<password>:employee,analyst1:<password>:analyst,lead1:<password>:lead")
        return 1
    added = set(init_db())
    for username, _, role in users:
        print(f"  {username} ({role}): {'added' if username in added else 'already there, not changed'}")
    print(f"Done: {len(added)} added, {len(users) - len(added)} already in the database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
