"""Test Bench scenario endpoint — `POST /simulate/scenario`.

Runs a headless what-if through the same physics, twin and PHM stack the live dashboard
uses, and returns the time-series plus a PASS/CAUTION/FAIL summary. Nothing here touches
the live simulation, the WebSocket, or the missions table: a scenario can be run while a
real mission is recording and neither notices the other.

Input bounds are enforced by `app/sim/scenario_engine.validate_scenario_params`, which
returns *reasons* rather than a boolean, and those reasons are what the 400 carries. That
matters more than it sounds: a mission planner who asks for 45 degC at 10 000 m needs to be
told the ISA deviation is outside the modelled range, not handed a silently clamped answer
that looks like a real one.

The run is seconds of CPU-bound Python, so it goes to a worker thread and through the
shared compute slot in `app/core/compute_budget.py` — which keeps the event loop, and
therefore the 10 Hz live telemetry broadcast, responsive while a scenario computes. See
that module for what was measured and why one slot rather than several.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.auth.deps import get_current_user
from app.core.compute_budget import heavy_compute_slot
from app.core.engine_params import PARAMS
from app.core.models import ScenarioParamsRequest
from app.db.repository import repository
from app.physics.fault_models import FAULT_TYPES
from app.sim.scenario_engine import (
    ScenarioParams,
    ScenarioResult,
    ScheduledFault,
    ThrottleWaypoint,
    run_scenario,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulate", tags=["test-bench"])


def _to_engine_params(req: ScenarioParamsRequest) -> ScenarioParams:
    if isinstance(req.throttle_profile, list):
        profile: float | list[ThrottleWaypoint] = [
            ThrottleWaypoint(time_min=wp.time_min, throttle_pct=wp.throttle_pct)
            for wp in req.throttle_profile
        ]
    else:
        profile = float(req.throttle_profile)

    return ScenarioParams(
        altitude_m=req.altitude_m,
        ambient_temperature_c=req.ambient_temperature_c,
        duration_minutes=req.duration_minutes,
        throttle_profile=profile,
        initial_fault_severities=dict(req.initial_fault_severities or {}),
        injected_faults_during_scenario=[
            ScheduledFault(
                fault_type=f.fault_type,
                severity=f.severity,
                at_time_min=f.at_time_min,
                ramp_minutes=f.ramp_minutes,
            )
            for f in req.injected_faults_during_scenario
        ],
        label=req.label,
    )


def _result_payload(result: ScenarioResult, include_frames: bool) -> dict:
    summary = result.summary
    return {
        "params": result.params,
        "frames": (
            [f.model_dump() for f in result.frames] if include_frames else []
        ),
        "frame_count": len(result.frames),
        "summary": {
            "verdict": summary.verdict,
            "headline": summary.headline,
            "stayed_within_safe_health": summary.stayed_within_safe_health,
            "stayed_within_operating_limits": summary.stayed_within_operating_limits,
            "health_safe_threshold": summary.health_safe_threshold,
            "min_health_score": summary.min_health_score,
            "min_health_at_min": summary.min_health_at_min,
            "final_health_score": summary.final_health_score,
            "final_rul_minutes": summary.final_rul_minutes,
            "min_rul_minutes": summary.min_rul_minutes,
            "worst_subsystem": summary.worst_subsystem,
            "worst_subsystem_score": summary.worst_subsystem_score,
            "final_subsystem_scores": summary.final_subsystem_scores,
            "final_recommendation": summary.final_recommendation,
            "worst_recommendation": summary.worst_recommendation,
            "mission_reliability_trajectory": [
                {
                    "time_min": p.time_min,
                    "score": p.score,
                    "recommendation": p.recommendation,
                }
                for p in summary.mission_reliability_trajectory
            ],
            # ---- Phase 5: recovery reliability ---------------------------------
            "final_recovery_recommendation": summary.final_recovery_recommendation,
            "worst_recovery_recommendation": summary.worst_recovery_recommendation,
            "recovery_reliability_trajectory": [
                {
                    "time_min": p.time_min,
                    "score": p.score,
                    "recommendation": p.recommendation,
                }
                for p in summary.recovery_reliability_trajectory
            ],
            "limit_excursions": [_excursion(e) for e in summary.limit_excursions],
            "caution_excursions": [_excursion(e) for e in summary.caution_excursions],
            "peak_cht_c": summary.peak_cht_c,
            "peak_egt_c": summary.peak_egt_c,
            "min_oil_pressure_kpa": summary.min_oil_pressure_kpa,
            "peak_oil_temp_c": summary.peak_oil_temp_c,
            "mean_power_kw": summary.mean_power_kw,
            "mean_bsfc_g_per_kwh": summary.mean_bsfc_g_per_kwh,
            "total_fuel_litres": summary.total_fuel_litres,
            "fuel_burn_lph_mean": summary.fuel_burn_lph_mean,
            "final_advisories": summary.final_advisories,
        },
        "integration_dt_s": result.integration_dt_s,
        "sample_interval_s": result.sample_interval_s,
        "simulated_seconds": result.simulated_seconds,
        "compute_seconds": result.compute_seconds,
        "notes": result.notes,
    }


def _excursion(e) -> dict:
    return {
        "parameter": e.parameter,
        "band": e.band,
        "limit": e.limit,
        "unit": e.unit,
        "peak_value": e.peak_value,
        "first_at_min": e.first_at_min,
        "duration_min": e.duration_min,
        "direction": e.direction,
    }


@router.post("/scenario", dependencies=[Depends(get_current_user)])
async def simulate_scenario(req: ScenarioParamsRequest) -> dict:
    """Run one what-if scenario and return its time-series and summary."""
    params = _to_engine_params(req)
    try:
        async with heavy_compute_slot("scenario"):
            result = await run_in_threadpool(run_scenario, params)
    except ValueError as exc:
        # Every message from the validator names the bound that was crossed and why the
        # model stops being trustworthy past it.
        raise HTTPException(
            status_code=400,
            detail={
                "error": "scenario is outside modelled validity",
                "reasons": str(exc).split("; "),
                "envelope": scenario_envelope(),
            },
        ) from exc

    run_id: int | None = None
    if req.save:
        try:
            run_id = repository.save_scenario_run(
                label=req.label,
                altitude_m=params.altitude_m,
                ambient_temperature_c=params.ambient_temperature_c,
                duration_minutes=params.duration_minutes,
                verdict=result.summary.verdict,
                min_health_score=result.summary.min_health_score,
                final_rul_minutes=result.summary.final_rul_minutes,
                worst_subsystem=result.summary.worst_subsystem,
                compute_seconds=result.compute_seconds,
                params=result.params,
                summary=_result_payload(result, include_frames=False)["summary"],
            )
        except Exception:
            # A failed history write must not lose the answer the operator waited for.
            logger.exception("Failed to record scenario run")

    payload = _result_payload(result, include_frames=req.include_frames)
    payload["scenario_run_id"] = run_id
    payload["is_simulation"] = True
    return payload


@router.get("/scenario/envelope")
def scenario_envelope() -> dict:
    """The modelled validity envelope, so the UI can bound its inputs rather than
    discovering the limits by getting a 400."""
    p = PARAMS
    return {
        "altitude_m": {
            "min": p.scenario_altitude_min_m,
            "max": p.scenario_altitude_max_m,
        },
        "ambient_temperature_c": {
            "min": p.scenario_ambient_min_c,
            "max": p.scenario_ambient_max_c,
        },
        "isa_deviation_k": {
            "min": p.scenario_isa_deviation_min_k,
            "max": p.scenario_isa_deviation_max_k,
        },
        "duration_minutes": {
            "min": p.scenario_duration_min_minutes,
            "max": p.scenario_duration_max_minutes,
        },
        "throttle_pct": {"min": 0.0, "max": 100.0},
        "fault_types": list(FAULT_TYPES),
        "operating_limits": {
            "cht_limit_c": p.cht_limit_c,
            "cht_caution_c": p.cht_caution_c,
            "egt_limit_c": p.egt_limit_c,
            "egt_caution_c": p.egt_caution_c,
            "oil_temp_limit_c": p.oil_temp_limit_c,
            "oil_temp_caution_c": p.oil_temp_caution_c,
            "oil_temp_min_operating_c": p.oil_temp_min_operating_c,
            "oil_pressure_min_operating_kpa": p.oil_pressure_min_operating_kpa,
            "oil_pressure_caution_kpa": p.oil_pressure_caution_kpa,
        },
        "health_safe_threshold": p.scenario_health_safe_threshold,
    }


@router.get("/scenario/runs")
def list_scenario_runs(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    """Past what-if runs, newest first. Parameters and summary only — the time-series is
    regenerable by re-running the same parameters."""
    return {"runs": repository.list_scenario_runs(limit)}


@router.get("/scenario/runs/{run_id}")
def get_scenario_run(run_id: int) -> dict:
    run = repository.get_scenario_run(run_id)
    if run is None:
        raise HTTPException(404, f"scenario run {run_id} not found")
    return run
