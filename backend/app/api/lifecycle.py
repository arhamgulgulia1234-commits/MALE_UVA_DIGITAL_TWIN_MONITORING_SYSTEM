"""Engine life-cycle endpoints — `GET /lifecycle/summary`, `POST /lifecycle/maintenance-action`.

This is the one Phase 5 surface that looks *across* missions rather than into one. Every
other reporting endpoint in this codebase (`/control/missions/{id}/report`, the Test Bench
scenario history) answers "how did this one run go?"; this answers "what has this engine
been through, cumulatively, ever?" — total operating hours, current wear per fault type,
how many missions each fault has shown up in, and the health-score trend mission over
mission.

Read-only with respect to the live simulation, in the same sense the Test Bench routers
are: `/lifecycle/summary` only reads the persisted ledger and past mission reports, and
`/lifecycle/maintenance-action` only ever writes to that ledger. Neither touches whatever
mission is recording right now — the ledger is consulted by the live engine exactly once,
at `POST /control/mission/start` (see app/api/control.py), which is the one seam where
persisted wear becomes live wear.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.audit import record
from app.auth.deps import CurrentUser, get_current_user, require_min_role
from app.core.fleet_registry import lifecycle_engine_id
from app.core.models import MaintenanceActionRequest
from app.core.uav_ids import DEFAULT_UAV_ID
from app.db.lifecycle_repository import lifecycle_repository
from app.db.models import ROLE_MAINTENANCE_ENGINEER
from app.db.repository import repository
from app.physics.fault_models import FAULT_TYPES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])


def _mission_health_trend(uav_id: str) -> list[dict]:
    """End-of-mission health score for every mission that has a report, oldest first.

    `list_missions()` already returns newest-first (the natural order for a history
    list), so this reverses it — a trend chart reads left-to-right as time moving
    forward, and a caller should not have to know the source order to plot it correctly.
    """
    missions = repository.list_missions(uav_id=uav_id)
    trend: list[dict] = []
    for m in reversed(missions):
        if not m.get("has_report"):
            continue
        report = repository.get_mission_report(m["id"])
        if not report:
            continue
        health = report.get("health") or {}
        end_score = health.get("end")
        if end_score is None:
            continue
        trend.append(
            {
                "mission_id": m["id"],
                "started_at": m.get("started_at"),
                "ended_at": m.get("ended_at"),
                "profile_name": m.get("mission_profile_name"),
                "health_score": round(float(end_score), 1),
                "worst_subsystem": health.get("worst_subsystem"),
            }
        )
    return trend


@router.get("/summary", dependencies=[Depends(get_current_user)])
async def lifecycle_summary(uav_id: str = DEFAULT_UAV_ID) -> dict:
    """Total hours, current wear per fault type, cumulative fault-event counts, the
    health-score trend across mission history, and the maintenance log — for one UAV.

    Phase 6: keyed by `lifecycle_engine_id(uav_id)`, not `uav_id` directly — UAV-01
    reuses the pre-existing "primary" ledger row, see fleet_registry.py."""
    engine_id = lifecycle_engine_id(uav_id)
    lifecycle = lifecycle_repository.get_current_lifecycle(engine_id=engine_id)
    return {
        **lifecycle,
        "uav_id": uav_id,
        "mission_health_trend": _mission_health_trend(uav_id),
        "maintenance_actions": lifecycle_repository.list_maintenance_actions(
            engine_id=engine_id
        ),
        "fault_types": list(FAULT_TYPES),
    }


@router.post(
    "/maintenance-action",
    dependencies=[Depends(require_min_role(ROLE_MAINTENANCE_ENGINEER))],
)
async def log_maintenance_action(
    req: MaintenanceActionRequest,
    uav_id: str = DEFAULT_UAV_ID,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Record a maintenance action against the persisted wear ledger.

    Takes effect at the *next* mission's start, not on whatever mission is running right
    now — see `lifecycle_repository.apply_maintenance_action` for why that seam is
    deliberate."""
    try:
        lifecycle = lifecycle_repository.apply_maintenance_action(
            req.fault_type,
            req.description,
            req.reset_amount,
            engine_id=lifecycle_engine_id(uav_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record(
        user,
        "maintenance_action",
        {"uav_id": uav_id, "fault_type": req.fault_type, "reset_amount": req.reset_amount},
    )
    return {"ok": True, "lifecycle": lifecycle}
