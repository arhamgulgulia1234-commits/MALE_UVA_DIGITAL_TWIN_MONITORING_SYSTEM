"""Repository layer — the only place the rest of the app touches the database.

Frames arrive at 10 Hz, so `save_frame()` buffers and flushes in batches rather than
committing per frame; a per-frame commit would fsync ten times a second for data that is
not individually precious. `flush()` is called on mission end and whenever the buffer
fills.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from sqlalchemy import select

from app.db.models import FaultEvent, Mission, ScenarioRun, TelemetryFrameRow
from app.db.session import get_session

logger = logging.getLogger(__name__)

#: Frames buffered before a commit. At 10 Hz this is a commit every ~5 s.
FRAME_BATCH_SIZE = 50


class MissionRepository:
    def __init__(self) -> None:
        self._frame_buffer: list[TelemetryFrameRow] = []

    # ---- missions ---------------------------------------------------------

    def start_mission(self, profile_name: str, notes: str | None = None) -> int:
        with get_session() as session:
            mission = Mission(mission_profile_name=profile_name, notes=notes)
            session.add(mission)
            session.commit()
            logger.info("Started mission %s (profile=%s)", mission.id, profile_name)
            return mission.id

    def end_mission(self, mission_id: int, report: dict | None = None) -> None:
        from datetime import datetime, timezone

        self.flush()
        with get_session() as session:
            mission = session.get(Mission, mission_id)
            if mission is None:
                return
            mission.ended_at = datetime.now(timezone.utc)
            if report is not None:
                mission.report = report
            session.commit()
            logger.info("Ended mission %s", mission_id)

    def list_missions(self) -> list[dict[str, Any]]:
        with get_session() as session:
            missions = session.scalars(
                select(Mission).order_by(Mission.id.desc())
            ).all()
            out: list[dict[str, Any]] = []
            for m in missions:
                frame_count = session.scalar(
                    select(TelemetryFrameRow.id)
                    .where(TelemetryFrameRow.mission_id == m.id)
                    .order_by(TelemetryFrameRow.id.desc())
                    .limit(1)
                )
                n_frames = len(m.frames) if frame_count is None else None
                out.append(
                    {
                        "id": m.id,
                        "started_at": m.started_at.isoformat() if m.started_at else None,
                        "ended_at": m.ended_at.isoformat() if m.ended_at else None,
                        "mission_profile_name": m.mission_profile_name,
                        "notes": m.notes,
                        "has_report": m.report is not None,
                        "frame_count": n_frames
                        if n_frames is not None
                        else self.count_frames(m.id),
                    }
                )
            return out

    def count_frames(self, mission_id: int) -> int:
        from sqlalchemy import func

        with get_session() as session:
            return int(
                session.scalar(
                    select(func.count(TelemetryFrameRow.id)).where(
                        TelemetryFrameRow.mission_id == mission_id
                    )
                )
                or 0
            )

    def get_mission(self, mission_id: int) -> dict[str, Any] | None:
        with get_session() as session:
            m = session.get(Mission, mission_id)
            if m is None:
                return None
            return {
                "id": m.id,
                "started_at": m.started_at.isoformat() if m.started_at else None,
                "ended_at": m.ended_at.isoformat() if m.ended_at else None,
                "mission_profile_name": m.mission_profile_name,
                "notes": m.notes,
                "report": m.report,
            }

    def get_mission_report(self, mission_id: int) -> dict | None:
        with get_session() as session:
            m = session.get(Mission, mission_id)
            return m.report if m else None

    # ---- frames -----------------------------------------------------------

    def save_frame(self, mission_id: int, frame: dict[str, Any]) -> None:
        """Buffer a frame; commits in batches of FRAME_BATCH_SIZE."""
        self._frame_buffer.append(
            TelemetryFrameRow(
                mission_id=mission_id,
                timestamp=float(frame.get("timestamp", 0.0)),
                frame=frame,
            )
        )
        if len(self._frame_buffer) >= FRAME_BATCH_SIZE:
            self.flush()

    def flush(self) -> None:
        if not self._frame_buffer:
            return
        buffered, self._frame_buffer = self._frame_buffer, []
        try:
            with get_session() as session:
                session.add_all(buffered)
                session.commit()
        except Exception:
            logger.exception("Failed to flush %d telemetry frames", len(buffered))

    def get_mission_frames(self, mission_id: int) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = session.scalars(
                select(TelemetryFrameRow)
                .where(TelemetryFrameRow.mission_id == mission_id)
                .order_by(TelemetryFrameRow.timestamp)
            ).all()
            return [row.frame for row in rows]

    def iter_mission_frames(self, mission_id: int) -> Iterable[dict[str, Any]]:
        return iter(self.get_mission_frames(mission_id))

    # ---- fault events -----------------------------------------------------

    def save_fault_event(
        self,
        mission_id: int,
        fault_type: str,
        severity: float,
        started_at: float,
        *,
        is_sensor_fault: bool = False,
        predicted_source: str | None = None,
        classifier_explanation: list[dict] | None = None,
    ) -> int:
        with get_session() as session:
            event = FaultEvent(
                mission_id=mission_id,
                fault_type=fault_type,
                severity=severity,
                started_at=started_at,
                is_sensor_fault=1 if is_sensor_fault else 0,
                predicted_source=predicted_source,
                classifier_explanation=(
                    {"features": classifier_explanation}
                    if classifier_explanation
                    else None
                ),
            )
            session.add(event)
            session.commit()
            return event.id

    def close_fault_event(
        self, mission_id: int, fault_type: str, cleared_at: float
    ) -> None:
        """Mark the most recent open event of this type as cleared."""
        with get_session() as session:
            event = session.scalars(
                select(FaultEvent)
                .where(
                    FaultEvent.mission_id == mission_id,
                    FaultEvent.fault_type == fault_type,
                    FaultEvent.cleared_at.is_(None),
                )
                .order_by(FaultEvent.started_at.desc())
                .limit(1)
            ).first()
            if event is None:
                return
            event.cleared_at = cleared_at
            session.commit()

    def get_fault_events(self, mission_id: int) -> list[dict[str, Any]]:
        with get_session() as session:
            events = session.scalars(
                select(FaultEvent)
                .where(FaultEvent.mission_id == mission_id)
                .order_by(FaultEvent.started_at)
            ).all()
            return [
                {
                    "fault_type": e.fault_type,
                    "severity": e.severity,
                    "started_at": e.started_at,
                    "cleared_at": e.cleared_at,
                    "is_sensor_fault": bool(e.is_sensor_fault),
                    "predicted_source": e.predicted_source,
                    "classifier_explanation": e.classifier_explanation,
                }
                for e in events
            ]


    # ---- Phase 4: Test Bench scenario runs --------------------------------

    def save_scenario_run(
        self,
        *,
        label: str | None,
        altitude_m: float,
        ambient_temperature_c: float | None,
        duration_minutes: float,
        verdict: str,
        min_health_score: float | None,
        final_rul_minutes: float | None,
        worst_subsystem: str | None,
        compute_seconds: float | None,
        params: dict[str, Any],
        summary: dict[str, Any],
    ) -> int:
        """Record a what-if run. Committed immediately — one row per run, not a stream."""
        with get_session() as session:
            run = ScenarioRun(
                label=label,
                altitude_m=altitude_m,
                ambient_temperature_c=ambient_temperature_c,
                duration_minutes=duration_minutes,
                verdict=verdict,
                min_health_score=min_health_score,
                final_rul_minutes=final_rul_minutes,
                worst_subsystem=worst_subsystem,
                compute_seconds=compute_seconds,
                params=params,
                summary=summary,
            )
            session.add(run)
            session.commit()
            return run.id

    def list_scenario_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with get_session() as session:
            runs = session.scalars(
                select(ScenarioRun).order_by(ScenarioRun.id.desc()).limit(limit)
            ).all()
            return [self._scenario_row(r, include_summary=False) for r in runs]

    def get_scenario_run(self, run_id: int) -> dict[str, Any] | None:
        with get_session() as session:
            run = session.get(ScenarioRun, run_id)
            return self._scenario_row(run, include_summary=True) if run else None

    @staticmethod
    def _scenario_row(run: ScenarioRun, *, include_summary: bool) -> dict[str, Any]:
        summary = run.summary or {}
        row: dict[str, Any] = {
            "id": run.id,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "label": run.label,
            "altitude_m": run.altitude_m,
            "ambient_temperature_c": run.ambient_temperature_c,
            "duration_minutes": run.duration_minutes,
            "verdict": run.verdict,
            "min_health_score": run.min_health_score,
            "final_rul_minutes": run.final_rul_minutes,
            "worst_subsystem": run.worst_subsystem,
            "compute_seconds": run.compute_seconds,
            "headline": summary.get("headline"),
            "params": run.params,
        }
        if include_summary:
            row["summary"] = summary
        return row


#: Single shared repository — the simulation loop and API handlers all use this one.
repository = MissionRepository()
