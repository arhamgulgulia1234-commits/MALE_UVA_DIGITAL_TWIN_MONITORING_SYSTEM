"""Mission profile: the commanded throttle / altitude / airspeed trajectory per phase.

Phase 2 change: phases no longer prescribe RPM, EGT, oil pressure and so on directly (as
the Phase 1 mock did). They command only what a pilot or autopilot actually commands —
throttle, target altitude, airspeed — and every engine signal *emerges* from the physics
in response. Climb is hot because the throttle is open and the climb rate is high, not
because a table says "climb EGT = 780".

`remaining_seconds()` feeds the mission-reliability model, which needs to know how much
flying is still planned in order to answer "will this engine make it?". `estimated_rtb_
seconds()` feeds the separate recovery-reliability model, which asks the different
question "if we abort right now, can it get back to base?" — see
app/ml/mission_reliability.py for why those two are kept apart rather than folded into
one score.
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

    def estimated_rtb_seconds(self) -> float:
        """How long it would take to fly back to base if the mission were aborted right
        now — a genuinely different question from `remaining_seconds()`, which answers
        "how much more flying is planned." Feeds `recovery_reliability` instead of
        `mission_reliability` (see app/ml/mission_reliability.py).

        Deliberately not a flight-path/geometry model — there is no distance-to-base
        coordinate anywhere in this simulator, and adding one just for this would be
        exactly the over-engineering the feature does not need. Instead it leans on the
        one fact this phase state machine already gives for free: `climb` and `cruise`
        are the outbound leg (each second spent in them is a second flown away from
        base), `loiter` holds a fixed station (it neither gains nor loses distance from
        base), and `descent` *is* the return leg already in progress. So:

            climb:   grows with how much of the outbound climb has already been flown,
                     plus the cruise-and-descent legs still needed to get all the way
                     back — i.e. RTB time roughly mirrors time already spent outbound.
            cruise:  the same idea, with the climb leg already banked in full and
                     cruise's own elapsed time added as it accrues.
            loiter:  frozen at the full outbound transit (climb + cruise) — circling on
                     station does not change distance from base, so RTB does not grow
                     with time spent loitering, matching the same "already spent
                     outbound" quantity carried over from when loiter began.
            descent: already flying home. RTB counts down from the descent leg's planned
                     duration as the descent itself completes, floored at zero.

        Monotonic within every phase, continuous at every phase boundary except the one
        place it should not be: descent's end (RTB = 0, home) into the next cycle's climb
        (RTB jumps back up to a full transit-and-descent estimate), which is correct —
        the moment a new outbound leg begins, aborting immediately still costs a full
        return leg, exactly as it would flying anything else away from base.
        """
        climb = PHASE_PROFILES["climb"]
        cruise = PHASE_PROFILES["cruise"]
        descent = PHASE_PROFILES["descent"]

        if self.phase == "climb":
            return self.phase_elapsed_s + cruise.duration_s + descent.duration_s
        if self.phase == "cruise":
            return climb.duration_s + self.phase_elapsed_s + descent.duration_s
        if self.phase == "loiter":
            return climb.duration_s + cruise.duration_s + descent.duration_s
        # descent
        return max(0.0, descent.duration_s - self.phase_elapsed_s)

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


# ---- Phase 3: environmental scenarios ---------------------------------------

#: Named operating environments. These set conditions the *airframe cannot control* —
#: how hot the day is — as opposed to the phase profile, which sets what the autopilot
#: commands. Hot-and-high is the classic MALE UAV problem case: thin, hot air both
#: starves the engine of oxygen and robs the cooling system of its temperature gradient,
#: so CHT climbs while available power falls.
SCENARIOS: dict[str, dict] = {
    "standard": {
        "ambient_temperature_c": None,   # follow ISA for the current altitude
        "description": "ISA standard day.",
    },
    "hot_weather": {
        "ambient_temperature_c": 48.0,
        "description": (
            "Hot-and-high desert operation at 48 degC. Air density falls beyond the "
            "ISA altitude effect, cooling effectiveness drops, and CHT margin shrinks."
        ),
    },
    "cold_soak": {
        "ambient_temperature_c": -25.0,
        "description": (
            "High-latitude cold start. Denser air improves breathing, but oil is thick "
            "and cylinder heads run cool."
        ),
    },
}


def apply_scenario(sim, scenario: str) -> dict:
    """Apply a named scenario to a running simulation loop."""
    config = SCENARIOS.get(scenario)
    if config is None:
        return {
            "ok": False,
            "detail": f"unknown scenario '{scenario}'",
            "available": list(SCENARIOS),
        }
    sim.set_ambient_temperature(config["ambient_temperature_c"])
    return {
        "ok": True,
        "scenario": scenario,
        "ambient_temperature_c": config["ambient_temperature_c"],
        "description": config["description"],
    }
