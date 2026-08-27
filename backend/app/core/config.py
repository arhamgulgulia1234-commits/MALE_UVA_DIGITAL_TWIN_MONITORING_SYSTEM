"""App-wide settings, loaded from environment variables (see .env.example)."""
from __future__ import annotations

import os


class Settings:
    host: str = os.getenv("BACKEND_HOST", "0.0.0.0")
    port: int = int(os.getenv("BACKEND_PORT", "8000"))
    cors_origins: list[str] = os.getenv(
        "CORS_ORIGINS", "http://localhost:3000"
    ).split(",")
    sim_tick_hz: float = float(os.getenv("SIM_TICK_HZ", "10"))
    tick_seconds: float = 1.0 / sim_tick_hz


settings = Settings()
