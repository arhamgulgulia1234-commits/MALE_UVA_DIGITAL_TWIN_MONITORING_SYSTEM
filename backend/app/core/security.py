"""Bearer-token authentication for telemetry and control.

Scope, stated honestly: this is a **single shared secret**, adequate for a hackathon
demo and for keeping a lab bench honest. It is deliberately not presented as
defence-grade. `docs/deployment-roadmap.md` sets out what a real deployment needs —
mTLS between the air vehicle and the ground station, per-airframe identities, signed
frames, key rotation, and an auditable command channel. The value of having this here is
that the *seams* exist: every control mutation and the telemetry socket already pass
through one choke point, so replacing the check with real identity handling later is a
contained change rather than a rewrite.

Auth is disabled by default (`TELEMETRY_AUTH_ENABLED=false`) so the demo runs without
setup friction. Set a token and enable it to turn enforcement on:

    TELEMETRY_AUTH_ENABLED=true
    TELEMETRY_TOKEN=<shared-secret>

Clients then send `Authorization: Bearer <token>` on REST calls. Browsers cannot set
headers on a WebSocket handshake, so the socket also accepts `?token=<token>` — a real
deployment would use a short-lived ticket issued over the authenticated REST channel
instead, because query strings land in logs.
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)


def auth_enabled() -> bool:
    return os.getenv("TELEMETRY_AUTH_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def expected_token() -> str:
    return os.getenv("TELEMETRY_TOKEN", "")


def _token_matches(candidate: str) -> bool:
    expected = expected_token()
    if not expected:
        logger.error(
            "TELEMETRY_AUTH_ENABLED is true but TELEMETRY_TOKEN is empty — "
            "refusing all requests rather than allowing everything through."
        )
        return False
    # Constant-time compare so a timing side channel cannot recover the token.
    return hmac.compare_digest(candidate, expected)


def extract_bearer(header_value: str | None) -> str | None:
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


async def require_token(request: Request) -> None:
    """FastAPI dependency guarding the /control/* routes."""
    if not auth_enabled():
        return
    token = extract_bearer(request.headers.get("Authorization"))
    if token is None:
        token = request.query_params.get("token")
    if token is None or not _token_matches(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def websocket_token_ok(header_value: str | None, query_token: str | None) -> bool:
    """Handshake check for /ws/telemetry."""
    if not auth_enabled():
        return True
    token = extract_bearer(header_value) or query_token
    return bool(token) and _token_matches(token)
