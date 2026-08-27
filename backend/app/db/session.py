"""SQLite engine and session factory.

The database file lives at backend/data/telemetry.db (override with DATABASE_URL).
`init_db()` creates the schema if it is missing, so there is no separate migration step
to run — starting the backend is enough.

Two SQLite specifics matter here:
  * `check_same_thread=False`, because FastAPI may touch the session from a different
    thread than the one that created it.
  * WAL journal mode, so the 10 Hz write stream from a recording mission does not block
    a concurrent read (the replay engine or a report query).
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DB_PATH = DATA_DIR / "telemetry.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    # WAL lets the recorder write while a replay or report reads.
    cursor.execute("PRAGMA journal_mode=WAL")
    # NORMAL is the right durability trade for telemetry we can regenerate.
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables if they do not exist. Safe to call on every startup."""
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
