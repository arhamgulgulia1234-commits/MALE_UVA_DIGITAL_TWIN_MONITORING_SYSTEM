"""WebSocket endpoint that broadcasts the mock TelemetryFrame stream to every connected
dashboard client at the configured tick rate (see app.core.config.settings.sim_tick_hz).

TODO(phase-2): the broadcast mechanics here stay the same; only the source of frames
changes (app.twin.digital_twin instead of app.sim.simulation_loop).
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast_json(self, payload: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@router.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            # Client -> server messages aren't required for telemetry (control happens
            # over REST), but we still need to await something to detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
