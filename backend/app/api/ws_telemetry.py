"""WebSocket endpoint broadcasting TelemetryFrames to every connected dashboard.

The same socket carries live physics and replayed missions — `app/sim/replay_engine.py`
pushes stored frames through this exact broadcast path, so the frontend needs no
replay-specific code. Frames carry `is_replay` purely so the UI can show a badge.

Phase 3 adds a bearer-token check on the handshake. Browsers cannot set headers when
opening a WebSocket, so a `?token=` query parameter is accepted alongside the
Authorization header; see app/core/security.py for why that is a demo-grade compromise
and what a real deployment should do instead.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.core.security import auth_enabled, websocket_token_ok

logger = logging.getLogger(__name__)

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

    @property
    def client_count(self) -> int:
        return len(self.active)


manager = ConnectionManager()


@router.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket) -> None:
    if not websocket_token_ok(
        websocket.headers.get("authorization"),
        websocket.query_params.get("token"),
    ):
        logger.warning("Rejected telemetry WebSocket: bad or missing token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket)
    if auth_enabled():
        logger.info("Telemetry client authenticated and connected")
    try:
        while True:
            # Control happens over REST, so nothing is expected from the client — but we
            # must await something to notice a disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
