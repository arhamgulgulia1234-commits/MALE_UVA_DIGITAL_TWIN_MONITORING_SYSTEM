"""App-wide settings, loaded from environment variables (see .env.example)."""
from __future__ import annotations

import os


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    host: str = os.getenv("BACKEND_HOST", "0.0.0.0")
    port: int = int(os.getenv("BACKEND_PORT", "8000"))
    # Comma-separated. Stripped and de-blanked because a pasted value like
    # "https://a.vercel.app, https://b.vercel.app" would otherwise register the second
    # origin as " https://b.vercel.app" and silently never match — a CORS failure that
    # looks identical to a missing entry.
    cors_origins: list[str] = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ]
    sim_tick_hz: float = float(os.getenv("SIM_TICK_HZ", "10"))
    tick_seconds: float = 1.0 / sim_tick_hz

    #: Fall back to the Phase 1 scripted generator instead of the physics simulation.
    #: Demo-safety escape hatch only — the real path is the physics model.
    use_mock: bool = _env_bool("USE_MOCK", False)

    #: True (default) = training/demo posture: fault-injection endpoints work for
    #: administrators. False = a real deployment posture: they are disabled outright,
    #: for every role — see app/auth/deps.py::require_demo_mode. Injecting a fault into
    #: a real engine has no business case outside training, so this is not a role check,
    #: it is a hard kill switch independent of who is asking.
    demo_mode: bool = _env_bool("DEMO_MODE", True)

    #: HMAC secret for the JWTs app/auth/jwt_lite.py issues. The insecure default is
    #: fine for the demo (see docs/deployment-roadmap.md) but must be overridden — a
    #: real deployment's tokens are only as good as this secret.
    jwt_secret: str = os.getenv("JWT_SECRET", "vayudrishti-dev-insecure-change-me")
    jwt_expires_minutes: float = float(os.getenv("JWT_EXPIRES_MINUTES", "60"))


settings = Settings()
