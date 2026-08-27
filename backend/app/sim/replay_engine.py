"""Replay stored missions over the live telemetry contract.

Frames come back out of the database on the *same* `/ws/telemetry` socket, in the same
schema, through the same broadcast path. The frontend needs no replay-specific code —
it renders replayed frames exactly as it renders live ones. The single concession is the
`is_replay` flag on each frame, which exists so the UI can show a badge; nothing about
how the data is parsed or displayed changes.

Timing is reconstructed from the recorded timestamps rather than assumed to be a fixed
10 Hz. If the original mission was recorded at 20x time acceleration, the gaps between
frames already encode that, and dividing them by `speed_factor` preserves the relationship
the operator actually saw. Gaps are clamped so a pause in recording does not stall replay
for minutes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Never wait longer than this between two replayed frames, whatever the recording says.
MAX_FRAME_GAP_S = 2.0


class ReplayEngine:
    """Owns the replay task. Only one replay runs at a time."""

    def __init__(self) -> None:
        self.active: bool = False
        self.mission_id: int | None = None
        self.speed_factor: float = 1.0
        self.frames_total: int = 0
        self.frames_sent: int = 0
        self._task: asyncio.Task | None = None

    @property
    def status(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "mission_id": self.mission_id,
            "speed_factor": self.speed_factor,
            "frames_total": self.frames_total,
            "frames_sent": self.frames_sent,
        }

    async def start(
        self, mission_id: int, speed_factor: float, broadcast, frames: list[dict]
    ) -> dict[str, Any]:
        await self.stop()

        if not frames:
            return {"ok": False, "detail": f"mission {mission_id} has no stored frames"}

        self.active = True
        self.mission_id = mission_id
        self.speed_factor = max(0.1, min(50.0, speed_factor))
        self.frames_total = len(frames)
        self.frames_sent = 0

        self._task = asyncio.create_task(self._run(frames, broadcast))
        logger.info(
            "Replay started: mission=%s frames=%d speed=%.1fx",
            mission_id,
            len(frames),
            self.speed_factor,
        )
        return {"ok": True, **self.status}

    async def _run(self, frames: list[dict], broadcast) -> None:
        try:
            previous_ts: float | None = None
            for frame in frames:
                if not self.active:
                    break

                # Reproduce the original inter-frame spacing, scaled by the requested
                # speed. The recording's own timing is the source of truth.
                ts = float(frame.get("timestamp", 0.0))
                if previous_ts is not None:
                    gap = max(0.0, min(MAX_FRAME_GAP_S, ts - previous_ts))
                    await asyncio.sleep(gap / self.speed_factor)
                previous_ts = ts

                payload = dict(frame)
                payload["is_replay"] = True
                payload["replay_mission_id"] = self.mission_id
                await broadcast(payload)
                self.frames_sent += 1

            logger.info(
                "Replay of mission %s finished (%d frames)",
                self.mission_id,
                self.frames_sent,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Replay failed")
        finally:
            self.active = False

    async def stop(self) -> dict[str, Any]:
        self.active = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        mission = self.mission_id
        self.mission_id = None
        return {"ok": True, "stopped_mission_id": mission}


#: Single shared replay engine.
replay_engine = ReplayEngine()
