"""FastAPI entrypoint.

Mounts the routers, enables CORS for the Next.js dev server, initialises the SQLite
schema, and starts the background simulation task. The active simulator is the Phase 2
physics model; setting USE_MOCK=true falls back to the Phase 1 scripted generator as a
demo-safety net.

Phase 4 mounts three more routers — /simulate/* (Test Bench scenarios), /optimize/*
(operating-point optimisation) and /performance-maps/* (steady-state engine maps). None of
them participates in the live telemetry path.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    audit,
    control,
    fleet,
    health,
    lifecycle,
    optimizer,
    performance_map,
    scenario,
    twin_diagnostics,
    ws_telemetry,
)
from app.auth.router import router as auth_router
from app.auth.seed import seed_default_users
from app.core.compute_budget import tune_interpreter
from app.core.config import settings
from app.core.fleet_registry import build_default_fleet
from app.db.repository import repository
from app.db.session import init_db
from app.sim.simulation_loop import run_simulation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _run_mock(app: FastAPI) -> None:
    """Phase 1 fallback loop (USE_MOCK=true). Not fleet-aware — this is a scripted
    demo safety net for when the physics model itself cannot run, so it keeps its
    original single-engine shape rather than being generalised alongside Phase 6."""
    sim = app.state.sim
    replay_engine = app.state.fleet.default.replay_engine
    last = time.perf_counter()
    while True:
        await asyncio.sleep(settings.tick_seconds)
        now = time.perf_counter()
        real_dt = now - last
        last = now
        frame = sim.tick(real_dt)
        if replay_engine.active:
            continue
        await ws_telemetry.manager.broadcast_json(frame.model_dump())


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_default_users()
    logger.info("Database ready.")
    tune_interpreter()

    logger.info(
        "JWT auth active on /control/*, /ws/telemetry, /ws/fleet-overview. DEMO_MODE=%s.",
        settings.demo_mode,
    )

    # Phase 6: app.state.fleet is the source of truth — one FleetEntry (SimulationLoop
    # + ReplayEngine) per UAV. app.state.sim stays as a backward-compat alias pointing
    # at the default UAV's SimulationLoop, so any code that has not been made
    # uav_id-aware yet (or the Phase 1 mock loop, which never will be) keeps working
    # unchanged against "whichever engine a single-engine caller means".
    app.state.fleet = build_default_fleet()
    app.state.sim = app.state.fleet.default.sim

    if settings.use_mock:
        from app.sim.mock_generator import SimulationLoop as MockLoop

        logger.warning("USE_MOCK=true — serving Phase 1 scripted telemetry, not physics.")
        app.state.sim = MockLoop()
        app.state.fleet.default.sim = app.state.sim
        app.state.physics_enabled = False
        task = asyncio.create_task(_run_mock(app))
        overview_task = None
    else:
        logger.info("Starting Phase 2 physics simulation for %d UAVs.", len(app.state.fleet.uav_ids))
        app.state.physics_enabled = True
        task = asyncio.create_task(run_simulation(app))
        overview_task = asyncio.create_task(fleet.run_fleet_overview_broadcast(app))

    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if overview_task is not None:
            overview_task.cancel()
            try:
                await overview_task
            except asyncio.CancelledError:
                pass
        for entry in app.state.fleet:
            await entry.replay_engine.stop()
        # Persist anything still buffered so a mission is not truncated by shutdown.
        repository.flush()


app = FastAPI(
    title="SIH26054 Engine Digital Twin — Physics + PHM Backend",
    lifespan=lifespan,
)

def _json_safe(value):
    """Replace non-JSON-compliant floats so a validation error can be serialised.

    `json.dumps` emits bare `NaN` / `Infinity`, which are not valid JSON, and Starlette's
    JSONResponse refuses them outright. FastAPI's 422 body echoes the offending input
    back to the caller, so a request carrying a NaN produced a *correct* validation
    failure that then died serialising its own error message — the caller saw a bare
    500 with no explanation, for input the API had in fact rejected properly.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": _json_safe(exc.errors())})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth_router)
app.include_router(audit.router)
app.include_router(control.router)
app.include_router(twin_diagnostics.router)
app.include_router(ws_telemetry.router)
# Phase 4 — Test Bench. These routers are read-only with respect to the live simulation:
# a scenario runs on its own plant, and the optimizer only reads the live fault state.
app.include_router(scenario.router)
app.include_router(optimizer.router)
# Steady-state performance maps. Read-only in the same sense as the two above: the map is
# a property of the engine parameters, and /performance-maps/live-point only reads the
# frame the simulation loop has already published.
app.include_router(performance_map.router)
# Phase 5 — engine life-cycle. Wired into mission start/end inside control.py; this
# router itself only reads the persisted ledger and writes maintenance actions to it.
app.include_router(lifecycle.router)
# Phase 6 — fleet-wide read endpoints. Read-only: /fleet/overview and /fleet/rankings
# summarise the per-UAV state control.py/simulation_loop.py already maintain.
app.include_router(fleet.router)
