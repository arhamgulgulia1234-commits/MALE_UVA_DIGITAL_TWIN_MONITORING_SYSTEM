"""WebSocket endpoint broadcasting TelemetryFrames to every connected dashboard.

The same socket carries live physics and replayed missions — `app/sim/replay_engine.py`
pushes stored frames through this exact broadcast path, so the frontend needs no
replay-specific code. Frames carry `is_replay` purely so the UI can show a badge.

The handshake requires a valid session JWT (any role). Browsers cannot set headers when
opening a WebSocket, so a `?token=` query parameter is accepted alongside the
Authorization header — see app/auth/deps.py::websocket_user.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.auth.deps import websocket_user
from app.core.uav_ids import DEFAULT_UAV_ID

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    """Phase 6: one client list per UAV, so a dashboard watching UAV-02 never sees
    UAV-01's frames and vice versa. Kept as a dict-of-lists rather than N separate
    manager instances so `client_count` can still answer "how many dashboards total"
    without the caller needing to know the fleet roster."""

    def __init__(self) -> None:
        self.active: dict[str, list[WebSocket]] = {}

    async def connect(self, ws: WebSocket, uav_id: str = DEFAULT_UAV_ID) -> None:
        await ws.accept()
        self.active.setdefault(uav_id, []).append(ws)

    def disconnect(self, ws: WebSocket, uav_id: str = DEFAULT_UAV_ID) -> None:
        sockets = self.active.get(uav_id)
        if sockets and ws in sockets:
            sockets.remove(ws)

    async def broadcast_to_uav(self, uav_id: str, payload: dict) -> None:
        # Iterate a snapshot. `send_json` awaits, and during that await the endpoint
        # coroutine for a *different* socket can notice its client has gone and call
        # `disconnect()`, which removes an entry from this same list. Mutating a list
        # while a `for` walks it by index makes the loop skip whichever element shifts
        # into the vacated slot, so a client that is still connected silently misses that
        # frame. Rapid tab open/close is exactly the workload that triggers it.
        sockets = self.active.get(uav_id, [])
        dead: list[WebSocket] = []
        for ws in list(sockets):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, uav_id)

    async def broadcast_json(self, payload: dict) -> None:
        """Backward-compat alias: broadcasts to the default UAV's subscribers only."""
        await self.broadcast_to_uav(DEFAULT_UAV_ID, payload)

    @property
    def client_count(self) -> int:
        return sum(len(sockets) for sockets in self.active.values())


manager = ConnectionManager()


class FleetOverviewConnectionManager:
    """A single shared broadcast list for `/ws/fleet-overview` — unlike telemetry,
    every client here watches the same fleet-wide summary, so there is nothing to
    key by UAV."""

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
        for ws in list(self.active):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def client_count(self) -> int:
        return len(self.active)


fleet_manager = FleetOverviewConnectionManager()


@router.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket) -> None:
    user = await websocket_user(
        websocket.headers.get("authorization"),
        websocket.query_params.get("token"),
    )
    if user is None:
        logger.warning("Rejected telemetry WebSocket: bad or missing token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    uav_id = websocket.query_params.get("uav_id") or DEFAULT_UAV_ID
    await manager.connect(websocket, uav_id)
    logger.info("Telemetry client authenticated as %s (uav=%s)", user.username, uav_id)
    try:
        while True:
            # Control happens over REST, so nothing is expected from the client — but we
            # must await something to notice a disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, uav_id)
    except Exception:
        manager.disconnect(websocket, uav_id)


@router.websocket("/ws/fleet-overview")
async def ws_fleet_overview(websocket: WebSocket) -> None:
    user = await websocket_user(
        websocket.headers.get("authorization"),
        websocket.query_params.get("token"),
    )
    if user is None:
        logger.warning("Rejected fleet-overview WebSocket: bad or missing token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await fleet_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        fleet_manager.disconnect(websocket)
    except Exception:
        fleet_manager.disconnect(websocket)
