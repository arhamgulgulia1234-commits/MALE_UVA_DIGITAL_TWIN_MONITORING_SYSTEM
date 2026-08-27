"""FastAPI entrypoint: mounts routers, enables CORS for the Next.js dev server, and runs
a background asyncio task that ticks the mock SimulationLoop at sim_tick_hz and broadcasts
each TelemetryFrame to every connected /ws/telemetry client.

TODO(phase-2): swap SimulationLoop for app.twin.digital_twin's orchestrator behind the
same tick()/get_latest() interface.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import control, health, ws_telemetry
from app.core.config import settings
from app.sim.simulation_loop import SimulationLoop


async def _tick_loop(app: FastAPI) -> None:
    sim: SimulationLoop = app.state.sim
    last = time.time()
    while True:
        await asyncio.sleep(settings.tick_seconds)
        now = time.time()
        real_dt = now - last
        last = now
        frame = sim.tick(real_dt)
        await ws_telemetry.manager.broadcast_json(frame.model_dump())


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sim = SimulationLoop()
    task = asyncio.create_task(_tick_loop(app))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="SIH26054 Engine Digital Twin — Mock Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(control.router)
app.include_router(ws_telemetry.router)
