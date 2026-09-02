"""POST /auth/login — the one unauthenticated route in this package."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.audit import record
from app.auth.deps import CurrentUser
from app.auth.jwt_lite import encode
from app.auth.passwords import verify_password
from app.core.config import settings
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(req: LoginRequest) -> dict:
    session = get_session()
    try:
        user = session.execute(select(User).where(User.username == req.username)).scalar_one_or_none()
    finally:
        session.close()

    if user is None or not verify_password(req.password, user.hashed_password):
        raise HTTPException(401, "Invalid username or password")

    expires_in = settings.jwt_expires_minutes * 60
    token = encode({"sub": user.username, "role": user.role}, settings.jwt_secret, expires_in)
    record(CurrentUser(username=user.username, role=user.role), "login")

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "username": user.username,
        "role": user.role,
        "demo_mode": settings.demo_mode,
    }
