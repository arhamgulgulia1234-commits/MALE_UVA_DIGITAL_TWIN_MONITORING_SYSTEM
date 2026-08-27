"""Phase 1 mock telemetry generator — retained as a demo-safety fallback.

Produces a believable TelemetryFrame every tick using sine-wave baselines + small random
noise per signal, driven by a simple mission-phase state machine (climb -> cruise ->
loiter -> descent -> climb ...), with fault injection that gradually ramps affected
signals + subsystem health scores + overall health + RUL + mission reliability.

This is *not* the Phase 2 path. The real physics simulation lives in
app/sim/simulation_loop.py driving app/physics/*. This module is kept only so a live demo
can fall back to known-good scripted telemetry if the physics model misbehaves on stage:

    USE_MOCK=true uvicorn app.main:app

It carries its own copy of the phase table so it stays self-contained and does not break
when the real mission profile is re-tuned.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Literal

from app.core.models import (
    ActiveFault,
    CylinderReading,
    HealthState,
    MissionReliability,
    TelemetryFrame,
)

MissionPhase = Literal["climb", "cruise", "loiter", "descent"]

PHASE_ORDER: list[MissionPhase] = ["climb", "cruise", "loiter", "descent"]
NUM_CYLINDERS = 4
BASE_ALTITUDE_M = 1800.0


@dataclass(frozen=True)
class _PhaseProfile:
    duration_s: float
    rpm: float
    manifold_kpa: float
    boost_kpa: float
    egt_c: float
    cht_c: float
    oil_temp_c: float
    oil_pressure_kpa: float
    fuel_flow_lph: float
    altitude_rate_m_s: float
    airspeed_ms: float


PHASE_PROFILES: dict[MissionPhase, _PhaseProfile] = {
    "climb": _PhaseProfile(
        duration_s=40, rpm=5350, manifold_kpa=95, boost_kpa=145, egt_c=780,
        cht_c=195, oil_temp_c=95, oil_pressure_kpa=420, fuel_flow_lph=28,
        altitude_rate_m_s=6.5, airspeed_ms=38,
    ),
    "cruise": _PhaseProfile(
        duration_s=60, rpm=4400, manifold_kpa=75, boost_kpa=110, egt_c=690,
        cht_c=175, oil_temp_c=90, oil_pressure_kpa=400, fuel_flow_lph=16,
        altitude_rate_m_s=0.0, airspeed_ms=45,
    ),
    "loiter": _PhaseProfile(
        duration_s=50, rpm=3200, manifold_kpa=55, boost_kpa=85, egt_c=590,
        cht_c=155, oil_temp_c=84, oil_pressure_kpa=370, fuel_flow_lph=9,
        altitude_rate_m_s=-0.5, airspeed_ms=32,
    ),
    "descent": _PhaseProfile(
        duration_s=35, rpm=3500, manifold_kpa=45, boost_kpa=70, egt_c=520,
        cht_c=140, oil_temp_c=78, oil_pressure_kpa=360, fuel_flow_lph=7,
        altitude_rate_m_s=-7.0, airspeed_ms=40,
    ),
}

FAULT_SUBSYSTEMS: dict[str, list[str]] = {
    "misfire": ["cylinder"],
    "spark_degradation": ["cylinder"],
    "piston_ring_wear": ["cylinder", "lubrication"],
    "bearing_wear": ["lubrication"],
    "oil_pump_degradation": ["lubrication"],
    "cooling_degradation": ["cooling"],
    "fuel_injector_clog": ["fuel"],
    "turbo_wear": ["turbo"],
    "air_filter_clog": ["turbo", "fuel"],
}

CYLINDER_SPECIFIC_FAULTS = {"misfire", "spark_degradation", "fuel_injector_clog"}

SUBSYSTEM_KEYS = ["cylinder", "lubrication", "cooling", "fuel", "turbo"]


@dataclass
class FaultState:
    type: str
    severity: float = 0.0
    target_severity: float = 0.0
    ramp_seconds: float = 15.0
    clearing: bool = False
    started_at: float = field(default_factory=time.time)
    cylinder_index: int = 0

    def step(self, dt: float) -> None:
        if self.ramp_seconds <= 0:
            self.severity = self.target_severity
            return
        rate = 1.0 / self.ramp_seconds
        if self.severity < self.target_severity:
            self.severity = min(self.target_severity, self.severity + rate * dt)
        elif self.severity > self.target_severity:
            self.severity = max(self.target_severity, self.severity - rate * dt)

    @property
    def is_settled(self) -> bool:
        return abs(self.severity - self.target_severity) < 1e-4


class SimulationLoop:
    """Owns all mutable mock-sim state. Not thread-safe by design — intended to be
    driven by a single asyncio task (see app/main.py) and mutated via its public
    methods from HTTP control handlers running in the same event loop."""

    def __init__(self) -> None:
        self._t0 = time.time()
        self.sim_time = 0.0
        self.phase_index = 0
        self.phase_elapsed = 0.0
        self.altitude_m = BASE_ALTITUDE_M
        self.throttle = 0.8
        self.time_scale = 1.0
        self.active_faults: dict[str, FaultState] = {}
        self._subsystem_scores = {k: 100.0 for k in SUBSYSTEM_KEYS}
        self._latest: TelemetryFrame | None = None
        self._rng = random.Random(42)

    # ---- control surface -------------------------------------------------

    def set_throttle(self, value: float) -> None:
        self.throttle = max(0.0, min(1.0, value))

    def set_time_scale(self, factor: float) -> None:
        self.time_scale = max(0.1, min(50.0, factor))

    def jump_phase(self, phase: MissionPhase) -> None:
        if phase in PHASE_ORDER:
            self.phase_index = PHASE_ORDER.index(phase)
            self.phase_elapsed = 0.0

    def inject_fault(self, fault_type: str, severity: float, ramp_seconds: float) -> None:
        severity = max(0.0, min(1.0, severity))
        existing = self.active_faults.get(fault_type)
        cyl_idx = existing.cylinder_index if existing else self._rng.randrange(NUM_CYLINDERS)
        started_at = existing.started_at if existing else time.time()
        self.active_faults[fault_type] = FaultState(
            type=fault_type,
            severity=existing.severity if existing else 0.0,
            target_severity=severity,
            ramp_seconds=max(0.5, ramp_seconds),
            clearing=False,
            started_at=started_at,
            cylinder_index=cyl_idx,
        )

    def clear_fault(self, fault_type: str) -> None:
        existing = self.active_faults.get(fault_type)
        if existing is None:
            return
        existing.target_severity = 0.0
        existing.clearing = True
        existing.ramp_seconds = max(0.5, existing.ramp_seconds)

    # ---- tick ---------------------------------------------------------

    def tick(self, real_dt: float) -> TelemetryFrame:
        dt = real_dt * self.time_scale
        self.sim_time += dt

        profile_key = PHASE_ORDER[self.phase_index]
        profile = PHASE_PROFILES[profile_key]
        self.phase_elapsed += dt
        if self.phase_elapsed >= profile.duration_s:
            self.phase_elapsed = 0.0
            self.phase_index = (self.phase_index + 1) % len(PHASE_ORDER)
            profile_key = PHASE_ORDER[self.phase_index]
            profile = PHASE_PROFILES[profile_key]

        # advance fault ramps, drop fully-cleared faults
        for ft in list(self.active_faults.keys()):
            fs = self.active_faults[ft]
            fs.step(dt)
            if fs.clearing and fs.is_settled and fs.severity <= 1e-4:
                del self.active_faults[ft]

        throttle_factor = 0.4 + 0.6 * self.throttle
        t = self.sim_time
        noise = self._rng.gauss

        rpm = profile.rpm * throttle_factor + 35 * math.sin(t * 0.9) + noise(0, 12)
        manifold_kpa = profile.manifold_kpa * throttle_factor + 3 * math.sin(t * 0.7) + noise(0, 1.2)
        boost_kpa = profile.boost_kpa * throttle_factor + 4 * math.sin(t * 0.6 + 1) + noise(0, 1.5)
        cht_c = profile.cht_c + 4 * math.sin(t * 0.15) + noise(0, 0.8)
        oil_temp_c = profile.oil_temp_c + 3 * math.sin(t * 0.12 + 0.5) + noise(0, 0.6)
        oil_pressure_kpa = profile.oil_pressure_kpa * (0.85 + 0.15 * throttle_factor) + 6 * math.sin(t * 0.8) + noise(0, 3)
        fuel_flow_lph = profile.fuel_flow_lph * throttle_factor + 0.6 * math.sin(t * 0.5) + noise(0, 0.3)

        self.altitude_m = max(0.0, self.altitude_m + profile.altitude_rate_m_s * dt + noise(0, 0.5))
        airspeed_ms = profile.airspeed_ms + 1.5 * math.sin(t * 0.3) + noise(0, 0.4)

        cylinders: list[CylinderReading] = []
        for i in range(NUM_CYLINDERS):
            phase_shift = (2 * math.pi / NUM_CYLINDERS) * i
            egt = profile.egt_c + 10 * math.sin(t * 1.3 + phase_shift) + noise(0, 3)
            base_vib = 0.12 + (rpm / 5500) * 0.10
            vib = base_vib + 0.02 * math.sin(t * 2.1 + phase_shift) + abs(noise(0, 0.015))
            cylinders.append(CylinderReading(id=i + 1, egt_c=egt, vibration_rms=vib))

        # ---- fault effects -------------------------------------------------
        subsystem_penalty = {k: 0.0 for k in SUBSYSTEM_KEYS}
        active_fault_models = list(self.active_faults.values())
        worst_severity = 0.0

        for fs in active_fault_models:
            s = fs.severity
            if s <= 1e-4:
                continue
            worst_severity = max(worst_severity, s)
            for sub in FAULT_SUBSYSTEMS[fs.type]:
                subsystem_penalty[sub] += s * 80

            if fs.type == "misfire":
                flicker = self._rng.uniform(0.5, 1.0)
                c = cylinders[fs.cylinder_index]
                c.egt_c -= s * 150 * flicker
                c.vibration_rms += s * 0.6 * self._rng.uniform(0.4, 1.0)
                fuel_flow_lph += s * 2
            elif fs.type == "spark_degradation":
                c = cylinders[fs.cylinder_index]
                c.egt_c += s * 80
                c.vibration_rms += s * 0.25
                fuel_flow_lph += s * 1
            elif fs.type == "piston_ring_wear":
                oil_pressure_kpa -= s * 90
                oil_temp_c += s * 15
                for c in cylinders:
                    c.egt_c += s * 15
                    c.vibration_rms += s * 0.12
            elif fs.type == "bearing_wear":
                oil_pressure_kpa -= s * 120
                oil_temp_c += s * 10
                for c in cylinders:
                    c.vibration_rms += s * 0.8
            elif fs.type == "oil_pump_degradation":
                oil_pressure_kpa -= s * 180
                oil_temp_c += s * 20
                for c in cylinders:
                    c.vibration_rms += s * 0.15
            elif fs.type == "cooling_degradation":
                cht_c += s * 55
                oil_temp_c += s * 10
                for c in cylinders:
                    c.egt_c += s * 20
            elif fs.type == "fuel_injector_clog":
                c = cylinders[fs.cylinder_index]
                c.egt_c += s * 60
                c.vibration_rms += s * 0.15
                fuel_flow_lph -= s * 3
            elif fs.type == "turbo_wear":
                boost_kpa -= s * 50
                manifold_kpa -= s * 20
                for c in cylinders:
                    c.egt_c += s * 25
                    c.vibration_rms += s * 0.1
            elif fs.type == "air_filter_clog":
                manifold_kpa -= s * 25
                boost_kpa -= s * 30
                fuel_flow_lph -= s * 2
                for c in cylinders:
                    c.egt_c += s * 15

        oil_pressure_kpa = max(20.0, oil_pressure_kpa)
        fuel_flow_lph = max(0.5, fuel_flow_lph)
        boost_kpa = max(0.0, boost_kpa)
        manifold_kpa = max(10.0, manifold_kpa)

        # ---- health scores --------------------------------------------------
        for k in SUBSYSTEM_KEYS:
            target = max(5.0, 100.0 - subsystem_penalty[k])
            drift = noise(0, 0.15)
            current = self._subsystem_scores[k]
            # move toward target at a bounded rate so recoveries/degradations feel smooth
            step = max(-6.0, min(6.0, target - current))
            self._subsystem_scores[k] = max(0.0, min(100.0, current + step * 0.5 + drift))

        subsystem_scores = dict(self._subsystem_scores)
        worst_subsystem = min(subsystem_scores.values())
        avg_subsystem = sum(subsystem_scores.values()) / len(subsystem_scores)
        overall_score = max(0.0, min(100.0, worst_subsystem * 0.6 + avg_subsystem * 0.4))

        rul_minutes: float | None = None
        if worst_severity > 0.02 or overall_score < 94.5:
            decay = max(worst_severity, (95.0 - overall_score) / 95.0)
            rul_minutes = max(0.0, 180.0 * (1.0 - decay) ** 1.5)

        reliability_score = max(
            0.0,
            min(1.0, 1.0 - worst_severity * 0.9 - (100.0 - overall_score) / 200.0),
        )
        if reliability_score > 0.75:
            recommendation = "GO"
        elif reliability_score > 0.4:
            recommendation = "CAUTION"
        else:
            recommendation = "NO-GO"

        active_faults_out = [
            ActiveFault(type=fs.type, severity=round(fs.severity, 4), started_at=fs.started_at)
            for fs in active_fault_models
            if fs.severity > 1e-4
        ]

        frame = TelemetryFrame(
            timestamp=time.time(),
            mission_phase=profile_key,
            rpm=round(rpm, 1),
            manifold_pressure_kpa=round(manifold_kpa, 2),
            boost_pressure_kpa=round(boost_kpa, 2),
            cylinders=[
                CylinderReading(
                    id=c.id, egt_c=round(c.egt_c, 1), vibration_rms=round(max(0.0, c.vibration_rms), 4)
                )
                for c in cylinders
            ],
            cht_c=round(cht_c, 1),
            oil_temp_c=round(oil_temp_c, 1),
            oil_pressure_kpa=round(oil_pressure_kpa, 1),
            fuel_flow_lph=round(fuel_flow_lph, 2),
            altitude_m=round(self.altitude_m, 1),
            airspeed_ms=round(max(0.0, airspeed_ms), 1),
            health=HealthState(
                overall_score=round(overall_score, 1),
                subsystem_scores=subsystem_scores,  # type: ignore[arg-type]
            ),
            rul_minutes=round(rul_minutes, 1) if rul_minutes is not None else None,
            mission_reliability=MissionReliability(
                score=round(reliability_score, 3), recommendation=recommendation
            ),
            active_faults=active_faults_out,
        )
        self._latest = frame
        return frame

    def get_latest(self) -> TelemetryFrame | None:
        return self._latest
