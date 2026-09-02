"""GET /audit-log — administrator only, paginated."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.deps import require_role
from app.db.audit_repository import audit_repository
from app.db.models import ROLE_ADMINISTRATOR

router = APIRouter(tags=["audit"])


@router.get("/audit-log", dependencies=[Depends(require_role(ROLE_ADMINISTRATOR))])
async def get_audit_log(limit: int = 50, offset: int = 0) -> dict:
    return audit_repository.list(limit=limit, offset=offset)
