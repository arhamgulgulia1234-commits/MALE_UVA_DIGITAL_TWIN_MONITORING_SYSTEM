"""FastAPI entrypoint.

Mounts the routers, enables CORS for the Next.js dev server, initialises the SQLite
schema, and starts the background simulation task. The active simulator is the Phase 2
physics model; setting USE_MOCK=true falls back to the Phase 1 scripted generator as a
demo-safety net.

Phase 4 mounts two more routers — /simulate/* (Test Bench scenarios) and /optimize/*
(operating-point optimisation). Neither participates in the live telemetry path.
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

from app.api import control, health, optimizer, scenario, twin_diagnostics, ws_telemetry
from app.core.compute_budget import tune_interpreter
from app.core.config import settings
from app.core.security import auth_enabled
from app.db.repository import repository
from app.db.session import init_db
from app.sim.replay_engine import replay_engine
from app.sim.simulation_loop import SimulationLoop, run_simulation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _run_mock(app: FastAPI) -> None:
    """Phase 1 fallback loop (USE_MOCK=true)."""
    sim = app.state.sim
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
    logger.info("Database ready.")
    tune_interpreter()

    if auth_enabled():
        logger.info("Telemetry auth ENABLED (bearer token required).")
    else:
        logger.info("Telemetry auth disabled — set TELEMETRY_AUTH_ENABLED=true to enforce.")

    if settings.use_mock:
        from app.sim.mock_generator import SimulationLoop as MockLoop

        logger.warning("USE_MOCK=true — serving Phase 1 scripted telemetry, not physics.")
        app.state.sim = MockLoop()
        app.state.physics_enabled = False
        task = asyncio.create_task(_run_mock(app))
    else:
        logger.info("Starting Phase 2 physics simulation.")
        app.state.sim = SimulationLoop()
        app.state.physics_enabled = True
        task = asyncio.create_task(run_simulation(app))

    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await replay_engine.stop()
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
app.include_router(control.router)
app.include_router(twin_diagnostics.router)
app.include_router(ws_telemetry.router)
# Phase 4 — Test Bench. Both routers are read-only with respect to the live simulation:
# a scenario runs on its own plant, and the optimizer only reads the live fault state.
app.include_router(scenario.router)
app.include_router(optimizer.router)
