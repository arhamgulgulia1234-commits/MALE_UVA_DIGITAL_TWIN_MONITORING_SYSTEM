"""SQLAlchemy tables for mission recording.

Telemetry frames are stored as a JSON blob rather than normalised into columns. That is a
deliberate trade: the TelemetryFrame schema is still growing (Phase 3 added nine fields to
it), and a wide table would need a migration every time. Replay and reporting both read
whole frames anyway, so there is nothing to gain from column-per-field here. Only the
fields we actually query on — mission id and timestamp — are promoted to real columns and
indexed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mission_profile_name: Mapped[str] = mapped_column(String(120), default="standard")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Mission report JSON, written on mission end (app/ml/mission_report.py).
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    frames: Mapped[list["TelemetryFrameRow"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    fault_events: Mapped[list["FaultEvent"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )


class TelemetryFrameRow(Base):
    __tablename__ = "telemetry_frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    #: The complete TelemetryFrame, exactly as broadcast over the WebSocket.
    frame: Mapped[dict] = mapped_column(JSON, nullable=False)

    mission: Mapped[Mission] = relationship(back_populates="frames")


# Replay reads a whole mission in timestamp order — index accordingly.
Index("ix_frames_mission_ts", TelemetryFrameRow.mission_id, TelemetryFrameRow.timestamp)


class FaultEvent(Base):
    __tablename__ = "fault_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    fault_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[float] = mapped_column(Float, nullable=False)
    cleared_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Phase 3: whether this was injected as a physical fault or a sensor fault.
    is_sensor_fault: Mapped[bool] = mapped_column(Integer, default=0)
    #: Phase 3: the classifier's verdict and its top contributing features.
    predicted_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    classifier_explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    mission: Mapped[Mission] = relationship(back_populates="fault_events")


Index("ix_fault_events_mission", FaultEvent.mission_id)
