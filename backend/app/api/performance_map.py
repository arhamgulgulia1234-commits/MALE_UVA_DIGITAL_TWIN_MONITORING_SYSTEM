"""Engine performance maps — `GET /performance-maps`.

Serves the steady-state RPM x load surface computed by
`app/physics/performance_map.py`, plus the two things that turn a static reference chart
into an operational one: where the live engine is sitting right now, and where the Phase 4
optimizer says it should be.

Read-only, in the strong sense. Nothing here touches the live simulation's state, the
WebSocket, or the physics loop — `/live-point` takes the latest broadcast frame the loop
has *already* published and reports two of its numbers. A performance map is a property of
the engine's parameters, not of any particular flight, so it can be generated while a
mission records and neither notices the other.

**How the compute is scheduled.** A 30x30 map is about 100 ms of pure-Python numerics —
short next to a scenario run, but still long enough to cost the 10 Hz broadcast a tick if
it ran on the event loop. So a cold map goes to a worker thread through the shared slot in
`app/core/compute_budget.py`, the same way scenarios and the optimiser do. A map that is
already memoised skips both: it is a dictionary lookup, and making the altitude slider
queue behind someone else's optimiser search would be a self-inflicted stall.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from app.core.compute_budget import heavy_compute_slot
from app.core.engine_params import PARAMS
from app.core.uav_ids import DEFAULT_UAV_ID
from app.physics.performance_map import (
    DEFAULT_LOAD_STEPS,
    DEFAULT_METRIC,
    DEFAULT_RPM_STEPS,
    MAX_GRID_STEPS,
    METRICS,
    cached_performance_map,
    is_map_cached,
    to_payload,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/performance-maps", tags=["test-bench"])


@router.get("")
async def get_performance_map(
    altitude_m: float = Query(
        default=0.0,
        ge=PARAMS.scenario_altitude_min_m,
        le=PARAMS.scenario_altitude_max_m,
        description="Pressure altitude the map is generated for. Maps shift with density.",
    ),
    metric: str = Query(
        default=DEFAULT_METRIC,
        description="power | bsfc | volumetric_efficiency",
    ),
    ambient_temperature_c: float | None = Query(
        default=None,
        ge=PARAMS.scenario_ambient_min_c,
        le=PARAMS.scenario_ambient_max_c,
        description="Hot-day override. Omit for the ISA temperature at this altitude.",
    ),
    rpm_steps: int = Query(default=DEFAULT_RPM_STEPS, ge=2, le=MAX_GRID_STEPS),
    load_steps: int = Query(default=DEFAULT_LOAD_STEPS, ge=2, le=MAX_GRID_STEPS),
) -> dict:
    """The RPM x throttle grid and the selected metric's z-values.

    All three surfaces are computed together and memoised, so switching metric at the same
    conditions costs a dictionary lookup rather than a second full pass.
    """
    name = metric.strip().lower()
    if name not in METRICS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"unknown metric {metric!r}",
                "reasons": [
                    "metric must name one of the computed surfaces",
                ],
                "metrics": list(METRICS),
            },
        )

    if is_map_cached(altitude_m, ambient_temperature_c, rpm_steps, load_steps):
        pmap = cached_performance_map(
            altitude_m, ambient_temperature_c, rpm_steps, load_steps
        )
    else:
        async with heavy_compute_slot(f"performance-map/{altitude_m:.0f}m"):
            pmap = await run_in_threadpool(
                cached_performance_map,
                altitude_m,
                ambient_temperature_c,
                rpm_steps,
                load_steps,
            )

    return to_payload(pmap, name)


@router.get("/metrics")
async def list_metrics() -> dict:
    """The selectable surfaces and the grid bounds, so the UI does not hardcode them."""
    p = PARAMS
    return {
        "metrics": [
            {
                "name": name,
                "label": spec.label,
                "unit": spec.unit,
                "lower_is_better": spec.lower_is_better,
                "description": spec.description,
            }
            for name, spec in METRICS.items()
        ],
        "default_metric": DEFAULT_METRIC,
        "altitude_m": {
            "min": p.scenario_altitude_min_m,
            "max": p.scenario_altitude_max_m,
        },
        "axes": {
            "rpm": {"min": p.rpm_idle, "max": p.rpm_redline},
            "throttle_pct": {"min": 0.0, "max": 100.0},
        },
        "grid": {
            "rpm_steps": DEFAULT_RPM_STEPS,
            "load_steps": DEFAULT_LOAD_STEPS,
            "max_steps": MAX_GRID_STEPS,
        },
    }


@router.get("/live-point")
async def live_operating_point(
    request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    """Where the live engine is on the map right now — RPM, throttle and altitude.

    Deliberately a poll rather than a subscription. The Test Bench page opens no WebSocket
    by design (see `frontend/app/test-bench/page.tsx`), and the marker only needs to be
    roughly current, so a 1 Hz GET is the honest way to get it there without widening the
    telemetry contract or giving this page a live socket it would then have to be trusted
    not to misuse.

    `available` is false rather than a 4xx when the mock backend is running or no frame has
    been produced yet: an absent marker is a normal state for this panel, not an error the
    operator needs to see.
    """
    fleet = getattr(request.app.state, "fleet", None)
    sim = fleet.entries[uav_id].sim if fleet is not None and uav_id in fleet.entries else None
    unavailable = {
        "available": False,
        "reason": "no live physics frame yet",
        "mission_recording": False,
    }
    if sim is None or not hasattr(sim, "get_latest"):
        return {**unavailable, "reason": "requires the physics backend (USE_MOCK=false)"}

    frame = sim.get_latest()
    if frame is None:
        return unavailable

    # `throttle` is the loop's commanded value; the frame itself does not carry it, and
    # the y axis of this map is throttle, so it is read from the same object the frame
    # came from rather than inferred from manifold pressure.
    throttle = getattr(sim, "throttle", None)
    if throttle is None:
        return {**unavailable, "reason": "live throttle unavailable"}

    return {
        "available": True,
        "rpm": round(float(frame.rpm), 1),
        "throttle_pct": round(float(throttle) * 100.0, 1),
        "altitude_m": round(float(frame.altitude_m), 1),
        "ambient_temperature_c": frame.ambient_temperature_c,
        "mission_phase": frame.mission_phase,
        "bsfc_g_per_kwh": frame.bsfc_g_per_kwh,
        "manifold_pressure_kpa": frame.manifold_pressure_kpa,
        "is_replay": bool(frame.is_replay),
        "mission_recording": getattr(sim, "active_mission_id", None) is not None,
        "timestamp": frame.timestamp,
    }
