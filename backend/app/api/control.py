"""Demo control surface.

Phase 1/2: fault injection/clearing, throttle, time-scale, mission-phase jump.
Phase 3: sensor-fault injection, environmental scenarios, mission recording sessions,
and mission replay.
Phase 4: the operating setpoint (throttle plus mixture and injection-timing trims) and
the three optimizer-resolved mission presets. Purely additive — every Phase 1-3 route
below is untouched.
Phase 6: every route below gained a `uav_id` query parameter (default "UAV-01", so an
old caller that never heard of the fleet still gets exactly the engine it always got).
It selects which UAV's `SimulationLoop`/`ReplayEngine` the route acts on via
`request.app.state.fleet.get(uav_id)` — the route bodies are otherwise unchanged from
Phase 1-4.

Every route here mutates one UAV's SimulationLoop and requires a valid session (any
role) via `get_current_user`; `/fault` and `/sensor-fault` additionally require the
administrator role and DEMO_MODE — see app/auth/deps.py and docs/deployment-roadmap.md.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.auth.audit import record
from app.auth.deps import CurrentUser, get_current_user, require_demo_mode, require_role
from app.core import mission_presets
from app.core.compute_budget import heavy_compute_slot
from app.core.fleet_registry import FleetRegistry, lifecycle_engine_id
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
from app.core.uav_ids import DEFAULT_UAV_ID
from app.db.lifecycle_repository import lifecycle_repository
from app.db.models import ROLE_ADMINISTRATOR
from app.db.repository import repository
from app.ml.mission_report import build_mission_report
from app.sim.mission_profiles import SCENARIOS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/control", dependencies=[Depends(get_current_user)])

#: Fault injection needs both the administrator role and DEMO_MODE — two independent
#: gates, stacked as separate dependencies rather than one combined check, so each
#: fails with its own clear 403 reason instead of a generic "not permitted."
_require_fault_injection = [Depends(require_role(ROLE_ADMINISTRATOR)), Depends(require_demo_mode)]


def _fleet(request: Request) -> FleetRegistry:
    return request.app.state.fleet


def _entry(request: Request, uav_id: str):
    try:
        return _fleet(request).get(uav_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---- Phase 1/2 controls -----------------------------------------------------


@router.post("/fault", dependencies=_require_fault_injection)
async def inject_fault(
    req: FaultInjectRequest,
    request: Request,
    uav_id: str = DEFAULT_UAV_ID,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    sim = _entry(request, uav_id).sim
    sim.inject_fault(req.type, req.severity, req.ramp_seconds)
    record(user, "fault.inject", {"uav_id": uav_id, "type": req.type, "severity": req.severity})

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
        "uav_id": uav_id,
        "injected": req.type,
        "target_severity": req.severity,
        "ramp_seconds": req.ramp_seconds,
        "active_faults": list(sim.active_faults.keys()),
    }


@router.post("/clear-fault")
async def clear_fault(
    req: ClearFaultRequest,
    request: Request,
    uav_id: str = DEFAULT_UAV_ID,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    sim = _entry(request, uav_id).sim
    sim.clear_fault(req.fault_type)
    mission_id = getattr(sim, "active_mission_id", None)
    if mission_id is not None:
        repository.close_fault_event(mission_id, req.fault_type, time.time())
    record(user, "fault.clear", {"uav_id": uav_id, "fault_type": req.fault_type})
    return {"ok": True, "cleared": req.fault_type}


@router.post("/throttle")
async def set_throttle(
    req: ThrottleRequest, request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    sim = _entry(request, uav_id).sim
    sim.set_throttle(req.value)
    return {"ok": True, "throttle": sim.throttle}


@router.post("/time-scale")
async def set_time_scale(
    req: TimeScaleRequest, request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    sim = _entry(request, uav_id).sim
    sim.set_time_scale(req.factor)
    return {"ok": True, "time_scale": sim.time_scale}


@router.post("/phase")
async def jump_phase(
    req: PhaseJumpRequest, request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    sim = _entry(request, uav_id).sim
    sim.jump_phase(req.phase)
    return {"ok": True, "phase": req.phase}


# ---- Phase 3: sensor faults -------------------------------------------------


@router.post("/sensor-fault", dependencies=_require_fault_injection)
async def inject_sensor_fault(
    req: SensorFaultRequest,
    request: Request,
    uav_id: str = DEFAULT_UAV_ID,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Corrupt what an instrument *reports*, leaving the engine physically healthy."""
    sim = _entry(request, uav_id).sim
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
    record(user, "sensor_fault.inject", {"uav_id": uav_id, "type": req.type, "severity": req.severity})

    return {
        "ok": True,
        "injected": req.type,
        "target_severity": req.severity,
        "kind": "sensor_fault",
    }


