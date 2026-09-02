"""Audit log persistence — every control-affecting action, who did it, and with what
parameters. See app/db/models.py::AuditLogEntry for the schema and its append-only
intent."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.db.models import AuditLogEntry
from app.db.session import get_session


class AuditRepository:
    def log(self, *, username: str, role: str, action: str, parameters: dict[str, Any]) -> None:
        session = get_session()
        try:
            session.add(
                AuditLogEntry(username=username, role=role, action=action, parameters=parameters)
            )
            session.commit()
        finally:
            session.close()

    def list(self, *, limit: int = 50, offset: int = 0) -> dict:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        session = get_session()
        try:
            total = session.execute(select(func.count()).select_from(AuditLogEntry)).scalar_one()
            rows = (
                session.execute(
                    select(AuditLogEntry)
                    .order_by(AuditLogEntry.timestamp.desc())
                    .offset(offset)
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            entries = [
                {
                    "id": r.id,
                    "timestamp": r.timestamp.isoformat(),
                    "username": r.username,
                    "role": r.role,
                    "action": r.action,
                    "parameters": r.parameters,
                }
                for r in rows
            ]
            return {"entries": entries, "total": total, "limit": limit, "offset": offset}
        finally:
            session.close()


#: Single shared instance — same pattern as app.db.repository.repository.
audit_repository = AuditRepository()
