"""One-line audit call for route handlers — thin wrapper so call sites read as
`record(user, "fault.inject", {...})` instead of importing the repository directly."""
from __future__ import annotations

from typing import Any

from app.auth.deps import CurrentUser
from app.db.audit_repository import audit_repository


def record(user: CurrentUser, action: str, parameters: dict[str, Any] | None = None) -> None:
    audit_repository.log(username=user.username, role=user.role, action=action, parameters=parameters or {})
