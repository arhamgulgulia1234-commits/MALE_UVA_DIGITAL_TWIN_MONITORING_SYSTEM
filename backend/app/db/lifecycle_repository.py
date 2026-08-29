"""Engine life-cycle ledger — persists wear *across* missions, not within one.

Every other repository method in this package is scoped to a single mission: a
`Mission` row, its frames, its fault events. This one is scoped to the *engine* — the
thing that keeps existing after the mission that wore it down has ended. Before this
existed, `SimulationLoop.faults` was the only place engine wear lived, and it was a
plain in-process object: a backend restart, or simply never having wired mission
boundaries to it, meant every mission effectively started from a factory-fresh engine
no matter how much wear the fleet had actually accumulated. `app/api/control.py`'s
mission-start and mission-end handlers are what close that loop — see the comments
there for exactly when this ledger is read from and written to.

`current_wear_state` and `cumulative_fault_event_counts` are always returned with every
key in `FAULT_TYPES` present (defaulting to `0.0` / `0`), even if the stored JSON is
missing one — the physics model's fault list can only grow, and a UI or a caller
iterating this dict should not have to guard against a key that simply has not been
written yet.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import DEFAULT_ENGINE_ID, EngineLifecycle, MaintenanceAction
from app.db.session import get_session
from app.physics.fault_models import FAULT_TYPES

logger = logging.getLogger(__name__)


def _filled_wear_state(raw: dict[str, Any] | None) -> dict[str, float]:
    raw = raw or {}
    # Rounded to 6 places: severities only ever arrive from float arithmetic (a ramp
    # target, a subtraction in apply_maintenance_action), and without this a value like
    # "0.4 minus 0.25" comes back as 0.15000000000000002 in every API response and every
    # UI that renders it.
    return {ft: round(float(raw.get(ft, 0.0)), 6) for ft in FAULT_TYPES}


def _filled_event_counts(raw: dict[str, Any] | None) -> dict[str, int]:
    raw = raw or {}
    return {ft: int(raw.get(ft, 0)) for ft in FAULT_TYPES}


class LifecycleRepository:
    def _get_or_create(self, session, engine_id: str) -> EngineLifecycle:
        row = session.get(EngineLifecycle, engine_id)
        if row is None:
            row = EngineLifecycle(
                engine_id=engine_id,
                total_operating_hours=0.0,
                cumulative_fault_event_counts={},
                current_wear_state={},
            )
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    @staticmethod
    def _to_dict(row: EngineLifecycle) -> dict[str, Any]:
        return {
            "engine_id": row.engine_id,
            "total_operating_hours": round(row.total_operating_hours, 3),
            "current_wear_state": _filled_wear_state(row.current_wear_state),
            "cumulative_fault_event_counts": _filled_event_counts(
                row.cumulative_fault_event_counts
            ),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    # ---- reads --------------------------------------------------------------

    def get_current_lifecycle(self, engine_id: str = DEFAULT_ENGINE_ID) -> dict[str, Any]:
        with get_session() as session:
            row = self._get_or_create(session, engine_id)
            return self._to_dict(row)

    def list_maintenance_actions(
        self, engine_id: str = DEFAULT_ENGINE_ID, limit: int = 100
    ) -> list[dict[str, Any]]:
        with get_session() as session:
            actions = session.scalars(
                select(MaintenanceAction)
                .where(MaintenanceAction.engine_id == engine_id)
                .order_by(MaintenanceAction.performed_at.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": a.id,
                    "fault_type": a.fault_type,
                    "description": a.description,
                    "performed_at": a.performed_at.isoformat() if a.performed_at else None,
                    "wear_reset_amount": a.wear_reset_amount,
                }
                for a in actions
            ]

    # ---- mutations ------------------------------------------------------------

    def increment_operating_hours(
        self, hours: float, engine_id: str = DEFAULT_ENGINE_ID
    ) -> dict[str, Any]:
        """Add `hours` of simulated running time to the engine's lifetime total.

        Called once per mission, from `POST /control/mission/end`, with the mission's
        recorded duration. Negative input is rejected rather than silently clamped — a
        caller passing a negative duration has a bug worth surfacing, not a wear
        reversal worth allowing (that is what a maintenance action is for)."""
        if hours < 0:
            raise ValueError(f"hours must be >= 0, got {hours}")
        with get_session() as session:
            row = self._get_or_create(session, engine_id)
            row.total_operating_hours += hours
            session.commit()
            session.refresh(row)
            return self._to_dict(row)

    def record_fault_event(
        self, fault_type: str, engine_id: str = DEFAULT_ENGINE_ID
    ) -> dict[str, Any]:
        """Count one more mission in which `fault_type` was active on this engine.

        This is the engine's *lifetime* tally — how many missions this fault has shown
        up in, ever — which is a different number from the per-mission `FaultEvent`
        rows: those record exactly when a fault started and cleared within one mission's
        telemetry; this is the one counter that survives past that mission's own
        history. Called once per distinct fault type per mission, from mission end."""
        if fault_type not in FAULT_TYPES:
            raise ValueError(f"unknown fault type: {fault_type}")
        with get_session() as session:
            row = self._get_or_create(session, engine_id)
            counts = _filled_event_counts(row.cumulative_fault_event_counts)
            counts[fault_type] += 1
            row.cumulative_fault_event_counts = counts
            session.commit()
            session.refresh(row)
            return self._to_dict(row)

    def set_wear_state(
        self, wear_state: dict[str, float], engine_id: str = DEFAULT_ENGINE_ID
    ) -> dict[str, Any]:
        """Overwrite the persisted wear state — the mission's final `FaultState`
        severities, written back wholesale so degradation (and, via a maintenance
        action, recovery) carries forward to the next mission's seed. Unlisted fault
        types are left at whatever they already were, not zeroed, so a partial snapshot
        never wipes out wear this call was not told about."""
        clamped = {
            ft: round(max(0.0, min(1.0, float(v))), 6)
            for ft, v in wear_state.items()
            if ft in FAULT_TYPES
        }
        with get_session() as session:
            row = self._get_or_create(session, engine_id)
            current = _filled_wear_state(row.current_wear_state)
            current.update(clamped)
            row.current_wear_state = current
            session.commit()
            session.refresh(row)
            return self._to_dict(row)

    def apply_maintenance_action(
        self,
        fault_type: str,
        description: str,
        reset_amount: float,
        engine_id: str = DEFAULT_ENGINE_ID,
    ) -> dict[str, Any]:
        """Reduce `current_wear_state[fault_type]` by `reset_amount` (clamped at 0) and
        log the action.

        This touches only the persisted ledger, never the live running engine — the
        same seam `current_wear_state` already has with the live `FaultState`: it is
        read into the live engine at the next mission's start, not injected into
        whatever mission is running right now. Performing maintenance mid-flight on an
        engine that has not landed is not a thing this simulates."""
        if fault_type not in FAULT_TYPES:
            raise ValueError(f"unknown fault type: {fault_type}")
        reset_amount = max(0.0, min(1.0, float(reset_amount)))
        with get_session() as session:
            row = self._get_or_create(session, engine_id)
            current = _filled_wear_state(row.current_wear_state)
            current[fault_type] = round(max(0.0, current[fault_type] - reset_amount), 6)
            row.current_wear_state = current
            session.add(
                MaintenanceAction(
                    engine_id=engine_id,
                    fault_type=fault_type,
                    description=description,
                    performed_at=datetime.now(timezone.utc),
                    wear_reset_amount=reset_amount,
                )
            )
            session.commit()
            session.refresh(row)
            return self._to_dict(row)


#: Single shared instance — same pattern as `app.db.repository.repository`.
lifecycle_repository = LifecycleRepository()
