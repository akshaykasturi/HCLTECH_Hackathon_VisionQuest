"""Put the demo users from DEMO_USERS (.env) into the database, with hashed passwords.

Run from the backend folder:
    .\\.venv\\Scripts\\python.exe scripts\\seed_users.py

DEMO_USERS format: name:password:role separated by commas, role = analyst or lead.
Safe to run again: existing users get the new password hash and role (repo.upsert_user).

Until db/repo.py provides upsert_user, this only checks DEMO_USERS and says so: login then reads
DEMO_USERS directly (app/auth/users.py), so the demo still works.
Prints usernames and roles only, never a password or a hash.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.security import hash_password  # noqa: E402
from app.auth.users import DemoUsersError, parse_demo_users  # noqa: E402
from app.common.env import load_dotenv  # noqa: E402
from app.common.trace import optional  # noqa: E402


def main() -> int:
    load_dotenv()
    try:
        users = parse_demo_users(os.getenv("DEMO_USERS", ""))
    except DemoUsersError as exc:
        print(f"Problem: {exc}")
        return 1
    if not users:
        print("DEMO_USERS is empty in backend/.env: add e.g. analyst1:<password>:analyst,lead1:<password>:lead")
        return 1

    upsert = optional("app.db.repo", "upsert_user")
    if upsert is None:
        print(f"DEMO_USERS is valid ({len(users)} users). db/repo.py has no upsert_user yet, so nothing was "
              "written: login reads DEMO_USERS directly until then.")
        for username, _, role in users:
            print(f"  {username} ({role})")
        return 0

    for username, password, role in users:
        upsert(username, hash_password(password), role)
        print(f"  saved {username} ({role})")
    print(f"Done: {len(users)} users in the database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