@router.post("/clear-sensor-fault")
async def clear_sensor_fault(
    req: ClearSensorFaultRequest, request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    sim = _entry(request, uav_id).sim
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
    req: AmbientTemperatureRequest, request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    sim = _entry(request, uav_id).sim
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
async def apply_scenario(
    req: ScenarioRequest, request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    sim = _entry(request, uav_id).sim
    if not hasattr(sim, "apply_scenario"):
        raise HTTPException(400, "requires the physics backend")
    result = sim.apply_scenario(req.scenario)
    if not result.get("ok"):
        raise HTTPException(400, result.get("detail", "unknown scenario"))
    return result


# ---- Phase 3: mission recording ---------------------------------------------


@router.post("/mission/start")
async def start_mission(
    req: MissionStartRequest,
    request: Request,
    uav_id: str = DEFAULT_UAV_ID,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Begin recording. Telemetry streams live either way; persistence only happens
    inside an explicit mission session.

    Phase 5: on the physics backend, this is also where the live engine picks up its
    accumulated wear. `engine_lifecycle.current_wear_state` — whatever the previous
    mission left behind, or a maintenance action reset since — replaces the live
    `FaultState` wholesale, so a mission on an engine with real bearing wear starts
    already partially degraded instead of pretending every flight begins on a
    factory-fresh engine. The mock backend has no wear model to seed, so this is skipped
    there exactly like every other Phase 4/5 physics-only control.

    Phase 6: the lifecycle ledger this seeds from is keyed by
    `lifecycle_engine_id(uav_id)`, not `uav_id` directly — see fleet_registry.py for
    why UAV-01 reuses the pre-existing "primary" engine_lifecycle row."""
    sim = _entry(request, uav_id).sim
    if getattr(sim, "active_mission_id", None) is not None:
        raise HTTPException(
            409, f"mission {sim.active_mission_id} is already recording"
        )
    mission_id = repository.start_mission(req.profile_name, req.notes, uav_id=uav_id)
    sim.active_mission_id = mission_id

    seeded_wear_state = None
    if hasattr(sim, "seed_fault_state_from_wear"):
        lifecycle = lifecycle_repository.get_current_lifecycle(
            engine_id=lifecycle_engine_id(uav_id)
        )
        seeded_wear_state = sim.seed_fault_state_from_wear(
            lifecycle["current_wear_state"]
        )
        # Mark the simulated-time clock, not the wall clock — see the note on
        # `sim_time_s` in simulation_loop.py. Mission end bills operating hours off the
        # delta from this mark, so a mission flown at 20x time-scale correctly credits
        # the engine with 20x the simulated seconds a 1x mission of the same wall-clock
        # length would.
        sim.mission_start_sim_time_s = sim.sim_time_s

    record(user, "mission.start", {"uav_id": uav_id, "mission_id": mission_id, "profile_name": req.profile_name})
    return {
        "ok": True,
        "uav_id": uav_id,
        "mission_id": mission_id,
        "profile_name": req.profile_name,
        "seeded_wear_state": seeded_wear_state,
    }


@router.post("/mission/end")
async def end_mission(
    request: Request, uav_id: str = DEFAULT_UAV_ID, user: CurrentUser = Depends(get_current_user)
) -> dict:
    sim = _entry(request, uav_id).sim
    mission_id = getattr(sim, "active_mission_id", None)
    if mission_id is None:
        raise HTTPException(409, "no mission is currently recording")

    # Stop recording first, so the report is built over a stable frame set.
    sim.active_mission_id = None
    repository.flush()
    record(user, "mission.end", {"uav_id": uav_id, "mission_id": mission_id})

    mission = repository.get_mission(mission_id) or {"id": mission_id}
    frames = repository.get_mission_frames(mission_id)
    events = repository.get_fault_events(mission_id)
    report = build_mission_report(mission, frames, events)
    repository.end_mission(mission_id, report)

    engine_id = lifecycle_engine_id(uav_id)
    lifecycle = None
    if hasattr(sim, "faults"):
        # Simulated seconds elapsed during this mission, from `sim_time_s` — not the
        # report's `duration_s`, which is wall-clock (`time.time()` deltas between
        # frames, because that clock is what the dashboard and replay need). At
        # time_scale 1x the two agree; at 20x, duration_s would credit the engine with
        # only 1/20th of the operating hours it actually accumulated, which made every
        # accelerated test mission during development read back as ~0.0 hours even
        # though the physics really did run that long in simulated time.
        elapsed_sim_s = sim.sim_time_s - getattr(sim, "mission_start_sim_time_s", sim.sim_time_s)
        duration_hours = max(0.0, elapsed_sim_s) / 3600.0
        lifecycle = lifecycle_repository.increment_operating_hours(
            duration_hours, engine_id=engine_id
        )

        final_wear = sim.faults.snapshot()
        lifecycle = lifecycle_repository.set_wear_state(final_wear, engine_id=engine_id)

        # A fault counts as "active during the mission" if it either fired during this
        # mission's own telemetry (a real, non-sensor FaultEvent row — the operator
        # injected it, or a scheduled Test Bench-style fault ramped up) or if the
        # mission was *seeded* already carrying it from the previous mission's wear.
        # The second half matters: an engine that starts a flight with bearing wear at
        # 0.4 and never gets worse should still count that flight against bearing wear's
        # lifetime tally — it was active the whole time, even though nothing "happened."
        active_from_events = {
            e["fault_type"]
            for e in events
            if not e.get("is_sensor_fault")
        }
        active_from_seed = {
            ft
            for ft, severity in getattr(sim, "mission_seed_wear_state", {}).items()
            if severity > 1e-4
        }
        for fault_type in active_from_events | active_from_seed:
            lifecycle = lifecycle_repository.record_fault_event(
                fault_type, engine_id=engine_id
            )

        sim.mission_seed_wear_state = {}

    return {"ok": True, "mission_id": mission_id, "report": report, "lifecycle": lifecycle}


@router.get("/mission/status")
async def mission_status(request: Request, uav_id: str = DEFAULT_UAV_ID) -> dict:
    sim = _entry(request, uav_id).sim
    mission_id = getattr(sim, "active_mission_id", None)
    return {
        "recording": mission_id is not None,
        "mission_id": mission_id,
        "frames_recorded": repository.count_frames(mission_id) if mission_id else 0,
    }


@router.get("/missions")
async def list_missions(uav_id: str | None = None) -> dict:
    return {"missions": repository.list_missions(uav_id=uav_id)}


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
async def start_replay(
    req: ReplayStartRequest, request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    from app.api import ws_telemetry

    frames = repository.get_mission_frames(req.mission_id)
    if not frames:
        raise HTTPException(404, f"mission {req.mission_id} has no recorded frames")

    entry = _entry(request, uav_id)
    result = await entry.replay_engine.start(
        req.mission_id,
        req.speed_factor,
        lambda payload: ws_telemetry.manager.broadcast_to_uav(uav_id, payload),
        frames,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("detail", "replay failed to start"))
    return result


@router.post("/replay/stop")
async def stop_replay(request: Request, uav_id: str = DEFAULT_UAV_ID) -> dict:
    return await _entry(request, uav_id).replay_engine.stop()


# ---- Phase 4: operating setpoint and mission presets -------------------------


@router.post("/setpoint")
async def set_operating_setpoint(
    req: OperatingSetpointRequest, request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    """Command throttle, mixture trim and/or injection-timing trim on the live engine.

    This is the existing manual-throttle override widened to the two levers Phase 2 kept
    internal. Omitted fields are left alone, so the mixture can be trimmed without
    disturbing the throttle."""
    sim = _entry(request, uav_id).sim
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
async def get_operating_setpoint(request: Request, uav_id: str = DEFAULT_UAV_ID) -> dict:
    sim = _entry(request, uav_id).sim
    if not hasattr(sim, "operating_setpoint"):
        raise HTTPException(400, "requires the physics backend")
    return {"setpoint": sim.operating_setpoint()}


@router.post("/setpoint/reset")
async def reset_operating_setpoint(
    request: Request, uav_id: str = DEFAULT_UAV_ID
) -> dict:
    """Return mixture and timing to their scheduled values, leaving throttle alone."""
    sim = _entry(request, uav_id).sim
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
async def apply_preset(
    req: ApplyPresetRequest,
    request: Request,
    uav_id: str = DEFAULT_UAV_ID,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Feed a preset's setpoint into the live simulation's manual-override mechanism."""
    sim = _entry(request, uav_id).sim
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
    record(user, "preset.apply", {"uav_id": uav_id, "preset_name": req.preset_name})
    return {"ok": True, "preset_name": req.preset_name, "setpoint": applied}


@router.get("/replay/status")
async def replay_status(request: Request, uav_id: str = DEFAULT_UAV_ID) -> dict:
    return _entry(request, uav_id).replay_engine.status
