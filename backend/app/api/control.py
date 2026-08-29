"""Demo control surface.

Phase 1/2: fault injection/clearing, throttle, time-scale, mission-phase jump.
Phase 3: sensor-fault injection, environmental scenarios, mission recording sessions,
and mission replay.
Phase 4: the operating setpoint (throttle plus mixture and injection-timing trims) and
the three optimizer-resolved mission presets. Purely additive — every Phase 1-3 route
below is untouched.

Every route here mutates the single shared SimulationLoop, and every route is guarded by
`require_token` — one choke point for authentication, so hardening it later is a contained
change (see app/core/security.py and docs/deployment-roadmap.md).
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.core import mission_presets
from app.core.compute_budget import heavy_compute_slot
from app.core.models import (
    AmbientTemperatureRequest,
    ApplyPresetRequest,
    ClearFaultRequest,
    ClearSensorFaultRequest,
    FaultInjectRequest,
    MissionStartRequest,
    OperatingSetpointRequest,
    PhaseJumpRequest,
    ReplayStartRequest,
    ScenarioRequest,
    SensorFaultRequest,
    ThrottleRequest,
    TimeScaleRequest,
)
from app.core.security import require_token
from app.db.repository import repository
from app.ml.mission_report import build_mission_report
from app.sim.mission_profiles import SCENARIOS
from app.sim.replay_engine import replay_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/control", dependencies=[Depends(require_token)])


# ---- Phase 1/2 controls -----------------------------------------------------


@router.post("/fault")
async def inject_fault(req: FaultInjectRequest, request: Request) -> dict:
    sim = request.app.state.sim
    sim.inject_fault(req.type, req.severity, req.ramp_seconds)

    # Log the event against the active mission, if one is recording.
    mission_id = getattr(sim, "active_mission_id", None)
    if mission_id is not None:
        diagnostics = getattr(sim, "diagnostics", None)
        repository.save_fault_event(
            mission_id,
            req.type,
            req.severity,
            time.time(),
            is_sensor_fault=False,
            predicted_source=getattr(diagnostics, "predicted_source", None),
            classifier_explanation=getattr(diagnostics, "classifier_explanation", None),
        )

    # `active_faults` only lists faults whose severity has already risen above zero, so
    # immediately after injection a ramping fault is not in it yet. Report what was
    # commanded as well, otherwise the response looks like the call did nothing.
    return {
        "ok": True,
        "injected": req.type,
        "target_severity": req.severity,
        "ramp_seconds": req.ramp_seconds,
        "active_faults": list(sim.active_faults.keys()),
    }


@router.post("/clear-fault")
async def clear_fault(req: ClearFaultRequest, request: Request) -> dict:
    sim = request.app.state.sim
    sim.clear_fault(req.fault_type)
    mission_id = getattr(sim, "active_mission_id", None)
    if mission_id is not None:
        repository.close_fault_event(mission_id, req.fault_type, time.time())
    return {"ok": True, "cleared": req.fault_type}


@router.post("/throttle")
async def set_throttle(req: ThrottleRequest, request: Request) -> dict:
    sim = request.app.state.sim
    sim.set_throttle(req.value)
    return {"ok": True, "throttle": sim.throttle}


@router.post("/time-scale")
async def set_time_scale(req: TimeScaleRequest, request: Request) -> dict:
    sim = request.app.state.sim
    sim.set_time_scale(req.factor)
    return {"ok": True, "time_scale": sim.time_scale}


@router.post("/phase")
async def jump_phase(req: PhaseJumpRequest, request: Request) -> dict:
    sim = request.app.state.sim
    sim.jump_phase(req.phase)
    return {"ok": True, "phase": req.phase}


# ---- Phase 3: sensor faults -------------------------------------------------


@router.post("/sensor-fault")
async def inject_sensor_fault(req: SensorFaultRequest, request: Request) -> dict:
    """Corrupt what an instrument *reports*, leaving the engine physically healthy."""
    sim = request.app.state.sim
    if not hasattr(sim, "inject_sensor_fault"):
        raise HTTPException(400, "sensor faults require the physics backend (USE_MOCK=false)")
    sim.inject_sensor_fault(req.type, req.severity, req.ramp_seconds)

    mission_id = getattr(sim, "active_mission_id", None)
    if mission_id is not None:
        repository.save_fault_event(
            mission_id,
            req.type,
            req.severity,
            time.time(),
            is_sensor_fault=True,
            predicted_source="sensor_fault",
        )

    return {
        "ok": True,
        "injected": req.type,
        "target_severity": req.severity,
        "kind": "sensor_fault",
    }


@router.post("/clear-sensor-fault")
async def clear_sensor_fault(req: ClearSensorFaultRequest, request: Request) -> dict:
    sim = request.app.state.sim
    if not hasattr(sim, "clear_sensor_fault"):
        raise HTTPException(400, "sensor faults require the physics backend")
    sim.clear_sensor_fault(req.fault_type)
    mission_id = getattr(sim, "active_mission_id", None)
    if mission_id is not None:
        repository.close_fault_event(mission_id, req.fault_type, time.time())
    return {"ok": True, "cleared": req.fault_type}


# ---- Phase 3: environment ---------------------------------------------------


@router.post("/ambient-temperature")
async def set_ambient_temperature(
    req: AmbientTemperatureRequest, request: Request
) -> dict:
    sim = request.app.state.sim
    if not hasattr(sim, "set_ambient_temperature"):
        raise HTTPException(400, "requires the physics backend")
    sim.set_ambient_temperature(req.ambient_temperature_c)
    return {"ok": True, "ambient_temperature_c": req.ambient_temperature_c}


@router.get("/scenarios")
async def list_scenarios() -> dict:
    return {
        "scenarios": [
            {"name": name, **{k: v for k, v in cfg.items()}}
            for name, cfg in SCENARIOS.items()
        ]
    }


@router.post("/scenario")
async def apply_scenario(req: ScenarioRequest, request: Request) -> dict:
    sim = request.app.state.sim
    if not hasattr(sim, "apply_scenario"):
        raise HTTPException(400, "requires the physics backend")
    result = sim.apply_scenario(req.scenario)
    if not result.get("ok"):
        raise HTTPException(400, result.get("detail", "unknown scenario"))
    return result


# ---- Phase 3: mission recording ---------------------------------------------


@router.post("/mission/start")
async def start_mission(req: MissionStartRequest, request: Request) -> dict:
    """Begin recording. Telemetry streams live either way; persistence only happens
    inside an explicit mission session."""
    sim = request.app.state.sim
    if getattr(sim, "active_mission_id", None) is not None:
        raise HTTPException(
            409, f"mission {sim.active_mission_id} is already recording"
        )
    mission_id = repository.start_mission(req.profile_name, req.notes)
    sim.active_mission_id = mission_id
    return {"ok": True, "mission_id": mission_id, "profile_name": req.profile_name}


@router.post("/mission/end")
async def end_mission(request: Request) -> dict:
    sim = request.app.state.sim
    mission_id = getattr(sim, "active_mission_id", None)
    if mission_id is None:
        raise HTTPException(409, "no mission is currently recording")

    # Stop recording first, so the report is built over a stable frame set.
    sim.active_mission_id = None
    repository.flush()

    mission = repository.get_mission(mission_id) or {"id": mission_id}
    frames = repository.get_mission_frames(mission_id)
    events = repository.get_fault_events(mission_id)
    report = build_mission_report(mission, frames, events)
    repository.end_mission(mission_id, report)

    return {"ok": True, "mission_id": mission_id, "report": report}


@router.get("/mission/status")
async def mission_status(request: Request) -> dict:
    sim = request.app.state.sim
    mission_id = getattr(sim, "active_mission_id", None)
    return {
        "recording": mission_id is not None,
        "mission_id": mission_id,
        "frames_recorded": repository.count_frames(mission_id) if mission_id else 0,
    }


@router.get("/missions")
async def list_missions() -> dict:
    return {"missions": repository.list_missions()}


@router.get("/missions/{mission_id}/report")
async def get_mission_report(mission_id: int) -> dict:
    report = repository.get_mission_report(mission_id)
    if report is None:
        # A mission that was never ended has no stored report — build one on demand
        # rather than making the caller re-run the mission.
        mission = repository.get_mission(mission_id)
        if mission is None:
            raise HTTPException(404, f"mission {mission_id} not found")
        frames = repository.get_mission_frames(mission_id)
        if not frames:
            raise HTTPException(404, f"mission {mission_id} has no recorded frames")
        report = build_mission_report(
            mission, frames, repository.get_fault_events(mission_id)
        )
    return {"mission_id": mission_id, "report": report}


# ---- Phase 3: replay --------------------------------------------------------


@router.post("/replay/start")
async def start_replay(req: ReplayStartRequest, request: Request) -> dict:
    from app.api import ws_telemetry

    frames = repository.get_mission_frames(req.mission_id)
    if not frames:
        raise HTTPException(404, f"mission {req.mission_id} has no recorded frames")

    result = await replay_engine.start(
        req.mission_id,
        req.speed_factor,
        ws_telemetry.manager.broadcast_json,
        frames,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("detail", "replay failed to start"))
    return result


@router.post("/replay/stop")
async def stop_replay() -> dict:
    return await replay_engine.stop()


# ---- Phase 4: operating setpoint and mission presets -------------------------


@router.post("/setpoint")
async def set_operating_setpoint(
    req: OperatingSetpointRequest, request: Request
) -> dict:
    """Command throttle, mixture trim and/or injection-timing trim on the live engine.

    This is the existing manual-throttle override widened to the two levers Phase 2 kept
    internal. Omitted fields are left alone, so the mixture can be trimmed without
    disturbing the throttle."""
    sim = request.app.state.sim
    if not hasattr(sim, "set_operating_setpoint"):
        raise HTTPException(400, "requires the physics backend (USE_MOCK=false)")
    if (
        req.throttle is None
        and req.afr_trim is None
        and req.injection_timing_trim_deg is None
    ):
        raise HTTPException(400, "no setpoint field supplied")
    setpoint = sim.set_operating_setpoint(
        throttle=req.throttle,
        afr_trim=req.afr_trim,
        injection_timing_trim_deg=req.injection_timing_trim_deg,
    )
    return {"ok": True, "setpoint": setpoint}


@router.get("/setpoint")
async def get_operating_setpoint(request: Request) -> dict:
    sim = request.app.state.sim
    if not hasattr(sim, "operating_setpoint"):
        raise HTTPException(400, "requires the physics backend")
    return {"setpoint": sim.operating_setpoint()}


@router.post("/setpoint/reset")
async def reset_operating_setpoint(request: Request) -> dict:
    """Return mixture and timing to their scheduled values, leaving throttle alone."""
    sim = request.app.state.sim
    if not hasattr(sim, "reset_trims"):
        raise HTTPException(400, "requires the physics backend")
    return {"ok": True, "setpoint": sim.reset_trims()}


@router.get("/presets")
async def list_presets() -> dict:
    """The three mission presets, each resolved by the operating-point optimizer.

    Resolving a preset is a few seconds of steady-state search, so it happens on a worker
    thread — the event loop has a 10 Hz telemetry broadcast to keep running — and the
    result is cached for the life of the process."""
    async with heavy_compute_slot("presets"):
        cards = await run_in_threadpool(mission_presets.all_preset_cards)
    return {"presets": cards, "reference": mission_presets.reference_summary()}


@router.post("/apply-preset")
async def apply_preset(req: ApplyPresetRequest, request: Request) -> dict:
    """Feed a preset's setpoint into the live simulation's manual-override mechanism."""
    sim = request.app.state.sim
    if not hasattr(sim, "set_operating_setpoint"):
        raise HTTPException(400, "requires the physics backend (USE_MOCK=false)")

    try:
        # Resolve off the event loop (it may need to run the optimizer), then apply on
        # it, so the simulation state is still only ever mutated from one thread.
        async with heavy_compute_slot(f"preset/{req.preset_name}"):
            setpoint = await run_in_threadpool(
                mission_presets.preset_setpoint, req.preset_name
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    applied = sim.set_operating_setpoint(
        throttle=setpoint["throttle"],
        afr_trim=setpoint["afr_trim"],
        injection_timing_trim_deg=setpoint["injection_timing_trim_deg"],
    )
    logger.info("Applied preset '%s' to the live engine: %s", req.preset_name, applied)
    return {"ok": True, "preset_name": req.preset_name, "setpoint": applied}


@router.get("/replay/status")
async def replay_status() -> dict:
    return replay_engine.status
