"""Phase 6: fleet-wide read endpoints — `GET /fleet/overview` and `GET /fleet/rankings`.

Both read the same per-UAV snapshot; `rankings` just sorts it by urgency. Neither
mutates anything — control, injection and mission start/end all stay in `control.py`,
now `uav_id`-aware. `build_fleet_overview()` is also what the `/ws/fleet-overview`
broadcast loop calls every tick, so the REST and WebSocket views can never disagree.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request

from app.core.fleet_registry import FleetEntry, FleetRegistry, lifecycle_engine_id
from app.db.lifecycle_repository import lifecycle_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fleet", tags=["fleet"])

#: How often `/ws/fleet-overview` pushes a fresh summary. A fleet roster card does not
#: need 10 Hz — 1.5 s keeps the UI current without re-running the sort on every tick.
FLEET_OVERVIEW_BROADCAST_INTERVAL_S = 1.5

#: Lower = more urgent. Mirrors the GO/CAUTION/NO-GO and RTB-SAFE/CAUTION/AT-RISK
#: ladders — a UAV that cannot even be trusted to get home outranks one that merely
#: cannot finish the plan.
_MISSION_URGENCY = {"NO-GO": 0, "CAUTION": 1, "GO": 2}
_RECOVERY_URGENCY = {"RTB-AT-RISK": 0, "RTB-CAUTION": 1, "RTB-SAFE": 2}


def _uav_snapshot(entry: FleetEntry) -> dict[str, Any]:
    frame = entry.sim.get_latest()
    lifecycle = lifecycle_repository.get_current_lifecycle(
        engine_id=lifecycle_engine_id(entry.uav_id)
    )

    worst_subsystem: str | None = None
    worst_subsystem_score: float | None = None
    overall_health: float | None = None
    rul_minutes: float | None = None
    mission_recommendation: str | None = None
    recovery_recommendation: str | None = None
    active_fault_count = 0

    if frame is not None:
        overall_health = frame.health.overall_score
        subsystem_scores = frame.health.subsystem_scores.model_dump(exclude_none=True)
        if subsystem_scores:
            worst_subsystem = min(subsystem_scores, key=lambda k: subsystem_scores[k])
            worst_subsystem_score = subsystem_scores[worst_subsystem]
        rul_minutes = frame.rul_minutes
        mission_recommendation = frame.mission_reliability.recommendation
        recovery_recommendation = frame.recovery_reliability.recommendation
        active_fault_count = len(frame.active_faults)

    return {
        "uav_id": entry.uav_id,
        "status": entry.status,
        "overall_health": overall_health,
        "worst_subsystem": worst_subsystem,
        "worst_subsystem_score": worst_subsystem_score,
        "rul_minutes": rul_minutes,
        "mission_reliability_recommendation": mission_recommendation,
        "recovery_reliability_recommendation": recovery_recommendation,
        "active_fault_count": active_fault_count,
        "total_operating_hours": lifecycle["total_operating_hours"],
    }


def _urgency_key(snapshot: dict[str, Any]) -> tuple:
    mission_rank = _MISSION_URGENCY.get(
        snapshot["mission_reliability_recommendation"], -1
    )
    recovery_rank = _RECOVERY_URGENCY.get(
        snapshot["recovery_reliability_recommendation"], -1
    )
    # A UAV with no telemetry yet (rank -1, before its first tick) sorts first —
    # "unknown" is at least as worth a look as "known bad" — then by declared
    # recommendation severity, then by raw health ascending (lower = worse) as the
    # tiebreaker within a recommendation band.
    health = snapshot["overall_health"]
    health_rank = health if health is not None else -1.0
    return (mission_rank, recovery_rank, health_rank)


def build_fleet_overview(fleet: FleetRegistry) -> list[dict[str, Any]]:
    return [_uav_snapshot(entry) for entry in fleet]


@router.get("/overview")
def get_fleet_overview(request: Request) -> list[dict[str, Any]]:
    fleet: FleetRegistry = request.app.state.fleet
    return build_fleet_overview(fleet)


@router.get("/rankings")
def get_fleet_rankings(request: Request) -> list[dict[str, Any]]:
    fleet: FleetRegistry = request.app.state.fleet
    overview = build_fleet_overview(fleet)
    return sorted(overview, key=_urgency_key)


async def run_fleet_overview_broadcast(app) -> None:
    """Background task: push the fleet-wide summary (ranked, same as `GET
    /fleet/rankings`) to every `/ws/fleet-overview` subscriber every ~1.5 s.

    Separate from `run_simulation`'s per-UAV telemetry loop — a fleet roster card
    wants a slow, ranked summary, not 10 Hz per-UAV frames, and the two sockets
    already had different consumers (a roster grid vs. a live gauge), so keeping this
    a second loop rather than piggy-backing on the first keeps a slow subscriber from
    ever throttling the telemetry stream."""
    from app.api import ws_telemetry

    fleet: FleetRegistry = app.state.fleet
    while True:
        await asyncio.sleep(FLEET_OVERVIEW_BROADCAST_INTERVAL_S)
        if not ws_telemetry.fleet_manager.active:
            continue
        try:
            rankings = sorted(build_fleet_overview(fleet), key=_urgency_key)
        except Exception:
            logger.exception("Failed to build fleet overview for broadcast")
            continue
        await ws_telemetry.fleet_manager.broadcast_json({"fleet": rankings})
