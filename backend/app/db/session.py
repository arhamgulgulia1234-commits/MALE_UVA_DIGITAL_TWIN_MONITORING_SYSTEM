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


#: Phase 6: columns added to an already-shipped table, keyed by (table, column) so
#: `_ensure_column` knows what to backfill. Every other schema change in this project has
#: been a brand-new table, which `create_all()` handles for free; this is the one case
#: where `create_all()` cannot help, because it only creates *missing* tables and never
#: alters an existing one's columns. `_ensure_column` is a deliberately narrow, additive
#: substitute for a real migration framework (no Alembic in this project) — safe here
#: only because SQLite's `ALTER TABLE ADD COLUMN` is a metadata-only change that never
#: rewrites existing rows.
_COLUMN_MIGRATIONS: list[tuple[str, str, str, str]] = [
    # (table, column, sql_type, default_sql)
    ("missions", "uav_id", "VARCHAR(40)", "'UAV-01'"),
]


def _ensure_column(table: str, column: str, sql_type: str, default_sql: str) -> None:
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if table not in tables:
            return  # create_all() will build it with the column already present
        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        if column in existing:
            return
        conn.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN {column} {sql_type} "
            f"NOT NULL DEFAULT {default_sql}"
        )
        conn.commit()


def init_db() -> None:
    """Create tables if they do not exist, then backfill any columns added to a table
    that already existed. Safe to call on every startup — both steps are idempotent."""
    Base.metadata.create_all(engine)
    for migration in _COLUMN_MIGRATIONS:
        _ensure_column(*migration)


def get_session() -> Session:
    return SessionLocal()
