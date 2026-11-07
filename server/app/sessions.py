"""Sign an account out everywhere, for example when a session cookie may have been copied.

    python -m app.sessions revoke <username>     # one account
    python -m app.sessions revoke --all          # every account (the guest too)

Each user has a session version that every cookie carries; raising it makes all older cookies invalid.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.auth import revoke_sessions
from app.db import SessionLocal
from app.models import AuditEvent, User


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "revoke":
        print(__doc__)
        return 2
    target = argv[2]
    with SessionLocal() as db:
        query = select(User) if target == "--all" else select(User).where(User.username == target)
        users = db.scalars(query).all()
        if not users:
            print(f"No account named {target}.")
            return 1
        for user in users:
            revoke_sessions(user)
            db.add(AuditEvent(username=user.username, action="sessions_revoked", detail="python -m app.sessions"))
        db.commit()
        print("Signed out everywhere: " + ", ".join(sorted(user.username for user in users)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
