"""Seeds the three default demo accounts, one per role, on first startup only (a
non-empty `users` table means a real deployment has already replaced them). Passwords
are documented in docs/deployment-roadmap.md — nowhere in frontend code, and this is
the only backend module that ever sees them in plaintext, at seed time."""
from __future__ import annotations

import logging
import os

from sqlalchemy import select

from app.auth.passwords import hash_password
from app.db.models import ROLE_ADMINISTRATOR, ROLE_MAINTENANCE_ENGINEER, ROLE_OPERATOR, User
from app.db.session import get_session

logger = logging.getLogger(__name__)

#: (username, role, env var carrying the password, insecure demo default).
_DEFAULT_ACCOUNTS: list[tuple[str, str, str, str]] = [
    ("operator1", ROLE_OPERATOR, "SEED_OPERATOR_PASSWORD", "operator-demo-pw"),
    ("engineer1", ROLE_MAINTENANCE_ENGINEER, "SEED_ENGINEER_PASSWORD", "engineer-demo-pw"),
    ("admin1", ROLE_ADMINISTRATOR, "SEED_ADMIN_PASSWORD", "admin-demo-pw"),
]


def seed_default_users() -> None:
    session = get_session()
    try:
        if session.execute(select(User.id).limit(1)).first() is not None:
            return
        for username, role, env_var, default_pw in _DEFAULT_ACCOUNTS:
            password = os.getenv(env_var, default_pw)
            session.add(User(username=username, hashed_password=hash_password(password), role=role))
        session.commit()
        logger.info(
            "Seeded 3 default accounts (operator1/engineer1/admin1) — "
            "see docs/deployment-roadmap.md for credentials."
        )
    finally:
        session.close()
