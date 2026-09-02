"""FastAPI dependencies for JWT auth and role-based access control.

`get_current_user` is the one choke point every protected REST route depends on
(directly or via `require_role`/`require_min_role`) — the same shape the old
`require_token` had, so route wiring changes but the pattern does not.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.auth.jwt_lite import decode
from app.core.config import settings
from app.db.models import ROLE_ADMINISTRATOR, ROLE_MAINTENANCE_ENGINEER, ROLE_OPERATOR

#: Rank order, not lexical — a min-role check needs "is this role at least as senior."
ROLE_RANK: dict[str, int] = {
    ROLE_OPERATOR: 1,
    ROLE_MAINTENANCE_ENGINEER: 2,
    ROLE_ADMINISTRATOR: 3,
}


def extract_bearer(header_value: str | None) -> str | None:
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


class CurrentUser:
    def __init__(self, username: str, role: str):
        self.username = username
        self.role = role


async def get_current_user(request: Request) -> CurrentUser:
    """Any valid, unexpired JWT — no role requirement. Accepts `?token=` too, for the
    same reason the WebSocket handshake needs it (see websocket_user below): a browser
    cannot set headers on a WS handshake, and REST callers get the header path."""
    token = extract_bearer(request.headers.get("Authorization"))
    if token is None:
        token = request.query_params.get("token")
    claims = decode(token, settings.jwt_secret) if token else None
    if claims is None or "sub" not in claims or "role" not in claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid session token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return CurrentUser(username=claims["sub"], role=claims["role"])


def require_role(*roles: str):
    """Exact-set role gate — used where seniority does not imply access (fault
    injection is administrator-only, not "maintenance_engineer and above")."""

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role: {' or '.join(roles)}")
        return user

    return _dep


def require_min_role(min_role: str):
    """Seniority gate — maintenance actions need maintenance_engineer *or above*, and
    administrator is a strict superset of every lesser role."""

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get(min_role, 999):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role {min_role} or above")
        return user

    return _dep


async def require_demo_mode() -> None:
    """Hard kill switch for fault injection, independent of role. See
    Settings.demo_mode's docstring — this is not a permission, it is a posture."""
    if not settings.demo_mode:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Fault injection is disabled outside demo/training mode (DEMO_MODE=false)",
        )


async def websocket_user(header_value: str | None, query_token: str | None) -> CurrentUser | None:
    """Handshake check for /ws/telemetry and /ws/fleet-overview. Returns None (caller
    closes the socket) rather than raising — there is no HTTP response to attach an
    error to once a WebSocket handshake is underway."""
    token = extract_bearer(header_value) or query_token
    if not token:
        return None
    claims = decode(token, settings.jwt_secret)
    if claims is None or "sub" not in claims or "role" not in claims:
        return None
    return CurrentUser(username=claims["sub"], role=claims["role"])
