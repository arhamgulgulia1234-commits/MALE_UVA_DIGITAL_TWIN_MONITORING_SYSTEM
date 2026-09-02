"""Operating-point optimizer endpoint — `POST /optimize/operating-point`.

Answers "what should this engine be running at these conditions, for this objective?" and
shows what the answer costs against the book cruise setting.

Two notes on how this is wired:

**It never mutates the live simulation.** The optimizer only *reads* the live fault
severities when `use_current_engine_health` is set; the recommendation is returned to the
operator, who decides whether to apply it (via `/control/apply-preset` or
`/control/setpoint`). A PHM system that could silently retrim a running engine because it
thought that was a good idea is exactly the design
docs/deployment-roadmap.md argues against.

**The search runs off the event loop, one at a time.** A few seconds of steady-state
simulation on the event loop would stall the 10 Hz telemetry broadcast for every connected
dashboard, so the CPU-bound part goes to a worker thread through the shared compute slot in
`app/core/compute_budget.py`; only the health snapshot is taken on the loop itself.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.auth.deps import get_current_user
from app.core.compute_budget import heavy_compute_slot
from app.core.engine_params import PARAMS
from app.core.models import OperatingPointRequest
from app.core.uav_ids import DEFAULT_UAV_ID
from app.ml.operating_point_optimizer import (
    OBJECTIVE_LABELS,
    OBJECTIVES,
    optimize_operating_point,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/optimize", tags=["test-bench"])


@router.post("/operating-point", dependencies=[Depends(get_current_user)])
async def optimize_point(
    req: OperatingPointRequest, request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    """Recommend a throttle / mixture / timing setpoint for one objective."""
    health_state: dict[str, float] | None = None
    health_source = "pristine engine"

    if req.use_current_engine_health:
        try:
            sim = request.app.state.fleet.get(uav_id).sim
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        if not hasattr(sim, "current_health_state"):
            raise HTTPException(
                400,
                "live engine health requires the physics backend (USE_MOCK=false)",
            )
        # Snapshot on the event loop, before handing the search to a worker thread, so we
        # optimise against one consistent picture rather than one that moves underneath
        # the search.
        health_state = sim.current_health_state()
        health_source = (
            "live engine health"
            if health_state
            else "live engine health (currently no active faults)"
        )

    try:
        async with heavy_compute_slot(f"optimiser/{req.objective}"):
            result = await run_in_threadpool(
                optimize_operating_point,
                req.altitude_m,
                req.ambient_temperature_c,
                req.objective,
                health_state,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "cannot optimise at these conditions",
                "reasons": [str(exc)],
                "objectives": list(OBJECTIVES),
            },
        ) from exc

    payload = result.to_dict()
    payload["health_source"] = health_source
    payload["used_current_engine_health"] = bool(req.use_current_engine_health)
    return payload


@router.get("/objectives")
async def list_objectives() -> dict:
    """The objectives and the search bounds, so the UI does not hardcode them."""
    p = PARAMS
    return {
        "objectives": [
            {"name": name, "label": OBJECTIVE_LABELS[name]} for name in OBJECTIVES
        ],
        "search_bounds": {
            "throttle": {"min": p.opt_throttle_min, "max": p.opt_throttle_max},
            "afr_trim": {"min": p.afr_trim_min, "max": p.afr_trim_max},
            "injection_timing_trim_deg": {
                "min": p.injection_timing_trim_min_deg,
                "max": p.injection_timing_trim_max_deg,
            },
        },
        "hard_limits": {
            "cht_limit_c": p.cht_limit_c,
            "egt_limit_c": p.egt_limit_c,
            "oil_temp_limit_c": p.oil_temp_limit_c,
            "oil_pressure_min_operating_kpa": p.oil_pressure_min_operating_kpa,
        },
        "baseline_throttle_pct": p.nominal_cruise_throttle * 100.0,
        "tbo_hours_nominal": p.tbo_hours_nominal,
    }
