"""Mission profile: the commanded throttle / altitude / airspeed trajectory per phase.

Phase 2 change: phases no longer prescribe RPM, EGT, oil pressure and so on directly (as
the Phase 1 mock did). They command only what a pilot or autopilot actually commands —
throttle, target altitude, airspeed — and every engine signal *emerges* from the physics
in response. Climb is hot because the throttle is open and the climb rate is high, not
because a table says "climb EGT = 780".

`remaining_seconds()` feeds the mission-reliability model, which needs to know how much
flying is still planned in order to answer "will this engine make it?".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MissionPhase = Literal["climb", "cruise", "loiter", "descent"]

PHASE_ORDER: list[MissionPhase] = ["climb", "cruise", "loiter", "descent"]


@dataclass(frozen=True)
class PhaseProfile:
    duration_s: float
    throttle: float
    target_altitude_m: float
    climb_rate_m_s: float
    airspeed_ms: float


#: Nominal mission. Durations are in *simulated* seconds and are compressed relative to a
#: real MALE UAV sortie so a demo cycles through every phase in a few minutes.
PHASE_PROFILES: dict[MissionPhase, PhaseProfile] = {
    "climb": PhaseProfile(
        duration_s=240.0,
        throttle=0.95,
        target_altitude_m=2400.0,
        climb_rate_m_s=6.5,
        airspeed_ms=38.0,
    ),
    "cruise": PhaseProfile(
        duration_s=240.0,
        throttle=0.78,
        target_altitude_m=2400.0,
        climb_rate_m_s=0.0,
        airspeed_ms=48.0,
    ),
    "loiter": PhaseProfile(
        duration_s=300.0,
        throttle=0.66,
        target_altitude_m=2200.0,
        climb_rate_m_s=-1.0,
        airspeed_ms=33.0,
    ),
    "descent": PhaseProfile(
        duration_s=150.0,
        throttle=0.56,
        target_altitude_m=900.0,
        climb_rate_m_s=-7.0,
        airspeed_ms=42.0,
    ),
}

TOTAL_MISSION_S: float = sum(p.duration_s for p in PHASE_PROFILES.values())

START_ALTITUDE_M: float = 600.0


class MissionProfile:
    """Phase state machine. Advances on a timer, or jumps on operator command."""

    def __init__(self) -> None:
        self.phase_index: int = 0
        self.phase_elapsed_s: float = 0.0
        self.altitude_m: float = START_ALTITUDE_M
        self.airspeed_ms: float = PHASE_PROFILES["climb"].airspeed_ms

    @property
    def phase(self) -> MissionPhase:
        return PHASE_ORDER[self.phase_index]

    @property
    def profile(self) -> PhaseProfile:
        return PHASE_PROFILES[self.phase]

    def jump_to(self, phase: MissionPhase) -> None:
        if phase in PHASE_ORDER:
            self.phase_index = PHASE_ORDER.index(phase)
            self.phase_elapsed_s = 0.0

    def remaining_seconds(self) -> float:
        """Planned flight time left in the mission, from the current point onward."""
        current_left = max(0.0, self.profile.duration_s - self.phase_elapsed_s)
        later = sum(
            PHASE_PROFILES[p].duration_s for p in PHASE_ORDER[self.phase_index + 1 :]
        )
        return current_left + later

    def step(self, dt: float) -> None:
        """Advance the phase clock and fly the altitude/airspeed trajectory."""
        self.phase_elapsed_s += dt
        if self.phase_elapsed_s >= self.profile.duration_s:
            self.phase_elapsed_s = 0.0
            self.phase_index = (self.phase_index + 1) % len(PHASE_ORDER)

        prof = self.profile
        target = prof.target_altitude_m
        rate = abs(prof.climb_rate_m_s)

        if rate > 1e-6:
            if self.altitude_m < target:
                self.altitude_m = min(target, self.altitude_m + rate * dt)
            elif self.altitude_m > target:
                self.altitude_m = max(target, self.altitude_m - rate * dt)
        self.altitude_m = max(0.0, self.altitude_m)

        # Airspeed eases toward the phase target rather than stepping.
        self.airspeed_ms += (prof.airspeed_ms - self.airspeed_ms) * min(1.0, dt / 8.0)

    def commanded_throttle(self) -> float:
        return self.profile.throttle
