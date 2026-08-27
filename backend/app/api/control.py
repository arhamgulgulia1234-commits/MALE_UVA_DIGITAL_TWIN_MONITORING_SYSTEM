"""Demo control surface: fault injection/clearing, throttle, time-scale, and mission-phase
jump — all mutate the single shared SimulationLoop instance driving the WebSocket stream.
Wired to the frontend's ControlDeck component.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.models import (
    ClearFaultRequest,
    FaultInjectRequest,
    PhaseJumpRequest,
    ThrottleRequest,
    TimeScaleRequest,
)

router = APIRouter(prefix="/control")


@router.post("/fault")
async def inject_fault(req: FaultInjectRequest, request: Request) -> dict:
    sim = request.app.state.sim
    sim.inject_fault(req.type, req.severity, req.ramp_seconds)
    return {"ok": True, "active_faults": list(sim.active_faults.keys())}


@router.post("/clear-fault")
async def clear_fault(req: ClearFaultRequest, request: Request) -> dict:
    sim = request.app.state.sim
    sim.clear_fault(req.fault_type)
    return {"ok": True}


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
