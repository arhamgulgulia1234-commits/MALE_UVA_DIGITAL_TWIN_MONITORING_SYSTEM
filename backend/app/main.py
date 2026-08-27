"""FastAPI entrypoint.

Mounts the routers, enables CORS for the Next.js dev server, and starts the background
simulation task. The active simulator is the Phase 2 physics model
(app/sim/simulation_loop.py); setting USE_MOCK=true falls back to the Phase 1 scripted
generator as a demo-safety net.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import control, health, twin_diagnostics, ws_telemetry
from app.core.config import settings
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
        await ws_telemetry.manager.broadcast_json(frame.model_dump())


@asynccontextmanager
async def lifespan(app: FastAPI):
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


app = FastAPI(
    title="SIH26054 Engine Digital Twin — Physics Backend",
    lifespan=lifespan,
)

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
